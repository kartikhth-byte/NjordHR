# Closed Issue: Settings Integer Bool Rejection

Status: closed by the existing `_agent_setting_int` hardening and pinned by
`tests/test_settings_precedence.py`.

## Title

Reject boolean payloads in `_agent_setting_int` while preserving numeric `0`

## Summary

The settings hardening now rejects boolean payloads before integer coercion while
preserving integer `0` as a valid local-agent value.

## Scope

- Reject `bool` explicitly in `_agent_setting_int`.
- Preserve the valid behavior for:
  - integer `0`
  - numeric strings where supported
  - empty values mapping to default / unset behavior
- Add regression coverage around settings payload behavior.

## Acceptance criteria

- `0` remains a valid returned value.
- `true` / `false` are rejected rather than coerced.
- Existing settings flows do not regress.

## Verification

- `test_settings_payload_preserves_zero_poll_interval_from_local_agent`
- `test_settings_payload_rejects_boolean_poll_interval_from_local_agent`
- `test_settings_payload_rejects_false_boolean_poll_interval_from_local_agent`

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_settings_precedence.py`
