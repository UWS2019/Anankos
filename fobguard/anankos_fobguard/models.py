"""Core data types shared across the pipeline."""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Any


class Severity(enum.IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: "str | int | Severity") -> "Severity":
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[str(value).strip().upper()]

    def label(self) -> str:
        return self.name.lower()


@dataclass
class Packet:
    """One demodulated RF frame (e.g. a single key fob code word)."""

    t: float                      # unix time of frame start
    monitor: str
    zone: str
    role: str                     # "uhf" or "lf"
    freq_hz: float
    rssi_dbfs: float
    modulation: str               # "ook" or "fsk"
    unit_us: float                # estimated symbol / pulse unit
    levels0: int                  # level of first element (1 = carrier on / mark)
    symbols: tuple[int, ...]      # run-length durations quantised to units
    repeats: int = 1              # identical frames seen in the same burst
    burst_id: int = 0

    @property
    def length(self) -> int:
        return len(self.symbols)

    @property
    def fingerprint(self) -> str:
        import hashlib

        h = hashlib.sha1(bytes(min(s, 255) for s in self.symbols))
        h.update(self.modulation.encode())
        return h.hexdigest()[:12]

    def summary(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "monitor": self.monitor,
            "zone": self.zone,
            "freq_mhz": round(self.freq_hz / 1e6, 4),
            "rssi_dbfs": round(self.rssi_dbfs, 1),
            "modulation": self.modulation,
            "unit_us": round(self.unit_us, 1),
            "length": self.length,
            "repeats": self.repeats,
            "fingerprint": self.fingerprint,
        }


@dataclass
class JamEvent:
    """Sustained in-band emission (jamming, or a long legitimate transmission)."""

    monitor: str
    zone: str
    start: float
    end: float | None             # None while still ongoing
    freq_lo_hz: float
    freq_hi_hz: float
    peak_excess_db: float
    peak_freq_hz: float = 0.0     # strongest persistent bin (the emitter itself)

    @property
    def duration(self) -> float:
        return (self.end if self.end is not None else self.start) - self.start


_det_ids = itertools.count(1)


@dataclass
class Detection:
    kind: str
    severity: Severity
    t: float
    monitor: str
    zone: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_det_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity.label(),
            "t": self.t,
            "monitor": self.monitor,
            "zone": self.zone,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class Incident:
    id: int
    opened: float
    updated: float
    severity: Severity
    title: str
    zone: str
    detections: list[Detection] = field(default_factory=list)
    acknowledged_by: str | None = None
    acknowledged_at: float | None = None
    alerted_severity: Severity | None = None

    @property
    def kinds(self) -> set[str]:
        return {d.kind for d in self.detections}

    def to_dict(self, with_detections: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "opened": self.opened,
            "updated": self.updated,
            "severity": self.severity.label(),
            "title": self.title,
            "zone": self.zone,
            "kinds": sorted(self.kinds),
            "detection_count": len(self.detections),
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }
        if with_detections:
            out["detections"] = [d.to_dict() for d in self.detections]
        return out
