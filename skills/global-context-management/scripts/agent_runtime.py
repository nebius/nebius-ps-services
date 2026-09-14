#!/usr/bin/env python3
"""Shared host context for source, installed helpers, and native plugin hooks."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any


AGENTS = ("codex", "claude")
CLAUDE_READ_ONLY_ROLES = ("repo-mapper", "test-strategist", "risk-reviewer")


def _claude_role_text(path: Path) -> str | None:
    """Read a bounded, stable role definition without following symlinks."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        return None
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022
                    or metadata.st_size > 16_384
                    or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)):
                return None
            raw = handle.read(16_385).decode("utf-8")
            after = os.fstat(handle.fileno())
            rebound = path.lstat()
            if ((metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size)
                    != (after.st_mtime_ns, after.st_ctime_ns, after.st_size)
                    or (metadata.st_dev, metadata.st_ino) != (rebound.st_dev, rebound.st_ino)):
                return None
    except (OSError, UnicodeError):
        return None
    return raw


def _claude_role_fields(raw: str) -> dict[str, str]:
    """Recognize our restricted profile; unknown YAML fields are not safe."""
    lines = raw.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return {}
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep or key in fields or key not in {"name", "description", "tools", "model"}:
            return {}
        fields[key] = value.strip()
    if (fields.get("name") not in CLAUDE_READ_ONLY_ROLES
            or not fields.get("description") or fields.get("model") != "inherit"
            or set(fields.get("tools", "").split(", ")) != {"Read", "Grep", "Glob"}
            or not "\n".join(lines[end + 1:]).strip()):
        return {}
    return fields


def claude_role_metadata(path: Path) -> dict[str, str]:
    """Recognize the non-executing profile, never importing a role file."""
    raw = _claude_role_text(path)
    return _claude_role_fields(raw) if raw is not None else {}


def _claude_role_tree(root: Path) -> list[tuple[str, dict[str, str]]] | None:
    """Inspect recursive native definitions; ambiguity suppresses candidates."""
    if any(parent.is_symlink() for parent in (root, *root.parents)):
        return None
    pending = [root]
    result = []
    count = 0
    try:
        if not root.exists():
            return []
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > 64 or entry.is_symlink():
                        return None
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        return None
                    if Path(entry.name).suffix != ".md":
                        continue
                    raw = _claude_role_text(Path(entry.path))
                    if raw is None:
                        return None
                    lines = raw.splitlines()
                    if not lines or lines[0] != "---" or "---" not in lines[1:]:
                        return None
                    header = lines[1:lines.index("---", 1)]
                    # Names, not filenames, define precedence. Accept a small
                    # scalar subset; ambiguous YAML cannot prove no override.
                    names = [line[5:].strip() for line in header if line.startswith("name:")]
                    if len(names) != 1 or any(
                        line.startswith(("'name'", '"name"', "<<:", "&", "*", "{"))
                        for line in header
                    ):
                        return None
                    name = names[0]
                    if len(name) > 1 and name[0] == name[-1] and name[0] in "\"'":
                        name = name[1:-1]
                    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
                        return None
                    result.append((name, _claude_role_fields(raw)))
    except OSError:
        return None
    return result


def claude_read_only_roles(home: Path, cwd: Path | None = None) -> list[dict[str, str]]:
    """Offer validated user roles only when no discoverable project role shadows.

    CLI and managed overrides are not observable from a hook payload; callers
    must still verify effective native capabilities before delegating.
    """
    definitions = _claude_role_tree(home / "agents")
    if definitions is None:
        return []
    shadowed: set[str] = set()
    if cwd is not None:
        for directory in (cwd, *cwd.parents):
            agents = directory / ".claude/agents"
            if agents == home / "agents":
                continue
            project_roles = _claude_role_tree(agents)
            if project_roles is None:
                return []
            shadowed.update(name for name, _ in project_roles)
    result = []
    for name in CLAUDE_READ_ONLY_ROLES:
        matches = [fields for identity, fields in definitions if identity == name]
        if len(matches) != 1:
            continue
        fields = matches[0]
        if fields.get("name") == name and name not in shadowed:
            result.append({"name": name, "description": fields["description"]})
    return result


def agent_name(value: str | None = None) -> str:
    selected = value or ("codex" if os.environ.get("CODEX_THREAD_ID")
                         else os.environ.get("SKILLS_AGENT", "codex"))
    if selected not in AGENTS:
        raise ValueError("SKILLS_AGENT must be codex or claude")
    return selected


def agent_home(value: str | None = None) -> Path:
    selected = agent_name(value)
    key, directory = (("CODEX_HOME", ".codex") if selected == "codex"
                      else ("CLAUDE_CONFIG_DIR", ".claude"))
    path = Path(os.environ.get(key, str(Path.home() / directory))).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{key} must be absolute")
    return path


def native_session_id() -> str | None:
    key = "CODEX_THREAD_ID" if agent_name() == "codex" else "SKILLS_SESSION_ID"
    return os.environ.get(key) or None


def installed_skills_dir(value: str | None = None) -> Path:
    selected = agent_name(value)
    return Path.home() / ".agents/skills" if selected == "codex" else agent_home(selected) / "skills"


def runtime_environment(value: str | None = None, *, home: Path | None = None,
                        fresh_session: bool = False) -> dict[str, str]:
    """Select a native home without leaking parent identity into a fresh worker."""
    selected = agent_name(value)
    env = dict(os.environ)
    env["SKILLS_AGENT"] = selected
    key = "CODEX_HOME" if selected == "codex" else "CLAUDE_CONFIG_DIR"
    env[key] = str(home if home is not None else agent_home(selected))
    if selected == "claude" or fresh_session:
        env.pop("CODEX_THREAD_ID", None)
    if selected == "codex" or fresh_session:
        env.pop("SKILLS_SESSION_ID", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def session_identity_source() -> str:
    return "CODEX_THREAD_ID" if agent_name() == "codex" else "SKILLS_SESSION_ID"


def claude_session_identity(payload: dict[str, Any]) -> str:
    session = payload.get("session_id")
    worker = payload.get("agent_id")
    if not isinstance(session, str) or not session or (worker is not None and not isinstance(worker, str)):
        raise ValueError("native Claude session identity is unavailable")
    # Length framing makes the compound native identity unambiguous.
    return f"{len(session)}:{session}:{worker}" if worker else session


def normalize_payload(payload: Any, agent: str) -> dict[str, Any]:
    """Translate only trusted host events, never invent a prompt identity."""
    selected = agent_name(agent)
    if not isinstance(payload, dict):
        raise ValueError("hook input must be an object")
    result = dict(payload)
    # A caller cannot supply internal adapter fields through a native event.
    for key in list(result):
        if key.startswith("_skills_"):
            result.pop(key)
    result["_skills_host"] = selected
    if selected == "codex":
        return result
    result.pop("turn_id", None)
    result.pop("codex_home", None)
    prompt_id = result.get("prompt_id")
    if isinstance(prompt_id, str) and re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", prompt_id
    ):
        result["turn_id"] = prompt_id
    # Native subagent provenance excludes authorization and prompt capture.
    if result.get("agent_id"):
        result["is_subagent"] = True
        result["role"] = "subagent"
    prompt = result.get("prompt")
    if isinstance(prompt, str):
        # Match the leading command only; quoted/casual mentions stay untouched.
        result["prompt"] = re.sub(
            r"^(\s*(?:(?:please\s+)?(?:run|apply|execute|invoke|use)\s+|please\s+)?)"
            r"/(?:skills:)?([a-z][a-z0-9-]*)(?=\s|$)",
            r"\1$\2", prompt, count=1, flags=re.IGNORECASE,
        )
    return result


def state_edit_only(payload: dict[str, Any], state_file: Path) -> bool:
    """Claude equivalent of an exact update/add to the advertised state file."""
    if payload.get("_skills_host") != "claude":
        return False
    tool = payload.get("tool_name")
    data = payload.get("tool_input")
    if tool not in {"Edit", "Write"} or not isinstance(data, dict):
        return False
    raw_path = data.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(str(payload.get("cwd") or ".")) / path
    if path.is_symlink() or path.resolve() != state_file.resolve():
        return False
    if tool == "Write":
        return not state_file.exists() and isinstance(data.get("content"), str)
    return (state_file.is_file() and isinstance(data.get("old_string"), str)
            and bool(data["old_string"]) and isinstance(data.get("new_string"), str)
            and data.get("replace_all", False) is False)
