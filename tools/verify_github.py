#!/usr/bin/env python3
"""verify_github.py -- per-repository configuration verification (design note s12.2).

Reads a repository's settings through GitHub's REST API (read-only) and compares them with the
kit's profile in tools/policy.json ("trunk", "review_policy", "github_profile"). It never changes
anything. Run through tools/handover.py:

    python tools/handover.py verify-github --repo OWNER/NAME

Authentication is optional. For a public repository the rules can be read anonymously; with a
token (environment variable GH_TOKEN or GITHUB_TOKEN) more fields become visible, for example the
rulesets' bypass lists, which GitHub shows only to users with write access. The token is sent only
in the Authorization header to api.github.com and is never printed or stored.

Each item is PASS, FAIL or UNKNOWN (not visible with the access used). Exit code: 0 when every item
passes, 1 when any item fails, 3 when nothing fails but some items are unknown.
Standard library only; Python 3.8 or later.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.github.com'
PASS, FAIL, UNKNOWN = 'PASS', 'FAIL', 'UNKNOWN'


def http_fetch(token=None):
    """Return fetch(path) -> (status, parsed JSON or None) for the live API."""
    def fetch(path):
        req = urllib.request.Request(API + path, headers={
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'handover-kit-verify-github'})
        if token:
            req.add_header('Authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b'null')
            except ValueError:
                return e.code, None
    return fetch


def gh_fetch(gh='gh'):
    """fetch(path) through the GitHub CLI: gh holds the credential, so no token passes through here."""
    import re
    import subprocess

    cmd = list(gh) if isinstance(gh, (list, tuple)) else [gh]

    def fetch(path):
        p = subprocess.run(cmd + ['api', '--include', '-H', 'Accept: application/vnd.github+json',
                            '-H', 'X-GitHub-Api-Version: 2022-11-28', path.lstrip('/')],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = p.stdout.decode('utf-8', 'replace')
        parts = re.split(r'\r?\n\r?\n', out, maxsplit=1)
        m = re.match(r'HTTP/\S+ (\d{3})', parts[0])
        if not m:
            return None, None
        body = parts[1] if len(parts) > 1 else ''
        try:
            return int(m.group(1)), (json.loads(body) if body.strip() else None)
        except ValueError:
            return int(m.group(1)), None
    return fetch


def collect(fetch, repo, trunk):
    """Everything the evaluation needs, as {name: (status, json)}."""
    q = urllib.parse.quote
    d = {'repo': fetch(f'/repos/{repo}'),
         'branch_rules': fetch(f'/repos/{repo}/rules/branches/{q(trunk, safe="")}'),
         'rulesets': fetch(f'/repos/{repo}/rulesets?includes_parents=true&per_page=100'),
         'codeowners_errors': fetch(f'/repos/{repo}/codeowners/errors?ref={q(trunk, safe="")}'),
         'codeowners_file': fetch(f'/repos/{repo}/contents/.github/CODEOWNERS?ref={q(trunk, safe="")}'),
         'policy_file': fetch(f'/repos/{repo}/contents/tools/policy.json?ref={q(trunk, safe="")}')}
    details = {}
    status, rulesets = d['rulesets']
    if status == 200 and isinstance(rulesets, list):
        for rs in rulesets:
            if isinstance(rs, dict) and rs.get('id') is not None:
                details[rs['id']] = fetch(f'/repos/{repo}/rulesets/{rs["id"]}')
    d['ruleset_details'] = details
    return d


def decode_content(resp):
    status, obj = resp
    if status != 200 or not isinstance(obj, dict) or obj.get('encoding') != 'base64':
        return None
    return base64.b64decode(obj.get('content', ''))


def evaluate(d, cfg, codeowners_text):
    """List of (item, status, detail). codeowners_text(policy) gives the expected CODEOWNERS bytes."""
    out = []
    add = lambda item, status, detail='': out.append((item, status, detail))  # noqa: E731
    prof = cfg.get('github_profile', {})
    trunk = cfg.get('trunk', 'master')
    app_id = prof.get('actions_app_id')
    checks = prof.get('required_checks', [])
    routine_approval = cfg.get('review_policy', {}).get('owner_approval_routine', True)

    status, repo = d['repo']
    if status != 200 or not isinstance(repo, dict):
        add('repository readable', FAIL, f'HTTP {status}')
        return out
    add('repository readable', PASS, repo.get('full_name', ''))
    add('default branch is the trunk', PASS if repo.get('default_branch') == trunk else FAIL,
        f'default branch {repo.get("default_branch")!r}, trunk {trunk!r}')

    status, rules = d['branch_rules']
    if status != 200 or not isinstance(rules, list):
        add(f'rules for {trunk} readable', UNKNOWN, f'HTTP {status}')
        rules = []
    by_type = {}
    for r in rules:
        by_type.setdefault(r.get('type'), []).append(r)

    pr = [r.get('parameters', {}) for r in by_type.get('pull_request', [])]
    if not pr:
        add('pull request required', FAIL, 'no pull_request rule applies to the trunk')
    else:
        p = pr[0] if len(pr) == 1 else {k: [x.get(k) for x in pr] for k in pr[0]}
        add('pull request required', PASS, f'{len(pr)} rule(s)')
        want = 1 if routine_approval else 0

        def flag(name, label, expected=True):
            v = p.get(name)
            ok = (v == expected) if not isinstance(v, list) else all(x == expected for x in v)
            add(label, PASS if ok else FAIL, f'{name} = {v!r}')
        flag('require_code_owner_review', 'Code Owner review required')
        flag('dismiss_stale_reviews_on_push', 'stale approvals dismissed')
        if prof.get('require_last_push_approval', True):
            flag('require_last_push_approval', 'approval of the most recent push required')
        flag('required_approving_review_count', f'required approvals = {want}', want)
        mm = p.get('allowed_merge_methods')
        if mm is None:
            repo_flags = [repo.get(k) for k in ('allow_squash_merge', 'allow_merge_commit', 'allow_rebase_merge')]
            if None in repo_flags:
                add('squash merging only', UNKNOWN, 'no allowed_merge_methods in the rule, and the repository flags are not visible')
            else:
                add('squash merging only', PASS if repo_flags == [True, False, False] else FAIL,
                    f'repository flags squash/merge/rebase = {repo_flags}')
        else:
            add('squash merging only', PASS if mm == ['squash'] else FAIL, f'allowed_merge_methods = {mm}')

    rsc = [r.get('parameters', {}) for r in by_type.get('required_status_checks', [])]
    found = {}
    strict = []
    for p in rsc:
        strict.append(p.get('strict_required_status_checks_policy'))
        for c in p.get('required_status_checks', []):
            found.setdefault(c.get('context'), []).append(c.get('integration_id'))
    for c in checks:
        if c not in found:
            add(f'required check {c}', FAIL, 'not required on the trunk')
        elif app_id is not None and any(i != app_id for i in found[c]):
            add(f'required check {c}', FAIL, f'expected source {app_id}, found {found[c]}')
        else:
            add(f'required check {c}', PASS, f'source {found[c]}')
    add('branches must be up to date', PASS if strict and all(strict) else FAIL, f'strict = {strict}')
    for t, label in (('required_linear_history', 'linear history'), ('non_fast_forward', 'force-push blocked'),
                     ('deletion', 'deletion blocked')):
        add(label, PASS if t in by_type else FAIL, '' if t in by_type else f'no {t} rule')

    # rulesets: enforcement, bypass lists, tag protection
    ids = {r.get('ruleset_id') for r in rules if r.get('ruleset_id') is not None}
    details = d.get('ruleset_details', {})
    for rid in sorted(ids):
        st, rs = details.get(rid, (None, None))
        if st != 200 or not isinstance(rs, dict):
            add(f'ruleset {rid} has no bypass', UNKNOWN, f'HTTP {st}')
            continue
        if 'bypass_actors' not in rs:
            add(f'ruleset {rid} ({rs.get("name")}) has no bypass', UNKNOWN,
                'bypass_actors is shown only to users with write access')
        else:
            add(f'ruleset {rid} ({rs.get("name")}) has no bypass', PASS if not rs['bypass_actors'] else FAIL,
                f'{len(rs["bypass_actors"])} bypass actor(s)')
    tag_sets = [rs for st, rs in details.values() if st == 200 and isinstance(rs, dict) and rs.get('target') == 'tag'
                and rs.get('enforcement') == 'active']
    tag_ok = [rs for rs in tag_sets
              if any(inc in ('refs/tags/v*', '~ALL') for inc in rs.get('conditions', {}).get('ref_name', {}).get('include', []))
              and {'creation', 'update', 'deletion'} <= {r.get('type') for r in rs.get('rules', [])}]
    add('v* tags protected', PASS if tag_ok else FAIL,
        f'{len(tag_sets)} active tag ruleset(s); {len(tag_ok)} cover v* creation, update and deletion')

    # CODEOWNERS
    st, errs = d['codeowners_errors']
    if st == 200 and isinstance(errs, dict):
        add('CODEOWNERS has no errors', PASS if not errs.get('errors') else FAIL, f'{len(errs.get("errors", []))} error(s)')
    else:
        add('CODEOWNERS has no errors', FAIL if st == 404 else UNKNOWN, f'HTTP {st}')
    owners, policy_bytes = decode_content(d['codeowners_file']), decode_content(d['policy_file'])
    if owners is None or policy_bytes is None:
        add('CODEOWNERS matches the policy', FAIL, 'CODEOWNERS or tools/policy.json not found on the trunk')
    else:
        try:
            remote_cfg = json.loads(policy_bytes.decode('utf-8'))
            ok = owners == codeowners_text(remote_cfg).encode('utf-8')
            add('CODEOWNERS matches the policy', PASS if ok else FAIL, 'generated from the trunk\'s tools/policy.json')
        except (ValueError, KeyError) as e:
            add('CODEOWNERS matches the policy', FAIL, f'policy unreadable: {e}')
    add('agent uses a separate identity', UNKNOWN, 'not visible through the API; record it in docs/GITHUB_SETUP.md')
    return out


def run(repo, cfg, codeowners_text, fetch=None, token=None):
    fetch = fetch or http_fetch(token if token is not None else (os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')))
    results = evaluate(collect(fetch, repo, cfg.get('trunk', 'master')), cfg, codeowners_text)
    statuses = {s for _, s, _ in results}
    code = 1 if FAIL in statuses else (3 if UNKNOWN in statuses else 0)
    return results, code
