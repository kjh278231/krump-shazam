"""Per-track score normalisation for the rhythm search -> db/rhythm_cohort.npz
(--wide: db/rhythm_cohort_wide.npz for SPEEDS_WIDE)

Some library tracks (plain drum loops, "bvckloop" tracks) correlate with almost any krump beat
and win searches they have nothing to do with. Querying the library with audio that is NOT in it
gives each track's usual score; search results are then compared as z = (score - mean) / std.
The usual score depends on the speeds searched (more speeds, more chances to line up by tempo
alone), so each speed grid has its own cohort.

The impostor queries are 30 s windows of held-out tracks (never indexed) that are not used by the
evaluation's holdout clips, both clean and mixed with synthetic crowd noise.
Usage: python poc/rhythm_cohort.py [--wide [--dump realtest/rhythm_cohort_speeds.npz]]
  --dump keeps every query's per-speed scores, so poc/eval_rhythm_wide.py can try other grids.
"""
from __future__ import annotations

import argparse
import random

import numpy as np

from common import HOLDOUT_FILE, ROOT, list_audio, panako_id
from make_testset import crowd, mix
from rhythm import SPEEDS_FAST, SPEEDS_WIDE, RhythmIndex, SR, decode, envelope, per_speed_scores


def impostor_queries():
    # Re-derive which held-out tracks make_testset.py (seed 1) used for its holdout clips, so
    # the cohort never sees the false-positive test songs.
    holdout = set(HOLDOUT_FILE.read_text(encoding="utf-8").splitlines())
    lib = [p for p in list_audio() if str(p) not in holdout]
    lib_ids = {panako_id(p) for p in lib}
    unknown = sorted(h for h in holdout if panako_id(h) not in lib_ids)
    pyrng = random.Random(1)
    pyrng.sample(lib, 100)
    eval_sources = set(pyrng.sample(unknown, 40))
    impostors = [p for p in unknown if p not in eval_sources]
    rng = np.random.default_rng(7)
    random.seed(7)
    for n, p in enumerate(impostors):
        y = decode(p)
        dur = len(y) / SR
        if dur < 40:
            continue
        for start in sorted(random.sample(range(5, int(dur) - 32), 2)):
            q = y[start * SR:(start + 30) * SR].astype(np.float64)
            for noisy in (False, True):
                # make_testset's crowd generator works at 22050 Hz; decimate to our rate.
                x = mix(q, crowd(2 * len(q), rng)[::2], 3) if noisy else q
                yield x.astype(np.float32)
        print(f"{n + 1}/{len(impostors)} impostor tracks", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wide", action="store_true")
    ap.add_argument("--dump")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    speeds = SPEEDS_WIDE if args.wide else SPEEDS_FAST
    out = ROOT / "db" / ("rhythm_cohort_wide.npz" if args.wide else "rhythm_cohort.npz")
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
    per_speed = [per_speed_scores(index, envelope(x), speeds, args.threads) for x in impostor_queries()]
    s = np.max(per_speed, axis=1)  # (queries, tracks): best over the grid, as search() scores
    np.savez(out, mean=s.mean(0), std=s.std(0) + 1e-6, n=len(s), speeds=np.array(speeds))
    print(f"{len(s)} impostor queries -> {out}")
    if args.dump:
        np.savez_compressed(ROOT / args.dump, scores=np.asarray(per_speed, np.float32), speeds=np.array(speeds))


if __name__ == "__main__":
    main()
