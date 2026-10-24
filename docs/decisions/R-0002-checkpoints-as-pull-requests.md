# R-0002 — Checkpoints as pull requests, in three tiers

**Status:** Accepted
**Date:** 2026-10-20
**Decided by:** @srahman75
**Supersedes:** none

## Ruling

1. `main` changes only by squash-merged pull requests. A **routine PR** (Tier 0) adds no
   directive. A **checkpoint PR** adds `directives/vN/DIRECTIVE.md`, where N is one more than the
   last ledger entry on its base, and is complete: the directive with its Tier line, the
   `STATE.md` banner naming `vN`, exactly one ledger entry `vN`, `reports/SESSION_REPORT_vN.md`,
   and for Tier 2 `reports/VERIFICATION_vN.md`.
2. **Tiers.** Tier 1 is a work package verified by its own session and CI. Tier 2 is required for
   the gate, protocol and template files, the core-domain paths, accepting a ruling or resolving a
   CR; it adds independent verification bound to the PR's final content. The Owner may raise a
   tier, never lower it.
3. **Prohibitions** apply before any tier: closed records, accepted decisions, existing pin lines
   and existing ledger bytes never change; no other new files under `directives/` or `reports/`.
4. **Approval.** An approval exists only as the Owner's review on GitHub; a file saying the Owner
   accepted is a note, not an approval. Checkpoint PRs need the Owner's approval. Routine PRs also
   need it until live test G1 validates merging them on CI alone.
5. **Merging.** The agent may merge a PR once its required checks and approvals have passed.
6. **Tags** label merged checkpoints and decide nothing. Signed tags are deferred.
7. **Core domain.** The boundary is decided at checkpoint v1 by the dependency analysis of the
   design note (section 3.5). Until then only the gate, protocol and template paths are Tier 2.
8. **Enforcement claims.** No GitHub-side rule is described as enforced until the capability tests
   it depends on and the repository's configuration verification have passed
   (`docs/GITHUB_SETUP.md`).

## Reason

Pull requests give each checkpoint a reviewable boundary and bind the Owner's approval to exact
content; tiers keep routine work light and reserve independent verification for changes to the
project's critical rules and to the gate itself.
