"""Store screenshots and a layout check of every popup screen, from the real extension pages.

    python store/shots/make.py check            every popup scene and the library page in ko and en
                                                -> build/shots/check/
    python store/shots/make.py store --lang en  the store screenshots -> store/assets/en/

The pages run in headless Chrome against stub.js (a stand-in chrome.* API and server with canned
results; album covers are title tiles). popup.html and library.html here are written from
extension/ on every run. The library shot needs the album list: GET /albums of a running local
server (--server, --key-file), covers dropped, saved as albums.json here (not in git).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CHECK = ROOT / "build" / "shots" / "check"
SCENES = ["consent", "consent_changed", "idle", "error", "error_unknown", "records", "records_list",
          "recording", "searching", "found", "unsure", "single", "fail"]
# store/assets/<lang>/ file name -> composed shot number in compose.html, or the library page
STORE = {"screenshot-1-found.png": "1", "screenshot-2-unsure.png": "2", "screenshot-3-listen.png": "3",
         "screenshot-4-library.png": "library", "screenshot-5-retry.png": "5", "screenshot-6-records.png": "6"}
CHROME = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")


def harness_pages() -> None:
    """extension/popup.html and library.html with stub.js instead of config.js."""
    for name in ("popup.html", "library.html"):
        html = (ROOT / "extension" / name).read_text(encoding="utf-8")
        html = re.sub(r'(href|src)="(?!https?:)([\w./-]+)"', r'\1="../../extension/\2"', html)
        html = html.replace('<script src="../../extension/config.js"></script>', '<script src="stub.js"></script>')
        (HERE / name).write_text(html, encoding="utf-8")


def albums(server: str, key: str) -> None:
    req = urllib.request.Request(f"{server}/albums", headers={"X-Api-Key": key})
    data = json.load(urllib.request.urlopen(req, timeout=120))
    for a in data["albums"]:
        a["cover"] = a["art"] = None
    (HERE / "albums.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve() -> tuple[http.server.ThreadingHTTPServer, int]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def shoot(url: str, out: Path, size: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(CHROME), "--headless=new", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={size}", "--virtual-time-budget=6000", f"--screenshot={out}", url],
                   check=True, capture_output=True)
    print(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["check", "store"])
    ap.add_argument("--lang", default="en")
    ap.add_argument("--server", default="http://127.0.0.1:8765")
    ap.add_argument("--key-file", type=Path)
    a = ap.parse_args()
    harness_pages()
    srv, port = serve()
    base = f"http://127.0.0.1:{port}/store/shots"
    try:
        albums(a.server, a.key_file.read_text().strip() if a.key_file else "")
        if a.what == "check":
            for lang in ("ko", "en"):
                for scene in SCENES:
                    shoot(f"{base}/popup.html?lang={lang}&scene={scene}", CHECK / f"{lang}-{scene}.png", "380,900")
                shoot(f"{base}/library.html?lang={lang}", CHECK / f"{lang}-library.png", "1280,800")
        else:
            out = ROOT / "store" / "assets" / a.lang
            for name, shot in STORE.items():
                url = (f"{base}/library.html?lang={a.lang}" if shot == "library"
                       else f"{base}/compose.html?lang={a.lang}&shot={shot}")
                shoot(url, out / name, "1280,800")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
