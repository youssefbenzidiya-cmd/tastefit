import contextlib
import http.client
import io
import json
import os
import re
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer

os.environ.pop("QLOO_API_KEY", None)
import app  # noqa: E402


def sse_events(body):
    """[(event, data)] from a Server-Sent Events body."""
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        out.append((lines.get("event"), json.loads(lines["data"]) if "data" in lines else None))
    return out


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        for name, value in (("DEMO_DELAY_SCALE", 0), ("run_limiter", app.RateLimiter(1000, 600)),
                            ("demo_limiter", app.RateLimiter(1000, 600)), ("run_slots", threading.BoundedSemaphore(2))):
            p = mock.patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def request(self, method, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path)
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read().decode()
        finally:
            conn.close()

    def test_index(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("TasteFit", body)
        self.assertNotIn("{{CTA}}", body)

    def test_index_has_the_ci_smoke_marker(self):
        ci = (app.ROOT / ".gitlab-ci.yml").read_text()
        markers = set(re.findall(r'_URL/" \| grep "([^"]+)"', ci))
        self.assertTrue(markers)
        body = self.get("/")[1]
        for m in markers:
            self.assertIn(m, body)

    def test_static_assets_are_served_with_their_types(self):
        for path, (_, ctype) in app.STATIC.items():
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                conn.request("GET", path)
                r = conn.getresponse()
                body = r.read()
            finally:
                conn.close()
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, ctype), path)
            self.assertGreater(len(body), 0, path)
        self.assertEqual(self.get("/static/app.js")[0], 404)
        self.assertEqual(self.get("/../app.py")[0], 404)

    def test_page_csp_allows_self_hosted_font_and_script_only(self):
        csp = self.request("GET", "/")[1]["Content-Security-Policy"]
        self.assertIn("font-src 'self';", csp)
        self.assertIn("script-src 'self' 'nonce-", csp)
        self.assertNotIn("fonts.gstatic.com", csp)
        self.assertNotIn("unsafe-eval", csp)

    def test_page_has_no_unfilled_placeholders_and_absolute_og_image(self):
        body = self.get("/")[1]
        self.assertNotRegex(body, r"\{\{[A-Z]+\}\}")
        self.assertRegex(body, r'property="og:image" content="https://[^"]+/og\.png"')
        self.assertEqual(len(re.findall(r'<script nonce="[^"]+">', body)), 2)

    def test_footer_build_dev_when_commit_unset(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("RENDER_GIT_COMMIT", None)
            body = self.get("/")[1]
        self.assertIn("TasteFit build dev", body)
        self.assertNotIn("{{BUILD}}", body)

    def test_footer_build_uses_first_7_chars_of_commit(self):
        with mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": "0123456789abcdef"}):
            body = self.get("/")[1]
        self.assertIn("TasteFit build 0123456<", body)

    def test_footer_keeps_osm_attribution(self):
        self.assertIn("OpenStreetMap", self.get("/")[1])

    def test_evidence_is_linked_and_quotes_the_negative_result(self):
        self.assertIn('href="/evidence"', self.get("/")[1])
        code, body = self.get("/evidence")
        self.assertEqual(code, 200)
        self.assertIn("No measurable gain", body)
        self.assertIn("Heckman (1979)", body)
        self.assertIn('href="/ledger"', body)

    def test_health(self):
        self.assertEqual(self.get("/health"), (200, "ok"))

    def test_run_requires_inputs(self):
        self.assertEqual(self.get("/run?concept=x")[0], 400)

    def test_tenant_validates_inputs(self):
        for q in ("", "city=Chicago", "lat=41.9", "lat=x&lon=1", "lat=91&lon=0", "lat=41.9&lon=-87.6&rent=-5",
                  "lat=41.9&lon=-87.6&rent=abc", "lat=41.9&lon=-87.6&portfolio=nope",
                  "lat=41.9&lon=-87.6&portfolio=100&k=9"):
            self.assertEqual(self.get("/tenant?" + q)[0], 400, q)

    def test_tenant_without_key_reports_offline(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("QLOO_API_KEY", None)
            code, body = self.get("/tenant?lat=41.9&lon=-87.68")
        self.assertEqual(code, 200)
        self.assertIn('event: error\ndata: "offline"', body)

    def test_stream_pings_while_the_pipeline_is_blocked(self):
        def fake_run(*_a, **_k):
            time.sleep(0.25)
            yield "unit", {"area": "Sample"}
            yield "budget", {"qloo_calls": 0}

        with mock.patch.object(app, "SSE_PING_S", 0.05), \
             mock.patch.dict(os.environ, {"QLOO_API_KEY": "k"}), \
             mock.patch.object(app.tenant, "run", fake_run):
            code, body = self.get("/tenant?lat=41.9&lon=-87.68")
        self.assertEqual(code, 200)
        self.assertLess(body.index(": ping"), body.index("event: unit"))
        kinds = [event for event, _data in sse_events(body) if event]
        self.assertEqual(kinds, ["unit", "budget"])
        script = (app.ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (line.startsWith(':')) continue;", script)
        self.assertIn("if (!named) continue;", script)
        self.assertIn("data ? JSON.parse(data) : null", script)
        self.assertIn("if (HANDLERS[event]) HANDLERS[event](data)", script)

    def test_tenant_streams_pipeline_events(self):
        def fake_run(city, location, lat, lon, rent, portfolio_rents=None, portfolio_k=1):
            yield "unit", {"city": city, "location": location, "lat": lat, "lon": lon, "rent": rent,
                           "portfolio_rents": portfolio_rents, "k": portfolio_k}
            yield "budget", {"qloo_calls": 3}
        with mock.patch.dict(os.environ, {"QLOO_API_KEY": "k"}), mock.patch.object(app.tenant, "run", fake_run):
            code, body = self.get("/tenant?city=Paris&location=Le%20Marais&rent=4000")
        self.assertEqual(code, 200)
        self.assertIn('"city": "Paris", "location": "Le Marais", "lat": null, "lon": null, "rent": 4000.0', body)
        self.assertIn("event: budget", body)

    def test_head_on_tenant_not_allowed(self):
        self.assertEqual(self.request("HEAD", "/tenant")[0], 405)

    def test_run_without_key_reports_offline(self):
        code, body = self.get("/run?concept=wine%20bar&location=Silver%20Lake")
        self.assertEqual(code, 200)
        self.assertIn('event: error\ndata: "offline"', body)

    def test_index_security_headers_and_nonce(self):
        code, headers, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        csp = headers["Content-Security-Policy"]
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertIn(f'<script nonce="{nonce}">', body)
        self.assertNotIn("{{NONCE}}", body)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("https://tile.openstreetmap.org", csp)
        self.assertNotIn("unsafe-inline", csp.split("script-src")[1].split(";")[0])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("Referrer-Policy", headers)
        self.assertEqual(headers["Server"], "TasteFit")
        self.assertNotEqual(nonce, re.search(r"'nonce-([^']+)'", self.request("GET", "/")[1]["Content-Security-Policy"]).group(1))

    def test_cdn_scripts_are_pinned_with_sri(self):
        body = self.get("/")[1]
        scripts = re.findall(r"<script (?:defer )?src=\"(https://[^\"]+)\"([^>]*)>", body)
        self.assertEqual(len(scripts), 3)
        for src, attrs in scripts:
            self.assertRegex(src, r"@\d+\.\d+\.\d+/")
            self.assertIn('integrity="sha', attrs)
        self.assertEqual(re.findall(r"<script (?:defer )?src=\"(/[^\"]+)\"", body), ["/app.js"])
        self.assertIn("DOMPurify.sanitize(marked.parse", self.get("/app.js")[1])

    def test_copy_has_no_pricing_promise_or_unsourced_claim(self):
        body = self.get("/")[1]
        self.assertNotIn("Paid plans open after judging", body)
        self.assertNotIn("$3,500", body)
        self.assertNotIn("github.com", body)
        self.assertIn(app.SOURCE_URL, body)

    def test_head_returns_headers_without_body(self):
        code, headers, body = self.request("HEAD", "/")
        self.assertEqual(code, 200)
        self.assertEqual(body, "")
        self.assertGreater(int(headers["Content-Length"]), 1000)
        code, headers, body = self.request("HEAD", "/health")
        self.assertEqual((code, body, headers["Content-Length"]), (200, "", "2"))

    def test_street_whatif_makes_no_qloo_call(self):
        payload = {"units": [{"id": "A", "rent": 2000}, {"id": "B", "rent": 2000}, {"id": "C", "rent": 2000}],
                   "concepts": [{"id": "ramen", "category": "restaurant", "fit": 80, "overlap": 10},
                                {"id": "coffee", "category": "cafe", "fit": 70, "overlap": 10},
                                {"id": "books", "category": "retail", "fit": 60, "overlap": 10}],
                   "remove": "books", "add": None}
        path = "/street?payload=" + urllib.parse.quote(json.dumps(payload))
        with mock.patch("qloo.exec_cached", side_effect=AssertionError("qloo")):
            code, body = self.get(path)
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["qloo_calls"], 0)
        self.assertIn("No improving move:", data["after"]["proof"])
        code, body = self.get("/street?payload=" + urllib.parse.quote(json.dumps({"units": [], "concepts": []})))
        self.assertEqual(code, 400)
        code, body = self.get("/street?payload=" + urllib.parse.quote(json.dumps({"units": payload["units"], "concepts": None})))
        self.assertEqual(code, 400)
        self.assertNotIn("<html", body.lower())
        handler = app.Handler.__new__(app.Handler)
        handler.command = "GET"
        handler.client_address = ("127.0.0.1", 0)
        handler.path = "/street?payload=" + urllib.parse.quote(json.dumps({"fit": 80, "overlap": 10}))
        handler.requestline = "GET " + handler.path + " HTTP/1.1"
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            handler.log_request(200, 3)
        log = buf.getvalue()
        self.assertIn("GET /street", log)
        self.assertNotIn("overlap", log)
        self.assertNotIn("payload", log)
        units = payload["units"]
        for bad in ({"id": "ramen", "fit": float("nan"), "overlap": 10},
                    {"id": "ramen", "fit": 80, "overlap": float("inf")},
                    {"id": "ramen", "fit": 101, "overlap": 10}):
            raw = json.dumps({"units": units, "concepts": [bad], "add": "noodles"})
            code, body = self.get("/street?payload=" + urllib.parse.quote(raw))
            self.assertEqual(code, 400, bad)
            self.assertNotIn("NaN", body)
            self.assertNotIn("Infinity", body)
        huge = json.dumps({"units": [{"id": "A", "rent": 10 ** 400}, {"id": "B", "rent": 2000},
                                     {"id": "C", "rent": 2000}],
                           "concepts": [{"id": "ramen", "fit": 10 ** 400, "overlap": 10 ** 400}],
                           "add": "noodles"})
        code, body = self.get("/street?payload=" + urllib.parse.quote(huge))
        self.assertEqual(code, 400)
        self.assertNotIn("Traceback", body)
        for rent_row in ({"viable": True}, {"viable": True, "rent_max": 2800, "ci90": "nope"}):
            raw = json.dumps({"units": units, "concepts": [{"id": "ramen", "fit": 80, "overlap": 10,
                                                            "rent_row": rent_row}], "add": "noodles"})
            code, body = self.get("/street?payload=" + urllib.parse.quote(raw))
            self.assertEqual(code, 400, rent_row)
            self.assertNotIn("Traceback", body)
        good = json.dumps({"units": units, "concepts": [{"id": "ramen", "fit": 80, "overlap": 10,
                                                         "rent_row": {"viable": True, "rent_max": 2800,
                                                                      "ci90": [2000, 3200]}}],
                           "add": "noodles"})
        code, body = self.get("/street?payload=" + urllib.parse.quote(good))
        self.assertEqual(code, 200)

    def test_wrong_methods_get_plain_405(self):
        for method, path, allow in (("POST", "/", "GET, HEAD"), ("DELETE", "/health", "GET, HEAD"),
                                    ("HEAD", "/run", "GET"), ("BREW", "/", "GET, HEAD")):
            code, headers, body = self.request(method, path)
            self.assertEqual(code, 405, method)
            self.assertEqual(headers["Allow"], allow)
            self.assertTrue(headers["Content-Type"].startswith("text/plain"))
            self.assertNotIn("<html", body.lower())

    def test_demo_streams_the_synthetic_report(self):
        code, body = self.get("/demo")
        self.assertEqual(code, 200)
        kinds = [k for k, _ in sse_events(body)]
        self.assertEqual(kinds[0], "demo")
        self.assertEqual(kinds[-3:], ["geo", "scorecard", "memo"])
        self.assertIn("synthetic", sse_events(body)[0][1]["notice"].lower())

    def test_run_errors_are_generic_and_logged(self):
        def broken(concept, location):
            yield "step", {"tool": "find_tags", "args": {}, "names": [], "error": None, "site": None}
            raise RuntimeError("stderr: /srv/secret-path token=abc")

        with mock.patch.dict(os.environ, {"QLOO_API_KEY": "test"}), mock.patch.object(app.agent, "run", broken), \
                self.assertLogs("tastefit", level="ERROR"):
            code, body = self.get("/run?concept=a&location=b")
        self.assertEqual(code, 200)
        self.assertIn('event: error\ndata: "internal"', body)
        self.assertNotIn("secret", body)

    def test_concurrency_cap_returns_429(self):
        gate, started = threading.Event(), threading.Semaphore(0)

        def slow(concept, location):
            started.release()
            gate.wait(5)
            yield "memo", "done"

        results = []
        with mock.patch.dict(os.environ, {"QLOO_API_KEY": "test"}), mock.patch.object(app.agent, "run", slow):
            threads = [threading.Thread(target=lambda: results.append(self.get("/run?concept=a&location=b"))) for _ in range(2)]
            for t in threads:
                t.start()
            self.assertTrue(started.acquire(timeout=5) and started.acquire(timeout=5))
            code, body = self.get("/run?concept=a&location=b")
            gate.set()
            for t in threads:
                t.join(5)
            self.assertEqual(code, 429)
            self.assertIn("sample report", body)
            self.assertEqual([r[0] for r in results], [200, 200])
            self.assertEqual(self.get("/run?concept=a&location=b")[0], 200)

    def test_per_ip_rate_limit(self):
        with mock.patch.dict(os.environ, {"QLOO_API_KEY": "test"}), \
                mock.patch.object(app.agent, "run", lambda c, loc: iter([("memo", "ok")])), \
                mock.patch.object(app, "run_limiter", app.RateLimiter(1, 600)):
            self.assertEqual(self.get("/run?concept=a&location=b")[0], 200)
            code, body = self.get("/run?concept=a&location=b")
        self.assertEqual(code, 429)
        self.assertIn("few minutes", body)


class RateLimiterTest(unittest.TestCase):
    def test_sliding_window_per_key(self):
        now = [0.0]
        rl = app.RateLimiter(2, 60, clock=lambda: now[0])
        self.assertTrue(rl.allow("a"))
        self.assertTrue(rl.allow("a"))
        self.assertFalse(rl.allow("a"))
        self.assertTrue(rl.allow("b"))
        now[0] = 61
        self.assertTrue(rl.allow("a"))


if __name__ == "__main__":
    unittest.main()
