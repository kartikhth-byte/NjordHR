# Shadow LLM Compound-Prompt Normalizer — Spec v1

## Status

Active spec. Locked contract for the shadow LLM compound-prompt normalizer. Implementation slices follow the migration discipline established by `coc_country_alias_migration_v1.md`.

This spec defines the wire format, capability catalog, validator, canonicalizer, sanity checker, dispatcher, audit trace, kill switch, and promotion gate. It enumerates one initial family (`vessel_tonnage`). It does not change any live runtime behavior; implementation is gated to a shadow path.

## Purpose

Convert free-text AI Search prompts — including compound prompts with multiple constraints, negation, ranges, units, and soft preferences — into a validated `query_plan.v1` JSON. A deterministic dispatcher then maps that JSON to the existing hard-filter evaluators via a closed registry.

The normalizer is a single LLM call per prompt. Multi-call orchestration is out of scope.

## Goal

- Provide a stable wire format (`query_plan.v1`) and capability catalog that decouples LLM output from internal code.
- Enable per-family shadow evaluation against the existing deterministic parser before any live cutover.
- Preserve the project's deterministic hard-filter contract: PASS / FAIL / UNKNOWN semantics, audit defensibility, kill-switch reversibility.
- Establish a closed-list promotion gate identical in shape to the existing `LLM_Promotion_Stage` discipline.

## Non-goals

- No live cutover of any family in this spec. Promotion is a separate per-family PR.
- No LangGraph or agentic multi-call orchestration in v1.
- LangChain is permitted only behind a provider adapter scoped to the LLM call (structured-output prompting, retries, tracing). It must not be exposed to the validator, canonicalizer, sanity checker, dispatcher, or evaluator logic. The wire format and capability catalog remain framework-independent and the provider adapter is replaceable with the raw Anthropic SDK without changing any downstream contract.
- No exposure of internal Python function names or executor IDs to the LLM.
- No ranking-layer integration. Soft signals are emitted and ignored by the dispatcher in v1.
- No refinement / multi-turn session state. A normalizer call is stateless.

## Vocabulary

- **Prompt** — the recruiter-authored natural-language AI Search string.
- **Filter family** — a closed-list identifier for a class of hard-filter constraints (e.g., `vessel_tonnage`, `coc_country_match`, `age_range`). Family IDs are wire-format contract.
- **Capability catalog** — the JSON file describing each supported family's schema, accepted phrases, and disallowed uses. LLM-facing.
- **Capability registry** — `CAPABILITY_REGISTRY`: the backend mapping from family ID to evaluator callable for every family the catalog declares. Populated at process startup. Backend-private. Never seen by the LLM.
- **Promoted families** — `PROMOTED_FAMILIES`: the subset of `CAPABILITY_REGISTRY` whose constraints are dispatched in live mode. Modified only by promotion PRs. Initially empty.
- **`query_plan.v1`** — the wire format emitted by the LLM, validated by this spec.
- **Shadow mode** — normalizer runs alongside the deterministic parser. Output is recorded as evidence and is not dispatched to evaluators.
- **Live mode** — normalizer output is dispatched. Per-family. Gated by promotion PR with evidence.
- **Source span** — the exact prompt substring that drove a given constraint emission. Required on every constraint.

## Wire format: `query_plan.v1`

The LLM emits a single JSON object conforming exactly to this schema. The Pydantic model is the authoritative definition; this section is the human-readable summary.

```json
{
  "version": "v1",
  "constraints": [
    {
      "filter_family": "<family_id>",
      "parameters": { /* family-specific shape, see catalog */ },
      "source_span": {"text": "<verbatim prompt substring>", "start": 0, "end": 0}
    }
  ],
  "soft_signals": [
    {
      "span": {"text": "<verbatim prompt substring>", "start": 0, "end": 0},
      "hint": "<optional family-qualified hint, e.g. 'engine_experience.include=man_b_w_me_gi'>"
    }
  ],
  "unapplied": [
    {
      "span": {"text": "<verbatim prompt substring>", "start": 0, "end": 0},
      "reason": "<short reason, closed-list: 'no matching capability' | 'out of scope' | 'unclear intent'>"
    }
  ],
  "needs_review": [
    {
      "span": {"text": "<verbatim prompt substring>", "start": 0, "end": 0},
      "candidate_families": ["<family_id>", "<family_id>"],
      "reason": "<short reason>"
    }
  ]
}
```

### Required keys

`version`, `constraints`, `soft_signals`, `unapplied`, `needs_review` are all required at the root. Each is required even when empty (`[]`). The validator rejects payloads missing any required key.

### `version`

Exact string `"v1"`. Any other value is rejected.

### Span object

`span` (and `source_span` on constraints) is an object: `{"text": "<verbatim substring>", "start": <int>, "end": <int>}`.

- `text` — verbatim substring of the input prompt after NFC normalization and whitespace collapse. Non-empty.
- `start` — zero-based character offset into the normalized prompt where `text` begins.
- `end` — zero-based character offset one past the last character of `text`.

The validator enforces that `normalized_prompt[start:end] == text`. Offsets are required so that repeated phrases ("Indian" appearing twice, "above 50k" appearing twice) are unambiguous to audit and review tooling.

### `constraints[]`

Each element requires `filter_family`, `parameters`, `source_span`.

- `filter_family` — string. Must appear in the catalog's closed-enum list. Unknown values are rejected.
- `parameters` — object. Shape is defined by the per-family schema in the catalog.
- `source_span` — span object as defined above.

### `soft_signals[]`

Each element requires `span`. `hint` is optional but, when present, must be in `family_id.field=value` form referencing a family in the catalog.

### `unapplied[]`

Each element requires `span` and `reason`. `reason` must be drawn from the closed list `{"no matching capability", "out of scope", "unclear intent"}`. Unknown reasons are rejected.

### `needs_review[]`

Each element requires `span`, `candidate_families`, and `reason`. `candidate_families` must be a non-empty list of valid family IDs.

## Capability catalog

### Location

`candidate_facts/aliases/filter_capability_catalog.json`. Loaded by a Pydantic-validated loader at `candidate_facts/aliases/filter_capability_catalog.py`, following the loader pattern established by `coc_country.py`.

### Catalog row shape

```json
{
  "family": "<family_id>",
  "purpose": "<single sentence describing what this family matches>",
  "accepted_phrases": ["<example>", "<example>", "<example>"],
  "output_schema": { /* JSON-schema-like description of the parameters object */ },
  "do_not_use_for": ["<explicit anti-example>", "<explicit anti-example>"],
  "plausibility_bounds": { /* per-numeric-field {min, max} bounds; see sanity check contract */ },
  "executor_id": "<opaque backend identifier>"
}
```

### Required keys per row

`family`, `purpose`, `accepted_phrases`, `output_schema`, `do_not_use_for`, `plausibility_bounds`, `executor_id`.

- `family` — closed-list identifier. Must match exactly one entry in `CAPABILITY_REGISTRY`.
- `purpose` — non-empty string. One sentence.
- `accepted_phrases` — non-empty list of at least three verbatim natural-language examples.
- `output_schema` — JSON-schema-shaped object describing the `parameters` field for this family.
- `do_not_use_for` — non-empty list of at least two anti-examples to discourage cross-family misclassification.
- `plausibility_bounds` — object mapping each numeric field in `output_schema` to `{"min": <number>, "max": <number>}`. Required for every numeric field; loader rejects rows where a numeric field in `output_schema` lacks a corresponding bound. Consumed by the sanity checker (see Sanity check contract).
- `executor_id` — opaque string. Backend-private. Stripped from the LLM-facing view of the catalog.

### LLM-facing view vs backend view

The loader exposes two views:

- `llm_facing_catalog()` — returns rows with `executor_id` removed. Used to build the LLM system prompt.
- `backend_catalog()` — returns full rows including `executor_id`. Used to validate dispatch wiring at startup.

### Catalog version

Catalog file carries `version` and `last_updated` fields at the root, matching `coc_country.json` convention. Loader enforces `^\d+\.\d+\.\d+$` for `version` and `^\d{4}-\d{2}-\d{2}$` for `last_updated`.

### Loader validation rules

The loader rejects on any of the following:

- Missing required root key (`version`, `last_updated`, `families`).
- Missing required per-row key.
- Empty `accepted_phrases` or fewer than three entries.
- Empty `do_not_use_for` or fewer than two entries.
- Duplicate `family` ID across rows.
- `executor_id` not present in `CAPABILITY_REGISTRY` at load time. (Note: `PROMOTED_FAMILIES` membership is not checked here. Catalog rows for unpromoted families load successfully so PR-2 can ship the catalog without requiring live dispatch wiring.)
- Any family ID containing characters outside `[a-z0-9_]`.

## Validator contract

The validator runs immediately after Pydantic parses the LLM output.

### Accepts

A `query_plan.v1` payload is accepted only when all of the following hold:

- All required root keys are present and well-typed.
- `version == "v1"`.
- Every `filter_family` in `constraints` and every entry of `candidate_families` in `needs_review` resolves to a known catalog family.
- Every `span` (including `source_span` on constraints, and `span` on `soft_signals`, `unapplied`, `needs_review`) satisfies `normalized_prompt[start:end] == text` with `start < end` and both within `[0, len(normalized_prompt)]`.
- Every `unapplied[].reason` is in the closed list.
- Every `soft_signals[].hint`, when present, is in `family.field=value` form. The `family` must resolve to a catalog family. The `field` must be a property defined in that family's `output_schema`. The `value` must satisfy the field's declared type, the field's enum membership where the schema declares an enum, and the field's `plausibility_bounds` where the field is numeric. Hints that name unknown fields, fail the type check, fall outside an enum, or fall outside `plausibility_bounds` are rejected — soft signals are not a side channel for shape-violating data.
- Every constraint's `parameters` object conforms to the family's `output_schema`.

### Rejects

A payload is rejected — and routed to a per-prompt failure record in the audit ledger — when any of the above fails. The validator does not attempt repair. Failed payloads do not retry by default. (Retry policy is the harness's concern, not the validator's.)

### Does not route to `needs_review` itself

The validator is binary: accept or reject. Routing to `needs_review` is a responsibility of the LLM (via the schema) or the canonicalizer (post-validation). The validator never silently rewrites the payload.

## Canonicalizer contract

The canonicalizer runs only on validator-accepted payloads. It produces a `canonical_query_plan.v1` object that downstream dispatch consumes.

### Responsibilities

- **Unit normalization.** Convert family-specific units to the canonical unit defined in the catalog (e.g., tonnage normalized to GT when the catalog declares GT canonical).
- **Range collapse.** Merge multiple constraints of the same family with non-conflicting parameters into a single constraint where the family schema permits (e.g., two age constraints with min and max merge into one).
- **Multi-value merge.** Merge multiple single-value constraints of the same family into one multi-value constraint when the schema supports it (e.g., two `coc_country_match` constraints with one country each become one with `countries: [...]`).
- **Dedupe.** Remove exact-duplicate constraints.
- **Conflict detection.** When two or more constraints of the same family carry contradictory parameters that cannot be merged (e.g., `coc_country_match` include `["india"]` and exclude `["india"]`), move the conflicting pair to `needs_review` with reason `"conflicting constraints"`.

### Constraints

- The canonicalizer must not invent constraints not present in the LLM output.
- The canonicalizer must not silently drop constraints. Anything removed from `constraints[]` must appear in `needs_review` or `unapplied` of the canonical output.
- The canonicalizer must preserve `source_span` provenance. When merging, the canonical constraint carries a list of source spans.

## Sanity check contract

The sanity checker runs after the canonicalizer. It enforces per-family plausibility bounds defined by the catalog (a `plausibility_bounds` field on each row).

### Default plausibility bounds (v1)

| Family            | Field                       | Bound                |
| ----------------- | --------------------------- | -------------------- |
| `vessel_tonnage`  | `min_tonnage`, `max_tonnage`| `[0, 600000]`        |
| `age_range`       | `min_age`, `max_age`        | `[16, 75]`           |

Additional families add bounds when they enter the catalog. Bounds are required for every numeric field.

### Behavior

- Values inside the bound — constraint stays in `constraints[]`.
- Values outside the bound — constraint moves to `needs_review` with reason `"value out of plausible range"`.
- The sanity checker does not clamp values silently. Out-of-range values always route to review.

## Dispatcher contract

Dispatch consumes `canonical_query_plan.v1` and runs each constraint's evaluator.

### Capability registry

```python
CAPABILITY_REGISTRY: dict[str, Evaluator] = {
    "vessel_tonnage": evaluate_vessel_tonnage,
    # every family the catalog declares lives here, regardless of promotion status
}
```

`CAPABILITY_REGISTRY` is closed at process startup. The catalog loader verifies that every catalog `executor_id` is present in `CAPABILITY_REGISTRY`. Startup fails if any catalog family lacks a backing evaluator.

### Promoted families

```python
PROMOTED_FAMILIES: set[str] = set()  # populated by promotion PRs
```

`PROMOTED_FAMILIES` is the closed subset of `CAPABILITY_REGISTRY` whose constraints are dispatched in live mode. PR-2 ships with `PROMOTED_FAMILIES = set()` and the catalog already populated. Promotion PRs add family IDs to this set; no other PR is allowed to modify it. Startup verifies `PROMOTED_FAMILIES <= CAPABILITY_REGISTRY.keys()`.

### Dispatch loop

```python
def dispatch(plan: CanonicalQueryPlan, candidate: CandidateFacts, mode: NormalizerMode) -> list[HardFilterDecision]:
    if mode != NormalizerMode.LIVE:
        return []  # shadow / deterministic modes do not dispatch via the LLM path
    decisions = []
    for c in plan.constraints:
        if c.filter_family not in PROMOTED_FAMILIES:
            continue  # not promoted; recorded in audit, not dispatched
        evaluator = CAPABILITY_REGISTRY[c.filter_family]
        decisions.append(evaluator(c.parameters, candidate))
    return decisions
```

Each `HardFilterDecision` is `PASS`, `FAIL`, or `UNKNOWN`, matching the existing deterministic hard-filter contract.

### Mode separation

- **Deterministic mode.** The LLM normalizer is not invoked at all. The deterministic parser handles every prompt. No LLM call, no audit record for this prompt.
- **Shadow mode.** The LLM normalizer runs. Output is validated, canonicalized, sanity-checked, and recorded to the LLM audit ledger. The dispatcher is not called. The deterministic parser still drives all live behavior.
- **Live mode.** The LLM normalizer runs and the dispatcher routes constraints whose family ID is in `PROMOTED_FAMILIES`. Unpromoted-family constraints from the canonical plan are recorded but not dispatched, exactly as in shadow mode for those families.

## Audit trace

Every invoked normalizer run emits one audit record.

`deterministic` mode does not invoke the normalizer and emits no `llm_normalizer_audit` row.

### Required fields

- `audit_id` — UUIDv7 or equivalent monotonic ID.
- `timestamp_utc` — ISO 8601, UTC.
- `prompt_raw` — verbatim recruiter input, exactly as received.
- `prompt_normalized` — the NFC-normalized, whitespace-collapsed prompt against which span offsets are anchored. Reviewers replay spans against this field, not against `prompt_raw`.
- `prompt_normalization_version` — semver of the normalization routine used. Incremented whenever the normalization algorithm changes so historical audit records remain replayable.
- `prompt_hash` — SHA-256 of `prompt_raw`.
- `model_id` — exact model string (e.g., `claude-opus-4-7`).
- `catalog_version` — semver from the catalog file.
- `mode` — `"shadow"` or `"live"`. (No `"deterministic"` value: that mode emits no audit row.)
- `raw_llm_output` — verbatim tool-use input block.
- `parsed_payload` — validator output or `null` if validator rejected.
- `validator_result` — `"accepted"` or `"rejected"`.
- `validator_errors` — list of validation error strings, empty when accepted.
- `canonical_payload` — canonicalizer output or `null`.
- `sanity_result` — `"ok"` or `"out_of_range"`.
- `dispatch_outcomes` — list of `HardFilterDecision` records, empty in shadow mode.
- `applied_family_ids` — list of family IDs whose constraints were dispatched.
- `unapplied_phrases` — count and verbatim list.
- `needs_review_phrases` — count and verbatim list.

### Storage

Audit records persist to a dedicated, separate sink named `llm_normalizer_audit` (table, namespace, or CSV file — implementation choice for PR-2). The existing hard-filter decision audit (the per-candidate CSV and any equivalent backends) is not modified. No new columns are added to the existing hard-filter audit CSV. The LLM audit is purely additive and independently versioned.

### Retention

Audit records retained per the project's existing audit retention policy. Privacy review required before LangSmith or any third-party trace export is enabled.

## Kill switch

Env var: `NJORDHR_LLM_NORMALIZER_MODE`. Tri-state.

- `"deterministic"` (default until any family promotes) — the LLM normalizer is not invoked. Deterministic parser handles every prompt. No LLM call, no normalizer audit record.
- `"shadow"` — the LLM normalizer runs, output is recorded to `llm_normalizer_audit`, but the dispatcher is not called and the deterministic parser still drives all live behavior. PR-3's evidence corpus runs in this mode.
- `"live"` — the LLM normalizer runs and the dispatcher routes constraints whose family ID is in `PROMOTED_FAMILIES`. Promotion PRs flip the default to `"live"` only after evidence gate passes.

Unknown values fall back to `"deterministic"` (safe default). Mode parsing strips whitespace and lowercases before matching.

For incident rollback from `"live"` to `"shadow"` without a code change, operators set `NJORDHR_LLM_NORMALIZER_MODE=shadow`. For full disable, set it to `"deterministic"`.

No separate `_SHADOW_ONLY` env var exists; the tri-state subsumes it.

## Versioning

- The wire format is `query_plan.v1`. Catalog rows are versioned independently via the catalog's `version` field.
- Adding a new family is a minor catalog bump, not a wire-format bump.
- Adding a new field to an existing family's `parameters` object is a minor catalog bump if optional, a major bump if required.
- Changing the four-channel structure (adding `ranking_weights`, removing `soft_signals`, etc.) is a wire-format bump to `v2` and follows the migration discipline of `coc_country_alias_migration_v1.md`: PR-1 dual-write, PR-2 runtime switch with kill switch, PR-3 removal.

## Initial family enumeration (v1)

The catalog ships with exactly one family in v1.

### `vessel_tonnage`

- `purpose` — match requested vessel tonnage experience.
- `accepted_phrases` — `["above 50000 GT", "minimum 40000 GRT", "between 30000 and 80000 DWT"]`.
- `output_schema` —
  ```json
  {
    "type": "object",
    "required": ["unit"],
    "properties": {
      "min_tonnage": {"type": ["number", "null"]},
      "max_tonnage": {"type": ["number", "null"]},
      "unit": {"enum": ["gt", "grt", "dwt", "any"]}
    }
  }
  ```
- `do_not_use_for` — `["vessel age", "engine power", "gross salary"]`.
- `plausibility_bounds` — `min_tonnage` and `max_tonnage` in `[0, 600000]`.
- `executor_id` — `"hard_filter.vessel_tonnage.v1"`.

No other families are enumerated in v1. Additional families enter via their own per-family PRs following the rollout below.

## Promotion gate

A family promotes from shadow to live only via a dedicated promotion PR. Plain "agreement against the deterministic baseline" is insufficient because the LLM normalizer is justified partly by cases the deterministic parser misses. The evidence ledger must therefore split metrics across three case classes:

**Class A — deterministic-covered cases.** Prompts the deterministic parser handles correctly today.

- LLM agreement rate with the deterministic parser must be at least 95%.
- LLM disagreement on Class A cases is treated as a regression; the promotion PR must enumerate every disagreement and prove it is not an unsafe widening.

**Class B — deterministic-missed cases.** Prompts the deterministic parser misses today, with human-labeled ground truth.

- LLM correct-classification rate must clear an explicit threshold (recommend at least 70%, set per family in the promotion PR).
- The promotion PR must show measurable recall lift over the deterministic baseline on this class. Zero lift, or negative lift, blocks promotion.

**Class C — adversarial / out-of-scope cases.** Prompts deliberately phrased to mislead (ambiguous, contradictory, off-topic).

- The LLM must route these to `needs_review` or `unapplied`, not to `constraints`. A constraint emission on a Class C case is an unsafe widening and blocks promotion.

Across all classes:

- **Zero unsafe widening.** No case where the LLM emits a hard-filter constraint that would admit candidates the deterministic parser correctly excludes, or that exceeds the recruiter's stated intent.
- **Schema-validation rate at least 99%** across the full corpus.
- **Reviewed false-positive rate below 2%** on the union of Classes A and B, where false-positive is defined as a constraint that survives validation, canonicalization, and sanity check but would produce a wrong dispatch decision in live mode.
- **Corpus size** of at least 200 distinct prompts for the family, with explicit class distribution recorded.
- **Kill-switch test** verifying `NJORDHR_LLM_NORMALIZER_MODE=shadow` reverts the family from live to shadow with no dispatch.

The corpus is append-only. Once a case enters the ledger, it stays. Regressions are annotated, not deleted.

Promotion is binary: a family is in `PROMOTED_FAMILIES` or it is not. No partial promotion.

## Rollout slices

The migration follows the same per-PR discipline as `coc_country_alias_migration_v1.md`.

### PR-1 — this spec

Locks the contracts above. No code changes. Adds this file and any cross-references in `docs/NjordHR_Implementation_Modules_and_Task_Backlog.md`.

### PR-2 — harness

Adds the LLM-call harness (raw Anthropic SDK or a thin provider adapter that uses LangChain internally, per non-goals), validator, canonicalizer, sanity checker, audit recorder, and catalog loader. Adds the catalog file with `vessel_tonnage` only. Adds Pydantic models for `query_plan.v1`. Populates `CAPABILITY_REGISTRY` with `vessel_tonnage` → evaluator. Initializes `PROMOTED_FAMILIES = set()`. Kill switch wired and defaulting to `"deterministic"`. No dispatch. No live behavior change. Scope guard: zero diff to `/analyze`, `/analyze_stream`, fingerprint, existing hard-filter audit CSV columns, telemetry, recovery, frontend. The `llm_normalizer_audit` sink is added as a new, separate audit channel.

### PR-3 — vessel_tonnage shadow corpus

Adds the evidence harness and a fixed evaluation corpus split across Classes A, B, and C as defined in the promotion gate. Runs the corpus in shadow mode (`NJORDHR_LLM_NORMALIZER_MODE=shadow`) and records the evidence ledger. Does not modify dispatch. Does not add anything to `PROMOTED_FAMILIES`.

### PR-4 — vessel_tonnage promotion

Adds `"vessel_tonnage"` to `PROMOTED_FAMILIES`. Flips the deployment default of `NJORDHR_LLM_NORMALIZER_MODE` to `"live"`. `live` mode dispatches only constraints whose family ID is in `PROMOTED_FAMILIES`; constraints for unpromoted families remain audit-only, identical to their shadow-mode behavior. After PR-4, the only family dispatched is `vessel_tonnage`. The env var remains the global kill switch; operators set it back to `"shadow"` or `"deterministic"` at any time without a code change. Adds a kill-switch regression test verifying that `mode=shadow` does not dispatch `vessel_tonnage` and that `mode=deterministic` does not invoke the normalizer at all. `CAPABILITY_REGISTRY` is unchanged by this PR.

### PR-N — next family

Per-family pipeline: catalog row addition, evidence corpus, promotion. One family at a time. Each its own PR.

## Scope guards (per slice)

PR-2, PR-3, and any promotion PR must each independently verify:

- `frontend.html` zero diff (no v1 slice has a UI surface).
- `/analyze` and `/analyze_stream` payload-shape unchanged.
- Fingerprint inputs unchanged.
- Saved/recovered draft serializer unchanged.
- Existing hard-filter audit CSV: zero column additions, zero column renames, zero removals. The `llm_normalizer_audit` sink is a separate channel with its own schema and storage; it does not share rows, columns, or files with the existing hard-filter audit.
- Telemetry events unchanged.
- Hard-filter evaluator branches unchanged.
- Existing deterministic parser untouched in deterministic and shadow modes. In live mode, the deterministic parser still runs for any family not in `PROMOTED_FAMILIES`; promoted families route through the LLM dispatcher instead.

## Open items (not blocking spec lock)

- Where the eval corpus lives and how it is owned. Recommend a separate `eval/` spec.
- Whether LangSmith or an equivalent trace store is adopted for the audit ledger. Requires a privacy review separate from this spec.
- Whether `soft_signals` ever wire to a ranking layer. v1 ignores them at dispatch; future work owns the question.
- Whether multi-turn refinement is supported. v1 is single-call only; refinement is a separate spec.

## Compliance with existing migration discipline

This spec follows the discipline established by `coc_country_alias_migration_v1.md`:

- Set-theoretic mode definitions (shadow vs live, per family).
- Behavior-level parity requirements (LLM output vs deterministic baseline).
- Kill switch via env var.
- Snapshot discipline (the evidence corpus is append-only; regressions are annotated, not deleted).
- Closed-list wording throughout this spec. PR-73 hedge terms are excluded.

## Summary

The shadow LLM compound-prompt normalizer is a single LLM call that emits a four-channel `query_plan.v1` JSON with offset-tagged spans. A closed capability catalog gives the LLM rails; a validator rejects malformed output; a canonicalizer normalizes and detects conflicts; a sanity checker enforces per-family plausibility bounds; a `CAPABILITY_REGISTRY` of all catalog-declared families is loaded at startup, and a separate `PROMOTED_FAMILIES` subset controls which families are dispatched in live mode. The LLM never sees Python function names or executor IDs. A tri-state `NJORDHR_LLM_NORMALIZER_MODE` env var (`deterministic` / `shadow` / `live`) controls runtime behavior and serves as the rollback path. Per-family promotion from shadow to live is gated by a three-class evidence ledger (deterministic-covered, deterministic-missed, adversarial) with explicit thresholds. v1 ships with one family (`vessel_tonnage`); additional families enter through their own per-family PRs following the rollout above. LangGraph and agentic multi-call orchestration are out of scope; LangChain is permitted only behind a provider adapter scoped to the LLM call and must not leak into validation, canonicalization, sanity checking, dispatch, or evaluator logic.
