"""When may a mid-recording rhythm candidate end the recording? Calibrates server/app.py RHYTHM_EARLY.

Runs the rhythm search (0.99-1.01x, as in partial checks) on the first 10/15/20 s of:
  pos      battle-100 windows found at 30 s (fingerprint confirmed or rhythm strong) whose song
           shows up in >= 2 windows of the video: likely right, the answer we want early
  neg      battle-100 windows where the 30 s search finds nothing: any early stop there is wrong
  holdout  tracks left out of the rhythm index (db/holdout.txt), clean, at 30/75/120 s, and the
           testset's holdout_crowd3 clips: songs that are not in the library
and stores the raw winner, ratio and agreement, so any thresholds can be simulated afterwards.

Usage: python poc/eval_rhythm_early.py          measure (resumable) -> realtest/rhythm_early.jsonl
       python poc/eval_rhythm_early.py report   simulate stop rules on the stored results
"""
from __future__ import annotations

import collections
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from common import ROOT, load_excluded, panako_id
from grouping import load_links
from rhythm import SPEEDS_FAST, SR, RhythmIndex, decode, envelope

OUT = ROOT / "realtest" / "rhythm_early.jsonl"
BATTLE = ROOT / "realtest" / "battle100"
LENGTHS = (10, 15, 20)
# Stop rules to compare: (seconds of audio, minimum ratio of a strong candidate), first match wins.
RULES = {
    "20 s only (before)": [(20, 1.2)],
    "15 s at 1.3": [(15, 1.3), (20, 1.2)],
    "10 s at 1.5, 15 s at 1.3 (chosen)": [(10, 1.5), (15, 1.3), (20, 1.2)],
    "10 s at 1.4, 15 s at 1.3": [(10, 1.4), (15, 1.3), (20, 1.2)],
    "all at 1.2": [(10, 1.2), (15, 1.2), (20, 1.2)],
}


def measure() -> None:
    index = RhythmIndex(ROOT / "db" / "rhythm.npz", exclude=load_excluded(), cohort=ROOT / "db" / "rhythm_cohort.npz")
    links = load_links()
    same = lambda a, b: a == b or frozenset((a, b)) in links

    def stats(y):
        hits, _, z_top = index.search_full(envelope(y), speeds=SPEEDS_FAST, top=12, cohort=index.cohort, threads=6)
        songs = []
        for h in hits:
            if not any(same(h["id"], s["id"]) for s in songs):
                songs.append(h)
        b = songs[0]
        return {"id": int(b["id"]), "path": str(b["path"]), "ratio": round(b["score"] / songs[1]["score"], 3),
                "z": round(float(b["z"]), 2), "agree": z_top is not None and same(z_top, b["id"])}

    done = {(r["kind"], r["src"], r["t"]) for r in map(json.loads, OUT.open(encoding="utf-8"))} if OUT.exists() else set()
    out = OUT.open("a", encoding="utf-8")

    def run(kind, src, t, y, want):
        if (kind, src, t) in done:
            return
        rec = {"kind": kind, "src": src, "t": t, "want": want,
               "by": {L: stats(y[:L * SR]) for L in LENGTHS if len(y) >= L * SR - SR // 2}}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()

    for p in (ROOT / "db" / "holdout.txt").read_text(encoding="utf-8").splitlines():
        if p.strip() and Path(p).exists():
            for t in (30, 75, 120):
                y = decode(p, t, 20)
                if len(y) >= 10 * SR:
                    run("holdout", Path(p).name, t, y, panako_id(Path(p)))
    for r in csv.DictReader(open(ROOT / "poc" / "testset" / "manifest.csv", encoding="utf-8")):
        if r["condition"] == "holdout_crowd3":
            run("holdout_crowd3", Path(r["clip"]).name, 0, decode(r["clip"], 0, 20),
                panako_id(r["expected"]) if r["expected"] else None)
    print("holdout done", flush=True)

    for line in (BATTLE / "windows.jsonl").open(encoding="utf-8"):
        v = json.loads(line)
        count = collections.Counter(w["songs"][0]["path"] for w in v["windows"] if w.get("songs"))
        x = None
        for w in v["windows"]:
            s = w.get("songs")
            if s and count[s[0]["path"]] >= 2 and (w["verdict"] == "confirmed" or s[0]["strength"] == "strong"):
                kind, want = "pos", s[0]["path"]
            elif not s and w["verdict"] != "error":
                kind, want = "neg", None
            else:
                continue
            if (kind, v["id"], w["t"]) in done:
                continue
            if x is None:
                x = np.frombuffer(subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(BATTLE / "audio" / v["file"]), "-ac", "1", "-ar", str(SR),
                     "-f", "f32le", "pipe:1"], capture_output=True, check=True).stdout, dtype="<f4")
            run(kind, v["id"], w["t"], x[int(w["t"] * SR):int((w["t"] + 20) * SR)], want)
        print(v["id"], flush=True)


def report() -> None:
    rows = [json.loads(l) for l in OUT.open(encoding="utf-8")]
    links = load_links()
    key = lambda p: "/".join(p.replace("\\", "/").lower().split("/")[-2:])
    path_links = collections.defaultdict(set)
    for r in csv.DictReader(open(ROOT / "db" / "same_audio.csv", encoding="utf-8")):
        if int(r["score"]) >= 30 and float(r["seconds"]) >= 15:
            a, b = key(r["path"]), key(r["other_path"])
            path_links[a].add(b)
            path_links[b].add(a)

    def right(r, b):
        if r["kind"] == "pos":
            return key(b["path"]) == key(r["want"]) or key(r["want"]) in path_links[key(b["path"])]
        if r["want"]:  # a holdout track can have a copy in the library
            return b["id"] == r["want"] or frozenset((b["id"], r["want"])) in links
        return False

    kinds = ("pos", "neg", "holdout", "holdout_crowd3")
    print("stops at 10 / 15 / 20 s, wrong stops (holdout 'wrong' includes unlinked copies of the same song)")
    for name, rule in RULES.items():
        print(name)
        for kind in kinds:
            rs = [r for r in rows if r["kind"] == kind]
            at, bad = collections.Counter(), 0
            for r in rs:
                for sec, ratio in rule:
                    b = r["by"].get(str(sec))
                    if b and b["agree"] and b["ratio"] >= ratio:
                        at[sec] += 1
                        bad += not right(r, b)
                        break
            print(f"  {kind:15} n={len(rs):5}  {at[10]:4} / {at[15]:4} / {at[20]:4}   wrong {bad}")


if __name__ == "__main__":
    report() if sys.argv[1:] == ["report"] else measure()
