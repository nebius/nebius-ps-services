from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from nebius_cxcli.deployment_recovery import deployment_preview


def test_preview_checks_bucket_without_creating_it(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_ensure_backend_s3_env_aliases", lambda: None)
    monkeypatch.setattr(cli, "backend_settings_from_config", lambda _: object())
    monkeypatch.setattr(cli, "ensure_state_bucket", lambda *a, **kw: calls.append(kw) or False)
    with deployment_preview(True):
        cli._ensure_terraform_backend_ready(object())
    assert calls == [{"create": False}]


@pytest.mark.parametrize("existing", [False, True])
def test_preview_never_bootstraps_or_rotates_authentication(monkeypatch, existing):
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="example", nebius=SimpleNamespace(project_id="preview-project")
        )
    )
    monkeypatch.setattr(cli, "_RUNTIME_AUTH_READY_PROJECTS", {})
    monkeypatch.setattr(cli, "_capture_runtime_auth_operator_environment", lambda: None)
    material = (
        SimpleNamespace(s3_access_key_id="placeholder", s3_secret_access_key="placeholder")
        if existing
        else None
    )
    monkeypatch.setattr(cli, "_runtime_auth_environment_material", lambda **kw: None)
    monkeypatch.setattr(cli, "_runtime_auth_cache_material", lambda **kw: material)
    monkeypatch.setattr(
        cli,
        "_create_or_recreate_runtime_auth_profile",
        lambda **kw: pytest.fail("preview wrote IAM"),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_s3_material",
        lambda *a: pytest.fail("preview wrote S3 credentials"),
    )
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _: None)
    monkeypatch.setattr(cli, "_runtime_identity_verifier", SimpleNamespace(verify=lambda _: None))
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _: None)
    with deployment_preview(True):
        if existing:
            cli._ensure_runtime_auth_material(config, need_terraform=True)
        else:
            with pytest.raises(RuntimeError, match="requires existing"):
                cli._ensure_runtime_auth_material(config, need_terraform=True)
