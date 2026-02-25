from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

from nebius_cxcli.cli import (
    _ensure_runtime_auth_material,
    _seed_external_secrets_mysterybox_auth_secret,
    app,
)
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.iam_bootstrap import CIBootstrapResult
from nebius_cxcli.paths import resolve_instance_paths, validate_path_alignment
from nebius_cxcli.render import RenderResult

runner = CliRunner()


def _git_init(repo_root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def test_create_from_nested_path_writes_workflow_at_git_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    target_path = repo_root / "customer" / "deployments-root"
    target_path.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(target_path),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--env",
            "prod",
            "--cluster-name",
            "client-a-prod",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
            "--bootstrap-ci",
            "--no-auto-auth-bootstrap",
        ],
    )
    assert result.exit_code == 0, result.output

    workflow_path = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    assert workflow_path.exists()
    assert (target_path / "instances").exists()

    workflow_text = workflow_path.read_text(encoding="utf-8")
    assert "NEBIUS_DISCOVER_TARGET: customer/deployments-root" in workflow_text
    assert "NEBIUS_CXCLI_REF: main" in workflow_text
    assert 'nebius-cxcli validate --strict "${{ matrix.config }}"' in workflow_text
    assert (
        'pip install "git+https://github.com/nebius/nebius-ps-services.git@${{ env.NEBIUS_CXCLI_REF }}#subdirectory=services/nebius-cxcli"'
        in workflow_text
    )
    assert (
        'nebius mk8s cluster get-credentials --id "${CLUSTER_ID}" --external --profile "${PROFILE}"'
        in workflow_text
    )
    assert 'echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${KEY_PATH}" >> "$GITHUB_ENV"' in workflow_text
    assert "NEBIUS_IAM_TOKEN" not in workflow_text


def test_discover_accepts_target_path_and_outputs_repo_relative_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    target_path = repo_root / "customer" / "deployments-root"
    config_path = (
        target_path
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
        / "config.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("version: v1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "discover",
            str(target_path),
            "--all",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": (
                    "customer/deployments-root/instances/"
                    "client-a--tenant-123/prod/client-a-prod/config.yaml"
                )
            }
        ]
    }


def test_list_catalog_top_level_infra_contains_required_and_optional_entries() -> None:
    result = runner.invoke(app, ["list-catalog", "--infra"])
    assert result.exit_code == 0, result.output
    assert "SCOPE" in result.output
    assert "STATUS" in result.output
    assert "mk8s" in result.output
    assert "required" in result.output
    assert "managed-postgresql" in result.output
    assert "default" in result.output


def test_list_catalog_infra_contains_required_and_optional_entries() -> None:
    result = runner.invoke(app, ["catalog", "list", "--infra"])
    assert result.exit_code == 0, result.output
    assert "SCOPE" in result.output
    assert "infra" in result.output
    assert "mk8s" in result.output
    assert "required" in result.output
    assert "managed-postgresql" in result.output
    assert "default" in result.output


def test_list_catalog_apps_filter_shows_only_apps_entries() -> None:
    result = runner.invoke(app, ["catalog", "list", "--apps"])
    assert result.exit_code == 0, result.output
    assert "apps" in result.output
    assert "n8n" in result.output
    assert "infra" not in result.output
    assert "managed-postgresql" not in result.output


def test_list_catalog_defaults_only_excludes_optional_entries() -> None:
    result = runner.invoke(app, ["list-catalog", "--defaults"])
    assert result.exit_code == 0, result.output
    assert "default" in result.output
    assert "optional" not in result.output


def test_catalog_add_and_list_custom_app_entry(tmp_path: Path) -> None:
    catalog_file = tmp_path / "catalogs" / "catalog.yaml"
    add_result = runner.invoke(
        app,
        [
            "catalog",
            "add",
            "--scope",
            "apps",
            "--id",
            "argo-cd",
            "--description",
            "Argo CD Helm release",
            "--chart-repo",
            "https://argoproj.github.io/argo-helm",
            "--chart-name",
            "argo-cd",
            "--version",
            "7.6.7",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert add_result.exit_code == 0, add_result.output

    list_result = runner.invoke(
        app,
        [
            "catalog",
            "list",
            "--apps",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert list_result.exit_code == 0, list_result.output
    assert "argo-cd" in list_result.output
    assert "helm_release" in list_result.output


def test_catalog_add_interactive_wizard_creates_custom_app_entry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("nebius_cxcli.cli._is_tty_session", lambda: False)
    catalog_file = tmp_path / "catalogs" / "catalog.yaml"
    add_result = runner.invoke(
        app,
        [
            "catalog",
            "add",
            "--catalog-file",
            str(catalog_file),
        ],
        input=(
            "apps\n"
            "argo-rollouts\n"
            "Argo Rollouts Helm release\n"
            "https://argoproj.github.io/argo-helm\n"
            "argo-rollouts\n"
        ),
    )
    assert add_result.exit_code == 0, add_result.output

    list_result = runner.invoke(
        app,
        [
            "catalog",
            "list",
            "--apps",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert list_result.exit_code == 0, list_result.output
    assert "argo-rollouts" in list_result.output
    assert "helm_release" in list_result.output


def test_catalog_add_no_interactive_requires_required_flags(tmp_path: Path) -> None:
    catalog_file = tmp_path / "catalogs" / "catalog.yaml"
    result = runner.invoke(
        app,
        [
            "catalog",
            "add",
            "--no-interactive",
            "--scope",
            "apps",
            "--id",
            "argo-cd",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert result.exit_code == 1
    assert "Missing required option: --description" in result.output


def test_create_with_custom_catalog_entry_stores_selection_in_config(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    catalog_file = deployments_root / "catalogs" / "catalog.yaml"

    add_result = runner.invoke(
        app,
        [
            "catalog",
            "add",
            "--scope",
            "apps",
            "--id",
            "argo-cd",
            "--description",
            "Argo CD Helm release",
            "--chart-repo",
            "https://argoproj.github.io/argo-helm",
            "--chart-name",
            "argo-cd",
            "--version",
            "7.6.7",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert add_result.exit_code == 0, add_result.output

    create_result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-ext",
            "--tenant-id",
            "tenant-ext",
            "--env",
            "prod",
            "--cluster-name",
            "client-ext-prod",
            "--project-id",
            "project-ext",
            "--subnet-id",
            "subnet-ext",
            "--app",
            "argo-cd",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert create_result.exit_code == 0, create_result.output

    config_path = (
        deployments_root
        / "instances"
        / "client-ext--tenant-ext"
        / "prod"
        / "client-ext-prod"
        / "config.yaml"
    )
    config = load_config(config_path)
    assert "argo-cd" in config.catalog.apps.selected
    assert config.catalog.apps.values["argo-cd"]["engine_type"] == "helm_release"
    assert (
        config.catalog.apps.values["argo-cd"]["source"]
        == "https://argoproj.github.io/argo-helm/argo-cd"
    )
    assert config.catalog.apps.values["argo-cd"]["version"] == "7.6.7"


def test_create_with_custom_infra_catalog_entry_stores_selection_in_config(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    catalog_file = deployments_root / "catalogs" / "catalog.yaml"

    add_result = runner.invoke(
        app,
        [
            "catalog",
            "add",
            "--scope",
            "infra",
            "--id",
            "managed-redis",
            "--description",
            "Managed Redis module",
            "--module-source",
            "git::https://github.com/example/iac.git//modules/managed-redis",
            "--version",
            "1.0.0",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert add_result.exit_code == 0, add_result.output

    create_result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-infra-ext",
            "--tenant-id",
            "tenant-infra-ext",
            "--env",
            "prod",
            "--cluster-name",
            "client-infra-ext-prod",
            "--project-id",
            "project-infra-ext",
            "--subnet-id",
            "subnet-infra-ext",
            "--infra",
            "managed-redis",
            "--catalog-file",
            str(catalog_file),
        ],
    )
    assert create_result.exit_code == 0, create_result.output

    config_path = (
        deployments_root
        / "instances"
        / "client-infra-ext--tenant-infra-ext"
        / "prod"
        / "client-infra-ext-prod"
        / "config.yaml"
    )
    config = load_config(config_path)
    assert "managed-redis" in config.catalog.infra.selected
    assert config.catalog.infra.values["managed-redis"]["engine_type"] == "terraform_module"
    assert (
        config.catalog.infra.values["managed-redis"]["source"]
        == "git::https://github.com/example/iac.git//modules/managed-redis"
    )
    assert config.catalog.infra.values["managed-redis"]["version"] == "1.0.0"
    assert config.catalog.infra.values["managed-redis"]["inputs"] == {}
    assert config.catalog.infra.values["managed-redis"]["depends_on_platform"] is True


def test_create_catalog_flags_control_enabled_components(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-cat",
            "--tenant-id",
            "tenant-cat",
            "--env",
            "prod",
            "--cluster-name",
            "client-cat-prod",
            "--project-id",
            "project-cat",
            "--subnet-id",
            "subnet-cat",
            "--infra",
            "managed-postgresql,sfs",
            "--app",
            "n8n,external-secrets",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Enabled infra catalog:" in result.output
    assert "Enabled apps catalog:" in result.output

    config_path = (
        deployments_root
        / "instances"
        / "client-cat--tenant-cat"
        / "prod"
        / "client-cat-prod"
        / "config.yaml"
    )
    config = load_config(config_path)

    assert config.infra.managed_postgresql.enabled is True
    assert config.infra.sfs.enabled is True
    assert config.infra.wireguard_jumphost.enabled is False
    assert config.infra.ssh_jumphost.enabled is False
    assert config.infra.mysterybox.enabled is False

    assert config.apps.workloads.n8n.enabled is True
    assert config.apps.platform.external_secrets.enabled is True
    assert config.apps.platform.envoy_gateway.enabled is True
    assert config.apps.platform.cert_manager.enabled is False
    assert config.apps.platform.external_dns.enabled is False
    assert config.apps.platform.observability.enabled is False


def test_create_accepts_custom_deployments_root_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    custom_root = repo_root / "customer" / "deployments-root" / "my-folder-name"
    custom_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(custom_root),
            "--no-interactive",
            "--client-name",
            "client-c",
            "--tenant-id",
            "tenant-321",
            "--env",
            "prod",
            "--cluster-name",
            "client-c-prod",
            "--project-id",
            "project-321",
            "--subnet-id",
            "subnet-abc123",
            "--bootstrap-ci",
            "--no-auto-auth-bootstrap",
        ],
    )
    assert result.exit_code == 0, result.output

    config_path = (
        custom_root
        / "instances"
        / "client-c--tenant-321"
        / "prod"
        / "client-c-prod"
        / "config.yaml"
    )
    assert config_path.exists()

    workflow_path = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    assert "NEBIUS_DISCOVER_TARGET: customer/deployments-root/my-folder-name" in workflow_text


def test_create_builds_hierarchy_and_valid_starter_config(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
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
            "client-a-prod",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--bootstrap-ci",
            "--no-auto-auth-bootstrap",
        ],
    )
    assert result.exit_code == 0, result.output

    config_path = (
        deployments_root
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
        / "config.yaml"
    )
    assert config_path.exists()
    assert (config_path.parent / "generated" / "infra").exists()
    assert (config_path.parent / "generated" / "flux" / "sources").exists()
    assert (config_path.parent / "generated" / "flux" / "apps" / "platform").exists()
    assert (config_path.parent / "generated" / "flux" / "apps" / "workloads").exists()
    assert (config_path.parent / "generated" / "inventory").exists()
    assert (config_path.parent / "generated" / "inventory" / "inventory.md").exists()

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    assert config.client_info.client_name == "client-a"
    assert config.client_info.nebius.project_id == "project-456"
    assert config.client_info.nebius.region_id == "eu-north1"
    assert (repo_root / ".github" / "workflows" / "nebius-deployments.yml").exists()


def test_create_is_idempotent_when_config_exists(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "stage",
            "--cluster-name",
            "client-z-stage",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--bootstrap-ci",
            "--no-auto-auth-bootstrap",
        ],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "stage",
            "--cluster-name",
            "client-z-stage",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--bootstrap-ci",
            "--no-auto-auth-bootstrap",
        ],
    )
    assert second.exit_code == 0, second.output
    assert "Config exists, keeping current file" in second.output

    config_path = (
        deployments_root
        / "instances"
        / "client-z--tenant-999"
        / "stage"
        / "client-z-stage"
        / "config.yaml"
    )
    assert config_path.exists()
    assert (repo_root / ".github" / "workflows" / "nebius-deployments.yml").exists()


def test_create_interactive_creates_instance(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
        ],
        input="\n".join(
            [
                "client-i",
                "tenant-888",
                "prod",
                "client-i-prod",
                "project-888",
                "",
                "ops@example.com",
                "",
                "",
                "",
            ]
        ),
    )
    assert result.exit_code == 0, result.output

    config_path = (
        deployments_root
        / "instances"
        / "client-i--tenant-888"
        / "prod"
        / "client-i-prod"
        / "config.yaml"
    )
    assert config_path.exists()
    config = load_config(config_path)
    assert config.client_info.nebius.region_id == "eu-north1"
    assert config.infra.mk8s.subnet_id == "subnet-REPLACE-ME"


def test_validate_strict_rejects_starter_placeholders(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-s",
            "--tenant-id",
            "tenant-555",
            "--env",
            "prod",
            "--cluster-name",
            "client-s-prod",
            "--project-id",
            "project-555",
            "--subnet-id",
            "subnet-abc123",
        ],
    )
    assert created.exit_code == 0, created.output

    config_path = (
        deployments_root
        / "instances"
        / "client-s--tenant-555"
        / "prod"
        / "client-s-prod"
        / "config.yaml"
    )
    strict_result = runner.invoke(
        app,
        [
            "validate",
            "--strict",
            str(config_path),
        ],
    )
    assert strict_result.exit_code == 1
    assert "Strict validation failed" in strict_result.output
    assert "infra.ssh_public_key" in strict_result.output
    assert "apps.workloads.n8n.route.hostname" in strict_result.output


def test_validate_strict_accepts_config_after_placeholder_updates(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "nebius-deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-t",
            "--tenant-id",
            "tenant-556",
            "--env",
            "prod",
            "--cluster-name",
            "client-t-prod",
            "--project-id",
            "project-556",
            "--subnet-id",
            "subnet-abc123",
        ],
    )
    assert created.exit_code == 0, created.output

    config_path = (
        deployments_root
        / "instances"
        / "client-t--tenant-556"
        / "prod"
        / "client-t-prod"
        / "config.yaml"
    )
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace(
        "ssh-ed25519 AAAA-REPLACE-WITH-YOUR-KEY",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
    )
    config_text = config_text.replace(
        "n8n.client-t-prod.example.internal",
        "n8n.client-t-prod.customer.internal",
    )
    config_path.write_text(config_text, encoding="utf-8")

    strict_result = runner.invoke(
        app,
        [
            "validate",
            "--strict",
            str(config_path),
        ],
    )
    assert strict_result.exit_code == 0, strict_result.output
    assert "Valid (strict):" in strict_result.output


def test_validate_rejects_custom_apps_catalog_missing_source(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)
    config_path = cluster_dir / "config.yaml"

    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="client-a-prod",
            project_id="project-456",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
        )
    )
    payload["catalog"]["apps"]["selected"] = ["custom-app"]
    payload["catalog"]["apps"]["values"]["custom-app"] = {
        "engine_type": "helm_release",
        "namespace": "custom-app",
        "release_name": "custom-app",
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "validate",
            str(config_path),
        ],
    )
    assert result.exit_code == 1
    assert "Missing chart source for custom app catalog entry 'custom-app'" in result.output


def test_validate_rejects_custom_infra_catalog_non_mapping_inputs(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)
    config_path = cluster_dir / "config.yaml"

    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="client-a-prod",
            project_id="project-456",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
        )
    )
    payload["catalog"]["infra"]["selected"] = ["custom-infra"]
    payload["catalog"]["infra"]["values"]["custom-infra"] = {
        "engine_type": "terraform_module",
        "source": "git::https://github.com/example/iac.git//modules/custom-infra",
        "inputs": "not-a-mapping",
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "validate",
            str(config_path),
        ],
    )
    assert result.exit_code == 1
    assert "catalog.infra.values.custom-infra.inputs must be a mapping" in result.output


def test_create_requires_existing_deployments_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    missing_root = repo_root / "missing-root"
    result = runner.invoke(
        app,
        [
            "create",
            str(missing_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--env",
            "prod",
            "--cluster-name",
            "client-a-prod",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
        ],
    )
    assert result.exit_code == 1
    assert "Deployments root does not exist" in result.output


def test_discover_requires_existing_deployments_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    missing_root = repo_root / "missing-root"
    result = runner.invoke(
        app,
        [
            "discover",
            str(missing_root),
        ],
    )
    assert result.exit_code == 1
    assert "Deployments root does not exist" in result.output


def test_create_requires_git_repository(tmp_path: Path) -> None:
    non_repo_root = tmp_path / "non-repo" / "my-folder-name"
    non_repo_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(non_repo_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--env",
            "prod",
            "--cluster-name",
            "client-a-prod",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
        ],
    )
    assert result.exit_code == 0, result.output


def test_create_bootstrap_ci_requires_git_repository(tmp_path: Path) -> None:
    non_repo_root = tmp_path / "non-repo" / "my-folder-name"
    non_repo_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(non_repo_root),
            "--no-interactive",
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--env",
            "prod",
            "--cluster-name",
            "client-a-prod",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
            "--bootstrap-ci",
        ],
    )
    assert result.exit_code == 1
    assert "Target path must be inside a git repository" in result.output


def test_discover_requires_git_repository(tmp_path: Path) -> None:
    non_repo_root = tmp_path / "non-repo" / "my-folder-name"
    non_repo_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "discover",
            str(non_repo_root),
        ],
    )
    assert result.exit_code == 1
    assert "Target path must be inside a git repository" in result.output


def test_auth_bootstrap_no_github_sync_does_not_print_secret_values(monkeypatch) -> None:
    expected = CIBootstrapResult(
        project_id="project-456",
        service_account_name="nebius-cxcli-ci",
        service_account_id="serviceaccount-123",
        service_account_created=True,
        roles_created=["roles/editor"],
        roles_already_present=[],
        auth_public_key_id="publickey-123",
        auth_private_key_pem="-----BEGIN PRIVATE KEY-----\nline\n-----END PRIVATE KEY-----\n",
        s3_access_key_id="AKIAXAMPLE",
        s3_secret_access_key="secret-value",
    )

    def _fake_bootstrap(**_: object) -> CIBootstrapResult:
        return expected

    monkeypatch.setattr("nebius_cxcli.cli.bootstrap_ci_service_account", _fake_bootstrap)

    result = runner.invoke(
        app,
        [
            "auth",
            "bootstrap",
            "--project-id",
            "project-456",
            "--no-github-sync",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "CI auth bootstrap completed." in result.output
    assert "Service account created." in result.output
    assert "Role grants applied." in result.output
    assert "Authorized key created." in result.output
    assert "Object Storage access key created." in result.output
    assert "GitHub sync is disabled. Generated secret values are intentionally not printed." in (
        result.output
    )
    assert "NEBIUS_S3_SECRET_ACCESS_KEY=secret-value" not in result.output
    assert "NEBIUS_AUTH_PRIVATE_KEY_PEM<<EOF" not in result.output


def test_auth_bootstrap_can_use_instance_config_for_project_id(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="client-a-prod",
            project_id="project-from-config",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
        ),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _fake_bootstrap(**kwargs: object) -> CIBootstrapResult:
        captured.update(kwargs)
        return CIBootstrapResult(
            project_id="project-from-config",
            service_account_name="nebius-cxcli-ci",
            service_account_id="serviceaccount-321",
            service_account_created=False,
            roles_created=[],
            roles_already_present=["roles/editor"],
            auth_public_key_id="publickey-321",
            auth_private_key_pem="pem",
            s3_access_key_id="AKIA321",
            s3_secret_access_key="secret321",
        )

    monkeypatch.setattr("nebius_cxcli.cli.bootstrap_ci_service_account", _fake_bootstrap)

    result = runner.invoke(
        app,
        [
            "auth",
            "bootstrap",
            "--instance-config",
            str(cfg),
            "--json",
            "--no-github-sync",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {"status": "ok"}
    assert captured["project_id"] == "project-from-config"


def test_runtime_auth_is_auto_bootstrapped_for_local_commands(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="client-a-prod",
            project_id="project-456",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
        ),
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)

    expected = CIBootstrapResult(
        project_id="project-456",
        service_account_name="nebius-cxcli-runtime",
        service_account_id="serviceaccount-rt",
        service_account_created=True,
        roles_created=["roles/editor"],
        roles_already_present=[],
        auth_public_key_id="publickey-rt",
        auth_private_key_pem="-----BEGIN PRIVATE KEY-----\nline\n-----END PRIVATE KEY-----\n",
        s3_access_key_id="AKIART",
        s3_secret_access_key="secret-rt",
    )

    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    monkeypatch.setattr(
        "nebius_cxcli.cli.bootstrap_ci_service_account",
        lambda **_: expected,
    )

    _ensure_runtime_auth_material(cfg, need_terraform=True, need_eso_mysterybox=True)

    assert os.environ["NEBIUS_SA_ID"] == "serviceaccount-rt"
    assert os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] == "publickey-rt"
    assert os.environ["NEBIUS_AUTH_PRIVATE_KEY_PEM"].startswith("-----BEGIN PRIVATE KEY-----")
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIART"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "secret-rt"
    key_path = Path(os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"])
    assert key_path.exists()


def test_seed_external_secrets_mysterybox_auth_secret_creates_bridge_auth_secret(
    tmp_path: Path, monkeypatch
) -> None:
    cfg_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            env="prod",
            cluster_name="client-a-prod",
            project_id="project-456",
            region_id="eu-north1",
            subnet_id="subnet-abc123",
            email="ops@example.com",
        )
    )
    payload["infra"]["mysterybox"]["enabled"] = True
    payload["infra"]["mysterybox"]["secrets"] = [
        {
            "id": "n8n-runtime",
            "scope": "apps",
            "name": "client-a-prod-n8n-runtime",
            "entries": [{"key": "N8N_ENCRYPTION_KEY", "value_from_env": "N8N_ENCRYPTION_KEY"}],
            "k8s_sync": {"enabled": True, "namespace": "n8n"},
        }
    ]
    payload["apps"]["platform"]["external_secrets"]["enabled"] = True
    payload["apps"]["platform"]["external_secrets"]["mysterybox"]["enabled"] = True
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    cfg = load_config(cfg_path)

    monkeypatch.setenv("NEBIUS_SA_ID", "serviceaccount-123")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "publickey-123")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_PEM", "-----BEGIN PRIVATE KEY-----\nline\n")
    monkeypatch.setenv("NEBIUS_MYSTERYBOX_WEBHOOK_TOKEN", "token-123")

    applied_docs: list[dict[str, object]] = []
    monkeypatch.setattr(
        "nebius_cxcli.cli._apply_kubernetes_doc",
        lambda doc: applied_docs.append(doc),
    )

    _seed_external_secrets_mysterybox_auth_secret(cfg)

    bridge_auth_doc = next(
        doc
        for doc in applied_docs
        if doc.get("kind") == "Secret"
        and doc.get("metadata", {}).get("name") == "mysterybox-bridge-webhook-auth"
    )
    mysterybox_auth_doc = next(
        doc
        for doc in applied_docs
        if doc.get("kind") == "Secret"
        and doc.get("metadata", {}).get("name") == "nebius-mysterybox-auth"
    )
    assert bridge_auth_doc["metadata"]["labels"]["external-secrets.io/type"] == "webhook"
    assert bridge_auth_doc["stringData"]["token"] == "token-123"
    assert mysterybox_auth_doc["stringData"]["NEBIUS_API_ENDPOINT"] == "api.nebius.cloud:443"


def test_create_triggers_auto_auth_bootstrap_by_default(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    captured: dict[str, object] = {}

    def _fake_auto_bootstrap(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "nebius_cxcli.cli._auto_bootstrap_ci_auth_and_secrets", _fake_auto_bootstrap
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "prod",
            "--cluster-name",
            "client-z-prod",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
            "--bootstrap-ci",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["project_id"] == "project-999"
    assert captured["repo_root"] == repo_root


def test_create_bootstrap_ci_fails_without_github_token_context(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("nebius_cxcli.cli.read_github_token", lambda **_: None)

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
            "client-a-prod",
            "--project-id",
            "project-456",
            "--subnet-id",
            "subnet-abc123",
            "--bootstrap-ci",
        ],
    )
    assert result.exit_code == 1
    assert "Automatic CI auth bootstrap requires a GitHub token" in result.output


def test_create_default_mode_does_not_call_auto_auth_bootstrap(tmp_path: Path, monkeypatch) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    called = {"value": False}

    def _fake_auto_bootstrap(**_: object) -> None:
        called["value"] = True

    monkeypatch.setattr(
        "nebius_cxcli.cli._auto_bootstrap_ci_auth_and_secrets", _fake_auto_bootstrap
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "prod",
            "--cluster-name",
            "client-z-prod",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
        ],
    )
    assert result.exit_code == 0, result.output
    assert called["value"] is False
    assert not (deployments_root / ".github").exists()


def test_create_rejects_deploy_and_bootstrap_ci_together(tmp_path: Path) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)
    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "prod",
            "--cluster-name",
            "client-z-prod",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
            "--deploy",
            "--bootstrap-ci",
        ],
    )
    assert result.exit_code == 1
    assert "--deploy and --bootstrap-ci are mutually exclusive" in result.output


def test_create_deploy_rejects_starter_placeholders(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "prod",
            "--cluster-name",
            "client-z-prod",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
            "--deploy",
        ],
    )
    assert result.exit_code == 1
    assert "Strict validation failed" in result.output


def test_create_deploy_runs_pipeline_for_existing_ready_config(tmp_path: Path, monkeypatch) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "prod",
            "--cluster-name",
            "client-z-prod",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
        ],
    )
    assert created.exit_code == 0, created.output

    cfg = (
        deployments_root
        / "instances"
        / "client-z--tenant-999"
        / "prod"
        / "client-z-prod"
        / "config.yaml"
    )
    text = cfg.read_text(encoding="utf-8")
    text = text.replace(
        "ssh-ed25519 AAAA-REPLACE-WITH-YOUR-KEY",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
    )
    text = text.replace("n8n.client-z-prod.example.internal", "n8n.client-z-prod.customer.internal")
    cfg.write_text(text, encoding="utf-8")

    calls: list[str] = []

    def _fake_render_instance(*_: object, **__: object) -> RenderResult:
        calls.append("render")
        return RenderResult(files_written=[cfg.parent / "generated" / "infra" / "main.tf"])

    def _fake_terraform_apply(*_: object, **__: object) -> None:
        calls.append("terraform_apply")

    def _fake_apply_rendered_flux(*_: object, **__: object) -> None:
        calls.append("apply_flux")

    monkeypatch.setattr("nebius_cxcli.cli.render_instance", _fake_render_instance)
    monkeypatch.setattr("nebius_cxcli.cli.terraform_apply", _fake_terraform_apply)
    monkeypatch.setattr("nebius_cxcli.cli._ensure_runtime_auth_material", lambda *_, **__: None)
    monkeypatch.setattr("nebius_cxcli.cli._apply_rendered_flux", _fake_apply_rendered_flux)

    deployed = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-z",
            "--tenant-id",
            "tenant-999",
            "--env",
            "prod",
            "--cluster-name",
            "client-z-prod",
            "--project-id",
            "project-999",
            "--subnet-id",
            "subnet-abc123",
            "--deploy",
        ],
    )
    assert deployed.exit_code == 0, deployed.output
    assert calls == ["render", "terraform_apply", "apply_flux"]


def test_render_deploy_rejects_starter_placeholders(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-r",
            "--tenant-id",
            "tenant-r",
            "--env",
            "prod",
            "--cluster-name",
            "client-r-prod",
            "--project-id",
            "project-r",
            "--subnet-id",
            "subnet-r",
        ],
    )
    assert created.exit_code == 0, created.output

    cfg = (
        deployments_root
        / "instances"
        / "client-r--tenant-r"
        / "prod"
        / "client-r-prod"
        / "config.yaml"
    )
    result = runner.invoke(
        app,
        [
            "render",
            "--deploy",
            str(cfg),
        ],
    )
    assert result.exit_code == 1
    assert "Strict validation failed" in result.output


def test_render_deploy_runs_pipeline_for_ready_config(tmp_path: Path, monkeypatch) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-r",
            "--tenant-id",
            "tenant-r",
            "--env",
            "prod",
            "--cluster-name",
            "client-r-prod",
            "--project-id",
            "project-r",
            "--subnet-id",
            "subnet-r",
        ],
    )
    assert created.exit_code == 0, created.output

    cfg = (
        deployments_root
        / "instances"
        / "client-r--tenant-r"
        / "prod"
        / "client-r-prod"
        / "config.yaml"
    )
    text = cfg.read_text(encoding="utf-8")
    text = text.replace(
        "ssh-ed25519 AAAA-REPLACE-WITH-YOUR-KEY",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8Yq7Rr0x2GdQ8gJ5Q40gF4yHahx7s6vH8kKf+demo",
    )
    text = text.replace("n8n.client-r-prod.example.internal", "n8n.client-r-prod.customer.internal")
    cfg.write_text(text, encoding="utf-8")

    calls: list[str] = []

    def _fake_render_instance(*_: object, **__: object) -> RenderResult:
        calls.append("render")
        return RenderResult(files_written=[cfg.parent / "generated" / "infra" / "main.tf"])

    def _fake_terraform_apply(*_: object, **__: object) -> None:
        calls.append("terraform_apply")

    def _fake_apply_rendered_flux(*_: object, **__: object) -> None:
        calls.append("apply_flux")

    monkeypatch.setattr("nebius_cxcli.cli.render_instance", _fake_render_instance)
    monkeypatch.setattr("nebius_cxcli.cli.terraform_apply", _fake_terraform_apply)
    monkeypatch.setattr("nebius_cxcli.cli._ensure_runtime_auth_material", lambda *_, **__: None)
    monkeypatch.setattr("nebius_cxcli.cli._apply_rendered_flux", _fake_apply_rendered_flux)

    result = runner.invoke(
        app,
        [
            "render",
            "--deploy",
            str(cfg),
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == ["render", "terraform_apply", "apply_flux"]
