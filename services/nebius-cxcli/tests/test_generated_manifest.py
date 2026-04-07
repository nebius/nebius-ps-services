from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _project_paths(tmp_path: Path) -> ProjectPaths:
    repo_root = tmp_path / "repo"
    generated_dir = (
        repo_root / "deployments" / "projects" / "client-a--tenant-123" / "project-456" / "generated"
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
        inventory_dir=generated_dir / "inventory",
        path_client_name="client-a",
        path_tenant_id="tenant-123",
        path_project_id="project-456",
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


def test_build_generated_manifest_uses_repo_relative_paths(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)

    manifest = build_generated_manifest(
        config=_runtime_payload(),
        paths=paths,
        handoffs=[{"component_id": "mk8s", "access": "external"}],
        required_component_outputs=[{"component_id": "mk8s", "output_name": "cluster_id"}],
        status_watchers=[
            {
                "component_id": "mk8s",
                "kind": "nebius.mk8s.cluster",
                "parent_id": "project-456",
                "resource_name": "clust1",
            }
        ],
        source_profile="portable",
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version="v2.8.0",
        terraform_version="1.14.1",
    )

    assert manifest["schema"] == GENERATED_MANIFEST_SCHEMA
    assert manifest["source_contract"]["config_path"] == (
        "deployments/projects/client-a--tenant-123/project-456/config.yaml"
    )
    assert manifest["paths"] == {
        "generated_dir": "deployments/projects/client-a--tenant-123/project-456/generated",
        "infra_dir": "deployments/projects/client-a--tenant-123/project-456/generated/infra",
        "flux_dir": "deployments/projects/client-a--tenant-123/project-456/generated/flux",
        "inventory_dir": "deployments/projects/client-a--tenant-123/project-456/generated/inventory",
    }
    assert manifest["tools"] == {
        "flux_version": "v2.8.0",
        "terraform_version": "1.14.1",
    }
    assert manifest["deploy"]["handoffs"] == [{"component_id": "mk8s", "access": "external"}]
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
    assert manifest["render"]["source_profile"] == "portable"
    assert "portable" not in manifest["render"]
    assert manifest["render"]["terraform_tfvars"] == {"mk8s_cluster_name": "clust1"}


def test_write_load_and_runtime_config_round_trip(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)

    written_path = write_generated_manifest(
        config=_runtime_payload(),
        paths=paths,
        handoffs=[{"component_id": "mk8s"}],
        required_component_outputs=[{"component_id": "mk8s", "output_name": "cluster_id"}],
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version="v2.8.0",
        terraform_version="1.14.1",
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
        "flux_version": "v2.8.0",
        "terraform_version": "1.14.1",
    }


def test_write_generated_manifest_to_path_uses_explicit_output_path(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)
    explicit_path = tmp_path / "staging" / GENERATED_MANIFEST_FILENAME

    written_path = write_generated_manifest_to_path(
        explicit_path,
        config=_runtime_payload(),
        paths=paths,
        handoffs=[{"component_id": "mk8s"}],
        required_component_outputs=[{"component_id": "mk8s", "output_name": "cluster_id"}],
        terraform_tfvars={"mk8s_cluster_name": "clust1"},
        flux_version="v2.8.0",
        terraform_version="1.14.1",
    )

    assert written_path == explicit_path
    assert json.loads(explicit_path.read_text(encoding="utf-8"))["paths"]["generated_dir"] == (
        "deployments/projects/client-a--tenant-123/project-456/generated"
    )


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
