from copy import deepcopy

import pytest
from tests.unit.test_ordinary_route_plan import world  # noqa: F401

from nebius_vpngw import ordinary_route_binding as binding
from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority
from nebius_vpngw.apply_approval import digest


def bound():
    generation = "a" * 64
    return {
        "schema": binding.SCHEMA,
        "project": "project",
        "gateway": "gateway",
        "node": "gateway-0",
        "generation": generation,
        "gateway_subnet_name": "gateway-subnet",
        "infrastructure": {
            "project": "project",
            "network_id": "network",
            "network_spec_sha256": "b" * 64,
            "table_ids": ["table"],
            "tables": {
                "table": {"default": False, "subnets": ["workload"], "spec_sha256": "c" * 64}
            },
            "targets": [{"subnet_id": "workload", "table_id": "table", "spec_sha256": "d" * 64}],
            "gateways": {
                "0": {
                    "name": "gateway-0",
                    "compute_id": "compute",
                    "nic": "eth0",
                    "subnet_id": "gateway-subnet",
                    "allocation_id": "allocation",
                }
            },
            "excluded_prefixes": ["10.0.0.0/16"],
        },
        "policy": {
            "connections": [
                {
                    "name": "peer",
                    "mode": "bgp",
                    "instance_index": 0,
                    "peers": ["169.254.1.2"],
                    "prefixes": [],
                    "max_prefixes": 10,
                }
            ],
            "excluded_prefixes": ["10.0.0.0/16"],
            "summarize": False,
        },
        "credentials": {
            "path": binding.credential_path(
                generation=generation, node="gateway-0", sha256="e" * 64
            ),
            "sha256": "e" * 64,
            "service_account": "sa",
            "authorized_key": "key",
        },
    }


def test_generated_binding_rejects_shared_tables_unresolved_identity_and_foreign_credentials():
    assert binding.validate(bound()) == bound()
    for mutate in (
        lambda v: v["infrastructure"]["tables"]["table"].update(default=True),
        lambda v: v["infrastructure"]["tables"]["table"]["subnets"].append("unrelated"),
        lambda v: v["infrastructure"]["gateways"]["0"].update(compute_id="@unresolved"),
        lambda v: v["credentials"].update(path="/tmp/arbitrary-key"),
        lambda v: v["policy"]["connections"][0].update(instance_index=1),
    ):
        value = deepcopy(bound())
        mutate(value)
        with pytest.raises(ValueError):
            binding.validate(value)


def test_workstation_reservation_survives_restart_and_never_expires(tmp_path):
    store = RouteAuthority(tmp_path / "routes")
    store.reserve("1" * 64, digest(store.snapshot()))
    restarted = RouteAuthority(store.root)
    with pytest.raises(RuntimeError, match="does not own"):
        restarted.require_runtime(bound())
    with pytest.raises(RuntimeError, match="Another command"):
        restarted.reserve("2" * 64, digest(restarted.snapshot()))
    assert restarted.reserve("1" * 64, "stale-preview")["authority"]["holder"] == "1" * 64
    restarted.release("1" * 64, bound(), None)
    restarted.require_runtime(bound())
    # Lost release reply is idempotent and does not reacquire runtime ownership.
    before = restarted.path.read_bytes()
    restarted.release("1" * 64, bound(), None)
    assert restarted.path.read_bytes() == before


def test_changed_runtime_state_requires_review_before_new_reservation(tmp_path):
    store = RouteAuthority(tmp_path / "routes")
    original = digest(store.snapshot())
    store.reserve("1" * 64, original)
    store.release("1" * 64, bound(), None)
    with pytest.raises(RuntimeError, match="changed since"):
        store.reserve("2" * 64, original)
    store.require_runtime(bound())


def test_retired_runtime_never_reacquires_cloud_writes_and_resumes_exactly(tmp_path):
    store = RouteAuthority(tmp_path / "routes")
    store.reserve("1" * 64, digest(store.snapshot()))
    store.release("1" * 64, bound(), None)
    original = store.snapshot()
    store.reserve("2" * 64, digest(original))
    terminal = store.retire("2" * 64, "c" * 64, None)
    assert terminal["authority"]["writer"] == "deployment"
    restarted = RouteAuthority(store.root)
    with pytest.raises(RuntimeError, match="does not own"):
        restarted.require_runtime(bound())
    before = store.path.read_bytes()
    assert restarted.retire("2" * 64, "c" * 64, None) == terminal
    assert store.path.read_bytes() == before
    with pytest.raises(RuntimeError, match="reservation"):
        restarted.retire("3" * 64, "c" * 64, None)
    restarted.reserve("3" * 64, digest(terminal))
    restarted.release("3" * 64, bound(), None)
    restarted.require_runtime(bound())


def test_unsettled_cloud_receipts_cannot_release_workstation(tmp_path):
    store = RouteAuthority(tmp_path / "routes")
    store.reserve("1" * 64, digest(store.snapshot()))
    journal = store.journal(bound())
    with journal:
        journal.state["transaction"] = {
            "intent": {},
            "plan": None,
            "approval": "a" * 64,
            "phase": "pending",
            "member": 0,
            "operations": {},
            "verified": [],
        }
        journal.save()
    with pytest.raises(RuntimeError, match="Unsettled"):
        store.release("1" * 64, bound(), journal.snapshot())
    assert store.read()["writer"] == "workstation"


def test_existing_agent_loop_closes_runtime_sdk_after_exit(monkeypatch):
    from types import SimpleNamespace

    from nebius_vpngw.agent import main, ordinary_cloud_routes, ordinary_selection

    calls = []
    monkeypatch.setattr(
        main, "Agent", lambda: SimpleNamespace(reload=lambda: calls.append("reload"))
    )
    monkeypatch.setattr(
        ordinary_selection,
        "OrdinarySelectionRuntime",
        lambda: SimpleNamespace(step=lambda: calls.append("selection")),
    )
    monkeypatch.setattr(
        ordinary_cloud_routes,
        "OrdinaryCloudRoutes",
        lambda: SimpleNamespace(
            step=lambda: calls.append("routes"), close=lambda: calls.append("closed")
        ),
    )
    monkeypatch.setattr(main.signal, "signal", lambda *args: None)
    monkeypatch.setattr(
        main.time, "sleep", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    main._run_agent()
    assert calls == ["reload", "selection", "routes", "closed"]


def test_runtime_with_no_generated_binding_does_not_create_sdk_or_cloud_effects(tmp_path):
    from nebius_vpngw.agent.ordinary_cloud_routes import OrdinaryCloudRoutes

    runtime = OrdinaryCloudRoutes(
        binding_path=tmp_path / "absent", sdk_factory=lambda *a, **k: pytest.fail("unbound SDK")
    )
    assert runtime.step() is None


def test_reserved_runtime_rejects_before_credential_or_cloud_access(tmp_path, monkeypatch):
    import contextlib
    import hashlib
    import json

    from nebius_vpngw import ordinary_operations as operations
    from nebius_vpngw.agent import ordinary_cloud_routes
    from nebius_vpngw.agent.ordinary_cloud_routes import OrdinaryCloudRoutes

    config = tmp_path / "config.yaml"
    config.write_text("gateway_group: {name: gateway, instance_index: 0}\nconnections: []\n")
    value = bound()
    value["generation"] = hashlib.sha256(config.read_bytes()).hexdigest()
    value["credentials"]["path"] = binding.credential_path(
        generation=value["generation"], node=value["node"], sha256=value["credentials"]["sha256"]
    )
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    boot = tmp_path / "boot"
    boot.write_text("boot")
    monkeypatch.setattr(operations, "BOOT", boot)
    monkeypatch.setattr(operations, "mutation_lock", contextlib.nullcontext)
    monkeypatch.setattr(operations, "require_idle", lambda: None)
    monkeypatch.setattr(ordinary_cloud_routes, "require_quiescent", lambda cfg: None)
    root = tmp_path / "routes"
    owner = RouteAuthority(root)
    owner.reserve("1" * 64, digest(owner.snapshot()))
    runtime = OrdinaryCloudRoutes(
        config_path=config,
        binding_path=path,
        root=root,
        sdk_factory=lambda *a, **k: pytest.fail("reserved SDK"),
    )
    with pytest.raises(RuntimeError, match="does not own"):
        runtime.step()


def test_route_operation_wait_obeys_remaining_tick_budget():
    from types import SimpleNamespace

    from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_budget, wait_vm_ha_operation

    calls = []
    with vm_ha_observation_budget(seconds=0.25):
        wait_vm_ha_operation(SimpleNamespace(sync_wait=lambda **kwargs: calls.append(kwargs)))
    assert 0 < calls[0]["timeout"] <= 0.25
    assert calls[0]["auth_timeout"] <= 0.25


@pytest.mark.parametrize("mixed_summary", [False, True])
def test_periodic_runtime_adds_and_withdraws_through_existing_receipt_owner(
    request, tmp_path, monkeypatch, mixed_summary
):
    """Real route planner/effect journal; fake SDK, credentials and native boundary."""
    import contextlib
    import hashlib
    import json
    from dataclasses import replace
    from types import SimpleNamespace

    from tests.unit.test_ordinary_route_plan import external_route

    from nebius_vpngw import ordinary_operations as operations
    from nebius_vpngw.agent import ordinary_cloud_routes as module
    from nebius_vpngw.deploy.ordinary_route_policy import Connection

    cloud, _, policy, _, observe = request.getfixturevalue("world")
    if mixed_summary:
        policy = replace(
            policy,
            summarize=True,
            connections=(
                *policy.connections,
                Connection("static", "static", 0, (), ("192.168.1.128/25",), 1),
            ),
        )
    dynamic_prefix = "192.168.1.0/25" if mixed_summary else "192.168.1.0/24"
    config = tmp_path / "config.yaml"
    config.write_text("gateway_group: {name: gateway, instance_index: 0}\nconnections: []\n")
    value = bound()
    value["generation"] = hashlib.sha256(config.read_bytes()).hexdigest()
    value["credentials"]["path"] = binding.credential_path(
        generation=value["generation"], node=value["node"], sha256=value["credentials"]["sha256"]
    )
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    boot = tmp_path / "boot"
    boot.write_text("boot")
    monkeypatch.setattr(operations, "BOOT", boot)
    monkeypatch.setattr(operations, "mutation_lock", contextlib.nullcontext)
    monkeypatch.setattr(operations, "require_idle", lambda: None)
    monkeypatch.setattr(module, "require_quiescent", lambda cfg: None)
    monkeypatch.setattr(module.OrdinaryCloudRoutes, "_credentials", lambda *args: None)
    protected = set()
    monkeypatch.setattr(
        module.ordinary,
        "destination_protection",
        lambda cfg: SimpleNamespace(verify=lambda: None, admit=protected.update),
    )
    learned = {"peer": (dynamic_prefix,)}

    class Discovery:
        def __init__(self, *_args):
            pass

        def observe(self):
            return {**observe(), "policy": policy, "active_tables": ("table",)}

        def learned(self, _policy):
            if "unknown" in learned:
                raise RuntimeError("unknown peer observation")
            return learned

        def encrypted(self, selected):
            assert set(selected) <= (
                {dynamic_prefix, "192.168.1.128/25"} if mixed_summary else {dynamic_prefix}
            )

    root = tmp_path / "runtime"
    owner = RouteAuthority(root)
    owner.reserve("1" * 64, digest(owner.snapshot()))
    owner.release("1" * 64, value, None)
    runtime = module.OrdinaryCloudRoutes(
        config_path=config,
        binding_path=path,
        root=root,
        sdk_factory=lambda *a, **kw: SimpleNamespace(client=object(), close=lambda: None),
        discovery_factory=Discovery,
    )
    assert runtime.step()["cloud_routes"] == "verified"
    assert len(cloud.routes) == 1 and protected == {dynamic_prefix}
    cloud.routes["external"] = external_route("172.16.0.0/24", "foreign")
    learned["peer"] = ()
    runtime.step()
    runtime.step()
    assert len(cloud.routes) == (3 if mixed_summary else 2)
    learned["unknown"] = ()
    with pytest.raises(RuntimeError, match="unknown"):
        runtime.step()
    assert len(cloud.routes) == (3 if mixed_summary else 2)
    del learned["unknown"]
    runtime.step()
    runtime.step()
    assert len(cloud.routes) == (3 if mixed_summary else 2)
    runtime.step()
    assert {r.spec.destination.cidr for r in cloud.routes.values()} == (
        {"172.16.0.0/24", "192.168.1.128/25"} if mixed_summary else {"172.16.0.0/24"}
    )
    assert protected == {dynamic_prefix}  # Withdrawal never retires destination protection.
    assert len(owner.journal(value).inventory()) == (1 if mixed_summary else 0)
    runtime.close()


def test_runtime_status_never_reuses_stale_or_reserved_success(tmp_path, monkeypatch):
    import hashlib
    import json

    from nebius_vpngw import ordinary_operations as operations
    from nebius_vpngw.agent import ordinary_cloud_routes as module
    from nebius_vpngw.agent.vm_ha.store import atomic_write_json

    config = tmp_path / "config"
    config.write_bytes(b"exact generation")
    boot = tmp_path / "boot"
    boot.write_text("boot")
    monkeypatch.setattr(operations, "BOOT", boot)
    monkeypatch.setattr(module.time, "time", lambda: 100.0)
    value = bound()
    value["generation"] = hashlib.sha256(config.read_bytes()).hexdigest()
    value["credentials"]["path"] = binding.credential_path(
        generation=value["generation"], node=value["node"], sha256="e" * 64
    )
    path = tmp_path / "binding"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    owner = RouteAuthority(tmp_path / "runtime")
    owner.reserve("1" * 64, digest(owner.snapshot()))
    owner.release("1" * 64, value, None)

    def read():
        return module.status(config_path=config, binding_path=path, root=owner.root)

    success = {
        "generation": value["generation"],
        "boot_id": "boot",
        "observed_at": 99,
        "cloud_routes": "verified",
        "routes": 2,
    }
    atomic_write_json(owner.root / "status.json", success)
    assert read()["cloud_routes"] == "verified" and read()["connectivity"] == "unknown"
    atomic_write_json(owner.root / "status.json", {**success, "observed_at": 10})
    assert read()["reason"] == "stale-observation"
    atomic_write_json(owner.root / "status.json", success)
    owner.reserve("2" * 64, digest(owner.snapshot()))
    assert read()["reason"] == "workstation-reserved"
    config.write_bytes(b"changed configuration")
    assert read()["cloud_routes"] == "unknown"
    owner.retire("2" * 64, hashlib.sha256(config.read_bytes()).hexdigest(), None)
    assert read()["reason"] == "deployment-owned"
    assert read()["cloud_routes"] == "unknown"
