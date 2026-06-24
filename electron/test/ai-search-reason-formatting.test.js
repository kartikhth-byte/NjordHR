const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadReasonFormattingHelpers() {
  const projectRoot = path.resolve(__dirname, "..", "..");
  const html = fs.readFileSync(path.join(projectRoot, "frontend.html"), "utf8");
  const match = html.match(/window\.NjordReasonFormatting = \(\(\) => \{[\s\S]*?\n\s*\}\)\(\);/);
  assert.ok(match, "frontend.html should define window.NjordReasonFormatting");
  const context = { window: {} };
  vm.runInNewContext(match[0], context, { filename: "frontend.html" });
  return context.window.NjordReasonFormatting;
}

const helpers = loadReasonFormattingHelpers();

function flattenedReasonLines(match) {
  const formatted = helpers.buildReasonDisplayModel(match);
  return [
    ...formatted.primary,
    ...formatted.matchedFilters,
    ...formatted.recruiterChecks,
    ...formatted.context,
  ];
}

function assertNoCanonicalIds(lines) {
  const text = Array.isArray(lines) ? lines.join("\n") : String(lines || "");
  assert.doesNotMatch(text, /\b[a-z]+(?:_[a-z0-9]+)+\b/);
  assert.doesNotMatch(text, /\blng carrier\b/);
}

test("reason display model groups matched filters, recruiter checks, and resume context cleanly", () => {
  const match = {
    reason: [
      "Passport is valid until 2033-08-11 for requested filter 'valid passport'.",
      "Candidate vessel tonnage evidence includes 37000 unspecified, matching the requested range.",
      "Candidate has experienced ship type matching 'container'.",
    ].join("; "),
    hard_filter_reasons: [
      {
        reason_code: "PASSPORT_VALID",
        message: "Passport is valid until 2033-08-11 for requested filter 'valid passport'.",
        actual_value: { expiry_date: "2033-08-11", months_remaining: 86 },
      },
      {
        reason_code: "VESSEL_TONNAGE_MATCH",
        message: "Candidate vessel tonnage evidence includes 37000 unspecified, matching the requested range.",
        actual_value: {
          matched_evidence: [{ value: 37000, unit: "unspecified" }],
        },
      },
      {
        reason_code: "EXPERIENCE_SHIP_TYPE_MATCH",
        message: "Candidate has experienced ship type matching 'container'.",
      },
    ],
    default_insights: {
      best_vessel_tonnage_value: 83000,
      best_vessel_tonnage_unit: "unspecified",
      current_rank_months_total: 86,
      has_contract_gap_over_6_months: true,
      max_contract_gap_days: 360,
      max_contract_gap_start: "2022-09-12",
      max_contract_gap_end: "2023-08-23",
    },
    experienced_ship_types: ["bulk carrier", "container"],
  };

  const formatted = helpers.buildReasonDisplayModel(match);

  assert.deepEqual(JSON.parse(JSON.stringify(formatted.primary)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(formatted.matchedFilters)), [
    "Passport valid until 2033-08-11.",
    "Tonnage matches requested range: 37,000 (unit unspecified).",
    "Experienced ship type matches 'container'.",
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(formatted.recruiterChecks)), [
    "Months in current rank: 86",
    "Gap > 6 months: Yes (12 months, 2022-09-12 to 2023-08-23)",
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(formatted.context)), [
    "Vessel tonnage evidence: 83,000 (unit unspecified).",
    "Experienced Ship Type: bulk carrier, container",
  ]);
});

test("reason display summary uses the same deduped structured lines across result surfaces", () => {
  const summary = helpers.buildReasonDisplaySummary({
    reason: "Passport is valid until 2033-08-11 for requested filter 'valid passport'.",
    hard_filter_reasons: [
      {
        reason_code: "PASSPORT_VALID",
        message: "Passport is valid until 2033-08-11 for requested filter 'valid passport'.",
      },
    ],
    default_insights: {
      current_rank_months_total: 24,
      has_contract_gap_over_6_months: false,
    },
  });

  assert.equal(
    summary,
    [
      "Passport valid until 2033-08-11.",
      "Months in current rank: 24",
      "Gap > 6 months: No",
    ].join("\n"),
  );
});

test("formatter humanizes unmatched hard-filter messages with reason code fallback", () => {
  const formatted = helpers.buildReasonDisplayModel({
    hard_filter_reasons: [
      {
        reason_code: "UNMAPPED_REASON_CODE",
        message: "Backend left a plain diagnostic message",
      },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(formatted.matchedFilters)), [
    "unmapped reason code: Backend left a plain diagnostic message.",
  ]);
});

test("formatter humanizes unknown review reason codes", () => {
  assert.equal(
    helpers.formatUnknownReasonType("FACTUAL_UNKNOWN"),
    "Evidence too weak for automation to decide",
  );
  assert.equal(
    helpers.formatUnknownReasonType("VERSION_MISMATCH_UNKNOWN"),
    "Resume facts need refresh before this filter can be evaluated",
  );
  assert.equal(helpers.formatUnknownReasonType("CUSTOM_UNKNOWN"), "custom unknown");
});

test("formatter rewrites engine fallback and engine+vessel messages into recruiter-facing phrasing", () => {
  const match = {
    hard_filter_reasons: [
      {
        reason_code: "ENGINE_EXPERIENCE_FAMILY_FALLBACK",
        message: "Resume mentions broader engine family MAN B&W ME, but does not confirm the requested engine filter 'MAN B&W ME-GI'. Included for recruiter review at reduced confidence.",
        actual_value: {
          matched_evidence: {
            display_label: "MAN B&W ME",
          },
        },
        expected_value: {
          engine_type: "man_b_w_me_gi",
        },
      },
      {
        reason_code: "ENGINE_EXPERIENCE_MATCH",
        message: "Candidate has MAN B&W ME-C experience matching 'MAN B&W ME'.",
        actual_value: {
          matched_months: 18,
          matched_contracts: 2,
          matched_evidence: {
            display_label: "MAN B&W ME-C",
          },
        },
        expected_value: {
          engine_type: "man_b_w_me",
        },
      },
      {
        reason_code: "ENGINE_EXPERIENCE_MISMATCH",
        message: "Candidate engine experience (Mitsubishi UEC-LSE) does not match 'MAN B&W ME-GI'.",
        actual_value: {
          aggregate_engine_types: ["mitsubishi_uec_lse"],
        },
        expected_value: {
          engine_type: "man_b_w_me_gi",
        },
      },
      {
        reason_code: "ENGINE_EXPERIENCE_NO_EVIDENCE_EXTRACTED",
        message: "Could not evaluate engine evidence for requested filter 'MAN B&W ME-GI'.",
        expected_value: {
          engine_type: "man_b_w_me_gi",
        },
      },
      {
        reason_code: "ENGINE_VESSEL_EXPERIENCE_MATCH",
        message: "Candidate has 'MAN B&W ME-GI' on 'lng carrier' in 3 contract(s).",
        actual_value: {
          matched_months: 22,
          matched_contracts: 3,
        },
      },
      {
        reason_code: "ENGINE_VESSEL_EXPERIENCE_INSUFFICIENT",
        message: "Candidate has only 4 month(s) with 'MAN B&W ME-GI' on 'lng carrier', below the required 6.",
        actual_value: {
          matched_months: 4,
          matched_contracts: 1,
        },
      },
    ],
  };
  const formatted = helpers.buildReasonDisplayModel(match);

  assert.deepEqual(JSON.parse(JSON.stringify(formatted.matchedFilters)), [
    "Broader engine family 'MAN B&W ME' was found, but 'MAN B&W ME-GI' is not confirmed. Included for recruiter review at reduced confidence.",
    "Engine experience matches 'MAN B&W ME' with 18 month(s) across 2 contract(s).",
    "Engine evidence (Mitsubishi UEC-LSE) does not match 'MAN B&W ME-GI'.",
    "Engine evidence could not be extracted for 'MAN B&W ME-GI'.",
    "Engine + vessel experience matches: 22 month(s) with 'MAN B&W ME-GI' on 'LNG carrier'.",
    "Engine + vessel experience is below the requested minimum for 'MAN B&W ME-GI' on 'LNG carrier' (4 month(s) found).",
  ]);
  assertNoCanonicalIds(flattenedReasonLines(match));
});

test("experience filter summaries use configured labels and ship display labels", () => {
  assert.equal(
    helpers.summarizeExperienceFilter({
      filter: {
        items: [
          {
            engine_family: "man_b_w_me_lgim",
            minimum_months: 12,
            years_back: 3,
          },
        ],
      },
      familyKey: "engine_family",
      configuredEngineFamilies: [
        { value: "man_b_w_me_lgim", label: "MAN B&W ME-LGIM" },
      ],
    }),
    "MAN B&W ME-LGIM 12m+ 3y",
  );

  assert.equal(
    helpers.summarizeExperienceFilter({
      filter: {
        items: [
          {
            ship_family: "lng_carrier",
            minimum_months: 12,
            contract_count: 2,
          },
        ],
      },
      familyKey: "ship_family",
    }),
    "LNG carrier 12m+ 2c",
  );
});

test("formatter rejects canonical id leaks in grouped recruiter-facing output", () => {
  const lines = flattenedReasonLines({
    hard_filter_reasons: [
      {
        reason_code: "ENGINE_EXPERIENCE_MISMATCH",
        message: "Candidate engine experience (Mitsubishi UEC-LSE) does not match 'MAN B&W ME-GI'.",
        actual_value: {
          aggregate_engine_types: ["mitsubishi_uec_lse"],
        },
        expected_value: {
          engine_type: "man_b_w_me_gi",
        },
      },
      {
        reason_code: "ENGINE_VESSEL_EXPERIENCE_MATCH",
        message: "Candidate has 'MAN B&W ME-GI' on 'lng carrier' in 3 contract(s).",
        actual_value: {
          matched_months: 22,
          matched_contracts: 3,
        },
      },
    ],
  });

  assertNoCanonicalIds(lines);
});
