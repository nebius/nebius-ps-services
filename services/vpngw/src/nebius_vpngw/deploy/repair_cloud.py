"""Retained-resource cloud effects and operation settlement for explicit repair."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import repair_guest
from .. import repair_state as storage
from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .repair_cloud_retry import RepairCloud
from .replacement_cloud import resource_id
from .vm_ha_cloud import (
    InstanceCloudState,
    instance_cloud_state,
    vm_ha_request_kwargs,
    wait_vm_ha_operation,
)


class CloudJournal:
    """Small adapter to the existing provider receipt owner, outside old state."""

    def __init__(self, root: Path, *, operation: str, intent: dict):
        self.path = root / "cloud" / operation / "operations.json"
        self.project = intent["project"]
        self.gateway = intent.get("gateway", "")
        self.targets = {target["compute"] for target in intent.get("targets", ())}
        self.operation, self.intent = operation, storage.digest(intent)
        raw = storage.read(self.path)
        self.state: dict[str, Any] = (
            json.loads(raw)
            if raw is not None
            else {
                "schema": "nebius-vpngw.repair-cloud.v1",
                "operation": operation,
                "transaction": {"approval": self.intent, "member": 0, "operations": {}},
            }
        )
        self.validate()

    def validate(self) -> None:
        transaction = self.state.get("transaction")
        if (
            set(self.state) != {"schema", "operation", "transaction"}
            or self.state["schema"] != "nebius-vpngw.repair-cloud.v1"
            or self.state["operation"] != self.operation
            or not isinstance(transaction, dict)
            or set(transaction) != {"approval", "member", "operations"}
            or transaction["approval"] != self.intent
            or transaction["member"] != 0
            or not isinstance(transaction["operations"], dict)
        ):
            raise RuntimeError("Repair cloud receipt belongs to another confirmed operation")
        from .replacement_cloud import effect_key

        records = transaction["operations"]
        groups = {
            value.get("resource_id")
            for value in records.values()
            if value.get("request_type") == "CreateSecurityGroupRequest"
            and value.get("status") == "settled"
        }
        rules = {
            value.get("resource_id")
            for value in records.values()
            if value.get("request_type") == "CreateSecurityRuleRequest"
            and value.get("status") == "settled"
        }
        for key, value in records.items():
            if value.get("request_type") not in {
                "StopInstanceRequest",
                "StartInstanceRequest",
                "UpdateInstanceRequest",
                "CreateSecurityGroupRequest",
                "DeleteSecurityGroupRequest",
                "CreateSecurityRuleRequest",
                "DeleteSecurityRuleRequest",
            } or value.get("status") not in {"intent", "accepted", "settled", "rejected", "failed"}:
                raise RuntimeError("Repair cloud receipt exceeds retained-resource scope")
            if key != effect_key(self.intent, 0, value["label"], self.operation):
                raise RuntimeError("Repair cloud receipt changed its confirmed request identity")
            request = value.get("request_metadata", {})
            kind = value["request_type"]
            metadata = request.get("metadata", {})
            if kind in {"StartInstanceRequest", "StopInstanceRequest"}:
                allowed = request.get("id") in self.targets
            elif kind == "UpdateInstanceRequest":
                allowed = metadata.get("id") in self.targets and value.get(
                    "user_data_source", metadata.get("id")
                ) == metadata.get("id")
            elif kind == "CreateSecurityGroupRequest":
                allowed = metadata.get("parent_id") == self.project and metadata.get(
                    "name", ""
                ).startswith("vpngw-repair-" + self.operation[:16] + "-")
            elif kind == "CreateSecurityRuleRequest":
                allowed = metadata.get("parent_id") in groups and metadata.get(
                    "name", ""
                ).startswith("repair-")
            elif kind == "DeleteSecurityGroupRequest":
                allowed = request.get("id") in groups
            else:
                allowed = request.get("id") in rules
            if not allowed:
                raise RuntimeError(
                    "Repair cloud request points outside retained targets or owned quarantine"
                )

    def save(self) -> None:
        self.validate()
        storage.publish(self.path, self.state)


def retained_binding(instance: Any) -> dict:
    """Immutable infrastructure identity; excludes mutable HA alias ownership."""
    from .vm_manager import VMManager

    interfaces = list(instance.spec.network_interfaces)
    result: dict[str, Any] = {
        "compute": resource_id(instance),
        "project": str(instance.metadata.parent_id),
        "disk": VMManager._instance_boot_disk_id(instance),
        "nics": [
            {
                "name": str(nic.name),
                "subnet": str(nic.subnet_id),
                "private": str(nic.ip_address.allocation_id),
                "public": str(nic.public_ip_address.allocation_id),
            }
            for nic in sorted(interfaces, key=lambda item: item.name)
        ],
    }
    if (
        not result["compute"]
        or not result["disk"]
        or not result["project"]
        or not interfaces
        or any(not row["name"] or not row["subnet"] for row in result["nics"])
    ):
        raise RuntimeError("Repair requires complete existing VM, disk and NIC identities")
    return result


def settle_resource(service: Any, identifier: str) -> tuple[str, ...]:
    """Wait for every listed operation on an exact resource, including failures.

    This is not proof that a lost request never existed. The caller additionally
    excludes old writers, settles known accepted receipts, and proves both HA
    members Stopped before permitting ownership reconstruction.
    """
    from nebius.api.nebius.common.v1 import GetOperationRequest, ListOperationsRequest

    endpoint = service.operation_service()
    inventory = collect_nebius_pages(
        lambda token: endpoint.list(
            ListOperationsRequest(resource_id=identifier, page_size=500, page_token=token),
            **vm_ha_request_kwargs(),
        ),
        context="Repair resource operations",
        items_field="operations",
        item_identity=nebius_resource_id,
    )
    completed = []
    for item in inventory:
        operation_id = nebius_resource_id(item)
        if not operation_id or str(getattr(item, "resource_id", "")) != identifier:
            raise RuntimeError("Provider operation inventory has ambiguous resource identity")
        operation = endpoint.get(
            GetOperationRequest(id=operation_id), **vm_ha_request_kwargs()
        ).wait()
        if str(operation.id) != operation_id:
            raise RuntimeError("Provider operation changed identity during repair settlement")
        wait_vm_ha_operation(operation)
        if not operation.done():
            raise RuntimeError("Old provider operation remains unsettled")
        completed.append(operation_id)
    return tuple(sorted(completed))


class RetainedCloud:
    def __init__(self, sdk: Any, journal: CloudJournal, bindings: dict[str, dict]):
        self.sdk, self.journal, self.bindings = sdk, journal, bindings
        self.owner = RepairCloud(sdk, journal, member_index=0, operation_scope=journal.operation)

    def require(self, name: str) -> Any:
        expected = self.bindings[name]
        current = self.owner.get_instance(expected["compute"])
        if retained_binding(current) != expected:
            raise RuntimeError("Retained gateway identity changed during repair")
        return current

    def transition_label(self, prefix: str, name: str) -> str:
        """Use a fresh effect for each observed power transition.

        Start/Stop need not change the Compute spec revision. Previously
        settled operations cannot stand in for a later stop of the same VM.
        Callers settle uncertain effects before rereading current power state.
        """
        prefix += "-" + name + "-"
        prior = [
            record for record in self.owner.records.values() if record["label"].startswith(prefix)
        ]
        if any(record["status"] in {"intent", "accepted"} for record in prior):
            raise RuntimeError("Prior repair power transition is still settling")
        return prefix + str(len(prior) + 1)

    def settle(self) -> dict[str, list[str]]:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient

        self.owner.settle()
        service = InstanceServiceClient(self.sdk)
        return {
            name: list(settle_resource(service, self.require(name).metadata.id))
            for name in sorted(self.bindings)
        }

    def stop_all(self) -> None:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, StopInstanceRequest

        service = InstanceServiceClient(self.sdk)
        self.settle()
        for name in sorted(self.bindings):
            current = self.require(name)
            if instance_cloud_state(current) is not InstanceCloudState.STOPPED:
                self.owner.mutate(
                    self.transition_label("repair-stop", name),
                    StopInstanceRequest(id=current.metadata.id),
                    service.stop,
                )
            self.owner.require_stopped(current.metadata.id)
        self.settle()
        for name in self.bindings:
            self.owner.require_stopped(self.require(name).metadata.id)

    def start_guarded(self, name: str, *, boot_protection: dict) -> None:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, StartInstanceRequest

        if (
            boot_protection.get("operation") != self.journal.operation
            or boot_protection.get("guard") != hashlib.sha256(repair_guest.GUARD).hexdigest()
            or boot_protection.get("compute") != self.bindings[name]["compute"]
            or boot_protection.get("binding") != storage.digest(self.bindings[name])
            or not boot_protection.get("boot")
        ):
            raise RuntimeError("Repair start requires independently verified boot protection")
        self.owner.settle()
        current = self.require(name)
        if instance_cloud_state(current) is InstanceCloudState.RUNNING:
            self.owner.settle()
            return
        self.owner.require_stopped(current.metadata.id)
        self.owner.mutate(
            self.transition_label("repair-start", name),
            StartInstanceRequest(id=current.metadata.id),
            InstanceServiceClient(self.sdk).start,
        )
        if instance_cloud_state(self.require(name)) is not InstanceCloudState.RUNNING:
            raise RuntimeError("Guarded repair guest has not reached Running")

    def select_owner(self, *, allocation_id: str, name: str, nic_name: str) -> None:
        from nebius.api.nebius.compute.v1 import IPAlias
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        from .vm_ha_cloud import (
            AllocationOwner,
            allocation_observation,
            network_interface_alias_allocation_ids,
        )

        allocations = AllocationServiceClient(self.sdk)
        self.settle()
        settle_resource(allocations, allocation_id)
        for member in self.bindings:
            self.owner.require_stopped(self.require(member).metadata.id)
        expected = AllocationOwner(self.bindings[name]["compute"], nic_name)
        current_allocation = allocations.get(
            GetAllocationRequest(id=allocation_id), **vm_ha_request_kwargs()
        ).wait()
        observed = allocation_observation(allocation_id, current_allocation).owner
        if observed is not None and observed.instance_id not in {
            value["compute"] for value in self.bindings.values()
        }:
            raise RuntimeError("Repair shared allocation is attached outside the retained pair")

        def change(member: str, *, present: bool) -> None:
            current = self.require(member)
            self.owner.require_stopped(current.metadata.id)
            selected = [
                nic
                for nic in current.spec.network_interfaces
                if (
                    nic.name == nic_name
                    if present
                    else allocation_id
                    in network_interface_alias_allocation_ids(nic, description="Repair member NIC")
                )
            ]
            for nic in selected:
                aliases = network_interface_alias_allocation_ids(
                    nic, description="Repair member NIC"
                )
                if (allocation_id in aliases) is present:
                    continue
                desired = (
                    sorted(set(aliases) | {allocation_id})
                    if present
                    else [item for item in aliases if item != allocation_id]
                )

                def edit(_metadata: Any, spec: Any, *, interface=nic.name, desired=desired) -> None:
                    interfaces = list(spec.network_interfaces)
                    for candidate in interfaces:
                        if candidate.name == interface:
                            candidate.aliases = [IPAlias(allocation_id=item) for item in desired]
                    spec.network_interfaces = interfaces

                self.owner.update(
                    current,
                    ("repair-owner-attach-" if present else "repair-owner-detach-")
                    + member
                    + "-"
                    + nic.name,
                    edit,
                )

        for member in sorted(set(self.bindings) - {name}):
            change(member, present=False)
        change(name, present=True)
        current_allocation = allocations.get(
            GetAllocationRequest(id=allocation_id), **vm_ha_request_kwargs()
        ).wait()
        if allocation_observation(allocation_id, current_allocation).owner != expected:
            raise RuntimeError("Repair shared allocation ownership did not verify")


def management_rules(rules: tuple[Any, ...]) -> tuple[Any, ...]:
    """Narrow existing stateful SSH access, retaining its deny ordering."""
    from nebius.api.nebius.vpc.v1 import RuleAccessAction, RuleProtocol, RuleType

    from .vm_ha_cloud import clone_nebius_sdk_message

    result = []
    for rule in rules:
        spec = rule.spec
        if not spec.__pb2_message__.HasField("ingress") or spec.protocol not in {
            RuleProtocol.ANY,
            RuleProtocol.TCP,
        }:
            continue
        if spec.ingress.destination_ports and 22 not in spec.ingress.destination_ports:
            continue
        if spec.access == RuleAccessAction.ALLOW and spec.type not in {
            RuleType.STATEFUL,
            RuleType.RULE_TYPE_UNSPECIFIED,
        }:
            # Turning a stateless allow into a stateful grant could open access
            # denied by the original egress policy. Never broaden it implicitly.
            continue
        narrowed = clone_nebius_sdk_message(spec)
        if spec.access == RuleAccessAction.ALLOW:
            narrowed.type = RuleType.STATEFUL
        narrowed.protocol = RuleProtocol.TCP
        narrowed.ingress.destination_ports = [22]
        result.append(narrowed)
    if not any(spec.access == RuleAccessAction.ALLOW for spec in result):
        raise RuntimeError("Stopped repair target has no existing stateful SSH ingress authority")
    return tuple(result)


class Quarantine:
    """All-NIC isolation before booting a stopped, unguarded retained guest.

    Only existing stateful TCP/22 ingress survives. An explicit egress deny
    blocks guest-initiated API and workload connections; provider default deny
    covers all other ingress. Nebius link-local exceptions remain separate and
    must be covered by the qualified endpoint/negative-control contract.
    """

    def __init__(self, cloud: RetainedCloud, name: str):
        self.cloud, self.name = cloud, name
        self.path = cloud.journal.path.parent / ("quarantine-" + name + ".json")

    def inspect(self) -> dict:
        from .security_groups import capture

        raw = storage.read(self.path)
        if raw is not None:
            value = json.loads(raw)
            if (
                value["binding"] != self.cloud.bindings[self.name]
                or value["operation"] != self.cloud.journal.operation
            ):
                raise RuntimeError("Repair quarantine belongs to another retained target")
            return value
        current = self.cloud.require(self.name)
        self.cloud.owner.require_stopped(current.metadata.id)
        return {
            "operation": self.cloud.journal.operation,
            "binding": self.cloud.bindings[self.name],
            "original": [asdict(nic) for nic in capture(self.cloud.sdk, current)],
            "groups": {},
            "rules": {},
            "restored": False,
        }

    def apply(self) -> dict:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.vpc.v1 import (
            CreateSecurityGroupRequest,
            CreateSecurityRuleRequest,
            ListSecurityRulesRequest,
            RuleAccessAction,
            RuleEgress,
            RuleProtocol,
            RuleType,
            SecurityGroupServiceClient,
            SecurityGroupSpec,
            SecurityRuleServiceClient,
            SecurityRuleSpec,
        )

        value = self.inspect()
        if value["restored"]:
            raise RuntimeError("A retired quarantine cannot authorize another unguarded start")
        self.cloud.owner.require_stopped(self.cloud.require(self.name).metadata.id)
        storage.publish(self.path, value)
        groups = SecurityGroupServiceClient(self.cloud.sdk)
        rules = SecurityRuleServiceClient(self.cloud.sdk)
        for index, nic in enumerate(value["original"]):
            key = f"{self.name}-{index}"
            group_request = CreateSecurityGroupRequest(
                metadata=ResourceMetadata(
                    parent_id=value["binding"]["project"],
                    name=f"vpngw-repair-{value['operation'][:16]}-{key}",
                ),
                spec=SecurityGroupSpec(network_id=nic["network_id"]),
            )
            identifier = self.cloud.owner.mutate(
                "quarantine-group-" + key, group_request, groups.create
            )
            value["groups"][nic["name"]] = identifier
            storage.publish(self.path, value)
            if nic["name"] not in value["rules"]:

                def group_rules(group: str) -> tuple[Any, ...]:
                    return collect_nebius_pages(
                        lambda token: rules.list(
                            ListSecurityRulesRequest(
                                parent_id=group, page_size=1000, page_token=token
                            ),
                            **vm_ha_request_kwargs(),
                        ),
                        context="Repair original security rules",
                        item_identity=nebius_resource_id,
                    )

                predecessor = tuple(
                    rule for group in nic["effective_ids"] for rule in group_rules(group)
                )
                selected = management_rules(predecessor)
                selected += (
                    SecurityRuleSpec(
                        access=RuleAccessAction.DENY,
                        priority=1,
                        protocol=RuleProtocol.ANY,
                        type=RuleType.STATELESS,
                        egress=RuleEgress(destination_cidrs=["0.0.0.0/0"]),
                    ),
                )
                # Persist exact public rule bytes before any provider request;
                # a retry never reselects from subsequently changed old groups.
                from google.protobuf import json_format  # type: ignore[import-untyped]

                value["rules"][nic["name"]] = [
                    json_format.MessageToDict(
                        spec.__pb2_message__, preserving_proto_field_name=True
                    )
                    for spec in selected
                ]
                storage.publish(self.path, value)
            from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]

            for ordinal, specification in enumerate(value["rules"][nic["name"]]):
                spec = SecurityRuleSpec()
                ParseDict(specification, spec.__pb2_message__)
                request = CreateSecurityRuleRequest(
                    metadata=ResourceMetadata(parent_id=identifier, name=f"repair-{ordinal}"),
                    spec=spec,
                )
                self.cloud.owner.mutate(f"quarantine-rule-{key}-{ordinal}", request, rules.create)
        self._attach(value, restore=False)
        self.verify(value)
        return value

    def _attach(self, value: dict, *, restore: bool) -> None:
        from dataclasses import replace

        from nebius.api.nebius.compute.v1 import InstanceServiceClient

        from .security_group_apply import member_request
        from .security_groups import NICSecurityGroups, capture

        self.cloud.owner.settle()
        current = self.cloud.require(self.name)
        if not restore:
            self.cloud.owner.require_stopped(current.metadata.id)
        before = capture(self.cloud.sdk, current)
        desired = tuple(
            NICSecurityGroups(
                **{
                    **nic,
                    "explicit_ids": tuple(nic["explicit_ids"]),
                    "effective_ids": tuple(nic["effective_ids"]),
                }
            )
            if restore
            else replace(
                next(item for item in before if item.name == nic["name"]),
                explicit_ids=(value["groups"][nic["name"]],),
                effective_ids=(value["groups"][nic["name"]],),
            )
            for nic in value["original"]
        )
        if before != desired:
            request = member_request(
                current,
                {
                    "compute_id": current.metadata.id,
                    "before": [asdict(item) for item in before],
                    "desired": [asdict(item) for item in desired],
                },
                self.cloud.sdk,
            )
            label = ("quarantine-restore-" if restore else "quarantine-attach-") + self.name
            self.cloud.owner.mutate(label, request, InstanceServiceClient(self.cloud.sdk).update)
        if capture(self.cloud.sdk, self.cloud.require(self.name)) != desired:
            raise RuntimeError("Repair quarantine attachments did not match on every NIC")

    def _rules(self, group: str) -> tuple[Any, ...]:
        from nebius.api.nebius.vpc.v1 import (
            ListSecurityRulesRequest,
            SecurityRuleServiceClient,
        )

        return collect_nebius_pages(
            lambda token: SecurityRuleServiceClient(self.cloud.sdk).list(
                ListSecurityRulesRequest(parent_id=group, page_size=1000, page_token=token),
                **vm_ha_request_kwargs(),
            ),
            context="Repair quarantine security rules",
            item_identity=nebius_resource_id,
        )

    def verify(self, value: dict) -> None:
        from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]
        from nebius.api.nebius.vpc.v1 import SecurityRuleSpec

        from .replacement_cloud import message_digest
        from .security_groups import capture

        for nic in capture(self.cloud.sdk, self.cloud.require(self.name)):
            if (
                nic.explicit_ids != (value["groups"][nic.name],)
                or nic.effective_ids != nic.explicit_ids
            ):
                raise RuntimeError("Repair quarantine has a permissive or changed attachment")

            actual = self._rules(nic.explicit_ids[0])
            expected = []
            for raw in value["rules"][nic.name]:
                spec = SecurityRuleSpec()
                ParseDict(raw, spec.__pb2_message__)
                expected.append(message_digest(spec))
            if sorted(message_digest(rule.spec) for rule in actual) != sorted(expected):
                raise RuntimeError("Repair quarantine policy differs from its exact rules")

    def restore(self, *, boot_protection: dict) -> None:
        value = self.inspect()
        expected = {
            "operation": self.cloud.journal.operation,
            "compute": value["binding"]["compute"],
            "binding": storage.digest(value["binding"]),
            "guard": hashlib.sha256(repair_guest.GUARD).hexdigest(),
        }
        if any(
            boot_protection.get(key) != item for key, item in expected.items()
        ) or not boot_protection.get("boot"):
            raise RuntimeError(
                "Repair quarantine release requires exact independent boot protection"
            )
        self._attach(value, restore=True)
        value["restored"] = True
        storage.publish(self.path, value)

    def start(self) -> None:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, StartInstanceRequest

        value = self.inspect()
        if value["restored"]:
            raise RuntimeError("Restored network policy cannot authorize unguarded boot")
        self.verify(value)
        self.cloud.owner.settle()
        current = self.cloud.require(self.name)
        if instance_cloud_state(current) is InstanceCloudState.RUNNING:
            self.cloud.owner.settle()
            return
        self.cloud.owner.require_stopped(current.metadata.id)
        self.cloud.owner.mutate(
            self.cloud.transition_label("repair-quarantined-start", self.name),
            StartInstanceRequest(id=current.metadata.id),
            InstanceServiceClient(self.cloud.sdk).start,
        )
        self.verify(value)

    def cleanup(self) -> None:
        from nebius.api.nebius.vpc.v1 import (
            DeleteSecurityGroupRequest,
            DeleteSecurityRuleRequest,
            SecurityGroupServiceClient,
            SecurityRuleServiceClient,
        )

        value = self.inspect()
        if not value["restored"]:
            raise RuntimeError("Repair quarantine cleanup precedes attachment restoration")
        self._attach(value, restore=True)
        for index, nic in enumerate(value["original"]):
            key = f"{self.name}-{index}"
            for ordinal, _rule in enumerate(value["rules"][nic["name"]]):
                identifier = self.cloud.owner.completed(f"quarantine-rule-{key}-{ordinal}")
                if not identifier:
                    raise RuntimeError("Repair quarantine rule has no creation receipt")
                self.cloud.owner.mutate(
                    f"quarantine-delete-rule-{key}-{ordinal}",
                    DeleteSecurityRuleRequest(id=identifier),
                    SecurityRuleServiceClient(self.cloud.sdk).delete,
                )
            self.cloud.owner.mutate(
                "quarantine-delete-group-" + key,
                DeleteSecurityGroupRequest(id=value["groups"][nic["name"]]),
                SecurityGroupServiceClient(self.cloud.sdk).delete,
            )
