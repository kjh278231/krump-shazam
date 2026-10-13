"""Concurrent load test against the running server (python server/app.py).

Sends the same set of Opus recordings with 1, 4 and 8 simultaneous clients and reports latency
and throughput. Requests are titled "loadtest" so their log rows can be removed afterwards
(--cleanup does that). Usage: python server/loadtest.py <webm>... [--cleanup]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics as st
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SERVER = "http://127.0.0.1:8765"
LOG = Path(__file__).resolve().parent.parent / "recordings" / "log.csv"


def post(path: str) -> tuple[float, dict]:
    data = Path(path).read_bytes()
    req = urllib.request.Request(f"{SERVER}/identify?title=loadtest", data=data, method="POST")
    t = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    return time.time() - t, out


def cleanup() -> None:
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    keep = [r for r in rows if r["page_title"] != "loadtest"]
    for r in rows:
        if r["page_title"] == "loadtest" and os.path.exists(r["clip"]):
            os.remove(r["clip"])
    with open(LOG, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(keep)
    print(f"removed {len(rows) - len(keep)} loadtest rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    for f in args.files:  # single-request baseline per file
        dt, out = post(f)
        top = (out.get("songs") or [{}])[0]
        print(f"{Path(f).name:<14} {dt:5.2f}s  {out['verdict']:<9} {out['method']:<11} {top.get('file', '')[:50]}")
    for clients in (1, 4, 8):
        jobs = args.files * max(1, (2 * clients) // len(args.files) + 1)
        t = time.time()
        with ThreadPoolExecutor(clients) as ex:
            res = list(ex.map(post, jobs))
        wall = time.time() - t
        lat = [r[0] for r in res]
        print(f"{clients} clients: {len(jobs)} requests in {wall:.1f}s -> {len(jobs) / wall:.2f} req/s, "
              f"latency median {st.median(lat):.2f}s, max {max(lat):.2f}s")
    if args.cleanup:
        cleanup()


if __name__ == "__main__":
    main()
