#!/usr/bin/env python3
"""Non-blocking prompt intake and project-contract observation for Codex."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any


INTAKE_SCHEMA = "maintain-project-specs.prompt-intake.v2"
CONFIG_SCHEMA = "maintain-project-specs.project.v1"
REQUIREMENTS_MARKER = (
    "<!-- maintain-project-specs:requirements:start "
    "schema=maintain-project-specs/requirements-v2 -->"
)
DESIGN_MARKER = (
    "<!-- maintain-project-specs:design:start "
    "schema=maintain-project-specs/design-v2 -->"
)
LEGACY_MARKER = re.compile(
    r"<!-- maintain-project-specs:(?:requirements|design):start "
    r"schema=maintain-project-specs/(?:requirements|design)-v1 -->"
)
SECRET_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b|"
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}|"
    r"(?i:\b(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)"
    r"\s*[:=]\s*['\"]?[^\s'\"`]{8,}))"
)
MAX_PROMPT_BYTES = 1024 * 1024
MAX_SPEC_BYTES = 1024 * 1024


class HookError(RuntimeError):
    """A private observation failed; this never blocks the submitted prompt."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _segment(value: object) -> str:
    raw = str(value)
    if re.fullmatch(r"[A-Za-z0-9._-]{1,96}", raw) and raw not in {".", ".."}:
        return raw
    return _digest(raw.encode())[:24]


def _excluded(payload: dict[str, Any]) -> bool:
    if payload.get("stop_hook_active") or payload.get("is_subagent"):
        return True
    origin = str(payload.get("prompt_source") or payload.get("source") or "").lower()
    if origin in {"stop", "continuation", "compaction", "subagent", "system"}:
        return True
    agent_type = str(payload.get("agent_type") or "").lower()
    return bool(agent_type and agent_type not in {"root", "primary"})


def _project(cwd: object) -> tuple[Path, Path, str] | None:
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    path = Path(os.path.abspath(Path(cwd).expanduser()))
    if path.is_symlink() or not path.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    git_root = Path(result.stdout.strip()).resolve()
    project = path.resolve()
    try:
        relative = project.relative_to(git_root)
    except ValueError:
        return None
    return project, git_root, "." if relative == Path(".") else relative.as_posix()


def _read_owned_bytes(
    path: Path, *, max_bytes: int, expected_mode: int | None = None
) -> bytes:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > max_bytes
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        or (
            expected_mode is not None
            and stat.S_IMODE(metadata.st_mode) != expected_mode
        )
    ):
        raise HookError("private or policy file is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise HookError("private or policy file changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HookError("private or policy file is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    opened_identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_mode,
        opened.st_nlink,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
        after.st_nlink,
    )
    current_identity = (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
        current.st_mode,
        current.st_nlink,
    )
    if opened_identity != after_identity or opened_identity != current_identity:
        raise HookError("private or policy file changed while reading")
    return b"".join(chunks)


def _disabled(project: Path, git_root: Path) -> bool:
    path = project / ".codex/project-specs.json"
    try:
        raw = _read_owned_bytes(path, max_bytes=4096)
        value = json.loads(raw)
    except FileNotFoundError:
        return False
    except (HookError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    if value != {"schema": CONFIG_SCHEMA, "mode": "disabled", "scope": "."}:
        return False
    try:
        relative = path.relative_to(git_root).as_posix()
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "ls-files",
                "--error-unmatch",
                "--",
                relative,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        committed = subprocess.run(
            ["git", "-C", str(git_root), "show", f"HEAD:{relative}"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return (
        tracked.returncode == 0
        and committed.returncode == 0
        and committed.stdout == raw
    )


def _context(event: str, message: str) -> dict[str, Any]:
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message,
        },
    }


def _ensure_private(path: Path) -> None:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    if home.resolve(strict=False) != home:
        raise HookError("CODEX_HOME is unsafe")
    root = home / "project-specs" / "prompt-intake"
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise HookError("prompt intake escaped its private root") from error
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home_metadata = home.lstat()
    if (
        stat.S_ISLNK(home_metadata.st_mode)
        or not stat.S_ISDIR(home_metadata.st_mode)
        or (hasattr(os, "getuid") and home_metadata.st_uid != os.getuid())
    ):
        raise HookError("CODEX_HOME is unsafe")
    current = home
    for part in ("project-specs", "prompt-intake", *relative.parts):
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        metadata = current.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise HookError("prompt intake directory is unsafe")
        os.chmod(current, 0o700)


def _write_exclusive(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_intake(path: Path) -> bytes:
    return _read_owned_bytes(path, max_bytes=16 * 1024, expected_mode=0o600)


def _acquire_turn_lock(path: Path) -> int:
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise HookError("prompt intake lock is unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _stage_prompt(
    payload: dict[str, Any], git_root: Path, scope: str, prompt: str
) -> str:
    session = payload.get("session_id")
    turn = payload.get("turn_id")
    if session in {None, ""} or turn in {None, ""}:
        raise HookError("prompt identity is unavailable")
    prompt_raw = prompt.encode("utf-8")
    if len(prompt_raw) > MAX_PROMPT_BYTES:
        raise HookError("prompt is too large")
    intent_id = _digest(
        str(session).encode() + b"\0" + str(turn).encode() + b"\0" + prompt_raw
    )
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    parent = (
        home
        / "project-specs/prompt-intake"
        / f"{_segment(git_root.name)}-{_digest(str(git_root).encode())[:12]}"
        / _segment(session)
    )
    _ensure_private(parent)
    path = parent / f"{_segment(turn)}.json"
    record = {
        "schema": INTAKE_SCHEMA,
        "intent_id": intent_id,
        "project_scope": scope,
        "prompt_sha256": _digest(prompt_raw),
        "session_sha256": _digest(str(session).encode()),
        "turn_sha256": _digest(str(turn).encode()),
        "classification": "unclassified",
    }
    raw = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    lock = _acquire_turn_lock(parent / f".{_segment(turn)}.lock")
    try:
        try:
            existing = _read_intake(path)
        except FileNotFoundError:
            try:
                _write_exclusive(path, raw)
            except FileExistsError:
                existing = _read_intake(path)
                if existing != raw:
                    raise HookError("turn identity was reused for different prompt bytes")
        else:
            if existing != raw:
                raise HookError("turn identity was reused for different prompt bytes")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    return intent_id


def _observe(project: Path) -> tuple[str, str]:
    requirements = project / "docs/requirements.md"
    design = project / "docs/design.md"
    try:
        requirements_text = _read_owned_bytes(
            requirements, max_bytes=MAX_SPEC_BYTES
        ).decode("utf-8")
        design_text = _read_owned_bytes(design, max_bytes=MAX_SPEC_BYTES).decode(
            "utf-8"
        )
    except FileNotFoundError:
        return "CONTRACT_PENDING", "Canonical requirements or design is missing."
    except (HookError, OSError, UnicodeError):
        return "CONTRACT_INVALID", "Canonical requirements or design is unreadable."
    combined = requirements_text + "\n" + design_text
    if LEGACY_MARKER.search(combined):
        return "CONTRACT_MIGRATION_REQUIRED", "Canonical documents still use schema v1."
    if REQUIREMENTS_MARKER not in requirements_text or DESIGN_MARKER not in design_text:
        return "CONTRACT_INVALID", "Canonical v2 ownership markers are missing."
    return "CONTRACT_CURRENT", "Canonical v2 ownership markers are present."


def _prompt_context(scope: str, intent_id: str | None, *, sensitive: bool) -> str:
    receipt = f" Root intent: {intent_id}." if intent_id else ""
    capture = (
        "Metadata capture was skipped because the prompt may contain sensitive data."
        if sensitive
        else "Metadata-only prompt identity was staged; the prompt body was not persisted."
    )
    return (
        f"Project-contract intake applies to direct root-user prompt scope {scope}.{receipt} "
        f"{capture} Classify the delivered prompt statement-by-statement before acting. "
        "A durable product behavior, constraint, acceptance condition, design change, or "
        "implementation request is project lifecycle intent: use maintain-project-specs "
        "to reconcile the selected project's docs/requirements.md and docs/design.md as "
        "one canonical pair before implementation, then record implementation and "
        "independent verification evidence after the work. Questions, status requests, "
        "conversation, and unrelated operations are not lifecycle intent. The startup cwd "
        "is the default project; an explicit project path wins, and ambiguous scope requires "
        "clarification. Root agents own classification and publication. Workers inherit the "
        "root intent and may return typed spec_gap proposals, but must not independently "
        "reclassify or write project specs. Hook failure never blocks tools or completion."
    )


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("hook_event_name")
    if event not in {"SessionStart", "UserPromptSubmit"}:
        return {}
    if event == "UserPromptSubmit" and _excluded(payload):
        return {}
    resolved = _project(payload.get("cwd"))
    if resolved is None:
        return {}
    project, git_root, scope = resolved
    if _disabled(project, git_root):
        return {}
    if event == "SessionStart":
        status, detail = _observe(project)
        if status == "CONTRACT_CURRENT":
            return {}
        return _context(
            "SessionStart",
            f"Project contract observation for scope {scope}: {status}. {detail} "
            "Use maintain-project-specs when the current task changes durable project intent. "
            "This observation never gates tools, workflows, Stop, or completion.",
        )
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return _context(
            "UserPromptSubmit", _prompt_context(scope, None, sensitive=False)
        )
    sensitive = SECRET_RE.search(prompt) is not None
    intent_id = None if sensitive else _stage_prompt(payload, git_root, scope, prompt)
    return _context(
        "UserPromptSubmit", _prompt_context(scope, intent_id, sensitive=sensitive)
    )


def main() -> int:
    try:
        payload: Any = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise HookError("hook payload must be an object")
        output = evaluate(payload)
    except Exception:
        event = (
            str(payload.get("hook_event_name"))
            if "payload" in locals() and isinstance(payload, dict)
            else "UserPromptSubmit"
        )
        output = (
            _context(
                event,
                "Project-contract observation or metadata intake was unavailable. "
                "Handle the delivered request normally and classify durable project intent "
                "with maintain-project-specs. Tools and completion remain available.",
            )
            if event in {"SessionStart", "UserPromptSubmit"}
            else {}
        )
    if output:
        print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
