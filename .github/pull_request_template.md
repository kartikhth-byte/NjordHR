## Summary

- What changed:
- Why:
- Related spec:
- Feature flags touched:

## Scope Check

- [ ] This PR is within the active implementation phase
- [ ] Candidate-intelligence Phase 2/3 items were not pulled into a Phase 1 change set
- [ ] Existing search behavior was not changed outside the stated scope
- [ ] Production routing was not changed outside the stated scope
- [ ] No direct CSV/SQLite access was added where a repository/service abstraction was required
- [ ] No local-folder writes were added outside the local-agent boundary
- [ ] Feature flag behavior was reviewed for the touched paths

## Structured Picker / Filter Contract

- [ ] If this PR introduces, removes, relaxes, or otherwise changes a structured-picker invariant, `docs/specs/search_pickers_v1.md` was updated in this PR
- [ ] If this PR modifies a helper with external callers in `_extract_job_constraints`, `/analyze_stream`, or `/analyze`, the caller grep is listed and an adjacent integration-path test observes the helper output through a consuming path
- [ ] Picker-vs-prompt arbitration behavior was reviewed for touched families
- [ ] Recruiter/operator-visible output was checked for canonical ID leaks
- [ ] Audit/export/telemetry visibility boundaries were reviewed for touched fields

## Feature Flags

- [ ] `USE_SUPABASE_DB` reviewed
- [ ] `USE_LOCAL_AGENT` reviewed
- [ ] `USE_CLOUD_EXPORT` reviewed
- [ ] Flag defaults documented in the relevant spec
- [ ] Rollout / rollback behavior reviewed

## Required Checks

- [ ] `python3 -m unittest` targeted tests listed below
- [ ] If evaluation / filter path changed: reran validated baseline corpus
- [ ] If a new extractor or adapter was added: dedicated test coverage added
- [ ] If a new rule was activated: `applied_constraints` activation behavior verified
- [ ] If dual-version / replay path was touched: fallback and pinned replay behavior verified
- [ ] If candidate-facts flow changed: replay audit metadata verified

## Targeted Tests

- List the exact commands you ran here.
- Include any focused unit tests, corpus checks, or smoke checks relevant to the PR.
- If no tests were needed, explain why in the PR description.

## Validated Baseline

- Folder: `/Users/kartikraghavan/temp12/2nd_Engineer`
- Prompt: `should be within the ages of 30 and 50 years old`
- Expected pass set:
  - `2nd_Engineer_315781.pdf`
  - `2nd_Engineer_349740.pdf`

- [ ] Baseline matched
- [ ] Baseline not applicable because this PR does not touch evaluation path

## Risks

- [ ] No hidden coupling to production search routing
- [ ] If a replay or persistence helper changed, identity selection was verified
- [ ] If a schema changed, validation and migration impact were reviewed
- [ ] Rollback steps reviewed

## Review Artifacts

- [ ] Review pack regenerated if query-understanding or candidate-facts review artifacts changed
- [ ] Shadow-audit artifact regenerated if relevant
- [ ] Output JSON inspected for stale placeholders or demo data

## Notes

- Spec reference: [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md)
- Hybrid spec: [NjordHR_Online_Hybrid_Architecture_Spec.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Online_Hybrid_Architecture_Spec.md)
- Backlog: [NjordHR_Implementation_Modules_and_Task_Backlog.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md)
- Prep checklist: [ai-search-v3.4-phase1-prep-checklist.md](/Users/kartikraghavan/Tools/NjordHR/docs/ai-search-v3.4-phase1-prep-checklist.md)
- M0 smoke checklist: [NjordHR_M0_Test_Matrix_and_Smoke_Checklist.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_M0_Test_Matrix_and_Smoke_Checklist.md)
- M0 rollback playbook: [NjordHR_Module_Rollback_Playbook.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Module_Rollback_Playbook.md)
