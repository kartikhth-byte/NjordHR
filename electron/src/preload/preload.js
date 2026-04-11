const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("NjordDesktop", {
  diagnostics: () => ipcRenderer.invoke("njordhr:diagnostics"),
  openLogs: () => ipcRenderer.invoke("njordhr:open-logs")
});
