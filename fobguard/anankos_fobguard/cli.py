"""Command-line interface: `fobguard <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import signal
import sys
import threading
import time
from importlib import resources
from pathlib import Path

from . import __version__
from .config import AlertConfig, Config, MonitorConfig, SinkConfig, load_config


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING - 10 * verbose
    logging.basicConfig(level=max(logging.DEBUG, level),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _engine(cfg: Config, with_store: bool = True):
    from .engine import Engine
    from .store import Store

    store = None
    if with_store:
        Path(cfg.storage_path).parent.mkdir(parents=True, exist_ok=True)
        store = Store(cfg.storage_path)
    return Engine(cfg, store=store)


def _start_dashboard(cfg: Config, engine):
    if not cfg.dashboard.enabled:
        return None
    from .dashboard import Dashboard

    if cfg.dashboard.bind not in ("127.0.0.1", "localhost", "::1") and not cfg.dashboard.token:
        logging.getLogger("fobguard").warning(
            "dashboard is exposed on %s without a token; set [dashboard] token", cfg.dashboard.bind)
    d = Dashboard(engine, cfg.dashboard.bind, cfg.dashboard.port, cfg.dashboard.token)
    d.start()
    print(f"Dashboard: http://{cfg.dashboard.bind}:{cfg.dashboard.port}/", file=sys.stderr)
    return d


def cmd_run(a) -> int:
    cfg = load_config(a.config)
    if not cfg.monitors:
        print("no [[monitor]] entries configured", file=sys.stderr)
        return 2
    eng = _engine(cfg)
    dash = _start_dashboard(cfg, eng)
    signal.signal(signal.SIGTERM, lambda *_: eng.stop())
    print(f"Anankos FobGuard {__version__} monitoring {len(cfg.monitors)} sensor(s) at {cfg.site.name}. "
          "Receive-only.", file=sys.stderr)
    try:
        eng.run_live()
    except KeyboardInterrupt:
        eng.stop()
    finally:
        if dash:
            dash.stop()
        if eng.store:
            eng.store.close()
    return 0


def _print_results(eng) -> None:
    tz = eng.cfg.site.tz()
    print(f"\nDecoded frames: {eng.packets_seen}")
    if not eng.detections:
        print("Detections: none - RF activity looked like normal use.")
    for d in eng.detections:
        t = dt.datetime.fromtimestamp(d.t, tz).strftime("%H:%M:%S.%f")[:-3]
        print(f"  {t}  {d.severity.name:<8} {d.kind:<22} {d.message}")
    for inc in eng.correlator.incidents:
        print(f"Incident #{inc.id}: {inc.severity.name} - {inc.title}")


def _sim_config(scenario: str, zone: str, dashboard: bool, port: int) -> Config:
    cfg = Config()
    cfg.site.timezone = "UTC"
    cfg.monitors = [MonitorConfig(name="sim-315", source="sim", scenario=scenario, zone=zone,
                                  center_freq=315.25e6)]
    cfg.alerts = AlertConfig(sinks=[SinkConfig("console")])
    cfg.dashboard.enabled = dashboard
    cfg.dashboard.bind = "127.0.0.1"
    cfg.dashboard.port = port
    cfg.baseline_path = "/nonexistent"
    return cfg


def cmd_simulate(a) -> int:
    from .engine import Engine
    from .simulate import SCENARIOS

    names = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    for name in names:
        zone = a.zone or ("key_storage" if name == "relay" else "lot")
        closed = a.closed or name == "relay"
        day = dt.datetime.now(dt.timezone.utc).date()
        # A Wednesday-style weekday time: 14:00 (open) or 03:00 (closed) UTC
        start = dt.datetime.combine(day, dt.time(3 if closed else 14), dt.timezone.utc)
        while start.weekday() > 4:
            start -= dt.timedelta(days=1)
        cfg = _sim_config(name, zone, a.dashboard, a.port)
        eng = Engine(cfg)
        dash = _start_dashboard(cfg, eng) if a.dashboard else None
        print(f"\n### Scenario: {name}  (site {'closed' if closed else 'open'}, zone {zone})")
        if a.realtime or a.dashboard:
            eng.monitors["sim-315"].cfg.source = "sim"
            # pace like a real radio so the dashboard is watchable
            from .sources import SimSource
            src = SimSource(eng.monitors["sim-315"].cfg, start_time=time.time(), throttle=True)
            for t0, iq in src:
                eng.handle_block("sim-315", t0, iq)
        else:
            eng.run_offline(start_time=start.timestamp())
        _print_results(eng)
        if dash:
            print("Simulation finished; dashboard stays up. Ctrl+C to exit.", file=sys.stderr)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                dash.stop()
    return 0


def cmd_analyze(a) -> int:
    from .engine import Engine

    cfg = load_config(a.config) if a.config else Config()
    if not a.config:
        cfg.alerts = AlertConfig(sinks=[])
        cfg.baseline_path = "/nonexistent"
    m = MonitorConfig(name=Path(a.file).stem, source="file", device=a.file, center_freq=a.freq * 1e6,
                      sample_rate=a.rate, zone=a.zone, file_format=a.format, file_start_time=a.start)
    cfg.monitors = [m]
    eng = Engine(cfg)
    eng.run_offline()
    _print_results(eng)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "detections": [d.to_dict() for d in eng.detections],
            "incidents": [i.to_dict() for i in eng.correlator.incidents]}, indent=2, default=str))
    return 0


def cmd_calibrate(a) -> int:
    from .engine import learn_baseline

    cfg = load_config(a.config)
    cfg.alerts.sinks = []
    eng = _engine(cfg, with_store=False)
    print(f"Calibrating for {a.minutes:.0f} min. Keep the site in its normal state; "
          "press a few customer fobs near each sensor so RSSI can be checked.", file=sys.stderr)
    timer = threading.Timer(a.minutes * 60, eng.stop)
    timer.start()
    try:
        eng.run_live()
    except KeyboardInterrupt:
        eng.stop()
    timer.cancel()
    base = learn_baseline(eng)
    out = Path(a.output or cfg.baseline_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(base.to_json()))
    print(f"Baseline saved to {out}: {len(base.floors)} noise floors, {len(base.fixed_codes)} fixed-code devices.")
    for name, st in eng.monitors.items():
        rssi = f"{st.max_rssi:.1f} dBFS" if st.packets else "n/a"
        print(f"  {name}: floor {st.dsp.stats.noise_floor_db:.1f} dB, {st.packets} frames, "
              f"strongest frame {rssi}")
    kinds = sorted({d.kind for d in eng.detections})
    if kinds:
        print(f"  NOTE: detections fired during calibration ({', '.join(kinds)}). "
              "Investigate before trusting this baseline.")
    print("Tip: set [detection] key_storage_rssi_dbfs a few dB below the RSSI of a fob pressed inside the key room.")
    return 0


def cmd_devices(a) -> int:
    try:
        from rtlsdr import RtlSdr  # type: ignore
    except ImportError:
        print("pyrtlsdr not installed (pip install 'anankos-fobguard[rtlsdr]')")
        return 1
    serials = RtlSdr.get_device_serial_addresses()
    if not serials:
        print("No RTL-SDR devices found.")
    for i, s in enumerate(serials):
        print(f"  index {i}: serial {s}")
    return 0


def cmd_init_config(a) -> int:
    dst = Path(a.path)
    if dst.exists() and not a.force:
        print(f"{dst} exists (use --force to overwrite)", file=sys.stderr)
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(resources.files("anankos_fobguard").joinpath("default_config.toml").read_text())
    print(f"Wrote {dst}")
    return 0


def cmd_check_config(a) -> int:
    cfg = load_config(a.config)
    print(f"Site: {cfg.site.name} ({cfg.site.timezone}) - open now: {cfg.site.is_open(time.time())}")
    for m in cfg.monitors:
        print(f"  monitor {m.name}: {m.source}:{m.device} {m.center_freq / 1e6:.3f} MHz "
              f"@ {m.sample_rate / 1e6:.3f} MS/s zone={m.zone} role={m.role}")
    print(f"  alert sinks: {', '.join(s.type for s in cfg.alerts.sinks) or 'none'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fobguard", description="Anankos FobGuard - passive key fob attack detection")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="count", default=1)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="monitor live with configured receivers")
    r.add_argument("-c", "--config", default="/etc/anankos-fobguard/config.toml")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("simulate", help="run a synthetic attack scenario (no radio needed)")
    s.add_argument("scenario", choices=["all", "normal", "rolljam", "replay", "rollback", "bruteforce", "relay"])
    s.add_argument("--closed", action="store_true", help="simulate after-hours")
    s.add_argument("--zone", default=None)
    s.add_argument("--realtime", action="store_true")
    s.add_argument("--dashboard", action="store_true", help="serve the dashboard while simulating")
    s.add_argument("--port", type=int, default=8742)
    s.set_defaults(fn=cmd_simulate)

    an = sub.add_parser("analyze", help="analyze an IQ recording (rtl_sdr .cu8, .cs16, .cf32)")
    an.add_argument("file")
    an.add_argument("--freq", type=float, required=True, help="center frequency of recording, MHz")
    an.add_argument("--rate", type=float, default=1.024e6, help="sample rate, S/s")
    an.add_argument("--format", default="auto", choices=["auto", "cu8", "cs16", "cf32"])
    an.add_argument("--start", default=None, help="ISO timestamp of first sample (default: file mtime)")
    an.add_argument("--zone", default="lot")
    an.add_argument("-c", "--config", default=None, help="use site settings from this config")
    an.add_argument("--json", default=None, help="write results to this JSON file")
    an.set_defaults(fn=cmd_analyze)

    c = sub.add_parser("calibrate", help="learn noise floors and benign fixed-code devices")
    c.add_argument("-c", "--config", default="/etc/anankos-fobguard/config.toml")
    c.add_argument("--minutes", type=float, default=30)
    c.add_argument("-o", "--output", default=None)
    c.set_defaults(fn=cmd_calibrate)

    d = sub.add_parser("devices", help="list RTL-SDR receivers")
    d.set_defaults(fn=cmd_devices)

    i = sub.add_parser("init-config", help="write a starter config file")
    i.add_argument("path", nargs="?", default="config.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init_config)

    k = sub.add_parser("check-config", help="validate a config file")
    k.add_argument("-c", "--config", default="/etc/anankos-fobguard/config.toml")
    k.set_defaults(fn=cmd_check_config)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    _setup_logging(a.verbose - 1)
    return int(a.fn(a) or 0)
