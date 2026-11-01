'use strict';
/*
 * index.js - scan orchestrator
 * Runs the full pipeline and emits progress the UI can display:
 *   1. detect local network  2. discover devices  3. scan ports  4. read Wi-Fi
 *   5. analyze into findings + score.
 * Hard-scoped to the local subnet from netinfo; it has no concept of any
 * address outside your own network.
 */
const { getNetworkInfo } = require('./netinfo');
const { discoverHosts } = require('./discovery');
const { scanHost, parsePortSpec } = require('./ports');
const { getWifiInfo } = require('./wifi');
const { analyze } = require('./checks');

/*
 * opts:
 *   mode: 'simple' | 'expert'
 *   portSpec: string (expert) - e.g. "1-1024,3389"
 *   timeout, concurrency: numbers (expert tuning)
 *   target: single IP (expert) - scan just one device
 * onEvent(evt): { type, ... } progress callback.
 */
async function runScan(opts = {}, onEvent = () => {}) {
  const started = Date.now();
  onEvent({ type: 'phase', phase: 'netinfo', message: 'Detecting your network…' });

  const netInfo = await getNetworkInfo();
  if (!netInfo.ok) {
    return { ok: false, error: netInfo.error };
  }
  onEvent({ type: 'netinfo', netInfo });

  const timeout = opts.timeout || (opts.mode === 'expert' ? 1000 : 600);
  const concurrency = opts.concurrency || 64;

  // Phase 2: discovery
  onEvent({ type: 'phase', phase: 'discovery', message: `Looking for devices on ${netInfo.cidr}…` });
  let devices = await discoverHosts(
    netInfo,
    { timeout, concurrency, gateway: netInfo.gateway },
    (phase, done, total) => onEvent({ type: 'progress', phase: 'discovery', done, total })
  );

  // Expert single-target narrows the list.
  if (opts.target) {
    devices = devices.filter((d) => d.ip === opts.target);
    if (!devices.length) devices = [{ ip: opts.target, mac: null, vendor: null, isGateway: opts.target === netInfo.gateway, isThisDevice: opts.target === netInfo.localIp, ports: [], services: [] }];
  }

  onEvent({ type: 'devices', count: devices.length, devices });

  // Phase 3: port scan each device
  const ports = opts.mode === 'expert' ? parsePortSpec(opts.portSpec) : undefined;
  onEvent({ type: 'phase', phase: 'ports', message: `Checking services on ${devices.length} device(s)…` });
  let pi = 0;
  for (const d of devices) {
    onEvent({ type: 'progress', phase: 'ports', done: pi, total: devices.length, current: d.ip });
    d.ports = await scanHost(d.ip, { timeout: Math.max(timeout, 700), concurrency: 32, ports });
    pi++;
    onEvent({ type: 'device-done', device: d });
  }
  onEvent({ type: 'progress', phase: 'ports', done: devices.length, total: devices.length });

  // Phase 4: Wi-Fi
  onEvent({ type: 'phase', phase: 'wifi', message: 'Checking your Wi-Fi security…' });
  const wifi = await getWifiInfo();
  onEvent({ type: 'wifi', wifi });

  // Phase 5: analyze
  onEvent({ type: 'phase', phase: 'analyze', message: 'Reviewing results…' });
  const analysis = analyze({ netInfo, devices, wifi });

  const report = {
    ok: true,
    generatedAt: new Date().toISOString(),
    durationMs: Date.now() - started,
    netInfo,
    wifi,
    devices,
    ...analysis
  };
  onEvent({ type: 'done', report });
  return report;
}

module.exports = { runScan };
