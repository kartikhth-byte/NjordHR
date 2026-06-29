# Availability Filter - Spec v1

## Status

Active implementation spec. Locked contract for the deterministic availability filter rollout. Implementation slices follow the structured-picker discipline in `search_pickers_v1.md` and the migration discipline in `coc_country_alias_migration_v1.md`.

This spec documents existing partial availability support and defines the product-complete rollout for extraction, evaluator semantics, UI picker propagation, Needs Review behavior, and future shadow-normalizer support.

## Current baseline

NjordHR already has partial availability support:

- `AIResumeAnalyzer._extract_availability_constraint` parses prompt phrases including immediate availability, notice periods, `available from`, `available by`, `next month`, and `joinable in N days`.
- `AIResumeAnalyzer._extract_availability_fact_from_text` parses SeaJobs-style resume sections including `Availability Details` and `From date - Till date`.
- `AIResumeAnalyzer._evaluate_availability_rule` evaluates availability facts as PASS / FAIL / UNKNOWN.
- Query understanding already exposes the `availability` family.
- `shadow_llm_compound_prompt_normalizer_v1.md` lists `availability` as a future active family.

The v1 rollout completes the structured picker/filter surface without changing unrelated filter families.

## Goal

Add a deterministic recruiter-facing availability picker and hard-filter family that supports:

- available immediately
- available by date
- available from date
- available within N days
- available during a requested date window

The implementation must preserve deterministic hard-filter semantics, audit defensibility, recovery behavior, and the full structured-picker propagation contract.

## Non-goals

v1 does not implement:

- contract-length filtering
- salary-gated availability
- day-of-week availability
- travel or recovery-day offsets after sign-off
- locale-based ambiguous numeric date inference
- combined availability plus ship-type parsing outside the compound-prompt normalizer
- shadow-normalizer promotion of `availability` before deterministic evidence exists

## Canonical candidate fact

Availability extraction writes one canonical fact object:

```json
{
  "version": "v1",
  "availability_date": "YYYY-MM-DD | null",
  "availability_end_date": "YYYY-MM-DD | null",
  "extraction_state": "PARSED | MISSING | INVALID | AMBIGUOUS_NUMERIC | CONTRADICTORY | STALE",
  "availability_source_label": "availability_details | notice_period | contract_end | sign_off | unknown",
  "availability_source_text": "verbatim source text",
  "availability_extracted_on_date": "YYYY-MM-DD"
}
```

`extraction_state` is the only source of truth for extraction reliability.

`immediate` is not stored as a separate extraction status. Immediate availability is derived when:

```text
availability_date <= availability_extracted_on_date
and (
  availability_end_date is null
  or availability_extracted_on_date <= availability_end_date
)
```

No standalone `availability_confidence` field exists in v1.

`availability_extracted_on_date` is the first parse date for the source resume text. Metadata-only refreshes do not change it. Re-parsing the source resume text writes a new `availability_extracted_on_date`.

The search-side anchor is stored separately as `resolved_reference_date` in the prompt/UI constraint and search context.

## Reference date rules

Resume-derived relative values anchor to `availability_extracted_on_date`:

- `immediate`, `ASAP`, and `ready to join` resolve against `availability_extracted_on_date`.
- `notice period N days` resolves to `availability_extracted_on_date + N`.
- future `sign-off date` evidence resolves against `availability_extracted_on_date`.

Resume-derived relative anchors do not advance on metadata-only refreshes.

Search-derived relative values anchor to server search-submission time:

- UI `within N days`
- prompt `joinable within N days`
- prompt `available in N days`

The resolved search reference date must be written to search context and audit payloads.

## Staleness rule

Resume-derived relative availability is valid only when the server search-submission date is within 90 days of `availability_extracted_on_date`.

If the resume source text is re-parsed, the new `availability_extracted_on_date` resets the staleness window.

Metadata-only refreshes do not change `availability_extracted_on_date` and do not reset the staleness window.

If the fact is older than 90 days and the source text is relative availability (`immediate`, `ASAP`, `notice period`, `ready to join`), set:

```text
extraction_state = STALE
```

Absolute availability dates do not become stale solely because the resume is old.

`STALE` routes to UNKNOWN / Needs Review.

## Supported resume formats

v1 deterministic extraction supports:

- `Availability Details / From date - Till date / 30-Apr-2026 - 01-May-2026`
- `Available from 15-May-2026`
- `Available until 15-Jun-2026`
- `Available: Immediate`
- `Immediately available`
- `Ready to join`
- `Join ASAP`
- `Notice period: 30 days`
- `Can join in 45 days`
- `Contract ends 20-Aug-2026`
- `Sign-off date 20-Aug-2026`, only when context explicitly treats sign-off as future availability evidence

Sign-off dates are not adjusted by travel or recovery days in v1.

## Numeric date rule

Numeric dates where both day and month positions are in `1..12` are `AMBIGUOUS_NUMERIC`.

Numeric dates where one position is greater than `12` parse unambiguously.

No locale, country, header, or document-template heuristic resolves ambiguous numeric dates in v1.

## Multi-source precedence

When multiple availability sources are present, v1 applies this precedence:

1. `availability_details`
2. `contract_end`
3. `sign_off`
4. `notice_period`
5. `unknown`

Absolute dated sources outrank relative sources when they are compatible.

If two sources at any precedence level produce non-overlapping availability windows, set:

```text
extraction_state = CONTRADICTORY
```

and route to Needs Review.

If two same-precedence sources produce different but overlapping windows, set:

```text
extraction_state = CONTRADICTORY
```

and route to Needs Review.

Only compatible lower-precedence signals are dropped after a higher-precedence source is selected.

## Prompt and UI constraint shape

```json
{
  "version": "v1",
  "value_type": "status | by_date | from_date | relative_days | window",
  "status": "immediate | null",
  "available_by_date": "YYYY-MM-DD | null",
  "available_from_date": "YYYY-MM-DD | null",
  "available_until_date": "YYYY-MM-DD | null",
  "relative_days": 30,
  "resolved_reference_date": "YYYY-MM-DD",
  "display_value": "available by 15 May 2026"
}
```

`relative_days` accepts integers from `0` to `365`.

Fields not applicable to the active `value_type` must be null. Non-null values in inactive fields are rejected.

## Filter semantics

### Available immediately

UNKNOWN when `extraction_state != PARSED`.

PASS when the candidate availability window includes the server search-submission date:

```text
availability_date <= server_search_submission_date
and (
  availability_end_date is null
  or server_search_submission_date <= availability_end_date
)
```

FAIL when the candidate availability window does not include the server search-submission date.

### Available by DATE

UNKNOWN when `extraction_state != PARSED`.

PASS when:

```text
availability_date <= DATE
```

FAIL when:

```text
availability_date > DATE
```

`availability_end_date` is ignored for `available by DATE`.

Example:

```text
Candidate: available 2026-01-01 to 2026-03-01
Search: available by 2026-04-15
Result: PASS
```

### Available from DATE

This means the candidate is free on the requested start date.

UNKNOWN when `extraction_state != PARSED`.

PASS when:

```text
availability_date <= DATE
and (
  availability_end_date is null
  or DATE <= availability_end_date
)
```

FAIL when the candidate availability window does not include DATE.

Example:

```text
Candidate: available from 2026-01-01 with no end date
Search: available from 2026-09-01
Result: PASS
```

### Available within N days

UNKNOWN when `extraction_state != PARSED`.

Resolve:

```text
available_by_date = server_search_submission_date + N days
```

Then apply `Available by DATE` semantics.

### Available between START and END

UNKNOWN when `extraction_state != PARSED`.

PASS when the candidate availability window overlaps the requested window.

FAIL when the candidate availability window does not overlap the requested window.

## UI picker

Modes:

- `Any availability`
- `Available immediately`
- `Available by date`
- `Available from date`
- `Available within N days`
- `Available between dates`

Validation:

- date fields must be valid ISO dates
- `within N days` accepts `0..365`
- window start must be less than or equal to window end
- clearing the picker restores `Any availability`

## Needs Review

Candidates route to Needs Review when:

```text
extraction_state in {
  MISSING,
  INVALID,
  AMBIGUOUS_NUMERIC,
  CONTRADICTORY,
  STALE
}
```

Locked recruiter-facing copy:

```text
Could not determine candidate availability reliably from the resume.
```

Internal enum names do not appear in recruiter-facing output.

## Kill switch

Add:

```text
NJORDHR_AVAILABILITY_EXTRACTION_MODE=legacy|v1
```

Default for PR-2 is `legacy`.

`legacy` preserves existing runtime behavior.

`v1` enables the new extraction path.

PR-2 dual-writes v1 facts only to the existing analyzer-internal audit log.

PR-2 does not add CSV columns, payload fields, telemetry fields, search context fields, or durable audit-event fields.

## Structured picker propagation

The availability picker must verify all structured-picker paths:

- UI state
- saved/recovered draft state
- `/analyze_stream` payload
- `/analyze` payload
- `claim_search_request` fingerprint/idempotency
- root-search validation
- refinement preflight
- refinement parent-context inheritance
- completed search-session context
- search-result `search_context`
- SSE start/progress/complete/error payloads
- telemetry start/complete/error payloads
- CSV audit and durable audit event
- prompt interaction/suppression behavior
- Needs Review behavior
- recruiter-facing output without internal status leaks

## Scope guards by PR

### PR-1: Spec only

Touches only docs.

No production code, tests, frontend, backend, payloads, fingerprints, telemetry, audit, CSV, evaluator, or parser changes.

### PR-2: Extraction behind kill switch

Allowed:

- v1 extraction helper
- candidate fact shape
- tests for supported resume formats
- `NJORDHR_AVAILABILITY_EXTRACTION_MODE`

Not allowed:

- UI picker
- search payload changes
- `/analyze` or `/analyze_stream` constraint changes
- fingerprint changes
- CSV column changes
- live behavior changes while default mode is `legacy`

### PR-3: Evaluator cutover

Allowed:

- deterministic evaluator update
- v1 fact consumption
- PASS / FAIL / UNKNOWN tests
- legacy-vs-v1 parity tests across the supported resume-format set
- default flip of `NJORDHR_AVAILABILITY_EXTRACTION_MODE` from `legacy` to `v1` after parity tests pass

Not allowed:

- UI picker
- shadow normalizer catalog
- unrelated hard-filter families
- recruiter-facing canonical/status leaks

### PR-4: UI picker and propagation

Allowed:

- frontend availability picker
- `/analyze_stream` and `/analyze` payload propagation
- fingerprint update
- recovery draft sanitization
- search-session/search-context propagation
- telemetry/audit fields required by the picker contract

Not allowed:

- changing extraction semantics
- changing evaluator semantics
- shadow normalizer promotion

### PR-5: Needs Review polish

Allowed:

- Needs Review copy
- result grouping/rendering
- audit/reason text polish

Not allowed:

- new result bucket values
- internal enum leakage
- evaluator semantic changes

### PR-6: Shadow normalizer catalog

Allowed:

- availability catalog row
- corpus additions
- shadow-only audit
- schema validation tests

Not allowed:

- live dispatch
- promotion to `PROMOTED_FAMILIES`
- changing deterministic behavior

### PR-7: Promotion PR

Allowed only after evidence threshold is met.

Must include:

- deterministic-covered agreement
- deterministic-missed human-labeled recall lift
- adversarial/out-of-scope safety evidence
- zero unsafe widening
- kill-switch regression test

## Shadow normalizer catalog shape

`availability` uses a discriminated union keyed by `value_type`.

```json
{
  "family": "availability",
  "executor_id": "availability",
  "version": "v1",
  "output_schema": {
    "type": "object",
    "required": ["version", "value_type", "display_value"],
    "properties": {
      "version": {"const": "v1"},
      "value_type": {
        "enum": ["status", "by_date", "from_date", "relative_days", "window"]
      },
      "status": {"enum": ["immediate", null]},
      "available_by_date": {"type": ["string", "null"]},
      "available_from_date": {"type": ["string", "null"]},
      "available_until_date": {"type": ["string", "null"]},
      "relative_days": {"type": ["integer", "null"]},
      "resolved_reference_date": {"type": ["string", "null"]},
      "display_value": {"type": "string"}
    }
  },
  "plausibility_bounds": {
    "relative_days": {"min": 0, "max": 365}
  }
}
```

The catalog validator enforces per-`value_type` required fields:

- `status`: requires `status = "immediate"`
- `by_date`: requires non-null `available_by_date`
- `from_date`: requires non-null `available_from_date`
- `relative_days`: requires integer `relative_days` in `0..365`
- `window`: requires non-null `available_from_date` and `available_until_date`, with `available_from_date <= available_until_date`

Fields not applicable to the active `value_type` must be null. Non-null values in inactive fields are rejected.

Payloads that satisfy the JSON shape but fail the per-discriminator rule are rejected.

## Shadow normalizer classes

Class A: deterministic-covered prompts. LLM output must agree with the deterministic parser.

Class B: deterministic-missed but human-labeled prompts. Includes:

- `not available until August`
- compound prompts where availability is one clause among multiple constraints

Class C: adversarial or out-of-scope prompts. Includes:

- contradictory availability instructions
- day-of-week availability
- salary-gated availability
- contract-length-gated availability
- `available next quarter but only on Tuesdays`

Class C must route to `needs_review` or `unapplied`, never to constraints.

## Required tests

Resume extraction:

- SeaJobs `Availability Details / From date - Till date`
- single `Available from`
- single `Available until`
- immediate / ASAP
- notice period
- ambiguous numeric date
- stale relative availability
- contradictory values

Evaluator:

- immediate PASS / FAIL / UNKNOWN
- available-by ignores `availability_end_date`
- available-from means free on DATE
- within-N-days resolution
- window overlap
- stale routes UNKNOWN

UI and propagation:

- `/analyze_stream`
- `/analyze`
- root search
- refinement
- recovery draft
- fingerprint/idempotency
- search context
- telemetry/audit
- Needs Review copy
