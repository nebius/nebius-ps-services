from __future__ import annotations

import copy

import pytest

from nebius_cxcli.soperator_upgrade_campaign import (
    compile_node_group_hop_targets,
    effective_campaign_segment_for_replacements,
    external_soperator_final_health_conflicts,
    finalize_soperator_upgrade_campaign,
    provider_control_plane_versions,
    snapshot_campaign_identity,
    soperator_upgrade_campaign_fingerprint,
    validate_campaign_segment_capabilities,
    validated_control_plane_path,
)


def _healthy_final_campaign_and_snapshot() -> tuple[dict[str, object], dict[str, object]]:
    group = {
        "id": "ng-worker-gpu",
        "name": "worker-gpu",
        "role": "worker-gpu",
        "platform": "gpu-h100-sxm",
        "preset": "8gpu-128vcpu-1600gb",
        "gpu_software_mode": "provider-managed",
        "source": {
            "kubernetes_version": "1.33",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda12.8",
        },
        "target": {
            "kubernetes_version": "1.34",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda13.0",
        },
    }
    campaign: dict[str, object] = {
        "identity": {
            "cluster_id": "cluster-1",
            "slurmcluster_uid": "slurm-uid",
        },
        "final_targets": {"kubernetes": "1.34"},
        "mk8s": {"node_groups": [group]},
        "managed_operators": {"gpu": {}, "network": {}},
    }
    snapshot: dict[str, object] = {
        "provider": {
            "mk8s_cluster": {
                "id": "cluster-1",
                "provider_state": "RUNNING",
                "provider_reconciling": False,
                "operation_terminal": True,
                "control_plane_version": "1.34",
            }
        },
        "node_groups": {
            "worker-gpu": {
                "node_count": 1,
                "provider": {
                    "node_group_id": "ng-worker-gpu",
                    "provider_state": "RUNNING",
                    "provider_reconciling": False,
                    "operation_terminal": True,
                    "target_node_count": 1,
                    "node_count": 1,
                    "ready_node_count": 1,
                    "outdated_node_count": 0,
                },
            }
        },
        "kubernetes_nodes": [
            {
                "metadata": {"name": "worker-gpu-0"},
                "spec": {"unschedulable": False},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ],
        "soperator_namespace_resources": [
            {
                "kind": "Deployment",
                "metadata": {"name": "soperator-manager"},
                "spec": {"replicas": 1},
                "status": {"replicas": 1, "readyReplicas": 1, "updatedReplicas": 1},
            },
            {
                "kind": "Pod",
                "metadata": {"name": "login-0"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
        ],
        "helm_releases": [{"name": "soperator", "status": "deployed"}],
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "training", "uid": "slurm-uid"},
                "status": {"phase": "Available"},
            },
            {
                "kind": "NodeSet",
                "metadata": {"name": "worker-gpu"},
                "status": {"phase": "Ready"},
            },
        ],
        "slurm_health": {"checked": True, "healthy": True},
        "gpu_stack": {
            "policies": [
                {"kind": "ClusterPolicy", "status": {"state": "ready"}},
                {"kind": "NicClusterPolicy", "status": {"state": "ready"}},
            ]
        },
        "collection_errors": [],
    }
    return campaign, snapshot


def test_external_soperator_final_health_accepts_fresh_healthy_state() -> None:
    campaign, snapshot = _healthy_final_campaign_and_snapshot()

    assert not external_soperator_final_health_conflicts(
        campaign=campaign,
        snapshot=snapshot,
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda snapshot: snapshot["provider"]["mk8s_cluster"].update(  # type: ignore[index,union-attr]
                {"provider_reconciling": True, "operation_terminal": False}
            ),
            "mk8s.control_plane.provider-not-terminal",
        ),
        (
            lambda snapshot: snapshot["node_groups"]["worker-gpu"]["provider"].update(  # type: ignore[index,union-attr]
                {"ready_node_count": 0, "operation_terminal": False}
            ),
            "mk8s.node_groups[ng-worker-gpu].provider-not-ready",
        ),
        (
            lambda snapshot: snapshot["kubernetes_nodes"][0]["status"].update(  # type: ignore[index,union-attr]
                {"conditions": [{"type": "Ready", "status": "False"}]}
            ),
            "kubernetes.nodes[worker-gpu-0].not-ready",
        ),
        (
            lambda snapshot: snapshot["soperator_namespace_resources"][0][  # type: ignore[index]
                "status"
            ].update({"readyReplicas": 0}),
            "kubernetes.workloads[Deployment/soperator-manager].not-ready",
        ),
        (
            lambda snapshot: snapshot["gpu_stack"]["policies"][0]["status"].update(  # type: ignore[index,union-attr]
                {"state": "notReady"}
            ),
            "gpu-stack.clusterpolicy-not-ready",
        ),
        (
            lambda snapshot: snapshot["gpu_stack"]["policies"][1]["status"].update(  # type: ignore[index,union-attr]
                {"state": "notReady"}
            ),
            "gpu-stack.nicclusterpolicy-not-ready",
        ),
        (
            lambda snapshot: snapshot["soperator_resources"][0]["status"].update(  # type: ignore[index,union-attr]
                {"phase": "Unavailable"}
            ),
            "soperator.slurmcluster-not-available",
        ),
        (
            lambda snapshot: snapshot["slurm_health"].update({"healthy": False}),  # type: ignore[union-attr]
            "slurm.scontrol-ping",
        ),
    ],
)
def test_external_soperator_final_health_rejects_fresh_degradation(
    mutate: object,
    expected: str,
) -> None:
    campaign, snapshot = _healthy_final_campaign_and_snapshot()
    assert callable(mutate)
    mutate(snapshot)

    assert expected in external_soperator_final_health_conflicts(
        campaign=campaign,
        snapshot=snapshot,
    )


def _snapshot() -> dict[str, object]:
    matrices: dict[str, object] = {}
    for version in ("1.32", "1.33", "1.34"):
        matrices[version] = {
            "cpu-d3": [{"platform": "cpu-d3", "os": "ubuntu24.04", "drivers_preset": ""}],
            "gpu-h100-sxm": [
                {
                    "platform": "gpu-h100-sxm",
                    "os": "ubuntu24.04",
                    "drivers_preset": "cuda12.8",
                },
                {
                    "platform": "gpu-h100-sxm",
                    "os": "ubuntu24.04",
                    "drivers_preset": "cuda13.0",
                },
            ],
        }
    return {
        "provider": {
            "control_plane_versions": ["1.34.4", "1.32", "1.33", "1.32.9"],
            "compatibility_matrix": matrices,
        },
        "node_groups": {
            "system": {
                "provider": {
                    "node_group_id": "ng-system",
                    "node_group_name": "system",
                    "node_template": {
                        "k8s_version": "1.31",
                        "os": "ubuntu24.04",
                        "platform": "cpu-d3",
                        "preset": "4vcpu-16gb",
                        "gpu_stack_preset": "",
                    },
                },
                "gpu": False,
            },
            "worker-gpu": {
                "provider": {
                    "node_group_id": "ng-worker-gpu",
                    "node_group_name": "worker-gpu",
                    "node_template": {
                        "k8s_version": "1.31",
                        "os": "ubuntu24.04",
                        "platform": "gpu-h100-sxm",
                        "preset": "8gpu-128vcpu-1600gb",
                        "gpu_stack_preset": "cuda12.8",
                    },
                },
                "gpu": True,
            },
        },
    }


def test_provider_graph_builds_all_contiguous_hops_and_exact_group_tuples() -> None:
    snapshot = _snapshot()

    assert provider_control_plane_versions(snapshot) == ("1.32", "1.33", "1.34")
    path = validated_control_plane_path(
        snapshot,
        current_version="1.31",
        target_version="1.34",
    )
    assert path == ("1.31", "1.32", "1.33", "1.34")

    targets = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=path,
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda13.0", "cuda12.8"),
    )

    assert tuple(targets) == (
        ("1.31", "1.32"),
        ("1.32", "1.33"),
        ("1.33", "1.34"),
    )
    for groups in targets.values():
        assert [group["id"] for group in groups] == ["ng-system", "ng-worker-gpu"]
        gpu = groups[1]
        assert gpu["target"] == {
            "kubernetes_version": groups[0]["target"]["kubernetes_version"],
            "os": "ubuntu24.04",
            "drivers_preset": "cuda12.8",
        }


def test_provider_graph_fails_closed_for_missing_control_plane_hop() -> None:
    snapshot = _snapshot()
    snapshot["provider"]["control_plane_versions"] = ["1.32", "1.34"]  # type: ignore[index]

    with pytest.raises(ValueError, match="Missing: 1.33"):
        validated_control_plane_path(
            snapshot,
            current_version="1.31",
            target_version="1.34",
        )


@pytest.mark.parametrize("node_group_version", ["1.30", "1.32"])
def test_provider_graph_rejects_mixed_source_node_group_versions(
    node_group_version: str,
) -> None:
    snapshot = _snapshot()
    snapshot["node_groups"]["worker-gpu"]["provider"]["node_template"][  # type: ignore[index]
        "k8s_version"
    ] = node_group_version

    with pytest.raises(
        ValueError,
        match=(
            "requires every source node group to match the control-plane source "
            "Kubernetes version 1.31"
        ),
    ):
        compile_node_group_hop_targets(
            snapshot,
            control_plane_path=("1.31", "1.32"),
            preferred_os=("ubuntu24.04",),
            preferred_drivers_presets=("cuda12.8",),
        )


def test_provider_graph_fails_closed_for_incompatible_concrete_group() -> None:
    snapshot = _snapshot()
    snapshot["provider"]["compatibility_matrix"]["1.33"]["gpu-h100-sxm"] = []  # type: ignore[index]
    path = validated_control_plane_path(
        snapshot,
        current_version="1.31",
        target_version="1.34",
    )

    with pytest.raises(ValueError, match="worker-gpu.*1.33"):
        compile_node_group_hop_targets(
            snapshot,
            control_plane_path=path,
            preferred_os=("ubuntu24.04",),
            preferred_drivers_presets=("cuda13.0", "cuda12.8"),
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("node_group_id", "", "stable provider id"),
        ("k8s_version", "", "source Kubernetes version"),
        ("platform", "", "platform"),
        ("preset", "", "hardware preset"),
        ("os", "", "source OS"),
    ],
)
def test_provider_graph_rejects_incomplete_concrete_group_identity(
    field: str,
    value: str,
    expected: str,
) -> None:
    snapshot = _snapshot()
    provider = snapshot["node_groups"]["system"]["provider"]  # type: ignore[index]
    if field == "node_group_id":
        provider[field] = value
    else:
        provider["node_template"][field] = value

    with pytest.raises(ValueError, match=expected):
        compile_node_group_hop_targets(
            snapshot,
            control_plane_path=("1.31", "1.32"),
            preferred_os=("ubuntu24.04",),
            preferred_drivers_presets=("cuda13.0",),
        )


def test_provider_graph_minimizes_tuple_churn_across_the_complete_path() -> None:
    snapshot = _snapshot()
    gpu_template = snapshot["node_groups"]["worker-gpu"]["provider"]["node_template"]  # type: ignore[index]
    gpu_template["gpu_stack_preset"] = "cuda12.7"
    matrix = snapshot["provider"]["compatibility_matrix"]  # type: ignore[index]
    matrix["1.32"]["gpu-h100-sxm"] = [
        {
            "platform": "gpu-h100-sxm",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda13.0",
        },
        {
            "platform": "gpu-h100-sxm",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda12.9",
        },
    ]
    for version in ("1.33", "1.34"):
        matrix[version]["gpu-h100-sxm"] = [
            {
                "platform": "gpu-h100-sxm",
                "os": "ubuntu24.04",
                "drivers_preset": "cuda12.9",
            }
        ]

    targets = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32", "1.33", "1.34"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda13.0", "cuda12.9"),
    )

    assert [groups[1]["target"]["drivers_preset"] for groups in targets.values()] == [
        "cuda12.9",
        "cuda12.9",
        "cuda12.9",
    ]


def test_provider_graph_uses_policy_rank_after_churn_is_equal() -> None:
    snapshot = _snapshot()
    gpu_template = snapshot["node_groups"]["worker-gpu"]["provider"]["node_template"]  # type: ignore[index]
    gpu_template["gpu_stack_preset"] = "cuda12.7"

    targets = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32", "1.33", "1.34"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda13.0", "cuda12.8"),
    )

    assert [groups[1]["target"]["drivers_preset"] for groups in targets.values()] == [
        "cuda13.0",
        "cuda13.0",
        "cuda13.0",
    ]


def test_campaign_fingerprint_is_deterministic_and_timestamp_independent() -> None:
    campaign = {
        "schema": "nebius-cxcli-ext-soperator-upgrade-campaign/v3",
        "locked": True,
        "identity": {"cluster_id": "cluster"},
        "segments": [],
    }
    first = finalize_soperator_upgrade_campaign(campaign, created_at="2026-01-01T00:00:00Z")
    second = finalize_soperator_upgrade_campaign(campaign, created_at="2026-02-01T00:00:00Z")

    assert first["fingerprint"] == second["fingerprint"]
    assert first["campaign_id"] == second["campaign_id"]
    assert soperator_upgrade_campaign_fingerprint(first) == first["fingerprint"]


def test_campaign_identity_rejects_provider_cluster_or_project_mismatch() -> None:
    snapshot = _snapshot()
    snapshot["provider"]["mk8s_cluster"] = {  # type: ignore[index]
        "id": "mk8scluster-live",
        "parent_id": "project-live",
    }
    snapshot["cluster_identity"] = {
        "kubernetes_uid": "kube-system-uid",
        "soperator_uid": "soperator-uid",
        "slurmcluster_uid": "slurmcluster-uid",
        "jail_filesystem_id": "filesystem-id",
    }

    with pytest.raises(ValueError, match="requested mk8scluster-requested"):
        snapshot_campaign_identity(
            snapshot,
            project_id="project-live",
            cluster_id="mk8scluster-requested",
            target_ref="training",
        )
    with pytest.raises(ValueError, match="config project-requested"):
        snapshot_campaign_identity(
            snapshot,
            project_id="project-requested",
            cluster_id="mk8scluster-live",
            target_ref="training",
        )


def test_provider_managed_gpu_revalidation_requires_exact_tuple() -> None:
    snapshot = _snapshot()
    path = validated_control_plane_path(
        snapshot,
        current_version="1.31",
        target_version="1.32",
    )
    planned = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=path,
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda12.8",),
    )[("1.31", "1.32")]
    segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.31", "target_version": "1.32"},
            "node_groups": planned,
        }
    }
    validate_campaign_segment_capabilities(snapshot, segment=segment)

    changed = copy.deepcopy(snapshot)
    changed["provider"]["compatibility_matrix"]["1.32"]["gpu-h100-sxm"] = [  # type: ignore[index]
        {
            "platform": "gpu-h100-sxm",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda13.0",
        }
    ]
    with pytest.raises(ValueError, match="no longer supported"):
        validate_campaign_segment_capabilities(changed, segment=segment)


def test_operator_managed_gpu_compiles_and_revalidates_driverless_target() -> None:
    snapshot = _snapshot()
    snapshot["node_groups"]["worker-gpu"]["provider"]["node_template"][  # type: ignore[index]
        "gpu_stack_preset"
    ] = ""
    planned = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda13.0", "cuda12.8"),
    )[("1.31", "1.32")]
    gpu_target = next(group for group in planned if group["id"] == "ng-worker-gpu")
    assert gpu_target["gpu_software_mode"] == "operator-managed"
    assert gpu_target["target"] == {
        "kubernetes_version": "1.32",
        "os": "ubuntu24.04",
        "drivers_preset": "",
    }

    segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.31", "target_version": "1.32"},
            "node_groups": planned,
        }
    }
    validate_campaign_segment_capabilities(snapshot, segment=segment)

    changed = copy.deepcopy(snapshot)
    changed["provider"]["compatibility_matrix"]["1.32"]["gpu-h100-sxm"] = [  # type: ignore[index]
        {
            "platform": "gpu-h100-sxm",
            "os": "ubuntu22.04",
            "drivers_preset": "cuda12.8",
        }
    ]
    with pytest.raises(ValueError, match="no longer supported"):
        validate_campaign_segment_capabilities(changed, segment=segment)


def test_live_capability_revalidation_rejects_changed_hardware_preset() -> None:
    snapshot = _snapshot()
    planned = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda12.8",),
    )[("1.31", "1.32")]
    segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.31", "target_version": "1.32"},
            "node_groups": planned,
        }
    }
    changed = copy.deepcopy(snapshot)
    changed["node_groups"]["worker-gpu"]["provider"]["node_template"][  # type: ignore[index]
        "preset"
    ] = "8gpu-256vcpu-3200gb"

    with pytest.raises(ValueError, match="hardware preset changed"):
        validate_campaign_segment_capabilities(changed, segment=segment)


def test_live_capability_revalidation_rejects_changed_role() -> None:
    snapshot = _snapshot()
    planned = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda12.8",),
    )[("1.31", "1.32")]
    segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.31", "target_version": "1.32"},
            "node_groups": planned,
        }
    }
    changed = copy.deepcopy(snapshot)
    changed["node_groups"]["worker-gpu"]["role"] = "login"  # type: ignore[index]

    with pytest.raises(ValueError, match="role changed"):
        validate_campaign_segment_capabilities(changed, segment=segment)


def test_live_capability_revalidation_rejects_extra_group_and_gpu_mode_drift() -> None:
    snapshot = _snapshot()
    planned = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda12.8",),
    )[("1.31", "1.32")]
    segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.31", "target_version": "1.32"},
            "node_groups": planned,
        }
    }
    extra = copy.deepcopy(snapshot)
    extra["node_groups"]["extra"] = copy.deepcopy(extra["node_groups"]["system"])  # type: ignore[index]
    extra["node_groups"]["extra"]["provider"]["node_group_id"] = "ng-extra"  # type: ignore[index]

    with pytest.raises(ValueError, match="unexpected ng-extra"):
        validate_campaign_segment_capabilities(extra, segment=segment)

    changed_mode = copy.deepcopy(snapshot)
    changed_mode["node_groups"]["worker-gpu"]["provider"]["node_template"][  # type: ignore[index]
        "gpu_stack_preset"
    ] = ""
    with pytest.raises(ValueError, match="GPU software mode changed"):
        validate_campaign_segment_capabilities(changed_mode, segment=segment)


def test_later_hop_revalidates_journal_bound_replacement_without_mutating_campaign() -> None:
    snapshot = _snapshot()
    targets = compile_node_group_hop_targets(
        snapshot,
        control_plane_path=("1.31", "1.32", "1.33", "1.34"),
        preferred_os=("ubuntu24.04",),
        preferred_drivers_presets=("cuda12.8",),
    )
    original_segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.32", "target_version": "1.33"},
            "node_groups": targets[("1.32", "1.33")],
        }
    }
    original_copy = copy.deepcopy(original_segment)
    bindings = (
        {
            "original_node_group_id": "ng-system",
            "original_node_group_name": "system",
            "replacement_node_group_id": "ng-controller-replacement",
            "replacement_node_group_name": "external-cluster-controller",
            "replacement_role": "controller",
        },
        {
            "original_node_group_id": "ng-system",
            "original_node_group_name": "system",
            "replacement_node_group_id": "ng-login-replacement",
            "replacement_node_group_name": "external-cluster-login",
            "replacement_role": "login",
        },
    )

    effective = effective_campaign_segment_for_replacements(
        original_segment,
        replacement_bindings=bindings,
        retired_node_group_ids=("ng-system",),
    )

    assert original_segment == original_copy
    replacement = next(
        group
        for group in effective["mk8s"]["node_groups"]
        if group["id"] == "ng-controller-replacement"
    )
    original = next(
        group for group in original_copy["mk8s"]["node_groups"] if group["id"] == "ng-system"
    )
    assert replacement["target"] == original["target"]
    assert replacement["platform"] == original["platform"]
    assert replacement["preset"] == original["preset"]
    assert replacement["role"] == "controller"

    live = copy.deepcopy(snapshot)
    original_live = live["node_groups"].pop("system")  # type: ignore[index]
    live["node_groups"]["external-cluster-controller"] = original_live  # type: ignore[index]
    live_controller = live["node_groups"]["external-cluster-controller"]  # type: ignore[index]
    live_controller["role"] = "controller"
    live_controller["provider"]["node_group_id"] = "ng-controller-replacement"
    live_controller["provider"]["node_group_name"] = "external-cluster-controller"
    live_controller["provider"]["node_template"]["k8s_version"] = "1.32"
    live_login = copy.deepcopy(original_live)
    live_login["role"] = "login"
    live_login["provider"]["node_group_id"] = "ng-login-replacement"
    live_login["provider"]["node_group_name"] = "external-cluster-login"
    live_login["provider"]["node_template"]["k8s_version"] = "1.32"
    live["node_groups"]["external-cluster-login"] = live_login  # type: ignore[index]
    live["node_groups"]["worker-gpu"]["provider"]["node_template"]["k8s_version"] = "1.32"  # type: ignore[index]
    validate_campaign_segment_capabilities(live, segment=effective)

    final_segment = {
        "mk8s": {
            "control_plane": {"source_version": "1.33", "target_version": "1.34"},
            "node_groups": targets[("1.33", "1.34")],
        }
    }
    effective_final = effective_campaign_segment_for_replacements(
        final_segment,
        replacement_bindings=bindings,
        retired_node_group_ids=("ng-system",),
    )
    for live_group in live["node_groups"].values():  # type: ignore[union-attr]
        live_group["provider"]["node_template"]["k8s_version"] = "1.33"
    validate_campaign_segment_capabilities(live, segment=effective_final)
    assert {
        group["target"]["kubernetes_version"] for group in effective_final["mk8s"]["node_groups"]
    } == {"1.34"}

    live_controller["provider"]["node_template"]["preset"] = "8vcpu-32gb"
    with pytest.raises(ValueError, match="hardware preset changed"):
        validate_campaign_segment_capabilities(live, segment=effective_final)
