"""Per-track score normalisation for the rhythm search -> db/rhythm_cohort.npz

Some library tracks (plain drum loops, "bvckloop" tracks) correlate with almost any krump beat
and win searches they have nothing to do with. Querying the library with audio that is NOT in it
gives each track's usual score; search results are then compared as z = (score - mean) / std.

The impostor queries are 30 s windows of held-out tracks (never indexed) that are not used by the
evaluation's holdout clips, both clean and mixed with synthetic crowd noise.
Usage: python poc/rhythm_cohort.py
"""
from __future__ import annotations

import random

import numpy as np

from common import HOLDOUT_FILE, ROOT, list_audio, panako_id
from make_testset import crowd, mix
from rhythm import SPEEDS_FAST, RhythmIndex, SR, decode, envelope

OUT = ROOT / "db" / "rhythm_cohort.npz"


def main() -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
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
    scores = []
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
                index.search(envelope(x.astype(np.float32)), speeds=SPEEDS_FAST, top=1)
                scores.append(index.last_best.copy())
        print(f"{n + 1}/{len(impostors)} impostor tracks, {len(scores)} queries", flush=True)
    s = np.asarray(scores)
    np.savez(OUT, mean=s.mean(0), std=s.std(0) + 1e-6, n=len(s))
    print(f"{len(s)} impostor queries -> {OUT}")


if __name__ == "__main__":
    main()
