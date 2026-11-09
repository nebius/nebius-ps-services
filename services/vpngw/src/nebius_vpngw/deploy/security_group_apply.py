"""Desired NIC attachments, composed by the existing apply owner.

The journal stores cloud identities and request hashes, never credentials. Cloud
mutations reuse the bounded, receipt-based Compute operation implementation used
by staged replacement. Group objects and rules remain customer-owned.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import security_groups
from .operator_guest import read_file
from .replacement import Journal, digest, metadata_only
from .replacement_cloud import (
    Cloud,
    effect_key,
    message_digest,
    resource_id,
    restore_compute_update,
)
from .replacement_ordinary import groups
from .vm_ha_cloud import clone_nebius_sdk_message, vm_ha_observation_budget


class AttachmentJournal(Journal):
    schema = "nebius-vpngw.security-group-apply.v1"

    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        super().__init__(
            project,
            gateway,
            root=root
            or Path.home() / ".local/state/nebius-vpngw/security-groups" / project / gateway,
        )

    def validate(self, state: dict) -> None:
        metadata_only(state)
        if (
            state.get("schema") != self.schema
            or state.get("project_id") != self.project
            or state.get("gateway_name") != self.gateway
            or type(state.get("revision")) is not int
            or state["revision"] < 0
        ):
            raise ValueError(
                "Security Group apply journal is invalid or belongs to another gateway"
            )
        transaction = state.get("transaction")
        if transaction is None:
            return
        try:
            if (
                transaction["phase"] not in {"pending", "complete"}
                or transaction["plan_digest"] != digest(transaction["plan"])
                or not isinstance(transaction["operations"], dict)
                or not isinstance(transaction["locks"], dict)
                or not isinstance(transaction["verified"], list)
                or re.fullmatch(r"[a-f0-9]{64}", transaction["approval"]) is None
                or re.fullmatch(r"[a-f0-9]{64}", transaction["scope"]) is None
                or re.fullmatch(r"[a-f0-9]{32}", transaction["operation_id"]) is None
            ):
                raise ValueError("Invalid transaction authority")
            validate_plan(transaction["plan"])
            from . import attachment_retry

            attachment_retry.validate_successors(transaction)
            successor = transaction.get("corrective_successor")
            if successor is not None and (
                set(successor)
                != {
                    "predecessor",
                    "plan_digest",
                    "prior_approval",
                    "prior_scope",
                    "scope",
                    "failed_lifecycle",
                    "artifact",
                }
                or any(
                    not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
                    for value in successor.values()
                )
                or successor["plan_digest"] != transaction["plan_digest"]
                or successor["scope"] != transaction["scope"]
                or re.fullmatch(r"[a-f0-9]{64}", transaction.get("history", "")) is None
            ):
                raise ValueError("Invalid corrective attachment successor")
            members = transaction["plan"]["members"]
            continuity = transaction.get("owner_continuity")
            if continuity is not None:
                from ..agent.vm_ha.attachment_continuity import validate

                validate(continuity)
                if not any(
                    member["compute_id"] == continuity["compute_id"] for member in members.values()
                ):
                    raise ValueError("Attachment continuity is outside the approved members")
            inhibition = transaction.get("owner_inhibition")
            if inhibition is not None:
                from ..agent.vm_ha.inhibition import STANDBY_REPLACEMENT_INHIBITION_SCHEMA

                if (
                    set(inhibition)
                    != {"schema", "cluster_id", "node_id", "generation_id", "operation_id"}
                    or inhibition["schema"] != STANDBY_REPLACEMENT_INHIBITION_SCHEMA
                    or not all(isinstance(value, str) and value for value in inhibition.values())
                    or re.fullmatch(r"[a-f0-9]{64}", inhibition["generation_id"]) is None
                    or inhibition["operation_id"]
                    != digest(
                        [
                            "security-group-apply",
                            transaction["operation_id"],
                            transaction["approval"],
                            transaction["plan"],
                        ]
                    )
                ):
                    raise ValueError("Invalid attachment owner inhibition")
            if "release_started" in transaction and (
                transaction["release_started"] is not True
                or inhibition is None
                or set(transaction["verified"]) != set(members)
                or any(
                    record["status"] != "settled" for record in transaction["operations"].values()
                )
            ):
                raise ValueError("Attachment release requires all completed cloud effects")
            for key, record in transaction["operations"].items():
                name = record["label"].removeprefix("security-groups-")
                if name not in members or record["label"] != "security-groups-" + name:
                    raise ValueError("Foreign attachment operation")
                member = members[name]
                request = record["request_metadata"]
                metadata = request["metadata"]
                if (
                    record["request_type"] != "UpdateInstanceRequest"
                    or record["status"]
                    not in {"intent", "accepted", "settled", "rejected", "failed"}
                    or key
                    != effect_key(
                        transaction["approval"],
                        sorted(members).index(name),
                        record["label"],
                        attachment_retry.request_scope(transaction, name),
                    )
                    or metadata["id"] != member["compute_id"]
                    or metadata["parent_id"] != self.project
                    or int(metadata["resource_version"]) <= 0
                    or re.fullmatch(r"[a-f0-9]{64}", record["request_sha256"]) is None
                    or record.get("user_data_source", member["compute_id"]) != member["compute_id"]
                    or (
                        record["status"] in {"accepted", "settled", "failed"}
                        and not record["cloud_operation_id"]
                    )
                    or (
                        record["status"] in {"failed", "rejected"}
                        and (
                            set(record.get("failure", {})) != {"kind", "code"}
                            or record["failure"]["kind"]
                            != ("operation" if record["status"] == "failed" else "request")
                            or not isinstance(record["failure"]["code"], str)
                            or not record["failure"]["code"]
                        )
                    )
                ):
                    raise ValueError("Attachment operation authority changed")
                requested = sorted(
                    request["spec"]["network_interfaces"], key=lambda nic: nic["name"]
                )
                wanted = member["desired"]
                if len(requested) != len(wanted) or any(
                    nic["name"] != target["name"]
                    or nic["subnet_id"] != target["subnet_id"]
                    or sorted(group["id"] for group in nic.get("security_groups", []))
                    != target["explicit_ids"]
                    for nic, target in zip(requested, wanted, strict=True)
                ):
                    raise ValueError("Attachment operation no longer matches its desired policy")
            if transaction["phase"] == "complete" and (
                set(transaction["verified"]) != set(members)
                or any(
                    record["status"] != "settled" for record in transaction["operations"].values()
                )
            ):
                raise ValueError("Attachment apply cannot complete with outstanding effects")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Security Group apply transaction is invalid") from error

    def read_transaction(self) -> dict | None:
        """Read-only preview: no directories, locks or journals are created."""
        record = read_file(self.path)
        if record is None:
            return None
        if record["uid"] != os.getuid() or record["mode"] != 0o600:
            raise ValueError("Security Group apply journal must be private")
        state = json.loads(record["content"])
        self.validate(state)
        return state["transaction"]

    def pending(self) -> dict | None:
        transaction = self.read_transaction()
        return transaction if transaction and transaction["phase"] != "complete" else None

    def begin(
        self, plan: dict, *, scope: str, approval: str, operation_id: str | None = None
    ) -> None:
        current = self.state["transaction"]
        if current and current["phase"] != "complete":
            if (
                current["scope"] != scope
                or current["approval"] != approval
                or current["plan_digest"] != digest(plan)
                or operation_id is not None
                and current["operation_id"] != operation_id
            ):
                raise RuntimeError("Resume the exact approved Security Group apply intent")
            return
        if current and current["operation_id"] == operation_id:
            if (
                current["scope"] != scope
                or current["approval"] != approval
                or current["plan_digest"] != digest(plan)
            ):
                raise RuntimeError("Completed attachment execution authority changed")
            return
        self.state["transaction"] = {
            "phase": "pending",
            "plan": plan,
            "plan_digest": digest(plan),
            "scope": scope,
            "approval": approval,
            "operation_id": operation_id or uuid.uuid4().hex,
            "member": 0,
            "operations": {},
            "locks": {},
            "verified": [],
        }
        self.save()

    def replace_unstarted(
        self, proposal: dict, *, approval: str, operation_id: str | None = None
    ) -> None:
        """A corrective release may replace only a provably unused prelude."""
        current = self.state["transaction"]
        if (
            current is not None
            and current.get("corrective_successor") == proposal
            and current["scope"] == proposal["scope"]
            and current["approval"] == approval
        ):
            if operation_id is not None and current["operation_id"] != operation_id:
                raise RuntimeError("Corrective prelude belongs to another command execution")
            return
        if (
            current is None
            or digest(current) != proposal["predecessor"]
            or not unused_prelude(current)
            or current["plan_digest"] != proposal["plan_digest"]
        ):
            raise RuntimeError("Attachment prelude changed before corrective approval")
        history = self.archive()
        self.state["transaction"] = {
            "phase": "pending",
            "plan": current["plan"],
            "plan_digest": current["plan_digest"],
            "scope": proposal["scope"],
            "approval": approval,
            "operation_id": operation_id or uuid.uuid4().hex,
            "member": 0,
            "operations": {},
            "locks": {},
            "verified": [],
            "corrective_successor": proposal,
            "history": history,
        }
        self.save()


def validate_plan(plan: dict) -> None:
    if set(plan) != {"desired_ids", "members"} or not isinstance(plan["members"], dict):
        raise ValueError("Invalid Security Group attachment plan")
    ids = plan["desired_ids"]
    if (
        not isinstance(ids, list)
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,127}", value) is None
            for value in ids
        )
        or ids != sorted(set(ids))
        or len(ids) > 8
    ):
        raise ValueError("Invalid desired Security Group IDs")
    for member in plan["members"].values():
        if (
            set(member) != {"compute_id", "before", "desired"}
            or not isinstance(member["compute_id"], str)
            or not member["compute_id"]
            or not member["before"]
            or not member["desired"]
        ):
            raise ValueError("Invalid Security Group member identity")
        before, desired = groups(member["before"]), groups(member["desired"])
        if len({item.name for item in before}) != len(before) or [
            (item.name, item.subnet_id, item.network_id) for item in before
        ] != [(item.name, item.subnet_id, item.network_id) for item in desired]:
            raise ValueError("Security Group plan changes NIC identity")
        if any(list(item.explicit_ids) != ids or not item.effective_ids for item in desired):
            raise ValueError("Security Group plan does not match desired membership")


def inspect_plan(manager: Any, names: tuple[str, ...], identifiers: tuple[str, ...]) -> dict:
    client = manager._get_client()
    if client is None:
        raise RuntimeError("Security Group discovery requires the Nebius SDK")
    targets = manager.inspect_ordinary_targets(names)
    result: dict[str, Any] = {"desired_ids": sorted(identifiers), "members": {}}
    for name, (identity, _target) in sorted(targets.items()):
        with vm_ha_observation_budget():
            instance = manager.get_ha_instance(identity)
            if (
                resource_id(instance) != identity
                or instance.metadata.parent_id != manager.project_id
            ):
                raise RuntimeError("Security Group discovery returned a foreign Compute instance")
            before = security_groups.capture(client, instance)
            desired = security_groups.desired(client, instance, identifiers)
        result["members"][name] = {
            "compute_id": identity,
            "before": [asdict(item) for item in before],
            "desired": [asdict(item) for item in desired],
        }
    # Canonical JSON also normalizes tuples for durable resume and approval.
    result = json.loads(json.dumps(result))
    validate_plan(result)
    return result


def changed(plan: dict) -> bool:
    return any(member["before"] != member["desired"] for member in plan["members"].values())


def unused_prelude(transaction: dict) -> bool:
    return bool(
        transaction.get("phase") == "pending"
        and transaction.get("member") == 0
        and transaction.get("operations") == {}
        and transaction.get("locks") == {}
        and transaction.get("verified") == []
        and "guests" not in transaction
        and "owner" not in transaction
        and "guest_history" not in transaction
    )


def corrective_prelude(
    observed: dict,
    pending: dict,
    *,
    corrective: dict,
    local: dict,
    project: str,
) -> dict:
    """Read-only proposal, after the existing HA owner admits a corrective release.

    No operation receipt is discarded, and configuration/Compute/NIC/attachment
    intent must remain exact. The new approval binds this archived predecessor.
    """
    if not unused_prelude(pending) or pending["plan"] != observed:
        raise RuntimeError("Corrective release requires an unchanged, unstarted attachment prelude")
    previous = pending.get("corrective_successor")
    previous_artifact = corrective["prior_artifact"]
    if previous is not None:
        if previous["failed_lifecycle"] != corrective["prior_record"] and (
            previous["artifact"] != corrective["prior_artifact"]
        ):
            raise RuntimeError("Attachment corrective lineage differs from the failed release")
        previous_artifact = previous["artifact"]
    old_scope = digest({"desired": local, "artifact": previous_artifact, "project": project})
    new_scope = digest({"desired": local, "artifact": corrective["artifact"], "project": project})
    if pending["scope"] != old_scope or new_scope == old_scope:
        raise RuntimeError("Attachment prelude is not bound to this corrective artifact")
    return {
        "predecessor": digest(pending),
        "plan_digest": pending["plan_digest"],
        "prior_approval": pending["approval"],
        "prior_scope": old_scope,
        "scope": new_scope,
        "failed_lifecycle": corrective["prior_record"],
        "artifact": corrective["artifact"],
    }


def resume_plan(observed: dict, pending: dict, *, scope: str) -> dict:
    original = pending["plan"]
    if pending["scope"] != scope or original["desired_ids"] != observed["desired_ids"]:
        raise RuntimeError(
            "Security Group apply is pending for different configuration or artifact"
        )
    if set(original["members"]) != set(observed["members"]):
        raise RuntimeError("Security Group apply member inventory changed")
    for name, member in original["members"].items():
        current = observed["members"][name]
        if (
            member["compute_id"] != current["compute_id"]
            or member["desired"] != current["desired"]
            or current["before"] not in (member["before"], member["desired"])
        ):
            raise RuntimeError("Security Group apply cloud membership changed outside its plan")
    return original


def require_plan(manager: Any, plan: dict, *, allow_desired: bool = False) -> None:
    """Recheck every member before effects, not only the next one to mutate."""
    current = inspect_plan(manager, tuple(plan["members"]), tuple(plan["desired_ids"]))
    if set(current["members"]) != set(plan["members"]):
        raise RuntimeError("Security Group member disappeared after planning")
    for name, member in plan["members"].items():
        now = current["members"][name]
        if (
            now["compute_id"] != member["compute_id"]
            or now["desired"] != member["desired"]
            or now["before"]
            not in ((member["before"], member["desired"]) if allow_desired else (member["before"],))
        ):
            raise RuntimeError("Security Group plan drifted; inspect a fresh apply plan")


def update_member(manager: Any, journal: AttachmentJournal, name: str) -> dict | None:
    """Settle any prior operation before accepting readback or submitting a CAS."""
    from nebius.api.nebius.compute.v1 import InstanceServiceClient

    from . import attachment_retry

    transaction = journal.state["transaction"]
    plan = transaction["plan"]
    member = plan["members"][name]
    index = sorted(plan["members"]).index(name)
    client = manager._get_client()
    cloud = Cloud(
        client,
        journal,
        member_index=index,
        operation_scope=attachment_retry.request_scope(transaction, name),
    )
    cloud.settle()
    instance = cloud.get_instance(member["compute_id"])
    observed = security_groups.capture(client, instance)
    wanted = groups(member["desired"])
    if observed != wanted:
        if observed != groups(member["before"]):
            raise RuntimeError("Security Group membership changed before update")
        request = member_request(instance, member, client)
        attachment_retry.require_request(transaction, name, request)
        cloud.mutate("security-groups-" + name, request, InstanceServiceClient(client).update)
    security_groups.verify_candidate(client, cloud.get_instance(member["compute_id"]), wanted)
    return verified_update(manager, journal, name)


def verified_update(manager: Any, journal: AttachmentJournal, name: str) -> dict | None:
    """Prove exact post-CAS state before replacing stale SSH cloud evidence."""
    from .ordinary_route_conversion import readback_matches

    transaction = journal.state["transaction"]
    member = transaction["plan"]["members"][name]
    records = [
        value
        for value in transaction["operations"].values()
        if value["label"] == "security-groups-" + name
    ]
    if not records:
        return None
    if len(records) != 1 or records[0]["status"] != "settled":
        raise RuntimeError("Attachment SSH refresh requires an exact successful operation")
    record = records[0]
    if record["resource_id"] != member["compute_id"]:
        raise RuntimeError("Attachment operation returned a different Compute identity")
    current = Cloud(manager._get_client(), journal).get_instance(member["compute_id"])
    request = restore_compute_update(record, current)
    if (
        current.metadata.resource_version <= request.metadata.resource_version
        or not readback_matches(current, request)
    ):
        raise RuntimeError("Attachment readback changed fields outside the exact approved update")
    continuity = transaction.get("owner_continuity")
    if (
        continuity is not None
        and continuity["compute_id"] == member["compute_id"]
        and not (
            str(request.metadata.resource_version) == continuity["before_revision"]
            and message_digest(request.spec) == continuity["after_spec"]
            and str(current.metadata.resource_version) == continuity["after_revision"]
        )
    ):
        raise RuntimeError("Attachment owner revision did not make its exact prepared transition")
    return {
        "compute": member["compute_id"],
        "metadata": message_digest(current.metadata),
        "spec": message_digest(current.spec),
    }


def refresh_guest_trust(
    manager: Any,
    ssh: Any,
    journal: AttachmentJournal,
    name: str,
    proof: dict,
    refresh: Any,
) -> None:
    from .ssh_policy import SSHTrustPolicy

    previous = ssh._ssh_policy
    if not isinstance(previous, SSHTrustPolicy) or not callable(refresh):
        raise RuntimeError("Attachment update requires pinned SSH evidence refresh")
    current = refresh()
    if (
        not isinstance(current, SSHTrustPolicy)
        or current.known_hosts_sha256 != previous.known_hosts_sha256
        or sorted(current.pin_targets) != sorted(previous.pin_targets)
        or sorted(current.transport_targets) != sorted(previous.transport_targets)
    ):
        raise RuntimeError("Attachment SSH refresh cannot change trusted keys or targets")
    current.assert_current()
    if verified_update(manager, journal, name) != proof:
        raise RuntimeError("Compute changed while refreshing attachment SSH evidence")
    ssh._ssh_policy = current


def member_request(instance: Any, member: dict, client: Any) -> Any:
    """One full-spec CAS construction for initial requests and reviewed successors."""
    from nebius.api.nebius.compute.v1 import SecurityGroup, UpdateInstanceRequest

    if resource_id(instance) != member["compute_id"] or security_groups.capture(
        client, instance
    ) != groups(member["before"]):
        raise RuntimeError("Security Group membership or Compute identity changed before update")
    metadata = clone_nebius_sdk_message(instance.metadata)
    revision = str(metadata.resource_version)
    if not revision.isdecimal() or int(revision) <= 0:
        raise RuntimeError("Security Group update requires a positive resource version")
    spec = clone_nebius_sdk_message(instance.spec)
    interfaces = list(spec.network_interfaces)
    by_name = {item.name: item for item in groups(member["desired"])}
    for nic in interfaces:
        nic.security_groups = [SecurityGroup(id=value) for value in by_name[nic.name].explicit_ids]
    spec.network_interfaces = interfaces
    unchanged = clone_nebius_sdk_message(spec)
    original_nics = {nic.name: nic for nic in instance.spec.network_interfaces}
    nics = list(unchanged.network_interfaces)
    for nic in nics:
        nic.security_groups = list(original_nics[nic.name].security_groups)
    unchanged.network_interfaces = nics
    if message_digest(unchanged) != message_digest(instance.spec):
        raise RuntimeError("Security Group update changed unrelated Compute fields")
    return UpdateInstanceRequest(metadata=metadata, spec=spec)


def observations(plan: dict) -> dict[str, dict]:
    return {
        name: {
            "desired_ids": plan["desired_ids"],
            "observed": member["before"],
            "state": "in-sync" if member["before"] == member["desired"] else "drift",
        }
        for name, member in plan["members"].items()
    }


def status(manager: Any, names: tuple[str, ...], identifiers: tuple[str, ...]) -> dict[str, dict]:
    """Incomplete cloud evidence is unknown, never an empty attachment set."""
    result = {
        name: {"desired_ids": list(identifiers), "observed": None, "state": "unknown"}
        for name in names
    }
    try:
        result.update(observations(inspect_plan(manager, names, identifiers)))
    except Exception:
        pass
    return result


def guest_identity_script() -> str:
    """The exact bounded remote identity probe, also exercised under native PID 1."""
    from nebius_vpngw import ordinary_operations

    return (
        ordinary_operations.streamed_bootstrap()
        + """
import hashlib,json,time,yaml
from pathlib import Path
raw=Path('/etc/nebius-vpngw/config-resolved.yaml').read_bytes()
cfg=yaml.safe_load(raw)
if not cfg.get('vm_ha'):
    manager=_ops.Manager(time.monotonic()+15)
    _ops.wait_quiet(manager,_ops.require_idle)
print(json.dumps({'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'config':hashlib.sha256(raw).hexdigest(),
    'binding':cfg.get('vm_ha',{}).get('runtime_binding')}))
"""
    )


def _guest_observation(ssh: Any, target: str, instance: Any, local: dict, script: str) -> dict:
    """Use one fresh, exact authenticated connection for a bounded observation."""
    from .ingress_ssh import _read
    from .ssh_policy import configure_paramiko_host_verification

    paramiko = ssh._ensure_paramiko()
    client = paramiko.SSHClient()
    try:
        configure_paramiko_host_verification(
            client,
            paramiko,
            policy=ssh._ssh_policy,
            hostname=instance.hostname,
            transport_host=target,
        )
        spec = local["gateway_group"]["vm_spec"]
        ssh._connect_client(
            client,
            hostname=target,
            username=spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
            vm_spec=spec,
        )
        return _read(client, "sudo -n /usr/bin/python3 -c " + shlex.quote(script))
    finally:
        client.close()


def guest_identity(ssh: Any, target: str, instance: Any, local: dict) -> dict:
    """Fresh authenticated connection; return only non-secret identity evidence."""
    evidence = _guest_observation(ssh, target, instance, local, guest_identity_script())
    if set(evidence) != {"boot", "config", "binding"} or not evidence["boot"]:
        raise RuntimeError("Security Group access verification returned incomplete identity")
    return evidence


def prepare_owner_continuity(
    manager: Any,
    ssh: Any,
    journal: AttachmentJournal,
    *,
    binding: Any,
    owner_name: str,
    operation: str,
    configs: dict,
    targets: dict,
    local: dict,
) -> None:
    """Prepare both guest readers before the serving Compute revision changes."""
    from ..agent.vm_ha.attachment_continuity import SCHEMA, validate

    transaction = journal.state["transaction"]
    member = transaction["plan"]["members"][owner_name]
    if member["before"] == member["desired"]:
        return
    proposal = transaction.get("owner_continuity")
    if proposal is None:
        current = manager.get_ha_instance(member["compute_id"])
        request = member_request(current, member, manager._get_client())
        proposal = {
            "schema": SCHEMA,
            "cluster_id": binding.cluster_id,
            "generation_id": binding.generation_id,
            "owner_node_id": configs[owner_name].vm_ha_node.node_id,
            "compute_id": member["compute_id"],
            "operation_id": operation,
            "before_revision": str(current.metadata.resource_version),
            "after_revision": str(current.metadata.resource_version + 1),
            "before_spec": message_digest(current.spec),
            "after_spec": message_digest(request.spec),
        }
        validate(proposal)
        transaction["owner_continuity"] = proposal
        journal.save()
    if not (
        proposal["operation_id"] == operation
        and proposal["generation_id"] == binding.generation_id
        and proposal["cluster_id"] == binding.cluster_id
        and proposal["owner_node_id"] == configs[owner_name].vm_ha_node.node_id
        and proposal["compute_id"] == member["compute_id"]
    ):
        raise RuntimeError("Attachment continuity changed command or owner authority")
    current = manager.get_ha_instance(member["compute_id"])
    if str(current.metadata.resource_version) == proposal["before_revision"]:
        request = member_request(current, member, manager._get_client())
        if not (
            message_digest(current.spec) == proposal["before_spec"]
            and message_digest(request.spec) == proposal["after_spec"]
        ):
            raise RuntimeError("Attachment continuity differs from the exact approved spec")
    elif not (
        str(current.metadata.resource_version) == proposal["after_revision"]
        and message_digest(current.spec) == proposal["after_spec"]
    ):
        raise RuntimeError("Attachment continuity cannot adopt another Compute revision")
    script = f"""
import json
from pathlib import Path
from nebius_vpngw.agent.main import _read_vm_ha_config
from nebius_vpngw.agent.vm_ha.attachment_continuity import prepare
print(json.dumps(prepare(json.loads({json.dumps(proposal)!r}),
    config=_read_vm_ha_config(), state_dir=Path('/var/lib/nebius-vpngw/vm-ha'),
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())))
"""
    receipts = [
        _guest_observation(ssh, targets[name][1], configs[name], local, script)
        for name in (owner_name, *sorted(set(configs) - {owner_name}))
    ]
    for receipt in receipts:
        validate(receipt, receipt=True)
        if {key: value for key, value in receipt.items() if key != "ownership_epoch"} != proposal:
            raise RuntimeError("Attachment guest prepared different revision authority")
    if any(receipt != receipts[0] for receipt in receipts[1:]):
        raise RuntimeError("Attachment guests disagree on established ownership epoch")


def guest_lock_present(ssh: Any, target: str, instance: Any, local: dict, receipt: dict) -> bool:
    """Reconcile a lost unlock reply without accepting a foreign or unsafe lock."""
    from .. import apply_lock

    expected = receipt["record_sha256"]
    if re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise ValueError("Invalid attachment lock digest")
    script = Path(apply_lock.__file__).read_text() + (
        "\ntry:\n"
        f"    verify('/var/lib/nebius-vpngw/vm-ha/apply.lock', {expected!r})\n"
        "except FileNotFoundError:\n"
        "    present = False\n"
        "else:\n"
        "    present = True\n"
        "print(json.dumps({'present': present}))\n"
    )
    evidence = _guest_observation(ssh, target, instance, local, script)
    if set(evidence) != {"present"} or type(evidence["present"]) is not bool:
        raise RuntimeError("Attachment lock observation is incomplete")
    return evidence["present"]


def execute(
    manager: Any,
    ssh: Any,
    plan: dict,
    local: dict,
    instances: tuple,
    journal: AttachmentJournal,
    *,
    scope: str,
    approval: str,
    verify_ha: Any = None,
    operation_id: str | None = None,
    request_retry: dict | None = None,
    command_journal: Any = None,
    refresh_ssh_policy: Any = None,
) -> None:
    """Run after guest apply, or alone when the installed runtime is unchanged."""
    from ..schema import VMHARuntimeBinding
    from .ssh_push import SSHPush, VMHAApplyLockReceipt
    from .vm_ha_cloud import allocation_observation

    with journal:
        journal.begin(plan, scope=scope, approval=approval, operation_id=operation_id)
        transaction = journal.state["transaction"]
        if transaction["phase"] == "complete":
            current = inspect_plan(manager, tuple(plan["members"]), tuple(plan["desired_ids"]))
            resume_plan(current, transaction, scope=scope)
            if changed(current):
                raise RuntimeError("Completed attachment execution cannot authorize new drift")
            if verify_ha is not None:
                verify_ha()
            return
        operation = digest(["security-group-apply", transaction["operation_id"], approval, plan])
        configs = {item.hostname: item for item in instances}
        targets = manager.inspect_ordinary_targets(tuple(configs))
        if set(targets) != set(configs):
            raise RuntimeError("Security Group apply requires every configured gateway")
        identities = {
            name: guest_identity(ssh, target, configs[name], local)
            for name, (_compute, target) in targets.items()
        }
        previous_guests = transaction.get("guests")
        if previous_guests is not None and (
            set(previous_guests) != set(identities)
            or any(
                {key: value for key, value in previous_guests[name].items() if key != "boot"}
                != {key: value for key, value in current.items() if key != "boot"}
                for name, current in identities.items()
            )
        ):
            raise RuntimeError(
                "Gateway configuration or runtime binding changed during attachment apply"
            )
        binding = None
        owner = None
        owner_name = None
        order = sorted(configs)
        if any(item.vm_ha_node is not None for item in instances):
            bindings = [
                VMHARuntimeBinding.model_validate(item["binding"]) for item in identities.values()
            ]
            binding = bindings[0]
            if any(item != binding for item in bindings) or {
                node.compute_id for node in binding.nodes
            } != {compute for compute, _target in targets.values()}:
                raise RuntimeError("HA attachment apply has inconsistent runtime authority")
            allocation = allocation_observation(
                binding.shared_allocation_id,
                manager.get_ha_allocation(binding.shared_allocation_id),
            )
            owner = allocation.owner
            owners = [
                name
                for name, (compute, _) in targets.items()
                if owner and compute == owner.instance_id
            ]
            if (
                len(owners) != 1
                or owner is None
                or not any(
                    node.compute_id == owner.instance_id
                    and node.network_interface_name == owner.network_interface_name
                    for node in binding.nodes
                )
            ):
                raise RuntimeError("HA attachment apply has no exact shared allocation owner")
            order.sort(key=lambda name: name == owners[0])
            owner_name = owners[0]
            if "owner" in transaction and transaction["owner"] != asdict(owner):
                raise RuntimeError("HA owner changed during attachment apply")
            if owner_name in transaction["locks"]:
                raise RuntimeError("Pending attachment inhibition cannot preserve the owner")
            from ..agent.vm_ha.inhibition import STANDBY_REPLACEMENT_INHIBITION_SCHEMA

            expected_owner_gate = {
                "schema": STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
                "cluster_id": binding.cluster_id,
                "node_id": configs[owner_name].vm_ha_node.node_id,
                "generation_id": binding.generation_id,
                "operation_id": operation,
            }
            if transaction.get("owner_inhibition") not in (None, expected_owner_gate):
                raise RuntimeError("HA attachment owner inhibition changed authority")
            expected_locks = {
                name: asdict(
                    SSHPush._vm_ha_apply_lock_payload(
                        cluster_id=binding.cluster_id,
                        node_id=configs[name].vm_ha_node.node_id,
                        generation_id=binding.generation_id,
                        operation_id=operation,
                    )[1]
                )
                for name in order
                if name != owner_name
            }
            if any(
                expected_locks.get(name) != receipt
                for name, receipt in transaction["locks"].items()
            ):
                raise RuntimeError("HA attachment passive lock changed authority")
            if transaction.get("release_started") and transaction["locks"] != expected_locks:
                raise RuntimeError("HA attachment release lacks complete passive lock authority")
            transaction["owner"] = asdict(owner)
            journal.save()
            if transaction.get("release_started"):
                if previous_guests != identities or request_retry is not None:
                    raise RuntimeError("Attachment release cannot adopt changed guest authority")
            else:
                for name in order:
                    if name == owner_name:
                        receipt = ssh.inhibit_vm_ha_standby_replacement(
                            targets[name][1],
                            name,
                            local,
                            node_id=configs[name].vm_ha_node.node_id,
                            operation_id=operation,
                        )
                        if receipt != expected_owner_gate:
                            raise RuntimeError(
                                "HA attachment owner gate returned foreign authority"
                            )
                        if (
                            transaction.get("owner_inhibition") is not None
                            and transaction["owner_inhibition"] != receipt
                        ):
                            raise RuntimeError("HA attachment owner inhibition changed")
                        transaction["owner_inhibition"] = receipt
                    else:
                        lock = ssh.install_vm_ha_apply_lock(
                            targets[name][1],
                            configs[name],
                            local,
                            runtime_binding=binding,
                            operation_id=operation,
                        )
                        if asdict(lock) != expected_locks[name]:
                            raise RuntimeError(
                                "HA attachment passive lock returned foreign authority"
                            )
                        if name in transaction["locks"] and transaction["locks"][name] != asdict(
                            lock
                        ):
                            raise RuntimeError("HA attachment lock identity changed")
                        transaction["locks"][name] = asdict(lock)
                    journal.save()

        def release() -> None:
            assert binding is not None and owner_name is not None
            require_plan(manager, plan, allow_desired=True)
            current = inspect_plan(manager, tuple(plan["members"]), tuple(plan["desired_ids"]))
            resume_plan(current, transaction, scope=scope)
            if changed(current):
                raise RuntimeError("Attachment release cannot replay cloud mutations")
            current_owner = allocation_observation(
                binding.shared_allocation_id,
                manager.get_ha_allocation(binding.shared_allocation_id),
            ).owner
            if current_owner != owner:
                raise RuntimeError("HA allocation owner changed before attachment release")
            for name in order:
                if guest_identity(ssh, targets[name][1], configs[name], local) != identities[name]:
                    raise RuntimeError("Gateway identity changed before attachment release")
            # The owner gate cannot inhibit its peer. Keep the peer locked until
            # the exact active owner's release is confirmed, including lost replies.
            ssh.release_vm_ha_standby_replacement_inhibition(
                targets[owner_name][1],
                owner_name,
                local,
                inhibition=transaction["owner_inhibition"],
            )
            for name in order:
                if name != owner_name and guest_lock_present(
                    ssh, targets[name][1], configs[name], local, transaction["locks"][name]
                ):
                    ssh.clear_vm_ha_apply_lock(
                        targets[name][1],
                        configs[name],
                        local,
                        receipt=VMHAApplyLockReceipt(**transaction["locks"][name]),
                    )
            if verify_ha is None:
                raise RuntimeError(
                    "HA attachment apply requires final independent health verification"
                )
            verify_ha()
            transaction["phase"] = "complete"
            journal.save()

        if binding is not None and transaction.get("release_started"):
            release()
            return

        def admission() -> None:
            require_plan(manager, plan, allow_desired=True)
            if binding is not None:
                for name in order:
                    if name == owner_name:
                        ssh.verify_vm_ha_standby_replacement_quiescent(
                            targets[name][1],
                            name,
                            local,
                            inhibition=transaction["owner_inhibition"],
                        )
                    else:
                        ssh.verify_vm_ha_apply_inhibition_quiescent(
                            targets[name][1],
                            name,
                            local,
                            node_id=configs[name].vm_ha_node.node_id,
                            operation_id=operation,
                        )
                current = allocation_observation(
                    binding.shared_allocation_id,
                    manager.get_ha_allocation(binding.shared_allocation_id),
                )
                if current.owner != owner:
                    raise RuntimeError("HA allocation owner changed under attachment inhibition")
            for name in order:
                if guest_identity(ssh, targets[name][1], configs[name], local) != identities[name]:
                    raise RuntimeError("Gateway identity changed under attachment admission")

        admission()
        if request_retry is not None:
            from . import attachment_retry

            if command_journal is None:
                raise RuntimeError("Attachment successor requires its original command authority")
            attachment_retry.authorize(manager, command_journal, journal, request_retry)
            transaction = journal.state["transaction"]
        # A reboot does not grant authority. Rebind only after the exact Compute,
        # configuration, ownership and current inhibition/quiescence proofs above.
        if previous_guests != identities:
            if previous_guests is not None:
                transaction.setdefault("guest_history", []).append(digest(previous_guests))
            transaction["guests"] = identities
            journal.save()
        for name in order:
            if name not in plan["members"]:
                continue
            admission()
            if binding is not None and name == owner_name:
                prepare_owner_continuity(
                    manager,
                    ssh,
                    journal,
                    binding=binding,
                    owner_name=owner_name,
                    operation=operation,
                    configs=configs,
                    targets=targets,
                    local=local,
                )
                admission()
            proof = update_member(manager, journal, name)
            if proof is not None:
                refresh_guest_trust(manager, ssh, journal, name, proof, refresh_ssh_policy)
            if guest_identity(ssh, targets[name][1], configs[name], local) != identities[name]:
                raise RuntimeError("Security Group access verification changed gateway identity")
            if name not in transaction["verified"]:
                transaction["verified"].append(name)
                journal.save()
        admission()
        if binding is not None:
            transaction["release_started"] = True
            journal.save()
            release()
            return
        transaction["phase"] = "complete"
        journal.save()
