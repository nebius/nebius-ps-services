"""Admission of ordinary operations while a logical member is being replaced."""

from __future__ import annotations

from typing import Any

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .replacement_cloud import LOGICAL_LABEL, ROLE_LABEL, TRANSACTION_LABEL


class ReplacementPending(RuntimeError):
    """A pending logical binding must never fall back to a configured address."""


def admit(instance: Any, logical_name: str) -> None:
    labels = dict(getattr(instance.metadata, "labels", {}) or {})
    operation = labels.get(TRANSACTION_LABEL)
    if not operation:
        return
    if (
        labels.get(LOGICAL_LABEL) != logical_name
        or labels.get(ROLE_LABEL) != "active"
        or instance.metadata.name != logical_name
    ):
        raise ReplacementPending(
            "Gateway replacement is pending; preserve resources and use apply --resume-replacement PATH"
        )


def admit_absence(sdk: Any, project: str, logical_name: str) -> None:
    """A rename gap must never be mistaken for authorization to create a VM."""
    from nebius.api.nebius.compute.v1 import InstanceServiceClient, ListInstancesRequest

    instances: Any = collect_nebius_pages(
        lambda token: InstanceServiceClient(sdk).list(
            ListInstancesRequest(parent_id=project, page_token=token, page_size=100)
        ),
        context="Replacement logical member",
        item_identity=nebius_resource_id,
    )
    for instance in instances:
        if instance.metadata.labels.get(LOGICAL_LABEL) == logical_name:
            # Even an active member under a different physical name is not an
            # absent gateway. The committed binding must first be repaired.
            raise ReplacementPending(
                "Gateway logical binding exists under a replacement identity; use apply --resume-replacement PATH"
            )


def require_idle(local: dict, plan: Any) -> None:
    """Resolve the complete logical group before commands that formerly used YAML IPs."""
    from .vm_manager import VMManager

    project = str(local.get("project_id") or "")
    if not project:
        raise RuntimeError("Gateway operations require project_id for exact replacement admission")
    with VMManager(project_id=project, region=plan.gateway_group.region) as manager:
        sdk = manager._get_client()
        if sdk is None:
            raise RuntimeError(
                "Gateway replacement admission requires authoritative Compute discovery"
            )
        for instance in plan.iter_instance_configs():
            current = manager._get_vm_by_name(sdk, instance.hostname)
            if current is None:
                raise RuntimeError(
                    "Configured gateway is absent; no SSH address fallback is permitted"
                )
            address = manager._vm_public_ip_from_object(current)
            if not address:
                raise RuntimeError("Configured gateway has no authoritative management address")
            instance.external_ip = address
