"use strict";
// Anankos Sim Lab dashboard. Renders the sand-table city, the ATT&CK
// red/consequence console (Enterprise IT -> OT pivot -> ICS impact), and the
// assessment/findings authoring panel. All state comes from the sim server.

const SVGNS = "http://www.w3.org/2000/svg";
let STATE = null, TECHS = [], DOMAIN = "all", AUTH_FAC = null;

const ICON = {
  power: "⚡", water: "💧", hospital: "🏥", base: "🪖",
  downtown: "🏛️", airport: "✈️", stadium: "🏟️", mfg: "🏭", residential: "🏘️",
};
const PARAM_BLOCK = new Set(["safe_min", "safe_max", "max_mw", "closed", "mode", "on_generator"]);
const COMMANDS = {
  pp_bkr: [["open_breaker", "Open main breaker (island plant)"]],
  wt_pump: [["stop_pump", "Stop high-lift pump"]],
  hp_ats: [["force_utility", "Lock transfer switch to utility (inhibit generator)"]],
  fb_sub: [["force_utility", "Lock base transfer switch to utility (inhibit generator)"]],
  fb_perim: [["unlock_gates", "Unlock perimeter gates"]],
};
const FINDING_HINT = {
  internet_exposed: "device reachable from the internet — enables direct OT access (T0883)",
  flat_network: "no IT/OT segmentation — pivot is trivial once IT is breached",
  default_creds: "factory/default credentials in use",
  unpatched: "known-vulnerable, unpatched firmware",
};

const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.style.display = "block";
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.style.display = "none"), 3400);
}

// ---- networking ------------------------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (m) => render(JSON.parse(m.data));
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onerror = () => ws.close();
}
async function post(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

// ---- sand table ------------------------------------------------------------
function facilityPoint(fid) { const f = STATE.facilities[fid]; return { x: f.map.x, y: f.map.y }; }

function drawEdge(svg, a, b, color, flowing, width) {
  const line = el("line", {
    x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: color,
    "stroke-width": width || 3, "stroke-linecap": "round", opacity: flowing ? 0.95 : 0.4,
  });
  if (flowing) {
    line.setAttribute("stroke-dasharray", "2 10");
    line.appendChild(el("animate", { attributeName: "stroke-dashoffset", from: "24", to: "0", dur: "0.8s", repeatCount: "indefinite" }));
  }
  svg.appendChild(line);
}
function drawNode(svg, pt, kind, label, opts = {}) {
  const g = el("g", { transform: `translate(${pt.x},${pt.y})` });
  const w = opts.big ? 58 : 42, h = opts.big ? 58 : 42;
  if (opts.pulse) {
    const ring = el("circle", { r: w / 2 + 10, fill: "none", stroke: "#ef4444", "stroke-width": 3 });
    ring.appendChild(el("animate", { attributeName: "opacity", values: "0.9;0.1;0.9", dur: "1s", repeatCount: "indefinite" }));
    ring.appendChild(el("animate", { attributeName: "r", values: `${w/2+6};${w/2+16};${w/2+6}`, dur: "1s", repeatCount: "indefinite" }));
    g.appendChild(ring);
  }
  g.appendChild(el("rect", {
    x: -w / 2, y: -h / 2, width: w, height: h, rx: 9,
    fill: opts.fill || "#1a2436",
    stroke: opts.stroke || (opts.critical ? "#3b5074" : "#2b3a4a"),
    "stroke-width": opts.critical ? 3 : 1.5,
  }));
  if (!opts.noWindows) {
    for (let i = 0; i < 4; i++) {
      const wx = -w / 2 + 8 + (i % 2) * (w - 22);
      const wy = -h / 2 + 9 + Math.floor(i / 2) * (h - 22);
      g.appendChild(el("rect", { x: wx, y: wy, width: 7, height: 7, rx: 1.5,
        fill: opts.powered ? "#ffd27a" : "#1c2535", opacity: opts.powered ? 0.95 : 0.6 }));
    }
  }
  g.appendChild(el("text", { x: 0, y: opts.big ? 6 : 5, "text-anchor": "middle", "font-size": opts.big ? 26 : 18 }, ICON[kind] || "■"));
  if (opts.water !== undefined) {
    g.appendChild(el("circle", { cx: w / 2 - 6, cy: -h / 2 + 6, r: 4, fill: opts.water ? "#38bdf8" : "#334155" }));
  }
  g.appendChild(el("text", { x: 0, y: h / 2 + 15, "text-anchor": "middle", "font-size": 11, fill: "#9fb0c8", "font-weight": "600" }, label));
  let ly = h / 2 + 28;
  (opts.badges || []).forEach((b) => {
    g.appendChild(el("text", { x: 0, y: ly, "text-anchor": "middle", "font-size": 9,
      fill: b.ok ? "#4ade80" : "#f87171" }, b.t));
    ly += 11;
  });
  if (opts.sub) { g.appendChild(el("text", { x: 0, y: ly, "text-anchor": "middle", "font-size": 9, fill: "#f59e0b" }, opts.sub)); }
  svg.appendChild(g);
}

const READY_COLOR = { full: ["#2b5a3a", "#0d1f14"], degraded: ["#5a4310", "#1b1407"], compromised: ["#7f1d1d", "#1a0d0d"], dark: ["#7f1d1d", "#120808"] };

function renderSandTable() {
  const svg = $("sandtable"); svg.innerHTML = "";
  const d = STATE.derived;
  const pp = facilityPoint("power"), wt = facilityPoint("water"), hp = facilityPoint("hospital"), fb = facilityPoint("base");

  const pColor = d.grid_ok ? "#fbbf24" : "#7f1d1d";
  const dmap = Object.fromEntries(d.districts.map((x) => [x.id, x]));
  [wt, hp, fb, ...d.districts.map((x) => ({ x: x.x, y: x.y }))].forEach((t) => drawEdge(svg, pp, t, pColor, d.grid_ok, 2.5));

  const wColor = !d.water_flowing ? "#334155" : (d.water_quality !== "safe" ? "#ef4444" : "#38bdf8");
  [hp, dmap.downtown, dmap.residential].forEach((t) => { if (t) drawEdge(svg, wt, { x: t.x, y: t.y }, wColor, d.water_flowing, 4); });

  d.districts.forEach((dist) => drawNode(svg, { x: dist.x, y: dist.y }, dist.id, dist.name, { powered: dist.powered, water: dist.water, critical: dist.critical }));

  drawNode(svg, pp, "power", STATE.facilities.power.name, {
    big: true, powered: d.grid_ok, compromised: STATE.facilities.power.compromised,
    stroke: d.grid_ok ? "#6b5410" : "#7f1d1d", fill: d.grid_ok ? "#241f0e" : "#1a0d0d" });
  drawNode(svg, wt, "water", STATE.facilities.water.name, {
    big: true, powered: d.grid_ok, water: d.water_flowing,
    stroke: d.water_quality !== "safe" ? "#7f1d1d" : "#0c4a6e", fill: d.water_quality !== "safe" ? "#1a0d0d" : "#0b2230" });
  if (d.water_quality !== "safe") {
    const plume = el("circle", { cx: wt.x, cy: wt.y, r: 44, fill: "#ef4444", opacity: 0.18 });
    plume.appendChild(el("animate", { attributeName: "r", values: "40;52;40", dur: "1.6s", repeatCount: "indefinite" }));
    svg.appendChild(plume);
  }
  drawNode(svg, hp, "hospital", STATE.facilities.hospital.name, {
    big: true, powered: d.hospital_powered, water: d.hospital_water, pulse: d.lives_at_risk > 0,
    stroke: d.hospital_powered ? "#2b5a3a" : "#7f1d1d", fill: d.hospital_powered ? "#0d1f14" : "#1a0d0d",
    sub: d.hospital_on_generator ? `GEN · ${d.hospital_gen_fuel_min}min` : null });

  const b = d.base, rc = READY_COLOR[b.readiness] || READY_COLOR.full;
  drawNode(svg, fb, "base", STATE.facilities.base.name, {
    big: true, powered: b.powered, pulse: b.breach, stroke: rc[0], fill: rc[1],
    badges: [{ t: b.comms ? "C2 ✓" : "C2 DOWN", ok: b.comms }, { t: b.perimeter_secure ? "PERIM ✓" : "PERIM ✗", ok: b.perimeter_secure }],
    sub: b.on_generator ? `GEN · fuel ${b.fuel_level_pct}%` : null });
}

// ---- stats + facilities + log ---------------------------------------------
function cls(bad, warn) { return bad ? "v bad" : warn ? "v warn" : "v good"; }
function renderStats() {
  const d = STATE.derived;
  $("clock").textContent = String(STATE.tick).padStart(3, "0");
  const sev = $("sev"); sev.className = "sev " + d.severity; sev.textContent = d.severity;
  const L = $("s-lives"); L.textContent = d.lives_at_risk; L.className = cls(d.lives_at_risk >= 25, d.lives_at_risk > 0);
  const G = $("s-grid"); G.textContent = `${d.generation_mw} / ${d.demand_mw} MW`; G.className = cls(!d.grid_ok, d.deficit_mw > 0);
  const W = $("s-water"); W.textContent = `${d.water_pressure} psi` + (d.water_quality !== "safe" ? " ⚠" : ""); W.className = cls(!d.water_flowing || d.water_quality !== "safe", d.water_pressure < 40);
  const H = $("s-hosp"); H.textContent = d.hospital_powered ? (d.hospital_on_generator ? "GENERATOR" : "Utility") : "DARK"; H.className = cls(!d.hospital_powered, d.hospital_on_generator);
  const P = $("s-pop"); P.textContent = `${d.population_without_power_pct}%`; P.className = cls(d.population_without_power_pct >= 50, d.population_without_power_pct > 0);
  const B = $("s-base"); B.textContent = d.base.readiness.toUpperCase(); B.className = "v " + d.base.readiness;
}

function deviceBadges(dev) {
  const b = [];
  if (!dev.online) b.push(`<span class="badge off">OFFLINE</span>`);
  if (!dev.functional) b.push(`<span class="badge dos">DoS</span>`);
  if (!dev.safety) b.push(`<span class="badge unsafe">SAFETY OFF</span>`);
  if (Object.keys(dev.spoof).length) b.push(`<span class="badge spoof">SPOOFED</span>`);
  return b.join(" ");
}
function stateSummary(dev) {
  return Object.entries(dev.state)
    .filter(([k]) => !["safe_min", "safe_max", "max_mw"].includes(k))
    .map(([k, v]) => `${k}=${typeof v === "number" ? Math.round(v * 10) / 10 : v}`).join("  ");
}
function renderFacilities() {
  const root = $("facilities");
  const openSet = new Set([...root.querySelectorAll(".facility.open")].map((e) => e.dataset.fid));
  root.innerHTML = "";
  for (const fid in STATE.facilities) {
    const f = STATE.facilities[fid];
    const wrap = document.createElement("div");
    wrap.className = "facility" + (openSet.has(fid) || openSet.size === 0 ? " open" : "");
    wrap.dataset.fid = fid;
    const access = f.compromised ? `<span class="badge spoof">OT OWNED</span>` : (f.it_compromised ? `<span class="badge dos">IT FOOTHOLD</span>` : "");
    const head = document.createElement("div");
    head.className = "fhead";
    head.innerHTML = `<span class="dot ${f.compromised ? "compromised" : ""}"></span>
      <span class="name">${f.name}</span> ${access}<span class="tag">${f.kind}</span>`;
    head.onclick = () => wrap.classList.toggle("open");
    wrap.appendChild(head);
    const dv = document.createElement("div"); dv.className = "devices";
    f.devices.forEach((did) => {
      const dev = STATE.devices[did];
      const row = document.createElement("div"); row.className = "device";
      const meta = dev.type === "IT" ? "enterprise/business network" : stateSummary(dev);
      row.innerHTML = `<span class="dname">${dev.name}<div class="dmeta">${meta}</div></span>
        <span class="badge">${dev.type}</span> ${deviceBadges(dev)}
        <button data-restore="${did}" style="padding:3px 8px">Restore</button>`;
      dv.appendChild(row);
    });
    wrap.appendChild(dv); root.appendChild(wrap);
  }
  root.querySelectorAll("[data-restore]").forEach((b) => {
    b.onclick = async (e) => { e.stopPropagation(); await post(`/api/restore/${b.dataset.restore}`); };
  });
}
function renderLog() {
  const log = $("log");
  log.innerHTML = STATE.events.slice().reverse().map((ev) =>
    `<div class="ev ${ev.level} ${ev.source === "attacker" ? "attack" : ""}">
      <span class="t">T+${String(ev.tick).padStart(3, "0")}</span>
      <span class="tg">${ev.technique ? `<span class="attid">${ev.technique}</span>` : ev.tag}</span>
      <span class="m">${ev.message}</span></div>`).join("");
}

// ---- attack console --------------------------------------------------------
function visibleTechs() { return TECHS.filter((t) => DOMAIN === "all" || t.domain === DOMAIN); }
function populateTechniques() {
  const sel = $("tech"), prev = sel.value;
  sel.innerHTML = visibleTechs().map((t) =>
    `<option value="${t.id}">[${t.domain === "enterprise" ? "IT" : "OT"}] ${t.id} · ${t.name} — ${t.tactic}</option>`).join("");
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  sel.onchange = onTechChange; onTechChange();
}
function currentTech() { return TECHS.find((t) => t.id === $("tech").value) || visibleTechs()[0]; }
function onTechChange() {
  const t = currentTech(); if (!t) return;
  const need = { none: "no access needed", it: "needs IT foothold", it_or_exposed: "needs IT foothold or exposed device", exposed: "needs internet-exposed device", ot: "needs OT access" }[t.requires];
  $("tech-desc").innerHTML = `<span class="attid">${t.id}</span> — ${t.description} <em>(${need})</em>`;
  const tgt = $("target"), opts = [];
  for (const did in STATE.devices) {
    const dev = STATE.devices[did];
    if (t.applies_to.includes(dev.type)) {
      const fac = STATE.facilities[dev.facility];
      opts.push(`<option value="${did}">${fac.name} · ${dev.name} (${dev.type})</option>`);
    }
  }
  tgt.innerHTML = opts.join(""); tgt.onchange = renderParams; renderParams();
}
function renderParams() {
  const t = currentTech(), did = $("target").value, dev = STATE.devices[did];
  const wrap = $("param-wrap"), psel = $("param"), val = $("value"), plabel = $("param-label");
  if (!dev) { wrap.style.display = "none"; return; }
  if (t.effect === "command") {
    wrap.style.display = "block"; plabel.textContent = "Command";
    const cmds = COMMANDS[did] || [];
    psel.innerHTML = cmds.map(([c, label]) => `<option value="${c}">${label}</option>`).join("") || `<option value="">(no command for this device)</option>`;
    psel.style.display = "block"; val.style.display = "none";
  } else if (t.effect === "set_param" || t.effect === "spoof") {
    wrap.style.display = "block";
    plabel.textContent = t.effect === "spoof" ? "Reading to falsify / value" : "Parameter / value";
    const keys = Object.keys(dev.state).filter((k) => !PARAM_BLOCK.has(k) && typeof dev.state[k] === "number");
    psel.innerHTML = keys.map((k) => `<option value="${k}">${k} (now ${Math.round(dev.state[k] * 10) / 10})</option>`).join("") || `<option value="">(no parameter)</option>`;
    psel.style.display = "block"; val.style.display = "block"; val.value = "";
    val.placeholder = keys.length ? `new value for ${keys[0]}` : "value";
    psel.onchange = () => (val.placeholder = `new value for ${psel.value}`);
  } else { wrap.style.display = "none"; }
}
async function execute() {
  const t = currentTech(), device_id = $("target").value;
  if (!device_id) return toast("No valid target device.", true);
  const params = {};
  if (t.effect === "command") params.command = $("param").value;
  else if (t.effect === "set_param" || t.effect === "spoof") { params.param = $("param").value; params.value = $("value").value; }
  const res = await post("/api/execute", { technique_id: t.id, device_id, params });
  toast(res.ok ? `✓ ${res.message}` : `✗ ${res.error}`, !res.ok);
}

// ---- findings / authoring --------------------------------------------------
function renderAuthoring() {
  const facSel = $("auth-fac");
  if (!AUTH_FAC) AUTH_FAC = Object.keys(STATE.facilities)[0];
  facSel.innerHTML = Object.values(STATE.facilities).map((f) => `<option value="${f.id}">${f.name}</option>`).join("");
  facSel.value = AUTH_FAC;
  facSel.onchange = () => { AUTH_FAC = facSel.value; renderAuthoring(); };

  const f = STATE.facilities[AUTH_FAC];
  $("findings").innerHTML = Object.entries(f.findings).map(([k, v]) =>
    `<label class="chk"><input type="checkbox" data-find="${k}" ${v ? "checked" : ""}>
      <span>${k.replace(/_/g, " ")}<div class="hint">${FINDING_HINT[k] || ""}</div></span></label>`).join("");
  $("findings").querySelectorAll("[data-find]").forEach((c) => {
    c.onchange = () => post("/api/finding", { facility_id: AUTH_FAC, key: c.dataset.find, value: c.checked });
  });

  const auth = $("authoring"); auth.innerHTML = "";
  f.devices.forEach((did) => {
    const dev = STATE.devices[did];
    const row = document.createElement("div"); row.className = "auth-dev";
    const flags = ["online", "functional", "safety"].map((fl) =>
      `<span class="pill ${dev[fl] ? "on" : "off"}" data-flag="${fl}" data-dev="${did}">${fl}: ${dev[fl] ? "on" : "OFF"}</span>`).join("");
    const keys = Object.keys(dev.state).filter((k) => !PARAM_BLOCK.has(k) && typeof dev.state[k] === "number");
    const paramUI = keys.length
      ? `<select data-pdev="${did}">${keys.map((k) => `<option value="${k}">${k}=${Math.round(dev.state[k]*10)/10}</option>`).join("")}</select>
         <input type="text" data-pval="${did}" placeholder="value" style="max-width:80px">
         <button class="set" data-pset="${did}">Set</button>` : "";
    row.innerHTML = `<div class="anm">${dev.name} <span class="badge">${dev.type}</span></div>
      <div class="arow">${flags} ${paramUI}</div>`;
    auth.appendChild(row);
  });
  auth.querySelectorAll("[data-flag]").forEach((p) => {
    p.onclick = () => {
      const dev = STATE.devices[p.dataset.dev];
      post("/api/device_flag", { device_id: p.dataset.dev, flag: p.dataset.flag, value: !dev[p.dataset.flag] });
    };
  });
  auth.querySelectorAll("[data-pset]").forEach((btn) => {
    btn.onclick = () => {
      const did = btn.dataset.pset;
      const param = auth.querySelector(`[data-pdev="${did}"]`).value;
      const value = auth.querySelector(`[data-pval="${did}"]`).value;
      if (value === "") return toast("Enter a value.", true);
      post("/api/manual", { device_id: did, param, value });
    };
  });
}

async function exportScenario() {
  const data = await (await fetch("/api/scenario")).json();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `anankos-scenario-T${STATE.tick}.json`; a.click();
  URL.revokeObjectURL(a.href);
  toast("✓ Scenario exported.");
}
function importScenario(file) {
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const data = JSON.parse(reader.result);
      const res = await post("/api/scenario", data);
      toast(res.ok ? "✓ Scenario imported — mirrors your findings." : `✗ ${res.error}`, !res.ok);
    } catch (e) { toast("✗ Invalid scenario file.", true); }
  };
  reader.readAsText(file);
}

// ---- main render -----------------------------------------------------------
function render(snap) {
  const first = STATE === null;
  STATE = snap;
  if (snap.techniques && snap.techniques.length && TECHS.length === 0) TECHS = snap.techniques;
  renderSandTable(); renderStats(); renderFacilities(); renderLog(); renderAuthoring();
  if (first && TECHS.length) populateTechniques();
  $("btn-start").disabled = snap.running; $("btn-pause").disabled = !snap.running;
}

// ---- wire up ---------------------------------------------------------------
$("btn-start").onclick = () => post("/api/sim/start");
$("btn-pause").onclick = () => post("/api/sim/pause");
$("btn-reset").onclick = () => post("/api/sim/reset");
$("btn-exec").onclick = execute;
$("btn-export").onclick = exportScenario;
$("btn-import").onclick = () => $("import-file").click();
$("import-file").onchange = (e) => { if (e.target.files[0]) importScenario(e.target.files[0]); e.target.value = ""; };
$("domain-seg").querySelectorAll("[data-dom]").forEach((b) => {
  b.onclick = () => {
    DOMAIN = b.dataset.dom;
    $("domain-seg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    populateTechniques();
  };
});

(async function init() {
  const snap = await (await fetch("/api/state")).json();
  TECHS = (await (await fetch("/api/techniques")).json()).techniques;
  render(snap); populateTechniques(); connect();
})();
