"""Backend for the browser extension: receives tab audio, queries Panako, returns matches.

Run locally:  python server/app.py            (listens on http://127.0.0.1:8765)
On AWS Lambda the same app runs in a container (server/deploy/, docs/lambda-deployment.md).

Every final query is kept: locally under recordings/ (WAV + row in recordings/log.csv, a valid
poc/evaluate.py manifest once the popup's feedback fills in `expected`); with KF_BUCKET set, in
S3 under recordings/ (WAV + one JSON per query).

Environment (all optional; unset = local behaviour):
  KF_BUCKET       S3 bucket for recordings and the preview MP3s (previews/<library key>.mp3)
  KF_API_KEY      required X-Api-Key header on every endpoint except /health
  KF_DATA_PREFIX  download the fingerprint DB (data.mdb) and rhythm.npz from s3://KF_BUCKET/<prefix>
                  into KF_TMP at start (Lambda)
  KF_READONLY_DB  1 = open the fingerprint DB read-only without a lock file
  KF_DB_DIR       Panako DB folder (default db/panako)
  KF_TMP          scratch folder for WAVs (default recordings/)
  HOST, PORT      listen address (default 127.0.0.1:8765)
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path

_T0 = time.time()


def startup_log(step: str) -> None:
    # Cold-start breakdown (CloudWatch on Lambda).
    print(f"startup {time.time() - _T0:6.1f}s {step}", file=sys.stderr, flush=True)


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "poc"))

import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from common import DB_DIR, FFMPEG, MUSIC_DIR, ROOT, EXCLUDE_FILE, Library, PanakoServer  # noqa: E402
from grouping import group, load_links, verdict  # noqa: E402
from rhythm import SR as RHYTHM_SR, RhythmIndex, identify as rhythm_identify  # noqa: E402

startup_log("imports")

BUCKET = os.environ.get("KF_BUCKET", "")
API_KEY = os.environ.get("KF_API_KEY", "")
TMP_DIR = Path(os.environ.get("KF_TMP", str(ROOT / "recordings")))
TMP_DIR.mkdir(parents=True, exist_ok=True)
LOG = TMP_DIR / "log.csv"  # local mode only
LOG_FIELDS = ["clip", "condition", "expected", "offset", "match", "score", "second",
              "method", "feedback", "page_title", "page_url", "time"]
S3 = None
if BUCKET:
    import boto3
    from botocore.config import Config as BotoConfig
    S3 = boto3.client("s3", config=BotoConfig(max_pool_connections=32))
RHYTHM_FILE = ROOT / "db" / "rhythm.npz"
DB_PATH = Path(os.environ.get("KF_DB_DIR", str(DB_DIR)))
db_download = None
if os.environ.get("KF_DATA_PREFIX"):
    # The two large files come from S3 at every cold start instead of from the image: Lambda reads
    # rarely used image files at ~7 MB/s (66 s cold starts, 27 s first queries), S3 in-region is
    # far faster. Each download is parallel ranged GETs; the fingerprint DB keeps downloading
    # while the rhythm index is loaded (CPU-bound).
    from concurrent.futures import ThreadPoolExecutor
    from boto3.s3.transfer import TransferConfig

    prefix = os.environ["KF_DATA_PREFIX"]
    RHYTHM_FILE = TMP_DIR / "data" / "rhythm.npz"
    DB_PATH = TMP_DIR / "data" / "panako"
    (DB_PATH / "db").mkdir(parents=True, exist_ok=True)
    transfer = TransferConfig(max_concurrency=int(os.environ.get("KF_DOWNLOAD_THREADS", "32")),
                              multipart_chunksize=8 << 20)

    def fetch(name: str, dest: Path) -> None:
        t = time.time()
        S3.download_file(BUCKET, prefix + name, str(dest), Config=transfer)
        startup_log(f"{name} from S3 ({dest.stat().st_size / 1e6 / (time.time() - t):.0f} MB/s)")

    def fetch_parts(name: str, dest: Path) -> None:
        """A file stored as independently zstd-compressed parts (server/deploy/stage.py): half
        the bytes over the network; parts are fetched and decompressed in parallel (zstd releases
        the GIL) and written at their offsets."""
        import zstandard

        t = time.time()
        manifest = json.loads(S3.get_object(Bucket=BUCKET, Key=f"{prefix}{name}.parts/manifest.json")["Body"].read())
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        os.ftruncate(fd, manifest["size"])

        def part(i: int) -> int:
            body = S3.get_object(Bucket=BUCKET, Key=f"{prefix}{name}.parts/{manifest['parts'][i]}")["Body"].read()
            raw = zstandard.ZstdDecompressor().decompress(body, max_output_size=manifest["part_bytes"])
            os.pwrite(fd, raw, i * manifest["part_bytes"])
            return len(body)

        try:
            with ThreadPoolExecutor(16) as pool:
                sent = sum(pool.map(part, range(len(manifest["parts"]))))
        finally:
            os.close(fd)
        dt = time.time() - t
        startup_log(f"{name} from S3 ({sent / 1e6:.0f} MB compressed, {sent / 1e6 / dt:.0f} MB/s)")

    downloads = ThreadPoolExecutor(2)
    # The rhythm index first (needed first, and CPU-bound to load), then the DB in the background.
    fetch("rhythm.npz", RHYTHM_FILE)
    db_download = downloads.submit(fetch_parts, "data.mdb", DB_PATH / "db" / "data.mdb")
LINKS = load_links()
# Library keys: paths relative to music/ with '/' (the same on this PC and on Lambda).
LIBRARY = Library()
# Never reported (long DJ/session mixes etc., see db/exclude.txt).
EXCLUDED = {l.strip() for l in EXCLUDE_FILE.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")} if EXCLUDE_FILE.exists() else set()
startup_log("links, catalog")
# Fallback for reverberant room recordings where Panako finds nothing (poc/rhythm.py).
RHYTHM = (RhythmIndex(RHYTHM_FILE, exclude=lambda p: LIBRARY.key(p) in EXCLUDED,
                      cohort=ROOT / "db" / "rhythm_cohort.npz", cohort_wide=ROOT / "db" / "rhythm_cohort_wide.npz")
          if RHYTHM_FILE.exists() else None)
startup_log("rhythm index")
RHYTHM_SLOTS = threading.BoundedSemaphore(int(os.environ.get("RHYTHM_WORKERS", "3")))
RHYTHM_THREADS = int(os.environ.get("RHYTHM_THREADS", "3"))  # speeds searched at once per request
# Partial checks run the rhythm search from RHYTHM_HINT_SECONDS of audio on. A strong candidate
# ends the recording when its ratio reaches the bar for the audio's length: shorter audio needs
# a clearer winner. (seconds, ratio), longest first; the extension times its checks to match.
RHYTHM_HINT_SECONDS = 10
RHYTHM_EARLY = ((20, 1.2), (15, 1.3), (10, 1.5))
RHYTHM_SLACK = 0.5  # the extension's 1 s chunks: a check at 10.5 s holds 10 s, decoded a hair short
PANAKO_SR = 16000  # Panako's PANAKO_SAMPLE_RATE
if db_download is not None:
    db_download.result()
# One JVM for all requests, storage kept open (poc/common.py PanakoServer).
READONLY_DB = os.environ.get("KF_READONLY_DB") == "1"
PANAKO = PanakoServer(db_dir=DB_PATH,
                      threads=int(os.environ.get("PANAKO_THREADS", "8")),
                      map_gb=int(os.environ.get("PANAKO_MAP_GB", "32")),
                      readonly=READONLY_DB,
                      log_path=TMP_DIR / "panako_server.log",
                      extra_config={"NUMBER_OF_QUERY_RESULTS": "10",
                                    # Panako creates its (here unused) print cache folder on start.
                                    **({"PANAKO_CACHE_FOLDER": str(TMP_DIR / "panako_cache")} if READONLY_DB else {})})
startup_log("panako server, ready")


def same_audio(a: int, b: int) -> bool:
    return a == b or frozenset((a, b)) in LINKS


def check_key(request: Request) -> None:
    if API_KEY and request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(401, "API 키가 맞지 않습니다.")


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origin_regex=r"chrome-extension://.*",
                   allow_methods=["*"], allow_headers=["*"])
log_lock = threading.Lock()


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    with LOG.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_log(rows: list[dict]) -> None:
    with LOG.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)


def save_record(row: dict, wav: Path) -> None:
    """Keep a final query: S3 (WAV + JSON) or local (WAV stays in recordings/, row in log.csv)."""
    if S3:
        S3.upload_file(str(wav), BUCKET, f"recordings/{row['time']}.wav")
        wav.unlink()
        row["clip"] = f"s3://{BUCKET}/recordings/{row['time']}.wav"
        S3.put_object(Bucket=BUCKET, Key=f"recordings/{row['time']}.json",
                      Body=json.dumps(row, ensure_ascii=False).encode(), ContentType="application/json")
        return
    row = {**row, "match": str(MUSIC_DIR / row["match"]) if row["match"] else ""}
    with log_lock:
        rows = read_log()
        rows.append(row)
        write_log(rows)


@app.get("/health")
def health() -> dict:
    # Also the warm-up ping (the extension calls it when recording starts): by the time this
    # answers, the JVM, fingerprint DB and rhythm index are loaded.
    return {"ok": True}


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Band-limited resampling by FFT truncation/padding (no scipy needed)."""
    n_out = int(round(len(x) * sr_out / sr_in))
    X = np.fft.rfft(x)
    m = n_out // 2 + 1
    X = X[:m] if m <= len(X) else np.concatenate([X, np.zeros(m - len(X), X.dtype)])
    return (np.fft.irfft(X, n_out) * (n_out / len(x))).astype(np.float32)


@app.post("/identify", dependencies=[Depends(check_key)])
async def identify(request: Request, title: str = "", url: str = "", partial: bool = False) -> dict:
    """partial=1: a mid-recording check from the extension. Fingerprint only (fast); the
    recording is kept and logged only when it is confirmed, since then it is the final answer.

    The work runs on the thread pool so concurrent requests from several users overlap: the
    Panako server handles queries on its own Java threads and numpy releases the GIL."""
    body = await request.body()
    return await run_in_threadpool(identify_audio, body, title, url, partial)


def identify_audio(body: bytes, title: str, url: str, partial: bool) -> dict:
    if len(body) < 1000:
        raise HTTPException(400, "recording is empty (is the tab playing audio?)")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Unique even for requests arriving in the same millisecond.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + "-" + uuid.uuid4().hex[:4]
    raw = TMP_DIR / f"{stamp}.webm"
    wav = TMP_DIR / f"{stamp}.wav"
    raw.write_bytes(body)
    # One ffmpeg call per request. The WAV is at Panako's rate, so Panako reads it directly
    # (no decoder process) and the rhythm search resamples it in numpy.
    conv = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", str(PANAKO_SR), str(wav)],
                          capture_output=True, text=True)
    raw.unlink(missing_ok=True)
    if conv.returncode != 0 or not wav.exists():
        raise HTTPException(400, f"ffmpeg could not decode the recording: {conv.stderr[-300:]}")
    # Instagram and others start videos muted; a muted tab records digital silence.
    with wave.open(str(wav), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if pcm.size == 0 or np.sqrt(np.mean(pcm ** 2)) < 0.003:
        wav.unlink()
        raise HTTPException(400, "소리가 거의 녹음되지 않았습니다. 탭/영상이 음소거 상태이거나 멈춰 있지 않은지 확인하세요.")

    t0 = time.time()
    try:
        matches = PANAKO.query(wav)
    except Exception:
        wav.unlink(missing_ok=True)
        raise
    elapsed = time.time() - t0
    timings = {"fingerprint": round(elapsed, 2)}  # seconds per search stage
    for m in matches:
        m.match_path = LIBRARY.key(m.match_path)
    matches = [m for m in matches if m.match_path not in EXCLUDED]

    songs = group(matches, LINKS)
    result, reason = verdict(songs)

    def describe(m):
        return {
            "path": m.match_path,
            "file": m.match_path.rsplit("/", 1)[-1],
            "folder": m.match_path.rsplit("/", 1)[0] if "/" in m.match_path else "",
            "score": m.score,
            "query_start": m.query_start, "query_stop": m.query_stop,
            "match_start": m.match_start, "match_stop": m.match_stop,
            "time_factor": m.time_factor,
        }

    out_songs = [{
        **describe(s.head),
        "score": s.score,
        "preview_only": s.preview_only,
        "also_in": [describe(m) for m in s.members if m is not s.head],
    } for s in songs]
    method = "fingerprint"

    dur = len(pcm) / PANAKO_SR
    # Mid-recording checks also run the rhythm search (battle videos almost never confirm by
    # fingerprint: 41 of 2,344 battle-100 windows). RHYTHM_EARLY, measured on 10/15/20 s
    # prefixes (poc/eval_rhythm_early.py): of 409 battle windows found at 30 s, 30 stop at 10 s
    # and 135 more at 15 s (1 wrong); of 1,618 windows where the final search finds nothing,
    # 27 stop on a candidate (20 s only: 15).
    run_rhythm = (result == "fail" and RHYTHM is not None
                  and (not partial or dur >= RHYTHM_HINT_SECONDS - RHYTHM_SLACK))
    final = not partial or result == "confirmed"

    if run_rhythm:
        y = resample(pcm, PANAKO_SR, RHYTHM_SR)
        with RHYTHM_SLOTS:  # each speed searched holds ~0.5 GB of temporaries
            t1 = time.time()
            r = rhythm_identify(RHYTHM, y, same_audio, threads=RHYTHM_THREADS)
            timings["rhythm"] = round(time.time() - t1, 2)
            # The final search also tries 0.95-1.05x speed (re-uploads played faster or slower).
            # Not mid-recording: it takes several times longer.
            if r is None and not partial:
                t1 = time.time()
                r = rhythm_identify(RHYTHM, y, same_audio, wide=True, threads=RHYTHM_THREADS)
                timings["rhythm_wide"] = round(time.time() - t1, 2)
        # Mid-recording, only strong candidates are shown: weak ones also appeared for 6-10/39
        # songs that are not in the library, and a wrong hint is worse than none.
        if r and (not partial or r["strength"] == "strong"):
            result, reason, method = "check", "rhythm", "rhythm"
            out_songs = []
            for h in r["songs"]:
                key = LIBRARY.key(h["path"])
                out_songs.append({
                    "path": key, "file": key.rsplit("/", 1)[-1],
                    "folder": key.rsplit("/", 1)[0] if "/" in key else "",
                    "score": round(h["score"], 3),
                    "query_start": 0, "query_stop": dur,
                    "match_start": h["pos"], "match_stop": h["pos"] + dur * h["speed"],
                    "time_factor": h["speed"], "preview_only": False, "also_in": [],
                })
            out_songs[0]["ratio"] = round(r["ratio"], 2)
            out_songs[0]["strength"] = r["strength"]
            if partial and r["strength"] == "strong" and any(
                    dur >= sec - RHYTHM_SLACK and r["ratio"] >= ratio for sec, ratio in RHYTHM_EARLY):
                final = True
        elapsed = time.time() - t0

    if not final:  # a hint only; the recording continues and the final request is logged
        wav.unlink()
        return {"id": None, "verdict": result, "reason": reason, "method": method, "final": False,
                "elapsed": round(elapsed, 2), "timings": timings, "songs": out_songs}

    save_record({"clip": str(wav), "condition": "browser", "expected": "", "offset": "",
                 "match": out_songs[0]["path"] if out_songs else "",
                 "score": out_songs[0]["score"] if out_songs else 0,
                 "second": out_songs[1]["score"] if len(out_songs) > 1 else 0, "method": method,
                 "feedback": "", "page_title": title, "page_url": url, "time": stamp}, wav)

    return {
        "id": stamp,
        "verdict": result,
        "reason": reason,
        "method": method,
        "final": True,
        "elapsed": round(elapsed, 2),
        "timings": timings,
        "songs": out_songs,
    }


class Feedback(BaseModel):
    id: str
    correct: bool
    path: str = ""  # library key of the match the user confirmed (defaults to the top one)


@app.post("/feedback", dependencies=[Depends(check_key)])
def feedback(fb: Feedback) -> dict:
    if S3:
        key = f"recordings/{Path(fb.id).name}.json"
        try:
            row = json.loads(S3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        except S3.exceptions.NoSuchKey:
            raise HTTPException(404, "unknown query id")
        row["feedback"] = "correct" if fb.correct else "wrong"
        row["expected"] = (fb.path or row["match"]) if fb.correct else ""
        S3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(row, ensure_ascii=False).encode(),
                      ContentType="application/json")
        return {"ok": True}
    with log_lock:
        rows = read_log()
        for r in rows:
            if r["time"] == fb.id:
                r["feedback"] = "correct" if fb.correct else "wrong"
                r["expected"] = (str(MUSIC_DIR / fb.path) if fb.path else r["match"]) if fb.correct else ""
                write_log(rows)
                return {"ok": True}
    raise HTTPException(404, "unknown query id")


PREVIEW_SECONDS = 10
# The S3 copies are constant-bitrate MP3s without ID3/Xing headers (server/deploy/make_previews.py),
# so second t starts at byte t * PREVIEW_KBPS * 125.
PREVIEW_KBPS = 96


def cut_preview(src: str, start: float, seek: bool = True) -> bytes:
    fade = f"afade=t=in:d=0.3,afade=t=out:st={PREVIEW_SECONDS - 0.5}:d=0.5"
    out = subprocess.run([FFMPEG, "-v", "error", *(["-ss", f"{start:.2f}"] if seek else []),
                          "-t", str(PREVIEW_SECONDS), "-i", src, "-vn", "-af", fade,
                          "-b:a", f"{PREVIEW_KBPS}k", "-f", "mp3", "pipe:1"], capture_output=True)
    if out.returncode != 0 or not out.stdout:
        raise HTTPException(500, "could not cut the preview")
    return out.stdout


@app.get("/preview", dependencies=[Depends(check_key)])
def preview(path: str, start: float = 0.0) -> Response:
    """A 10 s MP3 excerpt from the matched spot, for a by-ear check. The full library file is
    never served (copyright, once this is distributed). `path` is a library key."""
    start = max(0.0, start)
    path = LIBRARY.keys.get(path.lower(), "")
    if not path or path in EXCLUDED:
        raise HTTPException(404, "not a library file")
    if S3:
        # Fetch only the needed bytes (plus a second of slack) from the low-bitrate copy.
        first = int(start * PREVIEW_KBPS * 125)
        last = first + (PREVIEW_SECONDS + 1) * PREVIEW_KBPS * 125
        try:
            obj = S3.get_object(Bucket=BUCKET, Key=f"previews/{path}.mp3", Range=f"bytes={first}-{last}")
        except (S3.exceptions.NoSuchKey, S3.exceptions.InvalidRange):
            raise HTTPException(404, "no preview for this file")
        part = TMP_DIR / f"preview-{uuid.uuid4().hex}.mp3"
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        part.write_bytes(obj["Body"].read())
        try:
            return Response(cut_preview(str(part), 0, seek=False), media_type="audio/mpeg")
        finally:
            part.unlink(missing_ok=True)
    return Response(cut_preview(str(MUSIC_DIR / path), start), media_type="audio/mpeg")


ALBUMS_FILE = ROOT / "db" / "albums.json"  # server/build_albums.py
albums_body: bytes | None = None


@app.get("/albums", dependencies=[Depends(check_key)])
def albums() -> Response:
    """The library's albums for the extension's library page. Covers are inlined as data URIs
    (~4 KB each) so the page needs one request: one per cover would each be a Lambda invocation."""
    global albums_body
    if albums_body is None:
        import base64

        if not ALBUMS_FILE.exists():
            raise HTTPException(404, "앨범 목록이 없습니다 (server/build_albums.py를 실행하세요).")
        out = []
        for a in json.loads(ALBUMS_FILE.read_text(encoding="utf-8")):
            tracks = [t for t in a["tracks"] if LIBRARY.keys.get(t.lower()) == t and t not in EXCLUDED]
            if not tracks:
                continue
            cover = ALBUMS_FILE.parent / "covers" / f"{a['id']}.jpg"
            uri = None
            if a["cover"] and cover.exists():
                uri = "data:image/jpeg;base64," + base64.b64encode(cover.read_bytes()).decode()
            out.append({**a, "tracks": tracks, "cover": uri})
        albums_body = json.dumps({"albums": out}, ensure_ascii=False).encode()
    return Response(albums_body, media_type="application/json")


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8765")))
