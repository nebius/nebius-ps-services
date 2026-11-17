"""Activation of reconstructed retained hosts through the canonical owners."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .. import repair_guest
from .. import repair_state as storage
from . import repair_progress
from .activation_recovery import Recovery
from .repair_progress import Phase


def ha_operation(coordinator: Any) -> str:
    """Bind the canonical HA operation to its repair execution and intent."""
    return storage.digest(
        {
            "domain": "nebius-vpngw/repair-ha-activation-v1",
            "execution": coordinator.operation,
            "intent": storage.digest(coordinator.admission.intent),
        }
    )


def archive_file(path: Path, destination: Path) -> None:
    """Retire opaque product metadata without following links or removing locks."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("Repair metadata retirement requires an owned regular file")
    storage.directory(destination.parent)
    if destination.exists():
        if destination.read_bytes() != path.read_bytes():
            raise RuntimeError("Repair retired metadata changed before reconciliation")
        path.unlink()
    elif info.st_nlink == 1:
        os.rename(path, destination)
        destination.chmod(0o600)
    else:
        # Interrupted atomic publication can leave active and candidate names
        # linked to the same owned inode. Preserve the bytes independently;
        # never modify that inode or unlink any name outside this exact scope.
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(path.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        path.unlink()
    storage.sync(path.parent)
    storage.sync(destination.parent)


class Activation:
    """Exact repair authority adapter; old normal-apply journals grant no authority."""

    def __init__(self, coordinator: Any):
        self.owner = coordinator
        self.operation = ha_operation(coordinator)
        self.resumed = coordinator.resumed
        self.intent: dict[str, Any] = {"members": {}}
        self._recovery = Recovery(
            coordinator.directory,
            execution=coordinator.operation,
            authority=storage.digest(coordinator.admission.intent),
        )
        self._recovery.initialize()

    def recovery(self) -> Recovery:
        return self._recovery

    def snapshot(self) -> dict:
        owner = self.owner
        return {
            "transaction": {
                "execution_id": owner.operation,
                "approval": storage.digest(owner.admission.intent),
                "plan": {"artifact_sha256": owner.admission.intent["artifact"]},
            }
        }

    def verify_packages(self, artifact: Any, operation: str) -> None:
        if (
            operation != self.operation
            or artifact.sha256 != self.owner.admission.intent["artifact"]
        ):
            raise RuntimeError("Repair activation changed its installed artifact")
        for name in self.owner.instances:
            self.owner.remote(name, "verify-installed", request=self.owner.package_request(name))

    def enrolled(self, transaction: Any) -> None:
        # The canonical mTLS owner has independently checked both node identities.
        self.owner.advance("enrolled")

    def allow_activation(self, instance: Any, operation: str) -> None:
        if operation != self.operation:
            raise RuntimeError("Repair activation operation changed")
        self.owner.remote(instance.hostname, "prepare-activation")
        self.owner.remote(instance.hostname, "grant", units=list(repair_guest.UNITS))


def bind_ha(owner: Any) -> tuple[Any, Any, Any]:
    """Persist fresh ACTIVATING authority for exact retained Compute resources."""
    from .. import cli
    from ..schema import VMHARouteTarget
    from ..vm_ha_managed_credentials import ensure_managed_vm_ha_credentials
    from . import ordinary_route_migration, repair_routes
    from .vm_ha_lifecycle import (
        VMHALifecycleJournal,
        VMHALifecycleState,
        VMHALifecycleStatus,
        VMHALifecycleStore,
        VMHAMigrationTransaction,
        normalize_vm_ha_observation,
    )

    admission = owner.admission
    operation = ha_operation(owner)
    manager, plan = admission.manager, admission.plan
    retained = manager._vm_ha_journal
    if retained is not None and (
        retained.state.transaction is None or retained.state.transaction.operation_id != operation
    ):
        raise RuntimeError("Repair lifecycle cannot cross confirmed authority")
    # The retained ordinary route may still be serving an interrupted
    # conversion. Its original creation receipts remain the only authority
    # for migration; rebuilding software must not discard that handoff.
    repair_routes.prepare(owner)
    credentials = ensure_managed_vm_ha_credentials(
        admission.credential_plan,
        node_ids=tuple(item.vm_ha_node.node_id for item in admission.instances),
        tenant_id=admission.local.get("tenant_id"),
        region_id=plan.gateway_group.region,
    ).credentials
    manager._vm_ha_credentials = credentials
    observed = manager.observe_vm_ha_migration_state(
        plan.gateway_group, plan.gateway.get("local_prefixes")
    )
    manager._vm_ha_shared_allocation_id = observed["shared_allocation"]["allocation_id"]
    manager._vm_ha_route_targets = tuple(
        VMHARouteTarget.model_validate(item) for item in observed["route_targets"]
    )
    binding = manager._build_vm_ha_runtime_binding(owner.cloud.sdk, plan.gateway_group)
    members = manager.build_vm_ha_lifecycle_members(plan.gateway_group, admission.targets)
    store = VMHALifecycleStore(admission.path)
    if not owner.context.get("lifecycle_bound"):
        # A lost reply after the initial write is recognized by its new identity.
        try:
            previous = store.read(
                expected_project_id=manager.project_id,
                expected_gateway_name=plan.gateway_group.name,
            )
        except ValueError:
            previous = None
        if (
            previous is None
            or previous.transaction is None
            or previous.transaction.operation_id != operation
        ):
            resources = cli._vm_ha_initial_resource_bindings(
                observed, credential_bindings=credentials.resource_bindings()
            )
            route_source = ordinary_route_migration.capture(
                manager.project_id,
                plan.gateway_group.name,
                {instance.hostname: {} for instance in admission.instances},
            )
            if route_source is not None:
                resources[ordinary_route_migration.KEY] = json.dumps(
                    route_source, sort_keys=True, separators=(",", ":")
                )
            resources.update(
                {
                    "route-runtime-id": binding.route_runtime_id,
                    "heartbeat-initial-artifact": admission.intent["artifact"],
                }
            )
            for member in members:
                receipt = owner.context["installed"][member.instance_name]
                proof = {
                    "operation": operation,
                    "compute": member.compute_id,
                    "disk": member.disk_id,
                    "artifact": admission.intent["artifact"],
                    "boot": receipt["boot"],
                    "guard": receipt["guard"],
                }
                if proof["guard"] != hashlib.sha256(repair_guest.GUARD).hexdigest():
                    raise RuntimeError("Repair lifecycle lacks its independent reset proof")
                resources["repair-reset:" + member.instance_name] = json.dumps(
                    proof, sort_keys=True, separators=(",", ":")
                )
            previous = VMHALifecycleState(
                status=VMHALifecycleStatus.ACTIVATING,
                project_id=manager.project_id,
                gateway_name=plan.gateway_group.name,
                cluster_id=binding.cluster_id,
                allocation_id=binding.shared_allocation_id,
                allocation_name=f"{plan.gateway_group.name}-{binding.cluster_id}-shared-private-ip",
                members=members,
                route_runtime_id=binding.route_runtime_id,
                route_targets=tuple(
                    sorted(
                        json.dumps(
                            item.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                        )
                        for item in binding.route_targets
                    )
                ),
                transaction=VMHAMigrationTransaction(
                    operation_id=operation,
                    approval_kind="repair",
                    approval_digest=storage.digest(admission.intent),
                    desired_state_digest=cli._canonical_digest(
                        cli._vm_ha_desired_approval_state(plan)
                    ),
                    current_state_digest=storage.digest(observed),
                    checkpoint="repair-installed",
                    pending_effect=None,
                    completed_effects=tuple(
                        sorted("repair-reset-" + item.node_id for item in members)
                    ),
                    resource_bindings=tuple(sorted(resources.items())),
                    observation=normalize_vm_ha_observation(observed),
                    revision=1,
                    predecessor_sha256=None,
                ),
            )
            # Keep exact native resource/route authority outside the lifecycle
            # reset window. Explicit destroy can use it even if this process
            # stops after archiving the old sidecar and before publishing the new.
            storage.publish(
                owner.directory / "reconstruction-authority.json",
                {
                    "intent": storage.digest(admission.intent),
                    "state": previous.to_dict(),
                },
            )
            archive_file(store.path, owner.directory / "retired" / "lifecycle.json")
            store.write_verified(previous)
        owner.context["lifecycle_bound"] = True
        owner.save()
    current = store.read(
        expected_project_id=manager.project_id, expected_gateway_name=plan.gateway_group.name
    )
    if (
        current is None
        or current.transaction is None
        or current.transaction.operation_id != operation
        or current.transaction.approval_kind != "repair"
    ):
        raise RuntimeError("Repair lifecycle authority changed")
    journal = manager._vm_ha_journal
    if journal is None:
        journal = VMHALifecycleJournal(store, current)
        manager.set_vm_ha_lifecycle_journal(journal)
    elif journal.state != current:
        raise RuntimeError("Repair lifecycle authority changed before runtime binding")
    # Recheck current Compute and allocation authority with the persisted route
    # observation attached, before publishing either member's runtime config.
    binding = manager._build_vm_ha_runtime_binding(owner.cloud.sdk, plan.gateway_group)
    return binding, journal, admission.credential_plan


def activate_ha(owner: Any, result_report: Any) -> None:
    from .. import cli

    admission = owner.admission
    with repair_progress.step(Phase.BIND):
        binding, journal, credential_plan = bind_ha(owner)
    authority = Activation(owner)
    from .vm_ha_lifecycle import VMHALifecycleStatus

    if journal.state.status is VMHALifecycleStatus.ACTIVE:
        # A lost controller reply after ACTIVE persistence must not reinstall,
        # relock, or disturb the owner. Re-prove its exact terminal conditions.
        owner_id = cli._vm_ha_bound_owner_node_id(binding, journal.state)
        statuses = {}
        for instance in admission.instances:
            node = instance.vm_ha_node.node_id
            active = node == owner_id

            def terminal(value: dict, *, active: bool = active) -> bool:
                return bool(
                    value.get("data_plane_mode") == ("active" if active else "passive")
                    and value.get("observed_owner_node_id") == owner_id
                    and value.get("pending_operation_id") is None
                    and (
                        not active
                        or value.get("promotion_ready") is True
                        and cli._vm_ha_active_route_receipt_matches(
                            value, active_node_id=owner_id, runtime_binding=binding
                        )
                    )
                )

            statuses[node] = cli._wait_for_vm_ha_agent_status(
                predicate=terminal,
                target=admission.targets[instance.hostname],
                hostname=instance.hostname,
                username=admission.local["gateway_group"].get("vm_spec", {}).get("ssh_username")
                or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
                key_path=admission.manager._management_key_path,
                client_auth=cli._gateway_ssh_client_auth(admission.local),
                ssh_policy=admission.ssh._ssh_policy,
                inst_cfg=instance,
                runtime_binding=binding,
                expected_apply_locked=False,
                expected_operation_id=authority.operation,
            )
        from . import pair_phases

        pair_phases.reached("prepared", authority, evidence=statuses)
        pair_phases.reached("originating", authority, evidence=statuses)
        return
    cli._activate_bound_vm_ha(
        _config_target=lambda instance: admission.targets[instance.hostname],
        apply_report=SimpleNamespace(artifact=admission.artifact),
        command_journal=authority,
        enrollment_hosts=frozenset(),
        finish_attachments=lambda: None,
        ha_command=authority,
        ha_intent=authority.intent,
        lifecycle_journal=journal,
        lifecycle_state=journal.state,
        lifecycle_targets=admission.targets,
        local_cfg=admission.local,
        local_config_file=admission.path,
        management_key_path=admission.manager._management_key_path,
        ordinary_handoffs={},
        plan=admission.plan,
        planned_instances=admission.instances,
        prepare_vm_ha_peer_rotation=False,
        recreate_gw=False,
        result_report=result_report,
        ssh=admission.ssh,
        ssh_client_auth=cli._gateway_ssh_client_auth(admission.local),
        ssh_policy=admission.ssh._ssh_policy,
        standby_replacement_inhibition=None,
        upgrade_recovery=None,
        vm_ha_credential_plan=credential_plan,
        vm_ha_existing_members=admission.targets,
        vm_ha_migration_active_name=None,
        vm_ha_missing_standby_replacement=None,
        vm_ha_progress_sink=repair_progress.activation,
        vm_ha_runtime_binding=binding,
        vm_spec=admission.local["gateway_group"].get("vm_spec", {}),
        repair=authority,
    )


def activate_ordinary(owner: Any) -> None:
    """Rebuild ordinary runtime through its canonical guest and route owners."""
    from contextlib import ExitStack

    from .. import apply_approval
    from . import ordinary_apply, ordinary_command, pair_phases, repair_routes
    from .apply_command import CommandJournal
    from .ordinary_route_command import prepare as prepare_routes
    from .ordinary_route_ownership import RouteJournal
    from .ordinary_route_publication import inspect as inspect_publication

    admission = owner.admission
    repair_routes.prepare(owner)
    if "ordinary_route_handoff" not in owner.context:
        owner.context["ordinary_route_handoff"] = RouteJournal(
            admission.intent["project"], admission.intent["gateway"]
        ).snapshot()
        owner.save()
    journal = CommandJournal(
        admission.intent["project"],
        admission.intent["gateway"],
        root=owner.directory / "ordinary-command",
    )
    with ExitStack() as resources:
        resources.enter_context(journal)
        targets = admission.manager.inspect_ordinary_targets(sorted(owner.instances))
        expected = {
            name: (admission.bindings[name]["compute"], admission.targets[name])
            for name in owner.instances
        }
        if targets != expected:
            raise RuntimeError("Ordinary repair activation changed a retained target")
        manifests = ordinary_command.package_manifests(
            journal, local=admission.local, targets=targets, artifact=admission.intent["artifact"]
        )
        plans = {}
        for instance in admission.instances:
            if instance.hostname not in manifests:
                # Restore the pre-repair package-start policy before planning.
                # A pending ordinary transaction already owns that policy and
                # must resume unchanged, including its temporary inhibition.
                owner.remote(instance.hostname, "prepare-activation")
            with repair_progress.step(Phase.PLAN, repair_progress.member_index(owner, instance.hostname)):
                prepared = ordinary_apply.inspect_plan(
                    admission.ssh,
                    admission.targets[instance.hostname],
                    instance,
                    admission.local,
                    target_identity=admission.bindings[instance.hostname]["compute"],
                    **(
                        {"resume_manifest": manifests[instance.hostname]}
                        if instance.hostname in manifests
                        else {}
                    ),
                )
            resources.callback(prepared.close)
            plans[instance.hostname] = prepared
        publication = inspect_publication(
            journal,
            admission.ssh,
            admission.instances,
            admission.local,
            targets,
            repair={
                "operation": owner.operation,
                "journal": owner.context["ordinary_route_handoff"],
            },
        )
        routes = prepare_routes(
            admission.manager,
            admission.plan,
            admission.local,
            ssh_policy=admission.ssh._ssh_policy,
            runtime_changes=any(not value.noop for value in plans.values()),
            publication=publication,
        )
        command = ordinary_command.inspect(
            journal,
            local=admission.local,
            targets=targets,
            plans=plans,
            attachments=None,
            attachment_pending=None,
            artifact_sha256=admission.intent["artifact"],
            routes=routes,
            publication=publication,
        )
        if command.plan.effects:
            for prepared in plans.values():
                prepared.manifest["command_approval"] = apply_approval.bind(
                    command.approval, command.plan.mapping()
                )
            command.begin()
        owner.advance("enrolled")
        keys = {effect.key for effect in command.plan.effects}
        for name, prepared in sorted(plans.items()):
            key = ordinary_command.guest_key(name)
            if key in keys and not prepared.noop:
                journal.started(key)
            with repair_progress.step(Phase.CONFIGURE, repair_progress.member_index(owner, name)):
                ordinary_apply.stage_plan(admission.ssh, prepared, admission.local, approved=True)
        for name, prepared in sorted(plans.items()):
            with repair_progress.step(Phase.ACTIVATE, repair_progress.member_index(owner, name)):
                owner.remote(name, "grant", units=list(repair_guest.UNITS))
                receipt = ordinary_apply.execute_plan(
                    admission.ssh, prepared, admission.local, approved=True
                )
            key = ordinary_command.guest_key(name)
            if key in keys:
                journal.verified(key, receipt)
        authority = Activation(owner)
        pair_phases.reached("prepared", authority)
        owner.advance("prepared")
        pair_phases.reached("originating", authority)
        owner.advance("originating")
        if routes is not None:
            if routes.required:
                journal.started("routes")
            receipt = routes.finish(command.approval)
            if routes.required:
                journal.verified("routes", receipt)
        if publication is not None:
            publication.finish(routes)
        if command.plan.effects:
            journal.complete()
        owner.advance("verified")
