from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.wizard_profiles import BUILTIN_WIZARD_PROFILES


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
                        "wireguard-gw": {
                            "source": {
                                "portable": "git::https://github.com/example/infra.git//modules/wireguard-gw?ref=v1.2.3",
                                "local": "platform-infra/modules/wireguard-gw",
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
    assert set(infra) == {"mk8s", "wireguard-gw"}
    assert infra["mk8s"].engine_type == "terraform_module"
    assert infra["mk8s"].group == "Compute"
    assert infra["mk8s"].default_enabled is True
    mk8s_fields = infra["mk8s"].wizard_fields
    assert set(mk8s_fields) == set(BUILTIN_WIZARD_PROFILES["mk8s"])
    assert mk8s_fields["inputs.cluster"]["prompt"] is False
    assert mk8s_fields["inputs.cluster.cluster_name"]["required"] is True
    assert mk8s_fields["inputs.cluster.subnet_id"]["options"] == {
        "from": "project_subnets",
        "args": {"network_id_path": "inputs.cluster.network_id"},
        "auto_select_first": True,
    }
    assert mk8s_fields["inputs.cluster.k8s_version"]["options"] == {
        "from": "mk8s_control_plane_versions",
        "auto_select_first": True,
    }
    assert mk8s_fields["inputs.node_group_defaults.cpu.platform"]["options"] == {
        "from": "mk8s_compatible_platforms",
        "args": {"platform_prefix": "cpu-"},
    }
    assert mk8s_fields["inputs.node_group_defaults.gpu.platform"]["options"] == {
        "from": "mk8s_compatible_platforms",
        "args": {"platform_prefix": "gpu-"},
    }
    assert mk8s_fields["inputs.node_groups"]["prompt"] is False
    assert mk8s_fields["inputs.node_groups.system.platform"]["options"] == {
        "from": "mk8s_compatible_platforms",
        "args": {"platform_prefix": "cpu-"},
    }
    assert mk8s_fields["inputs.node_groups.system.preset"]["options"] == {
        "from": "compute_platform_presets",
        "args": {"platform_path": "inputs.node_groups.system.platform"},
    }
    assert mk8s_fields["inputs.gpu_clusters"]["prompt"] is False
    assert "inputs.cpu_nodes_platform" not in mk8s_fields
    assert "inputs.gpu_nodes_platform" not in mk8s_fields
    assert "inputs.infiniband_fabric" not in mk8s_fields

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
