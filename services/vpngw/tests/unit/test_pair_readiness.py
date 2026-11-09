import hashlib
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import yaml

from nebius_vpngw import pair_readiness as readiness


def test_readiness_diagnostics_never_copy_untrusted_exception_text():
    def unavailable():
        raise RuntimeError("TOP_SECRET remote exception")

    with pytest.raises(readiness.ReadinessFailure) as error:
        readiness.checked("pair-cloud", "cloud-observation-unavailable", unavailable)
    assert "TOP_SECRET" not in str(error.value)
    assert error.value.failure.phase == "pair-cloud"
    with pytest.raises(readiness.ReadinessFailure) as outer:
        readiness.checked(
            "pair-configuration",
            "configuration-unavailable",
            lambda: readiness.checked("pair-cloud", "cloud-observation-unavailable", unavailable),
        )
    assert outer.value.failure == error.value.failure


def route(prefix="10.20.0.0/16", allocation="allocation"):
    return NS(spec=NS(destination=NS(cidr=prefix), next_hop=NS(allocation=NS(id=allocation))))


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([], "missing"),
        ([route(allocation="foreign")], "next hop"),
        ([route(), route()], "missing"),
        ([route(), route("10.20.1.0/24", "foreign")], "overriding"),
    ],
)
def test_ordinary_cloud_readiness_requires_complete_unambiguous_coverage(rows, reason):
    observed = {
        "allocations": {0: "allocation"},
        "active_tables": ["table"],
        "routes": {"table": rows},
    }
    with pytest.raises(RuntimeError, match=reason):
        readiness.cloud_coverage(observed, ["10.20.0.0/16"])


def test_cloud_readiness_checks_every_workload_target():
    observed = {
        "allocations": {0: "allocation"},
        "active_tables": ["a", "b"],
        "routes": {"a": [route()], "b": []},
    }
    with pytest.raises(RuntimeError, match="missing"):
        readiness.cloud_coverage(observed, ["10.20.0.0/16"])
    observed["routes"]["b"] = [route()]
    readiness.cloud_coverage(observed, ["10.20.0.0/16"])


def test_ha_readiness_keeps_required_prefix_gate_beyond_existing_route_health(monkeypatch):
    from nebius_vpngw import cli

    monkeypatch.setattr(cli, "_vm_ha_cloud_authority", lambda *a: NS(condition="exact"))
    observed = {
        "route_targets": [{"route_table_id": "table"}],
        "routes": [
            {"route_table_id": "table", "prefix": "10.30.0.0/16", "allocation_id": "shared"}
        ],
    }
    with pytest.raises(RuntimeError, match="lacks its exact"):
        readiness.ha_cloud_coverage(NS(allocation_id="shared"), observed, ["10.20.0.0/16"])
    observed["routes"][0]["prefix"] = "10.20.0.0/16"
    readiness.ha_cloud_coverage(NS(allocation_id="shared"), observed, ["10.20.0.0/16"])
    monkeypatch.setattr(cli, "_vm_ha_cloud_authority", lambda *a: NS(condition="unknown"))
    with pytest.raises(RuntimeError, match="not exact"):
        readiness.ha_cloud_coverage(NS(allocation_id="shared"), observed, ["10.20.0.0/16"])


def test_native_readiness_reuses_encryption_and_protection_without_repair(tmp_path, monkeypatch):
    from pathlib import Path

    from nebius_vpngw.agent import ordinary, ordinary_cloud_routes

    path = tmp_path / "config"
    cfg = {"connections": []}
    raw = yaml.safe_dump(cfg).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(ordinary, "CONFIG", path)
    read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **kw: (
            "1" if str(self) == "/proc/sys/net/ipv4/ip_forward" else read(self, *a, **kw)
        ),
    )
    protection = NS(verify=Mock())
    monkeypatch.setattr(ordinary, "destination_protection", lambda _: protection)
    encrypted = Mock()
    monkeypatch.setattr(ordinary_cloud_routes, "encrypted_routes", encrypted)
    result = readiness.native(hashlib.sha256(raw).hexdigest(), ["10.20.0.0/16"])
    assert result["encrypted_routes"] == "verified"
    encrypted.assert_called_once_with(cfg, ["10.20.0.0/16"])
    protection.verify.assert_called_once()
    encrypted.side_effect = RuntimeError("exact CHILD mismatch")
    with pytest.raises(RuntimeError, match="CHILD mismatch"):
        readiness.native(hashlib.sha256(raw).hexdigest(), ["10.20.0.0/16"])
    with pytest.raises(RuntimeError, match="generation changed"):
        readiness.native("0" * 64, ["10.20.0.0/16"])


@pytest.fixture
def installed_ha(monkeypatch):
    from dataclasses import replace

    from test_ssh_push import _vm_ha_manifest_and_binding
    from test_vm_ha_prehost import fixture

    from nebius_vpngw.deploy.ssh_push import SSHPush
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus
    from nebius_vpngw.schema import VMHARuntimeBinding

    state, observation, _, hosts = fixture()
    state = replace(state, status=VMHALifecycleStatus.ACTIVE)
    template, _ = _vm_ha_manifest_and_binding()
    generation = template.vm_ha_generation
    binding = VMHARuntimeBinding.model_validate(hosts["gateway-0"]["runtime_binding"])
    binding = binding.model_copy(
        update={
            "static_routes_digest": generation.digests.static_routes,
            "bgp_policy_digest": generation.digests.bgp_policy,
        }
    )
    instances = []
    for member in state.members:
        node = replace(
            template.vm_ha_node,
            node_id=member.node_id,
            instance_index=member.instance_index,
            role=type(template.vm_ha_node.role)(member.role),
        )
        ready = replace(template.vm_ha_readiness, required_node_ids=("node-0", "node-1"))
        payload = yaml.safe_load(template.config_yaml)
        payload["vm_ha"]["cluster_id"] = state.cluster_id
        payload["vm_ha"]["node"] = hosts[member.instance_name]["node"]
        instance = replace(
            template,
            hostname=member.instance_name,
            instance_index=member.instance_index,
            vm_ha_node=node,
            vm_ha_readiness=ready,
            config_yaml=yaml.safe_dump(payload, sort_keys=False),
        )
        raw = SSHPush._render_vm_ha_config(instance, binding)
        host = hosts[member.instance_name]
        host["runtime_binding"] = binding.model_dump(mode="json")
        host["generation"]["digests"] = generation.digests.__dict__
        host["readiness"]["digests"] = generation.digests.__dict__
        host["config_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        instances.append(instance)
    monkeypatch.setattr(
        SSHPush, "inspect_vm_ha_prehost", lambda self, target, name, local: hosts[name]
    )
    context = NS(policies={name: object() for name in hosts})
    targets = {name: "target-" + name for name in hosts}
    return state, observation, hosts, tuple(instances), context, targets


def test_paired_ha_observation_hashes_actual_installed_renderer(
    installed_ha, monkeypatch, tmp_path
):
    import json

    from nebius_vpngw import cli, config_loader

    state, observation, hosts, instances, context, targets = installed_ha
    local = {
        "project_id": state.project_id,
        "gateway": {"local_prefixes": ["10.0.0.0/24"]},
        "connections": [{"remote_prefixes": ["10.20.0.0/16"]}],
    }
    plan = NS(vm_ha=object(), gateway_group=object(), iter_instance_configs=lambda: instances)
    observation["routes"][0].update(prefix="10.20.0.0/16", allocation_id=state.allocation_id)
    manager = NS(observe_vm_ha_migration_state=lambda *a: observation)
    inspected = NS(
        snapshot=NS(
            view=NS(overall="HEALTHY"),
            lifecycle_state=state,
            authority=NS(owner_name="gateway-0", ownership_digest="a" * 64),
        ),
        targets=targets,
        ssh_context=context,
    )
    monkeypatch.setattr(config_loader, "load_local_config", lambda path: local)
    monkeypatch.setattr(config_loader, "merge_with_peer_configs", lambda *a: plan)
    monkeypatch.setattr(readiness, "_manager", lambda *a: (manager, None))
    monkeypatch.setattr(cli, "_inspect_vm_ha_command_status", lambda *a, **kw: inspected)
    monkeypatch.setattr(cli, "_vm_ha_cloud_authority", lambda *a: NS(condition="exact"))
    monkeypatch.setattr(cli, "_status_ssh_target_command", lambda *a, **kw: ["ssh", "fixture"])
    requests = []

    def remote(argv, **kwargs):
        request = json.loads(kwargs["input"])
        requests.append(request)
        assert request["generation"] == hosts["gateway-0"]["config_sha256"]
        assert (
            request["generation"] != hashlib.sha256(instances[0].config_yaml.encode()).hexdigest()
        )
        return NS(returncode=0, stdout=kwargs["input"])

    monkeypatch.setattr(readiness.subprocess, "run", remote)
    assert readiness._observe(tmp_path / "config.yaml")["connection"] == "ready"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "drift", ["credential", "allocation", "member", "generation", "config", "peer"]
)
def test_paired_ha_render_refuses_foreign_or_inconsistent_authority(installed_ha, drift):
    state, _, hosts, instances, context, targets = installed_ha
    host = hosts["gateway-0"]
    if drift == "credential":
        host["credential_sha256"] = "f" * 64
    elif drift == "allocation":
        host["runtime_binding"]["shared_allocation_id"] = "foreign"
    elif drift == "member":
        host["runtime_binding"]["nodes"][0]["compute_id"] = "foreign"
    elif drift == "generation":
        for item in hosts.values():
            item["runtime_binding"]["generation_id"] = "f" * 64
            item["generation"]["generation_id"] = "f" * 64
            item["readiness"]["generation_id"] = "f" * 64
    elif drift == "config":
        host["config_sha256"] = "f" * 64
    else:
        hosts["gateway-1"]["runtime_binding"]["nodes"][0]["peer_endpoint"] = "192.0.2.99:9443"
    with pytest.raises((ValueError, RuntimeError)):
        readiness._ha_instance(context, state, instances, targets, {}, "gateway-0")
