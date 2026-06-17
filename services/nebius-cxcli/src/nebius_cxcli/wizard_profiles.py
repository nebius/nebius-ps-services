"""Built-in catalog wizard-profile shorthands."""

from __future__ import annotations

import copy
from typing import Any


def _project_network_and_subnet_fields() -> dict[str, dict[str, Any]]:
    return {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
                "auto_select_single": True,
            },
            "required": True,
            "type_hint": "string",
        },
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
                "args": {"network_id_path": "inputs.network_id"},
                "auto_select_single": True,
            },
            "required": True,
            "type_hint": "string",
        },
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


def _compute_public_image_family_field(
    *,
    image_field: str,
    platform_field: str,
) -> dict[str, dict[str, Any]]:
    return {
        image_field: {
            "options": {
                "from": "compute_public_image_families",
                "depends_on": platform_field,
                "auto_select_first": True,
            }
        }
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


def _sfs_type_field() -> dict[str, dict[str, Any]]:
    return {
        "inputs.type": {
            **_static_sources(
                (
                    "NETWORK_SSD",
                    "NETWORK_SSD  (SSD-backed shared filesystem; recommended default)",
                ),
                (
                    "NETWORK_HDD",
                    "NETWORK_HDD  (HDD-backed shared filesystem when available)",
                ),
                (
                    "WEKA",
                    "WEKA  (advanced; requires nonzero compute.filesystem.size.weka quota)",
                ),
                (
                    "VAST",
                    "VAST  (advanced; requires nonzero compute.filesystem.size.vast quota)",
                ),
            ),
            "default": "NETWORK_SSD",
            "write_default_to_config": True,
        }
    }


_SFS_LAYOUT_FILESYSTEM_KEYS = ("jail", "controller-spool", "accounting")


def _sfs_layout_filesystem_fields() -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    field_specs: dict[str, dict[str, Any]] = {
        "name": {"type_hint": "string"},
        "existing_id": {
            "options": {
                "from": "project_filesystems",
                "auto_select_single": False,
                "skip_prompt_if_no_choices": True,
            },
            "required": False,
            "type_hint": "string",
        },
        "size_gib": {"type_hint": "number"},
        "block_size_kib": {"type_hint": "number"},
        "mount_tag": {"type_hint": "string"},
        "forbid_deletion": {"type_hint": "bool"},
    }
    for filesystem_key in _SFS_LAYOUT_FILESYSTEM_KEYS:
        for field_name, spec in field_specs.items():
            fields[f"inputs.filesystems.{filesystem_key}.{field_name}"] = copy.deepcopy(spec)
    return fields


_SOPERATOR_DISABLED_BOOL_WIZARD_FIELDS = (
    "values.soperator-activechecks.enabled",
    "values.soperator-activechecks.waitForChecks.enabled",
    "values.soperator-checks.enabled",
    "values.soperator-dcgm-exporter.enabled",
    "values.soperator-notifier.enabled",
    "values.soperator-backup-config.enabled",
    "values.qosConfiguration.enabled",
    "values.sssd.enabled",
)

_SOPERATOR_NODE_GROUP_MAPPING_ROLES = (
    "system",
    "controller",
    "login",
    "accounting",
    "worker",
)


def _disabled_bool_wizard_field() -> dict[str, Any]:
    return {
        "default": False,
        "write_default_to_config": True,
        "type_hint": "bool",
    }


def _materialized_string_wizard_field(default: str) -> dict[str, Any]:
    return {
        "default": default,
        "write_default_to_config": True,
        "type_hint": "string",
    }


def _soperator_node_group_mapping_fields() -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for role in _SOPERATOR_NODE_GROUP_MAPPING_ROLES:
        provider_spec = {
            "from": "soperator_node_groups",
            "args": {
                "role": role,
            },
        }
        fields[f"values.nodeGroupMapping.{role}"] = {
            "write_default_to_config": True,
            "type_hint": "list(string)",
            "default_from": copy.deepcopy(provider_spec),
            "options": copy.deepcopy(provider_spec),
        }
    return fields


def _mk8s_soperator_autoscaling_fields() -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for role in ("system", "controller", "login", "accounting", "worker"):
        prefix = f"inputs.soperator.{role}_autoscaling"
        fields[f"{prefix}.enabled"] = (
            {
                "default": True,
                "write_default_to_config": True,
                "type_hint": "bool",
            }
            if role == "system"
            else _disabled_bool_wizard_field()
        )
        fields[f"{prefix}.min_node_count"] = {
            "default": 3 if role == "system" else 1,
            "write_default_to_config": True,
            "type_hint": "number",
        }
        fields[f"{prefix}.max_node_count"] = {
            "default": 5 if role == "system" else 1,
            "write_default_to_config": True,
            "type_hint": "number",
        }
    return fields


def _soperator_wizard_profile() -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {
        **_suppressed_prompt_fields("namespace", "release-name", "values"),
        "install_mode": {
            "default": "production-cluster",
            "write_default_to_config": True,
            "type_hint": "string",
            **_static_sources(
                (
                    "production-cluster",
                    "Create complete production Soperator cluster (MK8s + SFS + Soperator)",
                )
            ),
        },
        "profile": {
            "default": "nebius-gpu-v1",
            "write_default_to_config": True,
            "options": {
                "from": "soperator_nodesets_profiles",
            },
        },
        "values.partitionProfile": {
            "default": "shape-default",
            "write_default_to_config": True,
            "options": {
                "from": "soperator_partition_profiles",
                "args": {
                    "default": "shape-default",
                },
            },
        },
        "values.topologyProfile": {
            "default": "disabled",
            "write_default_to_config": True,
            "options": {
                "from": "soperator_topology_profiles",
                "args": {
                    "default": "disabled",
                },
            },
        },
        "values.soperator-notifier.slack.webhookSource": {
            "default": "deploy-time",
            "write_default_to_config": True,
            **_static_sources(
                ("deploy-time", "Provide webhook URL at deploy time"),
                ("mysterybox", "Use existing Nebius MysteryBox Secret ID"),
            ),
        },
        "values.soperator-notifier.slack.mysterybox.secretId": {
            "default": "",
            "type_hint": "string",
        },
        "values.soperator-notifier.slack.existingSecret": _materialized_string_wizard_field(
            "soperator-notifier-slack-webhook"
        ),
        "values.soperator-notifier.slack.existingSecretKey": _materialized_string_wizard_field(
            "url"
        ),
        "values.soperator-backup-config.secret.name": _materialized_string_wizard_field(
            "jail-backup"
        ),
        "values.soperator-backup-config.secret.keys.accessKeyID": (
            _materialized_string_wizard_field("aws-access-key-id")
        ),
        "values.soperator-backup-config.secret.keys.secretAccessKey": (
            _materialized_string_wizard_field("aws-access-secret-key")
        ),
        "values.soperator-backup-config.secret.keys.backupPassword": (
            _materialized_string_wizard_field("backup-password")
        ),
        "values.soperator-backup-config.backup.schedule": _materialized_string_wizard_field(
            "@daily-random"
        ),
        "values.soperator-backup-config.prune.schedule": _materialized_string_wizard_field(
            "@daily-random"
        ),
        **_soperator_node_group_mapping_fields(),
    }
    fields.update(
        {
            field_path: _disabled_bool_wizard_field()
            for field_path in _SOPERATOR_DISABLED_BOOL_WIZARD_FIELDS
        }
    )
    return fields


BUILTIN_WIZARD_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "vpc": {
        "inputs.parent_id": {
            "prompt": False,
            "type_hint": "string",
        },
        "inputs.network": {
            "prompt": False,
            "type_hint": "object({ existing_id = optional(string), name = optional(string), labels = optional(map(string)), ipv4_private_cidrs = optional(list(string)), ipv4_private_pool_ids = optional(list(string)), ipv4_private_source_pool_id = optional(string), ipv4_public_pool_ids = optional(list(string)) })",
        },
        "inputs.network.existing_id": {
            "options": {
                "from": "project_networks",
                "auto_select_single": False,
                "skip_prompt_if_no_choices": True,
            },
            "required": False,
            "type_hint": "string",
        },
        "inputs.network.name": {
            "default": "vpc-network",
            "write_default_to_config": True,
            "type_hint": "string",
        },
        "inputs.network.ipv4_private_cidrs": {
            "prompt": False,
            "type_hint": "list(string)",
        },
        "inputs.network.ipv4_private_pool_ids": {
            "prompt": False,
            "type_hint": "list(string)",
            "options": {
                "from": "project_private_pools",
                "auto_select_single": False,
                "skip_prompt_if_no_choices": True,
            },
        },
        "inputs.network.ipv4_private_source_pool_id": {
            "prompt": False,
            "type_hint": "string",
            "options": {
                "from": "project_private_pools",
                "auto_select_single": False,
                "skip_prompt_if_no_choices": True,
            },
        },
        "inputs.subnets": {
            "type_hint": "map(object({ name = optional(string), route_table_id = optional(string), use_network_private_pools = optional(bool, false), ipv4_private_cidrs = list(string), use_network_public_pools = optional(bool), ipv4_public_cidrs = optional(list(string)) }))",
            "prompt": False,
        },
    },
    "mk8s": {
        "inputs.cluster": {
            "prompt": False,
            "type_hint": "object({ parent_id = string, cluster_name = string, network_id = string, subnet_id = string, k8s_version = string, public_endpoint = bool })",
        },
        "inputs.cluster.parent_id": {
            "prompt": False,
            "type_hint": "string",
        },
        "inputs.cluster.cluster_name": {
            "required": True,
            "type_hint": "string",
        },
        "inputs.cluster.network_id": {
            "options": {
                "from": "project_networks",
                "auto_select_single": True,
            },
            "required": True,
            "type_hint": "string",
        },
        "inputs.cluster.subnet_id": {
            "options": {
                "from": "project_subnets",
                "args": {"network_id_path": "inputs.cluster.network_id"},
                "auto_select_single": True,
            },
            "required": True,
            "type_hint": "string",
        },
        "inputs.cluster.k8s_version": {
            "options": {
                "from": "mk8s_control_plane_versions",
                "auto_select_first": True,
            },
            "required": True,
            "type_hint": "string",
        },
        "inputs.cluster.public_endpoint": {
            "default": True,
            "write_default_to_config": True,
            "type_hint": "bool",
        },
        "inputs.node_group_defaults.cpu.platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "cpu-",
            }
        },
        "inputs.node_group_defaults.cpu.preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.node_group_defaults.cpu.platform",
            }
        },
        "inputs.node_group_defaults.cpu.os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "depends_on": "inputs.node_group_defaults.cpu.platform",
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.node_group_defaults.cpu.boot_disk.type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
        },
        "inputs.node_group_defaults.cpu.boot_disk.size_gibibytes": {
            "prompt": False,
        },
        "inputs.node_group_defaults.gpu.platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "gpu-",
            }
        },
        "inputs.node_group_defaults.gpu.preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.node_group_defaults.gpu.platform",
                "args": {
                    "gpu_cluster_required_path": "inputs.node_group_defaults.gpu.infiniband_fabric"
                },
                "auto_select_single": True,
            }
        },
        "inputs.node_group_defaults.gpu.gpu_stack_source": {
            **_static_sources(
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
            "default": "nebius_image",
            "write_default_to_config": True,
        },
        "inputs.node_group_defaults.gpu.infiniband_fabric": {
            "options": {
                "from": "mk8s_infiniband_fabrics",
                "depends_on": "inputs.node_group_defaults.gpu.platform",
                "args": {"preset_path": "inputs.node_group_defaults.gpu.preset"},
                "auto_select_first": True,
                "skip_prompt_if_no_choices": True,
            }
        },
        "inputs.node_group_defaults.gpu.gpu_stack_preset": {
            "options": {
                "from": "mk8s_gpu_stack_presets",
                "depends_on": "inputs.node_group_defaults.gpu.platform",
            },
            "prompt": False,
        },
        "inputs.node_group_defaults.gpu.os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "depends_on": "inputs.node_group_defaults.gpu.platform",
                "args": {"stack_preset_path": "inputs.node_group_defaults.gpu.gpu_stack_preset"},
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.node_group_defaults.gpu.boot_disk.type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
        },
        "inputs.node_group_defaults.gpu.boot_disk.size_gibibytes": {
            "prompt": False,
        },
        "inputs.node_groups": {
            "prompt": False,
            "type_hint": "map(object({ platform = string, preset = string, node_count = optional(number), autoscaling = optional(object({ enabled = optional(bool), min_node_count = optional(number), max_node_count = optional(number) })), gpu = optional(bool), os = optional(string), node_labels = optional(map(string)), taints = optional(list(any)), boot_disk = optional(any), reservation = optional(any), service_account = optional(any), ssh = optional(any), sfs_filesystem_keys = optional(list(string)), filesystems = optional(list(any)) }))",
            "required": True,
        },
        "inputs.node_groups.system.node_count": {
            "default": 2,
            "write_default_to_config": True,
            "required": True,
            "type_hint": "number",
        },
        "inputs.node_groups.system.gpu": {
            "default": False,
            "write_default_to_config": True,
            "prompt": False,
        },
        "inputs.node_groups.system.platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "cpu-",
            },
            "required": True,
        },
        "inputs.node_groups.system.preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.node_groups.system.platform",
            },
            "required": True,
        },
        "inputs.node_groups.system.os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "depends_on": "inputs.node_groups.system.platform",
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.node_groups.system.boot_disk.type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
        },
        "inputs.node_groups.system.boot_disk.size_gibibytes": {
            "prompt": False,
        },
        "inputs.gpu_clusters": {
            "prompt": False,
            "type_hint": "map(object({ infiniband_fabric = string, name = optional(string), labels = optional(map(string)) }))",
            "required": False,
        },
        "inputs.soperator.system_node_count": {
            "default": 3,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.controller_node_count": {
            "default": 2,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.login_node_count": {
            "default": 2,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.accounting_node_count": {
            "default": 2,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        **_mk8s_soperator_autoscaling_fields(),
        "inputs.soperator.worker_total_nodes": {
            "default": 1,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.worker_nodes_per_group": {
            "default": 100,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "deploy.targets[].secrets.mysterybox.enabled": {
            "default": True,
            "write_default_to_config": True,
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
            "write_default_to_config": True,
        },
        "deploy.targets[].secrets.mysterybox.refresh_interval": {
            "default": "15m",
            "write_default_to_config": True,
        },
        "deploy.targets[].secrets.mysterybox.sync_namespaces": {
            "default": ["default"],
            "type_hint": "list(string)",
            "prompt_complex": True,
            "write_default_to_config": True,
            "required": True,
        },
        **_suppressed_prompt_fields(
            "inputs.cluster.control_plane",
        ),
    },
    "managed-postgresql": {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
                "auto_select_single": True,
            },
            "required": True,
            "type_hint": "string",
        },
        "inputs.tier": _static_sources("small", "medium", "large"),
    },
    "sfs": {
        "inputs.parent_id": {
            "prompt": False,
            "type_hint": "string",
        },
        **_sfs_type_field(),
        "inputs.name": {
            "default": "sfs",
            "write_default_to_config": True,
            "type_hint": "string",
        },
        "inputs.size_gib": {
            "default": 1024,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.block_size_kib": {
            "default": 4,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.mount_tag": {
            "type_hint": "string",
        },
        "inputs.forbid_deletion": {
            "default": False,
            "write_default_to_config": True,
            "type_hint": "bool",
        },
        **_sfs_layout_filesystem_fields(),
        **_suppressed_prompt_fields(
            "inputs.filesystems",
        ),
    },
    "wireguard-gw": {
        **_project_network_and_subnet_fields(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
        **_compute_public_image_family_field(
            image_field="inputs.source_image_family",
            platform_field="inputs.platform",
        ),
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.wireguard_tunnel_cidr": {
            "write_default_to_config": True,
        },
        **_suppressed_prompt_fields(
            "inputs.boot_disk_block_size_bytes",
            "inputs.endpoint_host",
            "inputs.clients",
            "inputs.labels",
        ),
    },
    "ssh-jumphost": {
        **_project_network_and_subnet_fields(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
        **_compute_public_image_family_field(
            image_field="inputs.source_image_family",
            platform_field="inputs.platform",
        ),
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.allowed_cidrs": {
            "default_from": {
                "from": "operator_public_ip_cidr",
            },
            "type_hint": "list(string)",
            "write_default_to_config": True,
        },
        **_suppressed_prompt_fields(
            "inputs.boot_disk_block_size_bytes",
            "inputs.labels",
        ),
    },
    "nfs": {
        **_project_network_and_subnet_fields(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
        **_compute_public_image_family_field(
            image_field="inputs.source_image_family",
            platform_field="inputs.platform",
        ),
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.public_ip_mode": _static_sources("none", "dynamic", "static", "allocation"),
        "inputs.export_path": {
            "write_default_to_config": True,
        },
        "inputs.client_cidrs": {
            "type_hint": "list(string)",
            "write_default_to_config": True,
        },
        "inputs.data_disk_enabled": {
            "write_default_to_config": True,
        },
        "inputs.data_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
            "write_default_to_config": True,
        },
        "inputs.data_disk_size_gib": {
            "write_default_to_config": True,
        },
        **_suppressed_prompt_fields(
            "inputs.boot_disk_existing_id",
            "inputs.boot_disk_block_size_bytes",
            "inputs.boot_disk_device_id",
            "inputs.source_image_id",
            "inputs.public_ip_allocation_id",
            "inputs.private_ip_allocation_id",
            "inputs.security_group_ids",
            "inputs.labels",
            "inputs.data_disk_name",
            "inputs.data_disk_block_size_bytes",
            "inputs.data_disk_device_id",
            "inputs.data_disk_filesystem_type",
            "inputs.mount_options",
            "inputs.export_options",
            "inputs.filesystems",
        ),
    },
    "vm": {
        **_project_network_and_subnet_fields(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
        **_compute_public_image_family_field(
            image_field="inputs.source_image_family",
            platform_field="inputs.platform",
        ),
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.data_disk_enabled": {
            "write_default_to_config": True,
        },
        "inputs.data_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
            "write_default_to_config": True,
        },
        "inputs.data_disk_size_gib": {
            "write_default_to_config": True,
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
        "inputs.sfs_attachments": {
            "type_hint": "list(object({ source_instance = optional(string), keys = optional(list(string)), id = optional(string), mount_tag = optional(string), attach_mode = optional(string) }))",
            "prompt_complex": True,
            "required": False,
        },
        **_suppressed_prompt_fields(
            "inputs.boot_disk_existing_id",
            "inputs.boot_disk_block_size_bytes",
            "inputs.source_image_id",
            "inputs.boot_disk_device_id",
            "inputs.public_ip_allocation_id",
            "inputs.private_ip_allocation_id",
            "inputs.security_group_ids",
            "inputs.hostname",
            "inputs.cloud_init_user_data_override",
            "inputs.stopped",
            "inputs.labels",
            "inputs.data_disk_name",
            "inputs.data_disk_block_size_bytes",
            "inputs.data_disk_attach_mode",
            "inputs.data_disk_device_id",
            "inputs.data_disk_labels",
            "inputs.data_disks",
            "inputs.existing_data_disks",
            "inputs.filesystems",
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
    "soperator": _soperator_wizard_profile(),
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
