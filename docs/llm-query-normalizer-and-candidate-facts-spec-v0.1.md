# NjordHR LLM Query Normalizer and Candidate Facts Specification
## Specification v0.1 - Design Draft

---

## 1. Purpose

This document defines three related but separately shippable architecture tracks for NjordHR AI Search:

1. normalize recruiter prompts with an LLM into a validated search-plan JSON
2. expand reusable candidate-facts extraction so hard filters evaluate against structured resume facts
3. keep the current parser and evaluator available during migration

The goal is to reduce prompt-pattern brittleness without weakening deterministic eligibility checks.

These tracks must not be treated as one indivisible rollout. Each track has its own definition of done:

| Track | Definition of done | Required for first MVP? |
|---|---|---|
| Query-plan normalization contract | `query_plan.v1` schema, typed constraint payloads, validator, legacy adapter, and comparison harness exist. | Yes |
| Candidate-facts contract | `candidate_facts.v1` shape, source/status semantics, current-record rules, and fallback policy exist. | No for shadow-mode query normalizer; yes before persisted facts become authoritative. |
| Migration bridge | Legacy parser remains production source; LLM normalizer runs in shadow mode and writes comparison/audit output. | Yes |

First MVP scope:

- implement query-plan schema validation
- wrap the current parser as a legacy query-plan producer
- optionally call the LLM normalizer in shadow mode
- compare legacy vs LLM outputs
- do not change production search decisions
- do not require Supabase candidate-facts persistence

Candidate-facts expansion is a parallel architecture track, not a blocker for prompt-normalizer shadow mode.

---

## 2. Core Decision

NjordHR should not replace deterministic hard filters with LLM pass/fail judgments.

The LLM should interpret the prompt. Code should validate the interpretation and evaluate exact requirements against extracted facts.

Target flow:

```text
Resume ingestion:
PDF/text -> candidate facts JSON -> stored/cache/indexed

Search time:
Recruiter prompt -> LLM normalized query JSON
Query JSON + candidate facts JSON -> deterministic filters + semantic retrieval/ranking
```

---

## 3. LLM Normalizer Output Outcomes

The LLM normalizer should preserve the same broad outcome vocabulary the current parser uses, with clearer names and richer structure.

### 3.1 Applied constraints

`applied_constraints` means:

- the prompt contains a requirement NjordHR understands
- the requirement maps to a supported deterministic filter family
- the normalized value passed schema validation

Example:

```json
{
  "applied_constraints": [
    {
      "id": "recent_contract_vessel_experience",
      "constraint": {
        "type": "recent_contract_vessel_experience",
        "ship_family": "tanker",
        "minimum_months": 18,
        "recent_contract_count": 4
      },
      "source_text": "at least 18 months on tanker in recent 4 contracts",
      "confidence": "high"
    }
  ]
}
```

### 3.2 Unapplied constraints

`unapplied_constraints` means:

- the LLM recognized structured recruiter intent
- the intent sounds like a hard requirement
- NjordHR does not yet have a supported deterministic evaluator for it, or the canonical value is outside the allowed catalogue

These must not silently become semantic-only intent if the user phrased them as mandatory.

Example:

```json
{
  "unapplied_constraints": [
    {
      "id": "dry_dock_experience",
      "reason": "unsupported_filter_family",
      "source_text": "must have dry dock planning experience",
      "suggested_handling": "semantic_with_warning"
    }
  ]
}
```

Recommended product handling:

- If the prompt has applied constraints plus unapplied constraints, run supported hard filters and semantic search, but surface a warning that part of the requirement was not enforced deterministically.
- If the prompt only has unapplied constraints, do not claim exact filtering. Either run semantic retrieval with a clear warning or graceful-fail depending on product choice.
- Always log unapplied constraints to the prompt corpus for catalogue review.

### 3.3 Semantic query

`semantic_query` means:

- prompt text that should influence retrieval/ranking
- fuzzy experience, suitability, quality, or relevance intent
- supported hard constraints should be removed or de-emphasized from this text

Example:

```json
{
  "semantic_query": "strong LNG background and stable profile"
}
```

### 3.4 Unrecognized residual

`unrecognized_residual` means:

- the LLM could not confidently classify part of the prompt
- the text should be logged
- it must not be included in semantic search if it contains mandatory-language markers, numeric thresholds, document/certificate/endorsement terms, validity-window language, recent-window language, or other hard-filter trigger terms

Example:

```json
{
  "unrecognized_residual": [
    {
      "text": "for difficult charterer",
      "suggested_handling": "semantic"
    }
  ]
}
```

### 3.5 Required query-normalizer schema

The normalizer output must be versioned independently from candidate facts. The first production-compatible schema is `query_plan.v1`.

All outputs must validate against this shape before they are used by search:

```json
{
  "schema_version": "query_plan.v1",
  "normalizer": {
    "name": "llm|legacy|hybrid",
    "model": "string-or-null",
    "prompt_template_version": "string",
    "catalog_version": "string",
    "created_at": "ISO-8601 timestamp"
  },
  "input": {
    "raw_prompt": "string",
    "rank_context": "string-or-null",
    "ui_filters": {
      "schema_version": "ui_filters.v1",
      "filters": [
        {
          "id": "ui_filter_id",
          "value": "typed value for this filter id",
          "mode": "required|preferred",
          "source": "ui"
        }
      ]
    }
  },
  "applied_constraints": [],
  "unapplied_constraints": [],
  "semantic_query": "string",
  "unrecognized_residual": [],
  "warnings": [],
  "validation": {
    "status": "valid|invalid|degraded",
    "errors": []
  }
}
```

Required fields:

- `schema_version`
- `normalizer`
- `input.raw_prompt`
- `applied_constraints`
- `unapplied_constraints`
- `semantic_query`
- `unrecognized_residual`
- `warnings`
- `validation.status`

`ui_filters` policy:

- `input.ui_filters.schema_version` is required.
- `input.ui_filters.filters` must be an array.
- Each UI filter id must have a typed value schema in the UI-filter catalogue.
- Unknown UI filter ids must be rejected in production mode and logged in shadow mode.
- Adding a new UI filter is additive when it adds a new filter id and typed value schema to the UI-filter catalogue.
- Existing UI filter ids and value semantics must not change without a new `ui_filters.v2` schema.

Initial `ui_filters.v1` catalogue:

```json
{
  "applied_ship_type": {
    "value": "canonical_ship_type"
  },
  "experienced_ship_type": {
    "value": "canonical_ship_type"
  },
  "rank": {
    "value": "canonical_rank"
  }
}
```

Null policy:

- arrays must be empty arrays, not null
- `semantic_query` must be an empty string when absent, not null
- optional scalar fields may be null only when explicitly marked `string-or-null`
- unknown object keys must be rejected in production mode and logged in shadow mode

`validation.status` policy:

- `valid`: the envelope and every nested constraint payload validates against the active schema.
- `invalid`: the output cannot be used by search. Production must fail closed by default.
- `degraded`: the envelope is valid, but one or more prompt fragments were demoted to `unapplied_constraints` or `unrecognized_residual` because they failed nested payload validation, had unsupported values, or had low confidence.

Production behavior for `invalid`:

- Invalid plans must produce a normalizer-invalid blocked state by default.
- No automatic legacy parser or semantic-retrieval recovery is allowed after invalid normalizer output.
- A separate operator/developer-only legacy rerun action may exist for debugging or emergency operation, but it must be explicitly invoked, labelled `legacy_only_override`, and excluded from normalizer launch evidence.
- The UI must not display legacy-override results as verified by the LLM-normalizer path.
- The audit must record the invalid plan, validation errors, override user if any, override reason, and resulting execution path.

Production behavior for `degraded`:

- If degraded output contains any `mode=required` unapplied constraint, use the required-unapplied behavior in the operational matrix.
- If degraded output contains only preferred unapplied constraints or semantic residuals, search may continue with warnings.
- Degraded plans must be logged for review and cannot be used as launch evidence unless the degraded portion is explicitly out of scope.

Each `applied_constraints[]` item must use:

```json
{
  "id": "supported_constraint_id",
  "mode": "required|preferred",
  "constraint": "typed payload object for this id",
  "source_text": "exact or near-exact prompt fragment",
  "confidence": "high|medium|low",
  "compatibility": {
    "legacy_hard_constraints_key": "string-or-null",
    "legacy_applied_constraint_id": "string-or-null"
  }
}
```

The `constraint` payload is not free-form. It must validate against the typed payload schema for the corresponding `id`. For `query_plan.v1`, the minimum supported payload contracts are:

```json
{
  "age_range": {
    "type": "age_range",
    "minimum_years": "integer-or-null",
    "maximum_years": "integer-or-null"
  },
  "rank_match": {
    "type": "rank_match",
    "rank": "canonical_rank"
  },
  "coc_document_gate": {
    "type": "coc_document_gate",
    "required": true
  },
  "coc_grade_match": {
    "type": "coc_grade_match",
    "grade": "canonical_coc_grade"
  },
  "stcw_basic": {
    "type": "stcw_basic",
    "required": true
  },
  "us_visa": {
    "type": "us_visa",
    "required": true,
    "minimum_months_remaining": "integer-or-null"
  },
  "passport_validity": {
    "type": "passport_validity",
    "minimum_months_remaining": "integer"
  },
  "recent_contract_vessel_experience": {
    "type": "recent_contract_vessel_experience",
    "ship_family": "canonical_ship_family",
    "minimum_months": "integer-or-null",
    "recent_contract_count": "integer"
  },
  "engine_experience": {
    "type": "engine_experience",
    "engine_family": "canonical_engine_family",
    "minimum_months": "integer-or-null",
    "recent_contract_count": "integer-or-null"
  },
  "engine_vessel_experience": {
    "type": "engine_vessel_experience",
    "engine_family": "canonical_engine_family",
    "ship_family": "canonical_ship_family-or-null",
    "minimum_months": "integer-or-null",
    "recent_contract_count": "integer-or-null"
  },
  "company_continuity": {
    "type": "company_continuity",
    "minimum_contracts": "integer",
    "same_company_required": true
  },
  "recency": {
    "type": "recency",
    "maximum_months_since_last_contract": "integer-or-null",
    "must_be_currently_sailing": "boolean-or-null"
  },
  "rank_duration_experience": {
    "type": "rank_duration_experience",
    "rank": "canonical_rank-or-null",
    "minimum_months": "integer"
  },
  "stcw_endorsement": {
    "type": "stcw_endorsement",
    "endorsements_required": ["canonical_endorsement"]
  },
  "rank_certificate_expectation": {
    "type": "rank_certificate_expectation",
    "rank": "canonical_rank-or-null",
    "certificates_required": ["canonical_certificate"],
    "endorsements_required": ["canonical_endorsement"]
  },
  "certificate_requirement": {
    "type": "certificate_requirement",
    "certificates_required": ["canonical_certificate"]
  },
  "experience_ship_type": {
    "type": "experience_ship_type",
    "ship_family": "canonical_ship_family",
    "minimum_months": "integer-or-null"
  },
  "availability": {
    "type": "availability",
    "status": "available|available_by_date",
    "available_by": "ISO-8601-date-or-null"
  }
}
```

Every supported constraint id must have:

- a typed payload schema
- canonical value enums or catalogue references
- legacy compatibility mapping
- evaluator ownership
- tests proving invalid payloads are rejected

The normalizer must not emit an `applied_constraints` item for a family that lacks a typed payload schema.

### 3.6 Family coverage map

The hard-filter catalogue and typed payload catalogue must be reviewed together. A family may appear in only one of these statuses:

| Family | Normalizer status | Reason |
|---|---|---|
| age_range | active | Typed payload exists and evaluator exists. |
| rank_match | active | Typed payload exists and evaluator exists. |
| coc_document_gate | active | Typed payload exists and evaluator exists. |
| coc_grade_match | active | Typed payload exists and evaluator exists. |
| stcw_basic | active | Typed payload exists and evaluator exists. |
| rank_duration_experience | active | Typed payload exists and evaluator exists. |
| recent_contract_vessel_experience | active | Typed payload exists and evaluator exists. |
| engine_experience | active | Typed payload exists and evaluator exists. |
| engine_vessel_experience | active | Typed payload exists and evaluator exists. |
| company_continuity | active | Typed payload exists and evaluator exists. |
| recency | active | Typed payload exists and evaluator exists. |
| availability | active | Typed payload exists and evaluator exists. |
| stcw_endorsement | active | Typed payload exists and evaluator exists. |
| rank_certificate_expectation | active | Typed payload exists and evaluator exists. |
| certificate_requirement | active | Typed payload exists and evaluator exists. |
| experience_ship_type | active | Typed payload exists and evaluator exists. |
| us_visa | active | Typed payload exists and evaluator exists. |
| passport_validity | active | Typed payload exists and evaluator exists. |
| min_sea_service | unsupported | Current parser can recognize some forms, but deterministic evaluator support is not yet active. Must emit `unapplied_constraints`. |
| vessel_type | unsupported | Current parser can recognize some forms, but current handling is not an active prompt-normalizer hard-filter family in this plan. Must emit `unapplied_constraints` unless represented by `experience_ship_type` or a UI filter. |

Any new family must be added to this map before implementation. If a family has no typed payload schema, the normalizer must emit it only as `unapplied_constraints`.

Each `unapplied_constraints[]` item must use:

```json
{
  "id": "unsupported_or_unknown_constraint_id",
  "mode": "required|preferred",
  "reason": "unsupported_filter_family|unsupported_value|ambiguous_value|insufficient_schema|validation_failed",
  "source_text": "exact or near-exact prompt fragment",
  "suggested_handling": "block_search|semantic_with_warning|ignore_with_warning",
  "confidence": "high|medium|low"
}
```

Each `warnings[]` item must use:

```json
{
  "code": "string",
  "message": "string",
  "severity": "info|warning|error"
}
```

Backward compatibility policy:

- `query_plan.v1` must remain additive only.
- New optional fields may be added.
- Existing field names, meanings, enum values, and null behavior must not change.
- Breaking changes require `query_plan.v2` plus an adapter from v1 to the evaluator compatibility shape.
- The first implementation must include a validator that rejects invalid v1 output before search uses it.

---

## 4. Hard vs Semantic Decision Rules

The LLM normalizer receives:

- the raw recruiter prompt
- selected rank context
- UI-provided filters
- supported hard-filter catalogue
- allowed canonical vocabularies
- JSON schema
- examples from the prompt corpus

It must extract a hard constraint only when all are true:

1. the recruiter is asking for a condition, qualification, validity, count, duration, rank, document, certificate, endorsement, vessel, engine, or availability fact
2. the condition maps to a supported hard-filter family
3. the canonical value is known or safely normalizable
4. the output passes schema validation

It must keep text as semantic intent when:

- it is about quality, suitability, strength, stability, leadership, communication, background, exposure, or broad relevance
- it lacks enough structure for a deterministic evaluator
- the catalogue does not support exact evaluation and the prompt is not clearly mandatory

Mandatory unsupported requirements should be `unapplied_constraints`, not silently semantic.

Mandatory-marker scan:

Any fragment containing one or more of the following must be classified as `applied_constraints` or `unapplied_constraints`, never default semantic text:

- modal or requirement words: `must`, `required`, `require`, `need`, `needs`, `mandatory`, `should have`, `holding`, `hold`, `valid`
- threshold words: `minimum`, `at least`, `more than`, `less than`, `within`, `last`, `recent`, `current`
- numeric duration/count patterns: months, years, contracts, vessels, ships, days
- document/certificate/endorsement terms: passport, visa, CoC, STCW, ECDIS, CoP, DC, endorsement, certificate, course
- deterministic domain terms from the active hard-filter catalogue: rank aliases, ship-type aliases, engine aliases, certificate aliases, endorsement aliases

If such a fragment cannot be converted into a valid typed `applied_constraints` payload, it must become `unapplied_constraints` with `suggested_handling=block_search` when `mode=required`.

### 4.1 Decomposition precedence

When a prompt fragment could plausibly be both a hard constraint and semantic intent, the normalizer must apply this precedence:

1. Supported required hard constraint
2. Supported preferred hard constraint
3. Required unsupported structured constraint
4. Preferred unsupported structured constraint
5. Semantic intent
6. Unrecognized residual

Hard-filter interpretation wins over semantic interpretation when:

- the fragment maps to a supported typed constraint payload, and
- the wording indicates requirement or preference, and
- canonical values validate.

Fragment ownership rule:

- Each source fragment has exactly one primary bucket: `applied_constraints`, `unapplied_constraints`, `semantic_query`, or `unrecognized_residual`.
- Constraint-owned fragments must be removed from `semantic_query`.
- `semantic_query` may include connector words needed for readability, but not the full hard-constraint phrase.
- For MVP, the same source-text span must not appear in both a constraint bucket and `semantic_query`.
- If a phrase contains both structured and qualitative meaning, the normalizer must split it into non-overlapping source spans.
- If the phrase cannot be split confidently, it must become `unapplied_constraints` when it contains mandatory markers; otherwise it must become `unrecognized_residual`.
- Entity context needed for semantic retrieval must be represented later as structured context references, not by copying hard-constraint text into `semantic_query`.

Examples:

- `must have ECDIS and strong LNG background`: `ECDIS` is an applied hard constraint; `strong LNG background` remains semantic.
- `good tanker experience`: semantic unless the prompt includes a measurable or supported hard-filter shape.
- `must have tanker experience`: applied hard constraint if it maps to `experience_ship_type`; otherwise required unapplied.
- `prefer ECDIS`: preferred hard constraint, not plain semantic, because certificate presence is structured and supported.
- `minimum 12 months tanker experience and strong leadership`: the duration/tanker fragment is hard; only `strong leadership` remains semantic.

### 4.2 Operational behavior matrix

The engine must use the following behavior for normalized query plans:

| Query-plan shape | Search behavior | Recruiter-visible behavior | Audit behavior |
|---|---|---|---|
| applied constraints only | Evaluate deterministic filters. If all applied constraints pass, return verified matches. | Normal hard-filter results. | Log applied constraints and decisions. |
| semantic query only | Run semantic retrieval/ranking. Do not emit hard-filter pass/fail claims. | Ranked relevance results. | Log semantic-only query. |
| applied constraints + semantic query | Evaluate deterministic filters first, then run semantic reasoning/ranking only on candidates whose hard-filter decision is `PASS`. Candidates with `UNKNOWN` remain in the hard-filter review path and must not be promoted by semantic ranking. | Mixed result with hard-filter reasons plus semantic reason. | Log both paths. |
| applied constraints + required unapplied constraints | Block the search result list. Do not evaluate or display partial deterministic matches. Do not return ranked semantic results. | Blocked state: "Search includes requirements NjordHR cannot enforce yet." A future separate exploratory action may be offered, but it must be distinct from the blocked result. | Log unapplied details and do not count as executed search results. |
| required unapplied constraints only | Fail closed with graceful failure. | Must not imply requirements were checked. | Log as catalogue gap. |
| preferred unapplied constraints only | Run semantic retrieval with warning that preference was not enforced structurally. | Ranked relevance results with warning. | Log as catalogue gap. |
| invalid query plan | Block by default. Legacy execution only through explicit operator/developer override outside normal search flow. | Normalizer-invalid blocked state. | Log validation errors; log override separately if used. |
| degraded query plan | Follow the stricter behavior implied by the degraded content: required unapplied constraints fail closed; preferred-only degradation may continue with warning; validation-demoted applied constraints must not be evaluated. | Warning or graceful failure depending on degraded content. | Log degraded reason codes. |

Required unapplied constraints must fail closed in production. Any semantic exploration for those fragments must be a separate explicitly labelled product action, not an automatic fallback.

---

## 5. Reuse Of Existing Code

We should reuse existing code heavily.

### 5.1 Reuse as schema source

The current extractor functions in [ai_analyzer.py](/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py) should seed the first hard-filter catalogue:

- age
- rank
- CoC document gate
- CoC grade
- STCW basic
- rank duration experience
- recent-contract vessel experience
- engine experience
- engine + vessel compound experience
- company continuity
- recency
- availability
- endorsement requirement
- rank certificate expectation
- general ship-type experience
- US visa
- passport validity

The current `hard_constraints` output shape from `_extract_job_constraints` should become the compatibility target for the first LLM-normalizer adapter.

### 5.2 Catalogue snapshot for migration

Every shadow/comparison run must use a frozen catalogue snapshot that includes:

- hard-filter family map
- typed payload schemas
- canonical rank vocabulary
- canonical ship-type and ship-family vocabulary
- canonical engine-family vocabulary
- canonical certificate and endorsement vocabulary
- UI-filter catalogue
- legacy compatibility mappings

The snapshot id must be recorded in:

- legacy adapter output
- LLM query plan output
- comparison report
- activation evidence pack

Legacy and LLM outputs must be normalized against the same snapshot before comparison. Catalogue changes are separate migration events and must not be mixed into parser-quality comparisons.

### 5.3 Reuse as fallback parser

During migration, run both systems:

```text
raw prompt
   -> current parser output
   -> LLM normalizer output
   -> comparison/audit row
   -> production uses current parser initially
```

Later stages can switch selected families to LLM-normalized output after corpus evidence shows equivalent or better behavior.

### 5.4 Comparison equivalence contract

The comparison harness must convert both legacy parser output and LLM query-plan output into a canonical comparison record before scoring.

Canonical comparison record:

```json
{
  "catalog_snapshot_id": "string",
  "prompt_id": "string",
  "family": "constraint_family",
  "mode": "required|preferred",
  "normalized_payload": {},
  "source_text": "string",
  "status": "applied|unapplied|semantic|residual|invalid|degraded"
}
```

Comparison outcomes:

- `equivalent`: same family, mode, canonical values, threshold values, recent-window values, and required/preferred status.
- `expected_delta`: known and accepted difference caused by a documented legacy limitation or intentional LLM improvement.
- `unsupported_family_delta`: the family is marked unsupported in the normalizer plan and must be excluded from activation scoring.
- `regression`: LLM output drops, weakens, misclassifies, or changes a supported legacy constraint without an approved expected-delta note.
- `schema_error`: LLM output fails validation.
- `catalogue_drift`: outputs were produced with different catalogue snapshots and must not be scored.

Per-family equivalence must define:

- exact legacy field path to query-plan payload mapping
- canonical vocabulary normalization
- tolerated numeric differences, if any
- whether missing optional fields are equivalent to null
- which differences are expected deltas

Default tolerance is strict equality after canonicalization. Numeric thresholds, recent-contract counts, validity windows, rank, engine, ship family, certificate, and endorsement values have no tolerance unless a family-specific rule explicitly says otherwise.

Unsupported families such as `min_sea_service` and unsupported `vessel_type` forms are expected divergences when the normalizer emits `unapplied_constraints`. They must remain visible in reports but excluded from activation agreement metrics.

### 5.5 Reuse evaluator code

The deterministic evaluator should continue to use normalized hard constraints in the existing format initially.

The migration should avoid changing pass/fail logic while prompt-normalization behavior is being validated.

---

## 6. Candidate Facts Scope

Candidate facts should be source-agnostic in the target architecture. This is not a requirement that every source be fully parsed in the first implementation.

Candidate-facts MVP source scope:

- SeaJobs resumes: in scope for structured facts expansion first.
- Email/manual resumes: in scope for raw-text fallback and partial facts only until extractor coverage is explicitly added.
- Website-download resumes: same as their detected layout; otherwise partial facts plus fallback.
- Unknown layouts: partial facts only; do not make strict absence claims from missing fields.

The schema must support:

- SeaJobs resumes
- email/manual resumes
- future website-download resumes
- unknown layouts with partial extraction

Extractor coverage may be phased by source, but the target facts schema must be shared.

Unsupported-source fallback policy:

- `source_origin=unknown` or unsupported `detected_layout` values must set `extraction.status=partial` or `failed`.
- Hard-filter evaluators must return `UNKNOWN` rather than `FAIL` when required source facts are unavailable because the source layout is unsupported.
- Semantic retrieval may still use raw text/chunks for unsupported layouts.
- Unsupported source/layout occurrences must be logged for extraction-roadmap review.

Recommended top-level shape:

```json
{
  "schema_version": "candidate_facts.v1",
  "source": {
    "resume_id": "...",
    "candidate_id": "...",
    "source_origin": "seajobs_download|email_intake|manual_upload|website_download|unknown",
    "detected_layout": "seajobs|email|manual|website|unknown",
    "file_name": "...",
    "content_hash": "..."
  },
  "identity": {
    "candidate_name": {
      "value": "string-or-null",
      "presence": "observed_true|observed_false|unobserved_unknown",
      "confidence": "high|medium|low",
      "evidence_ids": ["evidence_id"]
    },
    "dob": {
      "value": "ISO-8601-date-or-null",
      "presence": "observed_true|observed_false|unobserved_unknown",
      "confidence": "high|medium|low",
      "evidence_ids": ["evidence_id"],
      "extraction": {
        "extractor": "string",
        "parser_version": "string",
        "method": "table_parser|regex|ocr|llm_extraction|manual|fallback"
      }
    },
    "nationality": {
      "value": "string-or-null",
      "presence": "observed_true|observed_false|unobserved_unknown",
      "confidence": "high|medium|low",
      "evidence_ids": ["evidence_id"]
    }
  },
  "rank": {},
  "documents": [],
  "certificates": [],
  "endorsements": [],
  "courses": [],
  "contracts": [],
  "rank_experience": [],
  "engine_experience": [],
  "vessel_experience": [],
  "application": {},
  "derived": {},
  "evidence": [],
  "extraction": {
    "parser_version": "...",
    "status": "complete|partial|failed",
    "minimums_satisfied": ["family_id"],
    "minimums_missing": ["family_id"],
    "provenance": {
      "mode": "persisted|transient_fallback|raw_text_fallback|semantic_chunk",
      "raw_text_version": "string-or-null",
      "chunk_index_version": "string-or-null",
      "fallback_reason": "string-or-null"
    },
    "warnings": []
  }
}
```

Search should use stored/cached facts where available. Prompt-time re-extraction remains a fallback until extraction coverage is proven.

### 6.1 Fact item contract

Repeatable fact buckets must use typed item contracts. Opaque objects are not eligible for deterministic filtering.

All deterministic fact items must include the common fields below:

```json
{
  "fact_id": "stable id within this facts row",
  "fact_type": "document|certificate|endorsement|course|contract|rank_experience|engine_experience|vessel_experience",
  "canonical_value": "string-or-null",
  "display_value": "string-or-null",
  "presence": "observed_true|observed_false|unobserved_unknown",
  "confidence": "high|medium|low",
  "evidence_ids": ["evidence_id"],
  "extraction": {
    "extractor": "string",
    "parser_version": "string",
    "source_origin": "seajobs_download|email_intake|manual_upload|website_download|unknown",
    "detected_layout": "seajobs|email|manual|website|unknown",
    "method": "table_parser|regex|ocr|llm_extraction|manual|fallback"
  }
}
```

Additional typed bucket fields:

```json
{
  "documents[]": {
    "document_type": "passport|us_visa|other",
    "document_number_present": "boolean-or-null",
    "issue_date": "ISO-8601-date-or-null",
    "expiry_date": "ISO-8601-date-or-null",
    "country": "string-or-null"
  },
  "certificates[]": {
    "certificate_type": "canonical_certificate",
    "certificate_number_present": "boolean-or-null",
    "issue_date": "ISO-8601-date-or-null",
    "expiry_date": "ISO-8601-date-or-null"
  },
  "endorsements[]": {
    "endorsement_type": "canonical_endorsement",
    "level": "basic|advanced|unknown|null",
    "issue_date": "ISO-8601-date-or-null",
    "expiry_date": "ISO-8601-date-or-null"
  },
  "courses[]": {
    "course_type": "canonical_course",
    "issue_date": "ISO-8601-date-or-null",
    "expiry_date": "ISO-8601-date-or-null"
  },
  "contracts[]": {
    "contract_order": "integer",
    "rank": "canonical_rank-or-null",
    "vessel_name": "string-or-null",
    "vessel_type": "canonical_ship_type-or-null",
    "ship_family": "canonical_ship_family-or-null",
    "engine_family": "canonical_engine_family-or-null",
    "company": "string-or-null",
    "start_date": "ISO-8601-date-or-null",
    "end_date": "ISO-8601-date-or-null",
    "duration_months": "number-or-null",
    "is_current_contract": "boolean-or-null"
  },
  "rank_experience[]": {
    "rank": "canonical_rank",
    "duration_months": "number-or-null",
    "source": "contracts|total_experience_table|derived|unknown"
  },
  "engine_experience[]": {
    "engine_family": "canonical_engine_family",
    "duration_months": "number-or-null",
    "contract_ids": ["fact_id"]
  },
  "vessel_experience[]": {
    "ship_family": "canonical_ship_family",
    "duration_months": "number-or-null",
    "contract_ids": ["fact_id"]
  }
}
```

If a bucket is stored with a looser shape during development, it must be placed under `derived` or `extraction.debug` and must not be consumed by deterministic filters.

### 6.2 Presence semantics

Every deterministic fact item must carry `presence`:

- `observed_true`: evidence supports that the fact exists or is true.
- `observed_false`: evidence explicitly supports that the fact is absent/false, such as a structured "No" field or an authoritative negative source.
- `unobserved_unknown`: the extractor did not observe reliable evidence either way.

Evaluator rules:

- `PASS` may use only `observed_true` facts that meet the family-specific evidence requirement.
- `FAIL` may use `observed_false` facts or observed positive facts that prove an insufficient value, such as an expired document or insufficient duration.
- Missing arrays, missing fields, OCR failure, unsupported layouts, and parser failure are `unobserved_unknown`, not `observed_false`.
- Absence of a certificate/document in raw resume text is not `observed_false` unless the source has an authoritative structured absence marker.
- If required facts are `unobserved_unknown`, the hard-filter result must be `UNKNOWN`, not `FAIL`.

### 6.3 Evidence and fallback provenance

Evidence must be first-class when it affects search or hard-filter decisions.

Evidence item contract:

```json
{
  "evidence_id": "stable id within this facts row",
  "source_kind": "pdf_page|table_cell|raw_text_chunk|ocr_text|manual_entry|external_record",
  "source_id": "resume_id/chunk_id/page_id/table_id/etc",
  "page_number": "integer-or-null",
  "table_name": "string-or-null",
  "row_index": "integer-or-null",
  "column_name": "string-or-null",
  "text_snippet": "short string-or-null",
  "text_hash": "string-or-null",
  "chunk_index_version": "string-or-null",
  "raw_text_version": "string-or-null"
}
```

Search audits must record whether a decision used:

- persisted candidate facts
- transient prompt-time extraction
- raw-text fallback
- semantic chunk retrieval

For fallback paths, audit rows must include:

- fallback mode
- fallback reason
- raw-text version or chunk-index version
- evidence ids or chunk ids used
- whether the fallback affected pass/fail/unknown or only explanation/ranking

Fallback-derived facts may be used for a single search decision only unless persisted as a new facts revision.

Transient fallback hard-filter eligibility:

- Transient fallback facts must be assigned a deterministic `transient_facts_id` derived from candidate resume id, resume blob content hash, extractor version, raw-text version, chunk-index version, fallback mode, and query/search session id.
- Transient fallback facts must include the same typed fact item, presence, evidence, and provenance fields as persisted facts.
- If the fallback source cannot provide stable raw-text or chunk snapshot identifiers, transient facts are not eligible for auditable hard-filter `PASS` or `FAIL`; the evaluator must return `UNKNOWN`.
- Search audits must persist the transient facts payload or a replayable pointer to it for the audit retention period.

### 6.4 Minimum facts by hard-filter family

Partial facts may become current only for the hard-filter families whose minimum facts are satisfied. The extractor must record `minimums_satisfied` and `minimums_missing`.

Minimum fact requirements:

| Family | Minimum facts required for deterministic evaluation |
|---|---|
| age_range | `identity.dob` observed true with evidence. |
| rank_match | current/applied rank observed true or selected rank context. |
| passport_validity | passport document fact with expiry date observed true. |
| us_visa | US visa document fact with presence and expiry date observed true, or observed_false from authoritative structured absence. |
| coc_document_gate | CoC certificate/document fact observed true or observed_false from authoritative structured absence. |
| coc_grade_match | CoC grade observed true with evidence. |
| stcw_basic | required STCW certificate facts observed true or observed_false from authoritative structured absence. |
| stcw_endorsement | required endorsement facts observed true or observed_false from authoritative structured absence. |
| certificate_requirement | required certificate facts observed true or observed_false from authoritative structured absence. |
| rank_certificate_expectation | rank context plus certificate/endorsement facts above. |
| recent_contract_vessel_experience | ordered contract facts with vessel type/family and dates or duration for the recent-window count. |
| engine_experience | engine facts from contracts or engine-experience facts with evidence. |
| engine_vessel_experience | engine facts plus vessel facts on the same contract rows, or UNKNOWN. |
| rank_duration_experience | rank-duration facts from contracts or total-experience table, with source noted. |
| experience_ship_type | vessel-experience facts or contract rows with vessel family. |
| company_continuity | ordered contract facts with company values for the evaluated window. |
| recency | latest contract end/sign-off date or current-contract marker. |
| availability | availability/sign-off/current-contract fact with evidence. |

If a partial row is current, evaluators may only run deterministically for families listed in `minimums_satisfied`. Other families must return `UNKNOWN` or use explicitly audited fallback.

### 6.5 Missing-facts policy by hard-filter family

Missing facts policy:

| Family | Missing/partial facts behavior |
|---|---|
| age_range | Missing DOB returns `UNKNOWN`; never infer failure from absent DOB. |
| rank_match | Missing rank returns `UNKNOWN` unless selected rank context is authoritative for the search scope. |
| passport_validity | Missing passport fact or expiry returns `UNKNOWN`; expired observed passport returns `FAIL`. |
| us_visa | Missing US visa evidence returns `UNKNOWN`; observed expired visa returns `FAIL`; observed authoritative absence returns `FAIL` only when visa is required. |
| coc_document_gate | Missing CoC evidence returns `UNKNOWN`; authoritative absence returns `FAIL`. |
| coc_grade_match | Missing grade returns `UNKNOWN`; observed non-matching grade returns `FAIL`. |
| stcw_basic | Missing required STCW evidence returns `UNKNOWN`; authoritative absence/expired observed certificate returns `FAIL`. |
| stcw_endorsement | Missing endorsement evidence returns `UNKNOWN`; authoritative absence/expired observed endorsement returns `FAIL`. |
| certificate_requirement | Missing certificate evidence returns `UNKNOWN`; authoritative absence/expired observed certificate returns `FAIL`. |
| rank_certificate_expectation | Missing rank or certificate evidence returns `UNKNOWN`; authoritative absence follows certificate/endorsement rules. |
| recent_contract_vessel_experience | Missing ordered recent contracts, dates, durations, or vessel family returns `UNKNOWN`; observed insufficient qualifying duration/count returns `FAIL`. |
| engine_experience | Missing engine evidence returns `UNKNOWN`; observed non-matching engine evidence is not by itself failure unless the evaluated contract/window coverage is complete. |
| engine_vessel_experience | Missing engine or vessel linkage on contract rows returns `UNKNOWN`; observed complete window with insufficient matches returns `FAIL`. |
| rank_duration_experience | Missing rank duration returns `UNKNOWN`; observed insufficient duration from accepted source returns `FAIL`. |
| experience_ship_type | Missing vessel experience evidence returns `UNKNOWN`; observed complete experience set with no matching family may return `FAIL` only if extraction coverage for vessel experience is complete. |
| company_continuity | Missing ordered company values returns `UNKNOWN`; observed break in required window returns `FAIL`. |
| recency | Missing latest sign-off/current marker returns `UNKNOWN`; observed stale date beyond threshold returns `FAIL`. |
| availability | Missing availability/sign-off evidence returns `UNKNOWN`; observed unavailable status/date conflict returns `FAIL`. |

Every hard-filter family must have tests for missing facts, observed insufficient facts, and observed satisfied facts before activation.

---

## 7. Storage Strategy

Short term:

- keep local/cache candidate facts while schema evolves
- use existing runtime artifacts and tests to stabilize field shape

Medium term:

- store one candidate-facts row per resume version in Supabase/Postgres
- use JSONB for `facts_json` initially
- include `parser_version`, `schema_version`, `content_hash`, `extraction_status`, and timestamps

Recommended first table:

```text
resume_blobs
  id
  tenant_id
  content_hash
  storage_uri
  reference_count_cache
  deletion_eligibility_status
  created_at

candidate_resumes
  id
  candidate_id
  resume_blob_id
  source_event_id
  lifecycle_status
  created_at

candidate_resume_facts
  id
  candidate_id
  candidate_resume_id
  resume_blob_id
  schema_version
  parser_version
  facts_revision
  facts_json
  extraction_status
  extraction_warnings
  is_current_for_resume
  created_at
  updated_at
```

Required uniqueness and selection rules:

- `resume_blobs.content_hash` must be unique within the same tenant/account scope.
- The same blob may be linked to more than one candidate only through separate `candidate_resumes` rows and only after identity-resolution review permits it.
- Blob storage uses a derived reference count based on active `candidate_resumes` links. `resume_blobs.reference_count_cache` is a cache only, not the source of truth.
- Link create/delete/anonymize operations must update `candidate_resumes` and refresh `reference_count_cache` in the same transaction, or leave the cache marked stale for a reconciliation job.
- Physical deletion must use the authoritative deletion-eligibility check, not `reference_count_cache` alone.
- A periodic consistency check must compare `reference_count_cache` to the count of active candidate links and log/repair drift.
- A blob may be physically deleted only when the deletion-eligibility check reports no active candidate links, retained audit requirements, legal holds, or export-replay requirements.
- `candidate_resume_facts` must have at most one current facts row for each `(candidate_resume_id, schema_version)`.
- The unique identity for a facts extraction is `(candidate_resume_id, schema_version, parser_version, facts_revision)`.
- A parser re-run for the same `candidate_resume_id`, `schema_version`, and `parser_version` may replace a failed/incomplete row only before it is marked current.
- Once a facts row has been used by a completed search audit, it must be treated as immutable. Corrections create a new `facts_revision`.
- Search must select facts through one deterministic selector, not ad hoc ordering.

Recommended current-facts selector:

```text
candidate.current_candidate_resume_id
  -> current search-active candidate_resume for the candidate
  -> candidate_resume_facts where:
       candidate_resume_id = current_candidate_resume_id
       schema_version = active schema
       is_current_for_resume = true
  -> highest parser_version/facts_revision only if exactly one row is marked current
```

If there is no current facts row for the selected resume, search may:

1. build candidate facts synchronously using the current fallback path, and
2. mark the search audit as using transient facts, or
3. exclude the candidate from strict hard-filter verification with an UNKNOWN result depending on the filter family.

It must not silently choose an arbitrary older facts row.

Resume lifecycle states:

- `ingested`: blob/link exists, extraction not complete enough for search-active use
- `extracting`: extraction is running
- `search_active`: eligible for default search selection
- `superseded`: older resume retained for audit/history but not default search
- `rejected_duplicate`: duplicate or identity-conflict record not used for search

Default search must select only `search_active` candidate resumes. Newly ingested resumes do not affect default search until promoted to `search_active`.

### 7.1 Transaction boundary for current facts

Facts writes must use a transaction or equivalent atomic operation:

1. insert the new facts row with `is_current_for_resume=false`
2. validate `facts_json` against the active candidate-facts schema
3. if validation passes and extraction status is acceptable, set all other rows for `(candidate_resume_id, schema_version)` to `is_current_for_resume=false`
4. set the new row to `is_current_for_resume=true`
5. commit

Search must read only committed current facts rows. It must not read a facts row while extraction is still in progress.

Required isolation semantics:

- Facts writes must run under a transaction that prevents two rows from being current for the same `(candidate_resume_id, schema_version)` at commit time. In Postgres, use a partial unique index on `(candidate_resume_id, schema_version)` where `is_current_for_resume=true`.
- The current-row flip must happen in the same transaction that validates and marks the new row current.
- Promotion of a `candidate_resumes` row to `search_active` and update of `candidates.current_candidate_resume_id` must happen in the same transaction as marking the acceptable facts row current, or in a later transaction that verifies the current facts row still exists.
- Search must select and record the facts row id before evaluating a candidate.
- Search must select and record the `candidate_resume_id` before selecting facts.
- Candidate evaluation is pinned to the selected `(candidate_id, candidate_resume_id, candidate_resume_facts_id)` tuple.
- Search must use that selected facts row id for the entire candidate evaluation, even if a newer current row commits during the same search.
- Search audit replay must load by recorded facts row id or recorded `(candidate_resume_id, schema_version, parser_version, facts_revision)`, never by "current" selector.
- If the selected facts row is deleted before audit replay, replay must report `facts_unavailable_deleted` rather than substituting a newer row.

Recommended read behavior:

- For each candidate, perform a single search-active resume lookup and current-facts lookup at candidate-evaluation start.
- Store `candidate_resumes.id` in the search audit.
- Store `candidate_resume_facts.id` in the search audit.
- Do not re-query current facts for the same candidate later in the same search.
- If the database supports transaction snapshots, use a repeatable-read snapshot for the candidate-facts lookup phase. If not, row-id pinning is mandatory.

If extraction first produces `partial` facts and later produces `complete` facts:

- `partial` may become current only if it meets the family-specific minimum fact requirements for search
- a later `complete` extraction creates a new `facts_revision`
- the `complete` row replaces the partial row as current atomically
- search audits that used the partial row continue to reference that exact partial facts revision

Concurrent write behavior:

- if a search starts while no current facts row exists for the selected search-active candidate resume, it may use transient fallback facts and must record that choice
- if a current facts row is replaced while a search is running, that search continues referencing the facts row selected at candidate-evaluation start
- search audit replay must use the recorded `candidate_resume_id`, `schema_version`, `parser_version`, and `facts_revision`, not the later current row

If more than one current row is detected for `(candidate_resume_id, schema_version)`, search must fail closed for persisted facts on that resume, log a data-integrity error, and use transient fallback or UNKNOWN according to the filter family.

Longer term:

- promote stable high-value facts into relational tables if query performance or reporting requires it
- keep vector/chunk data separate from deterministic facts

---

## 8. Resume Versioning

Do not overwrite old resume facts when a candidate sends an updated resume.

Recommended model:

```text
same candidate + new content_hash -> new candidate_resume record + new facts row
same candidate_resume + new parser_version -> new facts_revision, old row retained
```

Search should normally evaluate the `search_active` candidate resume per candidate unless the UI explicitly asks for historical search.

### 8.1 Current resume semantics

The authoritative search-active resume is `candidates.current_candidate_resume_id`.

`candidate_resumes.lifecycle_status` is a lifecycle annotation used for workflow and cleanup, not the default search selector. If it is stored, it must be reconciled to `candidates.current_candidate_resume_id`; it is not authoritative for search.

Reconciliation rule:

- exactly one `candidate_resumes` row per candidate may have `lifecycle_status=search_active`
- production storage must enforce that with a partial unique index or equivalent constraint on `candidate_resumes(candidate_id)` where `lifecycle_status='search_active'`
- that row must equal `candidates.current_candidate_resume_id`
- if they disagree, search must fail closed for that candidate, log `current_resume_selector_mismatch`, and route the candidate to data-integrity repair

When a new non-duplicate resume arrives for an existing candidate:

1. create or reuse a `resume_blobs` row by `content_hash`
2. create a `candidate_resumes` row with `lifecycle_status=ingested`
3. run extraction
4. create a new `candidate_resume_facts` row
5. promote the `candidate_resumes` row to `search_active` and update `candidates.current_candidate_resume_id` only after extraction reaches `complete` or acceptable `partial` status
6. keep old resume and facts rows for audit/debugging

Duplicate detection:

- same candidate + same `content_hash`: do not create a new candidate resume version; link the source event to the existing `candidate_resumes` row
- different candidate + same `content_hash`: create a separate `candidate_resumes` link only after identity-resolution review permits it; otherwise route to duplicate review

Parser-version refresh:

- same candidate resume + improved parser creates a new `facts_revision`
- exactly one facts row per `(candidate_resume_id, schema_version)` may be marked current
- previous facts rows remain available for search-audit replay

Search-audit rows must record:

- `candidate_resume_id`
- `resume_blob_id`
- `candidate_resume_facts_id` when persisted facts were used
- `source_origin`
- `detected_layout`
- `facts_schema_version`
- `parser_version`
- `facts_revision`
- whether facts were persisted or transient
- fallback mode, raw-text version, chunk-index version, and evidence ids/chunk ids when fallback influenced the decision

---

## 9. Modularization Target

The implementation should be split into modules before or during the LLM-normalizer work.

Recommended modules:

```text
query_understanding/
  schema.py
  hard_filter_catalog.py
  legacy_parser_adapter.py
  llm_normalizer.py
  normalizer_compare.py
  validation.py

candidate_facts/
  schema.py
  extractors/
    seajobs.py
    generic_pdf.py
    certificates.py
    endorsements.py
    contracts.py
    engines.py
  storage.py
  versioning.py

search/
  hard_filter_evaluator.py
  semantic_retrieval.py
  result_assembly.py
```

Initial implementation may keep compatibility wrappers in `ai_analyzer.py`, but new logic should avoid making that file larger.

Enforceable modularization rules:

- After Phase 1 starts, new query-understanding logic must live under `query_understanding/`.
- After Phase 1 starts, new candidate-facts extraction/storage/versioning logic must live under `candidate_facts/`.
- After Phase 2 starts, new hard-filter evaluation orchestration must live under `search/` unless it is a compatibility call into existing evaluator code.
- `ai_analyzer.py` may keep orchestration wrappers, feature flags, and compatibility adapters, but must not own new normalizer schemas, catalogue definitions, candidate-facts schemas, or extraction logic.
- Any pull request adding more than 50 net lines to `ai_analyzer.py` for this initiative must document why the logic could not live in the new modules.
- Deprecation target: once one family is activated from the LLM normalizer, `ai_analyzer.py` should call module APIs only for query understanding; direct family-specific prompt parsing additions are blocked unless they are emergency rollback fixes.

Module ownership:

| Module | Owns |
|---|---|
| `query_understanding/` | Query-plan schema, normalizer prompt, hard-filter catalogue, vocabulary snapshots, legacy adapter, comparison harness. |
| `candidate_facts/` | Candidate-facts schema, extraction contracts, source-specific extractors, facts storage/versioning, evidence provenance. |
| `search/` | Search routing, hard-filter evaluator integration, semantic retrieval orchestration, result assembly. |

---

## 10. Prompt Corpus And Catalogue

NjordHR already has prompt-corpus documentation and bootstrap corpora:

- [prompt-corpus-and-feedback-spec-v0.3.md](/Users/kartikraghavan/Tools/NjordHR/docs/prompt-corpus-and-feedback-spec-v0.3.md)
- [AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json](/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json)

The LLM normalizer should use the corpus in three ways:

1. examples in the LLM normalizer prompt
2. regression tests for normalized JSON output
3. review queue for unsupported/unapplied prompt families

Every failed or unsupported search should log enough information to classify:

- raw prompt
- selected rank
- current parser output
- LLM normalizer output
- applied constraints
- unapplied constraints
- semantic query
- unrecognized residual
- result counts

This becomes the living hard-filter catalogue review loop.

### 10.1 Corpus bias controls

The corpus must not rely only on uncertain matches and parser failures. That would under-sample confident-but-wrong behavior.

The implementation should add the following feedback/audit signals as soon as practical:

- explicit result-level feedback: `wrong_match`, `missing_expected_candidate`, `too_broad`, `too_narrow`, `good_result`
- search-level feedback: `no_results_but_expected_matches`, `unsupported_prompt`, `misunderstood_prompt`
- sampled confident-result review: periodically select verified matches and deterministic failures for manual review
- prompt-to-selected-candidate linkage when a recruiter opens, exports, contacts, or shortlists a candidate from search results

At least one durable outcome signal is mandatory before a family leaves shadow mode. Acceptable durable signals:

- explicit result-level feedback
- prompt-to-action linkage
- adjudicated sampled review performed by a reviewer who did not create the normalizer change

If none of these signals exists, the family must remain in shadow mode.

Launch evidence for LLM-normalized families must include adjudicated review of:

- at least 30 confident passes
- at least 30 deterministic failures
- all UNKNOWN/needs-review outcomes in the sampled window up to a documented cap
- at least 20 hard-negative challenge cases for the family
- at least 20 hard-positive challenge cases for the family

Sampling rule:

- Samples must be selected by a deterministic query or script before manual review begins.
- The sample seed, time window, rank/folder scope, constraint family, and selection query must be recorded with the evidence pack.
- Do not hand-pick examples after seeing outcomes.
- Use stratified random sampling across rank, source type, prompt shape, and result bucket whenever volume allows.
- Prompt-shape strata must include at least: direct requirement, threshold/duration, recent-window, alias/synonym, compound prompt, negation/irrelevant-control where applicable.
- Edge cases are operationally defined as: boundary numeric thresholds, aliases with known ambiguity, unsupported mandatory fragments, mixed hard+semantic prompts, OCR/layout partial extraction, and prompts previously associated with regressions.
- If volume is low, use the most recent eligible prompts plus the defined edge-case set, and label the evidence `low_volume`.
- Include negative controls: prompts that should not normalize to the target family and resumes that should fail the target hard filter.
- Launch evidence must report the denominator: total eligible prompts/results in the sampling window and how many were sampled.

Adjudicated validation set:

- Each family must have a labelled validation set separate from the legacy comparison report.
- The validation set must include expected normalized query-plan output and expected hard-filter outcome for representative resumes/facts.
- It must include hard positives, hard negatives, unsupported mandatory prompts, semantic-only controls, and compound prompts.
- Zero false passes on required constraints is a hard blocker.
- Legacy agreement is a supporting metric only; it is not proof of correctness.

### 10.2 Rollout governance: cold-start and bootstrap corpora

This section is release policy for activating a normalizer family. It is not part of the query-plan or candidate-facts runtime contract.

The 20-real-prompt threshold remains preferred for live families, but rare or newly introduced families need a safe bootstrap path.

A family may enter shadow mode with a labelled bootstrap corpus when all are true:

1. fewer than 20 stored real prompts exist for the family
2. the family is explicitly marked `bootstrap_evidence`, not production corpus evidence
3. synthetic prompts are generated from real recruiter phrasing patterns or domain-owner examples
4. each synthetic prompt has expected normalized JSON
5. production activation is limited to the prompt shapes covered by tests

Bootstrap evidence is shadow-only by default. It may not activate mature production behavior.

Beta activation from bootstrap evidence requires:

- product/developer signoff
- at least 30 bootstrap prompts
- at least 20 real prompts for the family, unless the product owner documents that fewer than 20 real prompts exist after a defined collection window
- real prompts must make up at least 40% of the beta activation prompt set unless the rare-family exception is approved
- expected normalizer output for every bootstrap prompt
- explicit negative examples and hard-negative challenge cases
- manual review of candidate outcomes on a representative resume sample
- visible UI/internal warning unless hidden behind an internal feature flag
- expiry after 30 days or 100 uses, whichever comes first, unless mature activation evidence is completed

A family may not claim mature corpus coverage until it reaches the real-prompt threshold and passes the adjudicated validation set. Written exceptions may extend beta validation for rare business-critical families, but they do not replace the minimum real-world threshold for mature production activation.

---

## 11. Side-By-Side Migration Plan

### Phase 0 - Spec and branch isolation

- create this spec on a separate branch
- no runtime behavior change

### Phase 1 - Extract catalogue from existing code

- define supported hard-filter catalogue from current parser/evaluator behavior
- define JSON schema for normalizer output
- define canonical vocabularies for ship types, engine families, certificates, endorsements, ranks, and documents

### Phase 2 - Legacy adapter and comparison harness

- wrap `_extract_job_constraints` as a legacy query-understanding adapter
- create a comparison report:
  - legacy output
  - LLM-normalized output
  - diff
  - validation errors

Production still uses legacy output.

### Phase 3 - LLM normalizer shadow mode

- call the LLM normalizer in shadow mode
- log output and differences
- do not affect search decisions
- compare against prompt corpus and live prompts

### Phase 4 - Family-by-family activation

- enable LLM-normalized output for one family at a time
- start with low-risk prompt interpretation where deterministic evaluator already exists
- keep legacy fallback on validation failure

Mandatory activation gate for each family:

- at least 100 shadow-mode prompts for common families, or documented low-volume evidence for rare families using the rollout-governance rules
- at least 30 prompts containing the target family after mandatory-marker scan
- at least one durable outcome signal from Section 10.1
- adjudicated validation set completed for the family
- zero false passes on required constraints in adjudicated validation
- hard-negative and hard-positive challenge cases completed
- 100% schema-valid query plans for sampled activation evidence
- zero unresolved `regression` comparison outcomes
- zero unresolved `schema_error` outcomes
- no `catalogue_drift` in the activation evidence pack
- at least 98% `equivalent` or approved `expected_delta` among supported-family comparisons
- all `unsupported_family_delta` outcomes excluded from agreement scoring but included in roadmap counts
- manual review completed for the sampling set defined in Section 10.1
- rollback owner and rollback trigger documented before activation

Legacy agreement is a guardrail for regression detection, not the primary correctness proof. Activation must be blocked by adjudicated validation failure even when legacy agreement is high.

Rollback triggers:

- any confirmed false pass on a required hard constraint
- any mandatory fragment routed to semantic search
- validation error rate above 1% for the activated family
- legacy-vs-LLM regression rate above 2% in live shadow audit after activation
- recruiter report of a common prompt form being blocked or misread, confirmed by review

Candidate first families:

1. passport validity
2. US visa validity
3. recent-contract vessel experience
4. engine experience
5. certificate/endorsement requirements

### Phase 5 - Candidate facts expansion

- increase extraction coverage table by table
- store candidate facts by resume version
- preserve prompt-time raw-text fallback until coverage is proven

---

## 12. External Research To Do

Before implementation, add a bounded decision memo that summarizes how similar systems handle:

- self-query retrieval
- metadata filter generation
- hybrid search
- semantic query plus structured filters
- LLM-generated filters with validation
- RAG over structured and unstructured resume facts

Sources to inspect:

- primary framework docs for LlamaIndex self-query / auto-retriever patterns
- primary framework docs for LangChain SelfQueryRetriever patterns
- primary vector database docs for metadata-filter examples
- RAG metadata-filter generation writeups from primary vendors or well-maintained open-source projects
- resume-screening MCP examples, clearly labelled as reference patterns rather than trusted domain solutions
- relevant GitHub/Hugging Face examples only when they include implementation details that affect this spec

The research should answer one practical question:

> Which parts should we copy as architecture patterns, and which parts are unsafe for NjordHR because they do not enforce maritime evidence rules?

Required deliverable:

- one decision memo, maximum 2 pages
- copied patterns list
- rejected patterns list
- risks introduced by copied patterns
- required spec/code changes, if any
- signoff by the implementation owner before Phase 1 begins

The memo must resolve:

- whether to use an LLM self-query pattern directly or a custom normalizer prompt
- whether vector metadata filters participate in hard filters or only pre-retrieval narrowing
- whether any external MCP/resume-matching tool is in scope for implementation or only benchmarking

---

## 13. Privacy And Retention Requirements

Prompt-normalizer and candidate-facts storage must not ship to production without a documented privacy/retention policy.

Minimum production policy:

- raw prompts retained for at most 90 days by default
- normalized query plans and aggregate metrics may be retained longer if raw prompt text is removed or redacted
- prompt exports must be restricted to implementation/review users
- candidate names, phone numbers, email addresses, passport numbers, visa numbers, and certificate numbers must be redacted from prompt-corpus exports where practical
- every prompt/audit export must include creation date and export owner
- deletion workflow must support removing prompt/audit rows for a tenant/account/date range
- candidate facts must not store full raw PDF text in `facts_json`; use evidence references or separate controlled text/chunk storage

Access model:

| Role | Raw prompts | Normalized query plans | Candidate facts | Evidence snippets/chunks | Audit rows | Exports |
|---|---|---|---|---|---|---|
| `admin` | tenant-scoped read | tenant-scoped read | tenant-scoped read | tenant-scoped read | tenant-scoped read | may approve/export |
| `operator` | own/tenant search history read | tenant-scoped read as needed for UI | candidate-scoped read needed for workflow | snippets shown in UI only | own/tenant operational read | no bulk export by default |
| `reviewer` | tenant-scoped read for approved review windows | tenant-scoped read | redacted facts by default | redacted snippets only | tenant-scoped read | may export redacted review packs |
| `debug_only` | time-boxed access by approval | time-boxed access by approval | time-boxed access by approval | time-boxed access by approval | time-boxed access by approval | no export unless separately approved |

Access rules:

- all access is tenant/account scoped
- raw prompt, evidence snippet, and candidate-facts export require `admin` approval or pre-approved reviewer workflow
- `debug_only` access must have expiry, owner, reason, and audit log
- service jobs may read/write only the tenant/account scope they are executing for
- exports must record role, owner, tenant/account, date range, fields included, redaction mode, and expiry

Candidate-facts governance:

- `candidate_resume_facts` is personal data and must follow the same tenant/account access controls as the underlying resume.
- Facts rows must be deleted or cryptographically/anonymously disassociated when the underlying candidate/resume is deleted according to product policy.
- Facts exports must redact direct identifiers unless the export is explicitly for operational debugging by an authorized user.
- Direct identifiers include candidate name, phone, email, passport number, visa number, certificate number, CDC number, national ID, and address.
- Evidence references should store page/table/field coordinates or short snippets where possible, not full resume text.
- If snippets are stored, they inherit the same retention and deletion policy as candidate facts.
- Candidate facts may be retained as long as the corresponding active resume is retained, plus any legally/business-approved audit window.
- Search-audit references to deleted facts must degrade to metadata-only records that preserve non-identifying counts/reason codes where possible.

Deletion cascade semantics:

- deleting a candidate must delete or anonymize all resumes, candidate facts, evidence snippets, vector chunks, embeddings, cached extraction artifacts, and prompt/search-audit rows that directly identify that candidate
- deleting a resume must delete or anonymize all candidate facts, evidence snippets, vector chunks, embeddings, and cached extraction artifacts derived from that resume
- search-audit rows may retain non-identifying aggregate fields such as constraint ids, decision counts, reason codes, timestamps, and parser versions
- search-audit rows must remove candidate name, filename if identifying, source path, direct identifiers, evidence snippets, resume text, and row ids that can be joined back to deleted candidate facts
- exports created before deletion must be tracked in an export registry with owner, location, fields, expiry, and revocation/deletion status
- out-of-system artifacts require an explicit exception record with owner, reason, expiry, and acknowledgement that they cannot be automatically purged
- deletion jobs must be idempotent and auditable by tenant/account/date/candidate/resume id

Blob deletion eligibility:

Deletion jobs must consume one authoritative deletion-eligibility view/function, not recompute conditions ad hoc. Required output:

```json
{
  "resume_blob_id": "string",
  "eligible_for_physical_delete": "boolean",
  "active_candidate_link_count": "integer",
  "audit_retention_ref_count": "integer",
  "legal_hold_ref_count": "integer",
  "export_replay_ref_count": "integer",
  "blocking_reasons": ["active_candidate_link|audit_retention|legal_hold|export_replay|cache_inconsistent"],
  "computed_at": "ISO-8601 timestamp"
}
```

`resume_blobs.deletion_eligibility_status` may cache this result, but deletion must re-check eligibility immediately before physical purge. If `reference_count_cache` disagrees with active links, deletion is blocked with `cache_inconsistent`.

Deletion contract by data type:

| Data type | Deletion behavior |
|---|---|
| Resume blob/file | Reference-counted. Delete from active storage only when no active candidate links, audit retention requirement, legal hold, or export-replay requirement references it; otherwise mark candidate-specific link deleted/anonymized and keep blob access scoped to remaining references. |
| Candidate resume link | Delete or anonymize candidate linkage. |
| Candidate facts | Delete or anonymize; remove direct identifiers and joinable row ids from retained audits. |
| Evidence snippets/chunks | Delete if derived from deleted resume/candidate; embeddings must also be deleted. |
| Search audit | Retain only non-identifying aggregate metadata if retention is needed. |
| Prompt corpus rows | Delete/anonymize rows containing candidate-identifying text; retain aggregate counts only. |
| Exports | Revoke/delete tracked exports before completing deletion where possible; otherwise record exception. |

Prototype/local development may use a shorter informal policy, but production rollout must document the concrete values for:

- retention window
- access roles
- export path
- redaction method
- deletion owner
- candidate/resume deletion behavior
- evidence-snippet retention behavior

---

## 14. Open Product Decisions

These decisions must not remain implicit during implementation. Defaults below apply until product explicitly changes them.

| Decision | Default for implementation | Blocking? |
|---|---|---|
| Prompt has only required `unapplied_constraints` | Graceful-fail / blocked state. No automatic semantic results. | Not blocking; default is set. |
| Unsupported mandatory requirements in UI | Show blocked/not-enforced message. Do not show verified result list. | Not blocking; default is set. |
| Semantic-only prompts and `needs_review` | Semantic-only prompts produce ranked relevance, not `needs_review`. `needs_review` remains for hard eligibility uncertainty. | Not blocking; default is set. |
| Candidate facts required before Supabase storage | Supabase storage may start with `source`, typed fact buckets, evidence, extraction provenance, presence semantics, and version fields defined in Sections 6-8. Opaque debug fields are allowed only outside deterministic paths. | Blocking for Supabase persistence. |
| Retention policy | Raw prompts max 90 days; candidate facts tied to resume retention plus approved audit window; exports require registry and expiry. | Blocking for production rollout. |
| "Search understood as..." preview | Not required for MVP. If added later, it must display unsupported required constraints as blocked/not enforced. | Not blocking. |

---

## 15. Cross-Boundary Release Readiness Checklist

Production activation is blocked unless all cross-boundary conditions below are true:

- invalid query plans block by default; any legacy rerun is explicit, labelled, audited, and excluded from normalizer evidence
- mandatory-marker fragments cannot enter semantic search or residual handling except as blocked `unapplied_constraints`
- every active hard-filter family has typed query payloads, typed candidate facts, presence semantics, evidence provenance, and missing-facts tests
- candidate evaluation pins `(candidate_id, candidate_resume_id, candidate_resume_facts_id)` or an auditable `transient_facts_id`
- transient fallback facts cannot produce hard-filter `PASS` or `FAIL` unless replayable provenance is captured
- access/export/delete rules cover raw prompts, normalized plans, candidate facts, evidence snippets, chunks, embeddings, audits, and exports
- open product decisions have defaults or are marked blocking before the affected phase
- adjudicated family validation proves correctness with hard positives and hard negatives; legacy agreement is only a regression guardrail
- new implementation logic follows module ownership rules and does not add family-specific prompt/facts logic to `ai_analyzer.py`
- deletion jobs and export revocation are auditable by tenant/account/date/candidate/resume identifiers

If any checklist item is false, the system may continue in spec/shadow/prototype mode only.

---

## 16. Non-Goals

This work does not initially:

- replace deterministic evaluators
- remove the current parser
- require all resumes to be fully parsed before search can run
- require Supabase storage before local validation
- claim that the hard-filter catalogue covers every possible recruiter request
- let the LLM invent new hard-filter types at runtime
