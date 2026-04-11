const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  bootstrapConfigFile,
  buildEnvironment,
  choosePorts,
  persistRuntimeEnvironment,
  resolvePythonCommand
} = require("../src/main/runtime-manager");

function createPaths(tempRoot) {
  const runtimeDir = path.join(tempRoot, "runtime");
  const repoRoot = path.join(tempRoot, "repo");
  fs.mkdirSync(repoRoot, { recursive: true });
  fs.mkdirSync(runtimeDir, { recursive: true });
  return {
    repoRoot,
    runtimeDir,
    configPath: path.join(tempRoot, "config.ini")
  };
}

test("persistRuntimeEnvironment writes clean compatibility keys and ports", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-runtime-"));
  const paths = createPaths(tempRoot);
  const ports = {
    backendPort: 5050,
    agentPort: 5051,
    backendUrl: "http://127.0.0.1:5050",
    agentUrl: "http://127.0.0.1:5051"
  };
  const env = {
    USE_SUPABASE_DB: "false",
    SUPABASE_SECRET_KEY: "secret-value",
    NJORDHR_AUTH_MODE: "local"
  };

  const runtimeEnvPath = persistRuntimeEnvironment(paths, ports, env);
  const content = fs.readFileSync(runtimeEnvPath, "utf8");

  assert.equal(runtimeEnvPath, path.join(paths.runtimeDir, "runtime.env"));
  assert.match(content, /^USE_SUPABASE_DB='false'$/m);
  assert.match(content, /^SUPABASE_SECRET_KEY='secret-value'$/m);
  assert.match(content, /^NJORDHR_AUTH_MODE='local'$/m);
  assert.match(content, /^NJORDHR_BACKEND_PORT='5050'$/m);
  assert.match(content, /^NJORDHR_AGENT_RUNTIME_PORT='5051'$/m);
  assert.match(content, /^NJORDHR_SERVER_URL='http:\/\/127\.0\.0\.1:5050'$/m);
  assert.match(content, /^NJORDHR_CONFIG_PATH='.*config\.ini'$/m);
  assert.doesNotMatch(content, /^export /m);
});

test("bootstrapConfigFile creates first-run config.ini from the template with runtime paths", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-config-"));
  const paths = createPaths(tempRoot);
  paths.downloadDir = path.join(tempRoot, "Downloaded_Resumes");
  paths.verifiedDir = path.join(tempRoot, "Verified_Resumes");
  paths.logsDir = path.join(tempRoot, "logs");
  fs.mkdirSync(paths.downloadDir, { recursive: true });
  fs.mkdirSync(paths.verifiedDir, { recursive: true });
  fs.mkdirSync(paths.logsDir, { recursive: true });
  fs.copyFileSync(
    path.join(process.cwd(), "..", "config.example.ini"),
    path.join(paths.repoRoot, "config.example.ini")
  );

  bootstrapConfigFile(paths, {});
  const content = fs.readFileSync(paths.configPath, "utf8");

  assert.match(content, /^\[Credentials\]$/m);
  assert.match(content, /^Default_Download_Folder = .*Downloaded_Resumes$/m);
  assert.match(content, /^Additional_Local_Folder = .*Verified_Resumes$/m);
  assert.match(content, /^admin_password =$/m);
  assert.match(content, /^admin_token = your-admin-token$/m);
  assert.match(content, /^log_dir = .*\/logs$/m);
  assert.match(content, /^registry_db_path = .*\/runtime\/registry\.db$/m);
  assert.match(content, /^feedback_db_path = .*\/runtime\/feedback\.db$/m);
});

test("resolvePythonCommand prefers the repo virtualenv in dev mode when present", () => {
  const app = {
    isPackaged: false
  };

  const python = resolvePythonCommand(app);

  if (process.platform === "win32") {
    assert.match(python.command, /(\.venv\\Scripts\\python\.exe|^py$)/);
  } else {
    assert.match(python.command, /(\.venv\/bin\/python3|\.venv\/bin\/python|^python3$)/);
  }
});

test("resolvePythonCommand uses packaged runtime path when app is packaged", () => {
  const originalResourcesPath = process.resourcesPath;
  Object.defineProperty(process, "resourcesPath", {
    value: path.join("/tmp", "njordhr-resources"),
    configurable: true
  });

  try {
    const python = resolvePythonCommand({ isPackaged: true });
    if (process.platform === "win32") {
      assert.equal(python.command, path.join(process.resourcesPath, "python", "python.exe"));
    } else {
      assert.equal(python.command, path.join(process.resourcesPath, "python", "bin", "python3"));
    }
  } finally {
    Object.defineProperty(process, "resourcesPath", {
      value: originalResourcesPath,
      configurable: true
    });
  }
});

test("buildEnvironment disables Supabase mode when credentials are incomplete", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-env-"));
  const paths = createPaths(tempRoot);
  const ports = {
    backendPort: 6200,
    agentPort: 6201,
    backendUrl: "http://127.0.0.1:6200",
    agentUrl: "http://127.0.0.1:6201"
  };

  const original = {
    USE_SUPABASE_DB: process.env.USE_SUPABASE_DB,
    USE_SUPABASE_READS: process.env.USE_SUPABASE_READS,
    USE_DUAL_WRITE: process.env.USE_DUAL_WRITE,
    SUPABASE_URL: process.env.SUPABASE_URL,
    SUPABASE_SECRET_KEY: process.env.SUPABASE_SECRET_KEY,
    SUPABASE_SERVICE_ROLE_KEY: process.env.SUPABASE_SERVICE_ROLE_KEY
  };

  process.env.USE_SUPABASE_DB = "true";
  process.env.USE_SUPABASE_READS = "true";
  process.env.USE_DUAL_WRITE = "true";
  delete process.env.SUPABASE_URL;
  delete process.env.SUPABASE_SECRET_KEY;
  delete process.env.SUPABASE_SERVICE_ROLE_KEY;

  try {
    const env = buildEnvironment(paths, ports);
    assert.equal(env.USE_SUPABASE_DB, "false");
    assert.equal(env.USE_SUPABASE_READS, "false");
    assert.equal(env.USE_DUAL_WRITE, "false");
    assert.equal(env.USE_LOCAL_AGENT, "true");
    assert.equal(env.NJORDHR_AUTH_MODE, "local");
  } finally {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
});

test("choosePorts reuses the persisted backend when runtime identity matches", async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-reuse-"));
  const paths = createPaths(tempRoot);
  const backendPort = 6000;
  const agentPort = 6001;
  const env = {};

  persistRuntimeEnvironment(paths, {
    backendPort,
    agentPort,
    backendUrl: `http://127.0.0.1:${backendPort}`,
    agentUrl: `http://127.0.0.1:${agentPort}`
  }, env);

  const selected = await choosePorts(paths, {
    backendPortStart: 6000,
    backendPortEnd: 6010,
    agentPortStart: 6011,
    agentPortEnd: 6020,
    isPortFree: async () => true,
    fetchRuntimeReady: async () => ({
      success: true,
      backend_ready: true,
      process_identity: {
        project_dir: paths.repoRoot,
        config_path: paths.configPath,
        runtime_dir: paths.runtimeDir
      },
      ports: {
        backend_port: backendPort,
        agent_port: agentPort
      }
    })
  });
  assert.equal(selected.backendPort, backendPort);
  assert.equal(selected.agentPort, agentPort);
  assert.equal(selected.browserUrl, `http://localhost:${backendPort}`);
});

test("choosePorts avoids a persisted backend port when the identity does not match", async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-electron-mismatch-"));
  const paths = createPaths(tempRoot);
  const backendPort = 6100;
  const agentPort = 6101;

  persistRuntimeEnvironment(paths, {
    backendPort,
    agentPort,
    backendUrl: `http://127.0.0.1:${backendPort}`,
    agentUrl: `http://127.0.0.1:${agentPort}`
  }, {});

  const selected = await choosePorts(paths, {
    backendPortStart: 6100,
    backendPortEnd: 6110,
    agentPortStart: 6111,
    agentPortEnd: 6120,
    isPortFree: async (port) => port !== backendPort,
    fetchRuntimeReady: async () => ({
      success: true,
      backend_ready: true,
      process_identity: {
        project_dir: "/other/project",
        config_path: "/other/config.ini",
        runtime_dir: "/other/runtime"
      },
      ports: {
        backend_port: backendPort,
        agent_port: agentPort
      }
    })
  });
  assert.notEqual(selected.backendPort, backendPort);
});
