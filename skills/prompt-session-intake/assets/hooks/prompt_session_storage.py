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
_load_skill_support('runtime', __file__, 'prompt-session-intake/assets/hooks/prompt_session_storage.py')

from agent_runtime import agent_home  # noqa: E402


ROOT_SCHEMA = "prompt-session-intake/root-v1"
BINDING_SCHEMA = "prompt-session-intake/binding-v1"
EVENT_SCHEMA = "prompt-session-intake/event-v2"
CURRENT_EVENT_SCHEMA = "prompt-session-intake/current-event-v2"
REGISTRY_SCHEMA = "prompt-session-intake/registry-v1"
CONTINUATION_SCHEMA = "prompt-session-intake/stop-continuation-v1"
MATERIAL_CLASSIFICATIONS = {
    "intent",
    "steering",
    "constraint",
    "clarification-answer",
    "acceptance-change",
}
DISPOSITIONS = {"merge", "noop", "sensitive"}
NOOP_REASONS = {
    "workflow-control",
    "tool-control",
    "delivery-control",
    "agent-control",
    "status",
    "conversation",
    "unrelated",
    "duplicate",
}
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
    re.compile(
        r"\bNEBIUS_(?!(?:PROFILE|PROJECT_ID|AUTH_CREDENTIALS_FILE)\b)"
        r"[A-Z0-9_]*\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}"
    ),
    re.compile(r"\bYC_TOKEN\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}"),
    re.compile(r"\bKUBECONFIG\b.*(certificate-authority-data|client-key-data|token:)"),
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


def event_operation_id(
    session_sha256: str, turn_sha256: str, submitted_sha256: str
) -> str:
    material = (
        f"prompt-session-intake/event-v2:{session_sha256}:"
        f"{turn_sha256}:{submitted_sha256}"
    )
    return sha256_bytes(material.encode("utf-8"))


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
        agent_home()
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
        / "events-v2"
        / turn_key(turn_id)
        / "event.json"
    )


def current_event_path(root: Path, session_id: object) -> Path:
    return root / "sessions" / session_key(session_id) / "current-event-v2.json"


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
