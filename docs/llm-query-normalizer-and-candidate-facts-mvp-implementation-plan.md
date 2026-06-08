# NjordHR LLM Query Normalizer and Candidate Facts MVP Implementation Plan

This plan follows [llm-query-normalizer-and-candidate-facts-spec-v0.1.md](/Users/kartikraghavan/Tools/NjordHR/docs/llm-query-normalizer-and-candidate-facts-spec-v0.1.md) and turns the updated MVP constraints into an execution sequence.

## 1. Goal

Deliver a portable MVP that:

- keeps the current deterministic search behavior intact during shadow validation
- produces validated `query_plan.v1` output for prompt normalization
- extracts candidate facts from PDFs into structured JSON
- stores extracted candidate facts in Supabase for user-testing and later phases
- stays OS-portable from the start
- validates on macOS first
- includes a minimal Windows runtime smoke gate before authoritative user-test data is trusted
- defers full Windows packaging/install validation until after macOS MVP validation with users

## 2. Milestones

### Milestone A: Query-normalizer shadow MVP

Purpose:
- validate the query-plan schema and legacy adapter
- keep the LLM path in shadow mode only
- compare legacy output against shadow output

Exit criteria:
- `query_plan.v1` validation passes for the supported prompt corpus
- legacy parser output can be wrapped into the query-plan envelope
- shadow LLM output can be generated and compared without changing production search decisions
- unsupported/unapplied constraints are logged clearly

### Milestone B: Candidate-facts extraction MVP

Purpose:
- produce structured candidate facts from PDFs in a portable way
- validate extracted JSON shape locally before treating it as authoritative
- prepare the data model for Supabase persistence

Exit criteria:
- at least the supported extraction path(s) emit valid `candidate_facts.v1`
- local validation cache is used before Supabase persistence is considered authoritative
- extraction output does not depend on macOS-only paths or shell assumptions
- existing SeaJobs compatibility remains intact

### Milestone C: Supabase persistence for user-testing

Purpose:
- persist validated extracted candidate-facts JSON in Supabase
- keep raw-text fallback available until extraction coverage is proven

Exit criteria:
- candidate-facts rows can be stored by resume version in Supabase/Postgres
- persistence metadata includes parser version, schema version, content hash, extraction status, and timestamps
- replay / current-row selection rules remain deterministic
- user-testing data is traceable to the underlying resume version
- Supabase governance is implemented and enforced before real candidate data is written:
  - tenant/account scoping is enforced
  - read access is role-scoped
  - service-role writes are the only write path for persisted facts
  - deletion/anonymization behavior is defined
  - full raw PDF text is not stored in `facts_json`

### Milestone D: Mac internal validation

Purpose:
- validate the MVP on macOS first

Exit criteria:
- macOS smoke tests pass for the MVP flow
- prompt normalization output is reviewable and stable
- no macOS-specific runtime assumptions leak into shared code

### Milestone E: Minimal Windows runtime smoke gate

Purpose:
- confirm the shared extraction/cache/persistence code behaves portably enough before user-test data becomes authoritative
- catch path, permission, temp-dir, and process behavior differences early

Exit criteria:
- extraction, cache, and persistence behavior are exercised on a real Windows runner or machine
- no macOS-only filesystem or shell assumptions remain in the shared code path
- the runtime smoke gate is thin and does not require full Windows packaging

### Milestone F: User testing with Supabase facts

Purpose:
- use real user testing to confirm the output quality once portability has been checked

Exit criteria:
- user-testing data is written only after the Windows runtime smoke gate has passed
- candidate facts are usable in the intended user-testing flow
- extracted facts remain traceable to the underlying resume version

### Milestone G: Full Windows packaging / installer validation

Purpose:
- defer installer and distribution hardening until the MVP behavior is proven

Exit criteria:
- build/package validation on Windows is done only after macOS MVP validation with users
- installer/distribution smoke tests pass
- signing / release-hardening work can start from a stable product behavior baseline

## 3. Workstreams

### 3.1 Query understanding

Files of interest:
- [query_understanding/schema.py](/Users/kartikraghavan/Tools/NjordHR/query_understanding/schema.py)
- [query_understanding/legacy_parser_adapter.py](/Users/kartikraghavan/Tools/NjordHR/query_understanding/legacy_parser_adapter.py)
- [query_understanding/normalizer_compare.py](/Users/kartikraghavan/Tools/NjordHR/query_understanding/normalizer_compare.py)
- [query_understanding/shadow_audit.py](/Users/kartikraghavan/Tools/NjordHR/query_understanding/shadow_audit.py)
- [query_understanding/shadow_llm_provider.py](/Users/kartikraghavan/Tools/NjordHR/query_understanding/shadow_llm_provider.py)
- [query_understanding/llm_normalizer.py](/Users/kartikraghavan/Tools/NjordHR/query_understanding/llm_normalizer.py)

Implementation notes:
- keep shadow mode disabled by default
- preserve the legacy parser as the production source of truth during MVP validation
- log unsupported and unapplied constraints for later catalogue review
- avoid introducing new production routing until shadow comparisons are stable

### 3.2 Candidate-facts extraction and persistence

Files of interest:
- [candidate_facts/schema.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/schema.py)
- [candidate_facts/orchestrator.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/orchestrator.py)
- [candidate_facts/storage.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/storage.py)
- [candidate_facts/persistence.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/persistence.py)
- [candidate_facts/repository.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/repository.py)
- [candidate_facts/extractors/seajobs.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/extractors/seajobs.py)
- [candidate_facts/extractors/generic_pdf.py](/Users/kartikraghavan/Tools/NjordHR/candidate_facts/extractors/generic_pdf.py)

Implementation notes:
- keep a local validation cache before Supabase becomes authoritative
- preserve the SeaJobs bridge while introducing portable candidate-facts extraction paths
- treat generic PDF extraction as a first-class MVP target, not just a stub
- do not store full raw PDF text in `facts_json`
- keep the candidate-facts JSON shape stable enough for user testing before broadening source coverage

### 3.3 Search/runtime integration

Files of interest:
- [ai_analyzer.py](/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py)
- [backend_server.py](/Users/kartikraghavan/Tools/NjordHR/backend_server.py)
- [frontend.html](/Users/kartikraghavan/Tools/NjordHR/frontend.html)

Implementation notes:
- avoid expanding `ai_analyzer.py` with new schema ownership
- keep it as an orchestration/compatibility layer while the shared modules mature
- do not change production search decisions until the shadow path and data model are validated

## 4. OS portability rules

Required for all new shared code:

- use `pathlib` or `os.path` for file paths
- use portable temp/runtime directories
- avoid hardcoded macOS-only paths
- avoid shell-only runtime dependencies
- keep fixtures and tests portable
- make Windows runtime smoke possible without a full Windows installer build

## 5. Validation plan

### macOS validation

- run unit and smoke tests for query normalization
- run unit and smoke tests for candidate-facts extraction
- verify extracted JSON can be stored and replayed locally
- confirm the shared code paths work in the development environment

### Supabase validation

- persist extracted candidate-facts JSON after local shape validation
- verify row identity, current-row selection, and replay behavior
- confirm the stored facts are sufficient for user-testing workflows

### Windows runtime smoke gate

- run the extraction path on a real Windows runner or machine
- verify no macOS-only assumptions exist in the shared modules
- confirm the runtime path can process candidate facts without packaging the app
- Windows-style path and temp-location tests remain part of earlier unit coverage, not a substitute for the real Windows gate

### Full Windows packaging validation

- defer until macOS MVP validation with users is complete
- then verify installer / distribution artifacts and Windows startup behavior

## 6. Suggested implementation order

1. Keep query-normalizer shadow mode and comparison harness stable.
2. Finish the portable candidate-facts extraction path.
3. Add/confirm Supabase persistence for extracted facts.
4. Validate on macOS with internal testing.
5. Run the minimal Windows runtime smoke gate on a real Windows runner or machine before authoritative user-test data is trusted.
6. Only then begin user testing with Supabase facts.
7. Only then begin full Windows packaging / installer work.

## 7. Acceptance bar

The MVP is ready for user testing when:

- the shadow query-normalizer path validates cleanly
- candidate facts are extracted into stable JSON
- facts can be persisted in Supabase
- the code remains OS-portable
- macOS validation passes
- the minimal Windows runtime smoke gate has passed on a real Windows runner or machine before broad user testing or authoritative Supabase facts
- Supabase governance has been implemented and enforced in the user-testing environment before real candidate data is written
