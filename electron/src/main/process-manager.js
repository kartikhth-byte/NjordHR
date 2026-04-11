const fs = require("fs");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");

const READY_TIMEOUT_MS = 30000;
const AGENT_SETTINGS_TIMEOUT_MS = 30000;

function waitForJson(url, timeoutMs = READY_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();

    const poll = () => {
      const req = http.get(url, (res) => {
        let body = "";
        res.on("data", (chunk) => {
          body += chunk.toString("utf8");
        });
        res.on("end", () => {
          if (res.statusCode !== 200) {
            if (Date.now() - startedAt >= timeoutMs) {
              reject(new Error(`Timed out waiting for ${url} (status ${res.statusCode})`));
            } else {
              setTimeout(poll, 500);
            }
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(error);
          }
        });
      });

      req.on("error", () => {
        if (Date.now() - startedAt >= timeoutMs) {
          reject(new Error(`Timed out waiting for ${url}`));
        } else {
          setTimeout(poll, 500);
        }
      });
      req.setTimeout(2000, () => req.destroy());
    };

    poll();
  });
}

function putJson(url, payload, timeoutMs = AGENT_SETTINGS_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(payload), "utf8");
    const target = new URL(url);
    const req = http.request(
      {
        method: "PUT",
        hostname: target.hostname,
        port: target.port,
        path: target.pathname,
        timeout: timeoutMs,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": String(body.length)
        }
      },
      (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
          resolve();
        } else {
          reject(new Error(`PUT ${url} failed with status ${res.statusCode}`));
        }
      }
    );
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

function createManagedProcess(child, stdoutPath, stderrPath) {
  if (child.stdout && stdoutPath) {
    child.stdout.pipe(fs.createWriteStream(stdoutPath, { flags: "a" }));
  }
  if (child.stderr && stderrPath) {
    child.stderr.pipe(fs.createWriteStream(stderrPath, { flags: "a" }));
  }
  return child;
}

function appendLaunchMarker(filePath, launchId, label) {
  const stamp = new Date().toISOString();
  fs.appendFileSync(
    filePath,
    `\n[${stamp}] [${launchId}] ${label}\n`,
    "utf8"
  );
}

function writePidFile(pidPath, child) {
  if (!child || !child.pid) {
    return;
  }
  fs.writeFileSync(pidPath, `${child.pid}\n`, "utf8");
}

function identityMatches(expected, actual) {
  if (!actual || !actual.process_identity) {
    return false;
  }
  const identity = actual.process_identity;
  return (
    identity.project_dir === expected.projectDir &&
    identity.config_path === expected.configPath &&
    identity.runtime_dir === expected.runtimeDir
  );
}

function runtimeStateMatches(expected, actual) {
  const runtime = actual && actual.runtime;
  const flags = runtime && runtime.feature_flags;
  if (!runtime || !flags) {
    return false;
  }

  return (
    Boolean(flags.use_supabase_db) === Boolean(expected.useSupabaseDb) &&
    Boolean(flags.use_dual_write) === Boolean(expected.useDualWrite) &&
    Boolean(flags.use_supabase_reads) === Boolean(expected.useSupabaseReads) &&
    Boolean(flags.use_local_agent) === Boolean(expected.useLocalAgent) &&
    String(runtime.auth_mode || "") === String(expected.authMode || "")
  );
}

class ProcessManager {
  constructor({ app, paths, ports, python, env, launchId, hooks = {} }) {
    this.app = app;
    this.paths = paths;
    this.ports = ports;
    this.python = python;
    this.env = env;
    this.launchId = launchId;
    this.backendProcess = null;
    this.agentProcess = null;
    this.waitForJson = hooks.waitForJson || waitForJson;
    this.putJson = hooks.putJson || putJson;
    this.spawnProcess = hooks.spawn || spawn;
  }

  async ensureBackendStarted() {
    const readyUrl = `${this.ports.backendUrl}/runtime/ready`;
    try {
      const runtime = await this.waitForJson(readyUrl, 1500);
      if (identityMatches(
        {
          projectDir: this.paths.repoRoot,
          configPath: this.paths.configPath,
          runtimeDir: this.paths.runtimeDir
        },
        runtime
      ) && runtimeStateMatches({
        useSupabaseDb: String(this.env.USE_SUPABASE_DB || "").trim().toLowerCase() === "true",
        useDualWrite: String(this.env.USE_DUAL_WRITE || "").trim().toLowerCase() === "true",
        useSupabaseReads: String(this.env.USE_SUPABASE_READS || "").trim().toLowerCase() === "true",
        useLocalAgent: String(this.env.USE_LOCAL_AGENT || "").trim().toLowerCase() === "true",
        authMode: String(this.env.NJORDHR_AUTH_MODE || "").trim().toLowerCase() || "auto"
      }, runtime)) {
        return runtime;
      }
    } catch (_error) {
      // Fall through to local process start.
    }

    const backendOut = path.join(this.paths.runtimeDir, "backend.out");
    const backendErr = path.join(this.paths.runtimeDir, "backend.err");
    appendLaunchMarker(backendOut, this.launchId, `Launching backend on ${this.ports.backendUrl}`);
    appendLaunchMarker(backendErr, this.launchId, `Launching backend on ${this.ports.backendUrl}`);
    const child = this.spawnProcess(
      this.python.command,
      [...this.python.args, "backend_server.py"],
      {
        cwd: this.paths.repoRoot,
        env: this.env,
        stdio: ["ignore", "pipe", "pipe"]
      }
    );
    this.backendProcess = createManagedProcess(child, backendOut, backendErr);
    writePidFile(path.join(this.paths.runtimeDir, "backend.pid"), child);
    const readyPromise = this.waitForJson(readyUrl, READY_TIMEOUT_MS);
    const crashPromise = new Promise((_, reject) => {
      child.once("exit", (code, signal) => {
        if (code === 0 || signal === "SIGTERM") {
          return;
        }
        reject(new Error(`Backend process exited before readiness check completed. Check ${backendErr} for details.`));
      });
    });
    return Promise.race([readyPromise, crashPromise]);
  }

  ensureAgentStarted() {
    const healthUrl = `${this.ports.agentUrl}/health`;
    this.waitForJson(healthUrl, 1500)
      .then(() => this.configureAgent())
      .catch(() => {
        const agentOut = path.join(this.paths.runtimeDir, "agent.out");
        const agentErr = path.join(this.paths.runtimeDir, "agent.err");
        appendLaunchMarker(agentOut, this.launchId, `Launching agent on ${this.ports.agentUrl}`);
        appendLaunchMarker(agentErr, this.launchId, `Launching agent on ${this.ports.agentUrl}`);
        const child = this.spawnProcess(
          this.python.command,
          [...this.python.args, "agent_server.py"],
          {
            cwd: this.paths.repoRoot,
            env: this.env,
            stdio: ["ignore", "pipe", "pipe"]
          }
        );
        this.agentProcess = createManagedProcess(child, agentOut, agentErr);
        writePidFile(path.join(this.paths.runtimeDir, "agent.pid"), child);
        this.waitForJson(healthUrl, AGENT_SETTINGS_TIMEOUT_MS)
          .then(() => this.configureAgent())
          .catch(() => {
            // Best effort in E0; the renderer banner will surface delayed agent state.
          });
      });
  }

  async configureAgent() {
    await this.putJson(`${this.ports.agentUrl}/settings`, {
      api_base_url: this.ports.backendUrl,
      cloud_sync_enabled: String(this.env.USE_SUPABASE_DB || "").trim().toLowerCase() === "true"
    });
  }

  async shutdown() {
    const children = [this.agentProcess, this.backendProcess].filter(Boolean);
    for (const child of children) {
      await stopChildGracefully(child);
    }
    for (const pidFile of ["agent.pid", "backend.pid"]) {
      const pidPath = path.join(this.paths.runtimeDir, pidFile);
      if (fs.existsSync(pidPath)) {
        fs.unlinkSync(pidPath);
      }
    }
  }
}

function stopChildGracefully(child, timeoutMs = 3000) {
  if (!child || typeof child.kill !== "function" || child.killed) {
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };

    child.once("exit", finish);

    try {
      child.kill("SIGTERM");
    } catch (_error) {
      finish();
      return;
    }

    setTimeout(() => {
      if (settled) {
        return;
      }
      try {
        child.kill("SIGKILL");
      } catch (_error) {
        // best effort
      }
      finish();
    }, timeoutMs);
  });
}

module.exports = {
  ProcessManager,
  waitForJson,
  runtimeStateMatches,
  stopChildGracefully
};
