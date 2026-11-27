import datetime as dt
import json
import subprocess
import urllib.request
from pathlib import Path

import pytest

from anankos_fobguard.antenna import AntennaError
from anankos_fobguard.config import AlertConfig, AntennaConfig, Config, MonitorConfig, load_config
from anankos_fobguard.detectors import Baseline
from anankos_fobguard.doctor import Doctor, PackageManager, run_request, write_request
from anankos_fobguard.engine import Engine
from anankos_fobguard.hardware import DeviceInfo, enumerate_usb
from anankos_fobguard.sources import open_source

OPEN = dt.datetime(2026, 11, 25, 14, 0, tzinfo=dt.timezone.utc).timestamp()


# ------------------------------------------------------------------ hardware

def _usbdev(root: Path, name, vid, pid, serial="", busnum="1", devnum="4"):
    d = root / name
    d.mkdir()
    for k, v in {"idVendor": vid, "idProduct": pid, "serial": serial, "busnum": busnum,
                 "devnum": devnum, "product": "x"}.items():
        (d / k).write_text(v + "\n")


def test_enumerate_usb_identifies_receivers(tmp_path):
    _usbdev(tmp_path, "1-1", "0bda", "2838", "LOT315")
    _usbdev(tmp_path, "1-2", "1df7", "3050", "RSPDX1", devnum="5")
    _usbdev(tmp_path, "1-3", "046d", "c52b")                     # a mouse: ignored
    devs = enumerate_usb(str(tmp_path))
    assert [(d.model, d.serial, d.driver) for d in devs] == [
        ("RTL2832U (RTL-SDR)", "LOT315", "rtlsdr"), ("SDRplay RSPdx", "RSPDX1", "sdrplay")]
    assert devs[1].ports == ["Antenna A", "Antenna B", "Antenna C"]


# ------------------------------------------------------------------- antennas

def antenna_engine(scenario="ambient"):
    cfg = Config()
    cfg.site.timezone = "UTC"
    cfg.alerts = AlertConfig(sinks=[])
    cfg.monitors = [MonitorConfig(name="m1", source="sim", scenario=scenario)]
    cfg.antennas = [AntennaConfig("Roof whip", "m1", port="Whip (sim)", band_mhz=[300, 330]),
                    AntennaConfig("Window stub", "m1", port="Weak (sim)", band_mhz=[300, 330]),
                    AntennaConfig("Unplugged spare", "m1", port="Disconnected (sim)"),
                    AntennaConfig("433 yagi", "m1", port="Whip (sim)", band_mhz=[420, 440])]
    eng = Engine(cfg, baseline=Baseline())
    st = eng.monitors["m1"]
    st.source = open_source(st.cfg, start_time=OPEN)
    eng.antennas.on_source_opened(st)
    it = iter(st.source)

    def run(seconds):
        for _ in range(int(seconds / (st.cfg.block_size / st.cfg.sample_rate))):
            t0, iq = next(it)
            eng.handle_block("m1", t0, iq)
    return eng, run


def test_antenna_switch_check_and_audit():
    eng, run = antenna_engine()
    opts = {o["id"]: o for o in eng.antennas.options("m1")}
    assert opts["433 yagi"]["band_ok"] is False and opts["Roof whip"]["band_ok"]
    run(30)
    assert eng.antennas.assess("m1")["verdict"] == "ok"

    eng.antennas.select("m1", "Unplugged spare", "Sam")
    assert eng.antennas.assess("m1")["verdict"] == "settling"
    run(35)
    a = eng.antennas.assess("m1")
    assert a["verdict"] == "possibly_disconnected", a
    assert eng.monitors["m1"].source.antenna == "Disconnected (sim)"

    eng.antennas.select("m1", "Window stub", "Sam")
    run(40)
    assert eng.antennas.assess("m1")["verdict"] == "weak"

    with pytest.raises(AntennaError, match="built for"):
        eng.antennas.select("m1", "433 yagi", "Sam")
    with pytest.raises(AntennaError):
        eng.antennas.select("m1", "port:NOPE", "Sam")
    audit = [d for d in eng.detections if d.kind == "antenna_changed"]
    assert len(audit) == 2 and audit[0].details["by"] == "Sam"
    # switching antennas must never look like jamming
    assert not any(d.kind == "jamming" for d in eng.detections)


def test_best_antenna_sweep_picks_and_applies_best():
    eng, run = antenna_engine()
    eng.antennas.select("m1", "Window stub", "Sam")
    info = eng.antennas.start_sweep("m1", dwell_s=30, who="Sam")
    assert info["options"] == ["Roof whip", "Window stub", "Unplugged spare"]
    run(3 * 36 + 2)
    st = eng.antennas.sweep_status("m1")
    assert st["running"] is False and st["best"] == "Roof whip"
    assert eng.antennas.active["m1"] == "Roof whip"
    ranking = [r["antenna"] for r in st["result"]["ranking"]]
    assert ranking[-1] == "Unplugged spare"


def test_antenna_state_persists(tmp_path):
    from anankos_fobguard.store import Store
    cfg = Config()
    cfg.alerts = AlertConfig(sinks=[])
    cfg.storage_path = str(tmp_path / "db.sqlite")
    cfg.monitors = [MonitorConfig(name="m1", source="sim")]
    cfg.antennas = [AntennaConfig("A", "m1", port="Whip (sim)"), AntennaConfig("B", "m1", port="Weak (sim)")]
    eng = Engine(cfg, store=Store(cfg.storage_path), baseline=Baseline())
    assert eng.antennas.active["m1"] == "A"
    eng.antennas.select("m1", "B", "Sam")
    eng2 = Engine(cfg, store=Store(cfg.storage_path), baseline=Baseline())
    assert eng2.antennas.active["m1"] == "B"


def test_antenna_config_validation(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[[monitor]]\nname="m1"\n[[antenna]]\nname="x"\nmonitor="nope"\nport="RX"\n')
    with pytest.raises(ValueError, match="unknown monitor"):
        load_config(p)
    p.write_text('[[monitor]]\nname="m1"\n[[antenna]]\nname="x"\nmonitor="m1"\n')
    with pytest.raises(ValueError, match="port"):
        load_config(p)


def test_switch_command_runs_without_shell(tmp_path):
    marker = tmp_path / "relay-state"
    cfg = Config()
    cfg.alerts = AlertConfig(sinks=[])
    cfg.monitors = [MonitorConfig(name="m1", source="sim")]
    cfg.antennas = [AntennaConfig("North mast", "m1", switch_command=f"touch {marker}"),
                    AntennaConfig("South mast", "m1", switch_command="false")]
    eng = Engine(cfg, baseline=Baseline())       # first profile re-asserted at start-up
    assert marker.exists()
    with pytest.raises(AntennaError, match="exited 1"):
        eng.antennas.select("m1", "South mast", "Sam")
    assert eng.antennas.active["m1"] == "North mast"


# --------------------------------------------------------------------- doctor

class FakeRunner:
    """Pretends to be apt: knows a few packages; records installs."""

    def __init__(self, available):
        self.available = set(available)
        self.calls = []

    def __call__(self, argv, timeout=600):
        self.calls.append(argv)
        out = ""
        if argv[:2] == ["apt-cache", "policy"]:
            pkg = argv[2]
            out = f"{pkg}:\n  Candidate: 1.0\n" if pkg in self.available else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


def fake_which(name):
    return "/usr/bin/" + name if name in ("apt-get",) else None


def test_package_search_picks_first_available_candidate():
    pm = PackageManager(FakeRunner({"soapysdr0.8-module-airspy"}), fake_which)
    assert pm.key == "apt"
    assert pm.search("soapy-airspy") == "soapysdr0.8-module-airspy"   # first candidate missing, second found
    assert pm.search("soapy-hackrf") is None


def test_doctor_plans_plugins_for_plugged_in_hardware(monkeypatch):
    import anankos_fobguard.doctor as doc_mod
    monkeypatch.setattr(doc_mod, "_importable", lambda m: (m == "numpy", "ImportError"))
    monkeypatch.setattr(doc_mod.PackageManager, "index_empty", lambda self, *a: False)
    devs = [DeviceInfo("Airspy HF+", "airspyhf", "soapy-airspyhf", "03eb", "800c"),
            DeviceInfo("SDRplay RSPdx", "sdrplay", "sdrplay-api", "1df7", "3050")]
    runner = FakeRunner({"python3-soapysdr", "soapysdr-module-airspyhf"})
    d = Doctor(None, runner=runner, which=fake_which, devices=devs)
    checks = {c.id: c for c in d.checks()}
    assert checks["soapysdr-python"].fix.commands == [
        ["apt-get", "install", "-y", "--no-install-recommends", "python3-soapysdr"]]
    assert checks["plugin-airspyhf"].status == "missing"
    assert checks["plugin-sdrplay"].status == "manual"            # proprietary: never auto-installed
    assert "rtl" not in " ".join(checks)                         # no RTL hardware/config -> no RTL checks


def test_doctor_fix_dry_run_and_root_gate(monkeypatch):
    import anankos_fobguard.doctor as doc_mod
    monkeypatch.setattr(doc_mod, "_importable", lambda m: (m == "numpy", "ImportError"))
    monkeypatch.setattr(doc_mod.PackageManager, "index_empty", lambda self, *a: False)
    devs = [DeviceInfo("Airspy HF+", "airspyhf", "soapy-airspyhf", "03eb", "800c")]
    runner = FakeRunner({"python3-soapysdr", "soapysdr-module-airspyhf"})
    d = Doctor(None, runner=runner, which=fake_which, devices=devs)
    res = d.apply(log=lambda *_: None, dry_run=True)
    cmds = [c for r in res for c in r.get("commands", [])]
    assert ["apt-get", "install", "-y", "--no-install-recommends", "soapysdr-module-airspyhf"] in cmds
    assert not any(c[:2] == ["apt-get", "install"] for c in runner.calls)     # dry run installs nothing
    monkeypatch.setattr(doc_mod.os, "geteuid", lambda: 1000)
    res = d.apply(log=lambda *_: None)
    assert all(not r["ok"] and "administrator" in r["message"] for r in res if r["id"] != "numpy")


def test_doctor_refreshes_empty_index_then_installs(monkeypatch):
    import anankos_fobguard.doctor as doc_mod
    monkeypatch.setattr(doc_mod, "_importable", lambda m: (m == "numpy", "ImportError"))
    monkeypatch.setattr(doc_mod.os, "geteuid", lambda: 0)
    devs = [DeviceInfo("Airspy HF+", "airspyhf", "soapy-airspyhf", "03eb", "800c")]

    class Stale(FakeRunner):
        refreshed = False

        def __call__(self, argv, timeout=600):
            if argv[:2] == ["apt-get", "update"]:
                self.refreshed = True
            if argv[:2] == ["apt-cache", "policy"] and not self.refreshed:
                self.calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")
            return super().__call__(argv, timeout)

    runner = Stale({"python3-soapysdr", "soapysdr-module-airspyhf"})
    state = {"empty": True}
    monkeypatch.setattr(doc_mod.PackageManager, "index_empty", lambda self, *a: state["empty"])
    d = Doctor(None, runner=runner, which=fake_which, devices=devs)
    c = {c.id: c for c in d.checks()}["plugin-airspyhf"]
    assert c.status == "missing" and "index is empty" in c.detail
    res = {r["id"]: r for r in d.apply(["plugin-airspyhf", "soapysdr-python"], log=lambda *_: None)}
    assert res["plugin-airspyhf"]["ok"], res
    assert ["apt-get", "update", "-qq"] in runner.calls
    assert ["apt-get", "install", "-y", "--no-install-recommends", "soapysdr-module-airspyhf"] in runner.calls


def test_request_file_only_carries_ids(tmp_path, monkeypatch):
    import anankos_fobguard.doctor as doc_mod
    p = write_request(str(tmp_path), ["plugin-airspyhf", "x" * 500, "; rm -rf /"], "Sam")
    req = json.loads(p.read_text())
    assert all(len(i) <= 64 for i in req["ids"])
    seen = {}

    def fake_apply(self, ids=None, log=print, dry_run=False):
        seen["ids"] = ids
        return []
    monkeypatch.setattr(doc_mod.Doctor, "apply", fake_apply)
    monkeypatch.setattr(doc_mod.Doctor, "checks", lambda self: [])
    monkeypatch.setattr(doc_mod, "detect_devices", lambda: [])
    out = run_request(None, str(tmp_path))
    assert seen["ids"][0] == "plugin-airspyhf"
    assert (tmp_path / "fix-result.json").exists() and not p.exists()
    assert out["requested_by"] == "Sam"


# ------------------------------------------------------------------ dashboard

def test_dashboard_hardware_endpoints(monkeypatch):
    from anankos_fobguard.dashboard import Dashboard
    import anankos_fobguard.doctor as doc_mod
    monkeypatch.setattr(doc_mod, "helper_installed", lambda: False)
    eng, run = antenna_engine()
    run(10)
    dash = Dashboard(eng, "127.0.0.1", 0, token="t", config_path="/etc/anankos-fobguard/config.toml")
    dash.start()
    base = f"http://127.0.0.1:{dash.httpd.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(base + path + "?token=t", data=json.dumps(body).encode(), method="POST")
        try:
            return 200, json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    try:
        hw = json.load(urllib.request.urlopen(base + "/api/hardware?token=t"))
        assert hw["monitors"]["m1"]["label"] == "Roof whip"
        code, r = post("/api/monitors/m1/antenna", {"option": "Window stub", "by": "Sam"})
        assert code == 200 and r["label"] == "Window stub"
        code, r = post("/api/monitors/m1/antenna", {"option": "433 yagi"})
        assert code == 400 and "built for" in r["error"]
        code, r = post("/api/monitors/m1/antenna/check", {})
        assert code == 200 and r["verdict"] == "settling"
        doc = json.load(urllib.request.urlopen(base + "/api/doctor?token=t"))
        assert any(c["id"] == "python" for c in doc["checks"])
        code, r = post("/api/doctor/fix", {"ids": ["numpy"]})
        assert r["queued"] is False and r["command"] == "sudo fobguard doctor --fix -c /etc/anankos-fobguard/config.toml"
    finally:
        dash.stop()


def test_sweep_ranking_prefers_margin_over_signal_count_luck():
    from anankos_fobguard.antenna import AntennaStats, Sweep

    eng, _ = antenna_engine()
    sw = Sweep("m1", ["Roof whip", "Window stub", "Unplugged spare"], 30, False, "t", "Roof whip")
    sw.results = [
        {"antenna": "Window stub", "signals": 6, "frames": 18, "median_rssi_dbfs": -50, "noise_floor_db": -55},
        {"antenna": "Roof whip", "signals": 5, "frames": 5, "median_rssi_dbfs": -30, "noise_floor_db": -55},
        {"antenna": "Unplugged spare", "signals": 0, "frames": 0, "median_rssi_dbfs": None, "noise_floor_db": -61},
    ]
    sw.idx = len(sw.options) - 1
    sw.results.pop()          # _tick appends the last antenna's result itself
    eng.antennas.stats[("m1", "Unplugged spare")] = AntennaStats(since=0.0)
    sw.phase_start = 0.0
    eng.antennas.sweeps["m1"] = sw
    eng.antennas._tick(sw, 1e9)
    assert sw.done and sw.best == "Roof whip"      # 5 strong signals beat 6 barely-audible ones
