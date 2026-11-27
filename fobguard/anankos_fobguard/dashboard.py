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

from pathlib import Path

from .antenna import AntennaError
from .engine import Engine

log = logging.getLogger("fobguard.dashboard")


def _page() -> bytes:
    return resources.files("anankos_fobguard").joinpath("web/dashboard.html").read_bytes()


class Dashboard:
    def __init__(self, engine: Engine, bind: str, port: int, token: str = "",
                 config_path: str | None = None):
        self.engine = engine
        self.token = token
        self.config_path = config_path
        self.state_dir = str(Path(engine.cfg.storage_path).parent)
        self._doctor_cache: tuple[float, dict] | None = None
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
                if path == "/api/hardware":
                    return self._json(200, dash.hardware())
                if path == "/api/doctor":
                    fresh = parse_qs(urlparse(self.path).query).get("refresh", ["0"])[0] == "1"
                    return self._json(200, dash.doctor(fresh))
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
                m = re.fullmatch(r"/api/monitors/([\w.-]+)/antenna(/check|/sweep)?", path)
                if m:
                    mon, action = m.group(1), m.group(2) or ""
                    who = str(body.get("by", "staff"))[:60] or "staff"
                    try:
                        if action == "/check":
                            return self._json(200, dash.engine.antennas.assess(mon))
                        if action == "/sweep":
                            return self._json(200, dash.engine.antennas.start_sweep(
                                mon, float(body.get("dwell_s", 60)), bool(body.get("apply", True)), who))
                        return self._json(200, dash.engine.antennas.select(
                            mon, str(body.get("option", "")), who, bool(body.get("force", False))))
                    except AntennaError as e:
                        return self._json(400, {"error": str(e)})
                if path == "/api/doctor/fix":
                    ids = [str(i) for i in body.get("ids", [])][:50]
                    return self._json(200, dash.request_fix(ids, str(body.get("by", "staff"))))
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

    # -- hardware / system --------------------------------------------------
    def hardware(self) -> dict:
        from .hardware import detect_devices

        eng = self.engine
        mons = {}
        for name in eng.monitors:
            mons[name] = {"options": eng.antennas.options(name), **eng.antennas.snapshot(name),
                          "check": eng.antennas.assess(name)}
        return {"devices": [d.to_dict() for d in detect_devices()], "monitors": mons}

    def doctor(self, fresh: bool = False) -> dict:
        from .doctor import Doctor, helper_installed, read_result

        now = time.time()
        if fresh or self._doctor_cache is None or now - self._doctor_cache[0] > 60:
            doc = Doctor(self.engine.cfg)
            self._doctor_cache = (now, {"package_manager": doc.pm.key or None,
                                        "checks": [c.to_dict() for c in doc.checks()]})
        return {**self._doctor_cache[1], "helper": helper_installed(), "last_fix": read_result(self.state_dir),
                "checked_at": self._doctor_cache[0]}

    def request_fix(self, ids: list[str], who: str) -> dict:
        from .doctor import helper_installed, write_request

        cfg_arg = f" -c {self.config_path}" if self.config_path else ""
        if not helper_installed():
            return {"queued": False, "command": f"sudo fobguard doctor --fix{cfg_arg}",
                    "message": "The installer's root helper is not set up on this machine. "
                               "Run the command shown on the FobGuard computer."}
        write_request(self.state_dir, ids, who)
        self._doctor_cache = None
        log.warning("system fix requested by %s: %s", who, ids)
        return {"queued": True, "message": "Install started by the system helper. This can take a few minutes."}

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
