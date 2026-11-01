'use strict';
/*
 * cli.js - headless runner for the scan engine (no Electron needed).
 * Usage:
 *   node src/scanner/cli.js            # simple scan, pretty summary
 *   node src/scanner/cli.js --json     # full JSON report
 *   node src/scanner/cli.js --expert --ports 1-1024
 *   node src/scanner/cli.js --target 192.168.1.1 --expert --ports 1-1024
 * This lets you verify the engine works before building/packaging the app.
 */
const { runScan } = require('./index');

function arg(flag, def) {
  const i = process.argv.indexOf(flag);
  if (i === -1) return def;
  const v = process.argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
}

(async () => {
  const opts = {
    mode: process.argv.includes('--expert') ? 'expert' : 'simple',
    portSpec: arg('--ports', undefined),
    target: arg('--target', undefined),
    timeout: Number(arg('--timeout', 0)) || undefined
  };
  const wantJson = process.argv.includes('--json');

  const report = await runScan(opts, (evt) => {
    if (wantJson) return;
    if (evt.type === 'phase') process.stderr.write(`\n[*] ${evt.message}\n`);
    if (evt.type === 'progress' && evt.phase === 'discovery') {
      process.stderr.write(`\r    sweeping ${evt.done}/${evt.total}   `);
    }
    if (evt.type === 'progress' && evt.phase === 'ports' && evt.current) {
      process.stderr.write(`\r    scanning ${evt.current} (${evt.done}/${evt.total})      `);
    }
  });

  if (!report.ok) {
    console.error('Scan failed:', report.error);
    process.exit(1);
  }
  if (wantJson) {
    console.log(JSON.stringify(report, null, 2));
    return;
  }

  console.log('\n=============================================');
  console.log(` Network Health: ${report.score}/100  (${report.grade})`);
  console.log(` Network: ${report.netInfo.cidr}   Router: ${report.netInfo.gateway || 'unknown'}`);
  console.log(` Devices found: ${report.devices.length}   Scan time: ${(report.durationMs / 1000).toFixed(1)}s`);
  console.log('=============================================\n');

  console.log('Devices:');
  for (const d of report.devices) {
    const tag = d.isGateway ? '[router]' : d.isThisDevice ? '[this pc]' : '';
    const openp = d.ports.map((p) => `${p.port}/${p.name}`).join(', ') || 'no common ports open';
    console.log(`  ${d.ip.padEnd(15)} ${d.vendor || 'unknown'} ${tag}`);
    console.log(`      ${openp}`);
  }

  console.log(`\nFindings (${report.findings.length}):`);
  for (const f of report.findings) {
    console.log(`\n  [${f.severity.toUpperCase()}] ${f.title}`);
    console.log(`     ${f.plain}`);
    f.steps.forEach((s, i) => console.log(`       ${i + 1}. ${s}`));
  }
  console.log('');
})();
