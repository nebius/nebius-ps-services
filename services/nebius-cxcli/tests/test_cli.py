from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli_module
import nebius_cxcli.component_sources as component_sources
import nebius_cxcli.templates as templates_module
from nebius_cxcli.cli import _load_context, _load_runtime_context, app
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.email_settings import EmailSettings
from nebius_cxcli.quota_checks import QuotaCheck, QuotaReport

runner = CliRunner()

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f "
    "demo@example"
)


def _portable_chart_source(*, repo: str, chart: str, version: str = "") -> dict[str, object]:
    portable: dict[str, object] = {
        "repo": repo,
        "chart": chart,
    }
    if version:
        portable["version"] = version
    return {"portable": portable}


def _empty_quota_report() -> cli_module.QuotaReport:
    return cli_module.QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
    )


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", raising=False)
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
    monkeypatch.setattr(
        "nebius_cxcli.cli.rendered_module_sources",
        lambda _config, *, source_profile: (),
    )
    monkeypatch.setattr("nebius_cxcli.infra_render.module_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli._try_generate_terraform_lock_file", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
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
    monkeypatch.setattr(
        cli_module,
        "assess_live_quotas",
        lambda *_args, **_kwargs: _empty_quota_report(),
    )
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
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


def _mock_bootstrap_ci_github_sync(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo_slug: str = "owner/repo",
    github_token: str = "token-123",
    email_sync_result: cli_module.GitHubEmailSyncResult | None = None,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_resolve_bootstrap_ci_github_target",
        lambda *, github_repo, github_token_env, repo_root: (
            github_repo or repo_slug,
            github_token,
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_sync_github_email_settings",
        lambda *, repo_slug, github_environment, github_token, settings: (
            email_sync_result
            if email_sync_result is not None
            else cli_module.GitHubEmailSyncResult(
                updated_vars=[],
                updated_secrets=[],
                removed_vars=[],
                removed_secrets=[],
            )
        ),
    )


def _project_config_path(deployments_root: Path) -> Path:
    return deployments_root / "tenant-123" / "project-456" / "config.yaml"


def _project_dir(
    deployments_root: Path,
    *,
    tenant_id: str = "tenant-123",
    project_id: str = "project-456",
) -> Path:
    return deployments_root / tenant_id / project_id


def _catalog(
    *,
    infra: dict[str, object] | None = None,
    apps: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "components": {
            "infra": infra or {},
            "apps": apps or {},
        }
    }


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


def _create_named_non_interactive(
    deployments_root: Path,
    *,
    client_name: str,
    tenant_id: str,
    project_id: str,
    extra: tuple[str, ...] = (),
):
    return runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            client_name,
            "--tenant-id",
            tenant_id,
            "--project-id",
            project_id,
            "--no-validate-sources",
            *extra,
        ],
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


def _component_add(config_path: Path, *extra: str, input_text: str | None = None):
    return runner.invoke(
        app,
        [
            "component",
            "add",
            str(config_path),
            "--no-validate-sources",
            *extra,
        ],
        input=input_text,
    )


def _component_remove(config_path: Path, *extra: str, input_text: str | None = None):
    return runner.invoke(
        app,
        [
            "component",
            "remove",
            str(config_path),
            *extra,
        ],
        input=input_text,
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
    assert "DEPLOYMENTS_ROOT" in result.output
    assert "Deployments root directory." in result.output
    assert "--env" not in result.output
    assert "Environment (dev|stage|prod)" not in result.output


def test_create_existing_instance_requires_force_to_overwrite(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    second = _create_non_interactive(deployments_root)
    assert second.exit_code == 1, second.output
    normalized_output = " ".join(second.output.split())
    assert "no longer reconciles existing configs" in normalized_output
    assert "component list/add/remove" in normalized_output
    assert "--force" in second.output


def test_create_warns_when_live_quota_is_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cli_module,
        "assess_live_quotas",
        lambda *_args, **_kwargs: QuotaReport(
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            checked_at="2026-04-10T00:00:00+00:00",
            checks=(
                QuotaCheck(
                    component_id="ssh-jumphost",
                    instance_id="ssh-jumphost",
                    component_label="ssh-jumphost",
                    quota_name="compute.instance.count",
                    region="eu-north1",
                    required=1,
                    reason="one VM",
                    unit="",
                    available=0,
                    sufficient=False,
                    tenant_limit=0,
                    tenant_usage=0,
                    project_limit=None,
                    project_usage=0,
                    source_scope="tenant",
                    description="VM count",
                    contributors=(),
                ),
            ),
        ),
    )

    result = _create_non_interactive(deployments_root, "--infra", "ssh-jumphost")

    assert result.exit_code == 0, result.output
    assert "Create completed with quota warnings." in result.output
    assert "compute.instance.count requires 1, available 0" in result.output


def test_create_runs_post_write_validation_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module,
        "_run_runtime_validation",
        lambda *, config_path, strict, title="Runtime validation": captured.update(
            {
                "config_path": config_path,
                "strict": strict,
                "title": title,
            }
        ),
    )

    result = _create_non_interactive(deployments_root)

    assert result.exit_code == 0, result.output
    assert captured == {
        "config_path": _project_config_path(deployments_root),
        "strict": False,
        "title": "Post-create validation",
    }
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    assert payload["client_info"]["notifications"]["email_enabled"] is False
    assert payload["client_info"]["notifications"]["email"] is None


def test_create_can_skip_post_write_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module,
        "_run_runtime_validation",
        lambda *, config_path, strict, title="Runtime validation": captured.update(
            {
                "config_path": config_path,
                "strict": strict,
                "title": title,
            }
        ),
    )

    result = _create_non_interactive(deployments_root, "--no-validate-config")

    assert result.exit_code == 0, result.output
    assert captured == {}


def test_create_interactive_existing_project_requires_confirmation(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    original = config_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-validate-sources",
        ],
        input="\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Tenant ID [tenant-123]" in result.output
    assert "Project ID [project-456]" in result.output
    assert "Existing project detected." in result.output
    assert "Continue and overwrite the existing tenant/project folder from scratch?" in result.output
    assert "(y/n, q=stop wizard) [n]" in result.output
    assert "Existing deployments root detected." not in result.output
    assert "Continue and enter project identity?" not in result.output
    assert "Client name [client-a]" not in result.output
    assert "No changes applied." in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_create_interactive_existing_root_new_project_skips_overwrite_warning(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    original = config_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-validate-sources",
        ],
        input="\nproject-789\nclient-b\n\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Tenant ID [tenant-123]" in result.output
    assert "Project ID [project-456]" in result.output
    assert "Existing deployments root detected." not in result.output
    assert "Continue and enter project identity?" not in result.output
    assert "Existing project detected." not in result.output
    assert "Continue and overwrite the existing tenant/project folder from scratch?" not in result.output
    assert "Client name [client-a]" not in result.output
    assert "Created project:" in result.output
    assert config_path.read_text(encoding="utf-8") == original
    assert (deployments_root / "tenant-123" / "project-789" / "config.yaml").exists()


def test_create_non_interactive_existing_tenant_new_project_creates_config(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    original_config_path = _project_config_path(deployments_root)
    original = original_config_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-b",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-789",
            "--no-validate-sources",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Existing project found:" not in result.output
    assert "Existing project detected." not in result.output
    assert "Created project:" in result.output
    assert original_config_path.read_text(encoding="utf-8") == original
    assert (deployments_root / "tenant-123" / "project-789" / "config.yaml").exists()


def test_single_existing_project_create_defaults_reads_identity_from_existing_config(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    defaults = cli_module._single_existing_project_create_defaults(deployments_root)
    assert defaults is not None
    assert defaults.tenant_id == "tenant-123"
    assert defaults.project_id == "project-456"
    assert defaults.config_path == _project_config_path(deployments_root)


def test_single_existing_project_create_defaults_requires_unambiguous_project(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--no-interactive",
            "--client-name",
            "client-b",
            "--tenant-id",
            "tenant-456",
            "--project-id",
            "project-789",
            "--no-validate-sources",
        ],
    )
    assert second.exit_code == 0, second.output

    defaults = cli_module._single_existing_project_create_defaults(deployments_root)
    assert defaults is None


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
    assert "*/*/generated/" not in content.splitlines()
    assert "*/*/config.yaml" not in content
    assert "*/*/generated/infra/.terraform/" in content
    assert "*/*/generated/infra/crash.*.log" in content
    assert "*/*/generated/infra/terraform.auto.tfvars.json" in content

    second = _create_non_interactive(deployments_root, "--force")
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

    config_path = _project_config_path(deployments_root)
    render_result = runner.invoke(app, ["render", "--force", str(config_path)])
    assert render_result.exit_code == 0, render_result.output
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert "*/*/generated/" not in content.splitlines()
    assert "*/*/config.yaml" not in content
    assert "*/*/generated/infra/.terraform/" in content
    assert "*/*/generated/infra/crash.*.log" in content
    assert "*/*/generated/infra/terraform.auto.tfvars.json" in content


def test_render_normalizes_ssh_public_key_file_path_into_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root, "--infra", "wireguard-jumphost")
    assert create_result.exit_code == 0, create_result.output

    key_path = tmp_path / "id_ed25519.pub"
    key_path.write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    jumphost = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "wireguard-jumphost"
    )
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "region": "eu-north1",
        "subnet_id": "subnet-123",
        "name": "wg-jumphost",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": str(key_path),
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_confirm_render_overwrite",
        lambda _paths, *, force: True,
    )
    monkeypatch.setattr(
        cli_module,
        "render_terraform_artifacts",
        lambda _config, _paths, *, source_profile: [tmp_path / "main.tf"],
    )
    monkeypatch.setattr(cli_module, "_runtime_component_output_values", lambda _config, _paths: {})
    monkeypatch.setattr(
        cli_module,
        "render_flux",
        lambda _config, _paths, *, component_output_values=None: [],
    )
    monkeypatch.setattr(cli_module, "write_inventory", lambda _config, _paths: None)
    monkeypatch.setattr(
        cli_module,
        "_write_generated_runtime_manifest",
        lambda _config, paths, *, source_profile, **kwargs: paths.generated_dir / "manifest.json",
    )
    monkeypatch.setattr(
        cli_module,
        "_try_generate_terraform_lock_file",
        lambda _config, _paths: False,
    )

    result = runner.invoke(app, ["render", "--force", str(config_path)])

    assert result.exit_code == 0, result.output
    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    refreshed_jumphost = next(
        item
        for item in refreshed["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "wireguard-jumphost"
    )
    assert refreshed_jumphost["inputs"]["ssh_public_key"] == _VALID_ED25519_PUBLIC_KEY


def test_load_context_rejects_missing_materialized_shared_admin_ssh_username(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "ssh-jumphost")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    ssh_jumphost = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "ssh-jumphost"
    )
    ssh_jumphost_inputs = ssh_jumphost.get("inputs", {})
    assert isinstance(ssh_jumphost_inputs, dict)
    ssh_jumphost_inputs.pop("ssh_user_name", None)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=(
            r"infra\.components\[id=ssh-jumphost\]\.inputs\.ssh_user_name is required; "
            r"shared-derived defaults must be materialized into config\.yaml"
        ),
    ):
        _load_context(config_path)


def test_first_render_from_create_scaffold_does_not_require_force(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(app, ["render", str(config_path)])

    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "Render will overwrite existing generated artifacts under" not in normalized
    assert "Continue and overwrite the existing generated artifacts?" not in normalized


def test_render_rejects_generated_directory_when_config_yaml_is_required(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    generated_dir = _project_config_path(deployments_root).parent / "generated"
    result = runner.invoke(app, ["render", str(generated_dir)])

    assert result.exit_code == 1, result.output
    normalized = " ".join(result.output.split())
    assert "Expected a project config.yaml file path, but got a directory:" in normalized
    assert "Pass <tenant>/<project>/config.yaml." in normalized


def test_create_force_overwrites_from_scratch_and_reuses_client_info_defaults(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "n8n")
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["client_info"]["nebius"]["region_id"] = "me-west1"
    payload["client_info"]["notifications"]["email_enabled"] = True
    payload["client_info"]["notifications"]["email"] = "ops@example.com"
    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    mk8s_row = next(
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "mk8s"
    )
    inputs = mk8s_row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    inputs["cluster_name"] = "custom-cluster"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    forced = _create_non_interactive(
        deployments_root,
        "--force",
        "--infra",
        "none",
        "--app",
        "none",
    )
    assert forced.exit_code == 0, forced.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "Overwritten project:" in forced.output
    assert refreshed["client_info"]["nebius"]["region_id"] == "me-west1"
    assert refreshed["client_info"]["notifications"]["email_enabled"] is True
    assert refreshed["client_info"]["notifications"]["email"] == "ops@example.com"
    assert refreshed.get("infra", {}).get("components", []) == []
    assert refreshed.get("apps", {}).get("charts", []) == []


def test_create_force_noninteractive_warns_before_overwrite(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    forced = _create_non_interactive(deployments_root, "--force")
    assert forced.exit_code == 0, forced.output
    assert "Existing project detected." in forced.output
    assert "`--force` confirms the overwrite in non-interactive mode." in forced.output
    assert "component list/add/remove" in forced.output


def test_create_force_overwrite_recreates_project_folder_and_removes_stale_files(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    project_dir = _project_dir(deployments_root)
    stale_note = project_dir / "notes.txt"
    stale_manifest = project_dir / "generated" / "nebius-cxcli-manifest.json"
    stale_flux_file = project_dir / "generated" / "flux" / "stale-release.yaml"
    stale_note.write_text("delete me", encoding="utf-8")
    stale_manifest.write_text("{}", encoding="utf-8")
    stale_flux_file.write_text("kind: HelmRelease\n", encoding="utf-8")

    forced = _create_non_interactive(
        deployments_root,
        "--force",
        "--infra",
        "none",
        "--app",
        "none",
    )
    assert forced.exit_code == 0, forced.output

    assert "Overwritten project:" in forced.output
    assert not stale_note.exists()
    assert not stale_manifest.exists()
    assert not stale_flux_file.exists()
    assert (project_dir / "config.yaml").exists()
    assert (project_dir / "generated" / "infra").is_dir()
    assert (project_dir / "generated" / "flux").is_dir()
    assert (project_dir / "generated" / "inventory" / "inventory.md").exists()


def test_create_force_overwrite_does_not_touch_other_projects(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output
    second = _create_named_non_interactive(
        deployments_root,
        client_name="client-b",
        tenant_id="tenant-999",
        project_id="project-999",
    )
    assert second.exit_code == 0, second.output

    other_project_dir = _project_dir(
        deployments_root,
        tenant_id="tenant-999",
        project_id="project-999",
    )
    other_marker = other_project_dir / "keep.txt"
    other_marker.write_text("keep me", encoding="utf-8")

    forced = _create_non_interactive(
        deployments_root,
        "--force",
        "--infra",
        "none",
        "--app",
        "none",
    )
    assert forced.exit_code == 0, forced.output

    assert other_marker.exists()
    assert other_marker.read_text(encoding="utf-8") == "keep me"
    assert (other_project_dir / "config.yaml").exists()


def test_create_interactive_force_existing_project_still_requires_confirmation(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    original = config_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--force",
            "--no-validate-sources",
        ],
        input="\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Existing project detected." in result.output
    assert "Continue and overwrite the existing tenant/project folder from scratch?" in result.output
    assert "(y/n, q=stop wizard) [n]" in result.output
    assert "Existing deployments root detected." not in result.output
    assert "No changes applied." in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_create_uses_defaults_when_no_component_flags(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root)
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
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

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled == {}
    assert apps_enabled["n8n"] is True


def test_create_prunes_redundant_live_chart_default_values_from_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "nebius_cxcli.cli.helm_chart_default_values",
        lambda **_kwargs: {
            "certgen": {
                "job": {
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                    }
                }
            }
        },
    )

    created = _create_non_interactive(deployments_root, "--app", "gateway-helm")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    gateway_row = next(item for item in charts if item.get("id") == "gateway-helm")
    gateway_row["values"] = {
        "certgen": {
            "job": {
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                }
            }
        }
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    refreshed = _create_non_interactive(
        deployments_root,
        "--force",
        "--app",
        "gateway-helm",
    )
    assert refreshed.exit_code == 0, refreshed.output
    assert "Overwritten project:" in refreshed.output

    cleaned = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cleaned_gateway = next(
        item for item in cleaned["apps"]["charts"] if item.get("id") == "gateway-helm"
    )
    assert cleaned_gateway["values"] == {}


def test_create_warns_when_early_exit_leaves_required_fields_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("name", "parent_id", "network_id"),
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--infra",
            "managed-postgresql",
            "--app",
            "none",
            "--no-validate-sources",
        ],
        input="client-a\ntenant-123\nproject-456\n\n\nq\n",
    )

    assert result.exit_code == 0, result.output
    assert "Wizard stopped before all required fields were filled." in result.output
    assert "infra.components[managed-postgresql].inputs.name is required" in result.output
    assert "infra.components[managed-postgresql].inputs.network_id is required" in result.output


def test_create_does_not_warn_when_early_exit_leaves_only_optional_fields(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--infra",
            "none",
            "--app",
            "gateway-helm",
            "--no-validate-sources",
        ],
        input="tenant-123\nproject-456\nclient-a\n\n\nq\n",
    )

    assert result.exit_code == 0, result.output
    assert "Wizard stopped before all required fields were filled." not in result.output
    assert "Wizard optional phases skipped." not in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled["gateway-helm"] is True


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

    config_path = _project_config_path(deployments_root)

    external_sources = tmp_path / "external-component-sources.yaml"
    external_sources.write_text(
        yaml.safe_dump(
            _catalog(
                apps={
                    "nginx": {
                        "source": _portable_chart_source(
                            repo="https://charts.bitnami.com/bitnami",
                            chart="nginx",
                        ),
                        "release": {
                            "namespace": "default",
                            "name": "external-app",
                        },
                        "ui": {
                            "group": "Workloads",
                            "enabled": False,
                        },
                    }
                }
            ),
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

    config_path = _project_config_path(deployments_root)
    _config, _paths = _load_context(config_path)


def test_load_context_rejects_missing_materialized_shared_app_defaults(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "shared": {
                    "admin_ssh": {
                        "user_name": "adminuser",
                    }
                },
                "components": {
                    "infra": {},
                    "apps": {
                        "demo-app": {
                            "source": _portable_chart_source(
                                repo="https://example.invalid/charts",
                                chart="demo-app",
                                version="1.0.0",
                            ),
                            "release": {
                                "namespace": "demo",
                                "name": "demo-app",
                            },
                            "ui": {
                                "group": "Workloads",
                                "enabled": False,
                            },
                            "defaults": {
                                "values.admin.sshUser": "shared.admin_ssh.user_name",
                            },
                        }
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()
    reset_component_entry_cache()

    deployments_root = tmp_path / "deployments"
    config_path = _project_config_path(deployments_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "instance_id": "demo-app",
                    "group": "workloads",
                    "enabled": True,
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo",
                    "release-name": "demo-app",
                    "values": {},
                }
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=(
            r"apps\.charts\[id=demo-app\]\.values\.admin\.sshUser is required; "
            r"shared-derived defaults must be materialized into config\.yaml"
        ),
    ):
        _load_context(config_path)


def test_load_runtime_context_runs_full_non_strict_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = SimpleNamespace(config_path=tmp_path / "config.yaml")
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli_module, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli_module,
        "_validate_active_component_sources",
        lambda config, *, chart_meta_cache=None: captured.update(
            {
                "active_config": config,
                "active_cache": chart_meta_cache,
            }
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_validate_component_dependencies",
        lambda config, *, chart_meta_cache=None: (
            captured.update(
                {
                    "dependency_config": config,
                    "dependency_cache": chart_meta_cache,
                }
            )
            or []
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "rendered_module_sources",
        lambda config, *, source_profile: (
            captured.update(
                {
                    "module_config": config,
                    "source_profile": source_profile,
                }
            )
            or ()
        ),
    )

    config, paths = _load_runtime_context(tmp_path / "config.yaml")

    assert config == "cfg"
    assert paths is fake_paths
    assert captured["active_config"] == "cfg"
    assert captured["dependency_config"] == "cfg"
    assert captured["module_config"] == "cfg"
    assert captured["source_profile"] == SourceProfile.PORTABLE
    assert isinstance(captured["active_cache"], dict)
    assert captured["active_cache"] is captured["dependency_cache"]


def test_load_runtime_context_fails_on_dependency_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = SimpleNamespace(config_path=tmp_path / "config.yaml")

    monkeypatch.setattr(cli_module, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli_module,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr(
        cli_module,
        "_validate_component_dependencies",
        lambda _cfg, *, chart_meta_cache=None: ["apps:n8n requires apps:redis"],
    )
    monkeypatch.setattr(
        cli_module,
        "rendered_module_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("module schema validation should not run after dependency failure")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=r"Runtime validation failed:\n  - apps:n8n requires apps:redis",
    ):
        _load_runtime_context(tmp_path / "config.yaml")


def test_create_does_not_embed_component_sources_block(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert result.exit_code == 0, result.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "component_sources" not in payload


def test_create_seeds_component_source_defaults_into_config(tmp_path: Path) -> None:
    module_dir = tmp_path / "modules" / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    (module_dir / "variables.tf").write_text(
        'variable "cluster_name" { type = string }\nvariable "cpu_nodes_count" { type = number }\n',
        encoding="utf-8",
    )
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            _catalog(
                infra={
                    "demo-module": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                            "local": str(module_dir),
                        },
                        "ui": {
                            "enabled": True,
                        },
                        "defaults": {
                            "inputs.cluster_name": "demo-cluster",
                            "inputs.cpu_nodes_count": 3,
                        },
                    }
                },
                apps={
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                        "ui": {
                            "enabled": True,
                        },
                        "defaults": {
                            "values.replicaCount": 2,
                            "values.image.tag": "stable",
                        },
                    }
                },
            ),
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
            "--source-profile",
            "local",
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

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
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


def test_create_seeds_mk8s_cpu_node_count_into_config(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    mk8s = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "mk8s"
    )
    assert mk8s["inputs"]["cpu_nodes_count"] == 2


def test_create_materializes_shared_admin_ssh_username_into_config(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "wireguard-jumphost",
        "--infra",
        "ssh-jumphost",
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    jump_hosts = {
        item["id"]: item
        for item in payload["infra"]["components"]
        if isinstance(item, dict)
        and item.get("id") in {"wireguard-jumphost", "ssh-jumphost"}
    }

    assert jump_hosts["wireguard-jumphost"]["inputs"]["ssh_user_name"] == "ubuntu"
    assert jump_hosts["ssh-jumphost"]["inputs"]["ssh_user_name"] == "ubuntu"


def test_create_materializes_shared_app_defaults_into_config(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "shared": {
                    "admin_ssh": {
                        "user_name": "adminuser",
                    }
                },
                "components": {
                    "infra": {},
                    "apps": {
                        "demo-app": {
                            "source": _portable_chart_source(
                                repo="https://example.invalid/charts",
                                chart="demo-app",
                                version="1.0.0",
                            ),
                            "release": {
                                "namespace": "demo",
                                "name": "demo-app",
                            },
                            "ui": {
                                "enabled": True,
                            },
                            "defaults": {
                                "values.admin.sshUser": "shared.admin_ssh.user_name",
                            },
                        }
                    },
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
            "--source-profile",
            "local",
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
            "--app",
            "demo-app",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    demo_app = next(
        item
        for item in payload["apps"]["charts"]
        if isinstance(item, dict) and item.get("id") == "demo-app"
    )
    assert demo_app["values"]["admin"]["sshUser"] == "adminuser"


def test_create_seeds_private_shared_admin_ssh_public_key_into_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_dir = tmp_path / "modules" / "demo-jumphost"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variable_names",
        lambda _source: ("parent_id", "ssh_public_key"),
    )

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "shared": {
                    "admin_ssh": {
                        "user_name": "ubuntu",
                        "public_key": _VALID_ED25519_PUBLIC_KEY,
                    }
                },
                "components": {
                    "infra": {
                        "demo-jumphost": {
                            "source": {
                                "portable": (
                                    "git::https://github.com/example/infra.git//modules/demo-jumphost"
                                    "?ref=v1.2.3"
                                ),
                                "local": str(module_dir),
                            },
                            "ui": {
                                "enabled": True,
                            },
                        }
                    },
                    "apps": {},
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
            "--source-profile",
            "local",
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

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    demo_jumphost = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "demo-jumphost"
    )
    assert demo_jumphost["inputs"]["parent_id"] == "project-456"
    assert demo_jumphost["inputs"]["ssh_public_key"] == _VALID_ED25519_PUBLIC_KEY


def test_component_add_seeds_private_shared_admin_ssh_public_key_into_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_dir = tmp_path / "modules" / "demo-jumphost"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variable_names",
        lambda _source: ("parent_id", "ssh_public_key"),
    )

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "shared": {
                    "admin_ssh": {
                        "user_name": "ubuntu",
                        "public_key": _VALID_ED25519_PUBLIC_KEY,
                    }
                },
                "components": {
                    "infra": {
                        "demo-jumphost": {
                            "source": {
                                "portable": (
                                    "git::https://github.com/example/infra.git//modules/demo-jumphost"
                                    "?ref=v1.2.3"
                                ),
                                "local": str(module_dir),
                            },
                            "ui": {
                                "enabled": False,
                            },
                        }
                    },
                    "apps": {},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = runner.invoke(
        app,
        [
            "--component-sources-file",
            str(sources_file),
            "--source-profile",
            "local",
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
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "--component-sources-file",
            str(sources_file),
            "--source-profile",
            "local",
            "component",
            "add",
            str(config_path),
            "--no-validate-sources",
            "demo-jumphost",
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    demo_jumphost = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "demo-jumphost"
    )
    assert demo_jumphost["inputs"]["parent_id"] == "project-456"
    assert demo_jumphost["inputs"]["ssh_public_key"] == _VALID_ED25519_PUBLIC_KEY


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

    config_path = _project_config_path(deployments_root)
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


def test_create_force_treats_components_as_new_selection(tmp_path: Path) -> None:
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

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    mk8s_row = next(
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "mk8s"
    )
    inputs = mk8s_row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    inputs["cluster_name"] = "custom-cluster"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    second = _create_non_interactive(
        deployments_root,
        "--force",
        "--infra",
        "none",
        "--app",
        "n8n",
    )
    assert second.exit_code == 0, second.output
    assert "Overwritten project:" in second.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(refreshed)
    apps_enabled = _apps_enabled_map(refreshed)
    refreshed_components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(refreshed_components, list)
    assert refreshed_components == []
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
        "--force",
        "--infra",
        "mk8s",
        "--app",
        "n8n",
    )
    assert second.exit_code == 0, second.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled["n8n"] is True


def test_component_list_reports_enabled_and_available_components(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "gateway-helm")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(app, ["component", "list", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Enabled infra component instances:" in result.output
    assert "mk8s" in result.output
    assert "Enabled apps component instances:" in result.output
    assert "gateway-helm" in result.output
    assert "Available infra components:" in result.output
    assert "managed-postgresql" in result.output
    assert "Available apps components:" in result.output
    assert "n8n" in result.output
    assert "Available infra components:" in result.output
    assert "mk8s" in result.output


def test_component_add_noninteractive_preserves_existing_values(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = payload.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    mk8s_row = next(
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "mk8s"
    )
    inputs = mk8s_row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    inputs["cluster_name"] = "custom-cluster"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _component_add(config_path, "managed-postgresql", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Added infra components: managed-postgresql" in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    refreshed_components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(refreshed_components, list)
    mk8s_refreshed = next(
        row
        for row in refreshed_components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "mk8s"
    )
    managed_pg = next(
        row
        for row in refreshed_components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "managed-postgresql"
    )
    assert mk8s_refreshed["inputs"]["cluster_name"] == "custom-cluster"
    assert managed_pg["enabled"] is True


def test_component_add_allows_multiple_instances_of_same_component_type(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    first = _component_add(config_path, "managed-postgresql", "--no-interactive")
    second = _component_add(config_path, "managed-postgresql", "--no-interactive")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "managed-postgresql@managed-postgresql-2" in second.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    postgres_rows = [
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "managed-postgresql"
    ]
    assert [row.get("instance_id") for row in postgres_rows] == [
        "managed-postgresql",
        "managed-postgresql-2",
    ]


def test_component_add_interactive_prompts_for_new_component_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("name", "parent_id", "network_id"),
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(
        config_path,
        input_text="managed-postgresql\n\n\ny\ndemo-pg\n",
    )
    assert result.exit_code == 0, result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    managed_pg = next(
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "managed-postgresql"
    )
    assert managed_pg["inputs"]["name"] == "demo-pg"


def test_component_add_noninteractive_adds_app_chart_and_preserves_existing_values(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--app", "gateway-helm")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    gateway_row = next(
        row
        for row in charts
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "gateway-helm"
    )
    gateway_row["namespace"] = "edge"
    gateway_row["release-name"] = "edge-gateway"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _component_add(config_path, "n8n", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Added apps components: n8n" in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    refreshed_charts = refreshed.get("apps", {}).get("charts", [])
    assert isinstance(refreshed_charts, list)
    gateway_refreshed = next(
        row
        for row in refreshed_charts
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "gateway-helm"
    )
    n8n_row = next(
        row
        for row in refreshed_charts
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "n8n"
    )
    assert gateway_refreshed["namespace"] == "edge"
    assert gateway_refreshed["release-name"] == "edge-gateway"
    assert n8n_row["enabled"] is True


def test_component_add_reuses_noninteractive_scope_validation_for_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    captured: list[tuple[str, str, bool]] = []

    def _record_scope_validation(
        *,
        tenant_id: str,
        project_id: str,
        interactive: bool,
        provider_lookup,
    ):
        _ = provider_lookup
        captured.append((tenant_id, project_id, interactive))
        return tenant_id, project_id

    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_tenant_project_ids_or_prompt",
        _record_scope_validation,
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "managed-postgresql", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert captured == [("tenant-123", "project-456", False)]


def test_component_remove_blocks_chart_dependency_breakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--app",
        "gateway-helm",
        "--app",
        "n8n",
    )
    assert created.exit_code == 0, created.output

    def _fake_chart_metadata(
        *,
        chart_name_or_ref: str,
        chart_repo: str,
        chart_version: str,
        cache=None,
    ):
        _ = (chart_repo, chart_version, cache)
        if chart_name_or_ref == "n8n":
            return chart_name_or_ref, None, {"gateway-helm"}, None
        return chart_name_or_ref, None, set(), None

    monkeypatch.setattr("nebius_cxcli.cli._helm_chart_metadata", _fake_chart_metadata)

    config_path = _project_config_path(deployments_root)
    result = _component_remove(config_path, "gateway-helm", "--no-interactive")
    assert result.exit_code == 1, result.output
    assert "app chart dependency requires 'apps:gateway-helm'" in result.output


def test_component_remove_noninteractive_removes_app_chart_when_no_dependency_breakage(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--app", "gateway-helm")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_remove(config_path, "gateway-helm", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Removed apps components: gateway-helm" in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = refreshed.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    assert all(
        not (isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "gateway-helm")
        for row in charts
    )


def test_component_remove_requires_instance_id_when_multiple_instances_match(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    assert _component_add(config_path, "managed-postgresql", "--no-interactive").exit_code == 0
    assert _component_add(config_path, "managed-postgresql", "--no-interactive").exit_code == 0

    ambiguous = _component_remove(config_path, "managed-postgresql", "--no-interactive")
    assert ambiguous.exit_code == 1, ambiguous.output
    normalized_output = " ".join(ambiguous.output.split())
    assert "matches multiple enabled instances" in normalized_output
    assert "Available instances:" in normalized_output

    targeted = _component_remove(config_path, "managed-postgresql-2", "--no-interactive")
    assert targeted.exit_code == 0, targeted.output
    assert "managed-postgresql@managed-postgresql-2" in targeted.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    remaining_instance_ids = [
        str(row.get("instance_id"))
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "managed-postgresql"
    ]
    assert remaining_instance_ids == ["managed-postgresql"]


def test_create_force_does_not_reuse_existing_chart_overrides(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root, "--app", "n8n")
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    original_repo = ""
    for row in charts:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip().lower() != "n8n":
            continue
        original_repo = str(row.get("repo", "")).strip()
        row["repo"] = "https://example.invalid/custom-charts"
        break
    assert original_repo
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    second = _create_non_interactive(deployments_root, "--force", "--app", "n8n")
    assert second.exit_code == 0, second.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    refreshed_charts = refreshed.get("apps", {}).get("charts", [])
    assert isinstance(refreshed_charts, list)
    n8n_row = next(
        row
        for row in refreshed_charts
        if isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "n8n"
    )
    assert str(n8n_row.get("repo", "")).strip() == original_repo


def test_bootstrap_ci_no_auth_writes_workflow_in_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(deployments_root)
    bootstrap = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert bootstrap.exit_code == 0, bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    assert workflow.exists()

    content = workflow.read_text(encoding="utf-8")
    assert "defaults:" in content
    assert "shell: bash" in content
    assert "cache: pip" in content
    assert "workflow_dispatch:" in content
    assert "has_changes" in content
    assert 'NEBIUS_CXCLI_PYTHON_VERSION: "3.12"' in content
    assert "Install kubectl" in content
    assert "https://dl.k8s.io/release/stable.txt" in content
    assert 'echo "${HOME}/.local/bin" >> "$GITHUB_PATH"' in content
    assert "kubectl version --client=true" in content
    assert "azure/setup-kubectl@v4" not in content
    assert 'echo "NEBIUS_SA_ID=${NEBIUS_SA_ID}"' in content
    assert 'echo "NEBIUS_AUTH_PUBLIC_KEY_ID=${NEBIUS_AUTH_PUBLIC_KEY_ID}"' in content
    assert 'echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${KEY_PATH}"' in content
    assert "NEBIUS_DISCOVER_TARGET: customer/deployments-root" in content
    assert "customer/deployments-root" in content
    assert 'if [[ "${GITHUB_EVENT_NAME}" == "workflow_dispatch" ]]; then' in content
    assert (
        'nebius-cxcli discover --all "${{ env.NEBIUS_DISCOVER_TARGET }}" > discover.json' in content
    )
    assert "name: ${{ matrix.github_environment }}" in content
    assert "**/customer/deployments-root/*/*/generated/**" in content
    assert "**/customer/deployments-root/**/config.yaml" not in content
    assert "Validate source contract changes" not in content
    assert 'nebius-cxcli validate-generated --portable "${{ matrix.generated }}"' in content
    assert "Restore generated Terraform inputs" not in content
    assert "generated manifest is missing render.terraform_tfvars" not in content
    assert "print(f\"discovery={json.dumps(payload, separators=(',', ':'))}\")" in content
    assert "Install Nebius CLI" not in content
    assert "curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | bash" not in content
    assert "NEBIUS_API_ENDPOINT" not in content
    assert "Inventory outputs" not in content
    assert "Bootstrap/reconcile Flux" in content
    assert 'nebius-cxcli flux bootstrap "${{ matrix.generated }}"' in content
    assert 'nebius-cxcli deploy "${{ matrix.generated }}"' not in content
    assert "Send inventory email" in content
    assert "vars.SMTP_HOST != ''" not in content
    assert "SMTP_HOST: ${{ vars.SMTP_HOST }}" in content
    assert "SMTP_USERNAME: ${{ secrets.SMTP_USERNAME }}" in content
    assert (
        f"NEBIUS_CXCLI_REF: ${{{{ vars.NEBIUS_CXCLI_REF || '{cli_module.default_cli_ref()}' }}}}"
        in content
    )


def test_bootstrap_ci_no_auth_is_idempotent_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(deployments_root)
    first = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert first.exit_code == 0, first.output
    assert "Created:" in first.output

    second = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert second.exit_code == 0, second.output
    assert "Workflow already aligned:" in second.output
    assert "Skipped Nebius CI auth bootstrap/secrets sync." in second.output


def test_bootstrap_ci_no_auth_reconciles_workflow_drift_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(deployments_root)
    first = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert first.exit_code == 0, first.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    workflow.write_text("name: Drifted Customer Workflow\n", encoding="utf-8")

    second = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert second.exit_code == 0, second.output
    assert "Updated:" in second.output
    content = workflow.read_text(encoding="utf-8")
    assert "name: Nebius Deployments" in content
    assert "name: Drifted Customer Workflow" not in content


def test_bootstrap_ci_recreates_deployments_gitignore_in_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    gitignore_path = deployments_root / ".gitignore"
    gitignore_path.unlink()
    assert not gitignore_path.exists()

    config_path = _project_config_path(deployments_root)
    bootstrap = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert bootstrap.exit_code == 0, bootstrap.output
    assert "Ensured deployments .gitignore:" in bootstrap.output
    assert gitignore_path.exists()

    content = gitignore_path.read_text(encoding="utf-8")
    assert "*/*/generated/infra/.terraform/" in content
    assert "*/*/generated/infra/crash.*.log" in content
    assert "*/*/generated/infra/terraform.auto.tfvars.json" in content


def test_bootstrap_ci_cli_ref_overrides_generated_workflow_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(deployments_root)
    bootstrap = runner.invoke(
        app,
        [
            "bootstrap-ci",
            str(config_path),
            "--no-auth-bootstrap",
            "--cli-ref",
            "feature/test-portable-catalog",
        ],
    )
    assert bootstrap.exit_code == 0, bootstrap.output
    assert "Workflow CLI ref: feature/test-portable-catalog" in bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    content = workflow.read_text(encoding="utf-8")
    assert (
        "NEBIUS_CXCLI_REF: ${{ vars.NEBIUS_CXCLI_REF || 'feature/test-portable-catalog' }}"
        in content
    )


def test_bootstrap_ci_no_auth_uses_release_tag_default_for_stable_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    monkeypatch.setattr(templates_module, "__version__", "1.2.3")
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(deployments_root)
    bootstrap = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert bootstrap.exit_code == 0, bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    content = workflow.read_text(encoding="utf-8")
    assert "NEBIUS_CXCLI_REF: ${{ vars.NEBIUS_CXCLI_REF || 'nebius-cxcli-v1.2.3' }}" in content


def test_bootstrap_ci_auth_bootstrap_fails_before_writing_workflow_when_repo_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    config_path = _project_config_path(deployments_root)
    bootstrap = runner.invoke(app, ["bootstrap-ci", str(config_path)])
    assert bootstrap.exit_code == 1, bootstrap.output
    assert "Failed to resolve git origin remote under" in bootstrap.output
    assert "Set --github-repo" in bootstrap.output
    assert "owner/repo" in bootstrap.output
    assert "explicitly." in bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    assert not workflow.exists()


def test_bootstrap_ci_auth_bootstrap_accepts_explicit_github_repo_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    _mock_bootstrap_ci_github_sync(monkeypatch)
    monkeypatch.setattr(cli_module, "_auto_bootstrap_ci_auth_and_secrets", lambda **_kwargs: None)

    config_path = _project_config_path(deployments_root)
    bootstrap = runner.invoke(
        app,
        [
            "bootstrap-ci",
            str(config_path),
            "--github-repo",
            "owner/repo",
        ],
    )
    assert bootstrap.exit_code == 0, bootstrap.output
    assert "GitHub repository: owner/repo" in bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    assert workflow.exists()


def test_bootstrap_ci_accepts_github_flags_when_no_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(deployments_root)
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
    assert result.exit_code == 0, result.output
    assert "GitHub repository: owner/repo" in result.output


def test_bootstrap_ci_no_auth_requires_github_token_for_email_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    config_path = _project_config_path(deployments_root)
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
    assert "GitHub bootstrap reconciliation requires a GitHub token." in result.output


def test_bootstrap_ci_auth_bootstrap_syncs_local_email_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    _mock_bootstrap_ci_github_sync(monkeypatch, github_token="token-123")
    monkeypatch.setattr(cli_module, "_auto_bootstrap_ci_auth_and_secrets", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "_load_local_email_settings",
        lambda *args, **kwargs: EmailSettings(
            host="smtp.example.com",
            port=587,
            starttls=True,
            from_addr="deployments@example.com",
            username="mailer",
            password="secret",
        ),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli_module,
        "_sync_github_email_settings",
        lambda *, repo_slug, github_environment, github_token, settings: (
            captured.update(
                {
                    "repo_slug": repo_slug,
                    "github_environment": github_environment,
                    "github_token": github_token,
                    "settings": settings,
                }
            )
            or cli_module.GitHubEmailSyncResult(
                updated_vars=["SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS", "SMTP_FROM"],
                updated_secrets=["SMTP_USERNAME", "SMTP_PASSWORD"],
                removed_vars=[],
                removed_secrets=[],
            )
        ),
    )

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "bootstrap-ci",
            str(config_path),
            "--github-repo",
            "owner/repo",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Email settings synced: 4 environment variable(s), 2 secret(s)" in result.output
    assert captured["repo_slug"] == "owner/repo"
    assert captured["github_environment"] == "client-a-project-456"
    assert captured["github_token"] == "token-123"
    assert captured["settings"] == EmailSettings(
        host="smtp.example.com",
        port=587,
        starttls=True,
        from_addr="deployments@example.com",
        username="mailer",
        password="secret",
    )


def test_bootstrap_ci_no_auth_reports_skipped_email_sync_when_local_email_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(
        monkeypatch,
        email_sync_result=cli_module.GitHubEmailSyncResult(
            updated_vars=["SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS"],
            updated_secrets=[],
            removed_vars=[],
            removed_secrets=[],
        ),
    )

    monkeypatch.setattr(
        cli_module,
        "_load_local_email_settings",
        lambda *args, **kwargs: EmailSettings(host="smtp.example.com", port=587),
    )

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])

    assert result.exit_code == 0, result.output
    assert "Email settings synced: 3 environment variable(s), 0 secret(s)" in result.output
    assert "Skipped Nebius CI auth bootstrap/secrets sync." in result.output


def test_bootstrap_ci_clears_github_email_settings_when_local_email_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "customer" / "deployments-root"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_result = _create_non_interactive(deployments_root)
    assert create_result.exit_code == 0, create_result.output

    _mock_bootstrap_ci_github_sync(
        monkeypatch,
        email_sync_result=cli_module.GitHubEmailSyncResult(
            updated_vars=[],
            updated_secrets=[],
            removed_vars=["SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS", "SMTP_FROM"],
            removed_secrets=["SMTP_USERNAME", "SMTP_PASSWORD"],
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_load_local_email_settings",
        lambda *args, **kwargs: EmailSettings(),
    )

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])

    assert result.exit_code == 0, result.output
    assert "cleared GitHub email settings: 4 environment variable(s), 2 secret(s)" in " ".join(
        result.output.split()
    )


def test_sync_github_email_settings_reconciles_upserts_and_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    deleted_vars: list[str] = []
    deleted_secrets: list[str] = []

    monkeypatch.setattr(
        cli_module,
        "ensure_github_environment",
        lambda *, repo_slug, token, environment_name: captured.update(
            {
                "ensure": (repo_slug, token, environment_name),
            }
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "upsert_environment_variables",
        lambda *, repo_slug, token, environment_name, variables: (
            captured.update({"vars": variables}) or list(variables)
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "upsert_environment_secrets",
        lambda *, repo_slug, token, environment_name, secrets: (
            captured.update({"secrets": secrets}) or list(secrets)
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "delete_environment_variable",
        lambda *, repo_slug, token, environment_name, variable_name: (
            deleted_vars.append(variable_name) or True
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "delete_environment_secret",
        lambda *, repo_slug, token, environment_name, secret_name: (
            deleted_secrets.append(secret_name) or True
        ),
    )

    result = cli_module._sync_github_email_settings(
        repo_slug="owner/repo",
        github_environment="client-a-project-456",
        github_token="gh-token",
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            starttls=True,
            from_addr="",
            username="mailer",
            password="secret",
        ),
    )

    assert result == cli_module.GitHubEmailSyncResult(
        updated_vars=["SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS"],
        updated_secrets=["SMTP_USERNAME", "SMTP_PASSWORD"],
        removed_vars=["SMTP_FROM"],
        removed_secrets=[],
    )
    assert captured["vars"] == {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_STARTTLS": "true",
    }
    assert captured["secrets"] == {
        "SMTP_USERNAME": "mailer",
        "SMTP_PASSWORD": "secret",
    }
    assert deleted_vars == ["SMTP_FROM"]
    assert deleted_secrets == []


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
    assert (
        "--github-repo and --github-token-env are valid only with --bootstrap-ci" in result.output
    )


def test_discover_accepts_non_git_directory(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    config_path = deployments_root / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(deployments_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
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


def test_discover_in_git_repo_outputs_repo_relative_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    config_path = deployments_root / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(deployments_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
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


def test_discover_accepts_generated_subdirectory_scope(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployments"
    generated_dir = deployments_root / "tenant-123" / "project-456" / "generated"
    config_path = generated_dir.parent / "config.yaml"
    generated_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(generated_dir), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
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
