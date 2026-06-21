# Issue Draft: Logical-Group Debug Payload Hygiene

Suggested labels: `diagnostics`, `hardening`

## Title

Trim internal detail from `_combine_any_of_item_results` debug payloads

## Summary

Reviewer follow-up noted that `_combine_any_of_item_results` can dump full
`item_results` structures into `actual_value`. That is useful while debugging
locally, but it risks surfacing oversized or overly internal payloads to
downstream logger/debug consumers.

Recruiter-facing reason text and audit/debug payloads should stay intentionally
separate.

## Scope

- Keep recruiter-visible messages unchanged.
- Reduce or reshape `actual_value` / debug payload content for logical-group
  aggregation so it contains only the fields actually needed downstream.
- Preserve enough information for support/debugging without leaking full nested
  expected values or canonical-id-heavy internals unnecessarily.

## Acceptance criteria

- `_combine_any_of_item_results` no longer stores the full raw `item_results`
  blob in `actual_value`.
- Debug payload still identifies which child item matched / failed / stayed
  unknown.
- Existing result rendering does not regress.

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
