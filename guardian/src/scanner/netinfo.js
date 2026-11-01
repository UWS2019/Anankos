'use strict';
/*
 * netinfo.js
 * Detects the machine's own local network: the IPv4 address, subnet mask,
 * default gateway (the home router) and the list of host addresses that belong
 * to THIS subnet. Everything the scanner does is derived from here, which is
 * what keeps Guardian scoped to your own home network and nothing beyond it.
 */
const os = require('os');
const { execFile } = require('child_process');

function run(cmd, args, timeout = 4000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, windowsHide: true }, (err, stdout) => {
      resolve(err ? '' : String(stdout || ''));
    });
  });
}

// netmask (e.g. "255.255.255.0") -> prefix length (e.g. 24)
function maskToPrefix(mask) {
  return mask
    .split('.')
    .map(Number)
    .reduce((bits, octet) => bits + ((octet >>> 0).toString(2).match(/1/g) || []).length, 0);
}

function ipToInt(ip) {
  return ip.split('.').reduce((acc, o) => ((acc << 8) + (Number(o) & 255)) >>> 0, 0) >>> 0;
}

function intToIp(int) {
  return [24, 16, 8, 0].map((s) => (int >>> s) & 255).join('.');
}

// Pick the first non-internal IPv4 interface (the one on your home LAN).
function getPrimaryInterface() {
  const ifaces = os.networkInterfaces();
  const candidates = [];
  for (const [name, addrs] of Object.entries(ifaces)) {
    for (const a of addrs || []) {
      if (a.family === 'IPv4' && !a.internal && a.address && a.netmask) {
        candidates.push({ name, address: a.address, netmask: a.netmask, mac: a.mac });
      }
    }
  }
  // Prefer common private LAN ranges.
  const isPrivate = (ip) =>
    ip.startsWith('192.168.') ||
    ip.startsWith('10.') ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(ip);
  candidates.sort((a, b) => Number(isPrivate(b.address)) - Number(isPrivate(a.address)));
  return candidates[0] || null;
}

async function detectGateway(fallbackIp) {
  const platform = process.platform;
  let out = '';
  try {
    if (platform === 'win32') {
      out = await run('cmd', ['/c', 'ipconfig']);
      const m = out.match(/Default Gateway[^\n]*:\s*([0-9.]+)/i);
      if (m && m[1]) return m[1];
    } else if (platform === 'darwin') {
      out = await run('route', ['-n', 'get', 'default']);
      const m = out.match(/gateway:\s*([0-9.]+)/i);
      if (m && m[1]) return m[1];
    } else {
      out = await run('ip', ['route']);
      let m = out.match(/default via ([0-9.]+)/i);
      if (m && m[1]) return m[1];
      out = await run('route', ['-n']);
      m = out.match(/^0\.0\.0\.0\s+([0-9.]+)/m);
      if (m && m[1]) return m[1];
    }
  } catch (_) {
    /* ignore */
  }
  // Fallback: on nearly all home networks the router is x.x.x.1
  if (fallbackIp) {
    const base = ipToInt(fallbackIp) & ~0;
    return intToIp((base & 0xffffff00) + 1);
  }
  return null;
}

/*
 * Returns the full picture of the local network, plus the concrete list of
 * host IPs to probe. Subnets larger than /24 are clamped to the /24 around the
 * local address so a scan stays fast and obviously scoped to the home LAN.
 */
async function getNetworkInfo() {
  const iface = getPrimaryInterface();
  if (!iface) {
    return { ok: false, error: 'No active network connection was found. Connect to your home Wi-Fi or Ethernet and try again.' };
  }
  let prefix = maskToPrefix(iface.netmask);
  const localInt = ipToInt(iface.address);

  // Clamp very large subnets to a /24 around the local address.
  let effectivePrefix = prefix < 24 ? 24 : prefix;
  const maskInt = effectivePrefix === 0 ? 0 : (0xffffffff << (32 - effectivePrefix)) >>> 0;
  const network = (localInt & maskInt) >>> 0;
  const broadcast = (network | (~maskInt >>> 0)) >>> 0;

  const hosts = [];
  for (let h = network + 1; h < broadcast; h++) {
    hosts.push(intToIp(h >>> 0));
    if (hosts.length > 4096) break; // hard safety cap
  }

  const gateway = await detectGateway(iface.address);

  return {
    ok: true,
    interfaceName: iface.name,
    localIp: iface.address,
    mac: iface.mac,
    netmask: iface.netmask,
    prefix,
    effectivePrefix,
    cidr: `${intToIp(network)}/${effectivePrefix}`,
    networkBase: intToIp(network),
    gateway,
    hostCount: hosts.length,
    hosts
  };
}

module.exports = { getNetworkInfo, ipToInt, intToIp, maskToPrefix, run };
