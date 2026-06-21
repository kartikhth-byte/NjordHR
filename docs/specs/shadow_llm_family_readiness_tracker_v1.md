# Shadow LLM Family Readiness Tracker v1

## Goal

Track each deterministic hard-filter family across three buckets:

- `shadow_prompt_ready`
- `shadow_tests_ready`
- `llm_revalidation_pending`

This keeps the working rhythm explicit:

- **code change now**
- **LLM evidence later, in batches**

The tracker is intentionally operational rather than aspirational. A family may
be implemented deterministically and still be only `partial` for shadow prompt
coverage if the prompt instructions are thin or indirect.

## Status meanings

- `yes` — ready and intentionally covered
- `partial` — some shadow support exists, but not at the desired final quality
- `no` — not ready / not covered yet

## Required workflow for new or changed filter families

When a PR adds or materially changes a deterministic hard-filter family:

1. update `query_understanding/shadow_llm_provider.py` prompt guidance or
   document why the family remains `partial`
2. add or extend direct shadow translation tests in
   `/Users/kartikraghavan/Tools/NjordHR/tests/test_query_understanding_shadow_llm_provider.py`
3. mark the family as `llm_revalidation_pending = yes`
4. defer the real Gemini-backed evidence run to the next broader shadow /
   normalization / revalidation batch unless the change specifically needs an
   isolated LLM smoke check

## Current family tracker

| Family | Owning spec / context | shadow_prompt_ready | shadow_tests_ready | llm_revalidation_pending | Notes |
|---|---|---:|---:|---:|---|
| `engine_experience` | `engine_experience_layers_v1.md`, `experience_filters_v1.md` | yes | yes | yes | Explicit prompt rules/examples added; bootstrap corpus rows and fallback tests are in place. Include in the next broader shadow revalidation pass. |
| `vessel_tonnage` | `vessel_tonnage_v1.md`, `experience_filters_v1.md` | yes | yes | yes | Prompt rules/examples and direct shadow translation tests now exist. Keep included in the next broader LLM revalidation batch. |
| `experience_ship_type` | `experience_filters_v1.md` | yes | yes | yes | Prompt rules/examples and direct shadow tests now exist. One shadow translation path still emits the current legacy scalar shape, so future shape-promotion work should update the tracker note rather than regress coverage. |
| `coc_document_gate` | `search_pickers_v1.md` | yes | yes | yes | Dedicated prompt-rule coverage now exists alongside compound and translation tests. |
| `coc_country_match` | `search_pickers_v1.md` | yes | yes | yes | Prompt rules/examples and direct shadow translation coverage now exist. Keep on the pending list for the next broader family revalidation pass. |

## Current policy decision

For the current implementation cycle:

- do the **deterministic and shadow-contract code work now**
- do **not** run an isolated Gemini evidence pass for every family addition
- batch the real LLM evidence run into the next broader normalization /
  revalidation cycle

That broader pass should explicitly include:

- `engine_experience`
- `vessel_tonnage`
- `experience_ship_type`
- `coc_document_gate`
- `coc_country_match`

## Update discipline

Whenever one of the families above changes, update this table in the same PR.
The goal is to avoid relying on chat history or memory to know whether a family
is shadow-ready or merely deterministic-ready.
