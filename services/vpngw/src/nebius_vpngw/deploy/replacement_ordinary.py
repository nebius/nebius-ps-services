"""Ordinary gateway replacement: prepare, fence, transfer, prove, retire.

Only public resource metadata is journalled. Authorization projections remain
in memory or on their authenticated source/candidate guest, never in the bundle.
"""

from __future__ import annotations

import os
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import operator_guest, ordinary_apply, replacement_guard, replacement_routes, security_groups
from .operator_access import OperatorEnrollment, OperatorTarget
from .replacement import Journal, Member, Plan, digest
from .replacement_cloud import Cloud, resource_id, single_nic_status, source_spec_digest
from .ssh_client_auth import resolve_ssh_client_auth
from .vm_diff import VMSpec
from .vm_ha_cloud import InstanceCloudState, instance_cloud_state, vm_ha_request_kwargs


def configuration_digest(instances: tuple) -> str:
    return digest(
        {item.hostname: ordinary_apply.sha(item.config_yaml.encode()) for item in instances}
    )


def groups(values: list[dict] | tuple[dict, ...]) -> tuple[security_groups.NICSecurityGroups, ...]:
    return tuple(
        security_groups.NICSecurityGroups(
            item["name"],
            item["subnet_id"],
            item["network_id"],
            tuple(item["explicit_ids"]),
            tuple(item["effective_ids"]),
        )
        for item in values
    )


def projection_digest(projection: dict, old_compute: str) -> str:
    return operator_guest.digest({**projection, "compute_id": old_compute})


class OrdinaryReplacement:
    def __init__(
        self,
        manager: Any,
        ssh: Any,
        deployment: Any,
        instances: tuple,
        local: dict,
        *,
        vm_ha: bool = False,
    ) -> None:
        if deployment.vm_ha is not None and not vm_ha:
            raise ValueError("Ordinary replacement cannot replace a VM-HA member")
        self.manager, self.ssh, self.deployment, self.local = manager, ssh, deployment, local
        self.instances = {item.hostname: item for item in instances}
        self.sdk = manager._get_client()
        if self.sdk is None or not manager.project_id or ssh._ssh_policy is None:
            raise RuntimeError("Replacement requires authoritative cloud and pinned SSH access")
        vm_spec = local["gateway_group"]["vm_spec"]
        key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        self.actor = resolve_ssh_client_auth(
            vm_spec.get("ssh_public_key"),
            explicit_private_key=Path(key).expanduser() if key else None,
        )
        self.username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        self.wheel = ssh._build_wheel(allow_installed_fallback=False)
        if self.wheel is None:
            raise RuntimeError("Replacement requires a pinned deployable agent artifact")
        self.snapshots: dict[str, dict] = {}

    def access(
        self, identifier: str, logical: str, *, cloud: Cloud | None = None
    ) -> tuple[OperatorEnrollment, OperatorTarget]:
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

        def current() -> Any:
            instance = (
                cloud.get_instance(identifier)
                if cloud
                else InstanceServiceClient(self.sdk)
                .get(GetInstanceRequest(id=identifier), **vm_ha_request_kwargs())
                .wait()
            )
            if (
                resource_id(instance) != identifier
                or instance.metadata.parent_id != self.manager.project_id
            ):
                raise RuntimeError("Replacement SSH target identity changed")
            if instance_cloud_state(instance) is not InstanceCloudState.RUNNING:
                raise RuntimeError("Replacement SSH target is not running")
            return instance

        original = current()
        address = self.manager._vm_public_ip_from_object(original)
        if not address:
            raise RuntimeError("Replacement SSH target has no authoritative public address")

        def assert_current() -> None:
            if self.manager._vm_public_ip_from_object(current()) != address:
                raise RuntimeError("Replacement SSH transport changed")

        target = OperatorTarget(logical, address, identifier, self.username, assert_current)
        return OperatorEnrollment((target,), self.actor, self.ssh._ssh_policy), target

    @staticmethod
    def request(
        access: OperatorEnrollment, target: OperatorTarget, action: str, **values: Any
    ) -> dict:
        client = access.connect(target)
        try:
            context = access._read_json(
                client,
                "/usr/bin/python3 -c "
                + shlex.quote(
                    "import json,os;print(json.dumps({'connection':os.environ['SSH_CONNECTION']}))"
                ),
            )
            return access.request(client, target, action, context["connection"], **values)
        finally:
            client.close()

    def inspect(self) -> Plan:
        desired = asdict(VMSpec.from_config(self.deployment.gateway_group.vm_spec))
        image = self.manager._resolve_boot_image_id(
            self.sdk,
            self.deployment.gateway_group,
            SimpleNamespace(boot_image=desired["disk_boot_image"]),
        )
        if not image:
            raise RuntimeError("Replacement requires an exact resolved source image ID")
        members = []
        for logical, configuration in self.instances.items():
            instance = self.manager._get_vm_by_name(self.sdk, logical)
            if instance is None:
                raise RuntimeError(
                    "Infrastructure replacement requires every existing gateway member"
                )
            if instance_cloud_state(instance) is not InstanceCloudState.RUNNING:
                raise RuntimeError("Replacement requires authenticated running source gateways")
            disk_id = self.manager._instance_boot_disk_id(instance)
            disk = self.manager._get_disk_by_id(self.sdk, disk_id)
            if disk is None:
                raise RuntimeError("Replacement source boot disk is unavailable")
            access, target = self.access(resource_id(instance), logical)
            snapshot = access.inspect(target)
            if snapshot.get("pending"):
                raise RuntimeError("Complete pending operator enrollment before VM replacement")
            self.snapshots[logical] = snapshot
            prepared = ordinary_apply.inspect_plan(
                self.ssh,
                target.address,
                configuration,
                self.local,
                target_identity=target.compute_id,
            )
            try:
                policy = ordinary_apply.sha(configuration.config_yaml.encode())
                if prepared.observation["config_sha256"] != policy:
                    raise RuntimeError(
                        "Apply VPN-policy changes separately before replacing infrastructure"
                    )
                if not prepared.observation["verified"] or prepared.observation.get("operation"):
                    raise RuntimeError("Replacement source must have a verified idle deployment")
            finally:
                prepared.close()
            nic = single_nic_status(instance)
            if not nic.public_ip_address.allocation_id:
                raise RuntimeError(
                    "Replacement requires an exact single NIC and retained public allocation"
                )
            self.ssh._ssh_policy.identity_for(
                logical
            )  # Private pinned host identity before effects.
            members.append(
                Member(
                    logical,
                    target.compute_id,
                    instance.metadata.name,
                    instance.metadata.resource_version,
                    disk_id,
                    (
                        disk.metadata.labels.get("nebius-vpngw-member") == logical
                        and disk.metadata.labels.get("nebius-vpngw-resource-role")
                        in {"boot", "candidate-boot"}
                    ),
                    source_spec_digest(instance),
                    snapshot["digest"],
                    policy,
                    tuple(asdict(item) for item in security_groups.capture(self.sdk, instance)),
                    (nic.public_ip_address.allocation_id,),
                    (nic.ip_address.allocation_id,),
                    "",
                    "",
                )
            )
        rosters = [item["projection"]["operators"] for item in self.snapshots.values()]
        if any(value != rosters[0] for value in rosters[1:]):
            raise RuntimeError("Managed operator rosters differ across gateway members")
        routes = replacement_routes.inventory(
            self.sdk,
            self.manager.project_id,
            {identifier for member in members for identifier in member.primary_allocations},
        )
        configuration = configuration_digest(tuple(self.instances.values()))
        artifact = ordinary_apply.sha(self.wheel.read_bytes())
        operation = digest(
            [
                self.manager.project_id,
                desired,
                image,
                configuration,
                artifact,
                [asdict(member) for member in members],
                routes,
            ]
        )
        from dataclasses import replace

        members = [
            replace(
                member,
                candidate_name=member.logical_name[:32] + "-r-" + operation[:16],
                candidate_disk_name=member.logical_name[:27] + "-r-" + operation[:16] + "-boot",
            )
            for member in members
        ]
        return Plan(
            self.manager.project_id,
            self.deployment.gateway_group.name,
            operation,
            image,
            desired,
            configuration,
            artifact,
            tuple(members),
            routes,
        )

    def settle(self, journal: Journal) -> None:
        Cloud(self.sdk, journal).settle()

    @staticmethod
    def member(journal: Journal, index: int | None = None) -> dict:
        transaction = journal.state["transaction"]
        return transaction["plan"]["members"][transaction["member"] if index is None else index]

    @staticmethod
    def result(journal: Journal, step: str, index: int | None = None) -> dict:
        transaction = journal.state["transaction"]
        return transaction["results"].get(
            f"{transaction['member'] if index is None else index}:{step}", {}
        )

    def verify_authority(self, journal: Journal, *, step: str) -> None:
        plan = journal.state["transaction"]["plan"]
        if (
            plan["configuration_sha256"] != configuration_digest(tuple(self.instances.values()))
            or plan["artifact_sha256"] != ordinary_apply.sha(self.wheel.read_bytes())
            or plan["desired"] != asdict(VMSpec.from_config(self.deployment.gateway_group.vm_spec))
        ):
            raise RuntimeError(
                "Replacement recovery requires the exact approved configuration and artifact"
            )
        member = self.member(journal)
        cloud = Cloud(self.sdk, journal)
        if step in {"prepare", "verify-prepared", "stop-old"}:
            old = cloud.get_instance(member["compute_id"])
            if source_spec_digest(old) != member["compute_sha256"]:
                raise RuntimeError("Source Compute configuration changed before cutover")
            security_groups.require_unchanged(self.sdk, old, groups(member["security_groups"]))
            nic = single_nic_status(old)
            if (nic.public_ip_address.allocation_id,) != tuple(member["public_allocations"]) or (
                nic.ip_address.allocation_id,
            ) != tuple(member["primary_allocations"]):
                raise RuntimeError("Source allocation binding changed before cutover")
            # A settled stop receipt can resume after the source stopped; no SSH
            # observation may be fabricated for the stopped guest.
            if step != "stop-old" or cloud.completed("stop-old") is None:
                access, target = self.access(
                    member["compute_id"], member["logical_name"], cloud=cloud
                )
                try:
                    snapshot = access.inspect(target, operation=plan["operation_id"])
                except RuntimeError:
                    snapshot = access.inspect(target)
                if snapshot["digest"] != member["authorization_sha256"]:
                    raise RuntimeError("Source operator authorization changed before cutover")
                client = access.connect(target)
                try:
                    configured = access._read_json(
                        client,
                        "sudo -n /usr/bin/python3 -c "
                        + shlex.quote(
                            "import hashlib,json;from pathlib import Path;print(json.dumps({'sha256':hashlib.sha256(Path('/etc/nebius-vpngw/config-resolved.yaml').read_bytes()).hexdigest()}))"
                        ),
                    )
                finally:
                    client.close()
                if configured != {"sha256": member["configured_policy_sha256"]}:
                    raise RuntimeError("Source VPN policy changed before replacement cutover")
                self.snapshots[member["logical_name"]] = snapshot
        elif step == "retire" and cloud.get_instance(member["compute_id"], absent_ok=True) is None:
            if cloud.completed("retire-compute") is None:
                raise RuntimeError("Source disappeared without the approved retirement receipt")
        elif step != "verify-retired":
            cloud.require_stopped(member["compute_id"])

    def _candidate(
        self, journal: Journal, index: int | None = None
    ) -> tuple[Cloud, dict, OperatorEnrollment, OperatorTarget]:
        cloud = Cloud(self.sdk, journal, member_index=index)
        member = self.member(journal, index)
        identifier = self.result(journal, "prepare", index).get("compute_id") or cloud.completed(
            "create-candidate"
        )
        if not identifier:
            raise RuntimeError("Replacement candidate has no persisted Compute receipt")
        access, target = self.access(identifier, member["logical_name"], cloud=cloud)
        candidate = cloud.get_instance(identifier)
        plan = journal.state["transaction"]["plan"]
        desired = plan["desired"]
        disk_id = self.result(journal, "prepare", index)["disk_id"]
        disk = self.manager._get_disk_by_id(self.sdk, disk_id)
        if (
            candidate.metadata.labels.get("nebius-vpngw-replacement") != plan["operation_id"]
            or candidate.metadata.labels.get("nebius-vpngw-member") != member["logical_name"]
            or candidate.metadata.labels.get("nebius-vpngw-resource-role")
            not in {"candidate", "committed", "active"}
            or candidate.spec.boot_disk.existing_disk.id != disk_id
            or candidate.spec.resources.platform != desired["platform"]
            or candidate.spec.resources.preset != desired["preset"]
            or disk is None
            or disk.status.source_image_id != plan["image_id"]
            or disk.status.size_bytes != desired["disk_gb"] * (1 << 30)
            or disk.status.block_size_bytes != desired["disk_block_bytes"]
            or str(getattr(disk.spec.type, "name", disk.spec.type)) != desired["disk_type"]
        ):
            raise RuntimeError(
                "Replacement candidate infrastructure differs from the approved plan"
            )
        security_groups.verify_candidate(self.sdk, candidate, groups(member["security_groups"]))
        return cloud, member, access, target

    def _verify_access(
        self, journal: Journal, index: int | None = None
    ) -> tuple[OperatorEnrollment, OperatorTarget, dict]:
        _cloud, member, access, target = self._candidate(journal, index)
        snapshot = access.inspect(
            target, operation=journal.state["transaction"]["plan"]["operation_id"]
        )
        if (
            projection_digest(snapshot["projection"], member["compute_id"])
            != member["authorization_sha256"]
        ):
            raise RuntimeError("Candidate operator authorization differs from the captured source")
        return access, target, snapshot

    def _prepared(self, journal: Journal) -> dict:
        access, target, snapshot = self._verify_access(journal)
        configuration = self.instances[target.hostname]
        prepared = ordinary_apply.inspect_plan(
            self.ssh, target.address, configuration, self.local, target_identity=target.compute_id
        )
        try:
            receipt = ordinary_apply.execute_plan(
                self.ssh,
                prepared,
                self.local,
                approved=True,
                prepare_replacement=journal.state["transaction"]["plan"]["operation_id"],
            )
        finally:
            prepared.close()
        if receipt["receipt"]["boot_id"] != snapshot["context"]["boot"]:
            raise RuntimeError("Candidate rebooted during preparation")
        return {"compute_id": target.compute_id, **receipt["receipt"]}

    def execute(self, journal: Journal, *, step: str, effect_id: str) -> dict:
        del effect_id  # Cloud effects use the same journal-derived stable identities.
        cloud = Cloud(self.sdk, journal)
        member = self.member(journal)
        plan = journal.state["transaction"]["plan"]
        logical, operation = member["logical_name"], plan["operation_id"]
        if step == "prepare":
            snapshot = self.snapshots[logical]
            access, target = self.access(member["compute_id"], logical, cloud=cloud)
            cloud.rename(
                member["compute_id"], logical, logical=logical, role="source", label="mark-source"
            )
            cloud.pin_public(member["compute_id"], tuple(member["public_allocations"]))
            self.request(
                access,
                target,
                "reserve-replacement",
                operation=operation,
                expected_digest=member["authorization_sha256"],
            )
            disk = cloud.create_disk(
                name=member["candidate_disk_name"],
                image_id=plan["image_id"],
                desired=plan["desired"],
                logical=logical,
            )
            allocation = cloud.create_management_allocation(
                name=member["candidate_name"] + "-mgmt",
                subnet=member["security_groups"][0]["subnet_id"],
                logical=logical,
            )
            index = self.instances[logical].instance_index
            cloud_init = self.candidate_cloud_init(
                index=index,
                logical=logical,
                operation=operation,
                authorization=snapshot["projection"],
            )
            candidate = cloud.create_candidate(
                name=member["candidate_name"],
                logical=logical,
                disk_id=disk,
                public_allocation=allocation,
                desired=plan["desired"],
                security_groups=member["security_groups"],
                cloud_init=cloud_init,
            )
            # Record identities before any SSH/bootstrap wait, including failures.
            journal.state["transaction"]["results"][
                f"{journal.state['transaction']['member']}:prepare"
            ] = {"compute_id": candidate, "disk_id": disk, "temporary_allocation_id": allocation}
            journal.save()
            deadline = time.monotonic() + 900
            while True:
                instance = cloud.get_instance(candidate)
                address = self.manager._vm_public_ip_from_object(instance)
                if address and self.manager.wait_for_vm_network(
                    logical, address, timeout=15, username=self.username
                ):
                    health = self.manager.check_vm_health(logical, address, username=self.username)
                    if (
                        health.get("cloud_init_complete")
                        and health.get("strongswan_installed")
                        and health.get("frr_installed")
                        and health.get("esp4_ready")
                        and not health.get("esp4_reboot_pending")
                    ):
                        break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Candidate bootstrap remains incomplete; source gateway was retained"
                    )
                time.sleep(5)
            result = self._prepared(journal)
            return {**self.result(journal, "prepare"), **result}
        if step == "verify-prepared":
            return self._prepared(journal)
        if step == "stop-old":
            # Fresh passive preparation immediately before the irreversible fence.
            self._prepared(journal)
            cloud.stop(member["compute_id"], "stop-old")
            return {"compute_id": member["compute_id"], "state": "stopped"}
        if step == "verify-stopped":
            cloud.require_stopped(member["compute_id"])
            return {"state": "stopped"}
        candidate = self.result(journal, "prepare")["compute_id"]
        if step == "transfer":
            cloud.detach_public(
                member["compute_id"], tuple(member["public_allocations"]), "detach-public"
            )
            cloud.attach_public(
                candidate, tuple(member["public_allocations"]), label="attach-public"
            )
            self._verify_allocations(journal)
            return {"compute_id": candidate, "public_allocations": member["public_allocations"]}
        if step == "commit-binding":
            cloud.rename(
                member["compute_id"],
                logical[:32] + "-retired-" + operation[:16],
                logical=logical,
                role="retired",
                label="rename-source",
            )
            cloud.rename(
                candidate, logical, logical=logical, role="committed", label="rename-candidate"
            )
            journal.state["bindings"][logical] = {
                "compute_id": candidate,
                "disk_id": self.result(journal, "prepare")["disk_id"],
                "operation_id": operation,
            }
            journal.save()
            return journal.state["bindings"][logical]
        if step == "activate":
            self._verify_allocations(journal)
            access, target, snapshot = self._verify_access(journal)
            self.request(
                access,
                target,
                "activate-replacement",
                operation=operation,
                expected_digest=snapshot["digest"],
            )
            prepared = ordinary_apply.inspect_plan(
                self.ssh,
                target.address,
                self.instances[logical],
                self.local,
                target_identity=candidate,
            )
            try:
                ordinary_apply.execute_plan(self.ssh, prepared, self.local, approved=True)
            finally:
                prepared.close()
            instance = cloud.get_instance(candidate)
            new_primary = str(single_nic_status(instance).ip_address.allocation_id)
            if not new_primary:
                raise RuntimeError("Candidate has no authoritative primary allocation")
            replacement_routes.rebind(
                cloud, tuple(plan["routes"]), member["primary_allocations"][0], new_primary
            )
            return {"compute_id": candidate, "primary_allocation_id": new_primary}
        if step == "verify-serving":
            return self._serving(journal)
        if step == "retire":
            cloud.retire(
                member["compute_id"],
                member["disk_id"],
                owned_disk=member["owned_disk"],
                candidate_verified=lambda: self._serving(journal),
            )
            return {
                "retired_compute_id": member["compute_id"],
                "disk_deleted": member["owned_disk"],
            }
        if step == "verify-retired":
            if cloud.get_instance(member["compute_id"], absent_ok=True) is not None:
                raise RuntimeError("Retired gateway Compute is still present")
            self._serving(journal)
            self._cleanup(journal)
            return {"verified": True}
        raise ValueError("Unknown replacement phase")

    def candidate_cloud_init(
        self, *, index: int, logical: str, operation: str, authorization: dict
    ) -> str:
        cloud_init = self.manager._build_cloud_init(
            local_prefixes=self.deployment.gateway.get("local_prefixes"),
            ingress=self.deployment.gateway_group.ingress_policies[index],
        )
        cloud_init = self.manager._render_gateway_ssh_enrollment_cloud_init(
            cloud_init, self.ssh._ssh_policy.identity_for(logical)
        )
        return replacement_guard.render(
            cloud_init, operation=operation, authorization=authorization
        )

    def _verify_allocations(self, journal: Journal, index: int | None = None) -> None:
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        cloud = Cloud(self.sdk, journal, member_index=index)
        member = self.member(journal, index)
        candidate = self.result(journal, "prepare", index)["compute_id"]
        if cloud.get_instance(member["compute_id"], absent_ok=True) is not None:
            cloud.require_stopped(member["compute_id"])
        elif cloud.completed("retire-compute") is None:
            raise RuntimeError("Former Compute disappeared without its retirement receipt")
        for identifier in member["public_allocations"]:
            allocation = (
                AllocationServiceClient(self.sdk)
                .get(GetAllocationRequest(id=identifier), **vm_ha_request_kwargs())
                .wait()
            )
            assignment = allocation.status.assignment.network_interface
            if (
                resource_id(allocation) != identifier
                or not allocation.status.static
                or getattr(allocation.status.state, "name", "") != "ASSIGNED"
                or assignment.instance_id != candidate
                or assignment.name != member["security_groups"][0]["name"]
            ):
                raise RuntimeError(
                    "Retained public allocation ownership is not confirmed on candidate"
                )

    def _serving(self, journal: Journal, index: int | None = None) -> dict:
        self._verify_allocations(journal, index)
        _access, target, snapshot = self._verify_access(journal, index)
        prepared = ordinary_apply.inspect_plan(
            self.ssh,
            target.address,
            self.instances[target.hostname],
            self.local,
            target_identity=target.compute_id,
        )
        try:
            if not prepared.noop or not prepared.observation["verified"]:
                raise RuntimeError(
                    "Replacement service proof remains incomplete; old resources are retained"
                )
            receipt = ordinary_apply.execute_plan(self.ssh, prepared, self.local, approved=False)
        finally:
            prepared.close()
        if receipt["receipt"]["boot_id"] != snapshot["context"]["boot"]:
            raise RuntimeError("Candidate rebooted during final service verification")
        member = self.member(journal, index)
        cloud = Cloud(self.sdk, journal, member_index=index)
        primary = str(
            single_nic_status(cloud.get_instance(target.compute_id)).ip_address.allocation_id
        )
        replacement_routes.rebind(
            cloud,
            tuple(journal.state["transaction"]["plan"]["routes"]),
            member["primary_allocations"][0],
            primary,
            verify_only=True,
        )
        return {
            "compute_id": target.compute_id,
            "boot_id": snapshot["context"]["boot"],
            "configuration_sha256": receipt["receipt"]["config_sha256"],
        }

    def _cleanup(self, journal: Journal) -> None:
        from nebius.api.nebius.vpc.v1 import (
            AllocationServiceClient,
            DeleteAllocationRequest,
            GetAllocationRequest,
        )

        from .vm_ha_cloud import nebius_request_error_code_is

        cloud = Cloud(self.sdk, journal)
        identifier = self.result(journal, "prepare")["temporary_allocation_id"]
        service = AllocationServiceClient(self.sdk)
        try:
            allocation = service.get(
                GetAllocationRequest(id=identifier), **vm_ha_request_kwargs()
            ).wait()
        except Exception as error:
            if not nebius_request_error_code_is(error, "NOT_FOUND"):
                raise
        else:
            if dict(allocation.metadata.labels) != cloud.labels(
                self.member(journal)["logical_name"], "temporary-management"
            ):
                raise RuntimeError("Temporary allocation ownership changed; cleanup is pending")
            if allocation.status.assignment.network_interface.instance_id:
                raise RuntimeError("Temporary management allocation remains attached")
            cloud.mutate(
                "delete-temporary-allocation",
                DeleteAllocationRequest(id=identifier),
                service.delete,
            )
        try:
            service.get(GetAllocationRequest(id=identifier), **vm_ha_request_kwargs()).wait()
        except Exception as error:
            if not nebius_request_error_code_is(error, "NOT_FOUND"):
                raise
        else:
            raise RuntimeError("Temporary allocation deletion is not confirmed")

    def finish(self, journal: Journal) -> None:
        members = journal.state["transaction"]["plan"]["members"]
        for index in range(len(members)):
            self._serving(journal, index)
        for index in range(len(members)):
            access, target, _snapshot = self._verify_access(journal, index)
            self.request(
                access,
                target,
                "release-replacement",
                operation=journal.state["transaction"]["plan"]["operation_id"],
            )
            Cloud(self.sdk, journal, member_index=index).rename(
                target.compute_id,
                target.hostname,
                logical=target.hostname,
                role="active",
                label="complete-candidate",
            )

    def verify(self, journal: Journal, *, step: str, result: dict) -> None:
        if not result:
            raise RuntimeError("Replacement phase returned no verified result")

    def promote(self, journal: Journal, *, effect_id: str) -> None:
        raise RuntimeError("Ordinary replacement has no HA promotion authority")
