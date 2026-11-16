"""Retire obsolete command envelopes after their accepted effects settle.

Old approval digests never authorize the new reconstruction. Their provider
receipts still identify work that must finish before a successor can proceed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import repair_state
from .repair_activation import archive_file
from .vm_ha_cloud import vm_ha_request_kwargs, wait_vm_ha_operation


def accepted(value: Any):
    if isinstance(value, dict):
        if value.get("cloud_operation_id") and value.get("status") == "accepted":
            yield value
        for child in value.values():
            yield from accepted(child)
    elif isinstance(value, list):
        for child in value:
            yield from accepted(child)


def settle_receipts(sdk: Any, value: Any) -> None:
    import nebius.api.nebius.compute.v1 as compute
    import nebius.api.nebius.iam.v1 as iam
    import nebius.api.nebius.vpc.v1 as vpc
    from nebius.api.nebius.common.v1 import GetOperationRequest

    services = {
        "Instance": compute.InstanceServiceClient,
        "Disk": compute.DiskServiceClient,
        "Allocation": vpc.AllocationServiceClient,
        "Route": vpc.RouteServiceClient,
        "RouteTable": vpc.RouteTableServiceClient,
        "Subnet": vpc.SubnetServiceClient,
        "SecurityGroup": vpc.SecurityGroupServiceClient,
        "SecurityRule": vpc.SecurityRuleServiceClient,
        "ServiceAccount": iam.ServiceAccountServiceClient,
        "AuthPublicKey": iam.AuthPublicKeyServiceClient,
    }
    for record in accepted(value):
        request = record.get("request_type")
        if request is None and record.get("label") == "create-candidate":
            request = "CreateInstanceRequest"
        service = next(
            (
                client
                for resource, client in services.items()
                if request
                in {
                    verb + resource + "Request"
                    for verb in ("Create", "Update", "Delete", "Start", "Stop")
                }
            ),
            None,
        )
        if service is None:
            raise RuntimeError("Accepted predecessor effect has no originating service binding")
        operation = (
            service(sdk)
            .operation_service()
            .get(GetOperationRequest(id=record["cloud_operation_id"]), **vm_ha_request_kwargs())
            .wait()
        )
        if str(operation.id) != record["cloud_operation_id"]:
            raise RuntimeError("Predecessor operation identity changed")
        wait_vm_ha_operation(operation)
        if not operation.done():
            raise RuntimeError("Predecessor provider operation is still settling")


def retire(owner: Any) -> None:
    """Called with guest writers inhibited and the gateway command lock held."""
    if owner.context.get("metadata_retired"):
        return
    from .vm_ha_lifecycle import VMHALifecycleStore

    admission = owner.admission
    lifecycle = VMHALifecycleStore(admission.path).path
    raw = repair_state.read(lifecycle, allow_hardlinks=True)
    if raw is not None:
        try:
            value = json.loads(raw)
        except ValueError:
            value = {}
        transaction = value.get("transaction") if isinstance(value, dict) else None
        if not isinstance(transaction, dict):
            transaction = {}
        identifier = transaction.get("accepted_cloud_operation_id")
        if identifier:
            from nebius.api.nebius.common.v1 import GetOperationRequest

            service = admission.manager._vm_ha_operation_service(
                owner.cloud.sdk, transaction["accepted_cloud_operation_effect"]
            )
            operation = service.get(
                GetOperationRequest(id=identifier), **vm_ha_request_kwargs()
            ).wait()
            if str(operation.id) != identifier:
                raise RuntimeError("Predecessor lifecycle operation identity changed")
            wait_vm_ha_operation(operation)
            if not operation.done():
                raise RuntimeError("Predecessor lifecycle operation is still settling")
        settle_receipts(owner.cloud.sdk, value)
    # Keep route ownership and credentials at their authoritative owners. These
    # are command envelopes, not permission to remove any retained cloud object.
    for kind in ("apply", "security-groups", "replacement", "credential-enrollment"):
        root = (
            Path.home()
            / ".local/state/nebius-vpngw"
            / kind
            / admission.intent["project"]
            / admission.intent["gateway"]
        )
        for suffix, source in (
            ("journal", root / "recovery.json"),
            ("candidate", root / ".recovery.json.vpngw-recovery/candidate"),
        ):
            raw = repair_state.read(source, allow_hardlinks=True)
            if raw is None:
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                value = {}
            if isinstance(value, dict):
                settle_receipts(owner.cloud.sdk, value)
            archive_file(source, owner.directory / "retired" / (kind + "-" + suffix + ".json"))
    owner.context["metadata_retired"] = True
    owner.save()
