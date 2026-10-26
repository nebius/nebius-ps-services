from __future__ import annotations

import copy
import itertools
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli.nat_gateway_address_inventory import read_address_pool, verify_gateway_address
from nebius_cxcli.nat_gateway_address_plan import (
    assess_config_address_space,
    assess_plan_address_space,
)
from nebius_cxcli.nat_gateway_address_space import (
    AddressBlock,
    AddressDemand,
    AddressPool,
    AddressSpaceUnresolved,
    IPv4,
    Reservation,
    assess_address_space,
)
from test_nat_gateway import config


def pool(cidr="10.0.0.0/28", occupied=(), *, identity="pool", maximum=32):
    return AddressPool(
        identity,
        (AddressBlock(IPv4(cidr), maximum),),
        tuple(Reservation(str(index), (IPv4(c),)) for index, c in enumerate(occupied)),
    )


def demand(cidr="/28", *, identity="nat", pools=("pool",)):
    return AddressDemand(identity, pools, cidr)


@pytest.mark.parametrize(
    ("occupied", "cidr", "expected"),
    [
        ((), "/28", "feasible"),
        (("10.0.0.0/28",), "/28", "insufficient"),
        (("10.0.0.0/32", "10.0.0.8/32"), "/29", "insufficient"),
        (("10.0.0.0/29",), "10.0.0.8/29", "feasible"),
        (("10.0.0.0/29",), "10.0.0.0/29", "insufficient"),
    ],
)
def test_contiguous_space_not_total_addresses(occupied, cidr, expected):
    report = assess_address_space((pool(occupied=occupied),), (demand(cidr),))
    assert report.status == expected


def test_aggregate_requests_do_not_reuse_same_block():
    report = assess_address_space((pool(),), (demand(identity="a"), demand(identity="b")))
    assert report.status == "insufficient"
    assert report.requested_addresses == 32
    assert report.capacities[0].free_addresses == 16


def test_fragmented_inventory_and_candidate_search_share_work_budget():
    occupied = tuple(f"10.0.0.{index}/32" for index in range(0, 256, 2))
    report = assess_address_space(
        (pool("10.0.0.0/24", occupied),), (demand("/32"),), work_limit=1_000
    )
    assert report.status == "unresolved"
    assert "work limit" in report.reason
    assert "usable free 0" not in report.message()
    assert (
        assess_address_space((pool("10.0.0.0/24", occupied),), (demand("/32"),)).status
        == "feasible"
    )
    pools = tuple(pool(identity=str(index)) for index in range(16))
    request = demand(pools=tuple(p.identity for p in pools))
    report = assess_address_space(pools, (request,), work_limit=300)
    assert report.status == "unresolved"
    assert "work limit" in report.reason


def test_pool_identity_and_mask_restrictions_survive_backtracking():
    pools = (pool(identity="a", maximum=28), pool(identity="b"))
    requests = (demand(pools=("a", "b")), demand("/32", identity="host", pools=("b",)))
    assert assess_address_space(pools, requests).status == "feasible"
    assert (
        assess_address_space((pools[0],), (demand("/32", pools=("a",)),)).status == "insufficient"
    )
    # Identical ranges in separate pool trees are independent reservations.
    assert (
        assess_address_space(
            pools, (demand(pools=("a",)), demand(identity="other", pools=("b",)))
        ).status
        == "feasible"
    )


def test_exact_requests_precede_auto_and_overlapping_occupancy_is_not_counted_twice():
    report = assess_address_space(
        (pool("10.0.0.0/27", ("10.0.0.0/30", "10.0.0.1/32")),),
        (demand("/28"), demand("10.0.0.8/29", identity="work")),
    )
    assert report.status == "feasible"
    assert report.capacities[0].free_addresses == 28


def test_search_limit_and_missing_pool_are_unresolved():
    assert assess_address_space((pool(),), (demand(),), search_limit=1).status == "unresolved"
    assert assess_address_space((pool(),), (demand(pools=("missing",)),)).status == "unresolved"


def test_fit_matches_small_exhaustive_address_oracle():
    # Independent bit-mask enumeration on eight addresses checks fragmentation,
    # alignment and multiple request sizes without mirroring CIDR subtraction.
    base = int(IPv4("10.0.0.0/29").network_address)

    def oracle(used, prefixes):
        if not prefixes:
            return True
        size = 1 << (32 - prefixes[0])
        for offset in range(0, 8, size):
            mask = ((1 << size) - 1) << offset
            if not used & mask and oracle(used | mask, prefixes[1:]):
                return True
        return False

    for used, prefixes in itertools.product(range(256), ((30,), (31, 31), (30, 31, 32))):
        occupied = [str(IPv4((base + i, 32))) for i in range(8) if used & (1 << i)]
        expected = oracle(used, prefixes)
        result = assess_address_space(
            (pool("10.0.0.0/29", occupied),),
            tuple(demand(f"/{prefix}", identity=str(i)) for i, prefix in enumerate(prefixes)),
        )
        assert (result.status == "feasible") == expected, (used, prefixes, result)


def sdk_pool(identity="pool", cidr="10.0.0.0/24", *, state="AVAILABLE", maximum=32, source=""):
    return NS(
        metadata=NS(id=identity),
        spec=NS(
            visibility="PRIVATE",
            version="IPV4",
            source_pool_id=source,
            cidrs=[NS(cidr=cidr, state=state, max_mask_length=maximum)],
        ),
        status=NS(
            state="READY",
            cidrs=[cidr],
            scope_id="tree",
            assignment=NS(subnets=["gateway"] if identity == "child" else []),
        ),
    )


class InventorySDK:
    """External SDK response fixture uses the documented pool-scoped requests."""

    sdk = object()

    def __init__(self, children=(), allocations=()):
        self.parent = sdk_pool()
        self.children, self.allocations = children, allocations

    def call(self, method, request):
        assert method.__name__ == "get"
        return {"pool": self.parent, **{p.metadata.id: p for p in self.children}}[request.id]

    def pages(self, method, factory, **kwargs):
        assert factory("next").pool_id == "pool"
        assert factory("next").page_token == "next"
        return self.children if method.__name__ == "list_by_source_pool" else self.allocations


def test_scoped_inventory_counts_unattached_child_and_dynamic_subnetless_allocation():
    child = sdk_pool("child", "10.0.0.0/25", source="pool")
    child.status.assignment.subnets = []
    allocation = NS(
        metadata=NS(id="dynamic"),
        status=NS(details=NS(pool_id="pool", version="IPV4", allocated_cidr="10.0.0.128/32")),
    )
    result = read_address_pool(InventorySDK([child], [allocation]), "pool")
    report = assess_address_space((result,), (demand("/25"),))
    assert report.status == "insufficient"
    assert report.capacities[0].free_addresses == 127


def test_disabled_pool_block_and_pending_child_are_not_free():
    sdk = InventorySDK()
    sdk.parent = sdk_pool(state="DISABLED")
    assert (
        assess_address_space((read_address_pool(sdk, "pool"),), (demand(),)).status
        == "insufficient"
    )
    sdk = InventorySDK([sdk_pool("child", "/28", source="pool")])
    sdk.children[0].status.cidrs = []
    with pytest.raises(AddressSpaceUnresolved):
        read_address_pool(sdk, "pool")


def test_ambiguous_resolved_block_constraints_are_unresolved():
    sdk = InventorySDK()
    sdk.parent.spec.cidrs = [
        NS(cidr="/24", state="AVAILABLE", max_mask_length=32),
        NS(cidr="/24", state="DISABLED", max_mask_length=32),
    ]
    with pytest.raises(AddressSpaceUnresolved, match="ambiguous"):
        read_address_pool(sdk, "pool")


class PlanSDK:
    pools = {"pool": pool("10.0.0.0/24")}
    networks = {"network-test": ("pool",)}

    def __init__(self, project):
        assert project == "project-test"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def network(self, identity):
        return NS(spec=NS(ipv4_private_pools=NS(pools=[NS(id=p) for p in self.networks[identity]])))

    def address_pool(self, identity, **kwargs):
        return self.pools[identity]


def change(address, kind, after, *, before=None, actions=None, unknown=None):
    return {
        "address": address,
        "type": "nebius_vpc_v1_" + kind,
        "mode": "managed",
        "change": {
            "actions": actions or ["create"],
            "after": after,
            "before": before,
            "after_unknown": unknown or {},
        },
    }


def subnet_change(address="module.nat.nebius_vpc_v1_subnet.gateway", cidr="/28", **kwargs):
    return change(
        address,
        "subnet",
        {
            "parent_id": "project-test",
            "network_id": "network-test",
            "ipv4_private_pools": {
                "use_network_pools": False,
                "pools": [{"cidrs": [{"cidr": cidr}]}],
            },
        },
        **kwargs,
    )


def assessment(resources, payload=None, modules=None, sdk=PlanSDK):
    return assess_plan_address_space(
        payload or config(),
        {"resource_changes": resources},
        modules or {("nat-gateway", "nat"): "nat"},
        sdk_factory=sdk,
    )


def test_fresh_plan_blocks_exhaustion_and_inventory_failure(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool(occupied=("10.0.0.0/28",))})
    report = assessment([subnet_change()])
    assert report.status == "insufficient"
    with pytest.raises(RuntimeError, match="then replan"):
        report.require_feasible()

    def denied(*args, **kwargs):
        raise RuntimeError("inventory denied")

    monkeypatch.setattr(PlanSDK, "address_pool", denied)
    assert assessment([subnet_change()]).status == "unresolved"


def test_each_admission_refreshes_inventory(monkeypatch):
    assert assessment([subnet_change()]).status == "feasible"
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool(occupied=("10.0.0.0/28",))})
    assert assessment([subnet_change()]).status == "insufficient"


@pytest.mark.parametrize("kind", ["retained", "vm", "restore", "remove"])
def test_no_spare_capacity_or_inventory_required_without_new_reservations(kind):
    resource = subnet_change(actions=["no-op"])
    if kind == "vm":
        resource["type"] = "nebius_compute_v1_instance"
        resource["change"]["actions"] = ["delete", "create"]
        resource["change"]["after"] = {
            "parent_id": "project-test",
            "network_interfaces": [
                {"subnet_id": "gateway", "ip_address": {"allocation_id": "stable"}}
            ],
        }
    elif kind == "restore":
        resource["change"]["before"] = copy.deepcopy(resource["change"]["after"])
        resource["change"]["after"]["route_table_id"] = "original"
        resource["change"]["actions"] = ["update"]
    elif kind == "remove":
        resource["change"]["actions"] = ["delete"]
    assert (
        assessment([resource], sdk=lambda _: pytest.fail("Unnecessary inventory")).status
        == "feasible"
    )


def test_partial_apply_counts_existing_child_once_and_no_pending_delete_credit(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/27", ("10.0.0.0/28",))})
    retained = subnet_change("module.old.subnet", "10.0.0.0/28", actions=["no-op"])
    assert assessment([retained, subnet_change()]).status == "feasible"
    retained["change"]["actions"] = ["delete", "create"]
    assert assessment([retained, subnet_change()]).status == "insufficient"


def test_multiple_gateways_and_vpc_workload_share_capacity(monkeypatch):
    payload = config(owner="terraform")
    payload["infra"]["components"][1]["inputs"]["subnets"]["work"]["ipv4_private_cidrs"] = [
        "10.0.0.0/28"
    ]
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/27")})
    # Feasible, but auto NAT allocation could still race the exact workload at apply.
    resources = [subnet_change(), subnet_change("module.vpc.work", "10.0.0.0/28")]
    modules = {("nat-gateway", "nat"): "nat", ("vpc", "network"): "vpc"}
    assert assessment(resources, payload, modules).status == "feasible"
    resources.append(subnet_change("module.another.subnet"))
    assert assessment(resources, payload, modules).status == "insufficient"


def test_new_network_symbolic_ids_and_parent_pool_do_not_double_count():
    payload = config(owner="terraform")
    nat, vpc = payload["infra"]["components"]
    nat["inputs"].pop("network_id")
    nat["bindings"] = {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "network",
            "source_output": "network_id",
        }
    }
    vpc["inputs"]["network"] = {"ipv4_private_cidrs": ["10.0.0.0/27"]}
    vpc["inputs"]["subnets"]["work"]["ipv4_private_cidrs"] = ["10.0.0.0/28"]
    report = assess_config_address_space(
        payload, sdk_factory=lambda _: pytest.fail("No live source")
    )
    assert report.status == "feasible"
    vpc["inputs"]["subnets"]["work"]["ipv4_private_cidrs"] = ["10.0.0.0/27"]
    assert assess_config_address_space(payload).status == "insufficient"


def test_new_network_derived_pool_checks_live_source_occupancy(monkeypatch):
    payload = config(owner="terraform")
    nat, vpc = payload["infra"]["components"]
    nat["inputs"].pop("network_id")
    nat["bindings"] = {"inputs.network_id": {"source_instance": "network"}}
    vpc["inputs"]["network"] = {
        "ipv4_private_cidrs": ["10.0.0.0/24"],
        "ipv4_private_source_pool_id": "pool",
    }
    vpc["inputs"]["subnets"] = {}
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/24", ("10.0.0.0/32",))})
    assert assess_config_address_space(payload, sdk_factory=PlanSDK).status == "insufficient"


def test_realized_gateway_prefix_pool_and_host_are_verified():
    sdk = InventorySDK([sdk_pool("child", "10.0.0.0/28", source="pool")])
    sdk.network = lambda _: NS(spec=NS(ipv4_private_pools=NS(pools=[NS(id="pool")])))
    subnet = NS(
        metadata=NS(id="gateway"),
        status=NS(ipv4_private_pools=[NS(pool_id="child", cidrs=["10.0.0.0/28"])]),
    )
    allocation = NS(
        status=NS(
            details=NS(
                pool_id="child", subnet_id="gateway", version="IPV4", allocated_cidr="10.0.0.1/32"
            )
        )
    )
    inputs = {"network_id": "network-test", "gateway_subnet_cidr": "/28"}
    verify_gateway_address(sdk, subnet, inputs, allocation)
    inputs["gateway_subnet_cidr"] = "/27"
    with pytest.raises(RuntimeError, match="requested CIDR"):
        verify_gateway_address(sdk, subnet, inputs, allocation)
    inputs["gateway_subnet_cidr"] = "/28"
    allocation.status.details.allocated_cidr = "10.0.0.30/32"
    with pytest.raises(RuntimeError, match="outside"):
        verify_gateway_address(sdk, subnet, inputs, allocation)


def test_pending_concrete_child_expansion_is_occupied():
    child = sdk_pool("child", "10.0.0.0/28", source="pool")
    child.spec.cidrs.append(NS(cidr="10.0.0.16/28"))
    sdk = InventorySDK([child])
    sdk.parent = sdk_pool(cidr="10.0.0.0/27")
    report = assess_address_space((read_address_pool(sdk, "pool"),), (demand(),))
    assert report.status == "insufficient"
    child.spec.cidrs[-1].cidr = "/28"
    with pytest.raises(AddressSpaceUnresolved, match="pending prefix"):
        read_address_pool(sdk, "pool")


def test_pool_snapshot_forwards_shared_deadline_and_failed_page_is_not_empty():
    sdk = InventorySDK()

    def fail(method, factory, *, deadline):
        assert deadline == 123.0
        raise RuntimeError("second inventory page unavailable")

    sdk.pages = fail
    with pytest.raises(RuntimeError, match="second inventory page"):
        read_address_pool(sdk, "pool", deadline=123.0)


def test_planned_network_pool_membership_replaces_live_membership(monkeypatch):
    monkeypatch.setattr(
        PlanSDK,
        "pools",
        {
            "pool": pool("10.0.0.0/24"),
            "exhausted": pool(occupied=("10.0.0.0/28",), identity="exhausted"),
        },
    )
    network = change(
        "module.other.network",
        "network",
        {
            "id": "network-test",
            "parent_id": "project-test",
            "ipv4_private_pools": {"pools": [{"id": "exhausted"}]},
        },
        before={"id": "network-test"},
        actions=["update"],
    )
    assert assessment([network, subnet_change()]).status == "insufficient"
    network["change"]["after_unknown"] = {"ipv4_private_pools": {"pools": [{"id": True}]}}
    assert assessment([network, subnet_change()]).status == "unresolved"


@pytest.mark.parametrize("field", ["state", "max_mask_length", "cidr"])
def test_unknown_planned_pool_controls_never_become_permissive_defaults(field):
    values = {
        "id": "pool",
        "visibility": "PRIVATE",
        "version": "IPV4",
        "cidrs": [{"cidr": "10.0.0.0/24", "state": "AVAILABLE", "max_mask_length": 32}],
    }
    before = copy.deepcopy(values)
    before["cidrs"][0]["state"] = "DISABLED"
    values["cidrs"][0][field] = None
    resource = change(
        "module.other.pool",
        "pool",
        values,
        before=before,
        actions=["update"],
        unknown={"cidrs": [{field: True}]},
    )
    assert assessment([resource, subnet_change()]).status == "unresolved"


def test_unrelated_public_or_private_changes_do_not_read_nat_capacity(monkeypatch):
    public = change(
        "module.other.public", "allocation", {"ipv4_public": {"subnet_id": "elsewhere"}}
    )
    assert (
        assessment([public], sdk=lambda _: pytest.fail("No private capacity demand")).status
        == "feasible"
    )
    monkeypatch.setattr(
        PlanSDK, "networks", {"network-test": ("pool",), "elsewhere": ("other-pool",)}
    )
    monkeypatch.setattr(
        PlanSDK, "address_pool", lambda *a, **k: pytest.fail("No affected pool demand")
    )
    resource = subnet_change("module.other.subnet")
    resource["change"]["after"]["network_id"] = "elsewhere"
    assert assessment([resource]).status == "feasible"


def test_in_place_expansion_checks_delta_but_replacement_needs_full_range(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/27", ("10.0.0.0/28",))})
    original = subnet_change(cidr="10.0.0.0/28")["change"]["after"]
    resource = subnet_change(cidr="10.0.0.0/27", before=original, actions=["update"])
    assert assessment([resource]).status == "feasible"
    resource["change"]["actions"] = ["delete", "create"]
    assert assessment([resource]).status == "insufficient"


def test_repeated_prefix_expansion_is_not_treated_as_retained():
    before = subnet_change()["change"]["after"]
    resource = subnet_change(actions=["update"], before=copy.deepcopy(before))
    resource["change"]["after"]["ipv4_private_pools"]["pools"][0]["cidrs"].append({"cidr": "/28"})
    assert assessment([resource]).status == "unresolved"


@pytest.mark.parametrize("parent_full", [False, True])
def test_selected_child_pool_expansion_checks_its_parent(monkeypatch, parent_full):
    occupied = ("10.0.0.0/28", "10.0.0.16/28") if parent_full else ("10.0.0.0/28",)
    monkeypatch.setattr(
        PlanSDK,
        "pools",
        {
            "pool": pool(occupied=("10.0.0.0/28",)),
            "parent": pool("10.0.0.0/27", occupied, identity="parent"),
        },
    )
    before = {
        "id": "pool",
        "parent_id": "project-test",
        "visibility": "PRIVATE",
        "version": "IPV4",
        "source_pool_id": "parent",
        "cidrs": [{"cidr": "10.0.0.0/28"}],
    }
    after = {**before, "cidrs": [{"cidr": "10.0.0.0/27"}]}
    expansion = change("module.other.pool", "pool", after, before=before, actions=["update"])
    assert assessment([expansion, subnet_change()]).status == (
        "insufficient" if parent_full else "feasible"
    )


@pytest.mark.parametrize("upstream_full", [False, True])
def test_pool_expansion_checks_multiple_ancestor_levels(monkeypatch, upstream_full):
    upstream_occupied = ("10.0.0.0/27", "10.0.0.32/27") if upstream_full else ("10.0.0.0/27",)
    monkeypatch.setattr(
        PlanSDK,
        "pools",
        {
            "pool": pool(occupied=("10.0.0.0/28",)),
            "parent": pool("10.0.0.0/27", ("10.0.0.0/28",), identity="parent"),
            "upstream": pool("10.0.0.0/26", upstream_occupied, identity="upstream"),
        },
    )
    expansions = []
    for identity, source, previous in (("parent", "upstream", "/27"), ("pool", "parent", "/28")):
        before = {
            "id": identity,
            "parent_id": "project-test",
            "visibility": "PRIVATE",
            "version": "IPV4",
            "source_pool_id": source,
            "cidrs": [{"cidr": "10.0.0.0" + previous}],
        }
        after = {**before, "cidrs": [{"cidr": "10.0.0.0/26"}]}
        expansions.append(
            change("module.other." + identity, "pool", after, before=before, actions=["update"])
        )
    assert assessment([*expansions, subnet_change()]).status == (
        "insufficient" if upstream_full else "feasible"
    )


def test_direct_pool_allocation_competes_with_subnet(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool()})
    allocation = change("module.other.private", "allocation", {"ipv4_private": {"pool_id": "pool"}})
    assert assessment([subnet_change(), allocation]).status == "insufficient"
    allocation["change"]["after_unknown"] = {"ipv4_private": {"cidr": True}}
    assert assessment([subnet_change(), allocation]).status == "unresolved"


def test_nat_private_host_is_inside_new_child_reservation(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool()})
    allocation = change(
        "module.nat.nebius_vpc_v1_allocation.private",
        "allocation",
        {"ipv4_private": {"subnet_id": None}},
        unknown={"ipv4_private": {"subnet_id": True}},
    )
    assert assessment([subnet_change(), allocation]).status == "feasible"


@pytest.mark.parametrize("status", ["insufficient", "unresolved"])
def test_wizard_preserves_well_formed_draft_after_capacity_failure(monkeypatch, status):
    from nebius_cxcli import nat_gateway_wizard as wizard
    from test_nat_gateway_wizard_quota import Lookup, WizardSDK, prompt

    emitted = []

    def capacity(self, identity, **kwargs):
        if status == "unresolved":
            raise RuntimeError("pool inventory denied")
        return pool(occupied=("10.0.0.0/28",), identity=identity)

    monkeypatch.setattr(WizardSDK, "address_pool", capacity)
    monkeypatch.setattr(wizard, "assess_live_quotas", lambda *a, **kw: None)
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: [])
    payload = config()
    result = wizard.configure_nat(
        payload,
        payload["infra"]["components"][0],
        prefix="nat",
        lookup=Lookup(),
        prompt=prompt,
        is_back=lambda _: False,
        emit=emitted.append,
        sdk_factory=WizardSDK,
    )
    assert result["inputs"]["gateway_subnet_cidr"] == "/28"
    assert any(f"address space {status}" in line for line in emitted)
    assert any("draft can be saved" in line for line in emitted)


def test_new_gateway_with_routing_disabled_still_hits_plan_gate(monkeypatch):
    from nebius_cxcli import nat_gateway_sdk
    from test_nat_gateway_alignment import prepared_manager

    manager, _, _, _, _, _ = prepared_manager(monkeypatch)
    monkeypatch.setattr(nat_gateway_sdk, "NatGatewaySDK", PlanSDK)
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool(occupied=("10.0.0.0/28",))})
    with pytest.raises(RuntimeError, match="NAT address space insufficient"):
        manager.check_plan({"resource_changes": [subnet_change()]})


def test_prepare_capacity_is_advisory_and_restoration_skips_it(monkeypatch):
    from test_nat_gateway_alignment import prepared_manager

    manager, sdk, _, _, _, _ = prepared_manager(monkeypatch)
    emitted = []
    manager.executor.cli.progress_console.print = lambda message, **kw: emitted.append(message)
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("pool inventory denied")

    monkeypatch.setattr(type(sdk), "address_pool", denied)
    manager.prepare()  # An unchanged disabled gateway is a recovery operation.
    assert not calls
    manager.prepared = False
    manager.state.value["gateways"] = {}
    manager.prepare()  # A new gateway reports the problem; check_plan owns admission.
    assert calls
    assert any("address space unresolved" in message for message in emitted)
    assert manager.prepared


def test_inherited_subnet_host_allocation_competes_in_parent(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/27", ("10.0.0.16/28",))})
    inherited = subnet_change("module.other.inherited", actions=["no-op"])
    inherited["change"]["after"].update(
        id="inherited", ipv4_private_pools={"use_network_pools": True}
    )
    allocation = change(
        "module.other.private", "allocation", {"ipv4_private": {"subnet_id": "inherited"}}
    )
    assert assessment([inherited, subnet_change(), allocation]).status == "insufficient"
    inherited["change"]["after"]["ipv4_private_pools"] = {
        "use_network_pools": False,
        "pools": [{"cidrs": [{"cidr": "10.0.0.16/28"}]}],
    }
    assert assessment([inherited, subnet_change(), allocation]).status == "feasible"


@pytest.mark.parametrize("mode", ["omitted", "empty", "dedicated"])
def test_sdk_default_pool_fields_do_not_prove_dedicated_isolation(monkeypatch, mode):
    from nebius.api.nebius.vpc.v1 import IPv4PrivateSubnetPools, SubnetCidr, SubnetPool, SubnetSpec

    spec = SubnetSpec(network_id="network-test")
    if mode != "omitted":
        spec.ipv4_private_pools = IPv4PrivateSubnetPools()
    if mode == "dedicated":
        spec.ipv4_private_pools.pools.append(SubnetPool(cidrs=[SubnetCidr(cidr="10.0.0.16/28")]))
    assert spec.ipv4_private_pools.use_network_pools is False

    class SDK(PlanSDK):
        sdk = object()

        def call(self, method, request):
            assert request.id == "work"
            return NS(metadata=NS(id="work", parent_id="project-test"), spec=spec)

    monkeypatch.setattr(SDK, "pools", {"pool": pool("10.0.0.0/27", ("10.0.0.16/28",))})
    allocation = change(
        "module.other.private",
        "allocation",
        {
            "parent_id": "project-test",
            "ipv4_private": {"subnet_id": "work"},
        },
    )
    report = assessment([subnet_change(), allocation], sdk=SDK)
    assert (
        report.status
        == {"omitted": "insufficient", "empty": "unresolved", "dedicated": "feasible"}[mode]
    )


@pytest.mark.parametrize("unknown", [False, True])
def test_planned_subnet_requires_positive_pool_evidence(unknown):
    subnet = subnet_change("module.work.subnet", actions=["no-op"])
    subnet["change"]["after"]["id"] = "work"
    if unknown:
        subnet["change"]["after_unknown"] = {"ipv4_private_pools": {"use_network_pools": True}}
    else:
        subnet["change"]["after"]["ipv4_private_pools"] = {"use_network_pools": False, "pools": []}
    allocation = change(
        "module.other.private", "allocation", {"ipv4_private": {"subnet_id": "work"}}
    )
    assert assessment([subnet_change(), subnet, allocation]).status == "unresolved"


@pytest.mark.parametrize("kind", ["vm", "cluster", "node_group"])
@pytest.mark.parametrize("inherited", [True, False])
def test_implicit_compute_demand_respects_subnet_isolation(monkeypatch, kind, inherited):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/27", ("10.0.0.16/28",))})
    subnet = subnet_change("module.work.subnet", actions=["no-op"])
    subnet["change"]["after"].update(id="work", ipv4_private_pools={"use_network_pools": inherited})
    if not inherited:
        subnet["change"]["after"]["ipv4_private_pools"]["pools"] = [
            {"cidrs": [{"cidr": "10.0.0.16/28"}]}
        ]
    workload = change("module.work.resource", "unused", {"parent_id": "project-test"})
    values = workload["change"]["after"]
    payload = config()
    modules = {("nat-gateway", "nat"): "nat"}
    if kind == "vm":
        workload["type"] = "nebius_compute_v1_instance"
        values["network_interfaces"] = [{"subnet_id": "work", "ip_address": {}}]
    elif kind == "cluster":
        workload["type"] = "nebius_mk8s_v1_cluster"
        values["control_plane"] = {"subnet_id": "work"}
    else:
        workload["type"] = "nebius_mk8s_v1_node_group"
        values.update(parent_id="cluster", template={"network_interfaces": [{"subnet_id": "work"}]})
        payload["infra"]["components"].append(
            {
                "id": "mk8s",
                "instance_id": "work",
                "enabled": True,
                "inputs": {"cluster": {"parent_id": "project-test"}},
            }
        )
        modules[("mk8s", "work")] = "work"
    report = assessment([subnet, subnet_change(), workload], payload, modules)
    assert report.status == (
        "feasible" if not inherited else "insufficient" if kind == "vm" else "unresolved"
    )
    if kind != "vm" and inherited:
        assert "Implicit private-address demand" in report.reason


def test_nat_vm_unknown_stable_allocation_does_not_double_count(monkeypatch):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool()})
    vm = change(
        "module.nat.module.vm.nebius_compute_v1_instance.vm",
        "unused",
        {
            "parent_id": "project-test",
            "network_interfaces": [{"subnet_id": None, "ip_address": {"allocation_id": None}}],
        },
    )
    vm["type"] = "nebius_compute_v1_instance"
    assert assessment([subnet_change(), vm]).status == "feasible"


@pytest.mark.parametrize("kind", ["vm", "cluster", "group_default", "group_override"])
def test_new_workload_symbolic_explicit_subnet_binding(monkeypatch, kind):
    monkeypatch.setattr(PlanSDK, "pools", {"pool": pool("10.0.0.0/27")})
    payload = config(owner="terraform")
    subnet = subnet_change('module.vpc.nebius_vpc_v1_subnet.this["work"]', "10.0.0.16/28")
    subnet["index"] = "work"
    row = {
        "id": "vm" if kind == "vm" else "mk8s",
        "instance_id": "workload",
        "enabled": True,
        "inputs": {
            "parent_id": "project-test",
            "cluster": {"parent_id": "project-test"},
            "node_groups": {"cpu": {}},
        },
    }
    binding = {
        "source_component": "vpc",
        "source_instance": "network",
        "source_output": "subnets",
        "key": "work",
        "attribute": "id",
    }
    path = "inputs.subnet_id" if kind == "vm" else "inputs.cluster.subnet_id"
    if kind == "group_override":
        path = "inputs.node_groups.cpu.subnet_id"
    row["bindings"] = {path: binding}
    payload["infra"]["components"].append(row)
    modules = {
        ("nat-gateway", "nat"): "nat",
        ("vpc", "network"): "vpc",
        (row["id"], "workload"): "workload",
    }
    workload = change("unused", "unused", {"parent_id": "project-test"})
    if kind == "vm":
        workload.update(
            type="nebius_compute_v1_instance",
            address="module.workload.nebius_compute_v1_instance.vm",
        )
        workload["change"]["after"]["network_interfaces"] = [{"subnet_id": None, "ip_address": {}}]
    elif kind == "cluster":
        workload.update(
            type="nebius_mk8s_v1_cluster", address="module.workload.nebius_mk8s_v1_cluster.this"
        )
        workload["change"]["after"]["control_plane"] = {"subnet_id": None}
    else:
        workload.update(
            type="nebius_mk8s_v1_node_group",
            address='module.workload.nebius_mk8s_v1_node_group.this["cpu"]',
            index="cpu",
        )
        workload["change"]["after"]["template"] = {"network_interfaces": [{"subnet_id": None}]}
    assert assessment([subnet_change(), subnet, workload], payload, modules).status == "feasible"
    row["bindings"][path]["key"] = "unresolved"
    assert assessment([subnet_change(), subnet, workload], payload, modules).status == "unresolved"


def test_vm_nic_security_update_retains_dynamic_allocation():
    values = {
        "network_interfaces": [
            {"name": "eth0", "subnet_id": "work", "ip_address": {}, "security_groups": []}
        ]
    }
    after = copy.deepcopy(values)
    after["network_interfaces"][0]["security_groups"] = [{"id": "updated"}]
    vm = change("module.work.vm", "unused", after, before=values, actions=["update"])
    vm["type"] = "nebius_compute_v1_instance"
    assert (
        assessment([vm], sdk=lambda _: pytest.fail("Retained address needs no inventory")).status
        == "feasible"
    )
