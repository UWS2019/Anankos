"""System doctor: verify everything FobGuard needs and install what's missing.

Safety model
------------
* Only items in :data:`REQUIREMENTS` can ever be installed. Each lists candidate
  package names per package manager; "search" means asking the system's own
  package manager (apt, dnf, pacman, brew) which candidate exists for this OS,
  never downloading from arbitrary places.
* Commands are built as argument lists (no shell), from that allowlist only.
* The web dashboard can only *request* fixes by check ID. A separate root
  helper (systemd path unit) re-runs the checks itself and applies only
  allowlisted fixes for checks that are still failing.
* Vendor/proprietary drivers (SDRplay API) are reported as manual steps.
"""

from __future__ import annotations

import ctypes.util
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config
from .hardware import DeviceInfo, detect_devices

UDEV_RULE_PATH = "/etc/udev/rules.d/99-anankos-rtlsdr.rules"
BLACKLIST_PATH = "/etc/modprobe.d/anankos-rtlsdr-blacklist.conf"
UDEV_RULE = """# Installed by Anankos FobGuard: let plugdev members open SDR receivers.
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="60a1", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="03eb", ATTRS{idProduct}=="800c", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6089", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="601f", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6108", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="b673", GROUP="plugdev", MODE="0660"
"""
BLACKLIST = "blacklist dvb_usb_rtl28xxu\nblacklist rtl2832\nblacklist rtl2830\n"

# key -> description, candidate package names per manager, optional pip spec
REQUIREMENTS: dict[str, dict] = {
    "librtlsdr": {"desc": "RTL-SDR driver library",
                  "apt": ["librtlsdr2", "librtlsdr0"], "dnf": ["rtl-sdr"], "pacman": ["rtl-sdr"],
                  "brew": ["librtlsdr"]},
    "rtl-sdr-tools": {"desc": "RTL-SDR command-line tools (rtl_test, rtl_eeprom)",
                      "apt": ["rtl-sdr"], "dnf": ["rtl-sdr"], "pacman": ["rtl-sdr"], "brew": ["librtlsdr"]},
    # pyrtlsdr 0.4+ calls rtlsdr_set_dithering, missing from stock distro librtlsdr 2.0.x
    "pyrtlsdr": {"desc": "Python bindings for RTL-SDR", "pip": "pyrtlsdr>=0.3,<0.4"},
    "numpy": {"desc": "NumPy signal processing", "pip": "numpy>=1.24"},
    "soapysdr-python": {"desc": "SoapySDR Python bindings (multi-vendor SDR support)",
                        "apt": ["python3-soapysdr"], "dnf": ["python3-SoapySDR"], "pacman": ["soapysdr"],
                        "brew": ["soapysdr"]},
    "soapy-rtlsdr": {"desc": "SoapySDR plugin for RTL-SDR",
                     "apt": ["soapysdr-module-rtlsdr", "soapysdr0.8-module-rtlsdr"],
                     "dnf": ["soapy-rtlsdr"], "pacman": ["soapyrtlsdr"], "brew": ["soapyrtlsdr"]},
    "soapy-airspy": {"desc": "SoapySDR plugin for Airspy R2/Mini",
                     "apt": ["soapysdr-module-airspy", "soapysdr0.8-module-airspy"],
                     "dnf": ["soapy-airspy"], "pacman": ["soapyairspy"], "brew": ["soapyairspy"]},
    "soapy-airspyhf": {"desc": "SoapySDR plugin for Airspy HF+ (LF/HF relay detection)",
                       "apt": ["soapysdr-module-airspyhf", "soapysdr0.8-module-airspyhf"],
                       "dnf": ["soapy-airspyhf"], "pacman": ["soapyairspyhf"], "brew": ["soapyairspyhf"]},
    "soapy-hackrf": {"desc": "SoapySDR plugin for HackRF (receive only)",
                     "apt": ["soapysdr-module-hackrf", "soapysdr0.8-module-hackrf"],
                     "dnf": ["soapy-hackrf"], "pacman": ["soapyhackrf"], "brew": ["soapyhackrf"]},
    "soapy-lms7": {"desc": "SoapySDR plugin for LimeSDR",
                   "apt": ["soapysdr-module-lms7", "soapysdr0.8-module-lms7"],
                   "dnf": ["LimeSuite"], "pacman": ["limesuite"], "brew": ["limesuite"]},
    "soapy-uhd": {"desc": "SoapySDR plugin for Ettus USRP",
                  "apt": ["soapysdr-module-uhd", "soapysdr0.8-module-uhd"],
                  "dnf": ["soapy-uhd"], "pacman": ["soapyuhd"], "brew": ["soapyuhd"]},
    "soapy-plutosdr": {"desc": "SoapySDR plugin for ADALM-Pluto",
                       "apt": ["soapysdr-module-plutosdr", "soapysdr0.8-module-plutosdr"],
                       "dnf": ["soapy-plutosdr"], "pacman": ["soapyplutosdr"], "brew": ["soapyplutosdr"]},
    "sdrplay-api": {"desc": "SDRplay API (proprietary vendor driver)", "manual":
                    "Download and install the SDRplay API from sdrplay.com/api, then install the "
                    "SoapySDRPlay3 plugin. FobGuard cannot install proprietary vendor software for you."},
    "usbutils": {"desc": "lsusb (receiver detection fallback)",
                 "apt": ["usbutils"], "dnf": ["usbutils"], "pacman": ["usbutils"]},
}


@dataclass
class Fix:
    kind: str                     # pkg | pip | blacklist | udev | manual
    requirement: str = ""
    needs_root: bool = True
    commands: list[list[str]] = field(default_factory=list)
    description: str = ""


@dataclass
class Check:
    id: str
    title: str
    status: str                   # ok | missing | warn | manual | skip
    detail: str = ""
    required: bool = True
    fix: Fix | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fixable"] = bool(self.fix and self.fix.kind not in ("manual",))
        return d


# ---------------------------------------------------------------------------
# Package manager abstraction ("search")
# ---------------------------------------------------------------------------

Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def default_runner(argv: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))


class PackageManager:
    def __init__(self, runner: Runner = default_runner, which: Callable[[str], str | None] = shutil.which):
        self.run = runner
        self.name = next((m for m in ("apt-get", "dnf", "pacman", "brew") if which(m)), None)
        self.key = {"apt-get": "apt", "dnf": "dnf", "pacman": "pacman", "brew": "brew"}.get(self.name or "", "")
        self._avail: dict[str, bool] = {}
        self._refreshed = False

    def refresh(self) -> None:
        if self._refreshed or not self.name:
            return
        cmd = {"apt": ["apt-get", "update", "-qq"], "dnf": ["dnf", "-q", "makecache"],
               "pacman": ["pacman", "-Sy", "--noconfirm"], "brew": ["brew", "update"]}[self.key]
        self.run(cmd)
        self._refreshed = True

    def index_empty(self, lists_dir: str = "/var/lib/apt/lists") -> bool:
        """apt only knows what's in its downloaded index; a fresh image has none."""
        if self.key != "apt":
            return False
        try:
            return not any("_Packages" in p.name for p in Path(lists_dir).iterdir())
        except OSError:
            return True

    def available(self, pkg: str) -> bool:
        if pkg in self._avail:
            return self._avail[pkg]
        try:
            if self.key == "apt":
                r = self.run(["apt-cache", "policy", pkg])
                ok = r.returncode == 0 and "Candidate:" in r.stdout and "Candidate: (none)" not in r.stdout
            elif self.key == "dnf":
                ok = self.run(["dnf", "-q", "info", pkg]).returncode == 0
            elif self.key == "pacman":
                ok = self.run(["pacman", "-Si", pkg]).returncode == 0
            elif self.key == "brew":
                ok = self.run(["brew", "info", "--formula", pkg]).returncode == 0
            else:
                ok = False
        except (OSError, subprocess.SubprocessError):
            ok = False
        self._avail[pkg] = ok
        return ok

    def search(self, requirement: str) -> str | None:
        """Pick the first candidate package this OS's repositories actually have."""
        for pkg in REQUIREMENTS.get(requirement, {}).get(self.key, []):
            if self.available(pkg):
                return pkg
        return None

    def install_cmd(self, pkg: str) -> list[str]:
        return {"apt": ["apt-get", "install", "-y", "--no-install-recommends", pkg],
                "dnf": ["dnf", "install", "-y", pkg],
                "pacman": ["pacman", "-S", "--noconfirm", "--needed", pkg],
                "brew": ["brew", "install", pkg]}[self.key]


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

def _importable(mod: str) -> tuple[bool, str]:
    try:
        importlib.import_module(mod)
        return True, ""
    except Exception as e:  # ImportError, or OSError when the C library is missing
        return False, f"{type(e).__name__}: {e}"


def _soapy_modules() -> set[str]:
    try:
        import SoapySDR  # type: ignore
        drivers = set()
        for path in SoapySDR.listModules():
            name = Path(path).name.lower()
            for key in ("rtlsdr", "airspyhf", "airspy", "hackrf", "lms7", "uhd", "plutosdr", "sdrplay"):
                if key in name:
                    drivers.add(key)
        return drivers
    except Exception:
        return set()


def _loaded_kernel_modules() -> set[str]:
    try:
        return {line.split()[0] for line in Path("/proc/modules").read_text().splitlines()}
    except OSError:
        return set()


def _pip_cmd(spec: str) -> list[str]:
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", spec]


class Doctor:
    def __init__(self, cfg: Config | None = None, runner: Runner = default_runner,
                 which: Callable[[str], str | None] = shutil.which,
                 devices: list[DeviceInfo] | None = None):
        self.cfg = cfg
        self.run = runner
        self.which = which
        self.pm = PackageManager(runner, which)
        self.devices = detect_devices() if devices is None else devices

    # what the site needs, from config + plugged-in hardware
    def needs(self) -> tuple[bool, bool, set[str]]:
        mons = self.cfg.monitors if self.cfg else []
        need_rtl = any(m.source == "rtlsdr" for m in mons) or any(d.driver == "rtlsdr" for d in self.devices)
        soapy_drivers = {d.driver for d in self.devices if d.driver != "rtlsdr"}
        for m in mons:
            if m.source == "soapy":
                drv = next((p.split("=", 1)[1] for p in m.device.split(",") if p.startswith("driver=")), "")
                if drv:
                    soapy_drivers.add(drv)
        need_soapy = bool(soapy_drivers) or any(m.source == "soapy" for m in mons)
        return need_rtl, need_soapy, soapy_drivers

    def _pkg_check(self, cid: str, title: str, requirement: str, present: bool, detail_ok: str,
                   required: bool = True) -> Check:
        if present:
            return Check(cid, title, "ok", detail_ok, required)
        req = REQUIREMENTS[requirement]
        if "manual" in req:
            return Check(cid, title, "manual", req["manual"], required, Fix("manual", requirement, False,
                                                                           description=req["manual"]))
        if "pip" in req:
            why = getattr(self, "_import_error", {}).get(requirement, "")
            detail = (f"{req['desc']} is installed but incompatible with this system ({why[:160]}). "
                      f"A compatible version ({req['pip']}) will be installed."
                      if "undefined symbol" in why or "AttributeError" in why else f"{req['desc']} not installed")
            return Check(cid, title, "missing", detail, required,
                         Fix("pip", requirement, False, [_pip_cmd(req["pip"])], f"pip install {req['pip']}"))
        if not self.pm.name:
            return Check(cid, title, "manual", f"{req['desc']} missing and no supported package manager "
                                               "(apt, dnf, pacman, brew) was found.", required)
        pkg = self.pm.search(requirement)
        if pkg is None and self.pm.index_empty():
            return Check(cid, title, "missing", f"{req['desc']} missing. This system's package index is empty, "
                                                "so it will be refreshed and searched during the fix.", required,
                         Fix("pkg", requirement, True, [], f"refresh {self.pm.key} index, search and install"))
        if pkg is None:
            return Check(cid, title, "manual", f"{req['desc']} missing; no package for it was found in this "
                                               f"system's {self.pm.key} repositories "
                                               f"(searched: {', '.join(req.get(self.pm.key, [])) or 'none'}).",
                         required)
        return Check(cid, title, "missing", f"{req['desc']} missing. Found package '{pkg}' via {self.pm.key}.",
                     required, Fix("pkg", requirement, self.pm.key != "brew", [self.pm.install_cmd(pkg)],
                                   f"install {pkg} ({self.pm.key})"))

    def checks(self) -> list[Check]:
        out: list[Check] = []
        need_rtl, need_soapy, soapy_drivers = self.needs()
        linux = platform.system() == "Linux"

        v = sys.version_info
        out.append(Check("python", "Python 3.10+", "ok" if v >= (3, 10) else "manual",
                         f"Python {v.major}.{v.minor}.{v.micro} at {sys.executable}"))
        ok, err = _importable("numpy")
        out.append(self._pkg_check("numpy", "NumPy", "numpy", ok, "installed"))

        # receivers
        if self.devices:
            names = ", ".join(f"{d.model}" + (f" (serial {d.serial})" if d.serial else "") for d in self.devices)
            out.append(Check("receivers", "SDR receivers detected", "ok", names))
        else:
            out.append(Check("receivers", "SDR receivers detected", "warn",
                             "No supported receiver is plugged in (or USB is not visible). "
                             "Plug in the receivers; then re-run the check.", required=False))
        if self.cfg:
            rtl_mons = [m for m in self.cfg.monitors if m.source == "rtlsdr"]
            rtl_devs = [d for d in self.devices if d.driver == "rtlsdr"]
            for m in rtl_mons:
                if m.device.isdigit():
                    found = int(m.device) < len(rtl_devs)
                    det = f"index {m.device}"
                else:
                    found = any(d.serial == m.device for d in rtl_devs)
                    det = f"serial {m.device}"
                out.append(Check(f"monitor-{m.name}", f"Receiver for sensor '{m.name}'",
                                 "ok" if found else "warn",
                                 f"{det} {'present' if found else 'not found - check USB or the serial in config'}",
                                 required=False))

        # RTL-SDR stack
        if need_rtl:
            lib = ctypes.util.find_library("rtlsdr")
            out.append(self._pkg_check("librtlsdr", "RTL-SDR driver library", "librtlsdr", bool(lib),
                                       f"found ({lib})"))
            ok, err = _importable("rtlsdr")
            self._import_error = {"pyrtlsdr": err}
            out.append(self._pkg_check("pyrtlsdr", "RTL-SDR Python bindings", "pyrtlsdr", ok, "installed"))
            out.append(self._pkg_check("rtl-sdr-tools", "RTL-SDR tools (rtl_test)", "rtl-sdr-tools",
                                       bool(self.which("rtl_test")), "installed", required=False))
            if linux:
                loaded = _loaded_kernel_modules() & {"dvb_usb_rtl28xxu", "rtl2832", "rtl2830"}
                blacklisted = Path(BLACKLIST_PATH).exists()
                if loaded:
                    out.append(Check("dvb-driver", "TV-tuner kernel driver released", "missing",
                                     f"Kernel module(s) {', '.join(sorted(loaded))} hold the RTL-SDR as a TV "
                                     "tuner; FobGuard can't open it.", True,
                                     Fix("blacklist", "", True,
                                         [["modprobe", "-r", *sorted(loaded)]],
                                         "blacklist and unload the DVB-T driver")))
                elif not blacklisted:
                    out.append(Check("dvb-driver", "TV-tuner kernel driver released", "warn",
                                     "Not loaded now, but not blacklisted; it may grab the dongle after a reboot "
                                     "or replug.", False, Fix("blacklist", "", True, [], "blacklist the DVB-T driver")))
                else:
                    out.append(Check("dvb-driver", "TV-tuner kernel driver released", "ok", "blacklisted"))

        # SoapySDR stack
        if need_soapy:
            ok, err = _importable("SoapySDR")
            out.append(self._pkg_check("soapysdr-python", "SoapySDR Python bindings", "soapysdr-python", ok,
                                       "installed"))
            have = _soapy_modules() if ok else set()
            for drv in sorted(soapy_drivers):
                req = "sdrplay-api" if drv == "sdrplay" else f"soapy-{'lms7' if drv == 'lime' else drv}"
                if req not in REQUIREMENTS:
                    out.append(Check(f"plugin-{drv}", f"SoapySDR plugin '{drv}'", "manual",
                                     "No known package for this driver; install its SoapySDR module manually."))
                    continue
                key = "lms7" if drv == "lime" else drv
                out.append(self._pkg_check(f"plugin-{drv}", f"SoapySDR plugin for {drv}", req,
                                           key in have, "installed"))

        # USB permissions
        if linux and self.devices:
            denied = [d for d in self.devices if d.accessible is False]
            if denied:
                out.append(Check("usb-permissions", "USB access to receivers", "missing",
                                 f"No read/write access to {', '.join(d.devnode for d in denied)} for user "
                                 f"{_user()}.", True,
                                 Fix("udev", "", True, [["udevadm", "control", "--reload-rules"],
                                                        ["udevadm", "trigger"]],
                                     "install udev rule granting plugdev access")))
            else:
                out.append(Check("usb-permissions", "USB access to receivers", "ok", "accessible"))

        # operational checks
        out.append(self._time_check())
        if self.cfg:
            out.append(self._storage_check())
            if self.cfg.dashboard.enabled and self.cfg.dashboard.bind not in ("127.0.0.1", "localhost", "::1") \
                    and not self.cfg.dashboard.token:
                out.append(Check("dashboard-token", "Dashboard protected", "warn",
                                 f"Dashboard listens on {self.cfg.dashboard.bind} without a token. "
                                 "Set FOBGUARD_DASHBOARD_TOKEN.", False))
        return out

    def _time_check(self) -> Check:
        if not self.which("timedatectl"):
            return Check("clock", "Clock synchronised (evidence timestamps)", "skip",
                         "timedatectl unavailable; ensure NTP is configured.", False)
        try:
            r = self.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
            synced = r.stdout.strip() == "yes"
        except (OSError, subprocess.SubprocessError):
            synced = False
        return Check("clock", "Clock synchronised (evidence timestamps)", "ok" if synced else "warn",
                     "NTP synchronised" if synced else "Clock is not NTP-synchronised; incident times may be "
                     "wrong. Enable with: timedatectl set-ntp true", False)

    def _storage_check(self) -> Check:
        p = Path(self.cfg.storage_path).parent
        probe = p if p.exists() else p.parent
        try:
            free = shutil.disk_usage(probe).free / 1e9
        except OSError:
            return Check("storage", "Evidence database storage", "warn", f"{p} not accessible", False)
        writable = os.access(p if p.exists() else probe, os.W_OK)
        status = "ok" if writable and free > 1 else "warn"
        return Check("storage", "Evidence database storage", status,
                     f"{p}: {'writable' if writable else 'NOT writable by ' + _user()}, {free:.1f} GB free", False)

    # ---- fixing -------------------------------------------------------------
    def apply(self, ids: list[str] | None = None, log: Callable[[str], None] = print,
              dry_run: bool = False) -> list[dict]:
        """Apply fixes for the given (or all) failing checks. Re-checks first."""
        checks = self.checks()
        todo = [c for c in checks if c.fix and c.fix.kind != "manual" and c.status in ("missing", "warn")
                and (ids is None or c.id in ids)]
        results = []
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        if any(c.fix.kind == "pkg" for c in todo) and not dry_run and (is_root or self.pm.key == "brew"):
            self.pm.refresh()
        for c in todo:
            f = c.fix
            if f.needs_root and not is_root and not dry_run:
                results.append({"id": c.id, "ok": False, "message": "needs administrator rights: re-run with sudo "
                                "or use the dashboard's install button (root helper)"})
                log(f"[skip] {c.title}: needs root")
                continue
            log(f"[fix ] {c.title}: {f.description}")
            if dry_run:
                results.append({"id": c.id, "ok": True, "message": "dry run",
                                "commands": f.commands})
                continue
            try:
                if f.kind == "pkg" and not f.commands:          # deferred search after index refresh
                    self.pm.refresh()
                    self.pm._avail.clear()
                    pkg = self.pm.search(f.requirement)
                    if pkg is None:
                        raise RuntimeError(f"no package for {f.requirement} in {self.pm.key} repositories "
                                           f"(searched {', '.join(REQUIREMENTS[f.requirement].get(self.pm.key, []))})")
                    log(f"       found {pkg}")
                    f.commands = [self.pm.install_cmd(pkg)]
                if f.kind == "blacklist":
                    Path(BLACKLIST_PATH).write_text(BLACKLIST)
                elif f.kind == "udev":
                    Path(UDEV_RULE_PATH).write_text(UDEV_RULE)
                    user = os.environ.get("FOBGUARD_SERVICE_USER", "fobguard")
                    if self.which("usermod"):
                        self.run(["usermod", "-aG", "plugdev", user])
                for cmd in f.commands:
                    r = self.run(cmd)
                    if r.returncode != 0:
                        raise RuntimeError(f"{' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()[-300:]}")
                msg = f.description
                if f.requirement == "soapysdr-python" and not _importable("SoapySDR")[0]:
                    msg += (f" - installed for the system Python, but FobGuard runs on {sys.executable}. "
                            "Recreate its venv with --system-site-packages (the installer does this).")
                results.append({"id": c.id, "ok": True, "message": msg})
            except Exception as e:
                results.append({"id": c.id, "ok": False, "message": str(e)})
                log(f"[fail] {c.title}: {e}")
        return results


def _user() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return os.environ.get("USER", "?")


# ---------------------------------------------------------------------------
# Dashboard <-> root helper handshake
# ---------------------------------------------------------------------------

HELPER_UNITS = ("/etc/systemd/system/anankos-fobguard-fix.path", "/lib/systemd/system/anankos-fobguard-fix.path")


def helper_installed() -> bool:
    return any(Path(p).exists() for p in HELPER_UNITS)


def write_request(state_dir: str, ids: list[str], who: str) -> Path:
    p = Path(state_dir) / "fix-request.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ids": [str(i)[:64] for i in ids][:50], "requested_by": who[:60],
                               "at": time.time()}))
    tmp.replace(p)
    return p


def read_result(state_dir: str) -> dict | None:
    p = Path(state_dir) / "fix-result.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def run_request(cfg: Config | None, state_dir: str, log: Callable[[str], None] = print) -> dict:
    """Executed by the root helper: process a pending request from the dashboard."""
    req_p = Path(state_dir) / "fix-request.json"
    try:
        req = json.loads(req_p.read_text())
    except (OSError, ValueError):
        return {"ok": False, "message": "no valid request"}
    ids = [i for i in req.get("ids", []) if isinstance(i, str)]
    doc = Doctor(cfg)
    results = doc.apply(ids, log=log)
    out = {"finished": time.time(), "requested_by": req.get("requested_by", "?"), "results": results,
           "checks": [c.to_dict() for c in doc.checks()]}
    (Path(state_dir) / "fix-result.json").write_text(json.dumps(out))
    req_p.unlink(missing_ok=True)
    return out
