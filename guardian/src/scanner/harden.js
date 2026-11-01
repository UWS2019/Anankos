'use strict';
/*
 * harden.js
 * Optional, opt-in hardening of THIS computer only. Reading the firewall state
 * needs no privileges; applying a change may need administrator rights, so each
 * action returns a result that either confirms success or hands back the exact
 * command for the user to run themselves. Nothing here touches other devices,
 * and every change is safe and reversible (enabling the OS firewall).
 *
 * The app never runs these on its own - the user clicks an explicit button for
 * each action.
 */
const { execFile } = require('child_process');

function run(cmd, args, timeout = 8000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, windowsHide: true }, (err, stdout, stderr) => {
      resolve({ err, stdout: String(stdout || ''), stderr: String(stderr || '') });
    });
  });
}

// ---- Read firewall state (no privileges needed) ----
async function getFirewallState() {
  const platform = process.platform;
  try {
    if (platform === 'win32') {
      const { stdout } = await run('netsh', ['advfirewall', 'show', 'allprofiles', 'state']);
      const states = (stdout.match(/State\s+(ON|OFF)/gi) || []).map((s) => /ON/i.test(s));
      if (!states.length) return { known: false };
      return { known: true, enabled: states.every(Boolean), detail: states.map((s) => (s ? 'on' : 'off')).join('/') };
    }
    if (platform === 'darwin') {
      const { stdout } = await run('/usr/libexec/ApplicationFirewall/socketfilterfw', ['--getglobalstate']);
      if (!stdout) return { known: false };
      return { known: true, enabled: /State = 1|enabled/i.test(stdout) };
    }
    // linux
    const { stdout, err } = await run('ufw', ['status']);
    if (err) return { known: false }; // ufw not installed or needs sudo
    return { known: true, enabled: /Status:\s*active/i.test(stdout) };
  } catch (_) {
    return { known: false };
  }
}

const MANUAL = {
  win32: 'netsh advfirewall set allprofiles state on',
  darwin: 'sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on',
  linux: 'sudo ufw enable'
};

// The catalog of available local hardening actions.
async function getHardeningStatus() {
  const fw = await getFirewallState();
  const platform = process.platform;
  const actions = [{
    id: 'enable-firewall',
    title: 'Turn on the built-in firewall',
    plain: 'The firewall blocks uninvited connections from other devices to this computer. It should always be on.',
    state: fw.known ? (fw.enabled ? 'ok' : 'action') : 'unknown',
    stateLabel: fw.known ? (fw.enabled ? 'Already on' : 'Currently OFF') : 'Could not read status',
    manualCommand: MANUAL[platform] || null
  }];
  return { platform, firewall: fw, actions };
}

async function applyHardening(actionId) {
  const platform = process.platform;
  if (actionId !== 'enable-firewall') {
    return { ok: false, message: 'Unknown action.' };
  }
  let cmd;
  let args;
  if (platform === 'win32') { cmd = 'netsh'; args = ['advfirewall', 'set', 'allprofiles', 'state', 'on']; }
  else if (platform === 'darwin') { cmd = '/usr/libexec/ApplicationFirewall/socketfilterfw'; args = ['--setglobalstate', 'on']; }
  else { cmd = 'ufw'; args = ['--force', 'enable']; }

  const { err, stderr } = await run(cmd, args);
  if (!err) {
    const after = await getFirewallState();
    return { ok: true, message: 'Firewall is now ON.', verified: after.enabled === true };
  }
  // Most failures are missing administrator rights.
  const needsAdmin = /denied|administrator|permitted|sudo|root|elevat/i.test((stderr || '') + (err.message || ''));
  return {
    ok: false,
    needsAdmin,
    message: needsAdmin
      ? 'This change needs administrator rights. Run the command below in an elevated terminal, then re-check.'
      : 'Could not apply automatically. Run the command below yourself.',
    manualCommand: MANUAL[platform] || null
  };
}

module.exports = { getHardeningStatus, applyHardening, getFirewallState };
