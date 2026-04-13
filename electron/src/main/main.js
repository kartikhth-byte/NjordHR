const path = require("path");
const crypto = require("crypto");
const { app, clipboard, ipcMain } = require("electron");
const {
  bootstrapConfigFile,
  resolveRuntimePaths,
  choosePorts,
  resolvePythonCommand,
  buildEnvironment,
  persistRuntimeEnvironment
} = require("./runtime-manager");
const { ProcessManager } = require("./process-manager");
const { createSplashWindow, createErrorWindow, createMainWindow } = require("./windows");
const {
  buildDiagnostics,
  writeStartupDiagnostics,
  buildStartupErrorDetails,
  openLogsFolder
} = require("./diagnostics");

let splashWindow = null;
let mainWindow = null;
let errorWindow = null;
let processManager = null;
let diagnostics = null;
let startupErrorDetails = null;
let isLaunching = false;
let isQuitting = false;
const APP_NAME = "NjordHR";
const PRELOAD_PATH = path.join(__dirname, "..", "preload", "preload.js");
const USER_DATA_OVERRIDE = String(process.env.NJORDHR_USER_DATA_DIR || "").trim();
const ALLOW_MULTI_INSTANCE = String(process.env.NJORDHR_ALLOW_MULTI_INSTANCE || "").trim().toLowerCase() === "true";

app.setName(APP_NAME);
app.setAppUserModelId(APP_NAME);
app.setPath(
  "userData",
  USER_DATA_OVERRIDE ? path.resolve(USER_DATA_OVERRIDE) : path.join(app.getPath("appData"), APP_NAME)
);

async function launchApp() {
  const launchId = crypto.randomUUID();
  const paths = resolveRuntimePaths(app);
  bootstrapConfigFile(paths, process.env);
  const ports = await choosePorts(paths);
  const python = resolvePythonCommand(app);
  const env = buildEnvironment(paths, ports, {
    packaged: app.isPackaged,
    python
  });
  persistRuntimeEnvironment(paths, ports, env);

  diagnostics = buildDiagnostics(paths, ports, {
    launchId,
    appVersion: app.getVersion(),
    platform: process.platform,
    packaged: app.isPackaged,
    pythonCommand: [python.command, ...python.args].join(" ").trim(),
    authMode: env.NJORDHR_AUTH_MODE || "",
    useLocalAgent: String(env.USE_LOCAL_AGENT || "").trim().toLowerCase() === "true",
    useSupabaseDb: String(env.USE_SUPABASE_DB || "").trim().toLowerCase() === "true"
  });
  writeStartupDiagnostics(paths, diagnostics);
  processManager = new ProcessManager({ app, paths, ports, python, env, launchId });

  splashWindow = createSplashWindow();
  const runtime = await processManager.ensureBackendStarted();

  mainWindow = createMainWindow(
    ports.browserUrl,
    PRELOAD_PATH
  );
  processManager.ensureAgentStarted();

  mainWindow.once("ready-to-show", () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
  });

  return runtime;
}

async function start() {
  if (isLaunching) {
    return;
  }
  isLaunching = true;
  try {
    startupErrorDetails = null;
    if (errorWindow && !errorWindow.isDestroyed()) {
      errorWindow.close();
      errorWindow = null;
    }
    await launchApp();
  } catch (error) {
    const errorPaths = processManager?.paths || diagnostics || {};
    const errorDiagnostics = diagnostics || {};
    if (processManager) {
      try {
        await processManager.shutdown();
      } catch (_shutdownError) {
        // best effort
      }
      processManager = null;
    }
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    startupErrorDetails = buildStartupErrorDetails(
      errorPaths,
      errorDiagnostics,
      error
    );
    errorWindow = createErrorWindow(startupErrorDetails, PRELOAD_PATH);
  } finally {
    isLaunching = false;
  }
}

const gotLock = ALLOW_MULTI_INSTANCE ? true : app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(start);
}

ipcMain.handle("njordhr:diagnostics", async () => diagnostics || {});
ipcMain.handle("njordhr:get-startup-error-details", async () => startupErrorDetails || {});
ipcMain.handle("njordhr:open-logs", async () => {
  const target = processManager?.paths || diagnostics;
  if (!target) return "";
  return openLogsFolder(target);
});
ipcMain.handle("njordhr:retry-startup", async () => {
  app.relaunch();
  app.quit();
  return true;
});
ipcMain.handle("njordhr:copy-text", async (_event, text) => {
  clipboard.writeText(String(text || ""));
  return true;
});
ipcMain.handle("njordhr:close-window", async (event) => {
  const window = event.sender.getOwnerBrowserWindow();
  if (window && !window.isDestroyed()) {
    window.close();
  }
  return true;
});

app.on("activate", () => {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  } else if (errorWindow && !errorWindow.isDestroyed()) {
    errorWindow.show();
    errorWindow.focus();
  } else if (!isLaunching) {
    start();
  } else {
    return;
  }
});

app.on("before-quit", (event) => {
  if (isQuitting || !processManager) {
    return;
  }
  event.preventDefault();
  isQuitting = true;
  processManager.shutdown()
    .catch(() => {
      // best effort
    })
    .finally(() => {
      processManager = null;
      app.quit();
    });
});
