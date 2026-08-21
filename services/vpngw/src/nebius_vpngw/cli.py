import functools
import hashlib
import inspect
import json
import math
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import typing as t
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import typer
import yaml
from rich import print
from typer.core import TyperGroup

from . import __version__
from .config_loader import (
    GatewayGroupSpec,
    ResolvedDeploymentPlan,
    build_config_from_peer_files,
    has_unresolved_tunnel_psk_placeholders,
    load_local_config,
    merge_with_peer_configs,
)
from .config_template import DEFAULT_CONFIG_TEMPLATE
from .config_wizard import (
    WizardCancelled,
    WizardInterrupted,
    WizardValidationError,
    run_config_wizard,
)
from .deploy.route_manager import (
    NebiusSDKRouteBackend,
    RouteManagementError,
    RouteManager,
)
from .deploy.ssh_policy import (
    SSHTrustPolicy,
    VMHASSHTrustScope,
    build_openssh_base_command,
    publish_vm_ha_ssh_trust,
    require_vm_ha_ssh_policy,
)
from .deploy.ssh_push import SSHPush
from .deploy.vm_ha_cloud import (
    AllocationOwner,
    InstanceCloudState,
    NebiusSDKCloudClient,
    VMHACloudAdapter,
)
from .deploy.vm_ha_identity import FormerVMHAProvenance, LegacyVMHAIdentity
from .deploy.vm_ha_lifecycle import (
    VMHAApplyLock,
    VMHALifecycleJournal,
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    vm_ha_activation_effect_is_host_only,
    vm_ha_effective_resource_bindings,
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_cycle_for_approval,
    vm_ha_passive_replacement_cycles,
    vm_ha_passive_replacement_effect,
    vm_ha_resource_binding_matches_observation,
)
from .deploy.vm_manager import VMManager
from .vm_ha_config_wizard import (
    VMHAConversionResult,
    is_vm_ha_conversion_candidate,
    resolve_vm_ha_conversion_source,
    run_vm_ha_conversion_wizard,
    validate_vm_ha_conversion_source,
)

DEFAULT_CONFIG_FILENAME = "nebius-vpngw.config.yaml"


def _format_help_examples(examples: t.Iterable[str]) -> str:
    """Format practical invocations consistently for Typer help epilogs."""
    return "Examples:\n\n" + "\n\n".join(f"  {example}" for example in examples)


_ROOT_HELP_EXAMPLES = (
    "nebius-vpngw create-config nebius-vpngw.config.yaml",
    "nebius-vpngw validate-config nebius-vpngw.config.yaml",
    "nebius-vpngw apply --local-config-file nebius-vpngw.config.yaml --dry-run",
)

_COMMAND_EXAMPLES: t.Mapping[tuple[str, ...], tuple[str, ...]] = MappingProxyType(
    {
        ("create-config",): (
            "nebius-vpngw create-config nebius-vpngw.config.yaml",
            "nebius-vpngw create-config nebius-vpngw.config.yaml --no-interactive",
        ),
        ("configure-vm-ha",): (
            "nebius-vpngw configure-vm-ha --local-config-file gateway.config.yaml "
            "--output gateway.vm-ha.config.yaml",
        ),
        ("prep-network",): (
            "nebius-vpngw prep-network --local-config-file nebius-vpngw.config.yaml",
        ),
        ("validate-config",): ("nebius-vpngw validate-config nebius-vpngw.config.yaml",),
        ("apply",): ("nebius-vpngw apply --local-config-file nebius-vpngw.config.yaml --dry-run",),
        ("status",): ("nebius-vpngw status --local-config-file nebius-vpngw.config.yaml",),
        ("set-vm-ha-mtls",): (
            "nebius-vpngw set-vm-ha-mtls --local-config-file nebius-vpngw.config.yaml --dry-run",
            "nebius-vpngw set-vm-ha-mtls --local-config-file nebius-vpngw.config.yaml --approve PLAN_DIGEST",
        ),
        ("vm-ha-rearm",): (
            "nebius-vpngw vm-ha-rearm --local-config-file nebius-vpngw.config.yaml",
        ),
        ("failover",): (
            "nebius-vpngw failover vm --local-config-file nebius-vpngw.config.yaml",
            "nebius-vpngw failover tunnel PASSIVE_TUNNEL_NAME "
            "--local-config-file nebius-vpngw.config.yaml",
        ),
        ("failover", "vm"): (
            "nebius-vpngw failover vm --local-config-file nebius-vpngw.config.yaml",
        ),
        ("failover", "tunnel"): (
            "nebius-vpngw failover tunnel PASSIVE_TUNNEL_NAME "
            "--local-config-file nebius-vpngw.config.yaml",
        ),
        ("failback",): (
            "nebius-vpngw failback vm --local-config-file nebius-vpngw.config.yaml",
            "nebius-vpngw failback tunnel ACTIVE_TUNNEL_NAME "
            "--local-config-file nebius-vpngw.config.yaml",
        ),
        ("failback", "vm"): (
            "nebius-vpngw failback vm --local-config-file nebius-vpngw.config.yaml",
        ),
        ("failback", "tunnel"): (
            "nebius-vpngw failback tunnel ACTIVE_TUNNEL_NAME "
            "--local-config-file nebius-vpngw.config.yaml",
        ),
        ("add-routes-local",): (
            "nebius-vpngw add-routes-local --local-config-file nebius-vpngw.config.yaml",
        ),
        ("list-routes-local",): (
            "nebius-vpngw list-routes-local --local-config-file nebius-vpngw.config.yaml",
        ),
        ("list-routes-remote",): (
            "nebius-vpngw list-routes-remote --local-config-file "
            "nebius-vpngw.config.yaml --connection CONNECTION_NAME",
        ),
        ("restart-tunnel",): (
            "nebius-vpngw restart-tunnel TUNNEL_NAME --local-config-file nebius-vpngw.config.yaml",
            "nebius-vpngw restart-tunnel all --local-config-file nebius-vpngw.config.yaml",
        ),
        ("create-from-peer-config",): (
            "nebius-vpngw create-from-peer-config gateway.config.yaml "
            "--peer-config-file peer-vpn.txt",
        ),
        ("destroy",): ("nebius-vpngw destroy --local-config-file nebius-vpngw.config.yaml",),
    }
)

_COMMAND_APPLICABILITY: t.Mapping[str, str] = MappingProxyType(
    {
        "create-config": "all",
        "configure-vm-ha": "ordinary",
        "prep-network": "all",
        "validate-config": "all",
        "apply": "all",
        "status": "all",
        "set-vm-ha-mtls": "vm-ha",
        "vm-ha-rearm": "vm-ha",
        "add-routes-local": "route-policy",
        "list-routes-local": "all",
        "list-routes-remote": "all",
        "restart-tunnel": "ordinary",
        "create-from-peer-config": "all",
        "destroy": "ordinary",
        "failover vm": "vm-ha",
        "failover tunnel": "ordinary-bgp",
        "failback vm": "vm-ha",
        "failback tunnel": "ordinary-bgp",
    }
)


def _configured_routing_modes(local_cfg: t.Mapping[str, t.Any]) -> frozenset[str]:
    default_mode = _normalize_config_value(
        ((local_cfg.get("defaults") or {}).get("routing") or {}).get("mode"),
        "bgp",
    )
    modes = {
        _normalize_config_value(connection.get("routing_mode"), default_mode)
        for connection in local_cfg.get("connections") or []
    }
    return frozenset(modes or {default_mode})


def _enforce_command_applicability(
    command: str,
    plan: ResolvedDeploymentPlan,
    local_cfg: t.Mapping[str, t.Any],
    *,
    summarize: bool = False,
    swap_route_table: bool = False,
    yes: bool = False,
) -> None:
    """Reject unsupported command/topology/mode combinations before effects."""

    applicability = _COMMAND_APPLICABILITY[command]
    is_vm_ha = plan.vm_ha is not None
    modes = _configured_routing_modes(local_cfg)

    if applicability.startswith("ordinary") and is_vm_ha:
        alternative = {
            "restart-tunnel": "use 'nebius-vpngw apply' or the VM-HA controller workflow",
            "destroy": "remove VM HA through the supported 'apply' lifecycle first",
            "failover tunnel": "use 'nebius-vpngw failover vm' for VM ownership",
            "failback tunnel": "use 'nebius-vpngw failback vm' for VM ownership",
        }.get(command, "use the VM-HA-specific workflow")
        raise typer.BadParameter(
            f"'{command}' is not supported for explicit VM HA; {alternative}."
        )
    if applicability == "vm-ha" and not is_vm_ha:
        raise typer.BadParameter(
            f"'{command}' requires an explicit gateway_group.vm_ha configuration."
        )
    if applicability == "ordinary-bgp" and modes == {"static"}:
        raise typer.BadParameter(f"'{command}' is supported only for BGP connections.")

    if command != "add-routes-local":
        return
    if yes and not swap_route_table:
        raise typer.BadParameter("'--yes' is valid only together with '--swap-route-table'.")
    if not is_vm_ha:
        return
    if summarize or swap_route_table or yes:
        raise typer.BadParameter(
            "VM-HA route repair does not accept --summarize, --swap-route-table, or --yes."
        )
    if modes != {"bgp"}:
        raise typer.BadParameter(
            "'add-routes-local' does not mutate controller-owned VM-HA static routes; "
            "use 'nebius-vpngw status' to verify authority and 'nebius-vpngw apply' "
            "to reconcile the installed generation."
        )


def _command_help_epilog(*command_path: str) -> str:
    """Return the canonical examples for one public command path."""
    return _format_help_examples(_COMMAND_EXAMPLES[command_path])


def _vm_ha_route_lifecycle_is_stable(
    config_path: Path,
    plan: ResolvedDeploymentPlan,
    project_id: str | None,
) -> bool:
    """Return whether local lifecycle authority permits an exact route audit/repair."""

    if plan.vm_ha is None:
        return True
    state = VMHALifecycleStore(config_path).read(
        expected_project_id=project_id,
        expected_gateway_name=plan.gateway_group.name,
    )
    return bool(
        state is not None
        and state.status is VMHALifecycleStatus.ACTIVE
        and state.transaction is not None
        and not state.transaction.pending_effect
    )


_HELP_COMMAND_ORDER = list(dict.fromkeys(path[0] for path in _COMMAND_EXAMPLES))
_HELP_SUBCOMMAND_ORDER = {
    group: [path[1] for path in _COMMAND_EXAMPLES if len(path) == 2 and path[0] == group]
    for group in ("failover", "failback")
}


class _WorkflowOrderTyperGroup(TyperGroup):
    """Render root and resource subcommands in canonical workflow order."""

    def list_commands(self, ctx: t.Any) -> list[str]:
        registered = super().list_commands(ctx)
        desired = _HELP_SUBCOMMAND_ORDER.get(self.name or "", _HELP_COMMAND_ORDER)
        order_index = {name: idx for idx, name in enumerate(desired)}
        return sorted(
            registered,
            key=lambda name: (order_index.get(name, len(desired)), registered.index(name)),
        )


app = typer.Typer(
    cls=_WorkflowOrderTyperGroup,
    add_completion=False,
    help="""
Nebius VM-based VPN Gateway orchestrator

Most commands look for 'nebius-vpngw.config.yaml' in your current directory.
Use --local-config-file to select a different config for operational commands.
Use positional file arguments for create-config and validate-config.
Run nebius-vpngw COMMAND --help for command-specific guidance and examples.
""",
    epilog=_format_help_examples(_ROOT_HELP_EXAMPLES),
)

failover_app = typer.Typer(
    cls=_WorkflowOrderTyperGroup,
    add_completion=False,
    no_args_is_help=True,
    help="Fail over VM ownership or a tunnel path.",
    epilog=_command_help_epilog("failover"),
)
failback_app = typer.Typer(
    cls=_WorkflowOrderTyperGroup,
    add_completion=False,
    no_args_is_help=True,
    help="Fail back VM ownership or a tunnel path.",
    epilog=_command_help_epilog("failback"),
)
app.add_typer(failover_app, name="failover")
app.add_typer(failback_app, name="failback")


def _registered_command_name(command_info: t.Any) -> str:
    """Resolve the CLI command name Typer will show in help output."""
    if command_info.name:
        return command_info.name

    callback = getattr(command_info, "callback", None)
    if callback is None:
        return ""

    return callback.__name__.replace("_", "-")


def _apply_help_command_order() -> None:
    """Sort registered commands to keep help output in a stable workflow order."""
    for command_app, desired in (
        (app, _HELP_COMMAND_ORDER),
        (failover_app, _HELP_SUBCOMMAND_ORDER["failover"]),
        (failback_app, _HELP_SUBCOMMAND_ORDER["failback"]),
    ):
        order_index = {name: idx for idx, name in enumerate(desired)}
        indexed_commands = list(enumerate(command_app.registered_commands))
        indexed_commands.sort(
            key=lambda item: (
                order_index.get(_registered_command_name(item[1]), len(desired)),
                item[0],
            )
        )
        command_app.registered_commands[:] = [command for _, command in indexed_commands]


def _version_callback(value: bool) -> bool:
    if value:
        print(f"nebius-vpngw {__version__}")
        raise typer.Exit()
    return value


def _is_windows() -> bool:
    return os.name == "nt" or platform.system().lower() == "windows"


def _ensure_ssh_available() -> None:
    if shutil.which("ssh"):
        return
    if _is_windows():
        print("[red]OpenSSH client not found in PATH.[/red]")
        print(
            "[yellow]Install it via Settings > Apps > Optional features > OpenSSH Client, "
            "or run: Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0[/yellow]"
        )
        print("[yellow]Alternatively, run the CLI from WSL.[/yellow]")
    else:
        print("[red]ssh client not found in PATH.[/red]")
        print(
            "[yellow]Install OpenSSH client (e.g., apt-get install openssh-client or brew install openssh).[/yellow]"
        )
    raise typer.Exit(code=1)


def _build_ssh_base_cmd(
    key_path: Path | None,
    *,
    ssh_policy: SSHTrustPolicy | None = None,
    hostname: str | None = None,
) -> list[str]:
    _ensure_ssh_available()
    return build_openssh_base_command(
        key_path=key_path,
        policy=ssh_policy,
        hostname=hostname,
    )


class _VMHAStatusSSHUnavailable(RuntimeError):
    """Exact SSH trust is unavailable for one VM-HA status target."""


@dataclass(frozen=True)
class _StatusSSHContext:
    username: str
    key_path: Path | None
    policies: t.Mapping[str, SSHTrustPolicy | None]
    unavailable_members: frozenset[str]
    vm_ha: bool


def _vm_ha_ssh_trust_scope(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None = None,
    cluster_id: str | None = None,
) -> VMHASSHTrustScope:
    """Bind managed SSH trust to one exact VM-HA deployment identity."""

    vm_ha = getattr(plan, "vm_ha", None)
    gateway_group = getattr(plan, "gateway_group", None)
    vm_ha_cluster = cluster_id or str(getattr(vm_ha, "cluster_id", "") or "").strip()
    return VMHASSHTrustScope(
        tenant_id=str(local_cfg.get("tenant_id") or "").strip(),
        project_id=str(project_id or local_cfg.get("project_id") or "").strip(),
        region_id=str(
            local_cfg.get("region_id") or getattr(gateway_group, "region", "") or ""
        ).strip(),
        gateway_name=str(getattr(gateway_group, "name", "") or "").strip(),
        cluster_id=vm_ha_cluster,
    )


def _build_status_ssh_context(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    vm_ips: t.Mapping[str, str],
    *,
    project_id: str | None = None,
) -> _StatusSSHContext:
    """Build one config-aware, fail-closed SSH context for status probes."""

    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    if plan.vm_ha is None:
        return _StatusSSHContext(
            username=str(username),
            key_path=key_path,
            policies=MappingProxyType({}),
            unavailable_members=frozenset(),
            vm_ha=False,
        )

    policies: dict[str, SSHTrustPolicy | None] = {}
    unavailable: set[str] = set()
    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue
        try:
            configured_address = str(getattr(inst_cfg, "external_ip", "") or "").strip()
            policy_options: dict[str, t.Any] = {}
            if configured_address and configured_address not in {inst_cfg.hostname, target}:
                policy_options["additional_aliases"] = {inst_cfg.hostname: (configured_address,)}
            policies[inst_cfg.hostname] = require_vm_ha_ssh_policy(
                ((inst_cfg.hostname, target),),
                enrollment_hosts=set(),
                trust_scope=_vm_ha_ssh_trust_scope(local_cfg, plan, project_id=project_id),
                **policy_options,
            )
        except (OSError, RuntimeError, ValueError):
            policies[inst_cfg.hostname] = None
            unavailable.add(inst_cfg.hostname)
    return _StatusSSHContext(
        username=str(username),
        key_path=key_path,
        policies=MappingProxyType(policies),
        unavailable_members=frozenset(unavailable),
        vm_ha=True,
    )


def _build_route_ssh_policy(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None = None,
) -> SSHTrustPolicy | None:
    """Freeze exact per-member SSH pins before a VM-HA route operation."""

    if plan.vm_ha is None:
        return None
    pin_targets: list[tuple[str, str]] = []
    for inst_cfg in plan.iter_instance_configs():
        target = str(inst_cfg.external_ip or "").strip()
        if not target:
            raise RouteManagementError(
                "VM-HA route operations require an external management IP for every member."
            )
        pin_targets.append((inst_cfg.hostname, target))
    try:
        return require_vm_ha_ssh_policy(
            tuple(pin_targets),
            enrollment_hosts=(),
            trust_scope=_vm_ha_ssh_trust_scope(local_cfg, plan, project_id=project_id),
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise RouteManagementError(
            "VM-HA route operations require exact pinned SSH trust for every member. "
            "Run apply with authoritative host-key evidence, or configure "
            "VPNGW_SSH_KNOWN_HOSTS_FILE with exact member pins, then retry."
        ) from error


def _status_ssh_target_command(
    context: _StatusSSHContext,
    *,
    hostname: str,
    target: str,
) -> list[str]:
    """Return the strict SSH prefix for one status target."""

    policy = context.policies.get(hostname)
    if context.vm_ha and policy is None:
        raise _VMHAStatusSSHUnavailable("exact SSH trust is unavailable for this VM-HA member")
    return _build_ssh_base_cmd(
        context.key_path,
        ssh_policy=policy,
        hostname=hostname if policy is not None else None,
    ) + [f"{context.username}@{target}"]


def _normalize_role_value(value: t.Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    value = str(value or "").strip().lower()
    return value or "unknown"


def _select_carrying_tunnel_for_connection(
    hostname: str,
    connection_name: str | None,
    tunnel_names: list[str],
    tunnel_statuses: dict[str, str],
    bgp_states: dict[str, str],
    tunnel_bgp_map: dict[str, dict[str, str]],
    tunnel_role_map: dict[str, dict[str, str]],
    tunnel_connection_map: dict[str, dict[str, str]],
) -> str | None:
    established: list[str] = []

    def _belongs_to_connection(tunnel_name: str) -> bool:
        if not connection_name:
            return True
        mapped_name = tunnel_connection_map.get(hostname, {}).get(tunnel_name)
        return mapped_name == connection_name

    if bgp_states:
        for name in tunnel_names:
            if not _belongs_to_connection(name):
                continue
            peer_ip = tunnel_bgp_map.get(hostname, {}).get(name)
            if not peer_ip:
                continue
            state = str(bgp_states.get(peer_ip, "")).strip().lower()
            if state == "established":
                established.append(name)
    if not established:
        for name in tunnel_names:
            if not _belongs_to_connection(name):
                continue
            if str(tunnel_statuses.get(name, "")).upper() == "ESTABLISHED":
                established.append(name)
    if len(established) == 1:
        return established[0]
    if len(established) > 1:
        for name in established:
            role_value = _normalize_role_value(tunnel_role_map.get(hostname, {}).get(name))
            if role_value == "active":
                return name
        return established[0]
    return None


def _bgp_state_for_tunnel(
    hostname: str,
    tunnel_name: str,
    bgp_states: dict[str, str],
    tunnel_bgp_map: dict[str, dict[str, str]],
) -> str:
    peer_ip = tunnel_bgp_map.get(hostname, {}).get(tunnel_name)
    if not peer_ip:
        return ""
    return str(bgp_states.get(peer_ip, "")).strip()


def _ipsec_status_reports_no_active_tunnels(output: str) -> bool:
    """Recognize a valid strongSwan status with zero active SAs."""

    normalized = output.lower()
    return bool(
        "no matching" in normalized
        or "no active" in normalized
        or (
            "security associations (0 up" in normalized
            and re.search(r"(?m)^\s*none\s*$", normalized)
        )
    )


def _detect_connection_role_overrides(
    hostname: str,
    tunnel_names: list[str],
    tunnel_statuses: dict[str, str],
    bgp_states: dict[str, str],
    tunnel_bgp_map: dict[str, dict[str, str]],
    tunnel_role_map: dict[str, dict[str, str]],
    tunnel_connection_map: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    overrides: list[dict[str, str]] = []
    connection_names = sorted(
        {
            connection_name
            for tunnel_name in tunnel_names
            if (connection_name := tunnel_connection_map.get(hostname, {}).get(tunnel_name))
        }
    )

    for connection_name in connection_names:
        carrying_tunnel = _select_carrying_tunnel_for_connection(
            hostname,
            connection_name,
            tunnel_names,
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
            tunnel_role_map,
            tunnel_connection_map,
        )
        if not carrying_tunnel:
            continue

        configured_active = next(
            (
                tunnel_name
                for tunnel_name in tunnel_names
                if tunnel_connection_map.get(hostname, {}).get(tunnel_name) == connection_name
                and _normalize_role_value(tunnel_role_map.get(hostname, {}).get(tunnel_name))
                == "active"
            ),
            None,
        )
        if not configured_active or carrying_tunnel == configured_active:
            continue

        active_bgp_state = _bgp_state_for_tunnel(
            hostname, configured_active, bgp_states, tunnel_bgp_map
        )
        active_ipsec_state = str(tunnel_statuses.get(configured_active, "")).strip().upper()

        if "admin" in active_bgp_state.lower():
            reason = "manual failover"
            detail = "configured active tunnel BGP is administratively down"
        elif active_bgp_state and active_bgp_state.lower() != "established":
            reason = "failover active"
            detail = f"configured active tunnel BGP is {active_bgp_state}"
        elif active_ipsec_state and active_ipsec_state != "ESTABLISHED":
            reason = "failover active"
            detail = f"configured active tunnel IPsec is {active_ipsec_state.lower()}"
        else:
            reason = "runtime override"
            detail = "runtime traffic selection differs from configured preference"

        overrides.append(
            {
                "connection": connection_name,
                "configured_active_tunnel": configured_active,
                "selected_tunnel": carrying_tunnel,
                "reason": reason,
                "detail": detail,
            }
        )

    return overrides


def _format_role_override_lines(
    overrides_by_vm: dict[str, list[dict[str, str]]],
) -> list[str]:
    lines = [
        "Traffic is currently using a tunnel that differs from the configured active/passive preference.",
        "Configured roles remain unchanged by design. Manual failover is an operational override; run 'nebius-vpngw failback tunnel' to restore steady state.",
    ]

    for hostname, overrides in sorted(overrides_by_vm.items()):
        lines.append("")
        lines.append(f"Gateway VM: {hostname}")
        for override in overrides:
            lines.append(f"  Connection: {override['connection']}")
            lines.append(f"    Configured active tunnel: {override['configured_active_tunnel']}")
            lines.append(f"    Current traffic path: {override['selected_tunnel']}")
            lines.append(f"    Reason: {override['reason']} ({override['detail']})")

    return lines


def _detect_cross_connection_ecmp_warnings(
    routes: dict[str, t.Any],
    peer_connection_map: dict[str, str],
    peer_tunnel_map: dict[str, str],
    peer_role_map: dict[str, str],
) -> list[dict[str, t.Any]]:
    warnings: list[dict[str, t.Any]] = []

    for prefix, raw_paths in (routes or {}).items():
        if not isinstance(raw_paths, list):
            continue

        active_entries: list[dict[str, str]] = []
        for path in raw_paths:
            if not isinstance(path, dict) or not path.get("multipath"):
                continue

            peer_ip = str(path.get("peerId") or "").strip()
            if not peer_ip:
                nexthops = path.get("nexthops") or []
                if isinstance(nexthops, list):
                    for nexthop in nexthops:
                        if not isinstance(nexthop, dict):
                            continue
                        candidate_ip = str(nexthop.get("ip") or "").strip()
                        if candidate_ip and candidate_ip in peer_connection_map:
                            peer_ip = candidate_ip
                            break
            if not peer_ip:
                continue

            role = _normalize_role_value(peer_role_map.get(peer_ip))
            if role != "active":
                continue

            connection_name = peer_connection_map.get(peer_ip)
            tunnel_name = peer_tunnel_map.get(peer_ip)
            if not connection_name or not tunnel_name:
                continue

            active_entries.append(
                {
                    "connection": connection_name,
                    "tunnel": tunnel_name,
                    "peer_ip": peer_ip,
                }
            )

        unique_connections = {entry["connection"] for entry in active_entries}
        if len(unique_connections) < 2:
            continue

        warnings.append(
            {
                "prefix": prefix,
                "connections": sorted(unique_connections),
                "entries": sorted(
                    active_entries,
                    key=lambda entry: (entry["connection"], entry["tunnel"], entry["peer_ip"]),
                ),
            }
        )

    return warnings


def _format_ecmp_warning_lines(
    active_ecmp_warnings: dict[str, list[dict[str, t.Any]]],
) -> list[str]:
    warning_lines = [
        "Live BGP multipath is active across different active connections for overlapping prefixes.",
        "Traffic may be hash-split across more than one site-level connection for those prefixes.",
    ]

    for hostname, warnings in sorted(active_ecmp_warnings.items()):
        warning_lines.append("")
        warning_lines.append(f"Gateway VM: {hostname}")
        for warning in warnings:
            warning_lines.append(f"  Overlapping prefix: {warning['prefix']}")
            warning_lines.append("  Active tunnels carrying this prefix:")
            for entry in warning["entries"]:
                warning_lines.append(f"    - {entry['tunnel']} (connection: {entry['connection']})")

    return warning_lines


def _build_remote_tunnel_restart_script() -> str:
    """Return a self-contained remote Python helper for tunnel restart.

    The restart command must not rely on the version of nebius-vpngw currently
    installed on the gateway VM. This inline helper is executed over SSH so the
    local CLI always uses the latest restart logic.
    """
    return (
        textwrap.dedent(
            """
        from __future__ import annotations

        import argparse
        import re
        import shutil
        import subprocess
        import sys
        import time
        from pathlib import Path

        CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")


        def _command_output(result: subprocess.CompletedProcess[str]) -> str:
            return (result.stderr or result.stdout or "").strip()


        def _run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )


        def _get_ipsec_tunnel_status(tunnel_name: str) -> str:
            if shutil.which("swanctl"):
                try:
                    result = _run(["swanctl", "--list-sas"], timeout=5)
                except subprocess.TimeoutExpired:
                    return "UNKNOWN"
                if result.returncode == 0 and result.stdout:
                    for line in result.stdout.splitlines():
                        if tunnel_name in line and "ESTABLISHED" in line.upper():
                            return "ESTABLISHED"
                        if tunnel_name in line and "CONNECTING" in line.upper():
                            return "CONNECTING"
                return "DOWN"

            try:
                result = _run(["ipsec", "status", tunnel_name], timeout=5)
            except subprocess.TimeoutExpired:
                return "UNKNOWN"

            if result.returncode != 0:
                return "DOWN"

            output = result.stdout.lower()
            if "established" in output:
                return "ESTABLISHED"
            if "connecting" in output or "negotiating" in output:
                return "CONNECTING"
            return "DOWN"


        def _wait_for_established(tunnel_name: str, timeout_seconds: int = 12) -> bool:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if _get_ipsec_tunnel_status(tunnel_name) == "ESTABLISHED":
                    return True
                time.sleep(1)
            return _get_ipsec_tunnel_status(tunnel_name) == "ESTABLISHED"


        def _list_configured_tunnels() -> list[str]:
            names: list[str] = []

            try:
                import yaml

                if CONFIG_PATH.exists():
                    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
                    idx = 0
                    for conn in cfg.get("connections") or []:
                        for tun in conn.get("tunnels") or []:
                            if str(tun.get("ha_role", "active")).lower() == "disable":
                                continue
                            names.append(str(tun.get("name") or f"tunnel{idx}"))
                            idx += 1
            except Exception:
                pass

            if names:
                return names

            if shutil.which("swanctl"):
                try:
                    result = _run(["swanctl", "--list-sas"], timeout=5)
                    if result.returncode == 0 and result.stdout:
                        for line in result.stdout.splitlines():
                            match = re.match(r"^([^:\\s]+):\\s+#\\d+,", line.strip())
                            if match:
                                name = match.group(1)
                                if name not in names:
                                    names.append(name)
                except Exception:
                    pass

            return names


        def _restart_tunnel(tunnel_name: str) -> bool:
            print(f"[TunnelMonitor] Restarting tunnel: {tunnel_name}")

            try:
                if shutil.which("swanctl"):
                    try:
                        load_result = _run(["swanctl", "--load-all"], timeout=15)
                        if load_result.returncode != 0:
                            print(
                                f"[TunnelMonitor] Warning: failed to reload swanctl config for {tunnel_name}: "
                                f"{_command_output(load_result)}"
                            )
                    except subprocess.TimeoutExpired:
                        print(
                            f"[TunnelMonitor] Warning: timeout reloading swanctl config for {tunnel_name}; proceeding"
                        )

                    terminated = False
                    terminate_attempts = [
                        (["swanctl", "--terminate", "--child", tunnel_name, "--timeout", "5"], "CHILD_SA"),
                        (["swanctl", "--terminate", "--ike", tunnel_name, "--timeout", "5"], "IKE_SA"),
                    ]
                    for terminate_cmd, label in terminate_attempts:
                        try:
                            result = _run(terminate_cmd, timeout=10)
                        except subprocess.TimeoutExpired:
                            print(
                                f"[TunnelMonitor] Warning: timeout terminating {label} for {tunnel_name}"
                            )
                            continue

                        if result.returncode == 0:
                            terminated = True
                            break

                        output = _command_output(result)
                        if output:
                            print(
                                f"[TunnelMonitor] Warning: failed to terminate {label} for {tunnel_name}: {output}"
                            )

                    if not terminated:
                        print(
                            "[TunnelMonitor] Warning: proceeding with initiate even though termination did not confirm"
                        )

                    time.sleep(2)

                    for attempt in range(1, 4):
                        try:
                            result = _run(
                                ["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"],
                                timeout=25,
                            )
                        except subprocess.TimeoutExpired:
                            result = subprocess.CompletedProcess(
                                args=["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"],
                                returncode=124,
                                stdout="",
                                stderr="timeout while initiating child SA",
                            )

                        if result.returncode == 0 and _wait_for_established(tunnel_name):
                            print(f"[TunnelMonitor] Successfully restarted tunnel: {tunnel_name}")
                            return True

                        if _wait_for_established(tunnel_name, timeout_seconds=4):
                            print(
                                f"[TunnelMonitor] Tunnel {tunnel_name} recovered after initiate attempt {attempt}"
                            )
                            return True

                        output = _command_output(result)
                        if output:
                            print(
                                f"[TunnelMonitor] Warning: failed to initiate {tunnel_name} "
                                f"(attempt {attempt}/3): {output}"
                            )
                        if attempt < 3:
                            print(
                                f"[TunnelMonitor] Warning: retrying tunnel initiate for {tunnel_name} in 3s"
                            )
                            time.sleep(3)

                    print(f"[TunnelMonitor] Failed to restart tunnel {tunnel_name}")
                    return False

                try:
                    result = _run(["ipsec", "down", tunnel_name], timeout=10)
                    if result.returncode != 0:
                        print(
                            f"[TunnelMonitor] Warning: failed to bring down {tunnel_name}: "
                            f"{_command_output(result)}"
                        )
                        print("[TunnelMonitor] Warning: proceeding with tunnel up attempt anyway")
                except subprocess.TimeoutExpired:
                    print(f"[TunnelMonitor] Warning: timeout bringing down {tunnel_name}")

                time.sleep(2)

                try:
                    result = _run(["ipsec", "up", tunnel_name], timeout=20)
                except subprocess.TimeoutExpired:
                    print(f"[TunnelMonitor] Failed to bring up {tunnel_name}: timeout")
                    return False

                if result.returncode != 0:
                    print(
                        f"[TunnelMonitor] Failed to bring up {tunnel_name}: {_command_output(result)}"
                    )
                    return False

                print(f"[TunnelMonitor] Successfully restarted tunnel: {tunnel_name}")
                return True
            except Exception as exc:
                print(f"[TunnelMonitor] Failed to restart tunnel {tunnel_name}: {exc}")
                return False


        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--restart-tunnel", required=True)
            args = parser.parse_args()

            if args.restart_tunnel.lower() == "all":
                tunnels = _list_configured_tunnels()
                if not tunnels:
                    print("[TunnelMonitor] No configured tunnels found")
                    return 1
                success_count = 0
                for tunnel_name in tunnels:
                    if _restart_tunnel(tunnel_name):
                        success_count += 1
                print(f"[TunnelMonitor] Restarted {success_count}/{len(tunnels)} tunnels")
                return 0 if success_count == len(tunnels) else 1

            return 0 if _restart_tunnel(args.restart_tunnel) else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        """
        ).strip()
        + "\n"
    )


def _normalize_config_value(value: t.Any, fallback: str = "") -> str:
    """Normalize config scalars that may be plain strings or enum values."""
    resolved = fallback if value is None else getattr(value, "value", value)
    return str(resolved or "").strip().lower()


def _create_config_from_template(output_path: Path) -> None:
    """Write the embedded config template to user's directory."""
    try:
        output_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    except Exception as e:
        print(f"[red]Failed to write config template:[/red] {e}")
        raise typer.Exit(code=1) from e


def _ensure_authentication(
    *,
    required: bool = True,
    timeout_seconds: int = 60,
    show_progress: bool = True,
) -> str | None:
    """Centralized authentication helper for all commands.

    Args:
        required: If True, exit with error if authentication fails. If False, continue with warning.
        timeout_seconds: Maximum time to wait for authentication (default: 60s)
        show_progress: If True, show authentication status messages

    Returns:
        Token string if successful, None if failed (only when required=False)

    Raises:
        typer.Exit: If authentication fails and required=True
    """
    # Skip if token already exists in environment
    if os.environ.get("NEBIUS_IAM_TOKEN"):
        return os.environ["NEBIUS_IAM_TOKEN"]

    try:
        from .vpngw_sa import ensure_cli_access_token

        if show_progress:
            print("[bold]Authenticating with Nebius...[/bold]")

        tok = ensure_cli_access_token(timeout_seconds=timeout_seconds)

        if tok:
            os.environ["NEBIUS_IAM_TOKEN"] = tok
            if show_progress:
                print("[green]✓ Authentication successful[/green]")
            return tok
        else:
            # Authentication failed or timed out
            if required:
                print("[red]✗ Authentication failed or timed out[/red]")
                print("[yellow]Please ensure you're logged in: nebius auth login[/yellow]")
                raise typer.Exit(code=1)
            else:
                if show_progress:
                    print("[yellow]⚠️  Authentication failed - continuing without token[/yellow]")
                return None

    except typer.Exit:
        raise
    except Exception as e:
        # Unexpected error during authentication
        if required:
            print(f"[red]✗ Authentication error: {e}[/red]")
            print("[yellow]Please ensure you're logged in: nebius auth login[/yellow]")
            raise typer.Exit(code=1) from e
        else:
            if show_progress:
                print(f"[yellow]⚠️  Authentication error: {e}[/yellow]")
            return None


def _should_prompt_add_routes_after_apply(
    plan: ResolvedDeploymentPlan,
    changes: list[tuple[str, t.Any]],
    *,
    recreate_gw: bool,
) -> bool:
    """Return True when apply should remind the user to create local static routes."""
    if recreate_gw or not plan.should_manage_routes or not changes:
        return False

    return all(
        getattr(diff, "differences", None) == ["VM does not exist (will create)"]
        for _, diff in changes
    )


def _vm_ready_for_config_push(health: dict[str, t.Any]) -> bool:
    """Return True when it is safe to SSH-push gateway config."""
    return bool(
        health.get("reachable")
        and health.get("cloud_init_complete")
        and health.get("esp4_ready", True)
        and not health.get("esp4_reboot_pending", False)
    )


def _vm_packages_verified(health: dict[str, t.Any]) -> bool:
    """Return True when bootstrap packages were verified on the VM."""
    return bool(health.get("strongswan_installed") and health.get("frr_installed"))


def _vm_ha_apply_order(plan: ResolvedDeploymentPlan) -> list[t.Any]:
    """Return the only safe node order: configured passive, then configured active."""

    instances = list(plan.iter_instance_configs())
    if plan.vm_ha is None:
        return instances
    if any(instance.vm_ha_node is None for instance in instances):
        raise ValueError("VM-HA deployment plan contains an incomplete node manifest")

    def role_value(instance: t.Any) -> str:
        node = instance.vm_ha_node
        if node is None:
            raise ValueError("VM-HA deployment plan contains an incomplete node manifest")
        return str(node.role.value)

    ordered = sorted(
        instances,
        key=lambda instance: 0 if role_value(instance) == "passive" else 1,
    )
    if [role_value(instance) for instance in ordered] != ["passive", "active"]:
        raise ValueError("VM-HA apply requires exactly one passive and one active node")
    return ordered


def _vm_ha_bound_owner_node_id(
    runtime_binding: t.Any,
    lifecycle_state: VMHALifecycleState,
) -> str:
    """Resolve the exact current shared-alias owner from durable cloud bindings."""

    transaction = lifecycle_state.transaction
    if transaction is None:
        raise RuntimeError("VM-HA activation has no durable owner binding")
    bindings = vm_ha_effective_resource_bindings(dict(transaction.resource_bindings))
    owner_compute = bindings.get("shared-allocation-owner-compute")
    owner_nic = bindings.get("shared-allocation-owner-nic")
    if not owner_compute or not owner_nic:
        raise RuntimeError("VM-HA activation owner binding is incomplete")
    matches = [
        node
        for node in runtime_binding.nodes
        if node.compute_id == owner_compute and node.network_interface_name == owner_nic
    ]
    if len(matches) != 1:
        raise RuntimeError("VM-HA activation owner binding is not an exact member")
    return str(matches[0].node_id)


def _vm_ha_apply_order_for_owner(
    plan: ResolvedDeploymentPlan,
    owner_node_id: str,
) -> list[t.Any]:
    """Stage and activate the current non-owner before the exact owner."""

    instances = _vm_ha_apply_order(plan)
    matches = [
        instance
        for instance in instances
        if instance.vm_ha_node is not None and instance.vm_ha_node.node_id == owner_node_id
    ]
    if len(matches) != 1:
        raise RuntimeError("VM-HA apply owner is not an exact deployment member")
    return sorted(
        instances,
        key=lambda instance: (
            instance.vm_ha_node is not None and instance.vm_ha_node.node_id == owner_node_id
        ),
    )


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _vm_ha_desired_approval_state(plan: ResolvedDeploymentPlan) -> dict[str, object]:
    """Return every desired identity and policy field covered by approval."""

    vm_ha = plan.vm_ha
    if vm_ha is None:
        raise ValueError("VM-HA migration approval requires an explicit HA plan")
    generation = vm_ha.generation
    gateway = plan.gateway_group
    return {
        "active_instance_index": next(
            member.instance_index for member in vm_ha.members if member.role.value == "active"
        ),
        "cluster_id": vm_ha.cluster_id,
        "gateway_name": gateway.name,
        "generation": {
            "bgp_policy_digest": generation.digests.bgp_policy,
            "configuration_digest": generation.digests.configuration,
            "generation_id": generation.generation_id,
            "static_routes_digest": generation.digests.static_routes,
        },
        "members": [
            {
                "instance_index": member.instance_index,
                "instance_name": f"{gateway.name}-{member.instance_index}",
                "node_id": member.node_id,
                "role": member.role.value,
            }
            for member in vm_ha.members
        ],
        "mutations": [
            "retain-active-identities",
            "provision-passive-identities",
            "create-or-approved-reuse-shared-private-allocation",
            "attach-shared-alias-to-configured-active",
            "stage-and-activate-passive-then-active",
            "reconcile-exact-managed-routes",
        ],
        "resource_names": {
            "shared_allocation": (f"{gateway.name}-{vm_ha.cluster_id}-shared-private-ip"),
            "members": [f"{gateway.name}-{member.instance_index}" for member in vm_ha.members],
        },
        "rollback_intent": {
            "before_cutover": "preserve-current-serving-owner-and-routes",
            "route_create_failure": "restore-exact-original-only-after-desired-absence",
            "unsafe_identity_drift": "block-without-adoption",
        },
    }


def _vm_ha_migration_approval_document(
    plan: ResolvedDeploymentPlan,
    current_state: t.Mapping[str, object],
    *,
    approval_kind: str,
) -> dict[str, object]:
    if approval_kind not in {"migration", "recovery"}:
        raise ValueError("VM-HA approval kind is invalid")
    return {
        "current_state": dict(current_state),
        "desired_state": _vm_ha_desired_approval_state(plan),
        "domain": f"nebius-vpngw/vm-ha-{approval_kind}-approval-v1",
        "schema": "nebius-vpngw/vm-ha-approval-document-v1",
    }


def _vm_ha_migration_plan_digest(
    plan: ResolvedDeploymentPlan,
    current_state: t.Mapping[str, object],
    *,
    approval_kind: str,
) -> str:
    """Bind approval to desired intent and authoritative current cloud state."""

    return _canonical_digest(
        _vm_ha_migration_approval_document(
            plan,
            current_state,
            approval_kind=approval_kind,
        )
    )


def _vm_ha_initial_resource_bindings(
    observation: t.Mapping[str, object],
) -> dict[str, str]:
    bindings: dict[str, str] = {}
    raw_members = observation.get("members", [])
    if not isinstance(raw_members, list):
        raise ValueError("VM-HA authoritative member observation is malformed")
    for raw_member in raw_members:
        if not isinstance(raw_member, dict) or not raw_member.get("present"):
            continue
        name = str(raw_member.get("instance_name") or "")
        nic = str(raw_member.get("network_interface_name") or "")
        for source, key in (
            ("compute_id", f"compute:{name}"),
            ("boot_disk_id", f"disk:{name}"),
            ("primary_allocation_id", f"primary-allocation:{name}:{nic}"),
            ("public_allocation_id", f"public-allocation:{name}:{nic}"),
        ):
            value = str(raw_member.get(source) or "")
            if value:
                bindings[key] = value
    shared = observation.get("shared_allocation")
    if isinstance(shared, dict) and shared.get("present"):
        allocation_id = str(shared.get("allocation_id") or "")
        if allocation_id:
            bindings["shared-allocation-id"] = allocation_id
        owner = shared.get("owner")
        if isinstance(owner, dict):
            compute_id = str(owner.get("compute_id") or "")
            nic = str(owner.get("network_interface_name") or "")
            if compute_id and nic:
                bindings["shared-allocation-owner-compute"] = compute_id
                bindings["shared-allocation-owner-nic"] = nic
    bindings["route-targets-digest"] = _canonical_digest(observation.get("route_targets", []))
    return bindings


def _vm_ha_observation_matches_bindings(
    observation: t.Mapping[str, object],
    expected: t.Mapping[str, str],
) -> bool:
    current = _vm_ha_initial_resource_bindings(observation)
    expected = vm_ha_effective_resource_bindings(expected)
    return all(
        vm_ha_resource_binding_matches_observation(
            key,
            value,
            observed=current,
            expected=expected,
        )
        for key, value in expected.items()
    )


def _vm_ha_failed_passive_replacement_plan(
    plan: ResolvedDeploymentPlan,
    lifecycle_state: VMHALifecycleState,
    observation: t.Mapping[str, object],
) -> tuple[str, str]:
    """Return the passive name and exact replacement approval digest."""

    transaction = lifecycle_state.transaction
    if (
        plan.vm_ha is None
        or lifecycle_state.record_version != 4
        or lifecycle_state.status is not VMHALifecycleStatus.PROVISIONING
        or transaction is None
    ):
        raise ValueError("No failed PROVISIONING passive is eligible for replacement")
    passive = next(
        (member for member in lifecycle_state.members if member.role == "passive"),
        None,
    )
    if passive is None:
        raise ValueError("VM-HA lifecycle has no configured passive")
    bindings = dict(transaction.resource_bindings)
    passive_name = passive.instance_name
    cycles = vm_ha_passive_replacement_cycles(bindings, passive_name)
    latest_cycle = cycles[-1] if cycles else None
    if latest_cycle is not None and (
        transaction.pending_effect is not None
        or vm_ha_passive_replacement_effect(
            passive_name,
            latest_cycle,
            "create-compute",
        )
        not in transaction.completed_effects
    ):
        persisted = bindings.get(
            vm_ha_passive_replacement_binding_key(
                "approval",
                passive_name,
                latest_cycle,
            )
        )
        if not persisted:
            raise ValueError("VM-HA passive replacement approval history is incomplete")
        return passive_name, persisted
    replacement_cycle = 1 if latest_cycle is None else latest_cycle + 1
    effective_bindings = vm_ha_effective_resource_bindings(bindings)
    compute_id = effective_bindings.get(f"compute:{passive_name}")
    disk_id = effective_bindings.get(f"disk:{passive_name}")
    if not compute_id or not disk_id:
        raise ValueError("VM-HA passive replacement lacks exact transaction-created identities")
    actions = (
        f"replacement-cycle:{replacement_cycle}",
        f"delete-compute:{passive_name}:{compute_id}",
        f"delete-boot-disk:{passive_name}:{disk_id}",
        f"retain-primary-allocation:{bindings.get(f'primary-allocation:{passive_name}:eth0', '')}",
        f"retain-public-allocation:{bindings.get(f'public-allocation:{passive_name}:eth0', '')}",
        "retain-active-compute-and-disk",
        "retain-shared-allocation-owner-and-routes",
        "create-passive-boot-disk-and-compute",
    )
    if any(action.endswith(":") for action in actions):
        raise ValueError("VM-HA passive replacement lacks retained allocation identities")
    digest = _canonical_digest(
        {
            "actions": actions,
            "current_observation": dict(observation),
            "domain": "nebius-vpngw/failed-provisioning-passive-replacement-v1",
            "lifecycle_record_sha256": lifecycle_state.record_sha256,
            "original_approval": {
                "approval_digest": transaction.approval_digest,
                "approval_kind": transaction.approval_kind,
                "current_state_digest": transaction.current_state_digest,
                "desired_state_digest": transaction.desired_state_digest,
                "operation_id": transaction.operation_id,
            },
            "passive_instance_name": passive_name,
            "replacement_cycle": replacement_cycle,
        }
    )
    lifecycle_state.authorize_failed_passive_replacement(
        passive_instance_name=passive_name,
        approval_digest=digest,
        retired_compute_id=compute_id,
        retired_disk_id=disk_id,
        current_observation=observation,
        replacement_cycle=replacement_cycle,
    )
    return passive_name, digest


def _vm_ha_activation_recovery_approval_state(
    plan: ResolvedDeploymentPlan,
    lifecycle_state: VMHALifecycleState,
    observation: t.Mapping[str, object],
) -> dict[str, object]:
    """Validate and bind one configured-active reset of interrupted activation."""

    transaction = lifecycle_state.transaction
    if (
        plan.vm_ha is None
        or lifecycle_state.record_version != 4
        or lifecycle_state.status is not VMHALifecycleStatus.ACTIVATING
        or transaction is None
    ):
        raise ValueError("No interrupted VM-HA activation is eligible for recovery")
    if not vm_ha_activation_effect_is_host_only(transaction.pending_effect):
        raise ValueError("VM-HA activation recovery cannot supersede a cloud effect")
    if transaction.accepted_cloud_operation_id is not None:
        raise ValueError("VM-HA activation recovery has an accepted cloud operation")
    desired_digest = _canonical_digest(_vm_ha_desired_approval_state(plan))
    if transaction.desired_state_digest != desired_digest:
        raise ValueError("VM-HA activation recovery desired state changed")

    recorded = {member.instance_name: member for member in lifecycle_state.members}
    raw_members = observation.get("members")
    if not isinstance(raw_members, list):
        raise ValueError("VM-HA activation recovery member observation is malformed")
    current = {
        str(member.get("instance_name") or ""): member
        for member in raw_members
        if isinstance(member, dict)
    }
    if set(current) != set(recorded) or len(current) != 2:
        raise ValueError("VM-HA activation recovery member set changed")
    for name, old_member in recorded.items():
        member = current[name]
        if member.get("present") is not True:
            raise ValueError("VM-HA activation recovery requires both exact members")
        observed_identity = {
            "compute_id": str(member.get("compute_id") or ""),
            "disk_id": str(member.get("boot_disk_id") or ""),
            "network_interface_name": str(member.get("network_interface_name") or ""),
            "network_interface_subnet_id": str(member.get("subnet_id") or ""),
            "primary_allocation_id": str(member.get("primary_allocation_id") or ""),
            "public_allocation_id": str(member.get("public_allocation_id") or ""),
            "public_ip": str(member.get("public_ip") or ""),
        }
        recorded_identity = {
            "compute_id": old_member.compute_id,
            "disk_id": old_member.disk_id,
            "network_interface_name": old_member.network_interface_name,
            "network_interface_subnet_id": old_member.network_interface_subnet_id,
            "primary_allocation_id": old_member.primary_allocation_id,
            "public_allocation_id": old_member.public_allocation_id,
            "public_ip": old_member.public_ip,
        }
        if observed_identity != recorded_identity:
            raise ValueError("VM-HA activation recovery member identity changed")

    active = next(member for member in lifecycle_state.members if member.role == "active")
    passive = next(member for member in lifecycle_state.members if member.role == "passive")
    if current[active.instance_name].get("aliases") != [lifecycle_state.allocation_id]:
        raise ValueError("VM-HA activation recovery configured-active alias is not exact")
    if current[passive.instance_name].get("aliases") != []:
        raise ValueError("VM-HA activation recovery passive still carries the shared alias")

    shared = observation.get("shared_allocation")
    if not isinstance(shared, dict) or shared.get("present") is not True:
        raise ValueError("VM-HA activation recovery shared allocation is missing")
    owner = shared.get("owner")
    if (
        str(shared.get("allocation_id") or "") != lifecycle_state.allocation_id
        or not isinstance(owner, dict)
        or str(owner.get("compute_id") or "") != active.compute_id
        or str(owner.get("network_interface_name") or "") != active.network_interface_name
    ):
        raise ValueError(
            "VM-HA activation recovery requires the exact configured-active cloud owner"
        )

    old_bindings = vm_ha_effective_resource_bindings(dict(transaction.resource_bindings))
    if (
        old_bindings.get("shared-allocation-owner-compute") != passive.compute_id
        or old_bindings.get("shared-allocation-owner-nic") != passive.network_interface_name
    ):
        raise ValueError("VM-HA activation recovery predecessor is not the promoted passive")
    new_bindings = _vm_ha_initial_resource_bindings(observation)
    immutable_keys = {
        key
        for key in old_bindings
        if key == "shared-allocation-id"
        or key == "route-targets-digest"
        or key.startswith(
            (
                "compute:",
                "disk:",
                "primary-allocation:",
                "public-allocation:",
            )
        )
    }
    if any(new_bindings.get(key) != old_bindings[key] for key in immutable_keys):
        raise ValueError("VM-HA activation recovery resource identity changed")

    return {
        "current_observation": dict(observation),
        "lifecycle_record_sha256": lifecycle_state.record_sha256,
        "recovery_mode": "configured-active-fenced-reset",
    }


def _vm_ha_provisioning_members(
    plan: ResolvedDeploymentPlan,
    observation: t.Mapping[str, object],
) -> tuple[VMHALifecycleMember, VMHALifecycleMember]:
    raw_members = observation.get("members", [])
    if not isinstance(raw_members, list):
        raise ValueError("VM-HA authoritative member observation is malformed")
    observed = {
        str(item.get("instance_name")): item for item in raw_members if isinstance(item, dict)
    }
    members: list[VMHALifecycleMember] = []
    assert plan.vm_ha is not None
    for member in sorted(plan.vm_ha.members, key=lambda item: item.instance_index):
        name = f"{plan.gateway_group.name}-{member.instance_index}"
        current = observed.get(name, {})
        members.append(
            VMHALifecycleMember(
                instance_index=member.instance_index,
                instance_name=name,
                node_id=member.node_id,
                role=member.role.value,
                compute_id=str(current.get("compute_id") or ""),
                network_interface_name=str(current.get("network_interface_name") or ""),
                public_ip=str(current.get("public_ip") or ""),
                compute_revision=str(current.get("compute_revision") or ""),
                disk_id=str(current.get("boot_disk_id") or ""),
                network_interface_subnet_id=str(current.get("subnet_id") or ""),
                primary_allocation_id=str(current.get("primary_allocation_id") or ""),
                public_allocation_id=str(current.get("public_allocation_id") or ""),
                alias_allocation_ids=tuple(
                    sorted(str(value) for value in current.get("aliases", []) or [])
                ),
            )
        )
    return t.cast(tuple[VMHALifecycleMember, VMHALifecycleMember], tuple(members))


def _vm_ha_apply_operation_id(runtime_binding: t.Any) -> str:
    """Derive one replay-stable operation identity from authoritative runtime IDs."""

    payload = {
        "allocation_id": runtime_binding.shared_allocation_id,
        "bgp_policy_digest": runtime_binding.bgp_policy_digest,
        "cluster_id": runtime_binding.cluster_id,
        "configuration_digest": runtime_binding.configuration_digest,
        "generation_id": runtime_binding.generation_id,
        "nodes": [
            {
                "compute_id": node.compute_id,
                "network_interface_name": node.network_interface_name,
                "node_id": node.node_id,
                "role": node.role.value,
            }
            for node in sorted(runtime_binding.nodes, key=lambda item: item.node_id)
        ],
        "route_runtime_id": runtime_binding.route_runtime_id,
        "static_routes_digest": runtime_binding.static_routes_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class _VMHAMTLSApplyTransaction:
    operation_id: str | None
    operation_kind: str | None
    nodes: tuple[tuple[t.Any, str, dict[str, object]], ...]

    @property
    def changed(self) -> bool:
        return self.operation_id is not None


def _vm_ha_mtls_operation_id(runtime_binding: t.Any) -> str:
    return _canonical_digest(
        {
            "domain": "nebius-vpngw/vm-ha-mtls-apply-v1",
            "apply_operation_id": _vm_ha_apply_operation_id(runtime_binding),
        }
    )


def _vm_ha_mtls_action_result(response: object) -> object:
    if not isinstance(response, dict) or set(response) != {"schema", "action", "result"}:
        raise RuntimeError("managed mTLS action returned invalid evidence")
    return response["result"]


def _vm_ha_mtls_exact_identity(
    status: dict[str, object], binding_node: t.Any, cluster_id: str
) -> bool:
    return bool(
        status.get("cluster_id") == cluster_id
        and status.get("node_id") == binding_node.node_id
        and status.get("compute_id") == binding_node.compute_id
        and isinstance(status.get("epoch"), int)
        and not isinstance(status.get("epoch"), bool)
        and int(t.cast(int, status["epoch"])) >= 1
        and isinstance(status.get("certificate_fingerprint"), str)
        and re.fullmatch(r"[0-9a-f]{64}", t.cast(str, status["certificate_fingerprint"]))
        and isinstance(status.get("spki_fingerprint"), str)
        and re.fullmatch(r"[0-9a-f]{64}", t.cast(str, status["spki_fingerprint"]))
    )


def _prepare_vm_ha_managed_mtls(
    *,
    ssh: SSHPush,
    ordered_instances: list[t.Any],
    targets: dict[str, str],
    local_cfg: dict[str, t.Any],
    runtime_binding: t.Any,
) -> _VMHAMTLSApplyTransaction:
    """Bootstrap, resume, repair, or replace managed mTLS before HA activation."""

    binding_by_node = {node.node_id: node for node in runtime_binding.nodes}
    entries: list[tuple[t.Any, str]] = []
    statuses: dict[str, dict[str, object]] = {}
    for inst_cfg in ordered_instances:
        node = inst_cfg.vm_ha_node
        if node is None or node.node_id not in binding_by_node:
            raise RuntimeError("managed mTLS apply requires exact runtime members")
        target = targets[inst_cfg.hostname]
        ssh.ensure_vm_ha_agent_package(target, inst_cfg, local_cfg)
        response = ssh.run_vm_ha_mtls_action(
            target,
            inst_cfg.hostname,
            local_cfg,
            action="status",
            request={},
        )
        result = _vm_ha_mtls_action_result(response)
        if not isinstance(result, dict):
            raise RuntimeError("managed mTLS status returned invalid evidence")
        statuses[node.node_id] = t.cast(dict[str, object], result)
        entries.append((inst_cfg, target))

    exact = {
        node_id: _vm_ha_mtls_exact_identity(
            status, binding_by_node[node_id], runtime_binding.cluster_id
        )
        for node_id, status in statuses.items()
    }
    healthy = all(
        exact[node_id] and status.get("state") == "healthy" and status.get("operation_id") is None
        for node_id, status in statuses.items()
    )
    if healthy:
        node_ids = tuple(statuses)
        first, second = node_ids
        cross_pinned = statuses[first].get("peer_fingerprints") == [
            statuses[second]["certificate_fingerprint"]
        ] and statuses[second].get("peer_fingerprints") == [
            statuses[first]["certificate_fingerprint"]
        ]
        if cross_pinned:
            return _VMHAMTLSApplyTransaction(None, None, ())

    operation_id = _vm_ha_mtls_operation_id(runtime_binding)
    pending = [status for status in statuses.values() if status.get("operation_id") is not None]
    if pending:
        if any(status.get("operation_id") != operation_id for status in pending):
            raise RuntimeError("managed mTLS is owned by a foreign transaction")
        kinds = {str(status.get("operation_kind")) for status in pending}
        if len(kinds) != 1 or next(iter(kinds)) not in {
            "bootstrap",
            "replacement",
            "recovery",
        }:
            raise RuntimeError("managed mTLS pending transaction is not an apply operation")
        operation_kind = next(iter(kinds))
    elif all(status.get("state") == "missing" for status in statuses.values()):
        operation_kind = "bootstrap"
    else:
        exact_healthy_nodes = [
            node_id
            for node_id, status in statuses.items()
            if exact[node_id] and status.get("state") == "healthy"
        ]
        missing_nodes = [
            node_id for node_id, status in statuses.items() if status.get("state") == "missing"
        ]
        if len(exact_healthy_nodes) == 1 and len(missing_nodes) == 1:
            operation_kind = "replacement"
        elif len(exact_healthy_nodes) == 2:
            operation_kind = "recovery"
        else:
            raise RuntimeError(
                "managed mTLS state is neither a healthy pair nor a safe bootstrap/replacement"
            )

    receipts: dict[str, dict[str, object]] = {}
    if operation_kind == "replacement":
        survivor_candidates = [
            node_id
            for node_id, status in statuses.items()
            if status.get("preserve_local") is True
            or (
                status.get("operation_id") is None
                and exact[node_id]
                and status.get("state") == "healthy"
            )
        ]
        if len(survivor_candidates) != 1:
            raise RuntimeError("managed mTLS replacement survivor is not exact")
        survivor_id = survivor_candidates[0]
        replacement_id = next(node_id for node_id in statuses if node_id != survivor_id)
        survivor_epoch = int(t.cast(int, statuses[survivor_id]["epoch"]))
        pending_epoch = next(
            (
                int(t.cast(int, status["peer_target_epoch"]))
                for status in pending
                if status.get("preserve_local") is True
            ),
            survivor_epoch + 1,
        )
        target_epochs = {
            survivor_id: (survivor_epoch, pending_epoch, True),
            replacement_id: (pending_epoch, survivor_epoch, False),
        }
    else:
        pending_epoch = next(
            (int(t.cast(int, status["target_epoch"])) for status in pending),
            1
            if operation_kind == "bootstrap"
            else max(int(t.cast(int, status["epoch"])) for status in statuses.values()) + 1,
        )
        target_epochs = {node_id: (pending_epoch, pending_epoch, False) for node_id in statuses}

    for inst_cfg, target in entries:
        node_id = inst_cfg.vm_ha_node.node_id
        local_epoch, peer_epoch, preserve_local = target_epochs[node_id]
        if preserve_local:
            action = "prepare-peer-replacement"
            request = {
                "operation_id": operation_id,
                "cluster_id": runtime_binding.cluster_id,
                "node_id": node_id,
                "compute_id": binding_by_node[node_id].compute_id,
                "target_peer_epoch": peer_epoch,
            }
        else:
            action = "prepare"
            request = {
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "cluster_id": runtime_binding.cluster_id,
                "node_id": node_id,
                "compute_id": binding_by_node[node_id].compute_id,
                "target_epoch": local_epoch,
                "peer_epoch": peer_epoch,
            }
        result = _vm_ha_mtls_action_result(
            ssh.run_vm_ha_mtls_action(
                target,
                inst_cfg.hostname,
                local_cfg,
                action=action,
                request=request,
            )
        )
        if not isinstance(result, dict):
            raise RuntimeError("managed mTLS prepare returned invalid evidence")
        receipts[node_id] = t.cast(dict[str, object], result)

    for inst_cfg, target in entries:
        node_id = inst_cfg.vm_ha_node.node_id
        peer_id = next(candidate for candidate in receipts if candidate != node_id)
        ssh.run_vm_ha_mtls_action(
            target,
            inst_cfg.hostname,
            local_cfg,
            action="stage-peer",
            request={"operation_id": operation_id, "peer_receipt": receipts[peer_id]},
        )
    for action in ("expand-trust", "activate"):
        for inst_cfg, target in entries:
            ssh.run_vm_ha_mtls_action(
                target,
                inst_cfg.hostname,
                local_cfg,
                action=action,
                request={"operation_id": operation_id},
            )

    return _VMHAMTLSApplyTransaction(
        operation_id,
        operation_kind,
        tuple(
            (inst_cfg, target, receipts[inst_cfg.vm_ha_node.node_id])
            for inst_cfg, target in entries
        ),
    )


def _finalize_vm_ha_managed_mtls(
    *,
    ssh: SSHPush,
    transaction: _VMHAMTLSApplyTransaction,
    local_cfg: dict[str, t.Any],
    agent_statuses: dict[str, dict[str, t.Any]],
) -> None:
    """Commit/prune only after each node observed a fresh authenticated peer."""

    if not transaction.changed:
        return
    assert transaction.operation_id is not None
    receipts = {
        inst_cfg.vm_ha_node.node_id: receipt for inst_cfg, _target, receipt in transaction.nodes
    }
    for inst_cfg, target, receipt in transaction.nodes:
        node_id = inst_cfg.vm_ha_node.node_id
        peer_id = next(candidate for candidate in receipts if candidate != node_id)
        peer_receipt = receipts[peer_id]
        status = agent_statuses.get(node_id)
        if not _vm_ha_mtls_agent_evidence_matches(transaction, node_id, status):
            raise RuntimeError("managed mTLS fresh bidirectional verification is unavailable")
        assert isinstance(status, dict)
        mtls = t.cast(dict[str, t.Any], status["mtls"])
        peer = t.cast(dict[str, t.Any], mtls["peer"])
        observation_id = _canonical_digest(
            {
                "domain": "nebius-vpngw/vm-ha-mtls-observation-v1",
                "operation_id": transaction.operation_id,
                "local_node_id": node_id,
                "peer_node_id": peer_id,
                "peer_boot_id": peer["boot_id"],
                "peer_sequence": peer["sequence"],
                "local_certificate_fingerprint": receipt["certificate_fingerprint"],
                "peer_certificate_fingerprint": peer_receipt["certificate_fingerprint"],
            }
        )
        ssh.run_vm_ha_mtls_action(
            target,
            inst_cfg.hostname,
            local_cfg,
            action="record-observation",
            request={
                "operation_id": transaction.operation_id,
                "local_certificate_fingerprint": receipt["certificate_fingerprint"],
                "peer_certificate_fingerprint": peer_receipt["certificate_fingerprint"],
                "local_epoch": receipt["epoch"],
                "peer_epoch": peer_receipt["epoch"],
                "observation_id": observation_id,
            },
        )
    for action in ("commit", "prune"):
        for inst_cfg, target, _receipt in transaction.nodes:
            ssh.run_vm_ha_mtls_action(
                target,
                inst_cfg.hostname,
                local_cfg,
                action=action,
                request={"operation_id": transaction.operation_id},
            )


def _vm_ha_mtls_agent_evidence_matches(
    transaction: _VMHAMTLSApplyTransaction,
    node_id: str,
    status: object,
) -> bool:
    if not transaction.changed or not isinstance(status, dict):
        return not transaction.changed
    receipts = {
        inst_cfg.vm_ha_node.node_id: receipt for inst_cfg, _target, receipt in transaction.nodes
    }
    if node_id not in receipts or len(receipts) != 2:
        return False
    peer_id = next(candidate for candidate in receipts if candidate != node_id)
    receipt = receipts[node_id]
    peer_receipt = receipts[peer_id]
    mtls = status.get("mtls")
    peer = mtls.get("peer") if isinstance(mtls, dict) else None
    return bool(
        isinstance(mtls, dict)
        and mtls.get("certificate_fingerprint") == receipt["certificate_fingerprint"]
        and mtls.get("epoch") == receipt["epoch"]
        and isinstance(peer, dict)
        and peer.get("fresh") is True
        and peer.get("node_id") == peer_id
        and peer.get("certificate_fingerprint") == peer_receipt["certificate_fingerprint"]
        and peer.get("epoch") == peer_receipt["epoch"]
        and isinstance(peer.get("boot_id"), str)
        and bool(peer["boot_id"])
        and isinstance(peer.get("sequence"), int)
        and not isinstance(peer.get("sequence"), bool)
    )


def _vm_ha_active_route_receipt_matches(
    payload: t.Mapping[str, t.Any],
    *,
    active_node_id: str,
    runtime_binding: t.Any,
    expected_ownership_incarnation: int | None = None,
) -> bool:
    receipt = payload.get("route_reconciliation")
    if not isinstance(receipt, dict):
        return False
    expected_digests = {
        "configuration": runtime_binding.configuration_digest,
        "static_routes": runtime_binding.static_routes_digest,
        "bgp_policy": runtime_binding.bgp_policy_digest,
    }
    operation_id = receipt.get("operation_id")
    ownership_epoch = receipt.get("ownership_epoch")
    ownership_incarnation = receipt.get("ownership_incarnation")
    return bool(
        receipt.get("owner_node_id") == active_node_id
        and receipt.get("allocation_id") == runtime_binding.shared_allocation_id
        and receipt.get("route_runtime_id") == runtime_binding.route_runtime_id
        and receipt.get("generation_id") == runtime_binding.generation_id
        and receipt.get("digests") == expected_digests
        and isinstance(operation_id, str)
        and operation_id
        and isinstance(ownership_epoch, str)
        and ownership_epoch.isdecimal()
        and int(ownership_epoch) > 0
        and ownership_epoch == payload.get("ownership_epoch")
        and isinstance(ownership_incarnation, int)
        and not isinstance(ownership_incarnation, bool)
        and ownership_incarnation >= 0
        and (
            expected_ownership_incarnation is None
            or ownership_incarnation == expected_ownership_incarnation
        )
    )


def _print_vm_ha_migration_preview(
    plan: ResolvedDeploymentPlan,
    *,
    retained_active_name: str,
    plan_digest: str,
) -> None:
    vm_ha = plan.vm_ha
    if vm_ha is None:
        raise ValueError("VM-HA migration preview requires an explicit HA plan")
    passive = next(member for member in vm_ha.members if member.role.value == "passive")
    passive_name = f"{plan.gateway_group.name}-{passive.instance_index}"
    print("[bold]Ordinary gateway to VM-HA migration plan[/bold]")
    print(
        f"[green]  Retain unchanged:[/green] {retained_active_name} Compute, disk, NIC, primary and public allocations"
    )
    print(
        f"[cyan]  Add:[/cyan] passive member {passive_name} and one movable secondary private alias"
    )
    print(
        "[cyan]  Cutover:[/cyan] keep current routes serving until both nodes are staged, locked, and verified"
    )
    print(
        "[cyan]  Failure safety:[/cyan] preserve the serving path until active release; "
        "restore an exact managed route if replacement fails"
    )
    print(f"[dim]  Plan digest: {plan_digest}[/dim]")


def _vm_ha_activation_blockers() -> tuple[str, ...]:
    """Expose the service-owned fail-closed activation boundary to apply."""

    from .agent.main import vm_ha_runtime_blockers

    return vm_ha_runtime_blockers()


def _requested_apply_service_account_token(
    *,
    sa_name: str,
    tenant_id: str | None,
    project_id: str | None,
    region_id: str | None,
    vm_ha_enabled: bool,
) -> str | None:
    """Create/select the requested SA at the flow's explicitly chosen boundary."""

    print(f"[bold]Ensuring Service Account '{sa_name}' and obtaining token...[/bold]")
    try:
        if vm_ha_enabled:
            from .vpngw_sa import (
                VM_HA_ROLE_ALLOWLIST,
                ensure_vm_ha_service_account_and_token,
            )

            token = ensure_vm_ha_service_account_and_token(
                sa_name=sa_name,
                tenant_id=tenant_id,
                project_id=project_id,
                region_id=region_id,
                verified_role_ids=tuple(sorted(VM_HA_ROLE_ALLOWLIST)),
            )
        else:
            from .vpngw_sa import ensure_service_account_and_token

            token = ensure_service_account_and_token(
                sa_name=sa_name,
                tenant_id=tenant_id,
                project_id=project_id,
                region_id=region_id,
                role_ids=("editor",),
                strict_role_grants=True,
            )
        if token:
            print("[green]Service Account token acquired.[/green]")
            os.environ["NEBIUS_IAM_TOKEN"] = token
            return token
        raise RuntimeError("Requested Service Account flow returned no access token")
    except Exception as error:
        label = "VM-HA Service Account" if vm_ha_enabled else "Service Account"
        print(f"[red]{label} setup failed:[/red] {error}")
        print(
            "[yellow]The requested identity must have exactly the reviewed project "
            "editor permit; ambient credential fallback is disabled.[/yellow]"
        )
        raise typer.Exit(code=1) from error


def _active_vm_ha_lifecycle_state(
    *,
    plan: ResolvedDeploymentPlan,
    runtime_binding: t.Any,
    members: tuple[VMHALifecycleMember, VMHALifecycleMember],
    project_id: str | None,
    previous: VMHALifecycleState,
    status: VMHALifecycleStatus = VMHALifecycleStatus.ACTIVE,
) -> VMHALifecycleState:
    """Bind an in-progress deployment to its authoritative cloud identities."""

    if not project_id:
        raise RuntimeError("VM-HA lifecycle provenance requires an exact project ID")
    if previous.transaction is None:
        raise RuntimeError("VM-HA lifecycle binding has no transaction")
    binding_nodes = {node.node_id: node for node in runtime_binding.nodes}
    for member in members:
        bound = binding_nodes.get(member.node_id)
        if (
            bound is None
            or str(bound.role.value) != member.role
            or bound.compute_id != member.compute_id
            or bound.network_interface_name != member.network_interface_name
        ):
            raise RuntimeError("VM-HA staged provenance does not match exact cloud identity")
    owner_members = [
        member
        for member in members
        if runtime_binding.shared_allocation_id in member.alias_allocation_ids
    ]
    if len(owner_members) != 1:
        raise RuntimeError("VM-HA authoritative binding has no exact shared-alias owner")
    owner_member = owner_members[0]
    owner_node = binding_nodes[owner_member.node_id]
    if (
        owner_node.compute_id != owner_member.compute_id
        or owner_node.network_interface_name != owner_member.network_interface_name
    ):
        raise RuntimeError("VM-HA authoritative owner does not match its exact member NIC")
    route_targets = tuple(
        sorted(
            json.dumps(
                target.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            for target in runtime_binding.route_targets
        )
    )
    transaction = previous.transaction.advance(
        predecessor_sha256=previous.record_sha256,
        checkpoint="authoritative-binding-complete",
        pending_effect=None,
        resource_updates={
            "route-runtime-id": runtime_binding.route_runtime_id,
            "shared-allocation-id": runtime_binding.shared_allocation_id,
            "shared-allocation-owner-compute": owner_member.compute_id,
            "shared-allocation-owner-nic": owner_member.network_interface_name,
        },
    )
    return VMHALifecycleState(
        status=status,
        project_id=project_id,
        gateway_name=plan.gateway_group.name,
        cluster_id=runtime_binding.cluster_id,
        allocation_id=runtime_binding.shared_allocation_id,
        allocation_name=(
            f"{plan.gateway_group.name}-{runtime_binding.cluster_id}-shared-private-ip"
        ),
        members=members,
        route_runtime_id=runtime_binding.route_runtime_id,
        route_targets=route_targets,
        transaction=transaction,
    )


class _VMHAAgentStatusError(ValueError):
    """Base class for typed VM-HA status validation failures."""


class _VMHAAgentStatusStale(_VMHAAgentStatusError):
    """A well-formed status for the expected node has not converged yet."""


class _VMHAAgentStatusPermanent(_VMHAAgentStatusError):
    """A malformed or foreign status must abort activation immediately."""


class _VMHAActivationSafelyBlocked(RuntimeError):
    """Activation failed, but both exact apply locks were independently restored."""


class _VMHAActivationUnsafe(RuntimeError):
    """Activation recovery could not establish an exact safe terminal state."""


def _validate_vm_ha_agent_status(
    payload: dict[str, t.Any],
    *,
    inst_cfg: t.Any,
    runtime_binding: t.Any | None = None,
    expected_apply_locked: bool | None = None,
    expected_operation_id: str | None = None,
    require_local_generation: bool = True,
) -> dict[str, t.Any]:
    node = inst_cfg.vm_ha_node
    generation = inst_cfg.vm_ha_generation
    if node is None or generation is None:
        raise _VMHAAgentStatusPermanent("VM-HA status validation requires a complete node manifest")
    expected_digests = {
        "configuration": generation.digests.configuration,
        "static_routes": generation.digests.static_routes,
        "bgp_policy": generation.digests.bgp_policy,
    }
    expected_cluster = None
    if runtime_binding is not None:
        expected_cluster = runtime_binding.cluster_id
    else:
        config_payload = yaml.safe_load(inst_cfg.config_yaml)
        if isinstance(config_payload, dict):
            expected_cluster = (config_payload.get("vm_ha") or {}).get("cluster_id")
    if payload.get("schema") != "nebius-vpngw/vm-ha-status-v1":
        raise _VMHAAgentStatusPermanent("VM-HA agent returned an invalid status schema")
    if (
        payload.get("cluster_id") != expected_cluster
        or payload.get("node_id") != node.node_id
        or payload.get("configured_role") != node.role.value
    ):
        raise _VMHAAgentStatusPermanent(
            "VM-HA agent status has a foreign cluster, node, or configured role"
        )
    if not isinstance(payload.get("apply_locked"), bool):
        raise _VMHAAgentStatusPermanent("VM-HA agent status has an invalid apply-lock record")
    if runtime_binding is not None and (
        payload.get("allocation_id") != runtime_binding.shared_allocation_id
        or payload.get("route_runtime_id") != runtime_binding.route_runtime_id
    ):
        raise _VMHAAgentStatusPermanent("VM-HA agent status does not match the runtime binding")
    if require_local_generation:
        if (
            payload.get("generation_id") != generation.generation_id
            or payload.get("digests") != expected_digests
        ):
            raise _VMHAAgentStatusStale(
                "VM-HA agent status has not reached the expected generation"
            )
    else:
        reported_generation = payload.get("generation_id")
        reported_digests = payload.get("digests")
        if not (
            isinstance(reported_generation, str)
            and re.fullmatch(r"[0-9a-f]{64}", reported_generation)
            and isinstance(reported_digests, dict)
            and set(reported_digests) == {"configuration", "static_routes", "bgp_policy"}
            and all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in reported_digests.values()
            )
            and reported_generation == reported_digests["configuration"]
        ):
            raise _VMHAAgentStatusPermanent("VM-HA agent status has an invalid generation record")
        if (
            reported_digests["static_routes"] != expected_digests["static_routes"]
            or reported_digests["bgp_policy"] != expected_digests["bgp_policy"]
        ):
            raise _VMHAAgentStatusStale(
                "VM-HA agent status has not reached the expected policy digests"
            )

    repair = payload.get("repair")
    if repair is not None:
        expected_repair_keys = {
            "deadline_at",
            "failure_fingerprint",
            "healthy_observations",
            "operation_id",
            "owner_node_id",
            "ownership_epoch",
            "remaining_seconds",
        }
        remaining: object = repair.get("remaining_seconds") if isinstance(repair, dict) else None
        deadline: object = repair.get("deadline_at") if isinstance(repair, dict) else None
        healthy_observations: object = (
            repair.get("healthy_observations") if isinstance(repair, dict) else None
        )
        if not (
            isinstance(repair, dict)
            and set(repair) == expected_repair_keys
            and isinstance(repair.get("operation_id"), str)
            and bool(repair.get("operation_id"))
            and repair.get("owner_node_id") == node.node_id
            and isinstance(repair.get("ownership_epoch"), str)
            and isinstance(repair.get("failure_fingerprint"), list)
            and all(
                isinstance(reason, str) and reason
                for reason in repair.get("failure_fingerprint", [])
            )
            and isinstance(healthy_observations, int)
            and not isinstance(healthy_observations, bool)
            and 0 <= healthy_observations <= 2
            and isinstance(remaining, (int, float))
            and not isinstance(remaining, bool)
            and math.isfinite(remaining)
            and remaining >= 0
            and isinstance(deadline, (int, float))
            and not isinstance(deadline, bool)
            and math.isfinite(deadline)
        ):
            raise _VMHAAgentStatusPermanent("VM-HA agent status has an invalid repair record")

    observed_operation = payload.get("apply_operation_id")
    if expected_apply_locked:
        if payload.get("apply_locked") is not True:
            raise _VMHAAgentStatusStale(
                "VM-HA agent status has not reached the expected apply-lock state"
            )
        if observed_operation != expected_operation_id:
            error_type = (
                _VMHAAgentStatusStale if observed_operation is None else _VMHAAgentStatusPermanent
            )
            raise error_type("VM-HA agent status has the wrong apply-lock operation")
    elif expected_apply_locked is False:
        if payload.get("apply_locked") is True:
            if expected_operation_id is not None and observed_operation == expected_operation_id:
                raise _VMHAAgentStatusStale(
                    "VM-HA agent status has not released the expected apply lock"
                )
            raise _VMHAAgentStatusPermanent(
                "VM-HA agent status is locked by a foreign apply operation"
            )
        if observed_operation is not None:
            raise _VMHAAgentStatusPermanent(
                "VM-HA unlocked status retained an apply operation identity"
            )
    return payload


_VM_HA_DISPLAY_STATES = frozenset(
    {
        "normal",
        "suspect",
        "fencing",
        "ownership-transfer",
        "promoting",
        "active",
        "degraded-path",
        "repairing",
        "repair-exhausted",
        "degraded",
        "blocked",
    }
)
_VM_HA_REARM_PHASES = frozenset(
    {"not-owner", "idle", "inhibited", "blocked", "starting", "running"}
)
_VM_HA_PENDING_ACTIONS_BY_STATE = {
    "normal": frozenset({"enter-passive"}),
    "fencing": frozenset({"stop-former-owner"}),
    "ownership-transfer": frozenset(
        {"attach-candidate", "detach-candidate-for-reproof", "detach-former-attachment"}
    ),
    "promoting": frozenset(
        {
            "confirm-candidate-ownership",
            "enable-active",
            "prepare-candidate-dataplane",
            "reconcile-routes",
        }
    ),
    "repairing": frozenset({"repair-local-dataplane"}),
}
_VM_HA_DURATION_FIELDS = (
    "preparation",
    "detection_repair",
    "common_cutover",
    "redundancy_restoration",
)
_VM_HA_SAFE_REASON_CODES = frozenset(
    {
        "active-node-lacks-exact-allocation-ownership",
        "active-route-reconciliation-context-stale",
        "apply-lock-held",
        "authoritative-cloud-observation-not-yet-recorded",
        "authoritative-cloud-observation-unavailable",
        "authoritative-owner-active",
        "authoritative-owner-peer-is-healthy",
        "authoritative-ownership-epoch-missing",
        "bgp-import-policy-mismatch",
        "bgp-not-ready",
        "bgp-policy-digest-mismatch",
        "candidate-attachment-observation-inconsistent",
        "candidate-attachment-requires-reproof",
        "candidate-dataplane-requires-owner-only-preparation",
        "candidate-owner-observation-with-former-attachment-present",
        "candidate-owner-observation-without-exact-attachment",
        "candidate-ownership-awaiting-authoritative-observation",
        "candidate-ownership-re-read-required",
        "candidate-requires-exact-shared-allocation",
        "checkpointed-action-prerequisites-changed",
        "checkpointed-action-requires-current-boot-guard",
        "checkpointed-action-requires-passive-dataplane",
        "cloud-operation-finalization-failed",
        "cloud-operation-journal-invalid",
        "cloud-operation-journal-unbound",
        "cloud-ownership-unavailable",
        "cold-start-guard-not-installed",
        "cold-start-guard-stale",
        "compute-start-failed",
        "configuration-digest-mismatch",
        "configured-bgp-sessions-not-established",
        "controller-effect-pending",
        "controller-step-failed",
        "current-boot-guard-not-active",
        "current-boot-guard-not-installed",
        "current-boot-status-stale",
        "data-plane-not-passive",
        "exact-owner-ready-to-enable-forwarding",
        "explicit-retry-required",
        "former-allocation-attachment-must-be-absent",
        "former-attachment-observation-inconsistent",
        "former-owner-compute-state-ambiguous",
        "former-owner-identity-mismatch",
        "former-owner-must-be-stopped",
        "generation-mismatch",
        "local-node-is-not-exact-non-owner",
        "local-ownership-lacks-establishment-proof",
        "local-repair-budget-exhausted",
        "local-repair-exhausted-forwarding-fenced",
        "local-repair-verification-active",
        "local-service-unhealthy",
        "local-shared-alias-present",
        "manual-failback-invalid-for-passive-role",
        "manual-failback-required",
        "manual-failover-invalid-for-active-role",
        "no-configured-bgp-sessions",
        "non-owner-must-remain-passive",
        "owner-must-materialize-passive-dataplane",
        "owner-routes-require-reconciliation",
        "owner-shared-alias-not-exact",
        "ownership-confirmation-without-exact-attachment",
        "ownership-or-alias-drift",
        "peer-configured-role-mismatch",
        "peer-generation-unavailable",
        "peer-heartbeat-stale",
        "peer-heartbeat-unhealthy",
        "peer-identity-mismatch",
        "promotion-receipt-invalid",
        "promotion-receipt-unavailable",
        "rearm-checkpoint-invalid",
        "rearm-request-changed",
        "rearm-request-invalid",
        "rearm-status-invalid",
        "rearm-status-unavailable",
        "rearm-writer-busy",
        "redundant-bgp-session-unavailable",
        "routing-hygiene-not-ready",
        "redundant-path-degraded",
        "replaying-checkpointed-action",
        "required-bgp-prefixes-lack-usable-xfrm-next-hop",
        "required-bgp-prefixes-not-learned",
        "route-runtime-identity-missing",
        "route-ledger-identity-not-exact",
        "shared-allocation-identity-missing",
        "standby-ready-evidence-invalid",
        "standby-transfer-readiness-unavailable",
        "static-route-digest-mismatch",
        "static-routes-not-ready",
        "suspicion-window-active",
        "unexpected-allocation-owner",
        "vm-ha-removal-or-disable-active",
        "xfrm-not-ready",
    }
)


def _validate_vm_ha_display_status(
    payload: dict[str, t.Any],
    *,
    inst_cfg: t.Any,
    runtime_binding: t.Any | None,
    require_local_generation: bool = True,
) -> dict[str, t.Any]:
    """Validate every agent field consumed by the integrated status view."""

    validated = _validate_vm_ha_agent_status(
        payload,
        inst_cfg=inst_cfg,
        runtime_binding=runtime_binding,
        require_local_generation=require_local_generation,
    )
    required = {
        "apply_operation_id",
        "data_plane_mode",
        "observed_owner_node_id",
        "pending_operation_id",
        "phase_durations_seconds",
        "promotion_ready",
        "rearm_phase",
        "rearm_reason",
        "reasons",
        "mtls",
        "standby_readiness_reasons",
        "standby_ready",
        "standby_tunnel_state",
        "state",
    }
    if not required.issubset(validated):
        raise _VMHAAgentStatusPermanent("VM-HA agent status is missing required display evidence")

    state = validated["state"]
    data_plane = validated["data_plane_mode"]
    owner = validated["observed_owner_node_id"]
    reasons = validated["reasons"]
    standby_reasons = validated["standby_readiness_reasons"]
    pending = validated["pending_operation_id"]
    apply_operation = validated["apply_operation_id"]
    rearm_reason = validated["rearm_reason"]
    durations = validated["phase_durations_seconds"]
    mtls = validated["mtls"]
    if not (
        isinstance(state, str)
        and state in _VM_HA_DISPLAY_STATES
        and isinstance(data_plane, str)
        and data_plane in {"blocked", "passive", "active"}
        and (owner is None or isinstance(owner, str) and bool(owner))
        and isinstance(reasons, list)
        and all(isinstance(reason, str) and reason for reason in reasons)
        and isinstance(standby_reasons, list)
        and all(isinstance(reason, str) and reason for reason in standby_reasons)
        and isinstance(validated["promotion_ready"], bool)
        and isinstance(validated["standby_ready"], bool)
        and isinstance(validated["standby_tunnel_state"], str)
        and validated["standby_tunnel_state"] in {"cold", "warm", "not-standby"}
        and (pending is None or isinstance(pending, str) and bool(pending))
        and (apply_operation is None or isinstance(apply_operation, str) and bool(apply_operation))
        and isinstance(validated["rearm_phase"], str)
        and validated["rearm_phase"] in _VM_HA_REARM_PHASES
        and (rearm_reason is None or isinstance(rearm_reason, str) and bool(rearm_reason))
        and isinstance(durations, dict)
        and set(durations) == set(_VM_HA_DURATION_FIELDS)
        and all(
            value is None
            or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            )
            for value in durations.values()
        )
        and isinstance(mtls, dict)
        and set(mtls)
        == {
            "state",
            "cluster_id",
            "node_id",
            "compute_id",
            "epoch",
            "certificate_fingerprint",
            "spki_fingerprint",
            "peer_fingerprints",
            "operation_id",
            "operation_kind",
            "target_epoch",
            "peer_target_epoch",
            "preserve_local",
            "inhibited",
            "inhibition_operation_id",
            "phase",
            "recovery",
            "peer",
        }
    ):
        raise _VMHAAgentStatusPermanent("VM-HA agent status has invalid display evidence")

    fingerprint = mtls["certificate_fingerprint"]
    spki = mtls["spki_fingerprint"]
    peer_fingerprints = mtls["peer_fingerprints"]
    operation_id = mtls["operation_id"]
    inhibition_id = mtls["inhibition_operation_id"]
    peer = mtls["peer"]
    if not (
        mtls["state"] in {"missing", "healthy", "transitioning", "invalid"}
        and (mtls["cluster_id"] is None or isinstance(mtls["cluster_id"], str))
        and (mtls["node_id"] is None or isinstance(mtls["node_id"], str))
        and (mtls["compute_id"] is None or isinstance(mtls["compute_id"], str))
        and (
            mtls["epoch"] is None
            or isinstance(mtls["epoch"], int)
            and not isinstance(mtls["epoch"], bool)
            and mtls["epoch"] >= 1
        )
        and (
            fingerprint is None
            or isinstance(fingerprint, str)
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        )
        and (
            spki is None
            or isinstance(spki, str)
            and re.fullmatch(r"[0-9a-f]{64}", spki)
        )
        and isinstance(peer_fingerprints, list)
        and all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in peer_fingerprints
        )
        and (
            operation_id is None
            or isinstance(operation_id, str)
            and re.fullmatch(r"[0-9a-f]{64}", operation_id)
        )
        and mtls["operation_kind"] in {None, "bootstrap", "replacement", "recovery", "rotation"}
        and (
            mtls["target_epoch"] is None
            or isinstance(mtls["target_epoch"], int)
            and not isinstance(mtls["target_epoch"], bool)
            and mtls["target_epoch"] >= 1
        )
        and (
            mtls["peer_target_epoch"] is None
            or isinstance(mtls["peer_target_epoch"], int)
            and not isinstance(mtls["peer_target_epoch"], bool)
            and mtls["peer_target_epoch"] >= 1
        )
        and (mtls["preserve_local"] is None or isinstance(mtls["preserve_local"], bool))
        and isinstance(mtls["inhibited"], bool)
        and (
            inhibition_id is None
            or isinstance(inhibition_id, str)
            and re.fullmatch(r"[0-9a-f]{64}", inhibition_id)
        )
        and (mtls["inhibited"] is (inhibition_id is not None))
        and mtls["phase"]
        in {
            None,
            "prepared",
            "peer-staged",
            "trust-expanded",
            "local-active",
            "verified",
            "committed",
            "pruned",
            "rolled-back",
        }
        and mtls["recovery"] in {None, "inspect", "resume", "roll-forward", "rollback-or-resume"}
        and (
            peer is None
            or isinstance(peer, dict)
            and set(peer)
            == {
                "node_id",
                "boot_id",
                "sequence",
                "epoch",
                "certificate_fingerprint",
                "fresh",
            }
            and isinstance(peer["node_id"], str)
            and isinstance(peer["boot_id"], str)
            and isinstance(peer["sequence"], int)
            and not isinstance(peer["sequence"], bool)
            and peer["sequence"] > 0
            and isinstance(peer["epoch"], int)
            and not isinstance(peer["epoch"], bool)
            and peer["epoch"] >= 1
            and isinstance(peer["certificate_fingerprint"], str)
            and re.fullmatch(r"[0-9a-f]{64}", peer["certificate_fingerprint"])
            and isinstance(peer["fresh"], bool)
        )
    ):
        raise _VMHAAgentStatusPermanent("VM-HA agent status has invalid managed mTLS evidence")
    if not (
        mtls["node_id"] in {None, validated["node_id"]}
        and mtls["cluster_id"] in {None, validated["cluster_id"]}
        and (
            peer is None
            or peer["node_id"] != validated["node_id"]
            and peer["certificate_fingerprint"] in peer_fingerprints
        )
        and (
            mtls["state"] != "healthy"
            or operation_id is None
            and mtls["phase"] is None
            and mtls["recovery"] is None
        )
    ):
        raise _VMHAAgentStatusPermanent("VM-HA agent status has conflicting managed mTLS evidence")

    if validated["apply_locked"] is not (apply_operation is not None):
        raise _VMHAAgentStatusPermanent("VM-HA agent status has an inconsistent apply-lock record")
    local_node = validated["node_id"]
    promotion_ready = validated["promotion_ready"]
    if promotion_ready is True and not (data_plane == "active" and owner == local_node):
        raise _VMHAAgentStatusPermanent(
            "VM-HA agent owner readiness evidence is internally inconsistent"
        )
    if validated["standby_ready"] is True and not (
        data_plane == "passive"
        and owner is not None
        and owner != local_node
        and validated["standby_tunnel_state"] in {"cold", "warm"}
        and standby_reasons == []
    ):
        raise _VMHAAgentStatusPermanent(
            "VM-HA agent standby readiness evidence is internally inconsistent"
        )
    if data_plane == "active" and owner != local_node:
        raise _VMHAAgentStatusPermanent(
            "VM-HA agent forwarding evidence conflicts with observed ownership"
        )
    return validated


def _validate_vm_ha_planned_status(
    payload: dict[str, t.Any],
    *,
    inst_cfg: t.Any,
    runtime_binding: t.Any,
) -> dict[str, t.Any]:
    """Validate exact current-runtime evidence used by planned preparation."""

    validated = _validate_vm_ha_agent_status(
        payload,
        inst_cfg=inst_cfg,
        runtime_binding=runtime_binding,
        expected_apply_locked=False,
    )
    required = {
        "controller_ready_boot_id",
        "data_plane_mode",
        "guard_boot_id",
        "observed_owner_node_id",
        "pending_operation_id",
        "promotion_ready",
        "route_reconciliation",
        "standby_readiness_reasons",
        "standby_ready",
        "state",
    }
    if not required.issubset(validated):
        raise _VMHAAgentStatusPermanent(
            "VM-HA planned status is missing required current-runtime evidence"
        )
    pending = validated["pending_operation_id"]
    standby_reasons = validated["standby_readiness_reasons"]
    # Status v1 predates explicit warm/cold reporting. Missing means the only
    # historically supported standby shape: warm and fully route-ready.
    standby_tunnel_state = validated.get(
        "standby_tunnel_state",
        "warm" if validated["standby_ready"] is True else "not-standby",
    )
    owner = validated["observed_owner_node_id"]
    guard_boot_id = validated["guard_boot_id"]
    ready_boot_id = validated["controller_ready_boot_id"]
    if not (
        validated["state"] in {"blocked", "normal", "suspect", "repairing", "promoting", "active"}
        and validated["data_plane_mode"] in {"blocked", "passive", "active"}
        and (owner is None or isinstance(owner, str))
        and (guard_boot_id is None or isinstance(guard_boot_id, str))
        and (ready_boot_id is None or isinstance(ready_boot_id, str))
        and isinstance(validated["promotion_ready"], bool)
        and isinstance(validated["standby_ready"], bool)
        and standby_tunnel_state in {"cold", "warm", "not-standby"}
        and isinstance(standby_reasons, list)
        and all(isinstance(reason, str) and reason for reason in standby_reasons)
        and (pending is None or isinstance(pending, str) and bool(pending))
    ):
        raise _VMHAAgentStatusPermanent("VM-HA planned status has invalid runtime evidence")
    if validated["standby_ready"] is True and not (
        validated["data_plane_mode"] == "passive"
        and standby_tunnel_state in {"cold", "warm"}
        and standby_reasons == []
        and isinstance(guard_boot_id, str)
        and guard_boot_id
        and ready_boot_id is None
    ):
        raise _VMHAAgentStatusPermanent("VM-HA planned standby evidence is internally inconsistent")
    route = validated["route_reconciliation"]
    if route is not None:
        expected_route_keys = {
            "allocation_id",
            "digests",
            "generation_id",
            "operation_id",
            "owner_node_id",
            "ownership_epoch",
            "ownership_incarnation",
            "route_runtime_id",
        }
        if not (
            isinstance(route, dict)
            and set(route) == expected_route_keys
            and route["allocation_id"] == runtime_binding.shared_allocation_id
            and route["digests"] == validated["digests"]
            and route["generation_id"] == validated["generation_id"]
            and isinstance(route["operation_id"], str)
            and bool(route["operation_id"])
            and isinstance(route["owner_node_id"], str)
            and bool(route["owner_node_id"])
            and isinstance(route["ownership_epoch"], str)
            and bool(route["ownership_epoch"])
            and isinstance(route["ownership_incarnation"], int)
            and not isinstance(route["ownership_incarnation"], bool)
            and route["ownership_incarnation"] >= 0
            and route["route_runtime_id"] == runtime_binding.route_runtime_id
        ):
            raise _VMHAAgentStatusPermanent(
                "VM-HA planned status has an invalid route reconciliation receipt"
            )
    if validated["promotion_ready"] is True and route is None:
        raise _VMHAAgentStatusPermanent(
            "VM-HA planned active status has no route reconciliation receipt"
        )
    return validated


def _fetch_vm_ha_agent_status(
    *,
    target: str,
    hostname: str,
    username: str,
    key_path: Path | None,
    ssh_policy: SSHTrustPolicy,
    inst_cfg: t.Any,
    runtime_binding: t.Any | None = None,
    expected_apply_locked: bool | None = None,
    expected_operation_id: str | None = None,
    require_local_generation: bool = True,
) -> dict[str, t.Any]:
    command = _build_ssh_base_cmd(key_path, ssh_policy=ssh_policy, hostname=hostname)
    command.extend(
        [
            "-o",
            "BatchMode=yes",
            f"{username}@{target}",
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-status",
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "VM-HA agent status command failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise _VMHAAgentStatusPermanent("VM-HA agent returned malformed status JSON") from error
    if not isinstance(payload, dict):
        raise _VMHAAgentStatusPermanent("VM-HA agent returned an invalid status record")
    return _validate_vm_ha_agent_status(
        payload,
        inst_cfg=inst_cfg,
        runtime_binding=runtime_binding,
        expected_apply_locked=expected_apply_locked,
        expected_operation_id=expected_operation_id,
        require_local_generation=require_local_generation,
    )


def _wait_for_vm_ha_agent_status(
    *,
    predicate: t.Callable[[dict[str, t.Any]], bool],
    timeout_seconds: float = 120.0,
    poll_seconds: float = 2.0,
    **fetch_kwargs: t.Any,
) -> dict[str, t.Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = _fetch_vm_ha_agent_status(**fetch_kwargs)
            if predicate(payload):
                return payload
            last_error = _VMHAAgentStatusStale("VM-HA agent has not reached the required state")
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            _VMHAAgentStatusStale,
        ) as error:
            last_error = error
        time.sleep(poll_seconds)
    raise RuntimeError(f"VM-HA status verification timed out: {last_error}") from last_error


def _print_vm_wait_reason(vm_name: str, health: dict[str, t.Any]) -> None:
    if not health.get("reachable"):
        print(f"[dim]{vm_name}: SSH not ready yet[/dim]")
    elif not health.get("cloud_init_complete"):
        print(f"[dim]{vm_name}: Cloud-init still running (packages being installed)[/dim]")
    elif health.get("esp4_reboot_pending"):
        print(f"[dim]{vm_name}: ESP4/kernel update prepared; waiting for reboot[/dim]")
    elif not health.get("esp4_ready", True):
        print(f"[dim]{vm_name}: ESP4 is not loadable yet[/dim]")


def _resolve_local_config(
    local_config_file: Path | None,
    *,
    create_if_missing: bool,
    exit_after_create: bool,
) -> Path:
    """Resolve config path, optionally creating from embedded template and exiting."""
    if local_config_file is not None:
        if not local_config_file.exists():
            print(f"[red]Error: Config file not found at {local_config_file}[/red]")
            print("[yellow]Use 'nebius-vpngw create-config <path>' to create a template.[/yellow]")
            raise typer.Exit(code=1)
        return local_config_file

    default_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if default_path.exists():
        return default_path

    if not create_if_missing:
        print(f"[red]Error: Config file not found at {default_path}[/red]")
        print("[yellow]Use 'nebius-vpngw create-config <path>' to create a template.[/yellow]")
        raise typer.Exit(code=1)

    _create_config_from_template(default_path)
    print(f"[green]✓ Created config template at[/green] {default_path}")
    print()
    print("[bold]Next steps:[/bold]")
    print("  1. Edit the file to set your project context (tenant_id, project_id, region_id)")
    print(
        "  2. Configure gateway networking and VMs "
        "(gateway_group.network_id, subnet, vm_spec, external_ips)"
    )
    print("  3. Define connections and tunnels with peer details")
    print(
        "  4. Set secrets via environment variables or directly in YAML "
        "(e.g., export GCP_TUNNEL_1_PSK=...)"
    )
    print("  5. Validate: [cyan]nebius-vpngw validate-config nebius-vpngw.config.yaml[/cyan]")
    print("  6. Deploy: [cyan]nebius-vpngw apply[/cyan]")
    print()

    if exit_after_create:
        raise typer.Exit(code=0)

    return default_path


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def _external_ips_assigned(external_ips: t.Any) -> bool:
    if not external_ips:
        return False
    if isinstance(external_ips, list):
        for entry in external_ips:
            if isinstance(entry, list):
                for ip in entry:
                    if (
                        isinstance(ip, str)
                        and ip.strip()
                        and not _ENV_PATTERN.fullmatch(ip.strip())
                    ):
                        return True
            elif (
                isinstance(entry, str)
                and entry.strip()
                and not _ENV_PATTERN.fullmatch(entry.strip())
            ):
                return True
    return False


def _format_external_ips_block(indent: str, external_ips: list[list[str]]) -> list[str]:
    lines = [f"{indent}external_ips:"]
    for inst_ips in external_ips:
        if not inst_ips:
            lines.append(f"{indent}  - []")
            continue
        ip_items = ", ".join(f'"{ip}"' for ip in inst_ips)
        lines.append(f"{indent}  - [{ip_items}]")
    return lines


def _normalize_file_text(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


@dataclass(frozen=True)
class _FileFingerprint:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    sha256: str


def _file_fingerprint(path: Path) -> _FileFingerprint | None:
    try:
        metadata = path.lstat()
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    return _FileFingerprint(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _atomic_write_text(
    path: Path,
    text: str,
    *,
    expected_fingerprint: _FileFingerprint | None,
) -> None:
    """Publish a completed wizard artifact without exposing a partial target."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if _file_fingerprint(path) != expected_fingerprint:
            raise OSError(
                "Destination changed while the wizard was running; nothing was written. "
                "Review the current file and rerun the command."
            )
        os.replace(temporary_path, path)
        temporary_path = None
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _fsync_parent_directory(path: Path) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path.parent, directory_flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _restore_interrupted_conditional_publication(path: Path, staging: Path) -> None:
    try:
        metadata = staging.lstat()
    except OSError as error:
        raise OSError(
            "Candidate publication recovery state could not be inspected safely."
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OSError(
            "Unsafe candidate publication recovery state exists; remove it only after manual review."
        )
    raise OSError(
        "A prior candidate publication was interrupted. No recovery artifact was trusted or "
        f"restored automatically; review {path} and {staging} before retrying."
    )


def _conditional_publish_text(
    path: Path,
    text: str,
    *,
    expected_fingerprint: _FileFingerprint | None,
) -> None:
    """Publish a complete file without clobbering a destination changed by another writer."""

    staging = path.parent / f".{path.name}.conditional-publication"
    if staging.exists():
        _restore_interrupted_conditional_publication(path, staging)

    temporary_path: Path | None = None
    quarantined: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        if expected_fingerprint is not None:
            staging.mkdir(mode=0o700)
            quarantined = staging / "expected"
            try:
                os.rename(path, quarantined)
            except FileNotFoundError as error:
                quarantined = None
                staging.rmdir()
                raise OSError(
                    "Destination disappeared while the wizard was running; nothing was written."
                ) from error
            if _file_fingerprint(quarantined) != expected_fingerprint:
                quarantined_metadata = quarantined.lstat()
                if stat.S_ISLNK(quarantined_metadata.st_mode) or not stat.S_ISREG(
                    quarantined_metadata.st_mode
                ):
                    raise OSError(
                        "Destination changed to an unsafe file type during conditional "
                        f"publication. It was not followed; review {quarantined}."
                    )
                try:
                    os.link(quarantined, path)
                except FileExistsError as error:
                    raise OSError(
                        "Destination changed during conditional publication. The newer file was "
                        f"not overwritten; recovery content remains at {quarantined}."
                    ) from error
                quarantined.unlink()
                quarantined = None
                staging.rmdir()
                raise OSError(
                    "Destination changed while the wizard was running; the changed file was "
                    "restored and the candidate was not written."
                )

        try:
            os.link(temporary_path, path)
        except OSError as error:
            recovery = (
                f" Prior destination content remains at {quarantined}."
                if quarantined is not None
                else ""
            )
            reason = (
                "Destination was created by another writer; it was not overwritten."
                if isinstance(error, FileExistsError)
                else "Candidate publication failed; no destination was overwritten."
            )
            raise OSError(reason + recovery) from error
        temporary_path.unlink()
        temporary_path = None
        if quarantined is not None:
            quarantined.unlink()
            quarantined = None
            staging.rmdir()
        _fsync_parent_directory(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if quarantined is None and staging.exists():
            try:
                staging.rmdir()
            except OSError:
                pass


def _update_external_ips_in_yaml(path: Path, external_ips: list[list[str]]) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Try to replace existing external_ips block first
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        match = re.match(r"^(\s*)external_ips\s*:(.*)$", line)
        if not match:
            continue
        indent = match.group(1)
        # Remove existing block lines (indented more than external_ips)
        j = i + 1
        while j < len(lines):
            next_line = lines[j]
            if next_line.strip() == "":
                break
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent <= len(indent):
                break
            j += 1
        new_block = _format_external_ips_block(indent, external_ips)
        lines = lines[:i] + new_block + lines[j:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    # If external_ips not found, insert under gateway_group
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^gateway_group\s*:", stripped):
            base_indent = " " * (len(line) - len(stripped))
            insert_indent = base_indent + "  "
            insert_at = i + 1
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.strip() == "":
                    j += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= len(base_indent):
                    break
                if next_line.lstrip().startswith("instance_count:") or (
                    next_line.lstrip().startswith("name:") and insert_at == i + 1
                ):
                    insert_at = j + 1
                j += 1
            new_block = _format_external_ips_block(insert_indent, external_ips)
            lines = lines[:insert_at] + new_block + lines[insert_at:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return

    raise ValueError("Unable to locate gateway_group or external_ips in YAML.")


def _ensure_gateway_vms_exist(
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None,
    zone: str | None,
    auth_token: str | None,
    tenant_id: str | None,
    region_id: str | None,
    action: str,
) -> None:
    if not project_id:
        print(f"[red]Error: project_id is required to {action}.[/red]")
        raise typer.Exit(code=1)

    vm_mgr = VMManager(
        project_id=project_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )

    client = vm_mgr._get_client()
    if client is None:
        print("[red]Error: Nebius SDK client not available; cannot verify gateway VMs.[/red]")
        raise typer.Exit(code=1)

    try:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, ListInstancesRequest

        isc = InstanceServiceClient(client)
        ilist_op = isc.list(ListInstancesRequest(parent_id=project_id))
        ilist = ilist_op.wait() if hasattr(ilist_op, "wait") else ilist_op

        items: list[t.Any] = []
        if hasattr(ilist, "items"):
            items = list(ilist.items)
        elif hasattr(ilist, "__iter__"):
            items = list(ilist)
    except Exception as e:
        print(f"[red]Error: Failed to query gateway VMs:[/red] {e}")
        raise typer.Exit(code=1)

    existing_vms = [
        inst
        for inst in items
        if getattr(getattr(inst, "metadata", None), "name", "").startswith(
            f"{plan.gateway_group.name}-"
        )
    ]

    if not existing_vms:
        print(f"[red]No gateway VMs found matching pattern '{plan.gateway_group.name}-*'.[/red]")
        print("[yellow]Run 'nebius-vpngw apply' to create gateway VMs first.[/yellow]")
        raise typer.Exit(code=1)


def _serialize_explicit_vm_ha_apply(function: t.Callable[..., t.Any]):
    """Hold one canonical project/gateway writer lock for the whole HA apply."""

    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args: t.Any, **kwargs: t.Any) -> t.Any:
        arguments = signature.bind_partial(*args, **kwargs)
        configured_path = arguments.arguments.get("local_config_file")
        config_path = _resolve_local_config(
            configured_path,
            create_if_missing=True,
            exit_after_create=True,
        )
        config = load_local_config(config_path)
        plan = merge_with_peer_configs(config, [])
        project_override = arguments.arguments.get("project_id")
        canonical_project = project_override or str(config.get("project_id") or "").strip()
        gateway_group = getattr(plan, "gateway_group", None)
        gateway_name = str(getattr(gateway_group, "name", "") or "").strip()
        requires_lock = plan.vm_ha is not None
        if (
            not requires_lock
            and not arguments.arguments.get("dry_run", False)
            and canonical_project
            and gateway_name
        ):
            try:
                lifecycle_state = VMHALifecycleStore(config_path).read(
                    expected_project_id=canonical_project,
                    expected_gateway_name=gateway_name,
                )
            except ValueError:
                # Preserve the command's existing lifecycle validation and error
                # reporting for malformed or mismatched local state.
                return function(*args, **kwargs)
            requires_lock = bool(
                lifecycle_state is not None
                and lifecycle_state.status
                in {
                    VMHALifecycleStatus.PROVISIONING,
                    VMHALifecycleStatus.ACTIVATING,
                    VMHALifecycleStatus.ACTIVE,
                    VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
                }
            )
        if not requires_lock:
            return function(*args, **kwargs)
        if not canonical_project or not gateway_name:
            # The command's normal validation reports malformed injected/test
            # plans before any real VM-HA cloud manager can be constructed.
            return function(*args, **kwargs)
        lock = VMHAApplyLock(
            project_id=canonical_project,
            gateway_name=gateway_name,
        )
        try:
            lock.__enter__()
        except RuntimeError as error:
            print(f"[red]VM-HA apply is already owned by another writer:[/red] {error}")
            raise typer.Exit(code=1) from error
        try:
            return function(*args, **kwargs)
        finally:
            lock.__exit__(None, None, None)

    return wrapped


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
):
    """Default action: creates config template if it doesn't exist."""
    if ctx.invoked_subcommand is None:
        default_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
        if default_path.exists():
            # If default config already exists, show help for convenience.
            typer.echo(ctx.get_help())
            raise typer.Exit()
        # No command given - create config template if missing
        _resolve_local_config(
            None,
            create_if_missing=True,
            exit_after_create=True,
        )


@app.command(epilog=_command_help_epilog("apply"))
@_serialize_explicit_vm_ha_apply
def apply(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: str | None = typer.Option(
        None,
        help=(
            "Ensure the exact dedicated Service Account/group with the reviewed project "
            "editor permit and use an impersonated token; fail closed on drift"
        ),
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect actions without applying"),
    approve_vm_ha_migration: str | None = typer.Option(
        None,
        "--approve-vm-ha-migration",
        metavar="DIGEST",
        help="Approve the exact desired and current-state VM-HA migration digest",
    ),
    recover_vm_ha_migration: str | None = typer.Option(
        None,
        "--recover-vm-ha-migration",
        metavar="DIGEST",
        help="Recover only the exact interrupted two-VM VM-HA migration digest",
    ),
    replace_failed_vm_ha_passive: str | None = typer.Option(
        None,
        "--replace-failed-vm-ha-passive",
        metavar="DIGEST",
        help=(
            "Replace only the exact transaction-created passive Compute and boot disk "
            "from a failed PROVISIONING checkpoint"
        ),
    ),
):
    """Reconcile desired state in Nebius and on the gateway VMs.

    Safe to rerun. Existing VMs, the dedicated gateway subnet, its route table,
    and matching IP allocations are reused when they already match the config.
    Use --recreate-gw only when infrastructure changes require VM recreation.
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=True,
        exit_after_create=True,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Building deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    print("[bold]Validating quotas and constraints...[/bold]")
    plan.validate()

    if replace_failed_vm_ha_passive is not None and plan.vm_ha is None:
        print("[red]Failed-passive replacement requires explicit VM-HA configuration.[/red]")
        raise typer.Exit(code=1)

    if dry_run and plan.vm_ha is None:
        print("[yellow]Dry-run: showing summary of actions[/yellow]")
        print(plan.summary())
        # Skip VM ensure and SSH push in dry-run; just show summary.
        raise typer.Exit(code=0)

    if plan.vm_ha is not None:
        blockers = _vm_ha_activation_blockers()
        if blockers:
            print("[red]VM-HA apply is BLOCKED before external mutation.[/red]")
            for blocker in blockers:
                print(f"[yellow]  - {blocker}[/yellow]")
            raise typer.Exit(code=1)

    # Resolve read-only context before VM-HA trust/member preflight. No cloud mutation is
    # allowed until every planned member is classified and every existing identity is pinned.
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None
    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec", {})
    raw_management_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    management_key_path = Path(raw_management_key).expanduser() if raw_management_key else None
    lifecycle_store = VMHALifecycleStore(local_config_file)
    gateway_name = str(getattr(getattr(plan, "gateway_group", None), "name", "") or "")
    try:
        lifecycle_state = lifecycle_store.read(
            expected_project_id=proj_id,
            expected_gateway_name=gateway_name,
        )
    except ValueError as error:
        print("[red]VM-HA lifecycle state is invalid; apply is blocked before cloud access:[/red]")
        print(f"[yellow]  - {error}[/yellow]")
        raise typer.Exit(code=1) from error
    if plan.vm_ha is not None and lifecycle_state is not None:
        vm_ha_spec = plan.gateway_group.vm_ha
        assert vm_ha_spec is not None
        if lifecycle_state.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS:
            print("[red]VM-HA activation is blocked by an unfinished removal transition.[/red]")
            raise typer.Exit(code=1)
        if lifecycle_state.status in {
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.ACTIVE,
        }:
            planned_members = {
                (
                    member.instance_index,
                    f"{gateway_name}-{member.instance_index}",
                    member.node_id,
                    member.role.value,
                )
                for member in vm_ha_spec.members
            }
            recorded_members = {
                (
                    member.instance_index,
                    member.instance_name,
                    member.node_id,
                    member.role,
                )
                for member in lifecycle_state.members
            }
            if (
                lifecycle_state.cluster_id != vm_ha_spec.cluster_id
                or recorded_members != planned_members
            ):
                print("[red]VM-HA lifecycle identity conflicts with the requested HA plan.[/red]")
                raise typer.Exit(code=1)

    ssh_policy: SSHTrustPolicy | None = None
    former_vm_ha_members: dict[str, str] = {}
    legacy_vm_ha_identities: dict[str, LegacyVMHAIdentity | None] | None = None
    discovery_manager: VMManager | None = None
    vm_ha_migration_active_name: str | None = None
    vm_ha_recovery_required = False
    vm_ha_activation_recovery_required = False
    vm_ha_existing_members: dict[str, str] = {}
    vm_ha_passive_replacement: tuple[str, str] | None = None
    needs_vm_ha_removal = bool(
        plan.vm_ha is None
        and lifecycle_state is not None
        and lifecycle_state.status
        in {
            VMHALifecycleStatus.PROVISIONING,
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.ACTIVE,
            VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
        }
    )
    service_account_selected = bool(plan.vm_ha is None and sa)
    if service_account_selected:
        discovery_auth_token = _requested_apply_service_account_token(
            sa_name=t.cast(str, sa),
            tenant_id=tenant_id,
            project_id=proj_id,
            region_id=region_id,
            vm_ha_enabled=False,
        )
        # Removal must not let the SDK replace an empty requested token with
        # broader ambient credentials at the first cloud-read boundary.
        if needs_vm_ha_removal and not discovery_auth_token:
            print(
                "[red]VM-HA removal discovery requires the requested Service Account "
                "token; refusing ambient credential fallback.[/red]"
            )
            raise typer.Exit(code=1)
    else:
        discovery_auth_token = _ensure_authentication(required=False, show_progress=False)
    if plan.vm_ha is not None:
        blockers = _vm_ha_activation_blockers()
        if blockers:
            print("[red]VM-HA apply is BLOCKED before external mutation.[/red]")
            for blocker in blockers:
                print(f"[yellow]  - {blocker}[/yellow]")
            raise typer.Exit(code=1)
        try:
            planned_instances = tuple(plan.iter_instance_configs())
            discovery_manager = VMManager(
                project_id=proj_id,
                zone=zone or plan.gateway_group.region,
                auth_token=discovery_auth_token,
                tenant_id=tenant_id,
                region_id=region_id,
                management_key_path=management_key_path,
            )
            existing_members = discovery_manager.discover_vm_ha_members(plan.gateway_group)
            vm_ha_existing_members = dict(existing_members)
            if lifecycle_state is not None:
                observer = getattr(discovery_manager, "observe_vm_ha_migration_state", None)
                if callable(observer):
                    candidate_observation = observer(
                        plan.gateway_group,
                        plan.gateway.get("local_prefixes"),
                    )
                    try:
                        vm_ha_passive_replacement = _vm_ha_failed_passive_replacement_plan(
                            plan,
                            lifecycle_state,
                            candidate_observation,
                        )
                    except ValueError:
                        vm_ha_passive_replacement = None
            if replace_failed_vm_ha_passive is not None:
                if vm_ha_passive_replacement is None:
                    raise RuntimeError(
                        "No exact failed PROVISIONING passive is eligible for replacement"
                    )
                if replace_failed_vm_ha_passive != vm_ha_passive_replacement[1]:
                    assert lifecycle_state is not None and lifecycle_state.transaction is not None
                    passive_name = vm_ha_passive_replacement[0]
                    recorded_cycle = vm_ha_passive_replacement_cycle_for_approval(
                        dict(lifecycle_state.transaction.resource_bindings),
                        passive_name,
                        replace_failed_vm_ha_passive,
                    )
                    if recorded_cycle is None:
                        raise RuntimeError(
                            "VM-HA failed-passive replacement approval digest is stale or incorrect"
                        )
                    vm_ha_passive_replacement = (
                        passive_name,
                        replace_failed_vm_ha_passive,
                    )
            if lifecycle_state is None or lifecycle_state.status is VMHALifecycleStatus.REMOVED:
                if len(existing_members) == 1:
                    active_index = next(
                        member.instance_index
                        for member in plan.vm_ha.members
                        if member.role.value == "active"
                    )
                    active_name = f"{gateway_name}-{active_index}"
                    if set(existing_members) != {active_name}:
                        raise RuntimeError(
                            "Ordinary-to-HA migration found one VM, but it is not the configured active member"
                        )
                    if recreate_gw:
                        raise RuntimeError(
                            "Ordinary-to-HA migration must retain the existing active; apply ordinary VM changes before enabling HA"
                        )
                    vm_ha_migration_active_name = active_name
                elif len(existing_members) > 1:
                    vm_ha_recovery_required = True
            enrollment_hosts = {
                instance.hostname
                for instance in planned_instances
                if recreate_gw or instance.hostname not in existing_members
            }
            if vm_ha_passive_replacement is not None:
                enrollment_hosts.add(vm_ha_passive_replacement[0])
            trust_targets: list[tuple[str, str]] = []
            trust_aliases: dict[str, tuple[str, ...]] = {}
            for instance in planned_instances:
                configured_address = str(instance.external_ip or "").strip()
                discovered_address = (
                    str(existing_members.get(instance.hostname) or "").strip()
                    if instance.hostname not in enrollment_hosts
                    else ""
                )
                target = discovered_address or configured_address or instance.hostname
                trust_targets.append((instance.hostname, target))
                trust_aliases[instance.hostname] = tuple(
                    alias
                    for alias in (configured_address, discovered_address)
                    if alias and alias not in {instance.hostname, target}
                )
            ssh_policy = require_vm_ha_ssh_policy(
                tuple(trust_targets),
                enrollment_hosts=enrollment_hosts,
                management_key_path=management_key_path,
                management_public_key=vm_spec.get("ssh_public_key"),
                require_management_key=True,
                trust_scope=_vm_ha_ssh_trust_scope(
                    local_cfg,
                    plan,
                    project_id=proj_id,
                ),
                allow_managed_repair=True,
                additional_aliases=trust_aliases,
            )
            discovery_manager.verify_vm_ha_existing_identities(
                {
                    name: address
                    for name, address in existing_members.items()
                    if vm_ha_passive_replacement is None or name != vm_ha_passive_replacement[0]
                },
                policy=ssh_policy,
                username=(
                    vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
        except (RuntimeError, ValueError) as error:
            print("[red]VM-HA SSH trust preflight failed before external mutation:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
    elif needs_vm_ha_removal:
        try:
            discovery_manager = VMManager(
                project_id=proj_id,
                zone=zone or plan.gateway_group.region,
                auth_token=discovery_auth_token,
                tenant_id=tenant_id,
                region_id=region_id,
                management_key_path=management_key_path,
            )
            assert lifecycle_state is not None
            former_candidates = discovery_manager.discover_former_vm_ha_candidate_members(
                plan.gateway_group,
                lifecycle_state=lifecycle_state,
            )
            if former_candidates:
                ssh_policy = require_vm_ha_ssh_policy(
                    tuple(former_candidates.items()),
                    enrollment_hosts=set(),
                    trust_scope=_vm_ha_ssh_trust_scope(
                        local_cfg,
                        plan,
                        project_id=proj_id,
                        cluster_id=lifecycle_state.cluster_id,
                    ),
                )
                discovery_manager.verify_vm_ha_existing_identities(
                    former_candidates,
                    policy=ssh_policy,
                    username=(
                        vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                    ),
                )
                candidate_provenance = discovery_manager.former_vm_ha_candidate_provenance
                if (
                    candidate_provenance is FormerVMHAProvenance.LIFECYCLE_STATE
                    and lifecycle_state.status
                    in {VMHALifecycleStatus.ACTIVATING, VMHALifecycleStatus.ACTIVE}
                ):
                    inspector = SSHPush(ssh_policy=ssh_policy)
                    legacy_vm_ha_identities = {
                        name: inspector.inspect_legacy_vm_ha_identity(target, name, local_cfg)
                        for name, target in sorted(former_candidates.items())
                    }
                former_vm_ha_members = discovery_manager.discover_former_vm_ha_members(
                    plan.gateway_group,
                    legacy_identities=legacy_vm_ha_identities,
                    lifecycle_state=lifecycle_state,
                )
        except (RuntimeError, ValueError) as error:
            print("[red]Former VM-HA discovery failed before ordinary provisioning:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
    else:
        discovery_manager = VMManager(
            project_id=proj_id,
            zone=zone or plan.gateway_group.region,
            auth_token=discovery_auth_token,
            tenant_id=tenant_id,
            region_id=region_id,
            management_key_path=management_key_path,
        )

    # Analyze the desired infrastructure and obtain any destructive-change approval while
    # every current HA member is still untouched. The discovery manager performs read-only
    # calls only. Ordinary removal uses the requested service-account token so it does not
    # depend on broader operator Compute or VPC read authority.
    assert discovery_manager is not None
    if vm_ha_passive_replacement is not None:
        passive_name, replacement_digest = vm_ha_passive_replacement
        print("[bold]Failed PROVISIONING passive replacement plan[/bold]")
        print(f"[yellow]  Passive only: {passive_name}[/yellow]")
        print("[yellow]  Retained: active VM, all allocations, shared owner, and routes[/yellow]")
        print(f"[dim]  Replacement digest: {replacement_digest}[/dim]")
    print("[bold]Analyzing configuration changes...[/bold]")
    changes = discovery_manager.check_changes(plan.gateway_group)

    has_destructive = False
    has_no_change = True
    for inst_name, diff in changes:
        if diff.requires_recreation():
            has_destructive = True
            has_no_change = False
            print(f"[red]{inst_name}:[/red]")
            print(diff.format_warning())
        elif diff.has_changes():
            has_no_change = False
            print(f"[yellow]{inst_name}:[/yellow]")
            print(diff.format_warning())
        else:
            print(f"[green]{inst_name}: No infrastructure changes[/green]")

    migration_plan_digest: str | None = None
    vm_ha_approval_observation: dict[str, object] | None = None
    vm_ha_approval_current_state: dict[str, object] | None = None
    vm_ha_approval_kind: str | None = None
    if plan.vm_ha is not None:
        observer = getattr(discovery_manager, "observe_vm_ha_migration_state", None)
        if callable(observer):
            vm_ha_approval_observation = observer(
                plan.gateway_group,
                plan.gateway.get("local_prefixes"),
            )
        else:
            # Bounded compatibility for injected offline managers. The real
            # manager always supplies the authoritative cloud observation.
            vm_ha_approval_observation = {
                "members": [
                    {"instance_name": name, "present": True, "public_ip": address}
                    for name, address in sorted(vm_ha_existing_members.items())
                ],
                "project_id": proj_id or "",
                "route_targets": [],
                "routes": [],
                "shared_allocation": {
                    "allocation_name": (
                        f"{gateway_name}-{plan.vm_ha.cluster_id}-shared-private-ip"
                    ),
                    "present": False,
                },
            }
        if (
            lifecycle_state is not None
            and lifecycle_state.status is VMHALifecycleStatus.ACTIVATING
            and lifecycle_state.transaction is not None
            and not _vm_ha_observation_matches_bindings(
                vm_ha_approval_observation,
                dict(lifecycle_state.transaction.resource_bindings),
            )
        ):
            try:
                vm_ha_approval_current_state = _vm_ha_activation_recovery_approval_state(
                    plan,
                    lifecycle_state,
                    vm_ha_approval_observation,
                )
                vm_ha_activation_recovery_required = True
            except ValueError:
                vm_ha_approval_current_state = None
        vm_ha_approval_kind = (
            "recovery"
            if vm_ha_recovery_required or vm_ha_activation_recovery_required
            else (
                lifecycle_state.transaction.approval_kind
                if lifecycle_state is not None
                and lifecycle_state.transaction is not None
                and lifecycle_state.status
                in {
                    VMHALifecycleStatus.PROVISIONING,
                    VMHALifecycleStatus.ACTIVATING,
                }
                else "migration"
            )
        )
        if vm_ha_approval_current_state is None:
            vm_ha_approval_current_state = vm_ha_approval_observation
        migration_plan_digest = _vm_ha_migration_plan_digest(
            plan,
            vm_ha_approval_current_state,
            approval_kind=vm_ha_approval_kind,
        )
    if vm_ha_migration_active_name is not None and migration_plan_digest is not None:
        _print_vm_ha_migration_preview(
            plan,
            retained_active_name=vm_ha_migration_active_name,
            plan_digest=migration_plan_digest,
        )
        if has_destructive:
            print(
                "[red]The retained active requires destructive changes; apply those changes while ordinary before enabling VM HA.[/red]"
            )
            raise typer.Exit(code=1)
    elif (
        vm_ha_recovery_required or vm_ha_activation_recovery_required
    ) and migration_plan_digest is not None:
        recovery_name = "activation" if vm_ha_activation_recovery_required else "migration"
        print(f"[bold]Interrupted VM-HA {recovery_name} recovery plan[/bold]")
        print(
            "[yellow]  Recovery is isolated from ordinary migration approval and "
            "will adopt no identity by name.[/yellow]"
        )
        if vm_ha_activation_recovery_required:
            print(
                "[yellow]  The exact configured-active owner will be rebound to a "
                "fresh passive-first apply transaction.[/yellow]"
            )
        print(f"[dim]  Recovery digest: {migration_plan_digest}[/dim]")

    if dry_run:
        if ssh_policy is not None and getattr(ssh_policy, "managed_action", None):
            print(
                "[dim]Dry-run: apply would "
                f"{ssh_policy.managed_action} the per-deployment VM-HA SSH trust store.[/dim]"
            )
        print(
            "[green]Dry-run complete; no lifecycle, cloud, route, or host state was changed.[/green]"
        )
        raise typer.Exit(code=0)

    approval_flags = tuple(
        value
        for value in (
            approve_vm_ha_migration,
            recover_vm_ha_migration,
            replace_failed_vm_ha_passive,
        )
        if value is not None
    )
    if len(approval_flags) > 1 or (replace_failed_vm_ha_passive and recreate_gw):
        print(
            "[red]VM-HA migration, recovery, replacement, and recreation are mutually exclusive.[/red]"
        )
        raise typer.Exit(code=1)
    if vm_ha_passive_replacement is not None and replace_failed_vm_ha_passive is None:
        print(
            "[red]Failed passive replacement requires the exact "
            "--replace-failed-vm-ha-passive DIGEST shown above.[/red]"
        )
        raise typer.Exit(code=1)
    if vm_ha_recovery_required or vm_ha_activation_recovery_required:
        if recover_vm_ha_migration != migration_plan_digest:
            print(
                "[red]Interrupted VM-HA recovery requires the exact "
                "--recover-vm-ha-migration DIGEST shown above.[/red]"
            )
            raise typer.Exit(code=1)
        if approve_vm_ha_migration:
            raise typer.Exit(code=1)
    elif vm_ha_migration_active_name is not None:
        if approve_vm_ha_migration is not None:
            if approve_vm_ha_migration != migration_plan_digest:
                print("[red]VM-HA migration approval digest is stale or incorrect.[/red]")
                raise typer.Exit(code=1)
        elif not typer.confirm("Proceed with this exact VM-HA migration?", default=False):
            print("[green]Aborted. No migration resources were changed.[/green]")
            raise typer.Exit(code=0)
    elif approve_vm_ha_migration or recover_vm_ha_migration:
        if lifecycle_state is None or lifecycle_state.transaction is None:
            print("[red]No migration or recovery approval is required for this topology.[/red]")
            raise typer.Exit(code=1)
        expected_flag = (
            recover_vm_ha_migration
            if lifecycle_state.transaction.approval_kind == "recovery"
            else approve_vm_ha_migration
        )
        if expected_flag != lifecycle_state.transaction.approval_digest:
            print("[red]VM-HA transaction approval digest does not match the checkpoint.[/red]")
            raise typer.Exit(code=1)

    if has_destructive and not recreate_gw:
        print("\n[red]⚠️  ERROR: Destructive changes require VM recreation[/red]")
        print("[yellow]To proceed with VM recreation, run:[/yellow]")
        print("  nebius-vpngw apply --recreate-gw")
        raise typer.Exit(code=1)

    if has_no_change and recreate_gw:
        print("\n[yellow]⚠️  WARNING: No configuration changes detected[/yellow]")
        print(
            "[yellow]VM recreation will use identical specifications (unnecessary downtime).[/yellow]"
        )
        print("\nDo you want to proceed? [y/N]: ", end="")
        import sys

        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)
        print("[yellow]Proceeding with VM recreation (user confirmed)...[/yellow]")
    elif has_destructive and recreate_gw:
        print("\n[yellow]⚠️  This will:[/yellow]")
        print("[yellow]  • Delete existing VM(s) and boot disk(s)[/yellow]")
        print("[yellow]  • Recreate VM(s) with new specifications[/yellow]")
        print("[yellow]  • Cause downtime for all VPN tunnels[/yellow]")
        print("[yellow]  • Preserve and reassign public IP allocations[/yellow]")
        print("")
        import sys

        sys.stdout.write("\033[1mProceed with VM recreation? [y/N]:\033[0m ")
        sys.stdout.flush()
        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)
        print("[yellow]Proceeding with destructive changes...[/yellow]")
    elif recreate_gw:
        print(
            "\n[yellow]Proceeding with VM recreation for safe changes (--recreate-gw flag provided)...[/yellow]"
        )

    if (
        plan.vm_ha is not None
        and ssh_policy is not None
        and getattr(ssh_policy, "managed_action", None)
    ):
        try:
            publish_vm_ha_ssh_trust(ssh_policy)
        except (OSError, RuntimeError, ValueError) as error:
            print("[red]VM-HA managed SSH trust publication failed before cloud mutation:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
        print(
            f"[green]VM-HA per-deployment SSH trust {ssh_policy.managed_action} completed.[/green]"
        )

    if former_vm_ha_members:
        assert discovery_manager is not None
        removal_writers_stopped = False
        try:
            if (
                lifecycle_state is not None
                and lifecycle_state.status
                in {VMHALifecycleStatus.ACTIVATING, VMHALifecycleStatus.ACTIVE}
                and (legacy_vm_ha_identities is not None)
            ):
                assert ssh_policy is not None
                inspector = SSHPush(ssh_policy=ssh_policy)
                legacy_vm_ha_identities = {
                    name: inspector.inspect_legacy_vm_ha_identity(target, name, local_cfg)
                    for name, target in sorted(former_vm_ha_members.items())
                }
            if discovery_manager.former_vm_ha_candidate_provenance is (
                FormerVMHAProvenance.LIFECYCLE_STATE
            ):
                discovery_manager.verify_former_vm_ha_member_snapshot(
                    plan.gateway_group,
                    former_vm_ha_members,
                    legacy_identities=legacy_vm_ha_identities,
                    lifecycle_state=lifecycle_state,
                )
            else:
                discovery_manager.verify_former_vm_ha_member_snapshot(
                    plan.gateway_group,
                    former_vm_ha_members,
                    legacy_identities=legacy_vm_ha_identities,
                )
            if lifecycle_state is not None:
                removal_observation = {
                    "lifecycle_sha256": lifecycle_state.record_sha256,
                    "members": sorted(former_vm_ha_members.items()),
                }
                if lifecycle_state.is_legacy_v2:
                    current_digest = _canonical_digest(
                        {
                            "lifecycle_sha256": lifecycle_state.record_sha256,
                            "members": sorted(former_vm_ha_members.items()),
                        }
                    )
                    desired_digest = _canonical_digest(
                        {
                            "domain": "nebius-vpngw/vm-ha-removal-v1",
                            "gateway_name": gateway_name,
                            "vm_ha": "disabled",
                        }
                    )
                    operation_id = _canonical_digest(
                        {
                            "current": current_digest,
                            "desired": desired_digest,
                        }
                    )
                    lifecycle_state = VMHALifecycleState.successor_from_v2(
                        lifecycle_state,
                        operation_id=operation_id,
                        approval_kind="migration",
                        approval_digest=_canonical_digest(
                            {
                                "domain": "nebius-vpngw/vm-ha-removal-approval-v1",
                                "current": current_digest,
                                "desired": desired_digest,
                            }
                        ),
                        desired_state_digest=desired_digest,
                        current_state_digest=current_digest,
                        initial_resource_bindings={
                            "shared-allocation-id": lifecycle_state.allocation_id,
                            **{
                                f"compute:{member.instance_name}": member.compute_id
                                for member in lifecycle_state.members
                                if member.compute_id
                            },
                        },
                        current_observation=removal_observation,
                    )
                    assert lifecycle_state.transaction is not None
                    lifecycle_store.write_verified(
                        lifecycle_state,
                        predecessor_sha256=lifecycle_state.transaction.predecessor_sha256,
                    )
                elif lifecycle_state.record_version == 3:
                    previous_sha256 = lifecycle_state.record_sha256
                    lifecycle_state = VMHALifecycleState.successor_from_v3(
                        lifecycle_state,
                        current_observation=removal_observation,
                    )
                    lifecycle_store.write_verified(
                        lifecycle_state,
                        predecessor_sha256=previous_sha256,
                    )
                removal_journal = VMHALifecycleJournal(
                    lifecycle_store,
                    lifecycle_state,
                )
                removal_writers_stopped = (
                    lifecycle_state.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS
                    and lifecycle_state.transaction is not None
                    and lifecycle_state.transaction.checkpoint
                    == "removal-mutation-services-stopped"
                )
                if not removal_writers_stopped:
                    lifecycle_state = lifecycle_state.with_status(
                        VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
                        checkpoint="removal-approved",
                    )
                    removal_journal.transition(lifecycle_state)
            planned_names = {instance.hostname for instance in plan.iter_instance_configs()}
            transition_ssh = SSHPush(ssh_policy=ssh_policy)
            if lifecycle_state is None or lifecycle_state.transaction is None:
                raise RuntimeError("VM-HA removal requires an exact lifecycle transaction")
            members_by_name = {member.instance_name: member for member in lifecycle_state.members}
            if set(former_vm_ha_members) != set(members_by_name):
                raise RuntimeError("VM-HA removal member set drifted before inhibition")
            removal_operation_id = lifecycle_state.transaction.operation_id
            if not removal_writers_stopped:
                inhibitions: dict[str, dict[str, t.Any]] = {}
                for name, target in sorted(former_vm_ha_members.items()):
                    member = members_by_name[name]
                    inhibitions[name] = transition_ssh.inhibit_vm_ha_removal(
                        target,
                        name,
                        local_cfg,
                        node_id=member.node_id,
                        operation_id=removal_operation_id,
                    )
                for name, target in sorted(former_vm_ha_members.items()):
                    transition_ssh.verify_vm_ha_removal_quiescent(
                        target,
                        name,
                        local_cfg,
                        inhibition=inhibitions[name],
                    )
                for name, target in sorted(former_vm_ha_members.items()):
                    transition_ssh.stop_vm_ha_mutation_services(target, name, local_cfg)
                lifecycle_state = lifecycle_state.with_status(
                    VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
                    checkpoint="removal-mutation-services-stopped",
                )
                removal_journal.transition(lifecycle_state)
            for name, target in sorted(former_vm_ha_members.items()):
                transition_ssh.deactivate_vm_ha(
                    target,
                    local_cfg,
                    instance_name=name,
                    retire_member=name not in planned_names,
                )
            for name, target in sorted(former_vm_ha_members.items()):
                transition_ssh.verify_vm_ha_deactivated(
                    target,
                    local_cfg,
                    instance_name=name,
                    retire_member=name not in planned_names,
                )
            if lifecycle_state is not None:
                discovery_manager.verify_former_vm_ha_member_snapshot(
                    plan.gateway_group,
                    former_vm_ha_members,
                    lifecycle_state=lifecycle_state,
                )
                lifecycle_state = lifecycle_state.with_status(VMHALifecycleStatus.REMOVED)
                removal_journal.transition(lifecycle_state)
        except (RuntimeError, ValueError) as error:
            print("[red]Former VM-HA teardown failed before ordinary provisioning:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error

    lifecycle_journal: VMHALifecycleJournal | None = None
    activating_resume = False
    if plan.vm_ha is not None:
        assert vm_ha_approval_observation is not None
        assert vm_ha_approval_kind is not None
        assert migration_plan_digest is not None
        observer = getattr(discovery_manager, "observe_vm_ha_migration_state", None)
        fresh_observation = (
            observer(plan.gateway_group, plan.gateway.get("local_prefixes"))
            if callable(observer)
            else vm_ha_approval_observation
        )
        if replace_failed_vm_ha_passive is not None:
            if lifecycle_state is None:
                print("[red]Failed-passive replacement has no durable lifecycle checkpoint.[/red]")
                raise typer.Exit(code=1)
            try:
                fresh_replacement = _vm_ha_failed_passive_replacement_plan(
                    plan,
                    lifecycle_state,
                    fresh_observation,
                )
            except ValueError as error:
                print(f"[red]Failed-passive replacement became unsafe: {error}[/red]")
                raise typer.Exit(code=1) from error
            if fresh_replacement[1] != replace_failed_vm_ha_passive:
                assert lifecycle_state.transaction is not None
                recorded_cycle = vm_ha_passive_replacement_cycle_for_approval(
                    dict(lifecycle_state.transaction.resource_bindings),
                    fresh_replacement[0],
                    replace_failed_vm_ha_passive,
                )
                if recorded_cycle is None:
                    print("[red]Failed-passive replacement approval became stale.[/red]")
                    raise typer.Exit(code=1)
        desired_digest = _canonical_digest(_vm_ha_desired_approval_state(plan))
        initial_bindings = _vm_ha_initial_resource_bindings(fresh_observation)
        if vm_ha_activation_recovery_required:
            if lifecycle_state is None:
                print("[red]Interrupted VM-HA activation has no lifecycle checkpoint.[/red]")
                raise typer.Exit(code=1)
            try:
                fresh_recovery_state = _vm_ha_activation_recovery_approval_state(
                    plan,
                    lifecycle_state,
                    fresh_observation,
                )
            except ValueError as error:
                print(f"[red]VM-HA activation recovery became unsafe: {error}[/red]")
                raise typer.Exit(code=1) from error
            fresh_digest = _vm_ha_migration_plan_digest(
                plan,
                fresh_recovery_state,
                approval_kind="recovery",
            )
            if fresh_digest != migration_plan_digest:
                print("[red]VM-HA activation recovery approval became stale.[/red]")
                raise typer.Exit(code=1)
            recovery_identity = {
                "approval_digest": migration_plan_digest,
                "domain": "nebius-vpngw/vm-ha-activation-recovery-operation-v1",
                "predecessor_sha256": lifecycle_state.record_sha256,
            }
            initial_bindings["route-runtime-id"] = lifecycle_state.route_runtime_id
            recovery = VMHALifecycleState.recover_interrupted_activation(
                lifecycle_state,
                members=_vm_ha_provisioning_members(plan, fresh_observation),
                operation_id=_canonical_digest(recovery_identity),
                approval_digest=migration_plan_digest,
                desired_state_digest=desired_digest,
                current_state_digest=_canonical_digest(fresh_recovery_state),
                initial_resource_bindings=initial_bindings,
                current_observation=fresh_observation,
            )
            lifecycle_store.write_verified(
                recovery,
                predecessor_sha256=lifecycle_state.record_sha256,
            )
            lifecycle_state = recovery
        if (
            lifecycle_state is not None
            and lifecycle_state.record_version == 3
            and lifecycle_state.status
            in {
                VMHALifecycleStatus.PROVISIONING,
                VMHALifecycleStatus.ACTIVATING,
            }
        ):
            previous_sha256 = lifecycle_state.record_sha256
            lifecycle_state = VMHALifecycleState.successor_from_v3(
                lifecycle_state,
                current_observation=fresh_observation,
            )
            lifecycle_store.write_verified(
                lifecycle_state,
                predecessor_sha256=previous_sha256,
            )
        resumable = bool(
            not vm_ha_activation_recovery_required
            and lifecycle_state is not None
            and lifecycle_state.record_version == 4
            and lifecycle_state.status
            in {
                VMHALifecycleStatus.PROVISIONING,
                VMHALifecycleStatus.ACTIVATING,
            }
        )
        if vm_ha_activation_recovery_required:
            pass
        elif resumable:
            assert lifecycle_state is not None and lifecycle_state.transaction is not None
            if lifecycle_state.transaction.desired_state_digest != desired_digest:
                print("[red]VM-HA desired state changed during an interrupted transaction.[/red]")
                raise typer.Exit(code=1)
            if not _vm_ha_observation_matches_bindings(
                fresh_observation,
                dict(lifecycle_state.transaction.resource_bindings),
            ):
                print("[red]VM-HA authoritative cloud identity drifted from the checkpoint.[/red]")
                raise typer.Exit(code=1)
            activating_resume = lifecycle_state.status is VMHALifecycleStatus.ACTIVATING
        else:
            fresh_digest = _vm_ha_migration_plan_digest(
                plan,
                fresh_observation,
                approval_kind=vm_ha_approval_kind,
            )
            if fresh_digest != migration_plan_digest:
                print("[red]VM-HA approval became stale before durable intent was written.[/red]")
                raise typer.Exit(code=1)
            operation_identity = {
                "approval_digest": migration_plan_digest,
                "domain": "nebius-vpngw/vm-ha-operation-v1",
            }
            if lifecycle_state is not None and lifecycle_state.status in {
                VMHALifecycleStatus.ACTIVE,
                VMHALifecycleStatus.REMOVED,
            }:
                operation_identity["predecessor_sha256"] = lifecycle_state.record_sha256
            operation_id = _canonical_digest(operation_identity)
            current_digest = _canonical_digest(fresh_observation)
            if lifecycle_state is not None and lifecycle_state.is_legacy_v2:
                lifecycle_state = VMHALifecycleState.successor_from_v2(
                    lifecycle_state,
                    operation_id=operation_id,
                    approval_kind=vm_ha_approval_kind,
                    approval_digest=migration_plan_digest,
                    desired_state_digest=desired_digest,
                    current_state_digest=current_digest,
                    initial_resource_bindings=initial_bindings,
                    current_observation=fresh_observation,
                    observed_members=_vm_ha_provisioning_members(
                        plan,
                        fresh_observation,
                    ),
                )
                assert lifecycle_state.transaction is not None
                lifecycle_store.write_verified(
                    lifecycle_state,
                    predecessor_sha256=lifecycle_state.transaction.predecessor_sha256,
                )
            else:
                previous = lifecycle_state
                provisioning = VMHALifecycleState.start_provisioning(
                    project_id=proj_id or "",
                    gateway_name=gateway_name,
                    cluster_id=plan.vm_ha.cluster_id,
                    allocation_name=(f"{gateway_name}-{plan.vm_ha.cluster_id}-shared-private-ip"),
                    members=_vm_ha_provisioning_members(plan, fresh_observation),
                    operation_id=operation_id,
                    approval_kind=vm_ha_approval_kind,
                    approval_digest=migration_plan_digest,
                    desired_state_digest=desired_digest,
                    current_state_digest=current_digest,
                    predecessor_sha256=(None if previous is None else previous.record_sha256),
                    initial_resource_bindings=initial_bindings,
                    current_observation=fresh_observation,
                )
                if (
                    previous is not None
                    and previous.record_version in {3, 4}
                    and previous.status is VMHALifecycleStatus.ACTIVE
                ):
                    provisioning = VMHALifecycleState(
                        status=VMHALifecycleStatus.PROVISIONING,
                        project_id=previous.project_id,
                        gateway_name=previous.gateway_name,
                        cluster_id=previous.cluster_id,
                        allocation_id=previous.allocation_id,
                        allocation_name=previous.allocation_name,
                        members=previous.members,
                        route_runtime_id=previous.route_runtime_id,
                        route_targets=previous.route_targets,
                        transaction=provisioning.transaction,
                        record_version=4,
                    )
                lifecycle_store.write_verified(
                    provisioning,
                    predecessor_sha256=(None if previous is None else previous.record_sha256),
                )
                lifecycle_state = provisioning
        observed_lifecycle = lifecycle_store.read(
            expected_project_id=proj_id,
            expected_gateway_name=gateway_name,
        )
        if observed_lifecycle != lifecycle_state:
            raise RuntimeError("VM-HA durable intent did not reread exactly")
        lifecycle_journal = VMHALifecycleJournal(
            lifecycle_store,
            t.cast(VMHALifecycleState, lifecycle_state),
        )

    # Optional Service Account provisioning/auth. Every ordinary --sa path, including
    # lifecycle-bound removal, selected this token before its first cloud read.
    auth_token = discovery_auth_token
    if sa and not service_account_selected:
        if lifecycle_journal is not None:
            lifecycle_journal.begin("prepare-service-account")
        auth_token = _requested_apply_service_account_token(
            sa_name=sa,
            tenant_id=tenant_id,
            project_id=proj_id,
            region_id=region_id,
            vm_ha_enabled=plan.vm_ha is not None,
        )
        if lifecycle_journal is not None:
            lifecycle_journal.complete("prepare-service-account")
    else:
        # No SA requested; if NEBIUS_IAM_TOKEN is missing, try to read it from CLI config
        if os.environ.get("NEBIUS_IAM_TOKEN"):
            print("[green]Using IAM token from Nebius CLI (auto-fetched).[/green]")
        else:
            print(
                "[yellow]No IAM token found; SDK will use Nebius CLI profile if configured.[/yellow]"
            )

    vm_mgr = VMManager(
        project_id=proj_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
        ssh_policy=ssh_policy,
        management_key_path=management_key_path,
    )
    if lifecycle_journal is not None:
        setter = getattr(vm_mgr, "set_vm_ha_lifecycle_journal", None)
        if not callable(setter):
            raise RuntimeError("VM-HA manager has no lifecycle journal interface")
        setter(lifecycle_journal)
    ssh = SSHPush(ssh_policy=ssh_policy)

    show_add_routes_hint = _should_prompt_add_routes_after_apply(
        plan,
        changes,
        recreate_gw=recreate_gw,
    )

    # Determine appropriate action message based on whether VMs exist
    has_existing_vms = any(
        diff.change_type.value != "safe" or "does not exist" not in " ".join(diff.differences)
        for _, diff in changes
    )
    if has_existing_vms or recreate_gw:
        print("[bold]Updating gateway VMs...[/bold]")
    else:
        print("[bold]Creating gateway VMs...[/bold]")

    if replace_failed_vm_ha_passive is not None:
        replace_passive = getattr(vm_mgr, "replace_failed_vm_ha_passive", None)
        if not callable(replace_passive):
            raise RuntimeError("VM-HA manager has no failed-passive replacement interface")
        replace_passive(
            plan.gateway_group,
            plan.gateway.get("local_prefixes"),
            approval_digest=replace_failed_vm_ha_passive,
        )

    if activating_resume:
        resume_activation = getattr(vm_mgr, "resume_vm_ha_activation", None)
        if not callable(resume_activation):
            raise RuntimeError("VM-HA manager has no activation-resume interface")
        vm_ips = resume_activation(
            plan.gateway_group,
            plan.gateway.get("local_prefixes"),
        )
    else:
        vm_ips = vm_mgr.ensure_group(
            plan.gateway_group,
            recreate=recreate_gw,
            local_prefixes=plan.gateway.get("local_prefixes"),
        )
    vm_ha_runtime_binding = getattr(vm_ips, "vm_ha_runtime_binding", None)
    if plan.vm_ha is not None and vm_ha_runtime_binding is None:
        raise RuntimeError("VM-HA provisioning returned no authoritative runtime binding")

    # Wait for VMs to be network-reachable and verify bootstrap
    if vm_ips:
        print("[bold]Waiting for VMs to become reachable...[/bold]")
        all_reachable = True
        for vm_name, vm_ip in vm_ips.items():
            if not vm_mgr.wait_for_vm_network(vm_name, vm_ip, timeout=180):
                all_reachable = False

        if all_reachable:
            print("[bold]Verifying VM bootstrap and package installation...[/bold]")
            all_healthy = True
            for vm_name, vm_ip in vm_ips.items():
                health = vm_mgr.check_vm_health(vm_name, vm_ip)
                if _vm_ready_for_config_push(health) and _vm_packages_verified(health):
                    print(f"[green]{vm_name} ({vm_ip}): {health['message']}[/green]")
                elif health["reachable"]:
                    print(f"[yellow]{vm_name} ({vm_ip}): {health['message']}[/yellow]")
                    all_healthy = False
                else:
                    print(f"[red]{vm_name} ({vm_ip}): {health['message']}[/red]")
                    all_healthy = False

            # If VMs are not fully healthy, wait for the bootstrap gate before pushing configs.
            if not all_healthy:
                import time

                print(
                    "[yellow]Waiting for cloud-init, ESP4 readiness, and package installation...[/yellow]"
                )
                max_wait = 900  # First boot can include apt upgrade plus one reboot.
                wait_interval = 10
                wait_elapsed = 0
                for attempt in range(max_wait // wait_interval):
                    time.sleep(wait_interval)
                    wait_elapsed = (attempt + 1) * wait_interval
                    all_ready = True
                    packages_verified = True
                    for vm_name, vm_ip in vm_ips.items():
                        health = vm_mgr.check_vm_health(vm_name, vm_ip)
                        if not _vm_ready_for_config_push(health):
                            all_ready = False
                            _print_vm_wait_reason(vm_name, health)
                            break
                        if not _vm_packages_verified(health):
                            packages_verified = False
                    if all_ready:
                        if not packages_verified:
                            print(
                                "[yellow]Bootstrap gate is ready, but package/service verification is incomplete; continuing with config push.[/yellow]"
                            )
                        else:
                            print(
                                f"[green]✓ All VMs ready: SSH accessible, cloud-init complete, ESP4 ready, and packages verified (waited {wait_elapsed}s)[/green]"
                            )
                        print(f"[green]✓ Config push gate passed after {wait_elapsed}s[/green]")
                        break
                    print(
                        f"[dim]Waiting for bootstrap to complete... ({wait_elapsed}s elapsed)[/dim]"
                    )
                else:
                    print(
                        "[red]VM bootstrap did not become ready for config push within timeout.[/red]"
                    )
                    print(
                        "[yellow]Rerun apply after cloud-init and any ESP4/kernel reboot finish.[/yellow]"
                    )
                    raise typer.Exit(code=1)
        else:
            print("[yellow]Some VMs did not become reachable within timeout[/yellow]")

    def _config_target(inst_cfg: t.Any) -> str:
        # Use discovered IP from vm_ips first, then fall back to config
        target = vm_ips.get(inst_cfg.hostname) or (inst_cfg.external_ip or "").strip()
        if not target:
            # Last resort: try to query the VM
            discovered_ip = vm_mgr.get_vm_public_ip(inst_cfg.hostname)
            if discovered_ip:
                target = discovered_ip
        return target

    if plan.vm_ha is not None:
        assert vm_ha_runtime_binding is not None
        lifecycle_targets: dict[str, str] = {}
        for inst_cfg in _vm_ha_apply_order(plan):
            target = _config_target(inst_cfg)
            if not target:
                raise RuntimeError(
                    f"VM-HA lifecycle cannot bind {inst_cfg.hostname} without an SSH target"
                )
            lifecycle_targets[inst_cfg.hostname] = target
        if lifecycle_targets and not activating_resume:
            if lifecycle_journal is None:
                raise RuntimeError("VM-HA lifecycle journal was lost before binding")
            finalizer = getattr(vm_mgr, "finalize_vm_ha_provisioning", None)
            if not callable(finalizer):
                raise RuntimeError("VM-HA manager has no authoritative provisioning finalizer")
            exact_members = finalizer(
                plan.gateway_group,
                plan.gateway.get("local_prefixes"),
                lifecycle_targets,
            )
            lifecycle_state = _active_vm_ha_lifecycle_state(
                plan=plan,
                runtime_binding=vm_ha_runtime_binding,
                members=exact_members,
                project_id=proj_id,
                previous=lifecycle_journal.state,
                status=VMHALifecycleStatus.ACTIVATING,
            )
            lifecycle_journal.transition(lifecycle_state)

    if plan.vm_ha is None:
        print("[bold]Pushing per-VM resolved configs and reloading agent...[/bold]")
        for inst_cfg in plan.iter_instance_configs():
            target = _config_target(inst_cfg)
            if not target:
                print(
                    f"[dim]Skipping config push for {inst_cfg.hostname}: No IP address available[/dim]"
                )
                continue
            stale_vm_ha_removed = bool(former_vm_ha_members)
            ssh.push_config_and_reload(
                target,
                inst_cfg,
                local_cfg,
                fail_closed=stale_vm_ha_removed,
            )
    else:
        assert vm_ha_runtime_binding is not None
        assert lifecycle_journal is not None
        current_owner_node_id = _vm_ha_bound_owner_node_id(
            vm_ha_runtime_binding,
            lifecycle_journal.state,
        )
        ordered_instances = _vm_ha_apply_order_for_owner(plan, current_owner_node_id)
        print("[bold]Staging VM-HA configs non-owner-first without activation...[/bold]")
        staged: list[tuple[t.Any, str, t.Any]] = []
        for inst_cfg in ordered_instances:
            target = _config_target(inst_cfg)
            if not target:
                print(f"[red]Cannot stage VM-HA node {inst_cfg.hostname}: no SSH target[/red]")
                print(
                    "[yellow]No staged node was activated; rerun apply after SSH is ready.[/yellow]"
                )
                raise typer.Exit(code=1)
            stage_effect = f"stage-{inst_cfg.vm_ha_node.node_id}"
            lifecycle_journal.begin(stage_effect)
            receipt = ssh.stage_vm_ha_config(
                target,
                inst_cfg,
                local_cfg,
                runtime_binding=vm_ha_runtime_binding,
                nebius_credentials_path=inst_cfg.vm_ha_node.nebius_credentials_path,
            )
            lifecycle_journal.complete(stage_effect)
            staged.append((inst_cfg, target, receipt))
            print(
                f"[green]✓ Staged {receipt.node_id} generation {receipt.generation_id[:12]}[/green]"
            )

        generation_ids = {receipt.generation_id for _, _, receipt in staged}
        digest_sets = {
            (
                receipt.configuration_digest,
                receipt.static_routes_digest,
                receipt.bgp_policy_digest,
            )
            for _, _, receipt in staged
        }
        if len(generation_ids) != 1 or len(digest_sets) != 1:
            print("[red]VM-HA staged acknowledgements do not have exact generation parity.[/red]")
            print("[yellow]Neither node was activated; repair parity and rerun apply.[/yellow]")
            raise typer.Exit(code=1)

        blockers = _vm_ha_activation_blockers()
        if blockers:
            print("[red]VM-HA activation is BLOCKED by incomplete runtime wiring.[/red]")
            for blocker in blockers:
                print(f"[yellow]  - {blocker}[/yellow]")
            print("[yellow]Both manifests remain staged and neither node was activated.[/yellow]")
            raise typer.Exit(code=1)

        operation_id = _vm_ha_apply_operation_id(vm_ha_runtime_binding)
        print("[bold]Installing exact-generation VM-HA apply locks non-owner-first...[/bold]")
        locked: list[tuple[t.Any, str, t.Any, t.Any]] = []
        try:
            for inst_cfg, target, receipt in staged:
                lock_effect = f"install-apply-lock-{receipt.node_id}"
                lifecycle_journal.begin(lock_effect)
                lock_receipt = ssh.install_vm_ha_apply_lock(
                    target,
                    inst_cfg,
                    local_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    operation_id=operation_id,
                )
                lifecycle_journal.complete(lock_effect)
                locked.append((inst_cfg, target, receipt, lock_receipt))
                print(
                    f"[green]✓ Locked {receipt.node_id} for operation {operation_id[:12]}[/green]"
                )
        except (OSError, RuntimeError, ValueError) as error:
            print(
                "[red]VM-HA apply-lock installation failed; installed locks were preserved.[/red]"
            )
            raise typer.Exit(code=1) from error

        try:
            owner_locked_entry = next(
                item for item in locked if item[0].vm_ha_node.node_id == current_owner_node_id
            )
            owner_cfg, owner_target, _owner_stage, owner_lock_receipt = owner_locked_entry
            adoption_effect = f"install-owner-adoption-{current_owner_node_id}"
            lifecycle_journal.rewind_host_activation_for_owner_adoption(adoption_effect)
            lifecycle_journal.begin(adoption_effect)
            ssh.install_vm_ha_apply_owner_adoption(
                owner_target,
                owner_cfg,
                local_cfg,
                runtime_binding=vm_ha_runtime_binding,
                lock_receipt=owner_lock_receipt,
            )
            lifecycle_journal.complete(adoption_effect)
            print(
                "[green]✓ Declared the exact cloud-selected owner for fenced "
                "generation adoption[/green]"
            )
        except (OSError, RuntimeError, ValueError) as error:
            safe_detail_prefixes = (
                "VM-HA apply-owner adoption verification failed",
                "VM-HA owner adoption",
                "VM-HA lifecycle",
            )
            detail = (
                str(error)
                if str(error).startswith(safe_detail_prefixes)
                else type(error).__name__
            )
            print(
                "[red]VM-HA owner-adoption declaration failed; exact-generation "
                f"apply locks were preserved ({detail}).[/red]"
            )
            raise typer.Exit(code=1) from error

        try:
            print("[bold]Preparing VM-local managed mTLS over exact-pinned SSH...[/bold]")
            mtls_transaction = _prepare_vm_ha_managed_mtls(
                ssh=ssh,
                ordered_instances=ordered_instances,
                targets=lifecycle_targets,
                local_cfg=local_cfg,
                runtime_binding=vm_ha_runtime_binding,
            )
            if mtls_transaction.changed:
                print(
                    "[green]✓ Managed mTLS identity and direct peer trust staged; "
                    "fresh heartbeat verification remains pending[/green]"
                )
            else:
                print("[green]✓ Managed mTLS is already exact and healthy[/green]")
        except (OSError, RuntimeError, ValueError) as error:
            print(
                "[red]Managed mTLS preparation failed; exact-generation apply locks "
                "were preserved on all reached members.[/red]"
            )
            raise typer.Exit(code=1) from error

        if lifecycle_state is None:
            raise RuntimeError("VM-HA activation has no durable lifecycle identity")
        try:
            print("[bold]Activating verified VM-HA configs non-owner-first...[/bold]")
            for inst_cfg, target, receipt, _lock_receipt in locked:
                activation_effect = f"activate-{receipt.node_id}"
                lifecycle_journal.begin(activation_effect)
                ssh.push_config_and_reload(
                    target,
                    inst_cfg,
                    local_cfg,
                    staged_receipt=receipt,
                    runtime_binding=vm_ha_runtime_binding,
                )
                lifecycle_journal.complete(activation_effect)
                print(f"[green]✓ Activated {receipt.node_id}[/green]")

            username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
            print(
                "[bold]Verifying both activated nodes remain fenced on the exact operation...[/bold]"
            )
            activated_agent_statuses: dict[str, dict[str, t.Any]] = {}
            for inst_cfg, target, _receipt, _lock_receipt in locked:
                node_id = inst_cfg.vm_ha_node.node_id
                activated_agent_statuses[node_id] = _wait_for_vm_ha_agent_status(
                    predicate=lambda payload: (
                        payload.get("data_plane_mode") == "passive"
                        and payload.get("promotion_ready") is False
                        and _vm_ha_mtls_agent_evidence_matches(
                            mtls_transaction,
                            str(payload.get("node_id") or ""),
                            payload,
                        )
                    ),
                    target=target,
                    hostname=inst_cfg.hostname,
                    username=username,
                    key_path=management_key_path,
                    ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                    inst_cfg=inst_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    expected_apply_locked=True,
                    expected_operation_id=operation_id,
                )

            _finalize_vm_ha_managed_mtls(
                ssh=ssh,
                transaction=mtls_transaction,
                local_cfg=local_cfg,
                agent_statuses=activated_agent_statuses,
            )
            if mtls_transaction.changed:
                print("[green]✓ Managed mTLS committed after fresh bidirectional proof[/green]")

            active_entry = next(
                item for item in locked if item[0].vm_ha_node.node_id == current_owner_node_id
            )
            passive_entry = next(
                item for item in locked if item[0].vm_ha_node.node_id != current_owner_node_id
            )
            active_cfg, active_target, _active_stage, active_lock = active_entry
            active_node_id = active_cfg.vm_ha_node.node_id
            print(
                "[bold]Releasing the current-owner lock and verifying routed forwarding...[/bold]"
            )
            try:
                lifecycle_journal.begin("verify-active-forwarding-and-routes")
                ssh.clear_vm_ha_apply_lock(
                    active_target,
                    active_cfg,
                    local_cfg,
                    receipt=active_lock,
                )
                _wait_for_vm_ha_agent_status(
                    predicate=lambda payload: (
                        payload.get("data_plane_mode") == "active"
                        and payload.get("promotion_ready") is True
                        and payload.get("observed_owner_node_id") == active_node_id
                        and payload.get("pending_operation_id") is None
                        and _vm_ha_active_route_receipt_matches(
                            payload,
                            active_node_id=active_node_id,
                            runtime_binding=vm_ha_runtime_binding,
                        )
                    ),
                    target=active_target,
                    hostname=active_cfg.hostname,
                    username=username,
                    key_path=management_key_path,
                    ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                    inst_cfg=active_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    expected_apply_locked=False,
                    expected_operation_id=operation_id,
                )
                lifecycle_journal.complete("verify-active-forwarding-and-routes")
            except Exception:
                ssh.install_vm_ha_apply_lock(
                    active_target,
                    active_cfg,
                    local_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    operation_id=operation_id,
                )
                raise

            passive_cfg, passive_target, _passive_stage, passive_lock = passive_entry
            print("[bold]Releasing the standby lock last and verifying passive state...[/bold]")
            try:
                lifecycle_journal.begin("verify-passive-unlocked-non-forwarding")
                ssh.clear_vm_ha_apply_lock(
                    passive_target,
                    passive_cfg,
                    local_cfg,
                    receipt=passive_lock,
                )
                _wait_for_vm_ha_agent_status(
                    predicate=lambda payload: (
                        payload.get("data_plane_mode") == "passive"
                        and payload.get("observed_owner_node_id") == active_node_id
                        and payload.get("pending_operation_id") is None
                    ),
                    target=passive_target,
                    hostname=passive_cfg.hostname,
                    username=username,
                    key_path=management_key_path,
                    ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                    inst_cfg=passive_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    expected_apply_locked=False,
                    expected_operation_id=operation_id,
                )
                lifecycle_journal.complete("verify-passive-unlocked-non-forwarding")
            except Exception:
                ssh.install_vm_ha_apply_lock(
                    passive_target,
                    passive_cfg,
                    local_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    operation_id=operation_id,
                )
                raise
            activating_predecessor = lifecycle_journal.state
            active_successor = activating_predecessor.with_status(
                VMHALifecycleStatus.ACTIVE,
                checkpoint="activation-complete",
            )
            try:
                lifecycle_journal.transition(active_successor)
                lifecycle_state = active_successor
            except Exception as transition_error:
                try:
                    observed_lifecycle = lifecycle_journal.store.read(
                        expected_project_id=activating_predecessor.project_id,
                        expected_gateway_name=activating_predecessor.gateway_name,
                    )
                except Exception as read_error:
                    raise _VMHAActivationUnsafe(
                        "final ACTIVE persistence failed and the lifecycle record "
                        "could not be read authoritatively"
                    ) from read_error

                if (
                    observed_lifecycle is not None
                    and observed_lifecycle.record_sha256 == active_successor.record_sha256
                ):
                    try:
                        _wait_for_vm_ha_agent_status(
                            predicate=lambda payload: (
                                payload.get("data_plane_mode") == "active"
                                and payload.get("promotion_ready") is True
                                and payload.get("observed_owner_node_id") == active_node_id
                                and payload.get("pending_operation_id") is None
                                and _vm_ha_active_route_receipt_matches(
                                    payload,
                                    active_node_id=active_node_id,
                                    runtime_binding=vm_ha_runtime_binding,
                                )
                            ),
                            target=active_target,
                            hostname=active_cfg.hostname,
                            username=username,
                            key_path=management_key_path,
                            ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                            inst_cfg=active_cfg,
                            runtime_binding=vm_ha_runtime_binding,
                            expected_apply_locked=False,
                            expected_operation_id=operation_id,
                        )
                        _wait_for_vm_ha_agent_status(
                            predicate=lambda payload: (
                                payload.get("data_plane_mode") == "passive"
                                and payload.get("observed_owner_node_id") == active_node_id
                                and payload.get("pending_operation_id") is None
                            ),
                            target=passive_target,
                            hostname=passive_cfg.hostname,
                            username=username,
                            key_path=management_key_path,
                            ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                            inst_cfg=passive_cfg,
                            runtime_binding=vm_ha_runtime_binding,
                            expected_apply_locked=False,
                            expected_operation_id=operation_id,
                        )
                    except Exception as status_error:
                        raise _VMHAActivationUnsafe(
                            "the exact ACTIVE lifecycle successor persisted, but "
                            "independent node status verification failed"
                        ) from status_error
                    lifecycle_journal.state = observed_lifecycle
                    lifecycle_state = observed_lifecycle
                elif (
                    observed_lifecycle is not None
                    and observed_lifecycle.record_sha256 == activating_predecessor.record_sha256
                ):
                    for (
                        recovery_cfg,
                        recovery_target,
                        expected_lock,
                    ) in (
                        (passive_cfg, passive_target, passive_lock),
                        (active_cfg, active_target, active_lock),
                    ):
                        node_id = recovery_cfg.vm_ha_node.node_id
                        try:
                            recovered_lock = ssh.install_vm_ha_apply_lock(
                                recovery_target,
                                recovery_cfg,
                                local_cfg,
                                runtime_binding=vm_ha_runtime_binding,
                                operation_id=operation_id,
                            )
                            if recovered_lock != expected_lock:
                                raise RuntimeError(
                                    "recovered apply lock does not match its exact receipt"
                                )
                            if recovery_cfg.vm_ha_node.role.value == "passive":

                                def passive_recovery_predicate(
                                    payload: dict[str, t.Any],
                                ) -> bool:
                                    return (
                                        payload.get("data_plane_mode") in {"blocked", "passive"}
                                        and payload.get("promotion_ready") is False
                                        and payload.get("observed_owner_node_id") == active_node_id
                                        and payload.get("pending_operation_id") is None
                                    )

                                recovery_predicate = passive_recovery_predicate
                            else:

                                def active_recovery_predicate(
                                    payload: dict[str, t.Any],
                                ) -> bool:
                                    return (
                                        payload.get("data_plane_mode") == "active"
                                        and payload.get("observed_owner_node_id") == active_node_id
                                        and payload.get("pending_operation_id") is None
                                        and _vm_ha_active_route_receipt_matches(
                                            payload,
                                            active_node_id=active_node_id,
                                            runtime_binding=vm_ha_runtime_binding,
                                        )
                                    )

                                recovery_predicate = active_recovery_predicate
                            _wait_for_vm_ha_agent_status(
                                predicate=recovery_predicate,
                                target=recovery_target,
                                hostname=recovery_cfg.hostname,
                                username=username,
                                key_path=management_key_path,
                                ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                                inst_cfg=recovery_cfg,
                                runtime_binding=vm_ha_runtime_binding,
                                expected_apply_locked=True,
                                expected_operation_id=operation_id,
                            )
                        except Exception as recovery_error:
                            raise _VMHAActivationUnsafe(
                                "the lifecycle remained at the exact ACTIVATING "
                                "predecessor, but exact apply-lock recovery failed for "
                                f"{node_id}: {recovery_error}; later relocking was not "
                                "attempted"
                            ) from recovery_error
                    raise _VMHAActivationSafelyBlocked(
                        "the final ACTIVE lifecycle state did not persist; passive and "
                        "active exact-operation apply locks were restored and verified"
                    ) from transition_error
                else:
                    raise _VMHAActivationUnsafe(
                        "final ACTIVE persistence failed and the lifecycle record is "
                        "neither the exact ACTIVE successor nor ACTIVATING predecessor"
                    ) from transition_error
        except _VMHAActivationSafelyBlocked as error:
            print("[red]VM-HA activation stopped before durable ACTIVE completion.[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
        except _VMHAActivationUnsafe as error:
            print("[red]VM-HA activation recovery is unsafe and requires inspection.[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
        except (OSError, RuntimeError, ValueError) as error:
            print(
                "[red]VM-HA activation verification failed; inspect exact node lock and status state before retrying.[/red]"
            )
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error

    print("[green]Apply completed successfully.[/green]")
    if show_add_routes_hint:
        print("")
        print("[yellow]⚠️  IMPORTANT: For static routing, run:[/yellow]")
        print(
            f"[bold]  nebius-vpngw add-routes-local --local-config-file {local_config_file}[/bold]"
        )
        print(
            "[dim]This creates Nebius VPC routes for remote prefixes using the gateway's static private IP allocations.[/dim]"
        )


@app.command(
    options_metavar="",
    epilog=_command_help_epilog("validate-config"),
)
def validate_config(
    config_file: Path = typer.Argument(
        ..., exists=True, readable=True, help="Path to configuration file to validate"
    ),
):
    """Validate configuration file against schema without deploying.

    This command performs comprehensive validation including:
    - Schema compliance (correct structure, no unknown fields)
    - Type checking (strings, numbers, booleans, lists)
    - Field constraints (IP addresses, CIDRs, ASN ranges)
    - Logical consistency (BGP mode requires remote_asn, etc.)
    - Resource quotas (connections, tunnels within limits)

    """
    from rich.console import Console
    from rich.panel import Panel

    from .config_loader import load_local_config

    console = Console()

    try:
        console.print(f"[bold]Validating configuration: {config_file}[/bold]")

        # Load and validate (this will trigger schema validation)
        local_cfg = load_local_config(config_file)

        # Extract key metrics for summary
        connections_count = len(local_cfg.get("connections", []))
        tunnels_count = sum(len(c.get("tunnels", [])) for c in local_cfg.get("connections", []))
        instance_count = local_cfg.get("gateway_group", {}).get("instance_count", 1)

        # Success message with summary
        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Configuration is valid![/bold green]\n\n"
                f"[dim]Summary:[/dim]\n"
                f"  • Gateway instances: {instance_count}\n"
                f"  • Connections: {connections_count}\n"
                f"  • Tunnels: {tunnels_count}\n"
                f"  • Schema version: v{local_cfg.get('version', 1)}",
                title="[green]Validation Passed[/green]",
                border_style="green",
            )
        )
        console.print()
        console.print(
            "[dim]You can now run 'nebius-vpngw apply' to deploy this configuration.[/dim]"
        )

    except ValueError as e:
        # Schema validation errors or missing env vars
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Configuration validation failed[/bold red]\n\n{str(e)}",
                title="[red]Validation Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e

    except Exception as e:
        # Unexpected errors
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Unexpected error during validation[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e


@app.command(
    options_metavar="",
    epilog=_command_help_epilog("create-config"),
)
def create_config(
    config_file: Path = typer.Argument(
        ..., help="Path for new configuration file (recommended: *.config.yaml)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing file if it exists"),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Run the guided wizard even when input/output are not terminals",
    ),
    no_interactive: bool = typer.Option(
        False,
        "--no-interactive",
        help="Write the existing commented template without prompting for config values",
    ),
):
    """Create a configuration with the guided wizard or existing template.

    Interactive terminals use the schema-backed wizard. Non-interactive invocations keep
    writing the existing comprehensive template. Use --interactive to force the wizard or
    --no-interactive to force template generation.

    Security best practice: Use *.config.yaml extension - these files are git-ignored
    automatically to prevent committing sensitive information (IPs, ASNs, secrets).

    Safe to rerun: if the target file already contains the exact generated
    template, the command exits successfully without rewriting it.

    """
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    if interactive and no_interactive:
        console.print("[red]--interactive and --no-interactive cannot be used together.[/red]")
        raise typer.Exit(code=2)

    use_wizard = interactive or (
        not no_interactive and bool(sys.stdin.isatty()) and bool(sys.stdout.isatty())
    )
    wizard_target_fingerprint = _file_fingerprint(config_file) if use_wizard else None

    desired_text = _normalize_file_text(DEFAULT_CONFIG_TEMPLATE)

    # Check if file exists
    if config_file.exists() and not force:
        existing_text = _normalize_file_text(config_file.read_text(encoding="utf-8"))
        if existing_text == desired_text:
            console.print()
            console.print(
                Panel.fit(
                    f"[bold green]✓ Configuration template already up to date[/bold green]\n\n"
                    f"File: [cyan]{config_file}[/cyan]",
                    title="[green]No Changes[/green]",
                    border_style="green",
                )
            )
            raise typer.Exit(code=0)
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ File already exists[/bold red]\n\n"
                f"Path: {config_file}\n\n"
                f"Use --force to overwrite, or choose a different filename.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    # Warn if not using .config.yaml extension
    if not str(config_file).endswith(".config.yaml"):
        console.print()
        console.print(
            Panel.fit(
                f"[bold yellow]⚠️  Security Warning[/bold yellow]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"The filename does not end with [bold].config.yaml[/bold]\n\n"
                f"Files matching [bold]*.config.yaml[/bold] are automatically git-ignored to prevent\n"
                f"committing sensitive information (public IPs, ASNs, PSKs).\n\n"
                f"[bold red]This file may be tracked by git and could expose secrets![/bold red]\n\n"
                f"[dim]Recommended: Use a .config.yaml extension (e.g., {config_file.stem}.config.yaml)[/dim]",
                title="[yellow]⚠️  Not Git-Ignored[/yellow]",
                border_style="yellow",
            )
        )

        # Ask for confirmation
        console.print()
        proceed = typer.confirm("Do you want to proceed anyway?", default=False)
        if not proceed:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
        console.print()

    if use_wizard:
        try:
            wizard_yaml = run_config_wizard(console, config_file)
            _atomic_write_text(
                config_file,
                wizard_yaml,
                expected_fingerprint=wizard_target_fingerprint,
            )
        except WizardCancelled:
            console.print("[yellow]Cancelled. No configuration file was written.[/yellow]")
            raise typer.Exit(code=0) from None
        except WizardInterrupted:
            console.print("[red]Input ended. No configuration file was written.[/red]")
            raise typer.Exit(code=130) from None
        except WizardValidationError as e:
            console.print()
            console.print(
                Panel.fit(
                    f"[bold red]✗ Configuration wizard validation failed[/bold red]\n\n{e}",
                    title="[red]Validation Error[/red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1) from e
        except Exception as e:
            console.print()
            console.print(
                Panel.fit(
                    f"[bold red]✗ Failed to create configuration file[/bold red]\n\n{e}",
                    title="[red]Error[/red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1) from e

        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Wizard configuration created[/bold green]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"The file is schema-valid and all PSKs are environment references.\n\n"
                f"[dim]Next steps:[/dim]\n"
                f"  1. Export the referenced PSK environment variables\n"
                f"  2. Validate: [cyan]nebius-vpngw validate-config {config_file}[/cyan]\n"
                f"  3. Deploy: [cyan]nebius-vpngw apply --local-config-file {config_file}[/cyan]",
                title="[green]Success[/green]",
                border_style="green",
            )
        )
        console.print()
        console.print(
            Panel.fit(
                "Network preparation authenticates to Nebius and may ensure/create the gateway "
                "subnet, its dedicated route table, and public IP allocations. When public IPs "
                "are automatic, it also updates gateway_group.external_ips in this YAML file.",
                title="Optional cloud operation",
                border_style="yellow",
            )
        )
        try:
            prepare_now = typer.confirm("Prepare gateway networking now?", default=False)
        except (typer.Abort, EOFError, KeyboardInterrupt):
            console.print(
                "[yellow]Network preparation skipped; the validated configuration remains saved.[/yellow]"
            )
            raise typer.Exit(code=0) from None
        if prepare_now:
            _run_network_preparation(config_file, zone=None, console=console)
        else:
            console.print(
                f"[dim]Skipped. Run nebius-vpngw prep-network -c {config_file} later.[/dim]"
            )
        return

    # Create the config file using the backward-compatible template path.
    try:
        config_file.write_text(desired_text, encoding="utf-8")

        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Configuration template created[/bold green]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"[dim]Next steps:[/dim]\n"
                f"  1. Edit file to set project context (tenant_id, project_id, region_id)\n"
                f"  2. Configure gateway networking and VMs\n"
                f"  3. Define connections and tunnels with peer details\n"
                f"  4. Set secrets via environment variables or directly in YAML\n"
                f"  5. Validate: [cyan]nebius-vpngw validate-config {config_file}[/cyan]\n"
                f"  6. Deploy: [cyan]nebius-vpngw apply --local-config-file {config_file}[/cyan]",
                title="[green]Success[/green]",
                border_style="green",
            )
        )

        # Additional warning for non-.config.yaml files
        if not str(config_file).endswith(".config.yaml"):
            console.print()
            console.print(
                "[bold red]Remember: This file is NOT git-ignored. Do not commit secrets![/bold red]"
            )

    except typer.Exit:
        raise
    except Exception as e:
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to create configuration file[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e


def _default_vm_ha_candidate_path(source: Path) -> Path:
    name = source.name
    if name.endswith(".config.yaml"):
        return source.with_name(f"{name[: -len('.config.yaml')]}.vm-ha.config.yaml")
    if source.suffix.lower() in {".yaml", ".yml"}:
        return source.with_name(f"{source.stem}.vm-ha{source.suffix}")
    return source.with_name(f"{name}.vm-ha.config.yaml")


def _read_safe_yaml_mapping(path: Path, *, label: str) -> tuple[dict[str, t.Any], _FileFingerprint]:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} does not exist.") from error
    if stat.S_ISLNK(before.st_mode):
        raise ValueError(f"{label} must not be a symbolic link.")
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file.")
    raw_bytes = path.read_bytes()
    after = path.lstat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f"{label} changed while it was being read; rerun the command.")
    try:
        loaded = yaml.safe_load(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"{label} is not valid UTF-8 YAML.") from error
    if not isinstance(loaded, dict):
        raise ValueError(f"{label} must contain a YAML mapping.")
    fingerprint = _FileFingerprint(
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
    return t.cast(dict[str, t.Any], loaded), fingerprint


def _safe_destination_fingerprint(
    source: Path,
    destination: Path,
) -> _FileFingerprint | None:
    source_resolved = source.resolve(strict=True)
    destination_resolved = destination.resolve(strict=False)
    if source_resolved == destination_resolved:
        raise ValueError("The VM-HA candidate must use a different path from the source.")
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("The VM-HA candidate destination must not be a symbolic link.")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("The VM-HA candidate destination must be a regular file.")
    source_metadata = source.lstat()
    if (metadata.st_dev, metadata.st_ino) == (
        source_metadata.st_dev,
        source_metadata.st_ino,
    ):
        raise ValueError("The VM-HA candidate destination must not be a hard link to the source.")
    return _file_fingerprint(destination)


def _resolve_cloud_field(value: t.Any, *, field: str, required: bool) -> str | None:
    normalized = str(value or "").strip()
    match = _ENV_PATTERN.fullmatch(normalized)
    if match:
        normalized = str(os.environ.get(match.group(1), "")).strip()
    if "${" in normalized:
        normalized = ""
    if not normalized:
        if required:
            raise ValueError(
                f"{field} must resolve before passive allocation preparation; set it in YAML "
                "or export its environment variable."
            )
        return None
    return normalized


def _reserve_vm_ha_passive_public_ip(
    source: dict[str, t.Any],
    *,
    zone: str | None,
) -> str:
    """Reserve only the deterministic instance-1 public allocation."""

    semantic_source = resolve_vm_ha_conversion_source(source)
    group = t.cast(dict[str, t.Any], semantic_source["gateway_group"])
    tenant_id = _resolve_cloud_field(
        semantic_source.get("tenant_id"), field="tenant_id", required=False
    )
    project_id = _resolve_cloud_field(
        semantic_source.get("project_id"), field="project_id", required=True
    )
    region_id = _resolve_cloud_field(
        semantic_source.get("region_id"), field="region_id", required=False
    )
    group_region = _resolve_cloud_field(
        group.get("region"), field="gateway_group.region", required=False
    )
    network_id = _resolve_cloud_field(
        group.get("network_id"), field="gateway_group.network_id", required=False
    )
    spec = GatewayGroupSpec(
        name=str(group["name"]),
        instance_count=2,
        region=group_region or region_id or "eu-north1-a",
        external_ips=[],
        subnet=t.cast(dict[str, t.Any], group.get("subnet") or {}),
        vm_spec=t.cast(dict[str, t.Any], group.get("vm_spec") or {}),
        network_id=network_id,
    )
    auth_token = _ensure_authentication(required=True, show_progress=True)
    manager = VMManager(
        project_id=project_id,
        zone=zone or spec.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )
    allocated = manager.prepare_public_allocations(
        spec,
        instance_indices={1},
        desired_external_ips=[],
        require_unattached=True,
    )
    passive_row = allocated.get(1) or []
    if len(passive_row) != 1 or not passive_row[0]:
        raise RuntimeError("Nebius returned no deterministic passive public allocation.")
    return passive_row[0]


def _vm_ha_wizard_streams_interactive() -> bool:
    return bool(sys.stdin.isatty()) and bool(sys.stdout.isatty())


@app.command(
    name="configure-vm-ha",
    options_metavar="",
    epilog=_command_help_epilog("configure-vm-ha"),
)
def configure_vm_ha(
    local_config_file: Path = typer.Option(
        ...,
        "--local-config-file",
        "-c",
        help="Existing ordinary single-VM configuration",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="New VM-HA candidate path (default: SOURCE with .vm-ha before .config.yaml)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace an existing nonmatching candidate only after final confirmation",
    ),
    zone: str | None = typer.Option(
        None,
        help="Nebius zone used only when reserving the passive public IP",
    ),
):
    """Guide a supported ordinary gateway into an explicit VM-HA candidate.

    The source is never modified. Phase 1 preflights both operator credential
    bundles, derives the passive member, and prints the peer handoff, optionally
    reserving only member 1's public IP. Phase 2 writes a complete schema-v1
    candidate after the peer endpoints are ready. Deployment, migration
    approval, fencing, activation, and recovery remain in the existing apply
    command.
    """
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    if not _vm_ha_wizard_streams_interactive():
        console.print(
            "[red]configure-vm-ha requires an interactive terminal. No file or cloud resource was changed.[/red]"
        )
        raise typer.Exit(code=1)

    destination = output or _default_vm_ha_candidate_path(local_config_file)
    reserved_ip: str | None = None
    reservation_attempted = False
    reservation_completed = False
    passive_allocation_name: str | None = None
    try:
        source, source_fingerprint = _read_safe_yaml_mapping(
            local_config_file,
            label="The source configuration",
        )
        validate_vm_ha_conversion_source(source)
        semantic_source = resolve_vm_ha_conversion_source(source)
        _enforce_command_applicability(
            "configure-vm-ha",
            merge_with_peer_configs(semantic_source, []),
            semantic_source,
        )
        destination_fingerprint = _safe_destination_fingerprint(
            local_config_file,
            destination,
        )
        if destination_fingerprint is not None:
            existing, existing_fingerprint = _read_safe_yaml_mapping(
                destination,
                label="The VM-HA candidate destination",
            )
            if existing_fingerprint != destination_fingerprint:
                raise ValueError(
                    "The VM-HA candidate destination changed while it was being inspected; "
                    "rerun the command."
                )
            if is_vm_ha_conversion_candidate(source, existing):
                mode = stat.S_IMODE(destination.lstat().st_mode)
                if mode != 0o600 and not force:
                    raise ValueError(
                        "The existing exact VM-HA candidate is not mode 0600; rerun with --force "
                        "to republish it safely."
                    )
                if mode == 0o600:
                    if _file_fingerprint(destination) != existing_fingerprint:
                        raise ValueError(
                            "The VM-HA candidate destination changed before the no-op check; "
                            "rerun the command."
                        )
                    console.print(
                        Panel.fit(
                            f"[bold green]VM-HA candidate already up to date[/bold green]\n\n"
                            f"File: [cyan]{destination}[/cyan]\n"
                            "The ordinary source remains unchanged.",
                            title="[green]No Changes[/green]",
                            border_style="green",
                        )
                    )
                    raise typer.Exit(code=0)
            if not force:
                raise ValueError(
                    "The VM-HA candidate destination already exists and does not match this "
                    "conversion. Choose another path or use --force."
                )

        def reserve_passive_ip() -> str:
            nonlocal passive_allocation_name
            nonlocal reservation_attempted, reserved_ip, reservation_completed
            if _file_fingerprint(local_config_file) != source_fingerprint:
                raise OSError(
                    "The source configuration changed before cloud preparation; no cloud "
                    "operation or candidate write was attempted."
                )
            semantic_group = resolve_vm_ha_conversion_source(source)["gateway_group"]
            passive_allocation_name = f"{semantic_group['name']}-1-eth0-ip"
            reservation_attempted = True
            reserved_ip = _reserve_vm_ha_passive_public_ip(source, zone=zone)
            reservation_completed = True
            return reserved_ip

        result: VMHAConversionResult = run_vm_ha_conversion_wizard(
            console,
            source,
            destination,
            reserve_passive_ip=reserve_passive_ip,
        )
        if result.yaml_text is None:
            raise typer.Exit(code=0)
        if _file_fingerprint(local_config_file) != source_fingerprint:
            raise OSError(
                "The source configuration changed while the wizard was running; no candidate was written."
            )
        _conditional_publish_text(
            destination,
            result.yaml_text,
            expected_fingerprint=destination_fingerprint,
        )
    except typer.Exit:
        raise
    except WizardCancelled:
        console.print(
            "[yellow]Cancelled. The source is unchanged and no candidate was written.[/yellow]"
        )
        if reservation_completed and reserved_ip:
            console.print(
                f"[yellow]The passive public IP {reserved_ip} remains allocated and will be reused.[/yellow]"
            )
        elif reservation_attempted and passive_allocation_name:
            console.print(
                "[yellow]The passive allocation request may have been accepted. "
                f"{passive_allocation_name} may remain allocated; rerun to resolve and reuse it. "
                "No rollback is claimed.[/yellow]"
            )
        raise typer.Exit(code=0) from None
    except WizardInterrupted:
        console.print(
            "[red]Input ended. The source is unchanged and no candidate was written.[/red]"
        )
        if reservation_completed and reserved_ip:
            console.print(
                f"[yellow]The passive public IP {reserved_ip} remains allocated and will be reused.[/yellow]"
            )
        elif reservation_attempted and passive_allocation_name:
            console.print(
                "[yellow]The passive allocation request may have been accepted. "
                f"{passive_allocation_name} may remain allocated; rerun to resolve and reuse it. "
                "No rollback is claimed.[/yellow]"
            )
        raise typer.Exit(code=130) from None
    except (WizardValidationError, ValueError, OSError, RuntimeError) as error:
        console.print(
            Panel.fit(
                f"[bold red]VM-HA candidate was not written[/bold red]\n\n{error}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        if reservation_completed and reserved_ip:
            console.print(
                f"[yellow]The passive public IP {reserved_ip} remains allocated and will be reused; no rollback is claimed.[/yellow]"
            )
        elif reservation_attempted and passive_allocation_name:
            console.print(
                "[yellow]The passive allocation request may have been accepted. "
                f"{passive_allocation_name} may remain allocated; rerun to resolve and reuse it. "
                "No rollback is claimed.[/yellow]"
            )
        raise typer.Exit(code=1) from error

    console.print(
        Panel.fit(
            f"[bold green]Complete VM-HA candidate created[/bold green]\n\n"
            f"File: [cyan]{destination}[/cyan]\n"
            "The ordinary source and member 0 were preserved. No deployment was performed.\n\n"
            "[dim]Next steps:[/dim]\n"
            f"  1. Validate credentials and config: [cyan]nebius-vpngw validate-config {destination}[/cyan]\n"
            f"  2. Preview migration: [cyan]nebius-vpngw apply --local-config-file {destination} --dry-run[/cyan]\n"
            "  3. Review the exact retained-active migration plan and digest\n"
            f"  4. Apply interactively: [cyan]nebius-vpngw apply --local-config-file {destination}[/cyan]\n"
            "     or use the exact digest approval printed by the preview/apply workflow.",
            title="[green]Success[/green]",
            border_style="green",
        )
    )


@dataclass(frozen=True)
class _NetworkPreparationResult:
    name: str
    allocated_ips: list[list[str]]
    used_assigned_ips: bool
    yaml_updated: bool


class _NetworkPreparationFailure(Exception):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        name: str | None = None,
        allocated_ips: list[list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.name = name
        self.allocated_ips = allocated_ips


def _prepare_network_config(
    local_config_file: Path,
    *,
    zone: str | None,
) -> _NetworkPreparationResult:
    """Own the shared cloud-preparation path used by both CLI entry points."""
    try:
        cfg = load_local_config(
            local_config_file,
            allow_missing_placeholders=True,
            validate_schema=False,
        )
    except Exception as error:
        raise _NetworkPreparationFailure("load", str(error)) from error

    tenant_id = str(cfg.get("tenant_id") or "").strip() or None
    project_id = str(cfg.get("project_id") or "").strip() or None
    region_id = str(cfg.get("region_id") or "").strip() or None
    if not project_id or "${" in project_id:
        raise _NetworkPreparationFailure(
            "project",
            "Set project_id directly in YAML or via ${PROJECT_ID} env var.",
        )

    gg = cfg.get("gateway_group", {}) or {}
    name = gg.get("name") or "nebius-vpn-gw"
    try:
        instance_count = int(gg.get("instance_count", 1))
    except (TypeError, ValueError) as error:
        raise _NetworkPreparationFailure("instance_count", "instance_count must be >= 1") from error
    if instance_count < 1:
        raise _NetworkPreparationFailure("instance_count", "instance_count must be >= 1")

    vm_spec = gg.get("vm_spec", {}) or {}
    external_ips = gg.get("external_ips", []) or []
    network_id = str(gg.get("network_id") or "").strip() or None
    subnet = gg.get("subnet", {}) or {}
    has_assigned_ips = _external_ips_assigned(external_ips)
    spec = GatewayGroupSpec(
        name=name,
        instance_count=instance_count,
        region=gg.get("region") or region_id or "eu-north1-a",
        external_ips=external_ips,
        subnet=subnet,
        vm_spec=vm_spec,
        network_id=network_id,
    )

    auth_token = _ensure_authentication(required=True, show_progress=True)
    vm_mgr = VMManager(
        project_id=project_id,
        zone=zone or spec.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )
    try:
        allocated_ips = vm_mgr.prepare_network(
            spec,
            allocate_ips=True,
            desired_external_ips=external_ips if has_assigned_ips else [],
        )
    except Exception as error:
        raise _NetworkPreparationFailure("prepare", str(error)) from error

    if has_assigned_ips:
        return _NetworkPreparationResult(
            name=name,
            allocated_ips=allocated_ips,
            used_assigned_ips=True,
            yaml_updated=False,
        )
    if not allocated_ips:
        raise _NetworkPreparationFailure("no_ips", "No public IPs were allocated.")

    try:
        _update_external_ips_in_yaml(local_config_file, allocated_ips)
    except Exception as error:
        raise _NetworkPreparationFailure(
            "yaml_update",
            str(error),
            name=name,
            allocated_ips=allocated_ips,
        ) from error
    return _NetworkPreparationResult(
        name=name,
        allocated_ips=allocated_ips,
        used_assigned_ips=False,
        yaml_updated=True,
    )


def _run_network_preparation(
    local_config_file: Path,
    *,
    zone: str | None,
    console: t.Any,
) -> _NetworkPreparationResult:
    """Run and render one preparation attempt while preserving legacy CLI messages."""
    from rich.panel import Panel

    try:
        result = _prepare_network_config(local_config_file, zone=zone)
    except _NetworkPreparationFailure as error:
        if error.stage == "yaml_update" and error.allocated_ips:
            console.print()
            console.print("[bold]Reserved public IPs:[/bold]")
            for inst_index, inst_ips in enumerate(error.allocated_ips):
                for nic_index, ip in enumerate(inst_ips):
                    console.print(
                        f"  - {error.name}-{inst_index} eth{nic_index}: [cyan]{ip}[/cyan]"
                    )
        if error.stage == "load":
            heading = "✗ Failed to load configuration"
        elif error.stage == "project":
            heading = "✗ project_id is required for prep-network"
        elif error.stage == "instance_count":
            heading = "✗ instance_count must be >= 1"
        elif error.stage == "prepare":
            heading = "✗ Failed to prepare network"
        elif error.stage == "no_ips":
            heading = "✗ No public IPs were allocated."
        else:
            heading = "✗ Failed to update YAML with allocated IPs"
        body = f"[bold red]{heading}[/bold red]"
        if str(error) and str(error) != heading.removeprefix("✗ "):
            body += f"\n\n{error}"
        console.print(Panel.fit(body, title="[red]Error[/red]", border_style="red"))
        raise typer.Exit(code=1) from error

    if result.used_assigned_ips:
        console.print(
            Panel.fit(
                "[bold green]external_ips set in YAML.[/bold green]\n\n"
                "Subnet/route table were ensured and requested IP allocations were verified/created.",
                title="[green]Prep Completed[/green]",
                border_style="yellow",
            )
        )
        console.print()
        console.print("[bold]Public IPs:[/bold]")
    else:
        console.print()
        console.print("[bold]Reserved public IPs:[/bold]")
    for inst_index, inst_ips in enumerate(result.allocated_ips):
        for nic_index, ip in enumerate(inst_ips):
            console.print(f"  - {result.name}-{inst_index} eth{nic_index}: [cyan]{ip}[/cyan]")

    if result.yaml_updated:
        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Updated config with allocated IPs[/bold green]\n\n"
                f"File: [cyan]{local_config_file}[/cyan]",
                title="[green]Success[/green]",
                border_style="green",
            )
        )
    return result


@app.command(
    name="prep-network",
    epilog=_command_help_epilog("prep-network"),
)
def prep_network(
    local_config_file: Path | None = typer.Option(
        None, "--local-config-file", "-c", help="Path to local config file"
    ),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
):
    """Prepare gateway networking before peer setup.

    Safe to rerun. Ensures the configured gateway subnet, its dedicated route
    table, and the requested public IP allocations exist without recreating
    matching resources.
    """
    from rich.console import Console

    console = Console()
    resolved_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    _run_network_preparation(resolved_config_file, zone=zone, console=console)


@app.command(
    options_metavar="",
    epilog=_command_help_epilog("create-from-peer-config"),
)
def create_from_peer_config(
    config_file: Path | None = typer.Argument(
        None,
        help=(
            "Path for new configuration file "
            f"(default: ./{DEFAULT_CONFIG_FILENAME}; recommended: *.config.yaml)"
        ),
    ),
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help=("Output local config file path. Alias for CONFIG_FILE on this command."),
    ),
    peer_config_file: list[Path] = typer.Option(
        ...,
        exists=True,
        readable=True,
        help="Peer config file(s). Supported formats: .txt, .csv, .json, .yaml, .yml",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing file if it exists"),
):
    """Create a schema-aligned configuration from keyword-imported peer inputs.

    This generates a standalone YAML config file from `.txt`, `.csv`, `.json`,
    `.yaml`, or `.yml` peer inputs using the shared keyword-based importer.
    The generated output is validated against the config schema before write.

    Safe to rerun: if the target file already contains the exact generated
    output for the same inputs, the command exits successfully without
    rewriting it.
    """
    import yaml
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    if (
        config_file is not None
        and local_config_file is not None
        and config_file != local_config_file
    ):
        console.print()
        console.print(
            Panel.fit(
                "[bold red]✗ Conflicting output file arguments[/bold red]\n\n"
                f"CONFIG_FILE: [cyan]{config_file}[/cyan]\n"
                f"--local-config-file: [cyan]{local_config_file}[/cyan]\n\n"
                "Provide only one output path, or pass the same value to both.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    config_file = local_config_file or config_file or (Path.cwd() / DEFAULT_CONFIG_FILENAME)

    if not peer_config_file:
        console.print(
            Panel.fit(
                "[bold red]✗ No peer config file provided[/bold red]\n\n"
                "Use --peer-config-file to specify at least one input file.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    if not str(config_file).endswith(".config.yaml"):
        console.print()
        console.print(
            Panel.fit(
                f"[bold yellow]⚠️  Security Warning[/bold yellow]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"The filename does not end with [bold].config.yaml[/bold]\n\n"
                f"Files matching [bold]*.config.yaml[/bold] are automatically git-ignored to prevent\n"
                f"committing sensitive information (public IPs, ASNs, PSKs).\n\n"
                f"[bold red]This file may be tracked by git and could expose secrets![/bold red]\n\n"
                f"[dim]Recommended: Use a .config.yaml extension (e.g., {config_file.stem}.config.yaml)[/dim]",
                title="[yellow]⚠️  Not Git-Ignored[/yellow]",
                border_style="yellow",
            )
        )
        console.print()
        proceed = typer.confirm("Do you want to proceed anyway?", default=False)
        if not proceed:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
        console.print()

    try:
        base_cfg = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE) or {}
        merged_cfg = build_config_from_peer_files(base_cfg, peer_config_file)
        from .schema import validate_config

        validate_config(merged_cfg)
        desired_text = _normalize_file_text(yaml.safe_dump(merged_cfg, sort_keys=False))

        if merged_cfg == base_cfg:
            console.print(
                "[yellow]⚠️  The importer did not detect meaningful peer fields. "
                "Review the input file and fill in any missing values manually.[/yellow]"
            )

        if config_file.exists() and not force:
            existing_text = _normalize_file_text(config_file.read_text(encoding="utf-8"))
            if existing_text == desired_text:
                console.print()
                console.print(
                    Panel.fit(
                        f"[bold green]✓ Peer-generated configuration already up to date[/bold green]\n\n"
                        f"File: [cyan]{config_file}[/cyan]",
                        title="[green]No Changes[/green]",
                        border_style="green",
                    )
                )
                raise typer.Exit(code=0)
            console.print()
            console.print(
                Panel.fit(
                    f"[bold red]✗ File already exists[/bold red]\n\n"
                    f"Path: {config_file}\n\n"
                    f"Use --force to overwrite, or choose a different filename.",
                    title="[red]Error[/red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)

        config_file.write_text(desired_text, encoding="utf-8")

        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Configuration created from peer config[/bold green]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"[dim]Next steps:[/dim]\n"
                f"  1. Review and replace any placeholders (tenant/project/region/PSKs)\n"
                f"  2. Validate: [cyan]nebius-vpngw validate-config {config_file}[/cyan]\n"
                f"  3. Deploy: [cyan]nebius-vpngw apply --local-config-file {config_file}[/cyan]",
                title="[green]Success[/green]",
                border_style="green",
            )
        )

        if not str(config_file).endswith(".config.yaml"):
            console.print()
            console.print(
                "[bold red]Remember: This file is NOT git-ignored. Do not commit secrets![/bold red]"
            )

    except typer.Exit:
        raise
    except Exception as e:
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to create configuration file[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e


def _vm_ha_status_runtime_binding(state: VMHALifecycleState) -> SimpleNamespace:
    """Project the immutable lifecycle identity needed for agent status validation."""

    if state.status not in {VMHALifecycleStatus.ACTIVATING, VMHALifecycleStatus.ACTIVE}:
        raise ValueError("VM-HA lifecycle has not reached an authoritative runtime binding")
    if not state.allocation_id or not state.route_runtime_id:
        raise ValueError("VM-HA lifecycle runtime binding is incomplete")
    return SimpleNamespace(
        cluster_id=state.cluster_id,
        route_runtime_id=state.route_runtime_id,
        shared_allocation_id=state.allocation_id,
    )


@dataclass(frozen=True)
class _VMHACloudAuthority:
    lifecycle: str
    condition: str
    owner_name: str | None
    owner_node_id: str | None
    operation_id: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class _VMHAMemberEvidence:
    name: str
    configured_role: str
    node_id: str
    condition: str
    reason: str
    record: t.Mapping[str, t.Any] | None = None


@dataclass(frozen=True)
class _VMHAStatusView:
    overall: str
    summary_rows: tuple[tuple[str, str, str], ...]
    member_rows: tuple[tuple[str, str, str, str], ...]


def _dedupe_vm_ha_reasons(values: t.Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _safe_vm_ha_reason(value: object) -> str:
    """Return only a known identity-free controller or rearm reason code."""

    text = value if isinstance(value, str) else ""
    if text in _VM_HA_SAFE_REASON_CODES:
        return text
    return "controller-reported-condition"


def _vm_ha_pending_action_kind(
    value: object,
    *,
    member_node_ids: frozenset[str],
) -> str | None:
    """Parse only a controller-generated, configured-member operation identity."""

    if not isinstance(value, str):
        return None
    parts = value.rsplit(":", 3)
    if len(parts) != 4:
        return None
    boot_id, sequence, action_kind, target_node_id = parts
    if not (
        boot_id
        and sequence.isascii()
        and sequence.isdecimal()
        and int(sequence) > 0
        and target_node_id in member_node_ids
    ):
        return None
    return action_kind


def _vm_ha_cloud_authority(
    state: VMHALifecycleState,
    observation: t.Mapping[str, object],
) -> _VMHACloudAuthority:
    """Normalize lifecycle and cloud truth without retaining displayable identities."""

    raw_members = observation.get("members")
    raw_shared = observation.get("shared_allocation")
    raw_targets = observation.get("route_targets")
    raw_routes = observation.get("routes")
    if not (
        isinstance(raw_members, list)
        and all(isinstance(member, dict) for member in raw_members)
        and isinstance(raw_shared, dict)
        and isinstance(raw_targets, list)
        and all(isinstance(target, dict) for target in raw_targets)
        and isinstance(raw_routes, list)
        and all(isinstance(route, dict) for route in raw_routes)
    ):
        raise ValueError("VM-HA cloud observation is malformed")

    transaction = state.transaction
    operation_id = transaction.operation_id if transaction and transaction.pending_effect else None
    lifecycle_transition = state.status in {
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
        VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
    }
    blocked: list[str] = []
    transitioning: list[str] = []
    if state.status is VMHALifecycleStatus.REMOVED:
        blocked.append("lifecycle-removed")
    elif lifecycle_transition:
        transitioning.append(f"lifecycle-{state.status.value}")
    elif operation_id is not None:
        transitioning.append("lifecycle-operation-pending")

    observed_members: dict[str, t.Mapping[str, object]] = {}
    for raw_member in t.cast(list[dict[str, object]], raw_members):
        name = raw_member.get("instance_name")
        if not isinstance(name, str) or not name or name in observed_members:
            blocked.append("cloud-member-identity-conflict")
            continue
        observed_members[name] = raw_member

    shared = t.cast(dict[str, object], raw_shared)
    shared_exact = (
        shared.get("present") is True and shared.get("allocation_id") == state.allocation_id
    )
    if not shared_exact:
        (transitioning if lifecycle_transition or operation_id else blocked).append(
            "shared-allocation-not-exact"
        )

    owner_name: str | None = None
    owner_node_id: str | None = None
    raw_owner = shared.get("owner")
    if raw_owner is None:
        (transitioning if lifecycle_transition or operation_id else blocked).append(
            "shared-allocation-unattached"
        )
    elif not isinstance(raw_owner, dict):
        blocked.append("shared-allocation-owner-malformed")
    else:
        owner_compute = raw_owner.get("compute_id")
        owner_nic = raw_owner.get("network_interface_name")
        matches = [
            member
            for member in state.members
            if member.compute_id == owner_compute and member.network_interface_name == owner_nic
        ]
        if len(matches) != 1:
            blocked.append("shared-allocation-owner-foreign")
        else:
            owner_name = matches[0].instance_name
            owner_node_id = matches[0].node_id

    for member in state.members:
        observed = observed_members.get(member.instance_name)
        if observed is None or observed.get("present") is not True:
            (transitioning if lifecycle_transition or operation_id else blocked).append(
                "cloud-member-unavailable"
            )
            continue
        if (
            observed.get("compute_id") != member.compute_id
            or observed.get("network_interface_name") != member.network_interface_name
        ):
            blocked.append("cloud-member-identity-conflict")
            continue
        aliases = observed.get("aliases")
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            blocked.append("cloud-member-alias-malformed")
            continue
        has_shared_alias = state.allocation_id in aliases
        if owner_name is not None and has_shared_alias != (member.instance_name == owner_name):
            blocked.append("shared-alias-owner-conflict")
        if owner_name is None and has_shared_alias:
            blocked.append("shared-alias-without-owner")

    observed_targets = {
        json.dumps(target, sort_keys=True, separators=(",", ":"))
        for target in t.cast(list[dict[str, object]], raw_targets)
    }
    target_table_ids = [target.get("route_table_id") for target in raw_targets]
    route_targets_exact = bool(
        observed_targets == set(state.route_targets)
        and target_table_ids
        and all(isinstance(table_id, str) and table_id for table_id in target_table_ids)
        and len(set(target_table_ids)) == len(target_table_ids)
    )
    route_records_exact = True
    route_next_hops_exact = True
    managed_prefixes_by_table: dict[str, set[str]] = {
        t.cast(str, table_id): set() for table_id in target_table_ids if isinstance(table_id, str)
    }
    managed_route_keys: set[tuple[str, str]] = set()
    authority_keys = NebiusSDKRouteBackend._AUTHORITY_LABEL_KEYS
    current_cluster_fingerprint = NebiusSDKRouteBackend._authority_fingerprint(
        state.cluster_id
    )
    current_allocation_fingerprint = NebiusSDKRouteBackend._authority_fingerprint(
        state.allocation_id
    )
    target_fingerprints = {
        str(target.get("route_table_id")): NebiusSDKRouteBackend._authority_fingerprint(
            json.dumps(target, sort_keys=True, separators=(",", ":"))
        )
        for target in t.cast(list[dict[str, object]], raw_targets)
        if isinstance(target.get("route_table_id"), str)
    }
    for route in t.cast(list[dict[str, object]], raw_routes):
        route_table_id = route.get("route_table_id")
        route_name = route.get("name")
        prefix = route.get("prefix")
        allocation_id = route.get("allocation_id")
        if not all(
            isinstance(value, str) for value in (route_table_id, route_name, prefix, allocation_id)
        ):
            route_records_exact = False
            continue
        route_table_id = t.cast(str, route_table_id)
        route_name = t.cast(str, route_name)
        prefix = t.cast(str, prefix)
        raw_labels = route.get("authority_labels")
        if not isinstance(raw_labels, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_labels.items()
        ):
            route_records_exact = False
            continue
        labels = t.cast(dict[str, str], raw_labels)
        present_authority_keys = authority_keys & labels.keys()
        if not route_name.startswith("vpngw-") and not present_authority_keys:
            continue
        labels_well_formed = bool(
            set(labels) == authority_keys
            and labels.get(NebiusSDKRouteBackend._AUTHORITY_MANAGED_LABEL) == "vm-ha-v1"
            and labels.get(NebiusSDKRouteBackend._AUTHORITY_KIND_LABEL) in {"bgp", "static"}
            and all(
                re.fullmatch(r"[0-9a-f]{32}", labels.get(key, ""))
                for key in (
                    NebiusSDKRouteBackend._AUTHORITY_CLUSTER_LABEL,
                    NebiusSDKRouteBackend._AUTHORITY_ALLOCATION_LABEL,
                    NebiusSDKRouteBackend._AUTHORITY_TARGET_LABEL,
                )
            )
        )
        if not labels_well_formed:
            route_records_exact = False
            continue
        if (
            labels.get(NebiusSDKRouteBackend._AUTHORITY_CLUSTER_LABEL)
            != current_cluster_fingerprint
        ):
            continue
        if route_table_id not in managed_prefixes_by_table:
            route_records_exact = False
            continue
        if (
            not route_name.startswith("vpngw-")
            or labels.get(NebiusSDKRouteBackend._AUTHORITY_TARGET_LABEL)
            != target_fingerprints.get(route_table_id)
        ):
            route_records_exact = False
            continue
        route_key = (route_table_id, prefix)
        if not prefix or route_key in managed_route_keys:
            route_records_exact = False
            continue
        managed_route_keys.add(route_key)
        managed_prefixes_by_table[route_table_id].add(prefix)
        if (
            allocation_id != state.allocation_id
            or labels.get(NebiusSDKRouteBackend._AUTHORITY_ALLOCATION_LABEL)
            != current_allocation_fingerprint
        ):
            route_next_hops_exact = False

    required_prefix_sets = tuple(managed_prefixes_by_table.values())
    route_prefixes_exact = bool(
        required_prefix_sets
        and required_prefix_sets[0]
        and not any(prefixes != required_prefix_sets[0] for prefixes in required_prefix_sets[1:])
    )
    route_reasons: list[str] = []
    if not route_targets_exact:
        route_reasons.append("route-targets-not-exact")
    elif not route_records_exact:
        route_reasons.append("route-records-not-exact")
    else:
        if not route_prefixes_exact:
            route_reasons.append("route-prefixes-not-exact")
        if not route_next_hops_exact:
            route_reasons.append("route-next-hop-not-exact")
    if route_reasons:
        (transitioning if lifecycle_transition or operation_id else blocked).extend(route_reasons)

    if blocked:
        condition = "blocked"
        reasons = _dedupe_vm_ha_reasons(blocked)
    elif transitioning:
        condition = "transitioning"
        reasons = _dedupe_vm_ha_reasons(transitioning)
    else:
        condition = "exact"
        reasons = ()
    return _VMHACloudAuthority(
        lifecycle=state.status.value,
        condition=condition,
        owner_name=owner_name,
        owner_node_id=owner_node_id,
        operation_id=operation_id,
        reasons=reasons,
    )


def _vm_ha_unavailable_authority(lifecycle: str, reason: str) -> _VMHACloudAuthority:
    return _VMHACloudAuthority(
        lifecycle=lifecycle,
        condition="unknown",
        owner_name=None,
        owner_node_id=None,
        operation_id=None,
        reasons=(reason,),
    )


def _vm_ha_member_failure_condition(error: Exception) -> tuple[str, str]:
    if isinstance(error, _VMHAStatusSSHUnavailable):
        return "unknown", "ssh-trust-unavailable"
    if isinstance(error, _VMHAAgentStatusStale):
        return "blocked", "agent-status-stale"
    if isinstance(error, _VMHAAgentStatusPermanent):
        message = str(error).lower()
        contradiction = any(
            marker in message
            for marker in (
                "foreign",
                "runtime binding",
                "expected generation",
                "apply-lock",
                "wrong apply",
                "internally inconsistent",
                "conflicts with observed ownership",
            )
        )
        return (
            ("blocked", "agent-status-conflict")
            if contradiction
            else ("unknown", "agent-status-invalid")
        )
    return "unknown", "agent-status-unavailable"


def _vm_ha_record_reasons(record: t.Mapping[str, t.Any]) -> tuple[str, ...]:
    values: list[object] = []
    values.extend(record.get("reasons") or ())
    values.extend(record.get("standby_readiness_reasons") or ())
    if record.get("rearm_reason"):
        values.append(record["rearm_reason"])
    repair = record.get("repair")
    if isinstance(repair, dict):
        values.extend(repair.get("failure_fingerprint") or ())
    return _dedupe_vm_ha_reasons(_safe_vm_ha_reason(value) for value in values)


def _vm_ha_status_view(
    authority: _VMHACloudAuthority,
    members: tuple[_VMHAMemberEvidence, _VMHAMemberEvidence],
    *,
    rearm_command: str,
    mtls_command: str = "nebius-vpngw set-vm-ha-mtls",
) -> _VMHAStatusView:
    """Classify and render one conservative, identity-safe HA status projection."""

    blocked = list(authority.reasons if authority.condition == "blocked" else ())
    unknown = list(authority.reasons if authority.condition == "unknown" else ())
    transitioning = list(authority.reasons if authority.condition == "transitioning" else ())
    degraded: list[str] = []
    exact_members = {member.node_id: member for member in members if member.record is not None}
    generation_identities = {
        (
            member.record.get("generation_id"),
            json.dumps(member.record.get("digests"), sort_keys=True, separators=(",", ":")),
        )
        for member in members
        if member.record is not None
    }
    if len(generation_identities) > 1:
        blocked.append("agent-status-conflict")
    member_node_ids = frozenset(member.node_id for member in members)
    expected_pending_members: set[str] = set()
    mtls_transitioning_members: set[str] = set()
    mtls_states: list[tuple[str, int | None, str | None, str | None, bool]] = []
    for member in members:
        if member.condition == "blocked":
            blocked.append(member.reason)
        elif member.condition == "unknown" and member.node_id != authority.owner_node_id:
            # A proven serving owner permits missing standby evidence to be degraded.
            continue
        elif member.condition == "unknown":
            unknown.append(member.reason)

    promotion_owners = [
        member
        for member in members
        if member.record is not None
        and member.record.get("promotion_ready") is True
        and member.record.get("observed_owner_node_id") == member.node_id
    ]
    if len(promotion_owners) > 1:
        blocked.append("multiple-forwarding-owners")

    for member in members:
        record = member.record
        if record is None:
            continue
        record_state = str(record["state"])
        pending = record.get("pending_operation_id")
        apply_operation = record.get("apply_operation_id")
        repair = record.get("repair")
        mtls = record.get("mtls")
        if not isinstance(mtls, dict):
            blocked.append("managed-mtls-status-unavailable")
            mtls = {}
        mtls_state = str(mtls.get("state") or "invalid")
        mtls_epoch = mtls.get("epoch") if isinstance(mtls.get("epoch"), int) else None
        mtls_fingerprint = (
            str(mtls["certificate_fingerprint"])
            if isinstance(mtls.get("certificate_fingerprint"), str)
            else None
        )
        mtls_phase = str(mtls["phase"]) if isinstance(mtls.get("phase"), str) else None
        mtls_inhibited = mtls.get("inhibited") is True
        mtls_operation_exact = bool(
            mtls.get("operation_kind") == "rotation"
            and isinstance(mtls.get("operation_id"), str)
            and mtls.get("operation_id") == mtls.get("inhibition_operation_id")
            and mtls.get("operation_id") == apply_operation
            and mtls_inhibited
        )
        mtls_states.append(
            (mtls_state, mtls_epoch, mtls_fingerprint, mtls_phase, mtls_inhibited)
        )
        if mtls_state in {"missing", "invalid"}:
            blocked.append(f"managed-mtls-{mtls_state}")
        elif mtls_state == "transitioning" or mtls_inhibited:
            if mtls_operation_exact:
                mtls_transitioning_members.add(member.node_id)
                transitioning.append("managed-mtls-rotation")
            else:
                blocked.append("managed-mtls-transaction-conflict")
        pending_action_kind = _vm_ha_pending_action_kind(
            pending,
            member_node_ids=member_node_ids,
        )
        repair_operation_exact = bool(
            pending is not None
            and isinstance(repair, dict)
            and repair.get("operation_id") == pending
        )
        pending_operation_expected = bool(
            pending is not None
            and pending_action_kind in _VM_HA_PENDING_ACTIONS_BY_STATE.get(record_state, ())
            and (record_state != "repairing" or repair_operation_exact)
        )
        if pending_operation_expected:
            expected_pending_members.add(member.node_id)
        apply_operation_exact = bool(
            authority.operation_id and apply_operation == authority.operation_id
        )
        member_transitioning = bool(
            record_state in {"suspect", "fencing", "ownership-transfer", "promoting", "repairing"}
            or record.get("rearm_phase") == "starting"
            or pending_operation_expected
            or apply_operation_exact
        )
        observed_owner = record.get("observed_owner_node_id")
        if record_state == "blocked":
            blocked.extend(_vm_ha_record_reasons(record) or ("controller-blocked",))
        if record.get("data_plane_mode") == "active" and member.node_id != authority.owner_node_id:
            blocked.append("nonowner-forwarding")
        if authority.owner_node_id is not None and observed_owner != authority.owner_node_id:
            if member_transitioning and (
                authority.condition == "transitioning" or pending_operation_expected
            ):
                transitioning.append("controller-ownership-transition")
            else:
                blocked.append("cloud-controller-owner-conflict")
        if record.get("apply_locked") is True:
            if apply_operation_exact or mtls_operation_exact:
                transitioning.append("controller-operation-pending")
            else:
                blocked.append("unexpected-controller-lock")
        if pending is not None:
            if pending_operation_expected:
                transitioning.append("controller-operation-pending")
            else:
                blocked.append("unexpected-controller-operation")
        if member_transitioning:
            transitioning.append(f"controller-{record_state}")
        if record_state in {"degraded-path", "repair-exhausted", "degraded"}:
            degraded.extend(_vm_ha_record_reasons(record) or (f"controller-{record_state}",))

    owner_member = (
        exact_members.get(authority.owner_node_id) if authority.owner_node_id is not None else None
    )
    owner_record = owner_member.record if owner_member is not None else None
    safe_owner = bool(
        owner_record is not None
        and owner_record.get("promotion_ready") is True
        and owner_record.get("data_plane_mode") == "active"
        and owner_record.get("observed_owner_node_id") == authority.owner_node_id
        and (
            owner_record.get("apply_locked") is False
            or authority.owner_node_id in mtls_transitioning_members
        )
        and owner_record.get("pending_operation_id") is None
    )
    if authority.condition == "exact" and owner_member is None:
        unknown.append("owner-status-unavailable")
    elif authority.condition == "exact" and not safe_owner:
        owner_state = str(owner_record.get("state")) if owner_record is not None else ""
        if (
            owner_state
            not in {"suspect", "fencing", "ownership-transfer", "promoting", "repairing"}
            and authority.owner_node_id not in expected_pending_members
        ):
            blocked.append("authoritative-owner-not-serving")

    standby_members = [member for member in members if member.node_id != authority.owner_node_id]
    standby_member = standby_members[0] if len(standby_members) == 1 else None
    standby_record = standby_member.record if standby_member is not None else None
    standby_ready = bool(
        standby_record is not None
        and standby_record.get("standby_ready") is True
        and standby_record.get("data_plane_mode") == "passive"
        and standby_record.get("observed_owner_node_id") == authority.owner_node_id
        and standby_member is not None
        and (
            standby_record.get("apply_locked") is False
            or standby_member.node_id in mtls_transitioning_members
        )
        and standby_record.get("pending_operation_id") is None
    )
    if authority.condition == "exact" and safe_owner and not standby_ready:
        if standby_member is None or standby_member.record is None:
            degraded.append("standby-status-unavailable")
        else:
            degraded.extend(_vm_ha_record_reasons(standby_member.record) or ("standby-not-ready",))
    if owner_record is not None and owner_record.get("rearm_phase") in {"blocked", "inhibited"}:
        degraded.extend(_vm_ha_record_reasons(owner_record) or ("rearm-not-ready",))

    blocked_reasons = _dedupe_vm_ha_reasons(blocked)
    unknown_reasons = _dedupe_vm_ha_reasons(unknown)
    transitioning_reasons = _dedupe_vm_ha_reasons(transitioning)
    degraded_reasons = _dedupe_vm_ha_reasons(degraded)
    if blocked_reasons:
        overall = "BLOCKED"
        overall_reasons = blocked_reasons
    elif unknown_reasons:
        overall = "UNKNOWN"
        overall_reasons = unknown_reasons
    elif transitioning_reasons:
        overall = "TRANSITIONING"
        overall_reasons = transitioning_reasons
    elif degraded_reasons:
        overall = "DEGRADED"
        overall_reasons = degraded_reasons
    elif authority.condition == "exact" and safe_owner and standby_ready:
        overall = "HEALTHY"
        overall_reasons = ()
    else:
        overall = "UNKNOWN"
        overall_reasons = ("ha-status-incomplete",)

    owner_label = authority.owner_name or (
        "unattached" if authority.condition == "transitioning" else "unknown"
    )
    owner_detail = "cloud authority"
    if owner_member is not None:
        owner_detail = f"configured {owner_member.configured_role}; controller corroborated"
    redundancy_value = (
        "ready"
        if safe_owner and standby_ready
        else "restoring"
        if overall == "TRANSITIONING"
        else "not ready"
        if safe_owner
        else "unknown"
    )
    redundancy_detail = (
        "owner and standby evidence agree"
        if safe_owner and standby_ready
        else "; ".join(overall_reasons[:3]) or "required evidence unavailable"
    )

    rearm_phase = "unknown"
    rearm_detail = "owner status unavailable"
    durations: t.Mapping[str, object] | None = None
    if owner_record is not None:
        rearm_phase = str(owner_record["rearm_phase"])
        rearm_reasons = _vm_ha_record_reasons(owner_record)
        rearm_detail = "; ".join(rearm_reasons) or "no inhibition reported"
        raw_durations = owner_record.get("phase_durations_seconds")
        durations = raw_durations if isinstance(raw_durations, dict) else None
    timing_labels = {
        "preparation": "preparation",
        "detection_repair": "detection/repair",
        "common_cutover": "common cutover",
        "redundancy_restoration": "redundancy restoration",
    }
    timing_detail = ", ".join(
        f"{timing_labels[field]} "
        + (
            "n/a"
            if durations is None or durations.get(field) is None
            else f"{float(t.cast(float, durations[field])):.3f}s"
        )
        for field in _VM_HA_DURATION_FIELDS
    )

    eligible_rearm = bool(
        overall == "DEGRADED"
        and safe_owner
        and standby_member is not None
        and standby_record is not None
        and not standby_ready
    )
    if mtls_transitioning_members:
        action = mtls_command
        action_detail = "resume the exact managed mTLS rotation transaction"
    elif overall == "HEALTHY":
        action = "none"
        action_detail = "no operator action required"
    elif overall == "TRANSITIONING":
        action = "wait"
        action_detail = "allow the current phase to finish, then rerun status"
    elif "routing-hygiene-not-ready" in overall_reasons:
        action = "wait"
        action_detail = (
            "allow the five-minute routing-maintenance cycle to restore standby "
            "hygiene, then rerun status; if drift persists, redeploy through the "
            "supported apply workflow"
        )
    elif eligible_rearm:
        action = rearm_command
        action_detail = "retry standby redundancy restoration"
    elif "route-next-hop-not-exact" in overall_reasons:
        action = "repair-route-authority"
        action_detail = (
            "reconcile managed route next hops through the supported apply workflow, "
            "then rerun status"
        )
    elif any(reason.startswith("route-") for reason in overall_reasons):
        action = "repair-route-authority"
        action_detail = (
            "inspect and reconcile the exact managed route targets through the supported "
            "apply workflow, then rerun status"
        )
    elif "ssh-trust-unavailable" in overall_reasons:
        action = "configure-ssh-trust"
        action_detail = "pin every VM-HA member exactly, then rerun status"
    elif "agent-status-stale" in overall_reasons:
        action = "reconcile-generation"
        action_detail = (
            "redeploy the exact VM-HA generation through the supported apply workflow, "
            "then rerun status"
        )
    else:
        action = "inspect"
        action_detail = "review the safe reason above, then rerun status"

    healthy_mtls = bool(
        len(mtls_states) == 2
        and all(state == "healthy" and not inhibited for state, _, _, _, inhibited in mtls_states)
    )
    mtls_value = "healthy" if healthy_mtls else "rotating" if mtls_transitioning_members else "blocked"
    mtls_detail = "; ".join(
        f"epoch {epoch if epoch is not None else 'unknown'} fp "
        f"{fingerprint[:12] if fingerprint is not None else 'unavailable'} "
        f"phase {phase or state}{' inhibited' if inhibited else ''}"
        for state, epoch, fingerprint, phase, inhibited in mtls_states
    ) or "managed mTLS status unavailable"
    summary_rows = (
        ("Overall", overall, "; ".join(overall_reasons[:3]) or "all required evidence agrees"),
        ("Lifecycle", authority.lifecycle, "; ".join(authority.reasons) or "authoritative"),
        ("Owner", owner_label, owner_detail),
        ("Redundancy", redundancy_value, redundancy_detail),
        ("Rearm", rearm_phase, rearm_detail),
        ("mTLS", mtls_value, mtls_detail),
        ("Timings", "observed", timing_detail),
        ("Action", action, action_detail),
    )

    member_rows: list[tuple[str, str, str, str]] = []
    for member in members:
        record = member.record
        if authority.owner_node_id is None:
            runtime_role = "unknown"
        elif member.node_id == authority.owner_node_id:
            runtime_role = "active"
        else:
            runtime_role = "standby"
        if record is None:
            member_rows.append((member.name, runtime_role, "unknown", "unknown"))
            continue
        is_owner = member.node_id == authority.owner_node_id
        mtls = t.cast(dict[str, t.Any], record["mtls"])
        mtls_state = str(mtls["state"])
        if mtls_state == "healthy" and mtls.get("inhibited") is True:
            mtls_state = "transitioning"
        locally_ready = safe_owner if is_owner else standby_ready
        if overall == "UNKNOWN":
            ready = "unknown"
        elif overall in {"BLOCKED", "TRANSITIONING"}:
            ready = "no"
        else:
            ready = "yes" if locally_ready else "no"
        member_rows.append(
            (
                member.name,
                runtime_role,
                mtls_state,
                ready,
            )
        )
    return _VMHAStatusView(
        overall=overall,
        summary_rows=summary_rows,
        member_rows=tuple(member_rows),
    )


def _render_vm_ha_status(console: t.Any, view: _VMHAStatusView) -> None:
    """Render the sole public VM-HA status section."""

    from rich.table import Table
    from rich.text import Text

    title_style = "bold green" if view.overall == "HEALTHY" else "bold red"
    title = Text.assemble("VM-HA Status — ", (view.overall, title_style))
    member_table = Table(title=title, show_header=True, header_style="bold cyan")
    for column in ("Gateway", "Role", "mTLS", "Ready"):
        member_table.add_column(column, style="white")
    for member_row in view.member_rows:
        gateway, role, mtls, ready = member_row
        member_table.add_row(
            gateway,
            role,
            Text(mtls, style="green" if mtls == "healthy" else "red"),
            Text(ready, style="green" if ready == "yes" else "red"),
        )
    console.print(member_table)


def _vpn_gateway_status_table() -> t.Any:
    """Build the compact primary status table with complete tunnel names."""

    from rich.table import Table

    table = Table(title="VPN Gateway Status", show_header=True, header_style="bold cyan")
    table.add_column("Tunnel", style="white", overflow="fold")
    for column in (
        "Configured Role",
        "Gateway VM",
        "IPsec",
        "BGP",
        "Peer IP",
        "Encryption",
        "BGP Uptime",
    ):
        table.add_column(column, style="white")
    return table


@app.command(epilog=_command_help_epilog("status"))
def status(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
):
    """Show status of VPN tunnels and gateway health."""
    import json
    import subprocess

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    # Use default config if not provided (do not auto-create for status)
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(
        local_config_file,
        allow_missing_tunnel_psk_placeholders=True,
    )
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
    require_local_generation = not has_unresolved_tunnel_psk_placeholders(local_cfg)

    # Resolve context from CLI args or config
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None

    # Get token for API access
    auth_token = _ensure_authentication(required=False, show_progress=True)

    vm_mgr = VMManager(
        project_id=proj_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )

    # Quick check: verify at least one gateway VM exists before attempting SSH
    print("[bold]Checking for gateway VMs...[/bold]")
    from nebius.api.nebius.compute.v1 import (  # type: ignore
        InstanceServiceClient,
        ListInstancesRequest,
    )

    client = vm_mgr._get_client()
    if client and proj_id:
        isc = InstanceServiceClient(client)
        ilist_op = isc.list(ListInstancesRequest(parent_id=proj_id))
        ilist = ilist_op.wait() if hasattr(ilist_op, "wait") else ilist_op

        items: list[t.Any] = []
        if hasattr(ilist, "items"):
            items = list(ilist.items)
        elif hasattr(ilist, "__iter__"):
            items = list(ilist)

        existing_vms = [
            inst
            for inst in items
            if getattr(getattr(inst, "metadata", None), "name", "").startswith(
                f"{plan.gateway_group.name}-"
            )
        ]

        if not existing_vms:
            console.print(
                f"[yellow]No gateway VMs found matching pattern '{plan.gateway_group.name}-*'[/yellow]"
            )
            console.print("[yellow]Run 'nebius-vpngw apply' to create gateway VMs first.[/yellow]")
            raise typer.Exit(0)

    print("[bold]Collecting gateway VM status...[/bold]")
    vm_ips = {}
    for inst_cfg in plan.iter_instance_configs():
        ip = vm_mgr.get_vm_public_ip(inst_cfg.hostname) or (inst_cfg.external_ip or "").strip()
        if ip:
            vm_ips[inst_cfg.hostname] = ip
        else:
            print(
                f"[yellow]Warning: Could not find IP for {inst_cfg.hostname}. "
                "Ensure project_id is correct and/or set gateway_group.external_ips if discovery is blocked.[/yellow]"
            )

    status_ssh_context = _build_status_ssh_context(
        local_cfg,
        plan,
        vm_ips,
        project_id=proj_id,
    )
    if status_ssh_context.unavailable_members:
        affected = ", ".join(sorted(status_ssh_context.unavailable_members))
        console.print(
            "[yellow]Exact VM-HA SSH trust is unavailable for "
            f"{affected}. Run apply with authoritative host-key evidence, or configure "
            "VPNGW_SSH_KNOWN_HOSTS_FILE with exact member pins. Status will not create, "
            "repair, enroll, or bypass host trust.[/yellow]"
        )

    # Create status table
    table = _vpn_gateway_status_table()

    # Build mapping of tunnel -> BGP peer IP, remote public IP, and ha_role per instance
    tunnel_bgp_map: dict[str, dict[str, str]] = {}
    tunnel_peer_map: dict[str, dict[str, str]] = {}
    tunnel_role_map: dict[str, dict[str, str]] = {}
    tunnel_connection_map: dict[str, dict[str, str]] = {}
    peer_connection_map: dict[str, dict[str, str]] = {}
    peer_tunnel_map: dict[str, dict[str, str]] = {}
    peer_role_map: dict[str, dict[str, str]] = {}
    ecmp_warnings_by_vm: dict[str, list[dict[str, t.Any]]] = {}
    role_overrides_by_vm: dict[str, list[dict[str, str]]] = {}

    def _normalize_mode(value: t.Any) -> str:
        if hasattr(value, "value"):
            value = value.value
        value = str(value or "").strip().lower()
        return value or "bgp"

    defaults_mode = _normalize_mode(
        (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
    )
    for conn in local_cfg.get("connections") or []:
        conn_name = str(conn.get("name") or "unnamed")
        conn_mode = _normalize_mode(conn.get("routing_mode") or defaults_mode)
        for tun in conn.get("tunnels") or []:
            try:
                inst_idx = int(tun.get("gateway_instance_index", 0))
            except Exception:
                inst_idx = 0
            hostname = f"{plan.gateway_group.name}-{inst_idx}"
            tunnel_bgp_map.setdefault(hostname, {})
            tunnel_peer_map.setdefault(hostname, {})
            tunnel_role_map.setdefault(hostname, {})
            tunnel_connection_map.setdefault(hostname, {})
            peer_connection_map.setdefault(hostname, {})
            peer_tunnel_map.setdefault(hostname, {})
            peer_role_map.setdefault(hostname, {})
            tunnel_name = str(tun.get("name") or f"tunnel{inst_idx}")
            if conn_mode == "bgp":
                peer_ip = tun.get("inner_remote_ip")
                if peer_ip:
                    peer_ip_text = str(peer_ip)
                    tunnel_bgp_map[hostname][tunnel_name] = peer_ip_text
                    peer_connection_map[hostname][peer_ip_text] = conn_name
                    peer_tunnel_map[hostname][peer_ip_text] = tunnel_name
            remote_public_ip = tun.get("remote_public_ip")
            if remote_public_ip:
                tunnel_peer_map[hostname][tunnel_name] = str(remote_public_ip)
            ha_role = _normalize_role_value(tun.get("ha_role") or "active")
            tunnel_role_map[hostname][tunnel_name] = ha_role
            tunnel_connection_map[hostname][tunnel_name] = conn_name
            if conn_mode == "bgp":
                peer_ip = tun.get("inner_remote_ip")
                if peer_ip:
                    peer_role_map[hostname][str(peer_ip)] = ha_role

    def format_role(role: str | None) -> str:
        role_value = role or "-"
        if hasattr(role_value, "value"):
            role_value = role_value.value  # type: ignore[assignment]
        role_value = str(role_value).lower()
        if role_value == "active":
            return "[green]active[/green]"
        if role_value == "passive":
            return "[yellow]passive[/yellow]"
        if role_value == "disable":
            return "[red]disabled[/red]"
        return role_value

    def format_bgp_status(bgp_status: str | None) -> str:
        if not bgp_status or bgp_status == "-":
            return "-"
        state = str(bgp_status).strip()
        state_lower = state.lower()
        if state_lower == "established":
            return "[green]Established[/green]"
        if "admin" in state_lower:
            return "[red]Down (Admin)[/red]"
        if state_lower.startswith("idle") or state_lower in ("connect", "active"):
            label = state.split()[0].capitalize()
            return f"[red]Down ({label})[/red]"
        return f"[red]{state}[/red]"

    def _uptime_seconds(text: str) -> int | None:
        value_text = text.strip().lower()
        if value_text.endswith("ago"):
            value_text = value_text[:-3].strip()

        if value_text.isdigit():
            return int(value_text)

        short_match = re.match(r"(\d+)\s*([smhd])$", value_text)
        if short_match:
            value = int(short_match.group(1))
            unit = short_match.group(2)
            multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
            return value * multiplier

        word_match = re.match(r"(\d+)\s+(second|minute|hour|day)s?$", value_text)
        if word_match:
            value = int(word_match.group(1))
            unit = word_match.group(2)
            multiplier = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[unit]
            return value * multiplier

        return None

    def _format_uptime(seconds: int) -> str:
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{days}:{hours:02d}:{minutes:02d}:{secs:02d}"

    def parse_strongswan_uptime(uptime_str: str) -> str:
        """Parse strongSwan uptime and return d:h:m:s."""
        seconds = _uptime_seconds(uptime_str)
        if seconds is None:
            return uptime_str.strip()
        return _format_uptime(seconds)

    def _bgp_uptime_seconds(token: str) -> int | None:
        value_text = token.strip().lower()
        if not value_text or value_text in {"never", "n/a", "unknown", "idle"}:
            return None

        if value_text.isdigit():
            return int(value_text)

        colon_match = re.match(r"^(\d+):(\d{2}):(\d{2})$", value_text)
        if colon_match:
            hours = int(colon_match.group(1))
            minutes = int(colon_match.group(2))
            seconds = int(colon_match.group(3))
            return hours * 3600 + minutes * 60 + seconds

        total = 0
        matched = False
        for unit, multiplier in (
            ("w", 604800),
            ("d", 86400),
            ("h", 3600),
            ("m", 60),
            ("s", 1),
        ):
            match = re.search(rf"(\d+){unit}", value_text)
            if match:
                total += int(match.group(1)) * multiplier
                matched = True
        if matched:
            return total

        return None

    def parse_bgp_uptime(uptime_str: str) -> str:
        seconds = _bgp_uptime_seconds(uptime_str)
        if seconds is None:
            return _format_uptime(0)
        return _format_uptime(seconds)

    # Check each gateway VM's tunnels
    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        # Pull BGP neighbor states (if any BGP tunnels on this instance)
        bgp_states: dict[str, str] = {}
        bgp_uptime: dict[str, str] = {}
        if tunnel_bgp_map.get(inst_cfg.hostname):
            try:
                # Try JSON output first
                bgp_out = subprocess.run(
                    _status_ssh_target_command(
                        status_ssh_context,
                        hostname=inst_cfg.hostname,
                        target=target,
                    )
                    + [
                        "sudo vtysh -c 'show bgp ipv4 unicast summary json'",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if bgp_out.returncode == 0 and bgp_out.stdout:
                    try:
                        data = json.loads(bgp_out.stdout)
                        # Try multiple possible JSON structures from different FRR versions
                        peers = (data.get("ipv4Unicast") or {}).get("peers") or {}
                        if not peers:
                            # Try alternative structure
                            peers = data.get("peers") or {}
                        for ip, info in peers.items():
                            # Try different possible field names for state
                            state = (
                                info.get("state")
                                or info.get("state_name")
                                or info.get("stateName")
                                or info.get("peerState")
                                or info.get("bgpState")
                            )
                            if state:
                                bgp_states[ip] = state
                            uptime_token = (
                                info.get("peerUptime")
                                or info.get("upDownTime")
                                or info.get("upDownTimeStr")
                                or info.get("upTime")
                                or info.get("uptime")
                            )
                            if uptime_token is None and info.get("peerUptimeMsec") is not None:
                                try:
                                    ms_val = int(info.get("peerUptimeMsec"))
                                    bgp_uptime[ip] = _format_uptime(int(ms_val / 1000))
                                except Exception:
                                    pass
                            elif uptime_token is not None:
                                bgp_uptime[ip] = parse_bgp_uptime(str(uptime_token))
                    except json.JSONDecodeError:
                        pass

                # If JSON parsing didn't work (or no uptime), fall back to text parsing
                if not bgp_states or not bgp_uptime:
                    bgp_out = subprocess.run(
                        _status_ssh_target_command(
                            status_ssh_context,
                            hostname=inst_cfg.hostname,
                            target=target,
                        )
                        + [
                            "sudo vtysh -c 'show bgp summary'",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if bgp_out.returncode == 0 and bgp_out.stdout:
                        header_cols: list[str] | None = None
                        updown_idx: int | None = None
                        state_idx: int | None = None
                        # Parse text output: look for neighbor lines
                        # Example: "169.254.5.153    4 65014      123      456       0    0 01:23:45 Established"
                        for line in bgp_out.stdout.splitlines():
                            if line.startswith("Neighbor"):
                                header_cols = line.split()
                                if "Up/Down" in header_cols:
                                    updown_idx = header_cols.index("Up/Down")
                                if "State/PfxRcd" in header_cols:
                                    state_idx = header_cols.index("State/PfxRcd")
                                continue
                            parts = line.split()
                            # Look for lines starting with an IP address
                            if len(parts) >= 2 and parts[0] and "." in parts[0]:
                                try:
                                    # Validate it's an IP
                                    octets = parts[0].split(".")
                                    if len(octets) == 4 and all(
                                        o.isdigit() and 0 <= int(o) <= 255 for o in octets
                                    ):
                                        # Last column is typically the state or prefix count
                                        state = (
                                            parts[state_idx]
                                            if state_idx is not None and len(parts) > state_idx
                                            else parts[-1]
                                        )
                                        if state.isdigit():
                                            state = "Established"
                                        bgp_states[parts[0]] = state

                                        if updown_idx is not None and len(parts) > updown_idx:
                                            uptime_token = parts[updown_idx]
                                        else:
                                            uptime_token = next(
                                                (
                                                    p
                                                    for p in parts
                                                    if _bgp_uptime_seconds(p) is not None
                                                ),
                                                None,
                                            )
                                        if uptime_token and parts[0] not in bgp_uptime:
                                            bgp_uptime[parts[0]] = parse_bgp_uptime(uptime_token)
                                except (ValueError, IndexError):
                                    continue
            except Exception:
                pass

            try:
                route_out = subprocess.run(
                    _status_ssh_target_command(
                        status_ssh_context,
                        hostname=inst_cfg.hostname,
                        target=target,
                    )
                    + [
                        "sudo vtysh -c 'show bgp ipv4 unicast json'",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if route_out.returncode == 0 and route_out.stdout:
                    route_data = json.loads(route_out.stdout)
                    routes = route_data.get("routes") or {}
                    if isinstance(routes, dict):
                        ecmp_warnings_by_vm[inst_cfg.hostname] = (
                            _detect_cross_connection_ecmp_warnings(
                                routes,
                                peer_connection_map.get(inst_cfg.hostname, {}),
                                peer_tunnel_map.get(inst_cfg.hostname, {}),
                                peer_role_map.get(inst_cfg.hostname, {}),
                            )
                        )
            except Exception:
                pass

        # If any expected peers are missing uptime/state, query neighbors directly
        try:
            expected_peers = set(tunnel_bgp_map.get(inst_cfg.hostname, {}).values())
            missing_peers = [
                peer for peer in expected_peers if peer not in bgp_uptime or peer not in bgp_states
            ]
            neighbor_state_re = re.compile(r"BGP state = ([^,]+), up for (.+)$")
            for peer_ip in sorted(missing_peers):
                try:
                    neigh_out = subprocess.run(
                        _status_ssh_target_command(
                            status_ssh_context,
                            hostname=inst_cfg.hostname,
                            target=target,
                        )
                        + [
                            f"sudo vtysh -c 'show bgp neighbors {peer_ip}'",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if neigh_out.returncode != 0 or not neigh_out.stdout:
                        continue
                    for line in neigh_out.stdout.splitlines():
                        match = neighbor_state_re.search(line.strip())
                        if match:
                            state = match.group(1).strip()
                            uptime_token = match.group(2).strip()
                            if peer_ip not in bgp_states and state:
                                bgp_states[peer_ip] = state
                            if peer_ip not in bgp_uptime and uptime_token:
                                bgp_uptime[peer_ip] = parse_bgp_uptime(uptime_token)
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # Run swanctl status command (preferred for VICI-based configs)
        try:
            result = subprocess.run(
                _status_ssh_target_command(
                    status_ssh_context,
                    hostname=inst_cfg.hostname,
                    target=target,
                )
                + [
                    "sudo swanctl --list-sas",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            output = result.stdout if result.returncode == 0 else ""
            if output:
                tunnel_statuses: dict[str, str] = {}
                tunnel_uptime: dict[str, str] = {}
                tunnel_uptime_seconds: dict[str, int] = {}
                tunnel_encryption: dict[str, list[str]] = {}
                tunnel_ike_encryption: dict[str, list[str]] = {}
                tunnel_order: list[str] = []
                current_tunnel: str | None = None
                header_pattern = re.compile(r"^(\S+?):\s+#\d+,", re.IGNORECASE)
                status_pattern = re.compile(r"\b(ESTABLISHED|CONNECTING)\b", re.IGNORECASE)
                uptime_pattern = re.compile(r"established\s+(\S+)\s+ago", re.IGNORECASE)
                esp_pattern = re.compile(r"\bESP:([^,]+)", re.IGNORECASE)
                for line in output.splitlines():
                    header_match = header_pattern.match(line)
                    if header_match:
                        name = header_match.group(1)
                        status_match = status_pattern.search(line)
                        status = status_match.group(1).upper() if status_match else "CONNECTING"
                        if name not in tunnel_statuses:
                            tunnel_statuses[name] = status
                            tunnel_order.append(name)
                        elif tunnel_statuses[name] != "ESTABLISHED" and status == "ESTABLISHED":
                            tunnel_statuses[name] = status
                        current_tunnel = name
                        continue

                    if not current_tunnel:
                        continue

                    uptime_match = uptime_pattern.search(line)
                    if uptime_match:
                        uptime_token = uptime_match.group(1)
                        uptime_display = parse_strongswan_uptime(uptime_token)
                        uptime_seconds = _uptime_seconds(uptime_token)
                        if uptime_seconds is None:
                            if current_tunnel not in tunnel_uptime:
                                tunnel_uptime[current_tunnel] = uptime_display
                        else:
                            prev = tunnel_uptime_seconds.get(current_tunnel)
                            if prev is None or uptime_seconds < prev:
                                tunnel_uptime_seconds[current_tunnel] = uptime_seconds
                                tunnel_uptime[current_tunnel] = uptime_display

                    esp_match = esp_pattern.search(line)
                    if esp_match:
                        algo = esp_match.group(1).strip()
                        if algo:
                            algos = tunnel_encryption.setdefault(current_tunnel, [])
                            if algo not in algos:
                                algos.append(algo)

                    if line.startswith("  "):
                        algo_line = line.strip()
                        if "PRF_" in algo_line and "MODP_" in algo_line and "/" in algo_line:
                            ike_algos = tunnel_ike_encryption.setdefault(current_tunnel, [])
                            if algo_line not in ike_algos:
                                ike_algos.append(algo_line)

                if tunnel_statuses:
                    role_overrides_by_vm[inst_cfg.hostname] = _detect_connection_role_overrides(
                        inst_cfg.hostname,
                        tunnel_order,
                        tunnel_statuses,
                        bgp_states,
                        tunnel_bgp_map,
                        tunnel_role_map,
                        tunnel_connection_map,
                    )
                    for tunnel_name in tunnel_order:
                        status_text = tunnel_statuses[tunnel_name]
                        if status_text == "ESTABLISHED":
                            status_display = "[green]Established[/green]"
                        elif status_text == "CONNECTING":
                            status_display = "[yellow]Connecting[/yellow]"
                        else:
                            status_display = f"[red]{status_text.capitalize()}[/red]"

                        peer_cfg_ip = tunnel_bgp_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                        if peer_cfg_ip and peer_cfg_ip in bgp_states:
                            bgp_status = bgp_states[peer_cfg_ip]
                        else:
                            bgp_status = "-"

                        bgp_display = format_bgp_status(bgp_status)

                        peer_display = (
                            tunnel_peer_map.get(inst_cfg.hostname, {}).get(tunnel_name) or "-"
                        )
                        role = format_role(
                            tunnel_role_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                        )
                        enc_algos = tunnel_encryption.get(tunnel_name) or []
                        if not enc_algos:
                            enc_algos = tunnel_ike_encryption.get(tunnel_name) or []
                        encryption_display = ", ".join(enc_algos) if enc_algos else "n/a"
                        if peer_cfg_ip and peer_cfg_ip in bgp_uptime:
                            uptime_display = bgp_uptime[peer_cfg_ip]
                        else:
                            uptime_display = tunnel_uptime.get(tunnel_name, "n/a")

                        table.add_row(
                            tunnel_name,
                            role,
                            inst_cfg.hostname,
                            status_display,
                            bgp_display,
                            peer_display,
                            encryption_display,
                            uptime_display,
                        )

                    continue

            # Fall back to ipsec statusall if swanctl is unavailable
            result = subprocess.run(
                _status_ssh_target_command(
                    status_ssh_context,
                    hostname=inst_cfg.hostname,
                    target=target,
                )
                + [
                    "sudo ipsec statusall",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                table.add_row(
                    "All tunnels",
                    "-",
                    inst_cfg.hostname,
                    "[red]ERROR[/red]",
                    "-",
                    "-",
                    "-",
                    f"Failed to get status: {result.stderr.strip()}",
                )
                continue

            output = result.stdout

            # Parse IPsec status output
            # Look for patterns like: "gcp-classic-tunnel-0[202]: ESTABLISHED 8 minutes ago, 10.48.0.13[10.48.0.13]...34.155.169.244[34.155.169.244]"
            tunnel_pattern = re.compile(
                r"(\S+)\[\d+\]:\s+(\w+)\s+(.+?),\s+[\d.]+\[[\d.]+\]\.\.\.(\d+\.\d+\.\d+\.\d+)\["
            )

            tunnels = {}
            for match in tunnel_pattern.finditer(output):
                tunnel_name = match.group(1)
                status = match.group(2)
                raw_uptime = match.group(3)
                formatted_uptime = parse_strongswan_uptime(raw_uptime)
                peer_ip = match.group(4)
                tunnels[tunnel_name] = {
                    "status": status,
                    "uptime": formatted_uptime,
                    "peer_ip": peer_ip,
                    "encryption": "Unknown",
                    "bgp": "-",
                    "role": format_role(
                        tunnel_role_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                    ),
                }

            # Parse encryption from IKE proposal lines
            # Pattern: "IKE proposal: AES_GCM_16_128/PRF_AES128_XCBC/MODP_2048"
            ike_pattern = re.compile(r"(\S+)\[\d+\]:.*?IKE proposal:\s+(\S+)")
            for match in ike_pattern.finditer(output):
                tunnel_name = match.group(1)
                encryption = match.group(2)
                if tunnel_name in tunnels:
                    tunnels[tunnel_name]["encryption"] = encryption

            # Fallback: parse simplified connection lines if no SAs matched yet
            # Example: "gcp-ha-tunnel-1:  %any...34.157.15.187  IKEv2, dpddelay=30s"
            if not tunnels:
                conn_line_pattern = re.compile(
                    r"^(\S+):\s+%any\.\.\.(\d+\.\d+\.\d+\.\d+)", re.MULTILINE
                )
                for match in conn_line_pattern.finditer(output):
                    tunnels[match.group(1)] = {
                        "status": "CONNECTING",
                        "uptime": "-",
                        "peer_ip": match.group(2),
                        "encryption": "Unknown",
                        "bgp": "-",
                        "role": format_role(
                            tunnel_role_map.get(inst_cfg.hostname, {}).get(match.group(1))
                        ),
                    }

            # Attach BGP states where we know the peer IP from config
            for tname, tinfo in tunnels.items():
                peer_cfg_ip = tunnel_bgp_map.get(inst_cfg.hostname, {}).get(tname)
                if peer_cfg_ip and peer_cfg_ip in bgp_states:
                    tinfo["bgp"] = bgp_states[peer_cfg_ip]
                elif bgp_states:
                    # Fallback: if we have BGP states but no exact match, try to match any peer
                    # This handles cases where tunnel name mapping might be off
                    for _bgp_ip, bgp_state in bgp_states.items():
                        # Simple heuristic: assign if we don't have a BGP status yet
                        if tinfo.get("bgp") == "-":
                            tinfo["bgp"] = bgp_state
                            break

            # Add rows to table
            if tunnels:
                tunnel_statuses = {
                    name: str(info.get("status", "")).upper() for name, info in tunnels.items()
                }
                role_overrides_by_vm[inst_cfg.hostname] = _detect_connection_role_overrides(
                    inst_cfg.hostname,
                    list(tunnels.keys()),
                    tunnel_statuses,
                    bgp_states,
                    tunnel_bgp_map,
                    tunnel_role_map,
                    tunnel_connection_map,
                )
                for tunnel_name, info in tunnels.items():
                    status_text = info["status"]
                    if status_text == "ESTABLISHED":
                        status_display = "[green]Established[/green]"
                    elif status_text == "CONNECTING":
                        status_display = "[yellow]Connecting[/yellow]"
                    else:
                        status_display = f"[red]{status_text.capitalize()}[/red]"

                    # Format BGP status with colors
                    bgp_status = info.get("bgp", "-")
                    bgp_display = format_bgp_status(bgp_status)

                    if peer_cfg_ip and peer_cfg_ip in bgp_uptime:
                        info["uptime"] = bgp_uptime[peer_cfg_ip]

                    table.add_row(
                        tunnel_name,
                        info.get("role", "-"),
                        inst_cfg.hostname,
                        status_display,
                        bgp_display,
                        info["peer_ip"],
                        info["encryption"],
                        info["uptime"],
                    )
            else:
                # No tunnels found in output
                if _ipsec_status_reports_no_active_tunnels(output):
                    table.add_row(
                        "No tunnels",
                        "-",
                        inst_cfg.hostname,
                        "[yellow]NONE[/yellow]",
                        "-",
                        "-",
                        "-",
                        "-",
                    )
                else:
                    table.add_row(
                        "Unknown",
                        "-",
                        inst_cfg.hostname,
                        "[red]PARSE ERROR[/red]",
                        "-",
                        "-",
                        "-",
                        "Could not parse ipsec output",
                    )
                    # Show a trimmed snippet to aid debugging
                    snippet = "\n".join(output.splitlines()[:20])
                    print(
                        f"[yellow]{inst_cfg.hostname} ipsec status output (first lines):[/yellow]\n{snippet}\n"
                    )

        except subprocess.TimeoutExpired:
            table.add_row(
                "All tunnels",
                "-",
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "-",
                "-",
                "-",
                "SSH command timed out",
            )
        except Exception as e:
            table.add_row(
                "All tunnels",
                "-",
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "-",
                "-",
                "-",
                str(e),
            )

    console.print(table)

    active_role_overrides = {
        hostname: overrides for hostname, overrides in role_overrides_by_vm.items() if overrides
    }
    if active_role_overrides:
        console.print(
            Panel.fit(
                "\n".join(_format_role_override_lines(active_role_overrides)),
                title="[yellow]Traffic Override[/yellow]",
                border_style="yellow",
            )
        )

    # Show service health
    console.print("\n[bold]Checking system services...[/bold]")
    service_table = Table(show_header=True, header_style="bold cyan")
    service_table.add_column("Gateway VM", style="white")
    service_table.add_column("Agent", style="white")
    service_table.add_column("StrongSwan", style="white")
    service_table.add_column("FRR", style="white")

    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        services = {
            "nebius-vpngw-agent": "Unknown",
            "strongswan": "Unknown",  # Check process, not systemd service
            "frr": "Unknown",
        }

        for service_name in services:
            try:
                # Special handling for strongSwan - check if charon daemon is running
                if service_name == "strongswan":
                    result = subprocess.run(
                        _status_ssh_target_command(
                            status_ssh_context,
                            hostname=inst_cfg.hostname,
                            target=target,
                        )
                        + [
                            "pgrep -x charon >/dev/null && echo active || echo inactive",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        shell=False,
                    )
                else:
                    result = subprocess.run(
                        _status_ssh_target_command(
                            status_ssh_context,
                            hostname=inst_cfg.hostname,
                            target=target,
                        )
                        + [
                            f"systemctl is-active {service_name}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )

                status_raw = result.stdout.strip()
                if status_raw == "active":
                    services[service_name] = "[green]active[/green]"
                elif status_raw == "inactive":
                    services[service_name] = "[yellow]inactive[/yellow]"
                else:
                    services[service_name] = f"[red]{status_raw}[/red]"
                    # Fetch last few lines of systemctl status for context
                    try:
                        detail_cmd = f"systemctl status {service_name} --no-pager -n 20"
                        if service_name == "strongswan":
                            detail_cmd = "systemctl status strongswan-starter --no-pager -n 20 || systemctl status strongswan --no-pager -n 20"
                        detail = subprocess.run(
                            _status_ssh_target_command(
                                status_ssh_context,
                                hostname=inst_cfg.hostname,
                                target=target,
                            )
                            + [
                                detail_cmd,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            shell=False,
                        )
                        snippet = (detail.stdout or detail.stderr or "").strip()
                        if snippet:
                            print(
                                f"[yellow]{inst_cfg.hostname} {service_name} status:[/yellow]\n{snippet}\n"
                            )
                    except Exception:
                        pass

            except Exception:
                services[service_name] = "[red]error[/red]"

        service_table.add_row(
            inst_cfg.hostname,
            services["nebius-vpngw-agent"],
            services["strongswan"],
            services["frr"],
        )

    console.print(service_table)

    # Show routing health (checks for routing table invariants)
    console.print("\n[bold]Routing Table Health:[/bold]")
    routing_table = Table(show_header=True, header_style="bold cyan")
    routing_table.add_column("Gateway VM", style="white")
    routing_table.add_column("Table 220", style="white")
    routing_table.add_column("Broad APIPA", style="white")
    routing_table.add_column("Tunnel Routes", style="white")
    routing_table.add_column("Overall", style="white")

    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        try:
            # Check routing health by running Python status check remotely
            check_cmd = """python3 -c "
import subprocess
import json

health = {
    'table_220': False,
    'table_220_error': False,
    'broad_apipa': False,
    'broad_apipa_error': False,
    'orphaned_count': 0,
    'status': 'healthy'
}

# Check policy-rule and route-only table 220 state
rules = subprocess.run(['ip', 'rule', 'show'], capture_output=True, text=True)
def selects_table_220(line):
    tokens = line.split()
    return any(
        token in ('lookup', 'table')
        and index + 1 < len(tokens)
        and tokens[index + 1] == '220'
        for index, token in enumerate(tokens)
    )

table_220_rule = any(selects_table_220(line) for line in rules.stdout.splitlines())
all_routes = subprocess.run(
    ['ip', '-j', '-4', 'route', 'show', 'table', 'all'], capture_output=True, text=True
)
table_220_routes = None
if all_routes.returncode == 0:
    try:
        parsed_routes = json.loads(all_routes.stdout)
        if not isinstance(parsed_routes, list) or any(
            not isinstance(route, dict) for route in parsed_routes
        ):
            raise ValueError
        table_220_routes = [
            route
            for route in parsed_routes
            if str(route.get('table', '')).lower() in ('220', 'ipsec')
        ]
    except (json.JSONDecodeError, ValueError):
        table_220_routes = None
if rules.returncode != 0 or table_220_routes is None:
    health['table_220_error'] = True
    health['status'] = 'error'
elif table_220_rule or table_220_routes:
    health['table_220'] = True
    health['status'] = 'error'

# Check broad APIPA
r = subprocess.run(['ip', 'route', 'show', '169.254.0.0/16'], capture_output=True, text=True)
if r.returncode != 0:
    health['broad_apipa_error'] = True
    health['status'] = 'error'
elif r.stdout.strip():
    health['broad_apipa'] = True
    health['status'] = 'error'

# Count APIPA tunnel routes (VTI subnets + BGP peer /32s)
# This is for informational purposes - these are expected/legitimate routes
r = subprocess.run(['ip', 'route', 'show'], capture_output=True, text=True)
apipa_count = 0
for line in r.stdout.split('\\n'):
    if '169.254.' in line and not line.startswith('169.254.169.'):
        apipa_count += 1

health['orphaned_count'] = apipa_count  # Note: 'orphaned' name kept for compatibility

print(json.dumps(health))
" """

            result = subprocess.run(
                _status_ssh_target_command(
                    status_ssh_context,
                    hostname=inst_cfg.hostname,
                    target=target,
                )
                + [
                    check_cmd,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                try:
                    health = json.loads(result.stdout.strip())

                    # Format table 220 status
                    if health.get("table_220_error"):
                        table_220_display = "[red]ERROR[/red]"
                    elif health.get("table_220"):
                        table_220_display = "[red]EXISTS[/red]"
                    else:
                        table_220_display = "[green]OK[/green]"

                    # Format broad APIPA status
                    if health.get("broad_apipa_error"):
                        broad_apipa_display = "[red]ERROR[/red]"
                    elif health.get("broad_apipa"):
                        broad_apipa_display = "[red]EXISTS[/red]"
                    else:
                        broad_apipa_display = "[green]OK[/green]"

                    # Format tunnel routes count (APIPA routes for VTI interfaces)
                    tunnel_routes_count = health.get("orphaned_count", 0)
                    tunnel_routes_display = f"{tunnel_routes_count} routes"

                    # Overall status
                    status = health.get("status", "unknown")
                    if status == "healthy":
                        overall_display = "[green]Healthy[/green]"
                    elif status == "warning":
                        overall_display = "[yellow]Warning[/yellow]"
                    else:
                        overall_display = "[red]Issues Found[/red]"

                    routing_table.add_row(
                        inst_cfg.hostname,
                        table_220_display,
                        broad_apipa_display,
                        tunnel_routes_display,
                        overall_display,
                    )
                except json.JSONDecodeError:
                    routing_table.add_row(
                        inst_cfg.hostname,
                        "[red]ERROR[/red]",
                        "[red]ERROR[/red]",
                        "-",
                        "[red]Parse Error[/red]",
                    )
            else:
                routing_table.add_row(
                    inst_cfg.hostname,
                    "[red]ERROR[/red]",
                    "[red]ERROR[/red]",
                    "-",
                    "[red]Check Failed[/red]",
                )

        except subprocess.TimeoutExpired:
            routing_table.add_row(
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "[red]TIMEOUT[/red]",
                "-",
                "[red]Timeout[/red]",
            )
        except Exception as e:
            routing_table.add_row(
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "[red]ERROR[/red]",
                "-",
                f"[red]{str(e)[:20]}[/red]",
            )

    console.print(routing_table)

    active_ecmp_warnings = {
        hostname: warnings for hostname, warnings in ecmp_warnings_by_vm.items() if warnings
    }
    if active_ecmp_warnings:
        warning_lines = _format_ecmp_warning_lines(active_ecmp_warnings)
        console.print(
            Panel.fit(
                "\n".join(warning_lines),
                title="[yellow]ECMP Warning[/yellow]",
                border_style="yellow",
            )
        )

    gateway_group_cfg = local_cfg.get("gateway_group", {}) or {}
    gateway_subnet_cfg = gateway_group_cfg.get("subnet", {}) or {}
    gateway_subnet_name = gateway_subnet_cfg.get("name") or "vpngw-subnet"

    # Show gateway subnet route table
    console.print("\n[bold]VPN Gateway Subnet Route Table:[/bold]")
    try:
        from nebius.api.nebius.vpc.v1 import (
            GetRouteTableRequest,
            GetSubnetByNameRequest,
            ListRoutesRequest,
            RouteServiceClient,
            RouteTableServiceClient,
            SubnetServiceClient,
        )
        from rich.table import Table

        client = vm_mgr._get_client()
        if client and proj_id:
            subnet_client = SubnetServiceClient(client)

            try:
                # Get configured gateway subnet
                subnet_obj = subnet_client.get_by_name(
                    GetSubnetByNameRequest(parent_id=proj_id, name=gateway_subnet_name)
                ).wait()

                # Get subnet CIDR
                subnet_spec = getattr(subnet_obj, "spec", None)
                subnet_cidrs = []
                if subnet_spec:
                    ipv4_pools = getattr(subnet_spec, "ipv4_private_pools", None)
                    if ipv4_pools:
                        pools = getattr(ipv4_pools, "pools", []) or []
                        for pool in pools:
                            cidrs = getattr(pool, "cidrs", []) or []
                            for cidr_obj in cidrs:
                                cidr_str = getattr(cidr_obj, "cidr", None)
                                if cidr_str:
                                    subnet_cidrs.append(cidr_str)

                subnet_cidr = subnet_cidrs[0] if subnet_cidrs else "unknown"

                # Get route table ID
                rt_id = getattr(subnet_spec, "route_table_id", None) if subnet_spec else None

                if not rt_id:
                    console.print(f"[yellow]Subnet: {gateway_subnet_name} ({subnet_cidr})[/yellow]")
                    console.print("[yellow]  No route table attached[/yellow]")
                else:
                    # Get route table details
                    rt_client = RouteTableServiceClient(client)
                    route_client = RouteServiceClient(client)

                    rt_obj = rt_client.get(GetRouteTableRequest(id=rt_id)).wait()
                    rt_meta = getattr(rt_obj, "metadata", None)
                    rt_name = getattr(rt_meta, "name", None) or "unknown"

                    # Check if it's default route table
                    is_default = False
                    try:
                        subnet_status = getattr(subnet_obj, "status", None)
                        if subnet_status:
                            rt_info = getattr(subnet_status, "route_table", None)
                            if rt_info:
                                is_default = getattr(rt_info, "default", False)
                    except Exception:
                        pass

                    console.print(f"Subnet: {gateway_subnet_name} ({subnet_cidr})")
                    console.print(f"  Route Table: {rt_name} (ID: {rt_id}, default={is_default})")

                    # Get routes in the table
                    routes_list_op = route_client.list(ListRoutesRequest(parent_id=rt_id))
                    routes_list = (
                        routes_list_op.wait() if hasattr(routes_list_op, "wait") else routes_list_op
                    )

                    route_items: list[t.Any] = []
                    if hasattr(routes_list, "items"):
                        route_items = list(routes_list.items)
                    elif hasattr(routes_list, "__iter__"):
                        route_items = list(routes_list)

                    if route_items:
                        # Create routes table
                        routes_table = Table(show_header=True, header_style="bold cyan", box=None)
                        routes_table.add_column("Destination", style="white")
                        routes_table.add_column("Next Hop", style="white")

                        for route in route_items:
                            route_spec = getattr(route, "spec", None)
                            if not route_spec:
                                continue

                            # Get destination
                            dest = getattr(route_spec, "destination", None)
                            dest_cidr = getattr(dest, "cidr", None) if dest else "unknown"

                            # Get next hop
                            next_hop_text = "unknown"
                            next_hop = getattr(route_spec, "next_hop", None)
                            if next_hop:
                                # Check for default_egress_gateway field
                                if hasattr(next_hop, "default_egress_gateway") and getattr(
                                    next_hop, "default_egress_gateway", False
                                ):
                                    next_hop_text = "default-egress"
                                elif hasattr(next_hop, "default_internet_gateway") and getattr(
                                    next_hop, "default_internet_gateway", False
                                ):
                                    next_hop_text = "default-gateway"
                                elif hasattr(next_hop, "allocation"):
                                    alloc = next_hop.allocation
                                    alloc_id = getattr(alloc, "id", None)
                                    if alloc_id:
                                        next_hop_text = f"allocation:{alloc_id[:16]}..."

                            routes_table.add_row(dest_cidr, next_hop_text)

                        console.print(routes_table)
                    else:
                        console.print("  [dim]No routes in table[/dim]")

            except Exception as e:
                console.print(
                    f"[yellow]Could not fetch gateway subnet '{gateway_subnet_name}' route table: {e}[/yellow]"
                )
    except Exception as e:
        console.print(f"[yellow]Error displaying route table: {e}[/yellow]")

    if plan.vm_ha is not None:
        lifecycle_state: VMHALifecycleState | None = None
        status_runtime_binding: t.Any | None = None
        try:
            lifecycle_state = VMHALifecycleStore(local_config_file).read(
                expected_project_id=proj_id or "",
                expected_gateway_name=plan.gateway_group.name,
            )
        except (OSError, RuntimeError, ValueError):
            authority = _vm_ha_unavailable_authority("unknown", "lifecycle-status-unavailable")
        else:
            if lifecycle_state is None:
                authority = _vm_ha_unavailable_authority("unknown", "lifecycle-status-unavailable")
            else:
                if lifecycle_state.status in {
                    VMHALifecycleStatus.ACTIVATING,
                    VMHALifecycleStatus.ACTIVE,
                }:
                    try:
                        status_runtime_binding = _vm_ha_status_runtime_binding(lifecycle_state)
                    except ValueError:
                        authority = _vm_ha_unavailable_authority(
                            lifecycle_state.status.value,
                            "lifecycle-binding-invalid",
                        )
                    else:
                        authority = _vm_ha_unavailable_authority(
                            lifecycle_state.status.value,
                            "cloud-observation-unavailable",
                        )
                else:
                    authority = _vm_ha_unavailable_authority(
                        lifecycle_state.status.value,
                        "cloud-observation-unavailable",
                    )

        if lifecycle_state is not None and authority.reasons == ("cloud-observation-unavailable",):
            raw_prefixes = (local_cfg.get("gateway") or {}).get("local_prefixes") or []
            try:
                cloud_observation = vm_mgr.observe_vm_ha_migration_state(
                    plan.gateway_group,
                    [str(prefix) for prefix in raw_prefixes],
                )
                authority = _vm_ha_cloud_authority(lifecycle_state, cloud_observation)
            except (OSError, RuntimeError, TypeError, ValueError):
                authority = _vm_ha_unavailable_authority(
                    lifecycle_state.status.value,
                    "cloud-observation-unavailable",
                )

        configured_members = tuple(plan.iter_instance_configs())
        if len(configured_members) != 2:
            raise RuntimeError("explicit VM-HA status requires exactly two configured members")

        member_evidence: list[_VMHAMemberEvidence] = []
        for inst_cfg in configured_members:
            node = inst_cfg.vm_ha_node
            if node is None:
                member_evidence.append(
                    _VMHAMemberEvidence(
                        name=inst_cfg.hostname,
                        configured_role="unknown",
                        node_id=f"missing-{inst_cfg.hostname}",
                        condition="blocked",
                        reason="member-configuration-invalid",
                    )
                )
                continue
            configured_role = str(getattr(node.role, "value", node.role))
            node_id = str(node.node_id)
            target = vm_ips.get(inst_cfg.hostname)
            if not target:
                member_evidence.append(
                    _VMHAMemberEvidence(
                        name=inst_cfg.hostname,
                        configured_role=configured_role,
                        node_id=node_id,
                        condition="unknown",
                        reason="member-address-unavailable",
                    )
                )
                continue
            try:
                status_ssh_policy = status_ssh_context.policies.get(inst_cfg.hostname)
                if status_ssh_policy is None:
                    raise _VMHAStatusSSHUnavailable(
                        "exact SSH trust is unavailable for this VM-HA member"
                    )
                vm_ha = _fetch_vm_ha_agent_status(
                    target=target,
                    hostname=inst_cfg.hostname,
                    username=status_ssh_context.username,
                    key_path=status_ssh_context.key_path,
                    ssh_policy=status_ssh_policy,
                    inst_cfg=inst_cfg,
                    runtime_binding=status_runtime_binding,
                    require_local_generation=require_local_generation,
                )
                vm_ha = _validate_vm_ha_display_status(
                    vm_ha,
                    inst_cfg=inst_cfg,
                    runtime_binding=status_runtime_binding,
                    require_local_generation=require_local_generation,
                )
                member_evidence.append(
                    _VMHAMemberEvidence(
                        name=inst_cfg.hostname,
                        configured_role=configured_role,
                        node_id=node_id,
                        condition="exact",
                        reason="",
                        record=vm_ha,
                    )
                )
            except Exception as error:
                condition, reason = _vm_ha_member_failure_condition(error)
                member_evidence.append(
                    _VMHAMemberEvidence(
                        name=inst_cfg.hostname,
                        configured_role=configured_role,
                        node_id=node_id,
                        condition=condition,
                        reason=reason,
                    )
                )

        view = _vm_ha_status_view(
            authority,
            t.cast(
                tuple[_VMHAMemberEvidence, _VMHAMemberEvidence],
                tuple(member_evidence),
            ),
            rearm_command=(
                "nebius-vpngw vm-ha-rearm --local-config-file "
                f"{shlex.quote(str(local_config_file))}"
            ),
            mtls_command=(
                "nebius-vpngw set-vm-ha-mtls --local-config-file "
                f"{shlex.quote(str(local_config_file))}"
            ),
        )
        _render_vm_ha_status(console, view)


@app.command(
    name="add-routes-local",
    epilog=_command_help_epilog("add-routes-local"),
)
def add_routes_local(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    summarize: bool = typer.Option(
        False,
        "--summarize",
        help=(
            "Collapse exact adjacent remote prefixes per gateway next-hop to reduce "
            "route-table entries. Only exact unions are summarized."
        ),
    ),
    swap_route_table: bool = typer.Option(
        False,
        "--swap-route-table",
        help=(
            "Build a fresh custom route table per selected subnet, copy preserved "
            "non-vpngw routes, rebuild managed VPN routes from the current YAML, "
            "validate the replacement table, then attach the subnet to the new "
            "table and print a rollback command."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=(
            "Skip the confirmation prompt for --swap-route-table. Use only when "
            "you already understand the cutover and rollback behavior."
        ),
    ),
):
    """Manage ordinary VPC routes or repair proven VM-HA BGP export drift.

    For ordinary gateways, this command selects workload subnets by
    gateway.local_prefixes and adds missing routes through the owning gateway
    private allocation. For explicit VM HA, VPC routes remain controller-owned:
    BGP mode may repair only proven export drift, while static mode must be
    reconciled through `apply`. BGP repair first verifies the installed agent's
    private capability contract on every target. Any incomplete route or repair
    exits nonzero. Use `apply` to deploy local YAML changes first.

    On ordinary gateways, optional `--swap-route-table` performs a blue/green
    route-table cutover:
    it builds a fresh custom route table, copies preserved non-vpngw routes,
    rebuilds managed VPN routes from the current YAML, validates the
    replacement, then attaches the subnet to the new table and prints a
    rollback command.
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    _enforce_command_applicability(
        "add-routes-local",
        plan,
        local_cfg,
        summarize=summarize,
        swap_route_table=swap_route_table,
        yes=yes,
    )
    try:
        ssh_policy = _build_route_ssh_policy(local_cfg, plan, project_id=project_id)
    except RouteManagementError as error:
        print(f"[red]Local route management failed:[/red] {error}")
        raise typer.Exit(code=1) from error

    # Resolve project_id
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    rollback_dir = local_config_file.parent / ".nebius-vpngw-rollbacks"

    if swap_route_table and not yes:
        print("")
        print(
            "[yellow]⚠ WARNING: --swap-route-table performs a blue/green subnet route-table cutover.[/yellow]"
        )
        print(
            "[yellow]  • A fresh custom route table will be created for each selected workload subnet[/yellow]"
        )
        print(
            "[yellow]  • Existing non-vpngw routes will be copied from the currently attached table[/yellow]"
        )
        print(
            "[yellow]  • Managed VPN routes will be rebuilt from the current YAML and then the subnet will be reattached to the new table[/yellow]"
        )
        print(
            "[yellow]  • The old route table will be left in place for rollback; rollback specs will be written to[/yellow]"
        )
        print(f"[yellow]    {rollback_dir}[/yellow]")
        print("")
        print(
            "[yellow]If the replacement table is incomplete or the subnet update converges slowly, traffic for the subnet can be briefly impacted.[/yellow]"
        )
        print(
            "[yellow]Only proceed if you are ready to validate routes immediately and use the printed rollback command if needed.[/yellow]"
        )
        print("")
        import sys

        sys.stdout.write("\033[1mProceed with route-table swap? [y/N]:\033[0m ")
        sys.stdout.flush()
        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)

    # Get token for API access (required for route management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    routes = RouteManager(
        project_id=proj_id,
        auth_token=auth_token,
        ssh_policy=ssh_policy,
    )

    try:
        routing_modes = _configured_routing_modes(local_cfg)
        if "bgp" in routing_modes:
            print("[bold]Checking installed gateway agent capabilities...[/bold]")
            routes.require_agent_capabilities(plan, local_cfg)

        if plan.vm_ha is None:
            print("[bold]Ensuring VPC routes for remote prefixes on local subnets...[/bold]")
            routes.add_routes(
                plan,
                local_cfg,
                summarize=summarize,
                swap_route_table=swap_route_table,
                rollback_dir=rollback_dir,
            )
        else:
            print(
                "[dim]VM-HA VPC routes remain controller-owned; skipping legacy "
                "member-primary route mutation.[/dim]"
            )

        if "bgp" in routing_modes:
            routes.ensure_bgp_advertisements_current(
                plan,
                local_cfg,
                vm_ha_lifecycle_guard=lambda: _vm_ha_route_lifecycle_is_stable(
                    local_config_file,
                    plan,
                    proj_id,
                ),
            )
    except RouteManagementError as error:
        print(f"[red]Local route management failed:[/red] {error}")
        raise typer.Exit(code=1) from error

    print("[green]Local route management completed.[/green]")


@app.command(
    name="list-routes-local",
    epilog=_command_help_epilog("list-routes-local"),
)
def list_routes_local(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
):
    """Read-only audit of Nebius workload routes and advertised BGP routes.

    Shows:
    1. Route table entries on workload subnets selected by gateway.local_prefixes
    2. BGP routes being advertised to peer routers, organized by connection/tunnel
    3. MATCH/DRIFT/UNKNOWN advertisement parity without config upload or service reload
    4. Multi-connection-safe peer attribution by owning gateway VM and peer IP
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
    try:
        ssh_policy = _build_route_ssh_policy(local_cfg, plan, project_id=project_id)
    except RouteManagementError as error:
        print(f"[red]Failed to list routes:[/red] {error}")
        raise typer.Exit(code=1) from error

    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None

    # Get token for API access (required for route management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    _ensure_gateway_vms_exist(
        plan,
        project_id=proj_id,
        zone=plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
        action="list local routes",
    )

    routes = RouteManager(
        project_id=proj_id,
        auth_token=auth_token,
        ssh_policy=ssh_policy,
    )

    print("[bold]Listing VPC routes for local prefixes...[/bold]")
    try:
        routes.list_routes(
            plan,
            local_cfg,
            vm_ha_lifecycle_guard=lambda: _vm_ha_route_lifecycle_is_stable(
                local_config_file,
                plan,
                proj_id,
            ),
        )
    except Exception as e:
        print(f"[red]Failed to list routes:[/red] {e}")
        raise typer.Exit(code=1)


@app.command(
    name="list-routes-remote",
    epilog=_command_help_epilog("list-routes-remote"),
)
def list_routes_remote(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    connection: str | None = typer.Option(
        None, help="Connection name to show routes for (default: all)"
    ),
):
    """List remote routes learned/configured via VPN (Remote → Nebius).

    - BGP mode: Shows routes learned from the selected connection's tunnel peers on the
      owning gateway VM(s), with whitelist status
    - Static mode: Shows static routes configured on the owning gateway VM(s)
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
    try:
        ssh_policy = _build_route_ssh_policy(local_cfg, plan)
    except RouteManagementError as error:
        print(f"[red]Failed to list remote routes:[/red] {error}")
        raise typer.Exit(code=1) from error

    # Get project_id for RouteManager (not really needed for this command but kept for consistency)
    proj_id = local_cfg.get("project_id") or ""

    routes = RouteManager(
        project_id=proj_id,
        auth_token=None,
        ssh_policy=ssh_policy,
    )

    print("[bold]Querying remote routes from gateway VMs...[/bold]")
    try:
        routes.list_remote_routes(plan, local_cfg, connection_filter=connection)
    except Exception as e:
        print(f"[red]Failed to list remote routes:[/red] {e}")
        raise typer.Exit(code=1)


@app.command(epilog=_command_help_epilog("destroy"))
def destroy(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Destroy ordinary gateway compute while preserving public IPs and VPC objects.

    Safe to rerun. Missing VMs, disks, routes, or private allocations are
    treated as already-cleaned-up state. Explicit VM HA must first be removed
    through the supported `apply` lifecycle.
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    _enforce_command_applicability("destroy", plan, local_cfg)

    # Resolve context from CLI args or config
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None

    # Get token for API access (required for VM management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    vm_mgr = VMManager(
        project_id=proj_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )

    # Confirmation prompt
    if not yes:
        print("\n[yellow]⚠️  WARNING: This will:[/yellow]")
        print(f"[yellow]  • Delete all gateway VMs ({plan.gateway_group.name}-*)[/yellow]")
        print("[yellow]  • Delete all boot disks[/yellow]")
        print("[yellow]  • Delete static private IP allocations[/yellow]")
        print("[yellow]  • Delete VPC routes pointing to gateway[/yellow]")
        print("[yellow]  • Terminate all VPN tunnels[/yellow]")
        print("")
        print("[green]  ✓ Preserve network resources (VPC, subnets)[/green]")
        print("[green]  ✓ Preserve public IP allocations (reusable)[/green]")
        print("")
        import sys

        sys.stdout.write("\033[1mProceed with destruction? [y/N]:\033[0m ")
        sys.stdout.flush()
        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)

    print("[bold]Destroying gateway infrastructure...[/bold]")

    try:
        client = vm_mgr._get_client()
        if client is None:
            print("[red]Error: Nebius SDK client is unavailable.[/red]")
            raise typer.Exit(code=1)

        # Get service clients
        from nebius.api.nebius.compute.v1 import (
            DiskServiceClient,
            InstanceServiceClient,
            ListInstancesRequest,
        )
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient

        isc = InstanceServiceClient(client)
        dsc = DiskServiceClient(client)
        asc = AllocationServiceClient(client)

        # List existing VMs matching the gateway group name
        print(
            f"[bold]Step 1/5: Listing VMs matching pattern '{plan.gateway_group.name}-*'...[/bold]"
        )
        ilist_op = isc.list(ListInstancesRequest(parent_id=proj_id or ""))
        ilist = ilist_op.wait() if hasattr(ilist_op, "wait") else ilist_op

        # Extract items from the response
        items: list[t.Any] = []
        if hasattr(ilist, "items"):
            items = list(ilist.items)
        elif hasattr(ilist, "__iter__"):
            items = list(ilist)

        existing = [
            inst
            for inst in items
            if getattr(getattr(inst, "metadata", None), "name", "").startswith(
                f"{plan.gateway_group.name}-"
            )
        ]

        if not existing:
            print(f"[yellow]No VMs found matching '{plan.gateway_group.name}-*'.[/yellow]")
        else:
            print(f"[yellow]Found {len(existing)} VM(s) to delete[/yellow]")

        # Collect private IP allocations to delete
        # Method 1: From existing VMs (if any)
        private_alloc_ids = []
        for inst in existing:
            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or "unknown"
            # Get network interfaces from VM status
            if hasattr(inst, "status") and hasattr(inst.status, "network_interfaces"):
                for ni in inst.status.network_interfaces:
                    # Private IP allocation (we want to delete these)
                    if hasattr(ni, "ip_address") and hasattr(ni.ip_address, "allocation_id"):
                        if ni.ip_address.allocation_id:
                            private_alloc_ids.append((inst_name, ni.ip_address.allocation_id))
                            print(
                                f"[dim]Found private allocation from VM {inst_name}: {ni.ip_address.allocation_id}[/dim]"
                            )

        # Method 2: Search by name pattern (catches allocations from already-deleted VMs)
        try:
            from nebius.api.nebius.vpc.v1 import ListAllocationsRequest

            alloc_list_op = asc.list(ListAllocationsRequest(parent_id=proj_id or ""))
            alloc_list = alloc_list_op.wait() if hasattr(alloc_list_op, "wait") else alloc_list_op

            alloc_items: list[t.Any] = []
            if hasattr(alloc_list, "items"):
                alloc_items = list(alloc_list.items)
            elif hasattr(alloc_list, "__iter__"):
                alloc_items = list(alloc_list)

            # Look for private IP allocations matching our naming pattern
            for alloc in alloc_items:
                alloc_name = getattr(getattr(alloc, "metadata", None), "name", None)
                alloc_id = getattr(alloc, "id", None) or getattr(
                    getattr(alloc, "metadata", None), "id", None
                )

                # Check if this is a private allocation for our gateway
                # Pattern: {gateway-name}-{index}-eth{nic}-private-ip
                if alloc_name and alloc_id:
                    for i in range(plan.gateway_group.instance_count):
                        expected_name = f"{plan.gateway_group.name}-{i}-eth0-private-ip"
                        if alloc_name == expected_name:
                            # Check if we already have this from VM inspection
                            if not any(aid == alloc_id for _, aid in private_alloc_ids):
                                inst_name = f"{plan.gateway_group.name}-{i}"
                                private_alloc_ids.append((inst_name, alloc_id))
                                print(
                                    f"[dim]Found private allocation by name pattern {alloc_name}: {alloc_id}[/dim]"
                                )
                            break
        except Exception as e:
            print(f"[dim]Could not search for allocations by name: {e}[/dim]")

        # Step 2: Delete VMs
        print("[bold]Step 2/5: Deleting VMs...[/bold]")
        for inst in existing:
            inst_id = getattr(inst, "id", None) or getattr(
                getattr(inst, "metadata", None), "id", None
            )
            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or "unknown"

            if inst_id:
                try:
                    print(f"[VMManager] Deleting VM {inst_name} (id={inst_id})...")
                    from nebius.api.nebius.compute.v1 import DeleteInstanceRequest

                    delete_req = DeleteInstanceRequest(id=inst_id)
                    op = isc.delete(delete_req)
                    if hasattr(op, "wait"):
                        op.wait()
                        print(f"[green]✓ VM {inst_name} deleted[/green]")
                except Exception as e:
                    print(f"[red]Failed to delete VM {inst_name}: {e}[/red]")

        # Wait for VM deletions to complete
        if existing:
            import time

            print("[VMManager] Waiting for VM deletions to complete...")
            time.sleep(15)

        # Step 3: Delete boot disks
        print("[bold]Step 3/5: Deleting boot disks...[/bold]")
        import time

        from nebius.api.nebius.common.v1 import GetByNameRequest

        for i in range(plan.gateway_group.instance_count):
            inst_name = f"{plan.gateway_group.name}-{i}"
            boot_disk_name = f"{inst_name}-boot"

            try:
                disk_obj = dsc.get_by_name(
                    GetByNameRequest(parent_id=proj_id, name=boot_disk_name)
                ).wait()
                disk_id = getattr(disk_obj, "id", None) or getattr(
                    getattr(disk_obj, "metadata", None), "id", None
                )

                if disk_id:
                    # Retry disk deletion up to 3 times
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(
                                f"[VMManager] Deleting boot disk {boot_disk_name} (id={disk_id})..."
                            )
                            from nebius.api.nebius.compute.v1 import DeleteDiskRequest

                            delete_disk_req = DeleteDiskRequest(id=disk_id)
                            disk_op = dsc.delete(delete_disk_req)
                            if hasattr(disk_op, "wait"):
                                disk_op.wait()
                                print(f"[green]✓ Boot disk {boot_disk_name} deleted[/green]")
                            break
                        except Exception as disk_err:
                            if "FAILED_PRECONDITION" in str(
                                disk_err
                            ) and "read-write attachments" in str(disk_err):
                                if attempt < max_retries - 1:
                                    wait_time = 10 * (attempt + 1)
                                    print(
                                        f"[yellow]Disk still attached, waiting {wait_time}s before retry {attempt + 2}/{max_retries}...[/yellow]"
                                    )
                                    time.sleep(wait_time)
                                else:
                                    print(
                                        f"[red]Could not delete boot disk {boot_disk_name} after {max_retries} attempts: {disk_err}[/red]"
                                    )
                            else:
                                print(
                                    f"[red]Could not delete boot disk {boot_disk_name}: {disk_err}[/red]"
                                )
                                break
            except Exception:
                # Non-fatal: disk might not exist
                print(
                    f"[dim]Boot disk {boot_disk_name} not found (may have been already deleted)[/dim]"
                )

        # Step 4: Delete VPC routes (MUST happen before deleting private IP allocations)
        print("[bold]Step 4/5: Deleting VPC routes pointing to gateway allocations...[/bold]")
        deleted_routes = []
        try:
            from nebius.api.nebius.vpc.v1 import (
                ListRoutesRequest,
                ListRouteTablesRequest,
                RouteServiceClient,
                RouteTableServiceClient,
            )

            rtc = RouteTableServiceClient(client)
            rsc = RouteServiceClient(client)

            # List all route tables in the project
            rt_list_op = rtc.list(ListRouteTablesRequest(parent_id=proj_id or ""))
            rt_list = rt_list_op.wait() if hasattr(rt_list_op, "wait") else rt_list_op

            rt_items: list[t.Any] = []
            if hasattr(rt_list, "items"):
                rt_items = list(rt_list.items)
            elif hasattr(rt_list, "__iter__"):
                rt_items = list(rt_list)

            # For each route table, list its routes
            for rt in rt_items:
                rt_id = getattr(rt, "id", None) or getattr(
                    getattr(rt, "metadata", None), "id", None
                )
                rt_name = getattr(getattr(rt, "metadata", None), "name", None) or "unknown"

                if not rt_id:
                    continue

                # List routes in this table using ListRoutesRequest
                try:
                    routes_list_op = rsc.list(ListRoutesRequest(parent_id=rt_id))
                    routes_list = (
                        routes_list_op.wait() if hasattr(routes_list_op, "wait") else routes_list_op
                    )

                    route_items: list[t.Any] = []
                    if hasattr(routes_list, "items"):
                        route_items = list(routes_list.items)
                    elif hasattr(routes_list, "__iter__"):
                        route_items = list(routes_list)

                    for route in route_items:
                        route_id = getattr(route, "id", None) or getattr(
                            getattr(route, "metadata", None), "id", None
                        )
                        route_name = (
                            getattr(getattr(route, "metadata", None), "name", None) or "unknown"
                        )
                        spec = getattr(route, "spec", None)
                        next_hop = getattr(spec, "next_hop", None) if spec else None

                        # Check if this route uses one of our private allocations
                        # NextHop has an 'allocation' field with an 'id' sub-field
                        if next_hop and hasattr(next_hop, "allocation"):
                            allocation = next_hop.allocation
                            if hasattr(allocation, "id") and allocation.id:
                                nh_alloc_id = allocation.id
                                for _inst_name, alloc_id in private_alloc_ids:
                                    if nh_alloc_id == alloc_id:
                                        # Delete this route
                                        try:
                                            print(f"Deleting route {route_name} → {alloc_id}")
                                            from nebius.api.nebius.vpc.v1 import (
                                                DeleteRouteRequest,
                                            )

                                            delete_route_req = DeleteRouteRequest(id=route_id)
                                            route_op = rsc.delete(delete_route_req)
                                            if hasattr(route_op, "wait"):
                                                route_op.wait()
                                                deleted_routes.append(route_id)
                                        except Exception as e:
                                            print(f"[yellow]Could not delete route: {e}[/yellow]")
                                        break
                except Exception as e:
                    print(f"[yellow]Could not list routes for table {rt_name}: {e}[/yellow]")

            if deleted_routes:
                print(f"[green]Deleted {len(deleted_routes)} route(s)[/green]")
            else:
                print("[dim]No routes found using gateway allocations[/dim]")
        except Exception as e:
            print(f"[yellow]Could not clean up routes: {e}[/yellow]")
            print(
                "[yellow]You may need to manually delete routes before private IP allocations can be removed[/yellow]"
            )

        # Step 5: Delete static private IP allocations (after routes are deleted)
        print("[bold]Step 5/5: Deleting static private IP allocations...[/bold]")
        if private_alloc_ids:
            from nebius.api.nebius.vpc.v1 import DeleteAllocationRequest

            for inst_name, alloc_id in private_alloc_ids:
                try:
                    print(
                        f"[VMManager] Deleting private IP allocation for {inst_name} (id={alloc_id})..."
                    )
                    delete_alloc_req = DeleteAllocationRequest(id=alloc_id)
                    alloc_op = asc.delete(delete_alloc_req)
                    if hasattr(alloc_op, "wait"):
                        alloc_op.wait()
                        print("[green]✓ Private IP allocation deleted[/green]")
                except Exception as e:
                    # Check if it's already deleted (lifecycle managed by network interface)
                    if "NOT_FOUND" in str(e):
                        print(
                            "[dim]Private IP allocation already deleted (auto-managed by network interface)[/dim]"
                        )
                    elif "FAILED_PRECONDITION" in str(e) and "used as next hop for routes" in str(
                        e
                    ):
                        print(
                            f"[yellow]Could not delete private IP allocation (still used by routes): {e}[/yellow]"
                        )
                        print("[yellow]This may require manual cleanup via console or CLI[/yellow]")
                    else:
                        print(f"[yellow]Could not delete private IP allocation: {e}[/yellow]")
        else:
            print("[dim]No private IP allocations found to delete[/dim]")

        print()
        print("[green]✓ Destroy completed successfully.[/green]")
        print("[dim]Preserved resources:[/dim]")
        print("[dim]  • Network resources (VPC, subnets)[/dim]")
        print("[dim]  • Public IP allocations (reusable via external_ips in config)[/dim]")

    except Exception as e:
        print(f"[red]Error during destroy: {e}[/red]")
        raise typer.Exit(code=1) from e


@app.command(
    name="restart-tunnel",
    epilog=_command_help_epilog("restart-tunnel"),
)
def restart_tunnel(
    tunnel_name: str = typer.Argument(
        ...,
        help=(
            "Name of the tunnel to restart (use 'all' to restart all tunnels). "
            "Tunnel names are global across the config; only the owning gateway VM(s) are "
            "targeted. Use 'nebius-vpngw status' to find tunnel names."
        ),
    ),
    local_config_file: Path = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help="Path to local config file",
        show_default="nebius-vpngw.config.yaml in current directory",
    ),
) -> None:
    """
    Manually perform a full tunnel reset to recover from stale state.

    For ordinary gateways, this command connects to the owning VM via SSH,
    restarts the matching
    IPsec tunnel, and clears the matching BGP neighbor when the tunnel uses
    BGP. Useful for immediate recovery from tunnel and control-plane desync
    or after network maintenance. It is rejected for explicit VM HA, whose
    controller owns data-plane repair. In ordinary multi-VM and
    multi-connection topologies,
    a named tunnel only targets its owning connection/instance.

    """
    try:
        # Resolve config path
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)
        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        _enforce_command_applicability("restart-tunnel", plan, local_cfg)

        gateway = local_cfg.get("gateway") or {}
        local_asn = gateway.get("local_asn")

        # Get gateway instances
        gateway_group = local_cfg.get("gateway_group", {})
        instance_count = gateway_group.get("instance_count", 1)

        print(f"[bold]Found {instance_count} gateway instance(s)[/bold]")

        # Construct restart command
        remote_restart_script = _build_remote_tunnel_restart_script()
        if tunnel_name.lower() == "all":
            cmd = "sudo /usr/bin/python3 - --restart-tunnel all"
            action_desc = "all tunnels"
        else:
            cmd = f"sudo /usr/bin/python3 - --restart-tunnel {shlex.quote(tunnel_name)}"
            action_desc = f"tunnel '{tunnel_name}'"

        print(f"[bold]Restarting {action_desc}...[/bold]")

        # Get SSH credentials and resolved deployment plan
        vm_spec = gateway_group.get("vm_spec", {})
        username = vm_spec.get("ssh_username", os.environ.get("VPNGW_SSH_USER", "ubuntu"))
        key_path_str = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(key_path_str).expanduser() if key_path_str else None

        defaults_mode = _normalize_config_value(
            (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
        )
        tunnel_bgp_map: dict[str, dict[str, str]] = {}
        tunnels_by_host: dict[str, set[str]] = {}
        restart_all = tunnel_name.lower() == "all"

        for conn in local_cfg.get("connections") or []:
            conn_mode = _normalize_config_value(conn.get("routing_mode"), defaults_mode)
            for tun in conn.get("tunnels") or []:
                if _normalize_config_value(tun.get("ha_role"), "active") == "disable":
                    continue
                try:
                    inst_idx = int(tun.get("gateway_instance_index", 0) or 0)
                except Exception:
                    inst_idx = 0
                hostname = f"{plan.gateway_group.name}-{inst_idx}"
                current_tunnel_name = str(tun.get("name") or f"tunnel{inst_idx}")
                tunnels_by_host.setdefault(hostname, set()).add(current_tunnel_name)
                if not restart_all and current_tunnel_name != tunnel_name:
                    continue
                if conn_mode != "bgp":
                    continue
                peer_ip = tun.get("inner_remote_ip") or (tun.get("bgp", {}) or {}).get("remote_ip")
                if peer_ip:
                    tunnel_bgp_map.setdefault(hostname, {})[current_tunnel_name] = str(peer_ip)

        target_instances = [
            inst for inst in plan.per_instance if tunnels_by_host.get(inst.hostname)
        ]
        if restart_all:
            if not target_instances:
                print("[red]No enabled tunnels found in config.[/red]")
                raise typer.Exit(code=1)
        else:
            target_instances = [
                inst
                for inst in target_instances
                if tunnel_name in tunnels_by_host.get(inst.hostname, set())
            ]
            if not target_instances:
                available = sorted(
                    tunnel for tunnels in tunnels_by_host.values() for tunnel in tunnels
                )
                if available:
                    print(
                        f"[red]Tunnel '{tunnel_name}' not found. Available: {', '.join(available)}[/red]"
                    )
                else:
                    print("[red]No enabled tunnels found in config.[/red]")
                raise typer.Exit(code=1)

        success_count = 0
        attempted_instances = 0

        for inst in target_instances:
            hostname = inst.hostname
            external_ip = inst.external_ip

            if not external_ip:
                print(f"[yellow]⚠️  No external IP for {hostname}, skipping[/yellow]")
                continue

            attempted_instances += 1
            print(f"\n[dim]Connecting to {hostname} ({external_ip})...[/dim]")

            # Build SSH command
            ssh_cmd = _build_ssh_base_cmd(key_path)
            ssh_cmd.extend([f"{username}@{external_ip}", cmd])

            try:
                import subprocess

                result = subprocess.run(
                    ssh_cmd,
                    capture_output=True,
                    text=True,
                    input=remote_restart_script,
                    timeout=30,
                )

                if result.returncode == 0:
                    print(f"[green]✓ IPsec restart completed on {hostname}[/green]")
                    if result.stdout.strip():
                        print(f"[dim]{result.stdout.strip()}[/dim]")

                    bgp_peers = sorted(set(tunnel_bgp_map.get(hostname, {}).values()))
                    if bgp_peers and local_asn:
                        print(
                            f"[dim]Resetting matching BGP neighbor(s) on {hostname}: {', '.join(bgp_peers)}[/dim]"
                        )
                        bgp_reset_failed = False
                        for peer_ip in bgp_peers:
                            shutdown_cmd = (
                                f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
                                f"-c 'neighbor {peer_ip} shutdown'"
                            )
                            no_shutdown_cmd = (
                                f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
                                f"-c 'no neighbor {peer_ip} shutdown'"
                            )

                            shutdown_result = subprocess.run(
                                ssh_cmd[:-1] + [shutdown_cmd],
                                capture_output=True,
                                text=True,
                                timeout=20,
                            )
                            if shutdown_result.returncode != 0:
                                print(
                                    f"[red]✗ Failed to administratively shut BGP neighbor {peer_ip}[/red]"
                                )
                                if shutdown_result.stdout.strip():
                                    print(f"[dim]{shutdown_result.stdout.strip()}[/dim]")
                                if shutdown_result.stderr.strip():
                                    print(f"[dim]{shutdown_result.stderr.strip()}[/dim]")
                                bgp_reset_failed = True
                                break

                            time.sleep(1)

                            no_shutdown_result = subprocess.run(
                                ssh_cmd[:-1] + [no_shutdown_cmd],
                                capture_output=True,
                                text=True,
                                timeout=20,
                            )
                            if no_shutdown_result.returncode != 0:
                                print(
                                    f"[red]✗ Failed to re-enable BGP neighbor {peer_ip} on {hostname}[/red]"
                                )
                                if no_shutdown_result.stdout.strip():
                                    print(f"[dim]{no_shutdown_result.stdout.strip()}[/dim]")
                                if no_shutdown_result.stderr.strip():
                                    print(f"[dim]{no_shutdown_result.stderr.strip()}[/dim]")
                                bgp_reset_failed = True
                                break

                        if bgp_reset_failed:
                            continue

                        print(f"[green]✓ Matching BGP neighbor(s) reset on {hostname}[/green]")
                    elif bgp_peers and not local_asn:
                        print(
                            "[yellow]BGP peers were found for this tunnel, but gateway.local_asn is missing. "
                            "Only the IPsec tunnel was restarted.[/yellow]"
                        )

                    success_count += 1
                else:
                    print(f"[red]✗ Failed on {hostname}[/red]")
                    if result.stdout.strip():
                        print(f"[dim]{result.stdout.strip()}[/dim]")
                    if result.stderr:
                        print(f"[dim]{result.stderr.strip()}[/dim]")
            except subprocess.TimeoutExpired:
                print(f"[red]✗ Timeout connecting to {hostname}[/red]")
            except Exception as e:
                print(f"[red]✗ Error connecting to {hostname}: {e}[/red]")

        print()
        target_count = attempted_instances
        if target_count == 0:
            print("[red]✗ No target gateway instances had reachable external IPs[/red]")
            raise typer.Exit(code=1)
        if success_count == target_count:
            print(
                f"[green]✓ Successfully reset {action_desc} on all {target_count} targeted gateway(s)[/green]"
            )
            print(
                "[dim]IPsec and matching BGP sessions should re-establish within 10-15 seconds. "
                "Run 'nebius-vpngw status' to verify.[/dim]"
            )
        elif success_count > 0:
            print(
                f"[yellow]⚠️  Partial success: reset on {success_count}/{target_count} targeted gateway(s)[/yellow]"
            )
            raise typer.Exit(code=1)
        else:
            print("[red]✗ Failed to restart on any gateway[/red]")
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as e:
        print(f"[red]Error during tunnel restart: {e}[/red]")
        import traceback

        print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1) from e


@failover_app.command(
    name="tunnel",
    epilog=_command_help_epilog("failover", "tunnel"),
)
def tunnel_failover(
    tunnel_name: str | None = typer.Argument(
        None,
        help=(
            "Passive tunnel name to fail over to. Required when more than one passive "
            "candidate exists in the config, which is typical for multi-connection "
            "topologies."
        ),
    ),
    local_config_file: Path = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help="Path to local config file",
        show_default="nebius-vpngw.config.yaml in current directory",
    ),
) -> None:
    """Fail over one ordinary BGP connection/instance to a passive tunnel."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)
        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        _enforce_command_applicability("failover tunnel", plan, local_cfg)

        gateway = local_cfg.get("gateway") or {}
        local_asn = gateway.get("local_asn")
        if not local_asn:
            print("[red]gateway.local_asn is required for BGP failover.[/red]")
            raise typer.Exit(code=1)

        defaults_mode = _normalize_config_value(
            (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode"),
            "bgp",
        )

        enabled_tunnels: list[dict[str, t.Any]] = []
        for conn in local_cfg.get("connections") or []:
            conn_mode = _normalize_config_value(conn.get("routing_mode"), defaults_mode)
            for tun in conn.get("tunnels") or []:
                ha_role = _normalize_config_value(tun.get("ha_role"), "active")
                if ha_role == "disable":
                    continue
                enabled_tunnels.append(
                    {
                        "name": tun.get("name"),
                        "ha_role": ha_role,
                        "conn_name": conn.get("name"),
                        "conn_mode": conn_mode,
                        "instance_index": int(tun.get("gateway_instance_index", 0) or 0),
                        "inner_remote_ip": tun.get("inner_remote_ip")
                        or (tun.get("bgp", {}) or {}).get("remote_ip"),
                        "inner_local_ip": tun.get("inner_local_ip")
                        or (tun.get("bgp", {}) or {}).get("local_ip"),
                    }
                )

        if not enabled_tunnels:
            print("[red]No enabled tunnels found in config.[/red]")
            raise typer.Exit(code=1)

        target: dict[str, t.Any] | None = None
        if tunnel_name:
            for tun in enabled_tunnels:
                if tun.get("name") == tunnel_name:
                    target = tun
                    break
            if not target:
                names = sorted(t.get("name") or "" for t in enabled_tunnels)
                print(
                    f"[red]Tunnel '{tunnel_name}' not found. Available: {', '.join(n for n in names if n)}[/red]"
                )
                raise typer.Exit(code=1)
        else:
            if len(enabled_tunnels) != 2:
                print(
                    "[red]Multiple tunnels found. Pass the passive tunnel name as an argument: "
                    "nebius-vpngw failover tunnel <passive-tunnel-name> "
                    "--local-config-file <file>[/red]"
                )
                raise typer.Exit(code=1)
            passives = [t for t in enabled_tunnels if t.get("ha_role") == "passive"]
            if len(passives) != 1:
                print(
                    "[red]Expected exactly one passive tunnel. Check ha_role settings in your config.[/red]"
                )
                raise typer.Exit(code=1)
            target = passives[0]

        if (target.get("ha_role") or "").lower() != "passive":
            print(
                "[red]Selected tunnel is not passive. Choose a passive tunnel for failover.[/red]"
            )
            raise typer.Exit(code=1)

        if (target.get("conn_mode") or "").lower() != "bgp":
            print("[red]Manual failover is only supported for BGP routing mode.[/red]")
            raise typer.Exit(code=1)

        conn_name = target.get("conn_name") or "unknown"
        instance_index = int(target.get("instance_index") or 0)

        active = None
        for tun in enabled_tunnels:
            if (
                tun.get("conn_name") == conn_name
                and int(tun.get("instance_index") or 0) == instance_index
                and tun.get("ha_role") == "active"
            ):
                active = tun
                break

        if not active:
            print("[red]No active tunnel found for the selected connection/instance.[/red]")
            raise typer.Exit(code=1)

        active_peer_ip = active.get("inner_remote_ip")
        if not active_peer_ip:
            print("[red]Active tunnel missing inner_remote_ip; cannot fail over.[/red]")
            raise typer.Exit(code=1)
        passive_peer_ip = target.get("inner_remote_ip")
        if not passive_peer_ip:
            print("[red]Passive tunnel missing inner_remote_ip; cannot fail over.[/red]")
            raise typer.Exit(code=1)

        target_instance = None
        for inst in plan.per_instance:
            if inst.instance_index == instance_index:
                target_instance = inst
                break

        if not target_instance or not target_instance.external_ip:
            print("[red]Could not resolve gateway VM IP for failover.[/red]")
            raise typer.Exit(code=1)

        gateway_group = local_cfg.get("gateway_group", {})
        vm_spec = gateway_group.get("vm_spec", {})
        username = vm_spec.get("ssh_username", os.environ.get("VPNGW_SSH_USER", "ubuntu"))
        key_path_str = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(key_path_str).expanduser() if key_path_str else None

        print(
            f"[bold]Failing over connection '{conn_name}' on {target_instance.hostname}:[/bold] "
            f"{active.get('name')} → {target.get('name')}"
        )

        cmd = (
            f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
            f"-c 'neighbor {active_peer_ip} shutdown'"
        )
        ssh_cmd = _build_ssh_base_cmd(key_path)
        ssh_cmd.extend([f"{username}@{target_instance.external_ip}", cmd])

        import subprocess

        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"[red]Failover command failed: {err}[/red]")
            raise typer.Exit(code=1)

        ssh_base = _build_ssh_base_cmd(key_path)
        ssh_target = f"{username}@{target_instance.external_ip}"

        def _fetch_bgp_states() -> dict[str, str]:
            import json

            summary_cmd = "sudo vtysh -c 'show bgp ipv4 unicast summary json'"
            result = subprocess.run(
                ssh_base + [ssh_target, summary_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    peers = (data.get("ipv4Unicast") or {}).get("peers") or data.get("peers") or {}
                    json_states: dict[str, str] = {}
                    for ip, info in peers.items():
                        state = (
                            info.get("state")
                            or info.get("state_name")
                            or info.get("stateName")
                            or info.get("peerState")
                            or info.get("bgpState")
                        )
                        if state:
                            json_states[ip] = str(state)
                    if json_states:
                        return json_states
                except json.JSONDecodeError:
                    pass

            text_cmd = "sudo vtysh -c 'show bgp summary'"
            result = subprocess.run(
                ssh_base + [ssh_target, text_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            text_states: dict[str, str] = {}
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] and "." in parts[0]:
                        octets = parts[0].split(".")
                        if len(octets) == 4 and all(
                            o.isdigit() and 0 <= int(o) <= 255 for o in octets
                        ):
                            state = parts[-1]
                            if state.isdigit():
                                state = "Established"
                            text_states[parts[0]] = state
            return text_states

        start = time.monotonic()
        timeout_seconds = 30
        active_state = "-"
        passive_state = "-"
        confirmed = False
        while time.monotonic() - start < timeout_seconds:
            states = _fetch_bgp_states()
            active_state = states.get(str(active_peer_ip), "-")
            passive_state = states.get(str(passive_peer_ip), "-")
            if (
                str(passive_state).strip().lower() == "established"
                and str(active_state).strip().lower() != "established"
            ):
                confirmed = True
                break
            time.sleep(1)

        elapsed = time.monotonic() - start
        if confirmed:
            print(
                "[green]✓ Failover confirmed.[/green] "
                f"{active.get('name')} BGP={active_state} "
                f"{target.get('name')} BGP={passive_state} "
                f"(elapsed {elapsed:.1f}s)"
            )
            print(
                "[dim]Configured active/passive roles in YAML are unchanged by design. "
                "Use 'nebius-vpngw status' to view configured role separately from current traffic state.[/dim]"
            )
        else:
            print(
                "[yellow]⚠ Failover triggered but not confirmed within timeout.[/yellow] "
                f"{active.get('name')} BGP={active_state} "
                f"{target.get('name')} BGP={passive_state} "
                f"(elapsed {elapsed:.1f}s)"
            )
            print("[dim]Run 'nebius-vpngw status' to verify current states.[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        print(f"[red]Error during failover: {e}[/red]")
        import traceback

        print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1) from e


@failback_app.command(
    name="tunnel",
    epilog=_command_help_epilog("failback", "tunnel"),
)
def tunnel_failback(
    tunnel_name: str | None = typer.Argument(
        None,
        help=(
            "Active tunnel name to restore. Required when more than one active "
            "candidate exists in the config, which is typical for multi-connection "
            "topologies."
        ),
    ),
    local_config_file: Path = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help="Path to local config file",
        show_default="nebius-vpngw.config.yaml in current directory",
    ),
) -> None:
    """Restore one ordinary BGP connection/instance to its active tunnel."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)
        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        _enforce_command_applicability("failback tunnel", plan, local_cfg)

        gateway = local_cfg.get("gateway") or {}
        local_asn = gateway.get("local_asn")
        if not local_asn:
            print("[red]gateway.local_asn is required for BGP failback.[/red]")
            raise typer.Exit(code=1)

        defaults_mode = _normalize_config_value(
            (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode"),
            "bgp",
        )

        enabled_tunnels: list[dict[str, t.Any]] = []
        for conn in local_cfg.get("connections") or []:
            conn_mode = _normalize_config_value(conn.get("routing_mode"), defaults_mode)
            for tun in conn.get("tunnels") or []:
                ha_role = _normalize_config_value(tun.get("ha_role"), "active")
                if ha_role == "disable":
                    continue
                enabled_tunnels.append(
                    {
                        "name": tun.get("name"),
                        "ha_role": ha_role,
                        "conn_name": conn.get("name"),
                        "conn_mode": conn_mode,
                        "instance_index": int(tun.get("gateway_instance_index", 0) or 0),
                        "inner_remote_ip": tun.get("inner_remote_ip")
                        or (tun.get("bgp", {}) or {}).get("remote_ip"),
                    }
                )

        active_tunnels = [t for t in enabled_tunnels if t.get("ha_role") == "active"]
        if not active_tunnels:
            print("[red]No active tunnels found in config.[/red]")
            raise typer.Exit(code=1)

        target: dict[str, t.Any] | None = None
        if tunnel_name:
            for tun in active_tunnels:
                if tun.get("name") == tunnel_name:
                    target = tun
                    break
            if not target:
                names = sorted(t.get("name") or "" for t in active_tunnels)
                print(
                    f"[red]Active tunnel '{tunnel_name}' not found. Available: {', '.join(n for n in names if n)}[/red]"
                )
                raise typer.Exit(code=1)
        else:
            if len(active_tunnels) != 1:
                print(
                    "[red]Multiple active tunnels found. Pass the active tunnel name as an argument: "
                    "nebius-vpngw failback tunnel <active-tunnel-name> "
                    "--local-config-file <file>[/red]"
                )
                raise typer.Exit(code=1)
            target = active_tunnels[0]

        if (target.get("conn_mode") or "").lower() != "bgp":
            print("[red]Manual failback is only supported for BGP routing mode.[/red]")
            raise typer.Exit(code=1)

        active_peer_ip = target.get("inner_remote_ip")
        if not active_peer_ip:
            print("[red]Active tunnel missing inner_remote_ip; cannot fail back.[/red]")
            raise typer.Exit(code=1)

        conn_name = target.get("conn_name") or "unknown"
        instance_index = int(target.get("instance_index") or 0)

        target_instance = None
        for inst in plan.per_instance:
            if inst.instance_index == instance_index:
                target_instance = inst
                break

        if not target_instance or not target_instance.external_ip:
            print("[red]Could not resolve gateway VM IP for failback.[/red]")
            raise typer.Exit(code=1)

        gateway_group = local_cfg.get("gateway_group", {})
        vm_spec = gateway_group.get("vm_spec", {})
        username = vm_spec.get("ssh_username", os.environ.get("VPNGW_SSH_USER", "ubuntu"))
        key_path_str = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(key_path_str).expanduser() if key_path_str else None

        print(
            f"[bold]Failing back connection '{conn_name}' on {target_instance.hostname}:[/bold] "
            f"restore {target.get('name')}"
        )

        cmd = (
            f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
            f"-c 'no neighbor {active_peer_ip} shutdown'"
        )
        ssh_cmd = _build_ssh_base_cmd(key_path)
        ssh_cmd.extend([f"{username}@{target_instance.external_ip}", cmd])

        import subprocess

        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"[red]Failback command failed: {err}[/red]")
            raise typer.Exit(code=1)

        ssh_base = _build_ssh_base_cmd(key_path)
        ssh_target = f"{username}@{target_instance.external_ip}"

        def _fetch_bgp_states() -> dict[str, str]:
            import json

            summary_cmd = "sudo vtysh -c 'show bgp ipv4 unicast summary json'"
            result = subprocess.run(
                ssh_base + [ssh_target, summary_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    peers = (data.get("ipv4Unicast") or {}).get("peers") or data.get("peers") or {}
                    json_states: dict[str, str] = {}
                    for ip, info in peers.items():
                        state = (
                            info.get("state")
                            or info.get("state_name")
                            or info.get("stateName")
                            or info.get("peerState")
                            or info.get("bgpState")
                        )
                        if state:
                            json_states[ip] = str(state)
                    if json_states:
                        return json_states
                except json.JSONDecodeError:
                    pass

            text_cmd = "sudo vtysh -c 'show bgp summary'"
            result = subprocess.run(
                ssh_base + [ssh_target, text_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            text_states: dict[str, str] = {}
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] and "." in parts[0]:
                        octets = parts[0].split(".")
                        if len(octets) == 4 and all(
                            o.isdigit() and 0 <= int(o) <= 255 for o in octets
                        ):
                            state = parts[-1]
                            if state.isdigit():
                                state = "Established"
                            text_states[parts[0]] = state
            return text_states

        start = time.monotonic()
        timeout_seconds = 30
        active_state = "-"
        confirmed = False
        while time.monotonic() - start < timeout_seconds:
            states = _fetch_bgp_states()
            active_state = states.get(str(active_peer_ip), "-")
            if str(active_state).strip().lower() == "established":
                confirmed = True
                break
            time.sleep(1)

        elapsed = time.monotonic() - start
        if confirmed:
            print(
                "[green]✓ Failback confirmed.[/green] "
                f"{target.get('name')} BGP={active_state} (elapsed {elapsed:.1f}s)"
            )
            print(
                "[dim]Configured active/passive roles in YAML are unchanged by design. "
                "Traffic should now return to the configured active tunnel.[/dim]"
            )
        else:
            print(
                "[yellow]⚠ Failback triggered but not confirmed within timeout.[/yellow] "
                f"{target.get('name')} BGP={active_state} (elapsed {elapsed:.1f}s)"
            )
            print("[dim]Run 'nebius-vpngw status' to verify current states.[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        print(f"[red]Error during failback: {e}[/red]")
        import traceback

        print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1) from e


# init_config command removed; auto-creation occurs on first run without --local-config-file


def _run_vm_ha_operator_command(
    *,
    local_config_file: Path,
    agent_flag: str,
    configured_role: str | None = None,
    timeout_seconds: float = 30.0,
    status_validator: t.Callable[[dict[str, t.Any], t.Any], dict[str, t.Any]] | None = None,
) -> list[dict[str, t.Any]]:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("VM-HA operator timeout must be finite and positive")
    local_cfg = load_local_config(local_config_file)
    plan = merge_with_peer_configs(local_cfg, [])
    if plan.vm_ha is None:
        raise typer.BadParameter("VM HA is not enabled in this configuration")
    ssh_policy = require_vm_ha_ssh_policy(
        tuple(
            (
                instance.hostname,
                (instance.external_ip or "").strip() or instance.hostname,
            )
            for instance in plan.iter_instance_configs()
        ),
        enrollment_hosts=(),
        trust_scope=_vm_ha_ssh_trust_scope(local_cfg, plan),
    )
    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    results: list[dict[str, t.Any]] = []
    for instance in _vm_ha_apply_order(plan):
        node = instance.vm_ha_node
        generation = instance.vm_ha_generation
        if node is None or generation is None:
            raise ValueError("VM-HA operator action requires a complete node manifest")
        if configured_role is not None and node.role.value != configured_role:
            continue
        target = (instance.external_ip or "").strip()
        if not target:
            raise RuntimeError(f"VM-HA node {node.node_id} has no SSH target")
        command = _build_ssh_base_cmd(
            key_path,
            ssh_policy=ssh_policy,
            hostname=instance.hostname,
        )
        command.extend(
            [
                f"{username}@{target}",
                f"sudo /usr/bin/python3 -m nebius_vpngw.agent.main {agent_flag}",
            ]
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"VM-HA action failed on {node.node_id}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError(f"VM-HA action on {node.node_id} returned invalid JSON")
        request_schemas = {
            "--vm-ha-manual-failback": "nebius-vpngw/vm-ha-manual-failback-v1",
            "--vm-ha-manual-failover": "nebius-vpngw/vm-ha-manual-failover-v1",
            "--vm-ha-rearm-request": "nebius-vpngw/vm-ha-rearm-request-v1",
        }
        expected_schema = request_schemas.get(agent_flag, "nebius-vpngw/vm-ha-status-v1")
        if payload.get("schema") != expected_schema:
            raise ValueError(f"VM-HA action on {node.node_id} returned the wrong record type")
        if not (
            payload.get("cluster_id") == plan.vm_ha.cluster_id
            and payload.get("node_id") == node.node_id
            and payload.get("generation_id") == generation.generation_id
        ):
            raise ValueError(f"VM-HA action on {node.node_id} returned stale node identity")
        if agent_flag not in request_schemas and payload.get("configured_role") != node.role.value:
            raise ValueError(f"VM-HA status on {node.node_id} returned the wrong configured role")
        if agent_flag not in request_schemas and status_validator is not None:
            payload = status_validator(payload, instance)
        results.append(payload)
    return results


@dataclass(frozen=True)
class _VMHAPlannedPreparation:
    outcome: t.Literal["already-owner", "standby-ready"]
    target_role: str
    record: dict[str, t.Any]


def _prepare_vm_ha_manual_failback_target(
    *,
    local_config_file: Path,
    timeout_seconds: int = 300,
) -> None:
    """Prepare the configured active through the canonical owner-side path."""

    _prepare_vm_ha_planned_target(
        local_config_file=local_config_file,
        target_role="active",
        timeout_seconds=timeout_seconds,
    )


def _require_vm_ha_manual_failover_target(*, local_config_file: Path) -> None:
    """Prepare the configured passive through the canonical owner-side path."""

    _prepare_vm_ha_planned_target(
        local_config_file=local_config_file,
        target_role="passive",
    )


def _prepare_vm_ha_configured_passive_standby(
    *,
    local_config_file: Path,
    timeout_seconds: int = 300,
) -> None:
    """Prepare whichever exact member is currently the non-owner."""

    _prepare_vm_ha_planned_target(
        local_config_file=local_config_file,
        target_role=None,
        timeout_seconds=timeout_seconds,
    )


def _prepare_vm_ha_planned_target(
    *,
    local_config_file: Path,
    target_role: str | None,
    timeout_seconds: int = 300,
    command: str | None = None,
) -> _VMHAPlannedPreparation:
    """Prepare the exact non-owner through the owner-side rearm bulkhead."""

    local_cfg = load_local_config(local_config_file)
    plan = merge_with_peer_configs(local_cfg, [])
    if command is not None:
        _enforce_command_applicability(command, plan, local_cfg)
    if plan.vm_ha is None:
        raise typer.BadParameter("VM HA is not enabled in this configuration")
    project_id = str(local_cfg.get("project_id") or "").strip()
    gateway_name = str(getattr(plan.gateway_group, "name", "") or "")
    state = VMHALifecycleStore(local_config_file).read(
        expected_project_id=project_id or None,
        expected_gateway_name=gateway_name,
    )
    if state is None or state.status is not VMHALifecycleStatus.ACTIVE:
        raise RuntimeError("planned VM-HA transfer requires an exact ACTIVE lifecycle record")
    if state.cluster_id != plan.vm_ha.cluster_id or state.transaction is None:
        raise RuntimeError("planned VM-HA transfer lifecycle identity does not match the config")
    status_runtime_binding = _vm_ha_status_runtime_binding(state)

    def validate_planned_status(payload: dict[str, t.Any], instance: t.Any) -> dict[str, t.Any]:
        return _validate_vm_ha_planned_status(
            payload,
            inst_cfg=instance,
            runtime_binding=status_runtime_binding,
        )

    planned_by_role: dict[str, t.Any] = {}
    for instance in plan.iter_instance_configs():
        node = instance.vm_ha_node
        if node is None or node.role.value in planned_by_role:
            raise RuntimeError("planned VM-HA transfer has an invalid member set")
        planned_by_role[node.role.value] = instance
    members_by_role = {member.role: member for member in state.members}
    if set(planned_by_role) != {"active", "passive"} or set(members_by_role) != {
        "active",
        "passive",
    }:
        raise RuntimeError("planned VM-HA transfer requires one member in each role")
    bindings = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
    for role, instance in planned_by_role.items():
        node = instance.vm_ha_node
        member = members_by_role[role]
        assert node is not None
        if not (
            instance.hostname == member.instance_name
            and node.node_id == member.node_id
            and str(instance.external_ip or "").strip() == member.public_ip
            and member.compute_id
            and bindings.get(f"compute:{member.instance_name}", member.compute_id)
            == member.compute_id
            and member.network_interface_name
        ):
            raise RuntimeError("planned VM-HA member identity does not match lifecycle")

    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    ssh_policy = require_vm_ha_ssh_policy(
        tuple(
            (instance.hostname, str(instance.external_ip or "").strip())
            for instance in plan.iter_instance_configs()
        ),
        enrollment_hosts=(),
        trust_scope=_vm_ha_ssh_trust_scope(local_cfg, plan),
    )
    auth_token = _ensure_authentication(required=True, show_progress=True)
    manager = VMManager(
        project_id=project_id,
        zone=plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=str(local_cfg.get("tenant_id") or "").strip() or None,
        region_id=str(local_cfg.get("region_id") or "").strip() or None,
        ssh_policy=ssh_policy,
        management_key_path=key_path,
    )
    sdk = manager._get_client()
    if sdk is None:
        raise RuntimeError("Nebius SDK client is unavailable for planned VM-HA preparation")
    deadline = time.monotonic() + timeout_seconds

    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("planned VM-HA preparation timed out")
        return remaining

    calls = NebiusSDKCloudClient(sdk, request_timeout_provider=remaining_timeout)
    adapter = VMHACloudAdapter(
        instance_reader=calls.get_instance,
        instance_stopper=calls.stop_instance,
        allocation_reader=calls.get_allocation,
        alias_allocation_setter=calls.set_alias_allocation,
    )
    active = members_by_role["active"]
    passive = members_by_role["passive"]
    owners = {
        "active": AllocationOwner(active.compute_id, active.network_interface_name),
        "passive": AllocationOwner(passive.compute_id, passive.network_interface_name),
    }

    def observe() -> t.Any:
        return adapter.observe_cluster(
            allocation_id=state.allocation_id,
            former_owner=owners["active"],
            candidate=owners["passive"],
        )

    observation = observe()
    owner_roles = [role for role, owner in owners.items() if observation.allocation.owner == owner]
    if len(owner_roles) != 1:
        raise RuntimeError("planned VM-HA preparation has no exact current owner")
    owner_role = owner_roles[0]
    resolved_target_role = target_role or ("passive" if owner_role == "active" else "active")
    if resolved_target_role not in {"active", "passive"}:
        raise ValueError("planned VM-HA target role is invalid")
    owner_member = members_by_role[owner_role]
    target_member = members_by_role[resolved_target_role]

    def members(current: t.Any) -> tuple[t.Any, t.Any]:
        by_role = {"active": current.former, "passive": current.candidate}
        owner_observation = by_role[owner_role]
        target_observation = by_role[resolved_target_role]
        if not (
            current.allocation.owner == owners[owner_role]
            and owner_observation.state is InstanceCloudState.RUNNING
            and owner_observation.has_alias_allocation(
                owner_member.network_interface_name, state.allocation_id
            )
            and not target_observation.has_alias_allocation(
                target_member.network_interface_name, state.allocation_id
            )
        ):
            raise RuntimeError("planned VM-HA owner or target evidence drifted")
        return owner_observation, target_observation

    if resolved_target_role == owner_role:
        by_role = {"active": observation.former, "passive": observation.candidate}
        owner_observation = by_role[owner_role]
        other_role = "passive" if owner_role == "active" else "active"
        other_member = members_by_role[other_role]
        other_observation = by_role[other_role]
        if not (
            owner_observation.state is InstanceCloudState.RUNNING
            and owner_observation.has_alias_allocation(
                owner_member.network_interface_name, state.allocation_id
            )
            and not other_observation.has_alias_allocation(
                other_member.network_interface_name, state.allocation_id
            )
        ):
            raise RuntimeError("planned VM-HA target owner is not exact and Running")
        manager.wait_for_vm_ha_member_ssh(
            owner_member.instance_name,
            owner_member.public_ip,
            username=username,
            timeout=remaining_timeout(),
        )
        records = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag="--vm-ha-status",
            configured_role=owner_role,
            timeout_seconds=remaining_timeout(),
            status_validator=validate_planned_status,
        )
        if len(records) != 1:
            raise RuntimeError("planned VM-HA owner status did not resolve exactly one member")
        record = records[0]
        if not (
            record.get("state") == "active"
            and record.get("promotion_ready") is True
            and record.get("data_plane_mode") == "active"
            and record.get("observed_owner_node_id") == owner_member.node_id
            and record.get("apply_locked") is False
            and record.get("pending_operation_id") is None
        ):
            raise RuntimeError("planned VM-HA target owner is not healthy and stable")
        final_owner = observe()
        final_by_role = {"active": final_owner.former, "passive": final_owner.candidate}
        if not (
            final_owner.allocation.owner == owners[owner_role]
            and final_by_role[owner_role].state is InstanceCloudState.RUNNING
            and final_by_role[owner_role].has_alias_allocation(
                owner_member.network_interface_name, state.allocation_id
            )
            and not final_by_role[other_role].has_alias_allocation(
                other_member.network_interface_name, state.allocation_id
            )
        ):
            raise RuntimeError("planned VM-HA owner drifted before no-op admission")
        return _VMHAPlannedPreparation("already-owner", resolved_target_role, record)

    _owner_observation, target_observation = members(observation)
    if target_observation.state is InstanceCloudState.STOPPED:
        retries = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag="--vm-ha-rearm-request",
            configured_role=owner_role,
            timeout_seconds=remaining_timeout(),
        )
        if len(retries) != 1:
            raise RuntimeError("VM-HA rearm retry did not target the exact owner")
    elif target_observation.state not in {
        InstanceCloudState.RUNNING,
        InstanceCloudState.TRANSITIONAL,
    }:
        raise RuntimeError("planned VM-HA target Compute is not safely startable")

    while target_observation.state is not InstanceCloudState.RUNNING:
        if time.monotonic() >= deadline:
            raise RuntimeError("planned VM-HA target did not become Running")
        time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
        observation = observe()
        _owner_observation, target_observation = members(observation)
        if target_observation.state not in {
            InstanceCloudState.RUNNING,
            InstanceCloudState.TRANSITIONAL,
        }:
            raise RuntimeError("planned VM-HA target left its safe startup transition")

    manager.wait_for_vm_ha_member_ssh(
        target_member.instance_name,
        target_member.public_ip,
        username=username,
        timeout=remaining_timeout(),
    )

    def standby_status() -> dict[str, t.Any]:
        records = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag="--vm-ha-status",
            configured_role=resolved_target_role,
            timeout_seconds=remaining_timeout(),
            status_validator=validate_planned_status,
        )
        if len(records) != 1:
            raise RuntimeError("planned VM-HA preparation did not resolve one exact target")
        return records[0]

    def standby_ready(record: t.Mapping[str, t.Any]) -> bool:
        return bool(
            record.get("standby_ready") is True
            and record.get("standby_readiness_reasons") == []
            and record.get("data_plane_mode") == "passive"
            and record.get("observed_owner_node_id") == owner_member.node_id
            and record.get("apply_locked") is False
            and record.get("pending_operation_id") is None
        )

    record = standby_status()
    while not standby_ready(record):
        if record.get("data_plane_mode") == "active" or record.get(
            "observed_owner_node_id"
        ) not in {None, owner_member.node_id}:
            raise RuntimeError("planned VM-HA target reported unsafe standby evidence")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "planned VM-HA target did not establish fresh standby readiness: "
                f"{record.get('standby_readiness_reasons')}"
            )
        time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
        record = standby_status()

    final = observe()
    _final_owner, final_target = members(final)
    if final_target.state is not InstanceCloudState.RUNNING:
        raise RuntimeError("planned VM-HA target did not remain Running at request admission")
    final_record = standby_status()
    if not standby_ready(final_record):
        raise RuntimeError("planned VM-HA target readiness drifted before request admission")
    return _VMHAPlannedPreparation("standby-ready", resolved_target_role, final_record)


@dataclass(frozen=True)
class _VMHAMTLSRotationMember:
    instance: t.Any
    target: str
    compute_id: str
    node_id: str
    role: str
    generation_id: str
    mtls: dict[str, object]
    agent: dict[str, t.Any]


@dataclass(frozen=True)
class _VMHAMTLSRotationPlan:
    config_path: Path
    local_config: dict[str, t.Any]
    project_id: str
    gateway_name: str
    cluster_id: str
    allocation_id: str
    owner_node_id: str
    passive_node_id: str
    operation_id: str
    target_epoch: int
    digest: str
    plan_payload: dict[str, object]
    members: tuple[_VMHAMTLSRotationMember, _VMHAMTLSRotationMember]
    ssh_policy: SSHTrustPolicy


_VM_HA_MTLS_ROTATION_PHASES = (
    "inhibit-both",
    "prepare-both",
    "expand-trust-both",
    "switch-passive",
    "switch-owner",
    "verify-three-fresh-rounds",
    "commit-and-prune",
    "release-inhibition",
)


def _vm_ha_mtls_remote_result(response: object) -> dict[str, object]:
    result = _vm_ha_mtls_action_result(response)
    if not isinstance(result, dict):
        raise RuntimeError("managed mTLS rotation returned invalid evidence")
    return t.cast(dict[str, object], result)


def _inspect_vm_ha_mtls_rotation(config_path: Path) -> _VMHAMTLSRotationPlan:
    """Build a mutation-free rotation plan from exact cloud, SSH, and node truth."""

    local_config = load_local_config(config_path)
    deployment = merge_with_peer_configs(local_config, [])
    _enforce_command_applicability("set-vm-ha-mtls", deployment, local_config)
    if deployment.vm_ha is None:
        raise typer.BadParameter("VM HA is not enabled in this configuration")
    project_id = str(local_config.get("project_id") or "").strip()
    gateway_name = str(deployment.gateway_group.name or "").strip()
    if not project_id or not gateway_name:
        raise RuntimeError("managed mTLS rotation requires exact project and gateway identity")
    lifecycle = VMHALifecycleStore(config_path).read(
        expected_project_id=project_id,
        expected_gateway_name=gateway_name,
    )
    if (
        lifecycle is None
        or lifecycle.status is not VMHALifecycleStatus.ACTIVE
        or lifecycle.cluster_id != deployment.vm_ha.cluster_id
        or lifecycle.transaction is None
        or lifecycle.transaction.pending_effect is not None
    ):
        raise RuntimeError("managed mTLS rotation requires an exact stable ACTIVE lifecycle")
    runtime_binding = _vm_ha_status_runtime_binding(lifecycle)
    lifecycle_by_node = {member.node_id: member for member in lifecycle.members}
    instances = tuple(deployment.iter_instance_configs())
    if len(instances) != 2 or len(lifecycle_by_node) != 2:
        raise RuntimeError("managed mTLS rotation requires exactly two lifecycle members")
    instance_rows: list[tuple[t.Any, VMHALifecycleMember]] = []
    pin_targets: list[tuple[str, str]] = []
    for instance in instances:
        node = instance.vm_ha_node
        generation = instance.vm_ha_generation
        if node is None or generation is None or node.node_id not in lifecycle_by_node:
            raise RuntimeError("managed mTLS rotation member identity is incomplete")
        member = lifecycle_by_node[node.node_id]
        target = str(member.public_ip or "").strip()
        if not (
            target
            and member.instance_name == instance.hostname
            and member.role == node.role.value
            and member.compute_id
            and member.network_interface_name
            and str(instance.external_ip or "").strip() == target
        ):
            raise RuntimeError("managed mTLS rotation member identity drifted")
        instance_rows.append((instance, member))
        pin_targets.append((instance.hostname, target))
    ssh_policy = require_vm_ha_ssh_policy(
        tuple(pin_targets),
        enrollment_hosts=(),
        trust_scope=_vm_ha_ssh_trust_scope(local_config, deployment),
    )
    vm_spec = (local_config.get("gateway_group") or {}).get("vm_spec") or {}
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    auth_token = _ensure_authentication(required=True, show_progress=True)
    manager = VMManager(
        project_id=project_id,
        zone=deployment.gateway_group.region,
        auth_token=auth_token,
        tenant_id=str(local_config.get("tenant_id") or "").strip() or None,
        region_id=str(local_config.get("region_id") or "").strip() or None,
        ssh_policy=ssh_policy,
        management_key_path=key_path,
    )
    local_prefixes = [
        str(prefix) for prefix in ((local_config.get("gateway") or {}).get("local_prefixes") or [])
    ]
    cloud_observation = manager.observe_vm_ha_migration_state(
        deployment.gateway_group, local_prefixes
    )
    observed_members = cloud_observation.get("members")
    if not (
        isinstance(observed_members, list)
        and len(observed_members) == 2
        and all(
            isinstance(member, dict)
            and member.get("state") == InstanceCloudState.RUNNING.value
            for member in observed_members
        )
    ):
        raise RuntimeError("managed mTLS rotation requires both members Running")
    authority = _vm_ha_cloud_authority(lifecycle, cloud_observation)
    if authority.condition != "exact" or authority.owner_node_id not in lifecycle_by_node:
        detail = ", ".join(authority.reasons) or "cloud-authority-not-exact"
        raise RuntimeError(f"managed mTLS rotation is blocked: {detail}")
    owner_node_id = t.cast(str, authority.owner_node_id)
    passive_node_id = next(node_id for node_id in lifecycle_by_node if node_id != owner_node_id)

    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    ssh = SSHPush(ssh_policy=ssh_policy)
    members: list[_VMHAMTLSRotationMember] = []
    operation_candidates: set[str] = set()
    target_epochs: set[int] = set()
    current_epochs: list[int] = []
    current_fingerprints: dict[str, str] = {}
    for instance, member in instance_rows:
        target = str(member.public_ip)
        mtls = _vm_ha_mtls_remote_result(
            ssh.run_vm_ha_mtls_action(
                target,
                instance.hostname,
                local_config,
                action="status",
                request={},
            )
        )
        if not _vm_ha_mtls_exact_identity(
            mtls,
            SimpleNamespace(node_id=member.node_id, compute_id=member.compute_id),
            lifecycle.cluster_id,
        ):
            raise RuntimeError("managed mTLS rotation found a missing or foreign local identity")
        if mtls.get("operation_kind") not in {None, "rotation"}:
            raise RuntimeError("managed mTLS rotation found a competing mTLS transaction")
        for field in ("operation_id", "inhibition_operation_id"):
            value = mtls.get(field)
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                    raise RuntimeError("managed mTLS rotation found an invalid operation identity")
                operation_candidates.add(value)
        target_epoch = mtls.get("target_epoch")
        if target_epoch is not None:
            if not isinstance(target_epoch, int) or isinstance(target_epoch, bool):
                raise RuntimeError("managed mTLS rotation found an invalid target epoch")
            target_epochs.add(target_epoch)
        current_epoch = int(t.cast(int, mtls["epoch"]))
        current_epochs.append(current_epoch)
        current_fingerprints[member.node_id] = str(mtls["certificate_fingerprint"])
        agent = _fetch_vm_ha_agent_status(
            target=target,
            hostname=instance.hostname,
            username=username,
            key_path=key_path,
            ssh_policy=ssh_policy,
            inst_cfg=instance,
            runtime_binding=runtime_binding,
        )
        if (
            agent.get("observed_owner_node_id") != owner_node_id
            or agent.get("pending_operation_id") is not None
            or agent.get("data_plane_mode")
            != ("active" if member.node_id == owner_node_id else "passive")
        ):
            raise RuntimeError("managed mTLS rotation requires one stable owner and passive")
        members.append(
            _VMHAMTLSRotationMember(
                instance=instance,
                target=target,
                compute_id=member.compute_id,
                node_id=member.node_id,
                role=member.role,
                generation_id=instance.vm_ha_generation.generation_id,
                mtls=mtls,
                agent=agent,
            )
        )
    if len(operation_candidates) > 1 or len(target_epochs) > 1:
        raise RuntimeError("managed mTLS rotation state is owned by conflicting transactions")
    pending_operation = next(iter(operation_candidates), None)
    pending_target = next(iter(target_epochs), None)
    target_epoch = pending_target or max(current_epochs) + 1
    if target_epoch <= max(current_epochs) and pending_operation is None:
        raise RuntimeError("managed mTLS rotation target epoch does not advance")
    if pending_operation is None:
        for rotation_member in members:
            peer_fingerprint = current_fingerprints[
                next(
                    node_id
                    for node_id in current_fingerprints
                    if node_id != rotation_member.node_id
                )
            ]
            if not (
                rotation_member.mtls.get("state") == "healthy"
                and rotation_member.mtls.get("operation_id") is None
                and rotation_member.mtls.get("inhibited") is False
                and rotation_member.mtls.get("peer_fingerprints") == [peer_fingerprint]
                and rotation_member.agent.get("apply_locked") is False
                and rotation_member.agent.get("apply_operation_id") is None
            ):
                raise RuntimeError("managed mTLS rotation requires a healthy exact starting pair")
        operation_id = _canonical_digest(
            {
                "domain": "nebius-vpngw/vm-ha-mtls-rotation-v1",
                "cluster_id": lifecycle.cluster_id,
                "allocation_id": lifecycle.allocation_id,
                "owner_node_id": owner_node_id,
                "target_epoch": target_epoch,
                "members": [
                    {
                        "node_id": member.node_id,
                        "compute_id": member.compute_id,
                        "generation_id": member.generation_id,
                        "epoch": member.mtls["epoch"],
                        "certificate_fingerprint": member.mtls[
                            "certificate_fingerprint"
                        ],
                    }
                    for member in sorted(members, key=lambda item: item.node_id)
                ],
            }
        )
    else:
        operation_id = pending_operation
        for rotation_member in members:
            lock_operation = rotation_member.agent.get("apply_operation_id")
            if lock_operation not in {None, operation_id}:
                raise RuntimeError("managed mTLS rotation found a competing remote writer")
    for rotation_member in members:
        if rotation_member.agent.get("repair") is not None or rotation_member.agent.get(
            "rearm_phase"
        ) in {
            "starting",
            "blocked",
        }:
            raise RuntimeError("managed mTLS rotation found another controller workflow")
        if pending_operation is None and (
            rotation_member.agent.get("state") not in {"normal", "active"}
            or rotation_member.agent.get("rearm_phase") == "inhibited"
        ):
            raise RuntimeError("managed mTLS rotation requires stable controller state")

    plan_payload: dict[str, object] = {
        "schema": "nebius-vpngw/vm-ha-mtls-rotation-plan-v1",
        "operation_id": operation_id,
        "cluster_id": lifecycle.cluster_id,
        "allocation_id": lifecycle.allocation_id,
        "owner_node_id": owner_node_id,
        "passive_node_id": passive_node_id,
        "target_epoch": target_epoch,
        "phases": list(_VM_HA_MTLS_ROTATION_PHASES),
        "members": [
            {
                "hostname": member.instance.hostname,
                "node_id": member.node_id,
                "role": member.role,
                "compute_id": member.compute_id,
                "generation_id": member.generation_id,
                "epoch": member.mtls["epoch"],
                "certificate_fingerprint": member.mtls["certificate_fingerprint"],
                "transaction_phase": member.mtls["phase"],
                "inhibited": member.mtls["inhibited"],
            }
            for member in sorted(members, key=lambda item: item.node_id)
        ],
        "cloud_observation_digest": _canonical_digest(cloud_observation),
    }
    digest = _canonical_digest(plan_payload)
    return _VMHAMTLSRotationPlan(
        config_path=config_path,
        local_config=local_config,
        project_id=project_id,
        gateway_name=gateway_name,
        cluster_id=lifecycle.cluster_id,
        allocation_id=lifecycle.allocation_id,
        owner_node_id=owner_node_id,
        passive_node_id=passive_node_id,
        operation_id=operation_id,
        target_epoch=target_epoch,
        digest=digest,
        plan_payload=plan_payload,
        members=t.cast(
            tuple[_VMHAMTLSRotationMember, _VMHAMTLSRotationMember], tuple(members)
        ),
        ssh_policy=ssh_policy,
    )


def _render_vm_ha_mtls_rotation_plan(plan: _VMHAMTLSRotationPlan) -> dict[str, object]:
    return {
        "schema": "nebius-vpngw/vm-ha-mtls-rotation-preview-v1",
        "plan_digest": plan.digest,
        "operation": "resume" if any(member.mtls["operation_id"] for member in plan.members) else "rotate",
        "owner_role": next(
            member.role for member in plan.members if member.node_id == plan.owner_node_id
        ),
        "target_epoch": plan.target_epoch,
        "members": [
            {
                "hostname": member.instance.hostname,
                "role": member.role,
                "epoch": member.mtls["epoch"],
                "fingerprint": str(member.mtls["certificate_fingerprint"])[:16],
                "phase": member.mtls["phase"] or "healthy",
            }
            for member in sorted(plan.members, key=lambda item: item.role)
        ],
        "phases": list(_VM_HA_MTLS_ROTATION_PHASES),
    }


def _execute_vm_ha_mtls_rotation(plan: _VMHAMTLSRotationPlan) -> None:
    """Resume one exact rotation; any failure retains inhibition for safe retry."""

    ssh = SSHPush(ssh_policy=plan.ssh_policy)
    by_node = {member.node_id: member for member in plan.members}
    owner = by_node[plan.owner_node_id]
    passive = by_node[plan.passive_node_id]

    def action(member: _VMHAMTLSRotationMember, name: str, request: dict[str, object]):
        return _vm_ha_mtls_remote_result(
            ssh.run_vm_ha_mtls_action(
                member.target,
                member.instance.hostname,
                plan.local_config,
                action=name,
                request=request,
            )
        )

    def inhibition_request(member: _VMHAMTLSRotationMember) -> dict[str, object]:
        return {
            "operation_id": plan.operation_id,
            "cluster_id": plan.cluster_id,
            "node_id": member.node_id,
            "generation_id": member.generation_id,
        }
    for member in plan.members:
        action(member, "inhibit", inhibition_request(member))

    vm_spec = (plan.local_config.get("gateway_group") or {}).get("vm_spec") or {}
    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    lifecycle = VMHALifecycleStore(plan.config_path).read(
        expected_project_id=plan.project_id,
        expected_gateway_name=plan.gateway_name,
    )
    if lifecycle is None:
        raise RuntimeError("managed mTLS rotation lifecycle disappeared")
    runtime_binding = _vm_ha_status_runtime_binding(lifecycle)

    def fetch(member: _VMHAMTLSRotationMember, predicate: t.Callable[[dict[str, t.Any]], bool]):
        return _wait_for_vm_ha_agent_status(
            target=member.target,
            hostname=member.instance.hostname,
            username=username,
            key_path=key_path,
            ssh_policy=plan.ssh_policy,
            inst_cfg=member.instance,
            runtime_binding=runtime_binding,
            expected_apply_locked=True,
            expected_operation_id=plan.operation_id,
            predicate=predicate,
            timeout_seconds=120.0,
            poll_seconds=1.0,
        )

    for member in plan.members:
        fetch(member, lambda status: status.get("apply_locked") is True)

    receipts: dict[str, dict[str, object]] = {}
    for member in plan.members:
        receipts[member.node_id] = action(
            member,
            "prepare",
            {
                "operation_id": plan.operation_id,
                "operation_kind": "rotation",
                "cluster_id": plan.cluster_id,
                "node_id": member.node_id,
                "compute_id": member.compute_id,
                "target_epoch": plan.target_epoch,
                "peer_epoch": plan.target_epoch,
            },
        )
    for member in plan.members:
        peer_id = next(node_id for node_id in receipts if node_id != member.node_id)
        action(
            member,
            "stage-peer",
            {"operation_id": plan.operation_id, "peer_receipt": receipts[peer_id]},
        )
    for member in plan.members:
        action(member, "expand-trust", {"operation_id": plan.operation_id})

    action(passive, "activate", {"operation_id": plan.operation_id})
    owner_old_fingerprint = str(owner.mtls["certificate_fingerprint"])

    def matches_pair(
        member: _VMHAMTLSRotationMember,
        status: dict[str, t.Any],
        *,
        local_fingerprint: str,
        peer_fingerprint: str,
    ) -> bool:
        mtls = status.get("mtls")
        peer = mtls.get("peer") if isinstance(mtls, dict) else None
        return bool(
            isinstance(mtls, dict)
            and mtls.get("certificate_fingerprint") == local_fingerprint
            and isinstance(peer, dict)
            and peer.get("fresh") is True
            and peer.get("certificate_fingerprint") == peer_fingerprint
        )

    owner_remote = action(owner, "status", {})
    if owner_remote.get("certificate_fingerprint") != receipts[owner.node_id][
        "certificate_fingerprint"
    ]:
        fetch(
            passive,
            lambda status: matches_pair(
                passive,
                status,
                local_fingerprint=str(
                    receipts[passive.node_id]["certificate_fingerprint"]
                ),
                peer_fingerprint=owner_old_fingerprint,
            ),
        )
        fetch(
            owner,
            lambda status: matches_pair(
                owner,
                status,
                local_fingerprint=owner_old_fingerprint,
                peer_fingerprint=str(
                    receipts[passive.node_id]["certificate_fingerprint"]
                ),
            ),
        )
    action(owner, "activate", {"operation_id": plan.operation_id})

    previous_sequences: dict[str, tuple[str, int]] = {}
    for round_index in range(3):
        round_statuses: dict[str, dict[str, t.Any]] = {}
        for member in plan.members:
            peer_id = next(node_id for node_id in receipts if node_id != member.node_id)

            def fresh_round(status: dict[str, t.Any], member=member, peer_id=peer_id) -> bool:
                if not matches_pair(
                    member,
                    status,
                    local_fingerprint=str(
                        receipts[member.node_id]["certificate_fingerprint"]
                    ),
                    peer_fingerprint=str(receipts[peer_id]["certificate_fingerprint"]),
                ):
                    return False
                peer = t.cast(dict[str, t.Any], t.cast(dict[str, t.Any], status["mtls"])["peer"])
                sequence = peer.get("sequence")
                boot_id = peer.get("boot_id")
                previous = previous_sequences.get(member.node_id)
                return bool(
                    isinstance(sequence, int)
                    and not isinstance(sequence, bool)
                    and sequence > 0
                    and isinstance(boot_id, str)
                    and boot_id
                    and (previous is None or boot_id != previous[0] or sequence > previous[1])
                )

            round_statuses[member.node_id] = fetch(member, fresh_round)
        for member in plan.members:
            peer_id = next(node_id for node_id in receipts if node_id != member.node_id)
            mtls = t.cast(dict[str, t.Any], round_statuses[member.node_id]["mtls"])
            peer = t.cast(dict[str, t.Any], mtls["peer"])
            previous_sequences[member.node_id] = (str(peer["boot_id"]), int(peer["sequence"]))
            observation_id = _canonical_digest(
                {
                    "domain": "nebius-vpngw/vm-ha-mtls-rotation-observation-v1",
                    "operation_id": plan.operation_id,
                    "round": round_index + 1,
                    "local_node_id": member.node_id,
                    "peer_node_id": peer_id,
                    "peer_boot_id": peer["boot_id"],
                    "peer_sequence": peer["sequence"],
                    "local_certificate_fingerprint": receipts[member.node_id][
                        "certificate_fingerprint"
                    ],
                    "peer_certificate_fingerprint": receipts[peer_id][
                        "certificate_fingerprint"
                    ],
                }
            )
            action(
                member,
                "record-observation",
                {
                    "operation_id": plan.operation_id,
                    "local_certificate_fingerprint": receipts[member.node_id][
                        "certificate_fingerprint"
                    ],
                    "peer_certificate_fingerprint": receipts[peer_id][
                        "certificate_fingerprint"
                    ],
                    "local_epoch": plan.target_epoch,
                    "peer_epoch": plan.target_epoch,
                    "observation_id": observation_id,
                },
            )

    for name in ("commit", "prune"):
        for member in plan.members:
            action(member, name, {"operation_id": plan.operation_id})
    for member in plan.members:
        action(member, "release-inhibition", inhibition_request(member))
    for member in plan.members:
        final = action(member, "status", {})
        peer_id = next(node_id for node_id in receipts if node_id != member.node_id)
        if not (
            final.get("state") == "healthy"
            and final.get("operation_id") is None
            and final.get("inhibited") is False
            and final.get("epoch") == plan.target_epoch
            and final.get("certificate_fingerprint")
            == receipts[member.node_id]["certificate_fingerprint"]
            and final.get("peer_fingerprints")
            == [receipts[peer_id]["certificate_fingerprint"]]
        ):
            raise RuntimeError("managed mTLS rotation final state is not exact")


@app.command(
    name="set-vm-ha-mtls",
    epilog=_command_help_epilog("set-vm-ha-mtls"),
)
def set_vm_ha_mtls(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the exact rotation plan"),
    approve: str | None = typer.Option(
        None, "--approve", metavar="PLAN_DIGEST", help="Approve the exact printed plan digest"
    ),
) -> None:
    """Rotate both VM-HA mTLS identities through a resumable passive-first transaction."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    try:
        inspected = _inspect_vm_ha_mtls_rotation(config_path)
        preview = _render_vm_ha_mtls_rotation_plan(inspected)
        print(json.dumps(preview, sort_keys=True, indent=2))
        if dry_run:
            return
        if approve is not None:
            if approve != inspected.digest:
                raise RuntimeError("managed mTLS rotation approval digest does not match")
        elif not typer.confirm("Execute this exact VM-HA mTLS rotation?"):
            raise typer.Abort()
        with VMHAApplyLock(
            project_id=inspected.project_id,
            gateway_name=inspected.gateway_name,
        ):
            current = _inspect_vm_ha_mtls_rotation(config_path)
            if current.digest != inspected.digest:
                raise RuntimeError("managed mTLS rotation plan drifted after approval")
            _execute_vm_ha_mtls_rotation(current)
        print(
            json.dumps(
                {
                    "schema": "nebius-vpngw/vm-ha-mtls-rotation-result-v1",
                    "status": "complete",
                    "target_epoch": inspected.target_epoch,
                },
                sort_keys=True,
            )
        )
    except (typer.Abort, typer.Exit):
        raise
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[red]Managed mTLS rotation failed:[/red] {error}")
        raise typer.Exit(code=1) from error


@app.command(
    name="vm-ha-rearm",
    epilog=_command_help_epilog("vm-ha-rearm"),
)
def vm_ha_rearm(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
) -> None:
    """Retry and verify whichever exact VM-HA member is currently the non-owner."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    preparation = _prepare_vm_ha_planned_target(
        local_config_file=config_path,
        target_role=None,
        command="vm-ha-rearm",
    )
    print(json.dumps(preparation.record, sort_keys=True))


@failback_app.command(
    name="vm",
    epilog=_command_help_epilog("failback", "vm"),
)
def vm_ha_failback(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
) -> None:
    """Fail back through fencing, or no-op when the active already owns safely."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    preparation = _prepare_vm_ha_planned_target(
        local_config_file=config_path,
        target_role="active",
        command="failback vm",
    )
    if preparation.outcome == "already-owner":
        print(
            json.dumps(
                {
                    "schema": "nebius-vpngw/vm-ha-planned-transfer-result-v1",
                    "outcome": "already-owner",
                    "target_role": "active",
                    "request_submitted": False,
                },
                sort_keys=True,
            )
        )
        return
    records = _run_vm_ha_operator_command(
        local_config_file=config_path,
        agent_flag="--vm-ha-manual-failback",
        configured_role="active",
    )
    if len(records) != 1:
        raise RuntimeError("manual VM-HA failback did not target exactly one configured active")
    print(json.dumps(records[0], sort_keys=True))


@failover_app.command(
    name="vm",
    epilog=_command_help_epilog("failover", "vm"),
)
def vm_ha_failover(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
) -> None:
    """Fail over through fencing, or no-op when the passive already owns safely."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    preparation = _prepare_vm_ha_planned_target(
        local_config_file=config_path,
        target_role="passive",
        command="failover vm",
    )
    if preparation.outcome == "already-owner":
        print(
            json.dumps(
                {
                    "schema": "nebius-vpngw/vm-ha-planned-transfer-result-v1",
                    "outcome": "already-owner",
                    "target_role": "passive",
                    "request_submitted": False,
                },
                sort_keys=True,
            )
        )
        return
    records = _run_vm_ha_operator_command(
        local_config_file=config_path,
        agent_flag="--vm-ha-manual-failover",
        configured_role="passive",
    )
    if len(records) != 1:
        raise RuntimeError("manual VM-HA failover did not target exactly one configured passive")
    print(json.dumps(records[0], sort_keys=True))


_apply_help_command_order()


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
