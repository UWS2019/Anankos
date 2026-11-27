"""Signal processing: spectral monitoring, jam tracking, burst capture, demod.

Everything here is receive-only analysis of complex baseband IQ samples.

Pipeline per block of IQ:

1. Short FFT frames (``nfft`` samples) -> per-frame, per-bin power in dB.
2. A per-bin noise floor that adapts slowly (fast during warm-up, almost
   frozen upward afterwards so an attacker can't "teach" us a jammer).
3. Bins that stay well above the floor for most of the block are
   *persistent*; contiguous persistent energy lasting ``jam_min_s`` becomes a
   :class:`JamEvent`.
4. Frames with short-lived energy above the floor (outside persistent bins)
   are merged into bursts. Each burst's IQ is channelised, demodulated
   (OOK or 2-FSK) and turned into run-length pulse timings.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from .models import JamEvent

EPS = 1e-20


# ---------------------------------------------------------------------------
# Filters / demodulation
# ---------------------------------------------------------------------------

def lowpass_taps(cutoff_hz: float, fs: float, numtaps: int = 129) -> np.ndarray:
    """Windowed-sinc low-pass FIR."""
    numtaps = max(3, numtaps | 1)
    fc = min(0.499, cutoff_hz / fs)
    n = np.arange(numtaps) - (numtaps - 1) / 2
    h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(numtaps)
    return (h / h.sum()).astype(np.float32)


@dataclass
class Demodulated:
    modulation: str                 # "ook" | "fsk"
    levels0: int
    durations_us: np.ndarray        # run lengths in microseconds
    fs: float
    rssi_dbfs: float = -120.0       # in-channel power while carrier is on


def _smooth_bool(b: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or b.size < width:
        return b
    k = np.ones(width, dtype=np.float32) / width
    return np.convolve(b.astype(np.float32), k, mode="same") > 0.5


def _runs(b: np.ndarray) -> tuple[int, np.ndarray]:
    if b.size == 0:
        return 0, np.zeros(0, dtype=np.int64)
    edges = np.flatnonzero(np.diff(b.astype(np.int8))) + 1
    bounds = np.concatenate(([0], edges, [b.size]))
    return int(b[0]), np.diff(bounds)


def demodulate(iq: np.ndarray, fs: float, f_off: float, chan_bw: float = 80e3,
               target_fs: float = 250e3) -> Demodulated | None:
    """Channelise a burst around ``f_off`` and slice it into pulse timings."""
    if iq.size < 64:
        return None
    n = np.arange(iq.size)
    x = iq * np.exp(-2j * np.pi * f_off * n / fs).astype(np.complex64)
    x = np.convolve(x, lowpass_taps(chan_bw / 2, fs), mode="same")
    dec = max(1, int(fs // target_fs))
    x = x[::dec]
    fs2 = fs / dec
    env = np.abs(x)
    if env.size < 32:
        return None
    lo, hi = np.percentile(env, [10, 98])
    if hi <= 0:
        return None
    # Low contrast means the carrier is on nearly the whole window (2-FSK);
    # then "on" is simply anything well above zero.
    thr = 0.3 * hi if (hi - lo) / hi < 0.5 else (lo + hi) / 2
    on = env > thr
    idx = np.flatnonzero(on)
    if idx.size < 16:
        return None
    first, last = idx[0], idx[-1] + 1
    rssi = float(10 * np.log10(np.mean(env[on] ** 2) + EPS))
    smooth = max(3, int(fs2 * 12e-6) | 1)  # odd width, ~12 us de-glitch
    on_s = _smooth_bool(on[first:last], smooth)
    _, env_runs = _runs(on_s)
    on_time_ms = on_s.sum() / fs2 * 1e3

    if env_runs.size / 2 < max(6, on_time_ms / 5):
        # Few envelope transitions for the on-time -> constant envelope (2-FSK).
        seg = x[first:last]
        inst = np.angle(seg[1:] * np.conj(seg[:-1])) * fs2 / (2 * np.pi)
        inst = np.append(inst, inst[-1:])
        k = max(1, int(fs2 * 6e-6))
        inst = np.convolve(inst, np.ones(k) / k, mode="same")
        p5, p95 = np.percentile(inst[on_s], [5, 95])
        if p95 - p5 < 4e3:          # no real deviation -> plain carrier
            return None
        # carrier-off gaps become long "space" runs so frames split there
        bits = _smooth_bool((inst > (p5 + p95) / 2) & on_s, smooth)
        lvl0, runs = _runs(bits)
        mod = "fsk"
    else:
        bits = _smooth_bool(on[first:last], smooth)
        lvl0, runs = _runs(bits)
        mod = "ook"
    if runs.size < 8:
        return None
    return Demodulated(mod, lvl0, runs * (1e6 / fs2), fs2, rssi)


# ---------------------------------------------------------------------------
# Pulse timings -> quantised frames ("packets")
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    levels0: int
    symbols: tuple[int, ...]
    unit_us: float
    start_us: float


def estimate_unit(durations_us: np.ndarray) -> float:
    d = durations_us[durations_us > 0]
    if d.size == 0:
        return 0.0
    short = np.percentile(d, 15)
    cand = d[(d >= 0.6 * short) & (d <= 1.6 * short)]
    return float(np.median(cand)) if cand.size else float(short)


def split_frames(demod: Demodulated, min_symbols: int = 32,
                 gap_units: float = 12.0, gap_min_us: float = 3000.0,
                 max_quant: int = 64, min_unit_us: float = 40.0,
                 max_symbols: int = 2000, max_timing_error: float = 0.17) -> list[Frame]:
    d = demod.durations_us
    unit = estimate_unit(d)
    if unit < min_unit_us:          # noise / garbage, not a remote-control frame
        return []
    gap = max(gap_units * unit, gap_min_us)
    frames: list[Frame] = []
    level = demod.levels0
    cur: list[float] = []
    cur_lvl0 = level
    t = 0.0
    cur_start = 0.0

    def flush():
        # drop first/last element: edges of a frame are least reliable
        body = cur[1:-1] if len(cur) > 4 else cur
        if min_symbols <= len(body) <= max_symbols:
            r = np.asarray(body) / unit
            # Real encoders use integer multiples of a base pulse; noise doesn't.
            small = r < 8
            if small.any() and np.abs(r[small] - np.round(r[small])).mean() > max_timing_error:
                return
            q = tuple(int(min(max_quant, max(1, round(v / unit)))) for v in body)
            frames.append(Frame(1 - cur_lvl0 if len(cur) > 4 else cur_lvl0, q, unit, cur_start))

    for dur in d:
        if level == 0 and dur >= gap:
            flush()
            cur = []
            cur_lvl0 = 1
            cur_start = t + dur
        else:
            if not cur:
                cur_lvl0 = level
                cur_start = t
            cur.append(float(dur))
        t += dur
        level ^= 1
    flush()
    return frames


@lru_cache(maxsize=8192)
def _arr(a: tuple[int, ...]) -> np.ndarray:
    return np.asarray(a, dtype=np.float32)


def similarity(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Fraction of run-length symbols that agree (with timing tolerance)."""
    if not a or not b or abs(len(a) - len(b)) > 2:
        return 0.0
    if a == b:
        return 1.0
    n = min(len(a), len(b))
    aa = _arr(a)[:n]
    bb = _arr(b)[:n]
    tol = np.maximum(0.6, 0.2 * np.maximum(aa, bb))
    ok = np.abs(aa - bb) <= tol
    return float(ok.sum()) / max(len(a), len(b))


# ---------------------------------------------------------------------------
# Spectrum monitor
# ---------------------------------------------------------------------------

@dataclass
class Burst:
    t: float
    start_sample: int
    iq: np.ndarray
    f_off: float
    rssi_dbfs: float
    peak_excess_db: float
    during_jam: bool
    continuous: bool = False        # decoded from a sustained emission (jam / data stream)


@dataclass
class MonitorStats:
    blocks: int = 0
    noise_floor_db: float = float("nan")
    jam_active: bool = False
    bursts: int = 0
    saturation: float = 0.0
    last_block_t: float = 0.0


@dataclass
class _Pending:
    start_f: int
    end_f: int
    bins: list = field(default_factory=list)
    excess: list = field(default_factory=list)
    fidx: list = field(default_factory=list)


class SpectrumMonitor:
    def __init__(self, name: str, zone: str, sample_rate: float, center_freq: float,
                 nfft: int = 512, burst_db: float = 12.0, jam_db: float = 8.0,
                 jam_duty: float = 0.6, jam_min_s: float = 2.0, jam_tau_s: float = 0.75,
                 warmup_s: float = 5.0, burst_gap_s: float = 0.03,
                 max_burst_s: float = 1.5, edge_frac: float = 0.1,
                 dc_bins: int = 2, ignore_ranges_hz: list[tuple[float, float]] | None = None):
        self.name = name
        self.zone = zone
        self.fs = float(sample_rate)
        self.fc = float(center_freq)
        self.nfft = nfft
        self.burst_db = burst_db
        self.jam_db = jam_db
        self.jam_duty = jam_duty
        self.jam_min_s = jam_min_s
        self.warmup_s = warmup_s
        self.gap_frames = max(1, int(burst_gap_s * self.fs / nfft))
        self.max_burst_frames = int(max_burst_s * self.fs / nfft)
        self.frame_s = nfft / self.fs
        self.jam_tau_s = jam_tau_s
        # 4-term Blackman-Harris: ~92 dB sidelobes so a strong jammer doesn't
        # smear across the band and hide the fob it sits next to.
        k = np.arange(nfft) / (nfft - 1)
        self.window = (0.35875 - 0.48829 * np.cos(2 * np.pi * k) + 0.14128 * np.cos(4 * np.pi * k)
                       - 0.01168 * np.cos(6 * np.pi * k)).astype(np.float32)
        self.win_gain = float((self.window ** 2).sum())
        self.bin_freqs = (np.arange(nfft) - nfft // 2) * (self.fs / nfft)

        valid = np.ones(nfft, dtype=bool)
        edge = int(nfft * edge_frac / 2)
        valid[:edge] = False
        valid[nfft - edge:] = False
        valid[nfft // 2 - dc_bins: nfft // 2 + dc_bins + 1] = False
        for lo, hi in ignore_ranges_hz or []:
            abs_f = self.fc + self.bin_freqs
            valid[(abs_f >= lo) & (abs_f <= hi)] = False
        self.valid = valid

        self.floor: np.ndarray | None = None
        self.duty_ema = np.zeros(nfft, dtype=np.float32)
        self.level_ref: np.ndarray | None = None      # slow p90 level, for jammed bins
        self._raw_since: float | None = None
        self._core_freq: float | None = None     # offset of the sustained emitter
        self._warm_until: float = 0.0
        self.stats = MonitorStats()
        self._t_start: float | None = None
        self._frame_cursor = 0          # global frame index of next block start
        self._hist: deque[tuple[int, np.ndarray]] = deque()
        self._pending: _Pending | None = None
        self._jam_since: float | None = None
        self._jam_event: JamEvent | None = None
        self._jam_last_seen: float = 0.0

    # -- persistence -------------------------------------------------------
    def export_floor(self) -> list[float] | None:
        return None if self.floor is None else self.floor.astype(float).round(2).tolist()

    def import_floor(self, values: list[float]) -> None:
        arr = np.asarray(values, dtype=np.float32)
        if arr.size == self.nfft:
            self.floor = arr
            self._preloaded = True

    def rewarm(self, t: float | None = None) -> None:
        """Re-learn the RF environment (after an antenna/gain change). Jam and
        burst detection pause for ``warmup_s`` while the floor re-settles.
        With ``t=None`` the pause is anchored to the next sample block, so it
        always follows the stream's own clock."""
        self._rewarm_next = t is None
        self._warm_until = (t + self.warmup_s) if t is not None else float("inf")
        self._preloaded = False
        self.duty_ema[:] = 0
        self.level_ref = None
        self._pending = None
        self._jam_event = None
        self._jam_since = None
        self._raw_since = None
        self._core_freq = None
        self.stats.jam_active = False

    @property
    def warming(self) -> bool:
        t = self.stats.last_block_t
        initial = self._t_start is not None and (t - self._t_start) < self.warmup_s \
            and not getattr(self, "_preloaded", False)
        return initial or t < self._warm_until

    # -- main entry --------------------------------------------------------
    def process(self, iq: np.ndarray, t0: float) -> tuple[list[Burst], list[JamEvent]]:
        if self._t_start is None:
            self._t_start = t0
        if getattr(self, "_rewarm_next", False):
            self._rewarm_next = False
            self._warm_until = t0 + self.warmup_s
        nframes = iq.size // self.nfft
        if nframes == 0:
            return [], []
        iq = iq[: nframes * self.nfft].astype(np.complex64, copy=False)
        block_start_frame = self._frame_cursor
        self._frame_cursor += nframes
        self._hist.append((block_start_frame * self.nfft, iq))
        self.stats.blocks += 1
        self.stats.last_block_t = t0
        self.stats.saturation = float(np.mean(np.maximum(np.abs(iq.real), np.abs(iq.imag)) > 0.98))

        frames = iq.reshape(nframes, self.nfft) * self.window
        spec = np.fft.fftshift(np.fft.fft(frames, axis=1), axes=1)
        pw = np.abs(spec) ** 2 / self.win_gain
        # 3-bin smoothing tames per-bin noise variance (fob signals span many bins)
        pw = (pw + np.roll(pw, 1, axis=1) + np.roll(pw, -1, axis=1)) / 3
        pdb = 10 * np.log10(pw + EPS).astype(np.float32)

        warm = ((t0 - self._t_start) < self.warmup_s and not getattr(self, "_preloaded", False)) \
            or t0 < self._warm_until
        blk_floor = np.median(pdb, axis=0)
        if self.floor is None:
            self.floor = blk_floor.astype(np.float32)

        excess = pdb - self.floor
        duty = (excess > self.jam_db).mean(axis=0)
        blk_s = nframes * self.frame_s
        a = 1.0 - np.exp(-blk_s / self.jam_tau_s)
        self.duty_ema = ((1 - a) * self.duty_ema + a * duty).astype(np.float32)
        # occupied over ~a second (fob presses are far shorter than that)
        persistent = (self.duty_ema >= self.jam_duty) & self.valid
        raw = (duty >= 0.7) & self.valid
        raw_busy = bool(raw.any())
        if raw_busy or persistent.any():
            ri = np.flatnonzero(raw | persistent)
            self._core_freq = float(self.bin_freqs[ri[np.argmax(pdb[:, ri].mean(axis=0))]])
        if raw_busy and self._raw_since is None:
            self._raw_since = t0
        elif not raw_busy and not persistent.any():
            self._raw_since = None

        # floor update (never rises on persistently occupied bins)
        if warm:
            self.floor = (0.6 * self.floor + 0.4 * blk_floor).astype(np.float32)
        else:
            up = blk_floor > self.floor
            alpha = np.where(up, 0.003, 0.05).astype(np.float32)
            alpha[persistent] = 0.0
            self.floor = (self.floor + alpha * (blk_floor - self.floor)).astype(np.float32)
        self.stats.noise_floor_db = float(np.median(self.floor[self.valid]))

        jams = [] if warm else self._track_jam(persistent, excess, t0, nframes)

        # ---- burst detection ----
        # Quiet bins: compare with the noise floor. Jammed bins: compare with
        # the jammer's own slow p90 level, so a fob pressed *next to* a jammer
        # (exactly what RollJam captures) is still seen.
        blk_p90 = np.percentile(pdb, 90, axis=0).astype(np.float32)
        if self.level_ref is None:
            self.level_ref = blk_p90
        else:
            a2 = 1.0 - np.exp(-blk_s / 2.0)
            self.level_ref = ((1 - a2) * self.level_ref + a2 * blk_p90).astype(np.float32)
        busy = self.duty_ema >= 0.15          # jammed, or on the skirts of a jammer
        ref = np.where(busy, np.maximum(self.floor, self.level_ref + 3.0), self.floor)
        ex = np.where(self.valid, pdb - ref, -np.inf)
        peak_bin = ex.argmax(axis=1)
        peak_ex = ex[np.arange(nframes), peak_bin]
        active = np.flatnonzero(peak_ex > self.burst_db) if not warm else np.zeros(0, int)

        bursts: list[Burst] = []
        for fi in active:
            g = block_start_frame + int(fi)
            p = self._pending
            if p is not None and g - p.end_f <= self.gap_frames:
                p.end_f = g
            else:
                if p is not None:
                    bursts += self._finalize(p)
                p = self._pending = _Pending(g, g)
            p.bins.append(int(peak_bin[fi]))
            p.fidx.append(g)
            p.excess.append(float(peak_ex[fi]))
            if p.end_f - p.start_f > self.max_burst_frames:
                # overly long: chop it so a dense stream still gets decoded
                bursts += self._finalize(p)
                self._pending = None

        # Sustained emission: decode it too. A noise jammer yields nothing; a
        # code scanner / brute forcer yields a stream of valid code words.
        if persistent.any() and not warm:
            idx = np.flatnonzero(persistent)
            k = int(idx[np.argmax(self.level_ref[idx])])
            s1 = self._frame_cursor * self.nfft
            s0 = max(0, s1 - 2 * nframes * self.nfft)        # previous + current block
            seg = self._slice(s0, s1)
            if seg.size:
                bursts.append(Burst(t=self._t_of_frame(s0 // self.nfft), start_sample=s0, iq=seg,
                                    f_off=self._centre(seg, float(self.bin_freqs[k])),
                                    rssi_dbfs=-120.0, peak_excess_db=float(excess[:, k].max()),
                                    during_jam=True, continuous=True))

        end_frame = self._frame_cursor
        if self._pending is not None and end_frame - self._pending.end_f > self.gap_frames:
            bursts += self._finalize(self._pending)
            self._pending = None

        # trim IQ history
        keep_from = (self._pending.start_f if self._pending else end_frame - self.gap_frames) - 4
        keep_from = min(keep_from, end_frame - nframes)      # keep one block for stream decode
        keep_from_s = keep_from * self.nfft
        while len(self._hist) > 1 and self._hist[0][0] + self._hist[0][1].size < keep_from_s:
            self._hist.popleft()

        self.stats.bursts += len(bursts)
        return bursts, jams

    # -- helpers -----------------------------------------------------------
    def _t_of_frame(self, f: int) -> float:
        return (self._t_start or 0.0) + f * self.frame_s

    def _slice(self, s0: int, s1: int) -> np.ndarray:
        parts = []
        for start, arr in self._hist:
            a, b = max(s0, start), min(s1, start + arr.size)
            if a < b:
                parts.append(arr[a - start: b - start])
        return np.concatenate(parts) if parts else np.zeros(0, np.complex64)

    def _finalize(self, p: _Pending) -> list[Burst]:
        """Turn a pending activity span into bursts, one per frequency cluster.

        Concurrent emissions (a fob next to a jammer's fluctuating skirt, two
        remotes at once) are separated so each is demodulated on its own
        frequency and time span.
        """
        if len(p.bins) < 4:
            return []
        bins = np.asarray(p.bins)
        fidx = np.asarray(p.fidx)
        w = np.asarray(p.excess)
        order = np.argsort(bins, kind="stable")
        gaps = np.flatnonzero(np.diff(bins[order]) > 6) + 1         # >12 kHz apart
        out: list[Burst] = []
        for grp in np.split(order, gaps):
            if grp.size < 4:
                continue
            b = self._make_burst(int(fidx[grp].min()), int(fidx[grp].max()), bins[grp], w[grp])
            if b is None:
                continue
            # 2-FSK puts its two tones in separate clusters: keep one burst
            dup = next((o for o in out if abs(o.f_off - b.f_off) < 15e3
                        and min(o.start_sample + o.iq.size, b.start_sample + b.iq.size)
                        - max(o.start_sample, b.start_sample) > 0.5 * min(o.iq.size, b.iq.size)), None)
            if dup is None:
                out.append(b)
            elif b.iq.size > dup.iq.size:
                out[out.index(dup)] = b
        return out

    def _make_burst(self, f0: int, f1: int, bins: np.ndarray, w: np.ndarray) -> Burst | None:
        pad = 2 * self.nfft
        s0 = max(0, f0 * self.nfft - pad)
        s1 = (f1 + 1) * self.nfft + pad
        iq = self._slice(s0, s1)
        if iq.size < self.nfft:
            return None
        counts = np.bincount(bins, weights=w, minlength=self.nfft)   # power-weighted mode
        k = int(counts.argmax())
        core = iq[pad: iq.size - pad] if iq.size > 2 * pad else iq
        f_off = self._centre(core, float(self.bin_freqs[k]))
        mag2 = np.abs(core) ** 2
        on = mag2 > np.percentile(mag2, 60)
        rssi = 10 * np.log10(mag2[on].mean() + EPS) if on.any() else -120.0
        t = self._t_of_frame(f0)
        jammed = self._jam_since is not None or self._raw_since is not None or (t - self._jam_last_seen) < 1.0
        if jammed and self._core_freq is not None and abs(f_off - self._core_freq) < 25e3:
            jammed = False          # this burst is part of the sustained emitter itself
        return Burst(t=t, start_sample=s0, iq=iq, f_off=f_off, rssi_dbfs=float(rssi),
                     peak_excess_db=float(w.max()), during_jam=jammed)

    def _centre(self, x: np.ndarray, f_peak: float) -> float:
        """Centre of the occupied band around the peak (handles 2-FSK tones)."""
        n = 1 << int(np.ceil(np.log2(min(max(x.size, 1024), 1 << 18))))
        seg = x[:n] if x.size >= n else np.pad(x, (0, n - x.size))
        X = np.fft.fftshift(np.abs(np.fft.fft(seg * np.hanning(n))) ** 2)
        f = (np.arange(n) - n // 2) * (self.fs / n)
        near = np.abs(f - f_peak) <= 60e3
        if not near.any():
            return f_peak
        Xn = np.where(near, X, 0)
        occ = Xn > Xn.max() * 0.03            # within ~15 dB of the peak
        idx = np.flatnonzero(occ)
        return float((f[idx.min()] + f[idx.max()]) / 2)

    def _track_jam(self, persistent: np.ndarray, excess: np.ndarray, t0: float,
                   nframes: int) -> list[JamEvent]:
        out: list[JamEvent] = []
        t_end = t0 + nframes * self.frame_s
        if persistent.any():
            self._jam_last_seen = t_end
            if self._jam_since is None:
                self._jam_since = self._raw_since if self._raw_since is not None else t0
            idx = np.flatnonzero(persistent)
            lo = self.fc + self.bin_freqs[idx.min()]
            hi = self.fc + self.bin_freqs[idx.max()] + self.fs / self.nfft
            peak = float(np.median(excess[:, idx], axis=0).max())
            if self._jam_event is None and t_end - self._jam_since >= self.jam_min_s:
                core = int(idx[np.argmax(self.level_ref[idx])])
                self._jam_event = JamEvent(self.name, self.zone, self._jam_since, None, lo, hi, peak,
                                           self.fc + float(self.bin_freqs[core]))
                out.append(self._jam_event)
            elif self._jam_event is not None:
                ev = self._jam_event
                ev.freq_lo_hz = min(ev.freq_lo_hz, lo)
                ev.freq_hi_hz = max(ev.freq_hi_hz, hi)
                ev.peak_excess_db = max(ev.peak_excess_db, peak)
        else:
            if self._jam_event is not None:
                self._jam_event.end = self._jam_last_seen
                out.append(self._jam_event)
            self._jam_event = None
            self._jam_since = None
        self.stats.jam_active = self._jam_event is not None
        return out
