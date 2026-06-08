# AI Search Tail Set v0.2 Eval Evidence — 2026-06-09

Branch: `codex/cycle-3-v0.2-eval-evidence`

This is the first Gemini-backed revalidation run against the rebuilt `AI_Search_Results/seajobs_tail_set_v0.2.json` on `main` after PR #21. v0.2 is new evidence, not a byte-equivalent replacement for the lost v0.1 corpus.

## Inputs

- Tail set: `AI_Search_Results/seajobs_tail_set_v0.2.json`
- Bootstrap solved sets:
  - `docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json`
  - `docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json`
- Combined audit rows: `318`
- LLM plans returned: `318 / 318`
- Shadow diagnostics: `{'schema_invalid': 73, 'ok': 245}`
- Score artifact: `docs/eval-evidence/ai-search-tail-set-v0.2-score-2026-06-09.json`

## Score Summary

- Promote candidates: `none`
- Control violations: `4 / 15`
- Solved-set regressions: `41 / 79`
- Rows missing from eval: `0`
- Rows not evaluated due disabled LLM: `0`

| Family | v0.1 preserved scoreboard | v0.2 rescue | Rescue rate | Control violation | Solved regressions | Verdict |
|---|---:|---:|---:|---|---:|---|
| `age_range` | 26/26 | 18/22 | 0.818 | no | 0/0 | `hold` |
| `certificate_requirement` | 2/2 | 8/20 | 0.400 | no | 0/0 | `hold` |
| `passport_validity` | n/a | 0/19 | 0.000 | yes | 2/20 | `hold` |
| `rank_match` | 25/25 | 12/22 | 0.545 | yes | 0/0 | `hold` |
| `stcw_basic` | 20/20 | 8/17 | 0.471 | no | 0/0 | `hold` |
| `unsupported` | n/a | 0/5 | 0.000 | no | 0/0 | `hold` |
| `us_visa` | 22/22 | 18/23 | 0.783 | yes | 20/40 | `hold` |

## Control Violations

- `no passport required for this position` -> hallucinated `passport_validity`
- `visa-free Schengen entry` -> hallucinated `us_visa`
- `no US visa needed` -> hallucinated `us_visa`
- `experienced senior captain who is youthful in approach` -> hallucinated `rank_match`

## Confidence Counts

Counts below are from emitted applied constraints in the LLM plans. They are not sufficient for promotion because the eval gate and value-review gate failed/are incomplete.

- `age_range`: `{'high': 38}`
- `availability`: `{'high': 4}`
- `certificate_requirement`: `{'high': 11}`
- `coc_document_gate`: `{'high': 17}`
- `coc_grade_match`: `{'high': 1}`
- `passport_validity`: `{'high': 24}`
- `rank_match`: `{'high': 149}`
- `stcw_basic`: `{'high': 28}`
- `stcw_endorsement`: `{'high': 4}`
- `us_visa`: `{'high': 61}`

## Verdict

v0.2 does **not** revalidate any promote candidate in this run. The result fails on automatic eval evidence before row-level value review: no family satisfies rescue rate plus zero control violations plus zero solved-set regressions.

This could reflect one or more of:

- v0.2 is harder than v0.1.
- The live model/provider behavior has drifted since the preserved v0.1 scoreboard.
- Some v0.2 labels/conventions need further review.
- The shadow provider emitted schema-invalid output for 73 rows, reducing usable rescue evidence.

Next steps should focus on schema-invalid output, control-veto failures, and solved-set regressions before any `Settings.LLM_Promotion_Stage` increase.
