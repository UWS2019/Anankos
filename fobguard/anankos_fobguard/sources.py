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


class Controllable:
    """Thread-safe control channel: the dashboard requests a change, the
    reader thread applies it between sample blocks (never mid-read)."""

    def _init_control(self) -> None:
        import threading

        self._ctl_lock = threading.Lock()
        self._pending: dict = {}
        self.antenna: str = ""
        self.bias_tee: bool = False
        self.control_error: str = ""

    def antenna_ports(self) -> list[str]:
        return [self.antenna] if self.antenna else []

    def supports_bias_tee(self) -> bool:
        return False

    def request(self, **settings) -> None:
        with self._ctl_lock:
            self._pending.update({k: v for k, v in settings.items() if v is not None})

    def _apply_pending(self) -> None:
        with self._ctl_lock:
            pend, self._pending = self._pending, {}
        if not pend:
            return
        try:
            self._apply(pend)
            self.control_error = ""
        except Exception as e:      # a bad control request must never stop monitoring
            self.control_error = str(e)

    def _apply(self, pend: dict) -> None:
        if "antenna" in pend and pend["antenna"] != self.antenna:
            if pend["antenna"] not in self.antenna_ports():
                raise SourceError(f"antenna port {pend['antenna']!r} not available")
            self._set_antenna(pend["antenna"])
            self.antenna = pend["antenna"]
        if "bias_tee" in pend and bool(pend["bias_tee"]) != self.bias_tee:
            if not self.supports_bias_tee():
                raise SourceError("this receiver has no software-controlled bias tee")
            self._set_bias_tee(bool(pend["bias_tee"]))
            self.bias_tee = bool(pend["bias_tee"])

    def _set_antenna(self, name: str) -> None:  # pragma: no cover - overridden
        pass

    def _set_bias_tee(self, on: bool) -> None:  # pragma: no cover - overridden
        pass


class RtlSdrSource(Controllable):
    def __init__(self, cfg: MonitorConfig):
        self._init_control()
        self.antenna = "RX"                 # RTL2832U dongles have a single RF input
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
                self._apply_pending()
                iq = np.asarray(self.sdr.read_samples(self.block), dtype=np.complex64)
                yield t_start + n / self.fs, iq
                n += iq.size
                # Re-anchor if we dropped samples (USB hiccup) so timestamps stay honest.
                drift = time.time() - (t_start + n / self.fs)
                if drift > 0.5:
                    t_start += drift
        finally:
            self.sdr.close()

    def antenna_ports(self) -> list[str]:
        return ["RX"]

    def supports_bias_tee(self) -> bool:     # RTL-SDR Blog V3/V4
        return hasattr(self.sdr, "set_bias_tee")

    def _set_bias_tee(self, on: bool) -> None:  # pragma: no cover - hardware
        self.sdr.set_bias_tee(on)


class SoapySource(Controllable):
    def __init__(self, cfg: MonitorConfig):
        self._init_control()
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
        self._rx = SOAPY_SDR_RX
        try:
            self.antenna = self.dev.getAntenna(SOAPY_SDR_RX, 0)
        except Exception:  # pragma: no cover
            self.antenna = ""

    def antenna_ports(self) -> list[str]:  # pragma: no cover - hardware
        try:
            return list(self.dev.listAntennas(self._rx, 0))
        except Exception:
            return [self.antenna] if self.antenna else []

    def _bias_key(self) -> str | None:  # pragma: no cover - hardware
        try:
            keys = {a.key for a in self.dev.getSettingInfo()}
        except Exception:
            return None
        return next((k for k in ("biastee", "bias_tx", "biasT_ctrl", "bias") if k in keys), None)

    def supports_bias_tee(self) -> bool:  # pragma: no cover - hardware
        return self._bias_key() is not None

    def _set_antenna(self, name: str) -> None:  # pragma: no cover - hardware
        self.dev.setAntenna(self._rx, 0, name)

    def _set_bias_tee(self, on: bool) -> None:  # pragma: no cover - hardware
        self.dev.writeSetting(self._bias_key(), "true" if on else "false")

    def __iter__(self):  # pragma: no cover - hardware
        self.dev.activateStream(self.stream)
        buf = np.empty(self.block, np.complex64)
        n = 0
        t_start = time.time()
        try:
            while True:
                self._apply_pending()
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


class FileSource(Controllable):
    def __init__(self, cfg: MonitorConfig, throttle: bool = False):
        self._init_control()
        self.antenna = "Recording"
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


class SimSource(Controllable):
    """Simulated receiver with three simulated antennas, so antenna switching,
    checks and the best-antenna sweep can be demonstrated without hardware."""

    # name -> (signal scale, noise scale)
    PORTS = {"Whip (sim)": (1.0, 1.0), "Weak (sim)": (0.1, 1.0), "Disconnected (sim)": (0.0, 0.5)}

    def __init__(self, cfg: MonitorConfig, start_time: float | None = None, throttle: bool = False):
        self._init_control()
        self.antenna = "Whip (sim)"
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

    def antenna_ports(self) -> list[str]:
        return list(self.PORTS)

    def supports_bias_tee(self) -> bool:
        return True

    def __iter__(self):
        rng = np.random.default_rng(1)
        na = self.scenario.noise_amp
        for t0, iq in self.scenario.blocks(self.block):
            self._apply_pending()
            sig, noise = self.PORTS[self.antenna]
            if (sig, noise) != (1.0, 1.0):
                fresh = (rng.standard_normal(iq.size) + 1j * rng.standard_normal(iq.size)) * (na / np.sqrt(2))
                iq = (sig * iq + noise * fresh).astype(np.complex64)
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
