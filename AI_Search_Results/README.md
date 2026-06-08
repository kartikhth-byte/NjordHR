# AI Search Corpus

This directory holds the labeled prompt corpora and eval artifacts used to validate
the shadow-normalizer (`query_understanding/shadow_llm_provider.py`) and the
deterministic hard-filter foundation (`ai_analyzer.py`).

## What lives here

Two kinds of files end up in this folder:

1. **Canonical labeled corpora** — version-controlled, load-bearing files that
   define what the eval gates measure against. These **must** be tracked in git.
2. **Diagnostic / rollup JSON** — one-off eval results, per-folder extractions,
   rollups for specific dates. These are gitignored by default; commit
   individually only if they document a permanent decision.

The canonical corpora needed for the eval gates per
`docs/prompt-parsing-registry-refactor-blueprint-v0.1.md` §11 and
`docs/PROJECT_STATUS_2026-06-01.md` §3:

- `seajobs_tail_set_v0.X.json` — labeled `miss` / `partial` / `wrong_family`
  rows that the legacy parser fails to handle, plus family-adjacent controls.
  Drives each cycle's per-family eval and confidence gate.
- `docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json` — known-good
  prompts the legacy parser already handles. Acts as the regression floor.
- `docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json`
  — equivalent for the May extension work.

## 2026-06-08 — corpus loss event

During the 2026-06-08 repo cleanup, `seajobs_tail_set_v0.1.json` (146 labeled
prompts that drove the 5-family promote-ready verdict in
`docs/PROJECT_STATUS_2026-06-01.md` §1) was lost. Root cause:

- `AI_Search_Results/` had a blanket `.gitignore` rule.
- The corpus had never been `git add -f`'d, so it was always working-tree-only.
- A `git clean -fdx` during cleanup removed it from disk.
- The pre-cleanup safety snapshot used `git ls-files --others --exclude-standard`
  which skips gitignored files, so the file was not captured.
- The tag `eval-gate-green-v2` (PROJECT_STATUS §2 "rollback point") preserves
  the eval *code* but not the corpus it was scored against, since the corpus
  was never in any commit's tree.

Eight independent recovery searches were exhausted (all git refs, all tags, all
git objects, GitHub API, safety tgz, Mac filesystem, Mac Trash, iCloud, agent
sandbox storage, mounted volumes, Time Machine snapshots) — the file does not
exist anywhere.

## What survived

- The bootstrap solved-set corpora and the corpus spec
  (`docs/prompt-corpus-and-feedback-spec-v0.3.md`), restored from the safety
  tgz in the same commit that introduces this README.
- The full eight-point lessons-learned record in
  `docs/PROJECT_STATUS_2026-06-01.md` §8.
- All shadow-normalizer code on `main` — anchor regexes, polarity-inversion
  vetoes, deterministic floors, rule blocks per family. The corpus drove the
  design of these; the design is intact.
- The per-family rescue-rate scoreboard for the 5 promote-ready families is
  preserved as prose in `PROJECT_STATUS_2026-06-01.md` §1, but the row-level
  evidence behind those numbers is not reproducible from this point.

## What's lost

- The 146 specific labeled prompts in v0.1.
- The per-row scoring notes — including the convention calls on edge cases
  (`under forty` strict vs inclusive, decade-span definitions, etc.).
- The `NEEDS_HUMAN_REVIEW` flag tracking on un-decided rows.

## Path forward — rebuild as v0.2, incrementally

Rebuilding v0.1 wholesale is not the recommended approach. Instead:

1. **Treat every new cycle as an opportunity to grow v0.2.** When working a
   family (e.g., `passport_validity` for cycle 3), add ~20 rescue rows + ~3
   family-adjacent controls + ~10 solved-set rows for that family. Commit
   them in the same PR as the cycle's code patches.

2. **Re-validate the 5 already-promoted families before bumping
   `Settings.LLM_Promotion_Stage` past 0.** This is Issue #19. The doc that
   gates production promotion should include the v0.2 rescue rows for each
   family it documents.

3. **Source candidate prompts from real telemetry.** If Supabase audit tables
   captured the prompts users actually ran during the AI Search work, those
   are higher-leverage than synthesized phrasings.

4. **Document convention decisions in this README as they're made.** The
   `NEEDS_HUMAN_REVIEW` flag on individual rows works, but the convention
   decisions themselves belong here so the next loss can't take them.

## v0.2 revalidation evidence

The rebuilt v0.2 corpus is **new evidence**, not a byte-for-byte replacement for
v0.1. A family may be called promote-ready on v0.2 only after all promotion
gates have an explicit disposition:

| Gate | What the v0.2 eval provides | What must be recorded manually |
| --- | --- | --- |
| Eval gate | `scripts/tail_set_score.py` rescue rate per family, with default pass threshold `>= 0.8` | Per-family verdict and any follow-up rows |
| Confidence gate | Count of shadow-normalizer rows emitted with `confidence == "high"` vs lower confidence | Per-family confidence summary |
| Value-correctness review | Score output marks rescued rows as `value_match: NEEDS_HUMAN_REVIEW` | Accept/reject/re-test disposition for every rescued row |
| Per-family feature flag | Nothing; this is operational, not corpus evidence | Keep `Settings.LLM_Promotion_Stage` at `0` until the first three gates are recorded |

### Row-level scoring notes

After each eval run, value-review outcomes must be written back into the
canonical corpus rows, not left only in chat, terminal output, or transient
score JSON. Use a `scoring_notes` object on the row:

```json
"scoring_notes": {
  "eval_date": "2026-06-08",
  "llm_family_rescued": true,
  "llm_confidence": "high",
  "value_review": "accepted",
  "reviewer": "manual",
  "review_note": "LLM extracted max_age=49 for 'in their 40s', matching corpus convention."
}
```

Allowed `value_review` values:

- `accepted` — family and normalized values match the corpus convention.
- `rejected` — family was rescued but values are wrong or unsafe.
- `retest` — row needs another eval run because the LLM response or harness
  output was inconclusive.
- `follow_up` — row exposed a convention or implementation gap that needs a
  separate issue or code change.

Every rescued row needs a disposition before the family can be used as
promotion evidence. This matters even when the automatic eval gate passes,
because the scorer only judges family rescue automatically; value correctness
is a human review gate.

### Family verdict format

Each completed v0.2 eval should add a dated verdict summary to this README or
to a linked calibration document under `docs/`:

```text
Cycle-3 v0.2 revalidation (YYYY-MM-DD)
- eval gate: 5 / 6 families pass rescue_rate >= 0.8
- confidence gate: N high-confidence rows, M lower-confidence rows, summarized per family
- value-correctness review: K accepted, J rejected/follow_up/retest
- solved-set regressions: 0
- control violations: 0
- comparison to v0.1 preserved scoreboard:
  - age_range: v0.1 26/26, v0.2 22/22, margin unchanged
  - us_visa: v0.1 22/22, v0.2 18/22, gate passed with reduced margin
- verdict: families that remain promote-candidates on v0.2 evidence
```

If a family clears the `0.8` threshold but drops below the v0.1 preserved
scoreboard, record the reduced margin explicitly. Do not collapse that into a
plain `promote_candidate=true`; the drop may mean v0.2 is harder, the normalizer
has drifted, or both.

## Process rule (enforced by `.gitignore`)

`AI_Search_Results/` remains gitignored as a default for one-off diagnostic
output. The canonical labeled corpus is exempted via an explicit `!` rule:

```
AI_Search_Results/*
!AI_Search_Results/README.md
!AI_Search_Results/seajobs_tail_set_v*.json
```

Any new canonical artifact placed in this folder must add its own `!`
exception in `.gitignore` and be committed. **No load-bearing file in this
folder may live outside git.**
