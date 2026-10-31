"""Catalog test: the archetype catalog + facility templates are consistent with
the runtime, instantiate into a valid topology that solves, and a subset client
site builds and scores. The catalog is the single source of truth for the town
(the engine builds every run from it).

Run:  python tests/catalog.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simlab import catalog                 # noqa: E402
from simlab.archetypes import Ctx          # noqa: E402
from simlab import solver                  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# 1) catalog <-> runtime consistency
problems = catalog.validate()
check(not problems, f"catalog validation problems: {problems}")

desc = catalog.describe_catalog()
check(len(desc["archetypes"]) == 9, f"expected 9 archetypes, got {len(desc['archetypes'])}")
check(len(desc["templates"]) == 6, f"expected 6 templates, got {len(desc['templates'])}")
gen = next(a for a in desc["archetypes"] if a["id"] == "generator")
check("T0836" in gen["attack_techniques"], "generator should expose T0836 (Modify Parameter)")
check(gen["d3fend"], "generator should map to at least one D3FEND countermeasure")

# 2) the Marin site pack instantiates into a complete, valid structure
inst = catalog.instantiate_site(catalog.load_site("marin"))
check(set(f["kind"] for f in inst["facilities"].values()) ==
      {"power", "water", "hospital", "base", "airport", "telecom"},
      "Marin site should instantiate all six facility kinds")
for n in inst["nodes"]:
    for role, did in n["dmap"].items():
        check(did in inst["devices"], f"node {n['id']} dmap -> missing device {did}")

# 3) instantiated topology actually solves (functional smoke)
class _E:
    def __init__(self, inst):
        self.town = {"devices": inst["devices"]}
        self.water_pressure = 60.0
        self.defenses_on = set()
    def _sis_on(self):
        return False
out = solver.solve(Ctx(_E(inst)), {"nodes": inst["nodes"], "links": inst["links"]}, advance=False)
check(out["grid"]["grid_ok"] is True, "instantiated topology should solve to grid_ok at baseline")
check(out["telecom"]["up"] is True and out["airport"]["status"] == "normal",
      "instantiated topology baseline telecom/airport should be healthy")

# 4) a subset client site (no base/airport) builds and scores correctly
from simlab.engine import SimEngine  # noqa: E402
eng = SimEngine()
subset = {"name": "ClientA", "placements": [
    {"template": "power_plant", "facility": "power", "name": "City Power", "prefix": "pp", "map": {"x": 120, "y": 200}},
    {"template": "water_treatment", "facility": "water", "name": "City Water", "prefix": "wt", "map": {"x": 430, "y": 150}},
    {"template": "hospital", "facility": "hospital", "name": "Mercy", "prefix": "hp", "map": {"x": 720, "y": 250}},
], "districts": []}
eng.load_site(subset)
check(set(eng.town["facilities"]) == {"power", "water", "hospital"}, "subset facilities wrong")
check(eng.derived["severity"] == "nominal", "subset baseline should be nominal")
check(eng.derived["present"] == {"water": True, "hospital": True, "base": False,
                                 "airport": False, "telecom": False, "power": True},
      f"subset presence wrong: {eng.derived['present']}")
eng.town["devices"]["wt_dose"]["state"]["dose_ppm"] = 15.0
eng._recompute()
check(eng.derived["severity"] == "severe" and eng.derived["lives_at_risk"] == 35,
      "subset chlorine attack should be severe / 35 lives")
# rebuilding full Marin via load_site still matches the default
eng.reset()
check(set(eng.town["facilities"]) == {"power", "water", "hospital", "base", "airport", "telecom"},
      "reset should restore full Marin")

if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS: catalog validated; Marin site instantiates a complete topology "
      "that solves; subset site builds & scores; reset restores Marin ✓")
