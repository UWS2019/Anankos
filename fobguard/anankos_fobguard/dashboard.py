"""Local staff dashboard: stdlib HTTP server + Server-Sent Events."""

from __future__ import annotations

import hmac
import json
import logging
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .engine import Engine

log = logging.getLogger("fobguard.dashboard")


def _page() -> bytes:
    return resources.files("anankos_fobguard").joinpath("web/dashboard.html").read_bytes()


class Dashboard:
    def __init__(self, engine: Engine, bind: str, port: int, token: str = ""):
        self.engine = engine
        self.token = token
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        engine.listeners.append(self._broadcast)
        dash = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "FobGuard/0.1"

            def log_message(self, fmt, *args):  # quiet
                log.debug("%s - " + fmt, self.address_string(), *args)

            def _authed(self) -> bool:
                if not dash.token:
                    return True
                q = parse_qs(urlparse(self.path).query).get("token", [""])[0]
                h = self.headers.get("Authorization", "")
                given = h[7:] if h.startswith("Bearer ") else q
                return hmac.compare_digest(given.encode(), dash.token.encode())

            def _send(self, code: int, body: bytes, ctype: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj):
                self._send(code, json.dumps(obj, default=str).encode())

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    # the page itself is static; API calls carry the token
                    return self._send(200, _page(), "text/html; charset=utf-8")
                if not self._authed():
                    return self._json(401, {"error": "unauthorized"})
                if path == "/api/status":
                    with dash.engine._lock:
                        st = dash.engine.status()
                    return self._json(200, st)
                if path == "/api/events":
                    return self._sse()
                if path == "/healthz":
                    return self._json(200, {"ok": True})
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if not self._authed():
                    return self._json(401, {"error": "unauthorized"})
                path = urlparse(self.path).path
                n = min(int(self.headers.get("Content-Length", 0) or 0), 10000)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "bad json"})
                m = re.fullmatch(r"/api/incidents/(\d+)/ack", path)
                if m:
                    inc = dash.engine.acknowledge(int(m.group(1)), str(body.get("by", "staff")))
                    return self._json(200 if inc else 404, inc.to_dict(False) if inc else {"error": "no such incident"})
                if path == "/api/suppress":
                    minutes = max(0.0, min(240.0, float(body.get("minutes", 0))))
                    reason = str(body.get("reason", ""))[:200]
                    dash.engine.alerts.suppress(time.time() + minutes * 60, reason)
                    log.warning("non-critical alerts suppressed for %.0f min: %s", minutes, reason)
                    return self._json(200, {"suppressed_until": dash.engine.alerts.suppressed_until})
                self._json(404, {"error": "not found"})

            def _sse(self):
                q: queue.Queue = queue.Queue(maxsize=500)
                with dash.lock:
                    dash.clients.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    while True:
                        try:
                            kind, data = q.get(timeout=15)
                            msg = f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
                        except queue.Empty:
                            msg = ": keepalive\n\n"
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with dash.lock:
                        if q in dash.clients:
                            dash.clients.remove(q)

        self.httpd = ThreadingHTTPServer((bind, port), Handler)
        self.httpd.daemon_threads = True

    def _broadcast(self, kind: str, data: dict) -> None:
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait((kind, data))
                except queue.Full:
                    pass

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dashboard")
        t.start()
        host, port = self.httpd.server_address[:2]
        log.info("dashboard on http://%s:%s/", host, port)
        return t

    def stop(self) -> None:
        self.httpd.shutdown()
