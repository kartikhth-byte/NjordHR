# NjordHR Candidate Intelligence Architecture
## Specification v3.4 — Target Architecture + Phased Rollout

---

> **How to read this document**
>
> Part A (Sections 1–9) is a **target architecture**. It describes where the system should end up. Several sections describe a future state that is not yet safe to build. Read it to understand design intent, not to derive a sprint plan.
>
> Part B (Sections 10–12) is a **phased implementation plan**. Only Phase 1 (Section 10) is an actionable implementation spec. Phases 2 and 3 are design sketches that must not be executed until the preceding phase passes its exit criteria.
>
> **What this document supersedes:** Deterministic Filter Engine Spec v2.1 remains authoritative for filter engine internals except where this v3.4 document explicitly extends or overrides filter behavior for v3.4 Phase 1 work. Architecture v3.3 is superseded by this revision.
>
> **What changed from v3.3:**
> - Section 3.3: Rank fuzzy matching explicitly staged — target architecture option, Phase 1 forbidden
> - Section 3.4: "pending" removed from the "absent" trigger; it maps to "unknown"
> - Section 4.2: `age_years` fallback clause removed; cached age is for diagnostics and display only, never hard filter input
> - Section 5.1 / 5.2: Explicit contract added — only fields listed in `applied_constraints` are authoritative for evaluation
> - Section 5.3: "No additional UX logic" claim softened
> - Section 5.6: Query routing and classification contract added for mixed, pure hard-constraint, semantic-only, and no-actionable-content searches
> - Section 6: Endorsement type-shape contract made explicit once
> - Section 6.5: Per-candidate result contract expanded to include typed UNKNOWN output
> - Section 8.2: "Better candidates" metric defined as recruiter override rate
> - Section 10.1: Synchronous re-extraction framed as a correctness trade-off, not only a latency safeguard
> - Section 10.5: "Recruiters see a consistent result list" replaced with honest best-effort wording
> - Section 10.7: RANK_MATCH demotion is a pre-launch gate, not a post-deploy soft demotion
> - Section 10.7 / 14: FACTUAL_UNKNOWN review-path launch gate and routing guard rails added

---

## 0. Implementation Risks

Read this section before implementing any Phase 1 change. These are the highest-risk failure modes in this architecture.

### 0.1 Synchronous re-extraction can degrade correctness and latency

The v1.1 → v2.0 transition is the sharpest operational risk in Phase 1. Synchronous re-extraction improves correctness, but under timeout, per-search cap, cooldown, or failure fallback, some candidates will still be evaluated on the v1.1 path. Implement exactly the controls in Section 10.5 and treat any deviation as a correctness bug, not an optimization detail.

### 0.2 Prompt parsing mistakes can create silent search errors

Constraint extraction is only safe if supported patterns are derived from the real prompt corpus and unsupported patterns are surfaced in `unapplied_constraints`. Do not broaden parsing rules from intuition alone. Use the prompt-corpus workflow and launch-gate coverage checks defined in Sections 5.2.1 and 10.7.

### 0.3 Cached age must never become an eligibility shortcut

`derived.age_years` is a cached display/diagnostic value only. Age-based eligibility must be computed from `identity.dob` at evaluation time. Any implementation path that uses cached age to exclude candidates — including metadata prefiltering — violates the hard-filter contract.

### 0.4 STCW and certificate absence logic is easy to overstate

Resume omission is not the same as certificate absence. `pending` is not `absent`. Silence is not `false`. Follow the four-state semantics in Section 3.4 strictly. Over-aggressive absence detection will create false negatives that are hard to spot in production.

### 0.5 Phase 2 ranking is heuristic and must not leak into Phase 1

Do not let scoring concepts distort Phase 1 filter behavior. Phase 1 is about deterministic fact extraction and hard-filter safety. The heuristic ranking layer is deferred, explicitly labeled, and gated by later validation because it is more subjective and easier to overfit.

### 0.6 Raw-text fallback must remain available until Phase 3 completes

Do not remove query-time raw-text fallback early. Extraction correctness is not yet strong enough for precomputed facts to be the sole basis of search completeness. Premature removal creates silent false negatives.

### 0.7 Measurement discipline is part of the implementation, not post-work

The tests, prompt-corpus thresholds, sampled resume checks, migration observability, partial-evaluation notices, and rollback discipline are part of the implementation itself. Skipping them turns a staged rollout into an uncontrolled behavior change.

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
   ┌────┼──────────────┐
 PASS  FAIL        UNKNOWN
   │     │              │
   ▼  excluded   review or partial evaluation
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

The alias table used for constraint extraction and the alias table used for CandidateFacts extraction must be the same instance or generated from the same canonical source. Any update to the alias table applies to both paths. Constraint-side and resume-side rank normalization must not drift independently.

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

For Phase 1, the four core STCW basic certificates are:
- Personal Survival Techniques (`PST`)
- Fire Prevention and Fire Fighting (`FPFF`)
- Elementary First Aid (`EFA`)
- Personal Safety and Social Responsibilities (`PSSR`)

`stcw_basic_all_valid` is a derived tri-state field computed from those four per-certificate states. It returns native boolean `true` only when all four are in `present` state. It returns native boolean `false` when at least one is in `expired` or `absent` state. It returns `null` in all other cases, including when any certificate is in `unknown` state (which includes pending). The stored field is therefore tri-state (`true` / `false` / `null`), not a stringified boolean.

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
    "raw_text_fragment": str | None    # bounded audit excerpt
}
```

`raw_text_fragment` is for auditability, not full-text storage. In Phase 1 it should be stored as a bounded excerpt only, sized just large enough to support debugging and review. It may be retained transiently in search/audit logs rather than permanently in every long-lived facts record, depending on the storage path. Any implementation storing it persistently must use an explicit retention/access policy consistent with the prompt corpus and audit-review workflow.

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
    "availability_date":      null,
    "availability_status":    "immediately",
    "salary_expectation_usd": 4500
  }
}
```

`sea_service_by_company` is excluded. Reliable company name normalization requires a dedicated registry and is deferred.

### 4.4 Tier 3 — Signals fields

```json
{
  "signals": {
    "extractable_profile_completeness_score":   null,
    "extractable_profile_completeness_missing": null,
    "career_stability_score":    null,
    "short_stint_count":         null,
    "gap_count":                 null,
    "longest_gap_months":        null,
    "promotion_path_valid":      null,
    "language_quality_score":    null
  }
}
```

`extractable_profile_completeness_score` is an extractor coverage metric, not a candidate quality metric. A strong candidate with a badly formatted resume may score low. Any surface displaying this value must make that explicit. In Phase 1 all Tier 3 signal fields remain null. Null signal fields indicate the relevant source data or signal computation is not yet available in the current phase.

### 4.5 Field confidence thresholds

| Field | Minimum confidence to use | Used in |
|---|---|---|
| DOB / age | 0.90 | Hard filter |
| Current rank (normalized) | 0.85 | Hard filter + scorer |
| COC validity | 0.85 | Hard filter (document gate) |
| STCW endorsements | 0.80 | Hard filter (Phase 2+, if constrained) |
| US visa status | 0.90 | Hard filter |
| Total sea service months | 0.70 | Hard filter (Phase 2+, if constrained) + scorer (Phase 2+) |
| Sea service by vessel type | 0.65 | Hard filter (Phase 2+, if constrained) + scorer (Phase 2+) |
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
    "availability": {
      "value_type": null,
      "status": null,
      "available_from_date": null,
      "relative_display_value": null
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
  "parsing_notes": [
    "VLCC-ish but not exactly"
  ]
}
```

**Evaluation contract:** only rule-family IDs named in `applied_constraints` are authoritative for filter evaluation. These IDs are stable activation keys, not schema paths. In Phase 1 and Phase 2 the canonical IDs are: `age_range`, `us_visa`, `rank_match`, `coc_document_gate`, `stcw_basic`, `min_sea_service`, `vessel_type`, `availability`, and `stcw_endorsement`. Fields present in `hard_constraints` but absent from `applied_constraints` are parsed informational data only. The filter engine must not read from `hard_constraints` directly to decide activation — it reads `applied_constraints` first, then reads the corresponding field within `hard_constraints` for parameter values. This contract prevents Phase 1 code from accidentally activating Phase 2 rules because a parsed field happens to be non-null. `parsing_notes` is the canonical minimal diagnostic field for prompt fragments that cannot be cleanly classified into supported hard constraints, semantic intent, or recognized-but-unapplied structured constraints. In Phase 1 it may be stored as plain fragment strings rather than structured diagnostic objects. It must not be surfaced in recruiter-visible constraint summaries. `extraction_notes` is a legacy free-text developer note field retained only for compatibility with older debug output; it is not part of the canonical Phase 1 schema example, must not drive routing behavior, and should not be emitted by new implementations unless a compatibility path explicitly requires it. New logic should use `parsing_notes` instead.

### 5.2 Constraint extraction rules

- Age ranges expressed as "between X and Y" extract as `{min_years: X, max_years: Y}`. Not compound AND queries.
- "Preferred" or "preferably" moves a constraint to `soft_preferences`.
- Vessel type and rank strings pass through the alias tables. Unrecognized strings → `null`.
- If a constraint cannot be reliably parsed, the field is `null`.

Activation rule for supported hard constraints in Phase 1:
- a supported constraint activates only when deterministic parsing yields the canonical parameter value required by that rule family
- for `rank_match`, alias table lookup must return a canonical rank ID; otherwise the constraint does not activate
- for `age_range`, a deterministic age-range pattern must yield at least one valid numeric bound; single-bound queries are allowed in Phase 1 and evaluate against the populated bound only (`min_years = 30, max_years = null` means age >= 30; `min_years = null, max_years = 40` means age <= 40)
- for `us_visa`, a supported visa-requirement phrase from the bounded prompt forms defined through Section 5.2.1 must be matched; otherwise the constraint does not activate
- for `coc_document_gate`, a supported COC-requirement phrase from the bounded prompt forms defined through Section 5.2.1 must be matched; otherwise the constraint does not activate
- for `stcw_basic`, a supported STCW-basic-validity phrase from the bounded prompt forms defined through Section 5.2.1 must be matched; otherwise the constraint does not activate
- for Phase 2 families recognized but not applied in Phase 1, recognition must be conservative and value-bearing, not type-only:
  - numeric duration phrases such as "7+ years" or "36 months sea service" map to `min_sea_service` and must preserve the parsed evaluation value normalized to months for later evaluation, while the original user-facing phrase may still be shown in the summary display
  - vessel-type phrases map to `vessel_type` only when they match the bounded ship-type vocabulary loaded by the running product from the active configuration `[ShipTypes] ship_type_options`; for Phase 1 this coupling is deliberate, meaning prompt-side vessel-type recognition uses the same runtime vocabulary the product already exposes for ship-type selection. The constraint parser must read this vocabulary through the same runtime configuration object used by the product, not from a separate hardcoded copy. Vocabulary changes take effect when that runtime configuration is reloaded; if the deployment only loads configuration at process start, recognition changes take effect on restart
  - explicit availability phrases map to the `availability` constraint family and populate `hard_constraints.availability` only when they match one of the bounded Phase 1 recognition forms below and the recognized value is preserved for summary display:
    - immediate-availability phrases: "available immediately", "join immediately". Store `value_type = status`, `status = immediately`
    - explicit-date phrases: "available from January 15". Store `value_type = date`, `available_from_date = <parsed date>`
    - relative-duration phrases: "joinable in 30 days". Store `value_type = relative_phrase`, `relative_display_value = in 30 days`
    Immediate-status and relative-duration availability values are display-only in Phase 1 and must not be consumed later as durable availability facts. `immediately` is not a durable date fact any more than `in 30 days` is: both are statements relative to the time the resume was written or parsed. Phase 2 availability scoring and any later availability-based evaluation must therefore re-extract candidate-side availability from the resume at evaluation time, or from later indexed CandidateFacts if Phase 3 introduces them, rather than consuming the Phase 1 display-oriented JobConstraints values directly
  - explicit endorsement phrases map to the `stcw_endorsement` constraint family and populate `hard_constraints.certifications.endorsements_required` only when the endorsement type can be identified and preserved using the canonical endorsement IDs from the CandidateFacts schema. This mapping is authoritative for Task 07 prompt parsing and for any later evaluation path that reads the stored endorsement requirement:
    - `DPO`, `DP operator` -> `dp_operational`
    - `GMDSS` -> `gmdss`
    - `oil tanker endorsement` -> `tanker_oil`
    - `chemical tanker endorsement` -> `tanker_chemical`
    - `gas tanker endorsement` -> `tanker_gas`
    Ambiguous tanker phrases such as `tanker endorsement` without oil / chemical / gas disambiguation, and ambiguous DP phrases such as `DP`, `DP2`, or `DP3`, belong in `parsing_notes`, not `unapplied_constraints`, unless later prompt-corpus review proves a narrower mapping is safe
- if a phrase is plausibly structured but does not match a bounded recognition form with an extractable value, it belongs in `parsing_notes`, not `unapplied_constraints`
- if the parser recognizes a structured intent but cannot derive a reliable canonical parameter value, it must not activate the rule on partial evidence

### 5.2.1 Prompt corpus and rule design requirement

Constraint extraction rules must be derived from a real prompt corpus, not invented only from hypothetical examples. For every supported constraint family, the team must collect a representative sample of recruiter prompts and use that sample to drive both parser design and regression coverage. The operational prompt-corpus source and review loop are defined in [prompt-corpus-and-feedback-spec-v0.3.md](/Users/kartikraghavan/Tools/NjordHR/docs/prompt-corpus-and-feedback-spec-v0.3.md).

Required workflow for each new constraint family:

1. Collect real prompt examples from users, logs, UAT notes, or recruiter-provided examples.
2. Cluster prompts by intent:
   - deterministic hard constraint
   - semantic / fuzzy preference
   - ambiguous or unsupported phrasing
3. Define the supported prompt forms for that family from the observed prompt corpus.
4. Add deterministic parsing rules for those forms.
5. Add regression tests using the observed prompt examples, not just synthetic examples.
6. Measure prompt coverage before launch:
   - how many sampled prompts are parsed correctly
   - how many fall into `unapplied_constraints`
   - how many are misparsed

This is a design and readiness requirement, not optional documentation. A constraint family is not ready because the field extractor works in resumes; it is ready only when real user phrasing for that constraint family is understood with acceptable coverage.

This requirement applies retroactively to already-live deterministic constraint families as well. In Phase 1, that means age range and US visa are not exempt because their parsers predate this section: a real prompt sample must still be assembled and coverage measured for them before Phase 1 is considered complete.

Terminology:
- **Constraint prompts** are prompts that express hard, checkable requirements such as age, visa, rank, or required documents.
- **Semantic or fuzzy prompts** are prompts that express qualitative relevance such as "strong leadership", "good stability", or "best fit". These are not deterministic constraints and should not be forced into hard-filter rules.

### 5.3 Mixed-support query UX

When a query contains supported and unsupported constraint types, the system must display a structured constraint summary:

```
Applied filters:   Rank (2nd Engineer), COC (valid, required)
Not applied:       Sea service (7+ years), Vessel type (Bulk Carrier)
                   These criteria will be available in a future update.
```

This summary is shown prominently on the results page. It is driven by the `applied_constraints` and `unapplied_constraints` fields in JobConstraints, which reduces the amount of UX-specific logic needed — though human-readable labels, ordering, and formatting still require presentation work on the product side. This section defines only the user-visible summary behavior. The routing and classification contract for mixed, pure hard-constraint, semantic-only, and no-actionable-content queries is defined in Section 5.6.

### 5.4 User-visible constraint preview (Phase 2)

Showing users the extracted constraints before a search runs is the right mechanism for catching LLM extraction drift. The interaction model (read-only preview vs editable constraints) and the backend contract for overrides must be designed before Phase 2 LLM constraint extraction is deployed.

### 5.5 Validation before use

JobConstraints do not enter the filter engine without passing schema validation. A validation failure falls back to vector search without hard filtering, with a visible warning.

### 5.6 Query Routing and Classification

This section defines how recruiter queries are classified and routed when they contain supported hard constraints, semantic / fuzzy intent, unsupported structured requirements, or unclassifiable fragments.

Queries in this architecture fall into four routing cases:
- mixed queries: at least one supported hard constraint and at least one semantic / fuzzy intent signal; these are governed by the mixed-search decision contract and pipeline below
- pure hard-constraint queries: one or more supported hard constraints and no semantic / fuzzy intent; these are governed by the same full-folder-scan rule described in the scope clarification below
- semantic-only queries: no supported hard constraints and one or more semantic / fuzzy intent signals; these are governed by the semantic-only query rule below
- queries with no actionable content: no supported hard constraints and no semantic / fuzzy intent after classification; these are governed by the graceful-failure rule below

A mixed search is a query that contains both:
- one or more supported hard constraints
- one or more semantic / fuzzy intent signals

Example:
- "2nd engineer with valid COC and strong leadership under pressure"

This query contains:
- supported hard constraints:
  - `rank_match`
  - `coc_document_gate`
- semantic / fuzzy intent:
  - "strong leadership under pressure"

If a prompt also contains recognized but currently unsupported structured requirements, those must be separated into `unapplied_constraints` rather than merged into the semantic bucket.

If a phrase cannot be reliably assigned to the supported-hard-constraints bucket, it must not be promoted into an active hard constraint on ambiguous evidence. It must instead default to:
- semantic / fuzzy intent, if it is plausibly qualitative
- `unapplied_constraints`, if it is plausibly structured but not reliably classifiable
- `parsing_notes`, if it is unrecognized noise, malformed input, or cannot be meaningfully classified as either qualitative or structured

Unrecognized input must not be silently discarded. `parsing_notes` is an internal JobConstraints field for diagnostic-only parser output and must not be surfaced in the recruiter-visible constraint summary. See Section 5.1 for the canonical field definition. In Phase 1, `parsing_notes` may contain only the unclassified prompt fragments themselves; richer diagnostic structure is optional and deferred unless later phases require it.

Example `parsing_notes` entry:

```json
"VLCC-ish but not exactly"
```

If the supported-hard-constraints bucket is empty and the semantic bucket is empty after prompt classification, the search must fail gracefully with a user-visible warning. It must not silently execute as an unconstrained search. This rule covers both:
- prompts where the entire input lands in `parsing_notes`
- prompts where the entire input lands in `unapplied_constraints`

When `unapplied_constraints` is the reason the search cannot proceed, the warning should surface the recognized-but-not-applied constraint types so the recruiter understands why no actionable search path exists.

**Scope clarification:** the full-folder-scan requirement in this section applies to any query that activates one or more supported hard constraints, not only mixed queries. This section focuses on the mixed case because that is where hard constraints and semantic intent interact, but the full candidate set rule also governs pure hard-constraint queries.

**Phase scope note:** the full-folder-scan requirement in this section applies in Phase 1 and Phase 2. In Phase 3, where extraction coverage and Pinecone metadata support are sufficient, pre-retrieval metadata filtering may replace full folder scan for supported structured dimensions. If a mixed query activates a supported constraint whose field is not present in the Pinecone metadata subset, the system must follow the general fallback principle in Section 12.3 and fall back to full-scan evaluation rather than failing the search or silently narrowing the candidate universe. A full Phase 3 fallback contract is deferred to Phase 3 planning.

During the Phase 1 and Phase 2 dual-version transition, the full candidate set in the selected rank folder may include both v1.1 and v2.0 records. These candidates remain subject to the routing rules in Section 10.5.

#### Mixed search decision contract

For mixed searches, supported hard constraints must be evaluated independently of semantic retrieval completeness.

In Phase 1 and Phase 2, this means:
- if a query contains one or more supported hard constraints, the hard filter runs against the full candidate set in the selected rank folder
- not against only the Pinecone-retrieved subset

Constraint extraction for this step must follow the `JobConstraints` contract in Section 5.1 and the extraction rules in Section 5.2.

Semantic retrieval is allowed to:
- refine ordering
- provide supporting evidence
- drive heuristic ranking inputs where applicable
- provide context for LLM reasoning

Semantic retrieval must not:
- determine whether a candidate who satisfies supported hard constraints is considered at all
- hide a hard-filter PASS candidate solely because no chunk was retrieved above similarity threshold
- override a deterministic FAIL on a supported hard constraint

A vector miss must never be the mechanism that excludes a structured-valid candidate.

#### Mixed search pipeline

For a mixed query, the system must separate the prompt into three routing buckets:
- supported hard constraints
- semantic / fuzzy intent
- recognized but currently unsupported structured constraints

Prompt fragments that cannot be classified into any of these routing buckets fall into `parsing_notes` as diagnostic output.

The system then applies the following order:

1. Extract and normalize supported hard constraints into `JobConstraints`. During extraction, each prompt fragment is assigned to exactly one of the routing buckets above or to `parsing_notes`.
2. Run the hard filter against the full candidate set in the selected rank folder. This full-folder evaluation rule applies in Phase 1 and Phase 2. For each candidate, compute the overall hard-filter result using the aggregation rule defined in Section 6.5.
3a. Retain all candidates whose overall hard-filter result is `PASS` for the normal result path.
3b. Retain all candidates whose overall hard-filter result is `UNKNOWN` for review or partial-evaluation routing.
3c. Exclude all candidates whose overall hard-filter result is `FAIL` from the visible result set. Their FAIL reason must remain available in the per-candidate audit output.
4. Identify UNKNOWN source types in the per-rule audit output produced by the hard filter:
   - `VERSION_MISMATCH_UNKNOWN`
   - `FACTUAL_UNKNOWN`
5. Apply UNKNOWN precedence at the candidate level:
   - if any per-rule UNKNOWN is `FACTUAL_UNKNOWN`, the candidate routes to the human review path
   - a candidate routes to the partial-evaluation path only when all per-rule UNKNOWN results are `VERSION_MISMATCH_UNKNOWN`
6. Route semantic / fuzzy intent into semantic retrieval, heuristic ranking inputs, and LLM reasoning for the PASS set. Semantic evidence may also be attached to UNKNOWN candidates as supporting context for the review interface, but it does not alter UNKNOWN routing or convert UNKNOWN to PASS.
7. Use semantic evidence to rank, explain, and differentiate hard-filter PASS candidates.
8. Surface unsupported structured constraints in `unapplied_constraints` and in the user-visible constraint summary. This step is order-independent relative to steps 6 and 7 and may be surfaced as soon as step 1 completes.

The hard filter must not emit a generic untyped UNKNOWN. It must encode the UNKNOWN source type in the per-candidate audit output so downstream routing and UI behavior do not need to re-derive it. The per-rule audit output format, including `unknown_reason`, is defined in Section 6.5.

`FACTUAL_UNKNOWN` is a conservative umbrella category covering multiple sub-causes, including:
- confidence below threshold
- null or unavailable value on a v2.0 record
- data conflict
- unrecognized or future-defined factual uncertainty states, treated as `FACTUAL_UNKNOWN` until explicitly reclassified

In this architecture all `FACTUAL_UNKNOWN` candidates route to human review by default. This is a conservative policy that may be refined later if real-world UNKNOWN rates show that some sub-causes warrant differentiated handling. Any such refinement would require updates to both the Section 6.5 reason codes and the routing rules in this section.

#### Human review path

The human review path means:
- the candidate is surfaced with a review-required flag in the per-candidate audit output
- the candidate remains accessible through the review interface
- semantic evidence may be attached as supporting context
- the candidate is not presented as a normal PASS result

For Phase 1, a functioning review interface for `FACTUAL_UNKNOWN` candidates is a launch requirement. The existing uncertain-match review-oriented UI surface may be adapted to serve this purpose, but it must not be assumed to work unchanged: hard-filter UNKNOWN candidates carry rule-level audit data, confidence-failure reasons, and conflict indicators that may need different presentation from LLM-confidence uncertainty. A corresponding exit criterion for this requirement is in Section 10.7.

This spec does not define the exact UI component or layout for the review interface.

#### Candidate visibility rule

All candidates who PASS the supported hard constraints in a mixed search must remain accessible to the recruiter, even if:
- semantic retrieval finds weak evidence for the fuzzy part of the query
- no chunk for that candidate crosses the similarity threshold
- the candidate is not included in the bounded LLM shortlist

The bounded semantic shortlist limits:
- LLM reasoning
- narrative explanation generation
- primary ranking display

It does not define the total valid result universe.

If semantic retrieval is unavailable or returns an error, the system must degrade gracefully by preserving and serving the hard-filter PASS candidate set, even if semantic ordering or LLM explanation is unavailable for that search.

When semantic retrieval is unavailable, UNKNOWN routing is unaffected:
- candidates with `VERSION_MISMATCH_UNKNOWN` still follow the partial-evaluation path
- candidates with `FACTUAL_UNKNOWN` still follow the human review path

Retrieval failure affects only semantic ordering and LLM explanation availability for PASS candidates.

If candidates are routed to the review path as UNKNOWN, the search response must surface that fact to the recruiter. UNKNOWN candidates must not disappear silently behind the PASS results. The specific UI mechanism - count badge, separate section, review indicator, or similar - is a product design decision not defined in this spec.

#### Disagreement handling

If structured and semantic parts of the query disagree, the following rules apply:

- **Structured PASS + weak semantic evidence**
  - Candidate remains a valid PASS result.
  - Candidate may rank lower on semantic-fit dimensions.
  - Candidate must not be excluded solely for weak semantic evidence.

- **Structured PASS + no semantic retrieval hit**
  - Candidate remains a valid PASS result.
  - Candidate may appear without strong semantic explanation.
  - This gap must not be treated as a filter failure. Result-metadata expansion for semantic evidence-gap tracking is deferred and is not a Phase 1 schema requirement.

- **Structured FAIL + any semantic evidence strength**
  - Candidate is excluded.
  - Semantic evidence does not override deterministic hard-filter failure.

- **Structured UNKNOWN + any semantic evidence strength**
  - If the hard filter emitted any `FACTUAL_UNKNOWN`, the candidate follows the human review path.
  - Otherwise, if all UNKNOWN results are `VERSION_MISMATCH_UNKNOWN`, the candidate follows the partial-evaluation path defined in Section 10.5.
  - Semantic evidence may be shown as context, but it does not convert UNKNOWN to PASS.

#### Semantic-only query rule

If a query contains no supported hard constraints, then:
- `applied_constraints` is empty
- the hard filter activates no rules
- all candidates in the selected rank folder remain eligible to be returned by semantic retrieval
- semantic retrieval applies its own similarity scoring and depth cutoff
- semantic retrieval, heuristic ranking, and LLM reasoning perform the substantive matching work

This is the correct behavior for semantic-only queries. In that case, semantic retrieval is not overriding hard constraints; there are no applied hard constraints at risk of being missed by a vector miss.

In semantic-only searches, vector retrieval depth limits the candidate set. Candidates below the similarity cutoff or outside the retrieval depth may not be returned. This is an accepted limitation for semantic-only queries. Retrieval depth therefore has material impact on recall and must be documented as a deployment parameter from Phase 1 onward. The configured retrieval depth must be recorded as a named constant or configurable parameter with a documented default value that is accessible to anyone deploying or operating the search system.

#### Product implication

The UI may present results from this routing model in multiple ways, for example:
- a top semantic-fit shortlist with LLM summaries
- followed by additional hard-filter PASS candidates
- or an expandable "additional valid matches" section

This spec does not define the presentation mechanism.

It does require that the product preserve the distinction between:
- PASS candidates with strong semantic evidence, elevated in ranking or explanation surfaces
- PASS candidates with weak or absent semantic evidence, still valid results even if lower-ranked or shown without LLM reasoning
- candidates routed to review or partial-evaluation awareness because of UNKNOWN hard-filter results

In Phase 1, hard-filter PASS candidates are returned without heuristic ranking. From Phase 2 onward, the distinction between elevated and lower-ranked PASS candidates is driven by the heuristic ranking score and its component breakdown, which are part of the per-candidate result output.

UNKNOWN candidates must not be presented as PASS results. They must be surfaced in a way that makes their status clear. The specific UI mechanism is a product design decision not defined in this spec.

---

## 6. Deterministic Hard Filter

### 6.1 Endorsement type-shape contract

Stored endorsement fields (Section 4.2) use four-state strings. The filter engine derives evaluation values from these strings as follows:

- `"present"` → evaluates as `true` for `eq(true)` rules
- `"expired"` → evaluates as `false` for `eq(true)` rules (candidate had it, no longer valid)
- `"absent"` → evaluates as `false` for `eq(true)` rules
- `"unknown"` → evaluates as UNKNOWN (neither PASS nor FAIL — routes to human review)

This derivation happens at evaluation time inside the rule function. The stored record retains the full four-state string. Rule functions must not read a raw boolean endorsement value from the stored record.

### 6.2 Phase 1 rule set

The filter engine activates a rule only when its constraint family is listed in `applied_constraints`. It does not read directly from `hard_constraints`.

| Rule ID | Field | Operator | FAIL condition | UNKNOWN condition |
|---|---|---|---|---|
| AGE_RANGE | recomputed_age_years_from(identity.dob) | range_inclusive | age < min when min exists, or age > max when max exists | dob_confidence < 0.90 or DATA_CONFLICT |
| RANK_MATCH | role.applied_rank_normalized | contains_any | rank not in required set | confidence < 0.85 |
| COC_DOCUMENT_GATE | certifications.coc.status | eq("valid") | COC absent or expired when required | confidence < 0.85 |
| STCW_BASIC | certifications.stcw_basic_all_valid | eq(true) | stcw_basic_all_valid is false when required | value is null or confidence < 0.80 |
| US_VISA | logistics.us_visa_valid | eq(true) | visa absent or expired when required | confidence < 0.90 |

Typed UNKNOWN mapping for Phase 1:
- `VERSION_MISMATCH_UNKNOWN` is reserved for dual-version cases defined in Section 10.5, where an active rule cannot be evaluated on a v1.1 record because the required field exists only in v2.0.
- `FACTUAL_UNKNOWN` is emitted for the rule-level UNKNOWN conditions in the table above when the candidate is already on the v2.0 evaluation path and the rule cannot safely decide because of low confidence, null value, or data conflict.

For `AGE_RANGE`, `recomputed_age_years_from(identity.dob)` uses completed whole years at the evaluation date, not fractional-year age.

Phase 2 additions (inactive in Phase 1, not in `applied_constraints`):

| Rule ID | Notes |
|---|---|
| MIN_SEA_SERVICE | Requires Tier 2 extraction |
| VESSEL_TYPE | Requires Tier 2 extraction |

### 6.3 COC_DOCUMENT_GATE scope

`COC_DOCUMENT_GATE` checks only: is a COC present, and is it not expired. It does not check grade appropriateness, issuing authority, or flag state endorsement. The rule ID name is intentional — "document gate" signals a document-level presence and validity check. Audit output and any UI referencing this rule must use the full name.

### 6.4 STCW conservative policy

The STCW_BASIC rule evaluates as UNKNOWN — not FAIL — when `stcw_basic_all_valid` is `null` or confidence is below 0.80. A candidate whose STCW section was not found, or whose certificates are pending, routes to human review, not disqualification.

### 6.5 Per-candidate result contract

This section defines the canonical per-candidate output contract produced across the hard-filter pipeline for Phase 1 and Phase 2. The per-candidate `decision` field is the overall hard-filter result and is computed using the following aggregation rule:

- if any active rule returns `FAIL`, overall result = `FAIL`
- else if any active rule returns `UNKNOWN`, overall result = `UNKNOWN`
- else overall result = `PASS`

Active rules are the rule families whose IDs appear in `applied_constraints` as defined in Section 5.1.

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
      "confidence":     0.95,
      "unknown_reason": null
    },
    {
      "rule_id":        "COC_DOCUMENT_GATE",
      "decision":       "UNKNOWN",
      "actual_value":   "status_missing_expiry",
      "expected_value": {"coc_required": true, "coc_valid_required": true},
      "confidence":     0.61,
      "unknown_reason": "FACTUAL_UNKNOWN"
    }
  ]
}
```

`unknown_reason` is required when a per-rule decision is `UNKNOWN` and must be omitted or `null` otherwise. In Phase 1 and Phase 2 the allowed typed UNKNOWN values are:
- `VERSION_MISMATCH_UNKNOWN`
- `FACTUAL_UNKNOWN`

Semantic evidence-gap tracking may be added later as result metadata for mixed-search explanation behavior, but it is not part of the required Phase 1 per-candidate contract. This does not change the Section 14 guard rail: semantic retrieval must not determine whether a hard-filter PASS candidate is included in visible results.

---

## 7. Heuristic Ranking Layer

> **Label requirement.** Every UI surface displaying a score or ranking from this layer must include: *"Heuristic ranking — see breakdown for details. Not an objective score."* Score breakdowns must always be visible alongside the final score.

### 7.1 Formula

The weights below are provisional starting values, not validated truth. They are domain-informed initial guesses and are expected to be revised against recruiter feedback before Phase 2 exit. An implementation may start with these values, but Phase 2 success is determined by the Section 11.3 validation gates, not by preserving these numbers unchanged.

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
if actual_months is UNKNOWN:
    sea_service = 0.0
    score_incomplete = true
elif required_months is not null:
    sea_service = min(1.0, actual_months / max(required_months, 1))
else:
    sea_service = min(1.0, actual_months / 36)
```

When a recruiter states a minimum, this component measures requirement satisfaction: a candidate who meets the stated minimum receives full component credit (`1.0`), and a candidate who falls short is scored proportionally. Additional experience above the minimum does not increase this component further — extra depth is handled separately through the depth bonus in Section 7.3.

When no minimum is stated, a 36-month heuristic baseline is used for queries that imply experience depth without stating a numeric minimum. This preserves useful differentiation between candidates without treating the baseline as a hard threshold. It is a ranking heuristic only — it is not an interpretation of recruiter intent on its own.

If `actual_months` is UNKNOWN, the component scores `0.0` and the candidate receives `score_incomplete: true`, which can make them eligible for the K score-incomplete path to LLM reasoning.

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
- if `logistics_extended.availability_status = immediately`, component = `1.0`
- otherwise evaluate from `logistics_extended.availability_date`:
  - ≤ 30 days: 1.0 / 31–90 days: 0.75 / 91–180 days: 0.50 / > 180 days: 0.25
- UNKNOWN: 0.0

### 7.3 Phase 2 bonuses and penalties

| Bonus | Condition | Value |
|---|---|---|
| Sea-service depth | Candidate exceeds `required_months` by ≥ 24 months, only when `required_months` is not null | +0.03 |
| Specialisation | ≥ 60% of sea service on required vessel type | +0.05 |
| Promotion candidate | Applied rank one step above most recent rank | +0.03 |

| Penalty | Condition | Value |
|---|---|---|
| Rank above target | Current rank is senior to applied rank | −0.05 |
| COC absent for senior rank | No COC for Chief Mate or above | −0.04 |

Signals-based bonuses/penalties excluded until signal quality has been assessed.

### 7.4 Definition of `score_incomplete`

A candidate receives `score_incomplete: true` when the candidate has already passed the hard filter, but one or more scorer-critical ranking components cannot be computed above confidence threshold.

This flag is a ranking-quality warning, not a hard-filter outcome. It means:
- the candidate remains eligible
- the heuristic score may under-represent the candidate
- the candidate may be considered for the additional K shortlist path to LLM reasoning

Typical causes include:
- sea service months unavailable or below confidence threshold
- vessel type breakdown unavailable
- certification detail ambiguous for ranking purposes
- recency or availability fields unavailable

### 7.5 Scorer output

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

Phase 1 result ordering:
- heuristic ranking is out of scope in Phase 1
- PASS candidates are returned in ascending alphabetical order on `source_document.filename`, case-insensitive, with `candidate_id` as deterministic tiebreaker
- if another Phase 1 UI grouping is added, it must preserve PASS accessibility and use a documented deterministic order within each group

The LLM receives:
- Top N candidates ranked by heuristic score. Default N = 5, maximum N = 10.
- Up to K additional candidates who passed the hard filter but have `score_incomplete: true` on rank, certification, or sea service. Default K = 2.

All candidates who PASS the hard filter must remain accessible to the recruiter, even if they do not receive LLM-generated narrative reasoning. The `N + K` cap controls LLM cost and latency — it must not be used as a reason to hide valid PASS candidates from view. See Section 5.6 Candidate visibility rule for the mixed-search routing case where semantic retrieval could otherwise hide PASS candidates. How non-shortlisted PASS candidates are presented in the UI — separate section, lower in the ranked list, on-demand expansion — is a product design decision not defined in this spec.

### 8.2 Shortlist monitoring

Monitoring triggers — run a broader comparison (LLM on top N+5 PASS candidates) when:
- A search uses a Tier 2 field deployed within the last 30 days
- A search returns a `score_incomplete: true` rate above 30% of PASS candidates

**Defining "better candidates":** the primary metric for deciding whether the broader set is outperforming the top-N set is recruiter override rate — if recruiters are consistently selecting candidates from the broader set in preference to top-N candidates, the shortlist is too aggressive. Secondary metric: recruiter-reported LLM narrative quality on a 1–3 scale. If the broader-set candidates consistently receive higher narrative quality ratings, N should be increased. Review after the first 50 triggered comparisons in Phase 2 production.

Instrumentation for both signals — override rate capture and narrative quality ratings — is a Phase 2 prerequisite and must be designed before Phase 2 shortlist monitoring is activated. The mechanism for capturing these signals is not defined in this spec.

### 8.3 What the LLM is given

The LLM prompt is built from CandidateFacts, not raw resume text. Exception: during Phase 1 and Phase 2, raw resume text may be included as a supplementary context block when a candidate has significant `score_incomplete` flags.

Supplementary raw resume text is included only for hard-filter PASS candidates when one or more scorer-critical components are incomplete. Its purpose is to compensate for incomplete structured extraction during Phase 1 and Phase 2, not to replace CandidateFacts as the primary LLM input.

Default rule:
- include supplementary raw text only when one or more scorer-critical components are incomplete
- prefer relevant extracted sections or retrieval-selected excerpts over full-resume dumps
- apply a bounded per-candidate text budget so one long resume cannot dominate the prompt context

This spec does not require whole-resume inclusion by default. If whole-resume text is ever included, it must be a deliberate bounded fallback rather than the normal path.

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
| extractable_profile_completeness_score | Tier 1 fields | Phase 2+ | Internal only |
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
- All signals
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
Add `_extract_stcw_fact_from_text(text)` and `_extract_endorsements_from_text(text)`. Apply four-state semantics from Section 3.4 using the four core STCW basic certificates named there. "Pending" → unknown. `stcw_basic_all_valid` logic follows Section 3.4 and stores native `true` / `false` / `null`.
Tests (extend `tests/test_ai_analyzer_certifications.py`): all STCW present → `true`, one expired → `false`, one absent explicitly → `false`, one pending → `null`, one not mentioned → `null`, STCW section entirely absent → `null`.

**Commit 5 — Logistics extraction**
Add `_extract_logistics_from_text(text)`. Absorb any existing visa extraction logic. Compute validity flags against evaluation date.
Tests: `tests/test_ai_analyzer_logistics.py` — valid visa, expired visa, visa absent, passport expiry variants, passport absent.

**Commit 6 — Extend hard filter rules**
Add `_evaluate_rank_rule(facts, constraints)`, `_evaluate_coc_document_gate(facts, constraints)`, and `_evaluate_stcw_basic_rule(facts, constraints)`. Wire into `_evaluate_hard_filters`. All three rules read their activation from `applied_constraints` only. None reads directly from `hard_constraints` to decide whether it should run.
Tests: rank match, rank mismatch, rank UNKNOWN, COC required + valid, COC required + expired, COC required + absent, COC not in applied_constraints (rule skipped), STCW required + `true`, STCW required + `null` → UNKNOWN not FAIL.

**Commit 7 — Mixed-support constraint summary and parser diagnostics**
Add `applied_constraints`, `unapplied_constraints`, and `parsing_notes` population to constraint extraction. Pipe `applied_constraints` and `unapplied_constraints` through to stream events for the frontend structured summary. Unclassifiable fragments must land in `parsing_notes`; they must not be silently discarded.
Tests: age + rank query → `age_range` and `rank_match` in applied, age + sea_service query → `min_sea_service` in unapplied, rank + vessel type query → `vessel_type` in unapplied with extracted value preserved, rank + availability query → availability constraint in unapplied with extracted value preserved, rank + endorsement query → endorsement constraint in unapplied with extracted value preserved, ambiguous tanker endorsement phrase → `parsing_notes` not `unapplied_constraints`, ambiguous DP phrase (`DP`, `DP2`, `DP3`) → `parsing_notes` not `unapplied_constraints`, `DPO` or `DP operator` phrase → endorsement constraint in unapplied with canonical value preserved, malformed or unclassifiable fragment → `parsing_notes` entry populated, all-unsupported query → applied empty, all filters skip.

**Commit 8 — Query routing and graceful failure**
Before implementing this commit, the graceful-failure warning presentation must be explicitly decided on the product side. Implement the routing guard that fails the search with a user-visible warning when prompt classification yields no supported hard constraints and no semantic intent. Do not fall through to an unconstrained search in this case. Surface recognized-but-unapplied constraint types when `unapplied_constraints` is the reason no actionable search path exists.
Tests: all-`parsing_notes` query → graceful failure with warning, all-unsupported query → graceful failure with warning naming unapplied constraint types, mixed query still proceeds normally.

**Commit 9 — Typed UNKNOWN audit output and review-path surfacing**
Before implementing this commit, the `FACTUAL_UNKNOWN` review-required presentation must be explicitly decided on the product side. Add typed `unknown_reason` emission to per-rule audit output and wire candidate-level UNKNOWN routing to consume it. `VERSION_MISMATCH_UNKNOWN` must be produced for the dual-version cases in Section 10.5; `FACTUAL_UNKNOWN` must be produced for rule-level uncertainty on the v2.0 path. Candidates routed to the review-required path must be visibly separated from PASS results.
Tests: low-confidence v2.0 COC rule → `FACTUAL_UNKNOWN`, v1.1 candidate evaluated against active RANK_MATCH → `VERSION_MISMATCH_UNKNOWN`, UNKNOWN candidates route correctly to review or partial-evaluation awareness, `FACTUAL_UNKNOWN` candidates visibly separated from PASS results.

**Commit 10 — Regression validation**
Run validated baseline: folder `/Users/kartikraghavan/temp12/2nd_Engineer`, query "should be within the ages of 30 and 50 years old". Confirm both expected PDFs match. Run full test suite. Record results in commit message.

**Commit 11 — Background re-extraction and migration readiness**
Implement the background re-extraction path to v2.0, ensure re-extraction is idempotent, and make migration progress observable to admins. This commit closes the migration work required by Sections 10.5 and 10.7; Phase 1 is not complete until this migration-readiness work is implemented and verified.
Tests: successfully re-extract a representative sample batch of resumes to v2.0, rerun on the same resumes and confirm identical v2.0 output, confirm migration progress reporting exposes v1.1 vs v2.0 counts accurately.

### 10.5 Dual-version operations

**Where `facts_version` lives.** Persisted in the stored CandidateFacts record. Retrievable on every read. Not a runtime-only field.

**Routing.** Filter engine checks `facts_version` before evaluation:
- `1.1` records: evaluate AGE_RANGE and US_VISA only. Rules in `applied_constraints` that require v2.0 fields — RANK_MATCH, COC_DOCUMENT_GATE, STCW_BASIC — return UNKNOWN, not FAIL and not skip. This routes v1.1 candidates to human review rather than silently passing them through unfiltered or incorrectly disqualifying them.
- `2.0` records: evaluate all rules whose family appears in `applied_constraints`.

**Transition-period result consistency.** A query returning both v1.1 and v2.0 candidates evaluates them under different rule sets. Under the synchronous re-extraction controls — per-search extraction cap, timeouts, and fallback paths — some v1.1 candidates in large folders will still be evaluated on the v1.1 path, returning UNKNOWN for RANK_MATCH, COC_DOCUMENT_GATE, and STCW_BASIC rather than receiving full v2.0 evaluation. Recruiters see a single result list, but transition-period evaluation consistency is best-effort until background migration reaches target coverage. This is a known and accepted trade-off during the migration window, not a bug.

Synchronous re-extraction is a correctness improvement under these conditions, not a correctness guarantee. Under load, the per-search extraction limit means the exact queries that most benefit from upgraded evaluation can still produce partially inconsistent results. This is a correctness trade-off as well as a latency safeguard. It is logged and flagged in the filter audit output, and the search response must include a user-visible partial-evaluation notice when any result in the current search fell back to v1.1 evaluation because of timeout, failure, cooldown, or per-search re-extraction limits.

**Synchronous re-extraction controls** (all mandatory):
- **Cooldown.** A record re-extracted within the last 24 hours must not be re-extracted again. Cache the re-extraction timestamp per candidate ID.
- **Per-search limit.** A single search triggers at most 5 synchronous re-extractions. Remaining v1.1 candidates above this limit are evaluated on the v1.1 path with a partial-evaluation flag.
- **Timeout.** Each synchronous re-extraction has a 10-second timeout. Timeout → v1.1 path with timeout-fallback flag.
- **Failure fallback.** Extraction failure → v1.1 path. Failures are logged but must not cause the search to fail.
- **Concurrency guard.** Two concurrent searches triggering re-extraction for the same candidate ID: one runs, the second waits up to 5 seconds then falls back to v1.1 path. This is materially harder than the other four controls. In a single-process local deployment, an in-process per-candidate lock is acceptable for Phase 1. In any multi-worker or multi-instance deployment, this control requires shared lock coordination or an equivalent cross-request mechanism before the spec can claim it is implemented correctly.

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

| Field | Correctness | Max incorrect rate | Usable coverage | Notes |
|---|---|---|---|---|
| Rank (normalized) | ≥ 85% correct | ≤ 10% incorrect | ≥ 70% not-UNKNOWN | Minimum pilot threshold on 30 sampled resumes, not a confidence-building threshold |
| COC present + valid | — | ≤ 10% incorrect | ≥ 60% not-UNKNOWN | Minimum pilot threshold on 30 sampled resumes, not a confidence-building threshold |
| STCW basic validity | — | ≤ 10% incorrect | ≥ 60% not-UNKNOWN | "Incorrect" means wrong per-certificate state for any of the four core certificates, or a wrong aggregated `stcw_basic_all_valid` result derived from those states |
| US visa extraction | No new false positives or false negatives vs v1.1 | — | — | — |

**Prompt corpus coverage** — validate against sampled real search prompts, not only synthetic examples. For newly introduced Phase 1 families that do not yet have 20 production prompts at launch time, a bootstrap corpus made from recruiter-provided examples, UAT prompts, or staged dry-run prompts is acceptable temporarily. Any such substitute corpus must be labeled as non-production in the sign-off record and replaced with real prompt sampling in the first post-launch review cycle.

| Constraint family | Measurement | Threshold |
|---|---|---|
| Age range prompt parsing | Sample 20 real age-related prompts | ≥ 85% parsed into correct `age_range` constraints, ≤ 10% misparsed |
| Rank prompt parsing | Sample 20 real rank-related prompts | ≥ 80% parsed into correct `rank_match` constraints, ≤ 10% misparsed |
| COC prompt parsing | Sample 20 real COC-related prompts | ≥ 80% parsed into correct `coc_document_gate` constraints, ≤ 10% misparsed |
| STCW basic prompt parsing | Sample 20 real STCW-related prompts | ≥ 80% parsed into correct `stcw_basic` constraints, ≤ 10% misparsed |
| US visa prompt parsing | Sample 20 real visa-related prompts | ≥ 85% parsed into correct `us_visa` constraints, ≤ 10% misparsed |

Prompts that are not yet supported may land in `unapplied_constraints`, but they must not be silently misparsed into the wrong rule family.

**RANK_MATCH launch gate:** if rank usable coverage is below 70% at exit evaluation, RANK_MATCH does not launch as a hard filter. Phase 1 ships without it active. It is re-evaluated once alias table coverage has been improved and the 30-resume sample re-run. This is a pre-launch gate, not a post-deploy soft demotion.

**Filter behavior**

| Criterion | Threshold | Notes |
|---|---|---|
| No unexpected false negatives from RANK_MATCH | ≤ 5% fewer PASS results on folders that ran without rank filter — investigate any decrease before sign-off | — |
| Dual-version routing verified | v1.1 candidates return UNKNOWN (not FAIL) for RANK_MATCH, confirmed by test | — |
| `applied_constraints` contract verified | Searches without rank in applied_constraints produce identical results to pre-Phase-1 baseline | — |
| Partial-evaluation notice surfaced | Searches that hit v1.1 fallback show a user-visible partial-evaluation notice | Confirmed in test or UI verification |
| `FACTUAL_UNKNOWN` review path functional | Candidates with `FACTUAL_UNKNOWN` are surfaced in a working review-required path and are not presented as PASS results | Confirmed in test or UI verification |
| Graceful-failure presentation decided and verified | Queries with no actionable search path show a visible failure warning and do not fall through to unconstrained search | Confirmed in test or UI verification |

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

Phase 2 shortlist monitoring must not be activated until the instrumentation design is explicitly approved for:
- recruiter override-rate capture
- recruiter-facing narrative-quality rating capture
- storage and retrieval of those signals for monitoring review
- the user role or workflow authorized to provide those ratings

### 11.2 Sea service extraction caution

Start with the most structured table formats. Accept UNKNOWN conservatively. Measure extraction accuracy on 30 resumes before wiring MIN_SEA_SERVICE or VESSEL_TYPE into filter rules or scorer.

### 11.3 Phase 2 exit criteria

| Criterion | Measurement | Threshold |
|---|---|---|
| Sea service correctness | Hand-check 30 resumes | ≥ 80% correct (minimum pilot threshold, not a confidence-building threshold) |
| Sea service usable coverage | Same 30 resumes | ≥ 60% not-UNKNOWN (minimum pilot threshold, not a confidence-building threshold) |
| Vessel type correctness | Same 30 resumes | ≥ 80% correct (minimum pilot threshold, not a confidence-building threshold) |
| Vessel type usable coverage | Same 30 resumes | ≥ 60% not-UNKNOWN (minimum pilot threshold, not a confidence-building threshold) |
| Sea service prompt parsing | Sample 20 real sea-service-related prompts | ≥ 80% parsed into correct `min_sea_service` constraints, ≤ 10% misparsed |
| Vessel type prompt parsing | Sample 20 real vessel-type-related prompts | ≥ 80% parsed into correct `vessel_type` constraints, ≤ 10% misparsed |
| Scorer-to-recruiter correlation | Compare heuristic ranking to recruiter ranking on 20 real candidates | Spearman ρ ≥ 0.60 (minimum pilot threshold, not a confidence-building threshold) |
| No score-based false exclusions | Shortlist monitoring results from 50 triggered comparisons | Broader set does not consistently outperform top-N by recruiter override rate |
| Scorer label visible in UI | UI review | Heuristic label on all score surfaces |
| Constraint preview design approved | Product sign-off | Interaction model approved before LLM constraint extraction deploys |
| Shortlist-monitoring instrumentation design approved | Product and developer sign-off | Override-rate capture and narrative-quality rating design approved before shortlist monitoring activates |

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
- `identity.dob` + `derived.age_computed_at`
- `certifications.coc.status`
- `logistics.us_visa_valid`
- `experience.sea_service_by_vessel_type` (list of canonical IDs, not month values)

Pre-retrieval age filtering in Phase 3 must recompute age from `identity.dob` at evaluation time, consistent with the hard filter contract. `derived.age_years` must not be stored in Pinecone metadata and must not be used for pre-retrieval filtering.

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
- **Do not store `derived.age_years` in Pinecone metadata for pre-retrieval filtering.** Pre-retrieval age filtering must use `identity.dob`. Storing `age_years` in metadata would create a path for cached age values to drive eligibility decisions, which conflicts with the hard filter contract.
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
- **Do not use semantic retrieval to determine whether a hard-filter PASS candidate is included in visible results.**
- **Do not route `VERSION_MISMATCH_UNKNOWN` and `FACTUAL_UNKNOWN` to the same path.**
- **Do not silently discard unclassifiable prompt fragments.** Route them to `parsing_notes`.
- **Do not execute an unconstrained search when both the supported-hard-constraints bucket and the semantic bucket are empty after classification.** Fail gracefully with a visible warning instead.

---

*Specification v3.4 — NjordHR Candidate Intelligence Architecture*
*Supersedes: Architecture v3.3 (2026-04-03)*
*Deterministic Filter Engine Spec v2.1 remains authoritative for filter engine internals*
*Status: Approved for implementation — stable working document*
*Date: 2026-04-05*
