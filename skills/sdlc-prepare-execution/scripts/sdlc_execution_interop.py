#!/usr/bin/env python3
"""Optional Agentic SDLC coordination with a managed outer worktree."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA = "agentic-sdlc/worktree-interop-v2"
SHA_RE = re.compile(r"[0-9a-f]{40,64}")
TOKEN_RE = re.compile(r"[0-9a-f]{32}")


class ExecutionInteropError(RuntimeError):
    pass


def _helper() -> Path:
    path = (
        Path(__file__).resolve().parents[2]
        / "worktree"
        / "scripts"
        / "worktree_manager.py"
    )
    if not path.is_file():
        raise ExecutionInteropError("managed outer worktree helper is unavailable")
    return path


def _call(cwd: Path, arguments: list[str]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [sys.executable, str(_helper()), *arguments],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        value: Any = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ExecutionInteropError("managed outer worktree helper failed") from exc
    if completed.returncode != 0 or not isinstance(value, dict):
        raise ExecutionInteropError("managed outer worktree coordination is blocked")
    return value


def _path(run_dir: Path) -> Path:
    return run_dir / "execution" / "interop.json"


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _valid_scope(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def load(run_dir: Path) -> dict[str, object] | None:
    path = _path(run_dir)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionInteropError("Agentic SDLC interop state is invalid") from exc
    required = {
        "schema",
        "mode",
        "run_id",
        "project_scope",
        "initial_head",
        "name",
        "lease_id",
        "promoted_head",
        "released",
        "outer_integration_status",
        "source_integration_head",
    }
    if (
        isinstance(value, dict)
        and value.get("schema") == "agentic-sdlc/worktree-interop-v1"
    ):
        raise ExecutionInteropError(
            "WORKFLOW_UPGRADE_REQUIRED: older Agentic SDLC interop is unsupported"
        )
    if not isinstance(value, dict) or set(value) != required:
        raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    promoted = value.get("promoted_head")
    if (
        value.get("schema") != SCHEMA
        or value.get("mode") not in {"managed", "unmanaged"}
        or value.get("run_id") != run_dir.name
        or not _valid_scope(value.get("project_scope"))
        or SHA_RE.fullmatch(str(value.get("initial_head") or "")) is None
        or (promoted is not None and SHA_RE.fullmatch(str(promoted)) is None)
        or not isinstance(value.get("released"), bool)
        or value.get("outer_integration_status")
        not in {"leased", "pending", "integrated", "not-required"}
        or (
            value.get("source_integration_head") is not None
            and SHA_RE.fullmatch(str(value["source_integration_head"])) is None
        )
    ):
        raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    if value["mode"] == "managed":
        if (
            not isinstance(value.get("name"), str)
            or not value["name"]
            or TOKEN_RE.fullmatch(str(value.get("lease_id") or "")) is None
            or (
                value["released"] is False
                and value["outer_integration_status"] != "leased"
            )
            or (
                value["released"] is True
                and value["outer_integration_status"] not in {"pending", "integrated"}
            )
            or (
                value["outer_integration_status"] == "integrated"
                and value["source_integration_head"] is None
            )
        ):
            raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    elif (
        any(value.get(key) is not None for key in ("name", "lease_id", "promoted_head"))
        or value["released"] is not True
        or value["outer_integration_status"] != "not-required"
        or value["source_integration_head"] is not None
    ):
        raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    return value


def _inspect_managed(
    run_dir: Path,
    selected_project_root: Path,
    state: dict[str, object],
) -> dict[str, object]:
    inspected = _call(
        selected_project_root,
        [
            "task-lease-inspect",
            "--owner-kind",
            "agentic-sdlc",
            "--name",
            str(state["name"]),
            "--lease-id",
            str(state["lease_id"]),
        ],
    )
    expected = {
        "owner_kind": "agentic-sdlc",
        "name": state["name"],
        "token": state["lease_id"],
        "run_id": run_dir.name,
        "workspace": str(run_dir.resolve()),
        "task_scope": state["project_scope"],
        "initial_head": state["initial_head"],
    }
    if any(inspected.get(key) != value for key, value in expected.items()):
        raise ExecutionInteropError("managed outer worktree lease identity changed")
    if inspected.get("state") not in {"active", "released"}:
        raise ExecutionInteropError("managed outer worktree lease state is invalid")
    if inspected.get("outer_clean") is not True:
        raise ExecutionInteropError("managed outer worktree must remain clean")
    return inspected


def _reconcile(
    run_dir: Path,
    selected_project_root: Path,
    state: dict[str, object],
) -> dict[str, object]:
    inspected = _inspect_managed(run_dir, selected_project_root, state)
    lease_promoted = inspected.get("promoted_head")
    local_promoted = state.get("promoted_head")
    repair_promoted = local_promoted is None and lease_promoted is not None
    if (
        lease_promoted is not None
        and local_promoted is not None
        and lease_promoted != local_promoted
    ):
        history = inspected.get("promotion_heads")
        if (
            not isinstance(history, list)
            or len(history) < 2
            or history[-2:] != [local_promoted, lease_promoted]
        ):
            raise ExecutionInteropError("managed outer promoted head changed")
        repair_promoted = True
    if local_promoted is not None and lease_promoted is None:
        raise ExecutionInteropError("local promotion is missing from the outer lease")
    expected_head = lease_promoted or state["initial_head"]
    if inspected.get("outer_head") != expected_head:
        raise ExecutionInteropError("managed outer Git head disagrees with its lease")
    released = inspected["state"] == "released"
    if state.get("released") is True and not released:
        raise ExecutionInteropError("local release is missing from the outer lease")
    changed = False
    if repair_promoted:
        state["promoted_head"] = lease_promoted
        changed = True
    if released and state.get("released") is False:
        state["released"] = True
        state["outer_integration_status"] = "pending"
        changed = True
    if changed:
        _write(_path(run_dir), state)
    return inspected


def acquire(
    run_dir: Path,
    selected_project_root: Path,
    project_scope: str,
    feature_base_head: str,
) -> dict[str, object]:
    existing = load(run_dir)
    if existing is not None:
        if existing["project_scope"] != project_scope:
            raise ExecutionInteropError("Agentic SDLC interop identity changed")
        anchor = _call(selected_project_root, ["anchor-inspect"])
        if existing["mode"] == "unmanaged":
            if anchor.get("status") != "unmanaged":
                raise ExecutionInteropError("managed outer worktree mode changed")
            return existing
        if (
            anchor.get("task_scope") != project_scope
            or anchor.get("name") != existing["name"]
        ):
            raise ExecutionInteropError(
                "managed outer worktree scope or identity changed"
            )
        inspected = _reconcile(run_dir, selected_project_root, existing)
        if inspected.get("outer_head") != feature_base_head:
            raise ExecutionInteropError(
                "managed outer feature base disagrees with its lease"
            )
        return existing
    anchor = _call(selected_project_root, ["anchor-inspect"])
    if anchor.get("status") == "unmanaged":
        state: dict[str, object] = {
            "schema": SCHEMA,
            "mode": "unmanaged",
            "run_id": run_dir.name,
            "project_scope": project_scope,
            "initial_head": feature_base_head,
            "name": None,
            "lease_id": None,
            "promoted_head": None,
            "released": True,
            "outer_integration_status": "not-required",
            "source_integration_head": None,
        }
    else:
        if (
            anchor.get("task_scope") != project_scope
            or anchor.get("head") != feature_base_head
        ):
            raise ExecutionInteropError("managed outer worktree scope or head changed")
        acquired = _call(
            selected_project_root,
            [
                "task-lease-acquire",
                "--owner-kind",
                "agentic-sdlc",
                "--workspace",
                str(run_dir.resolve()),
                "--run-id",
                run_dir.name,
                "--task-scope",
                project_scope,
                "--initial-head",
                feature_base_head,
            ],
        )
        expected_acquisition = {
            "owner_kind": "agentic-sdlc",
            "name": anchor.get("name"),
            "branch": anchor.get("branch"),
            "worktree": anchor.get("worktree"),
            "scope": anchor.get("scope"),
            "task_scope": project_scope,
            "run_id": run_dir.name,
            "workspace": str(run_dir.resolve()),
            "initial_head": feature_base_head,
        }
        if (
            acquired.get("state") != "active"
            or acquired.get("promoted_head") is not None
            or any(
                acquired.get(key) != value
                for key, value in expected_acquisition.items()
            )
        ):
            raise ExecutionInteropError("managed outer lease acquisition is invalid")
        state = {
            "schema": SCHEMA,
            "mode": "managed",
            "run_id": run_dir.name,
            "project_scope": project_scope,
            "initial_head": feature_base_head,
            "name": acquired["name"],
            "lease_id": acquired["token"],
            "promoted_head": acquired.get("promoted_head"),
            "released": False,
            "outer_integration_status": "leased",
            "source_integration_head": None,
        }
    _write(_path(run_dir), state)
    return state


def _managed_call(
    run_dir: Path, selected_project_root: Path, arguments: list[str]
) -> dict[str, object] | None:
    state = load(run_dir)
    if state is None or state.get("mode") != "managed":
        return None
    return _call(
        selected_project_root,
        [
            arguments[0],
            "--owner-kind",
            "agentic-sdlc",
            "--name",
            str(state["name"]),
            "--lease-id",
            str(state["lease_id"]),
            *arguments[1:],
        ],
    )


def record_resource(
    run_dir: Path,
    selected_project_root: Path,
    *,
    kind: str,
    path: Path,
    branch: str,
    state: str,
) -> None:
    _managed_call(
        run_dir,
        selected_project_root,
        [
            "task-lease-resource",
            "--kind",
            kind,
            "--path",
            str(path.absolute()),
            "--branch",
            branch,
            "--state",
            state,
        ],
    )


def record_promotion(
    run_dir: Path, selected_project_root: Path, promoted_head: str
) -> None:
    state = load(run_dir)
    if state is not None and state.get("mode") == "managed":
        if state.get("released") is True:
            raise ExecutionInteropError("released outer lease cannot be promoted")
        inspected = _inspect_managed(run_dir, selected_project_root, state)
        if inspected.get("state") != "active":
            raise ExecutionInteropError("released outer lease cannot be promoted")
        lease_promoted = inspected.get("promoted_head")
        history = inspected.get("promotion_heads")
        local_promoted = state.get("promoted_head")
        predecessor_matches = (
            lease_promoted == promoted_head
            and local_promoted not in {None, promoted_head}
            and isinstance(history, list)
            and len(history) >= 2
            and history[-2:] == [local_promoted, promoted_head]
        )
        if (
            local_promoted not in {None, lease_promoted, promoted_head}
            and not predecessor_matches
        ):
            raise ExecutionInteropError("managed outer promoted head changed")
        if (
            lease_promoted == promoted_head
            and local_promoted not in {None, promoted_head}
            and not predecessor_matches
        ):
            raise ExecutionInteropError(
                "managed outer promotion history is inconsistent"
            )
        if inspected.get("outer_head") != promoted_head:
            raise ExecutionInteropError("managed outer Git head is not the promotion")
        expected_head = (
            promoted_head
            if lease_promoted == promoted_head
            else lease_promoted or inspected.get("initial_head")
        )
        if not isinstance(expected_head, str):
            raise ExecutionInteropError("managed outer promotion baseline is invalid")
    else:
        expected_head = promoted_head
    result = _managed_call(
        run_dir,
        selected_project_root,
        [
            "task-lease-promote",
            "--promoted-head",
            promoted_head,
            "--expected-head",
            expected_head,
        ],
    )
    if result is None:
        return
    if result.get("state") != "active" or result.get("promoted_head") != promoted_head:
        raise ExecutionInteropError("outer lease did not record the exact promotion")
    assert state is not None
    state["promoted_head"] = promoted_head
    _write(_path(run_dir), state)


def release(
    run_dir: Path,
    selected_project_root: Path,
    promoted_head: str,
    *,
    final_alignment: str,
    uat: str,
    docs: str,
) -> dict[str, object]:
    if not all(value.strip() for value in (final_alignment, uat, docs)):
        raise ExecutionInteropError(
            "alignment, UAT, and documentation evidence are required"
        )
    state = load(run_dir)
    if state is None or state.get("mode") == "unmanaged":
        return {"status": "unmanaged"}
    if state.get("promoted_head") != promoted_head:
        raise ExecutionInteropError(
            "release head does not match the recorded outer promotion"
        )
    inspected = _reconcile(run_dir, selected_project_root, state)
    if inspected["state"] == "active":
        result = _managed_call(
            run_dir,
            selected_project_root,
            ["task-lease-release", "--promoted-head", promoted_head],
        )
        assert result is not None
    else:
        result = {**inspected, "status": "already-released"}
    if (
        result.get("state") != "released"
        or result.get("promoted_head") != promoted_head
    ):
        raise ExecutionInteropError("outer lease release receipt is inconsistent")
    state["released"] = True
    state["outer_integration_status"] = "pending"
    _write(_path(run_dir), state)
    return result


def reconcile_promotion(
    run_dir: Path,
    selected_project_root: Path,
    expected_head: str,
) -> dict[str, object]:
    """Repair an exact post-Git promotion crash window from durable lease truth."""

    state = load(run_dir)
    if state is None or state.get("mode") == "unmanaged":
        return {"status": "unmanaged"}
    inspected = _inspect_managed(run_dir, selected_project_root, state)
    lease_promoted = inspected.get("promoted_head")
    local_promoted = state.get("promoted_head")
    if lease_promoted not in {None, expected_head} or local_promoted not in {
        None,
        expected_head,
    }:
        raise ExecutionInteropError("managed outer promoted head changed")
    if inspected.get("outer_head") != expected_head:
        raise ExecutionInteropError(
            "managed outer Git head is not the expected promotion"
        )
    if lease_promoted is None:
        if inspected["state"] != "active":
            raise ExecutionInteropError("released outer lease is missing its promotion")
        result = _managed_call(
            run_dir,
            selected_project_root,
            [
                "task-lease-promote",
                "--promoted-head",
                expected_head,
                "--expected-head",
                str(inspected["initial_head"]),
            ],
        )
        assert result is not None
        if result.get("promoted_head") != expected_head:
            raise ExecutionInteropError("outer lease promotion repair failed")
        inspected = result
    if state.get("promoted_head") is None:
        state["promoted_head"] = expected_head
        _write(_path(run_dir), state)
    if inspected.get("state") == "released" and state.get("released") is False:
        state["released"] = True
        state["outer_integration_status"] = "pending"
        _write(_path(run_dir), state)
    return inspected


def inspect_anchor(selected_project_root: Path) -> dict[str, object]:
    return _call(selected_project_root, ["anchor-inspect"])


def complete_source_integration(
    run_dir: Path, selected_project_root: Path
) -> dict[str, object]:
    state = load(run_dir)
    if state is None or state.get("mode") != "managed":
        return {"status": "not-required"}
    if state.get("released") is not True:
        raise ExecutionInteropError("outer lease must be released before integration")
    inspected = _call(
        selected_project_root,
        ["inspect", "--name", str(state["name"]), "--require-clean"],
    )
    if (
        inspected.get("lifecycle_status") != "integrated"
        or inspected.get("head") != state.get("promoted_head")
        or SHA_RE.fullmatch(str(inspected.get("integration_head") or "")) is None
        or inspected.get("source_contains_integration") is not True
    ):
        raise ExecutionInteropError("exact outer source integration proof is missing")
    if (
        state.get("outer_integration_status") == "integrated"
        and state.get("source_integration_head") != inspected["integration_head"]
    ):
        raise ExecutionInteropError("recorded outer source integration proof changed")
    state["outer_integration_status"] = "integrated"
    state["source_integration_head"] = inspected["integration_head"]
    _write(_path(run_dir), state)
    return {
        "status": "integrated",
        "source_integration_head": state["source_integration_head"],
    }
