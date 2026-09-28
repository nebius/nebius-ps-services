"""Select an immutable release graph from the verified upstream Helm render."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .soperator_release import (
    SOPERATOR_MAIN_RELEASE_NAME,
    SoperatorReleaseGraphNode,
    SoperatorReleaseSnapshot,
)
from .soperator_release_resolver import (
    _release_chart_dependencies,
    _release_chart_identity,
    _render_upstream_umbrella,
    _rendered_repositories,
    _run,
    _topological_stages,
)


def selected_soperator_release_graph(
    lock: SoperatorReleaseSnapshot,
    documents: Sequence[Mapping[str, Any]],
    *,
    source_documents: Sequence[Mapping[str, Any]] = (),
) -> tuple[SoperatorReleaseGraphNode, ...]:
    """Bind every rendered child to frozen artifacts; reject ambiguity before effects."""
    repositories = _rendered_repositories(documents)
    source_repositories = _rendered_repositories(source_documents)
    source_identities = {
        str(doc.get("metadata", {}).get("name") or ""): _release_chart_identity(
            doc, source_repositories
        )
        for doc in source_documents
        if doc.get("kind") == "HelmRelease"
    }
    frozen = {node.release_name: node for node in lock.release_graph}
    nodes = {}
    dependencies = {}
    for doc in documents:
        if doc.get("kind") != "HelmRelease":
            continue
        metadata, spec = doc.get("metadata"), doc.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise ValueError("upstream HelmRelease has invalid metadata")
        name = str(metadata.get("name") or "")
        namespace = str(metadata.get("namespace") or "flux-system")
        if not name or name in nodes or namespace != "flux-system":
            raise ValueError("upstream HelmRelease identities are ambiguous or unsupported")
        chart, version, repository = _release_chart_identity(doc, repositories)
        source_selector = source_identities.get(name) == (chart, version, repository)
        candidates = [
            ("upstream", key)
            for key, artifact in lock.charts.items()
            if chart == artifact.name
            and repository == lock.registry
            and (version == artifact.version or source_selector)
        ]
        candidates += [
            ("third-party", key)
            for key, artifact in lock.third_party_charts.items()
            if chart == artifact.chart
            and repository == artifact.repository
            and (version == artifact.version or source_selector)
        ]
        previous = frozen.get(name)
        if previous is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate == (previous.owner, previous.chart_key)
            ]
        if len(candidates) != 1:
            raise ValueError(f"upstream HelmRelease {name} has no unambiguous frozen artifact")
        owner, key = candidates[0]
        dependencies[name] = _release_chart_dependencies(doc)
        nodes[name] = (namespace, owner, key)
    if SOPERATOR_MAIN_RELEASE_NAME not in nodes:
        raise ValueError("upstream render has no main Soperator workload")
    stages = _topological_stages(dependencies)
    return tuple(
        SoperatorReleaseGraphNode(
            name,
            nodes[name][0],
            nodes[name][1],
            stages[name],
            nodes[name][2],
            dependencies[name],
            name == SOPERATOR_MAIN_RELEASE_NAME,
        )
        for name in sorted(nodes, key=lambda name: (stages[name], name))
    )


def render_soperator_release_graph(
    lock: SoperatorReleaseSnapshot,
    source_root: Path,
    values: Mapping[str, Any],
    *,
    telemetry_patches: list[dict[str, Any]] | None = None,
    post_render_patches: tuple[Mapping[str, Any], ...] = (),
) -> tuple[SoperatorReleaseGraphNode, ...]:
    """Render effective values against the already verified frozen source."""
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("helm is required to select the upstream Soperator release graph")
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-graph-") as directory:
        values_path = Path(directory) / "values.yaml"
        values_path.write_text(yaml.safe_dump(dict(values), sort_keys=False))
        values_path.chmod(0o600)
        result = _run(
            [
                helm,
                "template",
                "soperator-fluxcd",
                str(source_root / lock.umbrella.source_path),
                "--namespace",
                "flux-system",
                "--values",
                str(values_path),
            ],
            label="render frozen upstream release graph",
        )
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]
    from .soperator_artifact_selection import transform_consumer_documents
    from .soperator_observability_routing import child_patches

    documents = transform_consumer_documents(documents, {}, post_render_patches)
    routed = transform_consumer_documents(documents, values)
    if telemetry_patches is not None:
        telemetry_patches.extend(child_patches(documents, routed))
    documents = routed
    return selected_soperator_release_graph(
        lock, documents, source_documents=_render_upstream_umbrella(source_root, helm=helm)
    )
