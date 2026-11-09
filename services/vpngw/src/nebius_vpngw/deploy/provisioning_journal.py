"""Private resource receipts for a single approved provisioning child."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..apply_approval import digest
from .provisioning_inventory import resource_types
from .provisioning_plan import Request, creation_receipt, request_types, validate_requests
from .replacement import Journal, metadata_only
from .replacement_cloud import effect_key, message_digest


def requests_from_plan(plan: dict) -> tuple[Request, ...]:
    if set(plan) != {
        "project_id",
        "gateway_name",
        "configuration_sha256",
        "artifact_sha256",
        "requests",
        "observed",
        "inventories",
        "bootstraps",
    }:
        raise ValueError("Invalid provisioning plan scope")
    for value in (plan["configuration_sha256"], plan["artifact_sha256"]):
        if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
            raise ValueError("Provisioning requires exact configuration and artifact hashes")
    requests = tuple(Request.parse(value) for value in plan["requests"])
    validate_requests(requests)
    if not isinstance(plan["observed"], dict):
        raise ValueError("Provisioning requires exact predecessor observations")
    for identifier, observed in plan["observed"].items():
        fields = {"kind", "id", "parent_id", "metadata_sha256", "spec_sha256"}
        hashes = ["metadata_sha256", "spec_sha256"]
        if observed.get("kind") in {"allocation", "security-group"}:
            fields.add("status_sha256")
            hashes.append("status_sha256")
        if (
            set(observed) != fields
            or observed["kind"] not in resource_types()
            or not isinstance(identifier, str)
            or not identifier
            or observed["id"] != identifier
            or not isinstance(observed["parent_id"], str)
            or not observed["parent_id"]
            or any(
                not isinstance(observed[key], str)
                or re.fullmatch(r"[a-f0-9]{64}", observed[key]) is None
                for key in hashes
            )
        ):
            raise ValueError("Provisioning predecessor observation is invalid")
    if not isinstance(plan["inventories"], list):
        raise ValueError("Provisioning requires frozen inventory boundaries")
    scopes = set()
    slots = {
        r.key: request_types()[r.type][3]
        for r in requests
        if request_types()[r.type][2] == "create"
    }
    for inventory in plan["inventories"]:
        if (
            set(inventory) != {"kind", "parent", "ids"}
            or inventory["kind"] not in {"route", "subnet"}
            or not isinstance(inventory["parent"], str)
            or not inventory["parent"]
            or not isinstance(inventory["ids"], list)
            or any(not isinstance(item, str) or not item for item in inventory["ids"])
            or inventory["ids"] != sorted(set(inventory["ids"]))
        ):
            raise ValueError("Provisioning inventory boundary is invalid")
        scope = (inventory["kind"], inventory["parent"])
        if scope in scopes:
            raise ValueError("Provisioning inventory is repeated")
        scopes.add(scope)
        if inventory["parent"].startswith("@"):
            if (
                inventory["kind"] != "route"
                or slots.get(inventory["parent"][1:]) != "table"
                or inventory["ids"]
            ):
                raise ValueError("Provisioning inventory has no exact table creation slot")
        elif any(
            plan["observed"].get(identifier, {}).get("kind") != inventory["kind"]
            for identifier in inventory["ids"]
        ):
            raise ValueError("Provisioning inventory lacks exact predecessor observations")
    guests = {r.guest for r in requests if r.guest is not None}
    if set(plan["bootstraps"]) != guests:
        raise ValueError("Provisioning bootstrap scope differs from Compute creation")
    for bootstrap in plan["bootstraps"].values():
        if set(bootstrap) != {"template_sha256", "trust_sha256"} or any(
            not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in bootstrap.values()
        ):
            raise ValueError("Provisioning requires the exact bootstrap and trust-owner intents")
    for request in requests:
        template = request.template()
        if (
            request.type != "CreateRouteRequest"
            and template.metadata.parent_id != plan["project_id"]
        ):
            raise ValueError("Provisioning request belongs to another project")
        if request_types()[request.type][2] == "update" and (
            not template.metadata.id
            or template.metadata.resource_version <= 0
            or template.metadata.id not in plan["observed"]
            or plan["observed"][template.metadata.id]["kind"] != request_types()[request.type][3]
        ):
            raise ValueError("Provisioning update requires an exact versioned resource")
    return requests


class ProvisioningJournal(Journal):
    schema = "nebius-vpngw.provisioning.v1"

    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        super().__init__(
            project,
            gateway,
            root=root or Path.home() / ".local/state/nebius-vpngw/provisioning" / project / gateway,
        )

    def validate(self, state: dict) -> None:
        metadata_only(state)
        try:
            if (
                set(state)
                != {"schema", "project_id", "gateway_name", "revision", "bindings", "transaction"}
                or state["schema"] != self.schema
                or state["project_id"] != self.project
                or state["gateway_name"] != self.gateway
                or type(state["revision"]) is not int
                or state["revision"] < 0
            ):
                raise ValueError("Invalid provisioning journal identity")
            transaction = state["transaction"]
            if transaction is None:
                if state["bindings"]:
                    raise ValueError("Creation receipts have no approved provisioning")
                return
            if set(transaction) != {
                "plan",
                "approval",
                "execution_id",
                "phase",
                "member",
                "operations",
                "verified",
                "request_digests",
            }:
                raise ValueError("Invalid provisioning transaction")
            requests = requests_from_plan(transaction["plan"])
            if (
                transaction["plan"]["project_id"] != self.project
                or transaction["plan"]["gateway_name"] != self.gateway
                or re.fullmatch(r"[a-f0-9]{64}", transaction["approval"]) is None
                or re.fullmatch(r"[a-f0-9]{32}", transaction["execution_id"]) is None
                or transaction["phase"] not in {"pending", "complete"}
                or transaction["member"] != 0
            ):
                raise ValueError("Invalid provisioning authority")
            keys = [r.key for r in requests]
            verified = transaction["verified"]
            if verified != keys[: len(verified)] or not set(transaction["request_digests"]) <= set(
                keys
            ):
                raise ValueError("Provisioning effects are not ordered")
            if any(
                not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
                for value in transaction["request_digests"].values()
            ):
                raise ValueError("Provisioning request identity is invalid")
            operations = transaction["operations"]
            expected_bindings = {}
            for key, record in operations.items():
                label = record["label"]
                if (
                    label not in keys
                    or key
                    != effect_key(transaction["approval"], 0, label, transaction["execution_id"])
                    or record["request_sha256"] != transaction["request_digests"].get(label)
                    or record["status"]
                    not in {"intent", "accepted", "settled", "rejected", "failed"}
                    or keys.index(label) > len(verified)
                    or record["status"] in {"accepted", "settled", "failed"}
                    and not record["cloud_operation_id"]
                ):
                    raise ValueError("Cloud receipt exceeds approved provisioning scope")
                if label in verified:
                    if record["status"] != "settled":
                        raise ValueError("Verified provisioning effect is unsettled")
                    recipe = requests[keys.index(label)]
                    _, _, method, kind = request_types()[recipe.type]
                    if method == "create":
                        if not record["resource_id"]:
                            raise ValueError("Created resource has no authoritative identity")
                        expected_bindings[label] = {
                            "kind": kind,
                            "resource_id": record["resource_id"],
                            "cloud_operation_id": record["cloud_operation_id"],
                            "request_sha256": record["request_sha256"],
                        }
            if state["bindings"] != expected_bindings or len(operations) < len(verified):
                raise ValueError("Creation bindings differ from successful operation receipts")
            if transaction["phase"] == "complete" and verified != keys:
                raise ValueError("Provisioning has unverified effects")
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise ValueError("Provisioning journal is invalid") from error

    def begin(self, plan: dict, *, approval: str, execution_id: str) -> None:
        requests_from_plan(plan)
        current = self.state["transaction"]
        if current:
            if (
                current["plan"] == plan
                and current["approval"] == approval
                and current["execution_id"] == execution_id
            ):
                return
            if current["phase"] != "complete" or current["execution_id"] == execution_id:
                raise RuntimeError("Provisioning requires its original command and intent")
            self.archive()
            self.state["bindings"] = {}
        self.state["transaction"] = {
            "plan": json.loads(json.dumps(plan)),
            "approval": approval,
            "execution_id": execution_id,
            "phase": "pending",
            "member": 0,
            "operations": {},
            "verified": [],
            "request_digests": {},
        }
        self.save()

    def stage(self, recipe: Request, request: Any) -> None:
        transaction = self.state["transaction"]
        recipes = requests_from_plan(transaction["plan"])
        if recipe not in recipes or recipes.index(recipe) > len(transaction["verified"]):
            raise RuntimeError("Provisioning prerequisites are not verified")
        expected = recipe.resolve(
            self.state["bindings"],
            bootstrap=request.spec.cloud_init_user_data if recipe.guest is not None else None,
        )
        signature = message_digest(request)
        if signature != message_digest(expected):
            raise RuntimeError("Resolved provisioning request exceeds approved intent")
        previous = transaction["request_digests"].get(recipe.key)
        if previous is not None:
            if previous != signature:
                raise RuntimeError("Resume requires the exact original provisioning request")
            return
        transaction["request_digests"][recipe.key] = signature
        self.save()

    def verified(self, recipe: Request, request: Any) -> None:
        transaction = self.state["transaction"]
        key = effect_key(transaction["approval"], 0, recipe.key, transaction["execution_id"])
        record = transaction["operations"][key]
        if record["status"] != "settled" or record["request_sha256"] != message_digest(request):
            raise RuntimeError("Provisioning effect is not exactly settled")
        if recipe.key in transaction["verified"]:
            return
        if request_types()[recipe.type][2] == "create":
            self.state["bindings"][recipe.key] = creation_receipt(recipe, record, request)
        transaction["verified"].append(recipe.key)
        self.save()

    def complete(self) -> dict:
        transaction = self.state["transaction"]
        if transaction["verified"] != [
            recipe.key for recipe in requests_from_plan(transaction["plan"])
        ]:
            raise RuntimeError("Provisioning has unverified effects")
        if transaction["phase"] != "complete":
            transaction["phase"] = "complete"
            self.save()
        return {"plan": digest(transaction["plan"]), "bindings": digest(self.state["bindings"])}
