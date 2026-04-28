from __future__ import annotations

import yaml

import nebius_cxcli.cli as cli_module
from nebius_cxcli.cli import (
    _prompt_path_sort_key,
    _prune_redundant_app_chart_default_values,
    _required_leaf_names_for_entry,
    _run_component_field_wizard,
)
from nebius_cxcli.components import ComponentEntry


def _mk8s_observability_wizard_entry() -> ComponentEntry:
    return ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "deploy.targets[].observability.enabled": {"default": False},
            "deploy.targets[].observability.kubernetes.logs.enabled": {"default": True},
            "deploy.targets[].observability.kubernetes.metrics.enabled": {"default": True},
            "deploy.targets[].observability.kubernetes.metrics.collect_k8s_cluster_metrics": {
                "default": True,
            },
            "deploy.targets[].observability.kubernetes.traces.enabled": {"default": True},
            "inputs.mk8s_cluster_public_endpoint": {"default": True},
        },
    )


def _observability_agent_entry() -> ComponentEntry:
    return ComponentEntry(
        id="nebius-observability-agent",
        scope="apps",
        config_path="apps.observability.nebius-observability-agent",
        description="Nebius observability agent",
        group="observability",
        source=(
            "oci://cr.nebius.cloud/observability/public/"
            "nebius-observability-agent-helm/nebius-observability-agent-helm"
        ),
        version="1.0.0",
        default_namespace="observability",
        default_release_name="nebius-observability-agent",
    )


def _grafana_entry() -> ComponentEntry:
    return ComponentEntry(
        id="grafana",
        scope="apps",
        config_path="apps.observability.grafana",
        description="Grafana observability console",
        group="observability",
        source="https://grafana-community.github.io/helm-charts/grafana",
        version="12.1.3",
        default_namespace="observability",
        default_release_name="grafana",
    )


def _gateway_entry() -> ComponentEntry:
    return ComponentEntry(
        id="gateway-helm",
        scope="apps",
        config_path="apps.platform.gateway-helm",
        description="Envoy Gateway control plane",
        group="platform",
        source="oci://docker.io/envoyproxy/gateway-helm",
        version="1.7.0",
        default_namespace="envoy-gateway-system",
        default_release_name="envoy-gateway",
    )


def _mk8s_observability_payload() -> dict[str, object]:
    return {
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
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
        "deploy": {
            "targets": [
                {
                    "instance_id": "mk8s",
                    "observability": {
                        "enabled": False,
                        "kubernetes": {
                            "logs": {"enabled": True},
                            "metrics": {
                                "enabled": True,
                                "collect_k8s_cluster_metrics": True,
                            },
                            "traces": {"enabled": True},
                        },
                    },
                },
            ],
        },
    }


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


def test_run_component_field_wizard_announces_observability_app_at_enable_prompt(
    monkeypatch,
) -> None:
    events: list[str] = []

    def _capture_continue_phase(label: str, *, default: bool = True) -> bool:
        events.append(f"phase:{label}")
        return label == "Configure 'mk8s' component fields now?"

    def _capture_prompt(path_label: str, current, **_kwargs):
        events.append(f"prompt:{path_label}")
        if path_label == "deploy.targets[0].observability.enabled":
            return True, False
        return current, False

    def _capture_print(message="", *_args, **_kwargs) -> None:
        text = str(message)
        if "Adjusted component selection:" in text:
            events.append(f"print:{text}")

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)
    monkeypatch.setattr(cli_module.console, "print", _capture_print)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(_mk8s_observability_payload(), sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(_mk8s_observability_wizard_entry(),),
        app_entries=(_observability_agent_entry(), _grafana_entry(), _gateway_entry()),
        provider_lookup=None,
    )

    assert completed is True
    adjusted_index = next(index for index, event in enumerate(events) if event.startswith("print:"))
    assert events.index("prompt:deploy.targets[0].observability.enabled") < adjusted_index
    assert adjusted_index < events.index(
        "prompt:infra.components[0].inputs.mk8s_cluster_public_endpoint"
    )
    assert adjusted_index < events.index(
        "phase:Configure 'nebius-observability-agent on mk8s' component fields now?"
    )
    assert "answering 'n' keeps the selected app defaults" in events[adjusted_index]

    updated_payload = yaml.safe_load(updated_yaml)
    enabled_apps = {row["id"]: row for row in updated_payload["apps"]["charts"]}
    assert enabled_apps["nebius-observability-agent"]["enabled"] is True
    assert enabled_apps["grafana"]["enabled"] is True
    assert enabled_apps["gateway-helm"]["enabled"] is True


def test_run_component_field_wizard_removes_backtracked_observability_app(
    monkeypatch,
) -> None:
    answers = {
        "deploy.targets[0].observability.enabled": [True, False],
        "deploy.targets[0].observability.kubernetes.logs.enabled": [cli_module._WIZARD_BACKTRACK],
    }

    def _answer_prompt(path_label: str, current, **_kwargs):
        pending = answers.get(path_label)
        if pending:
            return pending.pop(0), False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda label, default=True: label == "Configure 'mk8s' component fields now?",
    )
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _answer_prompt)
    monkeypatch.setattr(cli_module.console, "print", lambda *_args, **_kwargs: None)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(_mk8s_observability_payload(), sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(_mk8s_observability_wizard_entry(),),
        app_entries=(_observability_agent_entry(), _grafana_entry(), _gateway_entry()),
        provider_lookup=None,
    )

    assert completed is True
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["deploy"]["targets"][0]["observability"]["enabled"] is False
    assert updated_payload["apps"]["charts"] == []


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


def test_run_component_field_wizard_uses_scope_specific_phase_defaults(monkeypatch) -> None:
    prompts: list[tuple[str, bool]] = []

    def _capture_continue_phase(label: str, *, default: bool = True) -> bool:
        prompts.append((label, default))
        return False

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)

    infra_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
    )
    app_entry = ComponentEntry(
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

    infra_payload = {
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
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }
    app_payload = {
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

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(infra_payload, sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(infra_entry,),
        app_entries=(),
        provider_lookup=None,
    )
    assert completed is True

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(app_payload, sort_keys=False),
        selected_infra=set(),
        selected_apps={"gateway-helm"},
        infra_entries=(),
        app_entries=(app_entry,),
        provider_lookup=None,
    )
    assert completed is True

    assert prompts == [
        ("Configure 'mk8s' component fields now?", True),
        ("Configure 'gateway-helm' component fields now?", False),
    ]
