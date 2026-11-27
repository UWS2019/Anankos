"""Plain-language status for the staff status board.

Everything here is written for someone who doesn't know what a frequency is.
Rules for the wording:

* Say what is happening, where, and the next 2-3 things to do.
* Never ask staff to approach or confront anyone. Stay inside, use cameras,
  call the manager or 911.
* No jargon: no "jamming", "RollJam", "RSSI", "MHz".
"""

from __future__ import annotations

import time

from .models import Incident, Severity

# Board states, from calm to urgent. "unknown" is decided by the screen itself
# when it can't reach FobGuard (it must never keep showing a stale "all clear").
CLEAR, CHECK, ALERT, PROBLEM = "clear", "check", "alert", "problem"

THEFT_STEPS = [
    "Do not go outside or approach anyone.",
    "Look at the cameras for {where}.",
    "Call the manager on duty. If you see someone at a car, call 911.",
]

# Incident title (from correlator.PATTERNS) -> board wording
PLAIN: dict[str, dict] = {
    "RollBack rolling-code attack": {
        "state": ALERT, "headline": "Someone may be trying to steal a car",
        "why": "A device is re-sending copied car-remote signals to open a car.",
        "steps": THEFT_STEPS},
    "RollJam-style capture and replay": {
        "state": ALERT, "headline": "Someone may be trying to steal a car",
        "why": "Someone blocked car remotes, copied a key signal and has now used it.",
        "steps": THEFT_STEPS},
    "Relay attack in progress": {
        "state": ALERT, "headline": "Someone may be copying a key from the key room",
        "why": "A device outside is reaching a stored key so a car thinks the key is nearby.",
        "steps": ["Do not go outside or approach anyone.",
                  "Look at the cameras around the key room and {outside}.",
                  "Call the manager on duty. If you see someone at a car, call 911."]},
    "Replayed key fob code": {
        "state": ALERT, "headline": "A copied car-remote signal was used",
        "why": "Someone re-sent a car remote signal that was recorded earlier.",
        "steps": THEFT_STEPS},
    "Key code scanning / brute force": {
        "state": ALERT, "headline": "Someone may be trying to break into a car",
        "why": "A device is trying hundreds of car-remote codes very fast.",
        "steps": ["Do not go outside or approach anyone.",
                  "Look at the cameras for {where}. Look for someone holding a small device or sitting in a parked car.",
                  "Call the manager on duty. If you see someone at a car, call 911."]},
    "Stored key transmitting after hours": {
        "state": ALERT, "headline": "A key in the key room is being contacted",
        "why": "Keys in storage should be silent at night. Something is talking to one.",
        "steps": ["Do not go outside or approach anyone.",
                  "Look at the cameras around the key room and {outside}.",
                  "Call the manager on duty."]},
    "Jamming while a fob was used": {
        "state": ALERT, "headline": "Car remotes are being blocked",
        "why": "Someone pressed lock while the signal was blocked. That car may still be UNLOCKED.",
        "steps": ["Do not go outside alone.",
                  "Look at the cameras for {where}.",
                  "Tell the manager. When it is safe, a manager should check that cars locked in the "
                  "last few minutes are really locked."]},
    "Key fob band jammed": {
        "state": ALERT, "headline": "Car remotes are being blocked",
        "why": "Something is stopping car remotes from working. Cars may not lock.",
        "steps": ["Do not go outside alone.",
                  "Look at the cameras for {where}. Look for unfamiliar vehicles or people.",
                  "Tell the manager on duty."]},
    "Key wake-up signals after hours": {
        "state": ALERT, "headline": "Someone may be probing cars or keys",
        "why": "Signals that wake up car keys were picked up while we are closed.",
        "steps": ["Do not go outside or approach anyone.",
                  "Look at the cameras for {where}.",
                  "Call the manager on duty."]},
    "After-hours key fob activity": {
        "state": CHECK, "headline": "A car remote was used after closing",
        "why": "This is fine if staff are still on site.",
        "steps": ["If staff are on site, no action is needed. Tap \"I'm on it\".",
                  "If nobody should be here, look at the cameras for {where}.",
                  "Tell the manager on duty if anything looks wrong."]},
    "RF sensor offline": {
        "state": PROBLEM, "headline": "A security sensor stopped working",
        "why": "Part of the lot is not being watched until it is fixed.",
        "steps": ["Tell the manager or IT.",
                  "Check that the sensor box has power and its cables are plugged in."]},
    "Strong transmitter near sensor": {
        "state": CHECK, "headline": "Something near a sensor is interfering",
        "why": "A strong radio is very close to one of the sensors.",
        "steps": ["Look at the cameras for {where}.",
                  "If nothing looks wrong, tell IT when convenient."]},
}

DEFAULT_ZONE_NAMES = {
    "lot": "the lot",
    "key_storage": "the key room",
    "showroom": "the showroom",
    "service": "the service drive",
}

_ORDER = {CLEAR: 0, PROBLEM: 1, CHECK: 2, ALERT: 3}


def zone_label(zone: str, names: dict[str, str]) -> str:
    return names.get(zone) or DEFAULT_ZONE_NAMES.get(zone) or zone.replace("_", " ")


def describe(inc: Incident, zone_names: dict[str, str]) -> dict:
    p = PLAIN.get(inc.title)
    if p is None:          # unknown pattern: fall back to severity
        state = ALERT if inc.severity >= Severity.HIGH else CHECK
        p = {"state": state, "headline": "Unusual activity on the lot", "why": "",
             "steps": ["Look at the cameras for {where}.", "Tell the manager on duty."]}
    zones = [z.strip() for z in inc.zone.split(",") if z.strip()]
    where = " and ".join(dict.fromkeys(zone_label(z, zone_names) for z in zones)) or "the lot"
    # the outdoor places to check, for steps that already name the key room
    outside = " and ".join(dict.fromkeys(zone_label(z, zone_names) for z in zones if z != "key_storage")) \
        or zone_label("lot", zone_names)
    return {
        "incident_id": inc.id,
        "state": p["state"],
        "headline": p["headline"],
        "why": p["why"],
        "where": where,
        "steps": [s.format(where=where, outside=outside) for s in p["steps"]],
        "since": inc.opened,
        "updated": inc.updated,
        "acknowledged_by": inc.acknowledged_by,
        "acknowledged_at": inc.acknowledged_at,
    }


def board(engine) -> dict:
    """The single thing the status board shows, plus sensor health."""
    cfg = engine.cfg
    names = cfg.site.zone_names
    now_stream = engine.now or time.time()
    active = [i for i in engine.correlator.incidents
              if now_stream - i.updated <= engine.correlator.window and i.severity >= Severity.MEDIUM]
    items = [describe(i, names) for i in active]
    # most urgent first; unacknowledged before acknowledged; newest first
    items.sort(key=lambda x: (_ORDER[x["state"]], x["acknowledged_by"] is None, x["updated"]), reverse=True)

    total = len(engine.monitors)
    offline = [st.cfg.name for st in engine.monitors.values() if not st.online]
    top = items[0] if items else None
    state = top["state"] if top else (PROBLEM if offline else CLEAR)
    if top is None and offline:
        top = {"incident_id": None, "state": PROBLEM, "headline": "A security sensor stopped working",
               "why": "Part of the lot is not being watched until it is fixed.", "where": "",
               "steps": ["Tell the manager or IT.",
                         "Check that the sensor box has power and its cables are plugged in."],
               "since": None, "updated": None, "acknowledged_by": None, "acknowledged_at": None}
    return {
        "site": cfg.site.name,
        "open": cfg.site.is_open(time.time()),
        "state": state,
        "current": top,
        "more": max(0, len(items) - 1),
        "sensors_total": total,
        "sensors_online": total - len(offline),
        "server_time": time.time(),
    }
