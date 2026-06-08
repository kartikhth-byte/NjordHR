# NjordHR Email Resume Intake Phase A Customer Decision Sheet

## Purpose
This sheet lists the exact customer-side confirmations required before NjordHR begins implementation of Outlook email resume intake.

It is a short operational checklist derived from the full spec:
- [email-resume-intake-from-outlook-spec-v0.1.md](/Users/kartikraghavan/Tools/NjordHR/docs/email-resume-intake-from-outlook-spec-v0.1.md)

## Decisions Required

### 1. Microsoft 365 / Outlook Tenant Consent
Customer must confirm:
- the mailbox to be connected is hosted on Microsoft 365 / Outlook and accessible through Microsoft Graph
- the customer tenant admin is willing and able to grant consent to the **NjordHR-owned multitenant Microsoft Graph app**

Customer answer required:
- `Yes, tenant admin consent is feasible`
- or `No, tenant admin consent is not feasible`

Why this matters:
- without tenant-admin consent, mailbox access cannot be enabled in enterprise environments

Accepted decision:
- `Yes, tenant admin consent is feasible`

### 2. Target Mailbox Type
Customer must choose which mailbox NjordHR should connect to in v1:
- a shared team mailbox
- or an individual recruiter mailbox

Customer answer required:
- `Shared mailbox`
- or `Individual recruiter mailbox`

Why this matters:
- it determines operational ownership and who manages intake on the designated intake machine

Accepted decision:
- `Shared mailbox`
- expected example: `careers@njordshipping.com`

Locked v1 implementation rule:
- the account connected through Microsoft sign-in should be the actual shared mailbox account being fetched
- do not rely on delegated access from a different signed-in user in the first release

### 3. Designated Intake Machine
Customer must confirm which machine will be the v1 intake/index machine.

Customer answer required:
- machine owner / user
- operating system
- whether that machine is expected to stay on during business hours

Why this matters:
- v1 uses a single designated intake/index machine per active corpus
- SeaJobs downloads, Outlook intake, and local AI search corpus refresh happen there

Accepted decision:
- `One team-owned Windows machine`

### 4. Monitored Outlook Folder
Customer must decide which Outlook folder will be monitored.

Recommended default:
- `Inbox/NjordHR Resumes`

Customer answer required:
- exact folder path to monitor

Why this matters:
- first-run intake and steady-state polling both depend on a stable mailbox folder target

Accepted decision:
- create and monitor `Inbox/NjordHR Resumes`

### 5. Processed Mail Handling
Customer must decide what happens to emails after successful attachment processing.

Options:
- move to a `Processed` folder
- leave in place and mark read

Recommended default:
- move to `Inbox/NjordHR Processed`

Customer answer required:
- selected processed-mail behavior
- if moved, exact processed folder path

Why this matters:
- default behavior affects required Graph permissions and ongoing mailbox hygiene

Accepted decision:
- move processed items to `Inbox/NjordHR Processed`

### 6. Failed Mail Handling
Customer must decide how failed email items are handled.

Recommended default:
- move failed items to `Inbox/NjordHR Failed`

Customer answer required:
- whether failed items should be moved
- if yes, exact failed folder path

Why this matters:
- operators need a clear place to review items that did not process cleanly

Accepted decision:
- move failed items to `Inbox/NjordHR Failed`

### 7. Supported Attachment Types for v1
Customer must confirm whether legacy Word `.doc` files are required in the first release.

Options:
- `PDF + DOCX only`
- `PDF + DOCX + DOC`

Customer answer required:
- selected file-type scope

Why this matters:
- `.doc` support increases operational complexity and conversion risk

Accepted decision:
- `PDF + DOCX + DOC`

### 8. LibreOffice Deployment Model
Customer must confirm the acceptable conversion dependency model for Word-to-PDF conversion.

Options:
- NjordHR may require LibreOffice as a documented prerequisite on the intake machine
- NjordHR must bundle the conversion capability in the shipped app package

Customer answer required:
- `Prerequisite is acceptable`
- or `Bundling is required`

Why this matters:
- Word-to-PDF conversion is required for DOCX/DOC intake into the existing PDF-based workflow
- bundling has installer size and packaging implications

Accepted decision:
- `Prerequisite is acceptable`

### 9. Present-Rank Fallback
Customer must decide whether NjordHR may auto-route a resume when only a strong present-rank signal exists and no explicit applied-rank signal is found.

Options:
- allow present-rank fallback for auto-routing
- require manual review when applied-rank evidence is missing

Recommended default:
- require manual review unless product explicitly accepts present-rank fallback risk

Customer answer required:
- selected fallback policy

Why this matters:
- this directly affects misrouting risk in the rank classifier

Accepted decision:
- `Do not allow`
- present-rank-only evidence routes to manual review

### 10. Manual Review Queue Scope
Customer must decide whether manual-review items need centralized UI visibility only, or centralized visibility plus centralized resolution in the first rollout.

Options:
- centralized visibility only in v1
- centralized visibility and resolution in v1

Customer answer required:
- selected manual-review scope

Why this matters:
- this affects how much UI/admin work is required in the first implementation phase

Accepted decision:
- `Visibility only`
- ambiguous items remain local/manual-review items in v1

## Technical Items NjordHR Will Own
These do not require customer-side product decisions, but the customer should know they are part of setup:
- NjordHR will use a **multitenant Microsoft Graph app**
- NjordHR will use **Authorization Code Flow with PKCE**
- the app registration must include the required **localhost redirect URI pattern**
- accepted v1 callback URI: `http://localhost:53682/auth/outlook/callback`
- NjordHR will poll Outlook on a schedule rather than relying on push notifications in v1

## Minimum Answers Needed To Start Implementation
Implementation should not begin until the following are answered:
1. tenant-admin consent is feasible for the NjordHR multitenant app
2. target mailbox type is chosen
3. designated intake machine is identified
4. monitored Outlook folder is chosen
5. processed-mail behavior is chosen
6. LibreOffice deployment model is chosen
7. present-rank fallback policy is chosen

Current status:
- all minimum implementation-start answers above are now resolved
- cloud upload of email-ingested resumes is optional in v1
- ambiguous items stay local/manual in v1

## Recommended Customer Reply Template
Customer can reply using this format:

```text
1. Tenant admin consent feasible: Yes / No
2. Mailbox type: Shared mailbox / Individual recruiter mailbox
3. Intake machine: <user + machine + OS>
4. Monitored folder: <folder path>
5. Processed mail handling: Move to processed / Leave in place
6. Processed folder path: <folder path if applicable>
7. Failed mail handling: Move / Do not move
8. Failed folder path: <folder path if applicable>
9. Supported file types: PDF + DOCX / PDF + DOCX + DOC
10. LibreOffice model: Prerequisite acceptable / Bundling required
11. Present-rank fallback: Allow / Do not allow
12. Manual review scope: Visibility only / Visibility + resolution
```
