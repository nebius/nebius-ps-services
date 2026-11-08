"""Closed metadata schema for managed credential enrollment receipts."""

from __future__ import annotations

import re

from .replacement import metadata_only
from .replacement_cloud import effect_key

REQUESTS = {
    "service-account": "CreateServiceAccountRequest",
    "group": "CreateGroupRequest",
    "membership": "CreateGroupMembershipRequest",
    "permit": "CreateAccessPermitRequest",
    "authorized-key": "CreateAuthPublicKeyRequest",
}


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _actions(intent: dict) -> dict:
    graph = intent["iam_resources"]
    return {
        "service-account": graph["service_account"],
        "group": graph["group"],
        "membership": graph["membership"],
        "permit": graph["project_editor_permit"],
        "authorized-key": graph["authorized_key"],
    }


def _validate_intent(intent: dict, project: str, gateway: str) -> None:
    from ..vm_ha_managed_credentials import vm_ha_authorized_key_name, vm_ha_service_account_name

    fields = {
        "action",
        "algorithm",
        "authorized_key_name",
        "destination",
        "expires_at",
        "key_size_bits",
        "local_source_expected_absent",
        "project_id",
        "service_account_name",
        "iam_resources",
    }
    if "runtime_credentials" in intent:
        fields.add("runtime_credentials")
    if (
        set(intent) != fields
        or intent["action"] not in {"create", "reuse", "resume"}
        or intent["project_id"] != project
        or intent["service_account_name"] != vm_ha_service_account_name(gateway)
        or intent["authorized_key_name"] != vm_ha_authorized_key_name(gateway)
        or intent["algorithm"] != "RS256"
        or intent["key_size_bits"] != 4096
        or intent["expires_at"] is not None
        or type(intent["local_source_expected_absent"]) is not bool
        or not isinstance(intent["destination"], str)
        or not intent["destination"]
        or set(intent["iam_resources"])
        != {"service_account", "group", "membership", "project_editor_permit", "authorized_key"}
    ):
        raise ValueError("Invalid managed credential intent")
    actions = _actions(intent)
    for label, record in actions.items():
        expected = (
            {"action", "member_id"}
            if label == "membership"
            else {"action", "resource_id", "role"}
            if label == "permit"
            else {"action", "id"}
        )
        if set(record) != expected or record["action"] not in {"create", "reuse"}:
            raise ValueError("Invalid managed IAM action")
        if "id" in record and (
            record["id"] is not None
            and (not isinstance(record["id"], str) or not record["id"])
            or (record["action"] == "reuse") != bool(record["id"])
        ):
            raise ValueError("Managed IAM action has no exact identity")
    if (
        actions["permit"]["resource_id"] != project
        or actions["permit"]["role"] != "editor"
        or actions["membership"]["member_id"] != actions["service-account"]["id"]
    ):
        raise ValueError("Managed IAM permission scope changed")


def validate(state: dict, *, schema: str, project: str, gateway: str) -> None:
    metadata_only(state)
    try:
        if (
            set(state)
            != {"schema", "project_id", "gateway_name", "revision", "bindings", "transaction"}
            or state["schema"] != schema
            or state["project_id"] != project
            or state["gateway_name"] != gateway
            or state["bindings"]
            or type(state["revision"]) is not int
            or state["revision"] < 0
        ):
            raise ValueError("Invalid enrollment identity")
        current = state["transaction"]
        if current is None:
            return
        if (
            set(current)
            != {
                "intent",
                "approval",
                "execution_id",
                "phase",
                "selected",
                "operations",
                "receipt",
            }
            or not _sha(current["approval"])
            or re.fullmatch(r"[a-f0-9]{32}", current["execution_id"]) is None
            or current["phase"] not in {"pending", "complete"}
            or current["intent"]["project_id"] != project
            or current["selected"] is not None
            and not _sha(current["selected"])
        ):
            raise ValueError("Invalid enrollment authority")
        actions = _actions(current["intent"])
        _validate_intent(current["intent"], project, gateway)
        seen = set()
        expected_order = [
            label for label, action in actions.items() if action["action"] == "create"
        ]
        for key, record in current["operations"].items():
            label = record["label"]
            if (
                label not in REQUESTS
                or label in seen
                or actions[label]["action"] != "create"
                or key != effect_key(current["approval"], 0, label, current["execution_id"])
                or record["request_type"] != REQUESTS[label]
                or not _sha(record["request_sha256"])
                or type(record["submission_count"]) is not int
                or record["submission_count"] < 0
                or record["single_attempt"] is not True
                or current["selected"] is None
                or record["status"] not in {"intent", "accepted", "settled", "rejected", "failed"}
                or record["status"] in {"accepted", "settled", "failed"}
                and not record["cloud_operation_id"]
                or record["status"] == "settled"
                and not record["resource_id"]
                or label == "authorized-key"
                and (
                    record.get("public_key_sha256") != current["selected"]
                    or "data" in record["request_metadata"].get("spec", {})
                )
            ):
                raise ValueError("Enrollment operation exceeds its approved scope")
            seen.add(label)
        # JSON key sorting does not preserve dispatch order. Every earlier
        # approved action must nevertheless have a settled receipt.
        by_label = {record["label"]: record for record in current["operations"].values()}
        for label in seen:
            if any(
                by_label.get(prior, {}).get("status") != "settled"
                for prior in expected_order[: expected_order.index(label)]
            ):
                raise ValueError("Enrollment operation skipped an unsettled prerequisite")
        if current["phase"] == "complete":
            if (
                current["selected"] is None
                or not isinstance(current["receipt"], dict)
                or any(item["status"] != "settled" for item in current["operations"].values())
                or seen
                != {label for label, action in actions.items() if action["action"] == "create"}
            ):
                raise ValueError("Enrollment lacks completed operation receipts")
            bindings = current["receipt"]
            prefixes = (
                "credential-service-account:",
                "credential-authorized-key:",
                "credential-sha256:",
            )
            nodes = {
                key.removeprefix(prefixes[0]) for key in bindings if key.startswith(prefixes[0])
            }
            account = actions["service-account"]["id"] or by_label["service-account"]["resource_id"]
            authorized_key = (
                actions["authorized-key"]["id"] or by_label["authorized-key"]["resource_id"]
            )
            if (
                not nodes
                or any(not node for node in nodes)
                or set(bindings) != {prefix + node for prefix in prefixes for node in nodes}
                or any(
                    bindings[prefixes[0] + node] != account
                    or bindings[prefixes[1] + node] != authorized_key
                    or not _sha(bindings[prefixes[2] + node])
                    for node in nodes
                )
                or len({bindings[prefixes[2] + node] for node in nodes}) != 1
            ):
                raise ValueError("Published credential differs from exact IAM identities")
        elif current["receipt"] is not None:
            raise ValueError("Pending enrollment cannot assert a completed credential")
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError("Credential enrollment journal is invalid") from error
