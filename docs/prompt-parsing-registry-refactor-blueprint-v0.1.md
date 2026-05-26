# Prompt-Parsing Registry Refactor — Blueprint v0.1

## 0. Status

Design note only. No code in `ai_analyzer.py` has been changed by this document.
It is the concrete, incremental version of the "Path A" recommendation: collapse the
hand-maintained per-phrasing regex into a single declarative family registry, with
**span consumption** as the mechanism that makes "extract / strip / compound-protect"
derive from one source instead of three.

Related existing docs and code:
- `docs/bucketed-prompt-family-architecture-v0.1.md`
- `docs/guarded-llm-prompt-normalizer-v0.1.md` (the "Path B" layer)
- `query_understanding/` — **Path B is already built and running in shadow/observe-only mode**;
  see Section 9. This blueprint's registry (Path A) and that package should converge on one
  family ontology (`query_understanding/hard_filter_catalog.py`).

## 1. The problem this fixes (measured)

The prompt-parsing layer currently holds ~253 regex literals. The worst duplication:

| Family | extract | strip | compound-protect | copies of the same grammar |
|---|---|---|---|---|
| age | `_extract_age_constraint` (34) | `_strip_age_constraint_phrases` (32) | `_parse_compound_query` (9) | **3** |
| visa | `_extract_us_visa_constraint` (34) | `_strip_visa_constraint_phrases` (31) | — | **2** |

Every new age phrasing means editing up to three methods. Two structural bugs also
trace back to this design:

1. **COC-grade vs rank collision** — `chief mate coc` lights up both
   `_extract_coc_grade_constraint` (grade=chief_officer) and `_extract_rank_constraint`
   (rank=chief_officer), because nothing "consumes" the matched text after the first
   family claims it.
2. **Age-range `and` split** — `_parse_compound_query` needs a *second* copy of the age
   patterns purely to stop `between 30 and 50` from being split on " and ".

Both disappear once a family can declare the text span it owns, and later stages only
see what's left.

## 1A. Observed symptom: clauses after `and` get silently dropped

Reported behaviour: "when I use a prompt with `and`, most times the text after `and`
gets ignored."

The parser does **not** truncate at `and` — every `_extract_*_constraint` runs
`re.search` over the whole prompt, so a family on either side of `and` is detected. The
dropping happens downstream, via three mechanisms:

1. **Recognized-but-unapplied families.** `sea_service` and `vessel_type` are parsed and
   then pushed into `unapplied_constraints`, i.e. detected but intentionally **not**
   hard-gated. `master and 5 years sea service` parses the second clause and then does
   nothing with it. This is the most common cause.
2. **Unrecognized clauses reach only the LLM — if it runs.** Clauses like `DP experience`,
   `deep sea`, `cruise ships`, nationality, or education match no family. They survive
   only as text handed to the LLM. But `_has_semantic_intent` recognizes ~12 hardcoded
   words, so when the structured part is `structured_only_prompt and not
   has_semantic_intent`, **the LLM is skipped** and the clause is genuinely ignored.
3. **The compound structure is parsed and then discarded.** Any prompt with at least one
   structured family takes the full-scan branch, where `_parse_compound_query`'s
   `sub_queries` are thrown away. Replay of the current branch logic:

   ```
   valid US visa and 5 years on bulk carriers       -> applied:[us_visa]    -> FULL-SCAN (sub_queries DISCARDED)
   master and chief officer                          -> applied:[rank_match] -> FULL-SCAN (sub_queries DISCARDED)
   chief engineer with C1/D visa and DP experience   -> applied:[rank_match] -> FULL-SCAN (sub_queries DISCARDED)
   tanker experience and offshore exposure           -> applied:[exp_ship]   -> FULL-SCAN (sub_queries DISCARDED)
   ```

Net: a clause's fate depends on which bucket (`applied` / `unapplied` / unrecognized) it
landed in, and the user is never told which clauses were dropped. The fix is **clause
accounting** (Section 7A): every clause must end up labelled `applied`, `soft`, or
`unsupported`, and that labelling must be visible.

## 2. Principle

- **Families are finite and stable** (~12). They are your schema. Keep them.
- **Phrasings are infinite.** Stop enumerating them by hand.
- Separate the three jobs that are currently fused into one mega-regex per family:
  1. **trigger** — does the prompt mention this family at all? (cheap keyword gate)
  2. **value** — extract the constraint payload + the matched span
  3. **normalize** — map to the canonical constraint shape
- After a family extracts, **blank out its span** in the working prompt. Then:
  - "strip" is just *the leftover text after all families have run* — no separate patterns.
  - "compound-protect" is automatic — the ` and ` inside `between 30 and 50` is already
    blanked before compound splitting runs.
  - cross-family collisions vanish — higher-priority families consume their span first.

## 3. Shared value grammars (define once, reuse everywhere)

These are the reusable primitives. Different families compose them. Each returns the
match plus its `(start, end)` span in the string it was given.

```python
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

@dataclass
class ValueMatch:
    value: dict            # normalized constraint payload, e.g. {"min_age": 30, "max_age": 50}
    span: tuple            # (start, end) offsets in the prompt the matcher was given
    raw: str               # the literal text matched (for display_value / audit)

# --- primitives -------------------------------------------------------------
# NOTE: you are NOT rewriting your regexes. You are relocating them into ONE
# home per family and adding span capture (match.start()/match.end()).

def first_match(patterns, text, flags=re.IGNORECASE):
    """Return (re.Match, pattern) for the earliest-starting match, or (None, None)."""
    best = None
    best_pat = None
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m and (best is None or m.start() < best.start()):
            best, best_pat = m, pat
    return best, best_pat
```

The point of `first_match` is that a family extractor becomes "run my list of patterns,
return the earliest hit with its span" — and that list lives in exactly one place.

## 4. The Family object

```python
@dataclass
class PromptFamily:
    id: str                      # "age_range"
    bucket: str                  # "identity"  (for audit / future LLM ontology)
    constraint_key: str          # key under hard_constraints, e.g. "age_years"
    applied_id: str              # value pushed into applied/unapplied list, e.g. "age_range"
    extract: Callable[[str], Optional[ValueMatch]]
    triggers: tuple = ()         # cheap pre-filter; empty = always try
    apply_state: str = "applied" # "applied" | "unapplied" | "parsing_note"
    priority: int = 100          # LOWER runs first and consumes its span first
    consumes_span: bool = True   # set False for families that shouldn't blank text
                                 # (e.g. a pure trigger that others also read)
```

`priority` is how you resolve overlaps deterministically. `coc_grade` gets a lower
number than `rank_match`, so `chief mate coc` is claimed by COC-grade first and the
rank extractor never sees those words.

## 5. Worked example — the age family (replaces 3 methods with 1)

```python
def _age_extract(prompt: str) -> Optional[ValueMatch]:
    # These are your EXISTING patterns from _extract_age_constraint, moved here verbatim.
    range_patterns = [
        r'between\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})\s+years?\s+old',
        r'between\s+the\s+ages?\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
        # ... rest of your range_patterns ...
    ]
    min_patterns = [ r'at\s+least\s+(\d{1,2})\s+years?\s+old', r'older\s+than\s+(\d{1,2})', ... ]
    max_patterns = [ r'up\s+to\s+(\d{1,2})\s+years?\s+old', r'younger\s+than\s+(\d{1,2})', ... ]

    m, _ = first_match(range_patterns, prompt)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi: lo, hi = hi, lo
        return ValueMatch({"min_age": lo, "max_age": hi}, (m.start(), m.end()), m.group(0))

    m, pat = first_match(min_patterns, prompt)
    if m:
        v = int(m.group(1))
        if any(t in m.group(0).lower() for t in ("older than", "over", "above")): v += 1
        return ValueMatch({"min_age": v, "max_age": None}, (m.start(), m.end()), m.group(0))

    m, pat = first_match(max_patterns, prompt)
    if m:
        v = int(m.group(1))
        if any(t in m.group(0).lower() for t in ("younger than", "under", "below", "less than")): v -= 1
        return ValueMatch({"min_age": None, "max_age": v}, (m.start(), m.end()), m.group(0))
    return None

AGE_FAMILY = PromptFamily(
    id="age_range", bucket="identity",
    constraint_key="age_years", applied_id="age_range",
    triggers=("age", "aged", "year", "old"),   # cheap gate; skip extract if none present
    extract=_age_extract,
    priority=20,
)
```

`_strip_age_constraint_phrases` and the `age_range_patterns` block in
`_parse_compound_query` **both go away** — they are now produced by span consumption
(Section 6). That is the 34 + 32 + 9 = 75 → ~34 reduction for age alone.

## 6. The engine loop (one place, runs all families)

```python
def _blank_span(text: str, span: tuple) -> str:
    s, e = span
    return text[:s] + (" " * (e - s)) + text[e:]   # preserve offsets for later families

def parse_prompt(self, user_prompt, rank=None):
    constraints = {
        "rank": str(rank or "").strip(),
        "hard_constraints": {},
        "applied_constraints": [],
        "unapplied_constraints": [],
        "parsing_notes": [],
    }
    working = str(user_prompt or "")

    # priority order => specific families consume their span before general ones
    for family in sorted(self.PROMPT_FAMILIES, key=lambda f: f.priority):
        if family.triggers and not any(t in working.lower() for t in family.triggers):
            continue
        match = family.extract(working)
        if not match:
            continue

        constraints["hard_constraints"][family.constraint_key] = match.value
        if family.apply_state == "applied":
            constraints["applied_constraints"].append(family.applied_id)
        elif family.apply_state == "unapplied":
            constraints["unapplied_constraints"].append(family.applied_id)
        else:
            constraints["parsing_notes"].append(match.raw)

        if family.consumes_span:
            working = _blank_span(working, match.span)

    # whatever survives is the candidate for semantic intent / compound logic
    residual = re.sub(r"\s+", " ", working).strip(" ,.-")
    constraints["residual_text"] = residual

    # de-dupe exactly as today
    constraints["applied_constraints"]   = list(dict.fromkeys(constraints["applied_constraints"]))
    constraints["unapplied_constraints"] = list(dict.fromkeys(constraints["unapplied_constraints"]))
    constraints["parsing_notes"]         = list(dict.fromkeys(n for n in constraints["parsing_notes"] if n))
    if not constraints["applied_constraints"] and not constraints["unapplied_constraints"] and residual:
        constraints["parsing_notes"].append(residual)
    return constraints
```

Now:
- **strip** = `constraints["residual_text"]` (no `_strip_*` methods needed).
- **compound detection** runs on `residual_text`, which already has age/visa spans blanked,
  so `_parse_compound_query` no longer needs its age-protection patterns — feed it the
  residual instead of the raw prompt.
- **`_has_semantic_intent`** also runs on `residual_text` — cleaner than the current
  approach of replacing display_value strings back out one by one.

## 7. How this kills the two known bugs

**COC-grade vs rank.** Register with priorities:

```python
COC_GRADE_FAMILY = PromptFamily(..., id="coc_grade_match", priority=30, ...)
RANK_FAMILY      = PromptFamily(..., id="rank_match",      priority=60, ...)
```

`chief mate coc` → COC-grade (priority 30) extracts `{required_grades:["chief_officer"]}`
and blanks the span `chief mate coc`. When the rank family (priority 60) runs, those
words are gone, so it does **not** also emit a rank constraint. The recruiter's intent
("find a chief-mate COC") is preserved without the spurious rank filter.

**Age-range `and` split.** `between 30 and 50` is consumed by AGE_FAMILY (priority 20)
before compound detection runs. The ` and ` is already blanked, so the residual has no
splittable `and`. The separate age-protection regex list in `_parse_compound_query` is
no longer needed.

## 7A. Clause accounting — applied / soft / unsupported (fixes the `and` drop)

The registry gives every span a home, which means after `parse_prompt` runs you can
classify the *entire* prompt into three disjoint buckets and **show them to the user**:

- **applied** — a family was recognized and is hard-gated (PASS/FAIL/UNKNOWN).
- **soft** — recognized intent that is scored by the LLM but not hard-gated (e.g. a
  vessel-experience preference, or a semantic phrase like "strong leadership").
- **unsupported** — leftover text (`residual_text`) that mapped to no family and is not
  meaningfully semantic. This must be surfaced, never silently dropped.

The data is already half-present today: `run_analysis_stream`'s `complete` event carries
`unapplied_constraints` and `parsing_notes`. The gap is that in a *mixed* prompt where
something applied, the ignored clauses are carried silently and not surfaced. Clause
accounting closes that gap.

### 7A.1 Emit a clause ledger

Extend `parse_prompt` to return a per-clause ledger:

```python
constraints["clause_ledger"] = [
    {"text": "valid US visa",        "disposition": "applied", "family": "us_visa"},
    {"text": "5 years sea service",  "disposition": "soft",    "family": "sea_service"},
    {"text": "deep sea exposure",    "disposition": "unsupported", "family": None},
]
```

Build it from span consumption: each family that fired contributes an `applied`/`soft`
row (its `apply_state`); whatever survives in `residual_text`, split on conjunctions,
contributes `unsupported` rows. The UI then shows: "Enforced: US visa. Soft-matched: sea
service. Not applied: 'deep sea exposure'." Nothing is invisible.

### 7A.2 Promote unapplied families whose facts now exist

`sea_service` and `vessel_type` were `unapplied` because their evaluation/facts weren't
ready. They are now: vessel types via `experience.vessel_types`, sea-service months are
derivable. Wire their `_evaluate_*_rule` and move them from `unapplied_constraints` to
`applied_constraints` (i.e. `apply_state="applied"` in their `PromptFamily`). That alone
makes most "... and <experience clause>" prompts actually filter.

For anything genuinely not ready, keep `apply_state="soft"` so it is LLM-scored and
ledgered as `soft` — explicitly degraded, not silently dropped.

### 7A.3 Fix the LLM-skip condition

Current skip: `if structured_only_prompt and not has_semantic_intent: <PASS, no LLM>`.
The defect: a prompt can be "structured-only" on the part that parsed while still
carrying unhandled leftover text. Replace the gate with a residual check:

```python
# OLD
if structured_only_prompt and not has_semantic_intent:
    promote_to_verified_without_llm()

# NEW
has_residual = bool(constraints.get("residual_text"))
if not has_residual and not has_semantic_intent:
    # the prompt was fully consumed by hard families -> deterministic PASS is complete
    promote_to_verified_without_llm()
else:
    # there is leftover/semantic intent -> the LLM (or an explicit unsupported notice)
    # MUST run; never skip straight to PASS while text is unaccounted for
    run_llm_reasoning()
```

This preserves the win from the earlier safety work (deterministic-only prompts skip the
LLM and can't be vetoed) while guaranteeing that a non-empty residual is always either
LLM-scored or surfaced as unsupported.

Also widen `_has_semantic_intent`: instead of a 12-word allowlist, treat "is there any
meaningful `residual_text` left after family consumption?" as the semantic signal. The
registry produces that residual for free, so the allowlist can largely go away.

### 7A.4 Stop discarding `sub_queries` in the full-scan branch

Today `_parse_compound_query` splits on `and`, and the actionable branch ignores the
result. Two clean options:

- **Preferred:** delete the separate compound-retrieval branch. With the registry,
  per-clause family detection is the norm and `residual_text` captures the rest, so the
  AND/OR split is redundant for structured prompts.
- **Or:** when full-scanning, still pass the parsed clauses to the LLM as an explicit
  per-condition checklist (`condition 1: ...; condition 2: ...; ALL must hold`) rather
  than re-deriving compound-ness from the raw string.

Either way, "split then throw away" stops being wasted work.

### 7A.5 Why this is the structural fix, not another patch

The root cause of the `and` symptom is that the pipeline has no concept of *"this prompt
is a conjunction of N clauses, each of which must be accounted for."* Clause accounting
makes that explicit: every clause is `applied`, `soft`, or `unsupported`, and the ledger
is shown. A clause can no longer fall through all three buckets without a trace, which is
exactly what "text after `and` gets ignored" was.

## 8. Migration plan (keep all 215 tests green)

Do it as a strangler, not a rewrite:

1. **Add** the `ValueMatch`, `PromptFamily`, `first_match`, `_blank_span`, `parse_prompt`
   pieces alongside the existing code. Touch nothing else yet.
2. **Build `PROMPT_FAMILIES`** by wrapping your *existing* extractor bodies. For families
   you haven't refactored, the wrapper can call the old `_extract_*_constraint` and
   synthesize a span via `re.search(re.escape(display_value), prompt)` — so you can
   migrate incrementally, one family at a time, while the rest run unchanged.
3. **Make `_extract_job_constraints` delegate** to `parse_prompt`, then assert the output
   dict is identical to the legacy path on your existing job-constraints tests. This is
   the safety net: same inputs → same `hard_constraints` / `applied_constraints` shape.
4. **Migrate age first** (biggest win, 3 copies → 1). Delete `_strip_age_constraint_phrases`
   and the age block in `_parse_compound_query` only after the residual-based path passes.
5. **Migrate visa, then the rest**, lowest-risk family per commit. Each migration is a
   no-op to the public contract, verified by the existing tests.

Contract to preserve (so downstream `_evaluate_hard_filters` is untouched):
- `hard_constraints[constraint_key]` payload shape per family is byte-identical to today.
- `applied_constraints` / `unapplied_constraints` membership is identical.
- `parsing_notes` behaviour for ambiguous fragments (e.g. `tanker endorsement`) is identical.

## 9. Path B — the LLM normalizer ALREADY EXISTS in shadow mode (promote, don't build)

Correction to earlier framing: Path B is **not** a future "build a normalizer" task. The
`query_understanding/` package is already implemented and running in **shadow / observe-only**
mode. The remaining work is to **graduate it from shadow to active**, family-by-family,
using the comparison telemetry it is already collecting.

### 9.1 What is already built

In `query_understanding/`:

- `schema.py` — query-plan v1 schema with `validate_query_plan_v1` / `normalize_query_plan_v1`
- `hard_filter_catalog.py` — the family catalog (`ACTIVE_FAMILY_IDS`, `SUPPORTED_FAMILY_IDS`,
  `UNAPPLIED_FAMILY_IDS`); this is effectively the ontology the registry in this doc formalizes
- `legacy_parser_adapter.py` — wraps the existing `ai_analyzer` regex parser into the query-plan schema
- `shadow_llm_provider.py` — builds the LLM prompt and produces the normalized query plan (the actual normalizer)
- `llm_normalizer.py` — gated entry point; returns `None` unless enabled + a provider is attached
- `normalizer_compare.py` — compares legacy parse vs LLM plan (`regression` / `schema_error` / `catalogue_drift`)
- `shadow_audit.py`, `supabase_telemetry_store.py` — audit entry construction and telemetry persistence

### 9.2 How it runs today (observe-only)

- `ai_analyzer.py` has **zero** references to `query_understanding`. The live search still gates
  purely on the regex parser + reasoning LLM. The normalizer does not touch results.
- In `backend_server.py`, every search schedules `_schedule_search_prompt_audit` ->
  `_log_search_prompt_audit` on a **background daemon thread**, which builds a shadow plan,
  compares it to the legacy parse, and writes telemetry to Supabase.
- The normalizer LLM call is gated twice:
  - `llm_normalizer.is_enabled()` reads `NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM` (default `False`)
  - `_should_force_shadow_llm()` reads `NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM_FORCE` and also
    **forces shadow LLM on for `recruiter`-role actors** — but still only inside the observe-only
    audit path.

So for the question "if the parser doesn't recognize a prompt, does it go through the LLM?":
the normalizer LLM *does* run on unrecognized prompts today (for recruiter sessions / when the
flag is on), but **only to log a shadow comparison** — its output does not yet rescue the prompt
into a hard-gated family. The reasoning LLM still handles residual text as a soft signal in the
live path, exactly as in Section 7A.

### 9.3 Promotion path (shadow -> active)

This is the actual Path B work:

1. **Use the shadow telemetry as the go/no-go signal.** The `comparison_outcome` counts
   (agree vs `regression` / `schema_error` / `catalogue_drift`) per family in Supabase are the
   evidence for which families are safe to promote first. Promote a family only when its shadow
   agreement rate is high and regressions are rare.
2. **Promote one family at a time** by letting the normalized query plan supply that family's
   constraint when the deterministic parser produced nothing for it, behind the per-family
   confidence gate from `guarded-llm-prompt-normalizer-v0.1.md` (>= 0.90 auto, 0.75-0.89
   low-risk only, < 0.75 do not apply).
3. **Keep the deterministic parser as the primary.** If the regex parser already extracted a
   family cleanly, that wins; the normalizer only fills `residual_text` gaps. This is the same
   "deterministic wins first" rule the design doc specifies.
4. **The LLM still never decides eligibility.** It proposes families + values; the
   `_evaluate_*_rule` deterministic gate still decides candidate PASS/FAIL/UNKNOWN.

### 9.4 How the registry (this doc) and the existing package fit together

- `hard_filter_catalog.py` and the `PROMPT_FAMILIES` registry proposed here are the **same
  ontology** expressed in two places. Converge them: the registry should import / derive its
  family IDs from `hard_filter_catalog` so the legacy parser, the registry, and the normalizer
  all share one source of truth.
- `residual_text` from `parse_prompt` (Section 6) is exactly the input the normalizer should
  receive — only the text no family consumed. Today the shadow provider sees the whole prompt;
  feeding it `residual_text` instead reduces cost and false `catalogue_drift` noise.

### 9.5 Two integrity checks to do now

1. **Watch for legacy-adapter drift.** `legacy_parser_adapter.py` represents "what the regex
   parser produced." If it drifts from `ai_analyzer._extract_job_constraints`, the shadow
   comparison reports false regressions. Ideally the adapter calls the real parser rather than
   re-deriving its output.
2. **Read the current shadow outcomes before promoting anything.** The fastest way to know which
   family to graduate first is to look at the existing telemetry, not to guess.

## 10. Test strategy

- **Parity test** (the keystone): for a corpus of prompts (reuse
  `docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_*.json`), assert
  `parse_prompt(p) == legacy_extract_job_constraints(p)` for every prompt, field by field,
  until every family is migrated. This lets you refactor fearlessly.
- **Span tests**: for each migrated family, assert the consumed span and that
  `residual_text` is what you expect (this is what guarantees strip + compound-protect).
- **Collision tests**: `chief mate coc` → coc_grade only, no rank; `between 30 and 50 and
  valid US visa` → age + visa, residual has no stray `and`.
- **Clause-ledger tests** (the `and`-drop regression floor): for each disposition,
  assert the ledger is complete and nothing is silently lost. E.g.
  `master and 5 years sea service` → ledger has `rank_match=applied` AND
  `sea_service` present as `applied` (post-7A.2) or `soft`, never absent;
  `valid US visa and deep sea exposure` → `us_visa=applied` plus an `unsupported`
  row for `deep sea exposure`. Assert `applied ∪ soft ∪ unsupported` reconstructs the
  whole prompt with no gaps.
- **LLM-skip tests**: prompt fully consumed by hard families and no residual → LLM not
  called; same prompt with trailing unhandled text → LLM called (or unsupported notice
  emitted). Guards the 7A.3 fix.
- Keep the existing `test_ai_analyzer_job_constraints.py` cases — they become the
  regression floor for the parity test.

## 11. Bottom line

You will always have families — that is the schema, and it is correct. What you stop
doing is hand-encoding phrasings in three places. Span consumption turns "strip" and
"compound-protect" into byproducts of one extraction pass, removes the COC/rank and
age-`and` bugs by construction, and leaves a clean `residual_text` seam to feed the
guarded LLM normalizer that **already exists in shadow mode** (`query_understanding/`).
Path B is therefore a *promotion* task — graduate the shadow normalizer to active,
family-by-family, on the strength of its comparison telemetry — not a build task.

The `and`-truncation symptom is the same root cause seen from the user's side: the
pipeline never accounted for every clause. Clause accounting (Section 7A) makes each
clause explicitly `applied`, `soft`, or `unsupported` and shows that ledger, so a clause
can no longer vanish between buckets. Promote the now-ready `sea_service`/`vessel_type`
families to `applied`, replace the LLM-skip gate with a `residual_text` check, and stop
discarding the compound `sub_queries`.

Migrate age first behind a parity test; the rest follow the same shape with near-zero
contract risk.
