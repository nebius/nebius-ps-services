from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli_module
import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.cli import _load_context, app
from nebius_cxcli.component_sources import (
    ComponentOutput,
    reset_component_sources_cache,
    set_component_sources_file_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_tenant_project_ids_or_prompt",
        lambda **kwargs: (kwargs["tenant_id"], kwargs["project_id"]),
    )
    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda _source: (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
                sensitive=False,
            ),
            ComponentOutput(
                name="cluster_ca_certificate",
                kind="terraform_output",
                source_path="cluster_ca_certificate",
                sensitive=True,
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_variable_names", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        "nebius_cxcli.cli._helm_chart_metadata",
        lambda *, chart_name_or_ref, chart_repo, chart_version, cache=None: (
            chart_name_or_ref,
            set(),
            None,
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._with_infra_provider_groups", lambda entries: entries)
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
        / "project-456"
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
            "--project-id",
            "project-456",
            "--no-validate-sources",
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
    rows = payload.get("apps", {}).get("charts", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, bool] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        chart_id = str(item.get("id", "")).strip().lower()
        if not chart_id:
            continue
        result[chart_id] = bool(item.get("enabled", False))
    return result


def test_create_target_path_help_mentions_any_existing_directory() -> None:
    result = runner.invoke(app, ["create", "--help"])
    assert result.exit_code == 0
    assert "Deployments root folder path." in result.output
    assert "--env" not in result.output
    assert "Environment (dev|stage|prod)" not in result.output


def test_create_existing_instance_is_idempotent_without_force(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    second = _create_non_interactive(deployments_root)
    assert second.exit_code == 0, second.output
    assert "Config up-to-date:" in second.output


def test_create_writes_deployments_gitignore_when_target_is_in_git_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    gitignore_path = deployments_root / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert "instances/*/*/generated/" not in content.splitlines()
    assert "instances/*/*/config.yaml" not in content
    assert "instances/*/*/generated/infra/.terraform/" in content
    assert "instances/*/*/generated/infra/terraform.auto.tfvars.json" in content

    second = _create_non_interactive(deployments_root)
    assert second.exit_code == 0, second.output
    second_content = gitignore_path.read_text(encoding="utf-8")
    assert second_content.count("# >>> nebius-cxcli managed ignores >>>") == 1
    assert second_content == content


def test_create_skips_deployments_gitignore_when_target_not_in_git_repo(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    assert not (deployments_root / ".gitignore").exists()


def test_render_recreates_deployments_gitignore_in_git_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    gitignore_path = deployments_root / ".gitignore"
    assert gitignore_path.exists()
    gitignore_path.unlink()
    assert not gitignore_path.exists()

    config_path = _instance_config_path(deployments_root)
    render_result = runner.invoke(app, ["render", "--force", str(config_path)])
    assert render_result.exit_code == 0, render_result.output
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert "instances/*/*/generated/" not in content.splitlines()
    assert "instances/*/*/config.yaml" not in content
    assert "instances/*/*/generated/infra/.terraform/" in content
    assert "instances/*/*/generated/infra/terraform.auto.tfvars.json" in content


def test_create_force_overwrites_existing_config(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["client_info"]["nebius"]["region_id"] = "mutated"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    forced = _create_non_interactive(deployments_root, "--force")
    assert forced.exit_code == 0, forced.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert refreshed["client_info"]["nebius"]["region_id"] == "eu-north1"


def test_create_uses_defaults_when_no_component_flags(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_instance_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled["mk8s"] is True
    assert "object-storage" not in infra_enabled
    assert "n8n" not in apps_enabled


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
    assert infra_enabled == {}
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
                "--project-id",
                "project-456",
                "--no-validate-sources",
            ],
        )
    assert result.exit_code == 0, result.output


def test_create_aborts_cleanly_on_component_selection_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    def _abort_component_resolution(**_kwargs):  # type: ignore[no-untyped-def]
        raise cli_module.typer.Abort()

    monkeypatch.setattr("nebius_cxcli.cli._resolve_component_ids", _abort_component_resolution)

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
                "--project-id",
                "project-456",
                "--no-validate-sources",
            ],
        )

    assert result.exit_code == 130
    assert "Cancelled by user" in result.output


def test_load_context_uses_component_sources_override_file(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)

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
    assert "nginx" in app_ids


def test_load_context_does_not_require_embedded_component_sources(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)
    _config, _paths = _load_context(config_path)


def test_create_does_not_embed_component_sources_block(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert result.exit_code == 0, result.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "component_sources" not in payload


def test_create_seeds_component_source_defaults_into_config(tmp_path: Path) -> None:
    module_dir = tmp_path / "modules" / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    (module_dir / "variables.tf").write_text(
        'variable "cluster_name" { type = string }\n'
        'variable "cpu_nodes_count" { type = number }\n',
        encoding="utf-8",
    )
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "demo-module",
                            "source": str(module_dir),
                            "enable": True,
                            "defaults": {
                                "inputs.cluster_name": "demo-cluster",
                                "inputs.cpu_nodes_count": 3,
                            },
                        }
                    ]
                },
                "apps": {
                    "helm_charts": [
                        {
                            "name": "demo-app",
                            "repo": "https://example.invalid/charts",
                            "version": "1.0.0",
                            "namespace": "demo",
                            "releasename": "demo-app",
                            "enable": True,
                            "defaults": {
                                "values.replicaCount": 2,
                                "values.image.tag": "stable",
                            },
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(
        app,
        [
            "--component-sources-file",
            str(sources_file),
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--no-validate-sources",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_instance_config_path(deployments_root).read_text(encoding="utf-8"))
    demo_module = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "demo-module"
    )
    assert demo_module["inputs"]["cluster_name"] == "demo-cluster"
    assert demo_module["inputs"]["cpu_nodes_count"] == 3

    demo_app = next(
        item
        for item in payload["apps"]["charts"]
        if isinstance(item, dict) and item.get("id") == "demo-app"
    )
    assert demo_app["values"]["replicaCount"] == 2
    assert demo_app["values"]["image"]["tag"] == "stable"


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
    charts = payload.get("apps", {}).get("charts", [])
    n8n_row = None
    for row in charts:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip().lower() == "n8n":
            n8n_row = row
            break
    assert isinstance(n8n_row, dict)
    assert n8n_row.get("namespace") == "automation"
    assert n8n_row.get("release-name") == "workflow-core"


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
    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    mk8s_row = next(
        row for row in components if isinstance(row, dict) and str(row.get("id", "")).strip() == "mk8s"
    )
    inputs = mk8s_row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    inputs["cluster_name"] = "custom-cluster"
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
    refreshed_components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(refreshed_components, list)
    refreshed_mk8s = next(
        row for row in refreshed_components if isinstance(row, dict) and str(row.get("id", "")).strip() == "mk8s"
    )
    refreshed_inputs = refreshed_mk8s.get("inputs", {})
    assert isinstance(refreshed_inputs, dict)
    assert refreshed_inputs["cluster_name"] == "custom-cluster"
    assert infra_enabled["mk8s"] is True
    assert "object-storage" not in infra_enabled
    assert apps_enabled["n8n"] is True
    assert "component_sources" not in refreshed


def test_create_existing_config_selection_uses_full_component_registry(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "none",
    )
    assert first.exit_code == 0, first.output

    second = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "n8n",
    )
    assert second.exit_code == 0, second.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled["n8n"] is True


def test_create_dependency_resolution_uses_existing_chart_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root, "--app", "n8n")
    assert first.exit_code == 0, first.output

    config_path = _instance_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    for row in charts:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip().lower() != "n8n":
            continue
        row["repo"] = "https://example.invalid/custom-charts"
        break
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    called_repos: list[str] = []

    def _fake_chart_metadata(
        *,
        chart_name_or_ref: str,
        chart_repo: str,
        chart_version: str,
        cache,
    ):
        _ = (chart_name_or_ref, chart_version, cache)
        called_repos.append(chart_repo)
        return chart_name_or_ref, set(), None

    monkeypatch.setattr("nebius_cxcli.cli._helm_chart_metadata", _fake_chart_metadata)

    second = _create_non_interactive(deployments_root)
    assert second.exit_code == 0, second.output
    assert "https://example.invalid/custom-charts" in called_repos


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
    assert "defaults:" in content
    assert "shell: bash" in content
    assert "cache: pip" in content
    assert "has_changes" in content
    assert 'echo "NEBIUS_SA_ID=${NEBIUS_SA_ID}"' in content
    assert 'echo "NEBIUS_AUTH_PUBLIC_KEY_ID=${NEBIUS_AUTH_PUBLIC_KEY_ID}"' in content
    assert 'echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${KEY_PATH}"' in content
    assert "NEBIUS_DISCOVER_TARGET: customer/deployments-root" in content
    assert "customer/deployments-root" in content
    assert "name: ${{ matrix.github_environment }}" in content
    assert '**/customer/deployments-root/**/generated/**' in content
    assert '**/customer/deployments-root/**/config.yaml' not in content
    assert "Validate source contract changes" not in content
    assert "validate-generated" in content
    assert "Inventory outputs" not in content
    assert "Email inventory" not in content


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


def test_auth_rejects_github_flags_when_no_bootstrap_ci() -> None:
    result = runner.invoke(
        app,
        [
            "auth",
            "--project-id",
            "project-456",
            "--github-repo",
            "owner/repo",
        ],
    )
    assert result.exit_code == 1
    assert "--github-repo and --github-token-env are valid only with --bootstrap-ci" in result.output


def test_discover_accepts_non_git_directory(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    config_path = (
        deployments_root
        / "instances"
        / "client-a--tenant-123"
        / "project-456"
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
                "config": "deployments/instances/client-a--tenant-123/project-456/config.yaml",
                "generated": "deployments/instances/client-a--tenant-123/project-456/generated",
                "config_changed": False,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
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
        / "project-456"
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
                "config": "deployments/instances/client-a--tenant-123/project-456/config.yaml",
                "generated": "deployments/instances/client-a--tenant-123/project-456/generated",
                "config_changed": False,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }
