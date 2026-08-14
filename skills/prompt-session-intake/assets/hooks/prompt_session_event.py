#!/usr/bin/env python3
"""Validation for metadata-only prompt-session event-v2 records."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from prompt_session_storage import (
    DISPOSITIONS,
    EVENT_SCHEMA,
    MATERIAL_CLASSIFICATIONS,
    MAX_PROMPT_BYTES,
    NOOP_REASONS,
    PROMPT_ID_RE,
    PROMPT_REF_RE,
    SHA256_RE,
    WORKFLOWS,
    PromptSessionError,
    event_operation_id,
    identity_sha256,
    load_json,
    require_private_file,
    sha256_bytes,
)


COMMON_KEYS = {
    "schema",
    "phase",
    "session_sha256",
    "turn_sha256",
    "workflow",
    "project_root",
    "project_sha256",
    "disposition",
    "classification",
    "reason",
    "staged_at",
    "accepted_at",
    "consumed_at",
}
STAGED_SECRET_KEYS = {"submitted_sha256", "operation_id", "accept_token"}
PROJECTION_KEYS = {
    "projection_sha256",
    "projection_source_sha256",
    "base_prompt_path",
    "base_prompt_sha256",
    "new_objective",
}
RESULT_KEYS = {
    "prompt_id",
    "prompt_ref",
    "prompt_path",
    "prompt_sha256",
    "run_id",
    "objective_terminal",
    "duplicate",
}


def _valid_base(event: dict[str, Any]) -> bool:
    project_root = event.get("project_root")
    return bool(
        event.get("schema") == EVENT_SCHEMA
        and event.get("phase") in {"staged", "accepted", "consumed", "discarded"}
        and isinstance(event.get("session_sha256"), str)
        and SHA256_RE.fullmatch(str(event["session_sha256"]))
        and isinstance(event.get("turn_sha256"), str)
        and SHA256_RE.fullmatch(str(event["turn_sha256"]))
        and event.get("workflow") in WORKFLOWS
        and isinstance(project_root, str)
        and Path(project_root).is_absolute()
        and event.get("project_sha256") == identity_sha256(project_root)
        and event.get("disposition") in DISPOSITIONS | {None}
        and isinstance(event.get("staged_at"), str)
        and (
            event.get("accepted_at") is None
            or isinstance(event.get("accepted_at"), str)
        )
        and (
            event.get("consumed_at") is None
            or isinstance(event.get("consumed_at"), str)
        )
    )


def _validate_staged_identity(event: dict[str, Any]) -> None:
    submitted = event.get("submitted_sha256")
    operation = event.get("operation_id")
    token = event.get("accept_token")
    if (
        not isinstance(submitted, str)
        or not SHA256_RE.fullmatch(submitted)
        or not isinstance(operation, str)
        or operation
        != event_operation_id(
            str(event["session_sha256"]), str(event["turn_sha256"]), submitted
        )
        or not re.fullmatch(r"[0-9a-f]{48}", str(token or ""))
    ):
        raise PromptSessionError("EVENT_INVALID", "event-v2 identity is invalid")


def _validate_projection(path: Path, event: dict[str, Any]) -> None:
    projection_path = path.parent / "project-intent.md"
    require_private_file(projection_path)
    projection = projection_path.read_bytes()
    try:
        canonical_projection = (
            projection.decode("utf-8").strip().encode("utf-8") + b"\n"
        )
    except UnicodeDecodeError as error:
        raise PromptSessionError(
            "EVENT_INVALID", "accepted project-intent projection is not UTF-8"
        ) from error
    new_objective = event.get("new_objective")
    base_path = event.get("base_prompt_path")
    base_digest = event.get("base_prompt_sha256")
    if (
        not projection
        or canonical_projection == b"\n"
        or projection != canonical_projection
        or len(projection) > MAX_PROMPT_BYTES
        or b"\x00" in projection
        or sha256_bytes(projection) != event.get("projection_sha256")
        or not isinstance(event.get("projection_source_sha256"), str)
        or not SHA256_RE.fullmatch(str(event["projection_source_sha256"]))
        or not isinstance(new_objective, bool)
        or (new_objective and (base_path is not None or base_digest is not None))
        or (
            not new_objective
            and (
                not isinstance(base_path, str)
                or not Path(base_path).is_absolute()
                or not isinstance(base_digest, str)
                or not SHA256_RE.fullmatch(base_digest)
            )
        )
    ):
        raise PromptSessionError(
            "EVENT_INVALID", "accepted project-intent projection is invalid"
        )


def _validate_result(event: dict[str, Any]) -> None:
    if (
        not isinstance(event.get("prompt_id"), str)
        or not PROMPT_ID_RE.fullmatch(str(event["prompt_id"]))
        or not isinstance(event.get("prompt_ref"), str)
        or not PROMPT_REF_RE.fullmatch(str(event["prompt_ref"]))
        or not str(event["prompt_id"])
        .removeprefix("prompt-")
        .startswith(str(event["prompt_ref"]))
        or not isinstance(event.get("prompt_path"), str)
        or not Path(str(event["prompt_path"])).is_absolute()
        or not isinstance(event.get("prompt_sha256"), str)
        or not SHA256_RE.fullmatch(str(event["prompt_sha256"]))
        or event.get("run_id") is not None
        and not isinstance(event.get("run_id"), str)
        or not isinstance(event.get("objective_terminal"), bool)
        or not isinstance(event.get("duplicate"), bool)
    ):
        raise PromptSessionError("EVENT_INVALID", "consumed prompt result is invalid")


def load_event(path: Path) -> dict[str, Any]:
    """Load one event-v2 without reading any submitted prompt body."""

    event = load_json(path)
    if not _valid_base(event):
        raise PromptSessionError("EVENT_INVALID", "prompt-session event-v2 is invalid")
    phase = str(event["phase"])
    if phase == "discarded":
        if (
            set(event) != COMMON_KEYS
            or event.get("disposition") != "sensitive"
            or event.get("classification") is not None
            or event.get("reason") != "sensitive"
            or event.get("accepted_at") is None
            or event.get("consumed_at") is None
        ):
            raise PromptSessionError(
                "EVENT_INVALID", "sensitive event discard is invalid"
            )
        return event

    _validate_staged_identity(event)
    if phase == "staged":
        if (
            set(event) != COMMON_KEYS | STAGED_SECRET_KEYS
            or event.get("disposition") is not None
            or event.get("classification") is not None
            or event.get("reason") is not None
            or event.get("accepted_at") is not None
            or event.get("consumed_at") is not None
        ):
            raise PromptSessionError("EVENT_INVALID", "staged event is invalid")
        return event

    disposition = event.get("disposition")
    if event.get("accepted_at") is None:
        raise PromptSessionError("EVENT_INVALID", "accepted event is invalid")
    if disposition == "merge":
        expected = COMMON_KEYS | STAGED_SECRET_KEYS | PROJECTION_KEYS
        if event.get("classification") not in MATERIAL_CLASSIFICATIONS:
            raise PromptSessionError(
                "EVENT_INVALID", "merge classification is invalid"
            )
        if event.get("reason") is not None:
            raise PromptSessionError("EVENT_INVALID", "merge reason is invalid")
        _validate_projection(path, event)
    elif disposition == "noop":
        expected = COMMON_KEYS | STAGED_SECRET_KEYS
        if (
            event.get("classification") is not None
            or event.get("reason") not in NOOP_REASONS
        ):
            raise PromptSessionError("EVENT_INVALID", "no-op reason is invalid")
    else:
        raise PromptSessionError("EVENT_INVALID", "event disposition is invalid")

    if phase == "accepted":
        if set(event) != expected or event.get("consumed_at") is not None:
            raise PromptSessionError("EVENT_INVALID", "accepted event is invalid")
        return event

    if phase != "consumed" or event.get("consumed_at") is None:
        raise PromptSessionError("EVENT_INVALID", "consumed event is invalid")
    if disposition == "merge":
        if set(event) != expected | RESULT_KEYS:
            raise PromptSessionError("EVENT_INVALID", "consumed merge is invalid")
        _validate_result(event)
    elif set(event) != expected:
        raise PromptSessionError("EVENT_INVALID", "consumed no-op is invalid")
    return event
