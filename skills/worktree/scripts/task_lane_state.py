#!/usr/bin/env python3
"""Durable private state for persistent Task Implementer project lanes."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

from worktree_state import checked_state_directory, fsync_directory


LANE_SCHEMA = 1
LANE_KIND = "task-implementer-lane"
GENERATION_SCHEMA = 1
GENERATION_KIND = "task-implementer-generation"
CHECKPOINT_SCHEMA = 1
CHECKPOINT_KIND = "task-implementer-lane-checkpoint"
CHECKPOINT_STATES = {"prepared", "staged", "committed", "review-required"}
LANE_STATES = {
    "creating",
    "idle",
    "active",
    "pending",
    "integrating",
    "conflicted",
    "source-promoted",
    "removing",
    "removed",
    "recovery",
}
CLAIM_KINDS = {"exact", "prefix", "domain"}
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40,64}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
LANE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NAME_RE = re.compile(r"^project-[a-z0-9](?:[a-z0-9-]{0,86}[a-z0-9])?$")


class TaskLaneStateError(RuntimeError):
    """Task lane state is absent, malformed, or inconsistent."""


def _private_dir(path: Path, *, create: bool = True) -> None:
    if path.is_symlink():
        raise TaskLaneStateError(f"task lane directory must not be a symlink: {path}")
    if path.exists():
        if not path.is_dir() or path.resolve(strict=True) != path:
            raise TaskLaneStateError(f"task lane directory must be canonical: {path}")
    elif create:
        try:
            path.mkdir(parents=False, mode=0o700)
            fsync_directory(path.parent)
        except OSError as error:
            raise TaskLaneStateError(
                f"could not create task lane directory: {path}"
            ) from error
    if create:
        try:
            path.chmod(0o700)
        except OSError as error:
            raise TaskLaneStateError(
                f"could not secure task lane directory: {path}"
            ) from error


def _root(primary: Path, *, create: bool = False) -> Path:
    try:
        state = checked_state_directory(primary, create=create)
    except RuntimeError as error:
        raise TaskLaneStateError(str(error)) from error
    root = state / "task-lanes"
    _private_dir(root, create=create)
    return root


def lane_path(primary: Path, lane_id: str) -> Path:
    if LANE_ID_RE.fullmatch(lane_id) is None:
        raise TaskLaneStateError("task lane id is invalid")
    return _root(primary) / f"{lane_id}.json"


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    _private_dir(path.parent)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        fsync_directory(path.parent)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise TaskLaneStateError(
            f"could not persist task lane state: {path}"
        ) from error


def _load_json(path: Path, label: str) -> dict[str, object] | None:
    if path.is_symlink():
        raise TaskLaneStateError(f"{label} must not be a symlink: {path}")
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaskLaneStateError(f"{label} is unreadable or invalid: {path}") from error
    if not isinstance(value, dict):
        raise TaskLaneStateError(f"{label} must contain an object: {path}")
    return value


def _absolute(value: object, label: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise TaskLaneStateError(f"{label} must be absolute")
    path = Path(value)
    if os.path.normpath(value) != value:
        raise TaskLaneStateError(f"{label} must be normalized")
    return str(path)


def _scope(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TaskLaneStateError("task lane scope is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise TaskLaneStateError("task lane scope is invalid")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or OBJECT_ID_RE.fullmatch(value) is None:
        raise TaskLaneStateError(f"{label} is invalid")
    return value


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise TaskLaneStateError(f"{label} is invalid")
    return value


def _claims(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TaskLaneStateError("task lane claims are invalid")
    claims: list[dict[str, object]] = []
    identities: set[tuple[str, str, int]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "path", "generation"}:
            raise TaskLaneStateError("task lane claim fields are invalid")
        kind = item.get("kind")
        path_value = item.get("path")
        generation = item.get("generation")
        if kind not in CLAIM_KINDS or not isinstance(path_value, str) or not path_value:
            raise TaskLaneStateError("task lane claim is invalid")
        if kind != "domain":
            path = PurePosixPath(path_value)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != path_value
            ):
                raise TaskLaneStateError("task lane path claim is invalid")
        if not isinstance(generation, int) or generation < 1:
            raise TaskLaneStateError("task lane claim generation is invalid")
        identity = (str(kind), path_value, generation)
        if identity in identities:
            raise TaskLaneStateError("task lane claims repeat")
        identities.add(identity)
        claims.append({"kind": str(kind), "path": path_value, "generation": generation})
    return claims


def validate_lane(value: dict[str, object], lane_id: str) -> dict[str, object]:
    required = {
        "schema",
        "kind",
        "lane_id",
        "incarnation",
        "state",
        "primary",
        "common_dir",
        "source_branch",
        "source_ref",
        "scope",
        "name",
        "branch",
        "worktree",
        "base_head",
        "lane_head",
        "latest_generation",
        "last_integrated_generation",
        "active_generation",
        "pending_generations",
        "claims",
        "integration",
        "removal",
    }
    if set(value) != required or value.get("schema") != LANE_SCHEMA:
        raise TaskLaneStateError("task lane fields or schema are invalid")
    if value.get("kind") != LANE_KIND or value.get("lane_id") != lane_id:
        raise TaskLaneStateError("task lane identity is invalid")
    if LANE_ID_RE.fullmatch(lane_id) is None:
        raise TaskLaneStateError("task lane id is invalid")
    incarnation = value.get("incarnation")
    state = value.get("state")
    if not isinstance(incarnation, int) or incarnation < 1 or state not in LANE_STATES:
        raise TaskLaneStateError("task lane lifecycle state is invalid")
    name = value.get("name")
    branch = value.get("branch")
    if (
        not isinstance(name, str)
        or NAME_RE.fullmatch(name) is None
        or branch != f"feature/{name.removeprefix('project-')}"
    ):
        raise TaskLaneStateError("task lane branch identity is invalid")
    source_branch = value.get("source_branch")
    source_ref = value.get("source_ref")
    if (
        not isinstance(source_branch, str)
        or not source_branch
        or source_ref != f"refs/heads/{source_branch}"
    ):
        raise TaskLaneStateError("task lane source ref is invalid")
    latest = value.get("latest_generation")
    integrated = value.get("last_integrated_generation")
    pending = value.get("pending_generations")
    if (
        not isinstance(latest, int)
        or not isinstance(integrated, int)
        or integrated < 0
        or latest < integrated
        or not isinstance(pending, list)
        or any(not isinstance(item, int) or item < 1 for item in pending)
        or pending != list(range(integrated + 1, latest + 1))
    ):
        raise TaskLaneStateError("task lane generation range is invalid")
    active = value.get("active_generation")
    if active is not None:
        if not isinstance(active, dict) or set(active) != {
            "generation",
            "run_id",
            "token",
            "workspace",
            "initial_head",
        }:
            raise TaskLaneStateError("active task lane generation is invalid")
        if (
            active.get("generation") != latest + 1
            or not isinstance(active.get("run_id"), str)
            or RUN_ID_RE.fullmatch(str(active["run_id"])) is None
            or not isinstance(active.get("token"), str)
            or TOKEN_RE.fullmatch(str(active["token"])) is None
        ):
            raise TaskLaneStateError("active task lane generation identity is invalid")
        _absolute(active.get("workspace"), "active task lane workspace")
        _sha(active.get("initial_head"), "active task lane initial head")
    if state == "active" and active is None:
        raise TaskLaneStateError("active task lane is missing its generation")
    if state != "active" and active is not None:
        raise TaskLaneStateError("inactive task lane retains an active generation")
    claims = _claims(value.get("claims"))
    allowed_claim_generations = set(pending)
    if active is not None:
        allowed_claim_generations.add(int(active["generation"]))
    if any(int(item["generation"]) not in allowed_claim_generations for item in claims):
        raise TaskLaneStateError("task lane claim is outside the pending range")
    integration = value.get("integration")
    if integration is not None:
        if not isinstance(integration, dict) or set(integration) != {
            "first_generation",
            "last_generation",
            "source_head",
            "child_head",
            "candidate_head",
            "candidate_worktree",
            "phase",
        }:
            raise TaskLaneStateError("task lane integration journal is invalid")
        if (
            integration.get("first_generation") != integrated + 1
            or integration.get("last_generation") != latest
            or integration.get("phase")
            not in {
                "planned",
                "no-change",
                "conflicted",
                "candidate-ready",
                "source-promoted",
            }
        ):
            raise TaskLaneStateError("task lane integration range is invalid")
        _sha(integration.get("source_head"), "task lane integration source head")
        _sha(integration.get("child_head"), "task lane integration child head")
        candidate = integration.get("candidate_head")
        candidate_path = integration.get("candidate_worktree")
        if candidate is not None:
            _sha(candidate, "task lane integration candidate head")
        if candidate_path is not None:
            _absolute(candidate_path, "task lane integration candidate worktree")
    removal = value.get("removal")
    if removal is not None:
        if not isinstance(removal, dict) or set(removal) != {
            "head",
            "source_ref",
            "source_head",
            "phase",
        }:
            raise TaskLaneStateError("task lane removal intent is invalid")
        _sha(removal.get("head"), "task lane removal head")
        if removal.get("source_ref") != source_ref:
            raise TaskLaneStateError("task lane removal source ref is invalid")
        _sha(removal.get("source_head"), "task lane removal source head")
        if removal.get("phase") not in {
            "planned",
            "worktree-removed",
            "branch-removed",
        }:
            raise TaskLaneStateError("task lane removal phase is invalid")
    no_generation_work = (
        active is None and not pending and not claims and integration is None
    )
    if state in {"creating", "recovery"} and (
        latest != integrated or not no_generation_work or removal is not None
    ):
        raise TaskLaneStateError("task lane creation recovery shape is invalid")
    if state == "idle" and (
        latest != integrated or not no_generation_work or removal is not None
    ):
        raise TaskLaneStateError("idle task lane retains lifecycle work")
    if state == "pending" and (
        not pending
        or active is not None
        or integration is not None
        or removal is not None
    ):
        raise TaskLaneStateError("pending task lane shape is invalid")
    if state == "active" and (integration is not None or removal is not None):
        raise TaskLaneStateError("active task lane retains another transition")
    integration_phase = integration.get("phase") if integration is not None else None
    expected_integration_phases = {
        "integrating": {"planned", "no-change", "candidate-ready"},
        "conflicted": {"conflicted"},
        "source-promoted": {"source-promoted"},
    }
    if state in expected_integration_phases and (
        not pending
        or active is not None
        or removal is not None
        or integration_phase not in expected_integration_phases[state]
    ):
        raise TaskLaneStateError("task lane integration state is inconsistent")
    if state == "removing" and (
        latest != integrated or not no_generation_work or removal is None
    ):
        raise TaskLaneStateError("removing task lane shape is invalid")
    if state == "removed" and (
        latest != integrated or not no_generation_work or removal is not None
    ):
        raise TaskLaneStateError("removed task lane shape is invalid")
    return {
        **value,
        "primary": _absolute(value.get("primary"), "task lane primary"),
        "common_dir": _absolute(value.get("common_dir"), "task lane common dir"),
        "worktree": _absolute(value.get("worktree"), "task lane worktree"),
        "scope": _scope(value.get("scope")),
        "base_head": _sha(value.get("base_head"), "task lane base head"),
        "lane_head": _sha(value.get("lane_head"), "task lane head"),
        "claims": claims,
    }


def load_lane(
    primary: Path, lane_id: str, *, required: bool = True
) -> dict[str, object] | None:
    value = _load_json(lane_path(primary, lane_id), "task lane state")
    if value is None:
        if required:
            raise TaskLaneStateError("task lane state is missing")
        return None
    return validate_lane(value, lane_id)


def write_lane(primary: Path, value: dict[str, object]) -> Path:
    lane_id = str(value.get("lane_id", ""))
    validated = validate_lane(value, lane_id)
    path = _root(primary, create=True) / f"{lane_id}.json"
    _atomic_json(path, validated)
    return path


def all_lanes(primary: Path) -> list[dict[str, object]]:
    root = _root(primary)
    if not root.exists():
        return []
    lanes: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")):
        value = _load_json(path, "task lane state")
        if value is None:
            continue
        lanes.append(validate_lane(value, path.stem))
    return lanes


def checkpoint_path(primary: Path, lane_id: str, *, create: bool = False) -> Path:
    if LANE_ID_RE.fullmatch(lane_id) is None:
        raise TaskLaneStateError("task lane checkpoint identity is invalid")
    root = _root(primary, create=create) / "checkpoints"
    _private_dir(root, create=create)
    return root / f"{lane_id}.json"


def _checkpoint_claims(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TaskLaneStateError("task lane checkpoint claims are invalid")
    claims: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "path"}:
            raise TaskLaneStateError("task lane checkpoint claim fields are invalid")
        kind = item.get("kind")
        path_value = item.get("path")
        if kind not in CLAIM_KINDS or not isinstance(path_value, str) or not path_value:
            raise TaskLaneStateError("task lane checkpoint claim is invalid")
        if kind != "domain":
            path = PurePosixPath(path_value)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != path_value
            ):
                raise TaskLaneStateError("task lane checkpoint path claim is invalid")
        identity = (str(kind), path_value)
        if identity in identities:
            raise TaskLaneStateError("task lane checkpoint claims repeat")
        identities.add(identity)
        claims.append({"kind": str(kind), "path": path_value})
    return claims


def validate_checkpoint(value: dict[str, object], lane_id: str) -> dict[str, object]:
    required = {
        "schema",
        "kind",
        "state",
        "lane_id",
        "name",
        "branch",
        "worktree",
        "workspace",
        "run_id",
        "task_scope",
        "before_head",
        "initial_index_tree",
        "status_sha256",
        "candidate_tree",
        "changed_paths",
        "claims",
        "token",
        "commit_head",
        "commit_tree",
    }
    if (
        set(value) != required
        or value.get("schema") != CHECKPOINT_SCHEMA
        or value.get("kind") != CHECKPOINT_KIND
        or value.get("lane_id") != lane_id
        or value.get("state") not in CHECKPOINT_STATES
    ):
        raise TaskLaneStateError("task lane checkpoint fields or schema are invalid")
    name = value.get("name")
    branch = value.get("branch")
    if (
        not isinstance(name, str)
        or NAME_RE.fullmatch(name) is None
        or branch != f"feature/{name.removeprefix('project-')}"
    ):
        raise TaskLaneStateError("task lane checkpoint branch identity is invalid")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise TaskLaneStateError("task lane checkpoint run identity is invalid")
    changed = value.get("changed_paths")
    if not isinstance(changed, list) or any(
        not isinstance(item, str)
        or not item
        or PurePosixPath(item).is_absolute()
        or ".." in PurePosixPath(item).parts
        or PurePosixPath(item).as_posix() != item
        for item in changed
    ):
        raise TaskLaneStateError("task lane checkpoint changed paths are invalid")
    if changed != sorted(set(changed)):
        raise TaskLaneStateError("task lane checkpoint changed paths are not canonical")
    status_sha256 = value.get("status_sha256")
    if (
        not isinstance(status_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", status_sha256) is None
    ):
        raise TaskLaneStateError("task lane checkpoint status digest is invalid")
    state = str(value["state"])
    commit_head = value.get("commit_head")
    commit_tree = value.get("commit_tree")
    if state in {"committed", "review-required"}:
        _sha(commit_head, "task lane checkpoint commit head")
        _sha(commit_tree, "task lane checkpoint commit tree")
    elif commit_head is not None or commit_tree is not None:
        raise TaskLaneStateError("uncommitted task lane checkpoint has commit evidence")
    if state == "committed" and commit_tree != value.get("candidate_tree"):
        raise TaskLaneStateError("committed task lane checkpoint tree is inconsistent")
    claim_identities = {
        (str(item["kind"]), str(item["path"]))
        for item in _checkpoint_claims(value.get("claims"))
    }
    if not {("exact", item) for item in changed}.issubset(claim_identities):
        raise TaskLaneStateError("task lane checkpoint changed paths lack exact claims")
    return {
        **value,
        "worktree": _absolute(value.get("worktree"), "task lane checkpoint worktree"),
        "workspace": _absolute(
            value.get("workspace"), "task lane checkpoint workspace"
        ),
        "task_scope": _scope(value.get("task_scope")),
        "before_head": _sha(value.get("before_head"), "task lane checkpoint base"),
        "initial_index_tree": _sha(
            value.get("initial_index_tree"), "task lane checkpoint initial index"
        ),
        "candidate_tree": _sha(
            value.get("candidate_tree"), "task lane checkpoint candidate tree"
        ),
        "changed_paths": list(changed),
        "claims": _checkpoint_claims(value.get("claims")),
        "token": _token(value.get("token"), "task lane checkpoint token"),
    }


def load_checkpoint(
    primary: Path, lane_id: str, *, required: bool = False
) -> dict[str, object] | None:
    value = _load_json(
        checkpoint_path(primary, lane_id), "task lane checkpoint journal"
    )
    if value is None:
        if required:
            raise TaskLaneStateError("task lane checkpoint journal is missing")
        return None
    return validate_checkpoint(value, lane_id)


def write_checkpoint(primary: Path, value: dict[str, object]) -> Path:
    lane_id = str(value.get("lane_id", ""))
    validated = validate_checkpoint(value, lane_id)
    path = checkpoint_path(primary, lane_id, create=True)
    _atomic_json(path, validated)
    return path


def delete_checkpoint(primary: Path, lane_id: str) -> None:
    path = checkpoint_path(primary, lane_id)
    try:
        path.unlink()
        fsync_directory(path.parent)
    except FileNotFoundError:
        return
    except OSError as error:
        raise TaskLaneStateError(
            "could not remove task lane checkpoint journal"
        ) from error


def all_checkpoints(primary: Path) -> list[dict[str, object]]:
    directory = _root(primary) / "checkpoints"
    _private_dir(directory, create=False)
    if not directory.exists():
        return []
    checkpoints: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        value = _load_json(path, "task lane checkpoint journal")
        if value is not None:
            checkpoints.append(validate_checkpoint(value, path.stem))
    return checkpoints


def generation_path(
    primary: Path, lane_id: str, generation: int, *, create: bool = False
) -> Path:
    if LANE_ID_RE.fullmatch(lane_id) is None or generation < 1:
        raise TaskLaneStateError("task lane generation is invalid")
    root = _root(primary, create=create) / "generations"
    _private_dir(root, create=create)
    lane_root = root / lane_id
    _private_dir(lane_root, create=create)
    return lane_root / f"{generation:08d}.json"


def validate_generation(
    value: dict[str, object], lane_id: str, generation: int
) -> dict[str, object]:
    required = {
        "schema",
        "kind",
        "lane_id",
        "incarnation",
        "generation",
        "run_id",
        "token",
        "workspace",
        "initial_head",
        "promoted_head",
        "claims",
        "status",
    }
    if (
        set(value) != required
        or value.get("schema") != GENERATION_SCHEMA
        or value.get("kind") != GENERATION_KIND
        or value.get("lane_id") != lane_id
        or value.get("generation") != generation
        or value.get("status") != "released"
    ):
        raise TaskLaneStateError("task lane generation receipt is invalid")
    if not isinstance(value.get("incarnation"), int) or int(value["incarnation"]) < 1:
        raise TaskLaneStateError("task lane generation incarnation is invalid")
    if (
        not isinstance(value.get("run_id"), str)
        or RUN_ID_RE.fullmatch(str(value["run_id"])) is None
    ):
        raise TaskLaneStateError("task lane generation run id is invalid")
    if (
        not isinstance(value.get("token"), str)
        or TOKEN_RE.fullmatch(str(value["token"])) is None
    ):
        raise TaskLaneStateError("task lane generation token is invalid")
    _absolute(value.get("workspace"), "task lane generation workspace")
    _sha(value.get("initial_head"), "task lane generation initial head")
    _sha(value.get("promoted_head"), "task lane generation promoted head")
    claims = _claims(value.get("claims"))
    if any(item["generation"] != generation for item in claims):
        raise TaskLaneStateError("generation receipt claim is misbound")
    return {**value, "claims": claims}


def load_generation(
    primary: Path, lane_id: str, generation: int, *, required: bool = True
) -> dict[str, object] | None:
    value = _load_json(
        generation_path(primary, lane_id, generation),
        "task lane generation receipt",
    )
    if value is None:
        if required:
            raise TaskLaneStateError("task lane generation receipt is missing")
        return None
    return validate_generation(value, lane_id, generation)


def write_generation(primary: Path, value: dict[str, object]) -> Path:
    lane_id = str(value.get("lane_id", ""))
    generation = value.get("generation")
    if not isinstance(generation, int):
        raise TaskLaneStateError("task lane generation is invalid")
    validated = validate_generation(value, lane_id, generation)
    path = generation_path(primary, lane_id, generation, create=True)
    existing = _load_json(path, "task lane generation receipt")
    if existing is not None:
        if validate_generation(existing, lane_id, generation) != validated:
            raise TaskLaneStateError("task lane generation receipt is immutable")
        return path
    _atomic_json(path, validated)
    return path
