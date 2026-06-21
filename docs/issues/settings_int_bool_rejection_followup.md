# Issue Draft: Settings Integer Bool Rejection

Suggested labels: `settings`, `hardening`

## Title

Reject boolean payloads in `_agent_setting_int` while preserving numeric `0`

## Summary

The recent settings hardening fixed the important `0`-value round-trip bug in
`_agent_setting_int`, but one deferred edge case remains: boolean payloads
still coerce through `int(True) == 1` / `int(False) == 0`.

That means a malformed upstream payload such as `{poll_interval: true}` could be
silently stored as `1` instead of being rejected.

## Scope

- Reject `bool` explicitly in `_agent_setting_int`.
- Preserve the current valid behavior for:
  - integer `0`
  - numeric strings where supported
  - empty values mapping to default / unset behavior
- Add regression coverage around settings save/load behavior.

## Acceptance criteria

- `0` remains a valid stored and returned value.
- `true` / `false` are rejected rather than coerced.
- Existing settings flows do not regress.

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_settings_precedence.py`
