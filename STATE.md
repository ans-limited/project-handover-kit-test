# STATE.md — Handover Kit Test

CURRENT BASELINE: tag v0 (2026-10-20) — Repository initialised with the handover method

This file says what is true now and what to do next. It is rewritten at every checkpoint and
never appended to. History is in the ledgers, rulings are in docs/decisions/. Limits are enforced
by tools/check_docs.py.

## S1. Reading rules

- **Session start:** read this file in full, then the directive for this session
  (`directives/vN/DIRECTIVE.md`). Run the checker (see [CLAUDE.md](CLAUDE.md) section 2).
- **Everything else is read by pointer:** decision files, design files, ledger sections, evidence.
- If this file and any other document disagree on current state, this file wins. If this file and
  a decision file disagree on what a ruling says, the decision file wins.

## S2. Current state

- **Checkpoint v0:** the repository holds the documentation skeleton and its tools only. No
  application code yet. Record: [LEDGER_0001](docs/history/LEDGER_0001.md), entry v0.
- **Method in force:** R-0001 (documentation) and R-0002 (checkpoints as pull requests).
- **GitHub protection:** configured as described in [GITHUB_SETUP](docs/GITHUB_SETUP.md), not yet
  verified.

## S3. Governing baseline and mandate

- **Source documents:** <name the controlling requirements documents and where they live, read-only>.
- **Mandate:** <one or two lines: what the programme is to build, and who decides>.
- **Owner:** @srahman75. Directives are issued by the Owner; each names its checkpoint vN.

## S4. Standing rules

Pointers only; the decision file governs.

- Documentation method, size caps and ledger: R-0001.
- Checkpoints, tiers, approval and merging: R-0002.
- <add one line per standing engineering rule, each pointing to its decision file>

## S5. Decisions in force

Only decisions in force are listed. A superseded decision is named on the **Supersedes:** line
of the one that replaced it.

| ID | Title | Status | File |
|---|---|---|---|
| R-0001 | Adopt the handover documentation method | Accepted | [R-0001](docs/decisions/R-0001-adopt-the-handover-documentation-method.md) |
| R-0002 | Checkpoints as pull requests, in three tiers | Accepted | [R-0002](docs/decisions/R-0002-checkpoints-as-pull-requests.md) |

## S6. Verification baseline

- **Documentation gate:** `python tools/check_docs.py` → RESULT PASS.
- **Test suite:** the kit's tool tests only. <When the application exists: counts of record,
  environment, and which ledger entry signed them.>

## S7. Open items

Format: `- OI-#### [Open|Blocked|In progress] text — pointer`. Closed items are deleted here
and recorded in the ledger.

- OI-0001 [Open] Fill in S3 (source documents, mandate, Owner) — directive v1
- OI-0002 [Open] Core-domain boundary by dependency analysis — directive v1, R-0002 item 7
- OI-0003 [Blocked] GitHub capability tests and configuration verification — docs/GITHUB_SETUP.md

## S8. Accepted limitations

None.

## S9. Next step

Await the Owner's directive v1. Nothing beyond v0 is authorized.

