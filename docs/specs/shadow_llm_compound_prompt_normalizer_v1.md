# Shadow LLM Compound-Prompt Normalizer — Spec v1

## Status

Active spec. Locked contract for the shadow LLM compound-prompt normalizer. Implementation slices follow the migration discipline established by `coc_country_alias_migration_v1.md`.

This spec defines the wire format, capability catalog, provider-scoped helper tools when used by a family, validator, canonicalizer, sanity checker, dispatcher, audit trace, kill switch, and promotion gate. It enumerates catalog-declared families separately from promoted live-dispatch families. Live dispatch is gated by per-family promotion PRs.

## Purpose

Convert free-text AI Search prompts — including compound prompts with multiple constraints, negation, ranges, units, and soft preferences — into a validated `query_plan.v1` JSON. A deterministic dispatcher then maps that JSON to the existing hard-filter evaluators via a closed registry.

The normalizer produces one final `query_plan.v1` per prompt. A provider adapter is permitted to use deterministic helper-tool calls while constructing that final plan, but the backend dispatch contract consumes only the validated `query_plan.v1`.

A prompt-normalization run starts when one recruiter prompt enters the provider
adapter and ends when that adapter returns one final `query_plan.v1` or a
transport/parse failure. Multiple provider turns inside that run are not
agentic orchestration. Cross-run helper loops are prohibited and remain
LangGraph territory.

## Goal

- Provide a stable wire format (`query_plan.v1`) and capability catalog that decouples LLM output from internal code.
- Enable per-family shadow evaluation against the existing deterministic parser before any live cutover.
- Preserve the project's deterministic hard-filter contract: PASS / FAIL / UNKNOWN semantics, audit defensibility, kill-switch reversibility.
- Establish a closed-list promotion gate identical in shape to the existing `LLM_Promotion_Stage` discipline.

## Non-goals

- No live cutover of any family in this spec. Promotion is a separate per-family PR.
- No LangGraph or agentic multi-call orchestration in v1.
- LangChain is permitted only behind a provider adapter scoped to the LLM call (structured-output prompting, retries, tracing, provider-local helper-tool calls). It must not be exposed to the validator, canonicalizer, sanity checker, dispatcher, or evaluator logic. The wire format and capability catalog remain framework-independent and the provider adapter is replaceable with the raw Anthropic SDK without changing any downstream contract.
- No exposure of internal Python function names or executor IDs to the LLM.
- No LLM-callable evaluator, search, database, filesystem, network, audit-write, telemetry-write, or state-mutation tools.
- No ranking-layer integration. Soft signals are emitted and ignored by the dispatcher in v1.
- No refinement / multi-turn session state. A normalizer call is stateless.

## Vocabulary

- **Prompt** — the recruiter-authored natural-language AI Search string.
- **Filter family** — a closed-list identifier for a class of hard-filter constraints (e.g., `vessel_tonnage`, `coc_country_match`, `age_range`). Family IDs are wire-format contract.
- **Capability catalog** — the JSON file describing each supported family's schema, accepted phrases, and disallowed uses. LLM-facing.
- **Capability registry** — `CAPABILITY_REGISTRY`: the backend mapping from family ID to evaluator callable for every family the catalog declares. Populated at process startup. Backend-private. Never seen by the LLM.
- **Promoted families** — `PROMOTED_FAMILIES`: the subset of `CAPABILITY_REGISTRY` whose constraints are dispatched in live mode. Modified only by promotion PRs. Empty until the first promotion PR lands.
- **Provider helper tool** — a deterministic, side-effect-free helper exposed only inside the provider adapter to help construct `query_plan.v1`. Helper tools never dispatch constraints and never call evaluator/search code.
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
| `vessel_tonnage`  | `min_value`, `max_value`    | `[1, 600000]`        |
| `vessel_tonnage`  | `years_back`                | `[0, 50]`            |
| `age_range`       | `min_age`, `max_age`        | `[16, 75]`           |

Additional families add bounds when they enter the catalog. Bounds are required for every numeric field.

### Behavior

- Values inside the bound — constraint stays in `constraints[]`.
- Values outside the bound — constraint moves to `needs_review` with reason `"value out of plausible range"`.
- The sanity checker does not clamp values silently. Out-of-range values always route to review.
- For range-valued tonnage constraints, `min_value == max_value` is valid and means an exact-tonnage requirement.

## Dispatcher contract

Dispatch consumes `canonical_query_plan.v1` and runs each constraint's evaluator.

### Capability registry

```python
CAPABILITY_REGISTRY: dict[str, Evaluator] = {
    "availability": evaluate_availability,
    # every family the catalog declares lives here, regardless of promotion status
}
```

`CAPABILITY_REGISTRY` is closed at process startup. The catalog loader verifies that every catalog `executor_id` is present in `CAPABILITY_REGISTRY`. Startup fails if any catalog family lacks a backing evaluator.

### Promoted families

```python
PROMOTED_FAMILIES: set[str] = {"availability"}  # populated by promotion PRs
```

`PROMOTED_FAMILIES` is the closed subset of `CAPABILITY_REGISTRY` whose constraints are dispatched in live mode. PR-2 ships with `PROMOTED_FAMILIES = set()` and the catalog already populated. The `availability` promotion PR adds the first family to this set after its 200-prompt evidence gate and deterministic enforcement coverage gate pass. Promotion PRs add family IDs to this set; no other PR is allowed to modify it. Startup verifies `PROMOTED_FAMILIES <= CAPABILITY_REGISTRY.keys()`.

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

## Provider helper-tool contract

Helper tools are deterministic normalization aids used inside the provider
adapter before the final `query_plan.v1` is submitted to the backend validator.
They improve exactness for spans, dates, enums, numeric bounds, conflicts, and
family-specific parameter shapes. They do not replace the backend validator,
canonicalizer, sanity checker, or dispatcher.

### Allowed helper tool classes

The v1.1 helper surface is closed to these tool classes:

- `span_locator` — returns exact `{"text", "start", "end"}` spans against
  `prompt_normalized`.
- `date_phrase_parser` — converts a bounded date phrase into an ISO `YYYY-MM-DD`
  date using the supplied reference date.
- `catalog_parameter_checker` — validates family parameters against the
  capability catalog schema, enum values, inactive-field rules, and numeric
  bounds.
- `conflict_classifier` — detects contradictory shapes for a single family and
  returns `constraints`, `needs_review`, or `unapplied` routing guidance.

No other helper tool class is exposed in v1.1.

### Helper tool prohibitions

Helper tools must be side-effect-free and deterministic for a given input. They
must not:

- call hard-filter evaluators or search execution code;
- read or write resumes, databases, files, telemetry, audit sinks, or network
  resources;
- inspect candidate data;
- mutate request state, recovery drafts, sessions, fingerprints, or caches;
- return `executor_id`, Python function names, file paths, stack traces, or
  backend-private identifiers to the LLM.

### Helper tool output contract

Each helper tool returns JSON with exactly these top-level keys:

```json
{
  "tool_id": "<closed helper id>",
  "accepted": true,
  "result": {},
  "errors": []
}
```

- `tool_id` must match the invoked helper.
- `accepted` is boolean.
- `result` is an object. It is empty when `accepted=false`.
- `errors` is a list of strings. It is empty when `accepted=true`.
- Rejected helper output does not repair itself. The final plan must route the
  affected phrase to `needs_review` or `unapplied`.

The final `query_plan.v1` must pass the same backend validation whether helper
tools were used or not. Helper success is not evidence of dispatch safety.

### Helper audit fields

When a provider adapter uses helper tools, the `llm_normalizer_audit` row stores
these additional fields:

- `helper_tool_version` — semver for the helper-tool schema.
- `helper_tool_calls` — ordered list of helper invocations with helper ID,
  input hash, accepted flag, result hash, and errors.
- `helper_tool_call_count` — integer count of helper calls.

The audit stores hashes for helper inputs and results by default. A promotion PR
that needs verbatim helper payload retention must add a privacy review note in
the PR description.

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

After the first promotion PR, the runtime default is `"live"`. Unconfigured
environments without provider credentials must fail closed before any network
request and return a no-dispatch normalizer diagnostic. Local tests and ad-hoc
scripts that require zero provider invocation set
`NJORDHR_LLM_NORMALIZER_MODE=deterministic`.

For incident rollback from `"live"` to `"shadow"` without a code change, operators set `NJORDHR_LLM_NORMALIZER_MODE=shadow`. For full disable, set it to `"deterministic"`.

No separate `_SHADOW_ONLY` env var exists; the tri-state subsumes it.

## Versioning

- The wire format is `query_plan.v1`. Catalog rows are versioned independently via the catalog's `version` field.
- Adding a new family is a minor catalog bump, not a wire-format bump.
- Adding a new field to an existing family's `parameters` object is a minor catalog bump if optional, a major bump if required.
- Tightening `plausibility_bounds` on an existing field is a major catalog bump because it can move previously accepted constraints to `needs_review`. Widening `plausibility_bounds` is a minor catalog bump.
- Changing the four-channel structure (adding `ranking_weights`, removing `soft_signals`, etc.) is a wire-format bump to `v2` and follows the migration discipline of `coc_country_alias_migration_v1.md`: PR-1 dual-write, PR-2 runtime switch with kill switch, PR-3 removal.

## Initial family enumeration (v1)

The catalog ships with exactly one family in v1.

### `availability`

- `purpose` — match recruiter availability requirements against candidate
  availability windows extracted from resumes.
- `accepted_phrases` — `["available immediately", "available by 15 Apr 2026", "available from 1 Sep 2026", "available within 30 days", "available between 1 Apr 2026 and 1 May 2026"]`.
- `output_schema` — discriminated by `value_type` with exactly one active shape:
  `status`, `by_date`, `from_date`, `relative_days`, or `window`.
- `do_not_use_for` — salary expectations, contract length requirements,
  ambiguous locale-specific dates, travel or recovery offsets after sign-off,
  ship-type clauses without an availability requirement, contradictory
  availability instructions, and day-of-week availability.
- `plausibility_bounds` — `relative_days` in `[0, 365]`.
- `executor_id` — `"availability"`.

No other families are enumerated in v1. Additional families enter via their own per-family PRs following the rollout below.

## Family rollout and backfill inventory

This spec starts with `availability` because the availability filter has a
bounded schema, deterministic candidate facts, and a completed 200-prompt
real-LLM evidence artifact. After `availability` completes the harness,
evidence, and promotion sequence, remaining families enter the compound-prompt
normalizer one at a time.

### Current v1 family

- `availability` — first catalog row and first shadow/evidence/promotion
  target for this normalizer.

### Already-promoted rescue families to backfill

These five families are already live through the existing
`LLM_Promotion_Stage` rescue path. The compound-prompt normalizer does not
replace that path in PR-2 or PR-3. Each family enters this normalizer through a
separate backfill PR that adds its catalog row, proves `query_plan.v1` schema
parity against the existing promoted rescue output, and verifies live behavior
remains unchanged until an explicit cutover/promotion PR says otherwise.

A backfill PR adds the family to `CAPABILITY_REGISTRY` and records its
`parsed_payload` for parity against the existing rescue-path output, with the
rescue path continuing to drive live behavior. A cutover PR adds the family to
`PROMOTED_FAMILIES` and routes dispatch through the compound-prompt normalizer.
A later removal PR retires the rescue-path code for that family after the
cutover is verified, mirroring the PR-2 to PR-3 discipline used by
`coc_country_alias_migration_v1.md`.

- `certificate_requirement`
- `rank_match`
- `stcw_basic`
- `us_visa`
- `age_range`

### Future active families

The remaining active hard-filter families enter through later per-family PRs.
Each PR adds exactly one catalog row, evidence corpus, and promotion/backfill
decision.

- `coc_document_gate`
- `coc_country_match`
- `coc_issue_authority_match`
- `coc_grade_match`
- `passport_validity`
- `recent_contract_vessel_experience`
- `engine_experience`
- `engine_vessel_experience`
- `company_continuity`
- `recency`
- `rank_duration_experience`
- `stcw_endorsement`
- `rank_certificate_expectation`
- `experience_ship_type`
- `vessel_tonnage`

### Unsupported / unapplied families

These families stay out of the v1 catalog until a separate design explicitly
promotes them from unsupported/unapplied status.

The analyzer currently classifies these families as `unapplied_constraints`;
no deterministic evaluator exists for them. Moving either family into the
compound-prompt normalizer first requires defining the evaluator and
deterministic baseline, which is out of scope for v1.

- `min_sea_service`
- `vessel_type`

## Promotion gate

A family promotes from shadow to live only via a dedicated promotion PR. Plain "agreement against the deterministic baseline" is insufficient because the LLM normalizer is justified partly by cases the deterministic parser misses. The evidence ledger must therefore split metrics across three case classes:

**Class A — deterministic-covered cases.** Prompts the deterministic parser handles correctly today.

- LLM agreement rate with the deterministic parser must be at least 95%.
- LLM disagreement on Class A cases is treated as a regression; the promotion PR must enumerate every disagreement and prove it is not an unsafe widening.

**Class B — deterministic-missed cases.** Prompts the deterministic parser misses today, with human-labeled ground truth.

- LLM correct-classification rate must clear an explicit threshold (default floor 70%, overridden per family in the promotion PR).
- The promotion PR must show measurable recall lift over the deterministic baseline on this class. Zero lift, or negative lift, blocks promotion.

**Class C — adversarial / out-of-scope cases.** Prompts deliberately phrased to mislead (ambiguous, contradictory, off-topic).

- The LLM must route these to `needs_review` or `unapplied`, not to `constraints`. A constraint emission on a Class C case is an unsafe widening and blocks promotion.

Across all classes:

- **Zero unsafe widening.** No case where the LLM emits a hard-filter constraint that would admit candidates the deterministic parser correctly excludes, or that exceeds the recruiter's stated intent.
- **Schema-validation rate at least 99%** across the full corpus.
- **Reviewed false-positive rate below 2%** on the union of Classes A and B, where false-positive is defined as a constraint that survives validation, canonicalization, and sanity check but would produce a wrong dispatch decision in live mode.
- **Corpus size** of at least 200 distinct prompts for the family, with explicit class distribution recorded.
- **Kill-switch test** verifying `NJORDHR_LLM_NORMALIZER_MODE=shadow` reverts the family from live to shadow with no dispatch.
- **Deterministic enforcement coverage** proving the validator, canonicalizer, sanity checker, and dispatcher can validate the family safely before live dispatch.
- **Helper-tool coverage** for any provider helper tool used by the family. The
  promotion PR must cite tests for accepted output, rejected output, audit
  recording, and final-plan validation after helper use.

The corpus is append-only. Once a case enters the ledger, it stays. Regressions are annotated, not deleted.

Promotion is binary: a family is in `PROMOTED_FAMILIES` or it is not. No partial promotion.

An empty `constraints` array with non-empty `unapplied` or `needs_review` is
not a shadow-mode failure. The deterministic parser still drives live behavior
for that prompt.

### Deterministic enforcement coverage by family

No family is promoted because the LLM can emit valid JSON for it. A family is
promoted only when the deterministic enforcement layers can validate,
canonicalize, sanity-check, and dispatch that family's emitted constraints
safely.

Each promotion PR must include a deterministic enforcement coverage table for
the family being promoted. The table lists the concrete helpers, functions, or
checks used by each enforcement layer:

- `validator` — schema shape, required fields, source spans, type checks, enum
  membership, numeric bounds, and per-family parameter validation.
- `canonicalizer` — vocabulary lookup, alias resolution, date, numeric, unit,
  and enum normalization, conflict routing, and canonical label production.
- `sanity_checker` — plausibility bounds, unsafe-widening guards,
  contradictory-shape routing, and out-of-scope routing.
- `dispatcher` — `PROMOTED_FAMILIES` membership, executor lookup, live-mode
  routing, shadow-mode bypass, deterministic-mode bypass, and kill-switch
  behavior.

The promotion PR must cite direct tests proving the listed enforcement coverage
is exercised by at least one Class A prompt, one Class B prompt, and one Class C
prompt from the append-only corpus. Test citations must name the test function
and file.

- A family-specific helper is required only when the generic helper set cannot
  validate that family safely.

The promotion corpus must include compound prompts that combine the family being
promoted with every family already present in `PROMOTED_FAMILIES` at the time of
evaluation. The required minimum is one cross-family prompt per already-promoted
family, not every pairwise combination of promoted families. These cross-family
prompts must include Class A and Class B cases. Class C cases must include at
least one prompt where another promoted family is valid but the family under
promotion must route to `needs_review` or `unapplied`.

If a family uses provider helper tools, the promotion corpus must include Class
A, Class B, and Class C prompts that trigger at least one accepted helper call
and at least one rejected helper call. Helper-assisted prompts count toward the
same three-class evidence thresholds; there is no separate helper-only promotion
path.

When a cross-family prompt includes a family already using provider helper
tools, that prompt must trigger at least one helper invocation for the
helper-using promoted family. This requirement applies in addition to the
cross-family prompt count above.

#### Enforcement helper registry

The registry below is backend-private documentation. It is not part of the
LLM-facing capability catalog, and `executor_id` values are still hidden from
the LLM.

| helper_id | layer | families using | contract |
| --- | --- | --- | --- |
| `source_span_exact_replay` | validator | `vessel_tonnage`, `availability` | Verifies `normalized_prompt[start:end] == text`; repeated text requires exact offsets and is not guessed. |
| `catalog_parameter_validator` | validator | `vessel_tonnage`, `availability` | Validates emitted parameters against the catalog `output_schema`, enums, numeric ranges, and per-family required-field rules. |
| `date_phrase_normalizer` | canonicalizer | `availability` | Converts supported date phrases to ISO dates using the prompt reference date; unsupported or ambiguous dates route to review. |
| `numeric_unit_normalizer` | canonicalizer | `vessel_tonnage` | Converts numeric quantity and unit text into canonical numeric fields and unit enums. |
| `plausibility_bounds_checker` | sanity_checker | `vessel_tonnage`, `availability` | Enforces catalog `plausibility_bounds`; out-of-range values route to review. |
| `promoted_family_dispatch_gate` | dispatcher | all promoted families | Dispatches only when mode is `live` and the family is present in `PROMOTED_FAMILIES`; shadow and deterministic modes do not dispatch. |

Future promotion PRs add rows when they introduce new generic helpers or
family-specific helpers. Reused helpers update the `families using` cell in the
same PR that relies on them.

Changes to a generic helper require each family listed in that helper's
`families using` cell to re-cite Class A, Class B, and Class C test evidence in
the change PR.

#### Provider helper-tool registry

The provider helper-tool registry is LLM-visible only inside the provider
adapter. It is separate from `CAPABILITY_REGISTRY`, `PROMOTED_FAMILIES`, and the
backend-private enforcement helper registry above.

| tool_id | class | families using | contract |
| --- | --- | --- | --- |
| `locate_prompt_span.v1` | `span_locator` | `availability` pilot | Returns exact span offsets for one requested phrase in `prompt_normalized`; repeated or missing phrase returns `accepted=false`. |
| `parse_availability_date_phrase.v1` | `date_phrase_parser` | `availability` pilot | Converts supported availability date phrases to ISO dates using `resolved_reference_date`; ambiguous numeric dates, day-of-week-only phrases, and unsupported formats return `accepted=false`. |
| `check_availability_parameters.v1` | `catalog_parameter_checker` | `availability` pilot | Validates availability parameters against the catalog schema, inactive-field rules, date validity, reversed-window rules, and `relative_days` bounds. |
| `classify_availability_conflict.v1` | `conflict_classifier` | `availability` pilot | Routes contradictory or out-of-scope availability phrasing to `needs_review` or `unapplied` guidance; never returns a dispatchable constraint. |

Tool IDs are stable wire identifiers. They are not Python function names.
Changing a tool's input shape, output shape, or semantics requires a major tool
version bump and a new evidence run for every family listed in `families using`.

#### Availability promotion enforcement citations

The `availability` promotion cites these deterministic enforcement tests:

- `tests/test_availability_normalizer_evidence.py::AvailabilityNormalizerEvidenceTests::test_query_plan_fixture_rejects_bad_span` for `source_span_exact_replay`.
- `tests/test_filter_capability_catalog.py::FilterCapabilityCatalogTests::test_availability_schema_accepts_all_value_types` and `tests/test_filter_capability_catalog.py::FilterCapabilityCatalogTests::test_availability_schema_rejects_invalid_dates_and_reversed_window` for `catalog_parameter_validator`.
- `tests/test_filter_capability_catalog.py::FilterCapabilityCatalogTests::test_availability_schema_rejects_relative_days_out_of_bounds` for `plausibility_bounds_checker`.
- `tests/test_compound_prompt_normalizer_runtime.py::CompoundPromptNormalizerRuntimeTests::test_deterministic_mode_does_not_invoke_provider_or_dispatch`, `tests/test_compound_prompt_normalizer_runtime.py::CompoundPromptNormalizerRuntimeTests::test_shadow_mode_invokes_provider_but_does_not_dispatch`, and `tests/test_compound_prompt_normalizer_runtime.py::CompoundPromptNormalizerRuntimeTests::test_live_mode_dispatches_valid_promoted_availability_constraint` for `promoted_family_dispatch_gate`.
- `tests/test_ai_analyzer_job_constraints.py::AIAnalyzerJobConstraintTests::test_live_compound_normalizer_needs_review_suppresses_deterministic_availability_fallback` for Class C unsafe-widening suppression after a validator-accepted `needs_review` plan.

## Rollout slices

The migration follows the same per-PR discipline as `coc_country_alias_migration_v1.md`.

### PR-1 — this spec

Locks the contracts above. No code changes. Adds this file and any cross-references in `docs/NjordHR_Implementation_Modules_and_Task_Backlog.md`.

### PR-2 — harness

Adds the LLM-call harness (raw Anthropic SDK or a thin provider adapter that uses LangChain internally, per non-goals), validator, canonicalizer, sanity checker, audit recorder, and catalog loader. Adds the initial catalog file with `availability`. Adds Pydantic models for `query_plan.v1`. Populates `CAPABILITY_REGISTRY` with `availability` -> evaluator. Initializes `PROMOTED_FAMILIES = set()`. Kill switch wired and defaulting to `"deterministic"`. No dispatch. No live behavior change. Scope guard: zero diff to `/analyze`, `/analyze_stream`, fingerprint, existing hard-filter audit CSV columns, telemetry, recovery, frontend. The `llm_normalizer_audit` sink is added as a new, separate audit channel.

### PR-3 — availability shadow corpus

Adds the evidence harness and a fixed evaluation corpus split across Classes A, B, and C as defined in the promotion gate. Runs the corpus in shadow mode (`NJORDHR_LLM_NORMALIZER_MODE=shadow`) and records the evidence ledger. Does not modify dispatch. Does not add anything to `PROMOTED_FAMILIES`.

### PR-4 — availability promotion

Adds `"availability"` to `PROMOTED_FAMILIES`. Flips the deployment default of `NJORDHR_LLM_NORMALIZER_MODE` to `"live"`. `live` mode dispatches only constraints whose family ID is in `PROMOTED_FAMILIES`; constraints for unpromoted families remain audit-only, identical to their shadow-mode behavior. After PR-4, the only family dispatched is `availability`. The env var remains the global kill switch; operators set it back to `"shadow"` or `"deterministic"` at any time without a code change. Adds a kill-switch regression test verifying that `mode=shadow` does not dispatch `availability` and that `mode=deterministic` does not invoke the normalizer at all. `CAPABILITY_REGISTRY` is unchanged by this PR.

The PR-4 evidence artifact is `docs/eval-evidence/availability-shadow-normalizer-llm-evidence-2026-06-30.json`: 200 prompts with class distribution A=80, B=80, C=40; schema-valid rate 0.99 (198/200); unsafe widening count 0; Class A LLM match rate 1.0; Class B correct rate 0.9625 with deterministic baseline 0.0 and recall lift 0.9625; Class C safe-route rate 1.0; reviewed false-positive rate 0.0; promotion gate `passes=true`.

### PR-5 — tool-assisted availability pilot

Adds the provider helper-tool implementation for `availability` using only the
four registered v1 helper tools. Does not modify `PROMOTED_FAMILIES`; `availability`
is already promoted. Does not expose evaluator, search, database, filesystem,
network, audit-write, telemetry-write, or state-mutation tools. Re-runs the
availability evidence corpus with helper tools enabled and records helper audit
fields. The evidence artifact must compare JSON-only and tool-assisted results
for schema-valid rate, unsafe widening, Class A match rate, Class B correct
rate, Class B recall lift, Class C safe-route rate, reviewed false-positive
rate, helper accepted count, and helper rejected count.

The PR-5 scope guard is: zero diff to `/analyze`, `/analyze_stream`, request
fingerprints, recovery drafts, existing hard-filter audit CSV columns,
telemetry events, frontend, hard-filter evaluator branches, and
`PROMOTED_FAMILIES`.

The PR-5 fixture artifact is
`docs/eval-evidence/availability-helper-tool-pilot-fixture-evidence-2026-06-30.json`.
It records helper-tool plumbing on the 200-prompt corpus without invoking the
LLM: schema-valid rate 1.0 (200/200), unsafe widening count 0, Class A fixture
match rate 1.0, Class B correct rate 1.0 with recall lift 1.0, Class C
safe-route rate 1.0, helper accepted count 464, helper rejected count 60, and
promotion gate `passes=false` with only `real_llm_run_required` remaining.

The PR-5 real-LLM comparison uses three artifacts:

- `docs/eval-evidence/availability-normalizer-json-only-llm-evidence-2026-06-30.json`: JSON-only baseline, 200 prompts, schema-valid rate 1.0, unsafe widening count 0, Class A LLM match rate 0.9875, Class B correct rate 1.0, Class B recall lift 1.0, Class C safe-route rate 1.0, reviewed false-positive rate 0.0, helper accepted count 0, helper rejected count 0, promotion gate `passes=true`.
- `docs/eval-evidence/availability-normalizer-helper-tools-llm-evidence-2026-06-30.json`: first helper-assisted run, 200 prompts, schema-valid rate 0.995, unsafe widening count 0, Class A LLM match rate 0.8, Class B correct rate 0.75, Class B recall lift 0.75, Class C safe-route rate 0.975, reviewed false-positive rate 0.0, helper accepted count 464, helper rejected count 60, promotion gate `passes=false`. The failure was prompt/context induced: helper results caused display-value shortening and one Class C invalid route. This failed artifact remains part of the durable record.
- `docs/eval-evidence/availability-normalizer-helper-tools-prompt-fix-llm-evidence-2026-06-30.json`: prompt-fixed helper-assisted run, 200 prompts, schema-valid rate 1.0, unsafe widening count 0, Class A LLM match rate 1.0, Class B correct rate 1.0, Class B recall lift 1.0, Class C safe-route rate 1.0, reviewed false-positive rate 0.0, helper accepted count 464, helper rejected count 60, promotion gate `passes=true`.

Helper adoption for `availability` requires the prompt-fixed helper run to keep
unsafe widening at 0, keep schema-valid rate and Class C safe-route rate at
least equal to the JSON-only baseline, keep Class B correct rate at least
0.95 times the JSON-only baseline, and improve at least one of schema-valid
rate, Class A match rate, or Class B correct rate by at least 0.01 without
regressing any of the others. If the helper run does not meet that kill
criterion, helper use for `availability` is abandoned and JSON-only remains the
provider path. The prompt-fixed run meets the rule by improving Class A from
0.9875 to 1.0, a +0.0125 delta, while keeping schema-valid rate, Class B
correct rate, Class C safe-route rate, reviewed false-positive rate, and unsafe
widening unchanged.

The evidence schema records `failure_reason` and `quality_failure_class` on
each case result and each LLM audit record. The summary also records
`failure_reason_counts` and `quality_failure_class_counts` so schema failures,
display-value-only failures, parameter mismatches, and Class C unsafe routes
remain grep-able after each rerun.

### PR-6 — vessel_tonnage catalog row

Adds `vessel_tonnage` to the capability catalog with `version`, `value_type`,
`min_value`, `max_value`, `unit`, `years_back`, and `display_value` parameters.
The row remains unpromoted: `PROMOTED_FAMILIES` stays `{"availability"}` and
live dispatch remains unchanged. The catalog validator enforces inactive-field
rules, `min_value <= max_value`, unit enum membership, and plausibility bounds
for `min_value`, `max_value`, and `years_back`.

This PR does not add a `vessel_tonnage` provider prompt, evidence corpus,
Gemini run, helper-tool adoption, dispatcher branch, `/analyze` payload change,
frontend change, telemetry field, CSV column, or durable audit-event field.

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
- Where `model_id` is pinned (spec-level constant, PR-2 config, or env var) is deferred to PR-2.
- LLM call failure modes (timeout, transport error, structured-output parse failure) and per-mode fallback behavior are deferred to PR-2.
- Mixed-language prompts, right-to-left text, and context-window-overflow behavior are deferred to a later normalizer robustness spec.
- Whether LangSmith or an equivalent trace store is adopted for the audit ledger. Requires a privacy review separate from this spec.
- Whether `soft_signals` ever wire to a ranking layer. v1 ignores them at dispatch; future work owns the question.
- Whether multi-turn refinement is supported. v1.1 helper tools operate inside
  one prompt-normalization run only; refinement is a separate spec.

## Compliance with existing migration discipline

This spec follows the discipline established by `coc_country_alias_migration_v1.md`:

- Set-theoretic mode definitions (shadow vs live, per family).
- Behavior-level parity requirements (LLM output vs deterministic baseline).
- Deterministic enforcement coverage requirements across validator,
  canonicalizer, sanity checker, and dispatcher.
- Kill switch via env var.
- Snapshot discipline (the evidence corpus is append-only; regressions are annotated, not deleted).
- Closed-list wording throughout this spec. PR-73 hedge terms are excluded.

## Summary

The shadow LLM compound-prompt normalizer emits a four-channel `query_plan.v1` JSON with offset-tagged spans. A closed capability catalog gives the LLM rails; provider-scoped helper tools improve span, date, parameter, and conflict exactness when a family uses them; a validator rejects malformed output; a canonicalizer normalizes and detects conflicts; a sanity checker enforces per-family plausibility bounds; a `CAPABILITY_REGISTRY` of all catalog-declared families is loaded at startup, and a separate `PROMOTED_FAMILIES` subset controls which families are dispatched in live mode. The LLM never sees Python function names, executor IDs, or evaluator/search tools. A tri-state `NJORDHR_LLM_NORMALIZER_MODE` env var (`deterministic` / `shadow` / `live`) controls runtime behavior and serves as the rollback path. Per-family promotion from shadow to live is gated by a three-class evidence ledger (deterministic-covered, deterministic-missed, adversarial), explicit metric thresholds, deterministic enforcement coverage across validator, canonicalizer, sanity checker, and dispatcher, and helper-tool coverage whenever helper tools are used. v1 live dispatch ships with one promoted family (`availability`); additional catalog-declared families enter live dispatch only through their own per-family promotion PRs following the rollout above. LangGraph and agentic orchestration are out of scope; LangChain is permitted only behind a provider adapter scoped to the LLM call and must not leak into validation, canonicalization, sanity checking, dispatch, or evaluator logic.
