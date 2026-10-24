"""Shared fixtures. RepoTestCase: a fresh copy of this repository in a temp folder (no git).
GitRepoCase: the same copy as a git repository after init and the root commit, so pull requests,
the hook, the audit and tags can be exercised with real git."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, TOOLS_DIR)

import check_docs  # noqa: E402
import handover  # noqa: E402

SKIP = shutil.ignore_patterns('.git', '__pycache__', '.venv', 'node_modules', 'vendor')
OWNER = 'owner-test'
REPO_NAME = 'acme/project'
TEMPLATE_DIR = os.path.join(TESTS_DIR, 'template')

# The kit's own files, copied from the repository the tests run in (code, hook, templates)...
FIXTURE_COPY = ['tools', '.githooks', '.gitattributes', '.gitignore', 'AGENTS.md', 'directives/_TEMPLATE']
FIXTURE_GLOBS = [('reports', '_TEMPLATE_'), ('docs/decisions', '_TEMPLATE_'), ('docs/design', '_TEMPLATE_')]
# ...and every file that init or a checkpoint changes, from the pristine template in tools/tests/template/.


def build_fixture(src_root, dest):
    """A pristine, uninitialised copy of the kit for the tests, built from src_root's code and the
    stored template. Works in the kit's own source and in a project that has adopted the kit, where
    init has already run and checkpoints have changed STATE.md, the ledgers and the records."""
    os.makedirs(dest)
    for rel_path in FIXTURE_COPY:
        s = os.path.join(src_root, *rel_path.split('/'))
        d = os.path.join(dest, *rel_path.split('/'))
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=SKIP)
        elif os.path.isfile(s):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
    for folder, prefix in FIXTURE_GLOBS:
        s = os.path.join(src_root, *folder.split('/'))
        if os.path.isdir(s):
            os.makedirs(os.path.join(dest, *folder.split('/')), exist_ok=True)
            for name in os.listdir(s):
                if name.startswith(prefix):
                    shutil.copy2(os.path.join(s, name), os.path.join(dest, *folder.split('/'), name))
    for dp, dns, fns in os.walk(TEMPLATE_DIR):
        for fn in fns:
            rel_path = os.path.relpath(os.path.join(dp, fn), TEMPLATE_DIR)
            d = os.path.join(dest, rel_path)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(os.path.join(dp, fn), d)


class Result:
    """Adapter so snapshot tests read failures as (code, message) pairs."""

    def __init__(self, findings):
        self.findings = findings
        self.fails = [(c, m) for c, s, m, p in findings.items]
        self.warns = findings.warns


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='handover-test-')
        self.root = os.path.join(self.tmp, 'repo')
        build_fixture(REPO_ROOT, self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- file helpers
    def path(self, relp):
        return os.path.join(self.root, *relp.split('/'))

    def exists(self, relp):
        return os.path.exists(self.path(relp))

    def read(self, relp):
        with open(self.path(relp), 'r', encoding='utf-8', newline='') as f:
            return f.read()

    def write(self, relp, text):
        p = self.path(relp)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write(text)

    def write_bytes(self, relp, b):
        p = self.path(relp)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(b)

    def remove(self, relp):
        p = self.path(relp)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)

    def replace(self, relp, old, new):
        text = self.read(relp)
        self.assertIn(old, text, f'fixture text not found in {relp}')
        self.write(relp, text.replace(old, new, 1))

    def set_config(self, **changes):
        cfg = json.loads(self.read('tools/policy.json'))
        cfg.update(changes)
        self.write('tools/policy.json', json.dumps(cfg, indent=2, ensure_ascii=False) + '\n')

    def set_banner_tag(self, tag):
        lines = self.read('STATE.md').split('\n')
        for i, l in enumerate(lines):
            if l.startswith('CURRENT BASELINE:'):
                lines[i] = f'CURRENT BASELINE: tag {tag} (2026-10-21) — Test checkpoint {tag}'
        self.write('STATE.md', '\n'.join(lines))

    # ---- running the tools
    def check(self, expect_tag=None):
        snap = check_docs.Snapshot(self.root, check_docs.load_policy(self.root), expect_tag=expect_tag)
        return Result(snap.run())

    def failed(self, expect_tag=None):
        return {c for c, _ in self.check(expect_tag).fails}

    def assertPasses(self, expect_tag=None):
        self.assertEqual(self.check(expect_tag).fails, [], 'expected RESULT PASS')

    def assertFails(self, check_id, expect_tag=None):
        ch = self.check(expect_tag)
        got = {c for c, _ in ch.fails}
        self.assertIn(check_id, got, f'expected {check_id} to fail; failures: {ch.fails}')
        return ch

    def tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = handover.main(['--root', self.root] + list(args))
        return code, out.getvalue()

    def record(self, text='Record body.\n', name='record.md'):
        p = os.path.join(self.tmp, name)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        return p


class GitRepoCase(RepoTestCase):
    """After setUp: a git repository on its trunk (TRUNK) with the root commit (v0), init done, hook enabled."""

    POLICY = {}   # policy overrides applied before the root commit
    TRUNK = 'master'   # the branch init is run on, and so the policy's trunk

    def setUp(self):
        super().setUp()
        if self.POLICY:
            self.set_config(**self.POLICY)
        self.g('init', '-q', '-b', self.TRUNK)
        self.g('config', 'user.name', 'agent')
        self.g('config', 'user.email', 'agent@example.invalid')
        code, out = self.tool('init', '--project', 'Test Project', '--owner', OWNER)
        self.assertEqual(code, 0, out)
        self.root_commit = self.commit('v0: initialise', verify=False)
        self.pr_numbers = {}

    # ---- git
    def g(self, *args, ok=True):
        p = subprocess.run(['git', '-C', self.root] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if ok and p.returncode != 0:
            raise AssertionError(f'git {" ".join(args)} failed: {p.stderr.decode("utf-8", "replace")}')
        return p.stdout.decode('utf-8', 'replace').strip() if ok else p

    def head(self):
        return self.g('rev-parse', 'HEAD')

    def commit(self, msg, verify=False, allow_empty=False):
        self.g('add', '-A')
        args = ['commit', '-q', '-m', msg]
        if not verify:
            args.append('--no-verify')
        if allow_empty:
            args.append('--allow-empty')
        self.g(*args)
        return self.head()

    def try_commit(self, msg, add=True):
        """Commit through the pre-commit hook; returns (ok, output)."""
        if add:
            self.g('add', '-A')
        p = self.g('commit', '-q', '-m', msg, ok=False)
        return p.returncode == 0, (p.stdout + p.stderr).decode('utf-8', 'replace')

    def branch(self, name, start=None):
        start = start or self.TRUNK
        self.g('checkout', '-q', '-b', name, start)

    def checkout(self, name):
        self.g('checkout', '-q', name)

    def squash_merge(self, branch, msg=None):
        """Simulates GitHub's squash merge of a PR; returns the new master commit."""
        head = self.g('rev-parse', branch)
        self.checkout(self.TRUNK)
        self.g('merge', '-q', '--squash', branch)
        c = self.commit(msg or f'merge {branch}', allow_empty=True)
        n = self.pr_numbers.setdefault(branch, len(self.pr_numbers) + 1)
        self.g('update-ref', f'refs/pull/{n}/head', head)
        return c

    # ---- checks
    def pr(self, branch, base=None):
        base = base or self.TRUNK
        return check_docs.pr_check(self.root, base, branch)

    def pr_codes(self, branch, base=None):
        f, info = self.pr(branch, base)
        return {c for c, s, m, p in f.items}, f, info

    def assertPRPasses(self, branch, base=None):
        f, info = self.pr(branch, base)
        self.assertEqual([(c, m) for c, s, m, p in f.items], [], f'expected the PR check to pass ({info})')
        return info

    def assertPRFails(self, branch, code, base=None):
        codes, f, info = self.pr_codes(branch, base)
        self.assertIn(code, codes, f'expected {code}; got {[(c, m) for c, s, m, p in f.items]}')
        return f

    def staged(self):
        return check_docs.staged_check(self.root)

    def local_blocking(self):
        return [(c, s, m) for c, s, m, p in self.staged().blocking(local=True)]

    # ---- building checkpoints
    def next_n(self):
        code, out = self.tool('next')
        self.assertEqual(code, 0, out)
        return int(out.strip()[1:])

    def start_checkpoint(self, tier=1, branch=None):
        n = self.next_n()
        self.branch(branch or f'checkpoint/v{n}')
        code, out = self.tool('new-directive', '--tag', f'v{n}', '--tier', str(tier), '--title', f'Checkpoint v{n}')
        self.assertEqual(code, 0, out)
        return n

    def close_checkpoint(self, n, body='Record body.\n'):
        self.set_banner_tag(f'v{n}')
        code, out = self.tool('ledger-append', '--tag', f'v{n}', '--title', f'Checkpoint v{n}',
                              '--date', '2026-10-21', '--record', self.record(body, f'rec{n}.md'))
        self.assertEqual(code, 0, out)
        self.write(f'reports/SESSION_REPORT_v{n}.md', f'# SESSION_REPORT_v{n}.md\n\nReport.\n')

    def add_verification(self, n, verified=None, result='PASS'):
        v = verified or self.head()
        self.write(f'reports/VERIFICATION_v{n}.md',
                   f'# VERIFICATION_v{n}.md\n\n**Verifier:** independent reviewer\n**Verified commit:** {v}\n'
                   f'**Result:** {result}\n**Scope:** the whole PR\n')
        return v

    def full_checkpoint(self, tier=1, work=None, verify=True, body='Record body.\n', work_after_close=None):
        """Branch, directive, optional work, closing records, commit; Tier 2 gets a verification commit."""
        n = self.start_checkpoint(tier)
        if work:
            work(n)
        self.close_checkpoint(n, body)
        if work_after_close:
            work_after_close(n)
        self.commit(f'checkpoint v{n}')
        if tier == 2 and verify:
            self.add_verification(n)
            self.commit(f'verification v{n}')
        return n

    # ---- incomplete history (fix-check finding F2)
    def shallow_clone(self, branch):
        """A --depth=1 clone of branch: its tip still records a parent that the clone does not have."""
        from pathlib import Path
        clone = os.path.join(self.tmp, f'shallow-{branch.replace("/", "-")}')
        subprocess.run(['git', 'clone', '-q', '--depth=1', '--branch', branch, Path(self.root).as_uri(), clone],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return clone

    def commit_with_missing_parent(self):
        """A commit object whose header names a parent object that does not exist."""
        tree = self.g('rev-parse', 'HEAD^{tree}')
        body = (f'tree {tree}\nparent {"1" * 40}\nauthor a <a@example.invalid> 0 +0000\n'
                'committer a <a@example.invalid> 0 +0000\n\nparent missing\n')
        return subprocess.run(['git', '-C', self.root, 'hash-object', '-t', 'commit', '--literally', '-w', '--stdin'],
                              input=body.encode(), stdout=subprocess.PIPE, check=True).stdout.decode().strip()

    # ---- recorded API responses for the audit
    def pr_json(self, c, branch, number=None, repo=REPO_NAME, base_ref=None, merged=True):
        base_ref = base_ref or self.TRUNK
        n = number or self.pr_numbers.get(branch, 1)
        return [{'number': n, 'merge_commit_sha': c, 'merged_at': '2026-10-21T10:00:00Z' if merged else None,
                 'head': {'sha': self.g('rev-parse', branch)},
                 'base': {'ref': base_ref, 'repo': {'full_name': repo}}}]

    def review(self, state, commit, at, login=OWNER, rid=1):
        return {'id': rid, 'user': {'login': login}, 'state': state, 'commit_id': commit, 'submitted_at': at}
