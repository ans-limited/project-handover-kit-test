"""Tests for tools/check_docs.py: the starter passes, and each check catches what it is for."""
import contextlib
import io
import unittest

from helpers import RepoTestCase, check_docs

R1 = 'docs/decisions/R-0001-adopt-the-handover-documentation-method.md'


class StarterPasses(RepoTestCase):
    def test_starter_passes(self):
        self.assertPasses(expect_tag='v0')

    def test_main_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(check_docs.main(['--repo', self.root]), 0)
        self.assertIn('RESULT PASS', out.getvalue())
        self.assertIn('SIZE  STATE.md', out.getvalue())
        self.write('STRAY.md', 'x\n')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_docs.main(['--repo', self.root]), 1)

    def test_missing_config_is_exit_2(self):
        import os
        os.remove(self.path('tools/policy.json'))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_docs.main(['--repo', self.root]), 2)


class C1Size(RepoTestCase):
    def test_over_cap_fails(self):
        self.set_config(state_max_bytes=1000, state_warn_bytes=500)
        self.assertFails('C1')

    def test_over_warning_warns(self):
        self.set_config(state_warn_bytes=1000)
        ch = self.check()
        self.assertEqual(ch.fails, [])
        self.assertIn('C1', {c for c, _ in ch.warns})


class C2Banner(RepoTestCase):
    def test_two_banners(self):
        self.replace('STATE.md', '## S9. Next step', 'CURRENT BASELINE: tag v1 (2026-10-21) — x\n\n## S9. Next step')
        self.assertFails('C2')

    def test_long_banner(self):
        self.replace('STATE.md', 'with the handover method', 'with the handover method' + ' and more' * 40)
        self.assertFails('C2')

    def test_malformed_banner(self):
        self.replace('STATE.md', 'CURRENT BASELINE: tag v0 (2026-10-20)', 'CURRENT BASELINE: v0 on 20 Oct')
        self.assertFails('C2')

    def test_expect_tag_mismatch(self):
        self.assertFails('C2', expect_tag='v7')


class C3Sections(RepoTestCase):
    def test_missing_section(self):
        text = self.read('STATE.md')
        start, end = text.index('## S8.'), text.index('## S9.')
        self.write('STATE.md', text[:start] + text[end:])
        self.assertFails('C3')

    def test_out_of_order(self):
        self.replace('STATE.md', '## S9. Next step', '## S10. Next step')
        self.assertFails('C3')

    def test_unexpected_heading(self):
        self.replace('STATE.md', '## S9. Next step', '## Session notes\n\nx\n\n## S9. Next step')
        self.assertFails('C3')

    def test_section_over_budget(self):
        self.replace('STATE.md', '## S9. Next step\n', '## S9. Next step\n\n' + 'word ' * 60 + '\n' + 'word ' * 60 + '\n'
                     + ('more words here. ' * 20 + '\n') * 10)
        self.assertFails('C3')


class C4NoProofDetails(RepoTestCase):
    def test_sha256(self):
        self.replace('STATE.md', 'No\n  application code yet.', 'No application code yet. ' + 'a' * 64)
        self.assertFails('C4')

    def test_git_object_id(self):
        self.replace('STATE.md', 'No\n  application code yet.', 'Commit ' + '0123456789abcdef' * 2 + '01234567.')
        self.assertFails('C4')

    def test_uuid(self):
        self.replace('STATE.md', 'No\n  application code yet.', 'Record 3b435b08-9950-42f8-b0d7-9a9f0135c0b9.')
        self.assertFails('C4')

    def test_blockquote(self):
        self.replace('STATE.md', '## S8. Accepted limitations\n', '## S8. Accepted limitations\n\n> I accept it.\n')
        self.assertFails('C4')

    def test_long_line(self):
        self.replace('STATE.md', '- **Method in force:** R-0001 (documentation) and R-0002 (checkpoints as pull requests).', '- **Method in force:** R-0001. ' + 'detail ' * 70)
        self.assertFails('C4')

    def test_short_commit_reference_is_allowed(self):
        self.replace('STATE.md', '- **Method in force:** R-0001 (documentation) and R-0002 (checkpoints as pull requests).', '- **Method in force:** R-0001 (commit cc435d8).')
        self.assertPasses()


class C5Decisions(RepoTestCase):
    def test_unknown_id_in_state(self):
        self.replace('STATE.md', '- **Method in force:** R-0001 (documentation) and R-0002 (checkpoints as pull requests).', '- **Method in force:** R-0001 and R-0042.')
        self.assertFails('C5')

    def test_orphan_decision(self):
        self.write('docs/decisions/R-0003-orphan.md', '# R-0003 — Orphan\n\n**Status:** Proposed\n')
        self.assertFails('C5')

    def test_accepted_must_be_pinned(self):
        self.write('docs/decisions/R-0003-new-rule.md', '# R-0003 — New rule\n\n**Status:** Accepted\n')
        self.replace('STATE.md', '| R-0001 | Adopt', '| R-0003 | New rule | Accepted | x |\n| R-0001 | Adopt')
        self.assertFails('C5')

    def test_proposed_listed_passes(self):
        self.write('docs/decisions/R-0003-new-rule.md', '# R-0003 — New rule\n\n**Status:** Proposed\n')
        self.replace('STATE.md', '| R-0001 | Adopt', '| R-0003 | New rule | Proposed | x |\n| R-0001 | Adopt')
        self.assertPasses()

    def test_superseded_still_listed(self):
        self.write('docs/decisions/R-0003-replace.md',
                   '# R-0003 — Replace\n\n**Status:** Proposed\n**Supersedes:** R-0001\n')
        self.replace('STATE.md', '| R-0001 | Adopt', '| R-0003 | Replace | Proposed | x |\n| R-0001 | Adopt')
        self.assertFails('C5')

    def test_superseded_reachable_through_successor(self):
        self.write('docs/decisions/R-0003-replace.md',
                   '# R-0003 — Replace\n\n**Status:** Proposed\n**Supersedes:** R-0001\n')
        text = self.read('STATE.md')
        start = text.index('| R-0001 | Adopt')
        end = text.index('\n', start)
        text = text[:start] + '| R-0003 | Replace | Proposed | x |' + text[end:]
        self.write('STATE.md', text.replace('R-0001', 'R-0003'))
        self.assertPasses()

    def test_bad_file_name(self):
        self.write('docs/decisions/Ruling 2.md', '# x\n')
        self.assertFails('C5')

    def test_missing_status(self):
        self.write('docs/decisions/CR-0001-question.md', '# CR-0001 — Question\n\nno status\n')
        self.replace('STATE.md', '| R-0001 | Adopt', '| CR-0001 | Question | Open | x |\n| R-0001 | Adopt')
        self.assertFails('C5')


class C6OpenItems(RepoTestCase):
    def test_closed_item_left_in_state(self):
        self.replace('STATE.md', 'OI-0001 [Open]', 'OI-0001 [Closed]')
        self.assertFails('C6')

    def test_malformed_item(self):
        self.replace('STATE.md', '- OI-0001 [Open]', '- Item one: open')
        self.assertFails('C6')

    def test_duplicate_item(self):
        self.replace('STATE.md', '- OI-0001 [Open] Fill', '- OI-0001 [Blocked] Other — x\n- OI-0001 [Open] Fill')
        self.assertFails('C6')


class C7Pins(RepoTestCase):
    def test_edited_pinned_decision(self):
        self.replace(R1, 'A single handover file', 'One single handover file')
        self.assertFails('C7')

    def test_missing_pinned_file(self):
        import os
        os.remove(self.path(R1))
        self.assertFails('C7')


class C8Ledgers(RepoTestCase):
    def test_banner_ahead_of_ledger(self):
        self.set_banner_tag('v1')
        self.assertFails('C8')

    def test_unlisted_ledger(self):
        self.write('docs/history/LEDGER_0002.md', '# LEDGER_0002\n\nPredecessor: LEDGER_0001.md\n')
        self.assertFails('C8')

    def test_open_ledger_over_limit(self):
        self.set_config(ledger_max_bytes=600)
        self.replace('docs/history/LEDGER_0001.md', '## v0 —', '## v-1 — 2026-10-19 — Before\n\nx\n\n## v0 —')
        self.assertFails('C8')

    def test_hand_written_duplicate_tag(self):
        text = self.read('docs/history/LEDGER_0001.md')
        self.write('docs/history/LEDGER_0001.md', text + '\n## v0 — 2026-10-21 — Again\n\nx\n')
        self.assertFails('C8')


class C9Directives(RepoTestCase):
    def test_directive_over_cap(self):
        self.set_config(directive_max_bytes=500)
        self.assertFails('C9')

    def test_unlisted_evidence(self):
        self.write_bytes('directives/v0/evidence/owner.txt', b'I approve.\r\n')
        self.assertFails('C9')

    def test_changed_evidence(self):
        code, _ = self.tool('evidence-add', '--tag', 'v0', self.record('I approve.\n'), '--name', 'owner.txt')
        self.assertEqual(code, 0)
        self.assertPasses()
        self.write_bytes('directives/v0/evidence/owner.txt', b'I approve!\n')
        self.assertFails('C9')

    def test_directive_folder_without_directive(self):
        self.write('directives/v1/notes.txt', 'x\n')
        self.assertFails('C9')


class C10Links(RepoTestCase):
    def test_broken_link(self):
        self.replace('README.md', '## Layout', 'See [the plan](docs/design/plan.md).\n\n## Layout')
        self.assertFails('C10')

    def test_links_in_code_are_ignored(self):
        self.replace('README.md', '## Layout', '`[x](missing.md)`\n\n## Layout')
        self.assertPasses()


class C11Hygiene(RepoTestCase):
    def test_extra_root_markdown(self):
        self.write('STEP5_DESIGN.md', '# design\n')
        self.assertFails('C11')

    def test_revision_copy_in_design(self):
        self.write('docs/design/matching_REV3.md', '# design\n')
        self.assertFails('C11')

    def test_readme_with_state(self):
        self.replace('README.md', '## Layout', 'CURRENT BASELINE: tag v0 (2026-10-20) — x\n\n## Layout')
        self.assertFails('C11')

    def test_readme_over_cap(self):
        self.set_config(readme_max_bytes=200)
        self.assertFails('C11')

    def test_badly_named_report(self):
        self.write('reports/notes.md', '# notes\n')
        self.assertFails('C11')


class C12Text(RepoTestCase):
    def test_crlf(self):
        self.write_bytes('docs/design/topic.md', b'# Topic\r\n')
        self.assertFails('C12')

    def test_bom(self):
        self.write_bytes('docs/design/topic.md', b'\xef\xbb\xbf# Topic\n')
        self.assertFails('C12')

    def test_nul(self):
        self.write_bytes('docs/design/topic.md', b'# Topic\x00\n')
        self.assertFails('C12')

    def test_evidence_is_exempt(self):
        import os
        src = os.path.join(self.tmp, 'raw.txt')
        with open(src, 'wb') as f:
            f.write(b'Windows text\r\n')
        code, _ = self.tool('evidence-add', '--tag', 'v0', src)
        self.assertEqual(code, 0)
        self.assertPasses()


if __name__ == '__main__':
    unittest.main()
