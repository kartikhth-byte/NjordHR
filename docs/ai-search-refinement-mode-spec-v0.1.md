# NjordHR AI Search Refinement Mode Specification
## Specification v0.1 - Design Draft

Date: 2026-06-05
Status: Draft
Owner: AI Search / Candidate Intelligence

---

## 1. Detailed Explanation

### 1.1 What this specification is for

This specification defines a new **AI Search Refinement Mode** for NjordHR.

Today, every AI Search prompt starts from the selected rank folder and searches the candidate population available to that search. A recruiter can run one prompt and inspect its verified matches, but cannot deliberately run a second prompt against only those verified matches.

Refinement Mode adds that capability without changing the meaning of the existing default search.

The intended recruiter workflow is:

```text
Selected rank population
    -> First prompt
    -> Verified matches
    -> Enable "Search within previous verified matches"
    -> Confirm second prompt
    -> Smaller verified-match set
    -> Confirm additional prompts as needed
```

Example:

```text
All Chief Engineers: 89 candidates
    -> "has dual fuel experience": 14 verified matches
    -> "has a valid passport": 6 verified matches
    -> "is between 30 and 50 years old": 2 verified matches
```

Each refinement prompt applies only to the verified candidates produced by the immediately preceding search step. This creates a deliberate narrowing funnel.

### 1.2 What this specification sets out to achieve

The feature must:

1. Allow a recruiter to search within the verified matches from the immediately preceding search.
2. Make the distinction between a new full search and a refinement search explicit.
3. Require confirmation before every refinement search.
4. Lock all scope-defining page filters while Refinement Mode is active.
5. Ensure the backend, not only the UI, enforces the previous verified-candidate scope.
6. Preserve the existing hard-filter, semantic-reasoning, dedupe, and cross-platform path behavior inside the scoped population.
7. Prevent stale, missing, duplicated, or manipulated candidate references from expanding the scope.
8. Keep sufficient lineage and audit information to explain how a result set was narrowed.
9. Allow the recruiter to recover from an overly restrictive refinement without restarting unnecessarily.
10. Preserve current default-search behavior when Refinement Mode is off.
11. Preserve the recruiter's recoverable AI Search working state when the local NjordHR agent or backend becomes temporarily unavailable.
12. Recover local services without changing the existing SeaJobs idle-disconnect, OTP, or reconnect behavior.

### 1.3 Why an explicit switch is the recommended product design

Refinement must not happen automatically merely because previous results exist.

An explicit switch labelled **Search within previous verified matches** gives the recruiter a clear choice between:

- starting a new search over the selected rank population; and
- narrowing the current verified result set.

When the switch is enabled, locking the rank and ship-type filters prevents a logically inconsistent chain. For example, a recruiter must not begin with Chief Engineers on tankers and then silently change the rank to Second Engineers while still claiming to refine the earlier result set.

The switch therefore represents a real search-mode boundary, not only a visual preference.

### 1.4 How the feature will be achieved

The document defines five coordinated mechanisms. The first four comprise Core Refinement Mode; the fifth is the independently shippable Local-service Resilience Extension:

1. **Frontend mode and history state**
   - Track whether Refinement Mode is available, active, or blocked.
   - Track the current refinement chain and previous result snapshots.
   - Lock scope-defining filters while active.
   - Require confirmation before every scoped search.

2. **Server-authoritative search scopes**
   - Every refinable completed search commits an authoritative session summary plus indexed verified-candidate membership and frozen search context.
   - A refinement request references the immediately preceding search session.
   - The backend resolves the previous verified scope and never trusts client-supplied file paths or filenames as the source of truth.

3. **Analyzer candidate scoping**
   - The analyzer receives a validated set of canonical candidate-scope identifiers.
   - It evaluates only candidates inside that scope.
   - If scope resolution fails or resolves to zero candidates, it must not fall back to a full-folder search.

4. **Search lineage and audit**
   - Every refinement records its root search, parent search, depth, frozen filters, input count, resolved count, and output counts.
   - Search results and telemetry clearly state whether the search was a root search or a refinement.

5. **Local-service interruption recovery**
   - Detect local agent and backend interruptions independently of the SeaJobs website session.
   - Preserve a bounded, non-secret recovery draft containing the current completed AI Search state.
   - Recover local services through the desktop shell without requiring the user to close the tab.
   - Restore the last safe completed search state after a renderer reload or application restart.
   - Never automatically resume an interrupted search, download, mailbox operation, or SeaJobs session.

### 1.5 Core safety principle

The UI switch improves clarity, but it is not the security or correctness boundary.

The backend must independently enforce:

- the parent search exists and completed successfully;
- the current actor may use that parent search;
- the rank and page-filter context is unchanged;
- the candidate scope contains only the parent search's verified matches; and
- no invalid refinement request can silently become a full search.

---

## 2. Product Decision Summary

### 2.1 Chosen behavior

- Refinement Mode is available only after a completed search has at least one verified match and its authoritative refinement scope was saved successfully.
- Refinement Mode searches only the immediately preceding search's **verified matches**.
- `Needs Review` and `Uncertain Matches` are excluded from refinement scope.
- The recruiter must enable the switch deliberately.
- Every refinement search requires confirmation.
- Rank, applied ship type, experienced ship type, and future scope-defining filters are locked while Refinement Mode is active.
- Each successful refinement replaces the current scope with its new verified matches.
- Core Refinement Mode lets the recruiter return to an earlier retained result step within the current browser session; the Resilience Extension preserves retained steps for the 24-hour same-actor recovery-draft lifetime.
- Turning Refinement Mode off unlocks filters and makes the next search a new root search.
- Refinement Mode does not concatenate prompts or re-run prior prompts. It applies the new prompt to the previous verified-candidate snapshot.
- v0.1 refinement is same-actor-only. Managers and administrators cannot refine another user's search.
- Every refinement uses the runtime settings active when that refinement begins. It does not inherit or replay the root search's runtime settings.
- Dual-write scope persistence is primary-atomic with best-effort secondary mirroring.
- A local-agent or backend interruption must not clear completed results, the prompt, locked filters, or the recoverable refinement chain.
- The packaged desktop shell owns local-service lifecycle and must not terminate services solely because browser heartbeat timers were throttled or suspended.
- The existing SeaJobs idle timeout, disconnect, OTP, and reconnect flow remains unchanged.
- Core Refinement Mode and local-service resilience have independent release gates. Recovery work must not block a safe core refinement release.

### 2.2 Explicitly rejected behavior

- Do not automatically refine whenever prior results exist.
- Do not use filenames or filesystem paths as authoritative scope identifiers.
- Do not include `Needs Review` or `Uncertain Matches` automatically.
- Do not allow page-filter changes inside an active refinement chain.
- Do not fall back to a full-rank search if a parent scope is missing, invalid, expired, or empty.
- Do not reset or rebuild the local index as part of this feature.
- Do not require separate Windows and macOS behavior.
- Do not use an unvalidated `refinement_confirmed=true` request flag as a substitute for the frontend confirmation interaction.
- Do not use the unrelated prospect-enrollment audit surface for AI Search refinement lineage.
- Do not automatically keep the SeaJobs session alive, request an OTP, reconnect SeaJobs, or change its idle timeout as part of local-agent recovery.
- Do not automatically resume interrupted or long-running work, including searches, scheduled downloads, or mailbox sync, after local-service recovery.
- Do not persist passwords, OTPs, API keys, authentication tokens, settings secrets, or raw resume text in the frontend recovery draft.
- Do not shut down the packaged backend or local agent solely because frontend heartbeats were missed.
- Do not authorize recovery-draft access using a renderer-supplied actor ID.

---

## 3. Terminology

### 3.1 Root search

A normal AI Search executed over the current selected rank population while Refinement Mode is off.

### 3.2 Refinement search

An AI Search executed only against the verified matches of an immediately preceding completed search.

### 3.3 Search chain

A root search plus zero or more descendant refinement searches.

### 3.4 Parent search

The immediately preceding search whose verified matches become the current refinement input.

### 3.5 Candidate scope

The server-authoritative set of canonical candidate-scope identifiers that a refinement search is permitted to evaluate.

### 3.6 Scope-defining filters

Inputs that determine the candidate population independently of the prompt.

For the current UI these are:

- selected rank folder;
- applied ship type filter; and
- experienced ship type filter.

Any future page filter that changes the eligible population must be registered as scope-defining.

### 3.7 Canonical candidate-scope identifier

`candidate_scope_id` is a persistent, cross-platform resume-entity identity used for search scoping and dedupe.

It must survive a known resume-version update, re-OCR, or re-save so that a recruiter can continue refining the same candidate after the resume bytes change. It must not be recomputed directly from the latest file bytes on every search.

`candidate_scope_id` is an opaque persistent UUID generated when a registry resume entity is first created. The existing `stable_resume_id` helper from `repositories/resume_identity.py` is a legacy identifier helper, not proof of content identity: it returns a file-content SHA-1 when bytes are readable, but falls back to a normalized-path SHA-1 when they are not. It must not become the recomputed persistent identity of changed content.

A legacy `stable_resume_id` may be promoted to an exact-content/cross-platform alias only when ingestion or migration separately obtains a non-empty verified content hash from readable bytes and records alias provenance `verified_content_hash`. A path-fallback `stable_resume_id`, an empty-input hash, or an identifier whose provenance is unknown is a non-authoritative lookup hint only. It cannot automatically link, merge, or retain a `candidate_scope_id`.

The persistent identity must be resolved through the registry/identity mapping rather than from:

- display filename;
- absolute path;
- Windows-only path;
- macOS-only path; or
- external numeric candidate ID alone.

If a changed or replacement resume cannot be mapped safely to an existing persistent identity, the system must mark the previous scope member as unresolvable rather than guessing.

Identity-linking authority:

- exact-content and cross-platform duplicate detection may automatically alias another file location or vector/index resume ID to an existing `candidate_scope_id` only when a separately verified non-empty content hash proves equality;
- a controlled update to an existing registry resume record may retain that record's `candidate_scope_id`;
- a trusted source-system candidate association may retain identity only when it uses a configured provider namespace plus provider candidate ID already bound to that registry entity;
- a recruiter may explicitly confirm that a replacement resume belongs to an existing candidate entity through an audited replacement/merge action; and
- an approved migration may create aliases from legacy registry or index identifiers when the migration has deterministic evidence.

Every non-exact identity-link event records:

- previous and new content hashes;
- source and target registry identifiers;
- link authority: `controlled_update|trusted_source_association|recruiter_confirmed_replacement|approved_migration`;
- actor or migration identifier;
- timestamp; and
- an optional operator/recruiter reason.

Filename similarity, path similarity, extracted name, email address, phone number, or other resume text must never independently authorize an identity link. If no authoritative link event exists, changed content receives a new entity identity or the old scope member becomes unresolvable.

Every stored identity alias records `alias_type`, `alias_value`, and `alias_provenance`. Required provenance values include `verified_content_hash`, `legacy_path_fallback`, `legacy_unknown`, `vector_index_id`, and the approved non-exact link authorities above. Only `verified_content_hash` may independently authorize automatic exact-content linking.

Naming contract:

- singular identifier: `candidate_scope_id`;
- collection or analyzer parameter: `candidate_scope_ids`;
- scope-membership storage field: `candidate_scope_id`.

Do not use `verified_candidate_scope_ids` as a separate name.

### 3.8 Content fingerprint

`content_hash` is the current resume-content fingerprint used for change detection, duplicate detection, and evidence.

Unlike `candidate_scope_id`, `content_hash` is expected to change after a meaningful content edit, re-OCR, or re-save. A scope-membership record stores both values so the system can:

- retain the same resume entity in the refinement scope;
- detect that its content changed after the parent search; and
- surface that change without silently dropping the candidate.

### 3.9 Recovery draft

A **recovery draft** is a bounded, versioned, same-device snapshot of safe frontend working state used only to restore the recruiter after a local-agent interruption, backend interruption, renderer reload, tab close, or application restart.

It may contain:

- active application tab;
- AI Search prompt;
- selected rank and scope-defining filters;
- current completed analysis results;
- Refinement Mode state;
- up to the 10 retained interactive search-chain snapshots;
- current parent/root/session references;
- refinement availability and scope summaries; and
- the timestamp and reason for the last safe checkpoint.

It must not contain:

- passwords or OTPs;
- API keys, bearer tokens, admin tokens, cookies, or authentication secrets;
- secret settings values;
- raw resume text or extracted document bodies; or
- an instruction to automatically resume an interrupted operation.

Recovery drafts are scoped to the stable authenticated `actor.user_id`, schema-versioned, and expired after 24 hours.

Storage contract:

- packaged desktop: the authoritative recovery store is an Electron main-process JSON-file store under `app.getPath("userData")/recovery/ai-search/`, using one tab-scoped file per draft and exposed through a narrow recovery-draft IPC bridge;
- browser-only mode: use origin-scoped IndexedDB as a best-effort fallback, storing draft payloads encrypted and authenticated with a backend-issued actor-bound browser recovery key;
- packaged and browser-only recovery stores are intentionally separate and do not exchange drafts;
- do not rely on packaged renderer-origin IndexedDB as the authoritative store because NjordHR's selected localhost port, and therefore the renderer origin, may change after restart; and
- do not use `localStorage` for recovery drafts.

The browser-only backend issues recovery-key material only after successful authentication for the current stable actor. IndexedDB metadata must not expose result contents, prompts, or raw actor IDs. A renderer authenticated as one actor must not be able to decrypt another actor's stored draft. Key durability and rotation follow the browser recovery-key contract in Section 12.7.

Each draft is a purpose-built recovery projection, not a serialized copy of the complete React state or raw API response. Each serialized draft is limited to 4 MiB. The store retains at most three tab-scoped drafts per actor, for a maximum of 12 MiB per actor.

Recovery result cards are summary-only projections using schema `recovery_result_card.v1`. They may contain only:

- `candidate_scope_id`: opaque identifier, maximum 64 characters;
- `content_hash`: lowercase hash, maximum 128 characters;
- `filename`: display basename only, maximum 255 characters;
- `result_bucket`: `verified_match|uncertain_match|needs_review`;
- `confidence`: finite numeric value between 0 and 1 or `null`;
- `lineage_warning_codes`: allowlisted machine codes, maximum 10 entries of 64 characters each;
- `evidence_review_badges`: allowlisted non-free-text badge codes, maximum 10 entries; and
- `detail_available_after_recovery`: boolean, normally `false`.

Recovery result cards must not contain match reasons, hard-filter reason messages, default insights, computed age, date of birth, contact details, ship history, extracted facts, raw evidence, nested provider output, arbitrary metadata, or other free text derived from resume contents. Unknown fields are rejected, not silently retained. A restored summary-only card visibly explains that detailed reasoning was not stored and requires a deliberate new search to regenerate.

`current_completed_results` and every retained `search_chain` snapshot must use arrays of `recovery_result_card.v1` plus the allowlisted search/session/count metadata defined by this specification. They must never embed raw live match objects.

When a draft would exceed 4 MiB, trimming is deterministic:

1. never store excluded-candidate rows, raw resume chunks, raw provider responses, or complete hard-filter audit payloads;
2. trim detailed evidence from the oldest retained snapshots;
3. remove the oldest interactive snapshots while retaining compact chain lineage;
4. preserve the current prompt, filters, current completed verified-match cards, counts, session references, and refinement state whenever possible; and
5. if the remaining projection still exceeds the budget, save a context-only draft and mark it as partial rather than silently failing.

The restored UI must visibly distinguish `full`, `trimmed`, and `context_only` recovery and explain that a new search is required to refresh omitted evidence.

When saving a fourth non-expired tab draft would evict the oldest retained tab draft, the UI must disclose which draft timestamp will be removed and require confirmation unless the evicted draft was already explicitly discarded or expired. Cancellation preserves the existing drafts and skips the new checkpoint; it must not silently overwrite or evict another tab's recovery state. Because unload handlers cannot reliably show confirmation, an unload/restart checkpoint that would require eviction skips that checkpoint and surfaces the pending-eviction choice when the application next becomes interactive.

### 3.10 Local-service interruption

A **local-service interruption** occurs when the NjordHR backend or local agent becomes unreachable while the renderer remains open, or when the desktop shell must restart those services.

This is distinct from a **SeaJobs website-session disconnect**. SeaJobs idle timeout, authentication, OTP, and reconnect behavior are outside the local-service recovery mechanism and remain unchanged.

### 3.11 Search population counts

Search counts have distinct meanings and must not be presented interchangeably:

- `eligible_population_count`: for a root, unique canonical candidates eligible under the server-canonical rank and page-filter context before prompt-based retrieval; for a refinement, the parent scope's requested membership count;
- `frozen_rank_population_count`: optional informational count of the complete frozen rank/filter population; for a root search it normally equals `eligible_population_count`, while for a refinement it remains separate from refinement eligibility;
- `retrieved_count`: unique candidates selected by vector, keyword, or compound retrieval before final normalization; `null` when broad retrieval is not used;
- `evaluated_count`: unique normalized candidates that actually reach hard-filter or semantic evaluation;
- `verified_count`: evaluated candidates emitted as verified matches; and
- `candidate_scope_member_count`: verified candidates committed into the next refinable scope.

For a root search, `eligible_population_count` is the canonical rank/filter population before prompt retrieval. For a refinement, `eligible_population_count` is the parent scope's requested membership count and must never be recomputed from the full rank/filter population. If the full frozen rank/filter population is useful for diagnostics, report it separately as `frozen_rank_population_count`. `evaluated_count` is the number of resolved scoped candidates actually evaluated.

The UI must never label `eligible_population_count` as scanned or evaluated when semantic top-k retrieval evaluated only a subset.

---

## 4. Current Behavior and Architectural Gap

### 4.1 Frontend

The current AI Search frontend:

- stores only one active `analysisResults` object;
- clears that result object when a new analysis begins;
- sends prompt, rank folder, applied ship type, and experienced ship type to `/analyze_stream`;
- has no refinement-mode state, search history, or confirmation flow; and
- primarily identifies displayed matches by filename.

### 4.2 Backend

The current streaming endpoint:

- creates a new search session ID for each request;
- accepts no parent search or candidate-scope reference;
- passes no scope to the analyzer; and
- strips raw hard-filter audit rows before returning the final SSE payload.

### 4.3 Analyzer

The current analyzer:

- evaluates all candidates in the selected rank folder when actionable hard constraints are present; or
- retrieves candidates from the vector database or keyword fallback for semantic searches.

It has no supported way to constrain either path to a previous verified result set.

### 4.4 Identity and dedupe

NjordHR already contains cross-platform resume fingerprinting and stale/duplicate candidate normalization. Refinement Mode must build on that behavior.

The feature must not introduce a second identity mechanism based on current display filenames.

### 4.5 Gap to close

The primary missing capability is a server-authoritative way to tell the analyzer:

> Evaluate this new prompt, but only against the canonical candidates verified by the immediately preceding search.

### 4.6 Local-service resilience gap

The current renderer sends periodic UI heartbeats, while the macOS launcher can configure the backend to shut down after a short period without heartbeats. Browser background throttling, system sleep, or a suspended renderer can therefore appear identical to an intentional UI closure.

The current desktop recovery action relaunches the full application, and most interactive AI Search state exists only in React memory. If services stop or the renderer reloads, the recruiter can lose the current prompt, results, filter context, and refinement chain.

The resilience gap to close is:

> Recover the local NjordHR backend and agent without changing SeaJobs behavior, and restore the last safe completed AI Search state without automatically resuming interrupted work.

---

## 5. User Experience Specification

### 5.1 Switch label

Use:

> **Search within previous verified matches**

Do not use ambiguous labels such as:

- Advanced mode
- Filter mode
- Search results mode
- Refine automatically

### 5.2 Initial state

Before the first completed search:

- the switch is visible but disabled;
- helper text reads:

> Available after a search returns at least one verified match.

### 5.3 State after a successful root search

If the root search returns verified matches and the backend confirms that its refinement scope was saved:

- the switch becomes enabled but remains off;
- the label includes the available count:

> Search within previous verified matches (14)

If the search returns zero verified matches:

- the switch remains disabled;
- helper text reads:

> No verified matches are available to refine.

If results exist but scope persistence failed:

- the switch remains disabled;
- helper text reads:

> These results cannot be refined because the search scope could not be saved. Start a new search or retry.

### 5.4 Enabling Refinement Mode

When the recruiter enables the switch:

- the selected rank folder is locked;
- the applied ship type filter is locked;
- the experienced ship type filter is locked;
- any future scope-defining filters are locked;
- the prompt remains editable;
- the displayed results remain visible;
- a refinement banner appears;
- the action button changes from `Analyze Resumes` to the count-aware label:
  - `Refine 1 Match`; or
  - `Refine N Matches`.

Banner copy templates:

- `N = 1`: **Refinement Mode is on.** New prompts will search only within the current verified match. Candidates excluded by earlier searches will not be considered.
- `N != 1`: **Refinement Mode is on.** New prompts will search only within the current N verified matches. Candidates excluded by earlier searches will not be considered.

All user-visible count strings must use singular/plural-aware formatting rather than rendering phrases such as `1 matches`.

Locked filters must remain readable and visually show a lock state. They must not simply disappear.

### 5.5 Confirmation requirement

Every search initiated while Refinement Mode is active must show a confirmation dialog before any request is sent.

Confirmation title:

> Refine current verified matches?

Confirmation body templates:

- `N = 1`: This prompt will search only within the current verified match. Candidates excluded by previous searches will not be considered.
- `N != 1`: This prompt will search only within the current N verified matches. Candidates excluded by previous searches will not be considered.

The dialog must also show:

- the new prompt;
- current rank;
- frozen applied ship type;
- frozen experienced ship type;
- the user-facing chain step, such as `Next step: Refinement 2`; and
- the current input count, such as `14 candidates in scope`.

Actions:

- `Cancel`
- `Refine 1 Match` when `N = 1`; or
- `Refine N Matches` when `N != 1`.

Cancel behavior:

- no request is sent;
- existing results remain unchanged;
- Refinement Mode remains active;
- the prompt remains available for editing.

### 5.6 Running a refinement

While a refinement is running:

- the locked filters remain locked;
- the switch cannot be changed;
- the previous result snapshot remains recoverable;
- progress copy identifies the scoped population:

> Analyzing 3 of 14 candidates from the previous verified matches.

The frontend requests scope preflight when the switch is enabled and again immediately before each confirmation dialog. The preflight reports how many parent scope members are currently resolvable.

If more than 30% of the parent scope is unavailable, the confirmation dialog must add a warning:

> Some candidates from the previous step are no longer available. This refinement will search X of N previous verified matches.

If one or more scoped resumes changed content since the parent search, the confirmation dialog must state that those candidates remain in scope but will be evaluated using their current resume content.

If one or more scoped resumes changed content, confirmation requires a separate explicit acknowledgement:

> Some candidate resumes changed after an earlier search step. Earlier conditions have not been re-certified against the changed content. Continue using the current resumes?

The acknowledgement is required in addition to the normal refinement confirmation. Cancelling preserves the parent results and sends no analysis request.

After the recruiter confirms both dialogs, the frontend requests a server-issued changed-content acknowledgement record before opening the analysis stream. The acknowledgement is bound to:

- the stable authenticated `actor.user_id`;
- `parent_search_session_id`;
- the intended child `search_request_id`;
- a canonical fingerprint of the complete current changed-content candidate set, including each changed candidate's `candidate_scope_id`, parent content hash, and current content hash;
- issuance and expiry timestamps; and
- a one-time acknowledgement ID.

The backend re-runs authoritative preflight resolution before issuing the acknowledgement. It records no raw resume text. The acknowledgement expires after five minutes, is single-use for the bound request ID, and is consumed only when the child search is accepted. If the changed-content set differs when analysis begins, the acknowledgement is invalid and the backend returns `REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED`; the frontend must show the updated warning and obtain a new acknowledgement.

If no parent scope members are resolvable, the frontend must not offer confirmation and must show the refinement-specific failure state.

The backend must resolve the scope again when the search actually begins. The preflight is a recruiter warning, not the final correctness boundary.

### 5.7 Successful refinement with verified matches

When a refinement completes with one or more verified matches:

- the new results become the current result set;
- the verified matches become the next candidate scope;
- Refinement Mode remains active;
- the switch count updates;
- the search-chain indicator adds a step;
- another prompt may be run after confirmation.

Example structured-search chain indicator:

```text
Eligible/Evaluated: 89 -> Search 1 Verified: 14 -> Refinement 1 Verified: 6 -> Refinement 2 Verified: 2
```

For a semantic root that uses retrieval, the chain must distinguish population from evaluation:

```text
Eligible: 89 -> Retrieved: 50 -> Evaluated: 47 -> Search 1 Verified: 14
```

### 5.8 Successful refinement with zero verified matches

When a refinement completes with zero verified matches:

- display the zero-result search normally;
- keep Refinement Mode visibly active;
- disable further refinement because there is no valid input scope;
- keep filters locked until the recruiter goes back or turns Refinement Mode off;
- offer `Return to previous results`;
- offer `Start a new search`.

Do not silently reuse the previous non-empty scope for the next prompt.

### 5.9 Returning to an earlier result step

Core Refinement Mode must allow return to the immediately preceding retained result step within the current browser session. When the Resilience Extension is enabled, the same ability persists within the recovery-draft lifetime.

When returning:

- restore that step's result snapshot;
- restore its search-session reference;
- restore its refinement count and chain position;
- allow a new branch of refinement from that earlier step.

Core back-navigation restores an in-memory frontend result snapshot from `searchChain`; the backend does not re-emit or reconstruct prior result cards. When the Resilience Extension is enabled, the same bounded snapshots may also be restored from an authorized recovery draft.

The frontend may retain at most 10 result snapshots for back-navigation. Refinement itself is not capped: after the tenth retained snapshot, the oldest interactive snapshot is discarded while lineage remains persisted on the backend.

The Resilience Extension restores only the bounded same-actor frontend recovery draft. It does not reconstruct missing result cards from backend audit or lineage records.

### 5.10 Turning Refinement Mode off

Turning the switch off:

- unlocks scope-defining filters;
- leaves the currently displayed results visible;
- marks the next analysis as a new root search;
- does not automatically send a request.

If the recruiter turns the switch on again before changing any scope-defining filter or running a new root search, the currently displayed verified matches may be used again.

### 5.11 Changing filters while Refinement Mode is off

If a scope-defining filter changes while previous results are displayed:

- the previous results may remain visible for reference;
- refinement eligibility for those results is invalidated;
- the switch becomes disabled until a new root search completes;
- the UI indicates that the displayed results belong to an earlier filter context.

This prevents the switch from being enabled against results that do not correspond to the visible filter values.

### 5.12 Refresh and restart behavior

The frontend must checkpoint the last safe completed AI Search state into the same-actor recovery draft.

The draft is updated after:

- a root or refinement search completes successfully;
- Refinement Mode is enabled or disabled;
- the recruiter returns to an earlier result step;
- the prompt or scope-defining filters change; and
- the active tab changes.

Writes must be debounced and bounded so normal typing and progress events do not cause excessive storage activity.

After browser refresh, tab close/reopen, renderer reload, or application restart:

- authenticate the user normally;
- load only a non-expired draft belonging to the same stable `actor.user_id`;
- restore the active tab, prompt, filters, current completed results, and recoverable chain;
- revalidate the current parent scope through preflight before enabling another refinement;
- show a visible `Recovered previous AI Search` notice with `Discard recovered search` and `Start a new search` actions; and
- disclose whether the restored draft is full, trimmed, or context-only; and
- preserve persisted search audit and lineage for operational review.

If an interruption occurs while a root or refinement request is running:

- do not automatically resume or replay the interrupted request;
- restore the last completed result snapshot;
- for an interrupted refinement, return to the completed parent state and preserve its prompt and scope reference;
- mark the interrupted attempt visibly as not completed; and
- require a new explicit analysis action and, for refinement, a new confirmation.

If the draft is expired, corrupt, belongs to another actor, or cannot be migrated from its schema version:

- discard it safely;
- start with normal fresh frontend state; and
- do not infer ownership from username alone.

TTL validation is conservative:

- require parseable `saved_at` and `expires_at` values whose interval is no greater than 24 hours plus five minutes of serialization tolerance;
- allow at most five minutes of ordinary clock skew;
- discard a draft whose `saved_at` is implausibly in the future, whose `expires_at` has passed, or whose timestamp interval is malformed;
- never extend a draft beyond its recorded 24-hour expiry because the device clock moved backward; and
- record a clock-skew discard reason for diagnostics without including draft contents.

Logout clears the current actor's recovery draft. An explicit `Discard recovered search` or `Start a new search` action also clears the recoverable chain after confirmation.

Confirmation title:

> Start a new AI Search?

Confirmation body:

> This will clear the recovered AI Search prompt, results, and refinement chain from this device. Search audit and lineage records are not deleted.

Actions:

- `Cancel`
- `Clear recovered search and start new`

Cancel sends no request, deletes no draft, and preserves the recovered state. Confirmed clearing deletes only the current actor's selected recovery draft/chain and then initializes a normal new root-search state.

SeaJobs connection state is not restored from this draft. Its existing idle-disconnect and OTP reconnect behavior remains unchanged.

### 5.13 Frontend state machine

The frontend must implement the following explicit state model.

| State | Meaning |
|---|---|
| `disabled` | No completed result set is eligible for refinement. |
| `available` | Current results have at least one verified match and a saved authoritative scope; switch is off. |
| `active_idle` | Switch is on, filters are locked, and the user may enter another prompt. |
| `active_running` | A confirmed refinement is running. |
| `active_zero_result` | The latest refinement completed with zero verified matches; filters remain locked and further refinement is disabled. |
| `invalidated_by_filter_change` | Previous results remain visible, but an unlocked scope-defining filter changed and the old scope cannot be reused. |
| `mode_off_with_stale_results` | Switch is off and current results are displayed only for reference; the next analysis is a root search. |

Required transitions:

| Current state | Event | Next state | Required action |
|---|---|---|---|
| `disabled` | Root search completes with verified matches and saved scope | `available` | Enable switch. |
| `disabled` | Root search completes with zero verified matches or scope-save failure | `disabled` | Keep switch disabled and show reason. |
| `available` | Toggle on and preflight resolves at least one candidate | `active_idle` | Lock filters, show banner, and display any unavailable-candidate warning. |
| `available` | Toggle on and preflight resolves zero candidates or fails authoritatively | `disabled` | Keep filters unlocked and show the refinement-specific reason. |
| `available` | Scope-defining filter changes | `invalidated_by_filter_change` | Disable old refinement eligibility. |
| `active_idle` | Pre-confirmation preflight returns a retryable failure or `REFINEMENT_SCOPE_BACKFILL_PENDING` | `active_idle` | Preserve results and locked filters, disable the refine action temporarily, show retry guidance, and retry preflight later. |
| `active_idle` | Pre-confirmation preflight returns a structural failure such as expired, unauthorized, empty, unresolvable, or unavailable context | `disabled` | Preserve results for reference, turn mode off, unlock filters, and show the non-retryable reason. |
| `active_idle` | Confirmation cancelled | `active_idle` | Send no request and preserve results. |
| `active_idle` | Refinement confirmed | `active_running` | Start scoped search. |
| `active_running` | Refinement completes with verified matches and saved scope | `active_idle` | Replace current results and update scope. |
| `active_running` | Refinement completes but scope save fails | `disabled` | Show completed results, turn mode off, unlock filters, and explain why further refinement is unavailable. |
| `active_running` | Refinement completes with zero verified matches | `active_zero_result` | Disable further refinement and offer back/new search. |
| `active_running` | Transient request failure, local-service interruption, or SSE disconnect | `active_idle` | Preserve or restore parent results, mark the attempt interrupted, and allow a newly confirmed retry. |
| `active_running` | Structural refinement failure such as expired, unauthorized, empty, unresolvable, or unavailable parent scope | `disabled` | Preserve results for reference, unlock filters, and show the non-retryable reason. |
| Any refinable completed state | Preflight returns `REFINEMENT_SCOPE_BACKFILL_PENDING` | Same completed state | Preserve results and lock intent, temporarily disable the refine action, show `Refinement will be available when candidate identity preparation completes`, and retry preflight later. |
| `active_zero_result` | Return to previous results | `active_idle` | Restore the previous frontend snapshot and scope reference. |
| `active_idle` or `active_zero_result` | Toggle off | `mode_off_with_stale_results` | Unlock filters; next search is root. |
| `mode_off_with_stale_results` | Toggle on before context changes and preflight succeeds | `active_idle` | Reuse current eligible scope and show any warning. |
| `mode_off_with_stale_results` | Toggle on and preflight resolves zero candidates or fails authoritatively | `disabled` | Keep results for reference and disable reuse. |
| `mode_off_with_stale_results` | Scope-defining filter changes | `invalidated_by_filter_change` | Prevent reuse of displayed results. |
| `invalidated_by_filter_change` or `mode_off_with_stale_results` | New root search completes with verified matches and saved scope | `available` | Replace results and enable the new scope. |
| `invalidated_by_filter_change` or `mode_off_with_stale_results` | New root search completes with zero verified matches or no saved scope | `disabled` | Replace results and keep refinement unavailable. |
| Any running analysis | Terminal `request_status=SEARCH_REQUEST_IN_PROGRESS` | Previous safe completed state | Stop loading, preserve previous results if any, show retry timing, and do not attach to the existing stream. |
| Any running analysis | Terminal `request_status=SEARCH_REQUEST_ALREADY_COMPLETE` | Previous safe completed state or `disabled` | Stop loading, preserve existing interactive results, show the non-replayed completion summary, and require a new request ID to run again. Do not fabricate result cards. |
| Any running analysis | Terminal `request_status=SEARCH_REQUEST_ALREADY_FAILED` | Previous safe completed state or `disabled` | Stop loading, preserve existing interactive results, show the non-sensitive failure, and require a new request ID to retry. |
| Any running request | Terminal `error=SEARCH_REQUEST_ID_CONFLICT` | Previous safe completed state | Stop loading, preserve existing results, show a generic conflict, and generate a new request ID only after another explicit user action. |
| Any completed state | Browser refresh, tab reopen, renderer reload, or application restart with a valid same-actor recovery draft | Restored prior state | Restore completed results and chain, then revalidate refinement scope before allowing another refinement. |
| Any state | Browser refresh, tab reopen, renderer reload, or application restart without a valid same-actor recovery draft | `disabled` | Start fresh; next search is root. |

### 5.14 Search-chain presentation

Each displayed prompt label must be truncated to approximately 60 characters with an ellipsis. The full prompt must remain available through an accessible tooltip or details interaction.

The chain should show user-facing step labels such as `Search 1`, `Refinement 1`, and `Refinement 2`. It must not expose the technical `refinement_depth` value as the primary label.

The chain and summary must show the count taxonomy from Section 3.11:

- root eligible-population count;
- retrieved count when broad semantic or compound retrieval was used;
- evaluated count;
- each step's verified count; and
- current step.

The chain must not use `All candidates`, `Scanned`, or equivalent wording unless every eligible candidate was actually evaluated. Structured roots may collapse equal counts into `Eligible/Evaluated: N`; semantic roots must show eligible, retrieved, and evaluated counts separately.

### 5.15 Local-service recovery experience

Local-service recovery is orthogonal to the Refinement Mode state machine. Losing the local agent or backend must not itself change a completed refinement state.

Required recovery states:

| Recovery state | Meaning |
|---|---|
| `healthy` | Required local services are reachable. |
| `agent_unreachable` | Backend is reachable but the local agent is unavailable. |
| `backend_unreachable` | The renderer cannot reach the backend. |
| `recovering` | The desktop shell is restarting or reconnecting local services. |
| `recovered` | Services returned and the restored state is being revalidated. |
| `recovery_failed` | Automatic and requested recovery did not restore the required service. |

When the local agent becomes unreachable:

- keep the current page, prompt, completed results, and refinement chain visible;
- pause actions that require the local agent;
- retry health checks with bounded exponential backoff and immediately when the tab becomes visible or the device returns online;
- show a persistent recovery banner with `Reconnect Agent`, `Restart Local Services`, and `Open Logs` when those actions are available; and
- remove the banner only after health and current refinement-scope preflight succeed.

When the backend becomes unreachable:

- keep the renderer and current in-memory state open;
- write the latest safe recovery draft before requesting a restart when possible;
- in the packaged desktop application, call a desktop bridge action that restarts only the unhealthy required service or services without destroying the renderer;
- poll the backend health endpoint until it is reachable or recovery times out;
- re-authenticate if required, then restore and revalidate the same-actor recovery draft; and
- in browser-only mode, show: `NjordHR service is unavailable. Restart it using your NjordHR launcher or terminal, then select Check Again.` The available recovery action is `Check Again`; service restart and `Open Logs` actions are not shown.

The packaged desktop application must not use missed frontend heartbeats as the sole reason to terminate backend or agent processes. The Electron process manager should own child-service lifetime and shut services down on a real application quit. If heartbeat-based cleanup remains available for browser-only or development launchers, it must be opt-in, disabled by default, and must not be presented as a reliable indication that the user closed the application.

`Restart Local Services` must be distinct from the existing full `Restart App` action. It restarts only the unhealthy required service where possible. In particular, backend-only recovery must not restart a healthy local agent or disturb its active SeaJobs website session. If the local agent itself must restart, any in-memory SeaJobs session loss is reported through the existing SeaJobs disconnected/OTP flow; recovery must not claim that SeaJobs was restored or reconnect it automatically. A full relaunch may remain as a fallback after service-only recovery fails.

`Open Logs` opens the configured local NjordHR logs directory in Finder or Explorer. It does not open DevTools, upload logs, or display raw logs inside the recovery banner. The action is available only in the packaged desktop application for the local authenticated OS user.

The local-service recovery banner must not claim to reconnect SeaJobs and must not alter SeaJobs connection state.

---

## 6. Search Semantics

### 6.1 Scope membership

The authoritative input to a refinement is exactly the candidate-scope membership saved for the parent search.

Only candidates emitted as `match_found` and persisted with the `verified_match` result bucket enter the next scope.

Candidates persisted as `needs_review`, `uncertain_match`, `excluded`, or `llm_no_match` do not enter the next scope.

### 6.2 New prompt behavior

The new prompt is parsed and evaluated independently using the normal NjordHR search pipeline.

The system does not concatenate the previous prompts into a larger prompt.

The effective result is nevertheless a logical narrowing because only candidates verified by the parent search are eligible to enter the next step.

### 6.3 Previous requirements are not re-evaluated

Refinement Mode uses previous verified membership as a snapshot.

It does not automatically re-run every earlier prompt against updated facts.

If a known resume entity changes between steps:

- its persistent `candidate_scope_id` remains in scope;
- the current `content_hash` is compared with the parent membership's recorded `content_hash`;
- the new prompt is evaluated against the current resolvable resume;
- the search records and surfaces that the candidate content changed; and
- previous prompt conditions are not independently re-certified.

Changed-content candidates remain eligible only after the recruiter completes the explicit changed-content acknowledgement described in Section 5.6. Every changed candidate result card and exported lineage entry must retain a persistent warning:

> Resume content changed after an earlier search step. Earlier conditions were not re-certified against this version.

The warning remains visible through subsequent refinement steps unless the complete chain is deliberately re-run against the current content.

If the changed resume cannot be mapped safely to the persistent `candidate_scope_id`, it is reported as unresolvable and excluded from that refinement. The system must not guess identity from a similar filename.

Each refinement uses the runtime settings active when that refinement begins, including the current `LLM_Promotion_Stage`. Runtime settings are recorded with the child search for audit, but are not inherited from the root search because refinement is a new evaluation rather than a replay.

### 6.4 Scope must be applied before evaluation

The analyzer must restrict the candidate population before hard-filter and LLM evaluation.

It is not sufficient to:

1. run a normal full search; and
2. intersect the final results afterward.

That approach would waste provider calls, produce misleading audit counts, and risk leaking out-of-scope candidates through progress events.

### 6.5 Semantic-only refinement

For a semantic-only refinement prompt, the scoped set remains authoritative.

In scoped mode, the analyzer must never call broad `vector_db.query` or keyword top-k retrieval to choose candidates. It must enumerate or directly fetch the complete resolved `candidate_scope_ids` set first, then evaluate the semantic prompt within that set.

The analyzer may use already-indexed chunks for each scoped candidate, but vector similarity must not decide which scoped candidates are evaluated.

### 6.6 Structured-only and skip-LLM behavior

Scoped refinement preserves the existing deterministic skip-LLM optimization.

When a refinement prompt is structured-only and the existing analyzer rules determine `should_skip_llm=True`, candidates that pass deterministic hard filters become verified matches without provider-backed LLM reasoning. Refinement Mode must not force the LLM path merely because the search is scoped.

### 6.7 Empty and invalid scope behavior

If the parent scope is invalid, missing, unauthorized, expired, or resolves to zero current candidates:

- return a refinement-specific error or graceful failure;
- emit no candidate match events;
- do not run a full-rank search;
- preserve the previous results in the frontend.

---

## 7. Search Scope and Lineage Data Model

### 7.1 Authoritative scope record

Request idempotency and search lineage are separate durable contracts.

Before starting any analyzer worker, the backend must atomically create or resolve a globally unique `search_request_claims` row keyed by `search_request_id`. The claim stores the authenticated actor, canonical request fingerprint, status, optional search-session ID, and terminal non-sensitive outcome. Failure to durably create or resolve this claim returns `SEARCH_REQUEST_STORE_UNAVAILABLE`; no search begins.

After claiming the request, the backend attempts to create an indexed search-session lineage row. The lineage row separately records `actor_user_id` for ownership and authorization.

- If initial lineage creation fails for a root search, the claimed request may continue only as a non-refinable root search. Its durable claim remains `started` while analysis runs, then transitions to `complete` with a non-sensitive summary and `refinement.available=false`, or to `failed` if analysis fails. The claim records `lineage_failure_code=LINEAGE_CREATE_FAILED`. Reusing the same request ID follows the normal exact-duplicate behavior and never starts another worker.
- If initial lineage creation or scope storage is unavailable for a refinement request, the claimed request transitions to terminal `failed` with a non-sensitive failure code and analysis does not begin because the parent scope cannot be enforced safely.
- A claim must never remain permanently `started` solely because lineage creation failed. A bounded recovery job transitions abandoned claims whose worker lease expired to `failed`.

When a search completes successfully and scope storage is available, the primary store atomically:

- updates that session to `complete`;
- writes its canonical summary; and
- writes all verified-candidate membership rows.

Only a `complete`, unexpired search session can be used as a refinement parent.

Recommended schema:

```json
{
  "schema_version": "candidate_scope.v1",
  "search_request_id": "client-generated-uuid",
  "search_session_id": "uuid",
  "root_search_session_id": "uuid",
  "parent_search_session_id": "uuid-or-null",
  "search_mode": "root|refinement",
  "refinement_depth": 0,
  "status": "started|complete|failed",
  "delivery_status": "pending|complete_event_yielded|disconnected_before_complete|delivery_unknown",
  "mirror_status": "not_configured|pending|complete|failed",
  "actor": {
    "user_id": "stable-auth-mode-qualified-id",
    "username_at_event": "recruiter-name",
    "role_at_event": "recruiter",
    "auth_mode_at_event": "local|cloud"
  },
  "context": {
    "download_root_id": "opaque-active-root-uuid",
    "rank_folder_id": "canonical-discovered-rank-id",
    "rank_folder": "Chief_Engineer",
    "applied_ship_type": "",
    "experienced_ship_type": "",
    "llm_promotion_stage": "current-stage-at-search-time",
    "runtime_settings_fingerprint_schema": "ai_search_runtime.v1",
    "runtime_settings_fingerprint": "sha256-of-canonical-non-secret-runtime-settings"
  },
  "prompt_record": {
    "prompt_hash": "sha256-or-current-policy-hash",
    "raw_prompt": "string-or-null-according-to-current-prompt-storage-policy"
  },
  "changed_content_acknowledgement": {
    "acknowledgement_id": "uuid-or-null",
    "changed_content_set_fingerprint": "sha256-or-null",
    "acknowledged_at": "ISO-8601-or-null"
  },
  "input_scope": {
    "frozen_rank_population_count": 89,
    "eligible_population_count": 89,
    "retrieved_count": null,
    "evaluated_count": 89,
    "requested_count": 89,
    "resolved_count": 89,
    "changed_content_count": 0,
    "stale_count": 0,
    "unresolvable_count": 0,
    "duplicate_count": 0
  },
  "output": {
    "candidate_scope_member_count": 14,
    "verified_count": 14,
    "uncertain_count": 0,
    "needs_review_count": 2,
    "excluded_count": 73
  },
  "created_at": "ISO-8601",
  "completed_at": "ISO-8601",
  "membership_expires_at": "ISO-8601",
  "lineage_expires_at": "ISO-8601"
}
```

Verified membership is stored separately:

```json
{
  "search_session_id": "uuid",
  "candidate_scope_id": "persistent-resume-entity-id",
  "content_hash_at_event": "content-fingerprint",
  "result_bucket": "verified_match",
  "lineage_warning_codes": ["EARLIER_CONDITIONS_NOT_RECERTIFIED"],
  "decision_evidence": {
    "schema_version": "verified_scope_decision.v1",
    "decision_mode": "deterministic|llm|mixed",
    "facts_version": "string-or-null",
    "reason_codes": ["reason-code"],
    "decision_summary": "bounded non-secret explanation",
    "evidence_fingerprint": "sha256-of-canonical-minimum-decision-evidence"
  },
  "created_at": "ISO-8601"
}
```

For the 30-day membership-retention period, the committed scope-membership row is the authoritative record that a candidate entered the verified refinement scope and contains the minimum authoritative decision evidence explaining why. The richer existing per-candidate AI search audit remains the detailed historical evidence surface and may retain more explanation according to its own retention policy.

`lineage_warning_codes` is an empty array for candidates with no inherited warning. If a candidate's content changes after any earlier chain step, every later verified membership and exported lineage projection carries `EARLIER_CONDITIONS_NOT_RECERTIFIED` until the complete chain is deliberately re-run against the current content. A later step must not clear the warning merely because the content did not change again.

The scope-membership record is not an indefinite historical archive. If policy requires candidate-level authoritative evidence beyond 30 days, the membership/evidence retention period must be extended accordingly before deployment. A refinable search must never depend solely on a best-effort audit write to prove why a candidate entered its scope.

Session status semantics:

- `started`: the request was accepted and owns its idempotency key, but no completed authoritative scope has been committed;
- `complete`: analysis completed and the canonical summary plus any verified membership rows were committed atomically;
- `failed`: the accepted request terminated before a complete authoritative scope could be committed because of validation after acceptance, analyzer failure, provider failure, persistence failure, cancellation, or an unrecoverable runtime exception.

A `failed` session never becomes a refinement parent. Retrying a failed user action creates a new `search_request_id`. Reusing the failed request's existing `search_request_id` returns its recorded terminal failure and must not start a second worker. Reusing that ID with different canonical request content or from a different actor returns a generic `SEARCH_REQUEST_ID_CONFLICT` without revealing the original actor or request.

Recommended idempotency-claim schema:

```json
{
  "search_request_id": "globally-unique-client-uuid",
  "actor_user_id": "stable-auth-mode-qualified-id",
  "canonical_request_fingerprint": "sha256",
  "status": "started|complete|failed",
  "worker_lease_expires_at": "ISO-8601-or-null",
  "search_session_id": "uuid-or-null",
  "lineage_failure_code": "LINEAGE_CREATE_FAILED-or-null",
  "terminal_failure_code": "non-sensitive-code-or-null",
  "terminal_summary": {},
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

Claim creation, duplicate resolution, worker-lease renewal, and terminal transition are durable operations independent of whether a lineage row exists. Terminal summaries must contain no result cards, raw prompt, parent details, or cross-actor information.

`runtime_settings_fingerprint` is the SHA-256 hash of canonical JSON using schema `ai_search_runtime.v1`. The canonical JSON must include every non-secret runtime value that can affect candidate selection, hard-filter evaluation, LLM reasoning, or result classification, including:

- `LLM_Promotion_Stage`;
- enabled AI Search feature flags;
- hard-filter and confidence thresholds;
- minimum similarity score;
- reasoning model name and version;
- embedding model name and version;
- vector index and namespace identifiers;
- parser/rule-set schema versions; and
- other explicitly registered non-secret AI Search behavior settings.

The fingerprint must exclude credentials, tokens, passwords, secret keys, raw prompts, user-specific filesystem paths, and unrelated UI preferences. Adding or removing a fingerprinted setting requires a new fingerprint schema version. The committed session stores the schema name and hash; diagnostic tooling may separately render the canonical non-secret input for authorized operators.

### 7.2 Scope record storage

The primary store must atomically transition the accepted search-session row to `complete`, write its canonical summary, and write all verified scope-membership rows with their minimum decision evidence.

The existing per-candidate AI search audit is useful for richer explanation and evidence, but it must not be the only authoritative scope or minimum-decision-evidence store because per-candidate audit writes can partially fail.

Recommended implementation:

- introduce a small `SearchScopeRepository` abstraction;
- provide a local SQLite implementation using portable runtime storage;
- provide a Supabase-backed implementation when cloud persistence is active;
- implement Supabase request claiming and scope completion through versioned transactional PostgreSQL RPC/database functions, such as `claim_ai_search_request_v1` and `complete_ai_search_scope_v1`; completion validates the current `started` session, writes the canonical summary and every verified membership row, and transitions the session to `complete` in one database transaction;
- do not implement Supabase atomic completion as independent PostgREST requests from the application process;
- use an indexed `search_session_lineage` table keyed by `search_session_id` that also stores the canonical search summary;
- use a separate indexed `search_request_claims` table keyed globally by `search_request_id`;
- use an indexed `search_scope_membership` table keyed by `(search_session_id, candidate_scope_id)`;
- enforce a global unique idempotency key on `search_request_id` and separately index `actor_user_id`;
- index `root_search_session_id`, `parent_search_session_id`, `actor_user_id`, and `candidate_scope_id`;
- extend the AI registry with a persistent `candidate_scope_id` mapping and indexed content-hash aliases used to resolve known resume versions;
- store audited non-exact identity-link events and enforce the authority rules in Section 3.7;
- use the existing repository/factory precedence conventions;
- keep persisted app settings as the source of truth for any related feature flag;
- do not use OS-specific paths;
- do not reset or rebuild the local vector index.

An optional compact JSON array may be included in diagnostic exports, but membership queries and runtime intersection must use the indexed membership records rather than scanning JSON arrays.

If implementation chooses to extend an existing repository instead, it must still provide the same transactional and indexed membership contract.

Supabase deployment order is mandatory:

1. deploy tables, indexes, row-level-security policies, and the versioned transactional RPC;
2. run migration verification that exercises rollback on an intentionally invalid membership row;
3. deploy repository code capable of detecting the RPC/schema version;
4. keep the Supabase scope repository unavailable and refinement disabled until the required schema/RPC version is verified; and
5. only then enable Supabase scope writes or reads.

The RPC accepts canonical session summary and verified memberships as structured parameters, validates counts and actor/session ownership, and returns the committed session version. A partial membership write or summary-only commit must roll back completely.

The Supabase migration bundle must define, at minimum:

- `ai_search_request_claims` with global unique `search_request_id`, canonical fingerprint, worker lease, terminal status, and bounded terminal summary;
- `ai_search_session_lineage` with unique `search_session_id`, foreign-key parent/root relationships, frozen context, acknowledgement metadata, canonical counts, retention timestamps, delivery state, and mirror state;
- `ai_search_scope_membership` with primary key `(search_session_id, candidate_scope_id)`, minimum decision evidence, warning codes, content hash, and foreign key to lineage;
- `ai_candidate_identity_aliases` with alias type/value/provenance and authoritative-link eligibility;
- `ai_candidate_identity_links` with audited non-exact link authority;
- `ai_search_changed_content_acknowledgements` with actor/parent/request/set binding, expiry, and consumed timestamp; and
- version-reporting metadata/RPC so the application can verify compatibility before enabling the repository.

Required constraints include global request-ID uniqueness, parent/root/session referential integrity, one-time acknowledgement consumption, bounded status enums, and indexes used by ownership, expiry, membership, retry, and cleanup queries. Service-role/RLS policy must prevent untrusted clients from directly creating authoritative scope, claim, identity-link, or acknowledgement records.

### 7.3 Dual-write atomicity contract

When dual-write mode is active, scope persistence is **primary-atomic, secondary best-effort**:

1. The configured primary scope repository atomically commits the search-session row and all membership rows.
2. Refinement availability is enabled after the primary transaction succeeds.
3. The secondary mirror is attempted idempotently.
4. A secondary failure is audited and queued/retried through the extended persistent mirror-state contract below; the existing two-boolean state store is not sufficient.
5. A secondary failure does not disable refinement on the active primary-backed application.

In dual-write mode, runtime scope reads must fall back to the primary store when the secondary mirror does not yet contain the scope. A separate secondary-only runtime that cannot access the primary and cannot find the mirrored scope must report `REFINEMENT_SCOPE_STORE_UNAVAILABLE`; it must not reconstruct or guess the scope from partial audit data.

Mirror retries use exponential backoff with jitter, capped at 24 attempts over no more than 7 days. After the retry ceiling, `mirror_status` transitions from `pending` to `failed`, the terminal error is retained for operations, and an operational alert or equivalent visible health signal is emitted. A later explicit operator retry remains idempotent.

The existing two-boolean `DualWriteStateStore` is insufficient for this contract and must be extended or replaced for scope mirroring. Required durable mirror state:

```json
{
  "mirror_key": "scope:<search_session_id>",
  "primary_done": true,
  "secondary_status": "pending|complete|failed",
  "attempt_count": 3,
  "next_attempt_at": "ISO-8601-or-null",
  "last_attempt_at": "ISO-8601-or-null",
  "last_error_code": "bounded-non-sensitive-code-or-null",
  "terminal_failed_at": "ISO-8601-or-null",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

A bounded background mirror worker leases due `pending` rows, calls the secondary transactional completion operation idempotently, increments `attempt_count`, records bounded errors, schedules jittered retry, and marks terminal failure at the documented ceiling. Process restart must not lose pending retries. Operator retry clears terminal failure only through an audited explicit action and does not create a duplicate scope.

### 7.4 Scope record write timing

The backend must save the completed scope record before telling the frontend that refinement is available.

If the search succeeds but the scope record fails to save:

- return the search results;
- set `refinement.available` to `false`;
- include a non-blocking refinement-unavailable reason;
- do not pretend the result set is safely refinable.

### 7.5 Actor identity contract

Ownership checks use `actor.user_id`, not username or role labels.

Stable `user_id` resolution:

- cloud auth: `cloud:<uuid>`, using the stable UUID from the authenticated cloud user record (`public.users.id`, or `auth.uid()` when that is the configured identity provider);
- local auth: `local:<uuid>`, where the UUID is generated once and persisted in a durable local-user identity mapping associated with the local user record.

`username_at_event` and `role_at_event` are descriptive audit fields only. Renaming a user or changing a role must not change `user_id`.

Successful authentication must place the stable `user_id` into the server session so refinement routes do not re-identify users by username.

Existing local users must receive durable local UUIDs before Refinement Mode is enabled. If auth mode changes between parent and child search, the chain cannot continue unless an explicit identity-mapping migration proves that the new principal is the same user. The safe default is to invalidate the interactive chain.

Before changing auth mode while unexpired scope memberships exist, deployment tooling must either:

- run an explicit identity-mapping migration from old `actor.user_id` values to the new stable principal IDs; or
- accept that those existing chains become unrefinable.

The migration must never infer identity from username alone without an operator-approved mapping.

### 7.6 Canonical scope identifier

Each candidate result and scope record must use `candidate_scope_id`.

Required properties:

- persistent across known versions of the same resume entity;
- identical for cross-platform duplicate copies of the same resume;
- does not expose an absolute filesystem path;
- can be resolved against the current rank folder safely.

Implementation contract:

- generate and persist an opaque `candidate_scope_id` UUID for a new registry resume entity;
- treat `stable_resume_id` from `repositories/resume_identity.py` as a legacy identifier whose provenance must be established before use;
- use a legacy `stable_resume_id` as an exact-content alias only when a separately verified non-empty content hash proves it was content-derived; classify path-fallback and unknown-provenance values as non-authoritative lookup hints;
- preserve that identifier for known resume updates;
- store the latest `content_hash` separately;
- maintain aliases when duplicate content or cross-platform copies resolve to the same resume entity; and
- never merge changed resumes based only on similar filenames.

Existing registry records must be backfilled in place with generated persistent `candidate_scope_id` values. Existing `stable_resume_id` and vector/index `resume_id` values are imported with explicit provenance. Only separately verified content-derived identifiers become authoritative exact-content aliases; path-fallback and unknown-provenance identifiers remain lookup hints requiring another approved identity-link authority. This identity migration must not require resetting the local vector index.

Backfill runs as a bounded background migration. Root searches remain available while it progresses, but Refinement Mode remains disabled for any result set whose required identity mappings are not yet ready. Setup/diagnostics must expose backfill status and failures. The migration must not block application startup or perform an index reset.

When a preflight or restored recovery draft depends on identities that are still actively being backfilled, return `REFINEMENT_SCOPE_BACKFILL_PENDING` rather than `REFINEMENT_SCOPE_UNRESOLVABLE`. This is a temporary, retryable state. The response includes `retryable: true` and a recommended `retry_after_seconds`; the frontend preserves the restored chain and retries without treating it as broken. Once backfill completes, normal preflight resolution determines whether any members are genuinely unresolvable.

### 7.7 Retention and cleanup

Scope membership is temporary operational data.

Retention policy:

- retain full `search_scope_membership` rows and detailed search-session payload for 30 days after search completion;
- after 30 days, prune membership and detailed payload while retaining indexed lineage and compact summary metadata for 365 days;
- compact lineage retains IDs, actor ID, search mode, counts, timestamps, and prompt hash, but not raw prompt text;
- retain per-candidate AI search audit according to its existing audit-retention policy;
- after membership expiry, return `REFINEMENT_PARENT_EXPIRED` for attempts to refine that parent.

Cleanup implementation:

- local storage performs bounded cleanup opportunistically at repository initialization and after completed scope writes;
- each local cleanup invocation deletes at most 1,000 expired membership or lineage rows, then yields until a later invocation;
- Supabase storage uses a scheduled cleanup job or equivalent maintenance task;
- cleanup failures are logged but do not block active searches;
- cleanup must never delete the local vector index or candidate facts.

Idempotency claims and their canonical request fingerprints expire 30 days after their terminal transition, including claims for non-refinable roots or failed lineage creation that have no scope membership. Scope-backed claims may be cleaned up with full membership after 30 days. Compact lineage may remain for 365 days without retaining an active idempotency claim.

---

## 8. API Contract

### 8.1 Root search request

The existing root-search request remains compatible:

```http
GET /analyze_stream
  ?search_request_id=<client-generated-uuid>
  &prompt=...
  &rank_folder_id=<opaque-discovered-rank-id>
  &applied_ship_type=...
  &experienced_ship_type=...
```

Absence of `parent_search_session_id` means root search.

For backward compatibility, legacy root-search callers may omit `search_request_id`; the backend generates one. Legacy callers may also send `rank_folder=Chief_Engineer`, which the backend maps only through an exact unambiguous discovered allowlist match. The updated frontend must send both `search_request_id` and `rank_folder_id`. Refinement requests require `search_request_id`.

Before accepting a root request or writing its lineage row, the backend must server-canonicalize the rank context:

1. Load the server-discovered allowlist of rank-folder IDs beneath the active download root.
2. Require the submitted `rank_folder_id` to match exactly one allowed canonical entry, or map a legacy `rank_folder` value to exactly one entry for compatibility.
3. Reject absolute paths, `.`/`..`, path separators, encoded separators, NULs, ambiguous aliases, and any value not present in the allowlist.
4. Resolve the active download root and selected rank directory, including symlinks.
5. Require the resolved rank directory to be an allowed direct child contained beneath the resolved active download root.
6. Persist only the canonical `rank_folder_id` and canonical display label, never the raw client value.

Failure returns `INVALID_RANK_FOLDER` before analyzer construction, telemetry start, prompt audit scheduling, or search-session lineage acceptance. The same canonical context is inherited by every refinement child.

Canonical rank identity contract:

- `download_root_id` is an opaque UUID maintained in the local scope/identity repository for the canonical resolved active download root;
- `rank_folder_id` is an opaque UUID maintained in a server-side rank catalog and bound to one `download_root_id` plus one exact discovered direct-child directory entry;
- ordinary folder rename, deletion/recreation, symlink retargeting, or active-download-root change does not silently retain the old `rank_folder_id`;
- an explicit audited rank-folder move/rename operation may preserve the ID only after proving containment beneath the intended active root;
- the client receives the opaque ID and display label from rank discovery, but never constructs the ID itself; and
- no raw absolute root path is persisted in search lineage or sent to the client.

Rank discovery returns entries shaped as:

```json
{
  "rank_folder_id": "opaque-uuid",
  "display_label": "Chief Engineer",
  "legacy_folder_name": "Chief_Engineer"
}
```

`legacy_folder_name` exists only for compatibility/display diagnostics and is never the updated frontend's authoritative selection value.

Every refinement must re-resolve its frozen `download_root_id` and `rank_folder_id` against the current active download root before scope resolution. If the active root changed, the catalog entry disappeared, the folder was renamed without an approved catalog update, or resolved containment no longer holds, reject with `REFINEMENT_CONTEXT_UNAVAILABLE`. Do not search another folder with the same display name and do not fall back to a root search.

### 8.2 Refinement request

Recommended refinement request:

```http
GET /analyze_stream
  ?search_request_id=<client-generated-uuid>
  &prompt=...
  &parent_search_session_id=<uuid>
  &changed_content_acknowledgement_id=<uuid-if-required>
```

For refinement requests:

- `search_request_id` is generated once by the frontend for the attempt and is used for idempotency and disconnect observability;
- `parent_search_session_id` is authoritative;
- `changed_content_acknowledgement_id` is required only when authoritative scope resolution finds changed-content members and must reference the server-issued one-time acknowledgement bound to this actor, parent, request ID, and changed-content set;
- the backend loads rank and locked filters from the parent scope record;
- client-supplied rank or ship filters must not override the parent context;
- if client-supplied values are present and differ, reject the request.

This avoids sending candidate IDs or filenames through the URL and preserves the current EventSource transport.

The confirmation dialog is a frontend product requirement. The backend does not accept or validate a theatrical `refinement_confirmed=true` boolean.

### 8.3 Scope preflight request

Before showing the refinement confirmation dialog, the frontend requests:

```http
GET /search_scope_preflight
  ?parent_search_session_id=<uuid>
```

The preflight:

- uses the same authentication and authorization gate as `/analyze_stream`;
- enforces same-actor ownership;
- resolves the current parent scope without running prompt evaluation;
- returns requested, resolvable, changed-content, stale, duplicate, and unresolvable counts;
- returns the frozen search context; and
- never modifies or expands the parent scope.

The endpoint is subject to the application's normal authenticated per-actor rate limiting. A recommended initial limit is 30 preflight requests per actor per minute with a short cache keyed by actor, parent session, and current registry revision.

Successful response:

```json
{
  "success": true,
  "parent_search_session_id": "uuid",
  "search_context": {
    "download_root_id": "opaque-active-root-uuid",
    "rank_folder_id": "canonical-discovered-rank-id",
    "rank_folder": "Chief_Engineer",
    "applied_ship_type": "",
    "experienced_ship_type": ""
  },
  "scope_summary": {
    "requested_count": 14,
    "resolved_count": 13,
    "changed_content_count": 1,
    "changed_content_set_fingerprint": "sha256-or-null",
    "stale_count": 0,
    "unresolvable_count": 1,
    "duplicate_count": 0,
    "unavailable_percentage": 7.14
  },
  "refinement": {
    "available": true,
    "unavailable_reason": "",
    "retryable": false,
    "retry_after_seconds": null
  }
}
```

Failure responses use the refinement-specific error codes in Section 8.7 and do not reveal parent counts or context to an unauthorized actor.

Backfill-pending response:

```json
{
  "success": false,
  "error_code": "REFINEMENT_SCOPE_BACKFILL_PENDING",
  "message": "Refinement will be available when candidate identity preparation completes.",
  "retryable": true,
  "retry_after_seconds": 10
}
```

Preflight results are advisory and short-lived. The analyzer performs authoritative scope resolution again when the refinement begins.

#### 8.3.1 Changed-content acknowledgement request

After the user accepts the separate changed-content warning, the frontend requests:

```http
POST /search_scope_changed_content_acknowledgements
Content-Type: application/json

{
  "parent_search_session_id": "uuid",
  "search_request_id": "intended-child-request-uuid",
  "changed_content_set_fingerprint": "fingerprint-returned-by-preflight"
}
```

The route uses the same authentication, same-actor authorization, and rate-limit policy as preflight. It authoritatively resolves the parent scope again. It issues a one-time acknowledgement only when the submitted fingerprint exactly matches the complete current changed-content set.

Successful response:

```json
{
  "success": true,
  "acknowledgement_id": "uuid",
  "changed_content_set_fingerprint": "sha256",
  "expires_at": "ISO-8601-no-more-than-five-minutes-later"
}
```

The acknowledgement record contains no raw resume text and is stored server-side. The acknowledgement ID is only a lookup handle and cannot authorize anything without the existing authenticated session plus the bound actor, parent, request ID, and current changed-content fingerprint. Submitting an empty, stale, altered, wrong-actor, wrong-parent, wrong-request, expired, or already-consumed acknowledgement returns `REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED` without revealing candidate details.

### 8.4 Backend validation sequence

For a root request:

1. Authenticate the actor and resolve stable `actor.user_id`.
2. Validate the `search_request_id` syntax and perform a non-disclosing lookup that rejects an ID already claimed by another actor without revealing request details.
3. Canonicalize and validate the rank context according to Section 8.1.
4. Canonicalize and validate other scope-defining filters.
5. Build the canonical request fingerprint from the authenticated actor, canonical prompt, and canonical context.
6. Validate or claim the globally unique `search_request_id` under the exact duplicate/idempotency rules.
7. Create the root search-session lineage using only canonical context.

For a refinement request:

1. Authenticate the actor using the existing route policy.
2. Resolve the stable current `actor.user_id`.
3. Validate the `search_request_id` syntax and perform a non-disclosing lookup that rejects an ID already claimed by another actor without revealing request details.
4. Validate the syntax of `parent_search_session_id`.
5. Load the completed, unexpired parent scope record.
6. Require the immediate parent record's `actor.user_id` to equal the current `actor.user_id`.
7. Require at least one parent `candidate_scope_id` membership row.
8. Derive the frozen rank and filters from the parent record.
9. Revalidate the frozen `download_root_id` and `rank_folder_id` against the current active root and resolved containment.
10. Build the canonical request fingerprint from the authenticated actor, canonical prompt, parent session, and frozen context.
11. Validate or claim the globally unique `search_request_id` under the exact duplicate/idempotency rules.
12. Resolve the canonical scope against current rank candidates and compute the complete changed-content set fingerprint.
13. If changed-content members exist, validate and consume the one-time acknowledgement bound to the actor, parent, request ID, and exact changed-content set.
14. Create or update the new child search-session lineage with the acknowledgement record.
15. Pass the resolved scope to the analyzer.
16. Persist the completed child scope before enabling another refinement.

Ownership is checked against the immediate parent only. Because every child must be created by the same actor as its immediate parent, same-actor ownership is inherited transitively through the chain without walking back to the root on every request.

Canonical validation that fails before the idempotency claim, including `INVALID_RANK_FOLDER`, does not create a `started` lineage or idempotency row. The preliminary request-ID lookup is read-only and returns only the generic conflict response for an ID already claimed by another actor; validation ordering must not reveal the existing request's actor, status, prompt, or context.

### 8.5 Complete SSE event additions

The existing `complete` SSE event remains backward-compatible. The following fields are additive; all existing complete-event fields remain unchanged:

```json
{
  "search_session": {
    "search_session_id": "uuid",
    "root_search_session_id": "uuid",
    "parent_search_session_id": "uuid-or-null",
    "search_mode": "root|refinement",
    "refinement_depth": 0,
    "search_request_id": "client-generated-uuid",
    "delivery_status": "pending"
  },
  "search_context": {
    "download_root_id": "opaque-active-root-uuid",
    "rank_folder_id": "canonical-discovered-rank-id",
    "rank_folder": "Chief_Engineer",
    "applied_ship_type": "",
    "experienced_ship_type": ""
  },
  "scope_summary": {
    "frozen_rank_population_count": 89,
    "eligible_population_count": 14,
    "retrieved_count": null,
    "evaluated_count": 13,
    "requested_count": 14,
    "resolved_count": 13,
    "changed_content_count": 1,
    "stale_count": 1,
    "unresolvable_count": 0,
    "duplicate_count": 0
  },
  "refinement": {
    "available": true,
    "candidate_count": 6,
    "unavailable_reason": ""
  }
}
```

`delivery_status` in the emitted complete event is necessarily `pending`: the application cannot truthfully claim that the browser received the event before yielding it. The canonical lineage record is updated after the yield/generator lifecycle according to Section 8.8. Interactive behavior must not depend on the event's provisional delivery status.

### 8.6 Match payload additions

Every match-like payload returned to the frontend must include the following fields in addition to all existing fields:

```json
{
  "candidate_scope_id": "persistent-resume-entity-id",
  "content_hash": "current-content-fingerprint",
  "lineage_warning_codes": ["EARLIER_CONDITIONS_NOT_RECERTIFIED"],
  "filename": "display-name.pdf"
}
```

This applies to:

- verified matches;
- uncertain matches; and
- needs-review matches.

Only verified-match identifiers are written into the next refinement scope.

`lineage_warning_codes` is always present and is an empty array when no warning applies. The frontend renders the documented changed-content warning whenever it contains `EARLIER_CONDITIONS_NOT_RECERTIFIED`, and the backend carries that warning into later verified memberships and exported lineage.

### 8.7 Refinement-specific errors

Use explicit error codes:

- `INVALID_RANK_FOLDER`
- `REFINEMENT_PARENT_NOT_FOUND`
- `REFINEMENT_PARENT_NOT_COMPLETE`
- `REFINEMENT_PARENT_EXPIRED`
- `REFINEMENT_PARENT_UNAUTHORIZED`
- `REFINEMENT_CONTEXT_MISMATCH`
- `REFINEMENT_CONTEXT_UNAVAILABLE`
- `REFINEMENT_SCOPE_EMPTY`
- `REFINEMENT_SCOPE_UNRESOLVABLE`
- `REFINEMENT_SCOPE_STORE_UNAVAILABLE`
- `REFINEMENT_SCOPE_BACKFILL_PENDING`
- `REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED`

None of these errors may trigger a full-search fallback.

`REFINEMENT_SCOPE_BACKFILL_PENDING` is temporary and retryable. The other parent/scope validation errors are structural unless their response explicitly sets `retryable: true`.

Both root and refinement requests may also return:

- `SEARCH_REQUEST_STORE_UNAVAILABLE` when the durable request claim cannot be created or resolved safely;
- `SEARCH_REQUEST_ID_CONFLICT` when a globally unique request ID is reused by another actor or with different canonical request content;
- `SEARCH_REQUEST_IN_PROGRESS` when the same actor repeats the identical canonical request while its original attempt is still `started`;
- `SEARCH_REQUEST_ALREADY_COMPLETE` when the same actor repeats the identical canonical request after it completed; or
- `SEARCH_REQUEST_ALREADY_FAILED` when the same actor repeats the identical canonical request after it failed.

Standard JSON error response:

```json
{
  "success": false,
  "error_code": "STABLE_MACHINE_CODE",
  "message": "Non-sensitive user-facing message.",
  "retryable": false,
  "retry_after_seconds": null
}
```

All error responses and terminal SSE error/status events include `error_code`, `message`, `retryable`, and `retry_after_seconds`. Structural validation failures are non-retryable unless the underlying condition can change without user correction. Backfill, temporary store unavailability, and an in-progress duplicate may be retryable. Retry delay is `null` when not applicable.

Terminal SSE shapes:

```json
{
  "type": "error",
  "error_code": "REFINEMENT_CONTEXT_UNAVAILABLE",
  "message": "The original rank folder is no longer available.",
  "retryable": false,
  "retry_after_seconds": null
}
```

```json
{
  "type": "request_status",
  "error_code": "SEARCH_REQUEST_IN_PROGRESS",
  "message": "This request is already running.",
  "retryable": true,
  "retry_after_seconds": 5,
  "search_session_id": "uuid-or-null",
  "summary": {}
}
```

`summary` is present only for allowed same-actor duplicate outcomes and is bounded/non-sensitive. It never includes result cards, raw prompts, candidate identifiers, or parent details.

Transport contract:

- JSON endpoints return the standard shape with HTTP `400` for malformed/canonical-validation failures, `401/403` for ordinary authentication/authorization policy, `404` or a generic non-disclosing response for inaccessible parent resources, `409` for request-ID conflicts or acknowledgement-set changes, `410` for expired parents, and `503` for temporary required-store unavailability.
- `/analyze_stream` performs authentication, request-ID lookup/claim, canonical root or parent-context validation, and required acknowledgement validation before starting analysis. Once an SSE response has begun, any later failure is emitted as one terminal `error` event using the standard fields and the stream closes.
- Exact duplicates use one terminal `request_status` SSE event with the same standard fields plus the existing search-session ID and allowed summary fields; the stream then closes.
- The frontend must never depend on parsing an EventSource HTTP error body. Pre-stream HTTP failures are surfaced through the application's normal authenticated request/preflight path or a documented EventSource error fallback.

### 8.8 SSE disconnect and retry semantics

Core Refinement Mode does not restore result cards after browser refresh. When enabled, the Resilience Extension restores only the bounded same-actor frontend recovery draft; it does not reconstruct missing result cards from backend lineage or automatically attach an unseen completed request.

For an in-page SSE transport failure:

- the frontend closes the `EventSource` in `onerror` to prevent uncontrolled automatic reconnect;
- the frontend returns to `active_idle` and preserves the parent results;
- retrying creates a new `search_request_id`;
- the backend may finish an already-running search after the client disconnects;
- a completed disconnected search may persist its scope and audit for operational accuracy, but its delivery lifecycle is recorded using the rules below;
- the disconnected result is not automatically attached to the user's interactive chain; and
- expired or orphaned memberships are removed by normal retention cleanup.

For a local-service interruption or renderer restart:

- the frontend restores the last safe completed state from the same-actor recovery draft;
- an interrupted running attempt is not automatically resumed or attached to the interactive chain;
- the backend may retain a terminal `complete` or `failed` operational record for that attempt;
- a disconnected result is attached only through a future explicit recovery workflow designed for that purpose, not by v0.1 automatic recovery; and
- any retry is a new user action with a new `search_request_id` and, for refinement, a new confirmation.

Repeated requests with the same globally unique `search_request_id` must not start duplicate workers, attach to a running SSE producer, replay result cards, or create duplicate scope records.

Exact duplicate behavior:

- original status `started`: emit a terminal `request_status` SSE event with `SEARCH_REQUEST_IN_PROGRESS`, the existing search-session ID, and a recommended retry delay;
- original status `complete`: emit a terminal `request_status` SSE event with `SEARCH_REQUEST_ALREADY_COMPLETE`, the existing search-session ID, canonical summary, and refinement availability, but do not replay result cards;
- original status `failed`: emit a terminal `request_status` SSE event with `SEARCH_REQUEST_ALREADY_FAILED`, the existing search-session ID, and recorded non-sensitive failure code; and
- different actor or different canonical request fingerprint: emit a terminal generic `error` event with `SEARCH_REQUEST_ID_CONFLICT` without revealing whether the request ID exists, its actor, status, prompt, or context.

The backend stores a canonical request fingerprint with the idempotency row. The actor is part of conflict validation but not part of the unique key. A user who intentionally reruns a completed or failed search must create a new `search_request_id`.

Delivery-status semantics are transport-observability states, not proof that the browser rendered or consumed an event:

- `pending`: the canonical scope completed, but the complete event has not yet successfully passed through the generator yield lifecycle;
- `complete_event_yielded`: after yielding the complete-event bytes, the generator resumed normally, indicating that the WSGI/server transport accepted the chunk; this does not prove browser receipt;
- `disconnected_before_complete`: generator close, broken pipe, or transport exception occurred before the complete-event yield finished;
- `delivery_unknown`: the process stopped or the post-yield update could not be made, leaving receipt unknowable.

The backend commits the completed scope with `delivery_status=pending`, yields the complete event, then performs a best-effort post-yield update to `complete_event_yielded` when the generator resumes. It catches `GeneratorExit`, broken-pipe/connection exceptions, and equivalent close signals to set `disconnected_before_complete` when possible. A bounded cleanup job transitions stale `pending` delivery rows to `delivery_unknown`. No status may claim confirmed browser receipt.

---

## 9. Backend Changes Required

### 9.1 `backend_server.py`

Required changes:

- distinguish root and refinement requests;
- resolve stable actor identity for local and cloud auth;
- canonicalize root rank-folder context against the discovered allowlist and enforce resolved-path containment before accepting lineage;
- revalidate frozen root/rank identity and containment before every refinement;
- validate and resolve parent search sessions;
- issue, validate, consume, and persist changed-content acknowledgements bound to the actor, parent, request ID, and exact changed-content set;
- freeze refinement context from the parent scope;
- pass canonical scope IDs into the analyzer;
- add search-session metadata to complete events;
- persist authoritative scope records plus minimum verified-member decision evidence atomically;
- enforce globally unique request idempotency by `search_request_id` with the exact terminal-status behavior in Section 8.8;
- keep durable request claims independent of lineage creation and recover abandoned worker leases;
- expose the scope preflight endpoint;
- issue actor-bound signed recovery capabilities after successful authentication when packaged recovery is enabled;
- report whether another refinement is available;
- extend telemetry and AI search audit lineage;
- update delivery status through the post-yield/generator-close lifecycle without claiming confirmed browser receipt;
- keep existing authorization requirements;
- ensure failure to persist normal audit rows does not corrupt the authoritative scope record;
- ensure failure to persist the authoritative scope disables refinement safely.

### 9.2 Search-scope repository

Introduce or extend a repository capable of:

- atomically creating/resolving durable global request claims independently of lineage rows;
- atomically writing a completed search scope and minimum authoritative decision evidence for every verified member;
- reading a scope by search-session ID;
- validating actor ownership;
- enforcing globally unique `search_request_id` values;
- preserving parent/root lineage;
- querying indexed membership by `search_session_id` and `candidate_scope_id`;
- expiring membership and lineage according to the retention contract;
- mirroring primary commits idempotently in dual-write mode;
- executing Supabase completion through the required versioned transactional RPC rather than independent REST writes;
- persisting mirror attempt count, next-attempt time, bounded last error, and terminal-failure state for the background retry worker;
- supporting local and configured cloud-backed behavior;
- using cross-platform runtime paths.

The repository must not store absolute candidate paths as scope membership.

The AI registry may continue to store current file-location metadata for resolution, but file paths are never sent by the client and are never authoritative scope-membership keys.

### 9.3 Audit and lineage surfaces

Use three deliberately separate surfaces:

1. **`search_session_lineage` and `search_scope_membership`**
   - canonical indexed refinement lineage, ownership, summary, temporary verified scope membership, and minimum authoritative candidate-decision evidence;
   - used by the runtime to validate and execute refinements.

2. **Existing per-candidate AI search audit**
   - `Verified_Resumes/ai_search_audit.csv` and its configured repository equivalent;
   - richer historical evidence trail for individual candidate decisions and result buckets;
   - may include `search_session_id` but does not need root/parent/depth repeated on every row because those join through the indexed lineage record.

3. **Operational telemetry**
   - search health, errors, delivery state, and aggregate operational monitoring;
   - reads canonical summary counts from the committed search-session record rather than recomputing them independently.

Do not write AI Search refinement lineage into an unrelated prospect-enrollment `audit_log` table.

Failure of the richer per-candidate audit write does not invalidate a scope only when the primary atomic scope commit already contains the complete minimum decision evidence required by `verified_scope_decision.v1`. If that minimum evidence cannot be committed for every verified member, the search results may be shown but the search is non-refinable.

### 9.4 Concurrent refinements

Multiple tabs or requests may create distinct child refinements from the same completed parent scope.

Required behavior:

- each child receives a unique `search_request_id` and `search_session_id`;
- each child independently enforces immediate-parent ownership and frozen context;
- no single-child-per-parent lock is introduced;
- request idempotency prevents the same `search_request_id` from creating duplicate children; and
- one child does not mutate or invalidate its sibling's parent scope.

### 9.5 Local-service lifecycle and recovery

Required packaged-runtime changes:

- make the Electron process manager the authoritative owner of backend and local-agent child processes;
- do not terminate packaged services solely because `/client/heartbeat` calls were missed;
- shut down child services on a real Electron application quit;
- expose a narrow desktop bridge action `restartLocalServices()` that restarts only unhealthy required services without closing or reloading the renderer;
- retain the existing full-application relaunch only as a fallback when service-only recovery fails;
- restart services on their existing runtime URLs when possible so the renderer can reconnect without navigation;
- persist the Flask/session-signing secret through service restart so valid authenticated sessions are not invalidated merely because a child process restarted; and
- expose health and restart progress sufficient for the renderer recovery state machine.

For packaged mode, the launcher generates and persists the Flask/session-signing secret in the protected local runtime secret store before starting the backend. The secret is bootstrap/runtime security material, not a user-editable setting, is excluded from logs/telemetry/settings export/cloud sync, and is reused across child-service restart. Existing `NJORDHR_FLASK_SECRET` behavior may remain a bootstrap compatibility override for non-packaged launchers. If the protected secret is missing or corrupt, the launcher creates a new one, invalidates prior sessions explicitly, and requires normal re-authentication; it must not silently claim session continuity.

For browser-only and development launchers, heartbeat-based service cleanup may remain an explicit opt-in compatibility feature. It must be disabled by default, documented as launcher plumbing rather than a user-editable setting, and must not affect SeaJobs idle-session behavior.

Local-service recovery must never call SeaJobs start-session, OTP, disconnect, or reconnect routes.

### 9.6 Desktop recovery bridge contract

The preload exposes narrow, typed recovery methods. It must not accept shell commands, executable paths, arbitrary URLs, or arbitrary filesystem paths.

Start request:

```javascript
restartLocalServices({
    recovery_request_id: "client-generated-uuid",
    target: "auto|backend|agent",
    reason: "backend_unreachable|agent_unreachable|manual_retry"
})
```

Accepted response:

```json
{
  "accepted": true,
  "recovery_request_id": "uuid",
  "state": "accepted|already_running",
  "target": "backend|agent|backend_and_agent",
  "started_at": "ISO-8601"
}
```

Progress events and status polling use this shape:

```json
{
  "recovery_request_id": "uuid",
  "state": "accepted|stopping|starting_backend|starting_agent|health_checking|ready|failed",
  "target": "backend|agent|backend_and_agent",
  "updated_at": "ISO-8601",
  "message": "non-sensitive operator-facing summary",
  "failure_code": "string-or-null"
}
```

Required methods:

- `restartLocalServices(request)` accepts or identifies the recovery operation;
- `getLocalServiceRecoveryStatus(recovery_request_id)` returns the latest state; and
- `onLocalServiceRecoveryProgress(callback)` subscribes the renderer to progress.

Idempotency and concurrency:

- repeating the same `recovery_request_id` returns the existing operation state;
- while recovery is active, a different request returns `already_running` and the active request ID;
- `target=auto` restarts only services proven unhealthy;
- backend-only recovery must not restart a healthy local agent;
- agent-only recovery must not restart a healthy backend unless the agent cannot be configured safely without it; and
- no recovery operation may trigger a full application relaunch automatically.

The operation reaches terminal `ready` or `failed` within 180 seconds. On timeout it emits `failed` with `RECOVERY_TIMEOUT`. The renderer may then offer the existing full `Restart App` fallback after preserving a recovery draft.

`Open Logs` calls a separate fixed-purpose desktop bridge action that opens the configured NjordHR logs directory in Finder or Explorer. It accepts no caller-supplied path.

### 9.7 Recovery-draft authorization capability

Recovery-draft bridge authorization must not trust `actor_user_id`, role, username, or permissions supplied by the renderer.

After successful application authentication, the backend issues a short-lived signed `recovery_access.v1` capability containing:

```json
{
  "schema_version": "recovery_access.v1",
  "actor_user_id": "stable-auth-mode-qualified-id",
  "desktop_instance_id": "stable-local-desktop-instance-id",
  "auth_session_id": "opaque-session-binding-id",
  "permissions": ["save", "list", "load", "discard", "discard_all"],
  "issued_at": "ISO-8601",
  "expires_at": "ISO-8601-no-more-than-15-minutes-later",
  "nonce": "random-uuid"
}
```

The capability is signed by a persistent local recovery-capability signing key provisioned by the packaged launcher. The backend has signing access; the Electron main process has offline verification access. The renderer receives only the signed capability, never the signing key.

Signing-key lifecycle:

- the packaged launcher generates the signing key on first run and stores it in a local runtime secret store with local-OS-user-only permissions;
- each key has an opaque `key_id`, which is included in the signed capability;
- the backend receives signing access through protected local launch plumbing, while Electron main receives only the verification material required for accepted active/retained key IDs;
- rotation installs a new active key and retains prior verification material only until all capabilities signed by it have expired plus five minutes of clock-skew tolerance;
- keys are excluded from settings export, logs, telemetry, recovery drafts, and cloud sync;
- backup is optional because recovery capabilities are short-lived same-device artifacts; and
- missing, corrupt, unreadable, or unknown-key material fails closed, disables draft operations, and directs the authenticated user to start a fresh search without deleting drafts until an explicit discard or normal expiry.

Required authorization behavior:

- the Electron main process verifies signature, schema, expiry, desktop instance, and requested permission before every draft operation;
- the main process derives `actor_user_id` from the verified capability and ignores/rejects any renderer-supplied actor identity;
- capabilities expire within 15 minutes and are renewed while the authenticated backend session remains healthy;
- capabilities are held in renderer memory only and are never persisted inside recovery drafts;
- logout revokes the backend auth-session binding and requests best-effort main-process capability revocation and actor-draft deletion;
- after backend restart, draft listing/loading occurs only after successful re-authentication and issuance of a fresh capability; and
- invalid, expired, wrong-instance, wrong-permission, or revoked capabilities fail closed without returning draft metadata.

The narrow draft methods therefore use these signatures:

- `saveAiSearchRecoveryDraft(capability, draft_without_actor_id)`;
- `listAiSearchRecoveryDraftMetadata(capability)`;
- `loadAiSearchRecoveryDraft(capability, tab_id)`;
- `discardAiSearchRecoveryDraft(capability, tab_id)`; and
- `discardActorAiSearchRecoveryDrafts(capability)`.

The main process inserts the verified `actor_user_id` into stored envelopes and derives actor-scoped filenames from that verified value.

---

## 10. Analyzer Changes Required

### 10.1 Method contract

Extend analyzer entry points with an optional scope:

```python
run_analysis_stream(
    rank,
    user_prompt,
    applied_ship_type=None,
    experienced_ship_type=None,
    review_capture_callback=None,
    candidate_scope_ids=None,
)
```

The non-streaming compatibility path must accept and enforce the same scope contract if it supports refinement.

Analyzer surface semantics are strict:

- `candidate_scope_ids is None` means root search with no candidate scope;
- a non-empty collection means scoped refinement;
- `candidate_scope_ids == []` is invalid misuse and must raise or return a refinement-specific failure;
- an empty collection must never be interpreted as root search.

### 10.2 Scoped candidate resolution

When `candidate_scope_ids` is present:

1. Resolve the selected rank folder normally.
2. Preserve the existing ingestion/index-refresh behavior unless separately optimized.
3. Resolve the requested persistent `candidate_scope_ids` directly through the registry/current rank index.
4. Enumerate and normalize only the resolved scope candidates.
5. Compare current `content_hash` values with the parent membership records.
6. Preserve candidates whose content changed but persistent identity still resolves.
7. Inherit `EARLIER_CONDITIONS_NOT_RECERTIFIED` from parent membership and add it when content changed after any earlier chain step.
8. Evaluate only the resolved scope.
9. Report requested, resolved, changed-content, stale, unresolvable, and duplicate counts.

For every root and refinement search, also report the count taxonomy in Section 3.11:

- for a root search, compute `eligible_population_count` from the canonical rank/filter population before prompt-based retrieval;
- for a refinement, set `eligible_population_count` to the parent scope's requested membership count and never replace it with the full rank/filter population;
- optionally report the complete frozen rank/filter population as `frozen_rank_population_count`;
- set `retrieved_count` to the unique prompt-retrieved candidate count, or `null` when broad retrieval was not used; and
- set `evaluated_count` to the unique normalized candidates that actually enter evaluation.

Candidates outside the scope must not:

- enter hard-filter evaluation;
- enter semantic reasoning;
- appear in progress events;
- appear in audit rows for the child search; or
- appear in any result bucket.

### 10.3 Retrieval behavior

In refinement mode, candidate scope takes precedence over broad vector retrieval.

The scoped branch must never call `vector_db.query`, compound top-k retrieval, or keyword top-k retrieval to select candidates. It resolves `candidate_scope_ids` directly and may fetch the already-indexed chunks for those candidates.

The analyzer must not use any top-k retrieval step that can exclude members of the scoped population before evaluation.

### 10.4 Dedupe and cross-platform behavior

The existing stale-vector-hit and duplicate-collapse behavior must remain intact.

Scope membership comparison must use persistent `candidate_scope_id`, while duplicate detection and changed-content evidence use the current content fingerprint, so that:

- a Windows path and macOS path for the same resume collapse to one candidate;
- stale vector paths do not re-enter the scope;
- duplicate copies do not inflate refinement counts; and
- a genuinely different resume is not hidden merely because its filename is similar.

Persistent `candidate_scope_id` controls scope membership. Current `content_hash` controls content-change and duplicate evidence. The implementation must not conflate them.

Existing filename/path hints may help locate a file only after the registry has resolved the authoritative `candidate_scope_id`. They must not create, merge, retain, or expand scope membership and must not override an identity-link provenance failure.

### 10.5 Scope failure behavior

If scope resolution returns zero candidates:

- yield a refinement-specific graceful failure;
- do not call the LLM;
- do not perform broad retrieval;
- do not emit full-folder scanned counts.

---

## 11. Frontend Changes Required

### 11.1 New state

Recommended frontend state:

```javascript
const [refinementMode, setRefinementMode] = useState(false);
const [refinementAvailability, setRefinementAvailability] = useState({
    available: false,
    candidateCount: 0,
    reason: ''
});
const [searchChain, setSearchChain] = useState([]);
const [pendingRefinementConfirmation, setPendingRefinementConfirmation] = useState(null);
```

Each `searchChain` step should retain:

- search-session metadata;
- frozen search context;
- prompt;
- result snapshot;
- verified count; and
- scope summary.

The frontend retains at most 10 full result snapshots for back-navigation. Older steps remain visible as compact lineage metadata but are no longer interactive after their snapshots are discarded.

### 11.2 Analyze handler

The analysis handler must:

- treat switch-off searches as root searches;
- treat switch-on searches as refinement searches;
- submit the server-issued `rank_folder_id` for updated root searches and never construct an authoritative ID from the display label or legacy folder name;
- generate one `search_request_id` per analysis attempt;
- request and display scope preflight before refinement confirmation;
- open confirmation before a refinement request;
- preserve existing results until a refinement succeeds;
- send only the parent search-session ID for scope selection;
- never send candidate paths, filenames, or arbitrary candidate IDs as authoritative scope;
- never send an unvalidated `refinement_confirmed=true` flag;
- restore the previous state on cancellation or failure;
- append successful searches to the chain;
- preserve the additive `search_session`, `search_context`, `scope_summary`, and `refinement` objects from the complete event rather than reconstructing a result object that discards them;
- invalidate refinement availability if unlocked filters change.

The SSE handler must treat `request_status` as a terminal event type. For `SEARCH_REQUEST_IN_PROGRESS`, `SEARCH_REQUEST_ALREADY_COMPLETE`, and `SEARCH_REQUEST_ALREADY_FAILED`, it must:

- stop the loading/progress state;
- close the EventSource;
- preserve the last safe interactive result snapshot;
- display the non-sensitive status message and any retry delay;
- never attach to another worker or fabricate/replay result cards; and
- require a new explicit user action with a new request ID before starting another search.

`SEARCH_REQUEST_ALREADY_COMPLETE` may display its canonical count/refinement summary as informational text, but that summary does not become an interactive result set. `SEARCH_REQUEST_ID_CONFLICT` is handled as a terminal generic error with the same stop/close/preserve behavior and no information about the existing claim.

### 11.3 Do not clear results prematurely

The current flow clears `analysisResults` before connecting.

For refinement:

- retain the previous result snapshot while connecting and running;
- show progress separately;
- replace current results only when the refinement completes successfully;
- keep the prior result snapshot if the request errors or is cancelled.

Root searches may retain current behavior or adopt the same safer replacement behavior.

### 11.4 Locked controls

While Refinement Mode is active:

- disable rank and scope-defining filter controls;
- add lock indicators;
- expose helper text explaining why they are locked;
- ensure disabled styling remains readable;
- prevent programmatic changes from silently changing the request context.

### 11.5 Confirmation dialog

Use an accessible application modal rather than `window.confirm`.

Requirements:

- keyboard focus moves into the modal;
- focus returns to the initiating action after cancellation;
- `Escape` cancels;
- confirm button includes the candidate count;
- warning text explicitly states that excluded candidates will not be reconsidered.

### 11.6 Search-chain display

Display a concise chain near the results summary.

Minimum information:

- root eligible-population count;
- retrieved count when retrieval was used;
- evaluated count;
- each step's prompt label or sequence number;
- each step's verified count;
- current step;
- back action.

The chain must not imply that prior prompts were re-evaluated during later steps.

Changed-content candidates must retain the persistent `Earlier conditions were not re-certified` warning on their result cards and in any exported chain/lineage view.

Prompt labels must be truncated to approximately 60 characters with the full prompt available through an accessible tooltip or details interaction.

### 11.7 Recovery draft and service recovery

Add a versioned recovery store using:

- the Electron main-process recovery-draft bridge backed by `app.getPath("userData")` in packaged desktop mode; and
- origin-scoped IndexedDB with backend-issued actor-bound authenticated encryption as a best-effort browser-only fallback.

The packaged main-process store uses atomic temporary-file write-then-replace updates and local OS-user file permissions. Draft filenames use a hash of `actor.user_id` plus the random `tab_id`; raw usernames are never used in paths. The renderer never receives an arbitrary filesystem-read or filesystem-write capability.

The fixed-purpose recovery-draft bridge exposes:

- `saveAiSearchRecoveryDraft(capability, draft_without_actor_id)` to validate, trim, and atomically save the caller's allowlisted draft;
- `listAiSearchRecoveryDraftMetadata(capability)` to return metadata only for restore selection;
- `loadAiSearchRecoveryDraft(capability, tab_id)` to return one selected draft;
- `discardAiSearchRecoveryDraft(capability, tab_id)` to remove one draft; and
- `discardActorAiSearchRecoveryDrafts(capability)` for logout or explicit actor-wide discard.

The bridge accepts no renderer-supplied actor identity, arbitrary path, filename, or raw filesystem operation. It authorizes and derives the actor according to the signed capability contract in Section 9.7. The renderer restores content only after successful authentication and exact stable actor binding as required by Section 12.5.

Recommended draft envelope:

```json
{
  "schema_version": "ai_search_recovery.v1",
  "actor_user_id": "inserted-by-main-process-from-verified-capability",
  "tab_id": "stable-random-tab-id",
  "saved_at": "ISO-8601",
  "expires_at": "ISO-8601-plus-24-hours",
  "recovery_reason": "checkpoint|agent_unreachable|backend_unreachable|renderer_unload|service_restart|application_restart",
  "recovery_completeness": "full|trimmed|context_only",
  "trimmed_fields": [],
  "active_tab": "search",
  "search_state": {
    "prompt": "current prompt",
    "selected_rank_folder": "Chief_Engineer",
    "applied_ship_type": "",
    "experienced_ship_type": "",
    "refinement_state": "active_idle",
    "current_completed_results": {
      "schema_version": "recovery_results.v1",
      "verified_matches": ["recovery_result_card.v1"],
      "uncertain_matches": ["recovery_result_card.v1"],
      "needs_review_matches": ["recovery_result_card.v1"],
      "scope_summary": {},
      "search_session": {}
    },
    "search_chain": ["bounded recovery_results.v1 snapshots plus compact lineage"],
    "refinement_availability": {},
    "interrupted_attempt": {
      "search_request_id": "uuid",
      "prompt_summary": "truncated prompt",
      "parent_search_session_id": "uuid-or-null",
      "attempted_at": "ISO-8601",
      "interruption_reason": "sse_disconnect|agent_unreachable|backend_unreachable|renderer_unload|application_restart"
    }
  }
}
```

`interrupted_attempt` is `null` when no request was interrupted. Its non-null fields exist only to explain which attempt did not complete and to prefill a deliberate retry; they are never an instruction to resume automatically. `prompt_summary` is truncated to approximately 60 characters and does not replace the separately allowlisted prompt field.

Implementation requirements:

- allow only explicitly whitelisted fields;
- never serialize settings forms, authentication state, secrets, OTPs, cookies, raw resume text, active network handles, or EventSource objects;
- build a recovery projection rather than serializing raw `analysisResults`;
- validate every recovered result card against `recovery_result_card.v1`, reject unknown/nested fields, and enforce all field and array limits before persistence and after loading;
- enforce the 4 MiB per-draft and three-drafts-per-actor budgets and deterministic trim policy in Section 3.9;
- debounce ordinary checkpoints and force a best-effort checkpoint before a desktop service restart;
- retain at most the existing 10 interactive result snapshots;
- enforce the 24-hour TTL and same-actor check before restoration;
- validate timestamps using the clock-skew rules in Section 5.12;
- clear the draft on logout and explicit discard;
- restore an interrupted `active_running` state as its last completed parent state;
- re-run scope preflight before allowing the restored chain to refine again;
- show a visible recovered-state notice; and
- handle storage unavailability by continuing safely without recovery rather than blocking AI Search.

Unload and restart checkpoints:

- use `pagehide` as the primary renderer-unload checkpoint signal;
- use `beforeunload` only as a best-effort fallback;
- write `service_restart` before requesting service-only restart;
- write `application_restart` before requesting a full relaunch; and
- do not delete a valid draft merely because the page entered the back-forward cache.

Cross-tab behavior:

- store drafts internally under `(verified_capability_actor_user_id, tab_id)` rather than one shared actor key;
- use `BroadcastChannel` or equivalent best-effort coordination to announce checkpoints and explicit discards;
- retain at most the three newest non-expired tab drafts per actor;
- disclose and confirm eviction before a fourth non-expired tab draft removes the oldest retained draft;
- skip non-interactive unload/restart checkpoints that would require unconfirmed eviction and surface the choice on the next interactive load;
- on restore, offer the newest valid draft by `saved_at` and identify its originating tab timestamp; and
- never allow one tab's checkpoint to silently overwrite another tab's draft.

Service recovery requirements:

- maintain backend and agent reachability separately;
- retry transient health failures with bounded exponential backoff;
- retry immediately on `visibilitychange` to visible and browser `online`;
- use the desktop bridge to restart local services without reloading the renderer;
- preserve visible completed results while recovery runs;
- re-fetch runtime configuration and revalidate the refinement parent after recovery; and
- never treat a successful local-service recovery as a successful SeaJobs reconnect.

Browser-only recovery drafts remain in the browser origin that created them and are not imported into or exported from the packaged desktop store. Browser-only backend-unreachable UI shows `Check Again` after the operator restarts services externally.

---

## 12. Authorization, Validation, and Safety

### 12.1 Actor ownership

A refinement request must be limited to the same stable `actor.user_id` that created the immediate parent search.

Phase 1 does not allow managers or administrators to refine another user's search, regardless of role or knowledge of the search-session ID.

The permission name `refine_others_searches` is reserved for a possible later release. It has no effect in v0.1 and must not be granted implicitly through admin or manager roles.

If the application changes auth mode and cannot prove a stable mapping from the parent actor identity to the current actor identity, the refinement is rejected.

### 12.2 Server-authoritative context

The backend must derive refinement context from the parent scope record.

The frontend's disabled fields are informative only and must not be trusted as enforcement.

### 12.3 No arbitrary candidate injection

The client must not be allowed to submit arbitrary candidate IDs, filenames, or paths to expand a refinement scope.

If a future API accepts explicit candidate IDs, the backend must intersect them with the authoritative parent scope.

### 12.4 No silent fallback

Any refinement validation failure must remain a refinement failure.

Falling back to a root search would violate recruiter intent and could produce apparently valid but incorrect results.

### 12.5 Sensitive data

Search-scope records should store canonical candidate identifiers and necessary search context, not resume text or absolute file paths.

Frontend recovery drafts are local operational data and must use an explicit serialization allowlist. They must not include credentials, authentication material, settings secrets, raw resume text, or fields not required to redraw the completed AI Search experience.

Recovery-draft restoration must occur only after successful authentication and issuance of a fresh signed recovery capability bound to the exact stable `actor.user_id`. The main process derives ownership from that verified capability. The application must not restore a draft merely because a renderer supplies an actor ID or because the username, role, browser profile, or device matches.

### 12.6 Recovery-draft authorization

The renderer is an untrusted caller for recovery-store authorization.

- packaged desktop operations require a valid signed capability under Section 9.7;
- browser-only drafts require an actor-bound authenticated-encryption key issued only after successful backend authentication;
- renderer-supplied actor IDs never select, authorize, enumerate, load, or delete drafts;
- unauthorized operations return no draft metadata; and
- draft authorization is independent of the user's application role unless a future specification explicitly adds a cross-actor recovery permission.

### 12.7 Browser recovery-key durability and rotation

Browser-only recovery encryption must survive an ordinary backend restart without making existing non-expired drafts unreadable.

Key hierarchy:

- the backend owns a randomly generated 256-bit browser-recovery master key in a persistent local runtime secret store with local-OS-user-only permissions;
- the master key is bootstrap/runtime security material, not a user-editable app setting and not an environment-only secret;
- each master key has an opaque `key_id`;
- after successful authentication, the backend derives an actor-bound key-encryption key using HKDF-SHA-256 over the master key, stable `actor.user_id`, application origin, purpose `njordhr-browser-recovery-v1`, and `key_id`;
- each draft uses a fresh random data-encryption key and AES-256-GCM or an equivalently authenticated algorithm;
- the data-encryption key is wrapped by the actor-bound key-encryption key; and
- the IndexedDB envelope stores schema version, `key_id`, algorithm, nonce/IV, wrapped data key, ciphertext, authentication tag where separate, timestamps, and authenticated non-sensitive metadata.

The actor-bound key material is issued only after authentication and held in renderer memory only. Draft plaintext and unwrapped keys must not be persisted.

Rotation and failure behavior:

- rotation creates a new active master key and `key_id`;
- prior master keys remain available only for the maximum 24-hour draft TTL plus five minutes of clock-skew tolerance, then are securely retired;
- a successfully loaded draft encrypted under an older retained key is rewrapped under the active key on the next checkpoint;
- master-key files are excluded from ordinary settings export and cloud sync;
- backup is optional because drafts are same-device best-effort recovery data, but deletion, corruption, permission failure, or unavailable `key_id` makes affected drafts unreadable;
- unreadable drafts are discarded after authentication with a non-sensitive diagnostic reason and are never opened as plaintext or decrypted under another actor's key; and
- backend restart must load the existing active/retained key ring before offering browser draft restoration.

---

## 13. Telemetry and Observability

Each search should record:

- search request ID;
- search mode;
- search-session ID;
- root search-session ID;
- parent search-session ID;
- refinement depth;
- stable `actor.user_id`;
- frozen context;
- prompt hash or prompt according to current telemetry policy;
- frozen-rank-population count where reported;
- eligible-population count;
- retrieved count or explicit not-applicable value;
- evaluated count;
- input-scope requested count;
- input-scope resolved count;
- changed-content count;
- stale count;
- unresolvable count;
- duplicate count;
- whether a changed-content acknowledgement was required and validated, without emitting its token or candidate set;
- verified output count;
- uncertain output count;
- needs-review output count;
- excluded count;
- scope-persistence success/failure;
- secondary-mirror status where dual-write is active; and
- delivery status as transport observability, without treating it as confirmed browser receipt; and
- failure code where applicable.

Local-service recovery telemetry should record:

- interruption type: local agent, backend, renderer reload, or application restart;
- recovery trigger: automatic retry, visibility return, reconnect action, service restart, or full relaunch fallback;
- interruption and recovery timestamps;
- whether a valid same-actor recovery draft was available;
- whether the draft restored successfully or was discarded, expired, corrupt, or actor-mismatched;
- the restored refinement state and chain depth;
- whether a running attempt was marked interrupted; and
- service-recovery success or terminal failure.

Recovery telemetry must not include recovery-draft result payloads, resume text, credentials, OTPs, or SeaJobs authentication material.

Telemetry sinks receive stable `actor.user_id` only. `username_at_event`, `role_at_event`, and the complete actor object remain in authorized audit/lineage storage and must not be propagated as metric tags or general operational telemetry fields.

The committed search-session summary is the canonical source for aggregate counts. Operational telemetry must read those counts from the committed summary rather than recomputing them independently.

Operational logs should make it possible to answer:

- Was this a root search or refinement?
- Which parent verified set was used?
- Did any scoped candidates become stale or unresolvable?
- Did the backend reject a context mismatch?
- Was the next refinement disabled because scope persistence failed?
- Did the client disconnect before the complete event was delivered?
- Did the candidate content change between the parent and child search?
- How many candidates were eligible, retrieved, and actually evaluated?
- Did a changed-content candidate retain an earlier-conditions-not-re-certified warning?
- Did a local-service interruption occur, and was the last safe completed AI Search state restored?
- Was any interrupted request deliberately left unresumed?

---

## 14. Failure and Edge-Case Matrix

| Scenario | Required behavior |
|---|---|
| Root search returns zero verified matches | Switch remains disabled. |
| Root request supplies an unknown, absolute, traversal, symlink-escaping, or out-of-root rank folder | Reject with `INVALID_RANK_FOLDER` before lineage or analysis. |
| Active download root or frozen canonical rank folder changes/disappears before refinement | Reject with `REFINEMENT_CONTEXT_UNAVAILABLE`; do not search a same-named replacement or fall back to root. |
| Durable request claim cannot be created or resolved | Return retryable `SEARCH_REQUEST_STORE_UNAVAILABLE`; start no worker. |
| Root request claim succeeds but initial lineage creation fails | Continue only as a non-refinable root; keep the claim durable and transition it to terminal complete/failed when the worker ends. |
| Scope store unavailable when a root search starts | Root search may complete; mark results non-refinable. |
| Scope store unavailable when a refinement starts | Reject before analysis; preserve parent results. |
| Root search returns verified matches but scope save fails | Show results; disable switch with reason. |
| Refinement returns results but child scope save fails | Show results; turn mode off; unlock filters; disable further refinement with reason. |
| Recruiter cancels confirmation | Send no request; preserve results and mode. |
| Refinement returns verified matches | Replace current results; keep mode on; update scope. |
| Refinement returns zero verified matches | Disable further refinement; offer back/new search. |
| Refinement request fails | Preserve parent results and chain. |
| Refinement fails transiently | Preserve parent results and return to `active_idle`; allow a newly confirmed retry. |
| Refinement fails structurally because parent is expired, unauthorized, empty, unresolvable, or unavailable | Preserve results for reference; disable refinement and show the non-retryable reason. |
| Parent session does not exist | Reject; never run root search. |
| Parent membership has expired | Reject with `REFINEMENT_PARENT_EXPIRED`; never run root search. |
| Parent belongs to another actor | Reject; never reveal or use scope. |
| Auth mode changed and actor mapping cannot be proven | Reject as unauthorized; invalidate interactive chain. |
| Restored parent identity mapping is still being backfilled | Return retryable `REFINEMENT_SCOPE_BACKFILL_PENDING`; preserve chain and retry preflight later. |
| Parent scope contains stale candidates | Skip safely; report stale count. |
| More than 30% of parent scope is unavailable at preflight | Warn before confirmation and show resolvable count. |
| Entire parent scope is stale | Return scoped graceful failure; no broad retrieval. |
| Known scoped resume content changed | Keep persistent candidate in scope; report changed-content count. |
| Changed-content candidate is about to enter a refinement | Require explicit changed-content acknowledgement and persist the earlier-conditions-not-re-certified warning. |
| Changed-content acknowledgement is missing, stale, wrong-actor, wrong-request, consumed, or does not match the current changed set | Reject with `REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED`; show updated warning and obtain a new acknowledgement. |
| Changed resume cannot be mapped safely | Mark unresolvable and exclude; never guess by filename. |
| Duplicate resumes exist across Windows/macOS paths | Collapse to one canonical scoped candidate. |
| Filter values differ from parent context | Reject context mismatch. |
| User changes filters while mode is off | Invalidate prior refinement eligibility. |
| Browser refreshes or renderer reloads with a valid same-actor recovery draft | Restore the last safe completed state; revalidate scope before allowing refinement. |
| Browser refreshes or renderer reloads without a valid same-actor recovery draft | Start fresh; next search is root. |
| SSE disconnects during refinement | Preserve parent UI results; close auto-reconnect; mark server attempt delivery state. |
| Local agent becomes unreachable | Preserve the page and completed search state; pause dependent actions; retry and show recovery actions. |
| Backend becomes unreachable in packaged desktop mode | Preserve renderer state; restart local services through the desktop bridge; restore and revalidate the same-actor draft. |
| Backend becomes unreachable in browser-only mode | Preserve the recovery draft and explain that the external service must be restarted. |
| Local services recover after an interrupted search | Restore the last completed parent result; do not automatically resume the interrupted search. |
| SeaJobs website session reaches its existing idle timeout | Preserve existing SeaJobs disconnect and OTP reconnect behavior; local-service recovery takes no action. |
| Recovery banner appears while SeaJobs OTP entry is active | Keep OTP UI operable and focused; banner must not dismiss, cover, or submit the OTP flow. |
| Backend-only recovery occurs while the local agent and SeaJobs session are healthy | Restart only the backend; do not disturb the agent or active SeaJobs session. |
| Local agent must restart while SeaJobs was connected | Report the existing SeaJobs disconnected state afterward; do not auto-reconnect or claim it was preserved. |
| Recovery draft belongs to another actor, is expired, corrupt, or incompatible | Discard safely; never restore or infer ownership from username. |
| Multiple same-actor tabs checkpoint concurrently | Keep tab-scoped drafts; never silently overwrite another tab's draft. |
| Recovery projection exceeds 4 MiB | Apply deterministic trim policy and disclose partial restoration; never fail silently. |
| Device clock shifts around draft restoration | Apply conservative skew validation and never extend beyond recorded expiry. |
| Browser heartbeat is throttled or suspended in packaged desktop mode | Do not terminate backend or agent solely because heartbeats were missed. |
| Same actor refines one parent in two tabs | Allow distinct child searches with unique request/session IDs. |
| Same `search_request_id` is retried | Reuse existing request state; never create duplicate child scope. |
| Same request ID repeats an identical `started` request | Return `SEARCH_REQUEST_IN_PROGRESS`; do not attach to its SSE producer. |
| Same request ID repeats an identical `complete` request | Return `SEARCH_REQUEST_ALREADY_COMPLETE` with summary only; do not replay cards. |
| Same request ID repeats an identical `failed` request | Return `SEARCH_REQUEST_ALREADY_FAILED` with its non-sensitive failure code. |
| Same globally unique `search_request_id` is reused by another actor or with different request content | Reject generically as `SEARCH_REQUEST_ID_CONFLICT` without revealing original request details. |
| Complete SSE event is yielded and generator resumes | Mark `complete_event_yielded`; do not claim confirmed browser receipt. |
| Stream closes before complete-event yield finishes | Mark `disconnected_before_complete` when possible; preserve operational scope without attaching results. |
| Delivery remains `pending` after worker/transport timeout | Cleanup marks `delivery_unknown`; never infer receipt. |
| Minimum verified-member decision evidence cannot be committed atomically | Show results as non-refinable; do not enable the switch. |
| Primary scope write succeeds and secondary mirror fails | Enable refinement from primary; audit/retry secondary mirror. |
| Supabase transactional scope RPC is missing or wrong-version | Keep Supabase scope repository unavailable; do not attempt independent partial REST writes. |
| Fourth non-expired tab draft would evict another tab draft | Disclose the draft timestamp and require confirmation; cancellation skips the new checkpoint. |
| Browser recovery key is missing, corrupt, retired, or unknown | Discard affected unreadable draft after authentication with a non-sensitive reason; never fall back to plaintext or another actor's key. |
| Local index contains stale vector hits | Existing normalization removes them; no index reset. |

---

## 15. Test Plan

### 15.1 Analyzer unit tests

Add tests proving:

- scoped analysis evaluates only requested canonical candidates;
- candidates outside the scope never reach hard filters or LLM reasoning;
- semantic-only refinement evaluates the complete scoped set and never calls broad vector or keyword top-k candidate selection;
- structured-only refinement preserves the existing skip-LLM optimization;
- `candidate_scope_ids=None` runs a root search;
- `candidate_scope_ids=[]` fails and never runs a root search;
- empty scope never falls back to full-rank enumeration;
- stale scoped candidates are reported and skipped;
- known candidates whose content hashes changed remain in scope;
- changed-content warning codes are added once and inherited by later scoped results even when content does not change again;
- changed resumes that cannot be safely mapped are reported as unresolvable;
- existing vector/index resume IDs resolve through backfilled aliases without an index reset;
- path-fallback and unknown-provenance `stable_resume_id` values never authorize automatic exact-content identity linking;
- duplicate scope entries collapse;
- Windows and macOS representations of the same resume resolve to one scoped candidate;
- genuinely different resumes remain distinct;
- filename/path resolution hints never authorize identity linking or expand scoped membership;
- match payloads include `candidate_scope_id`;
- structured roots report equal eligible/evaluated counts and `retrieved_count=null`;
- semantic and compound roots report distinct eligible, retrieved, and evaluated counts; and
- evaluated count matches the unique normalized candidates that actually enter evaluation.

### 15.2 Backend route tests

Add tests proving:

- root requests remain backward compatible;
- rank discovery returns server-generated opaque rank-folder IDs and the updated frontend submits those IDs rather than constructing folder names;
- root rank folders are accepted only from the discovered canonical allowlist;
- traversal, absolute, encoded-separator, symlink-escaping, ambiguous, and out-of-root rank values are rejected before idempotency claim, lineage, or analyzer construction;
- every refinement revalidates frozen root/rank identity and rejects disappeared, renamed, root-changed, or containment-invalid context;
- refinement requests resolve parent scope correctly;
- parent context overrides or rejects mismatched client filters;
- same-actor ownership is enforced using stable `actor.user_id`;
- successful local and cloud authentication place stable `user_id` in the session;
- username and role changes do not break ownership for the same stable user ID;
- unproven auth-mode identity changes are rejected;
- invalid parent IDs do not trigger root search;
- expired parent memberships do not trigger root search;
- preflight reports changed, stale, duplicate, unresolvable, and resolvable counts;
- changed-content acknowledgement issuance revalidates scope and binds actor, parent, request ID, expiry, and exact changed-content set;
- missing, stale, altered, consumed, or mismatched changed-content acknowledgements are rejected before analysis and audited correctly;
- preflight warns when more than 30% of the parent scope is unavailable;
- completed child searches record correct parent/root/depth lineage;
- each refinement uses and records the current runtime settings and `LLM_Promotion_Stage`;
- initial lineage-write failure allows a root search to finish as non-refinable but blocks a refinement before analysis;
- scope persistence failure returns results but disables further refinement;
- primary scope success plus secondary mirror failure enables refinement and records mirror failure;
- richer audit logging failure does not corrupt an already committed authoritative scope with minimum decision evidence;
- failure to atomically commit minimum decision evidence makes completed results non-refinable;
- zero resolved scope returns a refinement-specific failure;
- concurrent child refinements from one parent are allowed;
- repeated globally unique request IDs do not create duplicate workers or child scopes;
- durable request claims prevent duplicate workers when initial lineage creation fails and abandoned worker leases become terminal failed claims;
- identical duplicate `started`, `complete`, and `failed` requests return the documented non-replay status responses;
- another actor or changed request content under the same globally unique request ID receives generic conflict without information leakage;
- SSE disconnects preserve operational records without attaching an unseen result to the interactive chain;
- delivery status follows pending, post-yield, generator-close, and unknown-cleanup semantics without asserting browser receipt;
- failed accepted sessions become terminal `failed` records and cannot become refinement parents;
- retrying a failed user action uses a new request ID, while reusing its old request ID returns the recorded terminal failure;
- runtime-settings fingerprints are stable for identical canonical non-secret settings and change when a registered behavior setting changes;
- preflight uses the same auth gate as analysis, rate limits per actor, and returns the documented response shape;
- every refinement-specific and request-specific error code in Section 8.7 has an explicit response-shape and no-fallback route test, including `REFINEMENT_PARENT_NOT_COMPLETE`, `REFINEMENT_SCOPE_EMPTY`, `REFINEMENT_CONTEXT_UNAVAILABLE`, `REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED`, and `SEARCH_REQUEST_STORE_UNAVAILABLE`;
- structural refinement failures disable retry while transient failures preserve `active_idle`; and
- backfill-pending preflight returns the retryable code and recommended retry delay without invalidating the restored chain.

### 15.3 Repository tests

Add tests proving:

- primary search-session and membership writes are atomic;
- request claims are durable and independent of lineage-row creation;
- request acceptance creates one idempotent `started` lineage row and completion atomically transitions it with membership;
- scope records round-trip all lineage and context fields;
- verified memberships round-trip minimum authoritative decision evidence;
- verified memberships round-trip inherited lineage warning codes;
- indexed candidate-scope membership preserves order-independent set semantics;
- membership queries by search session and candidate scope ID are indexed;
- partial or corrupt scope records are rejected;
- local and Supabase adapters follow the same contract;
- Supabase atomic completion uses the versioned transactional RPC and rolls back all writes when any membership is invalid;
- Supabase schema/RPC version mismatch keeps the repository unavailable rather than issuing independent partial REST writes;
- config-first repository selection remains intact;
- globally unique request IDs are enforced independently of actor ID;
- exact-content aliases may link automatically while non-exact identity links require and audit an approved authority event;
- unapproved changed-content identity guesses are rejected;
- dual-write uses primary-atomic, secondary-best-effort behavior;
- mirror retry state persists attempt count, next-attempt time, bounded error, and terminal failure across process restart;
- secondary retries are idempotent;
- mirror retries transition to terminal `failed` after the documented retry ceiling;
- backfill runs in bounded background work and gates refinement without blocking root search;
- local cleanup deletes at most 1,000 expired rows per invocation;
- idempotency claims expire 30 days after terminal transition, including claims without membership; and
- 30-day membership and 365-day lineage cleanup follow the retention contract.

### 15.4 Frontend behavior tests

Add tests proving:

- switch is disabled before a valid result set exists;
- switch enables only after verified matches and scope availability;
- enabling the switch locks filters;
- updated root-search requests use the selected server-issued `rank_folder_id`;
- enabling the switch runs scope preflight;
- every refinement requires confirmation;
- changed-content refinements require the additional explicit acknowledgement;
- the changed-content acknowledgement route is called only after user confirmation and its ID is sent with the bound refinement request;
- cancel sends no request;
- successful refinement updates the chain and count;
- complete-event search-session, context, scope-summary, and refinement metadata survive frontend result-state construction;
- failed refinement preserves previous results;
- SSE disconnect returns the UI to `active_idle` and preserves parent results;
- transient and structural failures follow their distinct state-machine transitions;
- zero-match refinement disables further prompts;
- returning to the previous step restores its results and scope;
- only the 10 most recent full result snapshots remain interactive;
- turning the switch off unlocks filters;
- changing an unlocked filter invalidates old refinement eligibility;
- every state and transition in the frontend state-machine table is covered;
- retryable and structural pre-confirmation preflight failures from `active_idle` follow their documented transitions;
- all terminal `request_status` outcomes and generic request-ID conflict stop loading, close EventSource, preserve safe results, and never fabricate result cards;
- singular and plural refinement labels render correctly;
- long prompts are truncated in the chain and expose their full text accessibly;
- structured and semantic chain presentations use truthful eligible, retrieved, evaluated, and verified counts;
- changed-content result cards and exported lineage retain the earlier-conditions-not-re-certified warning;
- completed state is checkpointed into a whitelisted, versioned, same-actor recovery draft;
- recovery drafts exclude secrets, raw resume text, and active-operation handles;
- every recovery result card obeys `recovery_result_card.v1`; unknown fields, nested evidence, free-text reasons, default insights, and resume-derived facts are rejected;
- packaged recovery uses the Electron main-process store and browser-only recovery uses actor-bound encrypted origin-scoped IndexedDB;
- packaged draft recovery remains available if the selected localhost port changes;
- recovery projections obey the 4 MiB budget and disclose full, trimmed, or context-only restoration;
- oversize projections trim deterministically without silent checkpoint failure;
- valid drafts restore after refresh, renderer reload, tab reopen, and application restart;
- `Start a new search` recovery confirmation uses the documented copy; cancellation preserves the draft and confirmed clearing removes only the selected actor draft/chain;
- expired, corrupt, incompatible, and actor-mismatched drafts are discarded;
- clock-skew validation rejects implausible timestamps without extending the recorded expiry;
- non-null interrupted-attempt metadata renders the incomplete attempt without causing automatic resume;
- `pagehide`, service-restart, and application-restart checkpoints use the documented recovery reasons;
- concurrent tabs retain separate tab-scoped drafts and restore the newest valid draft intentionally;
- fourth-tab draft eviction is disclosed and confirmed rather than silently removing another tab's draft;
- browser-only drafts remain separate from packaged drafts and browser-only recovery shows `Check Again`;
- browser-only recovery keys survive ordinary backend restart, rotate with retained key IDs, and fail closed on missing/corrupt/unknown key material;
- interrupted running searches restore the last completed parent and are not automatically resumed;
- agent/backend recovery preserves visible results and refinement state;
- restored refinement scopes are preflight-validated before reuse; and
- SeaJobs connection behavior is unaffected by local-service recovery.

### 15.5 Packaged recovery tests

Add tests proving:

- packaged backend and agent processes are not terminated solely because renderer heartbeats pause;
- real Electron application quit shuts down owned child services;
- signed recovery capabilities are issued only after successful authentication and are bound to the stable actor and desktop instance;
- recovery capability signing-key provisioning, rotation, retained verification, and corrupt-key failure behavior follow the documented contract;
- the recovery-draft bridge rejects arbitrary paths and enforces allowlist, size, actor, and tab-key validation;
- renderer-supplied actor IDs cannot enumerate, load, save, or delete another actor's packaged drafts;
- invalid, expired, wrong-instance, wrong-permission, and revoked recovery capabilities fail closed without metadata leakage;
- browser-only encrypted drafts cannot be decrypted by a different authenticated actor;
- `restartLocalServices()` restarts only the unhealthy required service or services without destroying the renderer;
- the desktop recovery bridge follows its typed request, progress, idempotency, concurrency, and 180-second timeout contract;
- `target=auto` restarts only unhealthy services;
- the renderer reconnects after service restart and refreshes runtime status;
- stable session signing preserves valid authentication through a child-service restart;
- missing/corrupt packaged session-signing material triggers explicit session invalidation and normal re-authentication rather than false continuity;
- service-only restart failure offers the existing full-relaunch fallback;
- browser-only mode provides an actionable external-restart message without pretending it can revive the backend;
- local-service recovery never calls SeaJobs start, OTP, disconnect, or reconnect routes;
- the recovery banner does not cover, dismiss, submit, or steal focus from active SeaJobs OTP entry;
- backend-only recovery preserves a healthy local agent and active SeaJobs session;
- agent restart reports SeaJobs through its existing disconnected flow without automatic reconnect;
- SeaJobs idle disconnect does not trigger local-service recovery;
- `Open Logs` opens only the configured logs directory through a fixed-purpose bridge action; and
- macOS and Windows packaged recovery behavior is equivalent.

### 15.6 Regression validation

Run existing tests covering:

- AI analyzer job constraints;
- hard-filter rules;
- cross-platform paths and duplicate collapse;
- settings precedence;
- repository adapters;
- cloud runtime where applicable; and
- backend event/audit flow.

No test should require resetting the local index.

---

## 16. Acceptance Criteria

This document contains two independently shippable deliverables:

- **Core Refinement Mode release gate:** criteria 1-20, 28, 33, 36-41, and 43-49.
- **Local-service Resilience Extension release gate:** criteria 21-27, 29-32, 34-35, 42, and 50.

Core Refinement Mode may ship after its gate passes without waiting for the Resilience Extension. A combined release must pass both gates.

1. A normal first search behaves exactly as it does before the feature when Refinement Mode is off.
2. The switch remains disabled until a completed search has at least one verified match and a saved authoritative scope.
3. Enabling the switch locks rank and all scope-defining filters.
4. Every refinement requires explicit confirmation.
5. A refinement evaluates only the previous step's verified candidates.
6. Needs-review, uncertain, excluded, and unrelated candidates cannot enter the refinement.
7. Invalid or empty refinement scopes never fall back to a full search.
8. Successful refinements can be chained while at least one verified match remains.
9. The recruiter can return to the previous retained result step within the current browser session; the Resilience Extension may preserve that ability across restart.
10. Failed or cancelled refinements do not destroy previous results.
11. Search lineage is visible in the UI and persisted in operational audit data.
12. Same-actor ownership is enforced with stable local/cloud `user_id` values rather than usernames.
13. Primary scope persistence is atomic and secondary dual-write mirroring is best-effort and idempotent.
14. Canonical candidate scoping works across Windows and macOS paths.
15. Known content changes preserve the persistent candidate scope identity and are surfaced to the recruiter.
16. Duplicate, stale, expired, and unresolvable candidate behavior follows the documented failure matrix.
17. Scoped mode never uses broad vector or keyword top-k retrieval to select candidates.
18. Structured-only refinements preserve deterministic skip-LLM behavior.
19. The frontend implements and tests the documented state machine.
20. No local index reset or platform-specific rebuild is required.
21. Local-agent or backend interruptions do not clear the last safe completed AI Search state.
22. Packaged services are not terminated solely because frontend heartbeat timers pause.
23. The desktop application can restart local services without destroying the renderer, with full application relaunch retained only as a fallback.
24. Recovery drafts are same-actor, versioned, bounded, expire after 24 hours, and contain no secrets or raw resume text.
25. Interrupted searches are never automatically resumed; the last completed parent state is restored instead.
26. Restored refinement scopes are revalidated before reuse.
27. Local-service recovery does not change SeaJobs idle timeout, disconnect, OTP, or reconnect behavior.
28. Failed-session, runtime-settings-fingerprint, preflight, mirror-retry, backfill, cleanup, and idempotency contracts follow the definitions in this specification.
29. Packaged recovery drafts use the capability-authorized Electron main-process store under `app.getPath("userData")`; browser-only drafts use separate actor-bound encrypted origin-scoped IndexedDB.
30. Each recovery draft stays within the 4 MiB budget, uses deterministic trimming, and visibly reports full, trimmed, or context-only restoration.
31. Concurrent tabs cannot silently overwrite each other's drafts.
32. The desktop recovery bridge is idempotent, reports progress, restarts only unhealthy required services, and reaches a terminal state within 180 seconds.
33. Backfill-pending scopes remain retryable and are not mislabeled as permanently unresolvable.
34. Backend-only recovery preserves a healthy local agent and active SeaJobs session; agent restart uses the existing SeaJobs disconnected flow without automatic reconnect.
35. Recovery telemetry contains stable `actor.user_id` only and does not propagate usernames or roles as metric tags.
36. Root rank-folder context is canonicalized against the discovered server allowlist and proven contained beneath the active download root before lineage or analysis.
37. Persistent candidate identity survives changed content only through an explicit authoritative identity-link event; legacy `stable_resume_id` remains an alias/hint with explicit provenance, not the persistent entity ID.
38. Globally unique request IDs follow the documented started/complete/failed duplicate behavior without SSE attachment, card replay, duplicate workers, or cross-actor information leakage.
39. Every refinable verified member has atomically committed minimum authoritative decision evidence.
40. Changed-content candidates require explicit recruiter acknowledgement and retain an earlier-conditions-not-re-certified warning on cards and exported lineage.
41. Root and refinement summaries truthfully distinguish eligible-population, retrieved, evaluated, and verified counts.
42. Recovery-draft access is authorized by a trusted actor-bound capability or browser recovery key; renderer-supplied actor IDs never authorize draft access.
43. Legacy `stable_resume_id` values authorize automatic exact-content linking only when separately verified non-empty content-hash provenance exists; path-fallback and unknown-provenance values never do.
44. Changed-content refinement requires a server-issued, actor/parent/request/set-bound, short-lived one-time acknowledgement that is validated and persisted before analysis.
45. Refinement `eligible_population_count` means parent requested scope membership, while any full frozen rank population is reported separately.
46. Supabase scope completion uses a verified transactional RPC, and mirror retries persist their full attempt/backoff/error/terminal state.
47. Durable globally unique request claims remain correct and terminal even when lineage creation fails.
48. Delivery status uses the documented post-yield/generator-close lifecycle, and every terminal request-status/error event has a frontend stop/close/preserve contract.
49. Canonical root/rank identities are defined and revalidated before every refinement; missing or changed context never silently resolves to a replacement folder.
50. Recovery result cards use an explicit privacy-safe allowlist, browser recovery keys have a durable rotation contract, and tab-draft eviction is disclosed and confirmed.

---

## 17. Implementation Sequence

### Phase 1: Scope contract and analyzer enforcement

- define `candidate_scope.v1`;
- introduce persistent `candidate_scope_id` plus separate `content_hash`;
- implement authoritative identity-link events, alias provenance, and rejection of path-fallback/unknown-provenance identity guesses;
- add durable local-user IDs and cloud-user ID resolution;
- add authoritative scope/lineage repository and indexed membership storage with minimum decision evidence;
- add durable global request-claim storage independent of lineage creation;
- deploy and verify the versioned Supabase transactional completion RPC before enabling Supabase scope storage;
- implement primary-atomic, secondary-best-effort dual-write behavior;
- implement persistent mirror retry worker/state;
- add analyzer `candidate_scope_ids` contract;
- add direct scoped resolution with no broad vector/keyword candidate selection;
- add eligible, retrieved, evaluated, and verified count reporting;
- define terminal `failed` session behavior and versioned runtime-settings fingerprinting;
- implement bounded background identity backfill and bounded cleanup;
- add scoped-enumeration, identity-change, retention, and no-fallback tests.

Phase 1 exit gate:

- all Section 15.1 analyzer tests and Section 15.3 repository tests pass;
- `candidate_scope_ids=None` and empty-list semantics are locked;
- primary transaction and secondary retry behavior are demonstrated;
- identity-link authority and minimum-decision-evidence atomicity are demonstrated;
- Windows/macOS identity tests pass before route work begins.

### Phase 2: Backend lineage and API

- add root/parent/depth search metadata;
- add stable actor ownership validation;
- canonicalize and contain root rank-folder context before lineage;
- create the opaque root/rank catalog and revalidate frozen context before every refinement;
- add globally unique request idempotency with explicit non-replay status behavior and SSE disconnect delivery state;
- add post-yield/generator-close delivery-state updates and abandoned-claim recovery;
- add scope preflight endpoint;
- add server-verifiable changed-content acknowledgement issuance, validation, consumption, and lineage;
- enforce preflight authentication, rate limiting, and documented response shape;
- resolve and validate parent scopes;
- persist completed child scopes;
- implement bounded secondary-mirror retry and terminal mirror failure;
- enrich complete SSE events;
- extend audit and telemetry.

Phase 2 exit gate:

- all Section 15.2 backend route tests pass;
- same-actor ownership, expiry, concurrent children, and disconnect behavior are demonstrated;
- existing root-search API tests remain green;
- complete-event additions are verified as backward-compatible.

### Phase 3: Frontend switch and confirmation

- add switch states;
- lock filters;
- add confirmation modal;
- add preflight warning behavior;
- add changed-content acknowledgement and persistent warning presentation;
- handle every terminal `request_status` and standard error event without leaving loading state;
- preserve previous results while refinement runs;
- add chain indicator, 10-snapshot retention, and return-to-previous-step behavior;
- display truthful eligible, retrieved, evaluated, and verified counts;
- implement every state-machine transition.

Phase 3 exit gate:

- all core-refinement portions of Section 15.4 frontend behavior tests pass;
- confirmation, changed-content acknowledgement, cancellation, zero-result, back-navigation, disconnect, and backfill-pending flows are manually smoke-tested;
- accessibility checks pass for the switch, confirmation modal, locked controls, and truncated prompt labels.

### Phase 4: Core cross-platform and packaged validation

- run macOS validation;
- run Windows path and packaged-runtime smoke tests;
- verify root rank containment, identity linking, and count presentation on both platforms;
- verify local and configured cloud scope repository behavior;
- verify no index reset or platform-specific settings change is needed.

Phase 4 exit gate:

- Section 15.6 regression validation passes for Core Refinement Mode;
- packaged macOS and Windows core-refinement smoke tests pass;
- content-change, stale-scope, duplicate-collapse, rank-containment, and truthful-count scenarios behave consistently on both platforms.

### Phase 5: Core Refinement Mode controlled rollout

- optionally gate Core Refinement Mode behind a persisted config-first feature flag during validation;
- collect refinement-specific telemetry;
- enable Core Refinement Mode broadly after its Section 16 release gate passes.

Phase 5 exit gate:

- the Core Refinement Mode acceptance gate in Section 16 passes;
- retention cleanup and secondary-mirror retry are observed in a staged environment;
- refinement telemetry shows no full-search fallback from scoped failures.

### Resilience Workstream R1: Recovery draft and frontend recovery

- add versioned same-actor recovery-draft checkpoint and restore;
- implement the actor-bound recovery capability and browser recovery-key contracts;
- implement persistent recovery/signing key rings, key IDs, rotation, and fail-closed corruption behavior;
- implement packaged main-process and browser-only encrypted IndexedDB recovery stores with separate contracts;
- implement `recovery_result_card.v1`, recovery projection, size budget, deterministic trimming, disclosed tab-draft eviction, and clock-skew validation;
- add local-service recovery banner and orthogonal recovery state machine.

R1 exit gate:

- recovery-specific portions of Section 15.4 pass;
- full, trimmed, context-only, concurrent-tab, authorization-failure, and restart restore flows are manually smoke-tested;
- no renderer-supplied actor ID can authorize draft access.

### Resilience Workstream R2: Packaged local-service recovery

- make the desktop process manager authoritative for backend and local-agent lifetime;
- remove missed frontend heartbeats as a packaged shutdown trigger;
- add service-only restart through the desktop bridge;
- implement the typed restart request, progress, status, idempotency, targeted-restart, and timeout contract;
- preserve renderer state while services recover;
- refresh runtime state and revalidate recovered refinement scope;
- retain full application relaunch as a fallback;
- verify stable authenticated sessions through child-service restart; and
- verify SeaJobs behavior remains unchanged.

R2 exit gate:

- all Section 15.5 packaged recovery tests pass;
- agent-only and backend-plus-agent interruption scenarios recover without destroying completed AI Search state;
- backend-only recovery preserves a healthy agent and SeaJobs session;
- interrupted searches remain deliberately unresumed;
- SeaJobs routes and idle behavior are untouched.

### Resilience Workstream R3: Cross-platform validation and rollout

- run macOS validation;
- run Windows path and packaged-runtime smoke tests;
- optionally gate the Resilience Extension behind a separate persisted config-first feature flag;
- enable it only after its independent Section 16 release gate passes.

R3 exit gate:

- the Local-service Resilience Extension acceptance gate in Section 16 passes;
- packaged macOS and Windows recovery smoke tests pass;
- recovery behavior and SeaJobs isolation are consistent on both platforms.

If either rollout flag is used, it must be persisted-settings-first. It must not be introduced as an env-only user-editable runtime knob.

---

## 18. Out of Scope for v0.1

- Automatically including needs-review or uncertain candidates.
- Union/OR operations that add candidates back into a refinement chain.
- Editing rank or ship-type filters while Refinement Mode remains active.
- Sharing or transferring refinement chains between users.
- Automatically re-evaluating all previous prompts at every step.
- Automatically resuming any interrupted or long-running operation, including searches, scheduled downloads, or mailbox sync.
- Changing, extending, or automatically reconnecting the SeaJobs website session.
- Restoring a recovery draft on another device or for another actor.
- Retaining recovery drafts for more than 24 hours in v0.1.
- Replacing the current search parser, hard-filter evaluator, or LLM reasoning model.
- Resetting, rebuilding, or migrating the local vector index solely for this feature.

---

## 19. Open Questions for Product Confirmation

The following product questions remain open:

1. Should the current prompt remain in the prompt box after a successful refinement, or should the box clear for the next condition?
2. Is a persisted config-first rollout flag desired for the initial release?

Resolved v0.1 decisions:

- include return-to-previous-step;
- keep Core Refinement Mode and the Local-service Resilience Extension in this document but give them independent implementation workstreams, acceptance gates, rollout flags, and release decisions;
- allow core return-to-previous-step from bounded in-memory frontend snapshots; when the Resilience Extension is enabled, restore only the bounded same-actor recovery draft after refresh or restart, without reconstructing missing result cards from backend audit;
- require same-actor ownership with stable `user_id`;
- do not allow manager/admin cross-actor refinement;
- reserve `refine_others_searches` for a possible later permissioned release;
- use primary-atomic, secondary-best-effort dual-write scope persistence;
- use a generated opaque persistent `candidate_scope_id` plus separate `content_hash`, and treat a legacy `stable_resume_id` as exact-content evidence only when separately verified non-empty content-hash provenance exists;
- retain candidate identity across changed content only through an explicitly authorized and audited identity-link event; otherwise mark the old scope member unresolvable or create a new entity;
- maintain opaque server-side download-root/rank-folder identities, canonicalize against the discovered allowlist, prove resolved-path containment before accepting lineage or analysis, and revalidate frozen context before every refinement;
- make `search_request_id` globally unique and use the exact non-attaching, non-replaying duplicate behavior defined for `started`, `complete`, and `failed` requests;
- make durable request claims independent of lineage creation so a lineage-write failure cannot start duplicate workers or leave a permanent stuck request;
- use a transactional Supabase RPC for atomic scope completion and a persistent mirror retry worker/state rather than assuming existing independent REST writes/two-boolean state are sufficient;
- atomically commit minimum authoritative decision evidence with every verified scope member before enabling refinement;
- require a server-issued, actor/parent/request/set-bound changed-content acknowledgement before analysis and retain an earlier-conditions-not-re-certified warning on cards and exported lineage;
- distinguish frozen-rank-population, eligible-population, retrieved, evaluated, verified, and scope-membership counts; refinement eligibility is always the parent requested scope, never the full rank population;
- treat SSE delivery status as post-yield transport observability rather than proof of browser receipt, and require explicit frontend handling for every terminal request-status/error event;
- retain full membership for 30 days and lineage summary for 365 days;
- treat confirmation as a frontend product interaction, not a backend boolean flag;
- keep the current prompt visible after completion unless product chooses otherwise;
- use a persisted rollout flag only if staged deployment is operationally useful;
- restore a same-actor recovery draft after refresh, tab reopen, renderer reload, or application restart;
- authorize packaged recovery-draft operations with a short-lived signed actor-bound capability and browser-only drafts with an actor-bound recovery key; never authorize draft access from a renderer-supplied actor ID;
- use persistent local signing/recovery key rings with key IDs, bounded rotation retention, and fail-closed corruption behavior;
- persist only privacy-safe `recovery_result_card.v1` projections and disclose/confirm any tab-draft eviction;
- use a 24-hour recovery-draft TTL;
- use an Electron main-process JSON-file recovery store for packaged mode and separate origin-scoped IndexedDB for browser-only mode;
- limit recovery to three tab-scoped drafts per actor and 4 MiB per draft with deterministic trimming;
- restore interrupted running searches to the last completed parent state without automatic resume;
- treat identity-backfill-pending recovery as temporary and retryable;
- make packaged service lifetime owned by Electron rather than missed renderer heartbeats;
- restart only unhealthy required services and preserve a healthy agent/SeaJobs session during backend-only recovery;
- propagate only stable `actor.user_id` to telemetry sinks; and
- leave SeaJobs idle-disconnect, OTP, and reconnect behavior unchanged.
