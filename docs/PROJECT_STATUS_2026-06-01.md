# NjordHR — Project Status Snapshot (2026-06-01)

This document captures the state of work at a clean stopping point so you can
pick up later without re-deriving context. Two parallel work streams are
described: the shadow-normalizer / promotion track (most of this document) and
the upcoming Windows packaging cleanup (briefly captured at the end so it
isn't lost).

---

## 1. Executive summary — where we are

The shadow normalizer eval gate is **fully cleared** for **five families** that
are now ready to promote from shadow → active, per
`docs/prompt-parsing-registry-refactor-blueprint-v0.1.md` §11.5.

| Family                    | Rescue rate | Controls | Solved-set | Confidence gate | Verdict          |
|---------------------------|-------------|----------|-----------:|------------------|------------------|
| `age_range`               | 26/26       | clean    | 0 / 1      | 47 / 47 high     | **promote candidate** |
| `us_visa`                 | 22/22       | clean    | 0 / 2      | 43 / 43 high     | **promote candidate** |
| `stcw_basic`              | 20/20       | clean    | 0 / 4      | 40 / 40 high     | **promote candidate** |
| `certificate_requirement` | 2/2         | clean    | 0 / 0      | 4 / 4 high       | **promote candidate** |
| `rank_match`              | 25/25       | clean    | 0 / 20     | 128 / 128 high   | **promote candidate** |

Aggregate gates also clean:
- Controls: 20 / 20 clean (zero hallucination on figurative / adjacent prompts).
- Solved-set: **0 / 32** regressions globally.

The work that remains is operational, not architectural:
- Wire LLM rescues into the live decision path behind a feature flag (the
  actual shadow → active promotion code change).
- Extend more families in future cycles (10 zero-rescue families remain).
- Separately, address Windows-version issues (email intake, OTP).

---

## 2. Rollback points (git tags)

Two tags mark stable snapshots:

- `eval-gate-green-v1` — 4 families promote-ready (age_range, us_visa,
  stcw_basic, certificate_requirement).
- `eval-gate-green-v2` — 5 families promote-ready (adds rank_match).

If you ever need to back out, either tag is a defensible state. Always
re-tag before adding promotion-related code changes (suggested next tag:
`eval-gate-green-v3` once the first family is actually promoted to active).

---

## 3. Architecture in place (what was built)

Cycle 1 produced a layered design per blueprint §9.3 ("deterministic wins
first; the LLM only fills gaps"). The same layers now exist for all five
promote-ready families.

### Per-family layers

For each promoted family, the stack from top to bottom:

1. **Tail-set corpus rows** in
   `AI_Search_Results/seajobs_tail_set_v0.1.json` — labeled `miss` /
   `partial` / `wrong_family` prompts the legacy parser failed to handle,
   plus family-adjacent control rows.
2. **Bootstrap solved-set rows** in
   `docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json` — known-good
   prompts that legacy already handles; act as the regression floor.
3. **Family-specific rules** in the shadow normalizer prompt
   (`query_understanding/shadow_llm_provider.py:build_shadow_llm_prompt`).
   Compact rule blocks tell the LLM what each family covers.
4. **Anchor veto helpers** that block LLM hallucinations on figurative or
   substring-overlap prompts:
   - `_age_range_is_anchored` — figurative deny (middle-aged, etc.) + numeric
     bound extractable via `_age_bounds_from_text`.
   - `_us_visa_is_anchored` — polarity inversion deny (visa-free, etc.) +
     supported-country cue (`_SUPPORTED_VISA_CONTEXT_CUES`).
   - `_stcw_basic_is_anchored` — STCW / BST / basic-cert / component cue
     (`_STCW_BASIC_CONTEXT_CUES`).
5. **Deterministic repair paths** in `_translate_model_payload` and
   `_family_to_canonical_items` that fire when the LLM emits an empty plan but
   the prompt is clearly anchored to a family:
   - Age: re-uses `_age_bounds_from_text` post-processor.
   - us_visa: `_extract_shadow_us_visa_constraint` plus `_visa_accepted_types_for_group` backfill.
   - stcw_basic: calls `analyzer._extract_stcw_basic_constraint`.
6. **Legacy parser** in `ai_analyzer.py` (`_extract_*_constraint` functions)
   and `RANK_ALIAS_TABLE`. This is the deterministic floor that runs first;
   most rescues never need the LLM at all.
7. **Schema validation** in `query_understanding/schema.py`. Strict enum
   validation for `visa_group` (`{usa, australia, schengen}`), structural
   constraints on accepted_types, plausibility for age bounds (14–80).
8. **Comparator** in `query_understanding/normalizer_compare.py`. Now
   propagates `confidence` end-to-end through `CanonicalComparisonRecord`.
9. **Scorer** in `scripts/tail_set_score.py`. Enforces three gates: rescue
   rate ≥ 0.8, zero controls violations, zero solved-set regressions
   (global + per-family). Accepts `expected_delta` and
   `unsupported_family_delta` as non-regressions per blueprint §11.2.

### Family-by-family fix mechanism

`rank_match` is **ontological** — closed-set canonical values. Closed in 3
patches: alias-table extension + separator normalization +
tail-set rows. No LLM rules, no anchor veto, no deterministic repair path
were needed because the legacy parser is already structurally correct.

`age_range`, `us_visa`, `stcw_basic`, `certificate_requirement` are **semantic
or hybrid**. They needed the full stack — LLM rules, anchor veto,
deterministic repair, plausibility checks, schema extensions.

The taxonomy predicts cycle complexity:
- **Ontological families** (closed-set vocabulary): typically 3–5 patches per cycle.
- **Semantic families** (open-ended phrasings): typically 10–15 patches per cycle.

---

## 4. Eval harness — how to run

### Full combined eval (use before any promotion vote)

```bash
EVAL_DATE=$(date +%F)

NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 \
python scripts/query_understanding_shadow_audit.py \
  --corpus AI_Search_Results/seajobs_tail_set_v0.1.json \
  --extra-corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \
  --combined-corpus-output AI_Search_Results/bootstrap_plus_tail_corpus_${EVAL_DATE}.json \
  --output AI_Search_Results/bootstrap_plus_tail_eval_${EVAL_DATE}.json

python scripts/tail_set_score.py \
  --eval AI_Search_Results/bootstrap_plus_tail_eval_${EVAL_DATE}.json \
  --solved-set-report AI_Search_Results/bootstrap_prompt_corpus_eval_current.json \
  --output AI_Search_Results/bootstrap_plus_tail_score_${EVAL_DATE}.json
```

Important: capture the date in `EVAL_DATE` once at the top of the script —
two `$(date +%F)` substitutions can resolve to different dates if you cross
midnight between the harness and scorer commands.

### Per-family eval (for fast iteration during family extension)

```bash
NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 \
python scripts/query_understanding_shadow_audit.py \
  --corpus AI_Search_Results/seajobs_tail_set_v0.1.json \
  --extra-corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \
  --family-filter <family_id> \
  --output /tmp/<family_id>_eval.json

python scripts/tail_set_score.py \
  --eval /tmp/<family_id>_eval.json \
  --output /tmp/<family_id>_score.json
```

Use this only during development. Per blueprint §11.7, **never promote based
on a per-family eval alone** — cross-family regressions are invisible without
the full corpus.

### Per-family confidence gate check

```bash
python3 << 'PY'
import json
from collections import defaultdict
d = json.load(open('AI_Search_Results/bootstrap_plus_tail_eval_<DATE>.json'))
by_family = defaultdict(list)
for row in d.get('rows') or []:
    for c in ((row.get('llm_plan') or {}).get('applied_constraints') or []):
        by_family[c.get('id')].append(c.get('confidence'))

for fam in ('age_range', 'us_visa', 'stcw_basic', 'certificate_requirement', 'rank_match'):
    cases = by_family.get(fam, [])
    auto = sum(1 for c in cases if c == 'high' or (isinstance(c, (int, float)) and c >= 0.90))
    lr   = sum(1 for c in cases if c == 'medium' or (isinstance(c, (int, float)) and 0.75 <= c < 0.90))
    no   = sum(1 for c in cases if c == 'low' or (isinstance(c, (int, float)) and c < 0.75))
    total = len(cases)
    print(f"  {fam:<28} auto={auto:>4}  low-risk={lr:>3}  don't-apply={no:>3}  total={total:>4}")
PY
```

---

## 5. Code map — where things live

| Concern | File | Notable functions |
|---------|------|-------------------|
| Legacy parser (deterministic floor) | `ai_analyzer.py` | `_extract_job_constraints`, `_extract_age_constraint`, `_extract_rank_constraint`, `_extract_us_visa_constraint`, `_extract_stcw_basic_constraint`, `RANK_ALIAS_TABLE` |
| Shared parsing helpers | `ai_analyzer.py` | `_age_range_patterns`, `_prompt_parsing_registry` |
| Shadow LLM provider | `query_understanding/shadow_llm_provider.py` | `build_shadow_llm_prompt`, `_age_bounds_from_text`, `_is_plausible_age`, `_age_range_is_anchored`, `_us_visa_is_anchored`, `_stcw_basic_is_anchored`, `_extract_shadow_us_visa_constraint`, `_visa_accepted_types_for_group`, `_translate_model_payload` |
| Catalog / schema | `query_understanding/schema.py` | `_validate_payload_family`, `SUPPORTED_VISA_GROUPS` |
| Catalog (family inventory) | `query_understanding/hard_filter_catalog.py` | `ACTIVE_FAMILY_IDS`, `UNAPPLIED_FAMILY_IDS`, `SUPPORTED_FAMILY_IDS` |
| Legacy → query_plan adapter | `query_understanding/legacy_parser_adapter.py` | `LegacyParserAdapter.adapt` |
| Comparator | `query_understanding/normalizer_compare.py` | `CanonicalComparisonRecord`, `compare_query_plans` |
| Harness | `scripts/query_understanding_shadow_audit.py` | `_build_prompts_from_corpora`, `_merge_corpora`, `--corpus`, `--extra-corpus`, `--family-filter`, `--combined-corpus-output` |
| Scorer | `scripts/tail_set_score.py` | mandatory solved-set gate at top |
| Tail set | `AI_Search_Results/seajobs_tail_set_v0.1.json` | 146 prompts; per-family rows + `unsupported_or_diagnostic` controls |
| Bootstrap corpus | `docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json` | known-good prompts for solved-set regression scoring |
| Blueprint (design doc) | `docs/prompt-parsing-registry-refactor-blueprint-v0.1.md` | §9.3 promotion policy, §11.5 sequence, §11.7 eval workflow, §11.8 promotion checklist |

---

## 6. Open items — what is NOT done

### 6.1 Shadow → active wiring (the actual promotion code change)

`ai_analyzer.py` still has **zero** imports from `query_understanding/`. The
live decision path is purely legacy parser. Promoting a family means letting
the LLM-rescued constraint flow into the live `_extract_job_constraints`
output for that family only, when (a) legacy missed it, (b) the LLM's
confidence is high, (c) a per-family feature flag is on.

Approximate code shape (not yet written):

```python
# Feature-flag-gated promotion in ai_analyzer.py, in _extract_job_constraints
PROMOTED_LLM_FAMILIES = {"certificate_requirement"}   # add one family at a time
if os.getenv("NJORDHR_LLM_PROMOTION_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
    for family in PROMOTED_LLM_FAMILIES:
        if family in constraints.get("applied_constraints", []):
            continue   # deterministic wins; do not override
        llm_plan = self._shadow_llm_provider_for_family(user_prompt, rank, family)
        # Pull the family's constraint out of llm_plan.applied_constraints, gate
        # on confidence == 'high', merge into constraints["applied_constraints"]
        # and the appropriate hard_constraints key.
```

Per blueprint §11.5 promotion sequence: `certificate_requirement` first
(smallest blast radius), then `rank_match` (purely deterministic, lowest LLM
dependency), then `age_range`, then `stcw_basic`, then `us_visa` last (most
recruiter-visible).

### 6.2 Remaining families (cycle 3+)

10 families still at 0% rescue. Recommended next-cycle order by complexity:

| Cycle | Family | Type | Expected effort |
|-------|--------|------|------------------|
| 3 | `passport_validity` | ontological — limited phrasings, schema already supports months | 3–5 patches |
| 4 | `coc_grade_match` | ontological — rank-keyed lookup like rank_match | 3–5 patches |
| 5 | `experience_ship_type` | ontological — catalog-driven ship type aliases | 4–6 patches |
| 6 | `recency` | semantic — time-window phrasings, similar to age | 10–15 patches |
| 7 | `sea_service` / `min_sea_service` | hybrid — duration extraction + schema currently unapplied | 10+ patches plus catalog work |
| 8 | `availability` | semantic — relative-phrase resolution still needs convention | 10+ patches |
| 9 | `company_continuity` | semantic — counting-pattern extraction | 10+ patches |
| 10 | `stcw_endorsement` | hybrid — catalog rich, phrasings vary | 8–10 patches |
| 11 | `coc_document_gate` | semantic — existing schema gap on `coc_valid_required` | 5–8 patches |
| 12 | `rank_certificate_expectation` / `certificate_requirement` extensions | hybrid | varies |

### 6.3 Latent / cosmetic items

- The `--family-filter` flag's tests cover the filter helper but the
  end-to-end flag handling could use more coverage; add as needed.
- `_age_bounds_from_text` plausibility floor (14–80) is conservative; verify
  with maritime regulations before any cadet-program work that might use
  under-18 ages.
- The bootstrap corpus solved-set has rows that legacy classifies as
  `validation: invalid` due to `mandatory_marker_in_semantic_query`. These
  produce `expected_delta` outcomes — sanctioned, not regressions. Future
  cleanup could fix the legacy parser to produce valid plans for those
  prompts.
- Tail-set rows still flagged `NEEDS_HUMAN_REVIEW` after the gates cleared
  are convention calls (e.g., `under forty` strict vs inclusive, `late 20s`
  range definition). Document the chosen conventions in
  `seajobs_tail_set_v0.1.json`'s `scoring_notes` once the per-row decisions
  are made.

---

## 7. Pickup instructions (when you return to this work)

### To resume shadow-normalizer promotion

1. `git fetch && git checkout eval-gate-green-v2` (or rebase onto main if
   relevant changes happened).
2. Re-run the full combined eval. Confirm the scoreboard still matches §1.
   If it has drifted (Flash Lite jitter, model updates, etc.), iterate
   before promotion.
3. Run the confidence gate script (§4) — confirm all 5 families still
   AUTO-APPLY OK.
4. Implement the feature-flag wiring in `ai_analyzer.py` per §6.1, starting
   with `certificate_requirement` only.
5. Deploy with flag OFF. Confirm no behavior change.
6. Flip flag in a controlled environment. Watch shadow telemetry in Supabase
   (`comparison_outcome` counts per family) for at least one week.
7. Promote the next family. Repeat.

### To start cycle 3 on a new family

Use the cycle-2 playbook (ontological-first):
1. Read existing tail-set rows for the family in
   `AI_Search_Results/seajobs_tail_set_v0.1.json`.
2. Read the legacy `_extract_<family>_constraint` and any associated
   alias/catalog in `ai_analyzer.py`.
3. Empirically test ~25 candidate phrasings against the current parser to
   identify deterministic misses.
4. If the family is ontological, extend the alias table / catalog
   structurally and add ~15–20 tail-set rescue rows + 3 controls.
5. Run a per-family eval with `--family-filter <family_id>` to baseline LLM
   behavior.
6. If gaps remain, add: family rule block in the LLM prompt, anchor veto
   helper, deterministic repair path.
7. Re-run per-family eval until family clears.
8. **Run the full combined eval** for cross-family safety. This is
   non-negotiable per blueprint §11.7.
9. Tag the branch with `eval-gate-green-v<n>`.

---

## 8. Lessons learned (preserve these for future cycles)

1. **The LLM is unreliable on substring discrimination.** Every family
   eventually finds a figurative or adjacent prompt to hallucinate on
   (`visa-free US entry`, `any safety training`, `middle-aged officer`).
   Anchor vetoes are non-optional for semantic families.
2. **The LLM prompt has a length threshold.** Around 3,500 characters,
   Flash Lite started emptying out plans for prompts unrelated to recently
   added rules. Path B (compact prompt + worked examples only) plus
   deterministic floors per family is the working balance.
3. **Cross-family regressions are real and invisible to per-family eval.**
   The patch-15 STCW rules indirectly broke age_range. Always run the full
   combined eval before any promotion vote.
4. **Stable means stable across two consecutive runs.** Flash Lite
   non-determinism can swing single-row results, especially on small tail
   sets. The 2026-06-01 promotion eval needed three consecutive runs to
   confirm stability.
5. **Schema fields are load-bearing.** When `visa_group` and
   `accepted_types` weren't in the schema, the LLM payload couldn't carry
   country-aware information, even when the LLM recognized the country.
   Extend schema first, then LLM rules.
6. **Plausibility checks belong on the deterministic side.** The age
   plausibility gate (14–80) is in `_age_bounds_from_text`, not in the LLM
   prompt — the LLM is unreliable on edge cases, but a regex check is
   deterministic.
7. **Tail-set size matters.** A 3-row tail set can't statistically support
   a 0.8 threshold (single-row flips swing the rate by 33 percentage
   points). Aim for ≥15–20 rescue rows + ≥3 family-adjacent controls per
   family.
8. **Promote-candidate verdict is necessary but not sufficient.** Per
   blueprint §11.6, an eval-gate pass authorizes the *vote*; the actual
   promotion needs the confidence gate + value-correctness review + per-
   family feature flag.

---

## 9. Next work stream — Windows packaging cleanup

(Independent of the shadow normalizer work. Captured here so it isn't lost.)

### Known issues to investigate

- **Email intake**: Windows version had problems with the email intake flow.
  Specific symptoms not yet captured here — record them on resume.
- **OTP delivery**: clicking the "Get OTP" button after entering a phone
  number on Windows does not result in the OTP arriving at the phone. macOS
  flow is fine.

### Likely investigation areas (placeholders for the next session)

- Compare the Windows packaging output with macOS for the agent's HTTP
  client setup (cert paths, Outlook OAuth token caching).
- Check `scripts/packaging/windows/` and the Electron app's Windows-specific
  bootstrap.
- The `agent/config_store.py` platform-aware download root recently changed
  for the macOS App Support flow — verify the Windows `%APPDATA%\NjordHR`
  path is symmetric and writable.
- The OTP flow likely involves a backend call to a third-party SMS provider
  (e.g., Twilio). Check whether the provider receives the request from
  Windows (server-side log) — if it does, the issue is downstream (provider
  config, phone-number validation). If it doesn't, the issue is in the
  Windows agent's network egress or the OTP endpoint wiring.

### Pickup checklist for the Windows stream

1. Reproduce the email intake issue on a clean Windows box; capture exact
   error messages and the request path.
2. Reproduce the OTP issue; collect server-side logs at the time of the
   Get OTP click.
3. Add specific symptoms / stack traces to this section before doing any
   coding so future-you (or a delegated agent) has the context.

---

## 10. Closing notes

This work cycle ran from approximately 2026-03-25 (initial branch state) to
2026-06-01. The patch sequence is captured in commit history. The branch
state at the time of writing is **uncommitted** — the patches are present in
the working tree but not yet committed. Suggested commit message for the
final commit:

```
Shadow normalizer: 5 families promote-ready

Cycle 1 (age_range, us_visa, stcw_basic, certificate_requirement) +
cycle 2 (rank_match) cleared the blueprint §11 eval gate and confidence
gate. Deterministic floor + anchor veto + LLM rule block + schema field
support all in place per family. 0/32 solved-set regressions, 20/20
controls clean, 100% high-confidence rescues across 262 LLM applications.

Next: feature-flag wiring in ai_analyzer.py for the actual shadow→active
promotion (Path A from PROJECT_STATUS_2026-06-01.md §6.1).
```

If you tag the final state with `eval-gate-green-v2` (or v3 if there have
been more changes by the time you commit), the rollback path is preserved
for any future operator.

The architectural work is **done**. What remains is shipping and the next
family cycle. Welcome back.
