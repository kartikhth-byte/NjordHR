const path = require("path");
const crypto = require("crypto");
const { app, ipcMain } = require("electron");
const {
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
const APP_NAME = "NjordHR";

app.setName(APP_NAME);
app.setAppUserModelId(APP_NAME);
app.setPath("userData", path.join(app.getPath("appData"), APP_NAME));

async function launchApp() {
  const launchId = crypto.randomUUID();
  const paths = resolveRuntimePaths(app);
  const ports = await choosePorts(paths);
  const python = resolvePythonCommand(app);
  const env = buildEnvironment(paths, ports);
  persistRuntimeEnvironment(paths, ports, env);

  diagnostics = buildDiagnostics(paths, ports, {
    launchId,
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
    path.join(__dirname, "..", "preload", "preload.js")
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
  try {
    await launchApp();
  } catch (error) {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    errorWindow = createErrorWindow(buildStartupErrorDetails(
      processManager?.paths || diagnostics || {},
      diagnostics || {},
      error
    ));
  }
}

const gotLock = app.requestSingleInstanceLock();
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
ipcMain.handle("njordhr:open-logs", async () => {
  if (!processManager) return "";
  return openLogsFolder(processManager.paths || diagnostics);
});

app.on("activate", () => {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  } else {
    start();
  }
});

app.on("before-quit", async () => {
  if (processManager) {
    await processManager.shutdown();
  }
});
