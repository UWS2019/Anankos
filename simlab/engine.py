"""Simulation engine.

Responsibilities:
  * load the MITRE ATT&CK for ICS technique library,
  * validate and apply an attacker action (technique -> device mutation),
  * each tick, recompute the physical state of the town by propagating
    device state through the dependency graph (generation -> grid ->
    water -> hospital -> wider city),
  * score the civilian consequence and emit a timestamped event log.

Everything here is a pure in-memory model.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .town import fresh_town

DATA = Path(__file__).parent / "data"

# Nominal demand (MW) of loads that are not the three modeled facilities.
TOWN_BASE_DEMAND_MW = 90.0
WATER_DEMAND_MW = 8.0
HOSPITAL_DEMAND_MW = 5.0

# Water pressure dynamics (psi), nominal 60.
PRESSURE_NOMINAL = 60.0
PRESSURE_FALL = 9.0   # psi lost per tick with no pumping
PRESSURE_RISE = 6.0   # psi recovered per tick when pumping


class SimEngine:
    def __init__(self):
        self.techniques = self._load_techniques()
        self.reset()

    def _load_techniques(self):
        raw = json.loads((DATA / "techniques.json").read_text())
        return {t["id"]: t for t in raw["techniques"]}

    # -- lifecycle -----------------------------------------------------------
    def reset(self):
        self.town = fresh_town()
        self.tick = 0
        self.running = False
        self.events = []
        # live-computed town state:
        self.water_pressure = PRESSURE_NOMINAL
        self.derived = {}
        self._log("system", "SIM", "Simulation reset. Town nominal.", "info")
        self._recompute()

    def _log(self, source, tag, message, level="info", technique=None):
        self.events.append({
            "tick": self.tick,
            "ts": time.time(),
            "source": source,
            "tag": tag,
            "technique": technique,
            "level": level,
            "message": message,
        })
        # keep the log bounded
        if len(self.events) > 400:
            self.events = self.events[-400:]

    # -- attacker actions ----------------------------------------------------
    def execute(self, technique_id, device_id, params=None):
        """Apply an attacker technique against a simulated device."""
        params = params or {}
        tech = self.techniques.get(technique_id)
        if not tech:
            return {"ok": False, "error": f"Unknown technique {technique_id}"}
        dev = self.town["devices"].get(device_id)
        if not dev:
            return {"ok": False, "error": f"Unknown device {device_id}"}
        if dev["type"] not in tech["applies_to"]:
            return {"ok": False,
                    "error": f"{tech['name']} does not apply to a {dev['type']}"}

        fac = self.town["facilities"][dev["facility"]]
        if tech["requires_compromise"] and not fac["compromised"]:
            return {"ok": False,
                    "error": f"{fac['name']} not yet compromised - run an "
                             f"Initial Access technique first."}

        effect = tech["effect"]
        msg = self._apply_effect(effect, tech, fac, dev, params)
        self._log("attacker", tech["id"], msg, "attack", technique=tech["id"])
        self._recompute()
        return {"ok": True, "message": msg}

    def _apply_effect(self, effect, tech, fac, dev, params):
        name = dev["name"]
        if effect == "compromise":
            fac["compromised"] = True
            return f"{tech['name']}: gained control of {fac['name']} OT network."
        if effect == "shutdown":
            dev["online"] = False
            return f"{tech['name']}: {name} forced offline."
        if effect == "dos":
            dev["functional"] = False
            return f"{tech['name']}: {name} unresponsive (comms flooded)."
        if effect == "disable_safety":
            dev["safety"] = False
            return f"{tech['name']}: safety function on {name} defeated."
        if effect == "spoof":
            param = params.get("param", "value")
            value = params.get("value")
            dev["spoof"][param] = value
            return (f"{tech['name']}: HMI now reports {param}={value} for "
                    f"{name} regardless of real value.")
        if effect == "command":
            return self._apply_command(tech, dev, params)
        if effect in ("set_param", ):
            param = params.get("param")
            value = params.get("value")
            if param is None or param not in dev["state"]:
                return (f"{tech['name']}: no settable parameter '{param}' on "
                        f"{name}.")
            try:
                value = float(value)
            except (TypeError, ValueError):
                return f"{tech['name']}: invalid value for {param}."
            dev["state"][param] = value
            return f"{tech['name']}: {name} {param} set to {value}."
        return f"{tech['name']}: no effect handler."

    def _apply_command(self, tech, dev, params):
        cmd = params.get("command")
        s = dev["state"]
        if dev["id"] == "pp_bkr" and cmd == "open_breaker":
            s["closed"] = False
            return f"{tech['name']}: main breaker commanded OPEN - plant islanded from grid."
        if dev["id"] == "wt_pump" and cmd == "stop_pump":
            s["speed_pct"] = 0.0
            return f"{tech['name']}: high-lift pump commanded to STOP."
        if dev["id"] == "hp_ats" and cmd == "force_utility":
            s["mode"] = "locked_utility"
            s["on_generator"] = False
            return f"{tech['name']}: transfer switch locked to utility - generator inhibited."
        return f"{tech['name']}: command '{cmd}' not valid for {dev['name']}."

    # -- physics / cascade ---------------------------------------------------
    def step(self):
        """Advance one simulated minute."""
        self.tick += 1
        self._recompute(advance=True)

    def _val(self, dev, param):
        """Real device value (spoofing does not change physics)."""
        return dev["state"].get(param)

    def _recompute(self, advance=False):
        d = self.town["devices"]

        # 1) Power generation.
        gov, bkr = d["pp_gov"], d["pp_bkr"]
        gen = self._val(gov, "setpoint_mw") or 0.0
        gen = min(gen, self._val(gov, "max_mw") or 0.0)
        if not gov["online"] or not gov["functional"]:
            gen = 0.0
        if not bkr["online"] or not self._val(bkr, "closed"):
            gen = 0.0  # breaker open -> no power reaches the grid
        gen = max(0.0, gen)

        # 2) Demand + grid balance.
        pump = d["wt_pump"]
        pumping_wanted = pump["online"] and pump["functional"] and (self._val(pump, "speed_pct") or 0) > 0
        water_demand = WATER_DEMAND_MW if pumping_wanted else 0.0
        demand = TOWN_BASE_DEMAND_MW + water_demand + HOSPITAL_DEMAND_MW
        grid_ok = gen >= demand
        deficit = max(0.0, demand - gen)

        # 3) Water treatment.
        pump_powered = grid_ok and pump["online"] and pump["functional"]
        pumping = pump_powered and (self._val(pump, "speed_pct") or 0) > 0
        if advance:
            if pumping:
                self.water_pressure = min(
                    PRESSURE_NOMINAL,
                    self.water_pressure + PRESSURE_RISE * (self._val(pump, "speed_pct") / 100.0),
                )
            else:
                self.water_pressure = max(0.0, self.water_pressure - PRESSURE_FALL)
        pressure = self.water_pressure
        water_flowing = pressure >= 20.0

        dose = self._val(d["wt_dose"], "dose_ppm") or 0.0
        smin = self._val(d["wt_dose"], "safe_min")
        smax = self._val(d["wt_dose"], "safe_max")
        dose_safe = (smin <= dose <= smax) and d["wt_dose"]["safety"]
        if dose > smax:
            water_quality = "unsafe_chemical"
        elif dose < smin:
            water_quality = "unsafe_untreated"
        else:
            water_quality = "safe"

        # 4) Hospital.
        ats = d["hp_ats"]
        ats_ok = ats["online"] and ats["functional"]
        can_transfer = ats_ok and self._val(ats, "mode") == "auto"
        on_generator = False
        if not grid_ok:
            if can_transfer and (self._val(ats, "gen_fuel_min") or 0) > 0:
                on_generator = True
        if advance and on_generator:
            ats["state"]["gen_fuel_min"] = max(0.0, self._val(ats, "gen_fuel_min") - 1.0)
        fuel = self._val(ats, "gen_fuel_min") or 0.0
        hospital_powered = grid_ok or (on_generator and fuel > 0)
        ats["state"]["on_generator"] = on_generator

        o2 = self._val(d["hp_o2"], "o2_psi") or 0.0
        o2_min = self._val(d["hp_o2"], "safe_min")
        o2_max = self._val(d["hp_o2"], "safe_max")
        o2_ok = (o2_min <= o2 <= o2_max) and d["hp_o2"]["online"]
        hospital_water = water_flowing and water_quality == "safe"

        # 5) Civilian consequence scoring.
        lives = 0
        notes = []
        if not hospital_powered:
            lives += 45
            notes.append("hospital lost all power (ICU/OR/ventilators)")
        elif on_generator and fuel <= 20:
            lives += 18
            notes.append(f"hospital on backup generator, ~{int(fuel)} min fuel left")
        if not hospital_water:
            lives += 20
            notes.append("hospital water loss (sterilization/dialysis impaired)")
        if not o2_ok:
            lives += 25
            notes.append("medical O2 pressure out of safe range")
        if water_quality != "safe":
            lives += 15
            notes.append(f"public water {water_quality.replace('_', ' ')}")

        population_wo_power = 0
        if not grid_ok:
            # crude load-shed estimate for the sand table
            population_wo_power = min(100, int(deficit / demand * 100) + 60)

        severity = self._severity(lives, grid_ok, water_flowing, water_quality)

        # 6) District (ambient city) status for the sand table.
        districts = []
        for dist in self.town["districts"]:
            powered = grid_ok
            water = water_flowing and water_quality == "safe"
            districts.append({**dist, "powered": powered, "water": water})

        self.derived = {
            "tick": self.tick,
            "running": self.running,
            "generation_mw": round(gen, 1),
            "demand_mw": round(demand, 1),
            "deficit_mw": round(deficit, 1),
            "grid_ok": grid_ok,
            "water_pressure": round(pressure, 1),
            "water_flowing": water_flowing,
            "water_quality": water_quality,
            "dose_safe": dose_safe,
            "hospital_powered": hospital_powered,
            "hospital_on_generator": on_generator,
            "hospital_gen_fuel_min": round(fuel, 0),
            "hospital_water": hospital_water,
            "o2_ok": o2_ok,
            "lives_at_risk": lives,
            "population_without_power_pct": population_wo_power,
            "severity": severity,
            "consequence_notes": notes,
            "districts": districts,
        }

        # Edge-triggered consequence logging while running.
        if advance:
            self._log_consequences()

    def _severity(self, lives, grid_ok, water_flowing, water_quality):
        if lives >= 60 or (not grid_ok and not water_flowing):
            return "catastrophic"
        if lives >= 25 or water_quality != "safe":
            return "severe"
        if lives > 0 or not grid_ok or not water_flowing:
            return "degraded"
        return "nominal"

    _last_severity = "nominal"

    def _log_consequences(self):
        sev = self.derived["severity"]
        if sev != self._last_severity:
            level = {"nominal": "info", "degraded": "warn",
                     "severe": "alert", "catastrophic": "alert"}[sev]
            notes = "; ".join(self.derived["consequence_notes"]) or "systems recovering"
            self._log("town", "CONSEQUENCE",
                      f"Town status -> {sev.upper()}: {notes}", level)
            self._last_severity = sev

    # -- control / manual operator restore -----------------------------------
    def manual_set(self, device_id, param, value):
        dev = self.town["devices"].get(device_id)
        if not dev or param not in dev["state"]:
            return {"ok": False, "error": "no such device/param"}
        try:
            dev["state"][param] = float(value)
        except (TypeError, ValueError):
            dev["state"][param] = value
        self._log("operator", "RESTORE", f"{dev['name']} {param} set to {value}.", "info")
        self._recompute()
        return {"ok": True}

    def restore_device(self, device_id):
        dev = self.town["devices"].get(device_id)
        if not dev:
            return {"ok": False, "error": "no such device"}
        dev["online"] = True
        dev["functional"] = True
        dev["safety"] = True
        dev["spoof"] = {}
        self._log("operator", "RESTORE", f"{dev['name']} brought back online.", "info")
        self._recompute()
        return {"ok": True}

    # -- serialization -------------------------------------------------------
    def snapshot(self):
        return {
            "version": "0.1.0",
            "tick": self.tick,
            "running": self.running,
            "facilities": self.town["facilities"],
            "devices": self.town["devices"],
            "derived": self.derived,
            "events": self.events[-80:],
            "techniques": list(self.techniques.values()),
        }
