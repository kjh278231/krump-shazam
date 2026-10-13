"""Identify music along whole real event videos, without ground-truth labels.

Each video's audio is cut into 30 s windows every 15 s and every window is queried. A
match is *consistent* when a neighbouring window matched the same track and the position in
the track moved by the same amount as the video did (±2 s, speed-corrected). Random false
matches essentially never line up like that, so consistent matches are almost surely right.

Usage: python poc/real_video_test.py realtest/audio [--titles realtest/videos.tsv]
Outputs realtest/windows.csv and a per-video summary on stdout.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
from pathlib import Path

from common import FFMPEG, ROOT, panako, parse_query_output

WIN, HOP = 30, 15
ACCEPT = 10


def cut_windows(src: Path, out_dir: Path) -> list[tuple[float, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{src.stem}_%04d.wav"
    full = out_dir / f"{src.stem}_full.wav"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "22050", str(full)], check=True)
    dur = os.path.getsize(full) / (22050 * 2)
    wins = []
    t = 0.0
    while t + WIN / 2 <= dur:  # keep a short tail window if at least 15 s long
        w = out_dir / f"{src.stem}_{int(t):04d}.wav"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", str(t), "-t", str(WIN), "-i", str(full), str(w)], check=True)
        wins.append((t, w))
        t += HOP
    full.unlink()
    return wins


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio_dir")
    ap.add_argument("--titles", default=str(ROOT / "realtest" / "videos.tsv"))
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    titles = {}
    if Path(args.titles).exists():
        for line in Path(args.titles).read_text(encoding="utf-8").splitlines():
            vid, _, title = line.split("\t", 2)
            titles[vid] = title

    win_dir = ROOT / "realtest" / "windows"
    videos = {p.stem: cut_windows(p, win_dir) for p in sorted(Path(args.audio_dir).iterdir()) if p.is_file()}
    all_wins = [w for wins in videos.values() for _, w in wins]
    results = {}
    for i in range(0, len(all_wins), 80):
        results.update(parse_query_output(panako("query", [str(w) for w in all_wins[i:i + 80]], threads=args.threads).stdout))

    rows = []
    for vid, wins in videos.items():
        best = []
        for t, w in wins:
            ms = results.get(os.path.normcase(os.path.abspath(w)), [])
            best.append((t, ms[0] if ms and ms[0].score >= ACCEPT else None, ms))
        for i, (t, m, ms) in enumerate(best):
            consistent = False
            if m:
                for j in (i - 1, i + 1):
                    if 0 <= j < len(best) and best[j][1] and best[j][1].match_id == m.match_id:
                        t2, m2 = best[j][0], best[j][1]
                        # Track position at the clip start = match_start - query_start * time_factor
                        p1 = m.match_start - m.query_start * m.time_factor
                        p2 = m2.match_start - m2.query_start * m2.time_factor
                        if abs((p2 - p1) - (t2 - t) * m.time_factor) <= 2:
                            consistent = True
            rows.append({"video": vid, "title": titles.get(vid, ""), "t": t,
                         "match": Path(m.match_path).name if m else "", "score": m.score if m else 0,
                         "second": ms[1].score if len(ms) > 1 else 0,
                         "track_pos": round(m.match_start - m.query_start * m.time_factor, 1) if m else "",
                         "speed": m.time_factor if m else "", "consistent": consistent})

    out = ROOT / "realtest" / "windows.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    tot = {"n": 0, "acc": 0, "cons": 0}
    for vid in videos:
        rs = [r for r in rows if r["video"] == vid]
        acc = [r for r in rs if r["match"]]
        cons = [r for r in acc if r["consistent"]]
        tracks = sorted({r["match"] for r in cons})
        tot["n"] += len(rs); tot["acc"] += len(acc); tot["cons"] += len(cons)
        print(f"\n{vid}  {titles.get(vid, '')[:70]}")
        print(f"  windows {len(rs)}, matched {len(acc)}, consistent {len(cons)}")
        for name in tracks:
            print(f"   - {name}")
    print(f"\nTOTAL windows {tot['n']}, matched {tot['acc']}, consistent {tot['cons']}  -> {out}")


if __name__ == "__main__":
    main()
