#!/usr/bin/env python3
"""Coordinate one run with its Worktree-owned persistent lane generation."""

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


SCHEMA = 4
NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?")
LEASE_ID_RE = re.compile(r"[0-9a-f]{32}")
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40,64}")
PRIVATE_WORKTREE_ACTIONS = frozenset(
    {
        "anchor-inspect",
        "task-lease-acquire",
        "task-lease-inspect",
        "task-lease-promote",
        "task-lease-release",
        "task-lease-resource",
        "task-lane-generation-acquire",
        "task-lane-generation-inspect",
        "task-lane-generation-release",
    }
)


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
            "the persistent lane requires the installed Worktree helper",
        )
    return path if path.is_file() else None


def _source_root(workspace: dict[str, object]) -> Path:
    return Path(required_string(workspace, "source_root", "workspace manifest"))


def _call(workspace: dict[str, object], arguments: list[str]) -> dict[str, object]:
    if not arguments or arguments[0] not in PRIVATE_WORKTREE_ACTIONS:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "Task Implementer private worktree interop rejects public lifecycle actions",
        )
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
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER",
            "the persistent Task Implementer lane requires the Worktree helper",
        )
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
        "lane_id",
        "generation",
    }
    if value.get("schema") in {1, 2, 3}:
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "older worktree interop state is unsupported; start a new run",
        )
    if set(value) != required or value.get("schema") != SCHEMA:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worktree interop state is invalid"
        )
    if value.get("run_id") != run_id or value.get("mode") != "lane":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "worktree interop identity is invalid"
        )
    if value["mode"] == "lane":
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
            or re.fullmatch(r"[0-9a-f]{32}", str(value.get("lane_id"))) is None
            or not isinstance(value.get("generation"), int)
            or int(value["generation"]) < 1
            or not Path(str(value["worktree"])).is_absolute()
            or outer_scope is None
            or task_scope is None
            or (
                promoted is not None
                and (
                    not isinstance(promoted, str)
                    or OBJECT_ID_RE.fullmatch(promoted) is None
                )
            )
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "Task lane interop scope is invalid"
            )
        if not isinstance(value.get("released"), bool):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "Task lane release state is invalid"
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


def _inspect_managed_lease(
    workspace: dict[str, object], interop: dict[str, object]
) -> dict[str, object]:
    inspected = _call(
        workspace,
        [
            "task-lane-generation-inspect",
            "--name",
            str(interop["name"]),
            "--generation",
            str(interop["generation"]),
            "--lease-id",
            str(interop["lease_id"]),
        ],
    )
    expected = {
        "owner_kind": "task-implementer",
        "name": interop["name"],
        "branch": interop["branch"],
        "worktree": interop["worktree"],
        "run_id": interop["run_id"],
        "scope": interop["outer_scope"],
        "task_scope": interop["task_scope"],
        "token": interop["lease_id"],
        "lane_id": interop["lane_id"],
        "generation": interop["generation"],
    }
    if any(inspected.get(key) != value for key, value in expected.items()):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane lease identity changed"
        )
    if inspected.get("state") not in {"active", "released"}:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane lease state is invalid"
        )
    if inspected.get("outer_clean") is not True:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane must remain clean"
        )
    return inspected


def _reconcile_managed_state(
    workspace: dict[str, object],
    run_dir: Path,
    interop: dict[str, object],
    *,
    initial_head: str | None = None,
    workspace_path: Path | None = None,
) -> dict[str, object]:
    inspected = _inspect_managed_lease(workspace, interop)
    if initial_head is not None and inspected.get("initial_head") != initial_head:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane initial head changed"
        )
    if workspace_path is not None and inspected.get("workspace") != str(
        workspace_path.resolve()
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane workspace identity changed"
        )
    lease_promoted = inspected.get("promoted_head")
    local_promoted = interop.get("promoted_head")
    repair_promoted = local_promoted is None and lease_promoted is not None
    if (
        lease_promoted is not None
        and local_promoted is not None
        and lease_promoted != local_promoted
    ):
        history = inspected.get("promotion_heads")
        if (
            not isinstance(history, list)
            or len(history) < 2
            or history[-2:] != [local_promoted, lease_promoted]
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "persistent lane promoted head changed"
            )
        repair_promoted = True
    if local_promoted is not None and lease_promoted is None:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "local promotion is missing from the lane lease"
        )
    expected_head = lease_promoted or inspected.get("initial_head")
    lease_released = inspected["state"] == "released"
    if not lease_released and inspected.get("outer_head") != expected_head:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane head disagrees with its lease"
        )
    if interop.get("released") is True and not lease_released:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "local release is missing from the lane lease"
        )
    changed = False
    if repair_promoted:
        interop["promoted_head"] = lease_promoted
        changed = True
    if lease_released and interop.get("released") is False:
        interop["released"] = True
        changed = True
    if changed:
        _validate_state(interop, run_dir.name)
        write_atomic(_interop_path(run_dir), stable_json(interop))
    return inspected


def acquire_interop(
    workspace: dict[str, object],
    run_dir: Path,
    workspace_path: Path,
    initial_head: str,
) -> dict[str, object]:
    existing = load_interop(run_dir, required=False)
    if existing is not None:
        anchor = inspect_anchor(workspace)
        if anchor.get("status") != "task-lane":
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "Task Implementer lane disappeared"
            )
        _reconcile_managed_state(
            workspace,
            run_dir,
            existing,
            initial_head=initial_head,
            workspace_path=workspace_path,
        )
        return existing
    anchor = inspect_anchor(workspace)
    task_scope = required_string(workspace, "scope", "workspace manifest")
    if anchor.get("status") != "task-lane":
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "Task Implementer runs require a workspace-v2 persistent lane",
        )
    outer_scope = str(anchor.get("scope"))
    if anchor.get("task_scope") != task_scope:
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "task scope must match the persistent Task Implementer lane",
        )
    acquired = _call(
        workspace,
        [
            "task-lane-generation-acquire",
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
    generation = acquired.get("generation")
    lane_id = acquired.get("lane_id")
    lease_id = acquired.get("token")
    expected_acquisition = {
        "owner_kind": "task-implementer",
        "name": anchor.get("name"),
        "branch": anchor.get("branch"),
        "worktree": anchor.get("worktree"),
        "scope": outer_scope,
        "task_scope": task_scope,
        "run_id": run_dir.name,
        "workspace": str(workspace_path.resolve()),
        "initial_head": initial_head,
    }
    if (
        not isinstance(lease_id, str)
        or not lease_id
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(lane_id, str)
        or acquired.get("state") != "active"
        or acquired.get("promoted_head") is not None
        or any(
            acquired.get(key) != value for key, value in expected_acquisition.items()
        )
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "Task Implementer generation identity is missing"
        )
    state = {
        "schema": SCHEMA,
        "mode": "lane",
        "run_id": run_dir.name,
        "name": str(acquired["name"]),
        "lease_id": lease_id,
        "outer_scope": outer_scope,
        "task_scope": task_scope,
        "branch": str(acquired["branch"]),
        "worktree": str(acquired["worktree"]),
        "promoted_head": acquired.get("promoted_head"),
        "released": False,
        "lane_id": lane_id,
        "generation": generation,
    }
    _validate_state(state, run_dir.name)
    write_atomic(_interop_path(run_dir), stable_json(state))
    return state


def managed(state: dict[str, object]) -> bool:
    return state.get("mode") == "lane"


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
    if interop.get("released") is True:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "released worktree interop cannot be promoted"
        )
    inspected = _inspect_managed_lease(workspace, interop)
    if inspected.get("state") != "active":
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "released worktree interop cannot be promoted"
        )
    lease_promoted = inspected.get("promoted_head")
    history = inspected.get("promotion_heads")
    local_promoted = interop.get("promoted_head")
    if lease_promoted == promoted_head:
        expected_head = promoted_head
    else:
        expected_head = lease_promoted or inspected.get("initial_head")
    if not isinstance(expected_head, str):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane promotion baseline is invalid"
        )
    predecessor_matches = (
        lease_promoted == promoted_head
        and local_promoted not in {None, promoted_head}
        and isinstance(history, list)
        and len(history) >= 2
        and history[-2:] == [local_promoted, promoted_head]
    )
    if (
        local_promoted not in {None, lease_promoted, promoted_head}
        and not predecessor_matches
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "worktree promotion changed after it was recorded"
        )
    if (
        lease_promoted == promoted_head
        and local_promoted not in {None, promoted_head}
        and not predecessor_matches
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "worktree promotion history is inconsistent"
        )
    if inspected.get("outer_head") != promoted_head:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane head is not the promotion"
        )
    result = _call(
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
            "--expected-head",
            expected_head,
        ],
    )
    if result.get("state") != "active" or result.get("promoted_head") != promoted_head:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "lane lease did not record the exact promotion"
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
    if interop.get("promoted_head") != promoted_head:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "release head does not match the recorded promotion"
        )
    inspected = _reconcile_managed_state(workspace, run_dir, interop)
    if inspected["state"] == "active":
        result = _call(
            workspace,
            [
                "task-lane-generation-release",
                "--name",
                str(interop["name"]),
                "--generation",
                str(interop["generation"]),
                "--lease-id",
                str(interop["lease_id"]),
                "--promoted-head",
                promoted_head,
            ],
        )
    else:
        result = {**inspected, "status": "already-released"}
    if (
        result.get("state") != "released"
        or result.get("promoted_head") != promoted_head
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "lane generation release receipt is inconsistent"
        )
    interop["released"] = True
    write_atomic(_interop_path(run_dir), stable_json(interop))
    return result
