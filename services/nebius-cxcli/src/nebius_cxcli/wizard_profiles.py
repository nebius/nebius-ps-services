"""Built-in catalog wizard-profile shorthands."""

from __future__ import annotations

import copy
from typing import Any


def _project_subnets_field() -> dict[str, dict[str, Any]]:
    return {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        }
    }


def _compute_platform_and_preset_fields(
    *,
    platform_field: str,
    preset_field: str,
) -> dict[str, dict[str, Any]]:
    return {
        platform_field: {
            "options": {
                "from": "compute_platforms",
            }
        },
        preset_field: {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": platform_field,
            }
        },
    }


def _static_sources(*values: str | tuple[str, str]) -> dict[str, Any]:
    normalized: list[str | dict[str, str]] = []
    for item in values:
        if isinstance(item, tuple):
            value, label = item
            normalized.append({"value": value, "label": label})
        else:
            normalized.append(item)
    return {
        "sources": [
            {
                "source": "static",
                "values": normalized,
            }
        ]
    }


def _suppressed_prompt_fields(*field_paths: str) -> dict[str, dict[str, Any]]:
    return {field_path: {"prompt": False} for field_path in field_paths}


BUILTIN_WIZARD_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "mk8s": {
        **_project_subnets_field(),
        "inputs.k8s_version": {
            "options": {
                "from": "mk8s_control_plane_versions",
                "auto_select_first": True,
            }
        },
        "inputs.cpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "cpu-",
            }
        },
        "inputs.gpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "gpu-",
            }
        },
        "inputs.cpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.cpu_nodes_platform",
            }
        },
        "inputs.gpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.gpu_nodes_platform",
                "args": {"gpu_cluster_required_path": "inputs.infiniband_fabric"},
                "auto_select_single": True,
            }
        },
        "inputs.gpu_stack_source": _static_sources(
            (
                "nebius_image",
                (
                    "nebius_image  (Nebius GPU image includes the host "
                    "NVIDIA driver/toolkit; GPU Operator does not install them)"
                ),
            ),
            (
                "operator_managed",
                (
                    "operator_managed  (base OS image; GPU Operator installs "
                    "and manages the NVIDIA driver/toolkit)"
                ),
            ),
        ),
        "inputs.cpu_nodes_os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "depends_on": "inputs.cpu_nodes_platform",
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.infiniband_fabric": {
            "options": {
                "from": "mk8s_infiniband_fabrics",
                "depends_on": "inputs.gpu_nodes_platform",
                "args": {"preset_path": "inputs.gpu_nodes_preset"},
                "skip_prompt_if_no_choices": True,
            }
        },
        "inputs.gpu_stack_preset": {
            "options": {
                "from": "mk8s_gpu_stack_presets",
                "depends_on": "inputs.gpu_nodes_platform",
            },
            "prompt": False,
        },
        "inputs.gpu_nodes_os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "depends_on": "inputs.gpu_nodes_platform",
                "args": {"stack_preset_path": "inputs.gpu_stack_preset"},
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.cpu_nodes_boot_disk_type": {
            "options": {
                "from": "mk8s_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.gpu_nodes_boot_disk_type": {
            "options": {
                "from": "mk8s_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "deploy.targets[].secrets.mysterybox.enabled": {
            "default": True,
            "materialize_default": True,
        },
        "deploy.targets[].secrets.mysterybox.store_name": {
            "default": "nebius-mysterybox-shared",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.api_domain": {
            "default": "api.nebius.cloud:443",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.credentials_secret.name": {
            "default": "nebius-mysterybox-shared-creds",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.credentials_secret.namespace": {
            "default": "external-secrets",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.credentials_secret.key": {
            "default": "credentials.json",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.allow_all_namespaces": {
            "default": True,
            "materialize_default": True,
        },
        "deploy.targets[].secrets.mysterybox.refresh_interval": {
            "default": "15m",
            "materialize_default": True,
        },
        "deploy.targets[].secrets.mysterybox.sync_namespaces": {
            "default": ["default"],
            "type_hint": "list(string)",
            "prompt_complex": True,
            "materialize_default": True,
            "required": True,
        },
        **_suppressed_prompt_fields(
            "inputs.mk8s_cluster_overrides",
            "inputs.mk8s_cpu_node_group_overrides",
            "inputs.mk8s_gpu_node_group_overrides",
        ),
    },
    "managed-postgresql": {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
            }
        },
        "inputs.tier": _static_sources("small", "medium", "large"),
    },
    "wireguard-jumphost": {
        **_project_subnets_field(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
    },
    "ssh-jumphost": {
        **_project_subnets_field(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
    },
    "vm": {
        **_project_subnets_field(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
        "inputs.source_image_family": {
            "options": {
                "from": "compute_public_image_families",
                "depends_on": "inputs.platform",
                "auto_select_first": True,
            }
        },
        "inputs.public_ip_mode": _static_sources("dynamic", "none", "static", "allocation"),
        "inputs.gpu_cluster_infiniband_fabric": {
            "options": {
                "from": "mk8s_infiniband_fabrics",
                "depends_on": "inputs.platform",
                "args": {"preset_path": "inputs.preset"},
                "skip_prompt_if_no_choices": True,
            }
        },
        **_suppressed_prompt_fields(
            "inputs.boot_disk_existing_id",
            "inputs.source_image_id",
            "inputs.boot_disk_device_id",
            "inputs.public_ip_allocation_id",
            "inputs.private_ip_allocation_id",
            "inputs.security_group_ids",
            "inputs.hostname",
            "inputs.stopped",
            "inputs.labels",
            "inputs.data_disks",
            "inputs.existing_data_disks",
            "inputs.filesystems",
            "inputs.observability_collector_enabled",
            "inputs.observability_collector_region_id",
            "inputs.observability_collector_package_name",
            "inputs.observability_collector_package_version",
            "inputs.observability_collector_apt_repository",
            "inputs.observability_collector_apt_key_url",
            "inputs.observability_collector_apt_suite",
            "inputs.observability_collector_apt_component",
            "inputs.observability_collector_apt_origin",
            "inputs.observability_collector_prometheus_package_name",
            "inputs.observability_collector_iam_token_file",
            "inputs.observability_collector_logs_enabled",
            "inputs.observability_collector_logs_systemd_units",
            "inputs.observability_collector_metrics_enabled",
            "inputs.observability_collector_metrics_export_port",
            "inputs.observability_collector_prometheus_agent_port",
            "inputs.recovery_policy",
            "inputs.gpu_cluster_id",
            "inputs.gpu_cluster_name",
            "inputs.container_entrypoint",
            "inputs.container_args",
            "inputs.container_env",
            "inputs.container_ports",
            "inputs.container_mounts",
        ),
    },
    "object-storage": {
        "inputs.versioning_policy": _static_sources("DISABLED", "ENABLED", "SUSPENDED"),
        "inputs.object_audit_logging": _static_sources("NONE", "MUTATE_ONLY", "ALL"),
    },
    "mysterybox": {
        "inputs.secrets": {
            "type_hint": "list(object({}))",
            "prompt_complex": True,
            "required": True,
        },
        **_suppressed_prompt_fields("inputs.payload_values"),
    },
}


def builtin_wizard_profile_names() -> tuple[str, ...]:
    return tuple(sorted(BUILTIN_WIZARD_PROFILES))


def resolve_builtin_wizard_profile(name: str) -> dict[str, dict[str, Any]]:
    profile = BUILTIN_WIZARD_PROFILES.get(name)
    if profile is None:
        supported = ", ".join(builtin_wizard_profile_names())
        raise ValueError(f"wizard_profile '{name}' is unknown; supported values: {supported}")
    return copy.deepcopy(profile)
