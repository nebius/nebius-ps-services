from __future__ import annotations

import json
import os
import re
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli
import nebius_cxcli.component_sources as component_sources
import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.cluster_handoffs import Handoff
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.deploy_targets import flux_target_dir
from nebius_cxcli.email_settings import EmailSettings
from nebius_cxcli.inventory_ops import write_inventory as write_inventory_artifacts
from nebius_cxcli.managed_tools import FLUX_VERSION_ENV, TERRAFORM_VERSION_ENV
from nebius_cxcli.paths import (
    ProjectPaths,
    resolve_generated_flux_paths,
    resolve_generated_infra_paths,
)
from nebius_cxcli.quota_checks import (
    QuotaCheck,
    QuotaCoverageGap,
    QuotaReport,
    QuotaRequestChange,
    QuotaRequestFailure,
    QuotaRequestResult,
    RegionalQuotaAvailability,
)

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_RUNTIME_AUTH_ENV_KEYS = (
    "NEBIUS_AUTH_CREDENTIALS_FILE",
    "NEBIUS_SA_ID",
    "NEBIUS_AUTH_PUBLIC_KEY_ID",
    "NEBIUS_AUTH_PRIVATE_KEY_FILE",
    "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    "NEBIUS_S3_ACCESS_KEY_ID",
    "NEBIUS_S3_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
)


def _empty_quota_report() -> cli.QuotaReport:
    return cli.QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
    )


@pytest.fixture(autouse=True)
def _reset_component_sources_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", raising=False)
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
            ComponentOutput(
                name="instance_id",
                kind="terraform_output",
                source_path="instance_id",
                sensitive=False,
            ),
        ),
    )
    monkeypatch.setattr(cli, "assess_live_quotas", lambda *_args, **_kwargs: _empty_quota_report())
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()


def _plain_output(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _clear_runtime_auth_env() -> None:
    for name in _RUNTIME_AUTH_ENV_KEYS:
        os.environ.pop(name, None)


def _fake_paths(tmp_path: Path) -> ProjectPaths:
    project_dir = tmp_path / "deployments" / "tenant-name-example" / "project-name-example"
    project_dir.mkdir(parents=True, exist_ok=True)
    return ProjectPaths(
        config_path=project_dir / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path / "deployments",
        project_dir=project_dir,
        generated_dir=project_dir / "generated",
        infra_dir=project_dir / "generated" / "infra",
        flux_dir=project_dir / "generated" / "flux",
        inventory_dir=project_dir / "generated" / "inventory",
        path_tenant_folder="tenant-name-example",
        path_project_folder="project-name-example",
    )


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


def _target_paths(paths: ProjectPaths, *, target_ref: str = "mk8s") -> ProjectPaths:
    return replace(paths, flux_dir=flux_target_dir(paths, target_ref))


def test_render_overwrite_warning_never_mentions_flux_system(tmp_path: Path) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.generated_dir / "dummy.txt").write_text("data\n", encoding="utf-8")

    warning_without_bootstrap = cli._render_overwrite_warning(fake_paths)

    assert warning_without_bootstrap is not None
    assert "Render will overwrite existing generated artifacts under" in warning_without_bootstrap
    assert "generated/flux/flux-system" not in warning_without_bootstrap

    bootstrap_dir = fake_paths.flux_dir / "flux-system"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "gotk-sync.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8"
    )

    warning_with_bootstrap = cli._render_overwrite_warning(fake_paths)

    assert warning_with_bootstrap is not None
    assert "generated/flux/flux-system" not in warning_with_bootstrap


def test_render_overwrite_warning_treats_removed_inventory_scaffold_as_meaningful(
    tmp_path: Path,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.inventory_dir / "inventory.md").write_text(
        "# Inventory\n\nGenerated by `nebius-cxcli inventory write`.\n",
        encoding="utf-8",
    )

    warning = cli._render_overwrite_warning(fake_paths)

    assert warning is not None
    assert "Render will overwrite existing generated artifacts under" in warning


def test_validate_command_runs_strict_checks_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    strict_called: dict[str, bool] = {"called": False}
    quota_called: dict[str, object] = {}
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(
        cli,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr(
        cli,
        "_validate_component_dependencies",
        lambda _cfg, *, chart_meta_cache=None: [],
    )
    monkeypatch.setattr(
        cli,
        "rendered_module_sources",
        lambda config, *, source_profile: (
            captured.update({"config": config, "source_profile": source_profile}) or ()
        ),
    )

    def _fake_strict(
        _cfg: object,
        *,
        chart_meta_cache: object | None = None,
        include_common_checks: bool = True,
    ) -> None:
        strict_called["called"] = True
        assert include_common_checks is False

    monkeypatch.setattr(cli, "_validate_strict_config", _fake_strict)
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_live_quota_issues",
        lambda config, *, phase: (
            quota_called.update({"config": config, "phase": phase}) or _empty_quota_report()
        ),
    )
    monkeypatch.setattr(
        cli,
        "_validation_scope_summary_lines",
        lambda *_args, **_kwargs: [
            "Validated scope:",
            "  infra:",
            "    - Compute: mk8s",
            "  apps:",
            "    - none",
        ],
    )

    result = runner.invoke(cli.app, ["validate", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Runtime validation:" in output
    assert "Load config and component catalog" in output
    assert "Validate active component sources" in output
    assert "Validate component dependencies" in output
    assert "Validate Terraform module inputs" in output
    assert "Validate strict deployment readiness" in output
    assert "Validate MK8s network preflight" in output
    assert "Validate live Nebius quota/capacity" in output
    assert "Validated scope:" in output
    assert "infra:" in output
    assert "Compute: mk8s" in output
    assert "apps:" in output
    assert "- none" in output
    assert "Valid:" in output
    assert strict_called["called"] is True
    assert captured["source_profile"] == SourceProfile.PORTABLE
    assert quota_called["config"] is captured["config"]
    assert quota_called["phase"] == "validate"


def test_validate_command_rejects_removed_strict_flag(
    tmp_path: Path,
) -> None:
    result = runner.invoke(cli.app, ["validate", "--strict", str(tmp_path / "config.yaml")])

    assert result.exit_code != 0
    assert "No such option: --strict" in _plain_output(result.output)


def test_validation_scope_summary_lines_group_enabled_components_concisely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "component_entries",
        lambda scope, *, source_profile=None: (
            (
                ComponentEntry(
                    id="mk8s",
                    scope="infra",
                    config_path="infra.mk8s",
                    description="Managed Kubernetes",
                    group="Compute",
                ),
            )
            if scope == "infra"
            else (
                ComponentEntry(
                    id="nvidia-gpu-operator",
                    scope="apps",
                    config_path="apps.platform.nvidia-gpu-operator",
                    description="GPU Operator",
                    group="Platform",
                ),
                ComponentEntry(
                    id="nvidia-network-operator",
                    scope="apps",
                    config_path="apps.platform.nvidia-network-operator",
                    description="Network Operator",
                    group="Platform",
                ),
            )
        ),
    )

    lines = cli._validation_scope_summary_lines(
        {
            "infra": {
                "components": [
                    {"id": "mk8s", "enabled": True},
                ]
            },
            "apps": {
                "charts": [
                    {"id": "nvidia-gpu-operator", "enabled": True, "group": "platform"},
                    {"id": "nvidia-network-operator", "enabled": True, "group": "platform"},
                ]
            },
        },
        source_profile=SourceProfile.PORTABLE,
    )

    assert lines == [
        "Validated scope:",
        "  infra:",
        "    - Compute: mk8s",
        "  apps:",
        "    - Platform: nvidia-gpu-operator, nvidia-network-operator",
    ]


def test_validate_command_fails_on_confirmed_live_quota_insufficiency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(
        cli,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr(
        cli,
        "_validate_component_dependencies",
        lambda _cfg, *, chart_meta_cache=None: [],
    )
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "rendered_module_sources", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "_raise_on_live_quota_issues",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Nebius quota/capacity is insufficient for validate.")
        ),
    )

    result = runner.invoke(cli.app, ["validate", str(tmp_path / "config.yaml")])

    assert result.exit_code != 0
    assert "Nebius quota/capacity is insufficient for validate." in _plain_output(result.output)


def test_runtime_validation_non_strict_warns_on_confirmed_live_quota_insufficiency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(
        cli,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr(
        cli,
        "_validate_component_dependencies",
        lambda _cfg, *, chart_meta_cache=None: [],
    )
    monkeypatch.setattr(cli, "rendered_module_sources", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "_warn_on_live_quota_issues",
        lambda *_args, **_kwargs: QuotaReport(
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            checked_at="2026-04-18T00:00:00+00:00",
            checks=(
                QuotaCheck(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    quota_name="compute.disk.size.network-ssd",
                    region="eu-north1",
                    required=1024,
                    reason="mk8s boot disks",
                    unit="byte",
                    available=0,
                    sufficient=False,
                    tenant_limit=0,
                    tenant_usage=0,
                    project_limit=None,
                    project_usage=None,
                    source_scope="tenant",
                    description="SSD quota",
                ),
            ),
        ),
    )
    monkeypatch.setattr(cli, "_validation_scope_summary_lines", lambda *_args, **_kwargs: None)

    with cli.console.capture() as capture:
        cli._run_runtime_validation(
            config_path=tmp_path / "config.yaml",
            strict=False,
            title="Post-create validation",
        )

    output = _plain_output(capture.get())
    assert "Valid with quota warnings:" in output
    assert "nebius-cxcli quota-request" in output


def test_validate_command_prints_mk8s_gpu_validation_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(
        cli,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr(
        cli,
        "_validate_component_dependencies",
        lambda _cfg, *, chart_meta_cache=None: [],
    )
    monkeypatch.setattr(cli, "rendered_module_sources", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli, "_raise_on_live_quota_issues", lambda *_args, **_kwargs: _empty_quota_report()
    )
    monkeypatch.setattr(cli, "_validation_scope_summary_lines", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "mk8s_gpu_validation_warnings",
        lambda _config: (
            "deploy.targets[].validations.mk8s_gpu.nccl.enabled is set on an Ethernet-only test shape.",
        ),
    )

    result = runner.invoke(cli.app, ["validate", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Deploy validation warning:" in output
    assert "Ethernet-only test shape" in output


def test_validate_command_accepts_local_source_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(
        cli,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: None,
    )
    monkeypatch.setattr(
        cli,
        "_validate_component_dependencies",
        lambda _cfg, *, chart_meta_cache=None: [],
    )
    monkeypatch.setattr(
        cli,
        "rendered_module_sources",
        lambda config, *, source_profile: (
            captured.update({"config": config, "source_profile": source_profile}) or ()
        ),
    )
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli, "_raise_on_live_quota_issues", lambda *_args, **_kwargs: _empty_quota_report()
    )

    result = runner.invoke(
        cli.app,
        ["--source-profile", "local", "validate", str(tmp_path / "config.yaml")],
    )

    assert result.exit_code == 0, result.output
    assert captured["source_profile"] == SourceProfile.LOCAL


def test_quota_check_command_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

    result = runner.invoke(cli.app, ["quota-check", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    output_without_linebreaks = output.replace("\n", "")
    assert "Nebius quota is sufficient:" in output
    assert str(fake_paths.config_path) in output_without_linebreaks


def test_quota_check_command_all_regions_reports_regional_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
    captured: dict[str, object] = {}

    def _fake_assess(*_args, **kwargs):
        captured.update(kwargs)
        return QuotaReport(
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="us-central1",
            checked_at="2026-04-10T00:00:00+00:00",
            regional_availability=(
                RegionalQuotaAvailability(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    quota_name="compute.instance.gpu.b200",
                    required=8,
                    reason="mk8s: 1 GPU node(s) at gpu-b200-sxm/8gpu-160vcpu-1792gb",
                    unit="count",
                    current_region="us-central1",
                    region_checks=(
                        QuotaCheck(
                            component_id="mk8s",
                            instance_id="mk8s",
                            component_label="mk8s",
                            quota_name="compute.instance.gpu.b200",
                            region="us-central1",
                            required=8,
                            reason="mk8s: 1 GPU node(s) at gpu-b200-sxm/8gpu-160vcpu-1792gb",
                            unit="count",
                            available=2,
                            sufficient=False,
                            tenant_limit=18,
                            tenant_usage=16,
                            project_limit=None,
                            project_usage=0,
                            source_scope="tenant",
                            description="B200 GPU quota",
                            contributors=(),
                        ),
                        QuotaCheck(
                            component_id="mk8s",
                            instance_id="mk8s",
                            component_label="mk8s",
                            quota_name="compute.instance.gpu.b200",
                            region="eu-north1",
                            required=8,
                            reason="mk8s: 1 GPU node(s) at gpu-b200-sxm/8gpu-160vcpu-1792gb",
                            unit="count",
                            available=18,
                            sufficient=True,
                            tenant_limit=18,
                            tenant_usage=0,
                            project_limit=None,
                            project_usage=0,
                            source_scope="tenant",
                            description="B200 GPU quota",
                            contributors=(),
                        ),
                    ),
                ),
            ),
        )

    monkeypatch.setattr(cli, "assess_live_quotas", _fake_assess)

    result = runner.invoke(cli.app, ["quota-check", "--all-regions", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert captured["all_regions"] is True
    output = " ".join(_plain_output(result.output).split())
    assert "Regional quota availability for the current config shape" in output
    assert "us-central1 (current): available 2 (insufficient)" in output
    assert "eu-north1: available 18 (sufficient)" in output
    assert "Next step: compare quota availability across regions with:" not in output


def test_quota_check_command_coverage_gap_warns_without_all_regions_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli,
        "assess_live_quotas",
        lambda *_args, **_kwargs: QuotaReport(
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            checked_at="2026-04-10T00:00:00+00:00",
            checks=(
                QuotaCheck(
                    component_id="object-storage",
                    instance_id="object-storage",
                    component_label="object-storage",
                    quota_name="storage.bucket.count",
                    region="eu-north1",
                    required=1,
                    reason="one bucket",
                    unit="",
                    available=5,
                    sufficient=True,
                    tenant_limit=5,
                    tenant_usage=0,
                    project_limit=None,
                    project_usage=0,
                    source_scope="tenant",
                    description="Bucket count",
                    contributors=(),
                ),
                QuotaCheck(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    quota_name="compute.instance.count",
                    region="eu-north1",
                    required=3,
                    reason="three cluster nodes",
                    unit="",
                    available=12,
                    sufficient=True,
                    tenant_limit=12,
                    tenant_usage=0,
                    project_limit=None,
                    project_usage=0,
                    source_scope="tenant",
                    description="VM count",
                    contributors=(),
                ),
                QuotaCheck(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    quota_name="compute.instance.non-gpu.vcpu",
                    region="eu-north1",
                    required=12,
                    reason="three cpu nodes at 4 vCPU",
                    unit="",
                    available=64,
                    sufficient=True,
                    tenant_limit=64,
                    tenant_usage=0,
                    project_limit=None,
                    project_usage=0,
                    source_scope="tenant",
                    description="Non-GPU vCPU quota",
                    contributors=(),
                ),
            ),
            coverage_gaps=(
                QuotaCoverageGap(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    message=(
                        "MK8s CPU node-group boot-disk quota could not be fully evaluated; "
                        "set inputs.cpu_nodes_boot_disk_size_gib and "
                        "inputs.cpu_nodes_boot_disk_type, or set "
                        "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* "
                        "and inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
                    ),
                ),
                QuotaCoverageGap(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    message=(
                        "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
                        "set inputs.gpu_nodes_boot_disk_size_gib and "
                        "inputs.gpu_nodes_boot_disk_type, or set "
                        "inputs.mk8s_gpu_node_group_overrides.template.boot_disk.size_* "
                        "and inputs.mk8s_gpu_node_group_overrides.template.boot_disk.type"
                    ),
                ),
            ),
        ),
    )

    result = runner.invoke(cli.app, ["quota-check", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    collapsed_output = " ".join(plain_output.split())
    assert "Quota check completed with warnings." in collapsed_output
    assert "No confirmed quota insufficiency was found." in collapsed_output
    assert (
        "Some quota dimensions could not be evaluated from the current config/API surface."
        in collapsed_output
    )
    assert (
        "Quota confirmed: live quota was sufficient for the following checked component(s)."
        in collapsed_output
    )
    assert "object-storage: 1 checked quota dimension confirmed in eu-north1" in collapsed_output
    assert "    checked:" in plain_output
    assert "      - storage.bucket.count" in plain_output
    assert (
        "mk8s: 2 checked quota dimensions confirmed in eu-north1 (partial coverage; see gaps below)"
    ) in collapsed_output
    assert "      - compute.instance.count" in plain_output
    assert "      - compute.instance.non-gpu.vcpu" in plain_output
    assert "  - mk8s" in plain_output
    assert "    gaps:" in plain_output
    assert (
        "MK8s CPU node-group boot-disk quota could not be fully evaluated; "
        "set inputs.cpu_nodes_boot_disk_size_gib and inputs.cpu_nodes_boot_disk_type, "
        "or set inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* "
        "and inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
    ) in collapsed_output
    assert (
        "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
        "set inputs.gpu_nodes_boot_disk_size_gib and inputs.gpu_nodes_boot_disk_type, "
        "or set inputs.mk8s_gpu_node_group_overrides.template.boot_disk.size_* "
        "and inputs.mk8s_gpu_node_group_overrides.template.boot_disk.type"
    ) in collapsed_output
    assert "Next step: compare quota availability across regions with:" not in collapsed_output


def test_quota_check_command_fails_on_confirmed_insufficiency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli,
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

    result = runner.invoke(cli.app, ["quota-check", str(tmp_path / "config.yaml")])

    assert result.exit_code != 0
    plain_output = _plain_output(result.output)
    output = " ".join(plain_output.split())
    output_without_linebreaks = plain_output.replace("\n", "")
    assert "Nebius quota/capacity is insufficient for quota check." in output
    assert (
        "Increase the quota, or for GPU shortages choose a platform/preset/fabric "
        "with available Capacity Dashboard capacity, and retry." in output
    )
    assert "nebius-cxcli quota-request" in output
    assert "compute.instance.count requires 1, available 0" in output
    assert "Next step: compare quota availability across regions with:" in output
    assert "nebius-cxcli quota-check --all-regions" in output
    assert str(fake_paths.config_path) in output_without_linebreaks


def test_quota_check_capacity_only_shortage_does_not_suggest_quota_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli,
        "assess_live_quotas",
        lambda *_args, **_kwargs: QuotaReport(
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            checked_at="2026-04-10T00:00:00+00:00",
            checks=(
                QuotaCheck(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    quota_name="compute.instance.gpu.h100",
                    region="eu-north1",
                    required=16,
                    reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                    unit="count",
                    available=0,
                    sufficient=False,
                    tenant_limit=64,
                    tenant_usage=0,
                    project_limit=64,
                    project_usage=0,
                    source_scope="capacity-dashboard/on-demand",
                    description=(
                        "Capacity Dashboard GPU availability "
                        "(on-demand VM slots, fabric fabric-4, converted to GPU units)"
                    ),
                    contributors=(),
                ),
            ),
        ),
    )

    result = runner.invoke(cli.app, ["quota-check", str(tmp_path / "config.yaml")])

    assert result.exit_code != 0
    output = " ".join(_plain_output(result.output).split())
    assert "nebius-cxcli quota-request" not in output
    assert (
        "choose a GPU platform/preset/fabric or region with available "
        "Capacity Dashboard capacity"
    ) in output
    assert "nebius-cxcli quota-check --all-regions" in output


def test_quota_request_discounts_existing_mk8s_state_for_day2_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cli.manifest_path_for_generated_dir(fake_paths.generated_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    live_report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=6,
                reason="mk8s: 6 instance(s)",
                unit="count",
                available=1,
                sufficient=False,
                tenant_limit=5,
                tenant_usage=4,
                project_limit=None,
                project_usage=0,
                source_scope="tenant",
                description="VM count",
                contributors=(
                    cli.QuotaContributor(
                        component_id="mk8s",
                        instance_id="mk8s",
                        component_label="mk8s",
                        required=6,
                        reason="6 instance(s)",
                    ),
                ),
            ),
        ),
    )

    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "assess_live_quotas", lambda *_args, **_kwargs: live_report)
    monkeypatch.setattr(
        cli,
        "load_generated_manifest",
        lambda _generated_dir: {"render": {"module_sources": []}},
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {"TF_VAR_demo": "1"})
    monkeypatch.setattr(cli, "terraform_init", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_quota_requirements_from_terraform_state",
        lambda *_args, **_kwargs: (
            SimpleNamespace(
                component_id="mk8s",
                instance_id="mk8s",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=4,
            ),
        ),
    )

    def _capture_request(report, *, context="quota request"):
        planned_changes = cli.plan_quota_request_changes(report)
        captured["report"] = report
        captured["planned_changes"] = planned_changes
        return QuotaRequestResult(
            planned_changes=planned_changes,
            unavailable_reason="internal quota-request API unavailable",
        )

    monkeypatch.setattr(cli, "request_quota_changes", _capture_request)

    result = runner.invoke(cli.app, ["quota-request", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    adjusted_report = cast(QuotaReport, captured["report"])
    assert adjusted_report.checks[0].required == 2
    assert "net-new after existing Terraform state discount" in adjusted_report.checks[0].reason
    planned_changes = cast(tuple[QuotaRequestChange, ...], captured["planned_changes"])
    assert len(planned_changes) == 1
    assert planned_changes[0].requested_limit == 6
    output = " ".join(_plain_output(result.output).split())
    assert "target 6 (current limit 5, current usage 4)" in output


def test_quota_request_command_submits_confirmed_shortages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="eu-north1",
                required=1024,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="SSD quota",
                tenant_quota_id="quota-tenant-1",
            ),
        ),
    )
    submitted: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), _fake_paths(tmp_path)))
    monkeypatch.setattr(cli, "_warn_on_config_live_quota_issues", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        cli,
        "request_quota_changes",
        lambda payload, *, context="quota request": (
            submitted.update({"report": payload, "context": context})
            or QuotaRequestResult(
                planned_changes=(
                    QuotaRequestChange(
                        container_id="tenant-123",
                        container_scope="tenant",
                        quota_name="compute.disk.size.network-ssd",
                        region="eu-north1",
                        current_limit=0,
                        current_usage=0,
                        required=1024,
                        requested_limit=1024,
                        unit="byte",
                    ),
                ),
                submitted_changes=(
                    QuotaRequestChange(
                        container_id="tenant-123",
                        container_scope="tenant",
                        quota_name="compute.disk.size.network-ssd",
                        region="eu-north1",
                        current_limit=0,
                        current_usage=0,
                        required=1024,
                        requested_limit=1024,
                        unit="byte",
                    ),
                ),
            )
        ),
    )

    result = runner.invoke(cli.app, ["quota-request", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    normalized_output = " ".join(output.split())
    assert "Planned quota requests for confirmed shortages:" in output
    assert "Quota request submitted:" in output
    assert "Current quota allowances remain unchanged until these requests are approved." in output
    assert "Administration -> Limits -> Quotas" in normalized_output
    assert submitted["report"] is report
    assert submitted["context"] == "quota request"


def test_quota_request_command_falls_back_cleanly_on_permission_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="eu-north1",
                required=1024,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="SSD quota",
                tenant_quota_id="quota-tenant-1",
            ),
        ),
    )
    change = QuotaRequestChange(
        container_id="tenant-123",
        container_scope="tenant",
        quota_name="compute.disk.size.network-ssd",
        region="eu-north1",
        current_limit=0,
        current_usage=0,
        required=1024,
        requested_limit=1024,
        unit="byte",
    )

    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), _fake_paths(tmp_path)))
    monkeypatch.setattr(cli, "_warn_on_config_live_quota_issues", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        cli,
        "request_quota_changes",
        lambda *_args, **_kwargs: QuotaRequestResult(
            planned_changes=(change,),
            failed_changes=(
                QuotaRequestFailure(
                    change=change,
                    message="Failed to request quota 'compute.disk.size.network-ssd': PERMISSION_DENIED",
                    permission_denied=True,
                ),
            ),
        ),
    )

    result = runner.invoke(cli.app, ["quota-request", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    normalized_output = " ".join(output.split())
    assert "Planned quota requests for confirmed shortages:" in output
    assert "Automatic quota-request submission was not permitted." in output
    assert "Manual follow-up is still required for:" in output
    assert "tenant tenant-123: eu-north1 compute.disk.size.network-ssd" in output
    assert "request total limit at least 1.0 KiB (1024 byte)" in normalized_output
    assert "increase by at least 1.0 KiB (1024 byte) over current limit 0 B" in normalized_output
    assert "Administration -> Limits -> Quotas" in normalized_output
    assert "Quota request submitted:" not in output


def test_quota_request_command_reports_noop_when_no_confirmed_shortage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), _fake_paths(tmp_path)))
    monkeypatch.setattr(
        cli, "_warn_on_config_live_quota_issues", lambda *_args, **_kwargs: _empty_quota_report()
    )

    result = runner.invoke(cli.app, ["quota-request", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert "No quota request needed:" in _plain_output(result.output)


def test_quota_request_command_falls_back_to_manual_when_internal_api_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="eu-north1",
                required=1024,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="SSD quota",
                tenant_quota_id="quota-tenant-1",
            ),
        ),
    )
    change = QuotaRequestChange(
        container_id="tenant-123",
        container_scope="tenant",
        quota_name="compute.disk.size.network-ssd",
        region="eu-north1",
        current_limit=0,
        current_usage=0,
        required=1024,
        requested_limit=1024,
        unit="byte",
    )

    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), _fake_paths(tmp_path)))
    monkeypatch.setattr(cli, "_warn_on_config_live_quota_issues", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        cli,
        "request_quota_changes",
        lambda *_args, **_kwargs: QuotaRequestResult(
            planned_changes=(change,),
            unavailable_reason="internal quota-request API unavailable",
        ),
    )

    result = runner.invoke(cli.app, ["quota-request", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    normalized_output = " ".join(output.split())
    assert "Planned quota requests for confirmed shortages:" in output
    assert "Automatic quota-request submission is unavailable." in output
    assert "Manual follow-up is still required for:" in output
    assert "internal quota-request API unavailable" in output
    assert "request total limit at least 1.0 KiB (1024 byte)" in normalized_output
    assert "Current quota allowances remain unchanged until the request is approved." in output
    assert "Quota request submitted:" not in output


def test_quota_request_command_prints_coverage_gaps_when_no_request_is_possible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="uk-south1",
        checked_at="2026-04-18T00:00:00+00:00",
        coverage_gaps=(
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.gpu_nodes_boot_disk_size_gib and "
                    "inputs.gpu_nodes_boot_disk_type, or set "
                    "inputs.mk8s_gpu_node_group_overrides.template.boot_disk.size_* "
                    "and inputs.mk8s_gpu_node_group_overrides.template.boot_disk.type"
                ),
            ),
        ),
    )
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), _fake_paths(tmp_path)))

    def _fake_warn_on_config_live_quota_issues(*_args, **_kwargs):
        cli._print_live_quota_report(report, phase="quota request")
        return report

    monkeypatch.setattr(
        cli,
        "_warn_on_config_live_quota_issues",
        _fake_warn_on_config_live_quota_issues,
    )

    result = runner.invoke(cli.app, ["quota-request", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "quota could not be fully evaluated for the following component(s)" in " ".join(
        output.split()
    )
    assert "MK8s GPU node-group boot-disk quota could not be fully evaluated" in output
    assert "No quota request was submitted." in output


def test_load_generated_context_exports_manifest_tool_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    fake_manifest = {
        "tools": {
            "flux_version": "v2.8.0",
            "terraform_version": "1.14.1",
        },
        "render": {
            "terraform_tfvars": {
                "mk8s_cluster_name": "clust1",
            }
        },
        "runtime_config": {
            "client_info": {"client_name": "client-a", "nebius": {"project_id": "project-456"}}
        },
    }
    runtime_config = SimpleNamespace(client_info=SimpleNamespace(client_name="client-a"))

    monkeypatch.delenv(FLUX_VERSION_ENV, raising=False)
    monkeypatch.delenv(TERRAFORM_VERSION_ENV, raising=False)
    monkeypatch.setattr(cli, "resolve_generated_paths", lambda _target: fake_paths)
    monkeypatch.setattr(cli, "load_generated_manifest", lambda _generated_dir: fake_manifest)
    monkeypatch.setattr(cli, "runtime_config_from_manifest", lambda _manifest: runtime_config)

    config, paths, manifest = cli._load_generated_context(tmp_path / "generated")

    assert config is runtime_config
    assert paths is fake_paths
    assert manifest is fake_manifest
    assert os.environ[FLUX_VERSION_ENV] == "v2.8.0"
    assert os.environ[TERRAFORM_VERSION_ENV] == "1.14.1"
    assert json.loads(
        (fake_paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    ) == {"mk8s_cluster_name": "clust1"}


def test_generated_subtree_path_resolvers_match_command_contract(tmp_path: Path) -> None:
    project_dir = tmp_path / "deployments" / "tenant-name-example" / "project-name-example"
    project_dir.mkdir(parents=True, exist_ok=True)

    infra_paths = resolve_generated_infra_paths(project_dir / "generated" / "infra" / "main.tf")
    flux_paths = resolve_generated_flux_paths(
        project_dir / "generated" / "flux" / "targets" / "mk8s" / "kustomization.yaml"
    )

    assert infra_paths.generated_dir == project_dir / "generated"
    assert infra_paths.infra_dir == project_dir / "generated" / "infra"
    assert flux_paths.generated_dir == project_dir / "generated"
    assert flux_paths.flux_dir == project_dir / "generated" / "flux"

    with pytest.raises(
        ValueError,
        match=r"Terraform target must point to `generated/` or `generated/infra/`, not `generated/flux",
    ):
        resolve_generated_infra_paths(project_dir / "generated" / "flux" / "kustomization.yaml")

    with pytest.raises(
        ValueError,
        match=r"Flux target must point to `generated/` or `generated/flux/`, not `generated/infra",
    ):
        resolve_generated_flux_paths(project_dir / "generated" / "infra" / "main.tf")


def test_try_generate_terraform_lock_file_uses_backendless_init_and_cleans_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    def _fail_backend_ready(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("render lock generation must not bootstrap backend auth")

    def _fake_terraform_init(
        infra_dir: Path,
        *,
        extra_env: dict[str, str] | None = None,
        backend: bool = True,
    ) -> None:
        captured["infra_dir"] = infra_dir
        captured["extra_env"] = extra_env
        captured["backend"] = backend
        (infra_dir / ".terraform").mkdir(parents=True, exist_ok=True)
        (infra_dir / ".terraform" / "terraform.tfstate").write_text("transient", encoding="utf-8")
        (infra_dir / "terraform.tfstate").write_text("state", encoding="utf-8")
        (infra_dir / ".terraform.tfstate.lock.info").write_text("lock", encoding="utf-8")
        (infra_dir / ".terraform.lock.hcl").write_text("provider-lock", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", _fail_backend_ready)
    monkeypatch.setattr(cli, "terraform_init", _fake_terraform_init)

    assert cli._try_generate_terraform_lock_file("cfg", fake_paths) is True
    assert captured == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": None,
        "backend": False,
    }
    assert not (fake_paths.infra_dir / ".terraform").exists()
    assert not (fake_paths.infra_dir / "terraform.tfstate").exists()
    assert not (fake_paths.infra_dir / ".terraform.tfstate.lock.info").exists()
    assert (fake_paths.infra_dir / ".terraform.lock.hcl").exists()


def test_render_command_invokes_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda config, paths, *, source_profile: (
            calls.update(
                {
                    "terraform_config": config,
                    "terraform_paths": paths,
                    "terraform_profile": source_profile,
                }
            )
            or [tmp_path / "a.tf"]
        ),
    )
    monkeypatch.setattr(
        cli,
        "_runtime_component_output_values",
        lambda config, paths, **kwargs: (
            calls.update({"outputs_config": config, "outputs_paths": paths}) or {}
        ),
    )
    monkeypatch.setattr(
        cli,
        "render_flux",
        lambda config, paths, *, component_output_values=None: (
            calls.update(
                {
                    "flux_config": config,
                    "flux_paths": paths,
                    "flux_outputs": component_output_values,
                }
            )
            or [tmp_path / "b.yaml"]
        ),
    )
    monkeypatch.setattr(
        cli,
        "_try_generate_terraform_lock_file",
        lambda config, paths, **kwargs: (
            calls.update(
                {
                    "lock_config": config,
                    "lock_paths": paths,
                }
            )
            or False
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.update({"inventory_config": config, "inventory_paths": paths})
            or SimpleNamespace(markdown=paths.inventory_dir / "deploy-report.md")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda config, paths, *, source_profile, **kwargs: (
            calls.update(
                {
                    "manifest_config": config,
                    "manifest_paths": paths,
                    "manifest_profile": source_profile,
                    "manifest_kwargs": kwargs,
                }
            )
            or paths.generated_dir / "nebius-cxcli-manifest.json"
        ),
    )

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert "Rendered 2 file(s)" in _plain_output(result.output)
    assert calls["terraform_config"] == "cfg"
    assert calls["terraform_profile"] == SourceProfile.PORTABLE
    assert calls["outputs_config"] == "cfg"
    assert calls["outputs_paths"] == fake_paths
    assert calls["flux_config"] == "cfg"
    assert calls["flux_outputs"] == {}
    assert calls["inventory_config"] == "cfg"
    assert calls["manifest_config"] == "cfg"
    assert calls["manifest_profile"] == SourceProfile.PORTABLE
    assert calls["lock_config"] == "cfg"
    assert calls["lock_paths"] == fake_paths

    staged_paths = calls["terraform_paths"]
    assert isinstance(staged_paths, ProjectPaths)
    assert staged_paths.generated_dir.name.startswith(".generated-staging-")
    assert calls["flux_paths"] == staged_paths
    assert calls["inventory_paths"] == staged_paths
    assert calls["manifest_paths"] == staged_paths
    assert calls["manifest_kwargs"]["manifest_paths"] == fake_paths
    assert calls["manifest_kwargs"]["output_path"] == (
        staged_paths.generated_dir / "nebius-cxcli-manifest.json"
    )


def test_render_command_persists_quota_report_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    report = QuotaReport(
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
        coverage_gaps=(
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s CPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.cpu_nodes_boot_disk_size_gib and "
                    "inputs.cpu_nodes_boot_disk_type, or set "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* "
                    "and inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
                ),
            ),
        ),
    )

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_confirm_render_overwrite", lambda _paths, *, force: True)
    monkeypatch.setattr(cli, "reset_generated_bundle", lambda _paths: None)
    monkeypatch.setattr(
        cli,
        "_ensure_deployments_gitignore",
        lambda deployments_root: SimpleNamespace(path=None, wrote=False),
    )
    monkeypatch.setattr(cli, "render_terraform_artifacts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "render_flux", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "write_inventory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "assess_live_quotas", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda config, paths, *, source_profile, quota_report=None, **kwargs: (
            captured.update(
                {
                    "config": config,
                    "paths": paths,
                    "source_profile": source_profile,
                    "quota_report": quota_report,
                    "kwargs": kwargs,
                }
            )
            or paths.generated_dir / "nebius-cxcli-manifest.json"
        ),
    )

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert captured["quota_report"] is report
    assert "Render completed with quota warnings." in _plain_output(result.output)
    assert "compute.instance.count requires 1, available 0" in _plain_output(result.output)
    assert "boot-disk quota could not be fully evaluated" not in _plain_output(result.output)


def test_render_command_accepts_local_source_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_confirm_render_overwrite", lambda _paths, *, force: True)
    monkeypatch.setattr(cli, "reset_generated_bundle", lambda _paths: None)
    monkeypatch.setattr(
        cli,
        "_ensure_deployments_gitignore",
        lambda deployments_root: SimpleNamespace(path=None, wrote=False),
    )
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda config, paths, *, source_profile: (
            calls.update({"source_profile": source_profile}) or [tmp_path / "a.tf"]
        ),
    )
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda config, paths, **kwargs: {})
    monkeypatch.setattr(
        cli, "render_flux", lambda config, paths, *, component_output_values=None: []
    )
    monkeypatch.setattr(cli, "write_inventory", lambda config, paths, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda config, paths, *, source_profile, **kwargs: paths.generated_dir / "manifest.json",
    )
    monkeypatch.setattr(
        cli, "_try_generate_terraform_lock_file", lambda config, paths, **kwargs: False
    )

    result = runner.invoke(
        cli.app,
        ["--source-profile", "local", "render", str(tmp_path / "config.yaml")],
    )

    assert result.exit_code == 0, result.output
    assert calls["source_profile"] == SourceProfile.LOCAL
    assert "Source profile: local" in _plain_output(result.output)
    output = _plain_output(result.output)
    assert "local source profile may embed local Terraform" in output
    assert "generated artifacts in CI" in output


def test_validate_generated_command_portable_checks_module_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "_load_generated_context",
        lambda _path: ("cfg", fake_paths, {"render": {"module_sources": []}}),
    )
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_generated_bundle_live_quota_issues",
        lambda config, paths, *, manifest, runtime_env, phase: (
            captured.update(
                {
                    "quota_phase": phase,
                    "quota_paths": paths,
                    "quota_manifest": manifest,
                    "quota_runtime_env": runtime_env,
                }
            )
            or _empty_quota_report()
        ),
    )
    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", lambda config, *, auto_auth_bootstrap: None
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "terraform_init", lambda infra_dir, *, extra_env=None: None)
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: None,
    )
    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 0)
    monkeypatch.setattr(
        cli,
        "_validate_generated_bundle_portability",
        lambda paths, manifest: captured.update({"paths": paths, "manifest": manifest}),
    )

    result = runner.invoke(
        cli.app,
        ["validate-generated", "--portable", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Validate strict deployment readiness" in output
    assert "Validate MK8s network preflight" in output
    assert "Validate live Nebius quota/capacity" in output
    assert captured["paths"] == fake_paths
    assert captured["quota_phase"] == "validate-generated"
    assert captured["quota_paths"] == fake_paths
    assert captured["quota_runtime_env"] == {}


def test_validate_generated_command_requires_manifest_module_sources_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        cli,
        "_load_generated_context",
        lambda _path: ("cfg", fake_paths, {"render": {}}),
    )
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_generated_bundle_live_quota_issues",
        lambda *_args, **_kwargs: _empty_quota_report(),
    )
    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", lambda config, *, auto_auth_bootstrap: None
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "terraform_init", lambda infra_dir, *, extra_env=None: None)
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: None,
    )
    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 0)

    result = runner.invoke(
        cli.app,
        ["validate-generated", "--portable", str(tmp_path / "generated")],
    )

    assert result.exit_code != 0
    assert "render.module_sources" in _plain_output(result.output)


def test_validate_sources_command_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = SimpleNamespace(
        tf_modules=[SimpleNamespace(module="mk8s")],
        helm_charts=[SimpleNamespace(name="gateway-helm")],
    )
    sources_file = tmp_path / "component_sources.yaml"

    monkeypatch.setattr(cli, "load_component_sources", lambda explicit=None: sources)
    monkeypatch.setattr(
        cli,
        "_validate_component_sources_registry",
        lambda explicit=None, progress_callback=None: (sources_file, [], []),
    )

    result = runner.invoke(cli.app, ["validate-sources"])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    normalized_output = output.replace("\n", "")
    assert "Component sources valid:" in output
    assert str(sources_file) in normalized_output


def test_validate_sources_command_reports_warnings_and_fails_on_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = SimpleNamespace(tf_modules=[], helm_charts=[])
    sources_file = tmp_path / "component_sources.yaml"

    monkeypatch.setattr(cli, "load_component_sources", lambda explicit=None: sources)
    monkeypatch.setattr(
        cli,
        "_validate_component_sources_registry",
        lambda explicit=None, progress_callback=None: (
            sources_file,
            ["module source './broken-module' does not resolve to an existing directory"],
            ["missing variables.tf"],
        ),
    )

    result = runner.invoke(cli.app, ["validate-sources"])
    output = _plain_output(result.output)
    normalized_output = output.replace("\n", "")

    assert result.exit_code == 1, result.output
    assert "Warning: missing variables.tf" in output
    assert "Component sources validation failed for" in output
    assert str(sources_file) in normalized_output
    assert "module source './broken-module' does not resolve to an existing directory" in output


def test_validate_sources_command_accepts_positional_component_sources_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = SimpleNamespace(
        tf_modules=[SimpleNamespace(module="mk8s")],
        helm_charts=[],
    )
    sources_file = tmp_path / "component_sources.yaml"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "load_component_sources",
        lambda explicit=None: (
            captured.__setitem__("load_explicit", explicit),
            sources,
        )[1],
    )
    monkeypatch.setattr(
        cli,
        "_validate_component_sources_registry",
        lambda explicit=None, progress_callback=None: (
            captured.__setitem__("validate_explicit", explicit),
            sources_file,
            [],
            [],
        )[1:],
    )

    result = runner.invoke(cli.app, ["validate-sources", str(sources_file)])

    assert result.exit_code == 0, result.output
    assert captured["load_explicit"] == sources_file
    assert captured["validate_explicit"] == sources_file


def test_render_command_requires_force_in_noninteractive_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.generated_dir / "existing.txt").write_text("existing", encoding="utf-8")

    called: dict[str, bool] = {"rendered": False}

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_can_prompt_for_render_overwrite", lambda: False)
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda *_args, **_kwargs: called.update({"rendered": True}) or [],
    )

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")])

    assert result.exit_code == 1, result.output
    assert "--force" in _plain_output(result.output)
    assert called["rendered"] is False


def test_render_command_force_allows_noninteractive_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.generated_dir / "existing.txt").write_text("existing", encoding="utf-8")
    calls: dict[str, bool] = {"rendered": False}

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_can_prompt_for_render_overwrite", lambda: False)
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda *_args, **_kwargs: calls.update({"rendered": True}) or [],
    )
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "render_flux", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda *_args, **_kwargs: SimpleNamespace(
            markdown=fake_paths.inventory_dir / "deploy-report.md"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda *_args, **_kwargs: fake_paths.generated_dir / "nebius-cxcli-manifest.json",
    )
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)

    result = runner.invoke(cli.app, ["render", "--force", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert calls["rendered"] is True


def test_render_command_prompts_before_overwrite_when_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.generated_dir / "existing.txt").write_text("existing", encoding="utf-8")
    calls: dict[str, bool] = {"rendered": False}

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_can_prompt_for_render_overwrite", lambda: True)
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda *_args, **_kwargs: calls.update({"rendered": True}) or [],
    )
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "render_flux", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda *_args, **_kwargs: SimpleNamespace(
            markdown=fake_paths.inventory_dir / "deploy-report.md"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda *_args, **_kwargs: fake_paths.generated_dir / "nebius-cxcli-manifest.json",
    )
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Continue and overwrite the existing generated artifacts?" in _plain_output(
        result.output
    )
    assert calls["rendered"] is True


def test_render_command_decline_is_clean_cancel_not_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.generated_dir / "existing.txt").write_text("existing", encoding="utf-8")
    calls: dict[str, bool] = {"rendered": False}

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_can_prompt_for_render_overwrite", lambda: True)
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda *_args, **_kwargs: calls.update({"rendered": True}) or [],
    )

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Render cancelled" in _plain_output(result.output)
    assert "ERROR:" not in _plain_output(result.output)
    assert calls["rendered"] is False


def test_render_command_preserves_existing_generated_bundle_when_rerender_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    preserved = fake_paths.generated_dir / "existing.txt"
    preserved.write_text("keep-me\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_load_runtime_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(cli, "_can_prompt_for_render_overwrite", lambda: False)
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda *_args, **_kwargs: [(_args[1].infra_dir / "main.tf")],
    )
    monkeypatch.setattr(
        cli,
        "render_flux",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = runner.invoke(cli.app, ["render", "--force", str(tmp_path / "config.yaml")])

    assert result.exit_code == 1, result.output
    assert "boom" in _plain_output(result.output)
    assert preserved.read_text(encoding="utf-8") == "keep-me\n"
    assert not any(fake_paths.project_dir.glob(".generated-staging-*"))


def test_deploy_command_passes_auto_auth_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_ensure_ci_workflow_for_deployments_root",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("deploy must not bootstrap CI workflow")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_auto_bootstrap_ci_auth_and_secrets",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("deploy must not bootstrap GitHub CI auth/secrets")
        ),
    )

    def _fake_deploy_generated_artifacts(
        config: object,
        paths: object,
        loaded_manifest: object,
        *,
        auto_auth_bootstrap: bool,
        skip_validations: bool,
        skip_validation_kinds: set[str],
        requested_target_ref: str | None = None,
        all_targets: bool = False,
    ) -> None:
        captured["config"] = config
        captured["paths"] = paths
        captured["manifest"] = loaded_manifest
        captured["auto_auth_bootstrap"] = auto_auth_bootstrap
        captured["skip_validations"] = skip_validations
        captured["skip_validation_kinds"] = skip_validation_kinds
        captured["requested_target_ref"] = requested_target_ref
        captured["all_targets"] = all_targets

    monkeypatch.setattr(cli, "_deploy_generated_artifacts", _fake_deploy_generated_artifacts)

    result = runner.invoke(
        cli.app,
        ["deploy", str(fake_paths.config_path), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Local deploy completed from" in output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "manifest": manifest,
        "auto_auth_bootstrap": True,
        "skip_validations": False,
        "skip_validation_kinds": set(),
        "requested_target_ref": None,
        "all_targets": False,
    }


def test_deploy_command_passes_one_run_validation_skip_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_deploy_generated_artifacts",
        lambda config, paths, loaded_manifest, **kwargs: captured.update(
            {
                "config": config,
                "paths": paths,
                "manifest": loaded_manifest,
                **kwargs,
            }
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "deploy",
            str(fake_paths.config_path),
            "--skip-validation",
            "nccl",
            "--skip-validation",
            "gpu-visibility",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["skip_validations"] is False
    assert captured["skip_validation_kinds"] == {"mk8s_nccl", "mk8s_gpu_visibility"}


def test_deploy_command_rejects_unknown_one_run_validation_skip_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(cli, "_deploy_generated_artifacts", lambda *args, **kwargs: None)

    result = runner.invoke(
        cli.app,
        [
            "deploy",
            str(fake_paths.config_path),
            "--skip-validation",
            "health-checker",
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported --skip-validation value(s): health-checker" in result.output


def test_deploy_command_accepts_config_yaml_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    captured: dict[str, object] = {}

    def _fake_load(target: Path) -> tuple[object, ProjectPaths, dict[str, str]]:
        captured["target"] = target
        return "cfg", fake_paths, manifest

    monkeypatch.setattr(cli, "_load_deploy_context", _fake_load)
    monkeypatch.setattr(cli, "_deploy_generated_artifacts", lambda *args, **kwargs: None)

    result = runner.invoke(cli.app, ["deploy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    assert captured["target"] == fake_paths.config_path
    assert "Local deploy completed from" in _plain_output(result.output)


def test_deploy_command_rejects_generated_target_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "_load_deploy_context",
        lambda _path: (_ for _ in ()).throw(
            ValueError(
                "Deploy target must be project config.yaml, not generated/. "
                "Pass <tenant-folder>/<project-folder>/config.yaml; deploy resolves sibling generated/ automatically."
            )
        ),
    )

    result = runner.invoke(cli.app, ["deploy", str(tmp_path / "generated")])

    assert result.exit_code != 0
    assert "Deploy target must be project config.yaml, not generated/." in _plain_output(
        result.output
    )


def test_destroy_command_passes_auto_auth_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_destroy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)

    def _fake_destroy_generated_artifacts(
        config: object,
        paths: object,
        loaded_manifest: object,
        *,
        auto_auth_bootstrap: bool,
        yes: bool = False,
    ) -> None:
        captured["config"] = config
        captured["paths"] = paths
        captured["manifest"] = loaded_manifest
        captured["auto_auth_bootstrap"] = auto_auth_bootstrap
        captured["yes"] = yes

    monkeypatch.setattr(cli, "_destroy_generated_artifacts", _fake_destroy_generated_artifacts)

    result = runner.invoke(
        cli.app,
        ["destroy", str(fake_paths.config_path), "--auto-auth-bootstrap", "--yes"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Local destroy completed from" in output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "manifest": manifest,
        "auto_auth_bootstrap": True,
        "yes": True,
    }


def test_destroy_command_accepts_config_yaml_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    captured: dict[str, object] = {}

    def _fake_load(target: Path) -> tuple[object, ProjectPaths, dict[str, str]]:
        captured["target"] = target
        return "cfg", fake_paths, manifest

    monkeypatch.setattr(cli, "_load_destroy_context", _fake_load)
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_destroy_generated_artifacts", lambda *args, **kwargs: None)

    result = runner.invoke(cli.app, ["destroy", str(fake_paths.config_path), "--yes"])

    assert result.exit_code == 0, result.output
    assert captured["target"] == fake_paths.config_path
    assert "Local destroy completed from" in _plain_output(result.output)


def test_destroy_command_rejects_generated_target_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "_load_destroy_context",
        lambda _path: (_ for _ in ()).throw(
            ValueError(
                "Destroy target must be project config.yaml, not generated/. "
                "Pass <tenant-folder>/<project-folder>/config.yaml; destroy resolves sibling generated/ automatically."
            )
        ),
    )

    result = runner.invoke(cli.app, ["destroy", str(tmp_path / "generated")])

    assert result.exit_code != 0
    assert "Destroy target must be project config.yaml, not generated/." in _plain_output(
        result.output
    )


def test_destroy_command_confirmation_targets_infra_only_when_no_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    config = {"infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]}}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(
        cli,
        "_load_destroy_context",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_confirm_generated_destroy",
        lambda **kwargs: captured.update(kwargs) or False,
    )

    result = runner.invoke(cli.app, ["destroy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    assert "No changes applied." in _plain_output(result.output)
    assert captured["action_label"] == "Destroy"
    assert (
        captured["prompt_text"]
        == "Continue and destroy all rendered infra resources for this project?"
    )
    assert captured["warning_text"] == (
        "Destroy will remove all rendered infra resources for this project by running "
        f"Terraform destroy against the rendered infra bundle under "
        f"{fake_paths.infra_dir}."
    )


def test_destroy_command_confirmation_skips_flux_delete_when_cluster_destroy_covers_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    config = {
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
        },
    }

    monkeypatch.setattr(
        cli,
        "_load_destroy_context",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_confirm_generated_destroy",
        lambda **kwargs: captured.update(kwargs) or False,
    )

    result = runner.invoke(cli.app, ["destroy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    assert "No changes applied." in _plain_output(result.output)
    assert (
        captured["prompt_text"]
        == "Continue and destroy all rendered app and infra resources for this project?"
    )
    assert captured["warning_text"] == (
        "Destroy will remove all rendered project resources represented by the generated "
        "manifest by running Terraform destroy against the rendered infra bundle under "
        f"{fake_paths.infra_dir}. Because this bundle destroys the handed-off cluster directly, "
        "it will not delete the rendered app resources under "
        f"{fake_paths.flux_dir} separately first."
    )


def test_destroy_command_confirmation_deletes_flux_first_for_external_cluster_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(
        cli,
        "_load_destroy_context",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_confirm_generated_destroy",
        lambda **kwargs: captured.update(kwargs) or False,
    )

    result = runner.invoke(cli.app, ["destroy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    assert "No changes applied." in _plain_output(result.output)
    assert (
        captured["prompt_text"]
        == "Continue and destroy all rendered app and infra resources for this project?"
    )
    assert captured["warning_text"] == (
        "Destroy will remove all rendered project resources represented by the generated "
        "manifest by deleting the rendered app resources from the target cluster using "
        f"{fake_paths.flux_dir} first and then running Terraform destroy against the rendered "
        f"infra bundle under {fake_paths.infra_dir}."
    )


def test_run_deploy_preflight_runs_strict_quota_backend_terraform_and_flux_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}}
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: calls.append(
            ("strict", config, include_common_checks)
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_mk8s_network_preflight",
        lambda config: calls.append(("mk8s", config)),
    )
    monkeypatch.setattr(
        cli,
        "_raise_on_generated_bundle_live_quota_issues",
        lambda config, paths, *, manifest, runtime_env, phase: calls.append(
            ("quota", config, paths, manifest, runtime_env, phase)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: calls.append(
            ("backend", config, auto_auth_bootstrap)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_terraform_runtime_env",
        lambda config: calls.append(("runtime_env", config)) or {"TF_VAR_DEMO": "1"},
    )
    monkeypatch.setattr(
        cli,
        "terraform_init",
        lambda infra_dir, *, extra_env=None: calls.append(("init", infra_dir, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: calls.append(
            ("validate", infra_dir, extra_env, initialize)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_validate_rendered_flux_manifests",
        lambda paths, *, command_name, manifest=None: calls.append(
            ("flux", paths, command_name, manifest)
        ),
    )

    cli._run_deploy_preflight(
        config,
        fake_paths,
        auto_auth_bootstrap=True,
        manifest={"render": {"module_sources": []}},
    )

    assert calls == [
        ("strict", config, False),
        ("mk8s", config),
        ("backend", config, True),
        ("runtime_env", config),
        (
            "quota",
            config,
            fake_paths,
            {"render": {"module_sources": []}},
            {"TF_VAR_DEMO": "1"},
            "deploy",
        ),
        ("validate", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, False),
        ("flux", fake_paths, "deploy", {"render": {"module_sources": []}}),
    ]


def test_run_deploy_preflight_skips_flux_validation_when_no_apps_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = {"apps": {"charts": []}}
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: calls.append(
            ("strict", config, include_common_checks)
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_mk8s_network_preflight",
        lambda config: calls.append(("mk8s", config)),
    )
    monkeypatch.setattr(
        cli,
        "_raise_on_generated_bundle_live_quota_issues",
        lambda config, paths, *, manifest, runtime_env, phase: calls.append(
            ("quota", config, paths, manifest, runtime_env, phase)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: calls.append(
            ("backend", config, auto_auth_bootstrap)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_terraform_runtime_env",
        lambda config: calls.append(("runtime_env", config)) or {"TF_VAR_DEMO": "1"},
    )
    monkeypatch.setattr(
        cli,
        "terraform_init",
        lambda infra_dir, *, extra_env=None: calls.append(("init", infra_dir, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: calls.append(
            ("validate", infra_dir, extra_env, initialize)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_validate_rendered_flux_manifests",
        lambda paths, *, command_name, manifest=None: calls.append(
            ("flux", paths, command_name, manifest)
        ),
    )

    cli._run_deploy_preflight(
        config,
        fake_paths,
        auto_auth_bootstrap=False,
        manifest={"render": {"module_sources": []}},
    )

    assert calls == [
        ("strict", config, False),
        ("mk8s", config),
        ("backend", config, False),
        ("runtime_env", config),
        (
            "quota",
            config,
            fake_paths,
            {"render": {"module_sources": []}},
            {"TF_VAR_DEMO": "1"},
            "deploy",
        ),
        ("validate", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, False),
    ]


def test_generated_bundle_live_quota_failure_prints_remediation_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    rendered_messages: list[str] = []
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-25T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=16,
                reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                unit="count",
                available=4,
                sufficient=False,
                tenant_limit=4,
                tenant_usage=0,
                project_limit=None,
                project_usage=0,
                source_scope="capacity-dashboard/on-demand",
                description=(
                    "Capacity Dashboard GPU availability "
                    "(on-demand VM slots, fabric fabric-4, converted to GPU units)"
                ),
                contributors=(),
            ),
        ),
    )

    monkeypatch.setattr(cli, "terraform_init", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_assess_live_quota_report", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_quota_requirements_from_terraform_state",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    with pytest.raises(RuntimeError, match="insufficient for deploy"):
        cli._raise_on_generated_bundle_live_quota_issues(
            "cfg",
            fake_paths,
            manifest={"render": {"module_sources": []}},
            runtime_env={},
            phase="deploy",
        )

    output = "\n".join(rendered_messages)
    assert "Next step: review and submit quota requests with:" in output
    assert f"nebius-cxcli quota-request {fake_paths.config_path}" in output
    assert "Next step: compare quota availability across regions with:" in output
    assert f"nebius-cxcli quota-check --all-regions {fake_paths.config_path}" in output


def test_adjust_quota_report_for_managed_mk8s_state_discounts_existing_cluster_capacity() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-21T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s-1",
                component_label="mk8s mk8s-1",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=16,
                reason="mk8s mk8s-1: 2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                unit="count",
                available=8,
                sufficient=False,
                tenant_limit=24,
                tenant_usage=16,
                project_limit=24,
                project_usage=16,
                source_scope="capacity-dashboard/on-demand",
                description=(
                    "Capacity Dashboard GPU availability "
                    "(on-demand VM slots, fabric fabric-4, converted to GPU units)"
                ),
                contributors=(
                    cli.QuotaContributor(
                        component_id="mk8s",
                        instance_id="mk8s-1",
                        component_label="mk8s mk8s-1",
                        required=16,
                        reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                    ),
                ),
            ),
        ),
    )

    adjusted = cli._adjust_quota_report_for_managed_mk8s_state(
        report,
        managed_requirements=(
            SimpleNamespace(
                component_id="mk8s",
                instance_id="mk8s-1",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=16,
            ),
        ),
    )

    assert adjusted.checks == ()
    assert adjusted.has_confirmed_insufficiency is False


def test_managed_mk8s_quota_requirements_from_terraform_state_maps_generated_module_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "client_info": {
            "nebius": {
                "project_id": "project-456",
                "region_id": "eu-north1",
            }
        },
        "infra": {"components": [{"id": "mk8s", "instance_id": "mk8s-1", "enabled": True}]},
    }
    manifest = {
        "render": {
            "module_sources": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s-1",
                    "module_name": "cluster_main",
                    "source": "git::https://example.invalid/platform-infra.git//modules/mk8s",
                }
            ]
        }
    }
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli, "terraform_state_list", lambda *_args, **_kwargs: ("module.cluster_main",)
    )
    monkeypatch.setattr(
        cli,
        "terraform_show_json",
        lambda *_args, **_kwargs: {
            "values": {
                "root_module": {
                    "child_modules": [
                        {
                            "address": "module.cluster_main",
                            "resources": [
                                {
                                    "address": "module.cluster_main.nebius_mk8s_v1_cluster.this",
                                    "type": "nebius_mk8s_v1_cluster",
                                    "values": {
                                        "parent_id": "project-456",
                                    },
                                },
                                {
                                    "address": "module.cluster_main.nebius_compute_v1_gpu_cluster.this[0]",
                                    "type": "nebius_compute_v1_gpu_cluster",
                                    "values": {
                                        "infiniband_fabric": "fabric-6",
                                    },
                                },
                                {
                                    "address": "module.cluster_main.nebius_mk8s_v1_node_group.gpu[0]",
                                    "type": "nebius_mk8s_v1_node_group",
                                    "values": {
                                        "fixed_node_count": 2,
                                        "template": {
                                            "resources": {
                                                "platform": "gpu-h100-sxm",
                                                "preset": "8gpu-128vcpu-1600gb",
                                            },
                                            "boot_disk": {
                                                "type": "NETWORK_SSD",
                                                "size_gibibytes": 200,
                                            },
                                            "network_interfaces": [
                                                {"public_ip_address": None},
                                            ],
                                        },
                                    },
                                },
                            ],
                        }
                    ]
                }
            }
        },
    )
    monkeypatch.setattr(
        cli,
        "estimate_mk8s_quota_requirements",
        lambda *, project_id, region, instance_id, inputs, context: (
            captured.update(
                {
                    "project_id": project_id,
                    "region": region,
                    "instance_id": instance_id,
                    "inputs": dict(inputs),
                    "context": context,
                }
            )
            or (
                (
                    SimpleNamespace(
                        component_id="mk8s",
                        instance_id=instance_id,
                        quota_name="compute.instance.gpu.h100",
                        region=region,
                        required=16,
                    ),
                ),
                (),
            )
        ),
    )

    requirements = cli._managed_mk8s_quota_requirements_from_terraform_state(
        config,
        fake_paths,
        manifest,
        runtime_env={"TF_VAR_demo": "1"},
    )

    assert len(requirements) == 1
    assert captured["project_id"] == "project-456"
    assert captured["region"] == "eu-north1"
    assert captured["instance_id"] == "mk8s-1"
    assert captured["context"] == "generated-bundle quota baseline"
    assert captured["inputs"] == {
        "gpu_enabled": True,
        "gpu_node_groups": 1,
        "gpu_nodes_count_per_group": 2,
        "gpu_nodes_platform": "gpu-h100-sxm",
        "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
        "gpu_nodes_boot_disk_type": "NETWORK_SSD",
        "gpu_nodes_boot_disk_size_gib": 200,
        "infiniband_fabric": "fabric-6",
    }


def test_validate_generated_mk8s_resource_name_preflight_skips_when_no_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        cli,
        "terraform_state_list",
        lambda *args, **kwargs: calls.append(("state_list", args, kwargs)) or (),
    )

    cli._validate_generated_mk8s_resource_name_preflight(
        {"apps": {"charts": []}},
        fake_paths,
        runtime_env={"TF_VAR_DEMO": "1"},
    )

    assert calls == []


def test_validate_generated_mk8s_resource_name_preflight_passes_state_managed_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "client_info": {
            "nebius": {
                "project_id": "project-123",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": {
                        "parent_id": "project-123",
                        "cluster_name": "cluster-a",
                        "gpu_enabled": True,
                        "infiniband_fabric": "fabric-1",
                    },
                }
            ]
        },
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "terraform_state_list",
        lambda infra_dir, *, extra_env=None, initialize=True: (
            "module.mk8s.nebius_mk8s_v1_cluster.this",
            "module.mk8s.nebius_compute_v1_gpu_cluster.this[0]",
        ),
    )
    monkeypatch.setattr(
        cli,
        "terraform_state_show",
        lambda infra_dir, address, *, extra_env=None, initialize=True: (
            'name = "cluster-a"\n'
            if "mk8s_v1_cluster" in address
            else 'name = "cluster-a-gpu-cluster"\n'
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_mk8s_resource_name_preflight",
        lambda current_config, *, managed_mk8s_cluster_names, managed_gpu_cluster_names: (
            captured.update(
                {
                    "config": current_config,
                    "managed_mk8s_cluster_names": managed_mk8s_cluster_names,
                    "managed_gpu_cluster_names": managed_gpu_cluster_names,
                }
            )
        ),
    )

    cli._validate_generated_mk8s_resource_name_preflight(
        config,
        fake_paths,
        runtime_env={"TF_VAR_DEMO": "1"},
    )

    assert captured == {
        "config": config,
        "managed_mk8s_cluster_names": {"cluster-a"},
        "managed_gpu_cluster_names": {"cluster-a-gpu-cluster"},
    }


def test_deploy_generated_artifacts_validates_before_apply_and_prepares_kube_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True, "target_ref": "mk8s"}]}}
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, auto_auth_bootstrap, manifest=None: calls.append(
            ("preflight", config, paths, auto_auth_bootstrap, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True, run_mk8s_preflight=True: calls.append(
            ("apply_with_status", config, paths, initialize, run_mk8s_preflight)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            calls.append(
                (
                    "kube_env",
                    config,
                    paths,
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: calls.append(("cluster_status", extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None: calls.append(
            ("warn_bootstrap", config, paths, extra_env, target_ref)
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.inventory_dir / "deploy-report.md")
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("preflight", config, fake_paths, True, manifest),
        ("apply_with_status", config, fake_paths, False, False),
        ("inventory", config, fake_paths),
        (
            "kube_env",
            config,
            fake_paths,
            _mk8s_target(fake_paths),
            True,
            True,
        ),
        ("cluster_status", {"KUBECONFIG": "/tmp/kubeconfig"}),
        ("flux", _target_paths(fake_paths), {"KUBECONFIG": "/tmp/kubeconfig"}),
        (
            "warn_bootstrap",
            config,
            _target_paths(fake_paths),
            {"KUBECONFIG": "/tmp/kubeconfig"},
            "mk8s",
        ),
    ]


def test_deploy_generated_artifacts_without_apps_still_prepares_kube_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, auto_auth_bootstrap, manifest=None: calls.append(
            ("preflight", config, paths, auto_auth_bootstrap, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True, run_mk8s_preflight=True: calls.append(
            ("apply_with_status", config, paths, initialize, run_mk8s_preflight)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            calls.append(
                (
                    "kube_env",
                    config,
                    paths,
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: calls.append(("cluster_status", extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None: calls.append(
            ("warn_bootstrap", config, paths, extra_env)
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.inventory_dir / "deploy-report.md")
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("preflight", config, fake_paths, True, manifest),
        ("apply_with_status", config, fake_paths, False, False),
        ("inventory", config, fake_paths),
        (
            "kube_env",
            config,
            fake_paths,
            _mk8s_target(fake_paths),
            True,
            True,
        ),
    ]


def test_deploy_generated_artifacts_with_multiple_handoffs_and_no_apps_refreshes_all_kubeconfigs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths),
                _mk8s_target(fake_paths, target_ref="mk8s-2"),
            ],
            "validations": [],
        }
    }
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, auto_auth_bootstrap, manifest=None: calls.append(
            ("preflight", config, paths, auto_auth_bootstrap, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True, run_mk8s_preflight=True: calls.append(
            ("apply_with_status", config, paths, initialize, run_mk8s_preflight)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            calls.append(
                (
                    "kube_env",
                    config,
                    paths,
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: calls.append(("cluster_status", extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.inventory_dir / "deploy-report.md")
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("preflight", config, fake_paths, True, manifest),
        ("apply_with_status", config, fake_paths, False, False),
        ("inventory", config, fake_paths),
        (
            "kube_env",
            config,
            fake_paths,
            _mk8s_target(fake_paths),
            True,
            False,
        ),
        (
            "kube_env",
            config,
            fake_paths,
            _mk8s_target(fake_paths, target_ref="mk8s-2"),
            True,
            False,
        ),
    ]


def test_deploy_generated_artifacts_runs_manifest_gpu_validations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU Visibility test",
                    "namespace": "gpu-validation",
                    "target_ref": "mk8s",
                }
            ],
        }
    }
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.inventory_dir / "deploy-report.md")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            calls.append(
                (
                    "kube_env",
                    config,
                    paths,
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: calls.append(("cluster_status", extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "run_mk8s_gpu_validations",
        lambda validations, *, inventory_dir, extra_env, emit=None: (
            calls.append(("gpu_validations", validations, inventory_dir, extra_env))
            or [inventory_dir / "gpu-visibility-report.json"]
        ),
    )

    class _FakeStatus:
        def update(self, _message: str, **_kwargs: object) -> None:
            return

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(cli.console, "print", lambda *args, **kwargs: None)

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("inventory", config, fake_paths),
        (
            "kube_env",
            config,
            fake_paths,
            _mk8s_target(fake_paths),
            True,
            True,
        ),
        ("cluster_status", {"KUBECONFIG": "/tmp/kubeconfig"}),
        (
            "gpu_validations",
            [
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU Visibility test",
                    "namespace": "gpu-validation",
                    "target_ref": "mk8s",
                }
            ],
            fake_paths.inventory_dir,
            {"KUBECONFIG": "/tmp/kubeconfig"},
        ),
        ("inventory", config, fake_paths),
    ]


def test_deploy_generated_artifacts_rejects_manifest_missing_deploy_validations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {"deploy": {"targets": [_mk8s_target(fake_paths)]}}
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)

    with pytest.raises(
        RuntimeError,
        match="Generated manifest is missing deploy\\.validations metadata",
    ):
        cli._deploy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            auto_auth_bootstrap=True,
            skip_validations=False,
            skip_validation_kinds=set(),
        )


def test_deploy_generated_artifacts_rejects_manifest_missing_deploy_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)

    with pytest.raises(
        RuntimeError,
        match="Generated manifest is missing deploy\\.validations metadata",
    ):
        cli._deploy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            auto_auth_bootstrap=True,
            skip_validations=False,
            skip_validation_kinds=set(),
        )


def test_deploy_generated_artifacts_updates_validation_spinner_when_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_operator_readiness",
                    "name": "GPU stack readiness",
                    "namespace": "gpu-operator",
                    "report_file": "gpu-stack-readiness-report.json",
                    "target_ref": "mk8s",
                },
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU Visibility test",
                    "namespace": "gpu-validation",
                    "report_file": "gpu-visibility-report.json",
                    "target_ref": "mk8s",
                },
            ],
        }
    }
    status_start: list[tuple[str, str | None]] = []
    status_updates: list[str] = []
    printed: list[str] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_inventory", write_inventory_artifacts)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    def _fake_run_mk8s_gpu_validations(
        validations: list[dict[str, object]],
        *,
        inventory_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert validations == manifest["deploy"]["validations"]
        assert inventory_dir == fake_paths.inventory_dir
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert emit is not None
        inventory_dir.mkdir(parents=True, exist_ok=True)
        emit("Starting validation 1/2: GPU stack readiness.")
        emit("[bold white]GPU Operator[/bold white] [dim][5s][/dim] clusterpolicy state=ready")
        (inventory_dir / "gpu-stack-readiness-report.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "gpu_operator": {"gpu_nodes": [{"name": "gpu-node-a"}]},
                    "network_operator": {"required": False},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        emit("Starting validation 2/2: GPU Visibility test.")
        emit("[bold white]GPU Visibility[/bold white] [dim][9s][/dim] pods 3/3 Succeeded")
        (inventory_dir / "gpu-visibility-report.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "selected_node_count": 3,
                    "total_gpu_node_count": 3,
                    "passed_node_count": 3,
                    "skipped_node_count": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return [
            inventory_dir / "gpu-stack-readiness-report.json",
            inventory_dir / "gpu-visibility-report.json",
        ]

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    class _FakeStatus:
        def update(self, message: str, **_kwargs: object) -> None:
            status_updates.append(message)

    @contextmanager
    def _fake_status(message: str, **kwargs: object):
        spinner = kwargs.get("spinner")
        status_start.append((message, spinner if isinstance(spinner, str) else None))
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert status_start == [("[cyan]Running MK8s GPU validations for mk8s...[/cyan]", "dots")]
    assert status_updates == [
        "Starting validation 1/2: GPU stack readiness.",
        "[bold white]GPU Operator[/bold white] [dim][5s][/dim] clusterpolicy state=ready",
        "Starting validation 2/2: GPU Visibility test.",
        "[bold white]GPU Visibility[/bold white] [dim][9s][/dim] pods 3/3 Succeeded",
    ]
    assert printed == [
        "Deploy validation summary:",
        "  Overall: PASS (2/2 completed, 0 not run)",
        "  PASS GPU stack readiness: GPU Operator ready on 1 Ready GPU node(s).",
        "  PASS GPU Visibility test: 3/3 selected node(s) passed; total Ready GPU nodes 3.",
        f"  Combined report: {fake_paths.inventory_dir / 'deploy-report.md'}",
        f"  JSON detail: {fake_paths.inventory_dir / 'gpu-stack-readiness-report.json'}",
        f"  JSON detail: {fake_paths.inventory_dir / 'gpu-visibility-report.json'}",
    ]


def test_deploy_generated_artifacts_prints_validation_phase_lines_when_console_is_not_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU Visibility test",
                    "namespace": "gpu-validation",
                    "report_file": "gpu-visibility-report.json",
                    "target_ref": "mk8s",
                }
            ],
        }
    }
    status_updates: list[str] = []
    printed: list[str] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_inventory", write_inventory_artifacts)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: False)

    def _fake_run_mk8s_gpu_validations(
        _validations: list[dict[str, object]],
        *,
        inventory_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert inventory_dir == fake_paths.inventory_dir
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert emit is not None
        inventory_dir.mkdir(parents=True, exist_ok=True)
        emit("Starting validation 1/1: GPU Visibility test.")
        emit("Starting validation 1/1: GPU Visibility test.")
        emit("[bold white]GPU Visibility[/bold white] [dim][7s][/dim] pods 3/3 Succeeded")
        (inventory_dir / "gpu-visibility-report.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "selected_node_count": 3,
                    "total_gpu_node_count": 3,
                    "passed_node_count": 3,
                    "skipped_node_count": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return [inventory_dir / "gpu-visibility-report.json"]

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    class _FakeStatus:
        def update(self, message: str, **_kwargs: object) -> None:
            status_updates.append(message)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert status_updates == [
        "Starting validation 1/1: GPU Visibility test.",
        "Starting validation 1/1: GPU Visibility test.",
        "[bold white]GPU Visibility[/bold white] [dim][7s][/dim] pods 3/3 Succeeded",
    ]
    assert printed == [
        "Starting validation 1/1: GPU Visibility test.",
        "[bold white]GPU Visibility[/bold white] [dim][7s][/dim] pods 3/3 Succeeded",
        "Deploy validation summary:",
        "  Overall: PASS (1/1 completed, 0 not run)",
        "  PASS GPU Visibility test: 3/3 selected node(s) passed; total Ready GPU nodes 3.",
        f"  Combined report: {fake_paths.inventory_dir / 'deploy-report.md'}",
        f"  JSON detail: {fake_paths.inventory_dir / 'gpu-visibility-report.json'}",
    ]


def test_deploy_generated_artifacts_writes_summary_even_when_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "apps": {"charts": []},
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_operator_readiness",
                    "name": "GPU stack readiness",
                    "report_file": "gpu-stack-readiness-report.json",
                    "target_ref": "mk8s",
                },
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU Visibility test",
                    "report_file": "gpu-visibility-report.json",
                    "target_ref": "mk8s",
                },
            ],
        }
    }
    printed: list[str] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_inventory", write_inventory_artifacts)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    def _fake_run_mk8s_gpu_validations(
        _validations: list[dict[str, object]],
        *,
        inventory_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert emit is not None
        inventory_dir.mkdir(parents=True, exist_ok=True)
        emit("Starting validation 1/2: GPU stack readiness.")
        (inventory_dir / "gpu-stack-readiness-report.json").write_text(
            json.dumps(
                {
                    "passed": False,
                    "gpu_operator": {"gpu_nodes": []},
                    "network_operator": {"required": False},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "GPU stack readiness check failed. Report: gpu-stack-readiness-report.json"
        )

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )

    with pytest.raises(RuntimeError, match="GPU stack readiness check failed"):
        cli._deploy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            auto_auth_bootstrap=True,
            skip_validations=False,
            skip_validation_kinds=set(),
        )

    markdown = (fake_paths.inventory_dir / "deploy-report.md").read_text(encoding="utf-8")
    assert "- Overall status: `FAIL`" in markdown
    assert "### GPU stack readiness" in markdown
    assert "### GPU Visibility test" in markdown
    assert "No deploy validation results recorded yet." in markdown
    assert printed == [
        "Deploy validation summary:",
        "  Overall: FAIL (1/2 completed, 1 not run)",
        "  FAIL GPU stack readiness: GPU Operator ready on 0 Ready GPU node(s).",
        "  NOT RUN GPU Visibility test: No deploy validation results recorded yet.",
        f"  Combined report: {fake_paths.inventory_dir / 'deploy-report.md'}",
        f"  JSON detail: {fake_paths.inventory_dir / 'gpu-stack-readiness-report.json'}",
    ]


def test_raise_on_live_quota_issues_fails_only_on_confirmed_insufficiency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_warn_on_live_quota_issues",
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

    with pytest.raises(
        RuntimeError,
        match=(
            "Increase the quota, or for GPU shortages choose a platform/preset/fabric "
            "with available Capacity Dashboard capacity, and retry"
        ),
    ):
        cli._raise_on_live_quota_issues("cfg", phase="deploy")


def test_destroy_generated_artifacts_destroys_flux_before_terraform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {
            "status_watchers": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "kind": "nebius.mk8s.cluster",
                    "parent_id": "project-456",
                    "resource_name": "cluster-a",
                }
            ]
        },
    }
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda current_config, *, auto_auth_bootstrap: calls.append(
            ("backend", current_config, auto_auth_bootstrap)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda current_config, paths, loaded_manifest: calls.append(
            ("destroy_flux", current_config, paths, loaded_manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda current_config, paths, *, auto_auth_bootstrap, yes, initialize=True, status_watchers=None: (
            calls.append(
                (
                    "destroy_tf",
                    current_config,
                    paths,
                    auto_auth_bootstrap,
                    yes,
                    initialize,
                    status_watchers,
                )
            )
        ),
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        yes=True,
    )

    assert calls == [
        ("backend", config, True),
        ("destroy_flux", config, fake_paths, manifest),
        (
            "destroy_tf",
            config,
            fake_paths,
            True,
            True,
            True,
            [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "kind": "nebius.mk8s.cluster",
                    "parent_id": "project-456",
                    "resource_name": "cluster-a",
                }
            ],
        ),
    ]


def test_destroy_generated_artifacts_continues_when_flux_teardown_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    captured: dict[str, object] = {}
    messages: list[str] = []

    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cluster unreachable")),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda current_config, paths, *, auto_auth_bootstrap, yes, initialize=True, status_watchers=None: (
            captured.setdefault(
                "destroy",
                {
                    "config": current_config,
                    "paths": paths,
                    "auto_auth_bootstrap": auto_auth_bootstrap,
                    "yes": yes,
                    "initialize": initialize,
                    "status_watchers": status_watchers,
                },
            )
        ),
    )
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: messages.append(str(message))
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        yes=True,
    )

    assert captured["destroy"] == {
        "config": config,
        "paths": fake_paths,
        "auto_auth_bootstrap": True,
        "yes": True,
        "initialize": True,
        "status_watchers": None,
    }
    assert any(
        "Rendered app teardown failed before infra destroy" in message for message in messages
    )
    assert any("cluster unreachable" in message for message in messages)


def test_destroy_generated_artifacts_skips_flux_teardown_when_handoff_cluster_is_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_mk8s_target(fake_paths)]},
    }
    captured: dict[str, object] = {}
    messages: list[str] = []

    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda current_config, paths, *, auto_auth_bootstrap, yes, initialize=True, status_watchers=None: (
            captured.setdefault(
                "destroy",
                {
                    "config": current_config,
                    "paths": paths,
                    "auto_auth_bootstrap": auto_auth_bootstrap,
                    "yes": yes,
                    "initialize": initialize,
                    "status_watchers": status_watchers,
                },
            )
        ),
    )
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: messages.append(str(message))
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
        yes=True,
    )

    assert captured["destroy"] == {
        "config": config,
        "paths": fake_paths,
        "auto_auth_bootstrap": True,
        "yes": True,
        "initialize": True,
        "status_watchers": None,
    }
    assert any(
        "Skipping rendered app teardown before infra destroy because this generated bundle destroys "
        "the handed-off cluster directly." in message
        for message in messages
    )


def test_apply_rendered_flux_installs_flux_controllers_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    cache_dirs: list[Path | None] = []
    status_start: list[tuple[str, str | None]] = []
    status_updates: list[str] = []
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)

    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(
        cli,
        "flux_controllers_installed",
        lambda *, extra_env=None: False,
    )
    monkeypatch.setattr(
        cli,
        "flux_crds_installed",
        lambda *, extra_env=None: False,
    )
    monkeypatch.setattr(
        cli,
        "install_flux_controllers",
        lambda *, extra_env=None: (
            calls.append(("install_flux", extra_env))
            or "https://github.com/fluxcd/flux2/releases/download/v2.8.0/install.yaml"
        ),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_flux_resource_apis",
        lambda paths, *, extra_env=None, cache_dir=None: (
            cache_dirs.append(cache_dir),
            calls.append(("wait_flux_apis", paths, extra_env, cache_dir)),
        )[-1],
    )
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda paths, *, extra_env=None, emit=None: calls.append(("wait_flux", paths, extra_env)),
    )

    def _fake_run(
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: int | None = None,
        check: bool = False,
    ) -> SimpleNamespace:
        calls.append(("run", tuple(cmd), env, capture_output, text, timeout, check))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    class _FakeStatus:
        def update(self, message: str, **_kwargs: object) -> None:
            status_updates.append(message)

    @contextmanager
    def _fake_status(message: str, **kwargs: object):
        spinner = kwargs.get("spinner")
        status_start.append((message, spinner if isinstance(spinner, str) else None))
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert ("install_flux", {"KUBECONFIG": "/tmp/kubeconfig"}) in calls
    assert any(call[0] == "run" and call[1] == ("kubectl", "cluster-info") for call in calls)
    assert len(cache_dirs) == 1
    cache_dir = cache_dirs[0]
    assert isinstance(cache_dir, Path)
    assert any(
        call[0] == "run"
        and call[1]
        == ("kubectl", "--cache-dir", str(cache_dir), "apply", "-k", str(fake_paths.flux_dir))
        for call in calls
    )
    assert ("wait_flux_apis", fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"}, cache_dir) in calls
    assert ("wait_flux", fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"}) in calls
    assert status_start == [
        ("[cyan]Preparing Flux deployment...[/cyan]", "dots"),
    ]
    assert status_updates == [
        "[cyan]Checking target Kubernetes cluster reachability...[/cyan]",
        "[cyan]Installing Flux controllers into the target cluster...[/cyan]",
        "[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]",
        "[cyan]Applying rendered Flux manifests to the target cluster...[/cyan]",
        "[cyan]Waiting for rendered Flux resources to become Ready...[/cyan]",
    ]


def test_apply_rendered_flux_skips_flux_install_when_controllers_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    cache_dirs: list[Path | None] = []
    status_start: list[tuple[str, str | None]] = []
    status_updates: list[str] = []
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)

    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(
        cli,
        "flux_controllers_installed",
        lambda *, extra_env=None: True,
    )
    monkeypatch.setattr(
        cli,
        "flux_crds_installed",
        lambda *, extra_env=None: True,
    )
    monkeypatch.setattr(
        cli,
        "install_flux_controllers",
        lambda *, extra_env=None: calls.append(("install_flux", extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_flux_resource_apis",
        lambda paths, *, extra_env=None, cache_dir=None: (
            cache_dirs.append(cache_dir),
            calls.append(("wait_flux_apis", paths, extra_env, cache_dir)),
        )[-1],
    )
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda paths, *, extra_env=None, emit=None: calls.append(("wait_flux", paths, extra_env)),
    )

    def _fake_run(
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: int | None = None,
        check: bool = False,
    ) -> SimpleNamespace:
        calls.append(("run", tuple(cmd), env, capture_output, text, timeout, check))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    class _FakeStatus:
        def update(self, message: str, **_kwargs: object) -> None:
            status_updates.append(message)

    @contextmanager
    def _fake_status(message: str, **kwargs: object):
        spinner = kwargs.get("spinner")
        status_start.append((message, spinner if isinstance(spinner, str) else None))
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert not any(call[0] == "install_flux" for call in calls)
    assert len(cache_dirs) == 1
    cache_dir = cache_dirs[0]
    assert isinstance(cache_dir, Path)
    assert any(
        call[0] == "run"
        and call[1]
        == ("kubectl", "--cache-dir", str(cache_dir), "apply", "-k", str(fake_paths.flux_dir))
        for call in calls
    )
    assert ("wait_flux_apis", fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"}, cache_dir) in calls
    assert ("wait_flux", fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"}) in calls
    assert status_start == [
        ("[cyan]Preparing Flux deployment...[/cyan]", "dots"),
    ]
    assert status_updates == [
        "[cyan]Checking target Kubernetes cluster reachability...[/cyan]",
        "[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]",
        "[cyan]Applying rendered Flux manifests to the target cluster...[/cyan]",
        "[cyan]Waiting for rendered Flux resources to become Ready...[/cyan]",
    ]


def test_apply_rendered_flux_prints_phase_lines_when_console_is_not_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)
    printed: list[str] = []

    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(cli, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(cli, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(cli, "wait_for_flux_resource_apis", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "wait_for_rendered_flux_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: False)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )

    class _FakeStatus:
        def update(self, _message: str, **_kwargs: object) -> None:
            return

    @contextmanager
    def _fake_status(message: str, **kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert printed == [
        "[cyan]Checking target Kubernetes cluster reachability...[/cyan]",
        "[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]",
        "[cyan]Applying rendered Flux manifests to the target cluster...[/cyan]",
        "[cyan]Waiting for rendered Flux resources to become Ready...[/cyan]",
    ]


def test_apply_rendered_flux_skips_when_no_rendered_resources_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    printed: list[str] = []

    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: False)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert printed == ["No rendered Flux resources are present; skipping local Flux apply."]


def test_apply_rendered_flux_private_handoff_reports_network_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)

    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="dial tcp 10.0.0.10:443: i/o timeout\n",
            stdout="",
        ),
    )
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    class _FakeStatus:
        def update(self, _message: str, **_kwargs: object) -> None:
            return

    @contextmanager
    def _fake_status(message: str, **kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    with pytest.raises(RuntimeError, match="private MK8s control-plane endpoint"):
        cli._apply_rendered_flux(
            fake_paths,
            extra_env={
                "KUBECONFIG": "/tmp/kubeconfig",
                flux_ops.CLUSTER_HANDOFF_ACCESS_ENV: "internal",
            },
        )


def test_filter_benign_kubectl_output_removes_known_noise() -> None:
    raw = "\n".join(
        [
            "token from NEBIUS_IAM_TOKEN env is used",
            (
                "Warning: resource helmreleases/demo is missing the "
                "kubectl.kubernetes.io/last-applied-configuration annotation"
            ),
            "The missing annotation will be patched automatically.",
            "helmrelease.helm.toolkit.fluxcd.io/demo configured",
        ]
    )

    filtered = cli._filter_benign_kubectl_output(raw)

    assert "token from NEBIUS_IAM_TOKEN env is used" not in filtered
    assert "last-applied-configuration annotation" not in filtered
    assert "patched automatically" not in filtered
    assert "helmrelease.helm.toolkit.fluxcd.io/demo configured" in filtered


def test_apply_rendered_flux_reinstalls_when_flux_crds_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)

    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(
        cli,
        "flux_controllers_installed",
        lambda *, extra_env=None: True,
    )
    monkeypatch.setattr(
        cli,
        "flux_crds_installed",
        lambda *, extra_env=None: False,
    )
    monkeypatch.setattr(
        cli,
        "install_flux_controllers",
        lambda *, extra_env=None: (
            calls.append(("install_flux", extra_env))
            or "https://github.com/fluxcd/flux2/releases/download/v2.8.0/install.yaml"
        ),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_flux_resource_apis",
        lambda paths, *, extra_env=None, cache_dir=None: calls.append(
            ("wait_flux_apis", paths, extra_env, cache_dir)
        ),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda paths, *, extra_env=None, emit=None: calls.append(("wait_flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kwargs: (
            calls.append(("run", tuple(cmd))),
            SimpleNamespace(returncode=0, stderr="", stdout=""),
        )[-1],
    )

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert ("install_flux", {"KUBECONFIG": "/tmp/kubeconfig"}) in calls


def test_wait_for_rendered_flux_resources_waits_for_sources_before_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": [
                    "./helm-repositories.yaml",
                    "./namespace-demo.yaml",
                    "./helmrelease-demo.yaml",
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helm-repositories.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmRepository",
                "metadata": {"name": "demo", "namespace": "flux-system"},
                "spec": {"interval": "30m", "url": "oci://example.invalid/demo", "type": "oci"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "namespace-demo.yaml").write_text(
        yaml.safe_dump(
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "demo"}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-demo.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "demo", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "demo"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_get_target",
        lambda target, *, env, timeout_seconds=20: (
            calls.append((target.kind, target.namespace, target.name))
            or ({"status": {"conditions": [{"type": "Ready", "status": "True"}]}}, "")
        ),
    )

    flux_ops.wait_for_rendered_flux_resources(
        SimpleNamespace(flux_dir=flux_dir),
        poll_interval_seconds=0.01,
    )

    assert calls == [
        ("HelmRepository", "flux-system", "demo"),
        ("HelmRelease", "demo", "demo"),
    ]


def test_wait_for_rendered_flux_resources_raises_with_guidance_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["./helmrelease-demo.yaml"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-demo.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "demo", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "demo"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_get_target",
        lambda target, *, env, timeout_seconds=20: (
            {
                "status": {
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "False",
                            "reason": "InstallFailed",
                            "message": "chart pull failed",
                        }
                    ]
                }
            },
            "",
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="kubectl -n demo describe helmrelease\\.helm\\.toolkit\\.fluxcd\\.io/demo",
    ):
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=flux_dir),
            timeout_seconds=0,
        )


def test_wait_for_rendered_flux_resources_fails_fast_on_terminal_workload_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["./helm-repositories.yaml", "./helmrelease-demo.yaml"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helm-repositories.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmRepository",
                "metadata": {"name": "demo", "namespace": "flux-system"},
                "spec": {"interval": "30m", "url": "oci://example.invalid/demo", "type": "oci"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-demo.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "demo", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "demo"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)

    def _fake_get_target(target, *, env, timeout_seconds=20):
        if target.kind == "HelmRepository":
            return ({}, "")
        return (
            {
                "status": {
                    "conditions": [
                        {
                            "type": "Stalled",
                            "status": "True",
                            "reason": "RetriesExceeded",
                            "message": "Failed to install after 1 attempt(s)",
                        },
                        {
                            "type": "Ready",
                            "status": "False",
                            "reason": "InstallFailed",
                            "message": "startup api check failed",
                        },
                    ]
                }
            },
            "",
        )

    monkeypatch.setattr(flux_ops, "_kubectl_get_target", _fake_get_target)

    with pytest.raises(
        RuntimeError,
        match="One or more rendered Flux resources reached a terminal failure state",
    ):
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=flux_dir),
            timeout_seconds=600,
        )


def test_wait_for_rendered_flux_resources_waits_for_other_workloads_to_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["./helmrelease-failed.yaml", "./helmrelease-slow.yaml"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-failed.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "failed", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "failed"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-slow.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "slow", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "slow"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    call_counts: dict[str, int] = {"failed": 0, "slow": 0}

    def _fake_get_target(target, *, env, timeout_seconds=20):
        call_counts[target.name] += 1
        if target.name == "failed":
            return (
                {
                    "status": {
                        "conditions": [
                            {
                                "type": "Stalled",
                                "status": "True",
                                "reason": "RetriesExceeded",
                                "message": "install failed",
                            },
                            {
                                "type": "Ready",
                                "status": "False",
                                "reason": "InstallFailed",
                                "message": "install failed",
                            },
                        ]
                    }
                },
                "",
            )
        if call_counts["slow"] == 1:
            return (
                {
                    "status": {
                        "conditions": [
                            {
                                "type": "Ready",
                                "status": "False",
                                "reason": "Progressing",
                                "message": "still reconciling",
                            }
                        ]
                    }
                },
                "",
            )
        return ({"status": {"conditions": [{"type": "Ready", "status": "True"}]}}, "")

    monkeypatch.setattr(flux_ops, "_kubectl_get_target", _fake_get_target)

    with pytest.raises(
        RuntimeError,
        match="One or more rendered Flux resources reached a terminal failure state",
    ):
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=flux_dir),
            timeout_seconds=600,
            poll_interval_seconds=0.01,
        )

    assert call_counts["failed"] >= 2
    assert call_counts["slow"] >= 2


def test_flux_wait_targets_capture_rendered_timeout_hints(tmp_path: Path) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["./helmrelease-demo.yaml"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-demo.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "demo", "namespace": "demo"},
                "spec": {
                    "interval": "5m",
                    "timeout": "12m30s",
                    "chart": {"spec": {"chart": "demo"}},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    targets = flux_ops._flux_wait_targets(flux_dir)

    assert len(targets) == 1
    assert targets[0].timeout_seconds == 750
    assert flux_ops._suggested_flux_wait_timeout_seconds(targets) == 810


def test_wait_for_rendered_flux_resources_emits_cluster_status_while_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["./helmrelease-demo.yaml"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-demo.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "demo", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "demo"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    emissions: list[str] = []
    calls = {"count": 0}

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)

    def _fake_get_target(target, *, env, timeout_seconds=20):
        calls["count"] += 1
        if calls["count"] == 1:
            return (
                {
                    "status": {
                        "conditions": [
                            {
                                "type": "Ready",
                                "status": "False",
                                "reason": "Progressing",
                                "message": "waiting for first reconciliation",
                            }
                        ]
                    }
                },
                "",
            )
        return (
            {
                "status": {
                    "conditions": [{"type": "Ready", "status": "True", "reason": "Succeeded"}]
                }
            },
            "",
        )

    monkeypatch.setattr(flux_ops, "_kubectl_get_target", _fake_get_target)
    monkeypatch.setattr(flux_ops.time, "sleep", lambda _seconds: None)

    monotonic_values = iter([0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    monkeypatch.setattr(flux_ops.time, "monotonic", lambda: next(monotonic_values))

    flux_ops.wait_for_rendered_flux_resources(
        SimpleNamespace(flux_dir=flux_dir),
        emit=emissions.append,
        poll_interval_seconds=0.01,
        repeat_interval_seconds=0.01,
    )

    assert len(emissions) >= 2
    plain = "\n".join(_plain_output(item) for item in emissions)
    assert "Flux status" in plain
    assert "HelmRelease" in plain
    assert "demo/demo" in plain
    assert "Progressing" in plain
    assert "waiting for first reconciliation" in plain
    assert "Ready" in plain
    assert "Succeeded" in plain


def test_wait_for_rendered_flux_resources_returns_when_only_sources_remain_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": [
                    "./helm-repositories.yaml",
                    "./helmrelease-demo.yaml",
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helm-repositories.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmRepository",
                "metadata": {"name": "demo", "namespace": "flux-system"},
                "spec": {"interval": "30m", "url": "oci://example.invalid/demo", "type": "oci"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (flux_dir / "helmrelease-demo.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "demo", "namespace": "demo"},
                "spec": {"interval": "5m", "chart": {"spec": {"chart": "demo"}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    emissions: list[str] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)

    def _fake_get_target(target, *, env, timeout_seconds=20):
        if target.kind == "HelmRepository":
            return ({}, "")
        return (
            {
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True", "reason": "InstallSucceeded"}
                    ]
                }
            },
            "",
        )

    monkeypatch.setattr(flux_ops, "_kubectl_get_target", _fake_get_target)

    flux_ops.wait_for_rendered_flux_resources(
        SimpleNamespace(flux_dir=flux_dir),
        emit=emissions.append,
        poll_interval_seconds=0.01,
    )

    plain = "\n".join(_plain_output(item) for item in emissions)
    assert "HelmRepository" in plain
    assert "flux-system/demo" in plain
    assert "controller has not published a Ready condition yet" in plain
    assert "HelmRelease" in plain
    assert "demo/demo" in plain
    assert "InstallSucceeded" in plain
    assert "Rendered HelmRelease workloads are Ready" in plain
    assert "Skipping the remaining wait for Flux source objects" in plain
    assert "kubectl get helmreleases.helm.toolkit.fluxcd.io -A" in plain
    assert "NOTE:" in plain


def test_flux_install_manifest_url_uses_default_pinned_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "v2.8.0",
                    }
                },
                "components": {
                    "infra": {},
                    "apps": {},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()
    assert (
        flux_ops.flux_install_manifest_url()
        == "https://github.com/fluxcd/flux2/releases/download/v2.8.0/install.yaml"
    )


def test_run_terraform_apply_with_status_wraps_apply_in_status_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    reporter = SimpleNamespace(handle_terraform_event="callback")

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(
        config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0
    ):
        calls.append(("status_enter", config, poll_interval_seconds, repeat_interval_seconds))
        emit("hello")
        yield reporter
        calls.append(("status_exit", config))

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)
    monkeypatch.setattr(
        cli,
        "terraform_apply",
        lambda infra_dir, *, extra_env=None, initialize=True, event_callback=None: calls.append(
            ("apply", infra_dir, extra_env, initialize, event_callback)
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message: calls.append(("print", message)),
    )

    cli._run_terraform_apply_with_status("cfg", fake_paths)

    assert calls == [
        ("status_enter", "cfg", 15.0, 60.0),
        ("print", "hello"),
        ("apply", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, True, "callback"),
        ("status_exit", "cfg"),
    ]


def test_run_terraform_apply_with_status_can_skip_mk8s_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    reporter = SimpleNamespace(handle_terraform_event="callback")

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})
    monkeypatch.setattr(
        cli,
        "validate_mk8s_network_preflight",
        lambda config: calls.append(("mk8s_preflight", config)),
    )

    @contextmanager
    def _fake_reporting(
        config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0
    ):
        yield reporter

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)
    monkeypatch.setattr(
        cli,
        "terraform_apply",
        lambda infra_dir, *, extra_env=None, initialize=True, event_callback=None: calls.append(
            ("apply", infra_dir, extra_env, initialize, event_callback)
        ),
    )

    cli._run_terraform_apply_with_status("cfg", fake_paths, run_mk8s_preflight=False)

    assert calls == [
        ("apply", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, True, "callback"),
    ]


def test_run_terraform_apply_with_status_passes_explicit_status_watchers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    reporter = SimpleNamespace(handle_terraform_event="callback")
    watchers = [
        {
            "component_id": "managed-postgresql",
            "kind": "nebius.msp.postgresql.cluster",
            "parent_id": "project-456",
            "resource_name": "pgsql1",
        }
    ]

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(
        config: object,
        *,
        emit,
        poll_interval_seconds=15.0,
        repeat_interval_seconds=60.0,
        status_watchers=None,
    ):
        calls.append(
            (
                "status_enter",
                config,
                poll_interval_seconds,
                repeat_interval_seconds,
                status_watchers,
            )
        )
        emit("hello")
        yield reporter
        calls.append(("status_exit", config))

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)
    monkeypatch.setattr(
        cli,
        "terraform_apply",
        lambda infra_dir, *, extra_env=None, initialize=True, event_callback=None: calls.append(
            ("apply", infra_dir, extra_env, initialize, event_callback)
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message: calls.append(("print", message)),
    )

    cli._run_terraform_apply_with_status("cfg", fake_paths, status_watchers=watchers)

    assert calls == [
        ("status_enter", "cfg", 15.0, 60.0, watchers),
        ("print", "hello"),
        ("apply", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, True, "callback"),
        ("status_exit", "cfg"),
    ]


def test_run_terraform_apply_with_status_appends_last_known_status_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    reporter = SimpleNamespace(
        handle_terraform_event="callback", snapshot=lambda: "Status [10s] TF: failed | API: pending"
    )

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(
        config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0
    ):
        yield reporter

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)

    def _fake_apply(
        infra_dir: Path, *, extra_env=None, initialize=True, event_callback=None
    ) -> None:
        raise RuntimeError("terraform failed")

    monkeypatch.setattr(cli, "terraform_apply", _fake_apply)

    with pytest.raises(RuntimeError, match="Last known deploy status"):
        cli._run_terraform_apply_with_status("cfg", fake_paths)


def test_run_terraform_apply_with_status_passes_abort_check_when_reporter_supports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[str, object]] = []
    reporter = SimpleNamespace(
        handle_terraform_event="callback",
        abort_reason=lambda: None,
        snapshot=lambda: "Status [10s] TF: pending | API: pending",
    )

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(
        config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0
    ):
        yield reporter

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)

    def _fake_apply(
        infra_dir: Path,
        *,
        extra_env=None,
        initialize=True,
        event_callback=None,
        abort_check=None,
    ) -> None:
        calls.append(
            (
                "apply",
                infra_dir,
                extra_env,
                initialize,
                event_callback,
                abort_check,
            )
        )

    monkeypatch.setattr(cli, "terraform_apply", _fake_apply)

    cli._run_terraform_apply_with_status("cfg", fake_paths)

    assert calls == [
        (
            "apply",
            fake_paths.infra_dir,
            {"TF_VAR_DEMO": "1"},
            True,
            "callback",
            reporter.abort_reason,
        )
    ]


def test_run_terraform_destroy_with_status_passes_abort_check_when_reporter_supports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[str, object]] = []
    reporter = SimpleNamespace(
        handle_terraform_event="callback",
        abort_reason=lambda: None,
        snapshot=lambda: "Status [10s] TF: pending | API: pending",
    )

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(
        config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0
    ):
        yield reporter

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)

    def _fake_destroy(
        infra_dir: Path,
        *,
        extra_env=None,
        initialize=True,
        event_callback=None,
        abort_check=None,
    ) -> None:
        calls.append(
            (
                "destroy",
                infra_dir,
                extra_env,
                initialize,
                event_callback,
                abort_check,
            )
        )

    monkeypatch.setattr(cli, "terraform_destroy", _fake_destroy)

    cli._run_terraform_destroy_with_status("cfg", fake_paths)

    assert calls == [
        (
            "destroy",
            fake_paths.infra_dir,
            {"TF_VAR_DEMO": "1"},
            True,
            "callback",
            reporter.abort_reason,
        )
    ]


def test_enabled_status_watcher_specs_resolve_from_enabled_component_inputs() -> None:
    config = {
        "client_info": {
            "nebius": {
                "project_id": "project-456",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-456",
                        "cluster_name": "clust1",
                    },
                },
                {
                    "id": "managed-postgresql",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-456",
                        "name": "pgsql1",
                        "network_id": "vpcnetwork-123",
                    },
                },
                {
                    "id": "ssh-jumphost",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-456",
                        "name": "bastion1",
                    },
                },
                {
                    "id": "mysterybox",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-456",
                        "secrets": {
                            "app": {
                                "name": "app-runtime",
                                "payload_keys": ["API_KEY"],
                            },
                            "worker": {
                                "name": "worker-runtime",
                                "payload_keys": ["TOKEN"],
                            },
                        },
                    },
                },
                {
                    "id": "sfs",
                    "enabled": False,
                    "inputs": {
                        "parent_id": "project-456",
                        "name": "sharedfs",
                    },
                },
            ]
        },
    }

    assert cli._enabled_status_watcher_specs(config) == [
        {
            "component_id": "mk8s",
            "instance_id": "mk8s",
            "kind": "nebius.mk8s.cluster",
            "parent_id": "project-456",
            "resource_name": "clust1",
        },
        {
            "component_id": "managed-postgresql",
            "instance_id": "managed-postgresql",
            "kind": "nebius.msp.postgresql.cluster",
            "parent_id": "project-456",
            "resource_name": "pgsql1",
        },
        {
            "component_id": "ssh-jumphost",
            "instance_id": "ssh-jumphost",
            "kind": "nebius.compute.instance",
            "parent_id": "project-456",
            "resource_name": "bastion1",
        },
        {
            "component_id": "mysterybox",
            "instance_id": "mysterybox",
            "kind": "nebius.mysterybox.secret",
            "parent_id": "project-456",
            "resource_name": "app-runtime",
        },
        {
            "component_id": "mysterybox",
            "instance_id": "mysterybox",
            "kind": "nebius.mysterybox.secret",
            "parent_id": "project-456",
            "resource_name": "worker-runtime",
        },
    ]


def test_terraform_plan_command_invokes_runtime_auth_and_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest))

    def _fake_ensure_terraform_backend_ready(config: object, *, auto_auth_bootstrap: bool) -> None:
        captured["backend"] = {
            "config": config,
            "auto_auth_bootstrap": auto_auth_bootstrap,
        }

    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", _fake_ensure_terraform_backend_ready
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})
    monkeypatch.setattr(
        cli,
        "terraform_init",
        lambda infra_dir, *, extra_env=None: captured.setdefault(
            "init", {"infra_dir": infra_dir, "extra_env": extra_env}
        ),
    )
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: captured.setdefault(
            "validate",
            {"infra_dir": infra_dir, "extra_env": extra_env, "initialize": initialize},
        ),
    )

    def _fake_terraform_plan(
        infra_dir: Path,
        *,
        extra_env: dict[str, str] | None = None,
        initialize: bool = True,
    ) -> None:
        captured["plan"] = {
            "infra_dir": infra_dir,
            "extra_env": extra_env,
            "initialize": initialize,
        }

    monkeypatch.setattr(cli, "terraform_plan", _fake_terraform_plan)

    result = runner.invoke(
        cli.app,
        ["terraform", "plan", str(tmp_path / "generated"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == {
        "config": "cfg",
        "auto_auth_bootstrap": True,
    }
    assert captured["init"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
    }
    assert captured["validate"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
        "initialize": False,
    }
    assert captured["plan"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
        "initialize": False,
    }


def test_terraform_commands_reject_generated_flux_paths(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "terraform",
            "plan",
            str(tmp_path / "generated" / "flux" / "kustomization.yaml"),
        ],
    )

    assert result.exit_code == 1, result.output
    plain = " ".join(_plain_output(result.output).split())
    assert "Terraform target must point to `generated/` or `generated/infra/`" in plain
    assert "not `generated/flux/kustomization.yaml`" in plain


def test_terraform_apply_command_invokes_runtime_auth_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest))

    def _fake_ensure_terraform_backend_ready(config: object, *, auto_auth_bootstrap: bool) -> None:
        captured["backend"] = {
            "config": config,
            "auto_auth_bootstrap": auto_auth_bootstrap,
        }

    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", _fake_ensure_terraform_backend_ready
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})
    monkeypatch.setattr(
        cli,
        "terraform_init",
        lambda infra_dir, *, extra_env=None: captured.setdefault(
            "init", {"infra_dir": infra_dir, "extra_env": extra_env}
        ),
    )
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: captured.setdefault(
            "validate",
            {"infra_dir": infra_dir, "extra_env": extra_env, "initialize": initialize},
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True: captured.setdefault(
            "apply", {"config": config, "paths": paths, "initialize": initialize}
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.setdefault(
            "inventory", {"config": config, "paths": paths}
        ),
    )

    result = runner.invoke(
        cli.app,
        ["terraform", "apply", str(tmp_path / "generated"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == {
        "config": "cfg",
        "auto_auth_bootstrap": True,
    }
    assert captured["init"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
    }
    assert captured["validate"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
        "initialize": False,
    }
    assert captured["apply"] == {
        "config": "cfg",
        "paths": fake_paths,
        "initialize": False,
    }
    assert captured["inventory"] == {
        "config": "cfg",
        "paths": fake_paths,
    }


def test_terraform_destroy_command_invokes_runtime_auth_and_destroy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)

    def _fake_ensure_terraform_backend_ready(config: object, *, auto_auth_bootstrap: bool) -> None:
        captured["backend"] = {
            "config": config,
            "auto_auth_bootstrap": auto_auth_bootstrap,
        }

    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", _fake_ensure_terraform_backend_ready
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda config, paths, *, auto_auth_bootstrap, yes, initialize=True, status_watchers=None: (
            captured.setdefault(
                "destroy",
                {
                    "config": config,
                    "paths": paths,
                    "auto_auth_bootstrap": auto_auth_bootstrap,
                    "yes": yes,
                    "initialize": initialize,
                    "status_watchers": status_watchers,
                },
            )
        ),
    )

    result = runner.invoke(
        cli.app,
        ["terraform", "destroy", str(tmp_path / "generated"), "--auto-auth-bootstrap", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Terraform destroy completed from" in _plain_output(result.output)
    assert captured["backend"] == {
        "config": "cfg",
        "auto_auth_bootstrap": True,
    }
    assert captured["destroy"] == {
        "config": "cfg",
        "paths": fake_paths,
        "auto_auth_bootstrap": True,
        "yes": True,
        "initialize": True,
        "status_watchers": None,
    }


def test_terraform_destroy_command_confirmation_targets_infra_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_confirm_generated_destroy",
        lambda **kwargs: captured.update(kwargs) or False,
    )

    result = runner.invoke(cli.app, ["terraform", "destroy", str(tmp_path / "generated")])

    assert result.exit_code == 0, result.output
    assert "No changes applied." in _plain_output(result.output)
    assert captured["action_label"] == "Terraform destroy"
    assert captured["prompt_text"] == "Continue and destroy the rendered infra resources?"
    assert captured["warning_text"] == (
        f"Terraform destroy will destroy the rendered infra resources under {fake_paths.infra_dir}."
    )


def test_run_terraform_destroy_with_recovery_clears_stale_lock_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[str, object]] = []
    lock_info = SimpleNamespace(lock_id="lock-123", who="rezab@host")

    def _fake_destroy_with_status(
        config: object, paths: object, *, initialize: bool = True, status_watchers=None
    ) -> None:
        calls.append(("destroy", initialize, status_watchers))
        if len([call for call in calls if call[0] == "destroy"]) == 1:
            raise RuntimeError("Terraform never acquired the remote state lock")

    monkeypatch.setattr(cli, "_run_terraform_destroy_with_status", _fake_destroy_with_status)
    monkeypatch.setattr(
        cli,
        "_unlock_terraform_state_lock",
        lambda config, paths, *, auto_auth_bootstrap, force: (
            calls.append(("unlock", auto_auth_bootstrap, force)) or lock_info
        ),
    )
    monkeypatch.setattr(
        cli,
        "_attempt_mk8s_node_group_destroy_recovery",
        lambda *, status_watchers, yes: calls.append(("cleanup", yes, status_watchers)) or False,
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    cli._run_terraform_destroy_with_recovery(
        "cfg",
        fake_paths,
        auto_auth_bootstrap=True,
        yes=True,
        initialize=True,
        status_watchers=None,
    )

    assert calls == [
        ("destroy", True, None),
        ("unlock", True, False),
        ("destroy", True, None),
    ]


def test_run_terraform_destroy_with_recovery_deletes_stuck_mk8s_node_group_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[str, object]] = []

    def _fake_destroy_with_status(
        config: object, paths: object, *, initialize: bool = True, status_watchers=None
    ) -> None:
        calls.append(("destroy", initialize, status_watchers))
        if len([call for call in calls if call[0] == "destroy"]) == 1:
            raise RuntimeError("Terraform destroy timed out")

    monkeypatch.setattr(cli, "_run_terraform_destroy_with_status", _fake_destroy_with_status)
    monkeypatch.setattr(
        cli,
        "_attempt_mk8s_node_group_destroy_recovery",
        lambda *, status_watchers, yes: calls.append(("cleanup", yes, status_watchers)) or True,
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    cli._run_terraform_destroy_with_recovery(
        "cfg",
        fake_paths,
        auto_auth_bootstrap=True,
        yes=True,
        initialize=True,
        status_watchers=[
            {
                "component_id": "mk8s",
                "instance_id": "mk8s",
                "kind": "nebius.mk8s.cluster",
                "parent_id": "project-456",
                "resource_name": "cluster-a",
            }
        ],
    )

    assert calls == [
        (
            "destroy",
            True,
            [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "kind": "nebius.mk8s.cluster",
                    "parent_id": "project-456",
                    "resource_name": "cluster-a",
                }
            ],
        ),
        (
            "cleanup",
            True,
            [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "kind": "nebius.mk8s.cluster",
                    "parent_id": "project-456",
                    "resource_name": "cluster-a",
                }
            ],
        ),
        (
            "destroy",
            True,
            [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "kind": "nebius.mk8s.cluster",
                    "parent_id": "project-456",
                    "resource_name": "cluster-a",
                }
            ],
        ),
    ]


def test_terraform_unlock_command_reports_when_no_lock_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_unlock_terraform_state_lock",
        lambda config, paths, *, auto_auth_bootstrap, force: None,
    )
    monkeypatch.setattr(
        cli,
        "backend_settings_from_config",
        lambda _cfg: SimpleNamespace(bucket="demo-bucket", key="terraform.tfstate"),
    )

    result = runner.invoke(cli.app, ["terraform", "unlock", str(tmp_path / "generated")])

    assert result.exit_code == 0, result.output
    plain = _plain_output(result.output).replace("\n", " ")
    assert "No remote Terraform state lock is present for" in plain
    assert "demo-bucket/terraform.tfstate.tflock." in plain


def test_terraform_unlock_command_reports_cleared_lock_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    lock_info = SimpleNamespace(
        lock_id="lock-123",
        who="rezab@host",
        created="2026-03-19T02:04:39Z",
        bucket="demo-bucket",
        object_key="terraform.tfstate.tflock",
    )

    monkeypatch.setattr(cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_unlock_terraform_state_lock",
        lambda config, paths, *, auto_auth_bootstrap, force: lock_info,
    )

    result = runner.invoke(
        cli.app,
        ["terraform", "unlock", str(tmp_path / "generated"), "--force"],
    )

    assert result.exit_code == 0, result.output
    plain = _plain_output(result.output)
    assert "Terraform state lock cleared:" in plain
    assert "id=lock-123" in plain
    assert "owner=rezab@host" in plain


def test_flux_bootstrap_command_invokes_flux_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1", "deploy": {}}

    monkeypatch.setattr(cli, "_load_generated_flux_context", lambda _path: ("cfg", fake_paths, manifest))

    def _fake_ensure_runtime_auth_material(
        config: object,
        *,
        need_terraform: bool,
        need_eso_mysterybox: bool,
        auto_bootstrap: bool,
    ) -> None:
        captured["auth"] = {
            "config": config,
            "need_terraform": need_terraform,
            "need_eso_mysterybox": need_eso_mysterybox,
            "auto_bootstrap": auto_bootstrap,
        }

    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _fake_ensure_runtime_auth_material)
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.setdefault(
            "inventory", {"config": config, "paths": paths}
        ),
    )
    monkeypatch.setattr(cli, "ensure_flux", lambda _paths, *, extra_env=None: "reconciled")

    result = runner.invoke(
        cli.app,
        ["flux", "bootstrap", str(tmp_path / "generated"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert "Flux reconciled" in _plain_output(result.output)
    assert captured["auth"] == {
        "config": "cfg",
        "need_terraform": False,
        "need_eso_mysterybox": False,
        "auto_bootstrap": True,
    }
    assert captured["inventory"] == {"config": "cfg", "paths": fake_paths}


def test_flux_commands_reject_generated_infra_paths(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "flux",
            "apply",
            str(tmp_path / "generated" / "infra" / "main.tf"),
        ],
    )

    assert result.exit_code == 1, result.output
    plain = " ".join(_plain_output(result.output).split())
    assert "Flux target must point to `generated/` or `generated/flux/`" in plain
    assert "not `generated/infra/main.tf`" in plain


def test_ensure_flux_uses_managed_flux_binary_when_missing_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = SimpleNamespace(
        repo_root=tmp_path,
        flux_dir=tmp_path / "generated" / "flux",
    )
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_namespace_ready", lambda *, extra_env=None: None)
    monkeypatch.setattr(flux_ops, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(
        flux_ops,
        "flux_bootstrap_resources_installed",
        lambda *, extra_env=None: True,
    )
    monkeypatch.setattr(flux_ops, "resolve_flux_binary", lambda: "/tmp/managed-flux")
    monkeypatch.setattr(
        flux_ops,
        "_run",
        lambda cmd, **kwargs: calls.append((tuple(cmd), kwargs)),
    )

    result = flux_ops.ensure_flux(fake_paths)

    assert result == "reconciled"
    assert calls == [
        (
            ("/tmp/managed-flux", "reconcile", "source", "git", "flux-system"),
            {"timeout": 300, "extra_env": None},
        ),
        (
            ("/tmp/managed-flux", "reconcile", "kustomization", "flux-system", "--with-source"),
            {"timeout": 300, "extra_env": None},
        ),
    ]


def test_ensure_flux_bootstrap_falls_back_to_git_origin_repo_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = SimpleNamespace(
        repo_root=tmp_path,
        flux_dir=tmp_path / "generated" / "flux",
    )
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_namespace_ready", lambda *, extra_env=None: None)
    monkeypatch.setattr(flux_ops, "flux_controllers_installed", lambda *, extra_env=None: False)
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: False)
    monkeypatch.setattr(
        flux_ops,
        "flux_bootstrap_resources_installed",
        lambda *, extra_env=None: False,
    )
    monkeypatch.setattr(flux_ops, "resolve_flux_binary", lambda: "/tmp/managed-flux")
    monkeypatch.setattr(flux_ops, "detect_github_repo_slug", lambda _repo_root: "owner/repo")
    monkeypatch.setattr(
        flux_ops,
        "install_flux_controllers",
        lambda *, extra_env=None: calls.append((("install_flux",), {"extra_env": extra_env})),
    )
    monkeypatch.setattr(
        flux_ops,
        "_run",
        lambda cmd, **kwargs: calls.append((tuple(cmd), kwargs)),
    )

    result = flux_ops.ensure_flux(fake_paths)

    assert result == "bootstrapped"
    assert calls[0] == (("install_flux",), {"extra_env": None})
    assert calls[1][0] == (
        "/tmp/managed-flux",
        "bootstrap",
        "github",
        "--owner",
        "owner",
        "--repository",
        "repo",
        "--branch",
        "main",
        "--path",
        "generated/flux",
        "--token-auth",
    )


def test_ensure_flux_bootstraps_when_controllers_exist_but_bootstrap_resources_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = SimpleNamespace(
        repo_root=tmp_path,
        flux_dir=tmp_path / "generated" / "flux",
    )
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_namespace_ready", lambda *, extra_env=None: None)
    monkeypatch.setattr(flux_ops, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(
        flux_ops,
        "flux_bootstrap_resources_installed",
        lambda *, extra_env=None: False,
    )
    monkeypatch.setattr(flux_ops, "resolve_flux_binary", lambda: "/tmp/managed-flux")
    monkeypatch.setattr(
        flux_ops,
        "_run",
        lambda cmd, **kwargs: calls.append((tuple(cmd), kwargs)),
    )

    result = flux_ops.ensure_flux(fake_paths)

    assert result == "bootstrapped"
    assert calls == [
        (
            (
                "/tmp/managed-flux",
                "bootstrap",
                "github",
                "--owner",
                "owner",
                "--repository",
                "repo",
                "--branch",
                "main",
                "--path",
                "generated/flux",
                "--token-auth",
            ),
            {"cwd": fake_paths.repo_root, "timeout": 1800, "extra_env": None},
        ),
        (
            ("/tmp/managed-flux", "reconcile", "kustomization", "flux-system", "--with-source"),
            {"timeout": 300, "extra_env": None},
        ),
    ]


def test_ensure_flux_reinstalls_when_crds_are_missing_then_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = SimpleNamespace(
        repo_root=tmp_path,
        flux_dir=tmp_path / "generated" / "flux",
    )
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_namespace_ready", lambda *, extra_env=None: None)
    monkeypatch.setattr(flux_ops, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: False)
    monkeypatch.setattr(
        flux_ops,
        "flux_bootstrap_resources_installed",
        lambda *, extra_env=None: True,
    )
    monkeypatch.setattr(flux_ops, "resolve_flux_binary", lambda: "/tmp/managed-flux")
    monkeypatch.setattr(
        flux_ops,
        "install_flux_controllers",
        lambda *, extra_env=None: calls.append((("install_flux",), {"extra_env": extra_env})),
    )
    monkeypatch.setattr(
        flux_ops,
        "_run",
        lambda cmd, **kwargs: calls.append((tuple(cmd), kwargs)),
    )

    result = flux_ops.ensure_flux(fake_paths)

    assert result == "reconciled"
    assert calls[0] == (("install_flux",), {"extra_env": None})
    assert calls[1:] == [
        (
            ("/tmp/managed-flux", "reconcile", "source", "git", "flux-system"),
            {"timeout": 300, "extra_env": None},
        ),
        (
            ("/tmp/managed-flux", "reconcile", "kustomization", "flux-system", "--with-source"),
            {"timeout": 300, "extra_env": None},
        ),
    ]


def test_wait_for_flux_namespace_ready_fails_with_targeted_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "metadata": {"deletionTimestamp": "2026-03-20T13:28:57Z"},
        "status": {
            "phase": "Terminating",
            "conditions": [
                {
                    "type": "NamespaceContentRemaining",
                    "status": "True",
                    "message": (
                        "Some resources are remaining: "
                        "helmcharts.source.toolkit.fluxcd.io has 1 resource instances, "
                        "kustomizations.kustomize.toolkit.fluxcd.io has 1 resource instances"
                    ),
                },
                {
                    "type": "NamespaceFinalizersRemaining",
                    "status": "True",
                    "message": (
                        "Some content in the namespace has finalizers remaining: "
                        "finalizers.fluxcd.io in 2 resource instances"
                    ),
                },
            ],
        },
    }
    monotonic_values = iter((0.0, 31.0))

    monkeypatch.setattr(
        flux_ops,
        "_get_namespace_payload",
        lambda namespace, *, extra_env=None: payload,
    )
    monkeypatch.setattr(flux_ops.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(flux_ops.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="stuck terminating") as excinfo:
        flux_ops.wait_for_flux_namespace_ready(timeout_seconds=30)

    message = str(excinfo.value)
    assert re.search(
        r"\bhelmcharts\.source\.toolkit\.fluxcd\.io has 1 resource instances\b",
        message,
    )
    assert re.search(r"\bfinalizers\.fluxcd\.io\b", message)
    assert "kubectl get namespace flux-system -o yaml" in message


def test_install_flux_controllers_waits_for_namespace_before_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(
        flux_ops,
        "wait_for_flux_namespace_ready",
        lambda *, extra_env=None: calls.append(("wait_namespace", extra_env)),
    )
    monkeypatch.setattr(
        flux_ops,
        "wait_for_flux_crds_clear",
        lambda *, extra_env=None: calls.append(("wait_crds_clear", extra_env)),
    )
    monkeypatch.setattr(
        flux_ops,
        "wait_for_flux_crds_ready",
        lambda *, extra_env=None: calls.append(("wait_crds_ready", extra_env)),
    )
    monkeypatch.setattr(
        flux_ops,
        "_run_filtered_kubectl_apply",
        lambda cmd, **kwargs: calls.append(("apply", tuple(cmd))),
    )
    monkeypatch.setattr(
        flux_ops,
        "_run",
        lambda cmd, **kwargs: calls.append(("run", tuple(cmd))),
    )

    manifest_url = flux_ops.install_flux_controllers(extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert manifest_url == "https://github.com/fluxcd/flux2/releases/download/v2.8.0/install.yaml"
    assert calls[0] == ("wait_namespace", {"KUBECONFIG": "/tmp/kubeconfig"})
    assert calls[1] == ("wait_crds_clear", {"KUBECONFIG": "/tmp/kubeconfig"})
    assert calls[2] == (
        "apply",
        ("kubectl", "apply", "-f", manifest_url),
    )
    assert calls[-1] == ("wait_crds_ready", {"KUBECONFIG": "/tmp/kubeconfig"})


def test_prepare_cluster_handoff_kube_env_writes_exec_kubeconfig_and_persists_local_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    captured: dict[str, object] = {}
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="context-entry",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command="/usr/local/bin/nebius-cxcli",
        exec_args=("mk8s-token", "--project-id", "project-456", "--client-name", "client-a"),
    )

    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 1)
    monkeypatch.setattr(
        cli,
        "_enabled_cluster_handoffs",
        lambda _config: [
            {
                "component_id": "mk8s",
                "instance_id": "mk8s",
                "cluster_id_output_name": "mk8s_cluster_id",
                "access": "external",
            }
        ],
    )
    monkeypatch.setattr(
        cli,
        "terraform_output_raw",
        lambda infra_dir, output_name, *, extra_env=None, initialize=True: (
            captured.update(
                {
                    "terraform_output": (
                        infra_dir,
                        output_name,
                        extra_env,
                        initialize,
                    )
                }
            )
            or "cluster-123"
        ),
    )
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda config, *, cluster_id, access: (
            captured.update({"handoff_spec": (config, cluster_id, access)}) or spec
        ),
    )
    monkeypatch.setattr(
        cli,
        "_persist_cluster_handoff_kubeconfig",
        lambda *, spec, set_current_context=True: (
            captured.setdefault("persist", (spec, set_current_context))
            or Path.home() / ".kube" / "config"
        ),
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(fake_config, fake_paths, stack=stack)
        assert env is not None
        kubeconfig_path = Path(env["KUBECONFIG"])
        kubeconfig = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))

    terraform_output = captured["terraform_output"]
    assert terraform_output[0] == fake_paths.infra_dir
    assert terraform_output[1] == "mk8s_cluster_id"
    assert terraform_output[3] is True
    assert terraform_output[2]["TF_VAR_nebius_provider_parent_id"] == "project-456"
    assert terraform_output[2]["TF_VAR_nebius_provider_module_name"]
    assert captured["handoff_spec"] == (fake_config, "cluster-123", "external")
    assert captured["persist"] == (spec, True)
    assert env[flux_ops.CLUSTER_HANDOFF_ACCESS_ENV] == "external"
    assert kubeconfig["clusters"][0]["cluster"]["server"] == "https://mk8s.example.invalid"
    assert kubeconfig["users"][0]["user"]["exec"]["command"] == "/usr/local/bin/nebius-cxcli"
    assert kubeconfig["users"][0]["user"]["exec"]["args"] == [
        "mk8s-token",
        "--project-id",
        "project-456",
        "--client-name",
        "client-a",
    ]
    assert kubeconfig["users"][0]["user"]["exec"]["interactiveMode"] == "Never"
    assert kubeconfig["current-context"] == "context-entry"


def test_prepare_cluster_handoff_kube_env_loads_runtime_auth_cache_when_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    captured: dict[str, object] = {}
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="context-entry",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command="/usr/local/bin/nebius-cxcli",
        exec_args=("mk8s-token",),
    )

    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 1)
    monkeypatch.setattr(
        cli,
        "_enabled_cluster_handoffs",
        lambda _config: [
            {
                "component_id": "mk8s",
                "instance_id": "mk8s",
                "cluster_id_output_name": "mk8s_cluster_id",
                "access": "external",
            }
        ],
    )
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *_args, **_kwargs: "cluster-123")
    monkeypatch.setattr(
        cli,
        "_runtime_auth_env_available",
        lambda: False,
    )
    monkeypatch.setattr(
        cli,
        "_runtime_auth_cache_load",
        lambda *, project_id, client_name: (
            captured.setdefault("cache_load", (project_id, client_name)) or True
        ),
    )
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda config, *, cluster_id, access: (
            captured.update({"handoff_spec": (config, cluster_id, access)}) or spec
        ),
    )
    monkeypatch.setattr(
        cli,
        "_persist_cluster_handoff_kubeconfig",
        lambda *, spec, set_current_context=True: (
            captured.setdefault("persist", (spec, set_current_context))
            or Path.home() / ".kube" / "config"
        ),
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(fake_config, fake_paths, stack=stack)

    assert env is not None
    assert captured["cache_load"] == ("project-456", "client-a")
    assert captured["handoff_spec"] == (fake_config, "cluster-123", "external")
    assert captured["persist"] == (spec, True)
    assert env[flux_ops.CLUSTER_HANDOFF_ACCESS_ENV] == "external"


def test_prepare_cluster_handoff_kube_env_skips_local_persist_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    captured: dict[str, object] = {}
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="context-entry",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command="/usr/local/bin/nebius-cxcli",
        exec_args=("mk8s-token",),
    )

    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 1)
    monkeypatch.setattr(
        cli,
        "_enabled_cluster_handoffs",
        lambda _config: [
            {
                "component_id": "mk8s",
                "instance_id": "mk8s",
                "cluster_id_output_name": "mk8s_cluster_id",
                "access": "external",
            }
        ],
    )
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *_args, **_kwargs: "cluster-123")
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda config, *, cluster_id, access: (
            captured.update({"handoff_spec": (config, cluster_id, access)}) or spec
        ),
    )
    monkeypatch.setattr(
        cli,
        "_persist_cluster_handoff_kubeconfig",
        lambda *, spec, set_current_context=True: (_ for _ in ()).throw(
            AssertionError("should not persist kubeconfig")
        ),
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(
            fake_config,
            fake_paths,
            stack=stack,
            persist_local_kubeconfig=False,
        )

    assert env is not None
    assert captured["handoff_spec"] == (fake_config, "cluster-123", "external")
    assert env[flux_ops.CLUSTER_HANDOFF_ACCESS_ENV] == "external"


def test_enabled_cluster_handoffs_normalizes_boolean_access_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
        outputs=(),
        handoff=Handoff(
            cluster_id_output_name="cluster_id",
            access_kind="input",
            access_source_path="inputs.mk8s_cluster_public_endpoint",
        ),
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {"mk8s_cluster_public_endpoint": False},
                }
            ]
        }
    }

    monkeypatch.setattr(
        cli,
        "component_entries",
        lambda scope: (entry,) if scope == "infra" else (),
    )

    assert cli._enabled_cluster_handoffs(payload) == [
        {
            "component_id": "mk8s",
            "instance_id": "mk8s",
            "cluster_id_output_name": "mk8s_cluster_id",
            "component_output_ref": "mk8s.cluster_id",
            "access": "internal",
        }
    ]


def test_persist_cluster_handoff_kubeconfig_skips_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")

    assert (
        cli._persist_cluster_handoff_kubeconfig(
            spec=cli._Mk8sKubeconfigSpec(
                cluster_entry_name="cluster-entry",
                user_entry_name="user-entry",
                context_name="context-entry",
                server="https://mk8s.example.invalid",
                ca_pem="FAKE-CA",
                exec_command="/usr/local/bin/nebius-cxcli",
                exec_args=("mk8s-token",),
            ),
        )
        is None
    )


def test_persist_cluster_handoff_kubeconfig_merges_exec_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG", "true")
    kubeconfig_path = tmp_path / ".kube" / "config"
    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {"name": "existing-cluster", "cluster": {"server": "https://existing"}}
                ],
                "users": [{"name": "existing-user", "user": {"token": "existing"}}],
                "contexts": [
                    {
                        "name": "existing-context",
                        "context": {"cluster": "existing-cluster", "user": "existing-user"},
                    }
                ],
                "current-context": "existing-context",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="context-entry",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command="/usr/local/bin/nebius-cxcli",
        exec_args=("mk8s-token", "--project-id", "project-456"),
    )

    result = cli._persist_cluster_handoff_kubeconfig(
        spec=spec,
    )

    persisted = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))
    assert result == kubeconfig_path
    assert persisted["current-context"] == "context-entry"
    assert [entry["name"] for entry in persisted["clusters"]] == [
        "existing-cluster",
        "cluster-entry",
    ]
    assert persisted["users"][-1]["user"]["exec"]["command"] == "/usr/local/bin/nebius-cxcli"
    assert persisted["contexts"][-1]["context"]["cluster"] == "cluster-entry"


def test_persist_cluster_handoff_kubeconfig_preserves_existing_current_context_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG", "true")
    kubeconfig_path = tmp_path / ".kube" / "config"
    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {"name": "existing-cluster", "cluster": {"server": "https://existing"}}
                ],
                "users": [{"name": "existing-user", "user": {"token": "existing"}}],
                "contexts": [
                    {
                        "name": "existing-context",
                        "context": {"cluster": "existing-cluster", "user": "existing-user"},
                    }
                ],
                "current-context": "existing-context",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="context-entry",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command="/usr/local/bin/nebius-cxcli",
        exec_args=("mk8s-token", "--project-id", "project-456"),
    )

    result = cli._persist_cluster_handoff_kubeconfig(spec=spec, set_current_context=False)

    persisted = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))
    assert result == kubeconfig_path
    assert persisted["current-context"] == "existing-context"
    assert [entry["name"] for entry in persisted["contexts"]] == [
        "existing-context",
        "context-entry",
    ]


def test_persist_cluster_handoff_kubeconfig_replaces_duplicate_named_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG", "true")
    kubeconfig_path = tmp_path / ".kube" / "config"
    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {"name": "cluster-entry", "cluster": {"server": "https://stale-a"}},
                    {"name": "cluster-entry", "cluster": {"server": "https://stale-b"}},
                ],
                "users": [
                    {"name": "user-entry", "user": {"token": "stale-a"}},
                    {"name": "user-entry", "user": {"token": "stale-b"}},
                ],
                "contexts": [
                    {
                        "name": "context-entry",
                        "context": {"cluster": "cluster-entry", "user": "user-entry"},
                    },
                    {
                        "name": "context-entry",
                        "context": {"cluster": "cluster-entry", "user": "user-entry"},
                    },
                ],
                "current-context": "context-entry",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="context-entry",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command="/usr/local/bin/nebius-cxcli",
        exec_args=("mk8s-token", "--project-id", "project-456"),
    )

    result = cli._persist_cluster_handoff_kubeconfig(spec=spec)

    persisted = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))
    assert result == kubeconfig_path
    assert [entry["name"] for entry in persisted["clusters"]] == ["cluster-entry"]
    assert [entry["name"] for entry in persisted["users"]] == ["user-entry"]
    assert [entry["name"] for entry in persisted["contexts"]] == ["context-entry"]
    assert persisted["clusters"][0]["cluster"]["server"] == "https://mk8s.example.invalid"
    assert persisted["users"][0]["user"]["exec"]["command"] == "/usr/local/bin/nebius-cxcli"
    assert persisted["contexts"][0]["context"]["cluster"] == "cluster-entry"
    assert persisted["current-context"] == "context-entry"


def test_mk8s_token_command_emits_exec_credential_from_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            captured["timeout"] = timeout
            return SimpleNamespace(
                token="token-123",
                expiration=cli.datetime(2026, 1, 2, 3, 4, 5, tzinfo=cli.UTC),
            )

        def sync_close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: False)
    monkeypatch.setattr(
        cli,
        "_runtime_auth_cache_load",
        lambda *, project_id, client_name: (
            captured.setdefault("cache_load", (project_id, client_name)) or True
        ),
    )
    monkeypatch.setattr(
        cli,
        "init_nebius_sdk",
        lambda *, parent_id, endpoint, context: (
            captured.update({"sdk_init": (parent_id, endpoint, context)}) or _FakeSDK()
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "mk8s-token",
            "--project-id",
            "project-456",
            "--client-name",
            "client-a",
            "--endpoint",
            "api.example.invalid",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert captured["cache_load"] == ("project-456", "client-a")
    assert captured["sdk_init"] == ("project-456", "api.example.invalid", "MK8s exec auth")
    assert captured["timeout"] == 20.0
    assert captured["closed"] is True
    assert payload["apiVersion"] == "client.authentication.k8s.io/v1"
    assert payload["kind"] == "ExecCredential"
    assert payload["status"]["token"] == "token-123"
    assert payload["status"]["expirationTimestamp"] == "2026-01-02T03:04:05Z"


def test_wait_for_cluster_nodes_ready_returns_immediately_when_nodes_are_already_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    monkeypatch.setattr(
        cli,
        "_node_readiness_summary",
        lambda *, extra_env: (True, "nodes 2/2 Ready; compute-1:Ready, compute-2:Ready"),
    )

    cli._wait_for_cluster_nodes_ready(
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        emit=messages.append,
    )

    assert messages == [
        "[bold white]Kubernetes[/bold white] [dim][0s][/dim] "
        "nodes 2/2 Ready; compute-1:Ready, compute-2:Ready; "
        "already Ready, continuing with Flux deployment."
    ]


def test_wait_for_cluster_nodes_ready_announces_wait_only_when_nodes_are_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    sleeps: list[float] = []
    states = iter(
        [
            (False, "nodes 0/2 Ready; waiting for node registration"),
            (True, "nodes 2/2 Ready; compute-1:Ready, compute-2:Ready"),
        ]
    )
    monotonic_values = iter([100.0, 110.0, 110.0])

    monkeypatch.setattr(cli, "_node_readiness_summary", lambda *, extra_env: next(states))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli._wait_for_cluster_nodes_ready(
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        emit=messages.append,
    )

    assert messages == [
        "Target Kubernetes nodes are not Ready yet; waiting before Flux deployment.",
        "[bold white]Kubernetes[/bold white] [dim][0s][/dim] nodes 0/2 Ready; waiting for node registration",
        "[bold white]Kubernetes[/bold white] [dim][10s][/dim] nodes 2/2 Ready; compute-1:Ready, compute-2:Ready",
    ]
    assert sleeps == [10.0]


def test_report_cluster_nodes_status_reports_ready_snapshot_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    monkeypatch.setattr(
        cli,
        "_node_readiness_summary",
        lambda *, extra_env: (True, "nodes 2/2 Ready; compute-1:Ready, compute-2:Ready"),
    )

    cli._report_cluster_nodes_status(
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        emit=messages.append,
    )

    assert messages == [
        "[bold white]Kubernetes[/bold white] nodes 2/2 Ready; compute-1:Ready, compute-2:Ready; "
        "proceeding with in-cluster deployment."
    ]


def test_report_cluster_nodes_status_reports_not_ready_snapshot_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    monkeypatch.setattr(
        cli,
        "_node_readiness_summary",
        lambda *, extra_env: (False, "nodes 1/2 Ready; compute-1:Ready, compute-2:NotReady"),
    )

    cli._report_cluster_nodes_status(
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        emit=messages.append,
    )

    assert messages == [
        "[bold white]Kubernetes[/bold white] nodes 1/2 Ready; compute-1:Ready, compute-2:NotReady; "
        "proceeding without waiting for every node because Flux and validation checks report live in-cluster progress."
    ]


def test_flux_bootstrap_command_uses_cluster_handoff_when_config_declares_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {},
        },
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_mk8s_target(fake_paths)]},
    }
    target_paths = _target_paths(fake_paths)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: captured.update(
            {"backend": (config, auto_auth_bootstrap)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            captured.update(
                {
                    "handoff": (
                        config,
                        paths,
                        target,
                        persist_local_kubeconfig,
                        set_current_context,
                    ),
                }
            )
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: captured.update({"cluster_status": extra_env}),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.update({"inventory": (config, paths)}),
    )
    monkeypatch.setattr(
        cli,
        "ensure_flux",
        lambda paths, *, extra_env=None: (
            captured.update({"flux": (paths, extra_env)}) or "reconciled"
        ),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "bootstrap", str(tmp_path / "generated"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == (fake_config, True)
    assert captured["inventory"] == (fake_config, fake_paths)
    assert captured["handoff"] == (
        fake_config,
        fake_paths,
        _mk8s_target(fake_paths),
        True,
        True,
    )
    assert captured["flux"] == (target_paths, {"KUBECONFIG": "/tmp/kubeconfig"})
    assert captured["cluster_status"] == {"KUBECONFIG": "/tmp/kubeconfig"}


def test_flux_apply_command_applies_rendered_flux_with_cluster_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {},
        },
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_mk8s_target(fake_paths)]},
    }
    target_paths = _target_paths(fake_paths)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: captured.update(
            {"backend": (config, auto_auth_bootstrap)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            captured.update(
                {
                    "handoff": (
                        config,
                        paths,
                        target,
                        persist_local_kubeconfig,
                        set_current_context,
                    ),
                }
            )
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: captured.update({"cluster_status": extra_env}),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: captured.update({"apply_flux": (paths, extra_env)}),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None: captured.update(
            {"warn_bootstrap": (config, paths, extra_env, target_ref)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.update({"inventory": (config, paths)}),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "apply", str(tmp_path / "generated"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert "Flux applied from" in _plain_output(result.output)
    assert captured["backend"] == (fake_config, True)
    assert captured["inventory"] == (fake_config, fake_paths)
    assert captured["handoff"] == (
        fake_config,
        fake_paths,
        _mk8s_target(fake_paths),
        True,
        True,
    )
    assert captured["apply_flux"] == (target_paths, {"KUBECONFIG": "/tmp/kubeconfig"})
    assert captured["warn_bootstrap"] == (
        fake_config,
        target_paths,
        {"KUBECONFIG": "/tmp/kubeconfig"},
        "mk8s",
    )
    assert captured["cluster_status"] == {"KUBECONFIG": "/tmp/kubeconfig"}


def test_flux_apply_command_all_targets_persists_contexts_without_switching_current_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {},
        },
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {
            "charts": [
                {"id": "gateway-helm", "enabled": True, "target_ref": "mk8s"},
                {"id": "gateway-helm", "enabled": True, "target_ref": "mk8s-2"},
            ]
        },
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths),
                _mk8s_target(fake_paths, target_ref="mk8s-2"),
            ]
        },
    }
    captured: dict[str, object] = {
        "handoffs": [],
        "apply_flux": [],
        "warn_bootstrap": [],
        "cluster_status": [],
    }

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: captured.update(
            {"backend": (config, auto_auth_bootstrap)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            cast(list[tuple[object, ...]], captured["handoffs"]).append(
                (
                    config,
                    paths,
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": f"/tmp/{target['target_ref']}.kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_report_cluster_nodes_status",
        lambda *, extra_env, emit: cast(list[dict[str, str]], captured["cluster_status"]).append(
            extra_env or {}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: cast(
            list[tuple[ProjectPaths, dict[str, str] | None]], captured["apply_flux"]
        ).append((paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None: cast(
            list[tuple[object, ...]], captured["warn_bootstrap"]
        ).append((config, paths, extra_env, target_ref)),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.update({"inventory": (config, paths)}),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "apply", str(tmp_path / "generated"), "--auto-auth-bootstrap", "--all-targets"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == (fake_config, True)
    assert captured["inventory"] == (fake_config, fake_paths)
    assert captured["handoffs"] == [
        (
            fake_config,
            fake_paths,
            _mk8s_target(fake_paths),
            True,
            False,
        ),
        (
            fake_config,
            fake_paths,
            _mk8s_target(fake_paths, target_ref="mk8s-2"),
            True,
            False,
        ),
    ]
    assert captured["apply_flux"] == [
        (
            _target_paths(fake_paths),
            {"KUBECONFIG": "/tmp/mk8s.kubeconfig"},
        ),
        (
            _target_paths(fake_paths, target_ref="mk8s-2"),
            {"KUBECONFIG": "/tmp/mk8s-2.kubeconfig"},
        ),
    ]
    assert captured["warn_bootstrap"] == [
        (
            fake_config,
            _target_paths(fake_paths),
            {"KUBECONFIG": "/tmp/mk8s.kubeconfig"},
            "mk8s",
        ),
        (
            fake_config,
            _target_paths(fake_paths, target_ref="mk8s-2"),
            {"KUBECONFIG": "/tmp/mk8s-2.kubeconfig"},
            "mk8s-2",
        ),
    ]


def test_flux_destroy_command_deletes_rendered_flux_with_cluster_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {},
        },
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_mk8s_target(fake_paths)]},
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: captured.update(
            {"backend": (config, auto_auth_bootstrap)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda config, paths, loaded_manifest, **kwargs: captured.update(
            {"destroy_flux": (config, paths, loaded_manifest, kwargs)}
        ),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "destroy", str(tmp_path / "generated"), "--auto-auth-bootstrap", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Flux resources deleted from" in _plain_output(result.output)
    assert captured["backend"] == (fake_config, True)
    assert captured["destroy_flux"] == (
        fake_config,
        fake_paths,
        manifest,
        {"requested_target_ref": None, "all_targets": False},
    )


def test_flux_destroy_command_confirmation_targets_flux_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(
        cli,
        "_load_generated_flux_context",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_confirm_generated_destroy",
        lambda **kwargs: captured.update(kwargs) or False,
    )

    result = runner.invoke(cli.app, ["flux", "destroy", str(tmp_path / "generated")])

    assert result.exit_code == 0, result.output
    assert "No changes applied." in _plain_output(result.output)
    assert captured["action_label"] == "Flux destroy"
    assert captured["prompt_text"] == (
        "Continue and delete the rendered app resources from the target cluster?"
    )
    assert captured["warning_text"] == (
        f"Flux destroy will delete the rendered app resources declared under {fake_paths.flux_dir}."
    )


def test_warn_if_flux_gitops_not_bootstrapped_prints_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    messages: list[str] = []

    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 1)
    monkeypatch.setattr(
        cli,
        "flux_bootstrap_resources_installed",
        lambda *, extra_env=None: False,
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: messages.append(str(message)),
    )

    cli._warn_if_flux_gitops_not_bootstrapped(
        {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}},
        fake_paths,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )

    assert len(messages) >= 3
    assert "Flux GitOps bootstrap is not configured" in messages[0]
    assert "Run to enable GitOps sync:" in messages[1]
    assert f"nebius-cxcli flux bootstrap {fake_paths.generated_dir}" == messages[2]


def test_help_text_aligns_render_and_apply_surfaces() -> None:
    top_result = runner.invoke(cli.app, ["--help"])
    quota_check_result = runner.invoke(cli.app, ["quota-check", "--help"])
    quota_request_result = runner.invoke(cli.app, ["quota-request", "--help"])
    render_result = runner.invoke(cli.app, ["render", "--help"])
    deploy_result = runner.invoke(cli.app, ["deploy", "--help"])
    destroy_result = runner.invoke(cli.app, ["destroy", "--help"])
    tf_apply_result = runner.invoke(cli.app, ["terraform", "apply", "--help"])
    tf_destroy_result = runner.invoke(cli.app, ["terraform", "destroy", "--help"])
    flux_apply_result = runner.invoke(cli.app, ["flux", "apply", "--help"])
    flux_destroy_result = runner.invoke(cli.app, ["flux", "destroy", "--help"])
    flux_bootstrap_result = runner.invoke(cli.app, ["flux", "bootstrap", "--help"])

    assert top_result.exit_code == 0, top_result.output
    assert quota_check_result.exit_code == 0, quota_check_result.output
    assert quota_request_result.exit_code == 0, quota_request_result.output
    assert render_result.exit_code == 0, render_result.output
    assert deploy_result.exit_code == 0, deploy_result.output
    assert destroy_result.exit_code == 0, destroy_result.output
    assert tf_apply_result.exit_code == 0, tf_apply_result.output
    assert tf_destroy_result.exit_code == 0, tf_destroy_result.output
    assert flux_apply_result.exit_code == 0, flux_apply_result.output
    assert flux_destroy_result.exit_code == 0, flux_destroy_result.output
    assert flux_bootstrap_result.exit_code == 0, flux_bootstrap_result.output

    render_help = " ".join(_plain_output(render_result.output).split()).lower()
    deploy_help = " ".join(_plain_output(deploy_result.output).split()).lower()
    destroy_help = " ".join(_plain_output(destroy_result.output).split()).lower()
    tf_apply_help = " ".join(_plain_output(tf_apply_result.output).split()).lower()
    tf_destroy_help = " ".join(_plain_output(tf_destroy_result.output).split()).lower()
    flux_apply_help = " ".join(_plain_output(flux_apply_result.output).split()).lower()
    flux_destroy_help = " ".join(_plain_output(flux_destroy_result.output).split()).lower()
    flux_bootstrap_help = " ".join(_plain_output(flux_bootstrap_result.output).split()).lower()
    quota_check_help = " ".join(_plain_output(quota_check_result.output).split()).lower()
    quota_request_help = " ".join(_plain_output(quota_request_result.output).split()).lower()

    assert "live nebius quota/capacity assessment" in quota_check_help
    assert "quota allowances to confirm the shortage" in quota_request_help
    assert "separate request surface" in quota_request_help
    assert "confirmed live quota shortages" in quota_request_help
    assert "--all-regions" in quota_check_help
    assert "selected config region still" in quota_check_help
    assert "quota-only" in quota_check_help
    assert "prompting before overwrite unless --force is provided" in render_help
    assert "generated artifact bundle" in deploy_help
    assert "does not run `flux bootstrap`" in deploy_help
    assert "does not create or update github workflows" in deploy_help
    assert "destroy all rendered project resources" in destroy_help
    assert "destructive inverse of `deploy`" in destroy_help
    assert "whole rendered project" in destroy_help
    assert "--yes" in destroy_help
    assert "refresh the deploy report" in deploy_help
    assert "refresh the deploy report" in tf_apply_help
    assert "terraform destroy" in tf_destroy_help
    assert "--yes" in tf_destroy_help
    assert "refresh the deploy report" in flux_apply_help
    assert "delete rendered flux resources" in flux_destroy_help
    assert "--yes" in flux_destroy_help
    assert "refresh the deploy report" in flux_bootstrap_help


def test_help_text_maps_commands_to_target_types() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    output = " ".join(plain_output.split())

    assert "Target guide:" in plain_output
    assert (
        "create bootstraps one name-based tenant/project folder from a deployments root directory"
        in output
    )
    assert "overwrites existing resolved project folders only with confirmation" in output
    assert "component list/add/remove are the day-2 config.yaml editing surface" in output
    assert "discover uses a deployment-scope directory" in output
    assert "validate/quota-check/quota-request/render/bootstrap-ci/deploy use config.yaml" in output
    assert (
        "destroy uses config.yaml to tear down all rendered project resources from sibling generated/"
        in output
    )
    assert "email also uses config.yaml and resolves sibling generated/ automatically" in output
    assert "validate-generated uses generated/" in output
    assert "terraform uses generated/infra" in output
    assert "flux uses generated/flux" in output
    assert "validate-sources accepts optional component_sources.yaml" in output
    assert "auth has no positional path" in output
    assert "report Use CONFIG_YAML" not in output
    assert "bootstrap-ci Use CONFIG_YAML" in output
    assert "component" in output
    assert "validate Use CONFIG_YAML" in output
    assert "source config" in output
    assert "deployment" in output
    assert "live quota/capacity" in output
    assert "readiness" in output
    assert "quota-check Use CONFIG_YAML" in output
    assert "quota/capacity" in output
    assert "assessment for enabled infra components." in output
    assert "quota-request Use CONFIG_YAML" in output
    assert "deploy Use CONFIG_YAML" in output
    assert "destroy Use CONFIG_YAML" in output


def test_command_help_usage_labels_positional_target_types() -> None:
    create_result = runner.invoke(cli.app, ["create", "--help"])
    component_result = runner.invoke(cli.app, ["component", "--help"])
    component_add_result = runner.invoke(cli.app, ["component", "add", "--help"])
    component_remove_result = runner.invoke(cli.app, ["component", "remove", "--help"])
    discover_result = runner.invoke(cli.app, ["discover", "--help"])
    validate_result = runner.invoke(cli.app, ["validate", "--help"])
    validate_sources_result = runner.invoke(cli.app, ["validate-sources", "--help"])
    validate_generated_result = runner.invoke(cli.app, ["validate-generated", "--help"])
    quota_request_result = runner.invoke(cli.app, ["quota-request", "--help"])
    deploy_result = runner.invoke(cli.app, ["deploy", "--help"])
    destroy_result = runner.invoke(cli.app, ["destroy", "--help"])
    tf_destroy_result = runner.invoke(cli.app, ["terraform", "destroy", "--help"])
    flux_destroy_result = runner.invoke(cli.app, ["flux", "destroy", "--help"])
    email_result = runner.invoke(cli.app, ["email", "--help"])

    assert create_result.exit_code == 0, create_result.output
    assert component_result.exit_code == 0, component_result.output
    assert component_add_result.exit_code == 0, component_add_result.output
    assert component_remove_result.exit_code == 0, component_remove_result.output
    assert discover_result.exit_code == 0, discover_result.output
    assert validate_result.exit_code == 0, validate_result.output
    assert validate_sources_result.exit_code == 0, validate_sources_result.output
    assert validate_generated_result.exit_code == 0, validate_generated_result.output
    assert quota_request_result.exit_code == 0, quota_request_result.output
    assert deploy_result.exit_code == 0, deploy_result.output
    assert destroy_result.exit_code == 0, destroy_result.output
    assert tf_destroy_result.exit_code == 0, tf_destroy_result.output
    assert flux_destroy_result.exit_code == 0, flux_destroy_result.output
    assert email_result.exit_code == 0, email_result.output

    create_help = _plain_output(create_result.output)
    component_help = _plain_output(component_result.output)
    component_add_help = _plain_output(component_add_result.output)
    component_remove_help = _plain_output(component_remove_result.output)
    discover_help = _plain_output(discover_result.output)
    validate_help = _plain_output(validate_result.output)
    validate_sources_help = _plain_output(validate_sources_result.output)
    validate_generated_help = _plain_output(validate_generated_result.output)
    quota_request_help = _plain_output(quota_request_result.output)
    deploy_help = _plain_output(deploy_result.output)
    destroy_help = _plain_output(destroy_result.output)
    tf_destroy_help = _plain_output(tf_destroy_result.output)
    flux_destroy_help = _plain_output(flux_destroy_result.output)
    email_help = _plain_output(email_result.output)
    normalized_email_help = " ".join(email_help.split())
    normalized_component_add_help = " ".join(component_add_help.split())
    normalized_component_remove_help = " ".join(component_remove_help.split())

    assert "create [OPTIONS] DEPLOYMENTS_ROOT" in create_help
    assert "--validate-config --no-validate-config" in " ".join(create_help.split())
    normalized_component_help = " ".join(component_help.split())
    assert "source-driven" in normalized_component_help
    assert "component instances" in normalized_component_help
    assert "Use this after create for day-2 add/remove/list changes." in normalized_component_help
    normalized_create_help = " ".join(create_help.split())
    assert (
        "bootstrap one name-based tenant/project folder with config.yaml plus generated/ skeleton"
        in normalized_create_help
    )
    assert "overwrite an existing resolved project folder from scratch" in normalized_create_help
    assert "skips that validation" in normalized_create_help
    assert "only; create still runs" in normalized_create_help
    assert "warning-only live" in normalized_create_help
    assert "quota/capacity" in normalized_create_help
    assert "assessment" in normalized_create_help
    assert "not a reservation" in normalized_create_help
    assert "not a wizard-selectable deployment gate" in normalized_create_help
    assert "add [OPTIONS] CONFIG_YAML [COMPONENT_SELECTOR]..." in component_add_help
    assert "Omit" in normalized_component_add_help
    assert "prompt" in normalized_component_add_help
    assert "interactively" in normalized_component_add_help
    assert "infra-only" in normalized_component_add_help
    assert "interactive adds" in normalized_component_add_help
    assert "valid" in normalized_component_add_help
    assert "Repeat reusable" in normalized_component_add_help
    assert "another instance" in normalized_component_add_help
    assert "<id>@<instance-id>" in normalized_component_add_help
    assert "infra:<id>" in normalized_component_add_help
    assert "apps:<id>" in normalized_component_add_help
    assert "all" in normalized_component_add_help
    assert "none" in normalized_component_add_help
    assert "apps.charts[].target_ref" in normalized_component_add_help
    assert "--validate-sources --no-validate-sources" in " ".join(component_add_help.split())
    assert "day-2 additive" in normalized_component_add_help
    assert "remove [OPTIONS] CONFIG_YAML [COMPONENT_SELECTOR]..." in component_remove_help
    assert "<id>@<instance-id>" in normalized_component_remove_help
    assert "infra:<id>" in normalized_component_remove_help
    assert "apps:<id>" in normalized_component_remove_help
    assert "<instance-id>" in normalized_component_remove_help
    assert "Omit" in normalized_component_remove_help
    assert "prompt" in normalized_component_remove_help
    assert "interactively" in normalized_component_remove_help
    assert "config.yaml row" in normalized_component_remove_help
    assert "day-2 infra/app component removal" in normalized_component_remove_help
    assert "discover [OPTIONS] DEPLOYMENT_SCOPE" in discover_help
    assert "generated/" in discover_help
    assert "narrower directory under it" in discover_help
    assert "validate [OPTIONS] CONFIG_YAML" in validate_help
    normalized_validate_help = " ".join(validate_help.split()).lower()
    normalized_validate_generated_help = " ".join(validate_generated_help.split()).lower()
    assert "--strict" not in validate_help
    assert (
        "source config, deployment readiness, and live quota/capacity"
        in normalized_validate_help
    )
    normalized_validate_sources_help = " ".join(validate_sources_help.split()).lower()
    assert "validate-sources [OPTIONS] [COMPONENT_SOURCES_YAML]" in validate_sources_help
    assert "active component_sources.yaml catalog" in normalized_validate_sources_help
    assert "global flags" in normalized_validate_sources_help
    assert "environment" in normalized_validate_sources_help
    assert "bundled defaults" in normalized_validate_sources_help
    assert "validate-generated [OPTIONS] GENERATED_PATH" in validate_generated_help
    assert "generated-bundle readiness" in normalized_validate_generated_help
    assert "portability" in normalized_validate_generated_help
    assert "--auto-auth-bootstrap" in validate_generated_help
    assert "--no-auto-auth" in validate_generated_help
    assert "backend/terraform" in normalized_validate_generated_help
    assert "validation when env" in normalized_validate_generated_help
    normalized_deploy_help = " ".join(deploy_help.split()).lower()
    assert "--skip-validations" in deploy_help
    assert "--skip-validation" in deploy_help
    assert "one-run override" in normalized_deploy_help
    assert "quota-request [OPTIONS] CONFIG_YAML" in quota_request_help
    normalized_quota_request_help = " ".join(quota_request_help.split()).lower()
    assert "already sufficient" in normalized_quota_request_help
    assert "quota-request is a no-op" in normalized_quota_request_help
    assert "remediation command" in normalized_quota_request_help
    assert "deploy [OPTIONS] CONFIG_YAML" in deploy_help
    assert "sibling generated/" in deploy_help
    assert "destroy [OPTIONS] CONFIG_YAML" in destroy_help
    assert "--yes" in " ".join(destroy_help.split())
    assert "destroy [OPTIONS] GENERATED_PATH" in tf_destroy_help
    assert "--yes" in " ".join(tf_destroy_help.split())
    assert "destroy [OPTIONS] GENERATED_PATH" in flux_destroy_help
    assert "--yes" in " ".join(flux_destroy_help.split())
    assert "email [OPTIONS] [CONFIG_YAML]" in email_help
    assert "Omit the path" in normalized_email_help
    assert "only when" in normalized_email_help
    assert "using --setup." in normalized_email_help


def test_bootstrap_ci_help_reflects_reconcile_first_contract() -> None:
    result = runner.invoke(cli.app, ["bootstrap-ci", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    output = " ".join(plain_output.split()).lower()

    assert "--force" not in output
    assert "--auth-bootstrap" in output
    assert "--no-auth-bootstrap" in output
    assert "--cli-ref" in output
    assert "CLI-managed customer GitHub workflow" in plain_output
    assert "[default: auth-bootstrap]" in plain_output
    assert "[default: GH_TOKEN]" in plain_output


def test_auth_help_reflects_sdk_auth_contract() -> None:
    result = runner.invoke(cli.app, ["auth", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    normalized_output = " ".join(plain_output.split())

    assert "Nebius SDK config profile name" in plain_output
    assert "Optional path to Nebius SDK config file" in plain_output
    assert "across all cached profiles for validate-only runs" in normalized_output
    assert "do not pass both" in plain_output
    assert "Valid only" in normalized_output
    assert "with --project-id; required" in normalized_output
    assert "Nebius CLI profile name used by Nebius SDK" not in plain_output
    assert "Nebius SDK/CLI config file" not in plain_output


def test_flux_apply_command_fails_when_no_enabled_charts_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = {"apps": {"charts": []}}

    monkeypatch.setattr(
        cli,
        "_load_generated_flux_context",
        lambda _path: (
            fake_config,
            fake_paths,
            {"schema": "nebius-cxcli-generated/v1", "deploy": {}},
        ),
    )

    result = runner.invoke(cli.app, ["flux", "apply", str(tmp_path / "generated")])

    assert result.exit_code == 1, result.output
    assert "No enabled apps charts are configured for this project." in _plain_output(result.output)


def test_report_command_is_not_registered() -> None:
    result = runner.invoke(cli.app, ["report", "config.yaml"])

    assert result.exit_code != 0
    assert "No such command" in _plain_output(result.output)


def test_email_command_handles_sent_and_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_load_email_context",
        lambda _path: ("cfg", fake_paths, {"schema": "nebius-cxcli-generated/v1"}),
    )
    monkeypatch.setattr(
        cli,
        "load_email_settings",
        lambda *, explicit=None: EmailSettings(
            host="smtp.example.com",
            port=2525,
            starttls=True,
            from_addr="deployments@example.com",
        ),
    )

    monkeypatch.setattr(
        cli,
        "send_deploy_report_email",
        lambda _cfg, _paths, *, smtp_settings=None: (
            captured.update({"smtp_settings": smtp_settings})
            or cli.DeployReportEmailResult(
                sent=True, reason="sent", message="Deploy report email sent"
            )
        ),
    )
    sent_result = runner.invoke(cli.app, ["email", str(fake_paths.config_path)])
    assert sent_result.exit_code == 0, sent_result.output
    assert "Deploy report email sent" in _plain_output(sent_result.output)
    assert captured["smtp_settings"] == {
        "host": "smtp.example.com",
        "port": 2525,
        "starttls": True,
        "from": "deployments@example.com",
    }

    monkeypatch.setattr(
        cli,
        "send_deploy_report_email",
        lambda _cfg, _paths, *, smtp_settings=None: cli.DeployReportEmailResult(
            sent=False,
            reason="disabled",
            message="Deploy report email disabled (`client_info.notifications.email_enabled=false`); nothing sent.",
        ),
    )
    noop_result = runner.invoke(cli.app, ["email", str(fake_paths.config_path)])
    assert noop_result.exit_code == 0, noop_result.output
    assert "Deploy report email disabled" in _plain_output(noop_result.output)

    monkeypatch.setattr(
        cli,
        "send_deploy_report_email",
        lambda _cfg, _paths, *, smtp_settings=None: cli.DeployReportEmailResult(
            sent=False,
            reason="smtp_unconfigured",
            message="Deploy report email enabled but SMTP is not configured. nothing sent.",
        ),
    )
    warning_result = runner.invoke(cli.app, ["email", str(fake_paths.config_path)])
    assert warning_result.exit_code == 0, warning_result.output
    assert "WARNING:" in _plain_output(warning_result.output)
    assert "SMTP is not configured" in _plain_output(warning_result.output)


def test_email_command_rejects_generated_target_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "_load_email_context",
        lambda _path: (_ for _ in ()).throw(
            ValueError(
                "Email target must be project config.yaml, not generated/. "
                "Pass <tenant-folder>/<project-folder>/config.yaml; email resolves sibling generated/ automatically."
            )
        ),
    )

    result = runner.invoke(cli.app, ["email", str(tmp_path / "generated")])

    assert result.exit_code != 0
    assert "Email target must be project config.yaml, not generated/." in _plain_output(
        result.output
    )


def test_email_command_setup_without_generated_path_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_interactive_email_settings_setup",
        lambda *, config_path=None: (
            EmailSettings(host="smtp.example.com", port=587),
            tmp_path / "email.yaml",
        ),
    )

    result = runner.invoke(cli.app, ["email", "--setup"])

    assert result.exit_code == 0, result.output
    assert "Configured local email settings:" in _plain_output(result.output)


def test_email_command_requires_config_path_without_setup() -> None:
    result = runner.invoke(cli.app, ["email"])

    assert result.exit_code == 1, result.output
    assert "config_path is required unless --setup is used." in _plain_output(result.output)


def test_email_help_describes_local_setup_flags() -> None:
    result = runner.invoke(cli.app, ["email", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    assert "--setup" in plain_output
    assert "~/.config/nebius-cxcli/email.yaml" in plain_output


def test_top_level_help_has_single_auth_command_surface() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Manage runtime auth profile" in output
    assert "auth-runtime-profile" not in output
    assert re.search(r"\bauth\b", output) is not None


def test_render_command_fails_before_render_when_active_source_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", _fake_paths(tmp_path)))
    monkeypatch.setattr(
        cli,
        "_validate_active_component_sources",
        lambda _cfg, *, chart_meta_cache=None: (_ for _ in ()).throw(RuntimeError("broken source")),
    )
    monkeypatch.setattr(
        cli,
        "render_terraform_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("render should not run")),
    )

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")])

    assert result.exit_code == 1, result.output
    assert "broken source" in _plain_output(result.output)


def test_validate_active_component_sources_uses_active_catalog_not_config_source_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_dir = tmp_path / "mk8s-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text("terraform {}\n", encoding="utf-8")

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": str(module_dir),
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }

    captured: list[str] = []
    monkeypatch.setattr(
        cli,
        "module_source_validation_issues",
        lambda source: captured.append(source) or (),
    )
    monkeypatch.setattr(
        cli,
        "module_output_names",
        lambda _source: ("cluster_id", "cluster_ca_certificate", "instance_id"),
    )
    monkeypatch.setattr(
        cli,
        "_validate_enabled_chart_sources",
        lambda _config, *, chart_meta_cache=None: [],
    )

    cli._validate_active_component_sources(payload)
    expected_source = next(
        entry.source for entry in cli.component_entries("infra") if entry.id == "mk8s"
    )
    assert captured == [expected_source]


def test_top_level_help_describes_global_component_sources_override() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "--component-sources-file" in output
    assert "Global optional override" in output
    assert "component sources" in output
    assert "component_sources.yaml" in output
    assert "cwd -> env ->" in output
    assert "--source-profile" in output
    assert "Defaults" in output
    assert "to portable." in output
    assert "source.portable" in output
    assert "source.local" in output


def test_auth_help_has_no_subcommand_layer() -> None:
    result = runner.invoke(cli.app, ["auth", "--help"])
    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Usage: " in output
    assert "auth [OPTIONS]" in output
    assert "COMMAND [ARGS]" not in output
    assert "--validate-profile" in output
    assert "--create" in output
    assert "--recreate" in output
    assert "--bootstrap-ci" in output


def test_bootstrap_ci_command_with_auth_passes_github_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    fake_workflow = SimpleNamespace(
        repo_root=tmp_path,
        workflow_file=tmp_path / ".github" / "workflows" / "nebius-deployments.yml",
        wrote_workflow=True,
        replaced_workflow=False,
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: (fake_config, fake_paths))
    monkeypatch.setattr(cli, "_require_git_root", lambda _path: tmp_path)
    monkeypatch.setattr(
        cli,
        "_resolve_bootstrap_ci_github_target",
        lambda *, github_repo, github_token_env, repo_root: (
            github_repo or "owner/repo",
            "token-123",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_ci_workflow_for_deployments_root",
        lambda *, deployments_root, cli_ref: captured.update({"cli_ref": cli_ref}) or fake_workflow,
    )

    def _fake_auto_bootstrap(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli, "_auto_bootstrap_ci_auth_and_secrets", _fake_auto_bootstrap)
    monkeypatch.setattr(
        cli,
        "_sync_github_email_settings",
        lambda *, repo_slug, github_environment, github_token, settings: cli.GitHubEmailSyncResult(
            updated_vars=[],
            updated_secrets=[],
            removed_vars=[],
            removed_secrets=[],
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "bootstrap-ci",
            str(tmp_path / "config.yaml"),
            "--auth-bootstrap",
            "--github-repo",
            "owner/repo",
            "--github-token-env",
            "MY_GH_TOKEN",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "CI bootstrap completed." in _plain_output(result.output)
    assert captured["project_id"] == "project-123"
    assert captured["github_environment"] == "client-a-project-123"
    assert captured["github_repo"] == "owner/repo"
    assert captured["github_token_env"] == "MY_GH_TOKEN"
    assert captured["cli_ref"] == cli.default_cli_ref()


def test_auth_requires_action_flag() -> None:
    result = runner.invoke(cli.app, ["auth", "--project-id", "project-123"])
    assert result.exit_code == 1
    assert "Select at least one action" in _plain_output(result.output)


def test_auth_rejects_mixed_project_config_and_project_id() -> None:
    result = runner.invoke(
        cli.app,
        [
            "auth",
            "--project-config",
            "tenant/project/config.yaml",
            "--project-id",
            "project-123",
            "--validate-profile",
        ],
    )

    assert result.exit_code == 1
    assert "--project-config and --project-id are mutually exclusive" in _plain_output(
        result.output
    )


def test_auth_rejects_client_name_with_project_config() -> None:
    result = runner.invoke(
        cli.app,
        [
            "auth",
            "--project-config",
            "tenant/project/config.yaml",
            "--client-name",
            "client-a",
            "--validate-profile",
        ],
    )

    assert result.exit_code == 1
    assert "--client-name is valid only with --project-id" in _plain_output(result.output)


def test_auth_rejects_client_name_without_target() -> None:
    result = runner.invoke(
        cli.app,
        [
            "auth",
            "--client-name",
            "client-a",
            "--validate-profile",
        ],
    )

    assert result.exit_code == 1
    assert "--client-name requires --project-id" in _plain_output(result.output)


def test_auth_create_warns_if_profile_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=Path("/tmp/nebius-cxcli/client-a-project-123/auth-private.pem"),
        private_key_pem="KEY-DATA",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **_kwargs: (material, False),
    )

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a", "--create"],
    )

    assert result.exit_code == 0, result.output
    assert "Runtime auth profile already exists" in _plain_output(result.output)


def test_auth_recreate_forces_profile_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=Path("/tmp/nebius-cxcli/client-a-project-123/auth-private.pem"),
        private_key_pem="KEY-DATA",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **_kwargs: (material, True),
    )

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a", "--recreate"],
    )

    assert result.exit_code == 0, result.output
    assert "Recreated runtime auth profile for project 'project-123'" in _plain_output(
        result.output
    )


def test_ensure_runtime_auth_material_recreates_stale_cached_public_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _RUNTIME_AUTH_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    try:
        fake_config = SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        )
        stale_private_key = tmp_path / "auth-private-stale.pem"
        stale_private_key.write_text("STALE-KEY", encoding="utf-8")
        refreshed_private_key = tmp_path / "auth-private-fresh.pem"
        refreshed_private_key.write_text("FRESH-KEY", encoding="utf-8")
        stale_status = cli.RuntimeAuthProfileStatus(
            project_id="project-123",
            client_name="client-a",
            cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
            metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
            metadata_exists=True,
            service_account_id="sa-stale",
            auth_public_key_id="auth-key-stale",
            private_key_file=stale_private_key,
            private_key_exists=True,
            cloud_public_key_exists=False,
            cloud_check_error=None,
            issues=(
                "auth_public_key_id 'auth-key-stale' does not exist (or is not accessible) in Nebius",
            ),
        )
        refreshed_material = cli.RuntimeAuthCacheMaterial(
            project_id="project-123",
            client_name="client-a",
            service_account_id="sa-fresh",
            auth_public_key_id="auth-key-fresh",
            private_key_file=refreshed_private_key,
            private_key_pem="FRESH-KEY-DATA",
            s3_access_key_id="fresh-access",
            s3_secret_access_key="fresh-secret",
        )

        def _fake_cache_load(**_kwargs: object) -> bool:
            os.environ["NEBIUS_SA_ID"] = "sa-stale"
            os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = "auth-key-stale"
            os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(stale_private_key)
            os.environ["AWS_ACCESS_KEY_ID"] = "stale-access"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "stale-secret"
            return True

        recreate_calls: list[bool] = []
        rendered_messages: list[str] = []

        monkeypatch.setattr(cli, "_runtime_auth_cache_load", _fake_cache_load)
        monkeypatch.setattr(cli, "_runtime_auth_profile_status", lambda **_kwargs: stale_status)
        monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
        monkeypatch.setattr(
            cli,
            "_create_or_recreate_runtime_auth_profile",
            lambda **kwargs: (
                recreate_calls.append(bool(kwargs["recreate"])) or (refreshed_material, True)
            ),
        )
        monkeypatch.setattr(
            cli.console,
            "print",
            lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
        )

        cli._ensure_runtime_auth_material(
            fake_config,
            need_terraform=True,
            need_eso_mysterybox=False,
            auto_bootstrap=True,
        )

        assert recreate_calls == [True]
        assert os.environ["NEBIUS_SA_ID"] == "sa-fresh"
        assert os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] == "auth-key-fresh"
        assert os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] == str(
            refreshed_material.private_key_file
        )
        assert os.environ["NEBIUS_AUTH_PRIVATE_KEY_PEM"] == "FRESH-KEY-DATA"
        assert os.environ["AWS_ACCESS_KEY_ID"] == "fresh-access"
        assert os.environ["AWS_SECRET_ACCESS_KEY"] == "fresh-secret"
        assert any(
            "Cached runtime auth profile is stale; recreating because" in message
            for message in rendered_messages
        )
    finally:
        _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_fails_fast_for_stale_cached_public_key_without_auto_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _RUNTIME_AUTH_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    try:
        fake_config = SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        )
        stale_private_key = tmp_path / "auth-private-stale.pem"
        stale_private_key.write_text("STALE-KEY", encoding="utf-8")
        stale_status = cli.RuntimeAuthProfileStatus(
            project_id="project-123",
            client_name="client-a",
            cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
            metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
            metadata_exists=True,
            service_account_id="sa-stale",
            auth_public_key_id="auth-key-stale",
            private_key_file=stale_private_key,
            private_key_exists=True,
            cloud_public_key_exists=False,
            cloud_check_error=None,
            issues=(
                "auth_public_key_id 'auth-key-stale' does not exist (or is not accessible) in Nebius",
            ),
        )

        def _fake_cache_load(**_kwargs: object) -> bool:
            os.environ["NEBIUS_SA_ID"] = "sa-stale"
            os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = "auth-key-stale"
            os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(stale_private_key)
            os.environ["AWS_ACCESS_KEY_ID"] = "stale-access"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "stale-secret"
            return True

        monkeypatch.setattr(cli, "_runtime_auth_cache_load", _fake_cache_load)
        monkeypatch.setattr(cli, "_runtime_auth_profile_status", lambda **_kwargs: stale_status)
        monkeypatch.setattr(
            cli,
            "_create_or_recreate_runtime_auth_profile",
            lambda **_kwargs: pytest.fail(
                "stale cached profile should fail before recreate when auto bootstrap is disabled"
            ),
        )

        with pytest.raises(RuntimeError) as exc_info:
            cli._ensure_runtime_auth_material(
                fake_config,
                need_terraform=True,
                need_eso_mysterybox=False,
                auto_bootstrap=False,
            )

        message = str(exc_info.value)
        assert "Cached runtime auth profile is stale" in message
        assert "--recreate" in message
        assert "--auto-auth-bootstrap" in message
    finally:
        _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_does_not_recreate_on_cloud_verification_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _RUNTIME_AUTH_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    try:
        fake_config = SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        )
        stale_private_key = tmp_path / "auth-private-stale.pem"
        stale_private_key.write_text("STALE-KEY", encoding="utf-8")
        verification_error_status = cli.RuntimeAuthProfileStatus(
            project_id="project-123",
            client_name="client-a",
            cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
            metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
            metadata_exists=True,
            service_account_id="sa-stale",
            auth_public_key_id="auth-key-stale",
            private_key_file=stale_private_key,
            private_key_exists=True,
            cloud_public_key_exists=None,
            cloud_check_error="temporary sdk failure",
            issues=("failed Nebius auth public key verification: temporary sdk failure",),
        )

        def _fake_cache_load(**_kwargs: object) -> bool:
            os.environ["NEBIUS_SA_ID"] = "sa-stale"
            os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = "auth-key-stale"
            os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(stale_private_key)
            os.environ["AWS_ACCESS_KEY_ID"] = "stale-access"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "stale-secret"
            return True

        monkeypatch.setattr(cli, "_runtime_auth_cache_load", _fake_cache_load)
        monkeypatch.setattr(
            cli, "_runtime_auth_profile_status", lambda **_kwargs: verification_error_status
        )
        monkeypatch.setattr(
            cli,
            "_create_or_recreate_runtime_auth_profile",
            lambda **_kwargs: pytest.fail(
                "cloud verification errors should not auto-recreate runtime auth material"
            ),
        )

        cli._ensure_runtime_auth_material(
            fake_config,
            need_terraform=True,
            need_eso_mysterybox=False,
            auto_bootstrap=True,
        )
    finally:
        _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_recreates_on_deleted_key_cloud_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _RUNTIME_AUTH_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    try:
        fake_config = SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        )
        stale_private_key = tmp_path / "auth-private-stale.pem"
        stale_private_key.write_text("STALE-KEY", encoding="utf-8")
        refreshed_private_key = tmp_path / "auth-private-fresh.pem"
        refreshed_private_key.write_text("FRESH-KEY", encoding="utf-8")
        deleted_key_status = cli.RuntimeAuthProfileStatus(
            project_id="project-123",
            client_name="client-a",
            cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
            metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
            metadata_exists=True,
            service_account_id="sa-stale",
            auth_public_key_id="auth-key-stale",
            private_key_file=stale_private_key,
            private_key_exists=True,
            cloud_public_key_exists=None,
            cloud_check_error=(
                "Request error INVALID_ARGUMENT: Public Key not exists, expired or deactivated: "
                "'auth-key-stale'; Caused by error: JwtKeyNotExists"
            ),
            issues=("failed Nebius auth public key verification",),
        )
        refreshed_material = cli.RuntimeAuthCacheMaterial(
            project_id="project-123",
            client_name="client-a",
            service_account_id="sa-fresh",
            auth_public_key_id="auth-key-fresh",
            private_key_file=refreshed_private_key,
            private_key_pem="FRESH-KEY-DATA",
            s3_access_key_id="fresh-access",
            s3_secret_access_key="fresh-secret",
        )

        def _fake_cache_load(**_kwargs: object) -> bool:
            os.environ["NEBIUS_SA_ID"] = "sa-stale"
            os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = "auth-key-stale"
            os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(stale_private_key)
            os.environ["AWS_ACCESS_KEY_ID"] = "stale-access"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "stale-secret"
            return True

        recreate_calls: list[bool] = []

        monkeypatch.setattr(cli, "_runtime_auth_cache_load", _fake_cache_load)
        monkeypatch.setattr(
            cli, "_runtime_auth_profile_status", lambda **_kwargs: deleted_key_status
        )
        monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
        monkeypatch.setattr(
            cli,
            "_create_or_recreate_runtime_auth_profile",
            lambda **kwargs: (
                recreate_calls.append(bool(kwargs["recreate"])) or (refreshed_material, True)
            ),
        )

        cli._ensure_runtime_auth_material(
            fake_config,
            need_terraform=True,
            need_eso_mysterybox=False,
            auto_bootstrap=True,
        )

        assert recreate_calls == [True]
        assert os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] == "auth-key-fresh"
    finally:
        _clear_runtime_auth_env()


def test_wait_for_runtime_auth_token_ready_retries_until_token_service_accepts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="publickey-123",
        private_key_file=private_key_file,
        private_key_pem="PRIVATE-KEY",
        s3_access_key_id="access-key",
        s3_secret_access_key="secret-key",
    )
    token_attempts: list[float] = []
    closed_sdks: list[object] = []
    rendered_messages: list[str] = []

    class _FakeRuntimeAuthSDK:
        def get_token_sync(self, *, timeout: float) -> object:
            token_attempts.append(timeout)
            if len(token_attempts) == 1:
                raise RuntimeError(
                    "Request error INVALID_ARGUMENT: Public Key not exists: "
                    "'publickey-123'; Caused by error: JwtKeyNotExists"
                )
            return object()

        def sync_close(self) -> None:
            closed_sdks.append(self)

    monkeypatch.setattr(cli, "_runtime_auth_token_sdk", lambda _material: _FakeRuntimeAuthSDK())
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    cli._wait_for_runtime_auth_token_ready(material)

    assert len(token_attempts) == 2
    assert len(closed_sdks) == 2
    assert any("waiting for propagation" in message for message in rendered_messages)


def test_auth_bootstrap_ci_syncs_runtime_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=Path("/tmp/nebius-cxcli/client-a-project-123/auth-private.pem"),
        private_key_pem="KEY-DATA",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    monkeypatch.setattr(cli, "_runtime_auth_cache_material", lambda **_kwargs: material)
    monkeypatch.setattr(
        cli,
        "_sync_runtime_auth_profile_to_ci_environment",
        lambda **_kwargs: ("owner/repo", "client-a-project-123", ["A", "B"]),
    )

    result = runner.invoke(
        cli.app,
        [
            "auth",
            "--project-id",
            "project-123",
            "--client-name",
            "client-a",
            "--bootstrap-ci",
            "--github-repo",
            "owner/repo",
        ],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Synced GitHub environment secrets to owner/repo/client-a-project-123" in output
    assert "2" in output


def test_auth_validate_profile_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    status = cli.RuntimeAuthProfileStatus(
        project_id="project-123",
        client_name="client-a",
        cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
        metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
        metadata_exists=True,
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=Path("/tmp/nebius-cxcli/client-a-project-123/auth-private.pem"),
        private_key_exists=True,
        cloud_public_key_exists=True,
        cloud_check_error=None,
        issues=(),
    )
    monkeypatch.setattr(cli, "_runtime_auth_profile_status", lambda **_kwargs: status)
    monkeypatch.setattr(
        cli, "_resolve_project_id_for_auth_bootstrap", lambda **_kwargs: "project-123"
    )

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a", "--validate-profile"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Project ID: project-123" in output
    assert "Profile status: OK" in output


def test_auth_validate_profile_without_target_discovers_all_cached_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = cli.RuntimeAuthProfileStatus(
        project_id="project-123",
        client_name="client-a",
        cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
        metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
        metadata_exists=True,
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=Path("/tmp/nebius-cxcli/client-a-project-123/auth-private.pem"),
        private_key_exists=True,
        cloud_public_key_exists=True,
        cloud_check_error=None,
        issues=(),
    )
    monkeypatch.setattr(
        cli,
        "_discover_runtime_auth_profiles",
        lambda: [("client-a", "project-123")],
    )
    monkeypatch.setattr(cli, "_runtime_auth_profile_status", lambda **_kwargs: status)

    result = runner.invoke(cli.app, ["auth", "--validate-profile"])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Project ID: project-123" in output
    assert "Profile status: OK" in output


def test_auth_validate_profile_fails_on_invalid_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    status = cli.RuntimeAuthProfileStatus(
        project_id="project-123",
        client_name="client-a",
        cache_dir=Path("/tmp/nebius-cxcli/client-a-project-123"),
        metadata_file=Path("/tmp/nebius-cxcli/client-a-project-123/runtime-auth.json"),
        metadata_exists=True,
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=Path("/tmp/nebius-cxcli/client-a-project-123/auth-private.pem"),
        private_key_exists=False,
        cloud_public_key_exists=False,
        cloud_check_error=None,
        issues=("private key file missing",),
    )
    monkeypatch.setattr(cli, "_runtime_auth_profile_status", lambda **_kwargs: status)
    monkeypatch.setattr(
        cli, "_resolve_project_id_for_auth_bootstrap", lambda **_kwargs: "project-123"
    )

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a", "--validate-profile"],
    )

    assert result.exit_code == 1
    assert "Runtime auth profile validation failed for project(s): project-123" in _plain_output(
        result.output
    )
