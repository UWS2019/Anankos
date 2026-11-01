'use strict';
/*
 * scheduler.js
 * Operator-mode scheduled re-scans. While the app is open, this re-runs the
 * scan on a fixed interval, records each run to history, and notifies a
 * listener so the Operator Console can update live. The schedule is persisted,
 * so it can resume automatically when the app is reopened.
 *
 * It runs scans sequentially (never overlapping) and is safe to start/stop.
 */
const { runScan } = require('./index');
const { appendHistory, loadSchedule, saveSchedule } = require('./history');

class Scheduler {
  constructor(dataDir, emit) {
    this.dataDir = dataDir;
    this.emit = emit || (() => {});
    this.timer = null;
    this.running = false;      // a scan is currently in progress
    this.cfg = loadSchedule(dataDir);
    this.nextRunAt = null;
    this.lastRunAt = null;
  }

  status() {
    return {
      enabled: this.cfg.enabled,
      intervalMinutes: this.cfg.intervalMinutes,
      mode: this.cfg.mode,
      portSpec: this.cfg.portSpec,
      runOnStart: this.cfg.runOnStart,
      scanning: this.running,
      nextRunAt: this.nextRunAt,
      lastRunAt: this.lastRunAt
    };
  }

  // cfg: { intervalMinutes, mode, portSpec, runOnStart }
  start(cfg) {
    this.stop();
    this.cfg = Object.assign({}, this.cfg, cfg, { enabled: true });
    saveSchedule(this.dataDir, this.cfg);

    const ms = Math.max(1, Number(this.cfg.intervalMinutes) || 60) * 60 * 1000;
    const tick = async () => {
      await this._runOnce('scheduled');
      this.nextRunAt = new Date(Date.now() + ms).toISOString();
      this.emit({ type: 'status', status: this.status() });
    };
    this.timer = setInterval(tick, ms);
    this.nextRunAt = new Date(Date.now() + ms).toISOString();
    this.emit({ type: 'status', status: this.status() });

    if (this.cfg.runOnStart) {
      // Fire one immediately (not awaited here; it emits its own events).
      this._runOnce('scheduled').then(() => this.emit({ type: 'status', status: this.status() }));
    }
    return this.status();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.cfg.enabled = false;
    this.nextRunAt = null;
    saveSchedule(this.dataDir, this.cfg);
    this.emit({ type: 'status', status: this.status() });
    return this.status();
  }

  // Resume a persisted schedule on app launch.
  resumeIfEnabled() {
    if (this.cfg && this.cfg.enabled) {
      // Don't double-run immediately on resume.
      this.start(Object.assign({}, this.cfg, { runOnStart: false }));
      return true;
    }
    return false;
  }

  async _runOnce(trigger) {
    if (this.running) return null; // never overlap
    this.running = true;
    this.emit({ type: 'scan-start', trigger, at: new Date().toISOString() });
    try {
      const opts = { mode: this.cfg.mode || 'simple' };
      if (opts.mode === 'expert' && this.cfg.portSpec) opts.portSpec = this.cfg.portSpec;
      const report = await runScan(opts, (evt) => this.emit({ type: 'progress', evt }));
      this.lastRunAt = new Date().toISOString();
      if (report && report.ok) {
        const history = appendHistory(this.dataDir, report, trigger);
        this.emit({ type: 'scan-done', trigger, report, history });
      } else {
        this.emit({ type: 'scan-error', trigger, error: (report && report.error) || 'unknown' });
      }
      return report;
    } catch (e) {
      this.emit({ type: 'scan-error', trigger, error: String((e && e.message) || e) });
      return null;
    } finally {
      this.running = false;
    }
  }

  // Manual immediate run through the scheduler (also logged to history).
  runNow() {
    return this._runOnce('manual');
  }
}

module.exports = { Scheduler };
