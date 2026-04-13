const fs = require("fs");
const os = require("os");
const path = require("path");
const { EventEmitter } = require("events");
const test = require("node:test");
const assert = require("node:assert/strict");

const { ProcessManager, runtimeStateMatches } = require("../src/main/process-manager");

test("runtimeStateMatches rejects backend reuse when auth/runtime flags differ", () => {
  const expected = {
    useSupabaseDb: false,
    useDualWrite: false,
    useSupabaseReads: false,
    useLocalAgent: true,
    authMode: "local"
  };

  const actual = {
    runtime: {
      feature_flags: {
        use_supabase_db: true,
        use_dual_write: true,
        use_supabase_reads: true,
        use_local_agent: false
      },
      auth_mode: "cloud"
    }
  };

  assert.equal(runtimeStateMatches(expected, actual), false);
});

test("runtimeStateMatches accepts backend reuse when auth/runtime flags match", () => {
  const expected = {
    useSupabaseDb: false,
    useDualWrite: false,
    useSupabaseReads: false,
    useLocalAgent: true,
    authMode: "local"
  };

  const actual = {
    runtime: {
      feature_flags: {
        use_supabase_db: false,
        use_dual_write: false,
        use_supabase_reads: false,
        use_local_agent: true
      },
      auth_mode: "local"
    }
  };

  assert.equal(runtimeStateMatches(expected, actual), true);
});

function createPaths(tempRoot) {
  const repoRoot = path.join(tempRoot, "repo");
  const runtimeDir = path.join(tempRoot, "runtime");
  fs.mkdirSync(repoRoot, { recursive: true });
  fs.mkdirSync(runtimeDir, { recursive: true });
  return {
    repoRoot,
    runtimeDir,
    configPath: path.join(tempRoot, "config.ini")
  };
}

function createPorts(backendPort = 5050, agentPort = 5051) {
  return {
    backendPort,
    agentPort,
    backendUrl: `http://127.0.0.1:${backendPort}`,
    agentUrl: `http://127.0.0.1:${agentPort}`
  };
}

test("configureAgent reflects USE_SUPABASE_DB in cloud_sync_enabled", async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-agent-config-"));
  const paths = createPaths(tempRoot);
  const ports = createPorts(6200, 6201);
  const calls = [];
  const manager = new ProcessManager({
    app: {},
    paths,
    ports,
    python: { command: "python3", args: [] },
    env: {
      USE_SUPABASE_DB: "false"
    },
    launchId: "launch-1",
    hooks: {
      putJson: async (url, payload) => {
        calls.push({ url, payload });
      }
    }
  });

  await manager.configureAgent();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${ports.agentUrl}/settings`);
  assert.equal(calls[0].payload.api_base_url, ports.backendUrl);
  assert.equal(calls[0].payload.cloud_sync_enabled, false);
});

test("ensureBackendStarted fails early when spawned backend exits before readiness", async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-backend-crash-"));
  const paths = createPaths(tempRoot);
  const ports = createPorts(6300, 6301);
  let waitCallCount = 0;
  const child = new EventEmitter();
  child.pid = 43210;
  child.stdout = null;
  child.stderr = null;
  child.kill = () => true;

  const manager = new ProcessManager({
    app: {},
    paths,
    ports,
    python: { command: "python3", args: [] },
    env: {
      USE_SUPABASE_DB: "false",
      USE_DUAL_WRITE: "false",
      USE_SUPABASE_READS: "false",
      USE_LOCAL_AGENT: "true",
      NJORDHR_AUTH_MODE: "local"
    },
    launchId: "launch-2",
    hooks: {
      waitForJson: async () => {
        waitCallCount += 1;
        if (waitCallCount === 1) {
          throw new Error("No reusable backend");
        }
        return new Promise(() => {});
      },
      spawn: () => {
        setImmediate(() => child.emit("exit", 1, null));
        return child;
      }
    }
  });

  await assert.rejects(
    manager.ensureBackendStarted(),
    /Backend process exited before readiness check completed/
  );

  const backendErr = fs.readFileSync(path.join(paths.runtimeDir, "backend.err"), "utf8");
  assert.match(backendErr, /Backend exited before readiness/);
});

test("ensureBackendStarted fails early when spawned backend errors before readiness", async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "njordhr-backend-error-"));
  const paths = createPaths(tempRoot);
  const ports = createPorts(6400, 6401);
  let waitCallCount = 0;
  const child = new EventEmitter();
  child.pid = 54321;
  child.stdout = null;
  child.stderr = null;
  child.kill = () => true;

  const manager = new ProcessManager({
    app: {},
    paths,
    ports,
    python: { command: "python3", args: [] },
    env: {
      USE_SUPABASE_DB: "false",
      USE_DUAL_WRITE: "false",
      USE_SUPABASE_READS: "false",
      USE_LOCAL_AGENT: "true",
      NJORDHR_AUTH_MODE: "local"
    },
    launchId: "launch-3",
    hooks: {
      waitForJson: async () => {
        waitCallCount += 1;
        if (waitCallCount === 1) {
          throw new Error("No reusable backend");
        }
        return new Promise(() => {});
      },
      spawn: () => {
        setImmediate(() => child.emit("error", new Error("spawn ENOENT")));
        return child;
      }
    }
  });

  await assert.rejects(
    manager.ensureBackendStarted(),
    /Backend process failed to launch/
  );

  const backendErr = fs.readFileSync(path.join(paths.runtimeDir, "backend.err"), "utf8");
  assert.match(backendErr, /Backend launch error: spawn ENOENT/);
});
