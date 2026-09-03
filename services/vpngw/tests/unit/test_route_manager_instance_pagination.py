from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from nebius_vpngw.deploy.route_manager import RouteManager


class _FakeInstancesPb2:
    class ListInstancesRequest:
        def __init__(self, **kwargs):
            self.parent_id = kwargs.get("parent_id")
            self.page_size = kwargs.get("page_size")
            self.page_token = kwargs.get("page_token")


class _FakeInstancesStub:
    """Serves one page of instances per List call from a fixed page plan."""

    def __init__(self, channel, pages):
        self._pages = pages
        self.requests = []

    def List(self, request):
        self.requests.append(request)
        token = request.page_token or "0"
        items, next_token = self._pages.get(token, ([], ""))
        return SimpleNamespace(items=list(items), next_page_token=next_token)


class _FakePb2Grpc:
    def __init__(self, pages):
        self._pages = pages
        self.stub = None

    def InstanceServiceStub(self, channel):
        if self.stub is None:
            self.stub = _FakeInstancesStub(channel, self._pages)
        return self.stub


def _make_instance(name: str, allocation_id: str | None = None, address: str = "10.0.0.5"):
    ni = SimpleNamespace(
        ip_address=SimpleNamespace(allocation_id=allocation_id, address=f"{address}/24")
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(network_interfaces=[ni]),
    )


def _run_paged_listing(pages: dict) -> tuple[list, _FakeInstancesStub]:
    """Run _list_all_instances_paged with fakes injected into sys.modules.

    The helper resolves the pb2 modules lazily via import_module, which
    consults sys.modules first, so the fakes are returned without importing
    the real (SDK-version-dependent) modules.
    """
    grpc_fake = _FakePb2Grpc(pages)

    with patch.dict(
        sys.modules,
        {
            "nebius.api.nebius.compute.v1.instance_service_pb2": _FakeInstancesPb2,
            "nebius.api.nebius.compute.v1.instance_service_pb2_grpc": grpc_fake,
        },
    ):
        rm = RouteManager(project_id="project-test")
        result = rm._list_all_instances_paged(None, parent_id="project-test")
    return result, grpc_fake.stub


def test_list_all_instances_paged_follows_next_page_tokens():
    page1 = [_make_instance(f"gw-0-{i}") for i in range(3)]
    page2 = [_make_instance(f"gw-1-{i}") for i in range(2)]

    result, stub = _run_paged_listing({"0": (page1, "tok-2"), "tok-2": (page2, "")})

    assert len(result) == 5
    assert len(stub.requests) == 2
    assert stub.requests[0].page_size == 999
    assert stub.requests[0].page_token == ""
    assert stub.requests[1].page_token == "tok-2"


def test_list_all_instances_paged_blank_parent_returns_nothing():
    grpc_fake = _FakePb2Grpc({})

    with patch.dict(
        sys.modules,
        {
            "nebius.api.nebius.compute.v1.instance_service_pb2": _FakeInstancesPb2,
            "nebius.api.nebius.compute.v1.instance_service_pb2_grpc": grpc_fake,
        },
    ):
        rm = RouteManager(project_id="project-test")
        result = rm._list_all_instances_paged(None, parent_id="  ")

    assert result == []
    assert grpc_fake.stub is None


def test_list_all_instances_paged_collects_gateway_vms_from_second_page():
    # Gateway VMs land on the second page; single-page listing would miss them.
    filler = [_make_instance(f"other-{i}") for i in range(3)]
    gateways = [
        _make_instance("nebius-vpn-gw-0", allocation_id="alloc-priv-0"),
        _make_instance("nebius-vpn-gw-1", allocation_id="alloc-priv-1", address="10.0.0.6"),
    ]

    result, stub = _run_paged_listing({"0": (filler, "page-2"), "page-2": (gateways, "")})

    names = [inst.metadata.name for inst in result]
    assert len(result) == 5
    assert "nebius-vpn-gw-0" in names
    assert "nebius-vpn-gw-1" in names
