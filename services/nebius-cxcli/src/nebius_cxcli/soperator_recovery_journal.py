"""Cluster-authoritative recovery journal for protected Soperator mutations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .soperator_receipt_io import write_owner_only_json

SOPERATOR_RECOVERY_JOURNAL_SCHEMA = "nebius-cxcli.soperator-recovery-journal.v4"
_STAGE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_IMMUTABLE_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")


class RecoveryCommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


RecoveryCommandRunner = Callable[..., RecoveryCommandResult]


@dataclass(frozen=True)
class SoperatorRecoveryIdentity:
    operation_id: str
    cluster_id: str
    kubernetes_uid: str
    lease_uid: str
    fencing_epoch: int
    source_release: str
    target_release: str
    target_jail_image: str
    infrastructure_receipt_sha256: str
    rootfs_classification_sha256: str

    def as_payload(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "clusterId": self.cluster_id,
            "kubernetesUid": self.kubernetes_uid,
            "leaseUid": self.lease_uid,
            "fencingEpoch": self.fencing_epoch,
            "sourceRelease": self.source_release,
            "targetRelease": self.target_release,
            "targetJailImage": self.target_jail_image,
            "infrastructureReceiptSha256": self.infrastructure_receipt_sha256,
            "rootfsClassificationSha256": self.rootfs_classification_sha256,
        }


class SoperatorRecoveryJournal:
    """CAS-updated ConfigMap journal with an owner-only local mirror."""

    def __init__(
        self,
        *,
        runner: RecoveryCommandRunner,
        kube_context: str,
        identity: SoperatorRecoveryIdentity,
        local_path: Path,
        assert_authority: Callable[[], object],
    ) -> None:
        self._runner = runner
        self._kube_context = str(kube_context or "").strip()
        self.identity = identity
        self.local_path = local_path
        self._assert_authority = assert_authority
        token = hashlib.sha256(identity.operation_id.encode("utf-8")).hexdigest()[:20]
        self.name = f"nebius-cxcli-soperator-recovery-{token}"
        if (
            not self._kube_context
            or identity.fencing_epoch < 1
            or not _IMMUTABLE_IMAGE.fullmatch(identity.target_jail_image)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity.infrastructure_receipt_sha256)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity.rootfs_classification_sha256)
        ):
            raise ValueError("Soperator recovery journal identity is incomplete")

    def _command(self, *args: str) -> Sequence[str]:
        return (
            "kubectl",
            "--context",
            self._kube_context,
            "--namespace",
            "kube-system",
            *args,
        )

    def _run(
        self,
        *args: str,
        input_text: str | None = None,
    ) -> RecoveryCommandResult:
        return self._runner(
            self._command(*args),
            timeout_seconds=60,
            check=False,
            **({"input_text": input_text} if input_text is not None else {}),
        )

    def _read(self) -> tuple[dict[str, object], str] | None:
        result = self._run("get", "configmap", self.name, "-o", "json")
        if result.returncode != 0:
            detail = f"{result.stdout}\n{result.stderr}".lower()
            if "notfound" in detail or "not found" in detail:
                return None
            raise RuntimeError("Unable to read the Soperator recovery journal")
        try:
            resource = json.loads(result.stdout or "{}")
            data = resource.get("data") if isinstance(resource, Mapping) else None
            payload = (
                json.loads(str(data.get("journal.json") or ""))
                if isinstance(data, Mapping)
                else None
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator recovery journal returned invalid JSON") from exc
        metadata = resource.get("metadata") if isinstance(resource, Mapping) else None
        resource_version = (
            str(metadata.get("resourceVersion") or "").strip()
            if isinstance(metadata, Mapping)
            else ""
        )
        if not isinstance(payload, dict) or not resource_version:
            raise RuntimeError("Soperator recovery journal is incomplete")
        self._validate(payload)
        return payload, resource_version

    def _validate(self, payload: Mapping[str, object]) -> None:
        if payload.get("schema") != SOPERATOR_RECOVERY_JOURNAL_SCHEMA:
            raise RuntimeError("Soperator recovery journal has an unsupported schema")
        expected = self.identity.as_payload()
        immutable = (
            "operationId",
            "clusterId",
            "kubernetesUid",
            "sourceRelease",
            "targetRelease",
            "targetJailImage",
            "infrastructureReceiptSha256",
            "rootfsClassificationSha256",
        )
        if any(payload.get(key) != expected[key] for key in immutable):
            raise RuntimeError("recovery-required: Soperator recovery identity changed")
        try:
            recorded_epoch = int(payload.get("fencingEpoch") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Soperator recovery fencing epoch is malformed") from exc
        if recorded_epoch < 1 or recorded_epoch > self.identity.fencing_epoch:
            raise RuntimeError("recovery-required: Soperator recovery fencing epoch is foreign")
        if not isinstance(payload.get("stages"), Mapping):
            raise RuntimeError("Soperator recovery journal has no stage map")

    def _manifest(
        self,
        payload: Mapping[str, object],
        *,
        resource_version: str = "",
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "name": self.name,
            "namespace": "kube-system",
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "app.kubernetes.io/part-of": "soperator",
                "nebius-cxcli/fence-epoch": str(self.identity.fencing_epoch),
            },
        }
        if resource_version:
            metadata["resourceVersion"] = resource_version
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata,
            "data": {"journal.json": json.dumps(payload, sort_keys=True, separators=(",", ":"))},
        }

    def _mirror(self, payload: Mapping[str, object]) -> None:
        write_owner_only_json(self.local_path, payload)

    def establish(self) -> dict[str, object]:
        self._assert_authority()
        current = self._read()
        if current is not None:
            payload, resource_version = current
            if (
                payload.get("leaseUid") != self.identity.lease_uid
                or payload.get("fencingEpoch") != self.identity.fencing_epoch
            ):
                payload["leaseUid"] = self.identity.lease_uid
                payload["fencingEpoch"] = self.identity.fencing_epoch
                self._replace(payload, resource_version=resource_version)
            self._mirror(payload)
            return payload
        payload: dict[str, object] = {
            "schema": SOPERATOR_RECOVERY_JOURNAL_SCHEMA,
            **self.identity.as_payload(),
            "status": "active",
            "stages": {},
        }
        result = self._run(
            "create",
            "-f",
            "-",
            input_text=json.dumps(self._manifest(payload), sort_keys=True),
        )
        if result.returncode != 0:
            raise RuntimeError("Unable to establish the Soperator recovery journal")
        self._mirror(payload)
        return payload

    def snapshot(self) -> dict[str, object]:
        """Read the cluster-authoritative journal without rewriting its local mirror."""

        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        return current[0]

    def _replace(self, payload: Mapping[str, object], *, resource_version: str) -> None:
        self._assert_authority()
        result = self._run(
            "replace",
            "-f",
            "-",
            input_text=json.dumps(
                self._manifest(payload, resource_version=resource_version),
                sort_keys=True,
            ),
        )
        if result.returncode != 0:
            raise RuntimeError("Lost the Soperator recovery journal CAS authority")
        self._mirror(payload)

    def stage(self, name: str) -> Mapping[str, object] | None:
        if not _STAGE_NAME.fullmatch(name):
            raise ValueError("Soperator recovery stage name is invalid")
        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        stages = current[0].get("stages")
        stage = stages.get(name) if isinstance(stages, Mapping) else None
        return stage if isinstance(stage, Mapping) else None

    def begin_stage(self, *, name: str, intent: Mapping[str, object]) -> Mapping[str, object]:
        if not _STAGE_NAME.fullmatch(name):
            raise ValueError("Soperator recovery stage name is invalid")
        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        payload, resource_version = current
        stages = payload.get("stages")
        if not isinstance(stages, dict):
            raise RuntimeError("Soperator recovery journal stage map is malformed")
        existing = stages.get(name)
        if isinstance(existing, Mapping):
            if existing.get("intent") != dict(intent):
                raise RuntimeError("recovery-required: Soperator stage intent changed")
            return existing
        stage = {"status": "intent", "intent": dict(intent), "attempts": 1}
        stages[name] = stage
        self._replace(payload, resource_version=resource_version)
        return stage

    def complete_stage(
        self,
        *,
        name: str,
        evidence: Mapping[str, object],
        disposition: str = "applied",
    ) -> Mapping[str, object]:
        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        payload, resource_version = current
        stages = payload.get("stages")
        stage = stages.get(name) if isinstance(stages, dict) else None
        if not isinstance(stage, dict) or stage.get("status") != "intent":
            raise RuntimeError("Soperator recovery stage has no write-ahead intent")
        stage["status"] = "complete"
        stage["disposition"] = disposition
        stage["evidence"] = dict(evidence)
        self._replace(payload, resource_version=resource_version)
        return stage

    def seal(self) -> Mapping[str, object]:
        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        payload, resource_version = current
        stages = payload.get("stages")
        if not isinstance(stages, Mapping) or any(
            not isinstance(stage, Mapping) or stage.get("status") != "complete"
            for stage in stages.values()
        ):
            raise RuntimeError("Soperator recovery journal has unfinished stages")
        payload["status"] = "complete"
        self._replace(payload, resource_version=resource_version)
        return payload

    def begin_safe_replay_supersession(
        self,
        *,
        predecessor_operation_id: str,
        discarded_replay_receipt_sha256: str,
        workload_name: str,
        workload_uid: str,
        workload_resource_version: str,
        workload_sha256: str,
        pod_identity_sha256: str,
    ) -> Mapping[str, object]:
        """Write ahead the exact read-only replay workload selected for removal."""

        if (
            not re.fullmatch(r"sha256:[0-9a-f]{64}", predecessor_operation_id)
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", discarded_replay_receipt_sha256
            )
            or not str(workload_name or "").strip()
            or not str(workload_uid or "").strip()
            or not str(workload_resource_version or "").strip()
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", workload_sha256)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", pod_identity_sha256)
        ):
            raise ValueError("Soperator superseded replay evidence is incomplete")
        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        payload, resource_version = current
        stages = payload.get("stages")
        expected_stages = {
            "rootfs-admission-decision",
            "rootfs-passive-target-identity",
        }
        if (
            payload.get("status") != "active"
            or not isinstance(stages, Mapping)
            or set(stages) != expected_stages
            or any(
                not isinstance(stage, Mapping) or stage.get("status") != "complete"
                for stage in stages.values()
            )
        ):
            raise RuntimeError(
                "recovery-required: Soperator replay journal crossed the safe inventory boundary"
            )
        intent = {
            "predecessorOperationId": predecessor_operation_id,
            "discardedReplayReceiptSha256": discarded_replay_receipt_sha256,
            "workloadName": workload_name,
            "workloadUid": workload_uid,
            "workloadResourceVersion": workload_resource_version,
            "workloadSha256": workload_sha256,
            "podIdentitySha256": pod_identity_sha256,
        }
        existing = payload.get("supersededReplayIntent")
        if existing is not None:
            if existing != intent:
                raise RuntimeError(
                    "recovery-required: Soperator replay supersession intent changed"
                )
            return payload
        payload["supersededReplayIntent"] = intent
        self._replace(payload, resource_version=resource_version)
        return payload

    def supersede_safe_replay(self) -> Mapping[str, object]:
        """Seal an empty pre-fix replay after its selected Job is confirmed absent."""

        current = self._read()
        if current is None:
            raise RuntimeError("Soperator recovery journal is missing")
        payload, resource_version = current
        intent = payload.get("supersededReplayIntent")
        if payload.get("status") != "active" or not isinstance(intent, Mapping):
            raise RuntimeError(
                "recovery-required: Soperator replay supersession has no write-ahead intent"
            )
        payload["status"] = "superseded-safe-replay"
        payload["supersededReplay"] = dict(intent)
        self._replace(payload, resource_version=resource_version)
        return payload


__all__ = [
    "SOPERATOR_RECOVERY_JOURNAL_SCHEMA",
    "SoperatorRecoveryIdentity",
    "SoperatorRecoveryJournal",
]
