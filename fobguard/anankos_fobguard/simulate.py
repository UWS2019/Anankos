"""Synthetic RF scenarios for testing, demos and staff training.

Generates complex baseband IQ containing rolling-code key fob presses,
fixed-code background devices, jammers, replays and code scanning, so the
whole pipeline can be exercised without radios. Nothing here transmits;
it only writes numbers into arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Waveform builders
# ---------------------------------------------------------------------------

def rolling_code_frame_us(code_bits: np.ndarray, te_us: float = 400.0) -> list[tuple[int, float]]:
    """KeeLoq-style PWM frame: preamble, header gap, then 1 bit = short on / long off."""
    seq: list[tuple[int, float]] = []
    for _ in range(12):
        seq += [(1, te_us), (0, te_us)]
    seq[-1] = (0, 10 * te_us)                       # header
    for b in code_bits:
        seq += [(1, te_us), (0, 2 * te_us)] if b else [(1, 2 * te_us), (0, te_us)]
    seq.append((1, te_us))                          # guard pulse
    return seq


def fixed_code_frame_us(code: int, nbits: int = 24, te_us: float = 350.0) -> list[tuple[int, float]]:
    """PT2262/EV1527-style fixed code (doorbells, gates, sensors)."""
    seq: list[tuple[int, float]] = []
    for i in range(nbits):
        b = (code >> (nbits - 1 - i)) & 1
        seq += [(1, 3 * te_us), (0, te_us)] if b else [(1, te_us), (0, 3 * te_us)]
    seq.append((1, te_us))
    return seq


def press_us(frame: list[tuple[int, float]], repeats: int = 3, gap_us: float = 15000.0):
    out: list[tuple[int, float]] = []
    for i in range(repeats):
        out += frame
        if i < repeats - 1:
            out.append((0, gap_us))
    return out


def render_ook(seq, fs: float, f_off: float, amp: float, jitter: float = 0.0,
               rng: np.random.Generator | None = None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    env_parts = []
    for level, dur in seq:
        d = dur * (1 + rng.uniform(-jitter, jitter)) if jitter else dur
        n = max(1, int(round(d * 1e-6 * fs)))
        env_parts.append(np.full(n, float(level), dtype=np.float32))
    env = np.concatenate(env_parts)
    n = np.arange(env.size)
    phase = rng.uniform(0, 2 * np.pi)
    return (amp * env * np.exp(1j * (2 * np.pi * f_off * n / fs + phase))).astype(np.complex64)


def render_fsk(seq, fs: float, f_off: float, amp: float, dev_hz: float = 25e3) -> np.ndarray:
    freqs = []
    for level, dur in seq:
        n = max(1, int(round(dur * 1e-6 * fs)))
        freqs.append(np.full(n, f_off + (dev_hz if level else -dev_hz), dtype=np.float64))
    f = np.concatenate(freqs)
    ph = 2 * np.pi * np.cumsum(f) / fs
    return (amp * np.exp(1j * ph)).astype(np.complex64)


# ---------------------------------------------------------------------------
# Scenario timeline
# ---------------------------------------------------------------------------

@dataclass
class _Emission:
    t: float
    iq: np.ndarray
    label: str


@dataclass
class _Jam:
    start: float
    end: float
    f_off: float
    amp: float
    bw_hz: float = 20e3


@dataclass
class Scenario:
    name: str
    duration_s: float
    fs: float = 1.024e6
    noise_amp: float = 0.002
    start_time: float = 0.0          # unix time of sample 0
    emissions: list[_Emission] = field(default_factory=list)
    jams: list[_Jam] = field(default_factory=list)
    truth: list[str] = field(default_factory=list)
    seed: int = 7

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)

    # builders --------------------------------------------------------------
    def fob_press(self, t: float, code_bits: np.ndarray | None = None, f_off: float = 60e3,
                  amp: float = 0.2, label: str = "fob press", jitter: float = 0.0) -> np.ndarray:
        if code_bits is None:
            code_bits = self.rng.integers(0, 2, 66)
        iq = render_ook(press_us(rolling_code_frame_us(code_bits)), self.fs, f_off, amp,
                        jitter=jitter, rng=self.rng)
        self.emissions.append(_Emission(t, iq, label))
        return code_bits

    def fixed_device(self, t: float, code: int, f_off: float = -150e3, amp: float = 0.05,
                     label: str = "fixed-code device"):
        iq = render_ook(press_us(fixed_code_frame_us(code), repeats=4, gap_us=10000), self.fs,
                        f_off, amp, rng=self.rng)
        self.emissions.append(_Emission(t, iq, label))

    def fsk_fob_press(self, t: float, code_bits=None, f_off: float = 80e3, amp: float = 0.2):
        if code_bits is None:
            code_bits = self.rng.integers(0, 2, 80)
        seq = []
        for _ in range(16):
            seq += [(1, 250.0), (0, 250.0)]
        for b in code_bits:               # Manchester
            seq += [(1, 250.0), (0, 250.0)] if b else [(0, 250.0), (1, 250.0)]
        iq = render_fsk(press_us(seq, repeats=3, gap_us=12000), self.fs, f_off, amp)
        self.emissions.append(_Emission(t, iq, "fsk fob press"))
        return code_bits

    def jam(self, start: float, end: float, f_off: float = 140e3, amp: float = 0.3, bw_hz: float = 20e3):
        self.jams.append(_Jam(start, end, f_off, amp, bw_hz))

    # rendering ---------------------------------------------------------------
    def render(self) -> np.ndarray:
        n = int(self.duration_s * self.fs)
        rng = self.rng
        x = (self.noise_amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)).astype(np.complex64)
        for j in self.jams:
            s0, s1 = int(j.start * self.fs), min(n, int(j.end * self.fs))
            if s1 <= s0:
                continue
            m = s1 - s0
            # narrow-band FM noise jammer
            fm = np.cumsum(rng.standard_normal(m))
            fm = fm - np.convolve(fm, np.ones(256) / 256, mode="same")
            fm = fm / (np.std(fm) + 1e-9) * j.bw_hz / 2
            ph = 2 * np.pi * np.cumsum(j.f_off + fm) / self.fs
            x[s0:s1] += (j.amp * np.exp(1j * ph)).astype(np.complex64)
        for e in self.emissions:
            s0 = int(e.t * self.fs)
            if s0 >= n:
                continue
            s1 = min(n, s0 + e.iq.size)
            x[s0:s1] += e.iq[: s1 - s0]
        return x

    def blocks(self, block_size: int = 131072):
        x = self.render()
        for i in range(0, x.size - block_size + 1, block_size):
            yield self.start_time + i / self.fs, x[i:i + block_size]


# ---------------------------------------------------------------------------
# Canned scenarios
# ---------------------------------------------------------------------------

def scenario_normal(start_time: float = 0.0, fs: float = 1.024e6, seed: int = 7) -> Scenario:
    """Staff locking/unlocking cars plus a gate remote: should raise nothing."""
    s = Scenario("normal", 30.0, fs=fs, start_time=start_time, seed=seed)
    for t in (6.0, 6.9, 12.0, 18.5, 24.0):         # double-press at 6.0/6.9 is normal
        s.fob_press(t)
    s.fixed_device(9.0, 0xA5C31)
    s.fsk_fob_press(15.0)
    s.truth.append("no alerts")
    return s


def scenario_rolljam(start_time: float = 0.0, fs: float = 1.024e6, seed: int = 7) -> Scenario:
    """Jam the channel, capture the owner's code(s), replay one later."""
    s = Scenario("rolljam", 32.0, fs=fs, start_time=start_time, seed=seed)
    s.jam(6.0, 18.0)
    c1 = s.fob_press(9.0, label="owner press #1 (jammed)")
    s.fob_press(13.0, label="owner press #2 (jammed)")
    s.fob_press(26.0, code_bits=c1, f_off=62e3, amp=0.12, jitter=0.02, label="attacker replay of #1")
    s.truth += ["jamming", "fob_during_jam", "replay", "RollJam"]
    return s


def scenario_replay(start_time: float = 0.0, fs: float = 1.024e6, seed: int = 7) -> Scenario:
    """Captured code replayed later (no jamming observed)."""
    s = Scenario("replay", 25.0, fs=fs, start_time=start_time, seed=seed)
    c = s.fob_press(6.0)
    s.fob_press(19.0, code_bits=c, f_off=58e3, amp=0.1, jitter=0.02, label="replay")
    s.truth.append("replay")
    return s


def scenario_rollback(start_time: float = 0.0, fs: float = 1.024e6, seed: int = 7) -> Scenario:
    """RollBack: two consecutive captured codes replayed in order."""
    s = Scenario("rollback", 30.0, fs=fs, start_time=start_time, seed=seed)
    c1 = s.fob_press(6.0)
    c2 = s.fob_press(9.0)
    s.fob_press(22.0, code_bits=c1, jitter=0.02, amp=0.1)
    s.fob_press(23.5, code_bits=c2, jitter=0.02, amp=0.1)
    s.truth += ["replay", "rollback"]
    return s


def scenario_bruteforce(start_time: float = 0.0, fs: float = 1.024e6, seed: int = 7) -> Scenario:
    """Rapid code scanning: many distinct fob-like codes per second."""
    s = Scenario("bruteforce", 30.0, fs=fs, start_time=start_time, seed=seed)
    t = 6.0
    while t < 28.0:
        iq = render_ook(rolling_code_frame_us(s.rng.integers(0, 2, 66)), fs, 60e3, 0.15, rng=s.rng)
        s.emissions.append(_Emission(t, iq, "scan"))
        t += 0.12
    s.truth.append("bruteforce")
    return s


def scenario_relay(start_time: float = 0.0, fs: float = 1.024e6, seed: int = 7) -> Scenario:
    """After-hours: a stored key in the key room starts answering (relay)."""
    s = Scenario("relay", 25.0, fs=fs, start_time=start_time, seed=seed)
    for t in (7.0, 9.5, 12.0, 14.5):
        s.fob_press(t, amp=0.5)                  # strong: sensor is next to key storage
    s.truth += ["after_hours_activity", "stored_key_responding", "relay"]
    return s


SCENARIOS = {
    "normal": scenario_normal,
    "rolljam": scenario_rolljam,
    "replay": scenario_replay,
    "rollback": scenario_rollback,
    "bruteforce": scenario_bruteforce,
    "relay": scenario_relay,
}
