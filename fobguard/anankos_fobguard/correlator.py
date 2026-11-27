"""Group detections into incidents, name the attack, decide when to alert."""

from __future__ import annotations

import itertools
import threading

from .config import Config
from .models import Detection, Incident, Severity

# (required kinds, title, severity, staff action) - first match wins.
PATTERNS: list[tuple[set[str], str, Severity, str]] = [
    ({"rollback"}, "RollBack rolling-code attack", Severity.CRITICAL,
     "Treat as an active theft attempt. Check the lot now, note vehicles near the sensor, and call "
     "security/police. Affected vehicles may need their fobs re-synchronised by service."),
    ({"replay", "jamming"}, "RollJam-style capture and replay", Severity.CRITICAL,
     "Someone jammed the fob band, captured a code and has now replayed it. Check the lot immediately; "
     "vehicles unlocked in the last few minutes may be compromised."),
    ({"lf_interrogation", "stored_key_responding"}, "Relay attack in progress", Severity.CRITICAL,
     "A key in storage is being relayed to a car. Check the key-room perimeter and the lot; "
     "move keys into a shielded (Faraday) cabinet."),
    ({"replay"}, "Replayed key fob code", Severity.HIGH,
     "A captured key code was re-transmitted. Check the lot near the reporting sensor."),
    ({"bruteforce"}, "Key code scanning / brute force", Severity.HIGH,
     "A device is cycling through codes. Look for a person with a handheld device or a parked vehicle "
     "near the reporting sensor."),
    ({"stored_key_responding"}, "Stored key transmitting after hours", Severity.HIGH,
     "Keys in storage should be silent when closed. Possible relay attack or unauthorised key-room access."),
    ({"jamming", "fob_during_jam"}, "Jamming while a fob was used", Severity.HIGH,
     "Vehicles locked during this period may NOT be locked. Physically check them; the code may have "
     "been captured for later replay."),
    ({"jamming"}, "Key fob band jammed", Severity.HIGH,
     "Something is blocking key fob signals. Cars may fail to lock. Walk the lot and look for "
     "unfamiliar devices or loitering vehicles."),
    ({"lf_interrogation"}, "Key wake-up signals after hours", Severity.HIGH,
     "Someone may be probing vehicles or keys with a relay device."),
    ({"sensor_offline"}, "RF sensor offline", Severity.HIGH,
     "Coverage is down. Check power/USB to the sensor; tampering is possible."),
    ({"after_hours_activity"}, "After-hours key fob activity", Severity.MEDIUM,
     "Fob use while closed. If no staff are on site, check cameras/lot."),
    ({"receiver_saturation"}, "Strong transmitter near sensor", Severity.MEDIUM,
     "A powerful transmitter is next to a sensor; check what is nearby."),
    ({"fixed_code_repeat"}, "Fixed-code device activity", Severity.LOW,
     "Informational. Fixed-code remotes are easily cloned."),
]

ESCALATE_WHEN_CLOSED = {"jamming", "replay", "bruteforce", "fob_during_jam", "stored_key_responding"}


class Correlator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.window = cfg.alerts.incident_window_seconds
        self.incidents: list[Incident] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def classify(self, inc: Incident) -> tuple[str, Severity, str]:
        kinds = inc.kinds
        for need, title, sev, action in PATTERNS:
            if need <= kinds:
                worst = max(d.severity for d in inc.detections)
                sev = max(sev, worst)
                closed = not self.cfg.site.is_open(inc.opened)
                if closed and sev == Severity.HIGH and kinds & ESCALATE_WHEN_CLOSED:
                    sev = Severity.CRITICAL
                return title, sev, action
        worst = max(d.severity for d in inc.detections)
        return inc.detections[0].kind.replace("_", " ").title(), worst, ""

    def open_incidents(self, now: float) -> list[Incident]:
        return [i for i in self.incidents if now - i.updated <= self.window]

    def add(self, det: Detection) -> tuple[Incident | None, bool]:
        """Returns (incident, should_alert)."""
        if det.severity == Severity.INFO:
            # informational: attach to an open incident if any, never alert
            with self._lock:
                for inc in reversed(self.incidents):
                    if det.t - inc.updated <= self.window:
                        inc.detections.append(det)
                        return inc, False
            return None, False
        with self._lock:
            target = None
            for inc in reversed(self.incidents):
                if det.t - inc.updated <= self.window:
                    target = inc
                    break
            if target is None:
                target = Incident(next(self._ids), det.t, det.t, det.severity, "", det.zone)
                self.incidents.append(target)
                if len(self.incidents) > 2000:
                    self.incidents = self.incidents[-1000:]
            target.detections.append(det)
            target.updated = max(target.updated, det.t)
            if det.zone not in target.zone.split(", "):
                target.zone = f"{target.zone}, {det.zone}"
            title, sev, _ = self.classify(target)
            target.title = title
            target.severity = sev
            min_sev = Severity.parse(self.cfg.alerts.min_severity)
            rank = self._rank(title)
            # Re-alert when it gets worse, or when it is identified as a more
            # specific attack (e.g. "band jammed" -> "RollJam capture and replay").
            alert = sev >= min_sev and (
                target.alerted_severity is None or sev > target.alerted_severity
                or (target.alerted_rank is not None and rank < target.alerted_rank))
            if alert:
                target.alerted_severity = sev
                target.alerted_rank = rank
            return target, alert

    @staticmethod
    def _rank(title: str) -> int:
        for i, (_, t, _, _) in enumerate(PATTERNS):
            if t == title:
                return i
        return len(PATTERNS)

    def action_for(self, inc: Incident) -> str:
        return self.classify(inc)[2]

    def get(self, incident_id: int) -> Incident | None:
        for inc in self.incidents:
            if inc.id == incident_id:
                return inc
        return None
