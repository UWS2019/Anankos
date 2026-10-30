"""Read-only HTML view of the archetype catalog + templates (the builder
palette, made visible). Standalone, inline CSS, matches the Anankos look.
"""
from __future__ import annotations

import html

CAT_COLOR = {"generation": "#fbbf24", "distribution": "#f59e0b", "process": "#38bdf8",
             "comms": "#34d399", "consumer": "#f472b6", "control": "#a78bfa"}


def _esc(s):
    return html.escape(str(s))


def _ports(ports):
    def chips(items, cls):
        return " ".join(f'<span class="port {cls}">{_esc(p["resource"])}·{_esc(p["role"])}</span>' for p in items) or '<span class="muted">—</span>'
    return f'<div class="io"><b>in</b> {chips(ports["in"], "pin")} &nbsp; <b>out</b> {chips(ports["out"], "pout")}</div>'


def render_catalog_html(desc):
    arche = ""
    for a in desc["archetypes"]:
        c = CAT_COLOR.get(a["category"], "#64748b")
        params = ", ".join(f'{_esc(p["name"])}={_esc(p.get("default"))}{_esc(p.get("unit",""))}' for p in a["params"]) or "—"
        roles = ", ".join(f'{_esc(r)} ({_esc(s["type"])})' for r, s in a["device_roles"].items()) or "none"
        atk = ", ".join(a["attack_techniques"]) or "—"
        d3f = ", ".join(a["d3fend"]) or "—"
        arche += f"""<div class="card">
          <div class="chead"><span class="tag" style="background:{c}">{_esc(a['category'])}</span>
            <b>{_esc(a['name'])}</b> <span class="mono">{_esc(a['id'])}</span></div>
          <div class="desc">{_esc(a['description'])}</div>
          {_ports(a['ports'])}
          <div class="row"><b>devices</b> {roles}</div>
          <div class="row"><b>params</b> {params}</div>
          <div class="row"><b>ATT&CK</b> <span class="mono atk">{_esc(atk)}</span></div>
          <div class="row"><b>D3FEND</b> <span class="mono d3f">{_esc(d3f)}</span></div>
        </div>"""

    tmpl = ""
    for t in desc["templates"]:
        c = CAT_COLOR.get(t["category"], "#64748b")
        nodes = ", ".join(f'{_esc(n["arch"])}' for n in t["nodes"])
        cons = ", ".join(_esc(x["resource"]) for x in t["consumes"]) or "—"
        prov = ", ".join(_esc(x["resource"]) for x in t["provides"]) or "—"
        tmpl += f"""<div class="card">
          <div class="chead"><span class="tag" style="background:{c}">{_esc(t['facility_kind'])}</span>
            <b>{_esc(t['name'])}</b> <span class="mono">{_esc(t['id'])}</span></div>
          <div class="row"><b>nodes</b> {nodes}</div>
          <div class="row"><b>devices</b> {t['device_count']}</div>
          <div class="row"><b>consumes</b> {cons} &nbsp; <b>provides</b> {prov}</div>
        </div>"""

    ok = not desc["validation"]
    status = ('<span class="ok">✓ catalog consistent with runtime</span>' if ok
              else '<span class="bad">✗ ' + _esc("; ".join(desc["validation"])) + "</span>")
    res = " ".join(f'<span class="port" style="border-color:{_esc(v["color"])};color:{_esc(v["color"])}">{_esc(k)}</span>' for k, v in desc["resources"].items())

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anankos Device Library</title><style>
  body {{ margin:0; background:#0a0e14; color:#d7e0ee; font-family:ui-sans-serif,system-ui,"Segoe UI",Arial,sans-serif; }}
  header {{ padding:16px 22px; border-bottom:1px solid #1e2838; background:#0d141e; }}
  .brand {{ font-weight:800; letter-spacing:.5px; }} .brand small {{ color:#7d8aa0; font-weight:500; }}
  h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:1.2px; color:#2dd4bf; margin:22px 22px 8px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:12px; padding:0 22px; }}
  .card {{ background:#121823; border:1px solid #1e2838; border-radius:12px; padding:12px 14px; }}
  .chead {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; flex-wrap:wrap; }}
  .tag {{ color:#0a0e14; font-weight:700; font-size:.64rem; text-transform:uppercase; padding:2px 7px; border-radius:6px; }}
  .mono {{ font-family:ui-monospace,monospace; font-size:.76rem; color:#7d8aa0; }}
  .mono.atk {{ color:#f472b6; }} .mono.d3f {{ color:#86efac; }}
  .desc {{ color:#9fb0c8; font-size:.84rem; margin-bottom:8px; }}
  .io {{ font-size:.78rem; margin-bottom:6px; }}
  .row {{ font-size:.8rem; margin-top:3px; }} .row b {{ color:#7d8aa0; font-weight:600; }}
  .port {{ display:inline-block; border:1px solid #2b3a4a; border-radius:6px; padding:1px 7px; font-size:.72rem; margin:1px; }}
  .pin {{ color:#9be7ff; border-color:#1c4a63; }} .pout {{ color:#ffd27a; border-color:#5a4310; }}
  .muted {{ color:#55657c; }} .ok {{ color:#4ade80; }} .bad {{ color:#f87171; }}
  .bar {{ padding:8px 22px; font-size:.8rem; color:#9fb0c8; border-bottom:1px solid #1e2838; }}
</style></head><body>
<header><div class="brand">ANANKOS · DEVICE LIBRARY <small>&nbsp;· archetype catalog &amp; facility templates (builder palette)</small></div></header>
<div class="bar">{status} &nbsp;·&nbsp; resources: {res}</div>
<h2>Archetypes ({len(desc['archetypes'])})</h2>
<div class="grid">{arche}</div>
<h2>Facility Templates ({len(desc['templates'])})</h2>
<div class="grid">{tmpl}</div>
<div style="height:28px"></div>
</body></html>"""
