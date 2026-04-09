# AI Search v3.4 Prompt Corpus Evidence

Date: 2026-04-09

Purpose:
- record the current prompt-corpus evidence available for Phase 1 launch-gate review
- distinguish stored real-prompt coverage from bootstrap corpus coverage
- note the current ship-type parser coverage result after config-alignment work

## 1. Evidence Sources

Artifacts used in this note:
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/prompt_corpus_review_current.json`
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/bootstrap_prompt_corpus_eval_current.json`
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/config_ship_type_prompt_coverage_current.json`

Supporting inputs:
- `/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/prompt_corpus_review_report.py`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/bootstrap_prompt_corpus_eval.py`

## 2. Real Stored Prompt Coverage

Current stored-prompt coverage from audit rows is still below the launch-gate threshold for all active deterministic families:

- `age_range`: `0`
- `us_visa`: `3`
- `rank_match`: `0`
- `coc_document_gate`: `0`
- `stcw_basic`: `11`

Current judgment:
- real production/UAT prompt volume is still too thin to claim launch-gate coverage from stored prompts alone
- this is an evidence gap, not a parser-implementation gap

## 3. Bootstrap Prompt Corpus Coverage

The bootstrap corpus now contains `20` prompts for each active deterministic family:

- `age_range`
- `us_visa`
- `rank_match`
- `coc_document_gate`
- `stcw_basic`

For launch-gate interpretation, the meaningful score is `expected_family_present_ratio`, not strict single-primary-family scoring, because the bootstrap corpus intentionally includes hybrid prompts.

Current bootstrap coverage:

- `age_range`: `1.0`
- `us_visa`: `1.0`
- `rank_match`: `1.0`
- `coc_document_gate`: `1.0`
- `stcw_basic`: `1.0`

Current judgment:
- the bootstrap corpus is sufficient as a non-production parser-coverage evidence pack
- it does not replace the need to accumulate real prompt samples over time
- it is appropriate for launch-gate review only if it is explicitly labeled bootstrap/non-production

## 4. Ship-Type Parser Coverage

After config-alignment work, ship-type prompt recognition now covers the full configured ship-type catalog in `config.ini`.

Current result:

- configured ship types: `104`
- covered by prompt and experience constraint recognition: `104`
- missed: `0`

Current judgment:
- the earlier ship-type parser/catalog mismatch is no longer open for the prompt-recognition layer

## 5. Remaining Evidence Gaps

The main remaining Phase 1 evidence gaps are now:

1. real stored prompt volume is still below the 20-prompt threshold for most active deterministic families
2. migration-readiness evidence still needs an explicit pack:
   - background re-extraction sample run
   - idempotence check
   - migration-progress observability check

## 6. Readiness Interpretation

Current interpretation:
- parser coverage for the active deterministic prompt families is now evidenced through the bootstrap corpus
- ship-type config alignment is evidenced
- real prompt-corpus sign-off is still only partial because stored prompt volume is not yet sufficient

Recommended next step:
- treat prompt-corpus coverage as `partial`, not `open`
- use the bootstrap corpus for current launch-gate review
- continue collecting real prompts until the stored corpus reaches the required thresholds
