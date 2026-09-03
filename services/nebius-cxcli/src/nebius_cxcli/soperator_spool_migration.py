"""Checkpointed bridge from legacy controller spool templates to volume sources."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .soperator_failures import SoperatorSafetyPauseError

SOPERATOR_CONTROLLER_SPOOL_MIGRATION_SCHEMA = "nebius-cxcli.soperator-controller-spool-migration.v1"

_SOPERATOR_NAMESPACE = "soperator"
_SOPERATOR_CLUSTER_NAME = "soperator"
_SOPERATOR_CONTROLLER_STATEFULSET = "controller"
_SOPERATOR_CONTROLLER_POD = "controller-0"
_SOPERATOR_CONTROLLER_NAMESPACE = "soperator-system"
_SOPERATOR_CONTROLLER_DEPLOYMENT = "soperator-controller-manager"
_SOPERATOR_CONTROLLER_WEBHOOK_SERVICE = "soperator-controller-webhook-service"
_KRUISE_STATEFULSET_RESOURCE = "statefulsets.apps.kruise.io"
_LEGACY_SPOOL_TEMPLATE = "controller-spool"
_TARGET_SPOOL_VOLUME_SOURCE = "controller-spool"
_TARGET_SPOOL_MOUNT_GATE = "mount-gate-controller-spool"
_KRUISE_SPECIFIED_DELETE_LABEL = "apps.kruise.io/specified-delete"


class MigrationCommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


MigrationCommandRunner = Callable[..., MigrationCommandResult]


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object, *, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class _ObservedMigrationIdentity:
    deployment_uid: str
    original_replicas: int
    slurm_cluster_uid: str
    statefulset_uid: str
    source_pvc_name: str
    source_pvc_uid: str
    source_pv_name: str
    source_pv_uid: str
    target_pvc_uid: str

    def as_payload(self) -> dict[str, object]:
        return {
            "deploymentUid": self.deployment_uid,
            "originalReplicas": self.original_replicas,
            "slurmClusterUid": self.slurm_cluster_uid,
            "statefulSetUid": self.statefulset_uid,
            "sourcePvcName": self.source_pvc_name,
            "sourcePvcUid": self.source_pvc_uid,
            "sourcePvName": self.source_pv_name,
            "sourcePvUid": self.source_pv_uid,
            "targetPvcUid": self.target_pvc_uid,
        }


class SoperatorControllerSpoolMigration:
    """Run and recover one exact non-deleting Kruise storage-shape migration."""

    def __init__(
        self,
        *,
        runner: MigrationCommandRunner,
        kube_context: str,
        operation_id: str,
        target_release: str,
        target_pvc_name: str,
        assert_authority: Callable[[], object],
        read_state: Callable[[], Mapping[str, object] | None],
        write_state: Callable[[Mapping[str, object]], None],
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._runner = runner
        self._kube_context = _text(kube_context)
        self._operation_id = _text(operation_id)
        self._target_release = _text(target_release)
        self._target_pvc_name = _text(target_pvc_name)
        self._assert_authority = assert_authority
        self._read_state = read_state
        self._write_state = write_state
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        if (
            not self._kube_context
            or not self._operation_id.startswith("sha256:")
            or not self._target_release
            or not self._target_pvc_name
            or timeout_seconds < 1
            or poll_interval_seconds <= 0
        ):
            raise ValueError("Soperator controller spool migration identity is incomplete")

    def _command(self, *args: str) -> tuple[str, ...]:
        return ("kubectl", "--context", self._kube_context, *args)

    def _run_json(self, *args: str, input_text: str | None = None) -> dict[str, object]:
        result = self._runner(
            self._command(*args),
            timeout_seconds=60,
            check=False,
            **({"input_text": input_text} if input_text is not None else {}),
        )
        if result.returncode != 0:
            detail = " ".join((result.stderr or "").split())[:400]
            if re.search(
                r"(?i)(authorization|certificate|cookie|credential|password|private.?key|secret|token)",
                detail,
            ):
                detail = "details redacted"
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Soperator controller spool migration Kubernetes request failed{suffix}"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Soperator controller spool migration returned invalid Kubernetes JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Soperator controller spool migration response is malformed")
        return payload

    def _get(self, *args: str) -> dict[str, object]:
        return self._run_json(*args, "-o", "json")

    def _patch(
        self,
        *,
        namespace: str | None,
        resource: str,
        name: str,
        operations: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        self._assert_authority()
        args: list[str] = []
        if namespace is not None:
            args.extend(("--namespace", namespace))
        args.extend(
            (
                "patch",
                resource,
                name,
                "--type=json",
                "-p",
                json.dumps(list(operations), sort_keys=True, separators=(",", ":")),
                "-o",
                "json",
            )
        )
        return self._run_json(*args)

    @staticmethod
    def _metadata(payload: Mapping[str, object], *, label: str) -> Mapping[str, object]:
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise RuntimeError(f"{label} metadata is unavailable")
        if not _text(metadata.get("uid")) or not _text(metadata.get("resourceVersion")):
            raise RuntimeError(f"{label} identity is incomplete")
        return metadata

    def _deployment(self) -> dict[str, object]:
        return self._get(
            "--namespace",
            _SOPERATOR_CONTROLLER_NAMESPACE,
            "get",
            "deployment",
            _SOPERATOR_CONTROLLER_DEPLOYMENT,
        )

    def _webhook_endpoints(self) -> dict[str, object]:
        return self._get(
            "--namespace",
            _SOPERATOR_CONTROLLER_NAMESPACE,
            "get",
            "endpoints",
            _SOPERATOR_CONTROLLER_WEBHOOK_SERVICE,
        )

    def _statefulset(self) -> dict[str, object]:
        return self._get(
            "--namespace",
            _SOPERATOR_NAMESPACE,
            "get",
            _KRUISE_STATEFULSET_RESOURCE,
            _SOPERATOR_CONTROLLER_STATEFULSET,
        )

    def _slurm_cluster(self) -> dict[str, object]:
        return self._get(
            "--namespace",
            _SOPERATOR_NAMESPACE,
            "get",
            "slurmcluster.slurm.nebius.ai",
            _SOPERATOR_CLUSTER_NAME,
        )

    def _pvc(self, name: str) -> dict[str, object]:
        return self._get("--namespace", _SOPERATOR_NAMESPACE, "get", "pvc", name)

    def _pv(self, name: str) -> dict[str, object]:
        return self._get("get", "pv", name)

    @staticmethod
    def _volume_templates(statefulset: Mapping[str, object]) -> list[Mapping[str, object]]:
        templates = _mapping(statefulset.get("spec")).get("volumeClaimTemplates")
        if templates is None:
            return []
        if not isinstance(templates, list) or any(
            not isinstance(item, Mapping) for item in templates
        ):
            raise RuntimeError("legacy controller volume claim templates are malformed")
        return list(templates)

    def _observe_identity(self, *, allow_migrated: bool) -> _ObservedMigrationIdentity:
        deployment = self._deployment()
        deployment_metadata = self._metadata(deployment, label="Soperator controller Deployment")
        deployment_labels = _mapping(deployment_metadata.get("labels"))
        if (
            _text(deployment_labels.get("app.kubernetes.io/version")) != self._target_release
            or _text(deployment_labels.get("app.kubernetes.io/managed-by")) != "Helm"
        ):
            raise SoperatorSafetyPauseError(
                "target Soperator controller Deployment ownership is not exact"
            )
        deployment_spec = _mapping(deployment.get("spec"))
        observed_replicas = _integer(deployment_spec.get("replicas"), default=1)

        slurm_cluster = self._slurm_cluster()
        slurm_metadata = self._metadata(slurm_cluster, label="SlurmCluster")
        statefulset = self._statefulset()
        statefulset_metadata = self._metadata(
            statefulset, label="legacy controller AdvancedStatefulSet"
        )
        owner_references = statefulset_metadata.get("ownerReferences")
        owners = owner_references if isinstance(owner_references, list) else []
        expected_owner = [
            item
            for item in owners
            if isinstance(item, Mapping)
            and item.get("controller") is True
            and item.get("kind") == "SlurmCluster"
            and item.get("name") == _SOPERATOR_CLUSTER_NAME
            and item.get("uid") == slurm_metadata.get("uid")
        ]
        if len(expected_owner) != 1:
            raise SoperatorSafetyPauseError(
                "legacy controller AdvancedStatefulSet owner is not the exact SlurmCluster"
            )
        statefulset_spec = _mapping(statefulset.get("spec"))
        if _integer(statefulset_spec.get("replicas"), default=1) != 1:
            raise SoperatorSafetyPauseError(
                "controller spool migration requires exactly one controller replica"
            )
        templates = self._volume_templates(statefulset)
        if templates:
            if len(templates) != 1 or _text(_mapping(templates[0].get("metadata")).get("name")) != (
                _LEGACY_SPOOL_TEMPLATE
            ):
                raise SoperatorSafetyPauseError(
                    "legacy controller volume claim template inventory is not exact"
                )
        elif not allow_migrated:
            raise SoperatorSafetyPauseError(
                "legacy controller spool template disappeared before migration admission"
            )

        source_pvc_name = f"{_LEGACY_SPOOL_TEMPLATE}-{_SOPERATOR_CONTROLLER_STATEFULSET}-0"
        source_pvc = self._pvc(source_pvc_name)
        source_pvc_metadata = self._metadata(source_pvc, label="legacy controller spool PVC")
        source_pvc_spec = _mapping(source_pvc.get("spec"))
        source_pvc_status = _mapping(source_pvc.get("status"))
        source_pv_name = _text(source_pvc_spec.get("volumeName"))
        if source_pvc_status.get("phase") != "Bound" or not source_pv_name:
            raise SoperatorSafetyPauseError("legacy controller spool PVC is not Bound")
        source_pv = self._pv(source_pv_name)
        source_pv_metadata = self._metadata(source_pv, label="legacy controller spool PV")
        source_pv_spec = _mapping(source_pv.get("spec"))
        claim_ref = _mapping(source_pv_spec.get("claimRef"))
        if (
            _text(claim_ref.get("namespace")) != _SOPERATOR_NAMESPACE
            or _text(claim_ref.get("name")) != source_pvc_name
            or _text(claim_ref.get("uid")) != _text(source_pvc_metadata.get("uid"))
        ):
            raise SoperatorSafetyPauseError(
                "legacy controller spool PV does not bind the exact PVC"
            )

        target_pvc = self._pvc(self._target_pvc_name)
        target_pvc_metadata = self._metadata(target_pvc, label="target controller spool PVC")
        target_pvc_status = _mapping(target_pvc.get("status"))
        if (
            target_pvc_status.get("phase") != "Bound"
            or self._target_pvc_name == source_pvc_name
            or target_pvc_metadata.get("uid") == source_pvc_metadata.get("uid")
        ):
            raise SoperatorSafetyPauseError(
                "target controller spool PVC is not a distinct Bound volume"
            )
        state = self._read_state()
        original_replicas = (
            _integer(state.get("originalReplicas"), default=0)
            if isinstance(state, Mapping)
            else observed_replicas
        )
        if original_replicas < 1:
            raise SoperatorSafetyPauseError(
                "Soperator controller Deployment replica preimage is unavailable"
            )
        if observed_replicas not in {0, original_replicas}:
            raise SoperatorSafetyPauseError(
                "Soperator controller Deployment replica count changed during migration"
            )
        return _ObservedMigrationIdentity(
            deployment_uid=_text(deployment_metadata.get("uid")),
            original_replicas=original_replicas,
            slurm_cluster_uid=_text(slurm_metadata.get("uid")),
            statefulset_uid=_text(statefulset_metadata.get("uid")),
            source_pvc_name=source_pvc_name,
            source_pvc_uid=_text(source_pvc_metadata.get("uid")),
            source_pv_name=source_pv_name,
            source_pv_uid=_text(source_pv_metadata.get("uid")),
            target_pvc_uid=_text(target_pvc_metadata.get("uid")),
        )

    def _new_state(self, identity: _ObservedMigrationIdentity) -> dict[str, object]:
        return {
            "schema": SOPERATOR_CONTROLLER_SPOOL_MIGRATION_SCHEMA,
            "operationId": self._operation_id,
            "targetRelease": self._target_release,
            "namespace": _SOPERATOR_NAMESPACE,
            "statefulSetName": _SOPERATOR_CONTROLLER_STATEFULSET,
            "targetPvcName": self._target_pvc_name,
            **identity.as_payload(),
            "status": "intent",
            "lastCompletedStep": "intent-recorded",
        }

    def _validate_state(
        self,
        state: Mapping[str, object],
        identity: _ObservedMigrationIdentity,
    ) -> None:
        expected = self._new_state(identity)
        immutable = (
            "schema",
            "operationId",
            "targetRelease",
            "namespace",
            "statefulSetName",
            "targetPvcName",
            "deploymentUid",
            "originalReplicas",
            "slurmClusterUid",
            "statefulSetUid",
            "sourcePvcName",
            "sourcePvcUid",
            "sourcePvName",
            "sourcePvUid",
            "targetPvcUid",
        )
        if any(state.get(key) != expected[key] for key in immutable):
            raise SoperatorSafetyPauseError(
                "recovery-required: controller spool migration identity changed"
            )

    def _checkpoint(self, state: Mapping[str, object], **updates: object) -> dict[str, object]:
        next_state = dict(state)
        next_state.update(updates)
        self._write_state(next_state)
        return next_state

    def _retain_source_pv(
        self,
        identity: _ObservedMigrationIdentity,
        state: Mapping[str, object],
    ) -> dict[str, object]:
        source_pv = self._pv(identity.source_pv_name)
        metadata = self._metadata(source_pv, label="legacy controller spool PV")
        if metadata.get("uid") != identity.source_pv_uid:
            raise SoperatorSafetyPauseError("legacy controller spool PV identity changed")
        spec = _mapping(source_pv.get("spec"))
        policy = _text(spec.get("persistentVolumeReclaimPolicy"))
        if policy != "Retain":
            if policy not in {"Delete", "Recycle"}:
                raise SoperatorSafetyPauseError(
                    "legacy controller spool PV reclaim policy is unsupported"
                )
            source_pv = self._patch(
                namespace=None,
                resource="pv",
                name=identity.source_pv_name,
                operations=(
                    {"op": "test", "path": "/metadata/uid", "value": identity.source_pv_uid},
                    {
                        "op": "test",
                        "path": "/metadata/resourceVersion",
                        "value": _text(metadata.get("resourceVersion")),
                    },
                    {
                        "op": "test",
                        "path": "/spec/persistentVolumeReclaimPolicy",
                        "value": policy,
                    },
                    {
                        "op": "replace",
                        "path": "/spec/persistentVolumeReclaimPolicy",
                        "value": "Retain",
                    },
                ),
            )
            if _mapping(source_pv.get("spec")).get("persistentVolumeReclaimPolicy") != ("Retain"):
                raise RuntimeError("legacy controller spool PV retention did not converge")
        return self._checkpoint(
            state,
            status="active",
            lastCompletedStep="source-pv-retained",
        )

    def _scale_controller(self, *, replicas: int, expected_uid: str) -> None:
        deployment = self._deployment()
        metadata = self._metadata(deployment, label="Soperator controller Deployment")
        if metadata.get("uid") != expected_uid:
            raise SoperatorSafetyPauseError("Soperator controller Deployment identity changed")
        spec = _mapping(deployment.get("spec"))
        current = _integer(spec.get("replicas"), default=1)
        if current == replicas:
            return
        self._patch(
            namespace=_SOPERATOR_CONTROLLER_NAMESPACE,
            resource="deployment",
            name=_SOPERATOR_CONTROLLER_DEPLOYMENT,
            operations=(
                {"op": "test", "path": "/metadata/uid", "value": expected_uid},
                {
                    "op": "test",
                    "path": "/metadata/resourceVersion",
                    "value": _text(metadata.get("resourceVersion")),
                },
                {"op": "test", "path": "/spec/replicas", "value": current},
                {"op": "replace", "path": "/spec/replicas", "value": replicas},
            ),
        )

    def _wait_for_controller(self, *, replicas: int, require_available: bool) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            deployment = self._deployment()
            metadata = self._metadata(deployment, label="Soperator controller Deployment")
            spec = _mapping(deployment.get("spec"))
            status = _mapping(deployment.get("status"))
            desired = _integer(spec.get("replicas"), default=1)
            observed = _integer(status.get("observedGeneration"))
            generation = _integer(metadata.get("generation"))
            ready = _integer(status.get("readyReplicas"))
            available = _integer(status.get("availableReplicas"))
            current = _integer(status.get("replicas"))
            if desired == replicas and observed == generation:
                if replicas == 0 and current == ready == available == 0:
                    return
                if (
                    replicas > 0
                    and require_available
                    and ready >= replicas
                    and available >= replicas
                ):
                    subsets = self._webhook_endpoints().get("subsets")
                    ready_addresses = (
                        sum(
                            len(addresses)
                            for subset in subsets
                            if isinstance(subset, Mapping)
                            for addresses in (subset.get("addresses"),)
                            if isinstance(addresses, list)
                        )
                        if isinstance(subsets, list)
                        else 0
                    )
                    if ready_addresses >= replicas:
                        return
            if time.monotonic() >= deadline:
                state = "available" if require_available else "quiescent"
                raise RuntimeError(f"Soperator controller Deployment did not become {state}")
            time.sleep(self._poll_interval_seconds)

    def _remove_legacy_template(
        self,
        identity: _ObservedMigrationIdentity,
        state: Mapping[str, object],
    ) -> dict[str, object]:
        statefulset = self._statefulset()
        metadata = self._metadata(statefulset, label="legacy controller AdvancedStatefulSet")
        if metadata.get("uid") != identity.statefulset_uid:
            raise SoperatorSafetyPauseError(
                "legacy controller AdvancedStatefulSet identity changed"
            )
        templates = self._volume_templates(statefulset)
        if not templates:
            return self._checkpoint(
                state,
                status="prepared",
                lastCompletedStep="legacy-template-removed",
            )
        if len(templates) != 1 or _text(_mapping(templates[0].get("metadata")).get("name")) != (
            _LEGACY_SPOOL_TEMPLATE
        ):
            raise SoperatorSafetyPauseError(
                "legacy controller volume claim template inventory changed"
            )
        claim_strategy = _mapping(
            _mapping(statefulset.get("spec")).get("volumeClaimUpdateStrategy")
        )
        if claim_strategy.get("type") != "OnDelete":
            statefulset = self._patch(
                namespace=_SOPERATOR_NAMESPACE,
                resource=_KRUISE_STATEFULSET_RESOURCE,
                name=_SOPERATOR_CONTROLLER_STATEFULSET,
                operations=(
                    {
                        "op": "test",
                        "path": "/metadata/uid",
                        "value": identity.statefulset_uid,
                    },
                    {
                        "op": "test",
                        "path": "/metadata/resourceVersion",
                        "value": _text(metadata.get("resourceVersion")),
                    },
                    {
                        "op": "test",
                        "path": "/spec/volumeClaimTemplates/0/metadata/name",
                        "value": _LEGACY_SPOOL_TEMPLATE,
                    },
                    {
                        "op": "add",
                        "path": "/spec/volumeClaimUpdateStrategy",
                        "value": {"type": "OnDelete"},
                    },
                ),
            )
            if (
                _mapping(_mapping(statefulset.get("spec")).get("volumeClaimUpdateStrategy")).get(
                    "type"
                )
                != "OnDelete"
            ):
                raise RuntimeError(
                    "legacy controller volume claim update strategy did not converge"
                )
            state = self._checkpoint(
                state,
                status="active",
                lastCompletedStep="claim-update-strategy-staged",
            )
        statefulset = self._statefulset()
        metadata = self._metadata(statefulset, label="legacy controller AdvancedStatefulSet")
        if metadata.get("uid") != identity.statefulset_uid:
            raise SoperatorSafetyPauseError(
                "legacy controller AdvancedStatefulSet identity changed"
            )
        templates = self._volume_templates(statefulset)
        claim_strategy = _mapping(
            _mapping(statefulset.get("spec")).get("volumeClaimUpdateStrategy")
        )
        if (
            claim_strategy.get("type") != "OnDelete"
            or len(templates) != 1
            or _text(_mapping(templates[0].get("metadata")).get("name")) != _LEGACY_SPOOL_TEMPLATE
        ):
            raise SoperatorSafetyPauseError(
                "legacy controller claim strategy or template changed before removal"
            )
        statefulset = self._patch(
            namespace=_SOPERATOR_NAMESPACE,
            resource=_KRUISE_STATEFULSET_RESOURCE,
            name=_SOPERATOR_CONTROLLER_STATEFULSET,
            operations=(
                {"op": "test", "path": "/metadata/uid", "value": identity.statefulset_uid},
                {
                    "op": "test",
                    "path": "/spec/volumeClaimUpdateStrategy/type",
                    "value": "OnDelete",
                },
                {
                    "op": "test",
                    "path": "/spec/volumeClaimTemplates/0/metadata/name",
                    "value": _LEGACY_SPOOL_TEMPLATE,
                },
                {"op": "remove", "path": "/spec/volumeClaimTemplates/0"},
            ),
        )
        if self._volume_templates(statefulset):
            raise RuntimeError("legacy controller spool template removal did not converge")
        return self._checkpoint(
            state,
            status="prepared",
            lastCompletedStep="legacy-template-removed",
        )

    def _verify_target_spool_projection(self) -> None:
        slurm_cluster = self._slurm_cluster()
        spec = _mapping(slurm_cluster.get("spec"))
        volume_sources = spec.get("volumeSources")
        sources = volume_sources if isinstance(volume_sources, list) else []
        target_sources = [
            item
            for item in sources
            if isinstance(item, Mapping) and _text(item.get("name")) == _TARGET_SPOOL_VOLUME_SOURCE
        ]
        if len(target_sources) != 1:
            raise SoperatorSafetyPauseError(
                "target SlurmCluster controller spool volume source is not unique"
            )
        claim = _mapping(target_sources[0].get("persistentVolumeClaim"))
        if _text(claim.get("claimName")) != self._target_pvc_name or claim.get("readOnly") is True:
            raise SoperatorSafetyPauseError(
                "target SlurmCluster controller spool PVC projection is not exact"
            )
        controller = _mapping(_mapping(spec.get("slurmNodes")).get("controller"))
        spool = _mapping(_mapping(controller.get("volumes")).get("spool"))
        if _text(spool.get("volumeSourceName")) != _TARGET_SPOOL_VOLUME_SOURCE:
            raise SoperatorSafetyPauseError(
                "target SlurmCluster controller spool selection is not exact"
            )

    @staticmethod
    def _controller_spool_claim(payload: Mapping[str, object]) -> str:
        spec = _mapping(payload.get("spec"))
        volumes = spec.get("volumes")
        items = volumes if isinstance(volumes, list) else []
        matches = [
            item
            for item in items
            if isinstance(item, Mapping) and _text(item.get("name")) == _TARGET_SPOOL_VOLUME_SOURCE
        ]
        if len(matches) != 1:
            return ""
        return _text(_mapping(matches[0].get("persistentVolumeClaim")).get("claimName"))

    @staticmethod
    def _controller_pod_is_owned(
        pod: Mapping[str, object],
        identity: _ObservedMigrationIdentity,
    ) -> bool:
        owners = _mapping(pod.get("metadata")).get("ownerReferences")
        owner_items = owners if isinstance(owners, list) else []
        return any(
            isinstance(item, Mapping)
            and item.get("controller") is True
            and item.get("apiVersion") == "apps.kruise.io/v1beta1"
            and item.get("kind") == "StatefulSet"
            and item.get("name") == _SOPERATOR_CONTROLLER_STATEFULSET
            and item.get("uid") == identity.statefulset_uid
            for item in owner_items
        )

    def _request_controller_pod_recreation(
        self,
        identity: _ObservedMigrationIdentity,
    ) -> None:
        pod = self._get(
            "--namespace",
            _SOPERATOR_NAMESPACE,
            "get",
            "pod",
            _SOPERATOR_CONTROLLER_POD,
        )
        metadata = self._metadata(pod, label="legacy controller Pod")
        if not self._controller_pod_is_owned(pod, identity):
            raise SoperatorSafetyPauseError(
                "legacy controller Pod owner is not the exact AdvancedStatefulSet"
            )
        claim_name = self._controller_spool_claim(pod)
        if claim_name == self._target_pvc_name:
            return
        if claim_name != identity.source_pvc_name:
            raise SoperatorSafetyPauseError("legacy controller Pod spool claim identity changed")
        labels = metadata.get("labels")
        label_map = labels if isinstance(labels, Mapping) else {}
        existing = label_map.get(_KRUISE_SPECIFIED_DELETE_LABEL)
        if existing == "true":
            return
        if existing is not None:
            raise SoperatorSafetyPauseError("legacy controller Pod specified-delete intent changed")
        label_path = "/metadata/labels/apps.kruise.io~1specified-delete"
        label_operation: dict[str, object]
        if labels is None:
            label_path = "/metadata/labels"
            label_operation = {
                "op": "add",
                "path": label_path,
                "value": {_KRUISE_SPECIFIED_DELETE_LABEL: "true"},
            }
        elif isinstance(labels, Mapping):
            label_operation = {
                "op": "add",
                "path": label_path,
                "value": "true",
            }
        else:
            raise SoperatorSafetyPauseError("legacy controller Pod labels are malformed")
        patched = self._patch(
            namespace=_SOPERATOR_NAMESPACE,
            resource="pod",
            name=_SOPERATOR_CONTROLLER_POD,
            operations=(
                {"op": "test", "path": "/metadata/uid", "value": metadata.get("uid")},
                {
                    "op": "test",
                    "path": "/metadata/resourceVersion",
                    "value": metadata.get("resourceVersion"),
                },
                label_operation,
            ),
        )
        patched_metadata = self._metadata(patched, label="legacy controller Pod")
        patched_labels = _mapping(patched_metadata.get("labels"))
        if (
            patched_metadata.get("uid") != metadata.get("uid")
            or patched_labels.get(_KRUISE_SPECIFIED_DELETE_LABEL) != "true"
        ):
            raise RuntimeError("controller Pod recreation request did not converge")

    def _wait_for_target_spool_adoption(
        self,
        identity: _ObservedMigrationIdentity,
    ) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            statefulset = self._statefulset()
            metadata = self._metadata(statefulset, label="legacy controller AdvancedStatefulSet")
            if metadata.get("uid") != identity.statefulset_uid:
                raise SoperatorSafetyPauseError(
                    "legacy controller AdvancedStatefulSet identity changed"
                )
            statefulset_spec = _mapping(statefulset.get("spec"))
            statefulset_template = _mapping(statefulset_spec.get("template"))
            statefulset_ready = (
                not self._volume_templates(statefulset)
                and self._controller_spool_claim(statefulset_template) == self._target_pvc_name
            )
            pods = self._get(
                "--namespace",
                _SOPERATOR_NAMESPACE,
                "get",
                "pods",
                "--field-selector",
                f"metadata.name={_SOPERATOR_CONTROLLER_POD}",
            ).get("items")
            pod_items = pods if isinstance(pods, list) else []
            pod = pod_items[0] if len(pod_items) == 1 and isinstance(pod_items[0], Mapping) else {}
            pod_metadata = _mapping(pod.get("metadata"))
            owners = pod_metadata.get("ownerReferences")
            owner_items = owners if isinstance(owners, list) else []
            owned = bool(owner_items) and self._controller_pod_is_owned(pod, identity)
            pod_claim_ready = owned and self._controller_spool_claim(pod) == self._target_pvc_name
            init_statuses = _mapping(pod.get("status")).get("initContainerStatuses")
            init_items = init_statuses if isinstance(init_statuses, list) else []
            gate_matches = [
                item
                for item in init_items
                if isinstance(item, Mapping) and _text(item.get("name")) == _TARGET_SPOOL_MOUNT_GATE
            ]
            gate_state = _mapping(gate_matches[0].get("state")) if len(gate_matches) == 1 else {}
            terminated = _mapping(gate_state.get("terminated"))
            gate_ready = _integer(terminated.get("exitCode"), default=-1) == 0
            if statefulset_ready and pod_claim_ready and gate_ready:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "controller workload did not adopt the target spool PVC and mount receipt"
                )
            time.sleep(self._poll_interval_seconds)

    def _verify_retained_storage(self, identity: _ObservedMigrationIdentity) -> None:
        observed = self._observe_identity(allow_migrated=True)
        if observed != identity:
            raise SoperatorSafetyPauseError(
                "controller spool storage or workload identity changed during migration"
            )
        source_pv = self._pv(identity.source_pv_name)
        if _mapping(source_pv.get("spec")).get("persistentVolumeReclaimPolicy") != "Retain":
            raise SoperatorSafetyPauseError("legacy controller spool PV lost its Retain policy")

    def prepare(self) -> Mapping[str, object]:
        """Quiesce the reconciler and stage the admission-safe two-step update."""

        self._assert_authority()
        current = self._read_state()
        identity = self._observe_identity(allow_migrated=isinstance(current, Mapping))
        if current is None:
            state = self._new_state(identity)
            self._write_state(state)
        else:
            self._validate_state(current, identity)
            state = dict(current)
        if state.get("status") == "complete":
            self._verify_target_spool_projection()
            if self._volume_templates(self._statefulset()):
                state = self._checkpoint(
                    state,
                    status="prepared",
                    lastCompletedStep="post-open-legacy-template-reintroduced",
                    releaseOpened=True,
                )
                return {
                    "status": "prepared",
                    "deploymentUid": identity.deployment_uid,
                    "statefulSetUid": identity.statefulset_uid,
                }
            self._request_controller_pod_recreation(identity)
            self._wait_for_target_spool_adoption(identity)
            self._verify_retained_storage(identity)
            return {"status": "already-complete", **identity.as_payload()}
        if state.get("status") == "prepared":
            self._scale_controller(
                replicas=identity.original_replicas,
                expected_uid=identity.deployment_uid,
            )
            self._wait_for_controller(
                replicas=identity.original_replicas,
                require_available=True,
            )
            self._verify_retained_storage(identity)
            self._checkpoint(
                state,
                status="prepared",
                lastCompletedStep="controller-restored-before-release-open",
            )
            return {
                "status": "prepared",
                "deploymentUid": identity.deployment_uid,
                "statefulSetUid": identity.statefulset_uid,
            }

        state = self._retain_source_pv(identity, state)
        scaled_down = False
        try:
            self._scale_controller(replicas=0, expected_uid=identity.deployment_uid)
            scaled_down = True
            self._wait_for_controller(replicas=0, require_available=False)
            state = self._checkpoint(
                state,
                status="active",
                lastCompletedStep="controller-reconciler-quiesced",
            )
            state = self._remove_legacy_template(identity, state)
            self._verify_retained_storage(identity)
            self._scale_controller(
                replicas=identity.original_replicas,
                expected_uid=identity.deployment_uid,
            )
            self._wait_for_controller(
                replicas=identity.original_replicas,
                require_available=True,
            )
            self._verify_retained_storage(identity)
            state = self._checkpoint(
                state,
                status="prepared",
                lastCompletedStep="controller-restored-before-release-open",
            )
            return {
                "status": "prepared",
                "deploymentUid": identity.deployment_uid,
                "statefulSetUid": identity.statefulset_uid,
            }
        except BaseException:
            if scaled_down:
                self._scale_controller(
                    replicas=identity.original_replicas,
                    expected_uid=identity.deployment_uid,
                )
                self._wait_for_controller(
                    replicas=identity.original_replicas,
                    require_available=True,
                )
            raise

    def finish(self, prepared: Mapping[str, object], *, release_opened: bool) -> None:
        """Verify the restored reconciler and seal after the target release opened."""

        state = self._read_state()
        if not isinstance(state, Mapping):
            raise SoperatorSafetyPauseError(
                "controller spool migration journal disappeared before restore"
            )
        identity = self._observe_identity(allow_migrated=True)
        self._validate_state(state, identity)
        self._scale_controller(
            replicas=identity.original_replicas,
            expected_uid=identity.deployment_uid,
        )
        self._wait_for_controller(
            replicas=identity.original_replicas,
            require_available=True,
        )
        if release_opened:
            self._verify_target_spool_projection()
            state = self._remove_legacy_template(identity, state)
            self._request_controller_pod_recreation(identity)
            self._wait_for_target_spool_adoption(identity)
        self._verify_retained_storage(identity)
        self._checkpoint(
            state,
            status="complete" if release_opened else "prepared",
            lastCompletedStep=(
                "target-release-opened-and-controller-restored"
                if release_opened
                else "controller-restored-before-retry"
            ),
            releaseOpened=release_opened,
        )


__all__ = [
    "SOPERATOR_CONTROLLER_SPOOL_MIGRATION_SCHEMA",
    "SoperatorControllerSpoolMigration",
]
