"""The trunk branch is configurable (policy "trunk"). init takes it from the new repository's own
branch, so `git init -b main` gives trunk main (GitHub's default for new repositories)."""
import unittest

from helpers import REPO_NAME, GitRepoCase, check_docs


class TrunkMain(GitRepoCase):
    TRUNK = 'main'

    def test_init_rewrites_policy_workflows_and_documents(self):
        self.assertEqual(check_docs.load_policy(self.root)['trunk'], 'main')
        self.assertIn('branches: [main]', self.read('.github/workflows/handover-gate.yml'))
        self.assertIn('branches: [main]', self.read('.github/workflows/handover-audit.yml'))
        self.assertIn('refs/remotes/origin/main', self.read('.github/workflows/handover-tags.yml'))
        for relp in ['CLAUDE.md', 'docs/GITHUB_SETUP.md', 'directives/v0/DIRECTIVE.md',
                     'docs/decisions/R-0002-checkpoints-as-pull-requests.md']:
            with self.subTest(file=relp):
                self.assertNotIn('`master`', self.read(relp))
        self.assertIn('`main`', self.read('CLAUDE.md'))
        self.assertPasses(expect_tag='v0')

    def test_routine_and_checkpoint_prs_into_main(self):
        self.branch('routine/ui')
        self.write('src/app.txt', 'x\n')
        self.commit('ui')
        self.assertPRPasses('routine/ui')
        self.checkout('main')
        n = self.full_checkpoint(tier=1)
        info = self.assertPRPasses(f'checkpoint/v{n}')
        self.assertEqual(info['N'], 1)

    def test_hook_refuses_commits_on_main_only(self):
        self.write('src/app.txt', 'x\n')
        ok, out = self.try_commit('on main')
        self.assertFalse(ok)
        self.assertIn('main', out)
        self.branch('routine/ok')
        ok, out = self.try_commit('on a branch')
        self.assertTrue(ok, out)

    def test_audit_uses_the_trunk(self):
        n = self.full_checkpoint(tier=1)
        b = f'checkpoint/v{n}'
        head = self.g('rev-parse', b)
        c = self.squash_merge(b)
        reviews = [self.review('APPROVED', head, '2026-10-24T09:00:00Z')]
        self.assertEqual(check_docs.audit(self.root, c, self.pr_json(c, b), reviews, REPO_NAME).items, [])
        f = check_docs.audit(self.root, c, self.pr_json(c, b, base_ref='master'), reviews, REPO_NAME)
        self.assertIn('A', {x[0] for x in f.items})

    def test_tag_check_follows_the_trunk(self):
        n = self.full_checkpoint(tier=1)
        c = self.squash_merge(f'checkpoint/v{n}')
        self.assertEqual(check_docs.tag_check(self.root, 'v1', c).items, [])
        self.assertEqual(check_docs.tag_check(self.root, 'v0', self.root_commit).items, [])

    def test_gate_must_target_the_trunk(self):
        self.replace('.github/workflows/handover-gate.yml', 'branches: [main]', 'branches: [master]')
        self.assertFails('C14')

    def test_invalid_trunk_name_is_rejected_by_policy_validation(self):
        self.set_config(trunk='bad..name')
        self.assertFails('C15')


class TrunkMasterDefault(GitRepoCase):
    def test_master_repositories_are_unchanged(self):
        self.assertEqual(check_docs.load_policy(self.root)['trunk'], 'master')
        self.assertIn('branches: [master]', self.read('.github/workflows/handover-gate.yml'))
        self.assertPasses(expect_tag='v0')


if __name__ == '__main__':
    unittest.main()
