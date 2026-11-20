"""Correlate native Codex tool events with naturally emitted installed hook events.

Read bounded prefixes in place; never copy prompts, tool arguments or transcripts.
This adapter admits the Codex 0.170.0 rollout event shape only; live
qualification remains separate from fixture validation.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shlex
import stat
import os
import sys
from itertools import islice
import verification_workspace as workspace

import three_tier_target as target

SCHEMA = "agentic-sdlc/native-activation-v1"
RELATIVE = "evidence/native-activation.json"
MAX_PREFIX = 16 * 1024 * 1024
MAX_LINE = 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,160}")


class NativeEvidenceError(ValueError):
    pass


def _prefix(
    path: Path, boundary: Path, size: int | None = None, *, private=True
) -> bytes:
    if not path.is_relative_to(boundary) or path.resolve(strict=False) != path:
        raise NativeEvidenceError("Native evidence path escapes its owned run.")
    parent = workspace._open_parent(path)
    try:
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022)
        ):
            raise NativeEvidenceError(
                "Native evidence must be one private regular file."
            )
        count = info.st_size if size is None else size
        if (
            not isinstance(count, int)
            or not 0 < count <= MAX_PREFIX
            or count > info.st_size
        ):
            raise NativeEvidenceError(
                "Native evidence prefix is unavailable or exceeds its bound."
            )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(count)
        if len(data) != count:
            raise NativeEvidenceError("Native evidence changed while reading.")
        if size is None and not data.endswith(b"\n"):
            data = data[: data.rfind(b"\n") + 1]
        return data
    finally:
        os.close(fd)


def _records(data: bytes):
    for number, line in enumerate(data.splitlines(), 1):
        if len(line) > MAX_LINE:
            raise NativeEvidenceError("Native record exceeds its bound.")
        try:
            value = json.loads(line)
        except (UnicodeError, ValueError) as error:
            raise NativeEvidenceError("Native record is malformed.") from error
        if not isinstance(value, dict):
            raise NativeEvidenceError("Native record is not an object.")
        yield number, value


def _calls(data: bytes, owner_hash: str) -> dict[tuple[str, str], int]:
    session_seen = False
    calls = {}
    for line, record in _records(data):
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            raise NativeEvidenceError("Unsupported native payload.")
        if record.get("type") == "session_meta":
            identity = payload.get("id")
            if (
                session_seen
                or not isinstance(identity, str)
                or hashlib.sha256(identity.encode()).hexdigest() != owner_hash
                or payload.get("source") != "cli"
                or payload.get("cli_version") != "0.170.0"
            ):
                raise NativeEvidenceError(
                    "Native session identity or event version is unsupported."
                )
            session_seen = True
        elif record.get("type") == "event_msg" and payload.get("type") == "item_completed":
            # Code-mode model calls wrap native commands. PreToolUse identifies
            # the actual CommandExecution item, not that enclosing model call.
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != "CommandExecution":
                continue
            if (item.get("source") != "unified_exec_startup"
                    or item.get("status") not in {"completed", "failed"}
                    or type(item.get("exit_code")) is not int):
                continue
            thread = payload.get("thread_id")
            if not isinstance(thread, str) or hashlib.sha256(thread.encode()).hexdigest() != owner_hash:
                raise NativeEvidenceError("Native command item belongs to a foreign session.")
            turn, call = payload.get("turn_id"), item.get("id")
            if (
                session_seen
                and isinstance(turn, str)
                and isinstance(call, str)
                and IDENTIFIER.fullmatch(turn)
                and IDENTIFIER.fullmatch(call)
            ):
                calls[(turn, call)] = line
    if not session_seen:
        raise NativeEvidenceError("Native session metadata is absent.")
    return calls


def _matches(native: bytes, hooks: bytes, startup: dict, *, after_line=0) -> list[dict]:
    calls = _calls(native, startup["owner_session_hash"])
    matches = []
    for line, event in _records(hooks):
        if (
            event.get("event") != "PreToolUse"
            or event.get("project_id") != startup["project_id"]
            or event.get("run_id") != startup["run_id"]
            or event.get("decision") not in {"allow", "context", "deny"}
        ):
            continue
        key = (event.get("turn_id"), event.get("tool_use_id"))
        if all(isinstance(item, str) for item in key) and key in calls and calls[key] > after_line:
            matches.append(
                {
                    "turn_id": key[0],
                    "tool_use_id": key[1],
                    "native_line": calls[key],
                    "hook_line": line,
                }
            )
    if not matches:
        raise NativeEvidenceError(
            "Native activation lacks a matching naturally emitted hook event."
        )
    return matches[:32]


def _paths(state: dict, startup: dict) -> tuple[Path, Path, Path]:
    boundary = Path(state["run_root"])
    if startup["agent"] != "codex":
        raise NativeEvidenceError(
            "Native activation adapter is not qualified for this host."
        )
    home = Path(state["private_root"]) / "codex-home"
    hooks = (
        home
        / "sdlc-runs"
        / startup["project_id"]
        / startup["run_id"]
        / "history/hook-events.jsonl"
    )
    if any(
        not isinstance(startup[key], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", startup[key]) is None
        for key in ("project_id", "run_id")
    ):
        raise NativeEvidenceError("Native workflow identity is unsafe.")
    return boundary, home, hooks


def hook_sources(skills_root: Path, *, home: Path | None = None) -> dict[str, bytes]:
    """Read the loader-defined hook closure, optionally checking its installation."""
    try:
        target.owned_git_origin._load_skill_support(
            "runtime", __file__, "sdlc-workflow-test/scripts/three_tier_native.py",
            source_only=True,
        )
        loader = sys.modules["_skills_trusted_runtime"]
        loader_relative = "global-context-management/scripts/trusted_runtime.py"
        if Path(loader.__file__).resolve() != skills_root / loader_relative:
            raise NativeEvidenceError("Native hook source uses a different runtime catalog.")
        paths = {
            "sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py": "pre_tool_use_sdlc_policy.py",
            loader_relative: "trusted_runtime.py",
        }
        for name in loader._GROUPS["sdlc-hook-state"]:
            relative = loader._SOURCES[name]
            paths[relative] = (
                "lib/" + Path(relative).name if name.startswith("lib")
                else Path(relative).name.removesuffix(".template")
            )
        sources = {}
        for relative, installed in paths.items():
            data, _ = loader._read_source(skills_root / relative)
            if home is not None:
                actual, _ = loader._read_source(home / "hooks" / installed)
                if actual != data:
                    raise NativeEvidenceError("Installed native hook differs from the frozen source.")
            sources[relative] = data
        return sources
    except (OSError, ImportError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, NativeEvidenceError):
            raise
        raise NativeEvidenceError("Native hook closure is unavailable or invalid.") from error


def _hook_identity(home: Path, skills_root: Path) -> dict:
    sources = hook_sources(skills_root, home=home)
    source_bytes = sources["sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py"]
    return {
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "installed_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }


def observe(state: dict, *, skills_root: Path) -> dict:
    startup = target.validate_startup_receipt(state)
    return _observe_identity(state, skills_root=skills_root, startup=startup)


def _observe_identity(state: dict, *, skills_root: Path, startup: dict) -> dict:
    boundary, home, hooks = _paths(state, startup)
    sessions = home / "sessions"
    if sessions.resolve(strict=False) != sessions:
        raise NativeEvidenceError("Native session directory is unsafe.")
    candidates = list(islice(sessions.rglob("*.jsonl"), 257))
    if len(candidates) > 256:
        raise NativeEvidenceError("Native session discovery exceeds its bound.")
    selected = []
    for path in candidates:
        header = _prefix(path, boundary, min(path.stat().st_size, MAX_LINE))
        # Inspect only bounded session metadata before selecting the trace.
        first = next(_records(header.split(b"\n", 1)[0]), (None, {}))[1]
        metadata = first.get("payload")
        if first.get("type") != "session_meta" or not isinstance(metadata, dict):
            raise NativeEvidenceError("Native session metadata is malformed.")
        identity = metadata.get("id")
        if (
            isinstance(identity, str)
            and hashlib.sha256(identity.encode()).hexdigest()
            == startup["owner_session_hash"]
        ):
            selected.append((path, _prefix(path, boundary)))
    if len(selected) != 1:
        raise NativeEvidenceError(
            "Exactly one owned native coordinator trace is required."
        )
    native_path, native = selected[0]
    hook_bytes = _prefix(hooks, boundary)

    def reference(path, data):
        return {
            "path": str(path.relative_to(boundary)),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    return {
        "schema": SCHEMA,
        "verification_id": state["verification_id"],
        "owner_session_hash": startup["owner_session_hash"],
        "hook": _hook_identity(home, skills_root),
        "native": reference(native_path, native),
        "events": reference(hooks, hook_bytes),
        "matches": _matches(native, hook_bytes, startup),
    }


def _validate_identity(state, *, skills_root, startup, value):
    boundary, home, hooks = _paths(state, startup)
    if (
        set(value)
        != {
            "schema",
            "verification_id",
            "owner_session_hash",
            "hook",
            "native",
            "events",
            "matches",
        }
        or value["schema"] != SCHEMA
        or value["verification_id"] != state["verification_id"]
        or value["owner_session_hash"] != startup["owner_session_hash"]
        or value["hook"] != _hook_identity(home, skills_root)
    ):
        raise NativeEvidenceError("Native activation identity changed.")
    contents = {}
    for key in ("native", "events"):
        ref = value[key]
        if (
            not isinstance(ref, dict)
            or set(ref) != {"path", "bytes", "sha256"}
            or not isinstance(ref["path"], str)
        ):
            raise NativeEvidenceError(
                "Native activation source reference is invalid."
            )
        path = boundary / ref["path"]
        if (key == "events" and path != hooks) or (
            key == "native"
            and (
                not path.is_relative_to(home / "sessions")
                or path.suffix != ".jsonl"
            )
        ):
            raise NativeEvidenceError(
                "Native activation source is outside its canonical owner."
            )
        contents[key] = _prefix(path, boundary, ref["bytes"])
        if hashlib.sha256(contents[key]).hexdigest() != ref["sha256"]:
            raise NativeEvidenceError("Native activation source prefix changed.")
    if value["matches"] != _matches(
        contents["native"], contents["events"], startup
    ):
        raise NativeEvidenceError("Native activation correlation changed.")
    return contents


def validate(state: dict, *, skills_root: Path) -> dict:
    try:
        startup = target.validate_startup_receipt(state)
        boundary = Path(state["run_root"])
        binding = state.get("native_activation")
        if (
            not isinstance(binding, dict)
            or set(binding) != {"path", "sha256"}
            or binding["path"] != RELATIVE
        ):
            raise NativeEvidenceError("Native activation receipt is missing.")
        data = _prefix(boundary / RELATIVE, boundary)
        if hashlib.sha256(data).hexdigest() != binding["sha256"]:
            raise NativeEvidenceError("Native activation receipt changed.")
        value = json.loads(data)
        _validate_identity(state, skills_root=skills_root, startup=startup, value=value)
        return value
    except (OSError, ValueError, KeyError, TypeError, target.TargetError) as error:
        if isinstance(error, NativeEvidenceError):
            raise
        raise NativeEvidenceError(
            "Native activation evidence is unavailable or invalid."
        ) from error


def _continuation_operation(state, startup, contents, *, skills_root):
    """Recognize one successful direct continuation, followed by owned hooks."""
    expected = [
        "-B", str(skills_root / "worktree/scripts/workflow_intake.py"),
        "--owner", "agentic-sdlc", "continue",
        "--project-root", state["selected_project_root"],
    ]
    calls = _calls(contents["native"], startup["owner_session_hash"])
    matches = []
    for line, record in _records(contents["native"]):
        payload = record.get("payload", {})
        item = payload.get("item", {})
        if not isinstance(item, dict):
            continue
        if (record.get("type") != "event_msg"
                or payload.get("type") != "item_completed"
                or item.get("type") != "CommandExecution"
                or calls.get((payload.get("turn_id"), item.get("id"))) != line
                or item.get("status") != "completed" or item.get("exit_code") != 0):
            continue
        argv = item.get("command")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            continue
        if len(argv) == 3 and Path(argv[0]).name in {"zsh", "bash", "sh"} and argv[1] in {"-c", "-lc"}:
            try:
                argv = shlex.split(argv[2])
            except ValueError:
                continue
        if (not argv or re.fullmatch(r"python3(?:\.\d+){0,2}", Path(argv[0]).name) is None
                or argv[1:] != expected):
            continue
        followup = _matches(contents["native"], contents["events"], startup, after_line=line)
        matches.append({"turn_id": payload["turn_id"], "tool_use_id": item["id"],
                        "native_line": line, "followup": followup[0]})
    if len(matches) != 1:
        raise NativeEvidenceError("Continuation needs one successful native continue command and a later same-run hook.")
    return matches[0]


def _continuation_identity(state, agent):
    startup = target.validate_startup_receipt(state)
    registration = target._registration(state, agent)
    owner = registration[3]["owner_session_hash"]
    if owner == startup["owner_session_hash"]:
        raise NativeEvidenceError("Continuation requires a fresh native coordinator.")
    return {**startup, "owner_session_hash": owner}


def observe_continuation(state, *, skills_root, agent):
    startup = _continuation_identity(state, agent)
    activation = _observe_identity(state, skills_root=skills_root, startup=startup)
    contents = _validate_identity(state, skills_root=skills_root, startup=startup, value=activation)
    return {"schema": "agentic-sdlc/native-continuation-v1", "activation": activation,
            "operation": _continuation_operation(state, startup, contents, skills_root=skills_root)}


def validate_continuation(state, value, *, skills_root, agent):
    try:
        startup = _continuation_identity(state, agent)
        if (not isinstance(value, dict) or set(value) != {"schema", "activation", "operation"}
                or value["schema"] != "agentic-sdlc/native-continuation-v1"):
            raise NativeEvidenceError("Native continuation evidence is missing or invalid.")
        contents = _validate_identity(state, skills_root=skills_root, startup=startup, value=value["activation"])
        if value["operation"] != _continuation_operation(state, startup, contents, skills_root=skills_root):
            raise NativeEvidenceError("Native continuation correlation changed.")
        return value
    except (OSError, ValueError, KeyError, TypeError, target.TargetError) as error:
        if isinstance(error, NativeEvidenceError):
            raise
        raise NativeEvidenceError("Native continuation evidence is unavailable or invalid.") from error
