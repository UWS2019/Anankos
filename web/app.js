"use strict";
// Anankos Sim Lab dashboard. Renders the sand-table city and drives the
// red/consequence ATT&CK console. All state comes from the sim server.

const SVGNS = "http://www.w3.org/2000/svg";
let STATE = null;
let TECHS = [];

const ICON = {
  power: "⚡", water: "💧", hospital: "🏥",
  downtown: "🏛️", airport: "✈️", stadium: "🏟️",
  mfg: "🏭", base: "🪖", residential: "🏘️",
};
const PARAM_BLOCK = new Set(["safe_min", "safe_max", "max_mw", "closed", "mode", "on_generator"]);
const COMMANDS = {
  pp_bkr: [["open_breaker", "Open main breaker (island plant)"]],
  wt_pump: [["stop_pump", "Stop high-lift pump"]],
  hp_ats: [["force_utility", "Lock transfer switch to utility (inhibit generator)"]],
};

// ---- element helpers -------------------------------------------------------
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.style.display = "block";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.style.display = "none"), 3200);
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
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

// ---- sand table ------------------------------------------------------------
function facilityPoint(fid) {
  const f = STATE.facilities[fid];
  return { x: f.map.x, y: f.map.y };
}
function drawEdge(svg, a, b, color, flowing, width) {
  const line = el("line", {
    x1: a.x, y1: a.y, x2: b.x, y2: b.y,
    stroke: color, "stroke-width": width || 3, "stroke-linecap": "round",
    opacity: flowing ? 0.95 : 0.4,
  });
  if (flowing) {
    line.setAttribute("stroke-dasharray", "2 10");
    const anim = el("animate", {
      attributeName: "stroke-dashoffset", from: "24", to: "0",
      dur: "0.8s", repeatCount: "indefinite",
    });
    line.appendChild(anim);
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
  const rect = el("rect", {
    x: -w / 2, y: -h / 2, width: w, height: h, rx: 9,
    fill: opts.dark ? "#0c1119" : (opts.fill || "#1a2436"),
    stroke: opts.stroke || (opts.critical ? "#3b5074" : "#2b3a4a"),
    "stroke-width": opts.critical ? 3 : 1.5,
  });
  g.appendChild(rect);
  // lit windows when powered
  if (!opts.noWindows) {
    const lit = opts.powered;
    for (let i = 0; i < 4; i++) {
      const wx = -w / 2 + 8 + (i % 2) * (w - 22);
      const wy = -h / 2 + 9 + Math.floor(i / 2) * (h - 22);
      g.appendChild(el("rect", {
        x: wx, y: wy, width: 7, height: 7, rx: 1.5,
        fill: lit ? "#ffd27a" : "#1c2535",
        opacity: lit ? 0.95 : 0.6,
      }));
    }
  }
  g.appendChild(el("text", {
    x: 0, y: opts.big ? 6 : 5, "text-anchor": "middle",
    "font-size": opts.big ? 26 : 18,
  }, ICON[kind] || "■"));
  // water indicator dot
  if (opts.water !== undefined) {
    g.appendChild(el("circle", {
      cx: w / 2 - 6, cy: -h / 2 + 6, r: 4,
      fill: opts.water ? "#38bdf8" : "#334155",
    }));
  }
  const lbl = el("text", {
    x: 0, y: h / 2 + 15, "text-anchor": "middle", "font-size": 11,
    fill: "#9fb0c8", "font-weight": "600",
  }, label);
  g.appendChild(lbl);
  if (opts.compromised) {
    g.appendChild(el("text", { x: 0, y: h / 2 + 28, "text-anchor": "middle", "font-size": 9, fill: "#f472b6" }, "COMPROMISED"));
  }
  svg.appendChild(g);
}

function renderSandTable() {
  const svg = $("sandtable");
  svg.innerHTML = "";
  const d = STATE.derived;
  const pp = facilityPoint("power"), wt = facilityPoint("water"), hp = facilityPoint("hospital");

  // power edges
  const pColor = d.grid_ok ? "#fbbf24" : "#7f1d1d";
  const targets = [wt, hp, ...d.districts.map((x) => ({ x: x.x, y: x.y }))];
  targets.forEach((t) => drawEdge(svg, pp, t, pColor, d.grid_ok, 2.5));

  // water edges (plant -> hospital + a couple districts)
  const wColor = !d.water_flowing ? "#334155" : (d.water_quality !== "safe" ? "#ef4444" : "#38bdf8");
  const dmap = Object.fromEntries(d.districts.map((x) => [x.id, x]));
  [hp, dmap.downtown, dmap.residential].forEach((t) => {
    if (t) drawEdge(svg, wt, { x: t.x, y: t.y }, wColor, d.water_flowing, 4);
  });

  // districts
  d.districts.forEach((dist) => {
    drawNode(svg, { x: dist.x, y: dist.y }, dist.id, dist.name, {
      powered: dist.powered, water: dist.water, critical: dist.critical,
    });
  });

  // facilities (big nodes)
  drawNode(svg, pp, "power", STATE.facilities.power.name, {
    big: true, powered: d.grid_ok,
    compromised: STATE.facilities.power.compromised,
    stroke: d.grid_ok ? "#6b5410" : "#7f1d1d",
    fill: d.grid_ok ? "#241f0e" : "#1a0d0d",
  });
  drawNode(svg, wt, "water", STATE.facilities.water.name, {
    big: true, powered: d.grid_ok, water: d.water_flowing,
    compromised: STATE.facilities.water.compromised,
    stroke: d.water_quality !== "safe" ? "#7f1d1d" : "#0c4a6e",
    fill: d.water_quality !== "safe" ? "#1a0d0d" : "#0b2230",
  });
  if (d.water_quality !== "safe") {
    const plume = el("circle", { cx: wt.x, cy: wt.y, r: 44, fill: "#ef4444", opacity: 0.18 });
    plume.appendChild(el("animate", { attributeName: "r", values: "40;52;40", dur: "1.6s", repeatCount: "indefinite" }));
    svg.appendChild(plume);
  }
  drawNode(svg, hp, "hospital", STATE.facilities.hospital.name, {
    big: true, powered: d.hospital_powered, water: d.hospital_water,
    compromised: STATE.facilities.hospital.compromised,
    pulse: d.lives_at_risk > 0,
    stroke: d.hospital_powered ? "#2b5a3a" : "#7f1d1d",
    fill: d.hospital_powered ? "#0d1f14" : "#1a0d0d",
  });
  if (d.hospital_on_generator) {
    drawNode._genLabel && svg.removeChild(drawNode._genLabel);
    svg.appendChild(el("text", { x: hp.x, y: hp.y - 42, "text-anchor": "middle", "font-size": 10, fill: "#f59e0b" },
      `GENERATOR · ${d.hospital_gen_fuel_min}min fuel`));
  }
}

// ---- stats + facilities + log ---------------------------------------------
function cls(bad, warn) { return bad ? "v bad" : warn ? "v warn" : "v good"; }
function renderStats() {
  const d = STATE.derived;
  $("clock").textContent = String(STATE.tick).padStart(3, "0");
  const sev = $("sev");
  sev.className = "sev " + d.severity;
  sev.textContent = d.severity;
  const L = $("s-lives"); L.textContent = d.lives_at_risk; L.className = cls(d.lives_at_risk >= 25, d.lives_at_risk > 0);
  const G = $("s-grid"); G.textContent = `${d.generation_mw} / ${d.demand_mw} MW`; G.className = cls(!d.grid_ok, d.deficit_mw > 0);
  const W = $("s-water"); W.textContent = `${d.water_pressure} psi` + (d.water_quality !== "safe" ? " ⚠" : ""); W.className = cls(!d.water_flowing || d.water_quality !== "safe", d.water_pressure < 40);
  const H = $("s-hosp");
  H.textContent = d.hospital_powered ? (d.hospital_on_generator ? "GENERATOR" : "Utility") : "DARK";
  H.className = cls(!d.hospital_powered, d.hospital_on_generator);
  const P = $("s-pop"); P.textContent = `${d.population_without_power_pct}%`; P.className = cls(d.population_without_power_pct >= 50, d.population_without_power_pct > 0);
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
    .filter(([k]) => !PARAM_BLOCK.has(k) || k === "closed" || k === "mode" || k === "on_generator")
    .map(([k, v]) => `${k}=${typeof v === "number" ? (Math.round(v * 10) / 10) : v}`)
    .join("  ");
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
    const head = document.createElement("div");
    head.className = "fhead";
    head.innerHTML = `<span class="dot ${f.compromised ? "compromised" : ""}"></span>
      <span class="name">${f.name}</span><span class="tag">${f.kind}</span>`;
    head.onclick = () => wrap.classList.toggle("open");
    wrap.appendChild(head);
    const dv = document.createElement("div");
    dv.className = "devices";
    f.devices.forEach((did) => {
      const dev = STATE.devices[did];
      const row = document.createElement("div");
      row.className = "device";
      row.innerHTML = `<span class="dname">${dev.name}
          <div class="dmeta">${stateSummary(dev)}</div></span>
        <span class="badge">${dev.type}</span> ${deviceBadges(dev)}
        <button data-restore="${did}" style="padding:3px 8px">Restore</button>`;
      dv.appendChild(row);
    });
    wrap.appendChild(dv);
    root.appendChild(wrap);
  }
  root.querySelectorAll("[data-restore]").forEach((b) => {
    b.onclick = async (e) => { e.stopPropagation(); await post(`/api/restore/${b.dataset.restore}`); };
  });
}

function renderLog() {
  const log = $("log");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.innerHTML = STATE.events.slice().reverse().map((ev) =>
    `<div class="ev ${ev.level} ${ev.source === "attacker" ? "attack" : ""}">
      <span class="t">T+${String(ev.tick).padStart(3, "0")}</span>
      <span class="tg">${ev.technique ? `<span class="attid">${ev.technique}</span>` : ev.tag}</span>
      <span class="m">${ev.message}</span></div>`).join("");
}

// ---- attack console --------------------------------------------------------
function populateTechniques() {
  const sel = $("tech");
  sel.innerHTML = TECHS.map((t) => `<option value="${t.id}">${t.id} · ${t.name} — ${t.tactic}</option>`).join("");
  sel.onchange = onTechChange;
  onTechChange();
}
function currentTech() { return TECHS.find((t) => t.id === $("tech").value); }
function onTechChange() {
  const t = currentTech();
  $("tech-desc").innerHTML = `<span class="attid">${t.id}</span> — ${t.description}` +
    (t.requires_compromise ? "" : ` <em>(no prior access needed)</em>`);
  // targets: devices whose type matches
  const tgt = $("target");
  const opts = [];
  for (const did in STATE.devices) {
    const dev = STATE.devices[did];
    if (t.applies_to.includes(dev.type)) {
      const fac = STATE.facilities[dev.facility];
      opts.push(`<option value="${did}">${fac.name} · ${dev.name} (${dev.type})</option>`);
    }
  }
  tgt.innerHTML = opts.join("");
  tgt.onchange = renderParams;
  renderParams();
}
function renderParams() {
  const t = currentTech();
  const did = $("target").value;
  const dev = STATE.devices[did];
  const wrap = $("param-wrap"), psel = $("param"), val = $("value"), plabel = $("param-label");
  if (!dev) { wrap.style.display = "none"; return; }

  if (t.effect === "command") {
    wrap.style.display = "block"; plabel.textContent = "Command";
    const cmds = COMMANDS[did] || [];
    psel.innerHTML = cmds.map(([c, label]) => `<option value="${c}">${label}</option>`).join("")
      || `<option value="">(no command available for this device)</option>`;
    psel.style.display = "block"; val.style.display = "none";
  } else if (t.effect === "set_param" || t.effect === "spoof") {
    wrap.style.display = "block";
    plabel.textContent = t.effect === "spoof" ? "Reading to falsify / value" : "Parameter / value";
    const keys = Object.keys(dev.state).filter((k) => !PARAM_BLOCK.has(k) && typeof dev.state[k] === "number");
    psel.innerHTML = keys.map((k) => `<option value="${k}">${k} (now ${Math.round(dev.state[k] * 10) / 10})</option>`).join("");
    psel.style.display = "block"; val.style.display = "block"; val.value = "";
    psel.onchange = () => { val.placeholder = `new value for ${psel.value}`; };
    val.placeholder = keys.length ? `new value for ${keys[0]}` : "value";
  } else {
    wrap.style.display = "none"; // compromise / shutdown / dos / disable_safety
  }
}
async function execute() {
  const t = currentTech();
  const device_id = $("target").value;
  if (!device_id) return toast("No valid target device.", true);
  const params = {};
  if (t.effect === "command") params.command = $("param").value;
  else if (t.effect === "set_param" || t.effect === "spoof") {
    params.param = $("param").value;
    params.value = $("value").value;
  }
  const res = await post("/api/execute", { technique_id: t.id, device_id, params });
  toast(res.ok ? `✓ ${res.message}` : `✗ ${res.error}`, !res.ok);
}

// ---- main render -----------------------------------------------------------
function render(snap) {
  const first = STATE === null;
  STATE = snap;
  if (snap.techniques && snap.techniques.length && TECHS.length === 0) TECHS = snap.techniques;
  renderSandTable();
  renderStats();
  renderFacilities();
  renderLog();
  if (first && TECHS.length) populateTechniques();
  $("btn-start").disabled = snap.running;
  $("btn-pause").disabled = !snap.running;
}

// ---- wire up ---------------------------------------------------------------
$("btn-start").onclick = () => post("/api/sim/start");
$("btn-pause").onclick = () => post("/api/sim/pause");
$("btn-reset").onclick = () => post("/api/sim/reset");
$("btn-exec").onclick = execute;

(async function init() {
  const snap = await (await fetch("/api/state")).json();
  const techs = await (await fetch("/api/techniques")).json();
  TECHS = techs.techniques;
  render(snap);
  populateTechniques();
  connect();
})();
