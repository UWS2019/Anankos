"""Topology: the town as a data-driven graph of archetype nodes + typed links,
instead of hardcoded per-facility physics. This is the foundation the future
drag-and-drop builder edits; Phase 1 ships the Marin topology as data and runs
it through the generic solver.

A node binds an archetype to a set of simulated devices (via `dmap`: role ->
device id) and parameters. A link carries one resource from a source node to a
destination node under a `role` the destination reads it by. `facility` groups
nodes for the dashboard/report (which still present facility-level state).
"""
from __future__ import annotations

from copy import deepcopy


def build_marin_topology():
    nodes = [
        {"id": "gen", "arch": "generator", "facility": "power",
         "dmap": {"gov": "pp_gov", "bkr": "pp_bkr"}, "params": {}},
        {"id": "grid", "arch": "grid_bus", "facility": "power",
         "dmap": {}, "params": {"base_load": 90.0}},
        {"id": "telecom", "arch": "comms_backbone", "facility": "telecom",
         "dmap": {"core": "tc_core", "fiber": "tc_fiber", "mw": "tc_mw"},
         "params": {"demand": 3.0}},
        {"id": "water_pump", "arch": "pump", "facility": "water",
         "dmap": {"vfd": "wt_pump"}, "params": {"demand": 8.0}},
        {"id": "water_pressure", "arch": "pressure_tank", "facility": "water",
         "dmap": {}, "params": {"nominal": 60.0, "rise": 6.0, "fall": 9.0}},
        {"id": "water_quality", "arch": "treatment", "facility": "water",
         "dmap": {"dose": "wt_dose"}, "params": {}},
        {"id": "hospital", "arch": "hospital_sector", "facility": "hospital",
         "dmap": {"ats": "hp_ats", "o2": "hp_o2"}, "params": {"demand": 5.0}},
        {"id": "base", "arch": "base_sector", "facility": "base",
         "dmap": {"sub": "fb_sub", "fuel": "fb_fuel", "c2": "fb_c2", "perim": "fb_perim"},
         "params": {"demand": 6.0}},
        {"id": "airport", "arch": "airport_sector", "facility": "airport",
         "dmap": {"radar": "ap_radar", "nav": "ap_nav", "tower": "ap_tower"},
         "params": {"demand": 4.0}},
    ]
    links = [
        {"src": "gen", "dst": "grid", "role": "supply", "resource": "power"},
        {"src": "grid", "dst": "telecom", "role": "grid", "resource": "power"},
        {"src": "grid", "dst": "water_pump", "role": "grid", "resource": "power"},
        {"src": "water_pump", "dst": "water_pressure", "role": "pump", "resource": "water"},
        {"src": "grid", "dst": "hospital", "role": "grid", "resource": "power"},
        {"src": "water_pressure", "dst": "hospital", "role": "water_press", "resource": "water"},
        {"src": "water_quality", "dst": "hospital", "role": "water_qual", "resource": "water"},
        {"src": "grid", "dst": "base", "role": "grid", "resource": "power"},
        {"src": "telecom", "dst": "base", "role": "transport", "resource": "comms"},
        {"src": "grid", "dst": "airport", "role": "grid", "resource": "power"},
        {"src": "telecom", "dst": "airport", "role": "transport", "resource": "comms"},
        {"src": "base", "dst": "airport", "role": "base_comms", "resource": "comms"},
    ]
    return {"nodes": nodes, "links": links}


def fresh_topology():
    return deepcopy(build_marin_topology())
