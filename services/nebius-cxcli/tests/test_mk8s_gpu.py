from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import nebius_cxcli.component_sources as component_sources
import nebius_cxcli.mk8s_gpu as mk8s_gpu
import nebius_cxcli.runtime_introspection as runtime_introspection
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    reset_component_sources_cache,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.mk8s_gpu import (
    _gpu_device_plugin_snapshot,
    _gpu_visibility_node_report,
    _interesting_allocatable_resources,
    _nccl_dmabuf_metadata,
    _nccl_json_report_summary,
    _rdma_resource_keys,
    _report_log_excerpt,
    _run_operator_readiness_validation,
    ensure_mk8s_gpu_app_rows,
    mk8s_gpu_dependency_issues,
    mk8s_gpu_flux_release_dependencies,
    mk8s_gpu_project_validation_defaults,
    mk8s_gpu_validation_specs,
    normalize_mk8s_gpu_project_validation_settings,
    prune_inactive_mk8s_gpu_app_rows,
    resolve_mk8s_gpu_app_selection,
)
from nebius_cxcli.runtime_introspection import reset_runtime_introspection_cache


def _reset_catalog_override() -> None:
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()
    reset_component_entry_cache()


def setup_function() -> None:
    _reset_catalog_override()
    set_component_sources_file_override(_local_catalog_path())
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()
    reset_component_entry_cache()


def teardown_function() -> None:
    _reset_catalog_override()


@pytest.fixture(autouse=True)
def _stub_catalog_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda _source: (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
                sensitive=False,
            ),
        ),
    )


def _local_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "component_sources.yaml"


def _nccl_chart_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "helm-charts" / "nccl-test"


def _render_nccl_chart(tmp_path: Path, chart_values: dict[str, Any]) -> str:
    values_file = tmp_path / "nccl-values.yaml"
    values_file.write_text(yaml.safe_dump(chart_values, sort_keys=False), encoding="utf-8")
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "smoke",
            str(_nccl_chart_dir()),
            "--namespace",
            "nccl-test",
            "-f",
            str(values_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    return rendered.stdout


def _mk8s_inputs(
    *,
    infiniband_fabric: str = "",
    platform: str = "gpu-h100-sxm",
    preset: str = "8gpu-128vcpu-1600gb",
    gpu_stack_source: str = "nebius_image",
) -> dict[str, Any]:
    group: dict[str, Any] = {
        "node_count": 1,
        "gpu": True,
        "platform": platform,
        "preset": preset,
        "gpu_stack_source": gpu_stack_source,
    }
    inputs: dict[str, Any] = {
        "node_group_defaults": {
            "gpu": {
                "platform": platform,
                "preset": preset,
                "gpu_stack_source": gpu_stack_source,
            }
        },
        "node_groups": {"worker": group},
    }
    if infiniband_fabric:
        group["gpu_cluster_key"] = "workers"
        inputs["gpu_clusters"] = {"workers": {"infiniband_fabric": infiniband_fabric}}
    return inputs


def _mk8s_payload(
    *,
    infiniband_fabric: str = "",
    platform: str = "gpu-h100-sxm",
    preset: str = "8gpu-128vcpu-1600gb",
    gpu_stack_source: str = "nebius_image",
) -> dict:
    return {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": _mk8s_inputs(
                        infiniband_fabric=infiniband_fabric,
                        platform=platform,
                        preset=preset,
                        gpu_stack_source=gpu_stack_source,
                    ),
                }
            ]
        },
        "apps": {"charts": []},
    }


def _external_h100_sxm_payload() -> dict[str, Any]:
    return {
        "deploy": {
            "targets": [
                {
                    "instance_id": "legacy-cluster",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "access": "external",
                    "kube_context": "legacy-context",
                    "inventory": {
                        "node_groups": {
                            "cpu-d3": {"gpu": False, "node_count": 2},
                            "gpu-h100-sxm": {
                                "gpu": True,
                                "node_count": 2,
                                "allocatable": {"nvidia.com/gpu": "8"},
                                "labels": {
                                    "nebius.com/driverful": "true",
                                    "nebius.com/resource-preset": "8gpu-128vcpu-1600gb",
                                    "topology.nebius.com/gpu-cluster-id": "gpu-cluster-a",
                                    "topology.nebius.com/tier-1": "leaf-a",
                                },
                            },
                        }
                    },
                    "soperator_onboarding": {
                        "accepted": True,
                        "state": "existing-soperator-supported",
                        "actions": ["reconcile-target-gpu-stack"],
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }


def _cluster2_gpu_row() -> dict[str, Any]:
    return {
        "id": "mk8s",
        "instance_id": "cluster2",
        "enabled": True,
        "inputs": _mk8s_inputs(
            platform="gpu-h100-sxm",
            preset="1gpu-16vcpu-200gb",
            gpu_stack_source="nebius_image",
        ),
    }


def test_normalize_runtime_config_payload_keeps_cpu_only_gpu_settings_unmaterialized() -> None:
    from nebius_cxcli.config_loader import normalize_runtime_config_payload

    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "cpu": {
                                "node_count": 2,
                                "gpu": False,
                                "platform": "cpu-d3",
                                "preset": "4vcpu-16gb",
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    assert normalize_runtime_config_payload(payload) is True

    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["node_groups"]["cpu"]["gpu"] is False
    assert "mk8s_gpu" not in payload["deploy"]["targets"][0].get("validations", {})


def _set_mk8s_gpu_validation_config(
    payload: dict,
    config: dict[str, Any],
    *,
    instance_id: str = "mk8s",
) -> None:
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": instance_id,
                "validations": {
                    "mk8s_gpu": config,
                },
            }
        ]
    }


def test_mk8s_gpu_app_selection_defaults_to_gpu_operator_for_nebius_images() -> None:
    selection = resolve_mk8s_gpu_app_selection(
        _mk8s_payload(),
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )

    assert selection.gpu_stack_source == "nebius_image"
    assert selection.auto_enabled_app_ids == ("nvidia-gpu-operator",)
    assert selection.selected_app_ids == ("nvidia-gpu-operator",)
    assert selection.issues == ()


def test_mk8s_gpu_app_selection_rejects_disabled_dcgm_exporter() -> None:
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {
                "dcgmExporter": {
                    "enabled": False,
                }
            },
        }
    ]

    selection = resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids={"nvidia-gpu-operator"},
        app_entries=component_entries("apps"),
    )

    assert selection.selected_app_ids == ("nvidia-gpu-operator",)
    assert selection.issues == (
        "GPU-enabled MK8s target 'mk8s' deployment requires "
        "'nvidia-gpu-operator.values.dcgmExporter.enabled' to stay true",
    )


def test_mk8s_gpu_cluster_adds_network_operator_and_nccl_validation() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-1")

    selection = resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )
    validations = mk8s_gpu_validation_specs(payload)
    dependencies = mk8s_gpu_flux_release_dependencies(
        payload,
        release_entry_ids=set(selection.selected_app_ids),
    )

    assert selection.auto_enabled_app_ids == (
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    )
    assert {item["kind"] for item in validations} == {
        "mk8s_gpu_operator_readiness",
        "mk8s_gpu_visibility",
        "mk8s_nccl",
    }
    assert [item["kind"] for item in validations] == [
        "mk8s_gpu_operator_readiness",
        "mk8s_gpu_visibility",
        "mk8s_nccl",
    ]
    nccl_spec = next(item for item in validations if item["kind"] == "mk8s_nccl")
    gpu_visibility_spec = next(
        item for item in validations if item["kind"] == "mk8s_gpu_visibility"
    )
    assert nccl_spec["chart_component_id"] == "nccl-test"
    assert nccl_spec["chart_name_or_ref"].endswith("/helm-charts/nccl-test")
    assert nccl_spec["chart_repo"] == ""
    assert gpu_visibility_spec["max_nodes"] == 3
    assert nccl_spec["max_nodes"] == 8
    assert nccl_spec["transport_mode"] == "rdma"
    assert nccl_spec["threshold_enforced"] is True
    assert (
        nccl_spec["chart_values"]["image"]["repository"]
        == "cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/images/nccl-test"
    )
    assert nccl_spec["chart_values"]["image"]["tag"] == "0.2.0"
    assert nccl_spec["chart_values"]["benchmark"]["mpiBaseArgs"] == [
        "-bind-to",
        "none",
        "-x",
        "LD_LIBRARY_PATH",
        "-x",
        "NCCL_DEBUG=WARN",
    ]
    assert nccl_spec["chart_values"]["benchmark"]["transport"] == {"mode": "rdma"}
    assert nccl_spec["chart_values"]["worker"]["gpus"] == 8
    assert nccl_spec["chart_values"]["benchmark"]["args"] == [
        "-b",
        "512M",
        "-e",
        "8G",
        "-f",
        "2",
        "-g",
        "1",
        "-n",
        "10",
        "-w",
        "3",
        "-T",
        "180",
    ]
    assert nccl_spec["chart_values"]["benchmark"]["mpiExtraArgs"] == [
        "-x",
        "NCCL_DMABUF_ENABLE=1",
    ]
    assert dependencies == {
        "nvidia-gpu-operator": ("nvidia-network-operator",),
    }


def test_mk8s_gpu_app_selection_uses_all_gpu_node_groups_in_target() -> None:
    payload = _mk8s_payload(
        platform="gpu-h100-sxm",
        preset="1gpu-16vcpu-200gb",
    )
    inputs = payload["infra"]["components"][0]["inputs"]
    inputs["node_groups"] = {
        "ethernet": {
            "node_count": 1,
            "gpu": True,
            "platform": "gpu-h100-sxm",
            "preset": "1gpu-16vcpu-200gb",
            "gpu_stack_source": "nebius_image",
        },
        "rdma": {
            "node_count": 1,
            "gpu": True,
            "platform": "gpu-h100-sxm",
            "preset": "8gpu-128vcpu-1600gb",
            "gpu_stack_source": "nebius_image",
            "gpu_cluster_key": "rdma",
        },
    }
    inputs["gpu_clusters"] = {"rdma": {"infiniband_fabric": "fabric-1"}}

    selection = resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )

    assert len(selection.cluster_contexts) == 2
    assert {context.gpu_cluster_enabled for context in selection.cluster_contexts} == {
        False,
        True,
    }
    assert selection.auto_enabled_app_ids == (
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    )


def test_materialize_mk8s_gpu_app_values_heals_stale_network_operator_affinity_defaults() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-1")
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {
                "operator": {
                    "resources": {
                        "limits": {
                            "memory": "350Mi",
                        }
                    },
                    "ofedDriver": {"deploy": True},
                },
                "nfd": {
                    "enabled": False,
                    "deployNodeFeatureRules": False,
                },
                "node-feature-discovery": {
                    "worker": {
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [
                                        {
                                            "matchExpressions": [
                                                {
                                                    "operator": "In",
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "feature.node.kubernetes.io/pci-15b3.present",
                                        "operator": "In",
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        },
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
    ]

    mk8s_gpu.materialize_mk8s_gpu_app_values(payload)

    network_values = payload["apps"]["charts"][0]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is False
    assert network_values["nfd"]["enabled"] is True
    assert network_values["nfd"]["deployNodeFeatureRules"] is True
    nfd_selector = network_values["node-feature-discovery"]["worker"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert nfd_selector == {
        "key": "nebius.com/driverful",
        "operator": "In",
        "values": ["true"],
    }
    nic_selector = network_values["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"][0]
    assert nic_selector == {
        "key": "feature.node.kubernetes.io/pci-15b3.present",
        "operator": "In",
        "values": ["true"],
    }


def test_materialize_mk8s_gpu_app_values_clears_stale_network_operator_cluster_only_paths() -> None:
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {
                "operator": {
                    "ofedDriver": {"deploy": True},
                },
                "nfd": {
                    "enabled": True,
                    "deployNodeFeatureRules": True,
                },
                "node-feature-discovery": {
                    "worker": {
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [
                                        {
                                            "matchExpressions": [
                                                {
                                                    "key": "nebius.com/driverful",
                                                    "operator": "In",
                                                    "values": ["true"],
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "feature.node.kubernetes.io/pci-15b3.present",
                                        "operator": "In",
                                        "values": ["true"],
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        }
    ]

    mk8s_gpu.materialize_mk8s_gpu_app_values(payload)

    network_values = payload["apps"]["charts"][0]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is False
    assert "nfd" not in network_values
    assert "node-feature-discovery" not in network_values
    assert "nodeAffinity" not in network_values


def test_materialize_mk8s_gpu_app_values_keeps_optional_driverful_network_operator_safe() -> None:
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        }
    ]

    mk8s_gpu.materialize_mk8s_gpu_app_values(payload)

    network_values = payload["apps"]["charts"][0]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is False
    assert "nfd" not in network_values
    assert "node-feature-discovery" not in network_values
    assert "nodeAffinity" not in network_values


def test_materialize_mk8s_gpu_app_values_operator_managed_stack_disables_nebius_driver_crd() -> (
    None
):
    payload = _mk8s_payload(gpu_stack_source="operator_managed")
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {
                "driver": {
                    "enabled": False,
                    "nvidiaDriverCRD": {
                        "enabled": True,
                    },
                },
                "toolkit": {
                    "enabled": False,
                },
            },
        }
    ]

    mk8s_gpu.materialize_mk8s_gpu_app_values(payload)

    gpu_values = payload["apps"]["charts"][0]["values"]
    assert gpu_values["driver"]["enabled"] is True
    assert gpu_values["toolkit"]["enabled"] is True
    assert gpu_values["driver"]["nvidiaDriverCRD"]["enabled"] is False


def test_materialize_mk8s_gpu_app_values_scopes_defaults_by_target_ref() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-6")
    payload["infra"]["components"].append(_cluster2_gpu_row())
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "cluster2",
            "enabled": True,
            "target_ref": "cluster2",
            "values": {
                "nfd": {
                    "enabled": False,
                }
            },
        },
    ]

    mk8s_gpu.materialize_mk8s_gpu_app_values(payload)

    network_values = payload["apps"]["charts"][0]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is False
    assert network_values["nfd"]["enabled"] is True
    assert "rdma_shared_device_plugin" not in network_values

    rdma_gpu_values = payload["apps"]["charts"][1]["values"]
    assert rdma_gpu_values["driver"]["enabled"] is False
    assert rdma_gpu_values["toolkit"]["enabled"] is False
    assert rdma_gpu_values["driver"]["nvidiaDriverCRD"]["enabled"] is False
    assert rdma_gpu_values["nfd"]["enabled"] is False
    assert "node-feature-discovery" not in rdma_gpu_values

    ethernet_gpu_values = payload["apps"]["charts"][2]["values"]
    assert ethernet_gpu_values["driver"]["enabled"] is False
    assert ethernet_gpu_values["toolkit"]["enabled"] is False
    assert ethernet_gpu_values["driver"]["nvidiaDriverCRD"]["enabled"] is False
    assert "nfd" not in ethernet_gpu_values
    assert ethernet_gpu_values["node-feature-discovery"]["worker"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0] == {
        "key": "nebius.com/gpu",
        "operator": "In",
        "values": ["true"],
    }


def test_mk8s_gpu_dependency_issues_require_app_rows_per_target() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-6")
    payload["infra"]["components"].append(_cluster2_gpu_row())
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
    ]

    issues = mk8s_gpu_dependency_issues(payload)

    assert issues == [
        "GPU-enabled MK8s target 'cluster2' requires 'apps:nvidia-gpu-operator' "
        "to be enabled with instance_id 'cluster2'",
    ]


def test_ensure_mk8s_gpu_app_rows_seeds_required_apps_per_target() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-6")
    payload["infra"]["components"].append(_cluster2_gpu_row())
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
    ]

    changed = ensure_mk8s_gpu_app_rows(payload, app_entries=component_entries("apps"))

    assert changed is True
    gpu_operator_rows = sorted(
        [row for row in payload["apps"]["charts"] if row["id"] == "nvidia-gpu-operator"],
        key=lambda item: item["target_ref"],
    )
    assert [row["target_ref"] for row in gpu_operator_rows] == ["cluster2", "mk8s"]
    cluster2_row = gpu_operator_rows[0]
    assert cluster2_row["instance_id"] == "cluster2"
    assert cluster2_row["namespace"] == "nvidia-gpu-operator"
    assert cluster2_row["release-name"] == "gpu-operator"
    assert [
        row["target_ref"]
        for row in payload["apps"]["charts"]
        if row["id"] == "nvidia-network-operator"
    ] == ["mk8s"]


def test_external_mk8s_inventory_enables_gpu_cluster_operator_stack() -> None:
    payload = _external_h100_sxm_payload()

    selection = resolve_mk8s_gpu_app_selection(payload, app_entries=component_entries("apps"))

    assert set(selection.auto_enabled_app_ids) == {
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    }
    assert selection.cluster_contexts[0].instance_id == "legacy-cluster"
    assert selection.cluster_contexts[0].gpu_platform == "gpu-h100-sxm"
    assert selection.cluster_contexts[0].gpu_preset == "8gpu-128vcpu-1600gb"
    assert selection.cluster_contexts[0].gpu_stack_source == "nebius_image"
    assert selection.cluster_contexts[0].gpu_cluster_enabled is True

    assert normalize_mk8s_gpu_project_validation_settings(payload) is True
    assert ensure_mk8s_gpu_app_rows(payload, app_entries=component_entries("apps")) is True

    mk8s_gpu_validations = payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]
    assert mk8s_gpu_validations["operator_readiness"]["enabled"] is True
    assert mk8s_gpu_validations["gpu_visibility"]["enabled"] is True
    assert mk8s_gpu_validations["nccl"]["enabled"] is True
    assert {
        (row["id"], row["instance_id"], row["target_ref"])
        for row in payload["apps"]["charts"]
    } == {
        ("nvidia-gpu-operator", "legacy-cluster", "legacy-cluster"),
        ("nvidia-network-operator", "legacy-cluster", "legacy-cluster"),
    }


def test_external_mk8s_single_gpu_inventory_enables_gpu_operator_only() -> None:
    payload = _external_h100_sxm_payload()
    gpu_group = payload["deploy"]["targets"][0]["inventory"]["node_groups"]["gpu-h100-sxm"]
    gpu_group["labels"] = {
        "nebius.com/driverful": "true",
        "nebius.com/resource-preset": "1gpu-16vcpu-200gb",
    }
    gpu_group["allocatable"] = {"nvidia.com/gpu": "1"}

    selection = resolve_mk8s_gpu_app_selection(payload, app_entries=component_entries("apps"))

    assert selection.auto_enabled_app_ids == ("nvidia-gpu-operator",)


def test_prune_inactive_mk8s_gpu_app_rows_removes_stale_operator_only_apps() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "system": {
                                "node_count": 1,
                                "gpu": False,
                                "platform": "cpu-d3",
                                "preset": "8vcpu-32gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "nvidia-gpu-operator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "target_ref": "mk8s",
                    "values": {},
                },
                {
                    "id": "nvidia-network-operator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "target_ref": "mk8s",
                    "values": {},
                },
                {
                    "id": "soperator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "target_ref": "mk8s",
                    "values": {},
                },
            ]
        },
    }

    changed = prune_inactive_mk8s_gpu_app_rows(payload)

    assert changed is True
    assert [row["id"] for row in payload["apps"]["charts"]] == ["soperator"]


def test_prune_inactive_mk8s_gpu_app_rows_removes_stripped_soperator_policy_apps() -> None:
    payload = {
        "deploy": {"targets": [{"instance_id": "mk8s"}]},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "worker-cpu": {
                                "node_count": 1,
                                "gpu": False,
                                "platform": "cpu-d3",
                                "preset": "8vcpu-32gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "values": {},
                },
                {
                    "id": "nvidia-network-operator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "group": "platform",
                    "repo": (
                        "oci://cr.eu-north1.nebius.cloud/marketplace/nebius/"
                        "nvidia-network-operator/chart/network-operator"
                    ),
                    "version": "25.7.0",
                    "namespace": "nvidia-network-operator",
                    "release-name": "network-operator",
                    "values": {},
                },
            ]
        },
    }

    changed = prune_inactive_mk8s_gpu_app_rows(payload)

    assert changed is True
    assert [row["id"] for row in payload["apps"]["charts"]] == ["soperator"]


def test_prune_inactive_mk8s_gpu_app_rows_keeps_required_and_applicable_gpu_apps() -> None:
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        }
    ]

    changed = prune_inactive_mk8s_gpu_app_rows(payload)

    assert changed is False
    assert [row["id"] for row in payload["apps"]["charts"]] == [
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    ]


def test_mk8s_gpu_post_render_patches_are_target_scoped() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-6")
    payload["infra"]["components"].append(_cluster2_gpu_row())

    patches = mk8s_gpu.mk8s_gpu_flux_release_post_render_patches(
        payload,
        release_entry_ids={"nvidia-network-operator"},
    )

    assert ("mk8s", "nvidia-network-operator") in patches
    assert ("cluster2", "nvidia-network-operator") not in patches


def test_mk8s_gpu_validation_overrides_can_disable_defaults_and_tune_nccl() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-1")
    _set_mk8s_gpu_validation_config(
        payload,
        {
            "operator_readiness": {"enabled": False},
            "gpu_visibility": {"enabled": False, "max_nodes": 2},
            "nccl": {
                "enabled": True,
                "max_nodes": 4,
                "average_bus_bandwidth_threshold_gbps": 350,
            },
        },
    )

    validations = mk8s_gpu_validation_specs(payload)

    assert {item["kind"] for item in validations} == {"mk8s_nccl"}
    nccl_spec = validations[0]
    assert nccl_spec["max_nodes"] == 4
    assert nccl_spec["average_bus_bandwidth_threshold_gbps"] == 350


def test_mk8s_gpu_validation_warnings_flag_single_gpu_nccl_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.ProviderOptionLookup.compute_platform_preset_resources",
        lambda self, *, project_id, platform_name, preset_name: (16, 200, 1),
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.ProviderOptionLookup.compute_platform_preset_allows_gpu_clustering",
        lambda self, *, project_id, platform_name, preset_name: False,
    )
    payload = {
        "client_info": {
            "nebius": {
                "project_id": "project-1",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": _mk8s_inputs(
                        platform="gpu-h100-sxm",
                        preset="1gpu-16vcpu-200gb",
                    ),
                }
            ]
        },
        "apps": {"charts": []},
    }

    warnings = mk8s_gpu.mk8s_gpu_validation_warnings(payload)
    validations = mk8s_gpu_validation_specs(payload)

    assert len(warnings) == 1
    assert warnings[0] == (
        "NCCL validation is enabled for 1-GPU Ethernet-only MK8s shape "
        "gpu-h100-sxm/1gpu-16vcpu-200gb; it will use Ethernet/TCPIP, not InfiniBand / "
        "GPUDirect-RDMA, so the run is a smoke check only: no collective bandwidth is "
        "expected and the result is not production-training representative."
    )
    assert {item["kind"] for item in validations} == {
        "mk8s_gpu_operator_readiness",
        "mk8s_gpu_visibility",
        "mk8s_nccl",
    }
    nccl_spec = next(item for item in validations if item["kind"] == "mk8s_nccl")
    assert nccl_spec["transport_mode"] == "socket"
    assert nccl_spec["threshold_enforced"] is False
    assert nccl_spec["chart_values"]["benchmark"]["transport"] == {"mode": "socket"}
    assert nccl_spec["chart_values"]["worker"]["gpus"] == 1


def test_mk8s_gpu_validation_warnings_flag_soperator_activechecks_nccl_overlap() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-a")
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
            "values": {"soperator-activechecks": {"enabled": True}},
        }
    ]
    _set_mk8s_gpu_validation_config(payload, {"nccl": {"enabled": True}})

    warnings = mk8s_gpu.mk8s_gpu_validation_warnings(payload)

    assert warnings == (
        "Soperator NCCL ActiveChecks and deploy-time NCCL validation are both enabled "
        "for MK8s target mk8s; the Soperator Slurm NCCL checks and cxcli Kubernetes "
        "MPIJob can compete for GPUs and RDMA bandwidth, skew benchmark results, "
        "delay deploy, or skip/fail after Soperator workers consume the GPUs. Prefer "
        "one validation path, or run cxcli NCCL before Soperator workers and "
        "ActiveChecks occupy the GPU nodes.",
    )


def test_mk8s_gpu_validation_warnings_skip_default_disabled_soperator_activechecks() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-a")
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
        }
    ]
    _set_mk8s_gpu_validation_config(payload, {"nccl": {"enabled": True}})

    warnings = mk8s_gpu.mk8s_gpu_validation_warnings(payload)

    assert warnings == ()


def test_mk8s_gpu_validation_warnings_skip_disabled_soperator_activechecks() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-a")
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
            "values": {"soperator-activechecks": {"enabled": False}},
        }
    ]
    _set_mk8s_gpu_validation_config(payload, {"nccl": {"enabled": True}})

    assert mk8s_gpu.mk8s_gpu_validation_warnings(payload) == ()


def test_mk8s_gpu_validation_warnings_skip_disabled_soperator_nccl_activechecks() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-a")
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
            "values": {
                "soperator-activechecks": {
                    "enabled": True,
                    "checks": {
                        "all-reduce-perf-nccl-in-docker": {"enabled": False},
                        "all-reduce-perf-nccl-with-ib": {"enabled": False},
                        "all-reduce-perf-nccl-without-ib": {"enabled": False},
                    },
                }
            },
        }
    ]
    _set_mk8s_gpu_validation_config(payload, {"nccl": {"enabled": True}})

    assert mk8s_gpu.mk8s_gpu_validation_warnings(payload) == ()


def test_mk8s_gpu_validation_warnings_are_target_scoped_for_soperator_activechecks() -> None:
    payload = _mk8s_payload(infiniband_fabric="fabric-a")
    payload["infra"]["components"].append(_cluster2_gpu_row())
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "soperator-cluster2",
            "target_ref": "cluster2",
            "enabled": True,
        }
    ]
    _set_mk8s_gpu_validation_config(payload, {"nccl": {"enabled": True}})
    payload["deploy"]["targets"].append(
        {
            "instance_id": "cluster2",
            "validations": {"mk8s_gpu": {"nccl": {"enabled": False}}},
        }
    )

    warnings = mk8s_gpu.mk8s_gpu_validation_warnings(payload)

    assert all("Soperator NCCL ActiveChecks" not in warning for warning in warnings)


def test_nccl_live_runtime_overrides_prefer_non_gpu_launcher_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_nodes = [
        {"name": "gpu-node-a", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
        {"name": "gpu-node-b", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
    ]

    spec = {
        "chart_values": {
            "launcher": {
                "resources": {
                    "requests": {
                        "cpu": "2",
                        "memory": "1Gi",
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        mk8s_gpu,
        "_ready_node_inventory",
        lambda **_kwargs: [
            {
                "name": "cpu-node",
                "gpu_count": 0,
                "allocatable_cpu_millicores": 8000,
                "allocatable_memory_bytes": 64 * (1 << 30),
                "allocatable_resources": {},
            },
            {
                "name": "gpu-node-a",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
            {
                "name": "gpu-node-b",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
        ],
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_pod_request_totals_by_node",
        lambda **_kwargs: {
            "gpu-node-a": {"cpu_millicores": 0, "memory_bytes": 0},
            "gpu-node-b": {"cpu_millicores": 0, "memory_bytes": 0},
        },
    )

    overrides, metadata = mk8s_gpu._nccl_live_runtime_overrides(
        spec=spec,
        worker_nodes=worker_nodes,
        extra_env=None,
    )

    assert overrides["worker"]["resources"]["requests"] == {"cpu": "16000m", "memory": "128Gi"}
    assert overrides["worker"]["resources"]["limits"] == {"cpu": "16000m", "memory": "128Gi"}
    assert overrides["launcher"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0] == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["cpu-node"],
    }
    assert metadata == {
        "worker_request_cpu": "16000m",
        "worker_request_memory": "128Gi",
        "launcher_non_gpu_node_names": ["cpu-node"],
    }


def test_nccl_live_runtime_overrides_filters_non_gpu_launcher_nodes_by_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_nodes = [
        {"name": "gpu-node-a", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
        {"name": "gpu-node-b", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
    ]

    spec = {
        "chart_values": {
            "launcher": {
                "resources": {
                    "requests": {
                        "cpu": "2",
                        "memory": "1Gi",
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        mk8s_gpu,
        "_ready_node_inventory",
        lambda **_kwargs: [
            {
                "name": "cpu-low",
                "gpu_count": 0,
                "allocatable_cpu_millicores": 4000,
                "allocatable_memory_bytes": 8 * (1 << 30),
                "allocatable_resources": {},
            },
            {
                "name": "cpu-ok",
                "gpu_count": 0,
                "allocatable_cpu_millicores": 4000,
                "allocatable_memory_bytes": 8 * (1 << 30),
                "allocatable_resources": {},
            },
            {
                "name": "gpu-node-a",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
            {
                "name": "gpu-node-b",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
        ],
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_pod_request_totals_by_node",
        lambda **_kwargs: {
            "cpu-low": {"cpu_millicores": 3000, "memory_bytes": 0},
            "cpu-ok": {"cpu_millicores": 1000, "memory_bytes": 1 << 30},
            "gpu-node-a": {"cpu_millicores": 0, "memory_bytes": 0},
            "gpu-node-b": {"cpu_millicores": 0, "memory_bytes": 0},
        },
    )

    overrides, metadata = mk8s_gpu._nccl_live_runtime_overrides(
        spec=spec,
        worker_nodes=worker_nodes,
        extra_env=None,
    )

    assert overrides["worker"]["resources"]["requests"] == {"cpu": "16000m", "memory": "128Gi"}
    assert overrides["launcher"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0] == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["cpu-ok"],
    }
    assert metadata["launcher_non_gpu_node_names"] == ["cpu-ok"]


def test_nccl_live_runtime_overrides_falls_back_when_non_gpu_launcher_nodes_lack_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_nodes = [
        {"name": "gpu-node-a", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
        {"name": "gpu-node-b", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
    ]

    spec = {
        "chart_values": {
            "launcher": {
                "resources": {
                    "requests": {
                        "cpu": "2",
                        "memory": "1Gi",
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        mk8s_gpu,
        "_ready_node_inventory",
        lambda **_kwargs: [
            {
                "name": "cpu-low",
                "gpu_count": 0,
                "allocatable_cpu_millicores": 4000,
                "allocatable_memory_bytes": 8 * (1 << 30),
                "allocatable_resources": {},
            },
            {
                "name": "gpu-node-a",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
            {
                "name": "gpu-node-b",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
        ],
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_pod_request_totals_by_node",
        lambda **_kwargs: {
            "cpu-low": {"cpu_millicores": 3000, "memory_bytes": 0},
            "gpu-node-a": {"cpu_millicores": 0, "memory_bytes": 0},
            "gpu-node-b": {"cpu_millicores": 0, "memory_bytes": 0},
        },
    )

    overrides, metadata = mk8s_gpu._nccl_live_runtime_overrides(
        spec=spec,
        worker_nodes=worker_nodes,
        extra_env=None,
    )

    assert overrides["worker"]["resources"]["requests"] == {"cpu": "14000m", "memory": "127Gi"}
    assert "launcher" not in overrides
    assert metadata == {
        "worker_request_cpu": "14000m",
        "worker_request_memory": "127Gi",
        "launcher_non_gpu_node_names": [],
    }


def test_nccl_live_runtime_overrides_subtract_launcher_headroom_without_cpu_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_nodes = [
        {"name": "gpu-node-a", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
        {"name": "gpu-node-b", "gpu_count": 1, "allocatable_resources": {"nvidia.com/gpu": "1"}},
    ]

    spec = {
        "chart_values": {
            "launcher": {
                "resources": {
                    "requests": {
                        "cpu": "2",
                        "memory": "1Gi",
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        mk8s_gpu,
        "_ready_node_inventory",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
            {
                "name": "gpu-node-b",
                "gpu_count": 1,
                "allocatable_cpu_millicores": 16000,
                "allocatable_memory_bytes": 128 * (1 << 30),
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            },
        ],
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_pod_request_totals_by_node",
        lambda **_kwargs: {
            "gpu-node-a": {"cpu_millicores": 0, "memory_bytes": 0},
            "gpu-node-b": {"cpu_millicores": 0, "memory_bytes": 0},
        },
    )

    overrides, metadata = mk8s_gpu._nccl_live_runtime_overrides(
        spec=spec,
        worker_nodes=worker_nodes,
        extra_env=None,
    )

    assert overrides["worker"]["resources"]["requests"] == {"cpu": "14000m", "memory": "127Gi"}
    assert overrides["worker"]["resources"]["limits"] == {"cpu": "14000m", "memory": "127Gi"}
    assert "launcher" not in overrides
    assert metadata == {
        "worker_request_cpu": "14000m",
        "worker_request_memory": "127Gi",
        "launcher_non_gpu_node_names": [],
    }


def test_mk8s_gpu_health_checker_override_reports_missing_catalog_role() -> None:
    payload = _mk8s_payload()
    _set_mk8s_gpu_validation_config(
        payload,
        {
            "health_checker": {"enabled": True},
        },
    )

    selection = resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )

    assert selection.issues == (
        "deploy.targets[].validations.mk8s_gpu.health_checker.enabled requires one apps "
        "component with cli.mk8s_gpu_policy.role: health_checker",
    )


def test_mk8s_gpu_project_validation_defaults_include_health_checker_for_custom_catalog_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mk8s_gpu, "has_mk8s_gpu_health_checker_app", lambda: True)

    defaults = mk8s_gpu_project_validation_defaults()

    assert defaults["health_checker"] == {"enabled": False}


def test_operator_managed_b200_requires_network_operator_without_infiniband() -> None:
    payload = _mk8s_payload(platform="gpu-b200-sxm", gpu_stack_source="operator_managed")

    selection = resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=component_entries("apps"),
    )

    assert selection.auto_enabled_app_ids == (
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    )


def test_operator_managed_b200_materializes_single_network_operator_nfd_owner() -> None:
    payload = _mk8s_payload(platform="gpu-b200-sxm", gpu_stack_source="operator_managed")
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-network-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {},
        },
    ]

    mk8s_gpu.materialize_mk8s_gpu_app_values(payload)

    network_values = payload["apps"]["charts"][0]["values"]
    assert network_values["operator"]["ofedDriver"]["deploy"] is True
    assert network_values["nfd"]["enabled"] is True
    assert network_values["nfd"]["deployNodeFeatureRules"] is True
    assert "node-feature-discovery" not in network_values

    gpu_values = payload["apps"]["charts"][1]["values"]
    assert gpu_values["driver"]["enabled"] is True
    assert gpu_values["toolkit"]["enabled"] is True
    assert gpu_values["driver"]["nvidiaDriverCRD"]["enabled"] is False
    assert gpu_values["nfd"]["enabled"] is False


@pytest.mark.parametrize("platform", ["gpu-b200-sxm", "gpu-b200-sxm-a", "gpu-b300-sxm"])
def test_operator_managed_blackwell_nccl_validation_keeps_hcoll_mpi_overlay(
    platform: str,
) -> None:
    payload = _mk8s_payload(
        infiniband_fabric="fabric-1",
        platform=platform,
        gpu_stack_source="operator_managed",
    )

    validations = mk8s_gpu_validation_specs(payload)

    nccl_spec = next(item for item in validations if item["kind"] == "mk8s_nccl")

    assert (
        nccl_spec["chart_values"]["image"]["repository"]
        == "cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/images/nccl-test"
    )
    assert nccl_spec["chart_values"]["image"]["tag"] == "0.2.0"
    assert nccl_spec["chart_values"]["benchmark"]["transport"] == {"mode": "rdma"}
    assert nccl_spec["chart_values"]["benchmark"]["mpiExtraArgs"] == [
        "-mca",
        "coll",
        "^hcoll",
        "-x",
        "NCCL_DMABUF_ENABLE=1",
    ]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize(
    ("payload", "transport_mode", "expected_tokens"),
    [
        (
            {
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "instance_id": "mk8s",
                            "enabled": True,
                            "inputs": _mk8s_inputs(
                                platform="gpu-h100-sxm",
                                preset="1gpu-16vcpu-200gb",
                            ),
                        }
                    ]
                },
                "apps": {"charts": []},
            },
            "socket",
            ("NCCL_NET=Socket", "NCCL_IB_DISABLE=1"),
        ),
        (
            _mk8s_payload(infiniband_fabric="fabric-1"),
            "rdma",
            ("NCCL_NET=IB", "NCCL_DMABUF_ENABLE=1"),
        ),
    ],
)
def test_nccl_validation_chart_values_render_with_transport_mode(
    tmp_path: Path,
    payload: dict[str, Any],
    transport_mode: str,
    expected_tokens: tuple[str, ...],
) -> None:
    validations = mk8s_gpu_validation_specs(payload)
    nccl_spec = next(item for item in validations if item["kind"] == "mk8s_nccl")

    assert nccl_spec["transport_mode"] == transport_mode
    rendered = _render_nccl_chart(tmp_path, nccl_spec["chart_values"])

    for token in expected_tokens:
        assert token in rendered


def test_nccl_validation_keeps_local_chart_defaults_without_helm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingHelmClient:
        def __init__(self) -> None:
            raise RuntimeError("helm not found in PATH")

    monkeypatch.setattr(runtime_introspection, "HelmClient", _FailingHelmClient)
    reset_runtime_introspection_cache()

    payload = _mk8s_payload(infiniband_fabric="fabric-1")
    validations = mk8s_gpu_validation_specs(payload)
    nccl_spec = next(item for item in validations if item["kind"] == "mk8s_nccl")

    assert (
        nccl_spec["chart_values"]["image"]["repository"]
        == "cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/images/nccl-test"
    )
    assert nccl_spec["chart_values"]["image"]["tag"] == "0.2.0"


def test_nccl_json_report_summary_keeps_customer_report_compact() -> None:
    summary = _nccl_json_report_summary(
        {
            "nccl_version": 22703,
            "config": {
                "minimum_bytes": 536870912,
                "maximum_bytes": 8589934592,
                "step_factor": 2,
                "warmup_iters": 3,
                "iterations": 10,
                "aggregated_iterations": 1,
                "validation": 1,
                "graph": 0,
                "blocking_collectives": False,
                "parallel_init": False,
            },
            "devices": [
                {"hostname": "node-a", "device_info": "NVIDIA H100 80GB HBM3"},
                {"hostname": "node-b", "device_info": "NVIDIA H100 80GB HBM3"},
            ],
            "results": [
                {
                    "size": 536870912,
                    "in_place": {"bus_bw": 301.5},
                    "out_of_place": {"bus_bw": 298.4},
                },
                {
                    "size": 1073741824,
                    "in_place": {"bus_bw": 322.7},
                    "out_of_place": {"bus_bw": 319.1},
                },
            ],
            "out_of_bounds": {"count": 0, "okay": True},
            "average_bus_bandwidth": {"bandwidth": 312.1, "okay": "unchecked"},
            "env": ["NOISY=value"],
        }
    )

    assert summary == {
        "nccl_version": 22703,
        "config": {
            "minimum_bytes": 536870912,
            "maximum_bytes": 8589934592,
            "step_factor": 2,
            "step_bytes": None,
            "warmup_iterations": 3,
            "iterations": 10,
            "aggregated_iterations": 1,
            "validation_checks": 1,
            "graph_launches": 0,
            "blocking_collectives": False,
            "parallel_init": False,
        },
        "device_count": 2,
        "hostnames": ["node-a", "node-b"],
        "device_info": ["NVIDIA H100 80GB HBM3"],
        "result_count": 2,
        "out_of_bounds_count": 0,
        "average_bus_bandwidth_gbps": 312.1,
        "average_bus_bandwidth_ok": "unchecked",
        "peak_bus_bandwidth_gbps": 322.7,
        "peak_bus_bandwidth_size_bytes": 1073741824,
        "peak_bus_bandwidth_variant": "in_place",
    }


def test_nccl_dmabuf_metadata_reports_rendered_mpi_env() -> None:
    assert _nccl_dmabuf_metadata(
        transport_mode="rdma",
        benchmark_map={
            "mpiBaseArgs": ["-x", "NCCL_DEBUG=WARN"],
            "mpiExtraArgs": [
                "-x",
                "NCCL_DMABUF_ENABLE=0",
                "-x",
                "NCCL_DMABUF_ENABLE=1",
            ],
        },
    ) == {
        "gpudirect_mode": "dma-buf",
        "nccl_dmabuf_env_name": "NCCL_DMABUF_ENABLE",
        "nccl_dmabuf_enable": "1",
        "nccl_dmabuf_enable_source": "explicit MPI environment",
    }
    assert _nccl_dmabuf_metadata(
        transport_mode="rdma",
        benchmark_map={"mpiExtraArgs": []},
    ) == {
        "gpudirect_mode": "dma-buf default when supported",
        "nccl_dmabuf_env_name": "NCCL_DMABUF_ENABLE",
        "nccl_dmabuf_enable": "unset",
        "nccl_dmabuf_enable_source": "unset",
    }
    assert _nccl_dmabuf_metadata(
        transport_mode="socket",
        benchmark_map={"mpiExtraArgs": ["-x", "NCCL_DMABUF_ENABLE=1"]},
    ) == {
        "gpudirect_mode": "",
        "nccl_dmabuf_env_name": "NCCL_DMABUF_ENABLE",
        "nccl_dmabuf_enable": "1",
        "nccl_dmabuf_enable_source": "explicit MPI environment",
    }


def test_run_kubectl_timeout_reports_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(["kubectl", "get", "pod"], timeout=30)

    monkeypatch.setattr(mk8s_gpu.subprocess, "run", _timeout)

    with pytest.raises(RuntimeError, match="kubectl get pod timed out after 30 seconds"):
        mk8s_gpu._run_kubectl(["get", "pod"], extra_env=None, timeout_seconds=30)


def test_run_kubectl_adds_explicit_context_from_extra_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(mk8s_gpu.subprocess, "run", _run)

    mk8s_gpu._run_kubectl(
        ["get", "nodes"],
        extra_env={"KUBECTL_CONTEXT": "external-context"},
        timeout_seconds=30,
    )

    assert calls == [["kubectl", "--context", "external-context", "get", "nodes"]]


def test_gpu_visibility_retries_transient_pod_phase_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_name = "gpu-node-a"
    spec = {
        "namespace": "gpu-validation",
        "image": "cuda-sample",
        "cleanup": True,
        "timeout": "1m",
        "max_nodes": 1,
        "report_file": "gpu-visibility-report.json",
    }
    emits: list[str] = []
    snapshot_calls = 0

    monkeypatch.setattr(
        mk8s_gpu,
        "_gpu_nodes",
        lambda **_kwargs: [
            {
                "name": node_name,
                "gpu_count": 1,
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            }
        ],
    )
    monkeypatch.setattr(mk8s_gpu, "_gpu_device_plugin_snapshot", lambda _nodes: {})
    monkeypatch.setattr(mk8s_gpu, "_pod_request_totals_by_node", lambda **_kwargs: {})
    monkeypatch.setattr(mk8s_gpu, "_apply_docs", lambda _docs, **_kwargs: None)
    monkeypatch.setattr(mk8s_gpu, "_delete_resource", lambda _args, **_kwargs: None)
    monkeypatch.setattr(mk8s_gpu.time, "sleep", lambda _seconds: None)

    def _snapshot(*, pod_names, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 1:
            raise RuntimeError("kubectl get pod timed out after 30 seconds")
        return {pod_names[0]: "Succeeded"}, True, False, "Succeeded=1/1"

    monkeypatch.setattr(mk8s_gpu, "_pod_phase_snapshot", _snapshot)
    monkeypatch.setattr(
        mk8s_gpu,
        "_run_kubectl",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["kubectl"], 0, stdout="Test PASSED\n", stderr=""
        ),
    )

    report_path = mk8s_gpu._run_gpu_visibility_validation(
        spec=spec,
        reports_dir=tmp_path,
        extra_env=None,
        emit=emits.append,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert snapshot_calls == 2
    assert report["passed"] is True
    assert report["passed_node_count"] == 1
    assert any("last kubectl poll failed" in item for item in emits)


def test_gpu_visibility_skips_when_all_gpus_are_already_allocated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = {
        "namespace": "gpu-validation",
        "image": "cuda-sample",
        "cleanup": True,
        "timeout": "1m",
        "max_nodes": 1,
        "report_file": "gpu-visibility-report.json",
    }
    emits: list[str] = []

    monkeypatch.setattr(
        mk8s_gpu,
        "_gpu_nodes",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 8,
                "allocatable_resources": {"nvidia.com/gpu": "8"},
            }
        ],
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_pod_request_totals_by_node",
        lambda **_kwargs: {"gpu-node-a": {"gpu_count": 8}},
    )
    monkeypatch.setattr(mk8s_gpu, "_apply_docs", lambda _docs, **_kwargs: pytest.fail())

    report_path = mk8s_gpu._run_gpu_visibility_validation(
        spec=spec,
        reports_dir=tmp_path,
        extra_env=None,
        emit=emits.append,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["skipped"] is True
    assert report["selected_node_count"] == 0
    assert report["saturated_node_count"] == 1
    assert "already have their GPUs allocated" in report["skip_reason"]
    assert any("Skipping GPU Visibility test" in item for item in emits)


def test_nccl_validation_skips_when_all_gpus_are_already_allocated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = {
        "namespace": "nccl-test",
        "gpu_platform": "gpu-h100-sxm",
        "transport_mode": "rdma",
        "threshold_enforced": True,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "timeout": "1m",
        "max_nodes": 2,
        "report_file": "nccl-test-report.json",
    }
    emits: list[str] = []

    monkeypatch.setattr(
        mk8s_gpu,
        "_gpu_nodes",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 8,
                "allocatable_resources": {"nvidia.com/gpu": "8"},
            }
        ],
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_pod_request_totals_by_node",
        lambda **_kwargs: {"gpu-node-a": {"gpu_count": 8}},
    )
    monkeypatch.setattr(mk8s_gpu, "_training_operator_present", lambda **_kwargs: pytest.fail())

    report_path = mk8s_gpu._run_nccl_validation(
        spec=spec,
        reports_dir=tmp_path,
        extra_env=None,
        emit=emits.append,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["skipped"] is True
    assert report["selected_worker_node_count"] == 0
    assert report["saturated_node_count"] == 1
    assert "already have their GPUs allocated" in report["skip_reason"]
    assert any("Skipping NCCL test" in item for item in emits)


def test_nccl_validation_passes_single_rank_smoke_without_collective_bandwidth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = {
        "namespace": "nccl-test",
        "gpu_platform": "gpu-h100-sxm",
        "transport_mode": "socket",
        "threshold_enforced": False,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "timeout": "1m",
        "max_nodes": 1,
        "report_file": "nccl-test-report.json",
        "chart_values": {
            "image": {"repository": "nccl-test", "tag": "0.2.0"},
            "benchmark": {"args": [], "mpiBaseArgs": [], "mpiExtraArgs": []},
        },
    }
    logs = "\n".join(
        [
            "# Collective test starting: all_reduce_perf",
            "# nThread 1 nGpus 1 minBytes 536870912 maxBytes 8589934592",
            "# Out of bounds values : 0 OK",
            "# Avg bus bandwidth    : 0",
            "# Collective test concluded: all_reduce_perf",
        ]
    )

    monkeypatch.setattr(
        mk8s_gpu,
        "_gpu_nodes",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 1,
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            }
        ],
    )
    monkeypatch.setattr(mk8s_gpu, "_pod_request_totals_by_node", lambda **_kwargs: {})
    monkeypatch.setattr(mk8s_gpu, "_training_operator_present", lambda **_kwargs: True)
    monkeypatch.setattr(mk8s_gpu, "_delete_resource", lambda _args, **_kwargs: None)
    monkeypatch.setattr(
        mk8s_gpu,
        "_nccl_live_runtime_overrides",
        lambda **_kwargs: (
            {},
            {
                "worker_request_cpu": "100m",
                "worker_request_memory": "1Gi",
                "launcher_non_gpu_node_names": [],
            },
        ),
    )
    monkeypatch.setattr(mk8s_gpu, "_nccl_chart_documents", lambda **_kwargs: [{}])
    monkeypatch.setattr(mk8s_gpu, "_apply_docs", lambda _docs, **_kwargs: None)
    monkeypatch.setattr(
        mk8s_gpu,
        "_wait_for_launcher_completion",
        lambda **_kwargs: "Succeeded",
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_run_kubectl",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["kubectl"], 0, stdout=logs, stderr=""
        ),
    )

    report_path = mk8s_gpu._run_nccl_validation(
        spec=spec,
        reports_dir=tmp_path,
        extra_env=None,
        emit=None,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["launcher_phase"] == "Succeeded"
    assert report["worker_node_count"] == 1
    assert report["worker_gpus"] == 1
    assert report["avg_bus_bandwidth_gbps"] == 0.0
    assert report["bandwidth_observed"] is False
    assert report["single_rank_smoke"] is True
    assert "launcher_log_excerpt" not in report


def test_nccl_wait_uses_mpijob_failure_when_launcher_pod_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _kubectl_json(args, **_kwargs):  # type: ignore[no-untyped-def]
        if args[:2] == ["get", "pod"]:
            raise RuntimeError("pod not found")
        if args[:2] == ["get", "mpijob"]:
            return {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
        raise AssertionError(args)

    monkeypatch.setattr(mk8s_gpu, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(mk8s_gpu.time, "sleep", lambda _seconds: pytest.fail())

    phase = mk8s_gpu._wait_for_launcher_completion(
        namespace="nccl-test",
        job_name="nccl-test-nebius",
        launcher_pod_name="nccl-test-nebius-launcher",
        extra_env=None,
        timeout_seconds=60,
        emit=None,
    )

    assert phase == "Failed"


def test_nccl_validation_skips_when_gpu_workload_preempts_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = {
        "namespace": "nccl-test",
        "gpu_platform": "gpu-h100-sxm",
        "transport_mode": "socket",
        "threshold_enforced": False,
        "average_bus_bandwidth_threshold_gbps": 300.0,
        "timeout": "1m",
        "max_nodes": 1,
        "report_file": "nccl-test-report.json",
    }
    emits: list[str] = []
    request_snapshots = [
        {},
        {"gpu-node-a": {"gpu_count": 1}},
    ]

    monkeypatch.setattr(
        mk8s_gpu,
        "_gpu_nodes",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 1,
                "allocatable_resources": {"nvidia.com/gpu": "1"},
            }
        ],
    )

    def _pod_requests(**_kwargs):  # type: ignore[no-untyped-def]
        if request_snapshots:
            return request_snapshots.pop(0)
        return {"gpu-node-a": {"gpu_count": 1}}

    monkeypatch.setattr(mk8s_gpu, "_pod_request_totals_by_node", _pod_requests)
    monkeypatch.setattr(mk8s_gpu, "_training_operator_present", lambda **_kwargs: True)
    monkeypatch.setattr(mk8s_gpu, "_delete_resource", lambda _args, **_kwargs: None)
    monkeypatch.setattr(
        mk8s_gpu,
        "_nccl_live_runtime_overrides",
        lambda **_kwargs: ({}, {}),
    )
    monkeypatch.setattr(mk8s_gpu, "_nccl_chart_documents", lambda **_kwargs: [{}])
    monkeypatch.setattr(mk8s_gpu, "_apply_docs", lambda _docs, **_kwargs: None)
    monkeypatch.setattr(
        mk8s_gpu,
        "_wait_for_launcher_completion",
        lambda **_kwargs: "Failed",
    )
    monkeypatch.setattr(
        mk8s_gpu,
        "_run_kubectl",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["kubectl"], 0, stdout="", stderr=""),
    )

    report_path = mk8s_gpu._run_nccl_validation(
        spec=spec,
        reports_dir=tmp_path,
        extra_env=None,
        emit=emits.append,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["skipped"] is True
    assert report["launcher_phase"] == "Failed"
    assert report["saturated_node_count"] == 1
    assert "before the NCCL test could complete" in report["skip_reason"]
    assert any("Skipping NCCL test" in item for item in emits)


def test_run_mk8s_gpu_validations_writes_error_report_on_nccl_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fail_nccl(**_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("kubectl get pod nccl-test-nebius-launcher timed out after 30 seconds")

    monkeypatch.setattr(mk8s_gpu, "_run_nccl_validation", _fail_nccl)
    validations = [
        {
            "kind": "mk8s_nccl",
            "name": "NCCL test",
            "report_file": "nccl-test-report.json",
            "transport_mode": "socket",
            "threshold_enforced": False,
            "chart_values": {
                "benchmark": {
                    "mpiExtraArgs": ["-x", "NCCL_DMABUF_ENABLE=1"],
                },
            },
        }
    ]

    with pytest.raises(RuntimeError, match="nccl-test-nebius-launcher timed out"):
        mk8s_gpu.run_mk8s_gpu_validations(
            validations,
            reports_dir=tmp_path,
            extra_env=None,
        )

    report = json.loads((tmp_path / "nccl-test-report.json").read_text(encoding="utf-8"))
    assert report["validation"] == "NCCL test"
    assert report["passed"] is False
    assert report["launcher_phase"] == "error"
    assert report["nccl_dmabuf_enable"] == "1"
    assert report["nccl_dmabuf_enable_source"] == "explicit MPI environment"
    assert "nccl-test-nebius-launcher timed out" in report["error"]


def test_report_log_excerpt_keeps_tail_only() -> None:
    assert _report_log_excerpt("abcdef", limit=4) == "cdef"
    assert _report_log_excerpt("abc", limit=4) == "abc"


def test_gpu_visibility_node_report_omits_success_logs_and_trims_failure_logs() -> None:
    assert _gpu_visibility_node_report(
        node_name="node-a",
        pod_name="gpu-visibility-node-a",
        gpu_count=8,
        phase="Succeeded",
        passed=True,
        logs="Test PASSED",
    ) == {
        "node_name": "node-a",
        "pod_name": "gpu-visibility-node-a",
        "gpu_count": 8,
        "phase": "Succeeded",
        "passed": True,
    }

    failure = _gpu_visibility_node_report(
        node_name="node-b",
        pod_name="gpu-visibility-node-b",
        gpu_count=8,
        phase="Failed",
        passed=False,
        logs="x" * 5000,
    )
    assert failure == {
        "node_name": "node-b",
        "pod_name": "gpu-visibility-node-b",
        "gpu_count": 8,
        "phase": "Failed",
        "passed": False,
        "log_excerpt": "x" * 4000,
    }


def test_gpu_visibility_node_report_includes_allocatable_resources_when_provided() -> None:
    assert _gpu_visibility_node_report(
        node_name="node-a",
        pod_name="gpu-visibility-node-a",
        gpu_count=8,
        allocatable_resources={"nvidia.com/gpu": "8", "rdma/shared_device": "16"},
        phase="Succeeded",
        passed=True,
        logs="Test PASSED",
    ) == {
        "node_name": "node-a",
        "pod_name": "gpu-visibility-node-a",
        "gpu_count": 8,
        "allocatable_resources": {
            "nvidia.com/gpu": "8",
            "rdma/shared_device": "16",
        },
        "phase": "Succeeded",
        "passed": True,
    }


def test_validation_run_token_is_never_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nebius_cxcli.mk8s_gpu.time.time_ns", lambda: 12345678)

    token = mk8s_gpu._validation_run_token()

    assert token == "run-00bc614e"
    assert not token.isdigit()


def test_device_plugin_snapshot_filters_interesting_allocatable_resources() -> None:
    resources = _interesting_allocatable_resources(
        {
            "cpu": "16",
            "memory": "64Gi",
            "nvidia.com/gpu": "8",
            "example.nvidia.com/gpu": "1",
            "rdma/shared_device": "16",
            "example.com/ignored": "1",
        }
    )

    assert resources == {
        "nvidia.com/gpu": "8",
        "rdma/shared_device": "16",
    }
    assert _rdma_resource_keys(resources) == ("rdma/shared_device",)

    snapshot = _gpu_device_plugin_snapshot(
        [
            {"name": "node-a", "gpu_count": 8, "allocatable_resources": resources},
            {
                "name": "node-b",
                "gpu_count": 4,
                "allocatable_resources": {"nvidia.com/gpu": "4"},
            },
        ]
    )

    assert snapshot == {
        "ready_gpu_node_count": 2,
        "rdma_resource_keys": ["rdma/shared_device"],
        "rdma_resource_node_count": 1,
        "nodes": [
            {
                "node_name": "node-a",
                "gpu_count": 8,
                "allocatable_resources": {
                    "nvidia.com/gpu": "8",
                    "rdma/shared_device": "16",
                },
            },
            {
                "node_name": "node-b",
                "gpu_count": 4,
                "allocatable_resources": {"nvidia.com/gpu": "4"},
            },
        ],
    }


def test_operator_readiness_uses_allocatable_gpu_nodes_for_nebius_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._resource_state_snapshot",
        lambda **_kwargs: (
            True,
            {
                "metadata": {"name": "cluster-policy"},
                "status": {
                    "state": "ready",
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "True",
                            "reason": "NoGPUNodes",
                            "message": "No GPU node found, watching for new nodes to join the cluster.",
                        }
                    ],
                },
            },
            "cluster-policy:ready",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._gpu_nodes",
        lambda **_kwargs: [{"name": "gpu-node-a", "gpu_count": 8}],
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._daemonset_summary",
        lambda **_kwargs: [],
    )

    emitted: list[str] = []
    report_path = _run_operator_readiness_validation(
        spec={
            "timeout": "30s",
            "gpu_operator_namespace": "nvidia-gpu-operator",
            "network_operator_required": False,
            "report_file": "gpu-stack-readiness-report.json",
        },
        reports_dir=tmp_path,
        extra_env=None,
        emit=emitted.append,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["gpu_operator"]["ready_condition"]["reason"] == "NoGPUNodes"
    assert report["gpu_operator"]["gpu_nodes"] == [{"name": "gpu-node-a", "gpu_count": 8}]
    assert any("allocatable GPUs on 1 Ready node(s)" in line for line in emitted)


def test_operator_readiness_collects_daemonset_summaries_only_after_readiness_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._resource_state_snapshot",
        lambda **kwargs: (
            True,
            {
                "metadata": {
                    "name": "cluster-policy"
                    if kwargs.get("resource_type") == "clusterpolicy"
                    else "nic-cluster-policy"
                },
                "status": {"state": "ready"},
            },
            f"{kwargs.get('resource_type')}:ready",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._gpu_nodes",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 8,
                "allocatable_resources": {
                    "nvidia.com/gpu": "8",
                    "rdma/shared_device": "16",
                },
            }
        ],
    )
    daemonset_calls: list[str] = []

    def _fake_daemonset_summary(
        *, namespace: str, extra_env: dict[str, str] | None
    ) -> list[dict[str, Any]]:
        daemonset_calls.append(namespace)
        return []

    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._daemonset_summary",
        _fake_daemonset_summary,
    )

    report_path = _run_operator_readiness_validation(
        spec={
            "timeout": "30s",
            "gpu_operator_namespace": "nvidia-gpu-operator",
            "network_operator_namespace": "nvidia-network-operator",
            "network_operator_required": True,
            "gpu_cluster_enabled": True,
            "report_file": "gpu-stack-readiness-report.json",
        },
        reports_dir=tmp_path,
        extra_env=None,
        emit=None,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert daemonset_calls == [
        "nvidia-gpu-operator",
        "nvidia-network-operator",
    ]
    assert report["network_operator"]["rdma_ready"] is True
    assert report["network_operator"]["device_plugin_snapshot"] == {
        "ready_gpu_node_count": 1,
        "rdma_resource_keys": ["rdma/shared_device"],
        "rdma_resource_node_count": 1,
        "nodes": [
            {
                "node_name": "gpu-node-a",
                "gpu_count": 8,
                "allocatable_resources": {
                    "nvidia.com/gpu": "8",
                    "rdma/shared_device": "16",
                },
            }
        ],
    }


def test_operator_readiness_requires_rdma_resources_for_gpu_cluster_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monotonic_values = iter([0.0, 0.0, 0.0, 31.0])
    monkeypatch.setattr(mk8s_gpu.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(mk8s_gpu.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._resource_state_snapshot",
        lambda **kwargs: (
            True,
            {
                "metadata": {
                    "name": "cluster-policy"
                    if kwargs.get("resource_type") == "clusterpolicy"
                    else "nic-cluster-policy"
                },
                "status": {
                    "state": "ready",
                    "appliedStates": [
                        {"name": "state-OFED", "state": "ignore"},
                        {"name": "state-RDMA-device-plugin", "state": "ignore"},
                    ],
                },
            },
            f"{kwargs.get('resource_type')}:ready",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._gpu_nodes",
        lambda **_kwargs: [
            {
                "name": "gpu-node-a",
                "gpu_count": 8,
                "allocatable_resources": {"nvidia.com/gpu": "8"},
            }
        ],
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_gpu._daemonset_summary",
        lambda **_kwargs: [],
    )

    with pytest.raises(RuntimeError, match="GPU stack readiness check failed"):
        _run_operator_readiness_validation(
            spec={
                "timeout": "30s",
                "gpu_operator_namespace": "nvidia-gpu-operator",
                "network_operator_namespace": "nvidia-network-operator",
                "network_operator_required": True,
                "gpu_cluster_enabled": True,
                "report_file": "gpu-stack-readiness-report.json",
            },
            reports_dir=tmp_path,
            extra_env=None,
            emit=None,
        )

    report = json.loads((tmp_path / "gpu-stack-readiness-report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["network_operator"]["ready"] is False
    assert report["network_operator"]["rdma_required"] is True
    assert report["network_operator"]["rdma_ready"] is False
    assert report["network_operator"]["applied_states"] == [
        {"name": "state-OFED", "state": "ignore"},
        {"name": "state-RDMA-device-plugin", "state": "ignore"},
    ]
    assert report["network_operator"]["device_plugin_snapshot"] == {
        "ready_gpu_node_count": 1,
        "rdma_resource_keys": [],
        "rdma_resource_node_count": 0,
        "nodes": [
            {
                "node_name": "gpu-node-a",
                "gpu_count": 8,
                "allocatable_resources": {"nvidia.com/gpu": "8"},
            }
        ],
    }


def test_mk8s_gpu_dependency_issues_report_missing_required_apps() -> None:
    issues = mk8s_gpu_dependency_issues(_mk8s_payload(infiniband_fabric="fabric-1"))

    assert issues == [
        "GPU-enabled MK8s deployment requires 'apps:nvidia-gpu-operator' to be enabled",
        "GPU-enabled MK8s deployment requires 'apps:nvidia-network-operator' to be enabled",
    ]


def test_mk8s_gpu_dependency_issues_report_disabled_dcgm_exporter() -> None:
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "mk8s",
            "enabled": True,
            "target_ref": "mk8s",
            "values": {
                "dcgmExporter": {
                    "enabled": False,
                }
            },
        }
    ]

    issues = mk8s_gpu_dependency_issues(payload)

    assert issues == [
        "GPU-enabled MK8s target 'mk8s' deployment requires "
        "'nvidia-gpu-operator.values.dcgmExporter.enabled' to stay true",
    ]


def test_normalize_mk8s_gpu_project_validation_settings_prunes_stale_block_without_mk8s() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "mk8s",
                    "validations": {
                        "mk8s_gpu": {
                            "operator_readiness": {"enabled": True},
                        }
                    },
                }
            ]
        },
    }

    changed = normalize_mk8s_gpu_project_validation_settings(payload)

    assert changed is True
    assert "deploy" not in payload


def test_normalize_mk8s_gpu_project_validation_settings_keeps_soperator_workload_defaults() -> None:
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
        }
    ]

    changed = normalize_mk8s_gpu_project_validation_settings(payload)

    assert changed is True
    mk8s_gpu_validations = payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]
    assert mk8s_gpu_validations["operator_readiness"]["enabled"] is True
    assert mk8s_gpu_validations["gpu_visibility"]["enabled"] is True
    assert mk8s_gpu_validations["gpu_visibility"]["max_nodes"] == 3
    assert mk8s_gpu_validations["nccl"]["enabled"] is True
    assert mk8s_gpu_validations["nccl"]["max_nodes"] == 8


def test_normalize_mk8s_gpu_project_validation_settings_preserves_explicit_soperator_workload_validations() -> (
    None
):
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
        }
    ]
    _set_mk8s_gpu_validation_config(
        payload,
        {
            "gpu_visibility": {"enabled": True},
            "nccl": {"enabled": True},
        },
    )

    changed = normalize_mk8s_gpu_project_validation_settings(payload)

    assert changed is True
    mk8s_gpu_validations = payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]
    assert mk8s_gpu_validations["gpu_visibility"]["enabled"] is True
    assert mk8s_gpu_validations["nccl"]["enabled"] is True


def test_normalize_mk8s_gpu_project_validation_settings_preserves_generated_soperator_workload_defaults() -> (
    None
):
    payload = _mk8s_payload()
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "mk8s",
            "target_ref": "mk8s",
            "enabled": True,
        }
    ]
    _set_mk8s_gpu_validation_config(payload, mk8s_gpu_project_validation_defaults())

    changed = normalize_mk8s_gpu_project_validation_settings(payload)

    assert changed is False
    mk8s_gpu_validations = payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]
    assert mk8s_gpu_validations["operator_readiness"]["enabled"] is True
    assert mk8s_gpu_validations["gpu_visibility"]["enabled"] is True
    assert mk8s_gpu_validations["gpu_visibility"]["max_nodes"] == 3
    assert mk8s_gpu_validations["nccl"]["enabled"] is True
    assert mk8s_gpu_validations["nccl"]["max_nodes"] == 8
