#!/usr/bin/env python3
"""Safely manage project-scoped linked Git worktrees.

The public interface is the ``worktree`` Codex skill. This helper owns only
deterministic discovery, creation, inspection, and cleanup mechanics. It never
commits changes or creates pull requests.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import sys
from typing import Any, Iterable, Sequence

from git_promotion import (
    GitPromotionError,
    resolve_remote_default,
    verify_remote_default,
)
from worktree_state import (
    SCHEMA as MANIFEST_SCHEMA,
    Manifest,
    StateError,
    delete_manifest,
    load_manifest,
    manifest_path,
    matching_manifests,
    write_manifest,
)
from worktree_interop import (
    InteropError,
    acquire_task_lease,
    assert_idle,
    begin_publication,
    end_publication,
    interop_lock,
    release_task_lease,
    update_task_lease,
)


BRANCH_PREFIX = "feature/"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?$")
TASK_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
CONFIG_FIELDS = {
    "scope": "worktreeSkillScope",
    "path": "worktreeSkillPath",
    "base": "worktreeSkillBase",
    "name": "worktreeSkillName",
}
REDACTIONS = (
    (re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE), r"\1<redacted>@"),
    (
        re.compile(r"(?i)\b(token|password|secret|api[-_]?key)=([^\s]+)"),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})\b"),
        "<redacted-token>",
    ),
)


class WorktreeError(RuntimeError):
    """A user-correctable worktree lifecycle error."""


@dataclass(frozen=True)
class WorktreeRecord:
    path: str
    head: str | None = None
    branch: str | None = None
    bare: bool = False
    detached: bool = False
    locked: str | None = None
    prunable: str | None = None


@dataclass(frozen=True)
class ManagedWorktree:
    name: str
    branch: str
    path: Path
    scope: str
    base: str
    head: str

    @property
    def scope_cwd(self) -> Path:
        return self.path if self.scope == "." else self.path / self.scope


def _branch_for_name(name: str) -> str:
    if not name.startswith("project-"):
        raise WorktreeError("managed worktree name must start with project-")
    return f"{BRANCH_PREFIX}{name.removeprefix('project-')}"


def _redact_detail(value: str) -> str:
    redacted = value
    for pattern, replacement in REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GH_PROMPT_DISABLED": "1",
        }
    )
    return environment


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    allowed: Iterable[int] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=cwd,
            env=_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise WorktreeError(f"could not run {arguments[0]}: {error}") from error
    if result.returncode not in set(allowed):
        detail = _redact_detail(
            result.stderr.strip() or result.stdout.strip() or "command failed"
        )
        rendered = " ".join(arguments)
        raise WorktreeError(f"{rendered}: {detail}")
    return result


def _run_bytes(
    arguments: Sequence[str],
    *,
    cwd: Path,
    allowed: Iterable[int] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=cwd,
            env=_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise WorktreeError(f"could not run {arguments[0]}: {error}") from error
    if result.returncode not in set(allowed):
        detail = _redact_detail(
            (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        )
        rendered = " ".join(arguments)
        raise WorktreeError(f"{rendered}: {detail or 'command failed'}")
    return result


def _git(cwd: Path, *arguments: str, allowed: Iterable[int] = (0,)) -> str:
    return _run(["git", *arguments], cwd=cwd, allowed=allowed).stdout.strip()


def _git_bytes(cwd: Path, *arguments: str, allowed: Iterable[int] = (0,)) -> bytes:
    return _run_bytes(["git", *arguments], cwd=cwd, allowed=allowed).stdout


def _canonical(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise WorktreeError(f"path cannot be resolved safely: {path}") from error


def discover_repository(cwd: Path) -> tuple[Path, Path]:
    root_text = _git(cwd, "rev-parse", "--show-toplevel")
    current_root = _canonical(Path(root_text))
    records = list_worktrees(current_root)
    if not records or records[0].bare:
        raise WorktreeError("the repository does not have a primary working tree")
    primary = _canonical(Path(records[0].path))
    return primary, current_root


def parse_worktree_porcelain(data: bytes) -> list[WorktreeRecord]:
    records: list[WorktreeRecord] = []
    current: dict[str, Any] = {}
    for raw_field in data.split(b"\0"):
        if not raw_field:
            if current:
                records.append(WorktreeRecord(**current))
                current = {}
            continue
        field = raw_field.decode("utf-8", "surrogateescape")
        label, separator, value = field.partition(" ")
        if label == "worktree":
            current["path"] = value
        elif label == "HEAD":
            current["head"] = value
        elif label == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif label in {"bare", "detached"}:
            current[label] = True
        elif label in {"locked", "prunable"}:
            current[label] = value if separator else ""
    if current:
        records.append(WorktreeRecord(**current))
    return records


def list_worktrees(repository: Path) -> list[WorktreeRecord]:
    data = _git_bytes(repository, "worktree", "list", "--porcelain", "-z")
    records = parse_worktree_porcelain(data)
    if any(not record.path for record in records):
        raise WorktreeError("Git returned an invalid worktree record")
    return records


def _config_key(branch: str, field: str) -> str:
    return f"branch.{branch}.{CONFIG_FIELDS[field]}"


def _read_config(repository: Path, branch: str, field: str) -> str | None:
    result = _run(
        ["git", "config", "--local", "--get", _config_key(branch, field)],
        cwd=repository,
        allowed=(0, 1),
    )
    return result.stdout.removesuffix("\n") if result.returncode == 0 else None


def _write_config(repository: Path, branch: str, field: str, value: str) -> None:
    _run(
        [
            "git",
            "config",
            "--local",
            "--replace-all",
            _config_key(branch, field),
            value,
        ],
        cwd=repository,
    )


def _managed_from_record(repository: Path, record: WorktreeRecord) -> ManagedWorktree:
    if not record.branch or not record.branch.startswith(BRANCH_PREFIX):
        raise WorktreeError(f"{record.path} is not on a managed worktree branch")
    values = {
        field: _read_config(repository, record.branch, field) for field in CONFIG_FIELDS
    }
    missing = sorted(field for field, value in values.items() if value is None)
    if missing:
        raise WorktreeError(
            f"{record.branch} is missing managed metadata: {', '.join(missing)}"
        )
    assert all(value is not None for value in values.values())
    name = str(values["name"])
    _validate_name(name)
    scope = str(values["scope"])
    _validate_scope_value(scope)
    path = _canonical(Path(str(values["path"])))
    record_path = _canonical(Path(record.path))
    if path != record_path:
        raise WorktreeError(
            f"managed path mismatch for {record.branch}: {path} != {record_path}"
        )
    if record.branch != _branch_for_name(name):
        raise WorktreeError(f"managed branch/name mismatch for {record.branch}")
    try:
        manifest = load_manifest(repository, name)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    assert manifest is not None
    expected_manifest = {
        "branch": record.branch,
        "primary": str(repository),
        "worktree": str(path),
        "scope": scope,
        "base": str(values["base"]),
        "name": name,
    }
    for field, expected in expected_manifest.items():
        if getattr(manifest, field) != expected:
            raise WorktreeError(
                f"ownership manifest {field} does not match live Git state"
            )
    expected_parent = repository.parent / f"{repository.name}-worktrees"
    if path.parent != expected_parent or path.name != name:
        raise WorktreeError(
            f"managed worktree path is outside the expected sibling parent: {path}"
        )
    scope_cwd = path if scope == "." else _canonical(path / scope)
    try:
        scope_cwd.relative_to(path)
    except ValueError as error:
        raise WorktreeError(
            "recorded project scope escapes the managed worktree"
        ) from error
    common_primary = _canonical(
        Path(
            _git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
    )
    common_linked = _canonical(
        Path(_git(path, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    )
    if common_primary != common_linked:
        raise WorktreeError("managed worktree does not share the primary Git directory")
    head = _git(path, "rev-parse", "HEAD")
    return ManagedWorktree(
        name=name,
        branch=record.branch,
        path=path,
        scope=scope,
        base=str(values["base"]),
        head=head,
    )


def _managed_from_recovery(
    repository: Path, record: WorktreeRecord, manifest: Manifest
) -> ManagedWorktree:
    if manifest.status not in {"planned", "recovery"}:
        raise WorktreeError("status-specific setup recovery is not authorized")
    if record.branch != manifest.branch:
        raise WorktreeError("recovery worktree branch does not match its manifest")
    path = _canonical(Path(record.path))
    expected_path = _canonical(Path(manifest.worktree))
    expected_parent = repository.parent / f"{repository.name}-worktrees"
    if (
        path != expected_path
        or path.parent != expected_parent
        or path.name != manifest.name
    ):
        raise WorktreeError("recovery worktree path does not match its manifest")
    _validate_scope_value(manifest.scope)
    common_primary = _canonical(
        Path(
            _git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
    )
    common_linked = _canonical(
        Path(_git(path, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    )
    if common_primary != common_linked:
        raise WorktreeError(
            "recovery worktree does not share the primary Git directory"
        )
    head = _git(path, "rev-parse", "HEAD")
    if head != manifest.base:
        raise WorktreeError(
            "recovery worktree advanced beyond its recorded creation base"
        )
    scope_cwd = path if manifest.scope == "." else _canonical(path / manifest.scope)
    try:
        scope_cwd.relative_to(path)
    except ValueError as error:
        raise WorktreeError("recovery project scope escapes its worktree") from error
    if not scope_cwd.is_dir():
        raise WorktreeError("recovery project scope is missing")
    return ManagedWorktree(
        name=manifest.name,
        branch=manifest.branch,
        path=path,
        scope=manifest.scope,
        base=manifest.base,
        head=head,
    )


def _validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise WorktreeError(
            "worktree name must contain only lowercase letters, digits, and hyphens"
        )


def _validate_task_slug(slug: str) -> None:
    if not TASK_SLUG_RE.fullmatch(slug):
        raise WorktreeError(
            "task slug must be 1-48 lowercase letters, digits, or hyphens"
        )


def _validate_scope_value(scope: str) -> None:
    if scope == ".":
        return
    candidate = PurePosixPath(scope)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise WorktreeError(
            "recorded project scope is not a safe repository-relative path"
        )


def resolve_scope(
    primary: Path, current: Path, project: str | None
) -> tuple[str, Path]:
    if project is not None:
        candidate_input = Path(project)
        if candidate_input.is_absolute():
            raise WorktreeError("--project must be a repository-relative directory")
        candidate = primary / candidate_input
    else:
        candidate = current
    resolved = _canonical(candidate)
    if not resolved.is_dir():
        raise WorktreeError(f"project scope is not a directory: {resolved}")
    try:
        relative = resolved.relative_to(primary)
    except ValueError as error:
        raise WorktreeError("project scope resolves outside the repository") from error
    scope = relative.as_posix() if relative.parts else "."
    return scope, resolved


def _paths_from_status(data: bytes) -> list[str]:
    tokens = data.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        raw = tokens[index]
        index += 1
        if not raw:
            continue
        text = raw.decode("utf-8", "surrogateescape")
        if len(text) < 4:
            raise WorktreeError("Git returned an invalid porcelain status record")
        status = text[:2]
        paths.append(text[3:])
        if "R" in status or "C" in status:
            if index >= len(tokens) or not tokens[index]:
                raise WorktreeError("Git returned an incomplete rename status record")
            paths.append(tokens[index].decode("utf-8", "surrogateescape"))
            index += 1
    return paths


def status_paths(repository: Path, scope: str | None = None) -> list[str]:
    arguments = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
    if scope is not None:
        arguments.extend(["--", scope])
    return _paths_from_status(_git_bytes(repository, *arguments))


def branch_changed_paths(repository: Path, base: str) -> list[str]:
    data = _git_bytes(repository, "diff", "--name-only", "-z", base, "HEAD")
    return [
        item.decode("utf-8", "surrogateescape") for item in data.split(b"\0") if item
    ]


def _inside_scope(path: str, scope: str) -> bool:
    if scope == ".":
        return True
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(scope)
    return candidate == boundary or boundary in candidate.parents


def _operation_in_progress(repository: Path) -> str | None:
    for marker in (
        "MERGE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
    ):
        marker_path = Path(
            _git(
                repository,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                marker,
            )
        )
        if marker_path.exists():
            return marker
    if _git(repository, "diff", "--name-only", "--diff-filter=U"):
        return "unresolved conflicts"
    return None


def _remote_head(repository: Path, branch: str) -> str | None:
    output = _git(
        repository,
        "ls-remote",
        "--heads",
        "origin",
        f"refs/heads/{branch}",
    )
    if not output:
        return None
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise WorktreeError(f"origin returned multiple matches for {branch}")
    return lines[0].split(maxsplit=1)[0]


def _local_branch_exists(repository: Path, branch: str) -> bool:
    result = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repository,
        allowed=(0, 1),
    )
    return result.returncode == 0


def _candidate_available(
    repository: Path,
    parent: Path,
    name: str,
    records: Sequence[WorktreeRecord],
) -> bool:
    branch = _branch_for_name(name)
    path = parent / name
    if (
        path.exists()
        or path.is_symlink()
        or manifest_path(repository, name).exists()
        or _local_branch_exists(repository, branch)
    ):
        return False
    if _remote_head(repository, branch) is not None:
        return False
    return not any(
        Path(record.path) == path or record.branch == branch for record in records
    )


def _new_name(
    repository: Path,
    parent: Path,
    task_slug: str,
    records: Sequence[WorktreeRecord],
) -> str:
    descriptive = f"project-{task_slug}"[:82].strip("-")
    for _ in range(20):
        name = f"{descriptive}-{secrets.token_hex(3)}"
        if _candidate_available(repository, parent, name, records):
            return name
    raise WorktreeError("could not generate a collision-free worktree name")


def add_worktree(
    *,
    cwd: Path,
    project: str | None,
    task_slug: str,
    reuse: str | None = None,
) -> dict[str, Any]:
    _validate_task_slug(task_slug)
    primary, current_root = discover_repository(cwd)
    if current_root != primary:
        raise WorktreeError("add must be invoked from the primary checkout")
    if _git(primary, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1)) == "":
        raise WorktreeError("cannot add a worktree from detached HEAD")
    operation = _operation_in_progress(primary)
    if operation:
        raise WorktreeError(f"repository operation is in progress: {operation}")
    try:
        default = resolve_remote_default(primary)
    except GitPromotionError as error:
        raise WorktreeError(str(error)) from error
    base_ref = str(default["default_ref"])
    base = str(default["default_head"])
    scope, _ = resolve_scope(primary, cwd, project)

    if reuse is not None:
        _validate_name(reuse)
        try:
            manifest = load_manifest(primary, reuse)
        except StateError as error:
            raise WorktreeError(str(error)) from error
        assert manifest is not None
        if (
            manifest.status != "active"
            or manifest.scope != scope
            or manifest.task_slug != task_slug
            or manifest.default_remote != default["remote"]
            or manifest.default_branch != default["default_branch"]
            or manifest.default_ref != base_ref
        ):
            raise WorktreeError(
                "requested reuse does not match the active lifecycle, project scope, "
                "task slug, or current remote-default identity"
            )
        record = _find_record(
            primary,
            current_root,
            name=reuse,
            current_cwd=cwd,
        )
        managed = _managed_from_record(primary, record)
        dirty = status_paths(managed.path)
        default_head_drift = manifest.default_head != default["default_head"]
        return {
            "action": "add",
            "status": "reused",
            "name": managed.name,
            "branch": managed.branch,
            "base_ref": manifest.default_ref,
            "base_sha": manifest.base,
            "current_default_sha": default["default_head"],
            "remote_default_head_drift": default_head_drift,
            "worktree": str(managed.path),
            "scope": managed.scope,
            "scope_cwd": str(managed.scope_cwd),
            "dirty_paths": dirty,
        }

    dirty_scope = status_paths(primary, scope)
    if dirty_scope:
        raise WorktreeError(
            "selected project has uncommitted changes; commit, push, open a PR, "
            f"and merge that work before creating a {base_ref} worktree"
        )
    merge_base = _git(primary, "merge-base", base, "HEAD")
    branch_diff = _git_bytes(
        primary, "diff", "--name-only", "-z", merge_base, "HEAD", "--", scope
    )
    if branch_diff:
        tree_comparison = _run(
            ["git", "diff", "--quiet", base, "HEAD", "--", scope],
            cwd=primary,
            allowed=(0, 1),
        )
        if tree_comparison.returncode != 0:
            raise WorktreeError(
                f"selected project has branch changes not contained in {base_ref}; "
                "merge its PR before creating another worktree for this project"
            )

    parent = primary.parent / f"{primary.name}-worktrees"
    if parent.is_symlink():
        raise WorktreeError(f"worktree parent must not be a symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True)
    if _canonical(parent) != parent.absolute():
        raise WorktreeError(f"worktree parent resolves unexpectedly: {parent}")

    try:
        existing = matching_manifests(primary, scope=scope, task_slug=task_slug)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    if existing:
        names = ", ".join(manifest.name for manifest in existing)
        raise WorktreeError(
            "a managed lifecycle already exists for this project/task: "
            f"{names}; pass --reuse with the exact active name, or recover/remove it"
        )

    records = list_worktrees(primary)
    name = _new_name(primary, parent, task_slug, records)
    _validate_name(name)
    branch = _branch_for_name(name)
    _git(primary, "check-ref-format", "--branch", branch)
    path = parent / name
    manifest = Manifest(
        schema=MANIFEST_SCHEMA,
        status="planned",
        name=name,
        branch=branch,
        primary=str(primary),
        worktree=str(path.absolute()),
        scope=scope,
        base=base,
        task_slug=task_slug,
        default_remote=str(default["remote"]),
        default_branch=str(default["default_branch"]),
        default_ref=base_ref,
        default_head=base,
    )
    try:
        write_manifest(primary, manifest)
    except StateError as error:
        raise WorktreeError(str(error)) from error

    created = False
    try:
        _git(
            primary,
            "worktree",
            "add",
            "--no-track",
            "-b",
            branch,
            str(path),
            base,
        )
        created = True
        for field, value in (
            ("scope", scope),
            ("path", str(path.absolute())),
            ("base", base),
            ("name", name),
        ):
            _write_config(primary, branch, field, value)
        record = next(
            (
                item
                for item in list_worktrees(primary)
                if item.branch == branch
                and _canonical(Path(item.path)) == _canonical(path)
            ),
            None,
        )
        if record is None:
            raise WorktreeError("created worktree could not be re-observed")
        managed = _managed_from_record(primary, record)
        if managed.head != base or status_paths(managed.path):
            raise WorktreeError(
                "created worktree failed base or cleanliness verification"
            )
        if not managed.scope_cwd.is_dir():
            raise WorktreeError(
                f"project scope is missing in new worktree: {managed.scope_cwd}"
            )
        try:
            write_manifest(
                primary, manifest.updated(status="active", expected_head=base)
            )
        except StateError as error:
            raise WorktreeError(str(error)) from error
    except BaseException:
        if created:
            try:
                _run(["git", "worktree", "remove", str(path)], cwd=primary)
                if _local_branch_exists(primary, branch):
                    head = _git(primary, "rev-parse", branch)
                    if head == base:
                        _remove_local_branch(primary, branch, expected_head=base)
            except WorktreeError:
                # Preserve the original interruption or setup error. Residual
                # resources are detected below and retain a recovery manifest.
                pass
        residual_path = path.exists() or path.is_symlink()
        residual_branch = _local_branch_exists(primary, branch)
        if residual_path or residual_branch:
            try:
                write_manifest(
                    primary, manifest.updated(status="recovery", expected_head=base)
                )
            except StateError:
                pass
        else:
            try:
                delete_manifest(primary, name)
            except StateError:
                pass
        raise

    unrelated = [
        path for path in status_paths(primary) if not _inside_scope(path, scope)
    ]
    return {
        "action": "add",
        "status": "created",
        "name": name,
        "branch": branch,
        "base_ref": base_ref,
        "base_sha": base,
        "worktree": str(managed.path),
        "scope": scope,
        "scope_cwd": str(managed.scope_cwd),
        "unrelated_primary_changes": unrelated,
    }


def _find_record(
    primary: Path,
    current_root: Path,
    *,
    name: str | None,
    current_cwd: Path,
    project: str | None = None,
) -> WorktreeRecord:
    records = list_worktrees(primary)
    if current_root != primary:
        for record in records:
            if _canonical(Path(record.path)) == current_root:
                return record
        raise WorktreeError("current linked worktree is not registered")
    if name is not None:
        _validate_name(name)
        try:
            manifest = load_manifest(primary, name)
        except StateError as error:
            raise WorktreeError(str(error)) from error
        assert manifest is not None
        branch = manifest.branch
        matches = [record for record in records if record.branch == branch]
    else:
        scope, _ = resolve_scope(primary, current_cwd, project)
        matches = [
            record
            for record in records
            if record.branch and _read_config(primary, record.branch, "scope") == scope
        ]
    if len(matches) != 1:
        rendered = ", ".join(record.branch or record.path for record in matches)
        raise WorktreeError(
            "expected exactly one managed worktree; "
            f"found {len(matches)}{': ' + rendered if rendered else ''}"
        )
    return matches[0]


def inspect_worktree(
    *,
    cwd: Path,
    name: str | None,
    require_scope_clean: bool,
    enforce_interop_idle: bool = True,
) -> dict[str, Any]:
    primary, current_root = discover_repository(cwd)
    record = _find_record(primary, current_root, name=name, current_cwd=cwd)
    managed = _managed_from_record(primary, record)
    if enforce_interop_idle:
        try:
            with interop_lock(primary):
                assert_idle(primary, managed.name)
        except InteropError as error:
            raise WorktreeError(str(error)) from error
    if require_scope_clean:
        if current_root == primary:
            raise WorktreeError(
                "scope-clean inspection for push or create-pr must run inside "
                "the managed linked worktree"
            )
        current_directory = _canonical(cwd)
        try:
            current_directory.relative_to(_canonical(managed.scope_cwd))
        except ValueError as error:
            raise WorktreeError(
                f"run the action from the recorded project scope: {managed.scope_cwd}"
            ) from error
    manifest = load_manifest(primary, managed.name)
    assert manifest is not None
    try:
        verify_remote_default(
            primary,
            expected_remote=manifest.default_remote,
            expected_branch=manifest.default_branch,
            expected_ref=manifest.default_ref,
            expected_head=manifest.default_head,
        )
    except GitPromotionError as error:
        raise WorktreeError(str(error)) from error
    _git(managed.path, "rev-parse", "--verify", manifest.default_ref)
    dirty = status_paths(managed.path)
    committed = branch_changed_paths(managed.path, managed.base)
    outside_dirty = sorted(
        path for path in dirty if not _inside_scope(path, managed.scope)
    )
    outside_committed = sorted(
        path for path in committed if not _inside_scope(path, managed.scope)
    )
    if require_scope_clean and (outside_dirty or outside_committed):
        raise WorktreeError(
            "managed branch contains changes outside its recorded project scope: "
            + ", ".join(outside_dirty + outside_committed)
        )
    return {
        "action": "inspect",
        "status": "valid",
        **asdict(managed),
        "path": str(managed.path),
        "scope_cwd": str(managed.scope_cwd),
        "dirty_paths": dirty,
        "branch_changed_paths": committed,
        "outside_scope_dirty": outside_dirty,
        "outside_scope_committed": outside_committed,
        "default_branch": manifest.default_branch,
        "default_ref": manifest.default_ref,
        "default_head": manifest.default_head,
    }


def inspect_managed_anchor(*, cwd: Path) -> dict[str, Any]:
    """Return exact managed-outer identity without publication guards."""

    primary, current_root = discover_repository(cwd)
    if current_root == primary:
        return {"action": "anchor-inspect", "status": "unmanaged"}
    record = _find_record(primary, current_root, name=None, current_cwd=cwd)
    managed = _managed_from_record(primary, record)
    manifest = load_manifest(primary, managed.name)
    assert manifest is not None
    if manifest.status != "active":
        raise WorktreeError(f"managed outer worktree is not active: {manifest.status}")
    current = _canonical(cwd)
    try:
        task_scope_cwd = current.relative_to(managed.path).as_posix() or "."
    except ValueError as error:
        raise WorktreeError(
            "current task scope escapes the managed worktree"
        ) from error
    common_dir = _canonical(
        Path(
            _git(
                managed.path,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
    )
    return {
        "action": "anchor-inspect",
        "status": "managed",
        "name": managed.name,
        "branch": managed.branch,
        "worktree": str(managed.path),
        "scope": managed.scope,
        "task_scope": task_scope_cwd,
        "head": managed.head,
        "common_dir": str(common_dir),
        "primary": str(primary),
    }


def task_lease_acquire(
    *,
    cwd: Path,
    workspace: Path,
    run_id: str,
    task_scope: str,
    initial_head: str,
    owner_kind: str,
) -> dict[str, Any]:
    anchor = inspect_managed_anchor(cwd=cwd)
    if anchor["status"] == "unmanaged":
        return {"action": "task-lease-acquire", "status": "unmanaged"}
    if anchor["task_scope"] != task_scope or anchor["head"] != initial_head:
        raise WorktreeError(
            "managed outer identity changed before task lease acquisition"
        )
    try:
        result = acquire_task_lease(
            Path(str(anchor["primary"])),
            name=str(anchor["name"]),
            branch=str(anchor["branch"]),
            worktree=Path(str(anchor["worktree"])),
            scope=str(anchor["scope"]),
            common_dir=Path(str(anchor["common_dir"])),
            workspace=workspace,
            run_id=run_id,
            task_scope=task_scope,
            initial_head=initial_head,
            owner_kind=owner_kind,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "task-lease-acquire", **result}


def _leased_anchor(cwd: Path, name: str) -> tuple[Path, ManagedWorktree]:
    primary, current_root = discover_repository(cwd)
    record = _find_record(primary, current_root, name=name, current_cwd=cwd)
    return primary, _managed_from_record(primary, record)


def task_lease_resource(
    *,
    cwd: Path,
    name: str,
    lease_id: str,
    kind: str,
    path: Path,
    branch: str,
    state: str,
    owner_kind: str,
) -> dict[str, Any]:
    primary, _ = _leased_anchor(cwd, name)
    try:
        result = update_task_lease(
            primary,
            name=name,
            token=lease_id,
            owner_kind=owner_kind,
            resource={
                "kind": kind,
                "path": str(path.absolute()),
                "branch": branch,
                "state": state,
            },
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "task-lease-resource", "status": "updated", **result}


def task_lease_promote(
    *, cwd: Path, name: str, lease_id: str, promoted_head: str, owner_kind: str
) -> dict[str, Any]:
    primary, managed = _leased_anchor(cwd, name)
    if managed.head != promoted_head:
        raise WorktreeError("outer worktree is not at the promoted task head")
    try:
        result = update_task_lease(
            primary,
            name=name,
            token=lease_id,
            owner_kind=owner_kind,
            promoted_head=promoted_head,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "task-lease-promote", "status": "updated", **result}


def task_lease_release(
    *, cwd: Path, name: str, lease_id: str, promoted_head: str, owner_kind: str
) -> dict[str, Any]:
    primary, managed = _leased_anchor(cwd, name)
    if managed.head != promoted_head:
        raise WorktreeError("outer worktree is not at the promoted task head")
    if status_paths(managed.path):
        raise WorktreeError("outer worktree must be clean before task lease release")
    records = list_worktrees(primary)
    registered = {_canonical(Path(record.path)) for record in records}
    branches = set(
        _git(
            primary, "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        ).splitlines()
    )
    try:
        result = release_task_lease(
            primary,
            name=name,
            token=lease_id,
            owner_kind=owner_kind,
            observed_outer_head=promoted_head,
            registered_worktrees=registered,
            existing_branches=branches,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "task-lease-release", **result}


def publication_begin(*, cwd: Path, action: str) -> dict[str, Any]:
    inspected = inspect_worktree(
        cwd=cwd,
        name=None,
        require_scope_clean=True,
        enforce_interop_idle=False,
    )
    primary, _ = discover_repository(cwd)
    try:
        result = begin_publication(
            primary,
            name=str(inspected["name"]),
            branch=str(inspected["branch"]),
            worktree=Path(str(inspected["path"])),
            action=action,
            starting_head=str(inspected["head"]),
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {
        "action": "publication-begin",
        "scope_cwd": inspected["scope_cwd"],
        "default_branch": inspected["default_branch"],
        "default_ref": inspected["default_ref"],
        "default_head": inspected["default_head"],
        **result,
    }


def publication_end_action(
    *, cwd: Path, action: str, reservation_id: str
) -> dict[str, Any]:
    primary, current_root = discover_repository(cwd)
    record = _find_record(primary, current_root, name=None, current_cwd=cwd)
    managed = _managed_from_record(primary, record)
    try:
        result = end_publication(
            primary,
            name=managed.name,
            action=action,
            reservation_id=reservation_id,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "publication-end", **result}


def _pull_requests(
    repository: Path, branch: str, expected_base: str
) -> list[dict[str, Any]]:
    fields = ",".join(
        (
            "number",
            "url",
            "state",
            "mergedAt",
            "headRefName",
            "headRefOid",
            "baseRefName",
        )
    )
    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--head",
            branch,
            "--base",
            expected_base,
            "--limit",
            "100",
            "--json",
            fields,
        ],
        cwd=repository,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorktreeError("GitHub CLI returned invalid PR JSON") from error
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise WorktreeError("GitHub CLI returned an unexpected PR response")
    return payload


def _matching_merged_pr(
    pull_requests: Sequence[dict[str, Any]],
    branch: str,
    head: str,
    expected_base: str,
) -> dict[str, Any] | None:
    matches = [
        item
        for item in pull_requests
        if item.get("headRefName") == branch
        and item.get("baseRefName") == expected_base
        and item.get("headRefOid") == head
        and (item.get("state") == "MERGED" or item.get("mergedAt"))
    ]
    if len(matches) > 1:
        raise WorktreeError("multiple merged PRs match the exact managed branch head")
    return matches[0] if matches else None


def _remove_local_branch(primary: Path, branch: str, *, expected_head: str) -> str:
    ref = f"refs/heads/{branch}"
    _run(["git", "update-ref", "-d", ref, expected_head], cwd=primary)
    _run(
        ["git", "config", "--local", "--remove-section", f"branch.{branch}"],
        cwd=primary,
        allowed=(0, 5),
    )
    return "deleted-with-expected-old-value"


def _remove_worktree_unlocked(*, cwd: Path, name: str | None) -> dict[str, Any]:
    primary, current_root = discover_repository(cwd)
    records = list_worktrees(primary)
    if current_root != primary:
        current_record = next(
            (
                record
                for record in records
                if _canonical(Path(record.path)) == current_root
            ),
            None,
        )
        suggested = (
            _read_config(primary, current_record.branch, "name")
            if current_record and current_record.branch
            else "<generated-worktree-name>"
        )
        raise WorktreeError(
            "remove must execute from the primary checkout so it cannot delete its "
            f"active cwd; rerun there with --name {suggested}"
        )
    if name is None:
        raise WorktreeError("remove from the primary checkout requires --name")
    _validate_name(name)
    try:
        manifest = load_manifest(primary, name, required=False)
    except StateError as error:
        raise WorktreeError(str(error)) from error

    branch = manifest.branch if manifest is not None else _branch_for_name(name)
    _git(primary, "fetch", "origin", "--prune")
    records = list_worktrees(primary)
    record = next((item for item in records if item.branch == branch), None)
    local_exists = _local_branch_exists(primary, branch)
    remote_head = _remote_head(primary, branch)

    if record is None and not local_exists and remote_head is None:
        if manifest is not None:
            try:
                delete_manifest(primary, name)
            except StateError as error:
                raise WorktreeError(str(error)) from error
        return {
            "action": "remove",
            "status": "already-removed",
            "name": name,
            "branch": branch,
        }
    if manifest is None:
        raise WorktreeError(
            "refusing cleanup because the durable managed ownership manifest is missing"
        )
    if (
        manifest.branch != branch
        or manifest.name != name
        or manifest.primary != str(primary)
    ):
        raise WorktreeError(
            "ownership manifest does not match the requested repository/ref"
        )
    if record is not None and not local_exists:
        raise WorktreeError("registered worktree branch is missing locally")

    if manifest.status in {"planned", "recovery"}:
        recovery_heads: list[tuple[str, str]] = []
        if local_exists:
            recovery_heads.append(
                ("local branch", _git(primary, "rev-parse", f"refs/heads/{branch}"))
            )
        if remote_head is not None:
            recovery_heads.append(("remote branch", remote_head))
        for resource, current_head in recovery_heads:
            if current_head != manifest.base:
                raise WorktreeError(
                    f"{resource} advanced beyond its recorded recovery base: "
                    f"{current_head} != {manifest.base}"
                )

    cleanup_head: str | None = None
    if manifest.status == "cleanup-pending":
        cleanup_head = manifest.expected_head
        if cleanup_head is None:
            raise WorktreeError("cleanup-pending manifest has no verified head")
        remaining_heads: list[tuple[str, str]] = []
        if local_exists:
            remaining_heads.append(
                ("local branch", _git(primary, "rev-parse", f"refs/heads/{branch}"))
            )
        if remote_head is not None:
            remaining_heads.append(("remote branch", remote_head))
        for resource, current_head in remaining_heads:
            if current_head != cleanup_head:
                raise WorktreeError(
                    f"{resource} advanced after cleanup proof: "
                    f"{current_head} != {cleanup_head}"
                )

    managed: ManagedWorktree | None = None
    if record is not None:
        if manifest.status in {"planned", "recovery"}:
            managed = _managed_from_recovery(primary, record, manifest)
        else:
            managed = _managed_from_record(primary, record)
        operation = _operation_in_progress(managed.path)
        if operation:
            raise WorktreeError(
                f"target worktree operation is in progress: {operation}"
            )
        dirty = status_paths(managed.path)
        if dirty:
            raise WorktreeError(
                "target worktree is dirty and will not be force-removed: "
                + ", ".join(dirty)
            )
        head = managed.head
        recent = _git(managed.path, "log", "--format=%H", "-5").splitlines()
    elif local_exists:
        head = _git(primary, "rev-parse", f"refs/heads/{branch}")
        if manifest.status in {"planned", "recovery"}:
            recovery_path = Path(manifest.worktree)
            if recovery_path.exists() or recovery_path.is_symlink():
                raise WorktreeError(
                    "unregistered recovery worktree path remains; refusing branch deletion"
                )
            if head != manifest.base:
                raise WorktreeError(
                    "recovery branch advanced beyond its recorded creation base"
                )
            scope = manifest.scope
            base = manifest.base
            path_text = manifest.worktree
        else:
            scope = _read_config(primary, branch, "scope")
            base = _read_config(primary, branch, "base")
            path_text = _read_config(primary, branch, "path")
            stored_name = _read_config(primary, branch, "name")
            if None in {scope, base, path_text, stored_name} or stored_name != name:
                raise WorktreeError("local branch is missing managed cleanup metadata")
            if (
                scope != manifest.scope
                or base != manifest.base
                or path_text != manifest.worktree
            ):
                raise WorktreeError(
                    "local branch metadata does not match ownership manifest"
                )
        recent = _git(primary, "log", "--format=%H", "-5", branch).splitlines()
    else:
        assert remote_head is not None
        head = remote_head
        scope = base = path_text = None
        recent = []

    pull_requests = _pull_requests(primary, branch, manifest.default_branch)
    merged_pr = _matching_merged_pr(
        pull_requests, branch, head, manifest.default_branch
    )
    unused_local = False
    if local_exists:
        base_value = managed.base if managed is not None else str(base)
        unique_count = int(
            _git(primary, "rev-list", "--count", f"{base_value}..{head}")
        )
        unused_local = unique_count == 0 and remote_head is None and not pull_requests
    if merged_pr is None and not unused_local:
        raise WorktreeError(
            "cleanup requires an exact merged PR/head match, or a never-published "
            "local branch with no commits beyond its recorded base"
        )
    if remote_head is not None and remote_head != head:
        raise WorktreeError(
            f"remote branch advanced unexpectedly: {remote_head} != verified {head}"
        )
    if cleanup_head is not None and head != cleanup_head:
        raise WorktreeError(
            f"managed head advanced after cleanup proof: {head} != {cleanup_head}"
        )
    if cleanup_head is None:
        try:
            write_manifest(
                primary, manifest.updated(status="cleanup-pending", expected_head=head)
            )
        except StateError as error:
            raise WorktreeError(str(error)) from error

    removed_path: str | None = None
    if managed is not None:
        if status_paths(managed.path):
            raise WorktreeError("target worktree changed during cleanup preflight")
        current_head = _git(managed.path, "rev-parse", "HEAD")
        if current_head != head:
            raise WorktreeError(
                f"target branch advanced during cleanup: {current_head} != {head}"
            )
        removed_path = str(managed.path)
        _git(primary, "worktree", "remove", str(managed.path))

    local_result = "already-removed"
    if local_exists:
        current_local_head = _git(primary, "rev-parse", f"refs/heads/{branch}")
        if current_local_head != head:
            raise WorktreeError(
                "local branch advanced after cleanup proof; worktree may be removed, "
                f"but branch was retained at {current_local_head}"
            )
        local_result = _remove_local_branch(primary, branch, expected_head=head)

    remote_result = "already-absent"
    if remote_head is not None:
        lease = f"--force-with-lease=refs/heads/{branch}:{head}"
        _git(primary, "push", lease, "origin", f":refs/heads/{branch}")
        remote_result = "deleted-with-exact-lease"
    _git(primary, "fetch", "origin", "--prune")
    try:
        delete_manifest(primary, name)
    except StateError as error:
        raise WorktreeError(str(error)) from error

    return {
        "action": "remove",
        "status": "removed",
        "name": name,
        "branch": branch,
        "head": head,
        "worktree": removed_path,
        "local_branch": local_result,
        "remote_branch": remote_result,
        "pull_request": merged_pr,
        "recent_commit_shas": recent,
    }


def remove_worktree(*, cwd: Path, name: str | None) -> dict[str, Any]:
    primary, current_root = discover_repository(cwd)
    if current_root != primary or name is None:
        return _remove_worktree_unlocked(cwd=cwd, name=name)
    _validate_name(name)
    try:
        with interop_lock(primary):
            assert_idle(primary, name)
            return _remove_worktree_unlocked(cwd=cwd, name=name)
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage project-scoped linked Git worktrees safely."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    add = subparsers.add_parser("add", help="create a managed linked worktree")
    add.add_argument("--project", help="repository-relative project directory")
    add.add_argument(
        "--task-slug",
        default="work",
        help="public-safe lowercase task slug; never pass prompt text or secrets",
    )
    add.add_argument(
        "--reuse",
        metavar="EXACT_NAME",
        help="reuse one exact active managed worktree instead of creating another",
    )

    inspect = subparsers.add_parser("inspect", help="validate managed identity")
    inspect.add_argument("--name", help="generated worktree name from primary checkout")
    inspect.add_argument(
        "--require-scope-clean",
        action="store_true",
        help="fail when dirty or committed paths escape the recorded scope",
    )

    remove = subparsers.add_parser("remove", help="remove a safely completed worktree")
    remove.add_argument(
        "--name",
        required=True,
        help="exact generated worktree name; run from the primary checkout",
    )

    subparsers.add_parser(
        "anchor-inspect", help="internal: inspect a managed outer checkout"
    )

    task_acquire = subparsers.add_parser(
        "task-lease-acquire", help="internal: acquire task coordinator ownership"
    )
    task_acquire.add_argument("--workspace", required=True, type=Path)
    task_acquire.add_argument("--run-id", required=True)
    task_acquire.add_argument("--task-scope", required=True)
    task_acquire.add_argument("--initial-head", required=True)
    task_acquire.add_argument(
        "--owner-kind", required=True, choices=("task-implementer", "agentic-sdlc")
    )

    task_resource = subparsers.add_parser(
        "task-lease-resource", help="internal: update task-owned resource intent"
    )
    task_resource.add_argument("--name", required=True)
    task_resource.add_argument("--lease-id", required=True)
    task_resource.add_argument(
        "--kind", required=True, choices=("integration", "worker")
    )
    task_resource.add_argument("--path", required=True, type=Path)
    task_resource.add_argument("--branch", required=True)
    task_resource.add_argument(
        "--state", required=True, choices=("planned", "present", "absent")
    )
    task_resource.add_argument(
        "--owner-kind", required=True, choices=("task-implementer", "agentic-sdlc")
    )

    task_promote = subparsers.add_parser(
        "task-lease-promote", help="internal: record an outer promotion"
    )
    task_promote.add_argument("--name", required=True)
    task_promote.add_argument("--lease-id", required=True)
    task_promote.add_argument("--promoted-head", required=True)
    task_promote.add_argument(
        "--owner-kind", required=True, choices=("task-implementer", "agentic-sdlc")
    )

    task_release = subparsers.add_parser(
        "task-lease-release", help="internal: release completed task ownership"
    )
    task_release.add_argument("--name", required=True)
    task_release.add_argument("--lease-id", required=True)
    task_release.add_argument("--promoted-head", required=True)
    task_release.add_argument(
        "--owner-kind", required=True, choices=("task-implementer", "agentic-sdlc")
    )

    publication_begin_parser = subparsers.add_parser(
        "publication-begin", help="internal: reserve outer publication"
    )
    publication_begin_parser.add_argument(
        "--publication-action", required=True, choices=("push", "create-pr")
    )

    publication_end_parser = subparsers.add_parser(
        "publication-end", help="internal: release outer publication"
    )
    publication_end_parser.add_argument(
        "--publication-action", required=True, choices=("push", "create-pr")
    )
    publication_end_parser.add_argument("--reservation-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "add":
            result = add_worktree(
                cwd=Path.cwd(),
                project=arguments.project,
                task_slug=arguments.task_slug,
                reuse=arguments.reuse,
            )
        elif arguments.action == "inspect":
            result = inspect_worktree(
                cwd=Path.cwd(),
                name=arguments.name,
                require_scope_clean=arguments.require_scope_clean,
            )
        elif arguments.action == "remove":
            result = remove_worktree(cwd=Path.cwd(), name=arguments.name)
        elif arguments.action == "anchor-inspect":
            result = inspect_managed_anchor(cwd=Path.cwd())
        elif arguments.action == "task-lease-acquire":
            result = task_lease_acquire(
                cwd=Path.cwd(),
                workspace=arguments.workspace,
                run_id=arguments.run_id,
                task_scope=arguments.task_scope,
                initial_head=arguments.initial_head,
                owner_kind=arguments.owner_kind,
            )
        elif arguments.action == "task-lease-resource":
            result = task_lease_resource(
                cwd=Path.cwd(),
                name=arguments.name,
                lease_id=arguments.lease_id,
                kind=arguments.kind,
                path=arguments.path,
                branch=arguments.branch,
                state=arguments.state,
                owner_kind=arguments.owner_kind,
            )
        elif arguments.action == "task-lease-promote":
            result = task_lease_promote(
                cwd=Path.cwd(),
                name=arguments.name,
                lease_id=arguments.lease_id,
                promoted_head=arguments.promoted_head,
                owner_kind=arguments.owner_kind,
            )
        elif arguments.action == "task-lease-release":
            result = task_lease_release(
                cwd=Path.cwd(),
                name=arguments.name,
                lease_id=arguments.lease_id,
                promoted_head=arguments.promoted_head,
                owner_kind=arguments.owner_kind,
            )
        elif arguments.action == "publication-begin":
            result = publication_begin(
                cwd=Path.cwd(), action=arguments.publication_action
            )
        else:
            result = publication_end_action(
                cwd=Path.cwd(),
                action=arguments.publication_action,
                reservation_id=arguments.reservation_id,
            )
    except (WorktreeError, GitPromotionError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
