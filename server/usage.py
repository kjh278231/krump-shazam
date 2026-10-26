"""Usage dashboard of the deployed server, for this PC only (docs/lambda-deployment.md, "사용량 대시보드").

    python server/usage.py            fetch from AWS, write build/usage/index.html and open it
    python server/usage.py --no-open  same, without opening the browser
    python server/usage.py --serve    local auto-refresh helper (installed by usage-autostart.ps1)

Only reads, with this PC's AWS credentials (the same as aws_setup.py):
- S3 recordings/: one JSON (+ WAV) per finished search of a user who agreed to share. The extension
  can't be used without agreeing, so that is every finished search; cancelled ones leave nothing.
  The record has no verdict, so it is derived from method and scores as server/app.py decides it.
- S3 videos/: the per-video records (docs/video-records.md).
- CloudWatch: the function's invocations, errors, throttles and duration by hour, and the warm-up
  pings (AWS/Scheduler), so the requests of users are the invocations minus the pings.
- CloudWatch Logs: cold starts (REPORT lines with an Init Duration, billed like the request).

There is no user id anywhere, so the number of users is not known (the Web Store dashboard has it).
The page keeps a local snapshot and can refresh through a loopback-only helper. It holds the tab titles
and URLs of users' searches: it stays in build/ (not in git) and is not to be published.
JSON already downloaded is kept in build/usage/cache.json and fetched again only when its ETag changes.
"""
from __future__ import annotations

import argparse
import json
import logging
import secrets
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import boto3

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "build" / "usage"
# pythonw (the login task) has no console; grouping imports common, which configures stdout.
if sys.stdout is None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sys.stdout = (OUT_DIR / "service.log").open("a", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout
sys.path.insert(0, str(ROOT / "poc"))
from grouping import CHECK_SCORE, CONFIRM_SCORE, RIVAL_RATIO  # noqa: E402

REGION = "ap-northeast-2"
NAME = "krump-finder"
CACHE = OUT_DIR / "cache.json"
DATA_FILE = OUT_DIR / "data.json"
SERVICE_FILE = OUT_DIR / "service.json"
TEMPLATE = Path(__file__).resolve().parent / "usage.html"
KST = timezone(timedelta(hours=9))
METRIC_DAYS = 90  # Lambda metrics and cold starts shown at most this far back
WAV_BYTES_PER_S = 16000 * 2  # the stored WAV is 16 kHz mono 16-bit (app.py PANAKO_SR)
# Seoul x86 prices (docs/lambda-deployment.md), before the free tier.
GB_SECOND_USD = 0.0000166667
REQUEST_USD = 0.20 / 1_000_000

def connect_aws() -> None:
    # Keep the helper available even when AWS credentials or the network are unavailable.
    global s3, cw, logs, lam, BUCKET
    s3 = boto3.client("s3", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    BUCKET = f"{NAME}-previews-{boto3.client('sts').get_caller_identity()['Account']}"


def listing(prefix: str) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        out += page.get("Contents", [])
    return out


def documents(objects: list[dict], cache: dict) -> dict[str, dict]:
    """The JSON objects by key; only new or changed ones (feedback rewrites a record) are downloaded."""
    want = [o for o in objects if o["Key"].endswith(".json")
            and cache.get(o["Key"], {}).get("etag") != o["ETag"]]

    def get(o: dict) -> tuple[str, dict]:
        body = s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()
        return o["Key"], {"etag": o["ETag"], "data": json.loads(body)}

    with ThreadPoolExecutor(16) as pool:
        cache.update(pool.map(get, want))
    keys = {o["Key"] for o in objects}
    return {k: v["data"] for k, v in cache.items() if k in keys}


def verdict(rec: dict) -> str:
    """confirmed / check / fail, as identify_audio decided it (grouping.verdict on Panako's songs)."""
    method, score, second = rec.get("method", ""), float(rec.get("score") or 0), float(rec.get("second") or 0)
    if method in ("pair", "rhythm+pair"):
        return "confirmed"
    if method == "rhythm":
        return "check"
    if not rec.get("match") or score < CHECK_SCORE:
        return "fail"
    if second >= RIVAL_RATIO * score or score < CONFIRM_SCORE:
        return "check"
    return "confirmed"


def site(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.").removeprefix("m.")
    if host in ("youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"):
        return "YouTube"
    if host == "instagram.com":
        return "Instagram"
    return host or "(주소 없음)"


def searches(objects: list[dict], docs: dict[str, dict]) -> list[dict]:
    wav = {o["Key"].removesuffix(".wav"): o["Size"] for o in objects if o["Key"].endswith(".wav")}
    out = []
    for key, rec in docs.items():
        stamp = rec.get("time") or key.rsplit("/", 1)[-1].removesuffix(".json")
        # Lambda runs in UTC, so the id's clock is UTC.
        at = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).astimezone(KST)
        size = wav.get(key.removesuffix(".json"))
        v = verdict(rec)
        out.append({
            "id": stamp, "at": at.strftime("%Y-%m-%d %H:%M"), "day": at.strftime("%Y-%m-%d"),
            "verdict": v, "method": rec.get("method", "") if v != "fail" else "",
            "track": rec.get("match", "") if v != "fail" else "",
            "feedback": rec.get("feedback", ""), "seconds": round((size - 44) / WAV_BYTES_PER_S, 1) if size else None,
            "site": site(rec.get("page_url", "")), "title": rec.get("page_title", ""), "url": rec.get("page_url", ""),
        })
    return sorted(out, key=lambda s: s["id"])


def video_events(docs: dict[str, dict]) -> list[dict]:
    return sorted(({"day": e.get("date", ""), "video": e.get("video", ""), "verdict": e.get("verdict", ""),
                    "feedback": e.get("feedback") or ""} for e in docs.values()), key=lambda e: e["day"])


def metric_hours(start: datetime, end: datetime) -> dict[str, dict[datetime, float]]:
    fn = [{"Name": "FunctionName", "Value": NAME}]
    queries = {
        "invocations": ("AWS/Lambda", "Invocations", fn),
        "errors": ("AWS/Lambda", "Errors", fn),
        "throttles": ("AWS/Lambda", "Throttles", fn),
        "duration_ms": ("AWS/Lambda", "Duration", fn),
        "warm": ("AWS/Scheduler", "InvocationAttemptCount", [{"Name": "ScheduleGroup", "Value": NAME}]),
    }
    out: dict[str, dict[datetime, float]] = {k: {} for k in queries}
    kw = {"MetricDataQueries": [{"Id": k, "ReturnData": True, "MetricStat": {
              "Metric": {"Namespace": ns, "MetricName": m, "Dimensions": dims}, "Period": 3600, "Stat": "Sum"}}
              for k, (ns, m, dims) in queries.items()],
          "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
    while True:
        r = cw.get_metric_data(**kw)
        for res in r["MetricDataResults"]:
            out[res["Id"]].update(zip(res["Timestamps"], res["Values"]))
        if "NextToken" not in r:
            return out
        kw["NextToken"] = r["NextToken"]


def cold_starts(start: datetime, cache: dict) -> dict[str, list[float]]:
    """{request id: [ms timestamp, init ms]} of the REPORT lines with an Init Duration, fetched from
    where the last run stopped (an hour back, for lines that arrived late)."""
    seen = cache.setdefault("events", {})
    since = max(int(start.timestamp() * 1000), cache.get("until", 0) - 3_600_000)
    now = int(time.time() * 1000)
    pages = logs.get_paginator("filter_log_events").paginate(
        logGroupName=f"/aws/lambda/{NAME}", startTime=since, filterPattern='"Init Duration"')
    for page in pages:
        for e in page["events"]:
            fields = dict(f.split(": ", 1) for f in e["message"].strip().split("\t") if ": " in f)
            if "REPORT RequestId" in fields and "Init Duration" in fields:  # not INIT_REPORT (failed init)
                seen[fields["REPORT RequestId"]] = [e["timestamp"], float(fields["Init Duration"].split()[0])]
    cache["until"] = now
    return seen


def server_days(start: datetime, end: datetime, cold: dict[str, list[float]], memory_gb: float) -> list[dict]:
    days: dict[str, dict] = {}

    def day(at: datetime) -> dict:
        d = at.astimezone(KST).strftime("%Y-%m-%d")
        return days.setdefault(d, {"day": d, "invocations": 0, "warm": 0, "errors": 0, "throttles": 0,
                                   "duration_ms": 0.0, "cold": 0, "init_ms": 0.0})

    for metric, hours in metric_hours(start, end).items():
        for at, v in hours.items():
            day(at)[metric] += v
    for ts, init_ms in cold.values():
        at = datetime.fromtimestamp(ts / 1000, timezone.utc)
        if at >= start:
            d = day(at)
            d["cold"] += 1
            d["init_ms"] += init_ms
    for d in days.values():
        d["requests"] = max(0, round(d["invocations"] - d["warm"]))
        gb_s = (d["duration_ms"] + d["init_ms"]) / 1000 * memory_gb
        d["cost"] = round(gb_s * GB_SECOND_USD + d["invocations"] * REQUEST_USD, 4)
        for k in ("invocations", "warm", "errors", "throttles"):
            d[k] = round(d[k])
        d["duration_ms"], d["init_ms"] = round(d["duration_ms"]), round(d["init_ms"])
    return sorted(days.values(), key=lambda d: d["day"])


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def service_url(config: dict) -> str:
    return f"http://127.0.0.1:{config['port']}/{config['token']}/"


def write_page(data: dict) -> None:
    config = json.loads(SERVICE_FILE.read_text(encoding="utf-8")) if SERVICE_FILE.exists() else None
    # A tab title must not be able to close the inline script.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("const LIVE_URL = __LIVE_URL__;",
                        f"const LIVE_URL = {json.dumps(service_url(config) if config else None)};")
    page = page.replace("let DATA = __DATA__;", f"let DATA = {blob};")
    atomic_write(OUT_DIR / "index.html", page)


def collect_data() -> dict:

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    connect_aws()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    t0 = time.time()
    rec_objects = listing("recordings/")
    found = searches(rec_objects, documents(rec_objects, cache.setdefault("objects", {})))
    vid_objects = listing("videos/")
    events = video_events(documents(vid_objects, cache["objects"]))

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = (datetime.now(KST) - timedelta(days=METRIC_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    memory_gb = lam.get_function_configuration(FunctionName=NAME)["MemorySize"] / 1024
    server = server_days(start, end, cold_starts(start, cache.setdefault("cold", {})), memory_gb)

    data = {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "today": datetime.now(KST).strftime("%Y-%m-%d"),
        "metricDays": METRIC_DAYS, "memoryGb": memory_gb,
        "searches": found, "server": server, "videoEvents": events,
    }
    write_page(data)
    atomic_write(DATA_FILE, json.dumps(data, ensure_ascii=False))
    atomic_write(CACHE, json.dumps(cache, ensure_ascii=False))
    print(f"{len(found)} searches, {len(events)} video records, {len(server)} days of server metrics "
          f"({time.time() - t0:.1f} s) -> {OUT_DIR / 'index.html'}", flush=True)
    return data


def prepare_auto_refresh(port: int = 8766) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if SERVICE_FILE.exists():
        config = json.loads(SERVICE_FILE.read_text(encoding="utf-8"))
    else:
        config = {"port": port, "token": secrets.token_urlsafe(32)}
        atomic_write(SERVICE_FILE, json.dumps(config))
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    elif (OUT_DIR / "index.html").exists():
        # Upgrade the original single-file snapshot without another AWS download.
        import re
        old = (OUT_DIR / "index.html").read_text(encoding="utf-8")
        data = json.loads(re.search(r"(?:const|let) DATA = (.*);", old)[1])
    else:
        data = {"generated": "미수집", "today": datetime.now(KST).strftime("%Y-%m-%d"),
                "metricDays": METRIC_DAYS, "memoryGb": 0, "searches": [], "server": [], "videoEvents": []}
    write_page(data)
    atomic_write(DATA_FILE, json.dumps(data, ensure_ascii=False))
    return config


def make_server(config: dict) -> ThreadingHTTPServer:
    origin = f"http://127.0.0.1:{config['port']}"
    prefix = f"/{config['token']}/"
    refresh_lock = threading.Lock()
    revision = 0
    latest = None

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the tab closed while AWS was being read

        def allowed_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{config['port']}"

        def do_GET(self) -> None:
            if not self.allowed_host() or self.path != prefix:
                self.reply(404, b"Not found", "text/plain")
                return
            self.reply(200, (OUT_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            nonlocal revision, latest
            if (not self.allowed_host() or self.path != prefix + "refresh"
                    or self.headers.get("Origin") != origin
                    or self.headers.get("Content-Type") != "application/json"):
                self.reply(403, b"Forbidden", "text/plain")
                return
            started = revision
            try:
                with refresh_lock:
                    # Tabs opened together share the refresh already in progress.
                    if revision == started:
                        latest = collect_data()
                        revision += 1
                    body = json.dumps(latest, ensure_ascii=False).encode("utf-8")
                self.reply(200, body, "application/json; charset=utf-8")
            except Exception:
                logging.exception("Usage refresh failed")
                self.reply(502, b'{"error":"refresh_failed"}', "application/json")

        def log_message(self, fmt: str, *args) -> None:
            logging.info(fmt, *args)

    server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
    config = {**config, "port": server.server_port}
    origin = f"http://127.0.0.1:{server.server_port}"
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-open", action="store_true", help="don't open the page in the browser")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true", help="run the local auto-refresh helper")
    mode.add_argument("--prepare-auto-refresh", action="store_true", help="connect the existing HTML to the helper")
    args = ap.parse_args()
    if args.prepare_auto_refresh:
        prepare_auto_refresh()
        return
    if args.serve:
        # Preparation is done once by the installer, before the helper can handle requests.
        config = json.loads(SERVICE_FILE.read_text(encoding="utf-8"))
        logging.basicConfig(filename=OUT_DIR / "service.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        with make_server(config) as server:
            server.serve_forever()
        return
    collect_data()
    if not args.no_open:
        webbrowser.open((OUT_DIR / "index.html").as_uri())


if __name__ == "__main__":
    main()
