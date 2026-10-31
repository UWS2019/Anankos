"""Physics regression test for the topology engine.

Replays a battery of deterministic scenarios (manipulating device state
directly to isolate the physical cascade from the access/defense layers) and
asserts the engine's `derived` output matches the committed golden fixture
(tests/physics_golden.json). The golden was captured from the pre-topology
engine, so this guards the data-driven solver against regressions.

Run:  python tests/parity.py
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simlab.engine import SimEngine  # noqa: E402

GOLDEN = Path(__file__).parent / "physics_golden.json"


def snap(e):
    d = copy.deepcopy(e.derived)
    d.pop("running", None)
    d.pop("present", None)  # additive presence metadata, not physics
    # absent-facility blocks carry a 'present' flag that is True for Marin
    for k in ("base", "airport", "telecom"):
        if isinstance(d.get(k), dict):
            d[k].pop("present", None)
    return d


def dev(e, did):
    return e.town["devices"][did]


def build_results():
    results = {}

    def run(name, setup, steps):
        e = SimEngine()
        setup(e)
        e._recompute()
        seq = [snap(e)]
        for _ in range(steps):
            e.step()
            seq.append(snap(e))
        results[name] = seq

    run("baseline", lambda e: None, 5)
    run("chlorine", lambda e: dev(e, "wt_dose")["state"].__setitem__("dose_ppm", 15.0), 3)
    run("breaker_open", lambda e: dev(e, "pp_bkr")["state"].__setitem__("closed", 0), 6)
    run("fiber_cut", lambda e: dev(e, "tc_fiber")["state"].__setitem__("link_up", 0), 2)
    run("cell_jam", lambda e: dev(e, "tc_cell")["state"].__setitem__("link_up", 0), 2)
    run("gps_spoof", lambda e: dev(e, "tc_gps")["state"].__setitem__("timing_ok", 0), 2)

    def s3b(e):
        dev(e, "tc_fiber")["state"]["link_up"] = 0
        dev(e, "tc_mw")["state"]["link_up"] = 0
        dev(e, "tc_cell")["state"]["link_up"] = 0
    run("telecom_down", s3b, 2)
    run("radar_stop", lambda e: dev(e, "ap_radar")["state"].__setitem__("sweep", 0), 2)
    run("tower_off", lambda e: dev(e, "ap_tower")["state"].__setitem__("freq_active", 0), 2)

    def s5(e):
        e.defenses_on.add("D3-SIS")
        dev(e, "wt_dose")["state"]["dose_ppm"] = 15.0
        dev(e, "hp_o2")["state"]["o2_psi"] = 10.0
    run("sis_clamp", s5, 2)

    def s6(e):
        dev(e, "fb_fuel")["state"]["level_pct"] = 0
        dev(e, "pp_bkr")["state"]["closed"] = 0
        dev(e, "fb_perim")["state"]["gates_locked"] = 0
    run("base_dark_breach", s6, 4)
    run("pump_stop", lambda e: dev(e, "wt_pump")["state"].__setitem__("speed_pct", 0), 8)

    def s8(e):
        dev(e, "wt_pump")["functional"] = False
        dev(e, "hp_o2")["online"] = False
    run("flags", s8, 3)
    run("low_setpoint", lambda e: dev(e, "pp_gov")["state"].__setitem__("setpoint_mw", 100.0), 3)
    return results


def main():
    golden = json.load(open(GOLDEN))
    results = build_results()
    diffs = 0
    for name, seq in golden.items():
        if name not in results:
            print(f"MISSING scenario {name}"); diffs += 1; continue
        for i, (g, r) in enumerate(zip(seq, results[name])):
            if g != r:
                diffs += 1
                changed = {k: (g.get(k), r.get(k)) for k in g if g.get(k) != r.get(k)}
                print(f"DIFF {name}[{i}]: {changed}")
    if diffs:
        print(f"\nFAIL: {diffs} difference(s) from golden")
        sys.exit(1)
    total = sum(len(v) for v in golden.values())
    print(f"PASS: {len(golden)} scenarios, {total} snapshots match golden ✓")


if __name__ == "__main__":
    main()
