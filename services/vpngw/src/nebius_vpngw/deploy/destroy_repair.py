"""Retire retained-VM repair through explicit destruction, without guest access."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import repair_state as storage
from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .repair_cloud import CloudJournal, retained_binding, settle_resource
from .repair_cloud_retry import RepairCloud
from .vm_ha_cloud import nebius_request_error_code_is, vm_ha_request_kwargs

if TYPE_CHECKING:
    from .destroy import DestroyPlan, NebiusDestroyBackend, _DestroyJournal
    from .vm_ha_lifecycle import VMHALifecycleState


def journal(project: str, gateway: str) -> storage.Journal:
    return storage.Journal(Path.home() / ".local/state/nebius-vpngw/repair" / project / gateway)


def reconstruction_authority(project: str, gateway: str) -> VMHALifecycleState | None:
    """Recover the exact native authority saved before repair's lifecycle reset.

    This never creates an ACTIVE record or infers ownership from resource names.
    A corrupt predecessor is not needed when repair saved fresh authority.
    """
    from .vm_ha_lifecycle import VMHALifecycleState

    owner = journal(project, gateway)
    record = owner.load()
    if record is None or not record["intent"]["ha"]:
        return None
    root = owner.root / "runs" / record["id"]
    raw = storage.read(root / "reconstruction-authority.json")
    if raw is not None:
        value = json.loads(raw)
        if set(value) != {"intent", "state"} or value["intent"] != storage.digest(record["intent"]):
            raise RuntimeError("Repair reconstruction authority changed its target intent")
        state = VMHALifecycleState.from_dict(value["state"])
    else:
        raw = storage.read(root / "retired" / "lifecycle.json")
        if raw is None:
            return None
        state = VMHALifecycleState.from_dict(json.loads(raw))
    expected = {(t["name"], t["compute"], t["disk"]) for t in record["intent"]["targets"]}
    actual = {(m.instance_name, m.compute_id, m.disk_id) for m in state.members}
    if (
        state.record_version != 4
        or state.transaction is None
        or state.project_id != project
        or state.gateway_name != gateway
        or actual != expected
    ):
        raise RuntimeError("Repair reconstruction authority differs from retained VM identities")
    return state


def archived_retired_disks(state: VMHALifecycleState) -> frozenset[str]:
    """Recover cleanup authority through the same deployment's repair history.

    Repair deliberately replaces runtime lifecycle state. Its archived native
    records still own disks retained by earlier standby replacements. Follow
    only digest-linked repair records whose exact member identities connect to
    the current lifecycle; unrelated history never grants deletion authority.
    The caller holds gateway exclusion and still verifies every cloud resource.
    """
    from .vm_ha_lifecycle import REPLACEMENT_CYCLE_PATTERN, VMHALifecycleState

    owner = journal(state.project_id, state.gateway_name)
    record = owner.load()
    if record is None or state.transaction is None:
        return frozenset()
    names = {member.instance_name for member in state.members}
    pattern = re.compile(
        rf"((?:retired|replacement)(?:-{REPLACEMENT_CYCLE_PATTERN})?)-compute:(.+)"
    )

    def pairs(snapshot: VMHALifecycleState) -> set[tuple[str, str, str]]:
        bindings = dict(snapshot.transaction.resource_bindings) if snapshot.transaction else {}
        found = {
            (member.instance_name, member.compute_id, member.disk_id)
            for member in snapshot.members
            if member.compute_id and member.disk_id
        }
        for name in names:
            compute, disk = bindings.get("compute:" + name), bindings.get("disk:" + name)
            if compute and disk:
                found.add((name, compute, disk))
        for key, compute in bindings.items():
            match = pattern.fullmatch(key)
            if match is not None and match[2] in names:
                disk = bindings.get(f"{match[1]}-disk:{match[2]}")
                if compute and disk:
                    found.add((match[2], compute, disk))
        return found

    def read(path: Path) -> bytes | None:
        for parent in (path.parent, *path.parents):
            if parent.is_symlink():
                raise RuntimeError("Repair history contains a symlink")
            if parent == owner.root:
                break
        return storage.read(path)

    known = pairs(state)
    disks: set[str] = set()
    history: dict[str, Path] | None = None
    seen: set[str] = set()
    while record is not None:
        identity = storage.digest(record)
        if identity in seen:
            raise RuntimeError("Repair history contains an ownership cycle")
        seen.add(identity)
        intent = record["intent"]
        targets = {(t["name"], t["compute"], t["disk"]) for t in intent["targets"]}
        if (
            not intent["ha"]
            or (intent["project"], intent["gateway"]) != (state.project_id, state.gateway_name)
            or {t[0] for t in targets} != names
            or not targets.issubset(known)
        ):
            break
        raw = read(owner.root / "runs" / record["id"] / "retired" / "lifecycle.json")
        archived = None
        if raw is not None:
            try:
                archived = VMHALifecycleState.from_dict(json.loads(raw))
            except (ValueError, TypeError, KeyError, AttributeError):
                pass  # Opaque damaged state is evidence, never resource authority.
        if (
            archived is not None
            and archived.record_version == 4
            and archived.transaction is not None
            and (archived.project_id, archived.gateway_name, archived.cluster_id)
            == (state.project_id, state.gateway_name, state.cluster_id)
            and {m.instance_name for m in archived.members} == names
            and targets.issubset(pairs(archived))
        ):
            known.update(pairs(archived))
            bindings = dict(archived.transaction.resource_bindings)
            for key in bindings:
                match = pattern.fullmatch(key)
                if match is not None and match[1].startswith("retired") and match[2] in names:
                    disk = bindings.get(f"{match[1]}-disk:{match[2]}")
                    if disk and (match[2], bindings[key], disk) in known:
                        disks.add(disk)
        predecessor = record["previous"]
        if predecessor is None:
            break
        if history is None:
            history = {}
            directory = owner.root / "history"
            if directory.is_symlink():
                raise RuntimeError("Repair history contains a symlink")
            for path in directory.glob("*.json"):
                if re.fullmatch(r"[a-f0-9]{32}\.json", path.name) is None:
                    continue
                raw = read(path)
                if raw is not None:
                    try:
                        value = storage.Journal.decode(raw)
                    except RuntimeError:
                        continue
                    if path.stem == value["id"]:
                        history[storage.digest(value)] = path
        predecessor_path = history.get(predecessor)
        raw = read(predecessor_path) if predecessor_path is not None else None
        record = storage.Journal.decode(raw) if raw is not None else None
        if record is not None and storage.digest(record) != predecessor:
            raise RuntimeError("Repair history changed during ownership inspection")
    return frozenset(disks)


class Admission:
    """One immutable destruction plan owns settlement and retirement."""

    def __init__(
        self, owner: storage.Journal, record: dict, plan: DestroyPlan, backend: NebiusDestroyBackend
    ):
        self.owner, self.record, self.plan, self.backend = owner, record, plan, backend
        intent = record["intent"]
        if (intent["project"], intent["gateway"]) != (plan.project_id, plan.gateway_name):
            raise RuntimeError("Repair predecessor belongs to another gateway")
        names = {target["name"]: target["compute"] for target in intent["targets"]}
        disks = {target["disk"] for target in intent["targets"]}
        if record["phase"] != "complete" and (
            any(names.get(r.name, r.resource_id) != r.resource_id for r in plan.compute)
            or not intent["ha"]
            and (
                not {r.resource_id for r in plan.compute}.issubset(names.values())
                or not {r.resource_id for r in plan.disks}.issubset(disks)
            )
        ):
            raise RuntimeError(
                "Destroy cannot adopt replacement resources for absent repair targets"
            )
        planned = {r.resource_id: r for r in plan.compute}
        for target in intent["targets"]:
            resource = planned.get(target["compute"])
            current: Any = backend._get_instance_by_id(target["compute"])
            if current is None:
                continue
            if (
                resource is None
                or resource.name != target["name"]
                or storage.digest(retained_binding(current)) != target["binding"]
                or str(current.metadata.name) != target["name"]
                or target["disk"] not in {r.resource_id for r in plan.disks}
            ):
                raise RuntimeError("Destroy plan differs from exact retained repair targets")
        self.cloud_journal = CloudJournal(owner.root, operation=record["id"], intent=intent)
        self.cloud = RepairCloud(
            backend.client, self.cloud_journal, member_index=0, operation_scope=record["id"]
        )
        self.targets = sorted(t["compute"] for t in intent["targets"])
        self.bound = owner.root / "destruction" / plan.digest / "predecessor.json"
        payload = {"plan": plan.digest, "record": record}
        existing = storage.read(self.bound)
        if existing is None:
            storage.publish(self.bound, payload)
        elif json.loads(existing) != payload:
            raise RuntimeError("Destruction changed its bound repair predecessor")

    def _settled(self) -> None:
        if any(r["status"] in {"intent", "accepted"} for r in self.cloud.records.values()):
            raise RuntimeError("Repair provider operations remain unsettled")

    def settle_before_stop(self) -> None:
        # An uncertain Start/Update may be replayed here. Never defer the first
        # settlement until after final fencing or Compute deletion.
        self.cloud.settle(require_success=False)
        self._settled()
        for target in self.targets:
            if not self.backend.command_compute_absent(target):
                settle_resource(self.backend.instances, target)

    def verify_fenced(self) -> None:
        self._settled()
        for target in self.targets:
            if not self.backend.command_compute_absent(target):
                settle_resource(self.backend.instances, target)
        for resource in self.plan.compute:
            if not self.backend.instance_fenced(resource):
                raise RuntimeError("Repair target is not Stopped or absent after final fencing")
        for target in self.targets:
            if target not in {r.resource_id for r in self.plan.compute}:
                if not self.backend.command_compute_absent(target):
                    raise RuntimeError("An absent repair target reappeared during destruction")

    def require_absent(self) -> str:
        if not all(self.backend.command_compute_absent(target) for target in self.targets):
            raise RuntimeError("Repair retirement requires every exact Compute target absent")
        return storage.digest(self.targets)

    def cleanup(self) -> None:
        self.require_absent()
        self._settled()
        cleanup_quarantine(self.cloud, self.plan.project_id)
        self._settled()

    def retire(self, destruction: _DestroyJournal) -> None:
        from .destroy import OrdinaryDestroyJournal, VMHADestroyJournal
        from .vm_ha_lifecycle import VMHALifecycleStatus

        if isinstance(destruction, OrdinaryDestroyJournal):
            terminal = destruction.receipt.terminal
        elif isinstance(destruction, VMHADestroyJournal):
            terminal = destruction.journal.state.status is VMHALifecycleStatus.DESTROYED
        else:
            terminal = False
        if (
            not terminal
            or destruction.plan != self.plan
            or destruction.pending_effect is not None
            or destruction.accepted_operation is not None
        ):
            raise RuntimeError("Repair retirement requires terminal destruction")
        self.cleanup()
        absence = [self.require_absent(), self.require_absent()]
        self.owner.retire_destroyed(
            self.record,
            {
                "schema": "nebius-vpngw.repair-destruction.v1",
                "repair": self.record["id"],
                "repair_sha256": storage.digest(self.record),
                "project": self.plan.project_id,
                "gateway": self.plan.gateway_name,
                "destroy_plan": self.plan.digest,
                "targets": self.targets,
                "cloud_sha256": storage.digest(self.cloud_journal.state),
                "absence": absence,
                "quarantine_absent": True,
            },
        )


def inspect(plan: DestroyPlan, backend: NebiusDestroyBackend) -> Admission | None:
    owner = journal(plan.project_id, plan.gateway_name)
    record = owner.load()
    raw = storage.read(owner.root / "destruction" / plan.digest / "predecessor.json")
    if raw is not None:
        saved = json.loads(raw)
        if set(saved) != {"plan", "record"} or saved["plan"] != plan.digest:
            raise RuntimeError("Repair destruction binding is malformed")
        if record is not None and record != saved["record"]:
            raise RuntimeError("Destroy cannot adopt a different active repair")
        record = storage.Journal.decode(json.dumps(saved["record"]).encode())
    return None if record is None else Admission(owner, record, plan, backend)


def pending_retirement(plan: DestroyPlan) -> bool:
    """Keep terminal ordinary destruction bound until its repair unlink finishes."""
    owner = journal(plan.project_id, plan.gateway_name)
    record = owner.load()
    if record is None:
        return False
    raw = storage.read(owner.root / "destruction" / plan.digest / "predecessor.json")
    if raw is None:
        return False
    saved = json.loads(raw)
    if set(saved) != {"plan", "record"} or saved["plan"] != plan.digest:
        raise RuntimeError("Repair destruction binding is malformed")
    return saved["record"] == record


def cleanup_quarantine(cloud: RepairCloud, project: str) -> None:
    """Delete only unchanged receipt-owned quarantine, after Compute absence."""
    from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
    from nebius.api.nebius.compute.v1 import InstanceServiceClient, ListInstancesRequest
    from nebius.api.nebius.vpc.v1 import (
        DeleteSecurityGroupRequest,
        DeleteSecurityRuleRequest,
        GetSecurityGroupRequest,
        GetSecurityRuleRequest,
        ListSecurityRulesRequest,
        SecurityGroupServiceClient,
        SecurityRuleServiceClient,
    )

    groups = {
        r["resource_id"]: r
        for r in cloud.records.values()
        if r["request_type"] == "CreateSecurityGroupRequest" and r["status"] == "settled"
    }
    rules = {
        r["resource_id"]: r
        for r in cloud.records.values()
        if r["request_type"] == "CreateSecurityRuleRequest" and r["status"] == "settled"
    }
    if not groups and not rules:
        return
    group_service, rule_service = (
        SecurityGroupServiceClient(cloud.sdk),
        SecurityRuleServiceClient(cloud.sdk),
    )

    def current(identifier: str, record: dict, group: bool) -> Any:
        service, request = (
            (group_service, GetSecurityGroupRequest)
            if group
            else (rule_service, GetSecurityRuleRequest)
        )
        try:
            value = service.get(request(id=identifier), **vm_ha_request_kwargs()).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise
        expected = record["request_metadata"]
        if (
            str(value.metadata.id) != identifier
            or str(value.metadata.parent_id) != expected["metadata"]["parent_id"]
            or str(value.metadata.name) != expected["metadata"]["name"]
            or MessageToDict(value.spec.__pb2_message__, preserving_proto_field_name=True)
            != expected.get("spec", {})
            or group
            and value.status.default
        ):
            raise RuntimeError("Repair quarantine identity or policy changed; retain foreign state")
        return value

    def group_rules(identifier: str) -> tuple[Any, ...]:
        return collect_nebius_pages(
            lambda token: rule_service.list(
                ListSecurityRulesRequest(parent_id=identifier, page_size=500, page_token=token),
                **vm_ha_request_kwargs(),
            ),
            context="Destroy repair quarantine rules",
            item_identity=nebius_resource_id,
        )

    # Check every foreign dependency before the first deletion, and each object's
    # exact identity again immediately before its own mutation.
    for identifier, record in groups.items():
        if current(identifier, record, True) is None:
            continue
        found = group_rules(identifier)
        if any(nebius_resource_id(rule) not in rules for rule in found):
            raise RuntimeError("Repair quarantine contains foreign rules; no cleanup admitted")
    instances = InstanceServiceClient(cloud.sdk)
    inventory: tuple[Any, ...] = collect_nebius_pages(
        lambda token: instances.list(
            ListInstancesRequest(parent_id=project, page_size=500, page_token=token),
            **vm_ha_request_kwargs(),
        ),
        context="Destroy repair quarantine attachments",
        item_identity=nebius_resource_id,
    )
    for instance in inventory:
        if any(
            str(group.id) in groups
            for nic in instance.spec.network_interfaces
            for group in nic.security_groups
        ):
            raise RuntimeError("Repair quarantine is attached to surviving Compute")
    for group, records, service, request in (
        (False, rules, rule_service, DeleteSecurityRuleRequest),
        (True, groups, group_service, DeleteSecurityGroupRequest),
    ):
        for identifier, record in sorted(records.items()):
            if current(identifier, record, group) is not None:
                cloud.mutate(
                    "destroy-quarantine-" + identifier, request(id=identifier), service.delete
                )
            if current(identifier, record, group) is not None:
                raise RuntimeError("Repair quarantine deletion has not converged")
