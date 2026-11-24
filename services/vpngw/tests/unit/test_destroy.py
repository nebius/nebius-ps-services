from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import nebius_vpngw.deploy.destroy as destroy_module
from nebius_vpngw.deploy.destroy import (
    DestroyCoordinator,
    DestroyFailure,
    DestroyOperationResume,
    DestroyPlan,
    DestroyReceiptStore,
    DestroyResource,
    DestroyResult,
    DestroyRoute,
    NebiusDestroyBackend,
    OrdinaryDestroyJournal,
    VMHADestroyJournal,
    execute_destroy,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHAEffectObservationGuard,
    VMHALifecycleJournal,
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    VMHAMigrationTransaction,
    normalize_vm_ha_observation,
    vm_ha_destroyed_retained_public_bindings,
)


@pytest.fixture
def pending_destroy_command(tmp_path, monkeypatch):
    from nebius_vpngw.deploy.apply_command import CommandJournal
    from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
    from nebius_vpngw.deploy.replacement import digest

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    config = tmp_path / "gateway.config.yaml"
    VMHALifecycleStore(config).write_verified(lifecycle, predecessor_sha256=None)
    command = CommandJournal(plan.project_id, plan.gateway_name)
    intent = {"attachments": None, "packages": None}
    command_plan = CommandPlan(
        plan.project_id,
        plan.gateway_name,
        "a" * 64,
        "b" * 64,
        (
            Effect(
                "runtime", "ha-lifecycle", "apply", ("old-compute",), digest(intent), "c" * 64, True
            ),
        ),
    )
    with command:
        command.begin(command_plan, intents={"runtime": intent})
        command.started("runtime")
    old_bytes = command.path.read_bytes()
    backend = _Backend(plan)
    backend.build_plan = lambda _: plan
    backend.old_observations = []

    def absent(target):
        backend.old_observations.append(target)
        return True

    backend.command_compute_absent = absent
    monkeypatch.setattr(destroy_module, "NebiusDestroyBackend", lambda *_a, **_k: backend)
    spec = SimpleNamespace(
        name=lifecycle.gateway_name,
        vm_ha=SimpleNamespace(
            cluster_id=lifecycle.cluster_id,
            members=tuple(
                SimpleNamespace(
                    instance_index=m.instance_index,
                    node_id=m.node_id,
                    role=SimpleNamespace(value=m.role),
                )
                for m in lifecycle.members
            ),
        ),
    )
    args = dict(
        config_path=config,
        config_digest=plan.config_digest,
        spec=spec,
        project_id=plan.project_id,
        vm_manager=object(),
    )
    return command, backend, args, old_bytes, command_plan, intent


def test_destroy_retires_old_pending_command_without_claiming_completion(pending_destroy_command):
    command, backend, args, old_bytes, command_plan, intent = pending_destroy_command
    old_execution = command.pending()["execution_id"]
    history_file = command.root / "failures" / "preserved.json"
    history_file.parent.mkdir(mode=0o700)
    history_file.write_text("retained diagnostic")
    result = execute_destroy(**args)
    assert result.topology == "vm-ha"
    assert backend.verifications == 2
    assert backend.old_observations == ["old-compute", "old-compute"]
    assert command.pending() is None
    assert any(path.read_bytes() == old_bytes for path in (command.root / "history").glob("*.json"))
    receipt = json.loads((command.root / "retirements" / (old_execution + ".json")).read_text())
    assert receipt["targets"] == ["old-compute"]
    assert receipt["execution"] == old_execution
    assert history_file.read_text() == "retained diagnostic"
    before = command.path.read_bytes()
    assert execute_destroy(**args).already_absent
    assert command.path.read_bytes() == before
    with command:
        command.begin(command_plan, intents={"runtime": intent})
    assert command.pending()["execution_id"] != old_execution


@pytest.mark.parametrize(
    "changed",
    [
        None,
        "missing",
        "execution",
        "approval",
        "artifact",
        "operation",
        "node",
        "compute",
        "disk",
        "members",
    ],
)
def test_destroy_retires_created_command_only_with_exact_creation_receipt(
    pending_destroy_command, changed
):
    from nebius_vpngw.apply_approval import SCHEMA, digest
    from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
    from nebius_vpngw.deploy.ha_created_packages import PREFIX
    from nebius_vpngw.deploy.ha_members import intent as member_intent
    from nebius_vpngw.deploy.ha_members import targets

    command, backend, args, _, previous_plan, _ = pending_destroy_command
    plan = replace(_plan(topology="vm-ha"), expected_compute_names=("gateway-0", "gateway-1"))
    backend.build_plan = lambda _: plan
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVATING, plan)
    members = member_intent(lifecycle.members, created={m.instance_name for m in lifecycle.members})
    for member in members.values():
        member["compute_id"] = member["disk_id"] = ""
    intent = {
        "attachments": None,
        "approval": "d" * 64,
        "runtime_scope": "e" * 64,
        "packages": {"parent": "operation-before", "checkpoint": "f" * 64, "manifests": {}},
        "members": members,
    }
    effect = Effect(
        "runtime", "ha-lifecycle", "apply", targets(members), digest(intent), "c" * 64, True
    )
    command_plan = CommandPlan(
        command.project, command.gateway, "a" * 64, previous_plan.artifact_sha256, (effect,)
    )
    with command:
        command.state.update(transaction=None, bindings={})
        command.save()
        command.begin(command_plan, intents={"runtime": intent})
        command.started("runtime")
    receipt = {
        "authority": {
            "execution": command.pending()["execution_id"],
            "operation": "operation-before",
            "artifact": command_plan.artifact_sha256,
            "lineage": {
                m.instance_name: {
                    "compute": m.compute_id,
                    "disk": m.disk_id,
                    "node": m.node_id,
                    "creation": f"provision-{m.instance_name}-compute",
                    "configuration": "a" * 64,
                }
                for m in lifecycle.members
            },
        },
        "manifests": {
            m.instance_name: {"wheel_sha256": command_plan.artifact_sha256}
            for m in lifecycle.members
        },
    }
    bindings = {
        "runtime-command-execution": command.pending()["execution_id"],
        "runtime-command-approval": json.dumps(
            {"schema": SCHEMA, "approval": intent["approval"], "scope": intent["runtime_scope"]}
        ),
        "runtime-package-manifests": "{}",
        "heartbeat-initial-artifact": command_plan.artifact_sha256,
        **{
            f"{kind}:{m.instance_name}": getattr(m, kind + "_id")
            for m in lifecycle.members
            for kind in ("compute", "disk")
        },
    }
    if changed in {"execution", "artifact", "operation"}:
        receipt["authority"][changed] = "foreign"
    elif changed in {"node", "compute", "disk"}:
        receipt["authority"]["lineage"]["gateway-0"][changed] = "foreign"
    elif changed == "members":
        receipt["manifests"].pop("gateway-1")
    elif changed == "approval":
        bindings["runtime-command-approval"] = json.dumps(
            {"schema": SCHEMA, "approval": "1" * 64, "scope": intent["runtime_scope"]}
        )
    if changed != "missing":
        bindings[PREFIX + "operation-before"] = json.dumps(receipt)
    lifecycle = replace(
        lifecycle,
        transaction=replace(
            lifecycle.transaction, resource_bindings=tuple(sorted(bindings.items()))
        ),
    )
    store = VMHALifecycleStore(args["config_path"])
    store.path.unlink()  # Replace the fixture's initial state before exercising the workflow.
    store.write_verified(lifecycle, predecessor_sha256=None)
    before = command.path.read_bytes()
    if changed:
        with pytest.raises(DestroyFailure, match="admission could not be retired"):
            execute_destroy(**args)
        assert command.path.read_bytes() == before
    else:
        execute_destroy(**args)
        assert command.pending() is None
        assert backend.old_observations == ["compute-0", "compute-1"] * 2


@pytest.mark.parametrize("failure", ["present", "unknown", "second-read"])
def test_destroy_keeps_old_admission_when_compute_absence_is_unproven(
    pending_destroy_command, failure
):
    command, backend, args, old_bytes, _, _ = pending_destroy_command
    observations = []

    def absent(target):
        observations.append(target)
        if failure == "unknown":
            raise RuntimeError("unavailable")
        return failure == "second-read" and len(observations) == 1

    backend.command_compute_absent = absent
    with pytest.raises(DestroyFailure, match="admission could not be retired"):
        execute_destroy(**args)
    assert command.path.read_bytes() == old_bytes
    assert not (command.root / "retirements").exists()
    assert backend.verifications == 2


@pytest.mark.parametrize("boundary", ["archive", "receipt", "clear"])
def test_destroy_retirement_resumes_each_publication_boundary(
    pending_destroy_command, monkeypatch, boundary
):
    from nebius_vpngw.deploy import destroy_command
    from nebius_vpngw.deploy.apply_command import CommandJournal

    command, backend, args, old_bytes, _, _ = pending_destroy_command
    owner, attribute = (
        (destroy_command, "publish")
        if boundary == "receipt"
        else (CommandJournal, "archive" if boundary == "archive" else "save")
    )
    original = getattr(owner, attribute)

    def fail(*_args, **_kwargs):
        raise OSError("interrupted retirement publication")

    monkeypatch.setattr(owner, attribute, fail)
    with pytest.raises(DestroyFailure, match="admission could not be retired"):
        execute_destroy(**args)
    assert command.path.read_bytes() == old_bytes
    monkeypatch.setattr(owner, attribute, original)
    assert execute_destroy(**args).already_absent
    assert command.pending() is None
    assert backend.verifications == 4


@pytest.mark.parametrize("boundary", ["receipt-link", "command-exchange"])
def test_destroy_retirement_recovers_interruption_inside_atomic_publication(
    pending_destroy_command, monkeypatch, boundary
):
    from nebius_vpngw.deploy import operator_guest

    command, _, args, _, _, _ = pending_destroy_command
    execution = command.pending()["execution_id"]
    receipt = command.root / "retirements" / (execution + ".json")
    fsync = operator_guest._fsync_directory
    exchange = operator_guest._exchange
    interrupted = []

    def fail_fsync(path):
        fsync(path)
        if path == receipt.parent and receipt.exists() and not interrupted:
            interrupted.append(True)
            raise OSError("lost receipt publication acknowledgment")

    def fail_exchange(first, second):
        exchange(first, second)
        if second == command.path and not interrupted:
            interrupted.append(True)
            raise OSError("lost command publication acknowledgment")

    attribute = "_fsync_directory" if boundary == "receipt-link" else "_exchange"
    monkeypatch.setattr(
        operator_guest, attribute, fail_fsync if boundary == "receipt-link" else fail_exchange
    )
    with pytest.raises(DestroyFailure, match="admission could not be retired"):
        execute_destroy(**args)
    assert interrupted
    monkeypatch.setattr(
        operator_guest, attribute, fsync if boundary == "receipt-link" else exchange
    )
    assert execute_destroy(**args).already_absent
    assert command.pending() is None
    assert not list(command.root.rglob("*.vpngw-recovery"))


@pytest.mark.parametrize("target", ["old-compute", "foreign-compute"])
def test_destroy_retirement_validates_and_preserves_package_reservations(
    pending_destroy_command, target
):
    from nebius_vpngw.deploy import package_reservation
    from nebius_vpngw.deploy.replacement import digest

    command, _, args, old_bytes, _, intent = pending_destroy_command
    scope = (command.root, command.pending()["execution_id"], digest(intent))
    token = package_reservation.SCOPE.set(scope)
    try:
        package_reservation.admit(
            {"action": "reserve", "package": {"host": "gateway-0", "target": target}},
            "d" * 64,
        )
    finally:
        package_reservation.SCOPE.reset(token)
    path = package_reservation.path_for(*scope[:2], "gateway-0")
    before = path.read_bytes()
    if target == "old-compute":
        execute_destroy(**args)
        assert command.pending() is None
    else:
        with pytest.raises(DestroyFailure, match="admission could not be retired"):
            execute_destroy(**args)
        assert command.path.read_bytes() == old_bytes
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "key,owner,action",
    [
        ("credentials", "managed-credentials", "enroll"),
        ("ssh", "ssh-bootstrap", "publish"),
        ("attachments", "security-groups", "reconcile"),
        ("runtime", "unknown-owner", "apply"),
    ],
)
def test_destroy_does_not_retire_unsettled_or_unknown_child_authority(
    pending_destroy_command, key, owner, action
):
    from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
    from nebius_vpngw.deploy.replacement import digest

    command, _, args, _, original, intent = pending_destroy_command
    runtime = original.effects[0]
    effect = Effect(key, owner, action, runtime.targets, digest(intent), "c" * 64, True)
    effects = (effect,) if key == "runtime" else (effect, runtime)
    new_plan = CommandPlan(command.project, command.gateway, "a" * 64, "b" * 64, effects)
    with command:
        command.state.update(transaction=None, bindings={})
        command.save()
        command.begin(new_plan, intents={"runtime": intent})
        command.started(key)
    before = command.path.read_bytes()
    with pytest.raises(DestroyFailure, match="admission could not be retired"):
        execute_destroy(**args)
    assert command.path.read_bytes() == before


def _plan(*, topology: str = "ordinary", blockers: tuple[str, ...] = ()) -> DestroyPlan:
    return DestroyPlan(
        project_id="project-a",
        gateway_name="gateway",
        topology=topology,
        config_digest="a" * 64,
        expected_compute_names=("gateway-0",),
        expected_disk_names=("gateway-0-boot",),
        expected_allocation_names=("gateway-0-eth0-private-ip",),
        compute=(DestroyResource("compute", "gateway-0", "compute-0", "1"),),
        disks=(DestroyResource("disk", "gateway-0-boot", "disk-0", "1"),),
        allocations=(
            DestroyResource(
                "allocation",
                "gateway-0-eth0-private-ip",
                "allocation-private",
                "1",
            ),
        ),
        routes=(
            DestroyRoute(
                route_id="route-0",
                name="vpngw-route-0",
                route_table_id="route-table-0",
                allocation_id="allocation-private",
                revision="1",
            ),
        ),
        retained_public_allocations=(
            DestroyResource(
                "public-allocation",
                "gateway-0-eth0-public-ip",
                "allocation-public",
                "1",
            ),
        ),
        retained_route_table_ids=("route-table-0",),
        blockers=blockers,
    )


@dataclass
class _Operation:
    id: str
    action: tuple[str, str]


class _Backend:
    def __init__(self, plan: DestroyPlan) -> None:
        self.compute = {item.resource_id for item in plan.compute}
        self.disks = {item.resource_id for item in plan.disks}
        self.allocations = {item.resource_id for item in plan.allocations}
        self.routes = {item.route_id for item in plan.routes}
        self.fenced: set[str] = set()
        self.events: list[str] = []
        self.operations: dict[str, _Operation] = {}
        self.fail_wait_once_for: str | None = None
        self.verifications = 0

    def preflight(self, _plan: DestroyPlan, *, require_revisions: bool = False) -> None:
        self.events.append(f"preflight:{require_revisions}")

    def instance_fenced(self, resource: DestroyResource) -> bool:
        return resource.resource_id not in self.compute or resource.resource_id in self.fenced

    def route_absent(self, route: DestroyRoute) -> bool:
        return route.route_id not in self.routes

    def resource_absent(self, resource: DestroyResource) -> bool:
        values = {
            "compute": self.compute,
            "disk": self.disks,
            "allocation": self.allocations,
        }[resource.kind]
        return resource.resource_id not in values

    def _submit(self, kind: str, resource_id: str, operation_id: str) -> _Operation:
        self.events.append(f"submit:{kind}:{resource_id}")
        operation = _Operation(f"cloud-{operation_id[:16]}", (kind, resource_id))
        self.operations[operation.id] = operation
        return operation

    def submit_stop(self, resource: DestroyResource, operation_id: str) -> object:
        return self._submit("compute-stop", resource.resource_id, operation_id)

    def submit_delete_route(self, route: DestroyRoute, operation_id: str) -> object:
        return self._submit("route", route.route_id, operation_id)

    def submit_delete_resource(self, resource: DestroyResource, operation_id: str) -> object:
        return self._submit(resource.kind, resource.resource_id, operation_id)

    @staticmethod
    def operation_id(operation: object) -> str:
        assert isinstance(operation, _Operation)
        return operation.id

    def _apply(self, operation: _Operation) -> None:
        kind, resource_id = operation.action
        if self.fail_wait_once_for == kind:
            self.fail_wait_once_for = None
            raise RuntimeError(f"injected {kind} wait failure")
        if kind == "compute-stop":
            self.fenced.add(resource_id)
        elif kind == "route":
            self.routes.discard(resource_id)
        elif kind == "compute":
            self.compute.discard(resource_id)
        elif kind == "disk":
            self.disks.discard(resource_id)
        elif kind == "allocation":
            self.allocations.discard(resource_id)
        else:  # pragma: no cover - defensive fake contract
            raise AssertionError(kind)
        self.events.append(f"complete:{kind}:{resource_id}")

    def wait_operation(self, operation: object) -> None:
        assert isinstance(operation, _Operation)
        self._apply(operation)

    def resume_operation(
        self,
        kind: str,
        cloud_operation_id: str,
    ) -> DestroyOperationResume:
        self.events.append(f"resume:{kind}:{cloud_operation_id}")
        self._apply(self.operations[cloud_operation_id])
        return DestroyOperationResume(lookup_supported=True, succeeded=True)

    def stable_verification(self, _plan: DestroyPlan) -> tuple[str, str]:
        assert not self.compute
        assert not self.disks
        assert not self.allocations
        assert not self.routes
        self.verifications += 2
        return "stable", "stable"


def _ordinary_journal(tmp_path: Path, plan: DestroyPlan) -> OrdinaryDestroyJournal:
    return OrdinaryDestroyJournal.load_or_start(
        DestroyReceiptStore(tmp_path / "gateway.config.yaml"),
        plan,
    )


@pytest.fixture
def retained_repair(tmp_path, monkeypatch):
    from nebius_vpngw import repair_state
    from nebius_vpngw.deploy import destroy_repair
    from nebius_vpngw.deploy.repair_cloud import retained_binding

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plan = _plan()
    backend = _Backend(plan)
    instance = SimpleNamespace(
        metadata=SimpleNamespace(id="compute-0", name="gateway-0", parent_id="project-a"),
        spec=SimpleNamespace(
            boot_disk=SimpleNamespace(existing_disk=SimpleNamespace(id="disk-0")),
            network_interfaces=[
                SimpleNamespace(
                    name="eth0",
                    subnet_id="subnet",
                    ip_address=SimpleNamespace(allocation_id="private"),
                    public_ip_address=SimpleNamespace(allocation_id="public"),
                )
            ],
        ),
    )
    backend.client, backend.instances = object(), object()
    backend._get_instance_by_id = lambda target: instance if target in backend.compute else None
    backend.command_compute_absent = lambda target: target not in backend.compute
    backend.build_plan = lambda _: plan
    monkeypatch.setattr(
        destroy_repair, "settle_resource", lambda *_: backend.events.append("resource-settle")
    )
    journal = destroy_repair.journal(plan.project_id, plan.gateway_name)
    journal.begin(
        {
            "project": plan.project_id,
            "gateway": plan.gateway_name,
            "configuration": "a" * 64,
            "artifact": "b" * 64,
            "ha": False,
            "targets": [
                {
                    "name": "gateway-0",
                    "compute": "compute-0",
                    "disk": "disk-0",
                    "transport": "192.0.2.1",
                    "binding": repair_state.digest(retained_binding(instance)),
                }
            ],
        }
    )
    journal.effect("package-worker", {"worker": "unreachable"}, state="accepted")
    return plan, backend, journal, instance


def test_destroy_settles_repair_before_fence_and_retires_without_ssh(tmp_path, retained_repair):
    from nebius_vpngw.deploy import destroy_repair

    plan, backend, journal, _ = retained_repair
    original = journal.load()
    admission = destroy_repair.inspect(plan, backend)

    def settle(**kwargs):
        assert kwargs == {"require_success": False}
        backend.events.append("repair-settle")

    admission.cloud.settle = settle
    result = DestroyCoordinator(backend, _ordinary_journal(tmp_path, plan), repair=admission).run()
    assert result.deleted_compute == 1
    assert backend.events.index("repair-settle") < next(
        i for i, value in enumerate(backend.events) if value.startswith("submit:compute-stop")
    )
    assert journal.load() is None
    archive = json.loads(
        (journal.root / "history" / (original["id"] + "-destroyed.json")).read_text()
    )
    assert archive == original and archive["effects"]["package-worker"]["state"] == "accepted"


@pytest.mark.parametrize("kind", ["compute", "disk"])
def test_destroy_does_not_adopt_reused_names_when_repair_compute_absent(retained_repair, kind):
    from nebius_vpngw.deploy import destroy_repair

    plan, backend, journal, _ = retained_repair
    backend.compute.clear()
    changed = replace(
        plan,
        **{
            kind if kind == "compute" else "disks": (
                replace(
                    (plan.compute if kind == "compute" else plan.disks)[0],
                    resource_id="replacement",
                ),
            )
        },
    )
    with pytest.raises(RuntimeError, match="cannot adopt replacement"):
        destroy_repair.inspect(changed, backend)
    assert journal.load() is not None and not backend.events


@pytest.mark.parametrize("ha", [False, True])
def test_completed_repair_does_not_block_authorized_replacement_destruction(
    tmp_path, retained_repair, ha
):
    from nebius_vpngw import repair_state
    from nebius_vpngw.deploy import destroy_repair

    plan, _backend, journal, _ = retained_repair
    record = journal.load()
    record["phase"] = "complete"
    record["effects"] = {}
    if ha:
        record["intent"]["ha"] = True
        record["intent"]["targets"].append(
            {
                "name": "gateway-1",
                "compute": "retired-peer",
                "disk": "retired-disk",
                "transport": "192.0.2.2",
                "binding": "f" * 64,
            }
        )
    repair_state.publish(journal.path, record)
    current = replace(
        plan,
        topology="vm-ha" if ha else "ordinary",
        compute=(replace(plan.compute[0], resource_id="replacement"),),
        disks=(replace(plan.disks[0], resource_id="replacement-disk"),),
    )
    backend = _Backend(current)
    backend.client = object()
    backend._get_instance_by_id = lambda target: (
        None
        if target != "replacement"
        else pytest.fail("historical authority must not inspect new target")
    )
    backend.command_compute_absent = lambda target: target not in backend.compute
    admission = destroy_repair.inspect(current, backend)
    DestroyCoordinator(backend, _ordinary_journal(tmp_path, current), repair=admission).run()
    assert journal.load() is None and not backend.compute


def test_public_destroy_resumes_terminal_plan_until_repair_unlink_finishes(
    tmp_path, monkeypatch, retained_repair
):
    from nebius_vpngw.deploy import destroy_repair

    plan, backend, journal, _ = retained_repair
    monkeypatch.setattr(destroy_module, "NebiusDestroyBackend", lambda *_a, **_k: backend)
    args = dict(
        config_path=tmp_path / "config.yaml",
        config_digest=plan.config_digest,
        spec=SimpleNamespace(name="gateway", vm_ha=None),
        project_id="project-a",
        vm_manager=object(),
    )
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path == journal.path:
            raise OSError("interrupted active unlink")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError, match="interrupted active unlink"):
            execute_destroy(**args)
    store = DestroyReceiptStore(args["config_path"])
    assert store.read().terminal and destroy_repair.pending_retirement(plan)

    def unexpected(*_):
        raise AssertionError("must retain original terminal plan")

    backend.build_plan = unexpected
    assert execute_destroy(**args).already_absent
    assert journal.load() is None and store.read().plan == plan


def test_destroy_rechecks_every_member_after_resource_settlement(
    tmp_path, retained_repair, monkeypatch
):
    from nebius_vpngw.deploy import destroy_repair

    plan, backend, journal, _ = retained_repair
    admission = destroy_repair.inspect(plan, backend)

    def settle(*_):
        backend.fenced.clear()

    monkeypatch.setattr(destroy_repair, "settle_resource", settle)
    with pytest.raises(RuntimeError, match="after final fencing"):
        DestroyCoordinator(backend, _ordinary_journal(tmp_path, plan), repair=admission).run()
    assert backend.compute and backend.routes and journal.load() is not None


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("action", ["start", "update"])
def test_destroy_settles_original_request_identity_before_final_fencing(
    tmp_path, retained_repair, monkeypatch, accepted, action
):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute import v1 as compute

    from nebius_vpngw.deploy import destroy_repair
    from nebius_vpngw.deploy.replacement_cloud import message_digest

    plan, backend, journal, _ = retained_repair
    admission = destroy_repair.inspect(plan, backend)
    keys = []
    request = (
        compute.StartInstanceRequest(id="compute-0")
        if action == "start"
        else compute.UpdateInstanceRequest(
            metadata=ResourceMetadata(id="compute-0", resource_version=7),
            spec=compute.InstanceSpec(),
        )
    )
    original_request = message_digest(request)

    def operation(*, pending=False):
        def wait(**_):
            if pending:
                raise TimeoutError("lost wait")

        return SimpleNamespace(
            id="accepted-" + action,
            resource_id="compute-0",
            sync_wait=wait,
            successful=lambda: True,
            done=lambda: True,
        )

    def submit(_request, **kwargs):
        keys.append(kwargs["metadata"])
        if not accepted:
            raise TimeoutError("lost reply")
        return SimpleNamespace(wait=lambda: operation(pending=True))

    with pytest.raises(TimeoutError):
        admission.cloud.mutate("old-" + action, request, submit)

    def replay(request, **kwargs):
        assert message_digest(request) == original_request
        keys.append(kwargs["metadata"])
        backend.events.append("settle-old-" + action)
        return SimpleNamespace(wait=operation)

    def lookup(*_a, **_k):
        backend.events.append("settle-old-" + action)
        return SimpleNamespace(wait=operation)

    def get(request, **_kwargs):
        assert request.id == "compute-0"
        # Reconstruct the saved Update against fresh authoritative metadata;
        # neither its newer revision nor a new idempotency key may be adopted.
        return SimpleNamespace(
            wait=lambda: compute.Instance(
                metadata=ResourceMetadata(
                    id="compute-0", parent_id="project-a", resource_version=8
                ),
                spec=compute.InstanceSpec(),
            )
        )

    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: SimpleNamespace(
            **{action: replay},
            get=get,
            operation_service=lambda: SimpleNamespace(get=lookup),
        ),
    )
    DestroyCoordinator(backend, _ordinary_journal(tmp_path, plan), repair=admission).run()
    assert journal.load() is None
    assert backend.events.index("settle-old-" + action) < next(
        i for i, value in enumerate(backend.events) if value.startswith("submit:compute-stop")
    )
    assert len(keys) == (1 if accepted else 2)
    assert all(key == keys[0] for key in keys)


def test_destroy_plan_round_trip_binds_exact_scope() -> None:
    plan = _plan()

    assert DestroyPlan.from_json(plan.to_json()) == plan
    assert plan.digest == DestroyPlan.from_dict(plan.to_dict()).digest
    assert plan.effect_count == 4


def test_vm_ha_journal_rejects_plan_outside_lifecycle_approval(tmp_path: Path) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest="0" * 64,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)

    with pytest.raises(ValueError, match="outside its lifecycle authority"):
        VMHADestroyJournal(VMHALifecycleJournal(store, destruction))


def test_coordinator_orders_fence_route_compute_disk_and_allocation(tmp_path: Path) -> None:
    plan = _plan()
    backend = _Backend(plan)
    journal = _ordinary_journal(tmp_path, plan)

    result = DestroyCoordinator(backend, journal).run()

    submissions = [event for event in backend.events if event.startswith("submit:")]
    assert submissions == [
        "submit:compute-stop:compute-0",
        "submit:route:route-0",
        "submit:compute:compute-0",
        "submit:disk:disk-0",
        "submit:allocation:allocation-private",
    ]
    assert result.deleted_compute == 1
    assert result.deleted_disks == 1
    assert result.deleted_routes == 1
    assert result.deleted_allocations == 1
    assert backend.verifications == 2
    receipt = DestroyReceiptStore(tmp_path / "gateway.config.yaml").read()
    assert receipt is not None and receipt.terminal is True
    assert receipt.pending_effect is None
    assert receipt.plan.retained_public_allocations[0].resource_id == "allocation-public"


def test_destroy_retains_operator_managed_vm_ha_credential(tmp_path: Path) -> None:
    plan = _plan(topology="vm-ha")
    backend = _Backend(plan)
    journal = _ordinary_journal(tmp_path, plan)
    credential = (
        tmp_path
        / ".config"
        / "nebius-vpngw"
        / "credentials"
        / plan.project_id
        / plan.gateway_name
        / "nebius-credentials.json"
    )
    credential.parent.mkdir(parents=True)
    credential.write_bytes(b"retained-private-credential")
    credential.chmod(0o600)

    DestroyCoordinator(backend, journal).run()

    assert credential.read_bytes() == b"retained-private-credential"


@pytest.mark.parametrize(
    ("failure_action", "journal_kind", "submission"),
    (
        ("compute-stop", "compute", "submit:compute-stop:compute-0"),
        ("route", "route", "submit:route:route-0"),
        ("compute", "compute", "submit:compute:compute-0"),
        ("disk", "disk", "submit:disk:disk-0"),
        ("allocation", "allocation", "submit:allocation:allocation-private"),
    ),
)
def test_interrupted_accepted_operation_resumes_without_resubmission(
    tmp_path: Path,
    failure_action: str,
    journal_kind: str,
    submission: str,
) -> None:
    plan = _plan()
    backend = _Backend(plan)
    backend.fail_wait_once_for = failure_action
    journal = _ordinary_journal(tmp_path, plan)

    expected_reason = {
        "compute-stop": "destroy-compute-fence-failed",
        "route": "destroy-route-delete-failed",
        "compute": "destroy-compute-delete-failed",
        "disk": "destroy-disk-delete-failed",
        "allocation": "destroy-allocation-delete-failed",
    }[failure_action]
    with pytest.raises(DestroyFailure) as failed:
        DestroyCoordinator(backend, journal).run()
    assert failed.value.reason_code == expected_reason

    receipt = DestroyReceiptStore(tmp_path / "gateway.config.yaml").read()
    assert receipt is not None
    assert receipt.terminal is False
    assert receipt.pending_effect is not None
    assert receipt.accepted_operation_kind == journal_kind
    submissions = backend.events.count(submission)

    resumed = OrdinaryDestroyJournal(
        DestroyReceiptStore(tmp_path / "gateway.config.yaml"),
        receipt,
    )
    DestroyCoordinator(backend, resumed).run()

    assert backend.events.count(submission) == submissions
    assert any(event.startswith(f"resume:{journal_kind}:") for event in backend.events)


def test_unsupported_operation_lookup_replays_the_exact_bound_effect(
    tmp_path: Path,
) -> None:
    plan = _plan()
    backend = _Backend(plan)
    backend.fail_wait_once_for = "route"
    journal = _ordinary_journal(tmp_path, plan)

    with pytest.raises(DestroyFailure) as failed:
        DestroyCoordinator(backend, journal).run()
    assert failed.value.reason_code == "destroy-route-delete-failed"

    receipt = DestroyReceiptStore(tmp_path / "gateway.config.yaml").read()
    assert receipt is not None
    resumed = OrdinaryDestroyJournal(
        DestroyReceiptStore(tmp_path / "gateway.config.yaml"),
        receipt,
    )
    backend.resume_operation = lambda _kind, _cloud_operation_id: DestroyOperationResume(  # type: ignore[method-assign]
        lookup_supported=False,
        succeeded=None,
    )

    DestroyCoordinator(backend, resumed).run()

    assert backend.events.count("submit:route:route-0") == 2


def test_failure_after_effect_is_completed_from_the_exact_postcondition(
    tmp_path: Path,
) -> None:
    plan = _plan()

    class Backend(_Backend):
        failed = False

        def wait_operation(self, operation: object) -> None:
            assert isinstance(operation, _Operation)
            self._apply(operation)
            if operation.action[0] == "compute-stop" and not self.failed:
                self.failed = True
                raise RuntimeError("controller reported a condition after deleting Compute")

    backend = Backend(plan)

    result = DestroyCoordinator(backend, _ordinary_journal(tmp_path, plan)).run()

    assert result.deleted_compute == 1
    assert backend.compute == set()


@pytest.mark.parametrize(
    ("failure_action", "submission"),
    (
        ("compute-stop", "submit:compute-stop:compute-0"),
        ("route", "submit:route:route-0"),
    ),
)
def test_terminal_failed_destroy_operation_advances_to_a_new_attempt(
    tmp_path: Path,
    failure_action: str,
    submission: str,
) -> None:
    plan = _plan()
    backend = _Backend(plan)
    backend.fail_wait_once_for = failure_action
    journal = _ordinary_journal(tmp_path, plan)

    with pytest.raises(DestroyFailure):
        DestroyCoordinator(backend, journal).run()

    receipt = DestroyReceiptStore(tmp_path / "gateway.config.yaml").read()
    assert receipt is not None
    resumed = OrdinaryDestroyJournal(
        DestroyReceiptStore(tmp_path / "gateway.config.yaml"),
        receipt,
    )
    backend.resume_operation = lambda _kind, _cloud_operation_id: SimpleNamespace(  # type: ignore[method-assign]
        lookup_supported=True,
        succeeded=False,
    )

    DestroyCoordinator(backend, resumed).run()

    assert backend.events.count(submission) == 2
    action = (failure_action, "compute-0" if failure_action == "compute-stop" else "route-0")
    assert (
        len(
            {
                operation_id
                for operation_id, operation in backend.operations.items()
                if operation.action == action
            }
        )
        == 2
    )
    final = DestroyReceiptStore(tmp_path / "gateway.config.yaml").read()
    assert final is not None and final.terminal
    assert len(final.failed_operations) == 1


def test_vm_ha_terminal_failed_destroy_operation_is_durably_superseded(
    tmp_path: Path,
) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)
    backend = _Backend(plan)
    backend.fail_wait_once_for = "route"

    with pytest.raises(DestroyFailure):
        DestroyCoordinator(
            backend,
            VMHADestroyJournal(VMHALifecycleJournal(store, destruction)),
        ).run()

    interrupted = store.read(
        expected_project_id="project-a",
        expected_gateway_name="gateway",
    )
    assert interrupted is not None
    backend.resume_operation = lambda _kind, _cloud_operation_id: DestroyOperationResume(  # type: ignore[method-assign]
        lookup_supported=True,
        succeeded=False,
    )

    DestroyCoordinator(
        backend,
        VMHADestroyJournal(VMHALifecycleJournal(store, interrupted)),
    ).run()

    final = store.read(
        expected_project_id="project-a",
        expected_gateway_name="gateway",
    )
    assert final is not None and final.status is VMHALifecycleStatus.DESTROYED
    assert final.transaction is not None
    failed = {
        key: value
        for key, value in final.transaction.resource_bindings
        if key.startswith("destroy-failed-operation:destroy-route-delete-")
    }
    assert len(failed) == 1
    assert (
        len(
            {
                operation_id
                for operation_id, operation in backend.operations.items()
                if operation.action == ("route", "route-0")
            }
        )
        == 2
    )


def test_terminal_destroy_rerun_verifies_without_new_cloud_effects(tmp_path: Path) -> None:
    plan = _plan()
    backend = _Backend(plan)
    DestroyCoordinator(backend, _ordinary_journal(tmp_path, plan)).run()
    submission_count = sum(event.startswith("submit:") for event in backend.events)
    receipt = DestroyReceiptStore(tmp_path / "gateway.config.yaml").read()
    assert receipt is not None and receipt.terminal

    result = DestroyCoordinator(
        backend,
        OrdinaryDestroyJournal(
            DestroyReceiptStore(tmp_path / "gateway.config.yaml"),
            receipt,
        ),
    ).run()

    assert result.already_absent is True
    assert sum(event.startswith("submit:") for event in backend.events) == submission_count


def test_terminal_verification_requires_retained_public_allocation_detached() -> None:
    plan = replace(
        _plan(),
        expected_compute_names=(),
        expected_disk_names=(),
        expected_allocation_names=(),
        compute=(),
        disks=(),
        allocations=(),
        routes=(),
    )
    retained = SimpleNamespace(
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(
                    instance_id="deleted-compute",
                    name="eth0",
                ),
                load_balancer=None,
            ),
        )
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend._get_instance_by_name = lambda _name: None
    backend._get_disk_by_name = lambda _name: None
    backend._get_allocation_by_name = lambda _name: None
    backend._route_inventory = lambda: ()
    backend._strict_resource = lambda _resource: retained

    with pytest.raises(RuntimeError, match="still attached"):
        backend._verification_observation(plan)


def test_terminal_verification_accepts_detached_retained_public_allocation() -> None:
    plan = replace(
        _plan(),
        expected_compute_names=(),
        expected_disk_names=(),
        expected_allocation_names=(),
        compute=(),
        disks=(),
        allocations=(),
        routes=(),
    )
    retained = SimpleNamespace(status=SimpleNamespace(state="ALLOCATED", assignment=None))
    backend = object.__new__(NebiusDestroyBackend)
    backend._get_instance_by_name = lambda _name: None
    backend._get_disk_by_name = lambda _name: None
    backend._get_allocation_by_name = lambda _name: None
    backend._route_inventory = lambda: ()
    backend._strict_resource = lambda _resource: retained

    first = backend._verification_observation(plan)
    second = backend._verification_observation(plan)
    assert len(first) == 64
    assert first == second


def test_terminal_ordinary_receipt_allows_fresh_recreated_identities(tmp_path: Path) -> None:
    first_plan = _plan()
    store = DestroyReceiptStore(tmp_path / "gateway.config.yaml")
    DestroyCoordinator(
        _Backend(first_plan), OrdinaryDestroyJournal.load_or_start(store, first_plan)
    ).run()
    terminal = store.read()
    assert terminal is not None and terminal.terminal
    second_plan = replace(
        first_plan,
        compute=(DestroyResource("compute", "gateway-0", "compute-1", "1"),),
        disks=(DestroyResource("disk", "gateway-0-boot", "disk-1", "1"),),
        allocations=(
            DestroyResource(
                "allocation",
                "gateway-0-eth0-private-ip",
                "allocation-private-1",
                "1",
            ),
        ),
        routes=(
            replace(
                first_plan.routes[0],
                route_id="route-1",
                allocation_id="allocation-private-1",
            ),
        ),
    )

    fresh = OrdinaryDestroyJournal.load_or_start(store, second_plan)

    assert fresh.receipt.operation_id != terminal.operation_id
    assert fresh.receipt.revision == terminal.revision + 1
    assert fresh.receipt.predecessor_sha256 == terminal.record_sha256
    assert fresh.receipt.terminal is False


def test_destroy_receipt_rejects_group_or_world_writable_state(tmp_path: Path) -> None:
    plan = _plan()
    store = DestroyReceiptStore(tmp_path / "gateway.config.yaml")
    OrdinaryDestroyJournal.load_or_start(store, plan)
    os.chmod(store.path, 0o622)

    with pytest.raises(ValueError, match="unsafe local metadata"):
        store.read()


def test_blocking_foreign_route_prevents_every_effect(tmp_path: Path) -> None:
    plan = _plan(blockers=("foreign route references a gateway allocation",))
    backend = _Backend(plan)
    journal = _ordinary_journal(tmp_path, plan)

    with pytest.raises(RuntimeError, match="foreign route"):
        DestroyCoordinator(backend, journal).run()

    assert backend.events == []


def test_preflight_rejects_name_that_appeared_after_planning() -> None:
    plan = replace(
        _plan(),
        compute=(),
        disks=(),
        allocations=(),
        routes=(),
        retained_public_allocations=(),
        retained_route_table_ids=(),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.project_id = "project-a"
    backend.spec = SimpleNamespace(name="gateway")
    backend._get_instance_by_name = lambda _name: object()
    backend._get_disk_by_name = lambda _name: None
    backend._get_allocation_by_name = lambda _name: None
    backend._route_inventory = lambda: ()

    with pytest.raises(RuntimeError, match="appeared after destroy planning"):
        backend.preflight(plan)


def test_preflight_rejects_new_unplanned_route_reference() -> None:
    plan = replace(
        _plan(),
        expected_compute_names=(),
        expected_disk_names=(),
        compute=(),
        disks=(),
        routes=(),
        retained_public_allocations=(),
        retained_route_table_ids=(),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.project_id = "project-a"
    backend.spec = SimpleNamespace(name="gateway")
    backend._get_allocation_by_name = lambda _name: None
    backend._strict_resource = lambda _resource: object()
    backend._route_inventory = lambda: (
        ("route-table-0", SimpleNamespace(metadata=SimpleNamespace(id="foreign-route"))),
    )
    backend._route_allocation_id = lambda _route: "allocation-private"

    with pytest.raises(RuntimeError, match="unplanned route"):
        backend.preflight(plan)


def test_vm_ha_destroy_owns_canonical_unlabeled_product_route_to_bound_allocation() -> None:
    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    allocation_id = lifecycle.members[0].primary_allocation_id
    route = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-product",
            name="vpngw-10.20.0.0-16",
            labels={},
            resource_version="7",
        ),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.20.0.0/16"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=allocation_id),
            ),
        ),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = lifecycle
    backend.resolved_predecessor_effect = None
    backend.resolved_predecessor_resource_id = None

    assert backend._ha_route_is_owned(route, "route-table-product", allocation_id)


def test_vm_ha_destroy_rejects_partial_labels_on_canonical_product_route() -> None:
    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    allocation_id = lifecycle.members[0].primary_allocation_id
    route = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-ambiguous",
            name="vpngw-10.20.0.0-16",
            labels={"nebius-vpngw-managed": "vm-ha-v1"},
            resource_version="7",
        ),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.20.0.0/16"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=allocation_id),
            ),
        ),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = lifecycle
    backend.resolved_predecessor_effect = None
    backend.resolved_predecessor_resource_id = None

    with pytest.raises(RuntimeError, match="partial authority labels"):
        backend._ha_route_is_owned(route, "route-table-product", allocation_id)


@pytest.mark.parametrize(
    ("route_name", "allocation_id"),
    (
        ("customer-route", "allocation-primary-0"),
        ("vpngw-10.20.0.0-16", "allocation-foreign"),
    ),
)
def test_vm_ha_destroy_does_not_adopt_noncanonical_or_foreign_route(
    route_name: str,
    allocation_id: str,
) -> None:
    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    route = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-foreign",
            name=route_name,
            labels={},
            resource_version="7",
        ),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.20.0.0/16"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=allocation_id),
            ),
        ),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = lifecycle
    backend.resolved_predecessor_effect = None
    backend.resolved_predecessor_resource_id = None

    assert not backend._ha_route_is_owned(route, "route-table-product", allocation_id)


def test_strict_resource_rejects_private_allocation_shape_change() -> None:
    resource = _plan().allocations[0]
    observed = SimpleNamespace(
        metadata=SimpleNamespace(id=resource.resource_id, name=resource.name)
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend._get_allocation_by_id = lambda _resource_id: observed
    backend._get_allocation_by_name = lambda _name: observed
    backend._allocation_shape = lambda _allocation: "public"

    with pytest.raises(RuntimeError, match="shape changed"):
        backend._strict_resource(resource)


def test_allocation_delete_rejects_foreign_compute_assignment() -> None:
    resource = _plan().allocations[0]
    observed = SimpleNamespace(
        metadata=SimpleNamespace(id=resource.resource_id, name=resource.name),
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(
                    instance_id="foreign-compute",
                    name="eth0",
                ),
                load_balancer=None,
            ),
        ),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend._get_allocation_by_id = lambda _resource_id: observed
    backend._get_allocation_by_name = lambda _name: observed
    backend._allocation_shape = lambda _allocation: "private"

    with pytest.raises(RuntimeError, match="remains assigned"):
        backend.resource_absent(resource)


def test_route_identity_rejects_in_place_ownership_change() -> None:
    route = _plan().routes[0]
    observed = SimpleNamespace(
        metadata=SimpleNamespace(
            id=route.route_id,
            name=route.name,
            parent_id=route.route_table_id,
            resource_version=route.revision,
        )
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = object()
    backend._route_allocation_id = lambda _route: route.allocation_id
    backend._ha_route_is_owned = lambda *_args: False

    with pytest.raises(RuntimeError, match="ownership changed"):
        backend._validate_route_identity(route, observed)


def _lifecycle(status: VMHALifecycleStatus, plan: DestroyPlan) -> VMHALifecycleState:
    members = (
        VMHALifecycleMember(
            instance_index=0,
            instance_name="gateway-0",
            node_id="node-0",
            role="active",
            compute_id="compute-0",
            network_interface_name="eth0",
            public_ip="203.0.113.10",
            compute_revision="1",
            disk_id="disk-0",
            network_interface_subnet_id="subnet-0",
            primary_allocation_id="allocation-primary-0",
            public_allocation_id="allocation-public-0",
            alias_allocation_ids=("allocation-shared",),
        ),
        VMHALifecycleMember(
            instance_index=1,
            instance_name="gateway-1",
            node_id="node-1",
            role="passive",
            compute_id="compute-1",
            network_interface_name="eth0",
            public_ip="203.0.113.11",
            compute_revision="1",
            disk_id="disk-1",
            network_interface_subnet_id="subnet-0",
            primary_allocation_id="allocation-primary-1",
            public_allocation_id="allocation-public-1",
        ),
    )
    transaction = VMHAMigrationTransaction(
        operation_id="operation-before",
        approval_kind="migration",
        approval_digest="b" * 64,
        desired_state_digest="c" * 64,
        current_state_digest="d" * 64,
        checkpoint="current",
        pending_effect=None,
        completed_effects=(),
        resource_bindings=(),
        revision=1,
        predecessor_sha256=None,
        observation=(),
    )
    return VMHALifecycleState(
        status=status,
        project_id="project-a",
        gateway_name="gateway",
        cluster_id="cluster-a",
        allocation_id="allocation-shared",
        allocation_name="gateway-cluster-a-shared-private-ip",
        members=members,
        route_runtime_id="route-runtime-a",
        route_targets=(
            '{"network_id":"network-a","project_id":"project-a",'
            '"route_table_id":"route-table-0","workload_subnet_id":"subnet-workload"}',
        ),
        transaction=transaction,
        record_version=4,
    )


@pytest.mark.parametrize("saved", ["reconstruction-authority.json", "retired/lifecycle.json"])
def test_destroy_recovers_exact_native_authority_across_repair_lifecycle_reset(
    tmp_path, monkeypatch, retained_repair, saved
):
    from nebius_vpngw import repair_state
    from nebius_vpngw.deploy import destroy_repair

    old_plan, backend, repair, _ = retained_repair
    old_record = repair.load()
    plan = replace(old_plan, topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    intent = {
        **old_record["intent"],
        "ha": True,
        "targets": [
            old_record["intent"]["targets"][0],
            {
                "name": "gateway-1",
                "compute": "compute-1",
                "disk": "disk-1",
                "transport": "192.0.2.2",
                "binding": "f" * 64,
            },
        ],
    }
    record = {**old_record, "intent": intent}
    repair_state.publish(repair.path, record)
    payload = previous.to_dict()
    if saved == "reconstruction-authority.json":
        payload = {"intent": repair_state.digest(intent), "state": payload}
    repair_state.publish(repair.root / "runs" / record["id"] / saved, payload)
    assert destroy_repair.reconstruction_authority("project-a", "gateway") == previous
    backend.build_plan = lambda _: plan
    monkeypatch.setattr(destroy_module, "NebiusDestroyBackend", lambda *_a, **_k: backend)
    spec = SimpleNamespace(
        name="gateway",
        vm_ha=SimpleNamespace(
            cluster_id=previous.cluster_id,
            members=tuple(
                SimpleNamespace(
                    instance_index=m.instance_index,
                    node_id=m.node_id,
                    role=SimpleNamespace(value=m.role),
                )
                for m in previous.members
            ),
        ),
    )
    config = tmp_path / "config.yaml"
    assert not VMHALifecycleStore(config).path.exists()
    result = execute_destroy(
        config_path=config,
        config_digest=plan.config_digest,
        spec=spec,
        project_id="project-a",
        vm_manager=object(),
    )
    assert result.deleted_compute == 1 and repair.load() is None
    terminal = VMHALifecycleStore(config).read(
        expected_project_id="project-a", expected_gateway_name="gateway"
    )
    assert terminal.status is VMHALifecycleStatus.DESTROYED
    assert terminal.transaction.approval_kind == "destruction"


@pytest.mark.parametrize(
    "status",
    (
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
        VMHALifecycleStatus.ACTIVE,
        VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
        VMHALifecycleStatus.REMOVED,
    ),
)
def test_vm_ha_destruction_starts_from_every_authoritative_state(
    status: VMHALifecycleStatus,
) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(status, plan)

    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )

    assert destruction.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS
    assert destruction.transaction is not None
    assert destruction.transaction.approval_kind == "destruction"
    assert dict(destruction.transaction.resource_bindings)["destroy-plan"] == plan.to_json()


def test_partial_provisioning_destroy_retains_completed_public_allocation_for_reprovision(
    tmp_path: Path,
) -> None:
    plan = _plan(topology="vm-ha")
    members = tuple(
        replace(
            member,
            compute_id="",
            network_interface_name="",
            public_ip="",
            compute_revision="",
            disk_id="",
            network_interface_subnet_id="",
            primary_allocation_id="",
            public_allocation_id="",
            alias_allocation_ids=(),
        )
        for member in _lifecycle(VMHALifecycleStatus.ACTIVE, plan).members
    )
    partial = VMHALifecycleState.start_provisioning(
        project_id="project-a",
        gateway_name="gateway",
        cluster_id="cluster-a",
        allocation_name="gateway-cluster-a-shared-private-ip",
        members=members,  # type: ignore[arg-type]
        operation_id="operation-partial",
        approval_kind="migration",
        approval_digest="b" * 64,
        desired_state_digest="c" * 64,
        current_state_digest="d" * 64,
        initial_resource_bindings={"public-allocation:gateway-0:eth0": "allocation-public-0"},
    )
    destruction = VMHALifecycleState.start_destruction(
        partial,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    destroyed = destruction.with_status(
        VMHALifecycleStatus.DESTROYED,
        checkpoint="destruction-verified-absent",
    )
    retained = {"public-allocation:gateway-0:eth0": "allocation-public-0"}
    assert vm_ha_destroyed_retained_public_bindings(destroyed) == retained
    reprovision = _clean_reprovision(
        destroyed,
        initial_resource_bindings={
            **retained,
            "route-targets-digest": "4" * 64,
        },
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(partial, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=partial.record_sha256)
    store.write_verified(destroyed, predecessor_sha256=destruction.record_sha256)

    store.write_verified(reprovision, predecessor_sha256=destroyed.record_sha256)


@pytest.mark.parametrize(
    "public_binding",
    (
        "public-allocation:gateway-0:eth1",
        "public-allocation:foreign-gateway:eth0",
    ),
)
def test_destroyed_partial_provisioning_rejects_foreign_retained_public_binding(
    public_binding: str,
) -> None:
    plan = _plan(topology="vm-ha")
    members = tuple(
        replace(
            member,
            compute_id="",
            network_interface_name="",
            public_ip="",
            compute_revision="",
            disk_id="",
            network_interface_subnet_id="",
            primary_allocation_id="",
            public_allocation_id="",
            alias_allocation_ids=(),
        )
        for member in _lifecycle(VMHALifecycleStatus.ACTIVE, plan).members
    )
    partial = VMHALifecycleState.start_provisioning(
        project_id="project-a",
        gateway_name="gateway",
        cluster_id="cluster-a",
        allocation_name="gateway-cluster-a-shared-private-ip",
        members=members,  # type: ignore[arg-type]
        operation_id="operation-partial",
        approval_kind="migration",
        approval_digest="b" * 64,
        desired_state_digest="c" * 64,
        current_state_digest="d" * 64,
        initial_resource_bindings={public_binding: "allocation-public-0"},
    )
    destruction = VMHALifecycleState.start_destruction(
        partial,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    destroyed = destruction.with_status(
        VMHALifecycleStatus.DESTROYED,
        checkpoint="destruction-verified-absent",
    )

    with pytest.raises(ValueError, match="binding is foreign"):
        vm_ha_destroyed_retained_public_bindings(destroyed)


def test_vm_ha_destruction_rejects_populated_public_allocation_outside_eth0() -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    previous = replace(
        previous,
        members=(
            replace(previous.members[0], network_interface_name="eth1"),
            previous.members[1],
        ),
    )

    with pytest.raises(ValueError, match="must use eth0"):
        VMHALifecycleState.start_destruction(
            previous,
            operation_id="operation-destroy",
            approval_digest=plan.digest,
            desired_state_digest="e" * 64,
            current_state_digest="f" * 64,
            destroy_plan_json=plan.to_json(),
            current_observation={},
        )


def test_vm_ha_destruction_requires_ambiguous_cloud_effect_resolution() -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVATING, plan)
    transaction = replace(
        previous.transaction,
        pending_effect="provision-gateway-1-compute",
        observation=(("/present", "false"),),
        observation_guard=VMHAEffectObservationGuard(
            effect="provision-gateway-1-compute",
            permitted_paths=("/present",),
            pre_observation=(("/present", "false"),),
        ),
    )
    previous = replace(previous, transaction=transaction)

    with pytest.raises(ValueError, match="ambiguous cloud effect"):
        VMHALifecycleState.start_destruction(
            previous,
            operation_id="operation-destroy",
            approval_digest=plan.digest,
            desired_state_digest="e" * 64,
            current_state_digest="f" * 64,
            destroy_plan_json=plan.to_json(),
            current_observation={},
        )


def test_vm_ha_destruction_rejects_unaccepted_non_host_effect() -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.PROVISIONING, plan)
    previous = replace(
        previous,
        transaction=replace(
            previous.transaction,
            pending_effect="provision-gateway-1-compute",
        ),
    )

    with pytest.raises(ValueError, match="ambiguous cloud effect"):
        VMHALifecycleState.start_destruction(
            previous,
            operation_id="operation-destroy",
            approval_digest=plan.digest,
            desired_state_digest="e" * 64,
            current_state_digest="f" * 64,
            destroy_plan_json=plan.to_json(),
            current_observation={},
        )


def test_vm_ha_destruction_supersedes_only_unaccepted_host_effect() -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVATING, plan)
    previous = replace(
        previous,
        transaction=replace(
            previous.transaction,
            pending_effect="stage-gateway-1",
        ),
    )

    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )

    assert destruction.transaction is not None
    assert (
        dict(destruction.transaction.resource_bindings)["destroy-superseded-host-effect"]
        == "stage-gateway-1"
    )


def test_resolved_predecessor_operation_binds_only_its_exact_returned_resource() -> None:
    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.PROVISIONING, plan)
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = lifecycle
    backend.resolved_predecessor_effect = "provision-gateway-1-compute"
    backend.resolved_predecessor_resource_id = "compute-accepted"

    compute, _disks, _allocations, _public = backend._ha_expected_ids()

    assert compute["gateway-0"] == "compute-0"
    assert compute["gateway-1"] == "compute-accepted"


@pytest.mark.parametrize("cycle", (1, 9, 10, 11, 99, 100, 1000))
def test_vm_ha_destroy_keeps_historical_replacement_disks_in_delete_authority(cycle) -> None:
    from nebius_vpngw.deploy.vm_ha_lifecycle import vm_ha_passive_replacement_binding_key

    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    lifecycle = replace(
        lifecycle,
        transaction=replace(
            lifecycle.transaction,
            resource_bindings=(
                (vm_ha_passive_replacement_binding_key("disk", "gateway-1", cycle), "disk-current"),
                (
                    vm_ha_passive_replacement_binding_key("retired-disk", "gateway-1", cycle),
                    "disk-retired",
                ),
            ),
        ),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = lifecycle

    observed = backend._ha_authoritative_disk_ids(
        {"gateway-0-boot": "disk-0", "gateway-1-boot": "disk-current"}
    )

    assert observed == {"disk-0", "disk-current", "disk-retired"}


def _repair_disk_history(tmp_path, monkeypatch):
    from nebius_vpngw import repair_state
    from nebius_vpngw.deploy import destroy_repair

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = _lifecycle(VMHALifecycleStatus.ACTIVE, _plan(topology="vm-ha"))
    owner = destroy_repair.journal(state.project_id, state.gateway_name)
    intent = {
        "project": state.project_id,
        "gateway": state.gateway_name,
        "configuration": "a" * 64,
        "artifact": "b" * 64,
        "ha": True,
        "targets": [
            {
                "name": member.instance_name,
                "compute": member.compute_id,
                "disk": member.disk_id,
                "transport": member.public_ip,
                "binding": "c" * 64,
            }
            for member in state.members
        ],
    }

    def append(snapshot, *, target_intent=None):
        record = owner.begin(target_intent or intent)
        path = owner.root / "runs" / record["id"] / "retired" / "lifecycle.json"
        repair_state.publish(path, snapshot.to_dict())
        for phase in repair_state.PHASES[1:]:
            owner.advance(phase)
        return record, path

    archived = replace(
        state,
        transaction=replace(
            state.transaction,
            resource_bindings=(
                ("replacement-compute:gateway-1", "compute-1"),
                ("replacement-disk:gateway-1", "disk-1"),
                ("retired-compute:gateway-1", "compute-retired"),
                ("retired-disk:gateway-1", "disk-retired"),
            ),
        ),
    )
    first, path = append(archived)
    return state, owner, intent, append, first, path


def test_destroy_recovers_retired_disk_after_repeated_repair(tmp_path, monkeypatch):
    from nebius_vpngw.deploy import destroy_repair

    state, owner, _intent, append, _first, _path = _repair_disk_history(tmp_path, monkeypatch)
    append(state)
    append(state)
    before = {p: p.read_bytes() for p in owner.root.rglob("*.json")}

    assert destroy_repair.archived_retired_disks(state) == {"disk-retired"}
    assert before == {p: p.read_bytes() for p in owner.root.rglob("*.json")}


@pytest.mark.parametrize("cycle", (1, 2, 10))
def test_normal_apply_then_repair_keeps_retired_disk_authority(tmp_path, monkeypatch, cycle):
    from nebius_vpngw.cli import _vm_ha_initial_resource_bindings
    from nebius_vpngw.deploy import destroy_repair
    from nebius_vpngw.deploy.vm_ha_lifecycle import (
        _validate_successor,
        vm_ha_passive_replacement_binding_key,
        vm_ha_passive_replacement_cycles,
    )

    state, _owner, _intent, append, _first, _path = _repair_disk_history(tmp_path, monkeypatch)
    name = state.members[1].instance_name
    history = {
        vm_ha_passive_replacement_binding_key(kind, name, cycle): value
        for kind, value in {
            "approval": "d" * 64,
            "compute": state.members[1].compute_id,
            "disk": state.members[1].disk_id,
            "retired-compute": "compute-retired",
            "retired-disk": "disk-retired",
        }.items()
    }
    previous = replace(
        state,
        transaction=replace(
            state.transaction,
            resource_bindings=tuple(sorted({**history, "heartbeat-initial-artifact": "e" * 64}.items())),
            completed_effects=("old-completed-action",),
        ),
    )
    observed = {
        "members": [
            {
                "instance_name": m.instance_name,
                "present": True,
                "compute_id": m.compute_id,
                "boot_disk_id": m.disk_id,
                "network_interface_name": m.network_interface_name,
            }
            for m in state.members
        ]
    }
    bindings = _vm_ha_initial_resource_bindings(observed, previous=previous)
    assert all(bindings.get(key) == value for key, value in history.items())
    assert "heartbeat-initial-artifact" not in bindings
    assert vm_ha_passive_replacement_cycles(bindings, name) == (cycle,)
    successor = replace(
        previous,
        status=VMHALifecycleStatus.PROVISIONING,
        transaction=replace(
            previous.transaction,
            operation_id="new-ordinary-apply",
            revision=1,
            predecessor_sha256=previous.record_sha256,
            resource_bindings=tuple(sorted(bindings.items())),
            completed_effects=(),
        ),
    )
    _validate_successor(previous, successor)
    completed = replace(successor, status=VMHALifecycleStatus.ACTIVE)
    append(completed)
    append(state)
    assert destroy_repair.archived_retired_disks(state) == {"disk-retired"}
    assert not _vm_ha_initial_resource_bindings(
        observed, previous=replace(previous, status=VMHALifecycleStatus.DESTROYED)
    ).keys() & history.keys()


@pytest.mark.parametrize("change", ("missing", "corrupt", "foreign", "unbound", "unlinked"))
def test_destroy_never_adopts_unproved_archived_disk(tmp_path, monkeypatch, change):
    from nebius_vpngw import repair_state
    from nebius_vpngw.deploy import destroy_repair

    state, owner, _intent, append, first, path = _repair_disk_history(tmp_path, monkeypatch)
    append(state)
    if change == "missing":
        path.unlink()
    elif change == "corrupt":
        path.write_text("{damaged")
    elif change == "foreign":
        value = VMHALifecycleState.from_dict(json.loads(path.read_text()))
        repair_state.publish(
            path,
            replace(
                value,
                cluster_id="another-cluster",
                allocation_name="gateway-another-cluster-shared-private-ip",
            ).to_dict(),
        )
    elif change == "unbound":
        value = VMHALifecycleState.from_dict(json.loads(path.read_text()))
        repair_state.publish(
            path,
            replace(
                value,
                members=tuple(
                    replace(m, compute_id="other-" + m.compute_id) for m in value.members
                ),
                transaction=replace(value.transaction, resource_bindings=()),
            ).to_dict(),
        )
    else:
        (owner.root / "history" / (first["id"] + ".json")).unlink()

    assert destroy_repair.archived_retired_disks(state) == frozenset()


def test_destroy_repair_history_bridges_only_recorded_replacement(tmp_path, monkeypatch):
    from nebius_vpngw.deploy import destroy_repair

    old, _owner, intent, append, _first, _path = _repair_disk_history(tmp_path, monkeypatch)
    changed = replace(old.members[1], compute_id="compute-new", disk_id="disk-new")
    current = replace(old, members=(old.members[0], changed))
    bridge = replace(
        current,
        transaction=replace(
            current.transaction,
            resource_bindings=(
                ("replacement-compute:gateway-1", "compute-new"),
                ("replacement-disk:gateway-1", "disk-new"),
                ("retired-compute:gateway-1", "compute-1"),
                ("retired-disk:gateway-1", "disk-1"),
            ),
        ),
    )
    new_intent = {
        **intent,
        "targets": [
            intent["targets"][0],
            {**intent["targets"][1], "compute": "compute-new", "disk": "disk-new"},
        ],
    }
    append(bridge, target_intent=new_intent)
    append(current, target_intent=new_intent)

    assert destroy_repair.archived_retired_disks(current) == {"disk-1", "disk-retired"}
    unrelated = replace(
        current,
        members=tuple(replace(m, compute_id="foreign-" + m.compute_id) for m in current.members),
    )
    assert destroy_repair.archived_retired_disks(unrelated) == frozenset()


def test_destroy_repair_archive_symlink_cannot_grant_ownership(tmp_path, monkeypatch):
    from nebius_vpngw.deploy import destroy_repair

    state, _owner, _intent, _append, _first, path = _repair_disk_history(tmp_path, monkeypatch)
    original = path.with_name("original.json")
    path.rename(original)
    path.symlink_to(original)
    with pytest.raises((RuntimeError, OSError)):
        destroy_repair.archived_retired_disks(state)


def test_destroy_plan_includes_archived_repair_disk_and_rejects_foreign_name(tmp_path, monkeypatch):
    from nebius_vpngw.deploy import destroy_repair

    state, _owner, _intent, append, _first, _path = _repair_disk_history(tmp_path, monkeypatch)
    append(state)
    disk = SimpleNamespace(
        metadata=SimpleNamespace(id="disk-retired", name="gateway-1-boot", resource_version="7")
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = state
    backend.resolved_predecessor_effect = backend.resolved_predecessor_resource_id = None
    backend.spec = SimpleNamespace(
        name="gateway",
        instance_count=2,
        vm_spec={"num_nics": 1},
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
    )
    backend.project_id = "project-a"
    backend._get_instance_by_name = lambda _name: None
    backend._get_instance_by_id = lambda _id: None
    backend._get_disk_by_name = lambda name: disk if name == "gateway-1-boot" else None
    backend._get_disk_by_id = lambda identifier: disk if identifier == "disk-retired" else None
    backend._get_allocation_by_name = lambda _name: None
    backend._get_allocation_by_id = lambda _id: None
    backend._route_inventory = lambda: ()

    with monkeypatch.context() as original:
        original.setattr(destroy_repair, "archived_retired_disks", lambda _: frozenset())
        assert backend.build_plan("a" * 64).blockers == (
            "unbound VM-HA disk occupies gateway-1-boot",
        )
    plan = backend.build_plan("a" * 64)
    assert not plan.blockers
    assert [(d.resource_id, d.name) for d in plan.disks] == [("disk-retired", "gateway-1-boot")]
    disk.metadata.id = "foreign-disk"
    plan = backend.build_plan("a" * 64)
    assert plan.blockers and not plan.disks


@pytest.mark.parametrize("cycle", (1, 9, 10, 11, 99, 100, 1000))
def test_vm_ha_destroy_plans_present_retired_compute_for_deletion(cycle) -> None:
    from nebius_vpngw.deploy.vm_ha_lifecycle import vm_ha_passive_replacement_binding_key

    plan = _plan(topology="vm-ha")
    lifecycle = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    lifecycle = replace(
        lifecycle,
        transaction=replace(
            lifecycle.transaction,
            resource_bindings=(
                (
                    vm_ha_passive_replacement_binding_key("compute", "gateway-1", cycle),
                    "compute-current",
                ),
                (
                    vm_ha_passive_replacement_binding_key("retired-compute", "gateway-1", cycle),
                    "compute-retired",
                ),
            ),
        ),
    )
    retired = SimpleNamespace(
        metadata=SimpleNamespace(
            id="compute-retired",
            name="gateway-1",
            resource_version="7",
        ),
        spec=SimpleNamespace(boot_disk=None, network_interfaces=()),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = lifecycle
    backend.resolved_predecessor_effect = None
    backend.resolved_predecessor_resource_id = None
    backend.spec = SimpleNamespace(
        name="gateway",
        instance_count=2,
        vm_spec={"num_nics": 1},
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
    )
    backend.project_id = "project-a"
    backend._get_instance_by_name = lambda name: retired if name == "gateway-1" else None
    backend._get_instance_by_id = lambda resource_id: (
        retired if resource_id == "compute-retired" else None
    )
    backend._get_disk_by_name = lambda _name: None
    backend._get_disk_by_id = lambda _resource_id: None
    backend._get_allocation_by_name = lambda _name: None
    backend._get_allocation_by_id = lambda _resource_id: None
    backend._route_inventory = lambda: ()

    observed = backend.build_plan("a" * 64)

    assert observed.blockers == ()
    assert {(resource.name, resource.resource_id) for resource in observed.compute} == {
        ("gateway-1", "compute-retired")
    }


def _ordinary_build_plan_backend(*, route_name: str) -> NebiusDestroyBackend:
    private = SimpleNamespace(
        metadata=SimpleNamespace(
            id="allocation-private",
            name="gateway-0-eth0-private-ip",
            resource_version="3",
        ),
        spec=SimpleNamespace(ipv4_private=object(), ipv4_public=None),
    )
    public = SimpleNamespace(
        metadata=SimpleNamespace(
            id="allocation-public",
            name="gateway-0-eth0-public-ip",
            resource_version="4",
        ),
        spec=SimpleNamespace(ipv4_private=None, ipv4_public=object()),
    )
    disk = SimpleNamespace(
        metadata=SimpleNamespace(
            id="disk-0",
            name="gateway-0-boot",
            resource_version="2",
        )
    )
    instance = SimpleNamespace(
        metadata=SimpleNamespace(
            id="compute-0",
            name="gateway-0",
            resource_version="1",
        ),
        spec=SimpleNamespace(
            boot_disk=SimpleNamespace(existing_disk=SimpleNamespace(id="disk-0")),
            network_interfaces=(
                SimpleNamespace(
                    name="eth0",
                    ip_address=SimpleNamespace(allocation_id="allocation-private"),
                    public_ip_address=SimpleNamespace(allocation_id="allocation-public"),
                ),
            ),
        ),
    )
    route = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-0",
            name=route_name,
            resource_version="5",
        ),
        spec=SimpleNamespace(
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id="allocation-private"),
            )
        ),
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.lifecycle_state = None
    backend.spec = SimpleNamespace(
        name="gateway",
        instance_count=1,
        vm_spec={"num_nics": 1},
        vm_ha=None,
    )
    backend.project_id = "project-a"
    backend._get_instance_by_name = lambda name: instance if name == "gateway-0" else None
    backend._get_instance_by_id = lambda resource_id: (
        instance if resource_id == "compute-0" else None
    )
    backend._get_disk_by_name = lambda name: disk if name == "gateway-0-boot" else None
    backend._get_disk_by_id = lambda resource_id: disk if resource_id == "disk-0" else None
    allocations = {
        "allocation-private": private,
        "allocation-public": public,
    }
    backend._get_allocation_by_name = lambda name: next(
        (allocation for allocation in allocations.values() if allocation.metadata.name == name),
        None,
    )
    backend._get_allocation_by_id = lambda resource_id: allocations.get(resource_id)
    backend._route_inventory = lambda: (("route-table-0", route),)
    return backend


def test_ordinary_destroy_build_plan_selects_only_exact_owned_resources() -> None:
    plan = _ordinary_build_plan_backend(route_name="vpngw-remote-prefix").build_plan("a" * 64)

    assert plan.blockers == ()
    assert [(item.name, item.resource_id) for item in plan.compute] == [("gateway-0", "compute-0")]
    assert [(item.name, item.resource_id) for item in plan.disks] == [("gateway-0-boot", "disk-0")]
    assert [(item.name, item.resource_id) for item in plan.allocations] == [
        ("gateway-0-eth0-private-ip", "allocation-private")
    ]
    assert [(item.name, item.resource_id) for item in plan.retained_public_allocations] == [
        ("gateway-0-eth0-public-ip", "allocation-public")
    ]
    assert [(item.name, item.route_id) for item in plan.routes] == [
        ("vpngw-remote-prefix", "route-0")
    ]
    assert plan.retained_route_table_ids == ("route-table-0",)


def test_ordinary_destroy_build_plan_blocks_a_foreign_route_to_its_private_allocation() -> None:
    plan = _ordinary_build_plan_backend(route_name="foreign-route").build_plan("a" * 64)

    assert plan.routes == ()
    assert plan.blockers == ("foreign route foreign-route references a gateway allocation",)


@pytest.mark.parametrize(
    ("fault", "expected_blocker"),
    (
        ("missing-private", "attached allocation disappeared from gateway-0"),
        ("wrong-private-shape", "attached allocation shape changed for gateway-0"),
        ("unconfigured-private", "unconfigured private allocation is attached to gateway-0"),
        ("missing-disk", "boot disk name gateway-0-boot was reused"),
        ("nameless-disk", "boot disk for gateway-0-boot has no exact name"),
        ("unbound-disk-name", "boot disk name binding changed for gateway-0-boot"),
        ("missing-route-id", "managed gateway route has no exact identity"),
    ),
)
def test_ordinary_destroy_build_plan_fails_closed_on_inventory_drift(
    fault: str,
    expected_blocker: str,
) -> None:
    backend = _ordinary_build_plan_backend(route_name="vpngw-remote-prefix")
    private = backend._get_allocation_by_id("allocation-private")
    disk = backend._get_disk_by_id("disk-0")
    route = backend._route_inventory()[0][1]
    assert private is not None
    assert disk is not None

    if fault == "missing-private":
        original_get_allocation = backend._get_allocation_by_id
        backend._get_allocation_by_id = lambda resource_id: (
            None if resource_id == "allocation-private" else original_get_allocation(resource_id)
        )
    elif fault == "wrong-private-shape":
        private.spec.ipv4_private = None
        private.spec.ipv4_public = object()
    elif fault == "unconfigured-private":
        private.metadata.name = "foreign-private"
    elif fault == "missing-disk":
        backend._get_disk_by_id = lambda _resource_id: None
    elif fault == "nameless-disk":
        disk.metadata.name = ""
    elif fault == "unbound-disk-name":
        backend._get_disk_by_name = lambda _name: None
    elif fault == "missing-route-id":
        route.metadata.id = ""
    else:  # pragma: no cover - the parameter table is closed above
        raise AssertionError(f"unknown fault: {fault}")

    plan = backend.build_plan("a" * 64)

    assert expected_blocker in plan.blockers


def test_ordinary_destroy_preflight_rechecks_the_exact_plan_and_route_set() -> None:
    backend = _ordinary_build_plan_backend(route_name="vpngw-remote-prefix")
    plan = backend.build_plan("a" * 64)
    planned_route = backend._route_inventory()[0][1]
    backend._route_by_id = lambda _route: planned_route

    backend.preflight(plan, require_revisions=True)

    unexpected_route = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-unplanned",
            name="foreign-route",
            resource_version="6",
        ),
        spec=SimpleNamespace(
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id="allocation-private"),
            )
        ),
    )
    backend._route_inventory = lambda: (
        ("route-table-0", planned_route),
        ("route-table-0", unexpected_route),
    )

    with pytest.raises(
        RuntimeError,
        match="An unplanned route references a private allocation selected for destroy",
    ):
        backend.preflight(plan)


def test_destroy_inventory_reads_every_page_and_rejects_token_cycles() -> None:
    class Request:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class Client:
        def __init__(self) -> None:
            self.cycle = False

        def list(self, request: Request, **_kwargs):
            assert request.parent_id == "project-a"
            assert request.page_size == 1000
            if request.page_token == "":
                return SimpleNamespace(items=["first"], next_page_token="second")
            return SimpleNamespace(
                items=["second"],
                next_page_token="second" if self.cycle else "",
            )

    client = Client()
    assert NebiusDestroyBackend._list_items(
        client,
        Request,
        parent_id="project-a",
    ) == ("first", "second")
    client.cycle = True

    with pytest.raises(RuntimeError, match="inventory is unavailable"):
        NebiusDestroyBackend._list_items(client, Request, parent_id="project-a")


@pytest.mark.parametrize(
    ("client_attribute", "lookup_name"),
    (
        ("instances", "_get_instance_by_name"),
        ("disks", "_get_disk_by_name"),
        ("allocations", "_get_allocation_by_name"),
    ),
)
def test_destroy_exact_name_reads_reject_foreign_parent(
    client_attribute: str,
    lookup_name: str,
) -> None:
    resource = SimpleNamespace(
        metadata=SimpleNamespace(
            id="resource-1",
            name="gateway-resource",
            parent_id="foreign-project",
        )
    )
    client = SimpleNamespace(
        get_by_name=lambda _request, **_kwargs: SimpleNamespace(wait=lambda: resource)
    )
    backend = object.__new__(NebiusDestroyBackend)
    backend.project_id = "project-a"
    setattr(backend, client_attribute, client)

    with pytest.raises(RuntimeError, match="inexact identity"):
        getattr(backend, lookup_name)("gateway-resource")


@pytest.mark.parametrize(
    ("raw_state", "deleted"),
    (
        ("STOPPED", False),
        ("RUNNING", True),
        ("STOPPING", True),
        ("CREATING", True),
        ("ERROR", True),
        ("UNRECOGNIZED", True),
    ),
)
def test_cloud_fence_deletes_every_non_stopped_compute_state(
    raw_state: str,
    deleted: bool,
) -> None:
    operation = SimpleNamespace(id="cloud-operation")
    rpc_waits = 0
    delete_requests: list[str] = []

    class Request:
        def wait(self):
            nonlocal rpc_waits
            rpc_waits += 1
            return operation

    class Instances:
        def stop(self, _request, **_kwargs):
            raise AssertionError("destroy must not depend on a successful stop")

        def delete(self, request, **_kwargs):
            delete_requests.append(request.id)
            return Request()

    backend = object.__new__(NebiusDestroyBackend)
    backend.instances = Instances()
    backend._strict_resource = lambda _resource: SimpleNamespace(
        status=SimpleNamespace(state=raw_state)
    )
    resource = DestroyResource("compute", "gateway-0", "compute-0")

    observed = backend.submit_stop(resource, "operation-key")

    assert (observed is operation) is deleted
    assert delete_requests == (["compute-0"] if deleted else [])
    assert rpc_waits == int(deleted)


@pytest.mark.parametrize(
    ("effect", "resource_id"),
    (
        ("replace-failed-gateway-1-create-compute", "compute-attempt"),
        ("replace-failed-gateway-1-delete-compute", ""),
    ),
)
def test_terminal_failed_predecessor_operation_is_resolved_for_destroy(
    monkeypatch: pytest.MonkeyPatch,
    effect: str,
    resource_id: str,
) -> None:
    operation = SimpleNamespace(
        done=lambda: True,
        successful=lambda: False,
        result=None,
        resource_id=resource_id,
    )

    class Service:
        def get(self, _request, **_kwargs):
            return operation

    backend = object.__new__(NebiusDestroyBackend)
    backend.client = object()
    backend.vm_manager = SimpleNamespace(
        _vm_ha_operation_service=lambda _client, _effect: Service()
    )
    monkeypatch.setattr(destroy_module, "wait_vm_ha_operation", lambda _operation: None)

    resolution = backend.resume_prior_vm_ha_operation(effect, "cloud-operation")

    assert resolution.succeeded is False
    assert resolution.resource_id == resource_id


def test_destroy_owned_terminal_failed_operation_is_typed_for_supersession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = SimpleNamespace(done=lambda: True, successful=lambda: False)

    class Service:
        def get(self, _request, **_kwargs):
            return operation

    backend = object.__new__(NebiusDestroyBackend)
    backend._operation_service = lambda _kind: Service()
    monkeypatch.setattr(destroy_module, "wait_vm_ha_operation", lambda _operation: None)

    resolution = backend.resume_operation("compute", "cloud-operation")

    assert resolution == DestroyOperationResume(
        lookup_supported=True,
        succeeded=False,
    )


def test_destroy_owned_unsupported_operation_lookup_requires_idempotent_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedLookup(RuntimeError):
        pass

    class Service:
        def get(self, _request, **_kwargs):
            raise UnsupportedLookup

    backend = object.__new__(NebiusDestroyBackend)
    backend._operation_service = lambda _kind: Service()
    monkeypatch.setattr(
        destroy_module,
        "operation_status_lookup_unsupported",
        lambda error: isinstance(error, UnsupportedLookup),
    )

    resolution = backend.resume_operation("route", "cloud-operation")

    assert resolution == DestroyOperationResume(
        lookup_supported=False,
        succeeded=None,
    )


def test_vm_ha_destroy_does_not_require_apply_runtime_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    config_path = tmp_path / "gateway.config.yaml"
    VMHALifecycleStore(config_path).write_verified(previous, predecessor_sha256=None)

    vm_ha = SimpleNamespace(
        cluster_id=previous.cluster_id,
        members=tuple(
            SimpleNamespace(
                instance_index=member.instance_index,
                node_id=member.node_id,
                role=SimpleNamespace(value=member.role),
            )
            for member in previous.members
        ),
    )
    spec = SimpleNamespace(name=previous.gateway_name, vm_ha=vm_ha)

    class Backend:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def build_plan(self, _config_digest: str) -> DestroyPlan:
            return plan

        def preflight(self, _plan: DestroyPlan, *, require_revisions: bool = False) -> None:
            assert require_revisions

    class Coordinator:
        def __init__(self, _backend, _journal, *, repair=None) -> None:
            assert repair is None

        def run(self) -> DestroyResult:
            return DestroyResult(
                topology="vm-ha",
                deleted_compute=1,
                deleted_disks=1,
                deleted_routes=1,
                deleted_allocations=1,
                already_absent=False,
            )

    class Manager:
        def observe_vm_ha_migration_state(self, *_args, **_kwargs):
            raise AssertionError("destroy must not require apply runtime observation")

    monkeypatch.setattr(destroy_module, "VMHAApplyLock", lambda **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(destroy_module, "NebiusDestroyBackend", Backend)
    monkeypatch.setattr(destroy_module, "DestroyCoordinator", Coordinator)

    result = execute_destroy(
        config_path=config_path,
        config_digest="a" * 64,
        spec=spec,
        project_id=previous.project_id,
        vm_manager=Manager(),
    )

    assert result.topology == "vm-ha"
    observed = VMHALifecycleStore(config_path).read(
        expected_project_id=previous.project_id,
        expected_gateway_name=previous.gateway_name,
    )
    assert observed is not None
    assert observed.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS
    assert observed.transaction is not None
    assert observed.transaction.observation == normalize_vm_ha_observation(
        {
            "schema": "nebius-vpngw/destroy-observation-v1",
            "plan_digest": plan.digest,
        }
    )


def test_vm_ha_destroy_journal_commits_explicit_destroyed_terminal(tmp_path: Path) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)
    journal = VMHADestroyJournal(VMHALifecycleJournal(store, destruction))

    journal.finish()

    observed = store.read(
        expected_project_id="project-a",
        expected_gateway_name="gateway",
    )
    assert observed is not None
    assert observed.status is VMHALifecycleStatus.DESTROYED
    assert observed.transaction is not None
    assert observed.transaction.checkpoint == "destruction-verified-absent"


def _clean_reprovision(
    destroyed: VMHALifecycleState,
    *,
    initial_resource_bindings: dict[str, str],
    current_observation: dict | None = None,
) -> VMHALifecycleState:
    clean_members = tuple(
        replace(
            member,
            compute_id="",
            network_interface_name="",
            public_ip="",
            compute_revision="",
            disk_id="",
            network_interface_subnet_id="",
            primary_allocation_id="",
            public_allocation_id="",
            alias_allocation_ids=(),
        )
        for member in destroyed.members
    )
    return VMHALifecycleState.start_provisioning(
        project_id="project-a",
        gateway_name="gateway",
        cluster_id="cluster-b",
        allocation_name="gateway-cluster-b-shared-private-ip",
        members=clean_members,  # type: ignore[arg-type]
        operation_id="operation-reprovision",
        approval_kind="migration",
        approval_digest="1" * 64,
        desired_state_digest="2" * 64,
        current_state_digest="3" * 64,
        predecessor_sha256=destroyed.record_sha256,
        initial_resource_bindings=initial_resource_bindings,
        current_observation=current_observation or {},
    )


def test_destroyed_vm_ha_lifecycle_can_start_clean_reprovision(tmp_path: Path) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    destroyed = destruction.with_status(
        VMHALifecycleStatus.DESTROYED,
        checkpoint="destruction-verified-absent",
    )
    reprovision = _clean_reprovision(
        destroyed,
        initial_resource_bindings={
            "public-allocation:gateway-0:eth0": "allocation-public-0",
            "public-allocation:gateway-1:eth0": "allocation-public-1",
            "route-targets-digest": "4" * 64,
        },
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)
    store.write_verified(destroyed, predecessor_sha256=destruction.record_sha256)

    store.write_verified(reprovision, predecessor_sha256=destroyed.record_sha256)

    observed = store.read(
        expected_project_id="project-a",
        expected_gateway_name="gateway",
    )
    assert observed is not None
    assert observed.status is VMHALifecycleStatus.PROVISIONING
    assert observed.cluster_id == "cluster-b"


def _fresh_enforced_plan(project="project-a"):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import Subnet, SubnetSpec

    from nebius_vpngw.deploy.ha_enforced_tables import freeze
    from nebius_vpngw.schema import VMHARouteTarget

    target = VMHARouteTarget(project_id=project, network_id="network",
                            workload_subnet_id="workload", route_table_id="table")
    subnet = Subnet(metadata=ResourceMetadata(id="workload", parent_id=project,
                                             resource_version=1),
                    spec=SubnetSpec(network_id="network", route_table_id="table"))
    return freeze(project=project, gateway="gateway", cluster="cluster-b", allocation="",
                  targets=(target,), subnets={"workload": subnet}, routes={"table": ()},
                  consumers={"table": {"workload"}}, desired={"workload": ["0.0.0.0/0"]},
                  previous={})


@pytest.mark.parametrize("corruption", [None, "unobserved", "changed-observation", "gateway",
                                       "cluster", "allocation", "targets", "target-digest",
                                       "project", "binding-missing"])
def test_destroyed_reprovision_binds_only_fresh_observed_enforcement(tmp_path, corruption):
    from nebius_vpngw.apply_approval import digest

    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous, operation_id="operation-destroy", approval_digest=plan.digest,
        desired_state_digest="e" * 64, current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(), current_observation={},
    )
    destroyed = destruction.with_status(VMHALifecycleStatus.DESTROYED,
                                        checkpoint="destruction-verified-absent")
    enforced = _fresh_enforced_plan("foreign" if corruption == "project" else "project-a")
    assert enforced is not None
    if corruption in {"gateway", "cluster", "allocation"}:
        enforced[corruption] = "foreign"
    raw = json.dumps(enforced, sort_keys=True, separators=(",", ":"))
    observation = {"enforced_table_plan": raw, "route_targets": enforced["targets"]}
    bindings = {
        **vm_ha_destroyed_retained_public_bindings(destroyed),
        "route-targets-digest": digest(enforced["targets"]), "enforced-route-plan": raw,
    }
    if corruption == "unobserved":
        observation.pop("enforced_table_plan")
    elif corruption == "changed-observation":
        observation["enforced_table_plan"] = "{}"
    elif corruption == "targets":
        observation["route_targets"] = []
    elif corruption == "target-digest":
        bindings["route-targets-digest"] = "f" * 64
    elif corruption == "binding-missing":
        bindings.pop("enforced-route-plan")
    reprovision = _clean_reprovision(destroyed, initial_resource_bindings=bindings,
                                    current_observation=observation)
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)
    store.write_verified(destroyed, predecessor_sha256=destruction.record_sha256)
    if corruption is not None:
        with pytest.raises(ValueError):
            store.write_verified(reprovision, predecessor_sha256=destroyed.record_sha256)
    else:
        store.write_verified(reprovision, predecessor_sha256=destroyed.record_sha256)
        assert store.read(expected_project_id="project-a", expected_gateway_name="gateway") == reprovision


@pytest.mark.parametrize(
    "public_bindings",
    (
        {},
        {
            "public-allocation:gateway-0:eth0": "foreign-public-0",
            "public-allocation:gateway-1:eth0": "allocation-public-1",
        },
    ),
)
def test_destroyed_vm_ha_lifecycle_requires_exact_retained_public_bindings(
    tmp_path: Path,
    public_bindings: dict[str, str],
) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    destroyed = destruction.with_status(
        VMHALifecycleStatus.DESTROYED,
        checkpoint="destruction-verified-absent",
    )
    reprovision = _clean_reprovision(
        destroyed,
        initial_resource_bindings={
            **public_bindings,
            "route-targets-digest": "4" * 64,
        },
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)
    store.write_verified(destroyed, predecessor_sha256=destruction.record_sha256)

    with pytest.raises(ValueError, match="cannot start an inexact reprovision"):
        store.write_verified(reprovision, predecessor_sha256=destroyed.record_sha256)


@pytest.mark.parametrize(
    "stale_binding",
    (
        ("retired-disk:gateway-1", "disk-retired"),
        ("replacement-compute:gateway-1", "compute-replacement"),
        ("route-runtime-id", "route-runtime-stale"),
        ("apply-lock-cycle", "operation-before-destroy"),
        ("ordinary-route-source", "{}"),
        ("enforced-route-cutover", "{}"),
        ("enforced-route-preparation", "{}"),
        ("enforced-route-lock:gateway-0", "{}"),
        ("standby-replacement-disk-name:gateway-1", "gateway-1-boot"),
    ),
)
def test_destroyed_vm_ha_lifecycle_rejects_stale_resource_binding(
    tmp_path: Path,
    stale_binding: tuple[str, str],
) -> None:
    plan = _plan(topology="vm-ha")
    previous = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = VMHALifecycleState.start_destruction(
        previous,
        operation_id="operation-destroy",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    destroyed = destruction.with_status(
        VMHALifecycleStatus.DESTROYED,
        checkpoint="destruction-verified-absent",
    )
    reprovision = _clean_reprovision(
        destroyed,
        initial_resource_bindings={
            "route-targets-digest": "4" * 64,
            stale_binding[0]: stale_binding[1],
        },
    )
    store = VMHALifecycleStore(tmp_path / "gateway.config.yaml")
    store.write_verified(previous, predecessor_sha256=None)
    store.write_verified(destruction, predecessor_sha256=previous.record_sha256)
    store.write_verified(destroyed, predecessor_sha256=destruction.record_sha256)

    with pytest.raises(ValueError, match="cannot start an inexact reprovision"):
        store.write_verified(reprovision, predecessor_sha256=destroyed.record_sha256)
