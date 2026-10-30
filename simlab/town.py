"""Town definition: the deeply-modeled facilities, their simulated devices,
the enterprise (IT) network that fronts each one, and the wider sand-table
city.

v1.1 deeply models four facilities wired through a dependency cascade:
    Power Plant -> Water Treatment -> Hospital
    Power Plant -> Fort Marin (military base)  [power, fuel farm, C2/radar,
                                                perimeter security]
Each facility also has an enterprise IT network device: the realistic
IT -> OT pivot path. Remaining districts (government, airport, arena,
manufacturing, residential) are ambient and react to grid / water / base
comms status.
"""
from __future__ import annotations

from copy import deepcopy


def _device(did, name, dtype, facility, state):
    return {
        "id": did,
        "name": name,
        "type": dtype,
        "facility": facility,
        "state": state,
        "spoof": {},
        "online": True,
        "functional": True,
        "safety": True,
    }


def _findings():
    """Assessment findings that mirror real on-the-ground conditions.
    Toggling these changes the viable attack path and is captured in the
    event log / exported report."""
    return {
        "internet_exposed": False,  # device reachable from the internet (Shodan-able)
        "flat_network": False,      # no IT/OT segmentation
        "default_creds": False,     # factory/default credentials in use
        "unpatched": False,         # known-vulnerable, unpatched firmware
    }


def build_town():
    devices = {
        # --- Power Plant -----------------------------------------------------
        "pp_it":  _device("pp_it",  "Plant Business Network", "IT",  "power", {}),
        "pp_gov": _device("pp_gov", "Turbine Governor PLC", "PLC", "power",
                          {"setpoint_mw": 110.0, "max_mw": 120.0}),
        "pp_bkr": _device("pp_bkr", "Main Breaker RTU", "RTU", "power", {"closed": True}),
        "pp_hmi": _device("pp_hmi", "Generation Control HMI", "HMI", "power", {}),
        # --- Water Treatment -------------------------------------------------
        "wt_it":   _device("wt_it",   "Utility Business Network", "IT", "water", {}),
        "wt_pump": _device("wt_pump", "High-Lift Pump VFD", "VFD", "water", {"speed_pct": 85.0}),
        "wt_dose": _device("wt_dose", "Chlorine Dosing PLC", "PLC", "water",
                          {"dose_ppm": 1.0, "safe_min": 0.2, "safe_max": 4.0}),
        "wt_hmi":  _device("wt_hmi", "SCADA HMI", "HMI", "water", {}),
        # --- Hospital --------------------------------------------------------
        "hp_it":  _device("hp_it",  "Hospital IT Network", "IT", "hospital", {}),
        "hp_ats": _device("hp_ats", "Automatic Transfer Switch", "ATS", "hospital",
                          {"mode": "auto", "on_generator": False, "gen_fuel_min": 240.0}),
        "hp_bms": _device("hp_bms", "Building HVAC / BMS PLC", "PLC", "hospital", {"temp_f": 71.0}),
        "hp_o2":  _device("hp_o2", "Medical Gas / O2 Monitor PLC", "PLC", "hospital",
                          {"o2_psi": 55.0, "safe_min": 45.0, "safe_max": 65.0}),
        # --- Fort Marin (military base) --------------------------------------
        "fb_it":    _device("fb_it",    "Base Admin Network", "IT",  "base", {}),
        "fb_sub":   _device("fb_sub",   "Base Substation ATS", "ATS", "base",
                            {"mode": "auto", "on_generator": False, "gen_fuel_min": 480.0}),
        "fb_fuel":  _device("fb_fuel",  "Fuel Farm PLC", "PLC", "base",
                            {"level_pct": 90.0}),
        "fb_c2":    _device("fb_c2",    "C2 / Radar Comms RTU", "RTU", "base",
                            {"link_up": 1.0}),
        "fb_perim": _device("fb_perim", "Perimeter Security PLC", "PLC", "base",
                            {"gates_locked": 1.0, "lighting": 1.0}),
    }

    def facility(fid, name, kind, dev_ids, x, y):
        return {
            "id": fid, "name": name, "kind": kind,
            "compromised": False,       # OT control gained
            "it_compromised": False,    # enterprise/IT foothold gained
            "findings": _findings(),
            "devices": dev_ids,
            "map": {"x": x, "y": y},
        }

    facilities = {
        "power":    facility("power", "Riverside Power Plant", "power",
                             ["pp_it", "pp_gov", "pp_bkr", "pp_hmi"], 120, 160),
        "water":    facility("water", "Northgate Water Treatment", "water",
                             ["wt_it", "wt_pump", "wt_dose", "wt_hmi"], 430, 110),
        "hospital": facility("hospital", "St. Marin General Hospital", "hospital",
                             ["hp_it", "hp_ats", "hp_bms", "hp_o2"], 720, 210),
        "base":     facility("base", "Fort Marin (Military Base)", "base",
                             ["fb_it", "fb_sub", "fb_fuel", "fb_c2", "fb_perim"], 910, 110),
    }

    districts = [
        {"id": "downtown",    "name": "Downtown / Government", "x": 540, "y": 340, "critical": True},
        {"id": "airport",     "name": "Marin Regional Airport", "x": 820, "y": 420, "critical": True},
        {"id": "stadium",     "name": "Civic Sports Arena",     "x": 300, "y": 400, "critical": False},
        {"id": "mfg",         "name": "Industrial / Mfg Park",  "x": 120, "y": 390, "critical": False},
        {"id": "residential", "name": "Residential Districts",  "x": 430, "y": 480, "critical": False},
    ]

    return {"devices": devices, "facilities": facilities, "districts": districts}


def fresh_town():
    return deepcopy(build_town())
