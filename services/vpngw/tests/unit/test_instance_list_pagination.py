from __future__ import annotations

from types import SimpleNamespace

from nebius_vpngw.cli import _list_all_instances


class _FakeListInstancesRequest:
    """Mimics nebius ListInstancesRequest: stores kwargs on the instance."""

    def __init__(self, **kwargs):
        self.parent_id = kwargs.get("parent_id")
        self.page_size = kwargs.get("page_size")
        self.page_token = kwargs.get("page_token")


class _FakeWaitable:
    def __init__(self, response):
        self._response = response

    def wait(self):
        return self._response


class _FakeInstanceServiceClient:
    """Serves instances from an in-memory project across N pages."""

    def __init__(self, project_instances: dict[str, list], page_size_limit: int = 5):
        self._projects = project_instances
        self._page_size_limit = page_size_limit
        self.requests: list[_FakeListInstancesRequest] = []

    def list(self, request):
        self.requests.append(request)
        items = self._projects.get(request.parent_id, [])
        # Emulate the server: page_size 0 means default (all in one page here),
        # otherwise honor the requested size up to the limit.
        if request.page_size:
            size = min(request.page_size, self._page_size_limit)
        else:
            size = self._page_size_limit

        if request.page_token:
            try:
                offset = int(request.page_token)
            except ValueError:
                offset = 0
        else:
            offset = 0

        page = items[offset : offset + size]
        next_offset = offset + size
        next_token = str(next_offset) if next_offset < len(items) else ""
        return _FakeWaitable(SimpleNamespace(items=page, next_page_token=next_token))


def _make_instance(name: str):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, id=f"id-{name}"),
    )


def test_list_all_instances_follows_pagination_across_pages():
    # 12 instances with a server page limit of 5 -> 3 pages (5 + 5 + 2)
    instances = [_make_instance(f"vm-{i}") for i in range(12)]
    fake = _FakeInstanceServiceClient({"proj-1": instances})

    result = _list_all_instances(fake, "proj-1")

    assert len(result) == 12
    names = [inst.metadata.name for inst in result]
    assert names == [f"vm-{i}" for i in range(12)]
    assert len(fake.requests) == 3
    # Every request must set an explicit page_size and carry the page token.
    assert all(req.page_size == 999 for req in fake.requests)
    assert [req.page_token for req in fake.requests] == ["", "5", "10"]


def test_list_all_instances_single_page_when_no_token():
    instances = [_make_instance(f"vm-{i}") for i in range(3)]
    fake = _FakeInstanceServiceClient({"proj-1": instances})

    result = _list_all_instances(fake, "proj-1")

    assert len(result) == 3
    assert len(fake.requests) == 1
    assert fake.requests[0].page_size == 999
    assert fake.requests[0].page_token == ""


def test_list_all_instances_empty_project_returns_empty_list():
    fake = _FakeInstanceServiceClient({})

    assert _list_all_instances(fake, "proj-1") == []
    assert len(fake.requests) == 1  # one request for the valid project id

    # Blank project ids must not hit the API at all.
    assert _list_all_instances(fake, "") == []
    assert _list_all_instances(fake, "   ") == []
    assert len(fake.requests) == 1


def test_list_all_instances_stops_on_repeated_token():
    # A server that keeps returning the same token must not cause a loop.
    class _RepeatingClient(_FakeInstanceServiceClient):
        def list(self, request):
            self.requests.append(request)
            page = [_make_instance("vm-x")]
            return _FakeWaitable(SimpleNamespace(items=page, next_page_token="same"))

    fake = _RepeatingClient({"proj-1": [_make_instance("vm-x")]})
    result = _list_all_instances(fake, "proj-1")

    assert len(result) == 2  # first page + one retry before detecting the loop
    assert len(fake.requests) == 2


def test_list_all_instances_uses_default_page_size_cap():
    # The helper must never ask for more than 999 (API limit).
    fake = _FakeInstanceServiceClient({"proj-1": [_make_instance("vm-0")]})

    _list_all_instances(fake, "proj-1")

    assert fake.requests[0].page_size <= 999


def test_gateway_vm_beyond_first_page_is_found_by_ensure_gate():
    """Regression for the reported bug.

    Project has more instances than one page. The gateway VMs sit past
    the first page. A single-page listing misses them (the old code),
    while the paginated helper finds them and the CLI gate passes.
    """

    from nebius_vpngw.cli import _ensure_gateway_vms_exist
    from nebius_vpngw.config_loader import (
        GatewayGroupSpec,
        InstanceResolvedConfig,
        ResolvedDeploymentPlan,
    )

    # 6 filler instances + 2 gateway VMs, server returns 3 per page:
    # gateway VMs land on page 3.
    items = [_make_instance(f"filler-{i}") for i in range(6)]
    items += [_make_instance("nebius-vpn-gw-0"), _make_instance("nebius-vpn-gw-1")]
    fake = _FakeInstanceServiceClient({"proj-1": items}, page_size_limit=3)

    # Old single-page behavior would find zero gateway VMs.
    single_op = fake.list(_FakeListInstancesRequest(parent_id="proj-1"))
    single_page = single_op.wait().items
    old_found = [i for i in single_page if i.metadata.name.startswith("nebius-vpn-gw-")]
    assert old_found == []

    # New paginated helper finds both gateway VMs across 3 pages.
    result = _list_all_instances(fake, "proj-1")
    gw_names = [i.metadata.name for i in result if i.metadata.name.startswith("nebius-vpn-gw-")]
    assert gw_names == ["nebius-vpn-gw-0", "nebius-vpn-gw-1"]

    # Drive the real CLI gate with a fake VMManager exposing the fake client.
    spec = GatewayGroupSpec(
        name="nebius-vpn-gw",
        instance_count=2,
        region="eu-west-2",
        external_ips=[[], []],
        vm_spec={},
    )
    plan = ResolvedDeploymentPlan(
        gateway_group=spec,
        per_instance=[
            InstanceResolvedConfig(0, "nebius-vpn-gw-0", "1.2.3.4", "cfg"),
            InstanceResolvedConfig(1, "nebius-vpn-gw-1", "1.2.3.5", "cfg"),
        ],
    )

    class _FakeVMManager:
        def __init__(self, **kwargs):
            pass

        def _get_client(self):
            return fake

    from unittest.mock import patch

    import nebius_vpngw.cli as cli_module

    with patch.object(cli_module, "VMManager", _FakeVMManager):
        # Must not raise typer.Exit (the old "no gateway VMs found" failure).
        _ensure_gateway_vms_exist(
            plan,
            project_id="proj-1",
            zone="eu-west-2",
            auth_token=None,
            tenant_id=None,
            region_id=None,
            action="status",
        )
