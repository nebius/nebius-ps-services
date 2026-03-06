from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import nebius_cxcli.cli as cli

runner = CliRunner()


def _fake_paths(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        deployments_dir=tmp_path / "deployments",
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=tmp_path / "generated" / "flux",
    )


def test_validate_command_non_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(cli, "_validate_component_dependencies", lambda _cfg: [])

    result = runner.invoke(cli.app, ["validate", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert "Valid:" in result.output


def test_validate_command_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    strict_called: dict[str, bool] = {"called": False}

    monkeypatch.setattr(cli, "_load_context", lambda _path: (object(), object()))
    monkeypatch.setattr(cli, "_validate_component_dependencies", lambda _cfg: [])

    def _fake_strict(_cfg: object) -> None:
        strict_called["called"] = True

    monkeypatch.setattr(cli, "_validate_strict_config", _fake_strict)

    result = runner.invoke(cli.app, ["validate", "--strict", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert "Valid (strict):" in result.output
    assert strict_called["called"] is True


def test_render_command_invokes_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    calls: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

    def _fake_render_instance(config: object, paths: object) -> SimpleNamespace:
        calls["config"] = config
        calls["paths"] = paths
        return SimpleNamespace(files_written=[tmp_path / "a.tf", tmp_path / "b.yaml"])

    monkeypatch.setattr(cli, "render_instance", _fake_render_instance)
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

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert "Rendered 2 file(s)" in result.output
    assert calls == {
        "config": "cfg",
        "paths": fake_paths,
        "lock_config": "cfg",
        "lock_paths": fake_paths,
    }


def test_deploy_command_passes_auto_auth_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

    def _fake_render_and_local_deploy(config: object, paths: object, *, auto_auth_bootstrap: bool) -> int:
        captured["config"] = config
        captured["paths"] = paths
        captured["auto_auth_bootstrap"] = auto_auth_bootstrap
        return 4

    monkeypatch.setattr(cli, "_render_and_local_deploy", _fake_render_and_local_deploy)

    result = runner.invoke(
        cli.app,
        ["deploy", str(tmp_path / "config.yaml"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert "Rendered 4 file(s)" in result.output
    assert "Local deploy completed." in result.output
    assert captured == {
        "config": "cfg",
        "paths": fake_paths,
        "auto_auth_bootstrap": True,
    }


def test_terraform_plan_command_invokes_runtime_auth_and_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

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

    def _fake_terraform_plan(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
        captured["plan"] = {"infra_dir": infra_dir, "extra_env": extra_env}

    monkeypatch.setattr(cli, "terraform_plan", _fake_terraform_plan)

    result = runner.invoke(
        cli.app,
        ["terraform", "plan", str(tmp_path / "config.yaml"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == {
        "config": "cfg",
        "auto_auth_bootstrap": True,
    }
    assert captured["plan"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
    }


def test_terraform_apply_command_invokes_runtime_auth_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

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

    def _fake_terraform_apply(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
        captured["apply"] = {"infra_dir": infra_dir, "extra_env": extra_env}

    monkeypatch.setattr(cli, "terraform_apply", _fake_terraform_apply)

    result = runner.invoke(
        cli.app,
        ["terraform", "apply", str(tmp_path / "config.yaml"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["backend"] == {
        "config": "cfg",
        "auto_auth_bootstrap": True,
    }
    assert captured["apply"] == {
        "infra_dir": fake_paths.infra_dir,
        "extra_env": {"TF_VAR_DEMO": "1"},
    }


def test_flux_bootstrap_command_invokes_flux_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

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
    monkeypatch.setattr(cli, "ensure_flux", lambda _paths: "reconciled")

    result = runner.invoke(
        cli.app,
        ["flux", "bootstrap", str(tmp_path / "config.yaml"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert "Flux reconciled" in result.output
    assert captured["auth"] == {
        "config": "cfg",
        "need_terraform": False,
        "need_eso_mysterybox": False,
        "auto_bootstrap": True,
    }


def test_inventory_commands_invoke_inventory_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))
    monkeypatch.setattr(
        cli,
        "write_inventory",
        lambda _cfg, _paths: SimpleNamespace(markdown=tmp_path / "generated" / "inventory" / "inventory.md"),
    )

    write_result = runner.invoke(cli.app, ["inventory", "write", str(tmp_path / "config.yaml")])

    assert write_result.exit_code == 0, write_result.output
    assert "Inventory written:" in write_result.output


def test_email_command_handles_sent_and_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = _fake_paths(tmp_path)
    monkeypatch.setattr(cli, "_load_context", lambda _path: ("cfg", fake_paths))

    monkeypatch.setattr(cli, "send_inventory_email", lambda _cfg, _paths: True)
    sent_result = runner.invoke(cli.app, ["email", str(tmp_path / "config.yaml")])
    assert sent_result.exit_code == 0, sent_result.output
    assert "Inventory email sent" in sent_result.output

    monkeypatch.setattr(cli, "send_inventory_email", lambda _cfg, _paths: False)
    noop_result = runner.invoke(cli.app, ["email", str(tmp_path / "config.yaml")])
    assert noop_result.exit_code == 0, noop_result.output
    assert "client_info.notifications.email not configured; nothing sent" in noop_result.output


def test_top_level_help_has_single_auth_command_surface() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "auth              Manage runtime auth profile" in result.output
    assert "auth-runtime-profile" not in result.output


def test_auth_help_has_no_subcommand_layer() -> None:
    result = runner.invoke(cli.app, ["auth", "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage: " in result.output
    assert "auth [OPTIONS]" in result.output
    assert "COMMAND [ARGS]" not in result.output
    assert "--validate-profile" in result.output
    assert "--create" in result.output
    assert "--recreate" in result.output
    assert "--bootstrap-ci" in result.output


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
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_load_context", lambda _path: (fake_config, fake_paths))
    monkeypatch.setattr(
        cli,
        "_ensure_ci_workflow_for_deployments_root",
        lambda *, deployments_root, force: fake_workflow,
    )

    def _fake_auto_bootstrap(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli, "_auto_bootstrap_ci_auth_and_secrets", _fake_auto_bootstrap)

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
    assert "CI bootstrap completed." in result.output
    assert captured["project_id"] == "project-123"
    assert captured["github_environment"] == "client-a-project-123"
    assert captured["github_repo"] == "owner/repo"
    assert captured["github_token_env"] == "MY_GH_TOKEN"


def test_auth_requires_action_flag() -> None:
    result = runner.invoke(cli.app, ["auth", "--project-id", "project-123"])
    assert result.exit_code == 1
    assert "Select at least one action" in result.output


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
    assert "Runtime auth profile already exists" in result.output


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
    assert "Recreated runtime auth profile for project 'project-123'" in result.output


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
    assert "Synced GitHub environment secrets to owner/repo/client-a-project-123" in result.output
    assert "2" in result.output


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
    assert "Project ID: project-123" in result.output
    assert "Profile status: OK" in result.output


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
    assert "Runtime auth profile validation failed for project(s): project-123" in result.output
