"""Tests for tools/handover.py: every write it makes leaves the repository passing check_docs.py.

Carried over from the starter kit's suite. Changes, each because a rule changed in R-0001/R-0002:
- rollover tests use a small but valid policy (ledger_max_bytes must cover the 15 KB entry cap);
- the old "oversized single record gets its own ledger" test is replaced: an entry over the cap is
  now refused (design note s6.2);
- ledger-append and new-directive accept only the next checkpoint number;
- init needs a git work tree before the root commit, and the owner is a GitHub user name;
- new-directive takes --tier.
"""
import os
import subprocess
import unittest

from helpers import RepoTestCase, check_docs

SMALL_LEDGER = dict(ledger_max_bytes=2200, ledger_entry_max_bytes=600, ledger_header_max_bytes=1000)


class LedgerAppend(RepoTestCase):
    def append(self, tag, body='Record body.\n'):
        code, out = self.tool('ledger-append', '--tag', tag, '--date', '2026-10-21',
                              '--title', f'Checkpoint {tag}', '--record', self.record(body))
        self.assertEqual(code, 0, out)
        self.set_banner_tag(tag)
        return out

    def test_append_then_pass(self):
        self.append('v1')
        self.assertPasses(expect_tag='v1')
        self.assertIn('## v1 — 2026-10-21 — Checkpoint v1', self.read('docs/history/LEDGER_0001.md'))

    def test_rollover_closes_pins_and_continues(self):
        self.set_config(**SMALL_LEDGER)
        body = 'line of history detail\n' * 20
        out = ''
        for t in ('v1', 'v2', 'v3', 'v4', 'v5'):
            out += self.append(t, body)
            self.assertPasses(expect_tag=t)
        self.assertIn('rolled over', out)
        rows, errors = check_docs.parse_ledger_register(self.path('docs/history/LEDGERS.md'))
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual([r['status'] for r in rows], ['CLOSED'] * (len(rows) - 1) + ['OPEN'])
        first = self.read('docs/history/LEDGER_0001.md')
        self.assertIn('## CLOSED — ', first)
        self.assertIn('Successor: LEDGER_0002.md', first)
        self.assertIn('Predecessor: LEDGER_0001.md', self.read('docs/history/LEDGER_0002.md'))
        self.assertIn('docs/history/LEDGER_0001.md', self.read('docs/PINS.sha256'))
        for r in rows:
            n = os.path.getsize(self.path('docs/history/' + r['file']))
            self.assertLessEqual(n, SMALL_LEDGER['ledger_max_bytes'], f'{r["file"]} is {n} B')

    def test_entry_over_cap_refused(self):
        self.set_config(**SMALL_LEDGER)
        code, out = self.tool('ledger-append', '--tag', 'v1', '--title', 't', '--record', self.record('x' * 2000 + '\n'))
        self.assertEqual(code, 1)
        self.assertIn('over the cap', out)

    def test_closed_ledger_cannot_be_edited(self):
        self.set_config(**SMALL_LEDGER)
        for t in ('v1', 'v2', 'v3', 'v4'):
            self.append(t, 'line of history detail\n' * 20)
        self.assertEqual(check_docs.parse_ledger_register(self.path('docs/history/LEDGERS.md'))[0][0]['status'], 'CLOSED')
        with open(self.path('docs/history/LEDGER_0001.md'), 'a', encoding='utf-8', newline='\n') as f:
            f.write('late addition\n')
        self.assertFails('C7', expect_tag='v4')

    def test_duplicate_tag_refused(self):
        code, out = self.tool('ledger-append', '--tag', 'v0', '--title', 't', '--record', self.record())
        self.assertEqual(code, 1)
        self.assertIn('the next checkpoint is v1', out)

    def test_record_with_level2_heading_refused(self):
        code, out = self.tool('ledger-append', '--tag', 'v1', '--title', 't', '--record', self.record('## Bad\n'))
        self.assertEqual(code, 1)

    def test_bad_tag_refused(self):
        code, _ = self.tool('ledger-append', '--tag', 'v 1', '--title', 't', '--record', self.record())
        self.assertEqual(code, 1)

    def test_skipped_number_refused(self):
        code, out = self.tool('ledger-append', '--tag', 'v2', '--title', 't', '--record', self.record())
        self.assertEqual(code, 1)
        self.assertIn('the next checkpoint is v1', out)


class Pin(RepoTestCase):
    def test_pin_is_idempotent(self):
        rel = 'docs/decisions/R-0001-adopt-the-handover-documentation-method.md'
        code, out = self.tool('pin', rel)
        self.assertEqual(code, 0)
        self.assertIn('already pinned', out)

    def test_pin_refuses_changed_file(self):
        rel = 'docs/decisions/R-0001-adopt-the-handover-documentation-method.md'
        self.replace(rel, 'The previous project', 'The earlier project')
        code, out = self.tool('pin', rel)
        self.assertEqual(code, 1)
        self.assertIn('immutable', out)

    def test_pin_refuses_outside_repo(self):
        code, _ = self.tool('pin', self.record())
        self.assertEqual(code, 1)

    def test_pin_refuses_targets_that_are_never_pinned(self):
        """Review finding R2: only a CLOSED ledger or an Accepted/Resolved decision may be pinned."""
        self.tool('new-decision', '--kind', 'R', '--title', 'Still proposed')
        cases = {
            'docs/history/LEDGER_0001.md': 'OPEN ledger',
            'docs/decisions/R-0003-still-proposed.md': 'is Proposed',
            'docs/decisions/_TEMPLATE_RULING.md': 'not a kind of file',
            'README.md': 'not a kind of file',
        }
        before = self.read('docs/PINS.sha256')
        for relp, why in cases.items():
            with self.subTest(target=relp):
                code, out = self.tool('pin', relp)
                self.assertEqual(code, 1, out)
                self.assertIn(why, out)
        self.assertEqual(self.read('docs/PINS.sha256'), before)


class NewDecision(RepoTestCase):
    def test_lifecycle(self):
        code, out = self.tool('new-decision', '--kind', 'R', '--title', 'Use exact decimal arithmetic')
        self.assertEqual(code, 0, out)
        rel = 'docs/decisions/R-0003-use-exact-decimal-arithmetic.md'
        self.assertTrue(os.path.isfile(self.path(rel)))
        self.assertFails('C5')  # orphan until listed in S5
        self.replace('STATE.md', '| R-0001 | Adopt',
                     '| R-0003 | Use exact decimal arithmetic | Proposed | [R-0003](docs/decisions/R-0003-use-exact-decimal-arithmetic.md) |\n'
                     '| R-0001 | Adopt')
        self.assertPasses()
        self.replace(rel, '**Status:** Proposed', '**Status:** Accepted')
        self.assertFails('C5')  # accepted but not pinned
        self.assertEqual(self.tool('pin', rel)[0], 0)
        self.assertPasses()

    def test_cr_numbering_is_separate(self):
        code, out = self.tool('new-decision', '--kind', 'CR', '--title', 'Which rounding rule?')
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(self.path('docs/decisions/CR-0001-which-rounding-rule.md')))


class Init(RepoTestCase):
    def git_init(self):
        subprocess.run(['git', '-C', self.root, 'init', '-q', '-b', 'master'], check=True)

    def test_init_fills_names_and_repins(self):
        self.git_init()
        code, out = self.tool('init', '--project', 'Acme Project', '--owner', 'a-owner')
        self.assertEqual(code, 0, out)
        self.assertTrue(self.read('STATE.md').startswith('# STATE.md — Acme Project\n'))
        self.assertIn('**Decided by:** @a-owner', self.read(
            'docs/decisions/R-0001-adopt-the-handover-documentation-method.md'))
        self.assertNotIn('<PROJECT NAME>', self.read('docs/history/LEDGER_0001.md'))
        self.assertIn('"owner": "@a-owner"', self.read('tools/policy.json'))
        self.assertIn('/tools/ @a-owner', self.read('.github/CODEOWNERS'))
        hp = subprocess.run(['git', '-C', self.root, 'config', 'core.hooksPath'], stdout=subprocess.PIPE)
        self.assertEqual(hp.stdout.strip(), b'.githooks')
        self.assertPasses(expect_tag='v0')

    def test_init_refused_outside_git(self):
        code, out = self.tool('init', '--project', 'X', '--owner', 'y')
        self.assertEqual(code, 1)
        self.assertIn('git work tree', out)

    def test_init_refused_after_first_checkpoint(self):
        self.git_init()
        code, _ = self.tool('ledger-append', '--tag', 'v1', '--title', 't', '--record', self.record())
        self.assertEqual(code, 0)
        code, out = self.tool('init', '--project', 'X', '--owner', 'y')
        self.assertEqual(code, 1)
        self.assertIn('untouched starter', out)


class Directives(RepoTestCase):
    def test_new_directive_and_evidence(self):
        self.assertEqual(self.tool('new-directive', '--tag', 'v1', '--tier', '1', '--title', 'First wave')[0], 0)
        self.assertIn('**Tier:** 1', self.read('directives/v1/DIRECTIVE.md'))
        src = os.path.join(self.tmp, 'owner.txt')
        with open(src, 'wb') as f:
            f.write(b'I approve the plan.\r\nOwner\r\n')  # raw bytes, CRLF kept on purpose
        code, out = self.tool('evidence-add', '--tag', 'v1', src)
        self.assertEqual(code, 0, out)
        with open(self.path('directives/v1/evidence/owner.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'I approve the plan.\r\nOwner\r\n')
        self.assertPasses()

    def test_evidence_never_overwritten(self):
        src = self.record('one\n')
        self.assertEqual(self.tool('evidence-add', '--tag', 'v0', src, '--name', 'a.txt')[0], 0)
        code, out = self.tool('evidence-add', '--tag', 'v0', src, '--name', 'a.txt')
        self.assertEqual(code, 1)
        self.assertIn('never overwritten', out)

    def test_existing_directive_refused(self):
        code, _ = self.tool('new-directive', '--tag', 'v0', '--tier', '1', '--title', 'again')
        self.assertEqual(code, 1)

    def test_wrong_number_refused(self):
        code, out = self.tool('new-directive', '--tag', 'v5', '--tier', '1', '--title', 'skip')
        self.assertEqual(code, 1)
        self.assertIn('the next checkpoint is v1', out)


if __name__ == '__main__':
    unittest.main()
