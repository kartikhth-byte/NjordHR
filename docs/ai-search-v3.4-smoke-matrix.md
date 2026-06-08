# AI Search v3.4 Smoke Matrix

This matrix defines the minimum manual and automated verification expected while implementing Phase 1 of Specification v3.4.

## 1. Automated suite

Run on every logical change set:

```bash
python3 -m unittest -v \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_dob_parsing.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_age_filters.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_visa_filters.py
```

Expected:
- all tests pass
- no age-rule reason-code regressions
- no visa-rule behavior regressions

## 2. Baseline deterministic regression

Corpus:
- `/Users/kartikraghavan/temp12/2nd_Engineer`

Prompt:
- `should be within the ages of 30 and 50 years old`

Expected:
- PASS:
  - `2nd_Engineer_315781.pdf`
  - `2nd_Engineer_349740.pdf`
- FAIL:
  - all other resumes in that validation set

This check is mandatory before merge for any logical change set that touches:
- `_extract_dob_fact_from_text`
- `_extract_stated_age_fact_from_text`
- `_resolve_candidate_age`
- `_evaluate_age_rule`
- `_evaluate_hard_filters`
- routing that changes structured-only scan behavior

## 3. Phase 1 feature smoke cases

### Rank extraction

- labeled current rank extracts correctly
- labeled applied rank extracts correctly
- unrecognized rank returns UNKNOWN
- deck/engine distinction does not cross-resolve

### COC extraction

- valid COC recognized
- expired COC recognized
- missing expiry produces UNKNOWN, not valid
- absent COC does not hallucinate presence

### STCW extraction

- all four core certs present -> `stcw_basic_all_valid = true`
- one explicit expired/absent -> `false`
- pending or omitted -> `null`

### Logistics extraction

- valid visa remains PASS-compatible with existing parser behavior
- expired visa remains FAIL-compatible with existing parser behavior
- missing visa remains UNKNOWN-compatible with existing parser behavior

### Mixed-support query summary

- supported-only query shows only applied constraints
- mixed query shows both applied and unapplied constraints
- unsupported-only query runs without silently pretending filters were applied

## 4. Dual-version transition smoke

Only required once v3.4 Phase 1 code exists.

- v1.1 record with active rank constraint:
  - either synchronous re-extraction occurs
  - or fallback is flagged as partial evaluation
- search response surfaces partial-evaluation notice when fallback occurred
- v1.1 path must not FAIL a candidate for missing v2.0-only fields

## 5. Exit-sample review

Before Phase 1 is considered complete:
- hand-check 30 resumes for rank extraction
- hand-check same sample for COC extraction
- rerun baseline corpus
- verify migration job on at least 30 resumes
