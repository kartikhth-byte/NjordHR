# NjordHR Candidate Intelligence Architecture
## Specification v3.4 — Target Architecture + Phased Rollout

---

> **How to read this document**
>
> Part A (Sections 1–9) is a **target architecture**. It describes where the system should end up. Several sections describe a future state that is not yet safe to build. Read it to understand design intent, not to derive a sprint plan.
>
> Part B (Sections 10–12) is a **phased implementation plan**. Only Phase 1 (Section 10) is an actionable implementation spec. Phases 2 and 3 are design sketches that must not be executed until the preceding phase passes its exit criteria.
>
> **What this document supersedes:** Deterministic Filter Engine Spec v2.1 remains authoritative for filter engine internals. Architecture v3.3 is superseded by this revision.
>
> **What changed from v3.3:**
> - Section 3.3: Rank fuzzy matching explicitly staged — target architecture option, Phase 1 forbidden
> - Section 3.4: "pending" removed from the "absent" trigger; it maps to "unknown"
> - Section 4.2: `age_years` fallback clause removed; cached age is for diagnostics and display only, never hard filter input
> - Section 5.1 / 5.2: Explicit contract added — only fields listed in `applied_constraints` are authoritative for evaluation
> - Section 5.3: "No additional UX logic" claim softened
> - Section 6: Endorsement type-shape contract made explicit once
> - Section 8.2: "Better candidates" metric defined as recruiter override rate
> - Section 10.1: Synchronous re-extraction framed as a correctness trade-off, not only a latency safeguard
> - Section 10.5: "Recruiters see a consistent result list" replaced with honest best-effort wording
> - Section 10.7: RANK_MATCH demotion is a pre-launch gate, not a post-deploy soft demotion

---

## Table of Contents

**Part A — Target Architecture**

1. [Design Principles](#1-design-principles)
2. [Pipeline Overview](#2-pipeline-overview)
3. [Extraction Hierarchy](#3-extraction-hierarchy)
4. [Canonical Candidate Schema](#4-canonical-candidate-schema)
5. [JobConstraints Schema](#5-jobconstraints-schema)
6. [Deterministic Hard Filter](#6-deterministic-hard-filter)
7. [Heuristic Ranking Layer](#7-heuristic-ranking-layer)
8. [LLM Reasoning Layer](#8-llm-reasoning-layer)
9. [Signals Computation](#9-signals-computation)

**Part B — Phased Implementation**

10. [Phase 1 — v3-core](#10-phase-1--v3-core)
11. [Phase 2 — v3-ranking](#11-phase-2--v3-ranking)
12. [Phase 3 — v3-indexed-facts](#12-phase-3--v3-indexed-facts)

**Appendix**

13. [Regression Protection](#13-regression-protection)
14. [What Not To Do](#14-what-not-to-do)

---

# Part A — Target Architecture

---

## 1. Design Principles

### 1.1 Layers are independent, not interleaved

The system has six conceptual layers. Each layer has a single responsibility, a defined input contract, and a defined output contract. No layer calls another layer's internal functions. Dependencies flow in one direction: downward.

### 1.2 Facts before scores

No score is computed until input facts are extracted and confidence-evaluated. Every scoring component is confidence-dampened — if the input fact is below its field threshold, the component contributes zero to the score and the output carries a `score_incomplete` flag.

### 1.3 UNKNOWN does not equal a penalty

A candidate whose availability is unknown is not a bad candidate — their availability is simply unconfirmed. UNKNOWN fields do not contribute to a score or filter decision; they do not subtract from one. Penalising UNKNOWN conflates missing evidence with negative evidence.

### 1.4 Deterministic parsing before prompt extraction

For any field with a predictable labeled structure, deterministic regex-based parsing is the primary extraction method. Prompt extraction is used only for fields that genuinely require layout understanding or narrative comprehension. The DOB parsing bug history is the standing evidence for why this ordering matters.

### 1.5 Raw resume text fallback is preserved until extraction quality is proven

The target state is that downstream layers read only from CandidateFacts. That is not the current state and must not be enforced as a current rule. For Phase 1 and Phase 2, query-time raw-text fallback remains available. Removing it is a Phase 3 decision.

### 1.6 The ranking layer is a heuristic, not an intelligence score

The soft scorer assigns weights and bonuses to candidate attributes. These weights are domain-informed initial guesses, not empirically calibrated values. The system must label them as such everywhere they appear. Validation against recruiter judgments is a Phase 2 precondition.

### 1.7 Schema evolution is additive

New fields may be added to CandidateFacts at any time. Existing field names and nesting paths must not be renamed or moved. Breaking changes require a MAJOR version increment and a controlled re-extraction migration.

### 1.8 Index-time pre-computation is an optimization, not a foundation

Pre-computing CandidateFacts at index time is desirable for performance and pre-retrieval filtering. It is also the riskiest change in this roadmap. It must not be attempted until Phase 3.

### 1.9 Conservative extractors prefer UNKNOWN over false

When evidence is absent or ambiguous, extractors return UNKNOWN rather than false. Absence of a certificate in resume text does not prove the candidate does not hold it. False negatives from over-aggressive absence detection are worse than UNKNOWN outcomes routed to human review.

### 1.10 Derived values are cached views, not authoritative stored truth

Fields like `age_years` are computed from primary facts (DOB) and must not be treated as authoritative records. A stored `age_years` value is a cache valid only at the time it was computed. The DOB is the source of truth. If DOB is unavailable, the age evaluation returns UNKNOWN — stored `age_years` does not substitute for it under any circumstances.

---

## 2. Pipeline Overview

```
Raw Resume / PDF / Text
        │
        ▼
┌──────────────────────────────┐
│  Extraction                  │
│  Deterministic parsers first │  ◄── Raw text accessed here only
│  Prompt extraction for hard  │
│  fields only                 │
│  Query-time or index-time    │  ◄── Both modes supported through Phase 2
└──────────────────────────────┘
        │
        ▼
┌──────────────────────────────┐
│  Normalization               │
│  Rank alias resolution       │
│  Vessel type canonicalization│
│  Conflict detection          │
│  Staged confidence           │
└──────────────────────────────┘
        │
        ▼
┌──────────────────────────────┐
│  Signals Computation         │  ◄── Derived from normalized facts only
│  Internal diagnostics only   │      until Phase 2 quality is proven
└──────────────────────────────┘
        │
        ▼
  CandidateFacts record
  (versioned, immutable once written)
        │
        │         ┌────────────────────────────────────┐
        │         │  Constraint Extraction              │
        │         │  Phase 1: regex-based               │
        │         │  Phase 2+: LLM + schema validation  │
        │         │  + deterministic post-normalization │
        │         │  + user-visible preview (Phase 2)   │
        │         └────────────────────────────────────┘
        │                       │
        ▼                       ▼
┌──────────────────────────────────────────────────────┐
│  Deterministic Hard Filter                           │
│  CandidateFacts + JobConstraints → PASS/FAIL/UNKNOWN │
│  Pure logic. No LLM. No probability.                 │
│  Reads only from applied_constraints                 │
└──────────────────────────────────────────────────────┘
        │
     ┌──┴───────────┐
   PASS          FAIL / UNKNOWN
     │                 │
     ▼            stop, or queue for human review
┌──────────────────────────────┐
│  Heuristic Ranking Layer     │
│  (NOT an intelligence score) │
│  Confidence-dampened         │
│  Score breakdowns surfaced   │
│  Phase 2+                    │
└──────────────────────────────┘
        │
        ▼ (top N by score + K score-incomplete candidates)
┌──────────────────────────────┐
│  LLM Reasoning               │
│  Bounded candidate count     │
│  Reads CandidateFacts only   │
└──────────────────────────────┘
        │
        ▼
   Ranked results with score breakdowns
```

### 2.1 Layer contract summary

| Layer | Inputs | Output | Uses LLM? | Runs per |
|---|---|---|---|---|
| Extraction | Raw resume text | Raw field dict + confidence | Hard fields only | Candidate |
| Normalization | Raw fields | Normalized CandidateFacts | No | Candidate |
| Signals | Normalized facts | Derived analytics | Optional | Candidate |
| Constraint extraction | NL query string | Validated JobConstraints | Phase 2+: Yes | Query |
| Hard filter | CandidateFacts + `applied_constraints` only | PASS / FAIL / UNKNOWN + audit | No | Candidate × Query |
| Heuristic ranking | CandidateFacts + JobConstraints | Score + breakdown | No | PASS candidates × Query |
| LLM reasoning | Top N CandidateFacts + constraints | Narrative + highlights | Yes | Top N × Query |

---

## 3. Extraction Hierarchy

### 3.1 Method hierarchy

1. **Labeled-field deterministic regex** — fields with predictable labeled formats. Cannot hallucinate. Either finds the value or returns MISSING.
2. **Structured table parsing** — sea service records and certification tables. Rule-based date arithmetic. Still deterministic.
3. **Prompt extraction with structured output** — for fields requiring layout understanding or narrative comprehension. Always followed by post-processing validation.
4. **Inference / imputation** — for derived fields only. Never used for primary facts that drive hard filter decisions.

### 3.2 Field method assignment

| Field | Primary method | Fallback | Notes |
|---|---|---|---|
| DOB | Labeled-field regex | None | DD-Mon-YYYY and ISO only. No unsafe fallback. |
| Stated age | Labeled-field regex + disqualifying prefix check | None | Vessel/ship/engine age must not match. |
| Full name | Labeled-field regex | Prompt | |
| Nationality | Labeled-field regex | Prompt | |
| Current rank | Labeled-field regex + alias table | Prompt | See Section 3.3. |
| Applied rank | Labeled-field regex + alias table | Prompt | |
| COC grade / expiry | Labeled-field regex | Prompt | Safety-critical. Deterministic preferred. |
| STCW certificates | Keyword regex per certificate | Prompt | See Section 3.4 for state semantics. |
| Endorsements (tanker, DP, GMDSS) | Keyword regex | Prompt | Same state semantics as STCW. |
| Passport expiry | Labeled-field regex | Prompt | |
| US visa status / expiry | Labeled-field regex | Prompt | |
| Availability date | Labeled-field regex + "immediately" keyword | Prompt | |
| Total sea service months | Structured table parsing → date arithmetic | Prompt | Phase 2+. |
| Sea service by vessel type | Structured table parsing | Prompt | Phase 2+. |
| Sea service by rank | Structured table parsing | Prompt | Phase 2+. |
| Last sign-off date | Labeled-field regex | Prompt from table | Phase 2+. |
| Language quality | LLM classification | 0.5 default | Phase 2+. |

### 3.3 Rank alias table — Phase 1 policy and target architecture

**Phase 1 policy (current):** the alias table is a bounded, explicit mapping. If a string is not in the table, the result is UNKNOWN. No fuzzy matching of any kind is performed in Phase 1.

**Target architecture (deferred):** once Phase 1 alias table coverage has been measured against a real resume sample, constrained fuzzy matching may be introduced as a later enhancement under the following rules: matching is permitted only within the same department family (deck abbreviations must not resolve to engine ranks and vice versa); abbreviations are explicit alias entries only and are never handled by edit-distance; any fuzzy match below 0.85 confidence returns UNKNOWN. This option is not implemented in Phase 1 and must not be added until alias table coverage is assessed.

The distinction matters: Section 3.3 describes the target-architecture option; Phase 1 implementation follows the Phase 1 policy only.

### 3.4 Certificate extraction state semantics

The four states for STCW certificates and endorsements:

| State | Meaning | Trigger conditions |
|---|---|---|
| `present` | Certificate confirmed held and in date | Certificate name found with a valid expiry date, or listed in a "held certificates" section without explicit validity concern |
| `expired` | Certificate confirmed held but out of date | Expiry date extracted and confirmed < evaluation date, or text contains "expired" adjacent to the certificate name |
| `absent` | Certificate explicitly stated as not held | Text contains "No [cert name]", "[cert name] N/A", a checklist with an explicit blank or cross for that certificate |
| `unknown` | Insufficient evidence to determine state | Certificate not mentioned; mentioned in an ambiguous context; or status described as "pending", "in progress", "applied for", or similar |

**"Pending" maps to `unknown`, not `absent`.** A candidate with "[cert name] pending" has indicated a certification is in process. That is not the same as not holding it. Mapping pending to absent could fail a candidate whose paperwork is in progress. The filter treats `unknown` conservatively — it becomes UNKNOWN in the tri-state evaluation, which routes the candidate to human review rather than disqualifying them.

"Explicitly absent" means the resume text literally negates possession: "No GMDSS", a checklist with an explicit X or blank cell where the table structure is known to represent holdings. Silence and omission are not absence — they are unknown.

`stcw_basic_all_valid` is a derived tri-state field computed from the four per-certificate states above. It returns native boolean `true` only when all four core STCW certificates are in `present` state. It returns native boolean `false` when at least one is in `expired` or `absent` state. It returns `null` in all other cases, including when any certificate is in `unknown` state (which includes pending). The stored field is therefore tri-state (`true` / `false` / `null`), not a stringified boolean.

### 3.5 Confidence assignment

| Method | Confidence range |
|---|---|
| Labeled-field regex, canonical format | 0.93–1.0 |
| Labeled-field regex, variant format + normalization | 0.85–0.92 |
| Structured table parsing | 0.70–0.85 |
| Prompt extraction of structured layout | 0.65–0.80 |
| Prompt extraction of narrative text | 0.55–0.70 |
| Inference / imputation | 0.50–0.60 |
| Below 0.50 | Discard — treat as MISSING |

### 3.6 Per-field extraction contract

```python
{
    "value":             <extracted value or None>,
    "status":            "PARSED" | "MISSING" | "AMBIGUOUS",
    "confidence":        float,        # 0.0–1.0
    "extraction_method": str,
    "source_label":      str | None,
    "raw_text_fragment": str | None    # for audit
}
```

---

## 4. Canonical Candidate Schema

### 4.1 Schema maturity tiers

**Tier 1 — Core (hard filter inputs).** Drive binary eligibility decisions. Phase 1 target.

**Tier 2 — Extended (soft scorer inputs).** Contribute to ranking but do not block candidates. Phase 2 target.

**Tier 3 — Signals (derived analytics).** Computed from facts. Internal diagnostics only until Phase 2 quality is proven.

### 4.2 Tier 1 — Core fields (Phase 1 target)

```json
{
  "candidate_id":   "cand_abc123",
  "facts_version":  "2.0",
  "extracted_at":   "2026-04-03T10:00:00Z",
  "source_document": {
    "filename": "2nd_Engineer_315781.pdf",
    "hash":     "sha256:abc123..."
  },

  "identity": {
    "full_name":   "Jose Santos",
    "dob":         "1988-11-04",
    "nationality": "Filipino"
  },

  "role": {
    "current_rank":            "Second Engineer",
    "current_rank_normalized": "2nd_engineer",
    "applied_rank":            "Second Engineer",
    "applied_rank_normalized": "2nd_engineer",
    "department":              "engine",
    "seniority_bucket":        "senior_officer"
  },

  "certifications": {
    "coc": {
      "grade":       "Second Engineer",
      "expiry_date": "2028-03-01",
      "status":      "valid"
    },
    "stcw_basic_all_valid": true,
    "endorsements": {
      "tanker_oil":      "unknown",
      "tanker_chemical": "unknown",
      "tanker_gas":      "unknown",
      "dp_operational":  "unknown",
      "gmdss":           "unknown"
    }
  },

  "logistics": {
    "passport_valid":  true,
    "passport_expiry": "2030-05-01",
    "us_visa_valid":   true,
    "us_visa_status":  "B1/B2",
    "us_visa_expiry":  "2028-01-01"
  },

  "derived": {
    "age_years":         37,
    "age_computed_at":   "2026-04-03",
    "age_is_cached":     true,
    "dob_confidence":    0.98,
    "stated_age":        null,
    "stated_age_status": "MISSING"
  },

  "fact_meta": {
    "identity.dob": {
      "confidence":        0.98,
      "extraction_method": "labeled_field_regex",
      "source_label":      "Date of Birth",
      "status":            "PARSED"
    },
    "role.current_rank_normalized": {
      "confidence":        0.95,
      "extraction_method": "alias_lookup",
      "status":            "PARSED"
    },
    "certifications.coc": {
      "confidence":        0.92,
      "extraction_method": "labeled_field_regex",
      "status":            "PARSED"
    },
    "logistics.us_visa_valid": {
      "confidence":        0.95,
      "extraction_method": "labeled_field_regex",
      "status":            "PARSED"
    }
  }
}
```

**`derived.age_is_cached: true`** marks `age_years` as a cached computed value. The filter engine always recomputes age from `identity.dob` at evaluation time. Stored `age_years` is used for diagnostics and display only — it must never substitute for DOB-based computation in the filter, scorer, or any eligibility decision. If `identity.dob` is absent, the age evaluation returns UNKNOWN. There is no fallback to stored `age_years`.

**Endorsement states** use string values from the four-state vocabulary ("present" / "expired" / "absent" / "unknown") rather than booleans. This preserves the full state information in the stored record. The filter engine derives boolean gate values from these strings at evaluation time (see Section 6.1).

**`stcw_basic_all_valid`** is not a four-state stored string. It is a derived tri-state field stored as native `true`, native `false`, or `null`. It is computed from the four-state per-certificate extraction results described in Section 3.4. This avoids the implementation hazards of stringified booleans while still preserving conservative UNKNOWN behavior.

### 4.3 Tier 2 — Extended fields (Phase 2 target)

```json
{
  "experience": {
    "total_sea_service_months":     84,
    "sea_service_by_vessel_type": {
      "bulk_carrier": 48,
      "container":    36
    },
    "sea_service_by_rank": {
      "2nd_engineer": 36,
      "3rd_engineer": 48
    },
    "last_vessel_type_normalized":  "bulk_carrier",
    "last_sign_off_date":           "2025-10-15",
    "last_sign_off_months_ago":     6
  },

  "identity_extended": {
    "current_location_country": "Philippines",
    "contact_email":            "jose.santos@email.com"
  },

  "logistics_extended": {
    "availability_date":      "2026-01-15",
    "availability_status":    "available",
    "salary_expectation_usd": 4500
  }
}
```

`sea_service_by_company` is excluded. Reliable company name normalization requires a dedicated registry and is deferred.

### 4.4 Tier 3 — Signals fields

```json
{
  "signals": {
    "extractable_profile_completeness_score":   0.78,
    "extractable_profile_completeness_missing": ["certifications.coc", "logistics.us_visa_valid"],
    "career_stability_score":    null,
    "short_stint_count":         null,
    "gap_count":                 null,
    "longest_gap_months":        null,
    "promotion_path_valid":      null,
    "language_quality_score":    null
  }
}
```

`extractable_profile_completeness_score` is an extractor coverage metric, not a candidate quality metric. A strong candidate with a badly formatted resume may score low. Any surface displaying this value must make that explicit. Null signal fields indicate the relevant Tier 2 source data is not yet available.

### 4.5 Field confidence thresholds

| Field | Minimum confidence to use | Used in |
|---|---|---|
| DOB / age | 0.90 | Hard filter |
| Current rank (normalized) | 0.85 | Hard filter + scorer |
| COC validity | 0.85 | Hard filter (document gate) |
| STCW endorsements | 0.80 | Hard filter |
| US visa status | 0.90 | Hard filter |
| Total sea service months | 0.70 | Hard filter (if constrained) + scorer |
| Sea service by vessel type | 0.65 | Hard filter (if constrained) + scorer |
| Availability date | 0.75 | Scorer only |
| Salary expectation | 0.70 | Scorer only |

### 4.6 Schema versioning policy

`facts_version` uses `MAJOR.MINOR` format. MAJOR changes require re-extraction. MINOR changes are additive only. Current target: `2.0`.

---

## 5. JobConstraints Schema

### 5.1 Schema and evaluation contract

```json
{
  "constraints_version": "1.0",
  "extracted_at":        "2026-04-03T10:05:00Z",
  "raw_query":           "2nd engineers, 7+ years on bulk carriers, valid COC required",

  "hard_constraints": {
    "rank": {
      "applied_rank_normalized": ["2nd_engineer"],
      "operator": "contains_any"
    },
    "age": {
      "min_years": null,
      "max_years": null
    },
    "sea_service": {
      "min_total_months": 84,
      "operator": "gte"
    },
    "vessel_type": {
      "required": ["bulk_carrier"],
      "operator": "contains_any"
    },
    "certifications": {
      "coc_required":          true,
      "coc_valid_required":    true,
      "endorsements_required": []
    },
    "visa": {
      "us_visa_required":  false,
      "us_visa_preferred": false
    }
  },

  "soft_preferences": {
    "vessel_types_preferred":   ["bulk_carrier"],
    "availability_before_date": null,
    "max_salary_usd":           null
  },

  "applied_constraints":   ["rank_match", "coc_document_gate"],
  "unapplied_constraints": ["min_sea_service", "vessel_type"],
  "extraction_notes": [
    "sea service: '7+ years' parsed but not applied in Phase 1",
    "vessel type: 'bulk carriers' parsed but not applied in Phase 1"
  ]
}
```

**Evaluation contract:** only rule-family IDs named in `applied_constraints` are authoritative for filter evaluation. These IDs are stable activation keys, not schema paths. In Phase 1 and Phase 2 the canonical IDs are: `age_range`, `us_visa`, `rank_match`, `coc_document_gate`, `stcw_basic`, `min_sea_service`, and `vessel_type`. Fields present in `hard_constraints` but absent from `applied_constraints` are parsed informational data only. The filter engine must not read from `hard_constraints` directly to decide activation — it reads `applied_constraints` first, then reads the corresponding field within `hard_constraints` for parameter values. This contract prevents Phase 1 code from accidentally activating Phase 2 rules because a parsed field happens to be non-null.

### 5.2 Constraint extraction rules

- Age ranges expressed as "between X and Y" extract as `{min_years: X, max_years: Y}`. Not compound AND queries.
- "Preferred" or "preferably" moves a constraint to `soft_preferences`.
- Vessel type and rank strings pass through the alias tables. Unrecognized strings → `null`.
- If a constraint cannot be reliably parsed, the field is `null`.

### 5.3 Mixed-support query UX

When a query contains supported and unsupported constraint types, the system must display a structured constraint summary:

```
Applied filters:   Rank (2nd Engineer), COC (valid, required)
Not applied:       Sea service (7+ years), Vessel type (Bulk Carrier)
                   These criteria will be available in a future update.
```

This summary is shown prominently on the results page. It is driven by the `applied_constraints` and `unapplied_constraints` fields in JobConstraints, which reduces the amount of UX-specific logic needed — though human-readable labels, ordering, and formatting still require presentation work on the product side.

### 5.4 User-visible constraint preview (Phase 2)

Showing users the extracted constraints before a search runs is the right mechanism for catching LLM extraction drift. The interaction model (read-only preview vs editable constraints) and the backend contract for overrides must be designed before Phase 2 LLM constraint extraction is deployed.

### 5.5 Validation before use

JobConstraints do not enter the filter engine without passing schema validation. A validation failure falls back to vector search without hard filtering, with a visible warning.

---

## 6. Deterministic Hard Filter

### 6.1 Endorsement type-shape contract

Stored endorsement fields (Section 4.2) use four-state strings. The filter engine derives evaluation values from these strings as follows:

- `"present"` → evaluates as `true` for `eq(true)` rules
- `"expired"` → evaluates as `false` for `eq(true)` rules (candidate had it, no longer valid)
- `"absent"` → evaluates as `false` for `eq(true)` rules
- `"unknown"` → evaluates as UNKNOWN (neither PASS nor FAIL — routes to human review)

This derivation happens at evaluation time inside the rule function. The stored record retains the full four-state string. No rule function should read a raw boolean endorsement value from the stored record.

### 6.2 Phase 1 rule set

The filter engine activates a rule only when its constraint family is listed in `applied_constraints`. It does not read directly from `hard_constraints`.

| Rule ID | Field | Operator | FAIL condition | UNKNOWN condition |
|---|---|---|---|---|
| AGE_RANGE | recomputed_age_years_from(identity.dob) | between_inclusive | age outside [min, max] | dob_confidence < 0.90 or DATA_CONFLICT |
| RANK_MATCH | role.applied_rank_normalized | contains_any | rank not in required set | confidence < 0.85 |
| COC_DOCUMENT_GATE | certifications.coc.status | eq("valid") | COC absent or expired when required | confidence < 0.85 |
| STCW_BASIC | certifications.stcw_basic_all_valid | eq(true) | stcw_basic_all_valid is false when required | value is null or confidence < 0.80 |
| US_VISA | logistics.us_visa_valid | eq(true) | visa absent or expired when required | confidence < 0.90 |

Phase 2 additions (inactive in Phase 1, not in `applied_constraints`):

| Rule ID | Notes |
|---|---|
| MIN_SEA_SERVICE | Requires Tier 2 extraction |
| VESSEL_TYPE | Requires Tier 2 extraction |

### 6.3 COC_DOCUMENT_GATE scope

`COC_DOCUMENT_GATE` checks only: is a COC present, and is it not expired. It does not check grade appropriateness, issuing authority, or flag state endorsement. The rule ID name is intentional — "document gate" signals a document-level presence and validity check. Audit output and any UI referencing this rule must use the full name.

### 6.4 STCW conservative policy

The STCW_BASIC rule evaluates as UNKNOWN — not FAIL — when `stcw_basic_all_valid` is `null` or confidence is below 0.80. A candidate whose STCW section was not found, or whose certificates are pending, routes to human review, not disqualification.

### 6.5 Filter output per candidate

```json
{
  "candidate_id":    "cand_abc123",
  "decision":        "PASS",
  "facts_version":   "2.0",
  "evaluation_date": "2026-04-03",
  "rules_evaluated": [
    {
      "rule_id":        "AGE_RANGE",
      "decision":       "PASS",
      "actual_value":   37,
      "expected_value": {"min": 30, "max": 50},
      "confidence":     0.98
    },
    {
      "rule_id":        "RANK_MATCH",
      "decision":       "PASS",
      "actual_value":   "2nd_engineer",
      "expected_value": ["2nd_engineer"],
      "confidence":     0.95
    }
  ]
}
```

---

## 7. Heuristic Ranking Layer

> **Label requirement.** Every UI surface displaying a score or ranking from this layer must include: *"Heuristic ranking — see breakdown for details. Not an objective score."* Score breakdowns must always be visible alongside the final score.

### 7.1 Formula

```
heuristic_score =
    (0.25 × rank_relevance)
  + (0.20 × sea_service)
  + (0.20 × vessel_type)
  + (0.15 × certification)
  + (0.10 × recency)
  + (0.10 × availability)
  + bonus_total
  - penalty_total
```

Weights sum to 1.0. Bonuses capped at +0.10. Penalties floored at −0.10. Final score clamped to [0.0, 1.0].

### 7.2 Component definitions

**rank_relevance**
- `1.0` — current rank = target rank
- `0.80` — one step below (promotion candidate)
- `0.40` — two steps below
- `0.0` — above target, wrong department, or UNKNOWN

If confidence < 0.85 → component = 0.0, `score_incomplete: true`.

**sea_service** *(Tier 2 only)*
```
sea_service = min(1.0, actual_months / max(required_months, 36))
```

**vessel_type** *(Tier 2 only)*
```
vessel_type = min(1.0, relevant_months / max(total_sea_service_months, 1))
```

**certification**
- COC present + valid: 0.50 / COC present + expired: 0.25
- STCW basic all valid: 0.30
- Required endorsements present: distributed across remaining 0.20

**recency** *(Tier 2 only — zero if absent, no penalty)*
- ≤ 3 months: 1.00 / ≤ 6 months: 0.85 / ≤ 12 months: 0.70
- ≤ 24 months: 0.50 / ≤ 48 months: 0.25 / > 48 months: 0.10 / UNKNOWN: 0.0

**availability** *(Tier 2 only — zero if absent, no penalty)*
- ≤ 30 days: 1.0 / 31–90 days: 0.75 / 91–180 days: 0.50 / > 180 days: 0.25 / UNKNOWN: 0.0

### 7.3 Phase 2 bonuses and penalties

| Bonus | Condition | Value |
|---|---|---|
| Specialisation | ≥ 60% of sea service on required vessel type | +0.05 |
| Promotion candidate | Applied rank one step above most recent rank | +0.03 |

| Penalty | Condition | Value |
|---|---|---|
| Rank above target | Current rank is senior to applied rank | −0.05 |
| COC absent for senior rank | No COC for Chief Mate or above | −0.04 |

Signals-based bonuses/penalties excluded until signal quality has been assessed.

### 7.4 Scorer output

```json
{
  "candidate_id":    "cand_abc123",
  "heuristic_score": 0.74,
  "score_incomplete": false,
  "label":           "Heuristic ranking — see breakdown. Not an objective score.",
  "components": {
    "rank_relevance": {"score": 1.0,  "confidence": 0.95, "weight": 0.25},
    "sea_service":    {"score": 0.90, "confidence": 0.80, "weight": 0.20},
    "vessel_type":    {"score": 0.70, "confidence": 0.72, "weight": 0.20},
    "certification":  {"score": 0.85, "confidence": 0.92, "weight": 0.15},
    "recency":        {"score": 0.85, "confidence": 1.0,  "weight": 0.10},
    "availability":   {"score": 0.0,  "confidence": 0.0,  "weight": 0.10}
  },
  "bonuses_applied":   ["specialisation"],
  "penalties_applied": [],
  "bonus_total":       0.05,
  "penalty_total":     0.0
}
```

---

## 8. LLM Reasoning Layer

### 8.1 Candidate selection

The LLM receives:
- Top N candidates ranked by heuristic score. Default N = 5, maximum N = 10.
- Up to K additional candidates who passed the hard filter but have `score_incomplete: true` on rank or certification. Default K = 2.

### 8.2 Shortlist monitoring

Monitoring triggers — run a broader comparison (LLM on top N+5 PASS candidates) when:
- A search uses a Tier 2 field deployed within the last 30 days
- A search returns a `score_incomplete: true` rate above 30% of PASS candidates

**Defining "better candidates":** the primary metric for deciding whether the broader set is outperforming the top-N set is recruiter override rate — if recruiters are consistently selecting candidates from the broader set in preference to top-N candidates, the shortlist is too aggressive. Secondary metric: recruiter-reported LLM narrative quality on a 1–3 scale. If the broader-set candidates consistently receive higher narrative quality ratings, N should be increased. Review after the first 50 triggered comparisons in Phase 2 production.

### 8.3 What the LLM is given

The LLM prompt is built from CandidateFacts, not raw resume text. Exception: during Phase 1 and Phase 2, raw resume text may be included as a supplementary context block when a candidate has significant `score_incomplete` flags.

### 8.4 What the LLM is asked to do

- Highlight strongest fit factors for the specific query.
- Identify risks or gaps not captured by the structured filter.
- Note inconsistency between narrative and structured facts.

### 8.5 What the LLM is not asked to do

- Re-evaluate hard constraints already decided by the filter.
- Produce a numeric score.
- Process candidates who FAILed the hard filter.

---

## 9. Signals Computation

Signals are derived from normalized facts. Not extracted from resume text. All signal fields carry `derived: true` in the output.

### 9.1 Readiness classification

| Signal | Source fields | Phase readiness | Recruiter-facing? |
|---|---|---|---|
| extractable_profile_completeness_score | Tier 1 fields | Phase 1 | Internal only |
| career_stability_score | Sea service table (Tier 2) | Phase 2+ | After validation only |
| gap_count / longest_gap_months | Sea service dates (Tier 2) | Phase 2+ | After validation only |
| promotion_path_valid | Sea service by rank (Tier 2) | Phase 2+ | After validation only |
| language_quality_score | LLM classification (optional) | Phase 2+ | After validation only |

### 9.2 Data quality caution

Real service records frequently have incomplete tables, month/year-only dates, overlapping voyages, and non-sea-side roles interleaved. Gap counts and stability scores from such data will be noisy. Any signal with a high UNCERTAIN rate on real resume data must not be surfaced to recruiters regardless of Phase 2 timeline.

---

# Part B — Phased Implementation

---

## 10. Phase 1 — v3-core

### 10.1 Scope and regression statement

Phase 1 adds new extraction, normalization, and filter capabilities. The precise regression requirement is:

- **Existing age filter behaviour must not change.** The AGE_RANGE rule, DOB parsing, stated age extraction, conflict detection, and all 22 age/DOB tests must produce identical results after every Phase 1 commit.
- **Existing US visa filter behaviour must not change.** The US_VISA rule and its evaluation path are untouched.
- **New rule families are additive.** RANK_MATCH, COC_DOCUMENT_GATE, and STCW_BASIC activate only when their rule-family ID appears in `applied_constraints`. They have no effect on searches that do not include the corresponding supported rank, COC, or STCW basic constraint type.

The phrase "all existing behaviour unchanged" must not be used as a regression statement. It is imprecise, and Phase 1 intentionally changes behaviour for rank-constrained queries, COC-constrained queries, mixed-version candidate handling, and unsupported-constraint warnings.

### 10.2 Out of scope for Phase 1

- Heuristic ranking layer
- Sea service table extraction
- Availability and salary extraction
- All signals except `extractable_profile_completeness_score`
- LLM constraint extraction
- Index-time pre-computation
- Constraint preview UI
- Rank fuzzy matching

### 10.3 Phase 1 query semantics

Supported constraint types — these activate hard filter rules in Phase 1:
- Age range (existing): "must be between 30 and 50", "ages 35 to 45"
- US visa (existing): "must have US visa", "B1/B2 required"
- Rank (new): "2nd engineers", "chief officers", "3rd engineer only"
- COC document (new): "must hold valid COC", "valid certificate of competency required"
- STCW basic validity (new): "valid STCW basic required", "must hold all basic STCW certificates"

Parsed but not applied in Phase 1 — these are stored in `unapplied_constraints` and shown in the constraint summary, but do not activate filter rules:
- Minimum sea service
- Vessel type requirements
- STCW endorsement requirements (tanker / DP / GMDSS specifics)
- Availability constraints

When an unsupported constraint type is detected, it is stored in `unapplied_constraints` and surfaced in the structured constraint summary (Section 5.3). The system must not silently skip it.

### 10.4 Implementation sequence

Each item is a separate commit. The full existing test suite must pass after every commit.

**Commit 1 — Schema stubs**
Add `identity`, `role`, `certifications`, `logistics` as null stubs. Add `age_is_cached: true` to `derived`. Bump `FACTS_VERSION` to `"2.0"`. No logic changes. All 22 existing tests pass without modification.

**Commit 2 — Rank alias table and normalization**
Add `RANK_ALIAS_TABLE` (explicit entries only — no fuzzy matching). Add `_normalize_rank(raw_rank)` → `(canonical_id, department, seniority_bucket, confidence)`. Add `_extract_rank_fact_from_text(text)`. Populate `role.*`.
Tests: `tests/test_ai_analyzer_rank_normalization.py` — exact alias match, variant alias, unrecognized rank → UNKNOWN, deck vs engine department, abbreviation as explicit alias.

**Commit 3 — COC extraction**
Add `_extract_coc_fact_from_text(text)`. Compute `coc.status` against evaluation date. Missing expiry → status "unknown", not "valid".
Tests: `tests/test_ai_analyzer_certifications.py` — valid COC, expired COC, COC absent, missing expiry → unknown, expiry format variants.

**Commit 4 — STCW and endorsements extraction**
Add `_extract_stcw_fact_from_text(text)` and `_extract_endorsements_from_text(text)`. Apply four-state semantics from Section 3.4. "Pending" → unknown. `stcw_basic_all_valid` logic follows Section 3.4 and stores native `true` / `false` / `null`.
Tests (extend `tests/test_ai_analyzer_certifications.py`): all STCW present → `true`, one expired → `false`, one absent explicitly → `false`, one pending → `null`, one not mentioned → `null`, STCW section entirely absent → `null`.

**Commit 5 — Logistics extraction**
Add `_extract_logistics_from_text(text)`. Absorb any existing visa extraction logic. Compute validity flags against evaluation date.
Tests: `tests/test_ai_analyzer_logistics.py` — valid visa, expired visa, visa absent, passport expiry variants, passport absent.

**Commit 6 — Extend hard filter rules**
Add `_evaluate_rank_rule(facts, constraints)`, `_evaluate_coc_document_gate(facts, constraints)`, and `_evaluate_stcw_basic_rule(facts, constraints)`. Wire into `_evaluate_hard_filters`. All three rules read their activation from `applied_constraints` only. None reads directly from `hard_constraints` to decide whether it should run.
Tests: rank match, rank mismatch, rank UNKNOWN, COC required + valid, COC required + expired, COC required + absent, COC not in applied_constraints (rule skipped), STCW required + `true`, STCW required + `null` → UNKNOWN not FAIL.

**Commit 7 — Mixed-support constraint summary**
Add `applied_constraints` / `unapplied_constraints` population to constraint extraction. Pipe both fields through to stream events for the frontend structured summary.
Tests: age + rank query → `age_range` and `rank_match` in applied, age + sea_service query → `min_sea_service` in unapplied, all-unsupported query → applied empty, all filters skip.

**Commit 8 — Extractable profile completeness signal**
Add `_compute_extractable_profile_completeness(facts)` → `(score, missing_fields)`. Tier 1 fields only.
Tests: full Tier 1 extraction, partial (several fields UNKNOWN), near-empty record.

**Commit 9 — Regression validation**
Run validated baseline: folder `/Users/kartikraghavan/temp12/2nd_Engineer`, query "should be within the ages of 30 and 50 years old". Confirm both expected PDFs match. Run full test suite. Record results in commit message.

### 10.5 Dual-version operations

**Where `facts_version` lives.** Persisted in the stored CandidateFacts record. Retrievable on every read. Not a runtime-only field.

**Routing.** Filter engine checks `facts_version` before evaluation:
- `1.1` records: evaluate AGE_RANGE and US_VISA only.
- `2.0` records: evaluate all rules whose family appears in `applied_constraints`.

**Transition-period result consistency.** A query returning both v1.1 and v2.0 candidates evaluates them under different rule sets. Under the synchronous re-extraction controls — per-search extraction cap, timeouts, and fallback paths — some v1.1 candidates in large folders will still be evaluated without RANK_MATCH or COC_DOCUMENT_GATE even when those constraints are active. Recruiters see a single result list, but transition-period evaluation consistency is best-effort until background migration reaches target coverage. This is a known and accepted trade-off during the migration window, not a bug.

Synchronous re-extraction is a correctness improvement under these conditions, not a correctness guarantee. Under load, the per-search extraction limit means the exact queries that most benefit from upgraded evaluation can still produce partially inconsistent results. This is a correctness trade-off as well as a latency safeguard. It is logged and flagged in the filter audit output, and the search response must include a user-visible partial-evaluation notice when any result in the current search fell back to v1.1 evaluation because of timeout, failure, cooldown, or per-search re-extraction limits.

**Synchronous re-extraction controls** (all mandatory):
- **Cooldown.** A record re-extracted within the last 24 hours must not be re-extracted again. Cache the re-extraction timestamp per candidate ID.
- **Per-search limit.** A single search triggers at most 5 synchronous re-extractions. Remaining v1.1 candidates above this limit are evaluated on the v1.1 path with a partial-evaluation flag.
- **Timeout.** Each synchronous re-extraction has a 10-second timeout. Timeout → v1.1 path with timeout-fallback flag.
- **Failure fallback.** Extraction failure → v1.1 path. Failures are logged but must not cause the search to fail.
- **Concurrency guard.** Two concurrent searches triggering re-extraction for the same candidate ID: one runs, the second waits up to 5 seconds then falls back to v1.1 path.

**Background re-extraction.** Iterates all indexed resumes and re-extracts to v2.0. Must be idempotent. Migration progress must be observable by admins. Target: ≥ 95% of records at v2.0 within 30 days of Phase 1 deployment.

### 10.6 Files changed

- `ai_analyzer.py` — all changes
- `tests/test_ai_analyzer_rank_normalization.py` — new
- `tests/test_ai_analyzer_certifications.py` — new
- `tests/test_ai_analyzer_logistics.py` — new
- `tests/test_ai_analyzer_age_filters.py` — existing, must pass unchanged
- `tests/test_ai_analyzer_dob_parsing.py` — existing, must pass unchanged

### 10.7 Phase 1 exit criteria

All criteria must be met before Phase 2 begins. Sign-off required from the developer who implemented Phase 1 and a recruiter or operator with direct search experience.

**Automated tests**

| Criterion | Threshold |
|---|---|
| Full test suite passes | 100% |
| Validated baseline matches | Both expected PDFs match |

**Extraction quality** — hand-check 30 sampled resumes from the index.

| Field | Correctness | Max incorrect rate | Usable coverage |
|---|---|---|---|
| Rank (normalized) | ≥ 85% correct | ≤ 10% incorrect | ≥ 70% not-UNKNOWN |
| COC present + valid | — | ≤ 10% incorrect | ≥ 60% not-UNKNOWN |
| US visa extraction | No new false positives or false negatives vs v1.1 | — | — |

**RANK_MATCH launch gate:** if rank usable coverage is below 70% at exit evaluation, RANK_MATCH does not launch as a hard filter. Phase 1 ships without it active. It is re-evaluated once alias table coverage has been improved and the 30-resume sample re-run. This is a pre-launch gate, not a post-deploy soft demotion.

**Filter behavior**

| Criterion | Threshold |
|---|---|
| No unexpected false negatives from RANK_MATCH | ≤ 5% fewer PASS results on folders that ran without rank filter — investigate any decrease before sign-off |
| Dual-version routing verified | v1.1 candidates return UNKNOWN (not FAIL) for RANK_MATCH, confirmed by test |
| `applied_constraints` contract verified | Searches without rank in applied_constraints produce identical results to pre-Phase-1 baseline |
| Partial-evaluation notice surfaced | Searches that hit v1.1 fallback show a user-visible partial-evaluation notice | Confirmed in test or UI verification |

**Migration**

| Criterion | Threshold |
|---|---|
| Background re-extraction functional | Successfully re-extracts ≥ 30 resumes to v2.0 with no errors |
| Re-extraction idempotent | Running twice on same resume produces identical v2.0 record |
| Migration progress observable | Admin view shows v1.1 vs v2.0 record counts |

---

## 11. Phase 2 — v3-ranking

> **Preconditions.** All Phase 1 exit criteria met and background re-extraction ≥ 95% complete.

### 11.1 Scope

- Sea service table extraction (total months, by vessel type, by rank, last sign-off)
- Heuristic ranking layer with mandatory heuristic label
- LLM constraint extraction with schema validation and deterministic post-normalization
- User-visible constraint preview (interaction model to be designed first)
- LLM shortlist: top N by score + K score-incomplete candidates
- Shortlist monitoring (Section 8.2)

### 11.2 Sea service extraction caution

Start with the most structured table formats. Accept UNKNOWN conservatively. Measure extraction accuracy on 30 resumes before wiring MIN_SEA_SERVICE or VESSEL_TYPE into filter rules or scorer.

### 11.3 Phase 2 exit criteria

| Criterion | Measurement | Threshold |
|---|---|---|
| Sea service correctness | Hand-check 30 resumes | ≥ 80% correct |
| Sea service usable coverage | Same 30 resumes | ≥ 60% not-UNKNOWN |
| Vessel type correctness | Same 30 resumes | ≥ 80% correct |
| Vessel type usable coverage | Same 30 resumes | ≥ 60% not-UNKNOWN |
| Scorer-to-recruiter correlation | Compare heuristic ranking to recruiter ranking on 20 real candidates | Spearman ρ ≥ 0.60 |
| No score-based false exclusions | Shortlist monitoring results from 50 triggered comparisons | Broader set does not consistently outperform top-N by recruiter override rate |
| Scorer label visible in UI | UI review | Heuristic label on all score surfaces |
| Constraint preview design approved | Product sign-off | Interaction model approved before LLM constraint extraction deploys |

Sign-off: recruiter or operator with ≥ 20 searches of production experience, plus developer.

---

## 12. Phase 3 — v3-indexed-facts

> **Preconditions.** All Phase 2 exit criteria met. All Tier 2 extractors ≥ 85% accuracy on a held-out 50-resume sample.

### 12.1 Scope

- Move extraction from query time to index time
- Pre-compute and store CandidateFacts v2.0 at index time
- Enable pre-retrieval filtering for structured-only queries
- Remove query-time raw-text fallback (only after this phase completes)

### 12.2 Storage model and source-of-truth hierarchy

1. **Dedicated store (Supabase or equivalent)** — authoritative. All filter and scoring decisions are made from this record.
2. **Pinecone metadata subset** — disposable optimization state. Carries a minimal filter-critical subset of Tier 1 fields for pre-retrieval filtering. Not the canonical record.
3. **Query-time transient extraction** — fallback only.

**Precedence rule:** the dedicated store is always authoritative. When Pinecone metadata and the dedicated store disagree, the metadata is refreshed from the dedicated store — the canonical record is never overwritten by metadata or transient extraction. Query-time transient extraction may supplement but never overwrite the dedicated store.

Pinecone metadata carries:
- `facts_version`
- `role.applied_rank_normalized`
- `derived.age_years` + `derived.age_computed_at`
- `certifications.coc.status`
- `logistics.us_visa_valid`
- `experience.sea_service_by_vessel_type` (list of canonical IDs, not month values)

The full CandidateFacts record must never be stored in Pinecone metadata.

### 12.3 Risk profile and mitigations

- **Stale record detection.** `extracted_at` older than 90 days → re-extraction triggered before evaluation.
- **Migration progress tracking.** System reports percentage of index at each facts_version.
- **Fallback for missing metadata fields.** If a constraint field is not in the Pinecone subset, fall back to full-scan evaluation rather than failing the search.

---

# Appendix

---

## 13. Regression Protection

### 13.1 Validated baseline

- **Folder**: `/Users/kartikraghavan/temp12/2nd_Engineer`
- **Query**: "should be within the ages of 30 and 50 years old"
- **Expected matches**: `2nd_Engineer_315781.pdf`, `2nd_Engineer_349740.pdf`

Checked manually before merging any logical change set that touches the evaluation path. Not required after every individual commit — the automated test suite covers per-commit regression.

### 13.2 Automated tests

Must pass on every commit:
- `tests/test_ai_analyzer_dob_parsing.py` — 6 tests
- `tests/test_ai_analyzer_age_filters.py` — 22 tests
- All Phase 1 tests added per commit

### 13.3 Isolation policy for stable extraction methods

`_extract_dob_fact_from_text` and `_extract_stated_age_fact_from_text` must not be modified as part of unrelated feature work. If a real bug is found, modification is permitted under these conditions: a dedicated commit modifying only these methods and their tests, with a commit message referencing the specific bug and the resume format that exposed it, verified against the validated baseline before merge.

### 13.4 New extractor requirements

Every new extractor must have a test file before integration. Tests must cover the UNKNOWN case — every extractor must have at least one test confirming it returns MISSING or UNKNOWN (not an incorrect value) when the field is absent.

---

## 14. What Not To Do

- **Do not use "all existing behaviour unchanged" as a regression statement.** Say instead: existing age and visa evaluation must not regress; new rules are additive.
- **Do not implement fuzzy rank matching in Phase 1.** Explicit alias entries only. Fuzzy matching is a deferred target-architecture option.
- **Do not map "pending" certificate states to "absent".** Pending → unknown. Absent requires explicit negation in the resume text.
- **Do not use `age_years` for hard filter decisions under any circumstances.** Recompute from DOB. If DOB is unavailable, return UNKNOWN. Stored `age_years` is for diagnostics and display only.
- **Do not read directly from `hard_constraints` in the filter engine to decide activation.** Read `applied_constraints` rule-family IDs first, then read the corresponding parameter from `hard_constraints`.
- **Do not deploy synchronous re-extraction without all five controls.** Cooldown, per-search limit, timeout, failure fallback, and concurrency guard are all required.
- **Do not launch RANK_MATCH as a hard filter if usable coverage is below 70% at exit evaluation.** It is a launch gate, not a post-deploy adjustment.
- **Do not enforce "no raw text after extraction" before Phase 3 completes.**
- **Do not present the heuristic score as objective.**
- **Do not multiply the scoring components.** They are additive weights.
- **Do not skip schema validation on JobConstraints.**
- **Do not implement Phase 3 before Phase 2 exit criteria are met.**
- **Do not present `extractable_profile_completeness_score` as candidate quality.** It is extractor coverage.
- **Do not store the full CandidateFacts record in Pinecone metadata.**
- **Do not let Pinecone metadata or transient extraction overwrite the dedicated store.**
- **Do not extend dual-version coexistence past 30 days without a diagnosed cause.**

---

*Specification v3.4 — NjordHR Candidate Intelligence Architecture*
*Supersedes: Architecture v3.3 (2026-04-03)*
*Deterministic Filter Engine Spec v2.1 remains authoritative for filter engine internals*
*Status: Final revision — treat as stable working document*
*Date: 2026-04-03*
