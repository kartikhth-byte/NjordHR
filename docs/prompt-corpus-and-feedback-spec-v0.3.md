# NjordHR Prompt Corpus and Parser Improvement Specification
## Specification v0.3 — Approved for Implementation

---

## 1. Purpose

This document defines a **simple, achievable** prompt-corpus specification for NjordHR using the tool capabilities that already exist or are very close to existing.

The goal is:
- improve the prompt extractor layer over time
- grow a corpus of real recruiter prompts
- identify obvious parser gaps and unsupported prompt patterns

This spec is **not** a ranking-evaluation spec and **not** a full search-analytics platform spec.

---

## 2. What This Spec Is Trying To Achieve

The prompt extractor will improve only if it is designed and reviewed against real prompt data.

This spec creates a lightweight feedback loop:

1. store what users actually typed
2. store what the search engine did with those prompts
3. store limited human feedback already supported by the UI
4. review prompt patterns and failures
5. update parsing rules and tests through normal code changes

The purpose is to improve:
- constraint pattern coverage
- prompt normalization quality
- unsupported-prompt handling

This spec is also the operational source for the prompt samples required by the launch-gate language in [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md). When that architecture spec requires a 20-prompt sample for a constraint family, the sample must be drawn from this stored prompt corpus rather than assembled ad hoc. The 20-prompt threshold in this spec is the same threshold that makes the architecture spec's launch-gate measurement possible; it is not an additional separate sample requirement.

---

## 3. What This Spec Is Not Trying To Do

This spec does not require:
- autonomous self-improving production logic
- model fine-tuning in production
- full recruiter-selection tracking
- override-rate ranking analytics
- explicit product feedback flows such as "results too broad" or "missing filter"

Those may be added later, but they are not part of this minimum spec.

---

## 4. Current-Tool-Compatible Scope

The current tool can already support or nearly support the following:

- log raw search prompts
- log a search session identifier
- log candidate-level AI search audit rows
- log approve/reject feedback for uncertain matches, with optional notes
- optionally link downstream candidate statuses as coarse outcome signals

This is the scope of this spec.

### 4.1 Privacy and retention

Raw recruiter prompts may contain operationally sensitive details such as candidate names, vessel names, company references, or compensation wording. Prompt-corpus storage and review must therefore follow an explicit retention and access decision made by the product/developer owner before broad rollout. At minimum:
- access to raw prompt exports must be limited to people reviewing parser behavior
- retained prompts must not be treated as open-ended permanent data by default
- if prompt exports are shared outside the implementation/review group, sensitive fields should be redacted where practical
- the retention and access decision must be documented, even if only as a short note in the implementation commit or rollout checklist

---

## 5. Core Terms

### 5.1 Prompt corpus

A stored collection of real search prompts plus lightweight execution artifacts that help the team review how prompts are being interpreted.

### 5.2 Prompt extractor layer

The logic that reads a user prompt and extracts structured constraints, supported rule families, and unsupported or ambiguous prompt parts.

### 5.3 Coarse downstream signal

A later candidate status such as:
- `Contacted`
- `Interested`
- `Not Interested`
- `Mail Sent (handoff complete)`

These are useful operational signals, but they are **not** equivalent to "candidate selected from this search result set."

---

## 6. Data To Store

### 6.1 Minimum per-search data

For each AI search session, store:
- `search_session_id`
- `created_at`
- `raw_prompt`
- `rank_folder`

If already available from the execution path, also store:
- applied ship-type filter
- experienced ship-type filter

### 6.2 Minimum per-candidate audit data

For each candidate touched by a search session, store:
- `search_session_id`
- `candidate_id` if available
- `filename`
- `raw_prompt`
- hard-filter decision
- hard-filter reason codes
- hard-filter reason messages
- whether the LLM was reached
- result bucket

This is enough to reconstruct:
- what the prompt was
- which candidates were evaluated
- which candidates failed or passed deterministic filters
- which candidates reached LLM reasoning

### 6.3 Minimum feedback data

For uncertain matches where the current UI already allows review, store:
- `filename`
- `query`
- LLM decision
- LLM reason
- LLM confidence
- user decision (`approve` / `reject`)
- user notes
- timestamp

This feedback path is intentionally narrow: it only captures feedback on uncertain matches that the current UI exposes for review. It does **not** capture confident-but-wrong decisions. To partially cover that gap, the review loop must also inspect:
- FAIL-rate spikes by constraint family
- sudden growth in `unapplied_constraints`
- prompt patterns that produce unexpectedly low match counts after a parser change

### 6.4 Optional coarse downstream linkage

Where practical, link later candidate statuses to the same candidate:
- `Contacted`
- `Interested`
- `Not Interested`
- `Mail Sent (handoff complete)`

This linkage is optional in this spec and should be treated as a coarse signal only.

Permitted uses of these coarse downstream signals:
- count how often searched candidates later move into each status bucket
- identify prompt families that rarely lead to any downstream action
- provide rough operational context during prompt review

Not permitted:
- treat `Contacted`, `Interested`, or `Mail Sent (handoff complete)` as true ranking-quality labels
- compute recruiter override rate from these statuses
- claim a candidate was the recruiter-selected result for a search unless explicit search-to-selection linkage exists

---

## 7. Storage Model

This spec does not require a new complex schema.

The current implementation paths are acceptable:
- AI search audit rows in CSV
- feedback rows in local SQLite or Supabase-backed feedback storage
- candidate event/status history in CSV or Supabase candidate-event storage

The storage requirement is functional, not relational:
- the data must be persistable
- the data must be reviewable later
- the data must be exportable for prompt review

Minimum tooling requirement:
- the stored format must support grouping prompts by intent family and summarizing frequent unsupported patterns without manual spreadsheet-only cleanup

Acceptable first implementations:
- a Python review script that reads the CSV/SQLite exports and produces grouped summaries
- a SQL view or Supabase query that groups prompts by parser output or intent family

Manual one-off transformation in spreadsheets is acceptable for inspection, but it must not be the only review path.

---

## 8. Review Workflow

Prompt review must be operationally owned. The default owner is the developer responsible for AI Search parser changes, with input from a recruiter or operator who uses search in production.

Default cadence:
- review once per week while Phase 1 and Phase 2 parser coverage is still being expanded
- once coverage expansion stabilizes, review at least once per month in steady state, with off-cycle review when a trigger fires

Off-cycle review triggers:
- visible spike in `unapplied_constraints`
- visible spike in uncertain matches
- visible spike in FAIL-rate for a supported constraint family after a release
- recruiter report that a common prompt form is being missed

### 8.1 Review steps

1. export recent prompts and audit rows
2. group prompts by intent family
3. identify:
   - frequently unsupported prompts
   - ambiguous phrasing
   - common phrasing variants
   - prompts that often lead to uncertain matches
   - uncertain matches that humans repeatedly approve
   - uncertain matches that humans repeatedly reject
4. decide whether the issue is:
   - parsing rule gap
   - normalization gap
   - unsupported constraint family
   - extraction quality problem
   - prompt wording the system should explicitly mark unsupported
5. update:
   - parser rules
   - normalization tables
   - prompt-parser tests
   - user-facing unsupported-constraint handling if needed

### 8.2 Phase A to Phase B transition gate

Phase B review should begin once the corpus contains enough prompt variety to be useful. Minimum gate:
- at least 20 stored prompts for each already-live deterministic family under active Phase 1 use:
  - age range
  - US visa
- at least 20 stored prompts for each newly introduced deterministic family before launch-gate review:
  - rank
  - COC
  - STCW basic

These stored prompts are the source for the architecture spec's launch-gate sample for that family. No separate second 20-prompt sample is required.

If a family has not yet reached the 20-prompt threshold, parser changes for that family may still proceed only for narrowly scoped bug fixes that correct a confirmed misparse on a specific observed prompt. Broader pattern expansion or new coverage claims for that family must not be made from thinner data, and launch-gate coverage claims must not be made until the threshold is met.

### 8.3 Expected outputs from review

Examples:
- add support for a common age-range phrasing
- add a missing rank alias
- add a missing COC phrasing pattern
- move a commonly misread prompt into explicit unsupported handling
- add tests for frequent real-world prompt variants

---

## 9. What Success Looks Like

This spec is successful if it helps the team answer questions like:
- what prompt forms do recruiters actually use?
- which prompt patterns are common but unsupported?
- which prompt patterns are being misparsed?
- which uncertain-match patterns are repeatedly corrected by humans?
- which constraint families need better prompt coverage first?

This spec is **not** trying to answer:
- did the recruiter prefer rank 3 over rank 1 in the result list?
- did the scoring model produce the optimal ordering?
- did the UI capture all recruiter intent explicitly?

---

## 10. Metrics

Keep the metrics simple and achievable.

Recommended review metrics:
- prompt frequency by intent family
- unsupported prompt frequency
- ambiguous prompt frequency
- uncertain-match frequency
- approve rate on uncertain matches
- reject rate on uncertain matches
- FAIL-rate by supported constraint family
- `unapplied_constraints` frequency by family
- coarse downstream counts by candidate status, where linked

These metrics are enough for parser improvement and obvious-gap detection.

---

## 11. Guardrails

The following must remain true:

- production parser behavior must remain code-defined and reviewable
- stored prompts must not directly rewrite parser rules in production
- human review is required before changing parsing behavior
- prompt logging should not break search if logging fails
- prompt logging failures must be observable in logs or monitoring so corpus gaps are diagnosable
- downstream status signals must not be over-interpreted as true ranking or selection outcomes

---

## 12. Rollout Recommendation

### Phase A — Logging and review only

Implement or confirm:
- prompt logging
- search session ID logging
- candidate-level audit logging
- uncertain-match feedback logging

No behavior changes are required for this phase.

### Phase B — Human review loop

Run periodic reviews of the stored prompts and feedback.

Use findings to:
- expand supported prompt patterns
- improve normalization
- add regression tests
- make unsupported prompt handling more explicit

### Phase C — Optional later expansion

Only after Phase A and B are stable, consider:
- richer structured search records
- explicit UI feedback prompts
- stronger search-to-outcome linkage

These are future enhancements, not part of this minimum spec.

---

## 13. Bottom Line

Yes, the current tool can support a useful prompt-corpus spec, as long as the goal stays narrow:
- parser improvement
- prompt corpus growth
- identifying obvious gaps

That is enough to justify prompt logging and human review now.

---

*Specification v0.3 — Prompt Corpus and Parser Improvement Specification*
*Status: Approved for implementation*
