"""Natural-language scenario compiler.

Turns a plain-English description ("a ransomware crew breaches the regional
telecom carrier and pivots into the water and power utilities, poisoning the
water and blacking out the grid") into a structured, playable scenario:
findings to set, defenses to deploy, and an ordered sequence of attacker steps
the engine auto-plays on the sand table.

Two paths (chosen in app.py):
  * on-device (default): a deterministic keyword/intent parser - fully offline,
    no API key, predictable for internal/air-gapped use.
  * optional AI: if an Anthropic API key is configured and reachable, Claude
    translates the text into the same scenario schema for richer free-form
    understanding; any failure falls back to the on-device parser.

Steps reference a facility by kind + device role; the engine resolves them to
concrete device ids at play time, so a scenario works on any topology that has
those facilities.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

# --- vocabulary -------------------------------------------------------------
FINDING_PATTERNS = {
    "internet_exposed": r"internet[- ]?(facing|exposed|accessible)|shodan|public[- ]?facing|exposed to the internet|exposed device",
    "default_creds": r"default (credential|password|cred)|factory (password|credential)|weak password",
    "flat_network": r"flat network|no segmentation|unsegmented|no ot/it boundary|no it/ot boundary",
    "unpatched": r"unpatched|out[- ]?of[- ]?date|outdated firmware|legacy (system|device|firmware)|end[- ]?of[- ]?life",
}

DEFENSE_PATTERNS = {
    "D3-BDI": r"segment|segregat|ot/it boundary|it/ot boundary|data diode|isolat",
    "D3-MFA": r"\bmfa\b|multi[- ]?factor|credential hardening|rotate (the )?credential",
    "D3-FEMP": r"patch|firmware update|hardening",
    "D3-MAIL": r"email filter|phishing (training|awareness)|dmarc",
    "D3-NTA": r"network monitoring|traffic analysis|\bids\b|\bips\b|ot monitoring|anomaly detection",
    "D3-PVM": r"process (variable )?monitoring|setpoint monitoring|historian",
    "D3-OAM": r"command authentication|signed command|authenticated (protocol|command)",
    "D3-SIS": r"safety instrumented|\bsis\b|safety system",
    "D3-DRP": r"restart protection|redundant controller|device hardening",
}

# impact patterns: regex -> (facility_kind, role, technique, params, narration)
IMPACTS = [
    (r"poison|contaminat|chlorine|chemical (dose|dosing)|overdose|dosing", "water", "dose", "T0836", {"param": "dose_ppm", "value": 15}, "Chlorine dose driven to an unsafe level — public water contaminated."),
    (r"stop (the )?pump|halt (the )?pump|pump (is )?stopped|loss of (water )?pressure", "water", "pump", "T0855", {"command": "stop_pump"}, "High-lift pump commanded to stop — water pressure collapses."),
    (r"blackout|black ?out|\bopen\w*\b.{0,18}breaker|\btrip\w*\b.{0,18}breaker|breaker (open|trip)|cut (the )?power|de-?energ|island(ed)?|grid (goes |is )?(down|collaps)|collaps\w*\b.{0,12}grid|lights out|knock out (the )?power", "power", "bkr", "T0855", {"command": "open_breaker"}, "Main breaker opened — the grid collapses."),
    (r"reduce generation|lower (the )?setpoint|under[- ]?generat", "power", "gov", "T0836", {"param": "setpoint_mw", "value": 40}, "Turbine generation setpoint forced down."),
    (r"oxygen|medical gas|\bo2\b", "hospital", "o2", "T0836", {"param": "o2_psi", "value": 20}, "Medical oxygen pressure driven out of safe range."),
    (r"drain (the )?generator|backup fuel|generator fuel|deplete (the )?fuel", "hospital", "ats", "T0836", {"param": "gen_fuel_min", "value": 2}, "Hospital backup-generator fuel depleted."),
    (r"perimeter|unlock (the )?gate|gates? (open|unlock)|breach the (base|perimeter)", "base", "perim", "T0855", {"command": "unlock_gates"}, "Base perimeter gates forced unlocked."),
    (r"fuel farm", "base", "fuel", "T0836", {"param": "level_pct", "value": 0}, "Base fuel farm drained."),
    (r"base comms|\bc2\b|command and control", "base", "c2", "T0814", {}, "Base C2/radar link denied."),
    (r"surveillance radar|airport radar|stop (the )?radar|radar off", "airport", "radar", "T0855", {"command": "radar_stop"}, "Airport surveillance radar stopped."),
    (r"\bils\b|navaid|precision approach", "airport", "nav", "T0836", {"param": "ils_on", "value": 0}, "ILS/navaids disabled."),
    (r"\btower\b|atc comms|control tower", "airport", "tower", "T0855", {"command": "comms_off"}, "Tower ATC comms disabled."),
    (r"cut (the )?fiber|fiber cut|sever (the )?fiber", "telecom", "fiber", "T0855", {"command": "cut_fiber"}, "Fiber backbone cut."),
    (r"jam (the )?microwave|microwave (jam|link|relay)", "telecom", "mw", "T0855", {"command": "jam_microwave"}, "Microwave relay jammed."),
    (r"cellular|\bcell\b|rf (jam|backhaul)|cell tower|lte|\b5g\b", "telecom", "cell", "T0855", {"command": "jam_cell"}, "Cellular/RF backhaul jammed."),
    (r"spoof (the )?gps|gps spoof|gnss|timing (attack|spoof)", "telecom", "gps", "T0855", {"command": "spoof_gps"}, "GPS timing spoofed — SCADA clock drift."),
    (r"\bbgp\b|routing hijack|reroute traffic|blackhole", "telecom", "core", "T0855", {"command": "hijack_routing"}, "Core routing hijacked — transport blackholed."),
]

DEFAULT_OT_ROLE = {"power": "bkr", "water": "dose", "hospital": "ats",
                   "base": "perim", "airport": "radar", "telecom": "core"}

TELECOM_WORDS = r"telecom|telco|carrier|\bisp\b|comms provider|communications provider|shared provider|service provider"
PIVOT_WORDS = r"pivot|into (the )?(other )?utilit|supply[- ]?chain|trusted relationship|every utility|all utilities|each utilit|across (the )?sector|cascad(e|ing)|ripple|downstream"
ENTRY_PHISH = r"phish|spear[- ]?phish|malicious email|credential theft"
ENTRY_EXPLOIT = r"exploit|\bvpn\b|\brdp\b|public[- ]?facing app|remote service|exposed service"


def _title(text):
    s = re.sub(r"\s+", " ", text).strip()
    return (s[:60] + "…") if len(s) > 62 else (s or "Scenario")


def compile_scenario(text):
    """On-device deterministic compile. Returns a scenario dict with steps
    referencing facilities by (kind, role); the engine resolves devices."""
    t = text.lower()

    findings = [k for k, pat in FINDING_PATTERNS.items() if re.search(pat, t)]
    defenses = [d for d, pat in DEFENSE_PATTERNS.items() if re.search(pat, t)]

    # ordered impacts by first match position
    found = []
    for pat, kind, role, tech, params, note in IMPACTS:
        m = re.search(pat, t)
        if m:
            found.append((m.start(), {"kind": kind, "role": role, "technique": tech,
                                      "params": dict(params), "text": note}))
    found.sort(key=lambda x: x[0])
    impacts = [f[1] for f in found]

    telecom_mentioned = bool(re.search(TELECOM_WORDS, t))
    telecom_entry = telecom_mentioned and bool(re.search(PIVOT_WORDS, t))

    # If nothing concrete was asked for, synthesize a sensible cascade.
    if not impacts:
        if telecom_entry:
            impacts = [
                {"kind": "water", "role": "dose", "technique": "T0836", "params": {"param": "dose_ppm", "value": 15}, "text": "Water treatment dosing driven unsafe."},
                {"kind": "power", "role": "bkr", "technique": "T0855", "params": {"command": "open_breaker"}, "text": "Grid breaker opened — power lost."},
                {"kind": "base", "role": "perim", "technique": "T0855", "params": {"command": "unlock_gates"}, "text": "Base perimeter forced open."},
            ]
        else:
            impacts = [
                {"kind": "power", "role": "bkr", "technique": "T0855", "params": {"command": "open_breaker"}, "text": "Grid breaker opened — power lost."},
                {"kind": "water", "role": "dose", "technique": "T0836", "params": {"param": "dose_ppm", "value": 15}, "text": "Water treatment dosing driven unsafe."},
            ]

    exposed = "internet_exposed" in findings
    use_exploit = bool(re.search(ENTRY_EXPLOIT, t)) and not re.search(ENTRY_PHISH, t)
    it_access_tech = "T0866" if use_exploit else "T1566"
    it_access_name = "Exploit remote service" if use_exploit else "Phishing"

    steps = []
    compromised = set()

    if telecom_entry:
        steps.append({"op": "execute", "kind": "telecom", "role": "it", "technique": it_access_tech,
                      "params": {}, "text": f"{it_access_name}: foothold on the telecom carrier's IT network."})
        steps.append({"op": "execute", "kind": "telecom", "role": "it", "technique": "T1210",
                      "params": {}, "text": "Pivot IT→OT inside the telecom carrier — provider OT compromised."})
        compromised.add("telecom")

    targeted = []
    for imp in impacts:
        if imp["kind"] not in targeted:
            targeted.append(imp["kind"])

    for kind in targeted:
        if kind in compromised:
            continue
        if telecom_entry and kind != "telecom":
            ot_role = next((i["role"] for i in impacts if i["kind"] == kind), DEFAULT_OT_ROLE.get(kind, "bkr"))
            steps.append({"op": "execute", "kind": kind, "role": ot_role, "technique": "T1199",
                          "params": {}, "text": f"Trusted-relationship pivot via the telecom carrier into the {kind} utility."})
        elif exposed:
            # direct access to an internet-exposed device of this facility
            role = next((i["role"] for i in impacts if i["kind"] == kind), "it")
            steps.append({"op": "execute", "kind": kind, "role": role, "technique": "T0883",
                          "params": {}, "text": f"Direct OT access to an internet-exposed {kind} device."})
        else:
            steps.append({"op": "execute", "kind": kind, "role": "it", "technique": it_access_tech,
                          "params": {}, "text": f"{it_access_name}: foothold on the {kind} IT network."})
            steps.append({"op": "execute", "kind": kind, "role": "it", "technique": "T1210",
                          "params": {}, "text": f"Pivot IT→OT inside the {kind} facility."})
        compromised.add(kind)

    for imp in impacts:
        steps.append({"op": "execute", "kind": imp["kind"], "role": imp["role"],
                      "technique": imp["technique"], "params": imp["params"], "text": imp["text"]})

    return {
        "title": _title(text),
        "narrative": text.strip(),
        "source": "on-device",
        "findings": findings,
        "defenses": defenses,
        "steps": steps,
    }


# --- optional AI path -------------------------------------------------------
SCHEMA_HINT = """You convert a plain-English critical-infrastructure attack scenario into JSON for a training simulator.
Return ONLY a JSON object: {"title": str, "findings": [str], "defenses": [str], "steps": [{"op":"execute","kind":str,"role":str,"technique":str,"params":{},"text":str}]}.
facility kinds: power, water, hospital, base, airport, telecom.
device roles by kind: power(gov,bkr,hmi,it) water(pump,dose,hmi,it) hospital(ats,o2,bms,it) base(sub,fuel,c2,perim,it) airport(radar,nav,tower,it) telecom(core,fiber,mw,cell,gps,it).
findings: internet_exposed, default_creds, flat_network, unpatched.
defenses (D3FEND ids): D3-BDI,D3-MFA,D3-FEMP,D3-MAIL,D3-NTA,D3-PVM,D3-OAM,D3-SIS,D3-DRP.
techniques: IT access T1566(phishing)/T1190/T0866; pivot T1210; telecom provider pivot T1199; OT access T0812/T0866/T0883; impact T0836(set param: params {param,value}) T0855(command: params {command: open_breaker|stop_pump|force_utility|unlock_gates|radar_stop|comms_off|cut_fiber|jam_microwave|jam_cell|spoof_gps|hijack_routing}) T0814(dos) T0816 T0880.
Order steps as a realistic kill chain: IT access -> pivot (or T1199 from a compromised telecom) -> impact. Keep each 'text' a short narration sentence."""


def compile_with_ai(text, timeout=20):
    key = os.environ.get("ANANKOS_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        body = json.dumps({
            "model": os.environ.get("ANANKOS_NL_MODEL", "claude-sonnet-4-5"),
            "max_tokens": 1500,
            "system": SCHEMA_HINT,
            "messages": [{"role": "user", "content": text}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        scn = json.loads(raw)
        scn.setdefault("title", _title(text))
        scn["narrative"] = text.strip()
        scn["source"] = "ai"
        scn.setdefault("findings", [])
        scn.setdefault("defenses", [])
        for s in scn.get("steps", []):
            s.setdefault("op", "execute")
            s.setdefault("params", {})
        if not scn.get("steps"):
            return None
        return scn
    except Exception:
        return None


def compile(text, use_ai=True):
    """Compile a scenario, preferring AI when configured/reachable, else the
    on-device parser."""
    if use_ai:
        scn = compile_with_ai(text)
        if scn:
            return scn
    return compile_scenario(text)
