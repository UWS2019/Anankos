"""Simulation engine.

  * loads the technique library (Enterprise ATT&CK IT chain + ATT&CK for ICS),
  * validates an attacker action against the kill chain (it_access -> pivot ->
    ot_access -> impact) and applies its effect to a simulated device,
  * each tick, recomputes the physical state of the town by propagating device
    state through the dependency graph (generation -> grid -> water / hospital /
    Fort Marin -> wider city),
  * lets an assessor author the scenario (findings, device flags, parameters,
    full scenario export/import) so the lab can mirror real ground-truth
    conditions and show a client the resulting outcome,
  * scores the civilian + mission consequence and emits a tagged event log.

Everything here is a pure in-memory model.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .town import fresh_town

DATA = Path(__file__).parent / "data"

SEV_RANK = {"nominal": 0, "degraded": 1, "severe": 2, "catastrophic": 3}
MILESTONES = {
    "blackout": "Grid-wide power loss",
    "hospital_dark": "Hospital lost all power",
    "water_loss": "Public water supply lost",
    "water_unsafe": "Public water contaminated",
    "base_dark": "Fort Marin lost all power",
    "base_breach": "Fort Marin perimeter breached",
    "base_comms": "Fort Marin C2/radar down",
    "airport_down": "Airport radar/ATC down",
}

TOWN_BASE_DEMAND_MW = 90.0
WATER_DEMAND_MW = 8.0
HOSPITAL_DEMAND_MW = 5.0
BASE_DEMAND_MW = 6.0

PRESSURE_NOMINAL = 60.0
PRESSURE_FALL = 9.0
PRESSURE_RISE = 6.0


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
        self.water_pressure = PRESSURE_NOMINAL
        self._last_severity = "nominal"
        self.derived = {}
        # --- run tracking for assessment reporting ---
        self.started_at = time.time()
        self.peak_lives = 0
        self.worst_sev = "nominal"
        self.milestones = {}   # milestone key -> first tick observed
        self.derived = {}
        self._log("system", "SIM", "Simulation reset. Town nominal.", "info")
        self._recompute()

    def _log(self, source, tag, message, level="info", technique=None):
        self.events.append({
            "tick": self.tick, "ts": time.time(), "source": source,
            "tag": tag, "technique": technique, "level": level, "message": message,
        })
        if len(self.events) > 500:
            self.events = self.events[-500:]

    # -- attacker actions ----------------------------------------------------
    def execute(self, technique_id, device_id, params=None):
        params = params or {}
        tech = self.techniques.get(technique_id)
        if not tech:
            return {"ok": False, "error": f"Unknown technique {technique_id}"}
        dev = self.town["devices"].get(device_id)
        if not dev:
            return {"ok": False, "error": f"Unknown device {device_id}"}
        if dev["type"] not in tech["applies_to"]:
            return {"ok": False, "error": f"{tech['name']} does not apply to a {dev['type']}"}

        fac = self.town["facilities"][dev["facility"]]
        gate = self._check_requires(tech, fac)
        if gate:
            return {"ok": False, "error": gate}

        msg = self._apply_effect(tech["effect"], tech, fac, dev, params)
        self._log("attacker", tech["id"], msg, "attack", technique=tech["id"])
        self._recompute()
        return {"ok": True, "message": msg}

    def _check_requires(self, tech, fac):
        req = tech.get("requires", "none")
        name = fac["name"]
        exposed = fac["findings"].get("internet_exposed")
        if req == "none":
            return None
        if req == "it" and not fac["it_compromised"]:
            return f"No IT foothold on {name} - run an IT Initial Access technique first."
        if req == "it_or_exposed" and not (fac["it_compromised"] or exposed):
            return (f"No path to {name} OT network. Either gain an IT foothold "
                    f"(phishing / exploit) and pivot, or mark a device "
                    f"'internet exposed' in Findings.")
        if req == "exposed" and not exposed:
            return (f"{tech['name']} needs an internet-exposed device. Set the "
                    f"'internet exposed' finding on {name} to mirror that condition.")
        if req == "ot" and not fac["compromised"]:
            return f"{name} OT not yet compromised - gain OT access first."
        return None

    def _apply_effect(self, effect, tech, fac, dev, params):
        name = dev["name"]
        if effect == "compromise_it":
            fac["it_compromised"] = True
            return f"{tech['name']}: foothold on {fac['name']} IT network ({name})."
        if effect == "pivot":
            fac["compromised"] = True
            return f"{tech['name']}: pivoted IT->OT; control of {fac['name']} OT network gained."
        if effect == "compromise":
            fac["compromised"] = True
            return f"{tech['name']}: gained control of {fac['name']} OT network."
        if effect == "shutdown":
            dev["online"] = False
            return f"{tech['name']}: {name} forced offline."
        if effect == "dos":
            dev["functional"] = False
            return f"{tech['name']}: {name} unresponsive (comms denied)."
        if effect == "disable_safety":
            dev["safety"] = False
            return f"{tech['name']}: safety function on {name} defeated."
        if effect == "spoof":
            param = params.get("param", "value")
            value = params.get("value")
            dev["spoof"][param] = value
            return (f"{tech['name']}: HMI/report now shows {param}={value} for "
                    f"{name} regardless of real value.")
        if effect == "command":
            return self._apply_command(tech, dev, params)
        if effect == "set_param":
            param = params.get("param")
            if param is None or param not in dev["state"]:
                return f"{tech['name']}: no settable parameter '{param}' on {name}."
            try:
                dev["state"][param] = float(params.get("value"))
            except (TypeError, ValueError):
                return f"{tech['name']}: invalid value for {param}."
            return f"{tech['name']}: {name} {param} set to {dev['state'][param]}."
        return f"{tech['name']}: no effect handler."

    def _apply_command(self, tech, dev, params):
        cmd = params.get("command")
        s = dev["state"]
        table = {
            ("pp_bkr", "open_breaker"): ("closed", 0, "main breaker commanded OPEN - plant islanded from grid"),
            ("wt_pump", "stop_pump"): ("speed_pct", 0.0, "high-lift pump commanded to STOP"),
            ("hp_ats", "force_utility"): ("mode", "locked_utility", "transfer switch locked to utility - generator inhibited"),
            ("fb_sub", "force_utility"): ("mode", "locked_utility", "base transfer switch locked to utility - generator inhibited"),
            ("fb_perim", "unlock_gates"): ("gates_locked", 0.0, "perimeter gates commanded UNLOCKED"),
        }
        key = (dev["id"], cmd)
        if key in table:
            param, value, desc = table[key]
            s[param] = value
            if dev["id"] == "hp_ats" and cmd == "force_utility":
                s["on_generator"] = False
            return f"{tech['name']}: {desc}."
        return f"{tech['name']}: command '{cmd}' not valid for {dev['name']}."

    # -- physics / cascade ---------------------------------------------------
    def step(self):
        self.tick += 1
        self._recompute(advance=True)

    def _v(self, dev, param):
        return dev["state"].get(param)

    def _recompute(self, advance=False):
        d = self.town["devices"]

        # 1) Power generation.
        gov, bkr = d["pp_gov"], d["pp_bkr"]
        gen = self._v(gov, "setpoint_mw") or 0.0
        gen = min(gen, self._v(gov, "max_mw") or 0.0)
        if not gov["online"] or not gov["functional"]:
            gen = 0.0
        if not bkr["online"] or not self._v(bkr, "closed"):
            gen = 0.0
        gen = max(0.0, gen)

        # 2) Demand + grid balance.
        pump = d["wt_pump"]
        pumping_wanted = pump["online"] and pump["functional"] and (self._v(pump, "speed_pct") or 0) > 0
        water_demand = WATER_DEMAND_MW if pumping_wanted else 0.0
        demand = TOWN_BASE_DEMAND_MW + water_demand + HOSPITAL_DEMAND_MW + BASE_DEMAND_MW
        grid_ok = gen >= demand
        deficit = max(0.0, demand - gen)

        # 3) Water treatment.
        pump_powered = grid_ok and pump["online"] and pump["functional"]
        pumping = pump_powered and (self._v(pump, "speed_pct") or 0) > 0
        if advance:
            if pumping:
                self.water_pressure = min(PRESSURE_NOMINAL,
                    self.water_pressure + PRESSURE_RISE * (self._v(pump, "speed_pct") / 100.0))
            else:
                self.water_pressure = max(0.0, self.water_pressure - PRESSURE_FALL)
        pressure = self.water_pressure
        water_flowing = pressure >= 20.0

        dose = self._v(d["wt_dose"], "dose_ppm") or 0.0
        smin, smax = self._v(d["wt_dose"], "safe_min"), self._v(d["wt_dose"], "safe_max")
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
        can_transfer = ats_ok and self._v(ats, "mode") == "auto"
        on_generator = bool(not grid_ok and can_transfer and (self._v(ats, "gen_fuel_min") or 0) > 0)
        if advance and on_generator:
            ats["state"]["gen_fuel_min"] = max(0.0, self._v(ats, "gen_fuel_min") - 1.0)
        fuel = self._v(ats, "gen_fuel_min") or 0.0
        hospital_powered = grid_ok or (on_generator and fuel > 0)
        ats["state"]["on_generator"] = on_generator
        o2 = self._v(d["hp_o2"], "o2_psi") or 0.0
        o2_ok = (self._v(d["hp_o2"], "safe_min") <= o2 <= self._v(d["hp_o2"], "safe_max")) and d["hp_o2"]["online"]
        hospital_water = water_flowing and water_quality == "safe"

        # 5) Fort Marin (military base).
        base = self._recompute_base(d, grid_ok, advance)

        # 6) Civilian consequence scoring.
        lives, notes = 0, []
        if not hospital_powered:
            lives += 45; notes.append("hospital lost all power (ICU/OR/ventilators)")
        elif on_generator and fuel <= 20:
            lives += 18; notes.append(f"hospital on backup generator, ~{int(fuel)} min fuel left")
        if not hospital_water:
            lives += 20; notes.append("hospital water loss (sterilization/dialysis impaired)")
        if not o2_ok:
            lives += 25; notes.append("medical O2 pressure out of safe range")
        if water_quality != "safe":
            lives += 15; notes.append(f"public water {water_quality.replace('_', ' ')}")

        population_wo_power = min(100, int(deficit / demand * 100) + 60) if not grid_ok else 0
        severity = self._severity(lives, grid_ok, water_flowing, water_quality, base)

        districts = []
        for dist in self.town["districts"]:
            powered = grid_ok
            if dist["id"] == "airport":
                powered = grid_ok and base["comms"]  # radar/ATC depends on base C2
            districts.append({**dist, "powered": powered, "water": water_flowing and water_quality == "safe"})

        self.derived = {
            "tick": self.tick, "running": self.running,
            "generation_mw": round(gen, 1), "demand_mw": round(demand, 1),
            "deficit_mw": round(deficit, 1), "grid_ok": grid_ok,
            "water_pressure": round(pressure, 1), "water_flowing": water_flowing,
            "water_quality": water_quality, "dose_safe": dose_safe,
            "hospital_powered": hospital_powered, "hospital_on_generator": on_generator,
            "hospital_gen_fuel_min": round(fuel, 0), "hospital_water": hospital_water,
            "o2_ok": o2_ok, "lives_at_risk": lives,
            "population_without_power_pct": population_wo_power,
            "severity": severity, "consequence_notes": notes,
            "base": base, "districts": districts,
        }
        self._track()
        if advance:
            self._log_consequences()

    def _track(self):
        d = self.derived
        self.peak_lives = max(self.peak_lives, d["lives_at_risk"])
        if SEV_RANK[d["severity"]] > SEV_RANK[self.worst_sev]:
            self.worst_sev = d["severity"]
        airport = next((x for x in d["districts"] if x["id"] == "airport"), {})
        conds = {
            "blackout": not d["grid_ok"],
            "hospital_dark": not d["hospital_powered"],
            "water_loss": not d["water_flowing"],
            "water_unsafe": d["water_quality"] != "safe",
            "base_dark": d["base"]["readiness"] == "dark",
            "base_breach": d["base"]["breach"],
            "base_comms": not d["base"]["comms"],
            "airport_down": not airport.get("powered", True),
        }
        for k, on in conds.items():
            if on and k not in self.milestones:
                self.milestones[k] = self.tick

    def _recompute_base(self, d, grid_ok, advance):
        sub, fuel, c2, perim = d["fb_sub"], d["fb_fuel"], d["fb_c2"], d["fb_perim"]
        ats_ok = sub["online"] and sub["functional"]
        fuel_ok = fuel["online"] and fuel["functional"] and (self._v(fuel, "level_pct") or 0) > 0
        on_gen = bool(not grid_ok and ats_ok and self._v(sub, "mode") == "auto"
                      and (self._v(sub, "gen_fuel_min") or 0) > 0 and fuel_ok)
        if advance and on_gen:
            sub["state"]["gen_fuel_min"] = max(0.0, self._v(sub, "gen_fuel_min") - 1.0)
            fuel["state"]["level_pct"] = max(0.0, self._v(fuel, "level_pct") - 0.5)
        sub["state"]["on_generator"] = on_gen
        powered = grid_ok or (on_gen and (self._v(sub, "gen_fuel_min") or 0) > 0)

        comms = bool(powered and c2["online"] and c2["functional"] and self._v(c2, "link_up") == 1)
        gates_locked = self._v(perim, "gates_locked") == 1 and perim["safety"]
        secure = bool(powered and perim["online"] and perim["functional"] and gates_locked)
        breach = bool(perim["online"] and (self._v(perim, "gates_locked") == 0 or not perim["safety"]))

        notes = []
        if not powered:
            readiness = "dark"; notes.append("base lost all power")
        elif breach or not comms:
            readiness = "compromised"
            if not comms: notes.append("C2/radar link down (airspace & comms blind)")
            if breach: notes.append("perimeter breach (gates unlocked)")
        elif on_gen or not secure:
            readiness = "degraded"
            if on_gen: notes.append(f"base on backup generator, fuel farm {int(self._v(fuel,'level_pct'))}%")
            if not secure: notes.append("perimeter security degraded")
        else:
            readiness = "full"
        return {
            "powered": powered, "on_generator": on_gen,
            "gen_fuel_min": round(self._v(sub, "gen_fuel_min") or 0, 0),
            "fuel_level_pct": round(self._v(fuel, "level_pct") or 0, 0),
            "comms": comms, "perimeter_secure": secure, "breach": breach,
            "readiness": readiness, "notes": notes,
        }

    def _severity(self, lives, grid_ok, water_flowing, water_quality, base):
        base_bad = base["readiness"] in ("compromised", "dark")
        if lives >= 60 or (not grid_ok and not water_flowing) or base["readiness"] == "dark":
            return "catastrophic"
        if lives >= 25 or water_quality != "safe" or base_bad:
            return "severe"
        if lives > 0 or not grid_ok or not water_flowing or base["readiness"] == "degraded":
            return "degraded"
        return "nominal"

    def _log_consequences(self):
        sev = self.derived["severity"]
        if sev != self._last_severity:
            level = {"nominal": "info", "degraded": "warn", "severe": "alert", "catastrophic": "alert"}[sev]
            notes = "; ".join(self.derived["consequence_notes"] + self.derived["base"]["notes"]) or "systems recovering"
            self._log("town", "CONSEQUENCE", f"Town status -> {sev.upper()}: {notes}", level)
            self._last_severity = sev

    # -- assessor authoring (mirror ground truth) ----------------------------
    def set_finding(self, facility_id, key, value):
        fac = self.town["facilities"].get(facility_id)
        if not fac or key not in fac["findings"]:
            return {"ok": False, "error": "no such facility/finding"}
        fac["findings"][key] = bool(value)
        label = key.replace("_", " ")
        self._log("assessor", "FINDING",
                  f"{fac['name']}: finding '{label}' set {'ON' if value else 'off'}.",
                  "warn" if value else "info")
        self._recompute()
        return {"ok": True}

    def set_device_flag(self, device_id, flag, value):
        dev = self.town["devices"].get(device_id)
        if not dev or flag not in ("online", "functional", "safety"):
            return {"ok": False, "error": "no such device/flag"}
        dev[flag] = bool(value)
        self._log("assessor", "AUTHOR", f"{dev['name']} {flag}={bool(value)}.", "info")
        self._recompute()
        return {"ok": True}

    def manual_set(self, device_id, param, value):
        dev = self.town["devices"].get(device_id)
        if not dev or param not in dev["state"]:
            return {"ok": False, "error": "no such device/param"}
        try:
            dev["state"][param] = float(value)
        except (TypeError, ValueError):
            dev["state"][param] = value
        self._log("assessor", "AUTHOR", f"{dev['name']} {param} set to {value}.", "info")
        self._recompute()
        return {"ok": True}

    def restore_device(self, device_id):
        dev = self.town["devices"].get(device_id)
        if not dev:
            return {"ok": False, "error": "no such device"}
        dev["online"] = dev["functional"] = dev["safety"] = True
        dev["spoof"] = {}
        self._log("operator", "RESTORE", f"{dev['name']} brought back online.", "info")
        self._recompute()
        return {"ok": True}

    # -- scenario export / import --------------------------------------------
    def export_scenario(self):
        return {
            "version": "0.2.0",
            "water_pressure": self.water_pressure,
            "facilities": {fid: {
                "it_compromised": f["it_compromised"],
                "compromised": f["compromised"],
                "findings": f["findings"],
            } for fid, f in self.town["facilities"].items()},
            "devices": {did: {
                "state": dev["state"], "online": dev["online"],
                "functional": dev["functional"], "safety": dev["safety"],
                "spoof": dev["spoof"],
            } for did, dev in self.town["devices"].items()},
        }

    def import_scenario(self, data):
        self.reset()
        try:
            for fid, fd in (data.get("facilities") or {}).items():
                fac = self.town["facilities"].get(fid)
                if not fac:
                    continue
                fac["it_compromised"] = bool(fd.get("it_compromised", False))
                fac["compromised"] = bool(fd.get("compromised", False))
                for k, v in (fd.get("findings") or {}).items():
                    if k in fac["findings"]:
                        fac["findings"][k] = bool(v)
            for did, dd in (data.get("devices") or {}).items():
                dev = self.town["devices"].get(did)
                if not dev:
                    continue
                for k, v in (dd.get("state") or {}).items():
                    if k in dev["state"]:
                        dev["state"][k] = v
                dev["online"] = bool(dd.get("online", True))
                dev["functional"] = bool(dd.get("functional", True))
                dev["safety"] = bool(dd.get("safety", True))
                dev["spoof"] = dict(dd.get("spoof") or {})
            if "water_pressure" in data:
                self.water_pressure = float(data["water_pressure"])
        except Exception as e:  # noqa: BLE001 - author-supplied data, keep sim alive
            return {"ok": False, "error": f"scenario import error: {e}"}
        self._log("assessor", "SCENARIO", "Scenario imported (mirrors assessment findings).", "warn")
        self._recompute()
        return {"ok": True}

    # -- assessment report ---------------------------------------------------
    def build_report(self):
        d = self.derived
        # attack narrative: ordered attacker + protocol actions
        narrative = [e for e in self.events if e["source"] in ("attacker", "protocol")]
        # ATT&CK coverage (console techniques only carry an id)
        coverage = {}
        for e in narrative:
            tid = e.get("technique")
            if not tid:
                continue
            t = self.techniques.get(tid, {})
            coverage.setdefault(tid, {"id": tid, "name": t.get("name", tid),
                                      "tactic": t.get("tactic", "—"), "domain": t.get("domain", "—"),
                                      "count": 0})
            coverage[tid]["count"] += 1
        protocol_writes = sum(1 for e in narrative if e["source"] == "protocol")
        severity_timeline = [{"tick": e["tick"], "message": e["message"]}
                             for e in self.events if e["tag"] == "CONSEQUENCE"]
        facilities = []
        for fid, f in self.town["facilities"].items():
            active = [k.replace("_", " ") for k, v in f["findings"].items() if v]
            facilities.append({
                "id": fid, "name": f["name"], "kind": f["kind"],
                "it_compromised": f["it_compromised"], "compromised": f["compromised"],
                "findings": active,
            })
        milestones = [{"key": k, "label": MILESTONES[k], "tick": self.milestones[k]}
                      for k in sorted(self.milestones, key=lambda x: self.milestones[x])]
        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "town": "Marin",
            "duration_min": self.tick,
            "worst_severity": self.worst_sev,
            "peak_lives_at_risk": self.peak_lives,
            "current": {
                "severity": d["severity"], "lives_at_risk": d["lives_at_risk"],
                "grid_ok": d["grid_ok"], "generation_mw": d["generation_mw"],
                "demand_mw": d["demand_mw"], "water_pressure": d["water_pressure"],
                "water_quality": d["water_quality"], "hospital_powered": d["hospital_powered"],
                "population_without_power_pct": d["population_without_power_pct"],
                "base_readiness": d["base"]["readiness"],
                "consequence_notes": d["consequence_notes"] + d["base"]["notes"],
            },
            "milestones": milestones,
            "narrative": [{"tick": e["tick"], "source": e["source"], "technique": e.get("technique"),
                           "tag": e["tag"], "message": e["message"]} for e in narrative],
            "attack_count": len(narrative),
            "protocol_writes": protocol_writes,
            "coverage": sorted(coverage.values(), key=lambda x: x["id"]),
            "facilities": facilities,
            "severity_timeline": severity_timeline,
        }

    # -- serialization -------------------------------------------------------
    def snapshot(self):
        return {
            "version": "0.2.0", "tick": self.tick, "running": self.running,
            "facilities": self.town["facilities"], "devices": self.town["devices"],
            "derived": self.derived, "events": self.events[-90:],
            "techniques": list(self.techniques.values()),
        }
