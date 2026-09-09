"""Package-independent HA guard and local package-repair admission.

This module has no cloud, lifecycle, credential or controller transition authority.
The deploy owner streams these exact bytes before importing the installed product.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

STATE = Path("/var/lib/nebius-vpngw/vm-ha")
BOOT = Path("/proc/sys/kernel/random/boot_id")
FORWARDING = Path("/proc/sys/net/ipv4/ip_forward")
STATUS_SCHEMA = "nebius-vpngw/vm-ha-status-v1"


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: Path, raw: bytes, mode: int = 0o600) -> None:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        sync_directory(directory.parent)
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise RuntimeError("HA publication directory is unsafe")
    if path.is_symlink():
        raise RuntimeError("HA publication target is unsafe")
    fd, temporary = tempfile.mkstemp(prefix=".ha-publish-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def cold_guard(
    *, state_dir: Path, boot_id: str, runner: Any = None, writer: Any = None, unlink: Any = None
) -> dict[str, Any]:
    """Canonical forwarding-off guard; caller owns routing serialization."""
    result = (runner or subprocess.run)(
        ["/usr/sbin/sysctl", "-w", "net.ipv4.ip_forward=0"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("cannot install VM-HA forwarding guard")
    record = {
        "schema": STATUS_SCHEMA,
        "guard_boot_id": boot_id,
        "data_plane_mode": "blocked",
        "installed_at": time.time(),
    }
    if writer is None:
        publish(state_dir / "guard.json", json.dumps(record).encode())
    else:
        writer(state_dir / "guard.json", record)
    if unlink is None:
        (state_dir / "standby-ready.json").unlink(missing_ok=True)
        sync_directory(state_dir)
    else:
        unlink(state_dir / "standby-ready.json")
    return record


@contextlib.contextmanager
def writer_locks(state_dir: Path = STATE, *, deadline: float) -> Iterator[None]:
    descriptors = []
    try:
        for path in (
            state_dir / "rearm.lock",
            state_dir / "mtls/writer.lock",
            Path("/run/nebius-vpngw/fix-routes.lock"),
        ):
            # Existing canonical owner directories must already exist for HA repair.
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("HA repair writer did not settle") from None
                    time.sleep(0.05)
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def read_json(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise RuntimeError("HA repair evidence is unsafe")
        raw = os.read(fd, 1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise RuntimeError("HA repair evidence exceeds limit")
        value = json.loads(raw)
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError("HA repair evidence changed during validation")
        if not isinstance(value, dict):
            raise RuntimeError("HA repair evidence is malformed")
        return value
    finally:
        os.close(fd)


def verified_file(path: Path, expected_sha256: str) -> bytes:
    """Verify and sync a private, single-link artifact before its first use."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise RuntimeError("HA approved file is unsafe")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read()
        current = path.lstat()
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        ) or sha(raw) != expected_sha256:
            raise RuntimeError("HA approved file changed")
        os.fsync(fd)
        sync_directory(path.parent)
        return raw
    finally:
        os.close(fd)


def initialize_unstarted_checkpoint(*, state_dir: Path = STATE, unstarted: bool) -> None:
    """Durably seed the canonical initial state before first startup admission.

    Caller holds all HA writer locks and proves controller startup was excluded.
    Never replace a checkpoint or infer initial state from a missing old record.
    """
    path = state_dir / "controller-checkpoint.json"
    if path.exists() or path.is_symlink():
        return
    if not unstarted or (state_dir / "status.json").exists():
        raise RuntimeError("HA initial checkpoint lacks first-start authority")
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint

    publish(path, json.dumps(controller_checkpoint_to_dict(ControllerCheckpoint())).encode())


def exact_lock(expected: dict[str, Any], state_dir: Path = STATE) -> dict[str, Any]:
    value = read_json(state_dir / "apply.lock")
    if (
        set(value)
        != {"schema", "apply_locked", "cluster_id", "node_id", "generation_id", "operation_id"}
        or value["schema"] != "nebius-vpngw/vm-ha-apply-lock-v2"
        or value["apply_locked"] is not True
        or any(
            value.get(key) != expected[key] for key in ("cluster_id", "node_id", "generation_id")
        )
        or (
            expected.get("operation_id") is not None
            and value["operation_id"] != expected["operation_id"]
        )
        or not isinstance(value["operation_id"], str)
        or len(value["operation_id"]) != 64
        or any(c not in "0123456789abcdef" for c in value["operation_id"])
    ):
        raise RuntimeError("HA repair requires the exact generation and operation apply lock")
    return value


def evidence(
    expected: dict[str, Any], *, state_dir: Path = STATE, unstarted: bool = False
) -> dict[str, Any]:
    """Observe underlying authority, never boot-filtered status projections."""
    lock = exact_lock(expected, state_dir)
    boot = BOOT.read_text().strip()
    guard = read_json(state_dir / "guard.json")
    if (
        guard.get("schema") != STATUS_SCHEMA
        or guard.get("guard_boot_id") != boot
        or guard.get("data_plane_mode") not in {"blocked", "passive"}
        or FORWARDING.read_text().strip() != "0"
    ):
        raise RuntimeError("HA repair current-boot forwarding exclusion is unproven")
    # These effects cannot belong to an unfinished ordinary conversion. Presence
    # is deliberately blocking, even if an observer would project a terminal state.
    for name in (
        "accepted-cloud-operation.json",
        "rearm-cloud-operation.json",
        "standby-restoration-authorization.json",
        "standby-auto-healing-recovery.json",
        "transfer-lineage.json",
        "transfer-progress.json",
        "mtls/inhibition.json",
    ):
        path = state_dir / name
        if path.exists() or path.is_symlink():
            raise RuntimeError("HA repair conflicts with durable writer authority")
    from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore, PeerLeaf
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_from_dict

    checkpoint_path = state_dir / "controller-checkpoint.json"
    checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else None
    if checkpoint is None:
        if not unstarted or (state_dir / "status.json").exists() or checkpoint_path.is_symlink():
            raise RuntimeError("HA repair required controller checkpoint is missing")
    else:
        # Legacy decoding must not normalize away an unproved external effect.
        if checkpoint.get("pending_action") is not None:
            raise RuntimeError("HA repair controller checkpoint is unresolved")
        decoded = controller_checkpoint_from_dict(checkpoint)
        if decoded.transfer_continuity is not None or decoded.repair_attempt is not None:
            raise RuntimeError("HA repair controller continuity is unresolved")
    store = ManagedMTLSStore(state_dir / "mtls", create=False)
    status = store.status()
    active = store._load_active()
    if active is None or any(
        active.get(k) != expected[k] for k in ("cluster_id", "node_id", "compute_id")
    ):
        raise RuntimeError("HA repair mTLS identity changed")
    # Validate the active identity's shared public shape, including numeric epoch
    # and fingerprints, in addition to the canonical active-record key contract.
    PeerLeaf.from_mapping(
        {
            key: active[key]
            for key in (
                "node_id",
                "compute_id",
                "epoch",
                "certificate_fingerprint",
                "spki_fingerprint",
            )
        }
    )
    if (
        active["operation_id"] is not None
        and store._load_transaction(active["operation_id"]) is None
    ):
        raise RuntimeError("HA repair active mTLS transaction is missing")
    if status.inhibited:
        raise RuntimeError("HA repair conflicts with mTLS inhibition")
    transactions = {}
    for path in sorted(store.transactions.glob("*.json")):
        record = store._load_transaction(path.stem)
        if record is None:
            raise RuntimeError("HA repair mTLS transaction disappeared")
        if record.get("phase") in {"pruned", "rolled-back"}:
            continue
        if (
            record.get("operation_kind") not in {"bootstrap", "replacement", "recovery"}
            or record.get("phase") not in {"local-active", "verified", "committed"}
            or record.get("operation_id") != active["operation_id"]
            or any(record.get(k) != expected[k] for k in ("cluster_id", "node_id", "compute_id"))
        ):
            raise RuntimeError("HA repair conflicts with mTLS transaction")
        transactions[path.name] = dict(record)
    return {
        "lock": lock,
        "boot_id": boot,
        "forwarding": False,
        "guard_boot_id": guard["guard_boot_id"],
        "mtls": active,
        "transactions": transactions,
        # Sequence changes under an inhibited controller are harmless; the raw
        # no-pending assertions above must be made afresh at every boundary.
        "checkpoint_schema": None if checkpoint is None else checkpoint["schema"],
    }


def persist_cold_guard(source: bytes) -> None:
    helper = Path("/var/lib/nebius-vpngw/ha-cold-guard.py")
    suffix = b"""\nif __name__ == "__main__":
    fd = os.open("/run/nebius-vpngw/fix-routes.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("HA cold guard routing lock did not settle")
                time.sleep(0.05)
        cold_guard(state_dir=STATE, boot_id=BOOT.read_text().strip())
    finally:
        os.close(fd)
"""
    publish(helper, source + suffix)
    publish(
        Path("/etc/systemd/system/nebius-vpngw-vm-ha-guard.service.d/package-independent.conf"),
        b"[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B /var/lib/nebius-vpngw/ha-cold-guard.py\n",
        0o644,
    )


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
