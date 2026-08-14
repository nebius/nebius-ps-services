#!/usr/bin/env python3
"""Private Task Implementer adapter for Worktree-owned persistent lanes."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from prompt_workspace_core import PromptWorkspaceError, required_string


PRIVATE_LANE_ACTIONS = frozenset(
    {
        "task-lane-ensure",
        "task-lane-generation-inspect",
        "task-lane-generation-claims",
        "task-lane-generation-release",
        "task-lane-integrate",
        "task-lane-remove",
    }
)


def _helper_path() -> Path:
    path = (
        Path(__file__).resolve().parents[2]
        / "worktree"
        / "scripts"
        / "worktree_manager.py"
    )
    if not path.is_file():
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER",
            "Task Implementer project lanes require the installed worktree helper",
        )
    return path


def _call(cwd: Path, arguments: list[str], *, timeout: int = 120) -> dict[str, object]:
    if not arguments or arguments[0] not in PRIVATE_LANE_ACTIONS:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "Task Implementer lane adapter rejects public Worktree lifecycle actions",
        )
    try:
        result = subprocess.run(
            [sys.executable, str(_helper_path()), *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Task Implementer lane helper could not run"
        ) from error
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "Task Implementer lane helper returned invalid state"
        ) from error
    if not isinstance(payload, dict):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "Task Implementer lane helper returned invalid state"
        )
    if result.returncode != 0 or payload.get("status") == "blocked":
        detail = payload.get("error")
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            str(detail)
            if isinstance(detail, str)
            else "Task Implementer lane is blocked",
        )
    return payload


def ensure_project_lane(project_path: Path) -> dict[str, object]:
    requested = project_path.expanduser().resolve()
    if not requested.is_dir():
        raise PromptWorkspaceError(
            "SCOPE_INVALID", "project folder must be an existing directory"
        )
    payload = _call(requested, ["task-lane-ensure"])
    required = {
        "lane_id",
        "incarnation",
        "name",
        "branch",
        "worktree",
        "scope",
        "scope_cwd",
        "primary",
        "common_dir",
        "source_branch",
        "source_ref",
        "source_head",
        "lane_head",
    }
    if any(key not in payload for key in required):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "Task Implementer lane identity is incomplete"
        )
    return payload


def workspace_lane_call(
    workspace: dict[str, object], arguments: list[str]
) -> dict[str, object]:
    source_root = Path(required_string(workspace, "source_root", "workspace manifest"))
    return _call(source_root, arguments)


def claim_generation(
    workspace: dict[str, object],
    *,
    name: str,
    generation: int,
    lease_id: str,
    claims: list[dict[str, str]],
) -> dict[str, object]:
    return workspace_lane_call(
        workspace,
        [
            "task-lane-generation-claims",
            "--name",
            name,
            "--generation",
            str(generation),
            "--lease-id",
            lease_id,
            "--claims-json",
            json.dumps(claims, separators=(",", ":"), sort_keys=True),
        ],
    )


def integrate_lane(
    workspace: dict[str, object],
    *,
    validated_head: str | None,
    restart: bool,
    review_rejected_head: str | None = None,
    review_findings_sha256: str | None = None,
) -> dict[str, object]:
    arguments = [
        "task-lane-integrate",
        "--lane-id",
        required_string(workspace, "lane_id", "workspace manifest"),
    ]
    if validated_head is not None:
        arguments.extend(("--validated-head", validated_head))
    if restart:
        arguments.append("--restart")
    if review_rejected_head is not None:
        arguments.extend(("--review-rejected-head", review_rejected_head))
    if review_findings_sha256 is not None:
        arguments.extend(("--review-findings-sha256", review_findings_sha256))
    return workspace_lane_call(workspace, arguments)


def remove_lane(workspace: dict[str, object]) -> dict[str, object]:
    primary = Path(required_string(workspace, "primary_root", "workspace manifest"))
    return _call(
        primary,
        [
            "task-lane-remove",
            "--lane-id",
            required_string(workspace, "lane_id", "workspace manifest"),
        ],
    )
