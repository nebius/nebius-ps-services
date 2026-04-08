from __future__ import annotations

import yaml

from nebius_cxcli.cli import (
    _prompt_path_sort_key,
    _prune_redundant_app_chart_default_values,
    _required_leaf_names_for_entry,
    _run_component_field_wizard,
)
from nebius_cxcli.components import ComponentEntry


def test_required_leaf_names_for_custom_component(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("platform", "preset"),
    )
    entry = ComponentEntry(
        id="wireguard-jumphost",
        scope="infra",
        config_path="infra.wireguard-jumphost",
        description="wg",
        source="platform-infra/modules/wireguard-jumphost",
    )
    required = _required_leaf_names_for_entry(entry)
    assert required == {"platform", "preset"}
    sort_key = _prompt_path_sort_key(
        ("infra", "wireguard-jumphost", "platform"),
        required_leaf_names=required,
    )
    assert sort_key[0] == 0


def test_prune_redundant_app_chart_defaults_removes_only_chart_default_copies(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.helm_chart_default_values",
        lambda **_kwargs: {"foo": {"enabled": True}, "replicaCount": 2},
    )
    entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.workloads.demo_app",
        description="demo",
    )
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "enabled": True,
                    "group": "workloads",
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo-app",
                    "release-name": "demo-app",
                    "values": {
                        "foo": {"enabled": True},
                        "replicaCount": 2,
                        "custom": {"enabled": False},
                    },
                },
            ]
        }
    }

    _prune_redundant_app_chart_default_values(payload=payload, app_entries=(entry,))
    values = payload["apps"]["charts"][0]["values"]
    assert values == {"custom": {"enabled": False}}


def test_run_component_field_wizard_keeps_chart_defaults_virtual_on_stop(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.helm_chart_default_values",
        lambda **_kwargs: {
            "certgen": {
                "job": {
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda _label, default=True: True,
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._prompt_scalar_override",
        lambda _path_label, current, **kwargs: (current, True),
    )
    entry = ComponentEntry(
        id="gateway-helm",
        scope="apps",
        config_path="apps.platform.gateway-helm",
        description="gateway",
        group="platform",
        source="oci://docker.io/envoyproxy/gateway-helm",
        version="1.0.0",
        default_namespace="envoy-gateway-system",
        default_release_name="envoy-gateway",
    )
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "gateway-helm",
                    "instance_id": "gateway-helm",
                    "group": "platform",
                    "enabled": True,
                    "repo": "oci://docker.io/envoyproxy",
                    "version": "1.0.0",
                    "namespace": "envoy-gateway-system",
                    "release-name": "envoy-gateway",
                    "values": {},
                }
            ]
        },
    }

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra=set(),
        selected_apps={"gateway-helm"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )

    assert completed is False
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["apps"]["charts"][0]["values"] == {}
