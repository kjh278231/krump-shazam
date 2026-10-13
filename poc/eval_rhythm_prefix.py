"""How reliable is the rhythm search on the first N seconds of a recording?

The extension checks the audio recorded so far every few seconds. To run the rhythm search in
those checks (instead of only at the end), we need its accuracy and false-candidate rate for
shorter prefixes. For each query this runs rhythm.identify on the first 15/20/25/30 s.
-> realtest/rhythm_prefix.jsonl
"""
from __future__ import annotations

import json
from pathlib import Path

from common import ROOT, load_excluded
from eval_rhythm import queries
from eval_rhythm_z import USER_KNOWN, MUSIC
from common import panako_id
from grouping import load_links
from rhythm import SR, RhythmIndex, decode, identify

LENGTHS = (15, 20, 25, 30)


def main() -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz", exclude=load_excluded(), cohort=ROOT / "db" / "rhythm_cohort.npz")
    links = load_links()
    same = lambda a, b: a == b or frozenset((a, b)) in links
    qs = [q for q in queries() if q[0] in ("crowd0_room", "holdout_crowd3", "video_known")]
    for name, rel in USER_KNOWN.items():
        lib = next(MUSIC.glob(rel.split("/")[0] + "*/" + rel.split("/")[1]), None) or MUSIC / rel
        qs.append(("user_known", str(ROOT / "recordings" / f"{name}.wav"), 0, panako_id(lib)))
    out = (ROOT / "realtest" / "rhythm_prefix.jsonl").open("w", encoding="utf-8")
    for cond, src, start, expected in qs:
        y = decode(src, start, 30)
        rec = {"cond": cond, "src": Path(src).name, "start": start, "expected": expected, "by_len": {}}
        for L in LENGTHS:
            if len(y) < L * SR - SR // 2:
                continue
            r = identify(index, y[: L * SR], same)
            rec["by_len"][L] = None if r is None else {
                "id": r["songs"][0]["id"], "name": Path(r["songs"][0]["path"]).name, "strength": r["strength"],
                "ratio": round(r["ratio"], 3), "z": round(r["z"], 2)}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        print(cond, rec["src"][:26], {L: (v["strength"][0] + ("+" if v["id"] == expected else "-")) if v else "." for L, v in rec["by_len"].items()}, flush=True)


if __name__ == "__main__":
    main()
