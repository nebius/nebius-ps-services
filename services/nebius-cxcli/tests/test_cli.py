from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nebius_cxcli.cli import _load_context, app
from nebius_cxcli.component_sources import (
    reset_component_sources_cache,
    set_component_sources_file_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    set_component_sources_file_override(None)
    reset_component_sources_cache()
    reset_component_entry_cache()


def _git_init(repo_root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def _instance_config_path(deployments_root: Path) -> Path:
    return (
        deployments_root
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "cluster-a"
        / "config.yaml"
    )


def _create_non_interactive(deployments_root: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--env",
            "prod",
            "--cluster-name",
            "cluster-a",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
            *extra,
        ],
    )


def _infra_enabled_map(payload: dict) -> dict[str, bool]:
    rows = payload.get("infra", {}).get("components", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, bool] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        result[component_id] = bool(item.get("enabled", False))
    return result


def _apps_enabled_map(payload: dict) -> dict[str, bool]:
    rows = payload.get("apps", {}).get("releases", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, bool] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        release_id = str(item.get("id", "")).strip().lower()
        if not release_id:
            continue
        result[release_id] = bool(item.get("enabled", False))
    return result


def test_create_target_path_help_mentions_any_existing_directory() -> None:
    result = runner.invoke(app, ["create", "--help"])
    assert result.exit_code == 0
    assert "Deployments root folder path." in result.output


def test_create_existing_instance_is_idempotent_without_force(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    second = _create_non_interactive(deployments_root)
    assert second.exit_code == 0, second.output
    assert "Config up-to-date:" in second.output


def test_create_force_overwrites_existing_config(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["client_info"]["cluster_name"] = "mutated"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    forced = _create_non_interactive(deployments_root, "--force")
    assert forced.exit_code == 0, forced.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert refreshed["client_info"]["cluster_name"] == "cluster-a"


def test_create_uses_defaults_when_no_component_flags(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_instance_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled["mk8s"] is True
    assert infra_enabled["object-storage"] is True
    assert apps_enabled["n8n"] is False


def test_create_component_flags_override_defaults(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "n8n",
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_instance_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled["mk8s"] is False
    assert infra_enabled["object-storage"] is False
    assert apps_enabled["n8n"] is True


def test_create_non_interactive_no_subnet_option(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--env",
            "prod",
            "--cluster-name",
            "cluster-a",
            "--project-id",
            "project-456",
        ],
    )
    assert result.exit_code == 0, result.output


def test_load_context_uses_embedded_component_sources_snapshot(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["component_sources"] = {
        "infra": {"tf_modules": []},
        "apps": {
            "helm_charts": [
                {
                    "name": "nginx",
                    "repo": "https://charts.bitnami.com/bitnami",
                    "version": "",
                    "namespace": "default",
                    "releasename": "embedded-app",
                    "group": "Workloads",
                    "enable": False,
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    external_sources = tmp_path / "external-component-sources.yaml"
    external_sources.write_text(
        yaml.safe_dump(
            {
                "infra": {"tf_modules": []},
                "apps": {
                    "helm_charts": [
                        {
                            "name": "nginx",
                            "repo": "https://charts.bitnami.com/bitnami",
                            "version": "",
                            "namespace": "default",
                            "releasename": "external-app",
                            "group": "Workloads",
                            "enable": False,
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    set_component_sources_file_override(external_sources)
    reset_component_sources_cache()
    reset_component_entry_cache()

    _config, _paths = _load_context(config_path)
    app_ids = {entry.id for entry in component_entries("apps")}
    assert "embedded-app" in app_ids
    assert "external-app" not in app_ids


def test_load_context_requires_embedded_component_sources(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload.pop("component_sources", None)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(RuntimeError, match="must include 'component_sources'"):
        _load_context(config_path)


def test_create_embeds_only_enabled_component_sources(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    sources = payload.get("component_sources", {})
    assert isinstance(sources, dict)
    infra_sources = (
        sources.get("infra", {}).get("tf_modules", [])
        if isinstance(sources.get("infra", {}), dict)
        else []
    )
    apps_sources = (
        sources.get("apps", {}).get("helm_charts", [])
        if isinstance(sources.get("apps", {}), dict)
        else []
    )
    infra_modules = {
        str(item.get("module", "")).strip()
        for item in infra_sources
        if isinstance(item, dict)
    }
    assert infra_modules == {"mk8s"}
    assert apps_sources == []


def test_create_app_namespace_and_releasename_overrides(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(
        deployments_root,
        "--app",
        "n8n",
        "--app-namespace",
        "n8n=automation",
        "--app-releasename",
        "n8n=workflow-core",
    )
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    releases = payload.get("apps", {}).get("releases", [])
    n8n_values = None
    for row in releases:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip().lower() == "n8n":
            n8n_values = row.get("values", {})
            break
    assert isinstance(n8n_values, dict)
    assert n8n_values.get("namespace") == "automation"
    assert n8n_values.get("release_name") == "workflow-core"


def test_create_updates_existing_instance_component_selection(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "n8n",
    )
    assert first.exit_code == 0, first.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["infra"]["ssh_user_name"] = "customuser"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    second = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "n8n",
    )
    assert second.exit_code == 0, second.output
    assert "Updated:" in second.output or "Config up-to-date:" in second.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(refreshed)
    apps_enabled = _apps_enabled_map(refreshed)
    assert refreshed["infra"]["ssh_user_name"] == "customuser"
    assert infra_enabled["mk8s"] is True
    assert infra_enabled["object-storage"] is False
    assert apps_enabled["n8n"] is True
    sources = refreshed.get("component_sources", {})
    assert isinstance(sources, dict)
    apps_sources = (
        sources.get("apps", {}).get("helm_charts", [])
        if isinstance(sources.get("apps", {}), dict)
        else []
    )
    assert any(isinstance(item, dict) and item.get("releasename") == "n8n" for item in apps_sources)


def test_bootstrap_ci_no_auth_writes_workflow_in_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    config_path = _instance_config_path(deployments_root)
    bootstrap = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert bootstrap.exit_code == 0, bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    assert workflow.exists()

    content = workflow.read_text(encoding="utf-8")
    assert "NEBIUS_DISCOVER_TARGET: customer/deployments-root" in content
    assert "customer/deployments-root" in content


def test_bootstrap_ci_no_auth_is_idempotent_without_force(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    config_path = _instance_config_path(deployments_root)
    first = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert first.exit_code == 0, first.output
    assert "Created:" in first.output

    second = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert second.exit_code == 0, second.output
    assert "Workflow exists, keeping current file:" in second.output
    assert "Skipped CI auth bootstrap/secrets sync." in second.output


def test_bootstrap_ci_rejects_github_flags_when_no_auth(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    config_path = _instance_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "bootstrap-ci",
            str(config_path),
            "--no-auth-bootstrap",
            "--github-repo",
            "owner/repo",
        ],
    )
    assert result.exit_code == 1
    assert "--github-repo and --github-token-env are valid only when --auth-bootstrap" in result.output


def test_auth_bootstrap_rejects_github_flags_when_no_github_sync() -> None:
    result = runner.invoke(
        app,
        [
            "auth",
            "bootstrap",
            "--project-id",
            "project-456",
            "--no-github-sync",
            "--github-repo",
            "owner/repo",
        ],
    )
    assert result.exit_code == 1
    assert "--github-repo and --github-token-env are valid only when --github-sync" in result.output


def test_discover_accepts_non_git_directory(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    config_path = (
        deployments_root
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "cluster-a"
        / "config.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("version: v1\n", encoding="utf-8")

    result = runner.invoke(app, ["discover", str(deployments_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": "deployments/instances/client-a--tenant-123/prod/cluster-a/config.yaml",
            }
        ]
    }


def test_discover_in_git_repo_outputs_repo_relative_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    config_path = (
        deployments_root
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "cluster-a"
        / "config.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("version: v1\n", encoding="utf-8")

    result = runner.invoke(app, ["discover", str(deployments_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": "deployments/instances/client-a--tenant-123/prod/cluster-a/config.yaml",
            }
        ]
    }
