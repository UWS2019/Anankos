'use strict';
/*
 * wifi.js
 * Reads the security type of the Wi-Fi network THIS computer is connected to,
 * using built-in OS commands. Read-only. If the machine is on Ethernet or the
 * command isn't available, it simply returns null and the scan continues.
 */
const { run } = require('./netinfo');

function classify(authText) {
  const t = (authText || '').toLowerCase();
  if (!t) return null;
  if (t.includes('open') || t.includes('none')) return { level: 'open', label: 'Open (no password)' };
  if (t.includes('wep')) return { level: 'wep', label: 'WEP (broken)' };
  if (t.includes('wpa3')) return { level: 'wpa3', label: 'WPA3' };
  if (t.includes('wpa2')) return { level: 'wpa2', label: 'WPA2' };
  if (t.includes('wpa')) return { level: 'wpa', label: 'WPA (old)' };
  return { level: 'unknown', label: authText };
}

async function getWifiInfo() {
  const platform = process.platform;
  try {
    if (platform === 'win32') {
      const out = await run('netsh', ['wlan', 'show', 'interfaces']);
      if (!out) return null;
      const ssid = (out.match(/^\s*SSID\s*:\s*(.+)$/im) || [])[1];
      const auth = (out.match(/Authentication\s*:\s*(.+)/i) || [])[1];
      const band = (out.match(/Band\s*:\s*(.+)/i) || [])[1];
      if (!auth) return null;
      return { ssid: ssid && ssid.trim(), security: classify(auth), band: band && band.trim() };
    }
    if (platform === 'darwin') {
      // airport -I is deprecated but still present on many macOS versions.
      const airport = '/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport';
      const out = await run(airport, ['-I']);
      if (!out) return null;
      const ssid = (out.match(/^\s*SSID:\s*(.+)$/im) || [])[1];
      const auth = (out.match(/link auth:\s*(.+)/i) || out.match(/auth:\s*(.+)/i) || [])[1];
      if (!auth) return { ssid: ssid && ssid.trim(), security: null };
      return { ssid: ssid && ssid.trim(), security: classify(auth) };
    }
    // Linux
    const out = await run('nmcli', ['-t', '-f', 'active,ssid,security', 'dev', 'wifi']);
    if (out) {
      const line = out.split(/\r?\n/).find((l) => l.startsWith('yes:'));
      if (line) {
        const parts = line.split(':');
        const ssid = parts[1];
        const sec = parts.slice(2).join(':');
        return { ssid, security: classify(sec) };
      }
    }
    return null;
  } catch (_) {
    return null;
  }
}

module.exports = { getWifiInfo };
