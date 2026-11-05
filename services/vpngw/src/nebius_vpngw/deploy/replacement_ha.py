"""Serial HA infrastructure replacement with explicit membership commits."""

from __future__ import annotations

import copy
import ipaddress
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from .. import cli
from ..config_loader import (
    VMHADigestRecord,
    VMHAGenerationRecord,
    VMHALogicalManifests,
    VMHAReadinessRecord,
)
from ..schema import VMHARuntimeBinding
from ..vm_ha_credentials import installed_vm_ha_credential_path
from . import replacement_routes, security_groups
from . import vm_ha_prepared_replacement as lifecycle
from .replacement import Journal, Member, Plan, digest
from .replacement_cloud import Cloud, message_digest, resource_id, single_nic_status
from .replacement_ha_runtime import Runtime
from .replacement_ordinary import OrdinaryReplacement, configuration_digest, groups
from .vm_diff import VMSpec
from .vm_ha_cloud import (
    AllocationOwner,
    InstanceCloudState,
    allocation_observation,
    clone_nebius_sdk_message,
    instance_cloud_state,
    network_interface_alias_allocation_ids,
    vm_ha_request_kwargs,
)
from .vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
)


def policy(value: dict) -> dict:
    """Compare VPN intent, excluding only generation and provider identity."""
    result = copy.deepcopy(value)
    for name in ("generation", "readiness", "runtime_binding"):
        result.get("vm_ha", {}).pop(name, None)
    return result


def spec_digest(instance: Any, shared: str) -> str:
    spec = clone_nebius_sdk_message(instance.spec)
    interfaces = list(spec.network_interfaces)
    for nic in interfaces:
        aliases = network_interface_alias_allocation_ids(nic, description="replacement source")
        if set(aliases) - {shared}:
            raise RuntimeError("HA replacement source has an unrelated secondary allocation")
        nic.aliases = []
        nic.public_ip_address = None
    spec.network_interfaces = interfaces
    return message_digest(spec)


def installed_config(template: Any, value: dict) -> Any:
    generation = value["vm_ha"]["generation"]
    digests = VMHADigestRecord(**generation["digests"])
    return replace(
        template,
        config_yaml=yaml.safe_dump(value, sort_keys=False),
        vm_ha_generation=VMHAGenerationRecord(
            generation["generation_id"],
            digests,
            VMHALogicalManifests(**generation["logical_manifests"]),
        ),
        vm_ha_readiness=VMHAReadinessRecord(
            tuple(value["vm_ha"]["readiness"]["required_node_ids"]),
            generation["generation_id"],
            digests,
        ),
    )


class HAReplacement(OrdinaryReplacement):
    def __init__(
        self,
        manager: Any,
        ssh: Any,
        deployment: Any,
        instances: tuple,
        local: dict,
        *,
        config_path: Path,
    ) -> None:
        if deployment.vm_ha is None:
            raise ValueError("HA replacement requires an explicit HA deployment")
        super().__init__(manager, ssh, deployment, instances, local, vm_ha=True)
        self.config_path = config_path
        self.store = VMHALifecycleStore(config_path)
        self.state: VMHALifecycleState | None = None

    def _resource_member(self, original: VMHALifecycleMember, instance: Any) -> VMHALifecycleMember:
        nic = single_nic_status(instance)
        specification = instance.spec.network_interfaces[0]
        return replace(
            original,
            compute_id=resource_id(instance),
            compute_revision=str(instance.metadata.resource_version),
            disk_id=self.manager._instance_boot_disk_id(instance),
            network_interface_name=nic.name,
            network_interface_subnet_id=specification.subnet_id,
            primary_allocation_id=nic.ip_address.allocation_id,
            public_allocation_id=nic.public_ip_address.allocation_id,
            public_ip=self.manager._vm_public_ip_from_object(instance),
            alias_allocation_ids=tuple(
                sorted(
                    network_interface_alias_allocation_ids(
                        specification, description="replacement member"
                    )
                )
            ),
        )

    def owner(self, members: tuple) -> Any:
        assert self.state is not None
        owner = lifecycle.owner(members, self.state.allocation_id)
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        allocation = (
            AllocationServiceClient(self.sdk)
            .get(GetAllocationRequest(id=self.state.allocation_id), **vm_ha_request_kwargs())
            .wait()
        )
        if allocation_observation(self.state.allocation_id, allocation).owner != AllocationOwner(
            owner.compute_id, owner.network_interface_name
        ):
            raise RuntimeError("Shared allocation ownership is not exact on the HA member")
        return owner

    def inspect(self) -> Plan:
        snapshot = self.store.read_hardened(
            expected_project_id=self.manager.project_id,
            expected_gateway_name=self.deployment.gateway_group.name,
        )
        if snapshot is None or snapshot.state.status is not VMHALifecycleStatus.ACTIVE:
            raise RuntimeError("HA replacement requires a completed ACTIVE lifecycle")
        self.state = snapshot.state
        if (
            self.state.transaction is None
            or self.state.transaction.pending_effect
            or self.state.transaction.accepted_cloud_operation_id
        ):
            raise RuntimeError("Settle the current HA lifecycle before infrastructure replacement")
        desired = asdict(VMSpec.from_config(self.deployment.gateway_group.vm_spec))
        image = self.manager._resolve_boot_image_id(
            self.sdk,
            self.deployment.gateway_group,
            SimpleNamespace(boot_image=desired["disk_boot_image"]),
        )
        if not image:
            raise RuntimeError("Replacement requires a frozen source image ID")
        current = []
        members = []
        bindings = []
        # Runtime only requires a journal for mutation paths; these reads have no effects.
        reader = Runtime.__new__(Runtime)
        reader.ports = self
        for old in self.state.members:
            instance = self.manager._get_vm_by_name(self.sdk, old.instance_name)
            if (
                instance is None
                or resource_id(instance) != old.compute_id
                or instance_cloud_state(instance) is not InstanceCloudState.RUNNING
            ):
                raise RuntimeError("HA replacement requires both exact retained members running")
            observed = self._resource_member(old, instance)
            if lifecycle.identity(observed) != lifecycle.identity(old):
                raise RuntimeError(
                    "HA replacement source differs from lifecycle resource authority"
                )
            current.append(observed)
            access, target = self.access(old.compute_id, old.instance_name)
            keys = access.inspect(target)
            if keys.get("pending"):
                raise RuntimeError("Complete pending operator grants before HA replacement")
            self.snapshots[old.instance_name] = keys
            config = reader.config(old.compute_id, old.instance_name)
            if policy(config["config"]) != policy(
                yaml.safe_load(self.instances[old.instance_name].config_yaml)
            ):
                raise RuntimeError(
                    "Apply VPN or topology changes separately before replacing HA infrastructure"
                )
            source = installed_config(self.instances[old.instance_name], config["config"])
            binding = VMHARuntimeBinding.model_validate(
                config["config"]["vm_ha"]["runtime_binding"]
            )
            status = reader.command(
                old.compute_id,
                old.instance_name,
                "sudo -n /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-status",
            )
            cli._validate_vm_ha_planned_status(status, inst_cfg=source, runtime_binding=binding)
            if (
                status.get("pending_operation_id") is not None
                or status.get("transfer_inhibition_operation_id") is not None
                or status.get("heartbeat", {}).get("peer_v5_fresh") is not True
                or not (
                    status.get("promotion_ready") is True or status.get("standby_ready") is True
                )
            ):
                raise RuntimeError(
                    "HA replacement requires a healthy quiescent pair with fresh peer evidence"
                )
            # Existing canonical planned promotion requires enabled restoration.
            policy_status = reader.command(
                old.compute_id,
                old.instance_name,
                "sudo -n /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-auto-healing-action status",
            )
            if (
                policy_status.get("desired") != "enabled"
                or policy_status.get("phase") != "committed"
                or policy_status.get("accepted_start") is not False
                or policy_status.get("recovery") is not None
            ):
                raise RuntimeError("HA replacement requires enabled, quiescent standby restoration")
            bindings.append(binding)
            disk = self.manager._get_disk_by_id(self.sdk, old.disk_id)
            if disk is None:
                raise RuntimeError("HA source boot disk is unavailable")
            self.ssh._ssh_policy.identity_for(old.instance_name)
            members.append(
                Member(
                    old.instance_name,
                    old.compute_id,
                    instance.metadata.name,
                    instance.metadata.resource_version,
                    old.disk_id,
                    disk.metadata.labels.get("nebius-vpngw-member") == old.instance_name
                    and disk.metadata.labels.get("nebius-vpngw-resource-role")
                    in {"boot", "candidate-boot"},
                    spec_digest(instance, self.state.allocation_id),
                    keys["digest"],
                    config["sha256"],
                    tuple(asdict(item) for item in security_groups.capture(self.sdk, instance)),
                    (observed.public_allocation_id,),
                    (observed.primary_allocation_id,),
                    "",
                    "",
                )
            )
        if bindings[0] != bindings[1]:
            raise RuntimeError("HA source runtime bindings differ")
        binding = bindings[0]
        if (
            binding.shared_allocation_id != self.state.allocation_id
            or binding.cluster_id != self.state.cluster_id
            or binding.route_runtime_id != self.state.route_runtime_id
            or not binding.nebius_service_account_id
            or {node.compute_id for node in binding.nodes}
            != {member.compute_id for member in current}
        ):
            raise RuntimeError("HA source runtime authority differs from lifecycle")
        rosters = [value["projection"]["operators"] for value in self.snapshots.values()]
        if rosters[0] != rosters[1]:
            raise RuntimeError("HA managed operator rosters differ")
        owner = self.owner(tuple(current))
        members.sort(key=lambda member: member.logical_name == owner.instance_name)
        routes = replacement_routes.inventory(
            self.sdk,
            self.manager.project_id,
            {identifier for member in members for identifier in member.primary_allocations},
        )
        ha = {
            "lifecycle": self.state.to_dict(),
            "runtime_binding": binding.model_dump(mode="json"),
            "current_members": [member.to_dict() for member in current],
        }
        configuration = configuration_digest(tuple(self.instances.values()))
        artifact = hashlib_sha(self.wheel.read_bytes())
        operation = digest(
            [
                self.manager.project_id,
                desired,
                image,
                configuration,
                artifact,
                [asdict(member) for member in members],
                routes,
                ha,
            ]
        )
        members = [
            replace(
                member,
                candidate_name=member.logical_name[:32] + "-r-" + operation[:16],
                candidate_disk_name=member.logical_name[:27] + "-r-" + operation[:16] + "-boot",
            )
            for member in members
        ]
        snapshot.assert_current()
        plan = Plan(
            self.manager.project_id,
            self.deployment.gateway_group.name,
            operation,
            image,
            desired,
            configuration,
            artifact,
            tuple(members),
            routes,
            True,
            owner.instance_name,
            ha,
        )
        self.validate_candidate_cloud_init(plan)
        return plan

    def _lifecycle(self, journal: Journal) -> None:
        tx = journal.state["transaction"]
        base = VMHALifecycleState.from_dict(tx["plan"]["ha"]["lifecycle"])
        if "ha-lifecycle" not in tx["results"]:
            captured = tuple(
                VMHALifecycleMember.from_dict(item) for item in tx["plan"]["ha"]["current_members"]
            )
            if len(captured) != 2:
                raise RuntimeError("HA replacement requires two captured source members")
            initial = lifecycle.start(
                base,
                tx["plan"],
                current_members=(captured[0], captured[1]),
                desired_digest=tx["plan"]["configuration_sha256"],
            )
            tx["results"]["ha-lifecycle"] = [initial.to_dict()]
            journal.save()
        chain = [VMHALifecycleState.from_dict(item) for item in tx["results"]["ha-lifecycle"]]
        previous = base
        for successor in chain:
            lifecycle.validate_successor(previous, successor)
            previous = successor
        snapshot = self.store.read_hardened(
            expected_project_id=base.project_id, expected_gateway_name=base.gateway_name
        )
        stored = snapshot.state if snapshot else None
        if stored is not None and stored != base and stored not in chain:
            raise RuntimeError("Local HA lifecycle differs from the replacement bundle")
        # A new operator imports the validated revision-1 approval, then replays
        # only explicit membership transitions. Generic lifecycle CAS is unchanged.
        for successor in chain:
            if stored == successor:
                continue
            if stored in chain and chain.index(stored) > chain.index(successor):
                continue
            self.store.write_verified(
                successor, predecessor_sha256=stored.record_sha256 if stored else None
            )
            stored = successor
        self.state = chain[-1]

    def transition(
        self, journal: Journal, *, revision: int, members: tuple, evidence: dict
    ) -> None:
        self._lifecycle(journal)
        assert self.state is not None and self.state.transaction is not None
        if self.state.transaction.revision >= revision:
            return
        successor = lifecycle.advance(self.state, members=members, evidence=evidence)
        assert successor.transaction is not None
        if successor.transaction.revision != revision:
            raise RuntimeError("HA replacement lifecycle phase changed")
        journal.state["transaction"]["results"]["ha-lifecycle"].append(successor.to_dict())
        journal.save()  # Write-ahead intent makes the two private stores replayable.
        self._lifecycle(journal)

    def members(self, journal: Journal, *, candidate: bool) -> tuple:
        assert self.state is not None
        tx = journal.state["transaction"]
        originals = {member.instance_name: member for member in self.state.members}
        result = []
        for index, item in enumerate(tx["plan"]["members"]):
            prepared = self.result(journal, "prepare", index)
            use_candidate = index < tx["member"] or candidate and index <= tx["member"]
            identifier = prepared["compute_id"] if use_candidate else item["compute_id"]
            instance = Cloud(self.sdk, journal, member_index=index).get_instance(identifier)
            result.append(self._resource_member(originals[item["logical_name"]], instance))
        return tuple(sorted(result, key=lambda item: item.instance_index))

    def runtime_binding(self, journal: Journal, *, candidate: bool) -> VMHARuntimeBinding:
        base = VMHARuntimeBinding.model_validate(
            journal.state["transaction"]["plan"]["ha"]["runtime_binding"]
        )
        generation = self.deployment.vm_ha.generation
        nodes = []
        # During passive preparation public addresses have not transferred yet;
        # only compute/NIC/private endpoint are used to build the future binding.
        for member in self.members(journal, candidate=candidate):
            original = next(node for node in base.nodes if node.node_id == member.node_id)
            if original.nebius_credentials_sha256 is None:
                raise RuntimeError("HA replacement runtime credential binding is incomplete")
            instance = Cloud(self.sdk, journal).get_instance(member.compute_id)
            address = str(ipaddress.ip_interface(single_nic_status(instance).ip_address.address).ip)
            nodes.append(
                original.model_copy(
                    update={
                        "compute_id": member.compute_id,
                        "network_interface_name": member.network_interface_name,
                        "peer_endpoint": f"{address}:9443",
                        "nebius_credentials_path": installed_vm_ha_credential_path(
                            node_id=member.node_id,
                            generation_id=generation.generation_id,
                            credential_sha256=original.nebius_credentials_sha256,
                        ),
                    }
                )
            )
        return VMHARuntimeBinding.model_validate(
            {
                **base.model_dump(mode="json"),
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "generation_id": generation.generation_id,
                "configuration_digest": generation.digests.configuration,
                "static_routes_digest": generation.digests.static_routes,
                "bgp_policy_digest": generation.digests.bgp_policy,
            }
        )

    def require_owner(self, journal: Journal, identifier: str) -> None:
        owner = self.owner(self.members(journal, candidate=True))
        if owner.compute_id != identifier:
            raise RuntimeError("HA shared owner changed during replacement")

    def settle(self, journal: Journal) -> None:
        super().settle(journal)
        self._lifecycle(journal)

    def verify_authority(self, journal: Journal, *, step: str) -> None:
        plan = journal.state["transaction"]["plan"]
        if (
            not plan["vm_ha"]
            or plan["configuration_sha256"] != configuration_digest(tuple(self.instances.values()))
            or plan["artifact_sha256"] != hashlib_sha(self.wheel.read_bytes())
            or plan["desired"] != asdict(VMSpec.from_config(self.deployment.gateway_group.vm_spec))
        ):
            raise RuntimeError(
                "HA replacement requires the exact approved configuration and artifact"
            )
        member = self.member(journal)
        cloud = Cloud(self.sdk, journal)
        assert self.state is not None
        if step in {"prepare", "verify-prepared", "stop-old"}:
            old = cloud.get_instance(member["compute_id"])
            if spec_digest(old, self.state.allocation_id) != member["compute_sha256"]:
                raise RuntimeError("HA source infrastructure changed before cutover")
            security_groups.require_unchanged(self.sdk, old, groups(member["security_groups"]))
            nic = single_nic_status(old)
            if (nic.public_ip_address.allocation_id,) != tuple(member["public_allocations"]) or (
                nic.ip_address.allocation_id,
            ) != tuple(member["primary_allocations"]):
                raise RuntimeError("HA source allocation changed before cutover")
            if step != "stop-old" or cloud.completed("stop-old") is None:
                access, target = self.access(member["compute_id"], member["logical_name"])
                try:
                    snapshot = access.inspect(target, operation=plan["operation_id"])
                except RuntimeError:
                    snapshot = access.inspect(target)
                if snapshot["digest"] != member["authorization_sha256"]:
                    raise RuntimeError("HA source operator authorization changed")
                configured = Runtime(self, journal).config(
                    member["compute_id"], member["logical_name"]
                )
                expected = member["configured_policy_sha256"]
                if journal.state["transaction"]["member"] == 1:
                    expected = journal.state["transaction"]["results"]["ha-runtime-0"]["stages"][
                        member["logical_name"]
                    ]["staged_file_sha256"]
                if configured["sha256"] != expected:
                    raise RuntimeError("HA source VPN policy changed before replacement")
                self.snapshots[member["logical_name"]] = snapshot
            current = self.members(journal, candidate=False)
            if self.owner(current).compute_id == member["compute_id"]:
                raise RuntimeError(
                    "HA infrastructure replacement may only stop the observed nonowner"
                )
        elif step == "retire" and cloud.get_instance(member["compute_id"], absent_ok=True) is None:
            if cloud.completed("retire-compute") is None:
                raise RuntimeError("Old HA Compute disappeared without its retirement receipt")
        elif step != "verify-retired":
            cloud.require_stopped(member["compute_id"])

    def candidate_cloud_init(self, **values: Any) -> str:
        from .vm_ha_identity import (
            PROVISIONING_MARKER_PREFIX,
            render_provisioning_marker,
            require_cloud_init_size,
        )

        rendered = super().candidate_cloud_init(**values)
        marker = render_provisioning_marker(self.deployment.gateway_group, values["index"])
        rendered = rendered.replace(
            "#cloud-config\n", "#cloud-config\n" + PROVISIONING_MARKER_PREFIX + marker + "\n", 1
        )
        require_cloud_init_size(rendered)
        return rendered

    def _prepared(self, journal: Journal) -> dict:
        self._verify_access(journal)
        return Runtime(self, journal).prepare()

    def execute(self, journal: Journal, *, step: str, effect_id: str) -> dict:
        member = self.member(journal)
        cloud = Cloud(self.sdk, journal)
        if step == "stop-old":
            if cloud.completed("stop-old") is None:
                self._prepared(journal)
                Runtime(self, journal).fence_sources()
            cloud.stop(member["compute_id"], "stop-old")
            return {"compute_id": member["compute_id"], "state": "stopped"}
        if step == "commit-binding":
            result = super().execute(journal, step=step, effect_id=effect_id)
            cloud.require_stopped(member["compute_id"])
            members = self.members(journal, candidate=True)
            candidate = next(
                item for item in members if item.instance_name == member["logical_name"]
            )
            owner = self.owner(members)
            self.transition(
                journal,
                revision=2 if journal.state["transaction"]["member"] == 0 else 4,
                members=members,
                evidence={
                    "kind": "membership",
                    "retired_compute_id": member["compute_id"],
                    "retired_state": "stopped",
                    "candidate_compute_id": candidate.compute_id,
                    "candidate_disk_id": candidate.disk_id,
                    "owner_compute_id": owner.compute_id,
                },
            )
            return result
        if step == "activate":
            self._verify_allocations(journal)
            result = Runtime(self, journal).activate()
            primary = single_nic_status(
                cloud.get_instance(result["compute_id"])
            ).ip_address.allocation_id
            replacement_routes.rebind(
                cloud,
                tuple(journal.state["transaction"]["plan"]["routes"]),
                member["primary_allocations"][0],
                primary,
            )
            return result
        return super().execute(journal, step=step, effect_id=effect_id)

    def _serving(self, journal: Journal, index: int | None = None) -> dict:
        self._verify_allocations(journal, index)
        _, target, snapshot = self._verify_access(journal, index)
        runtime = Runtime(self, journal)
        members = self.members(journal, candidate=True)
        owner = self.owner(members)
        binding = self.runtime_binding(journal, candidate=True)
        for member in members:
            runtime.wait(
                member,
                binding,
                lambda value, active=member == owner: bool(
                    value.get("data_plane_mode") == ("active" if active else "passive")
                    and value.get("observed_owner_node_id") == owner.node_id
                    and value.get("pending_operation_id") is None
                    and value.get("heartbeat", {}).get("peer_v5_fresh") is True
                    and (
                        cli._vm_ha_active_route_receipt_matches(
                            value, active_node_id=owner.node_id, runtime_binding=binding
                        )
                        if active
                        else value.get("standby_ready") is True
                    )
                ),
                locked=False,
            )
        member = self.member(journal, index)
        cloud = Cloud(self.sdk, journal, member_index=index)
        primary = single_nic_status(cloud.get_instance(target.compute_id)).ip_address.allocation_id
        replacement_routes.rebind(
            cloud,
            tuple(journal.state["transaction"]["plan"]["routes"]),
            member["primary_allocations"][0],
            primary,
            verify_only=True,
        )
        _, _, final_access = self._verify_access(journal, index)
        if final_access["context"]["boot"] != snapshot["context"]["boot"]:
            raise RuntimeError("HA candidate rebooted during final service verification")
        return {
            "compute_id": target.compute_id,
            "boot_id": snapshot["context"]["boot"],
            "generation_id": binding.generation_id,
        }

    def promote(self, journal: Journal, *, effect_id: str) -> None:
        from .replacement_ha_promotion import promote

        promote(self, journal, effect_id=effect_id)

    def finish(self, journal: Journal) -> None:
        super().finish(journal)
        members = self.members(journal, candidate=True)
        self.transition(
            journal,
            revision=5,
            members=members,
            evidence={
                "kind": "complete",
                "verified_member_ids": sorted(member.compute_id for member in members),
            },
        )


def hashlib_sha(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
