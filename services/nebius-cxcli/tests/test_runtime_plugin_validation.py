from __future__ import annotations

import re
from collections.abc import Mapping
from types import SimpleNamespace
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


def test_runtime_validation_plugins_accept_mysterybox_payload_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mysterybox": {
                "enabled": True,
                "parent_id": "project-1",
                "secrets": [
                    {
                        "name": "app-runtime",
                        "version_id": "n/a",
                        "eso_version_policy": "manual-version-pinning",
                        "kubernetes_secret_name": "app-runtime",
                        "payload": {
                            "API_KEY": {
                                "type": "text",
                            },
                            "CERT": {
                                "type": "file",
                            },
                        },
                    },
                ],
            },
        },
    }

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
        env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
    )


def test_runtime_validation_plugins_reject_mysterybox_secret_mapping_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mysterybox": {
                "enabled": True,
                "parent_id": "project-1",
                "secrets": {
                    "app": {
                        "name": "app-runtime",
                    },
                },
            },
        },
    }

    with pytest.raises(ValueError, match="secrets must be a list of secret objects"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_mysterybox_singular_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mysterybox": {
                "enabled": True,
                "parent_id": "project-1",
                "secrets": [
                    {
                        "name": "app-runtime",
                        "version": {
                            "payload": {
                                "API_KEY": {
                                    "type": "text",
                                },
                            },
                        },
                    },
                ],
            },
        },
    }

    with pytest.raises(ValueError, match="unsupported field\\(s\\): version"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_mysterybox_missing_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mysterybox": {
                "enabled": True,
                "parent_id": "project-1",
                "secrets": [
                    {
                        "name": "app-runtime",
                    },
                ],
            },
        },
    }

    with pytest.raises(ValueError, match=r"secrets\[0\]\.payload must be a non-empty mapping"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_mysterybox_invalid_version_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mysterybox": {
                "enabled": True,
                "parent_id": "project-1",
                "secrets": [
                    {
                        "name": "app-runtime",
                        "version_id": "v2",
                        "payload": {
                            "API_KEY": {
                                "type": "text",
                            },
                        },
                    },
                ],
            },
        },
    }

    with pytest.raises(ValueError, match="version ID starting with mbsecver-"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_reject_mysterybox_invalid_eso_version_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "infra": {
            "mysterybox": {
                "enabled": True,
                "parent_id": "project-1",
                "secrets": [
                    {
                        "name": "app-runtime",
                        "version_id": "n/a",
                        "eso_version_policy": "latest",
                        "payload": {
                            "API_KEY": {
                                "type": "text",
                            },
                        },
                    },
                ],
            },
        },
    }

    with pytest.raises(ValueError, match="eso_version_policy must be one of"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_accept_single_replica_grafana(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "apps": {
            "charts": [
                {
                    "id": "grafana",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "values": {},
                }
            ]
        },
    }

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
        env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
    )


def test_runtime_validation_plugins_reject_grafana_replicas_without_shared_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "apps": {
            "charts": [
                {
                    "id": "grafana",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "values": {
                        "replicas": 2,
                    },
                }
            ]
        },
    }

    with pytest.raises(ValueError, match="requires a shared Grafana database"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_accept_grafana_replicas_with_shared_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "apps": {
            "charts": [
                {
                    "id": "grafana",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "values": {
                        "replicas": 2,
                        "grafana.ini": {
                            "database": {
                                "type": "postgres",
                            },
                        },
                    },
                }
            ]
        },
    }

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

    with pytest.raises(
        ValueError,
        match=(
            "gpu_enabled=true requires either gpu_node_groups > 0 or at least one generic "
            "node_groups entry with gpu=true"
        ),
    ):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )


def test_runtime_validation_plugins_allow_generic_mk8s_gpu_node_groups(
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
                "gpu_node_groups": 0,
                "gpu_nodes_platform": "gpu-h100-sxm",
                "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                "node_groups": {
                    "worker-gpu-0": {
                        "gpu": True,
                        "fixed_node_count": 1,
                    }
                },
            }
        },
    }

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


def test_runtime_validation_plugins_reject_mk8s_infiniband_fabric_not_in_live_capacity_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.ProviderOptionLookup.compute_platform_preset_allows_gpu_clustering",
        lambda self, *, project_id, platform_name, preset_name: True,
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.ProviderOptionLookup.compute_platform_preset_fabrics",
        lambda self, *, tenant_id, project_id, region_id, platform_name, preset_name: (
            SimpleNamespace(fabric="fabric-2"),
            SimpleNamespace(fabric="fabric-3"),
        ),
    )
    payload = {
        "__shared_admin_ssh_user_name__": "ubuntu",
        "client_info": {
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            }
        },
        "infra": {
            "mk8s": {
                "gpu_enabled": True,
                "gpu_node_groups": 1,
                "gpu_nodes_count_per_group": 1,
                "gpu_nodes_platform": "gpu-h100-sxm",
                "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                "infiniband_fabric": "fabric-6",
            }
        },
    }

    with pytest.raises(ValueError, match="live Capacity Dashboard fabrics"):
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
            "targets": [
                {
                    "instance_id": "mk8s",
                    "validations": {
                        "mk8s_gpu": {
                            "gpu_visibility": {
                                "enabled": True,
                                "max_nodes": 0,
                            }
                        }
                    },
                }
            ]
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

    with pytest.raises(
        ValueError,
        match="deploy\\.targets\\[0\\]\\.validations\\.mk8s_gpu\\.gpu_visibility\\.max_nodes must be > 0",
    ):
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


def test_runtime_validation_plugins_reject_vm_gpu_cluster_on_single_gpu_preset_without_live_lookup(
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
                "platform": "gpu-h100-sxm",
                "preset": "1gpu-16vcpu-200gb",
                "source_image_family": "ubuntu24.04-cuda13.0",
                "gpu_cluster_enabled": True,
                "gpu_cluster_infiniband_fabric": "fabric-2",
            }
        },
    }

    with pytest.raises(
        ValueError, match="gpu_cluster_enabled requires a GPU-cluster-compatible preset"
    ):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )
