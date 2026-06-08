# NjordHR Email Resume Intake Implementation Task Breakdown v0.1

## Status Snapshot (2026-04-27)
Completed in the current repo:
- T1 Outlook auth foundation
- T2 agent config and secure token storage
- T4 processed-item registry and dedupe
- T5 attachment download and local staging/original retention
- T7 rank resolver first working cut
- T8 canonical rank-folder routing
- T9 local manual-review / failed artifacts
- T10 agent status and fetch APIs
- T11 first operator UI surface via manual `Fetch from Outlook`

Validated working sample:
- manual fetch from `recruitment@njordships.com`
- source folder: `Inbox/NjordHR Resumes`
- final routed sample ranks:
  - `OS`
  - `2nd_Engineer`
  - `3rd_Engineer`
  - `4th_Engineer`
  - `Oiler`
  - `Wiper`

Current implementation reality:
- v1 is manual fetch, not background polling
- mailbox originals are left untouched by the app
- Outlook rule performs mailbox-side copy into the intake folder
- duplicate suppression is local-registry driven
- developer validation may use a local LibreOffice install
- customer releases must not require end users to install LibreOffice separately

## Purpose
Break the approved Outlook email intake spec into implementation tasks in strict execution order.

Source spec:
- [email-resume-intake-from-outlook-spec-v0.1.md](/Users/kartikraghavan/Tools/NjordHR/docs/email-resume-intake-from-outlook-spec-v0.1.md)

This task list is ordered by dependency and operational risk, not by UI visibility.

## Priority Order Summary
1. Graph app setup and auth foundation
2. Agent state model and config plumbing
3. Manual fetch and mailbox read path
4. Attachment download and local staging
5. Word-to-PDF conversion path
6. Rank classifier and canonical folder routing
7. Manual-review local queue and metadata sidecars
8. Agent health/status endpoints
9. Minimal operator visibility UI
10. End-to-end tests and rollout checks

## Task Breakdown

### T1. Register and wire the Outlook auth foundation
Priority:
- P0

Why first:
- nothing else can be tested against a real mailbox until auth works

Tasks:
- create or verify the NjordHR multitenant Microsoft Graph app
- register the fixed localhost PKCE redirect URI:
  - `http://localhost:53682/auth/outlook/callback`
- confirm required scopes:
  - `Mail.Read`
  - `Mail.ReadWrite`
  - `offline_access`
- choose the MSAL Python integration approach for the local agent
- document expected tenant-admin consent flow
- lock v1 mailbox-auth rule:
  - the signed-in Outlook account must be the actual mailbox being fetched
  - do not treat delegated access from a different user as the default v1 path

Done when:
- a user can complete Microsoft login and the agent receives a valid token via PKCE callback
- token refresh works without re-login during normal runtime

### T2. Add local agent config and secure token storage
Priority:
- P0

Why second:
- the poller and mailbox worker need stable local state before any sync logic is safe

Tasks:
- extend agent config/state model with Outlook intake settings
- add mailbox identity, monitored folder, processed folder, failed folder, polling interval
- add enable/disable flag for email intake
- implement MSAL token cache storage using OS-protected storage
  - Windows Credential Manager
  - macOS Keychain
- define the filesystem/state location for non-secret intake metadata

Done when:
- agent can persist and reload non-secret intake config across restarts
- token cache is not stored as plain config JSON

### T3. Implement the manual mailbox fetch path
Priority:
- P0

Why third:
- this is the core runtime behavior that turns a connected mailbox into intake events without adding background complexity

Tasks:
- build the email intake worker inside the existing local agent process
- add recruiter-triggered manual fetch from the Downloads tab
- read only the configured mailbox folder
- process supported attachments independently
- leave original Outlook mail untouched
- rely on local processed registry instead of mailbox-side message state mutation

Done when:
- the agent can fetch the configured folder on demand and route attachments into the local corpus safely

### T4. Implement processed-item registry and idempotency
Priority:
- P0

Why fourth:
- attachment download and resync are unsafe without duplicate suppression

Tasks:
- persist registry keyed by `message_id + attachment_id`
- add checksum-based secondary dedupe with 90-day window
- define and document registry storage format and location
- make lookups efficient enough for bounded recovery re-sync

Done when:
- restarting the agent does not re-ingest already processed attachments
- recovery re-sync does not duplicate local file admission

### T5. Download attachments to local staging
Priority:
- P1

Why now:
- once auth, polling, and dedupe exist, attachment acquisition can be implemented safely

Tasks:
- filter supported attachments from each email
- download supported files to `_EmailInbox_Staging`
- optionally preserve originals in `_EmailInbox_Originals`
- record metadata:
  - sender
  - subject
  - received time
  - original filename
  - source file type
  - message/attachment ids
- process each attachment independently within a single email

Done when:
- a mail with multiple attachments yields independent staged intake records
- one bad attachment does not block another

### T6. Implement Word-to-PDF conversion
Priority:
- P1

Why now:
- the downstream NjordHR workflow expects PDFs

Tasks:
- integrate LibreOffice headless conversion on the intake machine
- add converter availability checks at agent startup
- expose converter status in health output
- preserve original DOC/DOCX inputs
- route conversion failures to manual review or failed intake with explicit reason code
- add app-owned converter path resolution for packaged Windows and macOS builds
- prefer bundled converter paths before system-path fallback
- accept build-time converter payloads from `NJORDHR_BUNDLED_CONVERTER_SOURCE`
- allow release builds to fail closed with `NJORDHR_REQUIRE_BUNDLED_CONVERTER=true`

Done when:
- PDF files pass through untouched
- DOCX converts to PDF and remains traceable to original
- missing converter is surfaced clearly and does not silently drop files
- customer builds do not require a separate LibreOffice install to support DOCX/DOC conversion

### T7. Build the rank resolver
Priority:
- P1

Why this is isolated:
- this is the hardest business-logic task in the feature and should not be buried inside routing code

Tasks:
- build canonical rank dictionary from `[Ranks] rank_options`
- define explicit alias table for rank matching
- implement evidence scan in required order:
  - subject
  - body
  - attachment filename
  - resume text
- distinguish applied-rank evidence from present-rank evidence
- enforce v1 rule:
  - present-rank-only evidence does not auto-route
- implement three routing outcomes:
  - `classified`
  - `manual_review`
  - `failed`
- generate canonical folder slug using the same shared helper already used by SeaJobs download placement and local-agent resume routing

Required tests:
- subject-only exact match
- body-only exact match
- filename-only exact match
- resume-text exact or alias match
- conflicting rank evidence
- no rank evidence
- present-rank-only fallback
- canonical slug generation

Done when:
- resolver produces deterministic routing outcomes for the test set
- no second folder naming convention is introduced
- Outlook intake and SeaJobs placement both call the same slugging helper

### T8. Route normalized PDFs into canonical rank folders
Priority:
- P1

Why after resolver:
- folder routing depends entirely on the rank resolver outcome

Tasks:
- create missing rank folders automatically
- place normalized PDF into `Downloaded_Resumes/<RankFolder>/`
- generate deterministic email-ingest filenames:
  - `<RankFolder>_EMAIL_<short-stable-id>.pdf`
- ensure existing backend folder enumeration and AI search can see the files without special handling

Done when:
- email-ingested PDFs appear exactly like SeaJobs-downloaded PDFs to the existing folder-based flows

### T9. Implement local manual-review queue artifacts
Priority:
- P1

Why before UI:
- the UI needs a stable source of truth to show unresolved items

Tasks:
- route ambiguous items to `_EmailInbox_ManualReview`
- route failed items to `_EmailInbox_Failed`
- write sidecar metadata for manual-review items with:
  - original filename
  - sender
  - subject
  - received time
  - reason code
  - rank guesses
  - conversion status

Done when:
- local manual-review items are inspectable even before full UI polish

### T10. Add agent status and health APIs
Priority:
- P1

Why now:
- operator visibility and Electron/web UI both need runtime status

Tasks:
- extend `/health` with:
  - mailbox connection state
  - converter availability
  - last poll result
  - throttled state
  - full-resync warning state
  - manual-review counts
  - failed-intake counts
- add any small dedicated endpoints needed for manual-review item listing

Done when:
- the UI can render operational state without filesystem scraping

### T10.a Add manual fetch stream support
Priority:
- P1

Tasks:
- expose a fetch endpoint in the local agent
- expose a streaming/proxy endpoint through the backend
- surface progress logs in the Downloads tab

Done when:
- recruiter can trigger fetch and observe per-message outcome logs without opening local files

### T11. Add the minimum Phase B operator UI
Priority:
- P1

Why before release:
- Phase B explicitly must not ship without manual-review visibility

Tasks:
- add intake status block in admin/setup UI
- show:
  - intake enabled
  - monitored folder
  - last successful sync
  - processed counts
  - converter status
  - last error
- add manual-review list surface with minimum fields:
  - original filename
  - sender
  - subject
  - received time
  - reason code

Not in first cut:
- full in-app resolution workflow

Done when:
- operators can see what is stuck and why without going directly to the filesystem

### T12. Add backend ingest/event support for provenance
Priority:
- P2

Why later:
- local-first v1 can function before full backend enrichment, but provenance should still be captured before rollout

Tasks:
- add email-intake event types and payload handling
- store resume source type as `email_outlook`
- preserve source provenance in candidate/event history
- keep backend changes narrow and non-invasive

Done when:
- downstream records can distinguish SeaJobs resumes from email-ingested resumes

### T13. Add optional cloud upload path
Priority:
- P2

Why later:
- cloud upload is optional in v1 and should not block local-first operation

Tasks:
- upload originals and normalized PDFs when enabled
- keep cloud-disabled mode fully operational
- record object metadata and source linkage

Done when:
- cloud upload can be enabled without changing local-first routing behavior

### T14. End-to-end validation and rollout checklist
Priority:
- P0 before release

Tasks:
- validate PDF intake end-to-end
- validate DOCX intake end-to-end
- validate DOC intake end-to-end if included
- validate manual-review visibility
- validate restart safety for processed-item registry
- validate first-run no-backfill behavior
- validate delta token recovery
- validate Graph throttling backoff behavior
- validate rank-folder visibility in existing search/verification flows

Done when:
- all Phase B exit criteria from the main spec are satisfied

## Remaining Next Engineering Order
1. DOCX validation with working converter
2. bundled converter packaging for Windows and macOS Electron releases
3. DOC validation if still required in active rollout
4. broaden real-mail corpus validation and capture misses
5. tighten manual-review reason surfacing in UI
6. backend provenance support
7. optional cloud upload path
8. only after that, decide whether background polling is worth adding

## Recommended Implementation Sequence
Use this as the actual execution order:

1. T1 Outlook auth foundation
2. T2 agent config and secure token storage
3. T3 manual mailbox fetch path
4. T4 processed-item registry and dedupe
5. T5 attachment download and staging
6. T6 Word-to-PDF conversion
7. validate DOCX locally on the development machine
8. package bundled converter support for Windows and macOS customer builds
9. T7 rank resolver
10. T8 canonical rank-folder routing
11. T9 local manual-review artifacts
12. T10 health and status APIs
13. T11 minimum operator UI
14. T12 backend provenance support
15. T13 optional cloud upload path
14. T14 full validation pass

## Tasks That Must Not Be Merged Prematurely
These should be implemented as separate reviewable units:
- T3 poller loop
- T4 dedupe registry
- T6 conversion
- T7 rank resolver
- T11 minimum operator UI

Reason:
- each has different failure modes
- each needs targeted testing
- combining them will make debugging and review much slower
