'use strict';
/*
 * checks.js
 * The "brain". Takes the raw scan results (devices, open ports, Wi-Fi info) and
 * turns them into plain-English findings, each with:
 *   - a severity,
 *   - what it means in everyday words,
 *   - why it matters,
 *   - exact step-by-step instructions to fix it (guided remediation).
 * It never changes anything and never tries passwords - it only observes and
 * advises. The user makes every change themselves, following the steps.
 */

const SEVERITY_WEIGHT = { critical: 30, high: 18, medium: 9, low: 3, info: 0 };

function newFinding(f) {
  return Object.assign(
    { id: '', severity: 'info', title: '', device: null, plain: '', why: '', steps: [], routerLink: false },
    f
  );
}

/*
 * result: {
 *   netInfo, devices: [{ip,mac,vendor,isGateway,isThisDevice,ports:[{port,name,risk,note}]}],
 *   wifi
 * }
 */
function analyze(result) {
  const findings = [];
  const { devices = [], wifi, netInfo = {} } = result;

  const gatewayIp = netInfo.gateway;
  const routerUrl = gatewayIp ? `http://${gatewayIp}` : null;

  // ---- Wi-Fi encryption ----
  if (wifi && wifi.security) {
    const lvl = wifi.security.level;
    if (lvl === 'open') {
      findings.push(newFinding({
        id: 'wifi-open', severity: 'critical',
        title: 'Your Wi-Fi has no password',
        plain: `The network "${wifi.ssid || 'your Wi-Fi'}" is open, so anyone nearby can join it without a password and see your devices.`,
        why: 'An open network lets strangers use your internet and attempt to reach your personal devices.',
        steps: [
          'Open your router settings using the button below (or type the router address into a web browser).',
          'Sign in with the router admin password (on a sticker on the router if you have never changed it).',
          'Find "Wireless" or "Wi-Fi" settings.',
          'Set Security to "WPA3" (or "WPA2" if WPA3 is not offered).',
          'Choose a strong password of at least 12 characters and save.'
        ],
        routerLink: true
      }));
    } else if (lvl === 'wep' || lvl === 'wpa') {
      findings.push(newFinding({
        id: 'wifi-weak', severity: 'high',
        title: `Your Wi-Fi uses outdated security (${wifi.security.label})`,
        plain: `The network "${wifi.ssid || 'your Wi-Fi'}" uses ${wifi.security.label}, which can be broken quickly with free tools.`,
        why: 'Old Wi-Fi encryption can be cracked, letting an attacker read your traffic or join your network.',
        steps: [
          'Open your router settings using the button below.',
          'Sign in with the router admin password.',
          'Go to "Wireless" / "Wi-Fi" security settings.',
          'Change the security mode to "WPA3" or "WPA2 (AES)".',
          'Save and reconnect your devices with the password.'
        ],
        routerLink: true
      }));
    } else if (lvl === 'wpa2') {
      findings.push(newFinding({
        id: 'wifi-wpa2', severity: 'info',
        title: 'Wi-Fi security is acceptable (WPA2)',
        plain: `Your Wi-Fi uses WPA2, which is fine. If your router supports WPA3, switching to it is even safer.`,
        why: 'WPA2 is still secure for home use; WPA3 adds extra protection against password guessing.',
        steps: ['Optional: in router Wi-Fi settings, switch the mode to "WPA3" or "WPA2/WPA3" if available.'],
        routerLink: true
      }));
    }
  }

  // ---- Per-device port findings ----
  for (const d of devices) {
    const label = deviceLabel(d);
    const ports = d.ports || [];
    const portNums = new Set(ports.map((p) => p.port));

    // Plaintext remote admin: Telnet.
    if (portNums.has(23) || portNums.has(2323)) {
      findings.push(newFinding({
        id: `telnet-${d.ip}`, severity: 'high', device: d.ip,
        title: `${label} allows Telnet remote control`,
        plain: `${label} (${d.ip}) has Telnet open. Telnet sends everything, including passwords, as readable text.`,
        why: 'Telnet is a classic target for attackers and malware (this is how many IoT botnets spread). It should almost never be on.',
        steps: [
          `In that device's settings (or the router if it is a router/IoT device), find "Remote management", "Telnet", or "Administration".`,
          'Turn Telnet OFF.',
          'If you need remote access, use SSH instead and set a strong password.'
        ]
      }));
    }
    // FTP
    if (portNums.has(21)) {
      findings.push(newFinding({
        id: `ftp-${d.ip}`, severity: 'medium', device: d.ip,
        title: `${label} shares files over unencrypted FTP`,
        plain: `${label} (${d.ip}) has an FTP server open. FTP logins and files travel unencrypted.`,
        why: 'Anyone on the network can capture FTP credentials. If the device is exposed to the internet, the risk is much higher.',
        steps: [
          'Decide whether you actually use FTP on this device.',
          'If not, disable the FTP service in the device settings.',
          'If you do need file sharing, prefer SFTP/SMB with a password instead.'
        ]
      }));
    }
    // Remote desktop / screen control
    if (portNums.has(3389)) {
      findings.push(newFinding({
        id: `rdp-${d.ip}`, severity: 'high', device: d.ip,
        title: `${label} has Remote Desktop open`,
        plain: `${label} (${d.ip}) accepts Windows Remote Desktop connections.`,
        why: 'Remote Desktop is heavily targeted. If this port is also open to the internet it can lead to full takeover and ransomware.',
        steps: [
          'If you do not use Remote Desktop, turn it off: Windows Settings > System > Remote Desktop > Off.',
          'If you do use it, ensure the account has a long password and enable Network Level Authentication.',
          'Make sure your router does NOT forward port 3389 to this PC.'
        ]
      }));
    }
    if ([5900, 5901].some((p) => portNums.has(p))) {
      findings.push(newFinding({
        id: `vnc-${d.ip}`, severity: 'high', device: d.ip,
        title: `${label} allows remote screen control (VNC)`,
        plain: `${label} (${d.ip}) runs VNC, which lets someone see and control its screen remotely.`,
        why: 'VNC is often left without a password or with a weak one, giving attackers direct control of the device.',
        steps: [
          'If you did not set up VNC on purpose, disable it in the device settings.',
          'If you need it, set a strong password and restrict it to trusted devices only.'
        ]
      }));
    }
    if (portNums.has(5555)) {
      findings.push(newFinding({
        id: `adb-${d.ip}`, severity: 'high', device: d.ip,
        title: `${label} exposes Android Debug Bridge`,
        plain: `${label} (${d.ip}) has ADB (port 5555) open, which can allow full remote control of an Android device.`,
        why: 'Open ADB is a well-known way malware spreads between Android/IoT devices.',
        steps: [
          'On the device, go to Developer Options and turn OFF "USB debugging" / "Wireless debugging".',
          'If this is a TV box or IoT device, update its firmware and disable debugging.'
        ]
      }));
    }
    // Exposed databases
    const dbPorts = [3306, 5432, 6379, 27017].filter((p) => portNums.has(p));
    if (dbPorts.length) {
      findings.push(newFinding({
        id: `db-${d.ip}`, severity: 'high', device: d.ip,
        title: `${label} exposes a database to the network`,
        plain: `${label} (${d.ip}) has a database service reachable from the network (${ports.filter((p) => dbPorts.includes(p.port)).map((p) => p.name).join(', ')}).`,
        why: 'Databases on these ports are frequently left without a password and are a direct route to your data.',
        steps: [
          'Confirm this device is meant to run a database.',
          'Restrict the database to listen only on 127.0.0.1 (localhost) if other machines do not need it.',
          'Set a strong database password and enable authentication.'
        ]
      }));
    }
    // Camera stream
    if (portNums.has(554)) {
      findings.push(newFinding({
        id: `cam-${d.ip}`, severity: 'medium', device: d.ip,
        title: `${label} looks like a camera with an open video stream`,
        plain: `${label} (${d.ip}) exposes an RTSP video stream (port 554).`,
        why: 'Many cameras ship with default passwords. An open stream can let strangers watch the feed.',
        steps: [
          'Log into the camera and change the default username and password.',
          'Update the camera firmware.',
          'Disable any "cloud" or "P2P" feature you do not use.'
        ]
      }));
    }
    // UPnP on a device
    if ([1900, 5000, 49152].some((p) => portNums.has(p))) {
      findings.push(newFinding({
        id: `upnp-${d.ip}`, severity: 'low', device: d.ip,
        title: `${label} uses UPnP auto-configuration`,
        plain: `${label} (${d.ip}) responds to UPnP, which lets devices open ports and discover each other automatically.`,
        why: 'UPnP is convenient but can let a compromised device silently open your network to the internet.',
        steps: [
          'If you do not knowingly need it, consider turning UPnP OFF on the router.',
          'Open your router settings and look under "UPnP", "NAT Forwarding", or "Advanced".'
        ],
        routerLink: d.isGateway
      }));
    }
    // Windows file sharing broadly open
    if (portNums.has(445) || portNums.has(139)) {
      findings.push(newFinding({
        id: `smb-${d.ip}`, severity: 'medium', device: d.ip,
        title: `${label} has Windows file sharing open`,
        plain: `${label} (${d.ip}) exposes SMB/NetBIOS file sharing.`,
        why: 'File sharing is normal on a home PC, but if shares are unprotected or the device is old, it can leak files or spread malware.',
        steps: [
          'Make sure the device is fully updated.',
          'Share only the folders you intend to, and require a password.',
          'Disable SMBv1 (the old, insecure version) if your system still has it on.'
        ]
      }));
    }
  }

  // ---- Router / gateway specific ----
  const gateway = devices.find((d) => d.isGateway);
  if (gateway) {
    const gp = new Set((gateway.ports || []).map((p) => p.port));
    if ([80, 8080].some((p) => gp.has(p)) && !gp.has(443)) {
      findings.push(newFinding({
        id: 'router-admin-http', severity: 'medium', device: gateway.ip,
        title: 'Router admin page is unencrypted',
        plain: `Your router's settings page (${gateway.ip}) is served over plain HTTP, so the admin password is sent without encryption.`,
        why: 'Anyone on your network could capture the router admin password as you log in.',
        steps: [
          'Open the router settings using the button below.',
          'Look for an option to enable "HTTPS" for the admin page and turn it on if available.',
          'Most importantly, make sure the admin password is NOT the default one.'
        ],
        routerLink: true
      }));
    }
    // Always advise verifying router admin password (we never test it).
    findings.push(newFinding({
      id: 'router-admin-password', severity: 'info', device: gateway.ip,
      title: 'Confirm your router admin password is not the default',
      plain: `Guardian found your router at ${gateway.ip}. It does not try passwords, so please confirm the admin login has been changed from the factory default.`,
      why: 'Default router passwords are published online. Changing it is the single most important home-network fix.',
      steps: [
        'Open the router settings using the button below.',
        'Go to "Administration" / "System" / "Password".',
        'Set a strong, unique admin password (not the Wi-Fi password).'
      ],
      routerLink: true
    }));
    if (gp.has(7547)) {
      findings.push(newFinding({
        id: 'router-tr069', severity: 'low', device: gateway.ip,
        title: 'ISP remote-management port is open on the router',
        plain: `Your router has the TR-069 remote-management port (7547) open.`,
        why: 'This port is used by ISPs but has had serious vulnerabilities. If you do not need remote ISP management, it is safer closed.',
        steps: [
          'This is usually controlled by your internet provider.',
          'If you manage your own router, look for "Remote management" / "TR-069 / CWMP" and disable it if unused.',
          'Otherwise, ask your ISP whether it can be disabled.'
        ],
        routerLink: true
      }));
    }
  }

  // ---- Unknown devices awareness ----
  const unknown = devices.filter((d) => !d.isThisDevice && !d.isGateway && !d.vendor);
  if (unknown.length) {
    findings.push(newFinding({
      id: 'unknown-devices', severity: 'low',
      title: `${unknown.length} device${unknown.length > 1 ? 's' : ''} could not be identified`,
      plain: `Guardian found ${unknown.length} device(s) it could not name: ${unknown.map((d) => d.ip).join(', ')}.`,
      why: 'Unknown devices are usually your own gadgets, but an unfamiliar one could be an uninvited guest on your Wi-Fi.',
      steps: [
        'Walk through the device list and match each one to something you own (phones, TVs, consoles, plugs, cameras).',
        'If any device is not yours, change your Wi-Fi password immediately.',
        'Removing/renaming devices in the router\'s device list makes future scans clearer.'
      ],
      routerLink: true
    }));
  }

  // ---- Score ----
  let penalty = 0;
  for (const f of findings) penalty += SEVERITY_WEIGHT[f.severity] || 0;
  let score = Math.max(0, Math.min(100, 100 - penalty));
  const grade = score >= 90 ? 'Excellent' : score >= 75 ? 'Good' : score >= 55 ? 'Fair' : score >= 35 ? 'At Risk' : 'Critical';

  // Sort findings: severity desc, then by id.
  const order = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  findings.sort((a, b) => (order[a.severity] - order[b.severity]) || a.id.localeCompare(b.id));

  const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  findings.forEach((f) => { counts[f.severity]++; });

  return { findings, score, grade, counts, routerUrl };
}

function deviceLabel(d) {
  if (d.isGateway) return 'Your router';
  if (d.isThisDevice) return 'This computer';
  if (d.vendor) return `${d.vendor} device`;
  return 'An unidentified device';
}

module.exports = { analyze, deviceLabel, SEVERITY_WEIGHT };
