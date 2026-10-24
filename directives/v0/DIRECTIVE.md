# DIRECTIVE v0 — Initialise the repository with the handover method

**Tag:** v0
**Tier:** 1
**Issued:** 2026-10-20 by @srahman75
**Baseline:** none (root commit)
**Applies:** R-0001, R-0002
**Evidence:** none

## 1. Objective

Create the repository with the documentation skeleton and its tools, so that the root commit
already passes the bootstrap check.

## 2. In scope

1. `STATE.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `.gitattributes`, `.gitignore`.
2. `docs/decisions/` with R-0001, R-0002 and the templates; `docs/history/` with LEDGER_0001 and
   its register; `docs/design/` with its template; `docs/PINS.sha256`; `docs/GITHUB_SETUP.md`.
3. `tools/check_docs.py`, `tools/handover.py`, `tools/policy.json` and their tests.
4. `.github/workflows/`, `.github/CODEOWNERS` and `.githooks/pre-commit`.

## 3. Out of scope

- Any application code.

## 4. Acceptance

- `python tools/check_docs.py --bootstrap` reports RESULT PASS before the root commit.
- `python -m unittest discover -s tools/tests` passes.

## 5. Stop conditions

- The bootstrap check or the tests fail and the cause is not a mechanical defect in this
  directive's files.

## 6. Session end

Commit everything as the root commit, push `main` once, apply `docs/GITHUB_SETUP.md`, and let
the Owner tag the root commit `v0` (design note s9).
