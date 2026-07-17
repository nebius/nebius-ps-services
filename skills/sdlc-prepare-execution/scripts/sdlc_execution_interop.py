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


SCHEMA = "agentic-sdlc/worktree-interop-v1"
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
    }
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
    ):
        raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    if value["mode"] == "managed":
        if (
            not isinstance(value.get("name"), str)
            or not value["name"]
            or TOKEN_RE.fullmatch(str(value.get("lease_id") or "")) is None
        ):
            raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    elif (
        any(value.get(key) is not None for key in ("name", "lease_id", "promoted_head"))
        or value["released"] is not True
    ):
        raise ExecutionInteropError("Agentic SDLC interop state is invalid")
    return value


def acquire(
    run_dir: Path,
    selected_project_root: Path,
    project_scope: str,
    initial_head: str,
) -> dict[str, object]:
    existing = load(run_dir)
    if existing is not None:
        if (
            existing["project_scope"] != project_scope
            or existing["initial_head"] != initial_head
        ):
            raise ExecutionInteropError("Agentic SDLC interop identity changed")
        anchor = _call(selected_project_root, ["anchor-inspect"])
        if existing["mode"] == "unmanaged":
            if anchor.get("status") != "unmanaged":
                raise ExecutionInteropError("managed outer worktree mode changed")
            return existing
        expected_head = existing.get("promoted_head") or initial_head
        if (
            anchor.get("task_scope") != project_scope
            or anchor.get("head") != expected_head
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
                initial_head,
            ],
        )
        if (
            acquired.get("name") != existing["name"]
            or acquired.get("token") != existing["lease_id"]
        ):
            raise ExecutionInteropError("managed outer worktree lease changed")
        return existing
    anchor = _call(selected_project_root, ["anchor-inspect"])
    if anchor.get("status") == "unmanaged":
        state: dict[str, object] = {
            "schema": SCHEMA,
            "mode": "unmanaged",
            "run_id": run_dir.name,
            "project_scope": project_scope,
            "initial_head": initial_head,
            "name": None,
            "lease_id": None,
            "promoted_head": None,
            "released": True,
        }
    else:
        if (
            anchor.get("task_scope") != project_scope
            or anchor.get("head") != initial_head
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
                initial_head,
            ],
        )
        state = {
            "schema": SCHEMA,
            "mode": "managed",
            "run_id": run_dir.name,
            "project_scope": project_scope,
            "initial_head": initial_head,
            "name": acquired["name"],
            "lease_id": acquired["token"],
            "promoted_head": acquired.get("promoted_head"),
            "released": False,
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
    result = _managed_call(
        run_dir,
        selected_project_root,
        ["task-lease-promote", "--promoted-head", promoted_head],
    )
    if result is None:
        return
    state = load(run_dir)
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
    if state.get("released") is True:
        return {"status": "already-released"}
    result = _managed_call(
        run_dir,
        selected_project_root,
        ["task-lease-release", "--promoted-head", promoted_head],
    )
    assert result is not None
    state["released"] = True
    _write(_path(run_dir), state)
    return result
