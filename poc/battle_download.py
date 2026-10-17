"""Download the audio of the videos in a list (realtest/battle100/videos.tsv), politely.

YouTube blocks yt-dlp as a bot after about 10 quick downloads. This pauses between videos and,
when blocked, waits BLOCK_WAIT before trying again; it never works around the block. Resumable:
videos already downloaded or marked unavailable are skipped.

Usage: python poc/battle_download.py [realtest/battle100]
Writes <dir>/audio/<id>.<ext>, <dir>/meta.tsv (id, upload date, title) and <dir>/download.log.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAUSE = (20, 40)         # seconds between videos
BLOCK_WAIT = 45 * 60     # after a bot check
BLOCKED = ("confirm you", "not a bot", "HTTP Error 429", "rate-limit")
GONE = ("Video unavailable", "Private video", "removed", "members-only", "account associated")


def main() -> None:
    d = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "realtest/battle100")
    audio = d / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    log = (d / "download.log").open("a", encoding="utf-8")

    def say(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    ids = [l.split("\t")[0] for l in (d / "videos.tsv").read_text(encoding="utf-8").splitlines() if l.strip()]
    gone_file = d / "unavailable.txt"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for n, vid in enumerate(ids, 1):
        gone = set(gone_file.read_text().split()) if gone_file.exists() else set()
        if vid in gone or any(audio.glob(f"{vid}.*")):
            continue
        errors = 0
        while True:
            r = subprocess.run(["yt-dlp", "-f", "bestaudio", "--no-playlist", "--no-progress",
                                "-o", str(audio / "%(id)s.%(ext)s"),
                                "--print-to-file", "%(id)s\t%(upload_date)s\t%(title)s", str(d / "meta.tsv"),
                                f"https://www.youtube.com/watch?v={vid}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
            if r.returncode == 0 and any(audio.glob(f"{vid}.*")):
                say(f"[{n}/{len(ids)}] ok {vid}")
                break
            if any(k in r.stderr for k in BLOCKED):
                say(f"[{n}/{len(ids)}] blocked ({err[:120]}); waiting {BLOCK_WAIT // 60} min")
                time.sleep(BLOCK_WAIT)
                continue
            errors += 1
            if any(k in r.stderr for k in GONE) or errors >= 3:
                say(f"[{n}/{len(ids)}] unavailable {vid}: {err[:120]}")
                with gone_file.open("a") as fh:
                    fh.write(vid + "\n")
                break
            say(f"[{n}/{len(ids)}] error {vid}: {err[:200]}; retrying in 5 min")
            time.sleep(300)
        time.sleep(random.uniform(*PAUSE))
    say("done")


if __name__ == "__main__":
    main()
