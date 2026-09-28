"""Closed source and render delta for the native observability install repair."""

from __future__ import annotations

import copy
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from .deployment_plan import soperator_target
from .soperator_install_render_repair import GRAPH_FILE, OUTER_FILE, VALUES_FILE, _documents

REASON = "install-native-observability-v1"
NATIVE_VALUES = {"dcgmExporter": {"values": {"validateToolkit": False}}}


def _patches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Frozen manifests canonicalize mapping order, including embedded JSON Patch.
    return [{**row, "patch": yaml.safe_load(row["patch"])} for row in rows]


def bound_artifact_files(
    files: Mapping[str, bytes], chart_inputs: Mapping[str, Any]
) -> dict[str, bytes]:
    """Replay the frozen OCI binder over an isolated predecessor copy."""
    from .compatibility_artifacts import bind_flux_artifacts
    from .deployment_state import _safe_relative
    from .soperator_install_render_repair import _files

    with tempfile.TemporaryDirectory(prefix="cxcli-observability-artifacts-") as directory:
        root = Path(directory)
        for name, data in files.items():
            path = root / _safe_relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)
        bind_flux_artifacts(SimpleNamespace(flux_dir=root), chart_inputs)
        return _files(root)


def corrected_config(previous: Mapping[str, Any]) -> dict[str, Any]:
    """Recognize only uncustomized obsolete exporter defaults, never arbitrary edits."""
    result = copy.deepcopy(dict(previous))
    selected = soperator_target(result)
    if selected is None:
        raise RuntimeError("Observability recovery requires one Soperator target")
    ref, row = selected
    values = row["values"]
    obsolete = values.get("soperator-dcgm-exporter")
    if (
        "observability" in values
        or not isinstance(obsolete, dict)
        or set(obsolete) != {"enabled", "validateToolkit", "fullnameOverride", "serviceMonitor"}
        or obsolete["enabled"] is not False
        or obsolete["validateToolkit"] is not False
        or obsolete["fullnameOverride"] != "soperator-dcgm-exporter"
        or obsolete["serviceMonitor"] != {"enabled": False}
        or any(
            p.startswith(("/soperator-dcgm-exporter", "/observability"))
            for p in row.get("values-explicit-paths", [])
        )
    ):
        raise RuntimeError("Observability recovery is outside the obsolete default correction")

    def replace_values(
        chart: Mapping[str, Any], category: str, replacement: dict[str, Any]
    ) -> None:
        before = copy.deepcopy(chart["values"])
        for key, alias in result["apps"].get(category, {}).items():
            if alias.get("target_ref") == ref and all(alias.get(k) == v for k, v in before.items()):
                result["apps"][category][key] = {
                    **{k: v for k, v in alias.items() if k not in before},
                    **copy.deepcopy(replacement),
                }
        chart["values"].clear()
        chart["values"].update(replacement)

    replacement = {k: v for k, v in values.items() if k != "soperator-dcgm-exporter"}
    replacement["observability"] = copy.deepcopy(NATIVE_VALUES)
    replace_values(row, "slurm", replacement)
    gpu = [
        r
        for r in result["apps"]["charts"]
        if r.get("enabled") and r.get("id") == "nvidia-gpu-operator" and r.get("target_ref") == ref
    ]
    if len(gpu) != 1 or "dcgmExporter" in gpu[0]["values"]:
        raise RuntimeError("Observability recovery requires the unchanged ordinary GPU exporter")
    replace_values(gpu[0], "platform", {**gpu[0]["values"], "dcgmExporter": {"enabled": False}})
    return result


def is_observability_correction(previous: Mapping[str, Any], desired: Mapping[str, Any]) -> bool:
    """Select the closed source correction; full recovery admission remains mandatory."""
    try:
        corrected = corrected_config(previous)
    except RuntimeError:
        return False
    return corrected == desired


def validate_render_transition(
    previous: Mapping[str, bytes],
    replacement: Mapping[str, bytes],
    *,
    frozen: Any,
    config: Mapping[str, Any],
    target_ref: str,
    render_inputs: Mapping[str, Any],
    chart_inputs: Mapping[str, Any] | None = None,
) -> None:
    """Rebuild the precise successor from frozen upstream defaults and prior values."""
    from .flux_render import _materialize_soperator_observability_values
    from .soperator_flux_graph import (
        render_soperator_flux_graph_documents,
        soperator_graph_post_render_patches,
    )
    from .soperator_values import with_frozen_observability

    previous = bound_artifact_files(previous, chart_inputs or {})
    if previous.keys() != replacement.keys():
        raise RuntimeError("Observability recovery changed the artifact inventory")
    cm = _documents(previous[VALUES_FILE])
    outer = _documents(previous[OUTER_FILE])
    after_cm = _documents(replacement[VALUES_FILE])
    after_outer = _documents(replacement[OUTER_FILE])
    if any(len(docs) != 1 for docs in (cm, outer, after_cm, after_outer)):
        raise RuntimeError("Observability recovery requires unambiguous umbrella values")
    before = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != before:
        raise RuntimeError("Observability recovery predecessor values disagree")
    adapter_name = "soperator-nebius-adapter.yaml"
    adapter = _documents(previous[adapter_name])
    lock = frozen.snapshot
    expected = copy.deepcopy(before)
    expected["observability"] = with_frozen_observability({"observability": NATIVE_VALUES}, frozen)[
        "observability"
    ]
    selected = soperator_target(config)
    if selected is None:
        raise RuntimeError("Observability recovery lost its source target")
    source_values = selected[1]["values"]
    bound = {
        "clusterName": source_values.get("clusterName", "soperator"),
        "observability": expected["observability"],
    }
    _materialize_soperator_observability_values(
        payload=config,
        values=bound,
        target_ref=target_ref,
        release=lock.release,
        resolved_component_outputs=render_inputs,
    )
    if yaml.safe_load(after_cm[0]["data"]["values.yaml"]) != expected:
        raise RuntimeError("Observability recovery differs from frozen native values")
    cm[0]["data"]["values.yaml"] = after_cm[0]["data"]["values.yaml"]
    if cm != after_cm:
        raise RuntimeError("Observability recovery changed the values wrapper")
    from .soperator_release_graph import render_soperator_release_graph

    old_graph = render_soperator_release_graph(lock, Path(frozen.source.source_dir), before)
    new_graph = render_soperator_release_graph(lock, Path(frozen.source.source_dir), expected)
    old_patches = soperator_graph_post_render_patches(
        lock, before, release_graph=old_graph, adapter_documents=adapter
    )
    new_patches = soperator_graph_post_render_patches(
        lock, expected, release_graph=new_graph, adapter_documents=adapter
    )
    patches = outer[0]["spec"]["postRenderers"][0]["kustomize"]["patches"]
    if not old_patches or _patches(patches[-len(old_patches) :]) != _patches(old_patches):
        raise RuntimeError("Observability recovery lost frozen predecessor patches")
    patches[-len(old_patches) :] = new_patches
    outer[0]["spec"]["values"] = expected
    for documents in (outer, after_outer):
        for renderer in documents[0]["spec"].get("postRenderers", []):
            kustomize = renderer.get("kustomize", {})
            if "patches" in kustomize:
                kustomize["patches"] = _patches(kustomize["patches"])
    if outer != after_outer:
        raise RuntimeError("Observability recovery changed unrelated umbrella settings")
    for content, values, selected_graph in (
        (previous, before, old_graph),
        (replacement, expected, new_graph),
    ):
        if _documents(content[GRAPH_FILE]) != render_soperator_flux_graph_documents(
            lock, values, release_graph=selected_graph, adapter_documents=adapter
        ):
            raise RuntimeError("Observability recovery changed the frozen release graph")
    gpu_name = "ordinary/helmrelease-platform-gpu-operator.yaml"
    gpu = _documents(previous[gpu_name])
    if len(gpu) != 1 or "dcgmExporter" in gpu[0]["spec"]["values"]:
        raise RuntimeError("Observability recovery lost the ordinary exporter predecessor")
    gpu[0]["spec"]["values"]["dcgmExporter"] = {"enabled": False}
    if gpu != _documents(replacement[gpu_name]):
        raise RuntimeError("Observability recovery changed unrelated ordinary applications")
    changed = {VALUES_FILE, OUTER_FILE, GRAPH_FILE, gpu_name}
    if any(previous[name] != replacement[name] for name in previous.keys() - changed):
        raise RuntimeError("Observability recovery changed unrelated artifacts")
