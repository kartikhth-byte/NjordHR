# Issue Draft: MAN B&W LMC Alias

Suggested labels: `engine-experience`, `extraction`

## Title

Add and verify `MAN B&W LMC` alias handling in deterministic engine extraction

## Summary

Reviewer follow-up noted that resumes may mention `MAN B&W LMC`, while the
current deterministic catalog is centered on the `MAN B&W MC` family and its
descendants. We need a narrow alias decision and regression coverage so that
recruiter searches do not miss resumes that use the `LMC` spelling.

## Why this matters

- `MAN B&W LMC` appears in real resume experience tables.
- This is a sharp false-negative if the alias is real and intentionally
  equivalent to the `MC` family in recruiter usage.
- It should land as a focused patch, not be mixed into broader engine-layer
  semantics work.

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

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_logistics.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`
