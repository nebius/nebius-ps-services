from __future__ import annotations

import ast
import inspect
import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli
import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.cluster_handoffs import Handoff
from nebius_cxcli.component_sources import (
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
    GpuCapacityShape,
    QuotaCheck,
    QuotaCoverageGap,
    QuotaReport,
    QuotaRequestChange,
    QuotaRequestFailure,
    QuotaRequestResult,
    QuotaRequirement,
    RegionalQuotaAvailability,
)

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_COPY_PASTE_COMMAND_STYLE_RE = re.compile(r"\x1b\[(?:1;38;2;0;215;255|1;96)m(?P<text>.*?)\x1b\[0m")
_RICH_BOX_RE = re.compile(r"[\u2500-\u257f]")
_RUNTIME_AUTH_ENV_KEYS = (
    "NEBIUS_AUTH_CREDENTIALS_FILE",
    "NEBIUS_IAM_TOKEN",
    "CXCLI_NEBIUS_DELEGATE_ID",
    "NEBIUS_SA_ID",
    "NEBIUS_AUTH_PUBLIC_KEY_ID",
    "NEBIUS_AUTH_PRIVATE_KEY_FILE",
    "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    "NEBIUS_S3_ACCESS_KEY_ID",
    "NEBIUS_S3_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV,
)
_REAL_ENSURE_RUNTIME_AUTH_MATERIAL = cli._ensure_runtime_auth_material
_REAL_LOAD_SOURCE_PAYLOAD = cli._load_source_payload
_REAL_LOAD_CONFIG_PAYLOAD = cli._load_config_payload


def _deliver_test_credentials(delivery: Any, value: Any) -> Any:
    intent = delivery.prepare(
        value,
        operation_id="d" * 32,
        credentials=(("auth-public-key", "publickey-test"),),
    )
    delivery.deliver(value, intent)
    return value


def _load_source_payload_without_auth(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Config file not found: {path}")
    if path.is_dir():
        raise ValueError(
            "Expected a project config.yaml file path, but got a directory: "
            f"{path}. Pass <tenant-folder>/<project-folder>/config.yaml."
        )
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    return payload


def _load_config_payload_without_auth(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("config.yaml root must be a mapping")
    return payload


def _bundled_tool_versions() -> tuple[str, str]:
    settings_path = Path(__file__).resolve().parents[1] / "component_cli_settings.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert isinstance(settings, dict)
    cli_settings = settings["cli"]
    return cli_settings["flux"]["version"], cli_settings["terraform"]["version"]


def _assert_copy_paste_command_styled(rendered: str, command: str) -> None:
    styled_texts = {
        match.group("text") for match in _COPY_PASTE_COMMAND_STYLE_RE.finditer(rendered)
    }
    assert command in styled_texts


def _assert_not_copy_paste_command_styled(rendered: str, text: str) -> None:
    styled_texts = {
        match.group("text") for match in _COPY_PASTE_COMMAND_STYLE_RE.finditer(rendered)
    }
    assert text not in styled_texts


def test_action_confirmations_never_default_to_no() -> None:
    source = inspect.getsource(cli)
    tree = ast.parse(source)
    direct_confirm_defaults: list[object] = []
    default_no_wizard_lines: list[int] = []
    raw_no_prompt_lines: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            function_name = f"{node.func.value.id}.{node.func.attr}"
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        default = keywords.get("default")
        if function_name == "typer.confirm":
            direct_confirm_defaults.append(
                default.value if isinstance(default, ast.Constant) else "<missing-or-dynamic>"
            )
        elif (
            function_name == "_wizard_continue_phase"
            and isinstance(default, ast.Constant)
            and default.value is False
        ):
            default_no_wizard_lines.append(node.lineno)
        elif (
            function_name == "typer.prompt"
            and isinstance(default, ast.Constant)
            and default.value == "n"
        ):
            raw_no_prompt_lines.append(node.lineno)

    assert direct_confirm_defaults == [None]
    assert default_no_wizard_lines == []
    assert raw_no_prompt_lines == []


def _bundled_flux_install_manifest_url() -> str:
    flux_version, _terraform_version = _bundled_tool_versions()
    return f"https://github.com/fluxcd/flux2/releases/download/{flux_version}/install.yaml"


def _mk8s_ready_status(version: str = "1.33", *, nodes: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        version=version,
        ready_node_count=nodes,
        target_node_count=nodes,
        node_count=nodes,
        outdated_node_count=0,
        reconciling=False,
    )


def _empty_quota_report() -> cli.QuotaReport:
    return cli.QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
    )


def _config_with_enabled_mk8s(*, charts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
        "infra": {
            "components": [{"id": "mk8s", "instance_id": "mk8s", "enabled": True, "inputs": {}}]
        },
        "apps": {"charts": charts or []},
    }


@pytest.fixture(autouse=True)
def _reset_component_sources_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", raising=False)
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_load_source_payload", _load_source_payload_without_auth)
    monkeypatch.setattr(cli, "_load_config_payload", _load_config_payload_without_auth)
    monkeypatch.setattr(cli, "assess_live_quotas", lambda *_args, **_kwargs: _empty_quota_report())
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()


def _plain_output(text: str) -> str:
    return _RICH_BOX_RE.sub(" ", _ANSI_ESCAPE_RE.sub("", text))


def _help_contains_option(help_text: str, option: str) -> bool:
    if option in help_text:
        return True
    return option == "--job-refresh-interval" and "--job-refresh-interv" in help_text


def _clear_runtime_auth_env() -> None:
    for name in _RUNTIME_AUTH_ENV_KEYS:
        os.environ.pop(name, None)
    cli._RUNTIME_AUTH_OPERATOR_ENV_SNAPSHOT = None


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
        reports_dir=project_dir / "generated" / "reports",
        path_tenant_folder="tenant-name-example",
        path_project_folder="project-name-example",
    )


def _mk8s_gpu_fabric_payload(*, fabric: str = "fabric-4") -> dict[str, Any]:
    return {
        "client_info": {
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"cluster_name": "cluster1"},
                        "gpu_clusters": {"workers": {"infiniband_fabric": fabric}},
                        "node_groups": {
                            "worker": {
                                "gpu": True,
                                "node_count": 1,
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                                "gpu_cluster_key": "workers",
                                "sfs_filesystem_keys": ["jail"],
                                "reservation": {"policy": "AUTO"},
                            }
                        },
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "values": {},
                }
            ]
        },
    }


def test_render_blocks_direct_mk8s_gpu_fabric_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (paths.generated_dir / "nebius-cxcli-manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli, "load_generated_manifest", lambda _generated_dir: {"runtime_config": {}}
    )
    monkeypatch.setattr(
        cli,
        "runtime_config_from_manifest",
        lambda _manifest: _mk8s_gpu_fabric_payload(fabric="fabric-4"),
    )

    with pytest.raises(RuntimeError, match="migrate node-group"):
        cli._raise_on_render_gpu_fabric_drift(
            _mk8s_gpu_fabric_payload(fabric="fabric-6"),
            paths,
        )


def test_terraform_apply_blocks_mk8s_gpu_fabric_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    monkeypatch.setattr(
        cli,
        "_mk8s_gpu_fabric_specs_from_terraform_state",
        lambda **_kwargs: {
            ("cluster1", "workers"): cli._Mk8sGpuFabricSpec(
                instance_id="cluster1",
                gpu_cluster_key="workers",
                fabric="fabric-4",
                node_groups=(),
            )
        },
    )

    with pytest.raises(RuntimeError, match="Terraform apply would change"):
        cli._raise_on_terraform_gpu_fabric_drift(
            _mk8s_gpu_fabric_payload(fabric="fabric-6"),
            paths,
            {"render": {"module_sources": []}},
            runtime_env={},
            initialize=False,
        )


def test_migrate_node_group_fabric_dry_run_prints_repeatable_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            nebius=SimpleNamespace(
                tenant_id="tenant-1",
                project_id="project-1",
                region_id="eu-north1",
            )
        )
    )
    source_payload = _mk8s_gpu_fabric_payload(fabric="fabric-4")
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: source_payload)
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, {"render": {"module_sources": []}}),
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "_node_group_migration_quota_report", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_mk8s_gpu_fabric_specs_from_terraform_state",
        lambda **_kwargs: {
            ("cluster1", "workers"): cli._Mk8sGpuFabricSpec(
                instance_id="cluster1",
                gpu_cluster_key="workers",
                fabric="fabric-4",
                node_groups=(),
            )
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "migrate",
            "node-group",
            str(paths.config_path),
            "infra:mk8s@cluster1",
            "--node-group",
            "worker",
            "--replacement-node-group",
            "worker-b200",
            "--replacement-name",
            "worker-b200",
            "--to-fabric",
            "fabric-6",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "MK8s node-group migration plan" in result.output
    assert "- current config fabric: fabric-4" in result.output
    assert "- current Terraform state fabric: fabric-4" in result.output
    assert "- requested target fabric: fabric-6" in result.output
    assert "- effective target fabric: fabric-6" in result.output
    assert "- fabric status: changing fabric-4 -> fabric-6" in result.output
    assert "--to-fabric fabric-6 --dry-run" in result.output
    assert "--execute --approve" in result.output


def test_migrate_node_group_execute_requires_approval(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "migrate",
            "node-group",
            str(tmp_path / "missing-config.yaml"),
            "infra:mk8s@cluster1",
            "--node-group",
            "worker",
            "--execute",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "--execute requires --approve" in _plain_output(result.output)


def test_migrate_node_group_omitted_fabric_inherits_current_fabric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            nebius=SimpleNamespace(
                tenant_id="tenant-1",
                project_id="project-1",
                region_id="eu-north1",
            )
        )
    )
    source_payload = _mk8s_gpu_fabric_payload(fabric="fabric-4")
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: source_payload)
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, {"render": {"module_sources": []}}),
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "_node_group_migration_quota_report", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_mk8s_gpu_fabric_specs_from_terraform_state",
        lambda **_kwargs: {
            ("cluster1", "workers"): cli._Mk8sGpuFabricSpec(
                instance_id="cluster1",
                gpu_cluster_key="workers",
                fabric="fabric-4",
                node_groups=(),
            )
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "migrate",
            "node-group",
            str(paths.config_path),
            "infra:mk8s@cluster1",
            "--node-group",
            "worker",
            "--replacement-node-group",
            "worker-b200",
            "--replacement-name",
            "worker-b200",
            "--to-platform",
            "gpu-b200-sxm",
            "--to-preset",
            "8gpu-160vcpu-1792gb",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (
        "- requested target fabric: (not provided; using current fabric when applicable)"
        in result.output
    )
    assert "- effective target fabric: fabric-4" in result.output
    assert "- fabric status: unchanged" in result.output
    assert "--to-fabric" not in result.output


def test_migrate_node_group_same_fabric_keeps_explicit_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            nebius=SimpleNamespace(
                tenant_id="tenant-1",
                project_id="project-1",
                region_id="eu-north1",
            )
        )
    )
    source_payload = _mk8s_gpu_fabric_payload(fabric="fabric-4")
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: source_payload)
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, {"render": {"module_sources": []}}),
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "_node_group_migration_quota_report", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_mk8s_gpu_fabric_specs_from_terraform_state",
        lambda **_kwargs: {
            ("cluster1", "workers"): cli._Mk8sGpuFabricSpec(
                instance_id="cluster1",
                gpu_cluster_key="workers",
                fabric="fabric-4",
                node_groups=(),
            )
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "migrate",
            "node-group",
            str(paths.config_path),
            "infra:mk8s@cluster1",
            "--node-group",
            "worker",
            "--replacement-node-group",
            "worker-b200",
            "--replacement-name",
            "worker-b200",
            "--to-platform",
            "gpu-b200-sxm",
            "--to-preset",
            "8gpu-160vcpu-1792gb",
            "--to-fabric",
            "fabric-4",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "- requested target fabric: fabric-4" in result.output
    assert "- fabric status: unchanged" in result.output
    assert "--to-fabric fabric-4 --dry-run" in result.output


def test_upgrade_readonly_context_does_not_materialize_terraform_tfvars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "deployments" / "tenant-a" / "project-a"
    generated_dir = project_dir / "generated"
    (generated_dir / "infra").mkdir(parents=True)
    (generated_dir / "flux").mkdir()
    (generated_dir / "reports").mkdir()
    config_path = project_dir / "config.yaml"
    config_path.write_text("client_info: {}\n", encoding="utf-8")
    (generated_dir / "nebius-cxcli-manifest.json").write_text(
        json.dumps(
            {
                "schema": "nebius-cxcli-generated/v1",
                "runtime_config": {
                    "client_info": {
                        "client_name": "client-a",
                        "nebius": {
                            "tenant_id": "tenant-123",
                            "project_id": "project-456",
                            "region_id": "eu-north1",
                        },
                    },
                    "infra": {"components": []},
                    "apps": {"charts": []},
                },
                "render": {"terraform_tfvars": {"sentinel": "value"}},
            }
        ),
        encoding="utf-8",
    )

    def _fail_materialize(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only upgrade planning must not write tfvars")

    monkeypatch.setattr(cli, "_materialize_generated_terraform_tfvars", _fail_materialize)

    config, paths, manifest = cli._load_deploy_context_readonly(config_path)

    assert config.client_info.client_name == "client-a"
    assert paths.config_path == config_path.resolve()
    assert manifest["render"]["terraform_tfvars"]["sentinel"] == "value"
    assert not (generated_dir / "infra" / "terraform.auto.tfvars.json").exists()


def test_managed_mk8s_handoff_uses_resolved_cluster_id_without_terraform_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    paths = _fake_paths(tmp_path)
    captured: dict[str, str] = {}

    def _fail_terraform_output(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("handoff should use the resolved cluster_id")

    def _fake_spec(*_args: object, cluster_id: str, access: str, **_kwargs: object):
        captured["cluster_id"] = cluster_id
        captured["access"] = access
        return cli._Mk8sKubeconfigSpec(
            cluster_entry_name="cluster",
            user_entry_name="user",
            context_name="context",
            server="https://127.0.0.1",
            ca_pem="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
            exec_command="nebius",
            exec_args=("iam", "get-access-token"),
        )

    monkeypatch.setattr(cli, "terraform_output_raw", _fail_terraform_output)
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "_mk8s_cluster_handoff_spec", _fake_spec)

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            target={
                "component_id": "mk8s",
                "target_ref": "mk8s",
                "access": "external",
                "cluster_id_output_name": "mk8s_cluster_id",
                "cluster_id": "mk8scluster-123",
            },
            persist_local_kubeconfig=False,
            set_current_context=False,
        )

    assert captured == {"cluster_id": "mk8scluster-123", "access": "external"}
    assert env is not None
    assert env[cli.CLUSTER_HANDOFF_ACCESS_ENV] == "external"
    assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "mk8scluster-123"


def test_managed_mk8s_handoff_uses_preferred_kube_context_without_terraform_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    paths = _fake_paths(tmp_path)
    context_name = "nebius-mk8s-mk8scluster-123-external"
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "contexts": [
                    {
                        "name": context_name,
                        "context": {"cluster": "cluster", "user": "user"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    monkeypatch.setattr(
        cli,
        "terraform_output_raw",
        lambda *_args, **_kwargs: pytest.fail("acceptance handoff must not read Terraform output"),
    )
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda *_args, **_kwargs: pytest.fail("existing kube context should be reused"),
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            target={
                "component_id": "mk8s",
                "target_ref": "mk8s",
                "access": "external",
                "cluster_id_output_name": "mk8s_cluster_id",
                "cluster_id": "mk8scluster-123",
                "kube_context": context_name,
            },
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=False,
        )
        assert Path(env["KUBECONFIG"]).read_text(encoding="utf-8")

    assert env is not None
    assert env[cli.CLUSTER_HANDOFF_ACCESS_ENV] == "external"
    assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "mk8scluster-123"
    assert env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == context_name


def test_long_running_handoff_replaces_existing_context_with_renewable_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    paths = _fake_paths(tmp_path)
    context_name = "existing-context"
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": "old-cluster",
                        "cluster": {"server": "https://old.example.invalid"},
                    }
                ],
                "users": [
                    {
                        "name": "old-user",
                        "user": {
                            "exec": {
                                "apiVersion": "client.authentication.k8s.io/v1",
                                "command": "/stale/checkout/.venv/bin/nebius-cxcli",
                            }
                        },
                    }
                ],
                "contexts": [
                    {
                        "name": context_name,
                        "context": {"cluster": "old-cluster", "user": "old-user"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    captured: dict[str, object] = {}
    renewable_spec = cli._Mk8sKubeconfigSpec(
        cluster_entry_name="cluster-entry",
        user_entry_name="user-entry",
        context_name="renewable-context",
        server="https://mk8s.example.invalid",
        ca_pem="FAKE-CA",
        exec_command=sys.executable,
        exec_args=("-m", "nebius_cxcli", "mk8s-token", "--require-renewable-auth"),
    )

    monkeypatch.setattr(cli, "_renewable_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda _config, *, cluster_id, access, require_renewable_auth=False: (
            captured.update(
                {
                    "cluster_id": cluster_id,
                    "access": access,
                    "require_renewable_auth": require_renewable_auth,
                }
            )
            or renewable_spec
        ),
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            target={
                "component_id": "mk8s",
                "target_ref": "mk8s",
                cli.DEPLOY_TARGET_KIND_FIELD: cli.EXTERNAL_MK8S_TARGET_KIND,
                "access": "external",
                "cluster_id": "mk8scluster-123",
                "kube_context": context_name,
            },
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=False,
            require_renewable_auth=True,
        )
        assert env is not None
        payload = yaml.safe_load(Path(env["KUBECONFIG"]).read_text(encoding="utf-8"))

    assert captured == {
        "cluster_id": "mk8scluster-123",
        "access": "external",
        "require_renewable_auth": True,
    }
    assert env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == "renewable-context"
    assert payload["users"][0]["user"]["exec"]["command"] == sys.executable
    assert payload["users"][0]["user"]["exec"]["args"][:4] == [
        "-m",
        "nebius_cxcli",
        "mk8s-token",
        "--require-renewable-auth",
    ]


def test_long_running_handoff_requires_immutable_cluster_id(
    tmp_path: Path,
) -> None:
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )

    with (
        pytest.raises(RuntimeError, match="requires an immutable cluster_id"),
        ExitStack() as stack,
    ):
        cli._prepare_cluster_handoff_kube_env(
            config,
            _fake_paths(tmp_path),
            stack=stack,
            target={
                "component_id": "mk8s",
                "target_ref": "mk8s",
                "access": "external",
                "kube_context": "existing-context",
            },
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=False,
            require_renewable_auth=True,
        )


def test_managed_mk8s_handoff_rejects_missing_context_when_terraform_output_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-456"),
        )
    )
    paths = _fake_paths(tmp_path)
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing-kubeconfig"))
    monkeypatch.setattr(
        cli,
        "terraform_output_raw",
        lambda *_args, **_kwargs: pytest.fail("Terraform output fallback must be disabled"),
    )

    with pytest.raises(RuntimeError) as exc_info, ExitStack() as stack:
        cli._prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            target={
                "component_id": "mk8s",
                "target_ref": "mk8s",
                "access": "external",
                "cluster_id_output_name": "mk8s_cluster_id",
            },
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=False,
        )

    message = str(exc_info.value)
    assert "does not read Terraform state" in message
    assert "generated/reports/deploy-report.md" in message


def test_upgrade_node_template_config_only_guided_dry_run_prompts_required_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
                "client_info": {
                    "client_name": "test-client",
                    "nebius": {
                        "tenant_id": "tenant-1",
                        "project_id": "project-1",
                        "region_id": "eu-north1",
                    },
                    "notifications": {},
                },
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": {
                                "cluster": {
                                    "cluster_name": "mk8s-live",
                                    "k8s_version": "1.32",
                                },
                                "node_groups": {
                                    "system": {
                                        "version": "1.32",
                                        "platform": "cpu-platform",
                                        "preset": "cpu-4-16",
                                        "os": "ubuntu22.04",
                                    },
                                    "gpu": {
                                        "gpu": True,
                                        "gpu_stack_source": "nebius_image",
                                        "version": "1.32",
                                        "platform": "gpu-platform",
                                        "preset": "8gpu",
                                        "os": "ubuntu22.04",
                                        "gpu_stack_preset": "cuda12.8",
                                    },
                                },
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    original_config = paths.config_path.read_text(encoding="utf-8")
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="test-client",
            nebius=SimpleNamespace(project_id="project-1"),
        )
    )
    manifest = {"deploy": {"targets": [_mk8s_target(paths)]}}
    prompt_paths: list[str] = []
    wait_calls: list[tuple[str, str]] = []
    rich_console = cli.Console(record=True, width=300)

    def _live_node_group(
        *,
        id: str,
        name: str,
        version: str,
        os: str,
        platform: str,
        preset: str,
        drivers_preset: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(id=id, name=name, resource_version=1),
            spec=SimpleNamespace(
                version=version,
                template=SimpleNamespace(
                    os=os,
                    resources=SimpleNamespace(platform=platform, preset=preset),
                    gpu_settings=SimpleNamespace(drivers_preset=drivers_preset),
                ),
            ),
            status=_mk8s_ready_status(version),
        )

    class FakeSdk:
        def sync_close(self) -> None:
            wait_calls.append(("sdk-close", ""))

    class FakeExecutor:
        def __init__(self, sdk: object) -> None:
            assert isinstance(sdk, FakeSdk)

        def get_cluster_by_name(self, *, project_id: str, name: str) -> SimpleNamespace:
            assert (project_id, name) == ("project-1", "mk8s-live")
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name=name, resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.32")),
            )

        def control_plane_versions(self) -> tuple[str, ...]:
            return ("1.33",)

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (
                _live_node_group(
                    id="ng-system",
                    name="mk8s-live-system",
                    version="1.32",
                    os="ubuntu22.04",
                    platform="cpu-platform",
                    preset="cpu-4-16",
                ),
                _live_node_group(
                    id="ng-gpu",
                    name="mk8s-live-gpu",
                    version="1.32",
                    os="ubuntu22.04",
                    platform="gpu-platform",
                    preset="8gpu",
                    drivers_preset="cuda12.8",
                ),
            )

        def compatibility_choices(self, *, target_version: str, platform: str):
            assert target_version == "1.33"
            return (
                SimpleNamespace(
                    platform=platform,
                    os="ubuntu24.04",
                    drivers_preset="cuda13.0" if platform == "gpu-platform" else "",
                ),
            )

    def _prompt_scalar(
        path_label: str,
        current: object,
        *,
        choices: list[cli.OptionChoice] | None = None,
        type_hint: str | None = None,
        required: bool = False,
        unset_on_skip: bool = False,
        **_kwargs: object,
    ) -> tuple[object, bool]:
        del type_hint, required
        answers: dict[str, Any] = {
            "upgrade.node_template.target": "infra:mk8s@mk8s",
            "upgrade.node_template.to_version": "1.33",
            "upgrade.node_template.node_group": "",
            "upgrade.node_template.to_os": "ubuntu24.04",
            "upgrade.node_template.to_gpu_stack_preset": "cuda13.0",
            "upgrade.node_template.dry_run": True,
            "upgrade.node_template.strategy": "safe-surge",
            "upgrade.node_template.strategy_max_surge_count": 1,
            "upgrade.node_template.drain_timeout": "auto",
            "upgrade.node_template.run_post_upgrade_validations": True,
        }
        prompt_paths.append(path_label)
        if path_label == "upgrade.node_template.node_group":
            assert current is None
            assert choices is None
            assert unset_on_skip is True
        elif path_label == "upgrade.node_template.to_os":
            assert choices is not None
            assert [choice.value for choice in choices] == ["ubuntu24.04"]
            assert current == "ubuntu24.04"
        elif path_label == "upgrade.node_template.to_gpu_stack_preset":
            assert choices is not None
            assert [choice.value for choice in choices] == ["cuda13.0"]
            assert current == "cuda13.0"
        if choices:
            assert answers[path_label] in {choice.value for choice in choices}
        return answers[path_label], False

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda _manifest, *, target_instance_id: {
            "component_id": "mk8s",
            "target_ref": target_instance_id,
            "access": "external",
            "cluster_id_output_name": "cluster_id",
        },
    )
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_target_with_cluster_id",
        lambda target, *, cluster_id: {**target, "cluster_id": cluster_id},
    )
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: FakeSdk())
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "collect_kubernetes_preflight_findings", lambda *, kube_env: ())
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    monkeypatch.setattr(cli, "_prompt_scalar_override", _prompt_scalar)
    monkeypatch.setattr(cli, "console", rich_console)

    cli.upgrade_node_template_command(paths.config_path)

    rendered = rich_console.export_text()
    assert prompt_paths == [
        "upgrade.node_template.target",
        "upgrade.node_template.to_version",
        "upgrade.node_template.node_group",
        "upgrade.node_template.to_os",
        "upgrade.node_template.to_gpu_stack_preset",
        "upgrade.node_template.dry_run",
        "upgrade.node_template.strategy",
        "upgrade.node_template.strategy_max_surge_count",
        "upgrade.node_template.drain_timeout",
        "upgrade.node_template.run_post_upgrade_validations",
    ]
    assert "Discovering live MK8s state and preparing node-template upgrade plan..." in rendered
    assert "MK8s node-template upgrade plan" in rendered
    assert "mk8s-live-system: version 1.32 -> 1.33, OS ubuntu22.04 -> ubuntu24.04" in rendered
    assert (
        "mk8s-live-gpu: version 1.32 -> 1.33, OS ubuntu22.04 -> ubuntu24.04, "
        "GPU stack cuda12.8 -> cuda13.0"
    ) in rendered
    assert (
        f"nebius-cxcli upgrade node-template {paths.config_path} infra:mk8s@mk8s "
        "--to-version 1.33 --to-os ubuntu24.04 --strategy safe-surge "
        "--strategy-max-surge-count 1 --to-gpu-stack-preset cuda13.0 "
        "--drain-timeout auto --no-interactive --dry-run"
    ) in rendered
    assert paths.config_path.read_text(encoding="utf-8") == original_config
    assert wait_calls == [("sdk-close", "")]


def test_upgrade_node_template_guided_backtracks_across_setup_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
                "client_info": {
                    "client_name": "test-client",
                    "nebius": {
                        "tenant_id": "tenant-1",
                        "project_id": "project-1",
                        "region_id": "eu-north1",
                    },
                    "notifications": {},
                },
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": {
                                "cluster": {
                                    "cluster_name": "mk8s-live",
                                    "k8s_version": "1.32",
                                },
                                "node_groups": {
                                    "system": {
                                        "version": "1.32",
                                        "platform": "cpu-platform",
                                        "preset": "cpu-4-16",
                                        "os": "ubuntu22.04",
                                    },
                                    "gpu": {
                                        "gpu": True,
                                        "gpu_stack_source": "nebius_image",
                                        "version": "1.32",
                                        "platform": "gpu-platform",
                                        "preset": "8gpu",
                                        "os": "ubuntu22.04",
                                        "gpu_stack_preset": "cuda12.8",
                                    },
                                },
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="test-client",
            nebius=SimpleNamespace(project_id="project-1"),
        )
    )
    manifest = {"deploy": {"targets": [_mk8s_target(paths)]}}
    prompt_paths: list[str] = []
    responses: dict[str, list[Any]] = {
        "upgrade.node_template.target": ["infra:mk8s@mk8s"],
        "upgrade.node_template.to_version": ["1.33"],
        "upgrade.node_template.node_group": ["", ""],
        "upgrade.node_template.to_os": [cli._WIZARD_BACKTRACK, "ubuntu24.04"],
        "upgrade.node_template.to_gpu_stack_preset": ["cuda13.0", "cuda13.0"],
        "upgrade.node_template.strategy": [cli._WIZARD_BACKTRACK, "safe-surge"],
        "upgrade.node_template.strategy_max_surge_count": [1],
        "upgrade.node_template.drain_timeout": ["auto"],
    }
    rich_console = cli.Console(record=True, width=300)

    def _live_node_group(
        *,
        id: str,
        name: str,
        version: str,
        os: str,
        platform: str,
        preset: str,
        drivers_preset: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(id=id, name=name, resource_version=1),
            spec=SimpleNamespace(
                version=version,
                template=SimpleNamespace(
                    os=os,
                    resources=SimpleNamespace(platform=platform, preset=preset),
                    gpu_settings=SimpleNamespace(drivers_preset=drivers_preset),
                ),
            ),
            status=_mk8s_ready_status(version),
        )

    class FakeSdk:
        def sync_close(self) -> None:
            pass

    class FakeExecutor:
        def __init__(self, sdk: object) -> None:
            assert isinstance(sdk, FakeSdk)

        def get_cluster_by_name(self, *, project_id: str, name: str) -> SimpleNamespace:
            assert (project_id, name) == ("project-1", "mk8s-live")
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name=name, resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.32")),
            )

        def control_plane_versions(self) -> tuple[str, ...]:
            return ("1.33",)

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (
                _live_node_group(
                    id="ng-system",
                    name="mk8s-live-system",
                    version="1.32",
                    os="ubuntu22.04",
                    platform="cpu-platform",
                    preset="cpu-4-16",
                ),
                _live_node_group(
                    id="ng-gpu",
                    name="mk8s-live-gpu",
                    version="1.32",
                    os="ubuntu22.04",
                    platform="gpu-platform",
                    preset="8gpu",
                    drivers_preset="cuda12.8",
                ),
            )

        def compatibility_choices(self, *, target_version: str, platform: str):
            assert target_version == "1.33"
            return (
                SimpleNamespace(
                    platform=platform,
                    os="ubuntu24.04",
                    drivers_preset="cuda13.0" if platform == "gpu-platform" else "",
                ),
            )

    def _prompt_scalar(
        path_label: str,
        current: object,
        *,
        choices: list[cli.OptionChoice] | None = None,
        type_hint: str | None = None,
        required: bool = False,
        unset_on_skip: bool = False,
        **_kwargs: object,
    ) -> tuple[object, bool]:
        del current, choices, type_hint, required, unset_on_skip
        prompt_paths.append(path_label)
        return responses[path_label].pop(0), False

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda _manifest, *, target_instance_id: {
            "component_id": "mk8s",
            "target_ref": target_instance_id,
            "access": "external",
            "cluster_id_output_name": "cluster_id",
        },
    )
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_target_with_cluster_id",
        lambda target, *, cluster_id: {**target, "cluster_id": cluster_id},
    )
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: FakeSdk())
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "collect_kubernetes_preflight_findings", lambda *, kube_env: ())
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    monkeypatch.setattr(cli, "_prompt_scalar_override", _prompt_scalar)
    monkeypatch.setattr(cli, "console", rich_console)

    cli.upgrade_node_template_command(paths.config_path, dry_run=True, skip_validations=True)

    assert prompt_paths == [
        "upgrade.node_template.target",
        "upgrade.node_template.to_version",
        "upgrade.node_template.node_group",
        "upgrade.node_template.to_os",
        "upgrade.node_template.node_group",
        "upgrade.node_template.to_os",
        "upgrade.node_template.to_gpu_stack_preset",
        "upgrade.node_template.strategy",
        "upgrade.node_template.to_gpu_stack_preset",
        "upgrade.node_template.strategy",
        "upgrade.node_template.strategy_max_surge_count",
        "upgrade.node_template.drain_timeout",
    ]
    rendered = rich_console.export_text()
    assert "Discovering live MK8s state and preparing node-template upgrade plan..." in rendered
    assert "MK8s node-template upgrade plan" in rendered
    assert all(not remaining for remaining in responses.values())


def test_upgrade_node_template_no_interactive_requires_explicit_required_values(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        cli.app,
        [
            "upgrade",
            "node-template",
            str(tmp_path / "missing-config.yaml"),
            "--no-interactive",
        ],
    )

    assert result.exit_code != 0
    flat_output = " ".join(result.output.split())
    assert (
        "Missing target selector and one of --to-version <major.minor>, --to-os <os>, "
        "or --to-gpu-stack-preset <preset>"
    ) in flat_output
    assert "allow interactive prompting" in flat_output
    assert "Config file not found" not in result.output


def test_upgrade_node_template_stages_control_plane_then_combined_node_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": {
                                "cluster": {
                                    "cluster_name": "mk8s-live",
                                    "k8s_version": "1.32",
                                },
                                "node_groups": {
                                    "system": {
                                        "version": "1.32",
                                        "platform": "cpu-platform",
                                        "preset": "cpu-4-16",
                                        "os": "ubuntu22.04",
                                    },
                                    "gpu": {
                                        "gpu": True,
                                        "gpu_stack_source": "nebius_image",
                                        "version": "1.32",
                                        "platform": "gpu-platform",
                                        "preset": "8gpu",
                                        "os": "ubuntu22.04",
                                        "gpu_stack_preset": "cuda12.8",
                                    },
                                },
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="test-client",
            nebius=SimpleNamespace(project_id="project-1"),
        )
    )
    manifest = {"deploy": {"targets": []}}
    plan_stages: list[dict[str, str]] = []
    apply_stages: list[dict[str, str]] = []
    wait_calls: list[tuple[str, str]] = []
    completed_templates: dict[str, tuple[str, str, str]] = {}

    def _live_node_group(
        *,
        id: str,
        name: str,
        version: str,
        os: str,
        platform: str,
        preset: str,
        drivers_preset: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(id=id, name=name, resource_version=1),
            spec=SimpleNamespace(
                version=version,
                template=SimpleNamespace(
                    os=os,
                    resources=SimpleNamespace(platform=platform, preset=preset),
                    gpu_settings=SimpleNamespace(drivers_preset=drivers_preset),
                ),
            ),
            status=_mk8s_ready_status(version),
        )

    class FakeSdk:
        def sync_close(self) -> None:
            wait_calls.append(("sdk-close", ""))

    class FakeExecutor:
        def __init__(self, sdk: object) -> None:
            assert isinstance(sdk, FakeSdk)

        def get_cluster_by_name(self, *, project_id: str, name: str) -> SimpleNamespace:
            assert (project_id, name) == ("project-1", "mk8s-live")
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name=name, resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.32")),
            )

        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name="mk8s-live", resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.33")),
            )

        def control_plane_versions(self) -> tuple[str, ...]:
            return ("1.33",)

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            system_template = completed_templates.get(
                "ng-system",
                ("1.32", "ubuntu22.04", ""),
            )
            gpu_template = completed_templates.get(
                "ng-gpu",
                ("1.32", "ubuntu22.04", "cuda12.8"),
            )
            return (
                _live_node_group(
                    id="ng-system",
                    name="mk8s-live-system",
                    version=system_template[0],
                    os=system_template[1],
                    platform="cpu-platform",
                    preset="cpu-4-16",
                    drivers_preset=system_template[2],
                ),
                _live_node_group(
                    id="ng-gpu",
                    name="mk8s-live-gpu",
                    version=gpu_template[0],
                    os=gpu_template[1],
                    platform="gpu-platform",
                    preset="8gpu",
                    drivers_preset=gpu_template[2],
                ),
            )

        def compatibility_choices(self, *, target_version: str, platform: str):
            assert target_version == "1.33"
            return (
                SimpleNamespace(
                    platform=platform,
                    os="ubuntu24.04",
                    drivers_preset="cuda13.0" if platform == "gpu-platform" else "",
                ),
            )

        def wait_cluster_version(self, *, cluster_id: str, version: str) -> None:
            wait_calls.append(("control-plane", f"{cluster_id}:{version}"))

        def wait_node_group_node_template(
            self,
            *,
            cluster_id: str,
            node_group_id: str,
            version: str,
            os: str,
            drivers_preset: str | None,
            timeout_seconds: int,
        ) -> None:
            completed_templates[node_group_id] = (version, os, drivers_preset or "")
            wait_calls.append(
                (
                    "node-template",
                    f"{cluster_id}:{node_group_id}:{version}:{os}:{drivers_preset}:{timeout_seconds}",
                )
            )

    def _current_state() -> dict[str, str]:
        payload = yaml.safe_load(paths.config_path.read_text(encoding="utf-8"))
        component = payload["infra"]["components"][0]
        groups = component["inputs"]["node_groups"]
        return {
            "cluster": component["inputs"]["cluster"]["k8s_version"],
            "system_version": groups["system"]["version"],
            "system_os": groups["system"]["os"],
            "gpu_version": groups["gpu"]["version"],
            "gpu_os": groups["gpu"]["os"],
            "gpu_stack": groups["gpu"]["gpu_stack_preset"],
        }

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli, "_load_deploy_context", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda _manifest, *, target_instance_id: {
            "component_id": "mk8s",
            "target_ref": target_instance_id,
            "access": "external",
            "cluster_id_output_name": "cluster_id",
        },
    )
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_target_with_cluster_id",
        lambda target, *, cluster_id: {**target, "cluster_id": cluster_id},
    )
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: FakeSdk())
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "collect_kubernetes_preflight_findings", lambda *, kube_env: ())
    monkeypatch.setattr(cli, "_run_generated_bundle_validation", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "render_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_manifest_status_watchers", lambda _manifest: [])
    monkeypatch.setattr(cli, "_enabled_status_watcher_specs", lambda _config: [])
    monkeypatch.setattr(
        cli,
        "terraform_plan",
        lambda *_args, **_kwargs: plan_stages.append(_current_state()),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *_args, **_kwargs: apply_stages.append(_current_state()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [])

    with cli.console.capture() as capture:
        cli.upgrade_node_template_command(
            paths.config_path,
            "infra:mk8s@mk8s",
            to_version="1.33",
            to_os="ubuntu24.04",
            to_gpu_stack_preset="cuda13.0",
            dry_run=False,
        )
    output = _plain_output(capture.get())

    expected_stages = [
        {
            "cluster": "1.33",
            "system_version": "1.32",
            "system_os": "ubuntu22.04",
            "gpu_version": "1.32",
            "gpu_os": "ubuntu22.04",
            "gpu_stack": "cuda12.8",
        },
        {
            "cluster": "1.33",
            "system_version": "1.33",
            "system_os": "ubuntu24.04",
            "gpu_version": "1.32",
            "gpu_os": "ubuntu22.04",
            "gpu_stack": "cuda12.8",
        },
        {
            "cluster": "1.33",
            "system_version": "1.33",
            "system_os": "ubuntu24.04",
            "gpu_version": "1.33",
            "gpu_os": "ubuntu24.04",
            "gpu_stack": "cuda13.0",
        },
        {
            "cluster": "1.33",
            "system_version": "1.33",
            "system_os": "ubuntu24.04",
            "gpu_version": "1.33",
            "gpu_os": "ubuntu24.04",
            "gpu_stack": "cuda13.0",
        },
    ]
    assert (
        "Node-template upgrade execution stages are per control-plane hop and per node group"
        in output
    )
    assert "not per node:" in output
    assert "1 control-plane stage(s)" in output
    assert "2 node-group template stage(s)" in output
    assert "- compatibility matrix:" in output
    assert "  - gpu-platform:" in output
    assert "    - ubuntu24.04: cuda13.0" in output
    compact_output = " ".join(output.split())
    assert "control-plane upgrade to Kubernetes 1.33" in compact_output
    assert (
        "node-group mk8s-live-system node-template upgrade to Kubernetes 1.33, OS ubuntu24.04"
    ) in compact_output
    assert (
        "node-group mk8s-live-gpu node-template upgrade to Kubernetes 1.33, OS ubuntu24.04"
    ) in compact_output
    assert plan_stages == expected_stages
    assert apply_stages == expected_stages
    assert wait_calls == [
        ("control-plane", "cluster-1:1.33"),
        ("node-template", "cluster-1:ng-system:1.33:ubuntu24.04:None:3600"),
        ("node-template", "cluster-1:ng-gpu:1.33:ubuntu24.04:cuda13.0:3600"),
        ("sdk-close", ""),
    ]
    assert "MK8s node-template upgrade report:" in output
    report_path = paths.reports_dir / cli.UPGRADE_NODE_TEMPLATE_REPORT_FILENAME
    report_json_path = paths.reports_dir / cli.UPGRADE_NODE_TEMPLATE_REPORT_JSON_FILENAME
    assert report_path.exists()
    assert report_json_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "# MK8s Node Template Upgrade Report" in report
    assert "- Target: `infra:mk8s@mk8s`" in report
    assert "- Kubernetes version: `1.32` -> `1.33`" in report
    assert "- `mk8s-live-system`: `PASS`" in report
    assert "- `mk8s-live-gpu`: `PASS`" in report
    payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "nebius-cxcli-upgrade-node-template-report/v1"
    assert payload["target"] == "infra:mk8s@mk8s"
    assert payload["validations"]["overall_status"] == "not_run"
    assert [group["name"] for group in payload["node_groups"]] == [
        "mk8s-live-system",
        "mk8s-live-gpu",
    ]


def test_upgrade_node_template_safe_surge_quota_blocks_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
                "client_info": {
                    "nebius": {
                        "tenant_id": "tenant-1",
                        "project_id": "project-1",
                        "region_id": "eu-north1",
                    }
                },
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": {
                                "cluster": {
                                    "cluster_name": "mk8s-live",
                                    "k8s_version": "1.32",
                                },
                                "node_groups": {
                                    "system": {
                                        "version": "1.32",
                                        "platform": "cpu-platform",
                                        "preset": "cpu-4-16",
                                        "os": "ubuntu22.04",
                                    },
                                },
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="test-client",
            nebius=SimpleNamespace(
                tenant_id="tenant-1",
                project_id="project-1",
                region_id="eu-north1",
            ),
        )
    )
    manifest = {"deploy": {"targets": []}}
    calls: list[str] = []

    class FakeSdk:
        def sync_close(self) -> None:
            calls.append("sdk-close")

    class FakeExecutor:
        def __init__(self, sdk: object) -> None:
            assert isinstance(sdk, FakeSdk)

        def get_cluster_by_name(self, *, project_id: str, name: str) -> SimpleNamespace:
            assert (project_id, name) == ("project-1", "mk8s-live")
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name=name, resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.32")),
            )

        def control_plane_versions(self) -> tuple[str, ...]:
            return ("1.33",)

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        id="ng-system",
                        name="mk8s-live-system",
                        resource_version=1,
                    ),
                    spec=SimpleNamespace(
                        version="1.32",
                        template=SimpleNamespace(
                            os="ubuntu22.04",
                            resources=SimpleNamespace(
                                platform="cpu-platform",
                                preset="cpu-4-16",
                            ),
                            gpu_settings=SimpleNamespace(drivers_preset=""),
                        ),
                    ),
                    status=_mk8s_ready_status("1.32"),
                ),
            )

        def compatibility_choices(self, *, target_version: str, platform: str):
            assert target_version == "1.33"
            return (SimpleNamespace(platform=platform, os="ubuntu24.04", drivers_preset=""),)

    def _quota_preflight(**kwargs: object) -> None:
        request_groups = kwargs["request_groups"]
        assert tuple(group.name for group in request_groups) == ("mk8s-live-system",)
        calls.append("quota-preflight")
        raise RuntimeError("safe-surge quota shortage")

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda _manifest, *, target_instance_id: {
            "component_id": "mk8s",
            "target_ref": target_instance_id,
            "access": "external",
            "cluster_id_output_name": "cluster_id",
        },
    )
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_target_with_cluster_id",
        lambda target, *, cluster_id: {**target, "cluster_id": cluster_id},
    )
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: FakeSdk())
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "collect_kubernetes_preflight_findings", lambda *, kube_env: ())
    monkeypatch.setattr(cli, "_run_node_template_safe_surge_quota_preflight", _quota_preflight)
    monkeypatch.setattr(cli, "_run_generated_bundle_validation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "render_command", lambda *_args, **_kwargs: calls.append("render"))
    monkeypatch.setattr(cli, "terraform_plan", lambda *_args, **_kwargs: calls.append("plan"))
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *_args, **_kwargs: calls.append("apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "upgrade",
            "node-template",
            str(paths.config_path),
            "infra:mk8s@mk8s",
            "--to-version",
            "1.33",
            "--to-os",
            "ubuntu24.04",
            "--strategy",
            "safe-surge",
            "--strategy-max-surge-count",
            "1",
            "--no-interactive",
        ],
    )

    assert result.exit_code != 0
    assert "safe-surge quota shortage" in _plain_output(result.output)
    assert "quota-preflight" in calls
    assert "render" not in calls
    assert "plan" not in calls
    assert "apply" not in calls


def test_node_template_safe_surge_quota_preflight_counts_selected_surge_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "id": "mk8s",
        "instance_id": "mk8s",
        "enabled": True,
        "inputs": {
            "gpu_clusters": {"workers": {"infiniband_fabric": "fabric-6"}},
            "node_groups": {
                "system": {
                    "name": "mk8s-live-system",
                    "version": "1.32",
                    "platform": "cpu-platform",
                    "preset": "cpu-4-16",
                    "os": "ubuntu22.04",
                    "node_count": 3,
                },
                "gpu": {
                    "name": "mk8s-live-gpu",
                    "gpu": True,
                    "gpu_stack_source": "nebius_image",
                    "version": "1.32",
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu22.04",
                    "gpu_stack_preset": "cuda12.8",
                    "gpu_cluster_key": "workers",
                    "reservation": {"policy": "AUTO"},
                    "autoscaling": {"initial_count": 4, "max_count": 8},
                },
            },
        },
    }
    plan = cli.plan_node_template_upgrade(
        target=cli.parse_upgrade_selector("infra:mk8s@mk8s"),
        cluster=SimpleNamespace(
            metadata=SimpleNamespace(id="cluster-1", name="mk8s-live", resource_version=1),
            spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.32")),
        ),
        cluster_id="cluster-1",
        source_component=source,
        target_version="1.33",
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        disruption_policy="safe-surge",
        drain_timeout=cli.resolve_drain_timeout("safe-surge", "auto"),
        strategy_max_surge_count=2,
        live_node_groups=(
            SimpleNamespace(
                metadata=SimpleNamespace(
                    id="ng-system", name="mk8s-live-system", resource_version=1
                ),
                spec=SimpleNamespace(
                    version="1.32",
                    template=SimpleNamespace(
                        os="ubuntu22.04",
                        resources=SimpleNamespace(platform="cpu-platform", preset="cpu-4-16"),
                        gpu_settings=SimpleNamespace(drivers_preset=""),
                    ),
                ),
                status=_mk8s_ready_status("1.32"),
            ),
            SimpleNamespace(
                metadata=SimpleNamespace(id="ng-gpu", name="mk8s-live-gpu", resource_version=1),
                spec=SimpleNamespace(
                    version="1.32",
                    template=SimpleNamespace(
                        os="ubuntu22.04",
                        resources=SimpleNamespace(platform="gpu-platform", preset="8gpu"),
                        gpu_settings=SimpleNamespace(drivers_preset="cuda12.8"),
                    ),
                ),
                status=_mk8s_ready_status("1.32"),
            ),
        ),
        compatibility_lookup=lambda **kwargs: (
            SimpleNamespace(
                platform=kwargs["platform"],
                os="ubuntu24.04",
                drivers_preset="cuda13.0" if kwargs["platform"] == "gpu-platform" else "",
            ),
        ),
    )
    estimate_inputs: list[Mapping[str, Any]] = []
    assessed_requirements: list[QuotaRequirement] = []

    def _estimate(**kwargs: object):
        inputs = kwargs["inputs"]
        assert isinstance(inputs, Mapping)
        estimate_inputs.append(inputs)
        groups = inputs["node_groups"]
        assert isinstance(groups, Mapping)
        group = next(iter(groups.values()))
        assert isinstance(group, Mapping)
        assert group["node_count"] == 2
        assert "autoscaling" not in group
        if group.get("gpu"):
            assert group["gpu_cluster_key"] == "workers"
            assert group["reservation"] == {"policy": "AUTO"}
            assert inputs["gpu_clusters"] == {"workers": {"infiniband_fabric": "fabric-6"}}
            return (
                (
                    QuotaRequirement(
                        component_id="mk8s",
                        instance_id="mk8s-node-template-surge-gpu",
                        component_label="gpu surge",
                        quota_name="compute.instance.gpu.h100",
                        region="eu-north1",
                        required=16,
                        reason="2 GPU surge nodes",
                        gpu_capacity_shape=GpuCapacityShape(
                            platform="gpu-platform",
                            preset="8gpu",
                            fabric="fabric-6",
                            mode="auto",
                            gpu_count_per_instance=8,
                        ),
                    ),
                ),
                (),
            )
        return (
            (
                QuotaRequirement(
                    component_id="mk8s",
                    instance_id="mk8s-node-template-surge-system",
                    component_label="system surge",
                    quota_name="compute.instance.non-gpu.vcpu",
                    region="eu-north1",
                    required=8,
                    reason="2 CPU surge nodes",
                ),
            ),
            (),
        )

    def _assess(**kwargs: object) -> QuotaReport:
        assert kwargs["tenant_id"] == "tenant-1"
        assert kwargs["project_id"] == "project-1"
        assert kwargs["region_id"] == "eu-north1"
        assessed_requirements.extend(kwargs["requirements"])
        return _empty_quota_report()

    monkeypatch.setattr(cli, "estimate_mk8s_quota_requirements", _estimate)
    monkeypatch.setattr(cli, "assess_live_quota_requirements", _assess)

    with cli.console.capture():
        cli._run_node_template_safe_surge_quota_preflight(
            source_payload={
                "client_info": {
                    "nebius": {
                        "tenant_id": "tenant-1",
                        "project_id": "project-1",
                        "region_id": "eu-north1",
                    }
                }
            },
            generated_config=SimpleNamespace(client_info=SimpleNamespace(nebius=SimpleNamespace())),
            source_component=source,
            plan=plan,
            max_surge_count=2,
            request_groups=plan.node_groups,
        )

    assert len(estimate_inputs) == 2
    assert {item.quota_name: item.required for item in assessed_requirements} == {
        "compute.instance.gpu.h100": 16,
        "compute.instance.non-gpu.vcpu": 8,
    }


def test_node_template_safe_surge_quota_preflight_reports_failure_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "inputs": {
            "node_groups": {
                "system": {
                    "platform": "cpu-platform",
                    "preset": "cpu-4-16",
                    "node_count": 3,
                },
            },
        },
    }
    plan = SimpleNamespace(
        target=SimpleNamespace(instance_id="mk8s"),
        cluster_name="mk8s-live",
    )
    group = SimpleNamespace(
        name="mk8s-live-system",
        platform="cpu-platform",
        preset="cpu-4-16",
        gpu=False,
        source=SimpleNamespace(
            key="system",
            gpu_cluster_key="",
            gpu_cluster_id="",
            gpu_stack_source="",
            reservation_policy="",
            reservation_ids=(),
        ),
    )

    def _estimate(**_kwargs: object):
        return (
            (
                QuotaRequirement(
                    component_id="mk8s",
                    instance_id="mk8s-node-template-surge-system",
                    component_label="system surge",
                    quota_name="compute.instance.non-gpu.vcpu",
                    region="eu-north1",
                    required=4,
                    reason="1 CPU surge node",
                ),
            ),
            (),
        )

    def _assess(**_kwargs: object) -> QuotaReport:
        return QuotaReport(
            tenant_id="tenant-1",
            project_id="project-1",
            region_id="eu-north1",
            checked_at="2026-04-10T00:00:00+00:00",
            errors=("quota lookup failed",),
        )

    monkeypatch.setattr(cli, "estimate_mk8s_quota_requirements", _estimate)
    monkeypatch.setattr(cli, "assess_live_quota_requirements", _assess)

    with cli.console.capture() as capture, pytest.raises(RuntimeError) as excinfo:
        cli._run_node_template_safe_surge_quota_preflight(
            source_payload={
                "client_info": {
                    "nebius": {
                        "tenant_id": "tenant-1",
                        "project_id": "project-1",
                        "region_id": "eu-north1",
                    }
                }
            },
            generated_config=SimpleNamespace(client_info=SimpleNamespace(nebius=SimpleNamespace())),
            source_component=source,
            plan=plan,
            max_surge_count=1,
            request_groups=(group,),
        )

    output = _plain_output(capture.get())
    message = str(excinfo.value)
    assert "mk8s-live-system requires 1 temporary surge node" in output
    assert "quota assessment was not fully available" not in output
    assert "quota assessment was not fully available" in message
    assert message.count("quota assessment was not fully available") == 1


def test_node_template_safe_surge_quota_preflight_reports_capacity_fabric_guidance() -> None:
    report = QuotaReport(
        tenant_id="tenant-1",
        project_id="project-1",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s-node-template-surge-worker",
                component_label="mk8s@worker surge",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=8,
                reason=(
                    "1 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb for "
                    "'worker-safe-surge' (reservation policy AUTO)"
                ),
                unit="count",
                available=0,
                sufficient=False,
                tenant_limit=32,
                tenant_usage=8,
                project_limit=None,
                project_usage=8,
                source_scope="capacity-dashboard/auto",
                description=(
                    "Capacity Dashboard GPU availability "
                    "(AUTO reservation policy: reserved + regular-vm slots, "
                    "fabric fabric-6, converted to GPU units)"
                ),
                contributors=(),
            ),
        ),
    )

    message = _plain_output(cli._node_template_safe_surge_quota_failure_message(report))

    assert "same fabric and reservation policy" in message
    assert "migrate to a new GPU node group/fabric" in message
    assert "detected insufficient Nebius quota/capacity" in message
    assert "AUTO reservation policy: reserved + regular-vm slots, fabric fabric-6" in message
    assert "[#ffbf00]" not in message
    assert "[/]" not in message


def test_upgrade_node_template_node_group_stages_only_selected_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": {
                                "cluster": {
                                    "cluster_name": "mk8s-live",
                                    "k8s_version": "1.32",
                                },
                                "node_groups": {
                                    "system": {
                                        "version": "1.32",
                                        "platform": "cpu-platform",
                                        "preset": "cpu-4-16",
                                        "os": "ubuntu22.04",
                                    },
                                    "gpu": {
                                        "gpu": True,
                                        "gpu_stack_source": "nebius_image",
                                        "version": "1.32",
                                        "platform": "gpu-platform",
                                        "preset": "8gpu",
                                        "os": "ubuntu22.04",
                                        "gpu_stack_preset": "cuda12.8",
                                    },
                                },
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="test-client",
            nebius=SimpleNamespace(project_id="project-1"),
        )
    )
    manifest = {"deploy": {"targets": []}}
    plan_stages: list[dict[str, str]] = []
    apply_stages: list[dict[str, str]] = []
    wait_calls: list[tuple[str, str]] = []
    completed_templates: dict[str, tuple[str, str, str]] = {}

    def _live_node_group(
        *,
        id: str,
        name: str,
        version: str,
        os: str,
        platform: str,
        preset: str,
        drivers_preset: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(id=id, name=name, resource_version=1),
            spec=SimpleNamespace(
                version=version,
                template=SimpleNamespace(
                    os=os,
                    resources=SimpleNamespace(platform=platform, preset=preset),
                    gpu_settings=SimpleNamespace(drivers_preset=drivers_preset),
                ),
            ),
            status=_mk8s_ready_status(version),
        )

    class FakeSdk:
        def sync_close(self) -> None:
            wait_calls.append(("sdk-close", ""))

    class FakeExecutor:
        def __init__(self, sdk: object) -> None:
            assert isinstance(sdk, FakeSdk)

        def get_cluster_by_name(self, *, project_id: str, name: str) -> SimpleNamespace:
            assert (project_id, name) == ("project-1", "mk8s-live")
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name=name, resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.32")),
            )

        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name="mk8s-live", resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.33")),
            )

        def control_plane_versions(self) -> tuple[str, ...]:
            return ("1.33",)

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            system_template = completed_templates.get(
                "ng-system",
                ("1.32", "ubuntu22.04", ""),
            )
            gpu_template = completed_templates.get(
                "ng-gpu",
                ("1.32", "ubuntu22.04", "cuda12.8"),
            )
            return (
                _live_node_group(
                    id="ng-system",
                    name="mk8s-live-system",
                    version=system_template[0],
                    os=system_template[1],
                    platform="cpu-platform",
                    preset="cpu-4-16",
                    drivers_preset=system_template[2],
                ),
                _live_node_group(
                    id="ng-gpu",
                    name="mk8s-live-gpu",
                    version=gpu_template[0],
                    os=gpu_template[1],
                    platform="gpu-platform",
                    preset="8gpu",
                    drivers_preset=gpu_template[2],
                ),
            )

        def compatibility_choices(self, *, target_version: str, platform: str):
            assert target_version == "1.33"
            return (
                SimpleNamespace(
                    platform=platform,
                    os="ubuntu24.04",
                    drivers_preset="cuda13.0" if platform == "gpu-platform" else "",
                ),
            )

        def wait_cluster_version(self, *, cluster_id: str, version: str) -> None:
            wait_calls.append(("control-plane", f"{cluster_id}:{version}"))

        def wait_node_group_node_template(
            self,
            *,
            cluster_id: str,
            node_group_id: str,
            version: str,
            os: str,
            drivers_preset: str | None,
            timeout_seconds: int,
        ) -> None:
            completed_templates[node_group_id] = (version, os, drivers_preset or "")
            wait_calls.append(
                (
                    "node-template",
                    f"{cluster_id}:{node_group_id}:{version}:{os}:{drivers_preset}:{timeout_seconds}",
                )
            )

    def _current_state() -> dict[str, str]:
        payload = yaml.safe_load(paths.config_path.read_text(encoding="utf-8"))
        component = payload["infra"]["components"][0]
        groups = component["inputs"]["node_groups"]
        return {
            "cluster": component["inputs"]["cluster"]["k8s_version"],
            "system_version": groups["system"]["version"],
            "system_os": groups["system"]["os"],
            "gpu_version": groups["gpu"]["version"],
            "gpu_os": groups["gpu"]["os"],
            "gpu_stack": groups["gpu"]["gpu_stack_preset"],
        }

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli, "_load_deploy_context", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda _manifest, *, target_instance_id: {
            "component_id": "mk8s",
            "target_ref": target_instance_id,
            "access": "external",
            "cluster_id_output_name": "cluster_id",
        },
    )
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_target_with_cluster_id",
        lambda target, *, cluster_id: {**target, "cluster_id": cluster_id},
    )
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: FakeSdk())
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "collect_kubernetes_preflight_findings", lambda *, kube_env: ())
    monkeypatch.setattr(cli, "_run_generated_bundle_validation", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "render_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_manifest_status_watchers", lambda _manifest: [])
    monkeypatch.setattr(cli, "_enabled_status_watcher_specs", lambda _config: [])
    monkeypatch.setattr(
        cli,
        "terraform_plan",
        lambda *_args, **_kwargs: plan_stages.append(_current_state()),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *_args, **_kwargs: apply_stages.append(_current_state()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [])

    with cli.console.capture() as capture:
        cli.upgrade_node_template_command(
            paths.config_path,
            "infra:mk8s@mk8s",
            to_version="1.33",
            to_os="ubuntu24.04",
            to_gpu_stack_preset="cuda13.0",
            node_group="mk8s-live-gpu",
            dry_run=False,
        )
    output = _plain_output(capture.get())

    expected_stages = [
        {
            "cluster": "1.33",
            "system_version": "1.32",
            "system_os": "ubuntu22.04",
            "gpu_version": "1.32",
            "gpu_os": "ubuntu22.04",
            "gpu_stack": "cuda12.8",
        },
        {
            "cluster": "1.33",
            "system_version": "1.32",
            "system_os": "ubuntu22.04",
            "gpu_version": "1.33",
            "gpu_os": "ubuntu24.04",
            "gpu_stack": "cuda13.0",
        },
        {
            "cluster": "1.33",
            "system_version": "1.32",
            "system_os": "ubuntu22.04",
            "gpu_version": "1.33",
            "gpu_os": "ubuntu24.04",
            "gpu_stack": "cuda13.0",
        },
    ]
    assert (
        "Node-template upgrade execution stages are per control-plane hop and per node group"
        in output
    )
    assert "not per node:" in output
    assert "1 control-plane stage(s)" in output
    assert "1 node-group template stage(s)" in output
    compact_output = " ".join(output.split())
    assert "control-plane upgrade to Kubernetes 1.33" in compact_output
    assert (
        "node-group mk8s-live-gpu node-template upgrade to Kubernetes 1.33, OS ubuntu24.04"
    ) in compact_output
    assert "mk8s-live-system node-template upgrade" not in output
    assert plan_stages == expected_stages
    assert apply_stages == expected_stages
    assert wait_calls == [
        ("control-plane", "cluster-1:1.33"),
        ("node-template", "cluster-1:ng-gpu:1.33:ubuntu24.04:cuda13.0:3600"),
        ("sdk-close", ""),
    ]
    payload = json.loads(
        (paths.reports_dir / cli.UPGRADE_NODE_TEMPLATE_REPORT_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert [group["name"] for group in payload["node_groups"]] == ["mk8s-live-gpu"]


def test_upgrade_node_template_resume_waits_and_restores_strategy_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": {
                                "cluster": {
                                    "cluster_name": "mk8s-live",
                                    "k8s_version": "1.33",
                                },
                                "node_groups": {
                                    "system": {
                                        "version": "1.33",
                                        "platform": "cpu-platform",
                                        "preset": "cpu-4-16",
                                        "os": "ubuntu24.04",
                                    },
                                },
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="test-client",
            nebius=SimpleNamespace(project_id="project-1"),
        )
    )
    manifest = {"deploy": {"targets": [_mk8s_target(paths)]}}
    calls: list[object] = []
    live_status = {
        "value": SimpleNamespace(
            version="1.33",
            ready_node_count=0,
            target_node_count=1,
            node_count=2,
            outdated_node_count=1,
            reconciling=True,
        )
    }

    class FakeSdk:
        def sync_close(self) -> None:
            calls.append("sdk-close")

    class FakeExecutor:
        def __init__(self, sdk: object) -> None:
            assert isinstance(sdk, FakeSdk)

        def get_cluster_by_name(self, *, project_id: str, name: str) -> SimpleNamespace:
            assert (project_id, name) == ("project-1", "mk8s-live")
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name=name, resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.33")),
            )

        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return SimpleNamespace(
                metadata=SimpleNamespace(id="cluster-1", name="mk8s-live", resource_version=1),
                spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.33")),
            )

        def control_plane_versions(self) -> tuple[str, ...]:
            return ("1.33",)

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        id="ng-system",
                        name="mk8s-live-system",
                        resource_version=1,
                    ),
                    spec=SimpleNamespace(
                        version="1.33",
                        template=SimpleNamespace(
                            os="ubuntu24.04",
                            resources=SimpleNamespace(
                                platform="cpu-platform",
                                preset="cpu-4-16",
                            ),
                            gpu_settings=SimpleNamespace(drivers_preset=""),
                        ),
                    ),
                    status=live_status["value"],
                ),
            )

        def compatibility_choices(self, *, target_version: str, platform: str):
            assert target_version == "1.33"
            return (SimpleNamespace(platform=platform, os="ubuntu24.04", drivers_preset=""),)

        def wait_node_group_node_template(
            self,
            *,
            cluster_id: str,
            node_group_id: str,
            version: str,
            os: str,
            drivers_preset: str | None,
            timeout_seconds: int,
        ) -> None:
            live_status["value"] = _mk8s_ready_status(version=version)
            calls.append(
                (
                    "wait-node-template",
                    f"{cluster_id}:{node_group_id}:{version}:{os}:{drivers_preset}:{timeout_seconds}",
                )
            )

    def _current_state() -> dict[str, str]:
        payload = yaml.safe_load(paths.config_path.read_text(encoding="utf-8"))
        group = payload["infra"]["components"][0]["inputs"]["node_groups"]["system"]
        return {
            "version": group["version"],
            "os": group["os"],
        }

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli, "_load_deploy_context", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda _manifest, *, target_instance_id: {
            "component_id": "mk8s",
            "target_ref": target_instance_id,
            "access": "external",
            "cluster_id_output_name": "cluster_id",
        },
    )
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_target_with_cluster_id",
        lambda target, *, cluster_id: {**target, "cluster_id": cluster_id},
    )
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: FakeSdk())
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "collect_kubernetes_preflight_findings", lambda *, kube_env: ())
    monkeypatch.setattr(
        cli,
        "_run_generated_bundle_validation",
        lambda *_args, **kwargs: calls.append(("validate", kwargs["title"])) or {},
    )
    monkeypatch.setattr(
        cli, "render_command", lambda *_args, **_kwargs: calls.append(("render", _current_state()))
    )
    monkeypatch.setattr(cli, "_manifest_status_watchers", lambda _manifest: [])
    monkeypatch.setattr(cli, "_enabled_status_watcher_specs", lambda _config: [])
    monkeypatch.setattr(
        cli, "terraform_plan", lambda *_args, **_kwargs: calls.append(("plan", _current_state()))
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *_args, **_kwargs: calls.append(("apply", _current_state())),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [])

    with cli.console.capture() as capture:
        cli.upgrade_node_template_command(
            paths.config_path,
            "infra:mk8s@mk8s",
            to_version="1.33",
            to_os="ubuntu24.04",
            disruption_policy="zero-surge",
            dry_run=False,
        )
    flat_output = " ".join(_plain_output(capture.get()).split())

    assert "1 strategy-restore stage" in flat_output
    assert calls == [
        ("validate", "Node-template upgrade preflight"),
        ("wait-node-template", "cluster-1:ng-system:1.33:ubuntu24.04:None:3600"),
        ("render", {"version": "1.33", "os": "ubuntu24.04"}),
        ("validate", "Validate rendered stage 1/1: node-group strategy restore"),
        ("plan", {"version": "1.33", "os": "ubuntu24.04"}),
        ("apply", {"version": "1.33", "os": "ubuntu24.04"}),
        "sdk-close",
    ]


def test_node_template_target_choices_do_not_label_endpoint_access() -> None:
    manifest = {
        "deploy": {
            "targets": [
                {
                    "component_id": "mk8s",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "access": "external",
                    "cluster_id_output_name": "cluster1_cluster_id",
                    "component_output_ref": "cluster1.cluster_id",
                    "flux_dir": "generated/flux/targets/cluster1",
                },
                {
                    "component_id": "mk8s",
                    "instance_id": "private-cluster",
                    "target_ref": "private-cluster",
                    "access": "internal",
                    "cluster_id_output_name": "private_cluster_cluster_id",
                    "component_output_ref": "private-cluster.cluster_id",
                    "flux_dir": "generated/flux/targets/private-cluster",
                },
                {
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "component_id": "external-mk8s",
                    "instance_id": "onboarded",
                    "target_ref": "onboarded",
                    "access": "external",
                    "cluster_id": "mk8scluster-external",
                    "flux_dir": "generated/flux/targets/onboarded",
                },
            ]
        }
    }

    choices = cli._managed_mk8s_upgrade_target_choices(manifest)

    assert [(choice.value, choice.label, choice.recommended) for choice in choices] == [
        ("infra:mk8s@cluster1", "infra:mk8s@cluster1", True),
        ("infra:mk8s@private-cluster", "infra:mk8s@private-cluster", False),
    ]


def test_node_template_version_choices_explain_sequential_minor_policy() -> None:
    choices = cli._node_template_version_choices(
        current_version="1.31",
        supported_versions=("1.31.9", "1.32.4", "1.33.1"),
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in choices] == [
        (
            "1.31",
            "1.31  (current/live; reconcile source and generated state)",
            False,
        ),
        (
            "1.32",
            "1.32  (next supported minor; upstream Kubernetes does not support skipped minors)",
            True,
        ),
    ]


def test_upgrade_helm_chart_apply_updates_source_and_runs_target_flux_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text(
        yaml.safe_dump(
            {
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
                            "id": "cert-manager",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "namespace": "cert-manager",
                            "release-name": "cert-manager",
                            "version": "0.25.0",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    generated_config = SimpleNamespace()
    manifest: dict[str, Any] = {"deploy": {"targets": [_mk8s_target(paths)]}}
    calls: list[object] = []

    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli, "_load_deploy_context", lambda _path: (generated_config, paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_run_generated_bundle_validation",
        lambda *_args, **kwargs: calls.append(("validate", kwargs["title"])) or {},
    )
    monkeypatch.setattr(cli, "render_command", lambda *_args, **_kwargs: calls.append("render"))
    monkeypatch.setattr(
        cli,
        "flux_apply_command",
        lambda *args, **kwargs: calls.append(("flux-apply", args, kwargs)),
    )
    monkeypatch.setattr(
        cli,
        "_verify_helm_chart_upgrade_ready",
        lambda *_args, **_kwargs: calls.append("helm-ready"),
    )

    cli.upgrade_helm_chart_command(
        paths.config_path,
        "apps:cert-manager@mk8s",
        to_version="0.26.0",
    )

    payload = yaml.safe_load(paths.config_path.read_text(encoding="utf-8"))
    assert payload["apps"]["charts"][0]["version"] == "0.26.0"
    assert calls == [
        ("validate", "Helm chart upgrade preflight"),
        "render",
        ("validate", "Validate rendered Helm chart upgrade to 0.26.0"),
        (
            "flux-apply",
            (paths.generated_dir,),
            {"target_ref": "mk8s", "all_targets": False},
        ),
        "helm-ready",
    ]


def test_format_helm_chart_upgrade_plan_warns_when_target_appears_lower() -> None:
    plan = cli._HelmChartUpgradePlan(
        target=cli._HelmChartUpgradeTarget(
            selector="apps:soperator@mk8s",
            chart_id="soperator",
            target_ref="mk8s",
        ),
        instance_id="mk8s",
        namespace="soperator",
        release_name="soperator",
        current_version="4.0.2-ps.1",
        target_version="4.0.1-ps.2",
    )

    rendered = "\n".join(cli._format_helm_chart_upgrade_plan(plan, dry_run=True))

    assert "- warnings:" in rendered
    assert "requested chart version appears lower" in rendered
    assert "Helm chart downgrades are not guaranteed safe" in rendered


def test_upgrade_helm_chart_readiness_requires_generated_target(
    tmp_path: Path,
) -> None:
    paths = _fake_paths(tmp_path)
    plan = cli._HelmChartUpgradePlan(
        target=cli._HelmChartUpgradeTarget(
            selector="apps:soperator@missing",
            chart_id="soperator",
            target_ref="missing",
        ),
        instance_id="missing",
        namespace="soperator",
        release_name="soperator",
        current_version="0.25.0",
        target_version="0.26.0",
    )

    with pytest.raises(RuntimeError, match="No built-in cluster targets"):
        cli._verify_helm_chart_upgrade_ready(
            SimpleNamespace(),
            paths,
            {"deploy": {"targets": []}},
            plan,
        )


def test_resolve_managed_mk8s_upgrade_target_uses_shared_upgrade_wording() -> None:
    external_manifest = {
        "deploy": {
            "targets": [
                {
                    "target_ref": "mk8s",
                    "instance_id": "mk8s",
                    "kind": "external-mk8s",
                    "component_id": "external-mk8s",
                    "access": "external",
                    "flux_dir": "flux/mk8s",
                    "cluster_id": "cluster-1",
                }
            ]
        }
    }
    with pytest.raises(RuntimeError) as external_error:
        cli._resolve_managed_mk8s_upgrade_target(
            external_manifest,
            target_instance_id="mk8s",
        )
    assert "upgrade v1 supports Terraform-managed infra:mk8s targets only" in str(
        external_error.value
    )

    wrong_component_manifest = {
        "deploy": {
            "targets": [
                {
                    "target_ref": "vpc",
                    "instance_id": "vpc",
                    "component_id": "vpc",
                    "access": "external",
                    "cluster_id_output_name": "cluster_id",
                    "component_output_ref": "infra.vpc.cluster_id",
                    "flux_dir": "flux/vpc",
                }
            ]
        }
    }
    with pytest.raises(RuntimeError) as component_error:
        cli._resolve_managed_mk8s_upgrade_target(
            wrong_component_manifest,
            target_instance_id="vpc",
        )
    assert "MK8s upgrade commands require a generated infra:mk8s target" in str(
        component_error.value
    )


@pytest.mark.parametrize(
    "command",
    [
        "gpu-" + "fabric",
        "gpu-" + "stack-preset",
        "platform",
        "cpu-" + "preset",
        "gpu-" + "preset",
    ],
)
def test_removed_upgrade_commands_are_not_registered(tmp_path: Path, command: str) -> None:
    result = runner.invoke(
        cli.app,
        [
            "upgrade",
            command,
            str(tmp_path / "missing-config.yaml"),
            "--no-interactive",
        ],
    )

    assert result.exit_code != 0
    assert "No such command" in _plain_output(result.output)
    assert "Config file not found" not in result.output


def test_migrate_node_group_accepts_to_gpu_stack_preset_flag_name(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "migrate",
            "node-group",
            str(tmp_path / "missing-config.yaml"),
            "infra:mk8s@mk8s",
            "--node-group",
            "worker",
            "--to-gpu-stack-preset",
            "cuda13.0",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "Config file not found" in result.output
    assert "No such option" not in _plain_output(result.output)


def test_upgrade_helm_chart_no_interactive_requires_explicit_target_and_version(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        cli.app,
        [
            "upgrade",
            "helm-chart",
            str(tmp_path / "missing-config.yaml"),
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1
    assert "Missing target selector and --to-version <chart-version>" in result.output
    assert "Config file not found" not in result.output


def _mk8s_target(paths: ProjectPaths, *, target_ref: str = "mk8s") -> dict[str, str]:
    return {
        "component_id": "mk8s",
        "ownership": "managed",
        "instance_id": target_ref,
        "target_ref": target_ref,
        "cluster_id_output_name": f"{target_ref.replace('-', '_')}_cluster_id",
        "component_output_ref": f"{target_ref}.cluster_id",
        "access": "external",
        "flux_dir": str(flux_target_dir(paths, target_ref)),
    }


def _external_mk8s_target(paths: ProjectPaths, *, target_ref: str = "mk8s") -> dict[str, str]:
    return {
        "kind": "external-mk8s",
        "ownership": "external",
        "component_id": "external-mk8s",
        "instance_id": target_ref,
        "target_ref": target_ref,
        "access": "external",
        "kube_context": f"nebius-{target_ref}-mk8scluster-123-external",
        "flux_dir": str(flux_target_dir(paths, target_ref)),
    }


def _target_paths(paths: ProjectPaths, *, target_ref: str = "mk8s") -> ProjectPaths:
    return replace(paths, flux_dir=flux_target_dir(paths, target_ref))


def test_manifest_target_flux_dir_requires_exact_canonical_target_path(
    tmp_path: Path,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    target = _mk8s_target(fake_paths, target_ref="cluster1")
    assert (
        cli._manifest_target_flux_dir(paths=fake_paths, target=target)
        == flux_target_dir(
            fake_paths,
            "cluster1",
        ).resolve()
    )

    target["flux_dir"] = str(flux_target_dir(fake_paths, "cluster2"))
    with pytest.raises(RuntimeError, match="canonical target ownership"):
        cli._manifest_target_flux_dir(paths=fake_paths, target=target)


def test_manifest_deploy_targets_require_internal_target_ref_to_match_instance_id(
    tmp_path: Path,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    target = _mk8s_target(fake_paths, target_ref="cluster1")
    target["target_ref"] = "cluster2"

    with pytest.raises(
        ValueError,
        match=(
            r"Generated manifest deploy\.targets\[0\]\.target_ref "
            r"must equal instance_id 'cluster1'"
        ),
    ):
        cli._manifest_deploy_targets({"deploy": {"targets": [target]}})


def test_manifest_deploy_targets_reject_legacy_target_shape(tmp_path: Path) -> None:
    fake_paths = _fake_paths(tmp_path)
    target = _mk8s_target(fake_paths, target_ref="cluster1")
    target.pop("target_ref")

    with pytest.raises(
        ValueError,
        match=r"Generated manifest deploy\.targets\[0\]\.target_ref is required",
    ):
        cli._manifest_deploy_targets({"deploy": {"targets": [target]}})


def test_manifest_deploy_targets_reject_malformed_target_rows() -> None:
    with pytest.raises(
        ValueError,
        match=r"Generated manifest deploy\.targets\[0\] must be a mapping",
    ):
        cli._manifest_deploy_targets({"deploy": {"targets": ["cluster1"]}})


def test_manifest_deploy_targets_accept_external_mk8s_target(tmp_path: Path) -> None:
    fake_paths = _fake_paths(tmp_path)
    target = _external_mk8s_target(fake_paths, target_ref="cluster1")

    assert cli._manifest_deploy_targets({"deploy": {"targets": [target]}}) == [target]


def test_render_overwrite_warning_never_mentions_flux_system(tmp_path: Path) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.generated_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.generated_dir / "dummy.txt").write_text("data\n", encoding="utf-8")

    warning_without_bootstrap = cli._render_overwrite_warning(fake_paths)

    assert warning_without_bootstrap is not None
    assert "Render will replace existing generated artifacts under" in warning_without_bootstrap
    assert "referenced JSON detail files" in warning_without_bootstrap
    assert "generated/flux/flux-system" not in warning_without_bootstrap

    bootstrap_dir = fake_paths.flux_dir / "flux-system"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "gotk-sync.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8"
    )

    warning_with_bootstrap = cli._render_overwrite_warning(fake_paths)

    assert warning_with_bootstrap is not None
    assert "generated/flux/flux-system" not in warning_with_bootstrap


def test_render_overwrite_warning_ignores_preserved_lifecycle_reports(
    tmp_path: Path,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.reports_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.reports_dir / cli.DEPLOY_REPORT_FILENAME).write_text(
        "# Deploy Report\n\nGenerated by `nebius-cxcli deploy`.\n",
        encoding="utf-8",
    )
    discovery_report = (
        fake_paths.reports_dir / "soperator-clusters" / "external-context" / "discovery"
    )
    discovery_report.mkdir(parents=True, exist_ok=True)
    (discovery_report / "manifest.json").write_text('{"schema": "discovery"}\n', encoding="utf-8")

    warning = cli._render_overwrite_warning(fake_paths)

    assert warning is None


def test_render_overwrite_warning_treats_unknown_reports_as_replaceable(
    tmp_path: Path,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.reports_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.reports_dir / "old.json").write_text("{}\n", encoding="utf-8")

    warning = cli._render_overwrite_warning(fake_paths)

    assert warning is not None
    assert "Render will replace existing generated artifacts under" in warning
    assert "lifecycle reports under `generated/reports/`" in warning


def test_validate_command_runs_strict_checks_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    strict_called: dict[str, bool] = {"called": False}
    quota_called: dict[str, Any] = {}
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
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
    monkeypatch.setattr(cli, "validate_vpc_networking_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_config_live_quota_issues",
        lambda config, paths, *, phase: (
            quota_called.update({"config": config, "paths": paths, "phase": phase})
            or _empty_quota_report()
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
    output = " ".join(_plain_output(result.output).split())
    assert "Runtime validation:" in output
    assert "Load config and component catalog" in output
    assert "Validate active component catalog/settings" in output
    assert "Validate component dependencies" in output
    assert "Validate Terraform module inputs" in output
    assert "Validate strict deployment readiness" in output
    assert "Validate VPC networking preflight" in output
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
    assert quota_called["paths"] == fake_paths
    assert quota_called["phase"] == "validate"


def test_validate_command_rejects_removed_strict_flag(
    tmp_path: Path,
) -> None:
    result = runner.invoke(cli.app, ["validate", "--strict", str(tmp_path / "config.yaml")])
    help_result = runner.invoke(cli.app, ["validate", "--help"])

    output = _plain_output(result.output)
    help_output = _plain_output(help_result.output)

    assert result.exit_code == 2
    assert "Usage: root validate [OPTIONS] CONFIG_YAML" in output
    assert "Config file not found" not in output
    assert help_result.exit_code == 0
    assert "--strict" not in help_output


def test_strict_config_runs_builtin_mk8s_gpu_runtime_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "worker": {
                                "gpu": True,
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                                "gpu_cluster_key": "workers",
                                "node_count": 1,
                                "reservation": {"policy": "FORBID"},
                            }
                        },
                        "gpu_clusters": {"workers": {}},
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }
    monkeypatch.setattr(cli, "materialize_compute_boot_disk_defaults", lambda _payload: None)
    for name in (
        "_required_enabled_infra_field_issues",
        "_binding_conflict_issues",
        "_jump_host_public_ip_allocation_issues",
        "_compute_boot_disk_security_issues",
        "_compute_data_disk_security_issues",
        "nfs_csi_binding_issues",
        "_enabled_custom_module_input_schema_issues",
        "_placeholder_value_issues",
    ):
        monkeypatch.setattr(cli, name, lambda *_args, **_kwargs: [])

    with pytest.raises(
        RuntimeError,
        match=(
            "GPU node_groups entry 'worker' references gpu_cluster_key 'workers', "
            "but inputs.gpu_clusters.workers.infiniband_fabric is missing"
        ),
    ):
        cli._validate_strict_config(payload, include_common_checks=False)


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
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), _fake_paths(tmp_path)))
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
    monkeypatch.setattr(cli, "validate_vpc_networking_preflight", lambda _cfg: None)
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "rendered_module_sources", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "_raise_on_config_live_quota_issues",
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
    lines = output.splitlines()
    assert "Valid with quota warnings:" in output
    assert "nebius-cxcli quota-request" in output
    assert f"nebius-cxcli quota-request {tmp_path / 'config.yaml'}" in lines


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
    monkeypatch.setattr(cli, "validate_vpc_networking_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_config_live_quota_issues",
        lambda *_args, **_kwargs: _empty_quota_report(),
    )
    monkeypatch.setattr(cli, "_validation_scope_summary_lines", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "mk8s_gpu_validation_warnings",
        lambda _config: (
            "deploy.targets[].deployment_testing.mk8s_gpu.gpu_visibility.max_nodes is high for deploy-time testing.",
        ),
    )

    result = runner.invoke(cli.app, ["validate", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Deploy validation warning:" in output
    assert "deployment_testing.mk8s_gpu.gpu_visibility.max_nodes" in output


def test_validate_command_accepts_local_source_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

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
    monkeypatch.setattr(cli, "validate_vpc_networking_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_config_live_quota_issues",
        lambda *_args, **_kwargs: _empty_quota_report(),
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
    captured: dict[str, Any] = {}

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
                        "set inputs.node_groups.<cpu-group>.boot_disk.type and "
                        "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
                    ),
                ),
                QuotaCoverageGap(
                    component_id="mk8s",
                    instance_id="mk8s",
                    component_label="mk8s",
                    message=(
                        "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
                        "set inputs.node_groups.<gpu-group>.boot_disk.type and "
                        "inputs.node_groups.<gpu-group>.boot_disk.size_gibibytes"
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
        "set inputs.node_groups.<cpu-group>.boot_disk.type and "
        "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
    ) in collapsed_output
    assert (
        "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
        "set inputs.node_groups.<gpu-group>.boot_disk.type and "
        "inputs.node_groups.<gpu-group>.boot_disk.size_gibibytes"
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
    lines = plain_output.splitlines()
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
    assert f"nebius-cxcli quota-request {fake_paths.config_path}" in lines
    assert f"nebius-cxcli quota-check --all-regions {fake_paths.config_path}" in lines
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
                        "(regular-vm slots, fabric fabric-4, converted to GPU units)"
                    ),
                    contributors=(),
                ),
            ),
        ),
    )

    result = runner.invoke(cli.app, ["quota-check", str(tmp_path / "config.yaml")])

    assert result.exit_code != 0
    plain_output = _plain_output(result.output)
    lines = plain_output.splitlines()
    output = " ".join(plain_output.split())
    assert "nebius-cxcli quota-request" not in output
    assert not any(line.startswith("nebius-cxcli quota-request ") for line in lines)
    assert (
        "choose a GPU platform/preset/fabric or region with available Capacity Dashboard capacity"
    ) in output
    assert "nebius-cxcli quota-check --all-regions" in output
    assert f"nebius-cxcli quota-check --all-regions {fake_paths.config_path}" in lines


def test_quota_request_discounts_existing_mk8s_state_for_day2_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cli.manifest_path_for_generated_dir(fake_paths.generated_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{}", encoding="utf-8")
    captured: dict[str, Any] = {}
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
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_ensure_backend_s3_env_aliases", lambda: None)
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


def test_validate_config_quota_gate_discounts_existing_mk8s_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cli.manifest_path_for_generated_dir(fake_paths.generated_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{}", encoding="utf-8")
    live_report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-28T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="cluster1",
                component_label="mk8s@cluster1",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=16,
                reason="mk8s@cluster1: 2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                unit="count",
                available=0,
                sufficient=False,
                tenant_limit=16,
                tenant_usage=16,
                project_limit=None,
                project_usage=0,
                source_scope="capacity-dashboard/on-demand",
                description="Capacity Dashboard GPU availability",
                contributors=(
                    cli.QuotaContributor(
                        component_id="mk8s",
                        instance_id="cluster1",
                        component_label="mk8s@cluster1",
                        required=16,
                        reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                    ),
                ),
            ),
        ),
    )

    monkeypatch.setattr(cli, "assess_live_quotas", lambda *_args, **_kwargs: live_report)
    monkeypatch.setattr(cli, "load_generated_manifest", lambda _generated_dir: {})
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_ensure_backend_s3_env_aliases", lambda: None)
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {"TF_VAR_demo": "1"})
    monkeypatch.setattr(cli, "terraform_init", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_managed_mk8s_quota_requirements_from_terraform_state",
        lambda *_args, **_kwargs: (
            SimpleNamespace(
                component_id="mk8s",
                instance_id="cluster1",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=16,
            ),
        ),
    )

    with cli.console.capture() as capture:
        adjusted = cli._raise_on_config_live_quota_issues(
            "cfg",
            fake_paths,
            phase="validate",
        )

    assert adjusted.checks == ()
    assert adjusted.has_confirmed_insufficiency is False
    output = " ".join(_plain_output(capture.get()).split())
    assert "compute.instance.gpu.h100 requires 16, available 0" not in output


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
    submitted: dict[str, Any] = {}

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
                    "set inputs.node_groups.<gpu-group>.boot_disk.type and "
                    "inputs.node_groups.<gpu-group>.boot_disk.size_gibibytes"
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
    flux_version, terraform_version = _bundled_tool_versions()
    fake_manifest = {
        "tools": {
            "flux_version": flux_version,
            "terraform_version": terraform_version,
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
    assert os.environ[FLUX_VERSION_ENV] == flux_version
    assert os.environ[TERRAFORM_VERSION_ENV] == terraform_version
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
    captured: dict[str, Any] = {}

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
    calls: dict[str, Any] = {}

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
    lines = _plain_output(result.output).splitlines()
    assert lines[-2:] == [
        "Next step: deploy the rendered bundle:",
        f"nebius-cxcli deploy {str((tmp_path / 'config.yaml').resolve())}",
    ]
    assert calls["terraform_config"] == "cfg"
    assert calls["terraform_profile"] == SourceProfile.PORTABLE
    assert calls["outputs_config"] == "cfg"
    assert calls["outputs_paths"] == fake_paths
    assert calls["flux_config"] == "cfg"
    assert calls["flux_outputs"] == {}
    assert calls["manifest_config"] == "cfg"
    assert calls["manifest_profile"] == SourceProfile.PORTABLE
    assert calls["lock_config"] == "cfg"
    assert calls["lock_paths"] == fake_paths

    staged_paths = calls["terraform_paths"]
    assert isinstance(staged_paths, ProjectPaths)
    assert staged_paths.generated_dir.name.startswith(".generated-staging-")
    assert calls["flux_paths"] == staged_paths
    assert calls["manifest_paths"] == staged_paths
    assert calls["manifest_kwargs"]["manifest_paths"] == fake_paths
    assert calls["manifest_kwargs"]["output_path"] == (
        staged_paths.generated_dir / "nebius-cxcli-manifest.json"
    )


def test_internal_render_command_suppresses_deploy_hint_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Path, bool, bool]] = []

    def fake_render_command(config_path: Path, *, force: bool) -> None:
        observed.append((config_path, force, cli._RENDER_DEPLOY_HINT_SUPPRESSED.get()))

    monkeypatch.setattr(cli, "render_command", fake_render_command)

    cli._run_internal_render_command(tmp_path / "config.yaml", force=True)

    assert observed == [(tmp_path / "config.yaml", True, True)]
    assert cli._RENDER_DEPLOY_HINT_SUPPRESSED.get() is False


def test_render_command_persists_quota_report_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
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
                    "set inputs.node_groups.<cpu-group>.boot_disk.type and "
                    "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
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
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)

    def fake_warn_on_config_live_quota_issues(
        config: object,
        paths: ProjectPaths,
        *,
        phase: str,
        all_regions: bool = False,
    ) -> QuotaReport:
        captured["quota_config"] = config
        captured["quota_paths"] = paths
        captured["quota_phase"] = phase
        captured["quota_all_regions"] = all_regions
        cli._print_live_quota_report(report, phase=phase)
        return report

    monkeypatch.setattr(
        cli,
        "_warn_on_config_live_quota_issues",
        fake_warn_on_config_live_quota_issues,
    )
    monkeypatch.setattr(
        cli,
        "_warn_on_live_quota_issues",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("render must use config-aware quota assessment")
        ),
    )
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
    assert captured["quota_config"] == "cfg"
    assert captured["quota_paths"] == fake_paths
    assert captured["quota_phase"] == "render"
    assert captured["quota_all_regions"] is False
    assert captured["quota_report"] is report
    assert "Render completed with quota warnings." in _plain_output(result.output)
    assert "compute.instance.count requires 1, available 0" in _plain_output(result.output)
    assert "boot-disk quota could not be fully evaluated" not in _plain_output(result.output)
    assert _plain_output(result.output).splitlines()[-2:] == [
        "Next step: deploy the rendered bundle:",
        f"nebius-cxcli deploy {str((tmp_path / 'config.yaml').resolve())}",
    ]


def test_render_command_accepts_local_source_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: dict[str, Any] = {}

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
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "_load_generated_context",
        lambda _path: (
            _config_with_enabled_mk8s(),
            fake_paths,
            {"render": {"module_sources": []}},
        ),
    )
    monkeypatch.setattr(cli, "_validate_strict_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "validate_vpc_networking_preflight", lambda _cfg: None)
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
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda config: None)
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
    assert "Validate VPC networking preflight" in output
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
    monkeypatch.setattr(cli, "validate_vpc_networking_preflight", lambda _cfg: None)
    monkeypatch.setattr(
        cli,
        "_raise_on_generated_bundle_live_quota_issues",
        lambda *_args, **_kwargs: _empty_quota_report(),
    )
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda config: None)
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
    assert "Component catalog/settings valid:" in output
    assert str(sources_file) in normalized_output


def test_generic_catalog_soperator_latest_is_rejected() -> None:
    version, issue = cli._catalog_chart_validation_version(
        configured_version="latest",
    )

    assert version == ""
    assert issue is not None
    assert "exact immutable chart version" in issue


def test_catalog_non_soperator_latest_is_rejected() -> None:
    version, issue = cli._catalog_chart_validation_version(
        configured_version="latest",
    )

    assert version == ""
    assert issue is not None
    assert "exact immutable chart version" in issue


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
    assert "Component catalog/settings validation failed for" in output
    assert str(sources_file) in normalized_output
    assert "module source './broken-module' does not resolve to an existing directory" in output
    assert "checks the full component catalog" in output
    assert "NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS" in output
    assert "--no-validate-sources" not in output


def test_create_source_validation_failure_reports_skip_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    monkeypatch.setattr(
        cli,
        "_validate_component_sources_registry",
        lambda explicit=None, progress_callback=None: (
            sources_file,
            ["could not be resolved by helm: operation timed out"],
            [],
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        cli._validate_component_sources_or_raise()

    message = str(exc_info.value)
    assert "checks the full component catalog" in message
    assert "NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS" in message
    assert "--no-validate-sources" in message


def test_validate_sources_command_accepts_positional_component_sources_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = SimpleNamespace(
        tf_modules=[SimpleNamespace(module="mk8s")],
        helm_charts=[],
    )
    sources_file = tmp_path / "component_sources.yaml"
    captured: dict[str, Any] = {}

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


def test_grafana_command_exports_selected_dashboard_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "dashboards"
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "bearer_auth_candidates",
        lambda **_kwargs: [cli.GrafanaAuth(kind="bearer", value="token", source="test")],
    )
    monkeypatch.setattr(
        cli,
        "list_folders",
        lambda base_url, auth_candidates: (
            captured.__setitem__("folder_base_url", base_url),
            captured.__setitem__("folder_auth_sources", [item.source for item in auth_candidates]),
            (cli.GrafanaFolder(uid="folder-uid", title="mk8s"),),
        )[2],
    )
    monkeypatch.setattr(
        cli,
        "list_dashboards",
        lambda base_url, auth_candidates, *, folder_uid, folder_title="": (
            captured.__setitem__("dashboard_base_url", base_url),
            captured.__setitem__("dashboard_folder_uid", folder_uid),
            captured.__setitem__("dashboard_folder_title", folder_title),
            (
                cli.GrafanaDashboard(
                    uid="dashboard-uid",
                    title="Cluster Autoscaler",
                    folder_uid=folder_uid,
                    folder_title=folder_title,
                ),
            ),
        )[3],
    )
    monkeypatch.setattr(
        cli,
        "dashboard_json",
        lambda base_url, auth_candidates, *, dashboard_uid: (
            captured.__setitem__("detail_base_url", base_url),
            captured.__setitem__("detail_dashboard_uid", dashboard_uid),
            {
                "uid": dashboard_uid,
                "title": "Cluster Autoscaler",
                "panels": [{"datasource": {"type": "prometheus", "uid": "source"}}],
            },
        )[2],
    )

    result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--export-dashboard",
            "https://grafana.example/dashboards/f/folder-uid/mk8s",
            "--dashboard-uid",
            "dashboard-uid",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    exported = output_dir / "mk8s" / "cluster-autoscaler.json"
    assert json.loads(exported.read_text(encoding="utf-8")) == {
        "uid": "dashboard-uid",
        "title": "Cluster Autoscaler",
        "panels": [{"datasource": {"type": "prometheus", "uid": "source"}}],
    }
    assert captured["folder_base_url"] == "https://grafana.example/"
    assert captured["folder_auth_sources"] == ["test"]
    assert captured["dashboard_folder_uid"] == "folder-uid"
    assert captured["detail_dashboard_uid"] == "dashboard-uid"
    assert "Exported Cluster Autoscaler" in _plain_output(result.output)


def test_grafana_command_api_export_with_attach_rewrites_and_attaches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "dashboards"
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text("components:\n  apps: {}\n", encoding="utf-8")
    captured: dict[str, Any] = {"detail_uids": []}

    monkeypatch.setattr(
        cli,
        "bearer_auth_candidates",
        lambda **_kwargs: [cli.GrafanaAuth(kind="bearer", value="token", source="test")],
    )
    monkeypatch.setattr(
        cli,
        "list_folders",
        lambda _base_url, _auth_candidates: (cli.GrafanaFolder(uid="folder-uid", title="mk8s"),),
    )
    monkeypatch.setattr(
        cli,
        "list_dashboards",
        lambda _base_url, _auth_candidates, *, folder_uid, folder_title="": (
            cli.GrafanaDashboard(
                uid="dashboard-one",
                title="Cluster",
                folder_uid=folder_uid,
                folder_title=folder_title,
            ),
            cli.GrafanaDashboard(
                uid="dashboard-two",
                title="Nodes",
                folder_uid=folder_uid,
                folder_title=folder_title,
            ),
        ),
    )

    def fake_dashboard_json(
        _base_url: str,
        _auth_candidates: object,
        *,
        dashboard_uid: str,
    ) -> dict[str, Any]:
        cast(list[str], captured["detail_uids"]).append(dashboard_uid)
        title = "Cluster" if dashboard_uid == "dashboard-one" else "Nodes"
        return {
            "uid": dashboard_uid,
            "title": title,
            "panels": [{"datasource": {"type": "prometheus", "uid": "source-prometheus"}}],
        }

    monkeypatch.setattr(cli, "dashboard_json", fake_dashboard_json)
    monkeypatch.setattr(
        cli,
        "resolve_component_sources_file",
        lambda *, explicit=None: explicit or catalog_path,
    )
    monkeypatch.setattr(
        cli,
        "catalog_datasources",
        lambda _path: (
            "grafana",
            (
                cli.CatalogDatasource(
                    name="Nebius User Metrics",
                    uid="nebius-user-metrics",
                    datasource_type="prometheus",
                ),
            ),
        ),
    )

    def fake_attach_dashboards_to_catalog(
        component_sources_path: Path,
        *,
        grafana_component_id: str,
        exports: Any,
        overwrite: bool,
    ) -> None:
        captured["attach_path"] = component_sources_path
        captured["grafana_component_id"] = grafana_component_id
        captured["exports"] = tuple(exports)
        captured["overwrite"] = overwrite

    monkeypatch.setattr(cli, "attach_dashboards_to_catalog", fake_attach_dashboards_to_catalog)

    result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--export-dashboard",
            "https://grafana.example/",
            "--folder-uid",
            "folder-uid",
            "--dashboard-uid",
            "dashboard-one,dashboard-two",
            "--output-dir",
            str(output_dir),
            "--attach",
            "--component-sources",
            str(catalog_path),
            "--dashboard-folder",
            "mk8s",
            "--datasource",
            "Nebius User Metrics",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["detail_uids"] == ["dashboard-one", "dashboard-two"]
    assert json.loads((output_dir / "mk8s" / "cluster.json").read_text(encoding="utf-8")) == {
        "uid": "dashboard-one",
        "title": "Cluster",
        "panels": [{"datasource": {"type": "prometheus", "uid": "nebius-user-metrics"}}],
    }
    assert captured["attach_path"] == catalog_path
    assert captured["grafana_component_id"] == "grafana"
    assert captured["overwrite"] is False
    exports = cast(tuple[cli.ExportedDashboard, ...], captured["exports"])
    assert [export.dashboard_key for export in exports] == ["cluster", "nodes"]
    assert all(export.catalog_folder == "mk8s" for export in exports)
    assert all(export.datasource_name == "Nebius User Metrics" for export in exports)
    assert "Attached 2 dashboard(s)" in _plain_output(result.output)


def test_grafana_export_url_parts_parse_dashboard_urls_and_uid_lists() -> None:
    base_url, folder_uid, dashboard_uids = cli._grafana_export_url_parts(
        "https://grafana.example/d/dashboard-uid/title?orgId=1",
        folder_uid="",
        dashboard_uids=(),
    )

    assert base_url == "https://grafana.example/"
    assert folder_uid == ""
    assert dashboard_uids == ("dashboard-uid",)

    requested_uids = tuple(cli._split_multi_value_tokens(["first,second", "third"]))
    base_url, folder_uid, dashboard_uids = cli._grafana_export_url_parts(
        "https://grafana.example/dashboards/f/folder-uid/mk8s",
        folder_uid="explicit-folder",
        dashboard_uids=requested_uids,
    )

    assert base_url == "https://grafana.example/"
    assert folder_uid == "explicit-folder"
    assert dashboard_uids == ("first", "second", "third")

    with pytest.raises(RuntimeError, match="must be a Grafana URL"):
        cli._grafana_export_url_parts("not-a-url", folder_uid="", dashboard_uids=())


def test_grafana_export_auth_candidates_support_basic_auth_password_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cr3t")
    monkeypatch.setattr(
        cli,
        "bearer_auth_candidates",
        lambda **_kwargs: [cli.GrafanaAuth(kind="bearer", value="token", source="bearer")],
    )

    candidates = cli._grafana_export_auth_candidates(
        token_env="",
        username="admin",
        password_env="ADMIN_PASSWORD",
    )

    assert [candidate.source for candidate in candidates] == ["bearer", "Basic auth user admin"]
    assert candidates[-1].authorization_header() == "Basic YWRtaW46czNjcjN0"


def test_grafana_export_auth_candidates_require_basic_auth_password_non_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAFANA_PASSWORD", raising=False)
    monkeypatch.setattr(cli, "bearer_auth_candidates", lambda **_kwargs: [])
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)

    with pytest.raises(RuntimeError, match="Grafana Basic auth password is missing"):
        cli._grafana_export_auth_candidates(
            token_env="",
            username="admin",
            password_env="GRAFANA_PASSWORD",
        )


def test_prompt_grafana_folder_tty_sorts_choices_and_enables_prefix_jump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    def fake_select(*_args: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return "question"

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
        select=fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)
    monkeypatch.setattr(
        cli,
        "_ask_questionary_with_prefix_jumps",
        lambda _question: cast(list[SimpleNamespace], captured["choices"])[0].value,
    )

    selected = cli._prompt_grafana_folder(
        (
            cli.GrafanaFolder(uid="gamma", title="Gamma"),
            cli.GrafanaFolder(uid="alpha", title="alpha"),
            cli.GrafanaFolder(uid="beta", title="Beta"),
        )
    )

    assert selected.uid == "alpha"
    assert [choice.title for choice in cast(list[SimpleNamespace], captured["choices"])] == [
        "alpha (alpha)",
        "Beta (beta)",
        "Gamma (gamma)",
    ]
    assert captured["use_jk_keys"] is False
    assert "Type a letter to jump" in str(captured["instruction"])


def test_prompt_grafana_dashboards_tty_sorts_choices_and_enables_prefix_jump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    def fake_checkbox(*_args: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return "question"

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
        checkbox=fake_checkbox,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)
    monkeypatch.setattr(cli, "_configure_questionary_checkbox_symbols", lambda: None)
    monkeypatch.setattr(
        cli,
        "_ask_questionary_with_prefix_jumps",
        lambda _question: [cast(list[SimpleNamespace], captured["choices"])[1].value],
    )

    selected = cli._prompt_grafana_dashboards(
        (
            cli.GrafanaDashboard(
                uid="gamma",
                title="Gamma Dashboard",
                folder_uid="folder",
                folder_title="Folder",
            ),
            cli.GrafanaDashboard(
                uid="alpha",
                title="alpha Dashboard",
                folder_uid="folder",
                folder_title="Folder",
            ),
            cli.GrafanaDashboard(
                uid="beta",
                title="Beta Dashboard",
                folder_uid="folder",
                folder_title="Folder",
            ),
        )
    )

    assert [dashboard.uid for dashboard in selected] == ["beta"]
    assert [choice.title for choice in cast(list[SimpleNamespace], captured["choices"])] == [
        "alpha Dashboard (alpha)",
        "Beta Dashboard (beta)",
        "Gamma Dashboard (gamma)",
    ]
    assert captured["use_jk_keys"] is False
    assert captured["use_search_filter"] is True
    assert "Type a letter to jump" in str(captured["instruction"])
    assert "Ctrl-A toggles all" in str(captured["instruction"])


def test_questionary_prefix_jump_keys_move_to_first_matching_choice() -> None:
    class _FakeKeyBindings:
        def __init__(self) -> None:
            self.handlers: dict[str, Callable[[object], None]] = {}
            self.calls: list[tuple[str, bool]] = []

        def add(self, key: str, eager: bool = False):
            self.calls.append((key, eager))

            def _decorator(fn: Callable[[object], None]) -> Callable[[object], None]:
                self.handlers[key] = fn
                return fn

            return _decorator

    control = SimpleNamespace(
        choices=[
            SimpleNamespace(title="Alpha", disabled=False),
            SimpleNamespace(title="Beta", disabled=False),
            SimpleNamespace(title="Gamma", disabled=False),
        ],
        pointed_at=0,
        is_selection_valid=lambda: True,
    )
    bindings = _FakeKeyBindings()
    question = SimpleNamespace(
        application=SimpleNamespace(
            key_bindings=bindings,
            layout=SimpleNamespace(
                container=SimpleNamespace(children=[SimpleNamespace(content=control)])
            ),
        )
    )

    cli._register_questionary_prefix_jump_keys(question)

    assert ("g", True) in bindings.calls
    assert ("G", True) in bindings.calls
    bindings.handlers["g"](SimpleNamespace(key_sequence=[SimpleNamespace(key="g")]))
    assert control.pointed_at == 2
    bindings.handlers["B"](SimpleNamespace(key_sequence=[SimpleNamespace(key="B")]))
    assert control.pointed_at == 1
    bindings.handlers["z"](SimpleNamespace(key_sequence=[SimpleNamespace(key="z")]))
    assert control.pointed_at == 1
    bindings.handlers["g"](SimpleNamespace(key_sequence=[]))
    assert control.pointed_at == 1


def test_collect_leaf_paths_skip_recursive_config_structures() -> None:
    payload: dict[str, Any] = {"name": "demo"}
    payload["self"] = payload
    values: list[object] = ["first"]
    values.append(values)

    assert cli._collect_scalar_leaf_paths(payload) == [("name",)]
    assert cli._collect_promptable_leaf_paths(values) == [(0,)]


def test_grafana_export_auth_candidates_suppress_bearer_warning_for_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_bearer_auth_candidates(**kwargs: object) -> list[object]:
        captured.update(kwargs)
        return []

    monkeypatch.setenv("GRAFANA_PASSWORD", "secret")
    monkeypatch.setattr(cli, "bearer_auth_candidates", fake_bearer_auth_candidates)

    candidates = cli._grafana_export_auth_candidates(
        token_env="",
        username="admin",
        password_env="GRAFANA_PASSWORD",
    )

    assert captured["on_warning"] is None
    assert len(candidates) == 1
    assert candidates[0].source == "Basic auth user admin"


def test_grafana_command_attaches_local_dashboard_json_without_api_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard_file = tmp_path / "source-dashboard.json"
    dashboard_file.write_text(
        json.dumps(
            {
                "dashboard": {
                    "id": 1,
                    "version": 4,
                    "uid": "local-dashboard",
                    "title": "Local Dashboard",
                    "panels": [{"datasource": {"type": "prometheus", "uid": "source-prometheus"}}],
                }
            }
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text("components:\n  apps: {}\n", encoding="utf-8")
    output_dir = tmp_path / "dashboards"
    captured: dict[str, Any] = {}

    def fail_api_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("local dashboard JSON mode must not call Grafana API helpers")

    monkeypatch.setattr(cli, "bearer_auth_candidates", fail_api_call)
    monkeypatch.setattr(cli, "list_folders", fail_api_call)
    monkeypatch.setattr(cli, "list_dashboards", fail_api_call)
    monkeypatch.setattr(cli, "dashboard_json", fail_api_call)
    monkeypatch.setattr(
        cli,
        "resolve_component_sources_file",
        lambda *, explicit=None: explicit or catalog_path,
    )
    monkeypatch.setattr(
        cli,
        "catalog_datasources",
        lambda path: (
            captured.__setitem__("catalog_path", path),
            (
                "grafana",
                (
                    cli.CatalogDatasource(
                        name="Nebius User Metrics",
                        uid="nebius-user-metrics",
                        datasource_type="prometheus",
                    ),
                ),
            ),
        )[1],
    )

    def fake_attach_dashboards_to_catalog(
        component_sources_path: Path,
        *,
        grafana_component_id: str,
        exports: Any,
        overwrite: bool,
    ) -> None:
        captured["attach_path"] = component_sources_path
        captured["grafana_component_id"] = grafana_component_id
        captured["exports"] = tuple(exports)
        captured["overwrite"] = overwrite

    monkeypatch.setattr(cli, "attach_dashboards_to_catalog", fake_attach_dashboards_to_catalog)

    result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--dashboard-json",
            str(dashboard_file),
            "--output-dir",
            str(output_dir),
            "--attach",
            "--component-sources",
            str(catalog_path),
            "--dashboard-folder",
            "mk8s",
            "--datasource",
            "Nebius User Metrics",
        ],
    )

    assert result.exit_code == 0, result.output
    exported = output_dir / "mk8s" / "local-dashboard.json"
    assert json.loads(exported.read_text(encoding="utf-8")) == {
        "uid": "local-dashboard",
        "title": "Local Dashboard",
        "panels": [{"datasource": {"type": "prometheus", "uid": "nebius-user-metrics"}}],
    }
    assert captured["catalog_path"] == catalog_path
    assert captured["attach_path"] == catalog_path
    assert captured["grafana_component_id"] == "grafana"
    assert captured["overwrite"] is False
    exports = captured["exports"]
    assert len(exports) == 1
    assert exports[0].dashboard_key == "local-dashboard"
    assert exports[0].catalog_folder == "mk8s"
    assert exports[0].datasource_name == "Nebius User Metrics"
    assert exports[0].path == exported
    assert "Attached 1 dashboard(s)" in _plain_output(result.output)


def test_grafana_command_exports_multiple_local_dashboard_json_without_catalog_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_dashboard = tmp_path / "cluster.json"
    first_dashboard.write_text(
        json.dumps(
            {
                "dashboard": {
                    "id": 1,
                    "version": 2,
                    "uid": "cluster",
                    "title": "Cluster",
                    "panels": [],
                }
            }
        ),
        encoding="utf-8",
    )
    second_dashboard = tmp_path / "nodes.json"
    second_dashboard.write_text(
        json.dumps({"id": 3, "version": 4, "uid": "nodes", "title": "Nodes", "panels": []}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "dashboards"

    def fail_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("local export-only mode must not call API or catalog helpers")

    monkeypatch.setattr(cli, "bearer_auth_candidates", fail_call)
    monkeypatch.setattr(cli, "list_folders", fail_call)
    monkeypatch.setattr(cli, "list_dashboards", fail_call)
    monkeypatch.setattr(cli, "dashboard_json", fail_call)
    monkeypatch.setattr(cli, "resolve_component_sources_file", fail_call)
    monkeypatch.setattr(cli, "catalog_datasources", fail_call)
    monkeypatch.setattr(cli, "attach_dashboards_to_catalog", fail_call)

    result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--dashboard-json",
            str(first_dashboard),
            "--dashboard-json",
            str(second_dashboard),
            "--output-dir",
            str(output_dir),
            "--dashboard-folder",
            "mk8s",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads((output_dir / "mk8s" / "cluster.json").read_text(encoding="utf-8")) == {
        "uid": "cluster",
        "title": "Cluster",
        "panels": [],
    }
    assert json.loads((output_dir / "mk8s" / "nodes.json").read_text(encoding="utf-8")) == {
        "uid": "nodes",
        "title": "Nodes",
        "panels": [],
    }
    assert "Attached" not in _plain_output(result.output)


def test_grafana_command_overwrite_applies_to_local_json_and_catalog_attach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard_file = tmp_path / "dashboard.json"
    dashboard_file.write_text(
        json.dumps({"uid": "local-dashboard", "title": "Local Dashboard", "panels": []}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "dashboards"
    existing = output_dir / "mk8s" / "local-dashboard.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"uid":"old"}\n', encoding="utf-8")
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text("components:\n  apps: {}\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "resolve_component_sources_file",
        lambda *, explicit=None: explicit or catalog_path,
    )
    monkeypatch.setattr(
        cli,
        "catalog_datasources",
        lambda _path: (
            "grafana",
            (
                cli.CatalogDatasource(
                    name="Nebius User Metrics",
                    uid="nebius-user-metrics",
                    datasource_type="prometheus",
                ),
            ),
        ),
    )

    def fake_attach_dashboards_to_catalog(
        _component_sources_path: Path,
        *,
        grafana_component_id: str,
        exports: Any,
        overwrite: bool,
    ) -> None:
        captured["grafana_component_id"] = grafana_component_id
        captured["exports"] = tuple(exports)
        captured["overwrite"] = overwrite

    monkeypatch.setattr(cli, "attach_dashboards_to_catalog", fake_attach_dashboards_to_catalog)

    result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--dashboard-json",
            str(dashboard_file),
            "--output-dir",
            str(output_dir),
            "--attach",
            "--component-sources",
            str(catalog_path),
            "--dashboard-folder",
            "mk8s",
            "--datasource",
            "Nebius User Metrics",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(existing.read_text(encoding="utf-8")) == {
        "uid": "local-dashboard",
        "title": "Local Dashboard",
        "panels": [],
    }
    assert captured["grafana_component_id"] == "grafana"
    assert captured["overwrite"] is True
    exports = captured["exports"]
    assert len(exports) == 1
    assert exports[0].path == existing


def test_grafana_command_requires_exactly_one_dashboard_source(tmp_path: Path) -> None:
    dashboard_file = tmp_path / "dashboard.json"
    dashboard_file.write_text(json.dumps({"uid": "local", "title": "Local"}), encoding="utf-8")

    missing_result = runner.invoke(cli.app, ["grafana"])
    both_result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--export-dashboard",
            "https://grafana.example/",
            "--dashboard-json",
            str(dashboard_file),
        ],
    )
    local_with_folder_uid_result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--dashboard-json",
            str(dashboard_file),
            "--folder-uid",
            "folder",
        ],
    )
    local_with_dashboard_uid_result = runner.invoke(
        cli.app,
        [
            "grafana",
            "--dashboard-json",
            str(dashboard_file),
            "--dashboard-uid",
            "dashboard",
        ],
    )

    assert missing_result.exit_code != 0
    assert "Pass exactly one of --export-dashboard or --dashboard-json" in _plain_output(
        missing_result.output
    )
    assert both_result.exit_code != 0
    assert "Pass exactly one of --export-dashboard or --dashboard-json" in _plain_output(
        both_result.output
    )
    assert local_with_folder_uid_result.exit_code != 0
    assert (
        "--folder-uid and --dashboard-uid are only valid with --export-dashboard"
        in _plain_output(local_with_folder_uid_result.output)
    )
    assert local_with_dashboard_uid_result.exit_code != 0
    assert (
        "--folder-uid and --dashboard-uid are only valid with --export-dashboard"
        in _plain_output(local_with_dashboard_uid_result.output)
    )


def test_validate_dashboards_command_reports_live_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    class Result(SimpleNamespace):
        @property
        def ok(self) -> bool:
            return not self.errors

    monkeypatch.setattr(
        cli,
        "_load_context_readonly",
        lambda path: (
            captured.__setitem__("config_path", path),
            (
                "config",
                SimpleNamespace(
                    config_path=path,
                    generated_dir=tmp_path / "generated",
                    reports_dir=tmp_path / "generated" / "reports",
                ),
            ),
        )[1],
    )

    def fake_validate_grafana_dashboard_fits(
        config: object,
        *,
        target_ref: str = "",
        target_extra_envs: object = None,
        progress_callback: object = None,
    ) -> tuple[Result, ...]:
        captured["target_ref"] = target_ref
        captured["target_extra_envs"] = target_extra_envs
        if callable(progress_callback):
            progress_callback("init", 0, 1)
            progress_callback("cluster1: nebius-kubernetes/kubernetes-cluster-monitoring", 0, 1)
            progress_callback("cluster1: nebius-kubernetes/kubernetes-cluster-monitoring", 1, 1)
            progress_callback("done", 1, 1)
        return (
            Result(
                target_ref="cluster1",
                signal="metrics",
                dashboard_ref="nebius-kubernetes/kubernetes-cluster-monitoring",
                dashboard_uid="cxcli-kubernetes-metrics",
                datasource="Nebius User Metrics",
                datasource_uid="nebius-user-metrics",
                datasource_type="prometheus",
                read_endpoint="metrics_user_read",
                source="cxcli-owned JSON",
                checks=("Metric/label names matched; PromQL checked",),
                errors=(),
                warnings=("Tempo query returned no traces: {}",),
            ),
        )

    monkeypatch.setattr(
        cli,
        "validate_grafana_dashboard_fits",
        fake_validate_grafana_dashboard_fits,
    )

    result = runner.invoke(
        cli.app,
        ["validate-dashboards", str(config_path), "--target", "cluster1"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    normalized_output = " ".join(output.split())
    assert captured["config_path"] == config_path
    assert captured["target_ref"] == "cluster1"
    assert captured["target_extra_envs"] == {}
    assert "Grafana dashboards: validating 1 dashboard binding(s)" in normalized_output
    assert (
        "Grafana dashboards: cluster1: nebius-kubernetes/kubernetes-cluster-monitoring (1/1)"
        in normalized_output
    )
    assert (
        normalized_output.count(
            "Grafana dashboards: cluster1: nebius-kubernetes/kubernetes-cluster-monitoring (1/1)"
        )
        == 1
    )
    assert "OK: metrics@cluster1" in normalized_output
    assert "Nebius User Metrics (prometheus, metrics_user_read)" in normalized_output
    assert "Source: cxcli-owned JSON" in normalized_output
    assert "Checks: - Metric/label names matched; PromQL checked" in normalized_output
    assert "Warnings: - Tempo query returned no traces: {}" in normalized_output
    assert f"Grafana dashboards fit live datasources: {config_path}" in output.replace("\n", "")


def test_validate_dashboards_reads_target_contexts_from_deploy_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / cli.DEPLOY_REPORT_FILENAME).write_text(
        "\n".join(
            [
                "### Target `cluster1`",
                "",
                "- MK8s: cluster ID `mk8scluster-111`; "
                "kube context `nebius-cluster1-mk8scluster-111-external`",
                "",
                "### Target `cluster2`",
                "",
                "- MK8s: cluster ID `mk8scluster-222`; "
                "kube context `nebius-cluster2-mk8scluster-222-external`",
                "",
                "## Infra",
                "",
                "### MK8s Clusters",
                "",
                "- `cluster3` (`cluster-three`)",
                "  - CPU nodes: `2` node(s) at `cpu-d3/32vcpu-128gb`",
                "  - Cluster ID: `mk8scluster-333`",
                "  - Kube context: `nebius-cluster3-mk8scluster-333-external`",
            ]
        ),
        encoding="utf-8",
    )

    metadata = cli._deploy_report_target_contexts(
        cast(ProjectPaths, SimpleNamespace(reports_dir=reports_dir))
    )

    assert metadata == {
        "cluster1": {
            "cluster_id": "mk8scluster-111",
            "kube_context": "nebius-cluster1-mk8scluster-111-external",
        },
        "cluster2": {
            "cluster_id": "mk8scluster-222",
            "kube_context": "nebius-cluster2-mk8scluster-222-external",
        },
        "cluster3": {
            "cluster_id": "mk8scluster-333",
            "kube_context": "nebius-cluster3-mk8scluster-333-external",
        },
    }


def test_kubeconfig_target_env_requires_context_in_kubeconfig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "contexts": [
                    {
                        "name": "nebius-cluster2-mk8scluster-222-external",
                        "context": {"cluster": "cluster2", "user": "user2"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    with ExitStack() as stack:
        env = cli._kubeconfig_target_env(
            "cluster2",
            stack=stack,
            preferred_context="nebius-cluster2-mk8scluster-222-external",
            preferred_cluster_id="mk8scluster-222",
        )
        assert env["KUBECONFIG"]
        assert env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == (
            "nebius-cluster2-mk8scluster-222-external"
        )
        assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "mk8scluster-222"

    with ExitStack() as stack:
        assert (
            cli._kubeconfig_target_env(
                "cluster1",
                stack=stack,
                preferred_context="nebius-cluster1-mk8scluster-111-external",
                preferred_cluster_id="mk8scluster-111",
            )
            == {}
        )


def test_kube_context_name_for_target_does_not_guess_ambiguous_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "contexts": [
                    {
                        "name": "nebius-cluster1-mk8scluster-old-external",
                        "context": {"cluster": "old", "user": "old"},
                    },
                    {
                        "name": "nebius-cluster1-mk8scluster-new-external",
                        "context": {"cluster": "new", "user": "new"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    assert cli._kube_context_name_for_target("cluster1") == ""


def test_kube_context_name_for_target_prefers_matching_current_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "nebius-cluster1-mk8scluster-new-external",
                "contexts": [
                    {
                        "name": "nebius-cluster1-mk8scluster-old-external",
                        "context": {"cluster": "old", "user": "old"},
                    },
                    {
                        "name": "nebius-cluster1-mk8scluster-new-external",
                        "context": {"cluster": "new", "user": "new"},
                    },
                    {
                        "name": "nebius-cluster2-mk8scluster-other-external",
                        "context": {"cluster": "other", "user": "other"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    assert (
        cli._kube_context_name_for_target("cluster1") == "nebius-cluster1-mk8scluster-new-external"
    )
    assert cli._kube_context_name_for_target("cluster2") == (
        "nebius-cluster2-mk8scluster-other-external"
    )


def test_validate_dashboards_refuses_current_context_fallback_for_targeted_grafana(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump({"apiVersion": "v1", "kind": "Config", "contexts": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    monkeypatch.setattr(
        cli, "enabled_cluster_target_refs", lambda _config: ("cluster1", "cluster2")
    )
    monkeypatch.setattr(
        cli,
        "grafana_enabled_for_target",
        lambda _config, *, target_ref="": target_ref in {"cluster1", "cluster2"},
    )
    paths = SimpleNamespace(
        generated_dir=tmp_path / "generated",
        reports_dir=tmp_path / "generated" / "reports",
    )

    with ExitStack() as stack, pytest.raises(RuntimeError) as excinfo:
        cli._grafana_dashboard_validation_target_envs(
            {},
            cast(ProjectPaths, paths),
            target_ref="",
            stack=stack,
        )

    message = str(excinfo.value)
    assert "could not resolve an explicit kube context" in message
    assert "cluster1, cluster2" in message
    assert f"nebius-cxcli flux apply {paths.generated_dir.resolve()}" in message
    assert "nebius-cxcli flux apply <config.yaml>" not in message


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
    fake_paths.reports_dir.mkdir(parents=True, exist_ok=True)
    deploy_report = fake_paths.reports_dir / "deploy-report.md"
    deploy_detail_report = fake_paths.reports_dir / "deploy-gpu-visibility-report-mk8s.json"
    soperator_cluster_report_dir = (
        fake_paths.reports_dir / "soperator-clusters" / "external-context"
    )
    onboard_report = soperator_cluster_report_dir / "discovery"
    node_template_report = fake_paths.reports_dir / "upgrade-node-template-report.md"
    node_template_report_json = fake_paths.reports_dir / "upgrade-node-template-report.json"
    node_group_report = fake_paths.reports_dir / "migrate-node-group-report.md"
    node_group_report_json = fake_paths.reports_dir / "migrate-node-group-report.json"
    upgrade_report = soperator_cluster_report_dir / "soperator-upgrade" / "report.md"
    upgrade_report_json = soperator_cluster_report_dir / "soperator-upgrade" / "report.json"
    stale_report = fake_paths.reports_dir / "old.json"
    deploy_report.write_text(
        "# Deploy Report\n\n- Detail report: `deploy-gpu-visibility-report-mk8s.json`\n",
        encoding="utf-8",
    )
    deploy_detail_report.write_text('{"status": "passed"}\n', encoding="utf-8")
    onboard_report.mkdir(parents=True)
    (onboard_report / "manifest.json").write_text('{"schema": "discovery"}\n', encoding="utf-8")
    node_template_report.write_text("# MK8s Node Template Upgrade Report\n", encoding="utf-8")
    node_template_report_json.write_text('{"status": "passed"}\n', encoding="utf-8")
    node_group_report.write_text("# MK8s Node-Group Upgrade Report\n", encoding="utf-8")
    node_group_report_json.write_text('{"status": "approved-pre-mutation"}\n', encoding="utf-8")
    upgrade_report.parent.mkdir(parents=True, exist_ok=True)
    upgrade_report.write_text("# Soperator Upgrade Report\n", encoding="utf-8")
    upgrade_report_json.write_text('{"status": "completed"}\n', encoding="utf-8")
    stale_report.write_text("{}\n", encoding="utf-8")
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
        "_write_generated_runtime_manifest",
        lambda *_args, **_kwargs: fake_paths.generated_dir / "nebius-cxcli-manifest.json",
    )
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)

    result = runner.invoke(cli.app, ["render", "--force", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert calls["rendered"] is True
    assert deploy_report.read_text(encoding="utf-8").startswith("# Deploy Report")
    assert deploy_detail_report.read_text(encoding="utf-8") == '{"status": "passed"}\n'
    assert (onboard_report / "manifest.json").read_text(encoding="utf-8") == (
        '{"schema": "discovery"}\n'
    )
    assert node_template_report.read_text(encoding="utf-8").startswith(
        "# MK8s Node Template Upgrade Report"
    )
    assert node_template_report_json.read_text(encoding="utf-8") == '{"status": "passed"}\n'
    assert node_group_report.read_text(encoding="utf-8").startswith(
        "# MK8s Node-Group Upgrade Report"
    )
    assert node_group_report_json.read_text(encoding="utf-8") == (
        '{"status": "approved-pre-mutation"}\n'
    )
    assert upgrade_report.read_text(encoding="utf-8").startswith("# Soperator Upgrade Report")
    assert upgrade_report_json.read_text(encoding="utf-8") == '{"status": "completed"}\n'
    assert not stale_report.exists()


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
        "_write_generated_runtime_manifest",
        lambda *_args, **_kwargs: fake_paths.generated_dir / "nebius-cxcli-manifest.json",
    )
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)

    result = runner.invoke(
        cli.app,
        ["render", str(tmp_path / "config.yaml")],
        input="\n\ny\n",
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert output.count("Continue and replace the existing generated artifacts?") == 3
    assert "[y/n]" in output
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
    assert "nebius-cxcli deploy" not in _plain_output(result.output)
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


def test_deploy_command_uses_single_canonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1", "render": {"module_sources": []}}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_ensure_ci_workflow_for_deployments_root",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("deploy must not bootstrap CI workflow")
        ),
    )

    def _fake_deploy_generated_artifacts(
        config: object,
        paths: object,
        loaded_manifest: object,
        *,
        skip_validations: bool,
        skip_validation_kinds: set[str],
        requested_target_ref: str | None = None,
        all_targets: bool = False,
        job_policy: str = "fail",
        cancel_job_ids: Sequence[str] = (),
        requeue_job_ids: Sequence[str] = (),
        job_wait_timeout_seconds: int = 0,
        job_refresh_interval_seconds: int = 30,
    ) -> cli.DeployRunSummary:
        captured["config"] = config
        captured["paths"] = paths
        captured["manifest"] = loaded_manifest
        captured["skip_validations"] = skip_validations
        captured["skip_validation_kinds"] = skip_validation_kinds
        captured["requested_target_ref"] = requested_target_ref
        captured["all_targets"] = all_targets
        captured["job_policy"] = job_policy
        captured["cancel_job_ids"] = cancel_job_ids
        captured["requeue_job_ids"] = requeue_job_ids
        captured["job_wait_timeout_seconds"] = job_wait_timeout_seconds
        captured["job_refresh_interval_seconds"] = job_refresh_interval_seconds
        return cli.DeployRunSummary()

    monkeypatch.setattr(cli, "_deploy_generated_artifacts", _fake_deploy_generated_artifacts)

    result = runner.invoke(
        cli.app,
        ["deploy", str(fake_paths.config_path)],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Deployment summary" in output
    assert "Validation:" in output
    assert "Copy/paste commands:" in output
    assert "Important paths:" in output
    assert "No deploy-time validations were configured for this run." in output
    assert f"Validation detail reports: {fake_paths.reports_dir}" in output
    assert f"Deploy report: {fake_paths.reports_dir / 'deploy-report.md'}" in output
    assert "Deploy completed" in output
    assert "Deploy completed from" not in output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "manifest": manifest,
        "skip_validations": False,
        "skip_validation_kinds": set(),
        "requested_target_ref": None,
        "all_targets": False,
        "job_policy": "wait-then-cancel",
        "cancel_job_ids": (),
        "requeue_job_ids": (),
        "job_wait_timeout_seconds": 3600,
        "job_refresh_interval_seconds": 30,
    }


def test_acceptance_test_smoke_command_passes_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_run_acceptance_smoke_command(**kwargs: Any) -> list[Path]:
        captured.update(kwargs)
        return [fake_paths.reports_dir / "acceptance-smoke-report-mk8s.json"]

    monkeypatch.setattr(
        cli,
        "_run_acceptance_smoke_command",
        _fake_run_acceptance_smoke_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "smoke",
            str(fake_paths.config_path),
            "--target",
            "mk8s",
            "--suite",
            "slurm",
            "--batch-size",
            "64",
            "--concurrency",
            "4",
            "--fail-fast",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Acceptance smoke report:" in _plain_output(result.output)
    assert captured == {
        "config_path": fake_paths.config_path,
        "requested_target_ref": "mk8s",
        "all_targets": False,
        "suites": ["slurm"],
        "batch_size": 64,
        "concurrency": 4,
        "continue_on_failure": False,
    }


def test_acceptance_test_smoke_command_requires_suite(tmp_path: Path) -> None:
    fake_paths = _fake_paths(tmp_path)

    result = runner.invoke(
        cli.app,
        ["acceptance-test", "smoke", str(fake_paths.config_path)],
    )

    assert result.exit_code == 1, result.output
    output = _plain_output(result.output)
    assert "WARNING:" in output
    assert "acceptance-test smoke requires --suite" in output
    assert "no default K8s suite is selected" in output
    assert "Choose one of: k8s-cuda, slurm" in output
    assert "nebius-cxcli acceptance-test smoke <config.yaml> --suite slurm" in output


def test_acceptance_test_smoke_command_prints_passed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    report_path = fake_paths.reports_dir / "acceptance-smoke-report-sop-cluster1.json"

    def _fake_run_acceptance_smoke_command(**_kwargs: Any) -> list[Path]:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "scope": "all-node-slurm-smoke",
                    "target_ref": "sop-cluster1",
                    "summary": "8/8 Soperator/Slurm checks passed.",
                    "elapsed_seconds": 83.2,
                    "elapsed_time": "00:01:23",
                }
            ),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(
        cli,
        "_run_acceptance_smoke_command",
        _fake_run_acceptance_smoke_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "smoke",
            str(fake_paths.config_path),
            "--all-targets",
            "--suite",
            "slurm",
        ],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    output_flat = " ".join(output.split())
    assert "Acceptance smoke report:" in output
    assert report_path.name in output
    assert (
        "Acceptance smoke result: PASSED all-node-slurm-smoke (sop-cluster1): "
        "8/8 Soperator/Slurm checks passed. (elapsed 00:01:23)"
    ) in output_flat


def test_acceptance_test_result_markup_colors_status_and_detail(tmp_path: Path) -> None:
    passed_path = tmp_path / "passed.json"
    failed_path = tmp_path / "failed.json"
    skipped_path = tmp_path / "skipped.json"

    passed_path.write_text(
        json.dumps(
            {
                "passed": True,
                "scope": "k8s-nccl",
                "target_ref": "mk8s",
                "summary": "All [good].",
                "elapsed_seconds": 1.0,
                "elapsed_time": "00:01:23",
            }
        ),
        encoding="utf-8",
    )
    failed_path.write_text(
        json.dumps(
            {
                "passed": False,
                "scope": "k8s-nccl",
                "target_ref": "mk8s",
                "error": "Bandwidth below threshold.",
            }
        ),
        encoding="utf-8",
    )
    skipped_path.write_text(
        json.dumps(
            {
                "passed": True,
                "skipped": True,
                "scope": "slurm-nccl",
                "target_ref": "sop-cluster1",
                "skip_reason": "No idle GPU nodes.",
            }
        ),
        encoding="utf-8",
    )

    passed = cli._acceptance_report_result_markup("benchmark", passed_path)
    failed = cli._acceptance_report_result_markup("benchmark", failed_path)
    skipped = cli._acceptance_report_result_markup("benchmark", skipped_path)
    unknown = cli._acceptance_report_result_markup("benchmark", tmp_path / "missing.json")
    report_path = cli._acceptance_report_path_markup("benchmark", passed_path)

    assert "[black on green]PASSED[/]" in passed
    assert "[bold cyan]k8s-nccl[/]" in passed
    assert "[bold magenta](mk8s)[/]" in passed
    assert "All \\[good]." in passed
    assert "[bold]All \\[good].[/]" not in passed
    assert "(elapsed 00:01:23)" in passed
    assert "[bold](elapsed 00:01:23)[/]" not in passed
    assert "[bold white on red]FAILED[/]" in failed
    assert "Bandwidth below threshold." in failed
    assert "[bold]Bandwidth below threshold.[/]" not in failed
    assert "[black on yellow]SKIPPED[/]" in skipped
    assert "No idle GPU nodes." in skipped
    assert "[bold]No idle GPU nodes.[/]" not in skipped
    assert "[black on cyan]UNKNOWN[/]" in unknown
    assert "Acceptance benchmark report:" in report_path
    assert "[bold]Acceptance benchmark report:[/]" not in report_path
    assert f"[bold cyan]{passed_path}[/]" in report_path


def test_acceptance_test_result_omits_bandwidth_comment_from_terminal_detail(
    tmp_path: Path,
) -> None:
    comment = (
        "Observed average bus bandwidth is below the configured threshold; "
        "for a 1-GPU Slurm NCCL run this is recorded as informational because "
        "the NCCL workload completed and reported average bandwidth."
    )
    summary = (
        "multi-node NCCL all_reduce_perf benchmark completed on partition gpu with "
        "4 rank(s) across 4 node(s) (1 GPU(s) per node); average bus bandwidth "
        f"1.9 Gbps across 2G message size. Required threshold: 300.0 Gbps. {comment}"
    )
    reports = {
        "slurm-nccl.json": {
            "passed": True,
            "status": "passed",
            "scope": "slurm-nccl",
            "target_ref": "mk8s",
            "summary": "10/10 Soperator/Slurm checks passed.",
            "elapsed_seconds": 248.0,
            "elapsed_time": "00:04:08",
            "checks": [
                {
                    "name": "Slurm NCCL benchmark",
                    "status": "passed",
                    "passed": True,
                    "summary": summary,
                    "bandwidth_threshold_comment": comment,
                }
            ],
        },
        "k8s-nccl.json": {
            "passed": True,
            "status": "passed",
            "scope": "k8s-nccl",
            "target_ref": "mk8s",
            "summary": summary.replace("partition gpu with ", ""),
            "elapsed_seconds": 248.0,
            "elapsed_time": "00:04:08",
            "bandwidth_threshold_comment": comment,
        },
    }

    for filename, payload in reports.items():
        report_path = tmp_path / filename
        report_path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

        markup = cli._acceptance_report_result_markup("benchmark", report_path)

        assert "Required threshold: 300.0 Gbps." in markup
        assert "Observed average bus bandwidth" not in markup
        assert "(elapsed 00:04:08)" in markup
        assert "[bold](elapsed 00:04:08)[/]" not in markup
        assert json.loads(report_path.read_text(encoding="utf-8")) == payload


def test_acceptance_test_smoke_runner_uses_readonly_context_and_kube_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s(
        charts=[
            {
                "id": "soperator",
                "instance_id": "mk8s",
                "target_ref": "mk8s",
                "enabled": True,
            }
        ]
    )
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    soperator_spec = {
        "kind": cli.SOPERATOR_CLUSTER_VALIDATION_KIND,
        "target_ref": "mk8s",
        "name": "Soperator acceptance smoke test (mk8s)",
        "report_file": "acceptance-smoke-report-mk8s.json",
        "mode": "acceptance",
    }
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monotonic_values = iter([100.0, 183.2])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    handoff_calls: list[tuple[Mapping[str, Any], bool]] = []

    def _fake_prepare_cluster_handoff_kube_env(
        *_args: object,
        target: Mapping[str, Any],
        allow_terraform_output: bool,
        **_kwargs: object,
    ) -> dict[str, str]:
        handoff_calls.append((target, allow_terraform_output))
        return {"KUBECONFIG": "/tmp/kubeconfig"}

    monkeypatch.setattr(
        cli, "_prepare_cluster_handoff_kube_env", _fake_prepare_cluster_handoff_kube_env
    )
    monkeypatch.setattr(
        cli,
        "_deploy_report_target_contexts",
        lambda _paths: {
            "mk8s": {
                "cluster_id": "mk8scluster-123",
                "kube_context": "nebius-mk8s-mk8scluster-123-external",
            }
        },
    )
    monkeypatch.setattr(
        cli,
        "soperator_acceptance_smoke_specs",
        lambda *_args, **_kwargs: [soperator_spec],
    )
    monkeypatch.setattr(cli, "mk8s_acceptance_smoke_validation_specs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_run_soperator_cluster_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        calls.append(("soperator", validations, reports_dir, extra_env))
        report_path = reports_dir / "acceptance-smoke-report-mk8s.json"
        report_path.write_text(
            json.dumps({"passed": True, "scope": "all-node-slurm-smoke", "target_ref": "mk8s"}),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(
        cli,
        "run_soperator_cluster_validations",
        _fake_run_soperator_cluster_validations,
    )

    written = cli._run_acceptance_smoke_command(
        config_path=fake_paths.config_path,
        requested_target_ref="mk8s",
        all_targets=False,
        suites=["slurm"],
        batch_size=128,
        concurrency=8,
        continue_on_failure=True,
    )

    assert written == [fake_paths.reports_dir / "acceptance-smoke-report-mk8s.json"]
    assert calls == [
        (
            "soperator",
            [soperator_spec],
            fake_paths.reports_dir,
            {"KUBECONFIG": "/tmp/kubeconfig"},
        )
    ]
    assert handoff_calls == [
        (
            {
                **_mk8s_target(fake_paths),
                "cluster_id": "mk8scluster-123",
                "kube_context": "nebius-mk8s-mk8scluster-123-external",
            },
            False,
        )
    ]
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["elapsed_seconds"] == pytest.approx(83.2)
    assert report["elapsed_time"] == "00:01:23"


def test_acceptance_test_rejects_same_target_duplicate_canonical_reports() -> None:
    with pytest.raises(RuntimeError, match="same canonical report file"):
        cli._raise_on_duplicate_acceptance_reports(
            [
                {
                    "name": "Soperator acceptance smoke test (mk8s)",
                    "report_file": "acceptance-smoke-report-mk8s.json",
                },
                {
                    "name": "K8s CUDA acceptance smoke (mk8s)",
                    "report_file": "acceptance-smoke-report-mk8s.json",
                },
            ]
        )


def test_acceptance_test_smoke_runner_runs_explicit_suite_for_all_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster-a"),
                _mk8s_target(fake_paths, target_ref="cluster-b"),
            ],
            "validations": [],
        }
    }
    smoke_specs = [
        {
            "kind": "mk8s_cuda_smoke",
            "target_ref": "cluster-a",
            "name": "K8s CUDA acceptance smoke (cluster-a)",
            "report_file": "acceptance-smoke-report-cluster-a.json",
        },
        {
            "kind": "mk8s_cuda_smoke",
            "target_ref": "cluster-b",
            "name": "K8s CUDA acceptance smoke (cluster-b)",
            "report_file": "acceptance-smoke-report-cluster-b.json",
        },
    ]
    handoff_calls: list[bool] = []
    smoke_kwargs: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(cli, "_deploy_report_target_contexts", lambda _paths: {})
    monkeypatch.setattr(cli, "soperator_acceptance_smoke_specs", lambda *_args, **_kwargs: [])

    def _fake_mk8s_acceptance_smoke_validation_specs(*_args: Any, **kwargs: Any):
        smoke_kwargs.update(kwargs)
        return smoke_specs

    monkeypatch.setattr(
        cli,
        "mk8s_acceptance_smoke_validation_specs",
        _fake_mk8s_acceptance_smoke_validation_specs,
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_prepare_cluster_handoff_kube_env(
        *_args: object,
        set_current_context: bool,
        **_kwargs: object,
    ) -> dict[str, str]:
        handoff_calls.append(set_current_context)
        return {"KUBECONFIG": "/tmp/kubeconfig"}

    monkeypatch.setattr(
        cli, "_prepare_cluster_handoff_kube_env", _fake_prepare_cluster_handoff_kube_env
    )
    monkeypatch.setattr(
        cli,
        "run_mk8s_gpu_validations",
        lambda validations, *, reports_dir, **_kwargs: [
            reports_dir / f"acceptance-smoke-report-{validations[0]['target_ref']}.json"
        ],
    )

    written = cli._run_acceptance_smoke_command(
        config_path=fake_paths.config_path,
        requested_target_ref=None,
        all_targets=False,
        suites=["k8s-cuda"],
        batch_size=128,
        concurrency=8,
        continue_on_failure=True,
    )

    assert written == [
        fake_paths.reports_dir / "acceptance-smoke-report-cluster-a.json",
        fake_paths.reports_dir / "acceptance-smoke-report-cluster-b.json",
    ]
    assert smoke_kwargs == {"include_soperator_targets": True}
    assert handoff_calls == [False, False]


def test_acceptance_test_help_shows_safe_examples() -> None:
    group_result = runner.invoke(cli.app, ["acceptance-test", "--help"])
    smoke_result = runner.invoke(cli.app, ["acceptance-test", "smoke", "--help"])
    benchmark_result = runner.invoke(cli.app, ["acceptance-test", "benchmark", "--help"])
    deploy_result = runner.invoke(cli.app, ["deploy", "--help"])

    assert group_result.exit_code == 0, group_result.output
    assert smoke_result.exit_code == 0, smoke_result.output
    assert benchmark_result.exit_code == 0, benchmark_result.output
    assert deploy_result.exit_code == 0, deploy_result.output

    group_help = " ".join(_plain_output(group_result.output).split())
    smoke_help = " ".join(_plain_output(smoke_result.output).split())
    benchmark_help = " ".join(_plain_output(benchmark_result.output).split())
    deploy_help = " ".join(_plain_output(deploy_result.output).split())

    assert "same-target K8s and Slurm suites cannot be combined" in group_help
    assert (
        "Commands write JSON reports only, print color-coded concise result lines with elapsed time"
        in group_help
    )
    assert "suite-driven smoke validation and explicit benchmarks" in group_help
    assert "and do not change config" in group_help
    assert (
        "nebius-cxcli acceptance-test smoke <config.yaml> --target sop-cluster1 --suite slurm"
        in group_help
    )
    assert "Slurm all-node smoke JSON report:" in group_help
    assert "K8s CUDA smoke JSON report:" in group_help
    assert "Slurm smoke on all targets:" in group_help
    assert "K8s NCCL benchmark on all targets:" in group_help
    assert "nebius-cxcli acceptance-test benchmark <config.yaml> --suite k8s-nccl" in group_help
    assert "Slurm NCCL benchmark JSON report:" in group_help
    assert "force K8s NCCL on a Soperator-owned GPU target during maintenance" in group_help
    assert "JSON reports only under generated/reports/" in smoke_help
    assert "does not change config.yaml" in smoke_help
    assert "Target handoff comes from deploy-report/local kubeconfig context" in smoke_help
    assert "does not initialize the Terraform backend" in smoke_help
    assert "prints a color-coded concise PASSED/FAILED/SKIPPED result line" in smoke_help
    assert "with elapsed time in hh:mm:ss" in smoke_help
    assert "Slurm all-node smoke JSON report:" in smoke_help
    assert "K8s CUDA smoke JSON report:" in smoke_help
    assert "--suite slurm --batch-size 128 --concurrency 8 --fail-fast" in smoke_help
    assert "--batch-size 128 --concurrency 8 --fail-fast" in smoke_help
    assert "Run acceptance smoke tests for one generated cluster target." in smoke_help
    assert "Omit --target to run every generated target." in smoke_help
    assert "This is the default when --target is omitted." in smoke_help
    assert "Required acceptance suite to run" in smoke_help
    assert "Supported values: k8s-cuda, slurm" in smoke_help
    assert "Omit --suite to run target defaults" not in smoke_help
    assert "suite defaults" not in smoke_help
    assert "JSON reports only under generated/reports/" in benchmark_help
    assert "does not change config.yaml" in benchmark_help
    assert "Target handoff comes from deploy-report/local kubeconfig context" in benchmark_help
    assert "does not initialize the Terraform backend" in benchmark_help
    assert "prints a color-coded concise PASSED/FAILED/SKIPPED result line" in benchmark_help
    assert "with elapsed time in hh:mm:ss" in benchmark_help
    assert "Run explicit post-deploy benchmark suites" in benchmark_help
    assert "Use --suite to select the required benchmark runtime path" in benchmark_help
    assert "omitted --suite fails fast instead of defaulting to a K8s suite" in benchmark_help
    assert "K8s NCCL uses the pinned transient nccl-test chart" in benchmark_help
    assert "slurm-nccl runs the Slurm NCCL suite" in benchmark_help
    assert "Run acceptance benchmarks for one generated cluster target." in benchmark_help
    assert "Required benchmark suite to run" in benchmark_help
    assert "Omit --suite for the default k8s-nccl suite." not in benchmark_help
    assert "Slurm NCCL benchmark:" in benchmark_help
    assert "Plain MK8s NCCL benchmark with run-only overrides:" in benchmark_help
    assert "plain MK8s K8s NCCL benchmark:" not in benchmark_help
    assert "Default: all schedulable GPU nodes." in benchmark_help
    assert "Default: no timeout; the run continues until completion or user cancellation." in (
        benchmark_help
    )
    assert "Default: 300. On 1-GPU runs, below-threshold bandwidth" in benchmark_help
    assert "reported as a comment when NCCL completes" in benchmark_help
    assert (
        "nebius-cxcli acceptance-test benchmark <config.yaml> --target mk8s-prod "
        "--suite k8s-nccl --max-nodes 4 --timeout 20m --average-bus-bandwidth-threshold-gbps 300"
    ) in benchmark_help
    assert (
        "nebius-cxcli acceptance-test benchmark <config.yaml> --target sop-cluster1 "
        "--suite k8s-nccl --max-nodes 2"
    ) in benchmark_help
    assert (
        "nebius-cxcli acceptance-test benchmark <config.yaml> --target sop-cluster1 "
        "--suite slurm-nccl --max-nodes 2 --timeout 5m "
        "--average-bus-bandwidth-threshold-gbps 300"
    ) in benchmark_help
    stale_help_terms = (
        "deploy.targets[].validations",
        "validations.mk8s_gpu",
        "cuda_smoke",
        "nccl.enabled",
        "nccl.max_nodes",
        "deploy-time NCCL",
        "generic deploy-time NCCL",
        "future benchmark suites",
        "exhaustive all-node validation",
        "plain MK8s all-node CUDA JSON report",
        "default smoke",
        "default K8s NCCL",
        "defaults to k8s-nccl",
        "suite defaults",
        "soperator-slurm",
        "Soperator-owned GPU targets are skipped unless --suite k8s-nccl",
        "--k8s",
        "--soperator",
        "soperator-nccl",
        "upgrade-report.md",
    )
    for help_text in (group_help, smoke_help, benchmark_help, deploy_help):
        for stale_term in stale_help_terms:
            assert stale_term.lower() not in help_text.lower()


def test_acceptance_test_benchmark_command_passes_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_run_acceptance_benchmark_command(**kwargs: Any) -> list[Path]:
        captured.update(kwargs)
        return [fake_paths.reports_dir / "acceptance-benchmark-report-mk8s.json"]

    monkeypatch.setattr(
        cli,
        "_run_acceptance_benchmark_command",
        _fake_run_acceptance_benchmark_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "benchmark",
            str(fake_paths.config_path),
            "--target",
            "mk8s",
            "--suite",
            "k8s-nccl",
            "--max-nodes",
            "16",
            "--timeout",
            "30m",
            "--average-bus-bandwidth-threshold-gbps",
            "450.5",
            "--fail-fast",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Acceptance benchmark report:" in _plain_output(result.output)
    assert captured == {
        "config_path": fake_paths.config_path,
        "requested_target_ref": "mk8s",
        "all_targets": False,
        "suites": ["k8s-nccl"],
        "continue_on_failure": False,
        "max_nodes": 16,
        "timeout": "30m",
        "average_bus_bandwidth_threshold_gbps": 450.5,
    }


def test_acceptance_test_benchmark_command_requires_suite(tmp_path: Path) -> None:
    fake_paths = _fake_paths(tmp_path)

    result = runner.invoke(
        cli.app,
        ["acceptance-test", "benchmark", str(fake_paths.config_path)],
    )

    assert result.exit_code == 1, result.output
    output = _plain_output(result.output)
    assert "WARNING:" in output
    assert "acceptance-test benchmark requires --suite" in output
    assert "no default K8s suite is selected" in output
    assert "Choose one of: k8s-nccl, slurm-nccl" in output
    assert "nebius-cxcli acceptance-test benchmark <config.yaml> --suite slurm-nccl" in output


def test_acceptance_test_benchmark_command_prints_skipped_k8s_nccl_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    report_path = fake_paths.reports_dir / "acceptance-benchmark-report-sop-cluster1.json"

    def _fake_run_acceptance_benchmark_command(**_kwargs: Any) -> list[Path]:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "skipped": True,
                    "scope": "k8s-nccl",
                    "target_ref": "sop-cluster1",
                    "skip_reason": (
                        "all Ready GPU nodes already have their GPUs allocated to "
                        "existing workloads"
                    ),
                    "elapsed_seconds": 83.2,
                    "elapsed_time": "00:01:23",
                }
            ),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(
        cli,
        "_run_acceptance_benchmark_command",
        _fake_run_acceptance_benchmark_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "benchmark",
            str(fake_paths.config_path),
            "--target",
            "sop-cluster1",
            "--suite",
            "k8s-nccl",
        ],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    output_flat = " ".join(output.split())
    assert "Acceptance benchmark report:" in output
    assert report_path.name in output
    assert (
        "Acceptance benchmark result: SKIPPED k8s-nccl (sop-cluster1): "
        "all Ready GPU nodes already have their GPUs allocated to existing workloads "
        "(elapsed 00:01:23)"
    ) in output_flat


def test_acceptance_test_benchmark_command_prints_skipped_slurm_nccl_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    report_path = fake_paths.reports_dir / "acceptance-benchmark-report-sop-cluster1.json"

    def _fake_run_acceptance_benchmark_command(**_kwargs: Any) -> list[Path]:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "status": "passed",
                    "scope": "slurm-nccl",
                    "target_ref": "sop-cluster1",
                    "summary": "7/8 Soperator/Slurm checks passed. 1 skipped.",
                    "elapsed_seconds": 83.2,
                    "elapsed_time": "00:01:23",
                    "checks": [
                        {
                            "name": "Soperator manager Deployment",
                            "status": "passed",
                            "passed": True,
                            "summary": "Ready.",
                        },
                        {
                            "name": "Slurm NCCL benchmark",
                            "status": "skipped",
                            "skipped": True,
                            "summary": (
                                "Skipped: fewer than two idle GPU nodes are available for "
                                "a multi-node NCCL benchmark."
                            ),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(
        cli,
        "_run_acceptance_benchmark_command",
        _fake_run_acceptance_benchmark_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "benchmark",
            str(fake_paths.config_path),
            "--target",
            "sop-cluster1",
            "--suite",
            "slurm-nccl",
        ],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    output_flat = " ".join(output.split())
    assert "Acceptance benchmark report:" in output
    assert report_path.name in output
    assert (
        "Acceptance benchmark result: SKIPPED slurm-nccl (sop-cluster1): "
        "Skipped: fewer than two idle GPU nodes are available for a multi-node NCCL benchmark. "
        "(elapsed 00:01:23)"
    ) in output_flat


def test_acceptance_test_benchmark_command_prints_failed_result_before_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    report_path = fake_paths.reports_dir / "acceptance-benchmark-report-mk8s.json"

    def _fake_run_acceptance_benchmark_command(**_kwargs: Any) -> list[Path]:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": False,
                    "scope": "k8s-nccl",
                    "target_ref": "mk8s",
                    "error": "NCCL average bus bandwidth 250.0 Gbps is below threshold 300.0.",
                    "elapsed_seconds": 83.2,
                    "elapsed_time": "00:01:23",
                }
            ),
            encoding="utf-8",
        )
        raise cli._AcceptanceTestRunError("NCCL benchmark failed.", reports=[report_path])

    monkeypatch.setattr(
        cli,
        "_run_acceptance_benchmark_command",
        _fake_run_acceptance_benchmark_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "benchmark",
            str(fake_paths.config_path),
            "--target",
            "mk8s",
            "--suite",
            "k8s-nccl",
        ],
    )

    assert result.exit_code == 1, result.output
    output = _plain_output(result.output)
    output_flat = " ".join(output.split())
    assert "Acceptance benchmark report:" in output
    assert report_path.name in output
    assert (
        "Acceptance benchmark result: FAILED k8s-nccl (mk8s): "
        "NCCL average bus bandwidth 250.0 Gbps is below threshold 300.0. "
        "(elapsed 00:01:23)"
    ) in output_flat
    assert "ERROR: NCCL benchmark failed." in output_flat


def test_acceptance_test_benchmark_command_passes_default_run_options_with_suite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_run_acceptance_benchmark_command(**kwargs: Any) -> list[Path]:
        captured.update(kwargs)
        return [fake_paths.reports_dir / "acceptance-benchmark-report-mk8s.json"]

    monkeypatch.setattr(
        cli,
        "_run_acceptance_benchmark_command",
        _fake_run_acceptance_benchmark_command,
    )

    result = runner.invoke(
        cli.app,
        [
            "acceptance-test",
            "benchmark",
            str(fake_paths.config_path),
            "--suite",
            "k8s-nccl",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "config_path": fake_paths.config_path,
        "requested_target_ref": None,
        "all_targets": False,
        "suites": ["k8s-nccl"],
        "continue_on_failure": True,
        "max_nodes": None,
        "timeout": None,
        "average_bus_bandwidth_threshold_gbps": 300.0,
    }


def test_acceptance_test_benchmark_rejects_unsupported_suite() -> None:
    with pytest.raises(ValueError) as exc_info:
        cli._normalized_acceptance_benchmark_suites(["nccl", "soperator-nccl"])

    message = str(exc_info.value)
    assert "Unsupported --suite value(s): nccl, soperator-nccl" in message
    assert "Supported values: k8s-nccl, slurm-nccl" in message


def test_acceptance_test_runners_require_suite_before_context_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_load_context(_path: Path):
        pytest.fail("acceptance-test runner must reject missing --suite before loading context")

    monkeypatch.setattr(cli, "_load_deploy_context_readonly", _fail_load_context)

    with pytest.raises(cli._AcceptanceSuiteRequiredError) as smoke_exc:
        cli._run_acceptance_smoke_command(
            config_path=tmp_path / "missing.yaml",
            requested_target_ref=None,
            all_targets=False,
            suites=None,
            batch_size=128,
            concurrency=8,
            continue_on_failure=True,
        )
    assert "acceptance-test smoke requires --suite" in str(smoke_exc.value)

    with pytest.raises(cli._AcceptanceSuiteRequiredError) as benchmark_exc:
        cli._run_acceptance_benchmark_command(
            config_path=tmp_path / "missing.yaml",
            requested_target_ref=None,
            all_targets=False,
            suites=[],
            continue_on_failure=True,
            max_nodes=None,
            timeout=None,
            average_bus_bandwidth_threshold_gbps=300.0,
        )
    assert "acceptance-test benchmark requires --suite" in str(benchmark_exc.value)

    with pytest.raises(cli._AcceptanceSuiteRequiredError):
        cli._run_acceptance_benchmark_command(
            config_path=tmp_path / "missing.yaml",
            requested_target_ref=None,
            all_targets=False,
            suites=[""],
            continue_on_failure=True,
            max_nodes=None,
            timeout=None,
            average_bus_bandwidth_threshold_gbps=300.0,
        )


def test_acceptance_test_smoke_suite_names_are_canonical() -> None:
    assert cli._normalized_acceptance_suites(["slurm", "k8s-cuda"]) == {
        "slurm",
        "k8s-cuda",
    }

    with pytest.raises(ValueError, match="Supported values: k8s-cuda, slurm"):
        cli._normalized_acceptance_suites(["soperator-slurm", "cuda", "k8s"])


def test_acceptance_test_benchmark_runner_runs_explicit_k8s_suite_for_all_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster-a"),
                _mk8s_target(fake_paths, target_ref="cluster-b"),
            ],
            "validations": [],
        }
    }
    benchmark_specs = [
        {
            "kind": "mk8s_nccl",
            "target_ref": "cluster-a",
            "name": "K8s NCCL benchmark (cluster-a)",
            "report_file": "acceptance-benchmark-report-cluster-a.json",
        },
        {
            "kind": "mk8s_nccl",
            "target_ref": "cluster-b",
            "name": "K8s NCCL benchmark (cluster-b)",
            "report_file": "acceptance-benchmark-report-cluster-b.json",
        },
    ]
    handoff_calls: list[bool] = []
    benchmark_kwargs: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(cli, "_deploy_report_target_contexts", lambda _paths: {})
    monkeypatch.setattr(
        cli,
        "soperator_acceptance_benchmark_specs",
        lambda *_args, **_kwargs: pytest.fail("k8s-nccl suite must not call Slurm specs"),
    )

    def _fake_mk8s_acceptance_benchmark_validation_specs(*_args: Any, **kwargs: Any):
        benchmark_kwargs.update(kwargs)
        return benchmark_specs

    monkeypatch.setattr(
        cli,
        "mk8s_acceptance_benchmark_validation_specs",
        _fake_mk8s_acceptance_benchmark_validation_specs,
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_prepare_cluster_handoff_kube_env(
        *_args: object,
        set_current_context: bool,
        **_kwargs: object,
    ) -> dict[str, str]:
        handoff_calls.append(set_current_context)
        return {"KUBECONFIG": "/tmp/kubeconfig"}

    monkeypatch.setattr(
        cli, "_prepare_cluster_handoff_kube_env", _fake_prepare_cluster_handoff_kube_env
    )
    monkeypatch.setattr(
        cli,
        "run_mk8s_gpu_validations",
        lambda validations, *, reports_dir, **_kwargs: [
            reports_dir / f"acceptance-benchmark-report-{validations[0]['target_ref']}.json"
        ],
    )

    written = cli._run_acceptance_benchmark_command(
        config_path=fake_paths.config_path,
        requested_target_ref=None,
        all_targets=False,
        suites=["k8s-nccl"],
        continue_on_failure=True,
        max_nodes=None,
        timeout=None,
        average_bus_bandwidth_threshold_gbps=300.0,
    )

    assert written == [
        fake_paths.reports_dir / "acceptance-benchmark-report-cluster-a.json",
        fake_paths.reports_dir / "acceptance-benchmark-report-cluster-b.json",
    ]
    assert benchmark_kwargs == {
        "include_soperator_targets": True,
        "max_nodes": None,
        "timeout": None,
        "average_bus_bandwidth_threshold_gbps": 300.0,
    }
    assert handoff_calls == [False, False]


def test_acceptance_test_benchmark_runner_rejects_same_target_k8s_and_slurm_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths, target_ref="sop-cluster1")],
            "validations": [],
        }
    }
    report_file = "acceptance-benchmark-report-sop-cluster1.json"
    soperator_spec = {
        "kind": cli.SOPERATOR_CLUSTER_VALIDATION_KIND,
        "target_ref": "sop-cluster1",
        "name": "Soperator NCCL benchmark (sop-cluster1)",
        "report_file": report_file,
    }
    k8s_spec = {
        "kind": "mk8s_nccl",
        "target_ref": "sop-cluster1",
        "name": "K8s NCCL benchmark (sop-cluster1)",
        "report_file": report_file,
    }

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "soperator_acceptance_benchmark_specs",
        lambda *_args, **_kwargs: [soperator_spec],
    )
    monkeypatch.setattr(
        cli,
        "mk8s_acceptance_benchmark_validation_specs",
        lambda *_args, **_kwargs: [k8s_spec],
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: pytest.fail("duplicate reports must fail before handoff"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        cli._run_acceptance_benchmark_command(
            config_path=fake_paths.config_path,
            requested_target_ref="sop-cluster1",
            all_targets=False,
            suites=["k8s-nccl", "slurm-nccl"],
            continue_on_failure=True,
            max_nodes=None,
            timeout=None,
            average_bus_bandwidth_threshold_gbps=300.0,
        )

    message = str(exc_info.value)
    assert "same canonical report file" in message
    assert report_file in message
    assert "Soperator NCCL benchmark (sop-cluster1)" in message
    assert "K8s NCCL benchmark (sop-cluster1)" in message


def test_acceptance_test_benchmark_runner_uses_readonly_context_and_kube_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    benchmark_spec = {
        "kind": "mk8s_nccl",
        "target_ref": "mk8s",
        "name": "K8s NCCL benchmark (mk8s)",
        "report_file": "acceptance-benchmark-report-mk8s.json",
    }
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monotonic_values = iter([10.0, 3733.4])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    monkeypatch.setattr(cli, "soperator_acceptance_benchmark_specs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        cli,
        "mk8s_acceptance_benchmark_validation_specs",
        lambda *_args, **_kwargs: [benchmark_spec],
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_run_mk8s_gpu_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        calls.append(("mk8s_gpu", validations, reports_dir, extra_env))
        report_path = reports_dir / "acceptance-benchmark-report-mk8s.json"
        report_path.write_text(
            json.dumps({"passed": True, "scope": "k8s-nccl", "target_ref": "mk8s"}),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    written = cli._run_acceptance_benchmark_command(
        config_path=fake_paths.config_path,
        requested_target_ref="mk8s",
        all_targets=False,
        suites=["k8s-nccl"],
        continue_on_failure=True,
        max_nodes=None,
        timeout=None,
        average_bus_bandwidth_threshold_gbps=None,
    )

    assert written == [fake_paths.reports_dir / "acceptance-benchmark-report-mk8s.json"]
    assert calls == [
        (
            "mk8s_gpu",
            [benchmark_spec],
            fake_paths.reports_dir,
            {"KUBECONFIG": "/tmp/kubeconfig"},
        )
    ]
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["elapsed_seconds"] == pytest.approx(3723.4)
    assert report["elapsed_time"] == "01:02:03"


def test_acceptance_test_benchmark_runner_passes_slurm_nccl_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths, target_ref="sop-cluster1")],
            "validations": [],
        }
    }
    soperator_spec = {
        "kind": cli.SOPERATOR_CLUSTER_VALIDATION_KIND,
        "target_ref": "sop-cluster1",
        "name": "Soperator NCCL benchmark (sop-cluster1)",
        "report_file": "acceptance-benchmark-report-sop-cluster1.json",
    }
    spec_kwargs: dict[str, Any] = {}
    validation_calls: list[list[dict[str, Any]]] = []

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(cli, "_deploy_report_target_contexts", lambda _paths: {})
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    monkeypatch.setattr(
        cli,
        "mk8s_acceptance_benchmark_validation_specs",
        lambda *_args, **_kwargs: [],
    )

    def _fake_soperator_acceptance_benchmark_specs(*_args: Any, **kwargs: Any):
        spec_kwargs.update(kwargs)
        return [soperator_spec]

    monkeypatch.setattr(
        cli,
        "soperator_acceptance_benchmark_specs",
        _fake_soperator_acceptance_benchmark_specs,
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_run_soperator_cluster_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        **_kwargs: Any,
    ) -> list[Path]:
        validation_calls.append(validations)
        return [reports_dir / "acceptance-benchmark-report-sop-cluster1.json"]

    monkeypatch.setattr(
        cli,
        "run_soperator_cluster_validations",
        _fake_run_soperator_cluster_validations,
    )

    written = cli._run_acceptance_benchmark_command(
        config_path=fake_paths.config_path,
        requested_target_ref="sop-cluster1",
        all_targets=False,
        suites=["slurm-nccl"],
        continue_on_failure=True,
        max_nodes=4,
        timeout="20m",
        average_bus_bandwidth_threshold_gbps=300.0,
    )

    assert written == [fake_paths.reports_dir / "acceptance-benchmark-report-sop-cluster1.json"]
    assert spec_kwargs == {
        "max_nodes": 4,
        "timeout": "20m",
        "average_bus_bandwidth_threshold_gbps": 300.0,
    }
    assert validation_calls == [[soperator_spec]]


def test_acceptance_test_benchmark_all_targets_uses_report_context_without_terraform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster-a"),
                _mk8s_target(fake_paths, target_ref="cluster-b"),
            ],
            "validations": [],
        }
    }
    benchmark_specs = [
        {
            "kind": "mk8s_nccl",
            "target_ref": "cluster-a",
            "name": "K8s NCCL benchmark (cluster-a)",
            "report_file": "acceptance-benchmark-report-cluster-a.json",
        },
        {
            "kind": "mk8s_nccl",
            "target_ref": "cluster-b",
            "name": "K8s NCCL benchmark (cluster-b)",
            "report_file": "acceptance-benchmark-report-cluster-b.json",
        },
    ]
    handoff_calls: list[tuple[Mapping[str, Any], bool, bool]] = []
    validation_envs: list[dict[str, str] | None] = []

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (config, fake_paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_deploy_report_target_contexts",
        lambda _paths: {
            "cluster-a": {
                "cluster_id": "mk8scluster-111",
                "kube_context": "nebius-cluster-a-mk8scluster-111-external",
            },
            "cluster-b": {
                "cluster_id": "mk8scluster-222",
                "kube_context": "nebius-cluster-b-mk8scluster-222-external",
            },
        },
    )
    monkeypatch.setattr(
        cli,
        "terraform_output_raw",
        lambda *_args, **_kwargs: pytest.fail(
            "acceptance benchmark must not read Terraform output"
        ),
    )
    monkeypatch.setattr(cli, "soperator_acceptance_benchmark_specs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        cli,
        "mk8s_acceptance_benchmark_validation_specs",
        lambda *_args, **_kwargs: benchmark_specs,
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_prepare_cluster_handoff_kube_env(
        _config: object,
        _paths: ProjectPaths,
        *,
        stack: ExitStack,
        target: Mapping[str, Any],
        persist_local_kubeconfig: bool,
        set_current_context: bool,
        allow_terraform_output: bool,
    ) -> dict[str, str]:
        stack.callback(lambda: None)
        assert persist_local_kubeconfig is True
        handoff_calls.append((target, set_current_context, allow_terraform_output))
        return {"TARGET_REF": str(target["target_ref"])}

    monkeypatch.setattr(
        cli, "_prepare_cluster_handoff_kube_env", _fake_prepare_cluster_handoff_kube_env
    )

    def _fake_run_mk8s_gpu_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        validation_envs.append(extra_env)
        target_ref = validations[0]["target_ref"]
        return [reports_dir / f"acceptance-benchmark-report-{target_ref}.json"]

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    written = cli._run_acceptance_benchmark_command(
        config_path=fake_paths.config_path,
        requested_target_ref=None,
        all_targets=True,
        suites=["k8s-nccl"],
        continue_on_failure=True,
        max_nodes=None,
        timeout=None,
        average_bus_bandwidth_threshold_gbps=None,
    )

    assert written == [
        fake_paths.reports_dir / "acceptance-benchmark-report-cluster-a.json",
        fake_paths.reports_dir / "acceptance-benchmark-report-cluster-b.json",
    ]
    assert handoff_calls == [
        (
            {
                **_mk8s_target(fake_paths, target_ref="cluster-a"),
                "cluster_id": "mk8scluster-111",
                "kube_context": "nebius-cluster-a-mk8scluster-111-external",
            },
            False,
            False,
        ),
        (
            {
                **_mk8s_target(fake_paths, target_ref="cluster-b"),
                "cluster_id": "mk8scluster-222",
                "kube_context": "nebius-cluster-b-mk8scluster-222-external",
            },
            False,
            False,
        ),
    ]
    assert validation_envs == [{"TARGET_REF": "cluster-a"}, {"TARGET_REF": "cluster-b"}]


def test_deploy_footer_groups_target_validations_and_keeps_paths_concise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.reports_dir.mkdir(parents=True, exist_ok=True)
    validations = [
        {
            "kind": "mk8s_gpu_operator_readiness",
            "name": "GPU stack readiness (cluster1)",
            "report_file": "deploy-gpu-stack-readiness-report-cluster1.json",
            "target_ref": "cluster1",
        },
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU visibility probe (cluster1)",
            "report_file": "deploy-gpu-visibility-report-cluster1.json",
            "target_ref": "cluster1",
        },
        {
            "kind": "mk8s_gpu_visibility",
            "name": "GPU visibility probe (cluster2)",
            "report_file": "deploy-gpu-visibility-report-cluster2.json",
            "target_ref": "cluster2",
        },
    ]
    (fake_paths.reports_dir / "deploy-gpu-stack-readiness-report-cluster1.json").write_text(
        json.dumps(
            {
                "passed": True,
                "target_ref": "cluster1",
                "gpu_operator": {"gpu_nodes": [{"name": "gpu-a"}, {"name": "gpu-b"}]},
                "network_operator": {"required": False},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (fake_paths.reports_dir / "deploy-gpu-visibility-report-cluster1.json").write_text(
        json.dumps(
            {
                "passed": True,
                "target_ref": "cluster1",
                "selected_node_count": 2,
                "total_gpu_node_count": 4,
                "passed_node_count": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (fake_paths.reports_dir / "deploy-gpu-visibility-report-cluster2.json").write_text(
        json.dumps(
            {
                "passed": True,
                "target_ref": "cluster2",
                "selected_node_count": 1,
                "total_gpu_node_count": 1,
                "passed_node_count": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = cli.build_deploy_validation_report(
        validations,
        reports_dir=fake_paths.reports_dir,
    )
    printed: list[str] = []

    monkeypatch.setattr(cli, "wireguard_access_command_hints", lambda *_args: [])
    monkeypatch.setattr(cli, "ssh_jump_access_hints", lambda *_args: [])
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message="", *args, **kwargs: printed.append(str(message)),
    )

    cli._print_deploy_command_footer(
        {},
        fake_paths,
        cli.DeployRunSummary(validation_report=report),
        succeeded=True,
    )

    assert printed == [
        "",
        "[bold]Deployment summary[/bold]",
        "[bright_magenta]Validation:[/bright_magenta]",
        "  Overall: [green]PASS[/green] (3/3 completed, 0 not run)",
        "  cluster1:",
        "    [green]PASS[/green] GPU stack readiness: GPU Operator ready on 2 GPU nodes.",
        (
            "    [green]PASS[/green] GPU visibility probe: 2/2 selected node(s) "
            "passed; total Ready GPU nodes 4."
        ),
        "  cluster2:",
        (
            "    [green]PASS[/green] GPU visibility probe: 1/1 selected node(s) "
            "passed; total Ready GPU nodes 1."
        ),
        "[bright_magenta]Copy/paste commands:[/bright_magenta]",
        "No immediate access or follow-up commands were derived.",
        "[bright_magenta]Important paths:[/bright_magenta]",
        f"  Generated bundle: {fake_paths.generated_dir}",
        f"  Validation detail reports: {fake_paths.reports_dir}",
        f"  Deploy report: {fake_paths.reports_dir / 'deploy-report.md'}",
        "[green]Deploy completed[/green]",
    ]
    assert all("Validation JSON:" not in line for line in printed)
    assert all("Generated manifest:" not in line for line in printed)


def test_deploy_command_prints_ssh_jumphost_access_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1", "render": {"module_sources": []}}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )
    monkeypatch.setattr(
        cli,
        "ssh_jump_access_hints",
        lambda _config, _paths: [
            {
                "target_label": "vm",
                "jump_host_label": "ssh-jumphost",
                "command": "ssh -J admin@198.51.100.20 ubuntu@10.0.0.15",
            }
        ],
    )

    result = runner.invoke(cli.app, ["deploy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    lines = output.splitlines()
    assert "Copy/paste commands:" in output
    assert "# SSH vm via ssh-jumphost" in output
    assert "ssh -J admin@198.51.100.20 ubuntu@10.0.0.15" in output
    assert "# SSH vm via ssh-jumphost" in lines
    assert "ssh -J admin@198.51.100.20 ubuntu@10.0.0.15" in lines


def test_deploy_command_prints_wireguard_access_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )
    monkeypatch.setattr(
        cli,
        "wireguard_access_command_hints",
        lambda _config, _paths: [
            {
                "label": "WireGuard connect laptop",
                "command": "wg-quick up /tmp/laptop.conf",
            },
            {
                "label": "WireGuard disconnect laptop",
                "command": "wg-quick down /tmp/laptop.conf",
            },
        ],
    )

    result = runner.invoke(cli.app, ["deploy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    lines = output.splitlines()
    assert "# WireGuard connect laptop" in output
    assert "wg-quick up /tmp/laptop.conf" in output
    assert "# WireGuard disconnect laptop" in output
    assert "wg-quick down /tmp/laptop.conf" in output
    assert "# WireGuard connect laptop" in lines
    assert "wg-quick up /tmp/laptop.conf" in lines
    assert "# WireGuard disconnect laptop" in lines
    assert "wg-quick down /tmp/laptop.conf" in lines


def test_deploy_command_prints_wireguard_generation_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )
    monkeypatch.setattr(
        cli,
        "wireguard_access_command_hints",
        lambda _config, _paths: [
            {
                "label": "Generate WireGuard client config for wireguard-gw",
                "command": f"nebius-cxcli wireguard --gen-client-conf {fake_paths.config_path}",
            },
        ],
    )

    result = runner.invoke(cli.app, ["deploy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    lines = output.splitlines()
    assert "# Generate WireGuard client config for wireguard-gw" in output
    assert f"nebius-cxcli wireguard --gen-client-conf {fake_paths.config_path}" in output
    assert "# Generate WireGuard client config for wireguard-gw" in lines
    assert f"nebius-cxcli wireguard --gen-client-conf {fake_paths.config_path}" in lines
    assert "--component wireguard-gw" not in output


def test_deploy_footer_styles_copy_paste_commands_not_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    rich_console = cli.Console(
        force_terminal=True,
        color_system="truecolor",
        width=220,
        record=True,
    )
    monkeypatch.setattr(cli, "console", rich_console)
    monkeypatch.setattr(
        cli,
        "wireguard_access_command_hints",
        lambda _config, _paths: [
            {
                "label": "WireGuard connect laptop",
                "command": "wg-quick up /tmp/laptop.conf",
            }
        ],
    )
    monkeypatch.setattr(cli, "ssh_jump_access_hints", lambda *_args: [])

    cli._print_deploy_command_footer({}, fake_paths, cli.DeployRunSummary(), succeeded=True)

    rendered = rich_console.export_text(styles=True)
    plain_rendered = _ANSI_ESCAPE_RE.sub("", rendered)
    assert "# WireGuard connect laptop" in plain_rendered
    assert "wg-quick up /tmp/laptop.conf" in plain_rendered
    _assert_not_copy_paste_command_styled(rendered, "# WireGuard connect laptop")
    _assert_copy_paste_command_styled(rendered, "wg-quick up /tmp/laptop.conf")


def test_deploy_command_passes_one_run_validation_skip_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
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
            "gpu-visibility",
            "--skip-validation",
            "observability-ingestion",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["skip_validations"] is False
    assert captured["skip_validation_kinds"] == {
        "mk8s_gpu_visibility",
        "mk8s_observability_ingestion",
    }


def test_deploy_command_rejects_unknown_one_run_validation_skip_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )

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


def test_deploy_command_rejects_legacy_cuda_smoke_skip_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )

    result = runner.invoke(
        cli.app,
        [
            "deploy",
            str(fake_paths.config_path),
            "--skip-validation",
            "cuda-smoke",
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported --skip-validation value(s): cuda-smoke" in result.output


def test_deploy_command_rejects_benchmark_nccl_skip_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )

    result = runner.invoke(
        cli.app,
        [
            "deploy",
            str(fake_paths.config_path),
            "--skip-validation",
            "nccl",
        ],
    )

    assert result.exit_code != 0
    output = _plain_output(result.output)
    assert "Unsupported --skip-validation value(s): nccl" in output
    assert "operator-readiness, gpu-visibility, observability-ingestion" in output


def test_deploy_validation_filter_preserves_stale_nccl_for_fail_fast_guard() -> None:
    validations = [
        {"kind": "mk8s_gpu_operator_readiness"},
        {"kind": "mk8s_nccl"},
        {"kind": "custom_future_validation"},
    ]

    filtered = cli._filter_deploy_validations(
        validations,
        skip_validations=False,
        skip_kinds=set(),
    )

    assert [item["kind"] for item in filtered] == [
        "mk8s_gpu_operator_readiness",
        "mk8s_nccl",
        "custom_future_validation",
    ]


@pytest.mark.parametrize("skip_validations", [False, True])
def test_deploy_generated_artifacts_rejects_unknown_validation_kind_before_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    skip_validations: bool,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {
        "deploy": {
            "targets": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "target_ref": "mk8s",
                    "access": "external",
                    "cluster_id_output_name": "mk8s_cluster_id",
                }
            ],
            "validations": [{"kind": "custom_future_validation", "target_ref": "mk8s"}],
        }
    }
    calls: list[str] = []

    monkeypatch.setattr(
        cli, "_run_deploy_preflight", lambda *_args, **_kwargs: calls.append("preflight")
    )

    with pytest.raises(RuntimeError) as exc_info:
        cli._deploy_generated_artifacts(
            {},
            fake_paths,
            manifest,
            skip_validations=skip_validations,
            skip_validation_kinds=set(),
            requested_target_ref="mk8s",
        )

    assert "custom_future_validation" in str(exc_info.value)
    assert calls == []


def test_deploy_command_rejects_required_cluster_smoke_skip_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )

    result = runner.invoke(
        cli.app,
        [
            "deploy",
            str(fake_paths.config_path),
            "--skip-validation",
            "cluster-smoke",
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported --skip-validation value(s): cluster-smoke" in result.output


def test_deploy_command_accepts_config_yaml_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    captured: dict[str, Any] = {}

    def _fake_load(target: Path) -> tuple[object, ProjectPaths, dict[str, str]]:
        captured["target"] = target
        return "cfg", fake_paths, manifest

    monkeypatch.setattr(cli, "_load_deploy_context", _fake_load)
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *args, **kwargs: cli.DeployRunSummary()
    )

    result = runner.invoke(cli.app, ["deploy", str(fake_paths.config_path)])

    assert result.exit_code == 0, result.output
    assert captured["target"] == fake_paths.config_path
    output = _plain_output(result.output)
    assert "Deploy completed" in output
    assert "Deploy completed from" not in output
    assert f"Validation detail reports: {fake_paths.reports_dir}" in output
    assert f"Deploy report: {fake_paths.reports_dir / 'deploy-report.md'}" in output


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
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_destroy_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)

    def _fake_destroy_generated_artifacts(
        config: object,
        paths: object,
        loaded_manifest: object,
        *,
        yes: bool = False,
    ) -> None:
        captured["config"] = config
        captured["paths"] = paths
        captured["manifest"] = loaded_manifest
        captured["yes"] = yes

    monkeypatch.setattr(cli, "_destroy_generated_artifacts", _fake_destroy_generated_artifacts)

    result = runner.invoke(
        cli.app,
        ["destroy", str(fake_paths.config_path), "--yes"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Local destroy completed from" in output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "manifest": manifest,
        "yes": True,
    }


def test_destroy_command_accepts_config_yaml_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    captured: dict[str, Any] = {}

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
    captured: dict[str, Any] = {}
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


def test_destroy_command_confirmation_deletes_apps_before_cluster_destroy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
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
        "manifest by deleting rendered app resources from the handed-off MK8s target first "
        "so Kubernetes finalizers and CSI cleanup can run, then running Terraform destroy "
        f"against the rendered infra bundle under {fake_paths.infra_dir}. This generated bundle "
        "still destroys the handed-off MK8s cluster directly after app teardown."
    )


def test_destroy_command_confirmation_deletes_flux_first_for_external_cluster_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}}
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_external_mk8s_target(fake_paths)]},
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
        == "Continue and delete rendered app resources and destroy only cxcli-owned infra?"
    )
    assert captured["warning_text"] == (
        "Destroy will delete the rendered app resources from the external MK8s target first. "
        "The existing MK8s cluster and node groups are external to cxcli and will not be "
        f"destroyed. Any cxcli-managed infra under {fake_paths.infra_dir} is still destroyed "
        "after app teardown."
    )


def test_run_deploy_preflight_runs_strict_quota_backend_terraform_and_flux_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = _config_with_enabled_mk8s(charts=[{"id": "gateway-helm", "enabled": True}])
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: calls.append(
            ("strict", config, include_common_checks)
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
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
        lambda config: calls.append(("backend", config)),
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
        manifest={"render": {"module_sources": []}},
    )

    assert calls == [
        ("strict", config, False),
        ("mk8s", config),
        ("backend", config),
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


def test_run_deploy_preflight_runs_mk8s_gpu_stack_compatibility_when_targeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = _config_with_enabled_mk8s()
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: calls.append(
            ("strict", config, include_common_checks)
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
        lambda config: calls.append(("mk8s", config)),
    )
    monkeypatch.setattr(
        cli,
        "has_mk8s_gpu_stack_compatibility_preflight_targets",
        lambda config: True,
    )
    monkeypatch.setattr(
        cli,
        "validate_mk8s_gpu_stack_compatibility_preflight",
        lambda config: calls.append(("gpu-stack", config)),
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
        lambda config: calls.append(("backend", config)),
    )
    monkeypatch.setattr(
        cli,
        "_terraform_runtime_env",
        lambda config: calls.append(("runtime_env", config)) or {"TF_VAR_DEMO": "1"},
    )
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: calls.append(
            ("validate", infra_dir, extra_env, initialize)
        ),
    )

    cli._run_deploy_preflight(
        config,
        fake_paths,
        manifest={"render": {"module_sources": []}},
    )

    assert calls == [
        ("strict", config, False),
        ("mk8s", config),
        ("gpu-stack", config),
        ("backend", config),
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


def test_run_deploy_preflight_skips_flux_validation_when_no_apps_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = _config_with_enabled_mk8s()
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: calls.append(
            ("strict", config, include_common_checks)
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
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
        lambda config: calls.append(("backend", config)),
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
        manifest={"render": {"module_sources": []}},
    )

    assert calls == [
        ("strict", config, False),
        ("mk8s", config),
        ("backend", config),
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


def _mysterybox_first_deploy_config(
    *,
    instance_id: str = "mysterybox",
    module_name: str | None = None,
    version_id: str = "n/a",
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "parent_id": "project-123",
        "secrets": [
            {
                "name": "db-username-password",
                "version_id": version_id,
                "payload": {
                    "USERNAME": {"type": "text"},
                    "PASSWORD": {"type": "text"},
                },
            },
            {
                "name": "secret2",
                "version_id": version_id,
                "payload": {
                    "MYKEY": {"type": "text"},
                },
            },
        ],
    }
    if module_name is not None:
        inputs["module_name"] = module_name
    return {
        "infra": {
            "components": [
                {
                    "id": "mysterybox",
                    "instance_id": instance_id,
                    "enabled": True,
                    "inputs": inputs,
                }
            ]
        }
    }


def test_mysterybox_runtime_payload_requirements_use_rendered_variable_names() -> None:
    config = _mysterybox_first_deploy_config(instance_id="secretstore-alpha")

    assert cli._mysterybox_runtime_payload_requirements(config) == {
        "TF_VAR_secretstore_alpha_payload_values": [
            ("db-username-password", "USERNAME"),
            ("db-username-password", "PASSWORD"),
            ("secret2", "MYKEY"),
        ]
    }


def test_mysterybox_runtime_payload_values_preflight_reports_missing_values() -> None:
    config = _mysterybox_first_deploy_config()

    with pytest.raises(RuntimeError) as exc_info:
        cli._validate_mysterybox_runtime_payload_values(config, environ={})

    message = str(exc_info.value)
    assert "Missing MysteryBox runtime payload values for first deploy" in message
    assert "TF_VAR_mysterybox_payload_values" in message
    assert "db-username-password.PASSWORD" in message
    assert "db-username-password.USERNAME" in message
    assert "secret2.MYKEY" in message
    assert "config.yaml or generated artifacts" in message
    assert (
        'export TF_VAR_mysterybox_payload_values=\'{"db-username-password": '
        '{"PASSWORD": "<value>", "USERNAME": "<value>"}, "secret2": {"MYKEY": "<value>"}}\''
        in message
    )


def test_mysterybox_runtime_payload_values_preflight_accepts_json_values() -> None:
    config = _mysterybox_first_deploy_config()

    cli._validate_mysterybox_runtime_payload_values(
        config,
        environ={
            "TF_VAR_mysterybox_payload_values": json.dumps(
                {
                    "db-username-password": {
                        "USERNAME": "alice",
                        "PASSWORD": "secret",
                    },
                    "secret2": {
                        "MYKEY": "token",
                    },
                }
            )
        },
    )


def test_mysterybox_runtime_payload_values_prompt_collects_hidden_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _mysterybox_first_deploy_config()
    answers = iter(["db-user", "db-password", "api-token"])
    prompts: list[tuple[str, dict[str, Any]]] = []

    def _fake_prompt(prompt_text: str, **kwargs: object) -> str:
        prompts.append((prompt_text, kwargs))
        return next(answers)

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    collected = cli._collect_mysterybox_runtime_payload_values(
        config,
        environ={},
        prompt=True,
    )

    assert json.loads(collected["TF_VAR_mysterybox_payload_values"]) == {
        "db-username-password": {
            "USERNAME": "db-user",
            "PASSWORD": "db-password",
        },
        "secret2": {
            "MYKEY": "api-token",
        },
    }
    assert [prompt for prompt, _kwargs in prompts] == [
        "MysteryBox payload value for db-username-password.USERNAME",
        "MysteryBox payload value for db-username-password.PASSWORD",
        "MysteryBox payload value for secret2.MYKEY",
    ]
    assert all(kwargs["hide_input"] is True for _prompt, kwargs in prompts)
    assert all(kwargs["show_default"] is False for _prompt, kwargs in prompts)


def test_mysterybox_runtime_payload_values_preflight_skips_recorded_versions() -> None:
    config = _mysterybox_first_deploy_config(version_id="mbsecver-e00abc123")

    cli._validate_mysterybox_runtime_payload_values(config, environ={})


def test_run_deploy_preflight_validates_mysterybox_payloads_before_live_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = _mysterybox_first_deploy_config()
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: calls.append(
            ("strict", config, include_common_checks)
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
        lambda _config: calls.append(("mk8s",)),
    )
    monkeypatch.delenv("TF_VAR_mysterybox_payload_values", raising=False)

    with pytest.raises(RuntimeError, match="TF_VAR_mysterybox_payload_values"):
        cli._run_deploy_preflight(
            config,
            fake_paths,
            manifest={"render": {"module_sources": []}},
        )

    assert calls == [("strict", config, False)]


def test_run_deploy_preflight_prompts_for_mysterybox_values_before_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    config = _mysterybox_first_deploy_config()
    payload_env = {"TF_VAR_mysterybox_payload_values": '{"secret2":{"MYKEY":"token"}}'}
    events: list[Any] = []

    class _FakeProgress:
        def __init__(self, *, title, phases):
            events.append(("progress_init", title, tuple(phase.key for phase in phases)))

        def __enter__(self):
            events.append("progress_enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("progress_exit")

        def run(self, phase_key, fn):
            events.append(("progress_run", phase_key))
            return fn()

    def _fake_collect(config, *, environ=None, prompt=False):
        events.append(("collect", prompt))
        return payload_env

    def _fake_validate_payload(config, *, environ=None):
        events.append(("validate_payload", dict(environ or {})))

    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)
    monkeypatch.setattr(cli, "_ValidationProgress", _FakeProgress)
    monkeypatch.setattr(cli, "_collect_mysterybox_runtime_payload_values", _fake_collect)
    monkeypatch.setattr(
        cli,
        "_validate_mysterybox_runtime_payload_values",
        _fake_validate_payload,
    )
    monkeypatch.setattr(
        cli,
        "_validate_strict_config",
        lambda config, *, include_common_checks=False: events.append(("strict", config)),
    )
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
        lambda config: events.append(("mk8s", config)),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config: events.append("backend"),
    )
    monkeypatch.setattr(
        cli,
        "_terraform_runtime_env",
        lambda config: events.append(("runtime_env", config)) or {"TF_VAR_DEMO": "1"},
    )
    monkeypatch.setattr(
        cli,
        "_raise_on_generated_bundle_live_quota_issues",
        lambda config, paths, *, manifest, runtime_env, phase: events.append(
            ("quota", runtime_env, phase)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_validate_generated_mk8s_resource_name_preflight",
        lambda config, paths, *, runtime_env: events.append(("mk8s_name", runtime_env)),
    )
    monkeypatch.setattr(
        cli,
        "terraform_validate",
        lambda infra_dir, *, extra_env=None, initialize=True: events.append(
            ("terraform_validate", extra_env, initialize)
        ),
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    returned_env = cli._run_deploy_preflight(
        config,
        fake_paths,
        manifest={"render": {"module_sources": []}},
    )

    assert returned_env == payload_env
    assert events[0] == ("collect", True)
    assert events[1][0] == "progress_init"
    assert ("progress_run", "mysterybox-payload-values") in events
    assert any(
        event[0] == "validate_payload"
        and event[1]["TF_VAR_mysterybox_payload_values"]
        == payload_env["TF_VAR_mysterybox_payload_values"]
        for event in events
        if isinstance(event, tuple)
    )


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
                    "(regular-vm slots, fabric fabric-4, converted to GPU units)"
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
    assert (
        cli.copy_paste_command_markup(f"nebius-cxcli quota-request {fake_paths.config_path}")
        in rendered_messages
    )
    assert (
        cli.copy_paste_command_markup(
            f"nebius-cxcli quota-check --all-regions {fake_paths.config_path}"
        )
        in rendered_messages
    )


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
                    "(regular-vm slots, fabric fabric-4, converted to GPU units)"
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
    captured: dict[str, Any] = {}
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
        "gpu_clusters": {"state": {"infiniband_fabric": "fabric-6"}},
        "node_groups": {
            "state-0": {
                "boot_disk": {
                    "size_gibibytes": 200,
                    "type": "NETWORK_SSD",
                },
                "gpu": True,
                "gpu_cluster_key": "state",
                "node_count": 2,
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
            }
        },
    }


def test_validate_generated_mk8s_resource_name_preflight_skips_when_no_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []
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
                        "cluster": {
                            "parent_id": "project-123",
                            "cluster_name": "cluster-a",
                        },
                        "node_groups": {
                            "worker": {
                                "node_count": 1,
                                "gpu": True,
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                                "gpu_cluster_key": "workers",
                            }
                        },
                        "gpu_clusters": {"workers": {"infiniband_fabric": "fabric-1"}},
                    },
                }
            ]
        },
    }
    captured: dict[str, Any] = {}

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
    config = {
        "infra": {"components": [{"id": "mk8s", "instance_id": "mk8s", "enabled": True}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True, "instance_id": "mk8s"}]},
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, manifest=None: calls.append(
            ("preflight", config, paths, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True, run_mk8s_preflight=True, **_kwargs: calls.append(
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
        lambda paths, *, extra_env=None, **_kwargs: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None, **_kwargs: calls.append(
            ("warn_bootstrap", config, paths, extra_env, target_ref)
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.reports_dir / "deploy-report.md")
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("preflight", config, fake_paths, manifest),
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


def test_deploy_generated_artifacts_external_target_skips_terraform_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True, "instance_id": "mk8s"}]}}
    manifest = {
        "deploy": {
            "targets": [_external_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    calls: list[tuple[Any, ...]] = []
    messages: list[str] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, manifest=None: calls.append(
            ("preflight", config, paths, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *_args, **_kwargs: calls.append(("apply_with_status",)),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            calls.append(("kube_env", target)) or {"KUBECONFIG": "/tmp/kubeconfig"}
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
        lambda paths, *, extra_env=None, **_kwargs: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.reports_dir / "deploy-report.md")
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: messages.append(str(message)),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert ("apply_with_status",) not in calls
    assert calls[:3] == [
        ("preflight", config, fake_paths, manifest),
        ("inventory", config, fake_paths),
        ("kube_env", _external_mk8s_target(fake_paths)),
    ]
    assert ("cluster_status", {"KUBECONFIG": "/tmp/kubeconfig"}) in calls
    assert ("flux", _target_paths(fake_paths), {"KUBECONFIG": "/tmp/kubeconfig"}) in calls
    assert any("skipping Terraform apply" in item for item in messages)


def test_deploy_generated_artifacts_recovers_mysterybox_versions_after_apply_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {"deploy": {"targets": [], "validations": []}}
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, manifest=None: calls.append(("preflight", manifest)) or {},
    )

    def _fake_apply(config, paths, **kwargs):
        calls.append(("apply", kwargs))
        raise RuntimeError("terraform failed")

    def _fake_sync(config, paths, *, initialize=True, require_all=True):
        calls.append(("sync", initialize, require_all))
        return True

    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", _fake_apply)
    monkeypatch.setattr(cli, "_sync_mysterybox_primary_version_ids_to_config", _fake_sync)
    monkeypatch.setattr(cli.console, "print", lambda message: calls.append(("print", message)))

    with pytest.raises(RuntimeError, match="terraform failed"):
        cli._deploy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            skip_validations=False,
            skip_validation_kinds=set(),
        )

    assert calls == [
        ("preflight", manifest),
        (
            "apply",
            {
                "initialize": False,
                "manifest": manifest,
                "run_mk8s_preflight": False,
            },
        ),
        ("sync", False, False),
        (
            "print",
            "Recovered MysteryBox primary version_id values from Terraform state; "
            "retry deploy to continue from the refreshed generated bundle.",
        ),
    ]


def test_collect_grafana_status_after_flux_waits_until_url_is_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    sleeps: list[float] = []
    printed: list[str] = []

    def _fake_collect(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], ...]:
        attempts.append("attempt")
        if len(attempts) == 1:
            return ({"target_ref": "cluster2", "base_url": ""},)
        return ({"target_ref": "cluster2", "base_url": "http://203.0.113.10/"},)

    monotonic_values = iter([100.0, 100.0])
    monkeypatch.setattr(cli, "grafana_enabled_for_target", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "collect_grafana_runtime_status", _fake_collect)
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: printed.append(str(message)),
    )

    statuses = cli._collect_grafana_status_after_flux(
        {},
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        target_ref="cluster2",
        timeout_seconds=30.0,
        poll_interval_seconds=5.0,
    )

    assert statuses == ({"target_ref": "cluster2", "base_url": "http://203.0.113.10/"},)
    assert sleeps == [5.0]
    assert printed == ["Waiting for Grafana Gateway/LoadBalancer address for cluster2..."]


def test_collect_grafana_status_after_flux_returns_pending_status_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[str] = []

    monkeypatch.setattr(cli, "grafana_enabled_for_target", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "collect_grafana_runtime_status",
        lambda *_args, **_kwargs: ({"target_ref": "cluster2", "base_url": ""},),
    )
    monotonic_values = iter([100.0, 102.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: printed.append(str(message)),
    )

    statuses = cli._collect_grafana_status_after_flux(
        {},
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        target_ref="cluster2",
        timeout_seconds=1.0,
        poll_interval_seconds=5.0,
    )

    assert statuses == ({"target_ref": "cluster2", "base_url": ""},)
    assert any("Grafana URL for cluster2 is still pending" in message for message in printed)


def test_deploy_generated_artifacts_without_apps_still_prepares_kube_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [],
        }
    }
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, manifest=None: calls.append(
            ("preflight", config, paths, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True, run_mk8s_preflight=True, **_kwargs: calls.append(
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
        lambda paths, *, extra_env=None, **_kwargs: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, **_kwargs: calls.append(
            ("warn_bootstrap", config, paths, extra_env)
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.reports_dir / "deploy-report.md")
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("preflight", config, fake_paths, manifest),
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
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths),
                _mk8s_target(fake_paths, target_ref="mk8s-2"),
            ],
            "validations": [],
        }
    }
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, manifest=None: calls.append(
            ("preflight", config, paths, manifest)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True, run_mk8s_preflight=True, **_kwargs: calls.append(
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
        lambda paths, *, extra_env=None, **_kwargs: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.reports_dir / "deploy-report.md")
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("preflight", config, fake_paths, manifest),
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


def test_deploy_generated_artifacts_defaults_multi_target_apps_to_all_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s(
        charts=[
            {"id": "gateway-helm", "enabled": True, "instance_id": "cluster1"},
            {"id": "gateway-helm", "enabled": True, "instance_id": "cluster2"},
        ]
    )
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster1"),
                _mk8s_target(fake_paths, target_ref="cluster2"),
            ],
            "validations": [],
        }
    }
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_run_deploy_preflight",
        lambda config, paths, *, manifest=None: calls.append(
            ("preflight", config, paths, manifest)
        ),
    )
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths, kwargs.get("validations")))
            or SimpleNamespace(markdown=paths.reports_dir / "deploy-report.md")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            calls.append(
                (
                    "kube_env",
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": f"/tmp/{cast(Mapping[str, Any], target)['target_ref']}.kubeconfig"}
        ),
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None, **_kwargs: calls.append(
            ("flux", paths.flux_dir, extra_env)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None, **_kwargs: calls.append(
            ("warn_bootstrap", target_ref, paths.flux_dir, extra_env)
        ),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert ("preflight", config, fake_paths, manifest) in calls
    assert (
        "kube_env",
        _mk8s_target(fake_paths, target_ref="cluster1"),
        True,
        False,
    ) in calls
    assert (
        "kube_env",
        _mk8s_target(fake_paths, target_ref="cluster2"),
        True,
        False,
    ) in calls
    assert (
        "flux",
        flux_target_dir(fake_paths, "cluster1"),
        {"KUBECONFIG": "/tmp/cluster1.kubeconfig"},
    ) in calls
    assert (
        "flux",
        flux_target_dir(fake_paths, "cluster2"),
        {"KUBECONFIG": "/tmp/cluster2.kubeconfig"},
    ) in calls


def test_deploy_generated_artifacts_prints_mk8s_gpu_warning_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": []}}
    manifest = {
        "runtime_config": config,
        "deploy": {"targets": [], "validations": []},
    }
    printed: list[str] = []

    monkeypatch.setattr(
        cli,
        "mk8s_gpu_validation_warnings",
        lambda _config: ("deploy warning only once",),
    )

    def _fake_preflight(config, paths, *, manifest=None):
        cli._print_mk8s_gpu_validation_warnings(config)
        return {}

    monkeypatch.setattr(cli, "_run_deploy_preflight", _fake_preflight)
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: SimpleNamespace(
            markdown=paths.reports_dir / "deploy-report.md"
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message="", *args, **kwargs: printed.append(str(message)),
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert sum("deploy warning only once" in item for item in printed) == 1


def test_deploy_generated_artifacts_runs_manifest_gpu_validations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU visibility probe",
                    "namespace": "gpu-validation",
                    "target_ref": "mk8s",
                }
            ],
        }
    }
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: (
            calls.append(("inventory", config, paths))
            or SimpleNamespace(markdown=paths.reports_dir / "deploy-report.md")
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
        lambda validations, *, reports_dir, extra_env, emit=None: (
            calls.append(("gpu_validations", validations, reports_dir, extra_env))
            or [reports_dir / "deploy-gpu-visibility-report.json"]
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
                    "name": "GPU visibility probe",
                    "namespace": "gpu-validation",
                    "target_ref": "mk8s",
                }
            ],
            fake_paths.reports_dir,
            {"KUBECONFIG": "/tmp/kubeconfig"},
        ),
        ("inventory", config, fake_paths),
    ]


def test_deploy_generated_artifacts_runs_cpu_cluster_smoke_before_app_flux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s(
        charts=[
            {
                "id": "grafana",
                "instance_id": "mk8s",
                "target_ref": "mk8s",
                "enabled": True,
            }
        ]
    )
    cluster_smoke_validation = {
        "kind": "mk8s_cluster_smoke",
        "name": "MK8s node inventory smoke",
        "report_file": "cluster-inventory-report.json",
        "target_ref": "mk8s",
        "required": True,
        "expect_gpu_nodes": False,
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [cluster_smoke_validation],
        }
    }
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda _config, paths, **_kwargs: SimpleNamespace(
            markdown=paths.reports_dir / "deploy-report.md"
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)
    monkeypatch.setattr(
        cli, "_reconcile_observability_gpu_node_labels", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        cli, "_ensure_mysterybox_eso_runtime_before_flux", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(cli, "_ensure_grafana_runtime_before_flux", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "_ensure_soperator_notifier_runtime_before_flux", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        cli, "_ensure_soperator_backup_runtime_before_flux", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None, **_kwargs: calls.append(("flux", paths)),
    )
    monkeypatch.setattr(
        cli, "_ensure_soperator_gpu_ephemeral_bootstrap_power_state", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(cli, "_collect_grafana_status_after_flux", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_warn_if_flux_gitops_not_bootstrapped", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)

    def _fake_run_mk8s_gpu_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert validations == [cluster_smoke_validation]
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        calls.append(("validation", validations))
        return [reports_dir / "cluster-inventory-report.json"]

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert calls == [
        ("validation", [cluster_smoke_validation]),
        ("flux", _target_paths(fake_paths)),
    ]


def test_deploy_generated_artifacts_rejects_manifest_missing_deploy_validations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
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
            skip_validations=False,
            skip_validation_kinds=set(),
        )


def test_deploy_generated_artifacts_rejects_manifest_missing_deploy_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {"schema": "nebius-cxcli-generated/v1", "render": {"module_sources": []}}
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)

    with pytest.raises(
        RuntimeError,
        match="Generated manifest is missing deploy\\.validations metadata",
    ):
        cli._deploy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            skip_validations=False,
            skip_validation_kinds=set(),
        )


def test_deploy_generated_artifacts_updates_validation_spinner_when_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_operator_readiness",
                    "name": "GPU stack readiness",
                    "namespace": "gpu-operator",
                    "report_file": "deploy-gpu-stack-readiness-report.json",
                    "target_ref": "mk8s",
                },
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU visibility probe",
                    "namespace": "gpu-validation",
                    "report_file": "deploy-gpu-visibility-report.json",
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
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert validations == manifest["deploy"]["validations"]
        assert reports_dir == fake_paths.reports_dir
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert emit is not None
        reports_dir.mkdir(parents=True, exist_ok=True)
        emit("Starting validation 1/2: GPU stack readiness.")
        emit("[bold white]GPU Operator[/bold white] [dim][5s][/dim] clusterpolicy state=ready")
        (reports_dir / "deploy-gpu-stack-readiness-report.json").write_text(
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
        emit("Starting validation 2/2: GPU visibility probe.")
        emit("[bold white]GPU visibility[/bold white] [dim][9s][/dim] pods 3/3 Succeeded")
        (reports_dir / "deploy-gpu-visibility-report.json").write_text(
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
            reports_dir / "deploy-gpu-stack-readiness-report.json",
            reports_dir / "deploy-gpu-visibility-report.json",
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
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert status_start == [("[cyan]Running deploy-time validations for mk8s...[/cyan]", "dots")]
    assert status_updates == [
        "Starting validation 1/2: GPU stack readiness.",
        "[bold white]GPU Operator[/bold white] [dim][5s][/dim] clusterpolicy state=ready",
        "Starting validation 2/2: GPU visibility probe.",
        "[bold white]GPU visibility[/bold white] [dim][9s][/dim] pods 3/3 Succeeded",
    ]
    assert printed == []


def test_deploy_generated_artifacts_default_all_targets_reports_all_validations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    cluster1_validation = {
        "kind": "mk8s_gpu_visibility",
        "name": "GPU visibility probe (cluster1)",
        "namespace": "gpu-validation",
        "report_file": "deploy-gpu-visibility-report-cluster1.json",
        "target_ref": "cluster1",
    }
    cluster2_validation = {
        "kind": "mk8s_gpu_visibility",
        "name": "GPU visibility probe (cluster2)",
        "namespace": "gpu-validation",
        "report_file": "deploy-gpu-visibility-report-cluster2.json",
        "target_ref": "cluster2",
    }
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster1"),
                _mk8s_target(fake_paths, target_ref="cluster2"),
            ],
            "validations": [cluster1_validation, cluster2_validation],
        }
    }

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_inventory", write_inventory_artifacts)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, **_kwargs: {
            "KUBECONFIG": f"/tmp/{cast(Mapping[str, Any], target)['target_ref']}.kubeconfig"
        },
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)

    validation_calls: list[tuple[list[dict[str, Any]], dict[str, str] | None]] = []

    def _fake_run_mk8s_gpu_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        validation_calls.append((validations, extra_env))
        reports_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for validation in validations:
            report_path = reports_dir / str(validation["report_file"])
            report_path.write_text(
                json.dumps({"passed": True, "summary": str(validation["name"])}) + "\n",
                encoding="utf-8",
            )
            written.append(report_path)
        return written

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert validation_calls == [
        ([cluster1_validation], {"KUBECONFIG": "/tmp/cluster1.kubeconfig"}),
        ([cluster2_validation], {"KUBECONFIG": "/tmp/cluster2.kubeconfig"}),
    ]
    markdown = (fake_paths.reports_dir / "deploy-report.md").read_text(encoding="utf-8")
    assert "GPU visibility probe (cluster1)" in markdown
    assert "GPU visibility probe (cluster2)" in markdown


def test_deploy_generated_artifacts_target_report_excludes_unselected_validations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    cluster1_validation = {
        "kind": "mk8s_gpu_visibility",
        "name": "GPU visibility probe (cluster1)",
        "namespace": "gpu-validation",
        "report_file": "deploy-gpu-visibility-report-cluster1.json",
        "target_ref": "cluster1",
    }
    cluster2_validation = {
        "kind": "mk8s_gpu_visibility",
        "name": "GPU visibility probe (cluster2)",
        "namespace": "gpu-validation",
        "report_file": "deploy-gpu-visibility-report-cluster2.json",
        "target_ref": "cluster2",
    }
    manifest = {
        "deploy": {
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster1"),
                _mk8s_target(fake_paths, target_ref="cluster2"),
            ],
            "validations": [cluster1_validation, cluster2_validation],
        }
    }
    printed: list[str] = []

    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_inventory", write_inventory_artifacts)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {"KUBECONFIG": "/tmp/cluster2.kubeconfig"},
    )
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda *, extra_env, emit: None)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    def _fake_run_mk8s_gpu_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert validations == [cluster2_validation]
        assert extra_env == {"KUBECONFIG": "/tmp/cluster2.kubeconfig"}
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "deploy-gpu-visibility-report-cluster2.json"
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "selected_node_count": 4,
                    "total_gpu_node_count": 4,
                    "passed_node_count": 4,
                    "skipped_node_count": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(cli, "run_mk8s_gpu_validations", _fake_run_mk8s_gpu_validations)

    class _FakeStatus:
        def update(self, message: str, **_kwargs: object) -> None:
            pass

    @contextmanager
    def _fake_status(message: str, **kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )
    fake_paths.reports_dir.mkdir(parents=True, exist_ok=True)
    stale_cluster1_report = fake_paths.reports_dir / "deploy-gpu-visibility-report-cluster1.json"
    stale_cluster1_report.write_text("{}\n", encoding="utf-8")

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
        requested_target_ref="cluster2",
    )

    markdown = (fake_paths.reports_dir / "deploy-report.md").read_text(encoding="utf-8")
    assert "GPU visibility probe (cluster2)" in markdown
    assert "GPU visibility probe (cluster1)" not in markdown
    assert not stale_cluster1_report.exists()
    assert printed == []


def test_deploy_generated_artifacts_keeps_required_mysterybox_validation_when_skipping_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    optional_validation = {
        "kind": "mk8s_gpu_visibility",
        "name": "GPU visibility probe",
        "report_file": "deploy-gpu-visibility-report.json",
        "target_ref": "mk8s",
    }
    required_validation = {
        "kind": cli.MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
        "name": "ESO MysteryBox connectivity (mk8s)",
        "target_ref": "mk8s",
        "required": True,
        "report_file": "mysterybox-eso-connectivity-report-mk8s.json",
    }
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [optional_validation, required_validation],
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
    monkeypatch.setattr(
        cli,
        "run_mk8s_gpu_validations",
        lambda *_args, **_kwargs: pytest.fail("optional validation should be skipped"),
    )

    def _fake_run_mysterybox_eso_validations(
        validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert validations == [required_validation]
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "mysterybox-eso-connectivity-report-mk8s.json"
        report_path.write_text(
            json.dumps(
                {
                    "validation": "ESO MysteryBox connectivity (mk8s)",
                    "kind": cli.MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
                    "passed": True,
                    "checks": [
                        {
                            "name": "Nebius API TLS",
                            "passed": True,
                            "summary": "TLS ok",
                        },
                        {
                            "name": "ClusterSecretStore Ready",
                            "passed": True,
                            "summary": "ClusterSecretStore/nebius-mysterybox-shared Ready=True",
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(
        cli,
        "run_mysterybox_eso_validations",
        _fake_run_mysterybox_eso_validations,
    )

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(cli.console, "status", _fake_status)
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message))
    )

    cli._deploy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        skip_validations=True,
        skip_validation_kinds=set(),
    )

    assert not (fake_paths.reports_dir / "deploy-gpu-visibility-report.json").exists()
    assert (fake_paths.reports_dir / "mysterybox-eso-connectivity-report-mk8s.json").exists()
    assert printed == [
        (
            "Skipping optional deploy-time validations for this run (--skip-validations); "
            "required validations still run."
        ),
    ]


def test_deploy_generated_artifacts_prints_validation_phase_lines_when_console_is_not_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU visibility probe",
                    "namespace": "gpu-validation",
                    "report_file": "deploy-gpu-visibility-report.json",
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
        _validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert reports_dir == fake_paths.reports_dir
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert emit is not None
        reports_dir.mkdir(parents=True, exist_ok=True)
        emit("Starting validation 1/1: GPU visibility probe.")
        emit("Starting validation 1/1: GPU visibility probe.")
        emit("[bold white]GPU visibility[/bold white] [dim][7s][/dim] pods 3/3 Succeeded")
        (reports_dir / "deploy-gpu-visibility-report.json").write_text(
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
        return [reports_dir / "deploy-gpu-visibility-report.json"]

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
        skip_validations=False,
        skip_validation_kinds=set(),
    )

    assert status_updates == [
        "Starting validation 1/1: GPU visibility probe.",
        "Starting validation 1/1: GPU visibility probe.",
        "[bold white]GPU visibility[/bold white] [dim][7s][/dim] pods 3/3 Succeeded",
    ]
    assert printed == [
        "Starting validation 1/1: GPU visibility probe.",
        "[bold white]GPU visibility[/bold white] [dim][7s][/dim] pods 3/3 Succeeded",
    ]


def test_deploy_generated_artifacts_writes_summary_even_when_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    manifest = {
        "deploy": {
            "targets": [_mk8s_target(fake_paths)],
            "validations": [
                {
                    "kind": "mk8s_gpu_operator_readiness",
                    "name": "GPU stack readiness",
                    "report_file": "deploy-gpu-stack-readiness-report.json",
                    "target_ref": "mk8s",
                },
                {
                    "kind": "mk8s_gpu_visibility",
                    "name": "GPU visibility probe",
                    "report_file": "deploy-gpu-visibility-report.json",
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
        _validations: list[dict[str, Any]],
        *,
        reports_dir: Path,
        extra_env: dict[str, str] | None,
        emit=None,
    ) -> list[Path]:
        assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
        assert emit is not None
        reports_dir.mkdir(parents=True, exist_ok=True)
        emit("Starting validation 1/2: GPU stack readiness.")
        (reports_dir / "deploy-gpu-stack-readiness-report.json").write_text(
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
            "GPU stack readiness check failed. Report: deploy-gpu-stack-readiness-report.json"
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
            skip_validations=False,
            skip_validation_kinds=set(),
        )

    markdown = (fake_paths.reports_dir / "deploy-report.md").read_text(encoding="utf-8")
    assert "- Overall status: `FAIL`" in markdown
    assert "### GPU stack readiness" in markdown
    assert "### GPU visibility probe" in markdown
    assert "No deploy validation results recorded yet." in markdown
    assert printed == [
        "",
        "[bold]Deployment summary[/bold]",
        "[bright_magenta]Validation:[/bright_magenta]",
        "  Overall: [red]FAIL[/red] (1/2 completed, 1 not run)",
        "  mk8s:",
        "    [red]FAIL[/red] GPU stack readiness: GPU Operator ready on 0 GPU nodes.",
        "    NOT RUN GPU visibility probe: No deploy validation results recorded yet.",
        "[bright_magenta]Copy/paste commands:[/bright_magenta]",
        "No immediate access or follow-up commands were derived.",
        "[bright_magenta]Important paths:[/bright_magenta]",
        f"  Generated bundle: {fake_paths.generated_dir}",
        f"  Validation detail reports: {fake_paths.reports_dir}",
        f"  Deploy report: {fake_paths.reports_dir / 'deploy-report.md'}",
        "[red]Deploy failed[/red]",
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
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda current_config: calls.append(("backend", current_config)),
    )
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda current_config, paths, loaded_manifest, **kwargs: calls.append(
            ("destroy_flux", current_config, paths, loaded_manifest, kwargs)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda current_config, paths, *, yes, initialize=True, status_watchers=None: calls.append(
            (
                "destroy_tf",
                current_config,
                paths,
                yes,
                initialize,
                status_watchers,
            )
        ),
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        yes=True,
    )

    assert calls == [
        ("backend", config),
        ("destroy_flux", config, fake_paths, manifest, {"all_targets": True}),
        (
            "destroy_tf",
            config,
            fake_paths,
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


def test_destroy_rendered_flux_bundle_deletes_post_flux_before_flux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": [{"id": "soperator", "enabled": True}]}}
    calls: list[tuple[str, ProjectPaths]] = []

    monkeypatch.setattr(
        cli,
        "_delete_post_flux_manifests",
        lambda paths, *, env: calls.append(("post_flux", paths)),
    )
    monkeypatch.setattr(
        cli,
        "delete_rendered_flux",
        lambda paths, *, extra_env=None, emit=None: calls.append(("flux", paths)),
    )
    monkeypatch.setattr(
        cli,
        "_delete_rendered_flux_namespaces",
        lambda paths, *, env: calls.append(("namespaces", paths)),
    )

    cli._destroy_rendered_flux_bundle(config, fake_paths, {})

    assert calls == [
        ("post_flux", fake_paths),
        ("flux", fake_paths),
        ("namespaces", fake_paths),
    ]


def test_destroy_generated_artifacts_deletes_all_target_flux_before_terraform(
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
            "targets": [
                _mk8s_target(fake_paths, target_ref="cluster-a"),
                _mk8s_target(fake_paths, target_ref="cluster-b"),
            ]
        },
    }
    calls: list[tuple[str, str | None]] = []

    def _fake_prepare_cluster_handoff_kube_env(
        _config: object,
        _paths: ProjectPaths,
        *,
        stack: ExitStack,
        target: Mapping[str, object],
        persist_local_kubeconfig: bool,
    ) -> dict[str, str]:
        assert persist_local_kubeconfig is False
        stack.callback(lambda: None)
        return {"TARGET_REF": str(target["target_ref"])}

    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        _fake_prepare_cluster_handoff_kube_env,
    )
    monkeypatch.setattr(
        cli,
        "_delete_post_flux_manifests",
        lambda _paths, *, env: calls.append(("post_flux", env.get("TARGET_REF"))),
    )
    monkeypatch.setattr(
        cli,
        "delete_rendered_flux",
        lambda _paths, *, extra_env=None, emit=None: calls.append(
            ("flux", (extra_env or {}).get("TARGET_REF"))
        ),
    )
    monkeypatch.setattr(
        cli,
        "_delete_rendered_flux_namespaces",
        lambda _paths, *, env: calls.append(("namespaces", env.get("TARGET_REF"))),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda *_args, **_kwargs: calls.append(("terraform", None)),
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        yes=True,
    )

    assert calls == [
        ("post_flux", "cluster-a"),
        ("flux", "cluster-a"),
        ("namespaces", "cluster-a"),
        ("post_flux", "cluster-b"),
        ("flux", "cluster-b"),
        ("namespaces", "cluster-b"),
        ("terraform", None),
    ]


def test_destroy_rendered_flux_bundle_attempts_remaining_targets_after_target_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}}
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {
            "targets": [
                _external_mk8s_target(fake_paths, target_ref="external-a"),
                _mk8s_target(fake_paths, target_ref="managed-b"),
            ]
        },
    }
    calls: list[tuple[str, str | None]] = []
    messages: list[str] = []

    def _fake_prepare_cluster_handoff_kube_env(
        _config: object,
        _paths: ProjectPaths,
        *,
        stack: ExitStack,
        target: Mapping[str, object],
        persist_local_kubeconfig: bool,
    ) -> dict[str, str]:
        assert persist_local_kubeconfig is False
        stack.callback(lambda: None)
        return {"TARGET_REF": str(target["target_ref"])}

    def _fake_delete_post_flux_manifests(_paths: ProjectPaths, *, env: Mapping[str, str]) -> None:
        target_ref = env.get("TARGET_REF")
        calls.append(("post_flux", target_ref))
        if target_ref == "external-a":
            raise RuntimeError("external-a unreachable")

    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        _fake_prepare_cluster_handoff_kube_env,
    )
    monkeypatch.setattr(cli, "_delete_post_flux_manifests", _fake_delete_post_flux_manifests)
    monkeypatch.setattr(
        cli,
        "delete_rendered_flux",
        lambda _paths, *, extra_env=None, emit=None: calls.append(
            ("flux", (extra_env or {}).get("TARGET_REF"))
        ),
    )
    monkeypatch.setattr(
        cli,
        "_delete_rendered_flux_namespaces",
        lambda _paths, *, env: calls.append(("namespaces", env.get("TARGET_REF"))),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: messages.append(str(message)),
    )

    with pytest.raises(RuntimeError, match="external-a unreachable"):
        cli._destroy_rendered_flux_bundle(config, fake_paths, manifest, all_targets=True)

    assert calls == [
        ("post_flux", "external-a"),
        ("post_flux", "managed-b"),
        ("flux", "managed-b"),
        ("namespaces", "managed-b"),
    ]
    assert any("continuing with remaining targets" in item for item in messages)


def test_staged_flux_cleanup_recognizes_adapter_labeled_soperator_configmaps(
    tmp_path: Path,
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    manifest_path = flux_dir / "post-flux-soperator-monitoring-dashboards.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "soperator-cluster-health",
                    "namespace": "monitoring-system",
                    "labels": {
                        "soperator.nebius.ai/managed-by": "nebius-cxcli-adapter",
                    },
                },
                "data": {"cluster-health.json": "{}\n"},
            }
        ),
        encoding="utf-8",
    )

    assert cli._remove_soperator_resources_from_staged_flux(flux_dir, release_refs=())
    assert not manifest_path.exists()


def test_post_flux_adapter_configmap_readback_requires_exact_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "soperator-cluster-health",
            "namespace": "monitoring-system",
            "labels": {
                "soperator.nebius.ai/managed-by": "nebius-cxcli-adapter",
                "grafana_dashboard": "1",
            },
            "annotations": {
                "soperator.nebius.ai/dashboard-sha256": "sha256:" + "a" * 64,
            },
        },
        "data": {"cluster-health.json": "{}\n"},
    }
    live = {
        **document,
        "metadata": {
            **document["metadata"],
            "labels": {**document["metadata"]["labels"], "server-label": "allowed"},
        },
    }
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(live),
            stderr="",
        ),
    )

    cli._verify_post_flux_soperator_adapter_configmaps([document], env={})

    live["data"] = {"cluster-health.json": "changed\n"}
    with pytest.raises(RuntimeError, match="read-back mismatch"):
        cli._verify_post_flux_soperator_adapter_configmaps([document], env={})


def test_delete_post_flux_manifest_removes_webhooks_before_namespaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "post-flux-soperator.yaml"
    manifest_path.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "admissionregistration.k8s.io/v1",
                    "kind": "ValidatingWebhookConfiguration",
                    "metadata": {"name": "soperator-validating-webhook-configuration"},
                    "webhooks": [
                        {
                            "name": "soperator.example.com",
                            "clientConfig": {
                                "service": {
                                    "namespace": "soperator",
                                    "name": "soperator-webhook-service",
                                }
                            },
                        }
                    ],
                },
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "soperator"},
                },
                {
                    "apiVersion": "slurm.nebius.ai/v1",
                    "kind": "SlurmCluster",
                    "metadata": {"name": "soperator", "namespace": "soperator"},
                },
                {
                    "apiVersion": "slurm.nebius.ai/v1alpha1",
                    "kind": "NodeSet",
                    "metadata": {"name": "worker-cpu", "namespace": "soperator"},
                },
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "soperator-manager", "namespace": "soperator"},
                },
                {
                    "apiVersion": "apiextensions.k8s.io/v1",
                    "kind": "CustomResourceDefinition",
                    "metadata": {"name": "nodesets.slurm.nebius.ai"},
                },
            ]
        ),
        encoding="utf-8",
    )
    calls: list[tuple[Any, ...]] = []

    def _fake_run(cmd: list[str], *, env: Mapping[str, str], **_kwargs: object) -> None:
        manifest = Path(cmd[cmd.index("-f") + 1])
        docs = [
            doc
            for doc in yaml.safe_load_all(manifest.read_text(encoding="utf-8"))
            if isinstance(doc, dict)
        ]
        calls.append(("kubectl", [doc["kind"] for doc in docs]))

    monkeypatch.setattr(cli, "_run_post_flux_kubectl", _fake_run)
    monkeypatch.setattr(
        cli,
        "_delete_admission_webhooks_for_namespaces",
        lambda namespaces, *, env: calls.append(("webhook-discovery", sorted(namespaces))),
    )

    cli._delete_post_flux_manifest(manifest_path, env={})

    assert calls == [
        ("kubectl", ["ValidatingWebhookConfiguration"]),
        ("webhook-discovery", ["soperator"]),
        ("kubectl", ["NodeSet"]),
        ("kubectl", ["SlurmCluster"]),
        ("kubectl", ["Deployment"]),
        ("kubectl", ["Namespace"]),
        ("kubectl", ["CustomResourceDefinition"]),
    ]


def test_destroy_generated_artifacts_external_target_skips_terraform_destroy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True, "instance_id": "mk8s"}]}}
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_external_mk8s_target(fake_paths)]},
    }
    calls: list[tuple[Any, ...]] = []
    messages: list[str] = []

    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda current_config, paths, loaded_manifest, **kwargs: calls.append(
            ("destroy_flux", current_config, paths, loaded_manifest, kwargs)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda *_args, **_kwargs: calls.append(("destroy_tf",)),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: messages.append(str(message)),
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        yes=True,
    )

    assert calls == [("destroy_flux", config, fake_paths, manifest, {"all_targets": True})]
    assert any(
        "External MK8s cluster and node groups were not destroyed" in item for item in messages
    )


def test_destroy_generated_artifacts_external_target_flux_failure_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {"apps": {"charts": [{"id": "gateway-helm", "enabled": True, "instance_id": "mk8s"}]}}
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "deploy": {"targets": [_external_mk8s_target(fake_paths)]},
    }
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cluster unreachable")),
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="external MK8s target"):
        cli._destroy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            yes=True,
        )


def test_destroy_generated_artifacts_stops_when_flux_teardown_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = {
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {}}]},
        "apps": {"charts": [{"id": "gateway-helm", "enabled": True}]},
    }
    manifest = {"schema": "nebius-cxcli-generated/v1", "render": {"module_sources": []}}
    captured: dict[str, Any] = {}
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
        lambda current_config, paths, *, yes, initialize=True, status_watchers=None: (
            captured.setdefault(
                "destroy",
                {
                    "config": current_config,
                    "paths": paths,
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

    with pytest.raises(RuntimeError, match="refusing to destroy cxcli-managed infra"):
        cli._destroy_generated_artifacts(
            config,
            fake_paths,
            manifest,
            yes=True,
        )

    assert "destroy" not in captured
    assert messages == []


def test_destroy_generated_artifacts_deletes_flux_before_handoff_cluster_is_destroyed(
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
    captured: dict[str, Any] = {}
    calls: list[str] = []
    messages: list[str] = []

    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_destroy_rendered_flux_bundle",
        lambda current_config, paths, loaded_manifest, **kwargs: (
            calls.append("destroy_flux"),
            captured.setdefault(
                "destroy_flux",
                {
                    "config": current_config,
                    "paths": paths,
                    "manifest": loaded_manifest,
                    "kwargs": kwargs,
                },
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda current_config, paths, *, yes, initialize=True, status_watchers=None: (
            calls.append("destroy_terraform"),
            captured.setdefault(
                "destroy",
                {
                    "config": current_config,
                    "paths": paths,
                    "yes": yes,
                    "initialize": initialize,
                    "status_watchers": status_watchers,
                },
            ),
        ),
    )
    monkeypatch.setattr(
        cli.console, "print", lambda message, *args, **kwargs: messages.append(str(message))
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        yes=True,
    )

    assert captured["destroy"] == {
        "config": config,
        "paths": fake_paths,
        "yes": True,
        "initialize": True,
        "status_watchers": None,
    }
    assert captured["destroy_flux"] == {
        "config": config,
        "paths": fake_paths,
        "manifest": manifest,
        "kwargs": {"all_targets": True},
    }
    assert calls == ["destroy_flux", "destroy_terraform"]
    assert messages == []


def test_apply_rendered_flux_installs_flux_controllers_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []
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
            calls.append(("install_flux", extra_env)) or _bundled_flux_install_manifest_url()
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
        "[cyan]Inspecting live release, Flux, and operation authority...[/cyan]",
        "[cyan]Installing Flux controllers into the target cluster...[/cyan]",
        "[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]",
        "[cyan]Materializing the suspended target release graph...[/cyan]",
        "[cyan]Waiting for rendered Flux resources to become Ready...[/cyan]",
    ]


def test_apply_rendered_flux_skips_flux_install_when_controllers_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []
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
        "[cyan]Inspecting live release, Flux, and operation authority...[/cyan]",
        "[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]",
        "[cyan]Materializing the suspended target release graph...[/cyan]",
        "[cyan]Waiting for rendered Flux resources to become Ready...[/cyan]",
    ]


def test_apply_rendered_flux_retries_transient_kubectl_apply_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, ...]] = []
    apply_calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []
    wait_calls: list[str] = []

    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(cli, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(cli, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(
        cli,
        "wait_for_flux_resource_apis",
        lambda *args, **kwargs: wait_calls.append("apis"),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda *args, **kwargs: wait_calls.append("flux"),
    )

    def _fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(cmd))
        if len(cmd) >= 4 and cmd[0] == "kubectl" and cmd[1] == "--cache-dir" and cmd[3] == "apply":
            apply_calls.append(tuple(cmd))
            if len(apply_calls) == 1:
                return SimpleNamespace(
                    returncode=1,
                    stderr="read: connection reset by peer",
                    stdout="",
                )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    class _FakeStatus:
        def update(self, _message: str, **_kwargs: object) -> None:
            return

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert calls[0] == ("kubectl", "cluster-info")
    assert len(apply_calls) == 2
    assert sleeps == [5.0]
    assert wait_calls == ["apis", "flux"]


def test_apply_rendered_flux_does_not_retry_non_transient_kubectl_apply_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    apply_calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []
    wait_calls: list[str] = []

    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(cli, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(cli, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(
        cli,
        "wait_for_flux_resource_apis",
        lambda *args, **kwargs: wait_calls.append("apis"),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda *args, **kwargs: wait_calls.append("flux"),
    )

    def _fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        if len(cmd) >= 4 and cmd[0] == "kubectl" and cmd[1] == "--cache-dir" and cmd[3] == "apply":
            apply_calls.append(tuple(cmd))
            return SimpleNamespace(
                returncode=1,
                stderr="Error from server (Forbidden): helmreleases is forbidden",
                stdout="",
            )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    class _FakeStatus:
        def update(self, _message: str, **_kwargs: object) -> None:
            return

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert len(apply_calls) == 1
    assert sleeps == []
    assert wait_calls == ["apis"]
    assert "Forbidden" in str(excinfo.value.stderr)


def test_apply_rendered_flux_applies_post_flux_manifests_after_flux_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    post_flux_path = fake_paths.flux_dir / "post-flux-mysterybox-eso.yaml"
    post_flux_path.write_text(
        "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: ns1\n",
        encoding="utf-8",
    )
    calls: list[tuple[Any, ...]] = []
    status_updates: list[str] = []

    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda _path: True)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/kubectl" if name == "kubectl" else None,
    )
    monkeypatch.setattr(cli, "flux_controllers_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(cli, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(
        cli,
        "wait_for_flux_resource_apis",
        lambda paths, *, extra_env=None, cache_dir=None: calls.append(("wait_apis", paths)),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda paths, *, extra_env=None, emit=None: calls.append(("wait_flux", paths)),
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
        calls.append(("run", tuple(cmd), timeout))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli, "_console_is_terminal", lambda: True)

    class _FakeStatus:
        def update(self, message: str, **_kwargs: object) -> None:
            status_updates.append(message)

    @contextmanager
    def _fake_status(_message: str, **_kwargs: object):
        yield _FakeStatus()

    monkeypatch.setattr(cli.console, "status", _fake_status)

    cli._apply_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert ("wait_flux", fake_paths) in calls
    assert any(
        call[0] == "run"
        and call[1][:4] == ("kubectl", "apply", "--server-side", "--force-conflicts")
        and call[1][4] == "-f"
        and str(call[1][5]).endswith("post-flux-mysterybox-eso-resources.yaml")
        and call[2] == 300
        for call in calls
    )
    assert (
        status_updates[-1] == "[cyan]Applying post-Flux manifests to the target cluster...[/cyan]"
    )


def test_apply_post_flux_manifest_orders_crds_webhook_resources_before_custom_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "post-flux-soperator.yaml"
    manifest_path.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "apiextensions.k8s.io/v1",
                    "kind": "CustomResourceDefinition",
                    "metadata": {"name": "slurmclusters.slurm.nebius.ai"},
                    "spec": {},
                },
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "soperator-manager", "namespace": "soperator"},
                    "spec": {},
                },
                {
                    "apiVersion": "admissionregistration.k8s.io/v1",
                    "kind": "MutatingWebhookConfiguration",
                    "metadata": {"name": "soperator-mutating-webhook-configuration"},
                    "webhooks": [
                        {
                            "name": "mnodeset-v1alpha1.kb.io",
                            "clientConfig": {
                                "service": {
                                    "name": "soperator-webhook-service",
                                    "namespace": "soperator",
                                }
                            },
                        }
                    ],
                },
                {
                    "apiVersion": "slurm.nebius.ai/v1",
                    "kind": "NodeSet",
                    "metadata": {"name": "worker-gpu", "namespace": "soperator"},
                    "spec": {},
                },
                {
                    "apiVersion": "slurm.nebius.ai/v1",
                    "kind": "SlurmCluster",
                    "metadata": {"name": "cluster1", "namespace": "soperator"},
                    "spec": {},
                },
            ],
            explicit_start=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []
    applied_kinds: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(cmd))
        if cmd[:5] == ["kubectl", "apply", "--server-side", "--force-conflicts", "-f"]:
            applied_kinds.append(
                [
                    doc["kind"]
                    for doc in yaml.safe_load_all(Path(cmd[5]).read_text(encoding="utf-8"))
                    if isinstance(doc, dict)
                ]
            )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    cli._apply_post_flux_manifest(manifest_path, env={})

    assert len(calls) == 9
    assert calls[0][:5] == ("kubectl", "apply", "--server-side", "--force-conflicts", "-f")
    assert calls[0][5].endswith("post-flux-soperator-crds.yaml")
    assert calls[1] == (
        "kubectl",
        "wait",
        "--for=condition=Established",
        "--timeout=120s",
        "customresourcedefinition.apiextensions.k8s.io/slurmclusters.slurm.nebius.ai",
    )
    assert calls[2][:5] == ("kubectl", "apply", "--server-side", "--force-conflicts", "-f")
    assert calls[2][5].endswith("post-flux-soperator-resources.yaml")
    assert calls[3] == (
        "kubectl",
        "-n",
        "soperator",
        "rollout",
        "status",
        "deployment/soperator-manager",
        "--timeout=180s",
    )
    assert calls[4] == (
        "kubectl",
        "-n",
        "soperator",
        "wait",
        "--for=jsonpath={.subsets[0].addresses[0].ip}",
        "--timeout=120s",
        "endpoints/soperator-webhook-service",
    )
    assert calls[5] == (
        "kubectl",
        "-n",
        "soperator",
        "delete",
        "cronjob.batch/run-extensive-check-on-reservations",
        "cronjob.batch/extensive-check",
        "--ignore-not-found=true",
        "--wait=false",
    )
    assert calls[6] == (
        "kubectl",
        "-n",
        "soperator",
        "delete",
        "job.batch,pod",
        "-l",
        "app.kubernetes.io/component=soperatorchecks",
        "--ignore-not-found=true",
        "--wait=false",
    )
    assert calls[7][:5] == ("kubectl", "apply", "--server-side", "--force-conflicts", "-f")
    assert calls[7][5].endswith("post-flux-soperator-custom-resources-10.yaml")
    assert calls[8][:5] == ("kubectl", "apply", "--server-side", "--force-conflicts", "-f")
    assert calls[8][5].endswith("post-flux-soperator-custom-resources-30.yaml")
    assert applied_kinds == [
        ["CustomResourceDefinition"],
        ["Deployment", "MutatingWebhookConfiguration"],
        ["SlurmCluster"],
        ["NodeSet"],
    ]


def test_apply_post_flux_manifest_deletes_hook_resources_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "post-flux-soperator.yaml"
    hook_annotations = {
        "helm.sh/hook": "post-install,post-upgrade",
        "helm.sh/hook-delete-policy": "before-hook-creation,hook-succeeded",
        "nebius-cxcli.nebius.ai/include-local-render": "true",
    }
    manifest_path.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": "cluster1-qos-reconcile-script",
                        "namespace": "soperator",
                        "annotations": hook_annotations,
                    },
                },
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {
                        "name": "cluster1-qos-reconcile",
                        "namespace": "soperator",
                        "annotations": hook_annotations,
                    },
                    "spec": {"activeDeadlineSeconds": 1200},
                },
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": "soperator-webhook-service", "namespace": "soperator"},
                    "spec": {},
                },
                {
                    "apiVersion": "slurm.nebius.ai/v1alpha1",
                    "kind": "SlurmCluster",
                    "metadata": {"name": "cluster1", "namespace": "soperator"},
                    "spec": {},
                },
            ],
            explicit_start=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []
    applied_kinds: list[list[str]] = []
    deleted_kinds: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(cmd))
        if len(cmd) > 5 and cmd[:5] == [
            "kubectl",
            "apply",
            "--server-side",
            "--force-conflicts",
            "-f",
        ]:
            applied_kinds.append(
                [
                    doc["kind"]
                    for doc in yaml.safe_load_all(Path(cmd[5]).read_text(encoding="utf-8"))
                    if isinstance(doc, dict)
                ]
            )
        if len(cmd) > 3 and cmd[:3] == ["kubectl", "delete", "-f"]:
            deleted_kinds.append(
                [
                    doc["kind"]
                    for doc in yaml.safe_load_all(Path(cmd[3]).read_text(encoding="utf-8"))
                    if isinstance(doc, dict)
                ]
            )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    cli._apply_post_flux_manifest(manifest_path, env={})

    assert applied_kinds == [
        ["Service"],
        ["SlurmCluster"],
        ["ConfigMap", "Job"],
    ]
    hook_delete_call = next(call for call in calls if call[:3] == ("kubectl", "delete", "-f"))
    assert hook_delete_call[:4] == ("kubectl", "delete", "-f", hook_delete_call[3])
    assert hook_delete_call[4:] == (
        "--ignore-not-found=true",
        "--wait=true",
        "--timeout=120s",
    )
    hook_wait_call = next(
        call
        for call in calls
        if call[:4] == ("kubectl", "-n", "soperator", "wait")
        and "job/cluster1-qos-reconcile" in call
    )
    assert hook_wait_call == (
        "kubectl",
        "-n",
        "soperator",
        "wait",
        "--for=condition=complete",
        "--timeout=1260s",
        "job/cluster1-qos-reconcile",
    )
    assert deleted_kinds == [["ConfigMap", "Job"]]


def test_apply_post_flux_manifest_replaces_priority_classes_when_value_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "post-flux-soperator.yaml"
    manifest_path.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "scheduling.k8s.io/v1",
                    "kind": "PriorityClass",
                    "metadata": {"name": "cluster1-slurm-worker"},
                    "value": 100000,
                },
                {
                    "apiVersion": "scheduling.k8s.io/v1",
                    "kind": "PriorityClass",
                    "metadata": {"name": "cluster1-slurm-login"},
                    "value": 100002,
                },
            ],
            explicit_start=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    def _fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(cmd))
        if cmd[:3] == ["kubectl", "get", "priorityclass"]:
            value = "1000000" if cmd[3] == "cluster1-slurm-worker" else "100002"
            return SimpleNamespace(returncode=0, stderr="", stdout=value)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    cli._apply_post_flux_manifest(manifest_path, env={})

    assert (
        "kubectl",
        "delete",
        "priorityclass",
        "cluster1-slurm-worker",
        "--ignore-not-found=true",
    ) in calls
    assert (
        "kubectl",
        "delete",
        "priorityclass",
        "cluster1-slurm-login",
        "--ignore-not-found=true",
    ) not in calls
    assert calls[-1][:5] == ("kubectl", "apply", "--server-side", "--force-conflicts", "-f")


def test_run_post_flux_kubectl_retries_transient_webhook_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    envs: list[Mapping[str, str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(tuple(cmd))
        env = kwargs.get("env")
        if isinstance(env, Mapping):
            envs.append(env)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stderr='Error from server (InternalError): failed calling webhook "x": connect: connection refused',
                stdout="",
            )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    sleeps: list[float] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli._run_post_flux_kubectl(
        ["kubectl", "apply", "-f", "custom.yaml"],
        env={"KUBECONFIG": "/tmp/kubeconfig"},
        retries=1,
        retry_delay_seconds=3.0,
        retry_stderr_markers=cli._KUBECTL_TRANSIENT_FAILURE_MARKERS,
    )

    assert calls == [
        ("kubectl", "apply", "-f", "custom.yaml"),
        ("kubectl", "apply", "-f", "custom.yaml"),
    ]
    assert sleeps == [3.0]
    assert envs[0]["KUBECONFIG"] == "/tmp/kubeconfig"
    assert envs[0]["PATH"] == os.environ["PATH"]


def test_run_post_flux_kubectl_retries_etcd_leader_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    def _fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(tuple(cmd))
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stderr=(
                    "Error from server: etcdserver: request timed out, "
                    "possibly due to previous leader failure"
                ),
                stdout="",
            )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli._run_post_flux_kubectl(
        ["kubectl", "apply", "--server-side", "--force-conflicts", "-f", "custom.yaml"],
        env={"KUBECONFIG": "/tmp/kubeconfig"},
        retries=1,
        retry_delay_seconds=2.0,
        retry_stderr_markers=cli._KUBECTL_TRANSIENT_FAILURE_MARKERS,
    )

    assert calls == [
        ("kubectl", "apply", "--server-side", "--force-conflicts", "-f", "custom.yaml"),
        ("kubectl", "apply", "--server-side", "--force-conflicts", "-f", "custom.yaml"),
    ]
    assert sleeps == [2.0]


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
        "[cyan]Inspecting live release, Flux, and operation authority...[/cyan]",
        "[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]",
        "[cyan]Materializing the suspended target release graph...[/cyan]",
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
    calls: list[tuple[Any, ...]] = []
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
            calls.append(("install_flux", extra_env)) or _bundled_flux_install_manifest_url()
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
        cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
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
            cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
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
            cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
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
            cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
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
        cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
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
        cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
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


def test_wait_for_rendered_flux_resources_treats_kubectl_timeout_as_pending(
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

    calls = {"count": 0}
    emissions: list[str] = []

    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)

    def _fake_run(cmd, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 20))
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "status": {
                        "conditions": [
                            {"type": "Ready", "status": "True", "reason": "InstallSucceeded"}
                        ]
                    }
                }
            ),
        )

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)
    monkeypatch.setattr(flux_ops.time, "sleep", lambda _seconds: None)

    flux_ops.wait_for_rendered_flux_resources(
        cast(ProjectPaths, SimpleNamespace(flux_dir=flux_dir)),
        emit=emissions.append,
        poll_interval_seconds=0.01,
        repeat_interval_seconds=0.01,
    )

    assert calls["count"] == 2
    plain = "\n".join(_plain_output(item) for item in emissions)
    assert "kubectl status read timed out after 20s" in plain
    assert "InstallSucceeded" in plain


def test_flux_install_manifest_url_uses_default_pinned_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flux_version, _terraform_version = _bundled_tool_versions()
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "components": {
                    "infra": {},
                    "apps": {},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    sources_file.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": flux_version,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()
    assert flux_ops.flux_install_manifest_url() == _bundled_flux_install_manifest_url()


def test_run_terraform_apply_with_status_wraps_apply_in_status_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []
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
        lambda message, **kwargs: calls.append(("print", message, kwargs)),
    )

    cli._run_terraform_apply_with_status("cfg", fake_paths)

    assert calls == [
        ("status_enter", "cfg", 15.0, 60.0),
        ("print", "hello", {"highlight": False}),
        ("apply", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, True, "callback"),
        ("status_exit", "cfg"),
    ]


def test_print_deployment_status_message_disables_rich_auto_highlighter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rich_console = cli.Console(
        force_terminal=True,
        color_system="standard",
        width=220,
        record=True,
    )
    monkeypatch.setattr(cli, "console", rich_console)

    cli._print_deployment_status_message("[bold cyan]API[/bold cyan] mk8s-cpu-b:DELETING 2/2 ready")

    rendered = rich_console.export_text(styles=True)
    plain_rendered = _ANSI_ESCAPE_RE.sub("", rendered)
    assert "mk8s-cpu-b:DELETING 2/2 ready" in plain_rendered
    assert "\x1b[1;36mAPI\x1b[0m" in rendered
    assert "\x1b[1;92mb:DE\x1b[0m" not in rendered


def test_print_copy_paste_command_styles_command_and_escapes_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rich_console = cli.Console(
        force_terminal=True,
        color_system="truecolor",
        width=220,
        record=True,
    )
    monkeypatch.setattr(cli, "console", rich_console)

    cli._print_copy_paste_command("nebius-cxcli render /tmp/[red]project[/red]/config.yaml")

    rendered = rich_console.export_text(styles=True)
    plain_rendered = _ANSI_ESCAPE_RE.sub("", rendered)
    assert "nebius-cxcli render /tmp/[red]project[/red]/config.yaml" in plain_rendered
    _assert_copy_paste_command_styled(
        rendered,
        "nebius-cxcli render /tmp/[red]project[/red]/config.yaml",
    )


def test_print_create_next_steps_styles_all_copy_paste_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rich_console = cli.Console(
        force_terminal=True,
        color_system="truecolor",
        width=220,
        record=True,
    )
    monkeypatch.setattr(cli, "console", rich_console)
    config_path = tmp_path / "config.yaml"

    cli._print_create_next_steps(config_path)

    rendered = rich_console.export_text(styles=True)
    plain_rendered = _ANSI_ESCAPE_RE.sub("", rendered)
    config_arg = str(config_path.resolve())
    assert plain_rendered.splitlines() == [
        "Next steps:",
        f"nebius-cxcli validate {config_arg}",
        f"nebius-cxcli render {config_arg}",
        f"nebius-cxcli deploy {config_arg}",
        "Optional CI bootstrap:",
        f"nebius-cxcli bootstrap-ci {config_arg}",
    ]
    for command in (
        f"nebius-cxcli validate {config_arg}",
        f"nebius-cxcli render {config_arg}",
        f"nebius-cxcli deploy {config_arg}",
        f"nebius-cxcli bootstrap-ci {config_arg}",
    ):
        _assert_copy_paste_command_styled(rendered, command)


def test_print_upgrade_plan_lines_styles_warnings_amber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rich_console = cli.Console(
        force_terminal=True,
        color_system="truecolor",
        width=220,
        record=True,
    )
    monkeypatch.setattr(cli, "console", rich_console)

    cli._print_upgrade_plan_lines(
        (
            "MK8s node-template upgrade plan",
            "- warnings:",
            "  - safe mode generic warning",
            "Dry run only: no changes.",
        )
    )

    rendered = rich_console.export_text(styles=True)
    plain_rendered = _ANSI_ESCAPE_RE.sub("", rendered)
    assert "- warnings:" in plain_rendered
    assert "  - safe mode generic warning" in plain_rendered
    assert "\x1b[1;38;2;255;191;0mwarnings:\x1b[0m" in rendered
    assert "\x1b[38;2;255;191;0msafe mode generic warning\x1b[0m" in rendered


def test_print_upgrade_plan_lines_wraps_repeat_dry_run_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rich_console = cli.Console(width=220, record=True)
    monkeypatch.setattr(cli, "console", rich_console)

    cli._print_upgrade_plan_lines(
        (
            "MK8s node-template upgrade plan",
            "- repeat dry-run command:",
            "  nebius-cxcli upgrade node-template '/tmp/project path/config.yaml' "
            "infra:mk8s@cluster1 --to-os ubuntu24.04 "
            "--strategy zero-surge --dry-run",
            "Dry run only: no changes.",
        )
    )

    rendered = rich_console.export_text()
    assert (
        "nebius-cxcli upgrade node-template '/tmp/project path/config.yaml' "
        "infra:mk8s@cluster1 --to-os ubuntu24.04 --strategy zero-surge --dry-run"
    ) in rendered
    assert "Dry run only: no changes." in rendered


def test_deploy_validation_warning_cache_dedupes_nested_upgrade_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "mk8s_gpu_validation_warnings",
        lambda _config: ("same GPU validation warning",),
    )
    monkeypatch.setattr(
        cli,
        "soperator_child_chart_warnings",
        lambda _config: ("same Soperator validation warning",),
    )

    token = cli._DEPLOY_VALIDATION_WARNING_CACHE.set(set())
    try:
        with cli.console.capture() as capture:
            cli._print_mk8s_gpu_validation_warnings(object())
            cli._print_mk8s_gpu_validation_warnings(object())
    finally:
        cli._DEPLOY_VALIDATION_WARNING_CACHE.reset(token)

    output = _plain_output(capture.get())
    assert output.count("Deploy validation warning:") == 2
    assert output.count("same GPU validation warning") == 1
    assert output.count("same Soperator validation warning") == 1


def test_run_terraform_apply_with_status_can_skip_mk8s_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []
    reporter = SimpleNamespace(handle_terraform_event="callback")

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
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


def test_run_terraform_apply_with_status_runs_mk8s_gpu_stack_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _config_with_enabled_mk8s()
    calls: list[tuple[Any, ...]] = []
    reporter = SimpleNamespace(handle_terraform_event="callback")

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})
    monkeypatch.setattr(
        cli,
        "validate_vpc_networking_preflight",
        lambda config: calls.append(("vpc-preflight", config)),
    )
    monkeypatch.setattr(
        cli,
        "has_mk8s_gpu_stack_compatibility_preflight_targets",
        lambda config: calls.append(("has-gpu-stack", config)) or True,
    )
    monkeypatch.setattr(
        cli,
        "validate_mk8s_gpu_stack_compatibility_preflight",
        lambda config: calls.append(("gpu-stack", config)),
    )

    @contextmanager
    def _fake_reporting(
        config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0
    ):
        _ = emit, poll_interval_seconds, repeat_interval_seconds
        yield reporter

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)
    monkeypatch.setattr(
        cli,
        "terraform_apply",
        lambda infra_dir, *, extra_env=None, initialize=True, event_callback=None: calls.append(
            ("apply", infra_dir, extra_env, initialize, event_callback)
        ),
    )

    cli._run_terraform_apply_with_status(config, fake_paths)

    assert calls == [
        ("vpc-preflight", config),
        ("has-gpu-stack", config),
        ("gpu-stack", config),
        ("apply", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, True, "callback"),
    ]


def test_run_terraform_apply_with_status_passes_explicit_status_watchers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []
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
        lambda message, **kwargs: calls.append(("print", message, kwargs)),
    )

    cli._run_terraform_apply_with_status("cfg", fake_paths, status_watchers=watchers)

    assert calls == [
        ("status_enter", "cfg", 15.0, 60.0, watchers),
        ("print", "hello", {"highlight": False}),
        ("apply", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, True, "callback"),
        ("status_exit", "cfg"),
    ]


def test_run_terraform_apply_with_status_passes_prompted_mysterybox_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    config = _mysterybox_first_deploy_config()
    calls: list[tuple[Any, ...]] = []
    reporter = SimpleNamespace(handle_terraform_event="callback")
    payload_values = json.dumps(
        {
            "db-username-password": {
                "USERNAME": "alice",
                "PASSWORD": "secret",
            },
            "secret2": {
                "MYKEY": "token",
            },
        }
    )

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

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

    cli._run_terraform_apply_with_status(
        config,
        fake_paths,
        run_mk8s_preflight=False,
        extra_env={"TF_VAR_mysterybox_payload_values": payload_values},
    )

    assert calls == [
        (
            "apply",
            fake_paths.infra_dir,
            {
                "TF_VAR_DEMO": "1",
                "TF_VAR_mysterybox_payload_values": payload_values,
            },
            True,
            "callback",
        )
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
    calls: list[tuple[Any, ...]] = []
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
    calls: list[tuple[Any, ...]] = []
    reporter = SimpleNamespace(
        handle_terraform_event="callback",
        abort_reason=lambda: None,
        snapshot=lambda: "Status [10s] TF: pending | API: pending",
    )

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(
        config: object,
        *,
        emit,
        operation="apply",
        poll_interval_seconds=15.0,
        repeat_interval_seconds=60.0,
    ):
        calls.append(("status_enter", config, operation))
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
        ("status_enter", "cfg", "destroy"),
        (
            "destroy",
            fake_paths.infra_dir,
            {"TF_VAR_DEMO": "1"},
            True,
            "callback",
            reporter.abort_reason,
        ),
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
                        "cluster": {
                            "parent_id": "project-456",
                            "cluster_name": "clust1",
                        },
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
                        "secrets": [
                            {
                                "name": "app-runtime",
                                "version_id": "n/a",
                                "payload": {
                                    "API_KEY": {
                                        "type": "text",
                                    },
                                },
                            },
                            {
                                "name": "worker-runtime",
                                "version_id": "mbsecver-e00worker",
                                "payload": {
                                    "TOKEN": {
                                        "type": "text",
                                    },
                                },
                            },
                        ],
                    },
                },
                {
                    "id": "sfs",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project-456",
                        "filesystems": {
                            "jail": {"name": "sharedfs-jail"},
                            "controller-spool": {"name": "sharedfs-controller-spool"},
                        },
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
        {
            "component_id": "sfs",
            "instance_id": "sfs",
            "kind": "nebius.compute.filesystem",
            "parent_id": "project-456",
            "resource_name": "sharedfs-jail",
        },
        {
            "component_id": "sfs",
            "instance_id": "sfs",
            "kind": "nebius.compute.filesystem",
            "parent_id": "project-456",
            "resource_name": "sharedfs-controller-spool",
        },
    ]


def test_sync_mysterybox_primary_version_ids_updates_unset_config_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    fake_paths.config_path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "components": [
                        {
                            "id": "mysterybox",
                            "enabled": True,
                            "inputs": {
                                "secrets": [
                                    {
                                        "name": "app-runtime",
                                        "version_id": "n/a",
                                        "payload": {"API_KEY": {"type": "text"}},
                                    },
                                    {
                                        "name": "worker-runtime",
                                        "version_id": "mbsecver-existing",
                                        "payload": {"TOKEN": {"type": "text"}},
                                    },
                                ],
                            },
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda config: {"TF_VAR_demo": "1"})

    def _fake_terraform_output_json(infra_dir, *, extra_env=None, initialize=True):
        captured["call"] = {
            "infra_dir": infra_dir,
            "extra_env": extra_env,
            "initialize": initialize,
        }
        return {
            "mysterybox_primary_secret_version_ids": {
                "value": {
                    "app-runtime": "mbsecver-created",
                    "worker-runtime": "mbsecver-new-primary",
                }
            }
        }

    monkeypatch.setattr(cli, "terraform_output_json", _fake_terraform_output_json)

    assert cli._sync_mysterybox_primary_version_ids_to_config("cfg", fake_paths, initialize=False)

    refreshed = yaml.safe_load(fake_paths.config_path.read_text(encoding="utf-8"))
    secrets = refreshed["infra"]["components"][0]["inputs"]["secrets"]
    assert secrets[0]["version_id"] == "mbsecver-created"
    assert secrets[1]["version_id"] == "mbsecver-existing"
    assert captured["call"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_demo": "1"},
        "initialize": False,
    }


def test_sync_mysterybox_primary_version_ids_updates_generated_manifest_and_tfvars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    source_payload = {
        "infra": {
            "components": [
                {
                    "id": "mysterybox",
                    "enabled": True,
                    "inputs": {
                        "secrets": [
                            {
                                "name": "app-runtime",
                                "version_id": "n/a",
                                "payload": {"API_KEY": {"type": "text"}},
                            }
                        ]
                    },
                }
            ]
        }
    }
    fake_paths.config_path.write_text(
        yaml.safe_dump(source_payload, sort_keys=False),
        encoding="utf-8",
    )
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "runtime_config": {
            **json.loads(json.dumps(source_payload)),
            "infra": {
                **json.loads(json.dumps(source_payload["infra"])),
                "mysterybox": {
                    "enabled": True,
                    "secrets": [
                        {
                            "name": "app-runtime",
                            "version_id": "n/a",
                            "payload": {"API_KEY": {"type": "text"}},
                        }
                    ],
                },
            },
        },
        "render": {
            "terraform_tfvars": {
                "mysterybox_secrets": [
                    {
                        "name": "app-runtime",
                        "version_id": "n/a",
                        "payload": {"API_KEY": {"type": "text"}},
                    }
                ]
            }
        },
        "deploy": {"targets": [], "validations": []},
    }
    manifest_path = fake_paths.generated_dir / "nebius-cxcli-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (fake_paths.infra_dir / "terraform.auto.tfvars.json").write_text(
        json.dumps(manifest["render"]["terraform_tfvars"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda config: {"TF_VAR_demo": "1"})
    monkeypatch.setattr(
        cli,
        "terraform_output_json",
        lambda *_args, **_kwargs: {
            "mysterybox_primary_secret_version_ids": {"value": {"app-runtime": "mbsecver-created"}}
        },
    )

    assert cli._sync_mysterybox_primary_version_ids_to_config(
        "cfg",
        fake_paths,
        initialize=False,
    )

    refreshed_config = yaml.safe_load(fake_paths.config_path.read_text(encoding="utf-8"))
    assert (
        refreshed_config["infra"]["components"][0]["inputs"]["secrets"][0]["version_id"]
        == "mbsecver-created"
    )
    manifest_payload = json.loads(
        (fake_paths.generated_dir / "nebius-cxcli-manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest_payload["runtime_config"]["infra"]["components"][0]["inputs"]["secrets"][0][
            "version_id"
        ]
        == "mbsecver-created"
    )
    assert (
        manifest_payload["runtime_config"]["infra"]["mysterybox"]["secrets"][0]["version_id"]
        == "mbsecver-created"
    )
    assert (
        manifest_payload["render"]["terraform_tfvars"]["mysterybox_secrets"][0]["version_id"]
        == "mbsecver-created"
    )
    tfvars = json.loads(
        (fake_paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert tfvars["mysterybox_secrets"][0]["version_id"] == "mbsecver-created"


@pytest.mark.parametrize("edited_target", ["config", "manifest", "tfvars"])
def test_sync_mysterybox_primary_version_ids_rejects_concurrent_bundle_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    edited_target: str,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    source_payload = {
        "infra": {
            "components": [
                {
                    "id": "mysterybox",
                    "enabled": True,
                    "inputs": {
                        "secrets": [
                            {
                                "name": "app-runtime",
                                "version_id": "n/a",
                                "payload": {"API_KEY": {"type": "text"}},
                            }
                        ]
                    },
                }
            ]
        }
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v1",
        "runtime_config": json.loads(json.dumps(source_payload)),
        "render": {
            "terraform_tfvars": {
                "mysterybox_secrets": [
                    {
                        "name": "app-runtime",
                        "version_id": "n/a",
                        "payload": {"API_KEY": {"type": "text"}},
                    }
                ]
            }
        },
    }
    manifest_path = fake_paths.generated_dir / "nebius-cxcli-manifest.json"
    tfvars_path = fake_paths.infra_dir / "terraform.auto.tfvars.json"
    fake_paths.config_path.write_text(
        yaml.safe_dump(source_payload, sort_keys=False),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tfvars_path.write_text(
        json.dumps(manifest["render"]["terraform_tfvars"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths_by_name = {
        "config": fake_paths.config_path,
        "manifest": manifest_path,
        "tfvars": tfvars_path,
    }
    original = {name: path.read_bytes() for name, path in paths_by_name.items()}
    operator_edit = b'{"operator": "edit"}\n'

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})

    def _fake_terraform_output_json(*_args, **_kwargs):
        paths_by_name[edited_target].write_bytes(operator_edit)
        return {
            "mysterybox_primary_secret_version_ids": {"value": {"app-runtime": "mbsecver-created"}}
        }

    monkeypatch.setattr(cli, "terraform_output_json", _fake_terraform_output_json)

    with pytest.raises(cli.ProjectBundleSafetyError, match="changed after generation admission"):
        cli._sync_mysterybox_primary_version_ids_to_config(
            "cfg",
            fake_paths,
            initialize=False,
        )

    for name, path in paths_by_name.items():
        assert path.read_bytes() == (operator_edit if name == edited_target else original[name])


def test_sync_mysterybox_primary_version_ids_checks_only_changed_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True, exist_ok=True)
    fake_paths.config_path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "components": [
                        {
                            "id": "mysterybox",
                            "enabled": True,
                            "inputs": {
                                "secrets": [
                                    {
                                        "name": "app-runtime",
                                        "version_id": "n/a",
                                        "payload": {"API_KEY": {"type": "text"}},
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    tfvars_path = fake_paths.infra_dir / "terraform.auto.tfvars.json"
    tfvars_path.write_text('{"before": true}\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})

    def _fake_terraform_output_json(*_args, **_kwargs):
        tfvars_path.write_text('{"operator": "edit"}\n', encoding="utf-8")
        return {
            "mysterybox_primary_secret_version_ids": {"value": {"app-runtime": "mbsecver-created"}}
        }

    monkeypatch.setattr(cli, "terraform_output_json", _fake_terraform_output_json)

    assert cli._sync_mysterybox_primary_version_ids_to_config("cfg", fake_paths)
    assert tfvars_path.read_text(encoding="utf-8") == '{"operator": "edit"}\n'


def test_terraform_plan_command_invokes_runtime_auth_and_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1", "render": {"module_sources": []}}

    monkeypatch.setattr(
        cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest)
    )

    def _fake_ensure_terraform_backend_ready(config: object) -> None:
        captured["backend"] = config

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
        ["terraform", "plan", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == "cfg"
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
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(
        cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest)
    )

    def _fake_ensure_terraform_backend_ready(config: object) -> None:
        captured["backend"] = config

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
        lambda config, paths, *, initialize=True, manifest=None, **_kwargs: captured.setdefault(
            "apply",
            {"config": config, "paths": paths, "initialize": initialize, "manifest": manifest},
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
        ["terraform", "apply", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == "cfg"
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
        "manifest": manifest,
    }
    assert captured["inventory"] == {
        "config": "cfg",
        "paths": fake_paths,
    }


def test_terraform_destroy_command_invokes_runtime_auth_and_destroy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(
        cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest)
    )
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)

    def _fake_ensure_terraform_backend_ready(config: object) -> None:
        captured["backend"] = config

    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", _fake_ensure_terraform_backend_ready
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_recovery",
        lambda config, paths, *, yes, initialize=True, status_watchers=None: captured.setdefault(
            "destroy",
            {
                "config": config,
                "paths": paths,
                "yes": yes,
                "initialize": initialize,
                "status_watchers": status_watchers,
            },
        ),
    )

    result = runner.invoke(
        cli.app,
        ["terraform", "destroy", str(tmp_path / "generated"), "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Terraform destroy completed from" in _plain_output(result.output)
    assert captured["backend"] == "cfg"
    assert captured["destroy"] == {
        "config": "cfg",
        "paths": fake_paths,
        "yes": True,
        "initialize": True,
        "status_watchers": None,
    }


def test_terraform_destroy_command_confirmation_targets_infra_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(
        cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest)
    )
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
    calls: list[tuple[Any, ...]] = []
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
        lambda config, paths, *, force: calls.append(("unlock", force)) or lock_info,
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
        yes=True,
        initialize=True,
        status_watchers=None,
    )

    assert calls == [
        ("destroy", True, None),
        ("unlock", False),
        ("destroy", True, None),
    ]


def test_terraform_unlock_non_force_rejects_blank_lock_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.infra_dir.mkdir(parents=True)
    lock_info = SimpleNamespace(lock_id="lock-123", who="")

    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "backend_settings_from_config", lambda _config: object())
    monkeypatch.setattr(cli, "read_state_lock_info", lambda *_args, **_kwargs: lock_info)
    monkeypatch.setattr(cli, "_active_local_terraform_processes", lambda: ())
    monkeypatch.setattr(cli, "terraform_force_unlock", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="no owner metadata"):
        cli._unlock_terraform_state_lock(
            "cfg",
            fake_paths,
            force=False,
        )


def test_run_terraform_destroy_with_recovery_deletes_stuck_mk8s_node_group_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[Any, ...]] = []

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

    monkeypatch.setattr(
        cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_unlock_terraform_state_lock",
        lambda config, paths, *, force: None,
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

    monkeypatch.setattr(
        cli, "_load_generated_infra_context", lambda _path: ("cfg", fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_unlock_terraform_state_lock",
        lambda config, paths, *, force: lock_info,
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
    captured: dict[str, Any] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1", "deploy": {}}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: ("cfg", fake_paths, manifest)
    )

    def _fake_ensure_runtime_auth_material(
        config: object,
        *,
        need_terraform: bool,
    ) -> None:
        captured["auth"] = {
            "config": config,
            "need_terraform": need_terraform,
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
        ["flux", "bootstrap", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0, result.output
    assert "Flux reconciled" in _plain_output(result.output)
    assert captured["auth"] == {
        "config": "cfg",
        "need_terraform": False,
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

    calls: list[tuple[Any, ...]] = []

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

    result = flux_ops.ensure_flux(cast(ProjectPaths, fake_paths))

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

    calls: list[tuple[Any, ...]] = []

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

    result = flux_ops.ensure_flux(cast(ProjectPaths, fake_paths))

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

    calls: list[tuple[Any, ...]] = []

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

    result = flux_ops.ensure_flux(cast(ProjectPaths, fake_paths))

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

    calls: list[tuple[Any, ...]] = []

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

    result = flux_ops.ensure_flux(cast(ProjectPaths, fake_paths))

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
    calls: list[tuple[Any, ...]] = []

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
        lambda cmd, **kwargs: (
            calls.append(("apply", tuple(cmd)))
            or flux_ops.FluxApplySummary(
                created=0,
                configured=1,
                unchanged=0,
                other=0,
            )
        ),
    )
    monkeypatch.setattr(
        flux_ops,
        "_run_captured",
        lambda cmd, **kwargs: (
            calls.append(("run", tuple(cmd)))
            or subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        ),
    )

    manifest_url = flux_ops.install_flux_controllers(extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert manifest_url == _bundled_flux_install_manifest_url()
    assert calls[0] == ("wait_namespace", {"KUBECONFIG": "/tmp/kubeconfig"})
    assert calls[1] == ("wait_crds_clear", {"KUBECONFIG": "/tmp/kubeconfig"})
    assert calls[2] == (
        "apply",
        ("kubectl", "apply", "-f", manifest_url),
    )
    assert calls[-1] == ("wait_crds_ready", {"KUBECONFIG": "/tmp/kubeconfig"})


@pytest.mark.parametrize(
    ("access", "expected_server"),
    [
        ("external", "https://public.example.invalid"),
        ("internal", "https://private.example.invalid"),
    ],
)
def test_mk8s_cluster_handoff_spec_selects_control_plane_endpoint_for_access(
    access: str,
    expected_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeSdk:
        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            captured["close_timeout"] = timeout
            captured["closed"] = True

    class FakeGetClusterRequest:
        def __init__(self, *, id: str) -> None:
            self.id = id

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="private-cluster"),
        status=SimpleNamespace(
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(
                    public_endpoint="https://public.example.invalid",
                    private_endpoint="https://private.example.invalid",
                ),
                auth=SimpleNamespace(cluster_ca_certificate="FAKE-CA"),
            )
        ),
    )

    class FakeGetOperation:
        def wait(self) -> object:
            return cluster

    class FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            captured["sdk"] = sdk

        def get(self, request: object) -> FakeGetOperation:
            captured["request_id"] = getattr(request, "id", None)
            return FakeGetOperation()

    def _package(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        return module

    nebius_package = _package("nebius")
    api_package = _package("nebius.api")
    api_nebius_package = _package("nebius.api.nebius")
    mk8s_package = _package("nebius.api.nebius.mk8s")
    v1_module = types.ModuleType("nebius.api.nebius.mk8s.v1")
    v1_module.ClusterServiceClient = FakeClusterServiceClient
    v1_module.GetClusterRequest = FakeGetClusterRequest
    nebius_package.api = api_package  # type: ignore[attr-defined]
    api_package.nebius = api_nebius_package  # type: ignore[attr-defined]
    api_nebius_package.mk8s = mk8s_package  # type: ignore[attr-defined]
    mk8s_package.v1 = v1_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius", nebius_package)
    monkeypatch.setitem(sys.modules, "nebius.api", api_package)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius", api_nebius_package)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.mk8s", mk8s_package)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.mk8s.v1", v1_module)

    def _init_nebius_sdk(**kwargs: object) -> FakeSdk:
        captured["init_kwargs"] = kwargs
        return FakeSdk()

    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "init_nebius_sdk", _init_nebius_sdk)

    spec = cli._mk8s_cluster_handoff_spec_for_identity(
        project_id="project-456",
        client_name="client-a",
        cluster_id="mk8scluster-123",
        access=access,
    )

    assert spec.server == expected_server
    assert captured["request_id"] == "mk8scluster-123"
    assert captured["init_kwargs"]["parent_id"] == "project-456"
    assert captured["closed"] is True


def test_mk8s_cluster_handoff_spec_loads_renewable_auth_despite_static_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class FakeSdk:
        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            calls.append("close")

    class FakeGetClusterRequest:
        def __init__(self, *, id: str) -> None:
            self.id = id

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="private-cluster"),
        status=SimpleNamespace(
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(public_endpoint="https://public.example.invalid"),
                auth=SimpleNamespace(cluster_ca_certificate="FAKE-CA"),
            )
        ),
    )
    request_kwargs: dict[str, object] = {}

    class FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            pass

        def get(self, request: object, **kwargs: object) -> object:
            request_kwargs.update(kwargs)
            return SimpleNamespace(wait=lambda: cluster)

    v1_module = types.ModuleType("nebius.api.nebius.mk8s.v1")
    v1_module.ClusterServiceClient = FakeClusterServiceClient
    v1_module.GetClusterRequest = FakeGetClusterRequest
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.mk8s.v1", v1_module)
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "_renewable_runtime_auth_env_available", lambda: False)
    monkeypatch.setattr(
        cli,
        "_runtime_auth_cache_load",
        lambda *, project_id, client_name: calls.append((project_id, client_name)) or True,
    )
    monkeypatch.setattr(
        cli,
        "init_nebius_sdk",
        lambda **kwargs: calls.append(("sdk", kwargs)) or FakeSdk(),
    )

    spec = cli._mk8s_cluster_handoff_spec_for_identity(
        project_id="project-456",
        client_name="client-a",
        cluster_id="mk8scluster-123",
        access="external",
        require_renewable_auth=True,
    )

    assert calls[:2] == [
        ("project-456", "client-a"),
        (
            "sdk",
            {
                "parent_id": "project-456",
                "endpoint": None,
                "context": "MK8s cluster handoff",
                "require_renewable_auth": True,
            },
        ),
    ]
    assert spec.exec_args[-1] == "--require-renewable-auth"
    assert request_kwargs["auth_options"] == {
        "token_renew_synchronous": "true",
        "token_renew_request_timeout": "20.0",
    }


def test_renewable_runtime_auth_env_does_not_accept_static_or_stale_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "static-token")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("NEBIUS_SA_ID", "service-account")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "public-key")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(tmp_path / "missing.pem"))

    assert cli._runtime_auth_env_available() is True  # noqa: SLF001
    assert cli._renewable_runtime_auth_env_available() is False  # noqa: SLF001


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
    captured: dict[str, Any] = {}
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
        lambda config, *, cluster_id, access, require_renewable_auth=False: (
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
        kubeconfig_mode = kubeconfig_path.stat().st_mode & 0o777

    terraform_output = captured["terraform_output"]
    assert terraform_output[0] == fake_paths.infra_dir
    assert terraform_output[1] == "mk8s_cluster_id"
    assert terraform_output[3] is True
    assert terraform_output[2]["TF_VAR_nebius_provider_parent_id"] == "project-456"
    assert terraform_output[2]["TF_VAR_nebius_provider_module_name"]
    assert captured["handoff_spec"] == (fake_config, "cluster-123", "external")
    assert captured["persist"] == (spec, True)
    assert env[flux_ops.CLUSTER_HANDOFF_ACCESS_ENV] == "external"
    assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "cluster-123"
    assert env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == "context-entry"
    assert kubeconfig["clusters"][0]["cluster"]["server"] == "https://mk8s.example.invalid"
    assert kubeconfig["users"][0]["user"]["exec"]["command"] == "/usr/local/bin/nebius-cxcli"
    assert kubeconfig["users"][0]["user"]["exec"]["args"] == [
        "mk8s-token",
        "--project-id",
        "project-456",
        "--client-name",
        "client-a",
        "--cache-file",
        str(kubeconfig_path.parent / "exec-credential-cache.json"),
    ]
    assert kubeconfig_mode == 0o600
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
    captured: dict[str, Any] = {}
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
        lambda config, *, cluster_id, access, require_renewable_auth=False: (
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
    assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "cluster-123"
    assert env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == "context-entry"


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
    captured: dict[str, Any] = {}
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
        lambda config, *, cluster_id, access, require_renewable_auth=False: (
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
    assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "cluster-123"
    assert env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV] == "context-entry"


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
            access_source_path="inputs.cluster.public_endpoint",
        ),
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {"cluster": {"public_endpoint": False}},
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


def test_mysterybox_eso_wizard_hides_target_prompts_without_backend_component() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
    )
    payload = {
        "infra": {
            "components": [
                {"id": "mk8s", "instance_id": "mk8s", "enabled": True},
            ]
        },
        "deploy": {
            "targets": [
                {
                    "secrets": {
                        "mysterybox": {
                            "enabled": False,
                        }
                    }
                }
            ]
        },
    }

    assert cli._skip_mysterybox_eso_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].secrets.mysterybox.enabled",
    )

    payload["infra"]["components"].append(
        {"id": "mysterybox", "instance_id": "mysterybox", "enabled": False}
    )
    assert cli._skip_mysterybox_eso_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].secrets.mysterybox.enabled",
    )

    payload["infra"]["components"][1]["enabled"] = True
    assert not cli._skip_mysterybox_eso_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].secrets.mysterybox.enabled",
    )


def test_mysterybox_eso_wizard_prompts_sync_namespaces_for_all_store_modes() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
    )
    payload = {
        "infra": {
            "components": [
                {"id": "mk8s", "instance_id": "mk8s", "enabled": True},
                {"id": "mysterybox", "instance_id": "mysterybox", "enabled": True},
            ]
        },
        "deploy": {
            "targets": [
                {
                    "secrets": {
                        "mysterybox": {
                            "enabled": True,
                            "allow_all_namespaces": True,
                        }
                    }
                }
            ]
        },
    }

    assert (
        cli._skip_mysterybox_eso_prompt(
            payload=payload,
            entry=entry,
            full_path_label="deploy.targets[0].secrets.mysterybox.sync_namespaces",
        )
        is False
    )
    del payload["deploy"]["targets"][0]["secrets"]["mysterybox"]["allow_all_namespaces"]
    assert (
        cli._skip_mysterybox_eso_prompt(
            payload=payload,
            entry=entry,
            full_path_label="deploy.targets[0].secrets.mysterybox.sync_namespaces",
        )
        is False
    )
    payload["deploy"]["targets"][0]["secrets"]["mysterybox"]["allow_all_namespaces"] = False
    assert (
        cli._skip_mysterybox_eso_prompt(
            payload=payload,
            entry=entry,
            full_path_label="deploy.targets[0].secrets.mysterybox.sync_namespaces",
        )
        is False
    )


def test_wizard_selected_value_shows_boolean_secret_controls() -> None:
    assert (
        cli._wizard_visible_value(
            True,
            path_label="deploy.targets[0].secrets.mysterybox.enabled",
        )
        == "true"
    )
    assert (
        cli._wizard_visible_value(
            ["default"],
            path_label="deploy.targets[0].secrets.mysterybox.sync_namespaces",
        )
        == '["default"]'
    )
    assert (
        cli._wizard_visible_value(
            "super-secret",
            path_label="deploy.targets[0].secrets.mysterybox.credentials_secret.key",
        )
        == "<redacted>"
    )
    assert (
        cli._wizard_visible_value(
            [
                {
                    "name": "db-uname-pass",
                    "kubernetes_secret_name": "app-db-creds",
                    "payload": {"USERNAME": {"type": "text"}, "PASSWORD": {"type": "text"}},
                }
            ],
            path_label="infra.components[0].inputs.secrets",
        )
        == "db-uname-pass->app-db-creds (PASSWORD, USERNAME)"
    )


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


def test_persist_cluster_handoff_kubeconfig_creates_missing_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG", "true")
    kubeconfig_path = tmp_path / ".kube" / "config"
    assert not kubeconfig_path.exists()

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
    assert persisted["apiVersion"] == "v1"
    assert persisted["kind"] == "Config"
    assert persisted["current-context"] == "context-entry"
    assert persisted["clusters"][0]["name"] == "cluster-entry"
    assert persisted["users"][0]["name"] == "user-entry"
    assert persisted["contexts"][0]["name"] == "context-entry"


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


@pytest.mark.parametrize(
    ("renewable_args", "expected_renewable"),
    [([], False), (["--require-renewable-auth"], True)],
)
def test_mk8s_token_command_emits_exec_credential_from_sdk(
    renewable_args: list[str],
    expected_renewable: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            captured["timeout"] = timeout
            return SimpleNamespace(
                token="token-123",
                expiration=cli.datetime(2026, 1, 2, 3, 4, 5, tzinfo=cli.UTC),
            )

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            captured["close_timeout"] = timeout
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
        lambda *, parent_id, endpoint, context, require_renewable_auth: (
            captured.update(
                {
                    "sdk_init": (
                        parent_id,
                        endpoint,
                        context,
                        require_renewable_auth,
                    )
                }
            )
            or _FakeSDK()
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
            *renewable_args,
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert captured["cache_load"] == ("project-456", "client-a")
    assert captured["sdk_init"] == (
        "project-456",
        "api.example.invalid",
        "MK8s exec auth",
        expected_renewable,
    )
    assert captured["timeout"] == cli._MK8S_CREDENTIAL_ATTEMPT_TIMEOUT_SECONDS
    assert captured["close_timeout"] == cli._MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS
    assert captured["closed"] is True
    assert payload["apiVersion"] == "client.authentication.k8s.io/v1"
    assert payload["kind"] == "ExecCredential"
    assert payload["status"]["token"] == "token-123"
    assert payload["status"]["expirationTimestamp"] == "2026-01-02T03:04:05Z"


def test_renewable_mk8s_exec_command_reuses_current_python_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _command: "/stale/bin/nebius-cxcli")

    command, args = cli._mk8s_token_exec_command(  # noqa: SLF001
        project_id="project-456",
        client_name="client-a",
        endpoint=None,
        require_renewable_auth=True,
    )

    assert command == sys.executable
    assert args == (
        "-m",
        "nebius_cxcli",
        "mk8s-token",
        "--project-id",
        "project-456",
        "--client-name",
        "client-a",
        "--require-renewable-auth",
    )


def test_mk8s_exec_credential_cache_reuses_one_exchange_across_process_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchanges = 0

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            nonlocal exchanges
            exchanges += 1
            return SimpleNamespace(
                token="cached-token",
                expiration=cli.datetime(2099, 1, 2, 3, 4, 5, tzinfo=cli.UTC),
            )

        def sync_close(self) -> None:
            pass

    cache_file = tmp_path / "exec-credential-cache.json"
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "_init_mk8s_exec_auth_sdk", lambda **_kwargs: _FakeSDK())

    first = runner.invoke(
        cli.app,
        ["mk8s-token", "--project-id", "project-456", "--cache-file", str(cache_file)],
    )
    second = runner.invoke(
        cli.app,
        ["mk8s-token", "--project-id", "project-456", "--cache-file", str(cache_file)],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(first.stdout)["status"] == json.loads(second.stdout)["status"]
    assert exchanges == 1
    assert cache_file.stat().st_mode & 0o777 == 0o600


def test_mk8s_exec_credential_cache_renews_repeatedly_across_token_lifetimes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_datetime = cli.datetime
    clock = {"now": real_datetime(2026, 1, 1, tzinfo=cli.UTC)}
    calls: list[bool] = []

    class _ClockedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            current = clock["now"]
            return current if tz is None else current.astimezone(tz)

    def acquire(**kwargs: Any) -> dict[str, str]:
        calls.append(kwargs["require_renewable_auth"])
        expiration = clock["now"] + timedelta(hours=12)
        return {
            "token": f"token-{len(calls)}",
            "expirationTimestamp": expiration.isoformat().replace("+00:00", "Z"),
        }

    cache_file = tmp_path / "exec-credential-cache.json"
    monkeypatch.setattr(cli, "datetime", _ClockedDateTime)
    monkeypatch.setattr(cli, "_acquire_mk8s_exec_credential_status", acquire)

    def status() -> dict[str, str]:
        return cli._mk8s_exec_credential_status(  # noqa: SLF001
            project_id="project-456",
            client_name="client-a",
            endpoint=None,
            cache_file=cache_file,
            require_renewable_auth=True,
        )

    assert status()["token"] == "token-1"
    clock["now"] += timedelta(hours=11, minutes=56)
    assert status()["token"] == "token-2"
    clock["now"] += timedelta(hours=11, minutes=56)
    assert status()["token"] == "token-3"
    assert calls == [True, True, True]


def test_mk8s_exec_credential_cache_rejects_non_owner_only_directory(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "shared-cache"
    cache_dir.mkdir(mode=0o755)
    cache_dir.chmod(0o755)

    with (
        pytest.raises(RuntimeError, match="directory must be owner-controlled"),
        cli._locked_mk8s_exec_credential_cache(  # noqa: SLF001
            cache_dir / "exec-credential-cache.json"
        ),
    ):
        pass


def test_mk8s_exec_credential_cache_single_flights_concurrent_callers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchanges = 0
    exchange_lock = threading.Lock()

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            nonlocal exchanges
            with exchange_lock:
                exchanges += 1
            time.sleep(0.02)
            return SimpleNamespace(
                token="shared-token",
                expiration=cli.datetime(2099, 1, 2, 3, 4, 5, tzinfo=cli.UTC),
            )

        def sync_close(self) -> None:
            pass

    cache_file = tmp_path / "exec-credential-cache.json"
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "_init_mk8s_exec_auth_sdk", lambda **_kwargs: _FakeSDK())

    def get_status() -> dict[str, str]:
        return cli._mk8s_exec_credential_status(  # noqa: SLF001
            project_id="project-456",
            client_name="client-a",
            endpoint=None,
            cache_file=cache_file,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(lambda _index: get_status(), range(8)))

    assert exchanges == 1
    assert {status["token"] for status in statuses} == {"shared-token"}


def test_mk8s_exec_credential_cache_uses_still_valid_token_when_refresh_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_file = tmp_path / "exec-credential-cache.json"
    binding_sha256 = cli._mk8s_exec_credential_binding_sha256(  # noqa: SLF001
        project_id="project-456",
        client_name="client-a",
        endpoint=None,
    )
    status = {
        "token": "still-valid-token",
        "expirationTimestamp": (cli.datetime.now(cli.UTC) + timedelta(seconds=120))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    cli._write_text_atomic(  # noqa: SLF001
        cache_file,
        json.dumps(
            cli._mk8s_exec_credential_cache_payload(  # noqa: SLF001
                binding_sha256=binding_sha256,
                status=status,
            )
        ),
        file_mode=0o600,
    )
    monkeypatch.setattr(
        cli,
        "_acquire_mk8s_exec_credential_status",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("transient")),
    )

    observed = cli._mk8s_exec_credential_status(  # noqa: SLF001
        project_id="project-456",
        client_name="client-a",
        endpoint=None,
        cache_file=cache_file,
    )

    assert observed == status


def test_mk8s_exec_credential_cache_single_flights_concurrent_failed_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_file = tmp_path / "exec-credential-cache.json"
    binding_sha256 = cli._mk8s_exec_credential_binding_sha256(  # noqa: SLF001
        project_id="project-456",
        client_name="client-a",
        endpoint=None,
    )
    status = {
        "token": "still-valid-token",
        "expirationTimestamp": (cli.datetime.now(cli.UTC) + timedelta(seconds=120))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    cli._write_text_atomic(  # noqa: SLF001
        cache_file,
        json.dumps(
            cli._mk8s_exec_credential_cache_payload(  # noqa: SLF001
                binding_sha256=binding_sha256,
                status=status,
            )
        ),
        file_mode=0o600,
    )
    exchanges = 0
    exchange_lock = threading.Lock()

    def failed_refresh(**_kwargs: Any) -> dict[str, str]:
        nonlocal exchanges
        with exchange_lock:
            exchanges += 1
        time.sleep(0.02)
        raise TimeoutError("transient")

    monkeypatch.setattr(cli, "_acquire_mk8s_exec_credential_status", failed_refresh)

    def get_status() -> dict[str, str]:
        return cli._mk8s_exec_credential_status(  # noqa: SLF001
            project_id="project-456",
            client_name="client-a",
            endpoint=None,
            cache_file=cache_file,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(lambda _index: get_status(), range(8)))

    assert exchanges == 1
    assert statuses == [status] * 8
    cached_payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached_payload["refresh_failed_at"].endswith("Z")


def test_mk8s_exec_credential_cache_does_not_fallback_after_token_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_file = tmp_path / "exec-credential-cache.json"
    binding_sha256 = cli._mk8s_exec_credential_binding_sha256(  # noqa: SLF001
        project_id="project-456",
        client_name="client-a",
        endpoint=None,
    )
    status = {
        "token": "expiring-token",
        "expirationTimestamp": (cli.datetime.now(cli.UTC) + timedelta(seconds=0.03))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    cli._write_text_atomic(  # noqa: SLF001
        cache_file,
        json.dumps(
            cli._mk8s_exec_credential_cache_payload(  # noqa: SLF001
                binding_sha256=binding_sha256,
                status=status,
            )
        ),
        file_mode=0o600,
    )
    monkeypatch.setattr(cli, "_MK8S_EXEC_CREDENTIAL_FALLBACK_SECONDS", 0.01)

    def timed_out_refresh(**_kwargs: Any) -> dict[str, str]:
        time.sleep(0.04)
        raise TimeoutError("transient")

    monkeypatch.setattr(cli, "_acquire_mk8s_exec_credential_status", timed_out_refresh)

    with pytest.raises(TimeoutError, match="transient"):
        cli._mk8s_exec_credential_status(  # noqa: SLF001
            project_id="project-456",
            client_name="client-a",
            endpoint=None,
            cache_file=cache_file,
        )


def test_mk8s_exec_auth_sdk_retries_one_timeout_with_fresh_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    attempts = 0
    closed = 0
    sleeps: list[float] = []

    class _FakeSDK:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt

        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            assert timeout == cli._MK8S_CREDENTIAL_ATTEMPT_TIMEOUT_SECONDS
            if self.attempt == 1:
                raise TimeoutError("transient")
            return expected

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            nonlocal closed
            assert timeout == cli._MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS
            closed += 1

    def _init_sdk(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        return _FakeSDK(attempts)

    monkeypatch.setattr(cli, "init_nebius_sdk", _init_sdk)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    sdk = cli._Mk8sExecAuthSdk(parent_id="project-456", endpoint=None, context="test")

    assert sdk.get_token_sync(timeout=20.0) is expected
    assert attempts == 2
    assert closed == 2
    assert sleeps == [cli._MK8S_CREDENTIAL_RETRY_DELAY_SECONDS]


def test_mk8s_exec_auth_sdk_bounds_timeout_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    closed = 0
    sleeps: list[float] = []

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            assert timeout == cli._MK8S_CREDENTIAL_ATTEMPT_TIMEOUT_SECONDS
            raise TimeoutError("sensitive-timeout-detail")

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            nonlocal closed
            assert timeout == cli._MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS
            closed += 1

    def _init_sdk(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        return _FakeSDK()

    monkeypatch.setattr(cli, "init_nebius_sdk", _init_sdk)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    sdk = cli._Mk8sExecAuthSdk(parent_id="project-456", endpoint=None, context="test")

    with pytest.raises(TimeoutError, match="timed out while creating") as exc_info:
        sdk.get_token_sync(timeout=20.0)

    assert "sensitive-timeout-detail" not in str(exc_info.value)
    assert attempts == cli._MK8S_CREDENTIAL_MAX_ATTEMPTS
    assert closed == cli._MK8S_CREDENTIAL_MAX_ATTEMPTS
    assert sleeps == [cli._MK8S_CREDENTIAL_RETRY_DELAY_SECONDS]


def test_mk8s_exec_auth_sdk_does_not_retry_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    closed = 0
    sleeps: list[float] = []

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            raise RuntimeError("permanent-auth-failure")

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            nonlocal closed
            assert timeout == cli._MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS
            closed += 1

    def _init_sdk(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        return _FakeSDK()

    monkeypatch.setattr(cli, "init_nebius_sdk", _init_sdk)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    sdk = cli._Mk8sExecAuthSdk(parent_id="project-456", endpoint=None, context="test")

    with pytest.raises(RuntimeError, match="permanent-auth-failure"):
        sdk.get_token_sync(timeout=20.0)

    assert attempts == 1
    assert closed == 1
    assert sleeps == []


def test_mk8s_exec_auth_sdk_elapsed_budget_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[float] = []

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            timeouts.append(timeout)
            time.sleep(timeout)
            raise TimeoutError("slow exchange")

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            assert timeout == 0.01

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _FakeSDK())
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_ATTEMPT_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_TOTAL_BUDGET_SECONDS", 0.10)
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_RETRY_DELAY_SECONDS", 0.01)
    sdk = cli._Mk8sExecAuthSdk(parent_id="project-456", endpoint=None, context="test")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out while creating"):
        sdk.get_token_sync(timeout=20.0)
    elapsed = time.monotonic() - started

    assert timeouts == [0.02, 0.02]
    assert elapsed < 0.10


def test_mk8s_exec_auth_sdk_bounds_hanging_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_close = threading.Event()
    close_finished = threading.Event()
    close_timeouts: list[float] = []

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            return object()

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            close_timeouts.append(timeout)
            release_close.wait()
            close_finished.set()

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _FakeSDK())
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS", 0.01)
    sdk = cli._Mk8sExecAuthSdk(parent_id="project-456", endpoint=None, context="test")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="cleanup timed out"):
        sdk.get_token_sync(timeout=20.0)
    elapsed = time.monotonic() - started
    release_close.set()

    assert close_finished.wait(timeout=1.0)
    assert close_timeouts == [0.01]
    assert elapsed < 0.5


def test_mk8s_exec_auth_sdk_bounds_hanging_init_and_cleans_up_late_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_init = threading.Event()
    close_finished = threading.Event()

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            return object()

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            close_finished.set()

    def _init_sdk(**_kwargs):  # type: ignore[no-untyped-def]
        release_init.wait()
        return _FakeSDK()

    monkeypatch.setattr(cli, "init_nebius_sdk", _init_sdk)
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_ATTEMPT_WALL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_TOTAL_BUDGET_SECONDS", 0.10)
    monkeypatch.setattr(cli, "_MK8S_CREDENTIAL_CLOSE_TIMEOUT_SECONDS", 0.01)
    sdk = cli._Mk8sExecAuthSdk(parent_id="project-456", endpoint=None, context="test")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="initialization or exchange exceeded"):
        sdk.get_token_sync(timeout=20.0)
    elapsed = time.monotonic() - started
    release_init.set()

    assert close_finished.wait(timeout=1.0)
    assert elapsed < 0.5


def test_mk8s_token_command_suppresses_sensitive_sdk_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger("nebius.aio.token.renewable")

    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            logger.error("sensitive-refresh-detail")
            raise RuntimeError("sensitive-auth-detail")

        def sync_close(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
            logger.error("sensitive-cleanup-detail")

    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _FakeSDK())

    result = runner.invoke(cli.app, ["mk8s-token", "--project-id", "project-456"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "ERROR: Unable to create an MK8s exec credential (reason: credential-exchange-failed)."
    )


def test_mk8s_token_command_failure_keeps_stdout_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            raise RuntimeError("sensitive-auth-detail")

        def sync_close(self) -> None:
            pass

    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "_init_mk8s_exec_auth_sdk", lambda **_kwargs: _FakeSDK())

    result = runner.invoke(cli.app, ["mk8s-token", "--project-id", "project-456"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Unable to create an MK8s exec credential" in result.stderr
    assert "sensitive-auth-detail" not in result.stderr


def test_mk8s_token_command_rejects_empty_credential_without_retry_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSDK:
        def get_token_sync(self, *, timeout):  # type: ignore[no-untyped-def]
            return SimpleNamespace()

        def sync_close(self) -> None:
            pass

    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    monkeypatch.setattr(cli, "_init_mk8s_exec_auth_sdk", lambda **_kwargs: _FakeSDK())

    result = runner.invoke(cli.app, ["mk8s-token", "--project-id", "project-456"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "ERROR: Unable to create an MK8s exec credential (reason: credential-exchange-failed)."
    )


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
    target_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    (target_paths.flux_dir / "post-flux-mysterybox-eso.yaml").write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "external-secrets.io/v1",
                    "kind": "ExternalSecret",
                    "metadata": {
                        "name": "soperator-notifier-slack-webhook",
                        "namespace": "soperator",
                    },
                    "spec": {
                        "target": {"name": "soperator-notifier-slack-webhook"},
                        "data": [{"secretKey": "url"}],
                    },
                }
            ],
            explicit_start=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config: captured.update({"backend": config}),
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
    monkeypatch.setattr(
        cli,
        "wait_for_rendered_flux_resources",
        lambda paths, *, extra_env=None, emit=None: captured.update(
            {"wait_flux": (paths, extra_env)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_apply_post_flux_manifests",
        lambda paths, *, env: captured.update({"post_flux": (paths, env)}),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_soperator_notifier_runtime_before_flux",
        lambda config, *, extra_env, target_ref="", externally_managed_secret_keys=None: (
            captured.update(
                {
                    "soperator_notifier": (
                        config,
                        extra_env,
                        target_ref,
                        externally_managed_secret_keys,
                    )
                }
            )
        ),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "bootstrap", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == fake_config
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
    assert captured["soperator_notifier"] == (
        fake_config,
        {"KUBECONFIG": "/tmp/kubeconfig"},
        "mk8s",
        {("soperator", "soperator-notifier-slack-webhook", "url")},
    )
    assert captured["wait_flux"] == (target_paths, {"KUBECONFIG": "/tmp/kubeconfig"})
    assert captured["post_flux"][0] == target_paths
    assert captured["post_flux"][1]["KUBECONFIG"] == "/tmp/kubeconfig"


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
    target_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    (target_paths.flux_dir / "post-flux-mysterybox-eso.yaml").write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "external-secrets.io/v1",
                    "kind": "ExternalSecret",
                    "metadata": {
                        "name": "soperator-notifier-slack-webhook",
                        "namespace": "soperator",
                    },
                    "spec": {
                        "target": {"name": "soperator-notifier-slack-webhook"},
                        "data": [{"secretKey": "url"}],
                    },
                }
            ],
            explicit_start=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config: captured.update({"backend": config}),
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
        lambda paths, *, extra_env=None, **_kwargs: captured.update(
            {"apply_flux": (paths, extra_env)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None, **_kwargs: captured.update(
            {"warn_bootstrap": (config, paths, extra_env, target_ref)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.update({"inventory": (config, paths)}),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_soperator_notifier_runtime_before_flux",
        lambda config, *, extra_env, target_ref="", externally_managed_secret_keys=None: (
            captured.update(
                {
                    "soperator_notifier": (
                        config,
                        extra_env,
                        target_ref,
                        externally_managed_secret_keys,
                    )
                }
            )
        ),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "apply", str(tmp_path / "generated")],
    )

    assert result.exit_code == 0, result.output
    assert "Flux applied from" in _plain_output(result.output)
    assert captured["backend"] == fake_config
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
    assert captured["soperator_notifier"] == (
        fake_config,
        {"KUBECONFIG": "/tmp/kubeconfig"},
        "mk8s",
        {("soperator", "soperator-notifier-slack-webhook", "url")},
    )


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
                {"id": "gateway-helm", "enabled": True, "instance_id": "mk8s"},
                {"id": "gateway-helm", "enabled": True, "instance_id": "mk8s-2"},
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
    captured: dict[str, Any] = {
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
        lambda config: captured.update({"backend": config}),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, target=None, persist_local_kubeconfig=True, set_current_context=True: (
            cast(list[tuple[Any, ...]], captured["handoffs"]).append(
                (
                    config,
                    paths,
                    target,
                    persist_local_kubeconfig,
                    set_current_context,
                )
            )
            or {"KUBECONFIG": f"/tmp/{cast(Mapping[str, Any], target)['target_ref']}.kubeconfig"}
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
        lambda paths, *, extra_env=None, **_kwargs: cast(
            list[tuple[ProjectPaths, dict[str, str] | None]], captured["apply_flux"]
        ).append((paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None, target_ref=None, **_kwargs: cast(
            list[tuple[Any, ...]], captured["warn_bootstrap"]
        ).append((config, paths, extra_env, target_ref)),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths, **kwargs: captured.update({"inventory": (config, paths)}),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "apply", str(tmp_path / "generated"), "--all-targets"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == fake_config
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
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli, "_load_generated_flux_context", lambda _path: (fake_config, fake_paths, manifest)
    )
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config: captured.update({"backend": config}),
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
        ["flux", "destroy", str(tmp_path / "generated"), "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Flux resources deleted from" in _plain_output(result.output)
    assert captured["backend"] == fake_config
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
    captured: dict[str, Any] = {}
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

    command = cli._warn_if_flux_gitops_not_bootstrapped(
        {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}},
        fake_paths,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )

    assert len(messages) >= 3
    assert "Flux GitOps bootstrap is not configured" in messages[0]
    assert "Local direct apply succeeded" in messages[0]
    assert "supported operating mode" in messages[0]
    assert "If this cluster should continuously sync from Git" in messages[1]
    assert "local generated bundle path" in messages[1]
    assert f"git origin under {fake_paths.repo_root}" in messages[1]
    assert "Commit and push the rendered generated/flux path" in messages[2]
    assert "skip this step when local direct apply is the intended workflow" in messages[2]
    assert "Optional command to enable GitOps sync:" in messages[3]
    assert (
        cli.copy_paste_command_markup(f"nebius-cxcli flux bootstrap {fake_paths.generated_dir}")
        == messages[4]
    )
    assert command == f"nebius-cxcli flux bootstrap {fake_paths.generated_dir}"

    messages.clear()
    command = cli._warn_if_flux_gitops_not_bootstrapped(
        {"apps": {"charts": [{"id": "gateway-helm", "enabled": True}]}},
        fake_paths,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        print_command=False,
    )

    assert len(messages) == 3
    assert "Flux GitOps bootstrap is not configured" in messages[0]
    assert "Local direct apply succeeded" in messages[0]
    assert "final Deployment summary" in messages[1]
    assert "Optional command to enable GitOps sync:" not in "\n".join(messages)
    assert command == f"nebius-cxcli flux bootstrap {fake_paths.generated_dir}"


def test_help_text_aligns_render_and_apply_surfaces() -> None:
    top_result = runner.invoke(cli.app, ["--help"])
    quota_check_result = runner.invoke(cli.app, ["quota-check", "--help"])
    quota_request_result = runner.invoke(cli.app, ["quota-request", "--help"])
    grafana_result = runner.invoke(cli.app, ["grafana", "--help"])
    render_result = runner.invoke(cli.app, ["render", "--help"])
    validate_dashboards_result = runner.invoke(cli.app, ["validate-dashboards", "--help"])
    deploy_result = runner.invoke(cli.app, ["deploy", "--help"])
    destroy_result = runner.invoke(cli.app, ["destroy", "--help"])
    tf_apply_result = runner.invoke(cli.app, ["terraform", "apply", "--help"])
    tf_destroy_result = runner.invoke(cli.app, ["terraform", "destroy", "--help"])
    flux_apply_result = runner.invoke(cli.app, ["flux", "apply", "--help"])
    flux_destroy_result = runner.invoke(cli.app, ["flux", "destroy", "--help"])
    flux_bootstrap_result = runner.invoke(cli.app, ["flux", "bootstrap", "--help"])
    upgrade_result = runner.invoke(cli.app, ["upgrade", "--help"])
    upgrade_node_template_result = runner.invoke(cli.app, ["upgrade", "node-template", "--help"])
    migrate_result = runner.invoke(cli.app, ["migrate", "--help"])
    migrate_node_group_result = runner.invoke(cli.app, ["migrate", "node-group", "--help"])
    removed_upgrade_results = {
        command: runner.invoke(cli.app, ["upgrade", command, "--help"])
        for command in (
            "k8s-" + "version",
            "os-" + "image",
            "gpu-" + "fabric",
            "gpu-" + "stack-preset",
            "platform",
            "cpu-" + "preset",
            "gpu-" + "preset",
            "node-group",
        )
    }
    upgrade_helm_result = runner.invoke(cli.app, ["upgrade", "helm-chart", "--help"])
    upgrade_firmware_result = runner.invoke(cli.app, ["upgrade", "firmware", "--help"])

    assert top_result.exit_code == 0, top_result.output
    assert quota_check_result.exit_code == 0, quota_check_result.output
    assert quota_request_result.exit_code == 0, quota_request_result.output
    assert grafana_result.exit_code == 0, grafana_result.output
    assert render_result.exit_code == 0, render_result.output
    assert validate_dashboards_result.exit_code == 0, validate_dashboards_result.output
    assert deploy_result.exit_code == 0, deploy_result.output
    assert destroy_result.exit_code == 0, destroy_result.output
    assert tf_apply_result.exit_code == 0, tf_apply_result.output
    assert tf_destroy_result.exit_code == 0, tf_destroy_result.output
    assert flux_apply_result.exit_code == 0, flux_apply_result.output
    assert flux_destroy_result.exit_code == 0, flux_destroy_result.output
    assert flux_bootstrap_result.exit_code == 0, flux_bootstrap_result.output
    assert upgrade_result.exit_code == 0, upgrade_result.output
    assert upgrade_node_template_result.exit_code == 0, upgrade_node_template_result.output
    assert migrate_result.exit_code == 0, migrate_result.output
    assert migrate_node_group_result.exit_code == 0, migrate_node_group_result.output
    for command, result in removed_upgrade_results.items():
        assert result.exit_code != 0, (command, result.output)
        assert "No such command" in _plain_output(result.output)
    assert upgrade_helm_result.exit_code == 0, upgrade_helm_result.output
    assert upgrade_firmware_result.exit_code != 0
    assert "No such command" in _plain_output(upgrade_firmware_result.output)

    render_help = " ".join(_plain_output(render_result.output).split()).lower()
    top_help = " ".join(_plain_output(top_result.output).split()).lower()
    validate_dashboards_help = " ".join(
        _plain_output(validate_dashboards_result.output).split()
    ).lower()
    deploy_help = " ".join(_plain_output(deploy_result.output).split()).lower()
    destroy_help = " ".join(_plain_output(destroy_result.output).split()).lower()
    tf_apply_help = " ".join(_plain_output(tf_apply_result.output).split()).lower()
    tf_destroy_help = " ".join(_plain_output(tf_destroy_result.output).split()).lower()
    flux_apply_help = " ".join(_plain_output(flux_apply_result.output).split()).lower()
    flux_destroy_help = " ".join(_plain_output(flux_destroy_result.output).split()).lower()
    flux_bootstrap_help = " ".join(_plain_output(flux_bootstrap_result.output).split()).lower()
    upgrade_help = " ".join(_plain_output(upgrade_result.output).split()).lower()
    upgrade_node_template_help = " ".join(
        _plain_output(upgrade_node_template_result.output).split()
    ).lower()
    migrate_help = " ".join(_plain_output(migrate_result.output).split()).lower()
    migrate_node_group_help = " ".join(
        _plain_output(migrate_node_group_result.output).split()
    ).lower()
    upgrade_helm_help = " ".join(_plain_output(upgrade_helm_result.output).split()).lower()
    quota_check_help = " ".join(_plain_output(quota_check_result.output).split()).lower()
    quota_request_help = " ".join(_plain_output(quota_request_result.output).split()).lower()
    grafana_help = " ".join(_plain_output(grafana_result.output).split()).lower()
    upgrade_example_config = "~/deployments/tenant-name-example/project-name-example/config.yaml"

    assert (
        "validate, validate-dashboards, quota-check, quota-request, render, deploy, "
        "acceptance-test, soperator, upgrade, migrate, and bootstrap-ci use config.yaml"
    ) in top_help
    assert (
        f"upgrade example: | nebius-cxcli upgrade node-template {upgrade_example_config} "
        "infra:mk8s@mk8s --to-version 1.33 --dry-run"
    ) in top_help
    assert "nebius-cxcli upgrade --help" in top_help
    assert "live nebius quota/capacity assessment" in quota_check_help
    assert "quota allowances to confirm the shortage" in quota_request_help
    assert "export grafana dashboards through the api or local json" in grafana_help
    assert "--export-dashboard" in grafana_help
    assert "--dashboard-json" in grafana_help
    assert "grafana base url or folder url to export from" in grafana_help
    assert "dashboard url to export from" not in grafana_help
    assert "repeat to process multiple files" in grafana_help
    assert "repeat to attach multiple files" not in grafana_help
    assert "--attach" in grafana_help
    assert "examples:" in grafana_help
    assert "interactive api export" in grafana_help
    assert "non-interactive api export" in grafana_help
    assert "api export with catalog attach" in grafana_help
    assert "local json attach without grafana api credentials" in grafana_help
    assert "multiple local json files with an explicit catalog" in grafana_help
    assert (
        "nebius-cxcli grafana --export-dashboard https://grafana.example.invalid/" in grafana_help
    )
    assert "grafana.nebius.dev" not in grafana_help
    assert "nebius-cxcli grafana --dashboard-json ./dashboards/mk8s/custom.json" in grafana_help
    assert "separate request surface" in quota_request_help
    assert "confirmed live quota shortages" in quota_request_help
    assert "--all-regions" in quota_check_help
    assert "selected config region still" in quota_check_help
    assert "quota-only" in quota_check_help
    assert (
        "prompting only before replacing existing render-owned artifacts unless --force is provided"
        in render_help
    )
    assert "target cluster instance_id" in validate_dashboards_help
    assert "explicit kube context" in validate_dashboards_help
    assert "target_ref" not in validate_dashboards_help
    assert "generated artifact bundle" in deploy_help
    assert "does not run `flux bootstrap`" in deploy_help
    assert "does not create or update github workflows" in deploy_help
    assert "deploy reconciles every target by default" in deploy_help
    assert "use `--target <target-id>` to narrow flux/app work" in deploy_help
    assert "the default all-target behavior" in deploy_help
    assert "for a single-target run, the refreshed validation summary" in deploy_help
    assert "include only validations for that selected target" in deploy_help
    assert "validation pass/fail" in deploy_help
    assert "copy-paste commands" in deploy_help
    assert "important generated paths" in deploy_help
    assert "day-2 lifecycle upgrades from config.yaml" in upgrade_help
    assert "non-soperator target-scoped helm chart upgrades" in upgrade_help
    legacy_scope_label = "V" + "1"
    assert legacy_scope_label not in upgrade_help
    for removed_command in ("k8s-" + "version", "os-" + "image"):
        assert removed_command not in upgrade_help
    assert "node-template" in upgrade_help
    assert "nebius-cxcli upgrade node-group" not in upgrade_help
    assert "helm-chart" in upgrade_help
    assert "gpu-" + "fabric" not in upgrade_help
    assert "upgrade gpu-" + "stack-preset" not in upgrade_help
    assert "cpu-" + "preset" not in upgrade_help
    assert "gpu-" + "preset" not in upgrade_help
    assert "nebius-cxcli upgrade " + "platform" not in upgrade_help
    assert "firmware" not in upgrade_help
    assert "examples:" in upgrade_help
    assert "reserved future command shapes:" not in upgrade_help
    assert "--yes" not in upgrade_help
    assert "--disruption-policy" not in upgrade_help
    assert "allow-unavailable" not in upgrade_help
    assert f"nebius-cxcli upgrade node-template {upgrade_example_config}" in upgrade_help
    assert "comments: guided wizard" in upgrade_help
    assert (
        "nebius-cxcli upgrade node-template <config.yaml> infra:mk8s@<target> "
        "--to-version 1.33 --to-os ubuntu24.04 --to-gpu-stack-preset cuda13.0 "
        "--dry-run"
    ) in upgrade_help
    assert (
        "nebius-cxcli upgrade node-template <config.yaml> infra:mk8s@<target> "
        "--to-os ubuntu24.04 --node-group system --dry-run"
    ) in upgrade_help
    assert "one non-interactive command" not in upgrade_help
    assert "create-cutover-retire migrations" in migrate_help
    assert "nebius-cxcli migrate node-group" in migrate_help
    assert (
        "nebius-cxcli upgrade helm-chart <config.yaml> apps:<chart>@<target> "
        "--to-version <chart-version>"
    ) in upgrade_help
    assert "upgrade mk8s kubernetes version, os image, or gpu stack values" in (
        upgrade_node_template_help
    )
    assert "config_yaml [target]" in upgrade_node_template_help
    assert "--to-version" in upgrade_node_template_help
    assert "--to-os" in upgrade_node_template_help
    assert "--to-gpu-stack-preset" in upgrade_node_template_help
    assert "--node-group" in upgrade_node_template_help
    assert "--dry-run" in upgrade_node_template_help
    assert "--yes" not in upgrade_node_template_help
    assert "--interactive" in upgrade_node_template_help
    assert "--no-interactive" in upgrade_node_template_help
    assert "--strategy" in upgrade_node_template_help
    assert "--strategy-max-surge-count" in upgrade_node_template_help
    assert "--drain-timeout" in upgrade_node_template_help
    assert "--disruption-policy" not in upgrade_node_template_help
    assert "allow-unavailable" not in upgrade_node_template_help
    assert "--auto-auth-bootstrap" not in upgrade_node_template_help
    assert "--skip-validations" in upgrade_node_template_help
    assert "--skip-validation" in upgrade_node_template_help
    assert f"guided wizard: | nebius-cxcli upgrade node-template {upgrade_example_config}" in (
        upgrade_node_template_help
    )
    assert (
        "dry-run plan: | nebius-cxcli upgrade node-template <config.yaml> "
        "infra:mk8s@<target> --to-version 1.33 --to-os ubuntu24.04 "
        "--to-gpu-stack-preset cuda13.0 --dry-run"
    ) in upgrade_node_template_help
    assert "omitted --to-version, --to-os, and --to-gpu-stack-preset values keep" in (
        upgrade_node_template_help
    )
    assert "--no-interactive for automation" in upgrade_node_template_help
    assert "selected node group rolls once" in upgrade_node_template_help
    assert "final mk8s readiness check" in upgrade_node_template_help
    assert "generated/reports/upgrade-node-template-report.md" in upgrade_node_template_help
    assert "generated/reports/upgrade-node-template-report.json" in upgrade_node_template_help
    assert "execute one explicit terraform-managed node-group migration" in (
        migrate_node_group_help
    )
    assert "config_yaml [target]" in migrate_node_group_help
    assert "--node-group" in migrate_node_group_help
    assert "--replacement-node-gro" in migrate_node_group_help
    assert "--replacement-name" in migrate_node_group_help
    assert "--to-platform" in migrate_node_group_help
    assert "--to-preset" in migrate_node_group_help
    assert "--to-os" in migrate_node_group_help
    assert "--to-gpu-stack-preset" in migrate_node_group_help
    assert "--to-fabric" in migrate_node_group_help
    assert "--dry-run" in migrate_node_group_help
    assert "--execute" in migrate_node_group_help
    assert "--approve" in migrate_node_group_help
    assert "generated/reports/migrate-node-group-report" in migrate_node_group_help
    assert (
        "nebius-cxcli migrate node-group <config.yaml> infra:mk8s@<target>"
    ) in migrate_node_group_help
    assert "use upgrade node-template for in-place kubernetes, os, or gpu-stack changes" in (
        migrate_node_group_help
    )
    assert "--interactive" in migrate_node_group_help
    assert "--no-interactive" in migrate_node_group_help
    assert "--yes" not in migrate_node_group_help
    assert "apps:grafana@mk8s" in upgrade_helm_help
    assert "apps:soperator@mk8s" not in upgrade_helm_help
    assert "reserved future command shape" not in upgrade_helm_help
    assert "config_yaml [target]" in upgrade_helm_help
    assert "--to-version" in upgrade_helm_help
    assert "--dry-run" in upgrade_helm_help
    assert "--interactive" in upgrade_helm_help
    assert "--no-interactive" in upgrade_helm_help
    assert "--yes" not in upgrade_helm_help
    assert "--strategy" not in upgrade_helm_help
    assert (
        "example: | nebius-cxcli upgrade helm-chart <config.yaml> "
        "apps:<chart>@mk8s --to-version <chart-version> --dry-run"
    ) in upgrade_helm_help
    assert "use nebius-cxcli soperator upgrade for soperator chart upgrades" in (upgrade_helm_help)
    assert "destroy all rendered project resources" in destroy_help
    assert "destructive inverse of `deploy`" in destroy_help
    assert "whole rendered project" in destroy_help
    assert "--yes" in destroy_help
    assert "refresh the deploy report" in deploy_help
    assert "refresh the deploy report" in tf_apply_help
    assert "terraform destroy" in tf_destroy_help
    assert "--yes" in tf_destroy_help
    assert "refresh the deploy report" in flux_apply_help
    for option in (
        "--job-policy",
        "--cancel-job",
        "--requeue-job",
        "--job-wait-timeout",
        "--job-refresh-interval",
    ):
        assert _help_contains_option(deploy_help, option)
        assert _help_contains_option(flux_apply_help, option)
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
        "create bootstraps one name-based tenant/project folder from a deployments root directory, creating that root when missing"
        in output
    )
    assert (
        "overwrites existing resolved project folders only with confirmation unless --force is provided"
        in output
    )
    assert (
        "component list/add/remove use --config CONFIG_YAML as the day-2 config.yaml editing surface"
        in output
    )
    assert "discover uses a deployment-scope directory" in output
    assert (
        "grafana exports dashboard JSON from a Grafana API or local JSON file "
        "and only edits component_sources.yaml with --attach"
    ) in output
    assert (
        "validate, validate-dashboards, quota-check, quota-request, render, "
        "deploy, acceptance-test, soperator, upgrade, migrate, and bootstrap-ci use config.yaml"
    ) in output
    assert (
        "destroy uses config.yaml to tear down all rendered project resources from sibling generated/"
        in output
    )
    assert "email also uses config.yaml and resolves sibling generated/ automatically" in output
    assert (
        "wireguard uses config.yaml to generate client configs and manage VM-local "
        "WireGuard route defaults from a deployed VPN gateway"
    ) in output
    assert "ssh-jumphost uses config.yaml to manage VM-local SSH source CIDR allowlists" in output
    assert "validate-generated uses generated/" in output
    assert "terraform uses generated/infra" in output
    assert "flux uses generated/flux" in output
    assert "validate-sources accepts optional component_sources.yaml" in output
    assert "auth has no positional path" in output
    assert "soperator onboard registers existing Nebius MK8s targets in config.yaml" in output
    assert "report Use CONFIG_YAML" not in output
    assert "bootstrap-ci Use CONFIG_YAML" in output
    assert "component" in output
    assert "grafana Export or attach Grafana dashboard JSON" in output
    assert "validate Use CONFIG_YAML" in output
    assert "validate-dashboards Use CONFIG_YAML" in output
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


def test_public_command_help_omits_legacy_mk8s_shortcut_fields() -> None:
    legacy_tokens = (
        "mk8s_cluster_public_endpoint",
        "kube_network_service_cidrs",
        "cpu_nodes_count",
        "cpu_nodes_platform",
        "cpu_nodes_preset",
        "cpu_nodes_os",
        "cpu_nodes_boot_disk_size_gib",
        "cpu_nodes_boot_disk_type",
        "gpu_enabled",
        "gpu_node_groups",
        "gpu_nodes_count_per_group",
        "gpu_nodes_platform",
        "gpu_nodes_preset",
        "gpu_nodes_os",
        "gpu_nodes_boot_disk_size_gib",
        "gpu_nodes_boot_disk_type",
        "mk8s_cluster_overrides",
        "mk8s_cpu_node_group_overrides",
        "mk8s_gpu_node_group_overrides",
    )
    help_commands = (
        ["--help"],
        ["create", "--help"],
        ["component", "--help"],
        ["component", "list", "--help"],
        ["component", "add", "--help"],
        ["component", "remove", "--help"],
        ["validate", "--help"],
        ["quota-check", "--help"],
        ["quota-request", "--help"],
        ["render", "--help"],
        ["validate-generated", "--help"],
        ["deploy", "--help"],
        ["destroy", "--help"],
        ["terraform", "plan", "--help"],
        ["terraform", "apply", "--help"],
        ["terraform", "destroy", "--help"],
        ["terraform", "unlock", "--help"],
        ["flux", "apply", "--help"],
        ["flux", "bootstrap", "--help"],
        ["flux", "destroy", "--help"],
        ["discover", "--help"],
        ["email", "--help"],
    )

    for args in help_commands:
        result = runner.invoke(cli.app, args, env={"COLUMNS": "240"}, terminal_width=240)

        assert result.exit_code == 0, result.output
        help_text = _plain_output(result.output)
        assert not any(token in help_text for token in legacy_tokens), args


def test_bootstrap_ci_help_reflects_reconcile_first_contract() -> None:
    result = runner.invoke(cli.app, ["bootstrap-ci", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    output = " ".join(plain_output.split()).lower()

    assert "--force" not in output
    assert "--auth-bootstrap" not in output
    assert "--no-auth-bootstrap" not in output
    assert "--cli-ref" in output
    assert "CLI-managed customer GitHub workflow" in plain_output
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
    assert "with --project-id; the canonical runtime cache" in normalized_output
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
    captured: dict[str, Any] = {}
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


def test_interactive_email_settings_setup_always_enables_starttls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "email.yaml"

    def _prompt(message: str, **_kwargs: object) -> str:
        if message == "SMTP host (blank disables local email config)":
            return "smtp.example.com"
        if message == "SMTP port":
            return "587"
        if message == "SMTP from address (blank uses username or noreply@localhost)":
            return "deployments@example.com"
        if message == "SMTP username (blank disables SMTP auth)":
            return ""
        raise AssertionError(f"unexpected prompt: {message}")

    monkeypatch.setattr(cli.typer, "prompt", _prompt)
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("STARTTLS should not be optional")
        ),
    )

    settings, written_path = cli._interactive_email_settings_setup(config_path=config_path)

    assert written_path == config_path.resolve()
    assert settings.starttls is True
    assert cli.load_email_settings(explicit=config_path).starttls is True


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
    assert "canonical project authentication" in output
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
    assert "nebius-cxcli deploy" not in _plain_output(result.output)


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
    assert "--create" not in output
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
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=tmp_path / "auth-private.pem",
        private_key_pem="PRIVATE",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    material_with_s3 = replace(
        material,
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )

    captured: dict[str, Any] = {}

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
    monkeypatch.setattr(
        cli,
        "_ensure_deployments_gitignore",
        lambda **_kwargs: SimpleNamespace(path=None, wrote=False),
    )
    monkeypatch.setattr(cli, "_load_local_email_settings", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(
        cli,
        "_runtime_auth_cache_material",
        lambda **kwargs: captured.update({"cache_lookup": kwargs}) or material,
    )
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_s3_material",
        lambda value: captured.update({"s3_input": value}) or material_with_s3,
    )
    monkeypatch.setattr(
        cli,
        "_export_runtime_auth_material",
        lambda value: captured.update({"exported": value}),
    )
    monkeypatch.setattr(
        cli,
        "_sync_runtime_auth_profile_to_ci_environment",
        lambda **kwargs: (
            captured.update({"auth_sync": kwargs})
            or ("owner/repo", "client-a-project-123", ["NEBIUS_SA_ID"])
        ),
    )
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
            "--github-repo",
            "owner/repo",
            "--github-token-env",
            "MY_GH_TOKEN",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "CI bootstrap completed." in _plain_output(result.output)
    assert captured["cache_lookup"] == {
        "project_id": "project-123",
        "client_name": "client-a",
    }
    assert captured["s3_input"] == material
    assert captured["exported"] == material_with_s3
    assert captured["auth_sync"] == {
        "material": material_with_s3,
        "client_name": "client-a",
        "github_repo": "owner/repo",
        "github_token_env": "MY_GH_TOKEN",
        "repo_root_hint": tmp_path,
    }
    assert captured["cli_ref"] == cli.default_cli_ref()


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


def test_auth_target_ensures_profile_automatically(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _material: None)

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a"],
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
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _material: None)

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a", "--recreate"],
    )

    assert result.exit_code == 0, result.output
    assert "Recreated runtime auth profile for project 'project-123'" in _plain_output(
        result.output
    )


def test_load_context_authenticates_before_normalized_config_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    fake_config = SimpleNamespace()
    fake_paths = _fake_paths(tmp_path)
    events: list[object] = []

    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path, *, persist_normalized: (
            events.append(("load", path, persist_normalized)) or fake_config
        ),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_material",
        lambda config, *, need_terraform: events.append(("auth", config, need_terraform)),
    )
    monkeypatch.setattr(cli, "to_plain_data", lambda _config: {})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: fake_paths)
    monkeypatch.setattr(
        cli,
        "validate_path_alignment",
        lambda config, paths: events.append(("alignment", config, paths)),
    )

    config, paths = cli._load_context(config_path)

    assert (config, paths) == (fake_config, fake_paths)
    assert events[:3] == [
        ("load", config_path, False),
        ("auth", fake_config, False),
        ("load", config_path, True),
    ]


def test_manifest_context_authenticates_before_tfvars_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}
    fake_config = SimpleNamespace()
    events: list[object] = []

    monkeypatch.setattr(
        cli,
        "load_generated_manifest",
        lambda path: events.append(("manifest", path)) or manifest,
    )
    monkeypatch.setattr(cli, "_apply_generated_tool_version_overrides", lambda _manifest: None)
    monkeypatch.setattr(cli, "runtime_config_from_manifest", lambda _manifest: fake_config)
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_material",
        lambda config, *, need_terraform: events.append(("auth", config, need_terraform)),
    )
    monkeypatch.setattr(
        cli,
        "_materialize_generated_terraform_tfvars",
        lambda paths, loaded_manifest: events.append(("tfvars", paths, loaded_manifest)),
    )

    returned = cli._load_manifest_backed_context(fake_paths)

    assert returned == (fake_config, fake_paths, manifest)
    assert events == [
        ("manifest", fake_paths.generated_dir),
        ("auth", fake_config, False),
        ("tfvars", fake_paths, manifest),
    ]


def test_source_payload_authenticates_after_exact_project_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "client_info": {
                    "client_name": "client-a",
                    "nebius": {
                        "tenant_id": "tenant-123",
                        "project_id": "project-123",
                        "region_id": "eu-north1",
                    },
                    "notifications": {},
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(cli, "_load_source_payload", _REAL_LOAD_SOURCE_PAYLOAD)
    monkeypatch.setattr(
        cli,
        "_ensure_project_auth_identity",
        lambda **kwargs: calls.append(dict(kwargs)),
    )

    payload = cli._load_source_payload(config_path)

    assert payload["client_info"]["nebius"]["project_id"] == "project-123"
    assert calls == [{"project_id": "project-123", "client_name": "client-a"}]


def test_runtime_auth_cache_metadata_write_keeps_previous_file_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_file = tmp_path / cli._RUNTIME_AUTH_CACHE_FILE
    metadata_file.write_text('{"old": true}\n', encoding="utf-8")

    def _fail_replace(_src: object, _dst: object) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(cli.os, "replace", _fail_replace)

    with pytest.raises(RuntimeError, match="replace failed"):
        cli._runtime_auth_cache_write_metadata(metadata_file, {"new": True})

    assert json.loads(metadata_file.read_text(encoding="utf-8")) == {"old": True}
    assert not list(tmp_path.glob(f".{cli._RUNTIME_AUTH_CACHE_FILE}.*.tmp"))


def test_runtime_auth_cache_write_fsyncs_parent_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_file = tmp_path / cli._RUNTIME_AUTH_CACHE_FILE
    events: list[str] = []
    real_fsync = cli.os.fsync
    real_replace = cli.os.replace

    def _record_fsync(descriptor: int) -> None:
        events.append(
            "directory-fsync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file-fsync"
        )
        real_fsync(descriptor)

    def _record_replace(source: object, destination: object) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(cli.os, "fsync", _record_fsync)
    monkeypatch.setattr(cli.os, "replace", _record_replace)

    cli._runtime_auth_cache_write_metadata(metadata_file, {"new": True})

    assert events == ["file-fsync", "replace", "directory-fsync"]


def test_runtime_auth_cache_material_repr_redacts_secret_values(tmp_path: Path) -> None:
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-123",
        private_key_file=tmp_path / "auth-private.pem",
        private_key_pem="PRIVATE-SECRET-VALUE",
        s3_access_key_id="access-key-id",
        s3_secret_access_key="S3-SECRET-VALUE",
    )

    rendered = repr(material)

    assert "PRIVATE-SECRET-VALUE" not in rendered
    assert "S3-SECRET-VALUE" not in rendered
    assert "serviceaccount-123" in rendered
    assert "publickey-123" in rendered


def test_runtime_auth_cache_rotation_keeps_previous_key_on_metadata_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-old",
        private_key_pem="OLD-PRIVATE-KEY",
    )
    original_replace = cli.os.replace

    def _fail_metadata_commit(src: object, dst: object) -> None:
        if Path(str(dst)).name == cli._RUNTIME_AUTH_CACHE_FILE:
            raise RuntimeError("metadata commit failed")
        original_replace(src, dst)

    monkeypatch.setattr(cli.os, "replace", _fail_metadata_commit)

    with pytest.raises(RuntimeError, match="metadata commit failed"):
        cli._runtime_auth_cache_write(
            project_id="project-123",
            client_name="client-a",
            service_account_id="serviceaccount-123",
            auth_public_key_id="publickey-new",
            private_key_pem="NEW-PRIVATE-KEY",
        )

    material = cli._runtime_auth_cache_material(
        project_id="project-123",
        client_name="client-a",
    )
    assert material is not None
    assert material.auth_public_key_id == "publickey-old"
    assert material.private_key_pem == "OLD-PRIVATE-KEY"


def test_runtime_auth_cache_write_creates_private_files_with_restricted_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))

    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-123",
        private_key_pem="PRIVATE-KEY",
        s3_access_key_id="access-key",
        s3_secret_access_key="secret-key",
    )

    cache_dir = cli._runtime_auth_cache_dir(project_id="project-123", client_name="client-a")
    assert cache_dir == cli._runtime_auth_cache_dir(
        project_id="project-123", client_name="different-client"
    )
    metadata_file = cache_dir / cli._RUNTIME_AUTH_CACHE_FILE
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    private_key_file = cache_dir / metadata["private_key_file"]

    assert oct(cache_dir.stat().st_mode & 0o777) == "0o700"
    assert private_key_file.read_text(encoding="utf-8") == "PRIVATE-KEY\n"
    assert oct(private_key_file.stat().st_mode & 0o777) == "0o600"
    assert oct(metadata_file.stat().st_mode & 0o777) == "0o600"
    assert metadata["schema"] == cli._RUNTIME_AUTH_CACHE_SCHEMA
    assert metadata["service_account_name"] == "nebius-cxcli-sa"
    assert metadata["s3_secret_access_key"] == "secret-key"


def test_runtime_auth_cache_rejects_legacy_identity_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cache_dir = cli._runtime_auth_cache_dir(project_id="project-123", client_name="client-a")
    cache_dir.mkdir(parents=True)
    cache_dir.chmod(0o700)
    key_file = cache_dir / "auth-private.pem"
    key_file.write_text("KEY\n", encoding="utf-8")
    key_file.chmod(0o600)
    metadata_file = cache_dir / cli._RUNTIME_AUTH_CACHE_FILE
    metadata_file.write_text(
        json.dumps(
            {
                "schema": cli._RUNTIME_AUTH_CACHE_SCHEMA,
                "state": cli._RUNTIME_AUTH_CACHE_STATE_READY,
                "project_id": "project-123",
                "client_name": "client-a",
                "service_account_name": "nebius-cxcli-tf-sa",
                "service_account_id": "legacy-sa",
                "auth_public_key_id": "legacy-key",
                "private_key_file": key_file.name,
            }
        ),
        encoding="utf-8",
    )
    metadata_file.chmod(0o600)

    with pytest.raises(RuntimeError, match="canonical nebius-cxcli-sa identity"):
        cli._runtime_auth_cache_material(project_id="project-123", client_name="client-a")


def test_runtime_auth_cache_rejects_symlinked_private_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-123",
        private_key_pem="PRIVATE-KEY",
    )
    cache_dir = cli._runtime_auth_cache_dir(project_id="project-123", client_name="client-a")
    metadata = json.loads((cache_dir / cli._RUNTIME_AUTH_CACHE_FILE).read_text(encoding="utf-8"))
    private_key_file = cache_dir / metadata["private_key_file"]
    alternate_key_file = cache_dir / "alternate.pem"
    alternate_key_file.write_text("ALTERNATE-KEY\n", encoding="utf-8")
    alternate_key_file.chmod(0o600)
    private_key_file.unlink()
    private_key_file.symlink_to(alternate_key_file.name)

    with pytest.raises(RuntimeError, match="private key file must not be a symlink"):
        cli._runtime_auth_cache_material(project_id="project-123", client_name="client-a")


def test_runtime_auth_profile_status_reports_unsafe_file_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-123",
        private_key_pem="PRIVATE-KEY",
    )
    cache_dir = cli._runtime_auth_cache_dir(project_id="project-123", client_name="client-a")
    metadata_file = cache_dir / cli._RUNTIME_AUTH_CACHE_FILE
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    private_key_file = cache_dir / metadata["private_key_file"]
    metadata_file.chmod(0o644)
    private_key_file.chmod(0o644)
    monkeypatch.setattr(cli, "auth_public_key_exists", lambda **_kwargs: True)

    status = cli._runtime_auth_profile_status(
        project_id="project-123",
        client_name="client-a",
        profile=None,
        endpoint=None,
        sdk_config_file=None,
    )

    assert "runtime-auth metadata file permissions must be 0600" in status.issues
    assert "private key file permissions must be 0600" in status.issues


def test_runtime_auth_key_intent_resumes_without_duplicate_cloud_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    monkeypatch.setattr(
        cli,
        "generate_service_account_auth_key_pair",
        lambda: ("PRIVATE", "PUBLIC"),
    )
    cli._runtime_auth_create_key_intent(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
    )
    monkeypatch.setattr(
        cli,
        "ensure_ci_service_account_identity",
        lambda **_kwargs: SimpleNamespace(service_account_id="sa-123"),
    )
    monkeypatch.setattr(
        cli,
        "service_account_auth_public_key_ids_by_description",
        lambda **_kwargs: ("key-existing",),
    )
    monkeypatch.setattr(
        cli,
        "create_service_account_auth_public_key",
        lambda **_kwargs: pytest.fail("recovered intent must not upload a duplicate key"),
    )

    material, created = cli._create_or_recreate_runtime_auth_profile(
        project_id="project-123",
        client_name="client-a",
        recreate=False,
        profile=None,
        endpoint=None,
        sdk_config_file=None,
    )

    assert created is True
    assert material.service_account_id == "sa-123"
    assert material.auth_public_key_id == "key-existing"
    assert material.s3_access_key_id is None
    assert not (material.private_key_file.parent / cli._RUNTIME_AUTH_KEY_INTENT_FILE).exists()


def test_runtime_auth_imports_exact_canonical_ci_environment_without_new_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path / "cache"))
    private_key_file = tmp_path / "ci-auth-private.pem"
    private_key_file.write_text("CI-PRIVATE-KEY\n", encoding="utf-8")
    private_key_file.chmod(0o600)
    monkeypatch.setenv("NEBIUS_SA_ID", "serviceaccount-123")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "publickey-123")
    monkeypatch.setenv(cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV, "project-123")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_PEM", "CI-PRIVATE-KEY")
    monkeypatch.setenv("NEBIUS_S3_ACCESS_KEY_ID", "s3-access")
    monkeypatch.setenv("NEBIUS_S3_SECRET_ACCESS_KEY", "s3-secret")
    identity_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "service_account_id_by_name",
        lambda **_kwargs: "serviceaccount-123",
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "ensure_ci_service_account_identity",
        lambda **kwargs: (
            identity_calls.append(kwargs)
            or SimpleNamespace(service_account_id="serviceaccount-123")
        ),
    )
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
    monkeypatch.setattr(
        cli,
        "generate_service_account_auth_key_pair",
        lambda: pytest.fail("canonical CI credentials must not create another key"),
    )

    material, created = cli._create_or_recreate_runtime_auth_profile(
        project_id="project-123",
        client_name="client-a",
        recreate=False,
        profile=None,
        endpoint=None,
        sdk_config_file=None,
    )

    assert created is False
    assert material.service_account_id == "serviceaccount-123"
    assert material.auth_public_key_id == "publickey-123"
    assert material.s3_access_key_id == "s3-access"
    assert material.s3_secret_access_key == "s3-secret"
    assert len(identity_calls) == 1
    assert identity_calls[0]["prefer_operator_auth"] is False
    assert identity_calls[0]["allow_mutation"] is False
    assert identity_calls[0]["allow_cli_token"] is False


def test_runtime_auth_cached_profile_is_returned_before_iam_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-123",
        private_key_pem="PRIVATE-KEY",
    )
    monkeypatch.setattr(
        cli,
        "ensure_ci_service_account_identity",
        lambda **_kwargs: pytest.fail(
            "cached key validity must be checked before any IAM reconciliation"
        ),
    )

    material, created = cli._create_or_recreate_runtime_auth_profile(
        project_id="project-123",
        client_name="client-a",
        recreate=False,
        profile=None,
        endpoint=None,
        sdk_config_file=None,
    )

    assert created is False
    assert material.service_account_id == "serviceaccount-123"
    assert material.auth_public_key_id == "publickey-123"


def test_runtime_auth_recreate_uses_operator_credentials_not_stale_cached_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_runtime_auth_env()
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-stale",
        private_key_pem="STALE-PRIVATE-KEY",
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )
    monkeypatch.setenv(cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV, "project-123")
    monkeypatch.setenv("NEBIUS_SA_ID", "serviceaccount-123")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "publickey-stale")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(tmp_path / "stale.pem"))
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_PEM", "STALE-PRIVATE-KEY")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token")
    captured: dict[str, str | None] = {}

    def _ensure_identity(**_kwargs: object) -> SimpleNamespace:
        captured.update(
            {
                "service_account_id": os.environ.get("NEBIUS_SA_ID"),
                "public_key_id": os.environ.get("NEBIUS_AUTH_PUBLIC_KEY_ID"),
                "private_key_file": os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"),
                "iam_token": os.environ.get("NEBIUS_IAM_TOKEN"),
            }
        )
        return SimpleNamespace(service_account_id="serviceaccount-123")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", _ensure_identity)
    monkeypatch.setattr(
        cli,
        "generate_service_account_auth_key_pair",
        lambda: ("FRESH-PRIVATE-KEY", "FRESH-PUBLIC-KEY"),
    )
    monkeypatch.setattr(
        cli,
        "service_account_auth_public_key_ids_by_description",
        lambda **_kwargs: ("publickey-fresh",),
    )

    material, created = cli._create_or_recreate_runtime_auth_profile(
        project_id="project-123",
        client_name="client-a",
        recreate=True,
        profile=None,
        endpoint=None,
        sdk_config_file=None,
    )

    assert captured == {
        "service_account_id": None,
        "public_key_id": None,
        "private_key_file": None,
        "iam_token": "operator-token",
    }
    assert created is True
    assert material.auth_public_key_id == "publickey-fresh"
    assert material.s3_access_key_id == "s3-access"
    assert material.s3_secret_access_key == "s3-secret"


def test_automatic_runtime_auth_rotation_preserves_cached_s3_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path))
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="serviceaccount-123",
        auth_public_key_id="publickey-stale",
        private_key_pem="STALE-PRIVATE-KEY",
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token")
    monkeypatch.setattr(
        cli,
        "ensure_ci_service_account_identity",
        lambda **_kwargs: SimpleNamespace(service_account_id="serviceaccount-123"),
    )
    monkeypatch.setattr(
        cli,
        "generate_service_account_auth_key_pair",
        lambda: ("FRESH-PRIVATE-KEY", "FRESH-PUBLIC-KEY"),
    )
    monkeypatch.setattr(
        cli,
        "service_account_auth_public_key_ids_by_description",
        lambda **_kwargs: ("publickey-fresh",),
    )
    wait_calls: list[str] = []

    def _wait(material: cli.RuntimeAuthCacheMaterial) -> None:
        wait_calls.append(material.auth_public_key_id)
        if material.auth_public_key_id == "publickey-stale":
            raise RuntimeError("Public Key not exists; JwtKeyNotExists")

    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", _wait)
    monkeypatch.setattr(
        cli,
        "issue_service_account_object_storage_access_key",
        lambda **_kwargs: pytest.fail("authorized-key rotation must not issue another S3 key"),
    )

    cli._ensure_runtime_auth_material(
        SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        ),
        need_terraform=True,
    )

    material = cli._runtime_auth_cache_material(
        project_id="project-123",
        client_name="client-a",
    )
    assert material is not None
    assert wait_calls == ["publickey-stale", "publickey-fresh"]
    assert material.auth_public_key_id == "publickey-fresh"
    assert material.s3_access_key_id == "s3-access"
    assert material.s3_secret_access_key == "s3-secret"
    assert os.environ["AWS_ACCESS_KEY_ID"] == "s3-access"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "s3-secret"
    _clear_runtime_auth_env()


def test_mysterybox_eso_credentials_json_uses_subject_credentials_format() -> None:
    credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )

    rendered = cli._mysterybox_eso_credentials_json(credentials)
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert ": " not in rendered
    assert ", " not in rendered
    assert payload == {
        "subject-credentials": {
            "alg": "RS256",
            "private-key": "MYSTERYBOX-PRIVATE-KEY",
            "kid": "publickey-mysterybox",
            "iss": "serviceaccount-mysterybox",
            "sub": "serviceaccount-mysterybox",
        }
    }


def test_mysterybox_eso_credentials_from_json_requires_subject_credentials() -> None:
    credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )

    assert (
        cli._mysterybox_eso_credentials_from_json(cli._mysterybox_eso_credentials_json(credentials))
        == credentials
    )
    assert (
        cli._mysterybox_eso_credentials_from_json(
            json.dumps(
                {
                    "subject-credentials": {
                        "alg": "RS256",
                        "private-key": "MYSTERYBOX-PRIVATE-KEY",
                        "kid": "publickey-mysterybox",
                        "iss": "serviceaccount-mysterybox",
                        "sub": "other-serviceaccount",
                    }
                }
            )
        )
        is None
    )


def test_create_mysterybox_eso_credentials_uses_dedicated_service_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    wait_calls: list[tuple[str, cli.MysteryBoxEsoCredentials]] = []
    runtime_env = {
        "NEBIUS_SA_ID": "runtime-tf-sa",
        "NEBIUS_AUTH_PUBLIC_KEY_ID": "runtime-tf-key",
        "NEBIUS_AUTH_PRIVATE_KEY_FILE": "/tmp/runtime-tf-key.pem",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM": "RUNTIME-TF-PRIVATE-KEY",
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
    }
    for key, value in runtime_env.items():
        monkeypatch.setenv(key, value)

    def _fake_bootstrap_service_account_auth_key(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        captured["runtime_auth_env"] = {key: os.environ.get(key) for key in runtime_env}
        result = SimpleNamespace(
            project_id="project-123",
            service_account_name="mysterybox-sa",
            service_account_id="serviceaccount-mysterybox",
            service_account_created=True,
            roles_created=["mysterybox.payload-viewer"],
            roles_already_present=[],
            auth_public_key_id="publickey-mysterybox",
            auth_private_key_pem="MYSTERYBOX-PRIVATE-KEY",
        )
        delivery = kwargs["delivery"]
        intent = delivery.prepare(
            result,
            operation_id="a" * 32,
            credentials=(("auth-public-key", "publickey-mysterybox"),),
        )
        delivery.deliver(result, intent)
        return result

    monkeypatch.setattr(
        cli,
        "bootstrap_service_account_auth_key",
        _fake_bootstrap_service_account_auth_key,
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_mysterybox_eso_token_ready",
        lambda *, project_id, credentials: wait_calls.append((project_id, credentials)),
    )

    delivered: list[cli.MysteryBoxEsoCredentials] = []
    credentials = cli._create_mysterybox_eso_credentials(
        project_id="project-123",
        delivery=cli.CallbackCredentialDeliveryAdapter(
            kind="test-secret",
            target="namespace/name:key",
            deliver_callback=lambda value, _intent: (
                wait_calls.append(("project-123", value)),
                delivered.append(value),
            ),
        ),
    )

    assert captured["service_account_name"] == "mysterybox-sa"
    assert captured["role_ids"] == ["mysterybox.payload-viewer"]
    assert captured["allow_cli_token"] is True
    assert captured["runtime_auth_env"] == {
        **{key: None for key in runtime_env if key != cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV},
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
    }
    assert {key: os.environ.get(key) for key in runtime_env} == runtime_env
    assert "access_key_description" not in captured
    assert credentials == cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )
    assert wait_calls == [("project-123", credentials)]
    assert delivered == [credentials]


def test_ensure_mysterybox_eso_service_account_uses_operator_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    runtime_env = {
        "NEBIUS_SA_ID": "runtime-tf-sa",
        "NEBIUS_AUTH_PUBLIC_KEY_ID": "runtime-tf-key",
        "NEBIUS_AUTH_PRIVATE_KEY_FILE": "/tmp/runtime-tf-key.pem",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM": "RUNTIME-TF-PRIVATE-KEY",
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
    }
    for key, value in runtime_env.items():
        monkeypatch.setenv(key, value)

    def _fake_ensure_ci_service_account_identity(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        captured["runtime_auth_env"] = {key: os.environ.get(key) for key in runtime_env}
        return SimpleNamespace(
            service_account_id="serviceaccount-mysterybox",
            service_account_created=False,
            roles_created=[],
            roles_already_present=["mysterybox.payload-viewer"],
        )

    monkeypatch.setattr(
        cli,
        "ensure_ci_service_account_identity",
        _fake_ensure_ci_service_account_identity,
    )

    result = cli._ensure_mysterybox_eso_service_account_identity(project_id="project-123")

    assert result.service_account_id == "serviceaccount-mysterybox"
    assert captured["service_account_name"] == "mysterybox-sa"
    assert captured["role_ids"] == ["mysterybox.payload-viewer"]
    assert captured["allow_cli_token"] is True
    assert captured["runtime_auth_env"] == {
        **{key: None for key in runtime_env if key != cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV},
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
    }
    assert {key: os.environ.get(key) for key in runtime_env} == runtime_env


def test_ensure_mysterybox_eso_credentials_secret_reuses_valid_cluster_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    rendered_messages: list[str] = []
    runtime_env = {
        "NEBIUS_SA_ID": "runtime-tf-sa",
        "NEBIUS_AUTH_PUBLIC_KEY_ID": "runtime-tf-key",
        "NEBIUS_AUTH_PRIVATE_KEY_FILE": "/tmp/runtime-tf-key.pem",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM": "RUNTIME-TF-PRIVATE-KEY",
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
    }
    for key, value in runtime_env.items():
        monkeypatch.setenv(key, value)
    auth_key_checks: list[dict[str, str | None]] = []

    monkeypatch.setattr(
        cli,
        "_kubectl_read_secret_key",
        lambda **_kwargs: cli._mysterybox_eso_credentials_json(credentials),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_mysterybox_eso_service_account_identity",
        lambda **_kwargs: SimpleNamespace(
            service_account_id="serviceaccount-mysterybox",
            roles_created=[],
        ),
    )
    monkeypatch.setattr(
        cli,
        "auth_public_key_exists",
        lambda **_kwargs: (
            auth_key_checks.append({key: os.environ.get(key) for key in runtime_env}) or True
        ),
    )
    monkeypatch.setattr(
        cli,
        "_create_mysterybox_eso_credentials",
        lambda **_kwargs: pytest.fail("valid Kubernetes Secret should be reused"),
    )
    monkeypatch.setattr(
        cli,
        "_apply_mysterybox_eso_credentials_secret",
        lambda **_kwargs: pytest.fail("valid Kubernetes Secret should not be rewritten"),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    fresh = cli._ensure_mysterybox_eso_credentials_secret(
        fake_config,
        spec={
            "namespace": "external-secrets",
            "name": "nebius-mysterybox-shared-creds",
            "key": "credentials.json",
        },
        extra_env=None,
        fresh_credentials=None,
    )

    assert fresh is None
    assert auth_key_checks == [
        {
            **{key: None for key in runtime_env if key != cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV},
            cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
        }
    ]
    assert {key: os.environ.get(key) for key in runtime_env} == runtime_env
    assert rendered_messages == [
        "Reused ESO MysteryBox credential Secret external-secrets/nebius-mysterybox-shared-creds for native provider."
    ]


def test_ensure_mysterybox_eso_credentials_secret_rotates_stale_cluster_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-old",
        auth_public_key_id="publickey-old",
        private_key_pem="OLD-KEY",
    )
    fresh_credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-fresh",
        private_key_pem="FRESH-KEY",
    )
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    applied: list[dict[str, Any]] = []
    rendered_messages: list[str] = []
    monkeypatch.setattr(cli, "_wait_for_mysterybox_eso_token_ready", lambda **_kwargs: None)

    monkeypatch.setattr(
        cli,
        "_kubectl_read_secret_key",
        lambda **_kwargs: cli._mysterybox_eso_credentials_json(stale_credentials),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_mysterybox_eso_service_account_identity",
        lambda **_kwargs: SimpleNamespace(
            service_account_id="serviceaccount-mysterybox",
            roles_created=[],
        ),
    )
    monkeypatch.setattr(cli, "auth_public_key_exists", lambda **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "_create_mysterybox_eso_credentials",
        lambda *, project_id, delivery: _deliver_test_credentials(delivery, fresh_credentials),
    )
    monkeypatch.setattr(
        cli,
        "_apply_mysterybox_eso_credentials_secret",
        lambda **kwargs: applied.append(
            {key: value for key, value in kwargs.items() if key != "delivery_marker_sha256"}
        ),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    returned = cli._ensure_mysterybox_eso_credentials_secret(
        fake_config,
        spec={
            "namespace": "external-secrets",
            "name": "nebius-mysterybox-shared-creds",
            "key": "credentials.json",
        },
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        fresh_credentials=None,
    )

    assert returned == fresh_credentials
    assert applied == [
        {
            "namespace": "external-secrets",
            "name": "nebius-mysterybox-shared-creds",
            "key": "credentials.json",
            "credentials": fresh_credentials,
            "extra_env": {"KUBECONFIG": "/tmp/kubeconfig"},
        }
    ]
    assert any("is stale; replacing it because" in message for message in rendered_messages)
    assert not any("is invalid; replacing it" in message for message in rendered_messages)


def test_ensure_mysterybox_eso_credentials_secret_converges_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )
    applied: list[dict[str, object]] = []
    delivery_state: dict[str, object] = {}
    monkeypatch.setattr(cli, "_wait_for_mysterybox_eso_token_ready", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_kubectl_read_secret_key", lambda **_kwargs: None)

    def _create_credentials(*, project_id: str, delivery: Any) -> Any:
        assert project_id == "project-123"
        intent = delivery.prepare(
            credentials,
            operation_id="e" * 32,
            credentials=(("auth-public-key", "publickey-mysterybox"),),
        )
        delivery.deliver(credentials, intent)
        delivery_state.update(adapter=delivery, intent=intent)
        return credentials

    monkeypatch.setattr(
        cli,
        "_create_mysterybox_eso_credentials",
        _create_credentials,
    )
    monkeypatch.setattr(
        cli,
        "_apply_mysterybox_eso_credentials_secret",
        lambda **kwargs: applied.append(
            {key: value for key, value in kwargs.items() if key != "delivery_marker_sha256"}
        ),
    )

    returned = cli._ensure_mysterybox_eso_credentials_secret(
        fake_config,
        spec={
            "namespace": "external-secrets",
            "name": "nebius-mysterybox-shared-creds",
            "key": "credentials.json",
        },
        extra_env=None,
        fresh_credentials=None,
    )

    assert returned == credentials
    assert applied[0]["credentials"] == credentials
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/kubectl")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error from server (NotFound)",
        ),
    )
    adapter = delivery_state["adapter"]
    intent = delivery_state["intent"]
    assert adapter.probe(intent) is cli.CredentialDeliveryDisposition.AMBIGUOUS


def test_ensure_mysterybox_eso_runtime_creates_credentials_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    applied: list[tuple[object, dict[str, str] | None]] = []
    tls_checks: list[dict[str, Any]] = []
    credential_creates: list[str] = []
    monkeypatch.setattr(cli, "_wait_for_mysterybox_eso_token_ready", lambda **_kwargs: None)

    monkeypatch.setattr(cli, "mysterybox_eso_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "mysterybox_eso_api_domains",
        lambda *_args, **_kwargs: ("api.eu-north1.nebius.cloud:443",),
    )
    monkeypatch.setattr(
        cli,
        "mysterybox_eso_runtime_secret_specs",
        lambda *_args, **_kwargs: (
            {
                "namespace": "external-secrets",
                "name": "nebius-mysterybox-shared-creds",
                "key": "credentials.json",
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "_kubectl_read_secret_key",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "_create_mysterybox_eso_credentials",
        lambda *, project_id, delivery: (
            credential_creates.append(project_id),
            _deliver_test_credentials(delivery, credentials),
            credentials,
        )[2],
    )
    monkeypatch.setattr(
        cli,
        "_kubectl_apply_manifest",
        lambda manifest, *, extra_env: applied.append((manifest, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_kubectl_validate_mysterybox_eso_tls",
        lambda **kwargs: tls_checks.append(dict(kwargs)),
    )
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    cli._ensure_mysterybox_eso_runtime_before_flux(
        fake_config,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )

    assert len(applied) == 1
    assert credential_creates == ["project-123"]
    assert tls_checks == [
        {
            "namespace": "external-secrets",
            "api_domain": "api.eu-north1.nebius.cloud:443",
            "extra_env": {"KUBECONFIG": "/tmp/kubeconfig"},
        }
    ]
    manifest, extra_env = applied[0]
    assert extra_env == {"KUBECONFIG": "/tmp/kubeconfig"}
    assert isinstance(manifest, list)
    namespace_doc, secret_doc = manifest
    assert namespace_doc == {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "external-secrets"},
    }
    assert secret_doc["apiVersion"] == "v1"
    assert secret_doc["kind"] == "Secret"
    assert secret_doc["type"] == "Opaque"
    assert secret_doc["metadata"]["name"] == "nebius-mysterybox-shared-creds"
    assert secret_doc["metadata"]["namespace"] == "external-secrets"
    assert secret_doc["metadata"]["annotations"]["nebius.ai/cxcli-credential-delivery"].startswith(
        "sha256:"
    )
    credentials = json.loads(secret_doc["stringData"]["credentials.json"])
    assert credentials == {
        "subject-credentials": {
            "alg": "RS256",
            "private-key": "MYSTERYBOX-PRIVATE-KEY",
            "kid": "publickey-mysterybox",
            "iss": "serviceaccount-mysterybox",
            "sub": "serviceaccount-mysterybox",
        }
    }


def test_ensure_mysterybox_eso_runtime_prints_confirmation_per_applied_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = cli.MysteryBoxEsoCredentials(
        service_account_id="serviceaccount-mysterybox",
        auth_public_key_id="publickey-mysterybox",
        private_key_pem="MYSTERYBOX-PRIVATE-KEY",
    )
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    applied: list[tuple[object, dict[str, str] | None]] = []
    rendered_messages: list[str] = []
    credential_creates: list[str] = []
    monkeypatch.setattr(cli, "_wait_for_mysterybox_eso_token_ready", lambda **_kwargs: None)

    monkeypatch.setattr(cli, "mysterybox_eso_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "mysterybox_eso_api_domains",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        cli,
        "mysterybox_eso_runtime_secret_specs",
        lambda *_args, **_kwargs: (
            {
                "namespace": "external-secrets",
                "name": "creds-a",
                "key": "credentials.json",
            },
            {
                "namespace": "external-secrets-other",
                "name": "creds-b",
                "key": "credentials.json",
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "_kubectl_read_secret_key",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "_create_mysterybox_eso_credentials",
        lambda *, project_id, delivery: (
            credential_creates.append(project_id),
            _deliver_test_credentials(delivery, credentials),
            credentials,
        )[2],
    )
    monkeypatch.setattr(
        cli,
        "_kubectl_apply_manifest",
        lambda manifest, *, extra_env: applied.append((manifest, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_kubectl_validate_mysterybox_eso_tls",
        lambda **_kwargs: pytest.fail("no api domains were configured"),
    )
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(a) for a in args)),
    )

    cli._ensure_mysterybox_eso_runtime_before_flux(
        fake_config,
        extra_env=None,
    )

    assert len(applied) == 2
    assert credential_creates == ["project-123"]
    confirmations = [
        m for m in rendered_messages if "Ensured ESO MysteryBox credential Secret" in m
    ]
    assert confirmations == [
        "Ensured ESO MysteryBox credential Secret external-secrets/creds-a for native provider.",
        "Ensured ESO MysteryBox credential Secret external-secrets-other/creds-b for native provider.",
    ]


def test_kubectl_validate_mysterybox_eso_tls_uses_in_cluster_curl_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    rendered_messages: list[str] = []

    def _fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '*  subjectAltName: host "api.eu-north1.nebius.cloud" matched cert\'s '
                '"api.eu-north1.nebius.cloud"\n'
                "*  issuer: C=US; O=Let's Encrypt; CN=R13\n"
                "*  SSL certificate verify ok.\n"
                "< HTTP/2 404\n"
            ),
            stderr='pod "nebius-tls-check" deleted from external-secrets namespace\n',
        )

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    cli._kubectl_validate_mysterybox_eso_tls(
        namespace="external-secrets",
        api_domain="https://api.eu-north1.nebius.cloud:443/",
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )

    args = captured["args"]
    assert isinstance(args, list)
    assert args[:4] == ["kubectl", "-n", "external-secrets", "run"]
    assert "--rm" in args
    assert "-i" in args
    assert f"--image={cli._MYSTERYBOX_ESO_TLS_CHECK_IMAGE}" in args
    assert args[-1] == "api.eu-north1.nebius.cloud:443"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] == 120
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["KUBECONFIG"] == "/tmp/kubeconfig"
    assert any(
        "Validated ESO MysteryBox Nebius API DNS/egress/TLS" in message
        for message in rendered_messages
    )
    assert not any("HTTP/2 404" in message for message in rendered_messages)


def test_kubectl_validate_mysterybox_eso_tls_fails_fast_on_tls_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="curl: (60) SSL certificate problem: unable to get local issuer certificate\n",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="ESO MysteryBox Nebius API TLS validation failed"):
        cli._kubectl_validate_mysterybox_eso_tls(
            namespace="external-secrets",
            api_domain="api.nebius.cloud:443",
            extra_env=None,
        )


def test_run_mysterybox_eso_connectivity_validation_writes_deploy_report_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _ready_payload(reason: str = "Ready") -> str:
        return json.dumps(
            {"status": {"conditions": [{"type": "Ready", "status": "True", "reason": reason}]}}
        )

    def _fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if args[:4] == ["kubectl", "-n", "external-secrets", "run"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '*  subjectAltName: host "api.nebius.cloud" matched cert\'s '
                    '"api.nebius.cloud"\n'
                    "*  issuer: C=US; O=Let's Encrypt; CN=R13\n"
                    "*  SSL certificate verify ok.\n"
                    "< HTTP/2 404\n"
                ),
                stderr="",
            )
        if args == [
            "kubectl",
            "get",
            "clustersecretstore",
            "nebius-mysterybox-shared",
            "-o",
            "json",
        ]:
            return SimpleNamespace(returncode=0, stdout=_ready_payload("Valid"), stderr="")
        if args == ["kubectl", "-n", "app", "get", "externalsecret", "app-config", "-o", "json"]:
            return SimpleNamespace(returncode=0, stdout=_ready_payload("SecretSynced"), stderr="")
        if (
            args[:5] == ["kubectl", "-n", "external-secrets", "logs", "deploy/external-secrets"]
            and len(args) == 6
            and args[5].startswith("--since-time=")
        ):
            return SimpleNamespace(
                returncode=0, stdout="provider nebiusmysterybox sync ok\n", stderr=""
            )
        raise AssertionError(f"unexpected subprocess call: {args}")

    spec = {
        "kind": cli.MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
        "name": "ESO MysteryBox connectivity (mk8s)",
        "target_ref": "mk8s",
        "store_name": "nebius-mysterybox-shared",
        "api_domain": "api.nebius.cloud:443",
        "credentials_secret": {
            "name": "nebius-mysterybox-shared-creds",
            "namespace": "external-secrets",
            "key": "credentials.json",
        },
        "eso_namespace": "external-secrets",
        "external_secrets": [{"namespace": "app", "name": "app-config"}],
        "report_file": "mysterybox-eso-connectivity-report-mk8s.json",
        "required": True,
    }

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    written = cli.run_mysterybox_eso_validations(
        [spec],
        reports_dir=tmp_path,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
    )

    report_path = tmp_path / "mysterybox-eso-connectivity-report-mk8s.json"
    assert written == [report_path]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert [item["name"] for item in report["checks"]] == [
        "Nebius API TLS",
        "ClusterSecretStore Ready",
        "ExternalSecret Ready",
        "ESO controller log scan",
    ]
    assert all(set(item) == {"name", "passed"} for item in report["checks"])
    assert "synced_resource_count" not in report
    assert "credentials_secret" not in report
    assert "external_secrets" not in report
    report_text = report_path.read_text(encoding="utf-8")
    assert "credentials.json" not in report_text
    assert "nebius-mysterybox-shared-creds" not in report_text
    assert "nebius-mysterybox-shared" not in report_text
    assert "external-secrets" not in report_text
    assert "app-config" not in report_text
    assert "api.nebius.cloud" not in report_text
    assert "provider nebiusmysterybox sync ok" not in report_text
    assert calls[0][-1] == "api.nebius.cloud:443"


def test_ensure_runtime_auth_material_recreates_stale_cached_public_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client-a",
            nebius=SimpleNamespace(project_id="project-123"),
        )
    )
    stale_key = tmp_path / "stale.pem"
    fresh_key = tmp_path / "fresh.pem"
    stale_key.write_text("STALE", encoding="utf-8")
    fresh_key.write_text("FRESH", encoding="utf-8")
    stale = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-stale",
        auth_public_key_id="key-stale",
        private_key_file=stale_key,
        private_key_pem="STALE",
        s3_access_key_id="s3",
        s3_secret_access_key="secret",
    )
    fresh = replace(
        stale,
        service_account_id="sa-fresh",
        auth_public_key_id="key-fresh",
        private_key_file=fresh_key,
        private_key_pem="FRESH",
    )
    recreate_calls: list[bool] = []
    wait_calls: list[str] = []

    def _profile(**kwargs: object) -> tuple[cli.RuntimeAuthCacheMaterial, bool]:
        recreate = bool(kwargs["recreate"])
        recreate_calls.append(recreate)
        return (fresh if recreate else stale), recreate

    def _wait(material: cli.RuntimeAuthCacheMaterial) -> None:
        wait_calls.append(material.auth_public_key_id)
        if material is stale:
            raise RuntimeError("Public Key not exists; JwtKeyNotExists")

    monkeypatch.setattr(cli, "_create_or_recreate_runtime_auth_profile", _profile)
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", _wait)

    cli._ensure_runtime_auth_material(fake_config, need_terraform=False)

    assert recreate_calls == [False, True]
    assert wait_calls == ["key-stale", "key-fresh"]
    assert os.environ["NEBIUS_SA_ID"] == "sa-fresh"
    assert os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] == "key-fresh"
    _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_does_not_duplicate_new_key_on_propagation_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    key_file = tmp_path / "new.pem"
    key_file.write_text("NEW", encoding="utf-8")
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-new",
        auth_public_key_id="key-new",
        private_key_file=key_file,
        private_key_pem="NEW",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    recreate_calls: list[bool] = []

    def _profile(**kwargs: object) -> tuple[cli.RuntimeAuthCacheMaterial, bool]:
        recreate_calls.append(bool(kwargs["recreate"]))
        return material, True

    monkeypatch.setattr(cli, "_create_or_recreate_runtime_auth_profile", _profile)
    monkeypatch.setattr(
        cli,
        "_wait_for_runtime_auth_token_ready",
        lambda _material: (_ for _ in ()).throw(
            RuntimeError("Public Key not exists; JwtKeyNotExists after propagation timeout")
        ),
    )

    with pytest.raises(RuntimeError, match="propagation timeout"):
        cli._ensure_runtime_auth_material(
            SimpleNamespace(
                client_info=SimpleNamespace(
                    client_name="client-a",
                    nebius=SimpleNamespace(project_id="project-123"),
                )
            ),
            need_terraform=False,
        )

    assert recreate_calls == [False]
    _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_replaces_inherited_snapshot_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    key_file = tmp_path / "canonical.pem"
    key_file.write_text("CANONICAL", encoding="utf-8")
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="canonical-sa",
        auth_public_key_id="canonical-key",
        private_key_file=key_file,
        private_key_pem="CANONICAL",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "snapshot-token")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(tmp_path / "human.json"))
    monkeypatch.setenv("NEBIUS_SA_ID", "inherited-sa")
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **_kwargs: (material, False),
    )
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)

    cli._ensure_runtime_auth_material(
        SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        ),
        need_terraform=False,
    )

    assert os.environ["NEBIUS_SA_ID"] == "canonical-sa"
    assert "NEBIUS_IAM_TOKEN" not in os.environ
    assert "NEBIUS_AUTH_CREDENTIALS_FILE" not in os.environ
    _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_does_not_recreate_on_cloud_verification_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    key_file = tmp_path / "key.pem"
    key_file.write_text("KEY", encoding="utf-8")
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa",
        auth_public_key_id="key",
        private_key_file=key_file,
        private_key_pem="KEY",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **kwargs: calls.append(bool(kwargs["recreate"])) or (material, False),
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_runtime_auth_token_ready",
        lambda _material: (_ for _ in ()).throw(RuntimeError("temporary sdk failure")),
    )

    with pytest.raises(RuntimeError, match="temporary sdk failure"):
        cli._ensure_runtime_auth_material(
            SimpleNamespace(
                client_info=SimpleNamespace(
                    client_name="client-a",
                    nebius=SimpleNamespace(project_id="project-123"),
                )
            ),
            need_terraform=False,
        )
    assert calls == [False]
    _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_issues_s3_only_for_terraform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    key_file = tmp_path / "key.pem"
    key_file.write_text("KEY", encoding="utf-8")
    api_only = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa",
        auth_public_key_id="key",
        private_key_file=key_file,
        private_key_pem="KEY",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    with_s3 = replace(
        api_only,
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )
    s3_calls: list[cli.RuntimeAuthCacheMaterial] = []
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **_kwargs: (api_only, False),
    )
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_s3_material",
        lambda material: s3_calls.append(material) or with_s3,
    )

    cli._ensure_runtime_auth_material(
        SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        ),
        need_terraform=True,
    )

    assert s3_calls == [api_only]
    assert os.environ["AWS_ACCESS_KEY_ID"] == "s3-access"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "s3-secret"
    _clear_runtime_auth_env()


def test_ensure_runtime_auth_material_preserves_operator_auth_until_s3_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", _REAL_ENSURE_RUNTIME_AUTH_MATERIAL)
    _clear_runtime_auth_env()
    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    key_file = tmp_path / "key.pem"
    key_file.write_text("KEY", encoding="utf-8")
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa",
        auth_public_key_id="key",
        private_key_file=key_file,
        private_key_pem="KEY",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    with_s3 = replace(
        material,
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )
    observed_operator_tokens: list[str | None] = []
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token")
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **_kwargs: (material, False),
    )
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_s3_material",
        lambda _material: (
            observed_operator_tokens.append(os.environ.get("NEBIUS_IAM_TOKEN")) or with_s3
        ),
    )

    cli._ensure_runtime_auth_material(
        SimpleNamespace(
            client_info=SimpleNamespace(
                client_name="client-a",
                nebius=SimpleNamespace(project_id="project-123"),
            )
        ),
        need_terraform=True,
    )

    assert observed_operator_tokens == ["operator-token"]
    assert "NEBIUS_IAM_TOKEN" not in os.environ
    assert os.environ["NEBIUS_SA_ID"] == "sa"
    _clear_runtime_auth_env()


def test_runtime_auth_s3_issuance_uses_captured_operator_not_canonical_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_runtime_auth_env()
    monkeypatch.setenv(cli._RUNTIME_AUTH_CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token")
    cli._capture_runtime_auth_operator_environment()
    cli._runtime_auth_cache_write(
        project_id="project-123",
        client_name="client-a",
        service_account_id="runtime-sa",
        auth_public_key_id="runtime-key",
        private_key_pem="RUNTIME-PRIVATE-KEY",
    )
    material = cli._runtime_auth_cache_material(
        project_id="project-123",
        client_name="client-a",
    )
    assert material is not None
    cli._export_runtime_auth_material(material)
    observed: list[dict[str, object]] = []
    delivery_state: dict[str, object] = {}

    def _issue(**kwargs: object) -> SimpleNamespace:
        observed.append(
            {
                "iam_token": os.environ.get("NEBIUS_IAM_TOKEN"),
                "service_account_env": os.environ.get("NEBIUS_SA_ID"),
                "allow_cli_token": kwargs["allow_cli_token"],
            }
        )
        result = SimpleNamespace(
            s3_access_key_id="s3-access",
            s3_secret_access_key="s3-secret",
        )
        delivery = kwargs["delivery"]
        intent = delivery.prepare(
            result,
            operation_id="b" * 32,
            credentials=(("access-key", "accesskey-resource"),),
        )
        delivery.deliver(result, intent)
        delivery_state.update(adapter=delivery, intent=intent)
        return result

    monkeypatch.setattr(cli, "issue_service_account_object_storage_access_key", _issue)

    updated = cli._ensure_runtime_auth_s3_material(material)

    assert observed == [
        {
            "iam_token": "operator-token",
            "service_account_env": None,
            "allow_cli_token": True,
        }
    ]
    assert updated.s3_access_key_id == "s3-access"
    assert updated.s3_secret_access_key == "s3-secret"
    assert "NEBIUS_IAM_TOKEN" not in os.environ
    assert os.environ["NEBIUS_SA_ID"] == "runtime-sa"
    cache_path = (
        cli._runtime_auth_cache_dir(project_id="project-123", client_name="client-a")
        / cli._RUNTIME_AUTH_CACHE_FILE
    )
    cache_path.unlink()
    adapter = delivery_state["adapter"]
    intent = delivery_state["intent"]
    assert adapter.probe(intent) is cli.CredentialDeliveryDisposition.AMBIGUOUS
    _clear_runtime_auth_env()


def test_runtime_auth_token_sdk_sets_canonical_user_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebius import sdk as nebius_sdk

    private_key_file = tmp_path / "auth-private.pem"
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="publickey-123",
        private_key_file=private_key_file,
        private_key_pem="PRIVATE-KEY",
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    captured: dict[str, object] = {}

    class _FakeSDK:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(nebius_sdk, "SDK", _FakeSDK)
    monkeypatch.setenv("NEBIUS_ENDPOINT", "api.example.invalid")

    sdk = cli._runtime_auth_token_sdk(material)

    assert isinstance(sdk, _FakeSDK)
    assert captured == {
        "service_account_id": "sa-123",
        "service_account_public_key_id": "publickey-123",
        "service_account_private_key_file_name": private_key_file,
        "parent_id": "project-123",
        "user_agent_prefix": cli.NEBIUS_SDK_USER_AGENT_PREFIX,
        "domain": "api.example.invalid",
    }


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
    synced_material = replace(
        material,
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **_kwargs: (material, False),
    )
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _material: None)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _material: None)
    monkeypatch.setattr(cli, "_ensure_runtime_auth_s3_material", lambda _material: synced_material)
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


def test_runtime_auth_ci_sync_includes_exact_project_binding_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = cli.RuntimeAuthCacheMaterial(
        project_id="project-123",
        client_name="client-a",
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        private_key_file=tmp_path / "auth-private.pem",
        private_key_pem="PRIVATE-KEY",
        s3_access_key_id="s3-access",
        s3_secret_access_key="s3-secret",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "read_github_token", lambda **_kwargs: "github-token")
    monkeypatch.setattr(cli, "_resolve_github_repo_slug", lambda **_kwargs: "owner/repo")
    monkeypatch.setattr(cli, "ensure_github_environment", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_sync_github_ci_secrets",
        lambda **kwargs: captured.update(kwargs) or sorted(kwargs["ci_secrets"]),
    )

    repo_slug, github_environment, updated = cli._sync_runtime_auth_profile_to_ci_environment(
        material=material,
        client_name="client-a",
        github_repo="owner/repo",
        github_token_env="GH_TOKEN",
        repo_root_hint=tmp_path,
    )

    assert repo_slug == "owner/repo"
    assert github_environment == "client-a-project-123"
    assert cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV in updated
    assert captured["ci_secrets"] == {
        cli._RUNTIME_AUTH_ACTIVE_PROJECT_ENV: "project-123",
        "NEBIUS_SA_ID": "sa-123",
        "NEBIUS_AUTH_PUBLIC_KEY_ID": "auth-key-123",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM": "PRIVATE-KEY",
        "NEBIUS_S3_ACCESS_KEY_ID": "s3-access",
        "NEBIUS_S3_SECRET_ACCESS_KEY": "s3-secret",
    }


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


def test_managed_node_template_batch_resolves_config_key_alias() -> None:
    live_group = SimpleNamespace(
        id="group-id",
        name="training-gpu",
        source=SimpleNamespace(key="gpu"),
    )

    selected = cli._managed_node_template_batch_selection(  # noqa: SLF001
        (live_group,),
        ("gpu",),
    )

    assert selected == (live_group,)


def test_managed_node_template_batch_rejects_ambiguous_alias() -> None:
    groups = (
        SimpleNamespace(
            id="group-a",
            name="training-gpu",
            source=SimpleNamespace(key="gpu"),
        ),
        SimpleNamespace(
            id="group-b",
            name="gpu",
            source=SimpleNamespace(key="other"),
        ),
    )

    with pytest.raises(RuntimeError, match="alias is ambiguous: gpu"):
        cli._managed_node_template_batch_selection(groups, ("gpu",))  # noqa: SLF001
