'use strict';
/*
 * sysmon.js
 * Live, local-only system metrics for the Live Monitor dashboard. Reads CPU,
 * memory, uptime and load from Node's os module - no network, no privileges.
 * CPU percentage is derived from the delta between two samples of the kernel's
 * per-core time counters.
 */
const os = require('os');

let lastCpu = null;

function snapshot() {
  const cpus = os.cpus();
  let idle = 0;
  let total = 0;
  const per = cpus.map((c) => {
    const t = Object.values(c.times).reduce((a, b) => a + b, 0);
    idle += c.times.idle;
    total += t;
    return { idle: c.times.idle, total: t };
  });
  return { idle, total, per };
}

function cpuPercent() {
  const cur = snapshot();
  if (!lastCpu || lastCpu.per.length !== cur.per.length) {
    lastCpu = cur;
    return { overall: 0, per: cur.per.map(() => 0) };
  }
  const idleD = cur.idle - lastCpu.idle;
  const totalD = cur.total - lastCpu.total;
  const overall = totalD > 0 ? Math.max(0, Math.min(100, (1 - idleD / totalD) * 100)) : 0;
  const per = cur.per.map((c, i) => {
    const id = c.idle - lastCpu.per[i].idle;
    const td = c.total - lastCpu.per[i].total;
    return td > 0 ? Math.max(0, Math.min(100, (1 - id / td) * 100)) : 0;
  });
  lastCpu = cur;
  return { overall, per };
}

function getLiveStats() {
  const total = os.totalmem();
  const free = os.freemem();
  const used = total - free;
  const cpus = os.cpus();
  return {
    ts: Date.now(),
    hostname: os.hostname(),
    platform: process.platform,
    cores: cpus.length,
    cpuModel: (cpus[0] || {}).model || 'CPU',
    cpu: cpuPercent(),
    mem: { total, free, used, pct: total > 0 ? (used / total) * 100 : 0 },
    uptime: os.uptime(),
    load: os.loadavg()
  };
}

module.exports = { getLiveStats };
