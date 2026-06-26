# Search Pickers v1

## Goal

Unify the AI Search UI around structured pickers that scope and filter the
candidate population deterministically, replacing today's ambiguous
folder-as-scope behavior and adding missing filter dimensions for candidate
age and CoC issuing authority.

v1 ships three related features:

- **Rank search scope.** Decouple search scope from folder organization.
  Replace the single folder picker with two independent rank pickers,
  `applied_rank` (folder) and `present_rank` (extracted from the resume).
  Either, or both, may be set; both blank is invalid.
- **CoC issue authority filter.** Add a new structured filter for the
  specific regulatory body that issued a candidate's Certificate of
  Competency, distinct from the existing CoC country filter. Backed by an
  alias dictionary and a new hard-filter family.
- **Age range filter.** Add recruiter-friendly minimum/maximum age inputs
  backed by the existing deterministic `age_range` hard-filter family and
  resume-derived candidate age evidence.

The three features are independent technically but share patterns: structured
pickers as source of truth, alias normalization, "Needs review" buckets for
unresolved inputs, structured validation error codes, audit-trailed
versioning. Documenting them together keeps the shared conventions in one
place.

## Phase 1 Scope

### Rank Search Scope

Includes:

- Two independent rank pickers (`applied_rank`, `present_rank`) in the
  search UI.
- An in-memory index keyed by canonical `present_rank` for fast lookup
  across all folders.
- A backend validator rejecting requests where both rank fields are null
  (`RANK_SCOPE_REQUIRED`).
- UI enforcement disabling the Search button until at least one picker has
  a value.
- An audit of every code path that previously assumed "no folder selected"
  meant something, with each path updated to surface the validator error.
- Suppression of prompt-derived rank constraints when either picker is set
  (pickers are the structured source of truth for rank scoping).
- `present_rank` UNKNOWN results surface in the existing general
  Needs Review section. v1 does not create a dedicated recruiter-facing
  rank-review bucket.

Excludes (deferred to later phases):

- Migrating the *saved configuration* of existing saved searches or
  scheduled tasks to require rank pickers (the runtime validator below
  still applies to every entry point in v1 — see "Audit Targets"; what's
  deferred is rewriting stored payloads, surfacing migration warnings in
  the saved-search UI, and any backfill of pre-existing records).
- Cross-folder bulk operations.
- Per-folder default picker preferences.
- Persisted index on disk.

Excludes (dropped, **not** deferred — see "Present-Rank Needs Review"
and the Phase 2 section for rationale):

- Recruiter-driven manual `present_rank` tagging from the UI.
- Hiding the `applied_rank` picker behind an advanced toggle.

### Age Range Filter

Includes:

- Two optional numeric inputs in the search UI: `Minimum age` and
  `Maximum age`.
- Reuse of the existing deterministic `age_range` hard-filter family.
- Request normalization to the existing legacy hard-constraint shape:
  `hard_constraints.age_years = {"min_age": N|null, "max_age": N|null}`.
- Suppression of prompt-derived age constraints when either age picker
  value is set.
- Existing UNKNOWN / Needs Review handling when candidate age evidence is
  missing or ambiguous.

Excludes (deferred to later phases only if production evidence shows a
need):

- DOB picker UI.
- Recruiter-driven manual age correction.
- New age extraction or backfill logic beyond the existing candidate-facts
  extraction.

### CoC Issue Authority Filter

Includes:

- A canonical alias file mapping surface forms to canonical authority IDs.
- A refactored normalization function shared between country alias lookup
  and authority alias lookup.
- Extraction of canonical authority on every CoC fact, alongside the
  existing raw `issue_authority` field.
- A new deterministic hard-filter family `coc_issue_authority_match`.
- Disentanglement of issue authority from `coc_country_match` in the
  shadow LLM provider so the two constraints are independent.
- A multi-select dropdown in the search UI.
- A "Needs alias review" bucket surfacing CoCs whose raw `issue_authority`
  did not resolve to a canonical entry.

Excludes (deferred to later phases):

- Recruiter-driven alias additions from the UI.
- Hierarchical UX that narrows the authority dropdown by selected country.
- Index-backed authority lookup for sub-second filter performance.
- Migration of the existing country alias dict from inline Python to JSON.

## Architectural Principles

Three principles cut across these features. Future structured-picker work
should follow them.

- **Folder is storage, not scope.** Folder organization (by applied rank)
  is the intake truth. Search scope is a separate dimension expressed by
  structured pickers. The two were conflated; v1 separates them.
- **Pickers are the structured source of truth.** When a picker has a
  value, free-form prompt phrases that would emit the same constraint are
  suppressed. Pickers win.
- **Unresolved inputs surface, never silently drop.** Unrecognized
  authority strings or unextractable present ranks go to "Needs review"
  buckets, not the bit bucket.

## Structured Picker Implementation Contract

Every new structured picker/filter must satisfy this checklist before the
PR is considered complete. This contract exists because picker work touches
many sibling paths; reviewing only the visible UI or only `/analyze_stream`
has repeatedly missed real regressions.

### Propagation Checklist

For each picker field, verify the value flows through every applicable
surface:

- UI state and recovery state.
- Request payload for `/analyze_stream`.
- Request payload for `/analyze`.
- Request fingerprint / idempotency key, when the picker changes search
  semantics.
- Root-search validation.
- Refinement parent-context inheritance.
- Completed search-session context.
- Search-result `search_context`.
- Telemetry start / complete / error payloads.
- CSV audit rows or equivalent durable audit event.
- Saved/recovered draft sanitization.

If a surface intentionally does not carry the picker, document why in the
PR and add a regression test for the omission. The default is propagation,
not omission.

### Vocabulary Checklist

Each picker must have a single source of truth for allowed values:

- Dropdown options come from the canonical product vocabulary, not from
  incidental storage labels such as folder names.
- Backend validation uses the same canonical vocabulary as the dropdown.
- Any accepted alias forms are explicit. If API aliases are intentionally
  narrower than parser aliases, document the difference near the validator.
- Recruiter-facing labels are generated from display-label maps/helpers,
  never by showing canonical IDs directly.
- Tests include at least one value that looks plausible but must be rejected
  because it is outside the canonical picker vocabulary.

### Prompt-Interaction Checklist

When a picker and a free-form prompt can express the same intent:

- Picker value wins and the prompt-derived same-family hard constraint is
  suppressed.
- The consumed prompt phrase remains visible in the clause ledger as
  observed/applied context, not as unsupported text.
- Suppression must not prevent mixed prompts from running semantic reasoning
  on real residual text.
- A prompt-only legacy path must still work when the picker is blank, unless
  the product intentionally retires that prompt path.

### Needs Review Checklist

UNKNOWN/Needs Review behavior must be explicit:

- The default destination is the existing general Needs Review section.
- A dedicated family-specific bucket or counter must be implemented,
  rendered, tested, and documented before the spec may promise one.
- Helper text must name the actual surface users see. Do not promise a
  dedicated bucket if the implementation only uses general Needs Review.

### Required Test Shape

Each picker PR should include tests for:

- Valid picker value accepted.
- Invalid picker value rejected with a structured error.
- Root search and refinement search.
- Recovery draft save/load.
- Audit/session/telemetry propagation.
- Prompt suppression with no residual text.
- Prompt suppression with residual semantic text.
- Recruiter-facing output does not leak canonical IDs.

If a PR modifies a helper with one or more external callers in
`_extract_job_constraints`, `/analyze_stream`, or `/analyze`, helper-level
coverage is not enough. The PR must list the caller grep used for verification
and add at least one adjacent integration-path test that observes the helper's
output through the consuming path. Valid consuming paths include downstream
branching, payload propagation, request fingerprinting, recovery state,
audit/export, telemetry, result rendering, `_extract_job_constraints`,
`/analyze_stream`, `/analyze`, search-session context, CSV audit row output, or
result formatting.

### Spec Update Rule

Every picker PR that introduces, removes, relaxes, or otherwise changes a
behavioral invariant must update this spec in the same PR. A behavioral
invariant means any rule in one of these closed categories, plus any behavior
independently asserted by tests:

- Picker-vs-prompt arbitration.
- Alias disambiguation.
- Canonical vocabulary source of truth.
- Needs Review behavior.
- Recruiter/operator output sanitization.
- Audit/export/telemetry visibility boundaries.
- Request payload, fingerprint, idempotency, recovery, or session/search-context
  propagation.
- Root/refinement inheritance.
- Prompt interaction/suppression behavior.
- Downstream branching when a constraint is filtered or partially suppressed.

Reviewer prompts must explicitly ask whether the change introduces, removes,
relaxes, or otherwise changes a structured-picker invariant and, if yes, where
this spec was updated.

## Locked Decisions

### Rank Pickers

| Decision | Value |
|---|---|
| Picker combination table | See semantics table below |
| Folder picker fate | Replaced by `applied_rank` picker (same data, clearer name) |
| Search button gating | Disabled until at least one picker has a value |
| Helper text | `"Select applied rank, present rank, or both."` |
| Backend validation | Reject if both null with `RANK_SCOPE_REQUIRED` |
| `applied_rank` source | Folder path (no extraction) |
| `present_rank` source | Existing resume extraction in `ai_analyzer.py` |
| Index location | In-memory at startup; persistence deferred |
| Staleness UX | "Index last updated: N minutes ago" + manual Reindex button |
| Prompt-picker interaction | Picker value present → suppress prompt rank constraints |

Picker semantics:

| applied_rank | present_rank | Behavior |
|---|---|---|
| blank | set | Search by `present_rank` across all folders. |
| set | blank | Search by `applied_rank`, restricted to that folder. |
| set | set | AND. Candidate must be in the folder AND match present rank. |
| blank | blank | Invalid. UI button disabled; backend rejects with `RANK_SCOPE_REQUIRED`. |

### Age Range

| Decision | Value |
|---|---|
| UI label | `Candidate Age` |
| Inputs | `Minimum age`, `Maximum age` |
| Empty state | Both blank means no age filter |
| Value type | Positive whole years |
| Allowed picker range | 18-80 inclusive |
| Bound semantics | Inclusive (`age >= minimum`, `age <= maximum`) |
| Backend shape | Existing `age_years` hard constraint |
| Query-plan family | Existing `age_range` family |
| Prompt-picker interaction | Either picker value present -> suppress prompt-derived age constraints |
| Prompt one-sided bounds | A one-sided picker still suppresses the whole prompt-derived age constraint |
| Missing age evidence | Existing UNKNOWN / Needs Review path |

### CoC Issue Authority

| Decision | Value |
|---|---|
| Alias file location | `candidate_facts/aliases/coc_issue_authority.json` |
| Alias file shape | Hierarchical: country → canonical authority → aliases |
| Canonical naming | Country-prefixed snake_case (`india_dg_shipping`) |
| Seed list source | IMO STCW White List + corpus frequency survey |
| Seed list size | 25-30 authorities covering 90%+ of corpus |
| Curation workflow | Engineer-only via PR for v1; recruiters flag via "Needs review" |
| Display policy | Canonical label in result cards, raw string on hover |
| Audit trail | Semver `version` + ISO `last_updated` in JSON header |
| Normalization | Shared with country: refactor `_normalize_coc_country` → generic `_normalize_alias(value, alias_map)` |
| Country alias migration | None in v1; country aliases stay inline |
| JSON schema validation | Strict, fail loud at module load |

## Rank Search Scope — Detailed Spec

### `applied_rank` Derivation

`applied_rank` is derived from the folder path. No extraction, no storage
in candidate_facts — folder name *is* the applied rank.

The picker is populated from the list of folders under the configured
corpus root. Picker values that don't match a folder return
`APPLIED_RANK_FOLDER_NOT_FOUND`.

### `present_rank` Derivation

`present_rank` is extracted from the resume body by the existing extractor.
It is already a structured field on candidate_facts. v1 does not add new
extraction logic; it adds the index and the picker wiring.

If extraction fails (bad OCR, unusual format, missing field),
`present_rank` remains null. The candidate appears in the existing
general Needs Review section when a present-rank filter is evaluated,
but is invisible to `present_rank`-scoped searches that rely on an index.

### Index Shape

```python
{
    "2nd_engineer": ["/corpus/2nd_Engineer/candidate_a.pdf", ...],
    "3rd_engineer": [...],
    ...
}
```

Properties:

- Built at app startup from the candidate_facts cache.
- Refreshed when **either** the resume file mtime **or** the
  candidate_facts entry for that file changes. The index is built from
  candidate_facts (the resume file alone doesn't carry the canonical
  rank), so any path that invalidates or regenerates candidate_facts
  (re-extract, cache version bump, manual edit) must also bump the
  index. Implementations should track per-file pairs of (resume mtime,
  candidate_facts version/mtime) and rebuild a row when either changes.
- Manually rebuildable via a "Reindex" button and a CLI command.
- Index version (monotonic counter) shown in the UI header with
  "Index last updated: N minutes ago".
- v1 holds the index in memory only; disk persistence is deferred.

### Search Request Shape

```json
{
  "applied_rank": "2nd_engineer",
  "present_rank": "3rd_engineer",
  "prompt": "candidates with USA visa above 30000 tonnage experience",
  "other_filters": "..."
}
```

Both rank fields are nullable strings. The validator requires at least one
to be non-null. Non-null values must match canonical rank IDs from the
existing rank canonicalization. Unknown canonical IDs are rejected with a
structured error (see Validation Errors below) — they are not silently
treated as empty results, because an unknown ID is almost always either a
typo or an API misuse worth surfacing.

### Query Plan Integration

Pickers are pre-filters on the candidate population, not new hard-filter
families. The pipeline:

1. Backend validator runs. `RANK_SCOPE_REQUIRED` aborts if both null.
2. Candidate population resolution:
   - `applied_rank` set → enumerate `<corpus_root>/<applied_rank>/`.
   - `present_rank` set → index lookup `index[present_rank]`.
   - Both set → set intersection.
3. Hard filters from the prompt plan and other UI filters evaluate against
   the resolved population.
4. LLM reasoning (if applicable) runs only on candidates that passed all
   hard filters.

### Prompt-Picker Interaction

When either picker has a value, the prompt parser suppresses any rank
constraint it would have emitted from free-form text.

Examples:

- Picker: `present_rank = 2nd_engineer`. Prompt: `"2nd Engineer with USA
  visa"`. → Only the USA visa constraint is emitted. The rank phrase is
  ignored.
- Picker: `present_rank = 2nd_engineer`. Prompt: `"Chief Engineer with USA
  visa"`. → USA visa constraint emitted. The conflicting rank phrase is
  ignored. No warning — recruiter intent is clear from the picker.
- Pickers: both blank. → Invalid; search is rejected before the prompt
  parser runs.

### Present-Rank Needs Review

**General Needs Review only in v1.** Candidates whose current/present rank
cannot be evaluated surface through the existing hard-filter Needs Review
section, using rank-specific reason codes. v1 does not add a dedicated
recruiter-facing rank-review bucket, drill-down view,
flag-for-review action, or manual tagging UI. Recruiters see the gap in the
general review surface; the fix path is an engineer patching the extractor
or hand-editing the candidate_facts entry.

Rationale: extraction reliability is high enough that a separate
rank-specific review workflow is not yet justified, and adding a tagging UI
introduces overwrite-conflict surface area (two recruiters disagreeing on
the same candidate) for marginal benefit. If extraction reliability turns
out worse than expected in production, manual tagging or a dedicated
rank-review queue can be added later as a separate feature — it is
explicitly **not** deferred to Phase 2.

### Validation Errors

- `RANK_SCOPE_REQUIRED` (HTTP 400): Both rank fields are null. Message:
  `"Select applied rank, present rank, or both to run a search."`
- `APPLIED_RANK_FOLDER_NOT_FOUND` (HTTP 400): Picker value doesn't match
  any folder under the corpus root. Bad input.
- `APPLIED_RANK_INVALID` (HTTP 400): Picker value is not a known canonical
  rank ID at all (e.g., `applied_rank = "foobar"`). Bad input. Message
  includes the rank that failed and a hint that canonical IDs are the
  validated set used by the existing rank canonicalization.
- `PRESENT_RANK_INVALID` (HTTP 400): Same shape as
  `APPLIED_RANK_INVALID`, but for the `present_rank` field. Bad input.
- `PRESENT_RANK_NOT_INDEXED` (HTTP 200, empty result): The value is a
  *valid* canonical rank but the index has no candidates for it. This is
  *not* an error; the response is an empty result with a notice
  suggesting reindex.

The distinction matters: unknown canonical IDs (`*_INVALID`) are bad
input and surface as 400s so API clients and automation catch typos.
Valid IDs with empty populations (`PRESENT_RANK_NOT_INDEXED`) are a
normal "no matches" outcome.

### Audit Targets (Knock-on Cleanup)

Every code path that previously assumed "no folder selected" meant
"search whatever is open" or "search all" must be updated to surface
`RANK_SCOPE_REQUIRED`:

- AI Search entry point in `frontend.html`.
- Scheduled search / saved query loaders.
- Hard-filter dispatcher in `ai_analyzer.py`.
- Any test fixture exercising a "no folder" path.

If a path is missed, it must fail loud, not return an empty result.

## Age Range Filter — Detailed Spec

### Evidence Source

The picker uses the existing candidate age evidence derived from resume
date-of-birth / age extraction. v1 does not add a new extractor or a
backfill job.

Candidate facts should already expose an integer candidate age through the
same path used by the existing `age_range` evaluator. Missing, ambiguous,
or out-of-date age evidence follows the current hard-filter UNKNOWN path
and surfaces in Needs Review.

### UI Shape

`frontend.html` adds a compact `Candidate Age` filter near the other
structured search filters.

- `Minimum age`: optional numeric input.
- `Maximum age`: optional numeric input.
- Control type: HTML number input with `step=1`, `min=18`, `max=80`.
- Helper text: `"Optional. Candidates pass when their extracted age is
  within the selected bounds."`
- Both fields blank: no age filter.
- One field set: one-sided bound.
- Both fields set: inclusive range.

The UI should reject non-integer, decimal, negative, zero, and out-of-range
values before submitting the request.

### Request Shape

UI payload:

```json
{
  "age_filter": {
    "minimum_years": 30,
    "maximum_years": 50
  }
}
```

Backend normalization emits the existing legacy hard-constraint shape:

```json
{
  "hard_constraints": {
    "age_years": {
      "min_age": 30,
      "max_age": 50
    }
  }
}
```

Field conversion is explicit and one-way:

| Layer | Minimum field | Maximum field | Notes |
|---|---|---|---|
| UI payload | `minimum_years` | `maximum_years` | Recruiter-facing units are explicit. |
| Query-plan family | `minimum_years` | `maximum_years` | Matches `age_range` schema naming. |
| Legacy analyzer hard constraint | `min_age` | `max_age` | Existing `age_years` evaluator shape; do not expose to UI. |

For query-plan v1 paths, the equivalent constraint remains the existing
`age_range` family:

```json
{
  "family": "age_range",
  "parameters": {
    "minimum_years": 30,
    "maximum_years": 50
  }
}
```

### Validation

- Both bounds blank: valid, no age filter emitted.
- `minimum_years` set and `maximum_years` blank: valid.
- `maximum_years` set and `minimum_years` blank: valid.
- Both set and `minimum_years <= maximum_years`: valid.
- Both set and `minimum_years > maximum_years`: reject with
  `AGE_FILTER_INVALID` and `detail.code = "inverted_bounds"`.
- Any non-positive, non-integer, or out-of-range value: reject with
  `AGE_FILTER_INVALID` and a specific detail code.

The HTTP error code stays coarse (`AGE_FILTER_INVALID`) so existing UI and
automation can branch on one category. The response must include a
machine-readable `detail.code` for user-facing copy:

| Detail code | Trigger | Suggested copy |
|---|---|---|
| `non_integer` | Value is not a whole number, including decimals like `30.5` | `"Age must be a whole number."` |
| `non_positive` | Value is `0` or negative | `"Age must be positive."` |
| `out_of_range` | Value is below `18` or above `80` | `"Age must be between 18 and 80."` |
| `inverted_bounds` | `minimum_years > maximum_years` | `"Minimum age cannot be greater than maximum age."` |

Allowed picker range is 18-80 inclusive. This mirrors the current
deterministic prompt parser's conservative age range, avoids accidental
matches on unrelated numeric fields, and reflects the product policy that
v1 does not source minors. If cadet sourcing for ages 16-17 becomes a real
requirement, it should be handled as an explicit policy change rather than
silently widening this picker.

### Prompt-Picker Interaction

When either age picker field has a value, prompt-derived age constraints
are suppressed. Picker values are treated as the structured source of
truth.

This suppression applies to the whole prompt-derived age constraint, even
when the picker is one-sided. Example: if `minimum_years = 30` and the
prompt says `"below 45"`, the prompt's upper bound is ignored. Recruiters
who need a two-sided age range must set both picker fields. This keeps all
age intent in the structured picker and avoids merging two sources with
unclear precedence.

Examples:

- Picker: `minimum_years = 30`, `maximum_years = 50`. Prompt:
  `"candidate below 45 with valid passport"`. -> Passport constraint only;
  age comes from the picker.
- Picker: `minimum_years = 30`, `maximum_years = null`. Prompt:
  `"below 45 with US visa"`. -> US visa constraint only; age comes from
  the picker; the prompt's upper age bound is ignored.
- Picker: both blank. Prompt: `"between 30 and 50 years old"`. -> Existing
  prompt parser emits the `age_range` / `age_years` constraint.

### Evaluator Semantics

The evaluator reuses the existing `age_range` behavior:

- Candidate age is computed from date of birth using the search request's
  reference date.
- Candidate age inside inclusive bounds -> `PASS`.
- Candidate age outside bounds -> `FAIL`.
- Candidate age missing or ambiguous -> `UNKNOWN`.

The reference date is the request timestamp captured at search start, not
wall-clock time at each candidate evaluation. Scheduled searches, saved
search reruns, and audits must persist that reference date alongside the
hard-filter decision so age decisions are reproducible.

Age filter results AND with rank pickers, CoC issue authority picker,
country picker, vessel tonnage, engine experience, experience ship type,
and prompt-derived hard filters.

### Display in Results

When the age filter fires, result cards should display the matched or
failed evidence using recruiter-readable wording:

- PASS: `"Age matches requested range: 38."`
- FAIL: `"Age 54 is outside requested range 30-50."`
- UNKNOWN: `"Candidate age could not be determined from extracted resume
  facts."`

No age line is shown when neither picker nor prompt produced an age
constraint.

## CoC Issue Authority — Detailed Spec

### Alias File Shape

```json
{
  "version": "0.1.0",
  "last_updated": "2026-06-12",
  "source": "Seeded from IMO STCW White List + corpus frequency survey",
  "authorities": {
    "india": {
      "country_canonical": "india",
      "authorities": {
        "india_dg_shipping": {
          "display_label": "Directorate General of Shipping, India",
          "aliases": [
            "dg shipping",
            "dg shipping india",
            "directorate general of shipping",
            "directorate general of shipping mumbai",
            "dgs",
            "mmd",
            "mmd mumbai",
            "indian maritime administration"
          ]
        }
      }
    },
    "uk": {
      "country_canonical": "uk",
      "authorities": {
        "uk_mca": {
          "display_label": "Maritime and Coastguard Agency (UK)",
          "aliases": [
            "mca",
            "mca uk",
            "maritime and coastguard agency"
          ]
        }
      }
    }
  }
}
```

Required top-level keys: `version`, `last_updated`, `source`,
`authorities`.

Required per-country entry: `country_canonical`, `authorities`.

Required per-authority entry: `display_label`, `aliases` (non-empty
list).

### Versioning

- `0.1.0` for the v1 seed.
- Patch bump for adding aliases to existing canonical authorities.
- Minor bump for adding new canonical authorities.
- Major bump for breaking changes (renaming canonical IDs, splitting
  authorities).

Eval evidence and audit logs reference the alias version, so
reproducibility is preserved when the dict evolves.

### Normalization Function (Shared with Country)

A single shared normalizer is used for both country alias lookup and
authority alias lookup. The existing `_normalize_coc_country` is refactored
into a generic:

```python
def _normalize_alias(value: str, alias_map: Mapping[str, str]) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    normalized = re.sub(r"[^a-z]+", " ", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return alias_map.get(normalized)
```

The `alias_map` passed into this function is **already normalized**:
keys are the result of applying the algorithm below to every raw alias
in the JSON file. The loader is responsible for building that map at
module load time. The JSON stores raw aliases (recruiter-readable
strings like `"M.C.A."`, `"DG Shipping, Mumbai"`); the runtime never
looks up against raw strings.

Both `_normalize_coc_country` and the new
`_normalize_coc_issue_authority` become thin wrappers passing their
respective alias maps.

Algorithm:

1. Cast to string, strip whitespace, lowercase.
2. Replace every non-`[a-z]` character with a space.
3. Collapse runs of whitespace into a single space.
4. Strip leading/trailing whitespace.
5. Look up the result in the alias map; return canonical ID or `None`.

Examples:

- `"DG Shipping India"` → `india_dg_shipping`
- `"Directorate General of Shipping, Mumbai"` → `india_dg_shipping`
- `"MCA, UK"` → `uk_mca`
- `"Office of Maritime Affairs, Tuvalu"` → `None`

### JSON Schema Validation

The alias JSON is validated at module load. Failed validation prevents
app start; no silent fallback to a partial dict.

Validation fails on:

- Missing required top-level keys.
- Missing required per-country or per-authority keys.
- Empty `aliases` list on any authority entry.
- Duplicate canonical authority IDs across the file.
- Duplicate aliases **after normalization**: the loader normalizes every
  raw alias using the algorithm above and checks that no two
  *normalized* aliases under *different* canonical authorities collide.
  `"MCA"`, `"M.C.A."`, and `"m c a"` all normalize to `"m c a"` — if
  two of those appear under different canonicals, validation fails;
  if all three appear under the same canonical, they collapse to one
  entry in the map silently. The raw aliases are preserved in the JSON
  for human readability; the runtime keyspace is the normalized form.
- `country_canonical` value not in the country alias dict.
- Malformed `version` (must match `^\d+\.\d+\.\d+$`).

### Candidate Facts Shape Additions

```json
{
  "fact_type": "certificate",
  "certificate_type": "coc",
  "country": "india",
  "issue_authority": "Directorate General of Shipping, Mumbai",
  "issue_authority_canonical": "india_dg_shipping"
}
```

`issue_authority_canonical` is a canonical authority ID or `null`. The
raw `issue_authority` is preserved unchanged. The new field is additive.

Schema validates `issue_authority_canonical` as a nullable string matching
`^[a-z][a-z0-9_]*$` when non-null. Cross-checking against the alias dict
happens at extraction time, not schema-validation time.

### Hard Filter Family

Catalog entry mirrors `coc_country_match`:

```python
"coc_issue_authority_match": {
    "legacy_hard_constraints_key": "coc_issue_authority",
    "legacy_applied_constraint_id": "coc_issue_authority_match",
    ...
}
```

Constraint shape:

```json
{
  "type": "coc_issue_authority",
  "authorities": ["india_dg_shipping", "uk_mca"]
}
```

`authorities` is a non-empty list of canonical IDs, interpreted as OR
within the family.

Evaluator semantics:

- Collect every `issue_authority_canonical` value from the candidate's
  CoCs.
- No CoC or no canonical authority on any CoC → `UNKNOWN`.
- Any canonical authority matches any value in `authorities` → `PASS`.
- Otherwise → `FAIL`.

This mirrors `coc_country_match` semantics exactly.

Decision examples:

```json
{
  "decision": "PASS",
  "reason_code": "COC_ISSUE_AUTHORITY_MATCH",
  "message": "Candidate holds a CoC issued by Directorate General of Shipping, India.",
  "actual_value": "india_dg_shipping",
  "expected_value": ["india_dg_shipping", "uk_mca"],
  "confidence": 0.95
}
```

```json
{
  "decision": "FAIL",
  "reason_code": "COC_ISSUE_AUTHORITY_MISMATCH",
  "message": "Candidate's CoC was issued by Maritime and Port Authority of Singapore; recruiter required Directorate General of Shipping, India.",
  "actual_value": "singapore_mpa",
  "expected_value": ["india_dg_shipping"],
  "confidence": 0.95
}
```

```json
{
  "decision": "UNKNOWN",
  "reason_code": "COC_ISSUE_AUTHORITY_NOT_FOUND",
  "message": "Candidate has a CoC but the issuing authority could not be canonicalized.",
  "actual_value": null,
  "expected_value": ["india_dg_shipping"],
  "confidence": 0.0
}
```

### Disentanglement from `coc_country_match`

Today, `query_understanding/shadow_llm_provider.py:1514` contains:

```python
raw_countries = parameters.get("countries") or parameters.get("country") or parameters.get("issue_authority")
```

The `issue_authority` fallback is removed once
`coc_issue_authority_match` exists. The two constraints become
independent in query plans. A prompt mentioning both emits two distinct
constraints, AND-ed.

LLM system prompt examples are extended to distinguish country adjective
from authority name:

- `"Indian CoC"` → `coc_country_match{country: india}`.
- `"DG Shipping India CoC"` → `coc_issue_authority_match{authorities:
  [india_dg_shipping]}`.
- `"Indian-issued CoC"` → `coc_country_match{country: india}` (ambiguous
  → broader, safer default).
- `"CoC issued by MCA"` → `coc_issue_authority_match{authorities:
  [uk_mca]}`.
- `"Indian CoC issued by DG Shipping"` → both families emitted, AND-ed.

Shadow eval corpus is extended with authority-specific prompts and
regression prompts for ambiguous cases. Zero regressions on country
prompts is the merge gate.

### UI Picker

A multi-select dropdown in `frontend.html`, positioned alongside the
rank pickers and the country picker.

- Label: `CoC Issue Authority`
- Optional, default empty.
- Options sourced from canonical authorities that appear in the indexed
  corpus (authorities seeded but never observed are hidden).
- Display: `display_label` from the alias file.
- Stored value: canonical ID.
- Helper text: `"Select one or more issuing authorities. Candidates pass
  if any of their CoCs was issued by a selected authority."`

AND-s with rank pickers, country picker, and prompt-derived hard filters.

### "Needs Alias Review" Bucket

Lists CoCs where `issue_authority` is non-empty but
`issue_authority_canonical` is null.

Shows: candidate identifier, file path, raw `issue_authority` string,
country (if resolved), "Flag for review" button.

Flagging adds the raw string to a queue file
(`candidate_facts/aliases/_needs_review.json`) reviewed by engineering on
the next alias dict update.

Recruiter alias additions are deferred to Phase 2.

### Display in Results

Result cards surface authority evidence whenever the
`coc_issue_authority_match` family fired on the candidate — whether the
constraint came from the UI picker or from a prompt-derived constraint
(e.g., a prompt that mentions a specific authority by name). The display
keys off "family fired," not "picker had a value."

- Visible: canonical `display_label`.
- On hover: raw string from the certificate.
- Reason code shown in audit detail.

When the family did not fire (no picker selection AND no prompt-derived
authority constraint), authority is not displayed.

## Shared Patterns

These patterns appear across these features and should be reused in future
structured-picker work.

### "Needs Review" Buckets

Structured picker features surface unresolved inputs rather than silently
dropping candidates:

- Present-rank UNKNOWN results — candidates where `present_rank`
  extraction failed or current-rank evidence is missing. Uses the existing
  hard-filter Needs Review surface; no separate rank-specific review queue
  in v1. See the "Present-Rank Needs Review" section for rationale.
- Age filter UNKNOWN results — candidates where age evidence is missing or
  ambiguous. Uses the existing hard-filter Needs Review surface; no
  separate age-specific review queue in v1.
- "Needs alias review" — CoCs where `issue_authority` did not resolve.
  Exposes the raw input, available context, and a flag-for-review
  action.

Engineering owns the fix workflow in v1. The asymmetry
(rank review uses the general Needs Review surface, alias-review is
actionable) reflects that alias additions are bounded data entry while rank
tagging introduces overwrite-conflict surface area.

### Structured Validation Errors

Backend validators emit structured error codes that automation and UI can
detect. Codes use UPPER_SNAKE_CASE; messages are human-readable.

| Code | When | HTTP |
|---|---|---|
| `RANK_SCOPE_REQUIRED` | Both rank pickers null | 400 |
| `APPLIED_RANK_FOLDER_NOT_FOUND` | Picker value doesn't match a folder | 400 |
| `APPLIED_RANK_INVALID` | `applied_rank` is not a known canonical rank ID | 400 |
| `PRESENT_RANK_INVALID` | `present_rank` is not a known canonical rank ID | 400 |
| `PRESENT_RANK_NOT_INDEXED` | Valid picker, empty index entry | 200 (notice, not error) |
| `AGE_FILTER_INVALID` | Age bound is non-integer, out of range, or min > max | 400 |

The distinction matters: bad input is an error, valid input with no
matches is a normal empty result.

### Versioning and Audit Trails

These features ship with explicit versioning where durable derived data is
introduced:

- Alias dict: semver in JSON header, referenced in audit logs.
- Index: monotonic version counter, displayed in UI header.

Reproducibility is preserved across changes. Eval evidence references
versions so historical comparisons stay valid.

### Alias Match Disambiguation

Longest-first alias matchers running on prompt or snippet text must
cross-check any in-text country or equivalent disambiguating axis before
accepting the alias. If the in-text country conflicts with the matched
authority's canonical country, discard the authority match and emit an
observability parsing note rather than silently routing to the wrong
canonical ID.

Prompt country detection must be scoped to the authority alias token
window: directly adjacent country-name/abbreviation tokens, or country
aliases connected to the authority alias by a short issuance connective
from the exact set `"issued"`, `"issuing"`, `"by"`, `"from"`. Incidental
countries elsewhere in the prompt, such as nationality or joining-location
text, must not suppress a valid authority constraint.

### Display Labels at Output Boundaries

Recruiter- and operator-visible audit/export sinks must translate
canonical IDs to display labels at the write boundary. This includes CSV
audit rows, visible event payloads, error UI, and similar exported views.
Machine-readable telemetry and session context may keep canonical IDs for
continuity, provided no UI surface renders them verbatim.

## Rollout Plan

The features are independent and can land in parallel or sequence.
The PR sequence below interleaves them by priority — rank pickers come
first because the current behavior (folder-as-scope) is actively
misaligned with recruiter workflow.

### PR-0: Spec

Commits this spec under `docs/specs/search_pickers_v1.md` and a short
policy excerpt in `AI_Search_Results/README.md`. No code.

### PR-1 (rank): Present-rank index

- Build the `{canonical_present_rank: [paths]}` index at startup.
- Add refresh logic on file mtime change.
- Add "Reindex" CLI command and a UI hook (not yet exposed).
- Backend hook shape: `/get_rank_folders` returns a `present_rank_index`
  status object, and `POST /rebuild_present_rank_index` rebuilds the
  in-memory index for the future UI button.
- `present_rank_index` status payload contains only aggregate fields:
  `version`, `built_at`, `row_count`, `indexed_count`, `unindexed_count`,
  and `rank_counts`. It must not expose candidate file paths or row-level
  candidate metadata.
- Refresh triggers: current persisted candidate-facts row changes
  (`candidate_facts_hash`, row ID, updated timestamp, or present-rank value)
  and matching resume file mtime changes. Refresh may rebuild the in-memory
  index but must avoid changing search execution until PR-4 wires candidate
  population resolution.
- `version` is session-scoped and resets when runtime managers/config roots
  reset. The UI may use it for within-session staleness hints only, not as a
  durable cross-restart revision.
- Tests: index builds correctly, refresh detects file changes, version
  increments on rebuild.
- No UI changes visible to recruiters yet.

### PR-2 (rank): Search UI — two rank pickers

- Replace folder picker with `applied_rank` and `present_rank` pickers.
- Wire pickers to the search request shape.
- Add helper text and Search-button disable logic.
- Add the index-header status display + Reindex button.
- Index status UI renders aggregate-only fields from `present_rank_index`:
  indexed row count, unindexed row count, present-rank group count, version,
  and last-built time. It must not render paths or row-level candidate
  metadata.
- Reindex button calls `POST /rebuild_present_rank_index`, updates the status
  from the returned payload, refreshes rank-folder options, disables while
  rebuilding/searching/refining, and surfaces 409 "already running" responses
  as a non-fatal warning.
- Logout/auth changes must clear session-scoped index status and picker-option
  state, and in-flight Reindex/fetch promises must not write status into a
  different authenticated session.
- No backend validation yet (PR-3 follows).

### Shared Instance Lifecycle Rule

When a picker/index feature uses a module-level instance protected by a rebuild
or refresh lock, all global reassignments of that instance must use the same
lock as the rebuild path. Rebuild endpoints must log internal exception details
server-side and return sanitized operator messages to the UI.

### PR-3 (rank): Backend validator + audit

- Add `RANK_SCOPE_REQUIRED` validator at the search entry point.
- Root searches must provide at least one rank scope. Applied-rank scope
  (`rank_folder_id`, `rank_folder`, or `applied_rank`) resolves a selected
  folder; present-rank-only root searches are valid in Phase 2 and resolve
  through the present-rank index across all applied-rank folders.
- Audit and update every code path that previously fell through to
  unbounded scan.
- Add `APPLIED_RANK_FOLDER_NOT_FOUND` error.
- `/analyze_stream` and `/analyze` must share the same root rank-scope
  validation semantics before analyzer construction. Missing scope returns
  `RANK_SCOPE_REQUIRED`; unknown, unsafe, or stale applied-rank folder
  references return `APPLIED_RANK_FOLDER_NOT_FOUND` with a machine-readable
  detail code preserving the resolver reason.
- On `/analyze_stream`, rank-scope validation must run after request-claim
  checks and after refinement-context inheritance so existing request statuses
  and parent rank context take precedence over live folder validation.
- Tests for all four picker states: no picker, present-rank only,
  applied-rank only, and applied+present rank.

### PR-4 (rank): Query plan + prompt parser interaction

- Pickers resolve before query plan execution.
- For the current root-search rollout, the candidate population is:
  applied-rank only -> selected folder scan; applied+present rank -> selected
  folder intersected with `present_rank_index[present_rank]`. Present-rank-only
  root search -> `present_rank_index[present_rank]` across all applied-rank
  folders.
- Applied+present rank intersection is literal AND semantics: an indexed row
  must both belong to the selected applied-rank canonical ID and resolve under
  the selected applied-rank folder path before it can enter the population.
- Prompt parser suppresses rank constraints when either picker is set.
- Present-rank picker values used for indexed population must not be re-added
  as a `rank_match` hard-filter family for the same root search; the picker is
  a population pre-filter, with prompt-derived rank text routed only to
  observability.
- Population-scoped rank pickers still appear on the observability constraint
  surface with a population-scope reason, even when the free-text prompt has no
  rank phrase.
- Valid present-rank IDs with no indexed candidates return an empty 200 search
  result with a `PRESENT_RANK_NOT_INDEXED` notice, not a validation error.
- Tests for prompt-with-rank-phrase under each picker state.

### PR-5 (rank): Present-rank Needs Review polish

- Ensure rank UNKNOWN results render clearly in the existing general
  Needs Review section.
- No dedicated drill-down view, no flag-for-review action, no manual
  tagging UI.
- Copy should make the limitation explicit, e.g. `"Could not determine
  current/present rank from this resume."`
- Rank Needs Review result cards may carry a sanitized
  `needs_review_rank_summary` string so saved/recovered drafts can show the
  same explicit copy without preserving raw hard-filter details.
- Streamed `unknown_match` payloads include `result_bucket = "needs_review"`,
  the same value used by existing Needs Review surfaces. No new bucket values
  are introduced.
- Tests verify rank UNKNOWN entries use the general Needs Review surface
  and do not imply a separate rank-review bucket.

### Phase 2 PR-1 (rank): Cross-folder present-rank search

- Root searches may provide `present_rank` without `applied_rank`,
  `rank_folder`, or `rank_folder_id`.
- Blank applied rank + present rank set resolves candidate population from
  `present_rank_index[present_rank]` across all applied-rank folders.
- Applied rank + present rank keeps the PR-4 AND semantics: an indexed row
  must match both the selected applied-rank canonical ID and selected folder
  path prefix.
- Blank applied rank + blank present rank still returns `RANK_SCOPE_REQUIRED`.
- Invalid present-rank values still return `PRESENT_RANK_INVALID` before
  analyzer construction on both `/analyze_stream` and `/analyze`.
- The UI search gate allows either rank picker, and the applied-rank picker
  includes an explicit "All applied ranks" blank option.
- Cross-folder result cards carry a sanitized `downloaded_rank_folder` so
  preview links and saved/recovered drafts can resolve the source folder
  without relying on the selected applied-rank picker.
- Refinement of a cross-folder present-rank root search inherits the saved
  candidate scope and resolves preflight against the corpus root; it must not
  require an applied-rank folder.
- Cross-folder index population ignores unsafe relative paths containing `.`
  or `..` components and ignores root-level files without a rank-folder path
  component.
- Tests cover `/analyze_stream`, `/analyze`, and analyzer indexed population
  rooted at the corpus root.

### Phase 2 PR-2 (rank): Rank picker defaults

- The applied-rank and present-rank picker remembers local UI defaults in
  actor-scoped browser storage. Storage keys require a user ID or username;
  role-only identities do not persist defaults, and username fallbacks are
  stored with opaque key material.
- The frontend applies restored defaults only after a successful
  `/get_rank_folders` response and only when that live catalog still contains
  the stored applied-rank folder and present-rank ID.
- Blank applied-rank selection is preserved as the explicit "All applied ranks"
  state; it must not be coerced to the first folder.
- Present-rank defaults are remembered per applied-rank selection, including
  the blank "All applied ranks" selection.
- When a parent applied-rank picker value changes, the child present-rank value
  is restored only from that parent's saved child default or cleared. Child
  defaults must not persist nonblank values inherited during parent changes.
- Recovery drafts, completed search-session context, and refinement parent
  context take precedence over remembered defaults.
- Defaults are not restored while a recovery-draft fetch is in flight. Once
  recovery settles, default restore is evaluated against the live picker state
  so recovered/current picker values keep precedence without stale closures.
- Remembered defaults do not change `/analyze_stream` payload shape,
  `/analyze` payload shape, request fingerprints, backend validation, query
  planning, index contents, or hard-filter behavior.
- Logout clears in-memory picker state. Actor-scoped local defaults remain
  in browser storage and must not restore for a different authenticated actor.
- Tests cover actor scoping, stale-catalog rejection, blank applied-rank
  preservation, and per-applied-rank present-rank restoration.

### Phase 2 PR-3 (rank): Cross-folder search context polish

- Historical search-step navigation restores both rank pickers from that
  step's `search_context`; cross-folder present-rank searches restore blank
  applied rank and the saved present-rank ID.
- Saved/recovered result payloads preserve sanitized rank search context fields:
  `rank_folder`, `applied_rank`, `present_rank`, `rank_folder_id`, and
  `download_root_id`.
- Cross-folder result cards preserve sanitized `downloaded_rank_folder` per
  result so preview links keep using the candidate's source folder after
  completion and recovery.
- This polish does not change `/analyze_stream` payload shape, `/analyze`
  payload shape, request fingerprints, backend validation, query planning,
  index contents, hard-filter behavior, or prompt parsing.
- Tests cover cross-folder picker-state restoration from `search_context` and
  recovery sanitization for cross-folder rank context plus result source folder.

### PR-6 (age): Age range picker

- Add `Candidate Age` minimum/maximum inputs to `frontend.html`.
- Normalize UI values into the existing `age_years` hard constraint.
- Suppress prompt-derived age constraints when either picker value is set.
- Reuse the existing `age_range` evaluator and result surfaces.
- Add `AGE_FILTER_INVALID` validation for invalid values.
- Capture and audit the age reference date used for evaluation.
- Tests: one-sided bounds, inclusive range, invalid values, prompt-picker
  suppression, reference-date determinism, PASS / FAIL / UNKNOWN result
  display.

### PR-7 (authority): Alias data + canonicalization

- Create `candidate_facts/aliases/` directory and `__init__.py`.
- Add `coc_issue_authority.json` seeded with 25-30 canonical authorities
  derived from the IMO STCW White List and a corpus frequency survey.
- Add JSON schema validator that runs at module load.
- Refactor `_normalize_coc_country` → generic `_normalize_alias(value,
  alias_map)`; update country callsite to use it.
- Add `_normalize_coc_issue_authority` wrapper.
- Update CoC extractor to populate `issue_authority_canonical`.
- Update `candidate_facts/extractors/seajobs.py` to pass the field
  through.
- Update `candidate_facts/schema.py` to validate the new field.
- Tests: alias resolution, JSON validator, country normalization
  regression, extractor regression.
- No UI, no query plan, no new hard filter family.

### PR-8 (authority): Hard filter family

- Add `coc_issue_authority_match` to the catalog.
- Add constraint extractor and evaluator (mirror country).
- Add schema entry.
- Remove `issue_authority` fallback from
  `shadow_llm_provider.py:1514`.
- Update LLM system prompt with country-vs-authority examples.
- Tests for PASS, FAIL, UNKNOWN, multi-authority OR, AND with country.
- Extend shadow eval corpus with authority-specific prompts. Re-run
  shadow eval; zero regressions on country prompts is the merge gate.

### PR-9 (authority): UI picker + "Needs alias review"

- Multi-select dropdown for `CoC Issue Authority` in `frontend.html`.
- Wire picker value into the search request and query plan.
- Result cards display canonical label + raw on hover when family fired.
- "Needs alias review" view and flag-for-review action.
- E2E tests for picker-driven searches.

### PR-10 (deferred, optional)

Hierarchical UX: narrow authority dropdown by selected country. Pure
UI/JS work.

### PR-11 (deferred, optional)

Index-backed authority lookup for sub-second filter performance.

## Test Fixtures by PR

### PR-1 (rank index)

- `test_index_builds_from_candidate_facts`.
- `test_index_refresh_detects_new_file`.
- `test_index_refresh_detects_deleted_file`.
- `test_index_refresh_detects_modified_file`.
- `test_index_version_increments_on_rebuild`.
- `test_reindex_cli_command`.

### PR-2 (rank UI)

- `test_search_button_disabled_when_both_pickers_blank` (E2E).
- `test_helper_text_visible_below_pickers` (E2E).
- `test_index_status_visible_in_header` (E2E).
- `test_reindex_button_triggers_rebuild` (E2E).

### PR-3 (rank backend)

- `test_backend_rejects_both_blank`: returns `RANK_SCOPE_REQUIRED`.
- `test_backend_accepts_applied_only`: scopes to folder.
- `test_backend_accepts_present_only`: scopes to index.
- `test_backend_accepts_both`: intersection.
- `test_backend_rejects_missing_folder`:
  `APPLIED_RANK_FOLDER_NOT_FOUND`.
- `test_backend_rejects_unknown_applied_rank`: returns
  `APPLIED_RANK_INVALID`.
- `test_backend_rejects_unknown_present_rank`: returns
  `PRESENT_RANK_INVALID`.
- `test_backend_returns_empty_for_valid_unindexed_present_rank`:
  `PRESENT_RANK_NOT_INDEXED` notice, 200, empty result.
- `test_ai_search_entry_validates`.
- `test_scheduled_search_loader_validates`: confirms scheduled task
  execution honors the runtime validator and returns
  `RANK_SCOPE_REQUIRED` for stored payloads with both rank fields null.
  Does *not* assert saved-payload migration (deferred — see Phase 1
  Scope).
- `test_hard_filter_dispatcher_validates`.

### PR-4 (rank prompt interaction)

- `test_prompt_rank_suppressed_when_present_rank_picker_set`.
- `test_prompt_rank_suppressed_when_applied_rank_picker_set`.
- `test_prompt_rank_suppressed_when_both_pickers_set`.
- `test_prompt_rank_legacy_path_still_works`: covers any non-AI-Search
  entry point that still emits rank constraints from prompt text alone
  (e.g., internal eval harnesses, the shadow LLM legacy adapter, CLI
  scripts that bypass the picker UI). Identify the concrete callers
  during PR-4; if no legitimate caller exists, drop this test and
  fully deprecate the prompt-rank path. Production AI Search requests
  always go through the validator and never reach this code path
  with both pickers blank.

### PR-6 (age picker)

Validation:

- `test_age_filter_accepts_min_only`.
- `test_age_filter_accepts_max_only`.
- `test_age_filter_accepts_inclusive_range`.
- `test_age_filter_accepts_picker_range_edges`: `18` and `80` are valid.
- `test_age_filter_rejects_non_integer`.
- `test_age_filter_rejects_decimal`.
- `test_age_filter_rejects_negative_or_zero`.
- `test_age_filter_rejects_out_of_picker_range`.
- `test_age_filter_rejects_min_greater_than_max`.
- `test_age_filter_invalid_returns_detail_code`: covers
  `non_integer`, `non_positive`, `out_of_range`, and
  `inverted_bounds`.

Prompt interaction:

- `test_prompt_age_suppressed_when_age_picker_min_set`.
- `test_prompt_age_suppressed_when_age_picker_max_set`.
- `test_prompt_age_upper_bound_suppressed_when_min_picker_set`:
  picker `minimum_years = 30`, prompt `"below 45"` emits only the picker
  lower bound.
- `test_prompt_age_legacy_path_still_works_when_picker_blank`.

Evaluator/display:

- `test_age_filter_passes_candidate_inside_range`.
- `test_age_filter_passes_candidate_at_inclusive_minimum`.
- `test_age_filter_passes_candidate_at_inclusive_maximum`.
- `test_age_filter_fails_candidate_outside_range`.
- `test_age_filter_unknown_when_age_missing`.
- `test_age_filter_uses_request_reference_date`.
- `test_age_filter_ands_with_other_hard_filters`.
- `test_age_filter_result_reason_is_human_readable`.
- `test_age_filter_omits_result_line_when_no_age_constraint_fired`.

### PR-7 (authority data)

Extraction and canonicalization:

- `test_alias_resolves_canonical_form_exact`.
- `test_alias_resolves_with_punctuation_variants`.
- `test_alias_resolves_with_case_variants`.
- `test_alias_unresolved_returns_null`.
- `test_country_alias_normalization_regression`: `"Indian"` → `"india"`
  unchanged after refactor.

JSON validator:

- `test_json_loads_valid_file`.
- `test_json_validator_rejects_missing_version`.
- `test_json_validator_rejects_duplicate_canonical`.
- `test_json_validator_rejects_duplicate_alias_across_authorities`.
- `test_json_validator_rejects_unknown_country_canonical`.
- `test_json_validator_rejects_empty_aliases_list`.

Schema:

- `test_schema_accepts_null_issue_authority_canonical`.
- `test_schema_accepts_well_formed_canonical_id`.
- `test_schema_rejects_malformed_canonical_id`.

Extractor:

- `test_coc_extraction_populates_canonical_when_resolvable`.
- `test_coc_extraction_leaves_canonical_null_when_unresolvable`.
- `test_coc_extraction_preserves_raw_issue_authority`.

## Phase 2

Tracked in GitHub Issue #46 (filed alongside PR-0). Phase 2 covers:

### Rank Search Scope

- Cross-folder present-rank root search: blank applied rank + set
  present rank searches the present-rank index across all applied-rank
  folders.
- Per-folder default picker preferences.
- Persisted index on disk for faster cold start.
- Saved searches and scheduled tasks: migration path to require rank
  pickers in their saved configuration.

Note: recruiter-driven manual `present_rank` tagging is explicitly
**not** deferred to Phase 2. Present-rank UNKNOWN results use the general
Needs Review surface (see "Present-Rank Needs Review" above). If extraction
reliability turns out worse than expected in production, manual tagging or
a dedicated rank-review workflow can be revisited as a standalone feature,
but it is not on the Phase 2 roadmap. Similarly, the previously-considered
"hide `applied_rank` behind an advanced toggle" option is dropped —
the two-picker model is locked.

### Age Range Filter

- Recruiter-driven manual age correction, only if UNKNOWN volume is high
  enough to justify a correction workflow.
- DOB / age backfill tooling, only if the existing candidate-facts
  extraction misses a material portion of the corpus.

No Phase 2 age work is planned by default. The Phase 1 min/max picker is
complete if existing extraction provides reliable age evidence.

### CoC Issue Authority

- Hierarchical UX: narrow authority dropdown by selected country.
- Index-backed authority lookup for sub-second filter performance.
- Recruiter-driven alias additions via a moderated UI workflow if the
  "Needs review" backlog cannot be drained by engineering at the
  required cadence.
- Migration of inline country aliases to a JSON file under
  `candidate_facts/aliases/`, if the inconsistency between alias storage
  patterns becomes a real maintenance cost.
- Cross-family co-occurrence: a generalized `same_row` logical group
  (also tracked in `vessel_tonnage_v1.md`) could eventually support
  combinations like `coc_issue_authority + coc_grade` on the same
  certificate, instead of independent matching across CoCs.
