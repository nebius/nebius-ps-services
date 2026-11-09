#!/usr/bin/env python3
"""Private coordinator entry point for first SDLC phase publication only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / "worktree/scripts"
sys.path.insert(0, str(SCRIPTS))
# The normal owner import loads its verified runtime before identity helpers.
from workflow_intake import agent_home, native_session_id  # noqa: E402
import workflow_objectives as objectives  # noqa: E402
from workflow_state import WorkflowError, digest, directory, lock, read, stable, write  # noqa: E402

OWNER = "agentic-sdlc"
INITIAL_SCHEMA = "agentic-sdlc/initial-phase-v1"
TELEMETRY = {"hook-events.jsonl", "continuation-state.json"}
RETRIES = (
    "requirements", "context", "design", "sdlc-auto-steering", "plan",
    "execution_preparation", "sdlc-tdd", "implementation", "validation", "test",
    "evaluation", "failure_classification", "troubleshoot", "sdlc-update-documents",
    "align", "sdlc-commit", "uat", "create-pr", "review-pr", "sdlc-merge-pr",
)
CHECKPOINT = re.compile(r"checkpoint-([0-9]{4,8})\.json\Z")


def _blocked():
    raise WorkflowError("RECOVERY_REQUIRED", "existing or unsafe phase evidence requires coordinator reconciliation")


def _entries(path):
    if not os.path.lexists(path):
        return {}
    with directory(path) as parent:
        result = {}
        with os.scandir(parent) as entries:
            for item in entries:
                if len(result) >= 128:
                    _blocked()
                result[item.name] = item.stat(follow_symlinks=False)
        return result


def _regular(metadata):
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
            or metadata.st_size > 4 * 1024 * 1024):
        _blocked()


def _history_bytes(current):
    return ("# Initial phase checkpoint\n\n"
            f"Checkpoint: {current['checkpoint_id']}\n\n"
            f"State SHA-256: {digest(current)}\n").encode()


def _history_name(current):
    return "iteration-" + current["checkpoint_id"].removeprefix("checkpoint-") + ".md"


def _read_history(path):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            _regular(before)
            if before.st_size > 512:
                _blocked()
            value = os.read(fd, 513)
            after = os.fstat(fd)
            bound = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            def identity(row):
                return (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if identity(before) != identity(after) or identity(after) != identity(bound):
                _blocked()
            return value
        finally:
            os.close(fd)


def _write_history(run_dir, current):
    with directory(run_dir / "history", create=True) as parent:
        fd = os.open(_history_name(current), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(_history_bytes(current))
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)


def _state(run, objective, checkpoint_id, timestamp):
    return {
        "state_version": 2, "project_id": run["project_id"], "run_id": run["run_id"],
        "iteration": 0, "checkpoint_id": checkpoint_id, "current_execution": None,
        "current_phase": "sdlc-start", "status": "running", "blocked_reason": None,
        "needs_human": False, "iteration_count": 0, "max_iterations": 200,
        "retry_counts": dict.fromkeys(RETRIES, 0), "last_successful_phase": None,
        "next_recommended_skill": "sdlc-create-requirements", "repair": None,
        "execution": None, "updated_at": timestamp,
        "initialization": {
            "schema": INITIAL_SCHEMA, "objective_id": objective["objective_id"],
            "objective_sha256": objective["revisions"][0]["sha256"],
            "source_head": run["source_head"],
            "coordinator_session_sha256": run["coordinator_session_sha256"],
        },
    }


def _checkpoint(current):
    return {**current, "created_at": current["updated_at"], "fingerprint_ids": [],
            "evidence": dict.fromkeys(("context", "plan", "execution", "validation", "tests",
                                       "evaluation", "documents", "uat", "pr", "review", "merge"))}


def _pointer(current):
    return {"checkpoint_id": current["checkpoint_id"],
            "path": "checkpoints/" + current["checkpoint_id"] + ".json",
            "created_at": current["updated_at"]}


def _admit(project, home, session, run_dir):
    bound = objectives.observe_binding(home, OWNER, session, project)
    if bound is None or bound[0] != run_dir:
        raise WorkflowError("OWNERSHIP_INVALID", "the selected native binding changed")
    run, objective = objectives.authorize_coordinator(run_dir, session, owner=OWNER)
    root, context, _, _ = objectives.project_context(project, home, OWNER)
    if (root != run_dir.parent or read(root / "project.json") != context
            or any(run.get(key) != value for key, value in context.items() if key != "schema")
            or read(root / "active-run.json") != {"schema": "workflow/active-run-v1", "run_id": run_dir.name}):
        raise WorkflowError("OWNERSHIP_INVALID", "active project registration disagrees with the native binding")
    ownership = read(root / "active.lock")
    expected = {
        "schema": "workflow/coordinator-ownership-v1", "owner": OWNER,
        "project_id": run["project_id"], "project_root": run["project_root"],
        "run_id": run_dir.name, "status": run["status"],
        "owner_session_hash": objectives.session_hash(session),
    }
    if (set(ownership) != set(expected) | {"updated_at"}
            or any(ownership.get(key) != value for key, value in expected.items())):
        raise WorkflowError("OWNERSHIP_INVALID", "active coordinator ownership disagrees with the native binding")
    if run["status"] != "accepted" or run.get("fence") is not None or len(objective["revisions"]) != 1:
        _blocked()
    return run, objective


def _admit_registration_only(run_dir, run, objective):
    allowed = {"run.json", "objective.json", "objectives", "git-promotion.json",
               "history", "checkpoints", "current-state.json", "execution"}
    entries = _entries(run_dir)
    if set(entries) - allowed:
        _blocked()
    for name, metadata in entries.items():
        if name not in {"objectives", "history", "checkpoints", "execution"}:
            _regular(metadata)
    if set(_entries(run_dir / "objectives")) != {Path(objective["revisions"][0]["snapshot"]).name}:
        _blocked()
    from workflow_interop import ExecutionInteropError, inspect_anchor, load, validate_contract_lease
    try:
        anchor = inspect_anchor(Path(run["project_root"]))
        if anchor.get("status") not in {"managed", "unmanaged"}:
            _blocked()
        if anchor["status"] == "managed":
            validate_contract_lease(run_dir, Path(run["project_root"]), run["source_head"])
        elif "execution" in entries:
            _blocked()
    except ExecutionInteropError as error:
        raise WorkflowError("OWNERSHIP_INVALID", "the managed outer lease is missing or no longer active") from error
    if "execution" in entries:
        # Managed intake reserves its outer lease before any phase exists.
        if set(_entries(run_dir / "execution")) != {"interop.json"}:
            _blocked()
        interop = load(run_dir)
        if (interop is None or interop["mode"] != "managed" or interop["released"]
                or interop["promoted_head"] is not None or interop["source_integration_head"] is not None
                or interop["initial_head"] != run["source_head"]):
            _blocked()


def _initial_snapshots(run_dir, run, objective):
    snapshots = {}
    entries = _entries(run_dir / "checkpoints")
    if len(entries) > 33:
        _blocked()
    for name, metadata in entries.items():
        _regular(metadata)
        if name == "latest.json":
            continue
        match = CHECKPOINT.fullmatch(name)
        if match is None or int(match[1]) < 1:
            _blocked()
        checkpoint = read(run_dir / "checkpoints" / name)
        timestamp = checkpoint.get("updated_at")
        if not isinstance(timestamp, str) or len(timestamp) > 64:
            _blocked()
        try:
            if datetime.fromisoformat(timestamp).tzinfo is None:
                _blocked()
        except ValueError:
            _blocked()
        expected = _state(run, objective, name.removesuffix(".json"), timestamp)
        if stable(checkpoint) != stable(_checkpoint(expected)) or int(match[1]) in snapshots:
            _blocked()
        snapshots[int(match[1])] = expected
    for path, values in (
        (run_dir / "checkpoints/latest.json", [_pointer(v) for v in snapshots.values()]),
        (run_dir / "current-state.json", list(snapshots.values())),
    ):
        if os.path.lexists(path):
            observed = stable(read(path))
            if not any(observed == stable(v) for v in values):
                _blocked()
    expected_history = {_history_name(v): _history_bytes(v) for v in snapshots.values()}
    complete_history = set()
    for name, metadata in _entries(run_dir / "history").items():
        _regular(metadata)
        if name in TELEMETRY:
            continue  # Audit telemetry is preserved, never used as phase authority.
        if name not in expected_history:
            _blocked()
        observed = _read_history(run_dir / "history" / name)
        if observed == expected_history[name]:
            complete_history.add(name)
        elif not expected_history[name].startswith(observed):
            _blocked()
    return snapshots, complete_history


def initialize(project: Path) -> dict:
    """Use the actual selected home and native identity; never infer an active run."""
    project = project.expanduser().absolute()
    home, session = agent_home(), native_session_id()
    if not session:
        raise WorkflowError("NATIVE_IDENTITY_REQUIRED", "a bound native coordinator is required")
    bound = objectives.observe_binding(home, OWNER, session, project)
    if bound is None:
        raise WorkflowError("OWNERSHIP_INVALID", "explicit objective intake or continuation is required")
    run_dir = bound[0]
    with lock(run_dir.parent):
        run, objective = _admit(project, home, session, run_dir)
        _admit_registration_only(run_dir, run, objective)
        snapshots, history = _initial_snapshots(run_dir, run, objective)
        last = max(snapshots, default=0)
        if last:
            current = snapshots[last]
            if (_history_name(current) in history
                    and all(os.path.lexists(path) and stable(read(path)) == stable(value)
                            for path, value in ((run_dir / "checkpoints/latest.json", _pointer(current)),
                                                (run_dir / "current-state.json", current)))):
                return {"status": "ALREADY_INITIALIZED", "run_id": run_dir.name,
                        "checkpoint_id": current["checkpoint_id"]}
        if last >= 32:
            _blocked()
        current = _state(run, objective, f"checkpoint-{last + 1:04d}",
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
        write(run_dir / "checkpoints" / (current["checkpoint_id"] + ".json"), _checkpoint(current), exclusive=True)
        write(run_dir / "checkpoints/latest.json", _pointer(current))
        write(run_dir / "current-state.json", current)
        _write_history(run_dir, current)
        return {"status": "RECOVERED" if snapshots else "INITIALIZED", "run_id": run_dir.name,
                "checkpoint_id": current["checkpoint_id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = initialize(args.project_root)
    except (WorkflowError, OSError, ValueError, RuntimeError) as error:
        # Do not reflect state, paths, exception text or traceback into diagnostics.
        code = error.code if isinstance(error, WorkflowError) else "INITIALIZATION_BLOCKED"
        result = {"status": "BLOCKED", "code": code,
                  "message": "Initialization stopped; preserve partial writes and reconcile native ownership and startup evidence."}
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
