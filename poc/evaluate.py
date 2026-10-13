"""Query every test clip against the Panako DB and report identification metrics.

Each 30 s clip is queried whole and as three overlapping 15 s segments
(0-15, 8-23, 15-30 s). A clip is *accepted* when the best full-clip match has
score >= threshold. Segment agreement is reported (`votes` in results.csv) but not
required: on the first full run it rejected many correct noisy matches (crowd 3 dB:
83% -> 66%) without removing the one real misidentification.

Reported per condition and threshold:
  top1      - accepted and correct (in-DB clips)
  wrong     - accepted but the wrong track (in-DB clips)
  false_pos - accepted at all (held-out clips that are NOT in the DB)
  raw_top1  - best full-clip match is correct, ignoring any threshold

Usage: python poc/evaluate.py [--manifest poc/testset/manifest.csv] [--threads 8]
Real recordings: add rows to a manifest CSV (clip, condition, expected, offset) with
expected = full path of the library file, or empty if the song is not in the library.
"""
from __future__ import annotations

import argparse
import csv
import os
import time
import wave
from collections import defaultdict
from pathlib import Path

from common import ROOT, panako, panako_id, parse_query_output

SEGMENTS = [(0, 15), (8, 23), (15, 30)]
THRESHOLDS = [0, 5, 8, 10, 15, 20, 30, 50]
SEG_DIR = ROOT / "poc" / "testset" / "_segments"


def norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)) if p else ""


def cut_segments(clip: Path) -> list[Path]:
    with wave.open(str(clip), "rb") as w:
        sr, params = w.getframerate(), w.getparams()
        frames = w.readframes(w.getnframes())
    width = params.sampwidth * params.nchannels
    out = []
    for a, b in SEGMENTS:
        seg = SEG_DIR / clip.parent.name / f"{clip.stem}_{a:02d}-{b:02d}.wav"
        seg.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(seg), "wb") as w:
            w.setparams(params)
            w.writeframes(frames[a * sr * width:b * sr * width])
        out.append(seg)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ROOT / "poc" / "testset" / "manifest.csv"))
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.manifest, encoding="utf-8")))
    # recordings/log.csv from the extension: only rows marked right/wrong in the popup have ground truth.
    rows = [r for r in rows if r.get("feedback", "x") != ""]
    seg_map = {r["clip"]: cut_segments(Path(r["clip"])) for r in rows}
    queries = [r["clip"] for r in rows] + [str(s) for segs in seg_map.values() for s in segs]

    t0 = time.time()
    results: dict = {}
    for i in range(0, len(queries), 80):
        res = panako("query", queries[i:i + 80], threads=args.threads)
        results.update(parse_query_output(res.stdout))
        print(f"queried {min(i + 80, len(queries))}/{len(queries)}", flush=True)
    per_query = (time.time() - t0) / len(queries)

    detail = []
    for r in rows:
        full = results.get(norm(r["clip"]), [])
        best = full[0] if full else None
        second = full[1].score if len(full) > 1 else 0
        votes = 0
        for seg in seg_map[r["clip"]]:
            sm = results.get(norm(str(seg)), [])
            if best and sm and sm[0].match_path == best.match_path:
                votes += 1
        detail.append({
            "clip": r["clip"], "condition": r["condition"], "expected": r["expected"],
            "match": best.match_path if best else "", "score": best.score if best else 0,
            "second": second, "votes": votes,
            "time_factor": best.time_factor if best else "",
            # compare content ids so a byte-identical copy in another album counts as correct
            "correct": bool(best) and bool(r["expected"]) and best.match_id == panako_id(r["expected"]),
        })

    # The synthetic test set reports into poc/; any other manifest reports next to itself.
    default = Path(args.manifest).resolve() == (ROOT / "poc" / "testset" / "manifest.csv").resolve()
    out_dir = ROOT / "poc" if default else Path(args.manifest).resolve().parent
    out_csv = out_dir / "results.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(detail[0].keys()))
        w.writeheader()
        w.writerows(detail)

    by_cond = defaultdict(list)
    for d in detail:
        by_cond[d["condition"]].append(d)

    lines = [f"Avg query time (amortized, {args.threads} threads): {per_query:.2f} s/query\n"]
    for thr in THRESHOLDS:
        lines.append(f"\n## threshold score >= {thr}")
        lines.append(f"{'condition':<16}{'n':>5}{'raw_top1':>10}{'top1':>8}{'wrong':>8}{'false_pos':>11}")
        for cond, ds in sorted(by_cond.items()):
            n = len(ds)
            accepted = [d for d in ds if d["score"] >= max(thr, 1)]
            if cond.startswith("holdout"):
                lines.append(f"{cond:<16}{n:>5}{'-':>10}{'-':>8}{'-':>8}{len(accepted)/n:>10.0%} ")
            else:
                raw = sum(d["correct"] for d in ds) / n
                top1 = sum(d["correct"] for d in accepted) / n
                wrong = sum(not d["correct"] for d in accepted) / n
                lines.append(f"{cond:<16}{n:>5}{raw:>10.0%}{top1:>8.0%}{wrong:>8.0%}{'-':>11}")
    report = "\n".join(lines)
    (out_dir / "report.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\ndetails -> {out_csv}")


if __name__ == "__main__":
    main()
