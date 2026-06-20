# Reviewer Prompt Template: Frontend Runtime / Scope Checks

Use this block in review requests when a change touches frontend code, especially inline JS, `frontend.html`, globals, IIFEs, or mixed script/module boundaries.

---

Please review the current frontend change with **runtime-scope validation**, not just diff review.

Review requirements:

1. **Scope / boundary check**
   - If the frontend uses inline scripts, multiple `<script>` blocks, globals, IIFEs, `window.*`, or mixed module / non-module code, verify that declarations and usages are in the same scope or intentionally exported.
   - Do not assume that a constant/helper declared elsewhere in the same file is visible at the usage site.
   - Inspect surrounding file structure, not just diff hunks.

2. **Startup / boot safety**
   - Check whether the page can fail during initial render or mount.
   - Call out any change that could cause a blank page, startup exception, missing global, or initialization-order bug.

3. **Console / runtime expectation**
   - Require one browser boot smoke test and console check when the change affects frontend wiring, shared helpers, recovery state, or initialization.
   - If no runtime smoke was performed, treat that as a review gap and say so explicitly.

4. **State / wiring checks**
   - Verify renamed state, helpers, payload fields, and effects are consistently wired through the UI flow.
   - Look for half-migrations where old names still exist beside new ones.

5. **Findings format**
   - List findings by severity.
   - Include file/line references.
   - Call out whether the issue is:
     - diff-visible
     - surrounding-context-visible
     - runtime-only

Please specifically look for bugs in these classes:
- cross-script scope errors
- globals not exported to the consumer scope
- startup render failures
- stale helper/state references
- initialization-order bugs
- recovery/restore state mismatches
- UI wiring that passes tests but fails at runtime

Suggested review language:

> This frontend change needs file-structure and runtime review, not just logic review. Verify script/module boundaries, symbol visibility, and page boot behavior.

---

## Optional Add-On for the Review Request

Paste this when you want to force a stronger review standard:

> For `frontend.html` or similar hybrid files, please inspect the declaration site and usage site in full context. A same-file declaration is not sufficient evidence of runtime visibility.

## When To Use This Template

Use this template when a change touches:
- `frontend.html`
- inline JavaScript
- React in inline Babel
- globals on `window`
- IIFEs
- mixed script boundaries
- startup/bootstrap/recovery code
- UI state rewiring
