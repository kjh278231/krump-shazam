"""Collect sub-window rhythm results (15 s windows, 7.5 s hop) for the calibration queries so
voting rules can be compared offline -> realtest/rhythm_votes.jsonl"""
from __future__ import annotations

import json
from pathlib import Path

from common import ROOT
from eval_rhythm import queries
from grouping import load_links
from rhythm import RhythmIndex, SR, decode, envelope

SPEEDS = (0.99, 1.0, 1.01)


def main() -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
    links = load_links()
    same = lambda a, b: a == b or frozenset((a, b)) in links
    extra = [("user_known", str(ROOT / "recordings" / f"{n}.wav"), 0, None) for n in
             ("20261013-223601-086", "20261013-223645-343", "20261013-210641-257")]
    out = (ROOT / "realtest" / "rhythm_votes.jsonl").open("w", encoding="utf-8")
    for cond, src, start, expected in list(queries()) + extra:
        if cond == "crowd3":
            continue
        y = decode(src, start, 30)
        if len(y) < SR * 15:
            continue
        wins = []
        s = 0.0
        while s + 15 <= len(y) / SR + 0.01:
            hits = index.search(envelope(y[int(s * SR): int((s + 15) * SR)]), speeds=SPEEDS, top=10)
            songs = []
            for h in hits:
                if not any(same(h["id"], x["id"]) for x in songs):
                    songs.append(h)
            wins.append({"t": s, "songs": [{k: (v if k != "path" else Path(v).name) for k, v in h.items()}
                                           for h in songs[:3]]})
            s += 7.5
        rec = {"cond": cond, "src": Path(src).name, "start": start, "expected": expected, "wins": wins}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        print(cond, rec["src"][:28], start, [w["songs"][0]["path"][:25] for w in wins], flush=True)


if __name__ == "__main__":
    main()
