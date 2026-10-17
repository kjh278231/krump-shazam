"""HTML report of poc/battle_test.py's results, for checking each found song by ear.

Consecutive windows that hit the same recording at positions advancing with the video (+-3 s,
speed-corrected, gaps of one window allowed) form a range. All ranges of one recording in a
video (DJ jump-backs, the search naming another copy or an extended version) form one song
card with a single 맞음 / 틀림 / 모르겠음; each range has two players, the video's audio and the
library track from the matching moment. Answers are kept in the browser (localStorage) and exported as JSON
with the button at the top; give that file back to evaluate.

Both sides are pre-cut 12 s MP3 clips in <dir>/clips/ (from the downloaded audio and from
music/), so the page plays short files from their start instead of seeking inside long ones.
Open it from disk: realtest/battle100/report.html. It is not meant to be published.

Usage: python poc/battle_report.py [realtest/battle100]
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poc"))
from common import MUSIC_DIR, list_audio, panako_id  # noqa: E402
from grouping import load_links  # noqa: E402

LINKS = load_links()


@lru_cache(maxsize=None)
def _id(key: str) -> int:
    return panako_id(MUSIC_DIR / key)


def same_audio(a: str, b: str) -> bool:
    """Library keys holding the same recording (another album's copy, or a mix containing it),
    as the server groups them (db/same_audio.csv). The rhythm search picks either copy, since
    they score alike, so consecutive windows of one song can name different files."""
    return a == b or _id(a) == _id(b) or frozenset((_id(a), _id(b))) in LINKS
LEVEL = {  # (verdict, method, strength) -> label, rank (higher = more trusted)
    ("confirmed", "fingerprint", ""): ("확정", 4),
    ("check", "fingerprint", ""): ("fingerprint 추가 확인", 2),
    ("check", "rhythm", "strong"): ("rhythm 유력", 3),
    ("check", "rhythm", "weak"): ("rhythm 약한", 1),
}
WIN = 30
CLIP = 12  # seconds per side


def level(w: dict):
    if w.get("verdict") not in ("confirmed", "check") or not w.get("songs"):
        return None
    s = w["songs"][0]
    return LEVEL.get((w["verdict"], w["method"], s.get("strength", "") if w["method"] == "rhythm" else ""))


def copies() -> dict[str, list[str]]:
    """Library key -> the other library files holding the same recording (copies on other albums,
    mixes that contain it), shown on each segment like the popup's 같은 음원 note."""
    keys = [f.relative_to(MUSIC_DIR).as_posix() for f in list_audio()]
    by_id: dict[int, list[str]] = {}
    for k in keys:
        by_id.setdefault(_id(k), []).append(k)
    out = {}
    for k in keys:
        i = _id(k)
        linked = [j for pair in LINKS if i in pair for j in pair if j != i]
        out[k] = [x for j in [i, *linked] for x in by_id.get(j, []) if x != k]
    return out


def segments(windows: list[dict]) -> list[dict]:
    segs: list[dict] = []
    for w in windows:
        lv = level(w)
        if not lv:
            continue
        s = w["songs"][0]
        last = segs[-1] if segs else None
        # A mix containing the song is linked too, but at another position: the position check
        # keeps it a separate segment.
        if (last and same_audio(last["path"], s["path"]) and w["t"] - last["t_last"] <= 30
                and abs((s["pos"] - last["pos_last"]) - (w["t"] - last["t_last"]) * s["speed"]) <= 3):
            last.update(t_last=w["t"], pos_last=s["pos"], n=last["n"] + 1)
            if s["path"] != last["path"] and s["path"] not in last["also"]:
                last["also"].append(s["path"])
            if lv[1] > last["rank"]:
                last.update(label=lv[0], rank=lv[1])
            last["best_score"] = max(last["best_score"], s["score"])
            continue
        segs.append({"path": s["path"], "t": w["t"], "pos": s["pos"], "speed": s["speed"], "t_last": w["t"],
                     "pos_last": s["pos"], "n": 1, "label": lv[0], "rank": lv[1], "method": w["method"],
                     "best_score": s["score"], "ratio": s.get("ratio", ""), "also": []})
    return segs


def songs(segs: list[dict]) -> list[dict]:
    """One entry per recording heard in the video, its ranges in video order."""
    out: list[dict] = []
    for x in segs:
        home = next((g for g in out if any(same_audio(r["path"], x["path"]) for r in g["ranges"])), None)
        if home is None:
            home = {"ranges": []}
            out.append(home)
        home["ranges"].append(x)
    for g in out:
        count: dict[str, int] = {}
        for r in g["ranges"]:
            count[r["path"]] = count.get(r["path"], 0) + r["n"]
        g["path"] = max(count, key=count.get)  # the copy named most often
        g["also"] = sorted({q for r in g["ranges"] for q in [r["path"], *r["also"]]} - {g["path"]})
        best = max(g["ranges"], key=lambda r: r["rank"])
        g.update(rank=best["rank"], label=best["label"], n=sum(r["n"] for r in g["ranges"]))
    return out


def cut(src: Path, start: float, out: Path) -> None:
    if out.exists():
        return
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.2f}", "-t", str(CLIP), "-i", str(src),
                    "-vn", "-ac", "1", "-b:a", "64k", "-af", "afade=t=in:d=0.2", str(out)], check=True)


def make_clips(d: Path, data: list[dict]) -> None:
    """<id>_<t>.mp3 (video side) and <id>_<t>_lib.mp3 (library side) for every segment."""
    clips = d / "clips"
    clips.mkdir(exist_ok=True)
    jobs = []
    for v in data:
        for s in (r for g in v["songs"] for r in g["ranges"]):
            jobs.append((d / "audio" / v["file"], s["t"], clips / f"{v['id']}_{s['t']}.mp3"))
            jobs.append((ROOT / "music" / s["path"], s["pos"], clips / f"{v['id']}_{s['t']}_lib.mp3"))
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda j: cut(*j), jobs))


def main() -> None:
    d = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "realtest/battle100")
    videos = [json.loads(l) for l in (d / "windows.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    order = {l.split("\t")[0]: i for i, l in enumerate((d / "videos.tsv").read_text(encoding="utf-8").splitlines())}
    videos.sort(key=lambda v: order.get(v["id"], 1e9))
    data = []
    same = copies()
    for v in videos:
        wins = v["windows"]
        dur = (wins[-1]["t"] + WIN) if wins else 0
        segs = segments(wins)
        for x in segs:  # every copy of the recording, not only the ones the search happened to name
            x["also"] = sorted(set(x["also"]) | set(same.get(x["path"], [])))
        keep = ("path", "t", "pos", "speed", "t_last", "n", "label", "rank", "method", "best_score", "ratio")
        data.append({"id": v["id"], "file": v["file"], "title": v["title"], "dur": dur, "n_windows": len(wins),
                     "songs": [{**{k: g[k] for k in ("path", "also", "rank", "label", "n")},
                                "ranges": [{k: r[k] for k in keep} for r in g["ranges"]]} for g in songs(segs)]})
    make_clips(d, data)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__DATA__", payload).replace("__DIR__", html.escape(d.name))
    out = d / "report.html"
    out.write_text(page, encoding="utf-8")
    n_song = sum(len(v["songs"]) for v in data)
    print(f"{len(data)} videos, {sum(1 for v in data if v['songs'])} with a song, {n_song} songs -> {out}")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Battle Test Review</title>
<style>
:root {
  --bg: #f6f5f2; --card: #ffffff; --ink: #1d1d1b; --muted: #6b6a66; --line: #e3e1db;
  --accent: #b4361f; --ok: #2f7d4f; --bad: #b4361f; --unk: #8a7a2e;
  --l4: #2f7d4f; --l3: #3f6fb0; --l2: #8a7a2e; --l1: #a08fb8; --none: #e3e1db;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #171716; --card: #222220; --ink: #ecebe6; --muted: #a09e97; --line: #34332f;
    --accent: #e06a52; --ok: #5fb683; --bad: #e06a52; --unk: #cdb75a;
    --l4: #5fb683; --l3: #6e9be0; --l2: #cdb75a; --l1: #b9a6d6; --none: #34332f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; }
header { position: sticky; top: 0; z-index: 5; background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 12px 16px; }
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 18px; margin: 0 0 6px; }
.stats { color: var(--muted); font-size: 13px; }
.bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }
button, select { font: inherit; font-size: 13px; border: 1px solid var(--line); background: var(--card);
  color: var(--ink); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
button.primary { background: var(--ink); color: var(--bg); border-color: var(--ink); }
main { padding: 12px 16px 80px; }
.video { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin: 12px 0; }
.vhead { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
.vtitle { font-weight: 600; overflow-wrap: anywhere; }
.vmeta { color: var(--muted); font-size: 13px; white-space: nowrap; }
.timeline { position: relative; height: 14px; background: var(--none); border-radius: 3px; margin: 10px 0 4px; overflow: hidden; }
.timeline span { position: absolute; top: 0; bottom: 0; border-radius: 2px; }
.range { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding-left: 8px; border-left: 3px solid var(--line); }
.seg { border-top: 1px solid var(--line); padding: 10px 0 4px; display: grid; gap: 6px; }
.seg:first-of-type { border-top: 0; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.badge { font-size: 12px; padding: 1px 8px; border-radius: 999px; color: #fff; white-space: nowrap; }
.track { font-weight: 600; overflow-wrap: anywhere; }
.folder { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.small { color: var(--muted); font-size: 12px; }
.play.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.ans button[aria-pressed="true"][data-v="ok"] { background: var(--ok); color: #fff; border-color: var(--ok); }
.ans button[aria-pressed="true"][data-v="bad"] { background: var(--bad); color: #fff; border-color: var(--bad); }
.ans button[aria-pressed="true"][data-v="unk"] { background: var(--unk); color: #fff; border-color: var(--unk); }
textarea { width: 100%; font: inherit; font-size: 13px; background: var(--bg); color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px; min-height: 34px; resize: vertical; }
.none { color: var(--muted); font-size: 13px; padding: 6px 0; }
a { color: var(--accent); }
.legend span { display: inline-flex; align-items: center; gap: 4px; margin-right: 10px; }
.legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
@media (max-width: 600px) { .vhead { flex-direction: column; } }
</style>
</head>
<body>
<header><div class="wrap">
  <h1>크럼프 배틀 영상 100개 — 찾은 곡 검증</h1>
  <div class="stats" id="stats"></div>
  <div class="bar">
    <select id="filter">
      <option value="all">전체 영상</option>
      <option value="todo">검증 안 한 곡이 있는 영상</option>
      <option value="found">곡을 찾은 영상</option>
      <option value="none">못 찾은 영상</option>
    </select>
    <select id="level">
      <option value="0">모든 신뢰도</option>
      <option value="3">rhythm 유력 이상</option>
      <option value="4">확정만</option>
      <option value="2">fingerprint 추가 확인만</option>
      <option value="1">rhythm 약한만</option>
    </select>
    <select id="span">
      <option value="1">구간 1개 이상에서 잡힌 곡</option>
      <option value="2" selected>구간 2개 이상에서 잡힌 곡 (먼저 검증 추천)</option>
      <option value="3">구간 3개 이상에서 잡힌 곡</option>
    </select>
    <button class="primary" id="export">검증 결과 내보내기 (JSON)</button>
    <span class="legend small">
      <span><i style="background:var(--l4)"></i>확정</span>
      <span><i style="background:var(--l3)"></i>rhythm 유력</span>
      <span><i style="background:var(--l2)"></i>fingerprint 추가 확인</span>
      <span><i style="background:var(--l1)"></i>rhythm 약한</span>
    </span>
  </div>
  <div class="small" style="margin-top:6px">곡마다 한 번만 답하면 됩니다. 들린 곳마다 ▶ 영상 / ▶ 라이브러리가 같은 순간부터 12초씩 재생됩니다 (다시 누르면 멈춤).
  답은 이 브라우저에 자동 저장되고, 끝나면 "내보내기"로 JSON을 받아 전달해 주세요.</div>
</div></header>
<main><div class="wrap" id="list"></div></main>
<audio id="player"></audio>
<script>
const DATA = __DATA__;
const KEY = 'battle-review:__DIR__';
let answers = {};
try { answers = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(answers)); } catch {} };
const color = { 4: 'var(--l4)', 3: 'var(--l3)', 2: 'var(--l2)', 1: 'var(--l1)' };
const fmt = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const songKey = (v, g) => `${v.id}|${g.path}`;
// Answers given before songs were grouped were per range: adopt the first one found.
for (const v of DATA) for (const g of v.songs) {
  const k = songKey(v, g);
  if (answers[k]) continue;
  for (const r of g.ranges) {
    const old = answers[`${v.id}@${r.t}|${r.path}`];
    if (old) { answers[k] = old; break; }
  }
}

function render() {
  const f = document.getElementById('filter').value;
  const lv = Number(document.getElementById('level').value), span = Number(document.getElementById('span').value);
  // 0 = any level, 3 = 유력 or 확정, otherwise exactly that level (the song's best range).
  const shown = (g) => g.n >= span && (lv === 0 || (lv === 3 ? g.rank >= 3 : g.rank === lv));
  const list = document.getElementById('list');
  list.innerHTML = '';
  for (const v0 of DATA) {
    const v = { ...v0, songs: v0.songs.filter(shown) };
    const todo = v.songs.some((g) => !answers[songKey(v, g)]);
    if (f === 'todo' && !todo) continue;
    if (f === 'found' && !v.songs.length) continue;
    if (f === 'none' && v0.songs.length) continue;
    if (f === 'all' && !v.songs.length && v0.songs.length) continue;  // its songs are all filtered out
    const el = document.createElement('section');
    el.className = 'video';
    const bars = v.songs.flatMap((g) => g.ranges).map((r) => {
      const a = r.t / v.dur * 100, w = (r.t_last + 30 - r.t) / v.dur * 100;
      return `<span style="left:${a}%;width:${Math.min(w, 100 - a)}%;background:${color[r.rank]}" title="${esc(r.label)}"></span>`;
    }).join('');
    el.innerHTML = `
      <div class="vhead">
        <div class="vtitle">${esc(v.title || v.id)}</div>
        <div class="vmeta">${fmt(v.dur)} · <a href="https://www.youtube.com/watch?v=${v.id}" target="_blank" rel="noopener">YouTube</a></div>
      </div>
      <div class="timeline">${bars}</div>
      <div class="segs"></div>
      <textarea placeholder="메모 (예: 놓친 곡, 실제 곡 이름, 구간)" data-note="${v.id}">${esc(answers['note:' + v.id] || '')}</textarea>`;
    const box = el.querySelector('.segs');
    if (!v0.songs.length) box.innerHTML = '<div class="none">찾은 곡 없음</div>';
    for (const g of v.songs) {
      const k = songKey(v, g);
      const file = g.path.split('/').pop(), folder = g.path.includes('/') ? g.path.slice(0, g.path.lastIndexOf('/')) : '';
      const ranges = g.ranges.map((r) => {
        const extra = r.method === 'rhythm' ? `ratio ${r.ratio || '-'}` : `점수 ${r.best_score}`;
        const speed = Math.abs(r.speed - 1) > 0.004 ? ` · 속도 ×${r.speed}` : '';
        const other = r.path !== g.path ? ` · ${esc(r.path.split('/').pop())}` : '';
        return `<div class="range">
          <a href="https://www.youtube.com/watch?v=${v.id}&t=${Math.round(r.t)}s" target="_blank" rel="noopener">${fmt(r.t)}–${fmt(r.t_last + 30)}</a>
          <button class="play" data-src="clips/${encodeURIComponent(v.id)}_${r.t}.mp3">▶ 영상</button>
          <button class="play" data-src="clips/${encodeURIComponent(v.id)}_${r.t}_lib.mp3">▶ 라이브러리</button>
          <span class="small"><span style="color:${color[r.rank]}">■</span> ${esc(r.label)} · 곡 ${fmt(r.pos)} · ${extra}${speed}${other}</span>
        </div>`;
      }).join('');
      const seg = document.createElement('div');
      seg.className = 'seg';
      seg.innerHTML = `
        <div class="row"><span class="badge" style="background:${color[g.rank]}">${esc(g.label)}</span>
          <span class="track">${esc(file)}</span></div>
        <div class="folder">${esc(folder)}</div>
        ${g.also.length ? `<div class="small">같은 음원: ${g.also.map((p) => esc(p.split('/').pop())).join(', ')}</div>` : ''}
        <div class="small">들린 곳 ${g.ranges.length}군데 · 잡힌 구간 ${g.n}개</div>
        ${ranges}
        <div class="row ans">
          <button data-v="ok" data-k="${esc(k)}" aria-pressed="${answers[k] === 'ok'}">맞음</button>
          <button data-v="bad" data-k="${esc(k)}" aria-pressed="${answers[k] === 'bad'}">틀림</button>
          <button data-v="unk" data-k="${esc(k)}" aria-pressed="${answers[k] === 'unk'}">모르겠음</button>
        </div>`;
      box.appendChild(seg);
    }
    list.appendChild(el);
  }
  stats();
}

function stats() {
  const all = DATA.flatMap((v) => v.songs.map((g) => answers[songKey(v, g)]));
  const c = (x) => all.filter((a) => a === x).length;
  const done = all.filter(Boolean).length;
  document.getElementById('stats').textContent =
    `영상 ${DATA.length}개 · 곡을 찾은 영상 ${DATA.filter((v) => v.songs.length).length}개 · 찾은 곡 ${all.length}개 · ` +
    `검증 ${done}/${all.length} (맞음 ${c('ok')}, 틀림 ${c('bad')}, 모르겠음 ${c('unk')})`;
}

const player = document.getElementById('player');
let stopTimer = null, current = null;
function stop() { player.pause(); clearTimeout(stopTimer); if (current) current.classList.remove('on'); current = null; }
function fail(b, why) { b.textContent = `재생 실패 (${why})`; b.classList.remove('on'); }
document.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.classList.contains('play')) {
    if (current === b) { stop(); return; }
    stop();
    current = b; b.classList.add('on');
    // Each side is a short clip played from its start (play() loads it; no seeking).
    player.src = b.dataset.src;
    player.play().catch((err) => { if (current === b) fail(b, err.name); });
  } else if (b.dataset.k) {
    const k = b.dataset.k;
    answers[k] = answers[k] === b.dataset.v ? undefined : b.dataset.v;
    if (!answers[k]) delete answers[k];
    save();
    b.parentElement.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(answers[k] === x.dataset.v)));
    stats();
  }
});
player.addEventListener('ended', stop);
player.addEventListener('error', () => { if (current) fail(current, `파일을 열 수 없음, code ${player.error?.code}`); current = null; });
document.addEventListener('input', (e) => {
  const id = e.target.dataset.note;
  if (!id) return;
  if (e.target.value) answers['note:' + id] = e.target.value; else delete answers['note:' + id];
  save();
});
for (const id of ['filter', 'level', 'span']) document.getElementById(id).addEventListener('change', render);
document.getElementById('export').addEventListener('click', () => {
  const out = { exported: new Date().toISOString(), songs: [], notes: {} };
  for (const v of DATA) {
    for (const g of v.songs) out.songs.push({ video: v.id, path: g.path, also: g.also, label: g.label, windows: g.n,
      answer: answers[songKey(v, g)] || null,
      ranges: g.ranges.map((r) => ({ t: r.t, t_end: r.t_last + 30, path: r.path, pos: r.pos, label: r.label })) });
    if (answers['note:' + v.id]) out.notes[v.id] = answers['note:' + v.id];
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' }));
  a.download = 'battle100_review.json';
  a.click();
});
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
