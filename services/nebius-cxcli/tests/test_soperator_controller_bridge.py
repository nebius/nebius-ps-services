from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_controller_bridge import (
    CONTROLLER_BRIDGE_JAIL_PVC,
    CONTROLLER_BRIDGE_JWT_MATERIAL_CONTRACT_SCHEMA,
    CONTROLLER_BRIDGE_JWT_MATERIAL_PROOF_SCHEMA,
    CONTROLLER_BRIDGE_LABEL,
    CONTROLLER_BRIDGE_MOUNT_CANARY_SCHEMA,
    CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
    CONTROLLER_BRIDGE_SOURCE_CONFIGURATION_SCHEMA,
    CONTROLLER_BRIDGE_STATE_PVC,
    BridgeNodeGroupPlan,
    BridgePlan,
    BridgeSourceBinding,
    BridgeStage,
    _bridge_pod_security_enforce_level,
    advance_bridge_stage,
    bridge_kubernetes_objects,
    bridge_network_policy_objects,
    bridge_node_group_payloads,
    bridge_only_controller_config,
    final_singleton_controller_config,
    new_bridge_journal,
    record_bridge_authority,
    source_controller_config_with_bridge_hosts,
    target_controller_gate_values,
    validate_bridge_journal,
)

_DIGEST = "a" * 64


def _runtime_fence(node: str) -> dict[str, object]:
    return {
        "schema": "nebius-cxcli-controller-runtime-fence/v1",
        "node_name": node,
        "node_uid": f"{node}-uid",
        "slurmctld_count": 0,
        "writable_state_mount_count": 0,
        "inspected_process_count": 42,
        "unreadable_process_count": 0,
        "marker_sha256": "e" * 64,
        "fenced": True,
    }


def _pre_source_mutation_canary(journal: Mapping[str, object]) -> dict[str, object]:
    node_groups = journal["node_groups"]
    resources = journal["kubernetes_resources"]
    assert isinstance(node_groups, list)
    assert isinstance(resources, list)
    pv_by_name = {
        item["name"]: item for item in resources if item["kind"] == "PersistentVolume"
    }
    pvc_by_name = {
        item["name"]: item for item in resources if item["kind"] == "PersistentVolumeClaim"
    }

    def storage_binding(claim_name: str) -> dict[str, str]:
        pv = pv_by_name[f"{claim_name}-pv"]
        pvc = pvc_by_name[claim_name]
        return {
            "pv_name": pv["name"],
            "pv_uid": pv["uid"],
            "pvc_name": pvc["name"],
            "pvc_uid": pvc["uid"],
        }
    attachment_sha256 = str(node_groups[0]["controller_spool_attachment_sha256"])
    return {
        "schema": CONTROLLER_BRIDGE_MOUNT_CANARY_SCHEMA,
        "purpose": CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
        "image": journal["source_binding"]["slurm_image_digest"],
        "token_sha256": "9" * 64,
        "controller_spool_attachment_sha256": attachment_sha256,
        "jail_attachment_sha256": str(node_groups[0]["jail_attachment_sha256"]),
        "storage": {
            "controller_spool": storage_binding(CONTROLLER_BRIDGE_STATE_PVC),
            "jail": storage_binding(CONTROLLER_BRIDGE_JAIL_PVC),
        },
        "mount_paths": {"controller_spool": "/shared", "jail": "/jail"},
        "pods": [
            {
                "slot": index,
                "pod_name": f"cxcli-controller-bridge-canary-{index}",
                "pod_uid": f"bridge-canary-pod-uid-{index}",
                "node_name": group["scheduling_failure_domain"]["node_name"],
                "node_uid": group["scheduling_failure_domain"]["node_uid"],
                "node_group_id": group["id"],
                "failure_domain": group["scheduling_failure_domain"]["zone"],
            }
            for index, group in enumerate(node_groups)
        ],
        "bidirectional": True,
        "observed_at": "2026-07-12T10:01:00Z",
    }


def _install_pre_source_fence_boundary(journal: dict[str, object]) -> None:
    canary = _pre_source_mutation_canary(journal)
    canary.update(
        {
            "purpose": migration.CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY,
            "token_sha256": "8" * 64,
            "observed_at": "2026-07-12T10:04:30Z",
        }
    )
    journal["shared_mount_canaries"].append(canary)  # type: ignore[union-attr]
    node_groups = journal["node_groups"]
    assert isinstance(node_groups, list)
    proofs = [
        {
            "slot": index,
            "id": group["id"],
            "name": group["name"],
            "resource_version": 17 + index,
            "controller_spool_attachment_sha256": group[
                "controller_spool_attachment_sha256"
            ],
            "jail_attachment_sha256": group["jail_attachment_sha256"],
            "node_name": group["scheduling_failure_domain"]["node_name"],
            "node_uid": group["scheduling_failure_domain"]["node_uid"],
            "failure_domain": group["scheduling_failure_domain"]["zone"],
        }
        for index, group in enumerate(node_groups)
    ]
    readiness = {
        "schema": "nebius-cxcli-controller-bridge-pre-source-fence-readiness/v1",
        "status": "verified",
        "authority_epoch": "source-epoch-1",
        "source_workload_uid": journal["source_binding"]["controller_workload_uid"],  # type: ignore[index]
        "source_workload_resource_version": "31",
        "source_pod_uid": journal["source_binding"]["controller_pod_uid"],  # type: ignore[index]
        "source_replicas": 1,
        "canary_sha256": migration._fingerprint(canary),  # noqa: SLF001
        "node_groups": proofs,
        "node_groups_sha256": migration._fingerprint(proofs),  # noqa: SLF001
        "verified_at": "2026-07-12T10:04:30Z",
    }
    readiness["semantic_sha256"] = migration._fingerprint(readiness)  # noqa: SLF001
    journal["pre_source_fence_readiness"] = readiness
    journal["source_fence_intent"] = {
        "schema": "nebius-cxcli-controller-bridge-source-fence-intent/v1",
        "state": "accepted",
        "authority_epoch": readiness["authority_epoch"],
        "readiness_sha256": readiness["semantic_sha256"],
        "canary_sha256": readiness["canary_sha256"],
        "source_workload_uid": readiness["source_workload_uid"],
        "source_pod_uid": readiness["source_pod_uid"],
        "intent_at": "2026-07-12T10:04:31Z",
        "accepted_at": "2026-07-12T10:05:00Z",
    }


def _client_propagation_proof(
    *,
    proof_stage: str,
    controller_hosts: list[str],
    roles: tuple[str, str],
    digest: str,
) -> dict[str, object]:
    return {
        "schema": "nebius-cxcli-controller-client-propagation/v1",
        "status": "verified",
        "proof_stage": proof_stage,
        "cluster_name": "old-cluster",
        "controller_hosts": controller_hosts,
        "timeouts": {
            "SlurmdTimeout": "3600" if roles[0] == "source-login" else "",
            "SlurmctldTimeout": "3600" if roles[0] == "source-login" else "",
        },
        "config_sha256": digest,
        "consumers": [
            {
                "role": role,
                "name": f"{role}-0",
                "uid": f"{role}-pod-uid",
                "node_name": f"{role}-node",
                "container_name": "sshd" if role.endswith("login") else "slurmd",
                "container_id": f"containerd://{role}",
                "restart_count": 0,
                "config_sha256": digest,
                "reconfigure_sha256": "1" * 64,
                "ping_sha256": "2" * 64,
                "show_config_sha256": "3" * 64,
            }
            for role in roles
        ],
        "live_rpc_verified": True,
        "verified_at": "2026-07-12T10:05:00Z",
        "revalidated_at": "2026-07-12T10:05:00Z",
    }


def _source() -> BridgeSourceBinding:
    return BridgeSourceBinding(
        namespace="soperator",
        slurmcluster_name="old-cluster",
        slurmcluster_uid="slurmcluster-uid",
        controller_workload_name="controller",
        controller_workload_uid="controller-workload-uid",
        controller_pod_name="controller-0",
        controller_pod_uid="controller-pod-uid",
        controller_pvc_name="controller-spool-controller-0",
        controller_pvc_uid="controller-pvc-uid",
        controller_pv_name="controller-pv",
        controller_pv_uid="controller-pv-uid",
        jail_pvc_name="jail-pvc",
        jail_pvc_uid="jail-pvc-uid",
        jail_pv_name="jail-pv",
        jail_pv_uid="jail-pv-uid",
        jail_filesystem_id="filesystem-jail",
        slurm_image_digest="registry.example/slurmctld@sha256:" + _DIGEST,
        slurm_version="24.11.6",
        configuration_fingerprint=_DIGEST,
        munge_fingerprint="b" * 64,
        jwt_fingerprint="c" * 64,
    )


def _plan() -> BridgePlan:
    source_template = {
        "metadata": {
            "labels": {
                "slurm.nebius.ai/nodeset-name": "controller",
                "nebius.com/node-group": "controller",
                "customer-label": "kept",
            }
        },
        "resources": {"platform": "cpu-d3", "preset": "8vcpu-32gb"},
        "taints": [
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "value": "controller",
                "effect": "NO_SCHEDULE",
            }
        ],
    }
    return BridgePlan(
        campaign_fingerprint=_DIGEST,
        cluster_id="cluster-1",
        cluster_name="old-cluster",
        source_kubernetes_version="1.31",
        source_slurm_image="registry.example/slurmctld@sha256:" + _DIGEST,
        target_slurm_image="registry.example/slurmctld@sha256:" + "d" * 64,
        source_slurm_version="24.11.6",
        target_slurm_version="25.11.3",
        state_save_location="/mnt/controller-spool/current",
        controller_spool_attachment={
            "mount_tag": "controller-spool",
            "existing_filesystem": {"id": "filesystem-1"},
        },
        jail_attachment={
            "mount_tag": "jail",
            "existing_filesystem": {"id": "filesystem-jail"},
        },
        node_groups=(
            BridgeNodeGroupPlan(
                name="cxcli-controller-bridge-a",
                template=deepcopy(source_template),
            ),
            BridgeNodeGroupPlan(
                name="cxcli-controller-bridge-b",
                template=deepcopy(source_template),
            ),
        ),
    )


def _target_login_binding() -> dict[str, object]:
    return {
        "target_slurmcluster": {
            "namespace": "soperator",
            "name": "target-cluster",
            "uid": "target-cluster-uid",
        },
        "workload": {
            "api_version": "apps.kruise.io/v1beta1",
            "kind": "StatefulSet",
            "name": "login",
            "uid": "target-login-workload-uid",
        },
        "replacement_node_group": {
            "id": "target-login-node-group",
            "name": "target-cluster-login",
        },
        "kubernetes_version": "1.32",
        "sshd_image": "",
        "sshd_image_digest": "",
    }


def _target_login_pod() -> dict[str, object]:
    binding = _target_login_binding()
    return {
        "name": "target-login-0",
        "uid": "target-login-pod-uid",
        "node_name": "target-login-node",
        "node_uid": "target-login-node-uid",
        "node_group_id": "target-login-node-group",
        "node_group_name": "target-cluster-login",
        "kubernetes_version": "1.32",
        "container_id": "containerd://target-login",
        "restart_count": 0,
        "workload": deepcopy(binding["workload"]),
        "configured_image": "",
        "resolved_image_digest": "",
        "host_key_fingerprints": ["SHA256:host-key"],
        "target_binding": binding,
    }


def _install_jwt_material_proof(journal: dict[str, object]) -> None:
    source = journal["source_binding"]
    takeover = journal["target_singleton_takeover"]
    authority = journal["authority"]
    assert isinstance(source, dict)
    assert isinstance(takeover, dict)
    assert isinstance(authority, dict)
    binding = {
        "namespace": source["namespace"],
        "name": "slurm-jwt",
        "uid": "slurm-jwt-uid",
        "data_keys": ["jwt_hs256.key"],
    }
    content_sha256 = "1" * 64
    wiring_sha256 = "2" * 64
    live_key_sha256 = "3" * 64
    jwt_key_path = "/run/slurm/jwt_hs256.key"
    jwt_secret_file_path = "/mnt/rest-jwt-key/rest_jwt.key"
    source_wiring_sha256 = "4" * 64
    destination_wiring_sha256 = "5" * 64
    jwt_secret_binding = {
        "namespace": binding["namespace"],
        "name": binding["name"],
        "uid": binding["uid"],
    }
    target_ref = "target-cluster"
    takeover["target_ref"] = target_ref
    journal["jwt_material_contract"] = {
        "schema": CONTROLLER_BRIDGE_JWT_MATERIAL_CONTRACT_SCHEMA,
        "secret_names": [binding["name"]],
        "data_keys": list(binding["data_keys"]),
        "source_fingerprint": source["jwt_fingerprint"],
        "content_sha256": content_sha256,
        "source_secret_bindings": [binding],
        "captured_at": "2026-07-12T10:00:30Z",
    }
    takeover["jwt_material_preflight"] = {
        "schema": migration.CONTROLLER_BRIDGE_JWT_MATERIAL_PREFLIGHT_SCHEMA,
        "status": "verified",
        "source_fingerprint": source["jwt_fingerprint"],
        "content_sha256": content_sha256,
        "secret_bindings": [binding],
        "jwt_key_path": jwt_key_path,
        "jwt_secret_binding": jwt_secret_binding,
        "jwt_data_key": "jwt_hs256.key",
        "jwt_secret_file_path": jwt_secret_file_path,
        "source_wiring_sha256": source_wiring_sha256,
        "destination_wiring_sha256": destination_wiring_sha256,
        "wiring_sha256": wiring_sha256,
        "live_key_sha256": live_key_sha256,
        "controller_pod_uid": "target-gated-controller-pod-uid",
        "controller_container_name": "slurmctld",
        "controller_container_image": "registry.example/slurm@sha256:" + "a" * 64,
        "controller_container_image_id": "registry.example/slurm@sha256:" + "a" * 64,
        "token_smoke_passed": False,
        "token_smoke_observed_at": "",
        "target_ref": target_ref,
        "controller_workload_uid": takeover["controller_workload_uid"],
        "controller_workload_resource_version": "31",
        "verified_at": "2026-07-12T10:19:00Z",
    }
    takeover["jwt_material_proof"] = {
        "schema": CONTROLLER_BRIDGE_JWT_MATERIAL_PROOF_SCHEMA,
        "status": "verified",
        "source_fingerprint": source["jwt_fingerprint"],
        "content_sha256": content_sha256,
        "secret_bindings": [binding],
        "jwt_key_path": jwt_key_path,
        "jwt_secret_binding": jwt_secret_binding,
        "jwt_data_key": "jwt_hs256.key",
        "jwt_secret_file_path": jwt_secret_file_path,
        "source_wiring_sha256": source_wiring_sha256,
        "destination_wiring_sha256": destination_wiring_sha256,
        "wiring_sha256": wiring_sha256,
        "live_key_sha256": live_key_sha256,
        "controller_container_name": "slurmctld",
        "controller_container_image": "registry.example/slurm@sha256:" + "a" * 64,
        "controller_container_image_id": "registry.example/slurm@sha256:" + "a" * 64,
        "token_smoke_passed": True,
        "token_smoke_observed_at": "2026-07-12T10:20:00Z",
        "target_ref": target_ref,
        "controller_pod_uid": takeover["controller_pod_uid"],
        "controller_workload_uid": takeover["controller_workload_uid"],
        "authority_epoch": authority["epoch"],
        "verified_at": "2026-07-12T10:20:00Z",
        "revalidated_at": "2026-07-12T10:20:00Z",
    }


def test_bridge_journal_starts_with_required_v4_sections() -> None:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
        created_at="2026-07-12T10:00:00Z",
    )

    assert journal["stage"] == BridgeStage.PLANNED.value
    assert journal["authority"]["owner"] == "source-singleton"
    assert journal["authority"]["source_restart_prohibited"] is False
    assert journal["source_configuration"] == {}
    assert journal["tui_actions"]["checkpoint_path"] == "slurm.action_journal"
    assert journal["login_session_handoff"]["target_binding"] == {}
    assert journal["authority_lease"]["holder_identity"] == ("source-epoch-1:source-singleton")
    assert journal["authority_lease_transitions"] == []
    assert journal["runtime_fence_proofs"] == []
    assert journal["security_contract"] == {}
    validate_bridge_journal(journal)


def test_bridge_login_handoff_requires_distinct_exact_source_and_target_identities() -> None:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
    )
    source_pod = {
        "name": "source-login-0",
        "uid": "source-login-pod-uid",
        "node_name": "source-login-node",
        "node_uid": "source-login-node-uid",
        "node_group_id": "source-login-group-id",
        "container_id": "containerd://source-sshd",
        "restart_count": 0,
        "host_key_fingerprints": ["SHA256:preserved-host-key"],
    }
    handoff = journal["login_session_handoff"]
    handoff.update(
        {
            "state": "protected",
            "locked_at": "2026-07-12T10:00:00Z",
            "source_services": [
                {
                    "name": "soperator-login",
                    "uid": "source-login-service-uid",
                    "type": "LoadBalancer",
                }
            ],
            "protected_pods": [source_pod],
            "no_sessions_at_lock": True,
        }
    )
    validate_bridge_journal(journal)

    handoff.update(
        {
            "state": "target-ready",
            "target_ready": True,
            "target_binding": _target_login_binding(),
            "target_pod": {
                **_target_login_pod(),
                "uid": source_pod["uid"],
            },
            "service_switched_at": "2026-07-12T10:05:00Z",
        }
    )
    with pytest.raises(ValueError, match="must be distinct"):
        validate_bridge_journal(journal)


def test_bridge_login_handoff_rejects_target_ready_without_exact_binding() -> None:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
    )
    journal["login_session_handoff"].update(
        {
            "state": "target-ready",
            "locked_at": "2026-07-12T10:00:00Z",
            "source_services": [
                {
                    "name": "soperator-login",
                    "uid": "source-login-service-uid",
                    "type": "LoadBalancer",
                }
            ],
            "protected_pods": [
                {
                    "name": "source-login-0",
                    "uid": "source-login-pod-uid",
                    "node_name": "source-login-node",
                    "node_uid": "source-login-node-uid",
                    "node_group_id": "source-login-group-id",
                    "container_id": "containerd://source-sshd",
                    "restart_count": 0,
                    "host_key_fingerprints": ["SHA256:host-key"],
                }
            ],
            "no_sessions_at_lock": True,
            "target_ready": True,
            "target_pod": _target_login_pod(),
            "service_switched_at": "2026-07-12T10:05:00Z",
        }
    )

    with pytest.raises(ValueError, match="requires an exact target binding"):
        validate_bridge_journal(journal)


def test_bridge_login_handoff_rejects_disappearance_without_exact_acknowledgement() -> None:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
    )
    source_pod = {
        "name": "source-login-0",
        "uid": "source-login-pod-uid",
        "node_name": "source-login-node",
        "node_uid": "source-login-node-uid",
        "node_group_id": "source-login-group-id",
        "container_id": "containerd://source-sshd",
        "restart_count": 0,
        "host_key_fingerprints": ["SHA256:preserved-host-key"],
    }
    journal["login_session_handoff"].update(
        {
            "state": "protected",
            "locked_at": "2026-07-12T10:00:00Z",
            "source_services": [
                {
                    "name": "soperator-login",
                    "uid": "source-login-service-uid",
                    "type": "LoadBalancer",
                }
            ],
            "protected_pods": [source_pod],
            "sessions": [
                {
                    "pod_uid": source_pod["uid"],
                    "socket_fingerprint": "a" * 64,
                    "socket_inode": "101",
                    "sshd_pid": "201",
                    "sshd_start_time": "301",
                    "shell_pid": "401",
                    "shell_start_time": "501",
                    "host_key_fingerprint": "b" * 64,
                    "ended_at": "2026-07-12T10:01:00Z",
                    "outcome": "voluntary-exit",
                }
            ],
            "no_sessions_at_lock": False,
        }
    )

    with pytest.raises(ValueError, match="explicit-exit acknowledgement"):
        validate_bridge_journal(journal)


def test_bridge_stage_cannot_skip_durable_authority_boundaries() -> None:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
    )

    with pytest.raises(ValueError, match="one durable boundary at a time"):
        advance_bridge_stage(journal, BridgeStage.SOURCE_FENCED)


def test_first_bridge_write_permanently_fences_source_restart() -> None:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
    )

    record_bridge_authority(
        journal,
        epoch="bridge-source-1",
        owner="bridge-source",
        reason="shared state loaded by source-version bridge",
        first_bridge_write=True,
        at="2026-07-12T10:10:00Z",
    )

    assert journal["authority"]["source_restart_prohibited"] is True
    with pytest.raises(ValueError, match="Source singleton restart is prohibited"):
        record_bridge_authority(
            journal,
            epoch="source-restart-2",
            owner="source-singleton",
            reason="unsafe rollback",
        )


def test_bridge_node_groups_are_fixed_isolated_and_sfs_attached_from_birth() -> None:
    payloads = bridge_node_group_payloads(_plan())

    assert [payload["spec"]["fixed_node_count"] for payload in payloads] == [1, 1]
    assert [payload["spec"]["version"] for payload in payloads] == ["1.31", "1.31"]
    for slot, payload in enumerate(payloads):
        template = payload["spec"]["template"]
        assert template["metadata"]["labels"] == {
            "customer-label": "kept",
            CONTROLLER_BRIDGE_LABEL: "true",
            "nebius.ai/cxcli-controller-bridge-slot": str(slot),
        }
        assert template["filesystems"][0]["existing_filesystem"]["id"] == "filesystem-1"
        assert template["filesystems"][1] == {
            "mount_tag": "jail",
            "existing_filesystem": {"id": "filesystem-jail"},
        }
        assert template["taints"] == [
            {
                "key": CONTROLLER_BRIDGE_LABEL,
                "value": "true",
                "effect": "NO_SCHEDULE",
            }
        ]


def test_old_controller_taint_is_replaced_by_one_shared_bridge_scheduling_contract() -> None:
    plan = _plan()
    expected_tolerations = [
        {
            "key": CONTROLLER_BRIDGE_LABEL,
            "operator": "Equal",
            "value": "true",
            "effect": "NoSchedule",
        }
    ]
    source_role_key = "slurm.nebius.ai/nodeset-name"
    assert any(
        taint["key"] == source_role_key
        for taint in plan.node_groups[0].template["taints"]
    )
    assert all(
        payload["spec"]["template"]["taints"]
        == [
            {
                "key": CONTROLLER_BRIDGE_LABEL,
                "value": "true",
                "effect": "NO_SCHEDULE",
            }
        ]
        for payload in bridge_node_group_payloads(plan)
    )
    controller_pod_spec = {
        "tolerations": [
            {
                "key": source_role_key,
                "operator": "Equal",
                "value": "controller",
                "effect": "NoSchedule",
            }
        ],
        "containers": [{"name": "slurmctld", "image": plan.source_slurm_image}],
    }
    objects = bridge_kubernetes_objects(
        plan,
        controller_pod_spec=controller_pod_spec,
        state_volume_name="state",
        state_volume_claim_name=CONTROLLER_BRIDGE_STATE_PVC,
        state_storage_class="controller-spool",
        state_storage_size="20Gi",
        state_local_path="/mnt/controller-spool",
        state_node_match_expressions=(
            {"key": CONTROLLER_BRIDGE_LABEL, "operator": "In", "values": ["true"]},
        ),
        jail_volume_claim_name=CONTROLLER_BRIDGE_JAIL_PVC,
        jail_storage_class="jail",
        jail_storage_size="512Gi",
        jail_local_path="/mnt/jail",
        jail_access_modes=("ReadWriteMany",),
        jail_volume_mode="Filesystem",
        soperator_namespace="soperator",
        soperator_cluster_name="old-cluster",
        kubernetes_api_cidrs=("10.96.0.1/32",),
        replicas=2,
    )
    statefulset = next(item for item in objects if item["kind"] == "StatefulSet")
    canary = migration._bridge_mount_canary_pod(  # noqa: SLF001
        namespace=plan.namespace,
        slot=0,
        image=plan.source_slurm_image,
    )
    stager = migration._bridge_stager_pod(plan=plan)  # noqa: SLF001

    assert statefulset["spec"]["template"]["spec"]["tolerations"] == expected_tolerations
    assert canary["spec"]["tolerations"] == expected_tolerations
    assert stager["spec"]["tolerations"] == expected_tolerations
    assert source_role_key not in repr((statefulset, canary, stager))


def test_bridge_statefulset_is_two_replicas_with_shared_state_and_anti_affinity() -> None:
    pod_spec = {
        "serviceAccountName": "slurm-controller",
        "containers": [
            {
                "name": "slurmctld",
                "image": _plan().source_slurm_image,
                "volumeMounts": [
                    {"name": "state", "mountPath": "/var/spool/slurmctld"},
                    {"name": "jail", "mountPath": "/run/jail"},
                ],
            }
        ],
        "volumes": [
            {"name": "state", "emptyDir": {}},
            {
                "name": "jail",
                "persistentVolumeClaim": {"claimName": CONTROLLER_BRIDGE_JAIL_PVC},
            },
        ],
    }

    objects = bridge_kubernetes_objects(
        _plan(),
        controller_pod_spec=pod_spec,
        state_volume_name="state",
        state_volume_claim_name="bridge-state",
        state_storage_class="controller-spool",
        state_storage_size="20Gi",
        state_local_path="/mnt/controller-spool",
        state_node_match_expressions=(
            {"key": CONTROLLER_BRIDGE_LABEL, "operator": "In", "values": ["true"]},
        ),
        jail_volume_claim_name=CONTROLLER_BRIDGE_JAIL_PVC,
        jail_storage_class="jail",
        jail_storage_size="512Gi",
        jail_local_path="/mnt/jail",
        jail_access_modes=("ReadWriteMany",),
        jail_volume_mode="Filesystem",
        soperator_namespace="soperator",
        soperator_cluster_name="old-cluster",
        kubernetes_api_cidrs=("10.96.0.1/32",),
        replicas=2,
    )
    statefulset = next(item for item in objects if item["kind"] == "StatefulSet")
    namespace = next(item for item in objects if item["kind"] == "Namespace")
    network_policies = {
        item["metadata"]["name"]: item for item in objects if item["kind"] == "NetworkPolicy"
    }

    for item in objects:
        assert item["metadata"]["labels"][CONTROLLER_BRIDGE_LABEL] == "true"
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == ("baseline")
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/warn"] == ("restricted")
    assert set(network_policies) == {
        "cxcli-controller-bridge-default-deny",
        "cxcli-controller-bridge-required-traffic",
    }
    assert network_policies["cxcli-controller-bridge-default-deny"]["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }
    required_policy = network_policies["cxcli-controller-bridge-required-traffic"]["spec"]
    assert required_policy["podSelector"] == {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}}
    assert required_policy["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 6817}]
    assert required_policy["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}}},
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "soperator"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/instance": "old-cluster"}},
        },
    ]
    assert required_policy["egress"][-1]["to"] == [{"ipBlock": {"cidr": "10.96.0.1/32"}}]
    assert "0.0.0.0/0" not in repr(required_policy)
    assert '{"namespaceSelector": {}}' not in repr(required_policy)
    assert {port["port"] for rule in required_policy["egress"] for port in rule["ports"]} == {
        53,
        443,
        6817,
        6818,
        6819,
    }
    assert statefulset["spec"]["replicas"] == 2
    assert statefulset["spec"]["podManagementPolicy"] == "Parallel"
    template_spec = statefulset["spec"]["template"]["spec"]
    assert template_spec["automountServiceAccountToken"] is False
    assert template_spec["nodeSelector"] == {CONTROLLER_BRIDGE_LABEL: "true"}
    assert template_spec["volumes"][-2:] == [
        {
            "name": "jail",
            "persistentVolumeClaim": {"claimName": CONTROLLER_BRIDGE_JAIL_PVC},
        },
        {
            "name": "state",
            "persistentVolumeClaim": {"claimName": "bridge-state"},
        },
    ]
    assert template_spec["containers"][0]["volumeMounts"] == [
        {
            "name": "state",
            "mountPath": "/var/spool/slurmctld",
            "subPath": "current",
        },
        {"name": "jail", "mountPath": "/run/jail"},
    ]
    pvcs = {
        item["metadata"]["name"]: item
        for item in objects
        if item["kind"] == "PersistentVolumeClaim"
    }
    assert pvcs["bridge-state"]["spec"]["volumeName"] == "bridge-state-pv"
    assert pvcs[CONTROLLER_BRIDGE_JAIL_PVC]["spec"] == {
        "volumeName": f"{CONTROLLER_BRIDGE_JAIL_PVC}-pv",
        "storageClassName": "jail",
        "volumeMode": "Filesystem",
        "resources": {"requests": {"storage": "512Gi"}},
        "accessModes": ["ReadWriteMany"],
    }
    required = template_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]
    assert required[0]["topologyKey"] == "nebius.com/node-group-id"


def test_bridge_network_policy_rejects_world_and_link_local_api_cidrs() -> None:
    for cidr in (
        "0.0.0.0/0",
        "0.0.0.0/32",
        "10.96.0.0/24",
        "169.254.169.254/32",
        "::/0",
    ):
        with pytest.raises(ValueError, match="API CIDR"):
            bridge_network_policy_objects(
                namespace="cxcli-soperator-upgrade-bridge",
                soperator_namespace="soperator",
                soperator_cluster_name="old-cluster",
                kubernetes_api_cidrs=(cidr,),
            )


@pytest.mark.parametrize(
    "pod_spec",
    (
        {"hostPID": True, "containers": []},
        {"volumes": [{"name": "host", "hostPath": {"path": "/var/lib/slurm"}}]},
        {"containers": [{"securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}}]},
    ),
)
def test_bridge_namespace_declares_privileged_psa_only_for_source_contracts_that_need_it(
    pod_spec: Mapping[str, object],
) -> None:
    assert _bridge_pod_security_enforce_level(pod_spec) == "privileged"


def test_bridge_substrate_resources_bind_the_exact_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "cxcli-soperator-upgrade-bridge",
                "labels": {
                    "app.kubernetes.io/managed-by": "nebius-cxcli",
                    CONTROLLER_BRIDGE_LABEL: "true",
                },
            },
        }
    ]
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (False, {}),
    )

    migration._bind_and_preflight_controller_bridge_resource_ownership(  # noqa: SLF001
        resources=resources,
        plan=_plan(),
        kube_context="ctx",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected runner"),
    )

    assert resources[0]["metadata"]["annotations"] == {
        "nebius.ai/cxcli-campaign-fingerprint": _DIGEST,
        "nebius.ai/cxcli-cluster-id": "cluster-1",
    }


def test_bridge_substrate_refuses_preexisting_foreign_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "cxcli-soperator-upgrade-bridge",
                "labels": {
                    "app.kubernetes.io/managed-by": "nebius-cxcli",
                    CONTROLLER_BRIDGE_LABEL: "true",
                },
            },
        }
    ]
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (
            True,
            {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/managed-by": "nebius-cxcli",
                        CONTROLLER_BRIDGE_LABEL: "true",
                    },
                    "annotations": {
                        "nebius.ai/cxcli-campaign-fingerprint": "f" * 64,
                        "nebius.ai/cxcli-cluster-id": "another-cluster",
                    },
                }
            },
        ),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="refuses to adopt or overwrite pre-existing Namespace",
    ):
        migration._bind_and_preflight_controller_bridge_resource_ownership(  # noqa: SLF001
            resources=resources,
            plan=_plan(),
            kube_context="ctx",
            command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected runner"),
        )


def test_source_and_final_controller_configs_have_exact_ordered_hosts() -> None:
    source = "ClusterName=old-cluster\nSlurmctldHost=controller-0\nSlurmctldPort=6817\n"

    bridged = source_controller_config_with_bridge_hosts(source)
    assert [line for line in bridged.splitlines() if line.startswith("SlurmctldHost=")] == [
        "SlurmctldHost=controller-0",
        "SlurmctldHost=cxcli-slurm-controller-bridge-0"
        "(cxcli-slurm-controller-bridge-0.cxcli-slurm-controller-bridge."
        "cxcli-soperator-upgrade-bridge.svc)",
        "SlurmctldHost=cxcli-slurm-controller-bridge-1"
        "(cxcli-slurm-controller-bridge-1.cxcli-slurm-controller-bridge."
        "cxcli-soperator-upgrade-bridge.svc)",
    ]

    final = final_singleton_controller_config(bridged)
    assert [line for line in final.splitlines() if line.startswith("SlurmctldHost=")] == [
        "SlurmctldHost=controller-0"
    ]

    target_bridge = bridge_only_controller_config(
        "ClusterName=old-cluster\nSlurmctldHost=controller-0\nStateSaveLocation=/old\n"
    )
    assert target_bridge.count("SlurmctldHost=") == 2
    assert "SlurmctldHost=controller-0\n" not in target_bridge
    assert "StateSaveLocation=/mnt/controller-spool/current" in target_bridge


def test_final_controller_config_restores_exact_original_timeouts() -> None:
    config = (
        "ClusterName=old-cluster\n"
        "SlurmctldHost=controller-0\n"
        "SlurmdTimeout=3600\n"
        "SlurmctldTimeout=3600\n"
    )

    restored = migration._restore_controller_gap_timeouts(  # noqa: SLF001
        config,
        {"SlurmdTimeout": "300", "SlurmctldTimeout": "120"},
    )

    assert "SlurmdTimeout=300\n" in restored
    assert "SlurmctldTimeout=120\n" in restored
    assert "Timeout=3600" not in restored


def test_final_controller_config_removes_timeouts_that_were_originally_absent() -> None:
    config = (
        "ClusterName=old-cluster\n"
        "SlurmctldHost=controller-0\n"
        "SlurmdTimeout=3600\n"
        "SlurmctldTimeout=3600\n"
    )

    restored = migration._restore_controller_gap_timeouts(  # noqa: SLF001
        config,
        {"SlurmdTimeout": "", "SlurmctldTimeout": ""},
    )

    assert "SlurmdTimeout" not in restored
    assert "SlurmctldTimeout" not in restored


def test_target_gate_uses_stable_chart_surface_and_disables_probes() -> None:
    values = {"images": {"slurmctld": _plan().target_slurm_image}}

    gated = target_controller_gate_values(values)

    slurmctld = gated["slurmNodes"]["controller"]["slurmctld"]
    assert slurmctld["command"] == ["/bin/sh", "-ec"]
    assert "while :" in slurmctld["args"][0]
    assert slurmctld["livenessProbe"] is None
    assert slurmctld["readinessProbe"] is None
    assert values == {"images": {"slurmctld": _plan().target_slurm_image}}


def _cleaned_journal() -> dict[str, object]:
    journal = new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch-1",
        created_at="2026-07-12T10:00:00Z",
    )
    node_groups = journal["node_groups"]
    assert isinstance(node_groups, list)
    for index, item in enumerate(node_groups):
        node_group_id = f"node-group-{index}"
        item.update(
            {
                "id": node_group_id,
                "created": True,
                "scheduling_failure_domain": {
                    "topology_key": "nebius.com/node-group-id",
                    "node_group_id": node_group_id,
                    "provider_topology_key": "topology.kubernetes.io/zone",
                    "zone": f"eu-north1-{chr(ord('a') + index)}",
                    "node_name": f"bridge-node-{index}",
                    "node_uid": f"bridge-node-uid-{index}",
                },
            }
        )
    journal["kubernetes_resources"] = [
        {
            "api_version": "v1",
            "kind": "Namespace",
            "namespace": "",
            "name": _plan().namespace,
            "uid": "bridge-namespace-uid",
            "resource_version": "10",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolume",
            "namespace": "",
            "name": f"{CONTROLLER_BRIDGE_STATE_PVC}-pv",
            "uid": "bridge-pv-uid",
            "resource_version": "11",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolumeClaim",
            "namespace": _plan().namespace,
            "name": CONTROLLER_BRIDGE_STATE_PVC,
            "uid": "bridge-pvc-uid",
            "resource_version": "12",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolume",
            "namespace": "",
            "name": f"{CONTROLLER_BRIDGE_JAIL_PVC}-pv",
            "uid": "bridge-jail-pv-uid",
            "resource_version": "13",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolumeClaim",
            "namespace": _plan().namespace,
            "name": CONTROLLER_BRIDGE_JAIL_PVC,
            "uid": "bridge-jail-pvc-uid",
            "resource_version": "14",
        },
    ]
    security_contract = {
        "schema": "nebius-cxcli-controller-security/v1",
        "soperator_namespace": {"name": "soperator", "uid": "soperator-namespace-uid"},
        "kubernetes_api_service": {
            "namespace": "default",
            "name": "kubernetes",
            "uid": "kubernetes-service-uid",
        },
        "kubernetes_api_cidrs": ["10.96.0.1/32"],
        "inspector": {
            "namespace": "cxcli-soperator-upgrade-inspectors",
            "namespace_uid": "inspector-namespace-uid",
            "resources_sha256": "a" * 64,
            "admission_nodes": [
                {
                    "node_name": "source-controller-node",
                    "node_uid": "source-controller-node-uid",
                    "provider_id": "nebius://source-controller-node",
                    "system_uuid": "source-controller-system-uuid",
                }
            ],
            "admission_nodes_sha256": "b" * 64,
            "server_dry_run_at": "2026-07-12T10:00:30Z",
        },
        "bridge": {
            "namespace": _plan().namespace,
            "namespace_uid": "bridge-namespace-uid",
            "pod_security_enforce": "baseline",
            "network_policies_sha256": "c" * 64,
            "workload_security_sha256": "d" * 64,
        },
    }
    security_contract["semantic_sha256"] = migration._fingerprint(  # noqa: SLF001
        security_contract
    )
    journal["security_contract"] = security_contract
    journal["shared_mount_canaries"] = [_pre_source_mutation_canary(journal)]
    _install_pre_source_fence_boundary(journal)
    journal["fencing"]["source"].update(
        {
            "proven": True,
            "process_absent": True,
            "writable_mount_absent": True,
            "runtime_evidence": [_runtime_fence("source-controller-node")],
            "observed_at": "2026-07-12T10:05:00Z",
        }
    )
    journal["fencing"]["bridge"].update(
        {
            "proven": True,
            "processes_absent": True,
            "writable_state_mounts_absent": True,
            "runtime_fence": [
                _runtime_fence("bridge-node-0"),
                _runtime_fence("bridge-node-1"),
            ],
            "observed_at": "2026-07-12T10:06:00Z",
        }
    )
    journal["state_manifest"].update(
        {
            "epoch_directory": "/mnt/controller-spool/epochs/target",
            "sha256": "f" * 64,
            "file_count": 4,
            "fsynced": True,
            "promoted": True,
        }
    )
    journal["state_precopy"].update(
        {
            "source_state_save_location": "/var/spool/slurmctld",
            "archive_path": "/artifacts/source.precopy.tar",
            "incremental_snapshot_path": "/artifacts/source.precopy.snar",
            "remote_path": "/shared/epochs/source.precopy",
            "archive_sha256": "1" * 64,
            "incremental_snapshot_sha256": "2" * 64,
            "remote_manifest_sha256": "3" * 64,
            "gnu_incremental": True,
            "completed_at": "2026-07-12T10:04:00Z",
        }
    )
    journal["cold_reader"].update(
        {
            "pod_name": "cxcli-controller-state-cold-reader",
            "pod_uid": "cold-reader-pod-uid",
            "source_pvc_name": "controller-spool-controller-0",
            "source_pvc_uid": "controller-pvc-uid",
            "source_node_name": "source-controller-node",
            "source_node_uid": "source-controller-node-uid",
            "source_image": _plan().source_slurm_image,
            "intent_at": "2026-07-12T10:04:30Z",
            "delta_archive_sha256": "4" * 64,
            "incremental_snapshot_sha256": "5" * 64,
            "source_manifest_sha256": "f" * 64,
            "status": "absent",
            "mount_absent": True,
            "absent_at": "2026-07-12T10:05:00Z",
        }
    )
    journal["authority"].update(
        {
            "epoch": "target-aaaaaaaaaaaa",
            "owner": "target-singleton",
            "first_bridge_write_at": "2026-07-12T10:07:00Z",
            "source_restart_prohibited": True,
            "writer_scale": {
                "state": "accepted",
                "statefulset_uid": "bridge-statefulset-uid",
                "accepted_at": "2026-07-12T10:07:00Z",
            },
        }
    )
    journal["authority_lease"].update(
        {
            "uid": "authority-lease-uid",
            "resource_version": "23",
            "holder_identity": "target-aaaaaaaaaaaa:target-singleton",
        }
    )
    journal["version_transition"].update(
        {
            "backup_sha256": "6" * 64,
            "backup_entry_count": 4,
            "backup_cold_after_runtime_fence": True,
            "backup_operation": {
                "state": "accepted",
                "intent_at": "2026-07-12T10:07:30Z",
                "accepted_at": "2026-07-12T10:08:00Z",
                "token": "abcd" * 8,
                "backup_epoch": "pre-target-aaaaaaaaaaaa",
                "expected_preimage_sha256": "6" * 64,
                "expected_preimage_entries": 4,
            },
            "source_runtime_fence": [
                _runtime_fence("bridge-node-0"),
                _runtime_fence("bridge-node-1"),
            ],
            "both_stopped_at": "2026-07-12T10:08:00Z",
            "target_config_sha256": "7" * 64,
            "target_write_at": "2026-07-12T10:09:00Z",
            "downgrade_prohibited": True,
            "target_writer_scale": {
                "state": "accepted",
                "statefulset_uid": "bridge-statefulset-uid",
                "accepted_at": "2026-07-12T10:09:00Z",
            },
        }
    )
    handoff_hosts = [
        line.split("=", 1)[1]
        for line in source_controller_config_with_bridge_hosts(
            "ClusterName=old-cluster\nSlurmctldHost=controller-0\n"
        ).splitlines()
        if line.startswith("SlurmctldHost=")
    ]
    original_slurm_conf = (
        "ClusterName=old-cluster\n"
        "SlurmctldHost=controller-0\n"
        "SlurmdTimeout=300\n"
        "SlurmctldTimeout=120\n"
    )
    intended_slurm_conf, original_timeouts = migration._bridge_slurm_config_with_timeouts(  # noqa: SLF001
        original_slurm_conf
    )
    original_data_sha256 = "a" * 64
    intended_data_sha256 = "b" * 64
    journal["source_configuration"] = {
        "schema": CONTROLLER_BRIDGE_SOURCE_CONFIGURATION_SCHEMA,
        "config_map_names": ["slurm-config"],
        "config_key": "slurm.conf",
        "original_slurm_conf": original_slurm_conf,
        "original_slurm_conf_sha256": hashlib.sha256(
            original_slurm_conf.encode("utf-8")
        ).hexdigest(),
        "intended_slurm_conf": intended_slurm_conf,
        "intended_slurm_conf_sha256": hashlib.sha256(
            intended_slurm_conf.encode("utf-8")
        ).hexdigest(),
        "original_config_data_sha256": original_data_sha256,
        "intended_config_data_sha256": intended_data_sha256,
        "original_timeouts": original_timeouts,
        "bridge_config_sha256": hashlib.sha256(intended_slurm_conf.encode("utf-8")).hexdigest(),
        "client_config_contract": migration._controller_bridge_client_config_contract(  # noqa: SLF001
            intended_slurm_conf
        ),
        "intent_at": "2026-07-12T10:02:00Z",
        "copies": {
            copy_name: {
                "namespace": namespace,
                "name": "slurm-config",
                "uid": f"{copy_name}-config-uid",
                "intent_resource_version": str(index * 10),
                "preimage_data_sha256": original_data_sha256,
                "intended_data_sha256": intended_data_sha256,
                "preimage_material_sha256": str(index) * 64,
                "intended_material_sha256": str(index + 2) * 64,
                "state": "accepted",
                "intent_at": "2026-07-12T10:02:00Z",
                "accepted_resource_version": str(index * 10 + 1),
                "accepted_data_sha256": intended_data_sha256,
                "accepted_at": "2026-07-12T10:03:00Z",
            }
            for index, (copy_name, namespace) in enumerate(
                (("source", "soperator"), ("bridge", journal["namespace"])),
                start=1,
            )
        },
        "client_propagation": _client_propagation_proof(
            proof_stage="before-source-fence",
            controller_hosts=handoff_hosts,
            roles=("source-login", "source-worker"),
            digest="8" * 64,
        ),
    }
    journal["target_singleton_takeover"].update(
        {
            "controller_pod_uid": "target-controller-pod-uid",
            "controller_workload_uid": "target-controller-workload-uid",
            "final_config_map_name": "slurm-config",
            "state_loaded": True,
            "only_primary_proven": True,
            "final_slurmctld_host_count": 1,
            "client_handoff_propagation": _client_propagation_proof(
                proof_stage="before-target-takeover",
                controller_hosts=handoff_hosts,
                roles=("target-login", "target-worker"),
                digest="9" * 64,
            ),
            "final_client_propagation": _client_propagation_proof(
                proof_stage="final-target-singleton",
                controller_hosts=["controller-0"],
                roles=("target-login", "target-worker"),
                digest="0" * 64,
            ),
        }
    )
    journal["login_session_handoff"].update(
        {
            "state": "complete",
            "locked_at": "2026-07-12T10:01:00Z",
            "source_services": [
                {"name": "login", "uid": "login-service-uid", "type": "LoadBalancer"}
            ],
            "protected_pods": [
                {
                    "name": "login-0",
                    "uid": "login-pod-uid",
                    "node_name": "login-node",
                    "node_uid": "login-node-uid",
                    "node_group_id": "login-node-group",
                    "container_id": "containerd://login",
                    "restart_count": 0,
                    "host_key_fingerprints": ["SHA256:host-key"],
                }
            ],
            "no_sessions_at_lock": True,
            "target_ready": True,
            "target_binding": _target_login_binding(),
            "target_pod": _target_login_pod(),
            "service_switched_at": "2026-07-12T10:20:00Z",
        }
    )
    journal["preservation_jobs"].update(
        {
            "capture_state": "complete",
            "captured_at": "2026-07-12T10:02:00Z",
            "verifications": [
                {
                    "stage": stage,
                    "status": "verified",
                    "jobs": {},
                    "verified_at": f"2026-07-12T10:{minute}:00Z",
                }
                for stage, minute in (
                    ("source-version-bridge-active", "07"),
                    ("target-version-bridge-active", "10"),
                    ("target-singleton-active", "20"),
                )
            ],
        }
    )
    pre_cleanup_verification = {
        "stage": "pre-cleanup",
        "status": "verified",
        "jobs": {},
        "verified_at": "2026-07-12T10:20:30Z",
        "action_journal_generation": 0,
        "authority_epoch": str(journal["authority"]["epoch"]),
    }
    journal["preservation_jobs"]["verifications"].append(pre_cleanup_verification)
    resource_keys = [
        "|".join(
            (
                str(item["api_version"]),
                str(item["kind"]),
                str(item["namespace"]),
                str(item["name"]),
                str(item["uid"]),
            )
        )
        for item in journal["kubernetes_resources"]
    ]
    direct_operations = {
        key: {
            "identity": {
                field: str(item[field])
                for field in (
                    "api_version",
                    "kind",
                    "namespace",
                    "name",
                    "uid",
                    "resource_version",
                )
            },
            "state": "absent",
            "intent_at": {
                "PersistentVolumeClaim": "2026-07-12T10:21:00Z",
                "Namespace": "2026-07-12T10:23:00Z",
                "PersistentVolume": "2026-07-12T10:25:00Z",
            }[str(item["kind"])],
            "absent_at": {
                "PersistentVolumeClaim": "2026-07-12T10:22:00Z",
                "Namespace": "2026-07-12T10:24:00Z",
                "PersistentVolume": "2026-07-12T10:26:00Z",
            }[str(item["kind"])],
        }
        for key, item in zip(resource_keys, journal["kubernetes_resources"], strict=True)
        if item["kind"] in {"Namespace", "PersistentVolume", "PersistentVolumeClaim"}
    }
    journal["cleanup"].update(
        {
            "intent_at": "2026-07-12T10:21:00Z",
            "pre_cleanup_preservation_proof": {
                "status": "verified",
                "action_journal_generation": 0,
                "authority_epoch": str(journal["authority"]["epoch"]),
                "verified_at": "2026-07-12T10:20:30Z",
                "verification_sha256": migration._fingerprint(  # noqa: SLF001
                    pre_cleanup_verification
                ),
            },
            "kubernetes_operations": direct_operations,
            "node_group_operations": {
                f"node-group-{index}": {
                    "operation": {
                        "attempt_state": "provider-terminal",
                        "provider_operation_id": f"delete-operation-{index}",
                    }
                }
                for index in range(2)
            },
            "bridge_storage_verified_at": "2026-07-12T10:22:00Z",
            "bridge_storage_bindings": {
                "controller_spool": {
                    "pv_name": f"{CONTROLLER_BRIDGE_STATE_PVC}-pv",
                    "pv_uid": "bridge-pv-uid",
                    "pvc_name": CONTROLLER_BRIDGE_STATE_PVC,
                    "pvc_uid": "bridge-pvc-uid",
                    "reclaim_policy": "Retain",
                    "local_path": "/mnt/controller-spool",
                },
                "jail": {
                    "pv_name": f"{CONTROLLER_BRIDGE_JAIL_PVC}-pv",
                    "pv_uid": "bridge-jail-pv-uid",
                    "pvc_name": CONTROLLER_BRIDGE_JAIL_PVC,
                    "pvc_uid": "bridge-jail-pvc-uid",
                    "reclaim_policy": "Retain",
                    "local_path": "/mnt/jail",
                },
            },
            "namespace_deleted": True,
            "node_groups_deleted": ["node-group-0", "node-group-1"],
            "kubernetes_resources_deleted": resource_keys,
            "bridge_resources_absent": True,
            "shared_state_retained": True,
            "final_singleton_proven": True,
            "target_state_binding": {
                "pvc_name": "controller-spool-pvc",
                "pvc_uid": "target-pvc-uid",
                "pv_name": "controller-spool-pv",
                "pv_uid": "target-pv-uid",
                "state_path": "/mnt/controller-spool/current",
            },
            "proof_checked_at": "2026-07-12T10:27:00Z",
            "completed_at": "2026-07-12T10:28:00Z",
        }
    )
    journal["partition_restore"].update(
        {
            "status": "restored",
            "records_sha256": migration._fingerprint([]),  # noqa: SLF001
            "partition_count": 0,
            "action_journal_generation": 0,
            "authority_epoch": str(journal["authority"]["epoch"]),
            "intent_at": "2026-07-12T10:20:40Z",
            "restored_at": "2026-07-12T10:20:45Z",
        }
    )
    _install_jwt_material_proof(journal)
    journal["stage"] = BridgeStage.CLEANED.value
    return journal


def test_cleaned_bridge_journal_requires_exact_resource_and_node_group_absence() -> None:
    journal = _cleaned_journal()

    validate_bridge_journal(journal)

    journal["cleanup"]["node_groups_deleted"] = ["node-group-0"]
    with pytest.raises(ValueError, match="delete both journaled node groups"):
        validate_bridge_journal(journal)


def test_cleaned_bridge_journal_requires_exact_state_and_jail_retain_proofs() -> None:
    journal = _cleaned_journal()
    journal["cleanup"]["bridge_storage_bindings"]["jail"]["reclaim_policy"] = "Delete"

    with pytest.raises(ValueError, match="state or Jail Retain storage proof drifted"):
        validate_bridge_journal(journal)


def test_cleaned_bridge_journal_enforces_pvc_namespace_pv_delete_order() -> None:
    journal = _cleaned_journal()
    jail_pv = next(
        item
        for item in journal["kubernetes_resources"]
        if item["kind"] == "PersistentVolume" and item["name"].endswith("jail-pv")
    )
    key = "|".join(
        str(jail_pv[field])
        for field in ("api_version", "kind", "namespace", "name", "uid")
    )
    journal["cleanup"]["kubernetes_operations"][key]["intent_at"] = (
        "2026-07-12T10:23:30Z"
    )

    with pytest.raises(ValueError, match="both PVs after"):
        validate_bridge_journal(journal)


def test_substrate_ready_requires_pre_source_mutation_canary() -> None:
    journal = _cleaned_journal()
    journal["stage"] = BridgeStage.SUBSTRATE_READY.value
    journal["shared_mount_canaries"] = []
    journal["pre_source_fence_readiness"] = {}
    journal["source_fence_intent"] = {}

    with pytest.raises(ValueError, match="pre-source-mutation shared-SFS canary"):
        validate_bridge_journal(journal)


def test_pre_source_mutation_canary_binds_exact_node_group_sfs_attachment() -> None:
    journal = _cleaned_journal()
    journal["stage"] = BridgeStage.SUBSTRATE_READY.value
    journal["node_groups"][1]["controller_spool_attachment_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="controller-spool"):
        validate_bridge_journal(journal)


def test_cleaned_bridge_journal_rejects_tampered_pre_cleanup_proof() -> None:
    journal = _cleaned_journal()
    pre_cleanup = next(
        item
        for item in journal["preservation_jobs"]["verifications"]
        if item["stage"] == "pre-cleanup"
    )
    pre_cleanup["jobs"] = {"42": {"status": "changed-after-proof"}}

    with pytest.raises(ValueError, match="preservation proof binding drifted"):
        validate_bridge_journal(journal)


def test_handoff_validated_journal_requires_final_client_propagation() -> None:
    journal = _cleaned_journal()
    journal["stage"] = BridgeStage.HANDOFF_VALIDATED.value
    journal["target_singleton_takeover"].pop("final_client_propagation")

    with pytest.raises(ValueError, match="final singleton client propagation"):
        validate_bridge_journal(journal)


@pytest.mark.parametrize(
    "count_field",
    (
        "slurmctld_count",
        "writable_state_mount_count",
        "inspected_process_count",
        "unreadable_process_count",
    ),
)
def test_bridge_journal_rejects_boolean_runtime_fence_counts(count_field: str) -> None:
    journal = _cleaned_journal()
    runtime_evidence = journal["fencing"]["source"]["runtime_evidence"]
    runtime_evidence[0][count_field] = False

    with pytest.raises(ValueError, match="process and writable-mount absence"):
        validate_bridge_journal(journal)
