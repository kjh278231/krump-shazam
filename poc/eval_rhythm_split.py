"""Rhythm fallback with a self-consistency check: a 30 s query is searched as two 20 s halves
(0-20, 10-30). Accept only when both halves pick the same song and their track positions are
10 s apart (speed-corrected). -> realtest/rhythm_split.csv
"""
from __future__ import annotations

import csv
from pathlib import Path

from common import ROOT
from eval_rhythm import queries
from grouping import load_links
from rhythm import RhythmIndex, decode, envelope, FPS

SPEEDS = [0.98, 0.99, 1.0, 1.01, 1.02]


def rhythm_identify(index, links, y, sr=11025):
    """Returns (song id, path, pos, min ratio, scores) or None when the halves disagree."""
    a = envelope(y[: 20 * sr])
    b = envelope(y[10 * sr: 30 * sr])
    out = []
    for e in (a, b):
        hits = index.search(e, speeds=SPEEDS, top=10)
        top = hits[0]
        same = lambda h, t=top: h["id"] == t["id"] or frozenset((h["id"], t["id"])) in links
        rival = next((h for h in hits[1:] if not same(h)), None)
        out.append((top, top["score"] / rival["score"] if rival else 99))
    (ta, ra), (tb, rb) = out
    same_song = ta["id"] == tb["id"] or frozenset((ta["id"], tb["id"])) in links
    consistent = ta["id"] == tb["id"] and abs((tb["pos"] - ta["pos"]) - 10 * ta["speed"]) < 1.0
    return {"top": Path(ta["path"]).name, "id": ta["id"], "pos": ta["pos"], "speed": ta["speed"],
            "score": round(min(ta["score"], tb["score"]), 4), "ratio": round(min(ra, rb), 3),
            "same_song": same_song, "consistent": consistent}


def main() -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
    links = load_links()
    rows = []
    for cond, src, start, expected in queries():
        if cond == "crowd3":
            continue
        y = decode(src, start, 30)
        if len(y) < 11025 * 30:
            continue
        r = rhythm_identify(index, links, y)
        r.update(cond=cond, src=Path(src).name, start=start,
                 correct="" if expected is None else (r["id"] == expected or frozenset((r["id"], expected)) in links))
        rows.append(r)
        print(cond, r["src"][:28], start, r["top"][:40], r["score"], r["ratio"], r["consistent"], r["correct"], flush=True)
    out = ROOT / "realtest" / "rhythm_split.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
