import datetime as dt
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from anankos_fobguard.config import AlertConfig, Config, MonitorConfig, SinkConfig, load_config
from anankos_fobguard.detectors import Baseline
from anankos_fobguard.dsp import demodulate, similarity, split_frames
from anankos_fobguard.engine import Engine
from anankos_fobguard.models import Severity
from anankos_fobguard.simulate import (Scenario, fixed_code_frame_us, press_us, render_ook,
                                       rolling_code_frame_us)

OPEN = dt.datetime(2026, 11, 25, 14, 0, tzinfo=dt.timezone.utc).timestamp()     # Wednesday 14:00
CLOSED = dt.datetime(2026, 11, 25, 3, 0, tzinfo=dt.timezone.utc).timestamp()    # Wednesday 03:00
FS = 1.024e6


def make_cfg(scenario="normal", zone="lot", **mon):
    cfg = Config()
    cfg.site.timezone = "UTC"
    cfg.monitors = [MonitorConfig(name="m1", source="sim", scenario=scenario, zone=zone, **mon)]
    cfg.alerts = AlertConfig(sinks=[])
    return cfg


def run_scenario(name, zone="lot", start=OPEN):
    eng = Engine(make_cfg(name, zone), baseline=Baseline())
    eng.run_offline(start_time=start)
    return eng


# --------------------------------------------------------------------- DSP

def test_rolling_code_roundtrip_and_similarity():
    rng = np.random.default_rng(1)
    c1, c2 = rng.integers(0, 2, 66), rng.integers(0, 2, 66)
    pad = np.zeros(4000, np.complex64)

    def frames(code, jitter=0.0):
        iq = render_ook(rolling_code_frame_us(code), FS, 50e3, 0.2, jitter=jitter, rng=rng)
        iq = np.concatenate([pad, iq, pad]) + 0.002 * (rng.standard_normal(iq.size + 8000)).astype(np.complex64)
        d = demodulate(iq, FS, 50e3)
        return split_frames(d)

    a, a2, b = frames(c1)[0], frames(c1, jitter=0.03)[0], frames(c2)[0]
    assert 140 <= len(a.symbols) <= 160
    assert abs(a.unit_us - 400) < 20
    assert similarity(a.symbols, a2.symbols) >= 0.95        # replay with timing jitter
    assert similarity(a.symbols, b.symbols) < 0.85          # different rolling code


def test_fsk_decoding():
    s = Scenario("fsk", 1.0)
    s.fsk_fob_press(0.1)
    d = demodulate(s.render(), FS, 80e3)
    assert d is not None and d.modulation == "fsk"
    fr = split_frames(d)
    assert len(fr) == 3 and abs(fr[0].unit_us - 250) < 15


def test_noise_produces_no_frames():
    rng = np.random.default_rng(3)
    x = (0.01 * (rng.standard_normal(200000) + 1j * rng.standard_normal(200000))).astype(np.complex64)
    d = demodulate(x, FS, 0.0)
    assert d is None or split_frames(d) == []


# --------------------------------------------------------------- scenarios

def test_normal_use_raises_nothing():
    eng = run_scenario("normal")
    assert eng.packets_seen >= 7
    assert [d for d in eng.detections if d.severity >= Severity.LOW] == []


def test_normal_use_after_hours_is_only_medium():
    eng = run_scenario("normal", start=CLOSED)
    kinds = {d.kind for d in eng.detections}
    assert kinds == {"after_hours_activity"}
    assert max(i.severity for i in eng.correlator.incidents) == Severity.MEDIUM


def test_rolljam():
    eng = run_scenario("rolljam")
    kinds = [d.kind for d in eng.detections]
    assert "jamming" in kinds and kinds.count("fob_during_jam") == 2
    rep = [d for d in eng.detections if d.kind == "replay"]
    assert len(rep) == 1 and rep[0].severity == Severity.CRITICAL and rep[0].details["captured_during_jam"]
    inc = eng.correlator.incidents
    assert len(inc) == 1 and inc[0].title == "RollJam-style capture and replay"
    assert inc[0].severity == Severity.CRITICAL


def test_replay():
    eng = run_scenario("replay")
    assert [d.kind for d in eng.detections] == ["replay"]
    assert eng.correlator.incidents[0].severity == Severity.HIGH


def test_replay_after_hours_escalates_to_critical():
    eng = run_scenario("replay", start=CLOSED)
    assert any(d.kind == "replay" for d in eng.detections)
    assert eng.correlator.incidents[0].severity == Severity.CRITICAL


def test_rollback():
    eng = run_scenario("rollback")
    kinds = [d.kind for d in eng.detections]
    assert kinds.count("replay") == 2 and "rollback" in kinds
    assert eng.correlator.incidents[0].title == "RollBack rolling-code attack"


def test_bruteforce():
    eng = run_scenario("bruteforce")
    kinds = {d.kind for d in eng.detections}
    assert "bruteforce" in kinds
    assert "fob_during_jam" not in kinds         # scanner frames are not "a fob under jamming"
    assert eng.correlator.incidents[0].title == "Key code scanning / brute force"


def test_relay_key_storage_after_hours():
    eng = run_scenario("relay", zone="key_storage", start=CLOSED)
    assert any(d.kind == "stored_key_responding" for d in eng.detections)
    assert eng.correlator.incidents[0].severity == Severity.CRITICAL


def test_relay_scenario_during_hours_is_quiet():
    eng = run_scenario("relay", zone="key_storage", start=OPEN)
    assert [d for d in eng.detections if d.severity >= Severity.MEDIUM] == []


def test_fixed_code_device_is_not_a_replay():
    s = Scenario("gate", 40.0, start_time=OPEN)
    for t in (6.0, 20.0, 34.0):          # gate remote pressed three times
        s.fixed_device(t, 0x5A5A5)
    eng = Engine(make_cfg(), baseline=Baseline())
    for t0, iq in s.blocks():
        eng.handle_block("m1", t0, iq)
    assert all(d.severity <= Severity.LOW for d in eng.detections)
    assert not any(d.kind == "replay" for d in eng.detections)


def test_calibrated_fixed_code_is_silent():
    frame = fixed_code_frame_us(0x5A5A5)
    s = Scenario("gate", 40.0, start_time=OPEN)
    for t in (6.0, 20.0, 34.0):
        s.fixed_device(t, 0x5A5A5)
    # learn it first
    eng = Engine(make_cfg(), baseline=Baseline())
    for t0, iq in s.blocks():
        eng.handle_block("m1", t0, iq)
    from anankos_fobguard.engine import learn_baseline
    base = learn_baseline(eng)
    assert base.fixed_codes and frame
    eng2 = Engine(make_cfg(), baseline=Baseline.from_json(json.loads(json.dumps(base.to_json()))))
    for t0, iq in s.blocks():
        eng2.handle_block("m1", t0, iq)
    assert eng2.detections == []


# --------------------------------------------------------- file / storage

def test_analyze_cu8_recording(tmp_path):
    s = Scenario("rec", 25.0, start_time=OPEN)
    c = s.fob_press(6.0)
    s.fob_press(19.0, code_bits=c, jitter=0.02, amp=0.1)
    x = s.render()
    u8 = np.empty(x.size * 2, np.uint8)
    u8[0::2] = np.clip(x.real * 127.5 + 127.5, 0, 255)
    u8[1::2] = np.clip(x.imag * 127.5 + 127.5, 0, 255)
    f = tmp_path / "capture.cu8"
    u8.tofile(f)
    cfg = Config()
    cfg.alerts = AlertConfig(sinks=[])
    cfg.monitors = [MonitorConfig(name="rec", source="file", device=str(f), center_freq=315.25e6,
                                  file_start_time=dt.datetime.fromtimestamp(OPEN, dt.timezone.utc).isoformat())]
    from anankos_fobguard.store import Store
    store = Store(str(tmp_path / "db.sqlite"))
    eng = Engine(cfg, store=store, baseline=Baseline())
    eng.run_offline()
    assert any(d.kind == "replay" for d in eng.detections)
    assert store.recent_incidents()[0]["title"] == "Replayed key fob code"


# ------------------------------------------------------------------ config

def test_business_hours():
    cfg = Config()
    cfg.site.timezone = "UTC"
    assert cfg.site.is_open(OPEN)
    assert not cfg.site.is_open(CLOSED)
    sunday = dt.datetime(2026, 11, 29, 12, tzinfo=dt.timezone.utc).timestamp()
    assert not cfg.site.is_open(sunday)
    cfg.site.holidays = ["2026-11-25"]
    assert not cfg.site.is_open(OPEN)


def test_default_config_loads(monkeypatch, tmp_path):
    from importlib import resources
    monkeypatch.setenv("FOBGUARD_DASHBOARD_TOKEN", "s3cret")
    p = tmp_path / "c.toml"
    p.write_text(resources.files("anankos_fobguard").joinpath("default_config.toml").read_text())
    cfg = load_config(p)
    assert [m.name for m in cfg.monitors] == ["lot-315", "lot-433"]
    assert cfg.dashboard.token == "s3cret"
    monkeypatch.delenv("FOBGUARD_DASHBOARD_TOKEN")
    assert load_config(p).dashboard.token == ""      # unset var -> empty, never literal


# ------------------------------------------------------- alerts / dashboard

def test_webhook_alert_payload():
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = make_cfg("replay")
    cfg.alerts = AlertConfig(sinks=[SinkConfig("webhook", {"url": f"http://127.0.0.1:{srv.server_port}/",
                                                           "format": "slack"})])
    eng = Engine(cfg, baseline=Baseline())
    orig = eng.alerts.dispatch
    eng.alerts.dispatch = lambda inc, action, now, background=True: orig(inc, action, now, background=False)
    eng.run_offline(start_time=OPEN)
    srv.shutdown()
    assert got and "Replayed key fob code" in got[0]["text"]


def test_dashboard_api_and_token():
    from anankos_fobguard.dashboard import Dashboard

    eng = run_scenario("replay")
    dash = Dashboard(eng, "127.0.0.1", 0, token="tok")
    dash.start()
    base = f"http://127.0.0.1:{dash.httpd.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/status")
        assert e.value.code == 401
        st = json.load(urllib.request.urlopen(base + "/api/status?token=tok"))
        assert st["incidents"][0]["title"] == "Replayed key fob code"
        iid = st["incidents"][0]["id"]
        req = urllib.request.Request(f"{base}/api/incidents/{iid}/ack?token=tok", method="POST",
                                     data=json.dumps({"by": "Sam"}).encode())
        assert json.load(urllib.request.urlopen(req))["acknowledged_by"] == "Sam"
        html = urllib.request.urlopen(base + "/").read()
        assert b"FOBGUARD" in html
    finally:
        dash.stop()


def test_offline_multi_monitor_routing():
    cfg = make_cfg("replay")
    cfg.monitors.append(MonitorConfig(name="m2", source="sim", scenario="normal"))
    eng = Engine(cfg, baseline=Baseline())
    eng.run_offline(start_time=OPEN)
    assert eng.monitors["m1"].packets >= 2 and eng.monitors["m2"].packets >= 7
    assert {d.monitor for d in eng.detections if d.kind == "replay"} == {"m1"}


@pytest.mark.parametrize("seed", [11, 4242])
def test_rolljam_robust_across_rf_randomness(seed):
    from anankos_fobguard.simulate import scenario_rolljam

    eng = Engine(make_cfg(), baseline=Baseline())
    for t0, iq in scenario_rolljam(start_time=OPEN, seed=seed).blocks():
        eng.handle_block("m1", t0, iq)
    kinds = [d.kind for d in eng.detections]
    assert kinds.count("fob_during_jam") == 2      # both jammed presses seen beside the jammer
    assert eng.correlator.incidents[0].title == "RollJam-style capture and replay"


def test_rolljam_after_hours_realerts_when_attack_identified():
    eng = run_scenario("rolljam", start=CLOSED)
    titles = [t for _, t in eng.alerts.sent]
    # first "band jammed", then re-alert as "RollJam" even though both are CRITICAL
    assert titles[0] == "Key fob band jammed"
    assert titles[-1] == "RollJam-style capture and replay"
