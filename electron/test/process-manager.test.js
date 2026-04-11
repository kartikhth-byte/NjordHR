const test = require("node:test");
const assert = require("node:assert/strict");

const { runtimeStateMatches } = require("../src/main/process-manager");

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
