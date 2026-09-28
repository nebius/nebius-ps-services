"""Admission and incarnation proofs for resources recreated by later stages."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .deployment_plan import terraform_admission, terraform_changes
from .deployment_state import digest


def resource_identity(values: Any) -> str:
    if not isinstance(values, Mapping):
        return ""
    metadata = values.get("metadata", {})
    if isinstance(metadata, list):
        metadata = metadata[0] if len(metadata) == 1 else {}
    identity = metadata.get("id") if isinstance(metadata, Mapping) else None
    identity = identity or values.get("id")
    return digest(str(identity)) if identity else ""


@dataclass(frozen=True)
class PriorDeletion:
    stage: str
    admission_digest: str
    address: str
    resource_type: str
    original_identity_digest: str

    def as_payload(self) -> dict[str, str]:
        return asdict(self)


def prior_deletions(stage: str, inventory: Mapping[str, Any]) -> tuple[PriorDeletion, ...]:
    result = []
    admission = digest(terraform_admission(inventory))
    for row in terraform_changes(inventory):
        if row["change"]["actions"] != ["delete"]:
            continue
        identity = row.get("beforeIdentity", "")
        if not identity:
            raise ValueError("Retirement admission requires the original resource identity")
        result.append(PriorDeletion(stage, admission, row["address"], row["type"], identity))
    return tuple(result)


def recreation_candidates(
    inventory: Mapping[str, Any], deleted: Sequence[PriorDeletion]
) -> tuple[PriorDeletion, ...]:
    desired = {
        row["address"]: row["type"]
        for row in inventory.get("resource_changes", [])
        if row.get("mode", "managed") == "managed" and row["change"]["actions"] != ["delete"]
    }
    return tuple(item for item in deleted if desired.get(item.address) == item.resource_type)


def admit_recreations(
    diagnostic: Mapping[str, Any],
    baseline: Mapping[str, Any],
    prerequisites: Sequence[PriorDeletion],
) -> dict[str, Any]:
    """Normalize only prior-deleted instances; never widen survivor destruction."""
    allowed = {item.address: item for item in prerequisites}
    original = {row["address"]: row for row in terraform_changes(baseline)}
    result = copy.deepcopy(dict(diagnostic))
    found = set()
    for row in terraform_changes(result):
        actions = row["change"]["actions"]
        prior = allowed.get(row["address"])
        if prior:
            if row["type"] != prior.resource_type or actions not in (
                ["delete", "create"],
                ["create", "delete"],
                ["create"],
            ):
                raise ValueError("Diagnostic plan did not recreate the admitted resource")
            row["change"]["actions"] = ["create"]
            found.add(row["address"])
        elif "delete" in actions:
            survivor = original.get(row["address"])
            if survivor is None or survivor["change"]["actions"] != actions:
                raise ValueError("Diagnostic plan introduces survivor destruction")
    if found != set(allowed):
        raise ValueError("Diagnostic plan is missing a required recreation")
    return terraform_admission(result)


def validate_dependency_journal(payload: Any) -> None:
    """Allow only frozen deletion references and replacement identity digests."""

    def sha(value: Any) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))

    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "intent", "deleted", "creating", "replacements"}
        or payload["schema"] != "nebius-cxcli.deployment-dependencies.v1"
        or not sha(payload["intent"])
        or any(
            not isinstance(payload[key], dict) for key in ("deleted", "creating", "replacements")
        )
    ):
        raise RuntimeError("Invalid support-safe dependency journal")
    for key, value in payload["deleted"].items():
        if (
            not isinstance(value, dict)
            or set(value)
            != {"stage", "admission_digest", "address", "resource_type", "original_identity_digest"}
            or key != digest(value)
            or not sha(value["admission_digest"])
            or not sha(value["original_identity_digest"])
            or value["stage"] not in {"shrink", "retire", "grow", "reconcile"}
            or not isinstance(value["address"], str)
            or not re.fullmatch(r'[A-Za-z0-9_.\[\]"-]{1,1024}', value["address"])
            or not isinstance(value["resource_type"], str)
            or not re.fullmatch(r"[a-z0-9_]{1,256}", value["resource_type"])
        ):
            raise RuntimeError("Invalid support-safe deletion reference")
    if (
        not set(payload["creating"]) <= set(payload["deleted"])
        or any(value is not True for value in payload["creating"].values())
        or not set(payload["replacements"]) <= set(payload["creating"])
        or any(not sha(value) for value in payload["replacements"].values())
    ):
        raise RuntimeError("Invalid support-safe replacement evidence")


class DependencyJournal:
    """Prove original deletion separately from later address reuse."""

    def __init__(self, path: Any, intent: str) -> None:
        from .soperator_receipt_io import read_owner_only_json

        self.path = path
        payload = (
            read_owner_only_json(path, label="Deployment dependencies")
            if path.exists()
            else {
                "schema": "nebius-cxcli.deployment-dependencies.v1",
                "intent": intent,
                "deleted": {},
                "creating": {},
                "replacements": {},
            }
        )
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "nebius-cxcli.deployment-dependencies.v1"
            or payload.get("intent") != intent
            or any(
                not isinstance(payload.get(key), dict)
                for key in ("deleted", "creating", "replacements")
            )
        ):
            raise RuntimeError("Deployment dependency journal belongs to another intent")

        validate_dependency_journal(payload)
        self.payload: dict[str, Any] = payload

    def _save(self) -> None:
        from .soperator_receipt_io import write_owner_only_json

        validate_dependency_journal(self.payload)
        write_owner_only_json(self.path, self.payload)

    def deleted(self, references: Sequence[PriorDeletion], resources: Mapping[str, str]) -> None:
        for item in references:
            key = digest(item.as_payload())
            if item.address in resources:
                raise RuntimeError("Retirement has not proved resource deletion")
            self.payload["deleted"][key] = item.as_payload()
        if references:
            self._save()

    def require(self, references: Sequence[PriorDeletion], resources: Mapping[str, str]) -> None:
        for item in references:
            key = digest(item.as_payload())
            if self.payload["deleted"].get(key) != item.as_payload():
                raise RuntimeError("Missing predecessor deletion evidence")
            current = resources.get(item.address)
            if current == item.original_identity_digest:
                raise RuntimeError("Original retired resource identity reappeared")
            if current is not None:
                bound = self.payload["replacements"].get(key)
                if not current or (bound is not None and bound != current):
                    raise RuntimeError("Recreated resource identity changed")
                if self.payload["creating"].get(key) is not True:
                    raise RuntimeError("Resource appeared before admitted creation")
                self.payload["replacements"][key] = current
            elif key in self.payload["replacements"]:
                raise RuntimeError("Previously recreated resource is absent")
        if references:
            self._save()

    def begin_creation(self, references: Sequence[PriorDeletion]) -> None:
        for item in references:
            self.payload["creating"][digest(item.as_payload())] = True
        if references:
            self._save()
