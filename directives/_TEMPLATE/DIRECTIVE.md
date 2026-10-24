# DIRECTIVE {TAG} — {TITLE}

**Tag:** {TAG}
**Tier:** {TIER}
**Issued:** {DATE} by <Owner>
**Baseline:** <prior tag>
**Applies:** <R-#### and CR-#### by ID only; never re-quote their text>
**Evidence:** <files in ./evidence/, if any, by name; their hashes are in evidence/MANIFEST.sha256>

## 1. Objective

<One paragraph: what this checkpoint delivers.>

## 2. In scope

1. <exact work item, with the requirement it implements (document and section)>

## 3. Out of scope

- <what must not be touched>

## 4. Acceptance

- The PR gate (`handover-gate`) and the tests (`handover-tests`) pass.
- <tests that must pass; counts against STATE.md S6>

## 5. Stop conditions

- <conditions under which the session stops and reports to the Owner instead of continuing>

## 6. Session end

Follow CLAUDE.md section 3: one pull request for checkpoint `{TAG}`, complete per R-0002.

<!-- Keep this file under the directive cap (tools/policy.json). If an Owner text must be
quoted exactly, add it with: python tools/handover.py evidence-add --tag {TAG} <file>
and name the file here instead of pasting it. -->
