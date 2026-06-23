const { test, expect } = require("@playwright/test");
const fs = require("node:fs");
const path = require("node:path");

function loadReasonFormattingScript() {
  const projectRoot = path.resolve(__dirname, "..", "..");
  const html = fs.readFileSync(path.join(projectRoot, "frontend.html"), "utf8");
  const match = html.match(/window\.NjordReasonFormatting = \(\(\) => \{[\s\S]*?\n\s*\}\)\(\);/);
  if (!match) {
    throw new Error("frontend.html should define window.NjordReasonFormatting");
  }
  return match[0];
}

test("reason formatting browser smoke renders structured sections", async ({ page }) => {
  const reasonFormattingScript = loadReasonFormattingScript();

  await page.setContent(`
    <!DOCTYPE html>
    <html lang="en">
      <body>
        <div id="app"></div>
      </body>
    </html>
  `);

  await page.addScriptTag({ content: reasonFormattingScript });
  await page.addScriptTag({
    content: `
      const sampleMatch = {
        reason: [
          "Passport is valid until 2033-08-11 for requested filter 'valid passport'.",
          "Candidate vessel tonnage evidence includes 37000 unspecified, matching the requested range.",
          "Candidate has experienced ship type matching 'container'."
        ].join("; "),
        hard_filter_reasons: [
          {
            reason_code: "PASSPORT_VALID",
            message: "Passport is valid until 2033-08-11 for requested filter 'valid passport'.",
            actual_value: { expiry_date: "2033-08-11", months_remaining: 86 }
          },
          {
            reason_code: "VESSEL_TONNAGE_MATCH",
            message: "Candidate vessel tonnage evidence includes 37000 unspecified, matching the requested range.",
            actual_value: {
              matched_evidence: [{ value: 37000, unit: "unspecified" }]
            }
          },
          {
            reason_code: "EXPERIENCE_SHIP_TYPE_MATCH",
            message: "Candidate has experienced ship type matching 'container'."
          }
        ],
        default_insights: {
          best_vessel_tonnage_value: 83000,
          best_vessel_tonnage_unit: "unspecified",
          current_rank_months_total: 86,
          has_contract_gap_over_6_months: true,
          max_contract_gap_days: 360,
          max_contract_gap_start: "2022-09-12",
          max_contract_gap_end: "2023-08-23"
        },
        experienced_ship_types: ["bulk carrier", "container"]
      };

      const model = window.NjordReasonFormatting.buildReasonDisplayModel(sampleMatch);
      const meta = window.NjordReasonFormatting.REASON_GROUP_META;
      const app = document.getElementById("app");

      const renderGroup = (key, lines) => {
        if (!Array.isArray(lines) || !lines.length) return "";
        const heading = meta[key] && meta[key].heading
          ? '<h2 data-group-heading="' + key + '">' + meta[key].heading + '</h2>'
          : "";
        const items = lines.map((line) => '<li>' + line + '</li>').join("");
        return '<section data-group="' + key + '">' + heading + '<ul>' + items + '</ul></section>';
      };

      app.innerHTML = [
        renderGroup("primary", model.primary),
        renderGroup("matchedFilters", model.matchedFilters),
        renderGroup("recruiterChecks", model.recruiterChecks),
        renderGroup("context", model.context)
      ].join("");
    `,
  });

  await expect(page.locator('[data-group-heading="matchedFilters"]')).toHaveText("Matched Filters");
  await expect(page.locator('[data-group="matchedFilters"] li')).toHaveText([
    "Passport valid until 2033-08-11.",
    "Tonnage matches requested range: 37,000 (unit unspecified).",
    "Experienced ship type matches 'container'.",
  ]);

  await expect(page.locator('[data-group-heading="recruiterChecks"]')).toHaveText("Default Recruiter Checks");
  await expect(page.locator('[data-group="recruiterChecks"] li')).toHaveText([
    "Months in current rank: 86",
    "Gap > 6 months: Yes (12 months, 2022-09-12 to 2023-08-23)",
  ]);

  await expect(page.locator('[data-group-heading="context"]')).toHaveText("Resume Context");
  await expect(page.locator('[data-group="context"] li')).toHaveText([
    "Vessel tonnage evidence: 83,000 (unit unspecified).",
    "Experienced Ship Type: bulk carrier, container",
  ]);
});
