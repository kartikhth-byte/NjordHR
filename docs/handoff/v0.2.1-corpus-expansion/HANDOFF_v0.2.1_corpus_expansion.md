# NjordHR v0.2.1 Tail-Set Corpus Expansion — Implementation Handoff (rev 3)

**Date:** 2026-06-10
**Repo:** `/Users/kartikraghavan/Tools/NjordHR` (remote: `https://github.com/kartikhth-byte/NjordHR.git`)
**You need:** write access to the repo, push access to `origin`, ability to run the shadow eval (Gemini API key in `config.ini`).

## Revision history

**rev 3 (2026-06-10):** patches three additional issues caught in a second review pass:

- *P1* — `scripts/bootstrap_prompt_corpus_eval.py` accepts only ONE `--corpus`. The v0.2 solved-set report used the COMBINED 175-row bootstrap (April 115 + May 60). Running the eval against just the default April corpus produces incomplete solved-set coverage. Fixed by adding `combine_bootstrap_corpora.py` (bundled in this package) and an explicit combine step before the solved-set eval.
- *P2* — Pre-check snippet for `_visa_type_definitions()` returned mangled output and rev 2 hard-coded wrong canonical values for USA `accepted_types`. Live canonical list per `_visa_type_definitions()` (group='usa') is: `['C1/D (USA)', 'B1/B2 (USA)', 'C1 (USA)', 'D (USA)', 'US Visa (USA)']` — five entries, all with `(USA)` suffix. Class shorthands (`H-1B`, `L-1`, `F-1`, `O-1`) are cue-regex aliases, NOT canonical accepted_types. Fixed in `v0.2.1_additions.json`: all 4 us_visa regression rows now use the live canonical list.
- *P2* — Splicer iterates `additions.keys()` and would mis-treat any underscore-prefixed metadata key inside `additions` as a corpus bucket. Hardened: splicer now skips underscore-prefixed keys defensively. `_schema_note` etc. remain at the JSON top level (where they always were) — the hardening is belt-and-suspenders.

**rev 2 (2026-06-10):** patched five bugs found during a self-review of rev 1:
- *P1* — `--solved-set-report` got the eval JSON instead of the bootstrap solved report. Fixed.
- *P1* — Verdict snippet used `rescue_summary`/`solved_summary`; actual keys are `rescue_by_family`/`solved_set`. Fixed.
- *P2* — Required a stability-comparison artifact but no script in the repo produces it. Fixed by bundling `compare_eval_stability.py`.
- *P2* — Referenced `scripts/validate_v0.2_labels.py` which does not exist in the repo. Fixed by bundling `validate_v0.2_labels.py`.
- *(self-caught)* — Multi-family rows' `expected_constraint` used hard_constraints schema keys instead of family IDs. Fixed; convention documented in the JSON's `_schema_note`.

## Purpose

Expand the v0.2 tail-set corpus along three coverage dimensions. This is a **follow-up PR after PR #23 and PR #24 merge**. It is NOT a corpus rebuild.

1. **Regression rows for two recently-fixed bugs** (PR #23: US Visa accepted-types collapse, container ship-family canonicalization).
2. **Multi-family compound prompts** — currently almost-zero coverage of the interaction surface.
3. **Controls bucket expansion** — currently ~1.7 controls per family; expanding to ~3.5 per family.

## Context

- **PR #21** merged: corpus restoration, README evidence standard, gitignore exception, v0.2 tail-set foundation.
- **PR #23** merged or pending: normalizer bug fixes + v0.2 eval evidence + stability comparison + Settings UI link.
- **PR #24** merged or pending: Supabase RPC race hardening.

Canonical artifacts:

- `AI_Search_Results/seajobs_tail_set_v0.2.json` — the tail-set you are extending. Current row count: 140.
- `AI_Search_Results/README.md` — the four-gate evidence standard and `scoring_notes` schema.
- `docs/eval-evidence/` — archived eval evidence from the v0.2 stability run. Note the v0.2 stability artifact's solved-set was generated from the **combined** 175-row bootstrap (April 115 + May 60).
- `scripts/tail_set_score.py` — the scorer.
- `scripts/bootstrap_prompt_corpus_eval.py` — produces the solved-set report. Single-corpus input.
- `scripts/query_understanding_shadow_audit.py` — shadow LLM eval harness. Accepts multiple corpora and emits a `--combined-corpus-output` as a side effect.
- `query_understanding/shadow_llm_provider.py` — anchor-veto helpers, context-cue regexes.

## What's in this package (5 files)

| File | Purpose |
|---|---|
| `HANDOFF_v0.2.1_corpus_expansion.md` | This document. |
| `v0.2.1_additions.json` | 30 rows to splice into the existing tail-set. |
| `validate_v0.2_labels.py` | Standalone label validator (axis B). Handles `multi` and unvalidated buckets. |
| `combine_bootstrap_corpora.py` | Standalone bootstrap-corpus combiner. Merges multiple bootstrap corpora into the single-corpus shape `bootstrap_prompt_corpus_eval.py` expects. **NEW in rev 3.** |
| `compare_eval_stability.py` | Standalone stability comparator. Produces the canonical artifact shape. |

The four scripts are standalone — copy them anywhere accessible and invoke directly.

## Scope checklist

### 1. Bug regression rows (7 rows)

**US Visa accepted-types regression (4 rows in `us_visa` bucket).** PR #23's `shadow_llm_provider.py` fix preserved the legacy-compatible accepted-types list for generic `US Visa (USA)` instead of collapsing to a narrower placeholder.

**Canonical USA accepted_types list** (verified against live `_visa_type_definitions()` on 2026-06-10):

```python
["C1/D (USA)", "B1/B2 (USA)", "C1 (USA)", "D (USA)", "US Visa (USA)"]
```

All five entries have the `(USA)` suffix. Class shorthands (`H-1B`, `L-1`, `F-1`, `O-1`) are cue-regex aliases for anchoring, NOT canonical accepted_types.

**Container ship-family canonicalization regression (3 rows in new `recent_contract_vessel_experience` bucket).** PR #23's `legacy_parser_adapter.py` fix emits canonical `container` (not `container vessel`).

### 2. Multi-family compound rows (10 rows in new `multi` bucket)

Real production phrasings combining constraints. Critical schema convention:

> Multi-family rows' `expected_constraint` keys MUST be family IDs (`age_range`, `rank_match`, `certificate_requirement`, etc.), NOT hard_constraints schema keys (`age_years`, `rank`, `certifications`). The scorer's `_expected_families("multi")` reads keys literally.

The committed v0.2 row `valid US visa and passport` follows this convention. The provided `v0.2.1_additions.json` already follows it across all 10 rows.

### 3. Controls expansion (12 → 25)

13 new controls across family-coverage gaps (certificate_requirement, rank_match, stcw_endorsement), sharper polarity inversions (us_visa rejected/Canadian/Indian, passport_validity process-vs-validity), and figurative descriptors (age_range without numeric).

## Pre-implementation checklist

```bash
cd /Users/kartikraghavan/Tools/NjordHR

# 1. Confirm on main, clean tree, PRs landed.
git fetch origin
git checkout main
git pull
git log -5 --oneline
# Expect to see merged squash commits for PRs #21, #23, #24.

# 2. Validate existing corpus parses + row count.
python3 -m json.tool AI_Search_Results/seajobs_tail_set_v0.2.json > /dev/null && echo "v0.2 JSON OK"
python3 -c "
import json
d = json.load(open('AI_Search_Results/seajobs_tail_set_v0.2.json'))
for fam, rows in d['families'].items():
    print(f'  {fam}: {len(rows)}')
print(f'  TOTAL: {sum(len(r) for r in d[\"families\"].values())}')
"
# Expected: 140 rows.

# 3. Verify the actual canonical USA accepted_types list (correct extraction).
python3 -c "
import sys, types
sys.modules.setdefault('fitz', types.ModuleType('fitz'))
pil = types.ModuleType('PIL'); image = types.ModuleType('PIL.Image'); pil.Image = image
sys.modules.setdefault('PIL', pil); sys.modules.setdefault('PIL.Image', image)
pc = types.ModuleType('pinecone')
class _S:
    def __init__(self, *a, **k): pass
pc.Pinecone = _S; pc.ServerlessSpec = _S
sys.modules.setdefault('pinecone', pc)
sys.path.insert(0, '.')
from ai_analyzer import AIResumeAnalyzer
a = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
# _visa_type_definitions returns a LIST of dicts, not a dict.
usa_canonical = [d['canonical'] for d in a._visa_type_definitions() if d.get('group') == 'usa']
print('USA canonical accepted_types:', usa_canonical)
"
# Expected (as of 2026-06-10):
# ['C1/D (USA)', 'B1/B2 (USA)', 'C1 (USA)', 'D (USA)', 'US Visa (USA)']
# If this differs from what's in v0.2.1_additions.json, update the four us_visa regression
# rows' accepted_types arrays to match.

# 4. Verify recent_contract_vessel_experience schema required fields.
python3 -c "
import sys
sys.path.insert(0, '.')
import query_understanding.schema as s
import inspect
src = inspect.getsource(s)
start = src.find('recent_contract_vessel_experience')
print(src[start:start+800])
"
# Confirm whether minimum_months is required or nullable. If required, add 'minimum_months': null
# to each of the three regression rows in recent_contract_vessel_experience bucket.
```

## Splice procedure (hardened against metadata keys)

```bash
cd /Users/kartikraghavan/Tools/NjordHR
git checkout -b codex/v0.2.1-corpus-expansion

ADDITIONS_PATH=/path/to/v0.2.1_additions.json

python3 <<PYEOF
import json
with open("AI_Search_Results/seajobs_tail_set_v0.2.json") as f:
    current = json.load(f)
with open("$ADDITIONS_PATH") as f:
    additions = json.load(f)

added_per_bucket = {}
for bucket, rows in additions["additions"].items():
    # Defensive: skip metadata keys that might end up inside 'additions' by mistake.
    if bucket.startswith("_"):
        print(f"  SKIPPED metadata key: {bucket}")
        continue
    if not isinstance(rows, list):
        print(f"  SKIPPED non-list value at {bucket}: {type(rows).__name__}")
        continue
    if bucket not in current["families"]:
        current["families"][bucket] = []
        print(f"  CREATED new bucket: {bucket}")
    current["families"][bucket].extend(rows)
    added_per_bucket[bucket] = len(rows)

with open("AI_Search_Results/seajobs_tail_set_v0.2.json", "w") as f:
    json.dump(current, f, indent=2, ensure_ascii=False)
    f.write("\n")

print()
print("Added rows per bucket:")
for bucket, count in added_per_bucket.items():
    print(f"  {bucket}: +{count}")
print()
print("Family bucket totals after splice:")
for fam, rows in current["families"].items():
    print(f"  {fam}: {len(rows)}")
print(f"  TOTAL: {sum(len(r) for r in current['families'].values())}")
PYEOF

python3 -m json.tool AI_Search_Results/seajobs_tail_set_v0.2.json > /dev/null && echo "JSON OK"
```

Expected post-splice totals: 140 + 30 = 170 rows.

## Axis B — label validation

```bash
cd /Users/kartikraghavan/Tools/NjordHR
python3 /path/to/validate_v0.2_labels.py AI_Search_Results/seajobs_tail_set_v0.2.json
```

For `recent_contract_vessel_experience` rows (no extractor mapped), spot-check via full pipeline:

```bash
python3 -c "
import sys, types
sys.modules.setdefault('fitz', types.ModuleType('fitz'))
pil = types.ModuleType('PIL'); image = types.ModuleType('PIL.Image'); pil.Image = image
sys.modules.setdefault('PIL', pil); sys.modules.setdefault('PIL.Image', image)
pc = types.ModuleType('pinecone')
class _S:
    def __init__(self, *a, **k): pass
pc.Pinecone = _S; pc.ServerlessSpec = _S
sys.modules.setdefault('pinecone', pc)
sys.path.insert(0, '.')
from ai_analyzer import AIResumeAnalyzer
a = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
import json
for prompt in ['container ship work experience required', 'experience on container vessel', 'must have served on container ships']:
    result = a._extract_job_constraints(prompt, rank=None)
    print(prompt)
    print(' -> applied:', result.get('applied_constraints'))
    print(' -> hard:', json.dumps(result.get('hard_constraints'), default=str)[:200])
    print()
"
```

If `recent_contract_vessel_experience` is in `applied_constraints` and `ship_family` is `container` (post PR #23 fix), labels are right.

## Axis C — full shadow eval (the corrected four-step workflow)

```bash
cd /Users/kartikraghavan/Tools/NjordHR
EVAL_DATE=$(date +%F)

# Sanity prereqs.
test -f config.ini || { echo "ERROR: config.ini missing"; exit 1; }
grep -q "Gemini_API_Key" config.ini || { echo "ERROR: Gemini_API_Key not in config.ini"; exit 1; }

# Step 1: Combine the two bootstrap corpora into a single 175-row corpus.
# (bootstrap_prompt_corpus_eval.py accepts only one --corpus, but the v0.2 stability
# evidence used the combined 175-row bootstrap.)
python3 /path/to/combine_bootstrap_corpora.py \
  --corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \
  --corpus docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json \
  --output docs/eval-evidence/ai-search-bootstrap-solved-corpus-${EVAL_DATE}.json

# Step 2: Build the bootstrap solved-set report from the combined corpus.
# This is the family_summaries-shaped file the scorer's --solved-set-report flag expects.
python3 scripts/bootstrap_prompt_corpus_eval.py \
  --corpus docs/eval-evidence/ai-search-bootstrap-solved-corpus-${EVAL_DATE}.json \
  --output docs/eval-evidence/ai-search-bootstrap-solved-report-${EVAL_DATE}.json

# Step 3: Shadow audit (LLM run against tail-set + extra bootstrap corpora).
NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 \
python3 scripts/query_understanding_shadow_audit.py \
  --corpus AI_Search_Results/seajobs_tail_set_v0.2.json \
  --extra-corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \
  --extra-corpus docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json \
  --combined-corpus-output docs/eval-evidence/ai-search-v0.2.1-corpus-${EVAL_DATE}.json \
  --output docs/eval-evidence/ai-search-v0.2.1-eval-${EVAL_DATE}.json

# Step 4: Score. --solved-set-report points at the BOOTSTRAP REPORT, not the eval.
python3 scripts/tail_set_score.py \
  --tail-set AI_Search_Results/seajobs_tail_set_v0.2.json \
  --eval docs/eval-evidence/ai-search-v0.2.1-eval-${EVAL_DATE}.json \
  --solved-set-report docs/eval-evidence/ai-search-bootstrap-solved-report-${EVAL_DATE}.json \
  --output docs/eval-evidence/ai-search-v0.2.1-score-${EVAL_DATE}.json \
  --promote-threshold 0.8

# Step 5: Read the verdict using ACTUAL score keys.
python3 -c "
import json
report = json.load(open('docs/eval-evidence/ai-search-v0.2.1-score-${EVAL_DATE}.json'))
print('=== Rescue rates per family ===')
for fam, tally in (report.get('rescue_by_family') or {}).items():
    total = tally.get('total', 0)
    rescued = tally.get('rescued', 0)
    rate = tally.get('rescue_rate', 0)
    regressions = tally.get('solved_set_regressions', 0)
    solved_total = tally.get('solved_set_total', 0)
    flag = 'PROMOTE' if rate >= 0.8 and regressions == 0 else 'GATE FAIL'
    print(f'  {fam:32}  rescue {rescued:>3}/{total:<3} ({rate:.2%})  solved regressions {regressions}/{solved_total}  {flag}')
print()
controls = report.get('controls') or {}
solved_set = report.get('solved_set') or {}
print(f'Total control violations: {controls.get(\"violations\", 0)} / {controls.get(\"total\", 0)}')
print(f'Total solved-set regressions: {solved_set.get(\"regressions\", 0)} / {solved_set.get(\"total\", 0)}')
print(f'Solved-set gate active: {report.get(\"solved_set_gate_active\")}')
"
```

## Axis B follow-up — value-correctness review

For every rescued row, populate `scoring_notes` per the README schema. Dispositions: `accepted`, `rejected`, `retest`, `follow_up`. Walk rescue cases:

```bash
python3 -c "
import json
report = json.load(open('docs/eval-evidence/ai-search-v0.2.1-score-${EVAL_DATE}.json'))
for fam, tally in (report.get('rescue_by_family') or {}).items():
    cases = tally.get('cases') or []
    print(f'=== {fam}: {len(cases)} rescued cases needing disposition ===')
    for case in cases[:3]:
        print(f'  prompt: {case.get(\"prompt\")!r}')
        print(f'  llm_normalized: {case.get(\"llm_normalized\")}')
        print(f'  expected_constraint: {case.get(\"expected_constraint\")}')
        print()
"
```

Per-row `scoring_notes` schema:

```json
"scoring_notes": {
  "eval_date": "YYYY-MM-DD",
  "llm_family_rescued": true,
  "llm_confidence": "high",
  "value_review": "accepted",
  "reviewer": "manual",
  "review_note": "<one-line disposition>"
}
```

## Stability re-run

Run the full four-step workflow above a second time with a different date suffix, then compare:

```bash
cd /Users/kartikraghavan/Tools/NjordHR

EVAL_DATE_2=$(date +%F-rerun)

# Re-run combine + bootstrap-eval + shadow audit + score with the -rerun suffix.
python3 /path/to/combine_bootstrap_corpora.py \
  --corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \
  --corpus docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json \
  --output docs/eval-evidence/ai-search-bootstrap-solved-corpus-${EVAL_DATE_2}.json

python3 scripts/bootstrap_prompt_corpus_eval.py \
  --corpus docs/eval-evidence/ai-search-bootstrap-solved-corpus-${EVAL_DATE_2}.json \
  --output docs/eval-evidence/ai-search-bootstrap-solved-report-${EVAL_DATE_2}.json

NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 \
python3 scripts/query_understanding_shadow_audit.py \
  --corpus AI_Search_Results/seajobs_tail_set_v0.2.json \
  --extra-corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \
  --extra-corpus docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json \
  --combined-corpus-output docs/eval-evidence/ai-search-v0.2.1-corpus-${EVAL_DATE_2}.json \
  --output docs/eval-evidence/ai-search-v0.2.1-eval-${EVAL_DATE_2}.json

python3 scripts/tail_set_score.py \
  --tail-set AI_Search_Results/seajobs_tail_set_v0.2.json \
  --eval docs/eval-evidence/ai-search-v0.2.1-eval-${EVAL_DATE_2}.json \
  --solved-set-report docs/eval-evidence/ai-search-bootstrap-solved-report-${EVAL_DATE_2}.json \
  --output docs/eval-evidence/ai-search-v0.2.1-score-${EVAL_DATE_2}.json \
  --promote-threshold 0.8

# Compare runs.
python3 /path/to/compare_eval_stability.py \
  --run-1-eval  docs/eval-evidence/ai-search-v0.2.1-eval-${EVAL_DATE}.json \
  --run-1-score docs/eval-evidence/ai-search-v0.2.1-score-${EVAL_DATE}.json \
  --run-2-eval  docs/eval-evidence/ai-search-v0.2.1-eval-${EVAL_DATE_2}.json \
  --run-2-score docs/eval-evidence/ai-search-v0.2.1-score-${EVAL_DATE_2}.json \
  --output      docs/eval-evidence/ai-search-v0.2.1-stability-comparison-$(date +%F).json \
  --purpose     "Stability rerun comparison for v0.2.1 corpus expansion."
```

The comparator's `verdict` field must be `stable_match` and `score_projection_match` must be `true`. Investigate divergent rows otherwise.

## Definition of done

1. The corpus splices cleanly and JSON validates.
2. The label validator exits 0 against the spliced corpus.
3. Bootstrap solved-set report was generated from the **combined** 175-row corpus (not just the default April corpus).
4. Two consecutive shadow eval runs produce `verdict: stable_match` from `compare_eval_stability.py`.
5. Every rescued row has a `scoring_notes` disposition.
6. Total control violations = 0.
7. Total solved-set regressions = 0.
8. Family verdict added to `AI_Search_Results/README.md` per the PR #21 format.

## PR submission

```bash
git add AI_Search_Results/seajobs_tail_set_v0.2.json \
        AI_Search_Results/README.md \
        docs/eval-evidence/

git commit -m "Expand v0.2 tail set: bug regressions + multi-family + controls"

git push -u origin codex/v0.2.1-corpus-expansion

gh pr create \
  --title "v0.2.1 corpus expansion: regression rows + multi-family compounds + controls" \
  --body "<see template in HANDOFF §pr-body-template>"
```

## Sanity-check questions before starting

- [ ] Are PR #23 and PR #24 merged to main?
- [ ] Is `config.ini` configured with a working Gemini API key?
- [ ] Did you confirm the corpus row count is 140?
- [ ] Did you run the `_visa_type_definitions()` snippet and confirm the USA list matches what's in `v0.2.1_additions.json`?
- [ ] Did you confirm `recent_contract_vessel_experience` schema fields (especially whether `minimum_months` is required)?
- [ ] Is `gh` CLI authenticated?
- [ ] Did you run the bootstrap combiner before the bootstrap eval? (Step 1 + Step 2 must both run before scoring; rev 1 / rev 2 of this handoff incorrectly skipped Step 1.)

## What's intentionally NOT in this PR

- New single-family rescue rows beyond what's added here.
- Cycle 4 work for `coc_grade_match` or any other new family.
- Production rollout of `LLM_Promotion_Stage > 0`.
- Real telemetry sourcing.

## PR body template

```markdown
## Summary

Expands the v0.2 tail-set corpus along three coverage dimensions:

- 7 bug-regression rows for the two issues fixed in PR #23 (US Visa accepted-types collapse, container ship-family canonicalization).
- 10 multi-family compound rows. Real production phrasings mix constraints; the current corpus is single-family per row.
- 13 controls expansion. Each anchor-veto helper in `query_understanding/shadow_llm_provider.py` now has at least one explicit control row.

Does NOT blanket-expand single-family rescue rows beyond ~25 per family.

## Evidence

- Combined bootstrap solved corpus: `docs/eval-evidence/ai-search-bootstrap-solved-corpus-<date>.json` (175 rows from April + May bootstraps)
- Bootstrap solved-set report: `docs/eval-evidence/ai-search-bootstrap-solved-report-<date>.json`
- Eval (run 1): `docs/eval-evidence/ai-search-v0.2.1-eval-<date>.json`
- Score (run 1): `docs/eval-evidence/ai-search-v0.2.1-score-<date>.json`
- Eval / score (run 2 stability): `*-<date>-rerun.json`
- Stability comparison: `docs/eval-evidence/ai-search-v0.2.1-stability-comparison-<date>.json` (verdict: `stable_match`)
- Value review dispositions: persisted in `scoring_notes` per row.

## Family verdict deltas vs v0.2

| Family | v0.2 | v0.2.1 |
|---|---|---|
| age_range | 22/22 | <new>/<total> |
| certificate_requirement | 15/15 | <new>/<total> |
| passport_validity | 20/20 | <new>/<total> |
| rank_match | 21/21 | <new>/<total> |
| stcw_basic | 17/17 | <new>/<total> |
| stcw_endorsement | 6/6 | <new>/<total> |
| us_visa | 22/22 | <new>/<total> |
| recent_contract_vessel_experience | (new) | <new>/<total> |
| multi | (new) | <new>/<total> |

If any family drops below the v0.2 scoreboard, document the reduced margin explicitly.

## Refs

Follow-up to PR #23 and PR #24.
```
