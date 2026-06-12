from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.deploy_targets import flux_target_dir
from nebius_cxcli.generated_manifest import (
    GENERATED_MANIFEST_FILENAME,
    GENERATED_MANIFEST_SCHEMA,
    build_generated_manifest,
    load_generated_manifest,
    manifest_path_for_generated_dir,
    runtime_config_from_manifest,
    terraform_tfvars_from_manifest,
    write_generated_manifest,
    write_generated_manifest_to_path,
)
from nebius_cxcli.paths import ProjectPaths


def _bundled_tool_versions() -> tuple[str, str]:
    settings_path = Path(__file__).resolve().parents[1] / "component_cli_settings.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert isinstance(settings, dict)
    cli = settings["cli"]
    return cli["flux"]["version"], cli["terraform"]["version"]


def _project_paths(tmp_path: Path) -> ProjectPaths:
    repo_root = tmp_path / "repo"
    generated_dir = (
        repo_root / "deployments" / "tenant-name-example" / "project-name-example" / "generated"
    )
    project_dir = generated_dir.parent
    return ProjectPaths(
        config_path=project_dir / "config.yaml",
        repo_root=repo_root,
        deployments_dir=repo_root / "deployments",
        project_dir=project_dir,
        generated_dir=generated_dir,
        infra_dir=generated_dir / "infra",
        flux_dir=generated_dir / "flux",
        reports_dir=generated_dir / "reports",
        path_tenant_folder="tenant-name-example",
        path_project_folder="project-name-example",
    )


def _runtime_payload() -> dict:
    return {
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
            },
            "notifications": {"email": "ops@example.com"},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {"cluster_name": "clust1"},
                }
            ]
        },
        "apps": {"charts": []},
    }


def _mk8s_target(paths: ProjectPaths, *, target_ref: str = "mk8s") -> dict[str, str]:
    return {
        "component_id": "mk8s",
        "instance_id": target_ref,
        "target_ref": target_ref,
        "cluster_id_output_name": f"{target_ref.replace('-', '_')}_cluster_id",
        "component_output_ref": f"{target_ref}.cluster_id",
        "access": "external",
        "flux_dir": str(flux_target_dir(paths, target_ref)),
    }


def test_build_generated_manifest_uses_repo_relative_paths(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)
    flux_version, terraform_version = _bundled_tool_versions()
    validations = [
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU Visibility test",
            "namespace": "gpu-validation",
        }
    ]

    manifest = build_generated_manifest(
        config=_runtime_payload(),
        paths=paths,
        targets=[_mk8s_target(paths)],
        required_component_outputs=[{"component_id": "mk8s", "output_name": "cluster_id"}],
        status_watchers=[
            {
                "component_id": "mk8s",
                "kind": "nebius.mk8s.cluster",
                "parent_id": "project-456",
                "resource_name": "clust1",
            }
        ],
        validations=validations,
        source_profile="portable",
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version=flux_version,
        terraform_version=terraform_version,
    )

    assert manifest["schema"] == GENERATED_MANIFEST_SCHEMA
    assert manifest["source_contract"]["config_path"] == (
        "deployments/tenant-name-example/project-name-example/config.yaml"
    )
    assert manifest["project"] == {
        "client_name": "client-a",
        "tenant_id": "tenant-123",
        "project_id": "project-456",
    }
    assert manifest["paths"] == {
        "generated_dir": "deployments/tenant-name-example/project-name-example/generated",
        "infra_dir": "deployments/tenant-name-example/project-name-example/generated/infra",
        "flux_dir": "deployments/tenant-name-example/project-name-example/generated/flux",
        "reports_dir": "deployments/tenant-name-example/project-name-example/generated/reports",
    }
    assert manifest["tools"] == {
        "flux_version": flux_version,
        "terraform_version": terraform_version,
    }
    assert manifest["deploy"]["targets"] == [
        {
            "component_id": "mk8s",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "cluster_id_output_name": "mk8s_cluster_id",
            "component_output_ref": "mk8s.cluster_id",
            "access": "external",
            "flux_dir": "deployments/tenant-name-example/project-name-example/generated/flux/targets/mk8s",
        }
    ]
    assert manifest["deploy"]["required_component_outputs"] == [
        {"component_id": "mk8s", "output_name": "cluster_id"}
    ]
    assert manifest["deploy"]["status_watchers"] == [
        {
            "component_id": "mk8s",
            "kind": "nebius.mk8s.cluster",
            "parent_id": "project-456",
            "resource_name": "clust1",
        }
    ]
    assert manifest["deploy"]["validations"] == validations
    assert manifest["render"]["source_profile"] == "portable"
    assert "portable" not in manifest["render"]
    assert manifest["render"]["terraform_tfvars"] == {"mk8s_cluster_name": "clust1"}
    assert manifest["quota"] == {}


def test_build_generated_manifest_rejects_target_ref_that_differs_from_instance_id(
    tmp_path: Path,
) -> None:
    paths = _project_paths(tmp_path)
    target = _mk8s_target(paths, target_ref="cluster1")
    target["target_ref"] = "cluster2"

    with pytest.raises(
        ValueError,
        match=(
            r"Generated manifest deploy\.targets\[0\]\.target_ref "
            r"must equal instance_id 'cluster1'"
        ),
    ):
        build_generated_manifest(
            config=_runtime_payload(),
            paths=paths,
            targets=[target],
            required_component_outputs=[],
        )


def test_build_generated_manifest_rejects_target_missing_target_ref(
    tmp_path: Path,
) -> None:
    paths = _project_paths(tmp_path)
    target = _mk8s_target(paths, target_ref="cluster1")
    target.pop("target_ref")

    with pytest.raises(
        ValueError,
        match=r"Generated manifest deploy\.targets\[0\]\.target_ref is required",
    ):
        build_generated_manifest(
            config=_runtime_payload(),
            paths=paths,
            targets=[target],
            required_component_outputs=[],
        )


def test_write_load_and_runtime_config_round_trip(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)
    flux_version, terraform_version = _bundled_tool_versions()

    written_path = write_generated_manifest(
        config=_runtime_payload(),
        paths=paths,
        targets=[_mk8s_target(paths)],
        required_component_outputs=[{"component_id": "mk8s", "output_name": "cluster_id"}],
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version=flux_version,
        terraform_version=terraform_version,
    )

    assert written_path == manifest_path_for_generated_dir(paths.generated_dir)
    assert written_path.name == GENERATED_MANIFEST_FILENAME

    loaded = load_generated_manifest(paths.generated_dir)
    runtime_config = runtime_config_from_manifest(loaded)
    tfvars = terraform_tfvars_from_manifest(loaded)

    assert runtime_config.client_info.client_name == "client-a"
    assert runtime_config.client_info.nebius.project_id == "project-456"
    assert runtime_config.infra.components[0].inputs.cluster_name == "clust1"
    assert tfvars == {"mk8s_cluster_name": "clust1"}
    assert loaded["tools"] == {
        "flux_version": flux_version,
        "terraform_version": terraform_version,
    }


def test_write_generated_manifest_to_path_uses_explicit_output_path(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)
    explicit_path = tmp_path / "staging" / GENERATED_MANIFEST_FILENAME
    flux_version, terraform_version = _bundled_tool_versions()

    written_path = write_generated_manifest_to_path(
        explicit_path,
        config=_runtime_payload(),
        paths=paths,
        targets=[_mk8s_target(paths)],
        required_component_outputs=[{"component_id": "mk8s", "output_name": "cluster_id"}],
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version=flux_version,
        terraform_version=terraform_version,
    )

    assert written_path == explicit_path
    assert json.loads(explicit_path.read_text(encoding="utf-8"))["paths"]["generated_dir"] == (
        "deployments/tenant-name-example/project-name-example/generated"
    )


def test_build_generated_manifest_includes_quota_report(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)
    flux_version, terraform_version = _bundled_tool_versions()

    manifest = build_generated_manifest(
        config=_runtime_payload(),
        paths=paths,
        targets=[],
        required_component_outputs=[],
        quota_report={
            "tenant_id": "tenant-123",
            "project_id": "project-456",
            "confirmed_insufficient": True,
            "checks": [
                {
                    "component_id": "ssh-jumphost",
                    "instance_id": "ssh-jumphost",
                    "component_label": "ssh-jumphost",
                    "quota_name": "compute.instance.count",
                    "region": "eu-north1",
                    "required": 1,
                    "reason": "one VM",
                    "unit": "",
                    "available": 0,
                    "sufficient": False,
                    "tenant_limit": 0,
                    "tenant_usage": 0,
                    "project_limit": None,
                    "project_usage": 0,
                    "source_scope": "tenant",
                    "description": "VM count",
                    "contributors": [],
                }
            ],
            "coverage_gaps": [],
            "errors": [],
        },
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version=flux_version,
        terraform_version=terraform_version,
    )

    assert manifest["quota"]["confirmed_insufficient"] is True
    assert manifest["quota"]["checks"][0]["quota_name"] == "compute.instance.count"


def test_load_generated_manifest_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Generated manifest not found"):
        load_generated_manifest(tmp_path / "generated")


def test_load_generated_manifest_rejects_unsupported_schema(tmp_path: Path) -> None:
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = generated_dir / GENERATED_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps({"schema": "wrong/v1"}), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported generated manifest schema"):
        load_generated_manifest(generated_dir)


def test_runtime_config_from_manifest_requires_runtime_config() -> None:
    with pytest.raises(ValueError, match="missing runtime_config"):
        runtime_config_from_manifest({"schema": GENERATED_MANIFEST_SCHEMA})


def test_terraform_tfvars_from_manifest_requires_render_tfvars() -> None:
    with pytest.raises(ValueError, match="missing render\\.terraform_tfvars"):
        terraform_tfvars_from_manifest({"schema": GENERATED_MANIFEST_SCHEMA, "render": {}})
