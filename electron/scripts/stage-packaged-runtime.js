#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const electronRoot = path.resolve(__dirname, "..");
const projectRoot = path.resolve(electronRoot, "..");
const stageRoot = path.join(projectRoot, "build", "electron-stage");
const stageAppRoot = path.join(stageRoot, "app");
const stagePythonRoot = path.join(stageRoot, "python");
const sourceVenvRoot = path.join(projectRoot, ".venv");

const APP_FILES = [
  "backend_server.py",
  "agent_server.py",
  "ai_analyzer.py",
  "app_settings.py",
  "csv_manager.py",
  "logger_config.py",
  "resume_extractor.py",
  "scraper_engine.py",
  "frontend.html",
  "requirements.txt",
  "config.example.ini",
  "Truncated_Njord_logo.jpg"
];

const APP_DIRS = [
  "agent",
  "repositories",
  "supabase"
];

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function removeDir(dirPath) {
  fs.rmSync(dirPath, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}

function copyRecursive(sourcePath, targetPath) {
  const stat = fs.statSync(sourcePath);
  if (stat.isDirectory()) {
    ensureDir(targetPath);
    for (const entry of fs.readdirSync(sourcePath)) {
      copyRecursive(path.join(sourcePath, entry), path.join(targetPath, entry));
    }
    return;
  }
  ensureDir(path.dirname(targetPath));
  fs.copyFileSync(sourcePath, targetPath);
}

function envOrDefault(name, fallback) {
  const value = process.env[name];
  if (value === undefined || value === null || String(value).trim() === "") {
    return fallback;
  }
  return String(value);
}

function writeDefaultRuntimeEnv() {
  const supabaseUrl = envOrDefault("NJORDHR_DEFAULT_SUPABASE_URL", "");
  const supabaseSecretKey = envOrDefault("NJORDHR_DEFAULT_SUPABASE_SECRET_KEY", "");
  const supabaseServiceRoleKey = envOrDefault("NJORDHR_DEFAULT_SUPABASE_SERVICE_ROLE_KEY", "");
  const cloudProvisioned = Boolean(supabaseUrl && supabaseSecretKey);

  const defaults = {
    USE_SUPABASE_DB: envOrDefault("NJORDHR_DEFAULT_USE_SUPABASE_DB", cloudProvisioned ? "true" : "false"),
    USE_SUPABASE_READS: envOrDefault("NJORDHR_DEFAULT_USE_SUPABASE_READS", cloudProvisioned ? "true" : "false"),
    USE_DUAL_WRITE: envOrDefault("NJORDHR_DEFAULT_USE_DUAL_WRITE", "false"),
    USE_LOCAL_AGENT: envOrDefault("NJORDHR_DEFAULT_USE_LOCAL_AGENT", "true"),
    NJORDHR_AUTH_MODE: envOrDefault("NJORDHR_DEFAULT_AUTH_MODE", cloudProvisioned ? "cloud" : "local"),
    NJORDHR_PASSWORD_HASH_METHOD: envOrDefault("NJORDHR_DEFAULT_PASSWORD_HASH_METHOD", "pbkdf2:sha256:600000"),
    SUPABASE_URL: supabaseUrl,
    SUPABASE_SECRET_KEY: supabaseSecretKey,
    SUPABASE_SERVICE_ROLE_KEY: supabaseServiceRoleKey
  };

  const body = Object.entries(defaults)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  fs.writeFileSync(path.join(stageAppRoot, "default_runtime.env"), `${body}\n`, "utf8");
}

function resolveBuildPython() {
  const override = (process.env.NJORDHR_BUILD_PYTHON_BIN || "").trim();
  if (override) {
    return { command: override, args: [] };
  }

  if (process.platform === "win32") {
    return { command: "py", args: ["-3.11"] };
  }

  const candidates = [
    path.join(projectRoot, ".venv", "bin", "python3"),
    path.join(projectRoot, ".venv", "bin", "python"),
    "python3"
  ];

  for (const candidate of candidates) {
    if (candidate.includes(path.sep)) {
      if (fs.existsSync(candidate)) {
        return { command: candidate, args: [] };
      }
      continue;
    }
    return { command: candidate, args: [] };
  }

  return { command: "python3", args: [] };
}

function runOrThrow(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    cwd: options.cwd || projectRoot,
    env: options.env || process.env
  });
  if (result.status !== 0) {
    throw new Error(`Command failed: ${[command, ...args].join(" ")}`);
  }
}

function pythonExecutable(root) {
  if (process.platform === "win32") {
    return path.join(root, "Scripts", "python.exe");
  }
  return path.join(root, "bin", "python3");
}

function pythonPathExists(root) {
  return fs.existsSync(pythonExecutable(root));
}

function runPythonJson(command, args) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    env: process.env,
    encoding: "utf8"
  });
  if (result.status !== 0) {
    throw new Error(`Command failed: ${[command, ...args].join(" ")}`);
  }
  return JSON.parse(String(result.stdout || "").trim());
}

function pythonSitePaths(root) {
  const pythonBin = pythonExecutable(root);
  return runPythonJson(pythonBin, [
    "-c",
    "import json, sysconfig; print(json.dumps({'purelib': sysconfig.get_path('purelib'), 'platlib': sysconfig.get_path('platlib')}))"
  ]);
}

function copyDirectoryContents(sourceDir, targetDir) {
  if (!fs.existsSync(sourceDir)) {
    return;
  }
  ensureDir(targetDir);
  for (const entry of fs.readdirSync(sourceDir)) {
    copyRecursive(path.join(sourceDir, entry), path.join(targetDir, entry));
  }
}

function pipModuleArgs(root, extraArgs) {
  return ["-m", "pip", ...extraArgs];
}

function stageAppPayload() {
  removeDir(stageAppRoot);
  ensureDir(stageAppRoot);

  for (const relativePath of APP_FILES) {
    copyRecursive(path.join(projectRoot, relativePath), path.join(stageAppRoot, relativePath));
  }

  for (const relativePath of APP_DIRS) {
    copyRecursive(path.join(projectRoot, relativePath), path.join(stageAppRoot, relativePath));
  }

  writeDefaultRuntimeEnv();
}

function stagePythonRuntime() {
  removeDir(stagePythonRoot);

  const buildPython = resolveBuildPython();
  const venvArgs = process.platform === "win32"
    ? [...buildPython.args, "-m", "venv", stagePythonRoot]
    : [...buildPython.args, "-m", "venv", "--copies", stagePythonRoot];

  runOrThrow(buildPython.command, venvArgs);

  if (pythonPathExists(sourceVenvRoot)) {
    const sourcePaths = pythonSitePaths(sourceVenvRoot);
    const targetPaths = pythonSitePaths(stagePythonRoot);
    copyDirectoryContents(sourcePaths.purelib, targetPaths.purelib);
    if (sourcePaths.platlib !== sourcePaths.purelib) {
      copyDirectoryContents(sourcePaths.platlib, targetPaths.platlib);
    }
    return;
  }

  const pythonBin = pythonExecutable(stagePythonRoot);
  runOrThrow(
    pythonBin,
    pipModuleArgs(stagePythonRoot, ["install", "-r", path.join(stageAppRoot, "requirements.txt")])
  );
}

function writeStageManifest() {
  const payload = {
    platform: process.platform,
    generatedAt: new Date().toISOString(),
    appRoot: stageAppRoot,
    pythonRoot: stagePythonRoot
  };
  fs.writeFileSync(path.join(stageRoot, "manifest.json"), `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function main() {
  ensureDir(stageRoot);
  stageAppPayload();
  stagePythonRuntime();
  writeStageManifest();
  console.log(`[NjordHR] Electron runtime staged at ${stageRoot}`);
}

main();
