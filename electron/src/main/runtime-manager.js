const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");

const BACKEND_PORT_START = 5050;
const BACKEND_PORT_END = 5150;
const AGENT_PORT_START = 5051;
const AGENT_PORT_END = 5151;
const RUNTIME_ENV_COMPAT_KEYS = [
  "USE_SUPABASE_DB",
  "USE_SUPABASE_READS",
  "USE_DUAL_WRITE",
  "USE_LOCAL_AGENT",
  "NJORDHR_AUTH_MODE",
  "NJORDHR_PASSWORD_HASH_METHOD",
  "SUPABASE_URL",
  "SUPABASE_SECRET_KEY",
  "SUPABASE_SERVICE_ROLE_KEY",
  "NJORDHR_DEFAULT_SUPABASE_SERVICE_ROLE_KEY"
];

function readEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  const values = {};
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex <= 0) {
      continue;
    }
    const key = trimmed.slice(0, separatorIndex).trim();
    const value = trimmed.slice(separatorIndex + 1).trim();
    values[key] = unescapeShellValue(value);
  }
  return values;
}

function resolveRepoRoot(app) {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "app");
  }
  return path.resolve(__dirname, "..", "..", "..");
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

function resolveRuntimePaths(app) {
  const userData = app.getPath("userData");
  const runtimeDir = ensureDir(path.join(userData, "runtime"));
  const configPath = path.join(userData, "config.ini");
  const verifiedDir = ensureDir(path.join(userData, "Verified_Resumes"));
  const logsDir = ensureDir(path.join(userData, "logs"));
  const downloadDir = ensureDir(
    process.platform === "win32"
      ? path.join(app.getPath("downloads"), "NjordHR", "Downloaded_Resumes")
      : path.join(app.getPath("downloads"), "NjordHR")
  );

  return {
    repoRoot: resolveRepoRoot(app),
    runtimeDir,
    configPath,
    verifiedDir,
    logsDir,
    downloadDir
  };
}

function probePort(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

function fetchJson(url, timeoutMs = 1200) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = "";
      res.on("data", (chunk) => {
        body += chunk.toString("utf8");
      });
      res.on("end", () => {
        if (res.statusCode !== 200) {
          reject(new Error(`GET ${url} failed with status ${res.statusCode}`));
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`Timed out waiting for ${url}`)));
  });
}

async function isPortFree(port) {
  return probePort(port);
}

function unescapeShellValue(raw) {
  const value = String(raw || "").trim();
  if (value.startsWith("'") && value.endsWith("'")) {
    return value.slice(1, -1).replace(/'\\''/g, "'");
  }
  return value;
}

function readRuntimeEnv(runtimeDir) {
  const runtimeEnvPath = path.join(runtimeDir, "runtime.env");
  if (!fs.existsSync(runtimeEnvPath)) {
    return {};
  }

  const values = {};
  const lines = fs.readFileSync(runtimeEnvPath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    if (!line || line.trim().startsWith("#")) {
      continue;
    }
    const separatorIndex = line.indexOf("=");
    if (separatorIndex <= 0) {
      continue;
    }
    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1);
    values[key] = unescapeShellValue(value);
  }
  return values;
}

function parsePort(rawValue) {
  const port = Number.parseInt(String(rawValue || ""), 10);
  if (!Number.isFinite(port) || port <= 0) {
    return null;
  }
  return port;
}

function expectedIdentity(paths) {
  return {
    project_dir: paths.repoRoot,
    config_path: paths.configPath,
    runtime_dir: paths.runtimeDir
  };
}

function readyIdentityMatches(paths, runtimeReady) {
  const identity = runtimeReady && runtimeReady.process_identity;
  const expected = expectedIdentity(paths);
  return Boolean(
    identity &&
    identity.project_dir === expected.project_dir &&
    identity.config_path === expected.config_path &&
    identity.runtime_dir === expected.runtime_dir
  );
}

async function pickFreePort(start, end) {
  for (let port = start; port <= end; port += 1) {
    // A successful bind means the port is currently free.
    // We close immediately and reserve it for the child launch that follows.
    // This mirrors the existing launcher behavior closely enough for E0.
    // A later E1 pass can tighten reservation guarantees if needed.
    // eslint-disable-next-line no-await-in-loop
    const free = await probePort(port);
    if (free) {
      return port;
    }
  }
  throw new Error(`No free port found in range ${start}-${end}`);
}

async function choosePort(preferredPort, start, end, excludedPorts = new Set(), isPortFreeFn = isPortFree) {
  if (preferredPort && !excludedPorts.has(preferredPort) && await isPortFreeFn(preferredPort)) {
    return preferredPort;
  }

  for (let port = start; port <= end; port += 1) {
    if (excludedPorts.has(port)) {
      continue;
    }
    // eslint-disable-next-line no-await-in-loop
    if (await isPortFreeFn(port)) {
      return port;
    }
  }

  throw new Error(`No free port found in range ${start}-${end}`);
}

async function choosePorts(paths, options = {}) {
  const backendPortStart = options.backendPortStart || BACKEND_PORT_START;
  const backendPortEnd = options.backendPortEnd || BACKEND_PORT_END;
  const agentPortStart = options.agentPortStart || AGENT_PORT_START;
  const agentPortEnd = options.agentPortEnd || AGENT_PORT_END;
  const isPortFreeFn = options.isPortFree || isPortFree;
  const fetchRuntimeReady = options.fetchRuntimeReady || ((port) => fetchJson(`http://127.0.0.1:${port}/runtime/ready`));
  const persisted = readRuntimeEnv(paths.runtimeDir);
  const persistedBackendPort = parsePort(persisted.NJORDHR_BACKEND_PORT || persisted.NJORDHR_PORT);
  const persistedAgentPort = parsePort(persisted.NJORDHR_AGENT_RUNTIME_PORT || persisted.NJORDHR_AGENT_PORT);

  if (persistedBackendPort) {
    try {
      const runtimeReady = await fetchRuntimeReady(persistedBackendPort);
      if (readyIdentityMatches(paths, runtimeReady)) {
        const runtimeAgentPort = parsePort(runtimeReady?.ports?.agent_port);
        const agentPortCandidate = runtimeAgentPort || persistedAgentPort;
        let agentPort = agentPortCandidate;

        if (!agentPort || agentPort === persistedBackendPort) {
          agentPort = await choosePort(null, agentPortStart, agentPortEnd, new Set([persistedBackendPort]), isPortFreeFn);
        }

        return {
          backendPort: persistedBackendPort,
          agentPort,
          backendUrl: `http://127.0.0.1:${persistedBackendPort}`,
          browserUrl: `http://localhost:${persistedBackendPort}`,
          agentUrl: `http://127.0.0.1:${agentPort}`
        };
      }
    } catch (_error) {
      // Persisted backend port is either free, unhealthy, or belongs to something else.
      // Fall through to free-port selection below.
    }
  }

  const backendPort = await choosePort(persistedBackendPort, backendPortStart, backendPortEnd, new Set(), isPortFreeFn);
  const agentPort = await choosePort(
    persistedAgentPort,
    agentPortStart,
    agentPortEnd,
    new Set([backendPort]),
    isPortFreeFn
  );

  return {
    backendPort,
    agentPort,
    backendUrl: `http://127.0.0.1:${backendPort}`,
    browserUrl: `http://localhost:${backendPort}`,
    agentUrl: `http://127.0.0.1:${agentPort}`
  };
}

function resolvePythonCommand(app) {
  const override = process.env.NJORDHR_ELECTRON_PYTHON;
  if (override) {
    return { command: override, args: [] };
  }

  const repoRoot = resolveRepoRoot(app);

  if (app.isPackaged) {
    if (process.platform === "win32") {
      return {
        command: path.join(process.resourcesPath, "python", "python.exe"),
        args: []
      };
    }
    if (process.platform === "darwin") {
      return {
        command: path.join(process.resourcesPath, "python", "bin", "python3"),
        args: []
      };
    }
    return {
      command: path.join(process.resourcesPath, "python", "bin", "python3"),
      args: []
    };
  }

  if (process.platform === "win32") {
    const repoVenvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    if (fs.existsSync(repoVenvPython)) {
      return { command: repoVenvPython, args: [] };
    }
    return { command: "py", args: ["-3.11"] };
  }

  const repoVenvPython3 = path.join(repoRoot, ".venv", "bin", "python3");
  if (fs.existsSync(repoVenvPython3)) {
    return { command: repoVenvPython3, args: [] };
  }
  const repoVenvPython = path.join(repoRoot, ".venv", "bin", "python");
  if (fs.existsSync(repoVenvPython)) {
    return { command: repoVenvPython, args: [] };
  }

  if (process.platform === "win32") {
    return { command: "py", args: ["-3.11"] };
  }
  return { command: "python3", args: [] };
}

function buildEnvironment(paths, ports) {
  const envDefaults = {
    ...readEnvFile(path.join(paths.repoRoot, ".env")),
    ...readEnvFile(path.join(paths.repoRoot, "default_runtime.env"))
  };
  const pythonPathEntries = [
    paths.repoRoot,
    path.join(paths.repoRoot, ".python-packages"),
    process.env.PYTHONPATH
  ].filter(Boolean);

  const env = {
    ...envDefaults,
    ...process.env,
    PYTHONPATH: pythonPathEntries.join(path.delimiter),
    NJORDHR_PORT: String(ports.backendPort),
    NJORDHR_AGENT_PORT: String(ports.agentPort),
    NJORDHR_AGENT_RUNTIME_PORT: String(ports.agentPort),
    NJORDHR_SERVER_URL: ports.backendUrl,
    NJORDHR_AGENT_URL: ports.agentUrl,
    NJORDHR_AGENT_BASE_URL: ports.agentUrl,
    NJORDHR_CONFIG_PATH: paths.configPath,
    NJORDHR_RUNTIME_DIR: paths.runtimeDir
  };

  const supabaseUrl = String(env.SUPABASE_URL || "").trim();
  const supabaseKey = String(env.SUPABASE_SECRET_KEY || env.SUPABASE_SERVICE_ROLE_KEY || "").trim();
  const wantsSupabase = String(env.USE_SUPABASE_DB || "").trim().toLowerCase() === "true";

  // E0/E1 desktop-shell rule:
  // if Electron is asked to start in Supabase mode but the required runtime
  // credentials are not present, force a safe local-mode fallback instead of
  // starting a backend that will immediately crash during repository wiring.
  // This is not the packaged production default; a provisioned desktop build
  // should still honor valid Supabase settings when they are available.
  if (wantsSupabase && (!supabaseUrl || !supabaseKey)) {
    env.USE_SUPABASE_DB = "false";
    env.USE_SUPABASE_READS = "false";
    env.USE_DUAL_WRITE = "false";
  }

  // In unprovisioned desktop-shell development runs, default to the local
  // agent and local auth so the shell can boot end-to-end without external
  // service configuration. Provisioned packaged builds may override these.
  if (!env.USE_LOCAL_AGENT) {
    env.USE_LOCAL_AGENT = "true";
  }
  if (!env.NJORDHR_AUTH_MODE) {
    env.NJORDHR_AUTH_MODE = "local";
  }

  return env;
}

function shellEscape(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function persistRuntimeEnvironment(paths, ports, env) {
  const runtimeEnvPath = path.join(paths.runtimeDir, "runtime.env");
  const lines = [];

  for (const key of RUNTIME_ENV_COMPAT_KEYS) {
    const value = env[key];
    if (value === undefined || value === null || String(value).trim() === "") {
      continue;
    }
    lines.push(`${key}=${shellEscape(value)}`);
  }

  lines.push(`NJORDHR_PORT=${shellEscape(String(ports.backendPort))}`);
  lines.push(`NJORDHR_BACKEND_PORT=${shellEscape(String(ports.backendPort))}`);
  lines.push(`NJORDHR_AGENT_PORT=${shellEscape(String(ports.agentPort))}`);
  lines.push(`NJORDHR_AGENT_RUNTIME_PORT=${shellEscape(String(ports.agentPort))}`);
  lines.push(`NJORDHR_SERVER_URL=${shellEscape(ports.backendUrl)}`);
  lines.push(`NJORDHR_AGENT_URL=${shellEscape(ports.agentUrl)}`);
  lines.push(`NJORDHR_AGENT_BASE_URL=${shellEscape(ports.agentUrl)}`);
  lines.push(`NJORDHR_CONFIG_PATH=${shellEscape(paths.configPath)}`);
  lines.push(`NJORDHR_RUNTIME_DIR=${shellEscape(paths.runtimeDir)}`);

  fs.writeFileSync(runtimeEnvPath, `${lines.join("\n")}\n`, "utf8");
  return runtimeEnvPath;
}

module.exports = {
  resolveRuntimePaths,
  choosePorts,
  resolvePythonCommand,
  buildEnvironment,
  persistRuntimeEnvironment,
  readRuntimeEnv
};
