"""TasteFit web app: stdlib HTTP server streaming the agent's steps (Server-Sent Events)."""
import collections
import html
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent
import scan

log = logging.getLogger("tastefit")
ROOT = Path(__file__).parent
SOURCE_URL = "https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit"

_CONTACT = os.environ.get("CONTACT_EMAIL", "").strip()
_CTA = (f'<a href="mailto:{html.escape(_CONTACT)}?subject=TasteFit%20early%20access">Request early access</a> '
        "Tell us your city and how many sites you check a year."
        if _CONTACT else
        f'TasteFit is a hackathon prototype. Follow the project on <a class="plain" href="{SOURCE_URL}">GitLab</a>.')
_TEMPLATE = (ROOT / "index.html").read_text().replace("{{CTA}}", _CTA).replace("{{SOURCE}}", SOURCE_URL)

# Synthetic replay for /demo; never real Qloo output (AGENTS.md rule 2).
DEMO_FILE = ROOT / "demo" / "sample-report.json"
DEMO_DELAY_SCALE = float(os.environ.get("DEMO_DELAY_SCALE", "1"))

CSP_PAGE = ("default-src 'self'; script-src 'nonce-{nonce}' https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data: https://tile.openstreetmap.org; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
CSP_OTHER = "default-src 'none'; frame-ancestors 'none'"

BUSY_MSG = ("TasteFit is already running as many analyses as it can. Try again in a minute, "
            "or open the sample report in the meantime.")
RATE_MSG = ("You have started several analyses in the last few minutes. Try again in a few minutes, "
            "or open the sample report in the meantime.")


class RateLimiter:
    """At most `limit` hits per `window` seconds per key (sliding window)."""

    def __init__(self, limit, window, clock=time.monotonic, max_keys=10000):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self.hits, self.lock = {}, threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault(key, collections.deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > self.max_keys:
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= self.window]:
                    del self.hits[k]
            return True


MAX_RUNS = int(os.environ.get("MAX_CONCURRENT_RUNS", "2"))
run_slots = threading.BoundedSemaphore(MAX_RUNS)
run_limiter = RateLimiter(int(os.environ.get("RUNS_PER_IP", "6")), 600)
demo_limiter = RateLimiter(30, 600)
scan_limiter = RateLimiter(60, 600)


def build_label():
    """Short build id shown in the footer: first 7 chars of RENDER_GIT_COMMIT, or 'dev' when unset."""
    return os.environ.get("RENDER_GIT_COMMIT", "").strip()[:7] or "dev"


def render_index(nonce=""):
    return _TEMPLATE.replace("{{BUILD}}", html.escape(build_label())).replace("{{NONCE}}", nonce).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "TasteFit"
    sys_version = ""
    _csp = CSP_OTHER

    def version_string(self):
        return self.server_version

    def end_headers(self):
        self.send_header("Content-Security-Policy", self._csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def client_ip(self):
        """Rightmost X-Forwarded-For hop (added by the hosting proxy), else the socket peer."""
        hops = [h.strip() for h in self.headers.get("X-Forwarded-For", "").split(",") if h.strip()]
        return hops[-1] if hops else self.client_address[0]

    def _page(self, path):
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            return 200, "text/html; charset=utf-8", render_index(nonce), CSP_PAGE.format(nonce=nonce)
        if path == "/health":
            return 200, "text/plain", f"ok {os.environ.get('RENDER_GIT_COMMIT', '')}".strip().encode(), None
        return 404, "text/plain", b"not found", None

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/run":
            self._run(urllib.parse.parse_qs(url.query))
        elif url.path == "/demo":
            self._demo()
        elif url.path in ("/scan", "/scan/unit"):
            self._scan(url.path, urllib.parse.parse_qs(url.query))
        else:
            self._send(*self._page(url.path))

    def do_HEAD(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/run", "/demo"):
            return self._not_allowed()
        self._send(*self._page(path))

    def _not_allowed(self):
        path = urllib.parse.urlparse(self.path).path
        self.close_connection = True
        self._send(405, "text/plain; charset=utf-8", b"method not allowed",
                   headers=[("Allow", "GET" if path in ("/run", "/demo") else "GET, HEAD")])

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed

    def send_error(self, code, message=None, explain=None):
        """Plain-text errors instead of the default HTML page; unknown methods get 405."""
        if code == 501:
            code, message = 405, "method not allowed"
        self.log_error("code %d, message %s", code, message)
        body = (message or self.responses.get(code, ("error",))[0]).encode("utf-8", "replace")
        self.close_connection = True
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Connection", "close")
            if code == 405:
                self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD" and code >= 200 and code not in (204, 304):
                self.wfile.write(body)
        except OSError:
            pass

    def _send(self, code, ctype, body, csp=None, headers=()):
        self._csp = csp or CSP_OTHER
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _too_many(self, message, retry_after):
        self._send(429, "text/plain; charset=utf-8", message.encode(), headers=[("Retry-After", str(retry_after))])

    def _start_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _event(self, kind, data):
        self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _run(self, q):
        concept, location = q.get("concept", [""])[0][:300], q.get("location", [""])[0][:120]
        if not concept or not location:
            return self._send(400, "text/plain", b"concept and location are required")
        if not os.environ.get("QLOO_API_KEY"):
            self._start_stream()
            return self._event("error", "offline")
        if not run_limiter.allow(self.client_ip()):
            return self._too_many(RATE_MSG, 600)
        if not run_slots.acquire(blocking=False):
            return self._too_many(BUSY_MSG, 60)
        events = None
        try:
            self._start_stream()
            events = agent.run(concept, location)
            for kind, data in events:
                self._event(kind, data)
        except (BrokenPipeError, ConnectionResetError):
            log.info("client disconnected during a run")
        except Exception:  # noqa: BLE001  details go to the server log, never to the browser
            log.exception("run failed")
            try:
                self._event("error", "internal")
            except OSError:
                pass
        finally:
            if hasattr(events, "close"):
                events.close()
            run_slots.release()

    def _scan(self, path, q):
        if not scan_limiter.allow(self.client_ip()):
            return self._too_many(RATE_MSG, 600)
        arg = lambda k: q.get(k, [""])[0]  # noqa: E731
        try:
            code, body = (scan.scan(arg("city"), arg("concept"), arg("top")) if path == "/scan"
                          else scan.unit(arg("city"), arg("lat"), arg("lon")))
        except Exception:  # noqa: BLE001  details go to the server log, never to the browser
            log.exception("scan failed")
            return self._send(500, "text/plain; charset=utf-8", b"internal")
        if "error" in body:
            return self._send(code, "text/plain; charset=utf-8", body["error"].encode())
        self._send(code, "application/json", json.dumps(body, ensure_ascii=False).encode())

    def _demo(self):
        if not demo_limiter.allow(self.client_ip()):
            return self._too_many(RATE_MSG, 600)
        try:
            events = json.loads(DEMO_FILE.read_text())["events"]
        except (OSError, ValueError, KeyError):
            log.exception("sample report unavailable")
            return self._send(503, "text/plain; charset=utf-8", b"sample report unavailable")
        self._start_stream()
        try:
            for e in events:
                time.sleep(max(0, e.get("delay_ms", 0)) / 1000 * DEMO_DELAY_SCALE)
                self._event(e["event"], e["data"])
        except (BrokenPipeError, ConnectionResetError):
            log.info("client disconnected during the sample report")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8000))), Handler).serve_forever()
