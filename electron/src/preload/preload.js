const { contextBridge, ipcRenderer } = require("electron");

const apiBaseUrl = String(
  process.env.NJORDHR_API_BASE_URL
    || process.env.NJORDHR_SERVER_URL
    || ""
).trim();

contextBridge.exposeInMainWorld("NjordDesktop", {
  runtimeConfig: {
    apiBaseUrl
  },
  diagnostics: () => ipcRenderer.invoke("njordhr:diagnostics"),
  startupErrorDetails: () => ipcRenderer.invoke("njordhr:get-startup-error-details"),
  onStartupErrorDetails: (callback) => {
    if (typeof callback !== "function") {
      return () => {};
    }
    const listener = (_event, details) => callback(details);
    ipcRenderer.on("njordhr:error-details", listener);
    return () => ipcRenderer.removeListener("njordhr:error-details", listener);
  },
  openLogs: () => ipcRenderer.invoke("njordhr:open-logs"),
  retryStartup: () => ipcRenderer.invoke("njordhr:retry-startup"),
  copyText: (text) => ipcRenderer.invoke("njordhr:copy-text", text),
  closeWindow: () => ipcRenderer.invoke("njordhr:close-window")
});
