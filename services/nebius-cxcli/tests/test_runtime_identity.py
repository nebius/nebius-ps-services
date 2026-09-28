from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, iam_bootstrap


def _sdk_error(code):
    from nebius.aio.service_error import RequestError, RequestStatusExtended

    return RequestError(
        RequestStatusExtended(
            code=code,
            message="private provider payload sentinel",
            details=[],
            service_errors=[],
            request_id="",
            trace_id="",
        )
    )


def test_project_auth_automatically_reconciles_after_key_validation(monkeypatch, material):
    events = []

    def load(**kwargs):
        assert kwargs["reconcile_permissions"] is True
        assert kwargs["recreate"] is False
        events.append("load")
        return material, False

    def verify(_material, **kwargs):
        assert kwargs.get("allow_mutation") is True
        events.append("permissions")

    monkeypatch.setattr(cli, "_RUNTIME_AUTH_READY_PROJECTS", {})
    monkeypatch.setattr(cli, "_capture_runtime_auth_operator_environment", lambda: None)
    monkeypatch.setattr(cli, "_create_or_recreate_runtime_auth_profile", load)
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _m: events.append("key"))
    monkeypatch.setattr(cli._runtime_identity_verifier, "verify", verify)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _m: events.append("export"))

    cli._ensure_project_auth_identity(project_id="project-test", client_name="test")
    assert events == ["load", "key", "permissions", "export"]


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


@pytest.mark.parametrize("allow_mutation", [False, True])
def test_provider_failure_does_not_reconcile_or_expose_error(monkeypatch, material, allow_mutation):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("private provider payload sentinel")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    with pytest.raises(RuntimeError, match="no permission repair was attempted") as error:
        cli._runtime_identity_verifier.verify(material, allow_mutation=allow_mutation)
    assert "sentinel" not in str(error.value)
    assert len(calls) == 1
    assert not calls[0]["allow_mutation"]


def test_readonly_identity_check_does_not_reconcile_drift(monkeypatch, material):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        raise iam_bootstrap.ManagedIdentityDriftError("missing project admin")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    with pytest.raises(RuntimeError, match="read-only"):
        cli._runtime_identity_verifier.verify(material)
    assert len(calls) == 1
    assert calls[0]["allow_mutation"] is False


def test_auth_reconciles_then_revalidates_with_exact_canonical_identity(monkeypatch, material):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise iam_bootstrap.ManagedIdentityDriftError("missing project admin")
        return SimpleNamespace(service_account_id="sa-test")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    cli._runtime_identity_verifier.verify(material, allow_mutation=True)
    assert [call["allow_mutation"] for call in calls] == [False, True, False]
    assert [call["prefer_operator_auth"] for call in calls] == [False, True, False]
    assert calls[1]["reconcile_managed_roles"] is True
    assert all(call["expected_service_account_id"] == "sa-test" for call in calls)
    assert all(call["project_id"] == "project-test" for call in calls)


@pytest.mark.parametrize("wrapped", [False, True])
def test_denied_canonical_iam_reads_require_operator_readonly_drift_proof(
    monkeypatch, material, wrapped
):
    from grpc import StatusCode

    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            denied = _sdk_error(StatusCode.PERMISSION_DENIED)
            if wrapped:
                raise RuntimeError("IAM lookup failed") from denied
            raise denied
        if len(calls) == 2:
            raise iam_bootstrap.ManagedIdentityDriftError("missing project admin")
        return SimpleNamespace(service_account_id="sa-test")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    cli._runtime_identity_verifier.verify(material, allow_mutation=True)
    assert [call["allow_mutation"] for call in calls] == [False, False, True, False]
    assert [call["prefer_operator_auth"] for call in calls] == [False, True, True, False]
    assert all(call["expected_service_account_id"] == "sa-test" for call in calls)


@pytest.mark.parametrize("operator_result", ["healthy", "foreign", "denied"])
def test_denied_canonical_reads_do_not_mutate_without_proved_drift(
    monkeypatch, material, operator_result
):
    from grpc import StatusCode

    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1 or operator_result == "denied":
            raise _sdk_error(StatusCode.PERMISSION_DENIED)
        if operator_result == "foreign":
            raise RuntimeError("foreign identity sentinel")
        return SimpleNamespace(service_account_id="sa-test")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    with pytest.raises(RuntimeError) as error:
        cli._runtime_identity_verifier.verify(material, allow_mutation=True)
    assert "sentinel" not in str(error.value)
    assert len(calls) == 2
    assert not any(call["allow_mutation"] for call in calls)


@pytest.mark.parametrize(
    "code_name,allow_mutation",
    [
        ("PERMISSION_DENIED", False),
        ("UNAVAILABLE", True),
        ("UNAUTHENTICATED", True),
    ],
)
def test_other_sdk_failures_and_readonly_checks_never_use_operator(
    monkeypatch, material, code_name, allow_mutation
):
    from grpc import StatusCode

    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        raise _sdk_error(StatusCode[code_name])

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    with pytest.raises(RuntimeError, match="no permission repair") as error:
        cli._runtime_identity_verifier.verify(material, allow_mutation=allow_mutation)
    assert "sentinel" not in str(error.value)
    assert len(calls) == 1
    assert calls[0]["prefer_operator_auth"] is False
    assert calls[0]["allow_mutation"] is False


def test_malformed_group_lookup_is_not_recoverable_drift():
    groups = SimpleNamespace(get_by_name=lambda _: SimpleNamespace(wait=lambda: SimpleNamespace()))
    with pytest.raises(RuntimeError, match="no resource identity") as error:
        iam_bootstrap._ensure_group(
            groups=groups, project_id="project-test", group_name="test", create_missing=False
        )
    assert not isinstance(error.value, iam_bootstrap.ManagedIdentityDriftError)


@pytest.mark.parametrize("failure_stage", ["operator", "canonical", "changed_identity"])
def test_failed_reconciliation_never_accepts_operator_only_success(
    monkeypatch, material, failure_stage
):
    calls = []

    def ensure(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise iam_bootstrap.ManagedIdentityDriftError("missing project admin")
        if len(calls) == 2:
            if failure_stage == "operator":
                raise RuntimeError("private provider payload sentinel")
            if failure_stage == "changed_identity":
                return SimpleNamespace(service_account_id="sa-foreign")
            return SimpleNamespace(service_account_id="sa-test")
        raise RuntimeError("private provider payload sentinel")

    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    with pytest.raises(RuntimeError) as error:
        cli._runtime_identity_verifier.verify(material, allow_mutation=True)
    assert "sentinel" not in str(error.value)
    assert len(calls) == (3 if failure_stage == "canonical" else 2)


@pytest.mark.parametrize("initial_roles", [[], ["editor"], ["admin", "editor"], ["admin"]])
def test_normal_project_auth_converges_managed_roles_and_reuses_healthy_credentials(
    monkeypatch, material, initial_roles
):
    rows = {f"permit-{role}": role for role in initial_roles}
    mutations = []
    identity_calls = []
    profile_calls = []

    class Permits:
        def list(self, request):
            assert request.parent_id == "group-test"
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    items=[
                        SimpleNamespace(
                            metadata=SimpleNamespace(id=permit_id),
                            spec=SimpleNamespace(resource_id="project-test", role=role),
                        )
                        for permit_id, role in rows.items()
                    ],
                    next_page_token="",
                )
            )

        def create(self, request):
            assert request.spec.role == "admin"
            assert request.spec.resource_id == "project-test"
            mutations.append("create-admin")
            rows["permit-admin"] = "admin"
            return SimpleNamespace(wait=lambda: None)

        def delete(self, request):
            assert "admin" in rows.values()
            assert request.id == "permit-editor"
            mutations.append("delete-editor")
            del rows[request.id]
            return SimpleNamespace(wait=lambda: None)

    def ensure(**kwargs):
        identity_calls.append(kwargs)
        assert kwargs["expected_service_account_id"] == material.service_account_id
        iam_bootstrap._ensure_project_role_permits(
            access_permits=Permits(),
            permit_parent_id="group-test",
            principal_label="test",
            project_id=kwargs["project_id"],
            role_ids=kwargs["role_ids"],
            reject_unexpected_role_ids=True,
            create_missing=kwargs["allow_mutation"],
            reconcile_managed_roles=kwargs.get("reconcile_managed_roles", False),
        )
        return SimpleNamespace(service_account_id=material.service_account_id)

    def load(**kwargs):
        assert kwargs["recreate"] is False
        assert kwargs["reconcile_permissions"] is True
        profile_calls.append(kwargs)
        return material, False

    monkeypatch.setattr(cli, "_RUNTIME_AUTH_READY_PROJECTS", {})
    monkeypatch.setattr(cli, "_capture_runtime_auth_operator_environment", lambda: None)
    monkeypatch.setattr(cli, "_create_or_recreate_runtime_auth_profile", load)
    monkeypatch.setattr(cli, "_wait_for_runtime_auth_token_ready", lambda _m: None)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", lambda _m: None)
    monkeypatch.setattr(cli, "ensure_ci_service_account_identity", ensure)
    monkeypatch.setattr(
        cli,
        "create_service_account_auth_public_key",
        lambda **kw: pytest.fail("healthy key must be reused"),
    )
    cli._ensure_project_auth_identity(project_id="project-test", client_name="test")
    assert rows == {"permit-admin": "admin"}
    expected = [] if "admin" in initial_roles else ["create-admin"]
    if "editor" in initial_roles:
        expected.append("delete-editor")
    assert mutations == expected

    cli._RUNTIME_AUTH_READY_PROJECTS.clear()
    identity_calls.clear()
    cli._ensure_project_auth_identity(project_id="project-test", client_name="test")
    assert mutations == expected
    assert len(profile_calls) == 2
    assert len(identity_calls) == 1
    assert identity_calls[0]["allow_mutation"] is False
    assert identity_calls[0]["prefer_operator_auth"] is False


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
