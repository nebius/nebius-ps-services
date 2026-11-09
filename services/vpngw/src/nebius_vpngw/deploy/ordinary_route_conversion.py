"""Reviewed private-table copies and exact receipt-bound request resolution.

The source table is never modified. Copies are fully verified before a subnet
CAS can publish the new table. Generated table IDs are resolved only from this
transaction's successful creation receipts, never from names or labels.
"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from typing import Any

from ..apply_approval import digest
from .ordinary_route_ownership import GATEWAY, MANAGED, labels, snapshot
from .replacement_cloud import message_digest


def slot(table: str) -> str:
    return "@private-table:" + table


def table_label(table: str) -> str:
    return "table-" + digest(table)[:32]


def convert(plan: dict, conversions: dict[str, dict], *, required: bool = True) -> dict:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import CreateRouteTableRequest, RouteTableSpec

    from .ordinary_route_plan import request_record

    plan = deepcopy(plan)
    plan["conversions"] = deepcopy(conversions)
    plan["released"] = []
    if not required:
        return plan
    original_requests = deepcopy(plan["requests"])
    original_coverage = deepcopy(plan["coverage"])
    sources = {item["source"] for item in conversions.values()}
    plan["requests"] = {
        key: record
        for key, record in original_requests.items()
        if (
            record["before"]["parent_id"]
            if record["type"] == "DeleteRouteRequest"
            else record["request"]["metadata"]["parent_id"]
        )
        not in sources
    }
    plan["coverage"] = {
        key: item for key, item in original_coverage.items() if item["table"] not in sources
    }
    for copy_id, conversion in sorted(conversions.items()):
        source = conversion["source"]
        target = slot(copy_id)
        plan["requests"][table_label(copy_id)] = request_record(
            CreateRouteTableRequest(
                metadata=ResourceMetadata(
                    parent_id=plan["project"],
                    name="vpngw-private-" + digest([plan["gateway"], copy_id])[:32],
                    labels={
                        MANAGED: "ordinary-table-v1",
                        GATEWAY: digest([plan["project"], plan["gateway"]])[:32],
                    },
                ),
                spec=RouteTableSpec(network_id=plan["infrastructure"]["network_id"]),
            ),
            source=source,
            copy_id=copy_id,
        )
        retired = {
            record["request"]["id"]
            for record in original_requests.values()
            if record["type"] == "DeleteRouteRequest" and record["before"]["parent_id"] == source
        }
        for label, original in original_requests.items():
            if (
                original["type"] == "CreateRouteRequest"
                and original["request"]["metadata"]["parent_id"] == source
            ):
                plan["requests"]["create-" + digest([copy_id, label])[:32]] = {
                    **deepcopy(original),
                    "table_source": copy_id,
                }
        for identifier, original in sorted(conversion["routes"].items()):
            owned = plan["ownership"].get(identifier)
            if owned:
                # Relinquish original-table authority only after the complete
                # copy/attachment transaction; never delete from a shared table.
                plan["released"].append(identifier)
            if identifier in retired:
                continue
            record = deepcopy(original)
            record["table_source"] = copy_id
            record["copy_of"] = identifier
            if owned:
                record["instance_index"] = owned["instance_index"]
            plan["requests"]["copy-" + digest([copy_id, identifier])[:32]] = record
        for subnet, record in sorted(conversion["subnets"].items()):
            plan["requests"]["attach-" + subnet] = {**deepcopy(record), "table_source": copy_id}
        for key, item in original_coverage.items():
            if item["table"] == source:
                plan["coverage"][digest([copy_id, key])] = {**item, "table": target}
    plan["released"] = sorted(set(plan["released"]))
    return plan


def resolve(record: dict, plan: dict, operations: dict) -> Any:
    from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
    from google.protobuf.message import DecodeError  # type: ignore[import-untyped]

    from .ordinary_route_plan import request_types

    try:
        request = request_types()[record["type"]]()
        request.__pb2_message__.ParseFromString(base64.b64decode(record["protobuf"], validate=True))
        if (
            message_digest(request) != record["sha256"]
            or MessageToDict(request.__pb2_message__, preserving_proto_field_name=True)
            != record["request"]
        ):
            raise ValueError("Request representations differ")
    except (KeyError, TypeError, ValueError, binascii.Error, DecodeError) as error:
        raise RuntimeError("Approved route request changed") from error
    source = record.get("table_source")
    if source is None:
        return request
    creation = next(
        (item for item in operations.values() if item["label"] == table_label(source)), None
    )
    if creation is None or creation["status"] != "settled" or not creation["resource_id"]:
        raise RuntimeError("Private route table has no successful exact creation receipt")
    target = creation["resource_id"]
    if record["type"] == "CreateRouteRequest":
        request.metadata.parent_id = target
        if "instance_index" in record:
            request.metadata.labels = labels(
                plan["project"],
                plan["gateway"],
                target,
                request.spec.destination.cidr,
                record["instance_index"],
            )
            request.metadata.name = "vpngw-" + digest([target, request.spec.destination.cidr])[:32]
    elif record["type"] == "UpdateSubnetRequest":
        request.spec.route_table_id = target
    else:
        raise RuntimeError("Invalid private-table request dependency")
    return request


def expected_infrastructure(plan: dict, operations: dict) -> dict:
    """Project only confirmed cloud effects onto the original exact inventory."""
    expected = deepcopy(plan["infrastructure"])
    for operation in sorted(
        operations.values(), key=lambda item: item["request_type"] != "CreateRouteTableRequest"
    ):
        if operation["status"] != "settled":
            continue
        record = plan["requests"][operation["label"]]
        request = resolve(record, plan, operations)
        if record["type"] == "CreateRouteTableRequest":
            identifier = operation["resource_id"]
            expected["table_ids"] = sorted(set(expected["table_ids"]) | {identifier})
            expected["tables"][identifier] = {
                "spec_sha256": message_digest(request.spec),
                "default": False,
                "subnets": [],
            }
        elif record["type"] == "UpdateSubnetRequest":
            identifier, target = request.metadata.id, request.spec.route_table_id
            original = next(item for item in expected["targets"] if item["subnet_id"] == identifier)
            expected["tables"][original["table_id"]]["subnets"].remove(identifier)
            expected["tables"][target]["subnets"].append(identifier)
            expected["tables"][target]["subnets"].sort()
            original.update(table_id=target, spec_sha256=message_digest(request.spec))
    return expected


def readback_matches(resource: Any, request: Any) -> bool:
    return bool(
        resource.metadata.parent_id == request.metadata.parent_id
        and resource.metadata.name == request.metadata.name
        and dict(resource.metadata.labels) == dict(request.metadata.labels)
        and message_digest(resource.spec) == message_digest(request.spec)
    )


def copy_inventory(routes: tuple[Any, ...]) -> dict:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import CreateRouteRequest

    from .ordinary_route_plan import request_record
    from .vm_ha_cloud import clone_nebius_sdk_message

    return {
        snapshot(route)["id"]: request_record(
            CreateRouteRequest(
                metadata=ResourceMetadata(
                    parent_id=route.metadata.parent_id,
                    name=route.metadata.name,
                    labels=dict(route.metadata.labels),
                ),
                spec=clone_nebius_sdk_message(route.spec),
            )
        )
        for route in routes
    }
