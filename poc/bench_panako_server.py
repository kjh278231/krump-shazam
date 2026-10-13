"""Benchmark the long-running Panako query server against one-shot `panako query` calls.

Checks that both return the same top match, then measures sequential latency and concurrent
throughput. Usage: python poc/bench_panako_server.py [--n 24] [--threads 8]
"""
from __future__ import annotations

import argparse
import csv
import shutil
import statistics as st
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import FFMPEG, ROOT, PanakoServer, panako, parse_query_output


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(ROOT / "poc" / "testset" / "manifest.csv", encoding="utf-8")))
    conds = ["clean", "crowd3", "speed+3", "holdout_crowd3"]
    src = [r["clip"] for c in conds for r in [x for x in rows if x["condition"] == c][: args.n // len(conds)]]
    tmp = ROOT / "poc" / "bench_tmp"
    tmp.mkdir(exist_ok=True)
    # 16 kHz mono WAVs are read by Panako directly (no ffmpeg), the way the server feeds it.
    clips = []
    for i, c in enumerate(src):
        out = tmp / f"c{i:02d}.wav"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", c, "-ac", "1", "-ar", "16000", str(out)], check=True)
        clips.append(str(out))
    # A path with characters outside cp949, which broke command-line arguments before.
    odd = tmp / "한글 ★ Ünïcode クランプ.wav"
    shutil.copy(clips[0], odd)
    clips.append(str(odd))

    print(f"{len(clips)} clips")
    one_shot, ref = [], {}
    for c, orig in list(zip(clips, src))[:8]:
        # Reference: the original 22.05 kHz clip through one-shot query (ffmpeg pipe decoder).
        t = time.time()
        res = parse_query_output(panako("query", [orig], threads=1, extra_config={"NUMBER_OF_QUERY_RESULTS": "10"}).stdout)
        one_shot.append(time.time() - t)
        ref[c] = next(iter(res.values()), [])
    print(f"one-shot query: median {st.median(one_shot):.2f}s per clip (sequential, {len(one_shot)} clips)")

    t = time.time()
    server = PanakoServer(threads=args.threads, extra_config={"NUMBER_OF_QUERY_RESULTS": "10"})
    print(f"server start-up: {time.time() - t:.1f}s (once)")

    server.query(clips[1])  # warm-up (JIT, page cache)
    seq, got = [], {}
    for c in clips:
        t = time.time()
        got[c] = server.query(c)
        seq.append(time.time() - t)
    print(f"server, sequential: median {st.median(seq):.2f}s, max {max(seq):.2f}s per clip")

    same = sum(1 for c in ref if [(m.match_id, m.score) for m in ref[c][:1]] == [(m.match_id, m.score) for m in got[c][:1]])
    print(f"same top match as one-shot: {same}/{len(ref)}")
    print(f"non-ASCII path: {'OK' if got[str(odd)] and got[str(odd)][0].match_id == got[clips[0]][0].match_id else 'FAILED'}"
          f" ({len(got[str(odd)])} matches)")

    for workers in (2, 4, 8):
        t = time.time()
        with ThreadPoolExecutor(workers) as ex:
            out = list(ex.map(server.query, clips * 2))
        dt = time.time() - t
        print(f"server, {workers} concurrent callers: {len(out) / dt:.1f} queries/s "
              f"({dt / len(out):.2f}s amortised), results identical: "
              f"{all([(m.match_id, m.score) for m in o[:1]] == [(m.match_id, m.score) for m in got[c][:1]] for o, c in zip(out, clips * 2))}")
    server.close()
    shutil.rmtree(odd.parent)


if __name__ == "__main__":
    main()
