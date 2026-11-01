'use strict';
/*
 * oui.js
 * Best-effort MAC -> hardware vendor lookup using the first 3 bytes (the OUI).
 * This is a compact, representative table covering vendors common on home
 * networks. It is intentionally offline and small; drop a fuller IEEE OUI list
 * into `extraOui` to extend coverage without code changes.
 */
const OUI = {
  // Apple
  '00:03:93': 'Apple', '00:05:02': 'Apple', '00:1b:63': 'Apple', '00:1e:c2': 'Apple',
  '00:25:00': 'Apple', '3c:07:54': 'Apple', 'a4:5e:60': 'Apple', 'f0:18:98': 'Apple',
  'ac:bc:32': 'Apple', 'dc:a9:04': 'Apple', '7c:d1:c3': 'Apple',
  // Samsung
  '00:12:fb': 'Samsung', '00:15:99': 'Samsung', '34:23:87': 'Samsung', '5c:0a:5b': 'Samsung',
  '78:1f:db': 'Samsung', 'bc:76:5e': 'Samsung', 'e8:50:8b': 'Samsung',
  // Google / Nest
  '3c:5a:b4': 'Google', '54:60:09': 'Google', 'f4:f5:d8': 'Google', 'd8:eb:46': 'Google',
  '18:b4:30': 'Google Nest',
  // Amazon (Echo / Fire / Ring)
  '44:65:0d': 'Amazon', '68:37:e9': 'Amazon', 'fc:65:de': 'Amazon', '74:c2:46': 'Amazon',
  '0c:47:c9': 'Amazon',
  // Routers / networking
  '00:1d:7e': 'Cisco-Linksys', 'c0:c1:c0': 'Cisco', '00:18:4d': 'Netgear',
  '20:e5:2a': 'Netgear', 'a0:40:a0': 'Netgear', '00:14:6c': 'Netgear',
  '14:cc:20': 'TP-Link', '50:c7:bf': 'TP-Link', 'a4:2b:b0': 'TP-Link', 'd8:07:b6': 'TP-Link',
  '00:1f:a4': 'Asus', '2c:56:dc': 'Asus', '04:d4:c4': 'Asus',
  '00:26:5a': 'D-Link', '1c:bd:b9': 'D-Link',
  '24:a4:3c': 'Ubiquiti', 'fc:ec:da': 'Ubiquiti', '78:8a:20': 'Ubiquiti',
  'b4:fb:e4': 'Ubiquiti', 'e0:63:da': 'Ubiquiti',
  // Printers
  '00:1b:a9': 'Brother', '3c:2a:f4': 'Brother', '00:00:48': 'Seiko Epson',
  'ac:18:26': 'Epson', '00:1e:0b': 'HP', '3c:d9:2b': 'HP', '98:e7:f4': 'HP',
  // IoT / cameras / misc
  '50:02:91': 'Espressif (ESP/IoT)', '24:0a:c4': 'Espressif (ESP/IoT)',
  '7c:9e:bd': 'Espressif (ESP/IoT)', 'b8:27:eb': 'Raspberry Pi',
  'dc:a6:32': 'Raspberry Pi', 'e4:5f:01': 'Raspberry Pi',
  '00:80:f0': 'Panasonic', '00:0f:7c': 'ACTi (camera)', '00:62:6e': 'Hikvision (camera)',
  '44:19:b6': 'Hangzhou Hikvision', 'bc:ad:28': 'Hangzhou Hikvision',
  // PCs / NICs
  '00:50:56': 'VMware', '08:00:27': 'VirtualBox', '00:15:5d': 'Microsoft Hyper-V',
  'd4:3b:04': 'Intel', '94:c6:91': 'Intel', '00:1c:c0': 'Intel'
};

// Optional user-supplied extensions, merged over the base table.
let extraOui = {};
try {
  extraOui = require('./oui-extra.json'); // not shipped by default; add your own
} catch (_) {
  extraOui = {};
}

function vendorForMac(mac) {
  if (!mac) return null;
  const norm = String(mac).toLowerCase().replace(/-/g, ':');
  const prefix = norm.split(':').slice(0, 3).join(':');
  return extraOui[prefix] || OUI[prefix] || null;
}

module.exports = { vendorForMac };
