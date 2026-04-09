# NjordHR Context Handoff

## Repo
- Repository folder: `/Users/kartikraghavan/Tools/NjordHR`
- Primary backlog: `/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md`

## Project Summary
- NjordHR is a cross-platform recruitment operations platform.
- Core stack:
  - Python backend and local agent
  - HTML/CSS/JavaScript frontend in `frontend.html`
  - Supabase for shared persistence and cloud auth
  - Pinecone for vector storage
  - Gemini for embeddings and LLM reasoning
- Deployment model today:
  - macOS desktop installer
  - Windows installer
  - hybrid architecture with local runtime + shared cloud state

## AI Search Summary
- Embeddings provider: Gemini embedding APIs
- Configured default embedding model: `text-embedding-004`
- Fallback model path in code: `gemini-embedding-001`
- Vector store: Pinecone
- Retrieval metric: cosine similarity
- No separate reranker model is implemented
- Retrieval flow:
  - chunk resumes
  - create embeddings
  - store/query in Pinecone
  - filter by `min_similarity_score`
  - group by `resume_id`
  - run Gemini reasoning over retrieved chunks
- Relevant files:
  - `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
  - `/Users/kartikraghavan/Tools/NjordHR/config.example.ini`

## Current Platform Status

### macOS
- Fresh install and login succeeded on a clean newer macOS machine after recent packaging fixes.
- Embedded runtime portability work was done in:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/packaging/macos/build_app_bundle.sh`
- Important recent fixes:
  - rewrite absolute Python framework references to bundle-local loader paths
  - relocate dylib dependencies into app bundle
  - normalize framework install ids
  - avoid executing embedded Python too early during launcher bootstrap
  - make launcher Python-version agnostic
  - fix backend idle-shutdown `UnboundLocalError`
- Known macOS constraint:
  - embedded runtime currently targets `macOS 11+`
  - older macOS versions can fail with `Could not find platform dependent libraries <exec_prefix>`
  - recommended product policy: explicitly support only `macOS 11 Big Sur and later`
- Recommended next mac task:
  - add explicit installer-time and/or first-launch macOS version guard

### Windows
- Windows installer and startup path were stabilized earlier.
- Windows-specific work included:
  - launcher logging and diagnostics
  - config bootstrap fixes
  - shared Supabase defaults injection
  - installer privilege fixes
  - startup reliability improvements
- No Windows files were modified in the latest mac packaging round.
- Windows still needs a fresh regression smoke test after latest shared changes.

### 2026-04-09 smoke note
- A focused manual smoke run was completed after the latest shared/runtime fixes.
- Passed flow:
  - refresh while logged out
  - login
  - open Search and a rank folder
  - confirm `Experienced Ship Type Filter` shows configured values
  - search `having valid US visa`
  - search `chief officer with valid US visa`
  - search with `Dredger` experience filter
  - confirm `Needs Review` works when present
  - refresh while logged in
  - logout
- User reported all exercised paths worked properly.

## Current Git State
- Recent packaging-related commits include:
  - `f8469a9` Rewrite absolute framework deps for any Versions/* path
  - `4bbdc05` Apply framework absolute-path rewrite across all bundled Python versions
  - `4486696` Rewrite absolute python.org framework deps to bundle-local loader paths
  - `0dadce1` Harden mac runtime relocation for framework dylibs and version pruning
  - `4c9a938` Fix UI idle auto-shutdown UnboundLocalError
  - `03ec390` Fix framework python binary selection for versioned installs
  - `9c31585` Make mac launcher Python-version agnostic for embedded runtime
  - `1246cfb` Fail-fast and verify Homebrew dylib rewrite in mac runtime

## Current Active Workstream
- Focus shifted from packaging-only stabilization to AI Search correctness.
- A deterministic hard-filter foundation has now been started for AI Search in:
  - `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
  - `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
- Purpose:
  - stop the LLM from deciding structured constraints like age
  - compute age from DOB at evaluation time
  - gate candidates before LLM reasoning with deterministic outcomes
- Implemented so far:
  - prompt age-range extraction into a minimal `JobConstraints` shape
  - candidate DOB extraction into a minimal `CandidateFacts` shape
  - deterministic age decision with `PASS`, `FAIL`, `UNKNOWN`
  - `FAIL` candidates are excluded before LLM reasoning
  - `UNKNOWN` candidates are surfaced in UI as `Needs Review`
  - AI Search UI now shows:
    - `Scanned`
    - `Passed Hard Filters`
    - `Needs Review`
    - `Matched`
- Important note:
  - these changes required packaged-app rebuild/reinstall to validate
  - packaged mac app validation has now passed for the tested age-range flow
  - root cause of the final age bug was DOB parsing, not the deterministic gate itself
  - resume DOB format `DD-Mon-YYYY` (for example `03-Feb-1974`) is now parsed correctly
  - structured-only age prompts now full-scan the selected rank folder instead of relying on vector retrieval first
  - age ranges like `30 and 50` are no longer incorrectly split as compound `AND` queries

### Verified AI Search age test case
- Test rank folder:
  - `/Users/kartikraghavan/temp12/2nd_Engineer`
- Corpus size during validation:
  - `5` resumes
- Verified DOB/age extraction after fix:
  - `2nd_Engineer_120969.pdf` -> `1971-01-04` -> `55`
  - `2nd_Engineer_17698.pdf` -> `1974-02-03` -> `52`
  - `2nd_Engineer_288.pdf` -> `1965-12-24` -> `60`
  - `2nd_Engineer_315781.pdf` -> `1995-02-26` -> `31`
  - `2nd_Engineer_349740.pdf` -> `1989-11-04` -> `36`
- Expected deterministic pass set for prompt:
  - `should be within the ages of 30 and 50 years old`
- Matching resumes:
  - `2nd_Engineer_315781.pdf`
  - `2nd_Engineer_349740.pdf`
- User confirmed the installed app now works for this case.

## Local Working Tree Notes
- At time of handoff, local modifications existed in:
  - `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
  - `/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md`
  - `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/packaging/macos/build_app_bundle.sh`
- There is also an untracked `release/` directory in the repo.
- `docs/CONTEXT_HANDOFF_2026-03-25.md` itself may still be untracked locally.
- Before doing new work, check `git status` and decide whether to commit, stash, or work around local changes.

## High-Value Repo Areas
- Backend:
  - `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
  - `/Users/kartikraghavan/Tools/NjordHR/repositories/`
- AI search:
  - `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- Local agent:
  - `/Users/kartikraghavan/Tools/NjordHR/agent/`
- Frontend:
  - `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
- Windows packaging and launch:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/windows/`
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/packaging/windows/`
- macOS packaging and launch:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/packaging/macos/`
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/start_njordhr.sh`

## Important Product/Architecture Decisions Already Discussed
- Shared cloud state matters more than shared local download folders.
- Current hybrid architecture is acceptable if one machine is primarily used for operations and admin supervision.
- Longer term, a more cloud-heavy architecture is possible and likely cleaner.
- Hardest part of a full cloud migration is not semantic search; it is the scraping/download workflow and any browser/session/OTP dependencies.
- If moving more fully online, AI search, embeddings, verified resumes, dashboard, auth, and exports are good candidates to centralize first.

## Known Issues and Open Risks
- macOS support boundary should be formalized and enforced.
- Windows still needs a fresh smoke run after the mac packaging work.
- The prior age bug showed that structured constraints cannot be left to LLM reasoning.
- Current deterministic filter scope now includes age, ship type, and other validated structured fields already implemented in the Phase 1 hard-filter path; the remaining caution is not missing migration, but avoiding broadening beyond the validated set without new evidence.
- DOB parsing is still format-driven; additional real-world DOB formats should be captured with regression tests before expanding structured filters.
- v3.4 Phase 1 follow-up: synchronous re-extraction controls still need explicit verification or implementation.
  Required controls per spec:
  - 24-hour cooldown per candidate
  - per-search cap of 5 re-extractions
  - 10-second timeout
  - failure fallback to v1.1 path
  - concurrency guard for same-candidate re-extraction
- v3.4 low-priority follow-up: add an age boundary regression test for `_calculate_age()` on the exact birthday boundary.
- v3.4 extraction backfill follow-up:
  apply the new diagnostic-first extraction workflow to these existing corpus-sensitive areas:
  - COC extraction
  - visa extraction
  - DOB/age extraction
  - rank extraction
- Settings flow bug was reported:
  - `Save & Apply` can reset the system and navigate to runtime flags page.
- Windows UX debt remains:
  - first-run dependency bootstrap currently exposes terminal behavior
  - needs background bootstrap + progress UI
- Installer/signing pipeline is not fully productionized:
  - Apple signing/notarization
  - Windows Authenticode signing
  - update manifest and rollback flow

## 2026-04-07 Session Addendum

### Scope of work completed in this session
- Stabilized cloud-mode AI Search startup and admin/runtime behavior:
  - added terminal helper scripts:
    - `/Users/kartikraghavan/Tools/NjordHR/scripts/run_agent.sh`
    - `/Users/kartikraghavan/Tools/NjordHR/scripts/run_backend.sh`
  - aligned backend startup with saved Settings data so Supabase URL/secret/runtime flags can be reused from config instead of being retyped every run
  - expanded Settings handling and related UI/backend wiring for Supabase config persistence
- Fixed cloud AI Search correctness/runtime bugs:
  - dual-write audit adapter crash fixed in `/Users/kartikraghavan/Tools/NjordHR/repositories/dual_write_candidate_event_repo.py`
  - Supabase AI file registry upsert conflict fixed in `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
  - Supabase AI-store request timeouts increased for cloud-mode stability
  - timezone-aware UTC timestamps replaced prior `datetime.utcnow()` usage in touched files
- Implemented Phase 1 synchronous re-extraction controls in `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`:
  - cooldown
  - per-search cap
  - timeout
  - failure fallback
  - concurrency guard
  - backend partial-evaluation payload surfacing
- Surfaced partial-evaluation notice in AI Search UI in `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
- Fixed v3.4 hard-filter activation bug:
  - age and visa now respect `applied_constraints`
  - regression coverage added
- Updated Downloads tab and PDF preview/open behavior:
  - cloud-safe preview route added in `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
  - Download tab preview and AI Search result resume links now use the preview route
  - removed unneeded Downloads intro copy
  - Downloads tab layout reorganized per user request
- Added collapsible `Needs Review` UI section in `/Users/kartikraghavan/Tools/NjordHR/frontend.html`

### STCW extraction diagnostic/tuning work completed
- Added and used a diagnostic-first workflow for extraction tuning.
- Saved folder-level diagnostics under `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/`.
- Improved STCW extraction in `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`:
  - dense all-four STCW-basic certificate-list resumes no longer stay `UNKNOWN`
  - date association for STCW expiry was tightened so generic nearby dates from endorsement tables do not drive false `expired`
- Added/updated STCW regression tests in:
  - `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_certifications.py`
- Cross-folder STCW diagnostics were run for:
  - `Master`
  - `Chief_Officer`
  - `2nd_Officer`
  - `Junior_4th_Engineer`
- Current STCW quality decision:
  - do not add a broader `UNKNOWN -> PASS` heuristic yet
  - remaining `UNKNOWN` cases are narrower and more judgment-based
  - false `expired` risk was prioritized and addressed before any further pass-promotion tuning

### AI Search performance/indexing work completed
- Instrumented search runtime in `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py` with `[PERF] ...` logs.
- Reduced avoidable runtime costs:
  - removed repeated `O(n^2)` folder rescanning for candidate path lookup
  - narrowed fixed LLM pacing from `2.5s` to `0.5s`
  - moved candidate path index build to metadata-driven per-search construction
- Added local ingest cache use in cloud mode via:
  - `/Users/kartikraghavan/Tools/NjordHR/logs/registry.db`
- Investigated repeated re-indexing and found the true trigger:
  - file cache was up to date
  - forced re-index was coming from `vector index is empty` logic, not file mtimes
- Resolved the intermittent Pinecone false-empty reindex issue in `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`:
  - captured failing rerun evidence and confirmed reads and writes were targeting `elegant-dogwood-v2-d3072`
  - ruled out namespace mismatch and wrong write-index resolution
  - identified the root cause as the empty-namespace probe, not the registry cache or write target
  - replaced the old zero-vector cosine query probe with `index.list(namespace=..., limit=1)` existence checks
  - retained bounded retry on empty namespace detection for brief visibility lag
  - kept query fallback only for Pinecone client/runtime cases where `list()` is unavailable
- Final state:
  - `Master` reruns now detect existing vectors in `elegant-dogwood-v2-d3072`
  - the app reports `Index is up to date.` instead of forcing a full reindex when the namespace already contains vectors
  - temporary Pinecone debug logging used during the investigation has been removed after validation

### UI/result-quality observations from manual review
- Verified Matches bucket for `Master` + prompt `with valid STCW basic safety` looks credible on sampled resumes.
- `Needs Review` bucket is mixed:
  - some are appropriately ambiguous
  - some likely remain false `UNKNOWN` cases
- Strong suspicious false-`UNKNOWN` examples from manual review:
  - `Ausaf`
  - `Sudhakar`
- Current product/engineering decision:
  - keep the `Needs Review` section
  - do not add a `Failed Hard Filters` section
  - do not broaden the STCW pass heuristic yet without more repeated cross-folder evidence

### Files materially touched during this session
- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
- `/Users/kartikraghavan/Tools/NjordHR/csv_manager.py`
- `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
- `/Users/kartikraghavan/Tools/NjordHR/repositories/dual_write_candidate_event_repo.py`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/run_agent.sh`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/run_backend.sh`
- `/Users/kartikraghavan/Tools/NjordHR/scripts/stcw_diagnostic_report.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_certifications.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_hard_filter_rules.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_job_constraints.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_pinecone.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_age_filters.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_dual_write_repo.py`

### Immediate next step for the next agent
1. Leave the Pinecone indexing fix alone unless a new concrete regression appears.
2. Treat the current extraction round as stabilized unless a new repeated corpus pattern appears.
3. If new extraction work is needed, start again with folder-level diagnostics and keep Phase 1 changes narrow and traceable.

### Explicit non-goals for the next agent
- Do not broaden STCW `UNKNOWN -> PASS` heuristics yet.
- Do not add a Failed Hard Filters UI section.
- Do not remove the Needs Review section.
- Do not reopen Pinecone indexing changes without fresh failing evidence.

## 2026-04-08 Session Addendum

### Extraction-quality validation completed
- The same diagnostic-first workflow was run beyond STCW for the currently implemented structured-extraction areas:
  - rank normalization
  - COC extraction
  - DOB / age extraction
  - visa / passport extraction
- Diagnostic artifacts were saved under:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/`
- A broader cross-folder rollup was produced in:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/extractor_sweep_rollup_current.json`

### Rank extraction status
- Rank extraction was tuned using folder-level diagnostics first, then broader validation.
- Narrow parser additions were made for repeated inline availability / applied-rank text patterns and related normalization gaps.
- Current judgment:
  - rank extraction is materially improved across the validated folders
  - remaining misses are small and should not trigger broader heuristic expansion without new repeated evidence

### COC extraction status
- COC extraction was tuned using repeated certificate-table row patterns observed in diagnostics.
- Narrow alias support was added for repeated deck and engineer competency row families, then revalidated across additional folders.
- Broader COC validation is now materially improved across the validated deck and engineer folders.
- Remaining `Chief_Engineer` misses were reviewed manually and are mostly incomplete rows or missing-date cases rather than a clean repeated parser gap.
- Current judgment:
  - keep the current COC extractor as-is for now
  - do not broaden COC parsing further unless a new repeated missed-positive row shape appears across multiple folders

### DOB / age and visa / passport status
- A full extraction sweep indicates DOB / age extraction is broadly stable in the current corpus.
- Visa extraction also appears broadly stable in the current corpus, with only a small residual number of passport-expiry misses.
- Current judgment:
  - no immediate extractor changes are recommended here
  - rerun diagnostics before making further changes

### STCW status after broader review
- STCW remains the largest conservative-unknown bucket.
- The current recommendation is unchanged:
  - do not broadly promote `UNKNOWN` to `PASS`
  - prefer fixing false `FAIL` / false `expired` outcomes first if fresh repeated evidence appears

### Current recommended posture
- No more extractor broadening should be done by default.
- The current extraction work should be treated as stable for this corpus family unless:
  - a new repeated false-negative pattern appears in diagnostics, or
  - a product decision is made to trade more recall for more aggressive parsing
- If new work starts, use the same sequence:
  1. folder-level diagnostic
  2. narrow parser change
  3. regression tests
  4. rerun same-folder diagnostic
  5. broader validation pass

## Pending UX / Product Tasks Already Noted
- Ensure Njord logo is consistently shown in header.
- Add password generator in User Password page.
- Add branded Windows icon parity with macOS.
- Hide Windows first-run terminal/bootstrap and replace with progress UI.
- Fix settings save/apply reset navigation issue.

## Backlog Summary
- Strategic backlog is in:
  - `/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md`
- Main module lanes:
  - `M1` Cloud foundation
  - `M2` Data migration to Supabase
  - `M3` Local agent
  - `M4` Frontend integration
  - `M5` Installer, signing, update
  - `M6` Cutover and hardening

## Recommended Immediate Next Tasks
1. Commit the AI Search deterministic-filter work and UI summary changes in a clean commit.
2. Add regression tests for DOB parsing and age-filter evaluation using real observed resume formats.
3. Extend the deterministic filter foundation to the next structured field after age:
   - likely `ship type`
4. Add explicit macOS minimum-version guard (`11+`) in installer and launcher.
5. Run a fresh Windows smoke regression:
   - build installer
   - install on clean machine
   - launch from Start menu
   - verify cloud auth and runtime startup
6. Fix the Settings `Save & Apply` reset/navigation bug.
7. Implement Windows first-run background bootstrap UX.

## Handover Prompt For Next Agent

Use this prompt verbatim or adapt minimally:

```text
You are taking over work in `/Users/kartikraghavan/Tools/NjordHR`.

Before changing any v3.4 AI Search behavior, read:
- `/Users/kartikraghavan/Tools/NjordHR/IMPLEMENTATION_RISKS_V3_4.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/prompt-corpus-and-feedback-spec-v0.3.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/v3.4-implementation-discipline-and-feedback-loop.md`
- `/Users/kartikraghavan/Tools/NjordHR/AGENTS.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/CONTEXT_HANDOFF_2026-03-25.md`

Important current context:
- Cloud-mode AI Search startup/config, dual-write audit, Supabase upsert, timeout, and UTC timestamp issues were fixed.
- Synchronous re-extraction controls and partial-evaluation notice were added.
- Downloads tab preview and AI Search result PDF opening were fixed with the cloud-safe preview route.
- STCW extraction was improved through a diagnostic-first workflow.
- Broad all-four-alias false `UNKNOWN` cases were fixed.
- False STCW expiry associations from nearby generic dates were tightened.
- Cross-folder diagnostics suggest the remaining STCW `UNKNOWN` cases should not yet be broadly promoted to `PASS`.
- `Needs Review` stays; `Failed Hard Filters` should not be added.
- AI Search indexing/file-cache behavior in cloud mode was improved using `/Users/kartikraghavan/Tools/NjordHR/logs/registry.db`.
- The intermittent Pinecone false-empty reindex path was resolved.
- Root cause: the empty-namespace probe was unreliable; the old zero-vector cosine query failed to detect existing vectors in `elegant-dogwood-v2-d3072`.
- Final fix: use Pinecone `list(namespace=..., limit=1)` for namespace existence checks, keep a short retry loop, and fall back to query probing only if `list()` is unavailable.

Immediate next step:
1. Apply the same diagnostic-first workflow to other implemented extraction areas that still need corpus-backed validation.
2. Keep Pinecone changes closed unless a new failing rerun provides fresh evidence.

Guardrails:
- Do not broaden STCW `UNKNOWN -> PASS` heuristics yet.
- Do not remove `Needs Review`.
- Do not add a `Failed Hard Filters` section.
- Keep changes narrow and traceable.

Helpful files:
- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
- `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_certifications.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_pinecone.py`
- `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/stcw_master_diagnostic_current.json`
```

## Useful Commands

### Check repo state
```bash
cd /Users/kartikraghavan/Tools/NjordHR
git status --short
git log --oneline -n 12
```

### Build mac app and pkg
```bash
cd /Users/kartikraghavan/Tools/NjordHR
rm -rf build/macos/NjordHR.app build/macos/pkgroot build/macos/component.plist
rm -f build/macos/NjordHR-unsigned.pkg
rm -f build/macos/NjordHR-*-unsigned.pkg 2>/dev/null || true

export NJORDHR_EMBED_RUNTIME=true
export NJORDHR_BUILD_PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"

/opt/homebrew/bin/bash ./scripts/packaging/macos/build_app_bundle.sh
/opt/homebrew/bin/bash ./scripts/packaging/macos/build_pkg.sh
```

### Validate packaged mac runtime has no host references
```bash
cd /Users/kartikraghavan/Tools/NjordHR
PKG="$(ls -1t build/macos/NjordHR-*-unsigned.pkg | head -n1)"
rm -rf /tmp/njordhr_pkg_check
pkgutil --expand-full "$PKG" /tmp/njordhr_pkg_check
find /tmp/njordhr_pkg_check/Payload/Applications/NjordHR.app/Contents/Resources/runtime -type f \( -perm -111 -o -name "*.so" -o -name "*.dylib" \) | while read -r f; do
  otool -L "$f" 2>/dev/null | awk '{print $1}' | grep -qE '^((/opt/homebrew|/usr/local)/(opt|Cellar)/|/Library/Frameworks/Python\.framework/)' && echo "$f"
done
```

### Install mac pkg locally
```bash
cd /Users/kartikraghavan/Tools/NjordHR
PKG="$(ls -1t build/macos/NjordHR-*-unsigned.pkg | head -n1)"
sudo rm -rf /Applications/NjordHR.app
sudo installer -pkg "$PKG" -target /
open /Applications/NjordHR.app
curl -s http://127.0.0.1:5050/auth/bootstrap_status
```

### Upload installer to GitHub release
```bash
cd /Users/kartikraghavan/Tools/NjordHR
PKG="$(ls -1t build/macos/NjordHR-*-unsigned.pkg | head -n1)"
VER="$(basename "$PKG" | sed -E 's/^NjordHR-([0-9.]+)-unsigned\.pkg$/\1/')"
gh release view "$VER" >/dev/null 2>&1 || gh release create "$VER" -t "NjordHR $VER" -n "macOS installer"
gh release upload "$VER" "$PKG" --clobber
shasum -a 256 "$PKG"
```

### Runtime sanity checks
```bash
curl -s http://127.0.0.1:5050/auth/bootstrap_status
curl -s http://127.0.0.1:5050/config/runtime
```

## Suggested Prompt For A Fresh Context Window
- Repo: `/Users/kartikraghavan/Tools/NjordHR`
- Repo remote: `https://github.com/kartikhth-byte/NjordHR.git`
- Start by reading:
  - `/Users/kartikraghavan/Tools/NjordHR/docs/CONTEXT_HANDOFF_2026-03-25.md`
  - `/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md`
  - `git status --short`
- Current focus:
  - preserve recent mac packaging fixes
  - preserve the validated deterministic AI Search hard-filter work
  - do not regress Windows startup/install path
  - packaged validation of the deterministic age gate is complete
  - deterministic age, ship-type, audit logging, and `Needs Review` routing work are now implemented in practice
  - the first retrieval chunking upgrade slice is now complete; any further AI-T6 work is optional follow-up rather than the current default next step
