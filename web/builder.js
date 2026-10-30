"use strict";
// Anankos Topology Builder: compose a site from facility templates, position
// and rename them, auto-wire power/water/comms links, and run it in the sim.

const SVGNS = "http://www.w3.org/2000/svg";
const ICON = { power: "⚡", water: "💧", hospital: "🏥", base: "🪖", airport: "✈️", telecom: "📡" };
const PREFIX = { power: "pp", water: "wt", hospital: "hp", base: "fb", airport: "ap", telecom: "tc" };
const RES_COLOR = { power: "#fbbf24", water: "#38bdf8", comms: "#34d399" };

let TEMPLATES = {};   // id -> template (with consumes/provides, icon, facility_kind)
let SITE = { name: "Marin", placements: [], districts: [] };
let SELECTED = null;

const $ = (id) => document.getElementById(id);
const svg = () => $("canvas");
function el(tag, attrs = {}, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.style.display = "block";
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.style.display = "none"), 3000);
}
async function jget(u) { return (await fetch(u)).json(); }
async function jpost(u, b) { return (await fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })).json(); }

// ---- palette ---------------------------------------------------------------
function renderPalette() {
  const placedKinds = new Set(SITE.placements.map((p) => TEMPLATES[p.template].facility_kind));
  $("palette").innerHTML = Object.values(TEMPLATES).map((t) => {
    const placed = placedKinds.has(t.facility_kind);
    return `<div class="pal ${placed ? "placed" : ""}" data-t="${t.id}">
      <span class="ico">${ICON[t.facility_kind] || "■"}</span>
      <span><div class="nm">${t.name}</div><div class="meta">${t.nodes.map((n) => n.arch).join(", ")} · ${t.device_count} devices</div></span>
    </div>`;
  }).join("");
  $("palette").querySelectorAll("[data-t]").forEach((p) => {
    p.onclick = () => addTemplate(p.dataset.t);
  });
}

function addTemplate(tid) {
  const t = TEMPLATES[tid];
  if (SITE.placements.some((p) => TEMPLATES[p.template].facility_kind === t.facility_kind)) {
    return toast("One of each facility kind (for now).", true);
  }
  // place it at an open-ish spot
  const n = SITE.placements.length;
  SITE.placements.push({
    template: tid, facility: t.facility_kind, name: t.name,
    prefix: PREFIX[t.facility_kind] || t.facility_kind.slice(0, 2),
    map: { x: 160 + (n % 4) * 230, y: 150 + Math.floor(n / 4) * 180 },
  });
  SELECTED = t.facility_kind;
  renderAll();
}

// ---- canvas ----------------------------------------------------------------
function autolinks() {
  // facility-level links from template consumes/provides (mirrors backend autowire)
  const providers = {};
  SITE.placements.forEach((p) => {
    (TEMPLATES[p.template].provides || []).forEach((pr) => {
      providers[pr.resource + ":" + pr.role] = p.facility;
    });
  });
  const links = [];
  SITE.placements.forEach((p) => {
    (TEMPLATES[p.template].consumes || []).forEach((c) => {
      const src = providers[c.resource + ":" + c.role];
      if (src && src !== p.facility) links.push({ src, dst: p.facility, resource: c.resource });
    });
  });
  return links;
}

function facById(fid) { return SITE.placements.find((p) => p.facility === fid); }

function renderCanvas() {
  const s = svg(); s.innerHTML = "";
  // links first
  autolinks().forEach((ln) => {
    const a = facById(ln.src), b = facById(ln.dst);
    if (!a || !b) return;
    const line = el("line", { x1: a.map.x, y1: a.map.y, x2: b.map.x, y2: b.map.y,
      stroke: RES_COLOR[ln.resource] || "#64748b", "stroke-width": ln.resource === "power" ? 2.5 : 2,
      opacity: 0.8, "stroke-dasharray": ln.resource === "comms" ? "2 6" : "0" });
    s.appendChild(line);
  });
  // nodes
  SITE.placements.forEach((p) => {
    const g = el("g", { transform: `translate(${p.map.x},${p.map.y})`, style: "cursor:grab", "data-fid": p.facility });
    const sel = SELECTED === p.facility;
    g.appendChild(el("rect", { x: -52, y: -30, width: 104, height: 60, rx: 10,
      fill: "#16202e", stroke: sel ? "#2dd4bf" : "#2b3a4a", "stroke-width": sel ? 2.5 : 1.5 }));
    g.appendChild(el("text", { x: 0, y: -4, "text-anchor": "middle", "font-size": 22 }, ICON[p.facility] || "■"));
    const nm = el("text", { x: 0, y: 20, "text-anchor": "middle", "font-size": 10, fill: "#aebcd2", "font-weight": "600" }, p.name.length > 18 ? p.name.slice(0, 17) + "…" : p.name);
    g.appendChild(nm);
    s.appendChild(g);
  });
  wireDrag();
}

function toView(evt) {
  const r = svg().getBoundingClientRect();
  return { x: (evt.clientX - r.left) * (1040 / r.width), y: (evt.clientY - r.top) * (600 / r.height) };
}
function wireDrag() {
  let drag = null, moved = false;
  svg().querySelectorAll("[data-fid]").forEach((g) => {
    g.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const p = facById(g.dataset.fid);
      const v = toView(e);
      drag = { p, dx: p.map.x - v.x, dy: p.map.y - v.y }; moved = false;
      SELECTED = g.dataset.fid; renderInspector();  // panel only; no canvas redraw yet
    });
    g.addEventListener("pointermove", (e) => {
      if (!drag) return; moved = true;
      const v = toView(e);
      drag.p.map.x = Math.max(60, Math.min(980, Math.round(v.x + drag.dx)));
      drag.p.map.y = Math.max(40, Math.min(560, Math.round(v.y + drag.dy)));
      renderCanvas();
    });
    g.addEventListener("pointerup", () => { drag = null; renderCanvas(); });
  });
}
function highlight() { renderCanvas(); }

// ---- inspector -------------------------------------------------------------
function renderInspector() {
  const p = SELECTED && facById(SELECTED);
  if (!p) { $("inspector").innerHTML = `<div class="hint">Select a facility on the canvas.</div>`; return; }
  const t = TEMPLATES[p.template];
  $("inspector").innerHTML = `
    <div class="field"><label>Facility name</label><input id="i-name" value="${p.name.replace(/"/g, "&quot;")}"></div>
    <div class="row"><b>type</b> ${t.name} <span class="mono">${t.facility_kind}</span></div>
    <div class="row"><b>device prefix</b> <span class="mono">${p.prefix}</span></div>
    <div class="row"><b>nodes</b> ${t.nodes.map((n) => n.arch).join(", ")}</div>
    <div class="row"><b>consumes</b> ${(t.consumes || []).map((c) => c.resource).join(", ") || "—"}</div>
    <div class="row"><b>provides</b> ${(t.provides || []).map((c) => c.resource).join(", ") || "—"}</div>
    <button id="i-remove" class="danger" style="margin-top:10px;width:100%">Remove facility</button>`;
  $("i-name").oninput = (e) => { p.name = e.target.value; renderCanvas(); };
  $("i-remove").onclick = () => {
    SITE.placements = SITE.placements.filter((x) => x.facility !== p.facility);
    SELECTED = null; renderAll();
  };
}

function renderAll() { renderPalette(); renderCanvas(); renderInspector(); }

// ---- site I/O --------------------------------------------------------------
async function loadCurrent() {
  const spec = await jget("/api/site");
  SITE = { name: spec.name || "Site", placements: spec.placements || [], districts: spec.districts || [] };
  $("site-name").value = SITE.name; SELECTED = null; renderAll();
  toast("Loaded current running site.");
}
async function run() {
  SITE.name = $("site-name").value || "Custom Site";
  if (!SITE.placements.some((p) => p.facility === "power")) return toast("Add a Power Plant (grid source) first.", true);
  const res = await jpost("/api/build", { name: SITE.name, placements: SITE.placements, districts: SITE.districts });
  toast(res.ok ? `✓ Built & running: ${res.facilities.join(", ")}` : `✗ ${res.error}`, !res.ok);
}
function exportSite() {
  SITE.name = $("site-name").value || "Site";
  const blob = new Blob([JSON.stringify(SITE, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = `anankos-site-${SITE.name.replace(/\s+/g, "_")}.json`; a.click(); URL.revokeObjectURL(a.href);
}
function importSite(file) {
  const r = new FileReader();
  r.onload = () => { try {
    const s = JSON.parse(r.result);
    SITE = { name: s.name || "Site", placements: s.placements || [], districts: s.districts || [] };
    $("site-name").value = SITE.name; SELECTED = null; renderAll(); toast("Site loaded from file.");
  } catch { toast("Invalid site file.", true); } };
  r.readAsText(file);
}

// ---- wire up ---------------------------------------------------------------
$("b-load").onclick = loadCurrent;
$("b-run").onclick = run;
$("b-export").onclick = exportSite;
$("b-import").onclick = () => $("import-file").click();
$("import-file").onchange = (e) => { if (e.target.files[0]) importSite(e.target.files[0]); e.target.value = ""; };
$("b-reset").onclick = async () => { await jpost("/api/sim/reset"); await loadCurrent(); toast("Reset to Marin."); };

(async function init() {
  const cat = await jget("/api/catalog");
  TEMPLATES = Object.fromEntries(cat.templates.map((t) => [t.id, t]));
  await loadCurrent();
})();
