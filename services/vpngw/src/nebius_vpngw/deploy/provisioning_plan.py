"""Frozen provisioning requests and references to exact creation receipts.

The binary templates contain only resource metadata/specifications. Compute
user-data is reconstructed by the SSH/bootstrap owner after approval and never
included in this plan. Names cannot resolve a creation slot.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from google.protobuf.message import DecodeError  # type: ignore[import-untyped]

from .replacement_cloud import message_digest


def request_types() -> dict[str, tuple[Any, Any, str, str]]:
    import nebius.api.nebius.compute.v1 as compute
    import nebius.api.nebius.vpc.v1 as vpc

    return {
        "CreateSubnetRequest": (
            vpc.CreateSubnetRequest,
            vpc.SubnetServiceClient,
            "create",
            "subnet",
        ),
        "UpdateSubnetRequest": (
            vpc.UpdateSubnetRequest,
            vpc.SubnetServiceClient,
            "update",
            "subnet",
        ),
        "UpdatePoolRequest": (vpc.UpdatePoolRequest, vpc.PoolServiceClient, "update", "pool"),
        "CreateRouteTableRequest": (
            vpc.CreateRouteTableRequest,
            vpc.RouteTableServiceClient,
            "create",
            "table",
        ),
        "CreateRouteRequest": (vpc.CreateRouteRequest, vpc.RouteServiceClient, "create", "route"),
        "CreateDiskRequest": (
            compute.CreateDiskRequest,
            compute.DiskServiceClient,
            "create",
            "disk",
        ),
        "CreateAllocationRequest": (
            vpc.CreateAllocationRequest,
            vpc.AllocationServiceClient,
            "create",
            "allocation",
        ),
        "CreateInstanceRequest": (
            compute.CreateInstanceRequest,
            compute.InstanceServiceClient,
            "create",
            "compute",
        ),
    }


# Only these identity fields may be derived from successful creation receipts.
REFERENCE_FIELDS = {
    "CreateRouteRequest": {"metadata.parent_id": "table"},
    "UpdateSubnetRequest": {"spec.route_table_id": "table"},
    "CreateSubnetRequest": {"spec.route_table_id": "table"},
    "CreateAllocationRequest": {
        "spec.ipv4_public.subnet_id": "subnet",
        "spec.ipv4_private.subnet_id": "subnet",
    },
    "CreateInstanceRequest": {
        "spec.boot_disk.existing_disk.id": "disk",
        "spec.network_interfaces.0.subnet_id": "subnet",
        "spec.network_interfaces.0.ip_address.allocation_id": "allocation",
        "spec.network_interfaces.0.public_ip_address.allocation_id": "allocation",
    },
}


def _field(request: Any, path: str) -> tuple[Any, str]:
    value = request
    parts = path.split(".")
    for part in parts[:-1]:
        value = value[int(part)] if part.isdecimal() else getattr(value, part)
    return value, parts[-1]


@dataclass(frozen=True)
class Reference:
    field: str
    slot: str
    kind: str


@dataclass(frozen=True)
class Request:
    key: str
    type: str
    protobuf: str
    references: tuple[Reference, ...] = ()
    guest: str | None = None

    @classmethod
    def freeze(
        cls,
        key: str,
        request: Any,
        *,
        references: tuple[Reference, ...] = (),
        guest: str | None = None,
    ) -> Request:
        value = cls(
            key,
            type(request).__name__,
            base64.b64encode(
                request.__pb2_message__.SerializeToString(deterministic=True)
            ).decode(),
            references,
            guest,
        )
        value.template()
        return value

    @classmethod
    def parse(cls, value: dict) -> Request:
        if not isinstance(value, dict) or set(value) != {
            "key",
            "type",
            "protobuf",
            "references",
            "guest",
        }:
            raise ValueError("Invalid provisioning request fields")
        try:
            if not isinstance(value["references"], list):
                raise ValueError("Invalid provisioning references")
            result = cls(
                **dict(value, references=tuple(Reference(**r) for r in value["references"]))
            )
        except TypeError as error:
            raise ValueError("Invalid provisioning references") from error
        result.template()
        return result

    def mapping(self) -> dict:
        return json.loads(json.dumps(asdict(self)))

    def template(self) -> Any:
        try:
            if (
                not isinstance(self.key, str)
                or not self.key
                or not isinstance(self.type, str)
                or self.type not in request_types()
                or not isinstance(self.protobuf, str)
            ):
                raise ValueError("Unknown provisioning request")
            request = request_types()[self.type][0]()
            request.__pb2_message__.ParseFromString(base64.b64decode(self.protobuf, validate=True))
            if self.type == "CreateInstanceRequest":
                if (
                    request.spec.cloud_init_user_data
                    or not isinstance(self.guest, str)
                    or not self.guest
                    or self.guest != request.metadata.name
                ):
                    raise ValueError("Compute plan must bind a guest without retaining user-data")
            elif self.guest is not None:
                raise ValueError("Only Compute may derive bootstrap data")
            permitted = REFERENCE_FIELDS.get(self.type, {})
            if len({r.field for r in self.references}) != len(self.references):
                raise ValueError("Repeated provisioning reference")
            for reference in self.references:
                if (
                    not isinstance(reference.field, str)
                    or permitted.get(reference.field) != reference.kind
                    or not isinstance(reference.slot, str)
                    or not reference.slot
                ):
                    raise ValueError("Invalid typed provisioning reference")
                owner, field = _field(request, reference.field)
                if getattr(owner, field) != "@" + reference.slot:
                    raise ValueError("Provisioning reference differs from its frozen template")
            return request
        except (
            KeyError,
            TypeError,
            AttributeError,
            IndexError,
            binascii.Error,
            DecodeError,
        ) as error:
            raise ValueError("Invalid provisioning request") from error

    def resolve(self, bindings: dict[str, dict], *, bootstrap: str | None = None) -> Any:
        request = self.template()
        for reference in self.references:
            binding = bindings.get(reference.slot)
            if (
                not binding
                or binding.get("kind") != reference.kind
                or not _identifier(binding.get("resource_id"))
                or not _identifier(binding.get("cloud_operation_id"))
                or not isinstance(binding.get("request_sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", binding["request_sha256"]) is None
            ):
                raise RuntimeError("Creation reference has no exact successful operation receipt")
            owner, field = _field(request, reference.field)
            setattr(owner, field, binding["resource_id"])
        if self.guest is not None:
            if not bootstrap:
                raise RuntimeError("Compute creation requires the approved bootstrap owner")
            request.spec.cloud_init_user_data = bootstrap
        elif bootstrap is not None:
            raise ValueError("Bootstrap data cannot be supplied to another resource")
        return request


def validate_requests(requests: tuple[Request, ...]) -> None:
    slots: dict[str, str] = {}
    keys = set()
    for request in requests:
        request.template()
        if request.key in keys:
            raise ValueError("Repeated provisioning request identity")
        keys.add(request.key)
        for reference in request.references:
            if slots.get(reference.slot) != reference.kind:
                raise ValueError("Provisioning references require an earlier typed creation slot")
        _, _, method, kind = request_types()[request.type]
        if method == "create":
            slots[request.key] = kind


def creation_receipt(request: Request, operation: dict, resolved: Any) -> dict:
    """Admit only the successful operation for this exact resolved create request."""
    _, _, method, kind = request_types()[request.type]
    if (
        method != "create"
        or operation.get("label") != request.key
        or operation.get("status") != "settled"
        or operation.get("request_sha256") != message_digest(resolved)
        or not _identifier(operation.get("resource_id"))
        or not _identifier(operation.get("cloud_operation_id"))
    ):
        raise RuntimeError("Provisioning creation lacks its exact successful operation receipt")
    return {
        "kind": kind,
        "resource_id": operation["resource_id"],
        "cloud_operation_id": operation["cloud_operation_id"],
        "request_sha256": operation["request_sha256"],
    }


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()
