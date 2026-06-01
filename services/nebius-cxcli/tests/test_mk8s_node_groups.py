from __future__ import annotations

from nebius_cxcli.mk8s_node_groups import (
    cluster_name,
    cluster_public_endpoint_enabled,
    cluster_service_cidrs,
    cluster_subnet_id,
    first_gpu_node_group,
    gpu_cluster_enabled,
    gpu_cluster_fabric,
    gpu_enabled,
    iter_node_groups,
    legacy_mk8s_input_keys,
)


def test_cluster_helpers_read_typed_cluster_inputs() -> None:
    inputs = {
        "cluster": {
            "cluster_name": "training",
            "subnet_id": "subnet-1",
            "public_endpoint": "true",
            "kube_network": {"service_cidrs": ["/20", "", None]},
        }
    }

    assert cluster_name(inputs) == "training"
    assert cluster_subnet_id(inputs) == "subnet-1"
    assert cluster_public_endpoint_enabled(inputs) is True
    assert cluster_service_cidrs(inputs) == ("/20",)


def test_legacy_mk8s_shortcut_keys_are_reported_for_fail_fast_validation() -> None:
    assert legacy_mk8s_input_keys(
        {
            "cluster": {},
            "gpu_enabled": True,
            "cpu_nodes_platform": "cpu-d3",
            "mk8s_cluster_overrides": {},
        }
    ) == ("cpu_nodes_platform", "gpu_enabled", "mk8s_cluster_overrides")


def test_node_group_helpers_skip_disabled_groups_and_coerce_values() -> None:
    groups = iter_node_groups(
        {
            "node_groups": {
                "disabled": {
                    "enabled": False,
                    "gpu": True,
                    "platform": "gpu-h100-sxm",
                    "preset": "8gpu-128vcpu-1600gb",
                },
                "cpu": {
                    "platform": "cpu-d3",
                    "preset": "4vcpu-16gb",
                    "node_count": "0",
                },
                "gpu": {
                    "gpu": "yes",
                    "platform": "gpu-h100-sxm",
                    "preset": "8gpu-128vcpu-1600gb",
                    "node_count": "-1",
                    "autoscaling": {
                        "min_node_count": "1",
                        "max_node_count": "4",
                    },
                    "gpu_cluster_key": "fabric-a",
                    "reservation": {"reservation_ids": ["cbg-1", ""]},
                    "node_labels": {"role": "worker"},
                    "labels": {"env": "test"},
                },
            }
        }
    )

    assert [group.key for group in groups] == ["cpu", "gpu"]
    assert groups[0].node_count == 0
    assert groups[0].gpu is False
    assert groups[1].gpu is True
    assert groups[1].node_count is None
    assert groups[1].autoscaling_min_node_count == 1
    assert groups[1].autoscaling_max_node_count == 4
    assert groups[1].reservation_policy == "FORBID"
    assert groups[1].reservation_ids == ("cbg-1",)
    assert groups[1].node_labels == {"role": "worker"}
    assert groups[1].labels == {"env": "test"}


def test_gpu_cluster_helpers_use_first_enabled_gpu_group() -> None:
    inputs = {
        "gpu_clusters": {
            "fabric-a": {"infiniband_fabric": "fabric-6"},
        },
        "node_groups": {
            "worker": {
                "gpu": True,
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "gpu_cluster_key": "fabric-a",
            }
        },
    }

    group = first_gpu_node_group(inputs)
    assert group is not None
    assert group.key == "worker"
    assert gpu_enabled(inputs) is True
    assert gpu_cluster_enabled(inputs) is True
    assert gpu_cluster_fabric(inputs, group) == "fabric-6"


def test_gpu_cluster_enabled_accepts_existing_gpu_cluster_id() -> None:
    inputs = {
        "node_groups": {
            "worker": {
                "gpu": True,
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "gpu_cluster_id": "gpucluster-existing",
            }
        },
    }

    assert gpu_cluster_enabled(inputs) is True


def test_gpu_stack_source_defaults_gpu_groups_to_nebius_image() -> None:
    groups = iter_node_groups(
        {
            "node_groups": {
                "h100": {
                    "gpu": True,
                    "node_count": 2,
                    "labels": {
                        "nebius.com/driverful": "true",
                        "nebius.com/drivers-preset": "cuda13.0",
                        "nebius.com/node-group": "h100",
                    },
                },
                "operator-gpu": {
                    "gpu": True,
                    "node_count": 2,
                    "labels": {"nebius.com/node-group": "operator-gpu"},
                },
            }
        }
    )

    assert groups[0].gpu_stack_source == "nebius_image"
    assert groups[1].gpu_stack_source == "nebius_image"


def test_gpu_stack_source_normalizes_explicit_value_case() -> None:
    groups = iter_node_groups(
        {
            "node_groups": {
                "h100": {
                    "gpu": True,
                    "gpu_stack_source": "Nebius_Image",
                },
            }
        }
    )

    assert groups[0].gpu_stack_source == "nebius_image"
