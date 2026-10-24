"""TasteFit web app: stdlib HTTP server streaming the agent's steps (Server-Sent Events)."""
import collections
import html
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent
import evidence
import ledger
import portfolio
import scan
import tenant

log = logging.getLogger("tastefit")
ROOT = Path(__file__).parent
SOURCE_URL = "https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit"

_CONTACT = os.environ.get("CONTACT_EMAIL", "").strip()
_CTA = (f'<a href="mailto:{html.escape(_CONTACT)}?subject=TasteFit%20early%20access">Request early access</a> '
        "Tell us your city and how many sites you check a year."
        if _CONTACT else
        f'TasteFit is a hackathon prototype. Follow the project on <a class="plain" href="{SOURCE_URL}">GitLab</a>.')
# Synthetic replay for /demo; never real Qloo output (AGENTS.md rule 2).
DEMO_FILE = ROOT / "demo" / "sample-report.json"
# Aggregates-only saved example. No Qloo response is stored in this file.
WICKER_FILE = ROOT / "demo" / "wicker-park.json"
DEMO_DELAY_SCALE = float(os.environ.get("DEMO_DELAY_SCALE", "1"))
# Comment pings while a run is blocked on the network. Proxies drop a silent stream.
# The pad and TCP_NODELAY are what make a comment leave the process: an 8-byte
# ping was held until the next real event on Cloud Run (measured 2026-10-17).
SSE_PING_S = 10
SSE_PING = b": ping" + b" " * 2048 + b"\n\n"

STATIC = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon.ico": ("favicon.svg", "image/svg+xml"),
    "/og.png": ("og.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/fonts/SchibstedGrotesk-latin.woff2": ("fonts/SchibstedGrotesk-latin.woff2", "font/woff2"),
}

CSP_PAGE = ("default-src 'self'; script-src 'self' 'nonce-{nonce}' https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "font-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
CSP_OTHER = "default-src 'none'; frame-ancestors 'none'"
CSP_LEDGER = ("default-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
STREAMS = ("/run", "/tenant", "/demo")

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
    page = ((ROOT / "index.html").read_text()
            .replace("{{CTA}}", _CTA).replace("{{SOURCE}}", SOURCE_URL)
            .replace("{{BUILD}}", html.escape(build_label())).replace("{{NONCE}}", nonce))
    return page.encode()


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

    def _page(self, path, query=""):
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            return 200, "text/html; charset=utf-8", render_index(nonce), CSP_PAGE.format(nonce=nonce)
        if path == "/health":
            return 200, "text/plain", f"ok {os.environ.get('RENDER_GIT_COMMIT', '')}".strip().encode(), None
        if path == "/walkthrough":
            nonce = secrets.token_urlsafe(16)
            page = (ROOT / "demo" / "walkthrough.html").read_text(encoding="utf-8")
            scene = urllib.parse.parse_qs(query).get("scene", [""])[0]
            if scene in ("1", "2", "3", "4", "5"):
                page = page.replace('<html lang="en">', f'<html lang="en" data-scene="{scene}">', 1)
            return 200, "text/html; charset=utf-8", page.encode(), CSP_PAGE.format(nonce=nonce)
        if path == "/ledger":
            return 200, "text/html; charset=utf-8", ledger.page().encode(), CSP_LEDGER
        if path == "/evidence":
            return 200, "text/html; charset=utf-8", evidence.page().encode(), CSP_LEDGER
        if path == "/wicker-example.json":
            return 200, "application/json", WICKER_FILE.read_bytes(), CSP_OTHER
        if path in STATIC:
            name, ctype = STATIC[path]
            age = "60" if name.endswith((".js", ".css")) else "86400"
            return 200, ctype, (ROOT / "static" / name).read_bytes(), None, (
                ("Cache-Control", f"public, max-age={age}"),
            )
        return 404, "text/plain", b"not found", None

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/run":
            self._run(urllib.parse.parse_qs(url.query))
        elif url.path == "/tenant":
            self._tenant(urllib.parse.parse_qs(url.query))
        elif url.path == "/demo":
            self._demo()
        elif url.path in ("/scan", "/scan/unit", "/ask", "/spot", "/taste"):
            self._scan(url.path, urllib.parse.parse_qs(url.query))
        elif url.path == "/street":
            self._street(urllib.parse.parse_qs(url.query))
        else:
            self._send(*self._page(url.path, url.query))

    def do_HEAD(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in STREAMS:
            return self._not_allowed()
        self._send(*self._page(url.path, url.query))

    def _not_allowed(self):
        path = urllib.parse.urlparse(self.path).path
        self.close_connection = True
        self._send(405, "text/plain; charset=utf-8", b"method not allowed",
                   headers=[("Allow", "GET" if path in STREAMS else "GET, HEAD")])

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
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            pass

    def _event(self, kind, data):
        self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _run(self, q):
        concept, location = q.get("concept", [""])[0][:300], q.get("location", [""])[0][:120]
        if not concept or not location:
            return self._send(400, "text/plain", b"concept and location are required")
        self._stream_agent(lambda: agent.run(concept, location))

    def _tenant(self, q):
        arg = lambda k: q.get(k, [""])[0].strip()  # noqa: E731
        city, location = arg("city")[:120], arg("location")[:120]
        try:
            lat = float(arg("lat")) if arg("lat") else None
            lon = float(arg("lon")) if arg("lon") else None
            rent = float(arg("rent")) if arg("rent") else None
        except ValueError:
            return self._send(400, "text/plain", b"lat, lon and rent must be numbers")
        if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
            return self._send(400, "text/plain", b"lat and lon must both be valid coordinates")
        if rent is not None and not (0 < rent < 1e7):
            return self._send(400, "text/plain", b"rent must be a positive monthly amount")
        extras, k = None, 1
        raw_portfolio = arg("portfolio")
        if raw_portfolio:
            extras = []
            for part in raw_portfolio.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    value = float(part)
                except ValueError:
                    return self._send(400, "text/plain", b"portfolio rents must be positive monthly amounts")
                if not (0 < value < 1e7):
                    return self._send(400, "text/plain", b"portfolio rents must be positive monthly amounts")
                extras.append(value)
            if len(extras) > 5:
                return self._send(400, "text/plain", b"portfolio accepts at most 5 rents")
        if arg("k"):
            try:
                k = int(arg("k"))
            except ValueError:
                return self._send(400, "text/plain", b"k must be a whole number from 1 to 5")
            if not 1 <= k <= 5:
                return self._send(400, "text/plain", b"k must be a whole number from 1 to 5")
        if lat is None and not (city and location):
            return self._send(400, "text/plain", b"a map point (lat, lon) or a city and location are required")
        self._stream_agent(lambda: tenant.run(city, location or None, lat, lon, rent,
                                               portfolio_rents=extras, portfolio_k=k))

    def _street(self, q):
        """Re-solve a street from concepts the page already has. This makes no Qloo call."""
        raw = (q.get("payload") or [""])[0]
        if len(raw) > 20000:
            return self._send(400, "text/plain", b"street payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return self._send(400, "text/plain", b"street payload must be JSON")
        if not isinstance(payload, dict):
            return self._send(400, "text/plain", b"street payload must be JSON")
        try:
            out = portfolio.what_if(payload.get("units"), payload.get("concepts"),
                                    remove=payload.get("remove"), add=payload.get("add"))
        except (ValueError, TypeError) as exc:
            return self._send(400, "text/plain", str(exc).encode())
        self._send(200, "application/json", json.dumps(out).encode())

    def log_request(self, code="-", size="-"):
        """Keep /street query strings, which carry this run's fit and overlap, out of the access log."""
        path = urllib.parse.urlparse(self.path).path
        line = f"{self.command} {path}" if path == "/street" else self.requestline
        self.log_message('"%s" %s %s', line, str(code), str(size))

    def _stream_agent(self, start):
        if not os.environ.get("QLOO_API_KEY"):
            self._start_stream()
            return self._event("error", "offline")
        if not run_limiter.allow(self.client_ip()):
            return self._too_many(RATE_MSG, 600)
        if not run_slots.acquire(blocking=False):
            return self._too_many(BUSY_MSG, 60)
        events = None
        stop = threading.Event()
        lock = threading.Lock()

        def ping():
            while not stop.wait(SSE_PING_S):
                try:
                    with lock:
                        if stop.is_set():
                            return
                        self.connection.sendall(SSE_PING)
                except OSError:
                    return

        try:
            self._start_stream()
            threading.Thread(target=ping, daemon=True).start()
            events = start()
            for kind, data in events:
                with lock:
                    self._event(kind, data)
        except (BrokenPipeError, ConnectionResetError):
            log.info("client disconnected during a run")
        except Exception:  # noqa: BLE001  details go to the server log, never to the browser
            log.exception("run failed")
            try:
                with lock:
                    self._event("error", "internal")
            except OSError:
                pass
        finally:
            stop.set()
            with lock:
                pass
            if hasattr(events, "close"):
                events.close()
            run_slots.release()

    def _scan(self, path, q):
        if not scan_limiter.allow(self.client_ip()):
            return self._too_many(RATE_MSG, 600)
        arg = lambda k: q.get(k, [""])[0]  # noqa: E731
        try:
            if path == "/scan":
                code, body = scan.scan(arg("city"), arg("concept"), arg("top"))
            elif path == "/ask":
                code, body = scan.ask(arg("city"), arg("group"))
            elif path == "/spot":
                code, body = scan.spot(arg("city"), arg("lat"), arg("lon"))
            elif path == "/taste":
                code, body = scan.taste(arg("concept"), arg("location"))
            else:
                code, body = scan.unit(arg("city"), arg("lat"), arg("lon"))
        except Exception:  # noqa: BLE001  details go to the server log, never to the browser
            log.exception("scan failed")
            return self._send(500, "text/plain; charset=utf-8", b"internal")
        if "error" in body:
            return self._send(code, "text/plain; charset=utf-8", body["error"].encode())
        extra = (("Cache-Control", "public, max-age=300"),) if path == "/ask" else ()
        self._send(code, "application/json", json.dumps(body, ensure_ascii=False).encode(), headers=extra)

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
