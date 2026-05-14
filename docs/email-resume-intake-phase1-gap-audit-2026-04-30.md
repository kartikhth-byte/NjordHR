# NjordHR Outlook Intake Phase 1 Gap Audit

Date:
- 2026-04-30

Scope:
- audit the Outlook email resume intake work against:
  - `docs/email-resume-intake-from-outlook-spec-v0.1.md`
  - `docs/email-resume-intake-implementation-task-breakdown-v0.1.md`
- distinguish:
  - completed Phase 1 behavior
  - remaining true Phase 1 / pre-release work
  - later-phase or explicitly deferred work

## 1. Current status

The repo now appears materially complete for the core local-first v1 intake path:
- Outlook delegated auth foundation exists
- local agent config and token persistence exist
- manual recruiter-triggered fetch exists
- intake is scoped to `Inbox/NjordHR Resumes`
- processed-item registry and checksum dedupe exist
- staging, originals retention, and PDF routing exist
- converter lookup supports bundled path, system `PATH`, and macOS app-bundle fallback
- canonical rank-folder routing exists
- local manual-review and failed queues exist
- manual-review UI, open, and move-to-role flow exist
- fetch streaming/progress surfacing exists
- non-resume gating and unreadable-document gating are in place

The candidate-name and document-quality pass completed on 2026-04-30 should be treated as part of this Phase 1 baseline, not as a remaining gap. See:
- `docs/resume-quality-and-candidate-name-extraction-end-state-2026-04-30.md`

## 2. Completed against the task breakdown

The following items appear implemented in the current repo:
- `T1` Outlook auth foundation
- `T2` local agent config and secure token storage
- `T3` manual mailbox fetch path
- `T4` processed-item registry and idempotency
- `T5` attachment download and local staging/original retention
- `T6` converter resolution and conversion fallback behavior
- `T7` first-cut rank resolver
- `T8` canonical rank-folder routing
- `T9` local manual-review / failed artifacts
- `T10` health/status surface and manual-review endpoints
- `T10.a` manual fetch stream support
- `T11` minimum operator UI surface

## 3. Remaining true Phase 1 / release-blocking work

The remaining Phase 1 work is now mostly validation and packaging, not new product behavior.

### 3.1 DOCX end-to-end validation

Still needed:
- validate a real Outlook mailbox intake path for `DOCX -> PDF -> rank folder`
- confirm the manual-review fallback is correct when conversion fails

Why this is still open:
- both the spec snapshot and implementation breakdown still call out DOCX validation as unfinished
- the code path exists, but the repo does not yet document a final validated DOCX corpus pass

### 3.2 DOC validation, if rollout still requires legacy `.doc`

Still needed:
- decide whether active rollout still includes legacy `.doc`
- if yes, run the same end-to-end validation as DOCX
- if no, explicitly record that `.doc` support is deferred or not required for release

Why this is still open:
- `.doc` is still accepted by `SUPPORTED_ATTACHMENT_EXTENSIONS`
- the specs continue to treat DOC validation as a separate item rather than implied by DOCX coverage

### 3.3 Bundled converter release payload validation

Still needed:
- validate packaged Windows build behavior with bundled converter payload
- validate packaged macOS build behavior with bundled converter payload
- confirm the customer release path does not rely on a separately installed LibreOffice

Why this is still open:
- converter lookup order and bundled-path wiring are in code
- the docs still mark release payload provisioning and packaged fallback validation as incomplete

### 3.4 Broader noisy-mailbox corpus validation

Still needed:
- run a broader mailbox validation pass beyond the clean happy-path sample
- include noisier attachment mixes:
  - PDF
  - DOCX
  - DOC if in scope
  - ambiguous/manual-review items
  - failed/unreadable items
  - internal non-resume attachments

Why this is still open:
- the current validated sample looks good
- the specs still call out broader corpus validation as unfinished
- this is the main remaining way to discover non-obvious routing or conversion regressions before release

## 4. Important items that are not Phase 1 blockers

These should not be treated as mandatory remaining Phase 1 scope unless product explicitly reopens them.

### 4.1 Backend provenance enrichment

Status:
- still listed as incomplete in the spec/task breakdown

Judgment:
- this is `T12` / `P2`
- useful, but not required to declare the local-first manual Outlook intake path operational

### 4.2 Optional cloud upload path

Status:
- still not complete

Judgment:
- this is `T13` / `P2`
- explicitly optional in the spec

### 4.3 Background polling / automatic sync loop

Status:
- not implemented

Judgment:
- not a gap for accepted v1
- the accepted v1 operating mode is manual fetch, not background polling

### 4.4 Outlook mailbox-side processed/failed moves

Status:
- app still leaves original mail untouched

Judgment:
- not a current gap
- the accepted implementation snapshot explicitly says attachment handling is copy-only and mailbox originals remain untouched

## 5. Notes on partial vs complete items

Some task-detail bullets are only partially reflected in the current repo, but do not currently justify broadening scope:
- the agent `/health` surface includes mailbox/auth and converter state, but not every richer status concept listed in the early task draft such as explicit throttled/full-resync fields
- the UI already shows manual-review items and reasons, so manual-review visibility is materially present even if wording/polish could still improve

These should be treated as follow-up polish only if validation reveals an operational blind spot.

## 6. Recommended next engineering order

1. Run a DOCX-focused end-to-end validation pass and save the artifact.
2. Decide whether legacy `.doc` remains in release scope.
3. If `.doc` remains in scope, run the DOC end-to-end validation pass.
4. Validate packaged bundled-converter behavior on Windows and macOS release builds.
5. Run one broader noisy-mailbox validation sweep and capture the outcome distribution.
6. Stop if those checks pass; do not reopen routing or extraction heuristics without new evidence.

## 7. Practical conclusion

For Outlook intake, the remaining work is no longer "build the feature."

The remaining work is:
- validate the Word conversion path end to end
- validate release packaging
- validate a broader real-mail corpus

If those checks pass, Phase 1 for the Outlook intake feature can reasonably be treated as complete without waiting for provenance enrichment, cloud upload, or background polling.
