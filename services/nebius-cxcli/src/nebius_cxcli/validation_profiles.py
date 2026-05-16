"""Built-in runtime validation-profile defaults for bundled infra components."""

from __future__ import annotations

_BUILTIN_VALIDATION_PROFILES: dict[str, str] = {
    "mk8s": "mk8s_cluster",
    "managed-postgresql": "postgresql_cluster",
    "sfs": "shared_filesystem",
    "mysterybox": "mysterybox",
    "vm": "vm_instance",
    "wireguard-gw": "wireguard_gw",
    "ssh-jumphost": "ssh_jumphost",
}


def resolve_builtin_validation_profile(component_id: str) -> str:
    return _BUILTIN_VALIDATION_PROFILES.get(str(component_id).strip().lower(), "")
