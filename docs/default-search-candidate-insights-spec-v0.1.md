# Default Search Candidate Insights Spec v0.1

Date: 2026-05-12
Status: Draft
Owner: AI Search / Candidate Intelligence

## 1. Goal

For every AI search result, display two default candidate insights without requiring the recruiter to ask for them explicitly:

1. Number of months the candidate has worked in the current rank
2. Whether the candidate has had any gap greater than 6 months between contracts

This spec is for default result-display enrichment, not for introducing new hard-filter families in Phase 1.

## 2. Feasibility Summary

### 2.1 Overall

The feature is feasible, but not at identical quality for all resume sources.

### 2.2 Months worked in current rank

Feasibility: High for SeaJobs resumes, Low-to-Medium for email resumes

Why:
- The analyzer already extracts:
  - current rank
  - SeaJobs experience rows
  - ordered sign-in / sign-out dates
- This is a deterministic derived signal:
  - sum durations for experience rows whose normalized rank matches `role.current_rank_normalized`

Main challenge:
- Email resumes do not have a single stable service-history table shape.

Conclusion:
- Feasible as a SeaJobs-first derived insight.
- Email resumes should return `UNKNOWN` / `SOURCE_EXCLUDED` until a later extractor round proves good enough.

### 2.3 Gap greater than 6 months between contracts

Feasibility: High for SeaJobs resumes, Low-to-Medium for email resumes

Why:
- SeaJobs service rows contain ordered sign-in / sign-out dates.
- Gap detection is deterministic:
  - sort contracts chronologically
  - compute days between one sign-out and the next sign-in
  - flag if any gap exceeds threshold

Main challenge:
- Requires reliable multiline row reconstruction and date parsing.
- Email resumes vary too much for safe Phase 1 default display.

Conclusion:
- Feasible as a SeaJobs-first derived insight.
- Email resumes should return `UNKNOWN` / `SOURCE_EXCLUDED` until explicitly expanded.

## 3. Product Decision

Build this feature as:
- default display-only candidate insights
- derived from structured candidate facts
- conservative where evidence is incomplete

Do not build it as:
- new automatic hard filters
- LLM-interpreted candidate judgments
- all-resume-source logic from day one

## 4. Phase 1 Scope

### 4.1 In scope

- Display two new default insights on every search result card
- SeaJobs-first extraction and derivation for:
  - months in current rank
  - contract-gap-over-6-months
- Conservative `UNKNOWN` or `SOURCE_EXCLUDED` behavior where evidence is insufficient

### 4.2 Out of scope

- Hard-filter prompts for these insights
- Ranking-weight changes
- Email-resume generalized support
- LLM-based “reputation” inference
- Automatic company prestige scoring from the public web

## 5. Definitions

### 5.1 Current rank months

`Months in current rank` means:
- total months across SeaJobs service-history rows whose normalized row rank equals `role.current_rank_normalized`

It does not mean:
- months in applied rank
- months in department
- months at or above current rank

### 5.2 Gap > 6 months

`Gap > 6 months` means:
- at least one interval between consecutive contracts within the last 5 years where:
  - next sign-in date - prior sign-out date > 183 days

This should be displayed as:
- whether such a gap exists
- the maximum detected gap within the last 5 years
- the timeline of that gap

## 6. Data Model Additions

Add the following derived fields to `candidate_facts`.

### 6.1 Months in current rank

Under `derived`:

```json
"current_rank_months_total": 26
```

Under `fact_meta`:

```json
"derived.current_rank_months_total": {
  "value": 26,
  "confidence": 0.9,
  "extraction_method": "seajobs_service_history_rank_duration_sum",
  "status": "PARSED | MISSING | SOURCE_EXCLUDED | UNKNOWN",
  "source_label": "seajobs_resume",
  "context": {
    "field": "derived.current_rank_months_total",
    "current_rank_normalized": "2nd_engineer"
  }
}
```

### 6.2 Gap over 6 months

Under `derived`:

```json
"has_contract_gap_over_6_months": true,
"max_contract_gap_days": 241,
"max_contract_gap_start": "2024-02-01",
"max_contract_gap_end": "2024-10-01"
```

Under `fact_meta`:

```json
"derived.has_contract_gap_over_6_months": {
  "value": true,
  "confidence": 0.9,
  "extraction_method": "seajobs_service_history_gap_scan",
  "status": "PARSED | MISSING | SOURCE_EXCLUDED | UNKNOWN",
  "source_label": "seajobs_resume",
  "context": {
    "field": "derived.has_contract_gap_over_6_months",
    "threshold_days": 183,
    "lookback_years": 5,
    "max_contract_gap_days": 241,
    "max_contract_gap_start": "2024-02-01",
    "max_contract_gap_end": "2024-10-01"
  }
}
```

## 7. Extraction / Derivation Design

### 7.1 Shared prerequisite

Use the existing SeaJobs-only experience-row pipeline as the primary source:
- `_extract_seajobs_experience_section`
- `_extract_seajobs_experience_row_snippets`
- multiline date reconstruction
- company-name normalization

Do not introduce a separate row parser if the existing row-reconstruction path can be extended safely.

### 7.2 Months in current rank derivation

Implementation model:
1. detect normalized current rank from `role.current_rank_normalized`
2. parse SeaJobs experience rows into:
   - row rank
   - sign-in date
   - sign-out date
3. normalize row rank
4. for rows matching current rank:
   - compute month duration conservatively
   - sum durations

Conservative rules:
- if row dates are incomplete, skip that row
- if current rank is missing, return `UNKNOWN`
- do not infer months from vessel count or free text

### 7.3 Contract-gap derivation

Implementation model:
1. parse all valid SeaJobs experience rows with sign-in and sign-out dates
2. sort rows by sign-in date ascending
3. compute gaps between:
   - previous row sign-out
   - next row sign-in
4. restrict evaluation to gaps whose surrounding contracts fall within the last 5 years
5. if any gap > 183 days:
   - set `has_contract_gap_over_6_months = true`
   - store `max_contract_gap_days`
   - store the gap timeline:
     - `max_contract_gap_start`
     - `max_contract_gap_end`

Conservative rules:
- if fewer than two valid rows exist, return `MISSING`
- if dates are contradictory or unparseable, return `UNKNOWN`

## 8. Resume Source Scope

### 8.1 SeaJobs resumes

Supported in Phase 1 for both insights.

### 8.2 Email resumes

Default behavior in Phase 1:
- `SOURCE_EXCLUDED` for:
  - months in current rank
  - gap over 6 months

Reason:
- current row-level service-history reliability is not validated broadly enough for these derived signals

## 9. UI Surfacing

On every result card, add an `Insights` block with up to two lines:

- `Months in current rank: 26`
- `Gap > 6 months: Yes (8 months, Feb 2024 to Oct 2024)`

Display rules:
- show only when status is `PARSED`
- if `MISSING` or `SOURCE_EXCLUDED`, hide the line by default
- optionally show in details drawer or expanded audit view later

Do not:
- color these as PASS/FAIL
- treat them as eligibility explanations unless the user explicitly searched on them in a future phase

## 10. Validation Workflow

Follow the repo extraction-tuning workflow.

### 10.1 Current-rank months

1. start with one SeaJobs-heavy rank folder
2. compare derived months with visible service rows
3. add row-shape regressions
4. rerun same-folder diagnostic
5. broaden across folders

### 10.2 Contract gaps

1. start with one SeaJobs-heavy folder
2. inspect top gap examples manually
3. verify multiline-date rows and chronological ordering
4. verify the last-5-years windowing behavior
5. rerun same-folder diagnostic
6. broaden across folders

## 11. Tests Required

### 11.1 Unit tests

- current-rank month summation from multiple rows
- gap-over-6-month detection
- email resume exclusion for both signals
- incomplete row behavior remains conservative

### 11.2 Corpus diagnostics

Save artifacts in `AI_Search_Results` for:
- single-folder baseline
- same-folder post-patch
- broader rollup

## 12. Risks

### 12.1 Current-rank month totals may overcount overlapping rows

Mitigation:
- detect overlapping intervals
- document conservative merge or skip policy

### 12.2 Gap detection depends on date quality

Mitigation:
- SeaJobs-only Phase 1
- use existing multiline date reconstruction
- add stricter row-level pairing for sign-in vs sign-out dates before trusting month totals
- keep `UNKNOWN` conservative

## 13. Implementation Sequence

Recommended order:

1. Spec approval
2. Add current-rank months derivation
3. Add gap-over-6-month derivation
4. UI surfacing
5. broader validation rollup

## 14. Recommendation

Proceed as a SeaJobs-first default-insights feature.

Recommended product stance:
- `months in current rank` is feasible and high-value
- `gap > 6 months` is feasible and high-value
- email-resume support should be deferred until separately validated
