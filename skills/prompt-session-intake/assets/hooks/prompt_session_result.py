#!/usr/bin/env python3
"""Canonical workflow prompt-result validation for prompt-session intake."""

from __future__ import annotations

from pathlib import Path

from prompt_session_storage import (
    PROMPT_SCHEMAS,
    PromptSessionError,
    load_json,
    require_private_directory,
)


OPERATION_MARKER_PREFIX = b"<!-- prompt-session-operation:"
TASK_WORKSPACE_SCHEMA = "task-implementer/workspace-v2"
SDLC_WORKSPACE_SCHEMA = "agentic-sdlc/prompt-workspace-v1"


def prompt_frontmatter(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical prompt is not UTF-8"
        ) from error
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical prompt frontmatter is missing"
        )
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return metadata
        if ":" not in line:
            raise PromptSessionError(
                "PROMPT_RESULT_INVALID", "canonical prompt frontmatter is invalid"
            )
        key, value = line.split(":", 1)
        key = key.strip()
        if not key or key in metadata:
            raise PromptSessionError(
                "PROMPT_RESULT_INVALID", "canonical prompt frontmatter is invalid"
            )
        metadata[key] = value.strip()
    raise PromptSessionError(
        "PROMPT_RESULT_INVALID", "canonical prompt frontmatter is unterminated"
    )


def validate_prompt_result(
    raw: bytes,
    *,
    workflow: str,
    prompt_id: str,
    prompt_ref: str,
    operation_id: str | None = None,
) -> None:
    metadata = prompt_frontmatter(raw)
    if (
        metadata.get("schema") != PROMPT_SCHEMAS[workflow]
        or metadata.get("prompt_id") != prompt_id
        or metadata.get("prompt_ref") != prompt_ref
    ):
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID",
            "canonical prompt identity differs from the workflow result",
        )
    if operation_id is not None:
        marker = f"<!-- prompt-session-operation:{operation_id} -->".encode("utf-8")
        if raw.count(marker) != 1:
            raise PromptSessionError(
                "PROMPT_RESULT_INVALID",
                "canonical prompt does not contain the exact intake operation",
            )


def _canonical_manifest_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise PromptSessionError("PROMPT_RESULT_INVALID", f"{label} is invalid")
    requested = Path(value)
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", f"{label} is unavailable"
        ) from error
    if str(requested) != str(resolved):
        raise PromptSessionError("PROMPT_RESULT_INVALID", f"{label} is not canonical")
    return resolved


def _task_manifest_project(manifest: dict[str, object]) -> Path:
    primary_root = _canonical_manifest_path(
        manifest.get("primary_root"), "canonical Task primary root"
    )
    lane_root = _canonical_manifest_path(
        manifest.get("repo_root"), "canonical Task lane root"
    )
    source_root = _canonical_manifest_path(
        manifest.get("source_root"), "canonical Task lane scope"
    )
    scope = manifest.get("scope")
    if not isinstance(scope, str) or not scope:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical Task scope is invalid"
        )
    scope_path = Path(scope)
    if (
        scope_path.is_absolute()
        or ".." in scope_path.parts
        or scope_path.as_posix() != scope
    ):
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical Task scope is invalid"
        )
    try:
        primary_project = (primary_root / scope_path).resolve(strict=True)
        lane_project = (lane_root / scope_path).resolve(strict=True)
        primary_project.relative_to(primary_root)
        lane_project.relative_to(lane_root)
    except (OSError, ValueError) as error:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical Task scope is unavailable"
        ) from error
    if source_root != lane_project:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical Task lane scope is inconsistent"
        )
    return primary_project


def validate_prompt_location(
    home: Path,
    workflow: str,
    prompt_path: Path,
    expected_project: Path,
) -> None:
    owner_name = "task-implementer" if workflow == "task-implementer" else "sdlc-runs"
    try:
        owner_root = (home / owner_name).resolve(strict=True)
        prompt_path.relative_to(owner_root)
        canonical_project = expected_project.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID",
            "canonical prompt is outside the bound workflow state root",
        ) from error
    if prompt_path.parent.name != "prompts":
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID",
            "canonical prompt is outside a managed prompt directory",
        )
    workspace_root = prompt_path.parent.parent
    require_private_directory(owner_root)
    require_private_directory(workspace_root)
    require_private_directory(prompt_path.parent)
    manifest = load_json(workspace_root / "workspace.json")
    if workflow == "task-implementer":
        expected_schema = TASK_WORKSPACE_SCHEMA
    else:
        expected_schema = SDLC_WORKSPACE_SCHEMA
    if manifest.get("schema") != expected_schema:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "canonical prompt workspace schema is invalid"
        )
    manifest_prompt_root = _canonical_manifest_path(
        manifest.get("prompt_root", str(workspace_root / "prompts"))
        if workflow == "task-implementer"
        else str(workspace_root / "prompts"),
        "canonical prompt workspace root",
    )
    manifest_project = (
        _task_manifest_project(manifest)
        if workflow == "task-implementer"
        else _canonical_manifest_path(
            manifest.get("project_root"), "canonical prompt bound project"
        )
    )
    if manifest_prompt_root != prompt_path.parent or manifest_project != canonical_project:
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID",
            "canonical prompt does not belong to the bound project",
        )
