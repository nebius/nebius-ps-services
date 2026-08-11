#!/usr/bin/env python3
"""Validation for durable prompt-session event records."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from prompt_session_storage import (
    CLASSIFICATIONS,
    EVENT_SCHEMA,
    MATERIAL_CLASSIFICATIONS,
    MAX_PROMPT_BYTES,
    PROMPT_ID_RE,
    PROMPT_REF_RE,
    SHA256_RE,
    WORKFLOWS,
    PromptSessionError,
    identity_sha256,
    load_json,
    require_private_file,
    sha256_bytes,
)


def load_event(path: Path) -> dict[str, Any]:
    """Load one event only after validating its private record and journals."""

    event = load_json(path)
    phase = event.get("phase")
    classification = event.get("classification")
    project_root = event.get("project_root")
    session_digest = event.get("session_sha256")
    turn_digest = event.get("turn_sha256")
    raw_digest = event.get("raw_sha256")
    operation_id = event.get("operation_id")
    if (
        event.get("schema") != EVENT_SCHEMA
        or phase not in {"staged", "accepted", "consumed"}
        or not isinstance(session_digest, str)
        or not SHA256_RE.fullmatch(session_digest)
        or not isinstance(turn_digest, str)
        or not SHA256_RE.fullmatch(turn_digest)
        or not isinstance(raw_digest, str)
        or not SHA256_RE.fullmatch(raw_digest)
        or not isinstance(operation_id, str)
        or operation_id
        != sha256_bytes(f"{session_digest}:{turn_digest}:{raw_digest}".encode("utf-8"))
        or event.get("workflow") not in WORKFLOWS
        or not isinstance(project_root, str)
        or not Path(project_root).is_absolute()
        or event.get("project_sha256") != identity_sha256(project_root)
        or not re.fullmatch(r"[0-9a-f]{48}", str(event.get("accept_token") or ""))
        or not isinstance(event.get("staged_at"), str)
        or event.get("accepted_at") is not None
        and not isinstance(event.get("accepted_at"), str)
        or event.get("consumed_at") is not None
        and not isinstance(event.get("consumed_at"), str)
    ):
        raise PromptSessionError("EVENT_INVALID", "prompt-session event is invalid")
    raw_path = path.parent / "raw.md"
    require_private_file(raw_path)
    raw = raw_path.read_bytes()
    if len(raw) > MAX_PROMPT_BYTES or b"\x00" in raw or sha256_bytes(raw) != raw_digest:
        raise PromptSessionError(
            "EVENT_INVALID", "prompt-session raw journal is invalid"
        )
    if phase == "staged":
        if (
            classification is not None
            or event.get("accepted_at") is not None
            or event.get("consumed_at") is not None
        ):
            raise PromptSessionError(
                "EVENT_INVALID", "staged event transition is invalid"
            )
        return event
    if classification not in CLASSIFICATIONS or event.get("accepted_at") is None:
        raise PromptSessionError(
            "EVENT_INVALID", "accepted event transition is invalid"
        )
    material = classification in MATERIAL_CLASSIFICATIONS
    refinement_keys = {
        "refined_sha256",
        "refined_source_sha256",
        "base_prompt_path",
        "base_prompt_sha256",
        "new_objective",
    }
    if material:
        refined_path = path.parent / "refined.md"
        require_private_file(refined_path)
        refined = refined_path.read_bytes()
        new_objective = event.get("new_objective")
        base_path = event.get("base_prompt_path")
        base_digest = event.get("base_prompt_sha256")
        if (
            not isinstance(event.get("refined_sha256"), str)
            or sha256_bytes(refined) != event.get("refined_sha256")
            or not isinstance(event.get("refined_source_sha256"), str)
            or not SHA256_RE.fullmatch(str(event["refined_source_sha256"]))
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
                "EVENT_INVALID", "material event transition is invalid"
            )
    elif any(key in event for key in refinement_keys):
        raise PromptSessionError(
            "EVENT_INVALID", "nonmaterial event carries refinement state"
        )
    if phase == "accepted":
        if event.get("consumed_at") is not None:
            raise PromptSessionError(
                "EVENT_INVALID", "accepted event transition is invalid"
            )
        return event
    if event.get("consumed_at") is None:
        raise PromptSessionError(
            "EVENT_INVALID", "consumed event transition is invalid"
        )
    result_keys = {
        "prompt_id",
        "prompt_ref",
        "prompt_path",
        "prompt_sha256",
        "run_id",
        "objective_terminal",
    }
    if material:
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
        ):
            raise PromptSessionError(
                "EVENT_INVALID", "consumed event result is invalid"
            )
    elif any(key in event for key in result_keys):
        raise PromptSessionError(
            "EVENT_INVALID", "nonmaterial event carries prompt result"
        )
    return event
