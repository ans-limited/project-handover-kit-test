"""verify-github evaluation, with responses shaped like GitHub's documented REST API (repos, rules
for a branch, rulesets, CODEOWNERS errors, contents). A live run is recorded separately; these
tests pin the evaluation logic."""
import base64
import copy
import json
import os
import unittest

from helpers import TOOLS_DIR, check_docs  # noqa: F401  (sets sys.path)
import verify_github as vg

REPO = 'acme/project'


def policy(trunk='main', routine=True):
    with open(os.path.join(TOOLS_DIR, 'policy.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    cfg['trunk'] = trunk
    cfg['owner'] = '@owner-test'
    cfg['review_policy']['owner_approval_routine'] = routine
    return cfg


def b64(text):
    return {'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()}


def compliant(cfg):
    trunk = cfg['trunk']
    app = cfg['github_profile']['actions_app_id']
    rules = [
        {'type': 'pull_request', 'ruleset_id': 1, 'parameters': {
            'require_code_owner_review': True, 'dismiss_stale_reviews_on_push': True,
            'require_last_push_approval': True,
            'required_approving_review_count': 1 if cfg['review_policy']['owner_approval_routine'] else 0,
            'required_review_thread_resolution': False, 'allowed_merge_methods': ['squash']}},
        {'type': 'required_status_checks', 'ruleset_id': 1, 'parameters': {
            'strict_required_status_checks_policy': True,
            'required_status_checks': [{'context': 'handover-gate', 'integration_id': app},
                                       {'context': 'handover-tests', 'integration_id': app}]}},
        {'type': 'required_linear_history', 'ruleset_id': 1},
        {'type': 'non_fast_forward', 'ruleset_id': 1},
        {'type': 'deletion', 'ruleset_id': 1},
    ]
    branch_rs = {'id': 1, 'name': 'trunk', 'target': 'branch', 'enforcement': 'active', 'bypass_actors': [],
                 'conditions': {'ref_name': {'include': ['~DEFAULT_BRANCH'], 'exclude': []}}, 'rules': rules}
    tag_rs = {'id': 2, 'name': 'tags', 'target': 'tag', 'enforcement': 'active', 'bypass_actors': [],
              'conditions': {'ref_name': {'include': ['refs/tags/v*'], 'exclude': []}},
              'rules': [{'type': 'creation'}, {'type': 'update'}, {'type': 'deletion'}]}
    q = trunk
    return {
        f'/repos/{REPO}': (200, {'full_name': REPO, 'default_branch': trunk}),
        f'/repos/{REPO}/rules/branches/{q}': (200, rules),
        f'/repos/{REPO}/rulesets?includes_parents=true&per_page=100': (200, [{'id': 1}, {'id': 2}]),
        f'/repos/{REPO}/rulesets/1': (200, branch_rs),
        f'/repos/{REPO}/rulesets/2': (200, tag_rs),
        f'/repos/{REPO}/codeowners/errors?ref={q}': (200, {'errors': []}),
        f'/repos/{REPO}/contents/.github/CODEOWNERS?ref={q}': (200, b64(check_docs.codeowners_text(cfg))),
        f'/repos/{REPO}/contents/tools/policy.json?ref={q}': (200, b64(json.dumps(cfg))),
    }


def run(responses, cfg):
    return vg.run(REPO, cfg, check_docs.codeowners_text, fetch=lambda p: responses.get(p, (404, None)))


def statuses(results):
    return {item: status for item, status, detail in results}


class VerifyGithub(unittest.TestCase):
    def test_compliant_repository_passes_except_identity(self):
        cfg = policy()
        results, code = run(compliant(cfg), cfg)
        st = statuses(results)
        self.assertEqual({k: v for k, v in st.items() if v != vg.PASS}, {'agent uses a separate identity': vg.UNKNOWN})
        self.assertEqual(code, 3)

    def test_each_deviation_fails(self):
        cfg = policy()

        def pr_params(r):
            return next(x for x in r[f'/repos/{REPO}/rules/branches/main'][1] if x['type'] == 'pull_request')['parameters']

        def checks(r):
            return next(x for x in r[f'/repos/{REPO}/rules/branches/main'][1]
                        if x['type'] == 'required_status_checks')['parameters']

        def drop(r, t):
            r[f'/repos/{REPO}/rules/branches/main'] = (200, [x for x in r[f'/repos/{REPO}/rules/branches/main'][1]
                                                              if x['type'] != t])
        cases = {
            'default branch is the trunk': lambda r: r.update({f'/repos/{REPO}': (200, {'full_name': REPO, 'default_branch': 'dev'})}),
            'Code Owner review required': lambda r: pr_params(r).update(require_code_owner_review=False),
            'stale approvals dismissed': lambda r: pr_params(r).update(dismiss_stale_reviews_on_push=False),
            'approval of the most recent push required': lambda r: pr_params(r).update(require_last_push_approval=False),
            'required approvals = 1': lambda r: pr_params(r).update(required_approving_review_count=0),
            'squash merging only': lambda r: pr_params(r).update(allowed_merge_methods=['squash', 'merge']),
            'required check handover-gate': lambda r: checks(r)['required_status_checks'][0].update(integration_id=999),
            'branches must be up to date': lambda r: checks(r).update(strict_required_status_checks_policy=False),
            'linear history': lambda r: drop(r, 'required_linear_history'),
            'force-push blocked': lambda r: drop(r, 'non_fast_forward'),
            'deletion blocked': lambda r: drop(r, 'deletion'),
            'ruleset 1 (trunk) has no bypass': lambda r: r[f'/repos/{REPO}/rulesets/1'][1].update(
                bypass_actors=[{'actor_id': 5, 'actor_type': 'RepositoryRole', 'bypass_mode': 'always'}]),
            'v* tags protected': lambda r: r[f'/repos/{REPO}/rulesets/2'][1].update(enforcement='disabled'),
            'CODEOWNERS has no errors': lambda r: r.update({f'/repos/{REPO}/codeowners/errors?ref=main': (200, {'errors': [{'line': 3}]})}),
            'CODEOWNERS matches the policy': lambda r: r.update({f'/repos/{REPO}/contents/.github/CODEOWNERS?ref=main': (200, b64('* @someone\n'))}),
        }
        for item, mutate in cases.items():
            with self.subTest(item=item):
                r = copy.deepcopy(compliant(cfg))
                mutate(r)
                results, code = run(r, cfg)
                self.assertEqual(statuses(results).get(item), vg.FAIL, results)
                self.assertEqual(code, 1)

    def test_pull_request_rule_missing_fails(self):
        cfg = policy()
        r = compliant(cfg)
        r[f'/repos/{REPO}/rules/branches/main'] = (200, [x for x in r[f'/repos/{REPO}/rules/branches/main'][1]
                                                          if x['type'] != 'pull_request'])
        self.assertEqual(statuses(run(r, cfg)[0])['pull request required'], vg.FAIL)

    def test_routine_policy_sets_the_expected_approval_count(self):
        cfg = policy(routine=False)
        results, code = run(compliant(cfg), cfg)
        self.assertEqual(statuses(results)['required approvals = 0'], vg.PASS)

    def test_anonymous_read_reports_unknown_not_pass(self):
        """GitHub returns bypass_actors only to users with write access; without it the item is UNKNOWN."""
        cfg = policy()
        r = compliant(cfg)
        del r[f'/repos/{REPO}/rulesets/1'][1]['bypass_actors']
        st = statuses(run(r, cfg)[0])
        self.assertEqual(st['ruleset 1 (trunk) has no bypass'], vg.UNKNOWN)

    def test_merge_methods_unknown_without_rule_or_visible_flags(self):
        cfg = policy()
        r = compliant(cfg)
        next(x for x in r[f'/repos/{REPO}/rules/branches/main'][1] if x['type'] == 'pull_request')['parameters'].pop('allowed_merge_methods')
        self.assertEqual(statuses(run(r, cfg)[0])['squash merging only'], vg.UNKNOWN)
        r[f'/repos/{REPO}'][1].update(allow_squash_merge=True, allow_merge_commit=False, allow_rebase_merge=False)
        self.assertEqual(statuses(run(r, cfg)[0])['squash merging only'], vg.PASS)

    def test_gh_transport_parses_status_and_body(self):
        """gh api --include prints the status line, headers, a blank line, then the body."""
        import subprocess
        import sys
        import tempfile
        d = tempfile.mkdtemp(prefix='fake-gh-')
        fake = os.path.join(d, 'fake_gh.py')
        with open(fake, 'w', encoding='utf-8') as f:
            f.write('import sys\n'
                    'path = sys.argv[-1]\n'
                    'if path.endswith("missing"):\n'
                    '    sys.stdout.buffer.write(b"HTTP/2.0 404 Not Found\\r\\nContent-Type: application/json\\r\\n\\r\\n{\\"message\\": \\"Not Found\\"}")\n'
                    '    sys.exit(1)\n'
                    'sys.stdout.buffer.write(b"HTTP/2.0 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n{\\"full_name\\": \\"acme/project\\"}")\n')
        fetch = vg.gh_fetch([sys.executable, fake])
        self.assertEqual(fetch('/repos/acme/project'), (200, {'full_name': 'acme/project'}))
        self.assertEqual(fetch('/repos/acme/missing')[0], 404)

    def test_unreadable_repository_fails(self):
        cfg = policy()
        results, code = run({}, cfg)
        self.assertEqual(results[0][1], vg.FAIL)
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
