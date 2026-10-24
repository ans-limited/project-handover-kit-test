"""Workflow shell logic, executed locally (review findings R4, R5, R6).

The marked blocks of .github/workflows/handover-audit.yml are extracted and run with a real POSIX
shell against disposable git repositories. This proves the shell control flow and the shell-to-
Python boundary; it does not prove behaviour on GitHub's runners (live tests in GITHUB_SETUP.md).
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO_NAME, GitRepoCase, check_docs

AUDIT = '.github/workflows/handover-audit.yml'


SHELL_TOOLS = ('base64', 'tr', 'sed')   # used by the workflow blocks besides git and sh


def find_sh():
    """A POSIX sh. On Windows, Git for Windows provides sh.exe and the other tools in its usr/bin
    folder, which is often not on PATH; it is located through git itself."""
    sh = shutil.which('sh')
    if sh:
        return sh
    exec_path = subprocess.run(['git', '--exec-path'], stdout=subprocess.PIPE, check=True).stdout.decode().strip()
    for cand in [os.path.join(exec_path, '..', '..', '..', 'usr', 'bin', 'sh.exe'),
                 os.path.join(exec_path, '..', '..', 'usr', 'bin', 'sh.exe')]:
        if os.path.isfile(cand):
            return os.path.normpath(cand)
    raise AssertionError('no POSIX sh found: these tests need one (Git for Windows provides it)')


def shell_path():
    """PATH for the workflow blocks: the sh folder first (on Windows, Git's usr/bin, which also holds
    base64, tr and sed), then the caller's PATH. Fails clearly when a needed tool is missing."""
    sh_dir = os.path.dirname(find_sh())
    path = sh_dir + os.pathsep + os.environ.get('PATH', '')
    missing = [t for t in SHELL_TOOLS if not shutil.which(t, path=path)]
    if missing:
        raise AssertionError(f'the workflow tests need {", ".join(missing)} on PATH or next to {find_sh()}')
    return path


def commands(workflow_text):
    """Workflow lines that are not comments."""
    return [l for l in workflow_text.split('\n') if l.strip() and not l.strip().startswith('#')]


def block(workflow_text, name):
    """The shell lines between '# BEGIN <name>' and '# END <name>', dedented."""
    lines = workflow_text.split('\n')
    start = next(i for i, l in enumerate(lines) if l.strip().startswith(f'# BEGIN {name}'))
    end = next(i for i, l in enumerate(lines) if l.strip().startswith(f'# END {name}'))
    body = lines[start + 1:end]
    indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
    return '\n'.join(l[indent:] for l in body) + '\n'


class Workflows(GitRepoCase):
    def run_block(self, name, env):
        script = 'set -eu\n' + block(self.read(AUDIT), name)
        path = os.path.join(self.tmp, f'{name}.sh')
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(script)
        e = dict(os.environ, **env)
        e['PATH'] = shell_path()
        p = subprocess.run([find_sh(), path], cwd=self.root, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode('utf-8', 'replace')

    def runner_temp(self):
        d = tempfile.mkdtemp(prefix='runner-', dir=self.tmp)
        os.makedirs(os.path.join(d, 'a'))
        return d

    # ---- R6: trusted-checker selection
    def test_root_commit_uses_its_own_checker(self):
        rt = self.runner_temp()
        code, out = self.run_block('trusted-checker', {'C': self.root_commit, 'RUNNER_TEMP': rt})
        self.assertEqual(code, 0, out)
        self.assertIn('root commit', out)
        with open(os.path.join(rt, 'a', 'check_docs.py'), 'rb') as f:
            self.assertEqual(f.read(), subprocess.run(['git', '-C', self.root, 'show', f'{self.root_commit}:tools/check_docs.py'],
                                                      stdout=subprocess.PIPE).stdout)

    def test_non_root_commit_uses_its_parent_checker(self):
        self.branch('cp')
        self.write('tools/check_docs.py', self.read('tools/check_docs.py') + '# changed in the child\n')
        child = self.commit('child changes the checker')
        rt = self.runner_temp()
        code, out = self.run_block('trusted-checker', {'C': child, 'RUNNER_TEMP': rt})
        self.assertEqual(code, 0, out)
        with open(os.path.join(rt, 'a', 'check_docs.py'), encoding='utf-8') as f:
            self.assertNotIn('# changed in the child', f.read())

    def test_non_root_without_parent_checker_fails_closed(self):
        """The old '|| cp tools/check_docs.py' fallback would have silently used the child's checker."""
        self.branch('cp')
        self.remove('tools/check_docs.py')
        no_checker = self.commit('parent without a checker')
        self.write('tools/check_docs.py', '# the commit under audit\n')
        child = self.commit('child restores a checker')
        rt = self.runner_temp()
        code, out = self.run_block('trusted-checker', {'C': child, 'RUNNER_TEMP': rt})
        self.assertNotEqual(code, 0, out)
        target = os.path.join(rt, 'a', 'check_docs.py')
        self.assertFalse(os.path.isfile(target) and open(target).read() == '# the commit under audit\n')

    def test_shallow_non_root_fails_closed(self):
        """Fix-check finding F2: a shallow clone shows the child as parentless; it must not pass as root."""
        self.branch('child')
        self.write('tools/check_docs.py', self.read('tools/check_docs.py') + '# CHILD_MARKER\n')
        child = self.commit('child changes the checker')
        self.root = self.shallow_clone('child')
        rt = self.runner_temp()
        code, out = self.run_block('trusted-checker', {'C': child, 'RUNNER_TEMP': rt})
        self.assertNotEqual(code, 0, out)
        self.assertNotIn('root commit', out)
        target = os.path.join(rt, 'a', 'check_docs.py')
        self.assertFalse(os.path.isfile(target) and '# CHILD_MARKER' in open(target, encoding='utf-8').read())

    def test_missing_parent_object_fails_closed(self):
        c = self.commit_with_missing_parent()
        rt = self.runner_temp()
        code, out = self.run_block('trusted-checker', {'C': c, 'RUNNER_TEMP': rt})
        self.assertNotEqual(code, 0, out)
        self.assertNotIn('root commit', out)

    def test_shell_prerequisites_are_arranged_by_the_harness(self):
        """On Windows, Git's usr/bin (base64, tr, sed) is often missing from PATH; the harness adds it.
        The fetch block is run with that folder removed from the caller's PATH."""
        sh_dir = os.path.normcase(os.path.normpath(os.path.dirname(find_sh())))
        stripped = os.pathsep.join(p for p in os.environ.get('PATH', '').split(os.pathsep)
                                   if os.path.normcase(os.path.normpath(p)) != sh_dir)
        origin = os.path.join(self.tmp, 'origin.git')
        subprocess.run(['git', 'clone', '-q', '--bare', self.root, origin], check=True)
        subprocess.run(['git', '-C', origin, 'update-ref', 'refs/pull/5/head', self.root_commit], check=True)
        self.g('remote', 'add', 'origin', origin)
        saved = os.environ['PATH']
        os.environ['PATH'] = stripped
        try:
            code, out = self.run_block('authenticated-fetch', {'GH_TOKEN': 'dummy-token', 'N': '5'})
        finally:
            os.environ['PATH'] = saved
        self.assertEqual(code, 0, out)

    # ---- R5: pages at the shell-to-Python boundary
    def merged_checkpoint(self):
        n = self.full_checkpoint(tier=1)
        b = f'checkpoint/v{n}'
        head = self.g('rev-parse', b)
        return self.squash_merge(b), b, head

    def run_audit_cli(self, c, prs_text, reviews_text):
        prs, rev = os.path.join(self.tmp, 'prs.json'), os.path.join(self.tmp, 'reviews.json')
        for p, t in ((prs, prs_text), (rev, reviews_text)):
            with open(p, 'w', encoding='utf-8') as f:
                f.write(t)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = check_docs.main(['--repo', self.root, '--audit', '--commit', c, '--prs', prs,
                                    '--reviews', rev, '--repo-name', REPO_NAME])
        return code, out.getvalue()

    def test_reviews_in_several_pages_are_read(self):
        c, b, head = self.merged_checkpoint()
        prs = json.dumps(self.pr_json(c, b))
        page1 = [self.review('COMMENTED', head, '2026-10-21T08:00:00Z', rid=1)]
        page2 = [self.review('APPROVED', head, '2026-10-21T09:00:00Z', rid=2)]
        for name, text in {'concatenated pages (--paginate)': json.dumps(page1) + '\n' + json.dumps(page2) + '\n',
                           'slurped pages (--paginate --slurp)': json.dumps([page1, page2]),
                           'single array': json.dumps(page1 + page2)}.items():
            with self.subTest(form=name):
                code, out = self.run_audit_cli(c, prs, text)
                self.assertEqual(code, 0, out)

    def test_approval_on_a_later_page_still_counts_as_superseded(self):
        c, b, head = self.merged_checkpoint()
        page1 = [self.review('APPROVED', head, '2026-10-21T09:00:00Z', rid=1)]
        page2 = [self.review('CHANGES_REQUESTED', head, '2026-10-21T09:30:00Z', rid=2)]
        code, out = self.run_audit_cli(c, json.dumps(self.pr_json(c, b)), json.dumps([page1, page2]))
        self.assertEqual(code, 1, out)

    def test_malformed_pages_fail_closed(self):
        c, b, head = self.merged_checkpoint()
        code, out = self.run_audit_cli(c, json.dumps(self.pr_json(c, b)), '{"message": "Not Found"}')
        self.assertEqual(code, 2, out)

    def test_pr_number_selection_reads_slurped_pages(self):
        """The workflow's inline Python that picks the PR number, run on slurped pages."""
        text = self.read(AUDIT)
        code = re.search(r'python3 -c "(.+?)" "\$RUNNER_TEMP/a/prs.json" "\$C"', text).group(1)
        c = 'c' * 40
        pages = [[{'number': 3, 'merge_commit_sha': 'd' * 40}], [{'number': 7, 'merge_commit_sha': c}]]
        p = os.path.join(self.tmp, 'pages.json')
        with open(p, 'w') as f:
            json.dump(pages, f)
        out = subprocess.run([sys.executable, '-c', code, p, c], stdout=subprocess.PIPE, check=True).stdout
        self.assertEqual(out.decode().strip(), '7')

    # ---- R4: authenticated fetch
    def test_fetch_block_authenticates_only_its_own_command(self):
        text = self.read(AUDIT)
        fetch = block(text, 'authenticated-fetch')
        self.assertIn('::add-mask::', fetch.split('git -c')[0])
        self.assertRegex(fetch, r'git -c "http\.https://github\.com/\.extraheader=AUTHORIZATION: basic \$AUTH" fetch')
        for name in os.listdir(self.path('.github/workflows')):
            wf = self.read(f'.github/workflows/{name}')
            self.assertNotIn('persist-credentials: true', wf)
            for line in commands(wf):
                if re.search(r'\bgit\b.*\bfetch\b', line):
                    self.assertIn('extraheader=AUTHORIZATION', line, f'{name}: unauthenticated fetch: {line.strip()}')

    def test_fetch_block_runs_against_a_local_remote(self):
        """Executes the exact fetch lines (quoting, base64, masking) with a local bare repository as
        origin; GitHub authentication itself is a live test."""
        origin = os.path.join(self.tmp, 'origin.git')
        subprocess.run(['git', 'clone', '-q', '--bare', self.root, origin], check=True)
        subprocess.run(['git', '-C', origin, 'update-ref', 'refs/pull/5/head', self.root_commit], check=True)
        self.g('remote', 'add', 'origin', origin)
        code, out = self.run_block('authenticated-fetch', {'GH_TOKEN': 'dummy-token', 'N': '5'})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.g('rev-parse', 'refs/pull/5/head'), self.root_commit)
        self.assertNotIn('dummy-token', out)

    def test_tags_workflow_does_not_fetch(self):
        lines = commands(self.read('.github/workflows/handover-tags.yml'))
        self.assertFalse([l for l in lines if re.search(r'\bgit\b.*\bfetch\b', l)])


if __name__ == '__main__':
    unittest.main()
