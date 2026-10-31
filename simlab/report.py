"""Render an assessment report (standalone, printable HTML) from the data
produced by SimEngine.build_report(). Self-contained: inline CSS, no network,
prints cleanly to PDF from the browser.
"""
from __future__ import annotations

import html

SEV_COLOR = {"nominal": "#16a34a", "degraded": "#d97706", "severe": "#ea580c", "catastrophic": "#dc2626"}


def _esc(s):
    return html.escape(str(s))


def _badge(sev):
    c = SEV_COLOR.get(sev, "#64748b")
    return f'<span class="sev" style="background:{c}">{_esc(sev.upper())}</span>'


def render_html(r):
    cur = r["current"]
    # milestones timeline
    ms = "".join(
        f'<tr><td class="t">T+{m["tick"]:03d}</td><td>{_esc(m["label"])}</td></tr>'
        for m in r["milestones"]) or '<tr><td colspan="2" class="muted">No critical service-loss milestones recorded.</td></tr>'

    # attack narrative
    nar = ""
    for e in r["narrative"]:
        tag = e["technique"] or e["tag"]
        src = "Protocol (Modbus)" if e["source"] == "protocol" else "Operator action"
        nar += (f'<tr><td class="t">T+{e["tick"]:03d}</td>'
                f'<td class="mono">{_esc(tag)}</td>'
                f'<td>{_esc(e["message"])}</td>'
                f'<td class="muted">{_esc(src)}</td></tr>')
    nar = nar or '<tr><td colspan="4" class="muted">No attacker actions recorded.</td></tr>'

    # ATT&CK coverage grouped by tactic
    by_tactic = {}
    for c in r["coverage"]:
        by_tactic.setdefault(c["tactic"], []).append(c)
    cov = ""
    for tactic, items in by_tactic.items():
        chips = " ".join(f'<span class="chip">{_esc(c["id"])} {_esc(c["name"])}'
                         + (f' ×{c["count"]}' if c["count"] > 1 else "") + "</span>" for c in items)
        cov += f'<div class="tactic"><div class="tac-h">{_esc(tactic)}</div><div>{chips}</div></div>'
    cov = cov or '<div class="muted">No ATT&CK techniques were exercised.</div>'

    # findings per facility
    fac = ""
    for f in r["facilities"]:
        access = ("OT compromised" if f["compromised"] else
                  "IT foothold" if f["it_compromised"] else "No access gained")
        acolor = "#dc2626" if f["compromised"] else "#d97706" if f["it_compromised"] else "#16a34a"
        finds = ", ".join(_esc(x) for x in f["findings"]) or '<span class="muted">none set</span>'
        fac += (f'<tr><td><b>{_esc(f["name"])}</b><div class="muted">{_esc(f["kind"])}</div></td>'
                f'<td style="color:{acolor};font-weight:600">{access}</td>'
                f'<td>{finds}</td></tr>')

    notes = "".join(f"<li>{_esc(n)}</li>" for n in cur["consequence_notes"]) or "<li class='muted'>No active civilian/mission impacts at report time.</li>"

    # blue-team / D3FEND
    b = r["blue"]
    deployed = " ".join(f'<span class="chip">{_esc(d["id"])} {_esc(d["name"])}</span>' for d in b["deployed"]) \
        or '<span class="muted">No defensive countermeasures were deployed during this run.</span>'
    det_rate = f'{b["detection_rate"]}%' if b["detection_rate"] is not None else "—"
    prev_rate = f'{b["prevention_rate"]}%' if b["prevention_rate"] is not None else "—"
    recs = ""
    for x in b["recommendations"]:
        recs += (f'<tr><td class="mono">{_esc(x["id"])}</td><td><b>{_esc(x["name"])}</b>'
                 f'<div class="muted">{_esc(x["d3f"])}</div></td>'
                 f'<td class="mono">{_esc(", ".join(x["addresses"]))}</td>'
                 f'<td>{_esc(x["recommends"])}</td></tr>')
    recs = recs or '<tr><td colspan="4" class="muted">No additional countermeasures required for the techniques exercised.</td></tr>'
    blue_html = f"""
  <h2>Blue Team — MITRE D3FEND Posture</h2>
  <div class="cards">
    <div class="kcard"><div class="k">Attacker actions</div><div class="v">{b['attempts']}</div></div>
    <div class="kcard"><div class="k">Prevented</div><div class="v" style="color:#16a34a">{b['blocked']}</div></div>
    <div class="kcard"><div class="k">Detected</div><div class="v" style="color:#0f766e">{b['detected']}</div></div>
    <div class="kcard"><div class="k">Undetected</div><div class="v" style="color:#dc2626">{b['undetected']}</div></div>
  </div>
  <p class="summary" style="margin-top:12px">Prevention rate <b>{prev_rate}</b> · Detection rate (of actions that landed) <b>{det_rate}</b>.</p>
  <b style="font-size:.86rem">Countermeasures deployed:</b>
  <div style="margin:6px 0 4px">{deployed}</div>

  <h2>Defensive Recommendations (D3FEND)</h2>
  <p class="summary">For each technique exercised in this run that was not already prevented or detected, the mapped MITRE D3FEND countermeasure is recommended:</p>
  <table><thead><tr><th>D3FEND</th><th>Countermeasure</th><th>Addresses</th><th>Recommendation</th></tr></thead>
    <tbody>{recs}</tbody></table>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Anankos Assessment Report — {_esc(r['town'])}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: ui-sans-serif, system-ui, "Segoe UI", Arial, sans-serif; color: #0f172a;
    margin: 0; background: #eef2f7; }}
  .page {{ max-width: 920px; margin: 0 auto; background: #fff; padding: 46px 54px; }}
  header {{ border-bottom: 3px solid #0f766e; padding-bottom: 16px; margin-bottom: 24px;
    display: flex; align-items: flex-end; justify-content: space-between; }}
  .brand {{ font-size: 1.5rem; font-weight: 800; letter-spacing: .5px; color: #0f766e; }}
  .brand small {{ display: block; color: #64748b; font-weight: 500; font-size: .8rem; letter-spacing: 0; }}
  .meta {{ text-align: right; color: #64748b; font-size: .82rem; }}
  h2 {{ font-size: .95rem; text-transform: uppercase; letter-spacing: 1px; color: #0f766e;
    border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin: 30px 0 14px; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .kcard {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; }}
  .kcard .k {{ font-size: .68rem; text-transform: uppercase; letter-spacing: .6px; color: #64748b; }}
  .kcard .v {{ font-size: 1.5rem; font-weight: 800; margin-top: 3px; }}
  .sev {{ color: #fff; padding: 3px 10px; border-radius: 999px; font-weight: 700; font-size: .8rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .86rem; }}
  th {{ text-align: left; color: #475569; font-weight: 600; border-bottom: 2px solid #e2e8f0; padding: 7px 8px; }}
  td {{ border-bottom: 1px solid #eef2f7; padding: 7px 8px; vertical-align: top; }}
  td.t {{ font-variant-numeric: tabular-nums; color: #64748b; white-space: nowrap; width: 62px; }}
  .mono {{ font-family: ui-monospace, Menlo, Consolas, monospace; color: #be185d; white-space: nowrap; }}
  .muted {{ color: #94a3b8; }}
  .tactic {{ margin-bottom: 10px; }}
  .tac-h {{ font-weight: 700; font-size: .82rem; color: #334155; margin-bottom: 4px; }}
  .chip {{ display: inline-block; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 6px;
    padding: 2px 8px; font-size: .76rem; margin: 2px 3px 2px 0; }}
  .summary {{ font-size: .95rem; line-height: 1.55; }}
  ul {{ margin: 8px 0 0; padding-left: 20px; }}
  .disclaimer {{ margin-top: 34px; padding: 12px 14px; background: #fff7ed; border: 1px solid #fed7aa;
    border-radius: 8px; color: #9a3412; font-size: .78rem; }}
  footer {{ margin-top: 22px; color: #94a3b8; font-size: .74rem; text-align: center; }}
  @media print {{ body {{ background: #fff; }} .page {{ padding: 0; max-width: none; }} @page {{ margin: 16mm; }} }}
</style></head>
<body><div class="page">
  <header>
    <div class="brand">ANANKOS<small>Critical Infrastructure Vulnerability Assessment</small></div>
    <div class="meta">Scenario town: <b>{_esc(r['town'])}</b><br>Generated {_esc(r['generated_at'])}<br>
      Run length: {r['duration_min']} simulated min</div>
  </header>

  <h2>Executive Summary</h2>
  <div class="cards">
    <div class="kcard"><div class="k">Worst severity</div><div class="v">{_badge(r['worst_severity'])}</div></div>
    <div class="kcard"><div class="k">Peak lives at risk</div><div class="v" style="color:#dc2626">{r['peak_lives_at_risk']}</div></div>
    <div class="kcard"><div class="k">Attacker actions</div><div class="v">{r['attack_count']}</div></div>
    <div class="kcard"><div class="k">Unauth. protocol writes</div><div class="v">{r['protocol_writes']}</div></div>
  </div>
  <p class="summary" style="margin-top:16px">
    During this {r['duration_min']}-minute simulated engagement against the town of {_esc(r['town'])},
    the modeled attack chain drove town status to <b>{_esc(r['worst_severity'].upper())}</b>, placing a peak of
    <b>{r['peak_lives_at_risk']}</b> lives at risk. At report time, grid generation was
    {cur['generation_mw']} MW against {cur['demand_mw']} MW of demand
    ({'balanced' if cur['grid_ok'] else 'in deficit'}), public water was
    <b>{_esc(cur['water_quality'].replace('_',' '))}</b> at {cur['water_pressure']} psi, the hospital was
    <b>{'powered' if cur['hospital_powered'] else 'without power'}</b>, Fort Marin readiness was
    <b>{_esc(cur['base_readiness'].upper())}</b>, and an estimated {cur['population_without_power_pct']}% of the
    city was without power.
  </p>
  <b style="font-size:.86rem">Active impacts at report time:</b>
  <ul>{notes}</ul>

  <h2>Critical Service-Loss Timeline</h2>
  <table><thead><tr><th>Time</th><th>Milestone</th></tr></thead><tbody>{ms}</tbody></table>

  <h2>Attack Narrative</h2>
  <table><thead><tr><th>Time</th><th>ATT&amp;CK / tag</th><th>Action &amp; effect</th><th>Vector</th></tr></thead>
    <tbody>{nar}</tbody></table>

  <h2>MITRE ATT&amp;CK Coverage</h2>
  {cov}

  <h2>Facility Posture &amp; Findings</h2>
  <table><thead><tr><th>Facility</th><th>Access gained</th><th>Findings mirrored from assessment</th></tr></thead>
    <tbody>{fac}</tbody></table>
  {blue_html}

  <div class="disclaimer">
    <b>Simulation notice.</b> This report was produced by the Anankos Sim Lab, a training and
    assessment simulator. All devices, outcomes, and consequences are modeled in software; no
    real infrastructure, control system, or network was accessed. Findings reflect conditions
    an assessor configured to mirror a real environment, and outcomes illustrate potential
    cascading impact for planning and resilience purposes.
  </div>
  <footer>Anankos · Critical Infrastructure Vulnerability Assessment · {_esc(r['generated_at'])}</footer>
</div></body></html>"""
