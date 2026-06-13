# Experience Filters v1

## Goal

Let recruiters express filters against the candidate's sea-service history
with per-criterion recency. The current filters apply to the entire
resume, which means a candidate with a single decade-old tanker contract
matches a `"tanker"` filter the same way as a candidate with five recent
tanker contracts. Recruiters want to distinguish those cases.

v1 introduces three changes:

- **Experienced Ship Type**: today's single-select picker becomes a
  multi-select with a recency picker per row. The existing
  `experience_ship_type` hard-filter family is extended; the
  `recent_contract_vessel_experience` family is folded into it and
  deprecated.
- **Engine Experience**: a new multi-select picker mirroring the ship
  type shape. The existing `engine_experience` hard-filter family is
  extended to support per-item recency.
- **Vessel Tonnage**: the existing tonnage filter (PR #39/#40) gains a
  single optional `years_back` field. Null means "any time"
  (backward-compatible with PR #39's constraint shape).

All recency is measured from today's date, not the candidate's last
sign-off. That matches the dominant recruiter intent ("active candidates
with recent X") and pairs naturally with availability filtering.

## Phase 1 Scope

Includes:

- Multi-select UI for Experienced Ship Type with per-item recency and
  per-item `minimum_months`.
- Multi-select UI for Engine Experience with per-item recency. (Engine
  canonical list reuses the existing in-code alias map; no new alias
  file in v1.)
- A single optional `years_back` field on the existing Vessel Tonnage
  filter.
- Schema and evaluator updates to accept the new multi-select shapes.
- Backward-compatible translation of the legacy single-select
  `experience_ship_type` shape and the legacy
  `recent_contract_vessel_experience` constraints to the new shape.
- A new "evidence scoper" concept: when an item has a recency window,
  the evaluator restricts which contract rows it considers to those
  whose date range overlaps the window.
- Updated prompt parser and shadow LLM emit the new shape instead of
  legacy shapes.
- Deprecation of the `recent_contract_vessel_experience` family with a
  documented transition window.

Excludes (deferred to later phases):

- AND match mode for multi-select. v1 is `any_of` only. AND lands later
  if anyone asks.
- Multi-row Vessel Tonnage (e.g., "≥50k in last 3 years OR ≥80k in
  last 5 years"). Single tonnage range per search in v1.
- Recruiter-driven manual present_rank tagging from the UI (unchanged
  from `search_pickers_v1.md`; explicitly **not** on the Phase 2
  roadmap either).
- Persisted on-disk index for recency lookups.

Note on contract-count recency: v1 **does** ship `contract_count` as a
per-item alternative to `years_back`. See "Contract Count Semantics"
below for the locked behavior.

## Architectural Principles

Two principles cut across this spec and `search_pickers_v1.md`.

- **Pickers are the structured source of truth.** When a multi-select
  filter has any value, free-form prompt phrases that would emit the
  same constraint are suppressed. Pickers win.
- **Evidence scopers narrow what hard filters see, never silently drop
  candidates.** A candidate with no dated contracts in the recency
  window appears in a "Needs review" bucket if the window would have
  been the deciding factor. They do not vanish silently.

## Locked Decisions

| Decision | Value |
|---|---|
| Recency reference point | From today's date |
| Filters that get recency | Experienced Ship Type, Engine Experience, Vessel Tonnage |
| Filters that do NOT get recency | Applied Rank, Present Rank, Applied Ship Type, CoC Issue Authority |
| Multi-select with per-item recency | Experienced Ship Type, Engine Experience |
| Single picker + single recency | Vessel Tonnage |
| Match mode (within a family) | `any_of` (OR) only in v1; AND deferred |
| `minimum_months` semantics | Per item (Option A): "at least N months of X experience within the last Y years" |
| Engine canonical list source | Existing in-code engine alias mapping; no new JSON alias file |
| `recent_contract_vessel_experience` family | Deprecated; subsumed by enhanced `experience_ship_type` |
| Legacy constraint shape | Auto-translated at schema-validation time to the new shape with one item |
| Boundary handling | Contract is in-window if any part of its date range overlaps the window |
| Undated contracts (date window) | Excluded from windowed evaluation; surfaced in "Needs review" when load-bearing |
| Per-item recency parameterization | Either `years_back` or `contract_count`; exactly one per item |
| Mutual-exclusion scope | Per filter row, not per search. Different rows in the same multi-select may use different parameterizations independently |
| Undated contracts (count window) | If any of a candidate's most-recent N contracts (within the `contract_count` window) is undated, candidate goes to "Needs review" rather than being evaluated against an incomplete chronology |
| Duplicate filter rows | Backend dedupes silently before evaluation; response includes a non-blocking warning; structured log line emitted |

## Recency Semantics — Evidence Scoper Pattern

The recency window is not a new hard-filter family; it modifies how
existing families evaluate their evidence.

### Reference window

The window is defined by exactly one of `years_back` or `contract_count`
per item (see "Contract Count Semantics" below). Both fields null/absent
on an item means "no window" — the evaluator considers all contract rows
on the candidate's resume. Both fields non-null on an item is a
validation error (`*_RECENCY_AMBIGUOUS`); see Validation Errors.

For a `years_back: N` value, the window is `[today - N years, today]`,
inclusive at both ends. `today` is the date the search request is
processed (not when the index was built).

For a `contract_count: N` value, the window is the candidate's N most
recent contracts ranked chronologically by end date (or by start date
if end date is missing but start date is present). Undated contracts
in the window send the candidate to "Needs review"; see "Contract
Count Semantics" for the exact rule.

### Boundary handling

A contract row is **in window** if any part of its date range overlaps
the window. Specifically:

- Contract end date in window: in window.
- Contract start date in window: in window.
- Contract spans the window (starts before, ends after): in window.
- Contract entirely before window: out.
- Contract entirely after window (rare, future-dated): out.

If a contract has a start date but no end date (ongoing employment),
it is treated as ending today.

If a contract has neither start nor end date, see "Undated contracts"
below.

### Per-family scoping

When an item has a recency window (set by either `years_back: N` or
`contract_count: N`):

- **`experience_ship_type`**: the evaluator iterates only the
  in-window contract rows when checking the ship family match and
  computing `minimum_months` accumulation.
- **`engine_experience`**: the evaluator iterates only in-window
  contract rows when checking engine family match.
- **`vessel_tonnage`**: `_collect_vessel_tonnage_evidence` (added in
  PR #41) filters its result to in-window evidence before passing to
  the min/max/unit logic. Note: `vessel_tonnage` accepts `years_back`
  only — `contract_count` is not supported for tonnage in v1
  (single-row filter, count parameterization would add little signal).

For all three, the existing reason codes (`MATCH`, `MISMATCH`,
`NOT_FOUND`, etc.) remain semantically the same; the difference is the
set of rows the evaluator looked at.

### `minimum_months` accumulation

`minimum_months` is computed by summing contract durations across the
in-window contract rows that also match the item's ship family or
engine family. If a contract straddles the window boundary, only the
portion inside the window counts toward the sum.

Example: window is last 3 years (2023-06-12 to 2026-06-12), candidate
has an Oil Tanker contract from 2023-01-01 to 2023-10-01. The
in-window portion is 2023-06-12 to 2023-10-01 = approximately 3.6
months. That counts toward `minimum_months`.

For implementation simplicity, contract durations may be calculated in
whole calendar months rounded down. Implementations should document the
rounding policy in audit output.

### Undated contracts

A candidate may have one or more contract rows where the date fields
are empty or unparseable. Behavior depends on which recency
parameterization the item uses.

**`years_back` window:**

- If the candidate has at least one **dated** in-window contract that
  satisfies the filter: PASS, regardless of undated contracts.
- If the candidate has no dated in-window contracts that satisfy, but
  has undated contracts that would have satisfied if dated: the
  candidate appears in a **"Needs contract date review"** bucket
  alongside the search results. They are not silently dropped.
- If the candidate has no contracts that satisfy regardless of dates:
  FAIL (or UNKNOWN if no contract evidence at all).

**`contract_count` window:**

`contract_count` only works if we can chronologically order a
candidate's contracts. To pick the "last N contracts," every contract
that *could* fall inside the window must be datable.

- Sort the candidate's contracts by end date (or start date if end
  date is missing) descending; the top N are the count window.
- If any of those N contracts is **undated**: the candidate appears in
  the "Needs contract date review" bucket. The evaluator does not
  guess at ordering and does not silently skip the undated rows. This
  is consistent with the age-filter UNKNOWN pattern — when the
  deterministic input is ambiguous, we do not let the evaluator infer.
- If all N contracts in the window are dated: evaluate the item
  against those rows exactly as with the `years_back` path.
- If the candidate has fewer than N total datable contracts: the
  window is "all contracts available." Pass/fail evaluated against
  that smaller set. (This is not Needs Review — the chronology is
  unambiguous, just shorter than requested.)

The "Needs contract date review" bucket is surfaced when the search
involves any per-item recency (years_back or contract_count). The
bucket lists the candidate and the specific filter that would have
matched, or could not be evaluated, because of undated contracts.

## Experienced Ship Type — Detailed Spec

### Schema

```json
{
  "type": "experience_ship_type",
  "items": [
    {
      "ship_family": "oil_tanker",
      "years_back": 3,
      "contract_count": null,
      "minimum_months": 6
    },
    {
      "ship_family": "container",
      "years_back": null,
      "contract_count": 3,
      "minimum_months": null
    },
    {
      "ship_family": "bulk_carrier",
      "years_back": null,
      "contract_count": null,
      "minimum_months": null
    }
  ],
  "match_mode": "any_of"
}
```

Required top-level fields: `type` (literal `"experience_ship_type"`),
`items` (non-empty list), `match_mode` (literal `"any_of"` in v1).

Required per-item fields: `ship_family` (canonical ship family ID from
existing alias list). Optional per-item: `years_back` (positive int or
null), `contract_count` (positive int or null), `minimum_months`
(positive int or null).

**Recency mutual exclusion (per item):** at most one of `years_back`
or `contract_count` may be non-null on a single item. Both null on the
same item means "no window" (all contracts considered). Both non-null
is a validation error. Different items in the same `items` array may
independently use different parameterizations.

Schema validation rejects:

- Empty `items` list.
- Unknown `ship_family` (not in the canonical alias list).
- `years_back <= 0` or non-integer.
- `contract_count <= 0` or non-integer.
- Both `years_back` and `contract_count` non-null on the same item.
- `minimum_months <= 0` or non-integer.
- `match_mode` other than `any_of`.

**Duplicate items (locked):** items with identical normalized field
sets (same `ship_family` + `years_back` + `contract_count` +
`minimum_months`) are **silently deduped** before evaluation. The
response includes a non-blocking warning entry:

```json
{
  "warnings": [
    {
      "code": "duplicate_filter_rows",
      "message": "Duplicate filter ignored",
      "removed_count": 1
    }
  ]
}
```

The UI renders dismissible warning banners above the result list when
`warnings[]` is non-empty. The backend emits a structured log line —
`event=deduped_filter_rows search_id=<id> family=experience_ship_type
removed_count=<N>` — so duplicates can be grepped for if they start
appearing systematically. Same dedup rule applies to
`engine_experience` items.

### Evaluator semantics

Candidate is evaluated by checking each item in `items` independently.
The candidate passes if **any** item passes (`any_of`).

Per item:

1. Identify in-window contract rows:
   - If `years_back: N` is set, use rows in the date window
     `[today - N years, today]`.
   - If `contract_count: N` is set, sort the candidate's contracts
     chronologically (end date desc, falling back to start date) and
     use the top N. If any of those N rows is undated, the candidate
     goes to `NEEDS_DATE_REVIEW` for this item (do not silently
     proceed with a partial chronology).
   - If both are null, use all rows.
2. Filter to rows matching the item's `ship_family`.
3. If `minimum_months` is set: sum the in-window durations of those
   rows; pass the item if the sum ≥ `minimum_months`.
4. If `minimum_months` is null: pass the item if at least one row
   matches.

Reason codes (per item, surfaced in audit detail):

- `EXPERIENCE_SHIP_TYPE_ITEM_MATCH` — pass.
- `EXPERIENCE_SHIP_TYPE_ITEM_BELOW_MINIMUM_MONTHS` — matched but
  duration insufficient.
- `EXPERIENCE_SHIP_TYPE_ITEM_NOT_FOUND` — no in-window rows matched.
- `EXPERIENCE_SHIP_TYPE_ITEM_NEEDS_DATE_REVIEW` — undated contracts
  could have matched.

Top-level decision:

- PASS if any item is `MATCH`.
- UNKNOWN if no item is `MATCH` but at least one is
  `NEEDS_DATE_REVIEW` and none is `MATCH`.
- FAIL otherwise.

### UI

Replaces the existing single-select "Experienced Ship Type" picker.

Component shape:

- Header: `Experienced Ship Type` (label) + `+ Add` button.
- Each row is rendered as:
  - Ship type dropdown (canonical ship families).
  - **Recency mode radio toggle** (per row, independent across rows):
    - `Recent by years` → enables a recency dropdown (`Any time`,
      `Last 1 year`, `Last 2 years`, `Last 3 years`, `Last 5 years`,
      `Last 10 years`). Emits `years_back`.
    - `Recent by contracts` → enables a number input
      (`Last N contracts`, default 3, min 1). Emits `contract_count`.
    - `Any time` (default) → neither field emitted (both null).
    - Whichever option is not selected has its input greyed out;
      switching modes clears the unselected input so only one field
      is non-null on submit.
  - Minimum months number input (optional).
  - Remove button.
- Empty state (no rows): no filter applied; helper text reads
  `"No ship type filter."`.
- Disabled during refinement (`disabled={refinementMode}`), matching
  the rest of the search-context lockdown pattern.

Helper text below the component:

```text
Matches candidates who have sea-service experience on any of the selected
ship types within the selected recency window. Recruiters can combine
multiple ship types with different windows; a candidate passes if they
match any one row.
```

### Backward compatibility — legacy single-select

The legacy shape:

```json
{
  "type": "experience_ship_type",
  "ship_family": "tanker",
  "minimum_months": 6
}
```

is auto-translated at schema-validation time to:

```json
{
  "type": "experience_ship_type",
  "items": [{"ship_family": "tanker", "years_back": null, "minimum_months": 6}],
  "match_mode": "any_of"
}
```

The legacy shape is accepted during the deprecation window. New code
(prompt parser, shadow LLM, UI) emits the new shape. After the
deprecation window closes, the legacy shape is rejected.

### Backward compatibility — `recent_contract_vessel_experience`

The legacy shape:

```json
{
  "type": "recent_contract_vessel_experience",
  "ship_family": "tanker",
  "recent_contract_count": 3
}
```

is translated to a single-item `experience_ship_type` with
`contract_count` set to the legacy `recent_contract_count` value (a
clean 1:1 translation now that v1 supports `contract_count`
natively). The legacy heuristic (rough mapping to `years_back`) is
not needed.

```json
{
  "type": "experience_ship_type",
  "items": [{
    "ship_family": "tanker",
    "years_back": null,
    "contract_count": 3,
    "minimum_months": null
  }],
  "match_mode": "any_of"
}
```

## Engine Experience — Detailed Spec

### Schema

```json
{
  "type": "engine_experience",
  "items": [
    {
      "engine_family": "man_b_w_me_c",
      "years_back": 3,
      "contract_count": null,
      "minimum_months": null
    },
    {
      "engine_family": "wartsila_rt_flex",
      "years_back": null,
      "contract_count": 2,
      "minimum_months": 6
    }
  ],
  "match_mode": "any_of"
}
```

Same field semantics as `experience_ship_type`, but the canonical key
is `engine_family` and the canonical list is the existing in-code
engine alias mapping (no new JSON file in v1).

Schema validation rules mirror `experience_ship_type` exactly,
including: mutual exclusion of `years_back` and `contract_count` per
item, silent dedup of identical items with a non-blocking warning,
and the structured `event=deduped_filter_rows` log line.

### Evaluator semantics

Mirrors `experience_ship_type` evaluator exactly, substituting
"engine family" for "ship family." Reason codes:

- `ENGINE_EXPERIENCE_ITEM_MATCH`
- `ENGINE_EXPERIENCE_ITEM_BELOW_MINIMUM_MONTHS`
- `ENGINE_EXPERIENCE_ITEM_NOT_FOUND`
- `ENGINE_EXPERIENCE_ITEM_NEEDS_DATE_REVIEW`

Top-level decision combines the same way as `experience_ship_type`.

### UI

New picker, positioned in the search form near the Experienced Ship
Type picker. Same multi-select-with-per-item-recency component pattern.

Helper text:

```text
Matches candidates who have sea-service experience on any of the
selected engine families within the selected recency window. Engine
canonical list is sourced from the in-code engine alias mapping.
```

### Engine canonical list

Sourced at module load time from the existing alias mapping in
`ai_analyzer.py` (the same mapping used by the existing
`engine_experience` family for matching). The dropdown displays the
canonical IDs; future work can attach human-readable display labels
analogous to the CoC issue authority pattern in
`search_pickers_v1.md`.

### Backward compatibility — legacy single engine

The legacy shape:

```json
{
  "type": "engine_experience",
  "engine_family": "wartsila_rt_flex",
  "minimum_months": 6
}
```

is auto-translated at schema-validation time to the single-item
multi-select shape. Same deprecation window as
`experience_ship_type`.

## Vessel Tonnage — Detailed Spec

### Schema

Adds a single optional field to the existing PR #39/#40 shape:

```json
{
  "type": "vessel_tonnage",
  "min_value": 50000,
  "max_value": null,
  "unit": "any",
  "years_back": 3
}
```

`years_back`: positive integer or null. Null/absent means "any time"
and preserves PR #39/#40 behavior exactly.

Validator: accepts the existing constraint shape unchanged; the new
field defaults to null when absent.

### Evaluator semantics

`_collect_vessel_tonnage_evidence` (PR #41) is updated to accept an
optional `years_back` parameter. When set, the evidence collection
filters contract rows by date before iterating tonnage entries.

The rest of the tonnage evaluator (`_evaluate_vessel_tonnage_rule`)
is unchanged. The unit policy, range comparison, and reason codes
(`MATCH`, `BELOW_MINIMUM`, `ABOVE_MAXIMUM`, `OUT_OF_RANGE`,
`NOT_FOUND`, `UNIT_NOT_FOUND`) all behave identically; they just
see a smaller evidence list.

A new reason code is added:

- `VESSEL_TONNAGE_NEEDS_DATE_REVIEW` — UNKNOWN when undated contracts
  with matching tonnage exist but no dated in-window contracts match.

### UI

Adds a single recency dropdown to the existing tonnage filter group.
Same options as the ship/engine pickers (`Any time` through
`Last 10 years`).

Helper text appended to the existing tonnage helper:

```text
The recency window restricts which sea-service rows are considered.
Leave at "Any time" to search the candidate's complete experience.
```

UI is locked during refinement.

## `recent_contract_vessel_experience` Deprecation

Three-step transition. Each step is an independent PR.

### Step 1 — accept both shapes (transition begins)

- Backend evaluator code accepts both legacy and new shapes.
- Schema validation auto-translates legacy to new.
- Prompt parser, shadow LLM, and legacy adapter still emit legacy
  shapes.
- Both code paths route through the same evaluator (the new one).
- This step is risk-free: no externally visible behavior change.

### Step 2 — switch emitters to new shape

- Prompt parser updated to emit `experience_ship_type` with a single
  item when it detects ship-type + recency phrasing.
- Shadow LLM system prompt updated with new shape examples.
- Legacy parser adapter updated to emit new shape.
- After this step, no production code emits
  `recent_contract_vessel_experience` constraints.
- Shadow eval re-run to confirm zero regressions on ship-type prompts.

### Step 3 — remove legacy code paths

- The `recent_contract_vessel_experience` family is removed from the
  hard-filter catalog.
- The legacy constraint shape is rejected at schema validation.
- Saved searches that still have legacy payloads get translated on
  load (UI prompts the recruiter to save the new shape on first
  edit).

The deprecation window between Step 2 and Step 3 should be at least
one full release cycle to give saved searches time to update.

## Contract Count Semantics

v1 supports both `years_back` (date-driven window) and
`contract_count` (count-driven window) as per-item alternatives. They
express different recruiter intents:

- "tanker in last 3 years" → `years_back: 3`. Date-driven; excludes
  candidates who've been ashore beyond the window.
- "tanker in last 3 contracts" → `contract_count: 3`. Count-driven;
  includes candidates regardless of shore time between contracts.

### Mutual exclusion (per-item, not per-search)

Exactly one of `years_back` or `contract_count` may be non-null on a
single item. Both null = "no window" (consider all contracts). Both
non-null = `EXPERIENCE_SHIP_TYPE_RECENCY_AMBIGUOUS` (or the engine
equivalent), HTTP 400.

The constraint is scoped to a single filter row. A multi-select with
two rows (e.g., tankers + container ships) may independently set one
row to `years_back: 3` and the other row to `contract_count: 5`.

### Undated contracts inside the count window

If a candidate's N most-recent contracts (ranked by end date, falling
back to start date) include any undated row, the candidate goes to
the "Needs contract date review" bucket for that item rather than
being evaluated against an incomplete chronology. Same conservative
pattern used for age (UNKNOWN → review). The evaluator does not
guess at ordering when it can't.

Candidates with fewer than N total datable contracts are evaluated
against the full set they do have (the window is "all contracts
available"); this is not a Needs-Review case because the chronology
is unambiguous.

### Prompt translation

Phrases the prompt parser recognizes as count-driven (`"last N
contracts"`, `"most recent N voyages"`, etc.) emit `contract_count`.
Date-driven phrases (`"last N years"`) emit `years_back`. The
heuristic translation that mapped count to year approximations is
removed — translation is now 1:1.

## Rollout Plan

The eight-step sequence balances independent mergeability against
correctness:

### PR-0 (this spec)

Commits `docs/specs/experience_filters_v1.md`. No code.

### PR-1 — evidence-scoper plumbing in the evaluator

Add the date-window filtering helpers (window calculation, contract
overlap check, undated-contract bucket logic) without changing any
schemas or UI. Apply them only when constraints carry a `years_back`
field — which no production code emits yet, so no behavior change.
Tests verify the helper functions in isolation.

### PR-2 — schema accepts new multi-select shapes (legacy still works)

Extend `experience_ship_type` and `engine_experience` validators to
accept both legacy and new shapes; auto-translate legacy. Evaluator
updates to read `items` array internally. Prompt parser and shadow
LLM still emit legacy shapes, so behavior is unchanged. Tests cover
both shapes round-trip through validation.

### PR-3 — emit new shapes from prompt parser and shadow LLM

Prompt parser updated; shadow LLM examples updated; legacy adapter
updated. After this PR, no production code emits legacy shapes for
these families. Shadow eval re-run; zero regressions on ship-type
and engine prompts is the merge gate.

### PR-4 — Vessel Tonnage `years_back` field

Schema accepts the new field; evaluator's evidence collector filters
by window when set. UI dropdown added to the tonnage filter group.
Tests cover any-time backward compatibility and windowed evaluation.

### PR-5 — Experienced Ship Type multi-select UI

Replace the single-select picker with the multi-select-with-per-item-
recency component. Wire to query plan emission of the new shape. UI
disabled during refinement.

### PR-6 — Engine Experience picker UI

New picker, same component pattern as PR-5. Engine canonical list
sourced from in-code mapping.

### PR-7 — `recent_contract_vessel_experience` deprecation

Remove from catalog. Schema rejects legacy shape. UI on saved-search
load prompts to migrate. Documentation of the heuristic translation
window in the deprecation runbook.

### PR-8 — "Needs contract date review" bucket

UI surface for undated contracts that would have matched. Same shape
as other "Needs review" buckets in `search_pickers_v1.md`.

## Validation Errors

Follows the same UPPER_SNAKE_CASE / structured-code convention as the
table in `search_pickers_v1.md` ("Shared Patterns → Structured
Validation Errors"). The codes below extend that pattern with families
specific to this spec:

| Code | When | HTTP |
|---|---|---|
| `EXPERIENCE_SHIP_TYPE_ITEMS_REQUIRED` | Multi-select submitted with empty items array but match_mode set | 400 |
| `EXPERIENCE_SHIP_TYPE_INVALID_SHIP_FAMILY` | Item ship_family not in canonical list | 400 |
| `EXPERIENCE_SHIP_TYPE_INVALID_RECENCY` | Item `years_back` is not a positive integer | 400 |
| `EXPERIENCE_SHIP_TYPE_INVALID_CONTRACT_COUNT` | Item `contract_count` is not a positive integer | 400 |
| `EXPERIENCE_SHIP_TYPE_RECENCY_AMBIGUOUS` | Both `years_back` and `contract_count` non-null on the same item | 400 |
| `EXPERIENCE_SHIP_TYPE_INVALID_MINIMUM_MONTHS` | Item `minimum_months` is not a positive integer | 400 |
| `EXPERIENCE_SHIP_TYPE_UNSUPPORTED_MATCH_MODE` | `match_mode` is anything other than `any_of` | 400 |
| `ENGINE_EXPERIENCE_ITEMS_REQUIRED` | Same as ship type, for engine family | 400 |
| `ENGINE_EXPERIENCE_INVALID_ENGINE_FAMILY` | Item engine_family not in canonical list | 400 |
| `ENGINE_EXPERIENCE_INVALID_RECENCY` | Item `years_back` is not a positive integer | 400 |
| `ENGINE_EXPERIENCE_INVALID_CONTRACT_COUNT` | Item `contract_count` is not a positive integer | 400 |
| `ENGINE_EXPERIENCE_RECENCY_AMBIGUOUS` | Both `years_back` and `contract_count` non-null on the same item | 400 |
| `ENGINE_EXPERIENCE_INVALID_MINIMUM_MONTHS` | Item `minimum_months` is not a positive integer | 400 |
| `ENGINE_EXPERIENCE_UNSUPPORTED_MATCH_MODE` | `match_mode` is anything other than `any_of` | 400 |
| `VESSEL_TONNAGE_INVALID_RECENCY` | `years_back` is not a positive integer when set | 400 |

### Non-blocking warnings

Backend may attach non-blocking warnings to the response in a
`warnings[]` array. These do not block the search; the UI surfaces a
dismissible banner when present.

| Code | When |
|---|---|
| `duplicate_filter_rows` | Two or more items in the same multi-select were identical after normalization. Backend deduped and returned `removed_count` in the warning payload. Structured log line emitted: `event=deduped_filter_rows search_id=<id> family=<family> removed_count=<N>` |

## Shared Patterns

Inherits the `search_pickers_v1.md` shared conventions:

- "Needs review" buckets for unresolved data ("Needs contract date
  review" is the v1 addition).
- Structured validation errors with UPPER_SNAKE_CASE codes.
- Versioning and audit trails (the spec version is referenced in
  audit logs whenever the new shapes are emitted).

## Test Fixtures by PR

### PR-1 (evidence scoper helpers)

- `test_contract_in_window_when_end_date_inside`.
- `test_contract_in_window_when_start_date_inside`.
- `test_contract_in_window_when_spans_window`.
- `test_contract_out_of_window_entirely_before`.
- `test_contract_with_no_end_date_treated_as_ongoing`.
- `test_undated_contract_bucketed_for_review`.
- `test_minimum_months_accumulation_with_boundary_straddle`.

### PR-2 (schema + evaluator backward compat)

- `test_legacy_single_select_experience_ship_type_translates`.
- `test_new_multi_select_experience_ship_type_validates`.
- `test_invalid_match_mode_rejected`.
- `test_unknown_ship_family_rejected`.
- `test_legacy_single_select_engine_experience_translates`.
- `test_recent_contract_vessel_experience_translates_to_contract_count`.
- `test_evaluator_pass_with_single_item_legacy_shape`.
- `test_evaluator_pass_with_two_item_any_of`.
- `test_evaluator_pass_with_item_years_back_window`.
- `test_evaluator_pass_with_item_contract_count_window`.
- `test_evaluator_fail_when_no_in_window_match`.
- `test_both_years_back_and_contract_count_rejected_as_ambiguous`.
- `test_duplicate_items_silently_deduped_and_warning_returned`.
- `test_dedupe_log_line_emitted_with_search_id_and_family`.
- `test_contract_count_window_with_undated_row_goes_to_needs_review`.
- `test_contract_count_window_with_fewer_dated_contracts_uses_all`.
- `test_two_rows_with_different_recency_modes_evaluated_independently`.

### PR-3 (emitter updates)

- `test_prompt_parser_emits_new_shape_for_recency_phrasing`.
- `test_shadow_llm_emits_new_shape_for_multi_ship_type_prompt`.
- `test_legacy_adapter_emits_new_shape`.
- `test_shadow_eval_no_regressions_on_ship_type_prompts`.

### PR-4 (tonnage years_back)

- `test_vessel_tonnage_years_back_null_matches_any_time`.
- `test_vessel_tonnage_years_back_3_filters_to_in_window`.
- `test_vessel_tonnage_years_back_excludes_old_contracts`.
- `test_vessel_tonnage_needs_date_review_when_undated_would_match`.

### PR-5 (ship type UI)

- `test_ship_type_multi_select_renders_empty_state`.
- `test_ship_type_add_remove_rows` (E2E).
- `test_ship_type_per_item_recency_sent_in_query_plan` (E2E).
- `test_ship_type_locked_during_refinement` (E2E).

### PR-6 (engine UI)

Mirrors PR-5 fixtures for the engine picker.

### PR-7 (deprecation)

- `test_legacy_recent_contract_vessel_experience_rejected`.
- `test_saved_search_with_legacy_shape_prompts_migration`.

### PR-8 ("Needs review" bucket)

- `test_needs_date_review_bucket_lists_candidate`.
- `test_needs_date_review_bucket_explains_match_path`.

## Phase 2

Tracked in GitHub Issue #45 (filed alongside PR-0). Phase 2 covers:

- AND match mode (`all_of`) for multi-select.
- Multi-row Vessel Tonnage.
- `contract_count` support on `vessel_tonnage` (v1 supports
  `years_back` only on tonnage).
- Engine canonical alias file (mirror the CoC issue authority
  pattern) if recruiters need human-readable display labels.
- Cross-family recency consistency: surface a global "default
  recency" picker that applies to every recency-aware filter that
  doesn't have an explicit value.
- Saved-search migration UI: a wizard that walks recruiters through
  updating saved searches with the new shapes when they open them.
- Bulk "Needs contract date review" remediation: a workflow for
  engineering or trained recruiters to date-tag a batch of contracts
  in one pass.

Explicitly **not** on the Phase 2 roadmap:

- Recruiter-driven manual `present_rank` tagging from the UI (see
  `search_pickers_v1.md` — informational count only, not a tagging
  workflow).
