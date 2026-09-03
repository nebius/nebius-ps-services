"""Ownership validation and atomic replacement for private rendered rules."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Optional

from .contracts import ProjectInstructionsError
from .contracts import _lstat_optional
from .contracts import _read_regular
from .contracts import _sha256_bytes
from .contracts import _valid_sha256
from .private_state import _relative_private_path


RENDER_STATE_SCHEMA = "project-agent-instructions.render-state.v1"
RENDER_STATE_PUBLICATION_INCOMPLETE = "RENDER_STATE_PUBLICATION_INCOMPLETE"
_RENDER_STATE_FIELDS = {
    "schema",
    "project_root",
    "git_root",
    "project_scope",
    "spec_owner",
    "requirements",
    "design",
    "spec_receipt",
    "manifest_path",
    "manifest_file_sha256",
    "decision_path",
    "decision_file_sha256",
    "decision_sha256",
    "disposition",
    "rules_path",
    "rules_sha256",
    "repository_mutated",
}


@contextmanager
def render_lock(
    private_root: Path,
    *,
    lock_name: str = ".render.lock",
    label: str = "render",
) -> Iterator[None]:
    """Serialize one private-state operation under an exact owned lock."""

    lock_path = private_root / lock_name
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created = False
    try:
        descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(lock_path, flags)
        except OSError as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", f"{label} lock could not be opened safely"
            ) from error
    except OSError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} lock could not be created safely"
        ) from error
    try:
        if created:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        opened = os.fstat(descriptor)
        current = _lstat_optional(lock_path)
        if (
            current is None
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", f"{label} lock is not an exact owned file"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise ProjectInstructionsError(
                    "CONCURRENT_MODIFICATION", f"another {label} is already active"
                ) from error
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", f"{label} lock could not be acquired safely"
            ) from error
        locked = _lstat_optional(lock_path)
        if (
            locked is None
            or (locked.st_dev, locked.st_ino) != (opened.st_dev, opened.st_ino)
            or locked.st_nlink != 1
            or stat.S_IMODE(locked.st_mode) != 0o600
        ):
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                f"{label} lock changed during acquisition",
            )
        yield
    except OSError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} lock could not be used safely"
        ) from error
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _human_existing_sufficient(manifest: dict[str, object]) -> bool:
    target = dict(manifest["target"])
    active = manifest.get("active_project_instruction")
    if not isinstance(active, dict) or active.get("path") != target.get("active_path"):
        return False
    if target.get("active_path") == target.get("path"):
        return target.get("file_status") in {"human-owned", "human-edited"}
    return active.get("kind") in {"project-override", "project-fallback"}


def validate_render_disposition(
    manifest: dict[str, object],
    disposition: str,
    approval: Optional[dict[str, str]],
) -> None:
    """Reject target/disposition combinations known to be inapplicable."""

    target = dict(manifest["target"])
    status = str(target["file_status"])
    target_digest = target.get("sha256")
    active_path = target.get("active_path")
    target_path = target.get("path")
    if status == "legacy":
        raise ProjectInstructionsError(
            "LEGACY_GENERATED_FILE",
            "legacy generated AGENTS.md requires manual resolution",
        )
    if disposition == "existing-sufficient":
        if (
            status == "managed"
            or approval is not None
            or not _human_existing_sufficient(manifest)
        ):
            raise ProjectInstructionsError(
                "EXISTING_INSTRUCTIONS_GAP",
                "existing-sufficient requires active human-owned instructions",
            )
        return
    if disposition == "not-needed":
        if status == "managed":
            if (
                approval is None
                or approval["action"] != "retire"
                or approval["target_sha256"] != target_digest
            ):
                raise ProjectInstructionsError(
                    "RETIREMENT_APPROVAL_REQUIRED",
                    "managed project AGENTS.md requires exact-digest retirement approval",
                )
            return
        if approval is not None:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "retirement approval is not applicable"
            )
        if status == "missing" and active_path is None:
            return
        raise ProjectInstructionsError(
            "INSTRUCTION_CONFLICT",
            "not-needed conflicts with existing project instructions",
        )
    if active_path is not None and active_path != target_path:
        raise ProjectInstructionsError(
            "EXISTING_INSTRUCTIONS_GAP", "alternate project instructions are active"
        )
    if status == "missing":
        if approval is not None:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "ownership approval is not applicable"
            )
        return
    if status == "human-edited":
        raise ProjectInstructionsError(
            "STALE_GENERATED_FILE", "edited generated instructions are human-owned"
        )
    if status == "human-owned":
        if approval is not None:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "ownership approval is not applicable"
            )
        return
    if status != "managed":
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "target ownership state is invalid"
        )
    # Managed `needed` outcomes require the private ownership receipt to decide
    # whether approval is required, invalid, or unnecessary. Apply owns that
    # final distinction.


def validate_render_predecessor(
    state_path: Path,
    output_path: Path,
    private_root: Path,
    current: dict[str, object],
    manifest_path: Path,
    decision_path: Path,
    existing: os.stat_result,
    existing_bytes: bytes,
) -> tuple[os.stat_result, bytes]:
    """Prove that the current private rules are owned by the prior render."""

    state_metadata = _lstat_optional(state_path)
    if (
        state_metadata is None
        or not stat.S_ISREG(state_metadata.st_mode)
        or stat.S_IMODE(state_metadata.st_mode) != 0o600
        or state_metadata.st_nlink != 1
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered rules predecessor state is unsafe"
        )
    state_bytes = _read_regular(state_path, "render state")
    if len(state_bytes) > 64 * 1024:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered rules predecessor state is too large"
        )
    try:
        state_value = json.loads(state_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered rules predecessor state is invalid"
        ) from error
    if not isinstance(state_value, dict):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered rules predecessor state is invalid"
        )
    state: dict[str, object] = state_value
    requirements = state.get("requirements")
    design = state.get("design")
    spec_receipt = state.get("spec_receipt")
    current_requirements = current.get("requirements")
    current_design = current.get("design")
    current_receipt = current.get("spec_receipt")
    disposition = state.get("disposition")
    if (
        set(state) != _RENDER_STATE_FIELDS
        or state.get("schema") != RENDER_STATE_SCHEMA
        or state.get("repository_mutated") is not False
        or state.get("project_root") != current.get("project_root")
        or state.get("git_root") != current.get("git_root")
        or state.get("project_scope") != current.get("project_scope")
        or state.get("spec_owner") != current.get("spec_owner")
        or not isinstance(requirements, dict)
        or not isinstance(current_requirements, dict)
        or requirements.get("path") != current_requirements.get("path")
        or not isinstance(design, dict)
        or not isinstance(current_design, dict)
        or design.get("path") != current_design.get("path")
        or not isinstance(spec_receipt, dict)
        or not isinstance(current_receipt, dict)
        or spec_receipt.get("path") != current_receipt.get("path")
        or state.get("manifest_path")
        != _relative_private_path(private_root, manifest_path)
        or state.get("decision_path")
        != _relative_private_path(private_root, decision_path)
        or state.get("rules_path")
        != _relative_private_path(private_root, output_path)
        or state.get("rules_sha256") != _sha256_bytes(existing_bytes)
        or not _valid_sha256(state.get("manifest_file_sha256"))
        or not _valid_sha256(state.get("decision_file_sha256"))
        or not _valid_sha256(state.get("decision_sha256"))
        or disposition not in {"needed", "not-needed", "existing-sufficient"}
        or (disposition == "needed") != bool(existing_bytes)
        or not stat.S_ISREG(existing.st_mode)
        or stat.S_IMODE(existing.st_mode) != 0o600
        or existing.st_nlink != 1
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered rules predecessor state does not own the target"
        )
    confirmed_state = _lstat_optional(state_path)
    if (
        confirmed_state is None
        or not stat.S_ISREG(confirmed_state.st_mode)
        or stat.S_IMODE(confirmed_state.st_mode) != 0o600
        or confirmed_state.st_nlink != 1
        or (confirmed_state.st_dev, confirmed_state.st_ino)
        != (state_metadata.st_dev, state_metadata.st_ino)
        or confirmed_state.st_size != state_metadata.st_size
        or confirmed_state.st_mtime_ns != state_metadata.st_mtime_ns
        or _read_regular(state_path, "render state") != state_bytes
    ):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "rendered rules predecessor state changed during validation",
        )
    return confirmed_state, state_bytes


def _recheck_predecessor(
    path: Path,
    label: str,
    predecessor: os.stat_result,
    predecessor_bytes: bytes,
) -> None:
    current = _lstat_optional(path)
    if current is None:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", f"{label} disappeared before replacement"
        )
    if (
        not stat.S_ISREG(current.st_mode)
        or stat.S_IMODE(current.st_mode) != 0o600
        or current.st_nlink != 1
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} became unsafe before replacement"
        )
    current_bytes = _read_regular(path, label)
    confirmed = _lstat_optional(path)
    if confirmed is None:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", f"{label} disappeared before replacement"
        )
    if (
        not stat.S_ISREG(confirmed.st_mode)
        or stat.S_IMODE(confirmed.st_mode) != 0o600
        or confirmed.st_nlink != 1
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} became unsafe before replacement"
        )
    if (
        (current.st_dev, current.st_ino) != (predecessor.st_dev, predecessor.st_ino)
        or current.st_size != predecessor.st_size
        or current.st_mtime_ns != predecessor.st_mtime_ns
        or current_bytes != predecessor_bytes
        or (confirmed.st_dev, confirmed.st_ino) != (current.st_dev, current.st_ino)
        or confirmed.st_size != current.st_size
        or confirmed.st_mtime_ns != current.st_mtime_ns
    ):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", f"{label} changed before replacement"
        )


def replace_rendered_rules(
    output_path: Path,
    rendered: bytes,
    private_root: Path,
    state_path: Path,
    predecessor_state: os.stat_result,
    predecessor_state_bytes: bytes,
    predecessor: os.stat_result,
    predecessor_bytes: bytes,
) -> None:
    """Atomically replace rules after one final dual-file compare-and-swap."""

    descriptor = -1
    temporary_path: Optional[Path] = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{output_path.name}.", dir=str(private_root)
        )
        temporary_path = Path(temporary)
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        _recheck_predecessor(
            output_path,
            "rendered rules",
            predecessor,
            predecessor_bytes,
        )
        _recheck_predecessor(
            state_path,
            "rendered rules predecessor state",
            predecessor_state,
            predecessor_state_bytes,
        )
        os.replace(temporary_path, output_path)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(private_root, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered rules could not be written safely"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None and _lstat_optional(temporary_path) is not None:
            temporary_path.unlink()
