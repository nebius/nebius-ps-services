from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli_module
import nebius_cxcli.component_sources as component_sources
import nebius_cxcli.deploy_targets as deploy_targets_module
import nebius_cxcli.templates as templates_module
from nebius_cxcli.cli import _load_context, _load_runtime_context, app
from nebius_cxcli.component_instances import component_instance_id
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import ComponentEntry, component_entries, reset_component_entry_cache
from nebius_cxcli.email_settings import EmailSettings
from nebius_cxcli.quota_checks import QuotaCheck, QuotaReport
from nebius_cxcli.runtime_config import to_plain_data
from nebius_cxcli.runtime_validation import validate_dynamic_payload_structure
from nebius_cxcli.wizard_profiles import BUILTIN_WIZARD_PROFILES

runner = CliRunner()

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
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


def _tenant_folder_name(tenant_id: str = "tenant-123") -> str:
    folder_by_tenant_id = {
        "tenant-123": "tenant-acme-labs",
        "tenant-999": "tenant-platform-ops",
    }
    return folder_by_tenant_id[tenant_id]


def _project_folder_name(project_id: str = "project-456") -> str:
    folder_by_project_id = {
        "project-456": "gpu-training-prod",
        "project-789": "model-serving-dev",
        "project-999": "quota-sandbox",
    }
    return folder_by_project_id[project_id]


def test_apply_vpc_ref_overrides_materializes_row_bindings() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "network": {"name": "worker-network"},
                        "subnets": {
                            "worker": {
                                "name": "worker-subnet",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/16"],
                            }
                        },
                    },
                },
                {
                    "id": "vm",
                    "instance_id": "worker",
                    "enabled": True,
                    "inputs": {},
                },
            ]
        }
    }

    cli_module._apply_vpc_ref_overrides(
        payload=payload,
        selected_infra={"worker"},
        network_refs=["infra:vm@worker=vpc@worker-vpc.network_id"],
        subnet_refs=["infra:vm@worker=vpc@worker-vpc.subnets.worker.id"],
    )

    vm = payload["infra"]["components"][1]
    assert vm["bindings"] == {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "worker-vpc",
            "source_output": "network_id",
        },
        "inputs.subnet_id": {
            "source_component": "vpc",
            "source_instance": "worker-vpc",
            "source_output": "subnets",
            "key": "worker",
            "attribute": "id",
        },
    }


def test_network_command_is_not_available() -> None:
    result = runner.invoke(app, ["network", "create"])

    assert result.exit_code != 0
    assert "No such command" in result.output


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", raising=False)
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_tenant_project_ids_or_prompt",
        lambda **kwargs: (kwargs["tenant_id"], kwargs["project_id"]),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._resolve_create_target_folders",
        lambda **kwargs: (
            _tenant_folder_name(kwargs["tenant_id"]),
            _project_folder_name(kwargs["project_id"]),
        ),
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
    monkeypatch.setattr("nebius_cxcli.cli.validate_vpc_networking_preflight", lambda _cfg: None)
    monkeypatch.setattr("nebius_cxcli.infra_render.module_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli._try_generate_terraform_lock_file", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    original_dynamic_choices = cli_module._resolve_dynamic_field_choices

    def _fake_dynamic_provider_choices(**kwargs):  # type: ignore[no-untyped-def]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".network_id"):
            return [cli_module.OptionChoice(value="vpcnetwork-123", label="default network")]
        if full_path_label.endswith(".subnet_id"):
            return [cli_module.OptionChoice(value="vpcsubnet-123", label="default subnet")]
        return original_dynamic_choices(**kwargs)

    monkeypatch.setattr(
        "nebius_cxcli.cli._resolve_dynamic_field_choices",
        _fake_dynamic_provider_choices,
    )
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
    yield
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
    return _project_dir(deployments_root) / "config.yaml"


def _normalized_cli_output(text: str) -> str:
    without_soft_hyphen_wraps = re.sub(r"-\s*\n\s*", "-", text)
    return " ".join(without_soft_hyphen_wraps.split())


def _project_dir(
    deployments_root: Path,
    *,
    tenant_id: str = "tenant-123",
    project_id: str = "project-456",
) -> Path:
    return deployments_root / _tenant_folder_name(tenant_id) / _project_folder_name(project_id)


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


def _write_compute_boot_disk_sources_file(path: Path, *, module_dir: Path) -> None:
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    (module_dir / "variables.tf").write_text(
        'variable "cluster" { type = any }\n'
        'variable "node_groups" { type = any }\n'
        'variable "node_group_defaults" { type = any }\n',
        encoding="utf-8",
    )
    boot_disk_defaults = {
        "disk_types": [
            {
                "value": "NETWORK_SSD",
                "allocation_unit_gib": 1,
                "label": "NETWORK_SSD",
            },
            {
                "value": "NETWORK_SSD_NON_REPLICATED",
                "allocation_unit_gib": 93,
                "label": "NETWORK_SSD_NON_REPLICATED",
            },
        ],
        "cpu": {
            "default_type": "NETWORK_SSD",
            "rules": [
                {
                    "max_vcpu": 8,
                    "max_memory_gib": 32,
                    "size_gib": 64,
                },
                {
                    "max_vcpu": 32,
                    "max_memory_gib": 128,
                    "size_gib": 93,
                },
                {
                    "max_vcpu": 64,
                    "max_memory_gib": 256,
                    "size_gib": 128,
                },
                {
                    "min_vcpu": 65,
                    "size_gib": 186,
                },
            ],
        },
    }
    path.write_text(
        yaml.safe_dump(
            _catalog(
                infra={
                    "mk8s": {
                        "source": {
                            "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                            "local": str(module_dir),
                        },
                        "ui": {
                            "enabled": True,
                        },
                        "defaults": {
                            "inputs.cluster.cluster_name": "mk8s",
                            "inputs.node_group_defaults.cpu.platform": "cpu-any",
                            "inputs.node_group_defaults.cpu.preset": "32vcpu-128gb",
                            "inputs.node_groups.cpu.node_count": 2,
                            "inputs.node_groups.cpu.platform": "cpu-any",
                            "inputs.node_groups.cpu.preset": "32vcpu-128gb",
                            "inputs.node_groups.cpu.gpu": False,
                        },
                    }
                }
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    path.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(
            {
                "compute": {
                    "boot_disk_defaults": boot_disk_defaults,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
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
            *extra,
            "--config",
            str(config_path),
            "--no-validate-sources",
        ],
        input=input_text,
    )


def _external_mk8s_target_row(instance_id: str = "external-cluster") -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "kind": "external-mk8s",
        "ownership": "external",
        "access": "external",
        "kube_context": f"{instance_id}-context",
        "inventory": {
            "node_groups": {
                "cpu-pool": {"gpu": False, "node_count": 2},
                "gpu-pool": {"gpu": True, "node_count": 1},
            }
        },
        "soperator_onboarding": {
            "accepted": True,
            "analysis_fingerprint": "",
            "state": "vanilla-mk8s",
            "actions": ["install-soperator"],
            "storage_mode": "adopt-existing-storage",
        },
    }


def _component_remove(config_path: Path, *extra: str, input_text: str | None = None):
    return runner.invoke(
        app,
        [
            "component",
            "remove",
            *extra,
            "--config",
            str(config_path),
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


def test_create_does_not_preflight_unselected_app_source_tools_before_identity_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli_module, "_validate_component_sources_or_raise", lambda **_kwargs: None)

    result = runner.invoke(app, ["create", str(deployments_root)], input="\n")

    assert result.exit_code != 0
    assert "helm missing from PATH" not in result.output
    assert "Tenant ID" in result.output


def test_create_creates_missing_deployments_root(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployment-example"

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )

    assert result.exit_code == 0, result.output
    assert deployments_root.is_dir()
    assert _project_config_path(deployments_root).exists()
    assert "Target directory does not exist" not in result.output


def test_create_reprompts_invalid_interactive_client_name(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--infra",
            "none",
            "--app",
            "none",
            "--no-validate-sources",
        ],
        input="Test\nclient-a\n\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "client_info.client_name must use lowercase" in result.output
    assert "letters, digits, and hyphens" in result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    assert payload["client_info"]["client_name"] == "client-a"


def test_create_guided_prefilled_infra_example_skips_source_and_post_write_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    def fail_source_validation(**_kwargs: object) -> None:
        raise AssertionError("source validation should be skipped")

    def fail_runtime_validation(**_kwargs: object) -> None:
        raise AssertionError("post-write config validation should be skipped")

    def capture_field_wizard(**kwargs: object) -> tuple[str, bool]:
        captured["selected_infra"] = set(kwargs["selected_infra"])  # type: ignore[arg-type]
        captured["selected_apps"] = set(kwargs["selected_apps"])  # type: ignore[arg-type]
        return str(kwargs["config_yaml"]), True

    def capture_quota_check(_config: object, *, phase: str, all_regions: bool = False):
        captured["quota_phase"] = phase
        captured["quota_all_regions"] = all_regions
        return _empty_quota_report()

    monkeypatch.setattr(
        cli_module,
        "_validate_component_sources_or_raise",
        fail_source_validation,
    )
    monkeypatch.setattr(cli_module, "_run_runtime_validation", fail_runtime_validation)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", capture_field_wizard)
    monkeypatch.setattr(cli_module, "_warn_on_live_quota_issues", capture_quota_check)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--infra",
            "mk8s,vm,wireguard-gw,ssh-jumphost",
            "--no-validate-sources",
            "--no-validate-config",
        ],
        input="\n\n\nnone\n",
    )

    assert result.exit_code == 0, result.output
    assert captured["selected_infra"] == {
        "mk8s",
        "vm",
        "wireguard-gw",
        "ssh-jumphost",
    }
    assert captured["selected_apps"] == set()
    assert captured["quota_phase"] == "create"
    assert captured["quota_all_regions"] is False
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    assert infra_enabled == {
        "mk8s": True,
        "vm": True,
        "wireguard-gw": True,
        "ssh-jumphost": True,
    }


def test_create_interactive_skips_app_selection_without_selected_mk8s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    resolved_scopes: list[str] = []
    captured: dict[str, object] = {}

    def capture_component_selection(**kwargs: object) -> set[str]:
        scope = str(kwargs["scope"])
        resolved_scopes.append(scope)
        if scope == "infra":
            return {"vpc"}
        raise AssertionError("app selection should be skipped without a selected MK8s target")

    def capture_field_wizard(**kwargs: object) -> tuple[str, bool]:
        captured["selected_infra"] = set(kwargs["selected_infra"])  # type: ignore[arg-type]
        captured["selected_apps"] = set(kwargs["selected_apps"])  # type: ignore[arg-type]
        payload = yaml.safe_load(str(kwargs["config_yaml"]))
        vpc = next(row for row in payload["infra"]["components"] if row.get("id") == "vpc")
        vpc.setdefault("inputs", {}).setdefault("network", {})["ipv4_private_cidrs"] = [
            "172.16.0.0/12"
        ]
        return yaml.safe_dump(payload, sort_keys=False), True

    monkeypatch.setattr(cli_module, "_resolve_component_ids", capture_component_selection)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", capture_field_wizard)
    monkeypatch.setattr(
        cli_module,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli_module._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(
        cli_module,
        "_warn_on_live_quota_issues",
        lambda *_args, **_kwargs: _empty_quota_report(),
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--no-validate-sources",
            "--no-validate-config",
        ],
    )

    assert result.exit_code == 0, result.output
    assert resolved_scopes == ["infra"]
    assert captured["selected_infra"] == {"vpc"}
    assert captured["selected_apps"] == set()
    assert "Skipping app chart selection because no MK8s target was selected" in result.output


def test_create_interactive_explicit_app_without_mk8s_target_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        cli_module,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli_module._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(
        cli_module,
        "_run_component_field_wizard",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("field wizard should not run for invalid app selection")
        ),
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "none",
            "--app",
            "n8n",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "Apps are Helm charts and require an enabled MK8s target" in result.output
    assert "Skipping app chart selection because no MK8s target was selected" not in result.output


def test_create_guided_prefilled_infra_and_apps_example_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    def capture_field_wizard(**kwargs: object) -> tuple[str, bool]:
        captured["selected_infra"] = set(kwargs["selected_infra"])  # type: ignore[arg-type]
        captured["selected_apps"] = set(kwargs["selected_apps"])  # type: ignore[arg-type]
        return str(kwargs["config_yaml"]), True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", capture_field_wizard)
    monkeypatch.setattr(
        cli_module,
        "_warn_on_live_quota_issues",
        lambda *_args, **_kwargs: _empty_quota_report(),
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--infra",
            "mk8s,vm",
            "--infra",
            "wireguard-gw,ssh-jumphost",
            "--app",
            "n8n,gateway-helm",
            "--app",
            "cert-manager",
            "--no-validate-sources",
            "--no-validate-config",
        ],
        input="\n\n\n",
    )

    assert result.exit_code == 0, result.output
    assert captured["selected_infra"] == {
        "mk8s",
        "vm",
        "wireguard-gw",
        "ssh-jumphost",
    }
    assert captured["selected_apps"] == {"n8n", "gateway-helm", "cert-manager"}
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    for component_id in ("mk8s", "vm", "wireguard-gw", "ssh-jumphost"):
        assert infra_enabled[component_id] is True
    for app_id in ("n8n", "gateway-helm", "cert-manager"):
        assert apps_enabled[app_id] is True


def test_create_noninteractive_auto_selects_single_live_vpc_choice(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "vm",
        "--app",
        "none",
        "--no-validate-config",
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    vm = next(row for row in payload["infra"]["components"] if row.get("id") == "vm")
    assert vm["inputs"]["network_id"] == "vpcnetwork-123"
    assert vm["inputs"]["subnet_id"] == "vpcsubnet-123"


def test_create_interactive_vpc_can_skip_live_network_and_create_network_without_subnets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    original_dynamic_choices = cli_module._resolve_dynamic_field_choices

    def _vpc_existing_network_choices(**kwargs):  # type: ignore[no-untyped-def]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".inputs.network.existing_id"):
            return [cli_module.OptionChoice(value="vpcnetwork-live", label="default network")]
        return original_dynamic_choices(**kwargs)

    monkeypatch.setattr(
        cli_module,
        "_resolve_dynamic_field_choices",
        _vpc_existing_network_choices,
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--infra",
            "vpc",
            "--app",
            "none",
            "--no-validate-sources",
            "--no-validate-config",
        ],
        input="y\ny\n\nmynetwork\n1\nfalse\n",
    )

    assert result.exit_code == 0, result.output
    assert "planned:" not in result.output
    assert "inputs.subnets.<new>.name" not in result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    vpc = next(row for row in payload["infra"]["components"] if row.get("id") == "vpc")
    assert vpc["inputs"]["network"] == {
        "name": "mynetwork",
        "ipv4_private_cidrs": ["10.8.0.0/13"],
    }
    assert "subnets" not in vpc["inputs"]


def test_create_interactive_vpc_selects_suggested_custom_network_cidr_and_subnet_cidr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    original_dynamic_choices = cli_module._resolve_dynamic_field_choices

    def _vpc_existing_network_choices(**kwargs):  # type: ignore[no-untyped-def]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".inputs.network.existing_id"):
            return [cli_module.OptionChoice(value="vpcnetwork-live", label="default network")]
        return original_dynamic_choices(**kwargs)

    monkeypatch.setattr(
        cli_module,
        "_resolve_dynamic_field_choices",
        _vpc_existing_network_choices,
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.com",
            "--infra",
            "vpc",
            "--app",
            "none",
            "--no-validate-sources",
            "--no-validate-config",
        ],
        input="y\ny\n\nmynetwork\n1\ntrue\nworkloads\n1\nfalse\n",
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    vpc = next(row for row in payload["infra"]["components"] if row.get("id") == "vpc")
    assert vpc["inputs"]["network"] == {
        "name": "mynetwork",
        "ipv4_private_cidrs": ["10.8.0.0/13"],
    }
    assert vpc["inputs"]["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.8.0.0/16"],
        }
    }


def test_noninteractive_auto_selects_single_planned_vpc_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-456",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker-subnet",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/16"],
                            }
                        },
                    },
                },
                {
                    "id": "vm",
                    "instance_id": "worker",
                    "enabled": True,
                    "inputs": {"parent_id": "project-456"},
                },
            ]
        }
    }

    def _single_planned_vpc_choice(**kwargs):  # type: ignore[no-untyped-def]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".network_id"):
            return [
                cli_module.OptionChoice(
                    value="planned:vpc@worker-vpc.network_id",
                    label="planned network",
                )
            ]
        if full_path_label.endswith(".subnet_id"):
            return [
                cli_module.OptionChoice(
                    value="planned:vpc@worker-vpc.subnets.worker.id",
                    label="planned subnet",
                )
            ]
        return []

    monkeypatch.setattr(
        cli_module,
        "_resolve_dynamic_field_choices",
        _single_planned_vpc_choice,
    )

    cli_module._materialize_singleton_provider_defaults(
        payload=payload,
        selected_infra={"worker"},
        infra_entries=component_entries("infra"),
        provider_lookup=SimpleNamespace(),
    )
    cli_module._materialize_planned_vpc_binding_tokens(payload)

    vm = payload["infra"]["components"][1]
    assert vm["inputs"] == {"parent_id": "project-456"}
    assert vm["bindings"] == {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "worker-vpc",
            "source_output": "network_id",
        },
        "inputs.subnet_id": {
            "source_component": "vpc",
            "source_instance": "worker-vpc",
            "source_output": "subnets",
            "key": "worker",
            "attribute": "id",
        },
    }


def test_component_field_wizard_configures_planned_vpc_before_mk8s_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {"cluster": {}},
                },
                {
                    "id": "vpc",
                    "instance_id": "vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                },
            ]
        },
        "apps": {"charts": []},
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components[].inputs",
        description="Managed Kubernetes",
        wizard_fields={
            "inputs.cluster.network_id": {
                "options": {"from": "project_networks", "auto_select_single": True},
                "required": True,
                "type_hint": "string",
            },
            "inputs.cluster.subnet_id": {
                "options": {
                    "from": "project_subnets",
                    "args": {"network_id_path": "inputs.cluster.network_id"},
                    "auto_select_single": True,
                },
                "required": True,
                "type_hint": "string",
            },
        },
    )
    vpc_entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    phase_prompts: list[str] = []

    class _ProviderLookup:
        def resolve(self, **_kwargs):  # type: ignore[no-untyped-def]
            return []

        def last_error(self) -> None:
            return None

    def _dynamic_choices(**kwargs):  # type: ignore[no-untyped-def]
        current_payload = kwargs["payload"]
        entry = kwargs["entry"]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".inputs.cluster.network_id"):
            return cli_module._combined_vpc_choices(
                payload=current_payload,
                entry=entry,
                full_path_label=full_path_label,
                provider="project_networks",
                args={},
                live_choices=[],
            )
        if full_path_label.endswith(".inputs.cluster.subnet_id"):
            return cli_module._combined_vpc_choices(
                payload=current_payload,
                entry=entry,
                full_path_label=full_path_label,
                provider="project_subnets",
                args={
                    "network_id_path": full_path_label.removesuffix("subnet_id")
                    + "network_id"
                },
                live_choices=[],
            )
        return []

    def _phase_decision(prompt_label: str, **_kwargs) -> cli_module._WizardPhaseDecision:
        phase_prompts.append(prompt_label)
        return cli_module._WizardPhaseDecision(proceed=True)

    def _answer_prompt(text: str, default=None, **_kwargs):  # type: ignore[no-untyped-def]
        if "inputs.network.name" in text:
            return "mynetwork"
        if "inputs.network.ipv4_private_cidrs" in text:
            return "1"
        if "inputs.subnets.add_another" in text:
            return "false"
        if "inputs.subnets.add" in text:
            return "true"
        if "inputs.subnets.<new>.name" in text:
            return "workloads"
        if "inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return "1"
        return "" if default is None else str(default)

    monkeypatch.setattr(cli_module, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli_module, "_resolve_dynamic_field_choices", _dynamic_choices)
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", _phase_decision)
    monkeypatch.setattr(cli_module.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli_module._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"mk8s", "vpc"},
        selected_apps=set(),
        infra_entries=(mk8s_entry, vpc_entry),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    assert completed is True
    assert phase_prompts[:2] == [
        "Configure 'vpc' component fields now?",
        "Configure 'mk8s' component fields now?",
    ]
    updated_payload = yaml.safe_load(updated_yaml)
    cli_module._materialize_planned_vpc_binding_tokens(updated_payload)
    mk8s = updated_payload["infra"]["components"][0]
    vpc = updated_payload["infra"]["components"][1]
    assert vpc["inputs"]["network"] == {
        "name": "mynetwork",
        "ipv4_private_cidrs": ["10.8.0.0/13"],
    }
    assert vpc["inputs"]["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.8.0.0/16"],
        }
    }
    assert mk8s.get("inputs", {}).get("cluster", {}) == {}
    assert mk8s["bindings"] == {
        "inputs.cluster.network_id": {
            "source_component": "vpc",
            "source_instance": "vpc",
            "source_output": "network_id",
        },
        "inputs.cluster.subnet_id": {
            "source_component": "vpc",
            "source_instance": "vpc",
            "source_output": "subnets",
            "key": "workloads",
            "attribute": "id",
        },
    }


def test_create_noninteractive_requires_scoped_vpc_flags_for_multiple_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    def _multiple_vpc_choices(**kwargs):  # type: ignore[no-untyped-def]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".network_id"):
            return [
                cli_module.OptionChoice(value="vpcnetwork-a", label="network a"),
                cli_module.OptionChoice(value="vpcnetwork-b", label="network b"),
            ]
        return []

    monkeypatch.setattr(
        "nebius_cxcli.cli._resolve_dynamic_field_choices",
        _multiple_vpc_choices,
    )

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "vm",
        "--app",
        "none",
        "--no-validate-config",
    )

    assert result.exit_code == 1
    assert "multiple live choices exist" in result.output
    assert "--network-id" in result.output


def test_create_accepts_bare_vpc_flags_for_single_subnet_attached_component(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "vm",
        "--app",
        "none",
        "--network-id",
        "vpcnetwork-explicit",
        "--subnet-id",
        "vpcsubnet-explicit",
        "--no-validate-config",
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    vm = next(row for row in payload["infra"]["components"] if row.get("id") == "vm")
    assert vm["inputs"]["network_id"] == "vpcnetwork-explicit"
    assert vm["inputs"]["subnet_id"] == "vpcsubnet-explicit"


def test_create_accepts_scoped_vpc_flags_for_multiple_components(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "vm,ssh-jumphost",
        "--app",
        "none",
        "--network-id",
        "infra:vm=vpcnetwork-vm",
        "--subnet-id",
        "infra:vm=vpcsubnet-vm",
        "--network-id",
        "infra:ssh-jumphost=vpcnetwork-ssh",
        "--subnet-id",
        "infra:ssh-jumphost=vpcsubnet-ssh",
        "--no-validate-config",
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in payload["infra"]["components"] if row.get("enabled")}
    assert by_id["vm"]["inputs"]["network_id"] == "vpcnetwork-vm"
    assert by_id["vm"]["inputs"]["subnet_id"] == "vpcsubnet-vm"
    assert by_id["ssh-jumphost"]["inputs"]["network_id"] == "vpcnetwork-ssh"
    assert by_id["ssh-jumphost"]["inputs"]["subnet_id"] == "vpcsubnet-ssh"


def test_wizard_field_prompt_describes_q_as_previous_field() -> None:
    rendered = cli_module._wizard_field_prompt_suffix("infra.components[0].inputs.name")

    assert "enter q to go back" in rendered
    assert "qq quits wizard" in rendered


def test_component_field_wizard_reports_skipped_component_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm_entry = next(entry for entry in cli_module.component_entries("infra") if entry.id == "vm")
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }
    skipped_components: set[tuple[str, str, str]] = set()

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: False)

    _updated_yaml, completed = cli_module._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"vm"},
        selected_apps=set(),
        infra_entries=(vm_entry,),
        app_entries=(),
        skipped_components=skipped_components,
    )

    assert completed is True
    assert skipped_components == {("infra", "vm", "vm")}


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


def _patch_late_mk8s_gpu_enable_wizard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    infiniband_fabric: str = "",
    cluster_name: str = "",
) -> None:
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)

    def _fake_run_component_field_wizard(
        *,
        config_yaml: str,
        selected_infra: set[str],
        selected_apps: set[str],
        infra_entries,
        app_entries,
        provider_lookup=None,
        **_kwargs,
    ) -> tuple[str, bool]:
        _ = selected_infra, selected_apps, infra_entries, app_entries, provider_lookup
        payload = yaml.safe_load(config_yaml) or {}
        components = payload.get("infra", {}).get("components", [])
        assert isinstance(components, list)
        mk8s_row = next(
            item
            for item in components
            if isinstance(item, dict) and str(item.get("id", "")).strip().lower() == "mk8s"
        )
        inputs = mk8s_row.setdefault("inputs", {})
        assert isinstance(inputs, dict)
        if cluster_name:
            cluster = inputs.setdefault("cluster", {})
            assert isinstance(cluster, dict)
            cluster["cluster_name"] = cluster_name
        inputs["node_group_defaults"] = {
            "gpu": {
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "gpu_stack_source": "nebius_image",
            }
        }
        gpu_group = {
            "node_count": 1,
            "gpu": True,
            "platform": "gpu-h100-sxm",
            "preset": "8gpu-128vcpu-1600gb",
            "gpu_stack_source": "nebius_image",
        }
        if infiniband_fabric:
            inputs["gpu_clusters"] = {"workers": {"infiniband_fabric": infiniband_fabric}}
            gpu_group["gpu_cluster_key"] = "workers"
        else:
            inputs.pop("gpu_clusters", None)
        inputs["node_groups"] = {"worker": gpu_group}
        return yaml.safe_dump(payload, sort_keys=False), True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _fake_run_component_field_wizard)


def _patch_late_observability_enable_wizard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)

    def _fake_run_component_field_wizard(
        *,
        config_yaml: str,
        selected_infra: set[str],
        selected_apps: set[str],
        infra_entries,
        app_entries,
        provider_lookup=None,
        **_kwargs,
    ) -> tuple[str, bool]:
        _ = selected_infra, selected_apps, infra_entries, app_entries, provider_lookup
        payload = yaml.safe_load(config_yaml) or {}
        assert isinstance(payload, dict)
        deploy = payload.setdefault("deploy", {})
        assert isinstance(deploy, dict)
        targets = deploy.setdefault("targets", [{"instance_id": "mk8s"}])
        assert isinstance(targets, list)
        target = targets[0]
        assert isinstance(target, dict)
        observability = target.setdefault("observability", {})
        assert isinstance(observability, dict)
        observability["enabled"] = True
        return yaml.safe_dump(payload, sort_keys=False), True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _fake_run_component_field_wizard)


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
    assert "nebius-cxcli quota-request" in result.output


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


def test_create_prints_next_step_commands_one_per_line(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root, "--no-validate-config")

    assert result.exit_code == 0, result.output
    config_arg = shlex.quote(str(_project_config_path(deployments_root).resolve()))
    lines = result.output.splitlines()
    next_steps_index = lines.index("Next steps:")
    assert lines[next_steps_index + 1 : next_steps_index + 5] == [
        f"  `nebius-cxcli validate {config_arg}`",
        f"  `nebius-cxcli render {config_arg}`",
        f"  `nebius-cxcli deploy {config_arg}`",
        f"  `nebius-cxcli bootstrap-ci {config_arg}` (optional)",
    ]
    assert "then deploy from the rendered bundle" not in result.output


def test_create_uses_name_based_project_folders(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root, "--no-validate-config")

    assert result.exit_code == 0, result.output
    assert _project_config_path(deployments_root).exists()
    assert not (deployments_root / "tenant-123" / "project-456" / "config.yaml").exists()


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


def test_create_no_validate_config_still_prints_mk8s_gpu_validation_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cli_module,
        "mk8s_gpu_validation_warnings",
        lambda _payload: (
            "deploy.targets[].validations.mk8s_gpu.nccl.enabled is set on an Ethernet-only test shape.",
        ),
    )

    result = _create_non_interactive(deployments_root, "--no-validate-config")

    assert result.exit_code == 0, result.output
    assert "Deploy validation warning:" in result.output
    assert "Ethernet-only test shape" in result.output


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
        input="tenant-123\nproject-456\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Tenant ID" in result.output
    assert "Project ID" in result.output
    assert "Tenant ID [tenant-123]" not in result.output
    assert "Project ID [project-456]" not in result.output
    assert "Existing project detected." in result.output
    assert "Continue and overwrite the existing project folder from scratch?" in result.output
    assert "(y/n, q/qq=stop wizard) [n]" in result.output
    assert "Existing deployments root detected." not in result.output
    assert "Continue and enter project identity?" not in result.output
    assert "Client name [client-a]" not in result.output
    assert "No changes applied." in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_create_existing_project_validates_sources_before_overwrite_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    original = config_path.read_text(encoding="utf-8")

    def _fail_source_validation(**_kwargs: object) -> None:
        raise RuntimeError("source validation failed")

    monkeypatch.setattr(
        cli_module,
        "_validate_component_sources_or_raise",
        _fail_source_validation,
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
        ],
        input="tenant-123\nproject-456\n",
    )

    assert result.exit_code == 1, result.output
    assert "source validation failed" in result.output
    assert "Continue and overwrite the existing project folder from scratch?" not in result.output
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
            "--infra",
            "none",
            "--app",
            "none",
            "--no-validate-sources",
        ],
        input="tenant-123\nproject-789\nclient-b\n\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Tenant ID" in result.output
    assert "Project ID" in result.output
    assert "Tenant ID [tenant-123]" not in result.output
    assert "Project ID [project-456]" not in result.output
    assert "Existing deployments root detected." not in result.output
    assert "Continue and enter project identity?" not in result.output
    assert "Existing project detected." not in result.output
    assert "Continue and overwrite the existing project folder from scratch?" not in result.output
    assert "Client name [client-a]" not in result.output
    assert "Created project:" in result.output
    assert config_path.read_text(encoding="utf-8") == original
    assert (
        _project_dir(
            deployments_root,
            tenant_id="tenant-123",
            project_id="project-789",
        )
        .joinpath("config.yaml")
        .exists()
    )


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
    assert (
        _project_dir(
            deployments_root,
            tenant_id="tenant-123",
            project_id="project-789",
        )
        .joinpath("config.yaml")
        .exists()
    )


def test_create_refuses_name_based_path_collision_with_different_project_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        cli_module,
        "_resolve_create_target_folders",
        lambda **_kwargs: ("shared-tenant-name", "shared-project-name"),
    )

    first = _create_named_non_interactive(
        deployments_root,
        client_name="client-a",
        tenant_id="tenant-123",
        project_id="project-456",
    )
    assert first.exit_code == 0, first.output

    second = _create_named_non_interactive(
        deployments_root,
        client_name="client-a",
        tenant_id="tenant-123",
        project_id="project-789",
    )

    assert second.exit_code == 1
    assert "Resolved name-based project path collision:" in second.output


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
    assert "Managed by `nebius-cxcli`" in content
    assert "Keep config.yaml and generated/nebius-cxcli-manifest.json versioned" in content
    assert "tfvars duplicates recreated from the generated manifest" in content
    assert "*/*/wireguard-clients/" in content
    assert "*/*/generated/infra/.terraform/" in content
    assert "*/*/generated/infra/crash.*.log" in content
    assert "*/*/generated/infra/terraform.auto.tfvars.json" in content
    assert ".coverage" not in content
    assert "*.tgz" not in content

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


def test_create_rejects_nested_deployments_root_with_managed_parent_gitignore(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployment-examples"
    deployments_root.mkdir(parents=True, exist_ok=True)
    create_root = _create_non_interactive(deployments_root)
    assert create_root.exit_code == 0, create_root.output

    nested_root = deployments_root / "post-sales"
    nested_root.mkdir(parents=True, exist_ok=True)
    result = _create_non_interactive(nested_root)

    assert result.exit_code == 1, result.output
    normalized = _normalized_cli_output(result.output)
    assert "nested under existing cxcli-managed deployments root" in normalized
    assert "Use '" in normalized
    assert "deployment-examples' as the deployments root" in normalized
    assert not (nested_root / ".gitignore").exists()
    assert not _project_config_path(nested_root).exists()


def test_create_rejects_missing_nested_deployments_root_with_managed_parent_gitignore(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployment-examples"
    create_root = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert create_root.exit_code == 0, create_root.output

    nested_root = deployments_root / "post-sales"
    result = _create_non_interactive(nested_root)

    assert result.exit_code == 1, result.output
    normalized = _normalized_cli_output(result.output)
    assert "nested under existing cxcli-managed deployments root" in normalized
    assert not nested_root.exists()
    assert not _project_config_path(nested_root).exists()


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
    assert "Managed by `nebius-cxcli`" in content
    assert "Keep config.yaml and generated/nebius-cxcli-manifest.json versioned" in content
    assert "tfvars duplicates recreated from the generated manifest" in content
    assert "*/*/wireguard-clients/" in content
    assert "*/*/generated/infra/.terraform/" in content
    assert "*/*/generated/infra/crash.*.log" in content
    assert "*/*/generated/infra/terraform.auto.tfvars.json" in content
    assert ".coverage" not in content
    assert "*.tgz" not in content


def test_render_rejects_config_under_nested_deployments_root_with_managed_parent_gitignore(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployment-examples"
    nested_root = deployments_root / "post-sales"
    nested_root.mkdir(parents=True, exist_ok=True)
    create_nested = _create_non_interactive(nested_root)
    assert create_nested.exit_code == 0, create_nested.output

    deployments_root.mkdir(parents=True, exist_ok=True)
    create_root = _create_non_interactive(deployments_root)
    assert create_root.exit_code == 0, create_root.output

    config_path = _project_config_path(nested_root)
    result = runner.invoke(app, ["render", "--force", str(config_path)])

    assert result.exit_code == 1, result.output
    assert "nested under existing cxcli-managed deployments root" in " ".join(result.output.split())


@pytest.mark.parametrize(
    ("component_id", "instance_name", "extra_inputs"),
    [
        ("wireguard-gw", "wg-gw", {"local_subnets": ["10.0.0.0/8"]}),
        ("ssh-jumphost", "ssh-jumphost", {"allowed_cidrs": ["203.0.113.10/32"]}),
    ],
)
def test_render_normalizes_ssh_public_key_file_path_into_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component_id: str,
    instance_name: str,
    extra_inputs: dict[str, object],
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    create_result = _create_non_interactive(deployments_root, "--infra", component_id)
    assert create_result.exit_code == 0, create_result.output

    key_path = tmp_path / "id_ed25519.pub"
    key_path.write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    jumphost = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == component_id
    )
    jumphost["instance_id"] = instance_name
    jumphost["inputs"] = {
        "parent_id": "project-456",
        "subnet_id": "subnet-123",
        "name": instance_name,
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": str(key_path),
        **extra_inputs,
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
    monkeypatch.setattr(
        cli_module, "_runtime_component_output_values", lambda _config, _paths, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_module,
        "render_flux",
        lambda _config, _paths, *, component_output_values=None: [],
    )
    monkeypatch.setattr(
        cli_module,
        "_write_generated_runtime_manifest",
        lambda _config, paths, *, source_profile, **kwargs: paths.generated_dir / "manifest.json",
    )
    monkeypatch.setattr(
        cli_module,
        "_try_generate_terraform_lock_file",
        lambda _config, _paths, **_kwargs: False,
    )

    result = runner.invoke(app, ["render", "--force", str(config_path)])

    assert result.exit_code == 0, result.output
    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    refreshed_jumphost = next(
        item
        for item in refreshed["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == component_id
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


def test_validate_rejects_enabled_ssh_component_missing_public_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    ssh_jumphost["inputs"] = {
        "parent_id": "project-456",
        "subnet_id": "subnet-123",
        "name": "ssh-jumphost",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "source_image_family": "ubuntu24.04-driverless",
        "ssh_user_name": "ubuntu",
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (
            "parent_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
            "ssh_public_key",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variable_names",
        lambda _source: (
            "parent_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
            "ssh_user_name",
            "ssh_public_key",
        ),
    )

    result = runner.invoke(app, ["validate", str(config_path)])

    assert result.exit_code == 1, result.output
    normalized = _normalized_cli_output(result.output)
    assert "Strict validation failed" in normalized
    assert "infra.components[ssh-jumphost].inputs.ssh_public_key is required" in normalized


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
    assert "Pass <tenant-folder>/<project-folder>/config.yaml." in normalized


def test_create_force_overwrites_from_scratch_without_reusing_client_info_defaults(
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
    mk8s_row["instance_id"] = "custom-cluster"
    inputs.setdefault("cluster", {})["cluster_name"] = "custom-cluster"
    for chart in payload.get("apps", {}).get("charts", []):
        if isinstance(chart, dict) and chart.get("instance_id") == "mk8s":
            chart["instance_id"] = "custom-cluster"
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict) and target.get("instance_id") == "mk8s":
            target["instance_id"] = "custom-cluster"
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
    assert refreshed["client_info"]["client_name"] == "client-a"
    assert refreshed["client_info"]["nebius"]["region_id"] == "eu-north1"
    assert refreshed["client_info"]["notifications"]["email_enabled"] is False
    assert refreshed["client_info"]["notifications"]["email"] is None
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
    assert "normal create defaults" in " ".join(forced.output.split())
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
    assert not (project_dir / "generated" / "inventory" / "deploy-report.md").exists()


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


def test_create_interactive_force_existing_project_skips_confirmation(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--force",
            "--infra",
            "none",
            "--app",
            "none",
            "--no-validate-sources",
        ],
        input="tenant-123\nproject-456\nclient-forced\neu-north1\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Existing project detected." in result.output
    assert "Continue and overwrite the existing project folder from scratch?" not in result.output
    assert "`--force` confirms the overwrite." in result.output
    assert "(y/n, q/qq=stop wizard) [n]" not in result.output
    assert "Existing deployments root detected." not in result.output
    assert "No changes applied." not in result.output
    assert "Overwritten project:" in result.output
    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert refreshed["client_info"]["client_name"] == "client-forced"


def test_create_interactive_overwrite_restarts_client_info_prompts_from_fresh_defaults(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    first = _create_non_interactive(deployments_root)
    assert first.exit_code == 0, first.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["client_info"]["client_name"] = "proserv1"
    payload["client_info"]["nebius"]["region_id"] = "me-west1"
    payload["client_info"]["notifications"]["email_enabled"] = True
    payload["client_info"]["notifications"]["email"] = "ops@example.com"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--infra",
            "none",
            "--app",
            "none",
            "--no-validate-sources",
        ],
        input="tenant-123\nproject-456\ny\nclient-b\n\n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Client name [proserv1]" not in result.output
    assert (
        "Notifications email (optional; leave blank to keep email disabled) [ops@example.com]"
        not in result.output
    )

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert refreshed["client_info"]["client_name"] == "client-b"
    assert refreshed["client_info"]["nebius"]["region_id"] == "eu-north1"
    assert refreshed["client_info"]["notifications"]["email_enabled"] is False
    assert refreshed["client_info"]["notifications"]["email"] is None


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
        "none",
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled == {}
    assert apps_enabled == {}


def test_create_auto_enables_gpu_operator_when_wizard_turns_on_mk8s_gpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    _patch_late_mk8s_gpu_enable_wizard(monkeypatch)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'apps:nvidia-gpu-operator'" in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled["nvidia-gpu-operator"] is True
    assert "nvidia-network-operator" not in apps_enabled


def test_create_auto_enables_nfs_csi_driver_when_nfs_and_mk8s_selected(
    tmp_path: Path,
) -> None:
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
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--infra",
            "nfs",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'apps:csi-driver-nfs@mk8s'" in result.output
    assert "VM-backed NFS for MK8s requires the NFS CSI driver" in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    csi_rows = [
        row for row in charts if isinstance(row, dict) and row.get("id") == "csi-driver-nfs"
    ]
    assert len(csi_rows) == 1
    assert csi_rows[0]["instance_id"] == "mk8s"
    assert csi_rows[0]["enabled"] is True
    assert "target_ref" not in csi_rows[0]


def test_create_explains_soperator_required_component_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    def _fake_dynamic_choices(
        *,
        payload,
        entry,
        full_path_label,
        provider_lookup,
    ):
        _ = entry, provider_lookup
        if full_path_label.endswith(".inputs.cluster.network_id"):
            return [cli_module.OptionChoice(value="vpcnetwork-1", label="default network")]
        if full_path_label.endswith(".inputs.cluster.subnet_id"):
            assert cli_module._read_payload_field(
                payload, "infra.components[0].inputs.cluster.network_id"
            )
            return [cli_module.OptionChoice(value="vpcsubnet-1", label="default subnet")]
        if full_path_label.endswith(".inputs.cluster.k8s_version"):
            return [cli_module.OptionChoice(value="1.33", label="1.33")]
        if full_path_label.endswith(".inputs.node_group_defaults.gpu.infiniband_fabric"):
            return [cli_module.OptionChoice(value="fabric-1", label="fabric-1")]
        return []

    monkeypatch.setattr(cli_module, "_resolve_dynamic_field_choices", _fake_dynamic_choices)

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
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "soperator",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'infra:sfs'" in result.output
    assert "'apps:cert-manager'" in result.output
    assert "install_mode=production-cluster creates the complete MK8s+SFS+Soperator bundle" in (
        result.output
    )
    assert "requires cert-manager for webhook certificate automation" in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled["mk8s"] is True
    assert infra_enabled["sfs"] is True
    assert apps_enabled["soperator"] is True
    assert apps_enabled["cert-manager"] is True
    mk8s_inputs = next(
        row["inputs"]
        for row in payload["infra"]["components"]
        if isinstance(row, dict) and row.get("id") == "mk8s"
    )
    assert mk8s_inputs["cluster"]["network_id"] == "vpcnetwork-1"
    assert mk8s_inputs["cluster"]["subnet_id"] == "vpcsubnet-1"
    assert mk8s_inputs["cluster"]["k8s_version"] == "1.33"
    assert mk8s_inputs["node_group_defaults"]["gpu"]["infiniband_fabric"] == "fabric-1"
    assert mk8s_inputs["gpu_clusters"] == {"workers": {"infiniband_fabric": "fabric-1"}}
    assert mk8s_inputs["node_groups"]["worker"]["gpu_cluster_key"] == "workers"
    assert mk8s_inputs["node_groups"]["worker"]["reservation"] == {"policy": "AUTO"}


def test_create_prompts_soperator_profile_before_field_wizard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    events: list[str] = []

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "production-cluster"

    def _capture_profile() -> str:
        events.append("profile")
        return "nebius-cpu-v1"

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["skip_soperator_profile_prompt"] is True
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        charts = payload.get("apps", {}).get("charts", [])
        soperator = next(
            row for row in charts if isinstance(row, dict) and row.get("id") == "soperator"
        )
        assert soperator["install_mode"] == "production-cluster"
        assert soperator["profile"] == "nebius-cpu-v1"
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_prompt_soperator_profile", _capture_profile)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "soperator",
        ],
    )

    assert result.exit_code == 0, result.output
    assert events == ["install_mode", "profile", "field_wizard"]
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    infra_rows = payload["infra"]["components"]
    app_rows = payload["apps"]["charts"]
    assert [(row["id"], row["instance_id"]) for row in infra_rows] == [
        ("mk8s", "mk8s"),
        ("sfs", "sfs"),
    ]
    assert [(row["id"], row["instance_id"]) for row in app_rows] == [
        ("soperator", "mk8s"),
        ("cert-manager", "mk8s"),
    ]
    assert app_rows[0]["profile"] == "nebius-cpu-v1"
    assert "Enabled apps components: cert-manager, soperator" in result.output
    assert "Enabled apps components: cert-manager, nvidia-gpu-operator, soperator" not in (
        result.output
    )


def test_create_soperator_cpu_worker_count_prompt_updates_persisted_node_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", lambda: "production-cluster")
    monkeypatch.setattr(cli_module, "_prompt_soperator_profile", lambda: "nebius-cpu-v1")

    def _set_worker_count_fields(**kwargs):  # type: ignore[no-untyped-def]
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        mk8s = next(
            row
            for row in payload["infra"]["components"]
            if isinstance(row, dict) and row.get("id") == "mk8s"
        )
        mk8s.setdefault("inputs", {}).setdefault("soperator", {}).update(
            {
                "worker_total_nodes": 3,
                "worker_nodes_per_group": 2,
            }
        )
        return yaml.safe_dump(payload, sort_keys=False), True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _set_worker_count_fields)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "soperator",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    mk8s_inputs = next(
        row["inputs"]
        for row in payload["infra"]["components"]
        if isinstance(row, dict) and row.get("id") == "mk8s"
    )
    node_groups = mk8s_inputs["node_groups"]
    assert {
        key: group["node_count"]
        for key, group in node_groups.items()
        if str(key).startswith("worker-cpu")
    } == {"worker-cpu-0": 2, "worker-cpu-1": 1}
    assert "gpu" not in mk8s_inputs.get("node_group_defaults", {})
    soperator = next(
        row
        for row in payload["apps"]["charts"]
        if isinstance(row, dict) and row.get("id") == "soperator"
    )
    assert soperator["values"]["nodeGroupMapping"]["worker"] == [
        "worker-cpu-0",
        "worker-cpu-1",
    ]
    worker_nodesets = [
        item
        for item in soperator["values"]["nodesets"]
        if str(item.get("name", "")).startswith("worker-cpu")
    ]
    assert [(item["name"], item["replicas"]) for item in worker_nodesets] == [
        ("worker-cpu-worker-cpu-0", 2),
        ("worker-cpu-worker-cpu-1", 1),
    ]
    assert "Enabled apps components: cert-manager, soperator" in result.output
    assert "nvidia-gpu-operator" not in result.output


def test_create_interactive_mk8s_only_does_not_prompt_soperator_or_add_apps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    events: list[str] = []

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_install_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Soperator install mode should not be prompted")
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Soperator profile should not be prompted")
        ),
    )

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["selected_infra"] == {"mk8s"}
        assert kwargs["selected_apps"] == set()
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert events == ["field_wizard"]
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    assert [row["id"] for row in payload["infra"]["components"]] == ["mk8s"]
    assert payload["apps"]["charts"] == []


def test_component_add_soperator_from_empty_config_prompts_profile_before_field_wizard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    events: list[str] = []
    config_path = _project_config_path(deployments_root)

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_module,
        "_prompt_infra_add_resource_names",
        lambda *, payload, add_targets, infra_entries: add_targets,
    )

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "production-cluster"

    def _capture_profile() -> str:
        events.append("profile")
        return "nebius-cpu-v1"

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["skip_soperator_profile_prompt"] is True
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        infra_rows = payload.get("infra", {}).get("components", [])
        app_rows = payload.get("apps", {}).get("charts", [])
        assert [(row.get("id"), row.get("instance_id")) for row in infra_rows] == [
            ("mk8s", "mk8s"),
            ("sfs", "sfs"),
        ]
        assert sum(1 for row in app_rows if row.get("id") == "soperator") == 1
        assert sum(1 for row in app_rows if row.get("id") == "cert-manager") == 1
        soperator = next(row for row in app_rows if row.get("id") == "soperator")
        assert soperator["instance_id"] == "mk8s"
        assert soperator["install_mode"] == "production-cluster"
        assert soperator["profile"] == "nebius-cpu-v1"
        mk8s_inputs = next(row["inputs"] for row in infra_rows if row.get("id") == "mk8s")
        assert "worker-cpu" in mk8s_inputs["node_groups"]
        assert "gpu_clusters" not in mk8s_inputs
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_prompt_soperator_profile", _capture_profile)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "soperator")

    assert result.exit_code == 0, result.output
    assert events == ["install_mode", "profile", "field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    infra_rows = payload["infra"]["components"]
    app_rows = payload["apps"]["charts"]
    assert [(row["id"], row["instance_id"]) for row in infra_rows] == [
        ("mk8s", "mk8s"),
        ("sfs", "sfs"),
    ]
    assert [(row["id"], row["instance_id"]) for row in app_rows] == [
        ("soperator", "mk8s"),
        ("cert-manager", "mk8s"),
    ]
    assert app_rows[0]["profile"] == "nebius-cpu-v1"


def test_component_add_soperator_onboarding_adds_external_target_without_mk8s_infra(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    events: list[str] = []
    config_path = _project_config_path(deployments_root)

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Soperator production profile should not be prompted")
        ),
    )

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "onboard-existing-cluster"

    def _external_target_row() -> dict[str, object]:
        events.append("external_target")
        return {
            "instance_id": "external-cluster",
            "kind": "external-mk8s",
            "ownership": "external",
            "access": "external",
            "kube_context": "external-context",
            "inventory": {
                "node_groups": {
                    "cpu-pool": {"gpu": False, "node_count": 2},
                    "gpu-pool": {"gpu": True, "node_count": 1},
                }
            },
            "soperator_onboarding": {
                "accepted": True,
                "analysis_fingerprint": "",
                "state": "vanilla-mk8s",
                "actions": ["install-soperator"],
                "storage_mode": "adopt-existing-storage",
            },
        }

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["selected_infra"] == set()
        assert kwargs["selected_apps"] == {"soperator", "cert-manager"}
        assert "skip_soperator_profile_prompt" not in kwargs
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        assert payload["infra"]["components"] == []
        targets = payload.get("deploy", {}).get("targets", [])
        assert [(row.get("instance_id"), row.get("kind")) for row in targets] == [
            ("external-cluster", "external-mk8s")
        ]
        app_rows = payload.get("apps", {}).get("charts", [])
        assert {(row.get("id"), row.get("instance_id")) for row in app_rows} == {
            ("soperator", "external-cluster"),
            ("cert-manager", "external-cluster"),
        }
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_prompt_soperator_onboarding_target_row", _external_target_row)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "mk8s", "soperator")

    assert result.exit_code == 0, result.output
    assert "install_mode=onboard-existing-cluster uses an external MK8s" in result.output
    assert events == ["install_mode", "external_target", "field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]
    assert payload["deploy"]["targets"][0]["kind"] == "external-mk8s"


def test_component_add_soperator_onboarding_reuses_existing_external_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": "external-cluster",
                "kind": "external-mk8s",
                "ownership": "external",
                "access": "external",
                "kube_context": "external-context",
                "inventory": {
                    "node_groups": {
                        "cpu-pool": {"gpu": False, "node_count": 2},
                        "gpu-pool": {"gpu": True, "node_count": 1},
                    }
                },
                "soperator_onboarding": {
                    "accepted": True,
                    "analysis_fingerprint": "",
                    "state": "vanilla-mk8s",
                    "actions": ["install-soperator"],
                    "storage_mode": "adopt-existing-storage",
                },
            }
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    events: list[str] = []
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_onboarding_target_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Existing external target should be reused")
        ),
    )

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "onboard-existing-cluster"

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        assert payload["infra"]["components"] == []
        targets = payload.get("deploy", {}).get("targets", [])
        assert [(row.get("instance_id"), row.get("kind")) for row in targets] == [
            ("external-cluster", "external-mk8s")
        ]
        app_rows = payload.get("apps", {}).get("charts", [])
        assert {(row.get("id"), row.get("instance_id")) for row in app_rows} == {
            ("soperator", "external-cluster"),
            ("cert-manager", "external-cluster"),
        }
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "soperator")

    assert result.exit_code == 0, result.output
    assert events == ["install_mode", "field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert len(payload["deploy"]["targets"]) == 1
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]


def test_component_add_soperator_external_target_noninteractive_infers_onboarding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {"targets": [_external_mk8s_target_row()]}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_install_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Non-interactive add should infer onboarding")
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_onboarding_target_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Existing external target should be reused")
        ),
    )

    result = _component_add(
        config_path,
        "soperator@external-cluster",
        "--no-interactive",
    )

    assert result.exit_code == 0, result.output
    assert "install_mode=onboard-existing-cluster" in result.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    app_rows = payload["apps"]["charts"]
    assert [(row["id"], row["instance_id"]) for row in app_rows] == [
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]
    soperator = next(row for row in app_rows if row["id"] == "soperator")
    assert soperator["install_mode"] == "onboard-existing-cluster"
    assert payload["deploy"]["targets"][0]["kind"] == "external-mk8s"


def test_component_add_soperator_repairs_existing_onboarding_dependency_without_infra(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {"targets": [_external_mk8s_target_row()]}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    seeded = _component_add(
        config_path,
        "soperator@external-cluster",
        "--no-interactive",
    )
    assert seeded.exit_code == 0, seeded.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["apps"]["charts"] = [
        row for row in payload["apps"]["charts"] if row["id"] != "cert-manager"
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    repaired = _component_add(config_path, "soperator", "--no-interactive")

    assert repaired.exit_code == 0, repaired.output
    assert "Config up-to-date" not in repaired.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]


def test_component_add_soperator_external_targets_noninteractive_scopes_dependencies(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {
        "targets": [
            _external_mk8s_target_row("external-a"),
            _external_mk8s_target_row("external-b"),
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _component_add(
        config_path,
        "soperator@external-a",
        "soperator@external-b",
        "--no-interactive",
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-a"),
        ("soperator", "external-b"),
        ("cert-manager", "external-a"),
        ("cert-manager", "external-b"),
    ]
    assert all(
        row["install_mode"] == "onboard-existing-cluster"
        for row in payload["apps"]["charts"]
        if row["id"] == "soperator"
    )


def test_component_add_soperator_repairs_multi_target_onboarding_dependencies(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {
        "targets": [
            _external_mk8s_target_row("external-a"),
            _external_mk8s_target_row("external-b"),
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    seeded = _component_add(
        config_path,
        "soperator@external-a",
        "soperator@external-b",
        "--no-interactive",
    )
    assert seeded.exit_code == 0, seeded.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["apps"]["charts"] = [
        row for row in payload["apps"]["charts"] if row["id"] != "cert-manager"
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    repaired = _component_add(config_path, "soperator", "--no-interactive")

    assert repaired.exit_code == 0, repaired.output
    assert "Config up-to-date" not in repaired.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-a"),
        ("soperator", "external-b"),
        ("cert-manager", "external-a"),
        ("cert-manager", "external-b"),
    ]


def test_component_add_soperator_repairs_multi_target_onboarding_with_explicit_dependency(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "none",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {
        "targets": [
            _external_mk8s_target_row("external-a"),
            _external_mk8s_target_row("external-b"),
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    seeded = _component_add(
        config_path,
        "soperator@external-a",
        "soperator@external-b",
        "--no-interactive",
    )
    assert seeded.exit_code == 0, seeded.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["apps"]["charts"] = [
        row for row in payload["apps"]["charts"] if row["id"] != "cert-manager"
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    repaired = _component_add(config_path, "cert-manager", "soperator", "--no-interactive")

    assert repaired.exit_code == 0, repaired.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-a"),
        ("soperator", "external-b"),
        ("cert-manager", "external-a"),
        ("cert-manager", "external-b"),
    ]
    assert all(component_instance_id(row) != "cert-manager" for row in payload["apps"]["charts"])


def test_component_add_soperator_onboarding_adds_dependency_for_external_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "cert-manager",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload.setdefault("deploy", {}).setdefault("targets", []).append(
        {
            "instance_id": "external-cluster",
            "kind": "external-mk8s",
            "ownership": "external",
            "access": "external",
            "kube_context": "external-context",
            "inventory": {
                "node_groups": {
                    "cpu-pool": {"gpu": False, "node_count": 2},
                    "gpu-pool": {"gpu": True, "node_count": 1},
                }
            },
            "soperator_onboarding": {
                "accepted": True,
                "analysis_fingerprint": "",
                "state": "vanilla-mk8s",
                "actions": ["install-soperator"],
                "storage_mode": "adopt-existing-storage",
            },
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    events: list[str] = []
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_onboarding_target_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Existing external target should be reused")
        ),
    )

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "onboard-existing-cluster"

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        app_rows = payload.get("apps", {}).get("charts", [])
        assert {(row.get("id"), row.get("instance_id")) for row in app_rows} == {
            ("cert-manager", "mk8s"),
            ("soperator", "external-cluster"),
            ("cert-manager", "external-cluster"),
        }
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "soperator")

    assert result.exit_code == 0, result.output
    assert events == ["install_mode", "field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("cert-manager", "mk8s"),
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]


def test_component_add_soperator_onboarding_does_not_duplicate_existing_external_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "cert-manager",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload.setdefault("deploy", {}).setdefault("targets", []).append(
        {
            "instance_id": "external-cluster",
            "kind": "external-mk8s",
            "ownership": "external",
            "access": "external",
            "kube_context": "external-context",
            "inventory": {"node_groups": {"gpu-pool": {"gpu": True, "node_count": 1}}},
            "soperator_onboarding": {
                "accepted": True,
                "analysis_fingerprint": "",
                "state": "vanilla-mk8s",
                "actions": ["install-soperator"],
                "storage_mode": "adopt-existing-storage",
            },
        }
    )
    cert_manager_mk8s = next(
        row for row in payload["apps"]["charts"] if row["id"] == "cert-manager"
    )
    payload["apps"]["charts"].append(
        {
            **cert_manager_mk8s,
            "instance_id": "external-cluster",
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    events: list[str] = []
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_onboarding_target_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Existing external target should be reused")
        ),
    )

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "onboard-existing-cluster"

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        app_pairs = [
            (row.get("id"), row.get("instance_id"))
            for row in payload.get("apps", {}).get("charts", [])
        ]
        assert app_pairs.count(("cert-manager", "external-cluster")) == 1
        assert set(app_pairs) == {
            ("cert-manager", "mk8s"),
            ("cert-manager", "external-cluster"),
            ("soperator", "external-cluster"),
        }
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "cert-manager", "soperator")

    assert result.exit_code == 0, result.output
    assert events == ["install_mode", "field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    app_pairs = [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]]
    assert app_pairs.count(("cert-manager", "external-cluster")) == 1
    assert set(app_pairs) == {
        ("cert-manager", "mk8s"),
        ("cert-manager", "external-cluster"),
        ("soperator", "external-cluster"),
    }


def test_component_add_soperator_onboarding_preserves_existing_infra_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s,sfs",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    events: list[str] = []
    config_path = _project_config_path(deployments_root)

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "onboard-existing-cluster"

    def _external_target_row() -> dict[str, object]:
        events.append("external_target")
        return {
            "instance_id": "external-cluster",
            "kind": "external-mk8s",
            "ownership": "external",
            "access": "external",
            "kube_context": "external-context",
            "inventory": {
                "node_groups": {
                    "cpu-pool": {"gpu": False, "node_count": 2},
                    "gpu-pool": {"gpu": True, "node_count": 1},
                }
            },
            "soperator_onboarding": {
                "accepted": True,
                "analysis_fingerprint": "",
                "state": "vanilla-mk8s",
                "actions": ["install-soperator"],
                "storage_mode": "adopt-existing-storage",
            },
        }

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        assert [
            (row.get("id"), row.get("instance_id")) for row in payload["infra"]["components"]
        ] == [
            ("mk8s", "mk8s"),
            ("sfs", "sfs"),
        ]
        app_rows = payload.get("apps", {}).get("charts", [])
        assert {(row.get("id"), row.get("instance_id")) for row in app_rows} == {
            ("soperator", "external-cluster"),
            ("cert-manager", "external-cluster"),
        }
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_prompt_soperator_onboarding_target_row", _external_target_row)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "mk8s", "sfs", "soperator")

    assert result.exit_code == 0, result.output
    assert "install_mode=onboard-existing-cluster uses an external MK8s" in result.output
    assert events == ["install_mode", "external_target", "field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert [(row["id"], row["instance_id"]) for row in payload["infra"]["components"]] == [
        ("mk8s", "mk8s"),
        ("sfs", "sfs"),
    ]
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]


def test_component_add_interactive_mk8s_only_keeps_existing_apps_on_original_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "soperator",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    events: list[str] = []

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_install_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Soperator install mode should not be prompted")
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Soperator profile should not be prompted")
        ),
    )

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["selected_infra"] == {"cluster2"}
        assert kwargs["selected_apps"] == set()
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = _component_add(config_path, "mk8s@cluster2")

    assert result.exit_code == 0, result.output
    assert events == ["field_wizard"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    mk8s_rows = [row for row in payload["infra"]["components"] if row.get("id") == "mk8s"]
    assert [row["instance_id"] for row in mk8s_rows] == ["mk8s", "cluster2"]
    app_pairs = {
        (row.get("id"), row.get("instance_id"))
        for row in payload["apps"]["charts"]
        if row.get("id") in {"soperator", "cert-manager"}
    }
    assert ("soperator", "mk8s") in app_pairs
    assert ("cert-manager", "mk8s") in app_pairs
    assert ("soperator", "cluster2") not in app_pairs
    assert ("cert-manager", "cluster2") not in app_pairs


def test_create_interactive_soperator_onboarding_adds_external_target_without_mk8s_infra(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    events: list[str] = []

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "_prompt_soperator_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Soperator production profile should not be prompted")
        ),
    )

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "onboard-existing-cluster"

    def _external_target_row() -> dict[str, object]:
        events.append("external_target")
        return {
            "instance_id": "external-cluster",
            "kind": "external-mk8s",
            "ownership": "external",
            "access": "external",
            "kube_context": "external-context",
            "inventory": {
                "node_groups": {
                    "cpu-pool": {"gpu": False, "node_count": 2},
                    "gpu-pool": {"gpu": True, "node_count": 1},
                }
            },
            "soperator_onboarding": {
                "accepted": True,
                "analysis_fingerprint": "",
                "state": "vanilla-mk8s",
                "actions": ["install-soperator"],
                "storage_mode": "adopt-existing-storage",
            },
        }

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["selected_infra"] == set()
        assert kwargs["selected_apps"] == {"soperator", "cert-manager"}
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        assert payload["infra"]["components"] == []
        targets = payload.get("deploy", {}).get("targets", [])
        assert [(row.get("instance_id"), row.get("kind")) for row in targets] == [
            ("external-cluster", "external-mk8s")
        ]
        app_rows = payload.get("apps", {}).get("charts", [])
        assert {(row.get("id"), row.get("instance_id")) for row in app_rows} == {
            ("soperator", "external-cluster"),
            ("cert-manager", "external-cluster"),
        }
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_prompt_soperator_onboarding_target_row", _external_target_row)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "soperator",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "install_mode=onboard-existing-cluster uses an external MK8s" in result.output
    assert events == ["install_mode", "external_target", "field_wizard"]
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert [(row["id"], row["instance_id"]) for row in payload["apps"]["charts"]] == [
        ("soperator", "external-cluster"),
        ("cert-manager", "external-cluster"),
    ]
    assert payload["deploy"]["targets"][0]["kind"] == "external-mk8s"


def test_create_validates_late_auto_enabled_nfs_csi_driver_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    source_validation_calls: list[dict[str, object]] = []

    def _capture_source_validation(**kwargs: object) -> None:
        selected_app_ids = kwargs.get("selected_app_ids")
        if isinstance(selected_app_ids, set):
            kwargs = {**kwargs, "selected_app_ids": set(selected_app_ids)}
        source_validation_calls.append(dict(kwargs))

    monkeypatch.setattr(
        cli_module,
        "_validate_component_sources_or_raise",
        _capture_source_validation,
    )

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
            "--region-id",
            "eu-north1",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--infra",
            "nfs",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert source_validation_calls[0] == {"selected_app_ids": set()}
    assert {
        "selected_app_ids": {"csi-driver-nfs"},
        "include_infra": False,
    } in source_validation_calls


def test_create_mysterybox_with_mk8s_surfaces_external_secrets_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli_module, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)

    captured_selected_apps: list[set[str]] = []

    def _fake_run_component_field_wizard(
        *,
        config_yaml: str,
        selected_infra: set[str],
        selected_apps: set[str],
        infra_entries,
        app_entries,
        provider_lookup=None,
        **_kwargs,
    ) -> tuple[str, bool]:
        _ = selected_infra, infra_entries, app_entries, provider_lookup
        captured_selected_apps.append(set(selected_apps))
        payload = yaml.safe_load(config_yaml) or {}
        charts = payload.get("apps", {}).get("charts", [])
        external_secrets = next(
            row for row in charts if isinstance(row, dict) and row.get("id") == "external-secrets"
        )
        assert external_secrets["instance_id"] == "mk8s"
        return config_yaml, True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _fake_run_component_field_wizard)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--infra",
            "mysterybox",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "'apps:external-secrets@mk8s'" in result.output
    assert captured_selected_apps == [{"external-secrets"}]


def test_create_auto_enables_network_operator_only_for_gpu_cluster_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    _patch_late_mk8s_gpu_enable_wizard(monkeypatch, infiniband_fabric="fabric-1")

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'apps:nvidia-gpu-operator'" in result.output
    assert "'apps:nvidia-network-operator'" in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled["nvidia-gpu-operator"] is True
    assert apps_enabled["nvidia-network-operator"] is True


def test_create_names_new_mk8s_target_from_cluster_name_and_retargets_apps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    _patch_late_mk8s_gpu_enable_wizard(
        monkeypatch,
        infiniband_fabric="fabric-1",
        cluster_name="cluster1",
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    mk8s_row = next(
        row
        for row in payload["infra"]["components"]
        if isinstance(row, dict) and row.get("id") == "mk8s"
    )
    assert mk8s_row["instance_id"] == "cluster1"
    assert mk8s_row["inputs"]["cluster"]["cluster_name"] == "cluster1"

    charts = {
        str(row["id"]): row
        for row in payload["apps"]["charts"]
        if isinstance(row, dict) and bool(row.get("enabled"))
    }
    assert charts["nvidia-gpu-operator"]["instance_id"] == "cluster1"
    assert charts["nvidia-network-operator"]["instance_id"] == "cluster1"
    assert "target_ref" not in charts["nvidia-gpu-operator"]
    assert "target_ref" not in charts["nvidia-network-operator"]


def test_align_new_mk8s_target_name_repairs_existing_app_target_refs() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cluster": {
                            "cluster_name": "soperator-cluster1",
                        },
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "mk8s",
                    "target_ref": "mk8s",
                    "enabled": True,
                    "values": {
                        "clusterName": "mk8s",
                    },
                },
                {
                    "id": "cert-manager",
                    "instance_id": "mk8s",
                    "target_ref": "mk8s",
                    "enabled": True,
                    "values": {},
                },
            ]
        },
        "deploy": {
            "targets": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                }
            ]
        },
    }

    assert cli_module._align_new_infra_instance_ids_with_resource_names(
        payload,
        selected_instance_ids={"mk8s"},
    ) == {"mk8s": "soperator-cluster1"}

    mk8s = payload["infra"]["components"][0]
    assert mk8s["instance_id"] == "soperator-cluster1"
    assert payload["deploy"]["targets"][0]["instance_id"] == "soperator-cluster1"

    charts = {row["id"]: row for row in payload["apps"]["charts"]}
    assert charts["soperator"]["instance_id"] == "soperator-cluster1"
    assert charts["soperator"]["target_ref"] == "soperator-cluster1"
    assert charts["soperator"]["values"]["clusterName"] == "soperator-cluster1"
    assert charts["cert-manager"]["instance_id"] == "soperator-cluster1"
    assert charts["cert-manager"]["target_ref"] == "soperator-cluster1"


def test_create_field_wizard_uses_entered_cluster_name_for_app_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infra_entries = cli_module._with_infra_provider_groups(component_entries("infra"))
    app_entries = component_entries("apps")
    payload = cli_module._starter_component_payload(
        client_name="client-a",
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        email=None,
        selected_infra={"mk8s", "sfs"},
        selected_apps={"soperator", "cert-manager"},
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    cli_module._apply_soperator_install_mode_to_payload(payload, mode="production-cluster")
    cli_module._apply_soperator_profile_to_payload(payload, profile="nebius-cpu-v1")
    cli_module._materialize_soperator_component_defaults(payload)

    decisions = iter([True, False, False, False])
    monkeypatch.setattr(
        cli_module,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: next(decisions, False),
    )
    monkeypatch.setattr(cli_module, "_resolve_dynamic_field_choices", lambda **_kwargs: [])
    monkeypatch.setattr(
        cli_module,
        "_provider_allowed_values_for_field",
        lambda **_kwargs: (set(), ()),
    )

    def _prompt_scalar_override(path_label, current, **_kwargs):  # type: ignore[no-untyped-def]
        if path_label.endswith(".inputs.cluster.cluster_name"):
            return "soperator-cluster1", False
        if path_label.endswith(".inputs.cluster.network_id"):
            return "vpcnetwork-1", False
        if path_label.endswith(".inputs.cluster.subnet_id"):
            return "vpcsubnet-1", False
        if path_label.endswith(".inputs.cluster.k8s_version"):
            return "1.33", False
        return current, False

    previewed_app_targets: list[tuple[str, str, str]] = []

    def _capture_app_defaults_preview(component_node, *, entry):  # type: ignore[no-untyped-def]
        if not isinstance(component_node, dict):
            return
        values = component_node.get("values")
        cluster_name = values.get("clusterName", "") if isinstance(values, dict) else ""
        previewed_app_targets.append(
            (
                entry.id,
                str(component_node.get("instance_id", "")),
                str(cluster_name),
            )
        )

    monkeypatch.setattr(cli_module, "_prompt_scalar_override", _prompt_scalar_override)
    monkeypatch.setattr(
        cli_module,
        "_print_app_chart_skip_defaults_preview",
        _capture_app_defaults_preview,
    )

    updated_yaml, completed = cli_module._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"mk8s", "sfs"},
        selected_apps={"soperator", "cert-manager"},
        infra_entries=infra_entries,
        app_entries=app_entries,
        align_infra_resource_names_before_apps=True,
    )

    assert completed is True
    updated = yaml.safe_load(updated_yaml)
    mk8s = next(row for row in updated["infra"]["components"] if row["id"] == "mk8s")
    assert mk8s["instance_id"] == "soperator-cluster1"
    assert mk8s["inputs"]["cluster"]["cluster_name"] == "soperator-cluster1"

    charts = {
        row["id"]: row
        for row in updated["apps"]["charts"]
        if isinstance(row, dict) and row.get("enabled") is True
    }
    assert charts["soperator"]["instance_id"] == "soperator-cluster1"
    assert charts["soperator"]["values"]["clusterName"] == "soperator-cluster1"
    assert charts["cert-manager"]["instance_id"] == "soperator-cluster1"
    assert ("soperator", "soperator-cluster1", "soperator-cluster1") in previewed_app_targets
    assert all(target != "mk8s" for _app_id, target, _cluster_name in previewed_app_targets)


def test_create_auto_enables_observability_agent_when_wizard_turns_on_observability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    _patch_late_observability_enable_wizard(monkeypatch)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--no-validate-sources",
            "--no-validate-config",
            "--infra",
            "mk8s",
            "--app",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'apps:nebius-observability-agent'" in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled["nebius-observability-agent"] is True


def test_create_vm_only_omits_kubernetes_observability_defaults(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--region-id",
            "eu-north1",
            "--infra",
            "vm",
            "--app",
            "none",
            "--no-interactive",
            "--no-validate-sources",
            "--no-validate-config",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    observability = payload["deploy"]["observability"]
    assert "kubernetes" not in observability
    assert observability["vm"]["logs"]["enabled"] is True
    deploy = payload.get("deploy", {})
    assert "validations" not in deploy


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

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "gateway-helm",
    )
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
        "--infra",
        "mk8s",
        "--app",
        "gateway-helm",
    )
    assert refreshed.exit_code == 0, refreshed.output
    assert "Overwritten project:" in refreshed.output

    cleaned = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cleaned_gateway = next(
        item for item in cleaned["apps"]["charts"] if item.get("id") == "gateway-helm"
    )
    assert "securityContext" not in cleaned_gateway["values"]["certgen"]["job"]
    assert (
        cleaned_gateway["values"]["certgen"]["job"]["affinity"]
        == cleaned_gateway["values"]["deployment"]["pod"]["affinity"]
    )
    assert cleaned_gateway["values"]["config"]["envoyGateway"]["provider"]["type"] == "Kubernetes"


def test_create_does_not_write_when_early_exit_leaves_required_fields_missing(
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
        input="tenant-123\nproject-456\nclient-a\n\n\nqq\n",
    )

    assert result.exit_code == 1, result.output
    normalized = _normalized_cli_output(result.output)
    assert "Wizard stopped before all required fields were filled." in normalized
    assert "No project config or generated output was written." in normalized
    assert "infra.components[managed-postgresql].inputs.name is required" in result.output
    assert "Ensured generated skeleton:" not in result.output
    assert not _project_config_path(deployments_root).exists()
    assert not (_project_dir(deployments_root) / "generated").exists()


def test_create_preserves_existing_project_when_quit_leaves_required_fields_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    original_config = config_path.read_text(encoding="utf-8")
    stale_note = config_path.parent / "generated" / "keep.txt"
    stale_note.write_text("preserve me", encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("name",),
    )

    result = runner.invoke(
        app,
        [
            "create",
            str(deployments_root),
            "--tenant-id",
            "tenant-123",
            "--project-id",
            "project-456",
            "--client-name",
            "client-b",
            "--region-id",
            "eu-north1",
            "--infra",
            "managed-postgresql",
            "--app",
            "none",
            "--no-validate-sources",
        ],
        input="y\nops@example.com\n\nqq\n",
    )

    assert result.exit_code == 1, result.output
    assert "Existing project preserved:" in result.output
    assert "Overwritten project:" not in result.output
    assert config_path.read_text(encoding="utf-8") == original_config
    assert stale_note.read_text(encoding="utf-8") == "preserve me"


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
            "none",
            "--no-validate-sources",
        ],
        input="tenant-123\nproject-456\nclient-a\n\n\nq\n",
    )

    assert result.exit_code == 0, result.output
    assert "Wizard stopped before all required fields were filled." not in result.output
    assert "Wizard optional phases skipped." not in result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    apps_enabled = _apps_enabled_map(payload)
    assert apps_enabled == {}


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
                infra={
                    "mk8s": {
                        "source": {
                            "portable": (
                                "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
                            ),
                        },
                        "status": {
                            "kind": "nebius.mk8s.cluster",
                            "name_input": "cluster_name",
                        },
                    }
                },
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
                },
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


def test_load_context_materializes_compute_boot_disk_defaults_for_existing_config(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_compute_boot_disk_sources_file(
        sources_file,
        module_dir=tmp_path / "modules" / "mk8s",
    )
    source_payload = yaml.safe_load(sources_file.read_text(encoding="utf-8"))
    source_payload["components"]["apps"]["demo-app"] = {
        "source": _portable_chart_source(
            repo="https://example.invalid/charts",
            chart="demo-app",
            version="1.0.0",
        ),
        "ui": {
            "group": "Workloads",
        },
        "release": {
            "namespace": "demo",
            "name": "demo-app",
        },
    }
    sources_file.write_text(yaml.safe_dump(source_payload, sort_keys=False), encoding="utf-8")
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    created = runner.invoke(
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
            "--infra",
            "mk8s",
        ],
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    mk8s = next(
        item
        for item in persisted["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "mk8s"
    )
    mk8s["inputs"].setdefault("node_group_defaults", {}).setdefault("cpu", {}).pop(
        "boot_disk",
        None,
    )
    persisted["apps"]["charts"] = [
        {
            "id": "demo-app",
            "instance_id": "mk8s",
            "group": "workloads",
            "enabled": True,
            "repo": "https://example.invalid/charts",
            "version": "1.0.0",
            "namespace": "demo",
            "release-name": "demo-app",
            "values": {},
        }
    ]
    config_path.write_text(yaml.safe_dump(persisted, sort_keys=False), encoding="utf-8")

    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()
    reset_component_entry_cache()
    config, _paths = _load_context(config_path)
    loaded = to_plain_data(config)
    loaded_mk8s = next(
        item
        for item in loaded["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "mk8s"
    )
    loaded_cpu_group = loaded_mk8s["inputs"]["node_groups"]["cpu"]
    assert loaded_cpu_group["boot_disk"]["size_gibibytes"] == 93
    assert loaded_cpu_group["boot_disk"]["type"] == "NETWORK_SSD"
    loaded_app = loaded["apps"]["charts"][0]
    assert loaded_app["target_ref"] == "mk8s"

    reloaded_from_disk = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    persisted_mk8s = next(
        item
        for item in reloaded_from_disk["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "mk8s"
    )
    persisted_cpu_group = persisted_mk8s["inputs"]["node_groups"]["cpu"]
    assert persisted_cpu_group["boot_disk"]["size_gibibytes"] == 93
    assert persisted_cpu_group["boot_disk"]["type"] == "NETWORK_SSD"
    assert "target_ref" not in reloaded_from_disk["apps"]["charts"][0]


def test_load_context_rejects_missing_materialized_shared_app_defaults(tmp_path: Path) -> None:
    mk8s_module_dir = tmp_path / "modules" / "mk8s"
    mk8s_module_dir.mkdir(parents=True, exist_ok=True)
    (mk8s_module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
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
                    "infra": {
                        "mk8s": {
                            "source": {
                                "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                                "local": str(mk8s_module_dir),
                            },
                            "ui": {
                                "enabled": True,
                            },
                        }
                    },
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
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "instance_id": "mk8s",
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
            r"apps\.charts\[id=demo-app,instance_id=mk8s\]\.values\.admin\.sshUser "
            r"is required; shared-derived defaults must be materialized into config\.yaml "
            r"during create/component add"
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
    module_dir = tmp_path / "modules" / "mk8s"
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
                    "mk8s": {
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
        if isinstance(item, dict) and item.get("id") == "mk8s"
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


def test_create_does_not_seed_legacy_mk8s_cpu_shortcut_inputs(tmp_path: Path) -> None:
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
    assert "cpu_nodes_count" not in mk8s["inputs"]
    assert "node_groups" not in mk8s["inputs"]


def test_create_materializes_catalog_owned_compute_boot_disk_defaults_into_config(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_compute_boot_disk_sources_file(
        sources_file,
        module_dir=tmp_path / "modules" / "mk8s",
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
            "--infra",
            "mk8s",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    mk8s = next(
        item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") == "mk8s"
    )
    cpu_group = mk8s["inputs"]["node_groups"]["cpu"]
    assert cpu_group["boot_disk"]["size_gibibytes"] == 93
    assert cpu_group["boot_disk"]["type"] == "NETWORK_SSD"


def test_create_materializes_shared_admin_ssh_username_into_config(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(
        deployments_root,
        "--infra",
        "wireguard-gw",
        "--infra",
        "ssh-jumphost",
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(_project_config_path(deployments_root).read_text(encoding="utf-8"))
    public_access_components = {
        item["id"]: item
        for item in payload["infra"]["components"]
        if isinstance(item, dict) and item.get("id") in {"wireguard-gw", "ssh-jumphost"}
    }

    assert public_access_components["wireguard-gw"]["inputs"]["ssh_user_name"] == "ubuntu"
    assert public_access_components["ssh-jumphost"]["inputs"]["ssh_user_name"] == "ubuntu"


def test_create_materializes_shared_app_defaults_into_config(tmp_path: Path) -> None:
    mk8s_module_dir = tmp_path / "modules" / "mk8s"
    mk8s_module_dir.mkdir(parents=True, exist_ok=True)
    (mk8s_module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")
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
                    "infra": {
                        "mk8s": {
                            "source": {
                                "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                                "local": str(mk8s_module_dir),
                            },
                            "ui": {
                                "enabled": True,
                            },
                        }
                    },
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
            "--infra",
            "mk8s",
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
            "demo-jumphost",
            "--config",
            str(config_path),
            "--no-validate-sources",
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
        "--infra",
        "mk8s",
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


def test_create_rejects_enabled_app_without_mk8s_target(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root, "--infra", "none", "--app", "n8n")

    assert result.exit_code == 1, result.output
    assert "Apps are Helm charts and require an enabled MK8s target" in result.output
    assert not _project_config_path(deployments_root).exists()


def test_create_rejects_internal_nccl_test_app_with_validation_guidance(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    result = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "nccl-test")

    assert result.exit_code == 1, result.output
    assert "transient runtime chart" in result.output
    assert "not a selectable persistent app" in result.output
    assert "deploy.targets[].validations.mk8s_gpu.nccl" in result.output
    assert "--app" in result.output
    assert not _project_config_path(deployments_root).exists()


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
    mk8s_row["instance_id"] = "custom-cluster"
    inputs.setdefault("cluster", {})["cluster_name"] = "custom-cluster"
    for chart in payload.get("apps", {}).get("charts", []):
        if isinstance(chart, dict) and chart.get("instance_id") == "mk8s":
            chart["instance_id"] = "custom-cluster"
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict) and target.get("instance_id") == "mk8s":
            target["instance_id"] = "custom-cluster"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    second = _create_non_interactive(
        deployments_root,
        "--force",
        "--infra",
        "mk8s",
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
    assert len(refreshed_components) == 1
    assert refreshed_components[0]["id"] == "mk8s"
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
    before_list = config_path.read_text(encoding="utf-8")
    result = runner.invoke(app, ["component", "list", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert config_path.read_text(encoding="utf-8") == before_list
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
    repeat = runner.invoke(app, ["component", "list", "--config", str(config_path)])
    assert repeat.exit_code == 0, repeat.output
    assert config_path.read_text(encoding="utf-8") == before_list


def test_component_add_uses_selector_first_config_option(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "component",
            "add",
            "infra:vm",
            "--config",
            str(config_path),
            "--no-validate-sources",
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Config file not found: infra:vm" not in result.output
    assert "Added infra components: vm" in result.output


def test_component_add_accepts_scoped_vpc_flags_for_multiple_components(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(
        config_path,
        "infra:vm@worker-vm",
        "infra:ssh-jumphost@jump",
        "--no-interactive",
        "--network-id",
        "infra:vm@worker-vm=vpcnetwork-vm",
        "--subnet-id",
        "infra:vm@worker-vm=vpcsubnet-vm",
        "--network-id",
        "infra:ssh-jumphost@jump=vpcnetwork-ssh",
        "--subnet-id",
        "infra:ssh-jumphost@jump=vpcsubnet-ssh",
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    by_instance = {
        row["instance_id"]: row for row in payload["infra"]["components"] if row.get("enabled")
    }
    assert by_instance["worker-vm"]["inputs"]["network_id"] == "vpcnetwork-vm"
    assert by_instance["worker-vm"]["inputs"]["subnet_id"] == "vpcsubnet-vm"
    assert by_instance["jump"]["inputs"]["network_id"] == "vpcnetwork-ssh"
    assert by_instance["jump"]["inputs"]["subnet_id"] == "vpcsubnet-ssh"


def test_component_add_requires_config_option_instead_of_treating_selector_as_path(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "component",
            "add",
            "infra:vm",
            str(config_path),
            "--no-interactive",
        ],
    )
    assert result.exit_code != 0
    assert "Missing option" in result.output
    assert "--config" in result.output
    assert "Config file not found: infra:vm" not in result.output


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
    mk8s_row["instance_id"] = "custom-cluster"
    inputs.setdefault("cluster", {})["cluster_name"] = "custom-cluster"
    for chart in payload.get("apps", {}).get("charts", []):
        if isinstance(chart, dict) and chart.get("instance_id") == "mk8s":
            chart["instance_id"] = "custom-cluster"
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict) and target.get("instance_id") == "mk8s":
            target["instance_id"] = "custom-cluster"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _component_add(config_path, "managed-postgresql", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Added infra components: managed-postgresql" in result.output
    normalized_output = " ".join(result.output.split())
    assert (
        "Only config.yaml was updated. Existing generated/ artifacts and live resources are "
        "unchanged until you run render and then deploy/destroy as needed."
    ) in normalized_output
    config_arg = shlex.quote(str(config_path.resolve()))
    assert f"Next steps: run `nebius-cxcli validate {config_arg}`, then " in normalized_output
    assert f"`nebius-cxcli render {config_arg}`." in normalized_output

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
    assert mk8s_refreshed["inputs"]["cluster"]["cluster_name"] == "custom-cluster"
    assert mk8s_refreshed["instance_id"] == "custom-cluster"
    assert managed_pg["enabled"] is True


def test_component_add_is_idempotent_and_explicit_resource_names_create_more_rows(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    first = _component_add(config_path, "managed-postgresql", "--no-interactive")
    repeat = _component_add(config_path, "managed-postgresql", "--no-interactive")
    explicit = _component_add(
        config_path,
        "managed-postgresql@analytics-pg",
        "--no-interactive",
    )
    repeat_explicit = _component_add(
        config_path,
        "managed-postgresql@analytics-pg",
        "--no-interactive",
    )

    assert first.exit_code == 0, first.output
    assert repeat.exit_code == 0, repeat.output
    assert explicit.exit_code == 0, explicit.output
    assert repeat_explicit.exit_code == 0, repeat_explicit.output
    assert "Skipped already-enabled components: managed-postgresql" in repeat.output
    assert "Added infra components: managed-postgresql@analytics-pg" in explicit.output
    assert (
        "Skipped already-enabled components: managed-postgresql@analytics-pg"
        in repeat_explicit.output
    )

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
        "analytics-pg",
    ]


def test_component_add_interactive_repeated_infra_selector_prompts_with_next_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "vm")
    assert created.exit_code == 0, created.output

    captured_selected_infra: list[set[str]] = []

    def _fake_run_component_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        captured_selected_infra.append(set(kwargs["selected_infra"]))
        return kwargs["config_yaml"], True

    monkeypatch.setattr(
        cli_module,
        "_run_component_field_wizard",
        _fake_run_component_field_wizard,
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "infra:vm", input_text="\ny\n")

    assert result.exit_code == 0, result.output
    assert "VM name for new infra:vm" in result.output
    assert captured_selected_infra == [{"vm-2"}]
    assert "Added infra components:" not in result.output
    assert "Added apps components:" not in result.output
    assert "Skipped already-enabled components: vm" not in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    vm_rows = [
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "vm"
    ]
    assert [row.get("instance_id") for row in vm_rows] == ["vm", "vm-2"]
    assert [row.get("inputs", {}).get("name") for row in vm_rows] == ["vm", "vm-2"]


def test_component_add_interactive_skipped_new_infra_fields_do_not_persist_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "vm")
    assert created.exit_code == 0, created.output

    def _fake_run_component_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        skipped_components = kwargs["skipped_components"]
        skipped_components.add(("infra", "vm", "vm-2"))
        return kwargs["config_yaml"], True

    monkeypatch.setattr(
        cli_module,
        "_run_component_field_wizard",
        _fake_run_component_field_wizard,
    )

    config_path = _project_config_path(deployments_root)
    original_config = config_path.read_text(encoding="utf-8")
    result = _component_add(config_path, "infra:vm", input_text="\ny\n")

    assert result.exit_code == 0, result.output
    assert "Skipped newly added infra components: vm@vm-2" in result.output
    assert "No component changes applied." in result.output
    assert "Updated:" not in result.output
    assert config_path.read_text(encoding="utf-8") == original_config


def test_component_add_interactive_repeated_infra_selector_uses_prompted_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "vm")
    assert created.exit_code == 0, created.output

    captured_selected_infra: list[set[str]] = []

    def _fake_run_component_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        captured_selected_infra.append(set(kwargs["selected_infra"]))
        return kwargs["config_yaml"], True

    monkeypatch.setattr(
        cli_module,
        "_run_component_field_wizard",
        _fake_run_component_field_wizard,
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "infra:vm", input_text="worker-vm\ny\n")

    assert result.exit_code == 0, result.output
    assert captured_selected_infra == [{"worker-vm"}]
    assert "Added infra components:" not in result.output
    assert "Added apps components:" not in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    vm_rows = [
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "vm"
    ]
    assert [row.get("instance_id") for row in vm_rows] == ["vm", "worker-vm"]
    assert [row.get("inputs", {}).get("name") for row in vm_rows] == ["vm", "worker-vm"]


def test_component_add_interactive_prompts_name_before_scope_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "vm")
    assert created.exit_code == 0, created.output

    captured: list[tuple[str, str, bool]] = []

    def _fail_scope_validation(
        *,
        tenant_id: str,
        project_id: str,
        interactive: bool,
        provider_lookup,
    ):
        _ = provider_lookup
        captured.append((tenant_id, project_id, interactive))
        raise RuntimeError("scope validation unavailable")

    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_tenant_project_ids_or_prompt",
        _fail_scope_validation,
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "infra:vm", input_text="worker-vm\ny\n")

    assert result.exit_code == 1, result.output
    assert "VM name for new infra:vm" in result.output
    assert result.output.index("VM name for new infra:vm") < result.output.index(
        "Validating Nebius tenant/project scope"
    )
    assert "scope validation unavailable" in result.output
    assert captured == [("tenant-123", "project-456", False)]


def test_component_add_infra_only_does_not_resolve_existing_app_chart_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "gateway-helm",
    )
    assert created.exit_code == 0, created.output

    def _fail_chart_dependency_lookup(**_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("infra-only add must not resolve existing app chart dependencies")

    def _fake_run_component_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_helm_chart_dependency_names", _fail_chart_dependency_lookup)
    monkeypatch.setattr(
        cli_module,
        "_run_component_field_wizard",
        _fake_run_component_field_wizard,
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "infra:vm", input_text="worker-vm\ny\n")

    assert result.exit_code == 0, result.output
    assert "Added infra components:" not in result.output
    assert "Added apps components:" not in result.output


def test_component_add_noop_duplicate_skips_provider_scope_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "vm")
    assert created.exit_code == 0, created.output

    def _fail_scope_validation(**_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("no-op duplicate add must not validate provider scope")

    monkeypatch.setattr(
        "nebius_cxcli.cli._validate_tenant_project_ids_or_prompt",
        _fail_scope_validation,
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "infra:vm", "--no-interactive")

    assert result.exit_code == 0, result.output
    assert "Added infra components:" not in result.output
    assert "Added apps components:" not in result.output
    assert "Skipped already-enabled components: vm" in result.output


def test_component_add_non_interactive_infra_selector_suffix_seeds_resource_name(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "infra:vm@worker-vm", "--no-interactive")

    assert result.exit_code == 0, result.output
    assert "Added infra components: vm@worker-vm" in result.output
    assert "Added apps components:" not in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    vm_rows = [
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "vm"
    ]
    assert len(vm_rows) == 1
    assert vm_rows[0]["instance_id"] == "worker-vm"
    assert vm_rows[0]["inputs"]["name"] == "worker-vm"


def test_all_infra_components_can_allocate_additional_instances() -> None:
    for entry in component_entries("infra"):
        payload = {
            "infra": {
                "components": [
                    {
                        "id": entry.id,
                        "instance_id": entry.id,
                        "enabled": True,
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        }

        row = cli_module._append_component_instance_row(
            payload=payload,
            entry=entry,
        )

        assert row["id"] == entry.id
        assert row["instance_id"] == f"{entry.id}-2"
        name_input = cli_module._entry_scalar_resource_name_input(entry)
        if name_input:
            assert cli_module._mapping_path_value(row.get("inputs", {}), name_input) == (
                f"{entry.id}-2"
            )


def test_runtime_validation_rejects_duplicate_infra_instance_id() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "worker-vm",
                    "enabled": True,
                    "inputs": {"name": "worker-vm"},
                },
                {
                    "id": "nfs",
                    "instance_id": "worker-vm",
                    "enabled": True,
                    "inputs": {"name": "worker-vm"},
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="instance_id 'worker-vm' is duplicated"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_mismatched_scalar_name_and_instance_id() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm-2",
                    "enabled": True,
                    "inputs": {"name": "worker-vm"},
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(
        ValueError,
        match=r"instance_id 'vm-2' must match normalized inputs\.name 'worker-vm'",
    ):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_duplicate_scalar_names_for_same_infra_type() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "worker-vm",
                    "enabled": True,
                    "inputs": {"name": "worker-vm"},
                },
                {
                    "id": "vm",
                    "instance_id": "worker-vm-2",
                    "enabled": True,
                    "inputs": {"name": "worker-vm"},
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match=r"inputs\.name 'worker-vm' duplicates"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_does_not_force_collection_identity_to_scalar_name() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mysterybox",
                    "instance_id": "secretstore-alpha",
                    "enabled": True,
                    "inputs": {
                        "secrets": [
                            {
                                "name": "db-credentials",
                                "payload": {"USERNAME": {"text": "app"}},
                            }
                        ]
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_dynamic_payload_structure(payload)

    scalar_collection_payload = {
        "infra": {
            "components": [
                {
                    "id": "mysterybox",
                    "instance_id": "secretstore-alpha",
                    "enabled": True,
                    "inputs": {"secrets": "db-credentials"},
                }
            ]
        },
        "apps": {"charts": []},
    }
    validate_dynamic_payload_structure(scalar_collection_payload)


def test_runtime_validation_rejects_overlapping_planned_vpc_private_cidrs() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {
                            "worker-a": {
                                "name": "worker-a",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/12"],
                            },
                            "worker-b": {
                                "name": "worker-b",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/13"],
                            },
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="Nebius requires subnet CIDR blocks"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_new_planned_vpc_without_network_private_cidr() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {"name": "worker-network"},
                        "subnets": {"worker": {"name": "worker"}},
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="network\\.ipv4_private_cidrs"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_vpc_subnet_network_private_pool_inheritance() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": True,
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="use_network_private_pools must be false"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_non_mapping_vpc_subnet() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {"worker": "worker-subnet"},
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="subnets\\.worker must be a mapping"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_accepts_new_planned_vpc_with_network_private_pool_id() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_pool_ids": ["vpcpool-private"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["10.1.0.0/16"],
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_dynamic_payload_structure(payload)


def test_runtime_validation_accepts_new_planned_vpc_with_pool_id_and_extended_cidr() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_pool_ids": ["vpcpool-private"],
                            "ipv4_private_cidrs": ["192.168.0.0/16"],
                        },
                        "subnets": {
                            "existing-pool-child": {
                                "name": "existing-pool-child",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["10.1.0.0/16"],
                            },
                            "extended-pool-child": {
                                "name": "extended-pool-child",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["192.168.1.0/24"],
                            },
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_dynamic_payload_structure(payload)


def test_runtime_validation_accepts_new_planned_vpc_with_public_pool_id() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                            "ipv4_public_pool_ids": ["vpcpool-public"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/16"],
                                "use_network_public_pools": True,
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_dynamic_payload_structure(payload)


def test_runtime_validation_accepts_new_planned_vpc_with_private_source_pool_id() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_source_pool_id": "vpcpool-source",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/16"],
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_private_source_pool_without_network_cidr() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_source_pool_id": "vpcpool-source",
                            "ipv4_private_pool_ids": ["vpcpool-private"],
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="ipv4_private_source_pool_id"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_planned_subnet_outside_new_vpc_network_range() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["192.168.0.0/16"],
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="must fit inside the VPC network private CIDR range"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_network_private_cidrs_for_existing_vpc_network() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "existing_id": "vpcnetwork-live",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="cannot be set when network\\.existing_id is set"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_overlapping_planned_vpc_cidrs_for_same_live_network() -> None:
    payload = {
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "vpc-a",
                    "enabled": True,
                    "inputs": {
                        "network": {"existing_id": "vpcnetwork-live"},
                        "subnets": {
                            "worker-a": {
                                "name": "worker-a",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/12"],
                            }
                        },
                    },
                },
                {
                    "id": "vpc",
                    "instance_id": "vpc-b",
                    "enabled": True,
                    "inputs": {
                        "network": {"existing_id": "vpcnetwork-live"},
                        "subnets": {
                            "worker-b": {
                                "name": "worker-b",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/13"],
                            }
                        },
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(ValueError, match="same VPC network"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_allows_overlapping_planned_vpc_cidrs_for_different_live_networks() -> (
    None
):
    payload = {
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "vpc-a",
                    "enabled": True,
                    "inputs": {
                        "network": {"existing_id": "vpcnetwork-a"},
                        "subnets": {
                            "worker-a": {
                                "name": "worker-a",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/12"],
                            }
                        },
                    },
                },
                {
                    "id": "vpc",
                    "instance_id": "vpc-b",
                    "enabled": True,
                    "inputs": {
                        "network": {"existing_id": "vpcnetwork-b"},
                        "subnets": {
                            "worker-b": {
                                "name": "worker-b",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/13"],
                            }
                        },
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }

    validate_dynamic_payload_structure(payload)


def _vpc_binding_payload() -> dict:
    return {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vpc",
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "worker-network",
                            "ipv4_private_cidrs": ["172.16.0.0/12"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["172.16.0.0/16"],
                            }
                        },
                    },
                },
                {
                    "id": "vm",
                    "instance_id": "worker",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vm",
                    "inputs": {
                        "parent_id": "project-123",
                        "name": "worker",
                    },
                    "bindings": {
                        "inputs.network_id": {
                            "source_component": "vpc",
                            "source_instance": "worker-vpc",
                            "source_output": "network_id",
                        },
                        "inputs.subnet_id": {
                            "source_component": "vpc",
                            "source_instance": "worker-vpc",
                            "source_output": "subnets",
                            "key": "worker",
                            "attribute": "id",
                        },
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }


def _install_vpc_binding_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda _source: (
            ComponentOutput(
                name="network_id",
                kind="terraform_output",
                source_path="network_id",
                sensitive=False,
            ),
            ComponentOutput(
                name="subnets",
                kind="terraform_output",
                source_path="subnets",
                sensitive=False,
            ),
        ),
    )
    reset_component_sources_cache()
    reset_component_entry_cache()


def test_runtime_validation_accepts_row_level_vpc_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_vpc_binding_output_discovery(monkeypatch)

    validate_dynamic_payload_structure(_vpc_binding_payload())


def test_runtime_validation_rejects_row_binding_literal_conflict() -> None:
    payload = _vpc_binding_payload()
    payload["infra"]["components"][1]["inputs"]["subnet_id"] = "vpcsubnet-live"

    with pytest.raises(ValueError, match=r"bindings\.inputs\.subnet_id conflicts"):
        validate_dynamic_payload_structure(payload)


def test_runtime_validation_rejects_invalid_row_binding_attribute() -> None:
    payload = _vpc_binding_payload()
    payload["infra"]["components"][1]["bindings"]["inputs.subnet_id"]["attribute"] = "0id"

    with pytest.raises(ValueError, match="attribute must be a simple attribute name"):
        validate_dynamic_payload_structure(payload)


def test_component_add_allows_multiple_mk8s_instances(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "mk8s@training-cluster", "--no-interactive")
    repeat = _component_add(config_path, "mk8s@training-cluster", "--no-interactive")

    assert result.exit_code == 0, result.output
    assert "Added infra components: mk8s@training-cluster" in result.output
    assert repeat.exit_code == 0, repeat.output
    assert "Skipped already-enabled components: mk8s@training-cluster" in repeat.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    mk8s_rows = [
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "mk8s"
    ]
    assert [row.get("instance_id") for row in mk8s_rows] == ["mk8s", "training-cluster"]
    assert [row.get("inputs", {}).get("cluster", {}).get("cluster_name") for row in mk8s_rows] == [
        "mk8s",
        "training-cluster",
    ]


def test_component_add_rejects_singular_app_scope_with_plural_hint(tmp_path: Path) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(
        config_path,
        "app:external-secrets@mk8s",
        "--no-interactive",
    )

    assert result.exit_code != 0
    assert "Invalid component selector 'app:external-secrets@mk8s'" in result.output
    assert "plural apps:, not app:" in result.output


def test_component_add_list_remove_bind_app_instance_to_explicit_mk8s_target(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    add_cluster = _component_add(config_path, "mk8s@mk8s-2", "--no-interactive")
    assert add_cluster.exit_code == 0, add_cluster.output

    missing_target = _component_add(config_path, "n8n", "--no-interactive")
    assert missing_target.exit_code != 0
    assert "must be added for an explicit cluster target" in missing_target.output
    assert "n8n@<target-id>" in missing_target.output

    add_app = _component_add(config_path, "n8n@mk8s-2", "--no-interactive")
    assert add_app.exit_code == 0, add_app.output
    assert "Added apps components: n8n@mk8s-2" in add_app.output

    duplicate_app = _component_add(config_path, "n8n@mk8s-2", "--no-interactive")
    assert duplicate_app.exit_code == 0, duplicate_app.output
    assert "Skipped already-enabled components: n8n@mk8s-2" in duplicate_app.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    n8n_rows = [row for row in charts if isinstance(row, dict) and row.get("id") == "n8n"]
    assert len(n8n_rows) == 1
    n8n_row = n8n_rows[0]
    assert n8n_row["instance_id"] == "mk8s-2"
    assert "target_ref" not in n8n_row
    assert n8n_row["release-name"] == "n8n"

    listed = runner.invoke(app, ["component", "list", "--config", str(config_path)])
    assert listed.exit_code == 0, listed.output
    assert "n8n @ mk8s-2 on mk8s-2" in listed.output

    removed = _component_remove(config_path, "n8n@mk8s-2", "--no-interactive")
    assert removed.exit_code == 0, removed.output
    assert "Removed apps components: n8n@mk8s-2" in removed.output


def test_component_add_rejects_app_without_mk8s_target(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "n8n", "--no-interactive")
    assert result.exit_code == 1, result.output
    assert "Apps are Helm charts and require an enabled MK8s target" in result.output


def test_component_add_can_add_first_mk8s_target_and_app_together(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "mk8s", "n8n", "--no-interactive")
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    n8n_row = next(row for row in charts if isinstance(row, dict) and row.get("id") == "n8n")
    assert n8n_row["instance_id"] == "mk8s"
    assert "target_ref" not in n8n_row
    assert n8n_row["release-name"] == "n8n"
    _load_context(config_path)


def test_component_add_auto_enables_nfs_csi_driver_when_nfs_meets_mk8s(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "nfs", "--no-interactive")

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'apps:csi-driver-nfs@mk8s'" in result.output
    assert "Added apps components: csi-driver-nfs@mk8s" in result.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = payload.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    csi_rows = [
        row for row in charts if isinstance(row, dict) and row.get("id") == "csi-driver-nfs"
    ]
    assert len(csi_rows) == 1
    assert csi_rows[0]["instance_id"] == "mk8s"
    assert csi_rows[0]["enabled"] is True
    assert "target_ref" not in csi_rows[0]


def test_component_add_explains_soperator_required_component_selection(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "nvidia-gpu-operator",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "soperator", "--no-interactive")

    assert result.exit_code == 0, result.output
    assert "Adjusted component selection:" in result.output
    assert "'infra:sfs'" in result.output
    assert "'apps:cert-manager'" in result.output
    assert "install_mode=production-cluster creates the complete MK8s+SFS+Soperator bundle" in (
        result.output
    )
    assert "requires cert-manager for webhook certificate automation" in result.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    infra_enabled = _infra_enabled_map(payload)
    apps_enabled = _apps_enabled_map(payload)
    assert infra_enabled["mk8s"] is True
    assert infra_enabled["sfs"] is True
    assert apps_enabled["soperator"] is True
    assert apps_enabled["cert-manager"] is True


def test_component_add_prompts_soperator_profile_before_field_wizard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    events: list[str] = []
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)

    def _capture_install_mode() -> str:
        events.append("install_mode")
        return "production-cluster"

    def _capture_profile() -> str:
        events.append("profile")
        return "nebius-cpu-v1"

    def _capture_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        events.append("field_wizard")
        assert kwargs["skip_soperator_profile_prompt"] is True
        payload = yaml.safe_load(kwargs["config_yaml"]) or {}
        charts = payload.get("apps", {}).get("charts", [])
        soperator = next(
            row for row in charts if isinstance(row, dict) and row.get("id") == "soperator"
        )
        assert soperator["install_mode"] == "production-cluster"
        assert soperator["profile"] == "nebius-cpu-v1"
        return kwargs["config_yaml"], True

    monkeypatch.setattr(cli_module, "_prompt_soperator_install_mode", _capture_install_mode)
    monkeypatch.setattr(cli_module, "_prompt_soperator_profile", _capture_profile)
    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _capture_field_wizard)

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "soperator")

    assert result.exit_code == 0, result.output
    assert events == ["install_mode", "profile", "field_wizard"]


def test_component_add_does_not_expand_existing_soperator_onboarding_selection(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
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
                    "id": "soperator",
                    "instance_id": "external-cluster",
                    "enabled": True,
                    "install_mode": "onboard-existing-cluster",
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "values": {},
                },
                {
                    "id": "cert-manager",
                    "instance_id": "external-cluster",
                    "enabled": True,
                    "namespace": "cert-manager",
                    "release-name": "cert-manager",
                    "values": {},
                },
            ]
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "external-cluster",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "access": "external",
                    "kube_context": "external-context",
                    "inventory": {"node_groups": {}},
                    "soperator_onboarding": {
                        "accepted": True,
                        "analysis_fingerprint": "",
                        "state": "vanilla-mk8s",
                        "actions": ["install-soperator"],
                        "storage_mode": "adopt-existing-storage",
                    },
                }
            ]
        },
    }
    cli_module._refresh_soperator_onboarding_fingerprints(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _component_add(config_path, "n8n@external-cluster", "--no-interactive")

    assert result.exit_code == 0, result.output
    assert "install_mode=production-cluster" not in result.output
    assert "'infra:mk8s'" not in result.output
    assert "'infra:sfs'" not in result.output
    assert "'apps:cert-manager'" not in result.output

    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["infra"]["components"] == []
    charts = updated["apps"]["charts"]
    assert ("n8n", "external-cluster") in {
        (row.get("id"), row.get("instance_id")) for row in charts if isinstance(row, dict)
    }


def test_component_add_added_app_labels_prefer_target_scoped_rows() -> None:
    payload = {
        "deploy": {"targets": [{"instance_id": "cluster2"}]},
        "apps": {
            "charts": [
                {
                    "id": "nvidia-gpu-operator",
                    "instance_id": "cluster2",
                    "enabled": True,
                },
                {
                    "id": "nvidia-network-operator",
                    "instance_id": "cluster2",
                    "enabled": True,
                },
            ]
        },
    }

    assert cli_module._target_aware_added_app_labels(
        payload,
        ["nvidia-gpu-operator", "nvidia-network-operator"],
    ) == [
        "nvidia-gpu-operator@cluster2",
        "nvidia-network-operator@cluster2",
    ]


def test_mk8s_node_group_shape_prompt_refreshes_boot_disk_size() -> None:
    previous_inputs = {
        "node_groups": {
            "system": {
                "node_count": 1,
                "gpu": False,
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "boot_disk": {
                    "type": "NETWORK_SSD",
                    "size_gibibytes": 64,
                },
            }
        }
    }
    payload = {
        "client_info": {
            "nebius": {"project_id": "project-456"},
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": yaml.safe_load(yaml.safe_dump(previous_inputs)),
                }
            ]
        },
        "apps": {"charts": []},
    }
    payload["infra"]["components"][0]["inputs"]["node_groups"]["system"]["preset"] = "32vcpu-128gb"
    entry = cli_module.ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
    )

    cli_module._maybe_refresh_compute_boot_disk_defaults_after_shape_change(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_groups.system.preset",
        previous_component_inputs=previous_inputs,
        provider_lookup=None,
    )

    boot_disk = payload["infra"]["components"][0]["inputs"]["node_groups"]["system"]["boot_disk"]
    assert boot_disk["type"] == "NETWORK_SSD"
    assert boot_disk["size_gibibytes"] == 93


def test_component_add_validates_late_auto_enabled_nfs_csi_driver_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "none",
        "--no-validate-config",
    )
    assert created.exit_code == 0, created.output

    source_validation_calls: list[dict[str, object]] = []

    def _capture_source_validation(**kwargs: object) -> None:
        selected_app_ids = kwargs.get("selected_app_ids")
        if isinstance(selected_app_ids, set):
            kwargs = {**kwargs, "selected_app_ids": set(selected_app_ids)}
        source_validation_calls.append(dict(kwargs))

    monkeypatch.setattr(
        cli_module,
        "_validate_component_sources_or_raise",
        _capture_source_validation,
    )

    config_path = _project_config_path(deployments_root)
    result = runner.invoke(
        app,
        [
            "component",
            "add",
            "nfs",
            "--config",
            str(config_path),
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert source_validation_calls[0] == {"selected_app_ids": set()}
    assert {
        "selected_app_ids": {"csi-driver-nfs"},
        "include_infra": False,
    } in source_validation_calls


def test_component_remove_cluster_target_cascades_target_bound_apps(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "n8n")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_remove(config_path, "mk8s", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Removed infra components: mk8s" in result.output
    assert "Removed apps components: n8n@mk8s" in result.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["infra"]["components"] == []
    assert payload["apps"]["charts"] == []
    assert payload.get("deploy", {}).get("targets", []) == []
    _load_context(config_path)


def test_component_remove_cluster_instance_cascades_only_that_target(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "none")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    add_cluster = _component_add(config_path, "mk8s@mk8s-2", "--no-interactive")
    assert add_cluster.exit_code == 0, add_cluster.output
    add_app = _component_add(config_path, "n8n@mk8s-2", "--no-interactive")
    assert add_app.exit_code == 0, add_app.output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["deploy"] = {
        "targets": [
            {"instance_id": "mk8s"},
            {"instance_id": "mk8s-2"},
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    result = _component_remove(config_path, "infra:mk8s-2", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Removed infra components: mk8s@mk8s-2" in result.output
    assert "Removed apps components: n8n@mk8s-2" in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    mk8s_rows = [
        row
        for row in refreshed["infra"]["components"]
        if isinstance(row, dict) and row.get("id") == "mk8s"
    ]
    assert [row["instance_id"] for row in mk8s_rows] == ["mk8s"]
    assert all(
        not (isinstance(row, dict) and row.get("instance_id") == "mk8s-2")
        for row in refreshed["apps"]["charts"]
    )
    assert [row["instance_id"] for row in refreshed["deploy"]["targets"]] == ["mk8s"]
    _load_context(config_path)


def test_component_add_wizard_tracks_target_bound_app_by_chart_and_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "gateway-helm",
    )
    assert created.exit_code == 0, created.output

    captured_selected_apps: list[set[str]] = []
    monkeypatch.setattr(cli_module, "_wizard_continue_phase", lambda *_args, **_kwargs: True)

    def _fake_run_component_field_wizard(
        *,
        config_yaml: str,
        selected_infra: set[str],
        selected_apps: set[str],
        infra_entries,
        app_entries,
        provider_lookup=None,
        **_kwargs,
    ) -> tuple[str, bool]:
        _ = selected_infra, infra_entries, app_entries, provider_lookup
        captured_selected_apps.append(set(selected_apps))
        return config_yaml, True

    monkeypatch.setattr(cli_module, "_run_component_field_wizard", _fake_run_component_field_wizard)

    config_path = _project_config_path(deployments_root)
    result = _component_add(config_path, "n8n")
    assert result.exit_code == 0, result.output
    assert captured_selected_apps == [{"n8n@mk8s"}]
    assert "Added infra components:" not in result.output
    assert "Added apps components:" not in result.output


def test_component_add_interactive_mk8s_target_scopes_auto_enabled_apps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_ids = ("nebius-observability-agent", "grafana", "gateway-helm")
    infra_entries = (
        cli_module.ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.components.mk8s",
            description="Managed Kubernetes",
            source="../../platform-infra/modules/mk8s",
            handoff=object(),  # only presence is needed for target-ref discovery
            wizard_fields={
                "deploy.targets[].observability.enabled": {"type_hint": "bool"},
                "deploy.targets[].observability.kubernetes.logs.enabled": {"type_hint": "bool"},
            },
        ),
    )
    app_entries = tuple(
        cli_module.ComponentEntry(
            id=app_id,
            scope="apps",
            config_path=f"apps.charts.{app_id}",
            description=app_id,
            chart_name=app_id,
            default_namespace="observability",
            default_release_name=app_id,
        )
        for app_id in app_ids
    )

    def _component_entries(scope: str, **_kwargs):  # type: ignore[no-untyped-def]
        return infra_entries if scope == "infra" else app_entries

    monkeypatch.setattr(cli_module, "component_entries", _component_entries)
    monkeypatch.setattr(deploy_targets_module, "component_entries", _component_entries)

    phase_prompts: list[str] = []

    def _wizard_phase(label: str, *, default: bool = True, allow_back: bool = False) -> bool:
        _ = default, allow_back
        phase_prompts.append(label)
        return label in {
            "Add selected components to config.yaml now?",
            "Configure 'mk8s@cluster2' component fields now?",
        }

    monkeypatch.setattr(cli_module, "_wizard_continue_phase", _wizard_phase)

    prompted_paths: list[str] = []

    def _prompt_scalar(path_label: str, current: object, **_kwargs) -> tuple[object, bool]:
        prompted_paths.append(path_label)
        if path_label == "deploy.targets[1].observability.enabled":
            return True, False
        if path_label == "deploy.targets[1].observability.kubernetes.logs.enabled":
            return True, False
        return current, False

    monkeypatch.setattr(cli_module, "_prompt_scalar_override", _prompt_scalar)

    source_validation_calls: list[tuple[set[str] | None, bool]] = []

    def _capture_source_validation(
        *,
        selected_app_ids: set[str] | None = None,
        include_infra: bool = True,
    ) -> None:
        source_validation_calls.append(
            (set(selected_app_ids) if selected_app_ids is not None else None, include_infra)
        )

    monkeypatch.setattr(
        cli_module,
        "_validate_component_sources_or_raise",
        _capture_source_validation,
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
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
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "cluster1",
                            "enabled": True,
                            "inputs": {"cluster": {"cluster_name": "cluster1"}},
                        }
                    ]
                },
                "apps": {
                    "charts": [
                        {
                            "id": app_id,
                            "instance_id": "cluster1",
                            "enabled": True,
                            "namespace": "observability",
                            "release-name": app_id,
                            "values": {},
                        }
                        for app_id in app_ids
                    ]
                },
                "deploy": {
                    "targets": [
                        {
                            "instance_id": "cluster1",
                            "observability": {
                                "enabled": True,
                                "kubernetes": {"logs": {"enabled": True}},
                            },
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "component",
            "add",
            "mk8s@cluster2",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "list index out of range" not in result.output
    assert "Component selections:" in result.output
    assert "mk8s@cluster2" in result.output
    assert "nebius-observability-agent on cluster2" in result.output
    assert "grafana on cluster2" in result.output
    assert "gateway-helm on cluster2" in result.output
    assert "nebius-observability-agent on cluster1" not in result.output
    assert "grafana on cluster1" not in result.output
    assert "gateway-helm on cluster1" not in result.output
    assert "Added infra components:" not in result.output
    assert "Added apps components:" not in result.output
    assert "deploy.targets[1].observability.enabled" in prompted_paths
    assert not any(" on cluster1" in label for label in phase_prompts)
    assert source_validation_calls == [
        (set(), True),
        (set(app_ids), False),
    ]

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    app_targets = sorted(
        (row["id"], row["instance_id"])
        for row in refreshed["apps"]["charts"]
        if isinstance(row, dict) and row.get("id") in app_ids and row.get("enabled") is True
    )
    assert app_targets == sorted(
        (app_id, target_ref) for target_ref in ("cluster1", "cluster2") for app_id in app_ids
    )


def test_component_add_interactive_prompts_for_new_component_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none")
    assert created.exit_code == 0, created.output

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("name",),
    )

    config_path = _project_config_path(deployments_root)
    result = _component_add(
        config_path,
        input_text="managed-postgresql\n\ndemo-pg\ny\n\n",
    )
    assert result.exit_code == 0, result.output
    assert "Select apps components to add too?" in result.output
    assert "Select apps components (comma-separated ids or indexes)" not in result.output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    managed_pg = next(
        row
        for row in components
        if isinstance(row, dict) and str(row.get("id", "")).strip() == "managed-postgresql"
    )
    assert managed_pg["inputs"]["name"] == "demo-pg"


def test_component_add_does_not_write_when_quit_leaves_required_fields_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "none", "--app", "none")
    assert created.exit_code == 0, created.output

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("name", "parent_id", "network_id"),
    )

    def _fake_run_component_field_wizard(**kwargs):  # type: ignore[no-untyped-def]
        return kwargs["config_yaml"], False

    monkeypatch.setattr(
        cli_module,
        "_run_component_field_wizard",
        _fake_run_component_field_wizard,
    )

    config_path = _project_config_path(deployments_root)
    original_config = config_path.read_text(encoding="utf-8")
    result = _component_add(config_path, "managed-postgresql", input_text="\ny\n")

    assert result.exit_code == 1, result.output
    normalized = _normalized_cli_output(result.output)
    assert "Wizard stopped before all required fields were filled." in normalized
    assert "No config.yaml changes were written." in normalized
    assert "Existing project preserved:" in result.output
    assert config_path.read_text(encoding="utf-8") == original_config


def test_component_add_requeues_provider_dependent_module_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)
    config_path = deployments_root / "tenant-123" / "project-456" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    module_specs = (
        SimpleNamespace(
            name="name",
            required=True,
            type_hint="string",
            has_default=False,
            default=None,
        ),
        SimpleNamespace(
            name="parent_id",
            required=True,
            type_hint="string",
            has_default=False,
            default=None,
        ),
        SimpleNamespace(
            name="network_id",
            required=True,
            type_hint="string",
            has_default=False,
            default=None,
        ),
        SimpleNamespace(
            name="subnet_id",
            required=True,
            type_hint="string",
            has_default=False,
            default=None,
        ),
        SimpleNamespace(
            name="platform",
            required=True,
            type_hint="string",
            has_default=False,
            default=None,
        ),
        SimpleNamespace(
            name="preset",
            required=True,
            type_hint="string",
            has_default=False,
            default=None,
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: module_specs)
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: tuple(item.name for item in module_specs),
    )

    def _fake_dynamic_choices(
        *,
        payload,
        entry,
        full_path_label,
        provider_lookup,
    ):
        _ = entry, provider_lookup
        if full_path_label.endswith(".inputs.network_id"):
            return [cli_module.OptionChoice(value="vpcnetwork-1", label="default network")]
        if full_path_label.endswith(".inputs.subnet_id"):
            assert cli_module._read_payload_field(payload, "infra.components[0].inputs.network_id")
            return [cli_module.OptionChoice(value="vpcsubnet-1", label="default subnet")]
        if full_path_label.endswith(".inputs.platform"):
            return [cli_module.OptionChoice(value="cpu-d3", label="cpu-d3")]
        if full_path_label.endswith(".inputs.preset"):
            assert cli_module._read_payload_field(payload, "infra.components[0].inputs.platform")
            return [cli_module.OptionChoice(value="4vcpu-16gb", label="4vcpu-16gb")]
        return []

    def _fake_allowed_values(
        *,
        payload,
        entry,
        full_path_label,
        provider_lookup,
    ):
        _ = payload, entry, provider_lookup
        if full_path_label.endswith(".inputs.network_id"):
            return {"vpcnetwork-1"}, ("project_networks",)
        if full_path_label.endswith(".inputs.subnet_id"):
            return {"vpcsubnet-1"}, ("project_subnets",)
        if full_path_label.endswith(".inputs.platform"):
            return {"cpu-d3"}, ("compute_platforms",)
        if full_path_label.endswith(".inputs.preset"):
            return {"4vcpu-16gb"}, ("compute_platform_presets",)
        return set(), ()

    monkeypatch.setattr(cli_module, "_resolve_dynamic_field_choices", _fake_dynamic_choices)
    monkeypatch.setattr(cli_module, "_provider_allowed_values_for_field", _fake_allowed_values)

    result = _component_add(
        config_path,
        "wireguard-gw",
        input_text="wg-gw\ny\ny\n\n\n\n\n",
    )

    assert result.exit_code == 0, result.output
    assert "infra.components[0].inputs.preset" in result.output
    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = refreshed.get("infra", {}).get("components", [])
    assert isinstance(components, list)
    wireguard = next(
        item for item in components if isinstance(item, dict) and item.get("id") == "wireguard-gw"
    )
    assert wireguard["instance_id"] == "wg-gw"
    assert wireguard["inputs"]["name"] == "wg-gw"
    assert wireguard["inputs"]["network_id"] == "vpcnetwork-1"
    assert wireguard["inputs"]["subnet_id"] == "vpcsubnet-1"
    assert wireguard["inputs"]["platform"] == "cpu-d3"
    assert wireguard["inputs"]["preset"] == "4vcpu-16gb"


def test_component_add_mysterybox_interactive_preserves_existing_mk8s_target(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    components = payload["infra"]["components"]
    mk8s = next(row for row in components if row["id"] == "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s_inputs = mk8s.setdefault("inputs", {})
    mk8s_inputs.update(
        {
            "cluster": {"cluster_name": "cluster1"},
            "node_groups": {
                "worker": {
                    "node_count": 1,
                    "gpu": True,
                    "platform": "gpu-h100-sxm",
                    "preset": "8gpu-128vcpu-1600gb",
                    "gpu_cluster_key": "workers",
                }
            },
            "gpu_clusters": {"workers": {"infiniband_fabric": "fabric-6"}},
        }
    )
    for chart in payload.get("apps", {}).get("charts", []):
        if isinstance(chart, dict) and chart.get("instance_id") == "mk8s":
            chart["instance_id"] = "cluster1"
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict) and target.get("instance_id") == "mk8s":
            target["instance_id"] = "cluster1"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    secrets = [
        {
            "name": "app-runtime",
            "version_id": "n/a",
            "payload": {
                "MESSAGE": {"type": "text"},
                "COLOR": {"type": "text"},
            },
        }
    ]
    result = _component_add(
        config_path,
        "mysterybox",
        input_text="y\ny\n\n" + json.dumps(secrets) + "\n\n",
    )

    assert result.exit_code == 0, result.output
    assert "'apps:external-secrets@cluster1'" in result.output
    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    infra_ids = {
        row["id"]
        for row in refreshed.get("infra", {}).get("components", [])
        if isinstance(row, dict)
    }
    assert {"mk8s", "mysterybox"} <= infra_ids
    external_secrets = next(
        row
        for row in refreshed.get("apps", {}).get("charts", [])
        if isinstance(row, dict) and row.get("id") == "external-secrets"
    )
    assert external_secrets["instance_id"] == "cluster1"


def test_component_add_noninteractive_adds_app_chart_and_preserves_existing_values(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "gateway-helm",
    )
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
    assert "Added apps components: n8n@mk8s" in result.output
    assert "Added infra components:" not in result.output

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
        "--infra",
        "mk8s",
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

    created = _create_non_interactive(
        deployments_root,
        "--infra",
        "mk8s",
        "--app",
        "gateway-helm",
    )
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    result = _component_remove(config_path, "gateway-helm", "--no-interactive")
    assert result.exit_code == 0, result.output
    assert "Removed apps components: gateway-helm@mk8s" in result.output
    normalized_output = " ".join(result.output.split())
    assert (
        "Only config.yaml was updated. Existing generated/ artifacts and live resources are "
        "unchanged until you run render and then deploy/destroy as needed."
    ) in normalized_output
    config_arg = shlex.quote(str(config_path.resolve()))
    assert f"Next steps: run `nebius-cxcli validate {config_arg}`, then " in normalized_output
    assert f"`nebius-cxcli render {config_arg}`." in normalized_output

    refreshed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    charts = refreshed.get("apps", {}).get("charts", [])
    assert isinstance(charts, list)
    assert all(
        not (isinstance(row, dict) and str(row.get("id", "")).strip().lower() == "gateway-helm")
        for row in charts
    )

    repeat = _component_remove(config_path, "gateway-helm", "--no-interactive")
    assert repeat.exit_code == 0, repeat.output
    assert "Skipped already-absent component: gateway-helm" in repeat.output
    assert "No components selected for remove." in repeat.output
    repeat_output = " ".join(repeat.output.split())
    assert f"Next steps: run `nebius-cxcli validate {config_arg}`, then " in repeat_output


def test_component_remove_requires_row_id_when_multiple_instances_match(
    tmp_path: Path,
) -> None:
    deployments_root = tmp_path / "deployments"
    deployments_root.mkdir(parents=True, exist_ok=True)

    created = _create_non_interactive(deployments_root, "--infra", "mk8s")
    assert created.exit_code == 0, created.output

    config_path = _project_config_path(deployments_root)
    assert _component_add(config_path, "managed-postgresql", "--no-interactive").exit_code == 0
    assert (
        _component_add(
            config_path,
            "managed-postgresql@analytics-pg",
            "--no-interactive",
        ).exit_code
        == 0
    )

    ambiguous = _component_remove(config_path, "managed-postgresql", "--no-interactive")
    assert ambiguous.exit_code == 1, ambiguous.output
    normalized_output = " ".join(ambiguous.output.split())
    assert "matches multiple enabled instances" in normalized_output
    assert "Available rows:" in normalized_output

    targeted = _component_remove(config_path, "analytics-pg", "--no-interactive")
    assert targeted.exit_code == 0, targeted.output
    assert "managed-postgresql@analytics-pg" in targeted.output

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

    first = _create_non_interactive(deployments_root, "--infra", "mk8s", "--app", "n8n")
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

    second = _create_non_interactive(
        deployments_root,
        "--force",
        "--infra",
        "mk8s",
        "--app",
        "n8n",
    )
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
    assert "Send deploy report email" in content
    assert "vars.SMTP_HOST != ''" not in content
    assert "SMTP_HOST: ${{ vars.SMTP_HOST }}" in content
    assert "SMTP_USERNAME: ${{ secrets.SMTP_USERNAME }}" in content
    assert 'nebius-cxcli email "${{ matrix.config }}"' in content
    assert 'nebius-cxcli email "${{ matrix.generated }}"' not in content
    assert (
        f"NEBIUS_CXCLI_REF: ${{{{ vars.NEBIUS_CXCLI_REF || '{cli_module.default_cli_ref()}' }}}}"
        in content
    )


def test_bootstrap_ci_repo_root_deployments_uses_clean_generated_glob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    create_result = _create_non_interactive(repo_root)
    assert create_result.exit_code == 0, create_result.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(repo_root)
    bootstrap = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])
    assert bootstrap.exit_code == 0, bootstrap.output

    workflow = repo_root / ".github" / "workflows" / "nebius-deployments.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "NEBIUS_DISCOVER_TARGET: ." in content
    assert '- "*/*/generated/**"' in content
    assert "**/./*/*/generated/**" not in content


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
    assert "Managed by `nebius-cxcli`" in content
    assert "Keep config.yaml and generated/nebius-cxcli-manifest.json versioned" in content
    assert "tfvars duplicates recreated from the generated manifest" in content
    assert "*/*/wireguard-clients/" in content
    assert "*/*/generated/infra/.terraform/" in content
    assert "*/*/generated/infra/crash.*.log" in content
    assert "*/*/generated/infra/terraform.auto.tfvars.json" in content
    assert ".coverage" not in content
    assert "*.tgz" not in content


def test_bootstrap_ci_rejects_config_under_nested_deployments_root_with_managed_parent_gitignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "customer-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    deployments_root = repo_root / "deployment-examples"
    nested_root = deployments_root / "post-sales"
    nested_root.mkdir(parents=True, exist_ok=True)
    create_nested = _create_non_interactive(nested_root)
    assert create_nested.exit_code == 0, create_nested.output

    deployments_root.mkdir(parents=True, exist_ok=True)
    create_root = _create_non_interactive(deployments_root)
    assert create_root.exit_code == 0, create_root.output
    _mock_bootstrap_ci_github_sync(monkeypatch)

    config_path = _project_config_path(nested_root)
    result = runner.invoke(app, ["bootstrap-ci", str(config_path), "--no-auth-bootstrap"])

    assert result.exit_code == 1, result.output
    assert "nested under existing cxcli-managed deployments root" in " ".join(result.output.split())
    assert not (repo_root / ".github" / "workflows" / "nebius-deployments.yml").exists()


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
    config_path = _project_config_path(deployments_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(deployments_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": (
                    f"deployments/{_tenant_folder_name()}/{_project_folder_name()}/config.yaml"
                ),
                "generated": (
                    f"deployments/{_tenant_folder_name()}/{_project_folder_name()}/generated"
                ),
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
    config_path = _project_config_path(deployments_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(deployments_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": (
                    f"deployments/{_tenant_folder_name()}/{_project_folder_name()}/config.yaml"
                ),
                "generated": (
                    f"deployments/{_tenant_folder_name()}/{_project_folder_name()}/generated"
                ),
                "config_changed": False,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }


def test_discover_accepts_repo_root_deployment_scope(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git_init(repo_root)

    config_path = _project_config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(repo_root), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": f"{_tenant_folder_name()}/{_project_folder_name()}/config.yaml",
                "generated": f"{_tenant_folder_name()}/{_project_folder_name()}/generated",
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
    generated_dir = _project_dir(deployments_root) / "generated"
    config_path = generated_dir.parent / "config.yaml"
    generated_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_discover_config_payload(), encoding="utf-8")

    result = runner.invoke(app, ["discover", str(generated_dir), "--all"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload == {
        "include": [
            {
                "config": (
                    f"deployments/{_tenant_folder_name()}/{_project_folder_name()}/config.yaml"
                ),
                "generated": (
                    f"deployments/{_tenant_folder_name()}/{_project_folder_name()}/generated"
                ),
                "config_changed": False,
                "generated_changed": False,
                "github_environment": "client-a-project-456",
            }
        ]
    }


def test_reconcile_observability_gpu_node_labels_uses_catalog_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "observability_gpu_node_label_reconciliation",
        lambda _config: SimpleNamespace(
            enabled=True,
            selector=(("nebius.com/gpu", "true"),),
            labels=(
                ("nvidia.com/gpu.deploy.operands", "true"),
                ("nvidia.com/gpu.deploy.dcgm-exporter", "true"),
                ("nvidia.com/gpu.deploy.operator-validator", "true"),
                ("nvidia.com/gpu.deploy.device-plugin", "false"),
            ),
        ),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="node/gpu-a labeled\n", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    cli_module._reconcile_observability_gpu_node_labels(
        {"deploy": {"observability": {"enabled": True}}},
        extra_env={"KUBECONFIG": "/tmp/kubeconfig", "NEBIUS_IAM_TOKEN": "token-123"},
    )

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        "kubectl",
        "label",
        "nodes",
        "-l",
        "nebius.com/gpu=true",
        "nvidia.com/gpu.deploy.operands=true",
        "nvidia.com/gpu.deploy.dcgm-exporter=true",
        "nvidia.com/gpu.deploy.operator-validator=true",
        "nvidia.com/gpu.deploy.device-plugin=false",
        "--overwrite",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 120
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["KUBECONFIG"] == "/tmp/kubeconfig"
    assert env["NEBIUS_IAM_TOKEN"] == "token-123"


def test_reconcile_observability_gpu_node_labels_noops_without_enabled_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "observability_gpu_node_label_reconciliation",
        lambda _config: SimpleNamespace(enabled=False, selector=(), labels=()),
    )
    monkeypatch.setattr(
        cli_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("kubectl should not be called"),
    )

    cli_module._reconcile_observability_gpu_node_labels(
        {"deploy": {"observability": {"enabled": False}}},
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )
