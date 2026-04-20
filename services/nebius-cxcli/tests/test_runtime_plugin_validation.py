from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pytest

from nebius_cxcli.runtime_config import read_path_with_catalog
from nebius_cxcli.runtime_plugin_validation import run_runtime_validation_plugins


def _get_path(payload: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    if dotted_path == "shared.admin_ssh.user_name":
        return payload.get("__shared_admin_ssh_user_name__", default)
    resolved = read_path_with_catalog(payload, dotted_path)
    return default if resolved is None else resolved


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def test_runtime_validation_plugins_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS", raising=False)
    payload = {"__shared_admin_ssh_user_name__": "BAD USER"}

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
        env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
    )


def test_runtime_validation_plugins_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS", "none")
    payload = {"__shared_admin_ssh_user_name__": "BAD USER"}

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
        env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
    )


def test_runtime_validation_plugins_can_be_enabled_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {"__shared_admin_ssh_user_name__": "BAD USER"}

    with pytest.raises(ValueError, match="shared.admin_ssh.user_name"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_invalid_component_ssh_user_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "infra": {
            "ssh_jumphost": {
                "enabled": True,
                "ssh_user_name": "BAD USER",
                "allowed_cidrs": ["203.0.113.10/32"],
            }
        },
    }

    with pytest.raises(ValueError, match="infra.ssh_jumphost.ssh_user_name"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_incomplete_flat_mk8s_gpu_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mk8s": {
                "gpu_enabled": True,
            }
        },
    }

    with pytest.raises(ValueError, match="gpu_node_groups must be > 0 when gpu_enabled=true"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_non_clusterable_mk8s_gpu_preset_with_infiniband(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.ProviderOptionLookup.compute_platform_preset_allows_gpu_clustering",
        lambda self, *, project_id, platform_name, preset_name: False,
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "client_info": {
            "nebius": {
                "project_id": "project-1",
            }
        },
        "infra": {
            "mk8s": {
                "gpu_enabled": True,
                "gpu_node_groups": 1,
                "gpu_nodes_count_per_group": 1,
                "gpu_nodes_platform": "gpu-b200-sxm",
                "gpu_nodes_preset": "1gpu-20vcpu-224gb",
                "infiniband_fabric": "us-central1-b",
            }
        },
    }

    with pytest.raises(ValueError, match="does not support GPU clustering"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
                )


def test_runtime_validation_plugins_reject_invalid_mk8s_gpu_validation_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "deploy": {
            "validations": {
                "mk8s_gpu": {
                    "gpu_visibility": {
                        "enabled": True,
                        "max_nodes": 0,
                    }
                }
            }
        },
        "infra": {
            "mk8s": {
                "gpu_enabled": True,
                "gpu_node_groups": 1,
                "gpu_nodes_count_per_group": 1,
                "gpu_nodes_platform": "gpu-h100-sxm",
                "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
            }
        },
    }

    with pytest.raises(ValueError, match="deploy\\.validations\\.mk8s_gpu\\.gpu_visibility\\.max_nodes must be > 0"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_preemptible_cpu_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "vm": {
                "enabled": True,
                "name": "demo-vm",
                "ssh_user_name": "ubuntu",
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
                "source_image_family": "ubuntu24.04-driverless",
                "preemptible_enabled": True,
                "recovery_policy": "FAIL",
            }
        },
    }

    with pytest.raises(ValueError, match="preemptible_enabled requires a GPU platform"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_require_vm_boot_image_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "vm": {
                "enabled": True,
                "name": "demo-vm",
                "ssh_user_name": "ubuntu",
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
            }
        },
    }

    with pytest.raises(ValueError, match="source_image_family is required"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_non_clusterable_vm_gpu_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.ProviderOptionLookup.compute_platform_preset_allows_gpu_clustering",
        lambda self, *, project_id, platform_name, preset_name: False,
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "client_info": {
            "nebius": {
                "project_id": "project-1",
            }
        },
        "infra": {
            "vm": {
                "enabled": True,
                "name": "demo-vm",
                "ssh_user_name": "ubuntu",
                "platform": "gpu-b200-sxm",
                "preset": "8gpu-160vcpu-1792gb",
                "source_image_family": "ubuntu24.04-cuda13.0",
                "gpu_cluster_enabled": True,
                "gpu_cluster_infiniband_fabric": "us-central1-b",
            }
        },
    }

    with pytest.raises(ValueError, match="does not support GPU clustering"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )
