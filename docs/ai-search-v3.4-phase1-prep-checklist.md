# AI Search v3.4 Phase 1 Prep Checklist

Use this checklist before starting implementation work for Specification v3.4 Phase 1.

## Scope lock

- [ ] Working spec is [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md)
- [ ] Phase 1 scope is limited to Tier 1 fields and rules only
- [ ] Phase 2 and Phase 3 items are explicitly out of scope for the current branch
- [ ] Rank fuzzy matching is explicitly excluded from Phase 1

## Baseline protection

- [ ] Existing age filter behavior is treated as non-regression-critical
- [ ] Existing US visa filter behavior is treated as non-regression-critical
- [ ] Validated baseline corpus is available:
  - `/Users/kartikraghavan/temp12/2nd_Engineer`
- [ ] Validated prompt is fixed:
  - `should be within the ages of 30 and 50 years old`
- [ ] Expected deterministic pass set is fixed:
  - `2nd_Engineer_315781.pdf`
  - `2nd_Engineer_349740.pdf`

## Repo state

- [ ] Current branch is correct for this work
- [ ] Any unrelated local changes are identified and will be left untouched
- [ ] AI-search files to be edited are understood before modification:
  - [ai_analyzer.py](/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py)
  - [frontend.html](/Users/kartikraghavan/Tools/NjordHR/frontend.html) if stream/UI wiring is needed
  - relevant test files under [tests](/Users/kartikraghavan/Tools/NjordHR/tests)

## Required test baseline

- [ ] Existing tests pass before Phase 1 implementation starts:
  - [test_ai_analyzer_dob_parsing.py](/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_dob_parsing.py)
  - [test_ai_analyzer_age_filters.py](/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_age_filters.py)
  - [test_ai_analyzer_visa_filters.py](/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_visa_filters.py)
- [ ] New extractors will not be wired in without dedicated tests
- [ ] UNKNOWN behavior is treated as first-class test coverage, not an edge case

## Phase 1 implementation order

- [ ] Commit 1: schema stubs
- [ ] Commit 2: rank alias table and normalization
- [ ] Commit 3: COC extraction
- [ ] Commit 4: STCW and endorsements extraction
- [ ] Commit 5: logistics extraction
- [ ] Commit 6: hard filter rule wiring
- [ ] Commit 7: mixed-support constraint summary
- [ ] Commit 8: extractable profile completeness
- [ ] Commit 9: regression validation

## Merge gate

- [ ] Validated baseline rerun before merge for any logical change set touching evaluation path
- [ ] Partial-evaluation fallback notice behavior verified if dual-version paths are touched
- [ ] Rollback steps reviewed in [ai-search-v3.4-rollback-playbook.md](/Users/kartikraghavan/Tools/NjordHR/docs/ai-search-v3.4-rollback-playbook.md)
