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
