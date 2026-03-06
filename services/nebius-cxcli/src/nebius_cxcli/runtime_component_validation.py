"""Component-specific runtime validation adapters."""

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
    ssh_user_name = as_text(get_path(payload, "infra.ssh_user_name"))
    if ssh_user_name and not _LINUX_USER_PATTERN.fullmatch(ssh_user_name):
        raise ValueError(
            "infra.ssh_user_name must match Linux username format (for example ubuntu, admin_user)"
        )

    if bool(get_path(payload, "infra.managed_postgresql.enabled", False)):
        tier = as_text(get_path(payload, "infra.managed_postgresql.tier"))
        if tier and tier not in {"small", "medium", "large"}:
            raise ValueError("infra.managed_postgresql.tier must be one of: small, medium, large")

    gpu_enabled = bool(get_path(payload, "infra.mk8s.gpu_nodes.enabled", False))
    mig_enabled = bool(get_path(payload, "infra.mk8s.gpu_nodes.mig.enabled", False))
    mig_strategy = as_text(get_path(payload, "infra.mk8s.gpu_nodes.mig.strategy"))
    mig_parted_config = as_text(get_path(payload, "infra.mk8s.gpu_nodes.mig.parted_config"))
    if mig_enabled and not gpu_enabled:
        raise ValueError("gpu_nodes.mig.enabled=true requires gpu_nodes.enabled=true")
    if mig_enabled and not mig_strategy:
        raise ValueError("gpu_nodes.mig.strategy is required when gpu_nodes.mig.enabled=true")
    if (mig_strategy or mig_parted_config) and not mig_enabled:
        raise ValueError("gpu_nodes.mig.strategy/parted_config require gpu_nodes.mig.enabled=true")

    if bool(get_path(payload, "infra.sfs.csi.enabled", False)):
        mode = as_text(get_path(payload, "infra.sfs.csi.mode", "dynamic")) or "dynamic"
        pvcs = get_path(payload, "infra.sfs.csi.pvcs", [])
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

    wireguard_enabled = bool(get_path(payload, "infra.wireguard-jumphost.enabled", False))
    if wireguard_enabled:
        wireguard_name = as_text(get_path(payload, "infra.wireguard-jumphost.name"))
        if not wireguard_name:
            raise ValueError("wireguard-jumphost.name is required when enabled=true")
        if not id_pattern.fullmatch(wireguard_name):
            raise ValueError(
                "wireguard-jumphost.name must use lowercase letters, digits, and hyphens"
            )
        create_public_ip = bool(
            get_path(payload, "infra.wireguard-jumphost.create_public_ip_allocation", True)
        )
        if as_text(get_path(payload, "infra.wireguard-jumphost.public_ip_allocation_id")) and create_public_ip:
            raise ValueError(
                "wireguard-jumphost.create_public_ip_allocation must be false "
                "when public_ip_allocation_id is set"
            )

        tunnel_cidr = as_text(get_path(payload, "infra.wireguard-jumphost.tunnel_cidr", "10.8.0.1/24"))
        try:
            interface = ipaddress.ip_interface(tunnel_cidr)
        except ValueError as exc:
            raise ValueError(
                "wireguard-jumphost.tunnel_cidr must be a valid IPv4 interface CIDR "
                "(example: 10.8.0.1/24)"
            ) from exc
        if interface.version != 4:
            raise ValueError(
                "wireguard-jumphost.tunnel_cidr must be an IPv4 interface CIDR "
                "(example: 10.8.0.1/24)"
            )

        listen_port = get_path(payload, "infra.wireguard-jumphost.listen_port", 51820)
        try:
            listen_port_int = int(listen_port)
        except Exception as exc:
            raise ValueError("wireguard-jumphost.listen_port must be an integer between 1 and 65535") from exc
        if listen_port_int < 1 or listen_port_int > 65535:
            raise ValueError("wireguard-jumphost.listen_port must be an integer between 1 and 65535")

    ssh_jump_enabled = bool(get_path(payload, "infra.ssh-jumphost.enabled", False))
    if ssh_jump_enabled:
        allowed_cidrs = get_path(payload, "infra.ssh-jumphost.allowed_cidrs", [])
        if not isinstance(allowed_cidrs, list) or not allowed_cidrs:
            raise ValueError(
                "ssh-jumphost.allowed_cidrs must contain at least one source CIDR when enabled=true"
            )
        for cidr in allowed_cidrs:
            try:
                network = ipaddress.ip_network(str(cidr), strict=False)
            except ValueError as exc:
                raise ValueError(
                    "ssh-jumphost.allowed_cidrs must contain valid CIDRs "
                    "(for example 203.0.113.10/32)"
                ) from exc
            if network.version != 4:
                raise ValueError("ssh-jumphost.allowed_cidrs currently supports IPv4 CIDRs only")

    mysterybox_enabled = bool(get_path(payload, "infra.mysterybox.enabled", False))
    mysterybox_secrets = get_path(payload, "infra.mysterybox.secrets", [])
    if mysterybox_enabled and (not isinstance(mysterybox_secrets, list) or not mysterybox_secrets):
        raise ValueError("infra.mysterybox.enabled=true requires infra.mysterybox.secrets")

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
                    "mysterybox.secrets[].k8s_sync.enabled=true requires mysterybox.secrets[].scope='apps'"
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
                        "mysterybox.secrets[].entries[].value_from_env must be an environment variable name"
                    )

    external_secrets_enabled = bool(get_path(payload, "apps.platform.external_secrets.enabled", False))
    external_secrets_mysterybox_enabled = bool(
        get_path(payload, "apps.platform.external_secrets.mysterybox.enabled", False)
    )
    if external_secrets_enabled and external_secrets_mysterybox_enabled:
        if not mysterybox_enabled:
            raise ValueError(
                "apps.platform.external_secrets.mysterybox.enabled=true requires infra.mysterybox.enabled=true"
            )
        has_k8s_sync = any(
            bool(get_path(secret, "k8s_sync.enabled", False))
            for secret in mysterybox_secrets
            if isinstance(secret, Mapping)
        )
        if not has_k8s_sync:
            raise ValueError(
                "apps.platform.external_secrets.mysterybox.enabled=true requires at least one mysterybox.secrets[].k8s_sync.enabled=true entry"
            )
