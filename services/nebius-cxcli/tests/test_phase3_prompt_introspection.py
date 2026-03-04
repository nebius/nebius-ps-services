from __future__ import annotations

from nebius_cxcli.cli import (
    _hydrate_app_component_values_from_chart_defaults,
    _prompt_path_sort_key,
    _required_leaf_names_for_entry,
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
        origin="custom",
        source="platform-infra/modules/wireguard-jumphost",
    )
    required = _required_leaf_names_for_entry(entry)
    assert required == {"platform", "preset"}
    required_rank, _label = _prompt_path_sort_key(
        ("infra", "wireguard-jumphost", "platform"),
        required_leaf_names=required,
    )
    assert required_rank == 0


def test_hydrate_app_values_merges_chart_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.helm_chart_default_values",
        lambda **_kwargs: {"foo": {"enabled": True}, "replicaCount": 2},
    )
    entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.workloads.demo_app",
        description="demo",
        origin="helm",
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
                    "values": {"foo": {"enabled": False}},
                },
            ]
        }
    }

    _hydrate_app_component_values_from_chart_defaults(payload=payload, entry=entry)
    values = payload["apps"]["charts"][0]["values"]
    assert values["foo"]["enabled"] is False
    assert values["replicaCount"] == 2
