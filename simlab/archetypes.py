"""Archetype library for the data-driven topology engine (Phase 1).

Each archetype is a reusable node behavior: it declares an optional grid-
independent electrical `demand`, and an `evaluate` that computes the node's
outputs from its bound devices, parameters, and upstream inputs. The generic
solver (solver.py) wires archetypes together from a topology (topology.py); the
consequence/scoring layer that turns node states into civilian impact stays in
engine.py.

Archetypes read/write the SAME simulated device objects the attacker
manipulates (town.py), so the control surface, Modbus map, findings, and
ATT&CK/D3FEND layers are unchanged. This file reproduces the behavior that was
previously hand-written in engine._recompute_*; the win is that behavior is now
composable per node instead of hardcoded per facility.
"""
from __future__ import annotations


class Ctx:
    """Evaluation context: device access + global flags, bound to the engine."""
    def __init__(self, engine):
        self.e = engine

    def dev(self, did):
        return self.e.town["devices"][did]

    @staticmethod
    def v(dev, param):
        return dev["state"].get(param)

    @property
    def sis(self):
        return self.e._sis_on()


def _clamp(x, lo, hi):
    return min(max(x, lo), hi)


# --- demand hooks (grid-independent electrical load, MW) --------------------
def _demand_const(ctx, node):
    return float(node["params"].get("demand", 0.0))


def _demand_grid(ctx, node):
    return float(node["params"].get("base_load", 0.0))


def _demand_pump(ctx, node):
    pump = ctx.dev(node["dmap"]["vfd"])
    wanted = pump["online"] and pump["functional"] and (ctx.v(pump, "speed_pct") or 0) > 0
    return float(node["params"].get("demand", 0.0)) if wanted else 0.0


# --- evaluate hooks ---------------------------------------------------------
def _eval_generator(ctx, node, inp, advance):
    gov, bkr = ctx.dev(node["dmap"]["gov"]), ctx.dev(node["dmap"]["bkr"])
    gen = ctx.v(gov, "setpoint_mw") or 0.0
    gen = min(gen, ctx.v(gov, "max_mw") or 0.0)
    if not gov["online"] or not gov["functional"]:
        gen = 0.0
    if not bkr["online"] or not ctx.v(bkr, "closed"):
        gen = 0.0
    return {"power_mw": max(0.0, gen)}


def _eval_grid(ctx, node, inp, advance):
    gen = inp["supply"]["power_mw"]
    demand = inp["_demand"]
    grid_ok = gen >= demand
    return {"gen_mw": round(gen, 1), "demand_mw": round(demand, 1),
            "deficit": round(max(0.0, demand - gen), 1), "grid_ok": grid_ok}


def _eval_comms_backbone(ctx, node, inp, advance):
    grid_ok = inp["grid"]["grid_ok"]
    core, fiber, mw = ctx.dev(node["dmap"]["core"]), ctx.dev(node["dmap"]["fiber"]), ctx.dev(node["dmap"]["mw"])
    powered = grid_ok
    routing = bool(core["online"] and core["functional"] and ctx.v(core, "routing") == 1)
    fiber_up = bool(powered and routing and fiber["online"] and fiber["functional"] and ctx.v(fiber, "link_up") == 1)
    mw_up = bool(powered and routing and mw["online"] and mw["functional"] and ctx.v(mw, "link_up") == 1)
    up = fiber_up or mw_up
    notes = []
    if not up:
        if not powered:
            notes.append("telecom central office lost power")
        elif not routing:
            notes.append("core router down - town-wide transport lost")
        else:
            notes.append("both fiber and microwave paths down - operators blind across sectors")
    elif not fiber_up:
        notes.append("fiber path down - running on microwave backup")
    elif not mw_up:
        notes.append("microwave path down - fiber primary intact")
    return {"powered": powered, "routing": routing, "fiber_up": fiber_up,
            "mw_up": mw_up, "up": up, "notes": notes}


def _eval_pump(ctx, node, inp, advance):
    pump = ctx.dev(node["dmap"]["vfd"])
    grid_ok = inp["grid"]["grid_ok"]
    powered = grid_ok and pump["online"] and pump["functional"]
    speed = ctx.v(pump, "speed_pct") or 0.0
    pumping = powered and speed > 0
    return {"powered": powered, "pumping": pumping, "speed": speed}


def _eval_pressure_tank(ctx, node, inp, advance):
    p = node["params"]
    pumping = inp["pump"]["pumping"]
    speed = inp["pump"]["speed"]
    if advance:
        if pumping:
            ctx.e.water_pressure = min(p["nominal"], ctx.e.water_pressure + p["rise"] * (speed / 100.0))
        else:
            ctx.e.water_pressure = max(0.0, ctx.e.water_pressure - p["fall"])
    pressure = ctx.e.water_pressure
    return {"pressure": round(pressure, 1), "flowing": pressure >= 20.0}


def _eval_treatment(ctx, node, inp, advance):
    dose_dev = ctx.dev(node["dmap"]["dose"])
    dose = ctx.v(dose_dev, "dose_ppm") or 0.0
    smin, smax = ctx.v(dose_dev, "safe_min"), ctx.v(dose_dev, "safe_max")
    sis = ctx.sis
    if sis:
        dose = _clamp(dose, smin, smax)
    dose_safe = (smin <= dose <= smax) and (dose_dev["safety"] or sis)
    if dose > smax:
        quality = "unsafe_chemical"
    elif dose < smin:
        quality = "unsafe_untreated"
    else:
        quality = "safe"
    return {"quality": quality, "dose_safe": dose_safe}


def _eval_hospital_sector(ctx, node, inp, advance):
    ats, o2 = ctx.dev(node["dmap"]["ats"]), ctx.dev(node["dmap"]["o2"])
    grid_ok = inp["grid"]["grid_ok"]
    flowing = inp["water_press"]["flowing"]
    quality = inp["water_qual"]["quality"]
    ats_ok = ats["online"] and ats["functional"]
    can_transfer = ats_ok and ctx.v(ats, "mode") == "auto"
    on_gen = bool(not grid_ok and can_transfer and (ctx.v(ats, "gen_fuel_min") or 0) > 0)
    if advance and on_gen:
        ats["state"]["gen_fuel_min"] = max(0.0, ctx.v(ats, "gen_fuel_min") - 1.0)
    fuel = ctx.v(ats, "gen_fuel_min") or 0.0
    powered = grid_ok or (on_gen and fuel > 0)
    ats["state"]["on_generator"] = on_gen
    o2v = ctx.v(o2, "o2_psi") or 0.0
    o2min, o2max = ctx.v(o2, "safe_min"), ctx.v(o2, "safe_max")
    if ctx.sis:
        o2v = _clamp(o2v, o2min, o2max)
    o2_ok = (o2min <= o2v <= o2max) and o2["online"]
    return {"powered": powered, "on_generator": on_gen, "gen_fuel_min": round(fuel, 0),
            "o2_ok": o2_ok, "hospital_water": flowing and quality == "safe"}


def _eval_base_sector(ctx, node, inp, advance):
    sub, fuel, c2, perim = (ctx.dev(node["dmap"]["sub"]), ctx.dev(node["dmap"]["fuel"]),
                            ctx.dev(node["dmap"]["c2"]), ctx.dev(node["dmap"]["perim"]))
    grid_ok = inp["grid"]["grid_ok"]
    transport_up = inp["transport"]["up"]
    ats_ok = sub["online"] and sub["functional"]
    fuel_ok = fuel["online"] and fuel["functional"] and (ctx.v(fuel, "level_pct") or 0) > 0
    on_gen = bool(not grid_ok and ats_ok and ctx.v(sub, "mode") == "auto"
                  and (ctx.v(sub, "gen_fuel_min") or 0) > 0 and fuel_ok)
    if advance and on_gen:
        sub["state"]["gen_fuel_min"] = max(0.0, ctx.v(sub, "gen_fuel_min") - 1.0)
        fuel["state"]["level_pct"] = max(0.0, ctx.v(fuel, "level_pct") - 0.5)
    sub["state"]["on_generator"] = on_gen
    powered = grid_ok or (on_gen and (ctx.v(sub, "gen_fuel_min") or 0) > 0)
    comms = bool(powered and c2["online"] and c2["functional"]
                 and ctx.v(c2, "link_up") == 1 and transport_up)
    gates_locked = ctx.v(perim, "gates_locked") == 1 and perim["safety"]
    secure = bool(powered and perim["online"] and perim["functional"] and gates_locked)
    breach = bool(perim["online"] and (ctx.v(perim, "gates_locked") == 0 or not perim["safety"]))
    notes = []
    if not powered:
        readiness = "dark"; notes.append("base lost all power")
    elif breach or not comms:
        readiness = "compromised"
        if not comms:
            notes.append("C2/radar link down (airspace & comms blind)")
        if breach:
            notes.append("perimeter breach (gates unlocked)")
    elif on_gen or not secure:
        readiness = "degraded"
        if on_gen:
            notes.append(f"base on backup generator, fuel farm {int(ctx.v(fuel, 'level_pct'))}%")
        if not secure:
            notes.append("perimeter security degraded")
    else:
        readiness = "full"
    return {"powered": powered, "on_generator": on_gen,
            "gen_fuel_min": round(ctx.v(sub, "gen_fuel_min") or 0, 0),
            "fuel_level_pct": round(ctx.v(fuel, "level_pct") or 0, 0),
            "comms": comms, "perimeter_secure": secure, "breach": breach,
            "readiness": readiness, "notes": notes}


def _eval_airport_sector(ctx, node, inp, advance):
    radar, nav, tower = ctx.dev(node["dmap"]["radar"]), ctx.dev(node["dmap"]["nav"]), ctx.dev(node["dmap"]["tower"])
    grid_ok = inp["grid"]["grid_ok"]
    transport_up = inp["transport"]["up"]
    base_comms = inp["base_comms"]["comms"]
    powered = grid_ok
    radar_up = bool(powered and radar["online"] and radar["functional"] and ctx.v(radar, "sweep") == 1)
    nav_up = bool(powered and nav["online"] and nav["functional"] and ctx.v(nav, "ils_on") == 1)
    tower_up = bool(powered and tower["online"] and tower["functional"] and ctx.v(tower, "freq_active") == 1)
    notes = []
    if not powered:
        status = "closed"; notes.append("airport lost power - field closed")
    elif not tower_up:
        status = "closed"; notes.append("tower ATC comms down - field closed")
    elif not radar_up or not nav_up:
        status = "degraded"
        if not radar_up:
            notes.append("surveillance radar down - procedural separation only")
        if not nav_up:
            notes.append("ILS/navaids down - no precision approaches")
    elif not transport_up:
        status = "degraded"; notes.append("telecom backhaul lost - remote monitoring blind")
    elif not base_comms:
        status = "degraded"; notes.append("military radar feed lost - reduced airspace picture")
    else:
        status = "normal"
    return {"powered": powered, "radar_up": radar_up, "nav_up": nav_up,
            "tower_up": tower_up, "status": status, "notes": notes}


# archetype registry: name -> (demand_fn, evaluate_fn)
ARCHETYPES = {
    "generator":      (lambda c, n: 0.0, _eval_generator),
    "grid_bus":       (_demand_grid, _eval_grid),
    "comms_backbone": (_demand_const, _eval_comms_backbone),
    "pump":           (_demand_pump, _eval_pump),
    "pressure_tank":  (lambda c, n: 0.0, _eval_pressure_tank),
    "treatment":      (lambda c, n: 0.0, _eval_treatment),
    "hospital_sector": (_demand_const, _eval_hospital_sector),
    "base_sector":    (_demand_const, _eval_base_sector),
    "airport_sector": (_demand_const, _eval_airport_sector),
}


def demand(arch, ctx, node):
    return ARCHETYPES[arch][0](ctx, node)


def evaluate(arch, ctx, node, inputs, advance):
    return ARCHETYPES[arch][1](ctx, node, inputs, advance)
