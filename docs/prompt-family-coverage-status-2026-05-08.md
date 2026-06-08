# NjordHR Prompt Family Coverage Status
## Status Note — 2026-05-08

## 1. Purpose

This note records the current parser-coverage state after the first shift away from one-off prompt fixes and toward bounded Phase 1 prompt families.

It is a checkpoint note, not a replacement for:
- [prompt-family-coverage-plan-v0.1.md](/Users/kartikraghavan/Tools/NjordHR/docs/prompt-family-coverage-plan-v0.1.md)
- [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md)
- [prompt-corpus-and-feedback-spec-v0.3.md](/Users/kartikraghavan/Tools/NjordHR/docs/prompt-corpus-and-feedback-spec-v0.3.md)

## 2. Family-Level Coverage Now In Place

### 2.1 Age

Current state:
- promoted to bounded family-level support

Representative supported phrasings now include:
- `below the age of 50`
- `below age 50`
- `less than 50 years old`
- `maximum age should be 50`
- `not more than 50 years old`
- `above the age of 25`
- `over 25`
- `minimum age should be 25`
- `between ages 30 and 45`
- `age between 30 to 45`

Important semantics:
- exclusive language like `below`, `under`, `less than`, `over`, `above` remains exclusive
- inclusive language like `maximum age should be` and `minimum age should be` remains inclusive

### 2.2 Company Continuity

Current state:
- promoted to bounded family-level support
- still SeaJobs-only at evaluation time
- email resumes remain excluded intentionally

Representative supported phrasings now include:
- `same company for 2 contracts`
- `same company for 3 contracts`
- `same employer for 3 contracts`
- `has worked for a company for 3 contracts`
- `worked with one employer for 3 contracts`
- `more than 1 contract with same company`
- `minimum 3 contracts with one employer`
- `at least 2 contracts in same company`
- `served minimum 2 contracts with same company`
- `has worked for a company for more than 2 contracts`
- `worked under one employer for more than 2 contracts`

Normalized family shape:
- `company_continuity -> min_same_company_contract_count = N`

Important scope boundary:
- this is not yet support for vague stability language like `stable candidate`

### 2.3 Visa

Current state:
- promoted to bounded family-level support for already-supported visa groups

Representative supported phrasings now include:
- `valid US visa`
- `US visa holder`
- `must have valid US visa`
- `holding valid US visa`
- `with valid US visa`
- `valid Schengen visa`
- `Schengen visa holder`
- `holding valid Schengen visa`
- `valid Australian visa`
- `Australian visa holder`
- `with valid Australian visa`

Supported groups remain:
- `usa`
- `schengen`
- `australia`

Important scope boundary:
- unsupported groups like UK remain explicit unsupported/typed-unknown behavior

### 2.4 Rank

Current state:
- promoted to bounded family-level support within the alias-table model

Representative supported phrasings now include:
- `need chief mate`
- `looking for second engineer`
- `require bosun candidate`
- `junior engineer profile`

Important scope boundary:
- this is still explicit alias-table support
- no fuzzy semantic rank understanding has been added

### 2.5 Sea Service

Current state:
- promoted to bounded family-level support for deterministic numeric minimum phrasing

Representative supported phrasings now include:
- `minimum 24 months sea service`
- `at least 5 years experience`
- `3 years sea time`
- `6+ months sailing experience`

Important scope boundary:
- this remains a numeric minimum family only

### 2.6 Vessel Experience

Current state:
- promoted to bounded family-level support for deterministic vessel-experience phrasing

Representative supported phrasings now include:
- `tanker background`
- `experience in bulk carrier`
- `sailed on lng carrier`
- `container vessel background`

Important scope boundary:
- this is still limited to canonical/configured vessel vocabulary

### 2.7 COC

Current state:
- promoted to bounded family-level support for the explicit COC requirement family

Representative supported phrasings now include:
- `valid coc`
- `coc mandatory`
- `coc holder required`
- `must hold coc`
- `valid certificate of competency required`

### 2.8 STCW Basic

Current state:
- promoted to bounded family-level support for the bundled STCW basic requirement family

Representative supported phrasings now include:
- `valid stcw basic`
- `basic stcw required`
- `all basic stcw required`
- `must hold valid basic stcw`

### 2.9 Validity Windows And Recent Contract Vessel Experience

Current state as of 2026-05-13:
- `passport validity window` is implemented as bounded family-level support
- `visa validity window` is implemented for the supported US visa group
- `recent contract vessel experience` is implemented as SeaJobs-first deterministic support
- bootstrap prompt coverage evidence is recorded in:
  - [AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_PROMPT_COVERAGE_EVIDENCE_2026-05-12.md](/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_PROMPT_COVERAGE_EVIDENCE_2026-05-12.md)

Representative supported phrasings now include:
- `passport valid for 18 months`
- `18 months of passport validity`
- `US visa is valid at least for 10 months`
- `valid US visa for 10 months`
- `12 months experience on container in last 3 contracts`
- `minimum 12 months container experience in recent 3 contracts`

Bootstrap parser-coverage result:
- `passport_validity`: `20/20`, expected-family-present ratio `1.0`
- `us_visa` validity-window prompts: `20/20`, expected-family-present ratio `1.0`
- `recent_contract_vessel_experience`: `19/20`, expected-family-present ratio `0.95`

Important scope boundaries:
- non-US visa validity windows are not included in this first pass
- recent-contract vessel experience remains SeaJobs-first
- email-resume support for recent-contract vessel windows remains out of scope
- one staged `cruise experience` recent-contract prompt did not activate under the fallback prompt-vocabulary path and is documented as a coverage note, not a current parser expansion trigger

Stored real-prompt evidence remains partial:
- `passport_validity`: `11`
- `us_visa`: `12`
- `recent_contract_vessel_experience`: `0`

## 3. Still Patch-Level Or Incomplete

The following should not yet be treated as mature family-level Phase 1 coverage:

- qualitative recruiter prompts remain outside deterministic family coverage
- broad semantic “best fit” style prompts remain unsupported in the deterministic parser
- some wording outside the canonical vessel or visa vocabularies will still remain unsupported
- company continuity remains SeaJobs-only at evaluation time

## 4. Intentionally Unsupported Or Deferred

These remain intentionally outside current deterministic Phase 1 coverage:
- `stable candidate`
- `good retention`
- `good company loyalty`
- `worked long in one company`
- `best fit`
- `strong leadership`
- `good communication`

These are either:
- qualitative semantic prompts
- ranking preferences
- broader derived-signal families that need stronger evidence and product decisions first

## 5. Why This Matters

This checkpoint matters because it changes how parser work should be described.

Before:
- many prompt changes were narrow bug-fix patches

Now:
- some high-value families have enough bounded phrasing coverage to be treated as family-level support

That does not mean the parser is broad natural-language search.
It means a few important recruiter intent families are now handled as families rather than as isolated sentence fixes.

## 6. Recommended Next Steps

The next parser-coverage steps should be:

1. Do not immediately widen more families unless a recruiter-facing gap is pressing.
2. Use this checkpoint to review real recruiter prompts against the now-covered families before widening anything else.
3. Keep unsupported qualitative prompts explicit instead of silently broadening them.

## 7. Bottom Line

As of this checkpoint:
- `age` is family-covered
- `company continuity` is family-covered for SeaJobs-only evaluation
- `visa` is family-covered for the currently supported visa groups
- `rank` is family-covered within the alias-table model
- `sea service` is family-covered for bounded numeric minimum phrasing
- `vessel experience` is family-covered for bounded canonical vessel-type wording
- `COC` is family-covered for explicit requirement phrasing
- `STCW basic` is family-covered for explicit bundled requirement phrasing

The remaining unsupported area is now clearer:
- qualitative prompts
- fuzzy semantic intent
- prompt families that need later-phase ranking or LLM-assisted interpretation
