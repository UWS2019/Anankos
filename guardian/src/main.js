'use strict';
/*
 * main.js - Electron main process.
 * Creates the window, and exposes the scan engine to the UI over IPC. The
 * engine runs here (Node side); the UI (renderer) stays sandboxed and can only
 * call the specific channels wired up in preload.js.
 */
const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { runScan } = require('./scanner');
const { getNetworkInfo } = require('./scanner/netinfo');

let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1120,
    height: 820,
    minWidth: 900,
    minHeight: 640,
    backgroundColor: '#000000',
    title: 'Anankos Guardian',
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  // Open external links (router admin page) in the system browser, not the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ---- IPC ----

ipcMain.handle('guardian:netinfo', async () => {
  try {
    return await getNetworkInfo();
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
});

ipcMain.handle('guardian:scan', async (event, opts) => {
  try {
    const report = await runScan(opts || {}, (evt) => {
      // Stream progress back to the renderer.
      if (!event.sender.isDestroyed()) event.sender.send('guardian:scan-progress', evt);
    });
    return report;
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
});

ipcMain.handle('guardian:open-external', async (event, url) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) {
    await shell.openExternal(url);
    return true;
  }
  return false;
});

ipcMain.handle('guardian:save-report', async (event, json) => {
  const { dialog } = require('electron');
  const fs = require('fs');
  const res = await dialog.showSaveDialog(win, {
    title: 'Save scan report',
    defaultPath: `anankos-guardian-report-${Date.now()}.json`,
    filters: [{ name: 'JSON', extensions: ['json'] }]
  });
  if (res.canceled || !res.filePath) return false;
  fs.writeFileSync(res.filePath, typeof json === 'string' ? json : JSON.stringify(json, null, 2));
  return res.filePath;
});
