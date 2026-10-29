"""Checks against a running server: the old extensions' requests still get the same answers, and the
per-video records follow their contract (docs/video-records.md §7).

  python tests/check_api.py --baseline FILE          before a server change: what the 1.0.0/1.1.0
                                                      requests get (status, keys, verdicts) -> FILE
  python tests/check_api.py --compare FILE --video-dir DIR
                                                      after it: the same answers, no `video` field,
                                                      no event written under DIR
  python tests/check_api.py --contract --video-dir DIR
                                                      the new /identify, GET /video and /feedback
                                                      contract (local server only: it stores events)
  python tests/check_api.py --server URL --key-file FILE --no-store
                                                      deployed smoke test: never sends share=1, so
                                                      nothing is stored
  python tests/check_api.py --errors [--key-file FILE]
                                                      user-facing errors carry a `code` and keep the
                                                      old `detail` (1.3.0 extension); no clips needed,
                                                      nothing is stored. With a key file the server's
                                                      key is required and a wrong one is tried too

Default server http://127.0.0.1:8765 (no API key). Run the local server with KF_TMP and KF_VIDEO_DIR
pointing at a scratch folder so recordings/ is not touched:
  KF_TMP=<scratch>/kf KF_VIDEO_DIR=<scratch>/videos python server/app.py
The clips are 30 s windows of battle-100 videos (realtest/battle100/audio/, not in git) whose
verdicts are known (realtest/battle100/pair_eval.jsonl, windows.jsonl).
"""
from __future__ import annotations

import argparse
import json
import random
import string
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poc"))
from common import FFMPEG  # noqa: E402

AUDIO = ROOT / "realtest" / "battle100" / "audio"
CLIPS = {  # name: (video, start s); 30 s each
    "confirmed": ("cwxXc58b-Co", 0),
    "check": ("H3CBS2FKfhc", 30),
    "fail": ("k3UA20TIamw", 195),
}
LENGTH = 30
TITLE = "check_api"  # recordings saved by these checks are easy to find and delete


class Server:
    def __init__(self, url: str, key: str):
        self.url, self.key = url.rstrip("/"), key

    def call(self, method: str, path: str, params: dict | None = None, body: bytes | None = None,
             json_body: dict | None = None) -> tuple[int, dict | bytes, str]:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        headers = {"X-Api-Key": self.key} if self.key else {}
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path + q, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, data, ctype = r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            status, data, ctype = e.code, e.read(), e.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            data = json.loads(data)
        return status, data, ctype


def cut_clips(tmp: Path) -> dict[str, bytes]:
    out = {}
    for name, (vid, start) in CLIPS.items():
        src = next(AUDIO.glob(f"{vid}.*"), None)
        if src is None:
            sys.exit(f"missing {AUDIO / vid}.* (battle-100 audio is not in git)")
        wav = tmp / f"{name}.wav"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", str(start), "-t", str(LENGTH), "-i", str(src),
                        "-ac", "1", "-ar", "16000", str(wav)], check=True)
        out[name] = wav.read_bytes()
    return out


def shape(status: int, data, ctype: str) -> dict:
    """What an old extension depends on: status, top-level keys, the top song's keys, the verdict."""
    s = {"status": status, "type": ctype.split(";")[0]}
    if isinstance(data, dict):
        s["keys"] = sorted(data)
        if data.get("songs"):
            s["song_keys"] = sorted(data["songs"][0])
            s["top"] = data["songs"][0]["path"]
        for k in ("verdict", "method", "final", "ok"):
            if k in data:
                s[k] = data[k]
        if "albums" in data:
            s["album_keys"] = sorted(data["albums"][0])
    return s


def old_requests(srv: Server, clips: dict[str, bytes], store: bool) -> dict[str, dict]:
    """The requests 1.0.0 and 1.1.0 send (offscreen.js, popup.js), with their answers' shapes."""
    out = {}
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=100s"
    page = {"title": TITLE, "url": url, "share": 1} if store else {}
    top = None
    for name, wav in clips.items():
        st, data, ct = srv.call("POST", "/identify", {**page, "partial": 1}, wav)
        out[f"identify partial {name}"] = shape(st, data, ct)
        st, data, ct = srv.call("POST", "/identify", page, wav)
        out[f"identify {name}"] = shape(st, data, ct)
        if name == "confirmed" and isinstance(data, dict):
            top = data
    if store and top and top.get("id"):
        st, data, ct = srv.call("POST", "/feedback", json_body={"id": top["id"], "correct": True,
                                                                "path": top["songs"][0]["path"]})
        out["feedback"] = shape(st, data, ct)
    st, data, ct = srv.call("POST", "/feedback", json_body={"id": "no-such-id", "correct": True, "path": ""})
    out["feedback unknown id"] = shape(st, data, ct)
    if top and top.get("songs"):
        st, data, ct = srv.call("GET", "/preview", {"path": top["songs"][0]["path"], "start": "12.0"})
        out["preview"] = {"status": st, "type": ct, "bytes": len(data) > 10000}
    st, data, ct = srv.call("GET", "/albums")
    out["albums"] = shape(st, data, ct)
    st, data, ct = srv.call("GET", "/health")
    out["health"] = shape(st, data, ct)
    return out


def events_under(d: Path | None) -> list[Path]:
    return sorted(d.rglob("*.json")) if d and d.exists() else []


class Checker:
    def __init__(self):
        self.failed = 0

    def ok(self, cond: bool, what: str) -> None:
        print(("  ok    " if cond else "  FAIL  ") + what)
        self.failed += not cond


def fresh_key(platform: str = "yt") -> str:
    return f"{platform}:T" + "".join(random.choices(string.ascii_letters + string.digits, k=10))


def contract(srv: Server, clips: dict[str, bytes], video_dir: Path) -> int:
    c = Checker()
    page = {"title": TITLE, "url": "https://www.youtube.com/watch?v=x", "share": 1}

    def event_file(key, rec_id):
        platform, vid = key.split(":")
        return video_dir / platform / vid / f"{rec_id}.json"

    def read(f):
        return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}

    print("confirmed result")
    k1 = fresh_key()
    st, r, _ = srv.call("POST", "/identify", {**page, "vkey": k1}, clips["confirmed"])
    c.ok(st == 200 and r["verdict"] == "confirmed", f"verdict confirmed ({st}, {r.get('verdict') if st == 200 else r})")
    c.ok(r.get("video") == {"key": k1}, f"video field ({r.get('video')})")
    f = event_file(k1, r["id"])
    e = read(f)
    top = r["songs"][0]["path"]
    c.ok(f.exists() and e.get("song", {}).get("path") == top and e.get("verdict") == "confirmed",
         f"event written ({f.relative_to(video_dir) if f.exists() else 'missing'})")
    text = json.dumps(e)
    c.ok(TITLE not in text and "youtube.com" not in text, "event holds no title or URL")
    c.ok(not {"range", "anchor", "video_duration"} & set(e), "event holds no place in the video")
    st, g, _ = srv.call("GET", "/video", {"key": k1})
    songs = g.get("songs", []) if st == 200 else []
    c.ok(st == 200 and g.get("key") == k1 and len(songs) == 1 and songs[0]["status"] == "confirmed"
         and songs[0]["song"]["path"] == top, f"GET /video: the song ({st})")
    c.ok(bool(songs) and {"path", "file", "folder", "match_start", "album"} <= set(songs[0]["song"]),
         "song has file, folder, match_start, album")
    c.ok(set(g) == {"key", "songs"}, f"GET /video keys {sorted(g)}")
    st, fb, _ = srv.call("POST", "/feedback", json_body={"id": r["id"], "correct": False, "path": top, "video": k1})
    c.ok(st == 200 and read(f).get("feedback") == "wrong", "틀림 recorded on the event")
    st, g, _ = srv.call("GET", "/video", {"key": k1})
    c.ok(st == 200 and g["songs"] == [], "after 틀림 nothing is shown")

    print("partial check that confirms (early stop) also records")
    k1b = fresh_key()
    st, r, _ = srv.call("POST", "/identify", {**page, "vkey": k1b, "partial": 1}, clips["confirmed"][:44 + 16000 * 2 * 11])
    if st == 200 and r.get("final"):
        c.ok(r.get("video") == {"key": k1b} and event_file(k1b, r["id"]).exists(), "final partial: video field and event")
    else:
        c.ok(st == 200 and "video" not in r and not events_under(video_dir / "yt" / k1b[3:]),
             f"non-final partial ({r.get('verdict') if st == 200 else st}): no video field, no event")

    print("unsure result: hidden until 맞음")
    k2 = fresh_key()
    st, r, _ = srv.call("POST", "/identify", {**page, "vkey": k2}, clips["check"])
    c.ok(st == 200 and r["verdict"] == "check" and event_file(k2, r["id"]).exists(),
         f"verdict check, event ({r.get('verdict') if st == 200 else st})")
    st, g, _ = srv.call("GET", "/video", {"key": k2})
    c.ok(st == 200 and g["songs"] == [], "unsure alone is not shown")
    srv.call("POST", "/feedback", json_body={"id": r["id"], "correct": True, "path": r["songs"][0]["path"], "video": k2})
    st, g, _ = srv.call("GET", "/video", {"key": k2})
    c.ok(st == 200 and len(g["songs"]) == 1 and g["songs"][0]["status"] == "verified", "after 맞음: verified")

    print("Instagram, two searches of the same song")
    k3 = fresh_key("ig")
    ids = []
    for _ in range(2):
        st, r, _ = srv.call("POST", "/identify", {**page, "vkey": k3}, clips["confirmed"])
        ids.append(r.get("id"))
    st, g, _ = srv.call("GET", "/video", {"key": k3})
    c.ok(st == 200 and len(g["songs"]) == 1 and all(event_file(k3, i).exists() for i in ids),
         "two events, one song")

    print("not found")
    k6 = fresh_key()
    st, r, _ = srv.call("POST", "/identify", {**page, "vkey": k6}, clips["fail"])
    c.ok(st == 200 and r["verdict"] == "fail" and r.get("video") == {"key": k6} and not events_under(video_dir / "yt" / k6[3:]),
         "fail: video field, no event")

    print("malformed and unshared")
    st, g, _ = srv.call("GET", "/video", {"key": "yt:bad"})
    c.ok(st == 400, f"GET /video bad key: 400 ({st})")
    st, g, _ = srv.call("GET", "/video", {"key": fresh_key()})
    c.ok(st == 200 and g == {"key": g.get("key"), "songs": []}, "GET /video unknown video: empty")
    st, r, _ = srv.call("POST", "/identify", {**page, "vkey": "yt:bad"}, clips["confirmed"])
    c.ok(st == 200 and "video" not in r, "bad vkey: answered as before, no video field")
    k8 = fresh_key()
    st, r, _ = srv.call("POST", "/identify", {"vkey": k8}, clips["confirmed"])
    c.ok(st == 200 and r["id"] is None and not events_under(video_dir / "yt" / k8[3:]), "without share: no event")
    st, fb, _ = srv.call("POST", "/feedback", json_body={"id": "no-such-id", "correct": True, "path": "", "video": k1})
    c.ok(st == 404, f"feedback unknown id with video: 404 ({st})")
    print("failed" if c.failed else "all passed", f"({c.failed} failed)")
    return c.failed


def smoke(srv: Server, clips: dict[str, bytes]) -> int:
    c = Checker()
    st, data, _ = srv.call("GET", "/health")
    c.ok(st == 200 and data == {"ok": True}, f"/health ({st})")
    st, r, _ = srv.call("POST", "/identify", {}, clips["confirmed"])
    c.ok(st == 200 and r["verdict"] == "confirmed" and r["id"] is None and "video" not in r,
         f"identify as before ({r.get('verdict') if st == 200 else st})")
    k = fresh_key()
    st, r, _ = srv.call("POST", "/identify", {"vkey": k}, clips["confirmed"])
    c.ok(st == 200 and r["id"] is None and r.get("video") == {"key": k}, "identify with a video key (not stored)")
    st, g, _ = srv.call("GET", "/video", {"key": k})
    c.ok(st == 200 and g == {"key": k, "songs": []}, f"GET /video: nothing stored ({st})")
    st, g, _ = srv.call("GET", "/video", {"key": "yt:bad"})
    c.ok(st == 400, f"GET /video bad key: 400 ({st})")
    print("failed" if c.failed else "all passed", f"({c.failed} failed)")
    return c.failed


def silence(tmp: Path, seconds: int = 5) -> bytes:
    """A recording of a muted tab: Opus in WebM, like the extension's MediaRecorder."""
    out = tmp / "silence.webm"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-t", str(seconds), "-c:a", "libopus", str(out)], check=True)
    return out.read_bytes()


def errors(srv: Server) -> int:
    """The errors the extension shows: the 1.3.0 extension words them from `code`, 1.0.0/1.2.0 show
    `detail` as it is, so `detail` must not change."""
    c = Checker()

    def check(st, data, status, code, detail, what, prefix=False):
        body = data if isinstance(data, dict) else {}
        got_detail = body.get("detail", "")
        same = got_detail.startswith(detail) if prefix else got_detail == detail
        c.ok(st == status and body.get("code") == code and same,
             f"{what}: {status} {code} ({st} {body.get('code')} {got_detail[:60]!r})")

    st, data, _ = srv.call("POST", "/identify", {}, b"")
    check(st, data, 400, "empty", "recording is empty (is the tab playing audio?)", "empty recording")
    st, data, _ = srv.call("POST", "/identify", {}, b"x" * 5000)
    check(st, data, 400, "undecodable", "ffmpeg could not decode the recording", "undecodable recording",
          prefix=True)  # ffmpeg's own message follows
    with tempfile.TemporaryDirectory() as d:
        quiet = silence(Path(d))
    st, data, _ = srv.call("POST", "/identify", {}, quiet)
    check(st, data, 400, "silent", "소리가 거의 녹음되지 않았습니다. 탭/영상이 음소거 상태이거나 멈춰 있지 않은지 확인하세요.",
          "silent recording")
    if srv.key:
        st, data, _ = Server(srv.url, srv.key + "-wrong").call("GET", "/albums")
        check(st, data, 401, "bad_key", "API 키가 맞지 않습니다.", "wrong API key")
    print("failed" if c.failed else "all passed", f"({c.failed} failed)")
    return c.failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8765")
    ap.add_argument("--key-file", type=Path, help="file holding the API key (build/api_key.txt)")
    ap.add_argument("--baseline", type=Path, help="write the old requests' answers here")
    ap.add_argument("--compare", type=Path, help="compare the old requests' answers with this baseline")
    ap.add_argument("--contract", action="store_true", help="check the per-video records contract")
    ap.add_argument("--no-store", action="store_true", help="deployed smoke test, never share=1")
    ap.add_argument("--errors", action="store_true", help="error codes and unchanged details")
    ap.add_argument("--video-dir", type=Path, help="the server's KF_VIDEO_DIR")
    a = ap.parse_args()
    srv = Server(a.server, a.key_file.read_text().strip() if a.key_file else "")
    clips = {}
    if a.baseline or a.compare or a.contract or a.no_store:
        with tempfile.TemporaryDirectory() as d:
            clips = cut_clips(Path(d))
    failed = 0
    if a.baseline or a.compare:
        before = events_under(a.video_dir)
        got = old_requests(srv, clips, store=True)
        if a.baseline:
            a.baseline.write_text(json.dumps(got, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {a.baseline} ({len(got)} requests)")
        else:
            want = json.loads(a.compare.read_text(encoding="utf-8"))
            c = Checker()
            for name in sorted(set(want) | set(got)):
                c.ok(want.get(name) == got.get(name),
                     name + ("" if want.get(name) == got.get(name) else f": {want.get(name)} -> {got.get(name)}"))
            c.ok(all("video" not in s.get("keys", []) for s in got.values()), "no video field for old requests")
            c.ok(events_under(a.video_dir) == before, "no event written for old requests")
            failed += c.failed
            print("failed" if c.failed else "all passed", f"({c.failed} failed)")
    if a.contract:
        if not a.video_dir:
            sys.exit("--contract needs --video-dir (the server's KF_VIDEO_DIR)")
        failed += contract(srv, clips, a.video_dir)
    if a.no_store:
        failed += smoke(srv, clips)
    if a.errors:
        failed += errors(srv)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
