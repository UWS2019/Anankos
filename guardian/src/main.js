'use strict';
/*
 * main.js - Electron main process.
 * Creates the window, and exposes the scan engine to the UI over IPC. The
 * engine runs here (Node side); the UI (renderer) stays sandboxed and can only
 * call the specific channels wired up in preload.js.
 */
const { app, BrowserWindow, ipcMain, shell, clipboard, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { runScan } = require('./scanner');
const { getNetworkInfo } = require('./scanner/netinfo');
const { buildReportHtml } = require('./scanner/report');
const { buildDebriefPrompt } = require('./scanner/debrief');
const { Scheduler } = require('./scanner/scheduler');
const { appendHistory, loadHistory, clearHistory, historyToCsv, loadSchedule } = require('./scanner/history');

let win = null;
let scheduler = null;
let dataDir = null;

function emitToWin(channel, payload) {
  if (win && win.webContents && !win.webContents.isDestroyed()) {
    win.webContents.send(channel, payload);
  }
}

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
  dataDir = app.getPath('userData');
  scheduler = new Scheduler(dataDir, (evt) => emitToWin('guardian:schedule-event', evt));
  createWindow();
  // Resume a persisted operator schedule once the window is ready to receive events.
  win.webContents.once('did-finish-load', () => {
    scheduler.resumeIfEnabled();
  });
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
    // Log manual scans to history too, so the operator trend is complete.
    if (report && report.ok && dataDir) {
      try { appendHistory(dataDir, report, 'manual'); } catch (_) { /* ignore */ }
    }
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

// ---- Exports: JSON / HTML / PDF ----
function stamp() {
  return new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19);
}

ipcMain.handle('guardian:export', async (event, { report, format }) => {
  if (!report) return false;
  try {
    if (format === 'json') {
      const res = await dialog.showSaveDialog(win, {
        title: 'Save scan report (JSON)',
        defaultPath: `anankos-guardian-${stamp()}.json`,
        filters: [{ name: 'JSON', extensions: ['json'] }]
      });
      if (res.canceled || !res.filePath) return false;
      fs.writeFileSync(res.filePath, JSON.stringify(report, null, 2));
      return res.filePath;
    }
    if (format === 'html') {
      const res = await dialog.showSaveDialog(win, {
        title: 'Save scan report (HTML)',
        defaultPath: `anankos-guardian-${stamp()}.html`,
        filters: [{ name: 'HTML', extensions: ['html'] }]
      });
      if (res.canceled || !res.filePath) return false;
      fs.writeFileSync(res.filePath, buildReportHtml(report));
      return res.filePath;
    }
    if (format === 'pdf') {
      const res = await dialog.showSaveDialog(win, {
        title: 'Save scan report (PDF)',
        defaultPath: `anankos-guardian-${stamp()}.pdf`,
        filters: [{ name: 'PDF', extensions: ['pdf'] }]
      });
      if (res.canceled || !res.filePath) return false;
      // Render the HTML report in an offscreen window and print it to PDF.
      const pdfWin = new BrowserWindow({ show: false, webPreferences: { offscreen: true } });
      const html = buildReportHtml(report);
      await pdfWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
      const pdf = await pdfWin.webContents.printToPDF({ printBackground: true, pageSize: 'A4', margins: { marginType: 'none' } });
      fs.writeFileSync(res.filePath, pdf);
      pdfWin.destroy();
      return res.filePath;
    }
    return false;
  } catch (e) {
    return { error: String((e && e.message) || e) };
  }
});

// ---- AI debrief prompt + clipboard ----
ipcMain.handle('guardian:debrief', async (event, report) => {
  try { return buildDebriefPrompt(report); } catch (e) { return ''; }
});

ipcMain.handle('guardian:copy', async (event, text) => {
  clipboard.writeText(String(text || ''));
  return true;
});

// ---- Operator scheduler ----
ipcMain.handle('guardian:schedule-start', async (event, cfg) => scheduler.start(cfg || {}));
ipcMain.handle('guardian:schedule-stop', async () => scheduler.stop());
ipcMain.handle('guardian:schedule-status', async () => scheduler.status());
ipcMain.handle('guardian:schedule-run-now', async () => { scheduler.runNow(); return scheduler.status(); });

// ---- History ----
ipcMain.handle('guardian:history-load', async () => loadHistory(dataDir));
ipcMain.handle('guardian:history-clear', async () => clearHistory(dataDir));
ipcMain.handle('guardian:history-export', async () => {
  const res = await dialog.showSaveDialog(win, {
    title: 'Export scan history (CSV)',
    defaultPath: `anankos-guardian-history-${stamp()}.csv`,
    filters: [{ name: 'CSV', extensions: ['csv'] }]
  });
  if (res.canceled || !res.filePath) return false;
  fs.writeFileSync(res.filePath, historyToCsv(loadHistory(dataDir)));
  return res.filePath;
});
