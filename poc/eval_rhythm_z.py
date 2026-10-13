"""Compare raw vs cohort-normalised (z) rhythm scores on the calibration queries plus the user's
own extension captures with a known track -> realtest/rhythm_z.jsonl (top songs per query)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from common import ROOT, panako_id
from eval_rhythm import queries
from grouping import load_links
from rhythm import SPEEDS_FAST, RhythmIndex, SR, decode, envelope

MUSIC = ROOT / "music"
USER_KNOWN = {  # recordings/<name>.wav -> library file
    "20261013-210641-257": "lil-roxxdamus-only-in-korea-album/2. Lil ROXXDAMUS - DARK (team Blackout).wav",
    "20261013-223601-086": "ʟʊӄɛ - ATMOSFEAR VOL . II/ʟʊӄɛ - ATMOSFEAR VOL . II - 19 KILL EM - LUKE x 2R.mp3",
    "20261013-223645-343": "ʟʊӄɛ - ATMOSFEAR VOL . II/ʟʊӄɛ - ATMOSFEAR VOL . II - 19 KILL EM - LUKE x 2R.mp3",
    "20261013-223943-351": "ʟʊӄɛ - ATMOSFEAR VOL . II/ʟʊӄɛ - ATMOSFEAR VOL . II - 19 KILL EM - LUKE x 2R.mp3",
    "20261013-225026-921": "MORFMUZIK - FOR THE STREET - MOZARF/MORFMUZIK - FOR THE STREET - MOZARF - 05 Mozarf_Afro Krump.mp3",
}


def ranked(index, scores, same, k=5):
    out = []
    for t in np.argsort(-scores):
        tid = int(index.ids[t])
        if not any(same(tid, o["id"]) for o in out):
            out.append({"id": tid, "name": Path(str(index.paths[t])).name, "s": float(scores[t])})
        if len(out) == k:
            break
    return out


def main() -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
    coh = np.load(ROOT / "db" / "rhythm_cohort.npz")
    links = load_links()
    same = lambda a, b: a == b or frozenset((a, b)) in links
    qs = [q for q in queries() if q[0] != "crowd3"]
    for name, rel in USER_KNOWN.items():
        lib = next(MUSIC.glob(rel.split("/")[0] + "*/" + rel.split("/")[1]), None) or MUSIC / rel
        qs.append(("user_known", str(ROOT / "recordings" / f"{name}.wav"), 0, panako_id(lib)))
    out = (ROOT / "realtest" / "rhythm_z.jsonl").open("w", encoding="utf-8")
    for cond, src, start, expected in qs:
        y = decode(src, start, 30)
        if len(y) < SR * 15:
            continue
        index.search(envelope(y), speeds=SPEEDS_FAST, top=1)
        raw = index.last_best.copy()
        z = (raw - coh["mean"]) / coh["std"]
        rec = {"cond": cond, "src": Path(src).name, "start": start, "expected": expected,
               "raw": ranked(index, raw, same), "z": ranked(index, z, same)}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        print(cond, rec["src"][:26], start, "| raw:", rec["raw"][0]["name"][:30], "| z:", rec["z"][0]["name"][:30],
              round(rec["z"][0]["s"], 1), round(rec["z"][1]["s"], 1), flush=True)


if __name__ == "__main__":
    main()
