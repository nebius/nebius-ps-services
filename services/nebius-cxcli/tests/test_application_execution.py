from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.application_compatibility import (
    admit_applications,
    application_files,
    captured_application_execution,
)
from nebius_cxcli.application_upgrade import prepare_chart_upgrade
from nebius_cxcli.compatibility_artifacts import capture_chart, frozen_chart
from nebius_cxcli.compatibility_execution import freeze_compatibility
from nebius_cxcli.deploy_targets import flux_target_dir
from nebius_cxcli.generated_manifest import build_generated_manifest, runtime_config_from_manifest
from nebius_cxcli.paths import resolve_project_paths

pytestmark = pytest.mark.usefixtures("offline_shared_lease")


@pytest.fixture
def application_project(tmp_path, monkeypatch):
    """Native Helm inspection/rendering; only artifact transport and live reads are fake."""
    pulls = []
    constraints = {}
    extra_manifests = {}

    def resolve(reference):
        cached = frozen_chart(reference)
        if cached is not None:
            return cached
        pulls.append(reference)
        root = tmp_path / "artifacts" / str(len(pulls))
        root.mkdir(parents=True)
        metadata = {
            "apiVersion": "v2",
            "name": reference.chart_name.split("/")[-1],
            "version": reference.chart_version,
        }
        if reference.chart_version in constraints:
            metadata["kubeVersion"] = constraints[reference.chart_version]
        (root / "Chart.yaml").write_text(yaml.safe_dump(metadata))
        (root / "values.yaml").write_text("{}\n")
        if reference.chart_version in extra_manifests:
            (root / "templates").mkdir(exist_ok=True)
            (root / "templates/extra.yaml").write_text(extra_manifests[reference.chart_version])
        if "network-operator" in reference.chart_name:
            (root / "templates").mkdir(exist_ok=True)
            (root / "templates" / "controller.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": "network-operator"},
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "name": "operator",
                                            "image": "nvcr.io/nvidia/cloud-native/network-operator:"
                                            + reference.chart_version,
                                        }
                                    ]
                                }
                            }
                        },
                    }
                )
            )
        return capture_chart(
            reference,
            root,
            oci_digest=("sha256:" + "a" * 64)
            if reference.chart_repo.startswith("oci://")
            else None,
        )

    monkeypatch.setattr("nebius_cxcli.compatibility_artifacts.resolve_chart_input", resolve)

    def create(
        *,
        repos=("oci://registry.test/cert-manager",),
        versions=None,
        component="cert-manager",
        kubernetes="1.35.5",
    ):
        versions = versions or ["1.0.0"] * len(repos)
        root = tmp_path / "deployments" / "tenant" / "project"
        root.mkdir(parents=True, exist_ok=True)
        paths = resolve_project_paths(root / "config.yaml")
        payload = {
            "client_info": {
                "client_name": "example",
                "nebius": {
                    "project_id": "project-example",
                    "tenant_id": "tenant-example",
                    "region_id": "eu-north2",
                },
            },
            "infra": {"components": []},
            "apps": {"charts": []},
            "deploy": {"targets": []},
        }
        targets = []
        for n, (repo, version) in enumerate(zip(repos, versions, strict=True)):
            ref = f"cluster{n}"
            payload["infra"]["components"].append(
                {
                    "id": "mk8s",
                    "instance_id": ref,
                    "enabled": True,
                    "inputs": {"cluster": {"k8s_version": kubernetes}},
                }
            )
            payload["deploy"]["targets"].append({"instance_id": ref})
            payload["apps"]["charts"].append(
                {
                    "id": component,
                    "instance_id": ref,
                    "enabled": True,
                    "repo": repo,
                    "version": version,
                    "values": {},
                    "namespace": "cert-manager",
                    "release-name": "cert-manager",
                }
            )
            directory = flux_target_dir(paths, ref)
            directory.mkdir(parents=True)
            (directory / "helmrelease-cert-manager.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "helm.toolkit.fluxcd.io/v2",
                        "kind": "HelmRelease",
                        "metadata": {"name": "cert-manager", "namespace": "cert-manager"},
                        "spec": {"values": {}},
                    }
                )
            )
            (directory / "kustomization.yaml").write_text(
                "resources: [helmrelease-cert-manager.yaml]\n"
            )
            targets.append(
                {
                    "component_id": "mk8s",
                    "instance_id": ref,
                    "target_ref": ref,
                    "ownership": "managed",
                    "cluster_id_output_name": ref + "_cluster_id",
                    "component_output_ref": ref + ".cluster_id",
                    "cluster_id": f"cluster-id-{n}",
                    "access": "external",
                    "flux_dir": str(directory),
                }
            )
        payload["compatibility"] = {
            "targets": {target["target_ref"]: {"version_set": None} for target in targets}
        }
        paths.config_path.write_text(yaml.safe_dump(payload))
        paths.infra_dir.mkdir()
        frozen = freeze_compatibility(payload, paths)
        manifest = build_generated_manifest(
            config=payload,
            paths=paths,
            targets=targets,
            required_component_outputs=[],
            compatibility=frozen,
            application_files=application_files(paths),
        )
        (paths.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
        config = runtime_config_from_manifest(manifest)
        monkeypatch.setattr(
            cli, "_load_generated_flux_context", lambda _: (config, paths, manifest)
        )
        monkeypatch.setattr(
            cli, "_load_deploy_context_readonly", lambda _: (config, paths, manifest)
        )
        return payload, config, paths, manifest

    create.pulls = pulls
    create.constraints = constraints
    create.extra_manifests = extra_manifests
    return create


def test_native_chart_evidence_accepts_bare_equals_crd_enum(application_project):
    application_project.extra_manifests["1.0.0"] = """apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: examples.test
spec:
  versions:
    - name: v1
      schema:
        openAPIV3Schema:
          type: string
          enum:
            - =
            - '!='
---
apiVersion: v1
kind: Pod
metadata:
  name: example
spec:
  containers:
    - name: example
      image: registry.test/example:1.0.0
"""
    _, config, paths, manifest = application_project()
    frozen = manifest["render"]["compatibility"]
    assert any(
        row.get("image_reference") == "registry.test/example:1.0.0" for row in frozen["inventory"]
    )
    from nebius_cxcli.compatibility_execution import validate_frozen_compatibility
    from nebius_cxcli.compatibility_matrix import load_matrix

    validate_frozen_compatibility(config, frozen, matrix=load_matrix())
    assert frozen["chart_inputs"]


def observed(_cli, _config, _paths, _manifest, targets, *, plan=None):
    return {
        target["target_ref"]: {
            "kubernetes_version": "1.35.5",
            "kubernetes_uid": "uid-" + target["target_ref"],
            "cluster_id": target.get("cluster_id", "cluster-id-0"),
            "release": {
                "name": "cert-manager",
                "namespace": "cert-manager",
                "chart_version": "1.0.0",
                "revision": "1",
            },
        }
        for target in targets
    }


def forbid_effects(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Application effects must not occur before admission")

    for name in (
        "_ensure_runtime_auth_material",
        "_ensure_terraform_backend_ready",
        "_prepare_cluster_handoff_kube_env",
        "_ensure_grafana_runtime_before_flux",
        "_apply_rendered_flux_with_soperator_job_policy",
    ):
        monkeypatch.setattr(cli, name, fail)


def test_public_flux_rejects_changed_http_chart_before_any_effect(
    application_project, monkeypatch, capsys
):
    _, _, paths, _ = application_project(repos=("https://charts.example.test",))
    application_project.constraints["1.0.0"] = ">=1.0.0"
    forbid_effects(monkeypatch)
    with pytest.raises(cli.typer.Exit) as error:
        cli.flux_apply_command(paths.generated_dir, target_ref="cluster0")
    assert error.value.exit_code == 1
    assert "HTTP chart contents changed since render" in capsys.readouterr().out
    assert not paths.reports_dir.exists()


def render_application_project(payload, paths, prior, outputs):
    from nebius_cxcli.application_compatibility import bind_application_artifacts

    for path in paths.flux_dir.rglob("*.yaml"):
        path.unlink()
    cli.render_flux(payload, paths, component_output_values=outputs)
    frozen = freeze_compatibility(payload, paths)
    bind_application_artifacts(paths, payload, frozen["chart_inputs"])
    paths.config_path.write_text(yaml.safe_dump(payload))
    manifest = build_generated_manifest(
        config=payload,
        paths=paths,
        targets=[
            {**row, "flux_dir": str(flux_target_dir(paths, row["target_ref"]))}
            for row in prior["deploy"]["targets"]
        ],
        required_component_outputs=cli._required_runtime_component_output_specs(payload),
        compatibility=frozen,
        application_files=application_files(paths),
    )
    config = runtime_config_from_manifest(manifest)
    return config, manifest


@pytest.mark.parametrize("binding", ["unresolved", "resolved", "disabled"])
def test_public_flux_nfs_binding_admission(application_project, monkeypatch, capsys, binding):
    payload, _, paths, prior = application_project(
        component="csi-driver-nfs", repos=("oci://registry.test/csi-driver-nfs",)
    )
    payload["infra"]["components"].append(
        {
            "id": "nfs",
            "instance_id": "nfs",
            "enabled": True,
            "inputs": {"kubernetes_target_ref": "cluster0"},
        }
    )
    if binding == "disabled":
        payload["apps"]["charts"][0]["values"] = {"storageClass": {"create": False}}
    outputs = (
        {"nfs.server_ip": "192.0.2.10", "nfs.export_path": "/data", "nfs.mount_options": []}
        if binding == "resolved"
        else {}
    )
    config, manifest = render_application_project(payload, paths, prior, outputs)
    if binding != "unresolved":
        result = admit_applications(config, paths, manifest, target_refs=["cluster0"])
        assert result["execution_scope"] == "applications"
        return
    monkeypatch.setattr(cli, "_load_generated_flux_context", lambda _: (config, paths, manifest))
    with pytest.raises(ValueError, match="NFS.*binding"):
        admit_applications(config, paths, manifest, target_refs=["cluster0"])
    forbid_effects(monkeypatch)
    with pytest.raises(cli.typer.Exit):
        cli.flux_apply_command(paths.generated_dir, target_ref="cluster0")
    assert "NFS StorageClass binding is unresolved" in capsys.readouterr().out


@pytest.mark.parametrize(
    "binding, api_version",
    [
        ("unresolved", None),
        ("partial_secret", None),
        ("partial_key", None),
        ("resolved", None),
        ("wrong_api_version", "external-secrets.io/"),
        ("wrong_api_version", "external-secrets.io/v1/extra"),
        ("wrong_api_version", "external-secrets.io/invalid"),
        ("wrong_api_version", "external-secrets.io/v1beta1"),
        ("wrong_api_version", "external-secrets.io.example.test/v1"),
        ("wrong_api_version", "example.test/external-secrets.io/v1"),
        ("wrong_api_version", "external-secrets.io"),
        ("wrong_api_version", ""),
        ("wrong_api_version", None),
        ("missing_api_version", None),
    ],
)
def test_mysterybox_binding_admission(application_project, binding, api_version):
    payload, _, paths, prior = application_project(
        component="external-secrets", repos=("oci://registry.test/external-secrets",)
    )
    payload["infra"]["components"].append(
        {
            "id": "mysterybox",
            "instance_id": "secrets",
            "enabled": True,
            "inputs": {
                "secrets": [
                    {
                        "name": "database",
                        "payload": {"username": {"type": "text"}, "password": {"type": "text"}},
                    },
                    {"name": "api", "payload": {"key": {"type": "text"}}},
                ]
            },
        }
    )
    payload["deploy"]["targets"][0]["secrets"] = {
        "mysterybox": {"enabled": True, "sync_namespaces": ["default"]}
    }
    secret_ids = {} if binding == "unresolved" else {"database": "secret-database"}
    if binding in {"resolved", "partial_key", "wrong_api_version", "missing_api_version"}:
        secret_ids["api"] = "secret-api"
    config, manifest = render_application_project(
        payload, paths, prior, {"secrets.secret_ids": secret_ids}
    )
    if binding in {"partial_key", "wrong_api_version", "missing_api_version"}:
        file = flux_target_dir(paths, "cluster0") / "post-flux-mysterybox-eso.yaml"
        documents = list(yaml.safe_load_all(file.read_text()))
        external_secret = next(doc for doc in documents if doc["kind"] == "ExternalSecret")
        assert external_secret["apiVersion"] == "external-secrets.io/v1"
        if binding == "partial_key":
            external_secret["spec"]["data"].pop()
        elif binding == "missing_api_version":
            del external_secret["apiVersion"]
        else:
            external_secret["apiVersion"] = api_version
        file.write_text(yaml.safe_dump_all(documents))
        manifest["render"]["application_files"] = application_files(paths)
    if binding == "resolved":
        assert admit_applications(config, paths, manifest, target_refs=["cluster0"])
    else:
        incomplete_identity = binding == "missing_api_version" or (
            binding == "wrong_api_version" and not api_version
        )
        error_type = RuntimeError if incomplete_identity else ValueError
        error_message = (
            "Desired resource has incomplete identity"
            if incomplete_identity
            else "MysteryBox secret binding is unresolved"
        )
        with pytest.raises(error_type, match=error_message):
            admit_applications(config, paths, manifest, target_refs=["cluster0"])
        # Unselected targets do not require this target's bindings.
        assert admit_applications(config, paths, manifest, target_refs=[])


@pytest.mark.parametrize(
    "binding", ["resolved", "unrelated", "unresolved", "custom_driver", "wrong_driver"]
)
def test_local_nfs_binding_checks_native_storage_class(tmp_path, monkeypatch, binding):
    from dataclasses import replace

    from nebius_cxcli import nfs_csi
    from nebius_cxcli.components import component_lookup
    from nebius_cxcli.flux_render import (
        _materialize_nfs_csi_storage_class_values,
        _render_local_helm_chart,
    )

    chart = tmp_path / "nfs-chart"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: csi-driver-nfs\nversion: 1.0.0\n")
    (chart / "templates" / "storage.yaml").write_text(
        "{{ if .Values.storageClass.create }}\n"
        "apiVersion: storage.k8s.io/v1\nkind: StorageClass\n"
        "metadata: {name: {{ .Values.storageClass.name }}}\n"
        'provisioner: {{ dig "driver" "name" "nfs.csi.k8s.io" .Values.AsMap }}\n'
        "parameters: {{ toJson .Values.storageClass.parameters }}\n"
        "{{ else }}\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: driver-config}\n{{ end }}\n"
    )
    entry = replace(component_lookup("apps")["csi-driver-nfs"], source=str(chart), chart_repo="")
    monkeypatch.setattr(nfs_csi, "component_entries", lambda scope: (entry,))
    row = {
        "id": "csi-driver-nfs",
        "instance_id": "cluster0",
        "enabled": True,
        "repo": "",
        "values": {"storageClass": {"name": "expected-nfs"}},
    }
    if binding in {"custom_driver", "wrong_driver"}:
        row["values"]["driver"] = {"name": "custom.nfs.example"}
    payload = {
        "infra": {
            "components": [
                {"id": "mk8s", "instance_id": "cluster0", "enabled": True},
                {"id": "nfs", "instance_id": "nfs", "enabled": True},
            ]
        },
        "apps": {"charts": [row]},
    }
    materialized = copy.deepcopy(row)
    outputs = (
        {"nfs.server_ip": "192.0.2.10", "nfs.export_path": "/data", "nfs.mount_options": []}
        if binding != "unresolved"
        else {}
    )
    _materialize_nfs_csi_storage_class_values(
        payload=payload,
        chart_node=materialized,
        target_ref="cluster0",
        resolved_component_outputs=outputs,
    )
    documents = list(
        yaml.safe_load_all(
            _render_local_helm_chart(
                release_name="nfs",
                namespace="kube-system",
                chart_path=str(chart),
                values=materialized["values"],
            )
        )
    )
    if binding == "unrelated":
        documents[0]["metadata"]["name"] = "unrelated-nfs"
    if binding == "wrong_driver":
        documents[0]["provisioner"] = "other.nfs.example"
    assert "server" not in row["values"]["storageClass"].get("parameters", {})
    if binding in {"resolved", "custom_driver"}:
        nfs_csi.require_rendered_nfs_csi_bindings(
            payload, target_ref="cluster0", documents=documents
        )
    else:
        with pytest.raises(ValueError, match="NFS StorageClass binding is unresolved"):
            nfs_csi.require_rendered_nfs_csi_bindings(
                payload, target_ref="cluster0", documents=documents
            )


def test_second_target_failure_prevents_first_target_effects(application_project, monkeypatch):
    _, _, paths, _ = application_project(
        repos=("oci://registry.test/cert-manager", "https://charts.example.test")
    )
    application_project.constraints["1.0.0"] = ">=1.0.0"
    forbid_effects(monkeypatch)
    with pytest.raises(cli.typer.Exit):
        cli.flux_apply_command(paths.generated_dir, all_targets=True)


def test_application_scope_excludes_terraform_and_unselected_checks(application_project):
    _, config, paths, manifest = application_project(
        repos=("oci://registry.test/cert-manager", "https://charts.example.test")
    )
    downloads = len(application_project.pulls)
    result = admit_applications(config, paths, manifest, target_refs=["cluster0"])
    assert len(application_project.pulls) == downloads
    assert result["execution_scope"] == "applications"
    assert all(row["subject"]["kind"] != "terraform" for row in result["rows"])
    broken = copy.deepcopy(manifest)
    broken["render"]["compatibility"]["inventory"].pop()
    with pytest.raises(ValueError, match="integrity"):
        admit_applications(config, paths, broken, target_refs=["cluster0"])


@pytest.mark.parametrize("ordinary", [False, True])
def test_application_admission_accepts_http_and_oci_without_changing_files(
    application_project, ordinary
):
    _, config, paths, manifest = application_project(
        repos=("https://charts.example.test", "oci://registry.test/cert-manager")
    )
    before = application_files(paths)
    downloads = len(application_project.pulls)
    with captured_application_execution(
        config, paths, manifest, target_refs=["cluster0", "cluster1"], ordinary=ordinary
    ) as (stage, _, report):
        assert application_files(stage) == before
        assert report["execution_scope"] == "applications"
    assert application_files(paths) == before
    assert len(application_project.pulls) == downloads + 1
    assert application_project.pulls[-1].chart_repo == "https://charts.example.test"


def test_full_admission_checks_http_content(application_project):
    from nebius_cxcli.compatibility_execution import admit_compatibility

    payload, _, paths, _ = application_project(repos=("https://charts.example.test",))
    payload["infra"]["components"] = []
    frozen = freeze_compatibility(payload, paths)
    assert admit_compatibility(payload, paths, frozen, terraform_validated=True)
    application_project.constraints["1.0.0"] = ">=1.0.0"
    with pytest.raises(ValueError, match="HTTP chart contents changed since render"):
        admit_compatibility(payload, paths, frozen, terraform_validated=True)


def test_admitted_copy_does_not_follow_concurrent_artifact_edit(application_project):
    _, config, paths, manifest = application_project()
    with captured_application_execution(config, paths, manifest, target_refs=["cluster0"]) as (
        stage,
        _,
        _,
    ):
        original = flux_target_dir(paths, "cluster0") / "helmrelease-cert-manager.yaml"
        expected = original.read_bytes()
        original.write_text("changed")
        assert (flux_target_dir(stage, "cluster0") / original.name).read_bytes() == expected
    with pytest.raises(ValueError, match="files changed"):
        admit_applications(config, paths, manifest, target_refs=["cluster0"])


@pytest.mark.parametrize(
    "repository", ["oci://registry.test/cert-manager", "https://charts.example.test"]
)
def test_upgrade_dry_run_assesses_candidate_without_publication(
    application_project, monkeypatch, capsys, repository
):
    _, _, paths, _ = application_project(repos=(repository,))
    captured = {}

    @contextmanager
    def capture_candidate(*args, **kwargs):
        with prepare_chart_upgrade(*args, **kwargs) as candidate:
            captured["admission"] = copy.deepcopy(candidate.admission)
            captured["transition"] = copy.deepcopy(candidate.transition)
            yield candidate
            assert candidate.admission == captured["admission"]
            assert candidate.transition == captured["transition"]

    monkeypatch.setattr("nebius_cxcli.application_upgrade.prepare_chart_upgrade", capture_candidate)
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    before = {p: p.read_bytes() for p in paths.project_dir.rglob("*") if p.is_file()}
    forbid_effects(monkeypatch)
    cli.upgrade_helm_chart_command(
        paths.config_path, "apps:cert-manager@cluster0", to_version="1.1.0", dry_run=True
    )
    assert {p: p.read_bytes() for p in paths.project_dir.rglob("*") if p.is_file()} == before
    assert captured["admission"]["admitted"] is True
    assert captured["admission"]["rows"]
    assert captured["transition"]["states"]
    output = capsys.readouterr()
    assert "Chart upgrade assessment complete" in output.out
    assert "Component compatibility" not in output.out + output.err
    assert "Operator transition:" not in output.out + output.err


def test_upgrade_rejects_live_patch_constraint_before_publication(application_project, monkeypatch):
    application_project.constraints["1.1.0"] = ">=1.35.5"
    _, _, paths, _ = application_project()

    def lower(*args, **kwargs):
        result = observed(*args, **kwargs)
        result["cluster0"]["kubernetes_version"] = "1.35.1"
        return result

    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", lower)
    before = paths.config_path.read_bytes()
    with pytest.raises(cli.typer.Exit):
        cli.upgrade_helm_chart_command(
            paths.config_path, "apps:cert-manager@cluster0", to_version="1.1.0", dry_run=True
        )
    assert paths.config_path.read_bytes() == before


def test_operator_upgrade_keeps_transition_findings_internal(
    application_project, monkeypatch, capsys
):
    from nebius_cxcli.compatibility_matrix import FROZEN_MATRIX, load_matrix

    matrix = copy.deepcopy(load_matrix())
    repo = "oci://registry.test/network-operator"
    matrix["distributions"]["nvidia-upstream-network"]["source"] = repo
    token = FROZEN_MATRIX.set(matrix)
    captured = {}
    try:
        _, _, paths, _ = application_project(
            repos=(repo,),
            versions=["25.7.0"],
            component="nvidia-network-operator",
            kubernetes="1.33.0",
        )

        def network(*args, **kwargs):
            result = observed(*args, **kwargs)
            result["cluster0"]["kubernetes_version"] = "1.33.0"
            result["cluster0"]["release"]["chart_version"] = "25.7.0"
            return result

        @contextmanager
        def capture_candidate(*args, **kwargs):
            with prepare_chart_upgrade(*args, **kwargs) as candidate:
                captured["transition"] = copy.deepcopy(candidate.transition)
                yield candidate
                assert candidate.transition == captured["transition"]

        monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", network)
        monkeypatch.setattr(
            "nebius_cxcli.application_upgrade.prepare_chart_upgrade", capture_candidate
        )
        forbid_effects(monkeypatch)
        cli.upgrade_helm_chart_command(
            paths.config_path,
            "apps:nvidia-network-operator@cluster0",
            to_version="25.10.0",
            dry_run=True,
        )
        assert captured["transition"]["transitions"]
        output = capsys.readouterr()
        assert "Chart upgrade assessment complete" in output.out
        assert "Component compatibility" not in output.out + output.err
        assert "Operator transition:" not in output.out + output.err
    finally:
        FROZEN_MATRIX.reset(token)


def test_upgrade_candidate_compare_and_set_rejects_concurrent_edit(
    application_project, monkeypatch
):
    payload, config, paths, manifest = application_project()
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    target = cli._parse_helm_chart_upgrade_selector("apps:cert-manager@cluster0")
    plan = cli._plan_helm_chart_upgrade(payload=payload, target=target, target_version="1.1.0")
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    with prepare_chart_upgrade(
        cli, payload, paths.config_path.read_bytes(), config, paths, manifest, plan
    ) as candidate:
        paths.config_path.write_text("concurrent user edit\n")
        with pytest.raises(RuntimeError, match="changed"):
            ProjectBundleTransaction(paths.project_dir).commit(
                candidate.writes,
                removals=candidate.removals,
                expected_preimages=candidate.expected_preimages,
            )
    assert paths.config_path.read_text() == "concurrent user edit\n"


def test_upgrade_rejects_manifest_change_between_load_and_preparation(
    application_project, monkeypatch
):
    payload, config, paths, manifest = application_project()
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    target = cli._parse_helm_chart_upgrade_selector("apps:cert-manager@cluster0")
    plan = cli._plan_helm_chart_upgrade(payload=payload, target=target, target_version="1.1.0")
    path = paths.generated_dir / "nebius-cxcli-manifest.json"
    concurrent = copy.deepcopy(manifest)
    concurrent["tools"]["flux_version"] = "v99.0.0"
    path.write_text(json.dumps(concurrent))
    before = {p: p.read_bytes() for p in paths.project_dir.rglob("*") if p.is_file()}
    pulls = len(application_project.pulls)
    with (
        pytest.raises(ValueError, match="manifest changed before upgrade preparation"),
        prepare_chart_upgrade(
            cli, payload, paths.config_path.read_bytes(), config, paths, manifest, plan
        ),
    ):
        pytest.fail("Upgrade accepted a concurrent manifest edit")
    assert {p: p.read_bytes() for p in paths.project_dir.rglob("*") if p.is_file()} == before
    assert len(application_project.pulls) == pulls


@pytest.mark.parametrize("dry_run", [True, False])
def test_public_operator_downgrade_rejected_before_publication(
    application_project, monkeypatch, dry_run
):
    from nebius_cxcli.compatibility_matrix import FROZEN_MATRIX, load_matrix

    matrix = copy.deepcopy(load_matrix())
    repo = "oci://registry.test/network-operator"
    matrix["distributions"]["nvidia-upstream-network"]["source"] = repo
    token = FROZEN_MATRIX.set(matrix)
    try:
        _, _, paths, _ = application_project(
            repos=(repo,),
            versions=["25.10.0"],
            component="nvidia-network-operator",
            kubernetes="1.33.0",
        )

        def network(*args, **kwargs):
            result = observed(*args, **kwargs)
            result["cluster0"]["kubernetes_version"] = "1.33.0"
            result["cluster0"]["release"]["chart_version"] = "25.10.0"
            return result

        monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", network)
        forbid_effects(monkeypatch)
        before = {p: p.read_bytes() for p in paths.project_dir.rglob("*") if p.is_file()}
        with pytest.raises(ValueError, match="downgrade"):
            cli._run_helm_chart_upgrade_command(
                config_path=paths.config_path,
                target_selector="apps:nvidia-network-operator@cluster0",
                to_version="25.7.0",
                dry_run=dry_run,
                interactive=False,
            )
        assert {p: p.read_bytes() for p in paths.project_dir.rglob("*") if p.is_file()} == before
    finally:
        FROZEN_MATRIX.reset(token)


def test_retry_reuses_admitted_artifact_and_rejects_changed_cluster(
    application_project, monkeypatch
):
    payload, config, paths, manifest = application_project()
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    target = cli._parse_helm_chart_upgrade_selector("apps:cert-manager@cluster0")
    plan = cli._plan_helm_chart_upgrade(payload=payload, target=target, target_version="1.1.0")
    from nebius_cxcli.generated_manifest import load_generated_manifest
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    with prepare_chart_upgrade(
        cli, payload, paths.config_path.read_bytes(), config, paths, manifest, plan
    ) as candidate:
        ProjectBundleTransaction(paths.project_dir).commit(
            candidate.writes,
            removals=candidate.removals,
            expected_preimages=candidate.expected_preimages,
        )
    manifest = load_generated_manifest(paths.generated_dir)
    config = runtime_config_from_manifest(manifest)
    payload = yaml.safe_load(paths.config_path.read_text())
    retry_plan = cli._plan_helm_chart_upgrade(
        payload=payload, target=target, target_version="1.1.0"
    )
    pulls = len(application_project.pulls)
    with prepare_chart_upgrade(
        cli, payload, paths.config_path.read_bytes(), config, paths, manifest, retry_plan
    ) as retry:
        assert retry.writes == {}
        assert retry.transition["source_sha256"] != retry.transition["target_sha256"]
    assert len(application_project.pulls) == pulls

    def retarget(*args, **kwargs):
        result = observed(*args, **kwargs)
        result["cluster0"]["kubernetes_uid"] = "another-cluster"
        return result

    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", retarget)
    with (
        pytest.raises(ValueError, match="immutable cluster identity"),
        prepare_chart_upgrade(
            cli, payload, paths.config_path.read_bytes(), config, paths, manifest, retry_plan
        ),
    ):
        pytest.fail("Retargeted retry admitted")


def test_execution_rejects_generation_replacement_after_publication(application_project):
    _, _, paths, manifest = application_project()
    from nebius_cxcli.deployment_state import (
        DeploymentGeneration,
        admitted_application_generation,
        assert_admitted_application_generation,
    )

    generation = DeploymentGeneration.capture(paths, manifest)
    with admitted_application_generation(paths, generation):
        assert_admitted_application_generation(paths, manifest)
        file = flux_target_dir(paths, "cluster0") / "helmrelease-cert-manager.yaml"
        file.write_text("changed\n")
        with pytest.raises(RuntimeError, match="changed before execution"):
            assert_admitted_application_generation(paths, manifest)


def accepted_soperator_project(application_project):
    """A locally accepted checkpoint with real chart admission and protected bytes."""
    from nebius_cxcli import ordinary_apps
    from nebius_cxcli.compatibility_adapters import receipt
    from nebius_cxcli.compatibility_matrix import assess, digest, load_matrix
    from nebius_cxcli.compatibility_runtime import selected_inventory

    payload, _, paths, manifest = application_project()
    target_dir = flux_target_dir(paths, "cluster0")
    ordinary = target_dir / "ordinary"
    ordinary.mkdir()
    for path in list(target_dir.glob("*.yaml")):
        path.rename(ordinary / path.name)
    (target_dir / "kustomization.yaml").write_text("resources: [protected.yaml]\n")
    (target_dir / "protected.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "protected", "namespace": "soperator"},
                "data": {"fixture": "accepted"},
            }
        )
    )
    payload["apps"]["charts"].append(
        {
            "id": "soperator",
            "instance_id": "cluster0",
            "enabled": True,
            "version": "4.1.7",
            "values": {},
        }
    )
    paths.config_path.write_text(yaml.safe_dump(payload))
    dashboard = paths.generated_dir / "grafana_dashboards" / "prior.json"
    dashboard.parent.mkdir()
    dashboard.write_text('{"title":"previous rendered dashboard"}')
    inventory = selected_inventory(payload)
    protected_subjects = [row for row in inventory if row["owner"] == "soperator"]
    roots = [row for row in inventory if row["kind"] == "helm" and row["owner"] != "soperator"]
    fresh = freeze_compatibility(payload, paths, inventory=roots)
    # This fixture declares an already accepted protected checkpoint; it does not
    # simulate Soperator preparation or claim live release evidence.
    protected_receipts = [
        receipt(
            row,
            adapter,
            {"fixture": "accepted-protected-checkpoint"},
            reason="Explicit accepted-checkpoint test fixture",
        )
        for row in protected_subjects
        for adapter in row["required_adapters"]
    ]
    enhanced = {(row["component_id"], row["instance_id"]): row for row in fresh["inventory"]}
    frozen = {
        **fresh,
        "inventory": [
            enhanced.get((row["component_id"], row["instance_id"]), row) for row in inventory
        ],
        "receipts": fresh["receipts"] + protected_receipts,
    }
    frozen["report"] = assess(
        frozen["inventory"], receipts=frozen["receipts"], matrix=load_matrix()
    )
    frozen.pop("sha256")
    frozen["sha256"] = digest(frozen)
    manifest = build_generated_manifest(
        config=payload,
        paths=paths,
        targets=[
            {**row, "flux_dir": str(flux_target_dir(paths, row["target_ref"]))}
            for row in manifest["deploy"]["targets"]
        ],
        required_component_outputs=[],
        compatibility=frozen,
        application_files=application_files(paths),
    )
    manifest_path = paths.generated_dir / "nebius-cxcli-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    ordinary_apps.accept_ordinary_app_baseline(
        paths,
        identities={"cluster0": {"cluster_id": "cluster-id-0", "kubernetes_uid": "uid-cluster0"}},
    )
    return payload, paths, manifest, frozen, protected_subjects, protected_receipts


def test_ordinary_upgrade_publishes_fresh_evidence_and_preserves_protected_generation(
    application_project, monkeypatch
):
    from nebius_cxcli import ordinary_apps
    from nebius_cxcli.compatibility_matrix import digest
    from nebius_cxcli.generated_manifest import load_generated_manifest
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    payload, paths, manifest, frozen, protected_subjects, protected_receipts = (
        accepted_soperator_project(application_project)
    )
    manifest_path = paths.generated_dir / "nebius-cxcli-manifest.json"
    protected = {p: p.read_bytes() for p in ordinary_apps._protected_files(paths)}
    baseline_path = paths.reports_dir / ordinary_apps.BASELINE_FILENAME
    baseline = baseline_path.read_bytes()
    config = runtime_config_from_manifest(manifest)
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    plan = cli._plan_helm_chart_upgrade(
        payload=payload,
        target=cli._parse_helm_chart_upgrade_selector("apps:cert-manager@cluster0"),
        target_version="1.1.0",
    )
    with prepare_chart_upgrade(
        cli, payload, paths.config_path.read_bytes(), config, paths, manifest, plan
    ) as candidate:
        ProjectBundleTransaction(paths.project_dir).commit(
            candidate.writes,
            removals=candidate.removals,
            expected_preimages=candidate.expected_preimages,
        )
    published = load_generated_manifest(paths.generated_dir)
    ordinary_apps.validate_ordinary_bundle(paths, published)
    admit_applications(
        runtime_config_from_manifest(published),
        paths,
        published,
        target_refs=["cluster0"],
        ordinary=True,
    )
    assert {p: p.read_bytes() for p in protected} == protected
    assert baseline_path.read_bytes() == baseline
    assert published["render"]["compatibility"]["sha256"] != frozen["sha256"]
    assert [
        r
        for r in published["render"]["compatibility"]["receipts"]
        if r["subject_sha256"] in {digest(s) for s in protected_subjects}
    ] == protected_receipts
    assert published["render"]["application_files"] == application_files(paths)
    assert (
        next(
            row
            for row in published["runtime_config"]["apps"]["charts"]
            if row["id"] == "cert-manager"
        )["version"]
        == "1.1.0"
    )

    # A later protected render carries the accepted ordinary app and evidence.
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from nebius_cxcli.render import reset_generated_bundle, staged_generated_paths

    stage = staged_generated_paths(paths)
    stage.generated_dir.rmdir()
    try:
        staged_manifest = DeploymentGeneration.capture(paths, published).materialize(stage)
        next(
            row
            for row in staged_manifest["runtime_config"]["apps"]["charts"]
            if row["id"] == "cert-manager"
        )["version"] = "1.0.0"
        (stage.generated_dir / manifest_path.name).write_text(json.dumps(staged_manifest))
        ordinary_apps.preserve_ordinary_app_generation(paths, stage)
        retained = load_generated_manifest(stage.generated_dir)
        admit_applications(
            runtime_config_from_manifest(retained),
            stage,
            retained,
            target_refs=["cluster0"],
            ordinary=True,
        )
        assert "chart_upgrade" not in retained["render"]
    finally:
        reset_generated_bundle(stage)


@pytest.mark.parametrize(
    "repository", ["oci://registry.test/cert-manager", "https://charts.example.test"]
)
@pytest.mark.parametrize("race", [None, "files", "identity", "readiness_identity"])
def test_public_upgrade_publishes_then_executes_only_admitted_candidate(
    application_project, monkeypatch, race, capsys, repository
):
    from nebius_cxcli.generated_manifest import load_generated_manifest

    _, _, paths, _ = application_project(repos=(repository,))
    events = []
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _: cli._load_manifest_backed_context(paths)
    )
    original_apply = cli.flux_apply_command

    def execute(*args, **kwargs):
        events.append("published")
        assert (
            yaml.safe_load(paths.config_path.read_text())["apps"]["charts"][0]["version"] == "1.1.0"
        )
        if race == "files":
            (flux_target_dir(paths, "cluster0") / "helmrelease-cert-manager.yaml").write_text(
                "changed\n"
            )
        return original_apply(*args, **kwargs)

    def observe_apply(*args, **kwargs):
        kwargs.pop("stack")
        kwargs.pop("kube_envs")["cluster0"] = {"KUBECONFIG": "private-test-context"}
        result = observed(*args, **kwargs)
        if race == "identity" or (race == "readiness_identity" and "apply" in events):
            result["cluster0"]["kubernetes_uid"] = "another-cluster"
        return result

    def apply_effects(_cli, config, private_paths, manifest, **kwargs):
        events.append("apply")
        assert private_paths.generated_dir != paths.generated_dir
        assert manifest["runtime_config"]["apps"]["charts"][0]["version"] == "1.1.0"
        assert manifest["render"]["application_files"] == application_files(private_paths)

    monkeypatch.setattr(cli, "flux_apply_command", execute)
    monkeypatch.setattr(
        "nebius_cxcli.application_execution.observe_application_targets", observe_apply
    )
    monkeypatch.setattr(
        cli, "_ensure_runtime_auth_material", lambda *a, **kw: events.append("auth")
    )
    monkeypatch.setattr(
        "nebius_cxcli.application_execution.execute_admitted_flux_apply", apply_effects
    )

    def verify_ready(**kwargs):
        from types import SimpleNamespace

        assert kwargs["expected_version"] == "1.1.0"
        events.append("ready")
        return SimpleNamespace(summary=lambda: "test workload ready")

    monkeypatch.setattr(cli, "verify_helm_chart_ready", verify_ready)
    if race:
        with pytest.raises(cli.typer.Exit):
            cli.upgrade_helm_chart_command(
                paths.config_path, "apps:cert-manager@cluster0", to_version="1.1.0"
            )
        assert events == (
            ["published", "auth", "apply"] if race == "readiness_identity" else ["published"]
        )
    else:
        cli.upgrade_helm_chart_command(
            paths.config_path, "apps:cert-manager@cluster0", to_version="1.1.0"
        )
        assert events == ["published", "auth", "apply", "ready"]
        admission = json.loads((paths.reports_dir / "application-admission.json").read_text())
        assert admission["admitted"] is True
        assert admission["rows"]
        assert admission["observed_compatibility"]
        output = capsys.readouterr()
        assert "Component compatibility" not in output.out + output.err
        assert "Operator transition:" not in output.out + output.err
        assert (
            load_generated_manifest(paths.generated_dir)["render"]["chart_upgrade"][
                "target_version"
            ]
            == "1.1.0"
        )


def test_new_application_generation_drops_previous_upgrade_intent(application_project):
    from nebius_cxcli.application_compatibility import (
        application_inputs,
        refresh_application_manifest,
    )

    _, config, paths, manifest = application_project()
    manifest["render"]["chart_upgrade"] = {"stale": True}
    with application_inputs(config, manifest):
        refreshed = refresh_application_manifest(config, paths, manifest)
    assert "chart_upgrade" not in refreshed["render"]


def test_private_generation_captures_non_yaml_kustomize_inputs(application_project):
    _, config, paths, manifest = application_project()
    values = flux_target_dir(paths, "cluster0") / "values.env"
    values.write_text("FEATURE=enabled\n")
    manifest["render"]["application_files"] = application_files(paths)
    with captured_application_execution(config, paths, manifest, target_refs=["cluster0"]) as (
        stage,
        _,
        _,
    ):
        assert (
            flux_target_dir(stage, "cluster0") / values.name
        ).read_bytes() == values.read_bytes()


def test_observation_rejects_ambient_context_without_a_target_handoff(
    application_project, monkeypatch, tmp_path
):
    from nebius_cxcli.application_execution import observe_application_targets

    _, config, paths, manifest = application_project()
    context = tmp_path / "operator-kubeconfig"
    context.write_text(yaml.safe_dump({"current-context": "unrelated"}))
    monkeypatch.setenv("KUBECONFIG", str(context))
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *a, **kw: None)

    def forbidden(*args, **kwargs):
        pytest.fail("No Kubernetes observation may run without a target handoff")

    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", forbidden)
    monkeypatch.setattr("nebius_cxcli.application_execution.SubprocessHelmCommandRunner", forbidden)
    with pytest.raises(ValueError, match="explicit cluster handoff"):
        observe_application_targets(cli, config, paths, manifest, [])


@pytest.mark.parametrize("drift", [None, "source", "terraform", "tfvars", "flux", "baseline"])
def test_public_soperator_app_apply_has_no_infrastructure_or_maintenance_path(
    application_project, monkeypatch, drift, capsys
):
    from types import SimpleNamespace

    from nebius_cxcli import ordinary_apps
    from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState
    from nebius_cxcli.generated_manifest import load_generated_manifest
    from nebius_cxcli.terraform_backend import backend_settings_from_config
    from test_deployment_state import Store

    real_loader = cli._load_generated_flux_context
    payload, paths, manifest, _, _, _ = accepted_soperator_project(application_project)
    monkeypatch.setattr(cli, "_load_generated_flux_context", real_loader)
    (paths.infra_dir / "main.tf").write_text(
        "# accepted infrastructure; remote drift is outside this app operation\n"
    )
    (paths.infra_dir / "terraform.tfvars.json").write_text('{"accepted": true}\n')
    generation = DeploymentGeneration.capture(paths, manifest)
    identity = {"cluster0": {"cluster_id": "cluster-id-0", "kubernetes_uid": "uid-cluster0"}}
    assert ordinary_apps.accept_ordinary_app_baseline(
        paths,
        identities=identity,
        deployment_generation=generation.identity,
        expected_generation=generation,
    )
    config = runtime_config_from_manifest(manifest)
    store = Store()
    state = DeploymentState(store, backend_settings_from_config(config), assert_held=lambda: None)
    state.register(
        generation,
        evidence={
            "identities": identity,
            "targets": {"cluster0": {"identity": identity["cluster0"]}},
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: store
    )
    # Publish a real ordinary-only candidate using the existing chart transaction.
    monkeypatch.setattr("nebius_cxcli.application_upgrade.observe_application_targets", observed)
    plan = cli._plan_helm_chart_upgrade(
        payload=payload,
        target=cli._parse_helm_chart_upgrade_selector("apps:cert-manager@cluster0"),
        target_version="1.1.0",
    )
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    with prepare_chart_upgrade(
        cli, payload, paths.config_path.read_bytes(), config, paths, manifest, plan
    ) as candidate:
        ProjectBundleTransaction(paths.project_dir).commit(
            candidate.writes,
            removals=candidate.removals,
            expected_preimages=candidate.expected_preimages,
        )
    manifest = load_generated_manifest(paths.generated_dir)
    if drift == "source":
        changed = yaml.safe_load(paths.config_path.read_text())
        changed["infra"]["components"][0]["inputs"]["node_count"] = 99
        paths.config_path.write_text(yaml.safe_dump(changed))
    elif drift == "terraform":
        (paths.infra_dir / "main.tf").write_text("changed\n")
    elif drift == "tfvars":
        (paths.infra_dir / "terraform.tfvars.json").write_text("{}\n")
    elif drift == "flux":
        (flux_target_dir(paths, "cluster0") / "protected.yaml").write_text("changed\n")
    elif drift == "baseline":
        (paths.reports_dir / ordinary_apps.BASELINE_FILENAME).unlink()
    before = {
        p: p.read_bytes()
        for p in paths.project_dir.rglob("*")
        if p.is_file() and ".lock" not in p.name
    }
    events = []

    def forbidden(*a, **kw):
        pytest.fail("Ordinary apply touched infrastructure or Slurm maintenance")

    for name in (
        "_materialize_generated_terraform_tfvars",
        "_ensure_terraform_backend_ready",
        "terraform_plan",
        "terraform_apply",
        "_apply_rendered_flux_with_soperator_job_policy",
    ):
        if hasattr(cli, name):
            monkeypatch.setattr(cli, name, forbidden)

    @contextmanager
    def lease(**kw):
        assert kw["bootstrap_backend"] is False
        events.append("project-lease")
        yield SimpleNamespace(assert_held=lambda: events.append("authority"))

    monkeypatch.setattr(cli, "_deployment_execution", lease)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "accepted-context",
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-id-0",
        },
    )
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid-cluster0")
    monkeypatch.setattr(
        "nebius_cxcli.application_execution.SubprocessHelmCommandRunner",
        lambda **kw: (
            lambda argv: SimpleNamespace(
                stdout=json.dumps({"serverVersion": {"gitVersion": "1.35.5"}})
            )
        ),
    )

    # Ordinary execution itself is covered with real ownership/resource checks in
    # test_ordinary_apps; this boundary proves public admission and dispatch.
    def apply(cfg, p, m, **kw):
        kw["assert_project_authority"]()
        ordinary_apps.validate_ordinary_bundle(p, m)
        events.append("ordinary-apply")

    monkeypatch.setattr(cli, "_apply_ordinary_app_command", apply)
    if drift:
        with pytest.raises(cli.typer.Exit) as error:
            cli.flux_apply_command(paths.generated_dir, target_ref="cluster0")
        assert error.value.exit_code == 1
        assert not events
    else:
        cli.flux_apply_command(paths.generated_dir, target_ref="cluster0")
        assert events.count("ordinary-apply") == 1
        assert "project-lease" in events
    assert {p: p.read_bytes() for p in before} == before
