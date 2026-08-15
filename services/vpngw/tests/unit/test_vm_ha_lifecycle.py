from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
)


def _state(status: VMHALifecycleStatus = VMHALifecycleStatus.ACTIVE) -> VMHALifecycleState:
    return VMHALifecycleState(
        status=status,
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_id="allocation-1",
        allocation_name="gateway-cluster-shared-private-ip",
        members=(
            VMHALifecycleMember(0, "gateway-0", "node-a", "active", "vm-0", "eth0", "ip-0"),
            VMHALifecycleMember(1, "gateway-1", "node-b", "passive", "vm-1", "eth0", "ip-1"),
        ),
    )


def test_lifecycle_transitions_preserve_identity_and_verify_terminal_tombstone(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)

    active = _state()
    store.write_verified(active)
    removing = active.with_status(VMHALifecycleStatus.REMOVAL_IN_PROGRESS)
    store.write_verified(removing)
    removed = removing.with_status(VMHALifecycleStatus.REMOVED)
    store.write_verified(removed)

    observed = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
    assert observed == removed
    assert observed.identity_sha256 == active.identity_sha256
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("field", ["status", "identity_sha256"])
def test_lifecycle_tamper_fails_closed(tmp_path: Path, field: str) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(_state())
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload[field] = "unexpected"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="lifecycle"):
        store.read(expected_project_id="project-1", expected_gateway_name="gateway")


def test_lifecycle_scope_mismatch_and_symlink_fail_closed(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(_state())

    with pytest.raises(ValueError, match="project identity"):
        store.read(expected_project_id="project-2", expected_gateway_name="gateway")
    original = tmp_path / "original.json"
    store.path.replace(original)
    store.path.symlink_to(original)
    with pytest.raises(ValueError, match="regular file"):
        store.read(expected_project_id="project-1", expected_gateway_name="gateway")
