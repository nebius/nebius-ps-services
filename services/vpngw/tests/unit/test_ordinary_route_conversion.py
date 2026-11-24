from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.common import v1 as common
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy.ordinary_route_conversion import convert, copy_inventory
from nebius_vpngw.deploy.ordinary_route_execute import execute
from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal
from nebius_vpngw.deploy.ordinary_route_plan import inspect, request_record
from nebius_vpngw.deploy.ordinary_route_policy import Connection, Policy
from nebius_vpngw.deploy.replacement_cloud import message_digest
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message as clone


@pytest.fixture
def copying(tmp_path, monkeypatch, request):
    def meta(identifier, parent="project", name=None, labels=None):
        return common.ResourceMetadata(
            id=identifier,
            parent_id=parent,
            name=name or identifier,
            labels=labels or {},
            resource_version=1,
        )

    source = vpc.RouteTable(metadata=meta("source"), spec=vpc.RouteTableSpec(network_id="network"))
    subnets = {
        name: vpc.Subnet(
            metadata=meta(name), spec=vpc.SubnetSpec(network_id="network", route_table_id="source")
        )
        for name in ("selected", "unrelated")
    }
    foreign = vpc.Route(
        metadata=meta("foreign", "source", "vpngw-customer-route", {"customer": "keep"}),
        spec=vpc.RouteSpec(
            destination=vpc.DestinationMatch(cidr="172.16.0.0/16"),
            next_hop=vpc.NextHop(allocation=vpc.AllocationNextHop(id="customer-next-hop")),
        ),
    )
    if getattr(request, "param", None) == "default-egress":
        foreign.spec.destination.cidr = "0.0.0.0/0"
        foreign.spec.next_hop = vpc.NextHop(default_egress_gateway=True)
    if getattr(request, "param", None) == "opaque":
        subnets["selected"].metadata.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
        subnets["selected"].spec.__pb2_message__.MergeFromString(b"\xf8\x07\x02")
        foreign.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x03")
    state = NS(
        tables={"source": source},
        subnets=subnets,
        routes={"foreign": foreign},
        operations={},
        effects=[],
        lose=None,
        before=None,
    )
    journal = RouteJournal("project", "gateway", root=tmp_path / "routes")
    policy = Policy(
        (Connection("static", "static", 0, (), ("192.168.1.0/24",), 1),), ("10.0.0.0/16",)
    )

    def observe(*, completed=False):
        transaction = journal.transaction()
        table_ids = {state.subnets["selected"].spec.route_table_id} | {
            item["snapshot"]["parent_id"] for item in journal.inventory().values()
        }
        if transaction and (transaction["phase"] != "complete" or completed):
            table_ids.update(transaction["intent"]["infrastructure"]["table_ids"])
            table_ids.update(
                item["resource_id"]
                for item in transaction["operations"].values()
                if item["request_type"] == "CreateRouteTableRequest" and item["status"] == "settled"
            )
        return {
            "infrastructure": {
                "project": "project",
                "network_id": "network",
                "table_ids": sorted(table_ids),
                "tables": {
                    identifier: {
                        "spec_sha256": message_digest(state.tables[identifier].spec),
                        "default": identifier == "source",
                        "subnets": sorted(
                            name
                            for name, subnet in state.subnets.items()
                            if subnet.spec.route_table_id == identifier
                        ),
                    }
                    for identifier in table_ids
                },
                "targets": [
                    {
                        "subnet_id": "selected",
                        "table_id": state.subnets["selected"].spec.route_table_id,
                        "spec_sha256": message_digest(state.subnets["selected"].spec),
                    }
                ],
                "gateways": {"0": {"compute_id": "compute", "allocation_id": "allocation"}},
            },
            "allocations": {0: "allocation"},
            "routes": {
                identifier: tuple(
                    clone(route)
                    for route in state.routes.values()
                    if route.metadata.parent_id == identifier
                )
                for identifier in table_ids
            },
            "table_resources": {
                identifier: clone(state.tables[identifier]) for identifier in table_ids
            },
            "subnet_resources": {name: clone(subnet) for name, subnet in state.subnets.items()},
        }

    def mutate(request, **kwargs):
        key = dict(kwargs["metadata"])["x-idempotency-key"]
        kind = type(request).__name__
        if key not in state.operations:
            if state.before:
                state.before(kind)
            identifier = "created-" + str(len(state.effects))
            if kind == "CreateRouteTableRequest":
                state.tables[identifier] = vpc.RouteTable(
                    metadata=meta(
                        identifier,
                        request.metadata.parent_id,
                        request.metadata.name,
                        dict(request.metadata.labels),
                    ),
                    spec=clone(request.spec),
                )
            elif kind == "CreateRouteRequest":
                assert not any(
                    r.metadata.parent_id == request.metadata.parent_id
                    and r.spec.destination.cidr == request.spec.destination.cidr
                    for r in state.routes.values()
                )
                state.routes[identifier] = vpc.Route(
                    metadata=meta(
                        identifier,
                        request.metadata.parent_id,
                        request.metadata.name,
                        dict(request.metadata.labels),
                    ),
                    spec=clone(request.spec),
                )
            elif kind == "UpdateSubnetRequest":
                identifier = request.metadata.id
                assert message_digest(request.metadata) == message_digest(
                    state.subnets[identifier].metadata
                )
                assert (
                    request.metadata.resource_version
                    == state.subnets[identifier].metadata.resource_version
                )
                state.subnets[identifier].spec = clone(request.spec)
                state.subnets[identifier].metadata.resource_version += 1
            elif kind == "DeleteRouteRequest":
                identifier = request.id
                del state.routes[identifier]
            else:
                raise AssertionError(kind)
            state.effects.append((kind, identifier))
            state.operations[key] = NS(
                id="op-" + str(len(state.effects)),
                resource_id=identifier,
                successful=lambda: True,
                sync_wait=lambda **kwargs: None,
            )

        def wait():
            if state.lose == kind:
                state.lose = None
                raise TimeoutError("accepted response lost")
            return state.operations[key]

        return NS(wait=wait)

    def operation_service():
        return NS(
            get=lambda request, **kwargs: NS(
                wait=lambda: next(op for op in state.operations.values() if op.id == request.id)
            )
        )

    def get_table(request, **kwargs):
        table = clone(state.tables[request.id])
        table.status.assignment = vpc.RouteTableAssignment(
            subnets=[
                key
                for key, subnet in state.subnets.items()
                if subnet.spec.route_table_id == request.id
            ]
        )
        return NS(wait=lambda: table)

    def get_subnet(request, **kwargs):
        subnet = clone(state.subnets[request.id])
        subnet.status.route_table.id = subnet.spec.route_table_id
        return NS(wait=lambda: subnet)

    monkeypatch.setattr(
        vpc,
        "RouteTableServiceClient",
        lambda sdk: NS(create=mutate, get=get_table, operation_service=operation_service),
    )
    monkeypatch.setattr(
        vpc,
        "RouteServiceClient",
        lambda sdk: NS(
            create=mutate,
            delete=mutate,
            get=lambda request, **kwargs: NS(wait=lambda: clone(state.routes[request.id])),
            operation_service=operation_service,
        ),
    )
    monkeypatch.setattr(
        vpc,
        "SubnetServiceClient",
        lambda sdk: NS(update=mutate, get=get_subnet, operation_service=operation_service),
    )
    observation = observe()
    plan = inspect(
        project="project",
        gateway="gateway",
        policy=policy,
        learned={},
        allocations={0: "allocation"},
        active_tables=("source",),
        routes=observation["routes"],
        ownership={},
        infrastructure=observation["infrastructure"],
    )
    plan = convert(
        plan,
        {
            "source": {
                "source": "source",
                "routes": copy_inventory(observation["routes"]["source"]),
                "subnets": {
                    "selected": request_record(
                        vpc.UpdateSubnetRequest(
                            metadata=clone(subnets["selected"].metadata),
                            spec=clone(subnets["selected"].spec),
                        )
                    )
                },
            }
        },
    )
    return state, journal, plan, observe


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
def test_enforced_default_displaces_only_the_private_copy(copying):
    state, journal, original, observe = copying
    observed = observe()
    observed["infrastructure"]["enforced_sources"] = {"peer": ["selected"]}
    policy = Policy(
        (Connection("peer", "bgp", 0, ("169.254.1.2",), ("0.0.0.0/0",), 1),),
        ("10.0.0.0/16",),
        enforced=(
            {
                "connection": "peer",
                "source_subnets": ["10.0.1.0/24"],
                "destinations": ["0.0.0.0/0"],
            },
        ),
    )
    plan = inspect(
        project="project",
        gateway="gateway",
        policy=policy,
        learned={"peer": ()},
        allocations={0: "allocation"},
        active_tables=("source",),
        routes=observed["routes"],
        ownership={},
        infrastructure=observed["infrastructure"],
        private_copies=("selected",),
    )
    result = convert(plan, original["conversions"])
    routes = [r for r in result["requests"].values() if r["type"] == "CreateRouteRequest"]
    assert len(routes) == 1
    assert routes[0]["request"]["spec"]["next_hop"] == {"allocation": {"id": "allocation"}}
    assert routes[0]["table_source"] == "source"
    assert state.routes["foreign"].spec.next_hop.default_egress_gateway
    assert result["conversions"]["source"]["routes"]["foreign"]["request"]["spec"]["next_hop"][
        "default_egress_gateway"
    ]
    assert not any(r["type"] == "DeleteRouteRequest" for r in result["requests"].values())


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
@pytest.mark.parametrize("drift", [False, True])
def test_displaced_egress_survives_generations_and_restores_only_with_exact_proof(copying, drift):
    from nebius_vpngw.deploy.enforced_predecessors import augment

    state, journal, original, observe = copying
    enabled = True
    policy = Policy(
        (Connection("peer", "bgp", 0, ("169.254.1.2",), ("0.0.0.0/0",), 1),),
        ("10.0.0.0/16",),
        enforced=(
            {
                "connection": "peer",
                "source_subnets": ["10.0.1.0/24"],
                "destinations": ["0.0.0.0/0"],
            },
        ),
    )

    def observation():
        result = observe()
        if enabled:
            result["infrastructure"]["enforced_sources"] = {"peer": ["selected"]}
        return result

    first = observation()
    plan = inspect(
        project="project",
        gateway="gateway",
        policy=policy,
        learned={"peer": ()},
        allocations={0: "allocation"},
        active_tables=("source",),
        routes=first["routes"],
        ownership={},
        infrastructure=first["infrastructure"],
        private_copies=("selected",),
    )
    plan = augment(convert(plan, original["conversions"]), first, {})
    execute(object(), journal, plan, approval="a" * 64, observe=observation)
    private = state.subnets["selected"].spec.route_table_id
    assert private != "source" and state.subnets["unrelated"].spec.route_table_id == "source"
    predecessors = journal.enforced_predecessors()
    assert len(predecessors) == 1 and next(iter(predecessors.values()))["table_id"] == private
    assert state.routes["foreign"].spec.next_hop.default_egress_gateway
    enabled = False
    observed = observation()
    removed = inspect(
        project="project",
        gateway="gateway",
        policy=Policy((), ("10.0.0.0/16",)),
        learned={},
        allocations={0: "allocation"},
        active_tables=(private,),
        routes=observed["routes"],
        ownership=journal.inventory(),
        infrastructure=observed["infrastructure"],
    )
    removed = augment(removed, observed, predecessors)
    if drift:
        state.routes["foreign"].metadata.name = "changed-by-customer"
        before = list(state.effects)
        with pytest.raises(RuntimeError, match="predecessor changed"):
            execute(object(), journal, removed, approval="b" * 64, observe=observation)
        assert state.effects == before and journal.enforced_predecessors() == predecessors
    else:
        execute(object(), journal, removed, approval="b" * 64, observe=observation)
        assert not journal.enforced_predecessors()
        routes = [r for r in state.routes.values() if r.metadata.parent_id == private]
        assert len(routes) == 1 and routes[0].spec.next_hop.default_egress_gateway


@pytest.mark.parametrize("defer_bgp", [True, False])
def test_empty_bgp_selection_still_prepares_private_runtime_table(copying, defer_bgp):
    from nebius_vpngw.deploy.ordinary_route_command import _observe_plan

    state, journal, original, observe = copying
    policy = Policy((Connection("peer", "bgp", 0, (), (), 1),), ("10.0.0.0/16",))
    discovery = NS(journal=journal, force_copy=False, learned=lambda policy: {"peer": ()})
    observation = {
        **observe(),
        "policy": policy,
        "active_tables": ("source",),
        "conversion_requests": original["conversions"],
    }
    plan, unknown = _observe_plan(discovery, observation, defer_bgp=defer_bgp)
    assert not unknown
    assert {item["type"] for item in plan["requests"].values()} == {
        "CreateRouteTableRequest",
        "CreateRouteRequest",
        "UpdateSubnetRequest",
    }
    source = message_digest(state.routes["foreign"])
    execute(object(), journal, plan, approval="a" * 64, observe=observe)
    private_table = state.subnets["selected"].spec.route_table_id
    assert private_table != "source"
    assert state.subnets["unrelated"].spec.route_table_id == "source"
    assert message_digest(state.routes["foreign"]) == source
    assert len(state.effects) == 3


def test_complete_copy_precedes_exact_subnet_attachment_and_source_is_preserved(copying):
    state, journal, plan, observe = copying
    source = message_digest(state.routes["foreign"])
    execute(object(), journal, plan, approval="a" * 64, observe=observe)
    assert [kind for kind, _ in state.effects] == [
        "CreateRouteTableRequest",
        "CreateRouteRequest",
        "CreateRouteRequest",
        "UpdateSubnetRequest",
    ]
    assert message_digest(state.routes["foreign"]) == source
    assert state.subnets["unrelated"].spec.route_table_id == "source"
    table = state.subnets["selected"].spec.route_table_id
    assert table != "source"
    assert {
        r.spec.destination.cidr for r in state.routes.values() if r.metadata.parent_id == table
    } == {"172.16.0.0/16", "192.168.1.0/24"}
    assert len(journal.inventory()) == 1  # The copied customer route is not adopted.
    before = journal.path.read_bytes()
    execute(object(), journal, plan, approval="a" * 64, observe=lambda: observe(completed=True))
    assert journal.path.read_bytes() == before
    assert len(state.effects) == 4


def test_review_names_only_selected_subnet_and_preserved_copy(copying):
    from nebius_vpngw.deploy.ordinary_route_command import PreparedRoutes
    from nebius_vpngw.deploy.ordinary_route_plan import intent

    state, journal, plan, _observe = copying
    prepared = PreparedRoutes(None, intent(plan), plan, True, None, None, False)
    text = "\n".join(prepared.review_lines())
    assert "Static destinations 192.168.1.0/24" in text
    assert "copy route table source to a new private table; preserve unrelated routes" in text
    assert "reassign only subnets selected" in text
    assert text.splitlines()[-1].endswith("reassign only subnets selected")
    assert state.effects == []
    assert not journal.root.exists()


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
def test_default_egress_is_copied_exactly_without_route_ownership(copying):
    state, journal, plan, observe = copying
    original = message_digest(state.routes["foreign"])
    execute(object(), journal, plan, approval="a" * 64, observe=observe)
    table = state.subnets["selected"].spec.route_table_id
    assert table != "source"
    copied = next(
        route
        for route in state.routes.values()
        if route.metadata.parent_id == table and route.spec.destination.cidr == "0.0.0.0/0"
    )
    assert copied.spec.next_hop.default_egress_gateway
    assert copied.spec.next_hop.allocation is None
    assert message_digest(copied.spec) == message_digest(state.routes["foreign"].spec)
    assert message_digest(state.routes["foreign"]) == original
    assert state.subnets["unrelated"].spec.route_table_id == "source"
    assert copied.metadata.id not in journal.inventory()
    before = journal.path.read_bytes()
    effects = deepcopy(state.effects)
    execute(object(), journal, plan, approval="a" * 64, observe=lambda: observe(completed=True))
    assert state.effects == effects and journal.path.read_bytes() == before


@pytest.mark.parametrize(
    "hop", [None, vpc.NextHop(), vpc.NextHop(allocation=vpc.AllocationNextHop())]
)
def test_incomplete_next_hop_cannot_be_copied_or_prove_coverage(hop):
    from nebius_vpngw.deploy.ordinary_route_ownership import allocation_id, snapshot

    route = vpc.Route(
        metadata=common.ResourceMetadata(id="route-a", parent_id="table-a", resource_version=1),
        spec=vpc.RouteSpec(destination=vpc.DestinationMatch(cidr="192.0.2.0/24"), next_hop=hop),
    )
    for observe in (snapshot, allocation_id):
        with pytest.raises(RuntimeError, match="next hop"):
            observe(route)


@pytest.mark.parametrize(
    "point", ["CreateRouteTableRequest", "CreateRouteRequest", "UpdateSubnetRequest"]
)
def test_lost_response_resumes_exact_copy_without_duplicate_or_early_attachment(copying, point):
    state, journal, plan, observe = copying
    state.lose = point
    with pytest.raises(TimeoutError):
        execute(object(), journal, plan, approval="a" * 64, observe=observe)
    before = deepcopy(state.effects)
    execute(object(), journal, plan, approval="a" * 64, observe=observe)
    assert state.effects[: len(before)] == before
    assert len(state.effects) == 4
    assert journal.transaction()["phase"] == "complete"


@pytest.mark.parametrize("copying", ["opaque"], indirect=True)
@pytest.mark.parametrize("point", ["CreateRouteRequest", "UpdateSubnetRequest"])
def test_copy_preserves_opaque_fields_and_original_cas_after_lost_reply(copying, point):
    state, journal, plan, observe = copying
    original = clone(state.subnets["selected"])
    state.lose = point
    with pytest.raises(TimeoutError):
        execute(object(), journal, plan, approval="a" * 64, observe=observe)
    effects = deepcopy(state.effects)
    execute(object(), journal, plan, approval="a" * 64, observe=observe)
    assert state.effects[: len(effects)] == effects
    assert len(state.effects) == 4
    actual = state.subnets["selected"]
    original.spec.route_table_id = actual.spec.route_table_id
    original.metadata.resource_version += 1
    assert message_digest(actual) == message_digest(original)
    copied = next(
        route
        for route in state.routes.values()
        if route.metadata.parent_id == actual.spec.route_table_id
        and route.spec.destination.cidr == "172.16.0.0/16"
    )
    assert message_digest(copied.spec) == message_digest(state.routes["foreign"].spec)
    before = journal.path.read_bytes()
    execute(object(), journal, plan, approval="a" * 64, observe=lambda: observe(completed=True))
    assert journal.path.read_bytes() == before


def test_request_record_preserves_full_approved_protobuf():
    from nebius_vpngw.deploy.ordinary_route_conversion import resolve

    request = vpc.UpdateSubnetRequest(
        metadata=common.ResourceMetadata(id="subnet", parent_id="project", resource_version=7),
        spec=vpc.SubnetSpec(network_id="network", route_table_id="table"),
    )
    request.metadata.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    request.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x02")
    assert message_digest(resolve(request_record(request), {}, {})) == message_digest(request)


@pytest.mark.parametrize("change", ["protobuf", "projection", "hash", "type"])
def test_request_record_rejects_changed_approval_representation(change):
    from nebius_vpngw.deploy.ordinary_route_conversion import resolve

    record = request_record(vpc.DeleteRouteRequest(id="approved"))
    if change == "protobuf":
        record["protobuf"] = "not base64!"
    elif change == "projection":
        record["request"]["id"] = "unapproved"
    elif change == "hash":
        record["sha256"] = "0" * 64
    else:
        record["type"] = "CreateInstanceRequest"
    with pytest.raises(RuntimeError, match="Approved route request"):
        resolve(record, {}, {})


@pytest.mark.parametrize(
    "change", ["source-route", "subnet-revision", "copy-route", "new-table-label"]
)
def test_concurrent_change_blocks_attachment_preserving_unrelated_state(copying, change):
    state, journal, plan, observe = copying
    state.lose = "CreateRouteRequest"
    with pytest.raises(TimeoutError):
        execute(object(), journal, plan, approval="a" * 64, observe=observe)
    if change == "source-route":
        state.routes["foreign"].spec.next_hop.allocation.id = "changed"
    elif change == "subnet-revision":
        state.subnets["selected"].metadata.resource_version += 1
    elif change == "copy-route":
        next(
            r for key, r in state.routes.items() if key != "foreign"
        ).spec.next_hop.allocation.id = "changed"
    else:
        next(t for key, t in state.tables.items() if key != "source").metadata.labels = {
            "foreign": "changed"
        }
    with pytest.raises(RuntimeError):
        execute(object(), journal, plan, approval="a" * 64, observe=observe)
    assert state.subnets["selected"].spec.route_table_id == "source"
    assert state.subnets["unrelated"].spec.route_table_id == "source"


def test_composed_owner_resumes_conversion_then_reapply_is_mutation_free(copying, monkeypatch):
    from nebius_vpngw.deploy import ordinary_route_command as command

    state, journal, plan, read = copying
    policy = Policy(
        (Connection("static", "static", 0, (), ("192.168.1.0/24",), 1),), ("10.0.0.0/16",)
    )
    manager = NS(project_id="project", auth_token=None, _get_client=lambda: object())

    def observe(**_kwargs):
        result = read(**_kwargs)
        target = state.subnets["selected"].spec.route_table_id
        return {
            **result,
            "policy": policy,
            "active_tables": (target,),
            "conversions": [],
            "conversion_requests": plan["conversions"] if target == "source" else {},
        }

    discovery = NS(
        journal=journal,
        manager=manager,
        observe=observe,
        learned=lambda policy: {},
        force_copy=False,
        routing_admission=lambda: {"gateway": "a" * 64},
    )
    monkeypatch.setattr(command, "Discovery", lambda *args, **kwargs: discovery)
    deployment = NS(vm_ha=None, gateway_group=NS(name="gateway"))

    def prepare():
        return command.prepare(
            manager, deployment, {}, ssh_policy=None, runtime_changes=False, journal=journal
        )

    prepared = prepare()
    assert prepared.required
    prepared.begin("a" * 64)
    state.lose = "UpdateSubnetRequest"
    with pytest.raises(TimeoutError):
        prepared.finish("a" * 64)
    resumed = prepare()
    assert resumed.intent == prepared.intent
    resumed.finish("a" * 64)
    before = journal.path.read_bytes()
    unchanged = prepare()
    assert not unchanged.required
    assert unchanged.verifies_completed()
    unchanged.finish("b" * 64)
    assert journal.path.read_bytes() == before
    assert len(state.effects) == 4
    policy = Policy(
        (Connection("static", "static", 0, (), ("192.168.1.0/24", "192.168.2.0/24"), 2),),
        ("10.0.0.0/16",),
    )
    second = prepare()
    assert second.required
    assert second.intent["infrastructure"]["table_ids"] == [
        state.subnets["selected"].spec.route_table_id
    ]
    second.begin("c" * 64)
    second.finish("c" * 64)
    assert len(state.effects) == 5
    assert len(journal.inventory()) == 2


def test_default_table_copy_failure_preserves_subnet_attachment(copying):
    state, journal, plan, observe = copying

    def reject(kind):
        if kind == "CreateRouteRequest":
            raise RuntimeError("copy denied")

    state.before = reject
    with pytest.raises(RuntimeError, match="copy denied"):
        execute(object(), journal, plan, approval="a" * 64, observe=observe)
    assert state.subnets["selected"].spec.route_table_id == "source"
    assert state.subnets["unrelated"].spec.route_table_id == "source"
    assert len(state.routes) == 1
    assert journal.transaction()["phase"] == "pending"


def test_each_selected_subnet_receives_its_own_complete_private_copy(copying):
    state, journal, original, read = copying
    state.subnets["selected-two"] = clone(state.subnets["selected"])
    state.subnets["selected-two"].metadata.id = "selected-two"
    state.subnets["selected-two"].metadata.name = "selected-two"

    def observe():
        result = read()
        subnet = state.subnets["selected-two"]
        result["infrastructure"]["targets"].append(
            {
                "subnet_id": "selected-two",
                "table_id": subnet.spec.route_table_id,
                "spec_sha256": message_digest(subnet.spec),
            }
        )
        return result

    base = inspect(
        project="project",
        gateway="gateway",
        policy=Policy(
            (Connection("static", "static", 0, (), ("192.168.1.0/24",), 1),), ("10.0.0.0/16",)
        ),
        learned={},
        allocations={0: "allocation"},
        active_tables=("source",),
        routes=observe()["routes"],
        ownership={},
        infrastructure=observe()["infrastructure"],
    )
    first = deepcopy(original["conversions"]["source"])
    second = deepcopy(first)
    second["subnets"] = {
        "selected-two": request_record(
            vpc.UpdateSubnetRequest(
                metadata=clone(state.subnets["selected-two"].metadata),
                spec=clone(state.subnets["selected-two"].spec),
            )
        )
    }
    plan = convert(base, {"first-copy": first, "second-copy": second})
    execute(object(), journal, plan, approval="a" * 64, observe=observe)
    selected = {state.subnets[name].spec.route_table_id for name in ("selected", "selected-two")}
    assert len(selected) == 2
    assert "source" not in selected
    assert len(state.effects) == 8
    for table in selected:
        assert {
            r.spec.destination.cidr for r in state.routes.values() if r.metadata.parent_id == table
        } == {"172.16.0.0/16", "192.168.1.0/24"}
    assert state.subnets["unrelated"].spec.route_table_id == "source"


def test_rollback_file_binds_post_cutover_revision_and_preserves_original_spec(copying, tmp_path):
    import json
    import stat

    from nebius_vpngw.deploy.ordinary_route_command import PreparedRoutes, _write_rollbacks
    from nebius_vpngw.deploy.ordinary_route_plan import intent

    state, journal, plan, read = copying
    execute(object(), journal, plan, approval="a" * 64, observe=read)
    prepared = PreparedRoutes(
        NS(journal=journal, observe=read),
        intent(plan),
        plan,
        True,
        None,
        journal.transaction(),
        False,
    )
    directory = tmp_path / "rollback"
    _write_rollbacks(prepared, directory)
    (path,) = directory.glob("*.json")
    payload = json.loads(path.read_text())
    assert payload["metadata"]["resource_version"] == str(
        state.subnets["selected"].metadata.resource_version
    )
    assert payload["spec"]["route_table_id"] == "source"
    assert payload["spec"]["network_id"] == "network"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    _write_rollbacks(prepared, directory)
    assert len(list(directory.glob("*.json"))) == 1
    state.subnets["selected"].spec.network_id = "concurrently-changed"
    with pytest.raises(RuntimeError, match="Subnet changed"):
        _write_rollbacks(prepared, directory)
    assert len(list(directory.glob("*.json"))) == 1


@pytest.mark.parametrize("copying", ["opaque"], indirect=True)
def test_rollback_never_exports_lossy_json(copying, tmp_path):
    from nebius_vpngw.deploy.ordinary_route_command import PreparedRoutes, _write_rollbacks
    from nebius_vpngw.deploy.ordinary_route_plan import intent

    state, journal, plan, read = copying
    execute(object(), journal, plan, approval="a" * 64, observe=read)
    prepared = PreparedRoutes(
        NS(journal=journal, observe=read),
        intent(plan),
        plan,
        True,
        None,
        journal.transaction(),
        False,
    )
    directory = tmp_path / "rollback"
    with pytest.raises(RuntimeError, match="cannot preserve unknown"):
        _write_rollbacks(prepared, directory)
    assert not list(directory.glob("*.json"))
    assert journal.transaction()["phase"] == "complete"


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
@pytest.mark.parametrize(
    "lost", [None, "CreateRouteTableRequest", "CreateRouteRequest", "UpdateSubnetRequest"]
)
def test_ha_enforced_private_tables_receipts_restore_and_resume(
    copying, tmp_path, monkeypatch, lost
):
    from nebius_vpngw.deploy.ha_enforced_tables import TableJournal, Transition, freeze
    from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
    from nebius_vpngw.schema import VMHARouteTarget

    state, _, _, _ = copying
    monkeypatch.setattr(
        NebiusSDKRouteBackend,
        "_raw_routes",
        lambda self, table: tuple(
            clone(r) for r in state.routes.values() if r.metadata.parent_id == table
        ),
    )
    state.subnets["selected-two"] = clone(state.subnets["selected"])
    state.subnets["selected-two"].metadata.id = "selected-two"
    state.subnets["selected-two"].metadata.name = "selected-two"
    journal = TableJournal("project", "gateway", root=tmp_path / "ha-tables")

    def plan(desired, previous):
        targets = tuple(
            VMHARouteTarget(
                project_id="project",
                network_id="network",
                workload_subnet_id=s,
                route_table_id=state.subnets[s].spec.route_table_id,
            )
            for s in ("selected", "selected-two")
        )
        return freeze(
            project="project",
            gateway="gateway",
            cluster="cluster",
            allocation="allocation",
            targets=targets,
            subnets=state.subnets,
            routes={
                t: tuple(clone(r) for r in state.routes.values() if r.metadata.parent_id == t)
                for t in state.tables
            },
            consumers={
                t: {s for s, obj in state.subnets.items() if obj.spec.route_table_id == t}
                for t in state.tables
            },
            desired=desired,
            previous=previous,
        )

    wanted = {
        "selected": ["0.0.0.0/0", "198.18.0.0/24"],
        "selected-two": ["0.0.0.0/0", "198.18.0.0/24"],
    }
    frozen = plan(wanted, {})
    import json

    from nebius_vpngw.deploy.ha_enforced_inventory import private_table_creations

    def pending_creations():
        return private_table_creations(
            {
                "enforced_table_plan": json.dumps(frozen),
                "route_targets": [
                    {
                        **target,
                        "route_table_id": state.subnets[
                            target["workload_subnet_id"]
                        ].spec.route_table_id,
                    }
                    for target in frozen["targets"]
                ],
            }
        )

    assert private_table_creations({}) == 0
    assert pending_creations() == 2
    owner = Transition(object(), journal, frozen, allocation="allocation")
    resume_state = _ha_table_resume_state(owner, state)
    state.lose = lost
    if lost in {"CreateRouteTableRequest", "CreateRouteRequest"}:
        with pytest.raises(TimeoutError):
            owner.prepare(admit=lambda: None)
    targets = owner.prepare(admit=lambda: None)
    if lost is None:
        _assert_prepared_ha_host_resume(owner, state, resume_state)
    assert state.subnets["selected"].spec.route_table_id == "source"
    assert state.subnets["selected-two"].spec.route_table_id == "source"
    assert len({t.route_table_id for t in targets}) == 2
    with pytest.raises(RuntimeError, match="writers"):
        owner.cutover(quiescent=lambda: (_ for _ in ()).throw(RuntimeError("writers running")))
    assert all(kind != "UpdateSubnetRequest" for kind, _ in state.effects)
    if lost == "UpdateSubnetRequest":
        with pytest.raises(TimeoutError):
            owner.cutover(quiescent=lambda: None)
        _assert_ha_partial_table_admission(owner, state, resume_state)
        before = deepcopy(state.effects)
        owner.prepare(admit=lambda: None)
        assert state.effects == before  # Preparation cannot replay a subnet CAS.
        assert any(
            op["status"] == "intent"
            for op in journal.snapshot()["transaction"]["operations"].values()
        )
    owner.cutover(quiescent=lambda: None)
    _assert_ha_partial_table_admission(owner, state, resume_state)
    if lost is None:
        rebound = _assert_ha_table_lifecycle_rebind(owner)
        _assert_cutover_routes_do_not_change_migration_payload(owner, state, rebound)
    bound = owner.commit()
    assert pending_creations() == 0  # A settled audit is not another creation plan.
    effects = deepcopy(state.effects)
    owner.prepare(admit=lambda: None)
    owner.cutover(quiescent=lambda: None)
    owner.commit()
    assert state.effects == effects
    with pytest.raises(ValueError, match="journal authority"):
        Transition(
            object(),
            TableJournal("project", "other-gateway", root=tmp_path / "wrong"),
            frozen,
            allocation="allocation",
        )
    assert state.subnets["unrelated"].spec.route_table_id == "source"
    assert state.routes["foreign"].spec.next_hop.default_egress_gateway
    assert plan(wanted, bound) is None
    disabled = plan({}, bound)
    owner = Transition(object(), journal, disabled, allocation="allocation")
    owner.prepare(admit=lambda: None)
    owner.cutover(quiescent=lambda: None)
    bound = owner.commit()
    assert not bound["desired"] and not bound["predecessors"]
    for subnet in ("selected", "selected-two"):
        table = state.subnets[subnet].spec.route_table_id
        rows = [r for r in state.routes.values() if r.metadata.parent_id == table]
        assert len(rows) == 1 and rows[0].spec.next_hop.default_egress_gateway
        assert rows[0].metadata.name == state.routes["foreign"].metadata.name
        assert dict(rows[0].metadata.labels) == dict(state.routes["foreign"].metadata.labels)


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
def test_ha_enforced_removal_rejects_foreign_replacement(copying):
    from nebius_vpngw.deploy.ha_enforced_tables import freeze
    from nebius_vpngw.schema import VMHARouteTarget

    state, _, _, _ = copying
    target = VMHARouteTarget(
        project_id="project",
        network_id="network",
        workload_subnet_id="selected",
        route_table_id="source",
    )
    with pytest.raises(RuntimeError, match="lost its exact managed authority"):
        freeze(
            project="project",
            gateway="gateway",
            cluster="cluster",
            allocation="allocation",
            targets=(target,),
            subnets=state.subnets,
            routes={"source": tuple(state.routes.values())},
            consumers={"source": {"selected"}},
            desired={},
            previous={
                "desired": {"selected": ["0.0.0.0/0"]},
                "predecessors": {"selected": {"0.0.0.0/0": None}},
            },
        )


def _assert_ha_table_lifecycle_rebind(owner):
    import hashlib
    import json
    from dataclasses import replace

    from nebius_vpngw import apply_lock
    from nebius_vpngw.deploy.ha_enforced_rebind import PLAN_KEY, successor
    from nebius_vpngw.deploy.vm_ha_lifecycle import (
        VMHALifecycleMember,
        VMHALifecycleState,
        _validate_successor,
    )

    locks = {
        f"node-{i}": {
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
            "cluster_id": "cluster",
            "node_id": f"node-{i}",
            "generation_id": "d" * 64,
            "operation_id": "e" * 64,
        }
        for i in range(2)
    }
    observation = {
        "members": [],
        "routes": [],
        "route_targets": owner.plan["targets"],
        "shared_allocation": {"allocation_id": "allocation", "present": True},
        "project_id": "project",
    }
    before = VMHALifecycleState.start_provisioning(
        project_id="project",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=tuple(
            VMHALifecycleMember(
                i, f"gateway-{i}", f"node-{i}", role, f"compute-{i}", "eth0", f"192.0.2.{i + 1}"
            )
            for i, role in enumerate(("active", "passive"))
        ),
        operation_id="table-transition",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        initial_resource_bindings={
            PLAN_KEY: json.dumps(owner.plan),
            "shared-allocation-id": "allocation",
            **{
                f"enforced-route-lock:{node}": hashlib.sha256(apply_lock.encode(lock)).hexdigest()
                for node, lock in locks.items()
            },
        },
        current_observation=observation,
    )
    proof = {
        "plan": owner.plan,
        "operations": deepcopy(owner.journal.state["transaction"]["operations"]),
        "operation": before.transaction.operation_id,
        "barrier": {
            "kind": "stopped-pair",
            "nodes": {
                m.node_id: {
                    "compute_id": m.compute_id,
                    "operation": before.transaction.operation_id,
                    "boot_id": f"boot-{m.node_id}",
                    "lock": locks[m.node_id],
                }
                for m in before.members
            },
        },
    }
    observation["route_targets"] = [t.model_dump(mode="json") for t in owner.targets()]
    empty = replace(before, members=tuple(replace(m, compute_id="") for m in before.members))
    unprovisioned = {**proof, "barrier": {"kind": "unprovisioned"}}
    with pytest.raises(ValueError, match="barrier"):
        successor(empty, proof=unprovisioned, observation=observation)
    for field in ("compute_id", "lock", "boot_id"):
        invalid = deepcopy(proof)
        invalid["barrier"]["nodes"]["node-0"][field] = "" if field != "lock" else {}
        with pytest.raises(ValueError, match="stopped-member"):
            successor(before, proof=invalid, observation=observation)
    for field in ("cluster_id", "node_id", "generation_id", "operation_id"):
        invalid = deepcopy(proof)
        invalid["barrier"]["nodes"]["node-0"]["lock"][field] = "foreign"
        with pytest.raises(ValueError, match="stopped-member"):
            successor(before, proof=invalid, observation=observation)
    after = successor(before, proof=proof, observation=observation)
    _validate_successor(before, after)
    assert after.members == before.members and after.allocation_id == "allocation"
    with pytest.raises(ValueError, match="expanded"):
        _validate_successor(before, replace(after, route_runtime_id="foreign"))
    incomplete = deepcopy(proof)
    next(iter(incomplete["operations"].values()))["status"] = "accepted"
    with pytest.raises(ValueError, match="unsettled|receipt"):
        successor(before, proof=incomplete, observation=observation)
    return after


def _assert_cutover_routes_do_not_change_migration_payload(owner, cloud, lifecycle):
    import json
    from dataclasses import replace

    from nebius_vpngw.deploy.vm_ha_lifecycle import normalize_vm_ha_observation
    from nebius_vpngw.deploy.vm_manager import VMManager

    observation = _ha_table_observation(owner, cloud)
    observation["members"] = [{"instance_name": "gateway-0", "primary_allocation_id": "primary"}]
    target = owner.targets()[0]
    retained = {
        "allocation_id": "allocation",
        "name": next(
            iter(
                VMManager._vm_ha_managed_route_names(
                    cluster_id="cluster",
                    route_target=target,
                    prefix="203.0.113.0/24",
                    allocation_id="allocation",
                )
            )
        ),
        "prefix": "203.0.113.0/24",
        "revision": "1",
        "route_id": "existing-migration",
        "route_table_id": target.route_table_id,
    }
    observation["routes"].append(retained)
    manager = VMManager(project_id="project", region="eu-north1")
    manager._vm_ha_journal = NS(
        state=replace(
            lifecycle,
            transaction=replace(
                lifecycle.transaction, observation=normalize_vm_ha_observation(observation)
            ),
        )
    )
    spec = NS(name="gateway", vm_ha=NS(active_instance_index=0, cluster_id="cluster"))
    result = manager._vm_ha_migration_route_bindings(spec, owner.targets(), "allocation")
    # Fresh staging had no migration authority for the routes this operation
    # subsequently created. Resume must not add them to the installed payload.
    assert [route.route_id for route in result] == ["existing-migration"]
    original = manager._vm_ha_journal.state.transaction
    for field in ("allocation_id", "route_table_id", "name", "prefix"):
        changed = deepcopy(observation)
        changed["routes"][0][field] = "changed"
        manager._vm_ha_journal.state = replace(
            lifecycle,
            transaction=replace(original, observation=normalize_vm_ha_observation(changed)),
        )
        with pytest.raises(RuntimeError, match="created route changed"):
            manager._vm_ha_migration_route_bindings(spec, owner.targets(), "allocation")

    from nebius_vpngw.deploy.ha_enforced_rebind import PROOF_KEY

    bindings = dict(original.resource_bindings)
    proof = json.loads(bindings[PROOF_KEY])
    proof["operation"] = "another-operation"
    bindings[PROOF_KEY] = json.dumps(proof)
    manager._vm_ha_journal.state = replace(
        lifecycle, transaction=replace(original, resource_bindings=tuple(sorted(bindings.items())))
    )
    assert len(manager._vm_ha_migration_route_bindings(spec, owner.targets(), "allocation")) == 5


def _ha_table_observation(owner, cloud):
    import json

    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy.ordinary_route_ownership import snapshot
    from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend

    targets = [
        {**t, "route_table_id": cloud.subnets[t["workload_subnet_id"]].spec.route_table_id}
        for t in owner.plan["targets"]
    ]
    routes = []
    for target in targets:
        for r in cloud.routes.values():
            if r.metadata.parent_id != target["route_table_id"]:
                continue
            s = snapshot(r)
            routes.append(
                {
                    "route_id": s["id"],
                    "route_table_id": s["parent_id"],
                    "name": s["name"],
                    "prefix": s["destination"],
                    "allocation_id": s["allocation_id"],
                    "revision": str(r.metadata.resource_version),
                    "authority_labels": {
                        k: v
                        for k, v in s["labels"].items()
                        if k in NebiusSDKRouteBackend._AUTHORITY_LABEL_KEYS
                    },
                    "snapshot_sha256": digest(s),
                }
            )
    return {
        "members": [],
        "project_id": "project",
        "routes": routes,
        "route_targets": targets,
        "shared_allocation": {"allocation_id": "allocation", "present": True},
        "enforced_table_plan": json.dumps(owner.plan, sort_keys=True, separators=(",", ":")),
    }


def _ha_table_resume_state(owner, cloud):
    import json

    from nebius_vpngw.deploy.ha_enforced_rebind import PLAN_KEY
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleMember, VMHALifecycleState

    return VMHALifecycleState.start_provisioning(
        project_id="project",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=tuple(
            VMHALifecycleMember(i, f"gateway-{i}", f"node-{i}", role, "", "", "")
            for i, role in enumerate(("active", "passive"))
        ),
        operation_id="table-transition",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        initial_resource_bindings={
            PLAN_KEY: json.dumps(owner.plan),
            "shared-allocation-id": "allocation",
        },
        current_observation=_ha_table_observation(owner, cloud),
    )


def _assert_ha_partial_table_admission(owner, cloud, lifecycle):
    from nebius_vpngw.deploy.ha_enforced_resume import admit

    saved = owner.journal.snapshot()
    current = _ha_table_observation(owner, cloud)
    assert admit(lifecycle, current, saved=saved)
    drift = deepcopy(current)
    drift["routes"][0]["snapshot_sha256"] = "foreign"
    with pytest.raises(ValueError, match="creation receipts"):
        admit(lifecycle, drift, saved=saved)
    drift = deepcopy(current)
    drift["shared_allocation"]["allocation_id"] = "foreign"
    with pytest.raises(ValueError, match="unrelated cloud drift"):
        admit(lifecycle, drift, saved=saved)
    missing = deepcopy(saved)
    del missing["transaction"]["operations"][
        next(
            k
            for k, op in missing["transaction"]["operations"].items()
            if op["request_type"] == "CreateRouteRequest"
        )
    ]
    with pytest.raises(ValueError, match="preparation receipts"):
        admit(lifecycle, current, saved=missing)


def _assert_prepared_ha_host_resume(owner, cloud, lifecycle):
    import json
    from dataclasses import replace

    from nebius_vpngw.deploy.ha_enforced_resume import admit
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus
    from nebius_vpngw.schema import VMHARuntimeBinding

    targets = owner.targets()
    pending = replace(
        lifecycle,
        status=VMHALifecycleStatus.ACTIVATING,
        allocation_id="allocation",
        members=tuple(
            replace(
                m,
                compute_id=f"compute-{m.instance_index}",
                compute_revision="1",
                disk_id=f"disk-{m.instance_index}",
                network_interface_name="eth0",
                network_interface_subnet_id="gateway-subnet",
                primary_allocation_id=f"primary-{m.instance_index}",
                public_allocation_id=f"public-{m.instance_index}",
                public_ip=f"192.0.2.{m.instance_index + 1}",
            )
            for m in lifecycle.members
        ),
        route_targets=tuple(
            sorted(
                json.dumps(t.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                for t in targets
            )
        ),
        route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
            "cluster", "allocation", targets
        ),
        transaction=replace(lifecycle.transaction, pending_effect="stage-node-0"),
    )
    observation = _ha_table_observation(owner, cloud)
    assert admit(pending, observation, saved=owner.journal.snapshot())
    with pytest.raises(ValueError, match="approved lifecycle"):
        admit(
            replace(
                pending,
                transaction=replace(
                    pending.transaction, pending_effect="provision-gateway-0-compute"
                ),
            ),
            observation,
            saved=owner.journal.snapshot(),
        )


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
@pytest.mark.parametrize("lost", [False, True])
def test_enforced_destroy_restores_original_after_fencing_with_receipt_replay(
    copying, tmp_path, monkeypatch, lost
):
    from nebius_vpngw.deploy import enforced_destroy as cleanup
    from nebius_vpngw.deploy.destroy import DestroyRoute
    from nebius_vpngw.deploy.ha_enforced_tables import TableJournal, Transition, freeze
    from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
    from nebius_vpngw.schema import VMHARouteTarget

    cloud, _, _, _ = copying
    monkeypatch.setattr(
        NebiusSDKRouteBackend,
        "_raw_routes",
        lambda self, table: tuple(
            clone(r) for r in cloud.routes.values() if r.metadata.parent_id == table
        ),
    )
    journal = TableJournal("project", "gateway", root=tmp_path / "ha-tables")
    target = VMHARouteTarget(
        project_id="project",
        network_id="network",
        workload_subnet_id="selected",
        route_table_id="source",
    )
    frozen = freeze(
        project="project",
        gateway="gateway",
        cluster="cluster",
        allocation="allocation",
        targets=(target,),
        subnets=cloud.subnets,
        routes={"source": tuple(cloud.routes.values())},
        consumers={"source": {"selected", "unrelated"}},
        desired={"selected": ["0.0.0.0/0"]},
        previous={},
    )
    transition = Transition(object(), journal, frozen, allocation="allocation")
    transition.prepare(admit=lambda: None)
    transition.cutover(quiescent=lambda: None)
    transition.commit()
    backend = NS(
        project_id="project",
        spec=NS(name="gateway"),
        client=object(),
        routes=vpc.RouteServiceClient(object()),
        _route_by_id=lambda r: clone(cloud.routes[r.route_id]),
        instance_fenced=lambda _: True,
        route_absent=lambda r: r.route_id not in cloud.routes,
    )
    routes = tuple(
        DestroyRoute(
            r.metadata.id,
            r.metadata.name,
            r.metadata.parent_id,
            "allocation",
            str(r.metadata.resource_version),
        )
        for r in cloud.routes.values()
        if r.spec.next_hop.allocation is not None and r.spec.next_hop.allocation.id == "allocation"
    )
    monkeypatch.setattr(cleanup, "owner", lambda *args: journal)
    restore = cleanup.RestoreJournal("project", "gateway", root=tmp_path / "restore")
    monkeypatch.setattr(cleanup, "RestoreJournal", lambda *args: restore)
    declaration = cleanup.freeze(backend, routes, ha=True)
    plan = NS(
        project_id="project",
        gateway_name="gateway",
        digest="e" * 64,
        enforced_restore=declaration,
        compute=(),
        routes=routes,
    )
    with pytest.raises(RuntimeError, match="owned route removal"):
        cleanup.execute(backend, plan)
    for r in routes:
        del cloud.routes[r.route_id]
    original_table = cloud.subnets["unrelated"].spec.route_table_id
    cloud.subnets["unrelated"].spec.route_table_id = cloud.subnets["selected"].spec.route_table_id
    with pytest.raises(RuntimeError, match="consumer scope"):
        cleanup.execute(backend, plan)
    cloud.subnets["unrelated"].spec.route_table_id = original_table
    cloud.routes["foreign"].metadata.name = "drift"
    with pytest.raises(RuntimeError, match="predecessor drifted"):
        cleanup.execute(backend, plan)
    cloud.routes["foreign"].metadata.name = "vpngw-customer-route"
    if lost:
        cloud.lose = "CreateRouteRequest"
        with pytest.raises(TimeoutError):
            cleanup.execute(backend, plan)
    cleanup.execute(backend, plan)
    cleanup.verify(backend, plan)
    cleanup.retire(backend, plan)
    effects = deepcopy(cloud.effects)
    cleanup.execute(backend, plan)
    cleanup.retire(backend, plan)
    assert cloud.effects == effects
    assert journal.snapshot()["transaction"] is None
    assert journal.snapshot()["bindings"] == {"destroyed_enforcement": plan.digest}
    from nebius_vpngw.deploy import ha_to_ordinary_routes as reverse

    monkeypatch.setattr(reverse, "TableJournal", lambda *args: journal)
    assert reverse.observe("project", "gateway", None, (), {}) is None
    table = cloud.subnets["selected"].spec.route_table_id
    restored = [r for r in cloud.routes.values() if r.metadata.parent_id == table]
    assert len(restored) == 1 and restored[0].spec.next_hop.default_egress_gateway


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
@pytest.mark.parametrize("retain", [True, False])
def test_ordinary_enforcement_converts_to_ha_with_original_egress_history(
    copying, tmp_path, monkeypatch, retain
):
    import json

    from nebius_vpngw.deploy.enforced_predecessors import augment
    from nebius_vpngw.deploy.ha_enforced_tables import TableJournal, Transition, freeze
    from nebius_vpngw.deploy.ordinary_route_migration import KEY, SCHEMA
    from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
    from nebius_vpngw.schema import VMHARouteTarget

    cloud, ordinary, original, read = copying
    policy = Policy(
        (Connection("peer", "static", 0, (), ("192.168.1.0/24",), 1),),
        ("10.0.0.0/16",),
        enforced=(
            {
                "connection": "peer",
                "source_subnets": ["10.0.1.0/24"],
                "destinations": ["0.0.0.0/0"],
            },
        ),
    )

    def observe():
        result = read()
        result["infrastructure"]["enforced_sources"] = {"peer": ["selected"]}
        return result

    observed = observe()
    initial = inspect(
        project="project",
        gateway="gateway",
        policy=policy,
        learned={},
        allocations={0: "allocation"},
        active_tables=("source",),
        routes=observed["routes"],
        ownership={},
        infrastructure=observed["infrastructure"],
        private_copies=("selected",),
    )
    initial = augment(convert(initial, original["conversions"]), observed, {})
    execute(object(), ordinary, initial, approval="a" * 64, observe=observe)
    captured = {KEY: json.dumps({"schema": SCHEMA, "sources": {"node": ordinary.snapshot()}})}
    monkeypatch.setattr(
        NebiusSDKRouteBackend,
        "_raw_routes",
        lambda self, table: tuple(
            clone(r) for r in cloud.routes.values() if r.metadata.parent_id == table
        ),
    )
    journal = TableJournal("project", "gateway", root=tmp_path / "ha-tables")

    def plan(desired, previous, ordinary_state=None):
        table = cloud.subnets["selected"].spec.route_table_id
        return freeze(
            project="project",
            gateway="gateway",
            cluster="cluster",
            allocation="ha-allocation",
            targets=(
                VMHARouteTarget(
                    project_id="project",
                    network_id="network",
                    workload_subnet_id="selected",
                    route_table_id=table,
                ),
            ),
            subnets=cloud.subnets,
            routes={
                table: tuple(
                    clone(r) for r in cloud.routes.values() if r.metadata.parent_id == table
                )
            },
            consumers={
                table: {s for s, obj in cloud.subnets.items() if obj.spec.route_table_id == table}
            },
            desired=desired,
            previous=previous,
            ordinary=ordinary_state,
            static_prefixes=frozenset({"192.168.1.0/24"}),
        )

    frozen = plan({"selected": ["0.0.0.0/0"]} if retain else {}, {}, ordinary.snapshot())
    effects = deepcopy(cloud.effects)
    with pytest.raises(RuntimeError, match="handoff authority"):
        Transition(object(), journal, frozen, allocation="ha-allocation")
    tampered = deepcopy(frozen)
    next(iter(tampered["ordinary_sources"].values()))["name"] = "changed"
    with pytest.raises(RuntimeError, match="creation receipts"):
        Transition(
            object(), journal, tampered, allocation="ha-allocation", ordinary_bindings=captured
        )
    assert cloud.effects == effects
    owner = Transition(
        object(), journal, frozen, allocation="ha-allocation", ordinary_bindings=captured
    )
    old_table = cloud.subnets["selected"].spec.route_table_id
    owner.prepare(admit=lambda: None)
    assert cloud.subnets["selected"].spec.route_table_id == old_table
    owner.cutover(quiescent=lambda: None)
    bound = owner.commit()
    assert bound["retired_routes"]
    if retain:
        disabled = plan({}, bound)
        owner = Transition(object(), journal, disabled, allocation="ha-allocation")
        owner.prepare(admit=lambda: None)
        owner.cutover(quiescent=lambda: None)
        bound = owner.commit()
    assert not bound["predecessors"]
    table = cloud.subnets["selected"].spec.route_table_id
    rows = {
        r.spec.destination.cidr: r for r in cloud.routes.values() if r.metadata.parent_id == table
    }
    assert set(rows) == {"0.0.0.0/0", "192.168.1.0/24"}
    assert rows["0.0.0.0/0"].spec.next_hop.default_egress_gateway
    assert rows["192.168.1.0/24"].spec.next_hop.allocation.id == "ha-allocation"
    assert dict(rows["0.0.0.0/0"].metadata.labels) == dict(cloud.routes["foreign"].metadata.labels)


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
@pytest.mark.parametrize("retain", [True, False])
def test_ha_to_ordinary_preserves_original_predecessor_and_exact_copy_authority(
    copying, tmp_path, monkeypatch, retain
):
    from nebius_vpngw.deploy import ha_to_ordinary_routes as reverse
    from nebius_vpngw.deploy.enforced_predecessors import augment
    from nebius_vpngw.deploy.ha_enforced_tables import TableJournal, Transition, freeze
    from nebius_vpngw.deploy.ordinary_route_command import _observe_plan
    from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
    from nebius_vpngw.deploy.vm_ha_routes import ManagedRouteKind
    from nebius_vpngw.schema import VMHARouteTarget

    cloud, ordinary, _, read = copying
    monkeypatch.setattr(
        NebiusSDKRouteBackend,
        "_raw_routes",
        lambda self, table: tuple(
            clone(r) for r in cloud.routes.values() if r.metadata.parent_id == table
        ),
    )
    journal = TableJournal("project", "gateway", root=tmp_path / "ha-tables")
    monkeypatch.setattr(reverse, "TableJournal", lambda project, gateway: journal)
    target = VMHARouteTarget(
        project_id="project",
        network_id="network",
        workload_subnet_id="selected",
        route_table_id="source",
    )
    frozen = freeze(
        project="project",
        gateway="gateway",
        cluster="cluster",
        allocation="ha-allocation",
        targets=(target,),
        subnets=cloud.subnets,
        routes={"source": tuple(cloud.routes.values())},
        consumers={"source": set(cloud.subnets)},
        desired={"selected": ["0.0.0.0/0"]},
        previous={},
    )
    owner = Transition(object(), journal, frozen, allocation="ha-allocation")
    owner.prepare(admit=lambda: None)
    owner.cutover(quiescent=lambda: None)
    bound = owner.commit()
    target = VMHARouteTarget.model_validate(bound["targets"][0])
    cloud.routes["ha-static"] = vpc.Route(
        metadata=common.ResourceMetadata(
            id="ha-static",
            parent_id=target.route_table_id,
            name="ha-static",
            resource_version=1,
            labels=NebiusSDKRouteBackend._authority_labels(
                cluster_id="cluster",
                allocation_id="ha-allocation",
                route_target=target,
                route_kind=ManagedRouteKind.STATIC,
            ),
        ),
        spec=vpc.RouteSpec(
            destination=vpc.DestinationMatch(cidr="192.168.1.0/24"),
            next_hop=vpc.NextHop(allocation=vpc.AllocationNextHop(id="ha-allocation")),
        ),
    )
    connection = Connection("peer", "static", 0, (), ("192.168.1.0/24",), 1)
    policy = Policy(
        (connection,),
        ("10.0.0.0/16",),
        enforced=(
            {
                "connection": "peer",
                "source_subnets": ["10.0.1.0/24"],
                "destinations": ["0.0.0.0/0"],
            },
        )
        if retain
        else (),
    )

    def observation():
        value = read()
        if policy.enforced:
            value["infrastructure"]["enforced_sources"] = {"peer": ["selected"]}
        return value

    first = observation()
    authority = reverse.observe(
        "project",
        "gateway",
        ordinary.snapshot(),
        first["infrastructure"]["targets"],
        first["routes"],
    )
    for field in ("source_sha256", "predecessors", "targets"):
        altered = deepcopy(authority)
        altered[field] = "f" * 64 if field == "source_sha256" else {}
        with pytest.raises(RuntimeError, match="HA conversion"):
            reverse.verify(altered)
    subnet = cloud.subnets["selected"]
    conversion = {
        "selected": {
            "source": target.route_table_id,
            "routes": copy_inventory(first["routes"][target.route_table_id]),
            "subnets": {
                "selected": request_record(
                    vpc.UpdateSubnetRequest(
                        metadata=clone(subnet.metadata), spec=clone(subnet.spec)
                    )
                )
            },
        }
    }
    plan, unknown = _observe_plan(
        NS(journal=ordinary, force_copy=False),
        {
            **first,
            "policy": policy,
            "active_tables": (target.route_table_id,),
            "conversion_requests": conversion,
            "ha_conversion": authority,
        },
        defer_bgp=True,
    )
    assert not unknown and not any(
        r["type"] == "DeleteRouteRequest" for r in plan["requests"].values()
    )
    cloud.lose = "UpdateSubnetRequest"
    with pytest.raises(TimeoutError):
        execute(object(), ordinary, plan, approval="c" * 64, observe=observation)
    interrupted = observation()
    assert (
        reverse.observe(
            "project",
            "gateway",
            ordinary.snapshot(),
            interrupted["infrastructure"]["targets"],
            interrupted["routes"],
        )
        == authority
    )
    execute(object(), ordinary, plan, approval="c" * 64, observe=observation)
    after = observation()
    assert (
        reverse.observe(
            "project",
            "gateway",
            ordinary.snapshot(),
            after["infrastructure"]["targets"],
            after["routes"],
        )
        is None
    )
    current = cloud.subnets["selected"].spec.route_table_id
    assert current != target.route_table_id
    assert cloud.routes["ha-static"].spec.next_hop.allocation.id == "ha-allocation"
    if retain:
        policy = Policy((connection,), ("10.0.0.0/16",))
        after = observation()
        disabled = inspect(
            project="project",
            gateway="gateway",
            policy=policy,
            learned={},
            allocations={0: "allocation"},
            active_tables=(current,),
            routes=after["routes"],
            ownership=ordinary.inventory(),
            infrastructure=after["infrastructure"],
        )
        disabled = augment(disabled, after, ordinary.enforced_predecessors())
        execute(object(), ordinary, disabled, approval="d" * 64, observe=observation)
    rows = {
        r.spec.destination.cidr: r for r in cloud.routes.values() if r.metadata.parent_id == current
    }
    assert set(rows) == {"0.0.0.0/0", "192.168.1.0/24"}
    assert rows["0.0.0.0/0"].spec.next_hop.default_egress_gateway
    assert rows["192.168.1.0/24"].spec.next_hop.allocation.id == "allocation"
    assert not ordinary.enforced_predecessors()


@pytest.mark.parametrize("copying", ["default-egress"], indirect=True)
@pytest.mark.parametrize("lose_reply", [False, True])
def test_ha_preparation_admits_only_receipted_allocation_revision(
    copying, tmp_path, monkeypatch, lose_reply
):
    from dataclasses import replace

    from nebius_vpngw.deploy import ha_enforced_preparation as preparation
    from nebius_vpngw.deploy.ha_enforced_tables import TableJournal, Transition, freeze
    from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
    from nebius_vpngw.deploy.vm_ha_lifecycle import (
        _validate_successor,
        normalize_vm_ha_observation,
    )
    from nebius_vpngw.deploy.vm_manager import VMManager
    from nebius_vpngw.schema import VMHARouteTarget

    cloud, _, _, _ = copying
    monkeypatch.setattr(
        NebiusSDKRouteBackend,
        "_raw_routes",
        lambda self, table: tuple(
            clone(r) for r in cloud.routes.values() if r.metadata.parent_id == table
        ),
    )
    journal = TableJournal("project", "gateway", root=tmp_path / "ha-tables")
    monkeypatch.setattr(
        preparation, "TableJournal", lambda *args: TableJournal(*args, root=tmp_path / "ha-tables")
    )
    target = VMHARouteTarget(
        project_id="project",
        network_id="network",
        workload_subnet_id="selected",
        route_table_id="source",
    )
    plan = freeze(
        project="project",
        gateway="gateway",
        cluster="cluster",
        allocation="allocation",
        targets=(target,),
        subnets=cloud.subnets,
        routes={"source": tuple(cloud.routes.values())},
        consumers={"source": set(cloud.subnets)},
        desired={"selected": ["198.18.0.0/24", "198.19.0.0/24"]},
        previous={},
    )
    owner = Transition(object(), journal, plan, allocation="allocation")

    def observe():
        value = _ha_table_observation(owner, cloud)
        references = sum(
            getattr(r.spec.next_hop.allocation, "id", None) == "allocation"
            for r in cloud.routes.values()
        )
        value["shared_allocation"]["resource_revision"] = str(1 + references)
        return value

    original = _ha_table_resume_state(owner, cloud)
    original = replace(
        original,
        transaction=replace(
            original.transaction, observation=normalize_vm_ha_observation(observe())
        ),
    )
    lifecycle = NS(state=original)
    transitions = []

    def transition(after):
        _validate_successor(lifecycle.state, after)
        transitions.append((lifecycle.state, after))
        lifecycle.state = after

    lifecycle.transition = transition
    manager = VMManager(project_id="project", region="eu-north1")
    manager._vm_ha_journal = lifecycle
    monkeypatch.setattr(manager, "_get_client", lambda: object())
    monkeypatch.setattr(manager, "observe_vm_ha_migration_state", lambda *args: observe())
    monkeypatch.setattr(manager, "_stable_vm_ha_effect_observation", observe)
    if lose_reply:

        def lose_first_reference(kind):
            copies = [r for r in cloud.routes.values() if r.metadata.parent_id != "source"]
            if (
                kind == "CreateRouteRequest"
                and copies
                and not any(
                    getattr(r.spec.next_hop.allocation, "id", None) == "allocation" for r in copies
                )
            ):
                cloud.lose = kind

        cloud.before = lose_first_reference
        with pytest.raises(TimeoutError, match="response lost"):
            owner.prepare(admit=lambda: manager._verify_vm_ha_transaction_preconditions(NS(), []))
        cloud.before = None
    owner.prepare(admit=lambda: manager._verify_vm_ha_transaction_preconditions(NS(), []))
    assert lifecycle.state.transaction.observation == normalize_vm_ha_observation(observe())
    verification_calls = []
    original_verify = owner.verify

    def verify(**kwargs):
        verification_calls.append(kwargs)
        return original_verify(**kwargs)

    monkeypatch.setattr(owner, "verify", verify)
    before_effects = list(cloud.effects)
    owner.prepare(admit=lambda: manager._verify_vm_ha_transaction_preconditions(NS(), []))
    assert cloud.effects == before_effects
    assert verification_calls == [dict(attached=False, uncertain=True), dict(attached=False)]
    assert len(transitions) == 2
    assert all(kind != "UpdateSubnetRequest" for kind, _ in cloud.effects)
    before, after = transitions[0]
    import json

    proof = json.loads(dict(after.transaction.resource_bindings)[preparation.PROOF_KEY])
    observation = observe()
    for field, value in (
        ("allocation_id", "foreign"),
        ("owner", {"compute_id": "foreign"}),
        ("resource_revision", "0"),
    ):
        drift = deepcopy(observation)
        drift["shared_allocation"][field] = value
        with pytest.raises(ValueError, match="unrelated"):
            preparation.successor(before, proof=proof, observation=drift)
    invalid = deepcopy(proof)
    invalid["references"] = {}
    with pytest.raises(ValueError, match="reference readback"):
        preparation.successor(before, proof=invalid, observation=observation)
    with pytest.raises(ValueError, match="expanded"):
        _validate_successor(before, replace(after, route_runtime_id="foreign"))
    # Even a valid journal cannot admit a foreign candidate route or consumer.
    cloud.subnets["unrelated"].spec.route_table_id = owner.targets()[0].route_table_id
    lifecycle.state = before
    with pytest.raises(RuntimeError, match="consumer"):
        preparation.reconcile(manager, observation)
    assert lifecycle.state == before
