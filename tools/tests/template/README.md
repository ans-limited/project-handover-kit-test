# <PROJECT NAME>

<One paragraph: what this system is.>

**Start here:** [STATE.md](STATE.md) is the current state and next step. Session rules are in
[CLAUDE.md](CLAUDE.md). This README deliberately repeats no project state.

## Layout

- `STATE.md` — what is true now (rewritten in each checkpoint)
- `docs/decisions/` — rulings and Clarification Reports, one per file
- `docs/history/` — append-only history ledgers
- `docs/design/` — one design file per topic
- `docs/GITHUB_SETUP.md` — the GitHub protection this repository relies on, and its test status
- `directives/vN/` — each checkpoint's directive and its evidence
- `reports/` — session and verification reports
- `tools/` — `check_docs.py` (the gate), `handover.py` (the writing helpers), `policy.json`

## Checks

```bash
python tools/check_docs.py
```

```bash
python -m unittest discover -s tools/tests
```

The tests need Python 3.8+, git 2.15+, full git history (not a shallow clone), and a POSIX `sh`
with `base64`, `tr` and `sed`. On Windows these come with Git for Windows (its `usr\bin` folder);
the tests locate it through git.

GitHub-side protection is **configured, not yet verified** until the tests in
`docs/GITHUB_SETUP.md` pass.
