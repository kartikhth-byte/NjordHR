# Closed Issue: MAN B&W LMC Alias

Status: closed by the deterministic engine alias table and pinned by
`tests/test_ai_analyzer_hard_filter_rules.py`,
`tests/test_ai_analyzer_logistics.py`, and
`tests/test_ai_analyzer_job_constraints.py`.

## Title

Add and verify `MAN B&W LMC` alias handling in deterministic engine extraction

## Summary

Reviewer follow-up noted that resumes may mention `MAN B&W LMC`, while the
deterministic catalog is centered on the `MAN B&W MC` family and its
descendants. The alias decision is now closed: `MAN B&W LMC` normalizes to
`man_b_w_mc`.

## Why this matters

- `MAN B&W LMC` appears in real resume experience tables.
- The alias now maps to the `MC` family in deterministic extraction.
- The fix stayed focused and did not change broader engine-layer semantics.

## Scope

- Confirm the intended canonical destination for `MAN B&W LMC`.
- Add the alias mapping.
- Add direct extraction tests.
- Add evaluator-path tests showing that a recruiter query for the mapped family
  matches resume evidence written with the alias.

## Acceptance criteria

- `MAN B&W LMC` normalizes deterministically to the chosen canonical node.
- Existing `MAN B&W MC` behavior does not regress.
- Recruiter-facing reason text stays humanized and does not leak canonical ids.

## Verification

- `test_engine_experience_extraction_normalizes_man_b_w_lmc_alias`
- `test_extract_engine_details_handles_engine_map_regressions`
- prompt-side case `has MAN B&W LMC engine experience` in
  `test_engine_experience_slot_parser_handles_recruiter_variants`

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_logistics.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`
