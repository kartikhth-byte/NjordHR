#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const electronRoot = path.resolve(__dirname, "..");
const projectRoot = path.resolve(electronRoot, "..");
const scriptPath = path.join(electronRoot, "scripts", "generate-icons.py");
const requiredIcons = [
  path.join(electronRoot, "buildResources", "NjordHR.icon.png"),
  path.join(electronRoot, "buildResources", "NjordHR.icns"),
  path.join(electronRoot, "buildResources", "NjordHR.ico")
];

function commandExists(command) {
  const probe = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(probe, [command], {
    stdio: "ignore",
    shell: process.platform === "win32"
  });
  return result.status === 0;
}

function resolvePythonCommand() {
  const override = String(process.env.NJORDHR_BUILD_PYTHON_BIN || "").trim();
  if (override) {
    return { command: override, args: [] };
  }

  const candidates = process.platform === "win32"
    ? [
        { command: "py", args: ["-3.11"] },
        { command: "python", args: [] }
      ]
    : [
        { command: path.join(projectRoot, ".venv", "bin", "python3"), args: [] },
        { command: path.join(projectRoot, ".venv", "bin", "python"), args: [] },
        { command: "python3", args: [] },
        { command: "python", args: [] }
      ];

  for (const candidate of candidates) {
    if (candidate.command.includes(path.sep)) {
      if (fs.existsSync(candidate.command)) {
        return candidate;
      }
      continue;
    }
    if (commandExists(candidate.command)) {
      return candidate;
    }
  }

  throw new Error("No usable Python interpreter found for icon generation.");
}

function main() {
  if (requiredIcons.every((iconPath) => fs.existsSync(iconPath))) {
    console.log("[NjordHR] Reusing committed icon assets.");
    return;
  }

  const python = resolvePythonCommand();
  const result = spawnSync(python.command, [...python.args, scriptPath], {
    cwd: projectRoot,
    stdio: "inherit",
    env: process.env,
    shell: process.platform === "win32" && !python.command.includes(path.sep)
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

main();
