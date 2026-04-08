from __future__ import annotations

import json
import os
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli
import nebius_cxcli.component_sources as component_sources
import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.component_sources import (
    ComponentOutput,
    Handoff,
    SourceProfile,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.email_settings import EmailSettings
from nebius_cxcli.managed_tools import FLUX_VERSION_ENV, TERRAFORM_VERSION_ENV
from nebius_cxcli.paths import ProjectPaths

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


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
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()


def _plain_output(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _fake_paths(tmp_path: Path) -> ProjectPaths:
    project_dir = tmp_path / "deployments" / "projects" / "client-a--tenant-123" / "project-456"
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
        path_client_name="client-a",
        path_tenant_id="tenant-123",
        path_project_id="project-456",
    )


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
    (bootstrap_dir / "gotk-sync.yaml").write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")

    warning_with_bootstrap = cli._render_overwrite_warning(fake_paths)

    assert warning_with_bootstrap is not None
    assert "generated/flux/flux-system" not in warning_with_bootstrap


def test_validate_command_non_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    result = runner.invoke(cli.app, ["validate", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Runtime validation:" in output
    assert "Load config and component catalog" in output
    assert "Validate active component sources" in output
    assert "Validate component dependencies" in output
    assert "Validate Terraform module inputs" in output
    assert "Valid:" in output
    assert captured["source_profile"] == SourceProfile.PORTABLE


def test_validate_command_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    strict_called: dict[str, bool] = {"called": False}
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
    monkeypatch.setattr(cli, "validate_mk8s_network_preflight", lambda _cfg: None)
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

    result = runner.invoke(cli.app, ["validate", "--strict", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Validate strict deployment readiness" in output
    assert "Validate MK8s network preflight" in output
    assert "Valid (strict):" in output
    assert strict_called["called"] is True
    assert captured["source_profile"] == SourceProfile.PORTABLE


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

    result = runner.invoke(
        cli.app,
        ["--source-profile", "local", "validate", str(tmp_path / "config.yaml")],
    )

    assert result.exit_code == 0, result.output
    assert captured["source_profile"] == SourceProfile.LOCAL


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
        "runtime_config": {"client_info": {"client_name": "client-a", "nebius": {"project_id": "project-456"}}},
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
    assert json.loads((fake_paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")) == {
        "mk8s_cluster_name": "clust1"
    }


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
        lambda config, paths: (
            calls.update({"outputs_config": config, "outputs_paths": paths})
            or {}
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
        lambda config, paths: (
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
        lambda config, paths: calls.update({"inventory_config": config, "inventory_paths": paths})
        or SimpleNamespace(markdown=paths.inventory_dir / "inventory.md"),
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
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda config, paths: {})
    monkeypatch.setattr(cli, "render_flux", lambda config, paths, *, component_output_values=None: [])
    monkeypatch.setattr(cli, "write_inventory", lambda config, paths: None)
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda config, paths, *, source_profile, **kwargs: paths.generated_dir / "manifest.json",
    )
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda config, paths: False)

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
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda config, *, auto_auth_bootstrap: None)
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "terraform_validate", lambda infra_dir, *, extra_env=None: None)
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
    assert captured["paths"] == fake_paths


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
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda config, *, auto_auth_bootstrap: None)
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(cli, "terraform_validate", lambda infra_dir, *, extra_env=None: None)
    monkeypatch.setattr(cli, "_active_chart_count", lambda _config: 0)

    result = runner.invoke(
        cli.app,
        ["validate-generated", "--portable", str(tmp_path / "generated")],
    )

    assert result.exit_code != 0
    assert "render.module_sources" in _plain_output(result.output)


def test_validate_sources_command_reports_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda *_args, **_kwargs: SimpleNamespace(markdown=fake_paths.inventory_dir / "inventory.md"),
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
        lambda *_args, **_kwargs: SimpleNamespace(markdown=fake_paths.inventory_dir / "inventory.md"),
    )
    monkeypatch.setattr(
        cli,
        "_write_generated_runtime_manifest",
        lambda *_args, **_kwargs: fake_paths.generated_dir / "nebius-cxcli-manifest.json",
    )
    monkeypatch.setattr(cli, "_try_generate_terraform_lock_file", lambda *_args, **_kwargs: False)

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Continue and overwrite the existing generated artifacts?" in _plain_output(result.output)
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


def test_deploy_command_passes_auto_auth_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(
        cli,
        "_ensure_ci_workflow_for_deployments_root",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("deploy must not bootstrap CI workflow")),
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
    ) -> None:
        captured["config"] = config
        captured["paths"] = paths
        captured["manifest"] = loaded_manifest
        captured["auto_auth_bootstrap"] = auto_auth_bootstrap

    monkeypatch.setattr(cli, "_deploy_generated_artifacts", _fake_deploy_generated_artifacts)

    result = runner.invoke(
        cli.app,
        ["deploy", str(tmp_path / "generated"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Local deploy completed from" in output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "manifest": manifest,
        "auto_auth_bootstrap": True,
    }


def test_destroy_command_passes_auto_auth_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)

    def _fake_destroy_generated_artifacts(
        config: object,
        paths: object,
        loaded_manifest: object,
        *,
        auto_auth_bootstrap: bool,
    ) -> None:
        captured["config"] = config
        captured["paths"] = paths
        captured["manifest"] = loaded_manifest
        captured["auto_auth_bootstrap"] = auto_auth_bootstrap

    monkeypatch.setattr(cli, "_destroy_generated_artifacts", _fake_destroy_generated_artifacts)

    result = runner.invoke(
        cli.app,
        ["destroy", str(tmp_path / "generated"), "--auto-auth-bootstrap", "--yes"],
    )

    assert result.exit_code == 0, result.output
    output = _plain_output(result.output)
    assert "Local destroy completed from" in output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "manifest": manifest,
        "auto_auth_bootstrap": True,
    }


def test_deploy_generated_artifacts_validates_before_apply_and_prepares_kube_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {
        "deploy": {
            "handoffs": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "cluster_id_output_name": "mk8s_cluster_id",
                    "component_output_ref": "mk8s.cluster_id",
                    "access": "external",
                }
            ]
        }
    }
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda config, *, auto_auth_bootstrap: calls.append(
            ("backend", config, auto_auth_bootstrap)
        ),
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {"TF_VAR_DEMO": "1"})
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
        "_run_terraform_apply_with_status",
        lambda config, paths, *, initialize=True: calls.append(
            ("apply_with_status", config, paths, initialize)
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda config, paths, *, stack, handoffs=None: (
            calls.append(("kube_env", config, paths, handoffs)) or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_cluster_nodes_ready",
        lambda *, extra_env, emit: calls.append(("wait_nodes", extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: calls.append(("flux", paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None: calls.append(("warn_bootstrap", config, paths, extra_env)),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths: calls.append(("inventory", config, paths))
        or SimpleNamespace(markdown=paths.inventory_dir / "inventory.md"),
    )

    cli._deploy_generated_artifacts("cfg", fake_paths, manifest, auto_auth_bootstrap=True)

    assert calls == [
        ("backend", "cfg", True),
        ("init", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}),
        ("validate", fake_paths.infra_dir, {"TF_VAR_DEMO": "1"}, False),
        ("apply_with_status", "cfg", fake_paths, False),
        ("inventory", "cfg", fake_paths),
        (
            "kube_env",
            "cfg",
            fake_paths,
                [
                    {
                        "component_id": "mk8s",
                        "instance_id": "mk8s",
                        "cluster_id_output_name": "mk8s_cluster_id",
                        "component_output_ref": "mk8s.cluster_id",
                        "access": "external",
                }
            ],
        ),
        ("wait_nodes", {"KUBECONFIG": "/tmp/kubeconfig"}),
        ("flux", fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"}),
        ("warn_bootstrap", "cfg", fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"}),
    ]


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
        "_run_terraform_destroy_with_status",
        lambda current_config, paths, *, initialize=True, status_watchers=None: calls.append(
            ("destroy_tf", current_config, paths, initialize, status_watchers)
        ),
    )

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
    )

    assert calls == [
        ("backend", config, True),
        ("destroy_flux", config, fake_paths, manifest),
        (
            "destroy_tf",
            config,
            fake_paths,
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
        "_run_terraform_destroy_with_status",
        lambda current_config, paths, *, initialize=True, status_watchers=None: captured.setdefault(
            "destroy",
            {
                "config": current_config,
                "paths": paths,
                "initialize": initialize,
                "status_watchers": status_watchers,
            },
        ),
    )
    monkeypatch.setattr(cli.console, "print", lambda message, *args, **kwargs: messages.append(str(message)))

    cli._destroy_generated_artifacts(
        config,
        fake_paths,
        manifest,
        auto_auth_bootstrap=True,
    )

    assert captured["destroy"] == {
        "config": config,
        "paths": fake_paths,
        "initialize": True,
        "status_watchers": None,
    }
    assert any("Rendered app teardown failed before infra destroy" in message for message in messages)
    assert any("cluster unreachable" in message for message in messages)


def test_apply_rendered_flux_installs_flux_controllers_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: list[tuple[object, ...]] = []
    cache_dirs: list[Path | None] = []
    status_start: list[tuple[str, str | None]] = []
    status_updates: list[str] = []
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)

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
    assert any(
        call[0] == "run" and call[1] == ("kubectl", "cluster-info")
        for call in calls
    )
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
    monkeypatch.setattr(cli.console, "print", lambda message, *args, **kwargs: printed.append(str(message)))

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


def test_apply_rendered_flux_private_handoff_reports_network_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)

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
            {"status": {"conditions": [{"type": "Ready", "status": "True", "reason": "Succeeded"}]}},
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
            {"status": {"conditions": [{"type": "Ready", "status": "True", "reason": "InstallSucceeded"}]}},
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
    assert "Treating the local apply as successful" in plain
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
    def _fake_reporting(config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0):
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
    reporter = SimpleNamespace(handle_terraform_event="callback", snapshot=lambda: "Status [10s] TF: failed | API: pending")

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    @contextmanager
    def _fake_reporting(config: object, *, emit, poll_interval_seconds=15.0, repeat_interval_seconds=60.0):
        yield reporter

    monkeypatch.setattr(cli, "deployment_status_reporting", _fake_reporting)

    def _fake_apply(infra_dir: Path, *, extra_env=None, initialize=True, event_callback=None) -> None:
        raise RuntimeError("terraform failed")

    monkeypatch.setattr(cli, "terraform_apply", _fake_apply)

    with pytest.raises(RuntimeError, match="Last known deploy status"):
        cli._run_terraform_apply_with_status("cfg", fake_paths)


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
    ]


def test_terraform_plan_command_invokes_runtime_auth_and_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))

    def _fake_ensure_terraform_backend_ready(
        config: object, *, auto_auth_bootstrap: bool
    ) -> None:
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


def test_terraform_apply_command_invokes_runtime_auth_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))

    def _fake_ensure_terraform_backend_ready(
        config: object, *, auto_auth_bootstrap: bool
    ) -> None:
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
        lambda config, paths: captured.setdefault("inventory", {"config": config, "paths": paths}),
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

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))
    monkeypatch.setattr(cli, "_confirm_generated_destroy", lambda *args, **kwargs: True)

    def _fake_ensure_terraform_backend_ready(
        config: object, *, auto_auth_bootstrap: bool
    ) -> None:
        captured["backend"] = {
            "config": config,
            "auto_auth_bootstrap": auto_auth_bootstrap,
        }

    monkeypatch.setattr(
        cli, "_ensure_terraform_backend_ready", _fake_ensure_terraform_backend_ready
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_destroy_with_status",
        lambda config, paths, *, initialize=True, status_watchers=None: captured.setdefault(
            "destroy",
            {
                "config": config,
                "paths": paths,
                "initialize": initialize,
                "status_watchers": status_watchers,
            },
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
        "initialize": True,
        "status_watchers": None,
    }


def test_terraform_unlock_command_reports_when_no_lock_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    manifest = {"schema": "nebius-cxcli-generated/v1"}

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))
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

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))
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


def test_flux_bootstrap_command_invokes_flux_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    manifest = {"schema": "nebius-cxcli-generated/v1", "deploy": {}}

    monkeypatch.setattr(cli, "_load_generated_context", lambda _path: ("cfg", fake_paths, manifest))

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
        lambda config, paths: captured.setdefault("inventory", {"config": config, "paths": paths}),
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
        lambda *, spec: captured.setdefault("persist", spec) or Path.home() / ".kube" / "config",
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(fake_config, fake_paths, stack=stack)
        assert env is not None
        kubeconfig_path = Path(env["KUBECONFIG"])
        kubeconfig = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))

    assert captured["handoff_spec"] == (fake_config, "cluster-123", "external")
    assert captured["persist"] == spec
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
        lambda *, project_id, client_name: captured.setdefault(
            "cache_load", (project_id, client_name)
        )
        or True,
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
        lambda *, spec: captured.setdefault("persist", spec) or Path.home() / ".kube" / "config",
    )

    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(fake_config, fake_paths, stack=stack)

    assert env is not None
    assert captured["cache_load"] == ("project-456", "client-a")
    assert captured["handoff_spec"] == (fake_config, "cluster-123", "external")
    assert captured["persist"] == spec
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
        lambda *, spec: (_ for _ in ()).throw(AssertionError("should not persist kubeconfig")),
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
        outputs=(
            ComponentOutput(
                name="access",
                kind="input",
                source_path="inputs.mk8s_cluster_public_endpoint",
            ),
        ),
        handoff=Handoff(
            cluster_id="cluster_id",
            access="access",
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
                "clusters": [{"name": "existing-cluster", "cluster": {"server": "https://existing"}}],
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
        lambda *, project_id, client_name: captured.setdefault(
            "cache_load", (project_id, client_name)
        )
        or True,
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
        "deploy": {
            "handoffs": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "cluster_id_output_name": "mk8s_cluster_id",
                    "component_output_ref": "mk8s.cluster_id",
                    "access": "external",
                }
            ]
        },
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli, "_load_generated_context", lambda _path: (fake_config, fake_paths, manifest)
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
        lambda config, paths, *, stack, handoffs=None: (
            captured.update({"handoff": (config, paths, handoffs)})
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_cluster_nodes_ready",
        lambda *, extra_env, emit: captured.update({"wait_nodes": extra_env}),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths: captured.update({"inventory": (config, paths)}),
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
        [
            {
                "component_id": "mk8s",
                "instance_id": "mk8s",
                "cluster_id_output_name": "mk8s_cluster_id",
                "component_output_ref": "mk8s.cluster_id",
                "access": "external",
            }
        ],
    )
    assert captured["flux"] == (fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"})
    assert captured["wait_nodes"] == {"KUBECONFIG": "/tmp/kubeconfig"}


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
        "deploy": {
            "handoffs": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "cluster_id_output_name": "mk8s_cluster_id",
                    "component_output_ref": "mk8s.cluster_id",
                    "access": "external",
                }
            ]
        },
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli, "_load_generated_context", lambda _path: (fake_config, fake_paths, manifest)
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
        lambda config, paths, *, stack, handoffs=None: (
            captured.update({"handoff": (config, paths, handoffs)})
            or {"KUBECONFIG": "/tmp/kubeconfig"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_cluster_nodes_ready",
        lambda *, extra_env, emit: captured.update({"wait_nodes": extra_env}),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, *, extra_env=None: captured.update({"apply_flux": (paths, extra_env)}),
    )
    monkeypatch.setattr(
        cli,
        "_warn_if_flux_gitops_not_bootstrapped",
        lambda config, paths, *, extra_env=None: captured.update(
            {"warn_bootstrap": (config, paths, extra_env)}
        ),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda config, paths: captured.update({"inventory": (config, paths)}),
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
        [
            {
                "component_id": "mk8s",
                "instance_id": "mk8s",
                "cluster_id_output_name": "mk8s_cluster_id",
                "component_output_ref": "mk8s.cluster_id",
                "access": "external",
            }
        ],
    )
    assert captured["apply_flux"] == (fake_paths, {"KUBECONFIG": "/tmp/kubeconfig"})
    assert captured["warn_bootstrap"] == (
        fake_config,
        fake_paths,
        {"KUBECONFIG": "/tmp/kubeconfig"},
    )
    assert captured["wait_nodes"] == {"KUBECONFIG": "/tmp/kubeconfig"}


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
        "deploy": {
            "handoffs": [
                {
                    "component_id": "mk8s",
                    "instance_id": "mk8s",
                    "cluster_id_output_name": "mk8s_cluster_id",
                    "component_output_ref": "mk8s.cluster_id",
                    "access": "external",
                }
            ]
        },
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli, "_load_generated_context", lambda _path: (fake_config, fake_paths, manifest)
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
        lambda config, paths, loaded_manifest: captured.update(
            {"destroy_flux": (config, paths, loaded_manifest)}
        ),
    )

    result = runner.invoke(
        cli.app,
        ["flux", "destroy", str(tmp_path / "generated"), "--auto-auth-bootstrap", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Flux resources deleted from" in _plain_output(result.output)
    assert captured["backend"] == (fake_config, True)
    assert captured["destroy_flux"] == (fake_config, fake_paths, manifest)


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
    render_result = runner.invoke(cli.app, ["render", "--help"])
    deploy_result = runner.invoke(cli.app, ["deploy", "--help"])
    destroy_result = runner.invoke(cli.app, ["destroy", "--help"])
    tf_apply_result = runner.invoke(cli.app, ["terraform", "apply", "--help"])
    tf_destroy_result = runner.invoke(cli.app, ["terraform", "destroy", "--help"])
    flux_apply_result = runner.invoke(cli.app, ["flux", "apply", "--help"])
    flux_destroy_result = runner.invoke(cli.app, ["flux", "destroy", "--help"])
    flux_bootstrap_result = runner.invoke(cli.app, ["flux", "bootstrap", "--help"])

    assert top_result.exit_code == 0, top_result.output
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

    assert "prompting before overwrite unless --force is provided" in render_help
    assert "generated artifact bundle" in deploy_help
    assert "does not run `flux bootstrap`" in deploy_help
    assert "does not create or update github workflows" in deploy_help
    assert "destructive inverse of `deploy`" in destroy_help
    assert "--yes" in destroy_help
    assert "refresh inventory" in deploy_help
    assert "refresh inventory" in tf_apply_help
    assert "terraform destroy" in tf_destroy_help
    assert "--yes" in tf_destroy_help
    assert "refresh inventory" in flux_apply_help
    assert "delete rendered flux resources" in flux_destroy_help
    assert "--yes" in flux_destroy_help
    assert "refresh inventory" in flux_bootstrap_help


def test_help_text_maps_commands_to_target_types() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0, result.output
    plain_output = _plain_output(result.output)
    output = " ".join(plain_output.split())

    assert "Target guide:" in plain_output
    assert "create uses a deployments root directory" in output
    assert "discover uses a deployment-scope directory" in output
    assert "component/validate/render/bootstrap-ci use config.yaml" in output
    assert "validate-generated/deploy/destroy/terraform/flux/inventory/email use generated/" in output
    assert "validate-sources accepts optional component_sources.yaml" in output
    assert "auth has no positional path" in output
    assert "bootstrap-ci Use CONFIG_YAML" in output
    assert "component" in output
    assert "deploy Use GENERATED_PATH" in output
    assert "destroy Use GENERATED_PATH" in output


def test_command_help_usage_labels_positional_target_types() -> None:
    create_result = runner.invoke(cli.app, ["create", "--help"])
    component_result = runner.invoke(cli.app, ["component", "--help"])
    component_add_result = runner.invoke(cli.app, ["component", "add", "--help"])
    component_remove_result = runner.invoke(cli.app, ["component", "remove", "--help"])
    discover_result = runner.invoke(cli.app, ["discover", "--help"])
    validate_result = runner.invoke(cli.app, ["validate", "--help"])
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
    assert "add [OPTIONS] CONFIG_YAML [COMPONENT_ID]..." in component_add_help
    assert "component add prompts" in normalized_component_add_help
    assert "Repeat a" in normalized_component_add_help
    assert "<component-id>@<instance-id>" in normalized_component_add_help
    assert "--validate-sources --no-validate-sources" in " ".join(component_add_help.split())
    assert "remove [OPTIONS] CONFIG_YAML [COMPONENT_ID]..." in component_remove_help
    assert "<component-id>@<instance-id>" in normalized_component_remove_help
    assert "discover [OPTIONS] DEPLOYMENT_SCOPE" in discover_help
    assert "generated/" in discover_help
    assert "narrower directory under it" in discover_help
    assert "validate [OPTIONS] CONFIG_YAML" in validate_help
    assert "deploy [OPTIONS] GENERATED_PATH" in deploy_help
    assert "destroy [OPTIONS] GENERATED_PATH" in destroy_help
    assert "--yes" in " ".join(destroy_help.split())
    assert "destroy [OPTIONS] GENERATED_PATH" in tf_destroy_help
    assert "--yes" in " ".join(tf_destroy_help.split())
    assert "destroy [OPTIONS] GENERATED_PATH" in flux_destroy_help
    assert "--yes" in " ".join(flux_destroy_help.split())
    assert "email [OPTIONS] [GENERATED_PATH]" in email_help
    assert "Omit the path" in normalized_email_help
    assert "only when using --setup." in normalized_email_help


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
    assert "Nebius CLI profile name used by Nebius SDK" not in plain_output
    assert "Nebius SDK/CLI config file" not in plain_output


def test_flux_apply_command_fails_when_no_enabled_charts_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = {"apps": {"charts": []}}

    monkeypatch.setattr(
        cli,
        "_load_generated_context",
        lambda _path: (fake_config, fake_paths, {"schema": "nebius-cxcli-generated/v1", "deploy": {}}),
    )

    result = runner.invoke(cli.app, ["flux", "apply", str(tmp_path / "generated")])

    assert result.exit_code == 1, result.output
    assert "No enabled apps charts are configured for this project." in _plain_output(result.output)


def test_inventory_commands_invoke_inventory_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(
        cli,
        "_load_generated_context",
        lambda _path: ("cfg", fake_paths, {"schema": "nebius-cxcli-generated/v1"}),
    )
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda _cfg, _paths: SimpleNamespace(markdown=tmp_path / "generated" / "inventory" / "inventory.md"),
    )

    write_result = runner.invoke(cli.app, ["inventory", "write", str(tmp_path / "generated")])

    assert write_result.exit_code == 0, write_result.output
    assert "Inventory written:" in _plain_output(write_result.output)


def test_email_command_handles_sent_and_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_load_generated_context",
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
        "send_inventory_email",
        lambda _cfg, _paths, *, smtp_settings=None: (
            captured.update({"smtp_settings": smtp_settings})
            or cli.InventoryEmailResult(sent=True, reason="sent", message="Inventory email sent")
        ),
    )
    sent_result = runner.invoke(cli.app, ["email", str(tmp_path / "generated")])
    assert sent_result.exit_code == 0, sent_result.output
    assert "Inventory email sent" in _plain_output(sent_result.output)
    assert captured["smtp_settings"] == {
        "host": "smtp.example.com",
        "port": 2525,
        "starttls": True,
        "from": "deployments@example.com",
    }

    monkeypatch.setattr(
        cli,
        "send_inventory_email",
        lambda _cfg, _paths, *, smtp_settings=None: cli.InventoryEmailResult(
            sent=False,
            reason="disabled",
            message="Inventory email disabled (`client_info.notifications.email_enabled=false`); nothing sent.",
        ),
    )
    noop_result = runner.invoke(cli.app, ["email", str(tmp_path / "generated")])
    assert noop_result.exit_code == 0, noop_result.output
    assert "Inventory email disabled" in _plain_output(noop_result.output)

    monkeypatch.setattr(
        cli,
        "send_inventory_email",
        lambda _cfg, _paths, *, smtp_settings=None: cli.InventoryEmailResult(
            sent=False,
            reason="smtp_unconfigured",
            message="Inventory email enabled but SMTP is not configured. nothing sent.",
        ),
    )
    warning_result = runner.invoke(cli.app, ["email", str(tmp_path / "generated")])
    assert warning_result.exit_code == 0, warning_result.output
    assert "WARNING:" in _plain_output(warning_result.output)
    assert "SMTP is not configured" in _plain_output(warning_result.output)


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


def test_email_command_requires_generated_path_without_setup() -> None:
    result = runner.invoke(cli.app, ["email"])

    assert result.exit_code == 1, result.output
    assert "generated_path is required unless --setup is used." in _plain_output(result.output)


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
        lambda *, github_repo, github_token_env, repo_root: (github_repo or "owner/repo", "token-123"),
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
    assert "Recreated runtime auth profile for project 'project-123'" in _plain_output(result.output)


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
    monkeypatch.setattr(cli, "_resolve_project_id_for_auth_bootstrap", lambda **_kwargs: "project-123")

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
    monkeypatch.setattr(cli, "_resolve_project_id_for_auth_bootstrap", lambda **_kwargs: "project-123")

    result = runner.invoke(
        cli.app,
        ["auth", "--project-id", "project-123", "--client-name", "client-a", "--validate-profile"],
    )

    assert result.exit_code == 1
    assert "Runtime auth profile validation failed for project(s): project-123" in _plain_output(
        result.output
    )
