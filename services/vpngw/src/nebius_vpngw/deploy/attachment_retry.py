"""Review a new request after a proven no-effect attachment rejection.

The desired command and its inhibition identity remain unchanged. This owner
cannot cancel uncertainty, replace a lock or authorize an unrelated cloud edit.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .apply_command import CommandJournal
from .apply_plan import Progress
from .replacement import digest
from .replacement_cloud import message_digest
from .vm_ha_cloud import clone_nebius_sdk_message

_FIELDS = {"parent", "child", "member", "rejected_key", "request_sha256", "resource_version"}


def request_scope(transaction: dict, name: str) -> str:
    successor = transaction.get("request_successors", {}).get(name)
    return (
        digest(["attachment-request-successor", transaction["operation_id"], successor["proposal"]])
        if successor
        else transaction["operation_id"]
    )


def validate_successors(transaction: dict) -> None:
    successors = transaction.get("request_successors", {})
    if not isinstance(successors, dict):
        raise ValueError("Invalid attachment request successor inventory")
    for name, successor in successors.items():
        proposal = successor["proposal"]
        if (
            name not in transaction["plan"]["members"]
            or set(successor) != {"proposal", "history"}
            or set(proposal) != _FIELDS
            or proposal["member"] != name
            or not isinstance(proposal["resource_version"], str)
            or not proposal["resource_version"].isdecimal()
            or int(proposal["resource_version"]) <= 0
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in [
                    successor["history"],
                    *(proposal[key] for key in _FIELDS - {"member", "resource_version"}),
                ]
            )
            or proposal["rejected_key"] in transaction["operations"]
        ):
            raise ValueError("Invalid reviewed attachment request successor")


def plan(manager: Any, parent: CommandJournal, transaction: dict | None) -> dict | None:
    """Read-only proposal: original authority, full request parity, fresh revision."""
    from . import security_group_apply as attachments

    if transaction is None or transaction["phase"] != "pending":
        return None
    rejected = [
        (key, item)
        for key, item in transaction["operations"].items()
        if item["status"] == "rejected"
    ]
    if not rejected:
        return None
    snapshot = parent.snapshot()
    command = snapshot["transaction"] if snapshot else None
    if (
        len(rejected) != 1
        or command is None
        or command["phase"] != "pending"
        or command["execution_id"] != transaction["operation_id"]
        or command["progress"].get("attachments") != Progress.STARTED.value
        or any(
            value != Progress.VERIFIED.value
            for key, value in command["progress"].items()
            if key != "attachments"
        )
    ):
        raise RuntimeError(
            "Rejected attachments lack their original completed command prerequisites"
        )
    key, record = rejected[0]
    if (
        type(record.get("submission_count")) is not int
        or record.get("submission_count") != 1
        or record.get("single_attempt") is not True
        or record.get("cloud_operation_id") is not None
        or record.get("failure", {}).get("kind") != "request"
        or record["failure"]["code"]
        not in {
            "ABORTED",
            "FAILED_PRECONDITION",
            "INVALID_ARGUMENT",
            "PERMISSION_DENIED",
            "UNAUTHENTICATED",
            "NOT_FOUND",
            "ALREADY_EXISTS",
            "OUT_OF_RANGE",
            "UNIMPLEMENTED",
        }
        or any(
            item["status"] != "settled"
            for other, item in transaction["operations"].items()
            if other != key
        )
    ):
        raise RuntimeError(
            "Cloud rejection does not prove no effects; retain original recovery authority"
        )
    name = record["label"].removeprefix("security-groups-")
    member = transaction["plan"]["members"][name]
    attachments.require_plan(manager, transaction["plan"], allow_desired=True)
    instance = manager.get_ha_instance(member["compute_id"])
    request = attachments.member_request(instance, member, manager._get_client())
    continuity = transaction.get("owner_continuity")
    if continuity is not None and continuity["compute_id"] == member["compute_id"]:
        if not (
            str(instance.metadata.resource_version) == continuity["before_revision"]
            and message_digest(instance.spec) == continuity["before_spec"]
            and message_digest(request.spec) == continuity["after_spec"]
        ):
            raise RuntimeError(
                "HA attachment owner no longer matches its prepared revision and spec; "
                "retain the command, guest inhibition and continuity receipts for guarded recovery"
            )
    signature = message_digest(request)
    previous = clone_nebius_sdk_message(request)
    previous.metadata.resource_version = int(
        record["request_metadata"]["metadata"]["resource_version"]
    )
    if message_digest(previous) != record["request_sha256"]:
        raise RuntimeError("Unrelated Compute state changed since the rejected request")
    return {
        "parent": digest(snapshot),
        "child": digest(transaction),
        "member": name,
        "rejected_key": key,
        "request_sha256": signature,
        "resource_version": str(request.metadata.resource_version),
    }


def authorize(manager: Any, parent: CommandJournal, journal: Any, proposal: dict) -> None:
    """Caller holds writer locks and has just reproven guest/HA quiescence."""
    transaction = journal.state["transaction"]
    if plan(manager, parent, transaction) != proposal:
        raise RuntimeError("Reviewed attachment request successor changed before admission")
    history = journal.archive()
    candidate = deepcopy(transaction)
    candidate["operations"].pop(proposal["rejected_key"])
    candidate.setdefault("request_successors", {})[proposal["member"]] = {
        "proposal": deepcopy(proposal),
        "history": history,
    }
    journal.state["transaction"] = candidate
    journal.save()


def require_request(transaction: dict, name: str, request: Any) -> None:
    successor = transaction.get("request_successors", {}).get(name)
    if successor and message_digest(request) != successor["proposal"]["request_sha256"]:
        raise RuntimeError("Reviewed attachment request changed; retain its successor checkpoint")
