"""Private durable ownership state for the worktree skill helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any


SCHEMA = 4
STATE_DIRECTORY = ".worktree-skill"
STATUSES = {"planned", "active", "recovery", "integrated"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?$")
TASK_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40,64}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
LEASE_STATES = {"none", "active", "released"}
LEASE_OWNERS = {"task-implementer", "agentic-sdlc"}


class StateError(RuntimeError):
    """The ownership manifest is absent, malformed, or inconsistent."""


def fsync_directory(path: Path) -> None:
    """Persist directory-entry changes at an already validated directory."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(f"directory sync target is not a directory: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    source_branch: str
    source_ref: str
    expected_head: str | None = None
    integration_source_head: str | None = None
    integration_child_head: str | None = None
    integration_head: str | None = None
    lease_state: str = "none"
    lease_owner: str | None = None
    lease_token: str | None = None

    def updated(self, **changes: object) -> "Manifest":
        return Manifest(
            **{
                **asdict(self),
                **changes,
            }
        )


def state_directory(primary: Path) -> Path:
    return primary.parent / f"{primary.name}-worktrees" / STATE_DIRECTORY


def checked_worktree_parent(primary: Path, *, create: bool = False) -> Path:
    """Return the canonical managed-worktree parent without following symlinks."""

    if not primary.is_absolute():
        raise StateError(f"primary worktree path must be absolute: {primary}")
    try:
        canonical_primary = primary.resolve(strict=True)
    except OSError as error:
        raise StateError(f"primary worktree path is unavailable: {primary}") from error
    parent = canonical_primary.parent / f"{canonical_primary.name}-worktrees"
    if parent.is_symlink():
        raise StateError(f"managed worktree parent must not be a symlink: {parent}")
    if parent.exists():
        if not parent.is_dir():
            raise StateError(f"managed worktree parent must be a directory: {parent}")
        try:
            canonical_parent = parent.resolve(strict=True)
        except OSError as error:
            raise StateError(
                f"managed worktree parent is unavailable: {parent}"
            ) from error
        if canonical_parent != parent:
            raise StateError(f"managed worktree parent must be canonical: {parent}")
    elif create:
        try:
            parent.mkdir(parents=False)
            fsync_directory(parent.parent)
        except OSError as error:
            raise StateError(
                f"could not create managed worktree parent: {parent}"
            ) from error
        if parent.is_symlink() or parent.resolve(strict=True) != parent:
            raise StateError(f"managed worktree parent must be canonical: {parent}")
    return parent


def checked_state_directory(primary: Path, *, create: bool = False) -> Path:
    """Return a validated state directory, creating only canonical components."""

    parent = checked_worktree_parent(primary, create=create)
    directory = parent / STATE_DIRECTORY
    if directory.is_symlink():
        raise StateError(f"state directory must not be a symlink: {directory}")
    if directory.exists():
        if not directory.is_dir():
            raise StateError(f"state directory must be a directory: {directory}")
        try:
            canonical_directory = directory.resolve(strict=True)
        except OSError as error:
            raise StateError(f"state directory is unavailable: {directory}") from error
        if canonical_directory != directory:
            raise StateError(f"state directory must be canonical: {directory}")
    elif create:
        try:
            directory.mkdir(parents=False, mode=0o700)
            fsync_directory(parent)
        except OSError as error:
            raise StateError(
                f"could not create state directory: {directory}"
            ) from error
        if directory.is_symlink() or directory.resolve(strict=True) != directory:
            raise StateError(f"state directory must be canonical: {directory}")
    if create and directory.exists():
        try:
            directory.chmod(0o700)
        except OSError as error:
            raise StateError(
                f"could not secure state directory: {directory}"
            ) from error
    return directory


def manifest_path(primary: Path, name: str) -> Path:
    return state_directory(primary) / f"{name}.json"


def write_manifest(primary: Path, manifest: Manifest) -> Path:
    directory = checked_state_directory(primary, create=True)
    destination = directory / f"{manifest.name}.json"
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
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        os.replace(temporary, destination)
        fsync_directory(directory)
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
    path = checked_state_directory(primary) / f"{name}.json"
    if path.is_symlink():
        raise StateError(f"ownership manifest must not be a symlink: {path}")
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
    if payload.get("schema") in {1, 2, 3}:
        raise StateError(
            "WORKFLOW_UPGRADE_REQUIRED: unfinished ownership manifest schema is unsupported"
        )
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
        "source_branch",
        "source_ref",
        "expected_head",
        "integration_source_head",
        "integration_child_head",
        "integration_head",
        "lease_state",
        "lease_owner",
        "lease_token",
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
        "source_branch",
        "source_ref",
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
        not manifest.source_branch
        or manifest.source_ref != f"refs/heads/{manifest.source_branch}"
    ):
        raise StateError(f"ownership manifest source identity is invalid: {path}")
    if manifest.expected_head is not None and (
        not isinstance(manifest.expected_head, str)
        or not OBJECT_ID_RE.fullmatch(manifest.expected_head)
    ):
        raise StateError(f"ownership manifest expected head is invalid: {path}")
    if manifest.status != "planned" and manifest.expected_head is None:
        raise StateError(f"ownership manifest expected head is required: {path}")
    integration_values = (
        manifest.integration_source_head,
        manifest.integration_child_head,
        manifest.integration_head,
    )
    if any(
        value is not None
        and (not isinstance(value, str) or not OBJECT_ID_RE.fullmatch(value))
        for value in integration_values
    ):
        raise StateError(f"ownership manifest integration proof is invalid: {path}")
    if manifest.status == "integrated":
        if any(value is None for value in integration_values):
            raise StateError(
                f"integrated ownership manifest requires exact merge proof: {path}"
            )
        if manifest.expected_head != manifest.integration_child_head:
            raise StateError(
                f"integrated ownership manifest child head is inconsistent: {path}"
            )
    elif any(value is not None for value in integration_values):
        raise StateError(
            f"non-integrated ownership manifest contains merge proof: {path}"
        )
    if manifest.lease_state not in LEASE_STATES:
        raise StateError(f"ownership manifest lease state is invalid: {path}")
    if manifest.lease_state == "none":
        if manifest.lease_owner is not None or manifest.lease_token is not None:
            raise StateError(
                f"unleased ownership manifest contains lease identity: {path}"
            )
    elif (
        manifest.lease_owner not in LEASE_OWNERS
        or not isinstance(manifest.lease_token, str)
        or TOKEN_RE.fullmatch(manifest.lease_token) is None
    ):
        raise StateError(f"ownership manifest lease identity is invalid: {path}")
    return manifest


def matching_manifests(primary: Path, *, scope: str, task_slug: str) -> list[Manifest]:
    directory = checked_state_directory(primary)
    if not directory.exists():
        return []
    matches: list[Manifest] = []
    for path in sorted(directory.glob("*.json")):
        manifest = load_manifest(primary, path.stem)
        assert manifest is not None
        if manifest.scope == scope and manifest.task_slug == task_slug:
            matches.append(manifest)
    return matches


def all_manifests(primary: Path) -> list[Manifest]:
    directory = checked_state_directory(primary)
    if not directory.exists():
        return []
    manifests: list[Manifest] = []
    for path in sorted(directory.glob("*.json")):
        manifest = load_manifest(primary, path.stem)
        assert manifest is not None
        manifests.append(manifest)
    return manifests


def delete_manifest(primary: Path, name: str) -> None:
    directory = checked_state_directory(primary)
    path = directory / f"{name}.json"
    if path.is_symlink():
        raise StateError(f"ownership manifest must not be a symlink: {path}")
    removed = False
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        pass
    except OSError as error:
        raise StateError(f"could not remove ownership manifest: {path}") from error
    if removed:
        try:
            fsync_directory(directory)
        except OSError as error:
            raise StateError(
                f"could not persist ownership manifest removal: {path}"
            ) from error
    try:
        directory.rmdir()
    except OSError:
        pass
    else:
        try:
            fsync_directory(directory.parent)
        except OSError as error:
            raise StateError(
                f"could not persist ownership state-directory removal: {directory}"
            ) from error
