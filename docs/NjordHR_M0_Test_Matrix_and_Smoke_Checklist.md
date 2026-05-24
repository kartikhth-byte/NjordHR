# NjordHR M0 Test Matrix and Smoke Checklist

This document is the baseline verification artifact for `M0-T4`.
It records the minimum checks expected before and during the guardrails phase.

## 1. Scope

Use this checklist for changes that touch:
- architecture guardrails
- feature flags
- review-pack generation
- query-understanding support tooling
- candidate-facts repository / replay plumbing

It is intentionally conservative. If a change touches any evaluation, replay, or routing path, use the applicable matrix below before merge.

## 2. Automated Baseline Matrix

### 2.1 Deterministic AI-search regression

Run:

```bash
python3 -m unittest -v \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_dob_parsing.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_age_filters.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_visa_filters.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_job_constraints.py
```

Expected:
- all tests pass
- no age-rule regressions
- no visa-rule regressions
- partial-evaluation / UNKNOWN behavior remains intact

### 2.2 Query-understanding smoke

Run:

```bash
python3 -m unittest -v \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_schema.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_compare.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_legacy_adapter.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_shadow_audit.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_shadow_llm_provider.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_review_pack.py
```

Expected:
- schema validation passes
- legacy adapter preserves current semantics
- comparison outcomes remain stable
- review-pack helper tests pass
- shadow-audit helper tests pass

### 2.3 Candidate-facts smoke

Run:

```bash
python3 -m unittest -v \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_candidate_facts_schema.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_candidate_facts_storage.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_candidate_facts_extractors.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_candidate_facts_persistence.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_candidate_facts_audit.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_candidate_facts_repository.py
```

Expected:
- candidate-facts schema stays valid
- SeaJobs compatibility bridge stays degraded/partial for hollow shells
- replay identity selection remains exact
- audit metadata reports the actual replay resolution mode
- repository smoke path remains repository-backed

## 3. Baseline Deterministic Regression

Corpus:
- `/Users/kartikraghavan/temp12/2nd_Engineer`

Prompt:
- `should be within the ages of 30 and 50 years old`

Expected pass set:
- `2nd_Engineer_315781.pdf`
- `2nd_Engineer_349740.pdf`

Expected fail set:
- all other resumes in the validation set

Required if the change touches:
- `_extract_dob_fact_from_text`
- `_extract_stated_age_fact_from_text`
- `_resolve_candidate_age`
- `_evaluate_age_rule`
- `_evaluate_hard_filters`
- structured-only scan routing

## 4. Smoke Checklist

- [ ] Baseline age corpus run completed
- [ ] Automated smoke commands completed for the touched slice
- [ ] Query-understanding review pack regenerated when related files changed
- [ ] Shadow-audit artifact regenerated when related files changed
- [ ] Candidate-facts replay block in the review pack is real repository-backed data
- [ ] `candidate_facts_audit` is present in shadow audit rows when applicable
- [ ] Feature flags are documented in the architecture spec
- [ ] Rollback path is reviewed before merge
- [ ] Any demo or placeholder data is called out explicitly in the PR

## 5. Notes

- Architecture spec: [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md)
- Hybrid rollout spec: [NjordHR_Online_Hybrid_Architecture_Spec.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Online_Hybrid_Architecture_Spec.md)
- PR template: [pull_request_template.md](/Users/kartikraghavan/Tools/NjordHR/.github/pull_request_template.md)
- Backlog: [NjordHR_Implementation_Modules_and_Task_Backlog.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md)
