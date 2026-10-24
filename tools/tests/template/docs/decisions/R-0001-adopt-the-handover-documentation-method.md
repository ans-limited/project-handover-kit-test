# R-0001 — Adopt the handover documentation method

**Status:** Accepted
**Date:** 2026-10-20
**Decided by:** <Owner>
**Supersedes:** none

## Ruling

1. Project state lives only in `STATE.md`. It is rewritten at every checkpoint and never
   appended to.
2. History lives only in the append-only ledgers under `docs/history/`, written with
   `tools/handover.py ledger-append`, which rolls a ledger over when it would pass its size limit.
   One entry per checkpoint, numbered v0, v1, v2, ... with no gap.
3. Each ruling and Clarification Report has its own file under `docs/decisions/`. Once accepted or
   resolved, the file is pinned in `docs/PINS.sha256` and never edited. A change is a new
   decision that names the old one on its **Supersedes:** line.
4. Each checkpoint's directive lives at `directives/vN/DIRECTIVE.md`. Verbatim Owner texts go in
   `directives/vN/evidence/`, byte-exact, listed in its `MANIFEST.sha256`.
5. `tools/check_docs.py` checks every commit locally (pre-commit hook) and every pull request
   (the `handover-gate` job). Its limits are in `tools/policy.json`; changing a limit requires a
   new ruling.
6. The size caps in force: `STATE.md` 50 KB (warning at 30 KB), a directive 15 KB (warning at
   10 KB), a ledger entry 15 KB, a decision 20 KB, a session report 40 KB, a verification report
   20 KB, `README.md` 4 KB, an OPEN ledger 500 KB.

## Reason

A single handover file that holds both current state and full history grows without limit, and
splitting it late is costly. Separating them from day one, with a tool that enforces the limits,
avoids that.
