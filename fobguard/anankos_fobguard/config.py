"""Configuration loading (TOML)."""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class MonitorConfig:
    name: str
    source: str = "rtlsdr"                 # rtlsdr | soapy | file | sim
    device: str = "0"                      # rtl index/serial, soapy args, or file path
    center_freq: float = 315.25e6          # tune off the fob freq to dodge the DC spike
    sample_rate: float = 1.024e6
    gain: float | str = 40.0               # dB, or "auto"
    ppm: int = 0
    zone: str = "lot"                      # lot | key_storage | showroom | service | ...
    role: str = "uhf"                      # uhf (fob replies) | lf (125 kHz wake-up)
    block_size: int = 131072
    file_format: str = "auto"              # cu8 | cs16 | cf32 | auto
    file_start_time: str | None = None     # ISO time for sample 0 of a recording
    scenario: str = "normal"               # sim source only
    ignore_ranges_mhz: list[list[float]] = field(default_factory=list)


@dataclass
class AntennaConfig:
    """A physical antenna the staff can switch a monitor to.

    Selection happens by *name* from the dashboard; the port / switch command
    come only from this file, so the web UI can never run arbitrary commands.
    """
    name: str
    monitor: str
    port: str | None = None                # receiver antenna port (Soapy port name, "RX" for RTL-SDR)
    switch_command: str | None = None      # external RF switch, e.g. "usbrelay ANT_1=1" (no shell)
    bias_tee: bool = False                 # power an active antenna / LNA over the coax
    band_mhz: list[float] = field(default_factory=list)   # [low, high] the antenna is designed for
    gain_dbi: float | None = None
    location: str = ""
    notes: str = ""

    def covers(self, freq_hz: float) -> bool:
        if len(self.band_mhz) != 2:
            return True
        lo, hi = sorted(self.band_mhz)
        return lo * 1e6 <= freq_hz <= hi * 1e6


@dataclass
class DetectionConfig:
    nfft: int = 512
    burst_threshold_db: float = 12.0
    jam_threshold_db: float = 8.0
    jam_min_seconds: float = 2.0
    jam_duty: float = 0.6
    warmup_seconds: float = 5.0
    press_window_seconds: float = 2.0      # repeats inside this are one button press
    replay_similarity: float = 0.95
    replay_freq_tolerance_khz: float = 150.0
    jam_context_seconds: float = 3.0       # packet "during jam" if this close to one
    bruteforce_window_seconds: float = 60.0
    bruteforce_distinct_codes: int = 25
    key_storage_rssi_dbfs: float = -30.0
    history_hours: float = 24.0
    min_frame_symbols: int = 32
    periodic_min_occurrences: int = 6


@dataclass
class SinkConfig:
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertConfig:
    min_severity: str = "medium"
    cooldown_seconds: float = 300.0
    incident_window_seconds: float = 180.0
    sinks: list[SinkConfig] = field(default_factory=lambda: [SinkConfig("console")])


@dataclass
class DashboardConfig:
    enabled: bool = True
    bind: str = "127.0.0.1"
    port: int = 8742
    token: str = ""                        # if set, required as ?token= or Bearer


@dataclass
class SiteConfig:
    name: str = "Dealership"
    timezone: str = "UTC"
    business_hours: dict[str, str] = field(default_factory=lambda: {
        "mon": "08:00-20:00", "tue": "08:00-20:00", "wed": "08:00-20:00",
        "thu": "08:00-20:00", "fri": "08:00-20:00", "sat": "09:00-18:00", "sun": "closed",
    })
    holidays: list[str] = field(default_factory=list)       # YYYY-MM-DD, treated as closed
    zone_names: dict[str, str] = field(default_factory=dict)  # zone -> words staff use, e.g. "the back lot"

    def tz(self):
        if ZoneInfo is None:
            return dt.timezone.utc
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return dt.timezone.utc

    def is_open(self, t: float) -> bool:
        local = dt.datetime.fromtimestamp(t, self.tz())
        if local.date().isoformat() in self.holidays:
            return False
        spec = self.business_hours.get(DAYS[local.weekday()], "closed").strip().lower()
        if spec in ("", "closed", "none"):
            return False
        if spec in ("24h", "open", "always"):
            return True
        now = local.hour * 60 + local.minute
        for part in spec.split(","):
            a, b = part.strip().split("-")
            ha, ma = map(int, a.split(":"))
            hb, mb = map(int, b.split(":"))
            lo, hi = ha * 60 + ma, hb * 60 + mb
            if lo <= now < hi or (hi < lo and (now >= lo or now < hi)):
                return True
        return False


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    monitors: list[MonitorConfig] = field(default_factory=list)
    antennas: list[AntennaConfig] = field(default_factory=list)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    storage_path: str = "fobguard.db"
    baseline_path: str = "fobguard-baseline.json"
    allowlist_fingerprints: list[str] = field(default_factory=list)


def _build(cls, data: dict[str, Any]):
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """Expand ${VAR}; unset variables become empty strings (never literal)."""
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | os.PathLike) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    raw = _expand_env(raw)  # lets secrets live in env vars: url = "${SLACK_WEBHOOK}"
    cfg = Config()
    site = dict(raw.get("site", {}))
    if site:
        cfg.site = _build(SiteConfig, site)
    cfg.monitors = [_build(MonitorConfig, m) for m in raw.get("monitor", [])]
    cfg.antennas = [_build(AntennaConfig, a) for a in raw.get("antenna", [])]
    if "detection" in raw:
        cfg.detection = _build(DetectionConfig, raw["detection"])
    if "alerts" in raw:
        a = dict(raw["alerts"])
        sinks = [SinkConfig(s.pop("type"), s) for s in (dict(x) for x in a.pop("sink", []))]
        cfg.alerts = _build(AlertConfig, a)
        if sinks:
            cfg.alerts.sinks = sinks
    if "dashboard" in raw:
        cfg.dashboard = _build(DashboardConfig, raw["dashboard"])
    storage = raw.get("storage", {})
    cfg.storage_path = storage.get("path", cfg.storage_path)
    cfg.baseline_path = storage.get("baseline_path", cfg.baseline_path)
    cfg.allowlist_fingerprints = list(raw.get("allowlist", {}).get("fingerprints", []))
    cfg._path = str(path)  # type: ignore[attr-defined]
    names = [m.name for m in cfg.monitors]
    if len(names) != len(set(names)):
        raise ValueError("monitor names must be unique")
    for a in cfg.antennas:
        if a.monitor not in names:
            raise ValueError(f"antenna {a.name!r}: unknown monitor {a.monitor!r}")
        if not a.port and not a.switch_command:
            raise ValueError(f"antenna {a.name!r}: needs a port and/or a switch_command")
    if len({(a.monitor, a.name) for a in cfg.antennas}) != len(cfg.antennas):
        raise ValueError("antenna names must be unique per monitor")
    for m in cfg.monitors:
        if m.role not in ("uhf", "lf"):
            raise ValueError(f"monitor {m.name}: role must be 'uhf' or 'lf'")
    return cfg
