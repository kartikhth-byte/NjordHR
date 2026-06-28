# Closed Issue: Sentence-Aware Engine Negation

Status: sentence / clause boundary behavior is closed by the current bounded
engine negation window and pinned by
`tests/test_ai_analyzer_hard_filter_rules.py`.

## Title

Sentence-aware negation handling for deterministic engine extraction

## Summary

`engine_experience` v1 suppresses straightforward negation near an engine
mention using a bounded look-behind heuristic that is reset at sentence and
clause boundaries.

Example:

- `Held no formal certifications. Operated ME-GI engines for 18 months.`

The sentence-boundary layer is now part of the deterministic extractor. Broader
context classification, such as training/course context versus sea-time context,
remains separate future work. Telemetry or audit markers for broader negation
review are deferred to that extraction-hardening work; this closeout does not
change telemetry or audit payload shape.

## Why this matters

- Same-sentence negation still suppresses direct negated evidence.
- A negation phrase in a prior sentence or clause does not suppress later
  positive engine evidence.
- Broader context classification and telemetry markers remain intentionally
  outside this closeout.

## Scope

- Split or preserve sentence boundaries before applying negation suppression.
- Keep the bounded-token negation behavior within the same sentence / clause
  window.
- Preserve compact-alias and model-token handling under the same rule.

## Acceptance criteria

- `no ME experience` suppresses `ME`.
- `never operated RT-flex` suppresses `RT-flex`.
- `Held no formal certifications. Operated ME-GI engines for 18 months.`
  extracts `ME-GI`.
- `Without X-DF background; later joined a vessel with ME-C.` extracts `ME-C`.
- Regression coverage exists for normal aliases, compact aliases, and model
  tokens.

## Verification

- `test_engine_experience_extraction_keeps_contrastive_positive_engine_evidence`
- `test_engine_experience_extraction_suppresses_negated_engine_list`
- `test_engine_experience_extraction_respects_sentence_boundary_negation`

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_logistics.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`
