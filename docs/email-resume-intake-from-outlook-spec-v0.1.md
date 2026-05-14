# NjordHR Email Resume Intake Spec v0.1

## Implementation Status Snapshot (2026-04-27)
Current implemented state in the repo:
- Outlook/Microsoft Graph auth foundation is working
- local agent config and token persistence are working
- manual `Fetch from Outlook` flow is working from the Downloads tab
- intake reads only from `Inbox/NjordHR Resumes`
- attachment handling is copy-only; the app does not move or delete the original Outlook mail
- final routing reuses the same canonical rank-folder structure used by SeaJobs intake
- processed-item registry and checksum dedupe are working
- manual review and failed local queues exist and are wired
- packaged runtime wiring now supports an app-owned bundled converter resource via `NJORDHR_BUNDLED_CONVERTER_DIR`
- converter lookup order is now:
  - bundled app resource
  - system `PATH`
  - macOS LibreOffice app-bundle fallback

Validated sample result:
- tested PDF sample set from `recruitment@njordships.com` routed successfully into:
  - `OS`
  - `2nd_Engineer`
  - `3rd_Engineer`
  - `4th_Engineer`
  - `Oiler`
  - `Wiper`
- duplicate copies in the mailbox intake folder were correctly skipped
- no items remained in manual review for the final validated sample batch

What is not yet complete:
- DOCX validation
- DOC validation
- converter availability / fallback validation on both platforms
- customer release payload provisioning of the bundled converter binaries themselves
- broader corpus validation across noisier mailbox samples
- backend provenance enrichment
- optional cloud upload path
- any background poller / automatic sync loop

## 1. Purpose
Define a second inbound resume flow for candidates who send resumes by email instead of through SeaJobs.

This flow must:
- automatically ingest resume attachments from Outlook/Microsoft 365
- support PDF and Word attachments
- convert to PDF when needed
- classify each resume into the correct rank folder inside the existing download hierarchy
- feed the resume into the same downstream search, verification, dashboard, and recruitment workflow already used for SeaJobs-downloaded resumes

This document is a product and architecture spec only. It does not authorize implementation yet.

## 2. Problem Statement
Current NjordHR intake assumes resumes originate from SeaJobs downloads. That leaves a missing operational path:
- recruiters also receive direct resumes over email
- attachments arrive outside the SeaJobs scraping flow
- attachments may be PDF, DOCX, or DOC
- these resumes still need to land in the same rank-based folders and then follow the normal pipeline

Without a first-class email intake flow:
- recruiters manually download attachments
- file naming and folder placement are inconsistent
- resumes bypass the standard ingestion trail
- dashboard and candidate history become incomplete

## 3. Goals
- Add an automated Outlook intake path without breaking the existing SeaJobs flow.
- Reuse the current local-agent and backend ingest model rather than creating a separate resume system.
- Preserve the current rank-folder-based downstream process.
- Support both Windows and macOS.
- Keep privacy-sensitive file access local-first.
- Allow optional cloud canonical storage for auditability and multi-device visibility.
- Define a practical v1 operating model for multi-recruiter use without solving full multi-device corpus synchronization yet.

### 3.1 Accepted v1 operating mode
The accepted first implementation cut is:
- manual recruiter-triggered fetch from the Downloads tab
- not a background poller
- Outlook rule or manual mailbox triage copies likely candidate mails into `Inbox/NjordHR Resumes`
- the app fetches from that folder on demand

This keeps v1 operationally simple while preserving the ability to add background polling later.

## 4. Non-Goals
- Replacing SeaJobs scraping.
- Full email client support beyond Outlook/Microsoft 365 in v1.
- Auto-ingesting inline email body text as a substitute for missing attachments.
- Perfect rank classification from every attachment on day one.
- End-user mail-rule authoring inside v1.

## 5. Key Decisions

### 5.1 Integration boundary
The **local agent** will own email intake.

Reason:
- email attachment download is operationally similar to local scraping and local folder writes
- the local agent already owns filesystem access, retry queues, and cloud sync
- this avoids letting the cloud backend write directly to local folders

### 5.2 Outlook integration strategy
Use **Microsoft Graph Mail API with delegated OAuth** via the local agent.

Do not use:
- Outlook COM automation: Windows-only, not cross-platform
- Apple Mail scripting: macOS-only, not cross-platform
- raw IMAP as the primary integration: weaker fit for Microsoft 365 auth, folder semantics, and future enterprise governance

Reason:
- Graph is cross-platform
- works for Outlook/Microsoft 365 mailboxes
- supports folder scoping, attachment retrieval, and message metadata
- aligns with a future enterprise deployment model better than client-specific automation

### 5.2.1 Mailbox model for v1
For v1, support **one connected mailbox per local agent instance**.

Reason:
- keeps setup and failure handling simple
- avoids ambiguous ownership across multiple mailboxes on one machine
- makes polling, dedupe, and processed-message tracking easier to reason about

This mailbox may be:
- an individual recruiter mailbox, or
- a shared team mailbox

but only one mailbox is active per intake machine in v1.

Accepted v1 choice:
- use a shared company mailbox, for example `careers@njordshipping.com`

### 5.3 Storage strategy
The answer to “can this folder be on cloud?” is:

- **Canonical copy in cloud**: yes
- **Working watch/processing folder**: still local

Reason:
- Outlook attachment retrieval and conversion happen on the local machine
- the existing downstream process expects local rank folders
- cloud storage is still valuable as the system-of-record copy and for audit/recovery

Therefore the v1 design is:
- local staging + local rank folders for processing
- optional or default cloud mirror for originals and normalized PDFs

### 5.4 Conversion strategy
For non-PDF documents, use **local document conversion in the agent**.

Preferred converter:
- `LibreOffice` in headless mode

Why:
- cross-platform
- supports DOCX and legacy DOC better than pure Python extraction alone
- does not depend on Microsoft Word being installed

Operational rule:
- if conversion is available, convert DOC/DOCX/DOC to PDF before final placement
- if conversion is unavailable or conversion fails, route the resume to a manual-review queue rather than guessing

### 5.4.1 Conversion pipeline contract
The conversion contract for v1 is:
- original attachment is preserved as the source artifact when configured
- normalized PDF is the only file admitted into the standard rank-folder corpus
- downstream search, verification, and preview continue to operate on PDF as they do today

This keeps the existing NjordHR resume-processing assumptions intact.

### 5.4.2 Explicit fallback if LibreOffice is unavailable
LibreOffice availability is not guaranteed in every deployment and bundling it may be rejected because of package size, platform handling complexity, or startup cost.

Therefore the required fallback behavior for v1 is:
- PDF attachments continue normally
- DOCX/DOC attachments are still downloaded and preserved as originals
- if no working converter is available, DOCX/DOC attachments do **not** enter the rank-folder PDF corpus
- those files route directly to manual review or failed intake with a clear reason code such as `converter_unavailable`

This fallback is mandatory and does not depend on whether LibreOffice is bundled or only documented as a prerequisite.

### 5.5 Classification policy
Rank classification must be conservative.

Order of evidence:
1. explicit rank from structured email subject/body patterns
2. explicit rank extracted from resume text
3. present-rank extraction when product rules allow it to stand in for applied rank
4. manual review queue if confidence is low or rank is ambiguous

Do not silently force uncertain resumes into a rank folder.

### 5.5.1 Rank detection strategy
`rank applied for` is the highest-risk classification step in this feature and must follow a bounded resolver rather than open-ended fuzzy guessing.

The v1 resolver should work in this order:
1. email subject exact or alias match against configured rank labels
2. email body exact or alias match against configured rank labels
3. attachment filename exact or alias match against configured rank labels
4. resume text match against configured rank labels using explicit alias tables
5. fallback to present-rank extraction only if product approves that behavior for v1 and the confidence is high
6. otherwise route to manual review

Rules:
- the canonical source of allowed ranks is the configured `[Ranks] rank_options`
- no unconstrained fuzzy rank matching in v1
- if multiple rank candidates are found with similar strength, route to manual review
- if no candidate is found, route to manual review

### 5.5.2 Rank routing decision model
The routing model for v1 should be binary at the decision point:
- `classified`
- `manual_review`
- `failed`

Auto-route only when all of the following are true:
- exactly one canonical rank is matched
- no conflicting rank evidence exists across subject, body, filename, and resume text
- the winning rank comes from either:
  - explicit applied-rank evidence in subject/body/filename, or
  - a single high-confidence resume-text rank match

Otherwise:
- ambiguous or weak rank evidence -> `manual_review`
- unreadable file, failed extraction, or failed conversion with no usable PDF -> `failed`

Confidence can still be stored internally for diagnostics, but v1 routing behavior must follow the three-outcome model above.

### 5.6 v1 multi-system operating model
For v1, NjordHR will use a **single designated intake/index machine** for each active resume corpus.

That means:
- SeaJobs downloads happen only on the intake machine
- Outlook email attachment ingestion happens only on the intake machine
- AI search that depends on the raw downloaded corpus runs only on the intake machine
- other recruiter systems can still view shared verified/recruitment state, but are not treated as raw-intake machines

Reason:
- this matches the current local-folder-first architecture
- it avoids duplicate raw corpus drift across recruiter machines
- it defers full cloud-first file canonicalization until after the workflow is validated in production use

## 6. Proposed User Flow

### 6.1 Admin setup
Admin configures:
- Outlook mailbox connection
- email folder to monitor, for example `Inbox/NjordHR Resumes`
- accepted attachment types
- whether cloud upload is enabled
- whether processed emails should be moved to another Outlook folder
- polling interval
- whether originals should be retained locally
- whether DOC legacy files are allowed

Accepted v1 operational folders:
- monitored folder: `Inbox/NjordHR Resumes`
- processed folder: `Inbox/NjordHR Processed`
- failed folder: `Inbox/NjordHR Failed`

### 6.1.1 Recommended v1 default settings
Recommended defaults:
- mailbox: one connected mailbox
- monitored folder: `Inbox/NjordHR Resumes`
- processed folder: `Inbox/NjordHR Processed`
- failed folder: `Inbox/NjordHR Failed`
- polling interval: 60 seconds
- allowed file types: PDF, DOCX, DOC
- local original retention: enabled
- cloud upload: enabled when available

Important:
- because the recommended default moves processed emails to another folder, `Mail.ReadWrite` is a required Microsoft Graph permission in the default configuration

### 6.2 Runtime intake flow
1. Recruiter triggers a manual fetch from the Downloads tab.
2. Local agent reads the configured Outlook folder through Microsoft Graph.
3. Agent filters for allowed attachments.
4. Agent saves attachments to a local staging/originals area.
5. Agent computes checksum and message identity metadata.
6. Agent converts non-PDF files to PDF if possible.
7. Agent extracts text and classifies the candidate’s applied rank.
8. Agent places the normalized PDF into the corresponding rank folder under the downloads hierarchy.
9. Agent emits ingest events to backend/cloud sync when applicable.
10. Resume enters the normal NjordHR pipeline.

### 6.2.a Current implemented v1 behavior
Current behavior differs intentionally from the earlier polling-oriented design:
- manual fetch only
- no automatic background mailbox polling
- no automatic message move to `Processed` or `Failed`
- mailbox originals remain untouched by the app
- duplicate prevention is handled locally by the processed registry

The Outlook mailbox rule is responsible only for copying likely resume mails into `Inbox/NjordHR Resumes`.

### 6.2.b Future enhancement path
Background polling, delta-token sync, and mailbox move/copy automation remain valid later-phase enhancements, but they are not required for the current working v1 implementation.

### 6.2.1 Processing unit
The processing unit is:
- one Outlook message
- zero or more supported attachments

Each supported attachment is processed independently.

This means:
- one email with three resume attachments produces three intake records
- one failed attachment does not block the other attachments in the same email

### 6.2.2 Polling and Graph change tracking
The intake worker should use Microsoft Graph incremental sync where possible rather than re-reading the full folder every cycle.

Primary mechanism:
- message delta tracking for the configured folder

Required local state:
- last successful delta token
- last successful poll timestamp
- processed attachment registry

If the delta token is rejected or expired:
- the worker must treat that as a distinct recovery path
- it must perform a bounded re-sync of the monitored folder
- it must rely on the processed-item registry to avoid duplicate attachment admission
- it must emit an operator-visible warning that a full folder re-sync occurred

### 6.2.3 First-run sync policy
On first enablement, when no delta token exists yet, v1 should **not** retroactively process the full historical mailbox folder by default.

Default first-run behavior:
- establish the initial Graph tracking state for the configured folder
- treat only messages received after intake is enabled as eligible for automatic processing

Optional future enhancement:
- an explicit admin-triggered backfill action for historical messages

Reason:
- avoids flooding rank folders and manual review with legacy email
- keeps first production rollout operationally predictable
- matches the conservative v1 design

### 6.3 Multi-recruiter behavior in v1
If multiple recruiters use NjordHR across multiple systems:
- only the designated intake machine should poll Outlook for resumes
- only the designated intake machine should run SeaJobs resume downloads
- non-intake systems should not build their own independent raw resume corpus
- non-intake systems operate as review, verification, dashboard, and recruitment clients against shared application state

Accepted v1 deployment choice:
- the designated intake/index machine is one team-owned Windows machine

## 7. End-to-End Processing Model

### 7.1 Local folders
Under the existing downloads root, introduce dedicated intake folders:
- `Downloaded_Resumes/<Rank>/...` for final rank placement
- `Downloaded_Resumes/_EmailInbox_Staging/` for freshly downloaded attachments
- `Downloaded_Resumes/_EmailInbox_Failed/` for failed conversion/extraction
- `Downloaded_Resumes/_EmailInbox_ManualReview/` for ambiguous rank classification
- `Downloaded_Resumes/_EmailInbox_Originals/` optional, for preserving source files locally

### 7.1.1 Canonical rank-folder naming convention
The email intake flow must reuse the same rank-folder slugging convention already used by SeaJobs downloads and the local agent:

- canonical label example: `Chief Engineer`
- folder slug: `Chief_Engineer`

Normalization rule:
- replace spaces with underscores
- replace `/` with `-`
- do not invent a second folder naming scheme for email intake
- implement this through the same shared helper used by SeaJobs download placement and local-agent resume routing
- do not duplicate slugging logic in a second Outlook-specific code path

Examples:
- `2nd Engineer` -> `2nd_Engineer`
- `Chief Officer` -> `Chief_Officer`
- `NCV/NWKO` -> `NCV-NWKO`

If the target rank folder does not already exist:
- create it automatically using the canonical slug
- do not wait for SeaJobs to create it first

This is required so that:
- `get_rank_folders`
- `get_rank_folder_files`
- `get_rank_folder_ship_types`
- AI search
- verification flows

all continue to recognize email-ingested resumes exactly like SeaJobs-downloaded resumes.

### 7.1.1.a Mailbox identity rule for v1
For v1, the Outlook account connected through Microsoft sign-in must be the actual mailbox account being fetched.

Examples:
- if intake is reading `recruitment@njordships.com`, the connected account should be `recruitment@njordships.com`
- do not rely on a different signed-in user with possible delegated access as the default v1 model

This constraint is intentional for v1 because:
- mailbox permissions are easier to reason about
- manual smoke testing is less ambiguous
- operational failures are easier to diagnose than delegated-access edge cases

Delegated mailbox access can be considered later, but it is out of scope for the first implementation cut.

### 7.1.2 Filename convention for email-ingested resumes
SeaJobs files often already carry a candidate id in the filename. Email attachments may not.

For email-ingested normalized PDFs, use a deterministic filename convention:
- `<RankFolder>_EMAIL_<short-stable-id>.pdf`

Where `short-stable-id` is derived from:
- message id + attachment id hash prefix, or
- sender + received timestamp + attachment checksum hash prefix

Examples:
- `Chief_Officer_EMAIL_a13f9c42.pdf`
- `2nd_Engineer_EMAIL_7db92e11.pdf`

Reason:
- stable enough for dedupe/audit
- safe for filesystem use
- compatible with existing folder scanning
- does not require a SeaJobs candidate id to exist

### 7.2 Cloud objects
Recommended buckets or paths:
- `incoming-email-resumes/originals/...`
- `incoming-email-resumes/normalized-pdf/...`
- existing canonical `resumes/...` path for resumes that have entered the standard downstream process

### 7.3 v1 corpus ownership rule
In v1, the local folder on the designated intake machine is the operational source for:
- newly downloaded SeaJobs resumes
- newly ingested Outlook attachments
- local PDF conversion outputs
- local AI-search corpus refreshes

Other systems may view downstream shared state, but should not be assumed to have the same local raw resume set.

Cloud storage metadata should include:
- mailbox id
- message id
- attachment id
- original filename
- normalized filename
- checksum
- detected format
- conversion status
- rank classification result

### 7.2.1 Original vs normalized cloud objects
If cloud upload is enabled, store:
- original attachment object
- normalized PDF object

The normalized PDF is the file that should align with the local rank-folder corpus.
The original attachment is retained for audit and reprocessing.

## 8. Data and Event Model

### 8.1 New source type
Add a new resume source classification:
- `resume_source_type = seajobs | email_outlook | manual_upload`

### 8.2 New agent event types
Add event types:
- `email_resume_detected`
- `email_attachment_downloaded`
- `email_attachment_converted`
- `email_rank_classified`
- `email_rank_unclassified`
- `email_resume_routed`
- `email_resume_failed`

### 8.2.1 Required event payload fields
Each email intake event should include, where applicable:
- `mailbox_id`
- `mail_folder_id`
- `message_id`
- `internet_message_id` if available
- `attachment_id`
- `sender_email`
- `subject`
- `received_at`
- `original_filename`
- `normalized_pdf_filename`
- `attachment_checksum_sha256`
- `source_file_type`
- `conversion_status`
- `rank_detected_label`
- `rank_detected_folder`
- `rank_detection_confidence`
- `routing_outcome`

### 8.3 Identity and dedupe
A resume ingested from email needs stable identity keys:
- `mailbox_id`
- `message_id`
- `attachment_id`
- `attachment_checksum_sha256`

Dedupe rules:
- do not ingest the same attachment twice if `message_id + attachment_id` already exists
- also dedupe on checksum within a **90-day window** to catch forwarded/resubmitted duplicates without treating identical resumes as permanently non-ingestable

Rationale:
- `message_id + attachment_id` is the primary idempotency key for the same mail artifact
- checksum dedupe is a secondary operational guard for near-term forwards and duplicate submissions
- a 90-day window is long enough to suppress obvious duplicate churn while still allowing a materially later resubmission to re-enter the workflow if desired

### 8.4 v1 duplicate tolerance
For v1, duplicate **local files across multiple machines** are not the primary concern because only one machine should perform intake.

The main v1 consistency rule is:
- shared verified/recruitment state must remain centralized and viewable from all systems
- raw intake duplication is controlled operationally by the single-intake-machine policy

Cross-device canonical file dedupe remains a later hardening phase for both SeaJobs and email intake sources.

## 9. Rank Classification Rules

### 9.1 Evidence sources
Classification should use:
- email subject
- email body
- attachment filename
- resume extracted text

### 9.1.1 Canonical rank dictionary
The classifier must use a canonical rank dictionary built from:
- configured `[Ranks] rank_options`
- an explicit alias map maintained in code or config

It must not infer arbitrary unseen rank spellings in v1.

### 9.1.2 Applied-rank vs present-rank distinction
The preferred target is `rank applied for`, not merely `present rank`.

Interpretation rules:
- if the email subject/body explicitly says `applying for`, `applied for`, `for the rank of`, or similar, that evidence outranks resume present-rank text
- if only present-rank evidence exists and no applied-rank evidence is found, classify as `present-rank fallback`
- product should be able to disable present-rank fallback if it causes operational noise

### 9.2 Confidence model
Three outcomes only:
- `classified`
- `manual_review`
- `failed`

Do not use an aggressive best-effort auto-route for ambiguous cases.

### 9.3 Folder routing behavior
- `classified`: place in `Downloaded_Resumes/<Rank>/`
- `manual_review`: place in `Downloaded_Resumes/_EmailInbox_ManualReview/`
- `failed`: place in `Downloaded_Resumes/_EmailInbox_Failed/`

### 9.3.1 Manual review metadata sidecar
For manual-review items, store a sidecar JSON record with:
- original file name
- detected source type
- sender
- subject
- received time
- candidate rank guesses
- confidence values
- conversion outcome
- error messages if any

This avoids forcing reviewers to reconstruct why the resume landed in manual review.

### 9.4 Normalization
Rank names must reuse the same canonical rank labels and rank-folder slugging already used elsewhere in NjordHR.

## 10. File Format Handling

### 10.1 Supported in v1
- `.pdf`
- `.docx`
- `.doc`

Accepted v1 file-type scope:
- `PDF + DOCX + DOC`

### 10.2 Rejected in v1
- password-protected archives
- image-only formats without OCR support
- nested ZIP/RAR bundles unless explicitly enabled later

### 10.3 Conversion outcomes
- PDF input:
  - no conversion required
- DOCX/DOC input:
  - convert to PDF via local converter
  - preserve original as source artifact when configured
- conversion failure:
  - move to failed/manual-review area
  - do not drop the file

### 10.4 Conversion dependency behavior
If LibreOffice is the selected converter:
- the agent must health-check its availability at startup and expose status in `/health`
- admin UI must show whether Word-to-PDF conversion is operational
- if converter is missing, DOC/DOCX intake should still download originals but route them to failed/manual-review with a clear reason

## 11. Outlook/Microsoft Graph Authentication

### 11.1 Auth model
Use delegated user consent in the local agent with **Authorization Code Flow with PKCE**.

Recommended auth flow:
- Authorization Code Flow with PKCE for desktop/Electron UX and security

Implementation guidance:
- use `msal` / `msal-python`
- do not use device code flow as the primary implementation for Electron

Reason:
- NjordHR already has a full desktop UI and browser surface
- PKCE is the recommended native-app pattern
- it avoids the weaker user experience of manual device-code entry
- it does not require shipping a client secret

Redirect handling requirement:
- the PKCE flow is expected to use a localhost redirect URI pattern for the local agent callback
- the Azure AD / Entra application registration must include the required localhost redirect URI pattern used by the agent
- this redirect registration is part of Phase A validation because the auth flow will fail at callback time if it is omitted

Accepted v1 redirect decision:
- use a fixed localhost callback URI
- recommended value: `http://localhost:53682/auth/outlook/callback`

V1 rationale:
- simpler app registration
- simpler agent implementation
- easier operational debugging
- acceptable tradeoff versus dynamic callback-port discovery

### 11.1.1 Azure AD / Microsoft Entra app registration risk
Microsoft Graph mailbox access requires an Azure AD / Microsoft Entra application registration. This is not just a configuration detail; for enterprise Microsoft 365 tenants it can be a deployment blocker.

Accepted v1 deployment decision:
- use a **NjordHR-owned multitenant app registration**
- the customer tenant admin grants consent to that app for mailbox access

Operational implication:
- enterprise tenants may require IT admin approval before any mailbox access is possible
- this must be treated as a **Phase A blocking validation**, not a routine setup checkbox
- expected customer deployment must confirm that tenant-admin consent for the NjordHR multitenant app is feasible before implementation proceeds

### 11.2 Required permissions
Expected Graph scopes, subject to final security review:
- `Mail.Read`
- `offline_access`
- `Mail.ReadWrite` because the recommended default behavior moves processed emails to another folder

### 11.3 Token storage
Store tokens locally under agent ownership, but **not** as a plain JSON file in the config directory.

Implementation guidance:
- use `msal` token cache serialization
- protect the cache with the OS credential store
  - Windows: Credential Manager
  - macOS: Keychain

This is required because Microsoft refresh tokens are long-lived, high-value credentials.

### 11.4 Mailbox scope
v1 should support:
- a single mailbox per local agent instance
- a configured target folder inside that mailbox

### 11.5 Connection ownership
The Outlook connection belongs to the local agent, not the backend browser session.

That means:
- auth token lifecycle is handled by the local agent
- intake keeps running even if the frontend window is closed, subject to app/runtime policy
- the backend/frontend reads intake status from agent APIs rather than talking to Graph directly

## 12. UX Requirements

### 12.1 New setup/admin area
Add a dedicated section for email intake settings:
- connect Outlook
- choose mailbox/folder
- test connection
- enable or disable intake
- show last poll time
- show last processed email count
- show error state
- show converter availability
- show polling interval
- show connected mailbox identity

### 12.1.1 Phase B setup path
The full polished admin setup screen is not required to block Phase B backend/agent development, but Phase B must still define how intake is configured and tested.

Minimum acceptable Phase B setup path:
- a documented developer/admin configuration flow using agent settings and/or a minimal setup surface
- the operator must be able to:
  - connect the mailbox
  - choose the monitored folder
  - enable intake
  - verify converter status

This must be documented explicitly as part of the Phase B deliverable even if the final polished UI lands in Phase C.

### 12.2 Intake visibility
The UI should expose:
- intake enabled/disabled
- monitored mailbox/folder
- last successful sync
- pending manual review count
- failed conversion count
- processed email count
- processed attachment count
- last error reason
- whether a full folder re-sync was triggered because the delta token expired

### 12.3 Manual review surface
Manual review needs a queue showing:
- original filename
- sender
- subject
- received time
- extracted rank hints
- current routing status
- action to assign rank and move into the correct folder

## 13. Local Agent Changes

### 13.1 New worker
Add an `email intake worker` inside the local agent.

Responsibilities:
- poll Graph
- download attachments
- maintain processed-message state
- convert documents
- classify rank
- route files locally
- emit sync events

### 13.1.1 Process model
This should be a **separate background worker inside the local agent process**, not a backend-server process and not a separate OS service in v1.

Reason:
- the agent already owns local filesystem writes
- the agent already owns retry-safe sync behavior
- this avoids introducing a second local daemon before the workflow is validated

### 13.1.2 Polling cadence
Recommended v1 polling cadence:
- default interval: 60 seconds
- minimum allowed interval: 30 seconds
- maximum allowed interval: 15 minutes

The poller should:
- back off automatically after repeated failures
- resume normal cadence after a successful cycle
- avoid concurrent overlapping poll cycles
- honor Graph `Retry-After` throttling guidance when rate limited

### 13.2 New local state
Agent state should persist:
- connected mailbox metadata
- token cache
- last successful poll cursor/delta token
- processed attachment registry
- conversion failures
- manual review queue state

### 13.2.1 Processed-item registry
Persist a processed-item registry keyed by:
- `message_id + attachment_id`

This registry is required so the poller can:
- skip already-processed attachments
- remain idempotent across app restarts
- survive transient failures without re-downloading everything

Implementation requirement:
- the registry must persist in agent-owned local state
- it must survive agent restarts
- it must support efficient duplicate checks during normal polling and recovery re-sync
- the chosen storage format and filesystem location must be documented in the Phase B deliverable

### 13.2.2 Delta token expiry handling
Microsoft Graph delta tokens may expire after inactivity or mailbox state churn.

Required behavior when the stored delta token is rejected, including 410-style expiry responses:
- clear the saved delta token
- emit an operational warning event
- perform a bounded re-sync from the monitored folder
- rely on the processed-item registry for duplicate suppression
- persist a new delta token after a successful recovery cycle

### 13.3 Concurrency
Email intake should run independently from:
- SeaJobs OTP session flow
- SeaJobs resume download jobs

But both should write into the same final rank-folder hierarchy and backend ingest stream.

## 14. Backend Changes

Backend changes should remain narrow:
- accept and store new email-intake event types
- surface intake status in runtime/admin APIs
- expose manual-review queue APIs if UI review is handled centrally
- preserve the existing downstream candidate workflow

The backend should not directly fetch email attachments in v1.

### 14.1 New backend responsibilities
Backend responsibilities in v1 should be limited to:
- receiving and storing email-intake event metadata
- surfacing operational status to the UI
- exposing manual-review items if the UI needs centralized visibility
- preserving source provenance in candidate/event history

Accepted v1 manual-review scope:
- ambiguous items remain local/manual-review items in v1
- do not implement a central backend resolution queue in the first rollout

## 15. Cloud Storage and Canonical Folder Model

### 15.1 Recommended model
Use cloud as canonical storage, local as processing workspace.

That means:
- local folder remains operationally required
- cloud folder is the durable copy
- final recruitment flow may still read local rank folders for compatibility during v1

Accepted v1 storage choice:
- email-ingested resumes remain local-first on the designated intake machine
- cloud upload of email-ingested resumes is optional in v1, not mandatory

### 15.2 Why not cloud-only processing
Pure cloud-only folder processing is not recommended for v1 because:
- Outlook attachment access originates in the local agent
- conversion is local
- the current downstream flow is local-folder-centric
- cloud-only would require broader changes to search/verification/recruitment logic

### 15.3 Planned evolution after v1 validation
The intended post-v1 direction is:
- store newly ingested resumes in canonical cloud storage first
- then optionally sync or cache copies onto each NjordHR machine
- move search/indexing toward a cloud-backed or cloud-synchronized corpus model

This should be treated as a later platform phase, not part of the first Outlook intake release.

## 16. Failure Handling

### 16.1 Failure classes
- Graph auth expired
- Azure AD / tenant consent not granted
- mailbox folder missing
- attachment download failed
- unsupported file type
- document conversion failed
- rank classification ambiguous
- local folder write failed
- cloud upload failed
- Graph delta token expired or invalidated
- Graph API rate limited / throttled

### 16.2 Required behavior
- never lose the original attachment silently
- always produce an operator-visible status
- keep retry-safe idempotency
- separate transient retry errors from manual-review business errors

Specific behaviors:
- Graph throttling:
  - honor `Retry-After` if present
  - suspend the next poll accordingly
  - surface the throttled state in agent health and UI status
- delta token expiry:
  - warn visibly
  - recover through bounded re-sync
  - suppress duplicates using the processed-item registry

## 17. Security and Compliance
- Keep mailbox access delegated and least-privileged.
- Do not expose public resume URLs.
- Do not write email credentials into `config.ini`.
- Store local auth/token material in agent-owned config/state, not in frontend local storage.
- Log metadata, not full email bodies, unless explicitly required.
- Do not ingest non-attachment email content into search by default.

## 18. Recommended Phase Sequence

### Phase A: Spec and dependency validation
- confirm Microsoft 365 mailbox assumptions
- confirm tenant-admin consent path for the NjordHR multitenant app with the expected customer
- confirm converter packaging direction for customer releases
- confirm which machine is the designated intake/index machine for each active recruiter workflow
- confirm exact rank alias source and maintenance owner

Phase A exit criteria:
- expected customer tenant can actually authorize Graph mailbox access to the NjordHR multitenant app
- PKCE auth flow and redirect handling approach are agreed
- conversion contingency is accepted if LibreOffice is unavailable

Customer answers already resolved for Phase A / v1 operating model:
- tenant-admin consent is considered feasible
- mailbox type: shared company mailbox
- designated intake machine: one team-owned Windows machine
- monitored folder: `Inbox/NjordHR Resumes`
- processed folder: `Inbox/NjordHR Processed`
- failed folder: `Inbox/NjordHR Failed`
- supported file types: `PDF + DOCX + DOC`
- developer validation may use a local LibreOffice install
- present-rank-only evidence must not auto-route
- first cut manual-review scope is visibility only

### Phase B: Local intake MVP
- Graph auth in local agent
- folder polling
- attachment download
- PDF pass-through
- DOCX/DOC conversion
- local rank classification
- local routing into rank folders
- event logging
- document single-machine intake operation as a v1 operational rule; do not build distributed enforcement in Phase B
- add intake status/health endpoints
- add deterministic filename generation
- add processed-item registry

Phase B implementation note:
- rank classification is the highest-risk logic area in this feature
- it must not be treated as a trivial parser task
- before Phase B is considered done, the resolver must have a defined test set covering:
  - subject-only exact match
  - body-only exact match
  - filename-only exact match
  - resume-text exact or alias match
  - conflicting rank evidence
  - no rank evidence
  - present-rank-only fallback case
  - canonical folder slug generation from the winning rank

Phase B exit criteria:
- a PDF attachment is ingested and routed end-to-end into the correct canonical rank folder
- a DOCX attachment is converted to PDF and routed end-to-end into the correct canonical rank folder
- a manual-review item is visible in the UI with:
  - original filename
  - sender
  - subject
  - received time
  - reason code
- the processed-item registry survives an agent restart and prevents known attachments from being re-ingested
- the agent `/health` endpoint reflects mailbox connection state and converter availability correctly
- first-run enablement does not bulk-ingest historical messages from the monitored folder
- delta-token recovery re-sync avoids duplicate local admission for previously processed attachments

Phase B must not ship without at least minimal operator visibility for manual-review items.

### Phase C: UI and manual review
- admin setup screen
- intake status indicators
- manual-review queue

Minimum release rule:
- Phase B and Phase C should ship together, or
- Phase B must include a basic in-app manual-review visibility surface before production use

For avoidance of ambiguity, the minimum operator visibility requirement is:
- a list of manual-review items in the UI, not only a count
- each row must show at least:
  - original filename
  - sender
  - subject
  - received time
  - reason code

A count-only indicator is not sufficient for production use.

### Phase D: Cloud canonicalization
- upload originals and normalized PDFs
- add signed-url preview/access where needed
- strengthen dedupe and audit reporting
- support broader multi-device corpus access and sync semantics

## 19. Open Decisions Requiring Product Confirmation
1. No remaining product decision is blocking the v1 implementation scope defined in this spec.

## 19.1 Phase A Blocking Confirmations Still Outstanding
The remaining blocking confirmations before implementation starts are:
- no unresolved Phase A blocker remains in this spec once the fixed localhost redirect URI below is registered in the multitenant app

Resolved Phase A confirmations:
- tenant-admin consent viability for the NjordHR multitenant Graph app is considered feasible
- developer validation may use a local LibreOffice install, but customer deployment must bundle the converter
- ambiguous items remain local/manual-review items in v1
- cloud upload of email-ingested resumes is optional in v1
- PKCE redirect URI decision: `http://localhost:53682/auth/outlook/callback`

## 20. Explicit Answers To Current Design Questions

### 20.1 How will Outlook be connected to the app?
- through Microsoft Graph delegated OAuth
- the local agent owns the connection and token lifecycle
- the frontend only configures and monitors it
- the PKCE callback uses `http://localhost:53682/auth/outlook/callback`

### 20.2 How often will download happen?
- by polling, not push, in v1
- recommended default every 60 seconds
- configurable within bounded limits

### 20.3 Will this be a separate backend process?
- no
- it should be a separate worker inside the existing local agent process
- the backend remains the control-plane and UI API layer

### 20.4 Can multiple emails/mailboxes be connected?
- not in v1
- support one connected mailbox per local agent instance
- expand later only if real usage requires it

Accepted v1 mailbox choice:
- use one shared company mailbox

### 20.5 How will rank-applied-for be determined?
- from a bounded resolver using subject, body, filename, and resume text
- against canonical configured rank labels and explicit aliases
- with manual review for low-confidence or conflicting cases

Accepted v1 routing rule:
- present-rank-only evidence must not auto-route
- such cases go to manual review

### 20.6 How will the correct download folder be chosen?
- by converting the canonical rank label to the same slug format already used by SeaJobs:
  - spaces -> `_`
  - `/` -> `-`
- create the folder automatically if it does not exist
- place only normalized PDFs into the final rank folder

## 21. Recommended Final Direction
The recommended v1 architecture is:
- **Microsoft Graph mailbox polling in the local agent**
- **local staging and local rank-folder routing**
- **conservative auto-classification with manual-review fallback**
- **PDF normalization through local conversion**
- **optional but strongly recommended cloud canonical copy**
- **one designated intake/index machine per active corpus in v1**
- **one mailbox per local agent instance in v1**
- **shared company mailbox intake in the first rollout**
- **bundled converter required for customer releases; local LibreOffice install allowed only for developer validation**

This is the lowest-risk design that:
- stays cross-platform
- reuses the current NjordHR architecture
- preserves the existing downstream resume workflow
- avoids a second disconnected resume-processing stack
