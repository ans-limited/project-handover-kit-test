#!/usr/bin/env python3
"""check_docs.py -- documentation and governance gate (prototype of design note draft 4.1).

Modes (run from the repository root, or pass --repo DIR):
  python tools/check_docs.py                         full snapshot check of the working tree
  python tools/check_docs.py --staged                pre-commit: local rules on the staged snapshot (note s4)
  python tools/check_docs.py --bootstrap             root-commit check of the working tree (note s9)
  python tools/check_docs.py --pr --base B --head H  authoritative pull-request check (note s3-s6)
  python tools/check_docs.py --audit --commit C --prs FILE --reviews FILE --repo-name OWNER/NAME
                                                     master audit from recorded API responses (note s7.4)
  python tools/check_docs.py --tag-check --tag vN --commit T     tag validation (note s7.5)
  python tools/check_docs.py --codeowners            print the CODEOWNERS file generated from the policy (note s8)
Options: --ci (skip the hook-path check), --expect-tag TAG, --quiet.

Exit 0 = PASS, 1 = FAIL, 2 = usage, configuration or environment error. Every error fails closed.
Standard library only; Python 3.8 or later.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SUPPORTED_SCHEMAS = (1,)
POLICY_PATH = 'tools/policy.json'

BANNER_PREFIX = 'CURRENT BASELINE:'
BANNER_RE = re.compile(r'^CURRENT BASELINE: tag (v\d+) \((\d{4}-\d{2}-\d{2})\) (?:—|--|-) \S')
SECTION_RE = re.compile(r'^## (S\d+)\. \S')
DECISION_ID_RE = re.compile(r'(?<![A-Za-z0-9])(?:R|CR)-\d{4}(?![0-9])')
DECISION_FILE_RE = re.compile(r'^((?:R|CR)-\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$')
DECISION_TITLE_RE = re.compile(r'^# ((?:R|CR)-\d{4}) (?:—|--|-) \S')
STATUS_RE = re.compile(r'^\*\*Status:\*\* ([A-Za-z][A-Za-z ]*?)\s*$')
SUPERSEDES_RE = re.compile(r'^\*\*Supersedes:\*\* (.+)$')
DECISION_STATUSES = {'R': ('Proposed', 'Accepted'), 'CR': ('Open', 'Resolved')}
IMMUTABLE_STATUSES = ('Accepted', 'Resolved')
LEDGER_FILE_RE = re.compile(r'^LEDGER_(\d{4})\.md$')
LEDGER_ENTRY_RE = re.compile(r'^## v(\d+) — (\d{4}-\d{2}-\d{2}) — \S')
LEDGER_CLOSED_RE = re.compile(r'^## CLOSED — v(\d+) \((\d{4}-\d{2}-\d{2})\)$')
PREDECESSOR_RE = re.compile(r'^Predecessor: (\S+)$')
LEDGER_SUFFIX_RE = re.compile('^\n*## (?:v\\d+ — \\d{4}-\\d{2}-\\d{2} — |CLOSED — v\\d+ \\()'.encode('utf-8'))
PIN_LINE_RE = re.compile(r'^([0-9a-f]{64})  (\S(?:.*\S)?)$')
LINK_RE = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
REPORT_FILE_RE = re.compile(r'^(SESSION_REPORT|VERIFICATION)_(v\d+)\.md$')
TIER_RE = re.compile(r'^\*\*Tier:\*\* (\S+)\s*$')
VERIFY_FIELDS = {
    'verifier': re.compile(r'^\*\*Verifier:\*\* (\S.*)$'),
    'commit': re.compile(r'^\*\*Verified commit:\*\* ([0-9a-f]{40})\s*$'),
    'result': re.compile(r'^\*\*Result:\*\* (\S+)\s*$'),
    'scope': re.compile(r'^\*\*Scope:\*\* (\S.*)$'),
}

# Local classification (note s4): True = blocking in the pre-commit hook, False = advisory there.
# In the PR check every finding blocks. The test suite asserts this table against the note.
LOCAL_CLASS = {
    ('C1', 'cap'): True,
    ('C2', 'form'): True,
    ('C2', 'expect'): False,
    ('C3', 'sections'): True,
    ('C4', 'proof'): True,
    ('C5', 'file'): True,
    ('C5', 'xref'): False,
    ('C6', 'format'): True,
    ('C7', 'manifest'): True,
    ('C7', 'match-base'): True,
    ('C7', 'match-new'): False,
    ('C8', 'entry'): True,
    ('C8', 'xref'): False,
    ('C9', 'size'): True,
    ('C9', 'tier'): True,
    ('C9', 'xref'): False,
    ('C9', 'evidence-base'): True,
    ('C9', 'evidence-new'): False,
    ('C10', 'xref'): False,
    ('C11', 'file'): True,
    ('C12', 'file'): True,
    ('C13', 'xref'): False,
    ('C14', 'file'): True,
    ('C15', 'file'): True,
    ('P1', 'base'): True,
    ('P3', 'base'): True,
    ('P4', 'prefix'): True,
    ('P5', 'prefix'): True,
    ('LOCAL', 'master'): True,
    ('LOCAL', 'hooks'): True,
    ('PR', 'advice'): False,
}

CHECK_NAMES = {
    'C1': 'STATE.md size', 'C2': 'banner', 'C3': 'sections and budgets', 'C4': 'no proof details',
    'C5': 'decisions', 'C6': 'open items', 'C7': 'pins', 'C8': 'ledgers', 'C9': 'directives and evidence',
    'C10': 'links', 'C11': 'repository hygiene', 'C12': 'text encoding', 'C13': 'CODEOWNERS',
    'C14': 'gate workflow', 'C15': 'policy',
}


# ---------------------------------------------------------------- errors and small helpers

class GateError(Exception):
    """An environment or configuration problem. The gate fails closed (exit 2)."""


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def read_text(path):
    return read_bytes(path).decode('utf-8')


def rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, '/')


def match(pattern, path):
    """Policy pattern: an exact path, 'dir/**', or single-segment wildcards (e.g. reports/_T_*.md)."""
    if pattern.endswith('/**'):
        return path.startswith(pattern[:-2])
    ps, xs = pattern.split('/'), path.split('/')
    return len(ps) == len(xs) and all(fnmatch.fnmatchcase(x, p) for p, x in zip(ps, xs))


def match_any(patterns, path):
    return any(match(p, path) for p in patterns)


def decision_kind(did):
    return 'CR' if did.startswith('CR-') else 'R'


def decision_status(text):
    st = [m.group(1) for m in (STATUS_RE.match(l) for l in text.split('\n')) if m]
    return st[0] if len(st) == 1 else None


def parse_manifest_text(text):
    entries, errors = {}, []
    for n, line in enumerate(text.split('\n'), 1):
        if not line.strip() or line.startswith('#'):
            continue
        m = PIN_LINE_RE.match(line)
        if not m:
            errors.append(f'line {n} is not "<sha256>  <path>": {line[:80]!r}')
            continue
        if m.group(2) in entries:
            errors.append(f'line {n}: {m.group(2)} is listed twice')
        entries[m.group(2)] = m.group(1)
    return entries, errors


def parse_manifest(path):
    if not os.path.isfile(path):
        return {}, []
    return parse_manifest_text(read_text(path))


def parse_register_text(text):
    rows, errors = [], []
    for line in text.split('\n'):
        if not line.startswith('|') or line.startswith('|---'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if cells and cells[0] == 'Ledger':
            continue
        if len(cells) != 4:
            errors.append(f'register row does not have 4 cells: {line!r}')
            continue
        rows.append({'file': cells[0].strip('`'), 'status': cells[1], 'first': cells[2], 'last': cells[3]})
    return rows, errors


def parse_ledger_register(path):
    if not os.path.isfile(path):
        return [], [f'missing register {path}']
    return parse_register_text(read_text(path))


def parse_ledger(text):
    """Structure of one ledger file (note s6.2)."""
    lines = text.split('\n')
    heads = [i for i, l in enumerate(lines) if l.startswith('## ')]
    header = '\n'.join(lines[:heads[0]] if heads else lines)
    out = {'header': header, 'entries': [], 'closed': None, 'problems': [], 'predecessor': None}
    preds = [m.group(1) for m in (PREDECESSOR_RE.match(l) for l in lines[:heads[0] if heads else len(lines)]) if m]
    out['predecessor'] = preds[0] if len(preds) == 1 else None
    if not lines or not lines[0].startswith('# LEDGER_'):
        out['problems'].append('the first line must be the "# LEDGER_####" title')
    for k, i in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        block = '\n'.join(lines[i:end])
        me, mc = LEDGER_ENTRY_RE.match(lines[i]), LEDGER_CLOSED_RE.match(lines[i])
        if mc:
            if k != len(heads) - 1:
                out['problems'].append('the CLOSED section must be the last section')
            out['closed'] = int(mc.group(1))
            out['closed_text'] = block
        elif me:
            if out['closed'] is not None:
                out['problems'].append('an entry follows the CLOSED section')
            out['entries'].append({'n': int(me.group(1)), 'bytes': len(block.encode('utf-8')), 'line': i + 1})
        else:
            out['problems'].append(f'line {i + 1}: {lines[i][:60]!r} is neither "## vN — YYYY-MM-DD — title" '
                                   'nor the CLOSED section')
    return out


def split_sections(text):
    lines = text.split('\n')
    heads = [i for i, l in enumerate(lines) if l.startswith('## ')]
    preamble = '\n'.join(lines[:heads[0]] if heads else lines)
    sections = []
    for k, i in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        m = SECTION_RE.match(lines[i])
        sections.append((m.group(1) if m else None, lines[i], '\n'.join(lines[i:end])))
    return preamble, sections


def strip_code(text):
    out, fence = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fence = not fence
            continue
        if not fence:
            out.append(re.sub(r'`[^`]*`', '', line))
    return '\n'.join(out)


# ---------------------------------------------------------------- the workflow YAML subset (C14)
#
# Workflow files are checked without a YAML library (standard library only). Instead of guessing
# every YAML spelling, the checker accepts only this subset and rejects everything else:
#   - block style; two-or-more-space indentation with spaces only; no tabs, no "---"/"..." markers;
#   - keys are plain names ([A-Za-z_][A-Za-z0-9_-]*) followed directly by ":" (no quotes, no space);
#   - values are plain scalars, simple quoted scalars without backslash escapes, "[a, b]" lists of
#     such scalars, "${{ ... }}" expressions, or "|" / ">" block scalars (whose content is skipped);
#   - no flow mappings ("{"), anchors ("&"), aliases ("*"), tags ("!") or merge keys.

WF_KEY_RE = re.compile(r'^( *)(- +)?([A-Za-z_][A-Za-z0-9_-]*):(?: +(.*))?$')
WF_ITEM_RE = re.compile(r'^( *)- +(.*)$')
WF_PLAIN = r'[A-Za-z0-9_./@*+=~^-][^\'"#,\[\]{}]*'
WF_SCALAR = r'(?:' + WF_PLAIN + r'|"[^"\\]*"|\'(?:[^\']|\'\')*\')'
WF_LIST_RE = re.compile(r'^\[\s*(?:' + WF_SCALAR + r'(?:\s*,\s*' + WF_SCALAR + r')*)?\s*\]$')


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
        return v[1:-1].replace("''", "'") if v[0] == "'" else v[1:-1]
    return v


def strip_comment(v):
    if v and v[0] in '"\'':
        q = v[0]
        end = v.find(q, 1)
        while q == "'" and end != -1 and v[end + 1:end + 2] == "'":
            end = v.find(q, end + 2)
        if end == -1:
            return v, 'unterminated quoted value'
        rest = v[end + 1:]
        if rest.strip() and not rest.lstrip().startswith('#'):
            return v, 'text after a quoted value'
        return v[:end + 1], None
    i = v.find(' #')
    return (v[:i] if i != -1 else v).rstrip(), None


def value_problem(v):
    core = re.sub(r'\$\{\{.*?\}\}', 'expr', v).strip()
    if not core or re.match(r'^[|>][-+]?$', core):
        return None
    if core[0] in '&*!%@`':
        return f'value {v[:40]!r} starts with an anchor, alias, tag or reserved character'
    if '{' in core or '}' in core:
        return f'value {v[:40]!r} uses a flow mapping or brace'
    if core[0] == '[':
        return None if WF_LIST_RE.match(core) else f'list {v[:40]!r} is outside the supported subset'
    if core[0] in '"\'':
        ok = re.fullmatch(r'"[^"\\]*"', core) or re.fullmatch(r"'(?:[^']|'')*'", core)
        return None if ok else f'quoted value {v[:40]!r} uses escapes or is malformed'
    if ': ' in core or core.endswith(':'):
        return f'plain value {v[:40]!r} contains ": "'
    return None


def parse_workflow(text):
    """Parse a workflow file in the kit's YAML subset. Returns (nodes, errors); each node is
    {'line', 'indent', 'key', 'value', 'path'} where path is the tuple of keys from the top."""
    nodes, errors, stack, block = [], [], [], None
    for n, raw in enumerate(text.split('\n'), 1):
        if '\t' in raw:
            errors.append(f'line {n}: tab character')
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        if block is not None:
            if not raw.strip() or indent > block:
                continue
            block = None
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        if s in ('---', '...'):
            errors.append(f'line {n}: document markers are outside the supported subset')
            continue
        m = WF_KEY_RE.match(raw)
        if m:
            ind = len(m.group(1)) + (len(m.group(2)) if m.group(2) else 0)
            value, err = strip_comment(m.group(4) or '')
            prob = err or value_problem(value)
            if prob:
                errors.append(f'line {n}: {prob}')
            while stack and stack[-1][0] >= ind:
                stack.pop()
            nodes.append({'line': n, 'indent': ind, 'key': m.group(3), 'value': value,
                          'path': tuple(k for _, k in stack) + (m.group(3),)})
            stack.append((ind, m.group(3)))
            if re.match(r'^[|>][-+]?$', value):
                block = ind
            continue
        m = WF_ITEM_RE.match(raw)
        if m:
            value, err = strip_comment(m.group(2))
            prob = err or value_problem(value)
            if prob:
                errors.append(f'line {n}: {prob}')
            continue
        errors.append(f'line {n}: {s[:50]!r} is outside the supported workflow subset '
                      '(plain keys written as "key:", block style, no quoted keys or flow mappings)')
    return nodes, errors


def banner_tag(text):
    for line in text.split('\n'):
        m = BANNER_RE.match(line)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- policy

def load_policy_bytes(b, where):
    try:
        cfg = json.loads(b.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as e:
        raise GateError(f'{where}: policy is not valid JSON: {e}')
    if cfg.get('schema_version') not in SUPPORTED_SCHEMAS:
        raise GateError(f'{where}: policy schema_version {cfg.get("schema_version")!r} is not supported by '
                        f'this checker (supported: {list(SUPPORTED_SCHEMAS)}); upgrade in two stages (note s8)')
    return cfg


def load_policy(root):
    path = os.path.join(root, *POLICY_PATH.split('/'))
    if not os.path.isfile(path):
        raise GateError(f'{POLICY_PATH} is missing in {root}')
    return load_policy_bytes(read_bytes(path), path)


def validate_policy(cfg):
    """Problems with a policy's own content (C15)."""
    problems = []
    reserved = set(cfg['paths']['reserved_top_level'])
    for p in cfg['paths'].get('core', []):
        first = p.split('/')[0]
        if not first or any(ch in first for ch in '*?['):
            problems.append(f'core pattern {p!r} must start with a literal top-level name')
        elif first in reserved:
            problems.append(f'core pattern {p!r} overlaps the reserved path {first!r}')
    for p in cfg['paths']['gate'] + cfg['paths']['templates'] + cfg['paths'].get('core', []):
        if '**' in p and not p.endswith('/**'):
            problems.append(f'pattern {p!r}: "**" is allowed only as a final "/**"')
    trunk = cfg.get('trunk', 'master')
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$', trunk) or '..' in trunk or trunk.endswith(('/', '.lock')):
        problems.append(f'trunk {trunk!r} is not a usable branch name')
    need = cfg['ledger_entry_max_bytes'] + cfg['ledger_header_max_bytes'] + 500
    if cfg['ledger_max_bytes'] < need:
        problems.append(f'ledger_max_bytes must be at least {need} (entry cap + header cap + closing section)')
    if not re.match(r'^@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(/[A-Za-z0-9._-]+)?$', cfg.get('owner', '')):
        problems.append('owner must be a GitHub user or team, e.g. "@name"')
    return problems


def codeowners_text(cfg):
    """The exact CODEOWNERS bytes for a policy (schema 1, note s8)."""
    pats = cfg['paths']['gate'] + cfg['paths']['templates'] + cfg['paths'].get('core', []) + \
        ['directives/**', 'docs/decisions/**', cfg['pins_file']]
    out, seen = [f'# Generated by tools/check_docs.py from {POLICY_PATH} (schema {cfg["schema_version"]}). '
                 'Do not edit by hand.'], set()
    for p in pats:
        line = '/' + (p[:-2] if p.endswith('/**') else p)
        if line not in seen:
            seen.add(line)
            out.append(f'{line} {cfg["owner"]}')
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------- git

def git(repo, *args, input=None, check=True):
    try:
        p = subprocess.run(['git', '-C', repo] + list(args), input=input, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    except OSError as e:
        raise GateError(f'git is not available: {e}')
    if check and p.returncode != 0:
        raise GateError(f'git {" ".join(args)} failed: {p.stderr.decode("utf-8", "replace").strip()}')
    return p


def git_out(repo, *args):
    return git(repo, *args).stdout.decode('utf-8').strip()


def git_blob(repo, treeish, path):
    p = git(repo, 'cat-file', 'blob', f'{treeish}:{path}', check=False)
    return p.stdout if p.returncode == 0 else None


def git_text(repo, treeish, path):
    b = git_blob(repo, treeish, path)
    return None if b is None else b.decode('utf-8')


def git_ls(repo, treeish, prefix=''):
    args = ['ls-tree', '-r', '--name-only', '-z', treeish]
    if prefix:
        args += ['--', prefix]
    out = git(repo, *args).stdout.decode('utf-8')
    return [p for p in out.split('\0') if p]


def git_diff(repo, a, b):
    out = git(repo, 'diff', '--no-renames', '--name-status', '-z', a, b).stdout.decode('utf-8').split('\0')
    out = [x for x in out if x]
    return [(out[i], out[i + 1]) for i in range(0, len(out), 2)]


def require_full_history(repo):
    """History-dependent checks refuse a shallow clone (fix-check finding F2)."""
    p = git(repo, 'rev-parse', '--is-shallow-repository', check=False)
    if p.returncode != 0:
        raise GateError('cannot tell whether the repository has full history')
    if p.stdout.decode().strip() == 'true':
        raise GateError('shallow clone: these checks need the full history (git fetch --unshallow, or '
                        'fetch-depth: 0 in CI)')


def commit_parents(repo, commit):
    """Parents read from the raw commit object's header, every one required to be present.

    rev-list and %P show a shallow commit as parentless, so a non-root commit could pass as a root
    commit and use its own checker (fix-check finding F2). The header cannot be hidden that way."""
    raw = git(repo, 'cat-file', 'commit', commit).stdout.decode('utf-8', 'replace')
    header = raw.split('\n\n', 1)[0]
    parents = [l.split()[1] for l in header.split('\n') if l.startswith('parent ')]
    for p in parents:
        if git(repo, 'cat-file', '-e', f'{p}^{{commit}}', check=False).returncode != 0:
            raise GateError(f'parent {p[:12]} of {commit[:12]} is not available: the full history is required')
    return parents


def is_work_tree(root):
    p = subprocess.run(['git', '-C', root, 'rev-parse', '--is-inside-work-tree'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return p.returncode == 0 and p.stdout.strip() == b'true'


def materialize(repo, treeish, dest):
    """Write the exact tracked paths and blob bytes of a commit or tree into dest.

    Uses ls-tree and cat-file only: no archive, export or checkout filters, so attributes such as
    export-ignore or export-subst cannot hide or transform a file (review finding R1). Returns a
    list of (kind, path) for entries that are not written as plain files: 'symlink', 'submodule',
    and 'case-collision' (paths that differ only in letter case, which Windows cannot hold)."""
    out = git(repo, 'ls-tree', '-r', '-z', '--full-tree', treeish).stdout.decode('utf-8')
    blobs, special, seen = [], [], {}
    for rec in [r for r in out.split('\0') if r]:
        meta, path = rec.split('\t', 1)
        mode, kind, sha = meta.split()
        if path.startswith('/') or '..' in path.split('/'):
            raise GateError(f'unsafe path in tree: {path}')
        low = path.lower()
        if low in seen:
            special.append(('case-collision', f'{seen[low]} / {path}'))
            continue
        seen[low] = path
        if mode == '160000':
            special.append(('submodule', path))
        elif mode == '120000':
            special.append(('symlink', path))
        elif kind == 'blob':
            blobs.append((sha, path))
        else:
            raise GateError(f'unexpected tree entry {mode} {kind} {path}')
    if blobs:
        data = git(repo, 'cat-file', '--batch', input=''.join(s + '\n' for s, _ in blobs).encode()).stdout
        pos = 0
        for sha, path in blobs:
            nl = data.index(b'\n', pos)
            header = data[pos:nl].split()
            if len(header) != 3 or header[0].decode() != sha or header[1] != b'blob':
                raise GateError(f'unexpected cat-file output for {path}')
            size = int(header[2])
            content = data[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
            fp = os.path.join(dest, *path.split('/'))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, 'wb') as f:
                f.write(content)
    return special


def hooks_problem(root):
    p = git(root, 'config', '--get', 'core.hooksPath', check=False)
    val = p.stdout.decode('utf-8').strip()
    return None if val == '.githooks' else f'core.hooksPath is {val or "unset"!r}, not ".githooks": run python tools/handover.py init'


# ---------------------------------------------------------------- findings

class Findings:
    def __init__(self):
        self.items = []   # (code, sub, msg, path)
        self.warns = []
        self.info = []

    def fail(self, code, sub, msg, path=None):
        self.items.append((code, sub, msg, path))

    def warn(self, code, msg):
        self.warns.append((code, msg))

    def extend(self, other):
        self.items += other.items
        self.warns += other.warns
        self.info += other.info

    def blocking(self, local=False):
        if not local:
            return list(self.items)
        return [x for x in self.items if LOCAL_CLASS.get((x[0], x[1]), True)]

    def advisory(self, local=False):
        if not local:
            return []
        return [x for x in self.items if not LOCAL_CLASS.get((x[0], x[1]), True)]


# ---------------------------------------------------------------- snapshot checks (C1-C15)

class Snapshot:
    """Checks of one tree on disk. cfg supplies the limits; head_cfg is the snapshot's own policy."""

    def __init__(self, root, cfg, head_cfg=None, expect_tag=None, specials=()):
        self.root, self.cfg, self.head_cfg = root, cfg, head_cfg or cfg
        self.specials = list(specials)
        self.expect_tag = expect_tag
        self.f = Findings()
        self.state_text = None
        self.sections = {}
        self.banner_tag = None
        self.pins = {}
        self.sizes = []

    def p(self, relp):
        return os.path.join(self.root, *relp.split('/'))

    def fail(self, code, sub, msg, path=None):
        self.f.fail(code, sub, msg, path)

    def size(self, relp, cap):
        fp = self.p(relp)
        if os.path.isfile(fp):
            self.sizes.append((relp, os.path.getsize(fp), cap))

    def run(self):
        self.c7_pins()
        self.c1_state()
        self.c2_banner()
        self.c3_sections()
        self.c4_proof()
        self.c5_decisions()
        self.c6_open_items()
        self.c8_ledgers()
        self.c9_directives()
        self.c10_links()
        self.c11_hygiene()
        self.c12_text()
        self.c13_codeowners()
        self.c14_gate()
        self.c15_policy()
        return self.f

    # C1
    def c1_state(self):
        fp = self.p(self.cfg['state_file'])
        if not os.path.isfile(fp):
            self.fail('C1', 'cap', f'{self.cfg["state_file"]} is missing')
            return
        raw = read_bytes(fp)
        try:
            self.state_text = raw.decode('utf-8')
        except UnicodeDecodeError as e:
            self.fail('C1', 'cap', f'{self.cfg["state_file"]} is not valid UTF-8: {e}')
            return
        self.size(self.cfg['state_file'], self.cfg['state_max_bytes'])
        if len(raw) > self.cfg['state_max_bytes']:
            self.fail('C1', 'cap', f'{len(raw):,} B is over the hard cap of {self.cfg["state_max_bytes"]:,} B')
        elif len(raw) > self.cfg['state_warn_bytes']:
            self.f.warn('C1', f'{len(raw):,} B is over the warning level of {self.cfg["state_warn_bytes"]:,} B')

    # C2
    def c2_banner(self):
        if self.state_text is None:
            return
        lines = self.state_text.split('\n')
        if not lines[0].startswith('# '):
            self.fail('C2', 'form', 'the first line of STATE.md must be its "# " title')
        banners = [(i, l) for i, l in enumerate(lines) if l.startswith(BANNER_PREFIX)]
        if len(banners) != 1:
            self.fail('C2', 'form', f'expected exactly one "{BANNER_PREFIX}" line, found {len(banners)}')
            return
        i, line = banners[0]
        heads = [k for k, l in enumerate(lines) if l.startswith('## ')]
        if heads and i > heads[0]:
            self.fail('C2', 'form', 'the banner must come before the first "## " section')
        if len(line) > self.cfg['banner_max_chars']:
            self.fail('C2', 'form', f'banner is {len(line)} characters (max {self.cfg["banner_max_chars"]})')
        m = BANNER_RE.match(line)
        if not m:
            self.fail('C2', 'form', 'banner must read "CURRENT BASELINE: tag vN (YYYY-MM-DD) — <title>"')
            return
        self.banner_tag = m.group(1)
        if self.expect_tag is not None and self.banner_tag != self.expect_tag:
            self.fail('C2', 'expect', f'banner tag {self.banner_tag!r} is not the expected {self.expect_tag!r}')

    # C3
    def c3_sections(self):
        if self.state_text is None:
            return
        preamble, sections = split_sections(self.state_text)
        n = len(preamble.encode('utf-8'))
        if n > self.cfg['preamble_max_bytes']:
            self.fail('C3', 'sections', f'the text before S1 is {n:,} B (max {self.cfg["preamble_max_bytes"]:,} B)')
        wanted = [s['id'] for s in self.cfg['sections']]
        budget = {s['id']: s['max_bytes'] for s in self.cfg['sections']}
        found = []
        for sid, heading, block in sections:
            if sid is None:
                self.fail('C3', 'sections', f'unexpected heading {heading!r}')
                continue
            found.append(sid)
            self.sections[sid] = block
            n = len(block.encode('utf-8'))
            if sid in budget and n > budget[sid]:
                self.fail('C3', 'sections', f'{sid} is {n:,} B, over its budget of {budget[sid]:,} B')
            elif sid in budget and n > 0.8 * budget[sid]:
                self.f.warn('C3', f'{sid} is {n * 100 // budget[sid]}% of its budget')
        if found != wanted:
            self.fail('C3', 'sections', f'sections found {found}, expected exactly {wanted} in that order')

    # C4
    def c4_proof(self):
        if self.state_text is None:
            return
        pats = [(p['name'], re.compile(p['regex'])) for p in self.cfg['state_forbidden_patterns']]
        hits = []
        for n, line in enumerate(self.state_text.split('\n'), 1):
            hits += [f'line {n}: {name}' for name, rx in pats if rx.search(line)]
            if len(line) > self.cfg['state_max_line_chars']:
                hits.append(f'line {n}: {len(line)} characters (max {self.cfg["state_max_line_chars"]})')
        for h in hits[:10]:
            self.fail('C4', 'proof', h)
        if len(hits) > 10:
            self.fail('C4', 'proof', f'... and {len(hits) - 10} more')

    # C5
    def c5_decisions(self):
        ddir = self.p(self.cfg['decisions_dir'])
        files, supersedes, statuses = {}, {}, {}
        if os.path.isdir(ddir):
            for name in sorted(os.listdir(ddir)):
                fp = os.path.join(ddir, name)
                if name.startswith('_') or not os.path.isfile(fp):
                    continue
                relp = f'{self.cfg["decisions_dir"]}/{name}'
                m = DECISION_FILE_RE.match(name)
                if not m:
                    self.fail('C5', 'file', f'{relp}: name must be R-####-slug.md or CR-####-slug.md')
                    continue
                did = m.group(1)
                if did in files:
                    self.fail('C5', 'file', f'{did} has two files: {files[did]} and {name}')
                    continue
                files[did] = name
                text = read_text(fp)
                if len(text.encode('utf-8')) > self.cfg['decision_max_bytes']:
                    self.fail('C5', 'file', f'{name} is over {self.cfg["decision_max_bytes"]:,} B')
                lines = text.split('\n')
                mt = DECISION_TITLE_RE.match(lines[0]) if lines else None
                if not mt or mt.group(1) != did:
                    self.fail('C5', 'file', f'{name}: first line must be "# {did} — <title>"')
                st = decision_status(text)
                if st not in DECISION_STATUSES[decision_kind(did)]:
                    self.fail('C5', 'file', f'{name}: needs exactly one "**Status:** '
                                            f'{"|".join(DECISION_STATUSES[decision_kind(did)])}" line')
                    st = None
                statuses[did] = st
                sup = set()
                for l in lines:
                    ms = SUPERSEDES_RE.match(l)
                    if ms and ms.group(1).strip().lower() != 'none':
                        sup.update(DECISION_ID_RE.findall(ms.group(1)))
                supersedes[did] = sup
        if self.state_text is not None:
            for did in sorted(set(DECISION_ID_RE.findall(self.state_text))):
                if did not in files:
                    self.fail('C5', 'xref', f'STATE.md names {did}, which has no file')
        reg = set(DECISION_ID_RE.findall(self.sections.get(self.cfg['register_section'], '')))
        superseded = set().union(*supersedes.values()) if supersedes else set()
        for did in sorted(reg & superseded):
            self.fail('C5', 'xref', f'{did} is superseded but still listed as in force')
        for did, sup in supersedes.items():
            for s in sorted(sup - set(files)):
                self.fail('C5', 'xref', f'{files[did]} supersedes {s}, which has no file')
        reach, todo = set(), [d for d in reg if d in files]
        while todo:
            d = todo.pop()
            if d not in reach:
                reach.add(d)
                todo.extend(s for s in supersedes.get(d, ()) if s in files)
        for did in sorted(set(files) - reach):
            self.fail('C5', 'xref', f'{files[did]} is an orphan: list it in {self.cfg["register_section"]} or '
                                    'name it on the Supersedes line of its successor')
        for did, st in statuses.items():
            relp = f'{self.cfg["decisions_dir"]}/{files[did]}'
            if st in IMMUTABLE_STATUSES and relp not in self.pins:
                self.fail('C5', 'xref', f'{relp} is {st} but not pinned')

    # C6
    def c6_open_items(self):
        block = self.sections.get(self.cfg['open_items_section'])
        if block is None:
            return
        rx, ok, seen = re.compile(self.cfg['open_item_regex']), set(self.cfg['open_item_statuses']), set()
        for line in block.split('\n')[1:]:
            if not line.startswith('- '):
                continue
            m = rx.match(line)
            if not m:
                self.fail('C6', 'format', f'item is not "- OI-#### [Status] text": {line[:80]!r}')
                continue
            if m.group(1) in seen:
                self.fail('C6', 'format', f'{m.group(1)} is listed twice')
            seen.add(m.group(1))
            if m.group(2) not in ok:
                self.fail('C6', 'format', f'{m.group(1)} has status [{m.group(2)}]: closed items leave STATE.md')

    # C7
    def c7_pins(self):
        fp = self.p(self.cfg['pins_file'])
        if os.path.isfile(fp):
            self.pins, errors = parse_manifest_text(read_text(fp))
        else:
            self.pins, errors = {}, []
        for e in errors:
            self.fail('C7', 'manifest', f'{self.cfg["pins_file"]}: {e}')
        for relp, sha in sorted(self.pins.items()):
            tp = self.p(relp)
            if not os.path.isfile(tp):
                self.fail('C7', 'match-base', f'pinned file {relp} is missing', path=relp)
            elif sha256_bytes(read_bytes(tp)) != sha:
                self.fail('C7', 'match-base', f'pinned file {relp} has changed: pinned files are immutable', path=relp)

    # C8
    def c8_ledgers(self):
        cfg = self.cfg
        rows, errors = parse_ledger_register(self.p(cfg['ledger_register']))
        for e in errors:
            self.fail('C8', 'xref', e)
        if not rows:
            self.fail('C8', 'xref', 'the ledger register lists no ledger')
            return
        hdir = self.p(cfg['history_dir'])
        on_disk = sorted(n for n in os.listdir(hdir) if LEDGER_FILE_RE.match(n)) if os.path.isdir(hdir) else []
        listed = [r['file'] for r in rows]
        for n in on_disk:
            if n not in listed:
                self.fail('C8', 'xref', f'{n} is not in the ledger register')
        opens = [i for i, r in enumerate(rows) if r['status'] == 'OPEN']
        if len(opens) != 1 or opens[0] != len(rows) - 1:
            self.fail('C8', 'xref', 'exactly one ledger must be OPEN, and it must be the last row')
        numbers = []
        for i, r in enumerate(rows):
            name = r['file']
            if r['status'] not in ('OPEN', 'CLOSED') or not LEDGER_FILE_RE.match(name):
                self.fail('C8', 'xref', f'register row {name!r} {r["status"]!r} is not valid')
                continue
            fp = os.path.join(hdir, name)
            if not os.path.isfile(fp):
                self.fail('C8', 'xref', f'{name} is listed but missing')
                continue
            text = read_text(fp)
            led = parse_ledger(text)
            for prob in led['problems']:
                self.fail('C8', 'entry', f'{name}: {prob}')
            if len(led['header'].encode('utf-8')) > cfg['ledger_header_max_bytes']:
                self.fail('C8', 'entry', f'{name}: header is over {cfg["ledger_header_max_bytes"]:,} B')
            for e in led['entries']:
                if e['bytes'] > cfg['ledger_entry_max_bytes']:
                    self.fail('C8', 'entry', f'{name}: entry v{e["n"]} is {e["bytes"]:,} B '
                                             f'(max {cfg["ledger_entry_max_bytes"]:,} B)')
            want_pred = 'none' if i == 0 else rows[i - 1]['file']
            if led['predecessor'] != want_pred:
                self.fail('C8', 'xref', f'{name}: header must say "Predecessor: {want_pred}"')
            numbers += [e['n'] for e in led['entries']]
            relp = f'{cfg["history_dir"]}/{name}'
            if r['status'] == 'CLOSED':
                if led['closed'] is None:
                    self.fail('C8', 'xref', f'{name} is CLOSED but has no CLOSED section')
                nxt = rows[i + 1]['file'] if i + 1 < len(rows) else None
                if nxt and f'Successor: {nxt}' not in led.get('closed_text', ''):
                    self.fail('C8', 'xref', f'{name} must name its successor: "Successor: {nxt}"')
                if relp not in self.pins:
                    self.fail('C8', 'xref', f'{relp} is CLOSED but not pinned')
            else:
                self.size(relp, cfg['ledger_max_bytes'])
                if led['closed'] is not None:
                    self.fail('C8', 'xref', f'{name} is OPEN but has a CLOSED section')
                if len(text.encode('utf-8')) > cfg['ledger_max_bytes']:
                    self.fail('C8', 'xref', f'{name} is over {cfg["ledger_max_bytes"]:,} B: it should have rolled over')
        if numbers != list(range(len(numbers))):
            self.fail('C8', 'xref', f'ledger entries must be v0, v1, v2, ... with no gap or repeat; found '
                                    f'{["v%d" % n for n in numbers][:12]}')
        self.last_entry = numbers[-1] if numbers else None
        if self.banner_tag and numbers and self.banner_tag != f'v{numbers[-1]}':
            self.fail('C8', 'xref', f'banner tag {self.banner_tag} but the last ledger entry is v{numbers[-1]}')
        if not numbers:
            self.fail('C8', 'xref', 'the ledgers hold no entry')

    # C9
    def c9_directives(self):
        ddir = self.p(self.cfg['directives_dir'])
        if not os.path.isdir(ddir):
            return
        for name in sorted(os.listdir(ddir)):
            sub = os.path.join(ddir, name)
            srel = f'{self.cfg["directives_dir"]}/{name}'
            if not os.path.isdir(sub):
                self.fail('C9', 'xref', f'{srel}: directives live in directives/<vN>/DIRECTIVE.md')
                continue
            if name != '_TEMPLATE' and not re.match(r'^v\d+$', name):
                self.fail('C9', 'xref', f'{srel}: directive folders are named vN')
            dpath = os.path.join(sub, 'DIRECTIVE.md')
            if not os.path.isfile(dpath):
                self.fail('C9', 'xref', f'{srel}/ has no DIRECTIVE.md')
            else:
                n = os.path.getsize(dpath)
                if name == self.banner_tag:
                    self.size(f'{srel}/DIRECTIVE.md', self.cfg['directive_max_bytes'])
                if n > self.cfg['directive_max_bytes']:
                    self.fail('C9', 'size', f'{srel}/DIRECTIVE.md is {n:,} B (max {self.cfg["directive_max_bytes"]:,} B)')
                elif n > self.cfg['directive_warn_bytes']:
                    self.f.warn('C9', f'{srel}/DIRECTIVE.md is {n:,} B (warning at {self.cfg["directive_warn_bytes"]:,} B)')
                if name != '_TEMPLATE':
                    tiers = [m.group(1) for m in (TIER_RE.match(l) for l in read_text(dpath).split('\n')) if m]
                    if len(tiers) != 1 or tiers[0] not in ('1', '2'):
                        self.fail('C9', 'tier', f'{srel}/DIRECTIVE.md needs exactly one "**Tier:** 1" or "**Tier:** 2" line')
            edir = os.path.join(sub, 'evidence')
            if not os.path.isdir(edir):
                continue
            mpath = os.path.join(edir, 'MANIFEST.sha256')
            if not os.path.isfile(mpath):
                self.fail('C9', 'xref', f'{srel}/evidence/ has no MANIFEST.sha256')
                continue
            entries, errors = parse_manifest(mpath)
            for e in errors:
                self.fail('C9', 'xref', f'{srel}/evidence/MANIFEST.sha256: {e}')
            for fn in sorted(os.listdir(edir)):
                if fn != 'MANIFEST.sha256' and fn not in entries and os.path.isfile(os.path.join(edir, fn)):
                    self.fail('C9', 'xref', f'{srel}/evidence/{fn} is not in MANIFEST.sha256')
            for fn, sha in sorted(entries.items()):
                fp = os.path.join(edir, fn)
                erel = f'{srel}/evidence/{fn}'
                if not os.path.isfile(fp):
                    self.fail('C9', 'evidence-base', f'{erel} is in the manifest but missing', path=erel)
                elif sha256_bytes(read_bytes(fp)) != sha:
                    self.fail('C9', 'evidence-base', f'{erel} does not match its manifest hash', path=erel)

    # C10
    def governed_docs(self):
        out = [self.p(n) for n in self.cfg['root_md_allowlist'] if os.path.isfile(self.p(n))]
        for base in ('docs', self.cfg['directives_dir'], self.cfg['reports_dir']):
            for dp, dns, fns in os.walk(self.p(base)):
                dns[:] = [d for d in dns if d != 'evidence']
                out += [os.path.join(dp, f) for f in fns if f.endswith('.md')]
        return sorted(out)

    def c10_links(self):
        for fp in self.governed_docs():
            for target in LINK_RE.findall(strip_code(read_text(fp))):
                if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I) or target.startswith('#') or '<' in target:
                    continue
                t = re.sub(r':\d+(-\d+)?$', '', re.sub(r'#.*$', '', target))
                if t and not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(fp), t))):
                    self.fail('C10', 'xref', f'{rel(self.root, fp)} links to {target}, which does not exist')

    # C11
    def c11_hygiene(self):
        cfg = self.cfg
        for kind, path in self.specials:
            if kind == 'case-collision':
                self.fail('C11', 'file', f'{path}: paths that differ only in letter case cannot be checked out on Windows')
            elif kind == 'symlink' and not cfg.get('allow_symlinks', False):
                self.fail('C11', 'file', f'{path} is a symbolic link; the policy does not allow them (allow_symlinks)')
            elif kind == 'submodule' and not cfg.get('allow_submodules', False):
                self.fail('C11', 'file', f'{path} is a submodule; the policy does not allow them (allow_submodules)')
        allow = set(cfg['root_md_allowlist'])
        for n in sorted(os.listdir(self.root)):
            if n.lower().endswith('.md') and os.path.isfile(self.p(n)) and n not in allow:
                self.fail('C11', 'file', f'{n} in the repository root: give it a home under docs/')
        readme = self.p('README.md')
        if os.path.isfile(readme):
            self.size('README.md', cfg['readme_max_bytes'])
            if os.path.getsize(readme) > cfg['readme_max_bytes']:
                self.fail('C11', 'file', f'README.md is over {cfg["readme_max_bytes"]:,} B')
            if any(l.startswith(BANNER_PREFIX) for l in read_text(readme).split('\n')):
                self.fail('C11', 'file', 'README.md carries a baseline banner: state lives only in STATE.md')
        for n, cap in cfg['protocol_files'].items():
            fp = self.p(n)
            if os.path.isfile(fp):
                self.size(n, cap)
                if os.path.getsize(fp) > cap:
                    self.fail('C11', 'file', f'{n} is over {cap:,} B')
                if any(l.startswith(BANNER_PREFIX) for l in read_text(fp).split('\n')):
                    self.fail('C11', 'file', f'{n} carries a baseline banner')
        ddir = self.p(cfg['design_dir'])
        if os.path.isdir(ddir):
            rx = re.compile(cfg['design_forbidden_name_regex'])
            for n in sorted(os.listdir(ddir)):
                fp = os.path.join(ddir, n)
                if rx.search(n):
                    self.fail('C11', 'file', f'{cfg["design_dir"]}/{n}: revisions live in git history, not copies')
                elif n.endswith('.md') and os.path.getsize(fp) > cfg['design_max_bytes']:
                    self.fail('C11', 'file', f'{cfg["design_dir"]}/{n} is over {cfg["design_max_bytes"]:,} B')
        rdir = self.p(cfg['reports_dir'])
        if os.path.isdir(rdir):
            for n in sorted(os.listdir(rdir)):
                fp = os.path.join(rdir, n)
                if n.startswith('_') or not os.path.isfile(fp):
                    continue
                m = REPORT_FILE_RE.match(n)
                cap = cfg['verification_max_bytes'] if m and m.group(1) == 'VERIFICATION' else cfg['report_max_bytes']
                if not m:
                    self.fail('C11', 'file', f'{cfg["reports_dir"]}/{n}: reports are SESSION_REPORT_vN.md or VERIFICATION_vN.md')
                elif os.path.getsize(fp) > cap:
                    self.fail('C11', 'file', f'{cfg["reports_dir"]}/{n} is over {cap:,} B')

    # C12
    def c12_text(self):
        exts, skip = tuple(self.cfg['text_extensions']), set(self.cfg['text_skip_dirs'])
        for dp, dns, fns in os.walk(self.root):
            dns[:] = [d for d in dns if d not in skip]
            for fn in fns:
                if not fn.lower().endswith(exts):
                    continue
                fp = os.path.join(dp, fn)
                b = read_bytes(fp)
                probs = []
                if b'\x00' in b:
                    probs.append('NUL bytes')
                if b.startswith(b'\xef\xbb\xbf'):
                    probs.append('a UTF-8 BOM')
                if b'\r' in b:
                    probs.append('CR line endings')
                try:
                    b.decode('utf-8')
                except UnicodeDecodeError:
                    probs.append('invalid UTF-8')
                if probs:
                    self.fail('C12', 'file', f'{rel(self.root, fp)} has {", ".join(probs)}')

    # C13
    def c13_codeowners(self):
        fp = self.p('.github/CODEOWNERS')
        want = codeowners_text(self.head_cfg)
        if not os.path.isfile(fp):
            self.fail('C13', 'xref', '.github/CODEOWNERS is missing: run python tools/handover.py codeowners')
        elif read_bytes(fp) != want.encode('utf-8'):
            self.fail('C13', 'xref', '.github/CODEOWNERS differs from the file generated from tools/policy.json')

    # C14
    def c14_gate(self):
        """Workflow files must use the kit's YAML subset (parse_workflow); within it, the gate job is
        unique and unconditional. Review finding R3: a text regex cannot recognise every YAML
        spelling of a key, so spellings outside the subset are rejected instead of guessed."""
        gate, job = self.head_cfg['gate']['workflow'], self.head_cfg['gate']['job']
        wdir = self.p('.github/workflows')
        if not os.path.isfile(self.p(gate)):
            self.fail('C14', 'file', f'{gate} is missing')
        if not os.path.isdir(wdir):
            return
        for n in sorted(os.listdir(wdir)):
            if not n.endswith(('.yml', '.yaml')):
                continue
            relp = f'.github/workflows/{n}'
            nodes, errors = parse_workflow(read_text(os.path.join(wdir, n)))
            for e in errors:
                self.fail('C14', 'file', f'{relp} {e}')
            jobs = [x for x in nodes if len(x['path']) == 2 and x['path'][0] == 'jobs']
            name_nodes = [x for x in nodes if len(x['path']) == 3 and x['path'][0] == 'jobs' and x['key'] == 'name']
            for x in name_nodes:
                if '${{' in x['value']:
                    # A required check is matched by job name, so names must be static text: an expression
                    # could evaluate to the gate's name (fix-check finding F1). Expressions are not evaluated.
                    self.fail('C14', 'file', f'{relp} line {x["line"]}: a job name must be static text, not an expression')
            names = {x['path'][1]: unquote(x['value']) for x in name_nodes}
            if relp != gate:
                for j in jobs:
                    if j['key'] == job or names.get(j['key']) == job:
                        self.fail('C14', 'file', f'{relp} line {j["line"]} defines a job named {job!r}; only {gate} may')
                continue
            if [j['key'] for j in jobs] != [job]:
                self.fail('C14', 'file', f'{gate} must define exactly one job, "{job}"')
            if job in names and names[job] != job:
                self.fail('C14', 'file', f'{gate}: the gate job\'s name must be "{job}"')
            for x in nodes:
                if x['key'] == 'if':
                    self.fail('C14', 'file', f'{gate} line {x["line"]}: no condition ("if") is allowed in the gate workflow')
                if x['key'] in ('paths', 'paths-ignore', 'branches-ignore', 'continue-on-error'):
                    self.fail('C14', 'file', f'{gate} line {x["line"]}: "{x["key"]}" is not allowed in the gate workflow')
            if not any(x['path'] == ('on', 'pull_request') for x in nodes):
                self.fail('C14', 'file', f'{gate} must run on pull_request (as an "on:" mapping)')
            trunk = self.head_cfg.get('trunk', 'master')
            br = [x for x in nodes if x['path'] == ('on', 'pull_request', 'branches')]
            branches = [unquote(v) for v in re.findall(r'[^\[\],\s]+', br[0]['value'])] if len(br) == 1 else None
            if branches != [trunk]:
                self.fail('C14', 'file', f'{gate} must run on pull requests into the trunk only: "branches: [{trunk}]"')

    # C15
    def c15_policy(self):
        for prob in validate_policy(self.head_cfg):
            self.fail('C15', 'file', f'{POLICY_PATH}: {prob}')


# ---------------------------------------------------------------- tree-level reading (for PR rules)

class Tree:
    """Read-only view of one commit or tree through git."""

    def __init__(self, repo, treeish, cfg):
        self.repo, self.ref, self.cfg = repo, treeish, cfg
        self._paths = None

    def paths(self):
        if self._paths is None:
            self._paths = set(git_ls(self.repo, self.ref))
        return self._paths

    def has(self, path):
        return path in self.paths()

    def text(self, path):
        return git_text(self.repo, self.ref, path) if self.has(path) else None

    def blob(self, path):
        return git_blob(self.repo, self.ref, path) if self.has(path) else None

    def register(self):
        t = self.text(self.cfg['ledger_register'])
        return parse_register_text(t)[0] if t else []

    def ledger_numbers(self):
        nums = []
        for r in self.register():
            t = self.text(f'{self.cfg["history_dir"]}/{r["file"]}')
            if t is not None:
                nums += [e['n'] for e in parse_ledger(t)['entries']]
        return nums

    def closed_ledgers(self):
        return [f'{self.cfg["history_dir"]}/{r["file"]}' for r in self.register() if r['status'] == 'CLOSED']


def is_template(cfg, path):
    return match_any(cfg['paths']['templates'], path)


def pin_target(cfg, path, text, register_rows):
    """(required tier, None) if path may be pinned, else (None, reason). Review finding R2.

    Only two kinds of file are ever pinned: a CLOSED ledger (tier 1; ledger-append pins it on
    rollover) and an Accepted ruling or Resolved CR (tier 2). Open ledgers, Proposed rulings, Open
    CRs, templates and every other file are refused."""
    name = path.split('/')[-1]
    if text is None:
        return None, f'{path} does not exist'
    if path == f'{cfg["history_dir"]}/{name}' and LEDGER_FILE_RE.match(name):
        if parse_ledger(text)['closed'] is None:
            return None, f'{path} is an OPEN ledger: only a closed ledger is pinned (ledger-append does it on rollover)'
        rows = {r['file']: r['status'] for r in register_rows or []}
        if register_rows is not None and rows.get(name) != 'CLOSED':
            return None, f'{path} is not CLOSED in the ledger register'
        return 1, None
    if path == f'{cfg["decisions_dir"]}/{name}' and DECISION_FILE_RE.match(name) and not is_template(cfg, path):
        st = decision_status(text)
        if st not in IMMUTABLE_STATUSES:
            return None, f'{path} is {st or "without a valid status"}: only an Accepted ruling or a Resolved CR is pinned'
        return 2, None
    return None, f'{path} is not a kind of file that is pinned (a closed ledger, or an accepted or resolved decision)'


def under(path, d):
    return path.startswith(d.rstrip('/') + '/')


# ---------------------------------------------------------------- record rules and tiers (PR check)

def byte_preservation(cfg, base, head, f, local=False):
    """P1, P3, P4 first half, P5 first half: bytes that exist on the base are unchanged.
    Correct against a stale base, so these may block locally (note s4)."""
    bpaths = base.paths()
    for path in sorted(bpaths):
        record = (under(path, cfg['directives_dir']) or under(path, cfg['reports_dir'])) and not is_template(cfg, path)
        if record or path in base.closed_ledgers():
            if not head.has(path):
                f.fail('P1', 'base', f'closed record {path} was deleted or renamed')
            elif head.blob(path) != base.blob(path):
                f.fail('P1', 'base', f'closed record {path} was changed')
        if under(path, cfg['decisions_dir']) and not is_template(cfg, path):
            st = decision_status(base.text(path) or '')
            if not head.has(path):
                f.fail('P3', 'base', f'decision file {path} was deleted or renamed')
            elif st in IMMUTABLE_STATUSES and head.blob(path) != base.blob(path):
                f.fail('P3', 'base', f'decision file {path} is {st} on the base and was changed')
    pb, ph = base.blob(cfg['pins_file']) or b'', head.blob(cfg['pins_file']) or b''
    if not ph.startswith(pb):
        f.fail('P4', 'prefix', f'{cfg["pins_file"]}: the base lines were changed, removed or reordered')
    for r in base.register():
        path = f'{cfg["history_dir"]}/{r["file"]}'
        lb, lh = base.blob(path), head.blob(path)
        if lb is not None and (lh is None or not lh.startswith(lb)):
            f.fail('P5', 'prefix', f'{path}: the base content is not preserved as a byte prefix')
        elif lb is not None and lh[len(lb):] and not LEDGER_SUFFIX_RE.match(lh[len(lb):]):
            # Existing entries stay unchanged: what is added must start a new entry or the CLOSED
            # section, never extend the last existing entry (review, "historical-entry extension").
            f.fail('P5', 'prefix', f'{path}: text was added to an existing entry; new text must begin '
                                   'with the next "## vN — " heading or the "## CLOSED — " section')


def tier_rules(cfg, base, head, f, changes):
    """P2, the rest of P4 and P5, tiers and completeness. Needs the true base (advisory locally)."""
    info = {}
    nums = base.ledger_numbers()
    N = (nums[-1] + 1) if nums else 0
    info['N'] = N
    vN = f'v{N}'
    added = {p for s, p in changes if s == 'A'}
    changed = [(s, p) for s, p in changes]
    new_dirs = sorted({p.split('/')[1] for p in added
                       if under(p, cfg['directives_dir']) and not is_template(cfg, p) and len(p.split('/')) > 2})
    checkpoint = bool(new_dirs)
    info['checkpoint'] = checkpoint
    declared = 0
    if checkpoint:
        if new_dirs != [vN]:
            f.fail('N', 'pr', f'new directive folder(s) {new_dirs}: this checkpoint must be {vN} (one more than '
                              f'the last ledger entry on the base)')
        dpath = f'{cfg["directives_dir"]}/{vN}/DIRECTIVE.md'
        if dpath not in added:
            f.fail('K', 'pr', f'{dpath} must be added by this checkpoint PR')
        else:
            tiers = [m.group(1) for m in (TIER_RE.match(l) for l in head.text(dpath).split('\n')) if m]
            if len(tiers) == 1 and tiers[0] in ('1', '2'):
                declared = int(tiers[0])
            else:
                f.fail('K', 'pr', f'{dpath} needs exactly one "**Tier:** 1" or "**Tier:** 2" line')
    info['declared'] = declared
    own = {f'{cfg["reports_dir"]}/SESSION_REPORT_{vN}.md', f'{cfg["reports_dir"]}/VERIFICATION_{vN}.md'}

    # P2
    for p in sorted(added):
        if (under(p, cfg['directives_dir']) or under(p, cfg['reports_dir'])) and not is_template(cfg, p):
            mine = checkpoint and (under(p, f'{cfg["directives_dir"]}/{vN}') or p in own)
            if not mine:
                f.fail('P2', 'pr', f'{p}: a new file under directives/ or reports/ is allowed only as the '
                                   f'checkpoint\'s own record ({vN})')

    # P4: kinds of new pin lines
    pb = (base.text(cfg['pins_file']) or '')
    ph = (head.text(cfg['pins_file']) or '')
    pin_req = 0
    if ph.startswith(pb):
        new_pins, errs = parse_manifest_text(ph[len(pb):])
        for e in errs:
            f.fail('P4', 'pr', f'{cfg["pins_file"]}: {e}')
        rows = head.register()
        for path in new_pins:
            req, why = pin_target(cfg, path, head.text(path), rows)
            if why:
                f.fail('P4', 'pr', f'{cfg["pins_file"]}: {why}')
            else:
                pin_req = max(pin_req, req)

    # P5: ledger delta and register transitions
    hnums = head.ledger_numbers()
    new_entries = hnums[len(nums):] if hnums[:len(nums)] == nums else None
    if new_entries is None:
        f.fail('P5', 'pr', 'the ledger entries of the base are not preserved in order')
    elif checkpoint and new_entries != [N]:
        f.fail('P5', 'pr', f'a checkpoint PR adds exactly one ledger entry, {vN}; it adds '
                           f'{["v%d" % n for n in new_entries]}')
    elif not checkpoint and new_entries:
        f.fail('P5', 'pr', 'a routine PR adds no ledger entry')
    rb, rh = base.register(), head.register()
    ok_reg = len(rh) >= len(rb) and len(rh) - len(rb) <= 1
    for i, r in enumerate(rb):
        if not ok_reg:
            break
        h = rh[i]
        if h['file'] != r['file']:
            ok_reg = False
        elif (h['status'], h['first'], h['last']) != (r['status'], r['first'], r['last']):
            first_ok = h['first'] == r['first'] or (r['first'] in ('—', '-', '') and re.match(r'^v\d+$', h['first']))
            last_ok = h['last'] == r['last'] or (r['last'] in ('—', '-', '') and re.match(r'^v\d+$', h['last'])
                                                 and h['status'] == 'CLOSED')
            status_ok = h['status'] == r['status'] or (r['status'] == 'OPEN' and h['status'] == 'CLOSED'
                                                      and i == len(rb) - 1 and len(rh) == len(rb) + 1)
            ok_reg = bool(first_ok and last_ok and status_ok)
    if ok_reg and len(rh) == len(rb) + 1 and rh[-1]['status'] != 'OPEN':
        ok_reg = False
    if not ok_reg:
        f.fail('P5', 'pr', f'{cfg["ledger_register"]}: only the transitions of note s6.2 are allowed')

    # tiers
    required = pin_req
    for s, p in changed:
        req, why = path_requirement(cfg, base, head, s, p, checkpoint, vN, own)
        if req == 'checkpoint-only':
            if not checkpoint:
                f.fail('T', 'pr', f'{p}: checkpoint records change only in a checkpoint PR')
            req = 1
        required = max(required, req)
        if req == 2:
            info.setdefault('tier2_paths', []).append(p)
    info['required'] = required
    if declared < required:
        f.fail('T', 'pr', f'required tier {required}, declared tier {declared}'
                          + (f' (Tier 2 because of {sorted(info.get("tier2_paths", []))[:5]})' if required == 2 else ''))

    # completeness
    if checkpoint:
        if banner_tag(head.text(cfg['state_file']) or '') != vN:
            f.fail('K', 'pr', f'STATE.md banner must name {vN}')
        if f'{cfg["reports_dir"]}/SESSION_REPORT_{vN}.md' not in added:
            f.fail('K', 'pr', f'{cfg["reports_dir"]}/SESSION_REPORT_{vN}.md must be added')
        if declared == 2 and f'{cfg["reports_dir"]}/VERIFICATION_{vN}.md' not in added:
            f.fail('K', 'pr', f'a Tier 2 checkpoint needs {cfg["reports_dir"]}/VERIFICATION_{vN}.md')
    return info


def path_requirement(cfg, base, head, status, path, checkpoint, vN, own):
    """Required tier of one change (note s3.3), after the prohibitions."""
    if match_any(cfg['paths']['gate'] + cfg['paths']['templates'], path):
        return 2, 'gate'
    if match_any(cfg['paths'].get('core', []), path):
        return 2, 'core'
    if (under(path, f'{cfg["directives_dir"]}/{vN}') or path in own or path == cfg['state_file']
            or under(path, cfg['history_dir'])):
        return 'checkpoint-only', 'records'
    if under(path, cfg['decisions_dir']):
        st = decision_status(head.text(path) or '') if head.has(path) else None
        return (2 if st in IMMUTABLE_STATUSES else 1), 'decision'
    if path == cfg['pins_file']:
        return 0, 'pins'   # by line kind, computed separately
    if under(path, cfg['design_dir']):
        return 1, 'design'
    return 0, 'other'


def verification_rules(repo, cfg, head_commit, vN, f):
    """Note s5.2, final-content binding. Runs only against a commit (the PR head)."""
    path = f'{cfg["reports_dir"]}/VERIFICATION_{vN}.md'
    text = git_text(repo, head_commit, path)
    if text is None:
        f.fail('V', 'pr', f'{path} is missing')
        return
    vals = {}
    for k, rx in VERIFY_FIELDS.items():
        found = [m.group(1) for m in (rx.match(l) for l in text.split('\n')) if m]
        if len(found) != 1:
            f.fail('V', 'pr', f'{path}: needs exactly one {k} line')
        else:
            vals[k] = found[0].strip()
    if vals.get('result') not in (None, 'PASS'):
        f.fail('V', 'pr', f'{path}: Result must be PASS')
    if len(text.encode('utf-8')) > cfg['verification_max_bytes']:
        f.fail('V', 'pr', f'{path} is over {cfg["verification_max_bytes"]:,} B')
    v = vals.get('commit')
    if not v:
        return
    if git(repo, 'cat-file', '-e', f'{v}^{{commit}}', check=False).returncode != 0:
        f.fail('V', 'pr', f'verified commit {v} does not exist')
        return
    headid = git_out(repo, 'rev-parse', f'{head_commit}^{{commit}}')
    if v == headid or git(repo, 'merge-base', '--is-ancestor', v, headid, check=False).returncode != 0:
        f.fail('V', 'pr', f'verified commit {v[:12]} is not a strict ancestor of the PR head')
        return
    if git_blob(repo, v, path) is not None:
        f.fail('V', 'pr', f'{path} already exists at the verified commit')
    diff = [p for s, p in git_diff(repo, v, headid)]
    if diff != [path]:
        f.fail('V', 'pr', f'between the verified commit and the head, only {path} may change; changed: {diff[:6]}')


def pr_check(repo, base_ref, head_ref, run_snapshot=True):
    """The authoritative PR check (note s3.4). Returns (Findings, info)."""
    f = Findings()
    require_full_history(repo)
    base_id = git_out(repo, 'rev-parse', f'{base_ref}^{{commit}}')
    head_id = git_out(repo, 'rev-parse', f'{head_ref}^{{commit}}')
    B = git_out(repo, 'merge-base', base_id, head_id)
    f.info.append(f'base {base_id} head {head_id} merge-base {B}')
    pol = git_blob(repo, B, POLICY_PATH)
    if pol is None:
        raise GateError(f'{POLICY_PATH} is missing on the base {B}')
    cfg = load_policy_bytes(pol, f'{B}:{POLICY_PATH}')
    hpol = git_blob(repo, head_id, POLICY_PATH)
    if hpol is None:
        raise GateError(f'{POLICY_PATH} is missing on the head')
    head_cfg = load_policy_bytes(hpol, f'{head_id}:{POLICY_PATH}')
    base, head = Tree(repo, B, cfg), Tree(repo, head_id, cfg)
    changes = git_diff(repo, B, head_id)
    byte_preservation(cfg, base, head, f)
    info = tier_rules(cfg, base, head, f, changes)
    if info.get('checkpoint') and info.get('declared') == 2:
        verification_rules(repo, cfg, head_id, f'v{info["N"]}', f)
    if run_snapshot:
        tmp = tempfile.mkdtemp(prefix='gate-head-')
        try:
            specials = materialize(repo, head_id, tmp)
            snap = Snapshot(tmp, cfg, head_cfg, specials=specials)
            f.extend(snap.run())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    info['cfg'] = cfg
    return f, info


# ---------------------------------------------------------------- local (pre-commit) mode

def staged_check(repo, ci=False):
    """Note s4 local rules on the staged snapshot. Returns (Findings, local=True)."""
    f = Findings()
    if not ci:
        prob = hooks_problem(repo)
        if prob:
            f.fail('LOCAL', 'hooks', prob)
    has_head = git(repo, 'rev-parse', '--verify', '-q', 'HEAD', check=False).returncode == 0
    tmp = tempfile.mkdtemp(prefix='gate-staged-')
    try:
        # The staged snapshot is the index written as a tree, materialized from exact blob bytes
        # (no checkout filters), the same way as the PR check (review finding R1).
        tree = git_out(repo, 'write-tree')
        specials = materialize(repo, tree, tmp)
        cfg = load_policy(tmp)
        if not has_head:
            f.extend(bootstrap_snapshot(tmp, cfg, specials))
            return f
        require_full_history(repo)
        branch = git(repo, 'symbolic-ref', '-q', '--short', 'HEAD', check=False).stdout.decode().strip()
        trunk = cfg.get('trunk', 'master')
        if branch == trunk:
            f.fail('LOCAL', 'master', f'commits on {trunk} (the trunk) are not allowed: it changes only by a merged PR')
        snap = Snapshot(tmp, cfg, specials=specials)
        sf = snap.run()
        base_ref = None
        if git(repo, 'rev-parse', '--verify', '-q', trunk, check=False).returncode == 0:
            base_ref = git_out(repo, 'merge-base', 'HEAD', trunk)
        base_pins = {}
        if base_ref:
            base = Tree(repo, base_ref, cfg)
            head = Tree(repo, tree, cfg)
            byte_preservation(cfg, base, head, f)
            base_pins = parse_manifest_text(base.text(cfg['pins_file']) or '')[0]
            base_paths = base.paths()
            adv = Findings()
            try:
                tier_rules(cfg, base, head, adv, git_diff(repo, base_ref, tree))
            except GateError as e:
                adv.fail('PR', 'advice', f'advice unavailable: {e}')
            for code, sub, msg, path in adv.items:
                f.fail('PR', 'advice', f'[{code}] {msg}')
        else:
            base_paths = set()
        # C7 and C9 matches: blocking only for bytes that exist on the local base
        for code, sub, msg, path in sf.items:
            if (code, sub) == ('C7', 'match-base') and path not in base_pins:
                sub = 'match-new'
            if (code, sub) == ('C9', 'evidence-base') and path not in base_paths:
                sub = 'evidence-new'
            f.fail(code, sub, msg, path)
        f.warns += sf.warns
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return f


# ---------------------------------------------------------------- bootstrap, audit, tags

def bootstrap_snapshot(root, cfg, specials=()):
    f = Snapshot(root, cfg, specials=specials).run()
    led_nums = []
    for r in parse_ledger_register(os.path.join(root, *cfg['ledger_register'].split('/')))[0]:
        fp = os.path.join(root, *cfg['history_dir'].split('/'), r['file'])
        if os.path.isfile(fp):
            led_nums += [e['n'] for e in parse_ledger(read_text(fp))['entries']]
    if led_nums != [0]:
        f.fail('B', 'bootstrap', f'the root commit holds exactly ledger entry v0 (found {led_nums})')
    ddir = os.path.join(root, cfg['directives_dir'])
    dirs = sorted(d for d in os.listdir(ddir) if d != '_TEMPLATE') if os.path.isdir(ddir) else []
    if dirs != ['v0']:
        f.fail('B', 'bootstrap', f'the root commit holds exactly directive v0 (found {dirs})')
    return f


def audit(repo, commit, prs, reviews, repo_name, prs_head_ref=None):
    """Note s7.4, from recorded API responses (prs: list for this commit; reviews: list for the PR)."""
    f = Findings()
    C = git_out(repo, 'rev-parse', f'{commit}^{{commit}}')
    require_full_history(repo)
    parents = commit_parents(repo, C)
    if not parents:
        tmp = tempfile.mkdtemp(prefix='gate-root-')
        try:
            specials = materialize(repo, C, tmp)
            f.extend(bootstrap_snapshot(tmp, load_policy(tmp), specials))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return f
    if len(parents) != 1:
        f.fail('A', 'audit', f'{C[:12]} has {len(parents)} parents: only squash merges may reach the trunk')
        return f
    pcfg = load_policy_bytes(git_blob(repo, parents[0], POLICY_PATH) or b'{}', f'{parents[0]}:{POLICY_PATH}')
    trunk = pcfg.get('trunk', 'master')
    cands = [p for p in prs if (p.get('base') or {}).get('repo', {}).get('full_name') == repo_name
             and (p.get('base') or {}).get('ref') == trunk and p.get('merged_at')
             and p.get('merge_commit_sha') == C]
    if len(cands) != 1:
        f.fail('A', 'audit', f'{len(cands)} merged PRs into {trunk} of {repo_name} record {C[:12]} as their merge '
                             'commit; exactly one is required')
        return f
    pr = cands[0]
    H = pr['head']['sha']
    ref = prs_head_ref or f'refs/pull/{pr["number"]}/head'
    fetched = git(repo, 'rev-parse', '--verify', '-q', f'{ref}^{{commit}}', check=False).stdout.decode().strip()
    if fetched != H:
        f.fail('A', 'audit', f'{ref} is {fetched[:12] or "missing"}, but the PR records head {H[:12]}')
        return f
    if git_out(repo, 'rev-parse', f'{C}^{{tree}}') != git_out(repo, 'rev-parse', f'{H}^{{tree}}'):
        f.fail('A', 'audit', f'the content of {C[:12]} differs from the approved PR head {H[:12]}')
    pf, info = pr_check(repo, parents[0], H)
    f.extend(pf)
    rp = info['cfg']['review_policy']
    need = rp['owner_approval_checkpoint'] if info.get('checkpoint') else rp['owner_approval_routine']
    if need:
        owner = info['cfg']['owner'].lstrip('@')
        mine = [r for r in reviews if (r.get('user') or {}).get('login') == owner
                and r.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')]
        mine.sort(key=lambda r: (r.get('submitted_at') or '', r.get('id') or 0))
        if not mine or mine[-1]['state'] != 'APPROVED':
            f.fail('A', 'audit', f'the Owner\'s latest review is {mine[-1]["state"] if mine else "missing"}, '
                                 'not a current approval')
        else:
            rc = mine[-1].get('commit_id') or ''
            if git(repo, 'cat-file', '-e', f'{rc}^{{commit}}', check=False).returncode != 0:
                f.fail('A', 'audit', f'the approved commit {rc[:12] or "?"} is not available')
            elif git_out(repo, 'rev-parse', f'{rc}^{{tree}}') != git_out(repo, 'rev-parse', f'{H}^{{tree}}'):
                f.fail('A', 'audit', 'the Owner approved content that differs from the PR head')
    return f


def load_json_pages(path):
    """A list of objects from GitHub CLI output (review finding R5): one JSON array; a --slurp array
    of page arrays; or several page arrays concatenated, as --paginate prints them without --slurp."""
    text = read_text(path)
    dec, pos, values = json.JSONDecoder(), 0, []
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        try:
            v, pos = dec.raw_decode(text, pos)
        except ValueError as e:
            raise GateError(f'{path}: not valid JSON: {e}')
        values.append(v)
    if not values:
        raise GateError(f'{path} is empty')
    out = []
    for v in values:
        if not isinstance(v, list):
            raise GateError(f'{path}: expected a JSON array, or pages of arrays')
        if v and all(isinstance(x, list) for x in v):
            for page in v:
                out.extend(page)
        else:
            out.extend(v)
    if any(not isinstance(x, dict) for x in out):
        raise GateError(f'{path}: every item must be a JSON object')
    return out


def tag_check(repo, tag, commit):
    f = Findings()
    m = re.match(r'^v(\d+)$', tag)
    if not m:
        f.fail('TAG', 'tag', f'{tag!r} is not a checkpoint tag vN')
        return f
    N = int(m.group(1))
    require_full_history(repo)
    T = git_out(repo, 'rev-parse', f'{commit}^{{commit}}')
    cfg = load_policy_bytes(git_blob(repo, T, POLICY_PATH) or b'{}', f'{T}:{POLICY_PATH}')
    trunk = cfg.get('trunk', 'master')
    chain = git_out(repo, 'rev-list', '--first-parent', trunk).split()
    if T not in chain:
        f.fail('TAG', 'tag', f'{tag} target {T[:12]} is not on the first-parent history of {trunk}')
        return f
    here = Tree(repo, T, cfg).ledger_numbers()
    parents = commit_parents(repo, T)
    if N == 0:
        if parents or here != [0]:
            f.fail('TAG', 'tag', 'v0 is valid only on the root commit, whose ledger holds exactly entry v0')
        return f
    before = Tree(repo, parents[0], cfg).ledger_numbers() if parents else []
    if not (before and before[-1] == N - 1 and here and here[-1] == N):
        f.fail('TAG', 'tag', f'{tag} must be on the commit that introduces ledger entry {tag}')
    return f


# ---------------------------------------------------------------- output and CLI

def report(f, local, quiet, title, sizes=()):
    if not quiet:
        print(f'check_docs: {title}')
        for line in f.info:
            print(f'INFO  {line}')
        for relp, n, cap in sizes:
            print(f'SIZE  {relp:<40} {n:>9,} B  ~{n / 4000:5.1f}k tokens  {n * 100 / cap:5.1f}% of {cap:,} B')
    block = f.blocking(local)
    for code, sub, msg, _ in block:
        print(f'FAIL  {code:<5} {CHECK_NAMES.get(code, "")}: {msg}')
    for code, sub, msg, _ in f.advisory(local):
        print(f'ADVICE {code:<5} {msg}')
    for code, msg in f.warns:
        print(f'WARN  {code:<5} {msg}')
    ok = not block
    print(f'RESULT {"PASS" if ok else "FAIL"} ({len(block)} failure(s), {len(f.advisory(local))} advice, '
          f'{len(f.warns)} warning(s))')
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description='Documentation and governance gate.')
    ap.add_argument('--repo', default=os.getcwd())
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--staged', action='store_true')
    mode.add_argument('--bootstrap', action='store_true')
    mode.add_argument('--pr', action='store_true')
    mode.add_argument('--audit', action='store_true')
    mode.add_argument('--tag-check', action='store_true')
    mode.add_argument('--codeowners', action='store_true')
    ap.add_argument('--base')
    ap.add_argument('--head')
    ap.add_argument('--commit')
    ap.add_argument('--tag')
    ap.add_argument('--prs')
    ap.add_argument('--reviews')
    ap.add_argument('--repo-name')
    ap.add_argument('--ci', action='store_true')
    ap.add_argument('--expect-tag')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    try:
        if a.codeowners:
            sys.stdout.write(codeowners_text(load_policy(repo)))
            return 0
        if a.staged:
            return report(staged_check(repo, ci=a.ci), True, a.quiet, f'staged snapshot of {repo}')
        if a.pr:
            if not (a.base and a.head):
                ap.error('--pr needs --base and --head')
            f, info = pr_check(repo, a.base, a.head)
            f.info.append(f'checkpoint={info.get("checkpoint")} N={info.get("N")} '
                          f'required tier={info.get("required")} declared tier={info.get("declared")}')
            return report(f, False, a.quiet, f'pull request {a.base}..{a.head}')
        if a.audit:
            if not (a.commit and a.prs and a.reviews and a.repo_name):
                ap.error('--audit needs --commit, --prs, --reviews and --repo-name')
            prs = load_json_pages(a.prs)
            reviews = load_json_pages(a.reviews)
            return report(audit(repo, a.commit, prs, reviews, a.repo_name), False, a.quiet, f'audit of {a.commit}')
        if a.tag_check:
            if not (a.tag and a.commit):
                ap.error('--tag-check needs --tag and --commit')
            return report(tag_check(repo, a.tag, a.commit), False, a.quiet, f'tag {a.tag}')
        cfg = load_policy(repo)
        if a.bootstrap:
            f = bootstrap_snapshot(repo, cfg)
            if is_work_tree(repo) and git(repo, 'rev-parse', '--verify', '-q', 'HEAD', check=False).returncode == 0:
                f.fail('B', 'bootstrap', 'bootstrap mode is only for a repository with no commit yet')
            return report(f, False, a.quiet, f'bootstrap of {repo}')
        snap = Snapshot(repo, cfg, expect_tag=a.expect_tag)
        f = snap.run()
        if not a.ci and is_work_tree(repo):
            prob = hooks_problem(repo)
            if prob:
                f.fail('LOCAL', 'hooks', prob)
        return report(f, False, a.quiet, f'snapshot of {repo}', snap.sizes)
    except GateError as e:
        print(f'ERROR {e}')
        print('RESULT FAIL (environment or configuration error; the gate fails closed)')
        return 2


if __name__ == '__main__':
    sys.exit(main())
