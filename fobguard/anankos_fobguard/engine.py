"""Pipeline orchestration: sources -> DSP -> packets -> detectors -> incidents -> alerts."""

from __future__ import annotations

import heapq
import itertools
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .alerts import AlertManager
from .config import Config, MonitorConfig
from .correlator import Correlator
from .detectors import Baseline, DetectorSuite
from .dsp import Burst, SpectrumMonitor, demodulate, similarity, split_frames
from .models import Detection, Incident, JamEvent, Packet, Severity
from .sources import open_source
from .store import Store

log = logging.getLogger("fobguard.engine")


def make_monitor(cfg: Config, m: MonitorConfig) -> SpectrumMonitor:
    d = cfg.detection
    return SpectrumMonitor(
        m.name, m.zone, m.sample_rate, m.center_freq, nfft=d.nfft,
        burst_db=d.burst_threshold_db, jam_db=d.jam_threshold_db, jam_duty=d.jam_duty,
        jam_min_s=d.jam_min_seconds, warmup_s=d.warmup_seconds,
        ignore_ranges_hz=[(lo * 1e6, hi * 1e6) for lo, hi in m.ignore_ranges_mhz],
        # LF wake-up bursts are long-ish continuous carriers; allow longer bursts
        max_burst_s=1.5 if m.role == "uhf" else 3.0,
    )


_burst_ids = itertools.count(1)


def burst_to_packets(b: Burst, m: MonitorConfig, center_freq: float, fs: float,
                     min_symbols: int = 32) -> list[Packet]:
    demod = demodulate(b.iq, fs, b.f_off)
    if demod is None:
        if m.role == "lf":
            # LF wake-up patterns are simple; any clean burst counts
            return [Packet(b.t, m.name, m.zone, m.role, center_freq + b.f_off, b.rssi_dbfs,
                           "carrier", 0.0, 1, (), 1, next(_burst_ids))]
        return []
    frames = split_frames(demod, min_symbols=min_symbols if m.role == "uhf" else 4)
    bid = next(_burst_ids)
    uniq: list[tuple] = []          # (frame, count)
    for f in frames:
        for i, (g, c) in enumerate(uniq):
            if similarity(g.symbols, f.symbols) >= 0.9:
                uniq[i] = (g, c + 1)
                break
        else:
            uniq.append((f, 1))
    out = []
    for f, c in uniq:
        out.append(Packet(b.t + f.start_us * 1e-6, m.name, m.zone, m.role, center_freq + b.f_off,
                          demod.rssi_dbfs, demod.modulation, f.unit_us, f.levels0, f.symbols, c, bid))
    if not out and m.role == "lf":
        out.append(Packet(b.t, m.name, m.zone, m.role, center_freq + b.f_off, demod.rssi_dbfs,
                          demod.modulation, 0.0, 1, (), 1, bid))
    return out


@dataclass
class MonitorState:
    cfg: MonitorConfig
    dsp: SpectrumMonitor
    last_t: float = 0.0
    online: bool = True
    error: str = ""
    packets: int = 0
    saturated_since: float | None = None
    stream_recent: list = None  # type: ignore[assignment]
    max_rssi: float = -200.0
    source: object = None


class Engine:
    def __init__(self, cfg: Config, *, store: Store | None = None, alerts: AlertManager | None = None,
                 baseline: Baseline | None = None):
        self.cfg = cfg
        self.store = store
        self.alerts = alerts or AlertManager(cfg)
        self.baseline = baseline if baseline is not None else self._load_baseline()
        self.detectors = DetectorSuite(cfg, self.baseline)
        self.correlator = Correlator(cfg)
        if store is not None:
            self.correlator._ids = itertools.count(store.max_incident_id() + 1)
        self.monitors: dict[str, MonitorState] = {}
        for m in cfg.monitors:
            st = MonitorState(m, make_monitor(cfg, m))
            fl = self.baseline.floors.get(m.name)
            if fl:
                st.dsp.import_floor(fl)
            self.monitors[m.name] = st
        self.listeners: list[Callable[[str, dict], None]] = []
        from .antenna import AntennaManager
        state = Path(cfg.storage_path).parent / "antenna-state.json" if store is not None else None
        self.antennas = AntennaManager(self, str(state) if state else None)
        self.detections: list[Detection] = []
        self.packets_seen = 0
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue(maxsize=10000)
        self.now = 0.0          # stream time of the newest block (live: ~wall clock)
        self._lock = threading.RLock()

    # -- baseline ------------------------------------------------------------
    def _load_baseline(self) -> Baseline:
        p = Path(self.cfg.baseline_path)
        if p.exists():
            try:
                return Baseline.from_json(json.loads(p.read_text()))
            except Exception as e:
                log.warning("could not read baseline %s: %s", p, e)
        return Baseline()

    # -- events --------------------------------------------------------------
    def emit(self, kind: str, data: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, data)
            except Exception:  # pragma: no cover
                log.exception("listener failed")

    def handle_block(self, name: str, t0: float, iq) -> None:
        """Process one IQ block for a monitor (DSP + detection, synchronous)."""
        st = self.monitors[name]
        bursts, jams = st.dsp.process(iq, t0)
        st.last_t = t0
        with self._lock:
            self.now = max(self.now, t0)
            for j in jams:
                self._handle_jam(j)
            rssis: list[float] = []
            for b in bursts:
                pkts = burst_to_packets(b, st.cfg, st.dsp.fc, st.dsp.fs, self.cfg.detection.min_frame_symbols)
                rssis += [p.rssi_dbfs for p in pkts]
                if b.continuous:
                    pkts = self._dedupe_stream(st, pkts)
                for p in pkts:
                    st.packets += 1
                    st.max_rssi = max(st.max_rssi, p.rssi_dbfs)
                    # frames *inside* a sustained emission are the emission itself,
                    # not a fob pressed under jamming
                    self._handle_packet(p, b.during_jam and not b.continuous)
            self._check_saturation(st, t0)
        self.antennas.record(st, t0, iq.size / st.dsp.fs, sum(1 for b in bursts if not b.continuous), rssis)

    def _dedupe_stream(self, st: MonitorState, pkts: list[Packet]) -> list[Packet]:
        """Overlapping stream windows see the same frame twice; keep the first."""
        if st.stream_recent is None:
            st.stream_recent = []
        out = []
        for p in pkts:
            st.stream_recent = [q for q in st.stream_recent if p.t - q.t < 1.0]
            if any(abs(q.t - p.t) < 0.05 and similarity(q.symbols, p.symbols) >= 0.9 for q in st.stream_recent):
                continue
            # also reject frames cut by the window edges: same start, shorter
            st.stream_recent.append(p)
            out.append(p)
        return out

    def _check_saturation(self, st: MonitorState, t: float) -> None:
        sat = st.dsp.stats.saturation
        if sat > 0.02:
            if st.saturated_since is None:
                st.saturated_since = t
            elif t - st.saturated_since > 3 and not self.detectors._throttle("sat", st.cfg.name, t, 600):
                self._handle_detection(DetectorSuite.saturation(st.cfg.name, st.cfg.zone, t, sat))
        else:
            st.saturated_since = None

    def _handle_jam(self, j: JamEvent) -> None:
        for d in self.detectors.on_jam(j):
            self._handle_detection(d)
        self.emit("jam", {"monitor": j.monitor, "start": j.start, "end": j.end,
                          "freq_lo_mhz": j.freq_lo_hz / 1e6, "freq_hi_mhz": j.freq_hi_hz / 1e6})

    def _handle_packet(self, p: Packet, during_jam: bool) -> None:
        self.packets_seen += 1
        if self.store:
            self.store.packet(p)
        self.emit("packet", p.summary())
        for d in self.detectors.on_packet(p, during_jam):
            self._handle_detection(d)

    def _handle_detection(self, d: Detection) -> None:
        self.detections.append(d)
        if len(self.detections) > 5000:
            self.detections = self.detections[-2500:]
        inc, alert = self.correlator.add(d)
        log.info("detection %s %s %s: %s", d.kind, d.severity.name, d.monitor, d.message)
        if self.store:
            if inc:
                self.store.incident(inc)
            self.store.detection(d, inc.id if inc else None)
            self.store.commit()
        self.emit("detection", d.to_dict())
        if inc is not None:
            self.emit("incident", inc.to_dict(with_detections=False))
            if alert:
                # muting is set in wall-clock time by staff, so compare with the wall clock
                self.alerts.dispatch(inc, self.correlator.action_for(inc), time.time())

    def acknowledge(self, incident_id: int, who: str) -> Incident | None:
        with self._lock:
            inc = self.correlator.get(incident_id)
            if inc is None:
                return None
            inc.acknowledged_by = who[:80] or "staff"
            inc.acknowledged_at = time.time()
            if self.store:
                self.store.incident(inc)
                self.store.commit()
            self.emit("incident", inc.to_dict(with_detections=False))
            return inc

    def status(self) -> dict:
        now = time.time()
        mons = []
        for st in self.monitors.values():
            s = st.dsp.stats
            mons.append({
                "name": st.cfg.name, "zone": st.cfg.zone, "role": st.cfg.role,
                "center_mhz": st.cfg.center_freq / 1e6, "online": st.online, "error": st.error,
                "noise_floor_db": None if s.noise_floor_db != s.noise_floor_db else round(s.noise_floor_db, 1),
                "jam_active": s.jam_active, "bursts": s.bursts, "packets": st.packets,
                "last_block_age_s": round(now - st.last_t, 1) if st.last_t else None,
                "antenna": self.antennas.snapshot(st.cfg.name),
            })
        return {
            "site": self.cfg.site.name, "open": self.cfg.site.is_open(now), "monitors": mons,
            "packets_seen": self.packets_seen,
            "suppressed_until": self.alerts.suppressed_until, "suppress_reason": self.alerts.suppress_reason,
            "incidents": [dict(i.to_dict(with_detections=True), action=self.correlator.action_for(i),
                               active=((self.now or time.time()) - i.updated) <= self.correlator.window)
                          for i in self.correlator.incidents[-50:]][::-1],
        }

    # -- offline (files / simulation) ------------------------------------------
    def run_offline(self, start_time: float | None = None) -> None:
        """Interleave all monitors by timestamp, as fast as possible."""
        def tagged(name: str, src):
            for t0, iq in src:
                yield t0, name, iq

        iters = []
        for name, st in self.monitors.items():
            st.source = open_source(st.cfg, start_time=start_time)
            self.antennas.on_source_opened(st)
            iters.append(tagged(name, st.source))
        for t0, name, iq in heapq.merge(*iters, key=lambda x: x[0]):
            self.handle_block(name, t0, iq)
        self.flush()

    def flush(self) -> None:
        if self.store:
            self.store.commit()

    # -- live ------------------------------------------------------------------
    def _reader(self, st: MonitorState, throttle: bool) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                src = open_source(st.cfg, throttle=throttle)
                st.source = src
                self.antennas.on_source_opened(st)
                st.online, st.error = True, ""
                backoff = 2.0
                for t0, iq in src:
                    if self._stop.is_set():
                        return
                    self._q.put((st.cfg.name, t0, iq))
                if st.cfg.source in ("file", "sim"):
                    return
                raise RuntimeError("stream ended")
            except Exception as e:
                if self._stop.is_set():
                    return
                st.online, st.error = False, str(e)
                log.error("monitor %s failed: %s (retry in %.0fs)", st.cfg.name, e, backoff)
                self._q.put(("__offline__", time.time(), (st.cfg.name, str(e))))
                self._stop.wait(backoff)
                backoff = min(60.0, backoff * 2)

    def run_live(self, throttle_files: bool = True) -> None:
        threads = [threading.Thread(target=self._reader, args=(st, throttle_files), daemon=True,
                                    name=f"reader-{n}") for n, st in self.monitors.items()]
        for t in threads:
            t.start()
        last_watchdog = time.time()
        try:
            while not self._stop.is_set():
                try:
                    name, t0, payload = self._q.get(timeout=1.0)
                except queue.Empty:
                    name = None
                if name == "__offline__":
                    mon, reason = payload
                    st = self.monitors[mon]
                    with self._lock:
                        if not self.detectors._throttle("offline", mon, t0, 600):
                            self._handle_detection(DetectorSuite.sensor_offline(mon, st.cfg.zone, t0, reason))
                elif name is not None:
                    self.handle_block(name, t0, payload)
                now = time.time()
                if now - last_watchdog > 5:
                    last_watchdog = now
                    self._watchdog(now)
                if all(not t.is_alive() for t in threads) and self._q.empty():
                    break
        finally:
            self._stop.set()
            self.flush()

    def _watchdog(self, now: float) -> None:
        for st in self.monitors.values():
            if st.cfg.source in ("file", "sim") or not st.last_t:
                continue
            if now - st.last_t > 15 and st.online:
                st.online = False
                with self._lock:
                    if not self.detectors._throttle("offline", st.cfg.name, now, 600):
                        self._handle_detection(DetectorSuite.sensor_offline(
                            st.cfg.name, st.cfg.zone, now, "no samples for 15s"))

    def stop(self) -> None:
        self._stop.set()


def learn_baseline(engine: Engine, packets: Iterable[Packet] | None = None) -> Baseline:
    """Build a baseline from what an engine has observed so far (used by `calibrate`)."""
    floors = {n: st.dsp.export_floor() for n, st in engine.monitors.items() if st.dsp.floor is not None}
    fixed = []
    for rec in engine.detectors.codes:
        if len(rec.times) >= 3 or (len(rec.times) >= 2 and len(rec.symbols) < DetectorSuite.ROLLING_MIN_SYMBOLS):
            fixed.append({"symbols": list(rec.symbols), "freq_hz": rec.freq_hz,
                          "modulation": rec.modulation, "seen": len(rec.times)})
    return Baseline(floors, fixed)
