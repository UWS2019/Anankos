'use strict';
/* renderer.js - UI logic. Talks to the Node scan engine only through
   window.guardian (defined in preload.js). No direct Node access here. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let mode = 'simple';
let lastReport = null;
let scanning = false;
let detachProgress = null;

const views = ['start', 'scanning', 'results', 'error'];
function show(view) {
  views.forEach((v) => $(`view-${v}`).classList.toggle('active', v === view));
}

// ---- Mode toggle ----
function setMode(m) {
  mode = m;
  $('tab-simple').classList.toggle('active', m === 'simple');
  $('tab-expert').classList.toggle('active', m === 'expert');
  $('tab-simple').setAttribute('aria-selected', m === 'simple');
  $('tab-expert').setAttribute('aria-selected', m === 'expert');
  $('expert-controls').hidden = m !== 'expert';
}
$('tab-simple').onclick = () => setMode('simple');
$('tab-expert').onclick = () => setMode('expert');

// ---- Load network info on startup ----
async function loadNetInfo() {
  try {
    const info = await window.guardian.getNetworkInfo();
    if (info && info.ok) {
      $('chip-cidr').textContent = info.cidr;
      $('chip-gw').textContent = info.gateway || 'unknown';
      $('chip-ip').textContent = info.localIp;
      $('scope-cidr').textContent = info.cidr;
    } else {
      $('chip-cidr').textContent = 'offline';
    }
  } catch (_) { /* ignore */ }
}

// ---- Start scan ----
async function startScan() {
  if (scanning) return;
  scanning = true;
  show('scanning');
  $('progress-bar').style.width = '0%';
  $('scan-phase').textContent = 'Preparing…';
  $('scan-counts').textContent = '';

  const opts = { mode };
  if (mode === 'expert') {
    opts.portSpec = $('exp-ports').value.trim();
    const t = $('exp-target').value.trim();
    if (t) opts.target = t;
    const to = parseInt($('exp-timeout').value, 10);
    if (to) opts.timeout = to;
  }

  // Subscribe to progress.
  if (detachProgress) detachProgress();
  detachProgress = window.guardian.onProgress(onProgress);

  const report = await window.guardian.startScan(opts);
  scanning = false;
  if (detachProgress) { detachProgress(); detachProgress = null; }

  if (!report || !report.ok) {
    $('error-msg').textContent = (report && report.error) || 'Unknown error.';
    show('error');
    return;
  }
  lastReport = report;
  renderResults(report);
  show('results');
}

const PHASE_LABEL = {
  netinfo: 'Detecting your network',
  discovery: 'Finding devices on your network',
  ports: 'Checking each device for risky services',
  wifi: 'Checking your Wi-Fi security',
  analyze: 'Reviewing results'
};

function onProgress(evt) {
  if (evt.type === 'phase') {
    $('scan-phase').textContent = (PHASE_LABEL[evt.phase] || evt.message || '') + '…';
    if (evt.phase === 'discovery') $('progress-bar').style.width = '8%';
    if (evt.phase === 'ports') $('progress-bar').style.width = '55%';
    if (evt.phase === 'wifi') $('progress-bar').style.width = '92%';
    if (evt.phase === 'analyze') $('progress-bar').style.width = '97%';
  }
  if (evt.type === 'progress' && evt.phase === 'discovery') {
    const pct = 8 + Math.round((evt.done / evt.total) * 42);
    $('progress-bar').style.width = pct + '%';
    $('scan-counts').textContent = `scanned ${evt.done} of ${evt.total} addresses`;
  }
  if (evt.type === 'devices') {
    $('scan-counts').textContent = `found ${evt.count} device${evt.count === 1 ? '' : 's'}`;
  }
  if (evt.type === 'progress' && evt.phase === 'ports') {
    const pct = 55 + Math.round((evt.done / evt.total) * 37);
    $('progress-bar').style.width = pct + '%';
    if (evt.current) $('scan-counts').textContent = `checking device ${evt.done + 1} of ${evt.total} (${evt.current})`;
  }
  if (evt.type === 'done') $('progress-bar').style.width = '100%';
}

// ---- Render results ----
const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info'];

function renderResults(r) {
  // Score ring
  const circ = 2 * Math.PI * 52;
  const offset = circ - (r.score / 100) * circ;
  const ring = $('ring-fg');
  ring.style.strokeDashoffset = circ;
  requestAnimationFrame(() => { ring.style.strokeDashoffset = offset; });
  const col = r.score >= 75 ? 'var(--green)' : r.score >= 45 ? 'var(--amber)' : 'var(--red)';
  ring.style.stroke = col;
  $('score-val').textContent = r.score;
  $('grade').textContent = 'Network Health — ' + r.grade;

  // Summary line
  const c = r.counts;
  const urgent = c.critical + c.high;
  let line;
  if (urgent === 0 && c.medium === 0) line = 'No serious problems found. A few optional tips below.';
  else if (urgent === 0) line = `No urgent problems. ${c.medium} thing${c.medium === 1 ? '' : 's'} worth tidying up.`;
  else line = `${urgent} issue${urgent === 1 ? '' : 's'} need your attention. Here is exactly what to do.`;
  $('summary-line').textContent = line;

  // Badges
  const badges = $('severity-badges'); badges.innerHTML = '';
  SEV_ORDER.forEach((s) => {
    if (c[s] > 0) badges.appendChild(el('span', `badge ${s}`, `${c[s]} ${s}`));
  });
  badges.appendChild(el('span', 'badge info', `${r.devices.length} devices`));

  // Findings
  const wrap = $('findings'); wrap.innerHTML = '';
  if (!r.findings.length) {
    wrap.appendChild(el('div', 'no-findings', '<div class="big">Your network looks healthy.</div><div>Guardian did not find anything to fix. Run again any time you add a new device.</div>'));
  } else {
    r.findings.forEach((f, i) => wrap.appendChild(renderFinding(f, i, r.routerUrl)));
  }

  // Devices
  renderDevices(r);
}

function renderFinding(f, i, routerUrl) {
  const node = el('div', `finding ${f.severity}${i === 0 && (f.severity === 'critical' || f.severity === 'high') ? ' open' : ''}`);
  const head = el('div', 'finding-head');
  head.appendChild(el('span', 'sev-pill', esc(f.severity)));
  head.appendChild(el('div', 'finding-title', `${esc(f.title)}<span class="plain">${esc(f.plain)}</span>`));
  head.appendChild(el('span', 'chev', '&#9656;'));
  head.onclick = () => node.classList.toggle('open');
  node.appendChild(head);

  const body = el('div', 'finding-body');
  if (f.why) body.appendChild(el('div', 'why', `<b>Why it matters:</b> ${esc(f.why)}`));
  body.appendChild(el('div', 'steps-label', 'How to fix it'));
  const ol = el('ol', 'steps');
  (f.steps || []).forEach((s) => ol.appendChild(el('li', null, esc(s))));
  body.appendChild(ol);

  if (f.routerLink && routerUrl) {
    const btn = el('button', 'fix-router', 'Open router settings &nbsp;&#8599;');
    btn.onclick = () => window.guardian.openExternal(routerUrl);
    body.appendChild(btn);
  }
  node.appendChild(body);
  return node;
}

function renderDevices(r) {
  const list = $('device-list'); list.innerHTML = '';
  r.devices.forEach((d) => {
    const row = el('div', 'dev' + (d.isGateway ? ' gw' : '') + (d.isThisDevice ? ' self' : ''));
    row.appendChild(el('div', 'ip', esc(d.ip)));
    const meta = el('div', 'meta');
    const name = d.isGateway ? 'Your router' : d.isThisDevice ? 'This computer' : (d.vendor || 'Unidentified device');
    meta.appendChild(el('div', 'name', esc(name)));
    if (d.mac) meta.appendChild(el('div', 'mac', esc(d.mac)));
    const tags = el('div', 'tags');
    if (!d.ports || !d.ports.length) {
      tags.appendChild(el('span', 'ptag', 'no common ports open'));
    } else {
      d.ports.forEach((p) => tags.appendChild(el('span', `ptag ${p.risk}`, `${p.port} ${esc(p.name)}`)));
    }
    meta.appendChild(tags);
    row.appendChild(meta);
    const role = d.isGateway ? 'ROUTER' : d.isThisDevice ? 'THIS DEVICE' : '';
    row.appendChild(el('div', 'role', role));
    list.appendChild(row);
  });
}

// ---- Buttons ----
$('btn-scan').onclick = startScan;
$('btn-rescan').onclick = () => show('start');
$('btn-cancel').onclick = () => { show('start'); /* engine finishes in bg; result ignored */ scanning = false; };
$('btn-error-back').onclick = () => show('start');
$('btn-view-devices').onclick = () => selectSub('devices');
$('sub-fixes').onclick = () => selectSub('fixes');
$('sub-devices').onclick = () => selectSub('devices');

function selectSub(which) {
  $('sub-fixes').classList.toggle('active', which === 'fixes');
  $('sub-devices').classList.toggle('active', which === 'devices');
  $('panel-fixes').classList.toggle('active', which === 'fixes');
  $('panel-devices').classList.toggle('active', which === 'devices');
}

$('btn-export').onclick = async () => {
  if (!lastReport) return;
  const path = await window.guardian.saveReport(JSON.stringify(lastReport, null, 2));
  if (path) {
    const btn = $('btn-export'); const old = btn.textContent;
    btn.textContent = 'Saved ✓';
    setTimeout(() => { btn.textContent = old; }, 1800);
  }
};

// ---- Init ----
setMode('simple');
show('start');
loadNetInfo();
