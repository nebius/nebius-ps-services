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
                            "source": {
                                "repo": "https://community-charts.github.io/helm-charts",
                                "chart": "n8n",
                                "version": "1.16.29",
                            },
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
        "inputs.gpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {"platform_path": "inputs.gpu_nodes_platform"},
            }
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
                            "source": {
                                "repo": "oci://docker.io/envoyproxy",
                                "chart": "gateway-helm",
                                "version": "1.4.2",
                            },
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
