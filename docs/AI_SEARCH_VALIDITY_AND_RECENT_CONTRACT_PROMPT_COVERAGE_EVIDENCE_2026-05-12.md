# AI Search Validity And Recent Contract Prompt Coverage Evidence

Date: 2026-05-12

Purpose:
- record parser-coverage evidence for the three families added in the May 12 validity-window spec
- distinguish staged bootstrap evidence from stored prompt-log evidence
- capture the one observed staged parser gap before making any broader coverage claim

## 1. Evidence Sources

Artifacts used in this note:
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/validity_and_recent_contract_bootstrap_prompt_corpus_eval_2026-05-12.json`
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/prompt_corpus_review_2026-05-12.json`

Supporting inputs:
- `/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/bootstrap_prompt_corpus_eval.py`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/prompt_corpus_review_report.py`

## 2. Bootstrap Prompt Coverage

The staged corpus contains `20` prompts for each reviewed family:
- `passport_validity`
- `us_visa` validity-window prompts
- `recent_contract_vessel_experience`

Coverage result:
- `passport_validity`
  - prompt count: `20`
  - expected-family-present ratio: `1.0`
  - primary-family-match ratio: `1.0`
- `us_visa`
  - prompt count: `20`
  - expected-family-present ratio: `1.0`
  - primary-family-match ratio: `1.0`
- `recent_contract_vessel_experience`
  - prompt count: `20`
  - expected-family-present ratio: `0.95`
  - primary-family-match ratio: `0.95`

Current judgment:
- the passport and visa validity-window parsers have clean staged coverage for the bounded prompt forms under review
- the recent-contract vessel parser is broadly covered but not perfect in the staged pack

## 3. Observed Bootstrap Gap

The single mismatch is:
- `chief cook minimum 5 months cruise experience in recent 2 contracts`

Observed parser behavior:
- rank was recognized
- `recent_contract_vessel_experience` did not activate

Current interpretation:
- this is a prompt-side ship-type vocabulary gap in the staged review path, not a row-level SeaJobs contract aggregation failure
- the current staged pack evaluates without a loaded runtime ship-type config and therefore exercises the fallback vocabulary path
- no parser expansion was made from this single staged example

Recommended handling:
- keep this as an explicit prompt-coverage note
- revisit only if `cruise` or a similar omitted configured ship-type appears in real recruiter prompts or a config-backed prompt coverage run

## 4. Stored Prompt Coverage

The stored audit-derived prompt review now recognizes the new family IDs in its reporting layer.

Current stored prompt counts:
- `passport_validity`: `11`
- `us_visa`: `12`
- `recent_contract_vessel_experience`: `0`

Current judgment:
- stored real-prompt evidence is still below the `20` prompt review threshold for all three reviewed families
- the real-corpus gap is largest for `recent_contract_vessel_experience`
- this is an evidence-volume gap, not a reason to undo the parser implementation

## 5. Readiness Interpretation

Current interpretation:
- implementation is complete for the three May 12 families
- staged bootstrap prompt coverage is complete for passport and visa validity windows
- staged bootstrap prompt coverage is nearly complete for recent-contract vessel experience, with one documented staged vocabulary miss
- stored real-prompt coverage remains partial and should continue to accumulate before stronger launch-gate claims are made

Recommended next step:
- treat these families as implementation-complete with bootstrap prompt evidence attached
- continue collecting real prompt logs for the three families
- revisit recent-contract prompt vocabulary only when prompted by repeated real examples or a config-backed prompt coverage run
