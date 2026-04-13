from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from nebius_cxcli.discover_ops import discover_configs


def _git_init_and_commit(repo_root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], check=True, cwd=repo_root, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def _discover_config_payload(
    *,
    client_name: str = "client-a",
    tenant_id: str = "tenant-123",
    project_id: str = "project-456",
) -> str:
    return yaml.safe_dump(
        {
            "client_info": {
                "client_name": client_name,
                "nebius": {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "region_id": "eu-north1",
                },
                "notifications": {
                    "email_enabled": False,
                    "email": None,
                },
            },
            "infra": {"components": []},
            "apps": {"charts": []},
        },
        sort_keys=False,
    )


def test_discover_include_all(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "nebius-deployments" / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    payload = discover_configs(deployments_dir="nebius-deployments", include_all=True)

    assert payload == {
        "include": [
            {
                "config": "nebius-deployments/tenant-123/project-456/config.yaml",
                "generated": "nebius-deployments/tenant-123/project-456/generated",
                "config_changed": False,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }


def test_discover_without_git_falls_back_to_scan_all(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "deployments" / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    payload = discover_configs(
        deployments_dir="deployments",
        include_all=False,
        repo_root=None,
    )

    assert payload == {
        "include": [
            {
                "config": "deployments/tenant-123/project-456/config.yaml",
                "generated": "deployments/tenant-123/project-456/generated",
                "config_changed": False,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }


def test_discover_changed_files_works_on_initial_commit(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    config_path = repo_root / "deployments" / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")
    _git_init_and_commit(repo_root)

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.delenv("GITHUB_EVENT_BEFORE", raising=False)
    payload = discover_configs(
        deployments_dir="deployments",
        include_all=False,
        repo_root=repo_root,
    )

    assert payload == {
        "include": [
            {
                "config": "deployments/tenant-123/project-456/config.yaml",
                "generated": "deployments/tenant-123/project-456/generated",
                "config_changed": True,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }


def test_discover_generated_change_maps_back_to_instance_config(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "repo"
    config_path = repo_root / "deployments" / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")
    _git_init_and_commit(repo_root)

    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(
        "nebius_cxcli.discover_ops._changed_files",
        lambda *, cwd: ["deployments/tenant-123/project-456/generated/flux/helmrelease-demo.yaml"],
    )
    payload = discover_configs(
        deployments_dir="deployments",
        include_all=False,
        repo_root=repo_root,
    )

    assert payload == {
        "include": [
            {
                "config": "deployments/tenant-123/project-456/config.yaml",
                "generated": "deployments/tenant-123/project-456/generated",
                "config_changed": False,
                "generated_changed": True,
                "github_environment": "client-a-project-456",
            }
        ]
    }


def test_discover_generated_scope_tracks_config_change_for_same_instance(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "repo"
    config_path = repo_root / "deployments" / "tenant-123" / "project-456" / "config.yaml"
    generated_dir = config_path.parent / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")
    _git_init_and_commit(repo_root)

    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(
        "nebius_cxcli.discover_ops._changed_files",
        lambda *, cwd: ["deployments/tenant-123/project-456/config.yaml"],
    )
    payload = discover_configs(
        deployments_dir=str(generated_dir),
        include_all=False,
        repo_root=repo_root,
    )

    assert payload == {
        "include": [
            {
                "config": "deployments/tenant-123/project-456/config.yaml",
                "generated": "deployments/tenant-123/project-456/generated",
                "config_changed": True,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }
