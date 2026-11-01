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
  saveReport: (json) => ipcRenderer.invoke('guardian:save-report', json),
  platform: process.platform
});
