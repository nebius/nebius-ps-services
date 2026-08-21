from __future__ import annotations

import sys
import typing as t
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

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


def _resource(resource_id: str, parent_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(id=resource_id, parent_id=parent_id, name=name)
    )


class _ExistingNamedService:
    def __init__(self, resource: object) -> None:
        self.resource = resource
        self.create_calls = 0

    def get_by_name(self, request: object) -> _Wait:
        del request
        return _Wait(self.resource)

    def create(self, request: object) -> _Wait:
        del request
        self.create_calls += 1
        return _Wait(SimpleNamespace(resource_id="created"))


class _CreatingNamedService(_ExistingNamedService):
    def __init__(self, resource: object) -> None:
        super().__init__(resource)
        self.reads = 0

    def get_by_name(self, request: object) -> _Wait:
        del request
        self.reads += 1
        if self.reads == 1:
            return _Wait(error=RuntimeError("StatusCode.NOT_FOUND"))
        return _Wait(self.resource)


class _MembershipService:
    def __init__(self, member_ids: tuple[str, ...]) -> None:
        self.member_ids = list(member_ids)
        self.create_calls = 0

    def list_members(self, request: object) -> _Wait:
        del request
        memberships = [
            SimpleNamespace(spec=SimpleNamespace(member_id=member_id))
            for member_id in self.member_ids
        ]
        return _Wait(SimpleNamespace(memberships=memberships, next_page_token=""))

    def create(self, request: object) -> _Wait:
        self.create_calls += 1
        self.member_ids.append(request.spec.member_id)  # type: ignore[attr-defined]
        return _Wait(SimpleNamespace(resource_id="membership"))


class _PermitService:
    def __init__(self, permits: tuple[tuple[str, str], ...]) -> None:
        self.permits = list(permits)
        self.create_calls = 0

    def list(self, request: object) -> _Wait:
        del request
        items = [
            SimpleNamespace(spec=SimpleNamespace(resource_id=resource_id, role=role))
            for resource_id, role in self.permits
        ]
        return _Wait(SimpleNamespace(items=items, next_page_token=""))

    def create(self, request: object) -> _Wait:
        self.create_calls += 1
        self.permits.append((request.spec.resource_id, request.spec.role))  # type: ignore[attr-defined]
        return _Wait(SimpleNamespace(resource_id="permit"))


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
