# Engine Experience Layers v1

## Goal

Represent recruiter-facing engine searches in two layers:

- A **deterministic engine layer** for exact engine families, explicit
  manufacturer hierarchies, and auditable fallback behavior.
- A **semantic technical-experience layer** for broader propulsion,
  automation, compliance, and systems concepts that do not map cleanly to a
  single engine family.

The split is intentional. Recruiters often search using a mixture of exact
model strings such as `ME-GI` and broad technical concepts such as
`diesel-electric`, `Tier III`, or `bunkering`. The deterministic layer should
own the former. This v1 spec **ships the deterministic layer** and defines the
boundary for the semantic layer. It does **not** ship a semantic evaluator in
this phase.

Primary v1 use cases:

- `has me engine type experience`
- `has man b&w experience`
- `has dual fuel engine experience`
- `has electronically controlled engine experience`
- `has x-df or me-gi experience`

Future-phase technical-search examples captured for the follow-up semantic spec:

- `has diesel-electric propulsion and high voltage experience`
- `has tier iii / scrubber / egr experience`

## Scope Split

### Deterministic Layer Includes

- Exact engine family and subtype normalization.
- Manufacturer and lineage hierarchy maps.
- Exact-match deterministic evaluation.
- Descendant-match deterministic evaluation.
- Lower-confidence family-level fallback for subtype prompts.
- Lower-confidence manufacturer-level fallback for family/subtype prompts.
- Broad engine buckets that can be derived from explicit subtype membership.

### Semantic Layer Includes

- Propulsion-system concepts that are often expressed indirectly.
- Emissions and environmental-system experience.
- Architecture and machinery-layout concepts.
- Composite technical intent that mixes engine family, certifications, and
  vessel-system experience in one search clause.

### v1 implementation boundary

v1 implements the deterministic layer only. The semantic layer in this document
is an inventory and boundary definition for a follow-up spec. Semantic-only and
cross-layer hybrid execution are therefore **out of scope for v1
implementation**.

## Locked Decisions

| Decision | Value |
|---|---|
| `B&W` vs `MAN B&W` | Treat as the same legacy/two-stroke family bucket for deterministic matching |
| Why `B&W` satisfies `MAN` | Intentional; in modern recruiter/resume usage, `B&W` is treated as part of the broader MAN lineage for deterministic search |
| `MAN` vs `MAN B&W` | Distinct canonical nodes; `MAN` is broader manufacturer-level evidence |
| Specific subtype request + ancestor-only evidence | Lower-confidence fallback, not a normal deterministic pass |
| Family fallback destination | `Needs Review` bucket, not standard Verified Matches |
| Manufacturer fallback destination | `Needs Review` bucket, not standard Verified Matches |
| Default family fallback confidence | `70%` |
| Default manufacturer fallback confidence | `60%` |
| Exact/descendant deterministic match | Existing deterministic pass behavior retained |
| Broad buckets (`dual_fuel`, `electronically_controlled_engine`, `mechanical_engine`) | Deterministic |
| `mechanical_engine` scope | Low-speed two-stroke mechanical bucket in v1; medium-speed and unmodeled manufacturers are excluded unless explicit subtype coverage is added |
| `sulzer` relation to `wartsila_rta` / `wartsila_rt_flex` | Compatibility alias bucket, not a second structural parent in the hierarchy |
| `ME-GA` vs `ME-GI` | Sibling products; both belong to `dual_fuel`; neither inherits the other's evidence |
| WinGD methanol / ethanol naming | Model `X-DF-M/E` as one platform node in v1; `X-DF-M`, `X-DF-M/E`, methanol, and ethanol resume mentions normalize there |
| WinGD LPG / ethane variants | No `X-DF-P` or `X-DF-E` canonical nodes in v1; unsupported until a real commercial product line is evidenced |
| Wartsila two-stroke branding after 2015 | Keep legacy resume/search behavior under `wartsila_*` for v1 even though commercial ownership moved into WinGD |
| Two-stroke / four-stroke recruiter asks | Route to semantic / follow-up spec in v1; do not create deterministic buckets yet |
| Multi-evidence tie-break | Evaluator returns the single strongest outcome per candidate per requested node |
| Reason text placeholders | Use human display labels for requested nodes and normalized raw mention / display label for evidence; never expose canonical ids in UI text |
| `Needs Review` overlap | Existing UNKNOWN reasons and new engine fallback reasons share the same bucket; `reason_code` is the disambiguator |
| Propulsion-system, emissions, and architecture concepts | Reserved for a follow-up semantic spec; not implemented in v1 |
| Extraction context awareness | v1 does **not** distinguish course/training/vessel-spec mentions from sea-time mentions; extracted engine evidence is accepted wherever the normalized engine token is found |
| Resume-internal negation | v1 suppresses engine evidence when a negation phrase such as `no`, `not`, `never`, or `without` appears within the configured look-behind window before the engine mention, constrained to the current sentence / clause window |
| Main vs auxiliary engine role | v1 does not model `engine_role`; all extracted engine evidence is role-agnostic |
| Empty / unextractable resume outcome | Distinct `no_evidence_extracted` path required; never silently conflate extraction failure with genuine absence |
| Compatibility expansion timing | Apply at evaluator time to the requested node set; do not rewrite extracted evidence nodes |
| Sibling-family evidence | Sibling family evidence is `FAIL` in v1 unless a broader family/manufacturer fallback rule explicitly applies |
| Input normalization form | Use Unicode `NFKC` before alias matching |
| Case folding | Use Unicode default case folding (`casefold`-equivalent), not locale-specific casing |
| Diacritic handling | Strip combining marks after decomposition before alias lookup |
| Separator normalization | Normalize dash/ampersand/spacing variants before alias lookup |
| Non-Latin text handling | v1 matches normalized Latin-script substrings only; it does not translate non-Latin prose |
| Map versioning | Every extraction and evaluation record carries a `map_version`; canonical ids are append-only and retirements require a migration entry |

## Canonical Model

The deterministic layer should model three levels explicitly.

### 1. Manufacturer

- `man`
- `wingd`
- `wartsila`
- `sulzer`
- `mitsubishi`
- `caterpillar`
- `mak`
- `yanmar`
- `bergen`
- `pielstick`
- `himsen`
- `daihatsu`
- `niigata`
- `doosan`
- `mtu`
- `detroit_diesel`

### 2. Family / legacy brand bucket

- `man_b_w`
- `wingd_x_engines`
- `wartsila_rta`
- `wartsila_rt_flex`
- `wartsila_dual_fuel`
- `mitsubishi_uec`

### 3. Specific subtype / model line

- `man_b_w_mc`
- `man_b_w_me`
- `man_b_w_me_b`
- `man_b_w_me_c`
- `man_b_w_me_gi`
- `man_b_w_me_c_gi`
- `man_b_w_me_ga`
- `man_b_w_me_lgi`
- `man_b_w_me_lgim`
- `man_b_w_me_lgip`
- `man_b_w_me_lgia`
- `man_b_w_me_gie`
- `wingd_x_df`
- `wingd_x_df_m_e`
- `wingd_x_df_a`
- `wingd_x_df_hp`
- `mitsubishi_uec_lsii`
- `mitsubishi_uec_lse`
- `mitsubishi_uec_lsh`
- `mitsubishi_uec_lsj`

## Deterministic Hierarchy

The evaluator should not infer hierarchy from string prefixes alone. Maintain
explicit parent/child relationships.

### MAN / MAN B&W

- `man`
  - `man_b_w`
    - `man_b_w_mc`
    - `man_b_w_me`
      - `man_b_w_me_b`
      - `man_b_w_me_c`
      - `man_b_w_me_gi`
      - `man_b_w_me_c_gi`
      - `man_b_w_me_ga`
      - `man_b_w_me_lgi`
      - `man_b_w_me_lgim`
      - `man_b_w_me_lgip`
      - `man_b_w_me_lgia`
      - `man_b_w_me_gie`

### WinGD

- `wingd`
  - `wingd_x_engines`
    - `wingd_x_df`
    - `wingd_x_df_m_e`
    - `wingd_x_df_a`
    - `wingd_x_df_hp`

### Wartsila / Sulzer

- `wartsila`
  - `wartsila_rta`
  - `wartsila_rt_flex`
  - `wartsila_dual_fuel`

`sulzer` is **not** a second structural parent. In v1 it is a legacy-brand
compatibility bucket that can map to `sulzer`, `wartsila_rta`, and
`wartsila_rt_flex` at prompt/evaluator time, but `parent_family_id` remains
singular on extracted evidence.

### Mitsubishi

- `mitsubishi`
  - `mitsubishi_uec`
    - `mitsubishi_uec_lsii`
    - `mitsubishi_uec_lse`
    - `mitsubishi_uec_lsh`
    - `mitsubishi_uec_lsj`

### Other manufacturers

These remain manufacturer-level only in v1 until subtype coverage is added:

- `mak`
- `yanmar`
- `bergen`
- `caterpillar`
- `pielstick`
- `himsen`
- `daihatsu`
- `niigata`
- `doosan`
- `mtu`
- `detroit_diesel`

## Resume-Side Normalization

Extract only what the resume actually proves.

### Input normalization pipeline

Apply the following pipeline before alias matching:

1. Unicode normalize input with `NFKC`.
2. Case-fold using Unicode default case folding (`casefold`-equivalent).
3. Decompose and strip combining marks for alias lookup.
4. Normalize all dash-like separators (`‐`, `‑`, `‒`, `–`, `—`, `−`) to ASCII
   `-`.
5. Normalize ampersand variants and textual joiners:
   - `＆` -> `&`
   - `B and W` / `B AND W` between single-letter tokens -> `B&W`
   - `B+W` -> `B&W`
6. Collapse trademark / registration symbols such as `™`, `®`, `©`.
7. Treat a single inner space as token-equivalent to a hyphen for alias lookup
   where the alias table expects hyphenated model names.
8. Ignore surrounding punctuation such as trailing `.`, `,`, or `;`.

This pipeline is how near-miss variants such as `ME GI`, `ME−GI`, `RT flex`,
`MAN-B&W`, `B＆W`, and fullwidth Latin strings are collapsed before lookup.

### Script policy

v1 extraction operates on normalized Latin-script substrings only. Resumes may
contain Korean, Japanese, Chinese, Cyrillic, Greek, or other non-Latin prose,
but the deterministic engine extractor does not translate it. If a Latin engine
token such as `MAN B&W 6S60ME-C` appears inside non-Latin prose, the Latin
substring is still eligible for matching.

### Context and negation policy

v1 is intentionally token-driven and does not yet model:

- training/course context vs sea-time context
- vessel-spec blocks vs sea-service blocks
- observation / familiarization-only phrasing

v1 does suppress straightforward negation before an engine mention, using a
bounded look-behind window (currently 80 characters and no more than 4
intervening tokens) inside the current sentence / clause window for phrases
such as:

- `no ME experience`
- `never operated RT-flex`
- `without X-DF background`

This reduces a known false-positive class, but it is still heuristic. Cases
such as broader prose within the same sentence or clause may still be
suppressed incorrectly and should be surfaced via telemetry. A follow-up
extraction-hardening spec should add stronger context classification.

### Explicit rules

- `B&W` -> `man_b_w`
- `MAN B&W` -> `man_b_w`
- `MAN & B&W` -> `man_b_w`
- `MAN BW` -> `man_b_w`
- `MAN-B&W` -> `man_b_w`
- `B and W` -> `man_b_w`
- `B+W` -> `man_b_w`
- longest-match wins, so `MAN B&W` is resolved before plain `MAN`
- plain `MAN` -> `man`
- `MAN B&W ME` -> `man_b_w_me`
- `MAN B&W ME-B` -> `man_b_w_me_b`
- `MAN B&W ME-C` -> `man_b_w_me_c`
- `MAN ME` -> `man_b_w_me`
- `ME engine` -> `man_b_w_me`
- `ME-GI` -> `man_b_w_me_gi`
- `ME GI` -> `man_b_w_me_gi`
- OCR near-miss `ME-Gl` may normalize to `man_b_w_me_gi` after case-fold /
  confusable cleanup if the implementation opts into that heuristic
- `ME-C-GI` -> `man_b_w_me_c_gi`
- `ME-GA` -> `man_b_w_me_ga`
- `ME-LGI` -> `man_b_w_me_lgi`
- `ME-LGIM` -> `man_b_w_me_lgim`
- `ME-LGIP` -> `man_b_w_me_lgip`
- `ME-LGIA` -> `man_b_w_me_lgia`
- `ME-GIE` -> `man_b_w_me_gie`
- `MC` / `MC-C` -> `man_b_w_mc`
- bore/stroke model strings such as `S60ME-C`, `K98MC`, and `RT-flex58T-D`
  resolve by longest recognized subtype/model token first
- `WinGD` -> `wingd`
- `WinGD X-DF` -> `wingd_x_df`
- `X-DF-M` -> `wingd_x_df_m_e`
- `X-DF-M/E` -> `wingd_x_df_m_e`
- `X-DF-A` -> `wingd_x_df_a`
- `X-DF-HP` -> `wingd_x_df_hp`
- `Wartsila` / `Wärtsilä` -> `wartsila`
- `Sulzer` -> `sulzer`
- `Sulzer RT-flex` -> `wartsila_rt_flex` with `raw_mention` preserved
- `Wartsila Sulzer` -> compatibility handling rooted at the matched subtype or
  family evidence
- `Sulzer RTA` -> `wartsila_rta` with `raw_mention` preserved
- `RT-flex` -> `wartsila_rt_flex`
- `RTA` -> `wartsila_rta`
- `Everllence` -> `man`
- `Everllence B&W` -> `man_b_w`
- `Mitsubishi` -> `mitsubishi`
- `Mitsubishi UEC` / `UEC` -> `mitsubishi_uec`
- `UEC-LSII` -> `mitsubishi_uec_lsii`
- `UEC-LSE` -> `mitsubishi_uec_lse`
- `UEC-LSH` -> `mitsubishi_uec_lsh`
- `UEC-LSJ` -> `mitsubishi_uec_lsj`
- `J-ENG` / `Japan Engine Corporation` -> `mitsubishi`
- `UEC Eco` / `Eco-Engine` -> `mitsubishi_uec_lse`

Do not up-convert generic mentions into specific subtype evidence during
extraction.

## Prompt-Side Normalization

Normalize recruiter intent to the most specific requested node.

Examples:

- `has me engine experience` -> `man_b_w_me`
- `has man b&w experience` -> `man_b_w`
- `has man engine experience` -> `man`
- `has everllence b&w experience` -> `man_b_w`
- `has everllence engine experience` -> `man`
- `has x-df experience` -> `wingd_x_df`
- `has x-df-hp experience` -> `wingd_x_df_hp`
- `has wartsila experience` -> `wartsila`
- `has sulzer experience` -> compatibility request bucket rooted at `sulzer`
- `has mitsubishi uec experience` -> `mitsubishi_uec`
- `has dual fuel engine experience` -> `dual_fuel`
- `has electronic engine experience` -> `electronically_controlled_engine`
- `has electronically controlled engine experience` ->
  `electronically_controlled_engine`
- `has mechanical engine experience` -> `mechanical_engine`

Disambiguation note: in v1, the phrases `electronic engine` and
`electronically controlled engine` are interpreted as conventional camless /
electronic-control main-engine intent, **not** diesel-electric propulsion.
`diesel-electric` remains semantic/future.

## Deterministic Evaluator Semantics

### Exact or descendant match

If candidate evidence is the requested node or a descendant:

- Decision: `PASS`
- Bucket: `Verified Matches`
- Confidence: existing deterministic confidence logic
- Reason code: `ENGINE_EXPERIENCE_MATCH`

Examples:

- Requested `man_b_w_me`, candidate `man_b_w_me`
- Requested `man_b_w_me`, candidate `man_b_w_me_gi`
- Requested `man_b_w`, candidate `man_b_w_mc`
- Requested `man`, candidate `man_b_w_me`
- Requested `electronically_controlled_engine`, candidate `mitsubishi_uec_lse`

If a candidate has multiple engine evidences that map to different strengths
for the same request, the evaluator returns the **single strongest** decision:

1. exact match
2. descendant match
3. family fallback
4. manufacturer fallback
5. mismatch

Ties are broken by the most specific matching evidence node.

The evaluator may retain weaker secondary evidence in `also_considered` for
debugging and UI explainability, but only the strongest outcome controls the
candidate's bucket/decision.

### Family-level fallback

If the recruiter requests a specific subtype and the resume only provides a
parent family:

- Decision: `UNKNOWN`
- Bucket: `Needs Review`
- Confidence: `70%`
- Reason code: `ENGINE_EXPERIENCE_FAMILY_FALLBACK`

Examples:

- Requested `man_b_w_me`, candidate `man_b_w`
- Requested `man_b_w_me_gi`, candidate `man_b_w_me`
- Requested `wingd_x_df`, candidate `wingd_x_engines`
- Requested `mitsubishi_uec`, candidate `mitsubishi`
- Requested `wartsila_rt_flex`, candidate `wartsila`

Reason text pattern:

`Resume mentions {evidence display label}, but does not specify the requested
subtype '{requested display label}'. Included as a family-level engine match
for recruiter review.`

### Manufacturer-level fallback

If the recruiter requests a family or subtype and the resume only provides the
broader manufacturer:

- Decision: `UNKNOWN`
- Bucket: `Needs Review`
- Confidence: `60%`
- Reason code: `ENGINE_EXPERIENCE_MANUFACTURER_FALLBACK`

Examples:

- Requested `man_b_w_me`, candidate `man`
- Requested `man_b_w`, candidate `man`
- Requested `wingd_x_df`, candidate `wingd`
- Requested `wartsila_rt_flex`, candidate `wartsila` when treated as
  manufacturer-only generic without lineage detail

Reason text pattern:

`Resume mentions {evidence display label}, but does not specify the requested
engine family/subtype '{requested display label}'. Included as a
manufacturer-level engine match for recruiter review.`

### No relationship

If candidate evidence belongs to a different branch:

- Decision: `FAIL`
- Bucket: filtered out from deterministic pass path
- Reason code: `ENGINE_EXPERIENCE_MISMATCH`

Examples:

- Requested `man_b_w_me`, candidate `mitsubishi_uec`
- Requested `wingd_x_df`, candidate `wartsila_rt_flex`

## Broad Deterministic Engine Buckets

These buckets remain deterministic because they can be defined as explicit
descendant maps rather than fuzzy semantic guesses.

Bucket membership is defined as:

- an explicit set of canonical roots
- plus the full descendant closure of those roots

The examples below name the roots and important descendants; the evaluator
should compute closure rather than treat the bullet lists as unrelated flat
members.

### `dual_fuel`

Children:

- `man_b_w_me_gi`
- `man_b_w_me_c_gi`
- `man_b_w_me_ga`
- `man_b_w_me_lgi`
- `man_b_w_me_lgim`
- `man_b_w_me_lgip`
- `man_b_w_me_lgia`
- `man_b_w_me_gie`
- `wingd_x_df`
- `wingd_x_df_m_e`
- `wingd_x_df_a`
- `wingd_x_df_hp`
- `wartsila_dual_fuel`

### `electronically_controlled_engine`

Children:

- `man_b_w_me`
- `man_b_w_me_b`
- `man_b_w_me_c`
- all `man_b_w_me_*`
- `wingd_x_engines`
- all `wingd_x_df*`
- `wartsila_rt_flex`
- `mitsubishi_uec_lse`
- `mitsubishi_uec_lsh`
- `mitsubishi_uec_lsj`

### `mechanical_engine`

Children:

- `man_b_w_mc`
- `wartsila_rta`
- `mitsubishi_uec_lsii`

v1 limitation: `mechanical_engine` is intentionally limited to explicit
low-speed two-stroke mechanical families and `mitsubishi_uec_lsii`. Generic
manufacturer evidence such as `mak`, `yanmar`, `bergen`, `caterpillar`, or
`pielstick` does not satisfy `mechanical_engine` unless a modeled subtype is
added later.

### Deterministic evaluation rule for broad buckets

If the recruiter asks for one of the above broad buckets:

- explicit descendant evidence -> `PASS`
- generic manufacturer evidence only -> `UNKNOWN` only if the manufacturer is
  strongly tied to the bucket lineage and the product decision explicitly
  allows that fallback
- otherwise -> `FAIL`

v1 recommendation:

- allow family fallback into `dual_fuel` and
  `electronically_controlled_engine` at the default family-fallback confidence
  of `70%`, landing in `Needs Review`
- do **not** allow manufacturer-only fallback into `mechanical_engine`

## Semantic Technical-Experience Layer

The following concepts should be handled outside the deterministic engine-family
evaluator.

This section is an inventory and scoping boundary only. v1 does not define a
semantic evaluator, semantic decision model, semantic confidence model, or
semantic result bucket. Those belong in a follow-up semantic technical-search
spec.

### Propulsion / power concepts

- `diesel-electric`
- `de propulsion`
- `electric propulsion`
- `hybrid propulsion`
- `shaft generator`
- `pto`
- `pti`
- `battery hybrid`
- `peak shaving`
- `azipod`
- `mermaid pod`
- `bluedrive`

### Environmental / emissions concepts

- `tier iii`
- `scr`
- `egr`
- `scrubber`
- `egcs`
- `water treatment unit`

### Layout / architecture concepts

- `crosshead`
- `slow speed diesel`
- `direct drive`
- `stuffing box`
- `trunk piston`
- `medium speed`
- `high speed`
- `reduction gearbox`
- `inline`
- `v-line`

### Operational / alternative-fuel ecosystem concepts

- `igf code`
- `lng fuel`
- `methanol propulsion`
- `bunkering`
- `high voltage`
- `hv certification`

### Deferred manufacturer / platform additions

The following manufacturers are acknowledged as common in marine engineer
resumes but remain manufacturer-only stubs in v1:

- `himsen`
- `daihatsu`
- `niigata`
- `doosan`
- `mtu`
- `detroit_diesel`

The following family-level gaps are also explicitly deferred:

- Wartsila dual-fuel subtypes such as `W20DF`, `W34DF`, `W50DF`
- deterministic two-stroke / four-stroke recruiter buckets

## Query Routing Rules

Because semantic execution is out of scope for v1, only the
**deterministic-only** route below ships in this phase. The semantic-only and
hybrid sections are design notes for the follow-up semantic spec.

### Deterministic-only

Queries made entirely of exact engine families, broad deterministic buckets, or
explicit manufacturer/family constraints should stay in the deterministic layer.

Examples:

- `has me-gi experience`
- `has man b&w me engine experience`
- `has dual fuel engine experience`
- `has electronic engine experience`

### Semantic-only

Queries made entirely of propulsion, emissions, or technical-system concepts
should go to semantic search / LLM normalization.

Examples:

- `has diesel-electric propulsion experience`
- `has tier iii and scrubber experience`
- `has peak shaving and pto/pti experience`

### Hybrid split

If a query mixes exact engine constraints with broader technical concepts, split
it:

- deterministic clause for engine-family evidence
- semantic clause for technical-system evidence

Example:

`("ME-GI" OR "X-DF" OR "Dual Fuel") AND ("High Voltage" OR "HV")`

Recommended future routing:

- deterministic: `ME-GI`, `X-DF`, `dual_fuel`
- semantic: `high voltage`, `hv`

Cross-layer recombination semantics (`AND` / `OR` / `NOT` spanning
deterministic and semantic clauses) are explicitly deferred to the follow-up
semantic spec and are not implemented in v1.

## Data Model Additions

Each extracted engine detail should support:

- `engine_type` — canonical engine node id for the strongest extracted engine
  evidence on that detail
- `manufacturer_id`
- `family_id`
- `parent_family_id` — singular immediate parent node id, or `null`
- `lineage` — ordered ancestor path from broadest to narrowest, excluding the
  current node; example: `["man", "man_b_w", "man_b_w_me"]`
- `match_source` — one of `alias`, `model_token`, `exact`, `descendant`,
  `family_fallback`, `manufacturer_fallback`
- `raw_mention`

For legacy-brand compatibility that is not true structural parentage (for
example `sulzer` vs `wartsila_rt_flex`), use a separate compatibility map
rather than overloading `parent_family_id`.

The evaluator should use explicit maps such as:

- `ENGINE_PARENT_MAP`
- `ENGINE_CHILDREN_MAP`
- `ENGINE_MANUFACTURER_MAP`
- `ENGINE_BUCKET_MEMBERSHIP`
- `ENGINE_COMPATIBILITY_EXPANSION_MAP`
- `ENGINE_DISPLAY_LABEL_MAP`

### Result payload schema

Each `(candidate, requested_node)` evaluation should produce a result object
with this shape:

```json
{
  "candidate_id": "candidate_scope_or_resume_id",
  "requested_node": "man_b_w_me",
  "decision": "PASS",
  "bucket": "verified_matches",
  "confidence": 0.92,
  "reason_code": "ENGINE_EXPERIENCE_MATCH",
  "reason_text": "Candidate has MAN B&W ME experience.",
  "match_source": "exact",
  "matched_evidence": {
    "engine_type": "man_b_w_me",
    "manufacturer_id": "man",
    "family_id": "man_b_w",
    "lineage": ["man", "man_b_w"],
    "raw_mention": "MAN B&W ME-C",
    "display_label": "MAN B&W ME",
    "map_version": "engine-map-2026-06-19"
  },
  "also_considered": [
    {
      "engine_type": "man_b_w",
      "raw_mention": "B&W",
      "would_have_decision": "ENGINE_EXPERIENCE_FAMILY_FALLBACK"
    }
  ],
  "compatibility_expansion_used": null,
  "map_version": "engine-map-2026-06-19"
}
```

Required semantics:

- `also_considered` captures weaker evidence that lost the strongest-outcome
  tie-break.
- `compatibility_expansion_used` records the compatibility map key, if any.
- `map_version` is required on both extracted evidence and evaluation results.
- if no engine evidence can be extracted at all, emit a structured
  `no_evidence_extracted` result path rather than a silent fail.

### Display labels

UI reason text must use a canonical-id -> display-label mapping provided by
`ENGINE_DISPLAY_LABEL_MAP`. Do not render canonical ids directly. If no
display-label mapping exists, fall back to a humanized raw mention, not the
canonical id.

### Replayability

Given fixed inputs (`resume_text`, `prompt`, `map_version`), extraction and
evaluation must be deterministic and reproducible from logs.

## UI Behavior

### Verified Matches

Only exact/descendant deterministic matches appear as standard verified engine
matches.

### Needs Review

Family fallback and manufacturer fallback appear in `Needs Review` with a lower
confidence badge and structured wording. This is the same UI bucket already
used for other deterministic `UNKNOWN` cases; `reason_code` distinguishes
engine fallback from missing-evidence cases such as DOB/date gaps.

If a candidate falls into the `no_evidence_extracted` path, the UI should say
that the resume could not be evaluated for engine evidence, rather than
implying the candidate lacks engine experience.

### Reason formatting

Add explicit reason codes for formatter support:

- `ENGINE_EXPERIENCE_MATCH`
- `ENGINE_EXPERIENCE_FAMILY_FALLBACK`
- `ENGINE_EXPERIENCE_MANUFACTURER_FALLBACK`
- `ENGINE_EXPERIENCE_MISMATCH`

## Test Matrix

### Deterministic exact / descendant

- requested `man_b_w_me`, candidate `man_b_w_me` -> `PASS`
- requested `man_b_w_me`, candidate `man_b_w_me_gi` -> `PASS`
- requested `man`, candidate `man_b_w_me` -> `PASS`
- requested `dual_fuel`, candidate `wingd_x_df` -> `PASS`
- requested `electronically_controlled_engine`, candidate `mitsubishi_uec_lse`
  -> `PASS`

### Family fallback

- requested `man_b_w_me`, candidate `man_b_w` -> `UNKNOWN`, `70%`
- requested `wingd_x_df`, candidate `wingd_x_engines` -> `UNKNOWN`, `70%`
- requested `mitsubishi_uec`, candidate `mitsubishi` -> `UNKNOWN`, `70%`

### Manufacturer fallback

- requested `man_b_w_me`, candidate `man` -> `UNKNOWN`, `60%`
- requested `man_b_w`, candidate `man` -> `UNKNOWN`, `60%`

### Mismatch

- requested `man_b_w_me`, candidate `mitsubishi_uec` -> `FAIL`
- requested `wingd_x_df`, candidate `wartsila_rt_flex` -> `FAIL`
- requested `mechanical_engine`, candidate `man_b_w_me` -> `FAIL`

### Data-accuracy regressions to pin

- raw `Everllence B&W` extraction -> `man_b_w`
- `X-DF-P` and `X-DF-E` should not normalize to canonical WinGD subtype nodes
- `X-DF-HP` should normalize to `wingd_x_df_hp`
- `UEC-LSE` should not satisfy `mechanical_engine`
- `UEC-LSE` should satisfy `electronically_controlled_engine`
- `ME-GA` request should not be satisfied by `ME-GI` evidence

### Semantic routing

- `diesel-electric` should not emit deterministic engine family constraints
- `tier iii scrubber experience` should not emit deterministic engine family
  constraints
- mixed prompt routing is deferred to the follow-up semantic spec

### Additional required tests

- raw `B&W` extraction -> `man_b_w`
- `Sulzer` prompt normalization and `Sulzer RT-flex` resume extraction
- `mechanical_engine` FAIL against an electronic-only candidate
- `dual_fuel` family fallback confidence and bucket behavior
- strongest-outcome tie-break when a candidate has both subtype and
  manufacturer evidence
- `man` vs `wartsila` cross-branch fail
- negative noise such as `MAN power tools` should not extract
- no-evidence-extracted path on OCR/garbled resume
- `MAN BW`, `B+W`, `B＆W`, `ME GI`, `RT flex`, and fullwidth Latin forms should
  normalize correctly
- compound precedence:
  - `6S60ME-C8.2-GI` -> deepest recognized canonical node wins
  - `5S50ME-B9.5` -> `man_b_w_me_b`
  - `10X92DF-2.0` -> `wingd_x_df`

### Worked precedence examples

- `6S60ME-C8.2-GI` -> `man_b_w_me_c_gi`
- `5S50ME-B9.5` -> `man_b_w_me_b`
- `7G70ME-C9.6-LGIM` -> `man_b_w_me_lgim`
- `10X92DF-2.0` -> `wingd_x_df`

Rule: if multiple recognized tokens appear in the same model string, the
deepest canonical node in the hierarchy wins.

## Telemetry Expectations

Track at minimum:

- `% candidates with no engine evidence extracted`
- `% Needs Review of all engine-related candidate results`
- `% manufacturer-fallback within Needs Review`
- per-canonical-id evidence volume distribution
- `% compatibility-expansion-matched`

These metrics are the first-line drift alarms for extraction breakage,
under-covered alias maps, and over-broad compatibility rules.

## Recommended Rollout

1. Introduce explicit engine hierarchy maps.
2. Add `man` as a broader manufacturer-level node.
3. Separate `man` from `man_b_w` in prompt and resume normalization.
4. Implement family and manufacturer fallback outcomes.
5. Route fallback outcomes into `Needs Review`.
6. Add broad deterministic buckets:
   - `dual_fuel`
   - `electronically_controlled_engine`
   - `mechanical_engine`
7. Write a separate semantic technical-experience spec covering:
   - semantic evaluator behavior
   - bucketing / confidence
   - cross-layer boolean recombination
   - propulsion, emissions, and architecture concepts

## Non-Goals for v1

- Full deterministic modeling of every marine engine subtype in the market.
- Cross-manufacturer ownership inheritance such as `mak -> caterpillar` or
  `pielstick -> man`.
- Subtype-level deterministic coverage for `himsen`, `daihatsu`, `niigata`,
  `doosan`, `mtu`, or `detroit_diesel`.
- Context-aware extraction (course/training/vessel-spec separation).
- Full clause-aware / grammar-aware negation handling beyond the current
  sentence-window heuristic.
- Main-engine vs auxiliary-engine role separation.
- Semantic extraction of all machinery-room concepts from arbitrary prose.
- Treating broad technical-system terms as hard deterministic engine evidence.
- Cross-layer hybrid execution semantics in this v1.

v1 should establish the deterministic hierarchy, the fallback semantics, and
the boundary for a later semantic layer cleanly.
# Follow-up note

- The current bootstrap corpus and shadow prompt coverage for `engine_experience`
  are preparation evidence only. On the next real shadow-LLM normalization /
  revalidation pass for the broader family set, include `engine_experience`
  explicitly so manufacturer-only vs subtype-specific prompts and fallback
  outcomes are evaluated alongside the other active families.
