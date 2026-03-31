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
                "infra": {
                    "tf_modules": [
                        {
                            "module": "mk8s",
                            "portable_source": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                            "local_source": "platform-infra/modules/mk8s",
                            "description": "Managed Kubernetes",
                            "group": "Compute",
                            "enable": True,
                        },
                        {
                            "module": "wireguard-jumphost",
                            "portable_source": "git::https://github.com/example/infra.git//modules/wireguard-jumphost?ref=v1.2.3",
                            "local_source": "platform-infra/modules/wireguard-jumphost",
                            "description": "WireGuard module",
                            "group": "Network",
                            "enable": False,
                        },
                    ]
                },
                "apps": {
                    "helm_charts": [
                        {
                            "name": "n8n",
                            "repo": "https://community-charts.github.io/helm-charts",
                            "version": "1.16.29",
                            "namespace": "n8n",
                            "releasename": "n8n",
                            "description": "n8n workload",
                            "group": "Workloads",
                            "enable": False,
                        }
                    ],
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
    assert infra["mk8s"].origin == "custom"
    assert infra["mk8s"].engine_type == "terraform_module"
    assert infra["mk8s"].group == "Compute"
    assert infra["mk8s"].default_enabled is True

    apps = {entry.id: entry for entry in component_entries("apps")}
    assert "n8n" in apps
    assert apps["n8n"].origin == "helm"
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
                "infra": {"tf_modules": []},
                "apps": {
                    "helm_charts": [
                        {
                            "name": "gateway-helm",
                            "repo": "oci://docker.io/envoyproxy/gateway-helm",
                            "version": "1.4.2",
                            "namespace": "envoy-gateway-system",
                            "releasename": "envoy-gateway",
                            "group": "Platform",
                            "enable": False,
                        }
                    ]
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
