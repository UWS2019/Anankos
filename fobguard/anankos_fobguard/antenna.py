"""Antenna selection, health checks and best-antenna sweeps.

What this can and cannot know
-----------------------------
An antenna is a passive piece of metal: there is no chip to read, so no
software can tell *which* antenna is attached. FobGuard instead:

* knows which **receiver** and which **port** is active, and which configured
  antenna profile staff selected (and who selected it, for the audit log);
* switches antennas by changing the receiver port and/or driving an external
  RF switch, and powers active antennas through the bias tee when the
  profile says so;
* **measures** the antenna in place: noise floor vs. the calibrated
  reference, signals heard per minute, received strength, overload. That is
  enough to flag "probably disconnected", "wrong band", "weaker than the
  alternative" or "overloaded";
* can **sweep** every antenna option on a sensor and pick the one that
  hears the most.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import cmp_to_key
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .config import AntennaConfig
from .models import Detection, Severity

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine, MonitorState

log = logging.getLogger("fobguard.antenna")


class AntennaError(ValueError):
    pass


@dataclass
class AntennaStats:
    since: float
    bursts: int = 0
    frames: int = 0
    rssi: deque = field(default_factory=lambda: deque(maxlen=300))
    floors: deque = field(default_factory=lambda: deque(maxlen=120))
    saturation: float = 0.0
    seconds: float = 0.0

    def floor_db(self) -> float | None:
        return float(np.median(self.floors)) if self.floors else None

    def median_rssi(self) -> float | None:
        return float(np.median(self.rssi)) if self.rssi else None

    def summary(self) -> dict:
        mins = max(self.seconds / 60.0, 1e-9)
        return {
            "measured_s": round(self.seconds, 1),
            "signals": self.bursts,
            "frames": self.frames,
            "signals_per_min": round(self.bursts / mins, 1) if self.seconds else None,
            "noise_floor_db": None if self.floor_db() is None else round(self.floor_db(), 1),
            "median_rssi_dbfs": None if self.median_rssi() is None else round(self.median_rssi(), 1),
            "max_rssi_dbfs": round(max(self.rssi), 1) if self.rssi else None,
            "clipped_pct": round(self.saturation * 100, 2),
        }


@dataclass
class Sweep:
    monitor: str
    options: list[str]
    dwell_s: float
    apply: bool
    who: str
    original: str
    idx: int = 0
    phase_start: float | None = None
    results: list[dict] = field(default_factory=list)
    done: bool = False
    best: str | None = None


class AntennaManager:
    def __init__(self, engine: "Engine", state_path: str | None = None):
        self.engine = engine
        self.state_path = Path(state_path) if state_path else None
        self.profiles: dict[str, dict[str, AntennaConfig]] = {}
        for a in engine.cfg.antennas:
            self.profiles.setdefault(a.monitor, {})[a.name] = a
        self.active: dict[str, str] = {}
        self.stats: dict[tuple[str, str], AntennaStats] = {}
        self.sweeps: dict[str, Sweep] = {}
        self.last_sweep: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._load_state()

    # -- persistence -----------------------------------------------------------
    def _load_state(self) -> None:
        saved = {}
        if self.state_path and self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text()).get("active", {})
            except Exception as e:
                log.warning("antenna state unreadable: %s", e)
        for mon in self.engine.monitors:
            choice = saved.get(mon)
            if choice and (choice in self.profiles.get(mon, {}) or choice.startswith("port:")):
                self.active[mon] = choice
            elif self.profiles.get(mon):
                self.active[mon] = next(iter(self.profiles[mon]))     # first profile = default
            else:
                self.active[mon] = ""
            prof = self.profiles.get(mon, {}).get(self.active[mon])
            if prof and prof.switch_command:
                self._run_switch(prof)          # re-assert the RF switch after a reboot

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"active": self.active}, indent=2))
            tmp.replace(self.state_path)
        except OSError as e:
            log.error("could not save antenna state: %s", e)

    # -- options ---------------------------------------------------------------
    def options(self, mon: str) -> list[dict]:
        st = self._mon(mon)
        out = []
        covered_ports = set()
        for p in self.profiles.get(mon, {}).values():
            covered_ports.add(p.port)
            out.append({"id": p.name, "label": p.name, "kind": "profile", "port": p.port,
                        "switch": bool(p.switch_command), "bias_tee": p.bias_tee,
                        "band_mhz": p.band_mhz, "gain_dbi": p.gain_dbi, "location": p.location,
                        "notes": p.notes, "band_ok": p.covers(st.cfg.center_freq)})
        for port in self._ports(st):
            if port not in covered_ports:
                out.append({"id": f"port:{port}", "label": f"Port {port}", "kind": "port", "port": port,
                            "switch": False, "bias_tee": False, "band_ok": True})
        return out

    @staticmethod
    def _ports(st: "MonitorState") -> list[str]:
        src = st.source
        if src is None:
            return []
        try:
            return list(src.antenna_ports())
        except Exception:
            return []

    def _mon(self, mon: str) -> "MonitorState":
        st = self.engine.monitors.get(mon)
        if st is None:
            raise AntennaError(f"unknown monitor {mon!r}")
        return st

    def label(self, mon: str) -> str:
        a = self.active.get(mon, "")
        if a.startswith("port:"):
            return f"Port {a[5:]}"
        if a:
            return a
        st = self.engine.monitors[mon]
        return f"Port {st.source.antenna}" if st.source is not None and getattr(st.source, "antenna", "") else "default"

    # -- switching -------------------------------------------------------------
    def _run_switch(self, prof: AntennaConfig) -> None:
        argv = shlex.split(prof.switch_command or "")
        if not argv:
            return
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise AntennaError(f"RF switch command failed: {e}") from e
        if r.returncode != 0:
            raise AntennaError(f"RF switch command exited {r.returncode}: {r.stderr.strip()[:200]}")

    @staticmethod
    def _now(st: "MonitorState") -> float | None:
        """Stream time of the last block (None before the first block)."""
        return st.last_t or None

    def select(self, mon: str, option: str, who: str = "staff", force: bool = False,
               _reason: str = "manual") -> dict:
        with self._lock:
            st = self._mon(mon)
            prof = self.profiles.get(mon, {}).get(option)
            if prof is None and not option.startswith("port:"):
                raise AntennaError(f"no antenna option {option!r} for {mon}")
            port = prof.port if prof else option[5:]
            if prof and not prof.covers(st.cfg.center_freq) and not force:
                raise AntennaError(f"{prof.name} is built for {prof.band_mhz[0]}-{prof.band_mhz[1]} MHz, "
                                   f"but {mon} listens at {st.cfg.center_freq / 1e6:.2f} MHz")
            ports = self._ports(st)
            if port and ports and port not in ports:
                raise AntennaError(f"receiver for {mon} has no port {port!r} (has: {', '.join(ports)})")
            if prof and prof.bias_tee and st.source is not None and not st.source.supports_bias_tee():
                raise AntennaError(f"{prof.name} needs bias-tee power, which this receiver can't supply")
            if prof and prof.switch_command:
                self._run_switch(prof)
            if st.source is not None:
                st.source.request(antenna=port or None, bias_tee=prof.bias_tee if prof else False
                                  if st.source.supports_bias_tee() else None)
            previous = self.label(mon)
            self.active[mon] = option
            t = self._now(st)
            st.dsp.rewarm(t)
            self.stats[(mon, option)] = AntennaStats(since=t or 0.0)
            t = t or time.time()
            self._save_state()
        det = Detection("antenna_changed", Severity.INFO, t, mon, st.cfg.zone,
                        f"Antenna on {mon} changed from {previous} to {self.label(mon)} by {who[:60]} "
                        f"({_reason}). Detection re-learns the RF environment for "
                        f"{st.dsp.warmup_s:.0f}s.",
                        {"from": previous, "to": self.label(mon), "by": who[:60], "reason": _reason})
        with self.engine._lock:
            self.engine._handle_detection(det)
        return {"monitor": mon, "active": option, "label": self.label(mon)}

    def on_source_opened(self, st: "MonitorState") -> None:
        """Re-apply the selected port/bias after the receiver (re)connects."""
        opt = self.active.get(st.cfg.name, "")
        prof = self.profiles.get(st.cfg.name, {}).get(opt)
        port = prof.port if prof else (opt[5:] if opt.startswith("port:") else None)
        bias = prof.bias_tee if prof else None
        if st.source is not None and (port or bias):
            st.source.request(antenna=port, bias_tee=bias if st.source.supports_bias_tee() else None)

    # -- measurement -----------------------------------------------------------
    def record(self, st: "MonitorState", t0: float, block_s: float, bursts: int, rssis: list[float]) -> None:
        mon = st.cfg.name
        key = (mon, self.active.get(mon, ""))
        s = self.stats.get(key)
        if s is None:
            s = self.stats[key] = AntennaStats(since=t0)
        sw = self.sweeps.get(mon)
        if sw is not None and not sw.done:
            self._tick(sw, t0)          # phase timing includes the re-learn pause
            if self.active.get(mon, "") != key[1]:
                return                  # the sweep just switched; this block belongs to the old antenna
        if st.dsp.warming:
            return
        s.seconds += block_s
        s.bursts += bursts
        s.frames += len(rssis)
        s.rssi.extend(rssis)
        if st.dsp.stats.noise_floor_db == st.dsp.stats.noise_floor_db:
            s.floors.append(st.dsp.stats.noise_floor_db)
        s.saturation = max(s.saturation * 0.99, st.dsp.stats.saturation)

    def reference_floor(self, mon: str) -> float | None:
        base = self.engine.baseline.floors.get(mon)
        if base:
            st = self.engine.monitors[mon]
            return float(np.median(np.asarray(base)[st.dsp.valid]))
        floors = [s.floor_db() for (m, _), s in self.stats.items() if m == mon and s.floor_db() is not None]
        return max(floors) if floors else None

    def assess(self, mon: str) -> dict:
        st = self._mon(mon)
        opt = self.active.get(mon, "")
        s = self.stats.get((mon, opt)) or AntennaStats(since=self._now(st) or 0.0)
        prof = self.profiles.get(mon, {}).get(opt)
        m = s.summary()
        ref = self.reference_floor(mon)
        floor = s.floor_db()
        out = {"monitor": mon, "antenna": self.label(mon), "receiver_port": getattr(st.source, "antenna", ""),
               "bias_tee": getattr(st.source, "bias_tee", False), **m,
               "reference_floor_db": None if ref is None else round(ref, 1)}

        def verdict(code, level, msg):
            out.update(verdict=code, level=level, message=msg)
            return out

        if not st.online:
            return verdict("offline", "bad", f"Receiver offline: {st.error or 'no samples'}")
        if st.dsp.warming:
            return verdict("settling", "info", "Antenna just changed; re-learning the RF environment.")
        if prof and not prof.covers(st.cfg.center_freq):
            return verdict("wrong_band", "bad", f"{prof.name} is designed for {prof.band_mhz} MHz, not "
                                                f"{st.cfg.center_freq / 1e6:.2f} MHz. Expect poor range.")
        if s.saturation > 0.01:
            return verdict("overloaded", "warn", "Receiver is clipping. Lower the gain or move the antenna away "
                                                 "from strong transmitters.")
        if s.seconds < 20:
            return verdict("measuring", "info", f"Measuring ({s.seconds:.0f}s so far). Check again shortly.")
        if floor is not None and ref is not None and floor < ref - 3 and s.bursts == 0:
            return verdict("possibly_disconnected", "bad",
                           f"Noise floor is {ref - floor:.1f} dB below the reference and no signals were heard. "
                           "The antenna or its cable is probably disconnected or broken.")
        others = [x.median_rssi() for (mm, o), x in self.stats.items()
                  if mm == mon and o != opt and len(x.rssi) >= 3]
        med = s.median_rssi()
        if others and med is not None and len(s.rssi) >= 3 and med < max(others) - 10:
            return verdict("weak", "warn", f"Signals arrive {max(others) - med:.0f} dB weaker than with another "
                                           "antenna on this sensor. Check placement and cabling.")
        if s.bursts == 0 and s.seconds >= 600:
            return verdict("quiet", "warn", "No signals in 10+ minutes. Normal on a very quiet site, otherwise "
                                            "check the antenna and cable.")
        return verdict("ok", "ok", "Antenna is receiving normally.")

    # -- sweep -------------------------------------------------------------------
    def start_sweep(self, mon: str, dwell_s: float = 60.0, apply: bool = True, who: str = "staff") -> dict:
        st = self._mon(mon)
        opts = [o["id"] for o in self.options(mon) if o["band_ok"]]
        if len(opts) < 2:
            raise AntennaError(f"{mon} has fewer than two usable antenna options to compare")
        if not 10 <= dwell_s <= 600:
            raise AntennaError("dwell must be 10-600 seconds per antenna")
        sw = Sweep(mon, opts, dwell_s, apply, who, self.active.get(mon, ""))
        self.sweeps[mon] = sw
        self.select(mon, opts[0], who, force=True, _reason="best-antenna sweep")
        sw.phase_start = self._now(st)
        return {"monitor": mon, "options": opts, "dwell_s": dwell_s,
                "eta_s": round(len(opts) * (dwell_s + st.dsp.warmup_s))}

    def _tick(self, sw: Sweep, t0: float) -> None:
        st = self.engine.monitors[sw.monitor]
        if sw.phase_start is None:
            sw.phase_start = t0
        if t0 - sw.phase_start < sw.dwell_s + st.dsp.warmup_s:
            return
        opt = sw.options[sw.idx]
        s = self.stats.get((sw.monitor, opt)) or AntennaStats(since=t0)
        sw.results.append({"antenna": opt, **s.summary()})
        sw.idx += 1
        if sw.idx < len(sw.options):
            self.select(sw.monitor, sw.options[sw.idx], sw.who, force=True, _reason="best-antenna sweep")
            sw.phase_start = t0
            return
        sw.done = True

        for r in sw.results:
            r["snr_db"] = (round(r["median_rssi_dbfs"] - r["noise_floor_db"], 1)
                           if r["median_rssi_dbfs"] is not None and r["noise_floor_db"] is not None else None)

        def better(a, b):
            """-1 if a is the better antenna. Hearing clearly more signals wins
            (range); otherwise the bigger margin over the noise floor wins.
            A one-or-two signal difference in a short dwell is just timing luck,
            and decoded-frame counts are ignored (a weak antenna splits one
            transmission into several noisy frames)."""
            sa, sb = a["signals"], b["signals"]
            if abs(sa - sb) >= max(3, 0.5 * min(sa, sb)):
                return -1 if sa > sb else 1
            na = a["snr_db"] if a["snr_db"] is not None else -999
            nb = b["snr_db"] if b["snr_db"] is not None else -999
            if na != nb:
                return -1 if na > nb else 1
            return -1 if sa > sb else (1 if sb > sa else 0)
        ranked = sorted(sw.results, key=cmp_to_key(better))
        sw.best = ranked[0]["antenna"]
        final = sw.best if sw.apply else sw.original
        if final and final != self.active.get(sw.monitor):
            self.select(sw.monitor, final, sw.who, force=True,
                        _reason="sweep result" if sw.apply else "sweep finished, restored")
        self.last_sweep[sw.monitor] = {"finished": t0, "best": sw.best, "applied": sw.apply,
                                       "ranking": ranked}
        self.engine.emit("sweep", {"monitor": sw.monitor, **self.last_sweep[sw.monitor]})

    def sweep_status(self, mon: str) -> dict | None:
        sw = self.sweeps.get(mon)
        if sw is None:
            return None
        return {"running": not sw.done, "step": sw.idx + (0 if sw.done else 1), "of": len(sw.options),
                "current": None if sw.done else sw.options[sw.idx], "best": sw.best,
                "result": self.last_sweep.get(mon)}

    def snapshot(self, mon: str) -> dict:
        st = self.engine.monitors[mon]
        return {"active": self.active.get(mon, ""), "label": self.label(mon),
                "receiver_port": getattr(st.source, "antenna", "") if st.source is not None else "",
                "bias_tee": getattr(st.source, "bias_tee", False) if st.source is not None else False,
                "control_error": getattr(st.source, "control_error", "") if st.source is not None else "",
                "warming": st.dsp.warming, "sweep": self.sweep_status(mon)}
