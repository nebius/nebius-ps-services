#!/usr/bin/env python3
"""Secure private state for capture-only prompt-session intake hooks."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import shlex
import stat
from typing import Any

from prompt_session_event import load_event as _load_event
from prompt_session_result import (
    OPERATION_MARKER_PREFIX,
    validate_prompt_location as _validate_prompt_location,
    validate_prompt_result as _validate_prompt_result,
)
from prompt_session_storage import (
    BINDING_SCHEMA,
    CONTINUATION_SCHEMA,
    CURRENT_EVENT_SCHEMA,
    DISPOSITIONS,
    EVENT_SCHEMA,
    MATERIAL_CLASSIFICATIONS,
    MAX_PROMPT_BYTES,
    NOOP_REASONS,
    PROMPT_ID_RE,
    PROMPT_REF_RE,
    REGISTRY_SCHEMA,
    SHA256_RE,
    WORKFLOWS,
    PromptSessionError,
    atomic_write as _atomic_write,
    binding_path as _binding_path,
    codex_home,
    contains_secret,
    continuation_path as _continuation_path,
    current_event_path as _current_event_path,
    ensure_root as _ensure_root,
    event_operation_id,
    event_path as _event_path,
    identity_sha256,
    load_json as _load_json,
    load_registry as _load_registry,
    require_private_directory as _require_private_directory,
    require_private_file as _require_private_file,
    sha256_bytes,
    stable_json,
    state_lock,
    state_root,
    utc_now,
    write_registry as _write_registry,
)


def _canonical_project(cwd: object) -> Path:
    if not isinstance(cwd, str) or not cwd.strip():
        raise PromptSessionError("IDENTITY_REQUIRED", "hook payload is missing cwd")
    requested = Path(cwd).expanduser()
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise PromptSessionError(
            "PROJECT_UNAVAILABLE", "bound project path is unavailable"
        ) from error
    if not resolved.is_dir():
        raise PromptSessionError(
            "PROJECT_UNAVAILABLE", "bound project path is not a directory"
        )
    return resolved


def _explicit_binding(prompt: str, cwd: Path) -> tuple[str, Path] | None:
    try:
        tokens = shlex.split(prompt, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    command = tokens[0]
    if command.startswith("$"):
        command = command[1:]
    workflow: str | None = None
    if command == "task-implementer":
        workflow = "task-implementer"
    elif command == "sdlc-start":
        workflow = "agentic-sdlc"
    if workflow is None or len(tokens) < 2:
        return None
    project = cwd
    if tokens[1:3] == ["workspace", "init"]:
        if len(tokens) == 4:
            candidate = Path(tokens[3]).expanduser()
            project = (
                (cwd / candidate).resolve()
                if not candidate.is_absolute()
                else candidate.resolve()
            )
        elif len(tokens) > 4:
            return None
    elif tokens[1] != "run" or len(tokens) != 3:
        return None
    return workflow, _canonical_project(str(project))


def _binding_record(session_id: object, workflow: str, project: Path) -> dict[str, Any]:
    return {
        "schema": BINDING_SCHEMA,
        "session_sha256": identity_sha256(session_id),
        "workflow": workflow,
        "project_root": str(project),
        "project_sha256": identity_sha256(project),
        "bound_at": utc_now(),
    }


def _validated_binding(value: dict[str, Any], session_id: object) -> dict[str, Any]:
    required = {
        "schema",
        "session_sha256",
        "workflow",
        "project_root",
        "project_sha256",
        "bound_at",
    }
    project_root = value.get("project_root")
    if (
        set(value) != required
        or value.get("schema") != BINDING_SCHEMA
        or value.get("session_sha256") != identity_sha256(session_id)
        or value.get("workflow") not in WORKFLOWS
        or not isinstance(project_root, str)
        or not Path(project_root).is_absolute()
        or value.get("project_sha256") != identity_sha256(project_root)
        or not isinstance(value.get("bound_at"), str)
    ):
        raise PromptSessionError("BINDING_INVALID", "session binding is invalid")
    return value


def bind_session(
    root: Path, session_id: object, workflow: str, project: Path
) -> dict[str, Any]:
    if workflow not in WORKFLOWS:
        raise PromptSessionError("WORKFLOW_INVALID", "bound workflow is unsupported")
    path = _binding_path(root, session_id)
    expected = _binding_record(session_id, workflow, project)
    if path.exists():
        current = _validated_binding(_load_json(path), session_id)
        if current.get("workflow") != workflow or not _binding_matches_project(
            root, current, project
        ):
            raise PromptSessionError(
                "BINDING_CONFLICT",
                "session is already bound to a different project or workflow",
            )
        return current
    _atomic_write(path, stable_json(expected), exclusive=True)
    return expected


def load_binding(root: Path, session_id: object) -> dict[str, Any] | None:
    path = _binding_path(root, session_id)
    if not path.exists():
        return None
    return _validated_binding(_load_json(path), session_id)


def _entry_matches_project(root: Path, entry: dict[str, Any], project: Path) -> bool:
    if entry.get("project_root") == str(project):
        return True
    if entry.get("workflow") != "task-implementer":
        return False
    prompt_path = entry.get("prompt_path")
    if not isinstance(prompt_path, str) or not Path(prompt_path).is_absolute():
        return False
    try:
        _validate_prompt_location(
            root.parent,
            "task-implementer",
            Path(prompt_path),
            project,
        )
    except PromptSessionError:
        return False
    return True


def _binding_matches_project(
    root: Path, binding: dict[str, Any], project: Path
) -> bool:
    bound_project = binding.get("project_root")
    if bound_project == str(project):
        return True
    if binding.get("workflow") != "task-implementer":
        return False
    registry = _load_registry(root)
    return any(
        isinstance(entry, dict)
        and entry.get("active") is True
        and entry.get("workflow") == "task-implementer"
        and _entry_matches_project(root, entry, Path(str(bound_project)))
        and _entry_matches_project(root, entry, project)
        for entry in registry["entries"]
    )


def _objective_identities(entries: list[dict[str, Any]]) -> set[tuple[object, ...]]:
    return {
        (entry.get("workflow"), entry.get("prompt_id"), entry.get("prompt_path"))
        for entry in entries
    }


def _attach_unique_active(
    root: Path, session_id: object, project: Path
) -> dict[str, Any] | None:
    registry = _load_registry(root)
    candidates = [
        entry
        for entry in registry["entries"]
        if isinstance(entry, dict)
        and entry.get("active") is True
        and _entry_matches_project(root, entry, project)
        and entry.get("workflow") in WORKFLOWS
    ]
    if len(_objective_identities(candidates)) > 1:
        raise PromptSessionError(
            "OBJECTIVE_AMBIGUOUS",
            "multiple active objectives require an explicit workflow selection",
        )
    if not candidates:
        return None
    return bind_session(root, session_id, str(candidates[0]["workflow"]), project)


def _excluded_payload(payload: dict[str, Any]) -> bool:
    if payload.get("stop_hook_active") or payload.get("is_subagent"):
        return True
    origin = str(payload.get("prompt_source") or payload.get("source") or "").lower()
    if origin in {"stop", "continuation", "compaction", "subagent", "system"}:
        return True
    agent_type = str(payload.get("agent_type") or "").lower()
    return bool(agent_type and agent_type not in {"root", "primary"})


def _context(message: str) -> dict[str, Any]:
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": message,
        },
    }


def _stage_turn(
    root: Path,
    binding: dict[str, Any],
    session_id: object,
    turn_id: object,
    prompt: str,
) -> dict[str, Any]:
    submitted = prompt.encode("utf-8")
    if len(submitted) > MAX_PROMPT_BYTES or b"\x00" in submitted:
        raise PromptSessionError(
            "PROMPT_TOO_LARGE"
            if len(submitted) > MAX_PROMPT_BYTES
            else "PROMPT_INVALID",
            "direct prompt is invalid or exceeds the intake limit",
        )
    submitted_digest = sha256_bytes(submitted)
    session_digest = identity_sha256(session_id)
    turn_digest = identity_sha256(turn_id)
    operation_id = event_operation_id(
        session_digest, turn_digest, submitted_digest
    )
    path = _event_path(root, session_id, turn_id)
    current_path = _current_event_path(root, session_id)
    if path.exists():
        event = _load_event(path)
        if event.get("phase") == "discarded":
            return _context(
                "Prompt-session intake already discarded capture for this exact turn. "
                "Continue the direct request without staging or replaying it."
            )
        if (
            event.get("session_sha256") != session_digest
            or event.get("turn_sha256") != turn_digest
            or event.get("submitted_sha256") != submitted_digest
            or event.get("operation_id") != operation_id
            or event.get("workflow") != binding.get("workflow")
            or event.get("project_root") != binding.get("project_root")
            or event.get("phase") not in {"staged", "accepted", "consumed"}
        ):
            raise PromptSessionError(
                "TURN_CONFLICT", "turn identity was reused with different content"
            )
        token = str(event.get("accept_token") or "")
        if not token:
            raise PromptSessionError("EVENT_INVALID", "staged turn token is missing")
        if event.get("phase") == "consumed":
            return _context(
                "Prompt-session intake recognized this exact turn as already consumed. "
                "Continue the direct request without projecting or merging it again."
            )
        _claim_bound_writer(root, binding, session_id)
    else:
        token = secrets.token_hex(24)
        event = {
            "schema": EVENT_SCHEMA,
            "phase": "staged",
            "session_sha256": session_digest,
            "turn_sha256": turn_digest,
            "submitted_sha256": submitted_digest,
            "operation_id": operation_id,
            "workflow": binding["workflow"],
            "project_root": binding["project_root"],
            "project_sha256": binding["project_sha256"],
            "accept_token": token,
            "disposition": None,
            "classification": None,
            "reason": None,
            "staged_at": utc_now(),
            "accepted_at": None,
            "consumed_at": None,
        }
        _claim_bound_writer(root, binding, session_id)
        _atomic_write(path, stable_json(event), exclusive=True)
        sequence = 1
        if current_path.exists():
            current = _load_json(current_path)
            if (
                current.get("schema") != CURRENT_EVENT_SCHEMA
                or current.get("session_sha256") != session_digest
                or not isinstance(current.get("sequence"), int)
                or isinstance(current.get("sequence"), bool)
                or current["sequence"] < 1
            ):
                raise PromptSessionError(
                    "CURRENT_EVENT_INVALID", "current-event receipt is invalid"
                )
            sequence = int(current["sequence"]) + 1
        current = {
            "schema": CURRENT_EVENT_SCHEMA,
            "session_sha256": session_digest,
            "turn_sha256": turn_digest,
            "event_sha256": identity_sha256(path),
            "sequence": sequence,
            "updated_at": utc_now(),
        }
        _atomic_write(current_path, stable_json(current))
    if event.get("phase") == "accepted":
        return _context(
            "Prompt-session intake recognized this exact turn as already accepted. "
            f"Resume only its existing `{path}` transition with token `{token}`, reuse "
            "its recorded operation ID, and consume it after the canonical prompt merge. "
            "Continue handling the direct request; do not project, merge, or execute "
            "through a second path."
        )
    if not current_path.exists():
        return _context(
            "Prompt-session intake found that this staged turn has no current capture "
            "claim. Continue the direct request without classifying or merging it."
        )
    current = _load_json(current_path)
    if (
        current.get("schema") != CURRENT_EVENT_SCHEMA
        or current.get("session_sha256") != session_digest
        or current.get("turn_sha256") != turn_digest
        or current.get("event_sha256") != identity_sha256(path)
    ):
        return _context(
            "Prompt-session intake found that this staged turn is no longer the "
            "session's current capture claim. Continue the direct request without "
            "classifying or merging the stale event."
        )
    return _context(
        "Continue handling the current direct request normally. Prompt-session intake "
        "also staged metadata-only capture. Use the internal $prompt-session-intake "
        "coordinator to record merge, noop, or sensitive for the already-delivered "
        "prompt. For merge, create a private project-intent projection containing only "
        "durable objective facts, accept this exact "
        f"event `{path}` with token `{token}`, merge only through the bound "
        f"{binding['workflow']} prompt adapter, then consume the same event. Exclude "
        "skill/workflow execution, shell/tool actions, delivery or agent control, "
        "status, conversation, unrelated, and duplicate-only content; retain commands "
        "only when they define a project contract or example. Do not start, resume, or "
        "select a workflow from capture. Never expose the private event path, token, "
        "or project-intent file in the final response."
    )


def _claim_bound_writer(
    root: Path, binding: dict[str, Any], session_id: object
) -> None:
    registry = _load_registry(root)
    active = [
        entry
        for entry in registry["entries"]
        if isinstance(entry, dict)
        and entry.get("active") is True
        and _entry_matches_project(
            root, entry, Path(str(binding.get("project_root")))
        )
    ]
    if len(_objective_identities(active)) > 1:
        raise PromptSessionError(
            "REGISTRY_INVALID", "project has multiple active prompt objectives"
        )
    if not active:
        return
    entry = active[0]
    if entry.get("workflow") != binding.get("workflow"):
        raise PromptSessionError(
            "WORKFLOW_CONFLICT", "active objective is owned by another workflow"
        )
    session_digest = identity_sha256(session_id)
    active_ids = {id(candidate) for candidate in active}
    registry["entries"] = [
        candidate
        for candidate in registry["entries"]
        if id(candidate) not in active_ids
    ]
    entry = dict(entry)
    entry["project_root"] = str(binding["project_root"])
    entry["project_sha256"] = identity_sha256(binding["project_root"])
    entry["writer_session_sha256"] = session_digest
    entry["updated_at"] = utc_now()
    registry["entries"].append(entry)
    _write_registry(root, registry)


def evaluate_submit(
    payload: dict[str, Any], home: Path | None = None
) -> dict[str, Any]:
    if payload.get("hook_event_name") != "UserPromptSubmit" or _excluded_payload(
        payload
    ):
        return {}
    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    prompt = payload.get("prompt")
    if session_id in {None, ""} or not isinstance(prompt, str):
        return {}
    if contains_secret(prompt):
        return _context(
            "Prompt-session capture was skipped because the direct input matched "
            "secret detection. Continue handling the user request normally, but do "
            "not persist, quote, or repeat sensitive content."
        )
    project = _canonical_project(payload.get("cwd"))
    selected_home = (home or codex_home(payload)).resolve()
    root = _ensure_root(selected_home)
    with state_lock(root):
        continuation = _continuation_path(root, session_id)
        if continuation.exists():
            marker = _load_json(continuation)
            expected = {
                "schema": CONTINUATION_SCHEMA,
                "session_sha256": identity_sha256(session_id),
            }
            if (
                marker.get("schema") != expected["schema"]
                or marker.get("session_sha256") != expected["session_sha256"]
                or not isinstance(marker.get("prompt_sha256"), str)
            ):
                raise PromptSessionError(
                    "CONTINUATION_INVALID", "Stop continuation marker is invalid"
                )
            continuation.unlink()
            if marker["prompt_sha256"] == sha256_bytes(prompt.encode("utf-8")):
                return _context(
                    "Prompt-session intake excluded this exact shared Stop continuation. "
                    "Continue the requested recovery without staging it as user intent."
                )
        explicit = _explicit_binding(prompt, project)
        if explicit is not None:
            workflow, bound_project = explicit
            binding = bind_session(root, session_id, workflow, bound_project)
            _claim_bound_writer(root, binding, session_id)
            return _context(
                f"Prompt-session intake bound this session to {workflow} for the exact selected project. "
                "This explicit invocation continues normally; later safe direct turns use the bound intake path."
            )
        binding = load_binding(root, session_id)
        if binding is None:
            binding = _attach_unique_active(root, session_id, project)
        if binding is None or turn_id in {None, ""}:
            return {}
        if not _binding_matches_project(root, binding, project):
            raise PromptSessionError(
                "PROJECT_MISMATCH",
                "current directory does not match the session's bound project",
            )
        return _stage_turn(root, binding, session_id, turn_id, prompt)


def mark_stop_continuation(
    home: Path, payload: dict[str, Any], reason: str
) -> dict[str, Any]:
    """Exclude exactly the continuation prompt created from one Stop reason."""

    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if session_id in {None, ""} or turn_id in {None, ""} or not reason:
        raise PromptSessionError(
            "IDENTITY_REQUIRED", "Stop continuation identity and reason are required"
        )
    root = _ensure_root(home.resolve())
    marker = {
        "schema": CONTINUATION_SCHEMA,
        "session_sha256": identity_sha256(session_id),
        "source_turn_sha256": identity_sha256(turn_id),
        "prompt_sha256": sha256_bytes(reason.encode("utf-8")),
        "created_at": utc_now(),
    }
    with state_lock(root):
        _atomic_write(_continuation_path(root, session_id), stable_json(marker))
    return {"status": "marked", "prompt_sha256": marker["prompt_sha256"]}


def _resolve_event(root: Path, event_path: Path) -> Path:
    requested = event_path.expanduser()
    try:
        resolved = requested.resolve(strict=True)
        expected_root = root.resolve(strict=True)
        relative = resolved.relative_to(expected_root / "sessions")
    except (OSError, ValueError) as error:
        raise PromptSessionError(
            "EVENT_PATH_INVALID", "event path is outside prompt-session state"
        ) from error
    if (
        len(relative.parts) != 4
        or relative.parts[1] != "events-v2"
        or relative.parts[3] != "event.json"
        or not re.fullmatch(r"[0-9a-f]{24}", relative.parts[0])
        or not re.fullmatch(r"[0-9a-f]{24}", relative.parts[2])
    ):
        raise PromptSessionError(
            "EVENT_PATH_INVALID", "event path must name one event-v2 receipt"
        )
    return resolved


def _check_token(event: dict[str, Any], token: str) -> None:
    expected = str(event.get("accept_token") or "")
    if not expected or not secrets.compare_digest(expected, token):
        raise PromptSessionError(
            "EVENT_TOKEN_INVALID", "event acceptance token is invalid"
        )


def _check_session(event: dict[str, Any], session_id: object) -> None:
    if event.get("session_sha256") != identity_sha256(session_id):
        raise PromptSessionError(
            "SESSION_MISMATCH", "event does not belong to the current Codex session"
        )


def _check_current_event(path: Path, event: dict[str, Any]) -> None:
    # current_event_path normally hashes the raw session ID. Resolve the receipt from
    # the validated event's session directory instead so no digest is hashed twice.
    current_path = path.parents[2] / "current-event-v2.json"
    if not current_path.exists():
        raise PromptSessionError(
            "STALE_EVENT", "staged event is not the current capture claim"
        )
    current = _load_json(current_path)
    if (
        set(current)
        != {
            "schema",
            "session_sha256",
            "turn_sha256",
            "event_sha256",
            "sequence",
            "updated_at",
        }
        or current.get("schema") != CURRENT_EVENT_SCHEMA
        or current.get("session_sha256") != event.get("session_sha256")
        or current.get("turn_sha256") != event.get("turn_sha256")
        or current.get("event_sha256") != identity_sha256(path)
        or not isinstance(current.get("sequence"), int)
        or isinstance(current.get("sequence"), bool)
        or current["sequence"] < 1
        or not isinstance(current.get("updated_at"), str)
    ):
        raise PromptSessionError(
            "STALE_EVENT", "staged event is not the current capture claim"
        )


def _safe_external_file(path: Path, label: str) -> tuple[Path, bytes]:
    requested = path.expanduser()
    if requested.is_symlink():
        raise PromptSessionError("INPUT_UNSAFE", f"{label} must not be a symlink")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise PromptSessionError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from error
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as error:
        raise PromptSessionError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise PromptSessionError(
                "INPUT_UNSAFE", f"{label} must be one regular file"
            )
        if os.name == "posix" and stat.S_IMODE(opened.st_mode) != 0o600:
            raise PromptSessionError(
                "INPUT_UNSAFE", f"{label} must use private mode 0600"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_PROMPT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_PROMPT_BYTES or b"\x00" in raw:
        raise PromptSessionError("INPUT_INVALID", f"{label} is invalid")
    return resolved, raw


def _validate_merge_base_request(
    *,
    prompt_path: Path | None,
    base_sha256: str | None,
    new_objective: bool,
) -> None:
    if new_objective:
        if prompt_path is not None or base_sha256 is not None:
            raise PromptSessionError(
                "PROMPT_BASE_CONFLICT",
                "new objective cannot name an existing prompt base",
            )
        return
    if prompt_path is None or base_sha256 is None:
        raise PromptSessionError(
            "PROMPT_BASE_REQUIRED",
            "material intent needs both a current prompt path and digest",
        )


def accept_event(
    home: Path,
    event_path: Path,
    token: str,
    disposition: str,
    *,
    session_id: object,
    classification: str | None = None,
    reason: str | None = None,
    projection_file: Path | None = None,
    prompt_path: Path | None = None,
    base_sha256: str | None = None,
    new_objective: bool = False,
) -> dict[str, Any]:
    if disposition not in DISPOSITIONS:
        raise PromptSessionError(
            "DISPOSITION_INVALID", "turn disposition is unsupported"
        )
    root = _ensure_root(home.resolve())
    with state_lock(root):
        path = _resolve_event(root, event_path)
        event = _load_event(path)
        _check_session(event, session_id)
        if event.get("phase") == "discarded":
            if disposition != "sensitive":
                raise PromptSessionError(
                    "EVENT_CONFLICT", "discarded event disposition differs"
                )
            return _public_event(event, path)
        _check_token(event, token)
        if event.get("phase") == "consumed":
            raise PromptSessionError("EVENT_CONSUMED", "event was already consumed")
        if event.get("phase") == "accepted":
            if (
                event.get("disposition") != disposition
                or event.get("classification") != classification
                or event.get("reason") != reason
            ):
                raise PromptSessionError(
                    "EVENT_CONFLICT", "accepted event disposition differs"
                )
            if disposition == "merge":
                if projection_file is None:
                    raise PromptSessionError(
                        "EVENT_CONFLICT", "accepted event retry omits its projection"
                    )
                _validate_merge_base_request(
                    prompt_path=prompt_path,
                    base_sha256=base_sha256,
                    new_objective=new_objective,
                )
                _, supplied = _safe_external_file(
                    projection_file, "project-intent projection"
                )
                try:
                    projection = supplied.decode("utf-8").strip().encode("utf-8") + b"\n"
                except UnicodeDecodeError as error:
                    raise PromptSessionError(
                        "INPUT_INVALID", "project-intent projection is not UTF-8"
                    ) from error
                requested_prompt = (
                    str(prompt_path.expanduser().resolve())
                    if prompt_path is not None
                    else None
                )
                if (
                    sha256_bytes(projection) != event.get("projection_sha256")
                    or requested_prompt != event.get("base_prompt_path")
                    or base_sha256 != event.get("base_prompt_sha256")
                    or new_objective is not event.get("new_objective")
                ):
                    raise PromptSessionError(
                        "EVENT_CONFLICT", "accepted event retry differs"
                    )
            elif any((projection_file, prompt_path, base_sha256, new_objective)):
                raise PromptSessionError(
                    "NONMERGE_MUTATION",
                    "non-merge turns cannot carry prompt mutation inputs",
                )
            return _public_event(event, path)
        if event.get("phase") != "staged":
            raise PromptSessionError("EVENT_INVALID", "event phase is invalid")
        _check_current_event(path, event)
        if disposition == "sensitive":
            if classification is not None or reason not in {None, "sensitive"}:
                raise PromptSessionError(
                    "SENSITIVE_DISPOSITION_INVALID",
                    "sensitive disposition cannot carry classification or detail",
                )
            if any((projection_file, prompt_path, base_sha256, new_objective)):
                raise PromptSessionError(
                    "SENSITIVE_MUTATION",
                    "sensitive disposition cannot carry prompt mutation inputs",
                )
            discarded_at = utc_now()
            event.update(
                {
                    "phase": "discarded",
                    "disposition": "sensitive",
                    "classification": None,
                    "reason": "sensitive",
                    "accepted_at": discarded_at,
                    "consumed_at": discarded_at,
                }
            )
            for key in ("submitted_sha256", "operation_id", "accept_token"):
                event.pop(key, None)
            projection_path = path.parent / "project-intent.md"
            if projection_path.exists() or projection_path.is_symlink():
                _require_private_file(projection_path)
                projection_path.unlink()
            _atomic_write(path, stable_json(event))
            return _public_event(event, path)
        if disposition == "merge":
            if classification not in MATERIAL_CLASSIFICATIONS:
                raise PromptSessionError(
                    "CLASSIFICATION_INVALID",
                    "merge requires a material project-intent classification",
                )
            if reason is not None:
                raise PromptSessionError(
                    "MERGE_REASON_INVALID", "merge disposition cannot carry a no-op reason"
                )
            if projection_file is None:
                raise PromptSessionError(
                    "PROJECTION_REQUIRED", "merge requires a project-intent projection"
                )
            _validate_merge_base_request(
                prompt_path=prompt_path,
                base_sha256=base_sha256,
                new_objective=new_objective,
            )
            projection_source, supplied = _safe_external_file(
                projection_file, "project-intent projection"
            )
            try:
                projection_text = supplied.decode("utf-8").strip()
            except UnicodeDecodeError as error:
                raise PromptSessionError(
                    "INPUT_INVALID", "project-intent projection is not UTF-8"
                ) from error
            if not projection_text:
                raise PromptSessionError(
                    "INPUT_INVALID", "project-intent projection is empty"
                )
            projection = projection_text.encode("utf-8") + b"\n"
            if OPERATION_MARKER_PREFIX in projection:
                raise PromptSessionError(
                    "PROMPT_RESERVED_INPUT",
                    "project-intent projection uses the reserved operation marker namespace",
                )
            if contains_secret(projection_text):
                raise PromptSessionError(
                    "PROMPT_SENSITIVE_INPUT",
                    "project-intent projection contains secret material",
                )
            if new_objective:
                current_prompt = None
                current_digest = None
            else:
                if prompt_path is None:
                    raise PromptSessionError(
                        "PROMPT_BASE_REQUIRED",
                        "material intent needs both a current prompt path and digest",
                    )
                current_prompt, current = _safe_external_file(
                    prompt_path, "canonical prompt"
                )
                _validate_prompt_location(
                    home.resolve(),
                    str(event["workflow"]),
                    current_prompt,
                    Path(str(event["project_root"])),
                )
                current_digest = sha256_bytes(current)
                if base_sha256 != current_digest:
                    raise PromptSessionError(
                        "PROMPT_DRIFT",
                        "canonical prompt changed after staging; reconcile the manual edit explicitly",
                    )
            _atomic_write(path.parent / "project-intent.md", projection)
            event.update(
                {
                    "projection_sha256": sha256_bytes(projection),
                    "projection_source_sha256": identity_sha256(projection_source),
                    "base_prompt_path": str(current_prompt) if current_prompt else None,
                    "base_prompt_sha256": current_digest,
                    "new_objective": new_objective,
                }
            )
        else:
            if classification is not None or reason not in NOOP_REASONS:
                raise PromptSessionError(
                    "NOOP_REASON_INVALID", "no-op requires one supported reason"
                )
            if (
                projection_file is not None
                or prompt_path is not None
                or base_sha256 is not None
                or new_objective
            ):
                raise PromptSessionError(
                    "NONMERGE_MUTATION",
                    "no-op turns cannot carry prompt mutation inputs",
                )
        event["phase"] = "accepted"
        event["disposition"] = disposition
        event["classification"] = classification
        event["reason"] = reason
        event["accepted_at"] = utc_now()
        _atomic_write(path, stable_json(event))
        return _public_event(event, path)


def _public_event(event: dict[str, Any], path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": event.get("phase"),
        "disposition": event.get("disposition"),
        "classification": event.get("classification"),
        "reason": event.get("reason"),
        "workflow": event.get("workflow"),
        "event": str(path),
    }
    if event.get("operation_id"):
        result["operation_id"] = event["operation_id"]
    if event.get("projection_sha256"):
        result["projection_sha256"] = event["projection_sha256"]
        result["projection_file"] = str(path.parent / "project-intent.md")
    if "duplicate" in event:
        result["duplicate"] = event["duplicate"]
    return result


def _claim_registry(
    root: Path,
    event: dict[str, Any],
    prompt_id: str,
    prompt_ref: str,
    prompt_path: Path,
    prompt_sha256: str,
    *,
    terminal: bool = False,
) -> None:
    registry = _load_registry(root)
    entries = [entry for entry in registry["entries"] if isinstance(entry, dict)]
    project_root = str(event["project_root"])
    workflow = str(event["workflow"])
    session_digest = str(event["session_sha256"])
    _upsert_registry_objective(
        root,
        entries,
        project_root=project_root,
        workflow=workflow,
        prompt_id=prompt_id,
        prompt_ref=prompt_ref,
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha256,
        session_digest=session_digest,
        last_turn_sha256=str(event["turn_sha256"]),
        terminal=terminal,
    )


def _upsert_registry_objective(
    root: Path,
    entries: list[dict[str, Any]],
    *,
    project_root: str,
    workflow: str,
    prompt_id: str,
    prompt_ref: str,
    prompt_path: Path,
    prompt_sha256: str,
    session_digest: str,
    last_turn_sha256: str | None,
    terminal: bool,
) -> None:
    project = Path(project_root)
    project_entries = [
        entry
        for entry in entries
        if entry.get("project_root") == project_root
        or (
            workflow == "task-implementer"
            and entry.get("workflow") == workflow
            and _entry_matches_project(root, entry, project)
        )
    ]
    active_entries = [entry for entry in project_entries if entry.get("active") is True]
    if len(_objective_identities(active_entries)) > 1:
        raise PromptSessionError(
            "REGISTRY_INVALID", "project has multiple active prompt objectives"
        )
    if active_entries:
        active = active_entries[0]
        if active.get("workflow") != workflow:
            raise PromptSessionError(
                "WORKFLOW_CONFLICT",
                "active objective is owned by a different workflow",
            )
        if active.get("prompt_id") != prompt_id:
            raise PromptSessionError(
                "OBJECTIVE_CONFLICT",
                "another active objective already owns this project intake registry",
            )
    project_entry_ids = {id(entry) for entry in project_entries}
    entries[:] = [
        entry for entry in entries if id(entry) not in project_entry_ids
    ]
    entries.append(
        {
            "project_root": project_root,
            "project_sha256": identity_sha256(project_root),
            "workflow": workflow,
            "prompt_id": prompt_id,
            "prompt_ref": prompt_ref,
            "prompt_path": str(prompt_path),
            "prompt_sha256": prompt_sha256,
            "writer_session_sha256": None if terminal else session_digest,
            "last_turn_sha256": last_turn_sha256,
            "active": not terminal,
            "updated_at": utc_now(),
        }
    )
    _write_registry(root, {"schema": REGISTRY_SCHEMA, "entries": entries})


def register_objective(
    home: Path,
    session_id: object,
    workflow: str,
    project: Path,
    *,
    prompt_id: str,
    prompt_ref: str,
    prompt_path: Path,
    prompt_sha256: str,
    terminal: bool,
) -> dict[str, Any]:
    """Register the result of an explicit bound workflow run or close it."""

    canonical_project = _canonical_project(str(project))
    if workflow not in WORKFLOWS:
        raise PromptSessionError(
            "WORKFLOW_INVALID", "objective workflow is unsupported"
        )
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "full prompt identity is invalid"
        )
    if not PROMPT_REF_RE.fullmatch(prompt_ref) or not prompt_id.removeprefix(
        "prompt-"
    ).startswith(prompt_ref):
        raise PromptSessionError("PROMPT_RESULT_INVALID", "prompt reference is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", prompt_sha256):
        raise PromptSessionError(
            "PROMPT_RESULT_INVALID", "prompt result digest is invalid"
        )
    resolved_prompt, raw = _safe_external_file(prompt_path, "canonical prompt")
    _validate_prompt_location(
        home.resolve(), workflow, resolved_prompt, canonical_project
    )
    if sha256_bytes(raw) != prompt_sha256:
        raise PromptSessionError(
            "PROMPT_RESULT_DRIFT", "canonical prompt result digest is stale"
        )
    _validate_prompt_result(
        raw,
        workflow=workflow,
        prompt_id=prompt_id,
        prompt_ref=prompt_ref,
    )
    root = _ensure_root(home.resolve())
    with state_lock(root):
        binding = load_binding(root, session_id)
        if binding is None:
            raise PromptSessionError(
                "BINDING_REQUIRED",
                "objective registration requires an explicit session binding",
            )
        binding_project = Path(str(binding.get("project_root")))
        binding_matches = binding_project == canonical_project
        if not binding_matches and workflow == "task-implementer":
            try:
                _validate_prompt_location(
                    home.resolve(), workflow, resolved_prompt, binding_project
                )
                binding_matches = True
            except PromptSessionError:
                binding_matches = False
        if binding.get("workflow") != workflow or not binding_matches:
            raise PromptSessionError(
                "BINDING_CONFLICT", "objective result differs from the session binding"
            )
        registry = _load_registry(root)
        entries = [entry for entry in registry["entries"] if isinstance(entry, dict)]
        _upsert_registry_objective(
            root,
            entries,
            project_root=str(canonical_project),
            workflow=workflow,
            prompt_id=prompt_id,
            prompt_ref=prompt_ref,
            prompt_path=resolved_prompt,
            prompt_sha256=prompt_sha256,
            session_digest=identity_sha256(session_id),
            last_turn_sha256=None,
            terminal=terminal,
        )
        return {
            "status": "terminal" if terminal else "active",
            "workflow": workflow,
            "prompt_id": prompt_id,
            "prompt_ref": prompt_ref,
        }


def consume_event(
    home: Path,
    event_path: Path,
    token: str,
    *,
    session_id: object,
    workflow: str,
    prompt_id: str | None = None,
    prompt_ref: str | None = None,
    prompt_path: Path | None = None,
    prompt_sha256: str | None = None,
    run_id: str | None = None,
    objective_terminal: bool = False,
    duplicate: bool = False,
) -> dict[str, Any]:
    root = _ensure_root(home.resolve())
    with state_lock(root):
        path = _resolve_event(root, event_path)
        event = _load_event(path)
        _check_session(event, session_id)
        if event.get("phase") == "discarded":
            return _public_event(event, path)
        _check_token(event, token)
        if event.get("workflow") != workflow or workflow not in WORKFLOWS:
            raise PromptSessionError(
                "WORKFLOW_CONFLICT", "consume workflow differs from binding"
            )
        if event.get("phase") == "consumed":
            if event.get("disposition") == "merge":
                requested_path = (
                    str(prompt_path.expanduser().resolve())
                    if prompt_path is not None
                    else None
                )
                if (
                    prompt_id != event.get("prompt_id")
                    or prompt_ref != event.get("prompt_ref")
                    or requested_path != event.get("prompt_path")
                    or prompt_sha256 != event.get("prompt_sha256")
                    or run_id != event.get("run_id")
                    or objective_terminal is not event.get("objective_terminal")
                    or duplicate is not event.get("duplicate")
                ):
                    raise PromptSessionError(
                        "EVENT_CONFLICT", "consumed event retry differs"
                    )
            elif any(
                (
                    prompt_id,
                    prompt_ref,
                    prompt_path,
                    prompt_sha256,
                    run_id,
                    objective_terminal,
                    duplicate,
                )
            ):
                raise PromptSessionError(
                    "NONMERGE_MUTATION",
                    "no-op consume cannot carry prompt or run identity",
                )
            return _public_event(event, path)
        if event.get("phase") != "accepted":
            raise PromptSessionError(
                "EVENT_NOT_ACCEPTED", "event must be accepted before consume"
            )
        if event.get("disposition") == "merge":
            if not all((prompt_id, prompt_ref, prompt_path, prompt_sha256)):
                raise PromptSessionError(
                    "PROMPT_RESULT_REQUIRED",
                    "material consume requires prompt result identity",
                )
            if not PROMPT_ID_RE.fullmatch(str(prompt_id)):
                raise PromptSessionError(
                    "PROMPT_RESULT_INVALID", "full prompt identity is invalid"
                )
            if not PROMPT_REF_RE.fullmatch(str(prompt_ref)) or not str(
                prompt_id
            ).removeprefix("prompt-").startswith(str(prompt_ref)):
                raise PromptSessionError(
                    "PROMPT_RESULT_INVALID", "prompt reference is invalid"
                )
            if not SHA256_RE.fullmatch(str(prompt_sha256)):
                raise PromptSessionError(
                    "PROMPT_RESULT_INVALID", "prompt result digest is invalid"
                )
            resolved, raw = _safe_external_file(prompt_path, "canonical prompt")
            _validate_prompt_location(
                home.resolve(),
                workflow,
                resolved,
                Path(str(event["project_root"])),
            )
            if sha256_bytes(raw) != prompt_sha256:
                raise PromptSessionError(
                    "PROMPT_RESULT_DRIFT", "canonical prompt result digest is stale"
                )
            operation_id = event.get("operation_id")
            if not isinstance(operation_id, str) or not SHA256_RE.fullmatch(
                operation_id
            ):
                raise PromptSessionError(
                    "EVENT_INVALID", "event operation identity is invalid"
                )
            projection_path = path.parent / "project-intent.md"
            projection = projection_path.read_bytes()
            projection_sha256 = str(event.get("projection_sha256") or "")
            if sha256_bytes(projection) != projection_sha256:
                raise PromptSessionError(
                    "EVENT_INVALID", "project-intent projection digest changed"
                )
            _validate_prompt_result(
                raw,
                workflow=workflow,
                prompt_id=str(prompt_id),
                prompt_ref=str(prompt_ref),
                operation_id=operation_id,
                projection_sha256=projection_sha256,
                projection=projection,
                duplicate=duplicate,
            )
            _claim_registry(
                root,
                event,
                str(prompt_id),
                str(prompt_ref),
                resolved,
                str(prompt_sha256),
                terminal=objective_terminal,
            )
            event.update(
                {
                    "prompt_id": prompt_id,
                    "prompt_ref": prompt_ref,
                    "prompt_path": str(resolved),
                    "prompt_sha256": prompt_sha256,
                    "run_id": run_id,
                    "objective_terminal": objective_terminal,
                    "duplicate": duplicate,
                }
            )
        elif any(
            (
                prompt_id,
                prompt_ref,
                prompt_path,
                prompt_sha256,
                run_id,
                objective_terminal,
                duplicate,
            )
        ):
            raise PromptSessionError(
                "NONMERGE_MUTATION",
                "no-op consume cannot carry prompt or run identity",
            )
        event["phase"] = "consumed"
        event["consumed_at"] = utc_now()
        _atomic_write(path, stable_json(event))
        return _public_event(event, path)


def release_writer(root: Path, session_id: object) -> None:
    registry = _load_registry(root)
    digest = identity_sha256(session_id)
    changed = False
    for entry in registry["entries"]:
        if isinstance(entry, dict) and entry.get("writer_session_sha256") == digest:
            entry["writer_session_sha256"] = None
            entry["updated_at"] = utc_now()
            changed = True
    if changed:
        _write_registry(root, registry)


def evaluate_stop(payload: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        return {"continue": True}
    session_id = payload.get("session_id")
    if session_id in {None, ""}:
        return {"continue": True}
    selected_home = (home or codex_home(payload)).resolve()
    root = state_root(selected_home)
    if not root.exists():
        return {"continue": True}
    _require_private_directory(root)
    with state_lock(root):
        release_writer(root, session_id)
        return {"continue": True}
