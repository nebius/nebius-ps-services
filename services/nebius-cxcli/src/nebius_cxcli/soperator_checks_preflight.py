"""Pre-mutation checks admission and native transport contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .deploy_targets import flux_target_dir
from .flux_ops import _staged_soperator_outer_release, rendered_soperator_graph_contract
from .paths import ProjectPaths
from .soperator_adapter import compile_upstream_soperator_values
from .soperator_checks_login import bind_checks_login
from .soperator_checks_phase import admission_partition_configuration
from .soperator_checks_policy import compile_checks_policy
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_source import ensure_soperator_release_source


def _soperator_upgrade_expected_static_slurm_nodes(
    values: Mapping[str, Any],
) -> tuple[str, ...]:
    """Derive the exact Slurm node names rendered by upstream static NodeSets."""

    nodesets_chart = values.get("nodesets")
    nodesets_overrides = (
        nodesets_chart.get("overrideValues") if isinstance(nodesets_chart, Mapping) else None
    )
    nodesets = (
        nodesets_overrides.get("nodesets") if isinstance(nodesets_overrides, Mapping) else None
    )
    if not isinstance(nodesets, list):
        return ()
    expected: list[str] = []
    for index, item in enumerate(nodesets):
        if not isinstance(item, Mapping):
            raise RuntimeError(f"Soperator NodeSet values[{index}] must be a mapping")
        name = item.get("name", "")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            raise RuntimeError(f"Soperator NodeSet values[{index}] has no name")
        replicas = item.get("replicas")
        if isinstance(replicas, bool):
            raise RuntimeError(f"Soperator NodeSet {name!r} has an invalid replica count")
        try:
            replica_count = int(str(replicas))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Soperator NodeSet {name!r} has an invalid replica count") from exc
        if replica_count <= 0:
            raise RuntimeError(f"Soperator NodeSet {name!r} must have at least one replica")
        expected.extend(f"{name}-{ordinal}" for ordinal in range(replica_count))
    if len(expected) != len(set(expected)):
        raise RuntimeError("Soperator NodeSet values render duplicate Slurm node names")
    return tuple(expected)


def _rendered_soperator_upstream_values(flux_dir: Path) -> Mapping[str, Any]:
    values_path = flux_dir / "configmap-terraform-fluxcd-values.yaml"
    values_documents = list(yaml.safe_load_all(values_path.read_text(encoding="utf-8")))
    if len(values_documents) != 1 or not isinstance(values_documents[0], Mapping):
        raise ValueError("rendered Soperator values ConfigMap is invalid")
    values_data = values_documents[0].get("data")
    values_raw = values_data.get("values.yaml") if isinstance(values_data, Mapping) else None
    if not isinstance(values_raw, str):
        raise ValueError("rendered Soperator values ConfigMap has no values.yaml string")
    soperator_values = yaml.safe_load(values_raw)
    if not isinstance(soperator_values, Mapping):
        raise ValueError("rendered Soperator values.yaml must be a mapping")
    return soperator_values


def _soperator_checks_kubernetes_payload(args: list[str], stdout: str) -> Mapping[str, Any]:
    # kubectl create flattens a List and prints a JSON object per created resource.
    # Mutation responses are unused: exact identities are re-read independently.
    if args[0] == "create":
        return {}
    payload = json.loads(stdout or "{}")
    if not isinstance(payload, Mapping):
        raise RuntimeError("invalid Soperator checks Kubernetes evidence")
    return payload


def _preflight_soperator_install_checks(paths: ProjectPaths, target_ref: str) -> None:
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    source = ensure_soperator_release_source(snapshot)
    _preflight_soperator_checks(
        _rendered_soperator_upstream_values(flux_target_dir(paths, target_ref)),
        source_dir=Path(source.source_dir),
        installing=True,
    )


def _preflight_soperator_upgrade_checks(values: Mapping[str, Any], frozen: Any) -> None:
    upstream_values, _ = compile_upstream_soperator_values(values, release=frozen.snapshot)
    upstream_values = bind_checks_login(upstream_values, Path(frozen.source.source_dir))
    _preflight_soperator_checks(
        upstream_values, source_dir=Path(frozen.source.source_dir), installing=False
    )


def _soperator_checks_target_writers(flux_dir: Path) -> tuple[tuple[str, str], ...]:
    graph = rendered_soperator_graph_contract(flux_dir)
    if not isinstance(graph, Mapping):
        raise RuntimeError("Soperator checks handoff has no frozen target graph")
    writers = {(str(row["namespace"]), str(row["releaseName"])) for row in graph["releases"]}
    documents = [
        doc
        for path in flux_dir.glob("*.yaml")
        for doc in yaml.safe_load_all(path.read_text())
        if isinstance(doc, dict)
    ]
    outer = _staged_soperator_outer_release(documents, graph["releases"])
    writers.add((outer["metadata"]["namespace"], outer["metadata"]["name"]))
    return tuple(sorted(writers))


def _preflight_soperator_checks(
    values: Mapping[str, Any], *, source_dir: Path, installing: bool
) -> Any:
    """Reject unsupported lifecycle execution before provisioning or scheduling changes."""
    policy = compile_checks_policy(source_dir, values)
    if installing:
        policy.effective_values(values, installing=True)
    nodesets = values.get("nodesets", {}).get("overrideValues", {}).get("nodesets", [])
    if any(row.get("replicas") is None for row in nodesets):
        raise ValueError("Soperator check acceptance requires explicit worker replicas")
    if not _soperator_upgrade_expected_static_slurm_nodes(values):
        raise ValueError("Soperator check acceptance requires a nonempty worker inventory")
    partitions = (
        values.get("slurmCluster", {}).get("overrideValues", {}).get("partitionConfiguration", {})
    )
    admission_partition_configuration(partitions, checks_open=True)
    if partitions.get("configType") == "structured":
        hidden = [row for row in partitions.get("partitions", []) if row.get("name") == "hidden"]
        expected_refs = {row["name"] for row in nodesets}
        if len(hidden) != 1 or (
            hidden[0].get("isAll") is not True
            and set(hidden[0].get("nodeSetRefs", [])) != expected_refs
        ):
            raise ValueError(
                "upstream checks require the hidden partition to cover every worker NodeSet"
            )
        config = dict(re.findall(r"(\w+)=(\S+)", str(hidden[0].get("config") or "")))
        if config.get("Hidden") != "YES" or config.get("State", "UP") != "UP":
            raise ValueError("upstream checks require an enabled hidden partition")
    return policy
