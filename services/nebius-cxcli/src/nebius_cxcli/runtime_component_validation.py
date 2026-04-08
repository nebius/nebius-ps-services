"""Component-specific runtime validation adapters.

Validation rules are dispatched by explicit component validation profiles from
the source catalog rather than ad-hoc aliases.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from re import Pattern
from typing import Any

_LINUX_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def validate_component_runtime_rules(
    payload: Mapping[str, Any],
    *,
    get_path: Callable[[Mapping[str, Any], str, Any], Any],
    as_text: Callable[[Any], str],
    id_pattern: Pattern[str],
    env_var_pattern: Pattern[str],
) -> None:
    ssh_user_name = as_text(get_path(payload, "shared.admin_ssh.user_name"))
    if ssh_user_name and not _LINUX_USER_PATTERN.fullmatch(ssh_user_name):
        raise ValueError(
            "shared.admin_ssh.user_name must match Linux username format (for example ubuntu, admin_user)"
        )

    from .components import component_entries

    for entry in component_entries("infra"):
        base = entry.config_path
        profile = getattr(entry, "validation_profile", "")

        if profile == "postgresql_cluster":
            _validate_postgresql(payload, get_path, as_text, base)
        elif profile == "mk8s_cluster":
            _validate_mk8s_gpu(payload, get_path, as_text, base)
        elif profile == "shared_filesystem":
            _validate_sfs_csi(payload, get_path, as_text, base)
        elif profile == "wireguard_jumphost":
            _validate_wireguard(payload, get_path, as_text, base, id_pattern)
        elif profile == "ssh_jumphost":
            _validate_ssh_jumphost(payload, get_path, as_text, base)

    for entry in component_entries("infra"):
        if getattr(entry, "validation_profile", "") == "mysterybox":
            _validate_mysterybox(payload, get_path, as_text, entry.config_path, env_var_pattern)


def _validate_postgresql(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    if bool(get_path(payload, f"{base}.enabled", False)):
        tier = as_text(get_path(payload, f"{base}.tier"))
        if tier and tier not in {"small", "medium", "large"}:
            raise ValueError(f"{base}.tier must be one of: small, medium, large")


def _validate_mk8s_gpu(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    gpu_enabled = bool(get_path(payload, f"{base}.gpu_nodes.enabled", False))
    mig_enabled = bool(get_path(payload, f"{base}.gpu_nodes.mig.enabled", False))
    mig_strategy = as_text(get_path(payload, f"{base}.gpu_nodes.mig.strategy"))
    mig_parted_config = as_text(get_path(payload, f"{base}.gpu_nodes.mig.parted_config"))
    if mig_enabled and not gpu_enabled:
        raise ValueError("gpu_nodes.mig.enabled=true requires gpu_nodes.enabled=true")
    if mig_enabled and not mig_strategy:
        raise ValueError("gpu_nodes.mig.strategy is required when gpu_nodes.mig.enabled=true")
    if (mig_strategy or mig_parted_config) and not mig_enabled:
        raise ValueError("gpu_nodes.mig.strategy/parted_config require gpu_nodes.mig.enabled=true")


def _validate_sfs_csi(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    if bool(get_path(payload, f"{base}.csi.enabled", False)):
        mode = as_text(get_path(payload, f"{base}.csi.mode", "dynamic")) or "dynamic"
        pvcs = get_path(payload, f"{base}.csi.pvcs", [])
        if isinstance(pvcs, list):
            seen: set[tuple[str, str]] = set()
            for pvc in pvcs:
                if not isinstance(pvc, Mapping):
                    continue
                namespace = as_text(pvc.get("namespace"))
                name = as_text(pvc.get("name"))
                key = (namespace, name)
                if key in seen:
                    raise ValueError(
                        f"Duplicate PVC definition for namespace/name '{namespace}/{name}'"
                    )
                seen.add(key)

                static_pv_name = pvc.get("static_pv_name", pvc.get("static-pv-name"))
                static_sub_path = pvc.get("static_sub_path", pvc.get("static-sub-path"))
                if mode == "dynamic" and (static_pv_name is not None or static_sub_path is not None):
                    raise ValueError("sfs.csi.pvcs[].static_* fields require sfs.csi.mode='static'")


def _validate_wireguard(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    id_pattern: Pattern[str],
) -> None:
    wireguard_enabled = bool(get_path(payload, f"{base}.enabled", False))
    if wireguard_enabled:
        wireguard_name = as_text(get_path(payload, f"{base}.name"))
        if not wireguard_name:
            raise ValueError(f"{base}.name is required when enabled=true")
        if not id_pattern.fullmatch(wireguard_name):
            raise ValueError(
                f"{base}.name must use lowercase letters, digits, and hyphens"
            )
        create_public_ip = bool(
            get_path(payload, f"{base}.create_public_ip_allocation", True)
        )
        if as_text(get_path(payload, f"{base}.public_ip_allocation_id")) and create_public_ip:
            raise ValueError(
                f"{base}.create_public_ip_allocation must be false "
                "when public_ip_allocation_id is set"
            )

        tunnel_cidr = as_text(get_path(payload, f"{base}.tunnel_cidr", "10.8.0.1/24"))
        try:
            interface = ipaddress.ip_interface(tunnel_cidr)
        except ValueError as exc:
            raise ValueError(
                f"{base}.tunnel_cidr must be a valid IPv4 interface CIDR "
                "(example: 10.8.0.1/24)"
            ) from exc
        if interface.version != 4:
            raise ValueError(
                f"{base}.tunnel_cidr must be an IPv4 interface CIDR "
                "(example: 10.8.0.1/24)"
            )

        listen_port = get_path(payload, f"{base}.listen_port", 51820)
        try:
            listen_port_int = int(listen_port)
        except Exception as exc:
            raise ValueError(f"{base}.listen_port must be an integer between 1 and 65535") from exc
        if listen_port_int < 1 or listen_port_int > 65535:
            raise ValueError(f"{base}.listen_port must be an integer between 1 and 65535")


def _validate_ssh_jumphost(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    ssh_jump_enabled = bool(get_path(payload, f"{base}.enabled", False))
    if ssh_jump_enabled:
        allowed_cidrs = get_path(payload, f"{base}.allowed_cidrs", [])
        if not isinstance(allowed_cidrs, list) or not allowed_cidrs:
            raise ValueError(
                f"{base}.allowed_cidrs must contain at least one source CIDR when enabled=true"
            )
        for cidr in allowed_cidrs:
            try:
                network = ipaddress.ip_network(str(cidr), strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"{base}.allowed_cidrs must contain valid CIDRs "
                    "(for example 203.0.113.10/32)"
                ) from exc
            if network.version != 4:
                raise ValueError(f"{base}.allowed_cidrs currently supports IPv4 CIDRs only")


def _validate_mysterybox(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    env_var_pattern: Pattern[str],
) -> None:
    mysterybox_enabled = bool(get_path(payload, f"{base}.enabled", False))
    mysterybox_secrets = get_path(payload, f"{base}.secrets", [])
    if mysterybox_enabled and (not isinstance(mysterybox_secrets, list) or not mysterybox_secrets):
        raise ValueError(f"{base}.enabled=true requires {base}.secrets")

    if isinstance(mysterybox_secrets, list):
        for secret in mysterybox_secrets:
            if not isinstance(secret, Mapping):
                continue
            scope = as_text(secret.get("scope"))
            k8s_sync_enabled = bool(
                get_path(secret, "k8s_sync.enabled", False)
            )
            if k8s_sync_enabled and scope != "apps":
                raise ValueError(
                    f"{base}.secrets[].k8s_sync.enabled=true requires {base}.secrets[].scope='apps'"
                )
            entries = secret.get("entries", [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                env_name = as_text(entry.get("value_from_env"))
                if env_name and not env_var_pattern.fullmatch(env_name):
                    raise ValueError(
                        f"{base}.secrets[].entries[].value_from_env must be an environment variable name"
                    )

    external_secrets_enabled = bool(get_path(payload, "apps.platform.external_secrets.enabled", False))
    external_secrets_mysterybox_enabled = bool(
        get_path(payload, "apps.platform.external_secrets.mysterybox.enabled", False)
    )
    if external_secrets_enabled and external_secrets_mysterybox_enabled:
        if not mysterybox_enabled:
            raise ValueError(
                f"apps.platform.external_secrets.mysterybox.enabled=true requires {base}.enabled=true"
            )
        has_k8s_sync = any(
            bool(get_path(secret, "k8s_sync.enabled", False))
            for secret in mysterybox_secrets
            if isinstance(secret, Mapping)
        )
        if not has_k8s_sync:
            raise ValueError(
                f"apps.platform.external_secrets.mysterybox.enabled=true requires at least one {base}.secrets[].k8s_sync.enabled=true entry"
            )
