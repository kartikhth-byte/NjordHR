const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadAiSearchStateHelpers() {
  const projectRoot = path.resolve(__dirname, "..", "..");
  const html = fs.readFileSync(path.join(projectRoot, "frontend.html"), "utf8");
  const match = html.match(/window\.NjordAiSearchState = \(\(\) => \{[\s\S]*?\n\s*\}\)\(\);/);
  assert.ok(match, "frontend.html should define window.NjordAiSearchState");
  const context = { window: {} };
  vm.runInNewContext(match[0], context, { filename: "frontend.html" });
  return context.window.NjordAiSearchState;
}

const helpers = loadAiSearchStateHelpers();

function results(searchSessionId, verifiedCount = 1, refinement = {}) {
  return {
    search_session: { search_session_id: searchSessionId },
    verified_matches: Array.from({ length: verifiedCount }, (_, index) => ({
      filename: `candidate-${index}.pdf`,
    })),
    refinement: {
      available: verifiedCount > 0,
      candidate_scope_member_count: verifiedCount,
      message: "Search scope saved. Previous verified matches can be refined.",
      ...refinement,
    },
  };
}

test("historical search steps display their own prompt and disable refinement expansion", () => {
  const chain = [
    { prompt: "has a valid passport", results: results("search-1", 3) },
    { prompt: "has tanker experience", results: results("search-2", 2) },
    { prompt: "has basic coc", results: results("search-3", 1) },
  ];

  assert.equal(helpers.isViewingHistoricalSearchStep(chain, 1), true);
  assert.equal(
    helpers.displayedAiPrompt({
      searchChain: chain,
      activeSearchStepIndex: 1,
      aiPrompt: "has basic coc",
    }),
    "has tanker experience",
  );

  const view = helpers.deriveSearchStepView(1, chain);
  assert.equal(view.refinementState, "viewing_history");
  assert.equal(view.refinementMode, true);
  assert.equal(view.refinementAvailability.available, false);
  assert.equal(view.refinementAvailability.parentSearchSessionId, "");
  assert.equal(view.refinementAvailability.reason, helpers.HISTORY_MESSAGE);
});

test("removing the latest refinement restores the previous step and prompt", () => {
  const chain = [
    { prompt: "has a valid passport", results: results("search-1", 4) },
    { prompt: "has tanker experience", results: results("search-2", 2) },
    { prompt: "has basic coc", results: results("search-3", 1) },
  ];

  const removal = helpers.removeLatestRefinement(chain);

  assert.equal(removal.canRemove, true);
  assert.equal(removal.chain.length, 2);
  assert.equal(removal.prompt, "has tanker experience");
  assert.equal(removal.view.boundedIndex, 1);
  assert.equal(removal.view.refinementAvailability.parentSearchSessionId, "search-2");
  assert.equal(removal.view.refinementState, "active_idle");
});

test("zero-result refinement remains in chain but can reveal a removably latest step", () => {
  const chain = [
    { prompt: "has a valid passport", results: results("search-1", 4) },
    {
      prompt: "has rare tanker experience",
      results: results("search-2", 0, {
        available: false,
        candidate_scope_member_count: 0,
        message: "No verified matches are available to refine.",
      }),
    },
  ];

  const latestView = helpers.deriveSearchStepView(1, chain);
  const removal = helpers.removeLatestRefinement(chain);

  assert.equal(latestView.refinementState, "active_zero_result");
  assert.equal(latestView.refinementAvailability.available, false);
  assert.equal(removal.canRemove, true);
  assert.equal(removal.prompt, "has a valid passport");
  assert.equal(removal.view.refinementAvailability.parentSearchSessionId, "search-1");
});

test("filter changes invalidate displayed root results but not active refinement mode", () => {
  assert.equal(
    helpers.shouldInvalidateRefinementForFilterChange({
      analysisResults: results("search-1", 2),
      refinementMode: false,
    }),
    true,
  );
  assert.equal(
    helpers.shouldInvalidateRefinementForFilterChange({
      analysisResults: results("search-1", 2),
      refinementMode: true,
    }),
    false,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(helpers.invalidatedRefinementAvailability())), {
    available: false,
    candidateCount: 0,
    reason: helpers.FILTER_INVALIDATED_MESSAGE,
    parentSearchSessionId: "",
  });
});

test("recovery restore clamps active index and preflights the latest refinable scope", () => {
  const state = {
    prompt: "current typed prompt",
    selected_rank_folder: "Chief_Engineer",
    applied_ship_type: "Bulk Carrier",
    experienced_ship_type: "Tanker",
    vessel_tonnage_filter: {
      type: "vessel_tonnage",
      min_value: 50000,
      max_value: 80000,
      unit: "gt_grt",
    },
    age_filter: {
      type: "age_range",
      minimum_years: 30,
      maximum_years: 50,
    },
    refinement_state: "active_running",
    active_search_step_index: 99,
    search_chain: [
      { prompt: "has a valid passport", results: results("search-1", 4) },
      { prompt: "has tanker experience", results: results("search-2", 2) },
    ],
    refinement_availability: {
      available: true,
      candidateCount: 2,
      reason: "",
      parentSearchSessionId: "",
    },
  };

  const restored = helpers.deriveRecoveredSearchState(state);

  assert.equal(restored.restoredIndex, 1);
  assert.equal(restored.restoredResults.search_session.search_session_id, "search-2");
  assert.equal(restored.refinementState, "active_idle");
  assert.equal(restored.refinementMode, true);
  assert.equal(restored.parentSearchSessionIdForPreflight, "search-2");
  assert.deepEqual(restored.vesselTonnageFilter, {
    type: "vessel_tonnage",
    min_value: 50000,
    max_value: 80000,
    unit: "gt_grt",
  });
  assert.deepEqual(restored.ageFilter, {
    type: "age_range",
    minimum_years: 30,
    maximum_years: 50,
  });
});

test("recovery restore preserves historical view instead of making it refinable", () => {
  const state = {
    refinement_state: "active_idle",
    active_search_step_index: 0,
    search_chain: [
      { prompt: "has a valid passport", results: results("search-1", 4) },
      { prompt: "has tanker experience", results: results("search-2", 2) },
    ],
    refinement_availability: {
      available: true,
      candidateCount: 2,
      reason: "",
      parentSearchSessionId: "search-2",
    },
  };

  const restored = helpers.deriveRecoveredSearchState(state);

  assert.equal(restored.restoredIndex, 0);
  assert.equal(restored.restoredResults.search_session.search_session_id, "search-1");
  assert.equal(restored.refinementState, "viewing_history");
  assert.equal(restored.refinementMode, true);
  assert.equal(restored.parentSearchSessionIdForPreflight, "search-2");
});

test("stream failure state explicitly preserves completed results", () => {
  const failure = helpers.streamFailureRecoveryState({ isRefinement: true });

  assert.equal(failure.preserveCompletedResults, true);
  assert.equal(failure.refinementState, "active_idle");
  assert.equal(failure.serviceRecovery.state, "backend_unreachable");
});

test("root stream failure restores the previous completed search state", () => {
  const chain = [
    { prompt: "has a valid passport", results: results("search-1", 4) },
    { prompt: "has tanker experience", results: results("search-2", 2) },
  ];
  const previousAvailability = {
    available: true,
    candidateCount: 2,
    reason: "",
    parentSearchSessionId: "search-2",
  };

  const snapshot = helpers.completedSearchSnapshot({
    prompt: "new root prompt that fails",
    analysisResults: chain[1].results,
    searchChain: chain,
    activeSearchStepIndex: 1,
    refinementMode: true,
    refinementState: "active_idle",
    refinementAvailability: previousAvailability,
  });
  const failure = helpers.streamFailureRecoveryState({
    isRefinement: false,
    completedSearchSnapshot: snapshot,
  });
  const restore = JSON.parse(JSON.stringify(failure.restoreCompletedResults));

  assert.equal(failure.preserveCompletedResults, true);
  assert.equal(failure.refinementState, "active_idle");
  assert.equal(restore.prompt, "has tanker experience");
  assert.equal(restore.analysisResults.search_session.search_session_id, "search-2");
  assert.equal(restore.searchChain.length, 2);
  assert.equal(restore.activeSearchStepIndex, 1);
  assert.equal(restore.refinementMode, true);
  assert.deepEqual(restore.refinementAvailability, previousAvailability);
});

test("present-rank index status formatter exposes aggregate recruiter text only", () => {
  const formatted = helpers.formatPresentRankIndexStatus({
    version: 7,
    built_at: "2026-06-26T03:08:45+00:00",
    row_count: 12,
    indexed_count: 9,
    unindexed_count: 3,
    rank_counts: {
      chief_officer: 5,
      second_engineer: 4,
    },
    resume_path: "/Users/example/Chief_Officer/a.pdf",
  });

  assert.equal(formatted.version, 7);
  assert.equal(formatted.rowCount, 12);
  assert.equal(formatted.indexedCount, 9);
  assert.equal(formatted.unindexedCount, 3);
  assert.equal(formatted.rankGroupCount, 2);
  assert.match(formatted.summary, /9 indexed current-rank rows/);
  assert.match(formatted.detail, /12 current facts rows/);
  assert.match(formatted.detail, /3 rows needing rank review/);
  assert.match(formatted.detail, /2 present-rank groups/);
  assert.doesNotMatch(`${formatted.summary}\n${formatted.detail}`, /\/Users|Chief_Officer\/a\.pdf/);
});

test("present-rank index status formatter handles missing initial status", () => {
  const formatted = helpers.formatPresentRankIndexStatus(null);

  assert.equal(formatted.version, 0);
  assert.equal(formatted.rowCount, 0);
  assert.equal(formatted.rankGroupCount, 0);
  assert.equal(formatted.summary, "Present-rank index awaiting first refresh");
  assert.equal(formatted.builtLabel, "Awaiting first refresh");
});
