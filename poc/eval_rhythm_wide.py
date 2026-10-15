"""Rhythm search over a wider speed grid (SPEEDS_WIDE) as a second stage after SPEEDS_FAST.

collect: every query's per-speed scores -> realtest/rhythm_wide_speeds.npz (not in git). Queries:
  the rhythm_z.jsonl set (room clips, unknown songs, the DARK video, user captures with a known
  track) plus the same room clips and unknown songs played 1.2-4.8 % faster or slower
  (room_fast, holdout_fast), the speed-ups seen in re-uploads.
report: decisions of the current rule (fast grid only) and of fast-then-wide, per condition,
  using the impostor per-speed scores of `rhythm_cohort.py --wide --dump`.

Usage: python poc/eval_rhythm_wide.py collect
       python poc/eval_rhythm_wide.py report [--wide-ratio 1.3 --wide-z 6 ...]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

from common import FFMPEG, ROOT, load_excluded, panako_id
from eval_rhythm import queries
from eval_rhythm_z import MUSIC, USER_KNOWN
from grouping import load_links
from rhythm import (SPEEDS_FAST, SPEEDS_WIDE, STRONG_RATIO, WEAK_RATIO, WEAK_Z, RhythmIndex, SR, decode,
                    envelope, per_speed_scores)

SCORES = ROOT / "realtest" / "rhythm_wide_speeds.npz"
COHORT = ROOT / "realtest" / "rhythm_cohort_speeds.npz"
REPORT = ROOT / "realtest" / "rhythm_wide_report.txt"
NEW_USER_KNOWN = {
    # shorts hdnJN-k6ePg, played at 1.03
    "20261014-112605-245-f2c1": "KNKRTWORLD - WLDLF/WLDLF - 07 7. This What They Waited For.wav",
    # KOB17 battle, rhythm candidate confirmed by the user in the popup
    "20261014-112404-917-e9ab": "MOZARF - DIRTY BAGZ WITH BUCK MUZIK/MOZARF - DIRTY BAGZ WITH BUCK MUZIK  01 - AINT GO ON DROP.mp3",
}


def decode_speed(path, speed: float) -> np.ndarray:
    """The clip played `speed` times faster (pitch moves too, like a re-upload sped up)."""
    rate = sf.info(str(path)).samplerate
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-af", f"asetrate={rate * speed:.3f},aresample={SR}",
           "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.float32)


def all_queries():
    """(cond, src, start, expected id or None, true speed); audio decoded by load()."""
    base = [q for q in queries() if q[0] != "crowd3"]
    for cond, src, start, expected in base:
        yield cond, src, start, expected, 1.0
    for name, rel in {**USER_KNOWN, **NEW_USER_KNOWN}.items():
        lib = next(MUSIC.glob(rel.split("/")[0] + "*/" + rel.split("/")[1]), None) or MUSIC / rel
        speed = 1.03 if name == "20261014-112605-245-f2c1" else 1.0
        yield "user_known", str(ROOT / "recordings" / f"{name}.wav"), 0, panako_id(lib), speed
    rng = np.random.default_rng(3)
    for cond, src, start, expected in base:
        if cond in ("crowd0_room", "holdout_crowd3"):
            speed = round(float(1 + rng.choice([-1, 1]) * rng.uniform(0.012, 0.048)), 4)
            yield ("room_fast" if cond == "crowd0_room" else "holdout_fast"), src, start, expected, speed


def load(cond, src, start, speed):
    if cond in ("room_fast", "holdout_fast"):
        return decode_speed(src, speed)
    return decode(src, start, 30)


def collect(threads: int) -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz")
    meta, scores = [], []
    for cond, src, start, expected, speed in all_queries():
        y = load(cond, src, start, speed)
        if len(y) < SR * 15:
            continue
        scores.append(per_speed_scores(index, envelope(y), SPEEDS_WIDE, threads))
        meta.append({"cond": cond, "src": Path(src).name, "start": start, "expected": expected, "speed": speed})
        print(len(meta), cond, meta[-1]["src"][:40], start, speed, flush=True)
    np.savez_compressed(SCORES, scores=np.asarray(scores, np.float32), speeds=np.array(SPEEDS_WIDE),
                        meta=json.dumps(meta))


class Rule:
    """rhythm.identify's decision on precomputed per-track scores."""

    def __init__(self, index_ids, excluded, same, cohort_scores, grid):
        self.ids, self.excluded, self.same, self.grid = index_ids, excluded, same, grid
        s = cohort_scores[:, grid].max(1)
        self.mean, self.std = s.mean(0), s.std(0) + 1e-6

    def decide(self, per_speed, strong_ratio, weak_ratio, weak_z):
        best = per_speed[self.grid].max(0)
        usable = np.isfinite(best) & ~self.excluded
        rank = np.where(usable, best, -np.inf)
        with np.errstate(invalid="ignore"):
            z = (best - self.mean) / self.std
        zr = np.where(usable & np.isfinite(z), z, -np.inf)
        z_top = int(self.ids[int(np.argmax(zr))])
        songs = []
        for t in np.argsort(-rank)[:12]:
            tid = int(self.ids[t])
            if not any(self.same(tid, s) for s, _ in songs):
                songs.append((tid, t))
        top_id, top_t = songs[0]
        ratio = best[top_t] / best[songs[1][1]] if len(songs) > 1 else 99.0
        agree = self.same(z_top, top_id)
        if ratio >= strong_ratio and agree:
            strength = "strong"
        elif ratio >= weak_ratio or (agree and z[top_t] >= weak_z):
            strength = "weak"
        else:
            return None
        return {"id": top_id, "strength": strength, "ratio": float(ratio), "z": float(z[top_t]),
                "speed": float(SPEEDS_WIDE[self.grid[int(np.argmax(per_speed[self.grid, top_t]))]])}


def report(args) -> None:
    z = np.load(SCORES)
    scores, meta = z["scores"], json.loads(str(z["meta"]))
    cohort = np.load(COHORT)["scores"]
    paths = np.load(ROOT / "db" / "rhythm.npz")["paths"]
    ids = np.load(ROOT / "db" / "rhythm.npz")["ids"]
    excl = load_excluded()
    import os
    excluded = np.array([os.path.normcase(str(p)) in excl for p in paths])
    links = load_links()
    same = lambda a, b: a == b or frozenset((a, b)) in links
    speeds = list(SPEEDS_WIDE)
    fast = Rule(ids, excluded, same, cohort, [speeds.index(s) for s in SPEEDS_FAST])
    lo, hi = args.range
    wide = Rule(ids, excluded, same, cohort, [i for i, s in enumerate(speeds) if lo - 1e-9 <= s <= hi + 1e-9])

    rows = defaultdict(Counter)
    lines = []
    for m, ps in zip(meta, scores):
        r1 = fast.decide(ps, STRONG_RATIO, WEAK_RATIO, WEAK_Z)
        r2 = r1 or wide.decide(ps, args.wide_strong_ratio, args.wide_ratio, args.wide_z)
        for name, r in (("fast", r1), ("fast+wide", r2)):
            c = rows[(m["cond"], name)]
            c["n"] += 1
            if r:
                ok = m["expected"] is not None and same(r["id"], m["expected"])
                c[r["strength"]] += 1
                c[r["strength"] + "_right"] += ok
        if r2 and not r1:
            ok = m["expected"] is not None and same(r2["id"], m["expected"])
            lines.append(f"  wide only: {m['cond']:14} {m['src'][:34]:34} speed {m['speed']:.4f} -> {r2['strength']:6} "
                         f"found {r2['speed']:.3f} ratio {r2['ratio']:.2f} z {r2['z']:.1f} "
                         f"{'right' if ok else ('wrong' if m['expected'] is not None else '?')}")
    out = [f"wide grid {lo}-{hi}, step 0.005; wide thresholds strong ratio>={args.wide_strong_ratio} and agree, "
           f"weak ratio>={args.wide_ratio} or (agree, z>={args.wide_z})",
           f"{'condition':15} {'rule':10} {'n':>4} {'strong':>12} {'weak':>12}   (shown/right)"]
    for (cond, name), c in sorted(rows.items()):
        out.append(f"{cond:15} {name:10} {c['n']:4} {c['strong']:5}/{c['strong_right']:<5} "
                   f"{c['weak']:5}/{c['weak_right']:<5}")
    out += lines
    text = "\n".join(out)
    print(text)
    if args.save:
        REPORT.write_text(text + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "report"])
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--range", type=float, nargs=2, default=(min(SPEEDS_WIDE), max(SPEEDS_WIDE)))
    ap.add_argument("--wide-strong-ratio", type=float, default=STRONG_RATIO)
    ap.add_argument("--wide-ratio", type=float, default=WEAK_RATIO)
    ap.add_argument("--wide-z", type=float, default=WEAK_Z)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    collect(args.threads) if args.cmd == "collect" else report(args)


if __name__ == "__main__":
    main()
