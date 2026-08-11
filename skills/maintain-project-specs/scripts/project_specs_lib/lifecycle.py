"""Private lifecycle state for prompt-to-spec reconciliation and sealing."""

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

from .contracts import (
    ProjectSpecError,
    canonical_digest,
    digest,
    load_project_config,
    project_identity,
    stable_json,
    validate_project,
)


STATE_SCHEMA = "maintain-project-specs.lifecycle.v1"
PHASES = {
    "planning-required",
    "planned",
    "implementation-open",
    "reconciliation-required",
    "seal-armed",
    "sealed",
    "waived",
}
STATE_KEYS = {
    "schema",
    "project_scope",
    "git_head_at_prompt",
    "turn_sha256",
    "phase",
    "receipt_sha256",
    "requirements_sha256",
    "design_sha256",
    "rules_path",
    "rules_sha256",
    "project_instructions_state_sha256",
    "project_instructions_reload_required",
    "write_epoch",
    "planned_write_epoch",
    "waiver",
}
SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,96}")
RULES_NAME = "pending-project-rules.md"
MAX_RULES_BYTES = 8 * 1024


def _valid_state(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != STATE_KEYS:
        return False
    digest_fields = (
        "receipt_sha256",
        "requirements_sha256",
        "design_sha256",
        "rules_sha256",
        "project_instructions_state_sha256",
    )
    planned = value.get("planned_write_epoch")
    reload_required = value.get("project_instructions_reload_required")
    return (
        value.get("schema") == STATE_SCHEMA
        and value.get("phase") in PHASES
        and isinstance(value.get("project_scope"), str)
        and re.fullmatch(r"[0-9a-f]{40,64}", str(value.get("git_head_at_prompt")))
        is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("turn_sha256"))) is not None
        and all(
            item is None or re.fullmatch(r"[0-9a-f]{64}", str(item)) is not None
            for item in (value.get(field) for field in digest_fields)
        )
        and type(value.get("write_epoch")) is int
        and int(value["write_epoch"]) >= 0
        and (planned is None or (type(planned) is int and int(planned) >= 0))
        and value.get("rules_path") in {None, RULES_NAME}
        and (value.get("rules_path") is None) == (value.get("rules_sha256") is None)
        and (
            value.get("phase")
            not in {"planned", "implementation-open", "seal-armed", "sealed"}
            or value.get("rules_path") == RULES_NAME
        )
        and (reload_required is None or type(reload_required) is bool)
        and value.get("waiver")
        in {
            None,
            "documentation-only",
            "read-only",
            "non-project",
            "project-policy",
        }
        and (value.get("phase") == "waived") == (value.get("waiver") is not None)
        and (
            value.get("phase") not in {"planned", "implementation-open", "seal-armed"}
            or (
                value.get("receipt_sha256") is not None
                and planned == value.get("write_epoch")
            )
        )
        and (
            value.get("phase") != "sealed"
            or (
                value.get("receipt_sha256") is not None
                and value.get("requirements_sha256") is not None
                and value.get("design_sha256") is not None
                and value.get("project_instructions_state_sha256") is not None
                and type(reload_required) is bool
                and planned == value.get("write_epoch")
            )
        )
    )


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _safe_segment(value: object) -> str:
    raw = str(value)
    if SAFE_SEGMENT_RE.fullmatch(raw) and raw not in {".", ".."}:
        return raw
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:48] or "session"
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def lifecycle_dir(git_root: Path, session_id: object) -> Path:
    workspace = f"{_safe_segment(git_root.name)}-{hashlib.sha256(str(git_root).encode()).hexdigest()[:12]}"
    return codex_home() / "project-specs" / workspace / _safe_segment(session_id)


def lifecycle_path(git_root: Path, session_id: object) -> Path:
    return lifecycle_dir(git_root, session_id) / "lifecycle.json"


def _secure_directory(path: Path) -> None:
    home = codex_home()
    if home.resolve(strict=False) != home:
        raise ProjectSpecError("UNSAFE_STATE", "CODEX_HOME must not use symlinks")
    try:
        home.mkdir(mode=0o700)
    except FileExistsError:
        pass
    home_metadata = home.lstat()
    if stat.S_ISLNK(home_metadata.st_mode) or not stat.S_ISDIR(home_metadata.st_mode):
        raise ProjectSpecError("UNSAFE_STATE", "CODEX_HOME is unsafe")
    private_root = home / "project-specs"
    try:
        relative = path.relative_to(private_root)
    except ValueError as error:
        raise ProjectSpecError(
            "UNSAFE_STATE", "private state escaped its managed root"
        ) from error
    current = private_root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = current.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ProjectSpecError("UNSAFE_STATE", "private state directory is unsafe")
        os.chmod(current, 0o700)


def _read_private_bytes(path: Path, label: str, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ProjectSpecError("LIFECYCLE_REQUIRED", f"{label} is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > max_bytes
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ProjectSpecError("UNSAFE_STATE", f"{label} is unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", f"{label} changed while opening"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ProjectSpecError("UNSAFE_STATE", f"{label} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        ) != (opened.st_size, opened.st_mtime_ns, opened.st_nlink):
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", f"{label} changed while reading"
            )
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} disappeared while reading"
        ) from error
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} changed while reading"
        )
    return b"".join(chunks)


def _read_private(path: Path, *, required: bool = False) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        if required:
            raise ProjectSpecError(
                "LIFECYCLE_REQUIRED", "project spec lifecycle is missing"
            )
        return None
    try:
        raw = _read_private_bytes(path, "private lifecycle state", 64 * 1024)
        value: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectSpecError(
            "UNSAFE_STATE", "private lifecycle state is invalid"
        ) from error
    if not _valid_state(value):
        raise ProjectSpecError(
            "UNSAFE_STATE", "private lifecycle state has an invalid schema"
        )
    value["_loaded_sha256"] = digest(raw)
    return value


def _write_private_bytes(path: Path, data: bytes) -> None:
    _secure_directory(path.parent)
    temporary_name = f".{path.name}.{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    parent = os.open(path.parent, directory_flags)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def write_validation_receipt(
    project_root: Path,
    output: Path,
    session_id: object | None,
) -> dict[str, Any]:
    receipt = validate_project(project_root)
    if session_id is None:
        raise ProjectSpecError(
            "LIFECYCLE_REQUIRED",
            "spec receipt output requires the current lifecycle session",
        )
    target = Path(os.path.abspath(output.expanduser()))
    git_root = Path(str(receipt["git_root"]))
    expected = Path(
        os.path.abspath(lifecycle_dir(git_root, session_id) / "spec-receipt.json")
    )
    if target != expected:
        raise ProjectSpecError(
            "UNSAFE_STATE",
            "spec receipt output must be the current session spec-receipt.json",
        )
    _write_private_bytes(target, stable_json(receipt))
    return receipt


def _write_private(path: Path, value: dict[str, Any]) -> None:
    expected = value.get("_loaded_sha256")
    if expected is not None and not isinstance(expected, str):
        raise ProjectSpecError("UNSAFE_STATE", "lifecycle compare state is invalid")
    serialized = dict(value)
    serialized.pop("_loaded_sha256", None)
    if not _valid_state(serialized):
        raise ProjectSpecError(
            "UNSAFE_STATE", "refusing to write invalid lifecycle state"
        )
    _secure_directory(path.parent)
    lock_path = path.parent / ".lifecycle.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ProjectSpecError("UNSAFE_STATE", "lifecycle lock is unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if expected is not None:
            current = _read_private_bytes(path, "private lifecycle state", 64 * 1024)
            if digest(current) != expected:
                raise ProjectSpecError(
                    "CONCURRENT_MODIFICATION",
                    "project spec lifecycle changed before transition",
                )
        _write_private_bytes(path, stable_json(serialized))
        value.pop("_loaded_sha256", None)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _outside_git(path: Path, git_root: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path.expanduser())).resolve(strict=False)
    try:
        absolute.relative_to(git_root.resolve())
    except ValueError:
        return
    raise ProjectSpecError("UNSAFE_STATE", f"{label} must stay outside Git")


def _path_has_suffix(path: Path, suffix: Path) -> bool:
    return (
        len(path.parts) >= len(suffix.parts)
        and path.parts[-len(suffix.parts) :] == suffix.parts
    )


def _require_current_project_instructions_bundle(
    lifecycle_state_path: Path,
    private_root: Path,
    *,
    rules_file: Path | None = None,
    render_state_file: Path | None = None,
    final_state_file: Path | None = None,
) -> Path:
    expected_root = Path(
        os.path.abspath(lifecycle_state_path.parent / "project-instructions")
    )
    actual_root = Path(os.path.abspath(private_root.expanduser()))
    expected_paths = {
        "rendered project rules": (rules_file, expected_root / "rules.md"),
        "project-rule render state": (
            render_state_file,
            expected_root / "render-state.json",
        ),
        "project-instructions state": (final_state_file, expected_root / "state.json"),
    }
    if actual_root != expected_root or any(
        value is not None and Path(os.path.abspath(value.expanduser())) != expected
        for value, expected in expected_paths.values()
    ):
        raise ProjectSpecError(
            "UNSAFE_STATE",
            "project-instructions evidence must use the current lifecycle session bundle",
        )
    return expected_root


def _verified_rendered_rules(
    project: Path,
    git_root: Path,
    receipt: dict[str, Any],
    rules_file: Path,
    render_state_file: Path,
    project_instructions_private_root: Path,
) -> bytes:
    rules_path = Path(os.path.abspath(rules_file.expanduser()))
    state_path = Path(os.path.abspath(render_state_file.expanduser()))
    private_root = Path(os.path.abspath(project_instructions_private_root.expanduser()))
    _outside_git(rules_path, git_root, "rendered project rules")
    _outside_git(state_path, git_root, "project-rule render state")
    _outside_git(private_root, git_root, "project-instructions private root")
    raw = _read_private_bytes(rules_path, "rendered project rules", MAX_RULES_BYTES)
    state_raw = _read_private_bytes(state_path, "project-rule render state", 64 * 1024)
    try:
        state: Any = json.loads(state_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectSpecError(
            "UNSAFE_RULES", "project-rule render state is invalid"
        ) from error
    state_rules = state.get("rules_path") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("schema") != "project-agent-instructions.render-state.v1"
        or state.get("repository_mutated") is not False
        or state.get("project_root") != str(project)
        or state.get("git_root") != str(git_root)
        or state.get("project_scope") != receipt["project_scope"]
        or state.get("spec_owner") != "maintain-project-specs"
        or dict(state.get("requirements") or {}).get("sha256")
        != receipt["requirements"]["sha256"]
        or dict(state.get("design") or {}).get("sha256") != receipt["design"]["sha256"]
        or not isinstance(state_rules, str)
        or Path(state_rules).is_absolute()
        or ".." in Path(state_rules).parts
        or not _path_has_suffix(rules_path, Path(state_rules))
        or state.get("rules_sha256") != digest(raw)
    ):
        raise ProjectSpecError(
            "UNSAFE_RULES", "rendered rules are not bound to the current project specs"
        )
    helper = (
        Path(__file__).resolve().parents[3]
        / "project-agent-instructions"
        / "scripts"
        / "project_agent_instructions.py"
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "render",
                "--private-root",
                str(private_root),
                "--manifest",
                str(state.get("manifest_path", "")),
                "--decision",
                str(state.get("decision_path", "")),
                "--output",
                str(state_rules),
                "--state",
                str(state_path),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        verified: Any = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ProjectSpecError(
            "UNSAFE_RULES", "project-rule render verification could not run"
        ) from error
    if (
        completed.returncode != 0
        or not isinstance(verified, dict)
        or verified.get("status") != "ok"
        or verified.get("repository_mutated") is not False
        or verified.get("rules_sha256") != digest(raw)
        or digest(
            _read_private_bytes(state_path, "project-rule render state", 64 * 1024)
        )
        != verified.get("state_sha256")
        or _read_private_bytes(rules_path, "rendered project rules", MAX_RULES_BYTES)
        != raw
    ):
        raise ProjectSpecError(
            "UNSAFE_RULES", "project-rule render verification is stale"
        )
    if b"\x00" in raw:
        raise ProjectSpecError("UNSAFE_RULES", "pending project rules are invalid")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectSpecError(
            "UNSAFE_RULES", "pending project rules must be UTF-8"
        ) from error
    return raw


def _turn_hash(turn_id: object) -> str:
    value = str(turn_id)
    if not value or len(value) > 256:
        raise ProjectSpecError("LIFECYCLE_REQUIRED", "turn identity is required")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _turn_matches(
    state: dict[str, Any],
    turn_id: object | None,
    turn_token: str | None,
) -> bool:
    if (turn_id is None) == (turn_token is None):
        return False
    if turn_token is not None:
        return (
            re.fullmatch(r"[0-9a-f]{64}", turn_token) is not None
            and state.get("turn_sha256") == turn_token
        )
    return state.get("turn_sha256") == _turn_hash(turn_id)


def _state_base(
    project_root: Path, session_id: object, turn_id: object
) -> tuple[dict[str, Any], Path]:
    project, git_root, scope, head = project_identity(project_root)
    policy = load_project_config(project)
    path = lifecycle_path(git_root, session_id)
    return (
        {
            "schema": STATE_SCHEMA,
            "project_scope": scope,
            "git_head_at_prompt": head,
            "turn_sha256": _turn_hash(turn_id),
            "phase": "waived" if policy["mode"] == "disabled" else "planning-required",
            "receipt_sha256": None,
            "requirements_sha256": None,
            "design_sha256": None,
            "rules_path": None,
            "rules_sha256": None,
            "project_instructions_state_sha256": None,
            "project_instructions_reload_required": None,
            "write_epoch": 0,
            "planned_write_epoch": None,
            "waiver": "project-policy" if policy["mode"] == "disabled" else None,
        },
        path,
    )


def start_prompt(
    project_root: Path, session_id: object, turn_id: object
) -> dict[str, Any]:
    fresh, path = _state_base(project_root, session_id, turn_id)
    existing = _read_private(path)
    if existing is None or existing.get("phase") in {"sealed", "waived"}:
        _write_private(path, fresh)
        return fresh
    if existing.get("project_scope") != fresh["project_scope"]:
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "unfinished lifecycle is bound to another scope"
        )
    existing["git_head_at_prompt"] = fresh["git_head_at_prompt"]
    existing["turn_sha256"] = fresh["turn_sha256"]
    if existing.get("phase") != "seal-armed":
        existing["phase"] = (
            "reconciliation-required"
            if int(existing.get("write_epoch", 0)) > 0
            or existing.get("phase")
            in {"implementation-open", "reconciliation-required"}
            else "planning-required"
        )
        for field in (
            "receipt_sha256",
            "requirements_sha256",
            "design_sha256",
            "rules_path",
            "rules_sha256",
            "planned_write_epoch",
        ):
            existing[field] = None
    _write_private(path, existing)
    return existing


def load_for_project(
    project_root: Path, session_id: object
) -> tuple[dict[str, Any] | None, Path, Path]:
    project, git_root, scope, _head = project_identity(project_root)
    path = lifecycle_path(git_root, session_id)
    state = _read_private(path)
    if state is not None and state.get("project_scope") != scope:
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "lifecycle is bound to another project scope"
        )
    return state, path, project


def plan(
    project_root: Path,
    session_id: object,
    turn_id: object | None,
    *,
    turn_token: str | None = None,
    rules_file: Path | None = None,
    render_state_file: Path | None = None,
    project_instructions_private_root: Path | None = None,
) -> dict[str, Any]:
    receipt = validate_project(project_root)
    state, path, project = load_for_project(project_root, session_id)
    if state is None or not _turn_matches(state, turn_id, turn_token):
        raise ProjectSpecError(
            "LIFECYCLE_REQUIRED", "current prompt has no bound lifecycle"
        )
    if state.get("phase") not in {
        "planning-required",
        "reconciliation-required",
        "planned",
    }:
        raise ProjectSpecError(
            "LIFECYCLE_INVALID", "spec planning is not allowed in this phase"
        )
    state["phase"] = "planned"
    state["receipt_sha256"] = canonical_digest(receipt)
    state["requirements_sha256"] = receipt["requirements"]["sha256"]
    state["design_sha256"] = receipt["design"]["sha256"]
    state["planned_write_epoch"] = int(state.get("write_epoch", 0))
    rule_inputs = (
        rules_file,
        render_state_file,
        project_instructions_private_root,
    )
    if any(value is None for value in rule_inputs):
        raise ProjectSpecError(
            "UNSAFE_RULES",
            "rendered rules and their verified project-agent render state are required",
        )
    assert rules_file is not None
    assert render_state_file is not None
    assert project_instructions_private_root is not None
    git_root = Path(str(receipt["git_root"]))
    _require_current_project_instructions_bundle(
        path,
        project_instructions_private_root,
        rules_file=rules_file,
        render_state_file=render_state_file,
    )
    raw = _verified_rendered_rules(
        project,
        git_root,
        receipt,
        rules_file,
        render_state_file,
        project_instructions_private_root,
    )
    rules_path = path.parent / RULES_NAME
    _write_private_bytes(rules_path, raw)
    state["rules_path"] = RULES_NAME
    state["rules_sha256"] = digest(raw)
    _write_private(path, state)
    return state


def open_implementation(
    project_root: Path,
    session_id: object,
    turn_id: object | None,
    *,
    turn_token: str | None = None,
) -> dict[str, Any]:
    receipt = validate_project(project_root)
    state, path, _project = load_for_project(project_root, session_id)
    if state is None or not _turn_matches(state, turn_id, turn_token):
        raise ProjectSpecError(
            "LIFECYCLE_REQUIRED", "current prompt has no bound lifecycle"
        )
    if state.get("phase") != "planned":
        raise ProjectSpecError(
            "LIFECYCLE_INVALID", "implementation requires a planned lifecycle"
        )
    if state.get("receipt_sha256") != canonical_digest(receipt):
        raise ProjectSpecError("SPEC_CHANGED", "specs changed after planning")
    state["phase"] = "implementation-open"
    _write_private(path, state)
    return state


def mark_material_write(
    project_root: Path, session_id: object
) -> dict[str, Any] | None:
    state, path, _project = load_for_project(project_root, session_id)
    if state is None or state.get("phase") not in {
        "implementation-open",
        "reconciliation-required",
    }:
        return state
    state["phase"] = "reconciliation-required"
    state["write_epoch"] = int(state.get("write_epoch", 0)) + 1
    _write_private(path, state)
    return state


def waive(
    project_root: Path,
    session_id: object,
    turn_id: object | None,
    reason: str,
    *,
    turn_token: str | None = None,
) -> dict[str, Any]:
    allowed = {"documentation-only", "read-only", "non-project", "project-policy"}
    if reason not in allowed:
        raise ProjectSpecError("WAIVER_INVALID", "waiver reason is not recognized")
    state, path, _project = load_for_project(project_root, session_id)
    if state is None or not _turn_matches(state, turn_id, turn_token):
        raise ProjectSpecError(
            "LIFECYCLE_REQUIRED", "current prompt has no bound lifecycle"
        )
    if state.get("phase") != "planning-required":
        raise ProjectSpecError(
            "LIFECYCLE_INVALID", "waiver is only valid before implementation"
        )
    state["phase"] = "waived"
    state["waiver"] = reason
    _write_private(path, state)
    return state


def _verify_project_instructions(
    project: Path,
    git_root: Path,
    state_path: Path,
    private_root: Path,
) -> tuple[str, bool]:
    absolute_state = Path(os.path.abspath(state_path.expanduser()))
    absolute_root = Path(os.path.abspath(private_root.expanduser()))
    _outside_git(absolute_state, git_root, "project-instructions state")
    _outside_git(absolute_root, git_root, "project-instructions private root")
    state_raw = _read_private_bytes(
        absolute_state, "project-instructions state", 64 * 1024
    )
    helper = (
        Path(__file__).resolve().parents[3]
        / "project-agent-instructions"
        / "scripts"
        / "project_agent_instructions.py"
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
                "verify",
                "--state",
                str(absolute_state),
                "--private-root",
                str(absolute_root),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        result: Any = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ProjectSpecError(
            "PROJECT_INSTRUCTIONS_REQUIRED",
            "project-instructions verification could not run",
        ) from error
    if (
        completed.returncode != 0
        or not isinstance(result, dict)
        or result.get("status") != "ok"
        or result.get("project_root") != str(project)
        or type(result.get("reload_required")) is not bool
    ):
        raise ProjectSpecError(
            "PROJECT_INSTRUCTIONS_REQUIRED",
            "a current verified project-instructions decision is required before sealing",
        )
    return digest(state_raw), bool(result["reload_required"])


def seal(
    project_root: Path,
    session_id: object,
    turn_id: object | None,
    *,
    turn_token: str | None = None,
    project_instructions_state: Path,
    project_instructions_private_root: Path,
) -> dict[str, Any]:
    receipt = validate_project(project_root)
    state, path, project = load_for_project(project_root, session_id)
    if state is None or not _turn_matches(state, turn_id, turn_token):
        raise ProjectSpecError(
            "LIFECYCLE_REQUIRED", "current prompt has no bound lifecycle"
        )
    if state.get("phase") != "seal-armed":
        raise ProjectSpecError(
            "LIFECYCLE_INVALID",
            "apply and verify the terminal project-instructions decision before sealing",
        )
    if state.get("receipt_sha256") != canonical_digest(receipt) or state.get(
        "planned_write_epoch"
    ) != state.get("write_epoch"):
        raise ProjectSpecError(
            "SPEC_CHANGED", "specs or the implementation epoch changed after planning"
        )
    _require_current_project_instructions_bundle(
        path,
        project_instructions_private_root,
        final_state_file=project_instructions_state,
    )
    instruction_digest, reload_required = _verify_project_instructions(
        project,
        Path(str(receipt["git_root"])),
        project_instructions_state,
        project_instructions_private_root,
    )
    state["project_instructions_state_sha256"] = instruction_digest
    state["project_instructions_reload_required"] = reload_required
    state["phase"] = "sealed"
    state["receipt_sha256"] = canonical_digest(receipt)
    state["requirements_sha256"] = receipt["requirements"]["sha256"]
    state["design_sha256"] = receipt["design"]["sha256"]
    _write_private(path, state)
    return state


def rules_context(state: dict[str, Any], path: Path) -> str | None:
    relative = state.get("rules_path")
    expected = state.get("rules_sha256")
    if relative is None:
        return None
    if relative != RULES_NAME or not isinstance(expected, str):
        raise ProjectSpecError(
            "UNSAFE_STATE", "pending project rules reference is invalid"
        )
    rules_path = path.parent / relative
    raw = _read_private_bytes(rules_path, "pending project rules", MAX_RULES_BYTES)
    if digest(raw) != expected:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", "pending project rules changed"
        )
    return raw.decode("utf-8")
