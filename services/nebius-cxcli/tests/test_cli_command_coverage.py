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


def _identity_result(*, created: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        service_account_created=created,
        roles_created=[],
        roles_already_present=["roles/editor"],
        service_account_id="sa-123",
    )


def _bootstrap_result(*, created: bool = True, private_key_pem: str = "PRIVATE-KEY") -> SimpleNamespace:
    return SimpleNamespace(
        service_account_created=created,
        roles_created=["roles/editor"],
        roles_already_present=[],
        service_account_id="sa-123",
        auth_public_key_id="auth-key-123",
        auth_private_key_pem=private_key_pem,
        s3_access_key_id="s3-key-123",
        s3_secret_access_key="s3-secret-123",
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

    result = runner.invoke(cli.app, ["render", str(tmp_path / "config.yaml")])

    assert result.exit_code == 0, result.output
    assert "Rendered 2 file(s)" in result.output
    assert calls == {"config": "cfg", "paths": fake_paths}


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
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    def _fake_terraform_plan(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
        captured["plan"] = {"infra_dir": infra_dir, "extra_env": extra_env}

    monkeypatch.setattr(cli, "terraform_plan", _fake_terraform_plan)

    result = runner.invoke(
        cli.app,
        ["terraform", "plan", str(tmp_path / "config.yaml"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["auth"] == {
        "config": "cfg",
        "need_terraform": True,
        "need_eso_mysterybox": False,
        "auto_bootstrap": True,
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
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _cfg: {"TF_VAR_DEMO": "1"})

    def _fake_terraform_apply(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
        captured["apply"] = {"infra_dir": infra_dir, "extra_env": extra_env}

    monkeypatch.setattr(cli, "terraform_apply", _fake_terraform_apply)

    result = runner.invoke(
        cli.app,
        ["terraform", "apply", str(tmp_path / "config.yaml"), "--auto-auth-bootstrap"],
    )

    assert result.exit_code == 0, result.output
    assert captured["auth"] == {
        "config": "cfg",
        "need_terraform": True,
        "need_eso_mysterybox": False,
        "auto_bootstrap": True,
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
    monkeypatch.setattr(cli, "upload_inventory", lambda _cfg, _paths: ["a", "b", "c"])

    write_result = runner.invoke(cli.app, ["inventory", "write", str(tmp_path / "config.yaml")])
    upload_result = runner.invoke(cli.app, ["inventory", "upload", str(tmp_path / "config.yaml")])

    assert write_result.exit_code == 0, write_result.output
    assert "Inventory written:" in write_result.output
    assert upload_result.exit_code == 0, upload_result.output
    assert "Uploaded 3 inventory object(s)" in upload_result.output


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


def test_bootstrap_ci_command_with_auth_passes_github_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_config = SimpleNamespace(
        client_info=SimpleNamespace(
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
    assert captured["github_repo"] == "owner/repo"
    assert captured["github_token_env"] == "MY_GH_TOKEN"


def test_auth_bootstrap_no_github_sync_identity_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", lambda **_kwargs: _identity_result())
    monkeypatch.setattr(
        cli,
        "bootstrap_ci_service_account",
        lambda **_kwargs: pytest.fail("bootstrap_ci_service_account should not be called"),
    )

    result = runner.invoke(
        cli.app,
        ["auth", "bootstrap", "--project-id", "project-123", "--no-github-sync"],
    )

    assert result.exit_code == 0, result.output
    assert "No key rotation performed." in result.output
    assert "GitHub sync is disabled." in result.output


def test_auth_bootstrap_no_github_sync_create_keys_writes_private_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key_path = tmp_path / "ci-auth.pem"

    monkeypatch.setattr(
        cli,
        "bootstrap_ci_service_account",
        lambda **_kwargs: _bootstrap_result(private_key_pem="KEY-DATA"),
    )
    monkeypatch.setattr(
        cli,
        "ensure_ci_service_account_identity",
        lambda **_kwargs: pytest.fail("ensure_ci_service_account_identity should not be called"),
    )

    result = runner.invoke(
        cli.app,
        [
            "auth",
            "bootstrap",
            "--project-id",
            "project-123",
            "--no-github-sync",
            "--create-keys",
            "--private-key-out",
            str(private_key_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Authorized key created." in result.output
    assert "Private key file written." in result.output
    assert private_key_path.read_text(encoding="utf-8") == "KEY-DATA"


def test_auth_bootstrap_github_sync_no_flux_token_flag_skips_flux_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "read_github_token", lambda preferred_env: "gh-token")
    monkeypatch.setattr(
        cli,
        "_resolve_github_repo_slug",
        lambda *, explicit_repo_slug, repo_root: explicit_repo_slug or "owner/repo",
    )

    def _fake_presence(*, repo_slug: str, token: str, names: list[str]) -> dict[str, bool]:
        captured["repo_slug"] = repo_slug
        captured["token"] = token
        captured["names"] = list(names)
        return {name: True for name in names}

    monkeypatch.setattr(cli, "repo_secrets_presence", _fake_presence)
    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", lambda **_kwargs: _identity_result())
    monkeypatch.setattr(
        cli,
        "bootstrap_ci_service_account",
        lambda **_kwargs: pytest.fail("bootstrap_ci_service_account should not be called"),
    )
    monkeypatch.setattr(
        cli,
        "upsert_repo_secrets",
        lambda **_kwargs: pytest.fail("upsert_repo_secrets should not be called"),
    )

    result = runner.invoke(
        cli.app,
        [
            "auth",
            "bootstrap",
            "--project-id",
            "project-123",
            "--github-repo",
            "owner/repo",
            "--github-token-env",
            "MY_GH_TOKEN",
            "--no-github-set-flux-token",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "GitHub Actions secrets already present in owner/repo; no sync changes." in result.output
    assert cli.FLUX_SECRET_KEY not in captured["names"]


def test_auth_bootstrap_github_sync_flux_only_sync_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "read_github_token", lambda preferred_env: "gh-token")
    monkeypatch.setattr(
        cli,
        "_resolve_github_repo_slug",
        lambda *, explicit_repo_slug, repo_root: explicit_repo_slug or "owner/repo",
    )

    def _fake_presence(*, repo_slug: str, token: str, names: list[str]) -> dict[str, bool]:
        return {
            name: (name != cli.FLUX_SECRET_KEY)
            for name in names
        }

    monkeypatch.setattr(cli, "repo_secrets_presence", _fake_presence)
    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", lambda **_kwargs: _identity_result())
    monkeypatch.setattr(
        cli,
        "bootstrap_ci_service_account",
        lambda **_kwargs: pytest.fail("bootstrap_ci_service_account should not be called"),
    )

    def _fake_upsert_repo_secrets(*, repo_slug: str, token: str, secrets: dict[str, str]) -> list[str]:
        captured["repo_slug"] = repo_slug
        captured["token"] = token
        captured["secrets"] = dict(secrets)
        return [cli.FLUX_SECRET_KEY]

    monkeypatch.setattr(cli, "upsert_repo_secrets", _fake_upsert_repo_secrets)

    result = runner.invoke(
        cli.app,
        ["auth", "bootstrap", "--project-id", "project-123", "--github-repo", "owner/repo"],
    )

    assert result.exit_code == 0, result.output
    assert "Synced missing FLUX_GITHUB_TOKEN to owner/repo." in result.output
    assert captured["secrets"] == {cli.FLUX_SECRET_KEY: "gh-token"}


def test_auth_bootstrap_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", lambda **_kwargs: _identity_result())

    result = runner.invoke(
        cli.app,
        ["auth", "bootstrap", "--project-id", "project-123", "--no-github-sync", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == '{"status": "ok"}'
