"""Master audit from recorded API responses (design note s7.4; acceptance criterion AC2),
tag validation (s7.5) and the bootstrap sequence (s9)."""
import contextlib
import io
import json
import os
import subprocess
import unittest

from helpers import OWNER, REPO_NAME, GitRepoCase, RepoTestCase, check_docs


class Audit(GitRepoCase):
    def merged(self, tier=1, approve=True):
        n = self.full_checkpoint(tier=tier, work=lambda n: self.write('src/a.php', '<?php\n'))
        branch = f'checkpoint/v{n}'
        head = self.g('rev-parse', branch)
        c = self.squash_merge(branch)
        reviews = [self.review('APPROVED', head, '2026-10-21T09:00:00Z')] if approve else []
        return c, branch, head, reviews

    def audit(self, c, prs, reviews):
        return {x[0] for x in check_docs.audit(self.root, c, prs, reviews, REPO_NAME).items}, \
            check_docs.audit(self.root, c, prs, reviews, REPO_NAME)

    def assertAuditPasses(self, c, prs, reviews):
        f = check_docs.audit(self.root, c, prs, reviews, REPO_NAME)
        self.assertEqual([x[:3] for x in f.items], [])

    def assertAuditFails(self, c, prs, reviews, code='A'):
        f = check_docs.audit(self.root, c, prs, reviews, REPO_NAME)
        self.assertIn(code, {x[0] for x in f.items}, [x[:3] for x in f.items])
        return f

    def test_valid_checkpoint_passes(self):
        c, b, h, r = self.merged()
        self.assertAuditPasses(c, self.pr_json(c, b), r)

    def test_tier2_verification_is_checked_against_the_pr_head(self):
        c, b, h, r = self.merged(tier=2)
        self.assertAuditPasses(c, self.pr_json(c, b), r)

    def test_identification_failures(self):
        c, b, h, r = self.merged()
        good = self.pr_json(c, b)[0]
        cases = {
            'no PR': [],
            'two PRs': [good, dict(good, number=99)],
            'other repository': self.pr_json(c, b, repo='someone/else'),
            'other target branch': self.pr_json(c, b, base_ref='develop'),
            'not merged': self.pr_json(c, b, merged=False),
            'other merge commit': [dict(good, merge_commit_sha=self.root_commit)],
        }
        for name, prs in cases.items():
            with self.subTest(case=name):
                self.assertAuditFails(c, prs, r)

    def test_fetched_head_differs_from_recorded_head(self):
        c, b, h, r = self.merged()
        self.g('update-ref', f'refs/pull/{self.pr_numbers[b]}/head', self.root_commit)
        self.assertAuditFails(c, self.pr_json(c, b), r)

    def test_landed_content_differs(self):
        c, b, h, r = self.merged()
        self.write('src/extra.php', '<?php\n')
        c2 = self.commit('different content landed', verify=False)
        self.g('update-ref', 'refs/heads/master', c2)
        f = self.assertAuditFails(c2, [dict(self.pr_json(c, b)[0], merge_commit_sha=c2)], r)
        self.assertTrue(any('differs from the approved PR head' in m for x, s, m, p in f.items))

    def test_approval_problems(self):
        c, b, h, r = self.merged()
        earlier = self.g('rev-parse', f'{b}~1')
        cases = {
            'missing': [],
            'dismissed': [self.review('DISMISSED', h, '2026-10-21T09:00:00Z')],
            'superseded by change request': [self.review('APPROVED', h, '2026-10-21T09:00:00Z'),
                                             self.review('CHANGES_REQUESTED', h, '2026-10-21T09:05:00Z', rid=2)],
            'on different content': [self.review('APPROVED', earlier, '2026-10-21T09:00:00Z')],
            'by someone else': [self.review('APPROVED', h, '2026-10-21T09:00:00Z', login='agent')],
        }
        for name, reviews in cases.items():
            with self.subTest(case=name):
                self.assertAuditFails(c, self.pr_json(c, b), reviews)

    def test_comment_after_approval_does_not_supersede(self):
        c, b, h, r = self.merged()
        reviews = r + [self.review('COMMENTED', h, '2026-10-21T09:10:00Z', rid=3)]
        self.assertAuditPasses(c, self.pr_json(c, b), reviews)

    def test_approval_on_identical_content_passes(self):
        n = self.full_checkpoint(tier=1)
        b = f'checkpoint/v{n}'
        approved = self.g('rev-parse', b)
        self.checkout(b)
        self.commit('empty after approval', allow_empty=True)
        c = self.squash_merge(b)
        self.assertAuditPasses(c, self.pr_json(c, b), [self.review('APPROVED', approved, '2026-10-21T09:00:00Z')])

    def test_routine_pr_needs_approval_under_current_policy(self):
        self.branch('routine/ui')
        self.write('src/app.php', '<?php\n')
        self.commit('ui')
        c = self.squash_merge('routine/ui')
        self.assertAuditFails(c, self.pr_json(c, 'routine/ui'), [])

    def test_two_parent_commit_fails(self):
        self.branch('routine/ui')
        self.write('src/app.php', '<?php\n')
        self.commit('ui')
        self.checkout('master')
        self.g('merge', '-q', '--no-ff', '--no-edit', 'routine/ui')
        self.assertAuditFails(self.head(), [], [])

    def test_root_commit_audit_uses_bootstrap_rules(self):
        f = check_docs.audit(self.root, self.root_commit, [], [], REPO_NAME)
        self.assertEqual([x[:3] for x in f.items], [])

    def test_cli(self):
        c, b, h, r = self.merged()
        prs, rev = os.path.join(self.tmp, 'prs.json'), os.path.join(self.tmp, 'reviews.json')
        json.dump(self.pr_json(c, b), open(prs, 'w'))
        json.dump(r, open(rev, 'w'))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = check_docs.main(['--repo', self.root, '--audit', '--commit', c, '--prs', prs,
                                    '--reviews', rev, '--repo-name', REPO_NAME])
        self.assertEqual(code, 0, out.getvalue())


class IncompleteHistory(GitRepoCase):
    """Fix-check finding F2: root status comes from the raw commit header, and history-dependent checks
    refuse shallow clones and missing parents instead of treating a commit as a root commit."""

    def child_commit(self):
        self.branch('child')
        self.write('src/app.php', '<?php\n')
        return self.commit('child')

    def test_shallow_non_root_audit_fails_closed(self):
        child = self.child_commit()
        shallow = self.shallow_clone('child')
        with self.assertRaises(check_docs.GateError) as e:
            check_docs.audit(shallow, child, [], [], REPO_NAME)
        self.assertIn('shallow', str(e.exception))

    def test_missing_parent_audit_fails_closed(self):
        c = self.commit_with_missing_parent()
        with self.assertRaises(check_docs.GateError) as e:
            check_docs.audit(self.root, c, [], [], REPO_NAME)
        self.assertIn('not available', str(e.exception))

    def test_complete_non_root_is_not_treated_as_root(self):
        child = self.child_commit()
        f = check_docs.audit(self.root, child, [], [], REPO_NAME)
        self.assertIn('A', {x[0] for x in f.items})          # no PR found: alert, not bootstrap rules

    def test_genuine_root_still_uses_bootstrap_rules(self):
        self.assertEqual(check_docs.commit_parents(self.root, self.root_commit), [])
        self.assertEqual(check_docs.audit(self.root, self.root_commit, [], [], REPO_NAME).items, [])

    def test_pr_check_and_tag_check_refuse_a_shallow_clone(self):
        n = self.full_checkpoint(tier=1)
        shallow = self.shallow_clone(f'checkpoint/v{n}')
        with self.assertRaises(check_docs.GateError):
            check_docs.pr_check(shallow, 'HEAD', 'HEAD')
        with self.assertRaises(check_docs.GateError):
            check_docs.tag_check(shallow, 'v1', 'HEAD')

    def test_cli_exit_code_is_2(self):
        child = self.child_commit()
        shallow = self.shallow_clone('child')
        prs, rev = os.path.join(self.tmp, 'p.json'), os.path.join(self.tmp, 'r.json')
        for p in (prs, rev):
            with open(p, 'w') as fh:
                fh.write('[]')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = check_docs.main(['--repo', shallow, '--audit', '--commit', child, '--prs', prs,
                                    '--reviews', rev, '--repo-name', REPO_NAME])
        self.assertEqual(code, 2, out.getvalue())


class AuditRoutineWithoutApproval(GitRepoCase):
    POLICY = {'review_policy': {'owner_approval_checkpoint': True, 'owner_approval_routine': False}}

    def test_routine_pr_passes_without_approval_when_policy_allows(self):
        self.branch('routine/ui')
        self.write('src/app.php', '<?php\n')
        self.commit('ui')
        c = self.squash_merge('routine/ui')
        f = check_docs.audit(self.root, c, self.pr_json(c, 'routine/ui'), [], REPO_NAME)
        self.assertEqual([x[:3] for x in f.items], [])


class Tags(GitRepoCase):
    def test_tag_rules(self):
        n = self.full_checkpoint(tier=1)
        cp = self.squash_merge(f'checkpoint/v{n}')
        self.branch('routine/ui')
        self.write('src/app.php', '<?php\n')
        self.commit('ui')
        routine = self.squash_merge('routine/ui')
        self.branch('side')
        self.write('x.txt', 'x\n')
        side = self.commit('side')
        cases = [('v1', cp, True), ('v1', routine, False), ('v0', self.root_commit, True), ('v0', cp, False),
                 ('v2', routine, False), ('v1', side, False), ('release-1', cp, False)]
        for tag, target, ok in cases:
            with self.subTest(tag=tag, target=target[:8]):
                f = check_docs.tag_check(self.root, tag, target)
                self.assertEqual(not f.items, ok, [x[:3] for x in f.items])


class BootstrapSequence(RepoTestCase):
    """Design note s9, the exact sequence, with the hook active for the root commit."""

    def g(self, *args):
        return subprocess.run(['git', '-C', self.root] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    def test_exact_sequence(self):
        self.assertEqual(self.g('init', '-q', '-b', 'master').returncode, 0)
        self.g('config', 'user.name', 'owner')
        self.g('config', 'user.email', 'owner@example.invalid')
        code, out = self.tool('init', '--project', 'Seq Project', '--owner', OWNER)
        self.assertEqual(code, 0, out)
        with contextlib.redirect_stdout(io.StringIO()) as o:
            self.assertEqual(check_docs.main(['--repo', self.root, '--bootstrap']), 0, o.getvalue())
        self.g('add', '-A')
        p = self.g('commit', '-q', '-m', 'v0: initialise')         # through the hook, in bootstrap mode
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr).decode())
        root = self.g('rev-parse', 'HEAD').stdout.decode().strip()
        self.assertEqual(check_docs.tag_check(self.root, 'v0', root).items, [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_docs.main(['--repo', self.root, '--bootstrap']), 1)

    def test_hook_refuses_a_broken_root_commit(self):
        self.g('init', '-q', '-b', 'master')
        self.g('config', 'user.name', 'owner')
        self.g('config', 'user.email', 'owner@example.invalid')
        self.tool('init', '--project', 'Seq Project', '--owner', OWNER)
        self.write('NOTES.md', 'stray\n')
        self.g('add', '-A')
        p = self.g('commit', '-q', '-m', 'v0')
        self.assertNotEqual(p.returncode, 0)


if __name__ == '__main__':
    unittest.main()
