# Issue Draft: CoC Demonym Normalization

Suggested labels: `coc`, `hardening`

## Title

Expand CoC country normalization to cover common demonyms

## Summary

The current CoC country matching work improved extraction and deterministic
evaluation, but reviewer follow-up flagged that demonym forms such as
`Iranian`, `Maldivian`, `Mauritian`, and `Argentinian` are still not covered
consistently.

This is a focused normalization-hardening issue for recruiter prompts and
candidate evidence that use demonym phrasing instead of country-name phrasing.

## Why this matters

- Recruiters naturally type demonyms (`Indian CoC`, `Iranian CoC`) as often as
  country names.
- Missing demonym support creates quiet false negatives in an otherwise mature
  CoC filter path.
- This work is narrow and should stay isolated from unrelated engine or search
  parser changes.

## Scope

- Expand alias coverage for the currently missed demonym forms.
- Add prompt-side normalization tests.
- Add candidate-evidence normalization tests where applicable.
- Confirm recruiter-visible reason text still uses human country labels.

## Acceptance criteria

- Prompts such as `has Iranian coc`, `has Maldivian coc`, `has Mauritian coc`,
  and `has Argentinian coc` normalize correctly.
- Equivalent resume evidence matches under the same canonical country.
- No regressions to existing `India` / `Indian` style paths.

## Likely files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/candidate_facts/aliases`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_logistics.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
- `/Users/kartikraghavan/Tools/NjordHR/docs/specs/search_pickers_v1.md`
