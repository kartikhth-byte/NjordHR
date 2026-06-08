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

### 7A.2 Two distinct steps: make a clause VISIBLE vs make it ENFORCED

> Correction (reviewer-driven, verified against code 2026-05-26): an earlier draft of this
> section claimed `sea_service`/`vessel_type` facts "are now ready — just wire their
> `_evaluate_*_rule` and flip them to applied." That is wrong. Grep confirms there is **no**
> `_evaluate_sea_service_rule` or `_evaluate_vessel_type_rule`; both families only ever go to
> `unapplied_constraints`. (`experience_ship_type` is a *different*, already-working family —
> don't confuse it with the `vessel_type` unapplied family.) Promoting them is real evaluator
> work plus fact-path verification, not a flag flip.

There are two separable things, and they must not be conflated:

- **Make a clause visible (cheap, low-risk):** clause accounting (7A.1) + `residual_text`.
  After this, `master and 5 years sea service` shows "Not enforced: 5 years sea service"
  instead of silently dropping it. This fixes the *honesty* half of the `and` complaint and
  needs no new evaluator.
- **Make a clause enforced (more work, higher-risk):** wire a deterministic
  `_evaluate_<family>_rule`, verify the fact extraction it depends on, add regression tests,
  then move the family from `unapplied_constraints` to `applied_constraints`
  (`apply_state="applied"`). Only then does the clause actually filter candidates.

Sequencing recommendation: ship the *visible* step first for every family. Promote a family
to *enforced* one at a time, only after its evaluator exists and its fact path is trusted.
Until then keep it `apply_state="soft"` (LLM-scored, ledgered as `soft`) — explicitly
degraded, never silently dropped. Do **not** batch-promote families on the assumption the
facts are ready; verify per family.

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
result. This is sequenced, not done at once:

- **Near term (keep the branch):** when full-scanning, pass the parsed clauses to the LLM
  as an explicit per-condition checklist (`condition 1: ...; condition 2: ...; ALL must
  hold`) rather than re-deriving compound-ness from the raw string. Low risk, no deletion.
- **End state (only after the registry is the norm):** delete the separate
  compound-retrieval branch, because per-clause family detection + `residual_text` make the
  AND/OR split redundant for structured prompts.

Deleting the branch is the destination, **not** step one — do not remove it while the
legacy compound path is still load-bearing. Either way, "split then throw away" stops being
wasted work.

### 7A.5 Why this is the structural fix, not another patch

The root cause of the `and` symptom is that the pipeline has no concept of *"this prompt
is a conjunction of N clauses, each of which must be accounted for."* Clause accounting
makes that explicit: every clause is `applied`, `soft`, or `unsupported`, and the ledger
is shown. A clause can no longer fall through all three buckets without a trace, which is
exactly what "text after `and` gets ignored" was.

## 8. Migration plan (keep all 215 tests green)

**Two independent tracks — do not run them in the same pass.** This doc describes two
changes that are easy to conflate: (A) the registry/span-consumption refactor of the
*deterministic parser* (Sections 3–7A), and (B) the shadow→active promotion of the *LLM
normalizer* (Section 9). They touch different code, carry different risk, and have different
gates. Interleaving them makes regressions un-diagnosable. Land Track A's clause-accounting
slice first (it's the near-term user-visible win); pursue Track B promotion separately and
later, on its own eval gate (Section 11).

**Phasing (reviewer-aligned).** This is incremental by design — there is no big-bang rewrite:

- **Phase 1 — visibility:** clause accounting (7A.1) + `residual_text` + the LLM-skip fix
  (7A.3). No new evaluators, no family promotions. Fixes the *honesty* half of the `and`
  complaint. Lowest risk.
- **Phase 2 — registry wrapper for the highest-collision families only:** age, visa, COC
  grade, rank-duration. Wrap existing extractor bodies; do not rewrite all families.
- **Phase 3 — parity tests, then gradual family-by-family migration** of the remainder.
- **Phase 4 — enforcement + promotion:** add missing evaluators (e.g. `sea_service`,
  `vessel_type`) and verify fact paths before flipping any family to `applied` (7A.2); use
  Track B's eval gate (Section 11) to decide which normalizer families graduate.

Within a phase, do it as a strangler, not a rewrite:

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

## 11. Promotion eval workflow (how to decide a family is safe to graduate)

The shadow normalizer (Section 9) needs a go/no-go signal before any family moves from
shadow to active. Since the implementation is recent, there is little organic telemetry —
so generate the signal offline against a **labeled tail set**, do not wait for traffic.

### 11.1 Why a tail set, not the bootstrap corpus

Agreement-with-the-regex-parser is the wrong signal: the bootstrap/extrapolated corpus is
full of prompts the regex parser already handles (`below 50`, `2nd engineer`, …), so
matching it only proves the LLM can reproduce a solved case. The normalizer's value is on
the prompts the regex parser **fails** — the long tail. So the eval set must be prompts
that currently miss, partially parse, or hit the wrong family, plus qualitative controls
that must stay unsupported.

Artifact: `AI_Search_Results/seajobs_tail_set_v0.1.json` (64 labeled prompts: ~54
rescue cases across every family + a `compound_tail` group for the `and`-symptom + 6
qualitative controls). Each entry carries gold fields beyond what the harness reads:
`expected_primary_family`, `expected_constraint`, `current_parser`
(`miss|partial|wrong_family|unsupported_ok`), `note`. The labels themselves need a human
review pass (some defaults are judgement calls, e.g. `recently signed off` -> 6 months).

### 11.2 Three metrics that gate promotion

> Reviewer-driven addition (2026-05-26): a tail-set run alone is **necessary but not
> sufficient**. Tail prompts over-represent rare/adversarial phrasing; a normalizer can ace
> the tail and still regress the boring ~80% of common prompts the regex parser already gets
> right. So the gate needs a regression metric on the *solved set*, not just rescue on the
> tail. `scripts/tail_set_score.py` now accepts a solved-set report input and computes (3)
> as well, so the promotion gate can require all three metrics before any family is
> promoted on the strength of an eval run.

1. **rescue_rate** (per family): of the prompts the regex parser currently fails, how many
   did the normalizer map to the expected family? This is the upside you promote *for*.
2. **control_violations**: of the qualitative control prompts that must stay unsupported,
   how many did the normalizer hallucinate an ACTIVE family for? This is the safety floor
   and must be zero.
3. **solved_set_regression** (per family): on a held-out set of prompts the regex parser
   **already** handles correctly, how many does the normalizer now get *wrong* (different
   family, different values, or drops a constraint)? This is the regression floor and must
   also be zero. Build this set from the bootstrap/extrapolated corpus rows where the legacy
   parser is known-good, label them, and score the normalizer against the legacy result.

A family is a **promote candidate** only when `rescue_rate >= threshold` (default 0.8) AND
zero control violations AND zero solved-set regressions. A control violation or a solved-set
regression involving a family demotes it even if its rescue rate is high — inventing or
breaking a constraint is more dangerous than missing one. Because misreading a hard
constraint (visa/age/passport/COC) is costlier than missing a vague semantic cue, the bar
should be *per-family and risk-weighted*: high-stakes families need a higher rescue
threshold and a larger zero-regression sample than low-stakes ones.

`family_rescued` (did it pick the right family) is scored automatically. **Value
correctness** (did it pick the right numbers/enums) is flagged `NEEDS_HUMAN_REVIEW`, not
auto-judged, because the plan payload shape differs from the gold shape — the scorer
stores both side by side for the reviewer.

### 11.3 The two-command loop

```
# 1. Generate comparison data — needs Gemini key in config + repo deps; automatable by
#    any agent/session with the app's credentials. Does NOT need the UI.
NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 \
python scripts/query_understanding_shadow_audit.py \
  --corpus AI_Search_Results/seajobs_tail_set_v0.1.json \
  --output AI_Search_Results/tail_set_eval_$(date +%F).json

# 2. Score it — pure stdlib, no credentials; runs anywhere.
python scripts/tail_set_score.py \
  --eval AI_Search_Results/tail_set_eval_$(date +%F).json \
  --output AI_Search_Results/tail_set_score_$(date +%F).json
```

Step 2 (`scripts/tail_set_score.py`) reads the harness rows' `comparison_results[*].llm_record`
(status `applied`) to see which families the normalizer proposed, joins to the gold set by
prompt text, and prints per-family `rescued/total`, control violations, and the promote
verdict. It detects a disabled-LLM run (`shadow_mode: disabled` / no `llm_plan`) and warns
to re-run with the flag instead of scoring everything as a miss.

### 11.4 Reading a low rescue rate correctly

A family missing its rescues has two distinct causes that need different fixes:

- **Normalizer-side**: the LLM didn't recognize the wording. Fix in the prompt/normalizer.
- **Catalogue-side**: the family's value vocabulary is incomplete (e.g. `VLCC` / `reefer`
  absent from `CANONICAL_SHIP_FAMILIES` in `hard_filter_catalog.py`), so even a correct
  mapping can't normalize. Fix in the catalogue.

The scorer's per-case `llm_applied_families` distinguishes these: if the LLM applied the
right family but with empty/!=expected values, it's catalogue-side; if it applied no/ wrong
family, it's normalizer-side.

### 11.5 Promotion sequence

1. Run the loop; review the gold labels and the `NEEDS_HUMAN_REVIEW` value cases once.
2. Promote the highest-confidence family first (likely `age_range` or `rank_match` —
   cleanest constraint shapes), behind the per-family confidence gate (Section 9.3).
3. After promotion, the live shadow telemetry becomes ongoing monitoring for that family;
   the tail-set eval remains the pre-promotion gate for the next one.
4. Re-run the loop whenever the normalizer prompt, the catalogue, or the tail set changes.

### 11.6 Agent runbook — how to run the prompt-normalizer eval

For an agent (or engineer) with the app's credentials. The harness needs a Gemini key; the
scorer does not.

**When this must run (scope — it is NOT a global prerequisite):**
- Run it **before** any work that promotes/refines a normalizer family or changes the
  normalizer prompt, catalogue, or tail set (Track B).
- Do **not** run it before unrelated work — the registry refactor (Track A), packaging,
  hard-filter evaluator work, or anything outside prompt normalization. It costs Gemini
  calls and tells you nothing about those tracks.
- The project's standing "before any work" checklist (read `docs/CONTEXT_HANDOFF_*`, the
  backlog, and `git status`) is separate and still applies; this eval is in addition to it,
  only for normalizer-promotion work.

**Preconditions:**
1. `cd /Users/kartikraghavan/Tools/NjordHR`.
2. Repo deps importable (the app's Python env; the harness imports `ai_analyzer` which needs
   PyMuPDF etc.).
3. Gemini key reachable by the analyzer config — either `[Credentials] Gemini_API_Key` in
   `config.ini` or `GEMINI_API_KEY` in the environment.
4. The tail set exists: `AI_Search_Results/seajobs_tail_set_v0.1.json`.

**Steps:**
```
# Step 1 — generate comparison data (LLM ON). Writes one row per tail prompt.
NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 \
python scripts/query_understanding_shadow_audit.py \
  --corpus AI_Search_Results/seajobs_tail_set_v0.1.json \
  --output AI_Search_Results/tail_set_eval_$(date +%F).json

# Step 2 — confirm the LLM actually ran (guard against a silent disabled run).
#   Expect shadow_mode "enabled" and non-empty comparison_results / llm_plan.
python -c "import json,sys; d=json.load(open('AI_Search_Results/tail_set_eval_$(date +%F).json')); \
rows=d.get('rows',[]); enabled=sum(1 for r in rows if r.get('shadow_mode')=='enabled' and r.get('llm_plan')); \
print(f'rows={len(rows)} llm_enabled_rows={enabled}'); sys.exit(0 if enabled else 1)" \
  || echo 'LLM did not run — check GEMINI key in config and the env flag, then re-run Step 1.'

# Step 3 — score it (no credentials needed).
python scripts/tail_set_score.py \
  --eval AI_Search_Results/tail_set_eval_$(date +%F).json \
  --output AI_Search_Results/tail_set_score_$(date +%F).json
```

**Interpreting the result:**
- Read the per-family `rescued/total` and the `promote_candidates` list.
- Any `control violation` line is a hard stop for the implicated family — it hallucinated a
  constraint on a qualitative prompt.
- `NEEDS_HUMAN_REVIEW` value cases require a person to confirm the values, not just the
  family, are correct.

**Hard limits — what an agent must NOT do off this run alone:**
- Do **not** flip any family from shadow to active on this eval by itself. The promotion gate
  now includes metric (3) solved_set_regression, but a clean tail-set run alone still does
  not prove the common-prompt path is safe. The solved-set report must also be clean.
- Do **not** change the live decision path or disable the deterministic parser.
- Surface the scores and the open caveats to a human; promotion is a human decision.

### 11.7 Eval workflow — iteration vs. promotion

The shadow audit serves two distinct purposes; treat them with different rigor.

**Iteration eval (fast, focused, optional)**

Use during development when tuning prompt rules, regex patterns, or deterministic
repair logic for a specific family.

- When the harness's `--family-filter` flag is implemented, use it to run
  per-family corpora. Otherwise maintain small per-family JSON files alongside
  the combined corpus.
- Faster turnaround, lower Gemini cost.
- Acceptable to iterate prompt rules and re-run dozens of times.
- Not authoritative for any promotion decision.

**Promotion eval (slow, full, required before any shadow→active flip)**

Always run with the full combined corpus before voting on any family promotion.

- No `--family-filter` flag.
- Captures cross-family regressions (e.g. patch-15's STCW additions indirectly
  broke age_range by bloating the system prompt).
- Required to verify the per-family confidence gate from
  `guarded-llm-prompt-normalizer-v0.1.md`.
- Must show stable numbers across **at least two consecutive runs** to control
  for LLM non-determinism (Gemini Flash Lite is not fully deterministic even
  with seed=0). The 2026-06-01 promotion eval needed three consecutive runs to
  confirm stability.
- Use a date-stable variable to avoid the midnight-rollover filename bug:
```bash
EVAL_DATE=$(date +%F)
```

**Anti-patterns**

- Promoting a family based on per-family eval alone. Per-family runs cannot
  detect cross-family interference (the patch-15 lesson).
- Single-run promotion votes. Flash Lite jitter can swing single-row results,
  especially on small tail sets (< 10 rows). Always confirm with a re-run.
- Adding LLM prompt content without re-validating all already-cleared families.
  The system prompt is a global resource; extending family rules for one family
  can degrade unrelated families if the prompt crosses a length threshold.
  Patch series confirmed empirically at ~3500 chars for Gemini Flash Lite.

### 11.8 Per-family promotion checklist

For each family flipping from shadow to active:

1. **Full combined eval gate**: rescue ≥0.8, zero controls, zero solved-set
   regressions, in at least two consecutive runs.
2. **Per-family confidence gate** from `guarded-llm-prompt-normalizer-v0.1.md`:
   ≥0.90 confidence on majority of rescue rows for auto-apply.
3. **Deterministic floor in place**: either an `_extract_<family>_constraint`
   in `ai_analyzer.py` or a deterministic repair path in
   `shadow_llm_provider.py` that catches family-detection failures when the
   LLM emits empty plans.
4. **Anchor veto in place**: `_<family>_is_anchored` helper that vetoes LLM
   proposals on figurative or context-mismatched prompts. Pattern: positive
   cue check + negative deny-list.
5. **Schema field coverage**: any constraint values the family needs (e.g.
   `visa_group`, `accepted_types`) must be in `query_understanding/schema.py`
   and propagated through `legacy_parser_adapter.py` and the LLM translation
   path.
6. **Tail-set coverage**: ≥10 rescue rows for statistical stability of the
   rescue rate; ≥3 family-adjacent controls testing the anchor veto's
   resilience against figurative or substring-overlap hallucinations.
7. **Watch live shadow telemetry** in Supabase for at least one observation
   window after promotion before promoting the next family.

## 12. Bottom line

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
can no longer vanish between buckets. First make clauses *visible* (clause accounting +
`residual_text` + the LLM-skip fix); make them *enforced* later, one family at a time, only
after each family's evaluator exists and its fact path is verified (`sea_service` and
`vessel_type` have **no** evaluator today — see 7A.2). Then stop discarding the compound
`sub_queries`.

Migrate age first behind a parity test; the rest follow the same shape with near-zero
contract risk. Gate every shadow-to-active promotion (a separate track) on the eval in
Section 11: high rescue rate on the prompts the regex parser fails, **zero** hallucinated
families on the controls, and **zero** regression on the solved set.

## 13. Reviewer feedback disposition (2026-05-26)

A developer review of this blueprint produced specific accept/decline points. This section
records what was changed and — equally important — what was deliberately **not** changed and
why, so the reasoning is auditable rather than silent.

### 13.1 Accepted — changed in this doc

| # | Reviewer point | Change made | Where |
|---|---|---|---|
| 1 | `sea_service`/`vessel_type` are not "ready to flip to applied"; verify evaluator + facts first | Rewrote 7A.2: confirmed via grep there is **no** `_evaluate_sea_service_rule`/`_evaluate_vessel_type_rule`; split "make visible" (no evaluator) from "make enforced" (new evaluator + fact verification); removed the "just wire and flip" claim | 7A.2, 12 |
| 2 | A tail-set run is necessary but not sufficient; can regress the common ~80% | Added a third gate metric **solved_set_regression** (must be zero) and risk-weighted per-family bars; noted that `tail_set_score.py` now enforces the solved-set gate as mandatory for any verdict beyond `hold` | 11.2 |
| 3 | Decouple the registry refactor from the shadow→active promotion | Added "two independent tracks — do not run in the same pass" plus an explicit Phase 1–4 plan mirroring the reviewer's phasing | 8 |
| 4 | "Delete the compound branch outright" is too aggressive | Reframed 7A.4: near-term keep the branch (LLM checklist); deletion is the *end state* only after the registry is the norm | 7A.4 |

### 13.2 Declined / no change needed — with rationale

- **"Don't rewrite all families in one shot."** No change: Section 8 already specifies a
  strangler migration (age first, one family per commit, parity-tested). The "full registry
  migration in one pass" the reviewer is wary of was a *menu option* offered in chat and
  declined — it was never the doc's recommendation. Tone tightened in Section 8 so this can't
  be misread.
- **"Keep the live decision path deterministic / shadow LLM observe-only."** No change: this
  is already the doc's stance (Section 9 documents the current observe-only wiring; Section
  9.3 makes promotion gated, family-by-family, with legacy fallback). We agree; nothing to
  alter.
- **"Don't rewrite the hard-filter evaluators in the same pass as the parser refactor."** No
  change: Section 8's "contract to preserve" already states `_evaluate_hard_filters` and the
  per-family payload shapes stay byte-identical through the parser migration. The refactor is
  parsing-only by construction.
- **"Keep the generic fallback extractor conservative/partial."** No change: nothing in the
  blueprint widens the fallback extractor. `residual_text` + clause accounting make
  unhandled text *visible*; they do not make the fallback more aggressive.
- **"Tail-case run can choose first families to graduate, but shouldn't flip the whole
  normalizer."** No change to intent — the doc never proposed a wholesale flip (Section 9.3 /
  11.5 are explicitly family-by-family behind confidence gates). The reviewer's emphasis is
  now reinforced by the new solved-set-regression gate (13.1 #2).

### 13.3 One point I'd push back on (recorded, not silently dropped)

The reviewer's Phase 1 ("clause accounting + residual text") is the right first step, but it
delivers **visibility, not enforcement**. After Phase 1, `master and 5 years sea service`
will *show* "Not enforced: 5 years sea service" — honest, but the clause still filters no one
until an evaluator exists (Phase 4). That's an acceptable, low-risk sequencing choice; it
just needs to be a conscious one, so nobody expects the `and` clauses to start *filtering*
at the end of Phase 1. This is captured in the 7A.2 visible-vs-enforced split.

### 13.4 Still open (not yet reflected in code or this doc beyond noting it)

- `scripts/tail_set_score.py` now implements metric (3), solved_set_regression, and the
  solved-set gate is mandatory for any verdict beyond `hold`. No family should be flipped
  on eval evidence alone unless both the tail set and solved set are clean.
- The gold labels in `seajobs_tail_set_v0.1.json` still need a human review pass.
- Cycle outcome: `age_range`, `certificate_requirement`, `stcw_basic`, and `us_visa`
  all cleared the promotion gate in the 2026-06-01 cycle. `age_range` remains the first
  family promoted from shadow to active in this branch after clearing the tail-set gate,
  controls, and solved-set checks. The remaining families stay on the follow-up backlog.

### 13.5 Adjacent implementation notes in the current branch

These items were implemented while working through the parser and runtime cleanup, but they
are **adjacent** to the registry refactor rather than part of its core contract. Keep them
visible in code review so they are not mistaken for blueprint requirements:

- **Unsupported-but-recruiter-like prompts now get semantic fallback.** Prompts such as
  `has experiencee in piracy routes` no longer fail fast if they look like real recruiter
  intent. They are allowed to proceed to semantic search with a warning that the result may
  be broad or approximate. This is a product-policy change adjacent to Path B and should be
  reviewed alongside the `residual_text` / skip-gate behavior, but it is not required by
  the registry refactor itself.
- **The active download root was corrected to the writable App Support `Resumes` folder.**
  The runtime config and launcher defaults now point at the canonical per-user app-data
  location, and the earlier `Downloads/NjordHR` / `temp12` defaults and detour logic were
  cleaned up. This is operational hygiene rather than prompt-parsing work, but it matters
  for reproducibility because the folder-backed resume corpus is what the AI search and
  download tabs discover at runtime.
