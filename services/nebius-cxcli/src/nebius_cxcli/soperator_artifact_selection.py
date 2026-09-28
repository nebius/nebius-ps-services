"""Select and verify only artifacts consumed by a declared Soperator operation."""

from __future__ import annotations

import copy
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .soperator_release import (
    SOPERATOR_MAIN_RELEASE_NAME,
    SOPERATOR_RELEASE_SNAPSHOT_SCHEMA,
    SoperatorArtifactRequest,
    SoperatorChartSnapshot,
    SoperatorReleaseGraphNode,
    SoperatorReleaseSnapshot,
    SoperatorThirdPartyChartSnapshot,
    VerifiedSoperatorSource,
    seal_soperator_release_snapshot,
)
from .soperator_release_source import normalized_script_manifest

_MAX_REQUIRED_ARTIFACTS = 256


def bind_final_consumers(
    snapshot: SoperatorReleaseSnapshot,
    documents: list[dict[str, Any]],
    values: Mapping[str, Any],
    adapter_documents: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Validate the actual post-rendered chart references and child values."""
    from .soperator_flux_graph import (
        _source_name,
        render_soperator_flux_graph_documents,
        soperator_graph_post_render_patches,
        target_soperator_release_name,
    )

    graph = snapshot.release_graph
    patches = soperator_graph_post_render_patches(
        snapshot, values, release_graph=graph, adapter_documents=adapter_documents
    )
    final = transform_consumer_documents(documents, {}, tuple(patches))
    sources = render_soperator_flux_graph_documents(
        snapshot, values, release_graph=graph, adapter_documents=adapter_documents
    )
    inventory = {
        (doc["kind"], doc["metadata"].get("namespace"), doc["metadata"]["name"]): doc
        for doc in sources
    }
    expected = {target_soperator_release_name(node.release_name): node for node in graph}
    consumers = []
    for doc in final:
        if doc.get("kind") != "HelmRelease":
            continue
        name = doc.get("metadata", {}).get("name")
        node = expected.pop(name, None)
        if node is None:
            raise ValueError("Final Soperator consumer is outside the admitted graph")
        ref = doc.get("spec", {}).get("chartRef", {})
        kind = "OCIRepository" if node.owner == "upstream" else "HelmChart"
        if ref != {"kind": kind, "name": _source_name(node), "namespace": node.namespace}:
            raise ValueError("Final Soperator consumer changed its admitted chart reference")
        if (kind, node.namespace, _source_name(node)) not in inventory:
            raise ValueError("Final Soperator consumer has no generated artifact source")
        # Helm validates the final child values, keyed by its original graph identity.
        consumer = copy.deepcopy(doc)
        consumer["metadata"]["name"] = node.release_name
        consumers.append(consumer)
    if expected:
        raise ValueError("Final Soperator render lost admitted consumers")
    return tuple(consumers)


def transform_consumer_documents(
    documents: list[dict[str, Any]],
    values: Mapping[str, Any],
    patches: tuple[Mapping[str, Any], ...] = (),
) -> list[dict[str, Any]]:
    """Apply authored patches and owned routing in deployment order."""
    from .soperator_observability_routing import route_documents
    from .soperator_release_resolver import _run

    if patches:
        if any(
            set(patch) - {"patch", "target", "options"} or not isinstance(patch.get("patch"), str)
            for patch in patches
        ):
            raise ValueError("Soperator consumer patches must be inline Kustomize patches")
        kustomize, kubectl = shutil.which("kustomize"), shutil.which("kubectl")
        if not kustomize and not kubectl:
            raise RuntimeError("kustomize or kubectl is required to validate Soperator patches")
        with tempfile.TemporaryDirectory(prefix="cxcli-consumer-patches-") as directory:
            root = Path(directory)
            (root / "resources.yaml").write_text(yaml.safe_dump_all(documents))
            (root / "kustomization.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "kustomize.config.k8s.io/v1beta1",
                        "kind": "Kustomization",
                        "resources": ["resources.yaml"],
                        "patches": [dict(p) for p in patches],
                    }
                )
            )
            for path in root.iterdir():
                path.chmod(0o600)
            command = (
                [kustomize, "build", directory]
                if kustomize
                else [str(kubectl), "kustomize", directory]
            )
            output = _run(command, label="validate Soperator consumer patches").stdout
            documents = [doc for doc in yaml.safe_load_all(output) if isinstance(doc, dict)]
    return route_documents(documents, values)


def compile_required_stages(
    source: VerifiedSoperatorSource,
    request: SoperatorArtifactRequest,
    acquired: SoperatorReleaseSnapshot | None = None,
) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    from .soperator_adapter import compile_upstream_soperator_values
    from .soperator_checks_login import bind_checks_login
    from .soperator_observability_routing import ROUTING_KEY
    from .soperator_release_resolver import _run
    from .soperator_values import read_observability_defaults, with_observability_defaults

    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("helm is required to select Soperator artifacts")
    root = Path(source.source.source_dir)
    defaults = read_observability_defaults(root / source.umbrella.source_path / "values.yaml")
    stages = {}
    for stage, intent in request.stages.items():
        values, _ = compile_upstream_soperator_values(
            with_observability_defaults(intent, defaults), release=acquired or source
        )
        if request.routing:
            if request.routing.get("metrics_storage") == "remote" and values.get(
                "notifier", {}
            ).get("enabled"):
                raise ValueError("Remote-only metrics cannot enable the local-metrics notifier")
            values[ROUTING_KEY] = copy.deepcopy(dict(request.routing))
            values["observability"]["publicEndpointEnabled"] = False
        values = bind_checks_login(values, root)
        if acquired is not None and stage in {"initial", "maintenance", "acceptance"}:
            from .soperator_checks_phase import ChecksPhase, ChecksPhaseContext
            from .soperator_checks_policy import compile_checks_policy

            policy = compile_checks_policy(root, values)
            context = (
                None
                if stage == "initial"
                else ChecksPhaseContext(
                    ChecksPhase(stage),
                    reservation="cxcli-admission",
                )
            )
            values = policy.effective_values(values, installing=stage == "initial", context=context)
        with tempfile.TemporaryDirectory(prefix="cxcli-selected-source-") as directory:
            path = Path(directory) / "values.yaml"
            path.write_text(yaml.safe_dump(values))
            path.chmod(0o600)
            output = _run(
                [
                    helm,
                    "template",
                    "soperator-fluxcd",
                    str(root / source.umbrella.source_path),
                    "--namespace",
                    "flux-system",
                    "--values",
                    str(path),
                ],
                label=f"select Soperator {stage} artifacts",
            ).stdout
        docs = [doc for doc in yaml.safe_load_all(output) if isinstance(doc, dict)]
        stages[stage] = (
            values,
            transform_consumer_documents(docs, values, request.post_render_patches),
        )
    return stages


def build_required_snapshot(
    source: VerifiedSoperatorSource,
    request: SoperatorArtifactRequest,
    *,
    cache_root: Path | None = None,
    emit: Any = None,
) -> SoperatorReleaseSnapshot:
    from . import soperator_release_resolver as resolver
    from .soperator_release_artifacts import verify_soperator_release_artifacts

    request_digest = request.fingerprint(source.identity_sha256)
    root = Path(source.source.source_dir)
    source_names = {chart.name: key for key, chart in source.charts.items()}
    charts: dict[str, SoperatorChartSnapshot] = {}
    third_party: dict[str, SoperatorThirdPartyChartSnapshot] = {}
    identities: dict[tuple[str, str, str], tuple[str, str]] = {}
    auxiliary: dict[str, set[str]] = {}
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("helm is required to admit Soperator packages")
    acquired: SoperatorReleaseSnapshot | None = None
    stages = compile_required_stages(source, request)

    with tempfile.TemporaryDirectory(prefix="cxcli-required-artifacts-") as directory:
        temp = Path(directory)

        def acquire(identity: tuple[str, str, str], reason: str, stack: tuple = ()) -> None:
            if identity in stack:
                raise ValueError("Soperator packaging dependency cycle")
            name, selector, repository = identity
            if identity in identities:
                _, key = identities[identity]
                if reason:
                    auxiliary.setdefault(key, set()).add(reason)
                return
            if len(identities) >= _MAX_REQUIRED_ARTIFACTS:
                raise ValueError("Soperator required artifact inventory exceeds its limit")
            own_key = source_names.get(name) if repository == source.registry else None
            if name in source_names and own_key is None:
                raise ValueError(f"Source chart {name} has a conflicting repository")
            key = (
                own_key
                or resolver._THIRD_PARTY_KEY_BY_CHART.get(name)
                or resolver._artifact_key(*identity)
            )
            if key in third_party:
                if name in resolver._THIRD_PARTY_KEY_BY_CHART:
                    raise ValueError(f"ambiguous authorities for adapter chart {name}")
                key = resolver._artifact_key(*identity)
            metadata = source.charts[own_key] if own_key else None
            version_selector = metadata.version if metadata else selector
            if metadata and own_key in charts:
                if selector not in {metadata.version, "*"}:
                    raise ValueError(f"Conflicting selected selectors for source chart {name}")
                identities[identity] = ("upstream", key)
                if reason:
                    auxiliary.setdefault(key, set()).add(reason)
                return
            destination = temp / str(len(identities))
            destination.mkdir()
            resolver._notify(emit, f"Verifying required chart {name} for {request.target_ref}")
            version, package_sha, oci_digest = resolver._pull_chart(
                helm=helm,
                chart=name,
                version=version_selector,
                repository=repository,
                destination=destination,
                emit=emit,
            )
            identities[identity] = ("upstream" if metadata else "third-party", key)
            if reason:
                auxiliary.setdefault(key, set()).add(reason)
            if metadata:
                if version != metadata.version or oci_digest is None:
                    raise ValueError(
                        f"Official source chart {name} has a different package identity"
                    )
                charts[key] = SoperatorChartSnapshot(
                    name,
                    version,
                    oci_digest,
                    package_sha,
                    metadata.source_path,
                    metadata.source_tree_sha256,
                )
                dependencies = resolver._source_chart_dependencies(root / metadata.source_path)
            else:
                third_party[key] = SoperatorThirdPartyChartSnapshot(
                    name, version, repository, package_sha, oci_digest
                )
                packages = list(destination.glob("*.tgz"))
                dependencies = ()
                if packages:
                    data = resolver._chart_metadata_from_package(packages[0])
                    from .soperator_release_artifacts import _chart_file_map

                    files = _chart_file_map(packages[0])
                    missing = [
                        item
                        for item in data.get("dependencies", [])
                        if not any(
                            path == f"charts/{item['name']}/Chart.yaml"
                            or path.startswith(f"charts/{item['name']}-")
                            and path.endswith(".tgz")
                            for path in files
                        )
                    ]
                    if missing:
                        raise ValueError(
                            f"Selected chart {name} has unbundled packaging dependencies"
                        )
            for dependency in dependencies:
                acquire(dependency, f"dependency:{key}", (*stack, identity))

        # The only unconditional package is the umbrella actually used by the operation.
        umbrella = source.umbrella
        acquire((umbrella.name, umbrella.version, source.registry), "operation:umbrella")
        for _ in range(_MAX_REQUIRED_ARTIFACTS):
            before = len(identities)
            graphs = {}
            for stage, (values, documents) in stages.items():
                repositories = resolver._rendered_repositories(documents)
                nodes, dependencies = {}, {}
                for document in documents:
                    if document.get("kind") != "HelmRelease":
                        continue
                    name = str(document.get("metadata", {}).get("name") or "")
                    namespace = str(document.get("metadata", {}).get("namespace") or "flux-system")
                    if not name or name in nodes or namespace != "flux-system":
                        raise ValueError("Soperator consumer identity is ambiguous or unsupported")
                    identity = resolver._release_chart_identity(document, repositories)
                    acquire(identity, "")
                    nodes[name] = identities[identity]
                    role = nodes[name][1]
                    if role in {"nodesets", "activeChecks"}:
                        auxiliary.setdefault(role, set()).add(f"{stage}:repair-consumer")
                    dependencies[name] = resolver._release_chart_dependencies(document)
                order = resolver._topological_stages(dependencies)
                graphs[stage] = tuple(
                    SoperatorReleaseGraphNode(
                        name,
                        "flux-system",
                        nodes[name][0],
                        order[name],
                        nodes[name][1],
                        dependencies[name],
                        name == SOPERATOR_MAIN_RELEASE_NAME,
                    )
                    for name in sorted(nodes, key=lambda name: (order[name], name))
                )
                # Dashboard delivery may replace the HelmRelease with ConfigMaps.
                if values.get("observability", {}).get("enabled") is True:
                    chart = source.charts.get("monitoringDashboards")
                    if chart is not None:
                        acquire(
                            (chart.name, chart.version, source.registry),
                            f"{stage}:dashboard-adapter",
                        )
            scripts, _ = normalized_script_manifest(root)
            meta, receipt = source.metadata, source.source
            candidate = SoperatorReleaseSnapshot(
                schema=SOPERATOR_RELEASE_SNAPSHOT_SCHEMA,
                target_ref=request.target_ref,
                request_sha256=request_digest,
                stage_graphs={k: v for k, v in graphs.items() if k != "desired"},
                auxiliary_artifacts={
                    key: tuple(sorted(reasons)) for key, reasons in auxiliary.items()
                },
                post_render_patches=request.post_render_patches,
                selector=meta.selector,
                release=meta.release,
                repository=meta.repository,
                tag=meta.tag,
                commit=meta.commit,
                tree=meta.tree,
                archive_url=meta.archive_url,
                archive_sha256=receipt.archive_sha256,
                archive_root=meta.archive_root,
                source_manifest_sha256=receipt.manifest_sha256,
                registry=source.registry,
                capability_contract=source.capability_contract,
                capability_sha256=source.capability_sha256,
                charts=dict(charts),
                third_party_charts=dict(third_party),
                release_graph=graphs["desired"],
                scripts_manifest_sha256=scripts,
                image_references=resolver._source_image_references(root),
                mount_image=source.mount_image,
                adapter_state_schema="nebius-cxcli.soperator-adapter-state.v2",
                populate_jail_image=source.populate_jail_image,
                jail_cuda_version=source.jail_cuda_version,
                snapshot_sha256="",
            )
            if acquired is not None and len(identities) == before:
                for stage, (values, _) in stages.items():
                    stage_snapshot = replace(candidate, release_graph=graphs[stage])
                    from .soperator_adapter import render_soperator_adapter_documents
                    from .soperator_values import with_source_observability

                    adapter_docs, _ = render_soperator_adapter_documents(
                        with_source_observability(request.stages[stage], source),
                        release=candidate,
                    )
                    verify_soperator_release_artifacts(
                        stage_snapshot,
                        receipt,
                        cache_root=cache_root,
                        values=values,
                        post_render_patches=request.post_render_patches,
                        adapter_documents=adapter_docs,
                    )
                return seal_soperator_release_snapshot(candidate)
            acquired = candidate
            stages = compile_required_stages(source, request, acquired)
    raise ValueError("Soperator artifact selection did not converge")
