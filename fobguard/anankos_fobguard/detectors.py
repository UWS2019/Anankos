"""Attack detectors: turn packets and jam events into Detections.

Normal use vs. theft - the signals we rely on:

* A rolling-code fob never legitimately sends the same long code word twice
  except as the 3-5 back-to-back repeats of a single button press. The same
  code seen again *later* is a replay.
* A code that was first heard while the band was jammed, then replayed, is
  the RollJam signature (jam -> capture -> replay the unused code).
* Two or more old codes replayed in order is the RollBack resync pattern.
* People press a fob a few times a minute at most; dozens of distinct,
  structurally identical code words per minute is code scanning/brute force.
* A key sitting in key storage should be silent after hours. If it starts
  answering (especially right after 125 kHz LF wake-up activity), someone
  is relaying its signal to a car outside.
* Fixed-code gadgets (gate remotes, doorbells, sensors) repeat by design;
  they are short frames, learned during calibration, or periodic, and are
  not treated as replays.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .dsp import similarity
from .models import Detection, JamEvent, Packet, Severity


@dataclass
class CodeRecord:
    symbols: tuple[int, ...]
    freq_hz: float
    modulation: str
    first_t: float
    last_t: float
    monitor: str
    seq: int                                  # order of first sighting
    times: list[float] = field(default_factory=list)
    captured_during_jam: bool = False
    benign: str | None = None                 # reason, if known-benign

    def periodic(self, min_occ: int) -> bool:
        if len(self.times) < min_occ:
            return False
        iv = np.diff(self.times)
        return bool(iv.mean() > 5 and iv.std() / iv.mean() < 0.25)


class Baseline:
    """Learned site knowledge: noise floors and benign fixed-code emitters."""

    def __init__(self, floors: dict[str, list[float]] | None = None,
                 fixed_codes: list[dict] | None = None):
        self.floors = floors or {}
        self.fixed_codes = fixed_codes or []

    def is_fixed_code(self, p: Packet, thr: float) -> bool:
        for fc in self.fixed_codes:
            if abs(fc["freq_hz"] - p.freq_hz) < 200e3 and similarity(tuple(fc["symbols"]), p.symbols) >= thr:
                return True
        return False

    def to_json(self) -> dict:
        return {"version": 1, "floors": self.floors, "fixed_codes": self.fixed_codes}

    @classmethod
    def from_json(cls, data: dict) -> "Baseline":
        return cls(data.get("floors", {}), data.get("fixed_codes", []))


class DetectorSuite:
    ROLLING_MIN_SYMBOLS = 100     # >= ~50 bits: rolling-code class frames

    def __init__(self, cfg: Config, baseline: Baseline | None = None):
        self.cfg = cfg
        self.d = cfg.detection
        self.baseline = baseline or Baseline()
        self.allow = set(cfg.allowlist_fingerprints)
        self.codes: list[CodeRecord] = []
        self._by_len: dict[tuple[str, int], list[CodeRecord]] = {}
        self._seq = 0
        self.jams: list[JamEvent] = []
        self._recent: dict[str, deque[Packet]] = {}     # distinct code reps per monitor
        self._last_emit: dict[tuple[str, str], float] = {}
        self._last_replay: tuple[float, CodeRecord] | None = None
        self._after_hours_count: dict[str, int] = {}

    # -- utilities ----------------------------------------------------------
    def _throttle(self, kind: str, key: str, t: float, every: float) -> bool:
        k = (kind, key)
        last = self._last_emit.get(k)
        if last is not None and t - last < every:
            return True
        self._last_emit[k] = t
        return False

    def jam_near(self, t: float, freq_hz: float | None = None) -> JamEvent | None:
        ctx = self.d.jam_context_seconds
        for j in reversed(self.jams):
            end = j.end if j.end is not None else math.inf
            if j.start - ctx <= t <= end + ctx:
                if freq_hz is None or (j.freq_lo_hz - 1e6 <= freq_hz <= j.freq_hi_hz + 1e6):
                    return j
        return None

    def _prune(self, now: float) -> None:
        horizon = now - self.d.history_hours * 3600
        if self.codes and self.codes[0].first_t < horizon and len(self.codes) % 256 == 0:
            self.codes = [c for c in self.codes if c.last_t >= horizon]
            self._by_len = {}
            for c in self.codes:
                self._by_len.setdefault((c.modulation, len(c.symbols)), []).append(c)
        self.jams = [j for j in self.jams if j.end is None or j.end >= horizon]

    # -- jam events --------------------------------------------------------
    def on_jam(self, ev: JamEvent) -> list[Detection]:
        band = f"{ev.freq_lo_hz / 1e6:.3f}-{ev.freq_hi_hz / 1e6:.3f} MHz"
        if ev.end is None:
            if ev not in self.jams:
                self.jams.append(ev)
            closed = not self.cfg.site.is_open(ev.start)
            return [Detection(
                "jamming", Severity.HIGH, ev.start, ev.monitor, ev.zone,
                f"Key fob band blocked by continuous RF energy ({band}, "
                f"+{ev.peak_excess_db:.0f} dB over noise, {self.d.jam_min_seconds:.0f}s+)."
                + (" Site is closed." if closed else ""),
                {"freq_lo_mhz": ev.freq_lo_hz / 1e6, "freq_hi_mhz": ev.freq_hi_hz / 1e6,
                 "excess_db": round(ev.peak_excess_db, 1), "after_hours": closed},
            )]
        return [Detection(
            "jamming_ended", Severity.INFO, ev.end, ev.monitor, ev.zone,
            f"Band clear again after {ev.duration:.1f}s of jamming ({band}).",
            {"duration_s": round(ev.duration, 1)},
        )]

    # -- packets -----------------------------------------------------------
    def on_packet(self, p: Packet, during_jam: bool = False) -> list[Detection]:
        out: list[Detection] = []
        self._prune(p.t)
        if p.role == "lf":
            return self._lf(p)
        if p.fingerprint in self.allow:
            return out

        jam = self.jam_near(p.t, p.freq_hz)
        # A frame sitting right on the jam's core frequency *is* the sustained
        # emission (e.g. a code scanner), not a fob pressed while jammed.
        on_core = jam is not None and jam.peak_freq_hz and abs(p.freq_hz - jam.peak_freq_hz) < 25e3
        under_jam = bool((during_jam or jam) and not on_core)
        rolling_class = p.length >= self.ROLLING_MIN_SYMBOLS
        closed = not self.cfg.site.is_open(p.t)

        if under_jam and rolling_class and not self._throttle("fob_during_jam", p.monitor, p.t,
                                                               self.d.press_window_seconds):
            out.append(Detection(
                "fob_during_jam", Severity.HIGH, p.t, p.monitor, p.zone,
                f"Key fob pressed while the band is jammed ({p.freq_hz / 1e6:.3f} MHz). "
                "The vehicle may NOT have locked, and the code may have been captured.",
                {"fingerprint": p.fingerprint, "rssi_dbfs": round(p.rssi_dbfs, 1)},
            ))

        out += self._replay(p, under_jam, rolling_class)
        out += self._bruteforce(p)
        if closed:
            out += self._after_hours(p)
        return out

    def _match(self, p: Packet) -> CodeRecord | None:
        tol = self.d.replay_freq_tolerance_khz * 1e3
        best, best_s = None, 0.0
        cands = [r for n in range(p.length - 2, p.length + 3)
                 for r in self._by_len.get((p.modulation, n), ())]
        for rec in cands:
            if abs(rec.freq_hz - p.freq_hz) > tol:
                continue
            s = similarity(rec.symbols, p.symbols)
            if s > best_s:
                best, best_s = rec, s
        return best if best_s >= self.d.replay_similarity else None

    def _replay(self, p: Packet, under_jam: bool, rolling_class: bool) -> list[Detection]:
        rec = self._match(p)
        if rec is None:
            self._seq += 1
            benign = "calibrated fixed-code device" if self.baseline.is_fixed_code(p, self.d.replay_similarity) else None
            rec = CodeRecord(p.symbols, p.freq_hz, p.modulation, p.t, p.t, p.monitor,
                             self._seq, [p.t], under_jam, benign)
            self.codes.append(rec)
            self._by_len.setdefault((p.modulation, p.length), []).append(rec)
            return []

        gap = p.t - rec.last_t
        if gap <= self.d.press_window_seconds:
            rec.last_t = p.t                       # same press (repeat frames / re-press)
            return []
        rec.times.append(p.t)
        rec.last_t = p.t
        if rec.benign is None and rec.periodic(self.d.periodic_min_occurrences):
            rec.benign = "periodic transmitter (sensor)"
        if rec.benign:
            return []
        if not rolling_class and not rec.captured_during_jam:
            # Short fixed-code frame repeating: expected behaviour for gate
            # remotes, doorbells etc. Keep a low-severity trail only.
            if self._throttle("fixed_code_repeat", p.fingerprint, p.t, 3600):
                return []
            return [Detection(
                "fixed_code_repeat", Severity.LOW, p.t, p.monitor, p.zone,
                f"Fixed-code remote/device repeated ({p.length} symbols, {p.freq_hz / 1e6:.3f} MHz). "
                "Fixed-code devices can be cloned; consider replacing them.",
                {"fingerprint": p.fingerprint},
            )]

        dets: list[Detection] = []
        first_age = p.t - rec.first_t
        if rec.captured_during_jam:
            sev, msg = Severity.CRITICAL, (
                f"Code first heard while the band was jammed was transmitted again {first_age:.0f}s later. "
                "This is the RollJam capture-and-replay pattern.")
        else:
            sev, msg = Severity.HIGH, (
                f"A rolling code seen {first_age:.0f}s ago was transmitted again. "
                "Rolling-code fobs never reuse a code: this is a replay.")
        dets.append(Detection(
            "replay", sev, p.t, p.monitor, p.zone, msg,
            {"fingerprint": p.fingerprint, "original_t": rec.first_t, "original_monitor": rec.monitor,
             "occurrences": len(rec.times), "captured_during_jam": rec.captured_during_jam,
             "rssi_dbfs": round(p.rssi_dbfs, 1), "freq_mhz": round(p.freq_hz / 1e6, 4)},
        ))
        prev = self._last_replay
        if prev is not None and p.t - prev[0] < 15 and rec.seq == prev[1].seq + 1:
            dets.append(Detection(
                "rollback", Severity.CRITICAL, p.t, p.monitor, p.zone,
                "Consecutive captured codes replayed in their original order - "
                "rolling-code resynchronisation (RollBack) attack pattern.",
                {"codes": [prev[1].seq, rec.seq]},
            ))
        self._last_replay = (p.t, rec)
        return dets

    def _bruteforce(self, p: Packet) -> list[Detection]:
        win = self.d.bruteforce_window_seconds
        dq = self._recent.setdefault(p.monitor, deque())      # distinct codes only
        while dq and p.t - dq[0].t > win:
            dq.popleft()
        if any(similarity(q.symbols, p.symbols) >= self.d.replay_similarity for q in dq):
            return []
        dq.append(p)
        similar = [q for q in dq if abs(q.length - p.length) <= 2
                   and abs(q.unit_us - p.unit_us) <= 0.15 * max(p.unit_us, 1.0)]
        n = len(similar)
        if n < self.d.bruteforce_distinct_codes:
            return []
        if self._throttle("bruteforce", p.monitor, p.t, win):
            return []
        span = max(1.0, similar[-1].t - similar[0].t)
        return [Detection(
            "bruteforce", Severity.HIGH, p.t, p.monitor, p.zone,
            f"{n} different key-fob-style codes in {span:.0f}s "
            f"({n / span * 60:.0f}/min). No person presses a fob this fast: code scanning / brute force.",
            {"distinct_codes": n, "window_s": round(span, 1), "length": p.length,
             "unit_us": round(p.unit_us)},
        )]

    def _after_hours(self, p: Packet) -> list[Detection]:
        if p.zone == "key_storage" and p.rssi_dbfs >= self.d.key_storage_rssi_dbfs:
            if self._throttle("stored_key_responding", p.monitor, p.t, 30):
                return []
            return [Detection(
                "stored_key_responding", Severity.HIGH, p.t, p.monitor, p.zone,
                f"A key in key storage transmitted after hours (RSSI {p.rssi_dbfs:.0f} dBFS). "
                "Keys only answer when a car (or a relay device) wakes them: possible relay attack.",
                {"rssi_dbfs": round(p.rssi_dbfs, 1), "fingerprint": p.fingerprint},
            )]
        n = self._after_hours_count[p.monitor] = self._after_hours_count.get(p.monitor, 0) + 1
        if self._throttle("after_hours_activity", p.monitor, p.t, 120):
            return []
        return [Detection(
            "after_hours_activity", Severity.MEDIUM, p.t, p.monitor, p.zone,
            f"Key fob activity while the site is closed ({p.freq_hz / 1e6:.3f} MHz, "
            f"RSSI {p.rssi_dbfs:.0f} dBFS).",
            {"count_since_close": n, "fingerprint": p.fingerprint},
        )]

    def _lf(self, p: Packet) -> list[Detection]:
        if self.cfg.site.is_open(p.t):
            return []
        if self._throttle("lf_interrogation", p.monitor, p.t, 30):
            return []
        return [Detection(
            "lf_interrogation", Severity.HIGH, p.t, p.monitor, p.zone,
            "Low-frequency (125 kHz) key wake-up signal detected while closed. Cars only send this "
            "when a door handle is touched; near key storage it indicates a relay device.",
            {"rssi_dbfs": round(p.rssi_dbfs, 1)},
        )]

    # -- sensor health -----------------------------------------------------
    @staticmethod
    def sensor_offline(monitor: str, zone: str, t: float, reason: str) -> Detection:
        return Detection("sensor_offline", Severity.HIGH, t, monitor, zone,
                         f"RF sensor stopped delivering samples: {reason}. Coverage lost "
                         "(unplugged, failed, or tampered with).", {"reason": reason})

    @staticmethod
    def saturation(monitor: str, zone: str, t: float, frac: float) -> Detection:
        return Detection("receiver_saturation", Severity.MEDIUM, t, monitor, zone,
                         f"Receiver overloaded ({frac * 100:.1f}% clipped samples): a very strong "
                         "transmitter is close to the sensor.", {"clipped_fraction": round(frac, 4)})
