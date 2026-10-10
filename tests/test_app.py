import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer

os.environ.pop("QLOO_API_KEY", None)
import app  # noqa: E402


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_index(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("TasteFit", body)
        self.assertNotIn("{{CTA}}", body)

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

    def test_health(self):
        self.assertEqual(self.get("/health"), (200, "ok"))

    def test_run_requires_inputs(self):
        self.assertEqual(self.get("/run?concept=x")[0], 400)

    def test_run_without_key_reports_offline(self):
        code, body = self.get("/run?concept=wine%20bar&location=Silver%20Lake")
        self.assertEqual(code, 200)
        self.assertIn('event: error\ndata: "offline"', body)


if __name__ == "__main__":
    unittest.main()
