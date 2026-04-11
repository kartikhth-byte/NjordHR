const fs = require("fs");
const path = require("path");
const { shell } = require("electron");

function buildDiagnostics(paths, ports, extras = {}) {
  return {
    launchId: extras.launchId || "",
    runtimeDir: paths.runtimeDir,
    configPath: paths.configPath,
    backendPort: ports.backendPort,
    agentPort: ports.agentPort,
    backendUrl: ports.backendUrl,
    browserUrl: ports.browserUrl,
    agentUrl: ports.agentUrl,
    pythonCommand: extras.pythonCommand || "",
    authMode: extras.authMode || "",
    useLocalAgent: extras.useLocalAgent ?? null,
    useSupabaseDb: extras.useSupabaseDb ?? null
  };
}

function writeStartupDiagnostics(paths, diagnostics) {
  const diagnosticsPath = path.join(paths.runtimeDir, "startup_diagnostics.json");
  fs.writeFileSync(
    diagnosticsPath,
    `${JSON.stringify({
      ...diagnostics,
      writtenAt: new Date().toISOString()
    }, null, 2)}\n`,
    "utf8"
  );
  return diagnosticsPath;
}

function tailFile(filePath, lineCount = 40) {
  if (!filePath || !fs.existsSync(filePath)) {
    return "";
  }
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  return lines.slice(-lineCount).join("\n").trim();
}

function buildStartupErrorDetails(paths, diagnostics, error) {
  const backendErrPath = path.join(paths.runtimeDir, "backend.err");
  const agentErrPath = path.join(paths.runtimeDir, "agent.err");
  return {
    message: error && error.message ? error.message : "Startup failed.",
    launchId: diagnostics.launchId || "",
    backendUrl: diagnostics.backendUrl || "",
    agentUrl: diagnostics.agentUrl || "",
    runtimeDir: diagnostics.runtimeDir || "",
    configPath: diagnostics.configPath || "",
    pythonCommand: diagnostics.pythonCommand || "",
    backendErrTail: tailFile(backendErrPath, 24),
    agentErrTail: tailFile(agentErrPath, 24)
  };
}

async function openLogsFolder(paths) {
  return shell.openPath(paths.runtimeDir);
}

module.exports = {
  buildDiagnostics,
  writeStartupDiagnostics,
  buildStartupErrorDetails,
  openLogsFolder
};
