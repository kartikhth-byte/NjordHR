# Vessel Tonnage Experience v1

## Goal

Allow recruiters to filter candidates by vessel tonnage experience using
structured, auditable hard-filter evidence.

Primary use cases:

- `has experience on vessels above 50000 tonnage`
- `minimum 30000 vessel tonnage`
- `vessel tonnage between 30000 and 80000`
- `has oil tanker experience above 50000 tonnage`
- `served on vessels above 100000 dwt`

Tonnage is a hard-filter family because it is structured, deterministic, and
audit-critical. It should not be treated as semantic-only matching.

## Phase 1 Scope

Phase 1 includes:

- Extract `Tonnage` from Seajobs `Seamen Experience Details` rows.
- Store tonnage per contract / sea-service row.
- Add candidate-level summary fields for debugging and fail messages.
- Add a structured UI filter for minimum / maximum vessel tonnage.
- Add deterministic evaluator support.
- Add deterministic prompt-parser support for simple tonnage phrases.

Phase 1 does not include:

- Full email / free-form resume tonnage extraction.
- Net tonnage / NRT support.
- Same-row cross-family co-occurrence enforcement.

## Locked Decisions

### Co-Occurrence Policy

Phase 1 uses independent matching. A candidate may pass a compound search if
one contract row satisfies vessel type and another contract row satisfies vessel
tonnage. For example, `oil tanker above 50000 tonnage` may pass when the resume
contains any oil-tanker experience and any separate tonnage row above 50000.

This is a known Phase 1 limitation. Results must show matched evidence so
recruiters can review. Same-row enforcement is deferred to Phase 2 via a
generalized `same_row` logical group. See GitHub Issue #36.

### Seajobs Unit Policy

Seajobs `Seamen Experience Details.Tonnage` is stored as `unit:
"unspecified"` until the vendor convention is verified. Do not assume the
column means GT/GRT or DWT.

If a recruiter chooses a GT/GRT-specific filter, Seajobs `unspecified` tonnage
does not match unless a future verified policy changes this behavior. The UI
default should be `Any / unspecified`.

### Confidence Policy

Use these defaults for Phase 1:

- `0.90` for clean integer cells.
- `0.70` for noisy but parseable cells with exactly one numeric token.
- Ambiguous cells are skipped in Phase 1 unless they contain distinct labeled
  units that can be split into separate tonnage entries.

Email / free-form extraction is deferred and should use lower confidence
defaults when implemented.

## Candidate Facts Shape

Attach tonnage to the contract / sea-service row where it appears:

```json
{
  "fact_type": "contract",
  "canonical_value": "MT Example",
  "attributes": {
    "vessel_name": "MT Example",
    "vessel_type": "oil_tanker",
    "rank": "3rd Engineer",
    "start_date": "2022-01-01",
    "end_date": "2022-10-01",
    "vessel_tonnage": [
      {
        "value": 58000,
        "unit": "unspecified",
        "source_label": "Tonnage",
        "confidence": 0.90,
        "evidence_text": "Tonnage: 58000"
      }
    ]
  }
}
```

Use an array so a row can hold multiple labeled tonnage values, such as
`58000 GT / 105000 DWT`.

Allowed `unit` values:

- `unspecified`
- `gt`
- `grt`
- `dwt`

Recommended summary fields:

```json
{
  "experience": {
    "vessel_tonnage_values": [58000, 74000, 105000],
    "max_vessel_tonnage": 105000,
    "min_vessel_tonnage": 58000
  }
}
```

Summaries are convenience values only. The evaluator should keep contract-row
evidence available so Phase 2 can add same-row matching without a schema reset.

## Seajobs Extraction

Source:

- Section: `Seamen Experience Details`
- Column: `Tonnage`

For each row:

1. Read the `Tonnage` cell.
2. Normalize numeric forms using the algorithm below.
3. Attach extracted tonnage to the same contract row as vessel name, vessel
   type, rank, and dates.
4. Preserve the raw cell text in `evidence_text`.
5. Store `source_label: "Tonnage"` unless the cell itself contains a stronger
   label such as `GT`, `GRT`, or `DWT`.

## Numeric Parsing

### Sentinels

Treat these values as missing:

- empty string
- whitespace-only
- `-`
- `--`
- `NA`
- `N/A`
- `?`
- `null`
- `none`
- `0`

Sentinel matching is case-insensitive after trimming whitespace.

### Clean Cells

Confidence: `0.90`

Accept cells matching:

```text
^\s*\d+(?:,\d{3})*(?:\.0)?\s*$
```

Examples:

- `58000` -> `58000`, `unit: "unspecified"`
- `58,000` -> `58000`, `unit: "unspecified"`
- `58000.0` -> `58000`, `unit: "unspecified"`

### Noisy Cells

Confidence: `0.70`, unless a recognized unit label is present.

Accept cells with exactly one parseable numeric token plus alphabetic noise.

Examples:

- `58000 MT` -> `58000`, `unit: "unspecified"`, confidence `0.70`
- `Tonnage 58000` -> `58000`, `unit: "unspecified"`, confidence `0.70`
- `58,000 tons` -> `58000`, `unit: "unspecified"`, confidence `0.70`

### Labeled Unit Cells

Confidence: `0.90` when the numeric token and unit label are unambiguous.

Examples:

- `105000 DWT` -> `105000`, `unit: "dwt"`
- `58000 GT` -> `58000`, `unit: "gt"`
- `58000 GRT` -> `58000`, `unit: "grt"`

If multiple distinct labeled values appear, emit one tonnage entry per unit on
the same contract row:

- `58000 GT / 105000 DWT` -> `[(58000, "gt"), (105000, "dwt")]`

### Rejected in Phase 1

Reject these values:

- Non-whole decimals: `58000.5`
- Shorthand numbers: `58k`, `5.8M`
- Multiple unlabeled numeric tokens: `58000 60000`
- Range-like unlabeled values: `58000-60000`

Rejected values should not create tonnage evidence.

## Query Plan Shape

Add hard-filter family:

```text
vessel_tonnage
```

Constraint shape:

```json
{
  "type": "vessel_tonnage",
  "min_value": 50000,
  "max_value": null,
  "unit": "any"
}
```

Allowed query units:

- `any`
- `unspecified`
- `gt_grt`
- `dwt`

UI mapping:

```json
{
  "filter_family": "vessel_tonnage",
  "parameters": {
    "min_value": 50000,
    "max_value": null,
    "unit": "any"
  }
}
```

## Evaluator Semantics

A candidate passes if any extracted contract-row tonnage satisfies the requested
range and unit policy.

Unit matching:

| Candidate unit | `any` | `unspecified` | `gt_grt` | `dwt` |
| --- | --- | --- | --- | --- |
| `unspecified` | match | match | no match | no match |
| `gt` | match | no match | match | no match |
| `grt` | match | no match | match | no match |
| `dwt` | match | no match | no match | match |

Decision examples:

```json
{
  "decision": "PASS",
  "reason_code": "VESSEL_TONNAGE_MATCH",
  "message": "Candidate has vessel tonnage experience 58000 matching minimum 50000.",
  "actual_value": {
    "value": 58000,
    "unit": "unspecified",
    "vessel_name": "MT Example"
  },
  "expected_value": {
    "min_value": 50000,
    "max_value": null,
    "unit": "any"
  },
  "confidence": 0.90
}
```

```json
{
  "decision": "FAIL",
  "reason_code": "VESSEL_TONNAGE_BELOW_MINIMUM",
  "message": "Highest vessel tonnage found is 28000, below required minimum 50000.",
  "actual_value": {
    "max_value": 28000,
    "unit": "unspecified"
  },
  "expected_value": {
    "min_value": 50000,
    "unit": "any"
  },
  "confidence": 0.90
}
```

```json
{
  "decision": "UNKNOWN",
  "reason_code": "VESSEL_TONNAGE_NOT_FOUND",
  "message": "No vessel tonnage evidence found in resume.",
  "actual_value": null,
  "expected_value": {
    "min_value": 50000,
    "unit": "any"
  },
  "confidence": 0.0
}
```

## UI Filter

Add filter group:

```text
Experienced Vessel Tonnage
```

Fields:

- Minimum tonnage
- Maximum tonnage
- Tonnage type

Tonnage type options:

- Any / unspecified
- GT / GRT
- DWT

Default:

- Any / unspecified

Helper text:

```text
Matches candidates whose resume shows sea-service experience on vessels within this tonnage range. Seajobs "Tonnage" is treated as unspecified unless the resume explicitly labels GT/GRT or DWT. When combined with vessel type or other filters, matches may come from different sea-service rows; review the evidence shown in results.
```

Validation:

- Minimum and maximum must be positive integers.
- If both are present, minimum must be less than or equal to maximum.
- Empty fields mean no tonnage filter.

## Prompt Parser

Support simple deterministic patterns:

Minimum:

- `above 50000 tonnage`
- `over 50000 grt`
- `minimum 30000 gt`
- `at least 75000 dwt`
- `50000+ tonnage`

Maximum:

- `below 50000 tonnage`
- `under 30000 grt`
- `maximum 80000 dwt`
- `up to 60000 gt`

Range:

- `between 30000 and 80000 tonnage`
- `30000 to 80000 grt`
- `30000-80000 dwt`

Parser output examples:

```json
{
  "type": "vessel_tonnage",
  "min_value": 50000,
  "max_value": null,
  "unit": "any"
}
```

```json
{
  "type": "vessel_tonnage",
  "min_value": 50000,
  "max_value": null,
  "unit": "gt_grt"
}
```

```json
{
  "type": "vessel_tonnage",
  "min_value": 100000,
  "max_value": null,
  "unit": "dwt"
}
```

## Audit and Explanation

Matched candidate:

```text
Matched vessel tonnage: 58,000 from MT Example.
Source: Seamen Experience Details > Tonnage
```

Failed candidate:

```text
Highest vessel tonnage found: 28,000, below required minimum 50,000.
```

Unknown:

```text
No vessel tonnage evidence found in this resume.
```

When a prompt combines tonnage with another family, the explanation must show
which evidence drove each family so recruiters can spot Phase 1 cross-row
matches.

## PR A Test Fixtures

Extraction tests:

- `test_seajobs_tonnage_clean_integer`: `58000` -> `58000`, `0.90`, `unspecified`
- `test_seajobs_tonnage_comma_separated`: `58,000` -> `58000`, `0.90`, `unspecified`
- `test_seajobs_tonnage_decimal_zero`: `58000.0` -> `58000`, `0.90`, `unspecified`
- `test_seajobs_tonnage_non_whole_decimal`: `58000.5` -> missing
- `test_seajobs_tonnage_shorthand_rejected`: `58k` -> missing
- `test_seajobs_tonnage_zero_is_missing`: `0` -> missing
- `test_seajobs_tonnage_dash_sentinels`: `-`, `--` -> missing
- `test_seajobs_tonnage_na_sentinels`: `NA`, `N/A` -> missing
- `test_seajobs_tonnage_question_sentinel`: `?` -> missing
- `test_seajobs_tonnage_null_string`: `null`, `none` -> missing
- `test_seajobs_tonnage_whitespace_only`: whitespace -> missing
- `test_seajobs_tonnage_noisy_with_unit`: `58000 MT` -> `58000`, `0.70`, `unspecified`
- `test_seajobs_tonnage_label_prefix`: `Tonnage 58000` -> `58000`, `0.70`, `unspecified`
- `test_seajobs_tonnage_dwt_labeled`: `105000 DWT` -> `105000`, `0.90`, `dwt`
- `test_seajobs_tonnage_gt_labeled`: `58000 GT` -> `58000`, `0.90`, `gt`
- `test_seajobs_tonnage_grt_labeled`: `58000 GRT` -> `58000`, `0.90`, `grt`
- `test_seajobs_tonnage_multi_labeled_split`: `58000 GT / 105000 DWT` -> two entries
- `test_seajobs_tonnage_multi_unlabeled_skip`: `58000 60000` -> missing
- `test_seajobs_tonnage_attaches_to_row`: value lands on same contract as vessel name/type/rank

## Rollout Plan

### PR A: Seajobs extraction, schema, and summaries

- Parse Seajobs `Tonnage`.
- Store per contract row.
- Add candidate summary fields.
- Add extraction and schema tests.
- Commit this spec and the co-occurrence policy.

### PR B: Evaluator and prompt parser

- Add `vessel_tonnage` hard-filter family.
- Add deterministic parser support.
- Add evaluator tests.

### PR C: UI filter

- Add min/max/unit controls.
- Wire request/query-plan parameters.
- Use the helper text verbatim from this spec.

### PR D: Email / free-form extraction

- Deferred until after Phase 1 and possibly after Phase 2 `same_row`.
- Use lower confidence defaults for narrative extraction.

## Phase 2

Generalized same-row matching is tracked in GitHub Issue #36:

```text
Generalized same_row logical group for cross-family co-occurrence
```

The `same_row` group should eventually support combinations such as:

- `experience_ship_type + vessel_tonnage`
- `rank + vessel_tonnage`
- `engine_experience + vessel_tonnage`
- `experience_ship_type + engine_experience`
