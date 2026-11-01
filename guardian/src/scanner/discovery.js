'use strict';
/*
 * discovery.js
 * Finds which devices are alive on your local subnet. Uses two privilege-free
 * techniques and merges them:
 *   1. A TCP "ping" sweep  - try a short connect to a handful of common ports;
 *      a successful connect OR a connection-refused both prove a host is alive.
 *   2. The OS ARP table     - `arp -a` lists devices the machine has recently
 *      talked to, and crucially gives us their MAC addresses (hence vendor).
 * No raw sockets, no ICMP, no elevated privileges required.
 */
const net = require('net');
const { run } = require('./netinfo');
const { vendorForMac } = require('./oui');

// Ports used only to detect liveness. Small and fast.
const PING_PORTS = [80, 443, 22, 445, 139, 53, 8080, 7, 548, 9100, 62078, 3389];

function tcpProbe(host, port, timeout) {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let done = false;
    const finish = (alive) => {
      if (done) return;
      done = true;
      sock.destroy();
      resolve(alive);
    };
    sock.setTimeout(timeout);
    sock.once('connect', () => finish(true));
    sock.once('timeout', () => finish(false));
    sock.once('error', (err) => {
      // ECONNREFUSED means something answered -> host is alive.
      finish(err && err.code === 'ECONNREFUSED');
    });
    try {
      sock.connect(port, host);
    } catch (_) {
      finish(false);
    }
  });
}

async function hostIsAlive(host, timeout) {
  // Resolve as soon as any probe says "alive".
  return new Promise((resolve) => {
    let pending = PING_PORTS.length;
    let settled = false;
    for (const port of PING_PORTS) {
      tcpProbe(host, port, timeout).then((alive) => {
        if (settled) return;
        if (alive) {
          settled = true;
          resolve(true);
        } else if (--pending === 0) {
          resolve(false);
        }
      });
    }
  });
}

// Run an async worker over items with bounded concurrency, reporting progress.
async function pool(items, concurrency, worker, onProgress) {
  const results = [];
  let index = 0;
  let completed = 0;
  async function runner() {
    while (index < items.length) {
      const i = index++;
      results[i] = await worker(items[i], i);
      completed++;
      if (onProgress) onProgress(completed, items.length);
    }
  }
  const runners = [];
  for (let c = 0; c < Math.min(concurrency, items.length); c++) runners.push(runner());
  await Promise.all(runners);
  return results;
}

// Read the OS ARP cache -> map of ip -> mac.
async function readArpTable() {
  const map = {};
  const platform = process.platform;
  let out = '';
  try {
    if (platform === 'win32') {
      out = await run('arp', ['-a']);
    } else {
      out = await run('arp', ['-a']);
      if (!out) out = await run('ip', ['neigh']);
    }
  } catch (_) {
    out = '';
  }
  const macRe = /([0-9a-f]{2}[:-]){5}[0-9a-f]{2}/i;
  const ipRe = /(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/;
  for (const line of out.split(/\r?\n/)) {
    const ipM = line.match(ipRe);
    const macM = line.match(macRe);
    if (ipM && macM) {
      const mac = macM[0].toLowerCase().replace(/-/g, ':');
      if (mac !== '00:00:00:00:00:00' && !/ff:ff:ff:ff:ff:ff/.test(mac)) {
        map[ipM[1]] = mac;
      }
    }
  }
  return map;
}

/*
 * Discover devices on the subnet described by `netInfo`.
 * opts: { timeout, concurrency, gateway }
 * onProgress(phase, done, total)
 */
async function discoverHosts(netInfo, opts, onProgress) {
  const timeout = opts.timeout || 600;
  const concurrency = opts.concurrency || 64;

  const arpBefore = await readArpTable();

  const aliveFlags = await pool(
    netInfo.hosts,
    concurrency,
    (ip) => hostIsAlive(ip, timeout),
    (done, total) => onProgress && onProgress('sweep', done, total)
  );

  // ARP table is richer after the sweep generated traffic.
  const arpAfter = await readArpTable();
  const arp = Object.assign({}, arpBefore, arpAfter);

  const aliveSet = new Set();
  netInfo.hosts.forEach((ip, i) => {
    if (aliveFlags[i]) aliveSet.add(ip);
  });
  // Any IP with a known MAC in our subnet is also a real device.
  Object.keys(arp).forEach((ip) => {
    if (netInfo.hosts.includes(ip)) aliveSet.add(ip);
  });
  // Always include the local machine and the gateway.
  aliveSet.add(netInfo.localIp);
  if (netInfo.gateway) aliveSet.add(netInfo.gateway);

  const devices = Array.from(aliveSet)
    .sort((a, b) => a.split('.').map(Number).reduce((x, o, i) => x + o * [1e9, 1e6, 1e3, 1][i], 0)
      - b.split('.').map(Number).reduce((x, o, i) => x + o * [1e9, 1e6, 1e3, 1][i], 0))
    .map((ip) => {
      const mac = ip === netInfo.localIp ? netInfo.mac : arp[ip] || null;
      return {
        ip,
        mac: mac || null,
        vendor: vendorForMac(mac),
        isGateway: ip === netInfo.gateway,
        isThisDevice: ip === netInfo.localIp,
        ports: [],
        services: []
      };
    });

  return devices;
}

module.exports = { discoverHosts, pool };
