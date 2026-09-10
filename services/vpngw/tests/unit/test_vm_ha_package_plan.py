from __future__ import annotations

import time

import pytest

from nebius_vpngw import ordinary_operations as ops
from nebius_vpngw.deploy import ordinary_remote


def test_real_observation_accepts_only_handoff_owned_runtime_changes(
    monkeypatch, tmp_path, ordinary_operation_guest
):
    """The actual inspector/journal reproduce the original stale predecessor."""
    from nebius_vpngw.deploy.vm_ha_package import package_predecessor

    config = tmp_path / "resolved.yaml"
    config.write_text("gateway: {}\n")
    monkeypatch.setattr(ordinary_remote, "CONFIG", config)
    monkeypatch.setattr(ordinary_remote, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary_remote, "ROUTING_LOCK", tmp_path / "routes.lock")
    monkeypatch.setattr(ordinary_remote, "DEADLINE", time.monotonic() + 600)
    state = {"agent": "active"}
    monkeypatch.setattr(ordinary_remote, "command", lambda *a, **kw: (0, state["agent"]))
    manifest = {"files": {}, "assets": {}, "package_identity": "1" * 64, "route_projection": []}
    before = ordinary_remote.inspect(manifest, verify_runtime=False)
    manager = ordinary_operation_guest(time.monotonic() + 600)
    operation = ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        purpose="ha-handoff",
    )
    state["agent"] = "inactive"
    after = ordinary_remote.inspect(manifest, verify_runtime=False)
    assert before != after
    assert after["operation"]["recoverable"] is False
    assert package_predecessor(before) == package_predecessor(after)
    for key in package_predecessor(before):
        changed = dict(after, **{key: "unapproved"})
        assert package_predecessor(before) != package_predecessor(changed), key
    assert operation.value["purpose"] == "ha-handoff"


def test_package_inspection_never_enters_ordinary_admission(monkeypatch, tmp_path):
    config = tmp_path / "resolved.yaml"
    config.write_text("gateway: {}\n")
    monkeypatch.setattr(ordinary_remote, "CONFIG", config)
    boot = tmp_path / "boot"
    boot.write_text("12345678-1234-1234-1234-123456789012")
    monkeypatch.setattr(ordinary_remote, "BOOT", boot)

    def forbidden(*args, **kwargs):
        pytest.fail("package planning must not consult ordinary service/journal admission")

    monkeypatch.setattr(ordinary_remote, "command", forbidden)
    monkeypatch.setattr(ops, "Manager", forbidden)
    result = ordinary_remote.inspect({"files": {}, "assets": {}}, runtime_admission=False)
    assert result["config_sha256"] == ordinary_remote.sha(config.read_bytes())
