"""Compute rhythm envelopes (see rhythm.py) for every indexed track -> db/rhythm.npz.

One entry per unique content id (byte-identical copies are skipped), held-out tracks excluded,
same as the Panako DB. Usage: python poc/build_rhythm_index.py [--workers 8]
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from common import HOLDOUT_FILE, ROOT, list_audio, panako_id
from rhythm import decode, envelope

OUT = ROOT / "db" / "rhythm.npz"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    holdout = set(HOLDOUT_FILE.read_text(encoding="utf-8").splitlines())
    tracks, seen = [], set()
    for p in list_audio():
        if str(p) in holdout:
            continue
        pid = panako_id(p)
        if pid not in seen:
            seen.add(pid)
            tracks.append((p, pid))

    t0 = time.time()
    envs = [None] * len(tracks)

    def work(i):
        envs[i] = envelope(decode(tracks[i][0])).astype(np.float16)
        if i % 200 == 0:
            print(f"{i}/{len(tracks)}  {time.time() - t0:.0f}s", flush=True)

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(work, range(len(tracks))))

    lengths = np.array([e.shape[1] for e in envs])
    np.savez(OUT, data=np.concatenate(envs, axis=1), offsets=np.concatenate([[0], np.cumsum(lengths)]),
             ids=np.array([pid for _, pid in tracks]), paths=np.array([str(p) for p, _ in tracks]))
    print(f"{len(tracks)} tracks, {lengths.sum()} frames, {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
