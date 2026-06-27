# Closed Follow-Up: CoC Demonym Normalization

Status: closed

## Title

Expand CoC country normalization to cover common demonyms

## Summary

CoC country matching now covers the reviewer-flagged demonym forms
`Iranian`, `Maldivian`, `Mauritian`, and `Argentinian` consistently across
prompt-side country constraints and candidate-evidence country extraction.

This note is retained as close-out evidence for the original focused
normalization-hardening issue.

## Why this matters

- Recruiters naturally type demonyms (`Indian CoC`, `Iranian CoC`) as often as
  country names.
- Missing demonym support creates quiet false negatives in an otherwise mature
  CoC filter path.
- This work is narrow and should stay isolated from unrelated engine or search
  parser changes.

## Closed Scope

- Alias coverage includes the originally missed demonym forms.
- Prompt-side normalization tests cover the named demonyms and adjacent
  long-tail demonyms.
- Candidate-evidence normalization tests cover demonym issue-authority rows.
- CoC country hard-filter messages use human display labels while preserving
  canonical values in actual/expected audit fields.

## Acceptance Criteria

- Prompts such as `has Iranian coc`, `has Maldivian coc`, `has Mauritian coc`,
  and `has Argentinian coc` normalize correctly.
- Equivalent resume evidence matches under the same canonical country.
- No regressions to existing `India` / `Indian` style paths.
- Recruiter-facing CoC country rule text uses human country labels.

## Evidence Files

- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_certifications.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_job_constraints.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
