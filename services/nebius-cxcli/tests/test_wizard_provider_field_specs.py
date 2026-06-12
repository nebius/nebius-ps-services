from __future__ import annotations

from typing import Any

from nebius_cxcli.cli import (
    _provider_allowed_values_for_field,
    _provider_source_specs_for_field,
    _resolve_dynamic_field_choices,
    _resolve_wizard_field_spec,
    _wizard_field_provider_default_value,
)
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup, _payload_value


def _infra_entry(
    component_id: str, *, wizard_fields: dict[str, dict] | None = None
) -> ComponentEntry:
    return ComponentEntry(
        id=component_id,
        scope="infra",
        config_path=f"infra.components.{component_id}",
        description=f"{component_id} component",
        wizard_fields=wizard_fields or {},
        source=f"../../modules/{component_id}",
    )


def test_provider_payload_value_supports_list_index_path_notation() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "inputs": {
                        "node_group_defaults": {"cpu": {"platform": "cpu-d3"}},
                    }
                }
            ]
        }
    }
    assert (
        _payload_value(payload, "infra.components[0].inputs.node_group_defaults.cpu.platform")
        == "cpu-d3"
    )


def test_explicit_wizard_field_relative_path_overrides_dynamic_component_path() -> None:
    entry = _infra_entry(
        "mk8s",
        wizard_fields={
            "inputs.node_group_defaults.gpu.platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "gpu-"},
                }
            }
        },
    )

    spec = _resolve_wizard_field_spec(
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.platform",
    )

    assert spec == {
        "options": {
            "from": "mk8s_compatible_platforms",
            "args": {"platform_prefix": "gpu-"},
        }
    }


def test_provider_specs_normalize_relative_depends_on_paths() -> None:
    entry = _infra_entry(
        "mk8s",
        wizard_fields={
            "inputs.node_group_defaults.cpu.preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.node_group_defaults.cpu.platform"},
                }
            }
        },
    )

    specs = _provider_source_specs_for_field(
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.cpu.preset",
    )

    assert specs == (
        (
            "compute_platform_presets",
            {"platform_path": "infra.components[0].inputs.node_group_defaults.cpu.platform"},
        ),
    )


def test_provider_specs_return_empty_for_undeclared_field() -> None:
    entry = _infra_entry("mk8s")

    specs = _provider_source_specs_for_field(
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.cpu.platform",
    )

    assert specs == ()


class _StubProviderLookup(ProviderOptionLookup):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def resolve(
        self,
        *,
        provider: str,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> list[OptionChoice]:
        self.calls.append((provider, args, field_path))
        return [OptionChoice(value="cpu-d3", label="cpu-d3")]


def test_dynamic_choices_do_not_run_provider_lookup_for_undeclared_field() -> None:
    entry = _infra_entry("mk8s")
    provider_lookup = _StubProviderLookup()

    choices = _resolve_dynamic_field_choices(
        payload={"infra": {"components": [{"inputs": {}}]}},
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.cpu.platform",
        provider_lookup=provider_lookup,
    )

    assert choices == []
    assert provider_lookup.calls == []


def test_dynamic_choices_use_explicit_wizard_wiring_for_declared_field() -> None:
    entry = _infra_entry(
        "mk8s",
        wizard_fields={
            "inputs.node_group_defaults.cpu.platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "cpu-"},
                }
            }
        },
    )
    provider_lookup = _StubProviderLookup()

    choices = _resolve_dynamic_field_choices(
        payload={"infra": {"components": [{"inputs": {}}]}},
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.cpu.platform",
        provider_lookup=provider_lookup,
    )

    assert [item.value for item in choices] == ["cpu-d3"]
    assert provider_lookup.calls == [
        (
            "mk8s_compatible_platforms",
            {"platform_prefix": "cpu-"},
            "infra.components[0].inputs.node_group_defaults.cpu.platform",
        )
    ]


def test_dynamic_choices_reuse_provider_source_specs_for_filter_args() -> None:
    entry = _infra_entry(
        "managed-postgresql",
        wizard_fields={
            "inputs.network_id": {
                "options": {
                    "from": "project_networks",
                    "filter": "^vpcnetwork-prod-",
                }
            }
        },
    )

    class _FilterAwareLookup(ProviderOptionLookup):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any], str]] = []

        def resolve(
            self,
            *,
            provider: str,
            args: dict[str, Any],
            payload: dict[str, Any],
            field_path: str,
        ) -> list[OptionChoice]:
            _ = payload
            self.calls.append((provider, args, field_path))
            return [OptionChoice(value="vpcnetwork-prod-a", label="prod")]

    provider_lookup = _FilterAwareLookup()
    choices = _resolve_dynamic_field_choices(
        payload={"infra": {"components": [{"inputs": {}}]}},
        entry=entry,
        full_path_label="infra.components[0].inputs.network_id",
        provider_lookup=provider_lookup,
    )

    assert [item.value for item in choices] == ["vpcnetwork-prod-a"]
    assert provider_lookup.calls == [
        (
            "project_networks",
            {"_filter": "^vpcnetwork-prod-"},
            "infra.components[0].inputs.network_id",
        )
    ]


def test_dynamic_vpc_choices_still_include_planned_networks() -> None:
    entry = _infra_entry(
        "managed-postgresql",
        wizard_fields={
            "inputs.network_id": {
                "options": {
                    "from": "project_networks",
                }
            }
        },
    )

    class _NetworkLookup(ProviderOptionLookup):
        def resolve(
            self,
            *,
            provider: str,
            args: dict[str, Any],
            payload: dict[str, Any],
            field_path: str,
        ) -> list[OptionChoice]:
            _ = args, payload, field_path
            assert provider == "project_networks"
            return [OptionChoice(value="vpcnetwork-live", label="live network")]

    choices = _resolve_dynamic_field_choices(
        payload={
            "infra": {
                "components": [
                    {
                        "id": "vpc",
                        "instance_id": "network-a",
                        "enabled": True,
                        "inputs": {"network": {"name": "training-network"}},
                    },
                    {
                        "id": "managed-postgresql",
                        "instance_id": "postgres",
                        "enabled": True,
                        "inputs": {},
                    },
                ]
            }
        },
        entry=entry,
        full_path_label="infra.components[1].inputs.network_id",
        provider_lookup=_NetworkLookup(),
    )

    assert [item.value for item in choices] == [
        "vpcnetwork-live",
        "planned:vpc@network-a.network_id",
    ]
    assert choices[1].label == "planned: training-network  (infra:vpc@network-a)"


def test_dynamic_choices_normalize_relative_depends_on_paths() -> None:
    entry = _infra_entry(
        "mk8s",
        wizard_fields={
            "inputs.node_group_defaults.cpu.preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.node_group_defaults.cpu.platform"},
                }
            }
        },
    )
    provider_lookup = _StubProviderLookup()

    _resolve_dynamic_field_choices(
        payload={
            "infra": {
                "components": [
                    {"inputs": {"node_group_defaults": {"cpu": {"platform": "cpu-d3"}}}}
                ]
            }
        },
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.cpu.preset",
        provider_lookup=provider_lookup,
    )

    assert provider_lookup.calls == [
        (
            "compute_platform_presets",
            {"platform_path": "infra.components[0].inputs.node_group_defaults.cpu.platform"},
            "infra.components[0].inputs.node_group_defaults.cpu.preset",
        )
    ]


def test_provider_allowed_values_reuse_filter_regex_from_wizard_metadata() -> None:
    entry = _infra_entry(
        "managed-postgresql",
        wizard_fields={
            "inputs.network_id": {
                "options": {
                    "from": "project_networks",
                    "filter": "^vpcnetwork-prod-",
                }
            }
        },
    )

    class _FilterAwareLookup(ProviderOptionLookup):
        def resolve(
            self,
            *,
            provider: str,
            args: dict[str, Any],
            payload: dict[str, Any],
            field_path: str,
        ) -> list[OptionChoice]:
            _ = provider, payload, field_path
            assert args == {"_filter": "^vpcnetwork-prod-"}
            return [OptionChoice(value="vpcnetwork-prod-a", label="prod")]

    allowed, providers = _provider_allowed_values_for_field(
        payload={"infra": {"components": [{"inputs": {}}]}},
        entry=entry,
        full_path_label="infra.components[0].inputs.network_id",
        provider_lookup=_FilterAwareLookup(),
    )

    assert allowed == {"vpcnetwork-prod-a"}
    assert providers == ("project_networks",)


def test_provider_default_for_string_list_field_materializes_choice_values() -> None:
    entry = _infra_entry(
        "ssh-jumphost",
        wizard_fields={
            "inputs.allowed_cidrs": {
                "default_from": {"from": "operator_public_ip_cidr"},
                "type_hint": "list(string)",
            }
        },
    )

    class _DefaultLookup(ProviderOptionLookup):
        def resolve(
            self,
            *,
            provider: str,
            args: dict[str, Any],
            payload: dict[str, Any],
            field_path: str,
        ) -> list[OptionChoice]:
            assert provider == "operator_public_ip_cidr"
            assert args == {}
            assert payload == {"infra": {"components": [{"inputs": {}}]}}
            assert field_path == "infra.components[0].inputs.allowed_cidrs"
            return [OptionChoice(value="203.0.113.10/32", label="detected")]

    default = _wizard_field_provider_default_value(
        payload={"infra": {"components": [{"inputs": {}}]}},
        entry=entry,
        full_path_label="infra.components[0].inputs.allowed_cidrs",
        provider_lookup=_DefaultLookup(),
        type_hint="list(string)",
    )

    assert default == ["203.0.113.10/32"]
