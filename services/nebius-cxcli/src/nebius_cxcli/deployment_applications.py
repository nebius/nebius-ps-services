"""Support-safe per-target progress beneath the shared deployment fence."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .deployment_state import digest
from .paths import ProjectPaths
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json


def _valid_identity(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"cluster_id", "kubernetes_uid"}
        and all(
            isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,256}", item)
            for item in value.values()
        )
    )


def recorded_application_identity(
    record: Mapping[str, Any], *, paths: ProjectPaths, target_ref: str
) -> dict[str, str] | None:
    """Project authenticated remote identity without restoring an execution cache."""
    accepted = (record.get("accepted") or {}).get("evidence", {}).get("identities", {})
    previous = accepted.get(target_ref)
    if previous is not None and not _valid_identity(previous):
        raise RuntimeError("Invalid accepted application identity")
    current = None
    active = record.get("active")
    if active:
        key = (
            f"{paths.path_tenant_folder}/{paths.path_project_folder}"
            "/generated/reports/deployment-applications.json"
        )
        recovery = active.get("recovery", {})
        if not isinstance(recovery, Mapping):
            raise RuntimeError("Invalid application recovery archive")
        if key in recovery:
            encoded = recovery[key]
            if not isinstance(encoded, str):
                raise RuntimeError("Invalid application recovery journal encoding")
            try:
                journal = json.loads(base64.b64decode(encoded, validate=True))
            except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
                raise RuntimeError("Invalid application recovery journal encoding") from exc
            validate_application_journal(journal)
            if journal["generation"] != active.get("generation") or journal[
                "selected"
            ] != active.get("plan", {}).get("semanticPlan", {}).get("selectedTargets"):
                raise RuntimeError("Application recovery differs from its frozen target selection")
            current = journal["targets"].get(target_ref, {}).get("identity")
    if previous is not None and current is not None and previous != current:
        raise RuntimeError("Active and accepted application identities conflict")
    identity = current if current is not None else previous
    return dict(identity) if identity is not None else None


def validate_application_journal(payload: Any) -> None:
    """Only identity, progress and digest evidence may cross runner boundaries."""

    def sha(value: Any) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))

    def invalid() -> None:
        raise RuntimeError("Invalid support-safe application journal")

    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "generation",
        "selected",
        "targets",
    }:
        invalid()
    if payload["schema"] != "nebius-cxcli.deployment-applications.v1" or not sha(
        payload["generation"]
    ):
        invalid()
    selected, targets = payload["selected"], payload["targets"]
    if (
        not isinstance(selected, list)
        or not all(
            isinstance(ref, str) and re.fullmatch(r"[a-z0-9-]{1,63}", ref) for ref in selected
        )
        or len(set(selected)) != len(selected)
    ):
        invalid()
    if not isinstance(targets, dict) or not set(targets) <= set(selected):
        invalid()
    for entry in targets.values():
        if (
            not isinstance(entry, dict)
            or not {"identity", "desiredBundle", "status"} <= set(entry)
            or not set(entry) <= {"identity", "desiredBundle", "status", "evidence", "inputRepair"}
        ):
            invalid()
        if (
            not _valid_identity(entry["identity"])
            or not sha(entry["desiredBundle"])
            or entry["status"] not in {"executing", "complete"}
        ):
            invalid()
        repair = entry.get("inputRepair")
        if repair is not None and (
            not isinstance(repair, dict)
            or set(repair) != {"reason", "previousBundle", "admissionSha256"}
            or repair["reason"]
            not in {
                "install-worker-docker-storage-v1",
                "install-native-observability-v1",
                "install-dashboard-source-delivery-v1",
            }
            or not sha(repair["previousBundle"])
            or not sha(repair["admissionSha256"])
            or repair["previousBundle"] == entry["desiredBundle"]
        ):
            invalid()
        proof = entry.get("evidence")
        if proof is None and entry["status"] == "executing":
            continue
        if (
            not isinstance(proof, dict)
            or not {"desiredBundle", "resources", "ready", "identity", "validations"} <= set(proof)
            or not set(proof)
            <= {
                "desiredBundle",
                "resources",
                "ready",
                "identity",
                "validations",
                "operationCompletion",
            }
            or proof["desiredBundle"] != entry["desiredBundle"]
            or proof["identity"] != entry["identity"]
            or proof["ready"] is not True
            or proof["validations"] != "passed"
        ):
            invalid()
        completion = proof.get("operationCompletion")
        if completion is not None and (
            not isinstance(completion, dict)
            or completion.get("schema") != "nebius-cxcli.operation-completion.v1"
            or completion.get("desiredBundle") != proof["desiredBundle"]
            or completion.get("receiptSha256")
            != digest({k: v for k, v in completion.items() if k != "receiptSha256"})
            or completion.get("data", {}).get("status") != "complete"
        ):
            invalid()
        if not isinstance(proof["resources"], dict):
            invalid()
        for key, value in proof["resources"].items():
            if (
                not isinstance(key, str)
                or len(key) > 1024
                or not isinstance(value, dict)
                or set(value) != {"uid", "desiredSha256", "observedSha256"}
                or not isinstance(value["uid"], str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", value["uid"])
                or not sha(value["desiredSha256"])
                or not sha(value["observedSha256"])
            ):
                invalid()


class ApplicationJournal:
    def __init__(self, path: Path, *, generation: str, selected: Sequence[str]) -> None:
        self.path = path
        payload = (
            read_owner_only_json(path, label="Deployment applications")
            if path.exists()
            else {
                "schema": "nebius-cxcli.deployment-applications.v1",
                "generation": generation,
                "selected": list(selected),
                "targets": {},
            }
        )
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "nebius-cxcli.deployment-applications.v1"
            or payload.get("generation") != generation
            or payload.get("selected") != list(selected)
            or not isinstance(payload.get("targets"), dict)
            or not set(payload["targets"]) <= set(selected)
        ):
            raise RuntimeError("Application recovery differs from its frozen target selection")

        validate_application_journal(payload)

        self.payload: dict[str, Any] = payload

    def entry(self, ref: str) -> Mapping[str, Any]:
        return self.payload["targets"].get(ref, {})

    def bind(self, ref: str, identity: Mapping[str, str], desired: str) -> None:
        if (
            ref not in self.payload["selected"]
            or set(identity) != {"cluster_id", "kubernetes_uid"}
            or not all(identity.values())
        ):
            raise RuntimeError("Application target binding is incomplete")
        previous = self.entry(ref)
        if previous and (
            previous.get("identity") != dict(identity) or previous.get("desiredBundle") != desired
        ):
            raise RuntimeError(
                "Application target differs from its checkpointed identity or bundle"
            )
        self.payload["targets"][ref] = {
            **previous,
            "identity": dict(identity),
            "desiredBundle": desired,
            "status": "executing",
        }
        self._save()

    def complete(self, ref: str, evidence: Mapping[str, Any]) -> None:
        previous = self.entry(ref)
        if not previous or previous["desiredBundle"] != evidence["desiredBundle"]:
            raise RuntimeError("Application completion differs from its frozen bundle")
        self.payload["targets"][ref] = {
            **previous,
            "status": "complete",
            "evidence": dict(evidence),
        }
        self._save()

    def repair_inputs(
        self,
        ref: str,
        *,
        identity: Mapping[str, str],
        previous: str,
        desired: str,
        admission_sha256: str,
        reason: str = "install-worker-docker-storage-v1",
    ) -> None:
        """Record a separately authenticated exact install repair, retaining its predecessor."""
        entry = self.entry(ref)
        if (
            entry.get("identity") != dict(identity)
            or entry.get("desiredBundle") != previous
            or entry.get("status") != "executing"
            or "inputRepair" in entry
            or "evidence" in entry
            or desired == previous
        ):
            raise RuntimeError("Application input repair lost its executing predecessor")
        self.payload["targets"][ref] = {
            **entry,
            "desiredBundle": desired,
            "inputRepair": {
                "reason": reason,
                "previousBundle": previous,
                "admissionSha256": admission_sha256,
            },
        }
        self._save()

    def _save(self) -> None:
        validate_application_journal(self.payload)
        write_owner_only_json(self.path, self.payload)


def target_bundle_digest(generation: Any, target_ref: str) -> str:
    from .deployment_observation import target_documents

    return digest(target_documents(generation, target_ref))
