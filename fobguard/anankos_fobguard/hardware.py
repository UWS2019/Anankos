"""Receiver (SDR) detection.

Antennas are passive and carry no identity, so no software can read which
antenna is attached. What we *can* identify is the receiver (USB vendor /
product / serial), which driver and plugin it needs, and which antenna
ports it exposes. Antenna *performance* is measured separately in
:mod:`anankos_fobguard.antenna`.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

# (vendor, product) -> (model, soapy driver, requirement key for its plugin)
KNOWN_SDRS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("0bda", "2838"): ("RTL2832U (RTL-SDR)", "rtlsdr", "soapy-rtlsdr"),
    ("0bda", "2832"): ("RTL2832U (RTL-SDR)", "rtlsdr", "soapy-rtlsdr"),
    ("1d50", "60a1"): ("Airspy R2 / Mini", "airspy", "soapy-airspy"),
    ("03eb", "800c"): ("Airspy HF+", "airspyhf", "soapy-airspyhf"),
    ("1d50", "6089"): ("HackRF One", "hackrf", "soapy-hackrf"),
    ("1d50", "cc15"): ("HackRF (rad1o)", "hackrf", "soapy-hackrf"),
    ("1df7", "2500"): ("SDRplay RSP1", "sdrplay", "sdrplay-api"),
    ("1df7", "3000"): ("SDRplay RSP1A", "sdrplay", "sdrplay-api"),
    ("1df7", "3010"): ("SDRplay RSP2", "sdrplay", "sdrplay-api"),
    ("1df7", "3020"): ("SDRplay RSPduo", "sdrplay", "sdrplay-api"),
    ("1df7", "3050"): ("SDRplay RSPdx", "sdrplay", "sdrplay-api"),
    ("1df7", "3060"): ("SDRplay RSP1B", "sdrplay", "sdrplay-api"),
    ("0403", "601f"): ("LimeSDR-USB", "lime", "soapy-lms7"),
    ("1d50", "6108"): ("LimeSDR", "lime", "soapy-lms7"),
    ("0403", "6108"): ("LimeSDR Mini", "lime", "soapy-lms7"),
    ("0456", "b673"): ("ADALM-Pluto", "plutosdr", "soapy-plutosdr"),
    ("2500", "0020"): ("Ettus USRP B200/B210", "uhd", "soapy-uhd"),
    ("2500", "0022"): ("Ettus USRP B205mini", "uhd", "soapy-uhd"),
}

# Antenna ports a receiver family exposes (used when the driver can't be asked).
KNOWN_PORTS: dict[str, list[str]] = {
    "rtlsdr": ["RX"],
    "airspy": ["RX"],
    "airspyhf": ["RX"],
    "hackrf": ["TX/RX"],
    "sdrplay": ["Antenna A", "Antenna B", "Antenna C"],   # model dependent
    "lime": ["LNAH", "LNAL", "LNAW"],
    "plutosdr": ["A_BALANCED"],
    "uhd": ["TX/RX", "RX2"],
}


@dataclass
class DeviceInfo:
    model: str
    driver: str
    requirement: str
    vendor_id: str
    product_id: str
    serial: str = ""
    manufacturer: str = ""
    product: str = ""
    bus: str = ""
    devnode: str = ""
    accessible: bool | None = None
    ports: list[str] = field(default_factory=list)
    source: str = "usb"

    def to_dict(self) -> dict:
        return asdict(self)


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def enumerate_usb(sysfs: str = "/sys/bus/usb/devices") -> list[DeviceInfo]:
    """Find known SDRs via Linux sysfs (no extra libraries, no root needed)."""
    root = Path(sysfs)
    out: list[DeviceInfo] = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        vid, pid = _read(d / "idVendor").lower(), _read(d / "idProduct").lower()
        if (vid, pid) not in KNOWN_SDRS:
            continue
        model, driver, req = KNOWN_SDRS[(vid, pid)]
        busnum, devnum = _read(d / "busnum"), _read(d / "devnum")
        devnode = f"/dev/bus/usb/{int(busnum):03d}/{int(devnum):03d}" if busnum and devnum else ""
        acc = os.access(devnode, os.R_OK | os.W_OK) if devnode and os.path.exists(devnode) else None
        out.append(DeviceInfo(model, driver, req, vid, pid, _read(d / "serial"), _read(d / "manufacturer"),
                              _read(d / "product"), d.name, devnode, acc, list(KNOWN_PORTS.get(driver, []))))
    return out


def enumerate_lsusb() -> list[DeviceInfo]:
    """Fallback when sysfs isn't available (non-Linux or containers)."""
    try:
        txt = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in txt.splitlines():
        m = re.search(r"Bus (\d+) Device (\d+): ID ([0-9a-f]{4}):([0-9a-f]{4})\s*(.*)", line, re.I)
        if not m:
            continue
        key = (m.group(3).lower(), m.group(4).lower())
        if key in KNOWN_SDRS:
            model, driver, req = KNOWN_SDRS[key]
            node = f"/dev/bus/usb/{m.group(1)}/{m.group(2)}"
            acc = os.access(node, os.R_OK | os.W_OK) if os.path.exists(node) else None
            out.append(DeviceInfo(model, driver, req, *key, product=m.group(5), bus=f"{m.group(1)}-{m.group(2)}",
                                  devnode=node, accessible=acc, ports=list(KNOWN_PORTS.get(driver, []))))
    return out


def enumerate_soapy() -> list[DeviceInfo]:
    """Ask SoapySDR (if installed) - it also knows network SDRs and real port names."""
    try:
        import SoapySDR  # type: ignore
        from SoapySDR import SOAPY_SDR_RX  # type: ignore
    except Exception:
        return []
    out = []
    try:
        results = SoapySDR.Device.enumerate()
    except Exception:
        return []
    for r in results:
        args = {k: r[k] for k in r.keys()}
        driver = args.get("driver", "?")
        ports: list[str] = []
        try:
            dev = SoapySDR.Device(args)
            ports = list(dev.listAntennas(SOAPY_SDR_RX, 0))
        except Exception:
            ports = list(KNOWN_PORTS.get(driver, []))
        out.append(DeviceInfo(args.get("label", driver), driver, f"soapy-{driver}", "", "",
                              args.get("serial", ""), args.get("manufacturer", ""), args.get("product", ""),
                              ports=ports, source="soapy"))
    return out


def detect_devices(sysfs: str = "/sys/bus/usb/devices") -> list[DeviceInfo]:
    usb = enumerate_usb(sysfs) or enumerate_lsusb()
    soapy = enumerate_soapy()
    # merge Soapy's richer port lists into USB entries with the same serial
    for s in soapy:
        match = next((u for u in usb if s.serial and u.serial == s.serial), None)
        if match:
            match.ports = s.ports or match.ports
        elif not any(u.driver == s.driver for u in usb):
            usb.append(s)
    return usb
