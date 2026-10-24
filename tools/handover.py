#!/usr/bin/env python3
"""handover.py -- the writing side of the handover method (check_docs.py is the read-only side).

Usage (from the repository root):
    python tools/handover.py init --project NAME --owner GITHUB_USER   (once, after git init, before the root commit)
    python tools/handover.py next                                      (prints the next checkpoint ID)
    python tools/handover.py new-directive --tag vN --tier 1|2 --title TITLE
    python tools/handover.py ledger-append --tag vN --title TITLE --record FILE [--date YYYY-MM-DD]
    python tools/handover.py pin PATH [PATH ...]
    python tools/handover.py new-decision --kind R|CR --title TITLE [--slug SLUG]
    python tools/handover.py evidence-add --tag vN FILE [--name NAME]
    python tools/handover.py codeowners                                (regenerates .github/CODEOWNERS)
    python tools/handover.py verify-github --repo OWNER/NAME [--record FILE]   (read-only settings check)

ledger-append performs the ledger rollover itself and refuses an entry over ledger_entry_max_bytes.
Every file is written as UTF-8 with LF line endings. Standard library only; Python 3.8 or later.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_docs import (DECISION_FILE_RE, LEDGER_FILE_RE, POLICY_PATH, GateError, codeowners_text,  # noqa: E402
                        is_work_tree, load_policy, parse_ledger, parse_ledger_register, parse_manifest,
                        pin_target, read_bytes, read_text, rel, sha256_bytes)

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CLOSING_RESERVE = 500   # bytes kept free for a ledger's CLOSED section; check_docs.validate_policy assumes it


class ToolError(Exception):
    pass


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def append_text(path, text):
    with open(path, 'a', encoding='utf-8', newline='\n') as f:
        f.write(text)


def today():
    return datetime.date.today().isoformat()


def P(root, relp):
    return os.path.join(root, *relp.split('/'))


# ---------------------------------------------------------------- checkpoint numbering

def ledger_numbers(root, cfg):
    rows, errors = parse_ledger_register(P(root, cfg['ledger_register']))
    if errors or not rows:
        raise ToolError(f'ledger register is not valid: {"; ".join(errors) or "no rows"}')
    nums = []
    for r in rows:
        nums += [e['n'] for e in parse_ledger(read_text(P(root, f'{cfg["history_dir"]}/{r["file"]}')))['entries']]
    return rows, nums


def next_id(root, cfg):
    _, nums = ledger_numbers(root, cfg)
    return f'v{nums[-1] + 1 if nums else 0}'


def require_next(root, cfg, tag):
    want = next_id(root, cfg)
    if tag != want:
        raise ToolError(f'the next checkpoint is {want}, not {tag}')


# ---------------------------------------------------------------- pins

PINS_HEADER = ('# Immutable files: accepted decisions, resolved CRs and closed ledgers.\n'
               '# Format: <sha256>  <path from the repository root>. Add lines only with tools/handover.py pin.\n')
MANIFEST_HEADER = ('# Byte-exact evidence for this directive. Format: <sha256>  <file name>.\n'
                   '# Add files only with tools/handover.py evidence-add.\n')


def add_manifest_line(manifest, relpath, sha, header):
    entries, errors = parse_manifest(manifest)
    if errors:
        raise ToolError(f'{manifest}: ' + '; '.join(errors))
    if relpath in entries:
        if entries[relpath] == sha:
            return False
        raise ToolError(f'{relpath} is already pinned with a different hash: pinned files are immutable')
    if not os.path.isfile(manifest):
        write_text(manifest, header)
    elif read_bytes(manifest) and not read_bytes(manifest).endswith(b'\n'):
        append_text(manifest, '\n')
    append_text(manifest, f'{sha}  {relpath}\n')
    return True


def cmd_pin(root, cfg, paths):
    rows, _ = parse_ledger_register(P(root, cfg['ledger_register']))
    for p in paths:
        fp = os.path.abspath(os.path.join(root, p)) if not os.path.isabs(p) else p
        if not os.path.isfile(fp):
            raise ToolError(f'{p} is not a file')
        relp = rel(root, fp)
        if relp.startswith('..'):
            raise ToolError(f'{p} is outside the repository')
        req, why = pin_target(cfg, relp, read_text(fp), rows)
        if why:
            raise ToolError(f'refused: {why}')
        added = add_manifest_line(P(root, cfg['pins_file']), relp, sha256_bytes(read_bytes(fp)), PINS_HEADER)
        print(f'{"pinned" if added else "already pinned"}  {relp}')


# ---------------------------------------------------------------- ledger

def write_register(path, rows):
    out = ['# Ledger register', '',
           'The history ledgers, in order. Exactly one is OPEN, and it is the last row. A CLOSED ledger is',
           'immutable and pinned in docs/PINS.sha256. Maintained only by tools/handover.py ledger-append.', '',
           '| Ledger | Status | First tag | Last tag |', '|---|---|---|---|']
    out += [f'| `{r["file"]}` | {r["status"]} | {r["first"]} | {r["last"]} |' for r in rows]
    write_text(path, '\n'.join(out) + '\n')


def project_name(root, cfg):
    first = read_text(P(root, cfg['state_file'])).split('\n', 1)[0]
    m = re.match(r'^# STATE\.md — (.+)$', first)
    return m.group(1).strip() if m else 'project'


def ledger_header(name, project, predecessor):
    return (f'# {name[:-3]} — {project} history ledger\n\n'
            f'Predecessor: {predecessor}\n\n'
            'Append-only. One section per checkpoint, written by tools/handover.py ledger-append. Read by pointer\n'
            '(file and section heading), never in full at session start.\n')


def cmd_ledger_append(root, cfg, tag, title, record_path, date):
    require_next(root, cfg, tag)
    if '\n' in title or not title.strip():
        raise ToolError('--title must be one non-empty line')
    body = read_text(record_path).replace('\r\n', '\n').strip('\n')
    if re.search(r'^## ', body, re.M):
        raise ToolError('the record must not contain "## " headings (use "### " for sub-sections)')
    entry = f'\n## {tag} — {date} — {title.strip()}\n\n{body}\n'
    n_entry = len(entry.encode('utf-8')) - 1   # the leading blank line belongs to the previous section
    if n_entry > cfg['ledger_entry_max_bytes']:
        raise ToolError(f'the entry is {n_entry:,} B, over the cap of {cfg["ledger_entry_max_bytes"]:,} B: '
                        'move detail into evidence files and cite them')
    rows, nums = ledger_numbers(root, cfg)
    hdir = P(root, cfg['history_dir'])
    regp = P(root, cfg['ledger_register'])
    open_row = rows[-1]
    open_path = os.path.join(hdir, open_row['file'])
    open_text = read_text(open_path)
    entries = parse_ledger(open_text)['entries']
    # Room for the CLOSED section is reserved, so that no ledger, open or closed, passes its limit.
    if entries and (len(open_text.encode('utf-8')) + len(entry.encode('utf-8')) + CLOSING_RESERVE
                    > cfg['ledger_max_bytes']):
        n = int(LEDGER_FILE_RE.match(open_row['file']).group(1)) + 1
        succ = f'LEDGER_{n:04d}.md'
        last = f'v{entries[-1]["n"]}'
        append_text(open_path, f'\n## CLOSED — {tag} ({date})\n\nThis ledger is closed. It holds the records of '
                               f'{open_row["first"]} to {last}. Successor: {succ}. No byte is ever added '
                               'to it again.\n')
        open_row['status'], open_row['last'] = 'CLOSED', last
        succ_path = os.path.join(hdir, succ)
        write_text(succ_path, ledger_header(succ, project_name(root, cfg), open_row['file']))
        rows.append({'file': succ, 'status': 'OPEN', 'first': '—', 'last': '—'})
        write_register(regp, rows)   # the register says CLOSED before the ledger is pinned
        cmd_pin(root, cfg, [f'{cfg["history_dir"]}/{open_row["file"]}'])
        print(f'rolled over: {open_row["file"]} CLOSED and pinned; {succ} is now OPEN')
        open_row, open_path = rows[-1], succ_path
    append_text(open_path, entry)
    if open_row['first'] in ('—', '-', ''):
        open_row['first'] = tag
    write_register(regp, rows)
    print(f'appended {tag} to {rel(root, open_path)} ({os.path.getsize(open_path):,} B of {cfg["ledger_max_bytes"]:,} B)')


# ---------------------------------------------------------------- decisions, directives, evidence

def slugify(s):
    s = re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    return '-'.join(s.split('-')[:6]) or 'decision'


def fill(template_path, values):
    text = read_text(template_path)
    for k, v in values.items():
        text = text.replace('{' + k + '}', v)
    return text


def cmd_new_decision(root, cfg, kind, title, slug):
    ddir = P(root, cfg['decisions_dir'])
    used = [int(m.group(1).split('-')[1]) for m in (DECISION_FILE_RE.match(n) for n in os.listdir(ddir))
            if m and m.group(1).split('-')[0] == kind]
    did = f'{kind}-{(max(used) + 1 if used else 1):04d}'
    name = f'{did}-{slugify(slug or title)}.md'
    tpl = os.path.join(ddir, '_TEMPLATE_RULING.md' if kind == 'R' else '_TEMPLATE_CR.md')
    write_text(os.path.join(ddir, name), fill(tpl, {'ID': did, 'TITLE': title.strip(), 'DATE': today()}))
    print(f'created {cfg["decisions_dir"]}/{name}')
    print(f'next: list {did} in STATE.md {cfg["register_section"]}; a decision change is made in a checkpoint PR')


def cmd_new_directive(root, cfg, tag, tier, title):
    require_next(root, cfg, tag)
    base = P(root, cfg['directives_dir'])
    d = os.path.join(base, tag)
    if os.path.exists(d):
        raise ToolError(f'{rel(root, d)} already exists')
    text = fill(os.path.join(base, '_TEMPLATE', 'DIRECTIVE.md'),
                {'TAG': tag, 'TIER': str(tier), 'TITLE': title.strip(), 'DATE': today()})
    write_text(os.path.join(d, 'DIRECTIVE.md'), text)
    write_text(os.path.join(d, 'evidence', 'MANIFEST.sha256'), MANIFEST_HEADER)
    print(f'created {rel(root, d)}/DIRECTIVE.md (Tier {tier}) and evidence/MANIFEST.sha256')


def cmd_evidence_add(root, cfg, tag, src, name):
    edir = os.path.join(P(root, cfg['directives_dir']), tag, 'evidence')
    if not os.path.isdir(edir):
        raise ToolError(f'no evidence folder for {tag}: run new-directive first')
    if not os.path.isfile(src):
        raise ToolError(f'{src} is not a file')
    name = name or os.path.basename(src)
    if name == 'MANIFEST.sha256' or '/' in name or '\\' in name:
        raise ToolError(f'{name!r} is not a usable evidence file name')
    dst = os.path.join(edir, name)
    if os.path.exists(dst):
        raise ToolError(f'{rel(root, dst)} already exists: evidence is never overwritten')
    shutil.copyfile(src, dst)
    sha = sha256_bytes(read_bytes(dst))
    if sha != sha256_bytes(read_bytes(src)):
        os.remove(dst)
        raise ToolError('the copy does not match its source byte for byte')
    add_manifest_line(os.path.join(edir, 'MANIFEST.sha256'), name, sha, MANIFEST_HEADER)
    print(f'added {rel(root, dst)} ({os.path.getsize(dst):,} B, sha256 {sha})')


def cmd_verify_github(root, cfg, repo, record=None):
    """Read-only comparison of a GitHub repository's settings with this policy (tools/verify_github.py)."""
    import verify_github
    if not re.match(r'^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$', repo):
        raise ToolError(f'{repo!r} is not OWNER/NAME')
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    gh = shutil.which('gh')
    gh_ok = (not token and gh and subprocess.run([gh, 'auth', 'status'], stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE).returncode == 0)
    seen = {}
    live = verify_github.gh_fetch(gh) if gh_ok else verify_github.http_fetch(token)
    token = token or gh_ok   # only used below to say whether the read was authenticated

    def fetch(path):
        seen[path] = live(path)
        return seen[path]
    results, code = verify_github.run(repo, cfg, codeowners_text, fetch=fetch)
    print(f'verify-github {repo} (trunk {cfg.get("trunk", "master")}; {"authenticated" if token else "anonymous"} read)')
    for item, status, detail in results:
        print(f'{status:<8} {item}' + (f' -- {detail}' if detail else ''))
    print('RESULT ' + {0: 'PASS', 1: 'FAIL', 3: 'INCOMPLETE (some items not visible)'}[code])
    if record:
        with open(record, 'w', encoding='utf-8', newline='\n') as f:
            json.dump({'repo': repo, 'authenticated': bool(token), 'responses': seen,
                       'results': results, 'exit_code': code}, f, indent=1)
        print(f'recorded the responses in {record}')
    return code


def cmd_codeowners(root, cfg):
    write_text(P(root, '.github/CODEOWNERS'), codeowners_text(cfg))
    print('wrote .github/CODEOWNERS')


# ---------------------------------------------------------------- init (once, after git init, before the root commit)

INIT_FILES = ['STATE.md', 'CLAUDE.md', 'README.md', 'docs/history/LEDGER_0001.md', 'directives/v0/DIRECTIVE.md',
              'docs/decisions/R-0001-adopt-the-handover-documentation-method.md',
              'docs/decisions/R-0002-checkpoints-as-pull-requests.md']
INIT_PINNED = ['docs/decisions/R-0001-adopt-the-handover-documentation-method.md',
               'docs/decisions/R-0002-checkpoints-as-pull-requests.md']
TRUNK_DOCS = INIT_FILES + ['docs/GITHUB_SETUP.md']
TRUNK_WORKFLOWS = ['.github/workflows/handover-gate.yml', '.github/workflows/handover-tests.yml',
                   '.github/workflows/handover-audit.yml', '.github/workflows/handover-tags.yml']


def unborn_branch(root):
    p = subprocess.run(['git', '-C', root, 'symbolic-ref', '--short', 'HEAD'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return p.stdout.decode().strip() if p.returncode == 0 else ''


def set_trunk(root, trunk):
    """The starter is written for trunk "master"; rewrite the trunk name where it appears."""
    if trunk == 'master':
        return
    for relp in TRUNK_DOCS:
        fp = P(root, relp)
        text = read_text(fp)
        new = text.replace('`master`', f'`{trunk}`')
        if new != text:
            write_text(fp, new)
    for relp in TRUNK_WORKFLOWS:
        fp = P(root, relp)
        if not os.path.isfile(fp):
            continue
        text = read_text(fp)
        new = (text.replace('branches: [master]', f'branches: [{trunk}]')
               .replace('origin/master', f'origin/{trunk}')
               .replace('git branch -f master ', f'git branch -f {trunk} ')
               .replace('the new master commit', f'the new {trunk} commit'))
        if new != text:
            write_text(fp, new)
    print(f'set the trunk branch to {trunk} in the policy, workflows and documents')


def cmd_init(root, cfg, project, owner):
    if not is_work_tree(root):
        raise ToolError('init runs inside a git work tree: run "git init -b main" (or your trunk name) first')
    if subprocess.run(['git', '-C', root, 'rev-parse', '--verify', '-q', 'HEAD'],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0:
        raise ToolError('init runs only before the root commit')
    owner = owner.strip().lstrip('@')
    if not re.match(r'^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$', owner):
        raise ToolError(f'{owner!r} is not a GitHub user name')
    trunk = unborn_branch(root)
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$', trunk) or '..' in trunk:
        raise ToolError(f'cannot use the current branch {trunk!r} as the trunk')
    rows, nums = ledger_numbers(root, cfg)
    pins, perrors = parse_manifest(P(root, cfg['pins_file']))
    if len(rows) != 1 or nums != [0] or perrors or set(pins) != set(INIT_PINNED):
        raise ToolError('init runs only on the untouched starter (ledger entry v0 only; R-0001 and R-0002 the only pins)')
    for relp in INIT_FILES:
        fp = P(root, relp)
        text = read_text(fp)
        new = text.replace('<PROJECT NAME>', project.strip()).replace('<Owner>', '@' + owner)
        if new != text:
            write_text(fp, new)
            print(f'filled in {relp}')
    set_trunk(root, trunk)
    pol = read_text(P(root, POLICY_PATH))
    if '"owner": "@OWNER"' not in pol or '"trunk": "master"' not in pol:
        raise ToolError(f'{POLICY_PATH} has no "owner": "@OWNER" or "trunk": "master" placeholder')
    write_text(P(root, POLICY_PATH), pol.replace('"owner": "@OWNER"', f'"owner": "@{owner}"')
               .replace('"trunk": "master"', f'"trunk": "{trunk}"'))
    print(f'set the owner in {POLICY_PATH} to @{owner} and the trunk to {trunk}')
    cfg = load_policy(root)
    pins_path = P(root, cfg['pins_file'])
    keep = [l for l in read_text(pins_path).split('\n') if not any(l.endswith('  ' + p) for p in INIT_PINNED)]
    write_text(pins_path, '\n'.join(keep).rstrip('\n') + '\n')
    cmd_pin(root, cfg, INIT_PINNED)
    subprocess.run(['git', '-C', root, 'config', 'core.hooksPath', '.githooks'], check=True)
    print('set core.hooksPath to .githooks')
    # The hook must be executable in git's index, or git skips it on Linux and macOS; on Windows the
    # file system has no executable bit, so it is set in the index explicitly.
    for hook in sorted(os.listdir(P(root, '.githooks'))):
        hp = P(root, f'.githooks/{hook}')
        os.chmod(hp, os.stat(hp).st_mode | 0o111)
        subprocess.run(['git', '-C', root, 'add', '--chmod=+x', f'.githooks/{hook}'], check=True)
    print('marked the hooks executable')
    cmd_codeowners(root, cfg)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description='Writing side of the handover method.')
    ap.add_argument('--root', default=os.path.dirname(TOOLS_DIR))
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('init')
    p.add_argument('--project', required=True)
    p.add_argument('--owner', required=True)
    sp.add_parser('next')
    p = sp.add_parser('new-directive')
    p.add_argument('--tag', required=True)
    p.add_argument('--tier', required=True, type=int, choices=[1, 2])
    p.add_argument('--title', required=True)
    p = sp.add_parser('ledger-append')
    p.add_argument('--tag', required=True)
    p.add_argument('--title', required=True)
    p.add_argument('--record', required=True)
    p.add_argument('--date', default=None)
    p = sp.add_parser('pin')
    p.add_argument('paths', nargs='+')
    p = sp.add_parser('new-decision')
    p.add_argument('--kind', required=True, choices=['R', 'CR'])
    p.add_argument('--title', required=True)
    p.add_argument('--slug')
    p = sp.add_parser('evidence-add')
    p.add_argument('--tag', required=True)
    p.add_argument('file')
    p.add_argument('--name')
    sp.add_parser('codeowners')
    p = sp.add_parser('verify-github', help='read-only check of a GitHub repository against this policy')
    p.add_argument('--repo', required=True, help='OWNER/NAME')
    p.add_argument('--record', help='write the API responses and results to this JSON file')
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        cfg = load_policy(root)
        if a.cmd == 'init':
            cmd_init(root, cfg, a.project, a.owner)
        elif a.cmd == 'next':
            print(next_id(root, cfg))
        elif a.cmd == 'new-directive':
            cmd_new_directive(root, cfg, a.tag, a.tier, a.title)
        elif a.cmd == 'ledger-append':
            date = a.date or today()
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
                raise ToolError('--date must be YYYY-MM-DD')
            cmd_ledger_append(root, cfg, a.tag, a.title, a.record, date)
        elif a.cmd == 'pin':
            cmd_pin(root, cfg, a.paths)
        elif a.cmd == 'new-decision':
            cmd_new_decision(root, cfg, a.kind, a.title, a.slug)
        elif a.cmd == 'evidence-add':
            cmd_evidence_add(root, cfg, a.tag, a.file, a.name)
        elif a.cmd == 'codeowners':
            cmd_codeowners(root, cfg)
        elif a.cmd == 'verify-github':
            return cmd_verify_github(root, cfg, a.repo, a.record)
    except (ToolError, GateError, OSError, subprocess.CalledProcessError) as e:
        print(f'ERROR {e}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
