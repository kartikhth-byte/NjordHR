# Resume Quality And Candidate-Name Extraction End State

Date: 2026-04-30
Owner: Intake / Manual Review workflow
Status: End-state note for current tuning round

## Purpose

Document the current end state of the resume-quality and candidate-name extraction work after the ranked-evidence refactor, targeted corpus diagnostics, and reversible corpus cleanup.

This note is meant to capture:

- what was implemented
- what was validated
- what was cleaned up
- why the current tuning round should stop here

## Scope Completed

The completed work in this round covered:

- explicit ranked candidate-name evidence collection
- preservation of the unreadable-document gate
- targeted header-name extraction improvements from real corpus evidence
- regression tests for the new evidence ordering and header patterns
- folder-level diagnostics and broader validation passes
- reversible quarantine of obvious non-resume / empty-text artifacts that were polluting role folders and `_EmailInbox_Originals`

The work did not expand into:

- OCR
- broad new unreadable-document salvage logic
- generalized certificate parsing
- unrelated routing, UI, or rank-logic changes

## Final Extraction Model

The current candidate-name pipeline is now explicitly ranked in this order:

1. `STRUCTURED_FIELD`
2. `HEADER_IDENTITY`
3. `SUBJECT_IDENTITY`
4. `SENDER_IDENTITY`
5. `FILENAME_IDENTITY`

Additional current behavior:

- unreadable documents still fail early instead of entering normal manual-review candidate-name flow
- weak sender / filename fallbacks are only used when stronger evidence is absent
- sidecars and manual-review payloads preserve candidate-name source and confidence

## Corpus-Guided Improvements Landed

The targeted readable-resume header patterns resolved in this round were:

- uppercase name before `ID No`
  - example: `Durvang Bhagat`
- standalone top-line name followed by role/contact lines
  - example: `Varun Rakesh Patel`
- letter-spaced top header with adjacent email/contact block
  - example: `Arpit Jajoo`

These were added because they appeared as repeated readable-resume misses in diagnostics. They were not added as speculative heuristic expansion.

## Validation Summary

### Manual Review queue

Current manual-review diagnostics show that the ranked pipeline materially improved source quality:

- baseline artifact:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/manual_review_name_and_quality_diagnostic_2026-04-30_ranked_pipeline_baseline.json`
- post-patch artifact:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/manual_review_name_and_quality_diagnostic_2026-04-30_ranked_pipeline_post_patch.json`

Observed result:

- candidate-name sourcing moved away from weak stored/subject fallbacks and toward explicit `STRUCTURED_FIELD` / `HEADER_IDENTITY`
- unreadable junk stayed out of the normal manual-review flow

### `_EmailInbox_Originals`

This folder became the main remaining readable-resume validation target after `AB` and `Bosun` were shown to be largely corpus-contamination buckets.

Key artifacts:

- before targeted Originals fixes:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/originals_candidate_name_diagnostic_2026-04-30_pre_patch.json`
- after `Durvang` / `Varun` fixes:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/originals_candidate_name_diagnostic_2026-04-30_post_patch.json`
- after `Arpit` fix:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/originals_candidate_name_diagnostic_2026-04-30_post_arpit_patch.json`

Observed result across the readable-resume tuning passes:

- missing names dropped from `26` to `21`
- `HEADER_IDENTITY` rose from `17` to `22`
- the remaining misses are no longer readable repeated resume-name failures

### Post-cleanup live-corpus state

Final broader rollup after cleanup:

- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/candidate_name_pipeline_broader_validation_2026-04-30_post_originals_cleanup.json`

Final live state for `_EmailInbox_Originals`:

- `184` PDFs remain in the live folder
- source distribution:
  - `160 STRUCTURED_FIELD`
  - `22 HEADER_IDENTITY`
  - `2 missing`
- quality distribution:
  - `176 READABLE`
  - `6 WEAK_BUT_USABLE`
  - `2 UNREADABLE`

## Cleanup Completed

Reversible quarantine moves were completed instead of deletion.

Cleanup root:

- `/Users/kartikraghavan/Library/Application Support/NjordHR/Resumes/_CorpusCleanup_NonResume_20260430`

Folders cleaned:

- `AB`
- `Bosun`
- `_EmailInbox_Originals`

Verification artifacts:

- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/ab_bosun_cleanup_verification_2026-04-30.json`
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/originals_cleanup_verification_2026-04-30.json`

Current cleanup conclusion:

- obvious proposal/certificate/test artifacts and empty-text originals were polluting validation counts
- quarantine materially improved the live-corpus signal without destroying source files

## Remaining Residue

The remaining unresolved `_EmailInbox_Originals` misses are:

- `2` unreadable files
  - `2ND_OFF_JAVED_-_Copy.pdf`
  - `2ND_OFF_JAVED_-_Copy_1.pdf`

Current bucket artifact:

- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/originals_candidate_name_miss_buckets_2026-04-30_post_arpit_patch.json`

There are no remaining repeated readable resume-name miss patterns in the current Originals bucket.

## Why This Round Should Stop Here

This tuning round should stop here because:

- the remaining misses are unreadable-only, not readable repeated header failures
- the repo workflow explicitly favors stopping after residual buckets become non-repeating or incomplete-source cases
- pushing further would mean broadening heuristics without repeated readable evidence
- the next meaningful step would be an OCR/product decision, which was explicitly out of scope for this work

## Recommendation

Treat the current candidate-name / resume-quality work as materially complete for the non-OCR phase.

Only reopen extractor tuning if one of the following happens:

- a new repeated readable resume-name miss pattern appears in diagnostics
- the product explicitly wants OCR or another unreadable-document salvage path
- new non-resume contamination enters the live corpus and requires another reversible cleanup pass

Until then:

- keep the current ranked pipeline
- keep the unreadable gate intact
- keep the current cleanup folder as the audit trail for quarantined artifacts
