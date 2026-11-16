"""Cloud route authority follows the proven effective subnet attachment."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
from nebius_vpngw.schema import VMHARouteTarget


@pytest.fixture
def binding(monkeypatch):
    from nebius.api.nebius.vpc import v1

    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="table-a",
    )
    subnet = SimpleNamespace(
        metadata=SimpleNamespace(id="subnet-a", parent_id="project-a"),
        spec=SimpleNamespace(network_id="network-a", route_table_id="table-a"),
        status=SimpleNamespace(route_table=SimpleNamespace(id="table-a", default=False)),
    )
    table = SimpleNamespace(
        metadata=SimpleNamespace(id="table-a", parent_id="project-a"),
        spec=SimpleNamespace(network_id="network-a"),
        status=SimpleNamespace(assignment=SimpleNamespace(subnets=["subnet-a"])),
    )
    network = SimpleNamespace(
        metadata=SimpleNamespace(id="network-a", parent_id="project-a"),
        status=SimpleNamespace(default_route_table_id="table-a"),
    )
    clients = {}
    for name, value in (("Subnet", subnet), ("RouteTable", table), ("Network", network)):
        client = Mock()
        client.get.return_value.wait.return_value = value
        monkeypatch.setattr(v1, name + "ServiceClient", Mock(return_value=client))
        clients[name] = client
    return SimpleNamespace(
        target=target,
        subnet=subnet,
        table=table,
        network=network,
        clients=clients,
        backend=NebiusSDKRouteBackend(object()),
    )


def inherited(binding):
    binding.subnet.spec.route_table_id = ""
    binding.subnet.status.route_table.default = True


@pytest.mark.parametrize("default", [False, True])
def test_exact_effective_attachment_is_authorized(binding, default):
    if default:
        inherited(binding)
    binding.backend.verify_target(binding.target)
    assert binding.clients["Network"].get.call_count == int(default)
    for client in binding.clients.values():
        for call in client.get.call_args_list:
            assert call.kwargs["retries"] == 1
            assert 0 < call.kwargs["timeout"] <= 10


@pytest.mark.parametrize("foreign_target", [False, True])
def test_route_target_gateway_error_retries_only_failed_read_and_revalidates_identity(
    binding, monkeypatch, foreign_target
):
    from grpc import StatusCode
    from nebius.aio.service_error import RequestError, RequestStatusExtended

    from nebius_vpngw.deploy import route_manager, vm_ha_cloud

    now = [0.0]
    error = RequestError(
        RequestStatusExtended(
            code=StatusCode.UNKNOWN,
            message="Received http2 header with status: 520",
            details=[],
            service_errors=[],
            request_id="",
            trace_id="",
        )
    )
    table = binding.clients["RouteTable"]
    table.get.return_value.wait.side_effect = [error, binding.table]
    if foreign_target:
        binding.table.metadata.id = "foreign-table"
    monkeypatch.setattr(
        route_manager,
        "vm_ha_observation_budget",
        lambda: vm_ha_cloud.vm_ha_observation_budget(seconds=1, clock=lambda: now[0]),
    )
    monkeypatch.setattr(
        route_manager,
        "read_vm_ha_cloud",
        lambda read: vm_ha_cloud.read_vm_ha_cloud(
            read,
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda: 0.0,
        ),
    )
    if foreign_target:
        with pytest.raises(RuntimeError, match="route target membership"):
            binding.backend.verify_target(binding.target)
    else:
        binding.backend.verify_target(binding.target)
    assert binding.clients["Subnet"].get.call_count == 1
    assert binding.clients["Network"].get.call_count == 0
    assert table.get.call_count == 2
    assert all(call.args[0].id == "table-a" for call in table.get.call_args_list)
    assert all(call.kwargs["retries"] == 1 for call in table.get.call_args_list)
    assert (
        table.get.call_args_list[1].kwargs["timeout"]
        < table.get.call_args_list[0].kwargs["timeout"]
    )


@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize(
    "field",
    [
        "subnet.metadata.id",
        "subnet.metadata.parent_id",
        "subnet.spec.network_id",
        "subnet.status.route_table.id",
        "table.metadata.id",
        "table.metadata.parent_id",
        "table.spec.network_id",
    ],
)
def test_changed_parent_or_effective_attachment_is_rejected(binding, default, field):
    if default:
        inherited(binding)
    obj = binding
    parts = field.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], "foreign")
    with pytest.raises(RuntimeError, match="route target membership"):
        binding.backend.verify_target(binding.target)


def test_explicit_spec_change_cannot_hide_behind_old_effective_status(binding):
    binding.subnet.spec.route_table_id = "replacement-table"
    with pytest.raises(RuntimeError, match="route target membership"):
        binding.backend.verify_target(binding.target)


@pytest.mark.parametrize(
    "field",
    [
        "subnet.status.route_table.default",
        "network.metadata.id",
        "network.metadata.parent_id",
        "network.status.default_route_table_id",
    ],
)
def test_default_attachment_requires_exact_network_authority(binding, field):
    inherited(binding)
    obj = binding
    parts = field.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], False if parts[-1] == "default" else "foreign")
    with pytest.raises(RuntimeError, match="route target membership"):
        binding.backend.verify_target(binding.target)


def test_unknown_network_read_cannot_authorize_default_attachment(binding):
    inherited(binding)
    binding.clients["Network"].get.side_effect = TimeoutError("network read unavailable")
    with pytest.raises(TimeoutError):
        binding.backend.verify_target(binding.target)


@pytest.mark.parametrize("default", [False, True])
def test_missing_effective_status_is_not_attachment_evidence(binding, default):
    if default:
        inherited(binding)
    binding.subnet.status = None
    with pytest.raises(RuntimeError, match="route target membership"):
        binding.backend.verify_target(binding.target)


@pytest.mark.parametrize("consumers", [[], ["subnet-a", "foreign-subnet"], ["foreign-subnet"]])
def test_inherited_table_cannot_extend_route_effects_to_unbound_subnets(binding, consumers):
    inherited(binding)
    binding.table.status.assignment.subnets = consumers
    with pytest.raises(RuntimeError, match="route target membership"):
        binding.backend.verify_target(binding.target)
