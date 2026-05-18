"""General NFS CSI app materialization for VM-backed NFS exports."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .component_defaults import resolve_component_defaults
from .component_instances import (
    INSTANCE_ID_FIELD,
    component_instance_id,
    component_instance_label,
    component_type_id,
    normalize_component_token,
)
from .component_wiring import component_output_ref
from .components import ComponentEntry, component_entries
from .deploy_targets import (
    TARGET_REF_FIELD,
    app_chart_target_ref,
    enabled_cluster_target_refs,
    is_auto_target_scoped_app_instance_id,
    target_scoped_app_instance_id,
)
from .runtime_config import to_plain_data

NFS_INFRA_ID = "nfs"
NFS_CSI_APP_ID = "csi-driver-nfs"
NFS_TARGET_REF_INPUT = "kubernetes_target_ref"


def _as_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    payload = to_plain_data(value)
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _app_chart_rows(payload: Mapping[str, Any]) -> list[Any]:
    apps = payload.get("apps")
    charts = _mapping(apps).get("charts")
    return charts if isinstance(charts, list) else []


def _app_chart_rows_mutable(payload: dict[str, Any]) -> list[Any]:
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        apps = {}
        payload["apps"] = apps
    charts = apps.get("charts")
    if not isinstance(charts, list):
        charts = []
        apps["charts"] = charts
    return charts


def _enabled_nfs_rows(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    infra = payload.get("infra")
    components = _mapping(infra).get("components")
    if not isinstance(components, list):
        return ()
    rows: list[Mapping[str, Any]] = []
    for row in components:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != NFS_INFRA_ID:
            continue
        rows.append(row)
    return tuple(rows)


def _nfs_input_target_ref(row: Mapping[str, Any]) -> str:
    inputs = row.get("inputs")
    return normalize_component_token(_mapping(inputs).get(NFS_TARGET_REF_INPUT))


def _nfs_csi_bindings(payload: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    target_refs = enabled_cluster_target_refs(payload)
    if not target_refs:
        return ()
    valid_target_refs = set(target_refs)
    nfs_rows = _enabled_nfs_rows(payload)
    if not nfs_rows:
        return ()

    bindings: list[tuple[str, str]] = []
    unscoped_nfs_ids: list[str] = []
    for row in nfs_rows:
        nfs_instance_id = component_instance_id(row)
        if not nfs_instance_id:
            continue
        target_ref = _nfs_input_target_ref(row)
        if target_ref:
            if target_ref in valid_target_refs:
                bindings.append((target_ref, nfs_instance_id))
            continue
        unscoped_nfs_ids.append(nfs_instance_id)

    if len(nfs_rows) == 1 and unscoped_nfs_ids:
        nfs_instance_id = unscoped_nfs_ids[0]
        bound_targets = {target_ref for target_ref, _ in bindings}
        for target_ref in target_refs:
            if target_ref not in bound_targets:
                bindings.append((target_ref, nfs_instance_id))
    return tuple(bindings)


def nfs_instance_id_for_target(payload: Mapping[str, Any], *, target_ref: str) -> str:
    """Resolve the NFS infra instance that should back one MK8s target."""
    normalized_target = normalize_component_token(target_ref)
    for current_target_ref, nfs_instance_id in _nfs_csi_bindings(payload):
        if current_target_ref == normalized_target:
            return nfs_instance_id
    return ""


def nfs_csi_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    payload = _as_payload(payload_or_config)
    refs: list[str] = []
    seen: set[str] = set()
    for target_ref, _nfs_instance_id in _nfs_csi_bindings(payload):
        if target_ref not in seen:
            seen.add(target_ref)
            refs.append(target_ref)
    return tuple(refs)


def nfs_csi_binding_issues(payload_or_config: Any) -> tuple[str, ...]:
    payload = _as_payload(payload_or_config)
    target_refs = set(enabled_cluster_target_refs(payload))
    if not target_refs:
        return ()
    available = ", ".join(sorted(target_refs)) or "(none)"
    issues: list[str] = []
    target_to_nfs: dict[str, str] = {}
    for row in _enabled_nfs_rows(payload):
        instance_id = component_instance_id(row)
        target_ref = _nfs_input_target_ref(row)
        if not target_ref:
            continue
        label = component_instance_label(NFS_INFRA_ID, instance_id)
        if target_ref not in target_refs:
            issues.append(
                f"infra.components[{label}].inputs.{NFS_TARGET_REF_INPUT} must reference "
                f"one enabled MK8s target: {available}"
            )
            continue
        previous_nfs = target_to_nfs.get(target_ref)
        if previous_nfs and previous_nfs != instance_id:
            issues.append(
                f"infra.components[{label}].inputs.{NFS_TARGET_REF_INPUT} duplicates "
                f"target '{target_ref}' already bound by infra.components["
                f"{component_instance_label(NFS_INFRA_ID, previous_nfs)}]"
            )
            continue
        target_to_nfs[target_ref] = instance_id
    if len(_enabled_nfs_rows(payload)) > 1:
        unscoped = [
            component_instance_label(NFS_INFRA_ID, component_instance_id(row))
            for row in _enabled_nfs_rows(payload)
            if not _nfs_input_target_ref(row)
        ]
        if unscoped:
            issues.append(
                "multiple enabled NFS components require "
                f"inputs.{NFS_TARGET_REF_INPUT} on each NFS row; missing on " + ", ".join(unscoped)
            )
    return tuple(issues)


def nfs_csi_terraform_output_specs(payload_or_config: Any) -> tuple[dict[str, str], ...]:
    payload = _as_payload(payload_or_config)
    specs: list[dict[str, str]] = []
    for target_ref in nfs_csi_target_refs(payload):
        nfs_instance_id = nfs_instance_id_for_target(payload, target_ref=target_ref)
        if not nfs_instance_id:
            continue
        for output_name in ("server_ip", "export_path", "mount_options"):
            specs.append(
                {
                    "component_id": NFS_INFRA_ID,
                    "instance_id": nfs_instance_id,
                    "output_name": output_name,
                    "source_ref": component_output_ref(nfs_instance_id, output_name),
                }
            )
    return tuple(specs)


def _chart_source_parts(entry: ComponentEntry) -> tuple[str, str]:
    source = str(entry.source or "").strip().rstrip("/")
    if not source:
        return "", ""
    if source.startswith("oci://"):
        return source, source.rsplit("/", maxsplit=1)[-1]
    if "/" not in source:
        return "", source
    return source.rsplit("/", maxsplit=1)[0], source.rsplit("/", maxsplit=1)[-1]


def _canonical_chart_repo(*, chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    chart = chart_name.strip().strip("/")
    if not repo:
        return repo
    if repo.startswith("oci://") and chart and repo.rsplit("/", maxsplit=1)[-1] == chart:
        return repo
    return repo


def _normalized_group(entry: ComponentEntry) -> str:
    raw_group = str(entry.group or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"


def _apply_app_entry_defaults(*, row: dict[str, Any], entry: ComponentEntry) -> None:
    chart_repo = str(entry.chart_repo or "").strip()
    chart_name = str(entry.chart_name or "").strip()
    if not chart_name:
        chart_repo, chart_name = _chart_source_parts(entry)
    if not chart_name:
        chart_name = entry.id
    if not str(row.get("repo", "")).strip():
        row["repo"] = _canonical_chart_repo(chart_repo=chart_repo, chart_name=chart_name)
    if not str(row.get("version", "")).strip() and entry.version:
        row["version"] = str(entry.version)
    if not str(row.get("namespace", "")).strip():
        row["namespace"] = str(entry.default_namespace or "").strip() or entry.id
    if not str(row.get("release-name", "")).strip():
        row["release-name"] = str(entry.default_release_name or "").strip() or entry.id
    if not str(row.get("group", "")).strip():
        row["group"] = _normalized_group(entry)
    if not isinstance(row.get("values"), dict):
        row["values"] = {}
    if entry.defaults:
        resolved = resolve_component_defaults(
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            include_shared=False,
        )
        row.clear()
        row.update(resolved)


def _new_nfs_csi_app_row(entry: ComponentEntry, *, target_ref: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": entry.id,
        INSTANCE_ID_FIELD: target_scoped_app_instance_id(entry.id, target_ref=target_ref),
        "group": _normalized_group(entry),
        "enabled": True,
        "repo": "",
        "version": "",
        "namespace": "",
        "release-name": "",
        "values": {},
    }
    _apply_app_entry_defaults(row=row, entry=entry)
    return row


def _effective_app_target_ref(row: Mapping[str, Any], *, target_refs: tuple[str, ...]) -> str:
    target_ref = app_chart_target_ref(row)
    if target_ref:
        return target_ref
    instance_id = component_instance_id(row)
    return instance_id if instance_id in target_refs else ""


def ensure_nfs_csi_app_rows(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> bool:
    """Ensure VM-backed NFS is consumable by any enabled built-in MK8s target."""
    payload = _as_payload(payload_or_config)
    if not payload:
        return False
    target_refs = nfs_csi_target_refs(payload)
    if not target_refs:
        return False
    entries = app_entries if app_entries is not None else component_entries("apps")
    entry = next((item for item in entries if item.id == NFS_CSI_APP_ID), None)
    if entry is None:
        return False

    charts = _app_chart_rows_mutable(payload)
    rows_for_app = [
        row for row in charts if isinstance(row, dict) and component_type_id(row) == NFS_CSI_APP_ID
    ]
    changed = False
    for target_ref in target_refs:
        existing = next(
            (
                row
                for row in rows_for_app
                if _effective_app_target_ref(row, target_refs=target_refs) == target_ref
            ),
            None,
        )
        if existing is None:
            existing = next(
                (
                    row
                    for row in rows_for_app
                    if not app_chart_target_ref(row) and not bool(row.get("enabled", False))
                ),
                None,
            )
        if existing is None:
            row = _new_nfs_csi_app_row(entry, target_ref=target_ref)
            charts.append(row)
            rows_for_app.append(row)
            changed = True
            continue

        before = copy.deepcopy(existing)
        previous_target_ref = app_chart_target_ref(existing)
        existing["enabled"] = True
        if not str(existing.get("id", "")).strip():
            existing["id"] = entry.id
        if (
            not previous_target_ref
            or not component_instance_id(existing)
            or is_auto_target_scoped_app_instance_id(
                existing.get(INSTANCE_ID_FIELD),
                app_id=entry.id,
                target_ref=target_ref,
            )
        ):
            existing[INSTANCE_ID_FIELD] = target_scoped_app_instance_id(
                entry.id,
                target_ref=target_ref,
            )
        if TARGET_REF_FIELD in existing:
            existing.pop(TARGET_REF_FIELD, None)
        _apply_app_entry_defaults(row=existing, entry=entry)
        if existing != before:
            changed = True
    return changed


__all__ = [
    "NFS_CSI_APP_ID",
    "NFS_INFRA_ID",
    "NFS_TARGET_REF_INPUT",
    "ensure_nfs_csi_app_rows",
    "nfs_csi_binding_issues",
    "nfs_csi_terraform_output_specs",
    "nfs_csi_target_refs",
    "nfs_instance_id_for_target",
]
