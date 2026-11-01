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

const views = ['start', 'scanning', 'results', 'operator', 'monitor', 'error'];
function show(view) {
  views.forEach((v) => $(`view-${v}`).classList.toggle('active', v === view));
}
// expose for preview/testing harnesses
window.show = show;

// ---- Mode toggle ----
function setMode(m) {
  mode = m;
  $('tab-simple').classList.toggle('active', m === 'simple');
  $('tab-expert').classList.toggle('active', m === 'expert');
  $('tab-simple').setAttribute('aria-selected', m === 'simple');
  $('tab-expert').setAttribute('aria-selected', m === 'expert');
  $('expert-controls').hidden = m !== 'expert';
  $('btn-operator').hidden = m !== 'expert';
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
$('btn-cancel').onclick = () => { show('start'); scanning = false; };
$('btn-error-back').onclick = () => show('start');
$('sub-fixes').onclick = () => selectSub('fixes');
$('sub-devices').onclick = () => selectSub('devices');

function selectSub(which) {
  $('sub-fixes').classList.toggle('active', which === 'fixes');
  $('sub-devices').classList.toggle('active', which === 'devices');
  $('panel-fixes').classList.toggle('active', which === 'fixes');
  $('panel-devices').classList.toggle('active', which === 'devices');
}

// ---- Exports ----
async function doExport(format, btn) {
  if (!lastReport) return;
  const old = btn.textContent;
  btn.textContent = 'Saving…';
  const res = await window.guardian.exportReport(lastReport, format);
  btn.textContent = (res && !res.error) ? 'Saved ✓' : (res && res.error ? 'Failed' : old);
  setTimeout(() => { btn.textContent = old; }, 1800);
}
$('btn-export-html').onclick = (e) => doExport('html', e.currentTarget);
$('btn-export-pdf').onclick = (e) => doExport('pdf', e.currentTarget);
$('btn-export-json').onclick = (e) => doExport('json', e.currentTarget);

// ---- AI debrief modal ----
async function openDebrief() {
  if (!lastReport) return;
  const text = await window.guardian.buildDebrief(lastReport);
  $('debrief-text').value = text || '';
  $('debrief-modal').hidden = false;
}
function closeDebrief() { $('debrief-modal').hidden = true; }
$('btn-debrief').onclick = openDebrief;
$('debrief-close').onclick = closeDebrief;
$('debrief-modal').addEventListener('click', (e) => { if (e.target.id === 'debrief-modal') closeDebrief(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDebrief(); });
$('debrief-copy').onclick = async (e) => {
  await window.guardian.copyText($('debrief-text').value);
  const b = e.currentTarget; const old = b.textContent;
  b.textContent = 'Copied ✓';
  setTimeout(() => { b.textContent = old; }, 1600);
};
$('debrief-save').onclick = async () => {
  const blob = new Blob([$('debrief-text').value], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'anankos-guardian-ai-debrief.txt';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
};

// ---- AI settings + direct send ----
async function loadAiSettings() {
  const s = await window.guardian.aiSettingsLoad();
  if (!s) return;
  $('ai-provider').value = s.provider || 'openai';
  $('ai-baseurl').value = s.baseUrl || '';
  $('ai-model').value = s.model || '';
  $('ai-key-status').textContent = s.hasKey ? '— saved ✓' : '— not set';
}
$('debrief-settings-toggle').onclick = async () => {
  const box = $('ai-settings');
  box.hidden = !box.hidden;
  if (!box.hidden) await loadAiSettings();
};
$('ai-save').onclick = async (e) => {
  const cfg = {
    enabled: true,
    provider: $('ai-provider').value,
    baseUrl: $('ai-baseurl').value.trim(),
    model: $('ai-model').value.trim(),
    apiKey: $('ai-apikey').value // blank keeps saved key (handled in main)
  };
  await window.guardian.aiSettingsSave(cfg);
  $('ai-apikey').value = '';
  const b = e.currentTarget; const old = b.textContent;
  b.textContent = 'Saved ✓'; setTimeout(() => { b.textContent = old; }, 1600);
  await loadAiSettings();
};
$('debrief-send').onclick = async (e) => {
  const wrap = $('ai-response-wrap'); const out = $('ai-response');
  wrap.hidden = false;
  out.className = 'ai-response loading';
  out.textContent = 'Sending to your AI…';
  const b = e.currentTarget; b.disabled = true;
  const res = await window.guardian.aiSend(lastReport, $('debrief-text').value);
  b.disabled = false;
  if (res && res.ok) { out.className = 'ai-response'; out.textContent = res.text; }
  else {
    out.className = 'ai-response err';
    out.textContent = (res && res.error) || 'Failed. Open AI settings and check your endpoint, model and key.';
  }
};

// ---- Harden this PC ----
$('btn-harden').onclick = openHarden;
$('harden-close').onclick = () => { $('harden-modal').hidden = true; };
$('harden-modal').addEventListener('click', (e) => { if (e.target.id === 'harden-modal') $('harden-modal').hidden = true; });

async function openHarden() {
  $('harden-modal').hidden = false;
  const list = $('harden-list');
  list.innerHTML = '<div class="modal-note">Checking this computer…</div>';
  const status = await window.guardian.hardenStatus();
  list.innerHTML = '';
  if (!status || status.error || !status.actions) {
    list.innerHTML = `<div class="harden-msg warn">Could not read this computer's security settings${status && status.error ? ': ' + esc(status.error) : ''}.</div>`;
    return;
  }
  status.actions.forEach((a) => list.appendChild(renderHardenItem(a)));
}

function renderHardenItem(a) {
  const item = el('div', `harden-item ${a.state}`);
  const top = el('div', 'harden-top');
  top.appendChild(el('div', 'harden-title', esc(a.title)));
  top.appendChild(el('span', 'harden-state', esc(a.stateLabel || a.state)));
  item.appendChild(top);
  item.appendChild(el('div', 'harden-plain', esc(a.plain)));

  if (a.state !== 'ok') {
    const btn = el('button', 'harden-apply', 'Apply this fix');
    const msg = el('div', 'harden-msg');
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = 'Applying…';
      const res = await window.guardian.hardenApply(a.id);
      if (res && res.ok) {
        item.className = 'harden-item ok';
        item.querySelector('.harden-state').textContent = 'Fixed ✓';
        msg.className = 'harden-msg ok'; msg.textContent = res.message || 'Done.';
        btn.remove();
      } else {
        btn.disabled = false; btn.textContent = 'Apply this fix';
        msg.className = 'harden-msg warn';
        msg.textContent = (res && res.message) || 'Could not apply automatically.';
        if (res && (res.manualCommand || a.manualCommand)) {
          item.appendChild(renderCmd(res.manualCommand || a.manualCommand));
        }
      }
    };
    item.appendChild(btn);
    item.appendChild(msg);
  }
  return item;
}

function renderCmd(cmd) {
  const box = el('div', 'harden-cmd');
  box.appendChild(el('code', null, esc(cmd)));
  const copy = el('button', null, 'Copy');
  copy.onclick = async () => { await window.guardian.copyText(cmd); copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1500); };
  box.appendChild(copy);
  return box;
}

// =====================================================================
//  OPERATOR CONSOLE
// =====================================================================
$('btn-operator').onclick = () => { openOperator(); };
$('btn-op-back').onclick = () => { show(lastReport ? 'results' : 'start'); };
$('op-mode').onchange = () => { $('op-ports').parentElement.hidden = $('op-mode').value !== 'expert'; };

async function openOperator() {
  const st = await window.guardian.scheduleStatus();
  applyScheduleStatus(st);
  // reflect persisted config into controls
  if (st.intervalMinutes) $('op-interval').value = String(st.intervalMinutes);
  if (st.mode) $('op-mode').value = st.mode;
  $('op-ports').parentElement.hidden = $('op-mode').value !== 'expert';
  if (st.portSpec) $('op-ports').value = st.portSpec;
  await refreshHistory();
  show('operator');
}

function applyScheduleStatus(st) {
  const badge = $('op-status-val');
  if (st.scanning) { badge.textContent = 'Scanning…'; badge.className = 'op-badge scanning'; }
  else if (st.enabled) { badge.textContent = 'Running'; badge.className = 'op-badge on'; }
  else { badge.textContent = 'Stopped'; badge.className = 'op-badge off'; }
  $('op-start').hidden = st.enabled;
  $('op-stop').hidden = !st.enabled;
  const rows = [];
  if (st.enabled) rows.push(`<div>Every <b>${st.intervalMinutes}</b> min &middot; depth <b>${st.mode}</b></div>`);
  if (st.nextRunAt) rows.push(`<div>Next run: <b>${new Date(st.nextRunAt).toLocaleTimeString()}</b></div>`);
  if (st.lastRunAt) rows.push(`<div>Last run: <b>${new Date(st.lastRunAt).toLocaleString()}</b></div>`);
  $('op-status-rows').innerHTML = rows.join('');
}

$('op-start').onclick = async () => {
  const cfg = {
    intervalMinutes: parseInt($('op-interval').value, 10),
    mode: $('op-mode').value,
    portSpec: $('op-mode').value === 'expert' ? $('op-ports').value.trim() : null,
    runOnStart: $('op-runstart').checked
  };
  logOp(`Schedule started — every ${cfg.intervalMinutes} min (${cfg.mode})`, 'ok');
  const st = await window.guardian.scheduleStart(cfg);
  applyScheduleStatus(st);
};
$('op-stop').onclick = async () => {
  const st = await window.guardian.scheduleStop();
  applyScheduleStatus(st);
  logOp('Schedule stopped', 'warn');
};
$('op-runnow').onclick = async () => {
  logOp('Manual run requested…', 'ok');
  const st = await window.guardian.scheduleRunNow();
  applyScheduleStatus(st);
};
$('op-history-export').onclick = async () => { await window.guardian.historyExport(); };
$('op-history-clear').onclick = async () => {
  await window.guardian.historyClear();
  await refreshHistory();
  logOp('History cleared', 'warn');
};

function logOp(msg, kind) {
  const log = $('op-log');
  const line = el('div');
  const t = new Date().toLocaleTimeString();
  line.innerHTML = `<span class="t">${t}</span> <span class="ev-${kind || 'ok'}">${esc(msg)}</span>`;
  log.insertBefore(line, log.firstChild);
  while (log.childNodes.length > 200) log.removeChild(log.lastChild);
}

async function refreshHistory() {
  const list = await window.guardian.historyLoad();
  const rows = $('op-history-rows');
  rows.innerHTML = '';
  if (!list || !list.length) {
    $('op-history-empty').style.display = 'block';
    return;
  }
  $('op-history-empty').style.display = 'none';
  list.slice().reverse().forEach((e) => {
    const c = e.counts || {};
    const col = e.score >= 75 ? 'var(--green)' : e.score >= 45 ? 'var(--amber)' : 'var(--red)';
    const tr = el('tr');
    tr.innerHTML = `
      <td class="mono" style="font-size:11px">${new Date(e.at).toLocaleString()}</td>
      <td class="mono" style="font-size:10px;color:var(--grey-500)">${esc(e.trigger || '')}</td>
      <td class="sc" style="color:${col}">${e.score}</td>
      <td>${esc(e.grade || '')}</td>
      <td>${c.critical || 0}</td><td>${c.high || 0}</td><td>${c.medium || 0}</td>
      <td>${e.deviceCount || 0}</td>`;
    rows.appendChild(tr);
  });
}

// Live events from the scheduler (background re-scans).
window.guardian.onScheduleEvent((evt) => {
  if (evt.type === 'scan-start') logOp(`${evt.trigger === 'manual' ? 'Manual' : 'Scheduled'} scan started`, 'ok');
  if (evt.type === 'scan-done') {
    logOp(`Scan complete — score ${evt.report.score}/100 (${evt.report.grade}), ${evt.report.devices.length} devices`, 'ok');
    refreshHistory();
  }
  if (evt.type === 'scan-error') logOp(`Scan error: ${evt.error}`, 'err');
  if (evt.type === 'status') applyScheduleStatus(evt.status);
});

// =====================================================================
//  LIVE MONITOR
// =====================================================================
let monTimer = null;
let cpuHistory = [];
const GAUGE_CIRC = 2 * Math.PI * 50; // r=50

$('btn-monitor').onclick = openMonitor;
$('btn-mon-back').onclick = closeMonitor;
$('btn-mon-scan').onclick = monitorScan;

async function openMonitor() {
  show('monitor');
  cpuHistory = [];
  const report = await window.guardian.lastReport();
  renderStatTiles(report);
  renderPresence(report);
  if (monTimer) clearInterval(monTimer);
  await pollLive();
  monTimer = setInterval(pollLive, 2000);
}
function closeMonitor() {
  if (monTimer) { clearInterval(monTimer); monTimer = null; }
  show(lastReport ? 'results' : 'start');
}

async function pollLive() {
  const s = await window.guardian.liveStats();
  if (!s || s.error) return;
  // Gauges
  setGauge('gauge-cpu', s.cpu.overall);
  setGauge('gauge-mem', s.mem.pct);
  $('cpu-val').textContent = Math.round(s.cpu.overall);
  $('mem-val').textContent = Math.round(s.mem.pct);
  $('mon-host').textContent = s.hostname || '';
  // Cores
  const cores = $('cpu-cores');
  if (cores.childNodes.length !== s.cpu.per.length) {
    cores.innerHTML = '';
    s.cpu.per.forEach(() => { const c = el('div', 'core'); c.appendChild(el('i')); cores.appendChild(c); });
  }
  s.cpu.per.forEach((v, i) => {
    const bar = cores.childNodes[i] && cores.childNodes[i].firstChild;
    if (bar) bar.style.height = Math.max(4, v) + '%';
  });
  // Rows
  $('sys-rows').innerHTML = [
    `<div class="r"><span>Processor</span><b>${esc(shorten(s.cpuModel, 34))}</b></div>`,
    `<div class="r"><span>Cores</span><b>${s.cores}</b></div>`,
    `<div class="r"><span>Memory</span><b>${fmtGB(s.mem.used)} / ${fmtGB(s.mem.total)} GB</b></div>`,
    `<div class="r"><span>Uptime</span><b>${fmtUptime(s.uptime)}</b></div>`
  ].join('');
  // Sparkline
  cpuHistory.push(s.cpu.overall);
  while (cpuHistory.length > 60) cpuHistory.shift();
  drawSpark();
}

function setGauge(id, pct) {
  const c = $(id);
  if (!c) return;
  c.style.strokeDashoffset = GAUGE_CIRC * (1 - Math.max(0, Math.min(100, pct)) / 100);
  if (id === 'gauge-cpu') {
    c.style.stroke = pct > 85 ? 'var(--c-red)' : pct > 60 ? 'var(--c-amber)' : 'var(--c-cyan)';
  }
}

function drawSpark() {
  const n = cpuHistory.length;
  if (n < 2) { $('spark-line').setAttribute('points', ''); return; }
  const pts = cpuHistory.map((v, i) => {
    const x = (i / (n - 1)) * 300;
    const y = 46 - (v / 100) * 42;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  $('spark-line').setAttribute('points', pts);
}

function shorten(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function fmtGB(bytes) { return (bytes / 1073741824).toFixed(1); }
function fmtUptime(sec) {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function renderStatTiles(report) {
  const host = $('stat-tiles');
  if (!report || !report.ok) {
    host.innerHTML = `<div class="stat-tile st-slate"><div class="st-top"><div class="st-lbl">No scan yet</div></div><div class="st-num">—</div></div>`;
    return;
  }
  const devs = report.devices || [];
  const services = devs.reduce((a, d) => a + ((d.ports || []).length), 0);
  const risky = devs.reduce((a, d) => a + ((d.ports || []).filter((p) => p.risk === 'high' || p.risk === 'medium').length), 0);
  const alerts = (report.counts.critical || 0) + (report.counts.high || 0);
  const scoreColor = report.score >= 75 ? 'st-green' : report.score >= 45 ? 'st-amber' : 'st-red';
  const age = report.generatedAt ? timeAgo(report.generatedAt) : '—';
  const tiles = [
    { cls: 'st-cyan', ico: '▣', num: devs.length, lbl: 'Devices' },
    { cls: 'st-green', ico: '✔', num: devs.length, lbl: 'Online' },
    { cls: 'st-violet', ico: '⇆', num: services, lbl: 'Open services' },
    { cls: 'st-red', ico: '⚠', num: alerts, lbl: 'Alerts', click: 'results' },
    { cls: scoreColor, ico: '♥', num: report.score, lbl: 'Health', click: 'results' },
    { cls: 'st-slate', ico: '⏱', num: age, lbl: 'Last scan', small: true }
  ];
  host.innerHTML = '';
  tiles.forEach((t) => {
    const tile = el('div', `stat-tile ${t.cls}${t.click ? ' clickable' : ''}`);
    tile.innerHTML = `<div class="st-top"><div class="st-lbl">${t.lbl}</div><div class="st-ico">${t.ico}</div></div><div class="st-num" ${t.small ? 'style="font-size:22px"' : ''}>${esc(String(t.num))}</div>`;
    if (t.click && lastReport) tile.onclick = () => { closeMonitor(); selectSub('fixes'); show('results'); };
    host.appendChild(tile);
  });
}

function renderPresence(report) {
  const grid = $('presence-grid');
  const empty = $('presence-empty');
  grid.innerHTML = '';
  const devs = (report && report.devices) || [];
  $('mon-dev-count').textContent = devs.length ? `${devs.length} devices` : '';
  if (!devs.length) { empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  devs.forEach((d) => {
    const risky = (d.ports || []).some((p) => p.risk === 'high');
    const name = d.isGateway ? 'Router' : d.isThisDevice ? 'This computer' : (d.vendor || 'Unknown device');
    const card = el('div', 'pdev');
    const chips = [];
    if (d.isGateway) chips.push('<span class="pd-chip role">ROUTER</span>');
    if (d.isThisDevice) chips.push('<span class="pd-chip role">THIS PC</span>');
    (d.ports || []).slice(0, 3).forEach((p) => chips.push(`<span class="pd-chip ${p.risk === 'high' ? 'risk' : ''}">${p.port}</span>`));
    card.innerHTML = `
      <div class="pd-top"><span class="pd-dot ${risky ? 'risk' : 'up'}"></span><span class="pd-name">${esc(name)}</span></div>
      <div class="pd-ip">${esc(d.ip)}</div>
      <div class="pd-tags">${chips.join('')}</div>`;
    grid.appendChild(card);
  });
}

async function monitorScan() {
  const btn = $('btn-mon-scan'); const old = btn.textContent;
  btn.disabled = true; btn.textContent = 'Scanning…';
  const report = await window.guardian.startScan({ mode: mode });
  btn.disabled = false; btn.textContent = old;
  if (report && report.ok) {
    lastReport = report;
    renderStatTiles(report);
    renderPresence(report);
  }
}

function timeAgo(iso) {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

// expose for preview harness
window.openMonitor = openMonitor;
window.renderStatTiles = renderStatTiles;
window.renderPresence = renderPresence;

// ---- Init ----
window.renderResults = renderResults;
setMode('simple');
show('start');
loadNetInfo();
