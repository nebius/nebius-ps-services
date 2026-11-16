import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from nebius_vpngw import cli, repair_state
from nebius_vpngw.deploy import repair_apply
from nebius_vpngw.deploy.apply_result import ApplyReport


@pytest.mark.parametrize("worker_state,observed_at", [
    ("RUNNING_OWNED", 1801), ("BLOCKED", 1), ("BLOCKED", 1801),
])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_retained_repair_worker_has_specific_public_recovery(
    monkeypatch, tmp_path, worker_state, observed_at, output_format,
):
    from nebius_vpngw.deploy import pair_phases
    from nebius_vpngw.deploy.recovery import Invocation, RepairBlocked, advice

    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.operation = "a" * 32
    coordinator.instances = {"node-a": object()}
    coordinator.context = {"guards": {"node-a": {}}}
    coordinator.cloud = SimpleNamespace(start_guarded=lambda *_a, **_k: None)
    coordinator.wait_access = lambda _: None
    coordinator.journal = SimpleNamespace(load=lambda: {"id": coordinator.operation, "phase": "staged"})
    coordinator.package_request = lambda _: {"identity": "private-worker-sentinel"}
    calls = []

    def remote(_name, action, **_payload):
        calls.append(action)
        return {"state": worker_state}

    coordinator.remote = remote
    with monkeypatch.context() as clock:
        moments = iter([0, observed_at])
        clock.setattr(repair_apply.time, "monotonic", lambda: next(moments))
        with pytest.raises(RuntimeError) as raised:
            coordinator.install()
    assert calls == ["isolated", "worker-start"]
    failure = raised.value
    path = tmp_path / "gateway's config.yaml"
    path.touch()
    monkeypatch.setattr(cli, "load_local_config", lambda _: {})
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: SimpleNamespace())

    def fail(**kwargs):
        kwargs["result_report"].started = True
        raise failure

    monkeypatch.setattr(cli, "_apply_impl", fail)
    reports = []
    monkeypatch.setattr(pair_phases, "report", reports.append)
    result = CliRunner().invoke(cli.app, [
        "apply", "-c", str(path), "--repair", "--output-format", output_format,
    ])
    assert result.exit_code == 1
    assert len(reports) == 1
    payload = reports[0]
    pending = worker_state == "RUNNING_OWNED"
    code = "repair-worker-pending" if pending else "repair-worker-blocked"
    assert payload["outcome"] == ("pending" if pending else "failed")
    assert payload.get("pending_reason") == (code if pending else None)
    recovery = payload["recovery"]
    assert recovery["reason_code"] == code
    assert "unknown" not in recovery["reason"]
    expected = ["nebius-vpngw", "apply", "--local-config-file", str(path), "--repair"]
    assert shlex.split(recovery["resume_command"]) == expected
    assert shlex.split(recovery["repair_command"]) == expected
    assert recovery["destroy_command"] is None
    assert recovery["repair_prerequisite"]
    assert "private-worker-sentinel" not in result.output
    if output_format == "json":
        assert json.loads(result.stdout) == json.loads(json.dumps(payload))
    else:
        assert result.stdout.count("Reason:") == 1
        assert shlex.join(expected) in result.stdout
    blocked = advice(Invocation("apply", repair=True), RepairBlocked(
        "repair-worker-blocked", ownership_established=True,
    ))
    assert blocked.destroy_command is None


@pytest.mark.parametrize(
    "option",
    [
        "dry_run",
        "recreate_gw",
        "approve_disruption",
        "resume_replacement",
        "prepare_vm_ha_peer_rotation",
        "approve_vm_ha_migration",
        "recover_vm_ha_migration",
        "replace_failed_vm_ha_passive",
        "replace_missing_vm_ha_standby",
        "sa",
    ],
)
def test_repair_rejects_other_operation_before_config_or_cloud(monkeypatch, option):
    monkeypatch.setattr(
        cli, "_resolve_local_config", lambda *_a, **_k: pytest.fail("config access")
    )
    with pytest.raises(typer.BadParameter, match="separate confirmed"):
        cli._apply_impl(repair=True, **{option: True})


def test_declining_repair_creates_no_journal_or_effect(monkeypatch, tmp_path):
    monkeypatch.setattr(repair_apply, "confirm", lambda _admission: False)
    monkeypatch.setattr(repair_apply, "Coordinator", lambda *_a, **_k: pytest.fail("effect"))
    repair_apply.execute(SimpleNamespace(root=tmp_path / "absent"), report=ApplyReport())
    assert not (tmp_path / "absent").exists()


def test_missing_resolver_is_reported_before_reconstruction_effects(monkeypatch, tmp_path):
    monkeypatch.setattr(repair_apply.ordinary_apply.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="install uv on this controller"):
        repair_apply.require_package_prerequisites(
            SimpleNamespace(root=tmp_path / "repair", intent={}, targets={"gateway-0": "ip"})
        )


@pytest.mark.parametrize("phase", ["confirmed", "isolated", "installed", "verified"])
def test_frozen_repair_resume_does_not_require_new_dependency_resolution(
    monkeypatch, tmp_path, phase
):
    intent = {"artifact": "same", "targets": [{"name": "gateway-0"}]}
    operation = {"id": "a" * 32, "intent": intent, "phase": phase}
    monkeypatch.setattr(
        repair_apply.state, "Journal", lambda _: SimpleNamespace(load=lambda: operation)
    )
    monkeypatch.setattr(repair_apply.ordinary_apply.shutil, "which", lambda _: None)
    root = tmp_path / "repair"
    if phase == "confirmed":
        repair_state.publish(
            root / "runs" / operation["id"] / "context.json",
            {"intent": repair_state.digest(intent), "packages": {"gateway-0": {"wheels": {}}}},
        )
    repair_apply.require_package_prerequisites(
        SimpleNamespace(root=root, intent=intent, targets={"gateway-0": "ip"})
    )


@pytest.mark.parametrize("changed_intent", [False, True])
def test_unresolved_or_successor_repair_requires_resolver(monkeypatch, tmp_path, changed_intent):
    intent = {"artifact": "new"}
    operation = {
        "id": "a" * 32,
        "intent": {"artifact": "old"} if changed_intent else intent,
        "phase": "installed" if changed_intent else "confirmed",
    }
    monkeypatch.setattr(
        repair_apply.state, "Journal", lambda _: SimpleNamespace(load=lambda: operation)
    )
    monkeypatch.setattr(repair_apply.ordinary_apply.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="install uv on this controller"):
        repair_apply.require_package_prerequisites(
            SimpleNamespace(root=tmp_path / "repair", intent=intent, targets={"gateway-0": "ip"})
        )


def test_stopped_ordinary_repair_admission_does_not_require_preboot_ssh(monkeypatch, tmp_path):
    monkeypatch.setattr(repair_apply.ordinary_apply, "require_dependency_resolver", lambda: "/uv")
    name, address = "gateway-0", "192.0.2.1"
    checked, options = [], {}
    retained = {"compute": "vm-a", "project": "project-a", "disk": "disk-a", "nics": []}
    manager = SimpleNamespace(
        project_id="project-a",
        _management_key_path=None,
        discover_ordinary_gateway_members=lambda _: {name: address},
        _ordinary_ssh_preflight_snapshot={name: (SimpleNamespace(state="STOPPED"), address)},
        ordinary_ssh_trust_bindings=lambda *_a, **_k: {
            name: lambda: checked.append("cloud-identity")
        },
        prepare_ordinary_ssh_policy=lambda *_a, **_k: pytest.fail("preboot SSH probe"),
    )
    monkeypatch.setattr(repair_apply, "retained_binding", lambda _: retained)
    monkeypatch.setattr(repair_apply.repair_ssh, "bindings", lambda *_: {name: lambda: None})
    monkeypatch.setattr(cli, "_ordinary_ssh_trust_scope", lambda *_a, **_k: object())
    monkeypatch.setattr(
        repair_apply, "require_vm_ha_ssh_policy", lambda *_a, **kw: options.update(kw)
    )
    monkeypatch.setattr(repair_apply, "SSHPush", lambda **_: SimpleNamespace())
    wheel = tmp_path / "release.whl"
    wheel.write_bytes(b"frozen-artifact")
    result = repair_apply.inspect(
        manager=manager,
        plan=SimpleNamespace(
            vm_ha=None, gateway_group=SimpleNamespace(name="gateway", region="region-a")
        ),
        local={"gateway_group": {"vm_spec": {}}},
        path=tmp_path / "config.yaml",
        instances=(SimpleNamespace(hostname=name, config_yaml="selected-config"),),
        wheel=wheel,
    )
    assert checked == ["cloud-identity"]
    assert options["retained_hosts"] == {name} and options["enrollment_hosts"] == ()
    assert options["persist_default_host_keys"] is False
    assert result.intent["targets"][0]["compute"] == "vm-a"


def test_dependency_tags_are_computed_for_guest_not_controller():
    value = repair_apply.dependency_environment(
        {
            "python": "3.12.3",
            "implementation": "cpython",
            "machine": "x86_64",
            "libc": ["glibc", "2.39"],
        }
    )
    assert "cp312-cp312-manylinux_2_39_x86_64" in value["wheel_tags"]
    assert "py3-none-any" in value["wheel_tags"]
    assert value["markers"]["sys_platform"] == "linux"
    assert value["markers"]["python_version"] == "3.12"


@pytest.mark.parametrize("error", [EOFError(), ConnectionResetError(), "banner"])
def test_repair_waits_for_authenticated_ssh_after_boot_transport_failure(monkeypatch, error):
    import paramiko

    if error == "banner":
        error = paramiko.SSHException("Error reading SSH protocol banner")
    calls = []
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = SimpleNamespace(
        intent={"exact": "retained-target"}, plan=SimpleNamespace(vm_ha=None)
    )
    coordinator.operation = "a" * 32
    coordinator.journal = SimpleNamespace(load=lambda: {"id": "a" * 32, "phase": "confirmed"})

    def remote(name, action, **payload):
        calls.append((name, action, payload))
        if len(calls) == 1:
            raise error
        return {"operation": "confirmed"}

    coordinator.remote = remote
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: None)
    coordinator.wait_access("node-a")
    assert calls == [("node-a", "admit", {"intent": coordinator.admission.intent})] * 2


@pytest.mark.parametrize("failure", ["identity", "authentication", "permission", "semantic"])
def test_repair_readiness_does_not_retry_identity_or_authority_failure(monkeypatch, failure):
    import paramiko

    errors = {
        "identity": paramiko.SSHException("Server host key differs"),
        "authentication": paramiko.AuthenticationException("denied"),
        "permission": PermissionError("denied"),
        "semantic": RuntimeError("Repair retained SSH identity changed"),
    }
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = SimpleNamespace(intent={})

    def remote(*_a, **_kw):
        raise errors[failure]

    coordinator.remote = remote
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(type(errors[failure])):
        coordinator.wait_access("node-a")


def test_repair_boot_transport_retry_retains_bounded_deadline(monkeypatch):
    import paramiko

    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = SimpleNamespace(intent={}, path=Path("gateway.config.yaml"))

    def remote(*_a, **_kw):
        raise paramiko.SSHException("Error reading SSH protocol banner")

    coordinator.remote = remote
    times = iter((0, 0, 3))
    monkeypatch.setattr(repair_apply.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="could not reach authenticated SSH") as failure:
        coordinator.wait_access("node-a", timeout=2)
    from nebius_vpngw.deploy.recovery import Invocation, advice

    recovery = advice(Invocation("apply", Path("gateway.config.yaml"), repair=True), failure.value)
    assert recovery.destroy_command == "nebius-vpngw destroy --local-config-file gateway.config.yaml"
    assert recovery.fresh_apply_command == "nebius-vpngw apply --local-config-file gateway.config.yaml"


def test_resume_waits_for_guest_writer_across_real_shared_error_boundary(monkeypatch):
    import json
    from unittest.mock import Mock

    from nebius_vpngw import repair_bootstrap
    from nebius_vpngw.deploy import ordinary_apply

    clients = []

    def client_factory():
        busy = not clients
        response = (
            {"status": "failed", "stage": "predecessor", "error_code": "repair_writer_busy"}
            if busy
            else {"operation": "a" * 32, "phase": "installed"}
        )
        channel = Mock()
        channel.send.side_effect = len
        channel.recv_ready.side_effect = [True, False, False]
        channel.recv.return_value = json.dumps(response).encode()
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = int(busy)
        client = Mock()
        client.exec_command.return_value = (
            SimpleNamespace(channel=channel),
            SimpleNamespace(channel=channel),
            Mock(),
        )
        clients.append(client)
        return client

    ssh = SimpleNamespace(
        _ssh_policy=None,
        _ensure_paramiko=lambda: SimpleNamespace(SSHClient=client_factory),
        _connect_client=Mock(),
    )
    monkeypatch.setattr(ordinary_apply, "configure_paramiko_host_verification", Mock())
    monkeypatch.setattr(ordinary_apply.ingress_ssh, "preflight", Mock())
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: None)
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = SimpleNamespace(
        intent={"exact": "retained-target"}, plan=SimpleNamespace(vm_ha=None)
    )
    coordinator.operation = "a" * 32
    coordinator.journal = SimpleNamespace(load=lambda: {"id": "a" * 32, "phase": "confirmed"})
    calls = []

    def remote(name, action, **payload):
        calls.append((name, action, payload))
        return ordinary_apply.remote(
            ssh,
            "192.0.2.1",
            SimpleNamespace(hostname=name, config_yaml="gateway: {}"),
            {},
            {"action": action, "operation": "a" * 32, **payload},
            bootstrap=repair_bootstrap,
        )

    coordinator.remote = remote
    coordinator.wait_access("node-a")
    assert calls == [("node-a", "admit", {"intent": coordinator.admission.intent})] * 2
    assert len(clients) == 2 and all(client.close.call_count == 1 for client in clients)


@pytest.mark.parametrize("settles", [False, True])
def test_guest_writer_wait_has_its_own_fixed_bound(monkeypatch, settles):
    from nebius_vpngw.deploy.ordinary_apply import RemoteGuestWriterBusy

    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = SimpleNamespace(intent={}, plan=SimpleNamespace(vm_ha=None))
    coordinator.operation = "confirmed"
    coordinator.journal = SimpleNamespace(load=lambda: {"id": "confirmed", "phase": "confirmed"})
    calls = []

    def remote(*_args, **_kwargs):
        calls.append(True)
        if not settles or len(calls) == 1:
            raise RemoteGuestWriterBusy("active writer")
        return {"operation": "confirmed"}

    coordinator.remote = remote
    times = iter((0, 10, 21))
    monkeypatch.setattr(repair_apply.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: None)
    if settles:
        coordinator.wait_access("node-a", timeout=2, writer_timeout=20)
    else:
        with pytest.raises(RuntimeError, match="waiting for the existing guest deployment writer"):
            coordinator.wait_access("node-a", timeout=2, writer_timeout=20)
    assert len(calls) == 2


@pytest.mark.parametrize("denied", [False, True])
def test_repair_boot_retry_crosses_real_shared_transport_error_boundary(monkeypatch, denied):
    from nebius_vpngw import repair_bootstrap
    from nebius_vpngw.deploy import ordinary_apply

    closed, calls = [], []
    client = SimpleNamespace(close=lambda: closed.append(True))
    error = PermissionError("denied") if denied else TimeoutError("timed out")

    def connect(*_a, **_kw):
        raise error

    ssh = SimpleNamespace(
        _ssh_policy=None,
        _ensure_paramiko=lambda: SimpleNamespace(SSHClient=lambda: client),
        _connect_client=connect,
    )
    monkeypatch.setattr(
        ordinary_apply, "configure_paramiko_host_verification", lambda *_a, **_kw: None
    )
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = SimpleNamespace(intent={})

    def remote(*_a, **_kw):
        calls.append(True)
        if len(calls) == 1:
            return ordinary_apply.remote(
                ssh,
                "192.0.2.1",
                SimpleNamespace(hostname="node-a"),
                {},
                {"action": "admit"},
                bootstrap=repair_bootstrap,
            )
        return {"operation": "confirmed"}

    coordinator.remote = remote
    monkeypatch.setattr(repair_apply.time, "sleep", lambda _: None)
    if denied:
        with pytest.raises(RuntimeError):
            coordinator.wait_access("node-a")
        assert len(calls) == 1
    else:
        coordinator.wait_access("node-a")
        assert len(calls) == 2
    assert closed == [True]


@pytest.mark.parametrize(
    "owner,expected", [("vm-a", ["node-b", "node-a"]), ("vm-b", ["node-a", "node-b"])]
)
def test_repair_inhibits_observed_nonowner_before_owner(owner, expected):
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.instances = {"node-a": object(), "node-b": object()}
    coordinator.admission = SimpleNamespace(
        plan=SimpleNamespace(vm_ha=object(), gateway_group=object(), gateway={}),
        bindings={"node-a": {"compute": "vm-a"}, "node-b": {"compute": "vm-b"}},
        manager=SimpleNamespace(
            observe_vm_ha_migration_state=lambda *_: {
                "shared_allocation": {"owner": {"compute_id": owner}}
            }
        ),
    )
    assert coordinator.inhibition_order() == expected


def test_guest_phase_receipts_precede_controller_progress():
    calls = []
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.instances = {"node-b": object(), "node-a": object()}
    coordinator.journal = SimpleNamespace(advance=lambda phase: calls.append(("controller", phase)))
    coordinator.remote = lambda name, _action, **payload: calls.append((name, payload["phase"]))
    coordinator.advance("enrolled")
    assert calls == [("node-a", "enrolled"), ("node-b", "enrolled"), ("controller", "enrolled")]


def test_package_context_alone_does_not_prove_staging(monkeypatch):
    from nebius_vpngw.deploy import repair_retirement

    monkeypatch.setattr(repair_retirement, "retire", lambda _: None)
    calls = []
    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.operation = "a" * 32
    coordinator.instances = {"node-a": object()}
    coordinator.context = {"guards": {"node-a": {}}, "packages": {"node-a": {}}, "staged": {}}
    coordinator.admission = SimpleNamespace(bindings={}, plan=SimpleNamespace(vm_ha=None))
    coordinator.cloud = SimpleNamespace(
        require=lambda _: object(), stop_all=lambda: calls.append("stop")
    )
    coordinator.wait_access = lambda _: None
    coordinator.stage = lambda name: (
        calls.append("stage"),
        coordinator.context["staged"].update({name: {}}),
    )
    coordinator.journal = SimpleNamespace(effect=lambda *_a, **_k: None, advance=lambda _: None)
    monkeypatch.setattr(
        repair_apply, "instance_cloud_state", lambda _: repair_apply.InstanceCloudState.RUNNING
    )
    monkeypatch.setattr(
        repair_apply,
        "Quarantine",
        lambda *_: SimpleNamespace(path=SimpleNamespace(exists=lambda: False)),
    )
    coordinator.prepare()
    assert calls[0] == "stage"


def test_repair_intent_rejects_path_traversal():
    with pytest.raises(ValueError, match="immutable target intent"):
        repair_state.validate_intent(
            {
                "project": "../foreign",
                "gateway": "gateway",
                "configuration": "a" * 64,
                "artifact": "b" * 64,
                "ha": False,
                "targets": [],
            }
        )


def test_first_start_for_repair_requires_exact_reset_evidence_without_creation_receipt():
    import hashlib
    import json

    from nebius_vpngw import repair_guest
    from nebius_vpngw.deploy.checkpoint_conversion import first_start_authorized
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus

    NS = SimpleNamespace
    artifact, operation = "a" * 64, "b" * 64
    instance = NS(
        hostname="gateway",
        vm_ha_node=NS(node_id="node"),
        vm_ha_generation=NS(generation_id="generation"),
    )
    binding = NS(
        cluster_id="cluster",
        generation_id="generation",
        nodes=[NS(node_id="node", compute_id="vm")],
    )
    proof = {
        "operation": operation,
        "compute": "vm",
        "disk": "disk",
        "artifact": artifact,
        "boot": "boot-a",
        "guard": hashlib.sha256(repair_guest.GUARD).hexdigest(),
    }
    resources = {
        "heartbeat-initial-artifact": artifact,
        "compute:gateway": "vm",
        "disk:gateway": "disk",
        "repair-reset:gateway": json.dumps(proof),
    }
    tx = NS(
        approval_kind="repair",
        operation_id=operation,
        resource_bindings=resources,
        completed_effects=["repair-reset-node"],
    )
    state = NS(
        status=VMHALifecycleStatus.ACTIVATING,
        cluster_id="cluster",
        transaction=tx,
        members=[NS(node_id="node", instance_name="gateway")],
    )
    assert first_start_authorized(state, instance, binding, artifact)
    for key in ("operation", "compute", "disk", "artifact", "guard"):
        resources["repair-reset:gateway"] = json.dumps({**proof, key: "foreign"})
        assert not first_start_authorized(state, instance, binding, artifact)
    for invalid in (
        "not-json",
        "[]",
        json.dumps({**proof, "boot": ""}),
        json.dumps({**proof, "boot": None}),
        json.dumps({**proof, "unexpected": "field"}),
    ):
        resources["repair-reset:gateway"] = invalid
        assert not first_start_authorized(state, instance, binding, artifact)
    del resources["repair-reset:gateway"]
    assert not first_start_authorized(state, instance, binding, artifact)
    resources["repair-reset:gateway"] = json.dumps(proof)
    tx.completed_effects.clear()
    assert not first_start_authorized(state, instance, binding, artifact)
