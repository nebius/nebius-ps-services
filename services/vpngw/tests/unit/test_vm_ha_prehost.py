from __future__ import annotations

import copy
import fcntl
import json
import subprocess
import sys
from dataclasses import replace

import pytest
import yaml

from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint, HAState
from nebius_vpngw.cli import (
    _vm_ha_initial_resource_bindings,
    _vm_ha_prehost_recovery_approval_state,
)
from nebius_vpngw.deploy.ssh_push import _VM_HA_PREHOST_INSPECTION
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStore,
    normalize_vm_ha_observation,
)
from nebius_vpngw.deploy.vm_ha_prehost import (
    PREHOST_EFFECTS,
    _credential_observation_digest,
    recover_prehost,
    validate_prehost_cloud,
    validate_prehost_hosts,
    validate_retained_route_scope,
)
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding, VMHARuntimeNodeBinding
from nebius_vpngw.vm_ha_credentials import installed_vm_ha_credential_path


def fixture():
    target = VMHARouteTarget(
        project_id="project",
        network_id="network",
        workload_subnet_id="subnet",
        route_table_id="table",
    )
    members = tuple(
        VMHALifecycleMember(
            instance_index=i,
            instance_name=f"gateway-{i}",
            node_id=f"node-{i}",
            role="active" if i == 0 else "passive",
            compute_id=f"vm-{i}",
            network_interface_name="eth0",
            public_ip=f"192.0.2.{i + 1}",
            compute_revision="7",
            disk_id=f"disk-{i}",
            network_interface_subnet_id="gateway-subnet",
            primary_allocation_id=f"primary-{i}",
            public_allocation_id=f"public-{i}",
            alias_allocation_ids=("allocation",) if i == 0 else (),
        )
        for i in range(2)
    )
    observation = {
        "project_id": "project",
        "members": [
            {
                "instance_name": m.instance_name,
                "compute_id": m.compute_id,
                "compute_revision": m.compute_revision,
                "boot_disk_id": m.disk_id,
                "network_interface_name": m.network_interface_name,
                "public_ip": m.public_ip,
                "subnet_id": m.network_interface_subnet_id,
                "primary_allocation_id": m.primary_allocation_id,
                "public_allocation_id": m.public_allocation_id,
                "aliases": list(m.alias_allocation_ids),
                "present": True,
                "state": "running",
            }
            for m in members
        ],
        "shared_allocation": {
            "allocation_id": "allocation",
            "present": True,
            "resource_revision": "4",
            "owner": {"compute_id": "vm-0", "network_interface_name": "eth0"},
        },
        "route_targets": [target.model_dump(mode="json")],
        "routes": [
            {
                "route_table_id": "table",
                "route_id": "route",
                "revision": "2",
                "prefix": "10.0.0.0/24",
                "authority_labels": {"managed": "yes"},
            }
        ],
    }
    runtime_id = VMHARuntimeBinding.derive_route_runtime_id("cluster", "allocation", (target,))
    bindings = _vm_ha_initial_resource_bindings(
        observation,
        credential_bindings={
            "credential-sha256:node-0": "d" * 64,
            "credential-sha256:node-1": "d" * 64,
            "credential-service-account:node-0": "service-account",
            "credential-service-account:node-1": "service-account",
            "credential-authorized-key:node-0": "key",
            "credential-authorized-key:node-1": "key",
        },
    )
    bindings["route-runtime-id"] = runtime_id
    state = VMHALifecycleState.start_provisioning(
        project_id="project",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="old-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        initial_resource_bindings=bindings,
        current_observation=observation,
    )
    state = replace(
        state,
        allocation_id="allocation",
        route_runtime_id=runtime_id,
        route_targets=(
            json.dumps(target.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        ),
        transaction=replace(
            state.transaction,
            completed_effects=tuple(sorted(PREHOST_EFFECTS)),
            checkpoint="after-construct-authoritative-runtime-binding",
            current_state_digest=_credential_observation_digest(state, observation),
        ),
    )
    binding = VMHARuntimeBinding(
        cluster_id="cluster",
        shared_allocation_id="allocation",
        route_runtime_id=runtime_id,
        route_targets=(target,),
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nebius_project_id="project",
        nebius_service_account_id="service-account",
        nebius_authorized_key_id="key",
        nodes=tuple(
            VMHARuntimeNodeBinding(
                node_id=m.node_id,
                nebius_credentials_sha256="d" * 64,
                nebius_credentials_path=installed_vm_ha_credential_path(
                    node_id=m.node_id, generation_id="a" * 64, credential_sha256="d" * 64
                ),
                role=m.role,
                compute_id=m.compute_id,
                network_interface_name=m.network_interface_name,
                peer_endpoint=f"https://192.0.2.{i + 1}:9443",
            )
            for i, m in enumerate(members)
        ),
    )
    hosts = {
        m.instance_name: {
            "node_id": m.node_id,
            "node": {"node_id": m.node_id, "instance_index": m.instance_index, "role": m.role},
            "cluster_id": "cluster",
            "readiness": {
                "generation_id": "a" * 64,
                "digests": {
                    "configuration": "a" * 64,
                    "static_routes": "b" * 64,
                    "bgp_policy": "c" * 64,
                },
                "required_node_ids": ["node-0", "node-1"],
            },
            "runtime_binding": binding.model_dump(mode="json"),
            "generation": {
                "generation_id": "a" * 64,
                "digests": {
                    "configuration": "a" * 64,
                    "static_routes": "b" * 64,
                    "bgp_policy": "c" * 64,
                },
            },
            "checkpoint": controller_checkpoint_to_dict(
                ControllerCheckpoint(state=HAState.ACTIVE if i == 0 else HAState.NORMAL)
            ),
            "transfer": None,
            "rearm": None,
            "pending_files": [],
            "writer_admission": True,
            "credential_sha256": "d" * 64,
            "boot_id": f"boot-{i}",
            "config_sha256": "e" * 64,
            "package_version": "0.6.3.dev22",
            "staged_configs": {"historical.yaml": "f" * 64},
        }
        for i, m in enumerate(members)
    }
    return state, observation, bindings, hosts


def successor(state, observation, bindings, hosts):
    return recover_prehost(
        state,
        operation_id="new-operation",
        approval_digest="e" * 64,
        desired_state_digest="f" * 64,
        bindings=bindings,
        observation=observation,
        host_evidence=hosts,
    )


def test_recovery_retains_identity_and_store_enforces_cas(tmp_path):
    state, observation, bindings, hosts = fixture()
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(state)
    recovered = successor(state, observation, bindings, hosts)
    store.write_verified(recovered, predecessor_sha256=state.record_sha256)
    store.write_verified(recovered, predecessor_sha256=state.record_sha256)
    assert recovered.has_same_identity(state)
    assert recovered.transaction.desired_state_digest != state.transaction.desired_state_digest
    assert store.read(expected_project_id="project", expected_gateway_name="gateway") == recovered
    with pytest.raises(ValueError, match="compare-and-swap"):
        store.write_verified(
            replace(
                recovered,
                transaction=replace(recovered.transaction, operation_id="racing-operation"),
            ),
            predecessor_sha256=state.record_sha256,
        )
    staged = recovered.begin_effect("stage-node-1")
    store.write_verified(staged, predecessor_sha256=recovered.record_sha256)
    with pytest.raises(ValueError, match="eligible"):
        successor(staged, observation, bindings, hosts)


@pytest.mark.parametrize("scope", [[], ["added"], ["replacement"]])
def test_route_scope_rejects_changes(scope):
    state, observation, _, _ = fixture()
    changed = copy.deepcopy(observation)
    if scope == []:
        changed["route_targets"] = []
    else:
        extra = dict(
            changed["route_targets"][0], workload_subnet_id="other", route_table_id="other"
        )
        changed["route_targets"] = (
            changed["route_targets"] + [extra] if scope == ["added"] else [extra]
        )
    validate_retained_route_scope(state, observation)
    with pytest.raises(ValueError, match="scope"):
        validate_retained_route_scope(state, changed)


@pytest.mark.parametrize(
    "mutation", ["pending", "accepted", "stage", "checkpoint", "identity", "observation"]
)
def test_recovery_rejects_non_prehost_checkpoints(mutation):
    state, observation, bindings, hosts = fixture()
    changes = {
        "pending": {"pending_effect": "stage-node-1"},
        "accepted": {
            "pending_effect": "cloud",
            "accepted_cloud_operation_effect": "cloud",
            "accepted_cloud_operation_id": "operation",
        },
        "stage": {"completed_effects": tuple(sorted((*PREHOST_EFFECTS, "stage-node-1")))},
        "checkpoint": {"checkpoint": "before-stage-node-1"},
        "observation": {"observation": ()},
    }
    if mutation == "identity":
        state = replace(state, allocation_id="")
    else:
        state = replace(state, transaction=replace(state.transaction, **changes[mutation]))
    with pytest.raises(ValueError):
        successor(state, observation, bindings, hosts)


@pytest.mark.parametrize(
    "field", ["compute_revision", "compute_id", "boot_disk_id", "aliases", "public_allocation_id"]
)
def test_recovery_rejects_cloud_drift(field):
    state, observation, bindings, _ = fixture()
    observation["members"][0][field] = [] if field == "aliases" else "changed"
    with pytest.raises(ValueError, match="identity or revision"):
        validate_prehost_cloud(state, observation, bindings)


def test_recovery_rejects_route_and_credential_drift():
    state, observation, bindings, _ = fixture()
    observation["routes"][0]["revision"] = "3"
    with pytest.raises(ValueError, match="routes changed"):
        validate_prehost_cloud(state, observation, bindings)
    observation["routes"][0]["revision"] = "2"
    bindings["credential-sha256:node-0"] = "e" * 64
    with pytest.raises(ValueError, match="credential"):
        validate_prehost_cloud(state, observation, bindings)


def test_completed_effect_names_do_not_prove_reuse():
    state, observation, bindings, hosts = fixture()
    # A completed attachment can advance the cloud snapshot. Its immutable
    # pre-effect approval digest must still prove no cloud mutation occurred.
    observation["members"][0]["compute_revision"] = "8"
    state = replace(
        state,
        transaction=replace(
            state.transaction, observation=normalize_vm_ha_observation(observation)
        ),
    )
    with pytest.raises(ValueError, match="reuse only"):
        successor(state, observation, bindings, hosts)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pending_files", ["apply.lock"]),
        ("node_id", "foreign"),
        ("checkpoint", {}),
        ("generation", {}),
        ("transfer", {}),
        ("rearm", {"phase": "starting"}),
    ],
)
def test_recovery_rejects_host_drift_or_unknown(field, value):
    state, _, _, hosts = fixture()
    hosts["gateway-0"][field] = value
    with pytest.raises(ValueError):
        validate_prehost_hosts(state, hosts)


def test_store_rejects_forged_host_evidence(tmp_path):
    state, observation, bindings, hosts = fixture()
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(state)
    recovered = successor(state, observation, bindings, hosts)
    entries = dict(recovered.transaction.resource_bindings)
    entries["prehost-superseded-host-evidence"] = "{}"
    forged = replace(
        recovered,
        transaction=replace(
            recovered.transaction, resource_bindings=tuple(sorted(entries.items()))
        ),
    )
    with pytest.raises(ValueError, match="both pinned"):
        store.write_verified(forged, predecessor_sha256=state.record_sha256)


def test_expanded_failed_observation_can_restore_only_retained_scope():
    state, observation, bindings, hosts = fixture()
    expanded = copy.deepcopy(observation)
    expanded["route_targets"].append(
        dict(expanded["route_targets"][0], workload_subnet_id="extra", route_table_id="extra")
    )
    expanded["routes"].append({"route_table_id": "extra", "route_id": "unmanaged", "revision": "1"})
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            observation=normalize_vm_ha_observation(expanded),
            current_state_digest=_credential_observation_digest(state, expanded),
        ),
    )
    assert successor(state, observation, bindings, hosts).route_runtime_id == state.route_runtime_id


def test_prehost_approval_binds_semantics_without_poll_counter():
    state, observation, bindings, hosts = fixture()
    credentials = {k: v for k, v in bindings.items() if k.startswith("credential-")}
    first = _vm_ha_prehost_recovery_approval_state(
        state, observation, credential_bindings=credentials, hosts=hosts
    )
    hosts["gateway-0"]["checkpoint"]["sequence"] += 1
    assert (
        _vm_ha_prehost_recovery_approval_state(
            state, observation, credential_bindings=credentials, hosts=hosts
        )
        == first
    )
    hosts["gateway-0"]["boot_id"] = "new-boot"
    assert (
        _vm_ha_prehost_recovery_approval_state(
            state, observation, credential_bindings=credentials, hosts=hosts
        )
        != first
    )


@pytest.mark.parametrize(
    "fault", [None, "busy", "symlink", "pending", "malformed", "cluster", "role", "readiness"]
)
def test_real_prehost_file_observer_is_bounded_and_excludes_secrets(tmp_path, fault):
    state, _, _, hosts = fixture()
    host = hosts["gateway-0"]
    root = tmp_path / "state"
    root.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "vm_ha": {
                    "node": host["node"],
                    "cluster_id": host["cluster_id"],
                    "readiness": host["readiness"],
                    "generation": host["generation"],
                    "runtime_binding": host["runtime_binding"],
                },
                "tunnels": [{"psk": "secret-never-export-this-fixture-value"}],
            }
        )
    )
    (root / "controller-checkpoint.json").write_text(json.dumps(host["checkpoint"]))
    (tmp_path / "boot").write_text("boot-0\n")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "historical.yaml").write_text("psk: secret-never-export-this-fixture-value\n")
    script = (
        _VM_HA_PREHOST_INSPECTION.replace("/var/lib/nebius-vpngw/vm-ha", str(root))
        .replace("/etc/nebius-vpngw/config-resolved.yaml", str(config))
        .replace("/etc/nebius-vpngw/vm-ha-staged", str(staged))
        .replace("/proc/sys/kernel/random/boot_id", str(tmp_path / "boot"))
        .replace("time.monotonic() + 10", "time.monotonic() + 0.1")
        # This fixture exercises the file observer; installed writer admission
        # has separate runtime tests and is exercised on both live hosts.
        .replace("    _require_auto_healing_mutation_admission(state_dir=root)", "    pass")
        .replace(
            "credential = validate_installed_credential_bundle(binding, local)",
            "credential = type('FixtureCredential', (), {'bundle_digest': 'd' * 64})()",
        )
    )
    if fault == "symlink":
        config.rename(tmp_path / "real-config.yaml")
        config.symlink_to(tmp_path / "real-config.yaml")
    elif fault == "pending":
        (root / "apply.lock").write_text("{}")
    elif fault == "malformed":
        (root / "controller-checkpoint.json").write_text("{}")
    elif fault in {"cluster", "role", "readiness"}:
        payload = yaml.safe_load(config.read_text())
        if fault == "cluster":
            payload["vm_ha"]["cluster_id"] = "foreign"
        elif fault == "role":
            payload["vm_ha"]["node"]["role"] = "passive"
        else:
            payload["vm_ha"]["readiness"]["generation_id"] = "f" * 64
        config.write_text(yaml.safe_dump(payload))
    with (root / "rearm.lock").open("w") as lock:
        if fault == "busy":
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=5
        )
    assert "secret-never-export" not in result.stdout + result.stderr
    if fault in {"busy", "symlink"}:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    hosts["gateway-0"] = json.loads(result.stdout)
    if fault in {"pending", "malformed", "cluster", "role", "readiness"}:
        with pytest.raises(ValueError):
            validate_prehost_hosts(state, hosts)
    else:
        validate_prehost_hosts(state, hosts)


def test_prehost_recovery_rejects_swapped_installed_binding_roles():
    state, _, _, hosts = fixture()
    for host in hosts.values():
        for node in host["runtime_binding"]["nodes"]:
            node["role"] = "passive" if node["role"] == "active" else "active"
    with pytest.raises(ValueError, match="member changed"):
        validate_prehost_hosts(state, hosts)
