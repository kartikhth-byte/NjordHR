# Closed Issue: Logical-Group Debug Payload Hygiene

Status: closed by `_redacted_group_child_results` and pinned by
`tests/test_ai_analyzer_hard_filter_rules.py`.

## Title

Trim internal detail from `_combine_any_of_item_results` debug payloads

## Summary

`_combine_any_of_item_results` now stores redacted child summaries in
`actual_value` instead of full `item_results` structures. This preserves the
child decision, reason code, message, confidence, unknown reason, and optional
child label without carrying nested `actual_value`, `expected_value`, or
`logical_group_child_constraint` payloads forward.

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

## Verification

- `test_combine_any_of_item_results_sanitizes_debug_payload`
- `test_combine_any_of_item_results_sanitizes_fail_and_unknown_payloads`
- `test_vessel_engine_any_of_group_redacts_child_payloads`
  (`tests/test_ai_analyzer_ship_type_filters.py`)

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
