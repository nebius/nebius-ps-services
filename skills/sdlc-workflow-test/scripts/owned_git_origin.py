"""Own the single local, read-only Git origin of a disposable SDLC fixture."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Iterator


SCHEMA = "agentic-sdlc/owned-git-origin-v1"
RECEIPT = ".owned-git-origin.json"
LOCK = ".owned-git-origin.lock"
ORIGIN = "origin.git"
REJECT_PUSH = b"#!/bin/sh\n# This disposable baseline is read-only.\nexit 1\n"
FETCH = "+refs/heads/*:refs/remotes/origin/*"
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class OriginError(RuntimeError):
    """The fixture's exact local Git ownership could not be established."""


def _environment() -> dict[str, str]:
    forbidden = {
        "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_TEMPLATE_DIR",
        "GIT_SHALLOW_FILE", "GIT_REPLACE_REF_BASE",
        "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND",
        "GIT_ALLOW_PROTOCOL", "GIT_PROTOCOL", "GIT_PROTOCOL_FROM_USER",
        "GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "SSH_ASKPASS",
    }
    if any(key in os.environ for key in forbidden) or any(
        key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) for key in os.environ
    ):
        raise OriginError("Git environment overrides are forbidden for owned fixtures.")
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _git(root: Path, *arguments: str, allowed: tuple[int, ...] = (0,)) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
             "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
            env=_environment(), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OriginError("Owned fixture Git command could not complete.") from error
    if result.returncode not in allowed:
        raise OriginError("Owned fixture Git validation or initialization failed.")
    return result.stdout.strip()


def _path(path: Path, *, directory: bool = True) -> None:
    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise OriginError("Owned Git paths must be canonical and absolute.")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise OriginError("Owned Git paths must not contain symlinks.")
    if path.exists():
        value = path.stat()
        if value.st_uid != os.getuid() or (
            not stat.S_ISDIR(value.st_mode) if directory
            else not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
        ):
            raise OriginError("Owned Git path has unsafe type, owner or link count.")


def _scope(project: Path, owner_root: Path, owner_id: str) -> Path:
    _environment()
    _path(owner_root)
    _path(project)
    if (
        not owner_root.is_dir() or not project.is_dir()
        or owner_root == project or owner_root not in project.parents
        or owner_root.stat().st_mode & 0o077
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", owner_id) is None
    ):
        raise OriginError("Owned Git scope or generation identity is invalid.")
    origin = owner_root / ORIGIN
    _path(origin)
    _path(owner_root / RECEIPT, directory=False)
    return origin


@contextmanager
def _lock(owner_root: Path, *, create: bool) -> Iterator[None]:
    path = owner_root / LOCK
    _path(path, directory=False)
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as error:
        raise OriginError("Owned Git lock is unavailable.") from error
    try:
        value = os.fstat(fd)
        if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
                or value.st_uid != os.getuid() or value.st_mode & 0o077):
            raise OriginError("Owned Git lock is unsafe.")
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if create else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as error:
            raise OriginError("Owned Git initialization or validation is busy.") from error
        yield
    finally:
        os.close(fd)


def _storage(root: Path, *, bare: bool = False) -> None:
    metadata = root if bare else root / ".git"
    _path(metadata)
    if not metadata.is_dir():
        raise OriginError("Owned fixture Git metadata is missing.")
    count = 0
    for directory, directories, files in os.walk(metadata, followlinks=False):
        for name in directories:
            _path(Path(directory) / name)
        for name in files:
            _path(Path(directory) / name, directory=False)
            count += 1
            if count > 50_000:
                raise OriginError("Owned Git metadata exceeds its validation bound.")
            if name.endswith(".promisor"):
                raise OriginError("Borrowed or partial Git object storage is forbidden.")
    if any((metadata / relative).exists() for relative in (
        "commondir", "shallow", "objects/info/alternates", "objects/info/http-alternates",
    )):
        raise OriginError("Borrowed or partial Git object storage is forbidden.")


def _configuration(root: Path, *, hooks_path: Path | None = None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in _git(root, "config", "--null", "--list").split("\0"):
        if not item:
            continue
        key, separator, value = item.partition("\n")
        if not separator:
            raise OriginError("Owned Git configuration could not be parsed.")
        result.setdefault(key.lower(), []).append(value)
    if any(key.endswith(".promisor") or key == "extensions.partialclone" for key in result):
        raise OriginError("Partial Git object storage is forbidden.")
    if any(value != str(hooks_path) for value in result.get("core.hookspath", [])):
        raise OriginError("Command-bearing Git hooks configuration is forbidden for owned fixtures.")
    command_keys = {
        "uploadpack.packobjectshook",
        "core.sshcommand", "core.gitproxy", "core.fsmonitor",
    }
    if any(
        (key in command_keys and not (
            key == "core.fsmonitor" and all(value.lower() in {"false", "no", "off", "0"} for value in values)
        ))
        or (key.startswith("filter.") and key.endswith((".clean", ".smudge", ".process")))
        for key, values in result.items()
    ):
        raise OriginError("Command-bearing Git configuration is forbidden for owned fixtures.")
    return result


def preflight(project: Path) -> None:
    """Reject inherited command configuration before fixture mutations."""
    _path(project)
    _configuration(project, hooks_path=project / ".git" / "hooks")
    hooks = project / ".git" / "hooks"
    if hooks.exists() or hooks.is_symlink():
        _path(hooks)
        if not hooks.is_dir() or any(
            path.is_symlink() or not path.is_file() or not path.name.endswith(".sample")
            for path in hooks.iterdir()
        ):
            raise OriginError("Active project Git hooks are forbidden for owned fixtures.")


def _reject_rewrites(config: dict[str, list[str]], *paths: Path) -> None:
    for key, values in config.items():
        if key.startswith("url.") and key.endswith((".insteadof", ".pushinsteadof")):
            if any(str(path).startswith(value) for value in values for path in paths):
                raise OriginError("Owned Git transport URL rewriting is forbidden.")


def _project(project: Path, origin: Path, *, initialized: bool) -> None:
    _storage(project)
    preflight(project)
    config = _configuration(project, hooks_path=project / ".git" / "hooks")
    _reject_rewrites(config, project, origin)
    if _git(project, "rev-parse", "--show-toplevel") != str(project):
        raise OriginError("Owned project Git identity changed.")
    expected = {"remote.origin.url": [str(origin)], "remote.origin.fetch": [FETCH]}
    remotes = {key: values for key, values in config.items() if key.startswith("remote.")}
    if remotes != (expected if initialized else {}):
        raise OriginError("Owned project must have exactly its one approved origin.")
    if initialized:
        for options in (("--all",), ("--push", "--all")):
            if _git(project, "remote", "get-url", *options, "origin") != str(origin):
                raise OriginError("Owned origin effective URL was redirected.")


def _validate(
    project: Path, owner_root: Path, owner_id: str, *, baseline: str | None = None,
) -> dict[str, str]:
    origin = _scope(project, owner_root, owner_id)
    receipt = owner_root / RECEIPT
    if (not receipt.is_file() or receipt.stat().st_size > 16_384
            or receipt.stat().st_mode & 0o077):
        raise OriginError("Owned origin receipt is missing or unsafe.")
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OriginError("Owned origin receipt is invalid.") from error
    keys = {"schema", "owner_root", "owner_id", "project_root", "origin_path",
            "baseline_sha", "default_ref"}
    if (not isinstance(value, dict) or set(value) != keys
            or any(not isinstance(item, str) for item in value.values())
            or value["schema"] != SCHEMA or value["owner_root"] != str(owner_root)
            or value["owner_id"] != owner_id or value["project_root"] != str(project)
            or value["origin_path"] != str(origin)
            or SHA.fullmatch(value["baseline_sha"]) is None
            or not value["default_ref"].startswith("refs/heads/")
            or (baseline is not None and baseline != value["baseline_sha"])):
        raise OriginError("Owned origin receipt identity does not match this fixture.")
    _project(project, origin, initialized=True)
    _storage(origin, bare=True)
    config = _configuration(origin, hooks_path=origin / "hooks")
    _reject_rewrites(config, project, origin)
    if any(key.startswith("remote.") for key in config):
        raise OriginError("The owned bare origin must not have another remote.")
    if _git(origin, "rev-parse", "--is-bare-repository") != "true":
        raise OriginError("Owned origin is not a bare repository.")
    if _git(origin, "rev-parse", "--absolute-git-dir") != str(origin):
        raise OriginError("Owned bare repository identity changed.")
    expected_ref = f"{value['default_ref']} {value['baseline_sha']}"
    if (_git(origin, "symbolic-ref", "HEAD") != value["default_ref"]
            or _git(origin, "for-each-ref", "--format=%(refname) %(objectname)") != expected_ref):
        raise OriginError("Owned origin default or frozen baseline references changed.")
    remote_default = "refs/remotes/origin/" + value["default_ref"].removeprefix("refs/heads/")
    if (_git(project, "symbolic-ref", "refs/remotes/origin/HEAD") != remote_default
            or _git(project, "rev-parse", remote_default) != value["baseline_sha"]):
        raise OriginError("Owned origin tracking default or baseline changed.")
    hooks = origin / "hooks"
    hook = hooks / "pre-receive"
    _path(hook, directory=False)
    if (_git(origin, "config", "--get", "core.hooksPath") != str(hooks)
            or not hook.is_file() or hook.stat().st_mode & 0o111 != 0o111
            or hook.read_bytes() != REJECT_PUSH):
        raise OriginError("Owned origin push rejection guard changed.")
    _git(project, "merge-base", "--is-ancestor", value["baseline_sha"], "HEAD")
    return value


def validate(
    project: Path, owner_root: Path, owner_id: str, *, baseline: str | None = None,
    require_clean: bool = False,
) -> dict[str, str]:
    """Read and validate exact ownership without contacting a remote."""
    _scope(project, owner_root, owner_id)
    with _lock(owner_root, create=False):
        value = _validate(project, owner_root, owner_id, baseline=baseline)
        if require_clean and _git(project, "status", "--porcelain", "--untracked-files=all"):
            raise OriginError("Owned project has uncommitted changes; cleanup is forbidden.")
        return value


def initialize(project: Path, owner_root: Path, owner_id: str) -> dict[str, str]:
    """Seed a proven owned clean fixture once; never adopt partial origins."""
    origin = _scope(project, owner_root, owner_id)
    receipt = owner_root / RECEIPT
    with _lock(owner_root, create=True):
        if origin.exists() or receipt.exists():
            return _validate(project, owner_root, owner_id)
        _project(project, origin, initialized=False)
        if _git(project, "status", "--porcelain", "--untracked-files=all"):
            raise OriginError("Owned origin initialization requires a clean baseline.")
        baseline = _git(project, "rev-parse", "HEAD")
        default = _git(project, "symbolic-ref", "HEAD")
        if SHA.fullmatch(baseline) is None or not default.startswith("refs/heads/"):
            raise OriginError("Owned origin initialization needs a committed branch.")
        _git(owner_root, "clone", "--bare", "--no-hardlinks", "--template=",
             "--single-branch", "--branch", default.removeprefix("refs/heads/"),
             "--", str(project), str(origin))
        origin.chmod(0o700)
        if _git(origin, "remote") == "origin":
            _git(origin, "remote", "remove", "origin")
        hooks = origin / "hooks"
        hooks.mkdir(mode=0o700, exist_ok=True)
        hook = hooks / "pre-receive"
        with hook.open("xb") as handle:
            handle.write(REJECT_PUSH)
        hook.chmod(0o755)
        _git(origin, "config", "core.hooksPath", str(hooks))
        if (_git(project, "rev-parse", "HEAD") != baseline
                or _git(project, "status", "--porcelain", "--untracked-files=all")):
            raise OriginError("Owned fixture changed while its origin was initialized.")
        _git(project, "remote", "add", "origin", str(origin))
        _storage(origin, bare=True)
        _configuration(origin, hooks_path=origin / "hooks")
        _git(project, "fetch", "--no-tags", "origin")
        _git(project, "remote", "set-head", "origin", "--auto")
        value = {"schema": SCHEMA, "owner_root": str(owner_root), "owner_id": owner_id,
                 "project_root": str(project), "origin_path": str(origin),
                 "baseline_sha": baseline, "default_ref": default}
        fd, temporary = tempfile.mkstemp(prefix=".owned-origin-", dir=owner_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, receipt)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return _validate(project, owner_root, owner_id, baseline=baseline)


def create_baseline(project: Path) -> str:
    """Create the lifecycle-owned marker baseline before any SDLC phase."""
    _path(project)
    if (project / ".git").exists() or (project / ".git").is_symlink():
        raise OriginError("The new lifecycle project already has Git metadata.")
    preflight(project)
    _git(project, "-c", "core.hooksPath=/dev/null", "init", "--template=", "-b", "main")
    _git(project, "config", "user.name", "SDLC Verification")
    _git(project, "config", "user.email", "sdlc-verification@example.invalid")
    _git(project, "config", "commit.gpgSign", "false")
    preflight(project)
    _git(project, "add", "-A")
    _git(project, "-c", f"core.hooksPath={project / '.git' / 'hooks'}",
         "commit", "-m", "initial disposable fixture")
    return _git(project, "rev-parse", "HEAD")
