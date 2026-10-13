"""Score the rhythm fallback on labelled and unlabelled queries -> realtest/rhythm_eval.csv.

Positives: synthetic room/crowd clips (poc/testset) and windows of real videos with a known track.
Negatives: held-out (not indexed) tracks and battle-video windows whose songs are unknown.
Usage: python poc/eval_rhythm.py [--speeds 0.98,0.99,1.0,1.01,1.02]
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from common import ROOT, panako_id
from grouping import load_links
from rhythm import RhythmIndex, decode, envelope

KNOWN_VIDEOS = {  # video id -> (library file, section start, end) in seconds
    "N5w_1Jv0S8U": ("lil-roxxdamus-only-in-korea-album/2. Lil ROXXDAMUS - DARK (team Blackout).wav", 30, 150),
}


def queries():
    for r in csv.DictReader(open(ROOT / "poc" / "testset" / "manifest.csv", encoding="utf-8")):
        if r["condition"] in ("crowd0_room", "crowd3", "holdout_crowd3"):
            yield r["condition"], r["clip"], 0, (panako_id(r["expected"]) if r["expected"] else None)
    for vid, (rel, a, b) in KNOWN_VIDEOS.items():
        src = next((ROOT / "realtest").rglob(f"{vid}.*"))
        for t in range(a, b - 29, 15):
            yield "video_known", str(src), t, panako_id(ROOT / "music" / rel)
    for src in sorted((ROOT / "realtest" / "audio").iterdir()):
        for t in range(0, 600, 30):
            yield "battle_unknown", str(src), t, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speeds", default="0.98,0.99,1.0,1.01,1.02")
    args = ap.parse_args()
    speeds = [float(s) for s in args.speeds.split(",")]

    t0 = time.time()
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
    print(f"index loaded in {time.time() - t0:.0f}s", flush=True)
    links = load_links()

    rows = []
    for cond, src, start, expected in queries():
        y = decode(src, start, 30)
        if len(y) < 11025 * 20:
            continue
        t1 = time.time()
        hits = index.search(envelope(y), speeds=speeds, top=10)
        top = hits[0]
        same = lambda h: h["id"] == top["id"] or frozenset((h["id"], top["id"])) in links
        rival = next((h for h in hits[1:] if not same(h)), None)
        rows.append({"cond": cond, "src": Path(src).name, "start": start,
                     "top": Path(top["path"]).name, "score": round(top["score"], 4),
                     "rival": round(rival["score"], 4) if rival else 0,
                     "ratio": round(top["score"] / rival["score"], 3) if rival else 99,
                     "speed": top["speed"], "pos": round(top["pos"], 1),
                     "correct": "" if expected is None else (top["id"] == expected or
                                                             frozenset((top["id"], expected)) in links),
                     "secs": round(time.time() - t1, 2)})
        print(cond, rows[-1]["src"][:30], start, rows[-1]["top"][:40], rows[-1]["score"], rows[-1]["ratio"],
              rows[-1]["correct"], rows[-1]["secs"], flush=True)

    out = ROOT / "realtest" / "rhythm_eval.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
