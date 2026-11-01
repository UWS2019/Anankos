'use strict';
/*
 * history.js
 * Persists a lightweight log of past scans so an operator can see trend over
 * time and compare re-scans. Stores compact SUMMARY entries (not full device
 * dumps) in a JSON file, plus the most recent full report separately.
 *
 * Decoupled from Electron: the base directory is injected, so it also works in
 * tests / the CLI.
 */
const fs = require('fs');
const path = require('path');

const MAX_ENTRIES = 500;

function ensureDir(dir) {
  try { fs.mkdirSync(dir, { recursive: true }); } catch (_) { /* ignore */ }
}

function summaryFile(dir) { return path.join(dir, 'scan-history.json'); }
function lastReportFile(dir) { return path.join(dir, 'last-report.json'); }
function scheduleFile(dir) { return path.join(dir, 'schedule.json'); }

function summarize(report, trigger) {
  return {
    at: report.generatedAt || new Date().toISOString(),
    trigger: trigger || 'manual', // 'manual' | 'scheduled'
    score: report.score,
    grade: report.grade,
    counts: report.counts,
    deviceCount: (report.devices || []).length,
    cidr: report.netInfo && report.netInfo.cidr,
    durationMs: report.durationMs
  };
}

function loadHistory(dir) {
  try {
    const raw = fs.readFileSync(summaryFile(dir), 'utf8');
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch (_) {
    return [];
  }
}

function appendHistory(dir, report, trigger) {
  ensureDir(dir);
  const list = loadHistory(dir);
  list.push(summarize(report, trigger));
  while (list.length > MAX_ENTRIES) list.shift();
  try {
    fs.writeFileSync(summaryFile(dir), JSON.stringify(list, null, 2));
    fs.writeFileSync(lastReportFile(dir), JSON.stringify(report, null, 2));
  } catch (_) { /* ignore */ }
  return list;
}

function clearHistory(dir) {
  try { fs.unlinkSync(summaryFile(dir)); } catch (_) { /* ignore */ }
  return [];
}

function loadSchedule(dir) {
  try {
    return JSON.parse(fs.readFileSync(scheduleFile(dir), 'utf8'));
  } catch (_) {
    return { enabled: false, intervalMinutes: 60, mode: 'simple', portSpec: null, runOnStart: true };
  }
}

function saveSchedule(dir, cfg) {
  ensureDir(dir);
  try { fs.writeFileSync(scheduleFile(dir), JSON.stringify(cfg, null, 2)); } catch (_) { /* ignore */ }
  return cfg;
}

// Export history to CSV text (for spreadsheets / operator records).
function historyToCsv(list) {
  const head = 'timestamp,trigger,score,grade,critical,high,medium,low,info,devices,network,duration_ms';
  const rows = (list || []).map((e) => {
    const c = e.counts || {};
    return [
      e.at, e.trigger, e.score, e.grade,
      c.critical || 0, c.high || 0, c.medium || 0, c.low || 0, c.info || 0,
      e.deviceCount || 0, e.cidr || '', e.durationMs || 0
    ].join(',');
  });
  return [head, ...rows].join('\n');
}

module.exports = {
  loadHistory, appendHistory, clearHistory,
  loadSchedule, saveSchedule, historyToCsv, summarize
};
