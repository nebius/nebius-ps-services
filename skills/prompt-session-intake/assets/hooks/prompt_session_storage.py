#!/usr/bin/env python3
"""Secure filesystem primitives and registry storage for prompt-session intake."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Iterator


ROOT_SCHEMA = "prompt-session-intake/root-v1"
BINDING_SCHEMA = "prompt-session-intake/binding-v1"
EVENT_SCHEMA = "prompt-session-intake/event-v1"
REGISTRY_SCHEMA = "prompt-session-intake/registry-v1"
CONTINUATION_SCHEMA = "prompt-session-intake/stop-continuation-v1"
MATERIAL_CLASSIFICATIONS = {
    "intent",
    "steering",
    "constraint",
    "clarification-answer",
    "acceptance-change",
}
NONMATERIAL_CLASSIFICATIONS = {"conversation", "status", "control"}
CLASSIFICATIONS = MATERIAL_CLASSIFICATIONS | NONMATERIAL_CLASSIFICATIONS
WORKFLOWS = {"task-implementer", "agentic-sdlc"}
PROMPT_ID_RE = re.compile(r"prompt-[0-9a-f]{32}\Z")
PROMPT_REF_RE = re.compile(r"[0-9a-f]{5,32}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PROMPT_SCHEMAS = {
    "task-implementer": "task-implementer/prompt-v3",
    "agentic-sdlc": "agentic-sdlc/prompt-v3",
}
MAX_PROMPT_BYTES = 256 * 1024
LOCK_TIMEOUT_SECONDS = 2.0
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAWS_ACCESS_KEY_ID\b\s*[:=]\s*[A-Z0-9]{16,}"),
    re.compile(r"\bAWS_SECRET_ACCESS_KEY\b\s*[:=]\s*[A-Za-z0-9/+=]{30,}"),
    re.compile(r"\bGITHUB_TOKEN\b\s*[:=]\s*[A-Za-z0-9_ghopsu-]{20,}"),
    re.compile(r"\bOPENAI_API_KEY\b\s*[:=]\s*sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bYC_TOKEN\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}"),
    re.compile(
        r"(?i)\b(password|secret|token)\b\s*[:=]\s*[\"']?"
        r"[A-Za-z0-9_./+=:-]{12,}"
    ),
)


class PromptSessionError(Exception):
    """Expected fail-closed prompt-session state error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def stable_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity_sha256(value: object) -> str:
    return sha256_bytes(str(value).encode("utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def codex_home(payload: dict[str, Any] | None = None) -> Path:
    if payload is not None:
        declared = payload.get("codex_home")
        if isinstance(declared, str) and declared.strip():
            return Path(declared).expanduser().resolve()
    return (
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        .expanduser()
        .resolve()
    )


def state_root(home: Path) -> Path:
    return home / "prompt-session-intake"


def session_key(session_id: object) -> str:
    return identity_sha256(session_id)[:24]


def turn_key(turn_id: object) -> str:
    return identity_sha256(turn_id)[:24]


def require_private_directory(path: Path, *, create: bool = False) -> None:
    if create and not path.exists():
        missing: list[Path] = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            if cursor.parent == cursor:
                break
            cursor = cursor.parent
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise PromptSessionError(
            "STATE_UNAVAILABLE", "private state directory is unavailable"
        ) from error
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise PromptSessionError("STATE_UNSAFE", "private state directory is unsafe")
    if os.name == "posix":
        current = stat.S_IMODE(mode)
        if current != 0o700:
            path.chmod(0o700)
            if stat.S_IMODE(path.lstat().st_mode) != 0o700:
                raise PromptSessionError(
                    "STATE_UNSAFE", "private state directory mode is unsafe"
                )


def require_private_file(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise PromptSessionError(
            "STATE_UNAVAILABLE", "private state file is unavailable"
        ) from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
        raise PromptSessionError("STATE_UNSAFE", "private state file is unsafe")
    if os.name == "posix" and stat.S_IMODE(mode) != 0o600:
        raise PromptSessionError("STATE_UNSAFE", "private state file mode is unsafe")


def ensure_root(home: Path) -> Path:
    root = state_root(home)
    require_private_directory(home)
    require_private_directory(root, create=True)
    marker = root / ".root.json"
    if not marker.exists():
        try:
            atomic_write(marker, stable_json({"schema": ROOT_SCHEMA}), exclusive=True)
        except FileExistsError:
            pass
    value = load_json(marker)
    if value != {"schema": ROOT_SCHEMA}:
        raise PromptSessionError("STATE_UNSAFE", "private state root marker is invalid")
    return root


def atomic_write(path: Path, payload: bytes, *, exclusive: bool = False) -> None:
    require_private_directory(path.parent, create=True)
    if path.is_symlink():
        raise PromptSessionError("STATE_UNSAFE", "refusing to replace a symlink")
    if exclusive and (path.exists() or path.is_symlink()):
        raise FileExistsError(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and (path.exists() or path.is_symlink()):
            raise FileExistsError(path)
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def load_json(path: Path) -> dict[str, Any]:
    require_private_file(path)
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptSessionError(
            "STATE_INVALID", "private state JSON is invalid"
        ) from error
    if not isinstance(value, dict):
        raise PromptSessionError(
            "STATE_INVALID", "private state JSON must be an object"
        )
    return value


@contextmanager
def state_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".state.lock"
    if lock_path.is_symlink():
        raise PromptSessionError("STATE_UNSAFE", "private state lock is unsafe")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise PromptSessionError("STATE_UNSAFE", "private state lock is unsafe")
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PromptSessionError(
                        "STATE_BUSY", "prompt-session state is busy"
                    )
                time.sleep(0.025)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def binding_path(root: Path, session_id: object) -> Path:
    return root / "sessions" / session_key(session_id) / "binding.json"


def event_path(root: Path, session_id: object, turn_id: object) -> Path:
    return (
        root
        / "sessions"
        / session_key(session_id)
        / "events"
        / turn_key(turn_id)
        / "event.json"
    )


def continuation_path(root: Path, session_id: object) -> Path:
    return root / "sessions" / session_key(session_id) / "stop-continuation.json"


def load_registry(root: Path) -> dict[str, Any]:
    path = root / "registry.json"
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "entries": []}
    value = load_json(path)
    if (
        value.get("schema") != REGISTRY_SCHEMA
        or not isinstance(value.get("entries"), list)
        or set(value) != {"schema", "entries"}
    ):
        raise PromptSessionError(
            "REGISTRY_INVALID", "prompt-session registry is invalid"
        )
    required = {
        "project_root",
        "project_sha256",
        "workflow",
        "prompt_id",
        "prompt_ref",
        "prompt_path",
        "prompt_sha256",
        "writer_session_sha256",
        "last_turn_sha256",
        "active",
        "updated_at",
    }
    project_roots: set[str] = set()
    for entry in value["entries"]:
        if not isinstance(entry, dict) or set(entry) != required:
            raise PromptSessionError(
                "REGISTRY_INVALID", "prompt-session registry entry is invalid"
            )
        project_root = entry.get("project_root")
        prompt_id = entry.get("prompt_id")
        prompt_ref = entry.get("prompt_ref")
        writer = entry.get("writer_session_sha256")
        last_turn = entry.get("last_turn_sha256")
        if (
            not isinstance(project_root, str)
            or not Path(project_root).is_absolute()
            or entry.get("project_sha256") != identity_sha256(project_root)
            or project_root in project_roots
            or entry.get("workflow") not in WORKFLOWS
            or not isinstance(prompt_id, str)
            or not PROMPT_ID_RE.fullmatch(prompt_id)
            or not isinstance(prompt_ref, str)
            or not PROMPT_REF_RE.fullmatch(prompt_ref)
            or not prompt_id.removeprefix("prompt-").startswith(prompt_ref)
            or not isinstance(entry.get("prompt_path"), str)
            or not Path(str(entry["prompt_path"])).is_absolute()
            or not isinstance(entry.get("prompt_sha256"), str)
            or not SHA256_RE.fullmatch(str(entry["prompt_sha256"]))
            or (
                writer is not None
                and (not isinstance(writer, str) or not SHA256_RE.fullmatch(writer))
            )
            or (
                last_turn is not None
                and (
                    not isinstance(last_turn, str) or not SHA256_RE.fullmatch(last_turn)
                )
            )
            or not isinstance(entry.get("active"), bool)
            or not isinstance(entry.get("updated_at"), str)
        ):
            raise PromptSessionError(
                "REGISTRY_INVALID", "prompt-session registry entry is invalid"
            )
        if entry["active"] is False and writer is not None:
            raise PromptSessionError(
                "REGISTRY_INVALID", "terminal registry entry has a writer"
            )
        project_roots.add(project_root)
    return value


def write_registry(root: Path, value: dict[str, Any]) -> None:
    atomic_write(root / "registry.json", stable_json(value))
