#!/usr/bin/env python3
"""Private Task Implementer adapter for Worktree-owned persistent lanes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any

from prompt_workspace_core import (
    RUN_ID_RE,
    PromptWorkspaceError,
    load_json_object,
    required_string,
    stable_json,
    write_atomic,
)


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
INTEGRATION_REVIEW_CORRECTION_SCHEMA = (
    "task-implementer/integration-review-correction-v1"
)
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40,64}")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
LANE_ID_RE = re.compile(r"[0-9a-f]{32}")


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


def _review_correction_path(workspace: dict[str, object]) -> Path:
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    path = runs_root.parent / "integration-review-correction.json"
    if not runs_root.is_absolute() or runs_root.name != "runs":
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "integration review correction path is invalid"
        )
    return path


def _validate_review_correction(
    workspace: dict[str, object], value: dict[str, object]
) -> dict[str, object]:
    required = {
        "schema",
        "lane_id",
        "source_head",
        "lane_head",
        "review_rejected_head",
        "findings_sha256",
        "bound_run_id",
    }
    bound_run_id = value.get("bound_run_id")
    if (
        set(value) != required
        or value.get("schema") != INTEGRATION_REVIEW_CORRECTION_SCHEMA
        or value.get("lane_id")
        != required_string(workspace, "lane_id", "workspace manifest")
        or LANE_ID_RE.fullmatch(str(value.get("lane_id"))) is None
        or any(
            OBJECT_ID_RE.fullmatch(str(value.get(key))) is None
            for key in ("source_head", "lane_head", "review_rejected_head")
        )
        or DIGEST_RE.fullmatch(str(value.get("findings_sha256"))) is None
        or (
            bound_run_id is not None
            and (
                not isinstance(bound_run_id, str)
                or RUN_ID_RE.fullmatch(bound_run_id) is None
            )
        )
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID",
            "integration review correction state is invalid",
        )
    return value


def load_integration_review_correction(
    workspace: dict[str, object], *, required: bool = False
) -> dict[str, object] | None:
    path = _review_correction_path(workspace)
    if not path.exists():
        if required:
            raise PromptWorkspaceError(
                "WORKSPACE_NOT_FOUND", "integration review correction is missing"
            )
        return None
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID",
            "integration review correction state is unavailable",
        ) from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_PERMISSION_INVALID",
            "integration review correction state is unsafe",
        )
    return _validate_review_correction(
        workspace, load_json_object(path, "integration review correction")
    )


def record_integration_review_correction(
    workspace: dict[str, object],
    result: dict[str, object],
    findings_sha256: str | None,
) -> dict[str, object]:
    if (
        result.get("status") != "correction-required"
        or findings_sha256 is None
        or DIGEST_RE.fullmatch(findings_sha256) is None
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "integration review correction result is incomplete",
        )
    value = {
        "schema": INTEGRATION_REVIEW_CORRECTION_SCHEMA,
        "lane_id": result.get("lane_id"),
        "source_head": result.get("source_head"),
        "lane_head": result.get("lane_head"),
        "review_rejected_head": result.get("review_rejected_head"),
        "findings_sha256": findings_sha256,
        "bound_run_id": None,
    }
    validated = _validate_review_correction(workspace, value)
    existing = load_integration_review_correction(workspace)
    if existing is not None:
        comparable = {**existing, "bound_run_id": None}
        if comparable == validated:
            validated = existing
    write_atomic(_review_correction_path(workspace), stable_json(validated))
    return validated


def integration_review_correction_matches(
    workspace: dict[str, object],
    *,
    run_id: str,
    run_dir: Path,
    lane_head: str,
    source_head: str,
) -> bool:
    value = load_integration_review_correction(workspace)
    if value is None:
        return False
    manifest = load_json_object(run_dir / "manifest.json", "run manifest")
    revisions = manifest.get("revisions")
    return bool(
        value.get("lane_head") == lane_head
        and value.get("source_head") == source_head
        and value.get("bound_run_id") in {None, run_id}
        and manifest.get("run_id") == run_id
        and isinstance(revisions, list)
        and bool(revisions)
        and isinstance(revisions[0], dict)
        and revisions[0].get("kind") == "completed_follow_up"
    )


def bind_integration_review_correction(
    workspace: dict[str, object],
    *,
    run_id: str,
    run_dir: Path,
    lane_head: str,
    source_head: str,
) -> bool:
    if not integration_review_correction_matches(
        workspace,
        run_id=run_id,
        run_dir=run_dir,
        lane_head=lane_head,
        source_head=source_head,
    ):
        return False
    value = load_integration_review_correction(workspace, required=True)
    assert value is not None
    bound = value.get("bound_run_id")
    if bound not in {None, run_id}:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "integration review correction belongs to another run",
        )
    if bound is None:
        value = {**value, "bound_run_id": run_id}
        write_atomic(_review_correction_path(workspace), stable_json(value))
    return True


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
