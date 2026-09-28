from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli, ordinary_apps
from nebius_cxcli.deploy_targets import flux_target_dir
from nebius_cxcli.generated_manifest import (
    build_generated_manifest,
    load_generated_manifest,
    manifest_path_for_generated_dir,
)
from nebius_cxcli.paths import resolve_project_paths


@pytest.fixture
def project(tmp_path, monkeypatch):
    def accepted(config, paths, manifest, baseline, refs, **kwargs):
        kwargs["assert_held"]()
        return SimpleNamespace(
            read=lambda: SimpleNamespace(
                value={
                    "active": None,
                    "accepted": {
                        "evidence": {
                            "targets": {
                                ref: {"identity": baseline["identities"][ref]} for ref in refs
                            }
                        }
                    },
                }
            )
        )

    monkeypatch.setattr(ordinary_apps, "assert_accepted_deployment", accepted)
    # These ownership tests use a synthetic chart outside the catalog. The real
    # compatibility and artifact boundary is covered in test_application_execution.
    monkeypatch.setattr(
        "nebius_cxcli.application_compatibility.refresh_application_manifest",
        lambda config, paths, manifest: copy.deepcopy(manifest),
    )
    config_path = tmp_path / "deployments" / "tenant" / "project" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    paths = resolve_project_paths(config_path)
    payload = {
        "client_info": {
            "client_name": "example",
            "nebius": {"project_id": "project-example", "region_id": "eu-north2"},
        },
        "infra": {
            "components": [{"id": "mk8s", "instance_id": "cluster", "enabled": True, "inputs": {}}]
        },
        "deploy": {"targets": [{"instance_id": "cluster"}]},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster",
                    "target_ref": "cluster",
                    "enabled": True,
                    "version": "4.1.7",
                    "values": {},
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload))
    paths.infra_dir.mkdir(parents=True)
    (paths.infra_dir / "main.tf").write_text("terraform {}\n")
    protected = flux_target_dir(paths, "cluster")
    protected.mkdir(parents=True)
    (protected / "kustomization.yaml").write_text(yaml.safe_dump({"resources": ["protected.yaml"]}))
    (protected / "protected.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "soperator-values", "namespace": "soperator"},
                "data": {"protected": "yes"},
            }
        )
    )
    target = {
        "component_id": "mk8s",
        "ownership": "managed",
        "instance_id": "cluster",
        "target_ref": "cluster",
        "cluster_id_output_name": "cluster_id",
        "component_output_ref": "cluster.cluster_id",
        "access": "external",
        "flux_dir": str(protected),
    }
    manifest = build_generated_manifest(
        config=payload, paths=paths, targets=[target], required_component_outputs=[]
    )
    manifest_path_for_generated_dir(paths.generated_dir).write_text(json.dumps(manifest))
    ordinary_apps.accept_ordinary_app_baseline(
        paths,
        identities={"cluster": {"cluster_id": "cluster-example", "kubernetes_uid": "uid-example"}},
    )
    return paths, payload


def add_app(paths, payload, *, name="example-app"):
    payload = copy.deepcopy(payload)
    payload["apps"]["charts"].append(
        {
            "id": name,
            "instance_id": "cluster",
            "target_ref": "cluster",
            "enabled": True,
            "repo": "https://charts.example.invalid",
            "chart": name,
            "version": "1.2.3",
            "namespace": "ordinary",
            "release-name": name,
            "values": {},
        }
    )
    paths.config_path.write_text(yaml.safe_dump(payload))
    return payload


def test_ordinary_render_filters_before_soperator_compilation(project, monkeypatch):
    paths, payload = project
    from nebius_cxcli import flux_render

    monkeypatch.setattr(
        flux_render,
        "freeze_soperator_release",
        lambda *_a, **_k: pytest.fail("ordinary render compiled Soperator"),
    )
    protected = {p: p.read_bytes() for p in ordinary_apps._protected_files(paths)}
    payload = add_app(paths, payload)
    written = ordinary_apps.render_ordinary_apps(payload, paths)
    assert written
    assert all("/ordinary/" in str(path) for path in written)
    assert {p: p.read_bytes() for p in protected} == protected
    manifest = load_generated_manifest(paths.generated_dir)
    ordinary_apps.validate_ordinary_bundle(paths, manifest)
    docs = ordinary_apps.resource_documents(flux_target_dir(paths, "cluster") / "ordinary")
    assert any(
        doc["kind"] == "HelmRelease" and doc["metadata"]["name"] == "example-app" for doc in docs
    )
    assert not any(doc.get("metadata", {}).get("name") == "soperator-values" for doc in docs)


@pytest.mark.parametrize("drift", ["source", "terraform", "flux", "manifest"])
def test_protected_drift_fails_before_render(project, monkeypatch, drift):
    paths, payload = project
    payload = add_app(paths, payload)
    if drift == "source":
        payload["infra"]["components"][0]["inputs"]["node_count"] = 99
        paths.config_path.write_text(yaml.safe_dump(payload))
    elif drift == "terraform":
        (paths.infra_dir / "main.tf").write_text("changed")
    elif drift == "flux":
        (flux_target_dir(paths, "cluster") / "protected.yaml").write_text("changed")
    else:
        manifest = load_generated_manifest(paths.generated_dir)
        manifest["runtime_config"]["apps"]["charts"][0]["version"] = "9.9.9"
        manifest_path_for_generated_dir(paths.generated_dir).write_text(json.dumps(manifest))
    monkeypatch.setattr(
        ordinary_apps,
        "render_flux",
        lambda *_a, **_k: pytest.fail("render ran before protected check"),
    )
    with pytest.raises(RuntimeError, match="Protected Soperator"):
        ordinary_apps.render_ordinary_apps(payload, paths)


def test_post_flux_collision_is_in_inventory(project):
    paths, _ = project
    protected = flux_target_dir(paths, "cluster")
    ordinary = protected / "ordinary"
    ordinary.mkdir()
    (ordinary / "kustomization.yaml").write_text("resources: []\n")
    (ordinary / "post-flux-test.yaml").write_bytes((protected / "protected.yaml").read_bytes())
    with pytest.raises(RuntimeError, match="collides"):
        ordinary_apps.validate_resource_ownership(ordinary, protected)


def test_pending_install_rejects_ordinary_changes(project):
    paths, payload = project
    (paths.reports_dir / "soperator-install-plan.json").write_text(json.dumps({"status": "failed"}))
    (paths.reports_dir / "soperator-install-plan.json").chmod(0o600)
    with pytest.raises(RuntimeError, match="pending Soperator install|requires recovery"):
        ordinary_apps.render_ordinary_apps(add_app(paths, payload), paths)


def test_app_apply_retry_rechecks_lease_before_second_attempt(monkeypatch):
    from types import SimpleNamespace

    from nebius_cxcli import cli
    from nebius_cxcli.app_mutation import app_mutation_scope

    calls = []
    checks = []

    def authority():
        checks.append(True)
        if len(checks) > 1:
            raise RuntimeError("lease lost")

    monkeypatch.setattr(
        cli.kubernetes_process,
        "run",
        lambda *_a, **_k: (
            calls.append("apply")
            or SimpleNamespace(returncode=1, stdout="", stderr="temporary failure")
        ),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda *_a: None)
    with app_mutation_scope(authority), pytest.raises(RuntimeError, match="lease lost"):
        cli._run_kubectl_with_transient_retries(
            ["kubectl", "apply", "-k", "ordinary"],
            env={},
            retries=2,
            retry_stderr_markers=("temporary",),
        )
    assert calls == ["apply"]


def test_ordinary_render_rejects_concurrent_app_publication(project, monkeypatch):
    paths, payload = project
    payload = add_app(paths, payload)
    ordinary_apps.render_ordinary_apps(payload, paths)
    real_render = ordinary_apps.render_flux
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)

    def concurrent_render(*args, **kwargs):
        result = real_render(*args, **kwargs)
        manifest = json.loads(manifest_path.read_text())
        manifest["concurrent-generation"] = True
        manifest_path.write_text(json.dumps(manifest))
        return result

    monkeypatch.setattr(ordinary_apps, "render_flux", concurrent_render)
    with pytest.raises(RuntimeError):
        ordinary_apps.render_ordinary_apps(payload, paths)
    assert json.loads(manifest_path.read_text())["concurrent-generation"] is True


def test_runtime_secret_owner_collision_prevents_write(monkeypatch):
    monkeypatch.setattr(
        ordinary_apps,
        "_live_json",
        lambda *_a: {"metadata": {"annotations": {"cxcli.nebius.com/app-owner": "other"}}},
    )
    guard = ordinary_apps.runtime_manifest_guard("this-project", {})
    with pytest.raises(RuntimeError, match="another owner"):
        guard({"kind": "Secret", "metadata": {"name": "custom-admin", "namespace": "ordinary"}})


def test_shared_protected_namespace_is_referenced_without_mutation(project):
    paths, _ = project
    root = flux_target_dir(paths, "cluster")
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "shared"}}
    (root / "protected.yaml").write_text(yaml.safe_dump(namespace))
    ordinary = root / "ordinary"
    ordinary.mkdir()
    (ordinary / "kustomization.yaml").write_text("resources: [namespace.yaml]\n")
    (ordinary / "namespace.yaml").write_text(yaml.safe_dump(namespace))
    ordinary_apps.omit_shared_protected_resources(ordinary, root)
    ordinary_apps.validate_resource_ownership(ordinary, root)
    assert ordinary_apps.resource_documents(ordinary) == []


@pytest.mark.parametrize("action", ["publish", "render", "apply"])
def test_lifecycle_config_lock_excludes_ordinary_operations(project, action):
    paths, payload = project
    from nebius_cxcli.soperator_operation_lock import SoperatorOperationLocalLock

    with (
        SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"),
        pytest.raises(RuntimeError, match="lock|already"),
    ):
        if action == "publish":
            ordinary_apps.publish_ordinary_app_config(paths, payload)
        elif action == "render":
            ordinary_apps.render_ordinary_apps(payload, paths)
        else:
            ordinary_apps.apply_ordinary_apps(
                payload, paths, {}, targets=[], services=None, assert_project_authority=lambda: None
            )


@pytest.mark.parametrize("successor_status", ["complete", "active", "missing", "cycle"])
def test_remote_recovery_supersession_requires_complete_successor(monkeypatch, successor_status):
    import hashlib

    identity = {"cluster_id": "cluster-example", "kubernetes_uid": "uid-example"}
    prefix = (
        "nebius-cxcli-soperator-op-"
        + hashlib.sha256(identity["cluster_id"].encode()).hexdigest()[:10]
    )
    data = {"clusterId": identity["cluster_id"], "kubernetesUid": identity["kubernetes_uid"]}
    items = [
        {
            "metadata": {"name": prefix + "-old"},
            "data": {**data, "operationId": "old", "status": "superseded", "supersededBy": "new"},
        }
    ]
    if successor_status != "missing":
        items.append(
            {
                "metadata": {"name": prefix + "-new"},
                "data": {
                    **data,
                    "operationId": "new",
                    "status": "superseded" if successor_status == "cycle" else successor_status,
                    "supersededBy": "old",
                },
            }
        )
    monkeypatch.setattr(ordinary_apps, "_live_json", lambda *_a: {"items": items})
    if successor_status == "complete":
        ordinary_apps._validate_live_lifecycle_complete(
            identity, {cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx"}
        )
    else:
        with pytest.raises(RuntimeError, match="operation"):
            ordinary_apps._validate_live_lifecycle_complete(
                identity, {cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx"}
            )


def test_unexpected_symlink_rejected_before_apply(project):
    paths, payload = project
    ordinary_apps.render_ordinary_apps(add_app(paths, payload), paths)
    root = flux_target_dir(paths, "cluster") / "ordinary"
    (root / "unlisted.yaml").symlink_to(paths.config_path)
    with pytest.raises(RuntimeError, match="unsafe"):
        ordinary_apps.validate_ordinary_bundle(paths, load_generated_manifest(paths.generated_dir))


def test_external_secret_cannot_overwrite_protected_literal_secret(project):
    paths, _ = project
    root = flux_target_dir(paths, "cluster")
    (root / "protected.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "protected", "namespace": "shared"},
            }
        )
    )
    ordinary = root / "ordinary"
    ordinary.mkdir()
    (ordinary / "kustomization.yaml").write_text("resources: [secret.yaml]\n")
    (ordinary / "secret.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "external-secrets.io/v1",
                "kind": "ExternalSecret",
                "metadata": {"name": "other", "namespace": "shared"},
                "spec": {"target": {"name": "protected", "creationPolicy": "Merge"}},
            }
        )
    )
    with pytest.raises(RuntimeError, match="target Secret"):
        ordinary_apps.validate_resource_ownership(ordinary, root)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "unaccepted",
        "digest",
        "settings",
        "auth",
        "foreign-consumer",
        "new-consumer",
        "removed-consumer",
        "unowned-consumer",
        "uid",
        "stale",
        "deleting",
        "source-owner",
        "controller",
        "tag",
    ],
)
def test_accepted_immutable_source_is_reused_without_adoption(tmp_path, monkeypatch, fault):
    source = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "OCIRepository",
        "metadata": {
            "name": "source",
            "namespace": "flux-system",
            "annotations": {"cxcli.nebius.com/app-owner": "owner"},
        },
        "spec": {
            "url": "oci://registry.example.invalid/chart",
            "interval": "30m",
            "ref": {"digest": "sha256:" + "a" * 64},
        },
    }
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": "app",
            "namespace": "flux-system",
            "annotations": {"cxcli.nebius.com/app-owner": "owner"},
        },
        "spec": {
            "chartRef": {"kind": "OCIRepository", "name": "source", "namespace": "flux-system"}
        },
    }
    prior_source = copy.deepcopy(source)
    prior_source["metadata"].pop("annotations")
    accepted = {
        ordinary_apps.resource_identity(prior_source): prior_source,
        ordinary_apps.resource_identity(release): copy.deepcopy(release),
    }
    live_source = copy.deepcopy(prior_source)
    live_source["metadata"].update(uid="source-uid", generation=1)
    live_source["spec"].update(provider="generic", timeout="60s")
    live_source["status"] = {
        "observedGeneration": 1,
        "conditions": [{"type": "Ready", "status": "True"}],
    }
    live_release = copy.deepcopy(release)
    live_release["metadata"]["uid"] = "release-uid"
    live_releases = [live_release]
    if fault == "unaccepted":
        accepted.clear()
    elif fault == "digest":
        live_source["spec"]["ref"]["digest"] = "sha256:" + "b" * 64
    elif fault == "settings":
        source["spec"]["interval"] = "10m"
    elif fault == "auth":
        live_source["spec"]["secretRef"] = {"name": "foreign"}
    elif fault == "foreign-consumer":
        foreign = copy.deepcopy(live_release)
        foreign["metadata"]["name"] = "foreign"
        foreign["metadata"]["annotations"]["cxcli.nebius.com/app-owner"] = "foreign"
        live_releases.append(foreign)
    elif fault == "new-consumer":
        accepted.pop(ordinary_apps.resource_identity(release))
    elif fault == "removed-consumer":
        removed = copy.deepcopy(release)
        removed["metadata"]["name"] = "removed"
        accepted[ordinary_apps.resource_identity(removed)] = removed
    elif fault == "unowned-consumer":
        live_release["metadata"].pop("annotations")
    elif fault == "uid":
        live_source["metadata"].pop("uid")
    elif fault == "stale":
        live_source["status"]["observedGeneration"] = 0
    elif fault == "deleting":
        live_source["metadata"]["deletionTimestamp"] = "now"
    elif fault == "source-owner":
        live_source["metadata"]["annotations"] = {"cxcli.nebius.com/app-owner": "foreign"}
    elif fault == "controller":
        live_source["metadata"]["ownerReferences"] = [{"uid": "foreign"}]
    elif fault == "tag":
        for item in (source, prior_source, live_source):
            item["spec"]["ref"] = {"tag": "1.0.0"}
    (tmp_path / "kustomization.yaml").write_text("resources: [source.yaml, release.yaml]\n")
    (tmp_path / "source.yaml").write_text(yaml.safe_dump(source))
    (tmp_path / "release.yaml").write_text(yaml.safe_dump(release))
    calls = []

    def transport(argv, env):
        calls.append(argv)
        if argv[:2] == ["helm", "version"]:
            return {"version": "v4.3.0"}
        if argv[:2] == ["helm", "list"]:
            return []
        if argv[2] == "helmreleases.helm.toolkit.fluxcd.io":
            return {"items": live_releases}
        return live_source

    monkeypatch.setattr(ordinary_apps, "_live_json", transport)
    if fault:
        with pytest.raises(RuntimeError, match="another owner"):
            ordinary_apps.validate_live_app_ownership(
                tmp_path, extra_env={}, accepted_documents=accepted
            )
        assert (tmp_path / "source.yaml").exists()
    else:
        ordinary_apps.validate_live_app_ownership(
            tmp_path, extra_env={}, accepted_documents=accepted
        )
        assert not (tmp_path / "source.yaml").exists()
        assert yaml.safe_load((tmp_path / "kustomization.yaml").read_text())["resources"] == [
            "release.yaml"
        ]
        assert (tmp_path / "release.yaml").exists()
        assert "annotations" not in live_source["metadata"]
    assert all(argv[0] == "helm" or argv[1] == "get" for argv in calls)


def test_live_external_secret_cannot_claim_foreign_secret(project, monkeypatch):
    paths, _ = project
    root = flux_target_dir(paths, "cluster") / "ordinary"
    root.mkdir()
    (root / "kustomization.yaml").write_text("resources: [secret.yaml]\n")
    (root / "secret.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "external-secrets.io/v1",
                "kind": "ExternalSecret",
                "metadata": {
                    "name": "other",
                    "namespace": "shared",
                    "annotations": {"cxcli.nebius.com/app-owner": "owner"},
                },
                "spec": {"target": {"name": "protected", "creationPolicy": "Merge"}},
            }
        )
    )

    def live(argv, _env):
        if argv[:2] == ["helm", "version"]:
            return {"version": "v4.3.0"}
        if argv[:3] == ["kubectl", "get", "secret"]:
            return {"metadata": {"uid": "foreign"}}
        return [] if argv[0] == "helm" else {}

    monkeypatch.setattr(ordinary_apps, "_live_json", live)
    with pytest.raises(RuntimeError, match="target Secret"):
        ordinary_apps.validate_live_app_ownership(root, extra_env={})


@pytest.mark.parametrize("status", ["failed", "pending-install", "uninstalled"])
def test_helm4_inventory_keeps_non_deployed_release_ownership(monkeypatch, tmp_path, status):
    desired = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "viewer", "namespace": "apps"},
        "spec": {},
    }
    monkeypatch.setattr(ordinary_apps, "resource_documents", lambda _: [desired])

    def live(argv, env):
        if argv[:2] == ["helm", "version"]:
            return {"version": "v4.3.0"}
        if argv[0] == "helm":
            if "--all" in argv:
                raise RuntimeError("unknown flag: --all")
            assert argv == ["helm", "list", "-A", "-o", "json"]
            return [{"name": "viewer", "namespace": "apps", "status": status}]
        return {"items": []}

    monkeypatch.setattr(ordinary_apps, "_live_json", live)
    with pytest.raises(RuntimeError, match="Helm release already exists"):
        ordinary_apps.validate_live_app_ownership(tmp_path, extra_env={})


@pytest.mark.parametrize("colliding", [False, True])
def test_existing_upstream_grafana_is_neither_adopted_nor_removed(tmp_path, monkeypatch, colliding):
    upstream = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "vm-stack", "namespace": "soperator"},
        "spec": {"releaseName": "upstream-grafana", "targetNamespace": "soperator"},
    }
    before = copy.deepcopy(upstream)
    desired = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": "grafana",
            "namespace": "observability",
            "annotations": {"cxcli.nebius.com/app-owner": "ordinary-owner"},
        },
        "spec": dict(upstream["spec"]) if colliding else {},
    }
    (tmp_path / "kustomization.yaml").write_text("resources: [grafana.yaml]\n")
    rendered = yaml.safe_dump(desired)
    (tmp_path / "grafana.yaml").write_text(rendered)
    commands = []

    def live(argv, env):
        commands.append(argv)
        if argv[:2] == ["helm", "version"]:
            return {"version": "v4.3.0"}
        if argv[:2] == ["helm", "list"]:
            return [{"name": "upstream-grafana", "namespace": "soperator"}]
        assert argv == ["kubectl", "get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json"]
        return {"items": [upstream]}

    monkeypatch.setattr(ordinary_apps, "_live_json", live)
    if colliding:
        with pytest.raises(RuntimeError, match="managed by another Flux resource"):
            ordinary_apps.validate_live_app_ownership(tmp_path, extra_env={})
    else:
        ordinary_apps.validate_live_app_ownership(tmp_path, extra_env={})
    assert upstream == before
    assert (tmp_path / "grafana.yaml").read_text() == rendered
    assert len(commands) == 3


def test_lifecycle_generation_preserves_app_bytes_and_runtime_contract(project):
    from nebius_cxcli.render import staged_generated_paths

    paths, payload = project
    ordinary_apps.render_ordinary_apps(add_app(paths, payload), paths)
    current = load_generated_manifest(paths.generated_dir)
    staged = staged_generated_paths(paths)
    staged.generated_dir.mkdir(parents=True, exist_ok=True)
    candidate = copy.deepcopy(current)
    candidate["runtime_config"]["apps"]["charts"][1]["version"] = "unexpected-catalog-drift"
    manifest_path_for_generated_dir(staged.generated_dir).write_text(json.dumps(candidate))
    destination = flux_target_dir(staged, "cluster") / "ordinary"
    destination.mkdir(parents=True)
    (destination / "unexpected.yaml").write_text("unexpected: content\n")
    ordinary_apps.preserve_ordinary_app_generation(paths, staged)
    result = load_generated_manifest(staged.generated_dir)
    assert result["runtime_config"]["apps"] == current["runtime_config"]["apps"]
    for relative in current["render"]["ordinary_files"]:
        assert (staged.generated_dir / relative).read_bytes() == (
            paths.generated_dir / relative
        ).read_bytes()
    assert not (destination / "unexpected.yaml").exists()
    ordinary_apps.reset_generated_bundle(staged)


@pytest.mark.parametrize("existing_namespace", [False, True])
def test_apply_binds_identity_and_never_uses_terraform_or_protected_resources(
    project, monkeypatch, existing_namespace
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from nebius_cxcli.grafana_runtime import (
        GRAFANA_TARGET_CLUSTER_ID_ENV,
        GRAFANA_TARGET_KUBE_CONTEXT_ENV,
    )

    paths, payload = project
    payload = add_app(paths, payload)
    ordinary_apps.render_ordinary_apps(payload, paths)
    manifest = load_generated_manifest(paths.generated_dir)
    source_dir = flux_target_dir(paths, "cluster") / "ordinary"
    source_files = {path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()}
    namespace = next(
        doc for doc in ordinary_apps.resource_documents(source_dir) if doc["kind"] == "Namespace"
    )
    applied = []
    checks = []

    def handoff(_config, _paths, **kwargs):
        assert kwargs["allow_terraform_output"] is False
        assert kwargs["target"]["cluster_id"] == "cluster-example"
        assert not kwargs["persist_local_kubeconfig"]
        return {
            GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-example",
            GRAFANA_TARGET_KUBE_CONTEXT_ENV: "exact-context",
        }

    def apply(paths, **kwargs):
        kwargs["assert_authority"]()
        resources = yaml.safe_load((paths.flux_dir / "kustomization.yaml").read_text())["resources"]
        assert all((paths.flux_dir / resource).is_file() for resource in resources)
        applied.extend(ordinary_apps.resource_documents(paths.flux_dir))
        assert all(row["id"] != "soperator" for row in kwargs["config"]["apps"]["charts"])

    monkeypatch.setattr(
        ordinary_apps,
        "SoperatorOperationLease",
        lambda **kwargs: nullcontext(SimpleNamespace(assert_held=lambda: checks.append(True))),
    )

    def live(argv, _env):
        if argv[:2] == ["helm", "version"]:
            return {"version": "v4.3.0"}
        if existing_namespace and argv[:3] == ["kubectl", "get", "Namespace"]:
            return namespace
        return [] if argv[0] == "helm" else {}

    monkeypatch.setattr(ordinary_apps, "_live_json", live)
    services = ordinary_apps.OrdinaryAppServices(
        prepare_handoff=handoff,
        read_kubernetes_uid=lambda **_kwargs: "uid-example",
        ensure_grafana=lambda *_a, **_k: None,
        apply_flux=apply,
        collect_grafana=lambda *_a, **_k: [],
        emit=lambda *_a: None,
    )
    ordinary_apps.apply_ordinary_apps(
        payload,
        paths,
        manifest,
        targets=[{"target_ref": "cluster"}],
        services=services,
        assert_project_authority=lambda: None,
    )
    assert checks and any(doc["kind"] == "HelmRelease" for doc in applied)
    assert not any(doc.get("metadata", {}).get("name") == "soperator-values" for doc in applied)
    assert any(doc["kind"] == "Namespace" for doc in applied) is not existing_namespace
    assert {path: path.read_bytes() for path in source_files} == source_files


def test_stale_component_selection_cannot_overwrite_new_app(project):
    paths, payload = project
    preimage = paths.config_path.read_bytes()
    candidate = copy.deepcopy(payload)
    candidate["apps"]["charts"].append(
        {"id": "first-app", "instance_id": "cluster", "enabled": True}
    )
    ordinary_apps.publish_ordinary_app_config(paths, candidate, expected_bytes=preimage)
    with pytest.raises(RuntimeError, match="Config changed"):
        ordinary_apps.publish_ordinary_app_config(paths, payload, expected_bytes=preimage)
    assert any(
        row["id"] == "first-app"
        for row in yaml.safe_load(paths.config_path.read_text())["apps"]["charts"]
    )


def test_mixed_project_keeps_normal_mk8s_apps_in_ordinary_scope(project, monkeypatch):
    from nebius_cxcli import flux_render
    from nebius_cxcli.render import staged_generated_paths

    paths, payload = project
    payload = add_app(paths, payload)
    payload["infra"]["components"].append(
        {"id": "mk8s", "instance_id": "other", "enabled": True, "inputs": {}}
    )
    payload["deploy"]["targets"].append({"instance_id": "other"})
    payload["apps"]["charts"].append(
        {
            **copy.deepcopy(payload["apps"]["charts"][-1]),
            "instance_id": "other",
            "target_ref": "other",
        }
    )
    specs = flux_render._configured_app_release_specs
    # Isolate upstream source retrieval; retain actual per-target Flux renderer.
    monkeypatch.setattr(
        flux_render,
        "_configured_app_release_specs",
        lambda config, **kwargs: specs(config, ordinary_only=True),
    )
    staged = staged_generated_paths(paths)
    flux_render.render_flux(payload, staged)
    for target in ("cluster", "other"):
        root = flux_target_dir(staged, target)
        assert not any(
            doc["kind"] == "HelmRelease" for doc in ordinary_apps.resource_documents(root)
        )
        assert any(
            doc["kind"] == "HelmRelease"
            for doc in ordinary_apps.resource_documents(root / "ordinary")
        )
    # Verify the real renderer's sibling bundle, then exercise its canonical apply owner.
    from nebius_cxcli import cli
    from nebius_cxcli.deployment_observation import target_documents
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from nebius_cxcli.deployment_target import apply_ordinary_bundle

    targets = [
        {"target_ref": ref, "flux_dir": str(flux_target_dir(staged, ref))}
        for ref in ("cluster", "other")
    ]
    frozen = DeploymentGeneration.capture(staged, {"deploy": {"targets": targets}})
    calls = []
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda p, **kw: calls.append(ordinary_apps.resource_documents(p.flux_dir)),
    )
    for ref in ("cluster", "other"):
        assert any(doc["kind"] == "HelmRelease" for doc in target_documents(frozen, ref).values())
        apply_ordinary_bundle(
            cli,
            __import__("dataclasses").replace(staged, flux_dir=flux_target_dir(staged, ref)),
            kube_env={},
            assert_authority=lambda: None,
            config=payload,
            target_ref=ref,
        )
    assert len(calls) == 2 and all(
        any(doc["kind"] == "HelmRelease" for doc in docs) for docs in calls
    )
    ordinary_apps.reset_generated_bundle(staged)


def test_static_key_provider_write_rechecks_app_authority(monkeypatch):
    from nebius_cxcli.app_mutation import app_mutation_scope
    from nebius_cxcli.iam_bootstrap import _credential_provider_result

    def lost():
        raise RuntimeError("lease lost")

    with app_mutation_scope(lost), pytest.raises(RuntimeError, match="lease lost"):
        _credential_provider_result("issue", lambda: pytest.fail("IAM write after lost lease"))


@pytest.mark.parametrize("status", ["planned", "failed"])
def test_unfinished_destroy_does_not_block_config_only_app_authoring(project, status):
    from nebius_cxcli.destroy import DESTROY_SCHEMA
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    paths, payload = project
    write_owner_only_json(
        paths.reports_dir / "destroy-cluster.json",
        {
            "schema": DESTROY_SCHEMA,
            "target_ref": "cluster",
            "status": status,
            "failure_classification": "provider-operation-failed",
            "checkpoints": [],
        },
    )
    before = ordinary_apps.protected_config_digest(payload)
    candidate = copy.deepcopy(payload)
    candidate["apps"]["charts"].append(
        {"id": "metrics", "instance_id": "metrics", "enabled": True, "version": "1.0.0"}
    )
    assert ordinary_apps.publish_ordinary_app_config(paths, candidate)
    authored = yaml.safe_load(paths.config_path.read_text())
    assert ordinary_apps.protected_config_digest(authored) == before
    assert authored["apps"]["charts"][-1]["id"] == "metrics"


def test_explicit_grafana_routing_publication_preserves_native_admission(project):
    from nebius_cxcli.observability_routing import resolve_settings, save_settings

    paths, source = project
    candidate = copy.deepcopy(source)
    candidate["apps"]["charts"].append({"id": "grafana", "instance_id": "cluster", "enabled": True})
    save_settings(candidate, "cluster", resolve_settings(candidate, "cluster"))
    before = ordinary_apps.protected_config_digest(source)
    with pytest.raises(RuntimeError, match="protected configuration"):
        ordinary_apps.publish_ordinary_app_config(paths, candidate)
    assert ordinary_apps.publish_ordinary_app_config(
        paths, candidate, observability_target_refs=frozenset({"cluster"})
    )
    saved = yaml.safe_load(paths.config_path.read_text())
    assert saved["infra"] == source["infra"]
    assert saved["apps"]["charts"][0] == source["apps"]["charts"][0]
    # Native routing still forces full lifecycle admission after config-only save.
    assert ordinary_apps.protected_config_digest(saved) != before
    with pytest.raises(RuntimeError, match="Protected Soperator configuration"):
        ordinary_apps.validate_ordinary_app_scope(paths)


def test_post_flux_waits_for_secret_store_before_external_secret(tmp_path, monkeypatch):
    from pathlib import Path

    from nebius_cxcli import cli

    manifest = tmp_path / "post-flux-mysterybox-eso.yaml"
    manifest.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "external-secrets.io/v1",
                    "kind": "ExternalSecret",
                    "metadata": {"name": "consumer", "namespace": "ordinary"},
                    "spec": {},
                },
                {
                    "apiVersion": "external-secrets.io/v1",
                    "kind": "ClusterSecretStore",
                    "metadata": {"name": "shared"},
                    "spec": {},
                },
            ]
        )
    )
    calls = []

    def run(argv, **_kwargs):
        if argv[1] == "apply":
            calls.append(("apply", yaml.safe_load(Path(argv[-1]).read_text())["kind"]))
        elif argv[1] == "wait":
            calls.append(("wait", argv[4].split("/")[0]))

    monkeypatch.setattr(cli, "_run_post_flux_kubectl", run)
    cli._apply_post_flux_manifest(manifest, env={})
    assert calls == [
        ("apply", "ClusterSecretStore"),
        ("wait", "ClusterSecretStore"),
        ("apply", "ExternalSecret"),
        ("wait", "ExternalSecret"),
    ]


def test_lifecycle_accepts_and_applies_both_mixed_target_identities(project, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from nebius_cxcli import cli
    from nebius_cxcli.grafana_runtime import (
        GRAFANA_TARGET_CLUSTER_ID_ENV,
        GRAFANA_TARGET_KUBE_CONTEXT_ENV,
    )

    paths, payload = project
    payload = add_app(paths, payload)
    payload["infra"]["components"].append(
        {"id": "mk8s", "instance_id": "other", "enabled": True, "inputs": {}}
    )
    payload["deploy"]["targets"].append({"instance_id": "other"})
    payload["apps"]["charts"].append(
        {
            **copy.deepcopy(payload["apps"]["charts"][-1]),
            "instance_id": "other",
            "target_ref": "other",
        }
    )
    paths.config_path.write_text(yaml.safe_dump(payload))
    manifest = load_generated_manifest(paths.generated_dir)
    manifest["runtime_config"] = payload
    manifest["deploy"]["targets"].append(
        {
            **manifest["deploy"]["targets"][0],
            "target_ref": "other",
            "instance_id": "other",
            "flux_dir": str(flux_target_dir(paths, "other")),
        }
    )
    manifest_path_for_generated_dir(paths.generated_dir).write_text(json.dumps(manifest))
    visited = []

    def handoff(_config, _paths, **kwargs):
        target = kwargs["target"]["target_ref"]
        if kwargs.get("allow_terraform_output") is False:
            assert kwargs["target"]["cluster_id"] == "cluster-" + target
        return {
            GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-" + target,
            GRAFANA_TARGET_KUBE_CONTEXT_ENV: target,
        }

    def apply(target_paths, **kwargs):
        visited.append(kwargs["target_ref"])
        docs = ordinary_apps.resource_documents(target_paths.flux_dir)
        releases = [doc for doc in docs if doc["kind"] == "HelmRelease"]
        assert len(releases) == 1
        assert kwargs["require_existing_flux"] is True
        kwargs["assert_authority"]()

    services = ordinary_apps.OrdinaryAppServices(
        prepare_handoff=handoff,
        read_kubernetes_uid=lambda **kwargs: "uid-" + kwargs["kube_context"],
        ensure_grafana=lambda *_a, **_k: None,
        apply_flux=apply,
        collect_grafana=lambda *_a, **_k: [],
        emit=lambda *_a: None,
    )
    workflow = ordinary_apps.OrdinaryAppWorkflow(
        services=lambda: services, resolve_targets=cli._resolve_selected_deploy_targets
    )
    workflow.accept_baseline(
        paths,
        identities={"cluster": {"cluster_id": "cluster-cluster", "kubernetes_uid": "uid-cluster"}},
    )
    ordinary_apps.render_ordinary_apps(payload, paths)
    monkeypatch.setattr(
        ordinary_apps,
        "SoperatorOperationLease",
        lambda **kwargs: nullcontext(SimpleNamespace(assert_held=lambda: None)),
    )
    monkeypatch.setattr(
        ordinary_apps,
        "_live_json",
        lambda argv, _env: (
            {"version": "v4.3.0"}
            if argv[:2] == ["helm", "version"]
            else []
            if argv[0] == "helm"
            else {}
        ),
    )
    workflow.apply(
        payload,
        paths,
        load_generated_manifest(paths.generated_dir),
        target_ref=None,
        all_targets=True,
        assert_project_authority=lambda: None,
    )
    assert visited == ["cluster", "other"]


@pytest.mark.parametrize("conflict", ["preimage", "unsafe"])
def test_baseline_publication_skips_only_concurrent_preimage_changes(
    project, monkeypatch, conflict
):
    from nebius_cxcli.project_bundle_transaction import ProjectBundlePreimageConflict

    paths, _ = project
    baseline = paths.reports_dir / ordinary_apps.BASELINE_FILENAME
    previous = baseline.read_bytes()

    def commit(*args, **kwargs):
        if conflict == "preimage":
            paths.config_path.write_text(
                paths.config_path.read_text() + "# concurrent author edit\n"
            )
            raise ProjectBundlePreimageConflict("source changed")
        raise RuntimeError("unsafe symlink boundary")

    monkeypatch.setattr(ordinary_apps.ProjectBundleTransaction, "commit", commit)
    if conflict == "preimage":
        assert ordinary_apps.accept_ordinary_app_baseline(paths) is False
        assert paths.config_path.read_text().endswith("# concurrent author edit\n")
    else:
        with pytest.raises(RuntimeError, match="unsafe"):
            ordinary_apps.accept_ordinary_app_baseline(paths)
    assert baseline.read_bytes() == previous


@pytest.mark.parametrize("version", ["v3.19.0", "v5.0.0", "", "invalid"])
def test_unsupported_helm_fails_before_live_ownership_inventory(monkeypatch, tmp_path, version):
    monkeypatch.setattr(ordinary_apps, "resource_documents", lambda _: [])

    def live(argv, env):
        assert argv[:2] == ["helm", "version"]
        return {"version": version}

    monkeypatch.setattr(ordinary_apps, "_live_json", live)
    with pytest.raises(RuntimeError, match="require Helm 4"):
        ordinary_apps.validate_live_app_ownership(tmp_path, extra_env={})
