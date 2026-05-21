const test = require("node:test");
const assert = require("node:assert/strict");

const { APP_DIRS, APP_FILES } = require("../scripts/stage-packaged-runtime");

test("packaged runtime includes root helpers imported by backend and agent code", () => {
  assert.equal(APP_FILES.includes("runtime_env.py"), true);
  assert.equal(APP_FILES.includes("rank_folders.py"), true);
});

test("packaged runtime includes local frontend vendor assets", () => {
  assert.equal(APP_DIRS.includes("web_vendor"), true);
});
