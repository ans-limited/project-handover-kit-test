"""The tests' fixture comes from tools/tests/template/ (found by the first GitHub run: the kit's tests
failed in an initialised repository, because they copied the live repository and ran init on it).

- In the kit's own uninitialised source, the template must equal the live files (no drift).
- In an initialised repository, the fixture must still be a pristine, working kit."""
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import REPO_ROOT, TEMPLATE_DIR, build_fixture, check_docs, handover  # noqa: F401


def template_files():
    for dp, dns, fns in os.walk(TEMPLATE_DIR):
        for fn in fns:
            yield os.path.relpath(os.path.join(dp, fn), TEMPLATE_DIR).replace(os.sep, '/')


def is_kit_source(root):
    return check_docs.load_policy(root).get('owner') == '@OWNER'


class TemplateDrift(unittest.TestCase):
    def test_template_equals_the_kit_source(self):
        if not is_kit_source(REPO_ROOT):
            self.skipTest('initialised repository: here the template, not the live files, is the reference')
        for rel_path in template_files():
            with self.subTest(file=rel_path):
                with open(os.path.join(TEMPLATE_DIR, *rel_path.split('/')), 'rb') as a, \
                        open(os.path.join(REPO_ROOT, *rel_path.split('/')), 'rb') as b:
                    self.assertEqual(a.read(), b.read(), f'{rel_path}: tools/tests/template/ is out of date')


class FixtureInInitialisedRepository(unittest.TestCase):
    def test_fixture_from_an_initialised_repository_is_pristine_and_works(self):
        tmp = tempfile.mkdtemp(prefix='adopted-')
        try:
            adopted = os.path.join(tmp, 'adopted')
            build_fixture(REPO_ROOT, adopted)
            subprocess.run(['git', '-C', adopted, 'init', '-q', '-b', 'main'], check=True)
            self.assertEqual(handover.main(['--root', adopted, 'init', '--project', 'Adopted', '--owner', 'someone']), 0)
            with open(os.path.join(adopted, 'src.txt'), 'w') as f:
                f.write('project file\n')
            fixture = os.path.join(tmp, 'fixture')
            build_fixture(adopted, fixture)
            cfg = check_docs.load_policy(fixture)
            self.assertEqual((cfg['owner'], cfg['trunk']), ('@OWNER', 'master'))
            self.assertFalse(os.path.exists(os.path.join(fixture, 'src.txt')))
            self.assertEqual(check_docs.Snapshot(fixture, cfg, expect_tag='v0').run().items, [])
            subprocess.run(['git', '-C', fixture, 'init', '-q', '-b', 'master'], check=True)
            self.assertEqual(handover.main(['--root', fixture, 'init', '--project', 'Again', '--owner', 'owner-test']), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
