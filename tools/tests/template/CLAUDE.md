# CLAUDE.md — <PROJECT NAME> (session protocol)

This file is the operating protocol only. It holds no project state: the current baseline,
rulings, open items and next step are in [STATE.md](STATE.md). If this file and STATE.md
disagree about the project, STATE.md wins. The method is ruled by R-0001 and R-0002.

## 1. Where things live

| What | Where | Rule |
|---|---|---|
| Current state, next step | `STATE.md` | Rewritten in each checkpoint PR; never appended to |
| Rulings, Clarification Reports | `docs/decisions/R-####-*.md`, `CR-####-*.md` | One per file; pinned and immutable once accepted |
| History and evidence of record | `docs/history/LEDGER_####.md` | One entry per checkpoint; only `tools/handover.py ledger-append` writes it |
| This checkpoint's instructions | `directives/vN/DIRECTIVE.md` | One per checkpoint, with its Tier line; cites decisions by ID |
| The Owner's exact words | `directives/vN/evidence/` | Byte-exact files listed in `MANIFEST.sha256` |
| Designs | `docs/design/<topic>.md` | Edited in place; revisions live in git |
| Session and verification reports | `reports/SESSION_REPORT_vN.md`, `reports/VERIFICATION_vN.md` | From the templates |

Never write hashes, uuids, transcript record numbers, byte counts or verbatim quotes into
`STATE.md`. They belong in the ledger entry or an evidence file. The checker rejects them.

**An approval exists only as the Owner's review on GitHub.** A file saying "the Owner accepted"
is a note, never an approval. Never write one as if it were.

## 2. Session start

1. Read `STATE.md` in full, and the directive for this session in full. Read anything else only by
   pointer, when the work needs it.
2. On an up-to-date `master`, run `python tools/check_docs.py`. Anything other than `RESULT PASS`
   is a STOP: report it to the Owner. Never repair the checker's inputs by hand to make it pass.
3. If no directive names this session's checkpoint, the session may do routine work only (Tier 0,
   section 4). Otherwise it works on that checkpoint.
4. Never commit on `master`. Create a branch: `checkpoint/vN` or `routine/<topic>`.

## 3. A checkpoint (Tier 1 or 2)

1. On the branch, create the directive with `python tools/handover.py new-directive --tag vN
   --tier <1|2> --title "..."` (or commit the Owner's directive as given). `python
   tools/handover.py next` prints N.
2. Do the work. Commit as often as useful; the pre-commit hook blocks only real damage and prints
   advice for anything that will matter at the PR.
3. Before opening the PR, complete the checkpoint (R-0002 item 1):
   - rewrite `STATE.md`: banner `vN` with today's date, S2, S7 (delete closed items), S9;
   - write the history record to a scratch file and run `python tools/handover.py ledger-append
     --tag vN --title "<one line>" --record <file>`;
   - write `reports/SESSION_REPORT_vN.md` from its template.
4. **Tier 2 only:** stop and ask the Owner for independent verification. The verifier adds
   `reports/VERIFICATION_vN.md` in a commit of its own. After that, change nothing else.
5. Open the PR into `master`. The Owner approves on GitHub. Once `handover-gate`, `handover-tests`
   and the approval have passed, the agent may squash-merge. If the PR falls behind `master`,
   update it; that cancels the approval and, for Tier 2, the verification.

## 4. Routine work (Tier 0)

Application code, tests and `README.md`, outside every protected path, with no directive and no
change to `STATE.md`, ledgers, reports or decisions. Open a PR; until live test G1 validates
otherwise, the Owner approves routine PRs too (R-0002 item 4).

## 5. Decisions

- Mechanical bugs are fixed with a regression test and disclosed in the session report.
- Design or scope questions go to the Owner as a CR (`python tools/handover.py new-decision --kind
  CR --title "..."`) inside a checkpoint PR. Only the affected area pauses.
- Accepted rulings are settled. A change is a new ruling naming the old one on its **Supersedes:**
  line, in a Tier 2 checkpoint; the old file is never edited.

## 6. Limits

Every limit is in `tools/policy.json` and enforced by `tools/check_docs.py`. When a file nears its
cap, move detail to its proper home (ledger, decision, design or evidence). A limit changes only
through a ruling and a Tier 2 checkpoint.
