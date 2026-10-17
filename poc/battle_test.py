"""Run the server's identification along whole battle videos, locally (no Lambda).

Each video's audio is cut into 30 s windows every 15 s. Every window goes through
server/app.py's identify_audio exactly as a final extension search would: fingerprint, then
the rhythm search at 0.99-1.01x, then 0.95-1.05x if that shows nothing. Nothing is logged or
uploaded. A video longer than ~3 min often has several tracks; the report
(poc/battle_report.py) groups consecutive windows into songs.

Usage: python poc/battle_test.py [realtest/battle100] [--follow]
  --follow  keep waiting for new audio until battle_download.py writes "done" to download.log
Appends one JSON line per video to <dir>/windows.jsonl (resumable).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WIN, HOP = 30, 15
SR = 16000  # server/app.py PANAKO_SR


def windows(path: Path):
    pcm = np.frombuffer(subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
        capture_output=True, check=True).stdout, dtype="<i2")
    dur = len(pcm) / SR
    t = 0
    while t + WIN / 2 <= dur:  # keep a tail window of at least 15 s
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm[int(t * SR):int((t + WIN) * SR)].tobytes())
        yield t, buf.getvalue()
        t += HOP


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default="realtest/battle100")
    ap.add_argument("--follow", action="store_true")
    args = ap.parse_args()
    d = ROOT / args.dir
    os.environ.setdefault("KF_TMP", str(d / "tmp"))
    os.environ.setdefault("RHYTHM_THREADS", "6")
    sys.path.insert(0, str(ROOT / "server"))
    sys.path.insert(0, str(ROOT / "poc"))
    import app  # noqa: E402  (starts the Panako query server, loads the rhythm index)

    app.save_record = lambda row, wav: wav.unlink(missing_ok=True)  # keep nothing
    titles = {}
    for name in ("videos.tsv", "meta.tsv"):  # meta.tsv (yt-dlp, UTF-8) wins
        f = d / name
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                p = line.split("\t")
                if len(p) >= 3:
                    titles[p[0]] = p[-1]
    out = d / "windows.jsonl"
    done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()} if out.exists() else set()

    while True:
        todo = [p for p in sorted((d / "audio").glob("*")) if p.stem not in done and p.suffix not in (".part", ".ytdl")]
        for p in todo:
            t0 = time.time()
            rows = []
            for t, wav in windows(p):
                try:
                    r = app.identify_audio(wav, titles.get(p.stem, ""), "", False)
                except Exception as e:  # e.g. a silent window
                    rows.append({"t": t, "verdict": "error", "error": str(getattr(e, "detail", e))[:200]})
                    continue
                songs = []
                for s in r["songs"][:3]:
                    # Track position at the window start (fingerprint matches start mid-window).
                    pos = s["match_start"] - s["query_start"] * s["time_factor"]
                    songs.append({"path": s["path"], "score": s["score"], "pos": round(pos, 2),
                                  "speed": round(s["time_factor"], 4), "strength": s.get("strength", ""),
                                  "ratio": s.get("ratio", "")})
                rows.append({"t": t, "verdict": r["verdict"], "method": r["method"],
                             "timings": r.get("timings", {}), "songs": songs})
            rec = {"id": p.stem, "file": p.name, "title": titles.get(p.stem, ""), "windows": rows,
                   "seconds": round(time.time() - t0, 1)}
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done.add(p.stem)
            found = sum(1 for w in rows if w.get("songs"))
            print(f"{p.stem} windows {len(rows)} with a song {found} ({rec['seconds']} s)", flush=True)
        log = d / "download.log"
        finished = log.exists() and log.read_text(encoding="utf-8").rstrip().endswith("done")
        if not args.follow or (finished and not todo):
            break
        if not todo:
            time.sleep(60)
    os._exit(0)  # the Panako server thread would keep the process alive


if __name__ == "__main__":
    main()
