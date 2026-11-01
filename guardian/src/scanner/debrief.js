'use strict';
/*
 * debrief.js
 * Builds a ready-to-paste prompt that a non-technical person can drop into ANY
 * large language model (ChatGPT, Claude, Gemini, a local model, etc.) to get a
 * plain-language explanation of their scan. The prompt carries only the scan
 * data and strict instructions; it tells the model to use nothing but the data
 * provided and never to invent findings.
 *
 * Privacy: the prompt contains local IP/MAC addresses and device names from the
 * user's own network. The UI warns before copying; nothing is sent anywhere by
 * the app itself - the user chooses where to paste it.
 */

const SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

function buildDebriefPrompt(report) {
  if (!report || !report.ok) return '';
  const r = report;
  const lines = [];

  lines.push('You are a friendly home-network security advisor. A non-technical person ran a security scan of their OWN home network and pasted the results below. Explain what it means in plain, everyday language.');
  lines.push('');
  lines.push('Please do the following:');
  lines.push('1. Start with one reassuring sentence summarizing how safe or unsafe the network looks overall.');
  lines.push('2. List the problems in priority order. For each: say what it is in simple words, why it matters to them personally, and the single most important action to take. Keep each to 2-3 sentences.');
  lines.push('3. Clearly separate "Do this now" (urgent) from "Worth doing" (optional tidy-ups).');
  lines.push('4. If anything in the data could suggest an unknown or uninvited device, or a possible compromise, flag it gently but clearly.');
  lines.push('5. End with a short, encouraging next-step checklist they can follow.');
  lines.push('');
  lines.push('Rules: Use ONLY the information in the data below. Do not invent devices, problems, or technical details that are not present. Avoid jargon; when you must use a technical word, explain it in brackets. Do not ask me to run more scans.');
  lines.push('');
  lines.push('=== SCAN DATA ===');
  lines.push(`Overall health score: ${r.score}/100 (${r.grade}).`);
  lines.push(`Network: ${r.netInfo && r.netInfo.cidr}. Router address: ${(r.netInfo && r.netInfo.gateway) || 'unknown'}.`);
  if (r.wifi && r.wifi.security) {
    lines.push(`Wi-Fi network "${r.wifi.ssid || 'unknown'}" security: ${r.wifi.security.label}.`);
  } else {
    lines.push('Wi-Fi security: not detected (this computer may be on a wired connection).');
  }
  lines.push(`Devices found on the network: ${r.devices ? r.devices.length : 0}.`);
  lines.push('');

  lines.push('Devices and their open network services:');
  (r.devices || []).forEach((d) => {
    const role = d.isGateway ? 'the router' : d.isThisDevice ? 'this computer' : (d.vendor || 'unidentified device');
    const ports = (d.ports || []).length
      ? d.ports.map((p) => `${p.port} ${p.name} (${p.risk} risk - ${p.note})`).join('; ')
      : 'no common services open';
    lines.push(`- ${d.ip} = ${role}${d.mac ? ` [${d.mac}]` : ''}: ${ports}`);
  });
  lines.push('');

  const findings = (r.findings || []).slice().sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]);
  lines.push(`Problems the scan flagged (${findings.length}), most serious first:`);
  if (!findings.length) {
    lines.push('- None. The scan did not flag any problems.');
  } else {
    findings.forEach((f, i) => {
      lines.push(`${i + 1}. [${f.severity.toUpperCase()}] ${f.title}`);
      lines.push(`   What it is: ${f.plain}`);
      if (f.why) lines.push(`   Why it matters: ${f.why}`);
      if (f.steps && f.steps.length) lines.push(`   Suggested fix steps: ${f.steps.join(' | ')}`);
    });
  }
  lines.push('');
  lines.push('=== END OF SCAN DATA ===');
  lines.push('');
  lines.push('Now write your plain-language briefing.');

  return lines.join('\n');
}

module.exports = { buildDebriefPrompt };
