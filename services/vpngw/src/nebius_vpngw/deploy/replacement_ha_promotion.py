"""Canonical planned promotion between two prepared replacement passes."""

from __future__ import annotations

import time

from .. import cli
from ..agent.vm_ha.progress import planned_request_fingerprint
from .replacement import Journal, digest
from .replacement_cloud import Cloud
from .replacement_ha_runtime import Runtime
from .vm_ha_cloud import (
    AllocationOwner,
    ClusterCloudObservation,
    allocation_observation,
    compute_observation,
    vm_ha_request_kwargs,
)


def promote(ports, journal: Journal, *, effect_id: str) -> None:
    from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

    tx = journal.state["transaction"]
    record = tx["results"].setdefault(
        "ha-promotion", {"effect_id": effect_id, "intent_at": time.time()}
    )
    if record["effect_id"] != effect_id:
        raise RuntimeError("HA promotion intent changed")
    journal.save()
    runtime = Runtime(ports, journal)
    members = ports.members(journal, candidate=True)
    binding = ports.runtime_binding(journal, candidate=True)
    target = next(
        member
        for member in members
        if member.instance_name == tx["plan"]["members"][0]["logical_name"]
    )
    former = next(member for member in members if member != target)
    operation = "failover" if target.role == "passive" else "failback"
    if "request" not in record:
        owner = ports.owner(members)
        if owner.compute_id == former.compute_id:
            ports._serving(journal)
            request = runtime.command(
                target.compute_id,
                target.instance_name,
                "sudo -n /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-manual-" + operation,
            )
        else:
            # A crash may occur after the canonical guest accepted its request.
            # Only its durable request from this intent can reconcile that gap.
            request = runtime.json(
                target.compute_id,
                target.instance_name,
                "import json;from pathlib import Path;print(Path("
                + repr("/var/lib/nebius-vpngw/vm-ha/manual-" + operation + ".json")
                + ").read_text())",
            )
        if (
            request.get("schema") != "nebius-vpngw/vm-ha-manual-" + operation + "-v1"
            or request.get("cluster_id") != binding.cluster_id
            or request.get("node_id") != target.node_id
            or request.get("generation_id") != binding.generation_id
            or not isinstance(request.get("requested_at"), int | float)
            or request["requested_at"] < record["intent_at"] - 5
        ):
            raise RuntimeError("HA promotion request is not bound to this replacement intent")
        record["request"] = request
        journal.save()

    def status(member):
        try:
            value = runtime.status(member, binding, locked=False)
            return cli._validate_vm_ha_planned_status(
                value, inst_cfg=ports.instances[member.instance_name], runtime_binding=binding
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise cli._VMHAPlannedTerminalObservationUnavailable(
                "target-agent" if member == target else "standby-agent"
            ) from error

    def cloud():
        try:
            sdk_cloud = Cloud(ports.sdk, journal)
            observations = {
                member.role: compute_observation(
                    member.compute_id, sdk_cloud.get_instance(member.compute_id)
                )
                for member in members
            }
            by_role = {member.role: member for member in members}
            allocation = (
                AllocationServiceClient(ports.sdk)
                .get(
                    GetAllocationRequest(id=binding.shared_allocation_id), **vm_ha_request_kwargs()
                )
                .wait()
            )
            return ClusterCloudObservation(
                allocation_observation(binding.shared_allocation_id, allocation),
                observations["active"],
                observations["passive"],
                AllocationOwner(
                    by_role["active"].compute_id, by_role["active"].network_interface_name
                ),
                AllocationOwner(
                    by_role["passive"].compute_id, by_role["passive"].network_interface_name
                ),
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise cli._VMHAPlannedTerminalObservationUnavailable("cloud") from error

    context = cli._VMHAPlannedTerminalContext(
        target_role=target.role,
        former_role=former.role,
        target_member=target,
        former_member=former,
        target_owner=AllocationOwner(target.compute_id, target.network_interface_name),
        allocation_id=binding.shared_allocation_id,
        runtime_binding=binding,
        status_reader=lambda: status(target),
        standby_status_reader=lambda: status(former),
        cloud_reader=cloud,
        request_timeout_seconds=60,
        cutover_timeout_seconds=600,
        restoration_timeout_seconds=300,
    )
    cli._wait_for_vm_ha_planned_transfer(
        context=context,
        operation_name="Replacement promotion",
        started_at=time.monotonic(),
        request_fingerprint=planned_request_fingerprint(record["request"]),
    )
    record["verified"] = True
    journal.save()
    ports.transition(
        journal,
        revision=3,
        members=ports.members(journal, candidate=True),
        evidence={
            "kind": "promotion",
            "owner_compute_id": target.compute_id,
            "canonical_promotion_sha256": digest(record["request"]),
        },
    )
