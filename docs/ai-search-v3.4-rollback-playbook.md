# AI Search v3.4 Rollback Playbook

This playbook covers rollback for Phase 1 deterministic AI-search changes.

## When to roll back

Roll back the Phase 1 change set if any of the following occurs:

- age baseline no longer returns the validated pass set
- existing US visa behavior regresses
- rank or COC rule activation changes unrelated searches
- mixed-support queries silently omit unapplied constraints
- dual-version fallback causes user-visible false failures
- synchronous re-extraction creates unacceptable search instability

## Rollback principles

- protect age and visa behavior first
- remove new rule activation before touching old rule paths
- prefer disabling new Phase 1 rule families over reverting the entire analyzer if possible
- never leave the system in a state where `hard_constraints` activates rules without `applied_constraints`

## Rollback order

### 1. Disable new rule families

If the regression is isolated to new rule families:
- disable `rank_match`
- disable `coc_document_gate`
- disable `stcw_basic`

Expected result:
- system returns to age + visa-only deterministic behavior
- existing baseline remains intact

### 2. Disable mixed-support constraint activation

If query parsing is the problem:
- stop populating new applied rule-family IDs
- keep parsed informational fields if safe
- preserve user-visible unapplied-constraint summary if it is accurate

Expected result:
- no accidental activation of partial Phase 1 logic

### 3. Revert extractor additions

If new extractors are producing widespread bad facts:
- revert rank extraction changes
- revert COC extraction changes
- revert STCW extraction changes
- revert logistics changes only if they changed existing visa behavior

Expected result:
- CandidateFacts returns to prior stable shape for active paths

### 4. Revert dual-version transition logic

If synchronous re-extraction is destabilizing search:
- disable synchronous re-extraction path
- retain v1.1/v2.0 routing
- mark all v1.1 candidates as partial evaluation for new-rule searches if that path still exists

Expected result:
- slower correctness improvement, but restored search stability

## Mandatory verification after rollback

Run:

```bash
python3 -m unittest -v \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_dob_parsing.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_age_filters.py \
  /Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_visa_filters.py
```

Then rerun the validated baseline:
- folder: `/Users/kartikraghavan/temp12/2nd_Engineer`
- prompt: `should be within the ages of 30 and 50 years old`
- expected pass set:
  - `2nd_Engineer_315781.pdf`
  - `2nd_Engineer_349740.pdf`

## Decision rule

If a rollback candidate still leaves age or visa behavior uncertain, keep rolling back until the deterministic engine returns to the last known-good age/visa baseline. New feature loss is acceptable. Old deterministic correctness loss is not.
