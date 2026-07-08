"""Cluster-scoped artifact paths for Soperator lifecycle commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data

SOPERATOR_CLUSTER_ARTIFACTS_DIR_NAME = "soperator-clusters"
SOPERATOR_CLUSTER_ARTIFACTS_DEFAULT_KEY = "mk8s"
_ARTIFACT_TOKEN_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")


@dataclass(frozen=True)
class SoperatorClusterArtifactIdentity:
    cluster_key: str
    cluster_id: str = ""
    cluster_name: str = ""
    target_ref: str = ""
    kube_context: str = ""

    def as_metadata(self) -> dict[str, str]:
        return {
            "cluster_key": self.cluster_key,
            "cluster_id": self.cluster_id,
            "cluster_name": self.cluster_name,
            "target_ref": self.target_ref,
            "kube_context": self.kube_context,
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _artifact_token(value: Any) -> str:
    normalized = normalize_component_token(value)
    chars: list[str] = []
    previous_separator = False
    for char in normalized:
        if char in _ARTIFACT_TOKEN_ALLOWED:
            chars.append(char)
            previous_separator = False
            continue
        if previous_separator:
            continue
        chars.append("-")
        previous_separator = True
    return "".join(chars).strip("-")


def _mapping_text(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        if "." in key:
            current: Any = source
            for part in key.split("."):
                if not isinstance(current, Mapping):
                    current = None
                    break
                current = current.get(part)
            value = _text(current)
            if value:
                return value
            continue
        value = _text(source.get(key))
        if value:
            return value
    return ""


def _nested_mapping(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = source.get(key)
    return value if isinstance(value, Mapping) else {}


def soperator_cluster_artifact_key(
    *,
    cluster_id: Any = "",
    cluster_name: Any = "",
    kube_context: Any = "",
    target_ref: Any = "",
) -> str:
    for value in (cluster_id, cluster_name, kube_context, target_ref):
        normalized = _artifact_token(value)
        if normalized:
            return normalized
    return SOPERATOR_CLUSTER_ARTIFACTS_DEFAULT_KEY


def soperator_cluster_artifact_identity(
    *,
    cluster_id: Any = "",
    cluster_name: Any = "",
    target_ref: Any = "",
    kube_context: Any = "",
) -> SoperatorClusterArtifactIdentity:
    resolved_cluster_id = _text(cluster_id)
    resolved_cluster_name = _text(cluster_name)
    resolved_target_ref = _artifact_token(target_ref) or _text(target_ref)
    resolved_kube_context = _text(kube_context)
    return SoperatorClusterArtifactIdentity(
        cluster_key=soperator_cluster_artifact_key(
            cluster_id=resolved_cluster_id,
            cluster_name=resolved_cluster_name,
            kube_context=resolved_kube_context,
            target_ref=resolved_target_ref,
        ),
        cluster_id=resolved_cluster_id,
        cluster_name=resolved_cluster_name,
        target_ref=resolved_target_ref,
        kube_context=resolved_kube_context,
    )


def soperator_cluster_artifact_identity_from_snapshot(
    *,
    target_ref: Any = "",
    snapshot: Mapping[str, Any] | None = None,
    cluster_id: Any = "",
    cluster_name: Any = "",
    kube_context: Any = "",
) -> SoperatorClusterArtifactIdentity:
    provider = snapshot.get("provider") if isinstance(snapshot, Mapping) else {}
    cluster = provider.get("mk8s_cluster") if isinstance(provider, Mapping) else {}
    cluster_mapping = cluster if isinstance(cluster, Mapping) else {}
    return soperator_cluster_artifact_identity(
        cluster_id=_text(cluster_id)
        or _mapping_text(cluster_mapping, "id", "cluster_id", "metadata.id"),
        cluster_name=_text(cluster_name)
        or _mapping_text(cluster_mapping, "name", "cluster_name", "display_name"),
        target_ref=target_ref,
        kube_context=kube_context,
    )


def _target_row(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    deploy = payload.get("deploy")
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    normalized_target = normalize_component_token(target_ref)
    if not isinstance(targets, list) or not normalized_target:
        return {}
    for row in targets:
        if not isinstance(row, Mapping):
            continue
        if normalize_component_token(row.get("instance_id")) == normalized_target:
            return row
    return {}


def _infra_cluster_name(payload: Mapping[str, Any], target_ref: str) -> str:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    normalized_target = normalize_component_token(target_ref)
    if not isinstance(components, list) or not normalized_target:
        return ""
    for row in components:
        if not isinstance(row, Mapping):
            continue
        instance_id = normalize_component_token(row.get("instance_id") or row.get("id"))
        component_id = normalize_component_token(row.get("component_id") or row.get("id"))
        if instance_id != normalized_target and component_id != normalized_target:
            continue
        inputs = _nested_mapping(row, "inputs")
        cluster = _nested_mapping(inputs, "cluster")
        return _mapping_text(
            row,
            "cluster_name",
        ) or _mapping_text(inputs, "cluster_name") or _mapping_text(cluster, "cluster_name")
    return ""


def soperator_cluster_artifact_identity_from_payload(
    payload_or_config: Any,
    *,
    target_ref: str,
    cluster_id: Any = "",
    cluster_name: Any = "",
    kube_context: Any = "",
) -> SoperatorClusterArtifactIdentity:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        payload = {}
    target = _target_row(payload, target_ref)
    inventory = _nested_mapping(target, "inventory")
    return soperator_cluster_artifact_identity(
        cluster_id=_text(cluster_id) or _mapping_text(target, "cluster_id"),
        cluster_name=(
            _text(cluster_name)
            or _mapping_text(target, "cluster_name", "name")
            or _mapping_text(inventory, "cluster_name", "mk8s_cluster_name", "name")
            or _infra_cluster_name(payload, target_ref)
        ),
        target_ref=target_ref,
        kube_context=_text(kube_context) or _mapping_text(target, "kube_context"),
    )


def soperator_cluster_reports_root(
    project_dir: Path,
    identity: SoperatorClusterArtifactIdentity,
) -> Path:
    return project_dir / "generated" / "reports" / SOPERATOR_CLUSTER_ARTIFACTS_DIR_NAME / identity.cluster_key


def soperator_cluster_report_dir(
    project_dir: Path,
    identity: SoperatorClusterArtifactIdentity,
    command_dir: str,
) -> Path:
    return soperator_cluster_reports_root(project_dir, identity) / command_dir


def soperator_cluster_backup_dir(
    project_dir: Path,
    backup_dir: Path | None,
    identity: SoperatorClusterArtifactIdentity,
) -> Path:
    root = backup_dir if backup_dir is not None else project_dir / "backups"
    return root / SOPERATOR_CLUSTER_ARTIFACTS_DIR_NAME / identity.cluster_key


def soperator_cluster_checkpoint_path(
    project_dir: Path,
    identity: SoperatorClusterArtifactIdentity,
    command_dir: str,
) -> Path:
    return (
        project_dir
        / ".nebius-cxcli"
        / SOPERATOR_CLUSTER_ARTIFACTS_DIR_NAME
        / identity.cluster_key
        / command_dir
        / "checkpoint.json"
    )
