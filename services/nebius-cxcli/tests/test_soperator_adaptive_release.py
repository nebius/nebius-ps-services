from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_release_resolver as resolver
from nebius_cxcli.soperator_release import (
    SOPERATOR_MAIN_RELEASE_NAME,
    SOPERATOR_UPSTREAM_CHART_ROLES,
    SoperatorArtifactRequest,
)
from soperator_fixtures import sample_snapshot


def _release(name, chart, *, source="soperator", version="1.0.0"):
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {
            "chart": {
                "spec": {
                    "chart": chart,
                    "version": version,
                    "sourceRef": {"kind": "HelmRepository", "name": source},
                }
            }
        },
    }


def _build(metadata, source):
    return resolver.build_soperator_release_snapshot(
        resolver.describe_soperator_source(metadata, source),
        SoperatorArtifactRequest.deployment("test-target", {}),
    )


def _fixture(tmp_path, monkeypatch, registry):
    names = [name for name, _ in SOPERATOR_UPSTREAM_CHART_ROLES]
    for name in [*names, "helm-new-feature", "helm-unused-helper"]:
        path = tmp_path / "helm" / name.removeprefix("helm-") / "Chart.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({"apiVersion": "v2", "name": name, "version": "1.0.0"}))
    files = {
        "helm/soperator-fluxcd/templates/soperator.yaml": "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n",
        "helm/soperator-fluxcd/templates/slurm-cluster.yaml": "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n",
        "helm/slurm-cluster/templates/slurm-cluster-cr.yaml": "apiVersion: slurm.nebius.ai/v1\nkind: SlurmCluster\n",
        "helm/soperator/crds/slurmcluster-crd.yaml": "name: slurmclusters.slurm.nebius.ai\ngroup: slurm.nebius.ai\n",
        "helm/soperator-fluxcd/values.yaml": yaml.safe_dump(
            {
                "observability": {"enabled": False},
                "slurmCluster": {},
                "soperator": {},
                "helmRepository": {"soperator": {"url": registry, "type": "oci"}},
            }
        ),
        "helm/slurm-cluster/values.yaml": "images: {}\npopulateJail: {}\nslurmNodes: {}\nvolumeSources: []\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    docs = [
        {
            "kind": "HelmRepository",
            "metadata": {"name": "soperator", "namespace": "flux-system"},
            "spec": {"url": registry, "type": "oci"},
        },
        _release("soperator-fluxcd-backup-config", "helm-soperator-backup-config"),
        _release(SOPERATOR_MAIN_RELEASE_NAME, "helm-slurm-cluster"),
    ]
    monkeypatch.setattr(resolver, "_render_upstream_umbrella", lambda *_a, **_k: docs)
    monkeypatch.setattr(
        "nebius_cxcli.soperator_adapter.render_soperator_adapter_documents",
        lambda *a, **k: ([], {}),
    )
    from nebius_cxcli import soperator_artifact_selection as selection

    monkeypatch.setattr(
        selection,
        "compile_required_stages",
        lambda source, request, acquired=None: {
            stage: (
                {},
                resolver._render_upstream_umbrella(tmp_path, helm=resolver.shutil.which("helm")),
            )
            for stage in request.stages
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_release_artifacts.verify_soperator_release_artifacts",
        lambda *a, **k: None,
    )
    pulls = []

    def pull(**kwargs):
        pulls.append((kwargs["chart"], kwargs["repository"]))
        return kwargs["version"], "sha256:" + "1" * 64, "sha256:" + "2" * 64

    monkeypatch.setattr(resolver, "_pull_chart", pull)
    monkeypatch.setattr(
        resolver,
        "_populate_jail_identity",
        lambda *_a: ("registry.example.invalid/jail@sha256:" + "3" * 64, "12.9"),
    )
    monkeypatch.setattr(resolver.shutil, "which", lambda _: "/test/helm")
    metadata = sample_snapshot(release="4.1.9")
    source = SimpleNamespace(
        release=metadata.release,
        commit=metadata.commit,
        tree=metadata.tree,
        source_dir=str(tmp_path),
        archive_sha256=metadata.archive_sha256,
        manifest_sha256=metadata.source_manifest_sha256,
    )
    return metadata, source, docs, pulls


@pytest.mark.parametrize(
    "registry", ["oci://cr.eu-north1.nebius.cloud/soperator", "oci://cr.nebius.cloud/soperator"]
)
def test_snapshot_follows_verified_release_registry(tmp_path, monkeypatch, registry):
    metadata, source, docs, pulls = _fixture(tmp_path, monkeypatch, registry)
    snapshot = _build(metadata, source)
    assert snapshot.registry == registry
    assert all(repo == registry for _, repo in pulls)
    backup = next(node for node in snapshot.release_graph if node.chart_key == "backupConfig")
    assert backup.owner == "upstream"


def test_additive_charts_are_frozen_by_authority_without_pulling_unused_source(
    tmp_path, monkeypatch
):
    registry = "oci://cr.nebius.cloud/soperator"
    metadata, source, docs, pulls = _fixture(tmp_path, monkeypatch, registry)
    docs.extend(
        [
            _release("soperator-fluxcd-new-feature", "helm-new-feature"),
            _release("soperator-fluxcd-security-profiles-operator", "security-profiles-operator"),
            _release("soperator-fluxcd-new-dependency", "new-dependency"),
        ]
    )
    snapshot = _build(metadata, source)
    assert "helm-unused-helper" not in {name for name, _ in pulls}
    for name, owner in [
        ("new-feature", "upstream"),
        ("security-profiles-operator", "third-party"),
        ("new-dependency", "third-party"),
    ]:
        node = next(
            node
            for node in snapshot.release_graph
            if node.release_name == "soperator-fluxcd-" + name
        )
        assert node.owner == owner


def test_source_chart_dependency_without_helmrelease_is_frozen(tmp_path, monkeypatch):
    metadata, source, docs, pulls = _fixture(
        tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator"
    )
    docs.append(_release("soperator-fluxcd-new-feature", "helm-new-feature"))
    chart_file = tmp_path / "helm/new-feature/Chart.yaml"
    chart = yaml.safe_load(chart_file.read_text())
    chart["dependencies"] = [
        {
            "name": "feature-helper",
            "version": "1.0.0",
            "repository": "https://charts.example.invalid",
        }
    ]
    chart_file.write_text(yaml.safe_dump(chart))
    snapshot = _build(metadata, source)
    assert ("feature-helper", "https://charts.example.invalid") in pulls
    assert any(
        artifact.chart == "feature-helper" and artifact.version == "1.0.0"
        for artifact in snapshot.third_party_charts.values()
    )
    assert all("feature-helper" not in node.release_name for node in snapshot.release_graph)


def test_freeze_includes_default_child_hidden_by_known_optional(tmp_path, monkeypatch):
    import shutil

    from nebius_cxcli.soperator_release_graph import render_soperator_release_graph

    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is required for the actual template boundary")
    render = resolver._render_upstream_umbrella
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    monkeypatch.setattr(resolver, "_render_upstream_umbrella", render)
    monkeypatch.setattr(resolver.shutil, "which", lambda _: helm)
    templates = tmp_path / "helm/soperator-fluxcd/templates"
    (templates / "soperator.yaml").write_text(yaml.safe_dump_all(docs))
    (templates / "slurm-cluster.yaml").write_text(
        "{{ if not .Values.backup.enabled }}\n"
        + yaml.safe_dump(_release("default-feature", "helm-new-feature"))
        + "{{ end }}\n"
    )
    defaults = tmp_path / "helm/soperator-fluxcd/values.yaml"
    defaults.write_text(defaults.read_text() + "backup:\n  enabled: false\n")
    snapshot = _build(metadata, source)
    assert "default-feature" in {node.release_name for node in snapshot.release_graph}
    for enabled in (False, True):
        graph = render_soperator_release_graph(snapshot, tmp_path, {"backup": {"enabled": enabled}})
        assert ("default-feature" in {node.release_name for node in graph}) is not enabled


@pytest.mark.parametrize("default_dependency_disabled", [False, True])
def test_inventory_does_not_combine_mutually_exclusive_dependency_edges(
    tmp_path, monkeypatch, default_dependency_disabled
):
    from copy import deepcopy

    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    render = resolver._render_upstream_umbrella
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.extend([_release("first", "helm-new-feature"), _release("second", "helm-new-feature")])
    optional = deepcopy(docs)
    optional[-2]["spec"]["dependsOn"] = [{"name": "second"}]
    defaults = deepcopy(docs)
    if default_dependency_disabled:
        defaults.pop()
        defaults[-1]["spec"]["dependsOn"] = [{"name": "second"}]
    else:
        defaults[-1]["spec"]["dependsOn"] = [{"name": "first"}]
    monkeypatch.setattr(resolver, "_render_upstream_umbrella", render)
    monkeypatch.setattr(
        resolver,
        "_run",
        lambda command, **_: SimpleNamespace(
            stdout=yaml.safe_dump_all(optional if "--set" in command else defaults)
        ),
    )
    snapshot = _build(metadata, source)
    selected = [(optional, "first")]
    if not default_dependency_disabled:
        selected.append((defaults, "second"))
    for documents, dependent in selected:
        graph = selected_soperator_release_graph(snapshot, documents)
        assert next(node for node in graph if node.release_name == dependent).stage == 1


def test_selected_graph_includes_additions_and_uses_frozen_registry(tmp_path, monkeypatch):
    from nebius_cxcli.soperator_flux_graph import (
        render_soperator_flux_graph_documents,
        soperator_graph_post_render_patches,
    )
    from nebius_cxcli.soperator_release_artifacts import _verify_rendered_release_graph
    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    registry = "oci://cr.nebius.cloud/soperator"
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, registry)
    docs.append(_release("soperator-fluxcd-new-feature", "helm-new-feature"))
    snapshot = _build(metadata, source)
    selected = selected_soperator_release_graph(snapshot, docs)
    _verify_rendered_release_graph(yaml.safe_dump_all(docs).encode(), snapshot)
    sources = render_soperator_flux_graph_documents(snapshot, {}, release_graph=selected)
    assert all(
        doc["spec"]["url"].startswith(registry + "/")
        for doc in sources
        if doc["kind"] == "OCIRepository"
    )
    patches = soperator_graph_post_render_patches(
        snapshot,
        {"slurmCluster": {"overrideValues": {"clusterName": "soperator"}}},
        release_graph=selected,
    )
    assert {
        row["target"]["name"] for row in patches if isinstance(yaml.safe_load(row["patch"]), list)
    } == {row.release_name for row in selected}
    assert "soperator-fluxcd-new-feature" in {row.release_name for row in selected}
    # Effective values may disable an optional release: selection follows its render.
    disabled = [
        doc for doc in docs if doc.get("metadata", {}).get("name") != "soperator-fluxcd-new-feature"
    ]
    assert "soperator-fluxcd-new-feature" not in {
        row.release_name for row in selected_soperator_release_graph(snapshot, disabled)
    }
    with pytest.raises(ValueError, match="admitted operation stages"):
        _verify_rendered_release_graph(yaml.safe_dump_all(disabled).encode(), snapshot)


@pytest.mark.parametrize(
    "invalid",
    [
        "missing-main",
        "unknown-artifact",
        "duplicate-release",
        "duplicate-repository",
        "source-kind",
        "source-namespace",
        "api",
        "cycle",
        "missing-dependency",
        "repository-drift",
        "version-drift",
    ],
)
def test_selected_graph_rejects_incompatible_or_unfrozen_inputs(tmp_path, monkeypatch, invalid):
    from copy import deepcopy

    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    snapshot = _build(metadata, source)
    docs = deepcopy(docs)
    if invalid == "missing-main":
        docs.pop()
    elif invalid == "unknown-artifact":
        docs.append(_release("soperator-fluxcd-unknown", "unfrozen-chart"))
    elif invalid == "duplicate-release":
        docs.append(deepcopy(docs[-1]))
    elif invalid == "duplicate-repository":
        docs.append(deepcopy(docs[0]))
    elif invalid == "source-kind":
        docs[-1]["spec"]["chart"]["spec"]["sourceRef"]["kind"] = "OCIRepository"
    elif invalid == "source-namespace":
        docs[-1]["spec"]["chart"]["spec"]["sourceRef"]["namespace"] = "elsewhere"
    elif invalid == "api":
        docs[-1]["apiVersion"] = "helm.toolkit.fluxcd.io/v99"
    elif invalid == "cycle":
        docs[-1]["spec"]["dependsOn"] = [{"name": docs[-1]["metadata"]["name"]}]
    elif invalid == "missing-dependency":
        docs[-1]["spec"]["dependsOn"] = [{"name": "absent"}]
    elif invalid == "repository-drift":
        docs[0]["spec"]["url"] = "oci://example.invalid/other"
    elif invalid == "version-drift":
        docs[-1]["spec"]["chart"]["spec"]["version"] = "99.0.0"
    with pytest.raises(ValueError):
        selected_soperator_release_graph(snapshot, docs)


def test_third_party_same_name_distinct_authorities_remain_distinct(tmp_path, monkeypatch):
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(
        {
            "kind": "HelmRepository",
            "metadata": {"name": "other", "namespace": "flux-system"},
            "spec": {"url": "https://charts.example.invalid"},
        }
    )
    docs += [
        _release("first", "new-dependency"),
        _release("second", "new-dependency", source="other"),
    ]
    snapshot = _build(metadata, source)
    nodes = {node.release_name: node for node in snapshot.release_graph}
    assert nodes["first"].chart_key != nodes["second"].chart_key
    assert len(snapshot.third_party_charts) == 2


def test_effective_values_drive_actual_helm_graph(tmp_path, monkeypatch):
    import shutil

    from nebius_cxcli.soperator_release_graph import render_soperator_release_graph

    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is required for the actual template boundary")
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(_release("soperator-fluxcd-new-feature", "helm-new-feature"))
    snapshot = _build(metadata, source)
    monkeypatch.setattr(resolver.shutil, "which", lambda _: helm)
    templates = tmp_path / "helm/soperator-fluxcd/templates"
    (templates / "soperator.yaml").write_text(yaml.safe_dump_all(docs[:-1]))
    (templates / "slurm-cluster.yaml").write_text(
        "{{ if .Values.newFeature.enabled }}\n" + yaml.safe_dump(docs[-1]) + "{{ end }}\n"
    )
    defaults = tmp_path / "helm/soperator-fluxcd/values.yaml"
    defaults.write_text(defaults.read_text() + "newFeature:\n  enabled: true\n")
    for enabled in (False, True):
        graph = render_soperator_release_graph(
            snapshot, tmp_path, {"newFeature": {"enabled": enabled}}
        )
        assert ("soperator-fluxcd-new-feature" in {node.release_name for node in graph}) is enabled


def test_third_party_version_drift_is_rejected(tmp_path, monkeypatch):
    from copy import deepcopy

    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(_release("new-dependency", "new-dependency"))
    snapshot = _build(metadata, source)
    changed = deepcopy(docs)
    changed[-1]["spec"]["chart"]["spec"]["version"] = "99.0.0"
    with pytest.raises(ValueError, match="frozen artifact"):
        selected_soperator_release_graph(snapshot, changed)


def test_dependency_semantics_are_not_silently_discarded(tmp_path, monkeypatch):
    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    snapshot = _build(metadata, source)
    docs[-1]["spec"]["dependsOn"] = [
        {
            "name": docs[-2]["metadata"]["name"],
            "readyExpr": "dep.spec.values.version == self.spec.values.version",
        }
    ]
    with pytest.raises(ValueError, match="unsupported dependencies"):
        selected_soperator_release_graph(snapshot, docs)


def test_additive_release_patches_apply_with_or_without_existing_labels(tmp_path, monkeypatch):
    import shutil
    import subprocess

    from nebius_cxcli.soperator_flux_graph import soperator_graph_post_render_patches
    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl kustomize is required to verify actual post-rendering")
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(_release("soperator-fluxcd-new-feature", "helm-new-feature"))
    docs[-1]["metadata"]["labels"] = {"upstream-label": "preserved"}
    snapshot = _build(metadata, source)
    graph = selected_soperator_release_graph(snapshot, docs)
    values = {"slurmCluster": {"overrideValues": {"clusterName": "soperator"}}}
    patches = soperator_graph_post_render_patches(snapshot, values, release_graph=graph)
    (tmp_path / "release.yaml").write_text(yaml.safe_dump_all(docs))
    (tmp_path / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["release.yaml"],
                "patches": patches,
            }
        )
    )
    result = subprocess.run(
        [kubectl, "kustomize", str(tmp_path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    rendered = [
        doc for doc in yaml.safe_load_all(result.stdout) if doc.get("kind") == "HelmRelease"
    ]
    assert len(rendered) == 3
    assert all("chartRef" in doc["spec"] and "chart" not in doc["spec"] for doc in rendered)
    assert all(
        doc["metadata"]["labels"]["soperator.nebius.ai/release-graph"] == "nebius-cxcli"
        for doc in rendered
    )
    addon = next(doc for doc in rendered if doc["metadata"]["name"].endswith("new-feature"))
    assert addon["metadata"]["labels"]["upstream-label"] == "preserved"


def test_only_verified_source_constraints_admit_frozen_versions(tmp_path, monkeypatch):
    from copy import deepcopy

    from nebius_cxcli.soperator_release_graph import selected_soperator_release_graph

    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(_release("new-dependency", "new-dependency"))
    snapshot = _build(metadata, source)
    original = deepcopy(docs)
    original[-1]["spec"]["chart"]["spec"]["version"] = "1.*"
    selected = selected_soperator_release_graph(snapshot, original, source_documents=original)
    assert selected[-1].release_name
    changed = deepcopy(original)
    changed[-1]["spec"]["chart"]["spec"]["version"] = "99.*"
    with pytest.raises(ValueError, match="frozen artifact"):
        selected_soperator_release_graph(snapshot, changed, source_documents=original)


def test_known_adapter_role_rejects_ambiguous_chart_authorities(tmp_path, monkeypatch):
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs += [_release("first", "cert-manager"), _release("second", "cert-manager", version="2.0.0")]
    with pytest.raises(ValueError, match="ambiguous authorities for adapter chart"):
        _build(metadata, source)


def test_source_dependency_packages_are_bound_to_repository_and_version(tmp_path, monkeypatch):
    from nebius_cxcli import soperator_release_artifacts as artifacts

    source = tmp_path / "source"
    source.mkdir()
    (source / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "parent",
                "version": "1.0.0",
                "dependencies": [
                    {
                        "name": "shared",
                        "repository": "https://second.example.invalid",
                        "version": "2.0.0",
                    }
                ],
            }
        )
    )
    destination = tmp_path / "package"
    destination.mkdir()
    packages = {
        ("shared", "https://first.example.invalid", "1.0.0"): tmp_path / "first.tgz",
        ("shared", "https://second.example.invalid", "2.0.0"): tmp_path / "second.tgz",
    }
    copied = []
    monkeypatch.setattr(
        artifacts, "_write_chart_tree", lambda package, _destination: copied.append(package)
    )
    monkeypatch.setattr(
        artifacts, "_run", lambda *_args, **_kwargs: (destination / "parent.tgz").touch()
    )
    artifacts._package_verified_source_chart(
        source, destination, helm="/test/helm", dependency_packages=packages
    )
    assert copied == [tmp_path / "second.tgz"]


def test_disabled_broken_nfs_is_never_acquired(tmp_path, monkeypatch):
    metadata, source, _docs, pulls = _fixture(
        tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator"
    )
    original = resolver._pull_chart

    def pull(**kwargs):
        if kwargs["chart"] == "helm-nfs-server":
            pytest.fail("disabled NFS must never be pulled")
        return original(**kwargs)

    monkeypatch.setattr(resolver, "_pull_chart", pull)
    snapshot = _build(metadata, source)
    assert "nfsServer" not in snapshot.charts
    assert "helm-nfs-server" not in {name for name, _ in pulls}
    assert "bootstrap" not in snapshot.charts


def test_selected_broken_nfs_fails_admission(tmp_path, monkeypatch):
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(_release("soperator-fluxcd-nfs-server", "helm-nfs-server"))

    def verify(snapshot, *_args, **_kwargs):
        assert "nfsServer" in snapshot.charts
        raise ValueError("official OCI chart helm-nfs-server differs from release source")

    monkeypatch.setattr(
        "nebius_cxcli.soperator_release_artifacts.verify_soperator_release_artifacts", verify
    )
    with pytest.raises(ValueError, match="helm-nfs-server differs"):
        _build(metadata, source)


def test_adapter_consumes_dashboard_even_after_helmrelease_removed(tmp_path, monkeypatch):
    from nebius_cxcli import soperator_artifact_selection as selection

    metadata, source, docs, pulls = _fixture(
        tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator"
    )
    monkeypatch.setattr(
        selection,
        "compile_required_stages",
        lambda *args: {"desired": ({"observability": {"enabled": True}}, docs)},
    )
    snapshot = _build(metadata, source)
    assert "monitoringDashboards" in snapshot.charts
    assert snapshot.auxiliary_artifacts["monitoringDashboards"] == ("desired:dashboard-adapter",)
    assert all(node.chart_key != "monitoringDashboards" for node in snapshot.release_graph)
    assert "helm-soperator-monitoring-dashboards" in {name for name, _ in pulls}


def test_recursive_source_packaging_dependency_cycle_is_rejected(tmp_path, monkeypatch):
    metadata, source, docs, _ = _fixture(tmp_path, monkeypatch, "oci://cr.nebius.cloud/soperator")
    docs.append(_release("feature", "helm-new-feature"))
    path = tmp_path / "helm/new-feature/Chart.yaml"
    data = yaml.safe_load(path.read_text())
    data["dependencies"] = [
        {
            "name": "helm-new-feature",
            "version": "1.0.0",
            "repository": "oci://cr.nebius.cloud/soperator",
        }
    ]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="dependency cycle"):
        _build(metadata, source)


def test_request_identity_covers_target_stages_routing_and_patches():
    from dataclasses import replace

    base = SoperatorArtifactRequest.deployment("one", {"observability": {"enabled": False}})
    digest = base.fingerprint("sha256:" + "1" * 64)
    for request in (
        replace(base, target_ref="two"),
        replace(base, stages={**base.stages, "maintenance": {}}),
        replace(base, routing={"metrics_storage": "remote"}),
        replace(base, post_render_patches=({"patch": "[]"},)),
        SoperatorArtifactRequest.deployment("one", {"observability": {"enabled": True}}),
    ):
        assert request.fingerprint("sha256:" + "1" * 64) != digest
    assert base.fingerprint("sha256:" + "2" * 64) != digest


def test_v2_snapshot_is_rejected_without_rewriting_receipt(tmp_path):
    import json

    from nebius_cxcli.soperator_release import load_soperator_release_snapshot

    path = tmp_path / "active-snapshot.json"
    original = json.dumps({"schema": "nebius-cxcli.soperator-release-snapshot.v2"}).encode()
    path.write_bytes(original)
    path.chmod(0o600)
    with pytest.raises(ValueError, match="previous cxcli binary"):
        load_soperator_release_snapshot(path)
    assert path.read_bytes() == original
