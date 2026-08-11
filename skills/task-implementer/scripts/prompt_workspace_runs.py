#!/usr/bin/env python3
"""Run snapshots, drift checks, and prompt metadata listing."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

from prompt_workspace_core import (
    HUB_FILENAME,
    MAX_PROMPT_BYTES,
    PROMPT_ID_RE,
    PROMPT_SCHEMA,
    REVISION_RE,
    RUN_ID_RE,
    RUN_SCHEMA,
    TERMINAL_RUN_STATUSES,
    PromptDocument,
    PromptWorkspaceError,
    contains_secret,
    create_prompt,
    complete_prompt_files_v3_migration,
    ensure_private_dir,
    ensure_prompt_hub,
    ensure_unique_prompt_id,
    init_workspace,
    iso_seconds,
    legacy_project_workspace_manifest,
    load_json_object,
    migrate_prompt_files_v2,
    now_local,
    now_utc,
    parse_frontmatter,
    private_chmod,
    read_prompt,
    resolve_prompt_reference,
    require_mode,
    required_string,
    stable_json,
    verify_workspace,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_lanes import ensure_project_lane
from prompt_workspace_specs import (
    begin_requirements_refinement,
    load_requirements_refinement,
    load_steering_ledger,
    pending_steering_revisions,
)


RUN_STATUSES = {
    "prepared",
    "running",
    "blocked",
    *TERMINAL_RUN_STATUSES,
}

STARTER_ASK = "Untitled prompt"
ACTIVITY_SCHEMA = "task-implementer/activity-v1"
QUEUE_SCHEMA = "task-implementer/prompt-queue-v1"
QUEUE_HISTORY_LIMIT = 200


def iso_utc(value: datetime) -> str:
    """Normalize activity timestamps so ordering is offset-independent."""

    return iso_seconds(value.astimezone(timezone.utc))


@contextmanager
def scope_lock(scope_dir: Path) -> Iterator[None]:
    """Serialize run transitions for one private project scope."""

    lock_path = scope_dir / ".workspace.lock"
    if lock_path.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "workspace lock must not be a symlink"
        )
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "workspace lock could not be opened safely"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "workspace lock is not a regular file"
            )
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + 10
        if os.name == "posix":
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PromptWorkspaceError(
                            "WORKSPACE_BUSY",
                            "another run transition holds the scope lock",
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif os.name == "nt":  # pragma: no cover - exercised on Windows.
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise PromptWorkspaceError(
                            "WORKSPACE_BUSY",
                            "another run transition holds the scope lock",
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - unsupported platforms fail closed.
            raise PromptWorkspaceError(
                "ENVIRONMENT_BLOCKER", "scope locking is unsupported on this platform"
            )
    finally:
        os.close(descriptor)


def require_inputs_directory(run_dir: Path) -> Path:
    inputs = run_dir / "inputs"
    if inputs.is_symlink() or not inputs.is_dir():
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run inputs directory is missing or unsafe"
        )
    if inputs.resolve() != run_dir.resolve() / "inputs":
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run inputs directory escapes private storage"
        )
    require_mode(inputs, 0o700, "run inputs directory")
    return inputs


def read_handoff_text(run_dir: Path) -> str | None:
    handoff = run_dir / "handoff.md"
    if not handoff.exists():
        return None
    if handoff.is_symlink() or not handoff.is_file():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "handoff path is unsafe")
    require_mode(handoff, 0o600, "handoff")
    try:
        return handoff.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff is unreadable or not valid UTF-8"
        ) from exc


def run_status(run_dir: Path) -> str:
    text = read_handoff_text(run_dir)
    if text is None:
        return "snapshot_only"
    matches = re.findall(r"(?m)^- Overall status:\s*([a-z_]+)\s*$", text)
    if len(matches) != 1 or matches[0] not in RUN_STATUSES:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff has no unique valid overall status"
        )
    return matches[0]


def handoff_field(text: str, label: str) -> str:
    matches = re.findall(
        rf"(?m)^- {re.escape(label)}:\s*(\S(?:.*\S)?)\s*$",
        text,
    )
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"handoff has no unique {label} field"
        )
    return matches[0]


def markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        text,
    )
    if match is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"handoff is missing the {heading} section"
        )
    return match.group(1)


def handoff_last_invoked_at(run_dir: Path) -> str | None:
    text = read_handoff_text(run_dir)
    if text is None:
        return None
    run_section = markdown_section(text, "Run")
    matches = re.findall(
        r"(?m)^- Last invoked at:\s*(\S(?:.*\S)?)\s*$",
        run_section,
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff has multiple Last invoked at fields"
        )
    try:
        value = datetime.fromisoformat(matches[0])
    except ValueError as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff Last invoked at is invalid"
        ) from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff Last invoked at has no UTC offset"
        )
    return iso_utc(value)


def touch_handoff_invocation(run_dir: Path, invoked_at: datetime) -> None:
    """Atomically update private prompt activity without touching the prompt."""

    timestamp = iso_utc(invoked_at)
    handoff = run_dir / "handoff.md"
    text = read_handoff_text(run_dir)
    if text is None:
        return
    run_section = markdown_section(text, "Run")
    matches = re.findall(r"(?m)^- Last invoked at:.*$", run_section)
    if len(matches) > 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "handoff has multiple Last invoked at fields"
        )
    existing = handoff_last_invoked_at(run_dir)
    if existing is not None and existing > timestamp:
        timestamp = existing
    if matches:
        updated = re.sub(
            r"(?m)^- Last invoked at:.*$",
            f"- Last invoked at: {timestamp}",
            text,
            count=1,
        )
    else:
        updated, count = re.subn(
            r"(?m)^(## Run\s*)$",
            rf"\1\n- Last invoked at: {timestamp}",
            text,
            count=1,
        )
        if count != 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff is missing the Run heading"
            )
    write_atomic(handoff, updated.encode("utf-8"))


def load_prompt_activity(scope_dir: Path) -> dict[str, str]:
    """Load optional mutable activity without changing existing workspace schemas."""

    path = scope_dir / "activity.json"
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "prompt activity path is unsafe"
        )
    require_mode(path, 0o600, "prompt activity")
    value = load_json_object(path, "prompt activity")
    if value.get("schema") != ACTIVITY_SCHEMA:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "prompt activity schema is invalid"
        )
    prompts = value.get("prompts")
    if not isinstance(prompts, dict):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "prompt activity entries are invalid"
        )
    result: dict[str, str] = {}
    for prompt_id, timestamp in prompts.items():
        if not isinstance(prompt_id, str) or PROMPT_ID_RE.fullmatch(prompt_id) is None:
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity identity is invalid"
            )
        if not isinstance(timestamp, str):
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity timestamp is invalid"
            )
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity timestamp is invalid"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity timestamp has no UTC offset"
            )
        result[prompt_id] = iso_utc(parsed)
    return result


def record_prompt_invocation(
    scope_dir: Path,
    prompt_id: str,
    invoked_at: datetime,
) -> str:
    """Persist monotonic prompt activity without touching editable prompt files."""

    timestamp = iso_utc(invoked_at)
    activity = load_prompt_activity(scope_dir)
    previous = activity.get(prompt_id)
    if previous is not None and previous > timestamp:
        timestamp = previous
    activity[prompt_id] = timestamp
    write_atomic(
        scope_dir / "activity.json",
        stable_json({"schema": ACTIVITY_SCHEMA, "prompts": activity}),
    )
    return timestamp


def _queue_path(scope_dir: Path) -> Path:
    return scope_dir / "prompt-queue.json"


def _empty_prompt_queue() -> dict[str, object]:
    return {"schema": QUEUE_SCHEMA, "entries": [], "history": []}


def _validate_queue_entry(entry: object, *, history: bool = False) -> dict[str, object]:
    required = {
        "queue_id",
        "prompt_id",
        "title",
        "source_path",
        "queued_at",
        "updated_at",
        "sha256",
        "intent_sha256",
        "snapshot",
    }
    if history:
        required |= {"disposition", "resolved_at"}
    if not isinstance(entry, dict) or set(entry) != required:
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue entry shape is invalid"
        )
    for key in required:
        if not isinstance(entry.get(key), str) or not str(entry[key]).strip():
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", f"prompt queue {key} is invalid"
            )
    if not PROMPT_ID_RE.fullmatch(str(entry["prompt_id"])):
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue prompt ID is invalid"
        )
    if re.fullmatch(r"queued-[0-9a-f]{32}", str(entry["queue_id"])) is None:
        raise PromptWorkspaceError("QUEUE_STATE_INVALID", "prompt queue ID is invalid")
    if history and entry.get("disposition") not in {
        "activated",
        "canceled",
        "no_effect",
    }:
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue disposition is invalid"
        )
    for key in ("sha256", "intent_sha256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(entry[key])) is None:
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", f"prompt queue {key} is invalid"
            )
    if Path(str(entry["source_path"])).name != entry["source_path"]:
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue source path is invalid"
        )
    for key in ("queued_at", "updated_at", *(("resolved_at",) if history else ())):
        try:
            parsed = datetime.fromisoformat(str(entry[key]))
        except ValueError as exc:
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", f"prompt queue {key} is invalid"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", f"prompt queue {key} is invalid"
            )
    return dict(entry)


def load_prompt_queue(scope_dir: Path) -> dict[str, object]:
    path = _queue_path(scope_dir)
    if not path.exists():
        return _empty_prompt_queue()
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError("QUEUE_STATE_INVALID", "prompt queue path is unsafe")
    require_mode(path, 0o600, "prompt queue")
    queue = load_json_object(path, "prompt queue")
    if (
        set(queue) != {"schema", "entries", "history"}
        or queue.get("schema") != QUEUE_SCHEMA
    ):
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue schema is invalid"
        )
    entries = queue.get("entries")
    history = queue.get("history")
    if not isinstance(entries, list) or not isinstance(history, list):
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue collections are invalid"
        )
    validated_entries = [_validate_queue_entry(item) for item in entries]
    validated_history = [_validate_queue_entry(item, history=True) for item in history]
    prompt_ids = [str(item["prompt_id"]) for item in validated_entries]
    if len(prompt_ids) != len(set(prompt_ids)):
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue contains duplicate prompts"
        )
    return {
        "schema": QUEUE_SCHEMA,
        "entries": validated_entries,
        "history": validated_history,
    }


def _save_prompt_queue(scope_dir: Path, queue: dict[str, object]) -> None:
    write_atomic(_queue_path(scope_dir), stable_json(queue))


def _queue_snapshot(scope_dir: Path, document: PromptDocument) -> str:
    snapshot_dir = scope_dir / "queued-prompts" / document.prompt_id
    ensure_private_dir(snapshot_dir)
    snapshot = snapshot_dir / f"{document.sha256}.md"
    if snapshot.exists():
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", "queued prompt snapshot is unsafe"
            )
        require_mode(snapshot, 0o600, "queued prompt snapshot")
        if snapshot.read_bytes() != document.raw:
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", "queued prompt snapshot digest collides"
            )
    else:
        write_exclusive(snapshot, document.raw)
    return str(snapshot.relative_to(scope_dir))


def enqueue_prompt_unlocked(
    scope_dir: Path,
    document: PromptDocument,
    queued_at: datetime,
) -> dict[str, object]:
    timestamp = iso_utc(queued_at)
    queue = load_prompt_queue(scope_dir)
    entries = list(queue["entries"])
    snapshot = _queue_snapshot(scope_dir, document)
    position = next(
        (
            index
            for index, item in enumerate(entries)
            if item["prompt_id"] == document.prompt_id
        ),
        None,
    )
    if position is None:
        entry = {
            "queue_id": f"queued-{uuid.uuid4().hex}",
            "prompt_id": document.prompt_id,
            "title": document.title,
            "source_path": document.path.name,
            "queued_at": timestamp,
            "updated_at": timestamp,
            "sha256": document.sha256,
            "intent_sha256": document.intent_sha256,
            "snapshot": snapshot,
        }
        entries.append(entry)
        action = "queued"
        position = len(entries) - 1
    else:
        entry = dict(entries[position])
        unchanged = entry["intent_sha256"] == document.intent_sha256
        entry.update(
            {
                "title": document.title,
                "source_path": document.path.name,
                "updated_at": timestamp,
                "sha256": document.sha256,
                "intent_sha256": document.intent_sha256,
                "snapshot": snapshot,
            }
        )
        entries[position] = entry
        action = "already_queued" if unchanged else "queue_updated"
    queue["entries"] = entries
    _save_prompt_queue(scope_dir, queue)
    return {
        "action": action,
        "position": position + 1,
        "entry": entry,
    }


def _resolve_queue_entry(
    entries: list[dict[str, object]], reference: str
) -> tuple[int, dict[str, object]]:
    matches = [
        (index, item)
        for index, item in enumerate(entries)
        if reference in {item["source_path"], item["prompt_id"], item["queue_id"]}
    ]
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "QUEUE_ENTRY_NOT_FOUND",
            "queued prompt reference must match one filename, prompt ID, or queue ID",
        )
    return matches[0]


def _resolve_queue_entry_unlocked(
    scope_dir: Path,
    reference: str,
    disposition: str,
    resolved_at: datetime,
) -> dict[str, object]:
    queue = load_prompt_queue(scope_dir)
    entries = list(queue["entries"])
    index, entry = _resolve_queue_entry(entries, reference)
    entries.pop(index)
    history = list(queue["history"])
    history.append(
        {
            **entry,
            "disposition": disposition,
            "resolved_at": iso_utc(resolved_at),
        }
    )
    queue["entries"] = entries
    queue["history"] = history[-QUEUE_HISTORY_LIMIT:]
    _save_prompt_queue(scope_dir, queue)
    return entry


def queue_rows(manifest_path: Path) -> list[dict[str, object]]:
    workspace = verify_workspace(manifest_path)
    scope_dir = Path(
        required_string(workspace, "runs_root", "workspace manifest")
    ).parent
    queue = load_prompt_queue(scope_dir)
    return [
        {
            "position": index,
            "queue_id": item["queue_id"],
            "prompt_id": item["prompt_id"],
            "title": item["title"],
            "source_path": item["source_path"],
            "queued_at": item["queued_at"],
            "updated_at": item["updated_at"],
        }
        for index, item in enumerate(queue["entries"], start=1)
    ]


def cancel_queued_prompt(
    manifest_path: Path,
    reference: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    scope_dir = Path(
        required_string(workspace, "runs_root", "workspace manifest")
    ).parent
    with scope_lock(scope_dir):
        queue = load_prompt_queue(scope_dir)
        index, queued = _resolve_queue_entry(list(queue["entries"]), reference)
        if index == 0:
            runs_root = Path(
                required_string(workspace, "runs_root", "workspace manifest")
            )
            for run_dir, manifest in load_run_manifests(runs_root):
                verified = verify_run(workspace, run_dir.name, None)
                recovered = _recover_queued_activation_unlocked(
                    scope_dir,
                    queued,
                    run_dir,
                    manifest,
                    verified,
                    clock=clock,
                )
                if recovered is not None:
                    return {
                        "status": "already_activated",
                        "prompt_id": recovered["prompt_id"],
                        "run_id": recovered["run_id"],
                    }
        entry = _resolve_queue_entry_unlocked(scope_dir, reference, "canceled", clock())
        return {"status": "canceled", "prompt_id": entry["prompt_id"]}


def queued_prompt_head(manifest_path: Path) -> dict[str, object] | None:
    workspace = verify_workspace(manifest_path)
    scope_dir = Path(
        required_string(workspace, "runs_root", "workspace manifest")
    ).parent
    entries = load_prompt_queue(scope_dir)["entries"]
    if not entries:
        return None
    prompt_root = Path(required_string(workspace, "prompt_root", "workspace manifest"))
    entry = dict(entries[0])
    document = read_prompt(
        prompt_root / str(entry["source_path"]), prompt_root, require_content=True
    )
    if document.prompt_id != entry["prompt_id"]:
        raise PromptWorkspaceError(
            "QUEUED_PROMPT_DRIFT", "queue head now refers to a different prompt"
        )
    if (
        document.sha256 != entry["sha256"]
        or document.intent_sha256 != entry["intent_sha256"]
    ):
        raise PromptWorkspaceError(
            "QUEUED_PROMPT_DRIFT",
            "queue head changed after acceptance; explicitly run it again to update the queue",
        )
    return {**entry, "path": str(document.path)}


def _rewrite_prompt_v3_references(
    scope_dir: Path,
    prompt_root: Path,
    migrations: list[dict[str, str]],
) -> None:
    """Rewrite mutable source pointers after the locked prompt-file migration."""

    if not migrations:
        return
    by_id = {item["prompt_id"]: item for item in migrations}
    queue = load_prompt_queue(scope_dir)
    queue_changed = False
    entries: list[dict[str, object]] = []
    for raw_entry in queue["entries"]:
        entry = dict(raw_entry)
        migration = by_id.get(str(entry["prompt_id"]))
        if migration is not None:
            if entry["source_path"] not in {
                migration["old_name"],
                migration["new_name"],
            }:
                raise PromptWorkspaceError(
                    "QUEUE_STATE_INVALID",
                    "queued prompt source does not match the migration input",
                )
            document = read_prompt(
                prompt_root / migration["new_name"], prompt_root, require_content=True
            )
            repaired = {
                "source_path": document.path.name,
                "sha256": document.sha256,
                "intent_sha256": document.intent_sha256,
                "snapshot": _queue_snapshot(scope_dir, document),
            }
            if any(entry.get(key) != value for key, value in repaired.items()):
                entry.update(repaired)
                queue_changed = True
        entries.append(entry)
    if queue_changed:
        queue["entries"] = entries
        _save_prompt_queue(scope_dir, queue)
    runs_root = scope_dir / "runs"
    for run_dir in sorted(runs_root.glob("run-*")):
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run manifest is missing or unsafe during migration"
            )
        manifest = load_json_object(manifest_path, "run manifest")
        migration = by_id.get(str(manifest.get("prompt_id") or ""))
        if migration is None:
            continue
        source_path = str(manifest.get("source_path") or "")
        if source_path not in {migration["old_name"], migration["new_name"]}:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                "run source does not match the prompt migration input",
            )
        if source_path != migration["new_name"]:
            manifest["source_path"] = migration["new_name"]
            write_atomic(manifest_path, stable_json(manifest))


def initialize_project_workspace(
    project_path: Path,
    codex_home: Path,
    *,
    clock: Callable[[], datetime] = now_local,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Initialize one exact project folder and ensure one starter prompt."""

    requested = project_path.expanduser().resolve()
    legacy = legacy_project_workspace_manifest(requested, codex_home)
    if legacy.exists():
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "legacy workspace-v1 state is unsupported; back up its prompt history "
            f"and remove the private workspace before initializing a lane: {legacy}",
        )
    lane = ensure_project_lane(requested)
    lane_root = Path(str(lane["worktree"]))
    result = init_workspace(
        lane_root,
        str(lane["scope"]),
        codex_home,
        lane=lane,
        clock=clock,
    )
    workspace_path = Path(str(result["workspace"]))
    prompt_root = Path(str(result["prompt_root"]))
    with scope_lock(workspace_path.parent):
        ensure_prompt_hub(prompt_root)
        migrations = migrate_prompt_files_v2(prompt_root)
        _rewrite_prompt_v3_references(workspace_path.parent, prompt_root, migrations)
        complete_prompt_files_v3_migration(prompt_root)
        prompt_paths = sorted(
            path for path in prompt_root.glob("*.md") if path.name != HUB_FILENAME
        )
        starter_created = False
        if not prompt_paths:
            if id_factory is None:
                starter = create_prompt(
                    workspace_path,
                    STARTER_ASK,
                    clock=clock,
                    draft=True,
                )
            else:
                starter = create_prompt(
                    workspace_path,
                    STARTER_ASK,
                    clock=clock,
                    id_factory=id_factory,
                    draft=True,
                )
            starter_created = True
            starter_path = str(starter["path"])
        else:
            starter_path = str(prompt_paths[0].resolve())
        rows = prompt_rows(workspace_path, None, None)
    result.update(
        {
            "starter_prompt": starter_path,
            "starter_created": starter_created,
            "prompts": rows,
        }
    )
    return result


def merge_session_refinement(
    manifest_path: Path,
    refined_file: Path,
    *,
    prompt_reference: str | Path | None,
    expected_sha256: str | None,
    new_objective: bool,
    operation_id: str,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """CAS-merge one accepted lossless refinement into an objective prompt."""

    workspace = verify_workspace(manifest_path)
    prompt_root = Path(required_string(workspace, "prompt_root", "workspace manifest"))
    scope_dir = Path(required_string(workspace, "runs_root", "workspace manifest")).parent
    requested = refined_file.expanduser()
    if requested.is_symlink():
        raise PromptWorkspaceError("PROMPT_PATH_INVALID", "refined input must not be a symlink")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise PromptWorkspaceError("PROMPT_PATH_INVALID", "refined input is unavailable") from error
    if not resolved.is_file() or resolved.stat().st_nlink != 1:
        raise PromptWorkspaceError("PROMPT_PATH_INVALID", "refined input must be one regular file")
    require_mode(resolved, 0o600, "refined session input")
    try:
        refined = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PromptWorkspaceError("PROMPT_INPUT_INVALID", "refined input is not UTF-8") from error
    sensitive = contains_secret(refined)
    if not refined.strip() or sensitive or "\x00" in refined:
        raise PromptWorkspaceError(
            "PROMPT_SENSITIVE_INPUT" if sensitive else "PROMPT_INPUT_INVALID",
            "refined input is empty or contains secret material",
        )
    if not re.fullmatch(r"[0-9a-f]{64}", operation_id):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "session operation identity is invalid"
        )
    if "<!-- prompt-session-operation:" in refined:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID",
            "refined input uses the reserved operation marker namespace",
        )
    operation_marker = f"<!-- prompt-session-operation:{operation_id} -->"
    marker_bytes = operation_marker.encode("utf-8")
    with scope_lock(scope_dir):
        if new_objective:
            if prompt_reference is not None or expected_sha256 is not None:
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID",
                    "new objective cannot name an existing prompt base",
                )
            applied: list[PromptDocument] = []
            for candidate_path in sorted(prompt_root.glob("*.md")):
                if candidate_path.name == HUB_FILENAME:
                    continue
                candidate = read_prompt(
                    candidate_path, prompt_root, require_content=False
                )
                marker_count = candidate.raw.count(marker_bytes)
                if marker_count > 1:
                    raise PromptWorkspaceError(
                        "PROMPT_STATE_INVALID",
                        "session operation appears more than once in one prompt",
                    )
                if marker_count == 1:
                    applied.append(
                        read_prompt(candidate_path, prompt_root, require_content=True)
                    )
            if len(applied) > 1:
                raise PromptWorkspaceError(
                    "PROMPT_CONFLICT",
                    "session operation is claimed by multiple prompts",
                )
            if applied:
                document = applied[0]
                return {
                    "action": "created",
                    "path": str(document.path),
                    "prompt_id": document.prompt_id,
                    "prompt_ref": document.prompt_ref,
                    "sha256": document.sha256,
                    "intent_sha256": document.intent_sha256,
                    "merged": True,
                }
            normalized_title = " ".join(refined.split())
            title = normalized_title[:197].rstrip() + (
                "..." if len(normalized_title) > 200 else ""
            )
            created = create_prompt(
                manifest_path,
                title,
                clock=clock,
                ask_body=f"{refined.strip()}\n\n{operation_marker}",
            )
            created_path = Path(str(created["path"]))
            document = read_prompt(created_path, prompt_root, require_content=True)
            if document.raw.count(marker_bytes) != 1:
                raise PromptWorkspaceError(
                    "PROMPT_STATE_INVALID",
                    "new prompt does not contain the exact session operation",
                )
            ensure_unique_prompt_id(document, prompt_root)
            return {
                "action": "created",
                "path": str(document.path),
                "prompt_id": document.prompt_id,
                "prompt_ref": document.prompt_ref,
                "sha256": document.sha256,
                "intent_sha256": document.intent_sha256,
                "merged": True,
            }
        if prompt_reference is None or expected_sha256 is None:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID",
                "existing objective merge requires a prompt reference and base digest",
            )
        document = resolve_prompt_reference(
            manifest_path, prompt_reference, require_content=True
        )
        marker_count = document.raw.count(marker_bytes)
        if marker_count > 1:
            raise PromptWorkspaceError(
                "PROMPT_STATE_INVALID",
                "session operation appears more than once in the prompt",
            )
        if marker_count == 1:
            return {
                "action": "merged",
                "path": str(document.path),
                "prompt_id": document.prompt_id,
                "prompt_ref": document.prompt_ref,
                "sha256": document.sha256,
                "intent_sha256": document.intent_sha256,
                "merged": True,
            }
        if document.sha256 != expected_sha256:
            raise PromptWorkspaceError(
                "PROMPT_DRIFT",
                "canonical prompt changed after acceptance; explicit reconciliation is required",
            )
        timestamp = iso_seconds(clock())
        separator = "" if document.text.endswith("\n") else "\n"
        heading = (
            f"\n### Session update {timestamp}\n\n"
            if "Steering" in document.sections
            else f"\n## Steering\n\n### Session update {timestamp}\n\n"
        )
        merged = (
            document.text
            + separator
            + heading
            + refined.strip()
            + "\n\n"
            + operation_marker
            + "\n"
        ).encode("utf-8")
        if len(merged) > MAX_PROMPT_BYTES:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", f"merged prompt exceeds {MAX_PROMPT_BYTES} bytes"
            )
        if merged.count(marker_bytes) != 1:
            raise PromptWorkspaceError(
                "PROMPT_STATE_INVALID",
                "merged prompt does not contain the exact session operation once",
            )
        write_atomic(document.path, merged)
        updated = read_prompt(document.path, prompt_root, require_content=True)
        ensure_unique_prompt_id(updated, prompt_root)
        return {
            "path": str(updated.path),
            "prompt_id": updated.prompt_id,
            "prompt_ref": updated.prompt_ref,
            "sha256": updated.sha256,
            "intent_sha256": updated.intent_sha256,
            "merged": True,
        }


def load_run_manifests(
    runs_root: Path, prompt_id: str | None = None
) -> list[tuple[Path, dict[str, object]]]:
    results: list[tuple[Path, dict[str, object]]] = []
    for run_dir in sorted(runs_root.glob("run-*")):
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"run directory is unsafe: {run_dir.name}"
            )
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                f"run manifest is missing or unsafe: {run_dir.name}",
            )
        manifest = load_json_object(manifest_path, "run manifest")
        if manifest.get("schema") != RUN_SCHEMA:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"run manifest schema is invalid: {run_dir.name}"
            )
        if manifest.get("run_id") != run_dir.name:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"run manifest identity is invalid: {run_dir.name}"
            )
        if prompt_id is None or manifest.get("prompt_id") == prompt_id:
            results.append((run_dir, manifest))
    results.sort(key=lambda item: str(item[1].get("created_at", "")))
    return results


def manifest_revisions(manifest: dict[str, object]) -> list[dict[str, object]]:
    revisions = manifest.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run manifest has no immutable revisions"
        )
    result: list[dict[str, object]] = []
    for revision in revisions:
        if not isinstance(revision, dict):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run manifest revision is invalid"
            )
        result.append(revision)
    return result


def revision_record(
    revision_id: str,
    created_at: datetime,
    document: PromptDocument,
    *,
    kind: str,
) -> dict[str, object]:
    return {
        "revision": revision_id,
        "created_at": iso_seconds(created_at),
        "sha256": document.sha256,
        "intent_sha256": document.intent_sha256,
        "kind": kind,
        "bytes": len(document.raw),
        "snapshot": f"inputs/{revision_id}/prompt.md",
    }


def generated_run_id(created_at: datetime) -> str:
    stamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp.lower()}-{uuid.uuid4().hex[:8]}"


def recover_incomplete_revisions_unlocked(
    all_runs: list[tuple[Path, dict[str, object]]],
    *,
    clock: Callable[[], datetime] = now_utc,
) -> bool:
    """Roll back a revision staged before its manifest commit point."""

    recovered = False
    for run_dir, manifest in all_runs:
        revisions = manifest_revisions(manifest)
        latest = revisions[-1]
        if "intent_sha256" not in latest or "kind" not in latest:
            continue
        referenced = {
            Path(str(revision.get("snapshot", ""))).parent.name
            for revision in revisions
        }
        inputs_dir = run_dir / "inputs"
        require_inputs_directory(run_dir)
        orphaned = [
            child
            for child in inputs_dir.iterdir()
            if REVISION_RE.fullmatch(child.name) is not None
            and child.name not in referenced
        ]
        if not orphaned:
            continue
        expected = f"r{len(revisions) + 1:04d}"
        if len(orphaned) != 1 or orphaned[0].name != expected:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run has unexpected uncommitted revisions"
            )
        orphan = orphaned[0]
        if orphan.is_symlink() or not orphan.is_dir():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "uncommitted revision path is unsafe"
            )
        require_mode(orphan, 0o700, "uncommitted revision directory")
        if any(child.name != "prompt.md" for child in orphan.iterdir()):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "uncommitted revision contains unexpected files"
            )
        snapshot = orphan / "prompt.md"
        if snapshot.exists() or snapshot.is_symlink():
            if snapshot.is_symlink() or not snapshot.is_file():
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "uncommitted revision snapshot is unsafe"
                )
            require_mode(snapshot, 0o600, "uncommitted prompt snapshot")
        shutil.rmtree(orphan)
        refinement = load_requirements_refinement(run_dir, required=False)
        latest_intent = str(latest.get("intent_sha256") or latest.get("sha256"))
        if refinement is not None and (
            refinement.get("revision") != latest.get("revision")
            or refinement.get("intent_sha256") != latest_intent
        ):
            begin_requirements_refinement(
                run_dir,
                str(manifest.get("prompt_id")),
                str(latest.get("revision")),
                latest_intent,
                clock=clock,
            )
        recovered = True
    return recovered


def _snapshot_prompt_unlocked(
    manifest_path: Path,
    prompt_path: Path,
    *,
    run_id: str | None,
    force_new_run: bool,
    clock: Callable[[], datetime] = now_utc,
    expected_sha256: str | None = None,
    allow_running: bool = False,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    prompt_root = Path(required_string(workspace, "prompt_root", "workspace manifest"))
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    document = read_prompt(prompt_path, prompt_root, require_content=True)
    ensure_unique_prompt_id(document, prompt_root)
    if expected_sha256 is not None and document.sha256 != expected_sha256:
        raise PromptWorkspaceError(
            "PROMPT_DRIFT", "prompt changed while its run transition was being prepared"
        )
    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "snapshot clock must be timezone-aware"
        )

    all_runs = load_run_manifests(runs_root)
    if recover_incomplete_revisions_unlocked(all_runs, clock=clock):
        all_runs = load_run_manifests(runs_root)
    verified_runs = {
        existing_run.name: verify_run(workspace, existing_run.name, None)
        for existing_run, _ in all_runs
    }
    prior_runs = [
        (run_dir, manifest)
        for run_dir, manifest in all_runs
        if manifest.get("prompt_id") == document.prompt_id
    ]
    if prior_runs:
        prior_latest = manifest_revisions(prior_runs[-1][1])[-1]
        if "intent_sha256" not in prior_latest or "kind" not in prior_latest:
            raise PromptWorkspaceError(
                "WORKFLOW_UPGRADE_REQUIRED",
                "prompt-v1 history cannot be continued; create a fresh prompt-v3 ID",
            )
    active = [
        run_dir.name
        for run_dir, _ in all_runs
        if run_status(run_dir) not in TERMINAL_RUN_STATUSES
    ]

    if run_id is not None:
        conflicting = [active_run for active_run in active if active_run != run_id]
        if conflicting:
            raise PromptWorkspaceError(
                "ACTIVE_RUN_EXISTS",
                f"another unfinished scope run must finish first: {conflicting[-1]}",
            )
        if force_new_run:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", "--run-id and --new-run cannot be combined"
            )
        if not RUN_ID_RE.fullmatch(run_id):
            raise PromptWorkspaceError("RUN_STATE_INVALID", "run ID format is invalid")
        run_dir = runs_root / run_id
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run directory is missing or unsafe"
            )
        manifest_file = run_dir / "manifest.json"
        manifest = load_json_object(manifest_file, "run manifest")
        if (
            manifest.get("schema") != RUN_SCHEMA
            or manifest.get("prompt_id") != document.prompt_id
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run does not belong to the submitted prompt"
            )
        status = run_status(run_dir)
        if status in TERMINAL_RUN_STATUSES:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "completed runs cannot be reconciled"
            )
        if status == "running" and not allow_running:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "a running task cannot be reconciled"
            )
        revisions = manifest_revisions(manifest)
        latest = revisions[-1]
        latest_intent = str(latest.get("intent_sha256") or latest.get("sha256"))
        if latest_intent == document.intent_sha256:
            if status == "snapshot_only":
                return {
                    "run_id": run_id,
                    "revision": latest["revision"],
                    "prompt_id": document.prompt_id,
                    "sha256": latest["sha256"],
                    "intent_sha256": latest_intent,
                    "snapshot": str(run_dir / str(latest["snapshot"])),
                    "manifest": str(manifest_file),
                    "resumed_prepare": True,
                    "created_revision": False,
                }
            verified = verified_runs[run_id]
            if verified.get("reconciliation_pending") is True:
                return {
                    "run_id": run_id,
                    "revision": latest["revision"],
                    "prompt_id": document.prompt_id,
                    "sha256": latest["sha256"],
                    "intent_sha256": latest_intent,
                    "snapshot": str(run_dir / str(latest["snapshot"])),
                    "manifest": str(manifest_file),
                    "resumed_reconciliation": True,
                    "created_revision": False,
                }
            raise PromptWorkspaceError("NO_CHANGES", "submitted prompt is unchanged")
        latest_id = str(latest.get("revision", ""))
        match = REVISION_RE.fullmatch(latest_id)
        if match is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "latest run revision ID is invalid"
            )
        revision_id = f"r{int(match.group(1)) + 1:04d}"
        revision_dir = run_dir / "inputs" / revision_id
        require_inputs_directory(run_dir)
        if revision_dir.exists():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "next revision path already exists"
            )
        try:
            revision_dir.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "next revision path already exists"
            ) from exc
        private_chmod(revision_dir, 0o700)
        snapshot_path = revision_dir / "prompt.md"
        write_exclusive(snapshot_path, document.raw)
        revisions.append(
            revision_record(
                revision_id,
                created_at,
                document,
                kind="active_steering",
            )
        )
        begin_requirements_refinement(
            run_dir,
            document.prompt_id,
            revision_id,
            document.intent_sha256,
            clock=clock,
        )
        manifest["revisions"] = revisions
        write_atomic(manifest_file, stable_json(manifest))
    else:
        if active:
            if len(active) == 1:
                active_run_id = active[0]
                active_verified = verified_runs[active_run_id]
                active_dir, active_manifest = next(
                    (candidate_dir, candidate_manifest)
                    for candidate_dir, candidate_manifest in all_runs
                    if candidate_dir.name == active_run_id
                )
                active_revisions = manifest_revisions(active_manifest)
                active_latest = active_revisions[-1]
                if (
                    active_verified.get("status") == "snapshot_only"
                    and active_manifest.get("prompt_id") == document.prompt_id
                    and str(
                        active_latest.get("intent_sha256")
                        or active_latest.get("sha256")
                    )
                    == document.intent_sha256
                ):
                    return {
                        "run_id": active_run_id,
                        "revision": active_latest["revision"],
                        "prompt_id": document.prompt_id,
                        "sha256": active_latest["sha256"],
                        "intent_sha256": (
                            active_latest.get("intent_sha256")
                            or active_latest["sha256"]
                        ),
                        "snapshot": str(active_dir / str(active_latest["snapshot"])),
                        "manifest": str(active_dir / "manifest.json"),
                        "resumed_prepare": True,
                        "created_revision": False,
                    }
            raise PromptWorkspaceError(
                "ACTIVE_RUN_EXISTS", f"unfinished run requires reconcile: {active[-1]}"
            )
        if prior_runs and not force_new_run:
            latest_revisions = manifest_revisions(prior_runs[-1][1])
            if (
                str(
                    latest_revisions[-1].get("intent_sha256")
                    or latest_revisions[-1].get("sha256")
                )
                == document.intent_sha256
            ):
                raise PromptWorkspaceError(
                    "NO_CHANGES", "submitted prompt matches the latest completed run"
                )
        while True:
            run_id = generated_run_id(created_at)
            run_dir = runs_root / run_id
            try:
                run_dir.mkdir(mode=0o700)
            except FileExistsError:
                continue
            private_chmod(run_dir, 0o700)
            break
        ensure_private_dir(run_dir / "inputs")
        revision_id = "r0001"
        revision_dir = run_dir / "inputs" / revision_id
        ensure_private_dir(revision_dir)
        snapshot_path = revision_dir / "prompt.md"
        manifest_file = run_dir / "manifest.json"
        predecessor = None
        revision_kind = "initial"
        lineage_root = run_id
        if prior_runs:
            parent_dir, parent_manifest = prior_runs[-1]
            parent_verified = verified_runs[parent_dir.name]
            if str(parent_verified["status"]) not in TERMINAL_RUN_STATUSES:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "follow-up predecessor is not terminal"
                )
            parent_revision = manifest_revisions(parent_manifest)[-1]
            predecessor = {
                "run_id": parent_dir.name,
                "revision": parent_revision["revision"],
                "sha256": parent_revision["sha256"],
            }
            revision_kind = "completed_follow_up"
            lineage_root = str(parent_manifest.get("lineage_root") or parent_dir.name)
        manifest = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "project_id": workspace["project_id"],
            "scope_id": workspace["scope_id"],
            "prompt_id": document.prompt_id,
            "source_path": document.path.name,
            "created_at": iso_seconds(created_at),
            "lineage_root": lineage_root,
            "predecessor": predecessor,
            "revisions": [
                revision_record(
                    revision_id,
                    created_at,
                    document,
                    kind=revision_kind,
                )
            ],
        }
        try:
            write_exclusive(snapshot_path, document.raw)
            write_exclusive(manifest_file, stable_json(manifest))
            begin_requirements_refinement(
                run_dir,
                document.prompt_id,
                revision_id,
                document.intent_sha256,
                predecessor_dir=parent_dir if prior_runs else None,
                clock=clock,
            )
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    return {
        "run_id": run_id,
        "revision": revision_id,
        "prompt_id": document.prompt_id,
        "sha256": document.sha256,
        "intent_sha256": document.intent_sha256,
        "snapshot": str(snapshot_path),
        "manifest": str(manifest_file),
        "created_revision": True,
    }


def snapshot_prompt(
    manifest_path: Path,
    prompt_path: Path,
    *,
    run_id: str | None,
    force_new_run: bool,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        return _snapshot_prompt_unlocked(
            manifest_path,
            prompt_path,
            run_id=run_id,
            force_new_run=force_new_run,
            clock=clock,
        )


def _recover_queued_activation_unlocked(
    scope_dir: Path,
    head: dict[str, object] | None,
    run_dir: Path,
    manifest: dict[str, object],
    verified: dict[str, object],
    *,
    clock: Callable[[], datetime],
) -> dict[str, object] | None:
    """Finish dequeue when run creation committed before queue resolution."""

    if head is None or manifest.get("prompt_id") != head.get("prompt_id"):
        return None
    revisions = manifest_revisions(manifest)
    latest = revisions[-1]
    if latest.get("sha256") != head.get("sha256") or (
        latest.get("intent_sha256") or latest.get("sha256")
    ) != head.get("intent_sha256"):
        return None
    created_at = datetime.fromisoformat(str(manifest["created_at"]))
    queued_at = datetime.fromisoformat(str(head["queued_at"]))
    if created_at < queued_at:
        return None
    _resolve_queue_entry_unlocked(
        scope_dir,
        str(head["queue_id"]),
        "activated",
        clock(),
    )
    return {
        "status": "activated",
        "action": "new",
        "recovered": True,
        "run_id": run_dir.name,
        "revision": verified["latest_revision"],
        "prompt_id": manifest["prompt_id"],
        "sha256": latest["sha256"],
        "intent_sha256": latest.get("intent_sha256") or latest["sha256"],
        "snapshot": str(run_dir / str(latest["snapshot"])),
        "manifest": str(run_dir / "manifest.json"),
        "created_revision": True,
    }


def _activate_next_queued_prompt_unlocked(
    manifest_path: Path,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object] | None:
    from prompt_workspace_interop import load_interop, managed

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    scope_dir = runs_root.parent
    queue = load_prompt_queue(scope_dir)
    head = queue["entries"][0] if queue["entries"] else None
    for run_dir, manifest in load_run_manifests(runs_root):
        verified = verify_run(workspace, run_dir.name, None)
        if (
            str(verified["status"]) not in TERMINAL_RUN_STATUSES
            or bool(verified["steering_pending"])
            or bool(verified["reconciliation_pending"])
        ):
            recovered = _recover_queued_activation_unlocked(
                scope_dir,
                head,
                run_dir,
                manifest,
                verified,
                clock=clock,
            )
            if recovered is not None:
                return recovered
            return {"status": "waiting_for_active_run", "run_id": run_dir.name}
        interop = load_interop(run_dir, required=False)
        if interop is not None and managed(interop) and interop["released"] is False:
            return {
                "status": "waiting_for_resource_release",
                "run_id": run_dir.name,
            }

    while True:
        head = queued_prompt_head(manifest_path)
        if head is None:
            return None
        prompt_root = Path(
            required_string(workspace, "prompt_root", "workspace manifest")
        )
        document = read_prompt(
            prompt_root / str(head["source_path"]),
            prompt_root,
            require_content=True,
        )
        snapshot = (scope_dir / str(head["snapshot"])).resolve()
        queued_root = (scope_dir / "queued-prompts").resolve()
        if (
            snapshot == queued_root
            or queued_root not in snapshot.parents
            or snapshot.is_symlink()
            or not snapshot.is_file()
        ):
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", "queued prompt snapshot path is unsafe"
            )
        require_mode(snapshot, 0o600, "queued prompt snapshot")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != head["sha256"]:
            raise PromptWorkspaceError(
                "QUEUE_STATE_INVALID", "queued prompt snapshot digest is invalid"
            )

        matching = load_run_manifests(runs_root, document.prompt_id)
        if matching:
            latest_dir, latest_manifest = matching[-1]
            verified = verify_run(workspace, latest_dir.name, None)
            latest = manifest_revisions(latest_manifest)[-1]
            latest_intent = str(latest.get("intent_sha256") or latest["sha256"])
            if (
                str(verified["status"]) in TERMINAL_RUN_STATUSES
                and latest_intent == document.intent_sha256
            ):
                _resolve_queue_entry_unlocked(
                    scope_dir,
                    str(head["queue_id"]),
                    "no_effect",
                    clock(),
                )
                continue

        result = _snapshot_prompt_unlocked(
            manifest_path,
            document.path,
            run_id=None,
            force_new_run=False,
            clock=clock,
            expected_sha256=document.sha256,
        )
        _resolve_queue_entry_unlocked(
            scope_dir,
            str(head["queue_id"]),
            "activated",
            clock(),
        )
        return {"status": "activated", **result}


def activate_next_queued_prompt(
    manifest_path: Path,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object] | None:
    workspace = verify_workspace(manifest_path)
    scope_dir = Path(
        required_string(workspace, "runs_root", "workspace manifest")
    ).parent
    with scope_lock(scope_dir):
        return _activate_next_queued_prompt_unlocked(manifest_path, clock=clock)


def verify_run(
    workspace: dict[str, object],
    run_id: str,
    document: PromptDocument | None,
    *,
    _lineage_seen: set[str] | None = None,
) -> dict[str, object]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run ID format is invalid")
    lineage_seen = set() if _lineage_seen is None else set(_lineage_seen)
    if run_id in lineage_seen:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run predecessor lineage contains a cycle"
        )
    lineage_seen.add(run_id)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    run_dir = runs_root / run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run directory is missing or unsafe"
        )
    require_mode(run_dir, 0o700, "run directory")
    require_inputs_directory(run_dir)
    manifest_path = run_dir / "manifest.json"
    require_mode(manifest_path, 0o600, "run manifest")
    manifest = load_json_object(manifest_path, "run manifest")
    if manifest.get("schema") != RUN_SCHEMA or manifest.get("run_id") != run_id:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run manifest identity is invalid"
        )
    if not PROMPT_ID_RE.fullmatch(str(manifest.get("prompt_id", ""))):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run prompt ID is invalid")
    try:
        run_created_at = datetime.fromisoformat(str(manifest.get("created_at", "")))
    except ValueError as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run creation timestamp is invalid"
        ) from exc
    if run_created_at.tzinfo is None or run_created_at.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run creation timestamp has no UTC offset"
        )
    source_path = Path(str(manifest.get("source_path", "")))
    if (
        source_path.is_absolute()
        or len(source_path.parts) != 1
        or source_path.suffix.lower() != ".md"
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run source path is invalid")
    if manifest.get("project_id") != workspace.get("project_id") or manifest.get(
        "scope_id"
    ) != workspace.get("scope_id"):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run manifest belongs to another workspace"
        )
    revisions = manifest_revisions(manifest)
    previous_created_at: datetime | None = None
    for expected_number, revision in enumerate(revisions, start=1):
        revision_id = str(revision.get("revision", ""))
        if revision_id != f"r{expected_number:04d}":
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run revisions are not contiguous"
            )
        relative = Path(str(revision.get("snapshot", "")))
        expected_relative = Path("inputs") / revision_id / "prompt.md"
        if relative != expected_relative:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                "snapshot path does not match the revision contract",
            )
        snapshot = run_dir / relative
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"snapshot is missing or unsafe: {revision_id}"
            )
        if snapshot.parent.is_symlink():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"revision directory is unsafe: {revision_id}"
            )
        require_mode(snapshot.parent, 0o700, "revision directory")
        require_mode(snapshot, 0o600, "prompt snapshot")
        try:
            revision_created_at = datetime.fromisoformat(
                str(revision.get("created_at", ""))
            )
        except ValueError as exc:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"revision timestamp is invalid: {revision_id}"
            ) from exc
        if (
            revision_created_at.tzinfo is None
            or revision_created_at.utcoffset() is None
            or (
                previous_created_at is not None
                and revision_created_at < previous_created_at
            )
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"revision timestamp is invalid: {revision_id}"
            )
        previous_created_at = revision_created_at
        digest_value = revision.get("sha256")
        intent_digest = revision.get("intent_sha256")
        revision_kind = revision.get("kind")
        byte_count = revision.get("bytes")
        if (
            not isinstance(digest_value, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest_value) is None
            or (
                intent_digest is not None
                and (
                    not isinstance(intent_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", intent_digest) is None
                )
            )
            or (
                revision_kind is not None
                and revision_kind
                not in {"initial", "active_steering", "completed_follow_up"}
            )
            or not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
            or byte_count > MAX_PROMPT_BYTES
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"revision metadata is invalid: {revision_id}"
            )
        raw = snapshot.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != digest_value or len(raw) != byte_count:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"snapshot digest is invalid: {revision_id}"
            )
        if intent_digest is not None:
            snapshot_document = read_prompt(
                snapshot,
                snapshot.parent,
                require_content=True,
                allow_legacy=True,
                allow_migration_history=True,
            )
            if snapshot_document.intent_sha256 != intent_digest:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID",
                    f"snapshot intent digest is invalid: {revision_id}",
                )
    latest = revisions[-1]
    predecessor = manifest.get("predecessor")
    lineage_root = manifest.get("lineage_root")
    legacy_run = all(
        "intent_sha256" not in revision and "kind" not in revision
        for revision in revisions
    )
    if not legacy_run and any(
        "intent_sha256" not in revision or "kind" not in revision
        for revision in revisions
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run mixes legacy and modern prompt revisions"
        )
    if predecessor is not None:
        if (
            not isinstance(predecessor, dict)
            or set(predecessor) != {"run_id", "revision", "sha256"}
            or not RUN_ID_RE.fullmatch(str(predecessor.get("run_id", "")))
            or not REVISION_RE.fullmatch(str(predecessor.get("revision", "")))
            or re.fullmatch(r"[0-9a-f]{64}", str(predecessor.get("sha256", ""))) is None
            or predecessor.get("run_id") == run_id
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run predecessor lineage is invalid"
            )
        parent_dir = runs_root / str(predecessor["run_id"])
        if parent_dir.is_symlink() or not parent_dir.is_dir():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run predecessor directory is unsafe"
            )
        parent_manifest = load_json_object(
            parent_dir / "manifest.json", "parent run manifest"
        )
        parent_verified = verify_run(
            workspace,
            str(predecessor["run_id"]),
            None,
            _lineage_seen=lineage_seen,
        )
        if (
            parent_manifest.get("schema") != RUN_SCHEMA
            or parent_manifest.get("prompt_id") != manifest.get("prompt_id")
            or str(parent_verified["status"]) not in TERMINAL_RUN_STATUSES
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run predecessor is not terminal prompt history"
            )
        parent_matches = [
            item
            for item in manifest_revisions(parent_manifest)
            if item.get("revision") == predecessor["revision"]
            and item.get("sha256") == predecessor["sha256"]
        ]
        if len(parent_matches) != 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run predecessor revision is invalid"
            )
        expected_root = str(parent_manifest.get("lineage_root") or parent_dir.name)
        if lineage_root != expected_root:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run lineage root does not match its predecessor"
            )
    elif not legacy_run and lineage_root != run_id:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "initial run lineage root must equal its run ID"
        )
    if not legacy_run and not RUN_ID_RE.fullmatch(str(lineage_root)):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run lineage root is invalid")
    if not legacy_run:
        expected_first_kind = (
            "completed_follow_up" if predecessor is not None else "initial"
        )
        if revisions[0].get("kind") != expected_first_kind or any(
            revision.get("kind") != "active_steering" for revision in revisions[1:]
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run revision kinds do not match lineage"
            )
    load_steering_ledger(run_dir, revisions)
    status = run_status(run_dir)
    handoff_text = read_handoff_text(run_dir)
    bound = latest
    if handoff_text is not None:
        handoff_last_invoked_at(run_dir)
        run_section = markdown_section(handoff_text, "Run")
        if handoff_field(run_section, "Run ID") != run_id:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff run ID does not match the run"
            )
        if handoff_field(run_section, "Prompt ID") != manifest.get("prompt_id"):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff prompt ID does not match the run"
            )
        handoff_manifest = Path(handoff_field(run_section, "Run manifest"))
        if (
            not handoff_manifest.is_absolute()
            or handoff_manifest.is_symlink()
            or handoff_manifest.resolve() != manifest_path.resolve()
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff manifest path does not match the run"
            )
        bound_revision = handoff_field(run_section, "Bound revision")
        bound_digest = handoff_field(run_section, "Bound SHA-256")
        matches = [
            revision
            for revision in revisions
            if revision.get("revision") == bound_revision
            and revision.get("sha256") == bound_digest
        ]
        if len(matches) != 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "handoff bound revision is not in the manifest"
            )
        bound = matches[0]
    if document is not None:
        if manifest.get("prompt_id") != document.prompt_id:
            raise PromptWorkspaceError(
                "PROMPT_CONFLICT", "run prompt ID does not match the source prompt"
            )
        bound_intent = str(bound.get("intent_sha256") or bound.get("sha256"))
        if bound_intent != document.intent_sha256:
            raise PromptWorkspaceError(
                "PROMPT_DRIFT", "editable prompt differs from the bound revision"
            )
    pending_steering = pending_steering_revisions(run_dir, revisions)
    return {
        "run_id": run_id,
        "prompt_id": manifest["prompt_id"],
        "source_path": source_path.name,
        "status": status,
        "revision": bound["revision"],
        "sha256": bound["sha256"],
        "intent_sha256": bound.get("intent_sha256") or bound["sha256"],
        "latest_revision": latest["revision"],
        "latest_sha256": latest["sha256"],
        "latest_intent_sha256": latest.get("intent_sha256") or latest["sha256"],
        "revision_kind": latest.get("kind") or "legacy",
        "predecessor": predecessor,
        "reconciliation_pending": bound["revision"] != latest["revision"],
        "pending_steering": pending_steering,
        "steering_pending": bool(pending_steering),
    }


def prompt_identity_and_digest(path: Path) -> tuple[str, str, str] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_PROMPT_BYTES:
            return None
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        frontmatter, _ = parse_frontmatter(text.splitlines())
        if frontmatter.get("schema") != PROMPT_SCHEMA:
            return None
        document = read_prompt(path, path.parent, require_content=False)
    except (OSError, UnicodeDecodeError, PromptWorkspaceError):
        return None
    return (
        frontmatter["prompt_id"],
        hashlib.sha256(raw).hexdigest(),
        document.intent_sha256,
    )


def verify_editable_source_digest(
    prompt_root: Path,
    source_name: str,
    prompt_id: str,
    bound_sha256: str,
    bound_intent_sha256: str,
) -> None:
    source = prompt_root / source_name
    identity_matches: list[tuple[Path, str, str]] = []
    for candidate in sorted(prompt_root.glob("*.md")):
        if candidate.name == HUB_FILENAME:
            continue
        identity = prompt_identity_and_digest(candidate)
        if identity is not None and identity[0] == prompt_id:
            identity_matches.append((candidate, identity[1], identity[2]))
    if not identity_matches:
        raise PromptWorkspaceError(
            "PROMPT_DRIFT",
            "editable prompt is missing, unsafe, or differs from the bound revision",
        )
    if len(identity_matches) != 1:
        raise PromptWorkspaceError(
            "PROMPT_CONFLICT", "prompt_id is duplicated in the workspace"
        )
    candidate, _digest, intent_digest = identity_matches[0]
    if (source.exists() or source.is_symlink()) and candidate != source:
        raise PromptWorkspaceError(
            "PROMPT_DRIFT",
            "recorded editable source no longer matches the bound prompt identity",
        )
    if not re.fullmatch(r"[0-9a-f]{64}", bound_sha256) or not re.fullmatch(
        r"[0-9a-f]{64}", bound_intent_sha256
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "bound prompt digest is invalid"
        )
    if intent_digest != bound_intent_sha256:
        raise PromptWorkspaceError(
            "PROMPT_DRIFT", "editable prompt differs from the bound revision"
        )
    require_mode(candidate, 0o600, "prompt file")


def verify_command(
    manifest_path: Path,
    prompt_path: Path | None,
    run_id: str | None,
) -> dict[str, object]:
    workspace = verify_workspace(manifest_path)
    document: PromptDocument | None = None
    if prompt_path is not None:
        prompt_root = Path(
            required_string(workspace, "prompt_root", "workspace manifest")
        )
        document = read_prompt(prompt_path, prompt_root, require_content=True)
        ensure_unique_prompt_id(document, prompt_root)
    result: dict[str, object] = {
        "workspace": str(manifest_path.expanduser().resolve()),
        "workspace_status": "valid",
    }
    if document is not None:
        result.update(
            {
                "prompt_id": document.prompt_id,
                "prompt_ref": document.prompt_ref,
                "prompt": str(document.path),
                "prompt_status": "valid",
            }
        )
    if run_id is not None:
        run_result = verify_run(workspace, run_id, document)
        if document is None:
            prompt_root = Path(
                required_string(workspace, "prompt_root", "workspace manifest")
            )
            verify_editable_source_digest(
                prompt_root,
                str(run_result["source_path"]),
                str(run_result["prompt_id"]),
                str(run_result["sha256"]),
                str(run_result["intent_sha256"]),
            )
        result["run"] = run_result
    return result


def prompt_rows(
    manifest_path: Path, query: str | None, date_value: str | None
) -> list[dict[str, object]]:
    workspace = verify_workspace(manifest_path)
    prompt_root = Path(required_string(workspace, "prompt_root", "workspace manifest"))
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    if date_value is not None:
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError as exc:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", "--date must use YYYY-MM-DD"
            ) from exc
    needle = query.casefold() if query else None
    all_runs = load_run_manifests(runs_root)
    verified_runs = {
        run_dir.name: verify_run(workspace, run_dir.name, None)
        for run_dir, _ in all_runs
    }
    activity = load_prompt_activity(runs_root.parent)
    queued = {
        str(item["prompt_id"]): index
        for index, item in enumerate(
            load_prompt_queue(runs_root.parent)["entries"], start=1
        )
    }
    rows: list[dict[str, object]] = []
    creation_by_path: dict[str, str] = {}
    for candidate in sorted(prompt_root.glob("*.md")):
        if candidate.name == HUB_FILENAME:
            continue
        try:
            document = read_prompt(candidate, prompt_root, require_content=False)
        except PromptWorkspaceError as exc:
            created_at = datetime.fromtimestamp(
                candidate.lstat().st_mtime, tz=timezone.utc
            )
            created_text = iso_seconds(created_at)
            searchable = candidate.name.casefold()
            if needle and needle not in searchable:
                continue
            if date_value and created_at.date().isoformat() != date_value:
                continue
            status = (
                "upgrade_required"
                if exc.code == "WORKFLOW_UPGRADE_REQUIRED"
                else "invalid"
            )
            path_text = str(candidate.absolute())
            creation_by_path[path_text] = created_text
            rows.append(
                {
                    "title": candidate.stem,
                    "last_invoked_at": created_text,
                    "status": status,
                    "path": path_text,
                }
            )
            continue
        creation_by_path[str(document.path)] = iso_seconds(document.created_at)
        searchable = " ".join(
            (document.title, document.path.name, document.sections.get("Ask", ""))
        ).casefold()
        if needle and needle not in searchable:
            continue
        if date_value and document.created_at.date().isoformat() != date_value:
            continue
        runs = [
            (run_dir, run_manifest)
            for run_dir, run_manifest in all_runs
            if run_manifest.get("prompt_id") == document.prompt_id
        ]
        status = "draft"
        queue_position = queued.get(document.prompt_id)
        last_invoked_at = activity.get(
            document.prompt_id,
            iso_seconds(document.created_at),
        )
        if runs:
            latest_dir, _ = runs[-1]
            verified = verified_runs[latest_dir.name]
            if verified["steering_pending"]:
                status = "steering_pending"
            elif verified["reconciliation_pending"]:
                status = "reconcile_pending"
            else:
                status = str(verified["status"])
            handoff_activity = handoff_last_invoked_at(latest_dir)
            if handoff_activity is not None and datetime.fromisoformat(
                handoff_activity
            ) > datetime.fromisoformat(last_invoked_at):
                last_invoked_at = handoff_activity
        if queue_position is not None:
            status = "queued"
        row: dict[str, object] = {
            "title": document.title,
            "prompt_ref": document.prompt_ref,
            "last_invoked_at": last_invoked_at,
            "status": status,
            "path": str(document.path),
        }
        if queue_position is not None:
            row["queue_position"] = queue_position
        rows.append(row)
    rows.sort(
        key=lambda row: (
            datetime.fromisoformat(str(row["last_invoked_at"])).timestamp(),
            datetime.fromisoformat(creation_by_path[str(row["path"])]).timestamp(),
            str(row["path"]),
        ),
        reverse=True,
    )
    return rows
