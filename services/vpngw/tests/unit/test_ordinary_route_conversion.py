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

    monkeypatch.setattr(vpc, "RouteTableServiceClient", lambda sdk: NS(create=mutate))
    monkeypatch.setattr(vpc, "RouteServiceClient", lambda sdk: NS(create=mutate))
    monkeypatch.setattr(vpc, "SubnetServiceClient", lambda sdk: NS(update=mutate))
    monkeypatch.setattr(
        common,
        "OperationServiceClient",
        lambda sdk: NS(
            get=lambda request, **kwargs: NS(
                wait=lambda: next(op for op in state.operations.values() if op.id == request.id)
            )
        ),
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
