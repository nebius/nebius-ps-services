"""Validated Git configuration for exactly owned disposable verification scopes.

This is an integrity boundary, not isolation from hostile same-user processes.
Profile creation and sealing belong to the fixture owner; consumers only validate.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

PROFILE_ENV = "SKILLS_GIT_PROFILE"
DIRECTORY = ".sdlc-git-environment"
SCHEMA = "skills/owned-git-environment-v1"
ORIGIN_RECEIPT = ".owned-git-origin.json"
# Match the three-tier lifecycle owner's state bound, not the small profile bound.
LIFECYCLE_STATE_LIMIT = 1024 * 1024
FETCH = "+refs/heads/*:refs/remotes/origin/*"
PUSH_GUARD = b"#!/bin/sh\n# This disposable baseline is read-only.\nexit 1\n"
FORBIDDEN = {
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_CONFIG", "GIT_TEMPLATE_DIR", "GIT_SHALLOW_FILE", "GIT_REPLACE_REF_BASE",
    "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND",
    "GIT_ALLOW_PROTOCOL", "GIT_PROTOCOL", "GIT_PROTOCOL_FROM_USER",
    "GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_ATTR_SOURCE",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_GRAFT_FILE",
    "GIT_NO_REPLACE_OBJECTS", "GIT_PREFIX", "GIT_QUARANTINE_PATH", "GIT_DEFAULT_HASH",
}


class GitEnvironmentError(ValueError):
    """An owned profile cannot safely supply this Git operation."""


def _fail(message: str) -> None:
    raise GitEnvironmentError("UNSAFE_GIT_ENVIRONMENT: " + message)


def _path(path: Path, *, private: bool = False) -> None:
    if not path.is_absolute() or path != path.resolve(strict=False) or any(
        character in str(path) for character in "\n\r\0"
    ):
        _fail("paths must be canonical and absolute")
    for part in (path, *path.parents):
        if part.is_symlink():
            _fail("symlinked profile or repository path")
        if part.exists():
            value = part.stat()
            sticky = value.st_uid == 0 and value.st_mode & stat.S_ISVTX
            if value.st_uid not in {0, os.getuid()} or value.st_mode & 0o022 and not sticky:
                _fail("unsafe path ownership or permissions")
    if private and (not path.is_dir() or path.stat().st_uid != os.getuid()
                    or path.stat().st_mode & 0o077):
        _fail("profile scope must be a private owned directory")


def _read(path: Path, *, private: bool = True, limit: int = 65536) -> bytes:
    _path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1 or before.st_mode & (0o077 if private else 0o022)):
                _fail("unsafe profile or repository file")
            if before.st_size > limit:
                _fail(f"file exceeds {limit}-byte limit")
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
        bound = path.lstat()
        def identity(item):
            return (item.st_dev, item.st_ino, item.st_mode, item.st_uid,
                    item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if len(data) > limit or identity(before) != identity(after) or identity(after) != identity(bound):
            _fail("file changed while validating")
        return data
    except (OSError, UnicodeError) as error:
        raise GitEnvironmentError("UNSAFE_GIT_ENVIRONMENT: required profile file unavailable") from error


def _json(path: Path, *, private: bool = True, limit: int = 65536) -> dict:
    data = _read(path, private=private, limit=limit)
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise GitEnvironmentError("UNSAFE_GIT_ENVIRONMENT: invalid profile JSON") from error
    if not isinstance(value, dict):
        _fail("profile JSON must be an object")
    return value


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _config(directory: Path) -> bytes:
    attributes = json.dumps(str(directory / "attributes"), ensure_ascii=False)
    return ("[user]\n name = SDLC Verification\n email = sdlc-verification@example.invalid\n"
            "[core]\n fsmonitor = false\n attributesFile = " + attributes + "\n"
            "[protocol]\n allow = never\n[protocol \"file\"]\n allow = always\n"
            "[commit]\n gpgSign = false\n[tag]\n gpgSign = false\n").encode()


def _marker(value: dict, *, bootstrap: bool) -> None:
    root = Path(value["owner_root"])
    project = Path(value["project_root"])
    if value["kind"] == "lightweight":
        if (value["owner_id"] != "lightweight" or project != root / "disposable-project"
                or _json(root / ".sdlc-workflow-test-root.json") != {"schema": "agentic-sdlc/verification-root-v1"}):
            _fail("lightweight fixture identity changed")
        marker = project / ".sdlc-workflow-test-fixture.json"
        if marker.exists() and _json(marker, private=False) != {"schema": "agentic-sdlc/disposable-fixture-v2"}:
            _fail("lightweight project marker changed")
        if not bootstrap and not marker.is_file():
            _fail("lightweight project marker missing")
    elif value["kind"] == "three-tier":
        if (re.fullmatch(r"[0-9a-f]{32}", value["owner_id"]) is None
                or project != root / "project" or root.name != value["owner_id"]
                or root.parent.name != "runs"):
            _fail("three-tier fixture identity changed")
        expected = {"schema": "agentic-sdlc/three-tier-lifecycle-v8", "verification_id": value["owner_id"]}
        if _json(root / ".agentic-sdlc-three-tier-run.json") != expected:
            _fail("three-tier generation marker changed")
        if _json(project / ".sdlc-workflow-test-project.json", private=False) != {
            **expected, "scenario": "three-tier-task-board-v1"
        }:
            _fail("three-tier project marker changed")
        if not bootstrap:
            active = _json(root.parent.parent / "active.json", limit=LIFECYCLE_STATE_LIMIT)
            if (active.get("verification_id") != value["owner_id"]
                    or active.get("run_root") != str(root)
                    or active.get("project_root") != str(project)
                    or active.get("status") == "DESTROYED"):
                _fail("three-tier generation is no longer active")
    else:
        _fail("unsupported profile owner")


def _profile(path: Path, *, bootstrap: bool = False) -> dict:
    _path(path.parent, private=True)
    value = _json(path)
    fields = {"schema", "kind", "owner_root", "owner_id", "project_root", "origin_receipt_sha256"}
    if (set(value) != fields or value.get("schema") != SCHEMA
            or any(not isinstance(value.get(key), str) for key in fields - {"origin_receipt_sha256"})):
        _fail("invalid profile schema")
    root = Path(value["owner_root"])
    _path(root, private=True)
    _path(Path(value["project_root"]))
    if path != root / DIRECTORY / "context.json":
        _fail("profile path is not bound to its owner")
    if _read(path.parent / "config") != _config(path.parent) or _read(path.parent / "attributes") != b"":
        _fail("fixed configuration or attributes changed")
    _marker(value, bootstrap=bootstrap)
    digest = value["origin_receipt_sha256"]
    if digest is None:
        if not bootstrap:
            _fail("profile initialization is not sealed")
    else:
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            _fail("invalid origin receipt digest")
        receipt_path = root / ORIGIN_RECEIPT
        if hashlib.sha256(_read(receipt_path)).hexdigest() != digest:
            _fail("sealed origin receipt changed")
        receipt = _json(receipt_path)
        if (receipt.get("owner_root") != str(root) or receipt.get("owner_id") != value["owner_id"]
                or receipt.get("project_root") != value["project_root"]
                or receipt.get("origin_path") != str(root / "origin.git")):
            _fail("sealed origin belongs to another fixture")
    return value


def create(project: Path, owner_root: Path, owner_id: str, *, kind: str) -> Path:
    """Create fresh profile files; the fixture owner must already hold its lock."""
    _path(owner_root, private=True)
    _path(project)
    directory = owner_root / DIRECTORY
    if directory.exists() or directory.is_symlink():
        _fail("existing profile must be validated, never recreated")
    value = {"schema": SCHEMA, "kind": kind, "owner_root": str(owner_root),
             "owner_id": owner_id, "project_root": str(project), "origin_receipt_sha256": None}
    _marker(value, bootstrap=True)
    directory.mkdir(mode=0o700)
    _write_new(directory / "config", _config(directory))
    _write_new(directory / "attributes", b"")
    _write_new(directory / "context.json", _bytes(value))
    return directory / "context.json"


def seal(path: Path) -> None:
    """Bind the completed origin once, while the fixture owner holds its lock."""
    value = _profile(path, bootstrap=True)
    if value["origin_receipt_sha256"] is not None:
        return
    value["origin_receipt_sha256"] = hashlib.sha256(_read(Path(value["owner_root"]) / ORIGIN_RECEIPT)).hexdigest()
    temporary = path.with_suffix(".pending")
    _write_new(temporary, _bytes(value))
    os.replace(temporary, path)
    _profile(path, bootstrap=True)


def discover(cwd: Path) -> Path | None:
    """Fixture-owner discovery only; ordinary consumers use the explicit pointer."""
    for parent in (cwd, *cwd.parents):
        candidate = parent / DIRECTORY / "context.json"
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None


def _scope(cwd: Path, value: dict, *, bootstrap: bool) -> tuple[Path | None, Path | None]:
    _path(cwd)
    root, project = Path(value["owner_root"]), Path(value["project_root"])
    origin = root / "origin.git"
    if cwd == root and bootstrap:
        return None, None
    if cwd == origin:
        return origin, origin
    current = cwd
    while root in current.parents:
        if current != project and (current / "HEAD").exists() and (
                (current / "commondir").exists()
                or all((current / name).exists() for name in ("objects", "refs"))):
            _fail("foreign bare repository inside owned scope")
        metadata = current / ".git"
        if metadata.is_symlink():
            _fail("linked repository metadata is unsafe")
        if current == project:
            if not metadata.exists() and bootstrap:
                return None, None
            if not metadata.is_dir():
                _fail("primary repository metadata missing")
            return metadata, metadata
        if metadata.exists():
            text = _read(metadata, private=False).decode().strip()
            if not text.startswith("gitdir: "):
                _fail("foreign repository in owned scope")
            target = Path(text.removeprefix("gitdir: "))
            if not target.is_absolute():
                target = (current / target).resolve()
            _path(target)
            if target.parent != project / ".git" / "worktrees":
                _fail("worktree belongs to another repository")
            common = _read(target / "commondir", private=False).decode().strip()
            if (target / common).resolve() != project / ".git":
                _fail("worktree common directory changed")
            if Path(_read(target / "gitdir", private=False).decode().strip()) != metadata:
                _fail("worktree registration points elsewhere")
            return project / ".git", target
        current = current.parent
    _fail("command is outside its exact owned Git scope")


@lru_cache(maxsize=64)
def _parse_configuration(data: bytes, git_binary: str) -> tuple[str, ...]:
    """Parse the exact checked bytes, never reopen a mutable configuration path."""
    try:
        result = subprocess.run(
            [git_binary, "config", "--no-includes", "--null", "--file", "-", "--list"],
            input=data, capture_output=True, timeout=10, check=True,
            env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"},
        )
        if len(result.stdout) > 65536:
            _fail("repository configuration exceeds its bound")
        return tuple(result.stdout.decode().split("\0"))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise GitEnvironmentError("UNSAFE_GIT_ENVIRONMENT: local configuration unavailable") from error


def _sealed_editor_base(value: dict) -> str:
    """Resolve a data-only history hint from the same immutable origin receipt."""
    data = _read(Path(value["owner_root"]) / ORIGIN_RECEIPT)
    if hashlib.sha256(data).hexdigest() != value["origin_receipt_sha256"]:
        _fail("sealed origin receipt changed")
    try:
        receipt = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        _fail("invalid sealed origin receipt")
    default = receipt.get("default_ref") if isinstance(receipt, dict) else None
    prefix = "refs/heads/"
    if (not isinstance(default, str) or not default.startswith(prefix)
            or not default.removeprefix(prefix)
            or any(ord(char) <= 32 or ord(char) == 127 for char in default)):
        _fail("invalid sealed origin default ref")
    return "origin/" + default.removeprefix(prefix)


def _local_configuration(metadata: Path, worktree_metadata: Path, value: dict, env: dict, *, bootstrap: bool) -> None:
    project = Path(value["project_root"])
    origin = Path(value["owner_root"]) / "origin.git"
    _path(metadata)
    if not metadata.is_dir():
        _fail("owned Git metadata is missing")
    # Only a registered linked worktree may redirect its common directory.
    # Git follows these files independently of repository configuration.
    for relative in ("commondir", "shallow", "objects/info/alternates",
                     "objects/info/http-alternates", "info/grafts"):
        path = metadata / relative
        if path.exists() or path.is_symlink():
            _fail("owned Git metadata redirects or borrows repository state")
    for relative in ("objects", "objects/info", "refs", "info"):
        _path(metadata / relative)
    hooks = metadata / "hooks"
    _path(hooks)
    sealed = value["origin_receipt_sha256"] is not None
    if metadata == origin and sealed:
        hook = hooks / "pre-receive"
        if _read(hook, private=False) != PUSH_GUARD or hook.stat().st_mode & 0o111 != 0o111:
            _fail("owned origin push guard is missing or disabled")
    if hooks.exists():
        for path in hooks.iterdir():
            data = _read(path, private=False)
            if path.name.endswith(".sample"):
                continue
            if not (metadata == origin and path.name == "pre-receive" and data == PUSH_GUARD):
                _fail("active repository hook is forbidden")
    booleans = {"core.filemode", "core.bare", "core.logallrefupdates", "core.ignorecase",
                "core.precomposeunicode", "core.symlinks", "extensions.worktreeconfig"}
    observed: dict[str, list[str]] = {}
    editor_base: str | None = None
    for path in dict.fromkeys([metadata / "config", worktree_metadata / "config.worktree"]):
        if not path.exists():
            continue
        # Identity, permissions and bytes are checked on every call. Only the
        # pure parse of those immutable bytes is cached, with a fixed bound.
        data = _read(path, private=False)
        binary = shutil.which("git", path=env.get("PATH"))
        if binary is None:
            _fail("Git executable is unavailable")
        entries = _parse_configuration(data, binary)
        for entry in entries:
            if not entry:
                continue
            key, _, item = entry.partition("\n")
            key = key.lower()
            observed.setdefault(key, []).append(item)
            safe = (
                key in booleans and item.lower() in {"true", "false"}
                or key == "core.repositoryformatversion" and item in {"0", "1"}
                or key == "extensions.objectformat" and item in {"sha1", "sha256"}
                or key in {"commit.gpgsign", "tag.gpgsign"} and item == "false"
                or key == "core.fsmonitor" and item.lower() in {"false", "no", "off", "0"}
                or key in {"user.name", "user.email"} and 0 < len(item) <= 256 and not any(c in item for c in "\n\r\0")
                or key == "core.hookspath" and item == str(hooks)
                or metadata != origin and key == "remote.origin.url" and item == str(origin)
                or metadata != origin and key == "remote.origin.fetch" and item == FETCH
                or bootstrap and value["origin_receipt_sha256"] is None and metadata == origin
                and key == "remote.origin.url" and item == str(project)
                or re.fullmatch(r"branch\..+\.remote", key) is not None and item in {"origin", "."}
                or re.fullmatch(r"branch\..+\.merge", key) is not None and item.startswith("refs/heads/")
            )
            if (not safe and sealed and metadata != origin
                    and re.fullmatch(r"branch\.[^\s\x00-\x1f\x7f]+\.vscode-merge-base", key)):
                # This editor hint changes no Git behavior or authority. Bind its
                # only admitted value to the frozen origin, not caller text.
                if editor_base is None:
                    editor_base = _sealed_editor_base(value)
                safe = item == editor_base
            if not safe:
                _fail("unsupported or executable repository-local configuration")
    if sealed:
        if metadata == origin:
            if observed.get("core.hookspath") != [str(hooks)] or observed.get("core.bare") != ["true"]:
                _fail("owned origin identity or effective push guard changed")
        elif (observed.get("remote.origin.url") != [str(origin)]
              or observed.get("remote.origin.fetch") != [FETCH]
              or observed.get("core.bare") != ["false"]):
            _fail("owned project identity or origin changed")


def environment(cwd: Path, *, env: dict[str, str] | None = None,
                profile: Path | None = None, bootstrap: bool = False) -> dict[str, str]:
    """Return normal environment or the one validated owned profile overlay."""
    result = dict(os.environ if env is None else env)
    pointer = result.get(PROFILE_ENV)
    if profile is None and pointer is None:
        return result
    path = profile or Path(pointer)
    if pointer is not None and pointer != str(path):
        _fail("conflicting profile pointers")
    value = _profile(path, bootstrap=bootstrap)
    overlay = {PROFILE_ENV: str(path), "GIT_CONFIG_GLOBAL": str(path.parent / "config"),
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    for key, item in result.items():
        if key in overlay:
            if item != overlay[key]:
                _fail("caller changed an owned environment value")
        elif key in FORBIDDEN or key.startswith(("GIT_CONFIG_", "GIT_ATTR_")):
            _fail("caller Git overrides are forbidden")
    result.update(overlay)
    metadata, worktree_metadata = _scope(cwd, value, bootstrap=bootstrap)
    if metadata is not None:
        _local_configuration(metadata, worktree_metadata, value, result, bootstrap=bootstrap)
    origin = Path(value["owner_root"]) / "origin.git"
    if value["origin_receipt_sha256"] is not None and metadata != origin:
        _local_configuration(origin, origin, value, result, bootstrap=bootstrap)
    return result
