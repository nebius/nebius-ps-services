"""Flux rendering orchestration (generic Helm release model)."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .component_defaults import (
    resolve_component_defaults,
    set_component_path,
)
from .component_instances import component_instance_id, component_type_id
from .component_sources import component_input_binding_ref
from .component_wiring import (
    _UNRESOLVED,
    component_entry_lookup,
    component_output_ref,
    input_binding_conflicts,
    output_lookup,
    resolve_component_output_value,
    resolve_input_binding_source,
)
from .components import component_entries, component_entry_chart_name
from .deploy_targets import (
    app_chart_target_ref,
    enabled_cluster_target_refs,
    flux_target_dir,
)
from .mk8s_gpu import (
    mk8s_gpu_flux_release_dependencies,
    mk8s_gpu_flux_release_post_render_patches,
)
from .mysterybox_eso import (
    MYSTERYBOX_ESO_MANAGED_LABEL,
    MYSTERYBOX_ESO_MANAGED_VALUE,
    mysterybox_eso_extra_objects_for_target,
)
from .nfs_csi import NFS_CSI_APP_ID, nfs_instance_id_for_target
from .paths import ProjectPaths
from .runtime_config import to_plain_data
from .soperator_child_charts import SOPERATOR_APP_ID

_CLUSTER_SCOPED_RENDER_KINDS = {
    ("", "Namespace"),
    ("", "Node"),
    ("", "PersistentVolume"),
    ("admissionregistration.k8s.io", "MutatingWebhookConfiguration"),
    ("admissionregistration.k8s.io", "ValidatingWebhookConfiguration"),
    ("apiextensions.k8s.io", "CustomResourceDefinition"),
    ("apiregistration.k8s.io", "APIService"),
    ("gateway.networking.k8s.io", "GatewayClass"),
    ("networking.k8s.io", "IngressClass"),
    ("node.k8s.io", "RuntimeClass"),
    ("rbac.authorization.k8s.io", "ClusterRole"),
    ("rbac.authorization.k8s.io", "ClusterRoleBinding"),
    ("scheduling.k8s.io", "PriorityClass"),
    ("storage.k8s.io", "CSIDriver"),
    ("storage.k8s.io", "CSINode"),
    ("storage.k8s.io", "StorageClass"),
    ("storage.k8s.io", "VolumeAttachment"),
}
_CERT_MANAGER_CERTIFICATE_API_VERSION = "cert-manager.io/v1"
_CERT_MANAGER_PRIVATE_KEY_ROTATION_POLICY = "Always"
_SOPERATOR_STATIC_SOURCE_KIND = "static_helm_chart"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _helm_repository_doc(name: str, url: str, *, repo_type: str = "default") -> dict[str, Any]:
    spec: dict[str, Any] = {"interval": "30m", "url": url}
    if repo_type == "oci":
        spec["type"] = "oci"
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": spec,
    }


def _git_repository_doc(name: str, url: str, ref: str) -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "GitRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {"interval": "5m", "url": url, "ref": {"branch": ref}},
    }


def _multi_doc_yaml(docs: list[dict[str, Any]]) -> str:
    return "\n---\n".join(yaml.safe_dump(doc, sort_keys=False).strip() for doc in docs) + "\n"


def _api_group(api_version: object) -> str:
    if not isinstance(api_version, str) or "/" not in api_version:
        return ""
    return api_version.split("/", maxsplit=1)[0]


def _local_chart_doc_needs_namespace(doc: dict[str, Any]) -> bool:
    kind = str(doc.get("kind", "")).strip()
    if not kind:
        return False
    return (_api_group(doc.get("apiVersion")), kind) not in _CLUSTER_SCOPED_RENDER_KINDS


def _local_chart_doc_is_helm_hook(doc: dict[str, Any]) -> bool:
    metadata = doc.get("metadata")
    annotations = metadata.get("annotations") if isinstance(metadata, Mapping) else None
    if not isinstance(annotations, Mapping):
        return False
    include_static = str(
        annotations.get("nebius-cxcli.nebius.ai/include-local-render", "") or ""
    ).strip()
    if include_static.lower() in {"1", "true", "yes"}:
        return False
    return bool(str(annotations.get("helm.sh/hook", "") or "").strip())


def _is_kubernetes_manifest_doc(doc: object) -> bool:
    if not isinstance(doc, dict):
        return False
    return bool(str(doc.get("apiVersion") or "").strip() and str(doc.get("kind") or "").strip())


def _make_cert_manager_certificate_rotation_policy_explicit(doc: dict[str, Any]) -> None:
    if (
        doc.get("apiVersion") != _CERT_MANAGER_CERTIFICATE_API_VERSION
        or doc.get("kind") != "Certificate"
    ):
        return
    spec = doc.setdefault("spec", {})
    if not isinstance(spec, dict):
        return
    private_key = spec.get("privateKey")
    if private_key is None:
        private_key = {}
        spec["privateKey"] = private_key
    if not isinstance(private_key, dict):
        return
    if not str(private_key.get("rotationPolicy") or "").strip():
        private_key["rotationPolicy"] = _CERT_MANAGER_PRIVATE_KEY_ROTATION_POLICY


def _cert_manager_certificate_rotation_policy_post_render_patch(
    *,
    name: str,
    namespace: str,
    rotation_policy: str = _CERT_MANAGER_PRIVATE_KEY_ROTATION_POLICY,
) -> dict[str, Any]:
    return {
        "target": {
            "group": "cert-manager.io",
            "version": "v1",
            "kind": "Certificate",
            "name": name,
            "namespace": namespace,
        },
        "patch": yaml.safe_dump(
            {
                "apiVersion": _CERT_MANAGER_CERTIFICATE_API_VERSION,
                "kind": "Certificate",
                "metadata": {"name": name, "namespace": namespace},
                "spec": {
                    "privateKey": {
                        "rotationPolicy": rotation_policy,
                    }
                },
            },
            sort_keys=False,
        ),
    }


def _soperator_certificate_rotation_policy_post_render_patches(
    *,
    release_name: str,
    namespace: str,
    values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cert_manager_values = values.get("certManager")
    private_key_values = (
        cert_manager_values.get("privateKey") if isinstance(cert_manager_values, Mapping) else None
    )
    rotation_policy = ""
    if isinstance(private_key_values, Mapping):
        rotation_policy = str(private_key_values.get("rotationPolicy") or "").strip()
    if not rotation_policy:
        rotation_policy = _CERT_MANAGER_PRIVATE_KEY_ROTATION_POLICY
    return [
        _cert_manager_certificate_rotation_policy_post_render_patch(
            name=f"{release_name}-serving-cert",
            namespace=namespace,
            rotation_policy=rotation_policy,
        ),
        _cert_manager_certificate_rotation_policy_post_render_patch(
            name=f"{release_name}-mariadb-operator-webhook-cert",
            namespace=namespace,
        ),
    ]


def _inject_local_chart_namespace(rendered: str, *, namespace: str) -> str:
    rendered_docs = [doc for doc in yaml.safe_load_all(rendered) if _is_kubernetes_manifest_doc(doc)]
    docs = [doc for doc in rendered_docs if not _local_chart_doc_is_helm_hook(doc)]
    if not docs:
        return ""
    for doc in docs:
        _make_cert_manager_certificate_rotation_policy_explicit(doc)
        if not _local_chart_doc_needs_namespace(doc):
            continue
        metadata = doc.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            continue
        if not str(metadata.get("namespace", "")).strip():
            metadata["namespace"] = namespace
    return _multi_doc_yaml(docs)


def _namespace_doc(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name},
    }


def _configmap_doc(
    *,
    name: str,
    namespace: str,
    data: dict[str, str],
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": data,
    }


def _github_tree_ref(repo: str) -> tuple[str, str, str] | None:
    token = repo.strip().rstrip("/")
    if not token:
        return None
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$", token)
    if not match:
        return None
    owner = match.group(1).strip()
    repository = match.group(2).strip()
    branch = match.group(3).strip()
    chart_path = match.group(4).strip()
    if not owner or not repository or not branch or not chart_path:
        return None
    return f"https://github.com/{owner}/{repository}.git", branch, chart_path


def _is_oci_repo(token: str) -> bool:
    return token.strip().lower().startswith("oci://")


def _oci_repo_url(chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    if not repo:
        return repo
    chart = chart_name.strip().strip("/")
    if not chart:
        return repo
    repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
    if repo_tail != chart.lower():
        return repo
    parent = repo.rsplit("/", maxsplit=1)[0].rstrip("/")
    return parent or repo


def _resolve_flux_chart_source(*, chart_name: str, chart_repo: str) -> dict[str, str]:
    if chart_repo:
        github_tree = _github_tree_ref(chart_repo)
        if github_tree is None:
            if _is_oci_repo(chart_repo):
                return {
                    "kind": "helm_repository",
                    "chart_ref": chart_name,
                    "repo_url": _oci_repo_url(chart_repo, chart_name),
                    "repo_type": "oci",
                }
            return {
                "kind": "helm_repository",
                "chart_ref": chart_name,
                "repo_url": chart_repo,
                "repo_type": "default",
            }
        git_url, git_ref, chart_path = github_tree
        return {
            "kind": "git_repository",
            "chart_ref": chart_path,
            "repo_url": git_url,
            "repo_ref": git_ref,
        }

    github_tree = _github_tree_ref(chart_name)
    if github_tree is not None:
        git_url, git_ref, chart_path = github_tree
        return {
            "kind": "git_repository",
            "chart_ref": chart_path,
            "repo_url": git_url,
            "repo_ref": git_ref,
        }

    raise ValueError(
        "chart.repo is required for Helm repository charts, "
        "or use a GitHub tree URL as chart.repo/chart.name for standalone chart sources"
    )


def _local_chart_path_from_entry(entry: Any | None) -> str:
    if entry is None:
        return ""
    source = str(getattr(entry, "source", "") or "").strip()
    if not source or source.startswith(("git::", "http://", "https://", "oci://")):
        return ""
    chart_path = Path(source).expanduser()
    if chart_path.is_dir() and (chart_path / "Chart.yaml").exists():
        return str(chart_path)
    return ""


def _runtime_app_chart_name(
    *,
    chart_node: dict[str, Any],
    entry_id: str,
    configured_chart_name: str | None,
    local_chart_path: str = "",
) -> str:
    repo = str(chart_node.get("repo", "")).strip().rstrip("/")
    if not repo and local_chart_path:
        return local_chart_path
    if repo.startswith("oci://") and "/" in repo:
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip()
        if configured_chart_name and repo_tail.lower() == configured_chart_name.lower():
            return repo_tail
        if repo_tail and not configured_chart_name:
            return repo_tail
    if configured_chart_name:
        return configured_chart_name
    return entry_id


def _requires_static_helm_render(*, entry_id: str, source_kind: str) -> bool:
    # The Soperator umbrella chart is large enough that storing Helm release
    # state in Kubernetes Secrets can exceed the 1 MiB object data limit.
    # Static rendering keeps the exact selected chart source while preserving
    # cxcli's established post-Flux apply model for Soperator resources.
    return entry_id == SOPERATOR_APP_ID and source_kind == "helm_repository"


def _file_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in value)


def _nested_mapping(node: dict[str, Any], key: str) -> dict[str, Any]:
    value = node.get(key)
    if isinstance(value, dict):
        return value
    value = {}
    node[key] = value
    return value


def _enabled_infra_instance_ids(payload: Mapping[str, Any], component_id: str) -> tuple[str, ...]:
    infra_node = payload.get("infra")
    components = infra_node.get("components") if isinstance(infra_node, Mapping) else None
    if not isinstance(components, list):
        return ()
    ids: list[str] = []
    for row in components:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != component_id:
            continue
        instance_id = component_instance_id(row)
        if instance_id:
            ids.append(instance_id)
    return tuple(ids)


def _soperator_nfs_instance_id(
    payload: Mapping[str, Any],
    *,
    target_ref: str,
) -> str:
    return nfs_instance_id_for_target(payload, target_ref=target_ref)


def _object_storage_instance_id(payload: Mapping[str, Any], *, target_ref: str) -> str:
    instance_ids = _enabled_infra_instance_ids(payload, "object-storage")
    if not instance_ids:
        return ""
    if target_ref and target_ref in instance_ids:
        return target_ref
    if len(instance_ids) == 1:
        return instance_ids[0]
    return ""


def _component_output_or_missing(
    resolved_component_outputs: Mapping[str, Any],
    *,
    instance_id: str,
    output_name: str,
) -> Any:
    ref = component_output_ref(instance_id, output_name)
    return resolved_component_outputs.get(ref, _UNRESOLVED)


def _materialize_soperator_nfs_values(
    *,
    payload: Mapping[str, Any],
    chart_node: dict[str, Any],
    target_ref: str,
    resolved_component_outputs: Mapping[str, Any],
) -> None:
    if not target_ref:
        return
    nfs_instance_id = _soperator_nfs_instance_id(payload, target_ref=target_ref)
    if not nfs_instance_id:
        return

    values = _nested_mapping(chart_node, "values")
    external_nfs = _nested_mapping(values, "externalNfs")
    explicitly_disabled = external_nfs.get("enabled") is False
    if explicitly_disabled:
        return

    existing_server = str(external_nfs.get("server", "") or "").strip()
    existing_path = str(external_nfs.get("path", "") or "").strip()
    server = existing_server or _component_output_or_missing(
        resolved_component_outputs,
        instance_id=nfs_instance_id,
        output_name="server_ip",
    )
    export_path = existing_path or _component_output_or_missing(
        resolved_component_outputs,
        instance_id=nfs_instance_id,
        output_name="export_path",
    )

    if server is _UNRESOLVED or export_path is _UNRESOLVED:
        if external_nfs.get("enabled") is True:
            missing = []
            if server is _UNRESOLVED:
                missing.append(component_output_ref(nfs_instance_id, "server_ip"))
            if export_path is _UNRESOLVED:
                missing.append(component_output_ref(nfs_instance_id, "export_path"))
            raise ValueError(
                "apps chart 'soperator' externalNfs binding requires Terraform output(s) "
                f"{', '.join(missing)}. Run `deploy`, or rerun `render` after Terraform state exists."
            )
        return

    external_nfs["enabled"] = True
    external_nfs.setdefault("server", server)
    external_nfs.setdefault("path", export_path)


def _coerce_mount_options(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    token = str(value or "").strip()
    return [token] if token else []


def _materialize_nfs_csi_storage_class_values(
    *,
    payload: Mapping[str, Any],
    chart_node: dict[str, Any],
    target_ref: str,
    resolved_component_outputs: Mapping[str, Any],
) -> None:
    if not target_ref:
        return
    nfs_instance_id = nfs_instance_id_for_target(payload, target_ref=target_ref)
    values = _nested_mapping(chart_node, "values")
    storage_class = _nested_mapping(values, "storageClass")
    if storage_class.get("create") is False:
        return
    if not nfs_instance_id:
        if storage_class.get("create") is True:
            raise ValueError(
                "apps chart 'csi-driver-nfs' StorageClass binding requires one enabled "
                "infra:nfs component for this MK8s target"
            )
        return

    parameters = _nested_mapping(storage_class, "parameters")
    existing_server = str(parameters.get("server", "") or "").strip()
    existing_share = str(parameters.get("share", "") or "").strip()
    server = existing_server or _component_output_or_missing(
        resolved_component_outputs,
        instance_id=nfs_instance_id,
        output_name="server_ip",
    )
    share = existing_share or _component_output_or_missing(
        resolved_component_outputs,
        instance_id=nfs_instance_id,
        output_name="export_path",
    )

    existing_mount_options = _coerce_mount_options(storage_class.get("mountOptions"))
    mount_options: Any
    if existing_mount_options:
        mount_options = existing_mount_options
    else:
        mount_options = _component_output_or_missing(
            resolved_component_outputs,
            instance_id=nfs_instance_id,
            output_name="mount_options",
        )

    missing: list[str] = []
    if server is _UNRESOLVED:
        missing.append(component_output_ref(nfs_instance_id, "server_ip"))
    if share is _UNRESOLVED:
        missing.append(component_output_ref(nfs_instance_id, "export_path"))
    if mount_options is _UNRESOLVED:
        missing.append(component_output_ref(nfs_instance_id, "mount_options"))
    if missing:
        if storage_class.get("create") is True:
            raise ValueError(
                "apps chart 'csi-driver-nfs' StorageClass binding requires Terraform output(s) "
                f"{', '.join(missing)}. Run `deploy`, or rerun `render` after Terraform state exists."
            )
        return

    storage_class["create"] = True
    parameters.setdefault("server", server)
    parameters.setdefault("share", share)
    if not existing_mount_options:
        normalized_mount_options = _coerce_mount_options(mount_options)
        if normalized_mount_options:
            storage_class["mountOptions"] = normalized_mount_options


def _materialize_soperator_backup_values(
    *,
    payload: Mapping[str, Any],
    chart_node: dict[str, Any],
    target_ref: str,
    resolved_component_outputs: Mapping[str, Any],
) -> None:
    values = _nested_mapping(chart_node, "values")
    backup_config = values.get("soperator-backup-config")
    if not isinstance(backup_config, dict) or backup_config.get("enabled") is not True:
        return

    bucket = _nested_mapping(backup_config, "bucket")
    existing_name = str(bucket.get("name", "") or "").strip()
    existing_endpoint = str(bucket.get("endpoint", "") or "").strip()
    if existing_name and existing_endpoint:
        return

    object_storage_instance_id = _object_storage_instance_id(payload, target_ref=target_ref)
    if not object_storage_instance_id:
        raise ValueError(
            "apps chart 'soperator' values.soperator-backup-config.enabled requires one enabled "
            "infra:object-storage component, or explicit values.soperator-backup-config.bucket.*"
        )

    bucket_name = existing_name or _component_output_or_missing(
        resolved_component_outputs,
        instance_id=object_storage_instance_id,
        output_name="bucket_name",
    )
    bucket_endpoint = existing_endpoint or _component_output_or_missing(
        resolved_component_outputs,
        instance_id=object_storage_instance_id,
        output_name="bucket_endpoint",
    )
    missing: list[str] = []
    if bucket_name is _UNRESOLVED:
        missing.append(component_output_ref(object_storage_instance_id, "bucket_name"))
    if bucket_endpoint is _UNRESOLVED:
        missing.append(component_output_ref(object_storage_instance_id, "bucket_endpoint"))
    if missing:
        raise ValueError(
            "apps chart 'soperator' values.soperator-backup-config.enabled requires "
            f"Terraform-derived output(s) {', '.join(missing)}. Run `deploy`, or rerun `render` "
            "after Terraform state exists."
        )

    bucket.setdefault("name", bucket_name)
    bucket.setdefault("endpoint", bucket_endpoint)


def _configured_app_release_specs(
    config: Any,
    *,
    component_output_values: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return []

    specs: list[dict[str, Any]] = []
    resolved_component_outputs = component_output_values or {}
    entry_by_id = {entry.id: entry for entry in component_entries("apps")}
    all_entry_by_id = component_entry_lookup()
    cluster_target_refs = set(enabled_cluster_target_refs(payload))
    apps_node = payload.get("apps")
    if isinstance(apps_node, dict):
        dynamic_charts = apps_node.get("charts")
        if isinstance(dynamic_charts, list):
            for raw_chart in dynamic_charts:
                if not isinstance(raw_chart, dict):
                    continue
                entry_id = str(raw_chart.get("id", "")).strip().lower()
                if not entry_id or not bool(raw_chart.get("enabled", False)):
                    continue
                entry = entry_by_id.get(entry_id)
                chart_node = dict(raw_chart)
                instance_id = str(chart_node.get("instance_id", entry_id)).strip() or entry_id
                target_ref = app_chart_target_ref(chart_node)
                if cluster_target_refs:
                    if not target_ref and instance_id in cluster_target_refs:
                        target_ref = instance_id
                    if not target_ref:
                        raise ValueError(
                            f"apps.charts[{instance_id}].instance_id must reference one of the enabled cluster targets"
                        )
                    if target_ref not in cluster_target_refs:
                        available = ", ".join(sorted(cluster_target_refs))
                        raise ValueError(
                            f"apps.charts[{instance_id}].instance_id must reference one of the enabled cluster targets: {available}"
                        )
                else:
                    target_ref = ""
                if entry is not None and entry.defaults:
                    chart_node = resolve_component_defaults(
                        payload=payload,
                        component_node=chart_node,
                        entry=entry,
                        preserve_existing_literal=True,
                        preserve_existing_shared=False,
                        include_shared=False,
                    )

                if entry is not None and entry.input_bindings:
                    conflicts = input_binding_conflicts(chart_node, entry)
                    if conflicts:
                        target_path, source_ref = conflicts[0]
                        raise ValueError(
                            f"apps chart '{entry_id}' field '{target_path}' is managed by component input "
                            f"binding '{source_ref}' and must not be set explicitly"
                        )
                    for binding in entry.input_bindings:
                        declared_source_ref = component_input_binding_ref(binding)
                        source_entry = all_entry_by_id.get(binding.source_component_id)
                        if source_entry is None:
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' references "
                                f"unknown component '{binding.source_component_id}'"
                            )
                        source_output = output_lookup(source_entry).get(binding.source_output_name)
                        if source_output is None:
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' references "
                                f"undeclared output '{declared_source_ref}'"
                            )
                        _resolved_source_entry, resolved_source_row, source_instance_id = (
                            resolve_input_binding_source(payload, binding=binding)
                        )
                        source_ref = (
                            component_output_ref(source_instance_id, binding.source_output_name)
                            if source_instance_id
                            else declared_source_ref
                        )

                        static_value = resolve_component_output_value(
                            payload,
                            component_id=binding.source_component_id,
                            output_name=binding.source_output_name,
                            instance_id=source_instance_id or binding.source_instance_id,
                        )
                        if static_value is not _UNRESOLVED:
                            set_component_path(chart_node, binding.target_path, static_value)
                            continue

                        if source_output.kind != "terraform_output":
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' could not "
                                f"resolve output '{source_ref}' from the current config payload"
                            )
                        if source_ref not in resolved_component_outputs:
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' requires "
                                f"Terraform-derived output '{source_ref}', but that value is not available yet. "
                                "Run `deploy`, or rerun `render` after Terraform state exists."
                            )
                        set_component_path(
                            chart_node,
                            binding.target_path,
                            resolved_component_outputs[source_ref],
                        )

                if entry_id == "soperator":
                    _materialize_soperator_nfs_values(
                        payload=payload,
                        chart_node=chart_node,
                        target_ref=target_ref,
                        resolved_component_outputs=resolved_component_outputs,
                    )
                    _materialize_soperator_backup_values(
                        payload=payload,
                        chart_node=chart_node,
                        target_ref=target_ref,
                        resolved_component_outputs=resolved_component_outputs,
                    )
                if entry_id == NFS_CSI_APP_ID:
                    _materialize_nfs_csi_storage_class_values(
                        payload=payload,
                        chart_node=chart_node,
                        target_ref=target_ref,
                        resolved_component_outputs=resolved_component_outputs,
                    )

                values_node = chart_node.get("values", {})
                if values_node is None:
                    values_node = {}
                if not isinstance(values_node, dict):
                    raise ValueError(
                        f"apps.charts[{entry_id}].values must be a mapping for enabled chart '{entry_id}'"
                    )

                chart_repo = str(chart_node.get("repo", "")).strip()
                local_chart_path = _local_chart_path_from_entry(entry)
                chart_name = _runtime_app_chart_name(
                    chart_node=chart_node,
                    entry_id=entry_id,
                    configured_chart_name=component_entry_chart_name(entry),
                    local_chart_path=local_chart_path,
                )
                chart_version = str(chart_node.get("version", "")).strip()
                if not chart_repo and not local_chart_path:
                    raise ValueError(
                        f"apps.charts[{entry_id}].repo is required for enabled chart '{entry_id}'"
                    )
                if local_chart_path and not chart_repo:
                    source_kind = "local_chart"
                    chart_ref = local_chart_path
                    source_url = ""
                    source_ref = ""
                    source_repo_type = "local"
                else:
                    source = _resolve_flux_chart_source(
                        chart_name=chart_name,
                        chart_repo=chart_repo,
                    )
                    source_kind = source["kind"]
                    chart_ref = source["chart_ref"]
                    source_url = source["repo_url"]
                    source_ref = source.get("repo_ref", "")
                    source_repo_type = source.get("repo_type", "default")
                    if _requires_static_helm_render(
                        entry_id=entry_id,
                        source_kind=source_kind,
                    ):
                        source_kind = _SOPERATOR_STATIC_SOURCE_KIND
                if source_kind in {"helm_repository", _SOPERATOR_STATIC_SOURCE_KIND} and not chart_version:
                    raise ValueError(
                        f"apps.charts[{entry_id}].version is required for enabled chart '{entry_id}'"
                    )

                namespace = str(chart_node.get("namespace", "")).strip()
                if not namespace:
                    raise ValueError(
                        f"apps.charts[{entry_id}].namespace is required for enabled chart '{entry_id}'"
                    )
                release_name = str(chart_node.get("release-name", entry_id)).strip() or entry_id
                repo_name_default = f"helm-{_file_slug(entry_id)}"
                repo_name = repo_name_default
                interval = "5m"
                scope = str(chart_node.get("group", "")).strip().lower() or "workloads"
                chart_values = values_node
                release_timeout = (
                    str(entry.default_release_timeout or "").strip() if entry is not None else ""
                )

                specs.append(
                    {
                        "scope": scope,
                        "entry_id": entry_id,
                        "instance_id": instance_id,
                        "target_ref": target_ref,
                        "release_name": release_name,
                        "namespace": namespace,
                        "source_name": repo_name,
                        "source_kind": source_kind,
                        "source_url": source_url,
                        "source_ref": source_ref,
                        "source_repo_type": source_repo_type,
                        "chart_ref": chart_ref,
                        "chart_version": chart_version
                        if source_kind in {"helm_repository", _SOPERATOR_STATIC_SOURCE_KIND}
                        else "",
                        "interval": interval,
                        "timeout": release_timeout,
                        "values": chart_values,
                        "depends_on": [],
                        "install_after": tuple(entry.default_release_install_after or ())
                        if entry is not None
                        else (),
                    }
                )
    dependency_map = mk8s_gpu_flux_release_dependencies(
        payload,
        release_entry_ids={
            str(item.get("entry_id", "")).strip()
            for item in specs
            if str(item.get("entry_id", "")).strip()
        },
    )
    specs_by_target_and_entry_id: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in specs:
        entry_id = str(item.get("entry_id", "")).strip()
        if not entry_id:
            continue
        key = (str(item.get("target_ref", "")).strip(), entry_id)
        specs_by_target_and_entry_id.setdefault(key, []).append(item)
    post_render_patch_map = mk8s_gpu_flux_release_post_render_patches(
        payload,
        release_entry_ids={
            str(item.get("entry_id", "")).strip()
            for item in specs
            if str(item.get("entry_id", "")).strip()
        },
    )
    for release in specs:
        entry_id = str(release.get("entry_id", "")).strip()
        if not entry_id:
            continue
        target_ref = str(release.get("target_ref", "")).strip()
        dependency_ids = tuple(
            dict.fromkeys(
                tuple(release.get("install_after") or ())
                + tuple(dependency_map.get(entry_id, ()))
            )
        )
        depends_on: list[dict[str, str]] = []
        for dependency_id in dependency_ids:
            for dependency in specs_by_target_and_entry_id.get((target_ref, dependency_id), ()):
                depends_on.append(
                    {
                        "name": str(dependency["release_name"]),
                        "namespace": str(dependency["namespace"]),
                    }
                )
        if depends_on:
            release["depends_on"] = depends_on
        patches = list(release.get("post_render_patches") or [])
        if entry_id == "soperator":
            patches.extend(
                _soperator_certificate_rotation_policy_post_render_patches(
                    release_name=str(release["release_name"]),
                    namespace=str(release["namespace"]),
                    values=release["values"] if isinstance(release.get("values"), Mapping) else {},
                )
            )
        patches.extend(post_render_patch_map.get((target_ref, entry_id)) or ())
        if patches:
            release["post_render_patches"] = [dict(item) for item in patches]
    return specs


def _helm_release_doc(
    *,
    release_name: str,
    namespace: str,
    source_name: str,
    source_kind: str,
    chart_ref: str,
    chart_version: str | None,
    interval: str,
    timeout: str | None,
    values: dict[str, Any],
    depends_on: list[dict[str, str]] | None = None,
    post_render_patches: list[dict[str, Any]] | None = None,
    disable_wait: bool = False,
) -> dict[str, Any]:
    chart_spec: dict[str, Any] = {
        "chart": chart_ref,
        "sourceRef": {
            "kind": source_kind,
            "name": source_name,
            "namespace": "flux-system",
        },
    }
    if chart_version:
        chart_spec["version"] = chart_version
    spec: dict[str, Any] = {
        "interval": interval,
        "chart": {"spec": chart_spec},
        "install": {"createNamespace": True},
        "values": values,
    }
    if disable_wait:
        spec["install"]["disableWait"] = True
        spec["upgrade"] = {"disableWait": True}
    if timeout:
        spec["timeout"] = timeout
    if depends_on:
        spec["dependsOn"] = depends_on
    if post_render_patches:
        spec["postRenderers"] = [
            {
                "kustomize": {
                    "patches": [
                        {
                            "target": dict(item.get("target", {}))
                            if isinstance(item.get("target"), dict)
                            else {},
                            "patch": str(item.get("patch", "")).strip(),
                        }
                        for item in post_render_patches
                        if str(item.get("patch", "")).strip()
                    ]
                }
            }
        ]
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": release_name, "namespace": namespace},
        "spec": spec,
    }


def _release_has_managed_mysterybox_external_secret(values: Mapping[str, Any]) -> bool:
    extra_objects = values.get("extraObjects")
    if not isinstance(extra_objects, list):
        return False
    for item in extra_objects:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "ExternalSecret":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        labels = metadata.get("labels")
        if not isinstance(labels, dict):
            continue
        if labels.get(MYSTERYBOX_ESO_MANAGED_LABEL) == MYSTERYBOX_ESO_MANAGED_VALUE:
            return True
    return False


def _pretty_dashboard_json(raw_json: str) -> str:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json.rstrip() + "\n"
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _dashboard_file_name(dashboard_name: str) -> str:
    return f"{_file_slug(dashboard_name)}.json"


def _target_dashboard_dir_name(target_ref: str) -> str:
    return _file_slug(target_ref) if target_ref else "current-cluster"


def _externalize_dashboard_json_values(
    state: _FluxRenderState,
    *,
    release: dict[str, Any],
) -> None:
    values = release.get("values")
    if not isinstance(values, dict):
        return
    dashboards = values.get("dashboards")
    if not isinstance(dashboards, dict):
        return

    release_name = str(release["release_name"])
    namespace = str(release["namespace"])
    target_ref = str(release.get("target_ref", "")).strip()
    target_dir = _target_dashboard_dir_name(target_ref)
    next_dashboards = copy.deepcopy(dashboards)
    dashboards_config_maps = copy.deepcopy(values.get("dashboardsConfigMaps") or {})
    if not isinstance(dashboards_config_maps, dict):
        dashboards_config_maps = {}

    for folder, folder_dashboards in list(dashboards.items()):
        if not isinstance(folder_dashboards, dict):
            continue
        folder_key = str(folder)
        configmap_data: dict[str, str] = {}
        next_folder = copy.deepcopy(next_dashboards.get(folder, {}))
        if not isinstance(next_folder, dict):
            next_folder = {}
        for dashboard_name, dashboard_spec in list(folder_dashboards.items()):
            if not isinstance(dashboard_spec, dict) or "json" not in dashboard_spec:
                continue
            dashboard_json = _pretty_dashboard_json(str(dashboard_spec.get("json") or ""))
            file_name = _dashboard_file_name(str(dashboard_name))
            dashboard_path = (
                state.paths.generated_dir
                / "grafana_dashboards"
                / target_dir
                / folder_key
                / file_name
            )
            _write_text(dashboard_path, dashboard_json)
            state.files.append(dashboard_path)
            configmap_data[file_name] = dashboard_json
            next_folder.pop(dashboard_name, None)

        if not configmap_data:
            continue
        configmap_name = _file_slug(f"{release_name}-{folder_key}-dashboards")
        dashboards_config_maps[folder_key] = configmap_name
        configmap_file = Path(
            f"configmap-{_file_slug(release_name)}-{_file_slug(folder_key)}-dashboards.yaml"
        )
        state.write_doc(
            configmap_file,
            _configmap_doc(
                name=configmap_name,
                namespace=namespace,
                data=configmap_data,
            ),
        )
        if next_folder:
            next_dashboards[folder] = next_folder
        else:
            next_dashboards.pop(folder, None)

    if dashboards_config_maps:
        values["dashboardsConfigMaps"] = dashboards_config_maps
    if next_dashboards:
        values["dashboards"] = next_dashboards
    else:
        values.pop("dashboards", None)


@dataclass
class _FluxRenderState:
    paths: ProjectPaths
    files: list[Path] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    repositories: list[dict[str, Any]] = field(default_factory=list)
    seen_repo_names: set[str] = field(default_factory=set)
    seen_namespaces: set[str] = field(default_factory=set)

    def add_repository_doc(self, *, repo_name: str, doc: dict[str, Any]) -> None:
        if repo_name in self.seen_repo_names:
            return
        self.repositories.append(doc)
        self.seen_repo_names.add(repo_name)

    def ensure_namespace(self, namespace: str) -> None:
        normalized = namespace.strip()
        if not normalized or normalized in self.seen_namespaces:
            return
        namespace_file = Path(f"namespace-{_file_slug(normalized)}.yaml")
        absolute_file = self.paths.flux_dir / namespace_file
        _write_text(absolute_file, yaml.safe_dump(_namespace_doc(normalized), sort_keys=False))
        self.files.append(absolute_file)
        self.resources.append(f"./{namespace_file.as_posix()}")
        self.seen_namespaces.add(normalized)

    def write_doc(self, relative_file: Path, doc: dict[str, Any]) -> None:
        absolute_file = self.paths.flux_dir / relative_file
        _write_text(absolute_file, yaml.safe_dump(doc, sort_keys=False))
        self.files.append(absolute_file)
        self.resources.append(f"./{relative_file.as_posix()}")

    def write_raw_resource(self, relative_file: Path, content: str) -> None:
        absolute_file = self.paths.flux_dir / relative_file
        _write_text(absolute_file, content)
        self.files.append(absolute_file)
        self.resources.append(f"./{relative_file.as_posix()}")

    def write_post_flux_docs(self, relative_file: Path, docs: list[dict[str, Any]]) -> None:
        absolute_file = self.paths.flux_dir / relative_file
        if not docs:
            absolute_file.unlink(missing_ok=True)
            return
        _write_text(absolute_file, _multi_doc_yaml(docs))
        self.files.append(absolute_file)

    def write_post_flux_raw(self, relative_file: Path, content: str) -> None:
        absolute_file = self.paths.flux_dir / relative_file
        if not content.strip():
            absolute_file.unlink(missing_ok=True)
            return
        _write_text(absolute_file, content)
        self.files.append(absolute_file)


def _finalize_flux_render_state(state: _FluxRenderState) -> None:
    repos_path = state.paths.flux_dir / "helm-repositories.yaml"
    if state.repositories:
        _ensure_parent(repos_path)
        repo_yaml = (
            "\n---\n".join(
                yaml.safe_dump(doc, sort_keys=False).strip() for doc in state.repositories
            )
            + "\n"
        )
        repos_path.write_text(repo_yaml, encoding="utf-8")
        state.files.append(repos_path)
        state.resources.insert(0, "./helm-repositories.yaml")
    else:
        repos_path.unlink(missing_ok=True)

    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": state.resources,
    }
    kustomization_path = state.paths.flux_dir / "kustomization.yaml"
    _write_text(kustomization_path, yaml.safe_dump(kustomization_doc, sort_keys=False))
    state.files.append(kustomization_path)


def _render_flux_app_helm_releases(
    state: _FluxRenderState, *, release_specs: list[dict[str, Any]]
) -> None:
    for release in release_specs:
        scope = release["scope"]
        release_name = release["release_name"]
        namespace = release["namespace"]
        source_name = release["source_name"]
        source_kind = release["source_kind"]
        source_url = release["source_url"]
        source_ref = release.get("source_ref", "")
        source_repo_type = release.get("source_repo_type", "default")
        if source_kind == "local_chart":
            state.ensure_namespace(namespace)
            local_chart_file_name = (
                f"post-flux-helmrender-{_file_slug(scope)}-{_file_slug(release_name)}.yaml"
            )
            state.write_post_flux_raw(
                Path(local_chart_file_name),
                _render_local_helm_chart(
                    release_name=release_name,
                    namespace=namespace,
                    chart_path=str(release["chart_ref"]),
                    values=release["values"],
                ),
            )
            continue
        if source_kind == _SOPERATOR_STATIC_SOURCE_KIND:
            state.ensure_namespace(namespace)
            rendered_chart_file_name = (
                f"post-flux-helmrender-{_file_slug(scope)}-{_file_slug(release_name)}.yaml"
            )
            state.write_post_flux_raw(
                Path(rendered_chart_file_name),
                _render_remote_helm_chart_static(
                    release_name=release_name,
                    namespace=namespace,
                    chart_ref=str(release["chart_ref"]),
                    source_url=source_url,
                    chart_version=str(release["chart_version"]),
                    values=release["values"],
                ),
            )
            continue
        if source_kind == "helm_repository":
            state.add_repository_doc(
                repo_name=source_name,
                doc=_helm_repository_doc(source_name, source_url, repo_type=source_repo_type),
            )
            flux_source_kind = "HelmRepository"
        else:
            state.add_repository_doc(
                repo_name=source_name,
                doc=_git_repository_doc(source_name, source_url, source_ref or "main"),
            )
            flux_source_kind = "GitRepository"
        state.ensure_namespace(namespace)
        _externalize_dashboard_json_values(state, release=release)
        release_file_name = f"helmrelease-{_file_slug(scope)}-{_file_slug(release_name)}.yaml"
        state.write_doc(
            Path(release_file_name),
            _helm_release_doc(
                release_name=release_name,
                namespace=namespace,
                source_name=source_name,
                source_kind=flux_source_kind,
                chart_ref=release["chart_ref"],
                chart_version=release["chart_version"],
                interval=release["interval"],
                timeout=release["timeout"] or None,
                values=release["values"],
                depends_on=release.get("depends_on") or None,
                post_render_patches=release.get("post_render_patches") or None,
                disable_wait=_release_has_managed_mysterybox_external_secret(release["values"]),
            ),
        )


def _render_local_helm_chart(
    *,
    release_name: str,
    namespace: str,
    chart_path: str,
    values: dict[str, Any],
) -> str:
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-helm-chart-") as chart_staging_dir:
        render_chart_path = _stage_local_helm_chart(chart_path, Path(chart_staging_dir))
        _build_local_helm_chart_dependencies(render_chart_path)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml") as values_file:
            yaml.safe_dump(values, values_file, sort_keys=False)
            values_file.flush()
            command = [
                "helm",
                "template",
                release_name,
                render_chart_path,
                "--namespace",
                namespace,
                "--include-crds",
                "--values",
                values_file.name,
            ]
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise ValueError(
            "local Helm chart render failed for "
            f"{chart_path}: {stderr or 'helm exited without diagnostic output'}"
        )
    rendered = result.stdout.strip()
    if not rendered:
        raise ValueError(f"local Helm chart render produced no manifests for {chart_path}")
    return _inject_local_chart_namespace(rendered, namespace=namespace)


def _remote_helm_template_chart_arg(*, source_url: str, chart_ref: str) -> str:
    if _is_oci_repo(source_url):
        repo = source_url.strip().rstrip("/")
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail == chart_ref.strip().lower():
            return repo
        return f"{repo}/{chart_ref.strip()}"
    return chart_ref


def _render_remote_helm_chart_static(
    *,
    release_name: str,
    namespace: str,
    chart_ref: str,
    source_url: str,
    chart_version: str,
    values: dict[str, Any],
) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml") as values_file:
        yaml.safe_dump(values, values_file, sort_keys=False)
        values_file.flush()
        command = [
            "helm",
            "template",
            release_name,
            _remote_helm_template_chart_arg(source_url=source_url, chart_ref=chart_ref),
            "--namespace",
            namespace,
            "--include-crds",
            "--values",
            values_file.name,
        ]
        if chart_version:
            command.extend(["--version", chart_version])
        if source_url and not _is_oci_repo(source_url):
            command.extend(["--repo", source_url])
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        source_label = source_url.strip() or chart_ref
        raise ValueError(
            "remote Helm chart static render failed for "
            f"{source_label}: {stderr or 'helm exited without diagnostic output'}"
        )
    rendered = result.stdout.strip()
    if not rendered:
        source_label = source_url.strip() or chart_ref
        raise ValueError(f"remote Helm chart static render produced no manifests for {source_label}")
    return _inject_local_chart_namespace(rendered, namespace=namespace)


def _local_chart_metadata(chart_path: str | Path) -> dict[str, Any]:
    chart_dir = Path(chart_path)
    chart_file = chart_dir / "Chart.yaml"
    if not chart_file.exists():
        return {}
    try:
        chart = yaml.safe_load(chart_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"unable to read local Helm chart metadata for {chart_path}") from exc
    return dict(chart) if isinstance(chart, Mapping) else {}


def _local_chart_dependency_metadata(chart_dir: Path) -> list[Mapping[str, Any]]:
    lock_file = chart_dir / "Chart.lock"
    if lock_file.exists():
        try:
            lock = yaml.safe_load(lock_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"unable to read local Helm chart lock for {chart_dir}") from exc
        dependencies = lock.get("dependencies") if isinstance(lock, Mapping) else None
    else:
        dependencies = _local_chart_metadata(chart_dir).get("dependencies")
    if not isinstance(dependencies, list):
        return []
    return [item for item in dependencies if isinstance(item, Mapping)]


def _local_chart_dependency_is_present(
    charts_dir: Path,
    *,
    name: str,
    version: str,
) -> bool:
    if not name or not version:
        return False
    if (charts_dir / f"{name}-{version}.tgz").is_file():
        return True
    unpacked_dir = charts_dir / name
    if not unpacked_dir.is_dir():
        return False
    metadata = _local_chart_metadata(unpacked_dir)
    return metadata.get("name") == name and str(metadata.get("version") or "") == version


def _local_chart_dependencies_are_packaged(chart_dir: Path) -> bool:
    dependencies = _local_chart_dependency_metadata(chart_dir)
    if not dependencies:
        return True
    if any(
        str(dependency.get("repository") or "").strip().startswith("file://")
        for dependency in dependencies
    ):
        return False
    charts_dir = chart_dir / "charts"
    return all(
        _local_chart_dependency_is_present(
            charts_dir,
            name=str(dependency.get("name") or "").strip(),
            version=str(dependency.get("version") or "").strip(),
        )
        for dependency in dependencies
    )


def _remove_staged_chart_dir(staged_chart_dir: Path) -> None:
    if not staged_chart_dir.exists() and not staged_chart_dir.is_symlink():
        return
    if staged_chart_dir.is_dir() and not staged_chart_dir.is_symlink():
        shutil.rmtree(staged_chart_dir)
        return
    staged_chart_dir.unlink()


def _stage_local_helm_chart(chart_path: str, staging_root: Path) -> str:
    source_chart_dir = Path(chart_path).expanduser().resolve()
    staged_chart_dir = staging_root / source_chart_dir.name
    _remove_staged_chart_dir(staged_chart_dir)
    shutil.copytree(source_chart_dir, staged_chart_dir, symlinks=False)

    staging_root = staging_root.resolve()
    for dependency in _local_chart_metadata(source_chart_dir).get("dependencies") or ():
        if not isinstance(dependency, Mapping):
            continue
        repository = str(dependency.get("repository") or "").strip()
        if not repository.startswith("file://"):
            continue
        relative_dependency_path = repository.removeprefix("file://")
        dependency_source = (source_chart_dir / relative_dependency_path).resolve()
        dependency_target = (staged_chart_dir / relative_dependency_path).resolve()
        try:
            dependency_target.relative_to(staging_root)
        except ValueError as exc:
            raise ValueError(
                f"local Helm chart dependency path escapes staging directory: {repository}"
            ) from exc
        if not dependency_source.is_dir():
            raise ValueError(f"local Helm chart dependency not found: {dependency_source}")
        if dependency_target.exists():
            continue
        dependency_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dependency_source, dependency_target, symlinks=False)
    return str(staged_chart_dir)


def _local_chart_remote_dependency_repositories(chart_dir: Path) -> list[str]:
    repositories: list[str] = []
    seen: set[str] = set()
    for dependency in _local_chart_dependency_metadata(chart_dir):
        repository = str(dependency.get("repository") or "").strip()
        if not (
            repository.startswith("http://") or repository.startswith("https://")
        ):
            continue
        if repository in seen:
            continue
        repositories.append(repository)
        seen.add(repository)
    return repositories


def _helm_repository_config_source() -> Path | None:
    configured = os.environ.get("HELM_REPOSITORY_CONFIG")
    if configured:
        return Path(configured).expanduser()

    result = subprocess.run(
        ["helm", "env", "HELM_REPOSITORY_CONFIG"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value).expanduser() if value else None


def _helm_repository_config_entries_for_urls(
    source: Path,
    required_urls: set[str],
) -> list[Mapping[str, Any]]:
    try:
        config = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(config, Mapping):
        return []
    repositories = config.get("repositories")
    if not isinstance(repositories, list):
        return []

    entries: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for repository in repositories:
        if not isinstance(repository, Mapping):
            continue
        url = str(repository.get("url") or "").strip()
        if not url or url not in required_urls or url in seen:
            continue
        entries.append(dict(repository))
        seen.add(url)
    return entries


def _seed_helm_repository_config(target: Path, repositories: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source = _helm_repository_config_source()
    entries: list[Mapping[str, Any]] = []
    if source and source.is_file():
        entries = _helm_repository_config_entries_for_urls(source, set(repositories))
    target.write_text(
        yaml.safe_dump({"repositories": entries}, sort_keys=False),
        encoding="utf-8",
    )


def _helm_repository_entries(config_path: Path) -> dict[str, str]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(config, Mapping):
        return {}
    repositories = config.get("repositories")
    if not isinstance(repositories, list):
        return {}

    entries: dict[str, str] = {}
    for repository in repositories:
        if not isinstance(repository, Mapping):
            continue
        url = str(repository.get("url") or "").strip()
        name = str(repository.get("name") or "").strip()
        if url and name and url not in entries:
            entries[url] = name
    return entries


def _helm_output(result: subprocess.CompletedProcess[str]) -> str:
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or "helm exited without diagnostic output"
    )


def _next_helm_repository_name(index: int, used_names: set[str]) -> str:
    while True:
        name = f"cxcli-local-{index}"
        if name not in used_names:
            used_names.add(name)
            return name
        index += 1


def _prepare_local_chart_helm_repositories(
    *,
    chart_path: str,
    repositories: list[str],
    repository_config: Path,
    repository_cache: Path,
) -> None:
    repository_cache.mkdir(parents=True, exist_ok=True)
    _seed_helm_repository_config(repository_config, repositories)
    configured = _helm_repository_entries(repository_config)
    used_names = set(configured.values())

    for index, repository in enumerate(repositories, start=1):
        repo_name = configured.get(repository)
        if repo_name:
            command = [
                "helm",
                "repo",
                "update",
                repo_name,
                "--repository-config",
                str(repository_config),
                "--repository-cache",
                str(repository_cache),
            ]
        else:
            repo_name = _next_helm_repository_name(index, used_names)
            command = [
                "helm",
                "repo",
                "add",
                repo_name,
                repository,
                "--force-update",
                "--repository-config",
                str(repository_config),
                "--repository-cache",
                str(repository_cache),
            ]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise ValueError(
                "local Helm chart dependency repository setup failed for "
                f"{chart_path}: {_helm_output(result)}"
            )


def _run_local_helm_dependency_build(
    *,
    chart_path: str,
    chart_dir: Path,
    repository_flags: list[str] | None = None,
) -> None:
    command = [
        "helm",
        "dependency",
        "build",
        "--skip-refresh",
        *(repository_flags or ()),
        str(chart_dir),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise ValueError(
            "local Helm chart dependency build failed for "
            f"{chart_path}: {_helm_output(result)}"
        )


def _build_local_helm_chart_dependencies(chart_path: str) -> None:
    chart_dir = Path(chart_path)
    chart = _local_chart_metadata(chart_dir)
    if not chart.get("dependencies"):
        return
    if _local_chart_dependencies_are_packaged(chart_dir):
        return

    remote_repositories = _local_chart_remote_dependency_repositories(chart_dir)
    if remote_repositories:
        with tempfile.TemporaryDirectory(
            prefix="nebius-cxcli-helm-repositories-"
        ) as repository_temp_dir:
            repository_root = Path(repository_temp_dir)
            repository_config = repository_root / "repositories.yaml"
            repository_cache = repository_root / "repository-cache"
            _prepare_local_chart_helm_repositories(
                chart_path=chart_path,
                repositories=remote_repositories,
                repository_config=repository_config,
                repository_cache=repository_cache,
            )
            _run_local_helm_dependency_build(
                chart_path=chart_path,
                chart_dir=chart_dir,
                repository_flags=[
                    "--repository-config",
                    str(repository_config),
                    "--repository-cache",
                    str(repository_cache),
                ],
            )
        return

    _run_local_helm_dependency_build(chart_path=chart_path, chart_dir=chart_dir)


def _render_post_flux_mysterybox_eso_resources(
    state: _FluxRenderState,
    *,
    config: Any,
    target_ref: str,
    component_output_values: dict[str, Any] | None,
) -> None:
    objects = mysterybox_eso_extra_objects_for_target(
        config,
        target_ref=target_ref,
        component_output_values=component_output_values or {},
    )
    state.write_post_flux_docs(Path("post-flux-mysterybox-eso.yaml"), objects)


def render_flux(
    config: Any,
    paths: ProjectPaths,
    *,
    component_output_values: dict[str, Any] | None = None,
) -> list[Path]:
    legacy_dirs = [
        path
        for path in (paths.flux_dir / "apps", paths.flux_dir / "sources")
        if path.exists() and path.is_dir()
    ]
    if legacy_dirs:
        locations = ", ".join(str(path) for path in legacy_dirs)
        raise ValueError(
            "Unsupported legacy Flux layout detected. "
            "Use only flat files under generated/flux and remove: "
            f"{locations}"
        )

    release_specs = _configured_app_release_specs(
        config,
        component_output_values=component_output_values,
    )
    cluster_target_refs = enabled_cluster_target_refs(config)
    if cluster_target_refs:
        written: list[Path] = []
        release_specs_by_target: dict[str, list[dict[str, Any]]] = {
            target_ref: [] for target_ref in cluster_target_refs
        }
        for item in release_specs:
            release_specs_by_target.setdefault(str(item.get("target_ref", "")).strip(), []).append(
                item
            )
        for target_ref in cluster_target_refs:
            target_paths = replace(paths, flux_dir=flux_target_dir(paths, target_ref))
            state = _FluxRenderState(paths=target_paths)
            _render_flux_app_helm_releases(
                state,
                release_specs=release_specs_by_target.get(target_ref, []),
            )
            _render_post_flux_mysterybox_eso_resources(
                state,
                config=config,
                target_ref=target_ref,
                component_output_values=component_output_values,
            )
            _finalize_flux_render_state(state)
            written.extend(state.files)
        return written

    state = _FluxRenderState(paths=paths)
    _render_flux_app_helm_releases(state, release_specs=release_specs)
    _render_post_flux_mysterybox_eso_resources(
        state,
        config=config,
        target_ref="",
        component_output_values=component_output_values,
    )
    _finalize_flux_render_state(state)
    return state.files


__all__ = ["render_flux"]
