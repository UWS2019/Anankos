'use strict';
/*
 * preload.js - the only bridge between the sandboxed UI and Node.
 * Exposes a tiny, explicit API on window.guardian. The UI cannot touch the
 * filesystem, network, or anything else except through these named channels.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('guardian', {
  getNetworkInfo: () => ipcRenderer.invoke('guardian:netinfo'),
  startScan: (opts) => ipcRenderer.invoke('guardian:scan', opts),
  onProgress: (cb) => {
    const listener = (_e, evt) => cb(evt);
    ipcRenderer.on('guardian:scan-progress', listener);
    return () => ipcRenderer.removeListener('guardian:scan-progress', listener);
  },
  openExternal: (url) => ipcRenderer.invoke('guardian:open-external', url),

  // Exports
  exportReport: (report, format) => ipcRenderer.invoke('guardian:export', { report, format }),

  // AI debrief + clipboard
  buildDebrief: (report) => ipcRenderer.invoke('guardian:debrief', report),
  copyText: (text) => ipcRenderer.invoke('guardian:copy', text),

  // Operator scheduler
  scheduleStart: (cfg) => ipcRenderer.invoke('guardian:schedule-start', cfg),
  scheduleStop: () => ipcRenderer.invoke('guardian:schedule-stop'),
  scheduleStatus: () => ipcRenderer.invoke('guardian:schedule-status'),
  scheduleRunNow: () => ipcRenderer.invoke('guardian:schedule-run-now'),
  onScheduleEvent: (cb) => {
    const listener = (_e, evt) => cb(evt);
    ipcRenderer.on('guardian:schedule-event', listener);
    return () => ipcRenderer.removeListener('guardian:schedule-event', listener);
  },

  // History
  historyLoad: () => ipcRenderer.invoke('guardian:history-load'),
  historyClear: () => ipcRenderer.invoke('guardian:history-clear'),
  historyExport: () => ipcRenderer.invoke('guardian:history-export'),

  platform: process.platform
});
