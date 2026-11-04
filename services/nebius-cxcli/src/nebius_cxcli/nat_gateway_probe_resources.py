"""Write-ahead ownership journal for the temporary SDK-only probe resources."""

from __future__ import annotations

import ipaddress
import time
import uuid
from contextlib import suppress
from typing import Any

from .nebius_api_helpers import (
    bounded_nebius_request_kwargs,
    sdk_message_to_mapping,
    sdk_parse_message,
    wait_nebius_operation,
)


class ProbeResources:
    def __init__(self, sdk: Any, probe: dict[str, Any], save: Any, assert_held: Any) -> None:
        self.sdk, self.probe, self.save, self.assert_held = sdk, probe, save, assert_held

    def api(self, kind: str) -> tuple[Any, Any]:
        from nebius.api.nebius.compute import v1 as compute
        from nebius.api.nebius.vpc import v1 as vpc

        namespace = vpc if kind == "allocation" else compute
        return namespace, getattr(namespace, kind.title() + "ServiceClient")(self.sdk.sdk)

    def read(self, kind: str, entry: dict[str, Any], *, timeout: float = 30) -> Any:
        from nebius.aio.service_error import RequestError
        from nebius.api.nebius.common.v1 import GetByNameRequest

        namespace, client = self.api(kind)
        try:
            if entry.get("id"):
                request = getattr(namespace, "Get" + kind.title() + "Request")(id=entry["id"])
                return client.get(
                    request, **bounded_nebius_request_kwargs(timeout_seconds=timeout)
                ).wait()
            request_type = (
                namespace.GetAllocationByNameRequest if kind == "allocation" else GetByNameRequest
            )
            return client.get_by_name(
                request_type(parent_id=self.sdk.project_id, name=entry["name"]),
                **bounded_nebius_request_kwargs(timeout_seconds=timeout),
            ).wait()
        except RequestError as exc:
            if exc.status.code.name == "NOT_FOUND":
                return None
            raise RuntimeError("Cannot establish probe resource existence") from None
        except Exception:
            raise RuntimeError("Cannot establish probe resource existence") from None

    def verify(self, kind: str, obj: Any, entry: dict[str, Any]) -> None:
        value = sdk_message_to_mapping(obj)
        metadata, spec = value["metadata"], value["spec"]
        if (
            metadata.get("parent_id") != self.sdk.project_id
            or metadata.get("name") != entry["name"]
            or metadata.get("labels", {}).get("cxcli-nat-probe") != self.probe["config"]["run_id"]
            or (entry.get("id") and metadata.get("id") != entry["id"])
        ):
            raise RuntimeError("Probe resource ownership differs; refusing adoption or deletion")
        if kind == "allocation":
            valid = spec.get("ipv4_private", {}).get("subnet_id") == self.probe[
                "subnet_id"
            ] and not spec.get("ipv4_public")
        elif kind == "disk":
            valid = (
                spec.get("source_image_id") == self.probe["shape"]["image"]
                and (int(spec.get("size_gibibytes", 0)) * 1024**3 or int(spec.get("size_bytes", 0)))
                == self.probe["shape"]["disk_gib"] * 1024**3
            )
        else:
            interfaces = spec.get("network_interfaces", [])
            valid = (
                len(interfaces) == 1
                and interfaces[0].get("subnet_id") == self.probe["subnet_id"]
                and interfaces[0].get("ip_address", {}).get("allocation_id")
                == self.probe["resources"]["allocation"]["id"]
                and "public_ip_address" not in interfaces[0]
                and not spec.get("service_account_id")
                and spec.get("boot_disk", {}).get("existing_disk", {}).get("id")
                == self.probe["resources"]["disk"]["id"]
            )
        if not valid:
            raise RuntimeError("Probe resource relationships differ; refusing adoption or deletion")

    def private_address(self, *, deadline: float) -> str:
        """Bind the SSH destination to exact owned Compute and VPC resources."""
        values = {}
        for kind in ("instance", "disk", "allocation"):
            self.assert_held()
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise RuntimeError("NAT probe identity readback deadline expired")
            entry = self.probe["resources"][kind]
            obj = self.read(kind, entry, timeout=min(30, remaining))
            if obj is None:
                raise RuntimeError("NAT probe resource disappeared before SSH verification")
            self.verify(kind, obj, entry)
            values[kind] = sdk_message_to_mapping(obj)
        status = values["allocation"].get("status", {})
        details = status.get("details", {})
        assignment = status.get("assignment", {}).get("network_interface", {})
        try:
            address = ipaddress.IPv4Network(details["allocated_cidr"])
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("NAT probe private allocation address is unavailable") from None
        if (
            address.prefixlen != 32
            or details.get("subnet_id") != self.probe["subnet_id"]
            or assignment.get("instance_id") != self.probe["resources"]["instance"]["id"]
            or values["instance"].get("status", {}).get("state") != "RUNNING"
        ):
            raise RuntimeError("NAT probe private allocation is not bound to the running test VM")
        return str(address.network_address)

    def create(self, kind: str, spec: dict[str, Any]) -> str:
        entries = self.probe.setdefault("resources", {})
        entry = entries.get(kind)
        if entry is None:
            entry = {
                "name": "cxcli-nat-probe-" + self.probe["config"]["run_id"][:20] + "-" + kind,
                "id": "",
                "create_key": str(uuid.uuid4()),
                "phase": "intent",
            }
            entries[kind] = entry
            self.save()
        if entry["phase"] == "intent":
            namespace, client = self.api(kind)
            try:
                request = sdk_parse_message(
                    getattr(namespace, "Create" + kind.title() + "Request"),
                    {
                        "metadata": {
                            "parent_id": self.sdk.project_id,
                            "name": entry["name"],
                            "labels": {"cxcli-nat-probe": self.probe["config"]["run_id"]},
                        },
                        "spec": spec,
                    },
                )
                self.assert_held()
                entry["phase"] = "submitted"
                self.save()
                operation = client.create(
                    request,
                    metadata=[("x-idempotency-key", entry["create_key"])],
                    **bounded_nebius_request_kwargs(),
                ).wait()
                entry.update(operation_id=operation.id, id=operation.resource_id)
                self.save()
                wait_nebius_operation(
                    operation, timeout_seconds=300, action="Create NAT probe " + kind
                )
            except Exception:
                # Instance cloud-init contains a temporary host private key.
                # Retain operation identities, never provider/request diagnostics.
                raise RuntimeError(
                    f"NAT probe {kind} creation failed; inspect journaled operation and cleanup status"
                ) from None
        elif entry.get("operation_id"):
            self.sdk.wait_operation(entry["operation_id"])
        elif entry["phase"] == "submitted":
            raise RuntimeError(
                "Probe creation outcome is unknown; use nat reconcile after confirming prior requests settled"
            )
        obj = self.read(kind, entry)
        if obj is None:
            raise RuntimeError(
                "Probe create outcome unresolved; do not resubmit until the prior request is reconciled"
            )
        self.verify(kind, obj, entry)
        entry.update(id=obj.metadata.id, phase="created")
        self.save()
        return entry["id"]

    def provision(self, cloud_init: str) -> None:
        shape = self.probe["shape"]
        disk = self.create(
            "disk",
            {
                "size_gibibytes": shape["disk_gib"],
                "block_size_bytes": 4096,
                "type": "NETWORK_SSD",
                "source_image_id": shape["image"],
                "forbid_deletion": False,
            },
        )
        allocation = self.create(
            "allocation", {"ipv4_private": {"subnet_id": self.probe["subnet_id"]}}
        )
        self.create(
            "instance",
            {
                "resources": {"platform": shape["platform"], "preset": shape["preset"]},
                "boot_disk": {
                    "attach_mode": "READ_WRITE",
                    "existing_disk": {"id": disk},
                    "device_id": "boot",
                },
                "network_interfaces": [
                    {
                        "name": "eth0",
                        "subnet_id": self.probe["subnet_id"],
                        "ip_address": {"allocation_id": allocation},
                    }
                ],
                "cloud_init_user_data": cloud_init,
            },
        )

    def cleanup(self) -> None:
        self.probe["cleanup"] = "pending"
        self.save()
        if self.probe.get("ssh_cleanup_pending"):
            raise RuntimeError(
                "NAT probe SSH shutdown is unresolved; cleanup requires reconciliation"
            )
        # Stop at the first unresolved deletion: never delete attached child resources.
        for kind in ("instance", "disk", "allocation"):
            entry = self.probe.get("resources", {}).get(kind)
            if not entry or entry["phase"] in {"intent", "deleted"}:
                continue
            failed_create = False
            if entry["phase"] == "submitted":
                if not entry.get("operation_id"):
                    raise RuntimeError(
                        "Probe creation outcome is unknown; cleanup requires nat reconcile after requests settle"
                    )
                from .nat_gateway_sdk import NatOperationFailed

                try:
                    self.sdk.wait_operation(entry["operation_id"])
                except NatOperationFailed:
                    failed_create = True
            obj = self.read(kind, entry)
            if obj is None:
                if not entry.get("id") and not failed_create:
                    raise RuntimeError(
                        "Probe creation outcome remains ambiguous; cleanup is pending"
                    )
                if entry["phase"] != "deleting" and not failed_create:
                    raise RuntimeError("Probe resource vanished outside the cleanup journal")
                entry["phase"] = "deleted"
                self.save()
                continue
            self.verify(kind, obj, entry)
            entry["id"] = obj.metadata.id
            if entry["phase"] != "deleting":
                entry.update(phase="deleting", delete_key=str(uuid.uuid4()))
                self.save()
                self.assert_held()
                namespace, client = self.api(kind)
                operation = client.delete(
                    getattr(namespace, "Delete" + kind.title() + "Request")(id=entry["id"]),
                    metadata=[("x-idempotency-key", entry["delete_key"])],
                    **bounded_nebius_request_kwargs(),
                ).wait()
                entry["delete_operation_id"] = operation.id
                self.save()
                wait_nebius_operation(
                    operation, timeout_seconds=300, action="Delete NAT probe " + kind
                )
            elif entry.get("delete_operation_id"):
                self.sdk.wait_operation(entry["delete_operation_id"])
            else:
                raise RuntimeError("Probe deletion outcome is unresolved; cleanup remains pending")
            if self.read(kind, entry) is not None:
                raise RuntimeError("Probe deletion has not converged; cleanup remains pending")
            entry["phase"] = "deleted"
            self.save()
        self.probe["cleanup"] = "complete"
        self.save()

    def reconcile(self, *, ssh_collectors_stopped: bool = False) -> None:
        """Called only after the operator explicitly confirms requests are terminal."""
        if self.probe.get("ssh_cleanup_pending"):
            if not ssh_collectors_stopped:
                raise RuntimeError(
                    "Confirm prior SSH collectors and descendants have stopped on their original "
                    "runners before reconciling pending probe cleanup"
                )
            self.probe["ssh_cleanup_pending"] = False
            self.save()
        for kind in ("disk", "allocation", "instance"):
            entry = self.probe.get("resources", {}).get(kind)
            if not entry or entry["phase"] not in {"submitted", "deleting"}:
                continue
            operation_id = (
                entry.get("delete_operation_id")
                if entry["phase"] == "deleting"
                else entry.get("operation_id")
            )
            if operation_id:
                from .nat_gateway_sdk import NatOperationFailed

                with suppress(NatOperationFailed):
                    self.sdk.wait_operation(operation_id)
            obj = self.read(kind, entry)
            if obj is None:
                entry["phase"] = "deleted"
            else:
                self.verify(kind, obj, entry)
                entry.update(id=obj.metadata.id, phase="created")
            self.save()
