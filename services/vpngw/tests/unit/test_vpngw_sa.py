from __future__ import annotations

import sys
import typing as t
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw import vpngw_sa
from nebius_vpngw.vpngw_sa import ensure_cli_access_token, get_cli_token


class _Wait:
    def __init__(self, value: object = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    def wait(self):  # type: ignore[no-untyped-def]
        if self.error is not None:
            raise self.error
        return self.value


class _Operation:
    def __init__(self, callback=lambda: None, error: Exception | None = None) -> None:
        self.callback = callback
        self.error = error
        self.sync_wait_calls: list[dict[str, object]] = []

    def sync_wait(self, **kwargs):  # type: ignore[no-untyped-def]
        self.sync_wait_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        self.callback()


def _resource(
    resource_id: str,
    parent_id: str,
    name: str,
    *,
    labels: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id=resource_id,
            parent_id=parent_id,
            name=name,
            labels=dict(labels or {}),
        )
    )


def test_service_account_token_identity_repr_omits_token() -> None:
    identity = vpngw_sa.ServiceAccountTokenIdentity(
        token="short-lived-secret",
        service_account_id="service-account-a",
        service_account_name="gateway-ha",
    )

    assert "short-lived-secret" not in repr(identity)


def test_vm_ha_iam_sdk_prefers_explicit_operator_token(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "selected-token")

    with patch("nebius_vpngw.nebius_auth.build_operator_sdk_client") as build_client:
        client, owned = vpngw_sa._init_client(None, "project-test", "eu-north1")

    assert client is build_client.return_value
    assert owned is True
    build_client.assert_called_once_with(explicit_token="selected-token")


def test_vm_ha_iam_sdk_uses_renewable_cli_credentials(monkeypatch) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)

    with patch("nebius_vpngw.nebius_auth.build_operator_sdk_client") as build_client:
        client, owned = vpngw_sa._init_client(None, "project-test", "eu-north1")

    assert client is build_client.return_value
    assert owned is True
    build_client.assert_called_once_with(explicit_token=None)


class _ExistingNamedService:
    def __init__(self, resource: object) -> None:
        self.resource = resource
        self.create_calls = 0
        self.operations: list[_Operation] = []

    def get_by_name(self, request: object) -> _Wait:
        del request
        return _Wait(self.resource)

    def create(self, request: object) -> _Wait:
        del request
        self.create_calls += 1
        operation = _Operation()
        self.operations.append(operation)
        return _Wait(operation)


class _CreatingNamedService(_ExistingNamedService):
    def __init__(self, resource: object) -> None:
        super().__init__(resource)
        self.reads = 0

    def get_by_name(self, request: object) -> _Wait:
        del request
        self.reads += 1
        if self.reads == 1:
            return _Wait(
                error=RequestError(  # type: ignore[arg-type]
                    SimpleNamespace(code=StatusCode.NOT_FOUND)
                )
            )
        return _Wait(self.resource)


class _MembershipService:
    def __init__(self, member_ids: tuple[str, ...]) -> None:
        self.member_ids = list(member_ids)
        self.create_calls = 0
        self.operations: list[_Operation] = []

    def list_members(self, request: object) -> _Wait:
        del request
        memberships = [
            SimpleNamespace(spec=SimpleNamespace(member_id=member_id))
            for member_id in self.member_ids
        ]
        return _Wait(SimpleNamespace(memberships=memberships, next_page_token=""))

    def create(self, request: object) -> _Wait:
        self.create_calls += 1
        operation = _Operation(
            lambda: self.member_ids.append(request.spec.member_id)  # type: ignore[attr-defined]
        )
        self.operations.append(operation)
        return _Wait(operation)


class _PermitService:
    def __init__(self, permits: tuple[tuple[str, str], ...]) -> None:
        self.permits = list(permits)
        self.create_calls = 0
        self.operations: list[_Operation] = []

    def list(self, request: object) -> _Wait:
        del request
        items = [
            SimpleNamespace(spec=SimpleNamespace(resource_id=resource_id, role=role))
            for resource_id, role in self.permits
        ]
        return _Wait(SimpleNamespace(items=items, next_page_token=""))

    def create(self, request: object) -> _Wait:
        self.create_calls += 1
        operation = _Operation(
            lambda: self.permits.append(  # type: ignore[attr-defined]
                (request.spec.resource_id, request.spec.role)
            )
        )
        self.operations.append(operation)
        return _Wait(operation)


def test_iam_membership_and_permit_inventory_read_every_page() -> None:
    class Service:
        def __init__(self) -> None:
            self.member_tokens: list[str] = []
            self.permit_tokens: list[str] = []

        def list_members(self, request: object) -> _Wait:
            token = str(request.page_token)  # type: ignore[attr-defined]
            self.member_tokens.append(token)
            suffix = "one" if not token else "two"
            return _Wait(
                SimpleNamespace(
                    memberships=[
                        SimpleNamespace(
                            metadata=SimpleNamespace(id=f"membership-{suffix}"),
                            spec=SimpleNamespace(member_id=f"member-{suffix}"),
                        )
                    ],
                    next_page_token="next" if not token else "",
                )
            )

        def list(self, request: object) -> _Wait:
            token = str(request.page_token)  # type: ignore[attr-defined]
            self.permit_tokens.append(token)
            suffix = "one" if not token else "two"
            return _Wait(
                SimpleNamespace(
                    items=[
                        SimpleNamespace(
                            metadata=SimpleNamespace(id=f"permit-{suffix}"),
                            spec=SimpleNamespace(
                                resource_id=f"project-{suffix}",
                                role="editor",
                            ),
                        )
                    ],
                    next_page_token="next" if not token else "",
                )
            )

    service = Service()

    assert vpngw_sa._list_group_member_ids(service, "group-a") == (
        "member-one",
        "member-two",
    )
    assert vpngw_sa._list_access_permits(service, "group-a") == (
        ("project-one", "editor"),
        ("project-two", "editor"),
    )
    assert service.member_tokens == ["", "next"]
    assert service.permit_tokens == ["", "next"]


@pytest.mark.parametrize(
    ("ensure", "message"),
    (
        (vpngw_sa._ensure_service_account, "Failed to read the requested Service Account"),
        (vpngw_sa._ensure_group, "Failed to read the dedicated Service Account group"),
    ),
)
def test_named_iam_read_does_not_treat_error_text_as_absence(ensure, message: str) -> None:
    service = SimpleNamespace(
        get_by_name=Mock(
            return_value=_Wait(error=OSError("upstream NOT_FOUND diagnostic; authorization failed"))
        ),
        create=Mock(),
    )

    with pytest.raises(RuntimeError, match=message):
        ensure(service, "project-test", "gateway-runtime")

    service.create.assert_not_called()


def _identity_services(
    *,
    project_id: str = "project-test",
    name: str = "gateway-runtime",
    create: bool = False,
    member_ids: tuple[str, ...] = ("service-account",),
    permits: tuple[tuple[str, str], ...] = (("project-test", "editor"),),
):
    service_account = _resource("service-account", project_id, name)
    group = _resource("group", project_id, name)
    named_type = _CreatingNamedService if create else _ExistingNamedService
    return (
        named_type(service_account),
        named_type(group),
        _MembershipService(() if create else member_ids),
        _PermitService(() if create else permits),
    )


def test_get_cli_token_reads_token_attribute(monkeypatch) -> None:
    nebius_module = ModuleType("nebius")
    aio_module = ModuleType("nebius.aio")
    cli_config_module = ModuleType("nebius.aio.cli_config")

    class FakeConfig:
        def __init__(self, no_parent_id: bool = True) -> None:
            self.no_parent_id = no_parent_id
            self.token = "cli-token"

    t.cast(t.Any, cli_config_module).Config = FakeConfig
    t.cast(t.Any, aio_module).cli_config = cli_config_module
    t.cast(t.Any, nebius_module).aio = aio_module

    monkeypatch.setitem(sys.modules, "nebius", nebius_module)
    monkeypatch.setitem(sys.modules, "nebius.aio", aio_module)
    monkeypatch.setitem(sys.modules, "nebius.aio.cli_config", cli_config_module)

    assert get_cli_token() == "cli-token"


def test_ensure_cli_access_token_falls_back_to_supported_cli_json() -> None:
    with (
        patch("nebius_vpngw.vpngw_sa.get_cli_token", return_value=None),
        patch(
            "nebius_vpngw.vpngw_sa.subprocess.run",
            return_value=Mock(returncode=0, stdout='{"access_token":"token-123"}', stderr=""),
        ) as run_mock,
    ):
        token = ensure_cli_access_token(timeout_seconds=1)

    assert token == "token-123"
    command = run_mock.call_args.args[0]
    assert command[-2:] == ["iam", "get-access-token"]
    assert "--format" in command
    assert "--impersonate-service-account-id" not in command


def test_cli_access_token_can_ignore_ambient_token(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "ambient-token")

    with patch(
        "nebius_vpngw.vpngw_sa.subprocess.run",
        return_value=Mock(returncode=0, stdout='{"access_token":"token-123"}', stderr=""),
    ) as run_mock:
        token = vpngw_sa._cli_access_token(
            timeout_seconds=1,
            ignore_ambient_token=True,
            no_browser=True,
            process_timeout_seconds=1.0,
        )

    assert token == "token-123"
    assert "NEBIUS_IAM_TOKEN" not in run_mock.call_args.kwargs["env"]
    assert "--no-browser" in run_mock.call_args.args[0]
    assert run_mock.call_args.kwargs["timeout"] == 1.0


def test_service_account_existing_identity_is_exact_and_token_is_impersonated(
    monkeypatch,
) -> None:
    client = SimpleNamespace(sync_close=Mock())
    services = _identity_services()
    monkeypatch.setattr(vpngw_sa, "_init_client", lambda *args: (client, True))
    monkeypatch.setattr(vpngw_sa, "_iam_services", lambda _: services)
    token_call = Mock(return_value="short-lived-token")
    monkeypatch.setattr(vpngw_sa, "_cli_access_token", token_call)

    token = vpngw_sa.ensure_service_account_and_token(
        "gateway-runtime",
        None,
        "project-test",
        None,
        role_ids=("editor",),
    )

    assert token == "short-lived-token"
    assert services[0].create_calls == 0
    assert services[1].create_calls == 0
    assert services[2].create_calls == 0
    assert services[3].create_calls == 0
    client.sync_close.assert_called_once_with()
    assert token_call.call_args.kwargs["service_account_id"] == "service-account"


def test_service_account_creation_rereads_every_identity_and_permission(monkeypatch) -> None:
    client = SimpleNamespace(sync_close=Mock())
    services = _identity_services(create=True)
    monkeypatch.setattr(vpngw_sa, "_init_client", lambda *args: (client, True))
    monkeypatch.setattr(vpngw_sa, "_iam_services", lambda _: services)
    monkeypatch.setattr(vpngw_sa, "_cli_access_token", lambda **kwargs: "token")

    assert (
        vpngw_sa.ensure_service_account_and_token(
            "gateway-runtime",
            None,
            "project-test",
            None,
            role_ids=("editor",),
        )
        == "token"
    )
    assert services[0].create_calls == 1
    assert services[1].create_calls == 1
    assert services[2].create_calls == 1
    assert services[3].create_calls == 1
    assert all(service.operations[0].sync_wait_calls for service in services)


def test_terminal_iam_operation_failure_is_not_treated_as_completed() -> None:
    operation = _Operation(error=RuntimeError("terminal operation failed"))

    with pytest.raises(RuntimeError, match="terminal operation failed"):
        vpngw_sa._wait_operation(_Wait(operation))

    assert operation.sync_wait_calls


def test_managed_iam_reuse_plan_is_read_only_and_rejects_late_permission_drift(
    monkeypatch,
) -> None:
    labels = {"managed-by": "nebius-vpngw", "vm-ha-owner": "owner"}
    service_account = _resource("service-account", "project-test", "gateway-runtime", labels=labels)
    group = _resource("group", "project-test", "gateway-runtime", labels=labels)
    services = (
        _ExistingNamedService(service_account),
        _ExistingNamedService(group),
        _MembershipService(("service-account",)),
        _PermitService((("project-test", "editor"),)),
    )
    client = SimpleNamespace(sync_close=Mock())
    monkeypatch.setattr(vpngw_sa, "_iam_services", lambda _: services)

    plan = vpngw_sa.inspect_vm_ha_service_account_identity(
        "gateway-runtime",
        None,
        "project-test",
        None,
        mode="reuse",
        expected_service_account_id="service-account",
        ownership_labels=labels,
        client_factory=lambda *_args: (client, True),
    )

    assert plan.approval_record() == {
        "group": {"action": "reuse", "id": "group"},
        "membership": {"action": "reuse", "member_id": "service-account"},
        "project_editor_permit": {
            "action": "reuse",
            "resource_id": "project-test",
            "role": "editor",
        },
        "service_account": {"action": "reuse", "id": "service-account"},
    }
    assert all(service.create_calls == 0 for service in services)

    services[3].permits.clear()
    monkeypatch.setattr(vpngw_sa, "_init_client", lambda *_args: (client, True))
    token_call = Mock(return_value="must-not-run")
    monkeypatch.setattr(vpngw_sa, "_cli_access_token", token_call)
    with pytest.raises(RuntimeError, match="permit changed after approval"):
        vpngw_sa.ensure_service_account_identity_and_token(
            "gateway-runtime",
            None,
            "project-test",
            None,
            ownership_labels=labels,
            reconciliation_plan=plan,
        )
    assert services[3].create_calls == 0
    token_call.assert_not_called()


@pytest.mark.parametrize(
    ("member_ids", "permits", "message"),
    [
        (("foreign",), (("project-test", "editor"),), "foreign or duplicate members"),
        (("service-account",), (("other-resource", "editor"),), "unexpected access permits"),
        (
            ("service-account",),
            (("project-test", "editor"), ("project-test", "editor")),
            "unexpected access permits",
        ),
    ],
)
def test_service_account_enrollment_fails_closed_on_group_drift(
    monkeypatch,
    member_ids: tuple[str, ...],
    permits: tuple[tuple[str, str], ...],
    message: str,
) -> None:
    client = SimpleNamespace(sync_close=Mock())
    services = _identity_services(member_ids=member_ids, permits=permits)
    monkeypatch.setattr(vpngw_sa, "_init_client", lambda *args: (client, True))
    monkeypatch.setattr(vpngw_sa, "_iam_services", lambda _: services)
    token_call = Mock(return_value="must-not-be-used")
    monkeypatch.setattr(vpngw_sa, "_cli_access_token", token_call)

    with pytest.raises(RuntimeError, match=message):
        vpngw_sa.ensure_service_account_and_token(
            "gateway-runtime",
            None,
            "project-test",
            None,
            role_ids=("editor",),
        )
    token_call.assert_not_called()
    client.sync_close.assert_called_once_with()


def test_service_account_rejects_legacy_and_unreviewed_role_names() -> None:
    for role_ids in (("roles/editor",), ("compute.editor", "vpc.editor"), ()):
        with pytest.raises(ValueError, match="only project role 'editor'"):
            vpngw_sa.ensure_service_account_and_token(
                "gateway-runtime",
                None,
                "project-test",
                None,
                role_ids=role_ids,
            )


def test_impersonated_token_command_is_bounded_and_never_logs_token(monkeypatch) -> None:
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout='{"access_token":"secret-token"}',
            stderr="",
        )
    )
    monkeypatch.setattr(vpngw_sa.subprocess, "run", run)

    assert (
        vpngw_sa._cli_access_token(
            timeout_seconds=7,
            service_account_id="service-account",
        )
        == "secret-token"
    )
    command = run.call_args.args[0]
    assert command[:3] == [
        "nebius",
        "--impersonate-service-account-id",
        "service-account",
    ]
    assert "--no-browser" in command
    assert run.call_args.kwargs["timeout"] == 12
    assert "secret-token" not in repr(run.call_args)


def test_impersonated_token_failure_returns_no_ambient_token(monkeypatch) -> None:
    monkeypatch.setattr(
        vpngw_sa.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="denied")),
    )
    assert (
        vpngw_sa._cli_access_token(
            timeout_seconds=1,
            service_account_id="service-account",
        )
        is None
    )


def test_access_token_parser_rejects_json_without_a_token() -> None:
    assert vpngw_sa._parse_access_token('{"unexpected":"value"}') is None
