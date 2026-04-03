## Summary

- What changed:
- Why:

## Scope Check

- [ ] This PR is within the active implementation phase
- [ ] Phase 2/3 items were not pulled into a Phase 1 change set
- [ ] Existing age behavior is unchanged
- [ ] Existing US visa behavior is unchanged

## Required Checks

- [ ] `python3 -m unittest -v /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_dob_parsing.py /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_age_filters.py /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_visa_filters.py`
- [ ] If evaluation path changed: reran validated baseline corpus
- [ ] If new extractor added: dedicated test coverage added
- [ ] If new rule activated: `applied_constraints` activation behavior verified
- [ ] If dual-version path touched: partial-evaluation fallback behavior verified

## Validated Baseline

- Folder: `/Users/kartikraghavan/temp12/2nd_Engineer`
- Prompt: `should be within the ages of 30 and 50 years old`
- Expected pass set:
  - `2nd_Engineer_315781.pdf`
  - `2nd_Engineer_349740.pdf`

- [ ] Baseline matched
- [ ] Baseline not applicable because this PR does not touch evaluation path

## Risks

- [ ] No change to `_extract_dob_fact_from_text` or `_extract_stated_age_fact_from_text`
- [ ] If those methods changed, the change was isolated in a dedicated commit with regression proof
- [ ] Rollback steps reviewed

## Notes

- Spec reference: [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md)
- Prep checklist: [ai-search-v3.4-phase1-prep-checklist.md](/Users/kartikraghavan/Tools/NjordHR/docs/ai-search-v3.4-phase1-prep-checklist.md)
- Smoke matrix: [ai-search-v3.4-smoke-matrix.md](/Users/kartikraghavan/Tools/NjordHR/docs/ai-search-v3.4-smoke-matrix.md)
- Rollback playbook: [ai-search-v3.4-rollback-playbook.md](/Users/kartikraghavan/Tools/NjordHR/docs/ai-search-v3.4-rollback-playbook.md)
