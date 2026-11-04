#!/usr/bin/env python3
"""Read-only information-boundary checks. No network, secret resolution or writes.

This is a deterministic guardrail, not a personal/customer-data classifier.
Review classification and rights before any public transfer, even after a pass.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

MAX_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_DEPTH = 3
RESOURCE = re.compile(r"\b(?:tenant|project|cluster|subnet|vpc|bucket|filesystem)-[a-z0-9]{14,}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
CREDENTIAL = re.compile(
    r'''(?im)^\s*["']?(?:password|api_key|api_token|access_token|refresh_token|client_secret|private_key|secret_access_key|tls\.key)["']?\s*[:=]\s*(.+?)\s*,?\s*$'''
)
PRIVATE_FIELD = re.compile(
    r'''(?im)^\s*["']?(?:tenant_id|project_id|cluster_id|subnet_id|filesystem_id)["']?\s*[:=]\s*["']?([^\n,"'}]+)'''
)
PRIVATE_URL = re.compile(
    r"https?://(?:[^\s/@]+:[^\s/@]+@|(?:10\.[0-9.]+|192\.168\.[0-9.]+|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9.]+)(?=[:/\s]|$)|[^/\s]+\.(?:internal|local)(?=[:/\s]|$))",
    re.I,
)
ARCHIVES = ('.zip', '.whl', '.tar', '.tar.gz', '.tgz')
RUNTIME_DIRS = {'.terraform', '.nebius-cxcli', 'wireguard-clients', 'runtime-data', 'database-dumps', 'backups'}
EXECUTABLE = {'.py', '.sh', '.bash', '.js', '.ts', '.tf', '.tfvars', '.whl'}
BUNDLE_FILES = {base + suffix for base in ('application', 'integrations', 'policies', 'secret-references', 'target-binding') for suffix in ('.json', '.yaml', '.yml')}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    code: str


def placeholder(value: str) -> bool:
    value = value.strip().rstrip(',').strip('"\'').strip()
    return (value in {'', 'null', 'None', '{}', '[]'} or
            value.startswith(('<', '${', '{{')) or
            value.upper().startswith(('REPLACE_', 'EXAMPLE_', 'SYNTHETIC_')))


def safe_label(label: str, private_values: tuple[str, ...]) -> str:
    for value in sorted(private_values, key=len, reverse=True):
        label = label.replace(value, '<private-value>')
    label = RESOURCE.sub('<resource-id>', label)
    label = EMAIL.sub('<contact>', label)
    return ''.join(c if c.isprintable() else '?' for c in label)


def forbidden_path(name: str) -> bool:
    p = PurePosixPath(name)
    n = p.name.lower()
    return (bool(set(p.parts) & RUNTIME_DIRS) or n == '.env' or
            (n.startswith('.env.') and not n.endswith(('.example', '.sample', '.template'))) or
            n in {'kubeconfig', 'credentials', 'id_rsa', 'id_ed25519', 'ssh_known_hosts', 'plan.out', '.terraform.tfstate.lock.info'} or
            n.endswith(('.tfstate', '.tfplan', '.dump', '.sql.gz', '.p12', '.pfx', '.key', '.log')) or
            '.tfstate.' in n or n.startswith('kubeconfig.') or
            n.endswith(('.auto.tfvars', '.auto.tfvars.json')))


def json_credentials(value: object) -> bool:
    if isinstance(value, list):
        return any(json_credentials(v) for v in value)
    if not isinstance(value, dict):
        return False
    if value.get('kind') == 'Secret' and any(value.get(k) for k in ('data', 'stringData')):
        return True
    keys = {'password', 'api_key', 'api_token', 'access_token', 'refresh_token',
            'client_secret', 'private_key', 'secret_access_key', 'tls.key'}
    for key, item in value.items():
        if key.lower() in keys and isinstance(item, str) and not placeholder(item):
            return True
        if json_credentials(item):
            return True
    return False


def fields(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from fields(item)
    elif isinstance(value, list):
        for item in value:
            yield from fields(item)


class Scanner:
    def __init__(self, destination: str, private_values: tuple[str, ...] = ()):
        self.destination = destination
        self.private_values = private_values
        self.findings: set[Finding] = set()
        self.files = 0
        self.expanded = 0
        self.members = 0
        self.prefix = ''

    def add(self, name: str, code: str, line: int = 0) -> None:
        self.findings.add(Finding(safe_label(self.prefix + name, self.private_values), line, code))

    def content(self, name: str, data: bytes, depth: int = 0) -> None:
        self.files += 1
        if forbidden_path(name):
            self.add(name, 'FORBIDDEN_RUNTIME_FILE')
        if self.destination == 'application-bundle':
            p = PurePosixPath(name)
            if p.name not in BUNDLE_FILES or len(p.parts) != 1:
                self.add(name, 'BUNDLE_PATH_NOT_ALLOWED')
            if p.suffix in EXECUTABLE:
                self.add(name, 'BUNDLE_EXECUTABLE')
        if len(data) > MAX_BYTES:
            self.add(name, 'UNINSPECTED_SIZE_LIMIT')
            return
        if self.destination == 'public':
            for value in self.private_values:
                if value.encode() in data:
                    self.add(name, 'PRIVATE_VALUE')
        if name.lower().endswith(ARCHIVES):
            self.archive(name, data, depth)
            return
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            self.add(name, 'UNINSPECTED_BINARY')
            return
        if '\x00' in text:
            self.add(name, 'UNINSPECTED_BINARY')
            return
        for match in PRIVATE_KEY.finditer(text):
            self.add(name, 'PRIVATE_KEY', text.count('\n', 0, match.start()) + 1)
        for match in CREDENTIAL.finditer(text):
            config = PurePosixPath(name).suffix in {'.json', '.yaml', '.yml', '.tf', '.tfvars', '.toml', '.ini', '.conf', '.env'}
            literal = match[1].lstrip().startswith(('"', "'"))
            reference = match[1].strip().startswith(('var.', 'local.', 'module.', 'data.'))
            if (config or literal) and not reference and not placeholder(match[1]):
                self.add(name, 'CREDENTIAL_VALUE', text.count('\n', 0, match.start()) + 1)
        try:
            obj = json.loads(text)
        except (ValueError, RecursionError):
            obj = None
        if json_credentials(obj):
            self.add(name, 'CREDENTIAL_VALUE')
        private_fields = [(m[1], m.start()) for m in PRIVATE_FIELD.finditer(text)]
        private_fields.extend((v, 0) for k, v in fields(obj)
                              if k in {'tenant_id', 'project_id', 'cluster_id', 'subnet_id', 'filesystem_id'}
                              and isinstance(v, str))
        has_private_fields = any(not placeholder(value) for value, _ in private_fields)
        if self.destination == 'private':
            parts = PurePosixPath(name).parts
            environment = parts[1] if len(parts) > 2 and parts[0] == 'clusters' and parts[1] in {'dev', 'prod'} else None
            if has_private_fields and not environment:
                self.add(name, 'PRIVATE_ENVIRONMENT_PATH')
            declared = [v for k, v in fields(obj) if k == 'environment' and v in ('dev', 'prod')]
            declared.extend(re.findall(
                r'''(?m)(?:^|[\s{,\-])["']?environment["']?\s*:\s*["']?(dev|prod)["']?(?=\s|[,}#]|$)''', text))
            if environment and any(v != environment for v in declared):
                self.add(name, 'ENVIRONMENT_PATH_MISMATCH')
        # No YAML parser dependency: conservatively require review for every
        # recognizable Secret declaration, including quoted/inline/list forms.
        if re.search(r'''(?m)(?:^|[\s{,\-])["']?kind["']?\s*:\s*["']?Secret["']?(?=\s|[,}#]|$)''', text):
            self.add(name, 'SECRET_PAYLOAD_REVIEW_REQUIRED')
        if self.destination == 'public':
            for value, offset in private_fields:
                if not placeholder(value):
                    self.add(name, 'PRIVATE_CONFIGURATION', text.count('\n', 0, offset) + 1)
            if PRIVATE_URL.search(text):
                self.add(name, 'PRIVATE_OR_CREDENTIALED_URL')

    def archive(self, name: str, data: bytes, depth: int) -> None:
        if depth >= MAX_DEPTH:
            self.add(name, 'UNINSPECTED_ARCHIVE_DEPTH')
            return
        try:
            if name.lower().endswith(('.zip', '.whl')):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for member in archive.infolist():
                        if not self.member_allowed(name, member.filename, member.file_size,
                                                   stat.S_ISLNK(member.external_attr >> 16)):
                            return
                        if member.is_dir():
                            continue
                        self.member_content(name, member.filename, archive.read(member), depth)
            else:
                if name.lower().endswith(('.gz', '.tgz')):
                    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                        data = compressed.read(MAX_ARCHIVE_BYTES + 1)
                    if len(data) > MAX_ARCHIVE_BYTES:
                        self.add(name, 'UNINSPECTED_ARCHIVE_LIMIT')
                        return
                # Never let tarfile implicitly decompress an unbounded stream.
                with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
                    for member in archive:
                        if not self.member_allowed(name, member.name, member.size,
                                                   not (member.isfile() or member.isdir())):
                            return
                        if member.isdir():
                            continue
                        stream = archive.extractfile(member)
                        if stream is None:
                            self.add(name, 'UNINSPECTED_ARCHIVE')
                        else:
                            self.member_content(name, member.name, stream.read(MAX_BYTES + 1), depth)
        except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile, tarfile.TarError):
            self.add(name, 'UNINSPECTED_ARCHIVE')

    def member_content(self, archive: str, member: str, data: bytes, depth: int) -> None:
        prefix = self.prefix
        self.prefix += archive + '!'
        try:
            self.content(member, data, depth + 1)
        finally:
            self.prefix = prefix

    def member_allowed(self, archive: str, member: str, size: int, link: bool) -> bool:
        self.members += 1
        self.expanded += size
        p = PurePosixPath(member)
        if link or p.is_absolute() or '..' in p.parts or '\\' in member or '.git' in p.parts:
            self.add(archive, 'UNSAFE_ARCHIVE_MEMBER')
            return False
        if self.members > MAX_MEMBERS or self.expanded > MAX_ARCHIVE_BYTES or size > MAX_BYTES:
            self.add(archive, 'UNINSPECTED_ARCHIVE_LIMIT')
            return False
        return True

    def file(self, root: Path, relative: str) -> None:
        path = root / relative
        try:
            if path.is_symlink() or any((root / Path(*Path(relative).parts[:i])).is_symlink()
                                        for i in range(1, len(Path(relative).parts))):
                self.add(relative, 'SYMLINK_NOT_ALLOWED')
            elif not path.is_file():
                self.add(relative, 'UNINSPECTED_FILE_TYPE')
            else:
                with path.open('rb') as stream:
                    self.content(relative, stream.read(MAX_BYTES + 1))
        except OSError:
            self.add(relative, 'UNREADABLE_FILE')


def git(root: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0',
               GIT_CONFIG_NOSYSTEM='1', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
    return subprocess.run(['git', '--no-lazy-fetch', '--no-replace-objects', '-c',
                           'core.fsmonitor=false', '-C', str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env).stdout


def scan_tree(scanner: Scanner, root: Path, tracked: bool) -> None:
    if tracked:
        names = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.').decode().split('\0')
        for name in sorted(set(filter(None, names))):
            scanner.file(root, name)
        return
    def unreadable(_error):
        scanner.add('.', 'UNINSPECTED_DIRECTORY')

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        for entry in list(dirs):
            p = Path(directory) / entry
            if entry == '.git':
                dirs.remove(entry)
                if scanner.destination == 'application-bundle':
                    scanner.add(str(p.relative_to(root)), 'BUNDLE_GIT_METADATA')
            elif p.is_symlink():
                dirs.remove(entry)
                scanner.add(str(p.relative_to(root)), 'SYMLINK_NOT_ALLOWED')
        for name in files:
            scanner.file(root, str((Path(directory) / name).relative_to(root)))


def scan_history(scanner: Scanner, root: Path, revision_range: str) -> None:
    # Scan complete trees of candidate commits, including deleted historical content.
    # Callers supply a local Git range; no remote access or fetching occurs here.
    if revision_range.startswith('-'):
        raise ValueError('invalid revision')
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('incomplete history')
    commits = git(root, 'rev-list', revision_range, '--').decode().splitlines()
    if not commits:
        raise ValueError('empty range')
    seen = set()
    for commit in commits:
        for record in git(root, 'ls-tree', '-rz', commit, '--', '.').split(b'\0'):
            if not record:
                continue
            meta, raw_path = record.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            path = raw_path.decode()
            key = (mode, oid, path)
            if key in seen:
                continue
            seen.add(key)
            scanner.prefix = commit[:12] + ':'
            if mode not in {'100644', '100755'} or kind != 'blob':
                scanner.add(path, 'GIT_LINK_OR_SPECIAL_FILE')
            elif int(git(root, 'cat-file', '-s', oid)) > MAX_BYTES:
                scanner.add(path, 'UNINSPECTED_SIZE_LIMIT')
            else:
                scanner.content(path, git(root, 'cat-file', 'blob', oid))
    scanner.prefix = ''


def binding_errors(desired: dict, observed: dict) -> list[str]:
    required = {'environment', 'project_id', 'cluster_id', 'state_namespace', 'object_namespace', 'backup_namespace', 'secret_namespace'}
    if not isinstance(desired, dict) or not isinstance(observed, dict):
        return ['BINDING_SHAPE']
    if set(desired) != required or set(observed) != required:
        return ['BINDING_SHAPE']
    if any(not isinstance(d[k], str) for d in (desired, observed) for k in required):
        return ['BINDING_SHAPE']
    if desired['environment'] not in {'dev', 'prod'} or observed['environment'] not in {'dev', 'prod'}:
        return ['BINDING_ENVIRONMENT']
    if any(placeholder(d[k]) for d in (desired, observed) for k in required):
        return ['BINDING_UNCONFIGURED']
    return ['BINDING_MISMATCH'] if desired != observed else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    scan = sub.add_parser('scan', help='Inspect files without printing matched values.')
    scan.add_argument('--root', type=Path, required=True)
    scan.add_argument('--destination', choices=('public', 'private', 'application-bundle'), required=True)
    scan.add_argument('--git-files', action='store_true', help='Tracked plus nonignored untracked files; ignored tracked files are still checked.')
    scan.add_argument('--history', help='Also inspect every candidate commit in a local Git revision range.')
    scan.add_argument('--private-values', type=Path, help='Private/local-only JSON array of exact values, stored outside the scan root; never supply in public CI.')
    target = sub.add_parser('verify-target', help='Compare supplied desired and independently observed JSON bindings; does not contact a cluster.')
    target.add_argument('--desired', type=Path, required=True)
    target.add_argument('--observed', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify-target':
            codes = binding_errors(json.loads(args.desired.read_text()), json.loads(args.observed.read_text()))
            print(json.dumps({'codes': codes, 'live_verification': False}))
            return bool(codes)
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError('invalid root')
        values = ()
        if args.private_values:
            source = args.private_values.resolve(strict=True)
            if source.is_relative_to(root) or args.destination != 'public':
                raise ValueError('private comparison scope')
            loaded = json.loads(source.read_text())
            if not isinstance(loaded, list) or not loaded or any(not isinstance(v, str) or len(v) < 4 for v in loaded):
                raise ValueError('invalid private values')
            values = tuple(loaded)
        scanner = Scanner(args.destination, values)
        scan_tree(scanner, root, args.git_files)
        if args.history:
            scan_history(scanner, root, args.history)
        if not scanner.files:
            scanner.add('.', 'EMPTY_SCAN')
        print(json.dumps({'files_checked': scanner.files, 'findings': [asdict(f) for f in sorted(scanner.findings)],
                          'classification_review_required': True}, indent=2))
        return bool(scanner.findings)
    except (OSError, ValueError, RecursionError, subprocess.CalledProcessError):
        # Exception messages may contain paths, content, or credentials.
        print(json.dumps({'codes': ['CHECK_INPUT_ERROR']}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
