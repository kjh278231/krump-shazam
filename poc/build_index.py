"""Fingerprint the local library into the Panako DB.

A deterministic ~2% of tracks is held out (never indexed) so the evaluation can
measure false positives on songs that are NOT in the DB. Re-running is safe:
Panako skips files that are already stored.

Usage: python poc/build_index.py [--threads 8] [--holdout-pct 2]
"""
from __future__ import annotations

import argparse
import hashlib
import time

from common import DB_DIR, HOLDOUT_FILE, MUSIC_DIR, ROOT, list_audio, panako

BATCH = 80  # keeps the Windows command line well under its 32k char limit


def is_holdout(rel: str, pct: float) -> bool:
    h = int(hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8], 16)
    return (h % 10000) < pct * 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--holdout-pct", type=float, default=2.0)
    args = ap.parse_args()

    files = list_audio()
    holdout, to_index = [], []
    for f in files:
        rel = f.relative_to(MUSIC_DIR).as_posix()
        (holdout if is_holdout(rel, args.holdout_pct) else to_index).append(f)

    HOLDOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOLDOUT_FILE.write_text("\n".join(str(f) for f in holdout), encoding="utf-8")
    DB_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(files)} tracks: indexing {len(to_index)}, holding out {len(holdout)}")

    log = (ROOT / "db" / "store.log").open("a", encoding="utf-8")
    t0 = time.time()
    for i in range(0, len(to_index), BATCH):
        batch = to_index[i:i + BATCH]
        res = panako("store", [str(f) for f in batch], threads=args.threads)
        log.write(res.stdout)
        errors = [l for l in res.stderr.splitlines() if "Exception" in l or "SEVERE" in l]
        log.write("\n".join(errors) + "\n")
        log.flush()
        done = i + len(batch)
        el = time.time() - t0
        print(f"[{done}/{len(to_index)}] {el/60:.1f} min elapsed, "
              f"~{el/done*(len(to_index)-done)/60:.0f} min left, errors in batch: {len(errors)}",
              flush=True)

    stats = panako("stats", [])
    print(stats.stdout.strip())


if __name__ == "__main__":
    main()
