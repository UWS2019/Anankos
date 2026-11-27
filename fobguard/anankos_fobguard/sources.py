"""IQ sample sources. All of them are receive-only.

* ``rtlsdr`` - RTL2832U dongles via ``pyrtlsdr`` (cheap; one per band).
* ``soapy``  - anything SoapySDR supports (Airspy, HackRF in RX, SDRplay, LimeSDR...).
* ``file``   - raw recordings (rtl_sdr .cu8, .cs16, .cf32) for forensics / tuning.
* ``sim``    - synthetic scenarios from :mod:`anankos_fobguard.simulate`.

Every source yields ``(t0, iq)`` where ``t0`` is the unix time of the first
sample and ``iq`` is complex64 normalised to roughly +/-1.0 full scale.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Iterator

import numpy as np

from .config import MonitorConfig


class SourceError(RuntimeError):
    pass


class RtlSdrSource:
    def __init__(self, cfg: MonitorConfig):
        try:
            from rtlsdr import RtlSdr  # type: ignore
        except ImportError as e:  # pragma: no cover - hardware path
            raise SourceError("pyrtlsdr not installed: pip install 'anankos-fobguard[rtlsdr]'") from e
        dev = cfg.device
        if dev.isdigit():
            self.sdr = RtlSdr(device_index=int(dev))
        else:
            self.sdr = RtlSdr(serial_number=dev)
        self.sdr.sample_rate = cfg.sample_rate
        self.sdr.center_freq = cfg.center_freq
        if cfg.ppm:
            self.sdr.freq_correction = cfg.ppm
        self.sdr.gain = "auto" if str(cfg.gain) == "auto" else float(cfg.gain)
        self.block = cfg.block_size
        self.fs = float(self.sdr.sample_rate)

    def __iter__(self) -> Iterator[tuple[float, np.ndarray]]:  # pragma: no cover - hardware
        n = 0
        t_start = time.time()
        try:
            while True:
                iq = np.asarray(self.sdr.read_samples(self.block), dtype=np.complex64)
                yield t_start + n / self.fs, iq
                n += iq.size
                # Re-anchor if we dropped samples (USB hiccup) so timestamps stay honest.
                drift = time.time() - (t_start + n / self.fs)
                if drift > 0.5:
                    t_start += drift
        finally:
            self.sdr.close()


class SoapySource:
    def __init__(self, cfg: MonitorConfig):
        try:
            import SoapySDR  # type: ignore
            from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise SourceError("SoapySDR python bindings not installed") from e
        self._soapy = SoapySDR
        self.dev = SoapySDR.Device(cfg.device)
        self.dev.setSampleRate(SOAPY_SDR_RX, 0, cfg.sample_rate)
        self.dev.setFrequency(SOAPY_SDR_RX, 0, cfg.center_freq)
        if str(cfg.gain) == "auto":
            self.dev.setGainMode(SOAPY_SDR_RX, 0, True)
        else:
            self.dev.setGain(SOAPY_SDR_RX, 0, float(cfg.gain))
        self.stream = self.dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        self.block = cfg.block_size
        self.fs = float(cfg.sample_rate)

    def __iter__(self):  # pragma: no cover - hardware
        self.dev.activateStream(self.stream)
        buf = np.empty(self.block, np.complex64)
        n = 0
        t_start = time.time()
        try:
            while True:
                got = 0
                while got < self.block:
                    sr = self.dev.readStream(self.stream, [buf[got:]], self.block - got, timeoutUs=1_000_000)
                    if sr.ret < 0:
                        raise SourceError(f"readStream error {sr.ret}")
                    got += sr.ret
                yield t_start + n / self.fs, buf.copy()
                n += self.block
        finally:
            self.dev.deactivateStream(self.stream)
            self.dev.closeStream(self.stream)


class FileSource:
    def __init__(self, cfg: MonitorConfig, throttle: bool = False):
        self.path = Path(cfg.device)
        if not self.path.exists():
            raise SourceError(f"recording not found: {self.path}")
        fmt = cfg.file_format
        if fmt == "auto":
            ext = self.path.suffix.lower().lstrip(".")
            fmt = {"cu8": "cu8", "bin": "cu8", "cs16": "cs16", "cf32": "cf32",
                   "raw": "cf32", "fc32": "cf32", "iq": "cu8"}.get(ext, "cu8")
        self.fmt = fmt
        self.fs = float(cfg.sample_rate)
        self.block = cfg.block_size
        self.throttle = throttle
        if cfg.file_start_time:
            self.t0 = dt.datetime.fromisoformat(cfg.file_start_time).timestamp()
        else:
            self.t0 = self.path.stat().st_mtime

    def _read(self, f, n: int) -> np.ndarray | None:
        if self.fmt == "cu8":
            raw = np.frombuffer(f.read(2 * n), dtype=np.uint8)
            if raw.size < 2:
                return None
            x = (raw.astype(np.float32) - 127.5) / 127.5
        elif self.fmt == "cs16":
            raw = np.frombuffer(f.read(4 * n), dtype=np.int16)
            if raw.size < 2:
                return None
            x = raw.astype(np.float32) / 32768.0
        else:
            x = np.frombuffer(f.read(8 * n), dtype=np.float32)
            if x.size < 2:
                return None
        x = x[: x.size // 2 * 2]
        return (x[0::2] + 1j * x[1::2]).astype(np.complex64)

    def __iter__(self):
        n = 0
        with self.path.open("rb") as f:
            while True:
                iq = self._read(f, self.block)
                if iq is None or iq.size == 0:
                    return
                yield self.t0 + n / self.fs, iq
                n += iq.size
                if self.throttle:
                    time.sleep(iq.size / self.fs)


class SimSource:
    def __init__(self, cfg: MonitorConfig, start_time: float | None = None, throttle: bool = False):
        from .simulate import SCENARIOS

        if cfg.scenario not in SCENARIOS:
            raise SourceError(f"unknown scenario {cfg.scenario!r}; choose from {sorted(SCENARIOS)}")
        import zlib
        seed = zlib.crc32(cfg.name.encode())       # independent RF per simulated sensor
        self.scenario = SCENARIOS[cfg.scenario](start_time=start_time or time.time(), fs=cfg.sample_rate,
                                                seed=seed)
        self.block = cfg.block_size
        self.fs = cfg.sample_rate
        self.throttle = throttle

    def __iter__(self):
        for t0, iq in self.scenario.blocks(self.block):
            yield t0, iq
            if self.throttle:
                time.sleep(iq.size / self.fs)


def open_source(cfg: MonitorConfig, *, throttle: bool = False, start_time: float | None = None):
    if cfg.source == "rtlsdr":
        return RtlSdrSource(cfg)
    if cfg.source == "soapy":
        return SoapySource(cfg)
    if cfg.source == "file":
        return FileSource(cfg, throttle=throttle)
    if cfg.source == "sim":
        return SimSource(cfg, start_time=start_time, throttle=throttle)
    raise SourceError(f"unknown source type {cfg.source!r}")
