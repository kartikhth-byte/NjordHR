# Closed Issue: Engine Bucket Fallback Symmetry

Status: closed by the fuel-bucket fallback matrix in
`docs/specs/engine_experience_layers_v1.md` and pinned by
`tests/test_ai_analyzer_hard_filter_rules.py`.

## Title

Decide and test fallback symmetry for methanol/ammonia engine buckets

## Summary

The deterministic engine layer now distinguishes:

- specific subtype families such as `MAN B&W ME-LGIM`
- broad fuel buckets such as `methanol_engine` / `ammonia_engine`
- family and manufacturer fallback outcomes with lowered confidence

The fallback symmetry decision is now documented:

1. When the recruiter asks for a broad bucket (`methanol engine`,
   `ammonia engine`), subtype evidence passes directly.
2. When the recruiter asks for a specific subtype and the resume only mentions
   the matching ancestor bucket, the result is `UNKNOWN` via
   `ENGINE_EXPERIENCE_FAMILY_FALLBACK` at `70%` confidence.
3. When the recruiter asks for a broad methanol/ammonia bucket and the resume
   only mentions generic manufacturer or family evidence (`MAN`, `MAN B&W`,
   `MAN B&W ME`, `WinGD`, `WinGD X engines`), the result is `FAIL`.

The evaluator and tests now encode that matrix symmetrically for methanol and
ammonia.

## Why this matters

- Recruiters often search broadly first, then call candidates for specifics.
- Low-confidence family fallback is allowed where the answer is plausibly true.
- Broad manufacturer mentions do not over-match advanced fuel buckets.

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
  - broad bucket evidence satisfying subtype request as family fallback
  - manufacturer-only evidence behavior for broad bucket requests
  - reason code and confidence for each fallback path
- Shadow prompt / corpus follow-up is recorded for the next revalidation batch
  if the decision changes deterministic behavior.

## Verification

- `test_engine_experience_rule_matches_specific_subtype_for_generic_methanol_bucket`
- `test_engine_experience_rule_matches_specific_subtype_for_generic_ammonia_bucket`
- `test_engine_experience_rule_uses_family_fallback_for_methanol_bucket_when_only_dual_fuel_is_known`
- `test_engine_experience_rule_uses_family_fallback_for_ammonia_bucket_when_only_dual_fuel_is_known`
- `test_engine_experience_rule_uses_family_fallback_for_specific_methanol_subtype_when_only_bucket_is_known`
- `test_engine_experience_rule_uses_family_fallback_for_specific_ammonia_subtype_when_only_bucket_is_known`
- `test_engine_experience_rule_rejects_generic_evidence_for_fuel_specific_buckets`

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/engine_experience_layers_v1.md`
- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/shadow_llm_family_readiness_tracker_v1.md`
