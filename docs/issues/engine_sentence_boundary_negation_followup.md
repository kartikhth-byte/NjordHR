# Issue Draft: Sentence-Aware Engine Negation

Suggested labels: `engine-experience`, `hardening`

## Title

Sentence-aware negation handling for deterministic engine extraction

## Summary

`engine_experience` v1 currently suppresses straightforward negation near an
engine mention using a bounded look-behind heuristic. That closes the obvious
false-positive class (`no ME experience`, `never operated RT-flex`) but still
allows a documented false-suppression case when sentence punctuation is stripped
and a negation phrase from one sentence suppresses a positive engine mention in
the next sentence.

Example:

- `Held no formal certifications. Operated ME-GI engines for 18 months.`

The current v1 spec explicitly treats sentence-boundary-aware negation as a
non-goal. This issue is the follow-up to implement that missing layer.

## Why this matters

- False suppression drops real candidates into `FAIL` / `UNKNOWN` outcomes.
- The current behavior is intentional but heuristic-limited; we should not rely
  on the look-behind window forever.
- The limitation is already documented in
  `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`.

## Scope

- Split or preserve sentence boundaries before applying negation suppression.
- Keep the current bounded-token negation behavior within the same sentence.
- Preserve compact-alias handling (`manb&w`, OCR-collapsed tokens) under the
  same rule.
- Add telemetry or audit markers when negation suppression fires, so future
  review can distinguish:
  - explicit same-sentence negation
  - sentence-boundary-sensitive cases

## Acceptance criteria

- `no ME experience` suppresses `ME`.
- `never operated RT-flex` suppresses `RT-flex`.
- `Held no formal certifications. Operated ME-GI engines for 18 months.`
  still extracts `ME-GI`.
- `Without X-DF background; later joined a vessel with ME-C.` does not suppress
  the `ME-C` mention unless the negation is in the same sentence / clause under
  the chosen rule.
- Regression coverage exists for both normal aliases and compact aliases.

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_logistics.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`
