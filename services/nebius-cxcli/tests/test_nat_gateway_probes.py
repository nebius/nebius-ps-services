from __future__ import annotations

import copy
import json
import time
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_probes as probes
from nebius_cxcli.nat_gateway import fingerprint, verification_enabled
from nebius_cxcli.nat_gateway_probe_resources import ProbeResources
from nebius_cxcli.nat_gateway_probe_result import accepted_report
from nebius_cxcli.ssh_host_identity import generate_host_identity
from test_nat_gateway import config, manager_fixture

CLIENT_PUBLIC_KEY = generate_host_identity()["ed25519_public"]


def probe_fixture():
    now = time.time()
    return {
        "started_at": now - 10,
        "subnet_id": "subnet-work",
        "config": {
            "run_id": "a" * 32,
            "program_sha256": "f" * 64,
            "activation": "activation",
            "expected_ip": "203.0.113.9",
            "endpoint_ip": "1.1.1.1",
        },
        "shape": {"image": "image-test", "disk_gib": 20, "platform": "cpu-test", "preset": "small"},
        "resources": {"instance": {"id": "instance-probe"}},
    }


def evidence(probe):
    report = {
        "schema": 1,
        "run_id": probe["config"]["run_id"],
        "activation": "activation",
        "config_sha256": fingerprint(probe["config"]),
        "program_sha256": probe["config"]["program_sha256"],
        "boot_id": "01234567-89ab-cdef-0123-456789abcdef",
        "boot_time": probe["started_at"] + 1,
        "finished_at": time.time(),
        "endpoint_ip": "1.1.1.1",
        "observed_ip": "203.0.113.9",
        "ok": True,
    }
    return json.dumps(report).encode()


def test_verification_defaults_selected_but_accepts_explicit_opt_out():
    row = config()["infra"]["components"][0]
    assert not verification_enabled(row)
    row["routing"].pop("verification")
    assert verification_enabled(row)
    row["routing"]["verification"] = {"enabled": "false"}
    with pytest.raises(ValueError):
        verification_enabled(row)


def test_probe_result_requires_current_run_and_fresh_boot():
    probe = probe_fixture()
    assert accepted_report(evidence(probe), probe)["ok"]
    stale = json.loads(evidence(probe))
    stale["boot_time"] = probe["started_at"] - 20
    with pytest.raises(RuntimeError, match="Stale"):
        accepted_report(json.dumps(stale).encode(), probe)
    probe["config"]["run_id"] = "b" * 32
    stale["boot_time"] = probe["started_at"] + 1
    with pytest.raises(RuntimeError, match="mismatched"):
        accepted_report(json.dumps(stale).encode(), probe)
    with pytest.raises(RuntimeError, match="empty"):
        accepted_report(b"", probe)


def test_probe_cannot_succeed_through_retained_exception_route():
    routes = [{"destination_cidr": "0.0.0.0/0"}, {"destination_cidr": "1.0.0.0/8"}]
    assert probes.endpoint_for(routes, ["1.1.1.1", "8.8.8.8"]) == "8.8.8.8"
    with pytest.raises(RuntimeError, match="retained"):
        probes.endpoint_for(routes, ["1.1.1.1", "127.0.0.1"])


def test_unknown_creation_never_adopts_or_deletes_visible_resource(monkeypatch):
    probe = probe_fixture()
    probe["resources"] = {"disk": {"name": "probe", "id": "", "phase": "submitted"}}
    resources = ProbeResources(NS(), probe, lambda: None, lambda: None)
    monkeypatch.setattr(
        resources, "read", lambda *a: pytest.fail("Unknown request must first be reconciled")
    )
    with pytest.raises(RuntimeError, match="unknown"):
        resources.create("disk", {})
    with pytest.raises(RuntimeError, match="unknown"):
        resources.cleanup()
    assert probe["cleanup"] == "pending"


def test_confirmed_settled_reconciliation_checks_ownership(monkeypatch):
    probe = probe_fixture()
    probe["resources"] = {"disk": {"name": "probe", "id": "", "phase": "submitted"}}
    resources = ProbeResources(NS(), probe, lambda: None, lambda: None)
    checked = []
    monkeypatch.setattr(resources, "read", lambda *a: NS(metadata=NS(id="disk-exact")))
    monkeypatch.setattr(resources, "verify", lambda *args: checked.append(args[0]))
    resources.reconcile()
    assert checked == ["disk"]
    assert probe["resources"]["disk"]["id"] == "disk-exact"
    assert probe["resources"]["disk"]["phase"] == "created"


@pytest.mark.parametrize("size", [{"size_gibibytes": "20"}, {"size_bytes": str(20 * 1024**3)}])
def test_disk_verification_accepts_supported_size_oneof(size, monkeypatch):
    probe = probe_fixture()
    resources = ProbeResources(NS(project_id="project-test"), probe, lambda: None, lambda: None)
    obj = {
        "metadata": {
            "id": "disk-exact",
            "parent_id": "project-test",
            "name": "probe",
            "labels": {"cxcli-nat-probe": "a" * 32},
        },
        "spec": {"source_image_id": "image-test", **size},
    }
    monkeypatch.setattr(
        "nebius_cxcli.nat_gateway_probe_resources.sdk_message_to_mapping", lambda value: value
    )
    resources.verify("disk", obj, {"id": "disk-exact", "name": "probe"})
    obj["metadata"]["labels"] = {}
    with pytest.raises(RuntimeError, match="ownership"):
        resources.verify("disk", obj, {"id": "disk-exact", "name": "probe"})


def test_probe_requests_private_only_and_clean_exact_reverse_dependency_order(monkeypatch):
    from nebius.api.nebius.compute.v1 import CreateDiskRequest, CreateInstanceRequest
    from nebius.api.nebius.vpc.v1 import CreateAllocationRequest

    from nebius_cxcli.nebius_api_helpers import sdk_message_to_mapping, sdk_parse_message

    probe = probe_fixture()
    probe["resources"] = {}
    resources = ProbeResources(NS(), probe, lambda: None, lambda: None)
    requests = []
    types = {
        "disk": CreateDiskRequest,
        "instance": CreateInstanceRequest,
        "allocation": CreateAllocationRequest,
    }

    def create(kind, spec):
        request = sdk_parse_message(
            types[kind], {"metadata": {"parent_id": "project-test"}, "spec": spec}
        )
        requests.append((kind, sdk_message_to_mapping(request)["spec"]))
        probe["resources"][kind] = {"id": kind + "-exact"}
        return kind + "-exact"

    monkeypatch.setattr(resources, "create", create)
    resources.provision(
        probes.cloud_init(
            probe["config"], public_key=CLIENT_PUBLIC_KEY, host_identity=generate_host_identity()
        )
    )
    assert [kind for kind, _ in requests] == ["disk", "allocation", "instance"]
    nic = requests[2][1]["network_interfaces"][0]
    assert "public_ip_address" not in nic
    assert nic["ip_address"]["allocation_id"] == "allocation-exact"
    assert requests[2][1]["boot_disk"]["existing_disk"]["id"] == "disk-exact"
    assert "packages:" not in requests[2][1]["cloud_init_user_data"]


def test_preflight_requires_temporary_allocation_quota(monkeypatch):
    row = config()["infra"]["components"][0]
    row["routing"]["verification"] = {"enabled": True}
    demands = []
    monkeypatch.setattr(probes, "probe_shape", lambda *a: {"vcpu": 2, "disk_gib": 20})
    monkeypatch.setattr(
        probes, "assess_live_quota_requirements", lambda **kw: demands.extend(kw["requirements"])
    )
    monkeypatch.setattr(
        probes,
        "assert_quota_resolved",
        lambda _: (_ for _ in ()).throw(RuntimeError("quota insufficient")),
    )
    with pytest.raises(RuntimeError, match="quota insufficient"):
        probes.preflight(
            NS(project_id="project-test"), row, {"outputs": {"instance_id": "gateway"}}, config()
        )
    assert {r.quota_name: r.required for r in demands}["vpc.allocation.count"] == 1


@pytest.mark.parametrize("prior", ["table-original", "table-nat"])
def test_failed_probe_restores_activation_preimage_not_lifetime_original(monkeypatch, prior):
    manager, _sdk, record, target, live, _effects = manager_fixture(monkeypatch, current=prior)
    target["installed"] = prior if prior != target["original"] else ""
    manager.rows[0]["routing"]["verification"] = {"enabled": True}
    monkeypatch.setattr(probes, "preflight", lambda *a: {"ready": True})
    monkeypatch.setattr(
        probes, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("probe failed"))
    )
    with pytest.raises(RuntimeError, match="probe failed"):
        manager.complete()
    assert live["route"] == prior
    assert record["activation"]["phase"] == "restored"
    assert target["pending"] is None


def test_failed_probe_preserves_concurrent_attachment_and_records_recovery(monkeypatch):
    manager, _sdk, record, target, live, _effects = manager_fixture(monkeypatch)
    monkeypatch.setattr(probes, "preflight", lambda *a: {"ready": True})
    cleaned = []

    def failed(*args, **kwargs):
        live["route"] = "table-third-party"
        raise RuntimeError("probe failed")

    monkeypatch.setattr(probes, "run", failed)
    monkeypatch.setattr(probes, "cleanup", lambda *a, **kw: cleaned.append(True))
    with pytest.raises(RuntimeError, match="pending"):
        manager.complete()
    assert live["route"] == "table-third-party" and cleaned
    assert record["activation"]["phase"] == "restoration_pending"


def test_probe_runs_in_every_subnet_and_cleans_before_next(monkeypatch):
    probe = probe_fixture()
    record = {
        "activation": {"id": "activation", "probes": {}},
        "network_id": "network",
        "outputs": {"public_ip": "203.0.113.9/32"},
        "targets": {"one": {"subnet_id": "subnet-one"}, "two": {"subnet_id": "subnet-two"}},
    }
    events = []

    class Resources:
        def __init__(self, sdk, probe, *args):
            self.probe = probe

        def provision(self, data):
            events.append(("create", self.probe["subnet_id"]))

        def cleanup(self):
            events.append(("clean", self.probe["subnet_id"]))
            self.probe["cleanup"] = "complete"

    monkeypatch.setattr(probes, "ProbeResources", Resources)
    access = NS(public_key=CLIENT_PUBLIC_KEY, wait=lambda *a, **kw: {"ok": True})
    sdk = NS(project_id="project-test", require_private_capacity=lambda *a: None)
    probes.run(
        sdk,
        record,
        {"shape": probe["shape"], "endpoints": {"one": "1.1.1.1", "two": "1.1.1.1"}, "ssh": access},
        save=lambda: None,
        assert_held=lambda: None,
    )
    assert events == [
        ("create", "subnet-one"),
        ("clean", "subnet-one"),
        ("create", "subnet-two"),
        ("clean", "subnet-two"),
    ]
    assert len({p["config"]["run_id"] for p in record["activation"]["probes"].values()}) == 2


def test_ssh_transport_timeout_reaches_probe_cleanup(monkeypatch):
    record = {
        "activation": {"id": "activation", "probes": {}},
        "network_id": "network",
        "outputs": {"public_ip": "203.0.113.9/32"},
        "targets": {"one": {"subnet_id": "subnet-one"}},
    }
    events = []

    class Resources:
        def __init__(self, sdk, probe, *args):
            self.probe = probe

        def provision(self, data):
            events.append("created")
            self.probe["resources"]["instance"] = {"id": "instance-probe"}

        def cleanup(self):
            events.append("cleaned")
            self.probe["cleanup"] = "complete"

    def timeout(*args, **kwargs):
        raise RuntimeError("NAT probe SSH result timeout")

    monkeypatch.setattr(probes, "ProbeResources", Resources)
    access = NS(public_key=CLIENT_PUBLIC_KEY, wait=timeout)
    sdk = NS(project_id="project-test", require_private_capacity=lambda *a: None)
    with pytest.raises(RuntimeError, match="SSH result timeout"):
        probes.run(
            sdk,
            record,
            {"shape": probe_fixture()["shape"], "endpoints": {"one": "1.1.1.1"}, "ssh": access},
            save=lambda: None,
            assert_held=lambda: None,
        )
    assert events == ["created", "cleaned"]
    assert record["activation"]["probes"]["one"]["cleanup"] == "complete"


def test_recovery_does_not_forget_unknown_external_request(monkeypatch):
    manager, _sdk, record, target, live, _effects = manager_fixture(monkeypatch)
    record["activation"] = {
        "id": "run",
        "phase": "activating",
        "previous_phase": "prepared",
        "attachments": {
            "target": {
                "before": "table-original",
                "effective_before": "table-original",
                "desired": "table-nat",
            }
        },
        "probes": {},
    }
    target["pending"] = {"expected": "table-original", "desired": "table-nat", "operation_id": ""}
    for _ in range(2):
        with pytest.raises(RuntimeError, match="incomplete"):
            manager.restore_activations()
        assert target["pending"] is not None
    live["route"] = "table-nat"
    with pytest.raises(RuntimeError, match="incomplete"):
        manager.restore_activations()
    assert target["pending"] is not None and live["route"] == "table-nat"


def test_inherited_route_restoration_admits_provider_null():
    from nebius_cxcli.nat_gateway_state import assert_activation_plan

    assert_activation_plan(
        {
            "resource_changes": [
                {
                    "type": "nebius_vpc_v1_subnet",
                    "mode": "managed",
                    "address": "module.vpc.work",
                    "change": {
                        "actions": ["update"],
                        "before": {"id": "subnet-work", "route_table_id": "table-nat"},
                        "after": {"id": "subnet-work", "route_table_id": None},
                    },
                }
            ]
        },
        {"subnet-work": ""},
    )


def test_cleanup_attempted_after_operation_read_failure_and_skips_retired(monkeypatch):
    manager, sdk, record, target, _live, _effects = manager_fixture(monkeypatch)
    record["activation"] = {
        "id": "run",
        "phase": "activating",
        "previous_phase": "prepared",
        "attachments": {
            "target": {
                "before": "table-original",
                "effective_before": "table-original",
                "desired": "table-nat",
            }
        },
        "probes": {},
    }
    target["pending"] = {
        "expected": "table-original",
        "desired": "table-nat",
        "operation_id": "operation",
    }
    retired = copy.deepcopy(record)
    retired.pop("activation")
    retired["phase"] = "retired"
    retired["targets"]["target"]["subnet_id"] = "deleted-subnet"
    manager.state.value["gateways"]["retired"] = retired
    original_subnet = type(sdk).subnet

    def subnet(self, identifier, network):
        assert identifier != "deleted-subnet"
        return original_subnet(self, identifier, network)

    cleaned = []
    monkeypatch.setattr(type(sdk), "subnet", subnet)
    monkeypatch.setattr(
        type(sdk), "wait_operation", lambda *a: (_ for _ in ()).throw(RuntimeError("read failed"))
    )
    monkeypatch.setattr(probes, "cleanup", lambda *a, **kw: cleaned.append(True))
    with pytest.raises(RuntimeError, match="incomplete"):
        manager.restore_activations()
    assert cleaned and target["pending"] is not None


def test_terminal_failed_creation_without_resource_id_allows_child_cleanup(monkeypatch):
    from nebius_cxcli.nat_gateway_sdk import NatOperationFailed

    probe = probe_fixture()
    probe["resources"] = {
        "instance": {"id": "", "name": "probe", "phase": "submitted", "operation_id": "failed"},
        "disk": {"id": "disk", "phase": "deleting", "delete_operation_id": "done"},
        "allocation": {"id": "allocation", "phase": "deleting", "delete_operation_id": "done"},
    }
    sdk = NS(wait_operation=lambda _: (_ for _ in ()).throw(NatOperationFailed("terminal failure")))
    resources = ProbeResources(sdk, probe, lambda: None, lambda: None)
    observed = []
    monkeypatch.setattr(resources, "read", lambda kind, entry: observed.append(kind))
    resources.cleanup()
    assert observed == ["instance", "disk", "allocation"] and probe["cleanup"] == "complete"


def test_readded_retired_name_does_not_read_historical_targets(monkeypatch):
    manager, sdk, record, target, _live, _effects = manager_fixture(monkeypatch)
    record["activation"] = {
        "id": "run",
        "phase": "activating",
        "previous_phase": "prepared",
        "attachments": {},
        "probes": {},
    }
    retired = copy.deepcopy(record)
    retired.pop("activation")
    retired["phase"] = "retired"
    retired["targets"]["target"]["subnet_id"] = "deleted-subnet"
    manager.state.value["gateways"]["readded"] = retired
    manager.rows.append({"instance_id": "readded"})
    original = type(sdk).subnet

    def read(self, identifier, network):
        assert identifier != "deleted-subnet"
        return original(self, identifier, network)

    monkeypatch.setattr(type(sdk), "subnet", read)
    manager.restore_activations()
    assert record["activation"]["phase"] == "restored"


def test_inherited_restore_rejects_unknown_route_value():
    from nebius_cxcli.nat_gateway_state import assert_activation_plan

    change = {
        "actions": ["update"],
        "before": {"id": "subnet", "route_table_id": "nat"},
        "after": {"id": "subnet", "route_table_id": None},
        "after_unknown": {"route_table_id": True},
    }
    with pytest.raises(RuntimeError, match="unapproved"):
        assert_activation_plan(
            {"resource_changes": [{"type": "nebius_vpc_v1_subnet", "change": change}]},
            {"subnet": ""},
        )


@pytest.mark.parametrize("boundary", ["prepare", "plan", "retire"])
@pytest.mark.parametrize("phase", ["restoration_pending", "restored", "complete"])
def test_removed_gateway_cannot_bypass_pending_probe_cleanup(monkeypatch, boundary, phase):
    manager, _sdk, record, _target, _live, _effects = manager_fixture(monkeypatch, enabled=False)
    record["phase"] = "detached"
    record["activation"] = {
        "phase": phase,
        "previous_phase": "detached",
        "attachments": {},
        "probes": {"test": {"cleanup": "pending"}},
    }
    manager.rows = []
    manager.executor.config["infra"]["components"] = []
    attempted = []

    def restore():
        attempted.append(True)
        raise RuntimeError("probe cleanup remains pending")

    monkeypatch.setattr(manager, "restore_activations", restore)
    with pytest.raises(RuntimeError, match="pending|unfinished"):
        if boundary == "prepare":
            manager.prepare()
        elif boundary == "plan":
            manager.check_plan(
                {
                    "resource_changes": [
                        {
                            "address": f"module.{record['module_name']}.instance",
                            "change": {
                                "actions": ["delete"],
                                "before": {"labels": {"cxcli-component": "nat-gateway"}},
                            },
                        }
                    ]
                }
            )
        else:
            manager.finalize_retirement()
    assert record["phase"] == "detached"
    if boundary == "prepare":
        assert attempted


def test_removed_gateway_recovers_unknown_probe_before_retirement(monkeypatch):
    manager, _sdk, record, _target, _live, _effects = manager_fixture(monkeypatch, enabled=False)
    record["phase"] = "detached"
    probe = probe_fixture()
    probe["cleanup"] = "pending"
    probe["resources"] = {"disk": {"phase": "submitted", "id": "", "name": "probe"}}
    record["activation"] = {
        "phase": "restoration_pending",
        "previous_phase": "detached",
        "attachments": {},
        "probes": {"test": probe},
    }
    manager.rows = []
    manager.executor.config["infra"]["components"] = []
    with pytest.raises(RuntimeError, match="probe cleanup pending"):
        manager.prepare()
    assert record["phase"] == "detached" and probe["cleanup"] == "pending"
    # Model authoritative settled-request reconciliation confirming absence.
    probe["resources"]["disk"]["phase"] = "deleted"
    with pytest.raises(RuntimeError, match="rerun deploy"):
        manager.prepare()
    assert probe["cleanup"] == "complete" and record["activation"]["phase"] == "restored"
    manager.prepare()
    manager.finalize_retirement()
    assert record["phase"] == "retired"
