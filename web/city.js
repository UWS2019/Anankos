"use strict";
/* Anankos sand-table city renderer.
 * Draws a detailed-but-2D "sand table": ground plate, road grid, per-type
 * building silhouettes, live power/water links, animated responders, and
 * pinch/zoom + pan. Exposed as window.City, driven by app.js each state push.
 *
 * Architecture for smooth animation:
 *   - a persistent <g id="viewport"> holds everything and carries the
 *     zoom/pan transform (survives re-renders),
 *   - ground + responder layers are built ONCE (so animateMotion never
 *     restarts), edges + buildings are rebuilt only when the visually-relevant
 *     state signature changes.
 */
const SVGNS = "http://www.w3.org/2000/svg";
function E(tag, attrs = {}, kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (kids) (Array.isArray(kids) ? kids : [kids]).forEach((c) => c && e.appendChild(c));
  return e;
}
function T(x, y, s, fill, anchor = "middle", weight = "600") {
  return E("text", { x, y, "font-size": s, fill, "text-anchor": anchor, "font-weight": weight }, [document.createTextNode("")]);
}
function label(x, y, str, fill = "#9fb0c8", s = 11) {
  const t = E("text", { x, y, "font-size": s, fill, "text-anchor": "middle", "font-weight": "600" });
  t.textContent = str; return t;
}

// ---- building silhouettes (centered at 0,0, ~64 wide) ----------------------
function win(x, y, lit) {
  return E("rect", { x, y, width: 5, height: 6, rx: 1, fill: lit ? "#ffd27a" : "#223049", opacity: lit ? 0.95 : 0.7 });
}
function winRow(cx, y, n, lit, gap = 8) {
  const g = E("g"), x0 = cx - ((n - 1) * gap) / 2 - 2.5;
  for (let i = 0; i < n; i++) g.appendChild(win(x0 + i * gap, y, lit));
  return g;
}
function body(w, h, fill, stroke) {
  return E("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 3, fill, stroke, "stroke-width": 1.5 });
}

function buildingPower(st) {
  const g = E("g"); const lit = st.powered; const bad = st.alarm;
  const fill = bad ? "#241114" : "#1c2432", stroke = bad ? "#7f1d1d" : "#3a4a63";
  g.appendChild(body(44, 30, fill, stroke));
  // cooling towers
  [-26, 26].forEach((x) => {
    g.appendChild(E("path", { d: `M${x - 9},6 C ${x - 11},-8 ${x - 4},-10 ${x},-14 C ${x + 4},-10 ${x + 11},-8 ${x + 9},6 Z`, fill: "#273348", stroke }));
    if (lit && st.running) {
      const steam = E("circle", { cx: x, cy: -16, r: 4, fill: "#9fb6d8", opacity: 0.0 });
      steam.appendChild(E("animate", { attributeName: "cy", values: "-14;-34", dur: "2.6s", repeatCount: "indefinite" }));
      steam.appendChild(E("animate", { attributeName: "opacity", values: "0.5;0", dur: "2.6s", repeatCount: "indefinite" }));
      g.appendChild(steam);
    }
  });
  g.appendChild(winRow(0, -4, 4, lit));
  g.appendChild(winRow(0, 5, 4, lit));
  return g;
}
function buildingWater(st) {
  const g = E("g"); const lit = st.powered;
  const bad = st.quality !== "safe";
  g.appendChild(body(30, 26, "#14202c", bad ? "#7f1d1d" : "#0c4a6e"));
  g.appendChild(winRow(0, 2, 2, lit));
  // storage tank with fill level
  const tx = 24;
  g.appendChild(E("rect", { x: tx - 11, y: -20, width: 22, height: 34, rx: 6, fill: "#0b1622", stroke: bad ? "#7f1d1d" : "#0c4a6e" }));
  const lvl = Math.max(2, (st.pressure ?? 60) / 60 * 30);
  g.appendChild(E("rect", { x: tx - 9, y: 12 - lvl, width: 18, height: lvl, rx: 4, fill: bad ? "#ef4444" : "#1f7fb5", opacity: 0.85 }));
  g.appendChild(E("ellipse", { cx: tx, cy: -20, rx: 11, ry: 4, fill: "#0e2434", stroke: bad ? "#7f1d1d" : "#0c4a6e" }));
  return g;
}
function buildingHospital(st) {
  const g = E("g"); const lit = st.powered;
  g.appendChild(body(40, 34, lit ? "#0e2018" : "#1a0f10", lit ? "#2b5a3a" : "#7f1d1d"));
  g.appendChild(winRow(0, -8, 4, lit));
  g.appendChild(winRow(0, 2, 4, lit));
  // red cross sign
  g.appendChild(E("rect", { x: -18, y: -24, width: 14, height: 14, rx: 2, fill: "#fff" }));
  g.appendChild(E("rect", { x: -12.5, y: -20, width: 3, height: 7, fill: "#e11d2a" }));
  g.appendChild(E("rect", { x: -15, y: -17.5, width: 8, height: 3, fill: "#e11d2a" }));
  // helipad H
  g.appendChild(E("circle", { cx: 15, cy: -22, r: 8, fill: "#111a26", stroke: "#3a4a63" }));
  g.appendChild(label(15, -19, "H", "#8aa0bd", 10));
  return g;
}
function buildingBase(st) {
  const g = E("g"); const ok = st.readiness === "full";
  const col = st.powered ? (ok ? "#17241a" : "#241a0c") : "#1a0f10";
  const stroke = st.breach ? "#ef4444" : st.powered ? (ok ? "#2b5a3a" : "#5a4310") : "#7f1d1d";
  // hangar
  g.appendChild(E("path", { d: "M-30,14 L-30,-4 C -30,-16 30,-16 30,-4 L30,14 Z", fill: col, stroke }));
  g.appendChild(winRow(0, 4, 3, st.powered));
  // radar tower + dish
  const dish = E("g", { transform: "translate(22,-20)" });
  dish.appendChild(E("line", { x1: 0, y1: 0, x2: 0, y2: 16, stroke, "stroke-width": 2 }));
  const arm = E("g");
  arm.appendChild(E("path", { d: "M-9,-3 A 9 9 0 0 1 9,-3 Z", fill: st.comms ? "#2dd4bf" : "#55606f", opacity: 0.9 }));
  if (st.comms) {
    const rot = E("animateTransform", { attributeName: "transform", type: "rotate", from: "0 0 0", to: "360 0 0", dur: "4s", repeatCount: "indefinite" });
    arm.appendChild(rot);
  }
  dish.appendChild(arm);
  g.appendChild(dish);
  // flag / mast
  g.appendChild(E("line", { x1: -24, y1: 14, x2: -24, y2: -18, stroke, "stroke-width": 2 }));
  g.appendChild(E("path", { d: "M-24,-18 l12,4 l-12,4 z", fill: st.powered ? "#6b8cc0" : "#3a4256" }));
  // perimeter fence hint
  g.appendChild(E("rect", { x: -36, y: -28, width: 72, height: 52, rx: 6, fill: "none", stroke: st.breach ? "#ef4444" : "#2b3a4a", "stroke-dasharray": "3 4", "stroke-width": 1 }));
  return g;
}
function buildingDistrict(kind, st) {
  const g = E("g"); const lit = st.powered;
  if (kind === "downtown") {
    g.appendChild(body(34, 22, lit ? "#1a2332" : "#141a26", "#3a4a63"));
    g.appendChild(E("path", { d: "M-14,-11 C -6,-26 6,-26 14,-11 Z", fill: lit ? "#243148" : "#161d2a", stroke: "#3a4a63" }));
    g.appendChild(E("line", { x1: 0, y1: -24, x2: 0, y2: -30, stroke: "#3a4a63", "stroke-width": 2 }));
    g.appendChild(winRow(0, 4, 4, lit, 7));
  } else if (kind === "airport") {
    g.appendChild(E("rect", { x: -20, y: 6, width: 44, height: 7, rx: 2, fill: "#20283a" })); // apron
    g.appendChild(body(12, 26, lit ? "#1a2332" : "#141a26", "#3a4a63")); // tower
    g.appendChild(E("rect", { x: -3, y: -16, width: 6, height: 6, fill: lit ? "#9be7ff" : "#223049" }));
    g.appendChild(label(0, -20, "✈", lit ? "#cfe8ff" : "#44506a", 13));
  } else if (kind === "stadium") {
    g.appendChild(E("ellipse", { cx: 0, cy: 0, rx: 26, ry: 16, fill: lit ? "#17202e" : "#131a24", stroke: "#3a4a63" }));
    g.appendChild(E("ellipse", { cx: 0, cy: 0, rx: 15, ry: 8, fill: lit ? "#1f6b3a" : "#15241a" }));
    [-26, 26].forEach((x) => g.appendChild(E("circle", { cx: x, cy: -14, r: 2.4, fill: lit ? "#fff6cf" : "#2a3246" })));
  } else if (kind === "mfg") {
    g.appendChild(E("path", { d: "M-22,12 L-22,-6 l8,10 l0,-10 l8,10 l0,-10 l8,10 l0,-10 l6,0 l0,18 Z", fill: lit ? "#1a2130" : "#141a24", stroke: "#3a4a63" }));
    g.appendChild(E("rect", { x: 14, y: -22, width: 6, height: 34, fill: "#273246" }));
    if (lit) { const s = E("circle", { cx: 17, cy: -24, r: 3, fill: "#6b7689", opacity: 0 }); s.appendChild(E("animate", { attributeName: "cy", values: "-24;-40", dur: "3s", repeatCount: "indefinite" })); s.appendChild(E("animate", { attributeName: "opacity", values: "0.4;0", dur: "3s", repeatCount: "indefinite" })); g.appendChild(s); }
  } else { // residential
    [[-16, 0], [0, -3], [16, 0]].forEach(([x, y]) => {
      g.appendChild(E("path", { d: `M${x - 9},${y + 10} l0,-8 l9,-7 l9,7 l0,8 Z`, fill: lit ? "#1a2230" : "#141a24", stroke: "#3a4a63" }));
      g.appendChild(win(x - 2, y + 1, lit));
    });
  }
  return g;
}

function buildingAirport(st) {
  const g = E("g"); const lit = st.powered;
  const stroke = st.status === "closed" ? "#7f1d1d" : st.status === "degraded" ? "#5a4310" : "#2b5a6a";
  const col = st.powered ? (st.status === "normal" ? "#0c1f26" : "#201a0c") : "#1a0f10";
  // runway + centerline
  g.appendChild(E("rect", { x: -34, y: 16, width: 64, height: 8, rx: 2, fill: "#20283a" }));
  g.appendChild(E("line", { x1: -30, y1: 20, x2: 26, y2: 20, stroke: "#3a4a63", "stroke-width": 1, "stroke-dasharray": "5 5" }));
  // terminal
  g.appendChild(body(32, 16, col, stroke));
  g.appendChild(winRow(0, 1, 4, lit, 7));
  // control tower
  const tw = E("g", { transform: "translate(20,-5)" });
  tw.appendChild(E("rect", { x: -5, y: -18, width: 10, height: 26, rx: 2, fill: col, stroke }));
  tw.appendChild(E("rect", { x: -6, y: -22, width: 12, height: 7, rx: 2, fill: lit ? "#9be7ff" : "#223049", stroke }));
  g.appendChild(tw);
  // rotating surveillance radar
  const dish = E("g", { transform: "translate(-22,-10)" });
  dish.appendChild(E("line", { x1: 0, y1: 0, x2: 0, y2: 16, stroke, "stroke-width": 2 }));
  const arm = E("g");
  arm.appendChild(E("path", { d: "M-8,-3 A 8 8 0 0 1 8,-3 Z", fill: st.radar_up ? "#2dd4bf" : "#55606f" }));
  if (st.radar_up) arm.appendChild(E("animateTransform", { attributeName: "transform", type: "rotate", from: "0 0 0", to: "360 0 0", dur: "3s", repeatCount: "indefinite" }));
  dish.appendChild(arm); g.appendChild(dish);
  return g;
}
function buildingTelecom(st) {
  const g = E("g"); const lit = st.powered;
  const stroke = st.up ? (st.status === "degraded" ? "#5a4310" : "#2b5a6a") : "#7f1d1d";
  g.appendChild(body(30, 26, lit ? "#10202a" : "#1a0f10", stroke));
  g.appendChild(winRow(0, -3, 3, lit, 7));
  g.appendChild(winRow(0, 6, 3, lit, 7));
  // lattice microwave tower with drums
  const tw = E("g", { transform: "translate(21,-2)" });
  tw.appendChild(E("path", { d: "M-6,16 L-2,-26 L2,-26 L6,16", fill: "none", stroke, "stroke-width": 1.4 }));
  tw.appendChild(E("path", { d: "M-5,6 L5,-8 M5,6 L-5,-8 M-4,-8 L4,-18", fill: "none", stroke, "stroke-width": 0.8, opacity: 0.8 }));
  tw.appendChild(E("circle", { cx: 0, cy: -20, r: 4, fill: st.mw_up ? "#22d3ee" : "#55606f" }));
  tw.appendChild(E("circle", { cx: 0, cy: -11, r: 3, fill: st.mw_up ? "#22d3ee" : "#55606f" }));
  g.appendChild(tw);
  // fiber node
  g.appendChild(E("circle", { cx: -15, cy: -14, r: 3.5, fill: st.fiber_up ? "#34d399" : "#55606f" }));
  // cellular mast (left) with RF arcs
  const cell = E("g", { transform: "translate(-20,-2)" });
  cell.appendChild(E("line", { x1: 0, y1: 12, x2: 0, y2: -16, stroke, "stroke-width": 1.4 }));
  [5, 9].forEach((r) => cell.appendChild(E("path", { d: `M${-r},-16 A ${r} ${r} 0 0 1 ${r},-16`, fill: "none",
    stroke: st.cell_up ? "#a78bfa" : "#55606f", "stroke-width": 1.2, opacity: 0.9 })));
  g.appendChild(cell);
  // GPS timing marker
  g.appendChild(E("circle", { cx: 12, cy: 12, r: 3, fill: st.timing_ok ? "#fde68a" : "#7f1d1d" }));
  return g;
}
function buildingFor(kind, st) {
  switch (kind) {
    case "power": return buildingPower(st);
    case "water": return buildingWater(st);
    case "hospital": return buildingHospital(st);
    case "base": return buildingBase(st);
    case "airport": return buildingAirport(st);
    case "telecom": return buildingTelecom(st);
    default: return buildingDistrict(kind, st);
  }
}

// ---- responder routes ------------------------------------------------------
const ROUTES = {
  ambulance: "M540,340 L640,275 L720,210",
  hazmat:    "M430,150 C 360,150 360,70 430,70 C 500,70 500,150 430,150",
  repair:    "M80,250 L360,250 L640,250 L980,250",
  convoy:    "M910,150 C 850,180 850,60 910,60 C 970,60 970,180 910,150",
};

// ---- City controller -------------------------------------------------------
const City = {
  svg: null, layers: {}, tf: { x: 0, y: 0, k: 1 }, _sig: null, _vehicles: {},

  init(svg) {
    this.svg = svg; svg.innerHTML = "";
    const vp = E("g", { id: "viewport" });
    svg.appendChild(vp);
    this.vp = vp;
    this.layers.ground = E("g"); this.layers.edges = E("g");
    this.layers.nodes = E("g"); this.layers.veh = E("g");
    [this.layers.ground, this.layers.edges, this.layers.nodes, this.layers.veh].forEach((l) => vp.appendChild(l));
    this._drawGround();
    this._buildVehicles();
    this._wireZoomPan();
    this._applyTf();
  },

  _drawGround() {
    const g = this.layers.ground;
    g.appendChild(E("rect", { x: 0, y: 0, width: 1040, height: 560, fill: "#0b1119" }));
    // roads
    const roads = ["M80,250 H980", "M300,40 V520", "M560,40 V520", "M820,40 V520", "M80,420 H980"];
    roads.forEach((d) => {
      g.appendChild(E("path", { d, stroke: "#161f2c", "stroke-width": 16, fill: "none", "stroke-linecap": "round" }));
      g.appendChild(E("path", { d, stroke: "#243040", "stroke-width": 2, "stroke-dasharray": "8 10", fill: "none" }));
    });
    // sand-table frame + compass + title
    g.appendChild(E("rect", { x: 6, y: 6, width: 1028, height: 548, rx: 10, fill: "none", stroke: "#1e2838", "stroke-width": 2 }));
    g.appendChild(label(60, 30, "N ▲", "#55657c", 11));
    g.appendChild(label(972, 544, "MARIN  ·  SCALE 1:12k", "#3a465a", 10));
  },

  _buildVehicles() {
    const defs = [
      ["ambulance", "🚑", "8s"], ["hazmat", "🧯", "7s"],
      ["repair", "🛠", "11s"], ["convoy", "🚓", "6s"],
    ];
    defs.forEach(([name, glyph, dur]) => {
      const g = E("g", { opacity: 0 });
      const t = label(0, 0, glyph, "#fff", 15); t.removeAttribute("font-weight"); g.appendChild(t);
      const m = E("animateMotion", { dur, repeatCount: "indefinite", rotate: "0", path: ROUTES[name], keyPoints: "0;1;0", keyTimes: "0;0.5;1", calcMode: "linear" });
      g.appendChild(m);
      this.layers.veh.appendChild(g);
      this._vehicles[name] = g;
    });
  },

  _setVeh(name, on) { const v = this._vehicles[name]; if (v) v.setAttribute("opacity", on ? 1 : 0); },

  render(state) {
    const d = state.derived; if (!d || !d.base) return;
    // responders by state
    this._setVeh("ambulance", d.lives_at_risk > 0);
    this._setVeh("hazmat", d.water_quality !== "safe");
    this._setVeh("repair", !d.grid_ok);
    this._setVeh("convoy", d.base.readiness !== "full");

    const sig = this._signature(state);
    if (sig === this._sig) return;   // nothing visual changed; keep animations smooth
    this._sig = sig;
    this._drawEdges(state); this._drawNodes(state);
  },

  _points(state) {
    const pt = {};
    for (const fid in state.facilities) pt[state.facilities[fid].kind] = this._pt(state, fid);
    return pt;
  },

  _signature(state) {
    const d = state.derived, f = state.facilities;
    const facs = Object.values(f).map((x) => `${x.compromised ? 1 : 0}${x.it_compromised ? 1 : 0}`).join("");
    const dist = d.districts.map((x) => (x.powered ? 1 : 0) + (x.water ? 1 : 0)).join("");
    const b = d.base, a = d.airport, t = d.telecom;
    return [d.grid_ok, d.water_flowing, d.water_quality, d.hospital_powered, d.hospital_on_generator,
      d.lives_at_risk, Math.round(d.water_pressure / 6), b.readiness, b.comms, b.perimeter_secure, b.breach,
      a.status, a.radar_up, t.up, t.fiber_up, t.mw_up, t.cell_up, t.timing_ok, t.status, facs, dist].join("|");
  },

  _pt(state, fid) { const m = state.facilities[fid].map; return { x: m.x, y: m.y }; },

  _drawEdges(state) {
    const g = this.layers.edges; g.innerHTML = "";
    const d = state.derived;
    const dmap = Object.fromEntries(d.districts.map((x) => [x.id, x]));
    const edge = (a, b, color, flow, wdt, opacity, dash) => {
      const ln = E("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: color, "stroke-width": wdt, "stroke-linecap": "round", opacity: opacity != null ? opacity : (flow ? 0.95 : 0.4) });
      if (flow) { ln.setAttribute("stroke-dasharray", dash || "2 10"); ln.appendChild(E("animate", { attributeName: "stroke-dashoffset", from: "24", to: "0", dur: "1.1s", repeatCount: "indefinite" })); }
      g.appendChild(ln);
    };
    const arc = (a, b, color, flow) => {
      const mx = (a.x + b.x) / 2, my = Math.min(a.y, b.y) - 55;
      const p = E("path", { d: `M${a.x},${a.y} Q ${mx},${my} ${b.x},${b.y}`, fill: "none", stroke: color, "stroke-width": 1.6, opacity: flow ? 0.8 : 0.3, "stroke-dasharray": "1 7", "stroke-linecap": "round" });
      if (flow) p.appendChild(E("animate", { attributeName: "stroke-dashoffset", from: "16", to: "0", dur: "0.9s", repeatCount: "indefinite" }));
      g.appendChild(p);
    };

    const pt = this._points(state);
    const distPts = d.districts.map((x) => ({ x: x.x, y: x.y }));
    const some = (...a) => a.filter(Boolean);

    // --- telecom transport (drawn first, underneath) ---
    if (pt.telecom) {
      const tel = d.telecom;
      const fColor = tel.fiber_up ? "#34d399" : "#52607a";
      some(pt.power, pt.water, pt.hospital, pt.base, pt.airport).concat(distPts)
        .forEach((t) => edge(pt.telecom, t, fColor, tel.fiber_up, 1.3, tel.fiber_up ? 0.55 : 0.28, "1 7"));
      const mColor = tel.mw_up ? "#22d3ee" : "#52607a";
      some(pt.base, pt.airport, pt.power).forEach((t) => arc(pt.telecom, t, mColor, tel.mw_up));
      // provider pivot: if the telecom OT is owned, show the trust-path reach
      const telFid = Object.keys(state.facilities).find((fid) => state.facilities[fid].kind === "telecom");
      if (telFid && state.facilities[telFid].compromised) {
        some(pt.power, pt.water, pt.hospital, pt.base, pt.airport)
          .forEach((t) => edge(pt.telecom, t, "#f472b6", true, 1.8, 0.85, "3 6"));
      }
    }
    // --- power grid ---
    if (pt.power) {
      const pColor = d.grid_ok ? "#fbbf24" : "#7f1d1d";
      some(pt.water, pt.hospital, pt.base, pt.airport, pt.telecom).concat(distPts)
        .forEach((t) => edge(pt.power, t, pColor, d.grid_ok, 2.5));
    }
    // --- water ---
    if (pt.water) {
      const wColor = !d.water_flowing ? "#334155" : (d.water_quality !== "safe" ? "#ef4444" : "#38bdf8");
      some(pt.hospital, dmap.downtown, dmap.residential)
        .forEach((t) => edge(pt.water, { x: t.x, y: t.y }, wColor, d.water_flowing, 4));
    }
  },

  _drawNodes(state) {
    const g = this.layers.nodes; g.innerHTML = "";
    const d = state.derived, f = state.facilities;
    // districts
    d.districts.forEach((dist) => {
      const node = E("g", { transform: `translate(${dist.x},${dist.y}) scale(0.82)` });
      node.appendChild(buildingDistrict(dist.id, { powered: dist.powered }));
      g.appendChild(node);
      g.appendChild(label(dist.x, dist.y + 30, dist.name, "#8294ad", 10));
    });
    // facility helper
    const place = (fid, kind, st, name, opts = {}) => {
      const p = this._pt(state, fid);
      if (opts.pulse) {
        const ring = E("circle", { cx: p.x, cy: p.y, r: 40, fill: "none", stroke: "#ef4444", "stroke-width": 3 });
        ring.appendChild(E("animate", { attributeName: "r", values: "34;46;34", dur: "1s", repeatCount: "indefinite" }));
        ring.appendChild(E("animate", { attributeName: "opacity", values: "0.9;0.15;0.9", dur: "1s", repeatCount: "indefinite" }));
        g.appendChild(ring);
      }
      const node = E("g", { transform: `translate(${p.x},${p.y})` });
      node.appendChild(buildingFor(kind, st));
      g.appendChild(node);
      g.appendChild(label(p.x, p.y + 34, name, "#aebcd2", 11));
      if (opts.badges) {
        let by = p.y + 46;
        opts.badges.forEach((b) => { g.appendChild(label(p.x, by, b.t, b.ok ? "#4ade80" : "#f87171", 9)); by += 11; });
      }
      if (opts.sub) g.appendChild(label(p.x, p.y + 46, opts.sub, "#f59e0b", 9));
      if (f[fid].compromised) g.appendChild(label(p.x, p.y - 34, "⚠ OT OWNED", "#f472b6", 9));
      else if (f[fid].it_compromised) g.appendChild(label(p.x, p.y - 34, "• IT FOOTHOLD", "#f59e0b", 9));
    };
    for (const fid in f) {
      const kind = f[fid].kind, name = f[fid].name;
      if (kind === "power") {
        place(fid, "power", { powered: d.grid_ok, running: d.grid_ok, alarm: !d.grid_ok }, name);
      } else if (kind === "water") {
        place(fid, "water", { powered: d.grid_ok, quality: d.water_quality, pressure: d.water_pressure }, name);
      } else if (kind === "hospital") {
        place(fid, "hospital", { powered: d.hospital_powered }, name,
          { pulse: d.lives_at_risk > 0, sub: d.hospital_on_generator ? `GEN · ${d.hospital_gen_fuel_min}min` : null });
      } else if (kind === "base") {
        const b = d.base;
        place(fid, "base", { powered: b.powered, readiness: b.readiness, comms: b.comms, breach: b.breach }, name,
          { pulse: b.breach, badges: [{ t: b.comms ? "C2 ✓" : "C2 DOWN", ok: b.comms }, { t: b.perimeter_secure ? "PERIM ✓" : "PERIM ✗", ok: b.perimeter_secure }] });
      } else if (kind === "airport") {
        const a = d.airport;
        place(fid, "airport", { powered: a.powered, radar_up: a.radar_up, status: a.status }, name,
          { badges: [{ t: a.radar_up ? "RADAR ✓" : "RADAR ✗", ok: a.radar_up }, { t: "ATC " + a.status.toUpperCase(), ok: a.status === "normal" }] });
      } else if (kind === "telecom") {
        const tel = d.telecom;
        place(fid, "telecom", { powered: tel.powered, up: tel.up, status: tel.status,
          fiber_up: tel.fiber_up, mw_up: tel.mw_up, cell_up: tel.cell_up, timing_ok: tel.timing_ok }, name,
          { pulse: !tel.up || f[fid].compromised,
            badges: [
              { t: tel.fiber_up ? "FIBER ✓" : "FIBER ✗", ok: tel.fiber_up },
              { t: tel.mw_up ? "MW ✓" : "MW ✗", ok: tel.mw_up },
              { t: tel.cell_up ? "CELL ✓" : "CELL ✗", ok: tel.cell_up },
              { t: tel.timing_ok ? "GPS ✓" : "GPS SPOOF", ok: tel.timing_ok },
            ] });
      }
    }
  },

  // ---- zoom / pan ----------------------------------------------------------
  _applyTf() { this.vp.setAttribute("transform", `translate(${this.tf.x},${this.tf.y}) scale(${this.tf.k})`); },
  reset() { this.tf = { x: 0, y: 0, k: 1 }; this._applyTf(); },
  zoomBy(factor, cx, cy) {
    const k2 = Math.min(4, Math.max(0.6, this.tf.k * factor));
    const r = this.svg.getBoundingClientRect();
    const px = cx == null ? r.width / 2 : cx - r.left, py = cy == null ? r.height / 2 : cy - r.top;
    // keep point under cursor stable (in viewBox units ~ svg is 1040 wide)
    const sx = 1040 / r.width, sy = 560 / r.height;
    const ux = (px * sx - this.tf.x) / this.tf.k, uy = (py * sy - this.tf.y) / this.tf.k;
    this.tf.k = k2; this.tf.x = px * sx - ux * k2; this.tf.y = py * sy - uy * k2;
    this._applyTf();
  },
  _wireZoomPan() {
    const svg = this.svg; let drag = null, pinch = null;
    svg.addEventListener("wheel", (e) => { e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1.12 : 0.89, e.clientX, e.clientY); }, { passive: false });
    svg.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, tx: this.tf.x, ty: this.tf.y }; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const r = svg.getBoundingClientRect(), sx = 1040 / r.width, sy = 560 / r.height;
      this.tf.x = drag.tx + (e.clientX - drag.x) * sx; this.tf.y = drag.ty + (e.clientY - drag.y) * sy; this._applyTf();
    });
    svg.addEventListener("pointerup", () => (drag = null));
    svg.addEventListener("pointercancel", () => (drag = null));
    // touch pinch
    svg.addEventListener("touchmove", (e) => {
      if (e.touches.length !== 2) return; e.preventDefault();
      const [a, b] = e.touches, dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
      if (pinch) this.zoomBy(dist / pinch, mx, my);
      pinch = dist;
    }, { passive: false });
    svg.addEventListener("touchend", () => (pinch = null));
  },
};
window.City = City;
