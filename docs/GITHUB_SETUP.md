# GitHub setup — profile and verification status

**Status: CONFIGURED, NOT YET VERIFIED.** None of the protections below may be described as
enforced until the capability tests they depend on have passed for this profile and this
repository's configuration verification has passed (R-0002 item 8; design note §12).

## 1. Profile P1 (provisional)

| Item | Value |
|---|---|
| Plan | <GitHub Pro or Team; to be named by the Owner> |
| Account | <personal account or organisation; inherited organisation rules, if any> |
| Agent identity | <separate GitHub user account, or GitHub App>; never a token created by the Owner's account |
| Protection mechanism | branch ruleset on `main` (or classic branch protection; record which) |
| Review policy | Owner approval required for every PR (routine PRs included, until G1 validates otherwise) |
| Merge authority | the agent may squash-merge once required checks and approvals have passed |
| Tags | `v*` tag ruleset; signed tags deferred |

## 2. Settings to apply on `main`

- Require a pull request before merging; required approvals: **1** (profile P1).
- Require review from Code Owners (`.github/CODEOWNERS` is generated from `tools/policy.json`).
- Dismiss stale approvals (GitHub dismisses them when the PR's diff changes).
- Require approval of the most recent reviewable push.
- Required status checks: `handover-gate` and `handover-tests`, each with the GitHub Actions app as
  the expected source. Require branches to be up to date before merging.
- Require linear history. Allow squash merging only. Block force-pushes and deletion. No bypass
  for anyone, administrators included.
- Tag ruleset for `v*`: only the Owner may create, update or delete.

**Known dependence on the Owner's review.** A pull request that changes a workflow file runs with
its own changed workflow, and a gate job skipped by a condition counts as passing. For any change
under `.github/**`, the Owner's Code Owner review is the real guard, not CI.

**Bootstrap window.** Between the first push of the root commit and applying these settings, the
branch is unprotected. Apply them immediately after the first push.

## 3. Capability tests (design note §12.1) — profile P1

| # | Test | Status | Result and evidence | Fallback used | Guarantee lost |
|---|---|---|---|---|---|
| G1 | Routine PR merge with the chosen approval settings | NOT RUN | — | — | — |
| G2 | Checkpoint PR blocked without Owner approval | NOT RUN | — | — | — |
| G3 | Approval dismissed by a diff-changing push | NOT RUN | — | — | — |
| G4 | Fake `handover-gate` status ignored | NOT RUN | — | — | — |
| G5 | Second workflow with a `handover-gate` job | NOT RUN | — | — | — |
| G6a | Base checker rejects an `if:` on the gate (local) | PASSED LOCALLY | `test_pr.GateIntegrity.test_condition_on_gate_fails_when_checked_directly`, full suite run 2026-10-24 | — | — |
| G6b | PR adding `if: false` to the gate blocked without Owner approval | NOT RUN | — | — | — |
| G7 | Agent direct push / force-push refused | NOT RUN | — | — | — |
| G8 | Owner direct push / force-push refused | NOT RUN | — | — | — |
| G9 | Squash merge only | NOT RUN | — | — | — |
| G10 | Agent cannot create or move `v*` tags | NOT RUN | — | — | — |
| G11 | Agent cannot dismiss the Owner's review | NOT RUN | — | — | — |
| G12 | Out-of-date PR blocked | NOT RUN | — | — | — |
| G13 | Reviews API returns state and commit per review | NOT RUN | — | — | — |
| G14 | Approval after an empty commit and a same-content rewrite | NOT RUN | — | — | — |
| G15 | Approve / request changes / dismiss / merge sequences | NOT RUN | — | — | — |

## 4. Configuration verification (design note §12.2) — this repository

**Not built yet.** `python tools/handover.py verify-github` is specified but deliberately not
implemented in the prototype: which API fields hold these settings, and whether reading them needs
admin rights, are unknown until the capability tests run. Until it exists, the Owner checks §2 by
hand and records the date and result here.

| Date | By | Result |
|---|---|---|
| — | — | NOT DONE |

## 5. Retest triggers (design note §12.3)

Repeat the capability tests when any of these changes: the plan; the account type or organisation;
inherited organisation rules; ruleset or classic protection; any setting in §2; the agent's
identity type or permissions; the gate workflow's job name or structure; or a GitHub change to
these features. Repeat the configuration verification after any settings change.

## 6. Workflow behaviour still to confirm on GitHub

The workflows were corrected after the independent review of 2026-10-24 (findings R4–R6). Their
shell lines run in the kit's tests against local repositories; none has run on GitHub yet.

| Item | What the kit's tests show | Still to confirm on GitHub |
|---|---|---|
| Audit: trusted checker from the parent; root commit only exception; no fallback | Executed: root, non-root, and non-root whose parent has no checker (fails) | The same on a runner |
| Audit: `gh api --paginate --slurp`; checker reads pages | Page forms parsed; inline PR-number selection run on slurped pages | Real API output for commits/pulls and reviews (G13) |
| Audit: authenticated fetch of `refs/pull/<number>/head` | Exact lines executed against a local bare remote; token not printed | Authentication against a private repository |
| Tags: no separate fetch; `origin/master` from checkout's full fetch | Static check only | That checkout with `fetch-depth: 0` brings `origin/master` on a tag push |
