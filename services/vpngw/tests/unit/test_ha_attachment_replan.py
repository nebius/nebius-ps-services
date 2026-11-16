"""HA attachment retirement keeps original cloud requests and native release order."""

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.compute import v1 as compute
from tests.unit import test_security_group_apply
from tests.unit.test_vm_ha_rearm import _binding

from nebius_vpngw.deploy import attachment_command, ha_attachment_guard, ha_attachment_replan
from nebius_vpngw.deploy import security_group_apply as sg
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.ssh_push import SSHPush
from nebius_vpngw.deploy.vm_ha_cloud import AllocationOwner, clone_nebius_sdk_message
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus

cloud = test_security_group_apply.cloud


@pytest.fixture
def interrupted(cloud, tmp_path, monkeypatch, request):
    monkeypatch.setenv("HOME", str(tmp_path))
    state, manager = cloud
    native = compute.InstanceServiceClient(object())
    second = clone_nebius_sdk_message(state.instance)
    second.metadata.id, second.metadata.name = "compute-b", "gateway-a-1"
    mode = getattr(request, "param", "standby")
    owner_update = mode.startswith("owner")
    if owner_update:
        interfaces = list(second.spec.network_interfaces)
        interfaces[0].security_groups = [compute.SecurityGroup(id="sg-b")]
        second.spec.network_interfaces = interfaces
    targets = {"gateway-a-0": ("compute-a", "target-a"), "gateway-a-1": ("compute-b", "target-b")}

    def get(identifier):
        assert identifier in {"compute-a", "compute-b"}
        return clone_nebius_sdk_message(state.instance if identifier == "compute-a" else second)

    def update(request, **kwargs):
        assert request.metadata.id == "compute-a", "Unsubmitted obsolete owner update executed"
        return native.update(request, **kwargs)

    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: NS(
            get=lambda request, **_: NS(wait=lambda: get(request.id)),
            update=update,
            operation_service=native.operation_service,
        ),
    )
    manager.get_ha_instance = get
    manager.inspect_ordinary_targets = lambda names: {name: targets[name] for name in names}
    manager.get_ha_allocation = lambda _: object()
    owner_name = "gateway-a-0" if owner_update else "gateway-a-1"
    world = NS(
        owner=AllocationOwner("compute-a" if owner_update else "compute-b", "eth0"),
        gates={},
        released={},
        events=[],
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.vm_ha_cloud.allocation_observation", lambda *_: NS(owner=world.owner)
    )
    monkeypatch.setattr(
        ha_attachment_guard, "allocation_observation", lambda *_: NS(owner=world.owner)
    )
    binding = _binding()
    configs = tuple(
        NS(
            hostname=name,
            vm_ha_node=NS(node_id=node.node_id),
            vm_ha_generation=NS(generation_id=binding.generation_id),
        )
        for name, node in zip(targets, binding.nodes, strict=True)
    )
    guests = {
        item.hostname: dict(
            boot=item.hostname + "-boot",
            config="d" * 64,
            binding=binding.model_dump(mode="json"),
        )
        for item in configs
    }
    monkeypatch.setattr(
        sg, "guest_identity", lambda _s, _t, config, _l: deepcopy(guests[config.hostname])
    )

    def install(_target, config, _local, **kwargs):
        receipt = SSHPush._vm_ha_apply_lock_payload(
            cluster_id=binding.cluster_id,
            node_id=config.vm_ha_node.node_id,
            generation_id=binding.generation_id,
            operation_id=kwargs["operation_id"],
        )[1]
        world.gates[config.hostname] = kwargs["operation_id"]
        return receipt

    def inhibit(_target, name, _local, **kwargs):
        world.gates[name] = kwargs["operation_id"]
        return dict(
            schema="nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
            cluster_id=binding.cluster_id,
            node_id=kwargs["node_id"],
            generation_id=binding.generation_id,
            operation_id=kwargs["operation_id"],
        )

    def ready(_target, name, _local, **kwargs):
        operation = kwargs.get("operation_id") or kwargs["inhibition"]["operation_id"]
        assert world.gates.get(name) == operation

    def release(_target, name, _local, **kwargs):
        operation = kwargs["inhibition"]["operation_id"]
        assert world.gates.get(name) == operation or world.released.get(name) == operation
        world.released[name] = operation
        world.gates.pop(name, None)
        world.events.append("owner-release")

    def clear(_target, config, _local, **kwargs):
        assert world.released.get(owner_name) == kwargs["receipt"].operation_id
        world.gates.pop(config.hostname)
        world.events.append("passive-release")

    ssh = NS(
        install_vm_ha_apply_lock=install,
        inhibit_vm_ha_standby_replacement=inhibit,
        verify_vm_ha_standby_replacement_quiescent=ready,
        verify_vm_ha_apply_inhibition_quiescent=ready,
        release_vm_ha_standby_replacement_inhibition=release,
        clear_vm_ha_apply_lock=clear,
    )
    monkeypatch.setattr(
        sg, "guest_lock_present", lambda _s, _t, config, _l, receipt: config.hostname in world.gates
    )
    monkeypatch.setattr(
        ha_attachment_guard,
        "native_receipts",
        lambda _s, _t, config, _l, _b: dict(
            inhibition=world.gates.get(config.hostname) if config.hostname == owner_name else None,
            release=world.released.get(config.hostname),
            continuity=continuity_receipt(config),
        ),
    )

    def continuity_receipt(config):
        from nebius_vpngw.agent.vm_ha.attachment_continuity import FILENAME, _load

        return _load(tmp_path / "native" / config.hostname / FILENAME)

    if owner_update:
        import io
        import json
        from contextlib import redirect_stdout

        from tests.unit.test_vm_ha_attachment_continuity import _publish_inhibited_status

        from nebius_vpngw.agent.vm_ha.inhibition import STANDBY_REPLACEMENT_INHIBITION_FILENAME

        def prepare_native(_ssh, _target, config, _local, script):
            # Execute the emitted preparation against the real native readers,
            # status writer and gate formats; only OS/config paths are relocated.
            root = tmp_path / "native" / config.hostname
            root.mkdir(parents=True, exist_ok=True)
            operation = world.gates[config.hostname]
            boot = guests[config.hostname]["boot"]
            _publish_inhibited_status(
                root,
                binding,
                node_id=config.vm_ha_node.node_id,
                operation=operation,
                boot=boot,
                epoch="7",
            )
            if config.hostname == owner_name:
                (root / STANDBY_REPLACEMENT_INHIBITION_FILENAME).write_text(
                    json.dumps(
                        dict(
                            schema="nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
                            cluster_id=binding.cluster_id,
                            node_id=config.vm_ha_node.node_id,
                            generation_id=binding.generation_id,
                            operation_id=operation,
                        )
                    )
                )
            else:
                payload, _ = SSHPush._vm_ha_apply_lock_payload(
                    cluster_id=binding.cluster_id,
                    node_id=config.vm_ha_node.node_id,
                    generation_id=binding.generation_id,
                    operation_id=operation,
                )
                (root / "apply.lock").write_bytes(payload)
            installed = dict(
                runtime_binding=binding.model_dump(mode="json"),
                node=dict(node_id=config.vm_ha_node.node_id),
            )
            script = (
                script.replace(
                    "_read_vm_ha_config()",
                    repr(installed),
                )
                .replace(
                    "Path('/var/lib/nebius-vpngw/vm-ha')",
                    "Path(" + repr(str(root)) + ")",
                )
                .replace(
                    "Path('/proc/sys/kernel/random/boot_id').read_text().strip()",
                    repr(boot),
                )
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exec(compile(script, "native-owner-continuity", "exec"), {})
            return json.loads(output.getvalue())

        monkeypatch.setattr(sg, "_guest_observation", prepare_native)
    if mode == "owner-rejected":
        from grpc import StatusCode
        from tests.unit.test_replacement_cloud_recovery import request_error

        service = compute.InstanceServiceClient(object())

        def reject_owner(request, **kwargs):
            assert request.metadata.id == "compute-a" and kwargs["retries"] == 0
            raise request_error(StatusCode.FAILED_PRECONDITION)

        monkeypatch.setattr(
            compute,
            "InstanceServiceClient",
            lambda _: NS(
                get=service.get,
                update=reject_owner,
                operation_service=service.operation_service,
            ),
        )
    monkeypatch.setattr(sg, "refresh_guest_trust", lambda _m, _s, _j, _n, _p, callback: callback())
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "command")
    child = sg.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "attachment")
    observed = sg.inspect_plan(manager, tuple(targets), ("sg-b",))
    original = attachment_command.inspect(
        parent,
        observed=observed,
        child=None,
        scope="a" * 64,
        artifact="b" * 64,
    )
    lifecycle = NS(
        status=VMHALifecycleStatus.ACTIVE,
        transaction=NS(pending_effect=None),
        cluster_id=binding.cluster_id,
        allocation_id=binding.shared_allocation_id,
        members=tuple(NS(compute_id=node.compute_id) for node in binding.nodes),
        record_sha256="e" * 64,
    )
    if mode.startswith("composed"):
        original, lifecycle = composed_runtime(
            parent,
            observed,
            binding,
            configs,
            world,
            monkeypatch,
            prerequisites=mode == "composed-prerequisites",
        )
    state.lose_receipt = True
    from nebius_vpngw.deploy.replacement_cloud import CloudOperationRejected

    with pytest.raises(CloudOperationRejected if mode == "owner-rejected" else TimeoutError):
        if mode.startswith("composed"):
            original.execute_attachments(
                manager,
                ssh,
                {},
                configs,
                child,
                lifecycle=lifecycle,
                verify_ha=lambda: None,
                observed=observed,
            )
        else:
            original.execute(manager, ssh, {}, configs, child, verify_ha=lambda: None)

    def health():
        assert not world.gates and world.owner == AllocationOwner(
            "compute-a" if owner_update else "compute-b",
            "eth0",
        )
        world.events.append("health")

    options = dict(
        scope="c" * 64,
        artifact="b" * 64,
        read_lifecycle=lambda: deepcopy(lifecycle),
        manager=manager,
        ssh=ssh,
        instances=configs,
        local={},
        refresh_ssh_policy=lambda: "same-pins",
        verify_ha=health,
    )
    world.state, world.manager, world.parent, world.child = state, manager, parent, child
    world.options, world.guests, world.lifecycle = options, guests, lifecycle
    world.continuity_receipt = continuity_receipt
    if mode.startswith("composed"):
        options["prerequisites"] = lambda: (world.credentials, world.policy)
    world.inspect = lambda: ha_attachment_replan.inspect(parent, child, **options)
    return world


def composed_runtime(parent, attachments, binding, configs, world, monkeypatch, *, prerequisites):
    import json
    from dataclasses import replace

    from tests.unit.test_cli_helpers import _lifecycle_state

    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy import (
        credential_enrollment,
        ha_command,
        ordinary_handoff,
        package_reservation,
        provisioning_trust,
    )
    from nebius_vpngw.vm_ha_managed_credentials import (
        vm_ha_authorized_key_name,
        vm_ha_service_account_name,
    )

    manifests = {
        config.hostname: dict(wheel_sha256="b" * 64, ha_parent="f" * 64) for config in configs
    }
    credential_bindings = {
        prefix + node: value
        for node in ("node-a", "node-b")
        for prefix, value in (
            ("credential-sha256:", "a" * 64),
            ("credential-service-account:", "account"),
            ("credential-authorized-key:", "key"),
        )
    }
    credential_intent = dict(
        action="reuse",
        algorithm="RS256",
        key_size_bits=4096,
        expires_at=None,
        local_source_expected_absent=False,
        project_id="project-a",
        purpose="ha-controller",
        node_ids=["node-a", "node-b"],
        service_account_name=vm_ha_service_account_name("gateway-a"),
        authorized_key_name=vm_ha_authorized_key_name("gateway-a"),
        destination="protected-source",
        iam_resources=dict(
            service_account=dict(action="reuse", id="account"),
            group=dict(action="reuse", id="group"),
            membership=dict(action="reuse", member_id="account"),
            project_editor_permit=dict(action="reuse", resource_id="project-a", role="editor"),
            authorized_key=dict(action="reuse", id="key"),
        ),
    )
    world.credentials = NS(
        credentials=NS(resource_bindings=lambda: deepcopy(credential_bindings)),
        observation_record=lambda: deepcopy(credential_intent),
    )
    world.policy = NS(
        managed_action=None,
        assert_current=lambda: None,
        known_hosts_sha256="a" * 64,
        managed_receipt_sha256="b" * 64,
        fresh_identity_approval_sha256=None,
        transport_targets=tuple((config.hostname, config.hostname) for config in configs),
        pin_targets=tuple((config.hostname, config.hostname) for config in configs),
        identity_for=lambda name: NS(private_key=(name + "-fixture-key").encode()),
    )
    trust_intent = provisioning_trust.intent(
        world.policy, tuple(sorted(config.hostname for config in configs))
    )
    original = ha_command.create(
        parent,
        scope="a" * 64,
        artifact="b" * 64,
        action_scope="c" * 64,
        targets=tuple(node.compute_id for node in binding.nodes),
        approval="d" * 64,
        runtime_scope="e" * 64,
        report={
            "kind": "apply-convergence",
            "managed_ssh_action": "repair" if prerequisites else None,
        },
        attachment_plan=attachments,
        packages=dict(parent="f" * 64, checkpoint=None, manifests=manifests),
        credentials=credential_intent if prerequisites else None,
        node_ids=("node-a", "node-b"),
        ssh=trust_intent if prerequisites else None,
    )
    with parent:
        original.begin()
        if prerequisites:
            original.start_prerequisite("credentials")
            with credential_enrollment.EnrollmentJournal(
                parent.project, parent.gateway
            ) as enrollment:
                enrollment.begin(
                    credential_intent,
                    approval=original.plan.approval,
                    execution_id=original.execution_id,
                )
                enrollment.select("fixture-public-key")
                world.credentials.public_key_sha256 = enrollment.state["transaction"]["selected"]
                parent.verified("credentials", enrollment.complete(credential_bindings))
            original.start_prerequisite("ssh")
            with provisioning_trust.TrustJournal(parent.project, parent.gateway) as trust:
                trust.begin(
                    trust_intent,
                    approval=original.plan.approval,
                    execution_id=original.execution_id,
                )
                trust.select(
                    provisioning_trust.selection(world.policy, tuple(trust_intent["members"]))
                )
                parent.verified("ssh", trust.complete())
        original.start_runtime()
    base = _lifecycle_state()
    resources = {
        **original.lifecycle_bindings(),
        **credential_bindings,
        "heartbeat-upgrade-artifact": "b" * 64,
        "shared-allocation-id": binding.shared_allocation_id,
    }
    state = replace(
        base,
        project_id="project-a",
        gateway_name="gateway-a",
        cluster_id=binding.cluster_id,
        allocation_id=binding.shared_allocation_id,
        allocation_name="gateway-a-cluster-a-shared-private-ip",
        members=tuple(
            replace(
                member,
                instance_name=config.hostname,
                node_id=node.node_id,
                compute_id=node.compute_id,
                alias_allocation_ids=(binding.shared_allocation_id,)
                if node.compute_id == world.owner.instance_id
                else (),
            )
            for member, config, node in zip(base.members, configs, binding.nodes, strict=True)
        ),
        transaction=replace(
            base.transaction,
            operation_id="f" * 64,
            resource_bindings=tuple(sorted(resources.items())),
        ),
    )
    world.native = {}
    world.reservations = {}
    token = package_reservation.SCOPE.set(
        (parent.root, original.execution_id, digest(original.intent))
    )
    try:
        for config in configs:
            reservation = package_reservation.admit(
                dict(
                    action="prepare-guarded",
                    package=dict(host=config.hostname, desired=manifests[config.hostname]),
                ),
                "c" * 64,
            )
            world.reservations[config.hostname] = reservation
            world.native[config.hostname] = dict(
                children=dict(
                    runtime=dict(
                        phase="complete",
                        parent="f" * 64,
                        artifact="b" * 64,
                        operation="native-operation",
                        digest="a" * 64,
                    ),
                    ordinary=dict(state="complete", id="native-operation", digest="b" * 64),
                    conversion=None,
                ),
                boot_id=config.hostname + "-boot",
                adoption_sha256=None,
                package_worker=dict(
                    identity=reservation["identity"], record="d" * 64, generation=1
                ),
                intent=None,
                settlement=None,
                installed="e" * 64,
            )
    finally:
        package_reservation.SCOPE.reset(token)

    def remote(_ssh, _target, config, _local, request):
        assert request["action"] == "replan-inspect", "Completed runtime must not be executed again"
        assert request["package_reservation"] == world.reservations[config.hostname]
        assert request["terminal_runtime"] is True
        return json.loads(json.dumps(world.native[config.hostname]))

    monkeypatch.setattr(ordinary_handoff, "corrective_action", remote)
    return original, state


@pytest.mark.parametrize(
    "interrupted", ["standby", "composed", "composed-prerequisites"], indirect=True
)
def test_settlement_preserves_unsubmitted_owner_update_and_releases_in_order(interrupted):
    world = interrupted
    before = world.child.path.read_bytes(), world.parent.path.read_bytes()
    proposal = world.inspect()
    assert not proposal.path.exists()
    assert before == (world.child.path.read_bytes(), world.parent.path.read_bytes())
    proposal.execute(world.inspect)
    assert world.events == ["owner-release", "passive-release", "health"]
    assert world.child.snapshot()["transaction"] is None and world.parent.pending() is None
    assert world.state.effects == 1 and len(world.state.requests) == 2
    assert world.state.requests[0][1] == world.state.requests[1][1]
    fresh = sg.inspect_plan(world.manager, tuple(world.guests), ("sg-c",))
    assert fresh["members"]["gateway-a-0"]["before"][0]["explicit_ids"] == ["sg-b"]
    assert fresh["members"]["gateway-a-1"]["before"][0]["explicit_ids"] == ["sg-a"]
    for owner, raw in zip((world.child, world.parent), before, strict=True):
        assert raw in [p.read_bytes() for p in (owner.root / "history").glob("*.json")]


@pytest.mark.parametrize("interrupted", ["composed"], indirect=True)
@pytest.mark.parametrize(
    "fault", ["phase", "artifact", "boot", "worker", "credentials", "ssh", "approval"]
)
def test_composed_attachment_requires_fresh_terminal_runtime_owners(interrupted, fault):
    world = interrupted
    if fault in {"phase", "artifact"}:
        world.native["gateway-a-0"]["children"]["runtime"][fault] = (
            "installed" if fault == "phase" else "c" * 64
        )
    elif fault == "boot":
        world.native["gateway-a-0"]["boot_id"] = "different-boot"
    elif fault == "worker":
        world.native["gateway-a-0"]["package_worker"]["identity"] = "c" * 64
    elif fault == "credentials":
        world.credentials.credentials.resource_bindings = lambda: {
            "credential-sha256:node-a": "b" * 64
        }
    elif fault == "ssh":
        world.policy.managed_action = "repair"
    else:
        from dataclasses import replace

        resources = dict(world.lifecycle.transaction.resource_bindings)
        resources["runtime-command-execution"] = "f" * 32
        world.options["read_lifecycle"] = lambda: replace(
            world.lifecycle,
            transaction=replace(
                world.lifecycle.transaction, resource_bindings=tuple(sorted(resources.items()))
            ),
        )
    before = world.parent.path.read_bytes(), world.child.path.read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        world.inspect()
    assert before == (world.parent.path.read_bytes(), world.child.path.read_bytes())
    assert not world.events and len(world.state.requests) == 1


@pytest.mark.parametrize("interrupted", ["composed"], indirect=True)
def test_composed_runtime_is_reobserved_between_native_gate_releases(interrupted, monkeypatch):
    world = interrupted
    proposal = world.inspect()
    ssh = world.options["ssh"]
    release = ssh.release_vm_ha_standby_replacement_inhibition

    def changed(*args, **kwargs):
        release(*args, **kwargs)
        world.native["gateway-a-0"]["children"]["runtime"]["phase"] = "activating"

    monkeypatch.setattr(ssh, "release_vm_ha_standby_replacement_inhibition", changed)
    with pytest.raises(RuntimeError, match="terminal"):
        proposal.execute(world.inspect)
    assert world.events == ["owner-release"]
    assert "gateway-a-0" in world.gates
    assert world.child.pending() is not None and world.parent.pending() is not None


@pytest.mark.parametrize("interrupted", ["owner", "owner-rejected"], indirect=True)
def test_submitted_owner_settlement_preserves_native_revision_continuity(interrupted):
    from nebius_vpngw.agent.vm_ha.attachment_continuity import validate

    world = interrupted
    original = world.child.snapshot()["transaction"]
    assert original["owner_continuity"]["compute_id"] == "compute-a"
    receipts = [world.continuity_receipt(config) for config in world.options["instances"]]
    assert receipts[0] == receipts[1]
    validate(receipts[0], receipt=True)
    assert receipts[0]["ownership_epoch"] == "7"
    rejected = any(record["status"] == "rejected" for record in original["operations"].values())
    world.inspect().execute(world.inspect)
    assert world.events == ["owner-release", "passive-release", "health"]
    assert world.parent.pending() is None and world.child.snapshot()["transaction"] is None
    assert world.state.effects == (0 if rejected else 1)
    assert len(world.state.requests) == (0 if rejected else 2)
    assert receipts == [world.continuity_receipt(config) for config in world.options["instances"]]


@pytest.mark.parametrize("interrupted", ["owner"], indirect=True)
@pytest.mark.parametrize("drift", ["peer-epoch", "owner-revision", "missing-continuity"])
def test_owner_settlement_refuses_unproven_continuity_without_mutation(interrupted, drift):
    import json

    from nebius_vpngw.agent.vm_ha.attachment_continuity import FILENAME

    world = interrupted
    path = world.parent.root.parent / "native" / "gateway-a-1" / FILENAME
    if drift == "peer-epoch":
        receipt = json.loads(path.read_text())
        receipt["ownership_epoch"] = "6"
        path.write_text(json.dumps(receipt))
    elif drift == "missing-continuity":
        path.unlink()
    else:
        world.state.instance.metadata.resource_version += 1
    before = world.parent.path.read_bytes(), world.child.path.read_bytes()
    with pytest.raises(RuntimeError):
        world.inspect()
    assert before == (world.parent.path.read_bytes(), world.child.path.read_bytes())
    assert not world.events and len(world.state.requests) == 1


@pytest.mark.parametrize("interrupted", ["standby", "composed-prerequisites"], indirect=True)
@pytest.mark.parametrize("boundary", ["cloud", "owner", "passive", "child"])
def test_interrupted_ha_settlement_resumes_original_requests_and_release_receipts(
    interrupted, monkeypatch, boundary
):
    world = interrupted
    proposal = world.inspect()
    if boundary == "cloud":
        target, name = world.child, "save"

        def eligible():
            return all(
                r["status"] == "settled"
                for r in world.child.state["transaction"]["operations"].values()
            )
    elif boundary == "child":
        target, name = world.child, "save"

        def eligible():
            return world.child.state["transaction"] is None
    else:
        target = world.options["ssh"]
        name = (
            "release_vm_ha_standby_replacement_inhibition"
            if boundary == "owner"
            else "clear_vm_ha_apply_lock"
        )

        def eligible():
            return True

    native = getattr(target, name)

    def lose_ack(*args, **kwargs):
        result = native(*args, **kwargs)
        if eligible():
            raise OSError("lost publication acknowledgement")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(target, name, lose_ack)
        with pytest.raises(OSError):
            proposal.execute(world.inspect)
    resumed = world.inspect()
    assert resumed.resumed and resumed.approval == proposal.approval
    resumed.execute(world.inspect)
    assert world.parent.pending() is None and world.child.snapshot()["transaction"] is None
    assert world.state.effects == 1 and not world.gates


@pytest.mark.parametrize("interrupted", ["standby", "composed-prerequisites"], indirect=True)
@pytest.mark.parametrize(
    "owner", ["credentials", "ssh", "attachments", "routes", "provisioning", "replacement"]
)
def test_every_child_owner_is_excluded_before_ha_settlement(interrupted, owner):
    world = interrupted
    proposal = world.inspect()
    selected = proposal.owners[owner]
    with (
        type(selected)(selected.project, selected.gateway, root=selected.root),
        pytest.raises(RuntimeError),
    ):
        proposal.execute(world.inspect)
    assert not proposal.path.exists() and not world.events
    assert len(world.state.requests) == 1


@pytest.mark.parametrize("drift", ["owner", "guest", "gate", "lifecycle", "generation", "compute"])
def test_unproven_ha_authority_preserves_all_original_evidence(interrupted, drift):
    world = interrupted
    if drift == "owner":
        world.owner = AllocationOwner("compute-a", "eth0")
    elif drift == "guest":
        world.guests["gateway-a-0"]["boot"] = "another-boot"
    elif drift == "gate":
        world.gates.pop("gateway-a-1")
    elif drift == "lifecycle":
        world.lifecycle.transaction.pending_effect = "other-effect"
    elif drift == "generation":
        world.options["instances"][0].vm_ha_generation.generation_id = "f" * 64
    else:
        world.state.instance.spec.resources.preset = "8vcpu-32gb"
    before = world.child.path.read_bytes(), world.parent.path.read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        world.inspect()
    assert before == (world.child.path.read_bytes(), world.parent.path.read_bytes())
    assert not world.events and len(world.state.requests) == 1


@pytest.mark.parametrize("decision", ["dry-run", "decline", "approve", "vm-ha"])
def test_cli_ha_settlement_approval_boundary(interrupted, monkeypatch, decision):
    import click

    from nebius_vpngw import cli
    from nebius_vpngw.deploy.apply_command import ReplanReady
    from nebius_vpngw.deploy.apply_result import ApplyReport
    from nebius_vpngw.deploy.recovery import PredecessorSettlementRequired

    world = interrupted
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_k: decision == "approve")
    report = ApplyReport(dry_run=decision == "dry-run")
    before = world.child.path.read_bytes(), world.parent.path.read_bytes()
    wanted = (
        ReplanReady
        if decision == "approve"
        else PredecessorSettlementRequired
        if decision == "vm-ha"
        else click.exceptions.Exit
    )
    options = {key: value for key, value in world.options.items() if key != "read_lifecycle"}
    with pytest.raises(wanted):
        cli._review_ha_attachment_replan(
            world.parent,
            world.child,
            **options,
            lifecycle_store=NS(read=lambda **_: world.options["read_lifecycle"]()),
            supplied=None,
            dry_run=decision == "dry-run",
            passive=decision == "vm-ha",
            report=report,
        )
    assert report.started is (decision == "approve")
    assert tuple(report.mapping()["pending"]) == ("settle-ha-attachment-predecessor",)
    if decision != "approve":
        assert before == (world.child.path.read_bytes(), world.parent.path.read_bytes())
        assert not world.events and len(world.state.requests) == 1
    else:
        assert world.parent.pending() is None and not world.gates


@pytest.mark.parametrize("resumed", [False, True])
def test_changed_ha_settlement_approval_cannot_release_gates(interrupted, monkeypatch, resumed):
    from nebius_vpngw.deploy.replacement_cloud import Cloud

    world = interrupted
    proposal = world.inspect()
    if resumed:
        with monkeypatch.context() as patch:

            def stop(*_a, **_k):
                raise TimeoutError("injected before cloud settlement")

            patch.setattr(Cloud, "settle", stop)
            with pytest.raises(TimeoutError):
                proposal.execute(world.inspect)
        proposal = world.inspect()
    fresh = world.inspect()
    proposal.path.parent.mkdir(mode=0o700, exist_ok=True)
    proposal.path.write_text('{"competing": "approval"}')
    proposal.path.chmod(0o600)
    before = world.parent.path.read_bytes(), world.child.path.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        proposal.execute(lambda: fresh)
    assert before == (world.parent.path.read_bytes(), world.child.path.read_bytes())
    assert proposal.path.read_text() == '{"competing": "approval"}'
    assert not world.events and len(world.state.requests) == 1


@pytest.mark.parametrize("drift", ["approval", "owner", "lifecycle"])
def test_release_rechecks_authority_between_the_two_native_gates(interrupted, monkeypatch, drift):
    world = interrupted
    proposal = world.inspect()
    ssh = world.options["ssh"]
    native = ssh.release_vm_ha_standby_replacement_inhibition

    def release_then_drift(*args, **kwargs):
        native(*args, **kwargs)
        if drift == "approval":
            proposal.path.write_text('{"competing": "approval"}')
        elif drift == "owner":
            world.owner = AllocationOwner("compute-a", "eth0")
        else:
            world.lifecycle.record_sha256 = "f" * 64

    monkeypatch.setattr(ssh, "release_vm_ha_standby_replacement_inhibition", release_then_drift)
    with pytest.raises(RuntimeError, match="changed"):
        proposal.execute(world.inspect)
    assert "gateway-a-0" in world.gates
    assert world.events == ["owner-release"]
    assert world.child.pending() is not None and world.parent.pending() is not None


@pytest.mark.parametrize(
    "interrupted", ["standby", "composed", "composed-prerequisites"], indirect=True
)
@pytest.mark.parametrize("dry_run,settle", [(False, False), (True, False), (False, True)])
def test_real_apply_reaches_ha_attachment_settlement_before_obsolete_retry(
    interrupted, monkeypatch, tmp_path, dry_run, settle
):
    from dataclasses import dataclass, field, replace

    from tests.unit.test_cli_helpers import _lifecycle_state
    from typer.testing import CliRunner

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import apply_command
    from nebius_vpngw.deploy.recovery import error_chain

    world = interrupted
    binding = _binding()
    base = _lifecycle_state()
    members = tuple(
        replace(
            prior,
            instance_name=config.hostname,
            node_id=node.node_id,
            compute_id=node.compute_id,
            alias_allocation_ids=(binding.shared_allocation_id,)
            if node.compute_id == world.owner.instance_id
            else (),
        )
        for prior, config, node in zip(
            base.members, world.options["instances"], binding.nodes, strict=True
        )
    )
    state = replace(
        base,
        project_id="project-a",
        gateway_name="gateway-a",
        cluster_id=binding.cluster_id,
        allocation_id=binding.shared_allocation_id,
        allocation_name="gateway-a-cluster-a-shared-private-ip",
        members=members,
        transaction=replace(
            base.transaction,
            resource_bindings=(("shared-allocation-id", binding.shared_allocation_id),),
        ),
    )
    if hasattr(world, "native"):
        state = world.lifecycle
        members = state.members
    world.lifecycle = state
    world.options["read_lifecycle"] = lambda: state
    configs = world.options["instances"]
    nodes = tuple(
        NS(instance_index=index, node_id=node.node_id, role=NS(value=node.role))
        for index, node in enumerate(binding.nodes)
    )
    generation = NS(
        generation_id=binding.generation_id,
        digests=NS(
            configuration=binding.configuration_digest,
            static_routes=binding.static_routes_digest,
            bgp_policy=binding.bgp_policy_digest,
        ),
    )
    for config, node, member in zip(configs, nodes, members, strict=True):
        config.vm_ha_node, config.vm_ha_generation, config.external_ip = (
            node,
            generation,
            member.public_ip,
        )
    ha = NS(cluster_id=binding.cluster_id, generation=generation, members=nodes)
    plan = NS(
        vm_ha=ha,
        gateway_group=NS(
            name="gateway-a", region="region-a", vm_ha=ha, security_group_ids=("sg-c",)
        ),
        gateway={},
        validate=lambda: None,
        iter_instance_configs=lambda: iter(configs),
    )
    local = dict(
        project_id="project-a",
        region_id="region-a",
        gateway_group=dict(name="gateway-a", vm_spec={}),
    )
    path = tmp_path / "gateway.yaml"
    path.write_text("version: 1\n")

    @dataclass
    class Credentials:
        credentials: object = field(
            default_factory=lambda: (
                world.credentials.credentials
                if hasattr(world, "credentials")
                else NS(resource_bindings=lambda: {})
            )
        )
        command_intent: object = None
        action: str = "reuse"
        command_action: str = "reuse"
        public_key_sha256: str | None = field(
            default_factory=lambda: getattr(
                getattr(world, "credentials", None), "public_key_sha256", None
            )
        )

        def approval_record(self):
            return {"action": "reuse"}

        def observation_record(self):
            return world.credentials.observation_record()

    class Manager:
        def __init__(self, **kwargs):
            self.project_id, self.auth_token = "project-a", None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __getattr__(self, name):
            return getattr(world.manager, name)

        def discover_vm_ha_members(self, _):
            return {member.instance_name: member.public_ip for member in members}

        def observe_vm_ha_migration_state(self, *_):
            return {
                "project_id": "project-a",
                "members": [],
                "routes": [],
                "route_targets": [],
                "shared_allocation": {"present": True},
            }

        def verify_vm_ha_existing_identities(self, *_a, **_kw):
            pass

        def check_changes(self, _):
            return []

    monkeypatch.setattr(cli, "load_local_config", lambda _: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(cli, "VMHALifecycleStore", lambda *_: NS(read=lambda **_: state))
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_a, **_k: world.parent)
    monkeypatch.setattr(sg, "AttachmentJournal", lambda *_a, **_k: world.child)
    monkeypatch.setattr(cli, "inspect_managed_vm_ha_credentials", lambda **_: Credentials())
    monkeypatch.setattr(cli, "_validate_vm_ha_lifecycle_credential_transition", lambda *_: None)
    monkeypatch.setattr(cli, "_apply_operator_auth_token", lambda: None)
    monkeypatch.setattr(cli, "VMManager", Manager)
    monkeypatch.setattr(
        cli,
        "require_vm_ha_ssh_policy",
        lambda *_a, **_k: (
            world.policy
            if hasattr(world, "policy")
            else NS(managed_action=None, assert_current=lambda: None)
        ),
    )
    monkeypatch.setattr(cli, "_vm_ha_missing_standby_replacement_plan", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "validate_retained_route_scope", lambda *_: None)
    monkeypatch.setattr(cli, "_vm_ha_desired_approval_state", lambda _: {"desired": "current"})
    monkeypatch.setattr(
        cli,
        "_resolve_vm_ha_agent_artifact",
        lambda _: NS(sha256="b" * 64, dependency_plans=(), capabilities=()),
    )
    monkeypatch.setattr(cli, "_vm_ha_observation_matches_bindings", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "_vm_ha_provisioning_members", lambda *_: members)
    monkeypatch.setattr(cli, "SSHPush", lambda **_: world.options["ssh"])
    monkeypatch.setattr(
        cli,
        "_review_attachment_request_retry",
        lambda *_a, **_k: (
            (None, None)
            if world.parent.pending() is None
            else pytest.fail("Obsolete retry preceded settlement")
        ),
    )
    monkeypatch.setattr(cli, "_unchanged_vm_ha_apply", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", lambda *_a, **_k: object())
    monkeypatch.setattr(
        cli, "_confirm_vm_ha_healthy", lambda *_a, **_k: world.options["verify_ha"]()
    )
    monkeypatch.setattr(
        cli,
        "_refresh_vm_ha_ssh_policy_after_compute",
        lambda **_: world.policy if hasattr(world, "policy") else "same-pins",
    )
    approvals, failures = [], []

    native_approval = cli._approve_disruption
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_k: settle and len(approvals) == 1)

    def decline(value, **kwargs):
        approvals.append((value, kwargs))
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        return native_approval(value, **kwargs)

    monkeypatch.setattr(cli, "_approve_disruption", decline)
    failed = cli.ApplyReport.failed

    def record_failure(report, error):
        import traceback

        failures.append([frame.name for frame in traceback.extract_tb(error.__traceback__)])
        failures.extend((type(item).__name__, str(item)) for item in error_chain(error))
        return failed(report, error)

    monkeypatch.setattr(cli.ApplyReport, "failed", record_failure)
    before = world.child.path.read_bytes(), world.parent.path.read_bytes()
    args = ["apply", "--local-config-file", str(path)] + (["--dry-run"] if dry_run else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, failures
    if settle:
        assert len(approvals) == 2
        assert approvals[0][0] != approvals[1][0]
        assert approvals[1][1]["supplied"] is None
        assert world.parent.pending() is None and world.child.snapshot()["transaction"] is None
        assert world.events == ["owner-release", "passive-release", "health"]
        assert len(world.state.requests) == 2 and world.state.effects == 1
    else:
        assert len(approvals) == 1
        assert before == (world.child.path.read_bytes(), world.parent.path.read_bytes())
        assert not world.events and len(world.state.requests) == 1


@pytest.mark.parametrize("foreign", [False, True])
def test_native_ha_receipt_probe_is_read_only_and_rejects_foreign_identity(
    tmp_path, monkeypatch, foreign
):
    import io
    import json
    from contextlib import redirect_stdout

    from nebius_vpngw.agent.vm_ha.inhibition import STANDBY_REPLACEMENT_INHIBITION_FILENAME

    binding = _binding()
    config = NS(vm_ha_node=NS(node_id="node-a"))
    path = tmp_path / STANDBY_REPLACEMENT_INHIBITION_FILENAME
    gate = dict(
        schema="nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
        cluster_id=binding.cluster_id,
        node_id="foreign" if foreign else "node-a",
        generation_id=binding.generation_id,
        operation_id="d" * 64,
    )
    path.write_text(json.dumps(gate))

    def execute(_ssh, _target, _instance, _local, script):
        # Execute the exact generated probe with only its filesystem root moved
        # to the fixture; the production readers and validators remain real.
        script = script.replace(
            "Path('/var/lib/nebius-vpngw/vm-ha')", "Path(" + repr(str(tmp_path)) + ")"
        )
        output = io.StringIO()
        with redirect_stdout(output):
            exec(compile(script, "native-attachment-probe", "exec"), {})
        return json.loads(output.getvalue())

    monkeypatch.setattr(sg, "_guest_observation", execute)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    if foreign:
        with pytest.raises(ValueError, match="identity"):
            ha_attachment_guard.native_receipts(object(), "target", config, {}, binding)
    else:
        assert ha_attachment_guard.native_receipts(object(), "target", config, {}, binding) == {
            "inhibition": "d" * 64,
            "release": None,
            "continuity": None,
        }
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
