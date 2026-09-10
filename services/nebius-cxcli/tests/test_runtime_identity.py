from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, iam_bootstrap


def test_auth_command_validates_key_before_explicit_permission_reconciliation(
    monkeypatch, material
):
    from typer.testing import CliRunner

    events = []

    def create(**kwargs):
        assert kwargs["reconcile_permissions"] is True
        events.append("load")
        return material, False

    def identity(_material, **kwargs):
        assert kwargs["allow_mutation"] is True
        events.append("permissions")

    monkeypatch.setattr(cli, "_create_or_recreate_runtime_auth_profile", create)
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _m: events.append("key"))
    monkeypatch.setattr(cli._runtime_identity_verifier, "verify", identity)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _m: events.append("export"))
    result = CliRunner().invoke(cli.app, ["auth", "--project-id", "project-test"])
    assert result.exit_code == 0, result.output
    assert events == ["load", "key", "permissions", "export"]


@pytest.fixture
def material(monkeypatch):
    monkeypatch.setattr(cli, "_runtime_auth_project_lock", lambda **_kw: nullcontext())
    monkeypatch.setattr(cli, "_canonical_runtime_auth_environment", lambda _m: nullcontext())
    monkeypatch.setattr(cli, "_operator_auth_env_without_runtime_auth", lambda **_kw: nullcontext())
    return SimpleNamespace(
        project_id="project-test", client_name="test", service_account_id="sa-test"
    )


@pytest.mark.parametrize("explicit_auth", [False, True])
def test_healthy_runtime_identity_needs_only_readonly_canonical_auth(
    monkeypatch, material, explicit_auth
):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(service_account_id="sa-test")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    cli._runtime_identity_verifier.verify(material, allow_mutation=explicit_auth)
    assert len(calls) == 1
    assert calls[0]["role_ids"] == ["admin"]
    assert calls[0]["expected_service_account_id"] == "sa-test"
    assert calls[0]["allow_mutation"] is False
    assert calls[0]["prefer_operator_auth"] is False
    assert calls[0]["allow_cli_token"] is False


def test_deployment_does_not_reconcile_role_or_expose_provider_error(monkeypatch, material):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("private provider payload sentinel")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    with pytest.raises(RuntimeError, match="auth --project-id") as error:
        cli._runtime_identity_verifier.verify(material)
    assert "sentinel" not in str(error.value)
    assert len(calls) == 1
    assert not calls[0]["allow_mutation"]


def test_explicit_auth_reconciles_then_revalidates_exact_project_identity(monkeypatch, material):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("missing project admin")
        return SimpleNamespace(service_account_id="sa-test")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    cli._runtime_identity_verifier.verify(material, allow_mutation=True)
    assert [call["allow_mutation"] for call in calls] == [False, True, False]
    assert [call["prefer_operator_auth"] for call in calls] == [False, True, True]
    assert calls[1]["reconcile_managed_roles"] is True
    assert all(call["expected_service_account_id"] == "sa-test" for call in calls)
    assert all(call["project_id"] == "project-test" for call in calls)


def test_expected_identity_mismatch_stops_before_group_mutation(monkeypatch):
    import nebius.api.nebius.iam.v1 as iam

    monkeypatch.setattr(
        iam_bootstrap, "_init_sdk", lambda **_kw: SimpleNamespace(sync_close=lambda: None)
    )
    for name in (
        "AccessPermitServiceClient",
        "GroupMembershipServiceClient",
        "GroupServiceClient",
        "ServiceAccountServiceClient",
    ):
        monkeypatch.setattr(iam, name, lambda _sdk: object())

    def service_account(**kwargs):
        assert kwargs["create_missing"] is False
        return "sa-foreign", False

    monkeypatch.setattr(iam_bootstrap, "_ensure_service_account", service_account)
    monkeypatch.setattr(
        iam_bootstrap, "_ensure_group", lambda **_kw: pytest.fail("must not mutate groups")
    )
    with pytest.raises(RuntimeError, match="does not match"):
        iam_bootstrap.ensure_ci_service_account_identity(
            project_id="project-test",
            service_account_name="nebius-cxcli-sa",
            service_account_description="test",
            role_ids=["admin"],
            profile=None,
            endpoint=None,
            config_file=None,
            prefer_operator_auth=True,
            allow_mutation=True,
            strict_managed_identity=True,
            reconcile_managed_roles=True,
            expected_service_account_id="sa-test",
        )
