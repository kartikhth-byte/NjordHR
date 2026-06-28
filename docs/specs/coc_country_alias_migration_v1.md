# CoC Country Alias Migration v1

## Status

Planning spec. No runtime behavior changes are introduced by this document.

## Problem

CoC country aliases currently live inline in two places:

- `ai_analyzer.py::_coc_country_aliases` (around line 2516 as of PR #95),
  used by prompt parsing, candidate-evidence extraction, and hard-filter
  evaluation.
- `backend_server.py::_coc_issue_authority_country_aliases` (around line 288
  as of PR #95), used to validate `coc_issue_authority.json`
  `country_canonical` values.

Those maps intentionally differ today. The analyzer map is broad and includes
long-tail countries, demonyms, and guarded ambiguous shortcuts. The backend map
is an authority-catalog allow-list, not the full analyzer country map. Future
country/authority picker work needs one auditable country vocabulary source
without widening backend validation or changing analyzer extraction behavior.

## Goals

- Move CoC country aliases to a JSON file under `candidate_facts/aliases/`.
- Preserve exact analyzer behavior for prompt-side country constraints,
  candidate-evidence extraction, and hard-filter normalization.
- Preserve exact backend issue-authority validation behavior by carrying an
  explicit `authority_catalog_allowed` flag per country. PR-2 must not widen
  the set of accepted `country_canonical` values.
- Keep ambiguous shortcut behavior explicit:
  - `in` is allowed only where `_coc_country_aliases(include_ambiguous_shortcuts=True)` currently allows it.
  - `us` / `u s` are excluded from snippet and issue-authority country matching where `include_ambiguous_shortcuts=False`.
- Keep recruiter-facing labels display-only. Audit payloads continue to store
  canonical country IDs.

## Non-Goals

- No new CoC country picker UI.
- No recruiter-managed alias editing.
- No saved-payload migration.
- No change to CoC issue-authority alias IDs.
- No broadening of country extraction beyond the current inline analyzer map.
- No widening of backend issue-authority country validation beyond the current
  inline backend allow-list.

## JSON Location

`candidate_facts/aliases/coc_country.json`

## JSON Shape

```json
{
  "version": "1.0.0",
  "last_updated": "YYYY-MM-DD",
  "source": "Migrated from ai_analyzer.py _coc_country_aliases",
  "countries": {
    "india": {
      "display_label": "India",
      "authority_catalog_allowed": true,
      "aliases": ["India", "Indian"],
      "ambiguous_shortcuts": ["in"]
    },
    "usa": {
      "display_label": "USA",
      "authority_catalog_allowed": false,
      "aliases": ["USA", "United States", "American"],
      "ambiguous_shortcuts": ["US", "U S"]
    }
  }
}
```

Rules:

- `version` must match `x.y.z`.
- `last_updated` must match the exact `YYYY-MM-DD` shape.
- `countries` keys are canonical IDs.
- Canonical IDs use lowercase words separated by spaces where the current
  analyzer map does so, e.g. `marshall islands`, `south africa`, `saudi arabia`.
- `display_label` is required and non-empty.
- `authority_catalog_allowed` is required and boolean.
- `aliases` is required and non-empty.
- `ambiguous_shortcuts` is optional and defaults to `[]`.
- v1 ambiguous shortcuts are a closed list:
  - `india`: `["in"]`
  - `usa`: `["US", "U S"]`
- Every canonical ID must normalize to itself through its `aliases` list.
- Every normalized alias-map value must match a `countries` key.

## Loader Contract

Add a loader next to the existing authority alias loader:

- `candidate_facts/aliases/coc_country.py`
- `load_coc_country_aliases(path=ALIAS_FILE) -> CocCountryAliases`

The loaded object exposes:

- `version`
- `raw`
- `alias_map`
- `alias_map_without_ambiguous_shortcuts`
- `authority_country_alias_map`
- `display_labels`
- `ambiguous_shortcuts_by_country`

Normalization must continue to use `normalize_alias_key` from the alias module
family so country and authority alias behavior stay byte-equivalent.

Map semantics are closed-list:

- `alias_map` is normalized `aliases` plus normalized `ambiguous_shortcuts`.
- `alias_map_without_ambiguous_shortcuts` is normalized `aliases` only.
- `authority_country_alias_map` is normalized `aliases` only, restricted to
  countries where `authority_catalog_allowed` is `true`.

The JSON storage layout does not need to match the current inline Python layout
when the current inline layout keeps a shortcut in one mode and subtracts it in
another mode. Parity compares the returned maps and consuming behaviors, not
the storage representation.

## Validation Contract

The loader must fail loudly at module/test load for:

- missing root keys
- malformed `version`
- malformed `last_updated`
- non-object `countries`
- empty or malformed canonical ID
- missing or empty `display_label`
- missing or non-boolean `authority_catalog_allowed`
- missing, empty, or non-list `aliases`
- non-list `ambiguous_shortcuts`
- ambiguous shortcut outside the v1 closed list
- empty normalized alias
- duplicate normalized alias across countries within the same exposed map
- duplicate normalized alias between `aliases` and `ambiguous_shortcuts`
- canonical ID missing from its own normalized aliases
- alias-map value not present as a `countries` key

PR-1 validation tests must also load the existing `coc_issue_authority.json`
catalog and assert every `country_canonical` value has
`authority_catalog_allowed=true`.

## Runtime Integration Contract

Analyzer:

- `_coc_country_aliases(include_ambiguous_shortcuts=True)` returns
  `alias_map`.
- `_coc_country_aliases(include_ambiguous_shortcuts=False)` returns
  `alias_map_without_ambiguous_shortcuts`.
- `_coc_country_display_label(country)` reads JSON `display_labels`, with the
  current `UK` / `UAE` / `USA` casing preserved by data rather than hardcoded
  display exceptions.

Backend:

- `_coc_issue_authority_country_aliases()` returns
  `authority_country_alias_map`.
- The return type is `Mapping[str, str]`, with normalized alias keys mapped to
  canonical country IDs, matching the current backend helper signature.
- `load_coc_issue_authority_aliases(..., country_aliases=...)` continues to
  validate every `country_canonical` value against that authority allow-list.

Caching:

- Analyzer and backend loaders keep process-level caches.
- Tests that mutate alias files reset those caches explicitly.

Kill switch:

- PR-2 adds `COC_COUNTRY_ALIAS_SOURCE=inline|json`.
- The default is `json` after PR-2 switches runtime call sites.
- `inline` restores the pre-migration inline maps for analyzer and backend
  country alias helpers without changing request payloads or result shapes.
- PR-3 removes the kill switch after JSON-backed runtime tests are green.

## Parity Requirements

PR-1 must include pre-migration parity tests before any runtime switch:

- Snapshot the current inline analyzer broad map at the PR commit.
- Snapshot the current inline analyzer no-ambiguous-shortcuts map at the PR
  commit.
- Snapshot the current inline backend authority-country allow-list at the PR
  commit.
- Assert JSON `alias_map` equals the analyzer broad snapshot.
- Assert JSON `alias_map_without_ambiguous_shortcuts` equals the analyzer
  no-ambiguous-shortcuts snapshot.
- Assert JSON `authority_country_alias_map` equals the backend allow-list
  snapshot exactly. No extra countries are accepted.
- Assert JSON `display_labels[id]` equals current
  `_coc_country_display_label(id)` for every canonical ID, preserving
  `UK` / `UAE` / `USA` casing.

If an inline map changes before PR-2 lands, the same commit that changes the
inline map must update the PR-1 parity snapshot and JSON data.

PR-1 or PR-2 must include behavior-level parity tests that run inline-backed
and JSON-backed implementations through the consuming functions:

- `_normalize_coc_country`
- `_extract_coc_country_from_snippet`
- `_is_coc_country_phrase_candidate`
- `_coc_issue_authority_prompt_country_for_alias`

The behavior fixture must contain at least 20 cases and cover:

- every `country_canonical` value currently present in `coc_issue_authority.json`
- ambiguous shortcut tokens `in`, `us`, and `u s` in positive and negative prose
- multi-word country names and demonyms
- employer, vessel, and route false positives
- prompt/snippet connective handling around authority aliases

Required named cases:

- `Indian CoC` -> `india`
- `Iranian CoC` -> `iran`
- `American CoC holder` -> `usa`
- `COC issued in 2019` -> no country
- `give us coc details` -> no country
- `USA-issued certificate of competency` -> `usa`
- `indian ocean route` without CoC context -> no country

## Rollout PRs

### PR-1: JSON + Loader + Validator

- Add `coc_country.json`.
- Add the loader/validator.
- Add loader validation tests.
- Add snapshot parity tests against the current inline analyzer and backend
  maps.
- Add display-label parity tests.
- Add `coc_issue_authority.json` `country_canonical` allow-list validation.
- No analyzer/backend runtime switch yet.

### PR-2: Analyzer + Backend Switch

- Add `COC_COUNTRY_ALIAS_SOURCE=inline|json`.
- Switch analyzer country aliases to the loader under the `json` source.
- Switch backend authority-country validation to
  `authority_country_alias_map` under the `json` source.
- Preserve all existing CoC country, authority, prompt, snippet, and evaluator
  tests.
- Add behavior-level parity tests for prompt/snippet paths.
- Add cache-reset tests for loader updates where applicable.
- Log the active alias source at startup.

### PR-3: Remove Inline Maps

- Remove the inline country maps once parity and switched-runtime tests are
  green.
- Remove `COC_COUNTRY_ALIAS_SOURCE`.
- Keep a single source of truth in `coc_country.json`.
- Update closeout docs and backlog.

## Review Checklist

- No canonical ID leaks in recruiter-facing text.
- No widening of ambiguous shortcuts in snippet paths.
- No widening of backend issue-authority country validation.
- No divergence between `/analyze` and `/analyze_stream`.
- No CoC Issue Authority validator regression.
- No prompt-picker arbitration changes.
- No saved/recovered draft behavior changes.
- Behavior-level parity covers prompt, snippet, candidate phrase, and
  issue-authority alias-country arbitration paths.
