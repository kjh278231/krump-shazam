"""Local auto-refresh: private routes, fresh data, and preservation on AWS failure."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import usage


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(usage, name, self.root / filename) for name, filename in (
            ("OUT_DIR", "."), ("CACHE", "cache.json"), ("DATA_FILE", "data.json"),
            ("SERVICE_FILE", "service.json"))]
        for p in self.patches:
            p.start()
        self.data = {"generated": "2026-10-26 12:00", "today": "2026-10-26", "metricDays": 90,
                     "memoryGb": 10, "searches": [], "server": [], "videoEvents": []}
        usage.DATA_FILE.write_text(json.dumps(self.data), encoding="utf-8")
        self.config = usage.prepare_auto_refresh(port=0)
        self.server = usage.make_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.path = f"/{self.config['token']}/"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        status, body, headers = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return status, body, headers

    def test_only_token_route_exposes_snapshot(self):
        self.assertEqual(self.request("GET", "/")[0], 404)
        self.assertEqual(self.request("GET", self.path + "../cache.json")[0], 404)
        self.assertEqual(self.request("GET", self.path, {"Host": "evil.example"})[0], 404)
        status, body, headers = self.request("GET", self.path)
        self.assertEqual(status, 200)
        self.assertIn(b"2026-10-26 12:00", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_open_refreshes_again_and_other_sites_cannot_trigger_aws(self):
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        with patch.object(usage, "collect_data", return_value=self.data) as collect:
            self.assertEqual(self.request("POST", self.path + "refresh")[0], 403)
            self.assertEqual(self.request("POST", self.path + "refresh", {
                **headers, "Origin": "https://evil.example"})[0], 403)
            collect.assert_not_called()
            for _ in range(2):
                status, body, _ = self.request("POST", self.path + "refresh", headers)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), self.data)
            self.assertEqual(collect.call_count, 2)

    def test_failed_refresh_keeps_last_good_page_and_allows_retry(self):
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        before = (self.root / "index.html").read_bytes()
        with patch.object(usage, "connect_aws"), patch.object(usage, "listing", side_effect=OSError("offline")):
            with self.assertLogs(level="ERROR"):
                self.assertEqual(self.request("POST", self.path + "refresh", headers)[0], 502)
        self.assertEqual((self.root / "index.html").read_bytes(), before)
        self.assertEqual(json.loads(usage.DATA_FILE.read_text(encoding="utf-8")), self.data)
        with patch.object(usage, "collect_data", return_value=self.data):
            self.assertEqual(self.request("POST", self.path + "refresh", headers)[0], 200)

    def test_titles_cannot_close_script_or_replace_configuration(self):
        self.data["searches"] = [{"title": "</script><script>alert(1)</script> __LIVE_URL__"}]
        usage.write_page(self.data)
        page = (self.root / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("</script><script>alert", page)
        import re
        restored = json.loads(re.search(r"let DATA = (.*);", page)[1])
        self.assertEqual(restored, self.data)


if __name__ == "__main__":
    unittest.main()
