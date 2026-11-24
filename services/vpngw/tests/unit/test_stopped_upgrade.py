from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_checkpoint_upgrade import conversion  # noqa: F401
from test_vm_ha_upgrade_recovery import checkpoint

from nebius_vpngw import checkpoint_upgrade as upgrade
from nebius_vpngw import ha_repair as repair
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
from nebius_vpngw.agent.vm_ha_controller import ActionKind, ControllerAction, ControllerCheckpoint
from nebius_vpngw.deploy.stopped_upgrade import admitted, observe


def stopped_state():
    state = checkpoint()
    return replace(
        state,
        transaction=replace(
            state.transaction,
            pending_effect=None,
            completed_effects=(
                *state.transaction.completed_effects,
                "stop-upgrade-writers-node-0",
                "stop-upgrade-writers-node-1",
            ),
        ),
    )


def test_only_completed_same_operation_stop_selects_readback():
    state = stopped_state()
    artifact = dict(state.transaction.resource_bindings)["heartbeat-upgrade-artifact"]
    assert admitted(state, "node-0", artifact, resumed=True)
    assert not admitted(state, "node-0", artifact, resumed=False)
    assert not admitted(state, "node-0", "f" * 64, resumed=True)
    assert not admitted(state, "foreign", artifact, resumed=True)
    for effect in ("stop-upgrade-writers-node-0", "install-apply-lock-node-1"):
        changed = replace(
            state,
            transaction=replace(
                state.transaction,
                pending_effect=effect,
                completed_effects=tuple(
                    x for x in state.transaction.completed_effects if x != effect
                ),
            ),
        )
        assert not admitted(changed, "node-0", artifact, resumed=True)
    changed = replace(
        state,
        transaction=replace(state.transaction, pending_effect="release-node-1"),
    )
    assert not admitted(changed, "node-0", artifact, resumed=True)


@pytest.fixture
def stopped_guest(conversion, monkeypatch):  # noqa: F811
    import json

    root = conversion["state_dir"]
    monkeypatch.setattr(repair, "STATE", root)
    expected = {key: value for key, value in conversion["expected"].items() if key != "host_id"}
    digests = {"configuration": "1" * 64, "static_routes": "2" * 64, "bgp_policy": "3" * 64}
    installed = {
        "cluster_id": "cluster",
        "node": {"node_id": "node"},
        "generation": {"generation_id": "1" * 64, "digests": digests},
        "runtime_binding": {
            "shared_allocation_id": "allocation",
            "nodes": [{"node_id": "node", "compute_id": "compute"}],
        },
    }
    staged = deepcopy(installed)
    staged["generation"]["generation_id"] = expected["generation_id"]
    request = {
        "expected": expected,
        "boot_id": "boot-a",
        "allocation_id": "allocation",
        "members": {"node": "compute"},
        "units": ["controller", "timer"],
        "required_units": ["controller"],
    }
    for key, value in (("config", installed), ("staged", staged)):
        raw = yaml.safe_dump({"vm_ha": value}).encode()
        path = root / (key + ".yaml")
        repair.publish(path, raw)
        request[key + "_path"] = str(path)
        request[key + "_sha256"] = repair.sha(raw)
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "boot-a:7:enter-passive:node",
        "boot-a",
        "node",
        "allocation",
        "epoch",
        "1" * 64,
        DigestSet(**digests),
    )
    record = controller_checkpoint_to_dict(ControllerCheckpoint(sequence=7, pending_action=action))
    repair.publish(root / "controller-checkpoint.json", json.dumps(record).encode())
    # The incident's controller projection is stale. It must neither authorize
    # continuation nor veto the independently proved stopped native state.
    repair.publish(root / "status.json", b'{"pending_operation_id":"old-disable-active"}')
    manager = SimpleNamespace(unit=lambda _: {"missing": False}, stopped=lambda _: True)
    return request, manager, record


def test_stopped_readback_preserves_exact_pending_passive_intent(stopped_guest):
    request, manager, before = stopped_guest
    result = observe(request, repair, upgrade, manager)
    assert result["state"] == "stopped" and result["boot_id"] == request["boot_id"]
    assert repair.read_json(repair.STATE / "controller-checkpoint.json") == before
    assert (
        repair.read_json(repair.STATE / "status.json")["pending_operation_id"]
        == "old-disable-active"
    )


@pytest.mark.parametrize(
    "change",
    [
        "boot",
        "config",
        "staged",
        "lock",
        "forwarding",
        "accepted",
        "active",
        "missing",
        "foreign-action",
    ],
)
def test_stopped_readback_never_substitutes_for_missing_authority(stopped_guest, change):
    import json

    request, manager, record = stopped_guest
    if change == "boot":
        request["boot_id"] = "old-boot"
    elif change in {"config", "staged"}:
        request[change + "_sha256"] = "f" * 64
    elif change == "lock":
        request["expected"]["operation_id"] = "f" * 64
    elif change == "forwarding":
        repair.FORWARDING.write_text("1")
    elif change == "accepted":
        repair.publish(repair.STATE / "accepted-cloud-operation.json", b"{}")
    elif change == "active":
        manager.stopped = lambda _: False
    elif change == "missing":
        manager.unit = lambda _: {"missing": True}
    else:
        record["pending_action"]["kind"] = "attach-candidate"
        repair.publish(repair.STATE / "controller-checkpoint.json", json.dumps(record).encode())
    with pytest.raises((RuntimeError, ValueError)):
        observe(request, repair, upgrade, manager)


@pytest.mark.parametrize(
    "filename",
    [
        "rearm-cloud-operation.json",
        "standby-restoration-authorization.json",
        "standby-auto-healing-recovery.json",
        "transfer-lineage.json",
        "mtls/inhibition.json",
    ],
)
def test_stopped_continuity_rejects_durable_external_writer(stopped_guest, filename):
    from nebius_vpngw.deploy.stopped_upgrade import verify_continuity

    request, _, _ = stopped_guest
    repair.publish(repair.STATE / filename, b"{}")
    with pytest.raises(RuntimeError, match="competing writer"):
        verify_continuity(
            repair,
            upgrade,
            boot=request["boot_id"],
            expected=request["expected"],
            allocation_id=request["allocation_id"],
            installed={},
            unstarted=True,
        )


@pytest.fixture
def completed_stopped_guest(tmp_path, monkeypatch):
    import json

    from test_ha_activation import _restoration_authority

    from nebius_vpngw import ha_activation

    store, request = _restoration_authority(tmp_path, monkeypatch)
    tmp_path.chmod(0o700)
    monkeypatch.setattr(repair, "STATE", tmp_path)
    checkpoint_path = tmp_path / "controller-checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(controller_checkpoint_to_dict(ControllerCheckpoint(sequence=1)))
    )
    checkpoint_path.chmod(0o600)
    installed = yaml.safe_load(ha_activation.CONFIG.read_text())["vm_ha"]
    expected = {**request["lock"], "generation_id": "c" * 64}
    (tmp_path / "apply.lock").write_text(json.dumps(expected))
    return store, {
        "boot": "boot",
        "expected": expected,
        "allocation_id": installed["runtime_binding"]["shared_allocation_id"],
        "installed": installed,
    }


@pytest.mark.parametrize("older", [False, True])
@pytest.mark.parametrize("node", ["node-a", "node-b"])
def test_stopped_completed_history_survives_new_apply_generation(
    completed_stopped_guest, older, node
):
    from nebius_vpngw.deploy.stopped_upgrade import verify_continuity

    store, kwargs = completed_stopped_guest
    kwargs["expected"]["node_id"] = kwargs["installed"]["node"]["node_id"] = node
    if older:
        generation = kwargs["installed"]["generation"]
        generation["generation_id"] = generation["digests"]["configuration"] = "b" * 64
        binding = kwargs["installed"]["runtime_binding"]
        binding["generation_id"] = binding["configuration_digest"] = "b" * 64
    before = {p: p.read_bytes() for p in (store.path, store.receipt_path)}
    verify_continuity(repair, upgrade, **kwargs)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize(
    "change", ["running", "blocked", "receipt", "unsafe", "allocation", "node"]
)
def test_stopped_completed_history_never_waives_authority_checks(completed_stopped_guest, change):
    import json

    from nebius_vpngw.deploy.stopped_upgrade import verify_continuity

    store, kwargs = completed_stopped_guest
    if change in {"running", "blocked"}:
        record = json.loads(store.path.read_text())
        record["phase"] = change
        if change == "blocked":
            record["blocked_reason"] = "standby-restoration-blocked"
        store.path.write_text(json.dumps(record))
        assert store.load().phase.value == change
    elif change == "receipt":
        store.receipt_path.unlink()
    elif change == "unsafe":
        store.path.chmod(0o644)
    elif change == "allocation":
        kwargs["allocation_id"] = "foreign"
    else:
        kwargs["expected"]["node_id"] = "foreign"
    with pytest.raises((RuntimeError, ValueError, OSError)):
        verify_continuity(repair, upgrade, **kwargs)


def test_transfer_progress_is_not_writer_authority(stopped_guest):
    from nebius_vpngw.deploy.stopped_upgrade import verify_continuity

    request, _, _ = stopped_guest
    progress = repair.STATE / "transfer-progress.json"
    repair.publish(progress, b"{}")
    verify_continuity(
        repair,
        upgrade,
        boot=request["boot_id"],
        expected=request["expected"],
        allocation_id=request["allocation_id"],
        installed=yaml.safe_load(Path(request["config_path"]).read_text())["vm_ha"],
    )
    assert progress.read_bytes() == b"{}"


def test_table_barrier_streams_operator_continuity(completed_stopped_guest, monkeypatch):
    """A pending package cannot select the installed predecessor's old guard."""
    import contextlib
    import io
    import json

    from nebius_vpngw.deploy import ha_enforced_barrier, stopped_upgrade

    _, kwargs = completed_stopped_guest
    expected = kwargs["expected"]
    monkeypatch.setattr(
        stopped_upgrade,
        "verify_continuity",
        lambda *args, **kwargs: pytest.fail("used installed predecessor guard"),
    )

    def inspect(target, hostname, local, *, script):
        scope = {}
        setup, call = script.rsplit("print(", 1)
        exec(setup, scope)

        def observation(request, *, continuity):
            continuity(repair, upgrade, **kwargs)
            return {"boot_id": "boot", "lock": request["expected"]}

        scope["namespace"]["observe"] = observation
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec("print(" + call, scope)
        return json.loads(output.getvalue())

    result = ha_enforced_barrier.verify(
        SimpleNamespace(_inspect_vm_ha_host=inspect),
        instance=SimpleNamespace(
            hostname="gateway", vm_ha_node=SimpleNamespace(node_id=expected["node_id"])
        ),
        target="gateway",
        local={},
        binding=SimpleNamespace(
            cluster_id=expected["cluster_id"],
            shared_allocation_id=kwargs["allocation_id"],
            route_runtime_id="runtime",
        ),
        receipt=SimpleNamespace(
            generation_id=expected["generation_id"], staged_file_sha256="d" * 64
        ),
        operation=expected["operation_id"],
    )
    assert result["lock"]["generation_id"] == expected["generation_id"]
