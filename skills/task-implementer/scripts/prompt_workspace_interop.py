#!/usr/bin/env python3
"""Optional coordination with a worktree-managed outer checkout."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any

from prompt_workspace_core import (
    PromptWorkspaceError,
    load_json_object,
    required_string,
    stable_json,
    write_atomic,
)
from prompt_workspace_execution import orchestration_dir


SCHEMA = 2
NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?")
LEASE_ID_RE = re.compile(r"[0-9a-f]{32}")
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40,64}")


def _helper_path(*, required: bool = True) -> Path | None:
    path = (
        Path(__file__).resolve().parents[2]
        / "worktree"
        / "scripts"
        / "worktree_manager.py"
    )
    if required and not path.is_file():
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER",
            "the managed outer checkout requires the installed worktree helper",
        )
    return path if path.is_file() else None


def _source_root(workspace: dict[str, object]) -> Path:
    return Path(required_string(workspace, "source_root", "workspace manifest"))


def _call(workspace: dict[str, object], arguments: list[str]) -> dict[str, object]:
    helper = _helper_path()
    assert helper is not None
    try:
        result = subprocess.run(
            [sys.executable, str(helper), *arguments],
            cwd=_source_root(workspace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "worktree interop helper could not run"
        ) from error
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "worktree interop helper returned invalid state"
        ) from error
    if not isinstance(payload, dict):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "worktree interop helper returned invalid state"
        )
    if result.returncode != 0 or payload.get("status") == "blocked":
        detail = payload.get("error")
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            str(detail) if isinstance(detail, str) else "worktree interop is blocked",
        )
    return payload


def _interop_path(run_dir: Path) -> Path:
    return orchestration_dir(run_dir) / "interop.json"


def _scope_contains(outer: str, inner: str) -> bool:
    if outer == ".":
        return True
    outer_path = PurePosixPath(outer)
    inner_path = PurePosixPath(inner)
    return inner_path == outer_path or outer_path in inner_path.parents


def _safe_scope(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        return None
    return value


def inspect_anchor(workspace: dict[str, object]) -> dict[str, object]:
    path = _helper_path(required=False)
    if path is None:
        repo = _source_root(workspace)
        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=15,
            ).stdout.strip()
            marker = subprocess.run(
                [
                    "git",
                    "config",
                    "--local",
                    "--get",
                    f"branch.{branch}.worktreeSkillName",
                ],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PromptWorkspaceError(
                "ENVIRONMENT_BLOCKER", "could not inspect the project checkout"
            ) from error
        if branch and marker.returncode == 0 and marker.stdout.strip():
            raise PromptWorkspaceError(
                "ENVIRONMENT_BLOCKER",
                "the managed outer checkout requires the installed worktree helper",
            )
        return {"status": "unmanaged"}
    return _call(workspace, ["anchor-inspect"])


def _validate_state(value: dict[str, object], run_id: str) -> dict[str, object]:
    required = {
        "schema",
        "mode",
        "run_id",
        "name",
        "lease_id",
        "outer_scope",
        "task_scope",
        "branch",
        "worktree",
        "promoted_head",
        "released",
    }
    if value.get("schema") == 1:
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "worktree interop schema v1 is unsupported; start a new run",
        )
    if set(value) != required or value.get("schema") != SCHEMA:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worktree interop state is invalid"
        )
    if value.get("run_id") != run_id or value.get("mode") not in {
        "managed",
        "unmanaged",
    }:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worktree interop identity is invalid"
        )
    if value["mode"] == "managed":
        for key in (
            "name",
            "lease_id",
            "outer_scope",
            "task_scope",
            "branch",
            "worktree",
        ):
            if not isinstance(value.get(key), str) or not value[key]:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "managed worktree interop is invalid"
                )
        name = value["name"]
        outer_scope = _safe_scope(value["outer_scope"])
        task_scope = _safe_scope(value["task_scope"])
        promoted = value["promoted_head"]
        if (
            NAME_RE.fullmatch(str(name)) is None
            or not str(name).startswith("project-")
            or value["branch"] != f"feature/{str(name).removeprefix('project-')}"
            or LEASE_ID_RE.fullmatch(str(value["lease_id"])) is None
            or not Path(str(value["worktree"])).is_absolute()
            or outer_scope is None
            or task_scope is None
            or not _scope_contains(outer_scope, task_scope)
            or (
                promoted is not None
                and (
                    not isinstance(promoted, str)
                    or OBJECT_ID_RE.fullmatch(promoted) is None
                )
            )
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "managed worktree interop scope is invalid"
            )
        if not isinstance(value.get("released"), bool):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "managed worktree release state is invalid"
            )
    else:
        if (
            any(
                value.get(key) is not None
                for key in (
                    "name",
                    "lease_id",
                    "outer_scope",
                    "task_scope",
                    "branch",
                    "worktree",
                    "promoted_head",
                )
            )
            or value.get("released") is not True
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "unmanaged worktree interop is invalid"
            )
    return value


def load_interop(run_dir: Path, *, required: bool = True) -> dict[str, object] | None:
    path = _interop_path(run_dir)
    if not path.exists():
        if required:
            raise PromptWorkspaceError(
                "WORKFLOW_UPGRADE_REQUIRED",
                "unfinished managed-outer runs without interop state are unsupported",
            )
        return None
    return _validate_state(load_json_object(path, "worktree interop"), run_dir.name)


def acquire_interop(
    workspace: dict[str, object],
    run_dir: Path,
    workspace_path: Path,
    initial_head: str,
) -> dict[str, object]:
    existing = load_interop(run_dir, required=False)
    if existing is not None:
        return existing
    anchor = inspect_anchor(workspace)
    task_scope = required_string(workspace, "scope", "workspace manifest")
    if anchor.get("status") == "unmanaged":
        state: dict[str, object] = {
            "schema": SCHEMA,
            "mode": "unmanaged",
            "run_id": run_dir.name,
            "name": None,
            "lease_id": None,
            "outer_scope": None,
            "task_scope": None,
            "branch": None,
            "worktree": None,
            "promoted_head": None,
            "released": True,
        }
    else:
        outer_scope = str(anchor.get("scope"))
        if anchor.get("task_scope") != task_scope or not _scope_contains(
            outer_scope, task_scope
        ):
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "task scope must be equal to or nested under the managed outer scope",
            )
        acquired = _call(
            workspace,
            [
                "task-lease-acquire",
                "--owner-kind",
                "task-implementer",
                "--workspace",
                str(workspace_path.resolve()),
                "--run-id",
                run_dir.name,
                "--task-scope",
                task_scope,
                "--initial-head",
                initial_head,
            ],
        )
        lease_id = acquired.get("token")
        if not isinstance(lease_id, str) or not lease_id:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "worktree task lease identity is missing"
            )
        state = {
            "schema": SCHEMA,
            "mode": "managed",
            "run_id": run_dir.name,
            "name": str(acquired["name"]),
            "lease_id": lease_id,
            "outer_scope": outer_scope,
            "task_scope": task_scope,
            "branch": str(acquired["branch"]),
            "worktree": str(acquired["worktree"]),
            "promoted_head": acquired.get("promoted_head"),
            "released": False,
        }
    _validate_state(state, run_dir.name)
    write_atomic(_interop_path(run_dir), stable_json(state))
    return state


def managed(state: dict[str, object]) -> bool:
    return state.get("mode") == "managed"


def record_resource(
    workspace: dict[str, object],
    run_dir: Path,
    *,
    kind: str,
    path: Path,
    branch: str,
    state: str,
) -> None:
    interop = load_interop(run_dir)
    assert interop is not None
    if not managed(interop):
        return
    _call(
        workspace,
        [
            "task-lease-resource",
            "--owner-kind",
            "task-implementer",
            "--name",
            str(interop["name"]),
            "--lease-id",
            str(interop["lease_id"]),
            "--kind",
            kind,
            "--path",
            str(path.absolute()),
            "--branch",
            branch,
            "--state",
            state,
        ],
    )


def record_promotion(
    workspace: dict[str, object], run_dir: Path, promoted_head: str
) -> None:
    interop = load_interop(run_dir)
    assert interop is not None
    if not managed(interop):
        return
    _call(
        workspace,
        [
            "task-lease-promote",
            "--owner-kind",
            "task-implementer",
            "--name",
            str(interop["name"]),
            "--lease-id",
            str(interop["lease_id"]),
            "--promoted-head",
            promoted_head,
        ],
    )
    interop["promoted_head"] = promoted_head
    write_atomic(_interop_path(run_dir), stable_json(interop))


def release_interop(
    workspace: dict[str, object], run_dir: Path, promoted_head: str
) -> dict[str, object]:
    interop = load_interop(run_dir)
    assert interop is not None
    if not managed(interop):
        return {"status": "unmanaged"}
    if interop["released"] is True:
        return {"status": "released", "promoted_head": promoted_head}
    result = _call(
        workspace,
        [
            "task-lease-release",
            "--owner-kind",
            "task-implementer",
            "--name",
            str(interop["name"]),
            "--lease-id",
            str(interop["lease_id"]),
            "--promoted-head",
            promoted_head,
        ],
    )
    interop["released"] = True
    write_atomic(_interop_path(run_dir), stable_json(interop))
    return result
