'use strict';
/*
 * report.js
 * Turns a scan report object into a single self-contained, branded HTML
 * document (no external assets) suitable for saving, printing, or exporting to
 * PDF. Pure function - no I/O here. The same HTML is used for the "Export HTML"
 * and "Export PDF" features.
 */
const { buildDebriefPrompt } = require('./debrief');

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const SEV_COLOR = {
  critical: '#d9534f', high: '#d97c41', medium: '#d9a441', low: '#8fbf5a', info: '#8a8d86'
};

function buildReportHtml(report) {
  const r = report;
  const when = new Date(r.generatedAt || Date.now()).toLocaleString();
  const scoreColor = r.score >= 75 ? '#8fbf5a' : r.score >= 45 ? '#d9a441' : '#d9534f';

  const badges = ['critical', 'high', 'medium', 'low', 'info']
    .filter((s) => r.counts && r.counts[s] > 0)
    .map((s) => `<span class="badge" style="color:${SEV_COLOR[s]};border-color:${SEV_COLOR[s]}66">${r.counts[s]} ${s}</span>`)
    .join('');

  const findings = (r.findings || []).map((f) => `
    <div class="finding" style="border-left-color:${SEV_COLOR[f.severity]}">
      <div class="f-head">
        <span class="sev" style="background:${SEV_COLOR[f.severity]}22;color:${SEV_COLOR[f.severity]}">${esc(f.severity)}</span>
        <span class="f-title">${esc(f.title)}</span>
      </div>
      <p class="f-plain">${esc(f.plain)}</p>
      ${f.why ? `<p class="f-why"><b>Why it matters:</b> ${esc(f.why)}</p>` : ''}
      <div class="f-steps-label">How to fix it</div>
      <ol class="f-steps">${(f.steps || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
    </div>`).join('');

  const devices = (r.devices || []).map((d) => {
    const name = d.isGateway ? 'Your router' : d.isThisDevice ? 'This computer' : (d.vendor || 'Unidentified device');
    const ports = (d.ports || []).length
      ? d.ports.map((p) => `<span class="pt" style="border-color:${SEV_COLOR[p.risk]}55">${p.port} ${esc(p.name)}</span>`).join(' ')
      : '<span class="pt dim">no common ports open</span>';
    return `<tr>
      <td class="mono">${esc(d.ip)}</td>
      <td>${esc(name)}${d.isGateway ? ' <span class="tag">ROUTER</span>' : ''}${d.isThisDevice ? ' <span class="tag">THIS DEVICE</span>' : ''}<div class="mono dim">${esc(d.mac || '')}</div></td>
      <td>${ports}</td>
    </tr>`;
  }).join('');

  const debrief = esc(buildDebriefPrompt(r));

  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Anankos Guardian Report - ${esc(when)}</title>
<style>
  :root{--green:#8fbf5a;--olive:#4e5540;--tan:#c9b48a;--ink:#0b0c0a;--grey:#6b6e68}
  *{box-sizing:border-box}
  body{font-family:"Segoe UI",system-ui,-apple-system,sans-serif;color:#1a1c18;background:#efeee8;margin:0;padding:40px;line-height:1.55}
  .wrap{max-width:860px;margin:0 auto;background:#fff;border:1px solid #d7d5cc;border-radius:12px;overflow:hidden}
  .top{background:linear-gradient(140deg,#1b2015,#050605);color:#f5f4ef;padding:28px 34px;display:flex;justify-content:space-between;align-items:center}
  .brand{font-size:20px;font-weight:600;letter-spacing:.04em}
  .brand span{color:var(--green)}
  .sub{font-family:ui-monospace,Consolas,monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#b3b5ae;margin-top:4px}
  .meta{text-align:right;font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#b3b5ae;line-height:1.7}
  .body{padding:30px 34px}
  .scorebar{display:flex;align-items:center;gap:22px;margin-bottom:8px}
  .score{font-size:46px;font-weight:300;color:${scoreColor}}
  .score small{font-size:16px;color:var(--grey)}
  .grade{font-family:ui-monospace,monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--olive)}
  .summary{font-size:19px;font-weight:300;margin:4px 0 14px}
  .badges{margin-bottom:26px}
  .badge{display:inline-block;font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:4px 9px;border:1px solid;border-radius:5px;margin-right:6px}
  h2{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--olive);border-bottom:1px solid #e2e0d7;padding-bottom:8px;margin:30px 0 16px;font-family:ui-monospace,monospace}
  .finding{border:1px solid #e2e0d7;border-left-width:4px;border-radius:8px;padding:14px 18px;margin-bottom:12px;background:#fafaf7}
  .f-head{display:flex;align-items:center;gap:12px;margin-bottom:6px}
  .sev{font-family:ui-monospace,monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;padding:3px 8px;border-radius:4px}
  .f-title{font-weight:600;font-size:15px}
  .f-plain{color:#33352f;margin:2px 0 8px}
  .f-why{background:#f0f0e8;border-radius:6px;padding:9px 12px;font-size:13.5px;color:#3a3c34;margin:0 0 10px}
  .f-why b{color:#6a5c3c}
  .f-steps-label{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#5a7a32;margin-bottom:4px}
  .f-steps{margin:0;padding-left:20px}
  .f-steps li{margin:4px 0}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #e9e7de;vertical-align:top}
  th{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--grey)}
  .mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
  .dim{color:#9a9c93}
  .tag{font-family:ui-monospace,monospace;font-size:9px;letter-spacing:.08em;color:var(--tan);background:#2e312c;padding:2px 6px;border-radius:3px}
  .pt{display:inline-block;font-family:ui-monospace,monospace;font-size:10.5px;padding:2px 7px;border:1px solid #ccc;border-radius:4px;margin:2px 2px 2px 0}
  .debrief-note{font-size:13px;color:#5a5c52;margin-bottom:10px}
  pre{white-space:pre-wrap;word-wrap:break-word;background:#0b0c0a;color:#d7e4c3;font-family:ui-monospace,Consolas,monospace;font-size:11.5px;line-height:1.6;padding:18px;border-radius:8px;border:1px solid #2e312c}
  .foot{padding:18px 34px;border-top:1px solid #e2e0d7;font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#9a9c93;text-align:center}
  @media print{body{padding:0;background:#fff}.wrap{border:0}}
</style></head><body>
<div class="wrap">
  <div class="top">
    <div><div class="brand">ANANKOS <span>GUARDIAN</span></div><div class="sub">Home Network Security Report</div></div>
    <div class="meta">Generated<br>${esc(when)}<br>${esc((r.netInfo && r.netInfo.cidr) || '')}</div>
  </div>
  <div class="body">
    <div class="scorebar">
      <div class="score">${r.score}<small>/100</small></div>
      <div><div class="grade">Network Health &mdash; ${esc(r.grade)}</div><div class="summary">${esc(summaryLine(r))}</div></div>
    </div>
    <div class="badges">${badges}<span class="badge" style="color:#6b6e68;border-color:#ccc">${(r.devices || []).length} devices</span></div>

    <h2>What to fix</h2>
    ${findings || '<p>No problems were flagged. Your network looks healthy.</p>'}

    <h2>Devices found</h2>
    <table><thead><tr><th>Address</th><th>Device</th><th>Open services</th></tr></thead><tbody>${devices}</tbody></table>

    <h2>AI debrief prompt</h2>
    <p class="debrief-note">Copy everything in the box below and paste it into any AI assistant (ChatGPT, Claude, Gemini, or a local model) to get a plain-language explanation of this report. Note: it contains your local device addresses &mdash; only paste it somewhere you trust.</p>
    <pre>${debrief}</pre>
  </div>
  <div class="foot">Anankos Guardian &middot; Scan of ${esc((r.netInfo && r.netInfo.cidr) || 'local network')} &middot; For networks you own or are authorized to assess</div>
</div>
</body></html>`;
}

function summaryLine(r) {
  const c = r.counts || {};
  const urgent = (c.critical || 0) + (c.high || 0);
  if (urgent === 0 && (c.medium || 0) === 0) return 'No serious problems found. A few optional tips below.';
  if (urgent === 0) return `No urgent problems. ${c.medium} thing(s) worth tidying up.`;
  return `${urgent} issue(s) need your attention. See the fixes below.`;
}

module.exports = { buildReportHtml };
