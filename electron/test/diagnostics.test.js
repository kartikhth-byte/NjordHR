const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buildDiagnostics,
  writeStartupDiagnostics,
  buildStartupErrorDetails
} = require("../src/main/diagnostics");

test("writeStartupDiagnostics persists launch metadata for the current run", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-diagnostics-"));
  const runtimeDir = path.join(tempRoot, "runtime");
  fs.mkdirSync(runtimeDir, { recursive: true });

  const diagnostics = buildDiagnostics(
    { runtimeDir, configPath: path.join(tempRoot, "config.ini") },
    {
      backendPort: 5052,
      agentPort: 5053,
      backendUrl: "http://127.0.0.1:5052",
      browserUrl: "http://localhost:5052",
      agentUrl: "http://127.0.0.1:5053"
    },
    {
      launchId: "launch-123",
      pythonCommand: "/tmp/python3",
      authMode: "cloud",
      useLocalAgent: true,
      useSupabaseDb: true
    }
  );

  const diagnosticsPath = writeStartupDiagnostics({ runtimeDir }, diagnostics);
  const payload = JSON.parse(fs.readFileSync(diagnosticsPath, "utf8"));

  assert.equal(payload.launchId, "launch-123");
  assert.equal(payload.backendPort, 5052);
  assert.equal(payload.agentPort, 5053);
  assert.equal(payload.authMode, "cloud");
  assert.equal(payload.useLocalAgent, true);
  assert.equal(payload.useSupabaseDb, true);
  assert.ok(payload.writtenAt);
});

test("buildStartupErrorDetails includes recent backend and agent error tails", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-error-"));
  const runtimeDir = path.join(tempRoot, "runtime");
  fs.mkdirSync(runtimeDir, { recursive: true });
  fs.writeFileSync(path.join(runtimeDir, "backend.err"), "line one\nline two\n", "utf8");
  fs.writeFileSync(path.join(runtimeDir, "agent.err"), "agent one\nagent two\n", "utf8");

  const details = buildStartupErrorDetails(
    { runtimeDir, configPath: path.join(tempRoot, "config.ini") },
    {
      launchId: "launch-456",
      backendUrl: "http://127.0.0.1:5054",
      agentUrl: "http://127.0.0.1:5055",
      runtimeDir,
      configPath: path.join(tempRoot, "config.ini"),
      pythonCommand: "/tmp/python3"
    },
    new Error("Timed out waiting for backend")
  );

  assert.equal(details.launchId, "launch-456");
  assert.equal(details.message, "Timed out waiting for backend");
  assert.match(details.backendErrTail, /line one/);
  assert.match(details.agentErrTail, /agent one/);
});
