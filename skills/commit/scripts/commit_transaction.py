#!/usr/bin/env python3
"""Prepare and execute one exact whole-repository local commit transaction."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Iterator, Sequence


# BEGIN shared runtime bootstrap
def _load_skill_support(group, anchor_file, declared_path, *, source_only=False):
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path
    import stat as _stat
    import sys as _sys
    from types import ModuleType as _ModuleType

    def read_source(path):
        path = _Path(_os.path.abspath(path))
        for part in (*reversed(path.parents), path):
            metadata = part.lstat()
            if _stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or part == path:
                    raise ImportError("shared runtime path contains an unsafe symlink")
                metadata = part.stat()
            if part != path:
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if (not _stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, _os.getuid()}
                        or metadata.st_mode & 0o022 and not sticky):
                    raise ImportError("unsafe shared runtime ancestry")
        path = path.resolve(strict=True)
        descriptor = _os.open(path.anchor, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = _os.open(part, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=descriptor)
                _os.close(descriptor)
                descriptor = child
                metadata = _os.fstat(descriptor)
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if metadata.st_uid not in {0, _os.getuid()} or metadata.st_mode & 0o022 and not sticky:
                    raise ImportError("unsafe shared runtime ancestry")
            child = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = _os.fstat(child)
                if (not _stat.S_ISREG(before.st_mode) or before.st_uid != _os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 1048576):
                    raise ImportError("unsafe shared runtime source")
                data = bytearray()
                while chunk := _os.read(child, min(65536, 1048577 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ImportError("shared runtime source exceeds size limit")
                after = _os.fstat(child)
                bound = _os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                def identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(bound):
                    raise ImportError("shared runtime source changed while reading")
                return bytes(data), identity(after)
            finally:
                _os.close(child)
        finally:
            _os.close(descriptor)

    anchor = _Path(_os.path.abspath(anchor_file))
    declared_paths = (declared_path,) if isinstance(declared_path, str) else declared_path
    candidates = []
    for declared in declared_paths:
        relative = _Path(declared)
        if tuple(anchor.parts[-len(relative.parts):]) == relative.parts:
            catalog = anchor.parents[len(relative.parts) - 1]
            candidates.append(("catalog", catalog, catalog / "global-context-management/scripts"))
    if not source_only:
        flat = anchor.parent.parent if anchor.parent.name == "lib" else anchor.parent
        candidates.append(("flat", flat, flat))
        agent = "codex" if _os.environ.get("CODEX_THREAD_ID") else _os.environ.get("SKILLS_AGENT", "codex")
        if agent not in {"codex", "claude"}:
            raise ImportError("SKILLS_AGENT must be codex or claude")
        key, default = ("CODEX_HOME", ".codex") if agent == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
        home = _Path(_os.environ.get(key, str(_Path.home() / default))).expanduser()
        if not home.is_absolute():
            raise ImportError("shared runtime home must be absolute")
        candidates.append(("flat", home / "hooks", home / "hooks"))
    for kind, root, support in candidates:
        loader_path = support / "trusted_runtime.py"
        if not loader_path.exists() and not loader_path.is_symlink():
            if ((kind == "catalog" and (support.exists() or support.is_symlink()))
                    or any((support / name).exists() or (support / name).is_symlink()
                           for name in ("agent_runtime.py", "hook_runtime.py", "task_state_permissions.py"))):
                raise ImportError("incomplete shared runtime bundle; reinstall current support")
            continue
        data, identity = read_source(loader_path)
        digest = _hashlib.sha256(data).hexdigest()
        cache_name = "_skills_trusted_runtime"
        loader = _sys.modules.get(cache_name)
        provenance = (str(loader_path), identity, digest)
        if cache_name in _sys.modules:
            if (type(loader) is not _ModuleType or getattr(loader, "_bootstrap_provenance", None) != provenance):
                raise ImportError("conflicting shared runtime loader")
        else:
            loader = _ModuleType(cache_name)
            loader.__file__ = str(loader_path)
            exec(compile(data, str(loader_path), "exec"), loader.__dict__)
            loader._bootstrap_provenance = provenance
            loader._read_source = read_source
            _sys.modules[cache_name] = loader
        return loader.load_support(group, anchor=(kind, root), source_only=source_only)
    raise ImportError("Shared skill runtime unavailable; install the complete current skill support")
# END shared runtime bootstrap
_load_skill_support("runtime", __file__, "commit/scripts/commit_transaction.py")

from agent_runtime import agent_home  # noqa: E402


AUTH_SCHEMA = "commit-transaction.authorization.v1"
CLAIM_SCHEMA = "commit-transaction.claim.v1"
CLAIM_STATES = {"PREPARED", "STAGED", "COMMITTED", "STALE", "REVIEW_REQUIRED"}
EXECUTABLE_STATES = {"PREPARED", "STAGED"}
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
OBJECT_RE = re.compile(r"^[0-9a-f]{40,64}$")
REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,240}$")
WORKTREE_NAME_RE = re.compile(r"^project-[a-z0-9](?:[a-z0-9-]{0,86}[a-z0-9])?$")
TASK_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_JSON_BYTES = 128 * 1024
REPOSITORY_SHAPING_GIT_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ATTR_NOSYSTEM",
        "GIT_ATTR_SOURCE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DEFAULT_HASH",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_EXEC_PATH",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_OPTIONAL_LOCKS",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)
REPOSITORY_SHAPING_GIT_ENV_PREFIXES = ("GIT_ATTR_", "GIT_CONFIG_")


class TransactionError(RuntimeError):
    """A typed failure, with safe continuation separate from user authorization."""

    def __init__(self, reason: str, code: str = "unsafe_state"):
        super().__init__(reason)
        self.code = code
        self.retryable = code in {"candidate_changed", "no_commit_failure"}
        self.next_action = {
            "candidate_changed": "correct and review a fresh candidate under the active task",
            "no_commit_failure": "correct the failure and prepare again under the active task",
            "effect_unknown": "reconcile the retained operation before any retry",
            "review_required": "review the retained actual result",
            "scope_changed": "resolve the scope change without extending existing authority",
            "history_changed": "reconcile unexpected history without resetting it",
            "task_closed": "the completed or cancelled task cannot be reused",
            "task_consumed": "verify or publish the existing commit; do not create another",
            "task_missing": "begin the already authorized root task",
        }.get(code, "resolve the reported safety condition")


_MUTATION_LOCK_FD: int | None = None


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_text(value: object) -> str:
    return _digest_bytes(str(value).encode("utf-8"))


def _stable_json(value: dict[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise TransactionError(f"directory sync target is invalid: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _secure_directory(path: Path, *, create: bool) -> None:
    if path.is_symlink():
        raise TransactionError(f"private directory must not be a symlink: {path}")
    if path.exists():
        if not path.is_dir() or path.resolve(strict=True) != path:
            raise TransactionError(f"private directory must be canonical: {path}")
    elif create:
        parent = path.parent
        if not parent.exists():
            _secure_directory(parent, create=True)
        path.mkdir(mode=0o700)
        _fsync_directory(parent)
    if create:
        path.chmod(0o700)


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    _secure_directory(path.parent, create=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.fchmod(handle.fileno(), 0o600)
            handle.write(_stable_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise TransactionError(
            f"could not persist private transaction state: {path}"
        ) from error


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise TransactionError(f"{label} must not be a symlink: {path}")
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_JSON_BYTES:
            raise TransactionError(f"{label} must be a bounded regular file: {path}")
        raw = path.read_bytes()
        value: Any = json.loads(raw)
    except FileNotFoundError as error:
        raise TransactionError(f"{label} is missing: {path}") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransactionError(f"{label} is unreadable or invalid: {path}") from error
    if not isinstance(value, dict):
        raise TransactionError(f"{label} must contain an object: {path}")
    return value


def _safe_private_file(path: Path, root: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    expected_root = Path(os.path.abspath(root))
    try:
        relative = absolute.relative_to(expected_root)
    except ValueError:
        return False
    current = expected_root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            metadata = current.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode):
            return False
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            return False
        if current == absolute:
            return (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_nlink == 1
                and stat.S_IMODE(metadata.st_mode) == 0o600
            )
        if not stat.S_ISDIR(metadata.st_mode):
            return False
    return False


def _git_environment() -> dict[str, str]:
    shaped = sorted(
        name
        for name in os.environ
        if (
            name in REPOSITORY_SHAPING_GIT_ENV
            or name.startswith(REPOSITORY_SHAPING_GIT_ENV_PREFIXES)
        )
    )
    if shaped:
        raise TransactionError(
            "repository-shaping Git environment must be unset: " + ", ".join(shaped)
        )
    return os.environ.copy()


def _run_git(
    root: Path,
    arguments: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    environment = _git_environment() if env is None else env
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=120,
            pass_fds=(() if _MUTATION_LOCK_FD is None else (_MUTATION_LOCK_FD,)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TransactionError(
            f"Git command could not run: git {' '.join(arguments)}"
        ) from error
    if check and completed.returncode != 0:
        reason = completed.stderr.decode("utf-8", errors="replace").strip()
        raise TransactionError(
            reason or f"Git command failed: git {' '.join(arguments)}"
        )
    return completed


def _git_text(root: Path, *arguments: str) -> str:
    return _run_git(root, arguments).stdout.decode("utf-8").strip()


def _exact_direct_child(root: Path, base_head: str, commit_head: str) -> bool:
    parents = _git_text(root, "rev-list", "--parents", "-n", "1", commit_head).split()
    return parents == [commit_head, base_head]


def _canonical_repo(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise TransactionError("repository root must be absolute")
    try:
        root = candidate.resolve(strict=True)
    except OSError as error:
        raise TransactionError("repository root is unavailable") from error
    observed = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve(
        strict=True
    )
    if observed != root:
        raise TransactionError("repository root is not the exact Git worktree root")
    return root


def _common_dir(root: Path) -> Path:
    value = Path(_git_text(root, "rev-parse", "--git-common-dir"))
    if not value.is_absolute():
        value = root / value
    return value.resolve(strict=True)


def _identity(root: Path) -> dict[str, str]:
    reference = _git_text(root, "symbolic-ref", "-q", "HEAD")
    if REF_RE.fullmatch(reference) is None:
        raise TransactionError("detached or invalid HEAD is not eligible for $commit")
    head = _git_text(root, "rev-parse", "HEAD")
    if OBJECT_RE.fullmatch(head) is None:
        raise TransactionError("current HEAD is invalid")
    common_dir = _common_dir(root)
    return {
        "repo_root": str(root),
        "worktree": str(root),
        "common_dir": str(common_dir),
        "ref": reference,
        "branch": reference.removeprefix("refs/heads/"),
        "head": head,
    }


def _repo_key(common_dir: Path) -> str:
    return _digest_text(common_dir)[:24]


def _ref_key(reference: str) -> str:
    return _digest_text(reference)[:24]


def _codex_home() -> Path:
    value = agent_home()
    if not value.is_absolute():
        raise TransactionError("CODEX_HOME must be absolute")
    return Path(os.path.abspath(value)).resolve(strict=False)


def _transaction_root(common_dir: Path) -> Path:
    return _codex_home() / "commit-transactions" / _repo_key(common_dir)


def expected_authorization_path(root: Path, session_id: str) -> Path:
    identity = _identity(root)
    return (
        _transaction_root(Path(identity["common_dir"]))
        / "sessions"
        / _digest_text(session_id)[:24]
        / "authorization.json"
    )


def expected_claim_path(root: Path) -> Path:
    identity = _identity(root)
    return (
        _transaction_root(Path(identity["common_dir"]))
        / "claims"
        / f"{_ref_key(identity['ref'])}.json"
    )


@contextmanager
def _repository_lock(common_dir: Path) -> Iterator[None]:
    global _MUTATION_LOCK_FD
    lock_root = _transaction_root(common_dir)
    _secure_directory(lock_root, create=True)
    lock_path = lock_root / ".lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise TransactionError("repository transaction lock is invalid")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _MUTATION_LOCK_FD = descriptor
        yield
    finally:
        _MUTATION_LOCK_FD = None
        # Closing our copy preserves the inherited lock if a Git child survives us.
        os.close(descriptor)


def _git_state_path(root: Path, name: str) -> Path:
    value = Path(_git_text(root, "rev-parse", "--git-path", name))
    return value if value.is_absolute() else root / value


def _safety_checks(root: Path, *, allow_default_branch: bool) -> None:
    conflicts = _git_text(root, "diff", "--name-only", "--diff-filter=U")
    if conflicts:
        raise TransactionError("unresolved conflicts block $commit")
    for marker in (
        "MERGE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
    ):
        if _git_state_path(root, marker).exists():
            raise TransactionError(
                f"Git operation in progress blocks $commit: {marker}"
            )
    branch = _identity(root)["branch"]
    default = _run_git(
        root,
        ("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"),
        check=False,
    )
    default_branch = default.stdout.decode("utf-8").strip().removeprefix("origin/")
    if (
        default.returncode == 0
        and branch == default_branch
        and not allow_default_branch
    ):
        raise TransactionError(
            f"current branch {branch} is the local default branch; explicit default-branch authorization is required"
        )


def _status(root: Path) -> bytes:
    return _run_git(
        root,
        ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
    ).stdout


def _index_path(root: Path) -> Path:
    value = Path(_git_text(root, "rev-parse", "--git-path", "index"))
    return (value if value.is_absolute() else root / value).resolve(strict=False)


def _preview_tree(root: Path, private_root: Path) -> tuple[str, str]:
    _secure_directory(private_root, create=True)
    source_index = _index_path(root)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="preview-index-", dir=private_root
    )
    os.close(descriptor)
    preview_index = Path(temporary_name)
    try:
        if source_index.exists():
            shutil.copyfile(source_index, preview_index)
        else:
            preview_index.unlink(missing_ok=True)
        environment = _git_environment()
        environment["GIT_INDEX_FILE"] = str(preview_index)
        if not source_index.exists():
            _run_git(root, ("read-tree", "HEAD"), env=environment)
        _run_git(root, ("add", "-A"), env=environment)
        tree = _run_git(root, ("write-tree",), env=environment).stdout.decode().strip()
        check = _run_git(
            root, ("diff", "--cached", "--check"), env=environment, check=False
        )
        if check.returncode != 0:
            reason = check.stdout.decode("utf-8", errors="replace").strip()
            raise TransactionError(
                reason or "candidate staged diff failed git diff --cached --check"
            )
        if OBJECT_RE.fullmatch(tree) is None:
            raise TransactionError("candidate tree is invalid")
        return tree, _digest_text(tree)
    finally:
        preview_index.unlink(missing_ok=True)


def _index_tree(root: Path) -> str:
    value = _git_text(root, "write-tree")
    if OBJECT_RE.fullmatch(value) is None:
        raise TransactionError("current index tree is invalid")
    return value


def _has_unstaged_or_untracked(root: Path) -> bool:
    unstaged = _run_git(root, ("diff", "--quiet", "--"), check=False)
    untracked = _run_git(
        root, ("ls-files", "--others", "--exclude-standard", "-z")
    ).stdout
    return unstaged.returncode != 0 or bool(untracked)


def _validate_authorization(
    value: dict[str, Any], root: Path, session_id: str, path: Path
) -> None:
    identity = _identity(root)
    expected_keys = {
        "schema",
        "state",
        "repo_root",
        "worktree",
        "common_dir",
        "ref",
        "base_head",
        "session_sha256",
        "turn_sha256",
        "prompt_sha256",
        "owner",
        "owner_evidence_path",
        "owner_evidence_sha256",
        "allow_default_branch",
    }
    if set(value) != expected_keys or value.get("schema") != (
        ROOT_AUTH_SCHEMA if value.get("owner") in ROOT_OWNERS else AUTH_SCHEMA
    ):
        raise TransactionError("commit authorization schema is invalid")
    if value.get("state") != "AUTHORIZED":
        raise TransactionError("commit authorization was already consumed")
    expected_path = expected_authorization_path(root, session_id)
    if path != expected_path:
        raise TransactionError(
            "commit authorization path is not canonical for this session"
        )
    expected = {
        "repo_root": identity["repo_root"],
        "worktree": identity["worktree"],
        "common_dir": identity["common_dir"],
        "ref": identity["ref"],
        "base_head": identity["head"],
        "session_sha256": _digest_text(session_id),
    }
    if any(
        value.get(key) != expected_value for key, expected_value in expected.items()
    ):
        raise TransactionError(
            "commit authorization is not bound to the current repository state"
        )
    if not all(
        isinstance(value.get(key), str) and DIGEST_RE.fullmatch(value[key])
        for key in ("turn_sha256", "prompt_sha256")
    ):
        raise TransactionError("commit authorization identity is invalid")
    if type(value.get("allow_default_branch")) is not bool:
        raise TransactionError("commit authorization default-branch policy is invalid")
    owner = value.get("owner")
    if owner in ROOT_OWNERS:
        _, grant = _validate_task(value, root, session_id)
        if value["ref"] not in grant["targets"]:
            raise TransactionError(
                "authorization target is outside the task", "scope_changed"
            )
        if value["allow_default_branch"] != grant["allow_default_branch"]:
            raise TransactionError("default branch policy changed", "scope_changed")
    elif owner == "task-implementer":
        evidence_value = value.get("owner_evidence_path")
        evidence_digest = value.get("owner_evidence_sha256")
        if (
            not isinstance(evidence_value, str)
            or not Path(evidence_value).is_absolute()
            or not isinstance(evidence_digest, str)
            or DIGEST_RE.fullmatch(evidence_digest) is None
        ):
            raise TransactionError("Task Implementer commit evidence is invalid")
        evidence_path = Path(evidence_value)
        if not _safe_private_file(evidence_path, _codex_home() / "task-implementer"):
            raise TransactionError("Task Implementer worker evidence path is unsafe")
        evidence = _load_json(evidence_path, "Task Implementer worker evidence")
        if (
            evidence_digest != value["turn_sha256"]
            or evidence.get("state") != "running"
            or evidence.get("base_commit") != identity["head"]
            or evidence.get("worker_session_sha256") != _digest_text(session_id)
            or evidence.get("assignment_sha256") != value["turn_sha256"]
        ):
            raise TransactionError("Task Implementer worker authorization is stale")
    else:
        raise TransactionError("commit authorization owner is invalid")


def _validate_claim_owner(
    claim: dict[str, Any],
    root: Path,
    session_id: str,
    *,
    allow_exact_direct_child: bool = False,
) -> None:
    if claim["authorization_owner"] in ROOT_OWNERS:
        _validate_task(
            claim,
            root,
            session_id,
            allow_closed=claim["state"] in {"COMMITTED", "REVIEW_REQUIRED"},
            validate_scope=claim["state"] not in {"COMMITTED", "REVIEW_REQUIRED"},
        )
        return
    evidence_path = Path(str(claim["owner_evidence_path"]))
    if not _safe_private_file(evidence_path, _codex_home() / "task-implementer"):
        raise TransactionError("Task Implementer worker evidence path is unsafe")
    evidence = _load_json(evidence_path, "Task Implementer worker evidence")
    current_head = _identity(root)["head"]
    expected_head = claim["base_head"]
    if claim["state"] in {"REVIEW_REQUIRED", "COMMITTED"}:
        commit_head = claim.get("commit_head")
        if isinstance(commit_head, str) and OBJECT_RE.fullmatch(commit_head):
            expected_head = commit_head
    head_matches = current_head == expected_head
    if (
        not head_matches
        and allow_exact_direct_child
        and claim["state"] in EXECUTABLE_STATES
    ):
        head_matches = _exact_direct_child(root, claim["base_head"], current_head)
    if (
        claim["owner_evidence_sha256"] != claim["turn_sha256"]
        or evidence.get("state") != "running"
        or evidence.get("base_commit") != claim["base_head"]
        or evidence.get("worker_session_sha256") != _digest_text(session_id)
        or evidence.get("assignment_sha256") != claim["turn_sha256"]
        or not head_matches
    ):
        raise TransactionError("Task Implementer worker commit ownership is stale")


def _attempt_authorization_path(claim: dict[str, Any]) -> Path:
    task = Path(claim["owner_evidence_path"])
    return (
        task.parent
        / (task.stem + ".attempts")
        / (claim["authorization_sha256"] + ".json")
    )


def _persist_attempt_authorization(authorization: dict[str, Any], root: Path) -> None:
    if authorization["owner"] not in ROOT_OWNERS:
        return
    path = _attempt_authorization_path(
        {
            **authorization,
            "authorization_sha256": _digest_bytes(_stable_json(authorization)),
        }
    )
    if path.exists() or path.is_symlink():
        if (
            not _safe_private_file(path, _transaction_root(_common_dir(root)))
            or _load_json(path, "attempt authorization") != authorization
        ):
            raise TransactionError("attempt authorization changed", "scope_changed")
    else:
        _atomic_json(path, authorization)


def _validate_claim_authorization(
    claim: dict[str, Any], root: Path, session_id: str
) -> None:
    path = (
        _attempt_authorization_path(claim)
        if claim["authorization_owner"] in ROOT_OWNERS
        else expected_authorization_path(root, session_id)
    )
    private_root = _transaction_root(_common_dir(root))
    if not _safe_private_file(path, private_root):
        raise TransactionError("commit authorization path is unsafe")
    authorization = _load_json(path, "commit authorization")
    expected_state = "AUTHORIZED"
    if (
        authorization.get("schema")
        != (
            ROOT_AUTH_SCHEMA
            if claim["authorization_owner"] in ROOT_OWNERS
            else AUTH_SCHEMA
        )
        or authorization.get("state") != expected_state
        or authorization.get("owner") != claim["authorization_owner"]
    ):
        raise TransactionError("commit claim authorization is stale")
    if claim["authorization_owner"] in ROOT_OWNERS:
        _validate_task(
            claim,
            root,
            session_id,
            allow_closed=claim["state"] in {"COMMITTED", "REVIEW_REQUIRED"},
            validate_scope=claim["state"] not in {"COMMITTED", "REVIEW_REQUIRED"},
        )
    normalized = {**authorization, "state": "AUTHORIZED"}
    if claim["authorization_sha256"] != _digest_bytes(_stable_json(normalized)):
        raise TransactionError("commit claim authorization digest does not match")


def _validate_claim(
    value: dict[str, Any], root: Path, path: Path, *, historical: bool = False
) -> None:
    required = {
        "schema",
        "state",
        "repo_root",
        "worktree",
        "common_dir",
        "ref",
        "branch",
        "base_head",
        "initial_index_tree",
        "initial_status_sha256",
        "candidate_tree",
        "candidate_index_sha256",
        "session_sha256",
        "turn_sha256",
        "authorization_sha256",
        "authorization_owner",
        "owner_evidence_path",
        "owner_evidence_sha256",
        "token_sha256",
        "allow_default_branch",
        "commit_head",
        "commit_tree",
        "failure",
    }
    expected_schema = (
        ROOT_CLAIM_SCHEMA
        if value.get("authorization_owner") in ROOT_OWNERS
        else CLAIM_SCHEMA
    )
    if (
        historical
        and value.get("schema") == CLAIM_SCHEMA
        and value.get("state") in {"COMMITTED", "STALE"}
    ):
        expected_schema = CLAIM_SCHEMA
    if set(value) != required or value.get("schema") != expected_schema:
        raise TransactionError("commit claim schema is invalid")
    if value.get("state") not in CLAIM_STATES:
        raise TransactionError("commit claim state is invalid")
    if path != expected_claim_path(root):
        raise TransactionError("commit claim path is not canonical")
    identity = _identity(root)
    expected_identity = {
        "repo_root": identity["repo_root"],
        "worktree": identity["worktree"],
        "common_dir": identity["common_dir"],
        "ref": identity["ref"],
        "branch": identity["branch"],
    }
    if any(value.get(key) != expected for key, expected in expected_identity.items()):
        raise TransactionError("commit claim repository identity is invalid")
    for key in (
        "base_head",
        "initial_index_tree",
        "candidate_tree",
    ):
        if (
            not isinstance(value.get(key), str)
            or OBJECT_RE.fullmatch(value[key]) is None
        ):
            raise TransactionError(f"commit claim {key} is invalid")
    for key in (
        "initial_status_sha256",
        "candidate_index_sha256",
        "session_sha256",
        "turn_sha256",
        "authorization_sha256",
    ):
        if (
            not isinstance(value.get(key), str)
            or DIGEST_RE.fullmatch(value[key]) is None
        ):
            raise TransactionError(f"commit claim {key} is invalid")
    token_sha256 = value.get("token_sha256")
    if not isinstance(token_sha256, str) or DIGEST_RE.fullmatch(token_sha256) is None:
        raise TransactionError("commit claim token digest is invalid")
    if type(value.get("allow_default_branch")) is not bool:
        raise TransactionError("commit claim default-branch policy is invalid")
    if value.get("candidate_index_sha256") != _digest_text(value["candidate_tree"]):
        raise TransactionError("commit claim candidate digest is invalid")
    commit_head = value.get("commit_head")
    commit_tree = value.get("commit_tree")
    if (commit_head is None) != (commit_tree is None) or any(
        candidate is not None
        and (not isinstance(candidate, str) or OBJECT_RE.fullmatch(candidate) is None)
        for candidate in (commit_head, commit_tree)
    ):
        raise TransactionError("commit claim result identity is invalid")
    if value["state"] in {"REVIEW_REQUIRED", "COMMITTED"} and commit_head is None:
        raise TransactionError("commit claim result identity is missing")
    failure = value.get("failure")
    if failure is not None and (not isinstance(failure, str) or not failure):
        raise TransactionError("commit claim failure state is invalid")
    if value.get("authorization_owner") not in {
        "direct",
        "task-implementer",
        "create-pr",
    }:
        raise TransactionError("commit claim authorization owner is invalid")
    if (
        historical
        and value.get("schema") == CLAIM_SCHEMA
        and value.get("authorization_owner") == "direct"
    ):
        if (
            value.get("owner_evidence_path") is not None
            or value.get("owner_evidence_sha256") is not None
        ):
            raise TransactionError("direct commit claim evidence is invalid")
    elif (
        not isinstance(value.get("owner_evidence_path"), str)
        or not isinstance(value.get("owner_evidence_sha256"), str)
        or DIGEST_RE.fullmatch(value["owner_evidence_sha256"]) is None
    ):
        raise TransactionError("Task Implementer commit claim evidence is invalid")


def _coordination_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or OBJECT_RE.fullmatch(value) is None:
        raise TransactionError(f"Worktree {label} is invalid")
    return value


def _coordination_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise TransactionError(f"Worktree {label} is invalid")
    return Path(value)


def _worktree_coordination_ref(value: dict[str, Any], directory: str, name: str) -> str:
    if directory == "integration-preparations":
        required = {
            "schema",
            "kind",
            "name",
            "branch",
            "worktree",
            "source_branch",
            "source_ref",
            "source_head",
            "child_head",
            "commit_order",
            "commits",
            "token",
        }
        if (
            set(value) != required
            or value.get("schema") != 1
            or value.get("kind") != "integration-commit-preparation"
        ):
            raise TransactionError("Worktree integration preparation is malformed")
        states = {"verified", "review-required"}
    else:
        required = {
            "schema",
            "name",
            "branch",
            "worktree",
            "source_branch",
            "source_ref",
            "source_head",
            "child_head",
            "integration_branch",
            "integration_worktree",
            "integration_head",
            "state",
            "token",
        }
        if set(value) != required or value.get("schema") != 3:
            raise TransactionError("Worktree integration reservation is malformed")
        states = set()
    if (
        value.get("name") != name
        or WORKTREE_NAME_RE.fullmatch(name) is None
        or value.get("branch") != f"feature/{name.removeprefix('project-')}"
    ):
        raise TransactionError("Worktree coordination identity is invalid")
    source_branch = value.get("source_branch")
    source_ref = value.get("source_ref")
    if (
        not isinstance(source_branch, str)
        or not source_branch
        or source_ref != f"refs/heads/{source_branch}"
        or not isinstance(source_ref, str)
        or REF_RE.fullmatch(source_ref) is None
    ):
        raise TransactionError("Worktree coordination source ref is invalid")
    _coordination_path(value.get("worktree"), "coordination worktree")
    source_head = _coordination_sha(
        value.get("source_head"), "coordination source head"
    )
    child_head = _coordination_sha(value.get("child_head"), "coordination child head")
    if (
        not isinstance(value.get("token"), str)
        or TOKEN_RE.fullmatch(value["token"]) is None
    ):
        raise TransactionError("Worktree coordination token is invalid")
    if directory == "integration-preparations":
        order = value.get("commit_order")
        if order not in (["child"], ["source"], ["child", "source"]):
            raise TransactionError("Worktree integration preparation order is invalid")
        commits = value.get("commits")
        if not isinstance(commits, list) or len(commits) > len(order):
            raise TransactionError(
                "Worktree integration preparation commits are invalid"
            )
        initial_heads = {"child": child_head, "source": source_head}
        for index, commit in enumerate(commits):
            if (
                not isinstance(commit, dict)
                or set(commit)
                != {
                    "target",
                    "before_head",
                    "after_head",
                    "reviewed_tree",
                    "commit_tree",
                    "status",
                }
                or commit.get("target") != order[index]
                or commit.get("before_head") != initial_heads[order[index]]
                or commit.get("status") not in states
            ):
                raise TransactionError(
                    "Worktree integration preparation commit is invalid"
                )
            for field in ("before_head", "after_head", "reviewed_tree", "commit_tree"):
                _coordination_sha(commit.get(field), f"preparation {field}")
    else:
        _coordination_path(
            value.get("integration_worktree"), "integration candidate worktree"
        )
        if value.get("integration_branch") != f"codex/worktree-integrate/{name}":
            raise TransactionError("Worktree integration branch is invalid")
        state = value.get("state")
        integration_head = value.get("integration_head")
        if state not in {"planned", "present", "ready"}:
            raise TransactionError("Worktree integration state is invalid")
        if state == "ready":
            _coordination_sha(integration_head, "integration candidate head")
        elif integration_head is not None:
            raise TransactionError("Worktree unfinished integration head is invalid")
    return source_ref


def _active_worktree_claims(root: Path, source_ref: str) -> list[str]:
    state_root = root.parent / f"{root.name}-worktrees" / ".worktree-skill"
    conflicts: list[str] = []
    for directory in ("integration-preparations", "reservations"):
        candidate = state_root / directory
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            raise TransactionError("Worktree coordination state is unsafe")
        for path in sorted(candidate.glob("*.json")):
            value = _load_json(path, "Worktree coordination record")
            observed_ref = _worktree_coordination_ref(value, directory, path.stem)
            if observed_ref == source_ref:
                conflicts.append(f"{directory}/{path.name}")
    return conflicts


def _primary_worktree(root: Path) -> Path:
    common = _common_dir(root)
    primary = common.parent if common.name == ".git" else root
    try:
        observed = Path(_git_text(primary, "rev-parse", "--show-toplevel")).resolve(
            strict=True
        )
    except (OSError, TransactionError):
        return root
    return observed


def _worktree_manifest_path(value: dict[str, Any], path: Path, primary: Path) -> Path:
    required = {
        "schema",
        "status",
        "name",
        "branch",
        "primary",
        "worktree",
        "scope",
        "base",
        "task_slug",
        "source_branch",
        "source_ref",
        "expected_head",
        "integration_source_head",
        "integration_child_head",
        "integration_head",
        "lease_state",
        "lease_owner",
        "lease_token",
    }
    name = path.stem
    if (
        set(value) != required
        or value.get("schema") != 4
        or value.get("status") not in {"planned", "active", "recovery", "integrated"}
        or value.get("name") != name
        or WORKTREE_NAME_RE.fullmatch(name) is None
        or value.get("branch") != f"feature/{name.removeprefix('project-')}"
    ):
        raise TransactionError("Worktree ownership manifest is malformed")
    primary_value = _coordination_path(value.get("primary"), "manifest primary")
    worktree_value = _coordination_path(value.get("worktree"), "manifest worktree")
    if primary_value.resolve(strict=False) != primary:
        raise TransactionError("Worktree ownership primary is invalid")
    scope = value.get("scope")
    task_slug = value.get("task_slug")
    if (
        not isinstance(scope, str)
        or not scope
        or Path(scope).is_absolute()
        or ".." in Path(scope).parts
        or not isinstance(task_slug, str)
        or TASK_SLUG_RE.fullmatch(task_slug) is None
    ):
        raise TransactionError("Worktree ownership scope is invalid")
    _coordination_sha(value.get("base"), "manifest base")
    source_branch = value.get("source_branch")
    source_ref = value.get("source_ref")
    if (
        not isinstance(source_branch, str)
        or not source_branch
        or source_ref != f"refs/heads/{source_branch}"
        or not isinstance(source_ref, str)
        or REF_RE.fullmatch(source_ref) is None
    ):
        raise TransactionError("Worktree ownership source ref is invalid")
    expected_head = value.get("expected_head")
    if expected_head is not None:
        _coordination_sha(expected_head, "manifest expected head")
    if value["status"] != "planned" and expected_head is None:
        raise TransactionError("Worktree ownership expected head is missing")
    integration = (
        value.get("integration_source_head"),
        value.get("integration_child_head"),
        value.get("integration_head"),
    )
    for candidate in integration:
        if candidate is not None:
            _coordination_sha(candidate, "manifest integration proof")
    if value["status"] == "integrated":
        if (
            any(candidate is None for candidate in integration)
            or expected_head != integration[1]
        ):
            raise TransactionError("Worktree ownership integration proof is invalid")
    elif any(candidate is not None for candidate in integration):
        raise TransactionError(
            "Worktree ownership contains unexpected integration proof"
        )
    lease_state = value.get("lease_state")
    lease_owner = value.get("lease_owner")
    lease_token = value.get("lease_token")
    if lease_state == "none":
        if lease_owner is not None or lease_token is not None:
            raise TransactionError("Worktree ownership lease is invalid")
    elif (
        lease_state not in {"active", "released"}
        or lease_owner not in {"task-implementer", "agentic-sdlc"}
        or not isinstance(lease_token, str)
        or TOKEN_RE.fullmatch(lease_token) is None
    ):
        raise TransactionError("Worktree ownership lease is invalid")
    return worktree_value.resolve(strict=False)


def _managed_worktree_name(primary: Path, worktree: Path) -> str | None:
    state_root = primary.parent / f"{primary.name}-worktrees" / ".worktree-skill"
    if not state_root.exists():
        return None
    if state_root.is_symlink() or not state_root.is_dir():
        raise TransactionError("Worktree ownership state is unsafe")
    for path in sorted(state_root.glob("*.json")):
        value = _load_json(path, "Worktree ownership record")
        candidate = _worktree_manifest_path(value, path, primary)
        if candidate == worktree:
            return path.stem
    return None


def _archive_terminal_claim(
    root: Path, claim_path: Path, claim: dict[str, Any]
) -> None:
    state = claim.get("state")
    if state == "REVIEW_REQUIRED":
        raise TransactionError(
            "an earlier commit requires review before a new transaction can start"
        )
    if state not in {"STALE", "COMMITTED"}:
        raise TransactionError("an existing commit transaction is still active")
    if state == "COMMITTED":
        commit_head = claim.get("commit_head")
        commit_tree = claim.get("commit_tree")
        if (
            not isinstance(commit_head, str)
            or OBJECT_RE.fullmatch(commit_head) is None
            or not isinstance(commit_tree, str)
            or OBJECT_RE.fullmatch(commit_tree) is None
            or _run_git(
                root,
                ("merge-base", "--is-ancestor", commit_head, "HEAD"),
                check=False,
            ).returncode
            != 0
            or _git_text(root, "rev-parse", f"{commit_head}^{{tree}}") != commit_tree
        ):
            raise TransactionError(
                "completed commit claim no longer has exact ancestry proof"
            )
    raw = _stable_json(claim)
    destination = (
        claim_path.parent.parent
        / "history"
        / claim_path.stem
        / f"{_digest_bytes(raw)}.json"
    )
    if destination.exists():
        if destination.read_bytes() != raw:
            raise TransactionError("commit claim history is inconsistent")
        return
    _atomic_json(destination, claim)


def _recover_post_commit_for_prepare(
    root: Path,
    claim_path: Path,
    claim: dict[str, Any],
    authorization_path: Path,
    authorization: dict[str, Any],
    session_id: str,
) -> dict[str, object] | None:
    """Rebind one exact direct child after a prepare/commit crash window."""

    head = _identity(root)["head"]
    if head == claim["base_head"] or claim["state"] not in {
        *EXECUTABLE_STATES,
        "REVIEW_REQUIRED",
    }:
        return None
    tree = _git_text(root, "rev-parse", "HEAD^{tree}")
    if not _exact_direct_child(root, claim["base_head"], head):
        if claim["state"] in EXECUTABLE_STATES:
            _atomic_json(
                claim_path,
                {
                    **claim,
                    "state": "STALE",
                    "failure": "HEAD is not the transaction's exact direct child",
                },
            )
        raise TransactionError(
            "repository history moved outside the transaction; request a fresh commit after resolving the blocker"
        )
    if claim["state"] == "REVIEW_REQUIRED" and (
        claim.get("commit_head") != head or claim.get("commit_tree") != tree
    ):
        raise TransactionError("review-required commit identity changed")
    if authorization.get("owner") != claim.get("authorization_owner") or any(
        authorization.get(key) != claim.get(key)
        for key in ("owner_evidence_path", "owner_evidence_sha256")
    ):
        raise TransactionError(
            "commit recovery owner does not match the existing claim"
        )
    clean = not _status(root)
    if tree == claim["candidate_tree"] and clean:
        completed = {
            **claim,
            "state": "COMMITTED",
            "session_sha256": _digest_text(session_id),
            "turn_sha256": authorization["turn_sha256"],
            "authorization_sha256": _digest_bytes(_stable_json(authorization)),
            "authorization_owner": authorization["owner"],
            "owner_evidence_path": authorization["owner_evidence_path"],
            "owner_evidence_sha256": authorization["owner_evidence_sha256"],
            "commit_head": head,
            "commit_tree": tree,
            "failure": None,
        }
        _atomic_json(claim_path, completed)
        _advance_task_commit(root, completed)
        if authorization["owner"] in {"direct", "create-pr"}:
            _atomic_json(authorization_path, {**authorization, "state": "CONSUMED"})
        return {
            "status": "committed",
            "branch": claim["branch"],
            "commit": head,
            "tree": tree,
        }
    claim_token = secrets.token_hex(32)
    rebound = {
        **claim,
        "state": "REVIEW_REQUIRED",
        "session_sha256": _digest_text(session_id),
        "turn_sha256": authorization["turn_sha256"],
        "authorization_sha256": _digest_bytes(_stable_json(authorization)),
        "authorization_owner": authorization["owner"],
        "owner_evidence_path": authorization["owner_evidence_path"],
        "owner_evidence_sha256": authorization["owner_evidence_sha256"],
        "token_sha256": _digest_text(claim_token),
        "commit_head": head,
        "commit_tree": tree,
        "failure": "direct-child commit tree or checkout requires explicit review",
    }
    _validate_claim(rebound, root, claim_path)
    _atomic_json(claim_path, rebound)
    if authorization["owner"] in {"direct", "create-pr"}:
        _atomic_json(authorization_path, {**authorization, "state": "CONSUMED"})
    return {
        "status": "review-required",
        "branch": claim["branch"],
        "commit": head,
        "tree": tree,
        "clean": clean,
        "claim": str(claim_path),
        "token": claim_token,
    }


def _pr_origin_digest(root: Path) -> str:
    # get-url expands insteadOf/pushInsteadOf and --all includes every destination.
    destinations = {
        "fetch": _git_text(root, "remote", "get-url", "--all", "origin"),
        "push": _git_text(root, "remote", "get-url", "--push", "--all", "origin"),
    }
    if not all(destinations.values()):
        raise TransactionError("PR continuation requires an origin remote")
    return _digest_bytes(_stable_json(destinations))


ROOT_AUTH_SCHEMA = "commit-transaction.root-authorization.v2"
ROOT_CLAIM_SCHEMA = "commit-transaction.root-claim.v2"
TASK_SCHEMA = "commit-transaction.task.v1"
TASK_STATE_SCHEMA = "commit-transaction.task-state.v1"
ROOT_OWNERS = {"direct", "create-pr"}


def _task_path(root: Path, session_id: str, digest: str) -> Path:
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise TransactionError("task receipt digest is invalid", "scope_changed")
    return (
        expected_authorization_path(root, session_id).parent
        / "tasks"
        / f"{digest}.json"
    )


def _task_state_path(path: Path) -> Path:
    return path.with_suffix(".state.json")


def _task_closed_path(path: Path) -> Path:
    return path.with_suffix(".closed.json")


def _task_state(path: Path, grant: dict[str, Any]) -> dict[str, Any]:
    state_path = _task_state_path(path)
    if not _safe_private_file(state_path, _transaction_root(Path(grant["common_dir"]))):
        raise TransactionError(
            "task checkpoint is unavailable or unsafe", "effect_unknown"
        )
    state = _load_json(state_path, "task checkpoint")
    if (
        set(state) != {"schema", "grant_sha256", "targets", "base_head"}
        or state.get("schema") != TASK_STATE_SCHEMA
        or state.get("grant_sha256") != _digest_bytes(_stable_json(grant))
        or not isinstance(state.get("targets"), dict)
        or set(state["targets"]) != set(grant["targets"])
        or (grant["base_head"] is None and state.get("base_head") is not None)
        or (
            grant["base_head"] is not None
            and (
                not isinstance(state.get("base_head"), str)
                or not OBJECT_RE.fullmatch(state["base_head"])
            )
        )
    ):
        raise TransactionError("task checkpoint is invalid", "effect_unknown")
    for value in state["targets"].values():
        if (
            not isinstance(value, dict)
            or set(value) != {"head", "commits", "last_claim", "sync"}
            or not isinstance(value["head"], str)
            or not OBJECT_RE.fullmatch(value["head"])
            or type(value["commits"]) is not int
            or value["commits"] < 0
            or (
                value["last_claim"] is not None
                and (
                    not isinstance(value["last_claim"], str)
                    or not DIGEST_RE.fullmatch(value["last_claim"])
                )
            )
            or (value["sync"] is not None and not isinstance(value["sync"], dict))
        ):
            raise TransactionError(
                "task target checkpoint is invalid", "effect_unknown"
            )
        pending = value["sync"]
        if pending is not None and (
            set(pending)
            != {"from_head", "source_head", "source_ref", "result_head", "result_tree"}
            or not _valid_object(pending.get("from_head"))
            or not _valid_object(pending.get("source_head"))
            or pending.get("source_ref") not in {grant["base_ref"], *grant["targets"]}
            or any(
                pending.get(key) is not None and not _valid_object(pending[key])
                for key in ("result_head", "result_tree")
            )
            or (pending.get("result_head") is None)
            != (pending.get("result_tree") is None)
        ):
            raise TransactionError(
                "pending sync checkpoint is invalid", "effect_unknown"
            )
    return state


def _valid_object(value: object) -> bool:
    return isinstance(value, str) and OBJECT_RE.fullmatch(value) is not None


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def _valid_ref(root: Path, value: object, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and not _run_git(root, ("check-ref-format", value), check=False).returncode
    )


def _validate_grant_scope(grant: dict[str, Any], root: Path) -> None:
    action = grant["requested_action"]
    targets, dependencies = grant["targets"], grant["dependencies"]
    if (
        not _valid_ref(root, grant["initial_ref"], "refs/heads/")
        or not _valid_object(grant["initial_head"])
        or (
            grant["recovery_claim_sha256"] is not None
            and not _valid_digest(grant["recovery_claim_sha256"])
        )
        or not isinstance(dependencies, dict)
        or set(dependencies) != set(targets)
    ):
        raise TransactionError("task scope shape is invalid", "scope_changed")
    for ref, parents in dependencies.items():
        if (
            not isinstance(parents, list)
            or any(
                not isinstance(parent, str) or parent not in targets
                for parent in parents
            )
            or len(parents) != len(set(parents))
            or ref in parents
        ):
            raise TransactionError("task dependency shape is invalid", "scope_changed")

    def visit(ref, chain):
        if ref in chain:
            raise TransactionError("task dependencies contain a cycle", "scope_changed")
        for parent in dependencies[ref]:
            visit(parent, {*chain, ref})

    for ref in targets:
        visit(ref, set())
    if action == "commit":
        if any(
            grant[key] is not None
            for key in (
                "base_ref",
                "base_head",
                "origin_sha256",
                "default_ref",
                "validation_ref",
            )
        ):
            raise TransactionError(
                "local commit publication scope is invalid", "scope_changed"
            )
    elif (
        not _valid_digest(grant["origin_sha256"])
        or not _valid_ref(root, grant["default_ref"], "refs/remotes/origin/")
        or grant["allow_default_branch"]
        or grant["default_ref"].replace("refs/remotes/origin/", "refs/heads/", 1)
        in targets
    ):
        raise TransactionError("publication scope is invalid", "scope_changed")
    if action == "create-pr":
        if (
            not _valid_ref(root, grant["base_ref"], "refs/remotes/origin/")
            or not _valid_object(grant["base_head"])
            or grant["base_ref"].replace("refs/remotes/origin/", "refs/heads/", 1)
            in targets
            or grant["recovery_claim_sha256"] is not None
        ):
            raise TransactionError("PR base scope is invalid", "scope_changed")
        if grant["validation_ref"] is not None and (
            not _valid_ref(root, grant["validation_ref"], "refs/heads/")
            or grant["validation_ref"] in targets
        ):
            raise TransactionError("scratch ref is invalid", "scope_changed")
    elif (
        set(targets) != {grant["initial_ref"]}
        or any(dependencies.values())
        or any(
            grant[key] is not None
            for key in ("base_ref", "base_head", "validation_ref")
        )
    ):
        raise TransactionError("standalone task scope is invalid", "scope_changed")


def _validate_task(
    evidence: dict[str, Any],
    root: Path,
    session_id: str,
    *,
    allow_closed: bool = False,
    validate_scope: bool = True,
) -> tuple[Path, dict[str, Any]]:
    path = Path(str(evidence.get("owner_evidence_path")))
    private_root = _transaction_root(_common_dir(root))
    if not _safe_private_file(path, private_root):
        raise TransactionError("task grant is unavailable or unsafe", "scope_changed")
    grant = _load_json(path, "task grant")
    identity = _identity(root)
    expected = {
        "schema": TASK_SCHEMA,
        **{key: identity[key] for key in ("repo_root", "worktree", "common_dir")},
        "session_sha256": _digest_text(session_id),
    }
    keys = {
        *expected,
        "requested_action",
        "receipt_sha256",
        "turn_sha256",
        "prompt_sha256",
        "initial_ref",
        "initial_head",
        "targets",
        "dependencies",
        "base_ref",
        "base_head",
        "origin_sha256",
        "default_ref",
        "allow_default_branch",
        "validation_ref",
        "recovery_claim_sha256",
    }
    if (
        set(grant) != keys
        or any(grant.get(k) != v for k, v in expected.items())
        or grant.get("requested_action") not in {"commit", "commit-push", "create-pr"}
        or any(
            not isinstance(grant.get(k), str) or not DIGEST_RE.fullmatch(grant[k])
            for k in ("receipt_sha256", "turn_sha256", "prompt_sha256")
        )
        or path != _task_path(root, session_id, grant["receipt_sha256"])
        or evidence.get("owner_evidence_sha256") != _digest_bytes(_stable_json(grant))
        or not isinstance(grant.get("targets"), dict)
        or not grant["targets"]
        or type(grant.get("allow_default_branch")) is not bool
    ):
        raise TransactionError("task grant identity changed", "scope_changed")
    for ref, target in grant["targets"].items():
        if (
            not _valid_ref(root, ref, "refs/heads/")
            or not isinstance(target, dict)
            or set(target) != {"head", "kind"}
            or target["kind"] not in {"local", "remote", "new"}
            or not isinstance(target["head"], str)
            or not OBJECT_RE.fullmatch(target["head"])
        ):
            raise TransactionError("task target is invalid", "scope_changed")
    _validate_grant_scope(grant, root)
    if _task_closed_path(path).exists() or _task_closed_path(path).is_symlink():
        closed = _task_closed_path(path)
        if not _safe_private_file(closed, private_root):
            raise TransactionError("task closure is unsafe", "scope_changed")
        closure = _load_json(closed, "task closure")
        if (
            closure.get("schema") != "commit-transaction.task-closure.v1"
            or closure.get("grant_sha256") != evidence["owner_evidence_sha256"]
            or closure.get("status") not in {"completed", "cancelled", "superseded"}
        ):
            raise TransactionError("task closure changed", "scope_changed")
        if not allow_closed or closure["status"] == "superseded":
            raise TransactionError("task is closed", "task_closed")
    if validate_scope and grant["requested_action"] != "commit":
        if grant["origin_sha256"] != _pr_origin_digest(root) or grant[
            "default_ref"
        ] != _git_text(root, "symbolic-ref", "refs/remotes/origin/HEAD"):
            raise TransactionError(
                "task origin or default branch changed", "scope_changed"
            )
    if validate_scope and grant["base_ref"]:
        current_base = _git_text(
            root, "rev-parse", "--verify", f"{grant['base_ref']}^{{commit}}"
        )
        if _run_git(
            root,
            (
                "merge-base",
                "--is-ancestor",
                _task_state(path, grant)["base_head"],
                current_base,
            ),
            check=False,
        ).returncode:
            raise TransactionError("task base lineage changed", "scope_changed")
    if _managed_worktree_name(_primary_worktree(root), root) is not None:
        raise TransactionError(
            "managed Worktree children require delegated ownership", "scope_changed"
        )
    return path, grant


def _task_from_arguments(
    arguments: argparse.Namespace,
    root: Path,
    *,
    allow_closed=False,
    validate_scope=True,
):
    path = _task_path(root, arguments.session_id, arguments.intent_sha256)
    if not _safe_private_file(path, _transaction_root(_common_dir(root))):
        raise TransactionError(
            "begin the authorized task before preparing effects", "task_missing"
        )
    grant = _load_json(path, "task grant")
    evidence = {
        "owner_evidence_path": str(path),
        "owner_evidence_sha256": _digest_bytes(_stable_json(grant)),
    }
    path, grant = _validate_task(
        evidence,
        root,
        arguments.session_id,
        allow_closed=allow_closed,
        validate_scope=validate_scope,
    )
    if grant["requested_action"] != arguments.requested_action:
        raise TransactionError(
            "requested action differs from the task", "scope_changed"
        )
    return path, grant, evidence


def _root_recovery_candidate(root: Path, action: str, targets: dict[str, Any]):
    """A fresh explicit standalone task may adopt only exact interrupted evidence."""
    claim_path = expected_claim_path(root)
    if action == "create-pr" or not claim_path.exists():
        return None
    if not _safe_private_file(claim_path, _transaction_root(_common_dir(root))):
        raise TransactionError("recovery claim is unsafe", "scope_changed")
    claim = _load_json(claim_path, "recovery claim")
    if claim.get("state") not in {*EXECUTABLE_STATES, "REVIEW_REQUIRED"}:
        return None
    _validate_claim(claim, root, claim_path)
    if claim["authorization_owner"] != "direct":
        raise TransactionError(
            "another workflow owns the interrupted claim", "scope_changed"
        )
    old_path = Path(claim["owner_evidence_path"])
    if not _safe_private_file(old_path, _transaction_root(_common_dir(root))):
        raise TransactionError("recovery task is unsafe", "scope_changed")
    old_grant = _load_json(old_path, "recovery task")
    canonical = (
        _transaction_root(_common_dir(root))
        / "sessions"
        / claim["session_sha256"][:24]
        / "tasks"
        / f"{old_grant.get('receipt_sha256')}.json"
    )
    if (
        old_path != canonical
        or _task_closed_path(old_path).exists()
        or old_grant.get("schema") != TASK_SCHEMA
        or old_grant.get("requested_action") != action
        or old_grant.get("session_sha256") != claim["session_sha256"]
        or _digest_bytes(_stable_json(old_grant)) != claim["owner_evidence_sha256"]
        or set(old_grant["targets"]) != set(targets)
    ):
        raise TransactionError("interrupted task cannot be adopted", "scope_changed")
    old_state = _task_state(old_path, old_grant)["targets"][claim["ref"]]
    if (
        old_state["head"] != claim["base_head"]
        or old_state["commits"]
        or old_state["sync"]
    ):
        raise TransactionError(
            "interrupted task checkpoint disagrees", "effect_unknown"
        )
    head = _identity(root)["head"]
    if head == claim["base_head"]:
        candidate, _ = _preview_tree(root, claim_path.parent)
        unchanged = (
            _index_tree(root) == claim["initial_index_tree"]
            and _digest_bytes(_status(root)) == claim["initial_status_sha256"]
        )
        staged = _index_tree(root) == candidate and not _has_unstaged_or_untracked(root)
        if candidate != claim["candidate_tree"] or not (unchanged or staged):
            raise TransactionError("interrupted candidate changed", "candidate_changed")
    elif not _exact_direct_child(root, claim["base_head"], head):
        raise TransactionError(
            "interrupted result is not an exact direct child", "history_changed"
        )
    targets[claim["ref"]]["head"] = claim["base_head"]
    return claim


def _retire_recovery_predecessor(root: Path, grant: dict[str, Any]) -> None:
    if not grant["recovery_claim_sha256"]:
        return
    claim_path = expected_claim_path(root)
    if not _safe_private_file(claim_path, _transaction_root(_common_dir(root))):
        raise TransactionError("recovery predecessor is unavailable", "effect_unknown")
    claim = _load_json(claim_path, "recovery predecessor")
    if _digest_bytes(_stable_json(claim)) != grant["recovery_claim_sha256"]:
        # A successfully rebound claim already belongs to this task.
        if claim.get("owner_evidence_sha256") == _digest_bytes(_stable_json(grant)):
            return
        raise TransactionError("recovery predecessor changed", "effect_unknown")
    closed = _task_closed_path(Path(claim["owner_evidence_path"]))
    result = {
        "schema": "commit-transaction.task-closure.v1",
        "status": "superseded",
        "grant_sha256": claim["owner_evidence_sha256"],
        "successor_sha256": _digest_bytes(_stable_json(grant)),
    }
    if closed.exists():
        if (
            not _safe_private_file(closed, _transaction_root(_common_dir(root)))
            or _load_json(closed, "recovery closure") != result
        ):
            raise TransactionError(
                "recovery predecessor closure changed", "scope_changed"
            )
    else:
        _atomic_json(closed, result)


def _root_task_context(root: Path, path: Path, grant: dict[str, Any], session_id: str) -> dict[str, object]:
    claims = expected_claim_path(root).parent
    return {"status": "active", "task": str(path),
            "task_sha256": _digest_bytes(_stable_json(grant)),
            "authorization": str(expected_authorization_path(root, session_id)),
            "claims": {ref: str(claims / f"{_ref_key(ref)}.json") for ref in grant["targets"]}}


def begin(arguments: argparse.Namespace) -> dict[str, object]:
    root = _canonical_repo(arguments.repo_root)
    identity = _identity(root)
    with _repository_lock(Path(identity["common_dir"])):
        identity = _identity(root)
        path = _task_path(root, arguments.session_id, arguments.intent_sha256)
        if path.exists() or path.is_symlink():
            path, grant, _ = _task_from_arguments(arguments, root)
            selected = set(arguments.target or [identity["branch"]])
            if selected != {
                ref.removeprefix("refs/heads/") for ref in grant["targets"]
            }:
                raise TransactionError("task targets cannot change", "scope_changed")
            _task_state(path, grant)
            supplied_edges = sorted(arguments.dependency or [])
            saved_edges = sorted(
                f"{child.removeprefix('refs/heads/')}:{parent.removeprefix('refs/heads/')}"
                for child, parents in grant["dependencies"].items()
                for parent in parents
            )
            if (
                supplied_edges != saved_edges
                or bool(arguments.allow_default_branch) != grant["allow_default_branch"]
                or (
                    f"refs/remotes/origin/{arguments.pr_base}"
                    if arguments.pr_base
                    else None
                )
                != grant["base_ref"]
                or (
                    f"refs/heads/{arguments.validation_branch}"
                    if arguments.validation_branch
                    else None
                )
                != grant["validation_ref"]
            ):
                raise TransactionError("task scope cannot change", "scope_changed")
            _retire_recovery_predecessor(root, grant)
            return _root_task_context(root, path, grant, arguments.session_id)
        receipt_path = expected_authorization_path(
            root, arguments.session_id
        ).with_name("intent.json")
        if not _safe_private_file(receipt_path, _transaction_root(_common_dir(root))):
            raise TransactionError(
                "current root receipt is unavailable", "scope_changed"
            )
        receipt = _load_json(receipt_path, "root receipt")
        expected = {
            "schema": "commit-transaction.intent.v1",
            **{k: identity[k] for k in ("repo_root", "worktree", "common_dir", "ref")},
            "base_head": identity["head"],
            "session_sha256": _digest_text(arguments.session_id),
        }
        if (
            set(receipt) != {*expected, "turn_sha256", "prompt_sha256"}
            or any(receipt.get(k) != v for k, v in expected.items())
            or any(
                not isinstance(receipt.get(k), str)
                or not DIGEST_RE.fullmatch(receipt[k])
                for k in ("turn_sha256", "prompt_sha256")
            )
            or _digest_bytes(_stable_json(receipt)) != arguments.intent_sha256
        ):
            raise TransactionError(
                "root receipt does not match task intake", "scope_changed"
            )
        action = arguments.requested_action
        if action != "commit" and arguments.allow_default_branch:
            raise TransactionError(
                "publication tasks forbid default-branch commits", "scope_changed"
            )
        _safety_checks(
            root,
            allow_default_branch=action == "create-pr"
            or arguments.allow_default_branch,
        )
        if _managed_worktree_name(_primary_worktree(root), root) is not None:
            raise TransactionError(
                "managed Worktree children require delegated ownership", "scope_changed"
            )
        default_ref = (
            _git_text(root, "symbolic-ref", "refs/remotes/origin/HEAD")
            if action != "commit"
            else None
        )
        base_ref = (
            f"refs/remotes/origin/{arguments.pr_base}"
            if action == "create-pr" and arguments.pr_base
            else None
        )
        if action == "create-pr" and base_ref is None:
            raise TransactionError("PR task requires a base", "scope_changed")
        targets = {}
        for branch in arguments.target or [identity["branch"]]:
            ref = f"refs/heads/{branch}"
            if (
                not REF_RE.fullmatch(ref)
                or _run_git(root, ("check-ref-format", ref), check=False).returncode
            ):
                raise TransactionError("invalid task target", "scope_changed")
            if action != "create-pr" and ref != identity["ref"]:
                raise TransactionError(
                    "standalone task must use the current branch", "scope_changed"
                )
            if action != "commit" and branch in {
                default_ref.removeprefix("refs/remotes/origin/"),
                arguments.pr_base,
            }:
                raise TransactionError(
                    "publication requires non-default target branches", "scope_changed"
                )
            local = _run_git(
                root, ("rev-parse", "--verify", f"{ref}^{{commit}}"), check=False
            )
            remote = _run_git(
                root,
                ("rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}"),
                check=False,
            )
            if local.returncode == 0:
                targets[ref] = {"head": local.stdout.decode().strip(), "kind": "local"}
            elif remote.returncode == 0:
                targets[ref] = {
                    "head": remote.stdout.decode().strip(),
                    "kind": "remote",
                }
            elif action == "create-pr" and identity[
                "branch"
            ] == default_ref.removeprefix("refs/remotes/origin/"):
                targets[ref] = {"head": identity["head"], "kind": "new"}
            else:
                raise TransactionError("unknown target branch", "scope_changed")
        recovery = _root_recovery_candidate(root, action, targets)
        dependencies = {ref: [] for ref in targets}
        for edge in arguments.dependency or []:
            child, separator, parent = edge.partition(":")
            child, parent = f"refs/heads/{child}", f"refs/heads/{parent}"
            if (
                not separator
                or child not in targets
                or parent not in targets
                or child == parent
            ):
                raise TransactionError(
                    "dependency must join selected distinct targets", "scope_changed"
                )
            dependencies[child].append(parent)

        def visit(ref, chain):
            if ref in chain:
                raise TransactionError(
                    "task dependencies contain a cycle", "scope_changed"
                )
            for parent in dependencies[ref]:
                visit(parent, {*chain, ref})

        for ref in targets:
            dependencies[ref] = sorted(set(dependencies[ref]))
            visit(ref, set())
        validation_ref = (
            f"refs/heads/{arguments.validation_branch}"
            if arguments.validation_branch
            else None
        )
        if validation_ref and (
            action != "create-pr"
            or validation_ref in targets
            or not _valid_ref(root, validation_ref, "refs/heads/")
            or _run_git(
                root, ("show-ref", "--verify", "--quiet", validation_ref), check=False
            ).returncode
            == 0
        ):
            raise TransactionError(
                "validation ref must be a new non-publication ref", "scope_changed"
            )
        grant = {
            "schema": TASK_SCHEMA,
            **{k: identity[k] for k in ("repo_root", "worktree", "common_dir")},
            "session_sha256": _digest_text(arguments.session_id),
            "requested_action": action,
            "receipt_sha256": arguments.intent_sha256,
            "turn_sha256": receipt["turn_sha256"],
            "prompt_sha256": receipt["prompt_sha256"],
            "initial_ref": identity["ref"],
            "initial_head": identity["head"],
            "targets": targets,
            "dependencies": dependencies,
            "base_ref": base_ref,
            "base_head": _git_text(root, "rev-parse", f"{base_ref}^{{commit}}")
            if base_ref
            else None,
            "origin_sha256": _pr_origin_digest(root) if action != "commit" else None,
            "default_ref": default_ref,
            "allow_default_branch": bool(arguments.allow_default_branch),
            "validation_ref": validation_ref,
            "recovery_claim_sha256": _digest_bytes(_stable_json(recovery))
            if recovery
            else None,
        }
        # Checkpoint first: a crash before publishing the immutable grant grants no authority.
        _atomic_json(
            _task_state_path(path),
            {
                "schema": TASK_STATE_SCHEMA,
                "grant_sha256": _digest_bytes(_stable_json(grant)),
                "base_head": grant["base_head"],
                "targets": {
                    ref: {
                        "head": item["head"],
                        "commits": 0,
                        "last_claim": None,
                        "sync": None,
                    }
                    for ref, item in targets.items()
                },
            },
        )
        _atomic_json(path, grant)
        _retire_recovery_predecessor(root, grant)
        return _root_task_context(root, path, grant, arguments.session_id)


def _advance_task_commit(root: Path, claim: dict[str, Any]) -> None:
    if claim["authorization_owner"] not in ROOT_OWNERS:
        return
    path = Path(claim["owner_evidence_path"])
    grant = _load_json(path, "task grant")
    state = _task_state(path, grant)
    target = state["targets"][claim["ref"]]
    digest = _digest_bytes(_stable_json(claim))
    if target["last_claim"] == digest:
        return
    if target["head"] != claim["base_head"] or target["sync"] is not None:
        raise TransactionError(
            "commit predecessor differs from task checkpoint", "history_changed"
        )
    if not _exact_direct_child(root, claim["base_head"], claim["commit_head"]):
        raise TransactionError(
            "commit lacks exact direct-child proof", "history_changed"
        )
    target.update(
        head=claim["commit_head"], commits=target["commits"] + 1, last_claim=digest
    )
    _atomic_json(_task_state_path(path), state)


def _bind_direct_intent(
    arguments: argparse.Namespace, root: Path, path: Path, private_root: Path
) -> None:
    task_path, grant, evidence = _task_from_arguments(arguments, root)
    if path != expected_authorization_path(root, arguments.session_id):
        raise TransactionError("authorization path is not canonical", "scope_changed")
    if path.exists() or path.is_symlink():
        if not _safe_private_file(path, private_root):
            raise TransactionError("prior authorization is unsafe", "scope_changed")
        if _load_json(path, "prior authorization").get("owner") not in ROOT_OWNERS:
            raise TransactionError(
                "delegated authorization owns this session", "scope_changed"
            )
    identity = _identity(root)
    state = _task_state(task_path, grant)
    if identity["ref"] not in state["targets"]:
        raise TransactionError("branch is not a selected task target", "scope_changed")
    target = state["targets"][identity["ref"]]
    if target["sync"] is not None:
        raise TransactionError(
            "pending sync requires reconciliation or review", "review_required"
        )
    authorization = {
        "schema": ROOT_AUTH_SCHEMA,
        "state": "AUTHORIZED",
        **{k: identity[k] for k in ("repo_root", "worktree", "common_dir", "ref")},
        "base_head": identity["head"],
        "session_sha256": grant["session_sha256"],
        "turn_sha256": grant["turn_sha256"],
        "prompt_sha256": grant["prompt_sha256"],
        "owner": "create-pr" if grant["requested_action"] == "create-pr" else "direct",
        **evidence,
        "allow_default_branch": grant["allow_default_branch"],
    }
    claim_path = expected_claim_path(root)
    existing = None
    if claim_path.exists() or claim_path.is_symlink():
        if not _safe_private_file(claim_path, private_root):
            raise TransactionError("predecessor claim is unsafe", "scope_changed")
        existing = _load_json(claim_path, "predecessor claim")
        _validate_claim(existing, root, claim_path, historical=True)
        same_task = all(existing.get(k) == v for k, v in evidence.items())
        adopting = grant["recovery_claim_sha256"] == _digest_bytes(
            _stable_json(existing)
        )
        if adopting:
            old_closed = _task_closed_path(Path(existing["owner_evidence_path"]))
            if (
                not _safe_private_file(old_closed, private_root)
                or _load_json(old_closed, "recovery closure").get("successor_sha256")
                != evidence["owner_evidence_sha256"]
            ):
                raise TransactionError(
                    "recovery predecessor is not retired", "effect_unknown"
                )
        if (
            existing["state"] in {*EXECUTABLE_STATES, "REVIEW_REQUIRED"}
            and not same_task
            and not adopting
        ):
            raise TransactionError(
                "another task owns the active claim", "scope_changed"
            )
        if same_task:
            _validate_claim_authorization(existing, root, arguments.session_id)
            if existing["state"] == "COMMITTED":
                _advance_task_commit(root, existing)
                state = _task_state(task_path, grant)
                target = state["targets"][identity["ref"]]
            elif existing["state"] == "REVIEW_REQUIRED":
                raise TransactionError(
                    "actual commit requires review", "review_required"
                )
            elif existing["state"] == "STALE" and (
                existing["commit_head"] is not None
                or identity["head"] != existing["base_head"]
            ):
                raise TransactionError(
                    "failed attempt has an unknown effect", "effect_unknown"
                )
            elif existing["state"] in EXECUTABLE_STATES:
                # Preserve active reservations. execute classifies changed candidates before a retry.
                if identity["head"] == existing["base_head"]:
                    candidate, _ = _preview_tree(root, claim_path.parent)
                    if candidate != existing["candidate_tree"]:
                        _mark_claim(claim_path, existing, "STALE", "candidate_changed")
                        existing = {**existing, "state": "STALE"}
                elif not _exact_direct_child(
                    root, existing["base_head"], identity["head"]
                ):
                    raise TransactionError(
                        "pending commit has unexplained history", "history_changed"
                    )
    if grant["requested_action"] != "create-pr" and target["commits"]:
        raise TransactionError("task already created its one commit", "task_consumed")
    recovering = (
        existing is not None
        and (
            existing.get("owner_evidence_path") == str(task_path)
            or grant["recovery_claim_sha256"] == _digest_bytes(_stable_json(existing))
        )
        and existing["state"] in {*EXECUTABLE_STATES, "REVIEW_REQUIRED"}
        and _exact_direct_child(root, existing["base_head"], identity["head"])
    )
    if identity["head"] != target["head"] and not recovering:
        raise TransactionError("task target history changed", "history_changed")
    _atomic_json(path, authorization)


def finish(arguments: argparse.Namespace) -> dict[str, object]:
    root = _canonical_repo(arguments.repo_root)
    with _repository_lock(_common_dir(root)):
        path, grant, _ = _task_from_arguments(
            arguments,
            root,
            allow_closed=True,
            validate_scope=arguments.outcome != "cancelled",
        )
        closed = _task_closed_path(path)
        if closed.exists():
            if not _safe_private_file(closed, _transaction_root(_common_dir(root))):
                raise TransactionError("task closure is unsafe", "scope_changed")
            return _load_json(closed, "task closure")
        state = _task_state(path, grant)
        if arguments.outcome == "cancelled":
            # The repository lock also fences surviving Git children. Invalidate
            # unused reservations, while preserving uncertain or actual effects.
            for ref in state["targets"]:
                claim_path = expected_claim_path(root).parent / f"{_ref_key(ref)}.json"
                if not claim_path.exists():
                    continue
                if not _safe_private_file(
                    claim_path, _transaction_root(_common_dir(root))
                ):
                    raise TransactionError("target claim is unsafe", "scope_changed")
                claim = _load_json(claim_path, "target claim")
                current = _run_git(root, ("rev-parse", "--verify", ref), check=False)
                if (
                    claim.get("owner_evidence_path") == str(path)
                    and claim["state"] in EXECUTABLE_STATES
                    and claim["commit_head"] is None
                    and not current.returncode
                    and current.stdout.decode().strip() == claim["base_head"]
                ):
                    _mark_claim(claim_path, claim, "STALE", "task_cancelled_no_commit")
                elif (
                    claim.get("owner_evidence_path") == str(path)
                    and claim["state"] in EXECUTABLE_STATES
                    and not current.returncode
                    and _exact_direct_child(
                        root, claim["base_head"], current.stdout.decode().strip()
                    )
                ):
                    head = current.stdout.decode().strip()
                    _atomic_json(
                        claim_path,
                        {
                            **claim,
                            "state": "REVIEW_REQUIRED",
                            "commit_head": head,
                            "commit_tree": _git_text(
                                root, "rev-parse", f"{head}^{{tree}}"
                            ),
                            "failure": "cancelled task retains an actual commit for review",
                        },
                    )
        if arguments.outcome == "completed":
            validation_path = path.with_name(path.stem + ".validation.json")
            if validation_path.exists():
                if (
                    not _safe_private_file(
                        validation_path, _transaction_root(_common_dir(root))
                    )
                    or _load_json(validation_path, "validation record").get("phase")
                    != "done"
                ):
                    raise TransactionError(
                        "scratch validation is unfinished", "effect_unknown"
                    )
            if (
                grant["validation_ref"]
                and not _run_git(
                    root,
                    ("show-ref", "--verify", "--quiet", grant["validation_ref"]),
                    check=False,
                ).returncode
            ):
                raise TransactionError("scratch ref still exists", "effect_unknown")
            if _status(root):
                raise TransactionError(
                    "completion requires a clean checkout", "candidate_changed"
                )
            for ref, target in state["targets"].items():
                current = _run_git(root, ("rev-parse", "--verify", ref), check=False)
                if (
                    target["sync"] is not None
                    or current.returncode
                    or current.stdout.decode().strip() != target["head"]
                ):
                    raise TransactionError(
                        "target is not at its verified checkpoint", "effect_unknown"
                    )
                claim_path = expected_claim_path(root).parent / f"{_ref_key(ref)}.json"
                if claim_path.exists():
                    if not _safe_private_file(
                        claim_path, _transaction_root(_common_dir(root))
                    ):
                        raise TransactionError(
                            "target claim is unsafe", "scope_changed"
                        )
                    claim = _load_json(claim_path, "target claim")
                    if claim.get("owner_evidence_path") == str(path) and claim[
                        "state"
                    ] not in {"COMMITTED", "STALE"}:
                        raise TransactionError(
                            "target has an unresolved claim", "effect_unknown"
                        )
        result = {
            "schema": "commit-transaction.task-closure.v1",
            "status": arguments.outcome,
            "grant_sha256": _digest_bytes(_stable_json(grant)),
            "heads": {ref: item["head"] for ref, item in state["targets"].items()},
        }
        _atomic_json(closed, result)
        return result


def sync(arguments: argparse.Namespace) -> dict[str, object]:
    root = _canonical_repo(arguments.repo_root)
    with _repository_lock(_common_dir(root)):
        path, grant, _ = _task_from_arguments(arguments, root)
        if grant["requested_action"] != "create-pr":
            raise TransactionError(
                "only PR tasks may synchronize history", "scope_changed"
            )
        state = _task_state(path, grant)
        identity = _identity(root)
        target = state["targets"].get(identity["ref"])
        if target is None:
            raise TransactionError("sync target is not selected", "scope_changed")
        if _active_worktree_claims(_primary_worktree(root), identity["ref"]):
            raise TransactionError("Worktree owns this source ref", "scope_changed")
        pending = target["sync"]
        if pending is None:
            if arguments.continue_sync or arguments.reviewed_tree:
                raise TransactionError("no pending sync", "scope_changed")
            _safety_checks(root, allow_default_branch=False)
            if _status(root) or identity["head"] != target["head"]:
                raise TransactionError(
                    "sync requires the clean verified head", "history_changed"
                )
            claim_path = expected_claim_path(root)
            if claim_path.exists():
                if not _safe_private_file(
                    claim_path, _transaction_root(_common_dir(root))
                ):
                    raise TransactionError(
                        "sync predecessor claim is unsafe", "scope_changed"
                    )
                previous = _load_json(claim_path, "sync predecessor claim")
                if previous["state"] not in {"COMMITTED", "STALE"}:
                    raise TransactionError(
                        "sync cannot bypass an active commit claim", "review_required"
                    )
                _validate_claim(previous, root, claim_path, historical=True)
                if previous["state"] == "STALE":
                    if previous["commit_head"] is not None or previous["base_head"] != identity["head"]:
                        raise TransactionError("stale predecessor lacks no-effect proof", "effect_unknown")
                    _archive_terminal_claim(root, claim_path, previous)
                    claim_path.unlink()
                    _fsync_directory(claim_path.parent)
            source_ref = grant["base_ref"]
            if arguments.dependency:
                source_ref = f"refs/heads/{arguments.dependency}"
                if source_ref not in grant["dependencies"][identity["ref"]]:
                    raise TransactionError(
                        "dependency was not declared", "scope_changed"
                    )
                source = state["targets"][source_ref]
                if source["sync"] is not None:
                    raise TransactionError(
                        "dependency is not verified", "review_required"
                    )
                source_head = source["head"]
                if _git_text(root, "rev-parse", source_ref) != source_head:
                    raise TransactionError("dependency head changed", "history_changed")
            else:
                source_head = _git_text(root, "rev-parse", f"{source_ref}^{{commit}}")
                state["base_head"] = source_head
            pending = {
                "from_head": identity["head"],
                "source_head": source_head,
                "source_ref": source_ref,
                "result_head": None,
                "result_tree": None,
            }
            target["sync"] = pending
            _atomic_json(_task_state_path(path), state)
        else:
            requested_source = (
                f"refs/heads/{arguments.dependency}"
                if arguments.dependency
                else grant["base_ref"]
            )
            if requested_source != pending["source_ref"]:
                raise TransactionError(
                    "pending sync source cannot change", "scope_changed"
                )
            if set(pending) != {
                "from_head",
                "source_head",
                "source_ref",
                "result_head",
                "result_tree",
            }:
                raise TransactionError(
                    "pending sync evidence is invalid", "effect_unknown"
                )
            merge_head = _run_git(
                root, ("rev-parse", "--verify", "MERGE_HEAD"), check=False
            )
            if merge_head.returncode == 0:
                if (
                    not arguments.continue_sync
                    or _identity(root)["head"] != pending["from_head"]
                    or merge_head.stdout.decode().strip() != pending["source_head"]
                ):
                    raise TransactionError(
                        "pending merge requires exact reviewed continuation",
                        "review_required",
                    )
                candidate, _ = _preview_tree(root, path.parent)
                if arguments.reviewed_tree is None:
                    return {"status": "candidate-review-required", "candidate_tree": candidate,
                            "next_action": "review this candidate and rerun sync --continue with --reviewed-tree"}
                if candidate != arguments.reviewed_tree:
                    raise TransactionError(
                        "merge candidate differs from reviewed tree",
                        "candidate_changed",
                    )
                _run_git(root, ("add", "-A"))
                if (
                    _index_tree(root) != candidate
                    or _run_git(
                        root, ("diff", "--cached", "--check"), check=False
                    ).returncode
                ):
                    raise TransactionError(
                        "merge candidate failed staged checks", "candidate_changed"
                    )
                _run_git(root, ("commit", "--no-edit"), check=False)
        # Retry only a proven not-started merge. The inherited lock ensures a
        # child from an interrupted invocation has exited before this decision.
        if (
            pending["result_head"] is None
            and _identity(root)["head"] == pending["from_head"]
            and not _status(root)
            and _run_git(
                root, ("rev-parse", "--verify", "MERGE_HEAD"), check=False
            ).returncode
        ):
            _safety_checks(root, allow_default_branch=False)
            _run_git(
                root,
                (
                    "merge",
                    "--no-overwrite-ignore",
                    "--no-autostash",
                    "--no-edit",
                    pending["source_head"],
                ),
                check=False,
            )
        current = _identity(root)["head"]
        parents = _git_text(root, "rev-list", "--parents", "-n", "1", current).split()
        noop = (
            current == pending["from_head"]
            and not _run_git(
                root,
                ("merge-base", "--is-ancestor", pending["source_head"], current),
                check=False,
            ).returncode
        )
        fast_forward = (
            current == pending["source_head"]
            and not _run_git(
                root,
                ("merge-base", "--is-ancestor", pending["from_head"], current),
                check=False,
            ).returncode
        )
        merged = parents == [current, pending["from_head"], pending["source_head"]]
        if (
            _run_git(
                root, ("rev-parse", "--verify", "MERGE_HEAD"), check=False
            ).returncode
            == 0
        ):
            raise TransactionError(
                "merge conflict retained for reviewed continuation", "review_required"
            )
        if not (noop or fast_forward or merged) or _status(root):
            raise TransactionError(
                "sync outcome needs reconciliation", "effect_unknown"
            )
        tree = _git_text(root, "rev-parse", "HEAD^{tree}")
        if pending["result_head"] is not None and (
            pending["result_head"] != current or pending["result_tree"] != tree
        ):
            raise TransactionError(
                "sync result changed before review", "history_changed"
            )
        pending.update(result_head=current, result_tree=tree)
        _atomic_json(_task_state_path(path), state)
        if arguments.reviewed_tree:
            if arguments.reviewed_tree != tree:
                raise TransactionError(
                    "actual merge result needs review", "review_required"
                )
            # Keep transition evidence before advancing the checkpoint.
            history = (
                path.parent
                / (path.stem + ".sync-history")
                / f"{_digest_bytes(_stable_json(pending))}.json"
            )
            _atomic_json(history, pending)
            target.update(head=current, sync=None)
            _atomic_json(_task_state_path(path), state)
            return {"status": "synchronized", "commit": current, "tree": tree}
        return {
            "status": "review-required",
            "commit": current,
            "tree": tree,
            "next_action": "review the result and rerun sync with --reviewed-tree",
        }


def _conflict_fingerprint(root: Path) -> str:
    paths = set(
        _run_git(root, ("diff", "--name-only", "-z", "HEAD")).stdout.split(b"\0")
    )
    paths.update(
        _run_git(
            root, ("ls-files", "--others", "--exclude-standard", "-z")
        ).stdout.split(b"\0")
    )
    values = {
        "index": _digest_bytes(_index_path(root).read_bytes()),
        "status": _digest_bytes(_status(root)),
    }
    for raw in sorted(paths - {b""}):
        path = root / os.fsdecode(raw)
        if path.is_symlink():
            value = b"symlink:" + os.fsencode(os.readlink(path))
        elif path.is_file():
            value = (
                b"file:" + str(path.stat().st_mode).encode() + b":" + path.read_bytes()
            )
        elif not path.exists():
            value = b"absent"
        else:
            raise TransactionError(
                "scratch conflict includes unsupported path state", "effect_unknown"
            )
        values[_digest_bytes(raw)] = _digest_bytes(value)
    return _digest_bytes(_stable_json(values))


def _validate_order_record(record, grant, order):
    keys = {
        "schema",
        "grant_sha256",
        "ref",
        "order",
        "heads",
        "base_head",
        "return_ref",
        "return_head",
        "head",
        "index",
        "pending",
        "phase",
        "outcome",
        "conflict_sha256",
    }
    if (
        set(record) != keys
        or record["schema"] != "commit-transaction.validation.v1"
        or record["grant_sha256"] != _digest_bytes(_stable_json(grant))
        or record["ref"] != grant["validation_ref"]
        or record["order"] != order
        or not isinstance(record["heads"], dict)
        or set(record["heads"]) != set(grant["targets"])
        or any(not _valid_object(head) for head in record["heads"].values())
        or record["return_ref"] not in grant["targets"]
        or record["return_head"] != record["heads"][record["return_ref"]]
        or any(
            not _valid_object(record[key])
            for key in ("base_head", "return_head", "head")
        )
        or type(record["index"]) is not int
        or not 0 <= record["index"] <= len(order)
        or (
            record["pending"] is not None
            and (
                record["index"] == len(order)
                or record["pending"] != record["heads"][order[record["index"]]]
            )
        )
        or record["phase"] not in {"starting", "merging", "cleanup", "done"}
        or record["outcome"] not in {None, "validated", "conflict"}
        or (
            record["conflict_sha256"] is not None
            and not _valid_digest(record["conflict_sha256"])
        )
    ):
        raise TransactionError("validation record shape is invalid", "effect_unknown")


def validate_order(arguments: argparse.Namespace) -> dict[str, object]:
    """Simulate a declared order on one task-owned ref, then remove that exact ref."""
    root = _canonical_repo(arguments.repo_root)
    with _repository_lock(_common_dir(root)):
        path, grant, evidence = _task_from_arguments(arguments, root)
        ref = grant["validation_ref"]
        order = [f"refs/heads/{branch}" for branch in arguments.branch]
        if (
            grant["requested_action"] != "create-pr"
            or not ref
            or len(order) != len(set(order))
            or set(order) != set(grant["targets"])
        ):
            raise TransactionError(
                "validation requires the declared scratch ref and every target once",
                "scope_changed",
            )
        for index, branch in enumerate(order):
            if any(
                order.index(parent) >= index for parent in grant["dependencies"][branch]
            ):
                raise TransactionError(
                    "validation order violates declared dependencies", "scope_changed"
                )
            if _active_worktree_claims(_primary_worktree(root), branch):
                raise TransactionError(
                    "Worktree owns a validation source ref", "scope_changed"
                )
        state = _task_state(path, grant)
        record_path = path.with_name(path.stem + ".validation.json")
        record = None
        if record_path.exists() or record_path.is_symlink():
            if not _safe_private_file(
                record_path, _transaction_root(_common_dir(root))
            ):
                raise TransactionError("validation record is unsafe", "scope_changed")
            record = _load_json(record_path, "validation record")
            _validate_order_record(record, grant, order)
        heads = {branch: item["head"] for branch, item in state["targets"].items()}
        base = _git_text(root, "rev-parse", f"{grant['base_ref']}^{{commit}}")
        for branch, item in state["targets"].items():
            if (
                item["sync"] is not None
                or _git_text(root, "rev-parse", branch) != item["head"]
            ):
                raise TransactionError(
                    "validation source is not verified", "history_changed"
                )
            claim_path = expected_claim_path(root).parent / f"{_ref_key(branch)}.json"
            if claim_path.exists():
                if not _safe_private_file(
                    claim_path, _transaction_root(_common_dir(root))
                ):
                    raise TransactionError(
                        "validation source claim is unsafe", "scope_changed"
                    )
                if _load_json(claim_path, "validation source claim")["state"] not in {
                    "COMMITTED",
                    "STALE",
                }:
                    raise TransactionError(
                        "validation source has an unresolved claim", "review_required"
                    )
        if record and record["phase"] == "done":
            if not _run_git(
                root, ("show-ref", "--verify", "--quiet", ref), check=False
            ).returncode:
                raise TransactionError(
                    "completed scratch ref was recreated", "history_changed"
                )
            if record["heads"] == heads and record["base_head"] == base:
                return {
                    "status": record["outcome"],
                    "heads": heads,
                    "scratch_removed": True,
                }
            history = (
                record_path.parent
                / (path.stem + ".validation-history")
                / f"{_digest_bytes(_stable_json(record))}.json"
            )
            _atomic_json(history, record)
            record = None
        if record is None:
            identity = _identity(root)
            _safety_checks(root, allow_default_branch=False)
            if (
                _status(root)
                or identity["ref"] not in heads
                or identity["head"] != heads[identity["ref"]]
            ):
                raise TransactionError(
                    "validation requires a clean selected target", "history_changed"
                )
            if not _run_git(
                root, ("show-ref", "--verify", "--quiet", ref), check=False
            ).returncode:
                raise TransactionError("scratch ref already exists", "scope_changed")
            state["base_head"] = base
            _atomic_json(_task_state_path(path), state)
            record = {
                "schema": "commit-transaction.validation.v1",
                "grant_sha256": evidence["owner_evidence_sha256"],
                "ref": ref,
                "order": order,
                "heads": heads,
                "base_head": base,
                "return_ref": identity["ref"],
                "return_head": identity["head"],
                "head": base,
                "index": 0,
                "pending": None,
                "phase": "starting",
                "outcome": None,
                "conflict_sha256": None,
            }
            _atomic_json(record_path, record)
        elif record["heads"] != heads:
            raise TransactionError(
                "validation source checkpoints changed during simulation",
                "history_changed",
            )

        def save():
            _atomic_json(record_path, record)

        if record["phase"] == "starting":
            if _status(root) or _identity(root)["ref"] not in {
                record["return_ref"],
                ref,
            }:
                raise TransactionError("scratch checkout changed", "history_changed")
            existing = _run_git(root, ("rev-parse", "--verify", ref), check=False)
            if existing.returncode:
                _run_git(
                    root,
                    (
                        "update-ref",
                        ref,
                        record["base_head"],
                        "0" * len(record["base_head"]),
                    ),
                )
            elif existing.stdout.decode().strip() != record["base_head"]:
                raise TransactionError("scratch ref changed", "history_changed")
            _run_git(
                root,
                ("switch", "--no-overwrite-ignore", ref.removeprefix("refs/heads/")),
            )
            record["phase"] = "merging"
            save()
        while record["phase"] == "merging" and record["index"] < len(order):
            if _identity(root)["ref"] != ref:
                raise TransactionError("scratch branch changed", "history_changed")
            source = record["heads"][order[record["index"]]]
            if record["pending"] is None:
                if _status(root) or _identity(root)["head"] != record["head"]:
                    raise TransactionError(
                        "scratch predecessor changed", "history_changed"
                    )
                record["pending"] = source
                save()
            elif record["pending"] != source:
                raise TransactionError("scratch source changed", "scope_changed")
            merge = _run_git(root, ("rev-parse", "--verify", "MERGE_HEAD"), check=False)
            own_merge = False
            if (
                merge.returncode
                and not _status(root)
                and _identity(root)["head"] == record["head"]
            ):
                _safety_checks(root, allow_default_branch=False)
                _run_git(
                    root,
                    (
                        "merge",
                        "--no-overwrite-ignore",
                        "--no-autostash",
                        "--no-edit",
                        source,
                    ),
                    check=False,
                )
                own_merge = True
                merge = _run_git(
                    root, ("rev-parse", "--verify", "MERGE_HEAD"), check=False
                )
            if not merge.returncode:
                if (
                    merge.stdout.decode().strip() != source
                    or _identity(root)["head"] != record["head"]
                ):
                    raise TransactionError(
                        "scratch merge ownership changed", "history_changed"
                    )
                if not own_merge:
                    raise TransactionError(
                        "unrecorded scratch conflict requires owner reconciliation",
                        "effect_unknown",
                    )
                record.update(
                    phase="cleanup",
                    outcome="conflict",
                    conflict_sha256=_conflict_fingerprint(root),
                )
                save()
                break
            current = _identity(root)["head"]
            parents = _git_text(
                root, "rev-list", "--parents", "-n", "1", current
            ).split()
            noop = (
                current == record["head"]
                and not _run_git(
                    root, ("merge-base", "--is-ancestor", source, current), check=False
                ).returncode
            )
            forward = (
                current == source
                and not _run_git(
                    root,
                    ("merge-base", "--is-ancestor", record["head"], current),
                    check=False,
                ).returncode
            )
            if _status(root) or not (
                noop or forward or parents == [current, record["head"], source]
            ):
                raise TransactionError(
                    "scratch merge outcome is uncertain", "effect_unknown"
                )
            record.update(head=current, index=record["index"] + 1, pending=None)
            save()
        if record["phase"] == "merging":
            record.update(phase="cleanup", outcome="validated")
            save()
        if record["phase"] == "cleanup":
            identity = _identity(root)
            if identity["ref"] == ref:
                if identity["head"] != record["head"]:
                    raise TransactionError(
                        "scratch cleanup head changed", "history_changed"
                    )
                merge = _run_git(
                    root, ("rev-parse", "--verify", "MERGE_HEAD"), check=False
                )
                if not merge.returncode:
                    if (
                        record["outcome"] != "conflict"
                        or merge.stdout.decode().strip() != record["pending"]
                    ):
                        raise TransactionError(
                            "scratch cleanup merge changed", "history_changed"
                        )
                    if record["conflict_sha256"] != _conflict_fingerprint(root):
                        raise TransactionError(
                            "scratch conflict changed; preserve intervening work",
                            "history_changed",
                        )
                    _run_git(root, ("merge", "--abort"))
                if _status(root):
                    raise TransactionError(
                        "scratch cleanup retains unexpected changes", "effect_unknown"
                    )
                if (
                    _git_text(root, "rev-parse", record["return_ref"])
                    != record["return_head"]
                ):
                    raise TransactionError("return branch changed", "history_changed")
                _run_git(
                    root,
                    (
                        "switch",
                        "--no-overwrite-ignore",
                        record["return_ref"].removeprefix("refs/heads/"),
                    ),
                )
            if (
                _identity(root)["ref"] != record["return_ref"]
                or _identity(root)["head"] != record["return_head"]
                or _status(root)
            ):
                raise TransactionError("return checkout changed", "history_changed")
            if not _run_git(
                root, ("show-ref", "--verify", "--quiet", ref), check=False
            ).returncode:
                _run_git(root, ("update-ref", "-d", ref, record["head"]))
            record["phase"] = "done"
            save()
        if record["phase"] != "done":
            raise TransactionError("validation phase is invalid", "effect_unknown")
        return {"status": record["outcome"], "heads": heads, "scratch_removed": True}


def prepare(arguments: argparse.Namespace) -> dict[str, object]:
    root = _canonical_repo(arguments.repo_root)
    authorization_path = Path(arguments.authorization).resolve(strict=False)
    claim_path = Path(arguments.claim).resolve(strict=False)
    identity = _identity(root)
    with _repository_lock(Path(identity["common_dir"])):
        identity = _identity(root)
        private_root = _transaction_root(Path(identity["common_dir"]))
        direct_assertion = bool(
            getattr(arguments, "requested_action", None)
            or getattr(arguments, "intent_sha256", None)
        )
        if direct_assertion:
            _bind_direct_intent(arguments, root, authorization_path, private_root)
        if not _safe_private_file(authorization_path, private_root):
            raise TransactionError("commit authorization path is unsafe")
        authorization = _load_json(authorization_path, "commit authorization")
        if (
            authorization.get("owner") in {"direct", "create-pr"}
            and not direct_assertion
        ):
            raise TransactionError(
                "direct preparation requires a receipt-bound requested action"
            )
        _validate_authorization(
            authorization, root, arguments.session_id, authorization_path
        )
        _persist_attempt_authorization(authorization, root)
        primary = _primary_worktree(root)
        managed_name = _managed_worktree_name(primary, root)
        if (
            authorization["owner"] in {"direct", "create-pr"}
            and managed_name is not None
        ):
            raise TransactionError(
                "managed Worktree children require the exact delegated integration flow: "
                + managed_name
            )
        if (
            bool(arguments.allow_default_branch)
            != authorization["allow_default_branch"]
        ):
            raise TransactionError(
                "default-branch execution does not match explicit $commit authorization"
            )
        _safety_checks(root, allow_default_branch=arguments.allow_default_branch)
        if claim_path.exists():
            if not _safe_private_file(claim_path, private_root):
                raise TransactionError("commit claim path is unsafe")
            existing = _load_json(claim_path, "commit claim")
            _validate_claim(existing, root, claim_path, historical=True)
        else:
            existing = None
        if existing is not None:
            if authorization["owner"] in ROOT_OWNERS:
                _, task_grant = _validate_task(
                    authorization, root, arguments.session_id
                )
                if task_grant["recovery_claim_sha256"] == _digest_bytes(
                    _stable_json(existing)
                ):
                    existing = {
                        **existing,
                        "session_sha256": authorization["session_sha256"],
                        "turn_sha256": authorization["turn_sha256"],
                        "authorization_sha256": _digest_bytes(
                            _stable_json(authorization)
                        ),
                        "owner_evidence_path": authorization["owner_evidence_path"],
                        "owner_evidence_sha256": authorization["owner_evidence_sha256"],
                    }
            recovered = _recover_post_commit_for_prepare(
                root,
                claim_path,
                existing,
                authorization_path,
                authorization,
                arguments.session_id,
            )
            if recovered is not None:
                return recovered
        status = _status(root)
        initial_index_tree = _index_tree(root)
        candidate_tree, candidate_index_sha256 = _preview_tree(root, claim_path.parent)
        if candidate_tree == _git_text(root, "rev-parse", "HEAD^{tree}"):
            raise TransactionError("nothing to commit")
        claim_token = secrets.token_hex(32)
        if existing is not None:
            _validate_claim(existing, root, claim_path, historical=True)
            identity_immutable = {
                "repo_root": identity["repo_root"],
                "worktree": identity["worktree"],
                "common_dir": identity["common_dir"],
                "ref": identity["ref"],
                "branch": identity["branch"],
                "base_head": identity["head"],
            }
            preparation_immutable = {
                "initial_index_tree": initial_index_tree,
                "initial_status_sha256": _digest_bytes(status),
                "candidate_tree": candidate_tree,
                "candidate_index_sha256": candidate_index_sha256,
            }
            if existing.get("state") in EXECUTABLE_STATES:
                identity_matches = all(
                    existing.get(key) == value
                    for key, value in identity_immutable.items()
                )
                preparation_matches = all(
                    existing.get(key) == value
                    for key, value in preparation_immutable.items()
                )
                exact_staged_recovery = (
                    existing.get("candidate_tree") == candidate_tree
                    and existing.get("candidate_index_sha256") == candidate_index_sha256
                    and initial_index_tree == candidate_tree
                    and not _has_unstaged_or_untracked(root)
                )
                if not identity_matches or not (
                    preparation_matches or exact_staged_recovery
                ):
                    raise TransactionError(
                        "an existing commit transaction is still active"
                    )
                claim = {
                    **existing,
                    "state": (
                        "STAGED"
                        if existing["state"] == "STAGED" or not preparation_matches
                        else existing["state"]
                    ),
                    "session_sha256": _digest_text(arguments.session_id),
                    "turn_sha256": authorization["turn_sha256"],
                    "authorization_sha256": _digest_bytes(_stable_json(authorization)),
                    "authorization_owner": authorization["owner"],
                    "owner_evidence_path": authorization["owner_evidence_path"],
                    "owner_evidence_sha256": authorization["owner_evidence_sha256"],
                    "token_sha256": _digest_text(claim_token),
                    "failure": None,
                }
            else:
                _archive_terminal_claim(root, claim_path, existing)
                claim = None
        else:
            claim = None
        if claim is None:
            claim = {
                "schema": ROOT_CLAIM_SCHEMA
                if authorization["owner"] in ROOT_OWNERS
                else CLAIM_SCHEMA,
                "state": "PREPARED",
                "repo_root": identity["repo_root"],
                "worktree": identity["worktree"],
                "common_dir": identity["common_dir"],
                "ref": identity["ref"],
                "branch": identity["branch"],
                "base_head": identity["head"],
                "initial_index_tree": initial_index_tree,
                "initial_status_sha256": _digest_bytes(status),
                "candidate_tree": candidate_tree,
                "candidate_index_sha256": candidate_index_sha256,
                "session_sha256": _digest_text(arguments.session_id),
                "turn_sha256": authorization["turn_sha256"],
                "authorization_sha256": _digest_bytes(_stable_json(authorization)),
                "authorization_owner": authorization["owner"],
                "owner_evidence_path": authorization["owner_evidence_path"],
                "owner_evidence_sha256": authorization["owner_evidence_sha256"],
                "token_sha256": _digest_text(claim_token),
                "allow_default_branch": bool(arguments.allow_default_branch),
                "commit_head": None,
                "commit_tree": None,
                "failure": None,
            }
        _validate_claim(claim, root, claim_path)
        _atomic_json(claim_path, claim)
        if authorization["owner"] in {"direct", "create-pr"}:
            _atomic_json(
                authorization_path,
                {
                    **authorization,
                    "state": "CONSUMED",
                },
            )
        return {
            "status": "prepared",
            "branch": identity["branch"],
            "base_head": identity["head"],
            "candidate_tree": candidate_tree,
            "claim": str(claim_path),
            "token": claim_token,
        }


def _mark_claim(
    path: Path, claim: dict[str, Any], state: str, reason: str | None
) -> None:
    _atomic_json(path, {**claim, "state": state, "failure": reason})


def _reconcile_committed(
    root: Path, claim: dict[str, Any], claim_path: Path
) -> dict[str, object] | None:
    head = _git_text(root, "rev-parse", "HEAD")
    if head == claim["base_head"]:
        return None
    tree = _git_text(root, "rev-parse", "HEAD^{tree}")
    if not _exact_direct_child(root, claim["base_head"], head):
        _mark_claim(
            claim_path,
            claim,
            "STALE",
            "HEAD is not the transaction's exact direct child",
        )
        raise TransactionError(
            "repository history moved outside the transaction; request a fresh commit after resolving the blocker"
        )
    if tree == claim["candidate_tree"] and not _status(root):
        committed = {
            **claim,
            "state": "COMMITTED",
            "commit_head": head,
            "commit_tree": tree,
            "failure": None,
        }
        _atomic_json(claim_path, committed)
        _advance_task_commit(root, committed)
        return {
            "status": "committed",
            "branch": claim["branch"],
            "commit": head,
            "tree": tree,
        }
    _atomic_json(
        claim_path,
        {
            **claim,
            "state": "REVIEW_REQUIRED",
            "commit_head": head,
            "commit_tree": tree,
            "failure": "HEAD moved without exact direct-child tree proof",
        },
    )
    raise TransactionError("repository moved after preparation; review is required")


def execute(arguments: argparse.Namespace) -> dict[str, object]:
    root = _canonical_repo(arguments.repo_root)
    claim_path = Path(arguments.claim).resolve(strict=False)
    identity = _identity(root)
    with _repository_lock(Path(identity["common_dir"])):
        identity = _identity(root)
        if not _safe_private_file(
            claim_path, _transaction_root(Path(identity["common_dir"]))
        ):
            raise TransactionError("commit claim path is unsafe")
        claim = _load_json(claim_path, "commit claim")
        _validate_claim(claim, root, claim_path)
        if not secrets.compare_digest(
            claim["token_sha256"], _digest_text(arguments.token)
        ):
            raise TransactionError("commit claim token does not match")
        if claim["session_sha256"] != _digest_text(arguments.session_id):
            raise TransactionError("commit claim is bound to another session")
        _validate_claim_authorization(claim, root, arguments.session_id)
        if claim["candidate_tree"] != arguments.reviewed_tree:
            raise TransactionError(
                "reviewed tree does not match the prepared candidate"
            )
        if claim["state"] == "COMMITTED":
            _advance_task_commit(root, claim)
            return {
                "status": "committed",
                "branch": claim["branch"],
                "commit": claim["commit_head"],
                "tree": claim["commit_tree"],
            }
        if claim["state"] not in EXECUTABLE_STATES:
            raise TransactionError(f"commit claim is not executable: {claim['state']}")
        _validate_claim_owner(
            claim,
            root,
            arguments.session_id,
            allow_exact_direct_child=True,
        )
        reconciled = _reconcile_committed(root, claim, claim_path)
        if reconciled is not None:
            return reconciled
        current = _identity(root)
        current_index_tree = _index_tree(root)
        current_status_sha256 = _digest_bytes(_status(root))
        staged_recovery = claim["state"] == "STAGED" or (
            current_index_tree == claim["candidate_tree"]
            and current_status_sha256 != claim["initial_status_sha256"]
        )
        if staged_recovery:
            if current_index_tree != claim[
                "candidate_tree"
            ] or _has_unstaged_or_untracked(root):
                _mark_claim(
                    claim_path,
                    claim,
                    "STALE",
                    "staged recovery has unreviewed remainder",
                )
                raise TransactionError(
                    "staged recovery differs from the reviewed candidate"
                )
            claim = {**claim, "state": "STAGED", "failure": None}
            _atomic_json(claim_path, claim)
        identity_immutable = {
            "repo_root": current["repo_root"],
            "worktree": current["worktree"],
            "common_dir": current["common_dir"],
            "ref": current["ref"],
            "branch": current["branch"],
            "base_head": current["head"],
        }
        initial_immutable = {
            "initial_index_tree": current_index_tree,
            "initial_status_sha256": current_status_sha256,
        }
        if any(
            claim.get(key) != value for key, value in identity_immutable.items()
        ) or (
            not staged_recovery
            and any(claim.get(key) != value for key, value in initial_immutable.items())
        ):
            _mark_claim(
                claim_path,
                claim,
                "STALE",
                "repository identity, index, or status changed",
            )
            raise TransactionError(
                "commit candidate or repository changed", "candidate_changed"
            )
        candidate_tree, candidate_index_sha256 = _preview_tree(root, claim_path.parent)
        if (
            candidate_tree != claim["candidate_tree"]
            or candidate_index_sha256 != claim["candidate_index_sha256"]
        ):
            _mark_claim(claim_path, claim, "STALE", "candidate tree changed")
            raise TransactionError("commit candidate changed", "candidate_changed")
        conflicts = _active_worktree_claims(_primary_worktree(root), claim["ref"])
        if conflicts:
            raise TransactionError(
                "Worktree owns this source ref: " + ", ".join(conflicts)
            )
        _safety_checks(root, allow_default_branch=claim["allow_default_branch"])
        _run_git(root, ("add", "-A"))
        staged_tree = _index_tree(root)
        if staged_tree != claim["candidate_tree"]:
            _mark_claim(
                claim_path,
                claim,
                "STALE",
                "real staged tree differs from candidate",
            )
            raise TransactionError(
                "real staged tree differs from the reviewed candidate"
            )
        check = _run_git(root, ("diff", "--cached", "--check"), check=False)
        if check.returncode != 0:
            _mark_claim(claim_path, claim, "STALE", "staged diff validation failed")
            raise TransactionError("staged diff failed git diff --cached --check")
        claim = {**claim, "state": "STAGED", "failure": None}
        _atomic_json(claim_path, claim)
        committed = _run_git(root, ("commit", "-m", arguments.message), check=False)
        if committed.returncode != 0:
            reconciled = _reconcile_committed(root, claim, claim_path)
            if reconciled is not None:
                return reconciled
            _mark_claim(claim_path, claim, "STALE", "normal-hook git commit failed")
            raise TransactionError(
                "normal-hook git commit failed without a commit", "no_commit_failure"
            )
        result = _reconcile_committed(root, claim, claim_path)
        if result is None:  # pragma: no cover - commit success must move HEAD
            _mark_claim(claim_path, claim, "STALE", "git commit did not move HEAD")
            raise TransactionError("git commit did not move HEAD")
        return result


def review(arguments: argparse.Namespace) -> dict[str, object]:
    """Acknowledge one exact hook-modified direct-child commit after review."""
    root = _canonical_repo(arguments.repo_root)
    claim_path = Path(arguments.claim).resolve(strict=False)
    identity = _identity(root)
    with _repository_lock(Path(identity["common_dir"])):
        identity = _identity(root)
        if not _safe_private_file(
            claim_path, _transaction_root(Path(identity["common_dir"]))
        ):
            raise TransactionError("commit claim path is unsafe")
        claim = _load_json(claim_path, "commit claim")
        _validate_claim(claim, root, claim_path)
        if not secrets.compare_digest(
            claim["token_sha256"], _digest_text(arguments.token)
        ):
            raise TransactionError("commit claim token does not match")
        if claim["session_sha256"] != _digest_text(arguments.session_id):
            raise TransactionError("commit claim is bound to another session")
        _validate_claim_authorization(claim, root, arguments.session_id)
        if claim["state"] == "COMMITTED":
            if (
                claim["commit_head"] != arguments.reviewed_commit
                or claim["commit_tree"] != arguments.reviewed_tree
            ):
                raise TransactionError(
                    "reviewed commit does not match the completed claim"
                )
            return {
                "status": "committed",
                "branch": claim["branch"],
                "commit": claim["commit_head"],
                "tree": claim["commit_tree"],
            }
        if claim["state"] != "REVIEW_REQUIRED":
            raise TransactionError(
                f"commit claim does not require review: {claim['state']}"
            )
        if (
            claim.get("commit_head") != arguments.reviewed_commit
            or claim.get("commit_tree") != arguments.reviewed_tree
            or identity["head"] != arguments.reviewed_commit
            or not _exact_direct_child(
                root, claim["base_head"], arguments.reviewed_commit
            )
            or _git_text(root, "rev-parse", "HEAD^{tree}") != arguments.reviewed_tree
            or bool(_status(root))
        ):
            raise TransactionError(
                "reviewed commit is not the current clean exact direct child"
            )
        _validate_claim_owner(claim, root, arguments.session_id)
        checked = _run_git(
            root,
            ("diff", "--check", claim["base_head"], arguments.reviewed_commit),
            check=False,
        )
        if checked.returncode != 0:
            raise TransactionError("reviewed commit failed git diff --check")
        completed = {**claim, "state": "COMMITTED", "failure": None}
        _atomic_json(claim_path, completed)
        _advance_task_commit(root, completed)
        return {
            "status": "committed",
            "branch": claim["branch"],
            "commit": arguments.reviewed_commit,
            "tree": arguments.reviewed_tree,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--repo-root", required=True)
    prepare_parser.add_argument("--session-id", required=True)
    prepare_parser.add_argument("--authorization", required=True)
    prepare_parser.add_argument("--claim", required=True)
    prepare_parser.add_argument(
        "--requested-action", choices=("commit", "commit-push", "create-pr")
    )
    prepare_parser.add_argument("--intent-sha256")
    prepare_parser.add_argument("--allow-default-branch", action="store_true")
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--repo-root", required=True)
    execute_parser.add_argument("--session-id", required=True)
    execute_parser.add_argument("--claim", required=True)
    execute_parser.add_argument("--token", required=True)
    execute_parser.add_argument("--reviewed-tree", required=True)
    execute_parser.add_argument("--message", required=True)
    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--repo-root", required=True)
    review_parser.add_argument("--session-id", required=True)
    review_parser.add_argument("--claim", required=True)
    review_parser.add_argument("--token", required=True)
    review_parser.add_argument("--reviewed-commit", required=True)
    review_parser.add_argument("--reviewed-tree", required=True)
    for name in ("begin", "finish", "sync", "validate-order"):
        task_parser = subparsers.add_parser(name)
        task_parser.add_argument("--repo-root", required=True)
        task_parser.add_argument("--session-id", required=True)
        task_parser.add_argument(
            "--requested-action",
            required=True,
            choices=("commit", "commit-push", "create-pr"),
        )
        task_parser.add_argument("--intent-sha256", required=True)
        if name == "begin":
            task_parser.add_argument("--target", action="append")
            task_parser.add_argument("--dependency", action="append")
            task_parser.add_argument("--validation-branch")
            task_parser.add_argument("--pr-base")
            task_parser.add_argument("--allow-default-branch", action="store_true")
        elif name == "finish":
            task_parser.add_argument(
                "--outcome", required=True, choices=("completed", "cancelled")
            )
        elif name == "validate-order":
            task_parser.add_argument("--branch", action="append", required=True)
        else:
            task_parser.add_argument("--dependency")
            task_parser.add_argument(
                "--continue", dest="continue_sync", action="store_true"
            )
            task_parser.add_argument("--reviewed-tree")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = {
            "begin": begin,
            "finish": finish,
            "sync": sync,
            "validate-order": validate_order,
            "prepare": prepare,
            "execute": execute,
            "review": review,
        }[arguments.action](arguments)
    except TransactionError as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": str(error),
                    "code": error.code,
                    "retryable": error.retryable,
                    "next_action": error.next_action,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
