"use strict";
// Anankos Sim Lab dashboard. Renders the sand-table city, the ATT&CK
// red/consequence console (Enterprise IT -> OT pivot -> ICS impact), and the
// assessment/findings authoring panel. All state comes from the sim server.

// NOTE: SVG drawing lives in city.js; this file avoids redeclaring its globals.
let STATE = null, TECHS = [], DOMAIN = "all", AUTH_FAC = null;

const PARAM_BLOCK = new Set(["safe_min", "safe_max", "max_mw", "closed", "mode", "on_generator"]);
const COMMANDS = {
  pp_bkr: [["open_breaker", "Open main breaker (island plant)"]],
  wt_pump: [["stop_pump", "Stop high-lift pump"]],
  hp_ats: [["force_utility", "Lock transfer switch to utility (inhibit generator)"]],
  fb_sub: [["force_utility", "Lock base transfer switch to utility (inhibit generator)"]],
  fb_perim: [["unlock_gates", "Unlock perimeter gates"]],
  ap_radar: [["radar_stop", "Stop surveillance radar sweep"]],
  ap_tower: [["comms_off", "Disable tower ATC comms"]],
  tc_fiber: [["cut_fiber", "Cut fiber backbone path"]],
  tc_mw: [["jam_microwave", "Jam microwave relay path"]],
  tc_cell: [["jam_cell", "Jam cellular / RF backhaul"]],
  tc_gps: [["spoof_gps", "Spoof GPS timing (clock drift)"]],
  tc_core: [["hijack_routing", "Hijack core routing (BGP blackhole)"]],
};
const FINDING_HINT = {
  internet_exposed: "device reachable from the internet — enables direct OT access (T0883)",
  flat_network: "no IT/OT segmentation — pivot is trivial once IT is breached",
  default_creds: "factory/default credentials in use",
  unpatched: "known-vulnerable, unpatched firmware",
};

const $ = (id) => document.getElementById(id);
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

// ---- sand table (delegated to City renderer in city.js) --------------------
let CITY_READY = false;
function renderSandTable() {
  if (!CITY_READY) { window.City.init($("sandtable")); CITY_READY = true; }
  window.City.render(STATE);
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
  const present = d.present || { base: true, airport: true, telecom: true };
  const B = $("s-base");
  B.textContent = present.base ? d.base.readiness.toUpperCase() : "—";
  B.className = present.base ? "v " + d.base.readiness : "v";
  const A = $("s-air");
  A.textContent = present.airport ? d.airport.status.toUpperCase() : "—";
  A.className = present.airport ? cls(d.airport.status === "closed", d.airport.status === "degraded") : "v";
  const T = $("s-tel");
  T.textContent = !present.telecom ? "—"
    : d.telecom.up ? (d.telecom.fiber_up && d.telecom.mw_up ? "UP" : "BACKUP") : "DOWN";
  T.className = !present.telecom ? "v" : cls(!d.telecom.up, d.telecom.up && !(d.telecom.fiber_up && d.telecom.mw_up));
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
  const srcClass = (ev) =>
    ev.source === "attacker" || ev.source === "protocol" ? "attack"
      : ev.source === "defense" || ev.source === "blue" ? "blue" : "";
  log.innerHTML = STATE.events.slice().reverse().map((ev) =>
    `<div class="ev ${ev.level} ${srcClass(ev)}">
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

// ---- blue team / D3FEND ----------------------------------------------------
const MODE_LABEL = { block: "BLOCK", detect: "DETECT", mitigate: "MITIGATE" };
function renderDefenses() {
  const s = STATE.score || { attempts: 0, blocked: 0, detected: 0, undetected: 0 };
  $("score").innerHTML = `
    <div class="sc"><div class="k">Attacks</div><div class="v">${s.attempts}</div></div>
    <div class="sc"><div class="k">Blocked</div><div class="v good">${s.blocked}</div></div>
    <div class="sc"><div class="k">Detected</div><div class="v acc">${s.detected}</div></div>
    <div class="sc"><div class="k">Undetected</div><div class="v bad">${s.undetected}</div></div>`;
  const defs = STATE.defenses || [];
  $("defenses").innerHTML = defs.map((d) =>
    `<div class="def ${d.deployed ? "on" : ""}">
       <span class="pill ${d.deployed ? "on" : ""}" data-def="${d.id}">${d.deployed ? "✓ deployed" : "deploy"}</span>
       <div class="dmain">
         <div><b>${d.id}</b> ${d.name} <span class="mode ${d.mode}">${MODE_LABEL[d.mode]}</span></div>
         <div class="hint">${d.d3f} · counters ${d.counters.join(", ")}</div>
       </div>
     </div>`).join("");
  $("defenses").querySelectorAll("[data-def]").forEach((p) => {
    p.onclick = () => {
      const d = defs.find((x) => x.id === p.dataset.def);
      post("/api/defense", { defense_id: p.dataset.def, deployed: !d.deployed });
    };
  });
}

// ---- protocol endpoint card ------------------------------------------------
async function renderProtocol() {
  let p;
  try { p = await (await fetch("/api/protocol")).json(); } catch { return; }
  const host = location.hostname; // reachable address (incl. Tailscale IP)
  if (!p.enabled) { $("protocol").innerHTML = `<div class="hint">Modbus endpoint disabled.</div>`; return; }
  const units = p.map.units.map((u) =>
    `<tr><td>${u.unit}</td><td>${u.facility}</td>
      <td class="mono">${Object.entries({ ...u.holding, ...u.coils }).map(([k, v]) => `${k}:${v}`).join("<br>")}</td>
      <td class="mono">${Object.entries(u.input).map(([k, v]) => `${k}:${v}`).join("<br>")}</td></tr>`).join("");
  $("protocol").innerHTML = `
    <div class="proto-head"><span class="dot-live"></span>
      <b>${p.protocol}</b> &nbsp;<span class="mono">${host}:${p.port}</span>
      <span class="hint">· unauthenticated · simulation only</span></div>
    <table class="proto"><thead><tr><th>Unit</th><th>Facility</th><th>Writable (HR/coil)</th><th>Read (IR)</th></tr></thead>
      <tbody>${units}</tbody></table>
    <div class="hint" style="margin-top:8px">Point real OT tooling at it. Writes appear in the log tagged <span class="attid">MODBUS</span> and drive the city. Examples:</div>
    <pre class="snip"># poison the water (chlorine dose 15.0 ppm) — unit 2, HR1, x10
python -c "from pymodbus.client import ModbusTcpClient as C; c=C('${host}',port=${p.port}); c.write_register(1,150,slave=2)"

# open the power plant main breaker — unit 1, coil 0 OFF
python -c "from pymodbus.client import ModbusTcpClient as C; c=C('${host}',port=${p.port}); c.write_coil(0,False,slave=1)"

# modpoll: read Fort Marin status (unit 4 input regs)
modpoll -m tcp -t 3 -r 1 -c 4 -0 ${host}:${p.port} -a 4</pre>`;
}

// ---- main render -----------------------------------------------------------
function render(snap) {
  const first = STATE === null;
  STATE = snap;
  if (snap.techniques && snap.techniques.length && TECHS.length === 0) TECHS = snap.techniques;
  renderSandTable(); renderStats(); renderFacilities(); renderLog(); renderAuthoring(); renderDefenses();
  if (first && TECHS.length) populateTechniques();
  $("btn-start").disabled = snap.running; $("btn-pause").disabled = !snap.running;
}

// ---- wire up ---------------------------------------------------------------
$("btn-start").onclick = () => post("/api/sim/start");
$("btn-pause").onclick = () => post("/api/sim/pause");
$("btn-reset").onclick = () => post("/api/sim/reset");
$("btn-report").onclick = () => window.open("/api/report.html", "_blank");
$("btn-exec").onclick = execute;
$("btn-export").onclick = exportScenario;
$("btn-import").onclick = () => $("import-file").click();
$("zin").onclick = () => window.City.zoomBy(1.25);
$("zout").onclick = () => window.City.zoomBy(0.8);
$("zreset").onclick = () => window.City.reset();
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
  render(snap); populateTechniques(); renderProtocol(); connect();
})();
