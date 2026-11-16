import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nebius_vpngw import ha_activation as guest
from nebius_vpngw.apply_approval import digest


def _restoration_authority(tmp_path, monkeypatch, *, node="node-a", active=False):
    import hashlib

    import yaml
    from tests.unit.test_vm_ha_restoration import _completed
    from tests.unit.test_vm_ha_runtime_ports import _runtime_config

    store = _completed(tmp_path)
    for path in (store.path, store.receipt_path):
        path.chmod(0o600)
    runtime = _runtime_config(tmp_path)
    runtime["node"]["node_id"] = node
    config, boot, forwarding = (tmp_path / name for name in ("config", "boot", "forwarding"))
    config.write_text(yaml.safe_dump({"vm_ha": runtime}))
    boot.write_text("boot")
    forwarding.write_text("1" if active else "0")
    lock = {
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
        "apply_locked": True,
        "cluster_id": "cluster-a",
        "node_id": node,
        "generation_id": "a" * 64,
        "operation_id": "f" * 64,
    }
    for name, value in (
        ("apply.lock", lock),
        (
            "guard.json",
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "guard_boot_id": "boot",
                "node_id": node,
                "data_plane_mode": "active" if active else "passive",
            },
        ),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    monkeypatch.setattr(guest, "STATE", tmp_path)
    monkeypatch.setattr(guest, "CONFIG", config)
    monkeypatch.setattr(guest, "BOOT", boot)
    monkeypatch.setattr(guest, "FORWARDING", forwarding)
    return store, {"lock": lock, "configuration": hashlib.sha256(config.read_bytes()).hexdigest()}


@pytest.mark.parametrize("node", ["node-a", "node-b"])
@pytest.mark.parametrize("active", [False, True])
def test_completed_restoration_is_history_not_an_activation_writer(
    tmp_path, monkeypatch, node, active
):
    store, request = _restoration_authority(tmp_path, monkeypatch, node=node, active=active)
    before = {path: path.read_bytes() for path in (store.path, store.receipt_path)}
    assert guest.require_authority(request, active_unlock=active) == "boot"
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize(
    "phase", ["armed", "committed", "start-accepted", "running", "awaiting-standby", "blocked"]
)
def test_unfinished_or_blocked_restoration_never_admits_activation(tmp_path, monkeypatch, phase):
    store, request = _restoration_authority(tmp_path, monkeypatch)
    record = json.loads(store.path.read_text())
    record["phase"] = phase
    if phase == "armed":
        for key in ("promotion_receipt_id", "ownership_epoch", "route_operation_id"):
            record[key] = None
    if phase == "blocked":
        record["blocked_reason"] = "standby-restoration-blocked"
    store.path.write_text(json.dumps(record))
    assert store.load().phase.value == phase
    with pytest.raises(RuntimeError, match="unresolved restoration authority"):
        guest.require_authority(request)


@pytest.mark.parametrize(
    "change", ["cluster", "allocation", "members", "node", "generation", "malformed"]
)
def test_completed_restoration_requires_exact_current_binding(tmp_path, monkeypatch, change):
    import hashlib

    import yaml

    from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding

    _store, request = _restoration_authority(tmp_path, monkeypatch)
    config = yaml.safe_load(guest.CONFIG.read_text())
    runtime = config["vm_ha"]
    binding = runtime["runtime_binding"]
    if change == "cluster":
        runtime["cluster_id"] = binding["cluster_id"] = "cluster-foreign"
    elif change == "allocation":
        binding["shared_allocation_id"] = "allocation-foreign"
    elif change == "members":
        binding["nodes"][1]["node_id"] = "node-foreign"
    elif change == "node":
        runtime["node"]["node_id"] = "node-b"
    elif change == "generation":
        binding["generation_id"] = binding["configuration_digest"] = "b" * 64
    else:
        binding["unexpected"] = True
    binding["route_runtime_id"] = VMHARuntimeBinding.derive_route_runtime_id(
        binding["cluster_id"],
        binding["shared_allocation_id"],
        tuple(VMHARouteTarget.model_validate(target) for target in binding["route_targets"]),
    )
    guest.CONFIG.write_text(yaml.safe_dump(config))
    request["configuration"] = hashlib.sha256(guest.CONFIG.read_bytes()).hexdigest()
    with pytest.raises((RuntimeError, ValueError)):
        guest.require_authority(request)


def test_completed_previous_generation_is_preserved_as_history(tmp_path, monkeypatch):
    import hashlib

    import yaml

    store, request = _restoration_authority(tmp_path, monkeypatch)
    before = store.path.read_bytes(), store.receipt_path.read_bytes()
    config = yaml.safe_load(guest.CONFIG.read_text())
    runtime = config["vm_ha"]
    runtime["generation"]["generation_id"] = "b" * 64
    runtime["runtime_binding"]["generation_id"] = "b" * 64
    runtime["runtime_binding"]["configuration_digest"] = "b" * 64
    guest.CONFIG.write_text(yaml.safe_dump(config))
    request["configuration"] = hashlib.sha256(guest.CONFIG.read_bytes()).hexdigest()
    request["lock"]["generation_id"] = "b" * 64
    (tmp_path / "apply.lock").write_text(json.dumps(request["lock"]))
    assert guest.require_authority(request) == "boot"
    assert (store.path.read_bytes(), store.receipt_path.read_bytes()) == before


@pytest.mark.parametrize("target", ["path", "receipt_path"])
@pytest.mark.parametrize("change", ["symlink", "permissions", "malformed", "changed-during-read"])
def test_restoration_evidence_must_remain_safe_and_stable(tmp_path, monkeypatch, target, change):
    store, request = _restoration_authority(tmp_path, monkeypatch)
    path = getattr(store, target)
    if change == "symlink":
        destination = path.with_suffix(".saved")
        path.rename(destination)
        path.symlink_to(destination)
    elif change == "permissions":
        path.chmod(0o644)
    elif change == "malformed":
        path.write_text('{"phase": "completed"}')
    else:
        original = guest.ha_repair.read_json
        count = 0

        def read(current):
            nonlocal count
            value = original(current)
            if current == path:
                count += 1
                if count == 2:
                    value["unexpected"] = True
            return value

        monkeypatch.setattr(guest.ha_repair, "read_json", read)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        guest.require_authority(request)


def test_completed_restoration_without_its_receipt_grants_no_authority(tmp_path, monkeypatch):
    store, request = _restoration_authority(tmp_path, monkeypatch)
    store.receipt_path.unlink()
    with pytest.raises(FileNotFoundError):
        guest.require_authority(request)


def test_completed_restoration_rejects_a_different_promotion_receipt(tmp_path, monkeypatch):
    store, request = _restoration_authority(tmp_path, monkeypatch)
    receipt = json.loads(store.receipt_path.read_text())
    receipt["receipt_id"] = "0" * 64
    store.receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(RuntimeError, match="promotion receipt digest is invalid"):
        guest.require_authority(request)


@pytest.mark.parametrize(
    "name",
    [
        "mtls/inhibition.json",
        "standby-replacement.inhibition.json",
        "accepted-cloud-operation.json",
        "rearm-cloud-operation.json",
        "standby-auto-healing-recovery.json",
    ],
)
def test_completed_restoration_does_not_override_another_inhibition(tmp_path, monkeypatch, name):
    _store, request = _restoration_authority(tmp_path, monkeypatch)
    path = tmp_path / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("{}")
    with pytest.raises(RuntimeError, match="another inhibition owner"):
        guest.require_authority(request)


def observation(*, failed="strongswan.service"):
    return {
        "boot": "boot",
        "clock": 100.0,
        "materialized": False,
        "complete": False,
        "units": {
            unit: {
                "LoadState": "loaded",
                "ActiveState": "failed" if unit == failed else "active",
                "SubState": "failed" if unit == failed else "running",
                "Job": "",
                "MainPID": "42",
                "ControlPID": "0",
            }
            for unit in guest.UNITS
        },
    }


def test_service_repair_resets_native_start_limit_but_controller_owns_activation(monkeypatch):
    observed = observation()
    monkeypatch.setattr(
        guest, "read_attempt", lambda: {"identity": "a" * 64, "scope": "activation"}
    )
    monkeypatch.setattr(guest, "command_deadline", lambda: 220)
    monkeypatch.setattr(guest.time, "monotonic", lambda: 100)
    monkeypatch.setattr(guest, "require_authority", lambda _: "boot")
    monkeypatch.setattr(guest, "inspect", lambda *_, **__: observed)
    monkeypatch.setattr(
        guest,
        "unit_state",
        lambda *_a, **_k: {"Job": "", "ControlPID": "0", "ActiveState": "active"},
    )
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(guest.subprocess, "run", run)
    guest.repair_service(
        {
            "identity": "a" * 64,
            "action_kind": "service",
            "fingerprint": digest({k: v for k, v in observed.items() if k != "clock"}),
        }
    )
    commands = [call.args[0] for call in run.call_args_list]
    assert ["systemctl", "reset-failed", "strongswan.service"] in commands
    restarts = [command for command in commands if "restart" in command]
    assert restarts == [
        ["systemctl", "--no-block", "--job-mode=fail", "restart", "nebius-vpngw-vm-ha.service"]
    ]


def test_changed_service_evidence_never_dispatches(monkeypatch):
    monkeypatch.setattr(
        guest, "read_attempt", lambda: {"identity": "a" * 64, "scope": "activation"}
    )
    monkeypatch.setattr(guest, "command_deadline", lambda: 220)
    monkeypatch.setattr(guest, "inspect", lambda *_, **__: observation())
    run = Mock()
    monkeypatch.setattr(guest.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="evidence changed"):
        guest.repair_service({"identity": "a" * 64, "fingerprint": "b" * 64})
    run.assert_not_called()


def test_complete_fenced_resume_has_no_service_or_configuration_mutation(monkeypatch):
    observed = observation(failed=None)
    observed.update(materialized=True, complete=True)
    monkeypatch.setattr(
        guest, "read_attempt", lambda: {"identity": "a" * 64, "scope": "activation"}
    )
    monkeypatch.setattr(guest, "command_deadline", lambda: 220)
    monkeypatch.setattr(guest, "inspect", lambda *_, **__: observed)
    run, publish = Mock(), Mock()
    monkeypatch.setattr(guest.subprocess, "run", run)
    monkeypatch.setattr(guest.ha_repair, "publish", publish)
    assert (
        guest.repair_service(
            {
                "identity": "a" * 64,
                "action_kind": "configuration",
                "resume_phase": "verify-active-forwarding-and-routes",
                "fingerprint": digest({k: v for k, v in observed.items() if k != "clock"}),
            }
        )
        == observed
    )
    run.assert_not_called()
    publish.assert_not_called()


def test_guest_deadline_does_not_add_transport_latency(monkeypatch):
    monkeypatch.setattr(guest.time, "monotonic", lambda: 110)
    monkeypatch.setattr(guest, "require_authority", lambda _: "boot")
    monkeypatch.setattr(guest, "read_attempt", lambda: None)
    published = []
    monkeypatch.setattr(
        guest.ha_repair, "publish", lambda _path, payload: published.append(json.loads(payload))
    )
    request = {"started_at": 220.0, "identity": "a" * 64, "lock": {}, "configuration": "b" * 64}
    assert guest.arm(request)["started_at"] == 220.0
    assert published[0]["started_at"] == 220.0
    monkeypatch.setattr(guest, "read_attempt", lambda: published[0])
    monkeypatch.setattr(guest.time, "monotonic", lambda: 115)
    assert guest.arm(request)["started_at"] == 220.0
    assert len(published) == 1


def test_expired_guest_budget_does_not_arm(monkeypatch):
    monkeypatch.setattr(guest.time, "monotonic", lambda: 220)
    publish = Mock()
    monkeypatch.setattr(guest.ha_repair, "publish", publish)
    with pytest.raises(ValueError, match="budget"):
        guest.arm({"started_at": float("inf")})
    publish.assert_not_called()


def test_live_guest_writer_blocks_observation_and_repair(tmp_path, monkeypatch, capsys):
    import base64
    import fcntl
    import sys

    path = tmp_path / "activation.lock"
    monkeypatch.setattr(guest, "WRITER", path)
    inspected = Mock()
    monkeypatch.setattr(guest, "inspect", inspected)
    monkeypatch.setattr(
        sys, "argv", ["ha_activation", base64.b64encode(b'{"action":"inspect"}').decode()]
    )
    with path.open("w") as writer:
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            guest.main()
    inspected.assert_not_called()
    assert "activation-authority-or-observation-unavailable" in capsys.readouterr().out


def test_active_owner_permit_is_only_for_own_unlock(tmp_path, monkeypatch):
    import hashlib

    config, boot, forwarding = (tmp_path / name for name in ("config", "boot", "forwarding"))
    config.write_text("approved configuration")
    boot.write_text("boot")
    forwarding.write_text("1")
    monkeypatch.setattr(guest, "STATE", tmp_path)
    monkeypatch.setattr(guest, "CONFIG", config)
    monkeypatch.setattr(guest, "BOOT", boot)
    monkeypatch.setattr(guest, "FORWARDING", forwarding)
    monkeypatch.setattr(guest.ha_repair, "exact_lock", lambda *_: None)
    monkeypatch.setattr(
        guest.ha_repair,
        "read_json",
        lambda _: {
            "schema": "nebius-vpngw/vm-ha-status-v1",
            "guard_boot_id": "boot",
            "node_id": "node",
            "data_plane_mode": "active",
        },
    )
    request = {
        "lock": {"node_id": "node"},
        "configuration": hashlib.sha256(config.read_bytes()).hexdigest(),
    }
    assert guest.require_authority(request, active_unlock=True) == "boot"
    with pytest.raises(RuntimeError, match="forwarding fence"):
        guest.require_authority(request)


def test_failed_cleanup_keeps_expired_native_enforcement(tmp_path, monkeypatch):
    import base64
    import hashlib
    import sys
    import time

    config, boot = tmp_path / "config", tmp_path / "boot"
    config.write_text("approved configuration")
    boot.write_text("boot")
    path = tmp_path / "activation-attempt.json"
    path.write_text("present")
    state = {
        "value": {
            "schema": guest.SCHEMA,
            "boot": "boot",
            "identity": "a" * 64,
            "configuration": hashlib.sha256(config.read_bytes()).hexdigest(),
            "lock": {},
            "started_at": time.monotonic() + 100,
        }
    }
    monkeypatch.setattr(guest, "STATE", tmp_path)
    monkeypatch.setattr(guest, "CONFIG", config)
    monkeypatch.setattr(guest, "BOOT", boot)
    monkeypatch.setattr(guest, "WRITER", tmp_path / "writer.lock")
    monkeypatch.setattr(guest, "read_attempt", lambda **_: state["value"])
    monkeypatch.setattr(
        guest.ha_repair, "publish", lambda _path, raw: state.update(value=json.loads(raw))
    )
    request = {**state["value"], "action": "finish", "verified": False}
    monkeypatch.setattr(
        sys, "argv", ["helper", base64.b64encode(json.dumps(request).encode()).decode()]
    )
    guest.main()
    assert path.exists()
    with pytest.raises(RuntimeError, match="retired"):
        guest.command_deadline(state_dir=tmp_path)
    request["verified"] = True
    monkeypatch.setattr(
        sys, "argv", ["helper", base64.b64encode(json.dumps(request).encode()).decode()]
    )
    guest.main()
    assert not path.exists()


def test_canonical_cold_guard_admits_exact_installed_configuration(tmp_path, monkeypatch):
    import hashlib

    import yaml
    from tests.unit.test_vm_ha_runtime_ports import _runtime_config

    runtime = _runtime_config(tmp_path)
    config, boot, forwarding = (tmp_path / name for name in ("config", "boot", "forwarding"))
    config.write_text(yaml.safe_dump({"vm_ha": runtime}))
    boot.write_text("boot")
    forwarding.write_text("0")
    monkeypatch.setattr(guest, "STATE", tmp_path)
    monkeypatch.setattr(guest, "CONFIG", config)
    monkeypatch.setattr(guest, "BOOT", boot)
    monkeypatch.setattr(guest, "FORWARDING", forwarding)
    # Exercise the actual systemd startup/shutdown guard writer contract.
    guard = guest.ha_repair.cold_guard(
        state_dir=tmp_path,
        boot_id="boot",
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        writer=lambda *_: None,
        unlink=lambda *_: None,
    )
    assert "node_id" not in guard
    monkeypatch.setattr(guest.ha_repair, "read_json", lambda _: guard)
    monkeypatch.setattr(guest.ha_repair, "exact_lock", lambda *_: None)
    request = {
        "lock": {"cluster_id": "cluster-a", "node_id": "node-a", "generation_id": "a" * 64},
        "configuration": hashlib.sha256(config.read_bytes()).hexdigest(),
    }
    assert guest.require_authority(request) == "boot"
    wrong = {**request, "lock": {**request["lock"], "node_id": "node-b"}}
    with pytest.raises(RuntimeError, match="installed node binding"):
        guest.require_authority(wrong)
    forwarding.write_text("1")
    with pytest.raises(RuntimeError, match="forwarding fence"):
        guest.require_authority(request)


def test_unlock_only_scope_survives_arm_and_rejects_later_service_repair(monkeypatch):
    import time

    monkeypatch.setattr(guest, "require_authority", lambda *_a, **_k: "boot")
    monkeypatch.setattr(guest, "read_attempt", lambda: None)
    monkeypatch.setattr(guest.ha_repair, "publish", lambda *_: None)
    attempt = guest.arm(
        {
            "started_at": time.monotonic() + 10,
            "identity": "a" * 64,
            "lock": {},
            "configuration": "b" * 64,
            "unlock_only": True,
        }
    )
    assert attempt["scope"] == "unlock"
    monkeypatch.setattr(guest, "read_attempt", lambda: attempt)
    observe = Mock()  # The member may now be passive; that cannot widen this permit.
    monkeypatch.setattr(guest, "inspect", observe)
    with pytest.raises(RuntimeError, match="service-scoped"):
        guest.repair_service({"identity": attempt["identity"]})
    observe.assert_not_called()
