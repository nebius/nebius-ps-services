#!/usr/bin/env python3
"""PreToolUse safety hook for Agentic SDLC runs."""

from __future__ import annotations

import json
import sys
from typing import Any

from lib.sdlc_policy import (
    allow,
    contains_secret,
    dangerous_shell_reason,
    deny,
    extract_apply_patch_targets,
    extract_obvious_command_paths,
    git_policy_reason,
    mcp_policy_reason,
    spec_warning_or_denial,
    validate_write_targets,
)
from lib.sdlc_state import (
    append_jsonl,
    load_active_state,
    now_iso,
    resolve_path,
    resolve_project_root,
)


WRITE_COMMANDS = {"rm", "mv", "cp", "rsync", "chmod", "chown", "mkdir", "touch", "tee"}


def _tool_text(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    try:
        return json.dumps(tool_input, sort_keys=True)
    except TypeError:
        return str(tool_input)


def _command_starts_with_write(command: str) -> bool:
    first = command.strip().split(maxsplit=1)[0] if command.strip() else ""
    return first in WRITE_COMMANDS or ">" in command or ">>" in command


def _log_event(payload: dict[str, Any], decision: dict[str, Any], reason: str | None) -> None:
    cwd = payload.get("cwd") or "."
    try:
        active, _, _, _ = load_active_state(cwd)
    except json.JSONDecodeError:
        active = None
    if active is None:
        return
    event = {
        "event": "PreToolUse",
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "turn_id": payload.get("turn_id"),
        "cwd": cwd,
        "decision": "deny" if reason else ("context" if decision else "allow"),
        "reason": reason,
        "project_id": active.project_id,
        "run_id": active.run_id,
        "created_at": now_iso(),
    }
    append_jsonl(active.history_dir / "hook-events.jsonl", event)


def _deny(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    decision = deny(reason)
    _log_event(payload, decision, reason)
    return decision


def _allow(payload: dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, Any]:
    result = decision if decision is not None else allow()
    _log_event(payload, result, None)
    return result


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "PreToolUse":
        return allow()

    cwd = resolve_path(payload.get("cwd") or ".")
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    command = _tool_text(tool_input)

    try:
        active, _, current_state, _ = load_active_state(cwd)
    except json.JSONDecodeError:
        return _deny(payload, "Blocked: active SDLC state is corrupt and must be repaired before mutating actions.")

    project_root = active.project_root if active else resolve_project_root(cwd)

    danger = dangerous_shell_reason(command)
    if danger:
        return _deny(payload, danger)

    if tool_name == "Bash":
        git_reason = git_policy_reason(command, project_root, active)
        if git_reason:
            return _deny(payload, git_reason)
        if _command_starts_with_write(command):
            targets = extract_obvious_command_paths(command, cwd)
            target_reason = validate_write_targets(targets, project_root, active)
            if target_reason:
                return _deny(payload, target_reason)
        if contains_secret(command):
            return _deny(payload, "Blocked: shell command appears to contain a secret.")
        return _allow(payload)

    if tool_name == "apply_patch":
        targets = extract_apply_patch_targets(command, cwd)
        target_reason = validate_write_targets(targets, project_root, active, allow_global_agents=True)
        if target_reason:
            return _deny(payload, target_reason)
        if contains_secret(command):
            return _deny(payload, "Blocked: patch appears to contain a secret.")
        spec_decision = spec_warning_or_denial(command, targets, project_root, current_state)
        if spec_decision:
            if spec_decision.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                return _deny(payload, spec_decision["hookSpecificOutput"]["permissionDecisionReason"])
            return _allow(payload, spec_decision)
        return _allow(payload)

    if tool_name.startswith("mcp__"):
        reason = mcp_policy_reason(tool_name, tool_input, cwd, project_root, active)
        if reason:
            return _deny(payload, reason)
        return _allow(payload)

    return _allow(payload)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        result = evaluate(payload)
        if result:
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # pragma: no cover - fail closed for hook runtime
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Blocked: SDLC PreToolUse hook failed closed: {exc}",
                    }
                },
                sort_keys=True,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
