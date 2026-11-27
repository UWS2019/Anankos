"""Alert delivery: console, webhook (Slack / Teams / generic JSON), email, syslog,
and an optional local command hook (e.g. to trigger a siren or strobe relay)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import logging.handlers
import shlex
import smtplib
import subprocess
import sys
import threading
import urllib.request
from email.message import EmailMessage

from .config import Config, SinkConfig
from .models import Incident, Severity

log = logging.getLogger("fobguard.alerts")

COLORS = {Severity.CRITICAL: "#d64545", Severity.HIGH: "#e08a2b", Severity.MEDIUM: "#c9b48a",
          Severity.LOW: "#8fbf5a", Severity.INFO: "#8a8d86"}


def render_text(cfg: Config, inc: Incident, action: str) -> tuple[str, str]:
    tz = cfg.site.tz()
    when = dt.datetime.fromtimestamp(inc.updated, tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    monitors = sorted({d.monitor for d in inc.detections})
    zones = sorted({d.zone for d in inc.detections})
    subject = f"[{inc.severity.name}] {inc.title} - {cfg.site.name}"
    lines = [
        f"Anankos FobGuard alert - {cfg.site.name}",
        f"{inc.severity.name}: {inc.title}",
        f"Time: {when}   Zone: {', '.join(zones)}   Sensors: {', '.join(monitors)}",
        "",
    ]
    for d in inc.detections[-6:]:
        t = dt.datetime.fromtimestamp(d.t, tz).strftime("%H:%M:%S")
        lines.append(f"  {t} [{d.severity.name.lower()}] {d.message}")
    if action:
        lines += ["", f"What to do: {action}"]
    lines += ["", f"Incident #{inc.id}. Acknowledge on the FobGuard dashboard."]
    return subject, "\n".join(lines)


class Sink:
    def __init__(self, cfg: Config, sc: SinkConfig):
        self.cfg = cfg
        self.opts = sc.options
        self.min_sev = Severity.parse(self.opts.get("min_severity", cfg.alerts.min_severity))

    def wants(self, inc: Incident) -> bool:
        return inc.severity >= self.min_sev

    def send(self, inc: Incident, subject: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError


class ConsoleSink(Sink):
    def send(self, inc, subject, body):
        bar = "=" * 72
        print(f"\n{bar}\n{body}\n{bar}", file=sys.stderr, flush=True)


class WebhookSink(Sink):
    def send(self, inc, subject, body):
        fmt = self.opts.get("format", "json")
        if fmt == "slack":
            payload = {"text": subject, "attachments": [{"color": COLORS[inc.severity], "text": body}]}
        elif fmt == "teams":
            payload = {"@type": "MessageCard", "@context": "https://schema.org/extensions",
                       "themeColor": COLORS[inc.severity].lstrip("#"), "summary": subject,
                       "title": subject, "text": body.replace("\n", "<br>")}
        else:
            payload = {"site": self.cfg.site.name, "subject": subject, "text": body,
                       "incident": inc.to_dict()}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.opts["url"], data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=float(self.opts.get("timeout", 10))) as r:
            r.read()


class EmailSink(Sink):
    def send(self, inc, subject, body):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.opts["from"]
        to = self.opts["to"]
        msg["To"] = ", ".join(to) if isinstance(to, list) else to
        msg.set_content(body)
        host, port = self.opts.get("host", "localhost"), int(self.opts.get("port", 587))
        with smtplib.SMTP(host, port, timeout=15) as s:
            if self.opts.get("starttls", True):
                s.starttls()
            if self.opts.get("username"):
                s.login(self.opts["username"], self.opts.get("password", ""))
            s.send_message(msg)


class SyslogSink(Sink):
    def __init__(self, cfg, sc):
        super().__init__(cfg, sc)
        addr = self.opts.get("address", "/dev/log")
        if ":" in str(addr):
            h, p = str(addr).rsplit(":", 1)
            addr = (h, int(p))
        self.logger = logging.getLogger("fobguard.syslog")
        self.logger.propagate = False
        if not self.logger.handlers:
            self.logger.addHandler(logging.handlers.SysLogHandler(address=addr))

    def send(self, inc, subject, body):
        self.logger.warning("fobguard incident=%s severity=%s title=%r kinds=%s",
                            inc.id, inc.severity.name, inc.title, ",".join(sorted(inc.kinds)))


class CommandSink(Sink):
    """Runs a local command, e.g. a script that drives a siren relay or PA system.
    Incident JSON is passed on stdin. No shell is involved."""

    def send(self, inc, subject, body):
        argv = shlex.split(self.opts["command"])
        subprocess.run(argv, input=json.dumps(inc.to_dict()).encode(), timeout=30, check=False)


SINKS = {"console": ConsoleSink, "webhook": WebhookSink, "email": EmailSink,
         "syslog": SyslogSink, "command": CommandSink}


class AlertManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sinks: list[Sink] = []
        for sc in cfg.alerts.sinks:
            cls = SINKS.get(sc.type)
            if cls is None:
                raise ValueError(f"unknown alert sink type {sc.type!r}")
            self.sinks.append(cls(cfg, sc))
        self.suppressed_until = 0.0
        self.suppress_reason = ""
        self.sent: list[tuple[int, str]] = []

    def suppress(self, until: float, reason: str) -> None:
        self.suppressed_until, self.suppress_reason = until, reason

    def dispatch(self, inc: Incident, action: str, now: float, background: bool = True) -> bool:
        if now < self.suppressed_until and inc.severity < Severity.CRITICAL:
            log.info("alert for incident %s suppressed (%s)", inc.id, self.suppress_reason)
            return False
        subject, body = render_text(self.cfg, inc, action)
        self.sent.append((inc.id, inc.title))
        for s in self.sinks:
            if not s.wants(inc):
                continue
            if background and not isinstance(s, ConsoleSink):
                threading.Thread(target=self._safe, args=(s, inc, subject, body), daemon=True).start()
            else:
                self._safe(s, inc, subject, body)
        return True

    @staticmethod
    def _safe(s: Sink, inc, subject, body):
        try:
            s.send(inc, subject, body)
        except Exception as e:  # never let a dead webhook stop detection
            log.error("alert sink %s failed: %s", type(s).__name__, e)
