"""Private durable ownership state for the worktree skill helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SCHEMA = 2
STATE_DIRECTORY = ".worktree-skill"
STATUSES = {"planned", "active", "recovery", "cleanup-pending"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?$")
TASK_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40,64}$")


class StateError(RuntimeError):
    """The ownership manifest is absent, malformed, or inconsistent."""


@dataclass(frozen=True)
class Manifest:
    schema: int
    status: str
    name: str
    branch: str
    primary: str
    worktree: str
    scope: str
    base: str
    task_slug: str
    default_remote: str
    default_branch: str
    default_ref: str
    default_head: str
    expected_head: str | None = None

    def updated(self, *, status: str, expected_head: str | None = None) -> "Manifest":
        return Manifest(
            **{
                **asdict(self),
                "status": status,
                "expected_head": expected_head,
            }
        )


def state_directory(primary: Path) -> Path:
    return primary.parent / f"{primary.name}-worktrees" / STATE_DIRECTORY


def manifest_path(primary: Path, name: str) -> Path:
    return state_directory(primary) / f"{name}.json"


def write_manifest(primary: Path, manifest: Manifest) -> Path:
    directory = state_directory(primary)
    if directory.is_symlink():
        raise StateError(f"manifest directory must not be a symlink: {directory}")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    destination = manifest_path(primary, manifest.name)
    payload = json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{manifest.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o600)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise StateError(
            f"could not persist ownership manifest: {destination}"
        ) from error
    return destination


def load_manifest(
    primary: Path, name: str, *, required: bool = True
) -> Manifest | None:
    path = manifest_path(primary, name)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            raise StateError(f"managed ownership manifest is missing: {path}")
        return None
    except OSError as error:
        raise StateError(f"could not read ownership manifest: {path}") from error
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise StateError(f"ownership manifest is invalid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise StateError(f"ownership manifest must contain an object: {path}")
    expected = {
        "schema",
        "status",
        "name",
        "branch",
        "primary",
        "worktree",
        "scope",
        "base",
        "task_slug",
        "default_remote",
        "default_branch",
        "default_ref",
        "default_head",
        "expected_head",
    }
    if set(payload) != expected:
        raise StateError(f"ownership manifest fields are invalid: {path}")
    try:
        manifest = Manifest(**payload)
    except TypeError as error:
        raise StateError(f"ownership manifest values are invalid: {path}") from error
    string_fields = (
        "status",
        "name",
        "branch",
        "primary",
        "worktree",
        "scope",
        "base",
        "task_slug",
        "default_remote",
        "default_branch",
        "default_ref",
        "default_head",
    )
    if any(not isinstance(getattr(manifest, field), str) for field in string_fields):
        raise StateError(f"ownership manifest values are invalid: {path}")
    if not isinstance(manifest.schema, int) or isinstance(manifest.schema, bool):
        raise StateError(f"ownership manifest schema is invalid: {path}")
    if manifest.schema != SCHEMA:
        raise StateError(f"unsupported ownership manifest schema: {manifest.schema}")
    if manifest.status not in STATUSES:
        raise StateError(f"ownership manifest status is invalid: {path}")
    if (
        not NAME_RE.fullmatch(manifest.name)
        or manifest.name != name
        or not manifest.name.startswith("project-")
    ):
        raise StateError(f"ownership manifest name is invalid: {path}")
    if manifest.branch != f"feature/{manifest.name.removeprefix('project-')}":
        raise StateError(f"ownership manifest branch is invalid: {path}")
    if (
        not Path(manifest.primary).is_absolute()
        or not Path(manifest.worktree).is_absolute()
    ):
        raise StateError(f"ownership manifest paths must be absolute: {path}")
    scope_parts = Path(manifest.scope).parts
    if not manifest.scope or Path(manifest.scope).is_absolute() or ".." in scope_parts:
        raise StateError(f"ownership manifest scope is invalid: {path}")
    if not OBJECT_ID_RE.fullmatch(manifest.base):
        raise StateError(f"ownership manifest base is invalid: {path}")
    if not TASK_SLUG_RE.fullmatch(manifest.task_slug):
        raise StateError(f"ownership manifest task slug is invalid: {path}")
    if (
        manifest.default_remote != "origin"
        or manifest.default_branch == ""
        or manifest.default_ref != f"origin/{manifest.default_branch}"
        or not OBJECT_ID_RE.fullmatch(manifest.default_head)
        or manifest.default_head != manifest.base
    ):
        raise StateError(f"ownership manifest default identity is invalid: {path}")
    if manifest.expected_head is not None and (
        not isinstance(manifest.expected_head, str)
        or not OBJECT_ID_RE.fullmatch(manifest.expected_head)
    ):
        raise StateError(f"ownership manifest expected head is invalid: {path}")
    if manifest.status != "planned" and manifest.expected_head is None:
        raise StateError(f"ownership manifest expected head is required: {path}")
    return manifest


def matching_manifests(primary: Path, *, scope: str, task_slug: str) -> list[Manifest]:
    directory = state_directory(primary)
    if not directory.exists():
        return []
    if directory.is_symlink():
        raise StateError(f"manifest directory must not be a symlink: {directory}")
    matches: list[Manifest] = []
    for path in sorted(directory.glob("*.json")):
        manifest = load_manifest(primary, path.stem)
        assert manifest is not None
        if manifest.scope == scope and manifest.task_slug == task_slug:
            matches.append(manifest)
    return matches


def delete_manifest(primary: Path, name: str) -> None:
    path = manifest_path(primary, name)
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise StateError(f"could not remove ownership manifest: {path}") from error
    directory = state_directory(primary)
    try:
        directory.rmdir()
    except OSError:
        pass
