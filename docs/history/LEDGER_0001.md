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
