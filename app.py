"""TasteFit web app: stdlib HTTP server streaming the agent's steps (Server-Sent Events)."""
import html
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent

_CONTACT = os.environ.get("CONTACT_EMAIL", "").strip()
_CTA = (f'<a href="mailto:{html.escape(_CONTACT)}?subject=TasteFit%20early%20access">Request early access</a>'
        "Tell us your city and how many sites you check a year."
        if _CONTACT else
        'Paid plans are not open yet. Follow the launch on '
        '<a class="plain" href="https://github.com/youssefbenzidiya-cmd/tastefit">GitHub</a>.')
INDEX = (Path(__file__).parent / "index.html").read_text().replace("{{CTA}}", _CTA).encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            self._send(200, "text/html; charset=utf-8", INDEX)
        elif url.path == "/health":
            self._send(200, "text/plain", f"ok {os.environ.get('RENDER_GIT_COMMIT', '')}".strip().encode())
        elif url.path == "/run":
            q = urllib.parse.parse_qs(url.query)
            concept, location = q.get("concept", [""])[0][:300], q.get("location", [""])[0][:120]
            if not concept or not location:
                return self._send(400, "text/plain", b"concept and location are required")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not os.environ.get("QLOO_API_KEY"):
                self.wfile.write(b'event: error\ndata: "offline"\n\n')
                return
            try:
                for kind, text in agent.run(concept, location):
                    self.wfile.write(f"event: {kind}\ndata: {json.dumps(text)}\n\n".encode())
                    self.wfile.flush()
            except Exception as e:  # noqa: BLE001  report to the page instead of dropping the stream
                self.wfile.write(f"event: error\ndata: {json.dumps(str(e))}\n\n".encode())
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8000))), Handler).serve_forever()
