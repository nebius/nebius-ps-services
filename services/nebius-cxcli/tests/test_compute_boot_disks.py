from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.component_sources import (
    reset_component_sources_cache,
    set_component_sources_file_override,
)
from nebius_cxcli.compute_boot_disks import (
    ComputeBootDiskRecommendationError,
    materialize_compute_boot_disk_defaults,
    refresh_compute_boot_disk_defaults,
)


def _write_sources_file(path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    path.write_text(
        yaml.safe_dump(
            {
                "components": {
                    "infra": {
                        "mk8s": {
                            "source": {
                                "portable": "../../platform-infra/modules/mk8s",
                                "local": str(repo_root / "platform-infra" / "modules" / "mk8s"),
                            },
                        },
                        "vm": {
                            "source": {
                                "portable": "../../platform-infra/modules/vm",
                                "local": str(repo_root / "platform-infra" / "modules" / "vm"),
                            },
                        },
                        "nfs": {
                            "source": {
                                "portable": "../../platform-infra/modules/nfs",
                                "local": str(repo_root / "platform-infra" / "modules" / "nfs"),
                            },
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    path.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(
            {
                "compute": {
                    "boot_disk_defaults": {
                        "disk_types": [
                            {
                                "value": "NETWORK_SSD",
                                "allocation_unit_gib": 1,
                                "label": "NETWORK_SSD",
                            },
                            {
                                "value": "NETWORK_SSD_NON_REPLICATED",
                                "allocation_unit_gib": 93,
                                "label": "NETWORK_SSD_NON_REPLICATED",
                            },
                            {
                                "value": "NETWORK_SSD_IO_M3",
                                "allocation_unit_gib": 93,
                                "label": "NETWORK_SSD_IO_M3",
                            },
                        ],
                        "cpu": {
                            "default_type": "NETWORK_SSD",
                            "rules": [
                                {
                                    "max_vcpu": 8,
                                    "max_memory_gib": 32,
                                    "size_gib": 64,
                                },
                                {
                                    "max_vcpu": 32,
                                    "max_memory_gib": 128,
                                    "size_gib": 93,
                                },
                                {
                                    "max_vcpu": 64,
                                    "max_memory_gib": 256,
                                    "size_gib": 128,
                                },
                                {
                                    "min_vcpu": 65,
                                    "size_gib": 186,
                                },
                            ],
                        },
                        "gpu": {
                            "default_type": "NETWORK_SSD",
                            "rules": [
                                {
                                    "max_gpu": 1,
                                    "max_vcpu": 32,
                                    "max_memory_gib": 384,
                                    "size_gib": 256,
                                },
                                {
                                    "min_gpu": 2,
                                    "max_gpu": 4,
                                    "max_vcpu": 96,
                                    "max_memory_gib": 768,
                                    "size_gib": 512,
                                },
                                {
                                    "min_gpu": 8,
                                    "max_gpu": 8,
                                    "size_gib": 1023,
                                },
                            ],
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def setup_function() -> None:
    set_component_sources_file_override(None)
    reset_component_sources_cache()


def teardown_function() -> None:
    set_component_sources_file_override(None)
    reset_component_sources_cache()


def test_materialize_compute_boot_disk_defaults_from_preset_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cpu_nodes_count": 2,
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "4vcpu-16gb",
                        "gpu_enabled": True,
                        "gpu_node_groups": 1,
                        "gpu_nodes_count_per_group": 1,
                        "gpu_nodes_platform": "gpu-l40s-a",
                        "gpu_nodes_preset": "1gpu-8vcpu-32gb",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["cpu_nodes_boot_disk_size_gib"] == 64
    assert inputs["cpu_nodes_boot_disk_type"] == "NETWORK_SSD"
    assert inputs["gpu_nodes_boot_disk_size_gib"] == 256
    assert inputs["gpu_nodes_boot_disk_type"] == "NETWORK_SSD"


def test_materialize_compute_boot_disk_defaults_from_provider_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cpu_nodes_count": 2,
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "custom-cpu-shape",
                        "gpu_enabled": True,
                        "gpu_node_groups": 1,
                        "gpu_nodes_count_per_group": 1,
                        "gpu_nodes_platform": "gpu-b300-sxm",
                        "gpu_nodes_preset": "custom-gpu-shape",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    class _Lookup:
        def compute_platform_preset_resources(self, *, project_id, platform_name, preset_name):
            assert project_id == "project-1"
            if platform_name == "cpu-d3" and preset_name == "custom-cpu-shape":
                return (32, 128, 0)
            if platform_name == "gpu-b300-sxm" and preset_name == "custom-gpu-shape":
                return (192, 2768, 8)
            return None

    changed = materialize_compute_boot_disk_defaults(payload, provider_lookup=_Lookup())

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["cpu_nodes_boot_disk_size_gib"] == 93
    assert inputs["gpu_nodes_boot_disk_size_gib"] == 1023


def test_materialize_compute_boot_disk_defaults_for_vm_uses_shared_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {
                        "platform": "gpu-l40s-a",
                        "preset": "1gpu-16vcpu-200gb",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["boot_disk_size_gib"] == 256
    assert inputs["boot_disk_type"] == "NETWORK_SSD"


def test_materialize_compute_boot_disk_defaults_for_nfs_uses_vm_style_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "nfs",
                    "instance_id": "nfs",
                    "enabled": True,
                    "inputs": {
                        "platform": "cpu-d3",
                        "preset": "32vcpu-128gb",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["boot_disk_size_gib"] == 93
    assert inputs["boot_disk_type"] == "NETWORK_SSD"


def test_materialize_compute_boot_disk_defaults_skips_vm_existing_boot_disk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {
                        "platform": "cpu-d3",
                        "preset": "4vcpu-16gb",
                        "boot_disk_existing_id": "compute-disk-1",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is False
    inputs = payload["infra"]["components"][0]["inputs"]
    assert "boot_disk_size_gib" not in inputs
    assert "boot_disk_type" not in inputs


def test_materialize_compute_boot_disk_defaults_fails_for_unmatched_shape(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cpu_nodes_count": 1,
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "custom-cpu-shape",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    class _Lookup:
        def compute_platform_preset_resources(self, *, project_id, platform_name, preset_name):
            assert project_id == "project-1"
            if platform_name == "cpu-d3" and preset_name == "custom-cpu-shape":
                return (48, 288, 0)
            return None

    with pytest.raises(ComputeBootDiskRecommendationError, match="No compute.boot_disk_defaults"):
        materialize_compute_boot_disk_defaults(payload, provider_lookup=_Lookup())


def test_materialize_compute_boot_disk_defaults_respects_existing_inputs_and_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cpu_nodes_count": 2,
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "32vcpu-128gb",
                        "gpu_enabled": True,
                        "gpu_node_groups": 1,
                        "gpu_nodes_count_per_group": 1,
                        "gpu_nodes_platform": "gpu-l40s-a",
                        "gpu_nodes_preset": "1gpu-8vcpu-32gb",
                        "gpu_nodes_boot_disk_size_gib": 512,
                        "mk8s_cpu_node_group_overrides": {
                            "template": {
                                "boot_disk": {
                                    "size_gibibytes": 200,
                                    "type": "NETWORK_HDD",
                                }
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert "cpu_nodes_boot_disk_size_gib" not in inputs
    assert "cpu_nodes_boot_disk_type" not in inputs
    assert inputs["gpu_nodes_boot_disk_size_gib"] == 512
    assert inputs["gpu_nodes_boot_disk_type"] == "NETWORK_SSD"


def test_refresh_compute_boot_disk_defaults_updates_auto_derived_size_after_type_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = {
        "cpu_nodes_count": 2,
        "cpu_nodes_platform": "cpu-d3",
        "cpu_nodes_preset": "4vcpu-16gb",
        "cpu_nodes_boot_disk_type": "NETWORK_SSD",
        "cpu_nodes_boot_disk_size_gib": 64,
    }
    inputs = dict(previous_inputs)
    inputs["cpu_nodes_boot_disk_type"] = "NETWORK_SSD_NON_REPLICATED"

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is True
    assert inputs["cpu_nodes_boot_disk_type"] == "NETWORK_SSD_NON_REPLICATED"
    assert inputs["cpu_nodes_boot_disk_size_gib"] == 93


def test_refresh_compute_boot_disk_defaults_updates_vm_after_shape_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = {
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "boot_disk_type": "NETWORK_SSD",
        "boot_disk_size_gib": 64,
    }
    inputs = {
        **previous_inputs,
        "preset": "32vcpu-128gb",
    }

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="vm",
        instance_id="vm",
        project_id="project-1",
    )

    assert changed is True
    assert inputs["boot_disk_type"] == "NETWORK_SSD"
    assert inputs["boot_disk_size_gib"] == 93


def test_refresh_compute_boot_disk_defaults_preserves_custom_size(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = {
        "gpu_enabled": True,
        "gpu_node_groups": 1,
        "gpu_nodes_count_per_group": 1,
        "gpu_nodes_platform": "gpu-b300-sxm",
        "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
        "gpu_nodes_boot_disk_type": "NETWORK_SSD",
        "gpu_nodes_boot_disk_size_gib": 1023,
    }
    inputs = dict(previous_inputs)
    inputs["gpu_nodes_boot_disk_type"] = "NETWORK_SSD_IO_M3"
    inputs["gpu_nodes_boot_disk_size_gib"] = 2048

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is False
    assert inputs["gpu_nodes_boot_disk_type"] == "NETWORK_SSD_IO_M3"
    assert inputs["gpu_nodes_boot_disk_size_gib"] == 2048
