from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw.deploy import vm_ha_cloud as cloud
from nebius_vpngw.nebius_pagination import NebiusPaginationError


def sdk_error(code, message=None):
    return RequestError(SimpleNamespace(code=code, message=message, service_errors=[]))


@pytest.mark.parametrize("status", [520, 521, 522, 523, 524, 525, 526, 527, 528, 529])
@pytest.mark.parametrize("wrapped", [False, True])
def test_gateway_read_recovers_within_original_observation_deadline(status, wrapped):
    now = [100.0]
    calls = []
    expected = object()

    def read(kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            error = sdk_error(StatusCode.UNKNOWN, f"Received http2 header with status: {status}")
            if wrapped:
                outer = NebiusPaginationError("inventory unavailable")
                outer.__cause__ = error
                raise outer
            raise error
        return expected

    with cloud.vm_ha_observation_budget(seconds=1, clock=lambda: now[0]):
        result = cloud.read_vm_ha_cloud(
            read,
            clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            jitter=lambda: 0.0,
        )
    assert result is expected
    assert len(calls) == 2
    assert all(call["retries"] == 1 for call in calls)
    assert 0 < calls[1]["timeout"] < calls[0]["timeout"] < 1


@pytest.mark.parametrize(
    "code,message",
    [
        (StatusCode.UNKNOWN, None),
        (StatusCode.UNKNOWN, "unknown application failure 520"),
        (StatusCode.UNKNOWN, "Received http2 header with status: 403"),
        (StatusCode.UNKNOWN, "Received http2 header with status: 5200"),
        (StatusCode.PERMISSION_DENIED, "Received http2 header with status: 520"),
        (StatusCode.UNAUTHENTICATED, "Received http2 header with status: 520"),
    ],
)
def test_gateway_classifier_does_not_retry_unrecognized_or_permanent_failures(code, message):
    error = sdk_error(code, message)
    read = MagicMock(side_effect=error)
    with pytest.raises(RequestError) as caught:
        cloud.read_vm_ha_cloud(read)
    assert caught.value is error
    assert read.call_count == 1


def test_gateway_text_on_an_untyped_error_does_not_authorize_retry():
    error = RuntimeError("Received http2 header with status: 520")
    error.code = StatusCode.UNKNOWN
    read = MagicMock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        cloud.read_vm_ha_cloud(read)
    assert caught.value is error
    assert read.call_count == 1


def test_gateway_read_exhaustion_preserves_original_deadline():
    now = [0.0]
    error = sdk_error(StatusCode.UNKNOWN, "Received http2 header with status: 520")
    read = MagicMock(side_effect=error)
    with (
        pytest.raises(TimeoutError),
        cloud.vm_ha_observation_budget(seconds=1, clock=lambda: now[0]),
    ):
        cloud.read_vm_ha_cloud(
            read,
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda: 1.0,
        )
    assert 1 < read.call_count < 10
    assert now[0] <= 1.0


def test_gateway_status_does_not_replay_an_uncertain_mutation():
    mutation = MagicMock(
        side_effect=sdk_error(StatusCode.UNKNOWN, "Received http2 header with status: 520")
    )
    with pytest.raises(cloud.AmbiguousHACloudError):
        cloud.VMHACloudAdapter._call("operation", mutation)
    assert mutation.call_count == 1


def test_gateway_text_with_service_error_details_is_not_transport_retry_authority():
    error = sdk_error(StatusCode.UNKNOWN, "Received http2 header with status: 520")
    error.status.service_errors = [object()]
    read = MagicMock(side_effect=error)
    with pytest.raises(RequestError) as caught:
        cloud.read_vm_ha_cloud(read)
    assert caught.value is error
    assert read.call_count == 1


@pytest.mark.parametrize("code", [StatusCode.UNAVAILABLE, StatusCode.DEADLINE_EXCEEDED])
@pytest.mark.parametrize("wrapped", [False, True])
def test_typed_and_paginated_transient_read_recovers_with_one_shared_budget(code, wrapped):
    now = [100.0]
    calls = []
    expected = object()

    def read(kwargs):
        calls.append(kwargs)
        if len(calls) <= 8:
            error = sdk_error(code)
            if wrapped:
                outer = NebiusPaginationError("inventory unavailable")
                outer.__cause__ = error
                raise outer
            raise error
        return expected

    with cloud.vm_ha_observation_budget(seconds=4, clock=lambda: now[0]):
        result = cloud.read_vm_ha_cloud(
            read,
            clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            jitter=lambda: 0.0,
        )
    assert result is expected
    assert len(calls) == 9
    assert all(call["retries"] == 1 for call in calls)
    assert all(0 < call["timeout"] < 4 for call in calls)
    assert calls[-1]["timeout"] < calls[0]["timeout"]


@pytest.mark.parametrize(
    "code",
    [
        StatusCode.PERMISSION_DENIED,
        StatusCode.UNAUTHENTICATED,
        StatusCode.NOT_FOUND,
        StatusCode.RESOURCE_EXHAUSTED,
        StatusCode.ABORTED,
    ],
)
def test_read_does_not_retry_access_missing_or_nontransient_errors(code):
    read = MagicMock(side_effect=sdk_error(code))
    with pytest.raises(RequestError):
        cloud.read_vm_ha_cloud(read)
    assert read.call_count == 1


def test_read_rejects_late_success_and_preserves_deadline():
    now = [0.0]

    def read(kwargs):
        now[0] = 2.0
        return object()

    with (
        pytest.raises(TimeoutError),
        cloud.vm_ha_observation_budget(seconds=1, clock=lambda: now[0]),
    ):
        cloud.read_vm_ha_cloud(read, clock=lambda: now[0])


def test_read_exhaustion_is_paced_and_does_not_reset_budget():
    now = [0.0]
    calls = []

    def read(kwargs):
        calls.append(kwargs)
        raise sdk_error(StatusCode.UNAVAILABLE)

    with (
        pytest.raises(TimeoutError),
        cloud.vm_ha_observation_budget(seconds=1, clock=lambda: now[0]),
    ):
        cloud.read_vm_ha_cloud(
            read,
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda: 1.0,
        )
    assert 1 < len(calls) < 10
    assert now[0] <= 1.0


@pytest.mark.parametrize(
    "method,client_name",
    [("get_instance", "InstanceServiceClient"), ("get_allocation", "AllocationServiceClient")],
)
def test_sdk_exact_read_uses_paced_helper(method, client_name):
    result = object()
    request = MagicMock()
    request.wait.return_value = result
    client = MagicMock()
    client.get.return_value = request
    module = (
        "nebius.api.nebius.compute.v1" if method == "get_instance" else "nebius.api.nebius.vpc.v1"
    )
    with patch(module + "." + client_name, return_value=client):
        assert getattr(cloud.NebiusSDKCloudClient(object()), method)("exact-id") is result
    assert client.get.call_args.kwargs["retries"] == 1
    assert client.get.call_args.args[0].id == "exact-id"


@pytest.mark.parametrize(
    "code,kind",
    [
        (StatusCode.UNAVAILABLE, cloud.RetryableHACloudError),
        (StatusCode.PERMISSION_DENIED, cloud.PermanentHACloudError),
        (StatusCode.NOT_FOUND, cloud.MissingHAComputeError),
    ],
)
def test_adapter_preserves_typed_sdk_classification_without_replaying_mutation(code, kind):
    mutation = MagicMock(side_effect=sdk_error(code))
    with pytest.raises(kind):
        cloud.VMHACloudAdapter._call("operation", mutation)
    assert mutation.call_count == 1


def test_constant_caller_allowance_is_an_absolute_retry_deadline():
    now = [0.0]
    calls = []

    def read(kwargs):
        calls.append(kwargs)
        raise sdk_error(StatusCode.UNAVAILABLE)

    with pytest.raises(TimeoutError):
        cloud.read_vm_ha_cloud(
            read,
            timeout_provider=lambda: 5.0,
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda: 1.0,
        )
    assert 1 < len(calls) < 20
    assert now[0] <= 5.0
    assert calls[-1]["timeout"] < calls[0]["timeout"]


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_caller_allowance_never_submits(value):
    read = MagicMock()
    with pytest.raises(TimeoutError):
        cloud.read_vm_ha_cloud(read, timeout_provider=lambda: value)
    read.assert_not_called()


@pytest.mark.parametrize("exhausted", [False, True])
def test_route_page_retries_reuse_token_inside_one_inventory_deadline(monkeypatch, exhausted):
    from nebius_vpngw.deploy import route_manager

    now = [0.0]
    calls = []

    class Request:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Client:
        def __init__(self, sdk):
            pass

        def list(self, request, **kwargs):
            def wait():
                calls.append((request.page_token, request.parent_id, kwargs))
                now[0] += 0.05
                if request.page_token and (exhausted or len(calls) < 4):
                    raise sdk_error(StatusCode.UNAVAILABLE)
                return SimpleNamespace(
                    items=[
                        SimpleNamespace(
                            metadata=SimpleNamespace(id="second" if request.page_token else "first")
                        )
                    ],
                    next_page_token="" if request.page_token else "next",
                )

            return SimpleNamespace(wait=wait)

    monkeypatch.setattr(
        route_manager.NebiusSDKRouteBackend,
        "_client_types",
        staticmethod(lambda: (Client, Request)),
    )
    monkeypatch.setattr(
        route_manager,
        "vm_ha_observation_budget",
        lambda: cloud.vm_ha_observation_budget(seconds=1, clock=lambda: now[0]),
    )
    monkeypatch.setattr(
        route_manager,
        "read_vm_ha_cloud",
        lambda read: cloud.read_vm_ha_cloud(
            read,
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda: 0.0,
        ),
    )
    backend = route_manager.NebiusSDKRouteBackend(object())
    if exhausted:
        with pytest.raises(NebiusPaginationError):
            backend._raw_routes("table-exact")
        assert now[0] <= 1.0
    else:
        result = backend._raw_routes("table-exact")
        assert [item.metadata.id for item in result] == ["first", "second"]
        assert [token for token, _, _ in calls] == ["", "next", "next", "next"]
    assert [token for token, _, _ in calls].count("") == 1
    assert all(parent == "table-exact" for _, parent, _ in calls)
    allowances = [kwargs["timeout"] for _, _, kwargs in calls]
    assert all(a > b for a, b in zip(allowances, allowances[1:], strict=False))
