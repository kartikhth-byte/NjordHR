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
- A "Needs rank review" bucket surfacing candidates whose `present_rank`
  could not be extracted.

Excludes (deferred to later phases):

- Migrating the *saved configuration* of existing saved searches or
  scheduled tasks to require rank pickers (the runtime validator below
  still applies to every entry point in v1 — see "Audit Targets"; what's
  deferred is rewriting stored payloads, surfacing migration warnings in
  the saved-search UI, and any backfill of pre-existing records).
- Cross-folder bulk operations.
- Per-folder default picker preferences.
- Persisted index on disk.

Excludes (dropped, **not** deferred — see "Needs Rank Review Bucket"
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
`present_rank` remains null. The candidate appears in the "Needs rank
review" bucket but is invisible to `present_rank`-scoped searches.

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

### "Needs Rank Review" Bucket

**Informational only in v1.** A header-level count surfaces how many
candidates have null `present_rank` (e.g., `"5 candidates couldn't be
auto-ranked — contact engineering"`). No drill-down view, no
flag-for-review action, no manual tagging UI. Recruiters see the gap
exists; the fix path is an engineer patching the extractor or
hand-editing the candidate_facts entry.

Rationale: extraction reliability is high enough that the bucket stays
small, and adding a tagging UI introduces overwrite-conflict surface
area (two recruiters disagreeing on the same candidate) for marginal
benefit. If extraction reliability turns out worse than expected in
production, manual tagging can be added later as a separate feature —
it is explicitly **not** deferred to Phase 2.

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
- Helper text: `"Optional. Candidates pass when their extracted age is
  within the selected range."`
- Both fields blank: no age filter.
- One field set: one-sided bound.
- Both fields set: inclusive range.

The UI should reject non-integer, negative, zero, and out-of-range values
before submitting the request.

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
  `AGE_FILTER_INVALID`.
- Any non-positive, non-integer, or out-of-range value: reject with
  `AGE_FILTER_INVALID`.

Allowed picker range is 18-80 inclusive. This mirrors the current
deterministic prompt parser's conservative age range and avoids accidental
matches on unrelated numeric fields.

### Prompt-Picker Interaction

When either age picker field has a value, prompt-derived age constraints
are suppressed. Picker values are treated as the structured source of
truth.

Examples:

- Picker: `minimum_years = 30`, `maximum_years = 50`. Prompt:
  `"candidate below 45 with valid passport"`. -> Passport constraint only;
  age comes from the picker.
- Picker: `minimum_years = 30`, `maximum_years = null`. Prompt:
  `"young candidates with US visa"`. -> US visa constraint only; age comes
  from the picker.
- Picker: both blank. Prompt: `"between 30 and 50 years old"`. -> Existing
  prompt parser emits the `age_range` / `age_years` constraint.

### Evaluator Semantics

The evaluator reuses the existing `age_range` behavior:

- Candidate age inside inclusive bounds -> `PASS`.
- Candidate age outside bounds -> `FAIL`.
- Candidate age missing or ambiguous -> `UNKNOWN`.

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

- "Needs rank review" — candidates where `present_rank` extraction
  failed. **Informational count only** in v1 (no drill-down, no
  flag-for-review action). See the "Needs Rank Review Bucket" section
  for rationale.
- Age filter UNKNOWN results — candidates where age evidence is missing or
  ambiguous. Uses the existing hard-filter Needs Review surface; no
  separate age-specific review queue in v1.
- "Needs alias review" — CoCs where `issue_authority` did not resolve.
  Exposes the raw input, available context, and a flag-for-review
  action.

Engineering owns the fix workflow in v1. The asymmetry
(rank-review is counter-only, alias-review is actionable) reflects
that alias additions are bounded data entry while rank tagging
introduces overwrite-conflict surface area.

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
- Tests: index builds correctly, refresh detects file changes, version
  increments on rebuild.
- No UI changes visible to recruiters yet.

### PR-2 (rank): Search UI — two rank pickers

- Replace folder picker with `applied_rank` and `present_rank` pickers.
- Wire pickers to the search request shape.
- Add helper text and Search-button disable logic.
- Add the index-header status display + Reindex button.
- No backend validation yet (PR-3 follows).

### PR-3 (rank): Backend validator + audit

- Add `RANK_SCOPE_REQUIRED` validator at the search entry point.
- Audit and update every code path that previously fell through to
  unbounded scan.
- Add `APPLIED_RANK_FOLDER_NOT_FOUND` error.
- Tests for all four picker states.

### PR-4 (rank): Query plan + prompt parser interaction

- Pickers resolve before query plan execution.
- Prompt parser suppresses rank constraints when either picker is set.
- Tests for prompt-with-rank-phrase under each picker state.

### PR-5 (rank): "Needs rank review" count

- Header-level count of candidates with null `present_rank`.
- No drill-down view, no flag-for-review action, no manual tagging UI.
- Copy: `"N candidates couldn't be auto-ranked — contact engineering."`
- Tests verify the count updates on reindex and renders only when
  non-zero.

### PR-6 (age): Age range picker

- Add `Candidate Age` minimum/maximum inputs to `frontend.html`.
- Normalize UI values into the existing `age_years` hard constraint.
- Suppress prompt-derived age constraints when either picker value is set.
- Reuse the existing `age_range` evaluator and result surfaces.
- Add `AGE_FILTER_INVALID` validation for invalid values.
- Tests: one-sided bounds, inclusive range, invalid values, prompt-picker
  suppression, PASS / FAIL / UNKNOWN result display.

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
- `test_age_filter_rejects_non_integer`.
- `test_age_filter_rejects_negative_or_zero`.
- `test_age_filter_rejects_out_of_picker_range`.
- `test_age_filter_rejects_min_greater_than_max`.

Prompt interaction:

- `test_prompt_age_suppressed_when_age_picker_min_set`.
- `test_prompt_age_suppressed_when_age_picker_max_set`.
- `test_prompt_age_legacy_path_still_works_when_picker_blank`.

Evaluator/display:

- `test_age_filter_passes_candidate_inside_range`.
- `test_age_filter_fails_candidate_outside_range`.
- `test_age_filter_unknown_when_age_missing`.
- `test_age_filter_result_reason_is_human_readable`.

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

- Per-folder default picker preferences.
- Persisted index on disk for faster cold start.
- Saved searches and scheduled tasks: migration path to require rank
  pickers in their saved configuration.

Note: recruiter-driven manual `present_rank` tagging is explicitly
**not** deferred to Phase 2. The "Needs Rank Review" bucket is
informational only (see "Needs Rank Review Bucket" above). If
extraction reliability turns out worse than expected in production,
manual tagging can be revisited as a standalone feature, but it is
not on the Phase 2 roadmap. Similarly, the previously-considered
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
