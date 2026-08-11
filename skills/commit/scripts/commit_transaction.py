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
    """The commit transaction is unsafe, stale, conflicting, or incomplete."""


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
    value = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
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
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
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
    if set(value) != expected_keys or value.get("schema") != AUTH_SCHEMA:
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
    if owner == "direct":
        if (
            value.get("owner_evidence_path") is not None
            or value.get("owner_evidence_sha256") is not None
        ):
            raise TransactionError("direct commit authorization evidence is invalid")
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
    if claim["authorization_owner"] == "direct":
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


def _validate_claim_authorization(
    claim: dict[str, Any], root: Path, session_id: str
) -> None:
    path = expected_authorization_path(root, session_id)
    private_root = _transaction_root(_common_dir(root))
    if not _safe_private_file(path, private_root):
        raise TransactionError("commit authorization path is unsafe")
    authorization = _load_json(path, "commit authorization")
    expected_state = (
        "CONSUMED" if claim["authorization_owner"] == "direct" else "AUTHORIZED"
    )
    if (
        authorization.get("schema") != AUTH_SCHEMA
        or authorization.get("state") != expected_state
        or authorization.get("owner") != claim["authorization_owner"]
    ):
        raise TransactionError("commit claim authorization is stale")
    normalized = {**authorization, "state": "AUTHORIZED"}
    if claim["authorization_sha256"] != _digest_bytes(_stable_json(normalized)):
        raise TransactionError("commit claim authorization digest does not match")


def _validate_claim(value: dict[str, Any], root: Path, path: Path) -> None:
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
    if set(value) != required or value.get("schema") != CLAIM_SCHEMA:
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
    if value.get("authorization_owner") not in {"direct", "task-implementer"}:
        raise TransactionError("commit claim authorization owner is invalid")
    if value.get("authorization_owner") == "direct":
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
            "repository history moved outside the transaction; run a fresh explicit $commit"
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
            "commit_head": head,
            "commit_tree": tree,
            "failure": None,
        }
        _atomic_json(claim_path, completed)
        if authorization["owner"] == "direct":
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
    if authorization["owner"] == "direct":
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


def prepare(arguments: argparse.Namespace) -> dict[str, object]:
    root = _canonical_repo(arguments.repo_root)
    authorization_path = Path(arguments.authorization).resolve(strict=False)
    claim_path = Path(arguments.claim).resolve(strict=False)
    identity = _identity(root)
    with _repository_lock(Path(identity["common_dir"])):
        private_root = _transaction_root(Path(identity["common_dir"]))
        if not _safe_private_file(authorization_path, private_root):
            raise TransactionError("commit authorization path is unsafe")
        authorization = _load_json(authorization_path, "commit authorization")
        _validate_authorization(
            authorization, root, arguments.session_id, authorization_path
        )
        primary = _primary_worktree(root)
        managed_name = _managed_worktree_name(primary, root)
        if authorization["owner"] == "direct" and managed_name is not None:
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
            _validate_claim(existing, root, claim_path)
        else:
            existing = None
        if existing is not None:
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
            _validate_claim(existing, root, claim_path)
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
                "schema": CLAIM_SCHEMA,
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
        if authorization["owner"] == "direct":
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
            "repository history moved outside the transaction; run a fresh explicit $commit"
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
                "commit claim is stale; run a fresh explicit $commit"
            )
        candidate_tree, candidate_index_sha256 = _preview_tree(root, claim_path.parent)
        if (
            candidate_tree != claim["candidate_tree"]
            or candidate_index_sha256 != claim["candidate_index_sha256"]
        ):
            _mark_claim(claim_path, claim, "STALE", "candidate tree changed")
            raise TransactionError(
                "commit claim is stale because the candidate changed; run a fresh explicit $commit"
            )
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
                "normal-hook git commit failed; run a fresh explicit $commit"
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = {
            "prepare": prepare,
            "execute": execute,
            "review": review,
        }[arguments.action](arguments)
    except TransactionError as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
