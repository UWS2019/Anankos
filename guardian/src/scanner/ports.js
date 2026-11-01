'use strict';
/*
 * ports.js
 * A plain TCP connect scan: we attempt a normal TCP handshake to a curated set
 * of common ports on each discovered device. A completed handshake means the
 * port is open. This is the same thing any app does when it connects to a
 * service - no raw packets, no spoofing, no exploitation.
 */
const net = require('net');
const { pool } = require('./discovery');

// Curated, home-relevant port list with friendly service names and a risk hint.
// risk: 'info' | 'low' | 'medium' | 'high'
const PORT_CATALOG = {
  21: { name: 'FTP', risk: 'high', note: 'File transfer in clear text' },
  22: { name: 'SSH', risk: 'low', note: 'Secure remote login' },
  23: { name: 'Telnet', risk: 'high', note: 'Remote login in clear text' },
  25: { name: 'SMTP', risk: 'low', note: 'Outgoing mail' },
  53: { name: 'DNS', risk: 'info', note: 'Name lookups (normal on routers)' },
  80: { name: 'HTTP', risk: 'info', note: 'Web interface (unencrypted)' },
  110: { name: 'POP3', risk: 'low', note: 'Mail retrieval' },
  135: { name: 'MS-RPC', risk: 'medium', note: 'Windows remote procedure call' },
  139: { name: 'NetBIOS', risk: 'medium', note: 'Legacy Windows file sharing' },
  143: { name: 'IMAP', risk: 'low', note: 'Mail retrieval' },
  443: { name: 'HTTPS', risk: 'info', note: 'Secure web interface' },
  445: { name: 'SMB', risk: 'medium', note: 'Windows file sharing' },
  515: { name: 'LPD', risk: 'low', note: 'Printer' },
  548: { name: 'AFP', risk: 'low', note: 'Apple file sharing' },
  554: { name: 'RTSP', risk: 'medium', note: 'Camera video stream' },
  631: { name: 'IPP', risk: 'low', note: 'Printer' },
  1080: { name: 'SOCKS', risk: 'medium', note: 'Proxy' },
  1883: { name: 'MQTT', risk: 'medium', note: 'IoT messaging (often unauthenticated)' },
  1900: { name: 'UPnP/SSDP', risk: 'medium', note: 'Auto device discovery' },
  2323: { name: 'Telnet-alt', risk: 'high', note: 'Remote login in clear text' },
  3000: { name: 'Dev/Web', risk: 'low', note: 'Development server' },
  3306: { name: 'MySQL', risk: 'high', note: 'Database exposed on network' },
  3389: { name: 'RDP', risk: 'high', note: 'Windows Remote Desktop' },
  5000: { name: 'UPnP/Web', risk: 'medium', note: 'Device web service' },
  5432: { name: 'PostgreSQL', risk: 'high', note: 'Database exposed on network' },
  5555: { name: 'ADB', risk: 'high', note: 'Android Debug Bridge (remote control)' },
  5900: { name: 'VNC', risk: 'high', note: 'Remote screen control' },
  5901: { name: 'VNC', risk: 'high', note: 'Remote screen control' },
  6379: { name: 'Redis', risk: 'high', note: 'Database (often no password)' },
  7547: { name: 'TR-069', risk: 'medium', note: 'ISP remote router management' },
  8080: { name: 'HTTP-alt', risk: 'info', note: 'Web interface' },
  8081: { name: 'HTTP-alt', risk: 'info', note: 'Web interface' },
  8443: { name: 'HTTPS-alt', risk: 'info', note: 'Secure web interface' },
  8888: { name: 'HTTP-alt', risk: 'low', note: 'Web interface' },
  9000: { name: 'HTTP-alt', risk: 'low', note: 'Web service' },
  9100: { name: 'JetDirect', risk: 'low', note: 'Network printer' },
  27017: { name: 'MongoDB', risk: 'high', note: 'Database (often no password)' },
  32400: { name: 'Plex', risk: 'low', note: 'Media server' },
  49152: { name: 'UPnP', risk: 'medium', note: 'Dynamic UPnP service' },
  62078: { name: 'iOS-sync', risk: 'info', note: 'Apple device sync' }
};

const DEFAULT_PORTS = Object.keys(PORT_CATALOG).map(Number);

function scanPort(host, port, timeout) {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let done = false;
    const finish = (open) => {
      if (done) return;
      done = true;
      sock.destroy();
      resolve(open);
    };
    sock.setTimeout(timeout);
    sock.once('connect', () => finish(true));
    sock.once('timeout', () => finish(false));
    sock.once('error', () => finish(false));
    try {
      sock.connect(port, host);
    } catch (_) {
      finish(false);
    }
  });
}

/*
 * Scan one host. ports = array of port numbers (defaults to the catalog).
 * Returns array of { port, name, risk, note }.
 */
async function scanHost(host, opts) {
  const timeout = (opts && opts.timeout) || 900;
  const concurrency = (opts && opts.concurrency) || 32;
  const ports = (opts && opts.ports) || DEFAULT_PORTS;
  const open = [];
  await pool(ports, concurrency, async (port) => {
    const isOpen = await scanPort(host, port, timeout);
    if (isOpen) {
      const meta = PORT_CATALOG[port] || { name: `tcp/${port}`, risk: 'low', note: 'Open service' };
      open.push({ port, name: meta.name, risk: meta.risk, note: meta.note });
    }
  });
  open.sort((a, b) => a.port - b.port);
  return open;
}

// Parse an expert-mode port spec like "1-1024,3389,8080".
function parsePortSpec(spec) {
  if (!spec) return DEFAULT_PORTS;
  const out = new Set();
  for (const part of String(spec).split(',')) {
    const p = part.trim();
    if (!p) continue;
    const range = p.match(/^(\d+)\s*-\s*(\d+)$/);
    if (range) {
      let lo = Math.max(1, parseInt(range[1], 10));
      let hi = Math.min(65535, parseInt(range[2], 10));
      for (let i = lo; i <= hi; i++) out.add(i);
    } else if (/^\d+$/.test(p)) {
      const n = parseInt(p, 10);
      if (n >= 1 && n <= 65535) out.add(n);
    }
  }
  return out.size ? Array.from(out).sort((a, b) => a - b) : DEFAULT_PORTS;
}

module.exports = { scanHost, parsePortSpec, PORT_CATALOG, DEFAULT_PORTS };
