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
    CLASSIFICATIONS,
    CONTINUATION_SCHEMA,
    EVENT_SCHEMA,
    MATERIAL_CLASSIFICATIONS,
    MAX_PROMPT_BYTES,
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
    ensure_root as _ensure_root,
    event_path as _event_path,
    identity_sha256,
    load_json as _load_json,
    load_registry as _load_registry,
    require_private_directory as _require_private_directory,
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
        if current.get("workflow") != workflow or current.get("project_root") != str(
            project
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


def _attach_unique_active(
    root: Path, session_id: object, project: Path
) -> dict[str, Any] | None:
    registry = _load_registry(root)
    candidates = [
        entry
        for entry in registry["entries"]
        if isinstance(entry, dict)
        and entry.get("active") is True
        and entry.get("project_root") == str(project)
        and entry.get("workflow") in WORKFLOWS
    ]
    if len(candidates) > 1:
        raise PromptSessionError(
            "OBJECTIVE_AMBIGUOUS",
            "multiple active objectives require an explicit workflow selection",
        )
    if not candidates:
        return None
    writer = candidates[0].get("writer_session_sha256")
    if writer not in {None, identity_sha256(session_id)}:
        raise PromptSessionError(
            "WRITER_CONFLICT",
            "active objective currently has a different writer session",
        )
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
    raw = prompt.encode("utf-8")
    if len(raw) > MAX_PROMPT_BYTES or b"\x00" in raw:
        raise PromptSessionError(
            "PROMPT_TOO_LARGE" if len(raw) > MAX_PROMPT_BYTES else "PROMPT_INVALID",
            "direct prompt is invalid or exceeds the intake limit",
        )
    digest = sha256_bytes(raw)
    path = _event_path(root, session_id, turn_id)
    if path.exists():
        event = _load_event(path)
        expected_operation = sha256_bytes(
            (
                f"{identity_sha256(session_id)}:{identity_sha256(turn_id)}:{digest}"
            ).encode("utf-8")
        )
        if (
            event.get("schema") != EVENT_SCHEMA
            or event.get("session_sha256") != identity_sha256(session_id)
            or event.get("turn_sha256") != identity_sha256(turn_id)
            or event.get("raw_sha256") != digest
            or event.get("operation_id") != expected_operation
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
                "Continue without refining, merging, or running it again."
            )
        _claim_bound_writer(root, binding, session_id)
    else:
        token = secrets.token_hex(24)
        event = {
            "schema": EVENT_SCHEMA,
            "phase": "staged",
            "session_sha256": identity_sha256(session_id),
            "turn_sha256": identity_sha256(turn_id),
            "raw_sha256": digest,
            "operation_id": sha256_bytes(
                (
                    f"{identity_sha256(session_id)}:{identity_sha256(turn_id)}:{digest}"
                ).encode("utf-8")
            ),
            "workflow": binding["workflow"],
            "project_root": binding["project_root"],
            "project_sha256": binding["project_sha256"],
            "accept_token": token,
            "classification": None,
            "staged_at": utc_now(),
            "accepted_at": None,
            "consumed_at": None,
        }
        _claim_bound_writer(root, binding, session_id)
        _atomic_write(path.parent / "raw.md", raw, exclusive=True)
        _atomic_write(path, stable_json(event), exclusive=True)
    if event.get("phase") == "accepted":
        return _context(
            "Prompt-session intake recognized this exact turn as already accepted. "
            f"Resume only its existing `{path}` transition with token `{token}`, reuse "
            "its recorded operation ID, and consume it after the bound workflow result. "
            "Do not refine, merge, or execute through a second path."
        )
    return _context(
        "Prompt-session intake staged the current direct turn. Use the internal "
        "$prompt-session-intake coordinator now: classify the delivered prompt; "
        "for material intent create a concise lossless refined file, accept this "
        f"exact event `{path}` with token `{token}`, merge through the bound "
        f"{binding['workflow']} adapter, run or resume that workflow once, then "
        "consume the same event. Conversation, status, or control turns must be "
        "accepted and consumed without prompt mutation or execution. Never expose "
        "the private event path, token, or raw journal in the final response."
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
        and entry.get("project_root") == binding.get("project_root")
    ]
    if len(active) > 1:
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
    writer = entry.get("writer_session_sha256")
    if writer not in {None, session_digest}:
        raise PromptSessionError(
            "WRITER_CONFLICT",
            "active objective currently has a different writer session",
        )
    entry["writer_session_sha256"] = session_digest
    entry["updated_at"] = utc_now()
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
        return {
            "continue": False,
            "stopReason": "Prompt-session intake rejected recognized secret material before persistence.",
        }
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
        if binding.get("project_root") != str(project):
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
        resolved.relative_to(expected_root / "sessions")
    except (OSError, ValueError) as error:
        raise PromptSessionError(
            "EVENT_PATH_INVALID", "event path is outside prompt-session state"
        ) from error
    if resolved.name != "event.json":
        raise PromptSessionError(
            "EVENT_PATH_INVALID", "event path must name event.json"
        )
    return resolved


def _check_token(event: dict[str, Any], token: str) -> None:
    expected = str(event.get("accept_token") or "")
    if not expected or not secrets.compare_digest(expected, token):
        raise PromptSessionError(
            "EVENT_TOKEN_INVALID", "event acceptance token is invalid"
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


def accept_event(
    home: Path,
    event_path: Path,
    token: str,
    classification: str,
    *,
    refined_file: Path | None = None,
    prompt_path: Path | None = None,
    base_sha256: str | None = None,
    new_objective: bool = False,
) -> dict[str, Any]:
    if classification not in CLASSIFICATIONS:
        raise PromptSessionError(
            "CLASSIFICATION_INVALID", "turn classification is unsupported"
        )
    root = _ensure_root(home.resolve())
    with state_lock(root):
        path = _resolve_event(root, event_path)
        event = _load_event(path)
        if event.get("schema") != EVENT_SCHEMA:
            raise PromptSessionError("EVENT_INVALID", "event schema is invalid")
        _check_token(event, token)
        if event.get("phase") == "consumed":
            raise PromptSessionError("EVENT_CONSUMED", "event was already consumed")
        if event.get("phase") == "accepted":
            if event.get("classification") != classification:
                raise PromptSessionError(
                    "EVENT_CONFLICT", "accepted event classification differs"
                )
            material = classification in MATERIAL_CLASSIFICATIONS
            if material:
                if refined_file is None:
                    raise PromptSessionError(
                        "EVENT_CONFLICT", "accepted event retry omits its refinement"
                    )
                _, refined = _safe_external_file(refined_file, "refined prompt input")
                requested_prompt = (
                    str(prompt_path.expanduser().resolve())
                    if prompt_path is not None
                    else None
                )
                if (
                    sha256_bytes(refined) != event.get("refined_sha256")
                    or requested_prompt != event.get("base_prompt_path")
                    or base_sha256 != event.get("base_prompt_sha256")
                    or new_objective is not event.get("new_objective")
                ):
                    raise PromptSessionError(
                        "EVENT_CONFLICT", "accepted event retry differs"
                    )
            elif any((refined_file, prompt_path, base_sha256, new_objective)):
                raise PromptSessionError(
                    "NONMATERIAL_MUTATION",
                    "nonmaterial turns cannot carry prompt mutation inputs",
                )
            return _public_event(event, path)
        if event.get("phase") != "staged":
            raise PromptSessionError("EVENT_INVALID", "event phase is invalid")
        material = classification in MATERIAL_CLASSIFICATIONS
        if material:
            if refined_file is None:
                raise PromptSessionError(
                    "REFINEMENT_REQUIRED", "material intent requires a refined file"
                )
            refined_path, refined = _safe_external_file(
                refined_file, "refined prompt input"
            )
            try:
                refined_text = refined.decode("utf-8")
            except UnicodeDecodeError as error:
                raise PromptSessionError(
                    "INPUT_INVALID", "refined prompt input is not UTF-8"
                ) from error
            if not refined_text.strip():
                raise PromptSessionError(
                    "INPUT_INVALID", "refined prompt input is empty"
                )
            if OPERATION_MARKER_PREFIX in refined:
                raise PromptSessionError(
                    "PROMPT_RESERVED_INPUT",
                    "refined prompt uses the reserved operation marker namespace",
                )
            if contains_secret(refined_text):
                raise PromptSessionError(
                    "PROMPT_SENSITIVE_INPUT", "refined prompt contains secret material"
                )
            if prompt_path is None:
                if not new_objective:
                    raise PromptSessionError(
                        "PROMPT_BASE_REQUIRED",
                        "material intent needs a current prompt digest or explicit new objective",
                    )
                current_prompt = None
                current_digest = None
            else:
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
            _atomic_write(path.parent / "refined.md", refined)
            event.update(
                {
                    "refined_sha256": sha256_bytes(refined),
                    "refined_source_sha256": identity_sha256(refined_path),
                    "base_prompt_path": str(current_prompt) if current_prompt else None,
                    "base_prompt_sha256": current_digest,
                    "new_objective": new_objective,
                }
            )
        else:
            if (
                refined_file is not None
                or prompt_path is not None
                or base_sha256 is not None
                or new_objective
            ):
                raise PromptSessionError(
                    "NONMATERIAL_MUTATION",
                    "nonmaterial turns cannot carry prompt mutation inputs",
                )
        event["phase"] = "accepted"
        event["classification"] = classification
        event["accepted_at"] = utc_now()
        _atomic_write(path, stable_json(event))
        return _public_event(event, path)


def _public_event(event: dict[str, Any], path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": event.get("phase"),
        "classification": event.get("classification"),
        "workflow": event.get("workflow"),
        "event": str(path),
        "raw_sha256": event.get("raw_sha256"),
        "operation_id": event.get("operation_id"),
    }
    if event.get("refined_sha256"):
        result["refined_sha256"] = event["refined_sha256"]
        result["refined_file"] = str(path.parent / "refined.md")
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
    project_entries = [
        entry for entry in entries if entry.get("project_root") == project_root
    ]
    active_entries = [entry for entry in project_entries if entry.get("active") is True]
    if len(active_entries) > 1:
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
        writer = active.get("writer_session_sha256")
        if writer not in {None, session_digest}:
            raise PromptSessionError(
                "WRITER_CONFLICT",
                "active objective currently has a different writer session",
            )
    entries[:] = [
        entry for entry in entries if entry.get("project_root") != project_root
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
        if binding.get("workflow") != workflow or binding.get("project_root") != str(
            canonical_project
        ):
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
    workflow: str,
    prompt_id: str | None = None,
    prompt_ref: str | None = None,
    prompt_path: Path | None = None,
    prompt_sha256: str | None = None,
    run_id: str | None = None,
    objective_terminal: bool = False,
) -> dict[str, Any]:
    root = _ensure_root(home.resolve())
    with state_lock(root):
        path = _resolve_event(root, event_path)
        event = _load_event(path)
        _check_token(event, token)
        if event.get("workflow") != workflow or workflow not in WORKFLOWS:
            raise PromptSessionError(
                "WORKFLOW_CONFLICT", "consume workflow differs from binding"
            )
        if event.get("phase") == "consumed":
            material = event.get("classification") in MATERIAL_CLASSIFICATIONS
            if material:
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
                )
            ):
                raise PromptSessionError(
                    "NONMATERIAL_MUTATION",
                    "nonmaterial consume cannot carry prompt or run identity",
                )
            return _public_event(event, path)
        if event.get("phase") != "accepted":
            raise PromptSessionError(
                "EVENT_NOT_ACCEPTED", "event must be accepted before consume"
            )
        material = event.get("classification") in MATERIAL_CLASSIFICATIONS
        if material:
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
            _validate_prompt_result(
                raw,
                workflow=workflow,
                prompt_id=str(prompt_id),
                prompt_ref=str(prompt_ref),
                operation_id=operation_id,
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
            )
        ):
            raise PromptSessionError(
                "NONMATERIAL_MUTATION",
                "nonmaterial consume cannot carry prompt or run identity",
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
    turn_id = payload.get("turn_id")
    if session_id in {None, ""}:
        return {"continue": True}
    selected_home = (home or codex_home(payload)).resolve()
    root = state_root(selected_home)
    if not root.exists():
        return {"continue": True}
    _require_private_directory(root)
    with state_lock(root):
        if turn_id not in {None, ""}:
            path = _event_path(root, session_id, turn_id)
            if path.exists():
                event = _load_event(path)
                phase = event.get("phase")
                if phase in {"staged", "accepted"}:
                    return {
                        "decision": "block",
                        "reason": "Complete the current prompt-session staged/accepted transition before stopping; do not replay any older turn.",
                    }
                if phase != "consumed":
                    return {
                        "continue": False,
                        "stopReason": "Prompt-session event state is invalid.",
                    }
        release_writer(root, session_id)
        return {"continue": True}
