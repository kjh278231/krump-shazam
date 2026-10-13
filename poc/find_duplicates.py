"""Find library files that contain the same recording (other album/master, a mix or preview
medley that includes the track) by querying every indexed track against the DB.

Uses the cached per-track prints, so no audio is decoded. Writes db/same_audio.csv with one
row per (track, other track, score) link; the server groups candidates with it.

Usage: python poc/find_duplicates.py [--threads 8] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import os

from common import DB_DIR, HOLDOUT_FILE, ROOT, list_audio, panako, panako_id, parse_query_output

OUT = ROOT / "db" / "same_audio.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    holdout = set(HOLDOUT_FILE.read_text(encoding="utf-8").splitlines())
    tracks, seen = [], set()
    for p in list_audio():
        if str(p) in holdout:
            continue
        pid = panako_id(p)
        if pid not in seen:  # byte-identical copies are stored once
            seen.add(pid)
            tracks.append((p, pid))
    if args.limit:
        tracks = tracks[:args.limit]

    links = []
    for i in range(0, len(tracks), 100):
        batch = tracks[i:i + 100]
        res = panako("query", [str(p) for p, _ in batch], threads=args.threads,
                     extra_config={"PANAKO_USE_CACHED_PRINTS": "TRUE", "NUMBER_OF_QUERY_RESULTS": "10"})
        results = parse_query_output(res.stdout)
        for p, pid in batch:
            for m in results.get(os.path.normcase(os.path.abspath(p)), []):
                if m.match_id != pid:
                    links.append({"id": pid, "path": str(p), "other_id": m.match_id,
                                  "other_path": m.match_path, "score": m.score,
                                  "seconds": round(m.query_stop - m.query_start, 1)})
        print(f"{min(i + 100, len(tracks))}/{len(tracks)} tracks, {len(links)} links", flush=True)

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "path", "other_id", "other_path", "score", "seconds"])
        w.writeheader()
        w.writerows(links)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
