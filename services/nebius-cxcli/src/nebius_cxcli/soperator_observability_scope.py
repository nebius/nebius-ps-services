"""Qualify only native graph changes caused by owning telemetry export."""

import base64
import copy
import json
from collections.abc import Mapping
from typing import Any

import yaml

from .soperator_flux_graph import SOPERATOR_GRAPH_CONFIGMAP

WRITER = "soperator-fluxcd-tsa-token-writer"
OWNED_WRITER = "cxcli-" + WRITER
JAIL_LOGS = "soperator-fluxcd-opentelemetry-collector-jail-logs"


def graph_contract(generation: Any, target: str) -> dict[str, Any]:
    for path, encoded in generation.files.items():
        if path.startswith(f"flux/targets/{target}/") and path.endswith(".yaml"):
            for doc in yaml.safe_load_all(base64.b64decode(encoded)):
                if (
                    isinstance(doc, dict)
                    and doc.get("kind") == "ConfigMap"
                    and doc.get("metadata", {}).get("name") == SOPERATOR_GRAPH_CONFIGMAP
                ):
                    return json.loads(doc["data"]["graph.json"])
    return {}


def normalized_graph(contract: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(contract))
    result["releases"] = [
        row for row in result.get("releases", []) if row["upstreamReleaseName"] != WRITER
    ]
    for row in result["releases"]:
        row.pop("stage", None)  # Derived from the dependencies, which stay compared.
        row["dependencies"] = [name for name in row["dependencies"] if name != OWNED_WRITER]
    readiness = result.get("readiness", {})
    if "telemetryReleases" in readiness:
        readiness["telemetryReleases"] = [
            name for name in readiness["telemetryReleases"] if name != OWNED_WRITER
        ]
    return result


def retired_writer_sources(source: Any, candidate: Any, target: str) -> set[tuple[str, str]]:
    before, after = graph_contract(source, target), graph_contract(candidate, target)
    if not before and not after:
        return set()
    if normalized_graph(before) != normalized_graph(after):
        raise RuntimeError(
            "Observability routing cannot change native workload graph, frozen revisions or protected storage"
        )
    if any(row["upstreamReleaseName"] == WRITER for row in after.get("releases", [])):
        raise RuntimeError("Routed native telemetry must remove the upstream token writer")
    used = {(row["sourceKind"], row["sourceName"]) for row in after.get("releases", [])}
    return {
        (row["sourceKind"], row["sourceName"])
        for row in before.get("releases", [])
        if row["upstreamReleaseName"] == WRITER
    } - used


def normalize_native_patches(patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for original in patches:
        patch = copy.deepcopy(original)
        name = patch.get("target", {}).get("name", "")
        if name == WRITER:
            continue
        operations = yaml.safe_load(patch.get("patch", "[]"))
        if not isinstance(operations, list) or not all(
            isinstance(op, Mapping) for op in operations
        ):
            result.append(patch)
            continue
        if (
            operations
            and (name.endswith(("-vm-stack", "-vm-logs")) or "-opentelemetry-collector-" in name)
            and all(op.get("path") == "/spec/values" for op in operations)
        ):
            continue
        normalized = []
        for op in operations:
            if op.get("path") == "/spec/dependsOn" and isinstance(op.get("value"), list):
                op["value"] = [dep for dep in op["value"] if dep.get("name") != OWNED_WRITER]
                if not op["value"]:
                    continue
            if name == JAIL_LOGS:
                for index in (0, 1):
                    prefix = f"/spec/values/extraVolumes/{index}/"
                    if op.get("path", "").startswith(prefix):
                        op["path"] = op["path"].replace(
                            prefix, "/spec/values/extraVolumes/jail/", 1
                        )
            normalized.append(op)
        patch["patch"] = yaml.safe_dump(normalized, sort_keys=True)
        result.append(patch)
    return result
