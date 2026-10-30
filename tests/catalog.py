"""Catalog test (Phase 2): the archetype catalog + templates are consistent
with the runtime, and instantiating the Marin site pack reproduces the live
town + topology exactly. Guarantees the data-driven palette is complete and
correct, so the future builder can rely on it.

Run:  python tests/catalog.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simlab import catalog                 # noqa: E402
from simlab.town import fresh_town         # noqa: E402
from simlab.topology import fresh_topology  # noqa: E402
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
# spot-check computed attack surface: generator has PLC+RTU -> impact techniques apply
gen = next(a for a in desc["archetypes"] if a["id"] == "generator")
check("T0836" in gen["attack_techniques"], "generator should expose T0836 (Modify Parameter)")
check(gen["d3fend"], "generator should map to at least one D3FEND countermeasure")

# 2) instantiation reproduces the live build
inst = catalog.instantiate_site(catalog.load_site("marin"))
town = fresh_town()
topo = fresh_topology()

# devices
check(set(inst["devices"]) == set(town["devices"]),
      f"device id mismatch: {set(inst['devices']) ^ set(town['devices'])}")
for did, d in town["devices"].items():
    check(inst["devices"].get(did) == d, f"device {did} differs:\n  live={d}\n  inst={inst['devices'].get(did)}")

# facilities
check(set(inst["facilities"]) == set(town["facilities"]), "facility id mismatch")
for fid, f in town["facilities"].items():
    g = inst["facilities"].get(fid, {})
    for k in ("id", "name", "kind", "map", "compromised", "it_compromised", "findings"):
        check(g.get(k) == f.get(k), f"facility {fid}.{k} differs: live={f.get(k)} inst={g.get(k)}")
    check(set(g.get("devices", [])) == set(f["devices"]), f"facility {fid} devices differ")

# districts
check(inst["districts"] == town["districts"], "districts differ")

# nodes
inst_nodes = {n["id"]: n for n in inst["nodes"]}
live_nodes = {n["id"]: n for n in topo["nodes"]}
check(set(inst_nodes) == set(live_nodes), f"node id mismatch: {set(inst_nodes) ^ set(live_nodes)}")
for nid, n in live_nodes.items():
    check(inst_nodes.get(nid) == n, f"node {nid} differs:\n  live={n}\n  inst={inst_nodes.get(nid)}")

# links (order-independent)
def lset(links):
    return {(l["src"], l["dst"], l["role"], l["resource"]) for l in links}
check(lset(inst["links"]) == lset(topo["links"]),
      f"links differ: {lset(inst['links']) ^ lset(topo['links'])}")

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

if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS: catalog validated; Marin site instantiation reproduces the live "
      "town + topology exactly; instantiated topology solves ✓")
