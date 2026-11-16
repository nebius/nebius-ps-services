"""Retained SSH identity that survives repair-owned power and network updates."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from .recovery import RepairBlocked
from .repair_cloud import retained_binding
from .vm_ha_identity import FormerVMHAProvenance, compute_provisioning_provenance
from .vm_ha_lifecycle import VMHALifecycleStore


def ha_bindings(manager: Any, spec: Any, path: Path) -> dict[str, Callable[[], None]]:
    """Use the existing HA provenance owner, including a retained ordinary VM."""
    snapshot = manager._vm_ha_ssh_preflight_snapshot
    options: dict[str, Any] = {}
    if any(compute_provisioning_provenance(instance) in {
        None, FormerVMHAProvenance.LEGACY_RUNTIME,
    } for instance, _address in snapshot.values()):
        try:
            authority = VMHALifecycleStore(path).read_hardened(
                expected_project_id=manager.project_id, expected_gateway_name=spec.name,
            )
        except (ValueError, OSError) as error:
            raise RepairBlocked("repair-ssh-authority") from error
        if authority is None:
            raise RepairBlocked("repair-ssh-authority")

        def current():
            try:
                authority.assert_current()
            except (ValueError, OSError) as error:
                raise RepairBlocked("repair-ssh-authority") from error
            return authority

        options["lifecycle_snapshot_loader"] = current
    return manager.vm_ha_ssh_trust_bindings(spec, retained_hosts=set(snapshot), **options)


def retryable_transport(error: Exception) -> bool:
    """Retry boot-time transport loss without hiding a trust or access rejection."""
    import paramiko  # type: ignore[import-untyped]

    from .ordinary_apply import RemoteTransportUnavailable
    from .ssh_push import _host_identity_failure

    if isinstance(error, RemoteTransportUnavailable):
        if not isinstance(error.__cause__, Exception):
            return False
        error = error.__cause__
    if isinstance(error, (PermissionError, paramiko.AuthenticationException)):
        return False
    if _host_identity_failure(error, paramiko, "retained gateway") is not None:
        return False
    return isinstance(error, (OSError, EOFError, paramiko.SSHException))


def bindings(manager: Any, spec: Any, snapshot: dict) -> dict[str, Callable[[], None]]:
    """Keep exact identity authority while allowing mutable Compute revisions.

    Host-key source verification remains with SSHTrustPolicy. This readback binds
    it to the same VM, name, project, disk, primary/public allocations, transport
    address and private-bootstrap hash. Security-group and HA alias changes are
    separately verified by the repair cloud owner.
    """
    client = manager._build_sdk_client(spec.region)
    if client is None:
        raise RuntimeError("Repair SSH identity requires fresh Compute evidence")
    result: dict[str, Callable[[], None]] = {}
    for name, (instance, address) in snapshot.items():
        signature = manager._vm_ha_ssh_member_signature(instance)
        expected = signature[:3] + signature[4:]
        infrastructure = deepcopy(retained_binding(instance))
        if signature[1] != name or signature[2] != manager.project_id or signature[4] != address:
            raise RuntimeError("Repair SSH retained target is inconsistent")

        def assert_current(*, expected=expected, infrastructure=infrastructure) -> None:
            current = manager._get_vm_by_id_for_vm_ha_preflight(client, expected[0])
            observed = manager._vm_ha_ssh_member_signature(current)
            if (
                observed[:3] + observed[4:] != expected
                or retained_binding(current) != infrastructure
            ):
                raise RuntimeError("Repair retained SSH identity changed")

        assert_current()
        result[name] = assert_current
    return result
