#!/usr/bin/env python3
"""Safely manage locally integrated linked Git worktrees.

The public interface is the ``worktree`` Codex skill. This helper owns only
deterministic discovery, creation, inspection, integration, and cleanup
mechanics. Its private integration-commit action creates only a preflight-
authorized, reviewed-tree commit behind a durable preparation claim. It never
pushes or creates pull requests.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
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
    promote_ff_only,
    public_safe_slug,
)
from task_lane_state import (
    GENERATION_KIND,
    GENERATION_SCHEMA,
    LANE_KIND,
    LANE_SCHEMA,
    OBJECT_ID_RE,
    TaskLaneStateError,
    all_lanes,
    load_generation,
    load_lane,
    validate_lane,
    write_generation,
    write_lane,
)
from worktree_state import (
    SCHEMA as MANIFEST_SCHEMA,
    Manifest,
    StateError,
    all_manifests,
    checked_worktree_parent,
    delete_manifest,
    load_manifest,
    manifest_path,
    matching_manifests,
    state_directory,
    write_manifest,
)
from worktree_interop import (
    InteropError,
    abort_integration_preparation,
    acquire_task_lease,
    active_preparations,
    active_reservations,
    all_leases,
    approve_integration_preparation_commit,
    assert_idle,
    begin_integration_preparation,
    begin_integration,
    delete_lease_removal,
    delete_released_lease,
    end_integration,
    integration_transition_lock,
    interop_lock,
    inspect_task_lease,
    lifecycle_lease,
    lifecycle_creation_lock,
    load_lease,
    load_lease_removal,
    load_preparation,
    load_reservation,
    prepare_lease_removal,
    record_integration_preparation_commit,
    release_task_lease,
    retire_released_task_lease,
    rollback_task_lease_acquisition,
    task_lane_transition_lock,
    update_integration,
    update_task_lease,
    validate_released_resources,
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
TASK_LANE_CONFIG_FIELDS = {
    "lane_id": "worktreeSkillTaskLaneId",
    "source_ref": "worktreeSkillTaskLaneSourceRef",
    "incarnation": "worktreeSkillTaskLaneIncarnation",
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


@dataclass(frozen=True)
class AddPreflight:
    primary: Path
    current_root: Path
    source_branch: str
    source_ref: str
    base: str
    scope: str
    task_slug: str
    parent: Path


@dataclass(frozen=True)
class TaskLanePreflight:
    primary: Path
    current_root: Path
    common_dir: Path
    source_branch: str
    source_ref: str
    source_head: str
    scope: str
    scope_path: Path
    lane_id: str
    scope_slug: str


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


def _task_lane_config_key(branch: str, field: str) -> str:
    return f"branch.{branch}.{TASK_LANE_CONFIG_FIELDS[field]}"


def _read_task_lane_config(
    repository: Path, branch: str, field: str
) -> str | None:
    result = _run(
        ["git", "config", "--local", "--get", _task_lane_config_key(branch, field)],
        cwd=repository,
        allowed=(0, 1),
    )
    return result.stdout.removesuffix("\n") if result.returncode == 0 else None


def _write_task_lane_config(
    repository: Path, branch: str, field: str, value: str
) -> None:
    _run(
        [
            "git",
            "config",
            "--local",
            "--replace-all",
            _task_lane_config_key(branch, field),
            value,
        ],
        cwd=repository,
    )


def _task_lane_id_for_branch(repository: Path, branch: str | None) -> str | None:
    if branch is None:
        return None
    values = {
        field: _read_task_lane_config(repository, branch, field)
        for field in TASK_LANE_CONFIG_FIELDS
    }
    present = {field for field, value in values.items() if value is not None}
    if not present:
        return None
    if present != set(TASK_LANE_CONFIG_FIELDS):
        raise WorktreeError("task lane branch metadata is incomplete")
    lane_id = str(values["lane_id"])
    source_ref = str(values["source_ref"])
    incarnation = str(values["incarnation"])
    if (
        re.fullmatch(r"[0-9a-f]{32}", lane_id) is None
        or not source_ref.startswith("refs/heads/")
        or not source_ref.removeprefix("refs/heads/")
        or not incarnation.isdigit()
        or int(incarnation) < 1
    ):
        raise WorktreeError("task lane branch metadata is invalid")
    return lane_id


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


def _resolve_task_slug(project_path: Path, task_slug: str | None) -> str:
    resolved = task_slug
    if resolved is None:
        resolved = public_safe_slug(project_path.name)
    _validate_task_slug(resolved)
    return resolved


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
    return not any(
        Path(record.path) == path or record.branch == branch for record in records
    )


def _configured_default_branch(repository: Path) -> str:
    _git(repository, "remote", "get-url", "origin")
    result = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repository,
        allowed=(0, 1),
    )
    if result.returncode != 0:
        raise WorktreeError(
            "cannot determine the configured default branch from origin/HEAD"
        )
    symbolic = result.stdout.strip()
    prefix = "origin/"
    if not symbolic.startswith(prefix) or symbolic == prefix:
        raise WorktreeError("configured origin/HEAD is invalid")
    branch = symbolic.removeprefix(prefix)
    _git(repository, "check-ref-format", "--branch", branch)
    return branch


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


def _preflight_add(
    *, cwd: Path, project: str | None, task_slug: str | None
) -> AddPreflight:
    """Validate add without creating lifecycle state or managed directories."""

    if task_slug is not None:
        _validate_task_slug(task_slug)
    primary, current_root = discover_repository(cwd)
    if current_root != primary:
        raise WorktreeError("add must be invoked from the primary checkout")
    source_branch = _git(
        primary, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1)
    )
    if source_branch == "":
        raise WorktreeError("cannot add a worktree from detached HEAD")
    operation = _operation_in_progress(primary)
    if operation:
        raise WorktreeError(f"repository operation is in progress: {operation}")
    default_branch = _configured_default_branch(primary)
    if source_branch == default_branch:
        raise WorktreeError(
            "worktree creation requires a non-default source branch; create or "
            "switch to a feature branch and rerun $worktree"
        )
    dirty_primary = status_paths(primary)
    if dirty_primary:
        raise WorktreeError(
            "the primary source worktree must be completely clean: "
            + ", ".join(dirty_primary)
        )
    base = _git(primary, "rev-parse", "HEAD")
    source_ref = f"refs/heads/{source_branch}"
    scope, project_path = resolve_scope(primary, cwd, project)
    resolved_task_slug = _resolve_task_slug(project_path, task_slug)
    try:
        parent = checked_worktree_parent(primary)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    return AddPreflight(
        primary=primary,
        current_root=current_root,
        source_branch=source_branch,
        source_ref=source_ref,
        base=base,
        scope=scope,
        task_slug=resolved_task_slug,
        parent=parent,
    )


def _add_worktree_unlocked(
    *,
    cwd: Path,
    project: str | None,
    task_slug: str | None,
    reuse: str | None = None,
) -> dict[str, Any]:
    preflight = _preflight_add(cwd=cwd, project=project, task_slug=task_slug)
    primary = preflight.primary
    current_root = preflight.current_root
    source_branch = preflight.source_branch
    source_ref = preflight.source_ref
    base = preflight.base
    scope = preflight.scope
    task_slug = preflight.task_slug

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
            or manifest.source_branch != source_branch
            or manifest.source_ref != source_ref
        ):
            raise WorktreeError(
                "requested reuse does not match the active lifecycle, project scope, "
                "task slug, or current local source branch"
            )
        record = _find_record(
            primary,
            current_root,
            name=reuse,
            current_cwd=cwd,
        )
        managed = _managed_from_record(primary, record)
        if not managed.scope_cwd.is_dir():
            raise WorktreeError("reused project scope is not a directory")
        dirty = status_paths(managed.path)
        source_head = _git(primary, "rev-parse", source_ref)
        return {
            "action": "add",
            "status": "reused",
            "name": managed.name,
            "branch": managed.branch,
            "source_branch": manifest.source_branch,
            "source_ref": manifest.source_ref,
            "base_sha": manifest.base,
            "current_source_sha": source_head,
            "source_head_drift": manifest.base != source_head,
            "worktree": str(managed.path),
            "scope": managed.scope,
            "task_slug": task_slug,
            "scope_cwd": str(managed.scope_cwd),
            "dirty_paths": dirty,
        }

    try:
        parent = checked_worktree_parent(primary, create=True)
    except StateError as error:
        raise WorktreeError(str(error)) from error

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
        source_branch=source_branch,
        source_ref=source_ref,
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

    return {
        "action": "add",
        "status": "created",
        "name": name,
        "branch": branch,
        "source_branch": source_branch,
        "source_ref": source_ref,
        "base_sha": base,
        "worktree": str(managed.path),
        "scope": scope,
        "task_slug": task_slug,
        "scope_cwd": str(managed.scope_cwd),
    }


def add_worktree(
    *,
    cwd: Path,
    project: str | None,
    task_slug: str | None,
    reuse: str | None = None,
) -> dict[str, Any]:
    preflight = _preflight_add(cwd=cwd, project=project, task_slug=task_slug)
    try:
        with lifecycle_creation_lock(preflight.primary):
            return _add_worktree_unlocked(
                cwd=cwd,
                project=project,
                task_slug=task_slug,
                reuse=reuse,
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _task_lane_scope_slug(scope: str) -> str:
    value = "repository" if scope == "." else PurePosixPath(scope).name
    slug = public_safe_slug(value)[:48].strip("-")
    return slug or "project"


def _task_lane_identity(
    *, common_dir: Path, primary: Path, source_ref: str, scope: str
) -> str:
    payload = "\0".join((str(common_dir), str(primary), source_ref, scope))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _task_lane_preflight(*, cwd: Path, project: str | None) -> TaskLanePreflight:
    primary, current_root = discover_repository(cwd)
    if current_root != primary:
        raise WorktreeError(
            "task lane creation must start from the primary checkout; an existing "
            "Task Implementer lane may be reused from its own checkout"
        )
    source_branch = _git(
        primary, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1)
    )
    if not source_branch:
        raise WorktreeError("Task Implementer lanes require an attached source branch")
    operation = _operation_in_progress(primary)
    if operation:
        raise WorktreeError(f"repository operation is in progress: {operation}")
    default_branch = _configured_default_branch(primary)
    if source_branch == default_branch:
        raise WorktreeError(
            "Task Implementer lane creation requires a named non-default source "
            "branch; create or switch to a feature branch and repeat workspace init"
        )
    source_ref = f"refs/heads/{source_branch}"
    source_head = _git(primary, "rev-parse", "--verify", source_ref)
    scope, scope_path = resolve_scope(primary, cwd, project)
    if scope != ".":
        object_name = f"{source_head}:{scope}"
        result = _run(
            ["git", "cat-file", "-t", object_name],
            cwd=primary,
            allowed=(0, 128),
        )
        if result.returncode != 0 or result.stdout.strip() != "tree":
            raise WorktreeError(
                "project scope must exist as a committed directory at source HEAD; "
                "uncommitted-only folders are not included in a Task Implementer lane"
            )
    common_dir = _canonical(
        Path(
            _git(
                primary,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
    )
    lane_id = _task_lane_identity(
        common_dir=common_dir,
        primary=primary,
        source_ref=source_ref,
        scope=scope,
    )
    return TaskLanePreflight(
        primary=primary,
        current_root=current_root,
        common_dir=common_dir,
        source_branch=source_branch,
        source_ref=source_ref,
        source_head=source_head,
        scope=scope,
        scope_path=scope_path,
        lane_id=lane_id,
        scope_slug=_task_lane_scope_slug(scope),
    )


def _task_lane_live(
    primary: Path, lane: dict[str, object]
) -> tuple[Manifest, ManagedWorktree]:
    name = str(lane["name"])
    try:
        manifest = load_manifest(primary, name)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    assert manifest is not None
    record = _find_record(primary, primary, name=name, current_cwd=primary)
    managed = _managed_from_record(primary, record)
    if (
        manifest.status not in {"active", "integrated"}
        or managed.branch != lane["branch"]
        or str(managed.path) != lane["worktree"]
        or managed.scope != lane["scope"]
        or manifest.source_ref != lane["source_ref"]
        or _task_lane_id_for_branch(primary, managed.branch) != lane["lane_id"]
        or _read_task_lane_config(primary, managed.branch, "source_ref")
        != lane["source_ref"]
        or _read_task_lane_config(primary, managed.branch, "incarnation")
        != str(lane["incarnation"])
    ):
        raise WorktreeError("Task Implementer lane state disagrees with live Git state")
    return manifest, managed


def _task_lane_refresh_idle(
    primary: Path, lane: dict[str, object]
) -> dict[str, object]:
    if lane["state"] != "idle" or lane["pending_generations"]:
        return lane
    manifest, managed = _task_lane_live(primary, lane)
    if manifest.status != "active":
        return lane
    if status_paths(managed.path):
        raise WorktreeError(
            "Task Implementer lane must be completely clean before reuse"
        )
    operation = _operation_in_progress(managed.path)
    if operation:
        raise WorktreeError(f"Task Implementer lane operation is in progress: {operation}")
    source_head = _git(primary, "rev-parse", "--verify", str(lane["source_ref"]))
    if managed.head == source_head:
        _write_config(primary, managed.branch, "base", source_head)
        try:
            if manifest.base != source_head or manifest.expected_head != source_head:
                write_manifest(
                    primary,
                    manifest.updated(base=source_head, expected_head=source_head),
                )
            if lane["lane_head"] != source_head or lane["base_head"] != source_head:
                lane = {**lane, "lane_head": source_head, "base_head": source_head}
                write_lane(primary, lane)
        except (StateError, TaskLaneStateError) as error:
            raise WorktreeError(str(error)) from error
        return lane
    if not _is_ancestor(primary, managed.head, source_head):
        raise WorktreeError(
            "idle Task Implementer lane diverged from its source; integrate or "
            "remove it before reuse"
        )
    _git(managed.path, "merge", "--ff-only", source_head)
    if _git(managed.path, "rev-parse", "HEAD") != source_head or status_paths(
        managed.path
    ):
        raise WorktreeError("Task Implementer lane refresh failed exact verification")
    _write_config(primary, managed.branch, "base", source_head)
    try:
        write_manifest(
            primary,
            manifest.updated(base=source_head, expected_head=source_head),
        )
        lane = {**lane, "base_head": source_head, "lane_head": source_head}
        write_lane(primary, lane)
    except (StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error
    return lane


def _task_lane_result(
    primary: Path, lane: dict[str, object], *, status: str
) -> dict[str, Any]:
    lane = _task_lane_refresh_idle(primary, lane)
    _, managed = _task_lane_live(primary, lane)
    scope_cwd = managed.scope_cwd
    if not scope_cwd.is_dir():
        raise WorktreeError("Task Implementer lane project scope is unavailable")
    return {
        "action": "task-lane-ensure",
        "status": status,
        "lane_id": lane["lane_id"],
        "incarnation": lane["incarnation"],
        "lane_state": lane["state"],
        "name": managed.name,
        "branch": managed.branch,
        "worktree": str(managed.path),
        "scope": managed.scope,
        "scope_cwd": str(scope_cwd),
        "primary": str(primary),
        "common_dir": lane["common_dir"],
        "source_branch": lane["source_branch"],
        "source_ref": lane["source_ref"],
        "source_head": _git(primary, "rev-parse", "--verify", str(lane["source_ref"])),
        "lane_head": _git(managed.path, "rev-parse", "HEAD"),
        "latest_generation": lane["latest_generation"],
        "last_integrated_generation": lane["last_integrated_generation"],
    }


def _task_lane_result_locked(
    primary: Path, lane: dict[str, object], *, status: str
) -> dict[str, Any]:
    """Refresh and inspect a lane under its shared transition lock.

    Callers already hold the repository lifecycle-creation lock, establishing
    the lock order lifecycle creation -> per-lane transition.
    """

    with integration_transition_lock(primary, str(lane["name"])):
        current = load_lane(primary, str(lane["lane_id"]))
        assert current is not None
        return _task_lane_result(primary, current, status=status)


def _task_lane_from_current(
    *, cwd: Path, project: str | None
) -> tuple[Path, dict[str, object]] | None:
    primary, current_root = discover_repository(cwd)
    if current_root == primary:
        return None
    record = next(
        (
            item
            for item in list_worktrees(primary)
            if _canonical(Path(item.path)) == current_root
        ),
        None,
    )
    lane_id = _task_lane_id_for_branch(primary, record.branch if record else None)
    if lane_id is None:
        raise WorktreeError(
            "workspace init from an unrelated linked worktree is unsupported; "
            "run it from the primary checkout or the owning Task Implementer lane"
        )
    try:
        lane = load_lane(primary, lane_id)
    except TaskLaneStateError as error:
        raise WorktreeError(str(error)) from error
    assert lane is not None
    if project is None:
        current_scope = _canonical(cwd).relative_to(current_root).as_posix()
        current_scope = current_scope or "."
        if current_scope != lane["scope"]:
            raise WorktreeError(
                "current directory does not match the owning Task Implementer "
                "project scope; initialize the exact project from the primary "
                "checkout"
            )
    else:
        candidate = PurePosixPath(project)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorktreeError("project scope must be repository-relative")
        normalized = candidate.as_posix() or "."
        if normalized != lane["scope"]:
            raise WorktreeError(
                "explicit project scope does not match the owning Task Implementer lane"
            )
    return primary, lane


def _recover_task_lane_creation(
    primary: Path, lane: dict[str, object]
) -> dict[str, object]:
    """Finish cleanup after an interrupted lane creation before reincarnation."""

    if lane["state"] not in {"creating", "recovery"}:
        return lane
    name = str(lane["name"])
    manifest = load_manifest(primary, name, required=False)
    if manifest is None:
        manifest = Manifest(
            schema=MANIFEST_SCHEMA,
            status="recovery",
            name=name,
            branch=str(lane["branch"]),
            primary=str(primary),
            worktree=str(lane["worktree"]),
            scope=str(lane["scope"]),
            base=str(lane["base_head"]),
            task_slug=f"ti-{_task_lane_scope_slug(str(lane['scope']))}"[:48].rstrip(
                "-"
            ),
            source_branch=str(lane["source_branch"]),
            source_ref=str(lane["source_ref"]),
            expected_head=str(lane["base_head"]),
        )
        write_manifest(primary, manifest)
    elif manifest.status == "planned":
        write_manifest(
            primary,
            manifest.updated(
                status="recovery", expected_head=str(lane["base_head"])
            ),
        )
    _remove_worktree_unlocked(cwd=primary, name=name, allow_task_lane=True)
    path = Path(str(lane["worktree"]))
    branch = str(lane["branch"])
    if (
        path.exists()
        or path.is_symlink()
        or _local_branch_exists(primary, branch)
        or manifest_path(primary, name).exists()
        or any(item.branch == branch for item in list_worktrees(primary))
    ):
        raise WorktreeError(
            "interrupted Task Implementer lane creation retains recovery resources"
        )
    recovered = {**lane, "state": "removed", "removal": None}
    write_lane(primary, recovered)
    return recovered


def task_lane_ensure(*, cwd: Path, project: str | None) -> dict[str, Any]:
    current = _task_lane_from_current(cwd=cwd, project=project)
    if current is not None:
        primary, lane = current
        try:
            with lifecycle_creation_lock(primary):
                lane = load_lane(primary, str(lane["lane_id"]))
                assert lane is not None
                return _task_lane_result_locked(primary, lane, status="reused")
        except (InteropError, TaskLaneStateError) as error:
            raise WorktreeError(str(error)) from error

    preflight = _task_lane_preflight(cwd=cwd, project=project)
    primary = preflight.primary
    try:
        with lifecycle_creation_lock(primary):
            locked = _task_lane_preflight(cwd=cwd, project=project)
            if locked != preflight:
                raise WorktreeError(
                    "source identity changed during Task Implementer lane initialization"
                )
            existing = load_lane(primary, preflight.lane_id, required=False)
            if existing is not None and existing["state"] != "removed":
                expected = {
                    "primary": str(primary),
                    "common_dir": str(preflight.common_dir),
                    "source_branch": preflight.source_branch,
                    "source_ref": preflight.source_ref,
                    "scope": preflight.scope,
                }
                if any(existing.get(key) != value for key, value in expected.items()):
                    raise WorktreeError("existing Task Implementer lane identity changed")
                if existing["state"] in {"creating", "recovery"}:
                    existing = _recover_task_lane_creation(primary, existing)
                else:
                    return _task_lane_result_locked(
                        primary, existing, status="reused"
                    )

            incarnation = (
                int(existing["incarnation"]) + 1 if existing is not None else 1
            )
            generation_base = (
                int(existing["latest_generation"]) if existing is not None else 0
            )
            suffix = f"-{preflight.lane_id[:8]}-{incarnation}"
            descriptive = f"project-ti-{preflight.scope_slug}"[: 95 - len(suffix)].rstrip("-")
            name = f"{descriptive}{suffix}"
            _validate_name(name)
            branch = _branch_for_name(name)
            parent = checked_worktree_parent(primary, create=True)
            path = parent / name
            if (
                path.exists()
                or path.is_symlink()
                or _local_branch_exists(primary, branch)
                or manifest_path(primary, name).exists()
            ):
                raise WorktreeError(
                    "Task Implementer lane resources already exist without matching state"
                )
            lane: dict[str, object] = {
                "schema": LANE_SCHEMA,
                "kind": LANE_KIND,
                "lane_id": preflight.lane_id,
                "incarnation": incarnation,
                "state": "creating",
                "primary": str(primary),
                "common_dir": str(preflight.common_dir),
                "source_branch": preflight.source_branch,
                "source_ref": preflight.source_ref,
                "scope": preflight.scope,
                "name": name,
                "branch": branch,
                "worktree": str(path),
                "base_head": preflight.source_head,
                "lane_head": preflight.source_head,
                "latest_generation": generation_base,
                "last_integrated_generation": generation_base,
                "active_generation": None,
                "pending_generations": [],
                "claims": [],
                "integration": None,
                "removal": None,
            }
            write_lane(primary, lane)
            manifest = Manifest(
                schema=MANIFEST_SCHEMA,
                status="planned",
                name=name,
                branch=branch,
                primary=str(primary),
                worktree=str(path),
                scope=preflight.scope,
                base=preflight.source_head,
                task_slug=f"ti-{preflight.scope_slug}"[:48].rstrip("-"),
                source_branch=preflight.source_branch,
                source_ref=preflight.source_ref,
            )
            write_manifest(primary, manifest)
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
                    preflight.source_head,
                )
                created = True
                for field, value in (
                    ("scope", preflight.scope),
                    ("path", str(path)),
                    ("base", preflight.source_head),
                    ("name", name),
                ):
                    _write_config(primary, branch, field, value)
                for field, value in (
                    ("lane_id", preflight.lane_id),
                    ("source_ref", preflight.source_ref),
                    ("incarnation", str(incarnation)),
                ):
                    _write_task_lane_config(primary, branch, field, value)
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
                    raise WorktreeError("created Task Implementer lane was not re-observed")
                managed = _managed_from_record(primary, record)
                if (
                    managed.head != preflight.source_head
                    or status_paths(managed.path)
                    or not managed.scope_cwd.is_dir()
                    or _git(primary, "rev-parse", "--verify", preflight.source_ref)
                    != preflight.source_head
                ):
                    raise WorktreeError(
                        "created Task Implementer lane failed exact baseline verification"
                    )
                write_manifest(
                    primary,
                    manifest.updated(
                        status="active", expected_head=preflight.source_head
                    ),
                )
                lane = {**lane, "state": "idle"}
                write_lane(primary, lane)
            except BaseException:
                lane = {**lane, "state": "recovery"}
                try:
                    write_lane(primary, lane)
                except TaskLaneStateError:
                    pass
                if created:
                    try:
                        _run(["git", "worktree", "remove", str(path)], cwd=primary)
                    except WorktreeError:
                        pass
                raise
            return _task_lane_result_locked(primary, lane, status="created")
    except (InteropError, StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error


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
    require_clean: bool,
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
    manifest = load_manifest(primary, managed.name)
    assert manifest is not None
    dirty = status_paths(managed.path)
    committed = branch_changed_paths(managed.path, managed.base)
    if require_clean and dirty:
        raise WorktreeError(
            "managed worktree must be completely clean: " + ", ".join(dirty)
        )
    source_head = _git(primary, "rev-parse", "--verify", manifest.source_ref)
    source_contains_integration = False
    if manifest.status == "integrated":
        assert manifest.integration_source_head is not None
        assert manifest.integration_child_head is not None
        assert manifest.integration_head is not None
        parents = _git(
            primary,
            "rev-list",
            "--parents",
            "-n",
            "1",
            manifest.integration_head,
        ).split()
        if parents != [
            manifest.integration_head,
            manifest.integration_source_head,
            manifest.integration_child_head,
        ]:
            raise WorktreeError("recorded integration merge proof is no longer exact")
        ancestry = _run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                manifest.integration_head,
                manifest.source_ref,
            ],
            cwd=primary,
            allowed=(0, 1),
        )
        if ancestry.returncode != 0:
            raise WorktreeError(
                "current local source branch no longer contains the recorded integration"
            )
        source_contains_integration = True
    return {
        "action": "inspect",
        "status": "valid",
        **asdict(managed),
        "path": str(managed.path),
        "scope_cwd": str(managed.scope_cwd),
        "task_slug": manifest.task_slug,
        "dirty_paths": dirty,
        "branch_changed_paths": committed,
        "source_branch": manifest.source_branch,
        "source_ref": manifest.source_ref,
        "source_head": source_head,
        "source_contains_integration": source_contains_integration,
        "source_base": manifest.base,
        "lifecycle_status": manifest.status,
        "integration_source_head": manifest.integration_source_head,
        "integration_child_head": manifest.integration_child_head,
        "integration_head": manifest.integration_head,
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
    lane_id = _task_lane_id_for_branch(primary, managed.branch)
    if lane_id is None:
        try:
            matching_lanes = [
                lane
                for lane in all_lanes(primary)
                if lane["state"] != "removed"
                and lane["name"] == managed.name
                and lane["branch"] == managed.branch
                and lane["worktree"] == str(managed.path)
            ]
        except TaskLaneStateError as error:
            raise WorktreeError(str(error)) from error
        if matching_lanes:
            raise WorktreeError(
                "Task Implementer lane branch metadata is missing from a live lane"
            )
    if lane_id is not None:
        try:
            lane = load_lane(primary, lane_id)
        except TaskLaneStateError as error:
            raise WorktreeError(str(error)) from error
        assert lane is not None
        if (
            lane["name"] != managed.name
            or lane["worktree"] != str(managed.path)
            or lane["scope"] != managed.scope
            or lane["branch"] != managed.branch
        ):
            raise WorktreeError("Task Implementer lane anchor identity is inconsistent")
        return {
            "action": "anchor-inspect",
            "status": "task-lane",
            "lane_id": lane_id,
            "incarnation": lane["incarnation"],
            "lane_state": lane["state"],
            "latest_generation": lane["latest_generation"],
            "last_integrated_generation": lane["last_integrated_generation"],
            "name": managed.name,
            "branch": managed.branch,
            "worktree": str(managed.path),
            "scope": managed.scope,
            "task_scope": task_scope_cwd,
            "head": managed.head,
            "common_dir": str(common_dir),
            "primary": str(primary),
            "source_branch": lane["source_branch"],
            "source_ref": lane["source_ref"],
            "source_base": lane["base_head"],
        }
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
        "source_branch": manifest.source_branch,
        "source_ref": manifest.source_ref,
        "source_base": manifest.base,
    }


def publication_guard(*, cwd: Path, action: str) -> dict[str, Any]:
    if action not in {"push", "create-pr"}:
        raise WorktreeError("publication guard action is invalid")
    primary, current_root = discover_repository(cwd)
    record = next(
        (
            item
            for item in list_worktrees(primary)
            if _canonical(Path(item.path)) == current_root
        ),
        None,
    )
    if record is None:
        raise WorktreeError("current linked worktree is not registered")

    branch = record.branch
    if branch is not None:
        metadata = {
            field: _read_config(primary, branch, field) for field in CONFIG_FIELDS
        }
        present = {field for field, value in metadata.items() if value is not None}
        if present:
            if len(present) != len(CONFIG_FIELDS):
                raise WorktreeError(
                    "partial managed worktree metadata makes publication unsafe"
                )
            managed = _managed_from_record(primary, record)
            lane_id = _task_lane_id_for_branch(primary, managed.branch)
            if lane_id is not None:
                raise WorktreeError(
                    f"Task Implementer lane branches must not {action}; run "
                    "$task-implementer integrate for the owning project scope"
                )
            raise WorktreeError(
                f"managed child branches must not {action}; run $worktree integrate "
                f"{managed.name}, then publish the source branch"
            )

    current_path = current_root
    current_common = _canonical(
        Path(
            _git(
                current_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
    )

    def classify_claim(claim_path: str, claim_branch: str, label: str) -> bool:
        path_matches = Path(claim_path) == current_path
        branch_matches = branch == claim_branch
        if path_matches != branch_matches:
            raise WorktreeError(
                f"{label} partially matches this checkout; publication is unsafe"
            )
        return path_matches and branch_matches

    try:
        for manifest in all_manifests(primary):
            if classify_claim(manifest.worktree, manifest.branch, "ownership manifest"):
                raise WorktreeError(
                    f"managed child branches must not {action}; run $worktree "
                    f"integrate {manifest.name}, then publish the source branch"
                )
        for reservation in active_reservations(primary):
            claims = (
                (
                    str(reservation["worktree"]),
                    str(reservation["branch"]),
                    "integration child reservation",
                ),
                (
                    str(reservation["integration_worktree"]),
                    str(reservation["integration_branch"]),
                    "integration candidate reservation",
                ),
            )
            for claim_path, claim_branch, label in claims:
                if classify_claim(claim_path, claim_branch, label):
                    raise WorktreeError(
                        f"private {label} branches must not {action}; publish only "
                        "the locally integrated source branch"
                    )
        for preparation in active_preparations(primary):
            if classify_claim(
                str(primary),
                str(preparation["source_branch"]),
                "integration preparation source",
            ):
                raise WorktreeError(
                    f"the source branch must not {action} while an integration "
                    "commit preparation is active"
                )
            if classify_claim(
                str(preparation["worktree"]),
                str(preparation["branch"]),
                "integration preparation child",
            ):
                raise WorktreeError(
                    f"integration preparation child branches must not {action}; "
                    "finish local integration first"
                )
        for lease in all_leases(primary):
            if Path(str(lease["common_dir"])) != current_common:
                raise WorktreeError(
                    "task lease Git common directory is inconsistent with this repository"
                )
            if classify_claim(
                str(lease["worktree"]), str(lease["branch"]), "task lease outer"
            ):
                raise WorktreeError(
                    f"managed outer branches must not {action}; finish the owning "
                    "coordinator and locally integrate the outer worktree"
                )
            for resource in lease["resources"]:
                if classify_claim(
                    str(resource["path"]),
                    str(resource["branch"]),
                    "task lease resource",
                ):
                    if resource["state"] == "absent":
                        raise WorktreeError(
                            "an absent task lease resource has reappeared; publication "
                            "is unsafe"
                        )
                    raise WorktreeError(
                        f"private task coordinator branches must not {action}; "
                        "promote through the owning coordinator"
                    )
        parent = checked_worktree_parent(primary)
    except (InteropError, StateError) as error:
        raise WorktreeError(str(error)) from error
    if parent in current_path.parents:
        raise WorktreeError(
            "an unclaimed checkout exists inside the managed worktree namespace; "
            "publication is unsafe"
        )
    return {
        "action": "publication-guard",
        "status": "allowed",
        "mode": "source" if current_root == primary else "unmanaged",
    }


def _record_manifest_lease(
    primary: Path,
    *,
    name: str,
    owner_kind: str,
    token: str,
    state: str,
) -> None:
    """Advance the durable ownership marker after the lease write succeeds."""

    if state not in {"active", "released"}:
        raise WorktreeError("ownership manifest lease state is invalid")
    try:
        with interop_lock(primary):
            lease = lifecycle_lease(
                primary,
                name,
                allow_external_transition=True,
            )
            if (
                lease is None
                or lease["owner_kind"] != owner_kind
                or lease["token"] != token
                or lease["state"] != state
            ):
                raise WorktreeError(
                    "task lease changed before its ownership marker was recorded"
                )
            manifest = load_manifest(primary, name)
            assert manifest is not None
            current_identity = (manifest.lease_owner, manifest.lease_token)
            expected_identity = (owner_kind, token)
            if state == "active":
                if manifest.lease_state == "released":
                    raise WorktreeError(
                        "released ownership manifest cannot become active"
                    )
                if (
                    manifest.lease_state == "active"
                    and current_identity != expected_identity
                ):
                    raise WorktreeError(
                        "task lease disagrees with its ownership manifest"
                    )
            elif manifest.lease_state == "none":
                raise WorktreeError(
                    "released task lease is missing its active ownership marker"
                )
            elif current_identity != expected_identity:
                raise WorktreeError("task lease disagrees with its ownership manifest")
            if manifest.lease_state != state or current_identity != expected_identity:
                write_manifest(
                    primary,
                    manifest.updated(
                        lease_state=state,
                        lease_owner=owner_kind,
                        lease_token=token,
                    ),
                )
    except (InteropError, StateError) as error:
        raise WorktreeError(str(error)) from error


def _validate_outer_lease_receipt(
    primary: Path,
    *,
    name: str,
    manifest: Manifest | None,
    observed_head: str | None = None,
    for_removal: bool = False,
) -> dict[str, object] | None:
    """Bind a terminal receipt to exact outer identity and absent resources."""

    try:
        intent = load_lease_removal(primary, name)
        if intent is not None:
            if not for_removal:
                raise InteropError(
                    "outer worktree removal is in progress; repeat $worktree remove"
                )
            lease = intent["receipt"]
            current = load_lease(primary, name)
            if current is not None and current != lease:
                raise InteropError("task lease disagrees with its removal intent")
        else:
            lease = (
                lifecycle_lease(primary, name)
                if manifest is not None
                else load_lease(primary, name)
            )
        if lease is None:
            return None
        if lease["state"] != "released":
            raise InteropError(
                f"{lease['owner_kind']} still owns the outer worktree; "
                "the outer lifecycle is blocked"
            )
        expected_worktree = (
            Path(manifest.worktree)
            if manifest is not None
            else checked_worktree_parent(primary) / name
        )
        expected_branch = (
            manifest.branch if manifest is not None else _branch_for_name(name)
        )
        expected_scope = manifest.scope if manifest is not None else lease["scope"]
        common_dir = _canonical(
            Path(
                _git(
                    primary,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            )
        )
        if (
            lease["branch"] != expected_branch
            or Path(str(lease["worktree"])) != expected_worktree
            or lease["scope"] != expected_scope
            or Path(str(lease["common_dir"])) != common_dir
        ):
            raise InteropError("released task lease outer identity is inconsistent")
        expected_head = observed_head
        if expected_head is None and manifest is not None:
            expected_head = (
                manifest.integration_child_head
                if manifest.status == "integrated"
                else manifest.base
            )
        if expected_head is not None and lease["promoted_head"] != expected_head:
            raise InteropError("released task lease promoted head is inconsistent")
        records = list_worktrees(primary)
        registered = {_canonical(Path(item.path)) for item in records}
        branches = set(
            _git(
                primary,
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/",
            ).splitlines()
        )
        return validate_released_resources(
            lease,
            registered_worktrees=registered,
            existing_branches=branches,
        )
    except (InteropError, StateError) as error:
        raise WorktreeError(str(error)) from error


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
    if anchor["status"] != "managed":
        raise WorktreeError(
            "ordinary task leases cannot be acquired from a Task Implementer "
            "persistent lane"
        )
    primary = Path(str(anchor["primary"]))
    name = str(anchor["name"])
    try:
        with integration_transition_lock(primary, name):
            locked_anchor = inspect_managed_anchor(cwd=cwd)
            expected_identity = {
                key: anchor[key]
                for key in (
                    "status",
                    "name",
                    "branch",
                    "worktree",
                    "scope",
                    "task_scope",
                    "head",
                    "common_dir",
                    "primary",
                )
            }
            identity_changed = any(
                locked_anchor.get(key) != value
                for key, value in expected_identity.items()
            )
            if (
                identity_changed
                or locked_anchor["task_scope"] != task_scope
                or locked_anchor["head"] != initial_head
            ):
                raise WorktreeError(
                    "managed outer identity changed before task lease acquisition"
                )
            outer_worktree = Path(str(locked_anchor["worktree"]))
            operation = _operation_in_progress(outer_worktree)
            if operation:
                raise WorktreeError(
                    "managed outer operation is in progress before lease "
                    f"acquisition: {operation}"
                )
            if status_paths(outer_worktree):
                raise WorktreeError(
                    "managed outer worktree must be completely clean before "
                    "lease acquisition"
                )
            result = acquire_task_lease(
                primary,
                name=name,
                branch=str(locked_anchor["branch"]),
                worktree=outer_worktree,
                scope=str(locked_anchor["scope"]),
                common_dir=Path(str(locked_anchor["common_dir"])),
                workspace=workspace,
                run_id=run_id,
                task_scope=task_scope,
                initial_head=initial_head,
                owner_kind=owner_kind,
            )
            final_anchor = inspect_managed_anchor(cwd=cwd)
            final_changed = any(
                final_anchor.get(key) != value
                for key, value in expected_identity.items()
            )
            if (
                _operation_in_progress(outer_worktree)
                or status_paths(outer_worktree)
                or final_changed
            ):
                if result["status"] == "acquired":
                    rollback_task_lease_acquisition(
                        primary,
                        name=name,
                        token=str(result["token"]),
                    )
                raise WorktreeError(
                    "managed outer checkout changed during task lease acquisition"
                )
            _record_manifest_lease(
                primary,
                name=name,
                owner_kind=owner_kind,
                token=str(result["token"]),
                state="active",
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "task-lease-acquire", **result}


def task_lease_inspect(
    *, cwd: Path, name: str, lease_id: str, owner_kind: str
) -> dict[str, Any]:
    primary, managed = _leased_anchor(cwd, name)
    manifest = load_manifest(primary, name)
    assert manifest is not None
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
    try:
        lease = inspect_task_lease(
            primary, name=name, token=lease_id, owner_kind=owner_kind
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    _record_manifest_lease(
        primary,
        name=name,
        owner_kind=owner_kind,
        token=lease_id,
        state=str(lease["state"]),
    )
    expected = {
        "branch": managed.branch,
        "worktree": str(managed.path),
        "common_dir": str(common_dir),
    }
    if any(lease.get(key) != value for key, value in expected.items()):
        raise WorktreeError("task lease no longer matches the managed outer checkout")
    if lease["state"] == "released":
        records = list_worktrees(primary)
        registered = {_canonical(Path(record.path)) for record in records}
        branches = set(
            _git(
                primary, "for-each-ref", "--format=%(refname:short)", "refs/heads/"
            ).splitlines()
        )
        resurrected = [
            f"{resource['path']} ({resource['branch']})"
            for resource in lease["resources"]
            if (
                Path(str(resource["path"])).resolve() in registered
                or Path(str(resource["path"])).exists()
                or Path(str(resource["path"])).is_symlink()
                or resource["branch"] in branches
            )
        ]
        if resurrected:
            raise WorktreeError(
                "released task lease resources reappeared: " + ", ".join(resurrected)
            )
    return {
        "action": "task-lease-inspect",
        "status": "valid",
        **lease,
        "primary": str(primary),
        "source_branch": manifest.source_branch,
        "outer_head": managed.head,
        "outer_clean": not status_paths(managed.path),
    }


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
    *,
    cwd: Path,
    name: str,
    lease_id: str,
    promoted_head: str,
    expected_head: str,
    owner_kind: str,
) -> dict[str, Any]:
    primary, managed = _leased_anchor(cwd, name)
    if managed.head != promoted_head:
        raise WorktreeError("outer worktree is not at the promoted task head")
    if status_paths(managed.path):
        raise WorktreeError("outer worktree must be clean before promotion is recorded")
    if not _is_ancestor(primary, expected_head, promoted_head):
        raise WorktreeError(
            "promoted task head does not descend from its expected head"
        )
    try:
        result = update_task_lease(
            primary,
            name=name,
            token=lease_id,
            owner_kind=owner_kind,
            promoted_head=promoted_head,
            expected_previous_head=expected_head,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {"action": "task-lease-promote", "status": "updated", **result}


def task_lease_release(
    *, cwd: Path, name: str, lease_id: str, promoted_head: str, owner_kind: str
) -> dict[str, Any]:
    primary, managed = _leased_anchor(cwd, name)
    manifest = load_manifest(primary, name)
    assert manifest is not None
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
    _record_manifest_lease(
        primary,
        name=name,
        owner_kind=owner_kind,
        token=lease_id,
        state="released",
    )
    return {
        "action": "task-lease-release",
        **result,
        "primary": str(primary),
        "source_branch": manifest.source_branch,
    }


def _task_lane_by_name(
    primary: Path, name: str
) -> tuple[dict[str, object], Manifest, ManagedWorktree]:
    _validate_name(name)
    manifest = load_manifest(primary, name)
    assert manifest is not None
    lane_id = _task_lane_id_for_branch(primary, manifest.branch)
    if lane_id is None:
        raise WorktreeError("managed worktree is not a Task Implementer lane")
    try:
        lane = load_lane(primary, lane_id)
    except TaskLaneStateError as error:
        raise WorktreeError(str(error)) from error
    assert lane is not None
    _, managed = _task_lane_live(primary, lane)
    return lane, manifest, managed


def _reset_task_lane_manifest_lease(
    primary: Path, manifest: Manifest, *, token: str
) -> Manifest:
    if manifest.lease_state == "none":
        return manifest
    if manifest.lease_token != token or manifest.lease_owner != "task-implementer":
        raise WorktreeError("Task Implementer lane lease marker is inconsistent")
    updated = manifest.updated(
        lease_state="none",
        lease_owner=None,
        lease_token=None,
    )
    try:
        write_manifest(primary, updated)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    return updated


def task_lane_generation_acquire(
    *,
    cwd: Path,
    workspace: Path,
    run_id: str,
    task_scope: str,
    initial_head: str,
) -> dict[str, Any]:
    anchor = inspect_managed_anchor(cwd=cwd)
    if anchor["status"] != "task-lane":
        raise WorktreeError("Task Implementer generation requires its persistent lane")
    primary = Path(str(anchor["primary"]))
    name = str(anchor["name"])
    try:
        with integration_transition_lock(primary, name):
            lane, manifest, managed = _task_lane_by_name(primary, name)
            if lane["state"] in {"integrating", "conflicted", "source-promoted"}:
                raise WorktreeError(
                    "Task Implementer lane integration must finish before another run"
                )
            if lane["state"] in {"creating", "recovery", "removing", "removed"}:
                raise WorktreeError(
                    f"Task Implementer lane cannot run from state {lane['state']}"
                )
            active = lane["active_generation"]
            if active is not None:
                if (
                    active["run_id"] != run_id
                    or active["workspace"] != str(workspace.resolve())
                    or active["initial_head"] != initial_head
                ):
                    raise WorktreeError(
                        "Task Implementer lane is already owned by another active run"
                    )
                result = acquire_task_lease(
                    primary,
                    name=name,
                    branch=managed.branch,
                    worktree=managed.path,
                    scope=managed.scope,
                    common_dir=Path(str(lane["common_dir"])),
                    workspace=workspace,
                    run_id=run_id,
                    task_scope=task_scope,
                    initial_head=initial_head,
                    owner_kind="task-implementer",
                )
                return {
                    "action": "task-lane-generation-acquire",
                    "generation": active["generation"],
                    "lane_id": lane["lane_id"],
                    **result,
                }
            if managed.head != initial_head or lane["lane_head"] != initial_head:
                raise WorktreeError(
                    "Task Implementer run baseline does not match the persistent lane"
                )
            if status_paths(managed.path):
                raise WorktreeError(
                    "Task Implementer lane must be completely clean before a run"
                )
            operation = _operation_in_progress(managed.path)
            if operation:
                raise WorktreeError(
                    f"Task Implementer lane operation is in progress: {operation}"
                )
            existing_lease = load_lease(primary, name)
            if existing_lease is not None:
                expected_lease = {
                    "owner_kind": "task-implementer",
                    "run_id": run_id,
                    "workspace": str(workspace.resolve()),
                    "task_scope": task_scope,
                    "initial_head": initial_head,
                    "branch": managed.branch,
                    "worktree": str(managed.path),
                    "scope": managed.scope,
                    "common_dir": str(lane["common_dir"]),
                    "state": "active",
                    "promoted_head": None,
                }
                recoverable = (
                    all(existing_lease.get(key) == value for key, value in expected_lease.items())
                    and existing_lease.get("resources") == []
                    and existing_lease.get("promotion_heads") == [initial_head]
                )
                if not recoverable:
                    raise WorktreeError(
                        "Task Implementer lane retains an unreconciled coordinator lease"
                    )
                token = str(existing_lease["token"])
                _record_manifest_lease(
                    primary,
                    name=name,
                    owner_kind="task-implementer",
                    token=token,
                    state="active",
                )
                generation = int(lane["latest_generation"]) + 1
                lane = {
                    **lane,
                    "state": "active",
                    "active_generation": {
                        "generation": generation,
                        "run_id": run_id,
                        "token": token,
                        "workspace": str(workspace.resolve()),
                        "initial_head": initial_head,
                    },
                }
                write_lane(primary, lane)
                return {
                    "action": "task-lane-generation-acquire",
                    "status": "recovered",
                    "state": "active",
                    "generation": generation,
                    "lane_id": lane["lane_id"],
                    **existing_lease,
                }
            if manifest.lease_state != "none":
                previous = int(lane["latest_generation"])
                receipt = (
                    load_generation(primary, str(lane["lane_id"]), previous)
                    if previous
                    else None
                )
                if receipt is None or receipt["token"] != manifest.lease_token:
                    raise WorktreeError(
                        "Task Implementer lane lease marker lacks its generation receipt"
                    )
                manifest = _reset_task_lane_manifest_lease(
                    primary, manifest, token=str(receipt["token"])
                )
            result = acquire_task_lease(
                primary,
                name=name,
                branch=managed.branch,
                worktree=managed.path,
                scope=managed.scope,
                common_dir=Path(str(lane["common_dir"])),
                workspace=workspace,
                run_id=run_id,
                task_scope=task_scope,
                initial_head=initial_head,
                owner_kind="task-implementer",
            )
            _record_manifest_lease(
                primary,
                name=name,
                owner_kind="task-implementer",
                token=str(result["token"]),
                state="active",
            )
            generation = int(lane["latest_generation"]) + 1
            lane = {
                **lane,
                "state": "active",
                "active_generation": {
                    "generation": generation,
                    "run_id": run_id,
                    "token": result["token"],
                    "workspace": str(workspace.resolve()),
                    "initial_head": initial_head,
                },
            }
            write_lane(primary, lane)
            return {
                "action": "task-lane-generation-acquire",
                "generation": generation,
                "lane_id": lane["lane_id"],
                **result,
            }
    except (InteropError, StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error


def task_lane_generation_inspect(
    *, cwd: Path, name: str, generation: int, lease_id: str
) -> dict[str, Any]:
    primary, _ = _leased_anchor(cwd, name)
    lane, _, managed = _task_lane_by_name(primary, name)
    active = lane["active_generation"]
    if active is not None and active["generation"] == generation:
        if active["token"] != lease_id:
            raise WorktreeError("Task Implementer generation token does not match")
        result = task_lease_inspect(
            cwd=cwd,
            name=name,
            lease_id=lease_id,
            owner_kind="task-implementer",
        )
        return {
            "action": "task-lane-generation-inspect",
            "lane_id": lane["lane_id"],
            "generation": generation,
            **result,
        }
    try:
        receipt = load_generation(primary, str(lane["lane_id"]), generation)
    except TaskLaneStateError as error:
        raise WorktreeError(str(error)) from error
    assert receipt is not None
    if receipt["token"] != lease_id:
        raise WorktreeError("Task Implementer generation token does not match")
    return {
        "action": "task-lane-generation-inspect",
        "status": "valid",
        "state": "released",
        "owner_kind": "task-implementer",
        "name": name,
        "branch": managed.branch,
        "worktree": str(managed.path),
        "scope": managed.scope,
        "common_dir": lane["common_dir"],
        "workspace": receipt["workspace"],
        "run_id": receipt["run_id"],
        "task_scope": managed.scope,
        "initial_head": receipt["initial_head"],
        "promoted_head": receipt["promoted_head"],
        "promotion_heads": [receipt["initial_head"], receipt["promoted_head"]],
        "token": receipt["token"],
        "resources": [],
        "primary": str(primary),
        "source_branch": lane["source_branch"],
        "outer_head": managed.head,
        "outer_clean": not status_paths(managed.path),
        "lane_id": lane["lane_id"],
        "generation": generation,
    }


def _claim_overlaps(left: dict[str, object], right: dict[str, object]) -> bool:
    if left["kind"] == "domain" or right["kind"] == "domain":
        return left["kind"] == right["kind"] and left["path"] == right["path"]
    left_path = PurePosixPath(str(left["path"]))
    right_path = PurePosixPath(str(right["path"]))
    if left["kind"] == "exact" and right["kind"] == "exact":
        return left_path == right_path
    if left["kind"] == "prefix" and right["kind"] == "prefix":
        return (
            left_path == right_path
            or left_path in right_path.parents
            or right_path in left_path.parents
        )
    prefix = left_path if left["kind"] == "prefix" else right_path
    exact = right_path if left["kind"] == "prefix" else left_path
    return exact == prefix or prefix in exact.parents


def task_lane_generation_claims(
    *,
    cwd: Path,
    name: str,
    generation: int,
    lease_id: str,
    claims: list[dict[str, str]],
) -> dict[str, Any]:
    primary, _ = _leased_anchor(cwd, name)
    try:
        with (
            task_lane_transition_lock(primary),
            integration_transition_lock(primary, name),
            interop_lock(primary),
        ):
            lane, _, _ = _task_lane_by_name(primary, name)
            active = lane["active_generation"]
            if (
                active is None
                or active["generation"] != generation
                or active["token"] != lease_id
            ):
                raise WorktreeError("Task Implementer generation is not active")
            normalized: list[dict[str, object]] = []
            existing_identities = {
                (str(item["kind"]), str(item["path"]), int(item["generation"]))
                for item in lane["claims"]
            }
            for item in claims:
                if set(item) != {"kind", "path"}:
                    raise WorktreeError("Task Implementer claim fields are invalid")
                candidate = {
                    "kind": item["kind"],
                    "path": item["path"],
                    "generation": generation,
                }
                identity = (str(item["kind"]), str(item["path"]), generation)
                if identity not in existing_identities:
                    normalized.append(candidate)
                    existing_identities.add(identity)
            candidate_lane = {**lane, "claims": [*lane["claims"], *normalized]}
            candidate_lane = validate_lane(
                candidate_lane, str(lane["lane_id"])
            )
            own_claims = [
                item
                for item in candidate_lane["claims"]
                if item["generation"] == generation
            ]
            for other in all_lanes(primary):
                if (
                    other["lane_id"] == lane["lane_id"]
                    or other["state"] in {"removed", "removing"}
                ):
                    continue
                for own_claim in own_claims:
                    for other_claim in other["claims"]:
                        if _claim_overlaps(own_claim, other_claim):
                            raise WorktreeError(
                                "Task Implementer repository claim conflicts with "
                                f"lane {other['lane_id']}: {own_claim['path']}"
                            )
            write_lane(primary, candidate_lane)
            return {
                "action": "task-lane-generation-claims",
                "status": "claimed",
                "lane_id": lane["lane_id"],
                "generation": generation,
                "claims": normalized,
            }
    except (InteropError, StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error


def task_lane_generation_release(
    *, cwd: Path, name: str, generation: int, lease_id: str, promoted_head: str
) -> dict[str, Any]:
    primary, _ = _leased_anchor(cwd, name)
    try:
        with (
            task_lane_transition_lock(primary),
            integration_transition_lock(primary, name),
        ):
            lane, manifest, managed = _task_lane_by_name(primary, name)
            active = lane["active_generation"]
            if active is None:
                receipt = load_generation(
                    primary, str(lane["lane_id"]), generation, required=False
                )
                if receipt is None or receipt["token"] != lease_id:
                    raise WorktreeError("Task Implementer generation is not active")
                lease = load_lease(primary, name)
                if lease is not None:
                    retire_released_task_lease(
                        primary, name=name, token=str(receipt["token"])
                    )
                _reset_task_lane_manifest_lease(
                    primary, manifest, token=str(receipt["token"])
                )
                return {
                    "action": "task-lane-generation-release",
                    "status": "already-released",
                    "state": "released",
                    "lane_id": lane["lane_id"],
                    "generation": generation,
                    **receipt,
                }
            if (
                active["generation"] != generation
                or active["token"] != lease_id
                or managed.head != promoted_head
            ):
                raise WorktreeError("Task Implementer generation release identity changed")
            released = task_lease_release(
                cwd=managed.path,
                name=name,
                lease_id=lease_id,
                promoted_head=promoted_head,
                owner_kind="task-implementer",
            )
            generation_claims = [
                item for item in lane["claims"] if item["generation"] == generation
            ]
            receipt: dict[str, object] = {
                "schema": GENERATION_SCHEMA,
                "kind": GENERATION_KIND,
                "lane_id": lane["lane_id"],
                "incarnation": lane["incarnation"],
                "generation": generation,
                "run_id": active["run_id"],
                "token": lease_id,
                "workspace": active["workspace"],
                "initial_head": active["initial_head"],
                "promoted_head": promoted_head,
                "claims": generation_claims,
                "status": "released",
            }
            write_generation(primary, receipt)
            lane = {
                **lane,
                "state": "pending",
                "latest_generation": generation,
                "active_generation": None,
                "pending_generations": [*lane["pending_generations"], generation],
                "lane_head": promoted_head,
            }
            write_lane(primary, lane)
            retire_released_task_lease(primary, name=name, token=lease_id)
            manifest = load_manifest(primary, name)
            assert manifest is not None
            _reset_task_lane_manifest_lease(primary, manifest, token=lease_id)
            return {
                "action": "task-lane-generation-release",
                "status": "released",
                "state": "released",
                "lane_id": lane["lane_id"],
                "generation": generation,
                **released,
            }
    except (InteropError, StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error


def _remove_local_branch(primary: Path, branch: str, *, expected_head: str) -> str:
    ref = f"refs/heads/{branch}"
    _run(["git", "update-ref", "-d", ref, expected_head], cwd=primary)
    _run(
        ["git", "config", "--local", "--remove-section", f"branch.{branch}"],
        cwd=primary,
        allowed=(0, 5, 128),
    )
    return "deleted-with-expected-old-value"


def _remove_local_branch_with_source_guard(
    primary: Path,
    branch: str,
    *,
    expected_head: str,
    source_ref: str,
    expected_source_head: str,
) -> str:
    """Delete one lane ref only while its source ref retains exact proof."""

    _validate_name(branch.removeprefix(BRANCH_PREFIX))
    if not branch.startswith(BRANCH_PREFIX):
        raise WorktreeError("Task Implementer lane branch is invalid")
    if (
        OBJECT_ID_RE.fullmatch(expected_head) is None
        or OBJECT_ID_RE.fullmatch(expected_source_head) is None
    ):
        raise WorktreeError("Task Implementer lane removal head is invalid")
    _run(["git", "check-ref-format", source_ref], cwd=primary)
    lane_ref = f"refs/heads/{branch}"
    _run(["git", "check-ref-format", lane_ref], cwd=primary)
    transaction = "\n".join(
        (
            "start",
            f"verify {source_ref} {expected_source_head}",
            f"delete {lane_ref} {expected_head}",
            "prepare",
            "commit",
            "",
        )
    )
    try:
        result = subprocess.run(
            ["git", "update-ref", "--stdin"],
            cwd=primary,
            env=_environment(),
            text=True,
            input=transaction,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise WorktreeError("could not run git update-ref") from error
    if result.returncode != 0:
        raise WorktreeError(
            "source ref moved or Task Implementer lane branch changed before "
            "atomic cleanup; the lane branch was retained"
        )
    _run(
        ["git", "config", "--local", "--remove-section", f"branch.{branch}"],
        cwd=primary,
        allowed=(0, 5, 128),
    )
    return "deleted-with-source-guard"


def _branch_head(primary: Path, branch: str) -> str | None:
    result = _run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=primary,
        allowed=(0, 128),
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repository,
        allowed=(0, 1),
    )
    return result.returncode == 0


def _merge_parents(repository: Path, commit: str) -> tuple[str, str]:
    fields = _git(repository, "rev-list", "--parents", "-n", "1", commit).split()
    if len(fields) != 3 or fields[0] != commit:
        raise WorktreeError("integration candidate is not an exact two-parent merge")
    return fields[1], fields[2]


def _integration_branch(name: str) -> str:
    return f"codex/worktree-integrate/{name}"


def _integration_path(primary: Path, name: str) -> Path:
    return state_directory(primary) / "integrations" / name


def _source_snapshot(primary: Path, manifest: Manifest) -> str:
    head = _source_identity_snapshot(primary, manifest)
    if status_paths(primary):
        raise WorktreeError("the primary source worktree must be completely clean")
    return head


def _source_identity_snapshot(primary: Path, manifest: Manifest) -> str:
    operation = _operation_in_progress(primary)
    if operation:
        raise WorktreeError(f"primary source operation is in progress: {operation}")
    branch = _git(primary, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1))
    if branch != manifest.source_branch:
        raise WorktreeError(
            "the primary checkout is no longer on the recorded source branch"
        )
    if branch == _configured_default_branch(primary):
        raise WorktreeError(
            "integration requires the recorded non-default source branch"
        )
    head = _git(primary, "rev-parse", "HEAD")
    ref_head = _git(primary, "rev-parse", "--verify", manifest.source_ref)
    if head != ref_head:
        raise WorktreeError("the checked-out source branch and source ref disagree")
    if not _is_ancestor(primary, manifest.base, head):
        raise WorktreeError(
            "the source branch no longer descends from the recorded creation base"
        )
    return head


def _integration_primary(cwd: Path) -> Path:
    primary, current_root = discover_repository(cwd)
    if current_root != primary:
        raise WorktreeError(
            "integration must be invoked from the primary checkout: " + str(primary)
        )
    return primary


def _direct_child_commit(repository: Path, before: str, after: str) -> bool:
    parents = _git(repository, "rev-list", "--parents", "-n", "1", after).split()
    return parents == [after, before]


def _orphan_candidate_blockers(primary: Path, name: str) -> list[str]:
    branch = _integration_branch(name)
    path = _integration_path(primary, name)
    branch_head = _branch_head(primary, branch)
    records = list_worktrees(primary)
    matching_records = [
        record
        for record in records
        if record.branch == branch or Path(record.path) == path
    ]
    blockers: list[str] = []
    if branch_head is not None:
        blockers.append("an orphan integration candidate branch already exists")
    if path.exists() or path.is_symlink():
        blockers.append("an orphan integration candidate path already exists")
    if matching_records:
        blockers.append("an orphan integration candidate worktree is registered")
    return blockers


def integration_preflight(
    *, cwd: Path, name: str, restart: bool = False
) -> dict[str, Any]:
    """Classify one integration without creating commits or lifecycle state."""

    primary = _integration_primary(cwd)
    _validate_name(name)
    try:
        manifest = load_manifest(primary, name)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    assert manifest is not None
    if _task_lane_id_for_branch(primary, manifest.branch) is not None:
        raise WorktreeError(
            "Task Implementer lanes integrate only through "
            "$task-implementer integrate"
        )
    if manifest.status not in {"active", "integrated"}:
        raise WorktreeError(
            f"managed worktree cannot integrate from status {manifest.status}"
        )

    record = _find_record(primary, primary, name=name, current_cwd=cwd)
    managed = _managed_from_record(primary, record)
    source_head = _source_identity_snapshot(primary, manifest)
    source_dirty = status_paths(primary)
    child_dirty = status_paths(managed.path)
    child_operation = _operation_in_progress(managed.path)
    reservation = load_reservation(primary, name)
    preparation = load_preparation(primary, name)
    blockers: list[str] = []

    try:
        lease = lifecycle_lease(primary, name)
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    nested_participation = lease is not None or manifest.lease_state != "none"
    if lease is not None:
        if lease["state"] == "active":
            blockers.append(f"{lease['owner_kind']} still owns the child worktree")
        else:
            _validate_outer_lease_receipt(
                primary,
                name=name,
                manifest=manifest,
                observed_head=managed.head,
            )

    if child_operation:
        blockers.append(f"child operation is in progress: {child_operation}")
    if not _is_ancestor(primary, manifest.base, managed.head):
        blockers.append("child branch no longer descends from its creation base")

    other_source_attempts = [
        item
        for item in active_reservations(primary)
        if item["name"] != name and item["source_ref"] == manifest.source_ref
    ]
    if other_source_attempts:
        blockers.append(
            "another managed child has an active integration into this source branch"
        )
    other_source_preparations = [
        item
        for item in active_preparations(primary)
        if item["name"] != name and item["source_ref"] == manifest.source_ref
    ]
    if other_source_preparations:
        blockers.append(
            "another managed child is preparing commits for this source branch"
        )
    if reservation is None:
        blockers.extend(_orphan_candidate_blockers(primary, name))

    mode = "fresh"
    if manifest.status == "integrated":
        mode = "reconcile"
        if source_dirty or child_dirty:
            blockers.append("an integrated lifecycle must be clean for reconciliation")
        if preparation is not None:
            blockers.append(
                "an integrated lifecycle retains an unexpected commit preparation"
            )
    elif reservation is not None:
        mode = "restart" if restart else "resume"
        if source_dirty:
            blockers.append("source is dirty during an active integration attempt")
        if child_dirty:
            blockers.append("child is dirty during an active integration attempt")
        source_already_promoted = reservation.get(
            "state"
        ) == "ready" and source_head == reservation.get("integration_head")
        if restart and source_already_promoted:
            blockers.append(
                "the ready candidate is already source HEAD and must be reconciled"
            )
        if not restart:
            if (
                source_head != reservation["source_head"]
                and not source_already_promoted
            ):
                blockers.append("source moved during the active integration attempt")
            if managed.head != reservation["child_head"]:
                blockers.append("child moved during the active integration attempt")
    elif preparation is not None:
        mode = "prepare"
        expected_identity = {
            "branch": managed.branch,
            "worktree": str(managed.path),
            "source_branch": manifest.source_branch,
            "source_ref": manifest.source_ref,
        }
        if any(
            preparation.get(key) != value for key, value in expected_identity.items()
        ):
            blockers.append("integration preparation identity changed")
        commits = {
            str(commit["target"]): commit for commit in preparation["commits"]
        }
        repositories = {"child": managed.path, "source": primary}
        observed_heads = {"child": managed.head, "source": source_head}
        dirty_paths = {"child": child_dirty, "source": source_dirty}
        for target, commit in commits.items():
            repository = repositories[target]
            if (
                observed_heads[target] != commit["after_head"]
                or not _direct_child_commit(
                    repository,
                    str(commit["before_head"]),
                    str(commit["after_head"]),
                )
                or _git(repository, "rev-parse", f"{commit['after_head']}^{{tree}}")
                != commit["commit_tree"]
            ):
                blockers.append(f"recorded preparatory {target} commit changed")
            if commit["status"] != "verified":
                blockers.append(
                    f"preparatory {target} commit requires actual-commit review"
                )
            if dirty_paths[target]:
                blockers.append(
                    f"{target} changed after its preparatory commit was created"
                )
        completed_targets = set(commits)
        remaining_targets = [
            target
            for target in preparation["commit_order"]
            if target not in completed_targets
        ]
        for target in remaining_targets:
            if observed_heads[target] != preparation[f"{target}_head"]:
                blockers.append(
                    f"unrecorded preparatory {target} commit requires review"
                )
            elif not dirty_paths[target]:
                blockers.append(
                    f"prepared {target} changes disappeared before commit"
                )
        for target in {"child", "source"} - set(preparation["commit_order"]):
            if dirty_paths[target]:
                blockers.append(
                    f"new {target} changes appeared after commit preparation"
                )
        if restart:
            blockers.append(
                "commit preparation must be explicitly aborted before restart"
            )
    elif restart:
        blockers.append("there is no active integration attempt to restart")

    commit_order: list[str] = []
    if preparation is not None and not blockers:
        completed_targets = {
            str(commit["target"]) for commit in preparation["commits"]
        }
        commit_order = [
            target
            for target in preparation["commit_order"]
            if target not in completed_targets
        ]
    elif manifest.status == "active" and reservation is None and not blockers:
        if child_dirty:
            if nested_participation:
                blockers.append(
                    "nested workflow ownership binds the child to an exact head"
                )
            else:
                commit_order.append("child")
        elif managed.head == manifest.base:
            blockers.append("child branch has no committed work to integrate")
        if source_dirty:
            commit_order.append("source")

    status = (
        "blocked" if blockers else "commit-required" if commit_order else "ready-clean"
    )
    if status == "ready-clean":
        next_action = "run the clean-only integration with these exact expected heads"
    elif status == "commit-required":
        next_action = (
            "commit the eligible checkouts in commit_order, then rerun this preflight"
        )
    else:
        next_action = "resolve the reported blockers without creating a candidate"

    return {
        "action": "integration-preflight",
        "status": status,
        "mode": mode,
        "name": name,
        "primary": str(primary),
        "source_branch": manifest.source_branch,
        "source_ref": manifest.source_ref,
        "source_head": source_head,
        "source_dirty_paths": source_dirty,
        "child_branch": managed.branch,
        "child_worktree": str(managed.path),
        "child_head": managed.head,
        "child_dirty_paths": child_dirty,
        "nested_participation": nested_participation,
        "active_reservation": reservation is not None,
        "active_preparation": preparation is not None,
        "preparation_token": preparation["token"] if preparation is not None else None,
        "preparation_commits": (
            preparation["commits"] if preparation is not None else []
        ),
        "commit_order": commit_order,
        "blockers": blockers,
        "next_action": next_action,
    }


def _preparation_checkout(
    primary: Path, manifest: Manifest, managed: ManagedWorktree, target: str
) -> tuple[Path, str]:
    if target == "child":
        return managed.path, managed.branch
    if target == "source":
        return primary, manifest.source_branch
    raise WorktreeError("integration commit target must be child or source")


def _integration_commit_unlocked(
    *,
    cwd: Path,
    name: str,
    target: str,
    expected_head: str,
    expected_tree: str,
    message: str,
    preparation_token: str | None = None,
) -> dict[str, Any]:
    """Create one exact reviewed preparatory commit behind a durable claim."""

    primary = _integration_primary(cwd)
    _validate_name(name)
    if re.fullmatch(r"[0-9a-f]{40,64}", expected_head) is None:
        raise WorktreeError("expected integration commit head is invalid")
    if re.fullmatch(r"[0-9a-f]{40,64}", expected_tree) is None:
        raise WorktreeError("reviewed staged tree is invalid")
    if not message.strip() or "\n" in message or "\r" in message:
        raise WorktreeError("integration commit message must be one non-empty line")
    if len(message) > 200:
        raise WorktreeError("integration commit message is too long")

    preflight = integration_preflight(cwd=primary, name=name)
    if preflight["status"] != "commit-required" or not preflight["commit_order"]:
        blockers = "; ".join(str(value) for value in preflight["blockers"])
        raise WorktreeError(
            blockers or "integration preflight does not permit a commit"
        )
    if preflight["commit_order"][0] != target:
        raise WorktreeError("integration commits must follow the preflight commit order")
    current_preparation = load_preparation(primary, name)
    if current_preparation is None:
        initial_source = str(preflight["source_head"])
        initial_child = str(preflight["child_head"])
        claim_order = list(preflight["commit_order"])
    else:
        initial_source = str(current_preparation["source_head"])
        initial_child = str(current_preparation["child_head"])
        claim_order = list(current_preparation["commit_order"])
    try:
        preparation = begin_integration_preparation(
            primary,
            name=name,
            branch=str(preflight["child_branch"]),
            worktree=Path(str(preflight["child_worktree"])),
            source_branch=str(preflight["source_branch"]),
            source_ref=str(preflight["source_ref"]),
            source_head=initial_source,
            child_head=initial_child,
            commit_order=claim_order,
            preparation_token=preparation_token,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error

    manifest = load_manifest(primary, name)
    assert manifest is not None
    record = _find_record(primary, primary, name=name, current_cwd=primary)
    managed = _managed_from_record(primary, record)
    claimed_preflight = integration_preflight(cwd=primary, name=name)
    if (
        claimed_preflight["status"] != "commit-required"
        or not claimed_preflight["commit_order"]
        or claimed_preflight["commit_order"][0] != target
        or claimed_preflight["preparation_token"] != preparation["token"]
    ):
        raise WorktreeError("integration commit eligibility changed after claiming it")
    checkout, expected_branch = _preparation_checkout(
        primary, manifest, managed, target
    )
    if _operation_in_progress(checkout):
        raise WorktreeError(f"{target} Git operation started before commit")
    branch = _git(checkout, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1))
    head = _git(checkout, "rev-parse", "HEAD")
    if branch != expected_branch or head != expected_head:
        raise WorktreeError(f"{target} branch or HEAD changed before commit")
    if not status_paths(checkout):
        raise WorktreeError(f"{target} checkout no longer has changes to commit")

    _git(checkout, "add", "-A")
    _git(checkout, "diff", "--cached", "--check")
    staged_tree = _git(checkout, "write-tree")
    if staged_tree != expected_tree:
        raise WorktreeError(
            f"{target} staged tree changed after review; inspect and retry"
        )
    _git(checkout, "commit", "-m", message)
    after_head = _git(checkout, "rev-parse", "HEAD")
    if not _direct_child_commit(checkout, head, after_head):
        raise WorktreeError(f"{target} commit is not one direct child of preflight HEAD")
    commit_tree = _git(checkout, "rev-parse", f"{after_head}^{{tree}}")
    try:
        preparation = record_integration_preparation_commit(
            primary,
            name=name,
            preparation_token=str(preparation["token"]),
            target=target,
            before_head=head,
            after_head=after_head,
            reviewed_tree=staged_tree,
            commit_tree=commit_tree,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    clean = not status_paths(checkout) and _operation_in_progress(checkout) is None
    same_branch = (
        _git(checkout, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1))
        == expected_branch
    )
    tree_verified = commit_tree == staged_tree
    status = "committed" if clean and same_branch and tree_verified else "review-required"
    return {
        "action": "integration-commit",
        "status": status,
        "name": name,
        "target": target,
        "branch": expected_branch,
        "before_head": head,
        "commit_head": after_head,
        "reviewed_tree": staged_tree,
        "commit_tree": commit_tree,
        "preparation_token": preparation["token"],
        "clean": clean,
        "same_branch": same_branch,
        "tree_verified": tree_verified,
        "next_action": (
            "rerun integration preflight"
            if status == "committed"
            else "review the actual commit and checkout before continuing"
        ),
    }


def integration_commit(
    *,
    cwd: Path,
    name: str,
    target: str,
    expected_head: str,
    expected_tree: str,
    message: str,
    preparation_token: str | None = None,
) -> dict[str, Any]:
    primary = _integration_primary(cwd)
    try:
        with integration_transition_lock(primary, name):
            return _integration_commit_unlocked(
                cwd=cwd,
                name=name,
                target=target,
                expected_head=expected_head,
                expected_tree=expected_tree,
                message=message,
                preparation_token=preparation_token,
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _integration_commit_review_unlocked(
    *,
    cwd: Path,
    name: str,
    target: str,
    preparation_token: str,
    commit_head: str,
    commit_tree: str,
) -> dict[str, Any]:
    """Acknowledge an exact actual commit only after external review."""

    primary = _integration_primary(cwd)
    preparation = load_preparation(primary, name)
    if preparation is None:
        raise WorktreeError("integration preparation is missing")
    manifest = load_manifest(primary, name)
    assert manifest is not None
    record = _find_record(primary, primary, name=name, current_cwd=primary)
    managed = _managed_from_record(primary, record)
    checkout, expected_branch = _preparation_checkout(
        primary, manifest, managed, target
    )
    if (
        _operation_in_progress(checkout)
        or status_paths(checkout)
        or _git(checkout, "symbolic-ref", "-q", "--short", "HEAD", allowed=(0, 1))
        != expected_branch
        or _git(checkout, "rev-parse", "HEAD") != commit_head
        or _git(checkout, "rev-parse", f"{commit_head}^{{tree}}") != commit_tree
    ):
        raise WorktreeError("actual preparatory commit changed before review approval")
    try:
        approved = approve_integration_preparation_commit(
            primary,
            name=name,
            preparation_token=preparation_token,
            target=target,
            commit_head=commit_head,
            commit_tree=commit_tree,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {
        "action": "integration-commit-review",
        "status": "verified",
        "name": name,
        "target": target,
        "commit_head": commit_head,
        "commit_tree": commit_tree,
        "preparation_token": approved["token"],
    }


def integration_commit_review(
    *,
    cwd: Path,
    name: str,
    target: str,
    preparation_token: str,
    commit_head: str,
    commit_tree: str,
) -> dict[str, Any]:
    primary = _integration_primary(cwd)
    try:
        with integration_transition_lock(primary, name):
            return _integration_commit_review_unlocked(
                cwd=cwd,
                name=name,
                target=target,
                preparation_token=preparation_token,
                commit_head=commit_head,
                commit_tree=commit_tree,
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _integration_preparation_abort_unlocked(
    *, cwd: Path, name: str, preparation_token: str
) -> dict[str, Any]:
    """Drop only the preparation claim while retaining every Git commit."""

    primary = _integration_primary(cwd)
    if _orphan_candidate_blockers(primary, name):
        raise WorktreeError("candidate resources exist; preserve them for recovery")
    try:
        result = abort_integration_preparation(
            primary, name=name, preparation_token=preparation_token
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {
        "action": "integration-preparation-abort",
        **result,
        "next_action": "rerun integration preflight; retained commits are unchanged",
    }


def integration_preparation_abort(
    *, cwd: Path, name: str, preparation_token: str
) -> dict[str, Any]:
    primary = _integration_primary(cwd)
    try:
        with integration_transition_lock(primary, name):
            return _integration_preparation_abort_unlocked(
                cwd=cwd,
                name=name,
                preparation_token=preparation_token,
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _candidate_record(
    primary: Path, branch: str, expected_path: Path
) -> WorktreeRecord | None:
    records = [record for record in list_worktrees(primary) if record.branch == branch]
    if len(records) > 1:
        raise WorktreeError("integration branch is checked out more than once")
    if not records:
        return None
    record = records[0]
    if _canonical(Path(record.path)) != _canonical(expected_path):
        raise WorktreeError("integration worktree path changed from durable state")
    return record


def _prepare_candidate(
    primary: Path, reservation: dict[str, object]
) -> dict[str, object]:
    name = str(reservation["name"])
    branch = str(reservation["integration_branch"])
    path = Path(str(reservation["integration_worktree"]))
    source_head = str(reservation["source_head"])
    parent = path.parent
    if parent.is_symlink():
        raise WorktreeError(f"integration parent must not be a symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = _candidate_record(primary, branch, path) if path.exists() else None
    branch_head = _branch_head(primary, branch)
    if record is None:
        if path.exists() or path.is_symlink():
            raise WorktreeError("unregistered integration worktree path already exists")
        if branch_head is None:
            _git(
                primary,
                "worktree",
                "add",
                "--no-track",
                "-b",
                branch,
                str(path),
                source_head,
            )
        else:
            if branch_head != source_head:
                raise WorktreeError("integration branch advanced during preparation")
            _git(primary, "worktree", "add", str(path), branch)
        record = _candidate_record(primary, branch, path)
    if record is None:
        raise WorktreeError("integration worktree could not be re-observed")
    if (
        _git(path, "rev-parse", "HEAD") != source_head
        or status_paths(path)
        or _operation_in_progress(path)
    ):
        raise WorktreeError("prepared integration worktree is not clean at source HEAD")
    try:
        return update_integration(
            primary,
            name=name,
            reservation_id=str(reservation["token"]),
            state="present",
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _seal_candidate(primary: Path, reservation: dict[str, object]) -> dict[str, object]:
    path = Path(str(reservation["integration_worktree"]))
    source_head = str(reservation["source_head"])
    child_head = str(reservation["child_head"])
    operation = _operation_in_progress(path)
    if operation is None:
        if _git(path, "rev-parse", "HEAD") != source_head or status_paths(path):
            raise WorktreeError("integration candidate changed before merge")
        result = _run(
            ["git", "merge", "--no-ff", "--no-commit", child_head],
            cwd=path,
            allowed=(0, 1),
        )
        if result.returncode == 1:
            return {
                **reservation,
                "status": "conflict",
                "recovery_worktree": str(path),
                "next_action": "resolve conflicts, stage the resolution, and repeat $worktree integrate",
            }
        _git(path, "commit", "--no-edit")
    elif operation == "MERGE_HEAD":
        merge_head = _git(path, "rev-parse", "MERGE_HEAD")
        if merge_head != child_head:
            raise WorktreeError("integration recovery MERGE_HEAD changed")
        if _git(path, "diff", "--name-only", "--diff-filter=U"):
            return {
                **reservation,
                "status": "conflict",
                "recovery_worktree": str(path),
                "next_action": "resolve conflicts, stage the resolution, and repeat $worktree integrate",
            }
        _git(path, "commit", "--no-edit")
    else:
        raise WorktreeError(
            f"unexpected integration operation is in progress: {operation}"
        )
    candidate = _git(path, "rev-parse", "HEAD")
    first_parent, second_parent = _merge_parents(path, candidate)
    if first_parent != source_head or second_parent != child_head:
        raise WorktreeError("integration merge parents do not match durable state")
    if status_paths(path) or _operation_in_progress(path):
        raise WorktreeError("sealed integration candidate is not clean")
    try:
        return update_integration(
            primary,
            name=str(reservation["name"]),
            reservation_id=str(reservation["token"]),
            state="ready",
            integration_head=candidate,
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _remove_integration_candidate(
    primary: Path,
    reservation: dict[str, object],
    *,
    abort_merge: bool,
) -> None:
    path = Path(str(reservation["integration_worktree"]))
    branch = str(reservation["integration_branch"])
    expected_head = str(
        reservation["integration_head"]
        if reservation["state"] == "ready"
        else reservation["source_head"]
    )
    record = _candidate_record(primary, branch, path) if path.exists() else None
    if record is not None:
        operation = _operation_in_progress(path)
        if operation == "MERGE_HEAD" and abort_merge:
            _git(path, "merge", "--abort")
            operation = _operation_in_progress(path)
        if operation:
            raise WorktreeError(
                f"integration recovery remains in progress: {operation}"
            )
        if status_paths(path):
            raise WorktreeError(
                "integration recovery worktree is dirty; preserve or clean it before restart"
            )
        observed_head = _git(path, "rev-parse", "HEAD")
        branch_head = _branch_head(primary, branch)
        if observed_head != expected_head or branch_head != expected_head:
            raise WorktreeError(
                "integration candidate branch advanced beyond durable state; "
                "preserve it for manual recovery"
            )
        _git(primary, "worktree", "remove", str(path))
    elif path.exists() or path.is_symlink():
        raise WorktreeError("unregistered integration recovery path remains")
    branch_head = _branch_head(primary, branch)
    if branch_head is not None:
        if branch_head != expected_head:
            raise WorktreeError(
                "integration candidate branch advanced beyond durable state; "
                "preserve it for manual recovery"
            )
        _remove_local_branch(primary, branch, expected_head=expected_head)


def _integrate_worktree_unlocked(
    *,
    cwd: Path,
    name: str,
    validated_head: str | None,
    restart: bool,
    expected_source_head: str | None,
    expected_child_head: str | None,
    preparation_token: str | None,
) -> dict[str, Any]:
    primary = _integration_primary(cwd)
    _validate_name(name)
    try:
        manifest = load_manifest(primary, name)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    assert manifest is not None
    if manifest.status == "integrated":
        reservation = load_reservation(primary, name)
        if reservation is not None:
            _remove_integration_candidate(primary, reservation, abort_merge=False)
            try:
                end_integration(
                    primary,
                    name=name,
                    reservation_id=str(reservation["token"]),
                )
            except InteropError as error:
                raise WorktreeError(str(error)) from error
        return {
            "action": "integrate",
            "status": "already-integrated",
            "name": name,
            "source_branch": manifest.source_branch,
            "source_head": manifest.integration_head,
            "child_head": manifest.integration_child_head,
        }
    if manifest.status != "active":
        raise WorktreeError(
            f"managed worktree cannot integrate from status {manifest.status}"
        )
    reservation = load_reservation(primary, name)
    preparation = load_preparation(primary, name)
    if reservation is None:
        candidate_blockers = _orphan_candidate_blockers(primary, name)
        if candidate_blockers:
            raise WorktreeError("; ".join(candidate_blockers))
    record = _find_record(primary, primary, name=name, current_cwd=cwd)
    managed = _managed_from_record(primary, record)
    _validate_outer_lease_receipt(
        primary,
        name=name,
        manifest=manifest,
        observed_head=managed.head,
    )
    if reservation is None and preparation is None:
        try:
            with interop_lock(primary):
                assert_idle(primary, name)
        except InteropError as error:
            raise WorktreeError(str(error)) from error
    operation = _operation_in_progress(managed.path)
    if operation:
        raise WorktreeError(f"child worktree operation is in progress: {operation}")
    dirty = status_paths(managed.path)
    if dirty:
        raise WorktreeError(
            "child worktree must be completely clean before integration: "
            + ", ".join(dirty)
        )
    child_head = managed.head
    if not _is_ancestor(primary, manifest.base, child_head):
        raise WorktreeError("child branch no longer descends from its creation base")
    if child_head == manifest.base:
        raise WorktreeError("child branch has no committed work to integrate")
    source_head = _source_snapshot(primary, manifest)
    if expected_source_head is None or expected_child_head is None:
        raise WorktreeError(
            "integration requires exact source and child heads returned by "
            "integration-preflight"
        )
    if source_head != expected_source_head:
        raise WorktreeError(
            "source HEAD changed after integration preflight: "
            f"{source_head} != {expected_source_head}"
        )
    if child_head != expected_child_head:
        raise WorktreeError(
            "child HEAD changed after integration preflight: "
            f"{child_head} != {expected_child_head}"
        )
    if reservation is not None and preparation is not None:
        try:
            reservation = begin_integration(
                primary,
                name=name,
                branch=managed.branch,
                worktree=managed.path,
                source_branch=manifest.source_branch,
                source_ref=manifest.source_ref,
                source_head=source_head,
                child_head=child_head,
                integration_branch=_integration_branch(name),
                integration_worktree=_integration_path(primary, name),
                preparation_token=preparation_token,
            )
        except InteropError as error:
            raise WorktreeError(str(error)) from error
    if restart:
        if reservation is None:
            raise WorktreeError("there is no active integration attempt to restart")
        if reservation.get("state") == "ready" and source_head == reservation.get(
            "integration_head"
        ):
            raise WorktreeError(
                "the integration candidate is already the source HEAD and must be reconciled, not restarted"
            )
        _remove_integration_candidate(primary, reservation, abort_merge=True)
        try:
            end_integration(
                primary,
                name=name,
                reservation_id=str(reservation["token"]),
            )
        except InteropError as error:
            raise WorktreeError(str(error)) from error
        return _integrate_worktree_unlocked(
            cwd=cwd,
            name=name,
            validated_head=None,
            restart=False,
            expected_source_head=source_head,
            expected_child_head=child_head,
            preparation_token=None,
        )
    if reservation is not None:
        source_already_promoted = reservation.get(
            "state"
        ) == "ready" and source_head == reservation.get("integration_head")
        if source_head != reservation["source_head"] and not source_already_promoted:
            raise WorktreeError(
                "source moved during integration; rerun with --restart after reviewing the retained recovery attempt"
            )
        if child_head != reservation["child_head"]:
            raise WorktreeError("child branch advanced during integration")
    else:
        reobserved_source = _source_snapshot(primary, manifest)
        reobserved_child = _git(managed.path, "rev-parse", "HEAD")
        if (
            reobserved_source != source_head
            or reobserved_child != child_head
            or status_paths(managed.path)
            or _operation_in_progress(managed.path)
        ):
            raise WorktreeError(
                "source or child changed after integration preflight; rerun preflight"
            )
        integration_branch = _integration_branch(name)
        integration_path = _integration_path(primary, name)
        try:
            reservation = begin_integration(
                primary,
                name=name,
                branch=managed.branch,
                worktree=managed.path,
                source_branch=manifest.source_branch,
                source_ref=manifest.source_ref,
                source_head=source_head,
                child_head=child_head,
                integration_branch=integration_branch,
                integration_worktree=integration_path,
                preparation_token=preparation_token,
            )
        except InteropError as error:
            raise WorktreeError(str(error)) from error
    if reservation["state"] == "planned":
        reservation = _prepare_candidate(primary, reservation)
    if reservation["state"] == "present":
        reservation = _seal_candidate(primary, reservation)
        if reservation.get("status") == "conflict":
            return {"action": "integrate", **reservation}
    candidate = str(reservation["integration_head"])
    candidate_path = Path(str(reservation["integration_worktree"]))
    first_parent, second_parent = _merge_parents(candidate_path, candidate)
    candidate_worktree_head = _git(candidate_path, "rev-parse", "HEAD")
    candidate_ref_head = _git(
        primary,
        "rev-parse",
        "--verify",
        str(reservation["integration_branch"]),
    )
    if (
        first_parent != reservation["source_head"]
        or second_parent != reservation["child_head"]
        or candidate_worktree_head != candidate
        or candidate_ref_head != candidate
        or status_paths(candidate_path)
        or _operation_in_progress(candidate_path)
    ):
        raise WorktreeError("ready integration candidate failed exact verification")
    if validated_head is None:
        return {
            "action": "integrate",
            "status": "validation-required",
            "name": name,
            "source_branch": manifest.source_branch,
            "source_head": reservation["source_head"],
            "child_head": reservation["child_head"],
            "candidate_head": candidate,
            "candidate_worktree": str(candidate_path),
            "next_action": "run non-mutating combined alignment and tests, then repeat with --validated-head",
        }
    if validated_head != candidate:
        raise WorktreeError(
            "validated head does not match the exact integration candidate"
        )
    observed_source = _source_snapshot(primary, manifest)
    if observed_source not in {
        reservation["source_head"],
        reservation["integration_head"],
    }:
        raise WorktreeError("source moved after candidate validation")
    if _git(managed.path, "rev-parse", "HEAD") != reservation["child_head"]:
        raise WorktreeError("child moved after candidate validation")
    try:
        promotion = promote_ff_only(
            primary,
            expected_branch=manifest.source_branch,
            expected_base=str(reservation["source_head"]),
            target=candidate,
        )
    except GitPromotionError as error:
        raise WorktreeError(str(error)) from error
    try:
        integrated = manifest.updated(
            status="integrated",
            expected_head=str(reservation["child_head"]),
            integration_source_head=str(reservation["source_head"]),
            integration_child_head=str(reservation["child_head"]),
            integration_head=candidate,
        )
        write_manifest(primary, integrated)
    except StateError as error:
        raise WorktreeError(str(error)) from error
    _remove_integration_candidate(primary, reservation, abort_merge=False)
    try:
        end_integration(
            primary,
            name=name,
            reservation_id=str(reservation["token"]),
        )
    except InteropError as error:
        raise WorktreeError(str(error)) from error
    return {
        "action": "integrate",
        "status": "integrated",
        "name": name,
        "source_branch": manifest.source_branch,
        "source_head": promotion["head"],
        "child_head": reservation["child_head"],
        "merge_head": candidate,
    }


def integrate_worktree(
    *,
    cwd: Path,
    name: str,
    validated_head: str | None,
    restart: bool,
    expected_source_head: str | None = None,
    expected_child_head: str | None = None,
    preparation_token: str | None = None,
) -> dict[str, Any]:
    primary = _integration_primary(cwd)
    _validate_name(name)
    manifest = load_manifest(primary, name)
    assert manifest is not None
    if _task_lane_id_for_branch(primary, manifest.branch) is not None:
        raise WorktreeError(
            "Task Implementer lanes integrate only through "
            "$task-implementer integrate"
        )
    try:
        with integration_transition_lock(primary, name):
            return _integrate_worktree_unlocked(
                cwd=cwd,
                name=name,
                validated_head=validated_head,
                restart=restart,
                expected_source_head=expected_source_head,
                expected_child_head=expected_child_head,
                preparation_token=preparation_token,
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def _task_lane_rearm(
    primary: Path, lane: dict[str, object]
) -> dict[str, Any]:
    manifest, managed = _task_lane_live(primary, lane)
    first_generation = int(lane["last_integrated_generation"]) + 1
    last_generation = int(lane["latest_generation"])
    if manifest.status == "active" and lane["state"] == "source-promoted":
        merge_head = manifest.base
        source_head = _git(primary, "rev-parse", str(lane["source_ref"]))
        if (
            managed.head != merge_head
            or manifest.expected_head != merge_head
            or status_paths(managed.path)
            or not _is_ancestor(primary, merge_head, source_head)
        ):
            raise WorktreeError(
                "Task Implementer lane rearm checkpoint cannot be reconciled"
            )
        lane = {
            **lane,
            "state": "idle",
            "base_head": merge_head,
            "lane_head": merge_head,
            "last_integrated_generation": lane["latest_generation"],
            "pending_generations": [],
            "claims": [],
            "integration": None,
        }
        write_lane(primary, lane)
        return {
            "action": "task-lane-integrate",
            "status": "integrated",
            "lane_id": lane["lane_id"],
            "name": lane["name"],
            "source_branch": lane["source_branch"],
            "source_head": merge_head,
            "lane_head": merge_head,
            "integrated_generations": list(
                range(first_generation, last_generation + 1)
            ),
            "last_integrated_generation": lane["last_integrated_generation"],
        }
    if manifest.status != "integrated" or manifest.integration_head is None:
        raise WorktreeError("Task Implementer lane has no promoted integration proof")
    merge_head = manifest.integration_head
    if managed.head != merge_head:
        if managed.head != manifest.integration_child_head:
            raise WorktreeError("Task Implementer lane moved before rearm")
        _git(managed.path, "merge", "--ff-only", merge_head)
    if _git(managed.path, "rev-parse", "HEAD") != merge_head or status_paths(
        managed.path
    ):
        raise WorktreeError("Task Implementer lane rearm failed exact verification")
    if not _is_ancestor(primary, merge_head, _git(primary, "rev-parse", lane["source_ref"])):
        raise WorktreeError("source no longer contains the Task Implementer integration")
    _write_config(primary, managed.branch, "base", merge_head)
    try:
        write_manifest(
            primary,
            manifest.updated(
                status="active",
                base=merge_head,
                expected_head=merge_head,
                integration_source_head=None,
                integration_child_head=None,
                integration_head=None,
                lease_state="none",
                lease_owner=None,
                lease_token=None,
            ),
        )
        lane = {
            **lane,
            "state": "idle",
            "base_head": merge_head,
            "lane_head": merge_head,
            "last_integrated_generation": lane["latest_generation"],
            "pending_generations": [],
            "claims": [],
            "integration": None,
        }
        write_lane(primary, lane)
    except (StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error
    return {
        "action": "task-lane-integrate",
        "status": "integrated",
        "lane_id": lane["lane_id"],
        "name": lane["name"],
        "source_branch": lane["source_branch"],
        "source_head": merge_head,
        "lane_head": merge_head,
        "integrated_generations": list(range(first_generation, last_generation + 1)),
        "last_integrated_generation": lane["last_integrated_generation"],
    }


def _task_lane_finish_no_change(
    primary: Path,
    lane: dict[str, object],
    manifest: Manifest,
    managed: ManagedWorktree,
) -> dict[str, Any]:
    """Reconcile a journaled no-change integration after any interruption."""

    journal = lane.get("integration")
    if not isinstance(journal, dict) or journal.get("phase") != "no-change":
        raise WorktreeError("Task Implementer no-change journal is unavailable")
    source_head = str(journal["source_head"])
    child_head = str(journal["child_head"])
    current_source = _git(
        primary, "rev-parse", "--verify", str(lane["source_ref"])
    )
    if current_source != source_head:
        raise WorktreeError(
            "source moved during Task Implementer no-change integration; retry "
            "with an explicit restart"
        )
    live_head = _git(managed.path, "rev-parse", "HEAD")
    if live_head not in {child_head, source_head}:
        raise WorktreeError(
            "Task Implementer lane moved during no-change integration"
        )
    if not _is_ancestor(primary, live_head, source_head):
        raise WorktreeError(
            "Task Implementer no-change lane no longer descends to the source"
        )
    if live_head != source_head:
        _git(managed.path, "merge", "--ff-only", source_head)
    if _git(managed.path, "rev-parse", "HEAD") != source_head or status_paths(
        managed.path
    ):
        raise WorktreeError(
            "Task Implementer no-change integration failed exact verification"
        )
    _write_config(primary, managed.branch, "base", source_head)
    try:
        write_manifest(
            primary,
            manifest.updated(base=source_head, expected_head=source_head),
        )
        integrated_generations = list(
            range(
                int(journal["first_generation"]),
                int(journal["last_generation"]) + 1,
            )
        )
        lane = {
            **lane,
            "state": "idle",
            "base_head": source_head,
            "lane_head": source_head,
            "last_integrated_generation": lane["latest_generation"],
            "pending_generations": [],
            "claims": [],
            "integration": None,
        }
        write_lane(primary, lane)
    except (StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error
    return {
        "action": "task-lane-integrate",
        "status": "integrated-no-change",
        "lane_id": lane["lane_id"],
        "name": lane["name"],
        "source_branch": lane["source_branch"],
        "source_head": source_head,
        "lane_head": source_head,
        "integrated_generations": integrated_generations,
        "last_integrated_generation": lane["last_integrated_generation"],
    }


def task_lane_integrate(
    *, cwd: Path, lane_id: str, validated_head: str | None, restart: bool
) -> dict[str, Any]:
    primary, _ = discover_repository(cwd)
    try:
        lane = load_lane(primary, lane_id)
    except TaskLaneStateError as error:
        raise WorktreeError(str(error)) from error
    assert lane is not None
    name = str(lane["name"])
    try:
        with integration_transition_lock(primary, name):
            lane = load_lane(primary, lane_id)
            assert lane is not None
            manifest, managed = _task_lane_live(primary, lane)
            if manifest.status == "integrated" or lane["state"] == "source-promoted":
                return _task_lane_rearm(primary, lane)
            if lane["state"] == "active" or lane["active_generation"] is not None:
                raise WorktreeError(
                    "active Task Implementer generation must finish before integration"
                )
            pending = list(lane["pending_generations"])
            if not pending:
                return {
                    "action": "task-lane-integrate",
                    "status": "already-integrated",
                    "lane_id": lane_id,
                    "name": name,
                    "source_branch": lane["source_branch"],
                    "source_head": _git(
                        primary, "rev-parse", "--verify", str(lane["source_ref"])
                    ),
                    "lane_head": managed.head,
                    "last_integrated_generation": lane[
                        "last_integrated_generation"
                    ],
                }
            if pending != list(
                range(
                    int(lane["last_integrated_generation"]) + 1,
                    int(lane["latest_generation"]) + 1,
                )
            ):
                raise WorktreeError("Task Implementer pending generation range is invalid")
            source_operation = _operation_in_progress(primary)
            child_operation = _operation_in_progress(managed.path)
            if source_operation or child_operation:
                raise WorktreeError(
                    "Git operation is in progress in the source or Task Implementer lane"
                )
            source_dirty = status_paths(primary)
            child_dirty = status_paths(managed.path)
            if source_dirty:
                raise WorktreeError(
                    "source checkout must be completely clean before Task Implementer "
                    "integration; run one fresh explicit $commit in the primary "
                    "checkout, review and commit the complete repository diff, then "
                    "repeat integrate. Dirty paths: "
                    + ", ".join(source_dirty)
                )
            if child_dirty:
                raise WorktreeError(
                    "Task Implementer lane must be completely clean before integration: "
                    + ", ".join(child_dirty)
                )
            source_head = _source_identity_snapshot(primary, manifest)
            journal = lane["integration"]
            if isinstance(journal, dict) and journal.get("phase") == "no-change":
                if journal["source_head"] != source_head:
                    if not restart:
                        raise WorktreeError(
                            "source moved during Task Implementer no-change "
                            "integration; retry with an explicit restart"
                        )
                    if not _is_ancestor(primary, managed.head, source_head):
                        raise WorktreeError(
                            "Task Implementer lane cannot restart no-change "
                            "integration from the moved source"
                        )
                    journal = {
                        **journal,
                        "source_head": source_head,
                        "child_head": managed.head,
                    }
                    lane = {**lane, "integration": journal}
                    write_lane(primary, lane)
                return _task_lane_finish_no_change(
                    primary, lane, manifest, managed
                )
            child_head = managed.head
            if child_head != lane["lane_head"]:
                raise WorktreeError("Task Implementer lane head changed before integration")
            # No-change generations are consumed without manufacturing an empty merge.
            if _is_ancestor(primary, child_head, source_head):
                journal = {
                    "first_generation": pending[0],
                    "last_generation": pending[-1],
                    "source_head": source_head,
                    "child_head": child_head,
                    "candidate_head": None,
                    "candidate_worktree": None,
                    "phase": "no-change",
                }
                lane = {**lane, "state": "integrating", "integration": journal}
                write_lane(primary, lane)
                return _task_lane_finish_no_change(
                    primary, lane, manifest, managed
                )
            if journal is None:
                journal = {
                    "first_generation": pending[0],
                    "last_generation": pending[-1],
                    "source_head": source_head,
                    "child_head": child_head,
                    "candidate_head": None,
                    "candidate_worktree": None,
                    "phase": "planned",
                }
                lane = {**lane, "state": "integrating", "integration": journal}
                write_lane(primary, lane)
            else:
                if (
                    journal["source_head"] != source_head
                    and not restart
                    and journal["phase"] != "source-promoted"
                ):
                    raise WorktreeError(
                        "source moved during Task Implementer integration; inspect "
                        "the retained candidate and retry with an explicit restart"
                    )
                if restart:
                    journal = {
                        **journal,
                        "source_head": source_head,
                        "child_head": child_head,
                        "candidate_head": None,
                        "candidate_worktree": None,
                        "phase": "planned",
                    }
                    lane = {**lane, "state": "integrating", "integration": journal}
                    write_lane(primary, lane)
                else:
                    child_head = str(journal["child_head"])
                    source_head = str(journal["source_head"])
            result = _integrate_worktree_unlocked(
                cwd=primary,
                name=name,
                validated_head=validated_head,
                restart=restart,
                expected_source_head=source_head,
                expected_child_head=child_head,
                preparation_token=None,
            )
            if result["status"] == "conflict":
                reservation = load_reservation(primary, name)
                journal = {
                    **journal,
                    "source_head": result["source_head"],
                    "child_head": result["child_head"],
                    "candidate_worktree": (
                        reservation["integration_worktree"] if reservation else None
                    ),
                    "phase": "conflicted",
                }
                write_lane(
                    primary,
                    {**lane, "state": "conflicted", "integration": journal},
                )
                return {"action": "task-lane-integrate", "lane_id": lane_id, **result}
            if result["status"] == "validation-required":
                journal = {
                    **journal,
                    "source_head": result["source_head"],
                    "child_head": result["child_head"],
                    "candidate_head": result["candidate_head"],
                    "candidate_worktree": result["candidate_worktree"],
                    "phase": "candidate-ready",
                }
                write_lane(
                    primary,
                    {**lane, "state": "integrating", "integration": journal},
                )
                return {"action": "task-lane-integrate", "lane_id": lane_id, **result}
            if result["status"] not in {"integrated", "already-integrated"}:
                raise WorktreeError("Task Implementer integration returned invalid state")
            lane = load_lane(primary, lane_id)
            assert lane is not None
            journal = {**lane["integration"], "phase": "source-promoted"}
            lane = {**lane, "state": "source-promoted", "integration": journal}
            write_lane(primary, lane)
            return _task_lane_rearm(primary, lane)
    except (InteropError, StateError, TaskLaneStateError) as error:
        raise WorktreeError(str(error)) from error


def _finalize_outer_removal_state(
    primary: Path,
    *,
    name: str,
    manifest: Manifest | None,
    receipt: dict[str, object] | None,
) -> None:
    """Delete receipt and manifest behind a durable exact removal intent."""

    try:
        if receipt is not None:
            prepare_lease_removal(primary, name, receipt)
        records = list_worktrees(primary)
        registered = {_canonical(Path(item.path)) for item in records}
        branches = set(
            _git(
                primary,
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/",
            ).splitlines()
        )
        delete_released_lease(
            primary,
            name,
            registered_worktrees=registered,
            existing_branches=branches,
            expected_lease=receipt,
        )
        if manifest is not None:
            delete_manifest(primary, name)
        if receipt is not None:
            records = list_worktrees(primary)
            registered = {_canonical(Path(item.path)) for item in records}
            branches = set(
                _git(
                    primary,
                    "for-each-ref",
                    "--format=%(refname:short)",
                    "refs/heads/",
                ).splitlines()
            )
            validate_released_resources(
                receipt,
                registered_worktrees=registered,
                existing_branches=branches,
            )
            if load_lease(primary, name) is not None:
                raise InteropError(
                    "task lease reappeared before removal intent deletion"
                )
            delete_lease_removal(
                primary,
                name,
                expected_receipt=receipt,
            )
    except (InteropError, StateError) as error:
        raise WorktreeError(str(error)) from error


def _remove_worktree_unlocked(
    *,
    cwd: Path,
    name: str | None,
    allow_task_lane: bool = False,
    task_lane_source_ref: str | None = None,
    task_lane_source_head: str | None = None,
) -> dict[str, Any]:
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
    if (
        manifest is not None
        and _task_lane_id_for_branch(primary, branch) is not None
        and not allow_task_lane
    ):
        raise WorktreeError(
            "Task Implementer lanes are removed only through "
            "$task-implementer workspace remove"
        )
    records = list_worktrees(primary)
    record = next((item for item in records if item.branch == branch), None)
    local_exists = _local_branch_exists(primary, branch)

    if record is None and not local_exists:
        try:
            if manifest is not None and load_lease_removal(primary, name) is None:
                assert_idle(primary, name)
        except InteropError as error:
            raise WorktreeError(str(error)) from error
        receipt = _validate_outer_lease_receipt(
            primary,
            name=name,
            manifest=manifest,
            for_removal=True,
        )
        _finalize_outer_removal_state(
            primary,
            name=name,
            manifest=manifest,
            receipt=receipt,
        )
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
    if load_lease_removal(primary, name) is None:
        try:
            assert_idle(primary, name)
        except InteropError as error:
            raise WorktreeError(str(error)) from error
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
    else:
        raise WorktreeError("managed local branch is missing")

    _validate_outer_lease_receipt(
        primary,
        name=name,
        manifest=manifest,
        observed_head=head,
        for_removal=True,
    )

    if manifest.status in {"planned", "recovery", "active"}:
        if head != manifest.base:
            raise WorktreeError(
                "unintegrated child commits must be integrated before removal"
            )
    elif manifest.status == "integrated":
        if (
            head != manifest.integration_child_head
            or manifest.integration_source_head is None
            or manifest.integration_head is None
        ):
            raise WorktreeError("integrated child no longer matches durable proof")
        first_parent, second_parent = _merge_parents(primary, manifest.integration_head)
        if (
            first_parent != manifest.integration_source_head
            or second_parent != manifest.integration_child_head
        ):
            raise WorktreeError("recorded local integration merge proof is invalid")
        source_head = _git(primary, "rev-parse", "--verify", manifest.source_ref)
        if not _is_ancestor(primary, manifest.integration_head, source_head):
            raise WorktreeError(
                "source history no longer contains the recorded local integration"
            )
    else:
        raise WorktreeError(
            f"managed worktree cannot be removed from status {manifest.status}"
        )

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
        if task_lane_source_ref is not None or task_lane_source_head is not None:
            if (
                not allow_task_lane
                or task_lane_source_ref is None
                or task_lane_source_head is None
            ):
                raise WorktreeError("Task Implementer removal guard is incomplete")
            local_result = _remove_local_branch_with_source_guard(
                primary,
                branch,
                expected_head=head,
                source_ref=task_lane_source_ref,
                expected_source_head=task_lane_source_head,
            )
        else:
            local_result = _remove_local_branch(
                primary, branch, expected_head=head
            )

    receipt = _validate_outer_lease_receipt(
        primary,
        name=name,
        manifest=manifest,
        observed_head=head,
        for_removal=True,
    )
    _finalize_outer_removal_state(
        primary,
        name=name,
        manifest=manifest,
        receipt=receipt,
    )

    return {
        "action": "remove",
        "status": "removed",
        "name": name,
        "branch": branch,
        "head": head,
        "worktree": removed_path,
        "local_branch": local_result,
        "integration_head": manifest.integration_head,
    }


def remove_worktree(*, cwd: Path, name: str | None) -> dict[str, Any]:
    primary, current_root = discover_repository(cwd)
    if current_root != primary or name is None:
        return _remove_worktree_unlocked(cwd=cwd, name=name, allow_task_lane=False)
    _validate_name(name)
    try:
        with interop_lock(primary):
            return _remove_worktree_unlocked(
                cwd=cwd, name=name, allow_task_lane=False
            )
    except InteropError as error:
        raise WorktreeError(str(error)) from error


def task_lane_remove(*, cwd: Path, lane_id: str) -> dict[str, Any]:
    primary, _ = discover_repository(cwd)
    try:
        lane = load_lane(primary, lane_id)
    except TaskLaneStateError as error:
        raise WorktreeError(str(error)) from error
    assert lane is not None
    if lane["state"] == "removed":
        return {
            "action": "task-lane-remove",
            "status": "already-removed",
            "lane_id": lane_id,
            "incarnation": lane["incarnation"],
        }
    name = str(lane["name"])
    try:
        with integration_transition_lock(primary, name):
            lane = load_lane(primary, lane_id)
            assert lane is not None
            if lane["state"] == "removed":
                return {
                    "action": "task-lane-remove",
                    "status": "already-removed",
                    "lane_id": lane_id,
                    "incarnation": lane["incarnation"],
                }
            if lane["state"] == "removing":
                removal = lane.get("removal") or {}
                removal_head = removal.get("head")
                result = _remove_worktree_unlocked(
                    cwd=primary,
                    name=name,
                    allow_task_lane=True,
                    task_lane_source_ref=str(removal.get("source_ref")),
                    task_lane_source_head=str(removal.get("source_head")),
                )
                lane = {**lane, "state": "removed", "removal": None}
                write_lane(primary, lane)
                return {
                    "action": "task-lane-remove",
                    "status": "removed",
                    "lane_id": lane_id,
                    "incarnation": lane["incarnation"],
                    "head": removal_head,
                    "worktree": result.get("worktree"),
                    "branch": result.get("branch"),
                }
            if (
                lane["state"] != "idle"
                or lane["active_generation"] is not None
                or lane["pending_generations"]
                or lane["claims"]
                or lane["integration"] is not None
            ):
                raise WorktreeError(
                    "Task Implementer lane must be idle and fully integrated before removal"
                )
            _, managed = _task_lane_live(primary, lane)
            if status_paths(managed.path):
                raise WorktreeError(
                    "Task Implementer lane is dirty and will not be force-removed"
                )
            operation = _operation_in_progress(managed.path)
            if operation:
                raise WorktreeError(
                    f"Task Implementer lane operation is in progress: {operation}"
                )
            head = managed.head
            if head != lane["lane_head"]:
                raise WorktreeError("Task Implementer lane moved before removal")
            source_head = _git(primary, "rev-parse", "--verify", str(lane["source_ref"]))
            if not _is_ancestor(primary, head, source_head):
                raise WorktreeError(
                    "source history does not contain the Task Implementer lane head"
                )
            lane = {
                **lane,
                "state": "removing",
                "removal": {
                    "head": head,
                    "source_ref": lane["source_ref"],
                    "source_head": source_head,
                    "phase": "planned",
                },
            }
            write_lane(primary, lane)
            result = _remove_worktree_unlocked(
                cwd=primary,
                name=name,
                allow_task_lane=True,
                task_lane_source_ref=str(lane["source_ref"]),
                task_lane_source_head=source_head,
            )
            lane = {
                **lane,
                "state": "removed",
                "removal": None,
            }
            write_lane(primary, lane)
            return {
                "action": "task-lane-remove",
                "status": "removed",
                "lane_id": lane_id,
                "incarnation": lane["incarnation"],
                "head": head,
                "worktree": result.get("worktree"),
                "branch": result.get("branch"),
            }
    except (InteropError, StateError, TaskLaneStateError) as error:
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
        help=(
            "public-safe lowercase task slug; defaults to the normalized resolved "
            "project-directory basename"
        ),
    )
    add.add_argument(
        "--reuse",
        metavar="EXACT_NAME",
        help="reuse one exact active managed worktree instead of creating another",
    )

    inspect = subparsers.add_parser("inspect", help="validate managed identity")
    inspect.add_argument("--name", help="generated worktree name from primary checkout")
    inspect.add_argument(
        "--require-clean",
        action="store_true",
        help="fail unless the entire managed linked worktree is clean",
    )

    integration_preflight_parser = subparsers.add_parser(
        "integration-preflight",
        help="read-only classification before one primary-anchored integration",
    )
    integration_preflight_parser.add_argument("--name", required=True)
    integration_preflight_parser.add_argument(
        "--restart",
        action="store_true",
        help="classify an explicit restart without permitting automatic commits",
    )

    integration_commit_parser = subparsers.add_parser(
        "integration-commit",
        help="internal: create one exact reviewed commit behind a preparation claim",
    )
    integration_commit_parser.add_argument("--name", required=True)
    integration_commit_parser.add_argument(
        "--target", required=True, choices=("child", "source")
    )
    integration_commit_parser.add_argument("--expected-head", required=True)
    integration_commit_parser.add_argument("--expected-tree", required=True)
    integration_commit_parser.add_argument("--message", required=True)
    integration_commit_parser.add_argument("--preparation-token")

    integration_commit_review_parser = subparsers.add_parser(
        "integration-commit-review",
        help="internal: bind external review to an exact hook-modified commit",
    )
    integration_commit_review_parser.add_argument("--name", required=True)
    integration_commit_review_parser.add_argument(
        "--target", required=True, choices=("child", "source")
    )
    integration_commit_review_parser.add_argument(
        "--preparation-token", required=True
    )
    integration_commit_review_parser.add_argument("--commit-head", required=True)
    integration_commit_review_parser.add_argument("--commit-tree", required=True)

    integration_preparation_abort_parser = subparsers.add_parser(
        "integration-preparation-abort",
        help="internal: drop one exact claim without reverting retained commits",
    )
    integration_preparation_abort_parser.add_argument("--name", required=True)
    integration_preparation_abort_parser.add_argument(
        "--preparation-token", required=True
    )

    integrate = subparsers.add_parser(
        "integrate",
        help="prepare, validate, or promote one primary-anchored local integration",
    )
    integrate.add_argument("--name", required=True)
    integrate.add_argument(
        "--expected-source-head",
        required=True,
        help="exact clean source SHA returned by integration-preflight",
    )
    integrate.add_argument(
        "--expected-child-head",
        required=True,
        help="exact clean child SHA returned by integration-preflight",
    )
    integrate.add_argument(
        "--preparation-token",
        help="exact private token returned after preparatory commits",
    )
    integrate.add_argument(
        "--validated-head",
        help="exact candidate SHA after non-mutating combined validation",
    )
    integrate.add_argument(
        "--restart",
        action="store_true",
        help="explicitly discard the exact retained attempt and start from current source",
    )

    remove = subparsers.add_parser("remove", help="remove a safely completed worktree")
    remove.add_argument(
        "--name",
        required=True,
        help="exact generated worktree name; run from the primary checkout",
    )

    task_lane_ensure_parser = subparsers.add_parser(
        "task-lane-ensure",
        help="internal: create or exactly reuse one Task Implementer project lane",
    )
    task_lane_ensure_parser.add_argument(
        "--project", help="repository-relative project directory"
    )

    task_lane_acquire_parser = subparsers.add_parser(
        "task-lane-generation-acquire",
        help="internal: acquire one monotonic Task Implementer lane generation",
    )
    task_lane_acquire_parser.add_argument("--workspace", required=True, type=Path)
    task_lane_acquire_parser.add_argument("--run-id", required=True)
    task_lane_acquire_parser.add_argument("--task-scope", required=True)
    task_lane_acquire_parser.add_argument("--initial-head", required=True)

    task_lane_inspect_parser = subparsers.add_parser(
        "task-lane-generation-inspect",
        help="internal: inspect one exact Task Implementer lane generation",
    )
    task_lane_inspect_parser.add_argument("--name", required=True)
    task_lane_inspect_parser.add_argument("--generation", required=True, type=int)
    task_lane_inspect_parser.add_argument("--lease-id", required=True)

    task_lane_claims_parser = subparsers.add_parser(
        "task-lane-generation-claims",
        help="internal: acquire repository-wide claims for one lane generation",
    )
    task_lane_claims_parser.add_argument("--name", required=True)
    task_lane_claims_parser.add_argument("--generation", required=True, type=int)
    task_lane_claims_parser.add_argument("--lease-id", required=True)
    task_lane_claims_parser.add_argument("--claims-json", required=True)

    task_lane_release_parser = subparsers.add_parser(
        "task-lane-generation-release",
        help="internal: release and archive one Task Implementer lane generation",
    )
    task_lane_release_parser.add_argument("--name", required=True)
    task_lane_release_parser.add_argument("--generation", required=True, type=int)
    task_lane_release_parser.add_argument("--lease-id", required=True)
    task_lane_release_parser.add_argument("--promoted-head", required=True)

    task_lane_integrate_parser = subparsers.add_parser(
        "task-lane-integrate",
        help="internal: integrate pending lane generations and rearm the lane",
    )
    task_lane_integrate_parser.add_argument("--lane-id", required=True)
    task_lane_integrate_parser.add_argument("--validated-head")
    task_lane_integrate_parser.add_argument("--restart", action="store_true")

    task_lane_remove_parser = subparsers.add_parser(
        "task-lane-remove",
        help="internal: proof-gate removal of one idle Task Implementer lane",
    )
    task_lane_remove_parser.add_argument("--lane-id", required=True)

    subparsers.add_parser(
        "anchor-inspect", help="internal: inspect a managed outer checkout"
    )
    publication_guard_parser = subparsers.add_parser(
        "publication-guard", help="internal: block direct managed-child publication"
    )
    publication_guard_parser.add_argument(
        "--publication-action", required=True, choices=("push", "create-pr")
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

    task_inspect = subparsers.add_parser(
        "task-lease-inspect", help="internal: inspect exact task coordinator ownership"
    )
    task_inspect.add_argument("--name", required=True)
    task_inspect.add_argument("--lease-id", required=True)
    task_inspect.add_argument(
        "--owner-kind", required=True, choices=("task-implementer", "agentic-sdlc")
    )

    task_promote = subparsers.add_parser(
        "task-lease-promote", help="internal: record an outer promotion"
    )
    task_promote.add_argument("--name", required=True)
    task_promote.add_argument("--lease-id", required=True)
    task_promote.add_argument("--promoted-head", required=True)
    task_promote.add_argument("--expected-head", required=True)
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
                require_clean=arguments.require_clean,
            )
        elif arguments.action == "integration-preflight":
            result = integration_preflight(
                cwd=Path.cwd(),
                name=arguments.name,
                restart=arguments.restart,
            )
        elif arguments.action == "integration-commit":
            result = integration_commit(
                cwd=Path.cwd(),
                name=arguments.name,
                target=arguments.target,
                expected_head=arguments.expected_head,
                expected_tree=arguments.expected_tree,
                message=arguments.message,
                preparation_token=arguments.preparation_token,
            )
        elif arguments.action == "integration-commit-review":
            result = integration_commit_review(
                cwd=Path.cwd(),
                name=arguments.name,
                target=arguments.target,
                preparation_token=arguments.preparation_token,
                commit_head=arguments.commit_head,
                commit_tree=arguments.commit_tree,
            )
        elif arguments.action == "integration-preparation-abort":
            result = integration_preparation_abort(
                cwd=Path.cwd(),
                name=arguments.name,
                preparation_token=arguments.preparation_token,
            )
        elif arguments.action == "integrate":
            result = integrate_worktree(
                cwd=Path.cwd(),
                name=arguments.name,
                validated_head=arguments.validated_head,
                restart=arguments.restart,
                expected_source_head=arguments.expected_source_head,
                expected_child_head=arguments.expected_child_head,
                preparation_token=arguments.preparation_token,
            )
        elif arguments.action == "remove":
            result = remove_worktree(cwd=Path.cwd(), name=arguments.name)
        elif arguments.action == "task-lane-ensure":
            result = task_lane_ensure(
                cwd=Path.cwd(), project=arguments.project
            )
        elif arguments.action == "task-lane-generation-acquire":
            result = task_lane_generation_acquire(
                cwd=Path.cwd(),
                workspace=arguments.workspace,
                run_id=arguments.run_id,
                task_scope=arguments.task_scope,
                initial_head=arguments.initial_head,
            )
        elif arguments.action == "task-lane-generation-inspect":
            result = task_lane_generation_inspect(
                cwd=Path.cwd(),
                name=arguments.name,
                generation=arguments.generation,
                lease_id=arguments.lease_id,
            )
        elif arguments.action == "task-lane-generation-claims":
            try:
                claims_value = json.loads(arguments.claims_json)
            except json.JSONDecodeError as error:
                raise WorktreeError("Task Implementer claims JSON is invalid") from error
            if not isinstance(claims_value, list) or any(
                not isinstance(item, dict) for item in claims_value
            ):
                raise WorktreeError("Task Implementer claims JSON must be a list")
            result = task_lane_generation_claims(
                cwd=Path.cwd(),
                name=arguments.name,
                generation=arguments.generation,
                lease_id=arguments.lease_id,
                claims=claims_value,
            )
        elif arguments.action == "task-lane-generation-release":
            result = task_lane_generation_release(
                cwd=Path.cwd(),
                name=arguments.name,
                generation=arguments.generation,
                lease_id=arguments.lease_id,
                promoted_head=arguments.promoted_head,
            )
        elif arguments.action == "task-lane-integrate":
            result = task_lane_integrate(
                cwd=Path.cwd(),
                lane_id=arguments.lane_id,
                validated_head=arguments.validated_head,
                restart=arguments.restart,
            )
        elif arguments.action == "task-lane-remove":
            result = task_lane_remove(
                cwd=Path.cwd(), lane_id=arguments.lane_id
            )
        elif arguments.action == "anchor-inspect":
            result = inspect_managed_anchor(cwd=Path.cwd())
        elif arguments.action == "publication-guard":
            result = publication_guard(
                cwd=Path.cwd(), action=arguments.publication_action
            )
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
        elif arguments.action == "task-lease-inspect":
            result = task_lease_inspect(
                cwd=Path.cwd(),
                name=arguments.name,
                lease_id=arguments.lease_id,
                owner_kind=arguments.owner_kind,
            )
        elif arguments.action == "task-lease-promote":
            result = task_lease_promote(
                cwd=Path.cwd(),
                name=arguments.name,
                lease_id=arguments.lease_id,
                promoted_head=arguments.promoted_head,
                expected_head=arguments.expected_head,
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
        else:
            parser.error(f"unsupported action: {arguments.action}")
            raise AssertionError("unreachable")
    except (
        WorktreeError,
        GitPromotionError,
        InteropError,
        StateError,
        TaskLaneStateError,
    ) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
