# NjordHR Module Rollback Playbook

This playbook defines the default rollback posture for the module backlog in
[NjordHR_Implementation_Modules_and_Task_Backlog.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md).

Use it as the first rollback reference for any module-level change. If a more
specific rollback playbook exists for a sub-feature, follow the sub-feature
playbook first, then return here for module-level containment.

## Core rollback rules

- Protect user-visible behavior first.
- Prefer disabling the newest behavior behind a flag before reverting older
  stable paths.
- Keep rollback steps deterministic and documented.
- Preserve observability so the rollback can be validated immediately.
- If a rollback increases local-only behavior temporarily, that is acceptable.
  If it risks data loss or unsupported writes, stop and widen the rollback.

## M0 - Program setup and guardrails

Rollback target:
- revert spec / template / checklist edits that changed rollout governance
- keep existing code paths unchanged

Use when:
- a guardrail doc creates contradictory scope
- a PR template or checklist blocks legitimate safe work
- feature-flag documentation is inaccurate

Verification:
- specs still agree on scope boundaries
- PR template remains usable
- no production code path changed

## M1 - Cloud foundation

Rollback target:
- disable new cloud API endpoints
- remove or disable Supabase-backed schema changes for the touched slice
- preserve existing local behavior

Use when:
- auth, RLS, or cloud API scaffolding is unstable
- schema changes break startup or health checks

Verification:
- local behavior still works
- cloud health checks stop failing
- no unsupported writes are left enabled

## M2 - Data layer migration

Rollback target:
- turn off dual-write
- revert reads to the last stable repository path
- keep migration scripts isolated from runtime

Use when:
- CSV/Supabase parity diverges
- idempotency or migration scripts are not safe
- read-path switch introduces missing data or stale data

Verification:
- current read path returns to the stable source
- dual-write no longer mutates the new backend
- parity checks are preserved for later rerun

## M3 - Local Agent

Rollback target:
- route session/download actions back to the existing local process
- disable agent-only job queue/sync paths
- keep local folder writes functioning

Use when:
- agent startup fails
- local downloads become unreliable
- sync retry / reconnect behavior is unstable

Verification:
- download-to-folder still works
- cloud sync paths are inert or disabled
- no cloud service writes to the local filesystem

## M4 - Frontend integration

Rollback target:
- restore the previous API routing/configuration
- disable agent-aware UI paths if they confuse or block users
- keep the core dashboard usable

Use when:
- environment routing is wrong
- mode indicators are misleading
- offline / disconnected states are confusing or incorrect

Verification:
- UI routes to the last known-good API target
- user workflow remains usable
- no hidden behavior changes are introduced

## M5 - Installer + auto-update + signing

Rollback target:
- disable auto-update
- revert to the last signed installer version
- keep manual install available

Use when:
- signature verification fails
- update application is unstable
- installer packaging breaks launch or service registration

Verification:
- the previous signed build still installs and runs
- update checks do not brick the agent
- rollback build is clearly versioned

## M6 - Cutover, hardening, and deprecation

Rollback target:
- re-enable the legacy CSV/SQLite path if the cutover is unsafe
- restore any deprecated write path that is still needed for operation
- keep the newest centralized storage path optional until proven

Use when:
- migration completeness is not proven
- hardening introduces regressions
- deprecation creates operational risk

Verification:
- legacy operation is restored if needed
- data access remains available
- rollback is recorded before the next rollout attempt

## Minimum rollback validation

After any rollback:
- rerun the relevant targeted tests
- rerun the relevant smoke cases
- re-open the baseline artifact or prompt corpus if the change touched evaluation
- document the exact rollback reason and the path disabled or restored

## Related references

- [NjordHR_Implementation_Modules_and_Task_Backlog.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Implementation_Modules_and_Task_Backlog.md)
- [NjordHR_Online_Hybrid_Architecture_Spec.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Online_Hybrid_Architecture_Spec.md)
- [candidate-intelligence-architecture-v3.4.md](/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md)
