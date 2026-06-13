from __future__ import annotations

from pathlib import Path
from typing import Any

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
    resolve_compute_boot_disk_recommendation,
)
from nebius_cxcli.provider_options import ProviderOptionLookup


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


def _mk8s_inputs(
    *,
    cpu_platform: str = "",
    cpu_preset: str = "",
    gpu_platform: str = "",
    gpu_preset: str = "",
) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    if cpu_platform or cpu_preset:
        defaults["cpu"] = {
            "platform": cpu_platform,
            "preset": cpu_preset,
        }
    if gpu_platform or gpu_preset:
        defaults["gpu"] = {
            "platform": gpu_platform,
            "preset": gpu_preset,
        }
    return {"node_group_defaults": defaults}


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
                    "inputs": _mk8s_inputs(
                        cpu_platform="cpu-d3",
                        cpu_preset="4vcpu-16gb",
                        gpu_platform="gpu-l40s-a",
                        gpu_preset="1gpu-8vcpu-32gb",
                    ),
                }
            ]
        },
        "apps": {"charts": []},
    }

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["size_gibibytes"] == 64
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["type"] == "NETWORK_SSD"
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["size_gibibytes"] == 256
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["type"] == "NETWORK_SSD"


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
                    "inputs": _mk8s_inputs(
                        cpu_platform="cpu-d3",
                        cpu_preset="custom-cpu-shape",
                        gpu_platform="gpu-b300-sxm",
                        gpu_preset="custom-gpu-shape",
                    ),
                }
            ]
        },
        "apps": {"charts": []},
    }

    class _Lookup(ProviderOptionLookup):
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
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["size_gibibytes"] == 93
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["size_gibibytes"] == 1023


def test_materialize_compute_boot_disk_defaults_for_mk8s_node_groups_without_helper(
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
                        "node_groups": {
                            "cpu": {
                                "node_count": 2,
                                "gpu": False,
                                "platform": "cpu-d3",
                                "preset": "4vcpu-16gb",
                            },
                            "gpu": {
                                "node_count": 1,
                                "gpu": True,
                                "platform": "gpu-b300-sxm",
                                "preset": "8gpu-192vcpu-2768gb",
                                "gpu_cluster_key": "gpu",
                            },
                        },
                        "gpu_clusters": {
                            "gpu": {"infiniband_fabric": "fabric-1"},
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
    assert "node_group_defaults" not in inputs
    assert inputs["node_groups"]["cpu"]["boot_disk"] == {
        "size_gibibytes": 64,
        "type": "NETWORK_SSD",
    }
    assert inputs["node_groups"]["gpu"]["boot_disk"] == {
        "size_gibibytes": 1023,
        "type": "NETWORK_SSD",
    }


def test_materialize_compute_boot_disk_defaults_ignores_stale_gpu_helper_without_gpu_groups(
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
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-b300-sxm",
                                "preset": "8gpu-192vcpu-2768gb",
                            }
                        },
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

    changed = materialize_compute_boot_disk_defaults(payload)

    assert changed is True
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["node_groups"]["cpu"]["boot_disk"]["size_gibibytes"] == 64
    assert "boot_disk" not in inputs["node_group_defaults"]["gpu"]


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
                    "inputs": _mk8s_inputs(
                        cpu_platform="cpu-d3",
                        cpu_preset="custom-cpu-shape",
                    ),
                }
            ]
        },
        "apps": {"charts": []},
    }

    class _Lookup(ProviderOptionLookup):
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
                        **_mk8s_inputs(
                            cpu_platform="cpu-d3",
                            cpu_preset="32vcpu-128gb",
                            gpu_platform="gpu-l40s-a",
                            gpu_preset="1gpu-8vcpu-32gb",
                        ),
                        "node_group_defaults": {
                            "cpu": {
                                "platform": "cpu-d3",
                                "preset": "32vcpu-128gb",
                                "boot_disk": {
                                    "size_gibibytes": 200,
                                    "type": "NETWORK_HDD",
                                },
                            },
                            "gpu": {
                                "platform": "gpu-l40s-a",
                                "preset": "1gpu-8vcpu-32gb",
                                "boot_disk": {
                                    "size_gibibytes": 512,
                                },
                            },
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
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["size_gibibytes"] == 200
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["type"] == "NETWORK_HDD"
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["size_gibibytes"] == 512
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["type"] == "NETWORK_SSD"


def test_refresh_compute_boot_disk_defaults_updates_auto_derived_size_after_type_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = _mk8s_inputs(cpu_platform="cpu-d3", cpu_preset="4vcpu-16gb")
    previous_inputs["node_group_defaults"]["cpu"]["boot_disk"] = {
        "type": "NETWORK_SSD",
        "size_gibibytes": 64,
    }
    inputs = yaml.safe_load(yaml.safe_dump(previous_inputs))
    inputs["node_group_defaults"]["cpu"]["boot_disk"]["type"] = "NETWORK_SSD_NON_REPLICATED"

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is True
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["type"] == "NETWORK_SSD_NON_REPLICATED"
    assert inputs["node_group_defaults"]["cpu"]["boot_disk"]["size_gibibytes"] == 93


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

    previous_inputs = _mk8s_inputs(
        gpu_platform="gpu-b300-sxm",
        gpu_preset="8gpu-192vcpu-2768gb",
    )
    previous_inputs["node_group_defaults"]["gpu"]["boot_disk"] = {
        "type": "NETWORK_SSD",
        "size_gibibytes": 1023,
    }
    inputs = yaml.safe_load(yaml.safe_dump(previous_inputs))
    inputs["node_group_defaults"]["gpu"]["boot_disk"]["type"] = "NETWORK_SSD_IO_M3"
    inputs["node_group_defaults"]["gpu"]["boot_disk"]["size_gibibytes"] = 2048

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is False
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["type"] == "NETWORK_SSD_IO_M3"
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["size_gibibytes"] == 2048


def test_refresh_compute_boot_disk_defaults_preserves_direct_mk8s_boot_disk_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = _mk8s_inputs(
        gpu_platform="gpu-b300-sxm",
        gpu_preset="8gpu-192vcpu-2768gb",
    )
    inputs = yaml.safe_load(yaml.safe_dump(previous_inputs))
    inputs["node_group_defaults"]["gpu"]["boot_disk"] = {
        "type": "NETWORK_SSD_IO_M3",
        "size_gibibytes": 2048,
    }

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is False
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["type"] == "NETWORK_SSD_IO_M3"
    assert inputs["node_group_defaults"]["gpu"]["boot_disk"]["size_gibibytes"] == 2048


def test_refresh_compute_boot_disk_defaults_updates_mk8s_node_group_shape_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = {
        "node_groups": {
            "system": {
                "node_count": 1,
                "gpu": False,
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "boot_disk": {
                    "type": "NETWORK_SSD",
                    "size_gibibytes": 64,
                },
            }
        }
    }
    inputs = yaml.safe_load(yaml.safe_dump(previous_inputs))
    inputs["node_groups"]["system"]["preset"] = "32vcpu-128gb"

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is True
    assert inputs["node_groups"]["system"]["boot_disk"]["type"] == "NETWORK_SSD"
    assert inputs["node_groups"]["system"]["boot_disk"]["size_gibibytes"] == 93


def test_refresh_compute_boot_disk_defaults_updates_mk8s_node_group_type_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = {
        "node_groups": {
            "system": {
                "node_count": 1,
                "gpu": False,
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "boot_disk": {
                    "type": "NETWORK_SSD",
                    "size_gibibytes": 64,
                },
            }
        }
    }
    inputs = yaml.safe_load(yaml.safe_dump(previous_inputs))
    inputs["node_groups"]["system"]["boot_disk"]["type"] = "NETWORK_SSD_NON_REPLICATED"

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is True
    assert (
        inputs["node_groups"]["system"]["boot_disk"]["type"]
        == "NETWORK_SSD_NON_REPLICATED"
    )
    assert inputs["node_groups"]["system"]["boot_disk"]["size_gibibytes"] == 93


def test_refresh_compute_boot_disk_defaults_preserves_mk8s_node_group_custom_size(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    previous_inputs = {
        "node_groups": {
            "gpu": {
                "node_count": 1,
                "gpu": True,
                "platform": "gpu-b300-sxm",
                "preset": "8gpu-192vcpu-2768gb",
                "gpu_cluster_key": "gpu",
            }
        },
        "gpu_clusters": {"gpu": {"infiniband_fabric": "fabric-1"}},
    }
    inputs = yaml.safe_load(yaml.safe_dump(previous_inputs))
    inputs["node_groups"]["gpu"]["boot_disk"] = {
        "type": "NETWORK_SSD_IO_M3",
        "size_gibibytes": 2048,
    }

    changed = refresh_compute_boot_disk_defaults(
        inputs,
        previous_inputs,
        component_id="mk8s",
        instance_id="mk8s",
        project_id="project-1",
    )

    assert changed is False
    assert inputs["node_groups"]["gpu"]["boot_disk"]["type"] == "NETWORK_SSD_IO_M3"
    assert inputs["node_groups"]["gpu"]["boot_disk"]["size_gibibytes"] == 2048


def test_resolve_compute_boot_disk_recommendation_uses_node_group_gpu_cluster_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_sources_file(sources_file)
    monkeypatch.setattr(component_sources, "_discover_terraform_outputs", lambda _source: ())
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    inputs = {
        "node_groups": {
            "gpu": {
                "node_count": 1,
                "gpu": True,
                "platform": "gpu-b300-sxm",
                "preset": "8gpu-192vcpu-2768gb",
                "gpu_cluster_key": "gpu",
            }
        },
        "gpu_clusters": {"gpu": {"infiniband_fabric": "fabric-1"}},
    }

    resolved = resolve_compute_boot_disk_recommendation(
        component_id="mk8s",
        instance_id="mk8s",
        inputs=inputs,
        project_id="project-1",
        field_scope="gpu",
        provider_lookup=None,
    )

    assert resolved is not None
    assert resolved.context.gpu_cluster_enabled is True
