# Issue Draft: Engine Bucket Fallback Symmetry

Suggested labels: `engine-experience`, `design-decision`

## Title

Decide and test fallback symmetry for methanol/ammonia engine buckets

## Summary

The deterministic engine layer now distinguishes:

- specific subtype families such as `MAN B&W ME-LGIM`
- broad fuel buckets such as `methanol_engine` / `ammonia_engine`
- family and manufacturer fallback outcomes with lowered confidence

What still needs a deliberate decision is the symmetry of bucket fallback:

1. When the recruiter asks for a broad bucket (`methanol engine`), should
   subtype evidence (`ME-LGIM`, `X-DF-M/E`) pass directly?  
   Current expectation: yes.
2. When the recruiter asks for a specific subtype (`ME-LGIM`) and the resume
   only mentions a broad bucket (`methanol engine`), should that be:
   - `UNKNOWN / family_fallback`
   - `UNKNOWN / manufacturer_fallback`
   - or `FAIL`
3. When the recruiter asks for a broad bucket and the resume only mentions a
   manufacturer (`MAN B&W`, `WinGD`) with no methanol/ammonia subtype, should
   that be `UNKNOWN` or `FAIL`?

This should be decided once in spec and then encoded consistently across the
evaluator and shadow/test corpus.

## Why this matters

- Recruiters often search broadly first, then call candidates for specifics.
- We want low-confidence, auditable inclusions where the answer is plausibly
  true, not silent misses.
- We do **not** want broad manufacturer mentions to over-match every advanced
  fuel bucket without a written policy.

## Decision points

- Define direct-pass vs fallback vs fail for:
  - subtype -> bucket
  - bucket -> subtype
  - manufacturer -> bucket
- Define expected confidence levels for any allowed fallback.
- Define recruiter-visible reason text for each allowed fallback.

## Acceptance criteria

- Spec explicitly documents the three-direction matrix above.
- Tests cover:
  - subtype evidence satisfying broad bucket
  - broad bucket evidence satisfying subtype request if allowed
  - manufacturer-only evidence behavior for broad bucket requests
  - reason code and confidence for each fallback path
- Shadow prompt / corpus follow-up is recorded for the next revalidation batch
  if the decision changes deterministic behavior.

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`
- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/shadow_llm_family_readiness_tracker_v1.md`
