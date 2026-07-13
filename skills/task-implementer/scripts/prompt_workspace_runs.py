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
    MAX_PROMPT_BYTES,
    PROMPT_ID_RE,
    REVISION_RE,
    RUN_ID_RE,
    RUN_SCHEMA,
    TERMINAL_RUN_STATUSES,
    PromptDocument,
    PromptWorkspaceError,
    create_prompt,
    ensure_private_dir,
    ensure_unique_prompt_id,
    init_workspace,
    iso_seconds,
    load_json_object,
    now_local,
    now_utc,
    parse_frontmatter,
    private_chmod,
    read_prompt,
    require_mode,
    required_string,
    stable_json,
    verify_workspace,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_specs import (
    load_steering_ledger,
    pending_steering_revisions,
)


RUN_STATUSES = {
    "prepared",
    "running",
    "blocked",
    *TERMINAL_RUN_STATUSES,
}

STARTER_ASK = "Describe the implementation task"
ACTIVITY_SCHEMA = "task-implementer/activity-v1"


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


def initialize_project_workspace(
    project_path: Path,
    codex_home: Path,
    *,
    clock: Callable[[], datetime] = now_local,
    id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Initialize one exact project folder and ensure one starter prompt."""

    requested = project_path.expanduser().resolve()
    result = init_workspace(
        requested,
        str(requested),
        codex_home,
        clock=clock,
    )
    workspace_path = Path(str(result["workspace"]))
    prompt_root = Path(str(result["prompt_root"]))
    with scope_lock(workspace_path.parent):
        prompt_paths = sorted(prompt_root.glob("*.md"))
        starter_created = False
        if not prompt_paths:
            if id_factory is None:
                starter = create_prompt(
                    workspace_path,
                    STARTER_ASK,
                    clock=clock,
                )
            else:
                starter = create_prompt(
                    workspace_path,
                    STARTER_ASK,
                    clock=clock,
                    id_factory=id_factory,
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


def load_run_manifests(runs_root: Path, prompt_id: str | None = None) -> list[tuple[Path, dict[str, object]]]:
    results: list[tuple[Path, dict[str, object]]] = []
    for run_dir in sorted(runs_root.glob("run-*")):
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"run directory is unsafe: {run_dir.name}"
            )
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"run manifest is missing or unsafe: {run_dir.name}"
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
) -> dict[str, object]:
    return {
        "revision": revision_id,
        "created_at": iso_seconds(created_at),
        "sha256": document.sha256,
        "bytes": len(document.raw),
        "snapshot": f"inputs/{revision_id}/prompt.md",
    }


def generated_run_id(created_at: datetime) -> str:
    stamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp.lower()}-{uuid.uuid4().hex[:8]}"


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
    verified_runs = {
        existing_run.name: verify_run(workspace, existing_run.name, None)
        for existing_run, _ in all_runs
    }
    prior_runs = [
        (run_dir, manifest)
        for run_dir, manifest in all_runs
        if manifest.get("prompt_id") == document.prompt_id
    ]
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
        if manifest.get("schema") != RUN_SCHEMA or manifest.get("prompt_id") != document.prompt_id:
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
        if latest.get("sha256") == document.sha256:
            if status == "snapshot_only":
                return {
                    "run_id": run_id,
                    "revision": latest["revision"],
                    "prompt_id": document.prompt_id,
                    "sha256": document.sha256,
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
                    "sha256": document.sha256,
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
        try:
            write_exclusive(snapshot_path, document.raw)
            revisions.append(revision_record(revision_id, created_at, document))
            manifest["revisions"] = revisions
            write_atomic(manifest_file, stable_json(manifest))
        except Exception:
            shutil.rmtree(revision_dir, ignore_errors=True)
            raise
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
                    and active_latest.get("sha256") == document.sha256
                ):
                    return {
                        "run_id": active_run_id,
                        "revision": active_latest["revision"],
                        "prompt_id": document.prompt_id,
                        "sha256": document.sha256,
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
            if latest_revisions[-1].get("sha256") == document.sha256:
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
        manifest = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "project_id": workspace["project_id"],
            "scope_id": workspace["scope_id"],
            "prompt_id": document.prompt_id,
            "source_path": document.path.name,
            "created_at": iso_seconds(created_at),
            "revisions": [revision_record(revision_id, created_at, document)],
        }
        try:
            write_exclusive(snapshot_path, document.raw)
            write_exclusive(manifest_file, stable_json(manifest))
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    return {
        "run_id": run_id,
        "revision": revision_id,
        "prompt_id": document.prompt_id,
        "sha256": document.sha256,
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


def verify_run(
    workspace: dict[str, object],
    run_id: str,
    document: PromptDocument | None,
) -> dict[str, object]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run ID format is invalid")
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    run_dir = runs_root / run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run directory is missing or unsafe")
    require_mode(run_dir, 0o700, "run directory")
    require_inputs_directory(run_dir)
    manifest_path = run_dir / "manifest.json"
    require_mode(manifest_path, 0o600, "run manifest")
    manifest = load_json_object(manifest_path, "run manifest")
    if manifest.get("schema") != RUN_SCHEMA or manifest.get("run_id") != run_id:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run manifest identity is invalid")
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
                "RUN_STATE_INVALID", "snapshot path does not match the revision contract"
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
            or (previous_created_at is not None and revision_created_at < previous_created_at)
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"revision timestamp is invalid: {revision_id}"
            )
        previous_created_at = revision_created_at
        digest_value = revision.get("sha256")
        byte_count = revision.get("bytes")
        if (
            not isinstance(digest_value, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest_value) is None
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
    latest = revisions[-1]
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
        if bound.get("sha256") != document.sha256:
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
        "latest_revision": latest["revision"],
        "latest_sha256": latest["sha256"],
        "reconciliation_pending": bound["revision"] != latest["revision"],
        "pending_steering": pending_steering,
        "steering_pending": bool(pending_steering),
    }


def prompt_identity_and_digest(path: Path) -> tuple[str, str] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_PROMPT_BYTES:
            return None
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        frontmatter, _ = parse_frontmatter(text.splitlines())
    except (OSError, UnicodeDecodeError, PromptWorkspaceError):
        return None
    return frontmatter["prompt_id"], hashlib.sha256(raw).hexdigest()


def verify_editable_source_digest(
    prompt_root: Path,
    source_name: str,
    prompt_id: str,
    bound_sha256: str,
) -> None:
    source = prompt_root / source_name
    identity_matches: list[tuple[Path, str]] = []
    for candidate in sorted(prompt_root.glob("*.md")):
        identity = prompt_identity_and_digest(candidate)
        if identity is not None and identity[0] == prompt_id:
            identity_matches.append((candidate, identity[1]))
    if not identity_matches:
        raise PromptWorkspaceError(
            "PROMPT_DRIFT",
            "editable prompt is missing, unsafe, or differs from the bound revision",
        )
    if len(identity_matches) != 1:
        raise PromptWorkspaceError(
            "PROMPT_CONFLICT", "prompt_id is duplicated in the workspace"
        )
    candidate, digest = identity_matches[0]
    if (source.exists() or source.is_symlink()) and candidate != source:
        raise PromptWorkspaceError(
            "PROMPT_DRIFT",
            "recorded editable source no longer matches the bound prompt identity",
        )
    if digest != bound_sha256:
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
        prompt_root = Path(required_string(workspace, "prompt_root", "workspace manifest"))
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
            )
        result["run"] = run_result
    return result


def prompt_rows(manifest_path: Path, query: str | None, date_value: str | None) -> list[dict[str, object]]:
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
    rows: list[dict[str, object]] = []
    creation_by_path: dict[str, str] = {}
    for candidate in sorted(prompt_root.glob("*.md")):
        document = read_prompt(candidate, prompt_root, require_content=False)
        creation_by_path[str(document.path)] = iso_seconds(document.created_at)
        searchable = " ".join((document.title, document.path.name)).casefold()
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
            if (
                handoff_activity is not None
                and datetime.fromisoformat(handoff_activity)
                > datetime.fromisoformat(last_invoked_at)
            ):
                last_invoked_at = handoff_activity
        rows.append(
            {
                "title": document.title,
                "last_invoked_at": last_invoked_at,
                "status": status,
                "path": str(document.path),
            }
        )
    rows.sort(
        key=lambda row: (
            datetime.fromisoformat(str(row["last_invoked_at"])).timestamp(),
            datetime.fromisoformat(
                creation_by_path[str(row["path"])]
            ).timestamp(),
            str(row["path"]),
        ),
        reverse=True,
    )
    return rows
