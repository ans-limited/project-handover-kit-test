# LEDGER_0001 — Handover Kit Test history ledger

Predecessor: none

Append-only. One section per checkpoint, written by tools/handover.py ledger-append. Read by pointer
(file and section heading), never in full at session start.

## v0 — 2026-10-20 — Repository initialised with the handover method

**Directive:** directives/v0/DIRECTIVE.md. **Baseline:** none (first commit).

### What was done
- Created the documentation skeleton and tools under R-0001 (the handover method).
- Pinned R-0001 in docs/PINS.sha256.

### State of the gate
- `python tools/check_docs.py`: RESULT PASS at this checkpoint.

### Items opened
- OI-0001: fill in STATE.md S3 (source documents, mandate, Owner).

## v1 — 2026-10-24 — Kit tests work in initialised repositories; verify-github

**Directive:** directives/v1/DIRECTIVE.md (Tier 2). **Baseline:** v0.

### What was done
- The kit's tests now build their fixture from `tools/tests/template/`, a pristine copy of the files
  that `init` and checkpoints change. Before this, the first GitHub run of `handover-tests` on v0
  failed: the tests copied this initialised repository and ran `init` on the copy.
- Added `tools/tests/test_template.py` checks: no drift from the kit source; a fixture built from an
  initialised repository works.
- The read-only `verify-github` command (present since v0) now reads through the GitHub CLI when
  gh is signed in and neither GH_TOKEN nor GITHUB_TOKEN is set; on that path no token passes
  through the kit's code. With either variable set, the kit's own HTTP code sends it; with a test.
- The pre-commit hook is now stored as executable. This checkpoint's first `handover-tests` run on
  GitHub (Linux) failed 7 hook tests because git skipped the hook stored as 100644; on Windows git
  ignores the bit, so local runs had passed. `init` marks hooks executable; C11 refuses one that is not.

### Verification
- Independent verification of the final content: `reports/VERIFICATION_v1.md`.
- Owner approval: GitHub review only (not recorded here).
