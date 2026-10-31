"""Archetype catalog + facility templates (Phase 2).

Loads the data-driven catalog (archetype metadata), facility templates, and
site packs; validates them against the runtime archetype registry and the
ATT&CK/D3FEND libraries; instantiates a site into the concrete device/facility/
node/link structures the engine runs; and describes the palette for the builder
API. The live engine still constructs Marin via town.py/topology.py; this module
proves (via tests/catalog.py) that the catalog reproduces that build exactly and
provides the instantiation the future drag-and-drop builder will use.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from . import archetypes as arch_mod

DATA = Path(__file__).parent / "data"
DEVICE_TYPES = {"IT", "PLC", "RTU", "HMI", "VFD", "ATS"}


def _findings():
    """Default assessment findings on a facility (toggled to mirror ground
    truth). These change the viable attack path and appear in the report."""
    return {"internet_exposed": False, "flat_network": False,
            "default_creds": False, "unpatched": False}


def _load(name):
    return json.loads((DATA / name).read_text())


def load_archetypes():
    return {a["id"]: a for a in _load("archetype_catalog.json")["archetypes"]}


def load_templates():
    return {t["id"]: t for t in _load("templates.json")["templates"]}


def load_site(name="marin"):
    return _load(f"site_{name}.json")


# --- instantiation ----------------------------------------------------------
def instantiate_site(site, templates=None):
    """Build concrete {devices, facilities, districts, nodes, links} from a
    site pack. Device ids are '<prefix>_<localkey>'; node ids are verbatim."""
    templates = templates or load_templates()
    devices, facilities, nodes, links = {}, {}, [], []

    for pl in site["placements"]:
        tpl = templates[pl["template"]]
        prefix, fid = pl["prefix"], pl["facility"]
        dev_ids = []
        for key, spec in tpl["devices"].items():
            did = f"{prefix}_{key}"
            devices[did] = {
                "id": did, "name": spec["name"], "type": spec["type"],
                "facility": fid, "state": deepcopy(spec["state"]),
                "spoof": {}, "online": True, "functional": True, "safety": True,
            }
            dev_ids.append(did)
        facilities[fid] = {
            "id": fid, "name": pl["name"], "kind": tpl["facility_kind"],
            "compromised": False, "it_compromised": False, "findings": _findings(),
            "devices": dev_ids, "map": deepcopy(pl["map"]),
        }
        for node in tpl["nodes"]:
            dmap = {role: f"{prefix}_{local}" for role, local in node["dmap"].items()}
            nodes.append({"id": node["id"], "arch": node["arch"], "facility": fid,
                          "dmap": dmap, "params": deepcopy(node.get("params", {}))})
        for ln in tpl.get("internal_links", []):
            links.append(dict(ln))
    for ln in site["links"]:
        links.append(dict(ln))

    return {"devices": devices, "facilities": facilities,
            "districts": deepcopy(site["districts"]),
            "nodes": nodes, "links": links}


def autowire(placements, templates=None):
    """Derive standard cross-facility links from each template's declared
    consumes/provides ports: a consumer port links to the first placement that
    provides the same resource+role. Missing providers are simply left
    unwired (the solver's input defaults treat them as not-limiting)."""
    templates = templates or load_templates()
    # index providers by (resource, role) -> node id
    providers = {}
    for pl in placements:
        tpl = templates[pl["template"]]
        for prov in tpl.get("provides", []):
            providers.setdefault((prov["resource"], prov["role"]), prov["node"])
    links = []
    for pl in placements:
        tpl = templates[pl["template"]]
        for cons in tpl.get("consumes", []):
            src = providers.get((cons["resource"], cons["role"]))
            if src:
                links.append({"src": src, "dst": cons["node"],
                              "role": cons["role"], "resource": cons["resource"]})
    return links


# --- validation -------------------------------------------------------------
def validate():
    """Return a list of consistency problems between the catalog data and the
    runtime (archetype registry, device types, dmap roles). Empty = healthy."""
    problems = []
    cat = load_archetypes()
    templates = load_templates()
    runtime = set(arch_mod.ARCHETYPES)

    for aid in runtime:
        if aid not in cat:
            problems.append(f"runtime archetype '{aid}' has no catalog entry")
    for aid, a in cat.items():
        if aid not in runtime:
            problems.append(f"catalog archetype '{aid}' not in runtime registry")
        for role, spec in a.get("device_roles", {}).items():
            if spec["type"] not in DEVICE_TYPES:
                problems.append(f"{aid}.{role}: unknown device type {spec['type']}")

    for tid, t in templates.items():
        for key, spec in t["devices"].items():
            if spec["type"] not in DEVICE_TYPES:
                problems.append(f"template {tid}.{key}: unknown device type {spec['type']}")
        for node in t["nodes"]:
            a = cat.get(node["arch"])
            if not a:
                problems.append(f"template {tid}: node '{node['id']}' uses unknown archetype {node['arch']}")
                continue
            roles = set(a.get("device_roles", {}))
            if set(node["dmap"]) != roles:
                problems.append(f"template {tid}.{node['id']}: dmap roles {set(node['dmap'])} != archetype roles {roles}")
            for local in node["dmap"].values():
                if local not in t["devices"]:
                    problems.append(f"template {tid}.{node['id']}: dmap -> unknown device '{local}'")
    return problems


# --- palette description (for the builder API) ------------------------------
def describe_catalog():
    cat = load_archetypes()
    templates = load_templates()
    techniques = _load("techniques.json")["techniques"]
    defenses = _load("defenses.json")["defenses"]

    def attack_surface(device_types):
        tids = [t["id"] for t in techniques if set(t["applies_to"]) & device_types]
        return tids

    def d3fend_for(tids):
        s = set(tids)
        return [d["id"] for d in defenses if s & set(d["counters"])]

    arche = []
    for a in cat.values():
        dtypes = {spec["type"] for spec in a.get("device_roles", {}).values()}
        tids = attack_surface(dtypes)
        arche.append({
            "id": a["id"], "name": a["name"], "category": a["category"],
            "icon": a["icon"], "description": a["description"],
            "ports": a["ports"], "params": a.get("params", []),
            "device_roles": a.get("device_roles", {}), "modbus": a.get("modbus", []),
            "attack_techniques": tids, "d3fend": d3fend_for(tids),
        })
    tmpl = [{
        "id": t["id"], "name": t["name"], "category": t["category"],
        "facility_kind": t["facility_kind"], "icon": t["icon"],
        "nodes": [{"id": n["id"], "arch": n["arch"]} for n in t["nodes"]],
        "device_count": len(t["devices"]),
        "consumes": t.get("consumes", []), "provides": t.get("provides", []),
    } for t in templates.values()]
    return {
        "resources": _load("archetype_catalog.json")["resources"],
        "archetypes": arche, "templates": tmpl,
        "device_types": sorted(DEVICE_TYPES),
        "validation": validate(),
    }
