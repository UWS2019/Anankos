"""Town definition: the three deeply-modeled facilities, their simulated
devices, and the layout of the wider sand-table city.

v1 deeply models Power Plant -> Water Treatment -> Hospital. The remaining
districts (government, airport, stadium, manufacturing, military base,
residential) are ambient: they are not individually controllable yet, but
they react to grid and water status so the city visibly shows consequences.
"""
from __future__ import annotations

from copy import deepcopy


# --- Simulated devices -------------------------------------------------------
# Each device is a plain dict. `state` holds the "real" (physical) values.
# `spoof` holds operator-facing overrides injected by T0856 (Spoof Reporting).
# `online` False = powered off / shut down. `functional` False = DoS'd.
# `safety` False = safety function defeated (T0880).

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


def build_town():
    """Return a fresh, pristine town state dict."""
    devices = {
        # --- Power Plant -----------------------------------------------------
        "pp_gov": _device(
            "pp_gov", "Turbine Governor PLC", "PLC", "power",
            {"setpoint_mw": 110.0, "max_mw": 120.0},
        ),
        "pp_bkr": _device(
            "pp_bkr", "Main Breaker RTU", "RTU", "power",
            {"closed": True},
        ),
        "pp_hmi": _device(
            "pp_hmi", "Generation Control HMI", "HMI", "power",
            {},
        ),
        # --- Water Treatment -------------------------------------------------
        "wt_pump": _device(
            "wt_pump", "High-Lift Pump VFD", "VFD", "water",
            {"speed_pct": 85.0},
        ),
        "wt_dose": _device(
            "wt_dose", "Chlorine Dosing PLC", "PLC", "water",
            {"dose_ppm": 1.0, "safe_min": 0.2, "safe_max": 4.0},
        ),
        "wt_hmi": _device(
            "wt_hmi", "SCADA HMI", "HMI", "water",
            {},
        ),
        # --- Hospital --------------------------------------------------------
        "hp_ats": _device(
            "hp_ats", "Automatic Transfer Switch", "ATS", "hospital",
            {"mode": "auto", "on_generator": False, "gen_fuel_min": 240.0},
        ),
        "hp_bms": _device(
            "hp_bms", "Building HVAC / BMS PLC", "PLC", "hospital",
            {"temp_f": 71.0},
        ),
        "hp_o2": _device(
            "hp_o2", "Medical Gas / O2 Monitor PLC", "PLC", "hospital",
            {"o2_psi": 55.0, "safe_min": 45.0, "safe_max": 65.0},
        ),
    }

    facilities = {
        "power": {
            "id": "power", "name": "Riverside Power Plant",
            "kind": "power", "compromised": False,
            "devices": ["pp_gov", "pp_bkr", "pp_hmi"],
            "map": {"x": 120, "y": 150},
        },
        "water": {
            "id": "water", "name": "Northgate Water Treatment",
            "kind": "water", "compromised": False,
            "devices": ["wt_pump", "wt_dose", "wt_hmi"],
            "map": {"x": 430, "y": 110},
        },
        "hospital": {
            "id": "hospital", "name": "St. Marin General Hospital",
            "kind": "hospital", "compromised": False,
            "devices": ["hp_ats", "hp_bms", "hp_o2"],
            "map": {"x": 740, "y": 190},
        },
    }

    # Ambient districts: react to grid/water but are not individually modeled.
    districts = [
        {"id": "downtown",   "name": "Downtown / Government", "x": 560, "y": 330, "critical": True},
        {"id": "airport",    "name": "Marin Regional Airport", "x": 820, "y": 410, "critical": True},
        {"id": "stadium",    "name": "Civic Sports Arena",     "x": 300, "y": 400, "critical": False},
        {"id": "mfg",        "name": "Industrial / Mfg Park",  "x": 120, "y": 380, "critical": False},
        {"id": "base",       "name": "Fort Marin (Mil. Base)", "x": 900, "y": 150, "critical": True},
        {"id": "residential","name": "Residential Districts",  "x": 440, "y": 470, "critical": False},
    ]

    return {
        "devices": devices,
        "facilities": facilities,
        "districts": districts,
    }


def fresh_town():
    return deepcopy(build_town())
