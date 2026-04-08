from __future__ import annotations

from nebius_cxcli.cli import (
    _provider_source_specs_for_field,
    _resolve_wizard_field_spec,
)
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import _payload_value


def _infra_entry(component_id: str, *, wizard_fields: dict[str, dict] | None = None) -> ComponentEntry:
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
                        "cpu_nodes_platform": "cpu-d3",
                    }
                }
            ]
        }
    }
    assert _payload_value(payload, "infra.components[0].inputs.cpu_nodes_platform") == "cpu-d3"


def test_explicit_wizard_field_relative_path_overrides_dynamic_component_path() -> None:
    entry = _infra_entry(
        "mk8s",
        wizard_fields={
            "inputs.gpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "gpu-"},
                }
            }
        },
    )

    spec = _resolve_wizard_field_spec(
        entry=entry,
        full_path_label="infra.components[0].inputs.gpu_nodes_platform",
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
            "inputs.cpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.cpu_nodes_platform"},
                }
            }
        },
    )

    specs = _provider_source_specs_for_field(
        entry=entry,
        full_path_label="infra.components[0].inputs.cpu_nodes_preset",
    )

    assert specs == (
        (
            "compute_platform_presets",
            {"platform_path": "infra.components[0].inputs.cpu_nodes_platform"},
        ),
    )


def test_provider_specs_return_empty_for_undeclared_field() -> None:
    entry = _infra_entry("mk8s")

    specs = _provider_source_specs_for_field(
        entry=entry,
        full_path_label="infra.components[0].inputs.cpu_nodes_platform",
    )

    assert specs == ()
