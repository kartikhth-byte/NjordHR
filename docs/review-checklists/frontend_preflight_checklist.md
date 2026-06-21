# Frontend Preflight Checklist

Use this checklist before calling a frontend change "done" and before requesting review.

This is especially important when the change touches:
- `frontend.html`
- inline JavaScript
- multiple `<script>` blocks
- globals on `window`
- IIFEs
- mixed module / non-module code
- React mounted from inline Babel or similar non-bundled setups

## 1. File-Structure Check

- Identify whether the file has multiple script/module scopes.
- Search for script boundaries:

```bash
rg -n '<script|type="module"|type="text/babel"' frontend.html
```

- If a symbol is declared in one block and used in another, verify that it is intentionally exposed.
- Do not assume "same file" means "same scope."

## 2. Declaration / Usage Check

For every newly added constant, helper, or shared state object:

- Find the declaration site.
- Find all usage sites.
- Confirm they are in the same lexical/module scope, or intentionally exported.
- If shared across boundaries, prefer one of:
  - move the symbol next to usage
  - expose it explicitly on a shared object
  - import/export it through the normal module path

## 3. Runtime Boot Check

Run the page once after frontend edits.

- Load the page.
- Confirm the app mounts.
- Check the browser console for startup/runtime errors.
- Treat "page is blank" as a failing test even if unit tests pass.

Minimum smoke:
- page loads
- React/Vue/etc. mounts
- no immediate console exceptions

## 4. Interaction Smoke Check

If the change affects a specific control or flow:

- Click it once
- change it once
- save/submit once if relevant
- confirm the expected payload/UI state changes

Examples:
- new filter row
- modal open/close
- dropdown population
- recovery/restore flow
- tab switch

## 5. Diff-Review Guardrail

Before asking for review, check whether the bug could hide outside the diff hunk:

- scope boundaries
- file-level initialization order
- duplicated helper names
- stale globals
- effects/hooks depending on old state names
- DOM elements rendered in one branch but referenced in another

If yes, inspect surrounding file context, not just the changed lines.

## 6. Reviewer Handoff Notes

When requesting review, explicitly say if the change needs:

- runtime-scope validation
- console-error check
- page boot smoke test
- cross-script/module boundary review

Suggested line to include:

> This review must include runtime-scope validation, not just diff review. Verify symbol visibility across script/module boundaries and do one browser boot smoke test with a console check.

## 7. When This Checklist Is Mandatory

Treat this checklist as mandatory when:

- a frontend file mixes HTML and JS in one file
- the app uses inline scripts
- the app relies on globals or `window.*`
- the change introduces new shared constants/helpers
- the change touches recovery/boot/init code
- the change refactors state wiring without changing backend behavior much

## 8. Post-Review Sanity Pass

If review comes back "looks good":

- rerun the boot smoke yourself once more
- scan for scope assumptions one last time
- only then commit

The lesson: unit tests and diff review are necessary, but neither replaces a runtime boot check for frontend work.
