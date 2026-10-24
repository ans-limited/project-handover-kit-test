"""Local rules and the pre-commit hook (design note s4 and s10; acceptance criterion AC5)."""
import contextlib
import io
import os
import re
import subprocess
import tempfile
import unittest

from helpers import TOOLS_DIR, GitRepoCase, check_docs

# Design note s4, local classification table (True = blocking in the hook, False = advisory).
EXPECTED_LOCAL = {
    ('C1', 'cap'): True,             # STATE.md over its hard cap
    ('C2', 'form'): True,            # one banner line, within 300 characters, in form
    ('C2', 'expect'): False,         # banner equals an expected tag (session start only)
    ('C3', 'sections'): True,        # sections present, in order, within budgets
    ('C4', 'proof'): True,           # no hashes, uuids, blockquotes, over-long lines
    ('C5', 'file'): True,            # decision file name, title, Status line, size
    ('C5', 'xref'): False,           # IDs have files; orphans; superseded; accepted pinned
    ('C6', 'format'): True,          # open-item format, status, duplicates
    ('C7', 'manifest'): True,        # PINS.sha256 line format (one file's own content)
    ('C7', 'match-base'): True,      # a file pinned on the local base still matches
    ('C7', 'match-new'): False,      # a file pinned on this branch matches
    ('C8', 'entry'): True,           # entry and header caps; structure of one ledger file
    ('C8', 'xref'): False,           # register, chain, banner = last entry, numbering, rollover
    ('C9', 'size'): True,            # directive within its cap
    ('C9', 'tier'): True,            # one valid Tier line (one file's own content)
    ('C9', 'xref'): False,           # DIRECTIVE.md present; manifest lists every file
    ('C9', 'evidence-base'): True,   # evidence existing on the local base still matches
    ('C9', 'evidence-new'): False,   # evidence added on this branch matches
    ('C10', 'xref'): False,          # links resolve
    ('C11', 'file'): True,           # root allowlist; sizes and names
    ('C12', 'file'): True,           # NUL, BOM, CR, UTF-8
    ('C13', 'xref'): False,          # CODEOWNERS equals its generated form
    ('C14', 'file'): True,           # gate workflow rules (each workflow file's own content)
    ('C15', 'file'): True,           # policy validation (the policy file's own content)
    ('P1', 'base'): True,
    ('P3', 'base'): True,
    ('P4', 'prefix'): True,
    ('P5', 'prefix'): True,
    ('LOCAL', 'master'): True,
    ('LOCAL', 'hooks'): True,
    ('PR', 'advice'): False,         # every PR-only rule, printed as advice locally
}
PR_ONLY_CODES = {'N', 'K', 'T', 'V', 'P2', 'B', 'A', 'TAG'}


class Classification(unittest.TestCase):
    def test_table_matches_the_note(self):
        self.assertEqual(check_docs.LOCAL_CLASS, EXPECTED_LOCAL)

    def test_every_emitted_finding_is_classified(self):
        src = open(os.path.join(TOOLS_DIR, 'check_docs.py'), encoding='utf-8').read()
        pairs = set(re.findall(r"\.fail\('([A-Z0-9]+)', '([a-z-]+)'", src))
        self.assertTrue(pairs)
        for code, sub in sorted(pairs):
            if code in PR_ONLY_CODES or (code, sub) in {('P4', 'pr'), ('P5', 'pr')}:
                continue
            self.assertIn((code, sub), EXPECTED_LOCAL, f'{code}/{sub} is emitted but not classified')


class Hook(GitRepoCase):
    def test_commit_on_master_refused(self):
        self.write('src/app.php', '<?php\n')
        ok, out = self.try_commit('on master')
        self.assertFalse(ok)
        self.assertIn('master', out)

    def test_routine_commit_on_branch_allowed(self):
        self.branch('routine/ui')
        self.write('src/app.php', '<?php\n')
        ok, out = self.try_commit('ui')
        self.assertTrue(ok, out)

    def test_staged_violation_with_clean_disk_refused(self):
        # A UTF-8 BOM, not CRLF: .gitattributes (eol=lf) normalises CRLF to LF when a file is staged,
        # so a CRLF file never reaches the index; a BOM is staged as it is.
        self.branch('routine/x')
        self.write_bytes('docs/notes.txt', b'\xef\xbb\xbfline\n')
        self.g('add', 'docs/notes.txt')
        self.write_bytes('docs/notes.txt', b'line\n')
        ok, out = self.try_commit('bom staged', add=False)
        self.assertFalse(ok, out)
        self.assertIn('UTF-8 BOM', out)

    def test_crlf_is_normalised_before_it_is_staged(self):
        self.branch('routine/x')
        self.write_bytes('docs/notes.txt', b'line\r\n')
        self.g('add', 'docs/notes.txt')
        self.assertEqual(subprocess.run(['git', '-C', self.root, 'cat-file', 'blob', ':docs/notes.txt'],
                                        stdout=subprocess.PIPE).stdout, b'line\n')
        ok, out = self.try_commit('crlf normalised', add=False)
        self.assertTrue(ok, out)

    def test_dirty_disk_with_clean_index_allowed(self):
        self.branch('routine/x')
        self.write_bytes('docs/notes.txt', b'line\n')
        self.g('add', 'docs/notes.txt')
        self.write_bytes('docs/notes.txt', b'line\r\n')
        ok, out = self.try_commit('clean staged', add=False)
        self.assertTrue(ok, out)

    def test_hook_off_fails_locally(self):
        self.g('config', '--unset', 'core.hooksPath')
        self.branch('routine/x')
        self.g('add', '-A')
        self.assertIn(('LOCAL', 'hooks'), {(c, s) for c, s, m in self.local_blocking()})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_docs.main(['--repo', self.root]), 1)
            self.assertEqual(check_docs.main(['--repo', self.root, '--ci']), 0)

    def test_without_git_fails_closed(self):
        d = tempfile.mkdtemp(prefix='nogit-')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = check_docs.main(['--staged', '--repo', d])
        self.assertEqual(code, 2, out.getvalue())


class IntermediateSnapshots(GitRepoCase):
    """AC5: valid intermediate snapshots commit locally with advice only; as a PR head they fail."""

    def check_case(self, prepare, expect_pr_codes):
        n = self.start_checkpoint(1)
        prepare(n)
        self.g('add', '-A')
        staged = self.staged()
        self.assertEqual(staged.blocking(local=True), [], [x[:3] for x in staged.items])
        self.assertTrue(staged.advisory(local=True), 'expected advice for an incomplete checkpoint')
        ok, out = self.try_commit('intermediate')
        self.assertTrue(ok, out)
        codes, f, info = self.pr_codes(f'checkpoint/v{n}')
        self.assertTrue(codes & expect_pr_codes, f'{expect_pr_codes} expected; got {[x[:3] for x in f.items]}')

    def test_ledger_entry_before_banner(self):
        def prep(n):
            self.tool('ledger-append', '--tag', f'v{n}', '--title', 't', '--record', self.record())
        self.check_case(prep, {'C8', 'K'})

    def test_banner_before_ledger_entry(self):
        self.check_case(lambda n: self.set_banner_tag(f'v{n}'), {'C8', 'P5'})

    def test_decision_not_yet_listed(self):
        def prep(n):
            self.write('docs/decisions/R-0003-new.md', '# R-0003 — New\n\n**Status:** Proposed\n')
        self.check_case(prep, {'C5'})

    def test_directive_folder_before_directive_file(self):
        def prep(n):
            self.remove(f'directives/v{n}/DIRECTIVE.md')
        self.check_case(prep, {'C9', 'K'})

    def test_link_to_file_not_yet_written(self):
        def prep(n):
            self.write('docs/design/topic.md', '# Topic — design\n\nSee [the plan](plan.md).\n')
        self.check_case(prep, {'C10'})

    def test_evidence_before_manifest_line(self):
        def prep(n):
            self.write(f'directives/v{n}/evidence/owner.txt', 'I approve.\n')
        self.check_case(prep, {'C9'})


class StaleLocalMaster(GitRepoCase):
    """Design note s4: blocking local rules stay correct against a stale local master."""

    def setUp(self):
        super().setUp()
        for _ in range(2):
            n = self.full_checkpoint(tier=1)
            self.squash_merge(f'checkpoint/v{n}')
        self.branch('routine/late')
        self.g('branch', '-f', 'master', self.root_commit)   # local master is now stale (v0)

    def test_valid_routine_change_not_refused(self):
        self.write('src/app.php', '<?php\n')
        self.g('add', '-A')
        staged = self.staged()
        self.assertEqual(staged.blocking(local=True), [], [x[:3] for x in staged.items])
        self.assertTrue(staged.advisory(local=True), 'checkpoints merged since look new: advice expected')
        ok, out = self.try_commit('ui')
        self.assertTrue(ok, out)

    def test_tampering_with_old_ledger_entry_still_caught(self):
        self.replace('docs/history/LEDGER_0001.md', 'Created the documentation', 'Made the documentation')
        ok, out = self.try_commit('tamper ledger')
        self.assertFalse(ok, out)
        self.assertIn('P5', out)

    def test_tampering_with_old_directive_still_caught(self):
        self.replace('directives/v0/DIRECTIVE.md', 'Create the repository', 'Make the repository')
        ok, out = self.try_commit('tamper directive')
        self.assertFalse(ok, out)
        self.assertIn('P1', out)


if __name__ == '__main__':
    unittest.main()
