"""Resolve target-scoped compatibility subjects from the selected catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .compatibility_matrix import assess, digest, load_matrix, require_admitted, validate_selection
from .compatibility_schema import kubernetes_minor, required_adapters
from .component_instances import component_instance_id
from .component_sources import TFModuleSource, load_component_sources
from .runtime_config import to_plain_data


def selected_inventory(config: Any, *, matrix: Mapping | None = None) -> list[dict[str, Any]]:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        raise ValueError("Compatibility requires a configuration mapping")
    matrix = matrix or load_matrix()
    validate_selection(payload)
    sources = load_component_sources()
    modules = {row.module: row for row in sources.tf_modules}
    charts = {row.name: row for row in sources.helm_charts}
    infra = payload.get("infra", {}).get("components", [])
    kubernetes = {
        component_instance_id(row): str(
            row.get("inputs", {}).get("cluster", {}).get("k8s_version", "")
        )
        for row in infra
        if row.get("enabled") and row.get("id") == "mk8s"
    }
    for target in payload.get("deploy", {}).get("targets", []):
        version = target.get("soperator_desired_platform", {}).get("kubernetes_version")
        if version:
            kubernetes[str(target.get("instance_id") or target.get("id"))] = str(version)
    inventory: list[dict[str, Any]] = []
    for kind, items in (("terraform", infra), ("helm", payload.get("apps", {}).get("charts", []))):
        for row in items:
            component = str(row.get("id", ""))
            target = component_instance_id(row)
            catalog = modules.get(component) if kind == "terraform" else charts.get(component)
            source = str(
                row.get("source" if kind == "terraform" else "repo")
                or (
                    catalog.source
                    if isinstance(catalog, TFModuleSource)
                    else catalog.repo
                    if catalog and not isinstance(catalog, TFModuleSource)
                    else ""
                )
            )
            version = str(row.get("version") or (catalog.version if catalog else "") or "")
            distribution = next(
                (
                    key
                    for key, value in matrix["distributions"].items()
                    if value["component_id"] == component and value["source"] == source.rstrip("/")
                ),
                "unknown",
            )
            if component == "soperator":
                distribution = "nebius-soperator"
                source = matrix["distributions"][distribution]["source"]
            subject = {
                "instance_id": target,
                "component_id": component,
                "kind": kind,
                "owner": "soperator" if component == "soperator" else "cxcli",
                "distribution": distribution,
                "source": source,
                "enabled": row.get("enabled") is True,
                "chart_version" if kind == "helm" else "module_version": version,
                "kubernetes_version": kubernetes.get(target, ""),
                "kubernetes_minor": kubernetes_minor(kubernetes.get(target, "")),
                "effective_inputs_sha256": digest(
                    row.get("values" if kind == "helm" else "inputs", {})
                ),
            }
            if component == "soperator":
                subject["release"] = version
            if component == "mk8s":
                subject["gpu_enabled"] = any(
                    chart.get("enabled") is True
                    and chart.get("id") == "nvidia-gpu-operator"
                    and component_instance_id(chart) == target
                    for chart in payload.get("apps", {}).get("charts", [])
                )
            subject["required_adapters"] = required_adapters(subject, matrix)
            # A chart version is intentionally NOT copied to application_version.
            inventory.append(subject)
    selections = payload.get("compatibility", {}).get("targets", {})
    for target in {row["instance_id"] for row in inventory}:
        profile = (
            "soperator"
            if any(
                row["instance_id"] == target
                and row["component_id"] == "soperator"
                and row["enabled"]
                for row in inventory
            )
            else "mk8s"
        )
        operators = [
            row
            for row in inventory
            if row["instance_id"] == target
            and row["enabled"]
            and row["component_id"] in {"nvidia-gpu-operator", "nvidia-network-operator"}
        ]
        choice = selections.get(target)
        if choice is None and operators:
            profile = (
                "soperator"
                if any(
                    row["instance_id"] == target
                    and row["component_id"] == "soperator"
                    and row["enabled"]
                    for row in inventory
                )
                else "mk8s"
            )
            choice = {"version_set": matrix["selection"]["profile_defaults"][profile]}
        name = choice.get("version_set") if choice else None
        if name is None:
            if choice is not None:
                for subject in operators:
                    authored = next(
                        row
                        for row in payload["apps"]["charts"]
                        if row["id"] == subject["component_id"]
                        and component_instance_id(row) == target
                    )
                    if not authored.get("version"):
                        raise ValueError(
                            "Custom compatibility selections require explicit exact operator versions"
                        )
            continue
        version_set = matrix["version_sets"].get(name)
        if version_set is None:
            raise ValueError(f"Unknown compatibility version set: {name}")
        if profile not in version_set["profiles"]:
            raise ValueError(f"Version set {name} does not apply to {profile}")
        for subject in operators:
            pin = version_set["pins"].get(subject["component_id"])
            authored = next(
                row
                for row in payload["apps"]["charts"]
                if row["id"] == subject["component_id"] and component_instance_id(row) == target
            )
            if pin and not authored.get("version"):
                subject["chart_version"] = pin["chart_version"]
            if (
                pin is None
                or subject["chart_version"] != pin["chart_version"]
                or subject["distribution"] != pin["distribution"]
            ):
                raise ValueError(
                    f"{subject['component_id']}@{target} conflicts with version set {name}; select exact custom versions with version_set: null"
                )
            subject["version_set"] = name
    return inventory


def assess_config(
    config: Any,
    *,
    matrix: Mapping | None = None,
    receipts: Sequence[Mapping] = (),
    admission: bool = False,
) -> dict[str, Any]:
    matrix = matrix or load_matrix()
    report = assess(
        selected_inventory(config, matrix=matrix),
        matrix=matrix,
        receipts=receipts,
        admission=admission,
    )
    require_admitted(report)
    return report


def materialize_selection(payload: dict[str, Any]) -> None:
    for subject in selected_inventory(payload):
        if subject.get("version_set"):
            for row in payload["apps"]["charts"]:
                if (
                    row["id"] == subject["component_id"]
                    and component_instance_id(row) == subject["instance_id"]
                    and not row.get("version")
                ):
                    row["version"] = subject["chart_version"]
            payload.setdefault("compatibility", {}).setdefault("targets", {}).setdefault(
                subject["instance_id"], {"version_set": subject["version_set"]}
            )
