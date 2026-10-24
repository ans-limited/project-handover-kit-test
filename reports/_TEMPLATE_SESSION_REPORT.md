# SESSION_REPORT_<tag>.md — session report

**Tag:** <tag>
**Date:** <YYYY-MM-DD>
**Directive executed:** directives/<tag>/DIRECTIVE.md
**Baseline tag (state before this session):** <prior tag>

## Claims

One row per file staged in the commit, except this report. Every row needs a path, the nature
of the change and a check (SHA-256 and bytes for a created or replaced file; a line range for an
edit). A row without all three cannot be verified.

| # | Path | Change | Check |
|---|---|---|---|
| 1 | STATE.md | S2, S7, S9 and banner rewritten | lines <a>-<b> |
| 2 | docs/history/LEDGER_####.md | entry <tag> appended | lines <a>-<b> |

## Test / regression status

<Suite counts against STATE.md S6, or "documentation-only; no test run applies".>

## Documentation gate

<The final RESULT line of python tools/check_docs.py, and its SIZE line for STATE.md.>

## Explicitly NOT done / deferred

## Deviations from the directive

None.
