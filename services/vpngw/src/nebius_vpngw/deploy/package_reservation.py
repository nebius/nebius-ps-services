"""Exact package admission readback under the existing command execution.

These protected child records locate the guest owner. They never replace the
command, lifecycle, fencing or guest journal checks and contain no wheel bytes.
"""

from __future__ import annotations

import json
import os
import re
import stat
from contextvars import ContextVar
from pathlib import Path

from ..package_worker import digest, identity
from .apply_diagnostics import PackageReservationConflict
from .operator_guest import publish, read_file
from .operator_keys import protected_directory

SCOPE: ContextVar[tuple[Path, str, str] | None] = ContextVar("package_reservation", default=None)


def recovery_owner(
    root: Path,
    execution: str,
    authority: str,
    *,
    parent: tuple[Path, str, str] | None = None,
    admit: bool = False,
) -> tuple[Path, str, str]:
    """Persist a composed command's budget owner before any child effect."""
    path = path_for(root, execution, "recovery-owner").parent / "recovery-owner.json"
    record = read_file(path)
    child = {"execution": execution, "authority": authority}
    if record is not None:
        value = json.loads(record["content"])
        if (
            record["uid"] != os.getuid()
            or record["mode"] != 0o600
            or set(value) != {"schema", "child", "parent"}
            or value["schema"] != "nebius-vpngw.recovery-owner.v1"
            or value["child"] != child
            or set(value["parent"]) != {"execution", "authority"}
            or re.fullmatch(r"[a-f0-9]{32}", value["parent"]["execution"]) is None
            or re.fullmatch(r"[a-f0-9]{64}", value["parent"]["authority"]) is None
        ):
            raise RuntimeError("Composed recovery owner changed")
        selected = (root, value["parent"]["execution"], value["parent"]["authority"])
        if parent is not None and parent not in {selected, (root, execution, authority)}:
            raise RuntimeError("Composed recovery scope differs")
        return selected
    if parent is None or parent == (root, execution, authority):
        return root, execution, authority
    if parent[0] != root or not admit:
        raise RuntimeError("Composed recovery requires its original durable owner")
    protected_directory(path.parent)
    value = {
        "schema": "nebius-vpngw.recovery-owner.v1",
        "child": child,
        "parent": {"execution": parent[1], "authority": parent[2]},
    }
    publish(
        path,
        None,
        dict(
            content=json.dumps(value, sort_keys=True), uid=os.getuid(), gid=os.getgid(), mode=0o600
        ),
    )
    return parent


def metadata(request: dict) -> dict:
    return {key: value for key, value in request.items() if key != "staged_package"}


def handoff_approval(
    reservation: dict | None, *, proposed: str, lifecycle_approval: str | None
) -> str:
    """Reattach an admitted takeover to its still-current lifecycle authority."""
    if reservation is None:
        return proposed
    request = reservation["request"]
    if (
        request.get("action") != "reserve"
        or lifecycle_approval is None
        or request.get("approval") != lifecycle_approval
    ):
        raise PackageReservationConflict(
            "package reservation approval differs from the active VM-HA transaction"
        )
    # Compute progress can change a new preview digest. It cannot change the
    # approval of this already admitted worker. bind() still checks every other
    # immutable request field and the exact worker source before reattachment.
    return lifecycle_approval


def path_for(root: Path, execution: str, host: str) -> Path:
    if re.fullmatch(r"[a-f0-9]{32}", execution) is None:
        raise ValueError("package reservation execution is invalid")
    return root / "package-reservations" / execution / (digest(host) + ".json")


def require_unreserved(root: Path, execution: str) -> None:
    """Prove no child request was published for an unused command execution."""
    import stat

    directory = path_for(root, execution, "").parent
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or any(directory.iterdir())
    ):
        raise RuntimeError("Unused command has package reservation evidence")


def read(root: Path, execution: str, authority: str, host: str) -> dict | None:
    record = read_file(path_for(root, execution, host))
    if record is None:
        return None
    value = json.loads(record["content"])
    if (
        record["uid"] != os.getuid()
        or record["mode"] != 0o600
        or set(value)
        != {"schema", "execution", "authority", "host", "request", "identity", "source"}
        or value["schema"] != "nebius-vpngw.package-reservation.v1"
        or value["execution"] != execution
        or value["authority"] != authority
        or value["host"] != host
        or value["request"]["package"]["host"] != host
        or value["identity"] != identity(value["request"])
        or re.fullmatch(r"[a-f0-9]{64}", value["source"]) is None
    ):
        raise RuntimeError("package reservation authority changed")
    return value


def for_host(root: Path, host: str) -> list[dict]:
    """Read original protected admissions; reconstruction does not invent one."""
    directory = root / "package-reservations"
    if not directory.exists() and not directory.is_symlink():
        return []
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise RuntimeError("Package reservation directory is unsafe")
    result = []
    for entry in sorted(directory.iterdir()):
        info = entry.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise RuntimeError("Package reservation execution directory is unsafe")
        path = path_for(root, entry.name, host)
        raw = read_file(path)
        if raw is None:
            continue
        stored = json.loads(raw["content"])
        if (not isinstance(stored.get("authority"), str)
                or re.fullmatch(r"[a-f0-9]{64}", stored["authority"]) is None):
            raise RuntimeError("Package reservation approval identity is invalid")
        value = read(root, entry.name, stored["authority"], host)
        if value is None:
            raise RuntimeError("Package reservation changed during inspection")
        result.append(value)
    return result


def admit(request: dict, source: str) -> dict | None:
    scope = SCOPE.get()
    if scope is None:
        return None
    root, execution, authority = scope
    host = request["package"]["host"]
    old = read(root, execution, authority, host)
    value = dict(
        schema="nebius-vpngw.package-reservation.v1",
        execution=execution,
        authority=authority,
        host=host,
        request=metadata(request),
        identity=identity(request),
        source=source,
    )
    if old is not None:
        # Fresh peer/observation evidence does not rewrite frozen admission.
        if old["identity"] != value["identity"] or old["source"] != source:
            raise PackageReservationConflict("package reservation differs from approved execution")
        return old
    path = path_for(root, execution, host)
    protected_directory(path.parent)
    publish(
        path,
        None,
        dict(
            content=json.dumps(value, sort_keys=True), uid=os.getuid(), gid=os.getgid(), mode=0o600
        ),
    )
    return value
