"""Built-in catalog wizard-profile shorthands."""

from __future__ import annotations

import copy
from typing import Any


def _project_subnets_field() -> dict[str, dict[str, Any]]:
    return {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        }
    }


def _compute_platform_and_preset_fields(
    *,
    platform_field: str,
    preset_field: str,
) -> dict[str, dict[str, Any]]:
    return {
        platform_field: {
            "options": {
                "from": "compute_platforms",
            }
        },
        preset_field: {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": platform_field,
            }
        },
    }


def _static_sources(*values: str) -> dict[str, Any]:
    return {
        "sources": [
            {
                "source": "static",
                "values": list(values),
            }
        ]
    }


BUILTIN_WIZARD_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "mk8s": {
        **_project_subnets_field(),
        "inputs.cpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "cpu-",
            }
        },
        "inputs.gpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "prefix": "gpu-",
            }
        },
        "inputs.cpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.cpu_nodes_platform",
            }
        },
        "inputs.gpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "depends_on": "inputs.gpu_nodes_platform",
            }
        },
    },
    "managed-postgresql": {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
            }
        },
        "inputs.tier": _static_sources("small", "medium", "large"),
    },
    "wireguard-jumphost": {
        **_project_subnets_field(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
    },
    "ssh-jumphost": {
        **_project_subnets_field(),
        **_compute_platform_and_preset_fields(
            platform_field="inputs.platform",
            preset_field="inputs.preset",
        ),
    },
    "object-storage": {
        "inputs.versioning_policy": _static_sources("DISABLED", "ENABLED", "SUSPENDED"),
        "inputs.object_audit_logging": _static_sources("NONE", "MUTATE_ONLY", "ALL"),
    },
}


def builtin_wizard_profile_names() -> tuple[str, ...]:
    return tuple(sorted(BUILTIN_WIZARD_PROFILES))


def resolve_builtin_wizard_profile(name: str) -> dict[str, dict[str, Any]]:
    profile = BUILTIN_WIZARD_PROFILES.get(name)
    if profile is None:
        supported = ", ".join(builtin_wizard_profile_names())
        raise ValueError(f"wizard_profile '{name}' is unknown; supported values: {supported}")
    return copy.deepcopy(profile)
