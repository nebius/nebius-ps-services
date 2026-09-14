#!/usr/bin/env python3
"""PreToolUse safety hook for Agentic SDLC runs."""

from __future__ import annotations

import json
import sys
from typing import Any



# BEGIN shared runtime bootstrap
def _load_skill_support(group, anchor_file, declared_path, *, source_only=False):
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path
    import stat as _stat
    import sys as _sys
    from types import ModuleType as _ModuleType

    def read_source(path):
        path = _Path(_os.path.abspath(path))
        for part in (*reversed(path.parents), path):
            metadata = part.lstat()
            if _stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or part == path:
                    raise ImportError("shared runtime path contains an unsafe symlink")
                metadata = part.stat()
            if part != path:
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if (not _stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, _os.getuid()}
                        or metadata.st_mode & 0o022 and not sticky):
                    raise ImportError("unsafe shared runtime ancestry")
        path = path.resolve(strict=True)
        descriptor = _os.open(path.anchor, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = _os.open(part, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=descriptor)
                _os.close(descriptor)
                descriptor = child
                metadata = _os.fstat(descriptor)
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if metadata.st_uid not in {0, _os.getuid()} or metadata.st_mode & 0o022 and not sticky:
                    raise ImportError("unsafe shared runtime ancestry")
            child = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = _os.fstat(child)
                if (not _stat.S_ISREG(before.st_mode) or before.st_uid != _os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 1048576):
                    raise ImportError("unsafe shared runtime source")
                data = bytearray()
                while chunk := _os.read(child, min(65536, 1048577 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ImportError("shared runtime source exceeds size limit")
                after = _os.fstat(child)
                bound = _os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                def identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(bound):
                    raise ImportError("shared runtime source changed while reading")
                return bytes(data), identity(after)
            finally:
                _os.close(child)
        finally:
            _os.close(descriptor)

    anchor = _Path(_os.path.abspath(anchor_file))
    declared_paths = (declared_path,) if isinstance(declared_path, str) else declared_path
    candidates = []
    for declared in declared_paths:
        relative = _Path(declared)
        if tuple(anchor.parts[-len(relative.parts):]) == relative.parts:
            catalog = anchor.parents[len(relative.parts) - 1]
            candidates.append(("catalog", catalog, catalog / "global-context-management/scripts"))
    if not source_only:
        flat = anchor.parent.parent if anchor.parent.name == "lib" else anchor.parent
        candidates.append(("flat", flat, flat))
        agent = "codex" if _os.environ.get("CODEX_THREAD_ID") else _os.environ.get("SKILLS_AGENT", "codex")
        if agent not in {"codex", "claude"}:
            raise ImportError("SKILLS_AGENT must be codex or claude")
        key, default = ("CODEX_HOME", ".codex") if agent == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
        home = _Path(_os.environ.get(key, str(_Path.home() / default))).expanduser()
        if not home.is_absolute():
            raise ImportError("shared runtime home must be absolute")
        candidates.append(("flat", home / "hooks", home / "hooks"))
    for kind, root, support in candidates:
        loader_path = support / "trusted_runtime.py"
        if not loader_path.exists() and not loader_path.is_symlink():
            if ((kind == "catalog" and (support.exists() or support.is_symlink()))
                    or any((support / name).exists() or (support / name).is_symlink()
                           for name in ("agent_runtime.py", "hook_runtime.py", "task_state_permissions.py"))):
                raise ImportError("incomplete shared runtime bundle; reinstall current support")
            continue
        data, identity = read_source(loader_path)
        digest = _hashlib.sha256(data).hexdigest()
        cache_name = "_skills_trusted_runtime"
        loader = _sys.modules.get(cache_name)
        provenance = (str(loader_path), identity, digest)
        if cache_name in _sys.modules:
            if (type(loader) is not _ModuleType or getattr(loader, "_bootstrap_provenance", None) != provenance):
                raise ImportError("conflicting shared runtime loader")
        else:
            loader = _ModuleType(cache_name)
            loader.__file__ = str(loader_path)
            exec(compile(data, str(loader_path), "exec"), loader.__dict__)
            loader._bootstrap_provenance = provenance
            loader._read_source = read_source
            _sys.modules[cache_name] = loader
        return loader.load_support(group, anchor=(kind, root), source_only=source_only)
    raise ImportError("Shared skill runtime unavailable; install the complete current skill support")
# END shared runtime bootstrap
_load_skill_support('sdlc-hook-state', __file__, 'sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py')

from lib.sdlc_policy import (  # noqa: E402 — verified bootstrap precedes runtime imports
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
from lib.sdlc_state import (  # noqa: E402 — verified bootstrap precedes runtime imports
    append_jsonl,
    load_active_state,
    now_iso,
    resolve_path,
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


def _log_event(
    payload: dict[str, Any], decision: dict[str, Any], reason: str | None
) -> None:
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


def _allow(
    payload: dict[str, Any], decision: dict[str, Any] | None = None
) -> dict[str, Any]:
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
        return _deny(
            payload,
            "Blocked: active SDLC state is corrupt and must be repaired before mutating actions.",
        )

    if tool_name == "Skill":
        selected = str(tool_input.get("skill", "")).split(":")[-1]
        internal = selected.startswith("sdlc-") and selected not in {"sdlc-start", "sdlc-workflow-test"}
        if internal and (active is None or payload.get("is_subagent")
                         or active.execution_role not in {"project", "coordinator"}
                         or not active.execution_identity_valid):
            return _deny(payload, "Internal SDLC skill requires verified workflow context and coordinator ownership.")
        return allow()

    if active is None:
        return allow()

    project_root = active.project_root

    if tool_name == "Bash":
        danger = dangerous_shell_reason(command)
        if danger:
            return _deny(payload, danger)
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

    if tool_name in {"apply_patch", "Edit", "Write"}:
        if tool_name == "apply_patch":
            targets = extract_apply_patch_targets(command, cwd)
        else:
            path = tool_input.get("file_path")
            if not isinstance(path, str) or not path:
                return _deny(payload, "Write tool requires one explicit file path.")
            targets = [resolve_path(path, cwd)]
            field = "content" if tool_name == "Write" else "new_string"
            if not isinstance(tool_input.get(field), str):
                return _deny(payload, "Write tool requires explicit text content.")
            command = tool_input[field]
        target_reason = validate_write_targets(
            targets, project_root, active, allow_global_agents=True
        )
        if target_reason:
            return _deny(payload, target_reason)
        if contains_secret(command):
            return _deny(payload, "Blocked: patch appears to contain a secret.")
        spec_decision = spec_warning_or_denial(
            command, targets, project_root, current_state
        )
        if spec_decision:
            if (
                spec_decision.get("hookSpecificOutput", {}).get("permissionDecision")
                == "deny"
            ):
                return _deny(
                    payload,
                    spec_decision["hookSpecificOutput"]["permissionDecisionReason"],
                )
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
