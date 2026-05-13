from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache


def _reset_component_state() -> None:
    reset_component_entry_cache()


def setup_function() -> None:
    _reset_component_state()


def teardown_function() -> None:
    _reset_component_state()


def _portable_chart_source(*, repo: str, chart: str, version: str = "") -> dict[str, object]:
    portable: dict[str, object] = {
        "repo": repo,
        "chart": chart,
    }
    if version:
        portable["version"] = version
    return {"portable": portable}


def test_components_discovered_from_source_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component-sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "components": {
                    "infra": {
                        "mk8s": {
                            "source": {
                                "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                                "local": "platform-infra/modules/mk8s",
                            },
                            "ui": {
                                "title": "Managed Kubernetes",
                                "group": "Compute",
                                "enabled": True,
                            },
                            "wizard_profile": "mk8s",
                        },
                        "wireguard-jumphost": {
                            "source": {
                                "portable": "git::https://github.com/example/infra.git//modules/wireguard-jumphost?ref=v1.2.3",
                                "local": "platform-infra/modules/wireguard-jumphost",
                            },
                            "ui": {
                                "title": "WireGuard module",
                                "group": "Network",
                                "enabled": False,
                            },
                        },
                    },
                    "apps": {
                        "n8n": {
                            "source": _portable_chart_source(
                                repo="https://community-charts.github.io/helm-charts",
                                chart="n8n",
                                version="1.16.29",
                            ),
                            "release": {
                                "namespace": "n8n",
                                "name": "n8n",
                            },
                            "ui": {
                                "title": "n8n workload",
                                "group": "Workloads",
                                "enabled": False,
                            },
                        }
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    reset_component_entry_cache()

    infra = {entry.id: entry for entry in component_entries("infra")}
    assert set(infra) == {"mk8s", "wireguard-jumphost"}
    assert infra["mk8s"].engine_type == "terraform_module"
    assert infra["mk8s"].group == "Compute"
    assert infra["mk8s"].default_enabled is True
    assert infra["mk8s"].wizard_fields == {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        },
        "inputs.k8s_version": {
            "options": {
                "from": "mk8s_control_plane_versions",
                "auto_select_first": True,
            }
        },
        "inputs.cpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "args": {"platform_prefix": "cpu-"},
            }
        },
        "inputs.gpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "args": {"platform_prefix": "gpu-"},
            }
        },
        "inputs.cpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {"platform_path": "inputs.cpu_nodes_platform"},
            }
        },
        "inputs.cpu_nodes_os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "args": {"platform_path": "inputs.cpu_nodes_platform"},
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.gpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {
                    "platform_path": "inputs.gpu_nodes_platform",
                    "gpu_cluster_required_path": "inputs.infiniband_fabric",
                },
                "auto_select_single": True,
            }
        },
        "inputs.gpu_stack_source": {
            "sources": [
                {
                    "source": "static",
                    "values": [
                        {
                            "value": "nebius_image",
                            "label": (
                                "nebius_image  (Nebius GPU image includes the host "
                                "NVIDIA driver/toolkit; GPU Operator does not "
                                "install them)"
                            ),
                        },
                        {
                            "value": "operator_managed",
                            "label": (
                                "operator_managed  (base OS image; GPU Operator "
                                "installs and manages the NVIDIA driver/toolkit)"
                            ),
                        },
                    ],
                }
            ]
        },
        "inputs.infiniband_fabric": {
            "options": {
                "from": "mk8s_infiniband_fabrics",
                "args": {
                    "platform_path": "inputs.gpu_nodes_platform",
                    "preset_path": "inputs.gpu_nodes_preset",
                },
                "skip_prompt_if_no_choices": True,
            }
        },
        "inputs.gpu_stack_preset": {
            "options": {
                "from": "mk8s_gpu_stack_presets",
                "args": {"platform_path": "inputs.gpu_nodes_platform"},
            },
            "prompt": False,
        },
        "inputs.gpu_nodes_os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "args": {
                    "platform_path": "inputs.gpu_nodes_platform",
                    "stack_preset_path": "inputs.gpu_stack_preset",
                },
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.cpu_nodes_boot_disk_type": {
            "options": {
                "from": "mk8s_boot_disk_types",
                "auto_select_first": True,
            },
        },
        "inputs.gpu_nodes_boot_disk_type": {
            "options": {
                "from": "mk8s_boot_disk_types",
                "auto_select_first": True,
            },
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
        "inputs.mk8s_cluster_overrides": {
            "prompt": False,
        },
        "inputs.mk8s_cpu_node_group_overrides": {
            "prompt": False,
        },
        "inputs.mk8s_gpu_node_group_overrides": {
            "prompt": False,
        },
    }

    apps = {entry.id: entry for entry in component_entries("apps")}
    assert "n8n" in apps
    assert apps["n8n"].engine_type == "helm_release"
    assert apps["n8n"].chart_name == "n8n"
    assert apps["n8n"].chart_repo == "https://community-charts.github.io/helm-charts"
    assert apps["n8n"].default_namespace == "n8n"
    assert apps["n8n"].default_release_name == "n8n"


def test_app_chart_name_becomes_component_id(monkeypatch, tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "components": {
                    "infra": {},
                    "apps": {
                        "gateway-helm": {
                            "source": _portable_chart_source(
                                repo="oci://docker.io/envoyproxy",
                                chart="gateway-helm",
                                version="1.4.2",
                            ),
                            "release": {
                                "namespace": "envoy-gateway-system",
                                "name": "envoy-gateway",
                            },
                            "ui": {
                                "group": "Platform",
                                "enabled": False,
                            },
                        }
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    reset_component_entry_cache()

    apps = {entry.id: entry for entry in component_entries("apps")}
    assert "gateway-helm" in apps
    assert apps["gateway-helm"].chart_name == "gateway-helm"
    assert apps["gateway-helm"].default_release_name == "envoy-gateway"
    assert apps["gateway-helm"].source == "oci://docker.io/envoyproxy/gateway-helm"
