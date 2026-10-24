# DIRECTIVE v1 — Kit tests work in initialised repositories; verify-github

**Tag:** v1
**Tier:** 2
**Issued:** 2026-10-24 by @srahman75
**Baseline:** v0
**Applies:** R-0001, R-0002
**Evidence:** none

## 1. Objective

Correct a defect found by the first GitHub run of `handover-tests` on v0: the kit's own tests built
their fixtures by copying the live repository and running `init` on the copy, which fails once a
repository has been initialised. Let the read-only `verify-github` command (present since v0) read
through the GitHub CLI, so no token passes through the kit's code.

## 2. In scope

1. `tools/tests/template/`: a pristine copy of every file that `init` or a checkpoint changes; the
   tests build their fixture from it (`tools/tests/helpers.py`).
2. `tools/tests/test_template.py`: no drift from the kit source; a fixture built from an initialised
   repository is pristine and works.
3. The GitHub CLI transport for `verify-github` (`gh_fetch` in `tools/verify_github.py`, chosen in
   `tools/handover.py`), with a test.
4. Test fixture text updated to the generic R-0001 reason.
5. `.githooks/pre-commit` stored as executable (mode 100755): the first `handover-tests` run of
   this checkpoint on GitHub's Linux runner showed that git skips the hook when it is stored as
   100644. `init` now marks hooks executable in the index, and C11 refuses a non-executable hook.

## 3. Out of scope

- Any change outside `tools/` and the hook's file mode, other than this checkpoint's own records.
- GitHub settings; they are applied after this checkpoint.

## 4. Acceptance

- `handover-gate` and `handover-tests` pass on the pull request.
- Independent verification of the final content (`reports/VERIFICATION_v1.md`).

## 5. Stop conditions

- `handover-tests` still fails in this initialised repository.

## 6. Session end

Follow CLAUDE.md section 3: one pull request for checkpoint `v1`, complete per R-0002.
