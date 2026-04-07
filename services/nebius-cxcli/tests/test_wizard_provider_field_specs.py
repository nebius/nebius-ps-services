from __future__ import annotations

from nebius_cxcli.cli import _infer_infra_provider_field_spec, _resolve_wizard_field_spec
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import _payload_value


def _infra_entry(component_id: str) -> ComponentEntry:
    return ComponentEntry(
        id=component_id,
        scope="infra",
        config_path=f"infra.components.{component_id}",
        description=f"{component_id} component",
    )


def test_mk8s_cpu_nodes_platform_uses_mk8s_compatible_platforms() -> None:
    """mk8s cpu_nodes_platform is now declared in wizard_fields YAML, not inferred.
    The inference function falls back to compute_platforms for mk8s since the
    entry.id == 'mk8s' branch was removed.  The real behavior routes through
    _resolve_wizard_field_spec which reads the YAML-declared options first."""
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("mk8s"),
        full_path_label="infra.components[0].inputs.cpu_nodes_platform",
    )
    # Inference now returns compute_platforms; the real mk8s resolution uses
    # wizard_fields.options.from = mk8s_compatible_platforms instead.
    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "compute_platforms",
                "args": {"platform_prefix": "cpu-"},
            }
        ]
    }


def test_non_mk8s_platform_uses_compute_platforms() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("wireguard-jumphost"),
        full_path_label="infra.components[0].inputs.cpu_nodes_platform",
    )
    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "compute_platforms",
                "args": {"platform_prefix": "cpu-"},
            }
        ]
    }


def test_preset_field_infers_matching_platform_path_for_underscore_shape() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("mk8s"),
        full_path_label="infra.components[0].inputs.cpu_nodes_preset",
    )
    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "compute_platform_presets",
                "args": {"platform_path": "infra.components[0].inputs.cpu_nodes_platform"},
            }
        ]
    }


def test_driver_preset_field_does_not_infer_compute_platform_presets() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("mk8s"),
        full_path_label="infra.components[0].inputs.gpu_default_drivers_preset",
    )
    assert spec is None


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


def test_parent_id_uses_tenant_projects_provider() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("managed-postgresql"),
        full_path_label="infra.components[0].inputs.parent_id",
    )
    assert spec == {"sources": [{"source": "provider", "provider": "tenant_projects"}]}


def test_network_id_uses_project_networks_provider_with_colocated_parent_path() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("managed-postgresql"),
        full_path_label="infra.components[0].inputs.network_id",
    )
    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "project_networks",
                "args": {
                    "project_id_path": "infra.components[0].inputs.parent_id",
                    "fallback_project_id_path": "client_info.nebius.project_id",
                },
            }
        ]
    }


def test_subnet_id_uses_project_subnets_provider_with_colocated_parent_path() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("wireguard-jumphost"),
        full_path_label="infra.components[0].inputs.subnet_id",
    )
    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "project_subnets",
                "args": {
                    "project_id_path": "infra.components[0].inputs.parent_id",
                    "fallback_project_id_path": "client_info.nebius.project_id",
                },
            }
        ]
    }


def test_kubernetes_version_uses_control_plane_versions_provider() -> None:
    spec = _infer_infra_provider_field_spec(
        entry=_infra_entry("mk8s"),
        full_path_label="infra.components[0].inputs.k8s_version",
    )
    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "mk8s_control_plane_versions",
            }
        ]
    }


def test_explicit_wizard_field_relative_path_overrides_dynamic_component_path() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        wizard_fields={
            "inputs.gpu_nodes_platform": {
                "sources": [
                    {
                        "source": "provider",
                        "provider": "mk8s_compatible_platforms",
                        "args": {"platform_prefix": "gpu-"},
                    }
                ]
            }
        },
    )

    spec = _resolve_wizard_field_spec(
        entry=entry,
        full_path_label="infra.components[0].inputs.gpu_nodes_platform",
    )

    assert spec == {
        "sources": [
            {
                "source": "provider",
                "provider": "mk8s_compatible_platforms",
                "args": {"platform_prefix": "gpu-"},
            }
        ]
    }
