"""The authoritative PR check (design note s2, s3, s5.2, s6), with real git repositories."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import GitRepoCase, check_docs

R1 = 'docs/decisions/R-0001-adopt-the-handover-documentation-method.md'
SMALL_LEDGER = dict(ledger_max_bytes=2200, ledger_entry_max_bytes=600, ledger_header_max_bytes=1000)
BIG_BODY = 'line of history detail\n' * 20   # about 500 B per entry, so SMALL_LEDGER rolls over within 4 entries


def proposed(did, title='A rule', status='Proposed'):
    return f'# {did} — {title}\n\n**Status:** {status}\n**Date:** 2026-10-21\n**Supersedes:** none\n\n## Ruling\n\n1. x\n'


class Routine(GitRepoCase):
    def test_application_change_passes(self):
        self.branch('routine/ui')
        self.write('src/app.php', '<?php\n')
        self.commit('ui')
        info = self.assertPRPasses('routine/ui')
        self.assertFalse(info['checkpoint'])
        self.assertEqual(info['required'], 0)

    def test_checkpoint_records_refused(self):
        for relp, text in [('STATE.md', None), ('docs/history/LEDGERS.md', None), ('docs/design/topic.md', '# t\n')]:
            with self.subTest(relp=relp):
                self.checkout('master')
                self.branch(f'routine/{len(relp)}-{relp.replace("/", "-")}')
                if text is None:
                    self.write(relp, self.read(relp) + '\n')
                else:
                    self.write(relp, text)
                self.commit('touch')
                self.assertPRFails(self.g('rev-parse', '--abbrev-ref', 'HEAD'), 'T')

    def test_gate_protocol_and_template_paths_need_tier2(self):
        for relp in ['tools/policy.json', 'CLAUDE.md', 'AGENTS.md', '.gitattributes', '.githooks/pre-commit',
                     '.github/workflows/handover-tests.yml', 'directives/_TEMPLATE/DIRECTIVE.md',
                     'reports/_TEMPLATE_SESSION_REPORT.md', 'docs/decisions/_TEMPLATE_CR.md']:
            with self.subTest(relp=relp):
                self.checkout('master')
                b = 'routine/' + relp.replace('/', '-').replace('.', '')
                self.branch(b)
                self.write(relp, self.read(relp) + '\n')
                self.commit('touch')
                f = self.assertPRFails(b, 'T')
                self.assertTrue(any('required tier 2' in m for c, s, m, p in f.items))

    def test_new_report_refused(self):
        self.branch('routine/report')
        self.write('reports/SESSION_REPORT_v1.md', '# r\n')
        self.commit('report')
        self.assertPRFails('routine/report', 'P2')

    def test_ledger_entry_refused(self):
        self.branch('routine/ledger')
        self.tool('ledger-append', '--tag', 'v1', '--title', 't', '--record', self.record())
        self.commit('ledger')
        self.assertPRFails('routine/ledger', 'P5')

    def test_routine_after_checkpoint_is_judged_on_its_own_changes(self):
        self.full_checkpoint(tier=1)
        self.squash_merge('checkpoint/v1')
        self.branch('routine/after')
        self.write('src/app.php', '<?php\n')
        self.commit('ui')
        info = self.assertPRPasses('routine/after')
        self.assertEqual(info['required'], 0)


class Checkpoint(GitRepoCase):
    def test_tier1_complete_passes(self):
        n = self.full_checkpoint(tier=1, work=lambda n: self.write('src/a.php', '<?php\n'))
        info = self.assertPRPasses(f'checkpoint/v{n}')
        self.assertEqual((info['checkpoint'], info['N'], info['declared'], info['required']), (True, 1, 1, 1))

    def test_each_missing_item_fails(self):
        cases = {
            'banner': lambda: self.set_banner_tag('v0'),
            'ledger': lambda: self.write('docs/history/LEDGER_0001.md', self.read_at('master', 'docs/history/LEDGER_0001.md')),
            'report': lambda: self.remove('reports/SESSION_REPORT_v1.md'),
        }
        for name, undo in cases.items():
            with self.subTest(missing=name):
                self.checkout('master')
                self.g('branch', '-D', 'checkpoint/v1', ok=False)
                n = self.start_checkpoint(1)
                self.close_checkpoint(n)
                undo()
                if name == 'ledger':
                    self.write('docs/history/LEDGERS.md', self.read_at('master', 'docs/history/LEDGERS.md'))
                self.commit('incomplete')
                codes, f, info = self.pr_codes('checkpoint/v1')
                self.assertTrue(codes & {'K', 'P5', 'C8'}, f'{name}: {f.items}')

    def read_at(self, ref, relp):
        return self.g('show', f'{ref}:{relp}') + '\n'

    def test_wrong_number_fails(self):
        self.branch('checkpoint/x')
        self.write('directives/v5/DIRECTIVE.md', '# DIRECTIVE v5 — x\n\n**Tier:** 1\n')
        self.commit('v5')
        self.assertPRFails('checkpoint/x', 'N')

    def test_two_directive_folders_fail(self):
        n = self.start_checkpoint(1)
        self.write('directives/v2/DIRECTIVE.md', '# DIRECTIVE v2 — x\n\n**Tier:** 1\n')
        self.close_checkpoint(n)
        self.commit('two')
        self.assertPRFails('checkpoint/v1', 'N')

    def test_folder_without_directive_fails(self):
        self.branch('checkpoint/v1')
        self.write('directives/v1/notes.md', '# notes\n')
        self.close_checkpoint(1)
        self.commit('no directive')
        self.assertPRFails('checkpoint/v1', 'K')

    def test_missing_tier_line_fails(self):
        n = self.start_checkpoint(1)
        self.replace(f'directives/v{n}/DIRECTIVE.md', '**Tier:** 1\n', '')
        self.close_checkpoint(n)
        self.commit('no tier')
        self.assertPRFails(f'checkpoint/v{n}', 'K')

    def test_tier1_touching_gate_fails_and_tier2_passes(self):
        n = self.full_checkpoint(tier=1, work=lambda n: self.write('CLAUDE.md', self.read('CLAUDE.md') + '\n'))
        self.assertPRFails(f'checkpoint/v{n}', 'T')
        self.checkout('master')
        self.g('branch', '-D', f'checkpoint/v{n}')
        n = self.full_checkpoint(tier=2, work=lambda n: self.write('CLAUDE.md', self.read('CLAUDE.md') + '\n'))
        info = self.assertPRPasses(f'checkpoint/v{n}')
        self.assertEqual(info['required'], 2)

    def test_owner_override_tier2_without_protected_paths(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.write('src/a.php', '<?php\n'))
        info = self.assertPRPasses(f'checkpoint/v{n}')
        self.assertEqual((info['declared'], info['required']), (2, 1))

    def test_tier2_without_verification_fails(self):
        n = self.full_checkpoint(tier=2, verify=False)
        self.assertPRFails(f'checkpoint/v{n}', 'K')

    def test_drafts_are_revisable_within_the_pr(self):
        n = self.start_checkpoint(1)
        self.commit('draft directive')
        self.replace(f'directives/v{n}/DIRECTIVE.md', 'Checkpoint v1', 'Checkpoint v1 (corrected)')
        self.commit('fix directive')
        self.close_checkpoint(n)
        self.commit('close')
        self.replace('docs/history/LEDGER_0001.md', 'Record body.', 'Record body, rewritten.')
        self.replace(f'reports/SESSION_REPORT_v{n}.md', 'Report.', 'Report, revised.')
        self.commit('revise records')
        self.assertPRPasses(f'checkpoint/v{n}')

    def test_concurrent_checkpoints_renumber(self):
        self.branch('cp-b')                      # B starts from v0 as well
        self.checkout('master')
        self.full_checkpoint(tier=1)            # A: checkpoint/v1
        self.squash_merge('checkpoint/v1')
        self.checkout('cp-b')
        self.tool('new-directive', '--tag', 'v1', '--tier', '1', '--title', 'B')
        self.write('src/b.php', '<?php\n')
        self.close_checkpoint(1)
        self.commit('B as v1')
        # Out of date with master, B still passes against its old base: this is why GitHub must
        # require branches to be up to date (live test G12).
        self.assertPRPasses('cp-b', base=self.root_commit)
        self.g('merge', '-q', '-X', 'theirs', '--no-edit', 'master', ok=False)
        self.g('checkout', 'master', '--', '.', ok=False)   # resolve to master's records
        self.write('src/b.php', '<?php\n')
        self.write('directives/v1/extra.md', 'B still claims v1\n')
        self.commit('updated, still v1')
        codes, f, info = self.pr_codes('cp-b')
        self.assertTrue({'N', 'P2'} & codes, f.items)
        self.remove('directives/v1/extra.md')
        self.tool('new-directive', '--tag', 'v2', '--tier', '1', '--title', 'B renumbered')
        self.close_checkpoint(2)
        self.commit('renumbered to v2')
        info = self.assertPRPasses('cp-b')
        self.assertEqual(info['N'], 2)

    def test_tags_change_no_result(self):
        n = self.full_checkpoint(tier=1)
        before = [(c, m) for c, s, m, p in self.pr(f'checkpoint/v{n}')[0].items]
        self.g('tag', 'v1', self.root_commit)
        self.g('tag', 'v7')
        after = [(c, m) for c, s, m, p in self.pr(f'checkpoint/v{n}')[0].items]
        self.g('tag', '-d', 'v1', 'v7')
        self.assertEqual(before, after)


class Decisions(GitRepoCase):
    def test_new_proposed_ruling_at_tier1_passes(self):
        def work(n):
            self.write('docs/decisions/R-0003-a-rule.md', proposed('R-0003'))
            self.replace('STATE.md', '| R-0001 | Adopt', '| R-0003 | A rule | Proposed | x |\n| R-0001 | Adopt')
        n = self.full_checkpoint(tier=1, work=work)
        self.assertPRPasses(f'checkpoint/v{n}')

    def accept(self, n):
        self.write('docs/decisions/R-0003-a-rule.md', proposed('R-0003', status='Accepted'))
        self.replace('STATE.md', '| R-0001 | Adopt', '| R-0003 | A rule | Accepted | x |\n| R-0001 | Adopt')
        self.tool('pin', 'docs/decisions/R-0003-a-rule.md')

    def test_accepting_needs_tier2(self):
        n = self.full_checkpoint(tier=1, work=self.accept)
        self.assertPRFails(f'checkpoint/v{n}', 'T')
        self.checkout('master')
        self.g('branch', '-D', f'checkpoint/v{n}')
        n = self.full_checkpoint(tier=2, work=self.accept)
        self.assertPRPasses(f'checkpoint/v{n}')

    def test_routine_pr_cannot_add_a_ruling(self):
        self.branch('routine/rule')
        self.write('docs/decisions/R-0003-a-rule.md', proposed('R-0003'))
        self.commit('rule')
        self.assertPRFails('routine/rule', 'T')

    def test_accepted_ruling_never_changes(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.replace(R1, 'A single handover file', 'One single handover file'))
        self.assertPRFails(f'checkpoint/v{n}', 'P3')

    def test_delete_and_rename_fail(self):
        for how in ('delete', 'rename'):
            with self.subTest(how=how):
                self.checkout('master')
                self.g('branch', '-D', 'checkpoint/v1', ok=False)
                def work(n, how=how):
                    if how == 'delete':
                        self.remove(R1)
                    else:
                        self.g('mv', R1, R1.replace('adopt-the', 'adopting-the'))
                n = self.full_checkpoint(tier=2, work=work)
                self.assertPRFails(f'checkpoint/v{n}', 'P3')


class Pins(GitRepoCase):
    def pins_text(self):
        return self.read('docs/PINS.sha256')

    def test_ruling_and_pin_edited_together_fails(self):
        def work(n):
            self.replace(R1, 'A single handover file', 'One single handover file')
            sha = check_docs.sha256_bytes(open(self.path(R1), 'rb').read())
            lines = [(sha + '  ' + R1) if l.endswith('  ' + R1) else l for l in self.pins_text().split('\n')]
            self.write('docs/PINS.sha256', '\n'.join(lines))
        n = self.full_checkpoint(tier=2, work=work)
        codes, f, info = self.pr_codes(f'checkpoint/v{n}')
        self.assertTrue({'P3', 'P4'} <= codes, f.items)

    def test_removed_and_reordered_lines_fail(self):
        for how in ('remove', 'reorder'):
            with self.subTest(how=how):
                self.checkout('master')
                self.g('branch', '-D', 'checkpoint/v1', ok=False)
                def work(n, how=how):
                    lines = self.pins_text().rstrip('\n').split('\n')
                    body = [l for l in lines if not l.startswith('#')]
                    head = [l for l in lines if l.startswith('#')]
                    body = body[1:] if how == 'remove' else list(reversed(body))
                    self.write('docs/PINS.sha256', '\n'.join(head + body) + '\n')
                n = self.full_checkpoint(tier=2, work=work)
                self.assertPRFails(f'checkpoint/v{n}', 'P4')

    def hand_pin(self, relp):
        """Append a pin line by hand: tools/handover.py pin now refuses these targets (finding R2), so
        the test simulates an agent that bypasses the tool; the PR check must still catch it."""
        sha = check_docs.sha256_bytes(open(self.path(relp), 'rb').read())
        self.write('docs/PINS.sha256', self.pins_text() + f'{sha}  {relp}\n')

    def test_pin_of_another_kind_fails(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.hand_pin('README.md'))
        self.assertPRFails(f'checkpoint/v{n}', 'P4')

    def test_pin_of_open_ledger_fails(self):
        """Review finding R2, first stage: pinning the still-OPEN ledger is refused at the PR."""
        n = self.full_checkpoint(tier=1, work_after_close=lambda n: self.hand_pin('docs/history/LEDGER_0001.md'))
        f = self.assertPRFails(f'checkpoint/v{n}', 'P4')
        self.assertTrue(any('OPEN ledger' in m for c, s, m, p in f.items))

    def test_open_ledger_pin_cannot_break_the_next_checkpoint(self):
        """Review finding R2, consequence: because the pin is refused, the next checkpoint is not blocked."""
        n = self.full_checkpoint(tier=1)
        self.squash_merge(f'checkpoint/v{n}')
        n = self.full_checkpoint(tier=1)
        self.assertPRPasses(f'checkpoint/v{n}')

    def test_pin_of_proposed_decision_fails(self):
        path = 'docs/decisions/R-0003-proposed.md'

        def work(n):
            self.write(path, '# R-0003 — Proposed\n\n**Status:** Proposed\n**Supersedes:** none\n\nRule.\n')
            self.replace('STATE.md', '| R-0001 | Adopt', '| R-0003 | Proposed | Proposed | x |\n| R-0001 | Adopt')
            self.hand_pin(path)
        n = self.full_checkpoint(tier=2, work=work)
        f = self.assertPRFails(f'checkpoint/v{n}', 'P4')
        self.assertTrue(any('is Proposed' in m for c, s, m, p in f.items))

    def test_pin_of_template_fails(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.hand_pin('docs/decisions/_TEMPLATE_RULING.md'))
        self.assertPRFails(f'checkpoint/v{n}', 'P4')


class SnapshotFidelity(GitRepoCase):
    """Review finding R1: the PR check reads exact tracked blobs; attributes cannot hide or alter files."""

    def test_nested_export_ignore_cannot_hide_a_file_in_a_routine_pr(self):
        self.branch('routine/hide')
        self.write('src/.gitattributes', 'bad.ts export-ignore\n')
        self.write_bytes('src/bad.ts', b'\xef\xbb\xbfconst value = 1;\n')
        self.commit('hide')
        f = self.assertPRFails('routine/hide', 'C12')
        self.assertTrue(any('src/bad.ts' in m for c, s, m, p in f.items))

    def test_root_export_ignore_cannot_hide_a_root_document(self):
        def work(n):
            self.write('NOTES.md', '# forbidden root document\n')
            self.write('.gitattributes', self.read('.gitattributes') + '\nNOTES.md export-ignore\n')
        n = self.full_checkpoint(tier=2, work=work)
        f = self.assertPRFails(f'checkpoint/v{n}', 'C11')
        self.assertTrue(any('NOTES.md' in m for c, s, m, p in f.items))

    def test_export_subst_does_not_change_checked_bytes(self):
        self.branch('routine/subst')
        self.write('src/.gitattributes', 'stamp.txt export-subst\n')
        self.write('src/stamp.txt', '$Format:%H$\n')
        c = self.commit('subst')
        archived = subprocess.run(['git', '-C', self.root, 'archive', '--format=tar', c, 'src/stamp.txt'],
                                  stdout=subprocess.PIPE, check=True).stdout
        self.assertNotIn(b'$Format:%H$', archived)          # git archive rewrites the bytes...
        d = tempfile.mkdtemp(prefix='mat-')
        check_docs.materialize(self.root, c, d)
        with open(os.path.join(d, 'src', 'stamp.txt'), 'rb') as fh:
            self.assertEqual(fh.read(), b'$Format:%H$\n')     # ...the gate checks what is stored

    def index_entry(self, branch, mode, obj, path):
        self.branch(branch)
        self.g('update-index', '--add', '--cacheinfo', f'{mode},{obj},{path}')
        self.g('commit', '-q', '--no-verify', '-m', f'{mode} {path}')

    def blob(self, text):
        p = subprocess.run(['git', '-C', self.root, 'hash-object', '-w', '--stdin'], input=text.encode(),
                           stdout=subprocess.PIPE, check=True)
        return p.stdout.decode().strip()

    def test_symlink_refused_by_default(self):
        self.index_entry('routine/link', '120000', self.blob('../README.md'), 'src/link.md')
        f = self.assertPRFails('routine/link', 'C11')
        self.assertTrue(any('symbolic link' in m for c, s, m, p in f.items))

    def test_submodule_refused_by_default(self):
        self.index_entry('routine/sub', '160000', self.root_commit, 'vendor/lib')
        f = self.assertPRFails('routine/sub', 'C11')
        self.assertTrue(any('submodule' in m for c, s, m, p in f.items))

    def test_hook_without_executable_bit_fails(self):
        """Found on GitHub's Linux runners: a hook stored as 100644 is skipped by git there."""
        self.assertTrue(self.g('ls-files', '-s', '.githooks/pre-commit').startswith('100755'))
        self.branch('routine/hookmode')
        self.g('update-index', '--chmod=-x', '.githooks/pre-commit')
        self.g('commit', '-q', '--no-verify', '-m', 'hook loses its executable bit')
        f = self.assertPRFails('routine/hookmode', 'C11')
        self.assertTrue(any('executable bit' in m for c, s, m, p in f.items))

    def test_case_collision_refused(self):
        self.index_entry('routine/case', '100644', self.blob('# a\n'), 'docs/Notes.txt')
        second = self.blob('# b\n')
        self.g('update-index', '--add', '--cacheinfo', f'100644,{second},docs/notes.txt')
        self.g('commit', '-q', '--no-verify', '-m', 'second case')
        f = self.assertPRFails('routine/case', 'C11')
        self.assertTrue(any('letter case' in m for c, s, m, p in f.items))


class LedgerExtension(GitRepoCase):
    """The review's historical-entry gap: existing entries stay unchanged, so text added after an
    existing ledger's bytes must begin with the next entry heading or the CLOSED section."""
    LEDGER = 'docs/history/LEDGER_0001.md'

    def extend_v0(self, n):
        self.write(self.LEDGER, self.read(self.LEDGER) + '\n### Added later, attributed to v0\n\nHistorical addition.\n')

    def test_extending_the_last_entry_before_the_new_heading_fails(self):
        n = self.full_checkpoint(tier=1, work=self.extend_v0)
        f = self.assertPRFails(f'checkpoint/v{n}', 'P5')
        self.assertTrue(any('added to an existing entry' in m for c, s, m, p in f.items))

    def test_the_hook_blocks_it_locally(self):
        n = self.start_checkpoint(1)
        self.extend_v0(n)
        ok, out = self.try_commit('extend v0')
        self.assertFalse(ok, out)
        self.assertIn('added to an existing entry', out)

    def test_extending_after_the_new_entry_is_allowed_within_the_pr(self):
        n = self.start_checkpoint(1)
        self.close_checkpoint(n)
        self.write(self.LEDGER, self.read(self.LEDGER) + '\nMore detail for the new entry.\n')
        self.commit('extend the new entry')
        self.assertPRPasses(f'checkpoint/v{n}')


class AdoptionWebService(GitRepoCase):
    """Generic use: a web service whose protected core is its migrations and billing module."""
    POLICY = {'paths': {'gate': ['tools/**', '.githooks/**', '.github/**', '.gitattributes', 'CLAUDE.md', 'AGENTS.md'],
                        'templates': ['directives/_TEMPLATE/**', 'reports/_TEMPLATE_*.md',
                                      'docs/decisions/_TEMPLATE_*.md', 'docs/design/_TEMPLATE_*.md'],
                        'core': ['migrations/**', 'src/billing/**'],
                        'reserved_top_level': ['directives', 'reports', 'docs', 'tools', '.github', '.githooks',
                                               'STATE.md', 'CLAUDE.md', 'AGENTS.md', '.gitattributes']}}
    CORE_FILE, OTHER_FILE = 'migrations/0002_add_invoices.sql', 'src/api/health.ts'

    def test_profile(self):
        self.assertIn('/' + self.CORE_FILE.split('/')[0] + '/', self.read('.github/CODEOWNERS'))
        self.branch('routine/other')
        self.write(self.OTHER_FILE, 'x\n')
        self.commit('other')
        self.assertPRPasses('routine/other')
        self.checkout('master')
        self.branch('routine/core')
        self.write(self.CORE_FILE, 'x\n')
        self.commit('core')
        self.assertPRFails('routine/core', 'T')
        self.checkout('master')
        n = self.full_checkpoint(tier=2, work=lambda n: self.write(self.CORE_FILE, 'x\n'))
        self.assertPRPasses(f'checkpoint/v{n}')


class AdoptionDataPipeline(AdoptionWebService):
    """Generic use: a data pipeline whose protected core is its validation stage and schemas."""
    POLICY = {'paths': dict(AdoptionWebService.POLICY['paths'], core=['pipelines/validation/**', 'schemas/**'])}
    CORE_FILE, OTHER_FILE = 'schemas/customer.json', 'notebooks/explore.txt'


class RolloverPins(GitRepoCase):
    POLICY = SMALL_LEDGER

    def test_rollover_pin_at_tier1_passes(self):
        for _ in range(4):
            n = self.full_checkpoint(tier=1, body=BIG_BODY)
            info = self.assertPRPasses(f'checkpoint/v{n}')
            self.squash_merge(f'checkpoint/v{n}')
        self.assertIn('docs/history/LEDGER_0001.md', self.read('docs/PINS.sha256'))

    def test_closed_ledger_never_changes(self):
        for _ in range(4):
            n = self.full_checkpoint(tier=1, body=BIG_BODY)
            self.squash_merge(f'checkpoint/v{n}')
        self.assertIn('LEDGER_0002.md', self.read('docs/history/LEDGERS.md'))
        n = self.full_checkpoint(tier=2, work=lambda n: self.write(
            'docs/history/LEDGER_0001.md', self.read('docs/history/LEDGER_0001.md') + 'late\n'))
        codes, f, info = self.pr_codes(f'checkpoint/v{n}')
        self.assertTrue({'P1', 'P5'} & codes, f.items)


class Ledgers(GitRepoCase):
    LEDGER = 'docs/history/LEDGER_0001.md'

    def test_earlier_entry_rewritten_fails(self):
        n = self.full_checkpoint(tier=1, work=lambda n: self.replace(self.LEDGER, 'Created the documentation', 'Made the documentation'))
        self.assertPRFails(f'checkpoint/v{n}', 'P5')

    def test_split_record_fails(self):
        n = self.start_checkpoint(1)
        self.close_checkpoint(n)
        self.write(self.LEDGER, self.read(self.LEDGER) + '\n## v2 — 2026-10-21 — Second half\n\nmore\n')
        self.set_banner_tag('v2')
        self.commit('split')
        codes, f, info = self.pr_codes(f'checkpoint/v{n}')
        self.assertIn('P5', codes)

    def test_text_outside_entries_fails(self):
        n = self.start_checkpoint(1)
        self.close_checkpoint(n)
        self.write(self.LEDGER, self.read(self.LEDGER) + '\n## Notes\n\nloose text\n')
        self.commit('loose')
        self.assertPRFails(f'checkpoint/v{n}', 'C8')

    def test_numbering_gap_fails(self):
        n = self.start_checkpoint(1)
        self.close_checkpoint(n)
        self.replace(self.LEDGER, '## v1 — ', '## v2 — ')
        self.commit('gap')
        codes, f, info = self.pr_codes(f'checkpoint/v{n}')
        self.assertTrue({'P5', 'C8'} <= codes, f.items)

    def test_register_edit_fails(self):
        n = self.start_checkpoint(1)
        self.close_checkpoint(n)
        self.replace('docs/history/LEDGERS.md', '| OPEN | v0 |', '| OPEN | v9 |')
        self.commit('register')
        self.assertPRFails(f'checkpoint/v{n}', 'P5')

    def test_large_dump_fails(self):
        n = self.start_checkpoint(1)
        self.close_checkpoint(n)
        self.write(self.LEDGER, self.read(self.LEDGER) + ('old history line ' * 40 + '\n') * 1900)
        self.commit('dump')
        f = self.assertPRFails(f'checkpoint/v{n}', 'C8')
        self.assertTrue(any('entry v1' in m for c, s, m, p in f.items))


class ClosedRecords(GitRepoCase):
    def setUp(self):
        super().setUp()
        n = self.full_checkpoint(tier=1)
        self.squash_merge(f'checkpoint/v{n}')

    def test_old_directive_report_and_evidence_never_change(self):
        for relp in ['directives/v1/DIRECTIVE.md', 'reports/SESSION_REPORT_v1.md', 'directives/v0/evidence/MANIFEST.sha256']:
            with self.subTest(relp=relp):
                self.checkout('master')
                self.g('branch', '-D', 'checkpoint/v2', ok=False)
                n = self.full_checkpoint(tier=2, work=lambda n, relp=relp: self.write(relp, self.read(relp) + 'edit\n'))
                self.assertPRFails(f'checkpoint/v{n}', 'P1')

    def test_old_directive_deleted_fails(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.remove('directives/v1'))
        self.assertPRFails(f'checkpoint/v{n}', 'P1')

    def test_template_change_is_tier2_not_prohibited(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.write(
            'reports/_TEMPLATE_SESSION_REPORT.md', self.read('reports/_TEMPLATE_SESSION_REPORT.md') + '\n'))
        self.assertPRPasses(f'checkpoint/v{n}')

    def test_new_template_is_tier2(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.write('reports/_TEMPLATE_REVIEW.md', '# t\n'))
        self.assertPRPasses(f'checkpoint/v{n}')

    def test_stray_report_in_checkpoint_fails(self):
        n = self.full_checkpoint(tier=1, work=lambda n: self.write('reports/NOTES_v2.md', '# n\n'))
        self.assertPRFails(f'checkpoint/v{n}', 'P2')


class Verification(GitRepoCase):
    def tier2(self, after_verify=None, verified=None, result='PASS', pre_create=False):
        n = self.start_checkpoint(2)
        self.write('src/a.php', '<?php\n')
        self.close_checkpoint(n)
        if pre_create:
            self.add_verification(n, verified='0' * 40)
        v = self.commit('work')
        if after_verify:
            after_verify()
        self.add_verification(n, verified=verified or v, result=result)
        self.commit('verification')
        return f'checkpoint/v{n}'

    def test_valid_verification_passes(self):
        self.assertPRPasses(self.tier2())

    def test_change_after_verified_commit_fails(self):
        def change():
            self.write('src/a.php', '<?php // changed\n')
            self.commit('late change')
        self.assertPRFails(self.tier2(after_verify=change), 'V')

    def test_change_then_revert_passes_by_design(self):
        def change_and_revert():
            self.write('src/a.php', '<?php // changed\n')
            self.commit('late change')
            self.write('src/a.php', '<?php\n')
            self.commit('revert')
        self.assertPRPasses(self.tier2(after_verify=change_and_revert))

    def test_non_ancestor_fails(self):
        self.assertPRFails(self.tier2(verified=self.root_commit.replace(self.root_commit[0], 'f' if self.root_commit[0] != 'f' else 'e', 1)), 'V')

    def test_unrelated_commit_fails(self):
        self.checkout('master')
        self.branch('elsewhere')
        self.write('x.txt', 'x\n')
        other = self.commit('elsewhere')
        self.checkout('master')
        self.assertPRFails(self.tier2(verified=other), 'V')

    def test_report_present_at_verified_commit_fails(self):
        self.assertPRFails(self.tier2(pre_create=True), 'V')

    def test_result_not_pass_fails(self):
        self.assertPRFails(self.tier2(result='FAIL'), 'V')


class CheckerChanges(GitRepoCase):
    def run_trusted(self, branch, base='master'):
        """Run the checker exactly as CI does: the copy taken from the base commit."""
        d = tempfile.mkdtemp(prefix='trusted-')
        p = os.path.join(d, 'check_docs.py')
        with open(p, 'wb') as f:
            f.write(subprocess.run(['git', '-C', self.root, 'show', f'{base}:tools/check_docs.py'],
                                   stdout=subprocess.PIPE, check=True).stdout)
        r = subprocess.run([sys.executable, p, '--repo', self.root, '--pr', '--base', base, '--head', branch, '--ci'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode, r.stdout.decode('utf-8', 'replace')

    def weaken(self, n=None):
        self.replace('tools/check_docs.py', 'def byte_preservation(cfg, base, head, f, local=False):\n',
                     'def byte_preservation(cfg, base, head, f, local=False):\n    return\n')

    def test_checker_change_below_tier2_fails(self):
        n = self.full_checkpoint(tier=1, work=self.weaken)
        code, out = self.run_trusted(f'checkpoint/v{n}')
        self.assertEqual(code, 1, out)
        self.assertIn('required tier 2', out)

    def test_weakened_checker_cannot_excuse_its_own_pr(self):
        def work(n):
            self.weaken()
            self.replace(R1, 'A single handover file', 'One single handover file')
        n = self.full_checkpoint(tier=2, work=work)
        code, out = self.run_trusted(f'checkpoint/v{n}')
        self.assertEqual(code, 1, out)
        self.assertIn('P3', out)

    def test_tier2_checker_change_passes_the_gate(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.replace('tools/check_docs.py', '"""check_docs.py --', '"""check_docs.py (v2) --'))
        code, out = self.run_trusted(f'checkpoint/v{n}')
        self.assertEqual(code, 0, out)

    def test_policy_change_applies_only_after_merge(self):
        self.branch('routine/core')
        self.write('src/core/rules/rule.php', '<?php\n')
        self.commit('kernel')
        self.assertPRPasses('routine/core')
        self.checkout('master')

        def add_core(n):
            pol = json.loads(self.read('tools/policy.json'))
            pol['paths']['core'] = ['src/core/rules/**']
            self.write('tools/policy.json', json.dumps(pol, indent=2, ensure_ascii=False) + '\n')
            self.tool('codeowners')
        n = self.full_checkpoint(tier=2, work=add_core)
        self.assertPRPasses(f'checkpoint/v{n}')
        self.squash_merge(f'checkpoint/v{n}')
        self.branch('routine/core2')
        self.write('src/core/rules/rule.php', '<?php\n')
        self.commit('kernel')
        self.assertPRFails('routine/core2', 'T')

    def test_overlapping_core_pattern_rejected(self):
        def bad_core(n):
            pol = json.loads(self.read('tools/policy.json'))
            pol['paths']['core'] = ['docs/**']
            self.write('tools/policy.json', json.dumps(pol, indent=2, ensure_ascii=False) + '\n')
            self.tool('codeowners')
        n = self.full_checkpoint(tier=2, work=bad_core)
        self.assertPRFails(f'checkpoint/v{n}', 'C15')

    def schema2(self, n):
        """Move the policy to schema 2 and regenerate CODEOWNERS for it. Applied after the checkpoint is
        closed, because the prototype's own tools (correctly) refuse to run on an unsupported schema."""
        pol = json.loads(self.read('tools/policy.json'))
        pol['schema_version'] = 2
        self.write('tools/policy.json', json.dumps(pol, indent=2, ensure_ascii=False) + '\n')
        self.write('.github/CODEOWNERS', check_docs.codeowners_text(pol))

    def test_schema_jump_fails_closed(self):
        n = self.full_checkpoint(tier=2, work_after_close=self.schema2)
        code, out = self.run_trusted(f'checkpoint/v{n}')
        self.assertEqual(code, 2, out)
        self.assertIn('not supported', out)

    def test_two_stage_upgrade(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.replace(
            'tools/check_docs.py', 'SUPPORTED_SCHEMAS = (1,)', 'SUPPORTED_SCHEMAS = (1, 2)'))
        self.assertEqual(self.run_trusted(f'checkpoint/v{n}')[0], 0)
        self.squash_merge(f'checkpoint/v{n}')
        n = self.full_checkpoint(tier=2, work_after_close=self.schema2)
        code, out = self.run_trusted(f'checkpoint/v{n}')
        self.assertEqual(code, 0, out)


class GateIntegrity(GitRepoCase):
    GATE = '.github/workflows/handover-gate.yml'

    def test_second_gate_job_fails(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.write(
            '.github/workflows/other.yml', 'name: other\non: [pull_request]\njobs:\n  handover-gate:\n    runs-on: x\n'))
        self.assertPRFails(f'checkpoint/v{n}', 'C14')

    def test_gate_job_by_name_in_other_file_fails(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.write(
            '.github/workflows/other.yml', 'jobs:\n  lookalike:\n    name: handover-gate\n'))
        self.assertPRFails(f'checkpoint/v{n}', 'C14')

    def test_condition_on_gate_fails_when_checked_directly(self):
        """G6a: the base checker, run directly against the head, rejects an if: on the gate."""
        n = self.full_checkpoint(tier=2, work=lambda n: self.replace(
            self.GATE, '    runs-on: ubuntu-24.04\n', '    if: false\n    runs-on: ubuntu-24.04\n'))
        self.assertPRFails(f'checkpoint/v{n}', 'C14')

    def snapshot_codes(self):
        return {c for c, s, m, p in check_docs.Snapshot(self.root, check_docs.load_policy(self.root)).run().items}

    def test_condition_spellings_are_all_rejected(self):
        """Review finding R3: spellings a text regex missed. Each is rejected as a condition or as
        YAML outside the supported subset."""
        variants = {
            'space before colon': '    if : false\n',
            'double-quoted key': '    "if": false\n',
            'single-quoted key': "    'if': false\n",
            'no space after colon': '    if:false\n',
            'condition on a step': '    steps:\n      - if: false\n',
        }
        original = self.read(self.GATE)
        for name, line in variants.items():
            with self.subTest(variant=name):
                self.write(self.GATE, original.replace('  handover-gate:\n', '  handover-gate:\n' + line, 1))
                self.assertIn('C14', self.snapshot_codes())
        self.write(self.GATE, original)
        self.assertNotIn('C14', self.snapshot_codes())

    def test_flow_mapping_and_yaml_features_are_rejected(self):
        original = self.read(self.GATE)
        variants = {
            'flow mapping job': original.replace('  handover-gate:\n    runs-on: ubuntu-24.04\n',
                                                 '  handover-gate: {if: false, runs-on: ubuntu-24.04}\n'),
            'anchor': original.replace('    runs-on: ubuntu-24.04\n', '    runs-on: &os ubuntu-24.04\n'),
            'document marker': '---\n' + original,
            'tab indentation': original.replace('    runs-on:', '\truns-on:'),
        }
        for name, text in variants.items():
            with self.subTest(variant=name):
                self.write(self.GATE, text)
                self.assertIn('C14', self.snapshot_codes())

    def test_duplicate_gate_job_spellings_in_other_workflows(self):
        base = 'name: other\non:\n  pull_request:\njobs:\n'
        variants = {
            'quoted id with space': base + '  "handover-gate" :\n    runs-on: x\n',
            'quoted name': base + '  lookalike:\n    name: "handover-gate"\n',
            'escaped name': base + '  lookalike:\n    name: "handover\\x2dgate"\n',
            'flow mapping': 'name: other\non:\n  pull_request:\njobs: {handover-gate: {runs-on: x}}\n',
        }
        for name, text in variants.items():
            with self.subTest(variant=name):
                self.write('.github/workflows/other.yml', text)
                self.assertIn('C14', self.snapshot_codes())

    def test_computed_job_names_are_rejected(self):
        """Fix-check finding F1: a job name built from an expression could evaluate to the gate's name.
        Job names must be static text; expressions are rejected, not evaluated."""
        base = 'name: lookalike\non:\n  pull_request:\n    branches: [master]\njobs:\n  alternate:\n'
        variants = {
            'constant expression': base + "    name: ${{ 'handover-gate' }}\n    runs-on: ubuntu-24.04\n",
            'matrix-derived': base + '    strategy:\n      matrix:\n        n: [handover-gate]\n'
                                     '    name: ${{ matrix.n }}\n    runs-on: ubuntu-24.04\n',
            'format() call': base + "    name: ${{ format('{0}-gate', 'handover') }}\n    runs-on: ubuntu-24.04\n",
            'variable': base + '    name: ${{ vars.GATE_NAME }}\n    runs-on: ubuntu-24.04\n',
        }
        for name, text in variants.items():
            with self.subTest(variant=name):
                self.write('.github/workflows/lookalike.yml', text)
                self.assertIn('C14', self.snapshot_codes())
        self.write('.github/workflows/lookalike.yml', base + '    name: Lint\n    runs-on: ubuntu-24.04\n')
        self.assertNotIn('C14', self.snapshot_codes())      # a static, different name is fine

    def test_gate_job_name_expression_is_rejected(self):
        self.replace(self.GATE, '  handover-gate:\n', "  handover-gate:\n    name: ${{ 'handover-gate' }}\n")
        self.assertIn('C14', self.snapshot_codes())

    def test_kit_workflows_are_inside_the_subset(self):
        import glob
        for path in glob.glob(os.path.join(self.root, '.github', 'workflows', '*.yml')):
            nodes, errors = check_docs.parse_workflow(open(path, encoding='utf-8').read())
            self.assertEqual(errors, [], path)

    def test_path_filter_on_gate_fails(self):
        n = self.full_checkpoint(tier=2, work=lambda n: self.replace(
            self.GATE, '    branches: [master]\n', '    branches: [master]\n    paths: [src/**]\n'))
        self.assertPRFails(f'checkpoint/v{n}', 'C14')


class Codeowners(GitRepoCase):
    def test_hand_edits_fail(self):
        edits = {
            'extra line': lambda t: t + '/src/ @someone-else\n',
            'reordered': lambda t: '\n'.join([t.split('\n')[0]] + list(reversed(t.rstrip('\n').split('\n')[1:]))) + '\n',
            'edited': lambda t: t.replace('/tools/', '/tool/'),
        }
        for name, edit in edits.items():
            with self.subTest(edit=name):
                self.checkout('master')
                self.g('branch', '-D', 'checkpoint/v1', ok=False)
                n = self.full_checkpoint(tier=2, work=lambda n, edit=edit: self.write(
                    '.github/CODEOWNERS', edit(self.read('.github/CODEOWNERS'))))
                self.assertPRFails(f'checkpoint/v{n}', 'C13')


if __name__ == '__main__':
    unittest.main()
