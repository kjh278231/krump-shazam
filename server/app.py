"""Local backend for the browser extension: receives tab audio, queries Panako, returns matches.

Run:  python server/app.py            (listens on http://127.0.0.1:8765)

Every query is kept under recordings/ (WAV + row in recordings/log.csv) so real browser
captures can later be scored with poc/evaluate.py: rows confirmed in the popup get
`expected` filled in, and log.csv is a valid evaluate.py manifest.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "poc"))

import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from common import FFMPEG, MUSIC_DIR, ROOT, list_audio, load_excluded, panako, parse_query_output  # noqa: E402
from grouping import group, load_links, verdict  # noqa: E402
from rhythm import RhythmIndex, decode as decode_rhythm, identify as rhythm_identify  # noqa: E402

REC_DIR = ROOT / "recordings"
LOG = REC_DIR / "log.csv"
LOG_FIELDS = ["clip", "condition", "expected", "offset", "match", "score", "second",
              "method", "feedback", "page_title", "page_url", "time"]
PORT = 8765
LINKS = load_links()
RHYTHM_FILE = ROOT / "db" / "rhythm.npz"
# Never reported (long DJ/session mixes etc., see db/exclude.txt).
EXCLUDED = load_excluded()
# Fallback for reverberant room recordings where Panako finds nothing (poc/rhythm.py).
RHYTHM = (RhythmIndex(RHYTHM_FILE, exclude=EXCLUDED, cohort=ROOT / "db" / "rhythm_cohort.npz")
          if RHYTHM_FILE.exists() else None)


def same_audio(a: int, b: int) -> bool:
    return a == b or frozenset((a, b)) in LINKS

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origin_regex=r"chrome-extension://.*",
                   allow_methods=["*"], allow_headers=["*"])
log_lock = threading.Lock()
# parse_query_output normcases paths (lowercase on Windows); map back for display.
ORIGINAL_PATH = {os.path.normcase(str(p)): str(p) for p in list_audio()}


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


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/identify")
async def identify(request: Request, title: str = "", url: str = "", partial: bool = False) -> dict:
    """partial=1: a mid-recording check from the extension. Fingerprint only (fast); the
    recording is kept and logged only when it is confirmed, since then it is the final answer."""
    body = await request.body()
    if len(body) < 1000:
        raise HTTPException(400, "recording is empty (is the tab playing audio?)")
    REC_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    raw = REC_DIR / f"{stamp}.webm"
    wav = REC_DIR / f"{stamp}.wav"
    raw.write_bytes(body)
    conv = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", "22050", str(wav)],
                          capture_output=True, text=True)
    if conv.returncode != 0 or not wav.exists():
        raise HTTPException(400, f"ffmpeg could not decode the recording: {conv.stderr[-300:]}")
    raw.unlink()
    # Instagram and others start videos muted; a muted tab records digital silence.
    with wave.open(str(wav), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if pcm.size == 0 or np.sqrt(np.mean(pcm ** 2)) < 0.003:
        wav.unlink()
        raise HTTPException(400, "소리가 거의 녹음되지 않았습니다. 탭/영상이 음소거 상태이거나 멈춰 있지 않은지 확인하세요.")

    t0 = time.time()
    res = panako("query", [str(wav)], threads=4, extra_config={"NUMBER_OF_QUERY_RESULTS": "10"})
    elapsed = time.time() - t0
    matches = next(iter(parse_query_output(res.stdout).values()), [])
    matches = [m for m in matches if m.match_path not in EXCLUDED]  # both normcased
    for m in matches:
        m.match_path = ORIGINAL_PATH.get(m.match_path, m.match_path)

    songs = group(matches, LINKS)
    result, reason = verdict(songs)

    def describe(m):
        return {
            "path": m.match_path,
            "file": Path(m.match_path).name,
            "folder": Path(m.match_path).parent.name,
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

    if partial and result != "confirmed":
        wav.unlink()
        return {"id": None, "verdict": result, "reason": reason, "method": method,
                "elapsed": round(elapsed, 2), "songs": out_songs}

    if result == "fail" and RHYTHM is not None:
        y = decode_rhythm(wav)
        dur = len(y) / 11025
        r = rhythm_identify(RHYTHM, y, same_audio)
        if r:
            result, reason, method = "check", "rhythm", "rhythm"
            out_songs = [{
                "path": h["path"], "file": Path(h["path"]).name, "folder": Path(h["path"]).parent.name,
                "score": round(h["score"], 3),
                "query_start": 0, "query_stop": dur,
                "match_start": h["pos"], "match_stop": h["pos"] + dur * h["speed"],
                "time_factor": h["speed"], "preview_only": False, "also_in": [],
            } for h in r["songs"]]
            out_songs[0]["ratio"] = round(r["ratio"], 2)
            out_songs[0]["strength"] = r["strength"]
        elapsed = time.time() - t0

    with log_lock:
        rows = read_log()
        rows.append({"clip": str(wav), "condition": "browser", "expected": "", "offset": "",
                     "match": out_songs[0]["path"] if out_songs else "",
                     "score": out_songs[0]["score"] if out_songs else 0,
                     "second": out_songs[1]["score"] if len(out_songs) > 1 else 0, "method": method,
                     "feedback": "", "page_title": title, "page_url": url, "time": stamp})
        write_log(rows)

    return {
        "id": stamp,
        "verdict": result,
        "reason": reason,
        "method": method,
        "elapsed": round(elapsed, 2),
        "songs": out_songs,
    }


class Feedback(BaseModel):
    id: str
    correct: bool
    path: str = ""  # which match the user confirmed (defaults to the top one)


@app.post("/feedback")
def feedback(fb: Feedback) -> dict:
    with log_lock:
        rows = read_log()
        for r in rows:
            if r["time"] == fb.id:
                r["feedback"] = "correct" if fb.correct else "wrong"
                r["expected"] = (fb.path or r["match"]) if fb.correct else ""
                write_log(rows)
                return {"ok": True}
    raise HTTPException(404, "unknown query id")


@app.get("/track")
def track(path: str) -> FileResponse:
    """Serve a library file so the popup can play the matched spot for a by-ear check."""
    p = Path(path).resolve()
    if not p.is_file() or MUSIC_DIR.resolve() not in p.parents:
        raise HTTPException(404, "not a library file")
    return FileResponse(p)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
