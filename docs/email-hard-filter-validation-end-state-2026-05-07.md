# Email Hard-Filter Validation End State

Date: 2026-05-07

## Scope

This note closes the email-resume hard-filter validation round for the Phase 1 AI analyzer workstream.

The focus of this round was not new product behavior. The goal was to answer a narrower operational question:

- do the active deterministic hard-filter families behave acceptably on email-downloaded resumes across the live rank corpus
- if not, are the remaining misses caused by repeated extraction gaps or by incomplete / mixed source text

Validation followed the extraction tuning workflow in `AGENTS.md`:

1. folder-level diagnostic
2. targeted extractor change
3. regression tests
4. rerun same-folder diagnostic
5. broader validation pass

## Families Now Broadly Validated

The active Phase 1 hard-filter families now have broad email-corpus validation artifacts:

- `age_range`
- `us_visa`
- `rank_match` support data
- `stcw_basic`
- `coc_document_gate`

The most up-to-date matrix snapshot is:

- `AI_Search_Results/email_hard_filter_validation_matrix_2026-05-07_post_coc_stcw.json`

## What Landed

### DOB / Age

The analyzer now handles repeated labeled DOB formats found in email resumes, including:

- ordinal month forms like `15th January 1995`
- `day of month year` forms like `04TH OF JUNE 1987`
- unambiguous labeled numeric forms like `19 /09/1996`

This improved email DOB extraction materially without relaxing ambiguous numeric DOB handling.

### Passport / Visa

The analyzer now handles repeated email-resume passport/visa layouts including:

- passport table rows where the second ordered date is the expiry date
- compact `U.S.A.VISA` tokens
- `ONLINE MARITIME CREW VISA` rows where the relevant dates appear before the visa label

This materially improved passport expiry extraction and visa recognition on email resumes without adding a separate email-only analyzer path.

### Current Rank

The analyzer now recognizes repeated email-resume current-rank patterns including:

- `POST APPLIED FOR`
- `APPLIED FOR`
- `Appraisee's Rank`
- `Position`
- common rank aliases like `OS`, `AB`, `A/B`, `2nd eng`, `3rd eng`, `4th eng`

This moved email current-rank extraction from a narrowly working path to a broadly usable one across the live email corpus.

### STCW Basic

The analyzer now handles repeated STCW email-resume variants including:

- punctuated abbreviations like `P.S.T`, `F.P.F.F`, `E.F.A`, `P.S.S.R`
- phrasing variants like `Personal Safety & Social Responsibility`
- certificate-table rows containing `CERT NO.` without incorrectly treating them as `No <certificate>`

This improved STCW basic extraction across multiple live folders while keeping the extractor conservative.

### COC

The analyzer now handles repeated email-resume COC variants including:

- dotted `C.O.C` labels
- engineer-grade variants like `MEO CL-2` and `MEO CL IV`
- label-adjacent grade cues like `Highest License Held ...`

This improved officer-side and engineer-side COC detection without broadening into speculative offshore/general-profile inference.

## Outcome Summary

Broad quality judgment after this round:

- email resumes still remain structurally noisier than SeaJobs-style resumes
- the largest repeated extractor gaps for active hard-filter families have been addressed
- the remaining misses are now more mixed and are much less clearly caused by one repeated parser defect

In practical terms, the validation result is:

- the active Phase 1 hard-filter families are now broadly validated on the email corpus
- residual misses remain, but they are no longer dominated by one clean repeated email-side layout bug

## Residuals Intentionally Left Alone

The following residuals were intentionally not expanded further in this round:

- offshore/general-profile resumes that do not actually surface a clear COC row
- resumes with sparse or mixed profile text where rank history appears but hard-filter evidence does not
- one-off `COC held` / `Highest License Held` layouts that do not repeat broadly enough to justify further heuristics beyond the narrow fixes already added
- remaining `UNKNOWN` STCW cases where source text is incomplete or mixed rather than clearly positive
- ambiguous numeric DOB values without a label-local disambiguation cue
- visa-missing folders where the resumes do not actually contain repeatable positive visa evidence

These were left alone intentionally because broadening further would likely trade conservative `UNKNOWN` behavior for avoidable false positives or false negatives.

## Recommended Stop Point

This round should be treated as closed for extractor tuning unless new corpus evidence appears.

The next work in this area should be one of:

- a closeout / acceptance snapshot using sampled end-to-end analyzer outcomes on email resumes
- a future evidence-driven extractor pass only if a new repeated miss pattern appears
- a separate product decision discussion for incomplete-source or OCR-like documents rather than more heuristic expansion
