#!/usr/bin/env python3
"""Shared fail-closed Git promotion and ref-safety primitives."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import unicodedata


OBJECT_ID_RE = re.compile(r"[0-9a-f]{40,64}\Z")
BRANCH_RE = re.compile(r"refs/heads/([^\s]+)\Z")
PROMOTION_SCHEMA = "git-promotion/v1"


class GitPromotionError(RuntimeError):
    """Repository identity or promotion safety could not be proven."""


def _run(
    cwd: Path,
    arguments: list[str],
    *,
    allowed: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitPromotionError("Git promotion command could not run") from exc
    if result.returncode not in allowed:
        raise GitPromotionError(f"Git promotion command failed: git {arguments[0]}")
    return result


def _git(cwd: Path, *arguments: str, allowed: tuple[int, ...] = (0,)) -> str:
    return _run(cwd, list(arguments), allowed=allowed).stdout.strip()


def repository_root(cwd: Path) -> Path:
    value = _git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel")
    path = Path(value).resolve()
    if not path.is_dir():
        raise GitPromotionError("Git repository root is missing")
    return path


def common_git_dir(cwd: Path) -> Path:
    root = repository_root(cwd)
    value = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    path = Path(value).resolve()
    if not path.is_dir():
        raise GitPromotionError("Git common directory is missing")
    return path


def current_branch(cwd: Path) -> str:
    return _git(cwd, "branch", "--show-current")


def current_head(cwd: Path) -> str:
    value = _git(cwd, "rev-parse", "HEAD")
    if OBJECT_ID_RE.fullmatch(value) is None:
        raise GitPromotionError("Git HEAD is invalid")
    return value


def is_clean(cwd: Path) -> bool:
    return _git(cwd, "status", "--porcelain=v1", "-z") == ""


def public_safe_slug(value: str, *, fallback: str = "work") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    slug = slug[:48].rstrip("-")
    return slug or fallback


def promotion_branch(task_slug: str, lifecycle_id: str) -> str:
    slug = public_safe_slug(task_slug)
    opaque = hashlib.sha256(lifecycle_id.encode("utf-8")).hexdigest()[:8]
    return f"feature/{slug}-{opaque}"


def resolve_remote_default(cwd: Path, *, remote: str = "origin") -> dict[str, str]:
    root = repository_root(cwd)
    _git(root, "remote", "get-url", remote)
    output = _git(root, "ls-remote", "--symref", "--exit-code", remote, "HEAD")
    symbolic: list[str] = []
    heads: list[str] = []
    for line in output.splitlines():
        if line.startswith("ref: "):
            value, separator, name = line.removeprefix("ref: ").partition("\t")
            match = BRANCH_RE.fullmatch(value)
            if separator and name == "HEAD" and match is not None:
                symbolic.append(match.group(1))
            continue
        oid, separator, name = line.partition("\t")
        if separator and name == "HEAD" and OBJECT_ID_RE.fullmatch(oid):
            heads.append(oid)
    if len(symbolic) != 1 or len(heads) != 1:
        raise GitPromotionError("origin HEAD is missing or ambiguous")
    branch = symbolic[0]
    _git(root, "check-ref-format", "--branch", branch)
    remote_ref = f"refs/remotes/{remote}/{branch}"
    _git(
        root,
        "fetch",
        "--no-tags",
        remote,
        f"+refs/heads/{branch}:{remote_ref}",
    )
    fetched = _git(root, "rev-parse", "--verify", remote_ref)
    if fetched != heads[0]:
        raise GitPromotionError("fetched default branch does not match origin HEAD")
    return {
        "remote": remote,
        "default_branch": branch,
        "default_ref": f"{remote}/{branch}",
        "default_head": fetched,
    }


def verify_remote_default(
    cwd: Path,
    *,
    expected_remote: str,
    expected_branch: str,
    expected_ref: str,
    expected_head: str,
) -> dict[str, str]:
    if OBJECT_ID_RE.fullmatch(expected_head) is None:
        raise GitPromotionError("recorded remote-default head is invalid")
    observed = resolve_remote_default(cwd, remote=expected_remote)
    if observed != {
        "remote": expected_remote,
        "default_branch": expected_branch,
        "default_ref": expected_ref,
        "default_head": expected_head,
    }:
        raise GitPromotionError("remote default changed from its recorded identity")
    return observed


def observe_repository(cwd: Path, *, remote: str = "origin") -> dict[str, object]:
    root = repository_root(cwd)
    branch = current_branch(root)
    if not branch:
        raise GitPromotionError("promotion requires a named branch")
    return {
        "schema": PROMOTION_SCHEMA,
        "checkout": str(root),
        "git_common_dir": str(common_git_dir(root)),
        "current_branch": branch,
        "current_head": current_head(root),
        "clean": is_clean(root),
        **resolve_remote_default(root, remote=remote),
    }


def ensure_promotion_branch(
    cwd: Path,
    *,
    lifecycle_id: str,
    task_slug: str,
    remote: str = "origin",
) -> dict[str, object]:
    observed = observe_repository(cwd, remote=remote)
    root = Path(str(observed["checkout"]))
    if not observed["clean"]:
        raise GitPromotionError("promotion checkout must be clean")
    branch = str(observed["current_branch"])
    head = str(observed["current_head"])
    default_branch = str(observed["default_branch"])
    default_head = str(observed["default_head"])
    if branch != default_branch:
        return {
            **observed,
            "promotion_branch": branch,
            "promotion_initial_head": head,
            "promotion_source": "existing",
        }
    ancestor = _run(
        root,
        ["merge-base", "--is-ancestor", head, default_head],
        allowed=(0, 1),
    )
    if ancestor.returncode != 0:
        raise GitPromotionError(
            "local default has commits not contained in the fetched remote default"
        )
    target = promotion_branch(task_slug, lifecycle_id)
    _git(root, "check-ref-format", "--branch", target)
    local = _run(
        root,
        ["show-ref", "--verify", "--quiet", f"refs/heads/{target}"],
        allowed=(0, 1),
    )
    remote_head = _run(
        root,
        ["ls-remote", "--exit-code", remote, f"refs/heads/{target}"],
        allowed=(0, 2),
    )
    if local.returncode == 0 or remote_head.returncode == 0:
        raise GitPromotionError("generated promotion branch already exists")
    _git(root, "switch", "--no-track", "-c", target, default_head)
    if (
        current_branch(root) != target
        or current_head(root) != default_head
        or not is_clean(root)
        or str(common_git_dir(root)) != str(observed["git_common_dir"])
    ):
        raise GitPromotionError("created promotion branch failed verification")
    return {
        **observed,
        "current_branch": target,
        "current_head": default_head,
        "promotion_branch": target,
        "promotion_initial_head": default_head,
        "promotion_source": "auto-created",
    }


@contextmanager
def promotion_lock(cwd: Path, *, timeout_seconds: float = 10.0) -> Iterator[None]:
    if os.name != "posix":
        raise GitPromotionError("promotion locking requires a POSIX host")
    import fcntl

    directory = common_git_dir(cwd) / "codex-workflows"
    try:
        if directory.is_symlink():
            raise GitPromotionError("promotion lock directory must not be a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise GitPromotionError("promotion lock directory is not a directory")
        directory.chmod(0o700)
    except OSError as exc:
        raise GitPromotionError(
            "promotion lock directory could not be prepared safely"
        ) from exc
    path = directory / "promotion.lock"
    if path.is_symlink():
        raise GitPromotionError("promotion lock must not be a symlink")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise GitPromotionError("promotion lock could not be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GitPromotionError("promotion lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise GitPromotionError(
                        "another promotion owns the repository lock"
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def promote_ff_only(
    cwd: Path,
    *,
    expected_branch: str,
    expected_base: str,
    target: str,
) -> dict[str, str]:
    root = repository_root(cwd)
    common = common_git_dir(root)
    with promotion_lock(root):
        target_head = _git(root, "rev-parse", "--verify", target)
        if OBJECT_ID_RE.fullmatch(target_head) is None:
            raise GitPromotionError("promotion target is invalid")
        if (
            repository_root(root) != root
            or common_git_dir(root) != common
            or current_branch(root) != expected_branch
            or not is_clean(root)
        ):
            raise GitPromotionError("promotion checkout identity changed")
        observed = current_head(root)
        if observed == target_head:
            status = "already-promoted"
        elif observed == expected_base:
            if (
                _run(
                    root,
                    ["merge-base", "--is-ancestor", expected_base, target_head],
                    allowed=(0, 1),
                ).returncode
                != 0
            ):
                raise GitPromotionError("promotion target is not a fast-forward")
            _git(root, "merge", "--ff-only", target_head)
            status = "promoted"
        else:
            raise GitPromotionError("promotion checkout moved from its recorded base")
        if (
            current_branch(root) != expected_branch
            or current_head(root) != target_head
            or common_git_dir(root) != common
            or not is_clean(root)
        ):
            raise GitPromotionError("promotion post-verification failed")
    return {"status": status, "branch": expected_branch, "head": target_head}


def delete_local_branch_exact(cwd: Path, *, branch: str, expected_head: str) -> None:
    if OBJECT_ID_RE.fullmatch(expected_head) is None:
        raise GitPromotionError("expected branch head is invalid")
    root = repository_root(cwd)
    _git(root, "check-ref-format", "--branch", branch)
    ref = f"refs/heads/{branch}"
    current = _git(root, "rev-parse", "--verify", ref)
    if current != expected_head:
        raise GitPromotionError("branch advanced before exact deletion")
    _git(root, "update-ref", "-d", ref, expected_head)
    if (
        _run(root, ["show-ref", "--verify", "--quiet", ref], allowed=(0, 1)).returncode
        == 0
    ):
        raise GitPromotionError("branch remains after exact deletion")
