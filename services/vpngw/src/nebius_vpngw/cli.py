import contextlib
import contextvars
import functools
import hashlib
import inspect
import io
import ipaddress
import json
import logging
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
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import paramiko  # type: ignore[import-untyped]
import typer
import yaml
from rich import print
from typer.core import TyperGroup

from . import __version__
from .agent.vm_ha.auto_healing import (
    AUTO_HEALING_CAPABILITY,
    AUTO_HEALING_REQUEST_SCHEMA,
    AUTO_HEALING_STATUS_SCHEMA,
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryReason,
    AutoHealingRecoveryRecord,
    StandbyAutoHealing,
    auto_healing_recovery_digest,
    encode_policy_request,
    policy_decision_digest,
)
from .agent.vm_ha.inhibition import (
    LIVE_PEER_REPLACEMENT_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
)
from .agent.vm_ha.progress import planned_request_fingerprint, validate_transfer_progress
from .agent.vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY
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
from .deploy.destroy import DestroyFailure, execute_destroy
from .deploy.route_manager import (
    NebiusSDKRouteBackend,
    RouteManagementError,
    RouteManager,
    VMHAStaticRouteConvergence,
)
from .deploy.ssh_client_auth import SSHClientAuth, resolve_ssh_client_auth
from .deploy.ssh_policy import (
    KNOWN_HOSTS_ENV,
    LegacyOrdinarySSHEnrollmentRequired,
    SSHTrustPolicy,
    VMHAReplacementSSHIdentityProblem,
    VMHAReplacementSSHIdentityUnavailable,
    VMHASSHIdentityRotationIntent,
    VMHASSHTrustScope,
    build_openssh_base_command,
    managed_ssh_trust_available,
    managed_ssh_trust_member,
    prepare_vm_ha_ssh_identity_rotation,
    publish_vm_ha_ssh_identity_rotation,
    publish_vm_ha_ssh_trust,
    require_vm_ha_ssh_policy,
    validate_vm_ha_ssh_identity_rotation,
)
from .deploy.ssh_push import (
    SSHPush,
    VMHAAgentArtifact,
    VMHAAgentArtifactError,
    VMHAAgentArtifactProblem,
    VMHAStandbyReplacementNotReady,
)
from .deploy.vm_ha_cloud import (
    AllocationOwner,
    AmbiguousHACloudError,
    InstanceCloudState,
    NebiusSDKCloudClient,
    RetryableHACloudError,
    VMHACloudAdapter,
    nebius_request_error_code_is,
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
    vm_ha_destroyed_retained_public_bindings,
    vm_ha_effective_resource_bindings,
    vm_ha_missing_standby_disk_name,
    vm_ha_missing_standby_disk_name_binding_key,
    vm_ha_missing_standby_owner_binding_key,
    vm_ha_missing_standby_owner_sequences,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_missing_standby_ssh_binding_key,
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_cycle_for_approval,
    vm_ha_passive_replacement_cycles,
    vm_ha_passive_replacement_effect,
    vm_ha_resource_binding_matches_observation,
)
from .deploy.vm_manager import PublicAllocationCandidate, VMManager
from .nebius_auth import error_chain_has_cli_authentication_failure
from .nebius_pagination import collect_nebius_pages, nebius_resource_id
from .vm_ha_command import (
    VMHACommandApproval,
    VMHACommandClassification,
    VMHACommandHealth,
    VMHACommandImpact,
    VMHACommandOutcome,
    VMHACommandResult,
    dedupe_reason_codes,
)
from .vm_ha_config_wizard import (
    is_vm_ha_conversion_candidate,
    resolve_vm_ha_conversion_source,
    run_vm_ha_conversion_wizard,
    validate_vm_ha_conversion_source,
)
from .vm_ha_credentials import (
    VMHACredentialIdentityError,
    VMHACredentialSet,
    credential_bindings_from_runtime,
    credential_resource_binding_subset,
)
from .vm_ha_managed_credentials import (
    VMHAManagedCredentialError,
    VMHAManagedCredentialPlan,
    ensure_managed_vm_ha_credentials,
    inspect_managed_vm_ha_credentials,
)

DEFAULT_CONFIG_FILENAME = "nebius-vpngw.config.yaml"
_NEBIUS_REGION_HELP = "Nebius region; precedence is --region, gateway_group.region, then region_id"
_VM_HA_AGENT_ARTIFACT_PREREQUISITES: t.Mapping[VMHAAgentArtifactProblem, tuple[str, str]] = (
    MappingProxyType(
        {
            VMHAAgentArtifactProblem.MISSING: (
                "agent-artifact-missing",
                "build the current project wheel or set VPNGW_AGENT_WHEEL to one "
                "current compatible wheel, then rerun vm-ha",
            ),
            VMHAAgentArtifactProblem.AMBIGUOUS: (
                "agent-artifact-selection-ambiguous",
                "set VPNGW_AGENT_WHEEL to exactly one current compatible wheel, then rerun vm-ha",
            ),
            VMHAAgentArtifactProblem.INCOMPATIBLE: (
                "agent-artifact-incompatible",
                "rebuild the agent wheel from the current source or set "
                "VPNGW_AGENT_WHEEL to one current compatible wheel, then rerun vm-ha",
            ),
            VMHAAgentArtifactProblem.CHANGED: (
                "agent-artifact-changed",
                "stabilize or rebuild the selected agent wheel, then rerun vm-ha to "
                "obtain a new exact plan",
            ),
        }
    )
)


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
        ("vm-ha",): (
            "nebius-vpngw vm-ha --local-config-file gateway.config.yaml",
            "nebius-vpngw vm-ha --local-config-file gateway.config.yaml --dry-run",
            "nebius-vpngw vm-ha --rotate-mtls --local-config-file gateway.config.yaml --dry-run",
            "nebius-vpngw vm-ha --rotate-mtls "
            "--local-config-file gateway.config.yaml --approve PLAN_DIGEST",
            "nebius-vpngw vm-ha --local-config-file gateway.config.yaml "
            "--standby-auto-healing disabled",
            "nebius-vpngw vm-ha --local-config-file gateway.config.yaml --region eu-north1",
            "nebius-vpngw vm-ha --local-config-file gateway.config.yaml --output-format json",
        ),
        ("prep-network",): (
            "nebius-vpngw prep-network --local-config-file nebius-vpngw.config.yaml",
        ),
        ("validate-config",): ("nebius-vpngw validate-config nebius-vpngw.config.yaml",),
        ("apply",): ("nebius-vpngw apply --local-config-file nebius-vpngw.config.yaml --dry-run",),
        ("status",): ("nebius-vpngw status --local-config-file nebius-vpngw.config.yaml",),
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
        "vm-ha": "all",
        "prep-network": "all",
        "validate-config": "all",
        "apply": "all",
        "status": "all",
        "vm-ha --rotate-mtls": "vm-ha",
        "add-routes-local": "route-policy",
        "list-routes-local": "all",
        "list-routes-remote": "all",
        "restart-tunnel": "ordinary",
        "create-from-peer-config": "all",
        "destroy": "all",
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


def _validate_vm_ha_peer_rotation_preparation(
    plan: ResolvedDeploymentPlan,
    *,
    local_config_was_explicit: bool,
    approval_flags_present: bool,
) -> None:
    """Admit only the explicit VM-HA peer-rotation checkpoint."""

    if not local_config_was_explicit:
        raise typer.BadParameter(
            "'--prepare-vm-ha-peer-rotation' requires an explicit --local-config-file."
        )
    if plan.vm_ha is None:
        raise typer.BadParameter(
            "'--prepare-vm-ha-peer-rotation' requires an explicit "
            "gateway_group.vm_ha configuration."
        )
    if approval_flags_present:
        raise typer.BadParameter(
            "'--prepare-vm-ha-peer-rotation' cannot be combined with VM-HA "
            "migration, recovery, or failed-passive replacement approval."
        )


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
        if command == "restart-tunnel":
            raise typer.BadParameter(
                "Tunnel restart is not supported for a VM-HA-enabled gateway. "
                "Tunnel recovery is controller-owned; use "
                "'nebius-vpngw status --local-config-file <file>' to inspect health "
                "and 'nebius-vpngw apply --local-config-file <file>' only for "
                "configuration convergence."
            )
        vm_command = {
            "failover tunnel": "failover vm",
            "failback tunnel": "failback vm",
        }.get(command)
        if vm_command is not None:
            action = command.split(" ", 1)[0]
            raise typer.BadParameter(
                f"Tunnel {action} is not supported for a VM-HA-enabled gateway. "
                f"Use 'nebius-vpngw {vm_command} --local-config-file <file>' "
                "only to transfer VM ownership; it does not select a tunnel."
            )
        alternative = {
            "restart-tunnel": "use 'nebius-vpngw apply' or the VM-HA controller workflow",
        }.get(command, "use the VM-HA-specific workflow")
        raise typer.BadParameter(f"'{command}' is not supported for explicit VM HA; {alternative}.")
    if applicability == "vm-ha" and not is_vm_ha:
        raise typer.BadParameter(
            f"'{command}' requires an explicit gateway_group.vm_ha configuration."
        )
    if applicability == "ordinary-bgp" and modes == {"static"}:
        action = command.split(" ", 1)[0]
        raise typer.BadParameter(
            f"Tunnel {action} is not supported for Static routing; it is available "
            "only for ordinary BGP configurations."
        )

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
    if modes not in ({"bgp"}, {"static"}):
        raise typer.BadParameter(
            "'add-routes-local' supports explicit VM HA only when routing is entirely "
            "static or entirely BGP; use 'nebius-vpngw apply' for mixed routing."
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
    state = _read_vm_ha_route_lifecycle_state(config_path, plan, project_id)
    return bool(
        state is not None
        and state.status is VMHALifecycleStatus.ACTIVE
        and state.transaction is not None
        and not state.transaction.pending_effect
    )


def _read_vm_ha_route_lifecycle_state(
    config_path: Path,
    plan: ResolvedDeploymentPlan,
    project_id: str | None,
) -> VMHALifecycleState | None:
    """Read the exact local lifecycle record for one route operation."""

    return VMHALifecycleStore(config_path).read(
        expected_project_id=project_id,
        expected_gateway_name=plan.gateway_group.name,
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
Use --local-config-file or -c to select a different config for operational commands.
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
    client_auth: SSHClientAuth | None = None,
    ssh_policy: SSHTrustPolicy | None = None,
    hostname: str | None = None,
) -> list[str]:
    _ensure_ssh_available()
    return build_openssh_base_command(
        key_path=key_path if client_auth is None else None,
        client_auth=client_auth,
        policy=ssh_policy,
        hostname=hostname,
    )


def _vm_spec_ssh_client_auth(vm_spec: t.Mapping[str, t.Any]) -> SSHClientAuth | None:
    """Resolve one VM spec's configured management identity without fallback."""

    public_key = str(vm_spec.get("ssh_public_key") or "").strip()
    if not public_key:
        return None
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    return resolve_ssh_client_auth(
        public_key,
        explicit_private_key=Path(str(raw_key)).expanduser() if raw_key else None,
    )


def _gateway_ssh_client_auth(
    local_cfg: t.Mapping[str, t.Any],
) -> SSHClientAuth | None:
    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
    return _vm_spec_ssh_client_auth(vm_spec)


class _VMHAStatusSSHUnavailable(RuntimeError):
    """Exact SSH trust is unavailable for one VM-HA status target."""


@dataclass(frozen=True)
class _StatusSSHContext:
    username: str
    key_path: Path | None
    client_auth: SSHClientAuth | None
    client_auth_required: bool
    policies: t.Mapping[str, SSHTrustPolicy | None]
    unavailable_members: frozenset[str]
    exact_trust_required: bool


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
        region_id=str(getattr(gateway_group, "region", "") or "").strip(),
        gateway_name=str(getattr(gateway_group, "name", "") or "").strip(),
        cluster_id=vm_ha_cluster,
    )


def _ordinary_ssh_trust_scope(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None = None,
) -> VMHASSHTrustScope:
    """Bind managed SSH trust to one exact ordinary gateway deployment."""

    if getattr(plan, "vm_ha", None) is not None:
        raise ValueError("Ordinary SSH trust cannot use a VM-HA plan")
    return _vm_ha_ssh_trust_scope(
        local_cfg,
        plan,
        project_id=project_id,
        cluster_id="ordinary-v1",
    )


def _gateway_ssh_trust_scope(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None = None,
) -> VMHASSHTrustScope:
    if getattr(plan, "vm_ha", None) is None:
        return _ordinary_ssh_trust_scope(local_cfg, plan, project_id=project_id)
    return _vm_ha_ssh_trust_scope(local_cfg, plan, project_id=project_id)


def _existing_gateway_ssh_policy(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    host_pairs: tuple[tuple[str, str], ...],
    *,
    project_id: str | None = None,
    additional_aliases: t.Mapping[str, t.Iterable[str]] | None = None,
) -> SSHTrustPolicy | None:
    """Resolve managed/explicit trust, preserving legacy ordinary system trust if absent."""

    scope = _gateway_ssh_trust_scope(local_cfg, plan, project_id=project_id)
    if getattr(plan, "vm_ha", None) is None:
        if any(not value for value in scope.values()):
            return None
        if KNOWN_HOSTS_ENV not in os.environ and not managed_ssh_trust_available(scope):
            return None
    options: dict[str, t.Any] = {}
    if additional_aliases is not None:
        options["additional_aliases"] = additional_aliases
    return require_vm_ha_ssh_policy(
        host_pairs,
        enrollment_hosts=set(),
        trust_scope=scope,
        **options,
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
    public_key = str(vm_spec.get("ssh_public_key") or "").strip()
    client_auth: SSHClientAuth | None = None
    if public_key:
        try:
            client_auth = resolve_ssh_client_auth(
                public_key,
                explicit_private_key=key_path,
            )
        except (OSError, RuntimeError, ValueError):
            client_auth = None
    policies: dict[str, SSHTrustPolicy | None] = {}
    unavailable: set[str] = set()
    scope = _gateway_ssh_trust_scope(local_cfg, plan, project_id=project_id)
    exact_trust_required = bool(plan.vm_ha is not None)
    if not exact_trust_required and all(scope.values()):
        exact_trust_required = bool(
            KNOWN_HOSTS_ENV in os.environ or managed_ssh_trust_available(scope)
        )
    resolved_targets: list[tuple[str, str]] = []
    additional_aliases: dict[str, tuple[str, ...]] = {}
    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue
        resolved_targets.append((inst_cfg.hostname, target))
        configured_address = str(getattr(inst_cfg, "external_ip", "") or "").strip()
        if configured_address and configured_address not in {inst_cfg.hostname, target}:
            additional_aliases[inst_cfg.hostname] = (configured_address,)

    if KNOWN_HOSTS_ENV not in os.environ and resolved_targets:
        # A managed receipt is authoritative for the complete deployment member
        # set. Resolve it once at that scope, then reuse the immutable policy for
        # each read-only member probe. Per-member resolution would reject a valid
        # multi-member receipt because its member set is intentionally complete.
        try:
            shared_policy = _existing_gateway_ssh_policy(
                local_cfg,
                plan,
                tuple(resolved_targets),
                project_id=project_id,
                additional_aliases=additional_aliases,
            )
        except (OSError, RuntimeError, ValueError):
            for hostname, _target in resolved_targets:
                policies[hostname] = None
                unavailable.add(hostname)
        else:
            for hostname, _target in resolved_targets:
                policies[hostname] = shared_policy
                if exact_trust_required and shared_policy is None:
                    unavailable.add(hostname)
    else:
        # An explicit operator file may intentionally contain only a subset.
        # Preserve per-member failure isolation for that temporary override.
        for hostname, target in resolved_targets:
            try:
                policy_options: dict[str, t.Any] = {}
                if hostname in additional_aliases:
                    policy_options["additional_aliases"] = {hostname: additional_aliases[hostname]}
                policies[hostname] = _existing_gateway_ssh_policy(
                    local_cfg,
                    plan,
                    ((hostname, target),),
                    project_id=project_id,
                    **policy_options,
                )
            except (OSError, RuntimeError, ValueError):
                policies[hostname] = None
                unavailable.add(hostname)
    return _StatusSSHContext(
        username=str(username),
        key_path=key_path,
        client_auth=client_auth,
        client_auth_required=bool(public_key),
        policies=MappingProxyType(policies),
        unavailable_members=frozenset(unavailable),
        exact_trust_required=exact_trust_required,
    )


def _build_route_ssh_policy(
    local_cfg: t.Mapping[str, t.Any],
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None = None,
) -> SSHTrustPolicy | None:
    """Freeze exact per-member SSH pins before a VM-HA route operation."""

    pin_targets: list[tuple[str, str]] = []
    for inst_cfg in plan.iter_instance_configs():
        target = str(inst_cfg.external_ip or "").strip()
        if not target:
            raise RouteManagementError(
                "VM-HA route operations require an external management IP for every member."
            )
        pin_targets.append((inst_cfg.hostname, target))
    try:
        return _existing_gateway_ssh_policy(
            local_cfg,
            plan,
            tuple(pin_targets),
            project_id=project_id,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise RouteManagementError(
            "Gateway route operations require exact pinned SSH trust for every managed member. "
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
    if context.exact_trust_required and policy is None:
        raise _VMHAStatusSSHUnavailable("exact SSH trust is unavailable for this gateway VM")
    if context.client_auth_required and context.client_auth is None:
        raise _VMHAStatusSSHUnavailable(
            "exact SSH client identity is unavailable for this gateway VM"
        )
    if context.client_auth is not None:
        return _build_ssh_base_cmd(
            None,
            client_auth=context.client_auth,
            ssh_policy=policy,
            hostname=hostname if policy is not None else None,
        ) + [f"{context.username}@{target}"]
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


def _format_configured_tunnel_role(role: str | None) -> str:
    role_value = _normalize_role_value(role or "-")
    if role_value == "active":
        return "[green]active[/green]"
    if role_value == "passive":
        return "[yellow]passive[/yellow]"
    if role_value == "disable":
        return "[red]disabled[/red]"
    return role_value


def _configured_and_runtime_tunnel_names(
    hostname: str,
    tunnel_role_map: dict[str, dict[str, str]],
    runtime_names: t.Iterable[str],
) -> list[str]:
    """Return configured tunnels first, followed by unexpected runtime tunnels."""

    ordered = list(tunnel_role_map.get(hostname, {}))
    configured = set(ordered)
    ordered.extend(name for name in runtime_names if name not in configured)
    return ordered


def _add_configured_tunnel_without_runtime_row(
    table: t.Any,
    hostname: str,
    tunnel_name: str,
    tunnel_role_map: dict[str, dict[str, str]],
    tunnel_peer_map: dict[str, dict[str, str]],
) -> None:
    """Render one configured tunnel that has no observed strongSwan SA."""

    table.add_row(
        tunnel_name,
        _format_configured_tunnel_role(tunnel_role_map.get(hostname, {}).get(tunnel_name)),
        hostname,
        "[yellow]NONE[/yellow]",
        "-",
        tunnel_peer_map.get(hostname, {}).get(tunnel_name, "-"),
        "-",
        "-",
    )


def _add_configured_no_active_tunnel_rows(
    table: t.Any,
    hostname: str,
    tunnel_role_map: dict[str, dict[str, str]],
    tunnel_peer_map: dict[str, dict[str, str]],
) -> None:
    """Render configured tunnel identity when strongSwan has no active SAs."""

    configured_roles = tunnel_role_map.get(hostname, {})
    if not configured_roles:
        table.add_row(
            "No configured tunnels",
            "-",
            hostname,
            "[yellow]NONE[/yellow]",
            "-",
            "-",
            "-",
            "-",
        )
        return

    for tunnel_name in configured_roles:
        _add_configured_tunnel_without_runtime_row(
            table,
            hostname,
            tunnel_name,
            tunnel_role_map,
            tunnel_peer_map,
        )


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
                if show_progress:
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
            if show_progress:
                print(f"[red]✗ Authentication error: {e}[/red]")
                print("[yellow]Please ensure you're logged in: nebius auth login[/yellow]")
            raise typer.Exit(code=1) from e
        else:
            if show_progress:
                print(f"[yellow]⚠️  Authentication error: {e}[/yellow]")
            return None


def _apply_operator_auth_token() -> str | None:
    """Return only an explicitly supplied operator token for ``apply``.

    Without an explicit token, ``VMManager`` uses the renewable Nebius CLI
    profile instead of exporting one process-wide static access token.
    """

    return os.environ.get("NEBIUS_IAM_TOKEN") or None


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
    *,
    credential_bindings: t.Mapping[str, str] | None = None,
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
    if credential_bindings is not None:
        bindings.update(credential_bindings)
    return bindings


def _vm_ha_observation_matches_bindings(
    observation: t.Mapping[str, object],
    expected: t.Mapping[str, str],
    *,
    credential_bindings: t.Mapping[str, str] | None = None,
) -> bool:
    current = _vm_ha_initial_resource_bindings(
        observation,
        credential_bindings=credential_bindings,
    )
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


def _vm_ha_approval_state_with_credentials(
    observation: t.Mapping[str, object],
    credentials: VMHACredentialSet,
) -> dict[str, object]:
    result = dict(observation)
    result["runtime_credentials"] = credentials.approval_records()
    return result


def _vm_ha_approval_state_with_managed_credential_plan(
    observation: t.Mapping[str, object],
    credential_plan: VMHAManagedCredentialPlan,
) -> dict[str, object]:
    """Bind approval to secret-free managed credential reuse or creation intent."""

    result = dict(observation)
    result["managed_runtime_credentials"] = credential_plan.approval_record()
    return result


def _validate_vm_ha_lifecycle_credential_transition(
    lifecycle_state: VMHALifecycleState | None,
    credentials: VMHACredentialSet,
) -> None:
    """Reject missing, partial, rotated, or otherwise rebound VM-HA identity."""

    if lifecycle_state is None or lifecycle_state.transaction is None:
        return
    status = lifecycle_state.status
    if status not in {
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
        VMHALifecycleStatus.ACTIVE,
    }:
        return
    fresh = credentials.resource_bindings()
    persisted = credential_resource_binding_subset(
        dict(lifecycle_state.transaction.resource_bindings)
    )
    if set(persisted) != set(fresh):
        raise ValueError("VM-HA lifecycle runtime credential binding is incomplete")
    if persisted != fresh:
        raise ValueError("VM-HA lifecycle runtime credential identity changed")


def _vm_ha_failed_passive_bootstrap_effect(
    passive_instance_name: str,
    replacement_cycle: int,
) -> str:
    """Return the durable proof marker required before replacing one passive."""

    return f"verify-{replacement_cycle}-bootstrap-timeout-{passive_instance_name}"


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
    failure_effect = _vm_ha_failed_passive_bootstrap_effect(
        passive_name,
        replacement_cycle,
    )
    if failure_effect not in transaction.completed_effects:
        raise ValueError("VM-HA passive replacement has no durable bootstrap-timeout evidence")
    effective_bindings = vm_ha_effective_resource_bindings(bindings)
    compute_id = effective_bindings.get(f"compute:{passive_name}")
    disk_id = effective_bindings.get(f"disk:{passive_name}")
    if not compute_id or not disk_id:
        raise ValueError("VM-HA passive replacement lacks exact transaction-created identities")
    actions: tuple[str, ...] = (
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


@dataclass(frozen=True)
class _VMHAMissingStandbyReplacementPlan:
    target_instance_name: str
    owner_instance_name: str
    approval_digest: str
    operation_id: str
    replacement_cycle: int
    replacement_disk_name: str
    retired_compute_id: str
    retired_disk_id: str
    primary_allocation_id: str
    public_allocation_id: str
    ssh_identity_rotation: VMHASSHIdentityRotationIntent | None = None
    authorization_persisted: bool = False


def _validate_vm_ha_missing_standby_replacement_observation(
    *,
    lifecycle_state: VMHALifecycleState,
    replacement: _VMHAMissingStandbyReplacementPlan,
    observation: t.Mapping[str, object],
) -> None:
    """Reprove the frozen owner and unrelated cloud state on every resume."""

    transaction = lifecycle_state.transaction
    raw_members = observation.get("members")
    shared = observation.get("shared_allocation")
    if (
        transaction is None
        or not isinstance(raw_members, list)
        or not isinstance(shared, t.Mapping)
    ):
        raise ValueError("VM-HA persisted missing standby observation is incomplete")
    observed = {
        str(item.get("instance_name")): item
        for item in raw_members
        if isinstance(item, t.Mapping) and isinstance(item.get("instance_name"), str)
    }
    if set(observed) != {member.instance_name for member in lifecycle_state.members}:
        raise ValueError("VM-HA persisted missing standby member evidence is incomplete")
    owner = next(
        (
            member
            for member in lifecycle_state.members
            if member.instance_name == replacement.owner_instance_name
        ),
        None,
    )
    owner_current = observed.get(replacement.owner_instance_name)
    shared_owner = shared.get("owner")
    if (
        owner is None
        or not isinstance(owner_current, t.Mapping)
        or owner_current.get("present") is not True
        or owner_current.get("state") != "running"
        or owner_current.get("compute_id") != owner.compute_id
        or owner_current.get("network_interface_name") != owner.network_interface_name
        or not isinstance(shared_owner, t.Mapping)
        or shared_owner.get("compute_id") != owner.compute_id
        or shared_owner.get("network_interface_name") != owner.network_interface_name
    ):
        raise ValueError("VM-HA persisted missing standby serving owner changed")

    bindings = dict(transaction.resource_bindings)
    effective = vm_ha_effective_resource_bindings(bindings)
    current = _vm_ha_initial_resource_bindings(observation)
    expected = dict(effective)
    replacement_compute_id = expected.pop(f"compute:{replacement.target_instance_name}", None)
    replacement_disk_id = expected.pop(f"disk:{replacement.target_instance_name}", None)
    expected["shared-allocation-owner-compute"] = owner.compute_id
    expected["shared-allocation-owner-nic"] = owner.network_interface_name
    if not all(
        vm_ha_resource_binding_matches_observation(
            key,
            value,
            observed=current,
            expected=expected,
        )
        for key, value in expected.items()
        if not key.startswith("credential-")
    ):
        raise ValueError("VM-HA persisted missing standby cloud authority drifted")

    target_current = observed[replacement.target_instance_name]
    accepted_compute = bool(
        transaction.pending_effect
        and transaction.accepted_cloud_operation_effect == transaction.pending_effect
        and transaction.pending_effect.endswith("-create-compute")
        and transaction.accepted_cloud_operation_id
    )
    if replacement_compute_id:
        if (
            target_current.get("present") is not True
            or target_current.get("compute_id") != replacement_compute_id
            or target_current.get("boot_disk_id") != replacement_disk_id
        ):
            raise ValueError("VM-HA persisted replacement Compute identity drifted")
    elif target_current != {
        "instance_name": replacement.target_instance_name,
        "present": False,
    } and not (accepted_compute and target_current.get("present") is True):
        raise ValueError("VM-HA persisted replacement target presence changed")


def _vm_ha_missing_standby_replacement_plan(
    plan: ResolvedDeploymentPlan,
    lifecycle_state: VMHALifecycleState,
    observation: t.Mapping[str, object],
    *,
    ssh_identity_rotation: VMHASSHIdentityRotationIntent | None = None,
) -> _VMHAMissingStandbyReplacementPlan:
    """Plan or recover one creation-only replacement for the current non-owner."""

    transaction = lifecycle_state.transaction
    if plan.vm_ha is None or lifecycle_state.record_version != 4 or transaction is None:
        raise ValueError("VM-HA missing standby replacement has no v4 lifecycle authority")
    bindings = dict(transaction.resource_bindings)
    if lifecycle_state.status in {
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
    } and any(key.startswith("standby-replacement-") for key in bindings):
        candidates: list[tuple[VMHALifecycleMember, int, str]] = []
        for member in lifecycle_state.members:
            for cycle in vm_ha_passive_replacement_cycles(bindings, member.instance_name):
                approval = bindings.get(
                    vm_ha_passive_replacement_binding_key("approval", member.instance_name, cycle)
                )
                disk_name = bindings.get(
                    vm_ha_missing_standby_disk_name_binding_key(member.instance_name, cycle)
                )
                if approval == transaction.approval_digest and disk_name:
                    candidates.append((member, cycle, disk_name))
        if len(candidates) != 1:
            raise ValueError("VM-HA persisted missing standby replacement is not exact")
        target, replacement_cycle, replacement_disk_name = candidates[0]
        owner_sequences = vm_ha_missing_standby_owner_sequences(bindings)
        owner_sequence = owner_sequences[-1] if owner_sequences else None
        owner = next(
            (
                member
                for member in lifecycle_state.members
                if owner_sequence is not None
                and member.instance_name
                == bindings.get(vm_ha_missing_standby_owner_binding_key("instance", owner_sequence))
                and member.compute_id
                == bindings.get(vm_ha_missing_standby_owner_binding_key("compute", owner_sequence))
                and member.network_interface_name
                == bindings.get(vm_ha_missing_standby_owner_binding_key("nic", owner_sequence))
            ),
            None,
        )
        retired_compute_id = bindings.get(
            vm_ha_passive_replacement_binding_key(
                "retired-compute", target.instance_name, replacement_cycle
            )
        )
        retired_disk_id = bindings.get(
            vm_ha_passive_replacement_binding_key(
                "retired-disk", target.instance_name, replacement_cycle
            )
        )
        effective = vm_ha_effective_resource_bindings(bindings)
        primary_id = effective.get(f"primary-allocation:{target.instance_name}:eth0")
        public_id = effective.get(f"public-allocation:{target.instance_name}:eth0")
        if not owner or not all((retired_compute_id, retired_disk_id, primary_id, public_id)):
            raise ValueError("VM-HA persisted missing standby replacement is incomplete")
        old_fingerprint = bindings.get(
            vm_ha_missing_standby_ssh_binding_key(
                "old-fingerprint",
                target.instance_name,
                replacement_cycle,
            )
        )
        persisted_rotation: VMHASSHIdentityRotationIntent | None = None
        if old_fingerprint is not None:
            rotation_values = {
                kind: bindings.get(
                    vm_ha_missing_standby_ssh_binding_key(
                        kind,
                        target.instance_name,
                        replacement_cycle,
                    )
                )
                for kind in (
                    "old-fingerprint",
                    "trust-scope",
                    "predecessor-receipt",
                    "predecessor-projection",
                    "storage-owner",
                )
            }
            if any(value is None for value in rotation_values.values()):
                raise ValueError("VM-HA persisted missing standby SSH rotation is incomplete")
            persisted_rotation = VMHASSHIdentityRotationIntent(
                hostname=target.instance_name,
                trust_scope_sha256=t.cast(str, rotation_values["trust-scope"]),
                old_fingerprint=t.cast(str, rotation_values["old-fingerprint"]),
                predecessor_receipt_sha256=(
                    None
                    if rotation_values["predecessor-receipt"] == "absent"
                    else t.cast(str, rotation_values["predecessor-receipt"])
                ),
                predecessor_projection_sha256=(
                    None
                    if rotation_values["predecessor-projection"] == "absent"
                    else t.cast(str, rotation_values["predecessor-projection"])
                ),
                storage_owner=t.cast(str, rotation_values["storage-owner"]),
            )
        replacement = _VMHAMissingStandbyReplacementPlan(
            target_instance_name=target.instance_name,
            owner_instance_name=owner.instance_name,
            approval_digest=transaction.approval_digest,
            operation_id=transaction.operation_id,
            replacement_cycle=replacement_cycle,
            replacement_disk_name=replacement_disk_name,
            retired_compute_id=t.cast(str, retired_compute_id),
            retired_disk_id=t.cast(str, retired_disk_id),
            primary_allocation_id=t.cast(str, primary_id),
            public_allocation_id=t.cast(str, public_id),
            ssh_identity_rotation=persisted_rotation,
            authorization_persisted=True,
        )
        _validate_vm_ha_missing_standby_replacement_observation(
            lifecycle_state=lifecycle_state,
            replacement=replacement,
            observation=observation,
        )
        return replacement

    if (
        lifecycle_state.status is not VMHALifecycleStatus.ACTIVE
        or transaction.pending_effect is not None
        or transaction.accepted_cloud_operation_id is not None
    ):
        raise ValueError("VM-HA missing standby replacement requires quiescent ACTIVE state")
    raw_members = observation.get("members")
    shared = observation.get("shared_allocation")
    if not isinstance(raw_members, list) or not isinstance(shared, t.Mapping):
        raise ValueError("VM-HA missing standby replacement observation is incomplete")
    observed = {
        str(item.get("instance_name")): item
        for item in raw_members
        if isinstance(item, t.Mapping) and isinstance(item.get("instance_name"), str)
    }
    members = {member.instance_name: member for member in lifecycle_state.members}
    owner_observation = shared.get("owner")
    if set(observed) != set(members) or not isinstance(owner_observation, t.Mapping):
        raise ValueError("VM-HA missing standby replacement identity is incomplete")
    owners = [
        member
        for member in lifecycle_state.members
        if member.compute_id == owner_observation.get("compute_id")
        and member.network_interface_name == owner_observation.get("network_interface_name")
    ]
    if len(owners) != 1:
        raise ValueError("VM-HA missing standby replacement owner is not exact")
    owner = owners[0]
    target = next(member for member in lifecycle_state.members if member is not owner)
    if observed.get(target.instance_name) != {
        "instance_name": target.instance_name,
        "present": False,
    }:
        raise ValueError("VM-HA non-owner Compute is not authoritatively absent")
    owner_current = observed.get(owner.instance_name)
    if not isinstance(owner_current, t.Mapping) or (
        owner_current.get("present") is not True
        or owner_current.get("state") != "running"
        or owner_current.get("compute_id") != owner.compute_id
        or owner_current.get("network_interface_name") != owner.network_interface_name
    ):
        raise ValueError("VM-HA serving owner is not stable")
    effective = vm_ha_effective_resource_bindings(bindings)
    expected = dict(effective)
    retired_compute_id = expected.pop(f"compute:{target.instance_name}", None)
    retired_disk_id = expected.pop(f"disk:{target.instance_name}", None)
    expected["shared-allocation-owner-compute"] = owner.compute_id
    expected["shared-allocation-owner-nic"] = owner.network_interface_name
    current = _vm_ha_initial_resource_bindings(observation)
    if not all(
        vm_ha_resource_binding_matches_observation(
            key,
            value,
            observed=current,
            expected=expected,
        )
        for key, value in expected.items()
        if not key.startswith("credential-")
    ):
        raise ValueError("VM-HA non-owner absence is accompanied by identity drift")
    primary_id = effective.get(f"primary-allocation:{target.instance_name}:eth0")
    public_id = effective.get(f"public-allocation:{target.instance_name}:eth0")
    if not all((retired_compute_id, retired_disk_id, primary_id, public_id)):
        raise ValueError("VM-HA missing standby retained identities are incomplete")
    prior_cycles = vm_ha_passive_replacement_cycles(bindings, target.instance_name)
    replacement_cycle = 1 if not prior_cycles else prior_cycles[-1] + 1
    replacement_disk_name = vm_ha_missing_standby_disk_name(
        gateway_name=lifecycle_state.gateway_name,
        instance_name=target.instance_name,
        predecessor_sha256=lifecycle_state.record_sha256,
        cycle=replacement_cycle,
    )
    actions: tuple[str, ...] = (
        f"replacement-cycle:{replacement_cycle}",
        f"create-compute:{target.instance_name}",
        f"create-fresh-boot-disk:{replacement_disk_name}",
        "leave-all-existing-disks-untouched",
        f"retain-serving-owner:{owner.instance_name}:{owner.compute_id}",
        f"retain-shared-allocation:{lifecycle_state.allocation_id}",
        f"retain-primary-allocation:{primary_id}",
        f"retain-public-allocation:{public_id}",
        "retain-routes-forwarding-roles-and-node-ids",
    )
    if ssh_identity_rotation is not None:
        if ssh_identity_rotation.hostname != target.instance_name:
            raise ValueError("VM-HA replacement SSH rotation target changed")
        actions = (*actions, "generate-and-rotate-missing-non-owner-ssh-identity")
    approval_digest = _canonical_digest(
        {
            "actions": actions,
            "current_observation": dict(observation),
            "domain": "nebius-vpngw/active-missing-standby-replacement-v1",
            "lifecycle_record_sha256": lifecycle_state.record_sha256,
            "retired_compute_id": retired_compute_id,
            "retired_disk_id": retired_disk_id,
            "ssh_identity_rotation": (
                None if ssh_identity_rotation is None else ssh_identity_rotation.approval_state()
            ),
            "target_instance_name": target.instance_name,
        }
    )
    operation_id = _canonical_digest(
        {
            "approval_digest": approval_digest,
            "domain": "nebius-vpngw/missing-standby-replacement-operation-v1",
            "predecessor_sha256": lifecycle_state.record_sha256,
        }
    )
    desired_digest = _canonical_digest(_vm_ha_desired_approval_state(plan))
    lifecycle_state.start_missing_standby_replacement(
        lifecycle_state,
        target_instance_name=target.instance_name,
        replacement_cycle=replacement_cycle,
        replacement_disk_name=replacement_disk_name,
        operation_id=operation_id,
        approval_digest=approval_digest,
        desired_state_digest=desired_digest,
        current_state_digest=_canonical_digest(observation),
        current_observation=observation,
        ssh_identity_rotation=(
            None if ssh_identity_rotation is None else ssh_identity_rotation.approval_state()
        ),
    )
    return _VMHAMissingStandbyReplacementPlan(
        target_instance_name=target.instance_name,
        owner_instance_name=owner.instance_name,
        approval_digest=approval_digest,
        operation_id=operation_id,
        replacement_cycle=replacement_cycle,
        replacement_disk_name=replacement_disk_name,
        retired_compute_id=t.cast(str, retired_compute_id),
        retired_disk_id=t.cast(str, retired_disk_id),
        primary_allocation_id=t.cast(str, primary_id),
        public_allocation_id=t.cast(str, public_id),
        ssh_identity_rotation=ssh_identity_rotation,
    )


def _create_missing_vm_ha_standby_under_owner_inhibition(
    *,
    plan: ResolvedDeploymentPlan,
    planned_instances: t.Iterable[t.Any],
    existing_members: t.Mapping[str, str],
    local_config: dict[str, t.Any],
    apply_report: "_VMHAApplyPlanReport | None",
    lifecycle_journal: VMHALifecycleJournal,
    vm_manager: t.Any,
    ssh: t.Any,
    replacement: _VMHAMissingStandbyReplacementPlan,
) -> tuple[dict[str, t.Any] | None, t.Any]:
    """Reprove, inhibit the owner, and create only the missing non-owner."""

    transaction = lifecycle_journal.state.transaction
    if transaction is None or plan.vm_ha is None:
        raise RuntimeError("VM-HA missing standby replacement lost its transaction")
    owner_config = next(
        instance
        for instance in planned_instances
        if instance.hostname == replacement.owner_instance_name
    )
    owner_target = existing_members.get(owner_config.hostname)
    if not owner_target:
        raise RuntimeError("VM-HA missing standby replacement owner address is unavailable")
    owner_generation = owner_config.vm_ha_generation
    if owner_config.vm_ha_node is None or owner_generation is None:
        raise RuntimeError("VM-HA missing standby replacement owner manifest is incomplete")
    owner_node_id = owner_config.vm_ha_node.node_id
    inhibition_effect = f"install-standby-replacement-inhibition-{owner_node_id}"
    release_effect = f"release-standby-replacement-inhibition-{owner_node_id}"
    already_inhibited = inhibition_effect in transaction.completed_effects
    inhibition_released = bool(
        release_effect in transaction.completed_effects
        or transaction.pending_effect == release_effect
    )
    prepare_owner_effect = f"prepare-live-peer-replacement-owner-v5-{owner_node_id}"

    def revalidate_replacement_authority() -> None:
        fresh_observation = vm_manager.observe_vm_ha_migration_state(
            plan.gateway_group,
            plan.gateway.get("local_prefixes"),
        )
        fresh_replacement = _vm_ha_missing_standby_replacement_plan(
            plan,
            lifecycle_journal.state,
            fresh_observation,
        )
        if fresh_replacement != replacement:
            raise RuntimeError(
                "VM-HA missing standby replacement authority changed before inhibition"
            )
        vm_manager.validate_missing_vm_ha_standby_replacement(
            plan.gateway_group,
            plan.gateway.get("local_prefixes"),
            target_instance_name=fresh_replacement.target_instance_name,
            retired_compute_id=fresh_replacement.retired_compute_id,
            replacement_disk_name=fresh_replacement.replacement_disk_name,
            primary_allocation_id=fresh_replacement.primary_allocation_id,
            public_allocation_id=fresh_replacement.public_allocation_id,
        )

    inhibition: dict[str, t.Any] | None = None
    if not already_inhibited and not inhibition_released:
        if apply_report is None or apply_report.artifact is None:
            raise RuntimeError("VM-HA standby replacement has no approved agent artifact")
        revalidate_replacement_authority()
        if apply_report.owner_refresh_required:
            lifecycle_journal.rewind_standby_replacement_inhibition_for_owner_refresh(
                owner_refresh_effect=prepare_owner_effect,
                inhibition_effect=inhibition_effect,
            )
            transaction = lifecycle_journal.state.transaction
            assert transaction is not None
            if prepare_owner_effect not in transaction.completed_effects:
                lifecycle_journal.begin(prepare_owner_effect)
                ssh.ensure_vm_ha_agent_package(
                    owner_target,
                    owner_config,
                    local_config,
                    artifact=apply_report.artifact,
                )
                ssh.refresh_vm_ha_control_services(
                    owner_target,
                    owner_config,
                    local_config,
                )
                lifecycle_journal.complete(prepare_owner_effect)
            revalidate_replacement_authority()
        lifecycle_journal.begin(inhibition_effect)
        inhibition = ssh.inhibit_vm_ha_standby_replacement(
            owner_target,
            owner_config.hostname,
            local_config,
            node_id=owner_node_id,
            operation_id=transaction.operation_id,
        )
    elif not inhibition_released:
        inhibition = {
            "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
            "cluster_id": plan.vm_ha.cluster_id,
            "node_id": owner_node_id,
            "generation_id": owner_generation.generation_id,
            "operation_id": transaction.operation_id,
        }
    if not inhibition_released:
        assert inhibition is not None
        try:
            ssh.verify_vm_ha_standby_replacement_quiescent(
                owner_target,
                owner_config.hostname,
                local_config,
                inhibition=inhibition,
            )
        except VMHAStandbyReplacementNotReady:
            raise _VMHAApplyConvergenceFailed(
                "serving owner did not acknowledge standby replacement inhibition",
                reason="standby-replacement-inhibition-not-ready",
                next_action=(
                    "rerun vm-ha to resume the exact inhibition checkpoint; if it "
                    "times out again, inspect the serving owner's VM-HA controller journal"
                ),
            ) from None
    if not already_inhibited and not inhibition_released:
        lifecycle_journal.complete(inhibition_effect)
    replace_standby = getattr(vm_manager, "replace_missing_vm_ha_standby", None)
    if not callable(replace_standby):
        raise RuntimeError("VM-HA manager has no missing-standby replacement interface")
    provisioning = replace_standby(
        plan.gateway_group,
        plan.gateway.get("local_prefixes"),
        approval_digest=replacement.approval_digest,
    )
    if getattr(provisioning, "vm_ha_runtime_binding", None) is None:
        raise RuntimeError("VM-HA standby replacement returned no runtime binding")
    return inhibition, provisioning


def _release_missing_vm_ha_standby_inhibition(
    *,
    lifecycle_journal: VMHALifecycleJournal,
    ssh: t.Any,
    owner_target: str,
    owner_config: t.Any,
    local_config: dict[str, t.Any],
    inhibition: t.Mapping[str, t.Any],
    effect: str,
) -> None:
    """Resume an exact receipt-backed owner-inhibition release idempotently."""

    transaction = lifecycle_journal.state.transaction
    if transaction is None:
        raise RuntimeError("VM-HA standby replacement release lost its transaction")
    if effect in transaction.completed_effects:
        return
    if transaction.pending_effect != effect:
        lifecycle_journal.begin(effect)
    ssh.release_vm_ha_standby_replacement_inhibition(
        owner_target,
        owner_config.hostname,
        local_config,
        inhibition=inhibition,
    )
    lifecycle_journal.complete(effect)


def _commit_missing_vm_ha_standby_replacement_active(
    lifecycle_journal: VMHALifecycleJournal,
) -> None:
    """Commit ACTIVE only after release and terminal owner/passive verification."""

    active_successor = lifecycle_journal.state.with_status(
        VMHALifecycleStatus.ACTIVE,
        checkpoint="missing-standby-replacement-complete",
    )
    lifecycle_journal.transition(active_successor)


def _vm_ha_activation_recovery_approval_state(
    plan: ResolvedDeploymentPlan,
    lifecycle_state: VMHALifecycleState,
    observation: t.Mapping[str, object],
    *,
    credential_bindings: t.Mapping[str, str] | None = None,
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
    new_bindings = _vm_ha_initial_resource_bindings(
        observation,
        credential_bindings=credential_bindings,
    )
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
                "credential-service-account:",
                "credential-authorized-key:",
                "credential-sha256:",
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


def _vm_ha_ordinary_migration_ssh_hosts(
    plan: ResolvedDeploymentPlan,
    lifecycle_state: VMHALifecycleState | None,
    migration_active_name: str | None,
) -> set[str]:
    """Recover the one retained ordinary SSH provenance from live or durable intent."""

    if plan.vm_ha is None:
        return set()
    if migration_active_name is None and (
        lifecycle_state is None
        or lifecycle_state.transaction is None
        or lifecycle_state.transaction.approval_kind != "migration"
    ):
        return set()
    active_names = {
        f"{plan.gateway_group.name}-{member.instance_index}"
        for member in plan.vm_ha.members
        if member.role.value == "active"
    }
    if len(active_names) != 1:
        raise ValueError("VM-HA plan has no unique configured active member")
    active_name = next(iter(active_names))
    if migration_active_name is not None:
        if migration_active_name != active_name:
            raise ValueError("VM-HA ordinary migration active identity changed")
        return {active_name}
    assert lifecycle_state is not None and lifecycle_state.transaction is not None
    transaction = lifecycle_state.transaction
    members = {member.instance_name: member for member in lifecycle_state.members}
    active = members.get(active_name)
    bindings = dict(transaction.resource_bindings)
    created_members = {
        effect[len("provision-") : -len("-compute")]
        for effect in transaction.completed_effects
        if effect.startswith("provision-") and effect.endswith("-compute")
    }
    passive_names = {
        f"{plan.gateway_group.name}-{member.instance_index}"
        for member in plan.vm_ha.members
        if member.role.value == "passive"
    }
    if passive_names and all(
        bindings.get(f"compute:{name}") and name not in created_members for name in passive_names
    ):
        return set()
    if (
        active is None
        or not active.compute_id
        or bindings.get(f"compute:{active_name}") != active.compute_id
        or active_name in created_members
    ):
        return set()
    return {active_name}


def _vm_ha_ordinary_migration_ssh_import_hosts(
    lifecycle_state: VMHALifecycleState | None,
    migration_active_name: str | None,
    migration_hosts: t.Iterable[str],
) -> set[str]:
    """Limit predecessor receipt reads to the unfinished migration transaction."""

    hosts = set(migration_hosts)
    if migration_active_name is not None:
        return hosts
    if lifecycle_state is not None and lifecycle_state.status in {
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
    }:
        return hosts
    return set()


def _refresh_vm_ha_ssh_policy_after_compute(
    *,
    plan: ResolvedDeploymentPlan,
    vm_manager: VMManager,
    vm_ips: t.Mapping[str, str],
    trust_scope: VMHASSHTrustScope,
    management_key_path: Path | None,
    management_public_key: str | None,
    ordinary_migration_hosts: t.Iterable[str],
    lifecycle_snapshot_loader: t.Callable[[], t.Any] | None = None,
) -> SSHTrustPolicy:
    """Rebind strict SSH evidence after an authorized VM-HA Compute transition."""

    if plan.vm_ha is None:
        raise ValueError("VM-HA SSH evidence refresh requires an explicit VM-HA plan")
    planned = {instance.hostname: instance for instance in plan.iter_instance_configs()}
    discovered = vm_manager.discover_vm_ha_members(plan.gateway_group)
    if set(discovered) != set(planned) or set(vm_ips) != set(planned):
        raise RuntimeError("VM-HA member set changed during post-provision SSH verification")
    for hostname, address in discovered.items():
        if str(vm_ips[hostname]).strip() != address:
            raise RuntimeError(
                f"VM-HA member {hostname} address changed during post-provision SSH verification"
            )

    retained = set(discovered)
    migration_hosts = set(ordinary_migration_hosts)
    lifecycle_options: dict[str, t.Any] = {}
    if lifecycle_snapshot_loader is not None:
        lifecycle_options["lifecycle_snapshot_loader"] = lifecycle_snapshot_loader
    bindings = vm_manager.vm_ha_ssh_trust_bindings(
        plan.gateway_group,
        retained_hosts=retained,
        ordinary_migration_hosts=migration_hosts,
        **lifecycle_options,
    )
    aliases: dict[str, tuple[str, ...]] = {}
    targets: list[tuple[str, str]] = []
    for hostname, instance in planned.items():
        target = discovered[hostname]
        configured = str(instance.external_ip or "").strip()
        targets.append((hostname, target))
        aliases[hostname] = tuple(
            alias for alias in (configured,) if alias and alias not in {hostname, target}
        )

    policy = require_vm_ha_ssh_policy(
        tuple(targets),
        enrollment_hosts=(),
        management_key_path=management_key_path,
        management_public_key=management_public_key,
        require_management_key=True,
        trust_scope=trust_scope,
        allow_managed_repair=False,
        persist_default_host_keys=False,
        additional_aliases=aliases,
        retained_hosts=retained,
        allow_default_known_hosts_import=False,
        default_known_hosts_bindings=bindings,
        default_known_hosts_import_hosts=(),
    )
    vm_manager.set_ssh_policy(policy)
    return policy


def _vm_ha_apply_operation_id(runtime_binding: t.Any) -> str:
    """Derive one replay-stable operation identity from authoritative runtime IDs."""

    payload = {
        "allocation_id": runtime_binding.shared_allocation_id,
        "bgp_policy_digest": runtime_binding.bgp_policy_digest,
        "cluster_id": runtime_binding.cluster_id,
        "configuration_digest": runtime_binding.configuration_digest,
        "generation_id": runtime_binding.generation_id,
        "nebius_authorized_key_id": runtime_binding.nebius_authorized_key_id,
        "nebius_project_id": runtime_binding.nebius_project_id,
        "nebius_service_account_id": runtime_binding.nebius_service_account_id,
        "nodes": [
            {
                "compute_id": node.compute_id,
                "nebius_credentials_path": node.nebius_credentials_path,
                "nebius_credentials_sha256": node.nebius_credentials_sha256,
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
        exact[node_id]
        and status.get("state") == "healthy"
        and status.get("operation_id") is None
        and status.get("operation_kind") is None
        and status.get("inhibited") is False
        and status.get("inhibition_operation_id") is None
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

    if any(
        status.get("operation_kind") == "rotation"
        or status.get("inhibited") is True
        or status.get("inhibition_operation_id") is not None
        for status in statuses.values()
    ):
        raise RuntimeError("managed mTLS apply is inhibited by a rotation transaction")

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
    expected_service_account_id: str | None = None,
) -> str | None:
    """Create/select the requested SA at the flow's explicitly chosen boundary."""

    print(f"[bold]Ensuring Service Account '{sa_name}' and obtaining token...[/bold]")
    token: str | None
    try:
        if vm_ha_enabled:
            from .vpngw_sa import (
                VM_HA_ROLE_ALLOWLIST,
                ensure_vm_ha_service_account_identity_and_token,
            )

            identity = ensure_vm_ha_service_account_identity_and_token(
                sa_name=sa_name,
                tenant_id=tenant_id,
                project_id=project_id,
                region_id=region_id,
                verified_role_ids=tuple(sorted(VM_HA_ROLE_ALLOWLIST)),
            )
            if (
                expected_service_account_id is not None
                and identity.service_account_id != expected_service_account_id
            ):
                raise RuntimeError(
                    "requested Service Account does not match the authenticated VM-HA runtime identity"
                )
            token = identity.token
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
    prior_bindings = dict(previous.transaction.resource_bindings)
    owner_updates = (
        {}
        if vm_ha_missing_standby_owner_sequences(prior_bindings)
        else {
            "shared-allocation-owner-compute": owner_member.compute_id,
            "shared-allocation-owner-nic": owner_member.network_interface_name,
        }
    )
    transaction = previous.transaction.advance(
        predecessor_sha256=previous.record_sha256,
        checkpoint="authoritative-binding-complete",
        pending_effect=None,
        resource_updates={
            "route-runtime-id": runtime_binding.route_runtime_id,
            "shared-allocation-id": runtime_binding.shared_allocation_id,
            **owner_updates,
            **credential_bindings_from_runtime(runtime_binding),
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


class _VMHARemoteAgentUnavailable(RuntimeError):
    """A read-only remote agent command did not return a usable response."""


class _VMHAActivationSafelyBlocked(RuntimeError):
    """Activation failed, but both exact apply locks were independently restored."""


class _VMHAActivationUnsafe(RuntimeError):
    """Activation recovery could not establish an exact safe terminal state."""


class _VMHAActivationFailed(RuntimeError):
    """A required activation effect failed before verified completion."""


class _VMHAApplyConvergenceFailed(RuntimeError):
    """Project an apply-owned activation exit into the VM-HA facade."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "apply-convergence-interrupted",
        next_action: str = (
            "rerun vm-ha to inspect durable checkpoints and resume idempotently; "
            "if the same checkpoint fails again, inspect VM-HA service journals"
        ),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.next_action = next_action


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
    if (
        runtime_binding is not None
        and getattr(runtime_binding, "nebius_service_account_id", None) is not None
    ):
        runtime_identity = payload.get("runtime_identity")
        if not (
            isinstance(runtime_identity, dict)
            and set(runtime_identity) == {"state", "reason"}
            and isinstance(runtime_identity.get("state"), str)
            and isinstance(runtime_identity.get("reason"), str)
        ):
            raise _VMHAAgentStatusStale(
                "VM-HA runtime credential identity proof is not yet available"
            )
        if runtime_identity["state"] == "blocked":
            raise _VMHAAgentStatusPermanent("VM-HA runtime credential identity is blocked")
        if runtime_identity["state"] != "verified":
            raise _VMHAAgentStatusStale(
                "VM-HA runtime credential identity has not reached the expected generation"
            )
    if runtime_binding is not None:
        controller_capabilities = payload.get("controller_capabilities")
        if not (
            isinstance(controller_capabilities, list)
            and all(isinstance(item, str) for item in controller_capabilities)
            and STANDBY_RESTORATION_CAPABILITY in controller_capabilities
        ):
            raise _VMHAAgentStatusStale(
                "VM-HA installed runtime lacks the standby restoration capability"
            )
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
    "blocked": frozenset({"disable-active"}),
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
        *(reason.value for reason in AutoHealingRecoveryReason),
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
        "automatic-retry-exhausted",
        "compute-start-failed",
        "compute-start-permanent-failure",
        "compute-start-retry-scheduled",
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
        "mtls-rotation-active",
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
        "runtime-identity-blocked",
        "shared-allocation-identity-missing",
        "standby-ready-evidence-invalid",
        "standby-readiness-timeout",
        "standby-auto-healing-peer-policy-unavailable",
        "standby-auto-healing-policy-disabled",
        "standby-auto-healing-policy-invalid",
        "standby-auto-healing-policy-transition",
        "standby-restoration-authorization-invalid",
        "standby-restoration-authority-stale-or-foreign",
        "standby-restoration-blocked",
        "standby-restoration-not-committed",
        "standby-restoration-policy-changed",
        "standby-restoration-policy-unavailable",
        "standby-restoration-start-identity-changed",
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
        "auto_healing",
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
    auto_healing = validated["auto_healing"]
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
        and isinstance(auto_healing, dict)
        and set(auto_healing) == {"state", "peer_agrees", "accepted_start"}
        and auto_healing.get("state") in {"enabled", "disabled", "transitioning", "blocked"}
        and isinstance(auto_healing.get("peer_agrees"), bool)
        and isinstance(auto_healing.get("accepted_start"), bool)
        and (
            auto_healing.get("state") not in {"enabled", "disabled"}
            or auto_healing.get("peer_agrees") is True
        )
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
        and (spki is None or isinstance(spki, str) and re.fullmatch(r"[0-9a-f]{64}", spki))
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
        "promotion_committed",
        "promotion_ready",
        "reasons",
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
    reasons = validated["reasons"]
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
        validated["state"] in _VM_HA_DISPLAY_STATES
        and validated["data_plane_mode"] in {"blocked", "passive", "active"}
        and (owner is None or isinstance(owner, str))
        and (guard_boot_id is None or isinstance(guard_boot_id, str))
        and (ready_boot_id is None or isinstance(ready_boot_id, str))
        and isinstance(validated["promotion_ready"], bool)
        and isinstance(validated["promotion_committed"], bool)
        and isinstance(validated["standby_ready"], bool)
        and isinstance(reasons, list)
        and all(
            isinstance(reason, str) and re.fullmatch(r"[a-z0-9-]+", reason) for reason in reasons
        )
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
    if validated["promotion_committed"] is True and not (
        validated["promotion_ready"] is True
        and validated["state"] == "active"
        and validated["data_plane_mode"] == "active"
        and pending is None
        and route is not None
    ):
        raise _VMHAAgentStatusPermanent(
            "VM-HA planned status has conflicting promotion commitment evidence"
        )
    return validated


def _fetch_vm_ha_agent_status(
    *,
    target: str,
    hostname: str,
    username: str,
    key_path: Path | None,
    client_auth: SSHClientAuth | None = None,
    ssh_policy: SSHTrustPolicy,
    inst_cfg: t.Any,
    runtime_binding: t.Any | None = None,
    expected_apply_locked: bool | None = None,
    expected_operation_id: str | None = None,
    require_local_generation: bool = True,
) -> dict[str, t.Any]:
    command = _build_ssh_base_cmd(
        key_path,
        client_auth=client_auth,
        ssh_policy=ssh_policy,
        hostname=hostname,
    )
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
    progress_callback: t.Callable[[], None] | None = None,
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
        if progress_callback is not None:
            progress_callback()
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


def _read_regular_file_snapshot(
    path: Path,
) -> tuple[bytes, _FileFingerprint] | None:
    """Read one stable regular-file inode without following symbolic links."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("safe no-follow file reads are unavailable on this platform")
    flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("refusing to read a non-regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read()
            after = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise OSError("file changed while it was being read") from error
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    current_identity = (
        current.st_dev,
        current.st_ino,
        current.st_mode,
        current.st_size,
        current.st_mtime_ns,
    )
    if before_identity != after_identity or after_identity != current_identity:
        raise OSError("file changed while it was being read")
    fingerprint = _FileFingerprint(
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    return content, fingerprint


def _file_fingerprint(path: Path) -> _FileFingerprint | None:
    snapshot = _read_regular_file_snapshot(path)
    return None if snapshot is None else snapshot[1]


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


def _render_external_ips_in_yaml(text: str, external_ips: list[list[str]]) -> str:
    """Return one targeted external_ips edit while preserving unrelated YAML text."""

    lines = text.splitlines()
    gateway_matches: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s*)gateway_group\s*:\s*(?:#.*)?$", line)
        if match:
            gateway_matches.append((index, match.group(1)))
    if len(gateway_matches) != 1:
        raise ValueError("Unable to identify one block-style gateway_group in YAML.")

    gateway_index, base_indent = gateway_matches[0]
    base_width = len(base_indent)
    block_end = len(lines)
    child_widths: list[int] = []
    for index in range(gateway_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        width = len(lines[index]) - len(lines[index].lstrip())
        if width <= base_width:
            block_end = index
            break
        child_widths.append(width)
    child_width = min(child_widths) if child_widths else base_width + 2
    child_indent = " " * child_width

    external_matches: list[int] = []
    insert_at = gateway_index + 1
    for index in range(gateway_index + 1, block_end):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        width = len(lines[index]) - len(lines[index].lstrip())
        if width != child_width:
            continue
        if re.match(r"^external_ips\s*:", lines[index].lstrip()):
            external_matches.append(index)
        elif re.match(r"^(?:name|instance_count)\s*:", lines[index].lstrip()):
            insert_at = index + 1
    if len(external_matches) > 1:
        raise ValueError("gateway_group contains duplicate external_ips keys.")
    if not external_matches:
        new_block = _format_external_ips_block(child_indent, external_ips)
        lines = lines[:insert_at] + new_block + lines[insert_at:]
        return "\n".join(lines) + "\n"

    external_index = external_matches[0]
    cursor = external_index + 1
    pending_trivia: int | None = None
    block_after = block_end
    while cursor < block_end:
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("#"):
            if pending_trivia is None:
                pending_trivia = cursor
            cursor += 1
            continue
        width = len(lines[cursor]) - len(lines[cursor].lstrip())
        if width <= child_width:
            block_after = pending_trivia if pending_trivia is not None else cursor
            break
        pending_trivia = None
        cursor += 1
    else:
        if pending_trivia is not None:
            block_after = pending_trivia
    new_block = _format_external_ips_block(child_indent, external_ips)
    lines = lines[:external_index] + new_block + lines[block_after:]
    return "\n".join(lines) + "\n"


def _update_external_ips_in_yaml(
    path: Path,
    external_ips: list[list[str]],
    *,
    expected_fingerprint: _FileFingerprint | None = None,
    source_text: str | None = None,
) -> bool:
    """Conditionally publish a complete matrix without following or clobbering files."""

    if source_text is None:
        snapshot = _read_regular_file_snapshot(path)
        if snapshot is None:
            raise OSError("Configuration file disappeared before it could be updated.")
        source_bytes, observed_fingerprint = snapshot
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise OSError("Configuration file is not valid UTF-8.") from error
        if expected_fingerprint is None:
            expected_fingerprint = observed_fingerprint
        elif observed_fingerprint != expected_fingerprint:
            raise OSError("Configuration file changed before network preparation completed.")
    if expected_fingerprint is None:
        raise OSError("Configuration fingerprint is required for a safe update.")
    rendered = _render_external_ips_in_yaml(source_text, external_ips)
    if rendered == _normalize_file_text(source_text):
        return False
    _atomic_write_text(
        path,
        rendered,
        expected_fingerprint=expected_fingerprint,
    )
    return True


_VMManagerOwner = t.TypeVar("_VMManagerOwner", bound=t.Callable[..., t.Any])
_VM_MANAGER_LIFETIMES: contextvars.ContextVar[contextlib.ExitStack | None] = contextvars.ContextVar(
    "nebius_vpngw_vm_manager_lifetimes", default=None
)


def _with_vm_manager_lifetimes(function: _VMManagerOwner) -> _VMManagerOwner:
    """Give one command owner a deterministic stack of VMManager contexts."""

    @functools.wraps(function)
    def wrapped(*args: t.Any, **kwargs: t.Any) -> t.Any:
        if _VM_MANAGER_LIFETIMES.get() is not None:
            return function(*args, **kwargs)
        with contextlib.ExitStack() as lifetimes:
            token = _VM_MANAGER_LIFETIMES.set(lifetimes)
            try:
                return function(*args, **kwargs)
            finally:
                _VM_MANAGER_LIFETIMES.reset(token)

    return t.cast(_VMManagerOwner, wrapped)


def _own_vm_manager(manager: VMManager) -> VMManager:
    lifetimes = _VM_MANAGER_LIFETIMES.get()
    if lifetimes is None:
        raise RuntimeError("VMManager construction has no owning command lifetime")
    return lifetimes.enter_context(manager)


class _GatewayVMDiscoveryError(RuntimeError):
    """A configured gateway VM could not be classified safely."""


def _list_status_routes(
    route_client: t.Any,
    request_type: t.Any,
    *,
    route_table_id: str,
) -> tuple[object, ...]:
    """Buffer every status route page before the caller renders a result."""

    return collect_nebius_pages(
        lambda page_token: route_client.list(
            request_type(
                parent_id=route_table_id,
                page_size=1000,
                page_token=page_token,
            )
        ),
        context="Status route",
        item_identity=nebius_resource_id,
    )


def _configured_gateway_vms_exist(
    client: t.Any,
    *,
    project_id: str,
    instance_names: t.Iterable[str],
) -> bool:
    """Return whether any exact configured gateway VM exists in the project."""

    try:
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
        from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

        instances = InstanceServiceClient(client)
    except Exception as error:
        raise _GatewayVMDiscoveryError("Unable to query configured gateway VMs.") from error

    for instance_name in instance_names:
        try:
            value = instances.get_by_name(
                GetByNameRequest(parent_id=project_id, name=instance_name)
            )
            waiter = getattr(value, "wait", None)
            response = waiter() if callable(waiter) else value
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                continue
            raise _GatewayVMDiscoveryError("Unable to query configured gateway VMs.") from error

        metadata = getattr(response, "metadata", None)
        returned_name = getattr(metadata, "name", None)
        returned_id = getattr(metadata, "id", None)
        returned_parent_id = getattr(metadata, "parent_id", None)
        if (
            returned_name != instance_name
            or not isinstance(returned_id, str)
            or not returned_id
            or returned_parent_id != project_id
        ):
            raise _GatewayVMDiscoveryError("Unable to query configured gateway VMs.")
        return True

    return False


@_with_vm_manager_lifetimes
def _ensure_gateway_vms_exist(
    plan: ResolvedDeploymentPlan,
    *,
    project_id: str | None,
    region: str | None,
    auth_token: str | None,
    tenant_id: str | None,
    action: str,
) -> None:
    if not project_id:
        print(f"[red]Error: project_id is required to {action}.[/red]")
        raise typer.Exit(code=1)

    effective_region = region or plan.gateway_group.region
    vm_mgr = _own_vm_manager(
        VMManager(
            project_id=project_id,
            region=effective_region,
            auth_token=auth_token,
            tenant_id=tenant_id,
            region_id=effective_region,
        )
    )

    client = vm_mgr._get_client()
    if client is None:
        print("[red]Error: Nebius SDK client not available; cannot verify gateway VMs.[/red]")
        raise typer.Exit(code=1)

    try:
        gateway_vms_exist = _configured_gateway_vms_exist(
            client,
            project_id=project_id,
            instance_names=(instance.hostname for instance in plan.iter_instance_configs()),
        )
    except _GatewayVMDiscoveryError as error:
        print("[red]Error: Unable to query configured gateway VMs.[/red]")
        raise typer.Exit(code=1) from error

    if not gateway_vms_exist:
        print("[red]No configured gateway VMs found.[/red]")
        print("[yellow]Run 'nebius-vpngw apply' to create gateway VMs first.[/yellow]")
        raise typer.Exit(code=1)


def _serialize_explicit_vm_ha_apply(function: t.Callable[..., t.Any]):
    """Hold one canonical project/gateway writer lock for every mutating apply."""

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
        requires_lock = bool(
            not arguments.arguments.get("dry_run", False) and canonical_project and gateway_name
        )
        if not requires_lock:
            return function(*args, **kwargs)
        lock = VMHAApplyLock(
            project_id=canonical_project,
            gateway_name=gateway_name,
        )
        try:
            lock.__enter__()
        except RuntimeError as error:
            print(f"[red]Gateway apply is already owned by another writer:[/red] {error}")
            raise typer.Exit(code=1) from error
        try:
            with _suppress_vm_ha_sdk_retry_diagnostics():
                return function(*args, **kwargs)
        except typer.Exit:
            raise
        except Exception as error:
            if _vm_ha_error_chain_has_sdk_code(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                label = "VM-HA" if plan.vm_ha is not None else "Gateway"
                print(
                    f"[red]{label} apply stopped: Nebius cloud authentication was rejected.[/red]"
                )
                print(
                    "[yellow]Refresh the Nebius CLI profile or replace NEBIUS_IAM_TOKEN, "
                    "then rerun apply.[/yellow]"
                )
                raise typer.Exit(code=1) from None
            if not _vm_ha_error_chain_has_sdk_code(error, "DEADLINE_EXCEEDED"):
                raise
            label = "VM-HA" if plan.vm_ha is not None else "Gateway"
            print(
                f"[red]{label} apply stopped: Nebius cloud request timed out "
                "after bounded retries.[/red]"
            )
            if plan.vm_ha is not None:
                print(
                    "[yellow]Run 'nebius-vpngw vm-ha --local-config-file <file>' "
                    "to inspect and resume.[/yellow]"
                )
            else:
                print("[yellow]Run 'nebius-vpngw apply -c <file>' to inspect and resume.[/yellow]")
            raise typer.Exit(code=1) from None
        finally:
            lock.__exit__(None, None, None)

    return wrapped


@dataclass(frozen=True)
class _VMHAApplyPlanReport:
    """Typed approval plan emitted before the first apply mutation."""

    kind: str
    digest: str
    engine_digest: str
    effects: tuple[str, ...]
    has_destructive_changes: bool
    managed_ssh_action: str | None
    managed_credential_action: str | None = None
    authorization_persisted: bool = False
    owner_refresh_required: bool = False
    artifact_sha256: str | None = None
    artifact: VMHAAgentArtifact | None = None
    impact: VMHACommandImpact = VMHACommandImpact(
        summary="Impact is not classified; operator approval is required",
        destructive=None,
        vpn_traffic_interruption=None,
        resource_creation=None,
    )


def _vm_ha_missing_standby_owner_refresh_required(
    *,
    replacement: _VMHAMissingStandbyReplacementPlan,
    planned_instances: t.Iterable[t.Any],
    existing_members: t.Mapping[str, str],
    lifecycle_state: VMHALifecycleState,
    vm_spec: t.Mapping[str, t.Any],
    management_key_path: Path | None,
    ssh_policy: SSHTrustPolicy,
) -> bool:
    """Return whether the serving owner must be upgraded for live peer replacement."""

    owner_config = next(
        instance
        for instance in planned_instances
        if instance.hostname == replacement.owner_instance_name
    )
    owner_target = existing_members.get(owner_config.hostname)
    if not owner_target or owner_config.vm_ha_node is None:
        raise RuntimeError("VM-HA missing standby owner status target is unavailable")
    runtime_binding = _vm_ha_planned_terminal_runtime_binding(
        lifecycle_state,
        owner_config,
        replacement=replacement,
    )
    status = _fetch_vm_ha_agent_status(
        target=owner_target,
        hostname=owner_config.hostname,
        username=(
            str(vm_spec.get("ssh_username") or "") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        ),
        key_path=management_key_path,
        client_auth=_vm_spec_ssh_client_auth(vm_spec),
        ssh_policy=ssh_policy,
        inst_cfg=owner_config,
        runtime_binding=runtime_binding,
        expected_apply_locked=False,
    )
    owner_node_id = owner_config.vm_ha_node.node_id
    serving_exactly = bool(
        status.get("data_plane_mode") == "active"
        and status.get("promotion_ready") is True
        and status.get("observed_owner_node_id") == owner_node_id
    )
    missing_peer_fail_closed = bool(
        status.get("state") == "blocked"
        and status.get("reasons") == ["controller-step-failed"]
        and status.get("data_plane_mode") == "blocked"
        and status.get("promotion_ready") is False
        and status.get("observed_owner_node_id") == owner_node_id
        and status.get("apply_locked") is False
        and status.get("pending_operation_id") is None
        and status.get("transfer_inhibition_operation_id") is None
        and _vm_ha_active_route_receipt_matches(
            status,
            active_node_id=owner_node_id,
            runtime_binding=runtime_binding,
        )
    )
    transaction = lifecycle_state.transaction
    inhibition_effect = f"install-standby-replacement-inhibition-{owner_node_id}"
    cloud_effects = {
        vm_ha_missing_standby_replacement_effect(
            replacement.target_instance_name,
            replacement.replacement_cycle,
            action,
        )
        for action in ("create-boot-disk", "create-compute")
    }
    pending_inhibition_guarded_owner = bool(
        lifecycle_state.record_version == 4
        and lifecycle_state.status is VMHALifecycleStatus.PROVISIONING
        and transaction is not None
        and transaction.approval_kind == "recovery"
        and transaction.approval_digest == replacement.approval_digest
        and transaction.operation_id == replacement.operation_id
        and transaction.pending_effect == inhibition_effect
        and not cloud_effects.intersection(transaction.completed_effects)
        and transaction.observation_guard is None
        and transaction.accepted_cloud_operation_effect is None
        and transaction.accepted_cloud_operation_id is None
        and replacement.authorization_persisted
        and status.get("state") == "blocked"
        and status.get("reasons") == ["current-boot-guard-not-active"]
        and status.get("data_plane_mode") == "blocked"
        and status.get("promotion_ready") is False
        and status.get("observed_owner_node_id") == owner_node_id
        and status.get("apply_locked") is False
        and status.get("apply_operation_id") is None
        and status.get("pending_operation_id") is None
        and status.get("transfer_inhibition_operation_id") is None
        and _vm_ha_active_route_receipt_matches(
            status,
            active_node_id=owner_node_id,
            runtime_binding=runtime_binding,
        )
    )
    inhibited_owner_resume = bool(
        lifecycle_state.record_version == 4
        and lifecycle_state.status is VMHALifecycleStatus.PROVISIONING
        and transaction is not None
        and transaction.approval_kind == "recovery"
        and transaction.approval_digest == replacement.approval_digest
        and transaction.operation_id == replacement.operation_id
        and transaction.pending_effect == inhibition_effect
        and not cloud_effects.intersection(transaction.completed_effects)
        and transaction.observation_guard is None
        and transaction.accepted_cloud_operation_effect is None
        and transaction.accepted_cloud_operation_id is None
        and replacement.authorization_persisted
        and status.get("data_plane_mode") == "passive"
        and status.get("promotion_ready") is False
        and status.get("observed_owner_node_id") == owner_node_id
        and status.get("apply_locked") is False
        and status.get("apply_operation_id") is None
        and status.get("transfer_inhibition_operation_id") == replacement.operation_id
        and _vm_ha_active_route_receipt_matches(
            status,
            active_node_id=owner_node_id,
            runtime_binding=runtime_binding,
        )
        and (
            status.get("state") == "blocked"
            and status.get("reasons") == ["checkpointed-action-prerequisites-changed"]
            and status.get("pending_operation_id") is None
            and status.get("transfer_inhibition_quiescent") is True
            or status.get("state") == "promoting"
            and status.get("reasons")
            in (
                ["candidate-dataplane-requires-owner-only-preparation"],
                ["owner-routes-require-reconciliation"],
                ["exact-owner-ready-to-enable-forwarding"],
                ["replaying-checkpointed-action"],
            )
            and isinstance(status.get("pending_operation_id"), str)
            and bool(status.get("pending_operation_id"))
            and status.get("transfer_inhibition_quiescent") is False
        )
    )
    if not (
        serving_exactly
        or missing_peer_fail_closed
        or pending_inhibition_guarded_owner
        or inhibited_owner_resume
    ):
        raise RuntimeError("VM-HA missing standby owner is not serving exactly")
    capabilities = status.get("controller_capabilities")
    required_capabilities = {
        LIVE_PEER_REPLACEMENT_CAPABILITY,
        STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
    }
    return not (isinstance(capabilities, list) and required_capabilities.issubset(capabilities))


def _vm_ha_apply_plan_impact(
    kind: str,
    *,
    has_destructive_changes: bool,
    owner_refresh_required: bool = False,
) -> VMHACommandImpact:
    """Classify exact apply impact without parsing presentation effect strings."""

    if has_destructive_changes:
        return VMHACommandImpact(
            summary=("Deletes and recreates gateway VM resources and may interrupt VPN traffic"),
            destructive=True,
            vpn_traffic_interruption=True,
            resource_creation=True,
        )
    impacts = {
        "migration": VMHACommandImpact(
            summary=(
                "May briefly interrupt VPN traffic during VM-HA activation; "
                "the serving gateway is retained"
            ),
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=True,
        ),
        "provisioning": VMHACommandImpact(
            summary=(
                "May briefly interrupt VPN traffic during VM-HA activation; "
                "no gateway VM or disk is deleted"
            ),
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=True,
        ),
        "recovery": VMHACommandImpact(
            summary=(
                "May briefly interrupt VPN traffic while the interrupted VM-HA "
                "transaction resumes; no gateway VM or disk is deleted"
            ),
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=None,
        ),
        "resume-transaction": VMHACommandImpact(
            summary=(
                "May briefly interrupt VPN traffic while the approved VM-HA transaction "
                "resumes; no gateway VM or disk is deleted"
            ),
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=None,
        ),
        "failed-passive-replacement": VMHACommandImpact(
            summary=(
                "Deletes and recreates the failed standby VM and boot disk; "
                "VPN traffic is expected to remain available"
            ),
            destructive=True,
            vpn_traffic_interruption=False,
            resource_creation=True,
        ),
        "active-standby-replacement": VMHACommandImpact(
            summary=(
                (
                    "Upgrades and restarts the serving-owner VM-HA control services, then "
                    "creates a fresh non-owner VM and boot disk; existing disks are left "
                    "untouched and VPN traffic may be briefly interrupted"
                )
                if owner_refresh_required
                else (
                    "Creates a fresh non-owner VM and boot disk and may rotate only its "
                    "managed SSH identity; existing disks are left untouched and the "
                    "serving owner is not restarted"
                )
            ),
            destructive=False,
            vpn_traffic_interruption=owner_refresh_required,
            resource_creation=True,
        ),
        "apply-convergence": VMHACommandImpact(
            summary=(
                "May briefly interrupt VPN traffic while the serving owner is reconciled; "
                "no gateway VM or disk is deleted"
            ),
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=False,
        ),
        "artifact-standby-recovery": VMHACommandImpact(
            summary=(
                "May briefly interrupt VPN traffic while the serving owner is upgraded; "
                "no gateway VM or disk is deleted"
            ),
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=False,
        ),
    }
    return impacts.get(
        kind,
        VMHACommandImpact(
            summary="Impact is not classified; operator approval is required",
            destructive=None,
            vpn_traffic_interruption=None,
            resource_creation=None,
        ),
    )


class _VMHAApplyPlanCaptured(RuntimeError):
    """Private control-flow signal for a read-only typed apply plan."""

    def __init__(self, report: _VMHAApplyPlanReport) -> None:
        super().__init__("VM-HA apply plan captured")
        self.report = report


class _VMHAApplyPlanningFailed(RuntimeError):
    """Sanitized pre-mutation planning failure projected by the VM-HA facade."""

    def __init__(
        self,
        *,
        reason: str,
        next_action: str,
        classification: VMHACommandClassification = (
            VMHACommandClassification.EXTERNAL_PREREQUISITE
        ),
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.next_action = next_action
        self.classification = classification


def _resolve_vm_ha_agent_artifact(
    ssh_policy: SSHTrustPolicy | None,
) -> VMHAAgentArtifact:
    """Keep read-only artifact selection as one independently testable boundary."""

    return SSHPush(ssh_policy=ssh_policy).resolve_vm_ha_agent_artifact()


class _VMHAProgressState(str, Enum):
    """Closed presentation states for sanitized VM-HA progress."""

    STARTED = "started"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class _VMHAProgressPhase(str, Enum):
    """Identity-free VM-HA phases that are safe to render to operators."""

    RESOLVE_CONFIG = "resolve-config"
    PREPARE_PASSIVE_IP = "prepare-passive-ip"
    INSPECT_STATE = "inspect-state"
    OBSERVE_CONTROLLER = "observe-controller"
    CONFIRM_HEALTH = "confirm-health"
    PLAN_CONVERGENCE = "plan-convergence"
    ACQUIRE_LOCK = "acquire-lock"
    REVALIDATE_APPROVAL = "revalidate-approval"
    VERIFY_ENGINE_PLAN = "verify-engine-plan"
    EXECUTE_APPLY = "execute-apply"
    VERIFY_CREDENTIALS = "verify-credentials"
    PREPARE_TRANSACTION = "prepare-transaction"
    PREPARE_SERVICE_ACCOUNT = "prepare-service-account"
    RECONCILE_COMPUTE = "reconcile-compute"
    WAIT_COMPUTE = "wait-compute"
    WAIT_BOOTSTRAP = "wait-bootstrap"
    BIND_MEMBERS = "bind-members"
    STAGE_STANDBY = "stage-standby"
    STAGE_OWNER = "stage-owner"
    PREPARE_AGENT_PACKAGES = "prepare-agent-packages"
    LOCK_STANDBY = "lock-standby"
    LOCK_OWNER = "lock-owner"
    DECLARE_OWNER = "declare-owner"
    PREPARE_MTLS = "prepare-mtls"
    RELOAD_STANDBY_SERVICES = "reload-standby-services"
    RELOAD_OWNER_SERVICES = "reload-owner-services"
    VERIFY_FENCED = "verify-fenced"
    COMMIT_MTLS = "commit-mtls"
    VERIFY_OWNER = "verify-owner"
    VERIFY_STANDBY = "verify-standby"
    COMMIT_LIFECYCLE = "commit-lifecycle"
    AUTHENTICATE = "authenticate"
    VERIFY_REARM_AUTHORITY = "verify-rearm-authority"
    REQUEST_REARM = "request-rearm"
    WAIT_REARM_COMPUTE = "wait-rearm-compute"
    WAIT_REARM_SSH = "wait-rearm-ssh"
    WAIT_REARM_SERVICES = "wait-rearm-services"
    ROTATE_MTLS = "rotate-mtls"


@dataclass(frozen=True)
class _VMHAProgressEvent:
    phase: _VMHAProgressPhase
    state: _VMHAProgressState
    elapsed_seconds: float | None = None


_VMHAProgressSink = t.Callable[[_VMHAProgressEvent], None]
_VMHAStatusFactory = t.Callable[[t.TextIO, str], t.Any]


class _VMHASDKRetryDiagnosticFilter(logging.Filter):
    """Drop only Nebius SDK records that explicitly announce an internal retry."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage().casefold()
        except Exception:
            return True
        return not (
            "request attempt" in message
            and ("but will be retried" in message or "will retry the request" in message)
        )


def _vm_ha_error_chain_has_sdk_code(error: BaseException, code_name: str) -> bool:
    """Match one typed SDK status in the finite explicit-cause chain."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if nebius_request_error_code_is(current, code_name):
            return True
        current = current.__cause__
    return False


@contextlib.contextmanager
def _suppress_vm_ha_sdk_retry_diagnostics() -> t.Iterator[None]:
    """Hide only SDK retry announcements during one serialized VM-HA apply."""

    logger = logging.getLogger("nebius.aio.request")
    retry_filter = _VMHASDKRetryDiagnosticFilter()
    logger.addFilter(retry_filter)
    try:
        yield
    finally:
        logger.removeFilter(retry_filter)


class _VMHARichStatus:
    """Transient spinner that never replaces the facade-owned process streams."""

    from rich.console import Console
    from rich.live import Live
    from rich.spinner import Spinner

    def __init__(self, stream: t.TextIO, label: str) -> None:
        self.status = label
        self.renderable = self.Spinner(
            "dots",
            text=label,
            style="cyan",
        )
        self._live = self.Live(
            self.renderable,
            console=self.Console(
                file=stream,
                force_terminal=True,
                highlight=False,
            ),
            transient=True,
            refresh_per_second=12.5,
            redirect_stdout=False,
            redirect_stderr=False,
        )

    def start(self) -> None:
        self._live.start()

    def update(self, label: str) -> None:
        self.status = label
        self.renderable.update(text=label)

    def stop(self) -> None:
        self._live.stop()


def _vm_ha_status_spinner(stream: t.TextIO, label: str) -> _VMHARichStatus:
    """Create one transient animated status bound to the command's stderr."""

    return _VMHARichStatus(stream, label)


_VMHA_PROGRESS_LABELS: t.Mapping[_VMHAProgressPhase, str] = MappingProxyType(
    {
        _VMHAProgressPhase.RESOLVE_CONFIG: "resolving and validating the VM-HA configuration",
        _VMHAProgressPhase.PREPARE_PASSIVE_IP: "preparing the passive Nebius public IP",
        _VMHAProgressPhase.INSPECT_STATE: "inspecting authoritative VM-HA state",
        _VMHAProgressPhase.OBSERVE_CONTROLLER: "observing controller-owned recovery",
        _VMHAProgressPhase.CONFIRM_HEALTH: "verifying two agreeing fresh health samples",
        _VMHAProgressPhase.PLAN_CONVERGENCE: "planning exact VM-HA convergence",
        _VMHAProgressPhase.ACQUIRE_LOCK: "acquiring the VM-HA writer lock",
        _VMHAProgressPhase.REVALIDATE_APPROVAL: "revalidating the exact approved plan",
        _VMHAProgressPhase.VERIFY_ENGINE_PLAN: "binding the approved plan at the apply effect boundary",
        _VMHAProgressPhase.EXECUTE_APPLY: "applying the approved VM-HA transaction",
        _VMHAProgressPhase.VERIFY_CREDENTIALS: "verifying VM-HA runtime credential identity",
        _VMHAProgressPhase.PREPARE_TRANSACTION: "preparing and verifying the durable VM-HA transaction",
        _VMHAProgressPhase.PREPARE_SERVICE_ACCOUNT: "preparing the approved service-account session",
        _VMHAProgressPhase.RECONCILE_COMPUTE: "creating or reconciling the warm-standby Compute member",
        _VMHAProgressPhase.WAIT_COMPUTE: "waiting for the VM-HA Compute members to become reachable",
        _VMHAProgressPhase.WAIT_BOOTSTRAP: "waiting for both VM-HA members to become configurable",
        _VMHAProgressPhase.BIND_MEMBERS: "binding provisioned members to the VM-HA lifecycle",
        _VMHAProgressPhase.STAGE_STANDBY: "staging the current configuration on the non-owner",
        _VMHAProgressPhase.STAGE_OWNER: "staging the current configuration on the owner",
        _VMHAProgressPhase.PREPARE_AGENT_PACKAGES: "preparing exact VM-HA agent packages",
        _VMHAProgressPhase.LOCK_STANDBY: "installing the exact apply lock on the non-owner",
        _VMHAProgressPhase.LOCK_OWNER: "installing the exact apply lock on the owner",
        _VMHAProgressPhase.DECLARE_OWNER: "declaring the exact cloud-selected owner",
        _VMHAProgressPhase.PREPARE_MTLS: "preparing exact VM-local mTLS identity and peer trust",
        _VMHAProgressPhase.RELOAD_STANDBY_SERVICES: "applying configuration and restarting VM-HA control services on the non-owner",
        _VMHAProgressPhase.RELOAD_OWNER_SERVICES: "applying configuration and restarting VM-HA control services on the owner",
        _VMHAProgressPhase.VERIFY_FENCED: "verifying both activated members remain passively fenced",
        _VMHAProgressPhase.COMMIT_MTLS: "committing managed mTLS after fresh peer proof",
        _VMHAProgressPhase.VERIFY_OWNER: "releasing the owner lock and verifying routes and forwarding",
        _VMHAProgressPhase.VERIFY_STANDBY: "releasing the standby lock and verifying passive non-forwarding state",
        _VMHAProgressPhase.COMMIT_LIFECYCLE: "committing the durable ACTIVE lifecycle state",
        _VMHAProgressPhase.AUTHENTICATE: "authenticating for VM-HA convergence",
        _VMHAProgressPhase.VERIFY_REARM_AUTHORITY: "verifying the exact owner and non-owner rearm authority",
        _VMHAProgressPhase.REQUEST_REARM: "requesting owner-side standby rearm",
        _VMHAProgressPhase.WAIT_REARM_COMPUTE: "waiting for the standby Compute member to become Running",
        _VMHAProgressPhase.WAIT_REARM_SSH: "waiting for the standby management channel",
        _VMHAProgressPhase.WAIT_REARM_SERVICES: "waiting for standby services and warm-standby readiness",
        _VMHAProgressPhase.ROTATE_MTLS: "rotating both VM-HA mTLS identities",
    }
)

_VMHA_PROGRESS_COMPLETED_LABELS: t.Mapping[_VMHAProgressPhase, str] = MappingProxyType(
    {
        _VMHAProgressPhase.EXECUTE_APPLY: "approved VM-HA transaction completed",
        _VMHAProgressPhase.WAIT_BOOTSTRAP: "both VM-HA members are ready for configuration",
        _VMHAProgressPhase.PREPARE_AGENT_PACKAGES: "exact VM-HA agent packages are ready",
    }
)


def _emit_vm_ha_progress(
    sink: _VMHAProgressSink | None,
    phase: _VMHAProgressPhase,
    state: _VMHAProgressState,
    *,
    elapsed_seconds: float | None = None,
) -> None:
    if sink is not None:
        try:
            sink(_VMHAProgressEvent(phase, state, elapsed_seconds))
        except Exception:
            # Presentation must never become mutation or verification authority.
            pass


@contextlib.contextmanager
def _vm_ha_progress_step(
    sink: _VMHAProgressSink | None,
    phase: _VMHAProgressPhase,
) -> t.Iterator[None]:
    """Emit truthful start/completion, and never complete a failed phase."""

    _emit_vm_ha_progress(sink, phase, _VMHAProgressState.STARTED)
    try:
        yield
    except BaseException:
        _emit_vm_ha_progress(sink, phase, _VMHAProgressState.FAILED)
        raise
    _emit_vm_ha_progress(sink, phase, _VMHAProgressState.COMPLETED)


class _VMHAProgressWait:
    """Rate-limit elapsed wait events without participating in authority."""

    def __init__(
        self,
        sink: _VMHAProgressSink | None,
        phase: _VMHAProgressPhase,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self._sink = sink
        self._phase = phase
        self._started = time.monotonic()
        self._interval_seconds = interval_seconds
        self._next_update = interval_seconds

    def update(self) -> None:
        elapsed = max(time.monotonic() - self._started, 0.0)
        if elapsed < self._next_update:
            return
        _emit_vm_ha_progress(
            self._sink,
            self._phase,
            _VMHAProgressState.WAITING,
            elapsed_seconds=elapsed,
        )
        while self._next_update <= elapsed:
            self._next_update += self._interval_seconds


class _VMHAProgressReporter:
    """Render best-effort progress and close any unfinished nested phases."""

    def __init__(
        self,
        stream: t.TextIO,
        *,
        status_factory: _VMHAStatusFactory = _vm_ha_status_spinner,
    ) -> None:
        self._stream = stream
        self._status_factory = status_factory
        self._active: list[_VMHAProgressPhase] = []
        self._disabled = False
        self._status: t.Any | None = None
        self._status_phase: _VMHAProgressPhase | None = None
        self._sdk_retry_logger = logging.getLogger("nebius.aio.request")
        self._sdk_retry_filter = _VMHASDKRetryDiagnosticFilter()
        self._sdk_retry_filter_installed = False
        try:
            self._interactive = bool(stream.isatty())
        except Exception:
            self._interactive = False

    def _install_sdk_retry_filter(self) -> None:
        if self._sdk_retry_filter_installed:
            return
        self._sdk_retry_logger.addFilter(self._sdk_retry_filter)
        self._sdk_retry_filter_installed = True

    def _remove_sdk_retry_filter(self) -> None:
        if not self._sdk_retry_filter_installed:
            return
        self._sdk_retry_logger.removeFilter(self._sdk_retry_filter)
        self._sdk_retry_filter_installed = False

    def _stop_status(self) -> None:
        status = self._status
        self._status = None
        self._status_phase = None
        if status is None:
            return
        try:
            status.stop()
        except Exception:
            self._disabled = True

    def _show_status(self, phase: _VMHAProgressPhase, label: str) -> None:
        if not self._interactive or self._disabled:
            return
        if self._status is not None and self._status_phase is phase:
            self._status.update(label)
            return
        self._stop_status()
        if self._disabled:
            return
        status = self._status_factory(self._stream, label)
        self._status = status
        self._status_phase = phase
        status.start()

    def _resume_active_status(self) -> None:
        if not self._active:
            return
        phase = self._active[-1]
        self._show_status(phase, f"{_VMHA_PROGRESS_LABELS[phase]}.")

    def _render(self, event: _VMHAProgressEvent) -> None:
        if self._disabled:
            return
        suffix = (
            f" ({event.elapsed_seconds:.0f}s elapsed)" if event.elapsed_seconds is not None else ""
        )
        try:
            if event.state in {
                _VMHAProgressState.STARTED,
                _VMHAProgressState.WAITING,
            }:
                self._show_status(
                    event.phase,
                    f"{_VMHA_PROGRESS_LABELS[event.phase]}{suffix}.",
                )
                return

            self._stop_status()
            succeeded = event.state is _VMHAProgressState.COMPLETED
            label = (
                _VMHA_PROGRESS_COMPLETED_LABELS.get(
                    event.phase,
                    _VMHA_PROGRESS_LABELS[event.phase],
                )
                if succeeded
                else _VMHA_PROGRESS_LABELS[event.phase]
            )
            typer.secho(
                f"{'✓' if succeeded else '✗'} {label}.",
                fg=typer.colors.GREEN if succeeded else typer.colors.RED,
                file=self._stream,
                color=self._interactive,
            )
        except Exception:
            self._disabled = True

    def __call__(self, event: _VMHAProgressEvent) -> None:
        if event.state is _VMHAProgressState.STARTED:
            self._active.append(event.phase)
            self._install_sdk_retry_filter()
        elif event.state in {
            _VMHAProgressState.COMPLETED,
            _VMHAProgressState.FAILED,
        }:
            try:
                index = len(self._active) - 1 - self._active[::-1].index(event.phase)
            except ValueError:
                index = -1
            if index >= 0:
                dangling = self._active[index + 1 :]
                del self._active[index:]
                for phase in reversed(dangling):
                    self._render(_VMHAProgressEvent(phase, _VMHAProgressState.FAILED))
        self._render(event)
        if event.state in {
            _VMHAProgressState.COMPLETED,
            _VMHAProgressState.FAILED,
        }:
            self._resume_active_status()
            if not self._active:
                self._remove_sdk_retry_filter()

    def close_unfinished(self) -> None:
        try:
            while self._active:
                self._render(
                    _VMHAProgressEvent(
                        self._active.pop(),
                        _VMHAProgressState.FAILED,
                    )
                )
        finally:
            self._stop_status()
            self._remove_sdk_retry_filter()


def _vm_ha_progress_sink(stream: t.TextIO) -> _VMHAProgressReporter:
    """Bind progress to the caller's original stderr before raw capture begins."""

    return _VMHAProgressReporter(stream)


def _validate_vm_ha_expected_apply_plan(
    actual: _VMHAApplyPlanReport | None,
    expected: _VMHAApplyPlanReport | None,
) -> None:
    """Reject approval drift at the apply engine's last pre-effect boundary."""

    if expected is not None and actual != expected:
        raise RuntimeError("VM-HA apply plan changed after approval")


def _missing_standby_apply_approval_lines(
    local_config_file: Path,
) -> tuple[str, str]:
    """Return the direct-apply refusal and its public VM-HA action."""

    return (
        "Missing standby replacement must be approved through vm-ha.",
        "Next: run nebius-vpngw vm-ha --local-config-file "
        f"{shlex.quote(str(local_config_file))} to create the missing non-owner VM.",
    )


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


@_with_vm_manager_lifetimes
def _apply_impl(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: str | None = typer.Option(
        None,
        help=(
            "Ordinary gateways only: ensure the exact dedicated Service Account/group "
            "with the reviewed project editor permit and use an impersonated token"
        ),
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    region: str | None = typer.Option(None, help=_NEBIUS_REGION_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect actions without applying"),
    prepare_vm_ha_peer_rotation: bool = typer.Option(
        False,
        "--prepare-vm-ha-peer-rotation",
        help=(
            "Stage a VM-HA IPsec peer credential change and exit with both members passively fenced"
        ),
    ),
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
    *,
    replace_missing_vm_ha_standby: str | None = None,
    vm_ha_plan_sink: t.Callable[[_VMHAApplyPlanReport], None] | None = None,
    vm_ha_progress_sink: _VMHAProgressSink | None = None,
    stop_after_vm_ha_plan: bool = False,
    expected_vm_ha_plan: _VMHAApplyPlanReport | None = None,
):
    """Reconcile desired state in Nebius and on the gateway VMs.

    Safe to rerun. Existing VMs, the dedicated gateway subnet, its route table,
    and matching IP allocations are reused when they already match the config.
    Use --recreate-gw only when infrastructure changes require VM recreation.
    """
    local_config_was_explicit = local_config_file is not None
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=True,
        exit_after_create=True,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = _load_config_with_region_override(
        local_config_file,
        region=region,
    )

    print("[bold]Building deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    print("[bold]Validating quotas and constraints...[/bold]")
    plan.validate()

    if prepare_vm_ha_peer_rotation:
        _validate_vm_ha_peer_rotation_preparation(
            plan,
            local_config_was_explicit=local_config_was_explicit,
            approval_flags_present=any(
                value is not None
                for value in (
                    approve_vm_ha_migration,
                    recover_vm_ha_migration,
                    replace_failed_vm_ha_passive,
                )
            ),
        )

    if replace_failed_vm_ha_passive is not None and plan.vm_ha is None:
        print("[red]Failed-passive replacement requires explicit VM-HA configuration.[/red]")
        raise typer.Exit(code=1)

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
    effective_region = (
        str(
            getattr(getattr(plan, "gateway_group", None), "region", "")
            or local_cfg.get("region_id")
            or ""
        ).strip()
        or None
    )
    region_id = effective_region
    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec", {})
    raw_management_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    management_key_path = Path(raw_management_key).expanduser() if raw_management_key else None
    planned_instances = tuple(plan.iter_instance_configs())
    vm_ha_node_ids = tuple(
        str(getattr(getattr(instance, "vm_ha_node", None), "node_id", "") or "")
        for instance in planned_instances
        if getattr(getattr(instance, "vm_ha_node", None), "node_id", None)
    )
    gateway_name = str(getattr(getattr(plan, "gateway_group", None), "name", "") or "")
    if plan.vm_ha is not None and sa is not None:
        print(
            "[red]VM-HA manages one deterministic runtime Service Account; "
            "--sa is supported only for ordinary gateways.[/red]"
        )
        raise typer.Exit(code=1)
    vm_ha_credentials: VMHACredentialSet | None = None
    vm_ha_credential_plan: VMHAManagedCredentialPlan | None = None
    if plan.vm_ha is not None:
        if not proj_id:
            print("[red]VM-HA runtime credential verification requires an exact project ID.[/red]")
            raise typer.Exit(code=1)
        print("[bold]Inspecting managed VM-HA runtime credentials...[/bold]")
        with _vm_ha_progress_step(
            vm_ha_progress_sink,
            _VMHAProgressPhase.VERIFY_CREDENTIALS,
        ):
            try:
                vm_ha_credential_plan = inspect_managed_vm_ha_credentials(
                    project_id=proj_id,
                    gateway_name=gateway_name,
                    node_ids=vm_ha_node_ids,
                    tenant_id=tenant_id,
                    region_id=region_id,
                )
                vm_ha_credentials = vm_ha_credential_plan.credentials
            except (VMHACredentialIdentityError, VMHAManagedCredentialError) as error:
                reason = getattr(error, "reason", str(error))
                print("[red]Managed VM-HA runtime credential inspection failed.[/red]")
                print(f"[yellow]  - {reason}[/yellow]")
                raise typer.Exit(code=1) from error
        if vm_ha_credential_plan.credentials is not None:
            print("[green]✓ Managed VM-HA runtime credential identity verified[/green]")
        else:
            print(
                "[yellow]Managed VM-HA runtime credentials will be created after "
                "the exact plan is approved.[/yellow]"
            )
    lifecycle_store = VMHALifecycleStore(local_config_file)
    try:
        lifecycle_state = lifecycle_store.read(
            expected_project_id=proj_id,
            expected_gateway_name=gateway_name,
        )
    except ValueError as error:
        print("[red]VM-HA lifecycle state is invalid; apply is blocked before cloud access:[/red]")
        print(f"[yellow]  - {error}[/yellow]")
        raise typer.Exit(code=1) from error
    replacement_policy_reproof_required = bool(
        lifecycle_state is not None
        and lifecycle_state.status is VMHALifecycleStatus.ACTIVE
        and lifecycle_state.transaction is not None
        and lifecycle_state.transaction.checkpoint == "missing-standby-replacement-complete"
    )
    lifecycle_retained_hosts: set[str] = set()
    if plan.vm_ha is not None and lifecycle_state is not None:
        vm_ha_spec = plan.gateway_group.vm_ha
        assert vm_ha_spec is not None
        if lifecycle_state.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS:
            print("[red]VM-HA activation is blocked by an unfinished removal transition.[/red]")
            raise typer.Exit(code=1)
        if lifecycle_state.status in {
            VMHALifecycleStatus.PROVISIONING,
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
            lifecycle_retained_hosts = {
                member.instance_name for member in lifecycle_state.members if member.compute_id
            }
    if (
        plan.vm_ha is not None
        and vm_ha_credential_plan is not None
        and vm_ha_credential_plan.credentials is None
        and lifecycle_state is not None
        and lifecycle_state.status
        in {
            VMHALifecycleStatus.PROVISIONING,
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.ACTIVE,
        }
    ):
        print(
            "[red]Managed VM-HA credentials are missing for an active lifecycle; "
            "automatic replacement or rotation is refused.[/red]"
        )
        raise typer.Exit(code=1)
    if vm_ha_credentials is not None:
        try:
            _validate_vm_ha_lifecycle_credential_transition(
                lifecycle_state,
                vm_ha_credentials,
            )
        except ValueError as error:
            print("[red]VM-HA lifecycle credential identity conflicts with this apply.[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error

    ssh_policy: SSHTrustPolicy | None = None
    ordinary_enrollment_required: frozenset[str] = frozenset()
    former_vm_ha_members: dict[str, str] = {}
    legacy_vm_ha_identities: dict[str, LegacyVMHAIdentity | None] | None = None
    discovery_manager: VMManager | None = None
    vm_ha_migration_active_name: str | None = None
    vm_ha_recovery_required = False
    vm_ha_activation_recovery_required = False
    vm_ha_existing_members: dict[str, str] = {}
    vm_ha_passive_replacement: tuple[str, str] | None = None
    vm_ha_missing_standby_replacement: _VMHAMissingStandbyReplacementPlan | None = None
    vm_ha_missing_standby_observation: t.Mapping[str, object] | None = None
    vm_ha_ssh_trust_scope: VMHASSHTrustScope | None = None
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
    lifecycle_journal: VMHALifecycleJournal | None = None
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
        discovery_auth_token = _apply_operator_auth_token()
    if plan.vm_ha is not None:
        assert vm_ha_credential_plan is not None
        blockers = _vm_ha_activation_blockers()
        if blockers:
            print("[red]VM-HA apply is BLOCKED before external mutation.[/red]")
            for blocker in blockers:
                print(f"[yellow]  - {blocker}[/yellow]")
            raise typer.Exit(code=1)
        try:
            discovery_manager = _own_vm_manager(
                VMManager(
                    project_id=proj_id,
                    region=effective_region,
                    auth_token=discovery_auth_token,
                    tenant_id=tenant_id,
                    region_id=region_id,
                    management_key_path=management_key_path,
                    management_public_key=vm_spec.get("ssh_public_key"),
                )
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
                    vm_ha_missing_standby_observation = candidate_observation
                    try:
                        vm_ha_missing_standby_replacement = _vm_ha_missing_standby_replacement_plan(
                            plan,
                            lifecycle_state,
                            candidate_observation,
                        )
                    except ValueError:
                        vm_ha_missing_standby_replacement = None
                    if (
                        vm_ha_missing_standby_replacement is not None
                        and not vm_ha_missing_standby_replacement.authorization_persisted
                    ):
                        discovery_manager.validate_missing_vm_ha_standby_replacement(
                            plan.gateway_group,
                            plan.gateway.get("local_prefixes"),
                            target_instance_name=(
                                vm_ha_missing_standby_replacement.target_instance_name
                            ),
                            retired_compute_id=(
                                vm_ha_missing_standby_replacement.retired_compute_id
                            ),
                            replacement_disk_name=(
                                vm_ha_missing_standby_replacement.replacement_disk_name
                            ),
                            primary_allocation_id=(
                                vm_ha_missing_standby_replacement.primary_allocation_id
                            ),
                            public_allocation_id=(
                                vm_ha_missing_standby_replacement.public_allocation_id
                            ),
                        )
                    if vm_ha_missing_standby_replacement is None:
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
            if lifecycle_state is None or lifecycle_state.status in {
                VMHALifecycleStatus.REMOVED,
                VMHALifecycleStatus.DESTROYED,
            }:
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
            if vm_ha_missing_standby_replacement is not None:
                enrollment_hosts.add(vm_ha_missing_standby_replacement.target_instance_name)
            retained_hosts = set(existing_members) | lifecycle_retained_hosts
            if vm_ha_missing_standby_replacement is not None:
                retained_hosts.discard(vm_ha_missing_standby_replacement.target_instance_name)
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
            trust_bindings: dict[str, t.Callable[[], None]] = {}
            recover_host_identities: t.Callable[..., t.Any] | None = None
            ordinary_migration_hosts = _vm_ha_ordinary_migration_ssh_hosts(
                plan,
                lifecycle_state,
                vm_ha_migration_active_name,
            )
            ordinary_migration_import_hosts = _vm_ha_ordinary_migration_ssh_import_hosts(
                lifecycle_state,
                vm_ha_migration_active_name,
                ordinary_migration_hosts,
            )
            binding_builder = getattr(discovery_manager, "vm_ha_ssh_trust_bindings", None)
            recovery_builder = getattr(discovery_manager, "recover_vm_ha_ssh_host_keys", None)
            lifecycle_snapshot_loader: t.Callable[[], t.Any] | None = None
            if callable(binding_builder):

                def load_hardened_lifecycle():
                    snapshot = lifecycle_store.read_hardened(
                        expected_project_id=proj_id,
                        expected_gateway_name=gateway_name,
                    )
                    expected_lifecycle = (
                        lifecycle_journal.state
                        if lifecycle_journal is not None
                        else lifecycle_state
                    )
                    if (
                        snapshot is not None
                        and expected_lifecycle is not None
                        and snapshot.state != expected_lifecycle
                    ):
                        raise RuntimeError(
                            "VM-HA lifecycle authority changed during SSH trust preflight"
                        )
                    return snapshot

                lifecycle_snapshot_loader = load_hardened_lifecycle

                trust_bindings = binding_builder(
                    plan.gateway_group,
                    retained_hosts=set(existing_members),
                    lifecycle_snapshot_loader=load_hardened_lifecycle,
                    ordinary_migration_hosts=ordinary_migration_hosts,
                )
            trust_imports: t.Mapping[str, t.Any] = {}
            if ordinary_migration_import_hosts:
                import_builder = getattr(
                    discovery_manager,
                    "ordinary_migration_ssh_imports",
                    None,
                )
                if not callable(import_builder):
                    raise RuntimeError("VM-HA migration cannot import ordinary managed SSH trust")
                trust_imports = import_builder(
                    plan.gateway_group,
                    ordinary_scope=_vm_ha_ssh_trust_scope(
                        local_cfg,
                        plan,
                        project_id=proj_id,
                        cluster_id="ordinary-v1",
                    ),
                    hostnames=ordinary_migration_import_hosts,
                )
            if callable(recovery_builder):
                recover_host_identities = functools.partial(
                    recovery_builder,
                    spec=plan.gateway_group,
                    ordinary_migration_hosts=ordinary_migration_hosts,
                )
            vm_ha_ssh_trust_scope = _vm_ha_ssh_trust_scope(
                local_cfg,
                plan,
                project_id=proj_id,
            )

            def resolve_ssh_policy(
                *,
                rotation_hosts: tuple[str, ...] = (),
            ) -> SSHTrustPolicy:
                return require_vm_ha_ssh_policy(
                    tuple(trust_targets),
                    enrollment_hosts=enrollment_hosts,
                    management_key_path=management_key_path,
                    management_public_key=vm_spec.get("ssh_public_key"),
                    require_management_key=True,
                    trust_scope=vm_ha_ssh_trust_scope,
                    allow_managed_repair=True,
                    persist_default_host_keys=(
                        not dry_run
                        and not rotation_hosts
                        and vm_ha_missing_standby_replacement is None
                    ),
                    additional_aliases=trust_aliases,
                    retained_hosts=retained_hosts,
                    allow_default_known_hosts_import=not dry_run,
                    default_known_hosts_bindings=trust_bindings,
                    default_known_hosts_import_hosts=(
                        set(trust_bindings) - ordinary_migration_hosts
                    ),
                    host_identity_recovery=recover_host_identities,
                    trusted_member_imports=trust_imports,
                    rotate_identity_hosts=rotation_hosts,
                )

            rotation_intent = (
                None
                if vm_ha_missing_standby_replacement is None
                else vm_ha_missing_standby_replacement.ssh_identity_rotation
            )
            if rotation_intent is None:
                try:
                    ssh_policy = resolve_ssh_policy()
                except VMHAReplacementSSHIdentityUnavailable as error:
                    if (
                        vm_ha_missing_standby_replacement is None
                        or vm_ha_missing_standby_observation is None
                        or error.rotation_intent is None
                        or error.rotation_intent.hostname
                        != vm_ha_missing_standby_replacement.target_instance_name
                    ):
                        raise
                    rotation_intent = error.rotation_intent
                    vm_ha_missing_standby_replacement = _vm_ha_missing_standby_replacement_plan(
                        plan,
                        t.cast(VMHALifecycleState, lifecycle_state),
                        vm_ha_missing_standby_observation,
                        ssh_identity_rotation=rotation_intent,
                    )
                    ssh_policy = resolve_ssh_policy(
                        rotation_hosts=(rotation_intent.hostname,),
                    )
            else:
                ssh_policy = resolve_ssh_policy(
                    rotation_hosts=(rotation_intent.hostname,),
                )
            if replace_missing_vm_ha_standby is not None:
                if vm_ha_missing_standby_replacement is None:
                    raise RuntimeError(
                        "No exact missing current non-owner is eligible for replacement"
                    )
                if (
                    replace_missing_vm_ha_standby
                    != vm_ha_missing_standby_replacement.approval_digest
                ):
                    raise RuntimeError(
                        "VM-HA missing standby replacement approval digest is stale or incorrect"
                    )
            discovery_manager.verify_vm_ha_existing_identities(
                {
                    name: address
                    for name, address in existing_members.items()
                    if vm_ha_passive_replacement is None or name != vm_ha_passive_replacement[0]
                    if (
                        vm_ha_missing_standby_replacement is None
                        or name != vm_ha_missing_standby_replacement.target_instance_name
                    )
                },
                policy=ssh_policy,
                username=(
                    vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
        except (RuntimeError, ValueError) as error:
            if _vm_ha_error_chain_has_sdk_code(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                raise
            print("[red]VM-HA SSH trust preflight failed before external mutation:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            if isinstance(error, VMHAReplacementSSHIdentityUnavailable):
                if error.problem is VMHAReplacementSSHIdentityProblem.OPERATOR_SOURCE_CONFLICT:
                    next_action = (
                        "remove VPNGW_SSH_KNOWN_HOSTS_FILE and VPNGW_SSH_HOST_KEYS_DIR "
                        "from the intended product-managed invocation, then rerun vm-ha "
                        "to resume the checkpointed SSH identity rotation"
                    )
                elif (
                    error.problem
                    is VMHAReplacementSSHIdentityProblem.MANAGED_PREDECESSOR_UNAVAILABLE
                ):
                    next_action = (
                        "restore the exact product-managed SSH trust predecessor bound to "
                        "the checkpointed rotation, then rerun vm-ha"
                    )
                else:
                    next_action = (
                        "restore the missing non-owner's original private SSH host key "
                        "matching its exact pin, then rerun vm-ha"
                    )
                planning_failure = _VMHAApplyPlanningFailed(
                    reason="replacement-ssh-identity-unavailable",
                    next_action=next_action,
                )
            elif "Ordinary gateway managed SSH receipt is unavailable" in str(error):
                planning_failure = _VMHAApplyPlanningFailed(
                    reason="ordinary-ssh-trust-required",
                    next_action=(
                        "run nebius-vpngw apply --local-config-file <ordinary-source> "
                        "to enroll or publish the retained VM trust, then rerun vm-ha"
                    ),
                )
            else:
                planning_failure = _VMHAApplyPlanningFailed(
                    reason="ssh-trust-preflight-unavailable",
                    next_action="restore exact pinned SSH trust and rerun vm-ha",
                )
            raise typer.Exit(code=1) from planning_failure
    elif needs_vm_ha_removal:
        try:
            discovery_manager = _own_vm_manager(
                VMManager(
                    project_id=proj_id,
                    region=effective_region,
                    auth_token=discovery_auth_token,
                    tenant_id=tenant_id,
                    region_id=region_id,
                    management_key_path=management_key_path,
                    management_public_key=vm_spec.get("ssh_public_key"),
                )
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
            if _vm_ha_error_chain_has_sdk_code(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                raise
            print("[red]Former VM-HA discovery failed before ordinary provisioning:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
    else:
        discovery_manager = _own_vm_manager(
            VMManager(
                project_id=proj_id,
                region=effective_region,
                auth_token=discovery_auth_token,
                tenant_id=tenant_id,
                region_id=region_id,
                management_key_path=management_key_path,
                management_public_key=vm_spec.get("ssh_public_key"),
            )
        )
        try:
            ssh_policy = discovery_manager.prepare_ordinary_ssh_policy(
                plan.gateway_group,
                planned_instances,
                trust_scope=_ordinary_ssh_trust_scope(
                    local_cfg,
                    plan,
                    project_id=proj_id,
                ),
                recreate=recreate_gw,
                management_public_key=vm_spec.get("ssh_public_key"),
                dry_run=dry_run,
                username=(
                    vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
        except LegacyOrdinarySSHEnrollmentRequired as error:
            ordinary_enrollment_required = error.hostnames
        except (RuntimeError, ValueError) as error:
            if _vm_ha_error_chain_has_sdk_code(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                raise
            print("[red]Gateway SSH trust preflight failed before cloud mutation:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            print(
                "[yellow]  - Preserve strict host verification. A genuinely absent gateway "
                "can create product-managed per-deployment trust; a present or recreated "
                "gateway requires its exact original private host identity.[/yellow]"
            )
            raise typer.Exit(code=1) from error

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

    if ordinary_enrollment_required:
        names = ", ".join(sorted(ordinary_enrollment_required))
        if dry_run:
            print(
                "[yellow]Dry-run blocked: retained ordinary gateway SSH trust requires "
                f"one-time enrollment for {names}.[/yellow]"
            )
            print(
                "[yellow]Run the same apply without --dry-run to pin the unchanged "
                "gateway's current Ed25519 host key before any host or cloud mutation.[/yellow]"
            )
            raise typer.Exit(code=1)
        if recreate_gw or has_destructive:
            print(
                "[red]One-time ordinary gateway SSH enrollment is refused when VM "
                "recreation is requested or required.[/red]"
            )
            print(
                "[yellow]Resolve the infrastructure change without replacing the retained VM.[/yellow]"
            )
            raise typer.Exit(code=1)
        print(
            "[yellow]Enrolling SSH trust for an unchanged pre-branch ordinary gateway. "
            "This one-time network observation cannot exclude an active transparent MITM.[/yellow]"
        )
        try:
            enrolled = discovery_manager.enroll_ordinary_ssh_host_keys(
                plan.gateway_group,
                ordinary_enrollment_required,
                management_public_key=vm_spec.get("ssh_public_key"),
                username=(
                    vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
            ssh_policy = discovery_manager.prepare_ordinary_ssh_policy(
                plan.gateway_group,
                planned_instances,
                trust_scope=_ordinary_ssh_trust_scope(
                    local_cfg,
                    plan,
                    project_id=proj_id,
                ),
                recreate=False,
                management_public_key=vm_spec.get("ssh_public_key"),
                dry_run=False,
                username=(
                    vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
                legacy_host_key_enrollments=enrolled,
            )
            if discovery_manager.check_changes(plan.gateway_group) != changes:
                raise RuntimeError(
                    "Gateway infrastructure changed during SSH enrollment; rerun apply"
                )
        except (RuntimeError, ValueError) as error:
            print("[red]One-time ordinary gateway SSH enrollment failed closed.[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error

    migration_plan_digest: str | None = None
    vm_ha_approval_observation: dict[str, object] | None = None
    vm_ha_approval_current_state: dict[str, object] | None = None
    vm_ha_approval_kind: str | None = None
    if plan.vm_ha is not None:
        assert vm_ha_credential_plan is not None
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
            vm_ha_credentials is not None
            and lifecycle_state is not None
            and lifecycle_state.status is VMHALifecycleStatus.ACTIVATING
            and lifecycle_state.transaction is not None
            and not _vm_ha_observation_matches_bindings(
                vm_ha_approval_observation,
                dict(lifecycle_state.transaction.resource_bindings),
                credential_bindings=vm_ha_credentials.resource_bindings(),
            )
        ):
            try:
                vm_ha_approval_current_state = _vm_ha_activation_recovery_approval_state(
                    plan,
                    lifecycle_state,
                    vm_ha_approval_observation,
                    credential_bindings=vm_ha_credentials.resource_bindings(),
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
            _vm_ha_approval_state_with_managed_credential_plan(
                vm_ha_approval_current_state,
                vm_ha_credential_plan,
            ),
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

    apply_report: _VMHAApplyPlanReport | None = None
    if plan.vm_ha is not None and migration_plan_digest is not None:
        assert vm_ha_credential_plan is not None
        managed_ssh_action = str(getattr(ssh_policy, "managed_action", "") or "").strip() or None
        plan_effects: tuple[str, ...]
        owner_refresh_required = False
        if vm_ha_missing_standby_replacement is not None:
            plan_kind = "active-standby-replacement"
            engine_digest = vm_ha_missing_standby_replacement.approval_digest
            if lifecycle_state is None or ssh_policy is None:
                raise RuntimeError("VM-HA missing standby owner capability is unavailable")
            owner_refresh_required = _vm_ha_missing_standby_owner_refresh_required(
                replacement=vm_ha_missing_standby_replacement,
                planned_instances=planned_instances,
                existing_members=vm_ha_existing_members,
                lifecycle_state=lifecycle_state,
                vm_spec=vm_spec,
                management_key_path=management_key_path,
                ssh_policy=ssh_policy,
            )
            plan_effects = (
                "create-fresh-non-owner-boot-disk-and-compute",
                "leave-all-existing-disks-untouched",
                "retain-serving-owner-allocation-and-routes",
                "resume-passive-first-activation",
                "publish-live-replacement-peer-identity",
                "install-sha256-verified-agent-artifact-on-non-owner",
            )
            if owner_refresh_required:
                plan_effects = (
                    "upgrade-and-restart-serving-owner-control-services",
                    *plan_effects,
                )
            if vm_ha_missing_standby_replacement.ssh_identity_rotation is not None:
                plan_effects = (
                    "rotate-missing-non-owner-managed-ssh-identity",
                    *plan_effects,
                )
        elif vm_ha_passive_replacement is not None:
            plan_kind = "failed-passive-replacement"
            engine_digest = vm_ha_passive_replacement[1]
            plan_effects = (
                "replace-exact-failed-passive-compute-and-disk",
                "retain-active-owner-allocation-routes-and-forwarding",
                "resume-passive-first-activation",
            )
        elif vm_ha_recovery_required or vm_ha_activation_recovery_required:
            plan_kind = "recovery"
            engine_digest = migration_plan_digest
            plan_effects = (
                "resume-exact-interrupted-vm-ha-transaction",
                "stage-and-activate-non-owner-before-owner",
                "verify-owner-routes-forwarding-and-standby",
            )
        elif vm_ha_migration_active_name is not None:
            plan_kind = "migration"
            engine_digest = migration_plan_digest
            plan_effects = tuple(
                t.cast(list[str], _vm_ha_desired_approval_state(plan)["mutations"])
            )
        elif lifecycle_state is None or lifecycle_state.status in {
            VMHALifecycleStatus.REMOVED,
            VMHALifecycleStatus.DESTROYED,
        }:
            plan_kind = "provisioning"
            engine_digest = migration_plan_digest
            plan_effects = tuple(
                t.cast(list[str], _vm_ha_desired_approval_state(plan)["mutations"])
            )
        elif lifecycle_state.status in {
            VMHALifecycleStatus.PROVISIONING,
            VMHALifecycleStatus.ACTIVATING,
        }:
            plan_kind = "resume-transaction"
            engine_digest = migration_plan_digest
            plan_effects = (
                "resume-exact-approved-vm-ha-transaction",
                "stage-and-activate-non-owner-before-owner",
                "verify-owner-routes-forwarding-and-standby",
            )
        else:
            plan_kind = "apply-convergence"
            engine_digest = migration_plan_digest
            plan_effects = (
                "stage-current-generation-non-owner-before-owner",
                "reconcile-managed-routes-through-apply-owner",
                "verify-owner-forwarding-and-warm-standby",
            )
            if replacement_policy_reproof_required:
                plan_effects = (
                    *plan_effects,
                    "reconcile-fresh-replacement-policy-with-retained-owner",
                )
        if plan_kind != "active-standby-replacement":
            plan_effects = (
                *plan_effects,
                "install-sha256-verified-agent-artifact",
                "refresh-and-restart-vm-ha-systemd-services",
            )
        if has_destructive:
            plan_effects = (*plan_effects, "recreate-gateway-compute")
        if managed_ssh_action is not None:
            plan_effects = (*plan_effects, "publish-managed-ssh-trust")
        plan_effects = (
            *plan_effects,
            f"{vm_ha_credential_plan.action}-managed-vm-ha-runtime-credential",
        )
        artifact = _resolve_vm_ha_agent_artifact(ssh_policy)
        impact = _vm_ha_apply_plan_impact(
            plan_kind,
            has_destructive_changes=has_destructive,
            owner_refresh_required=owner_refresh_required,
        )
        public_digest = _canonical_digest(
            {
                "domain": "nebius-vpngw/vm-ha-command-approval-v3",
                "engine_digest": engine_digest,
                "kind": plan_kind,
                "effects": plan_effects,
                "has_destructive_changes": has_destructive,
                "managed_ssh_action": managed_ssh_action,
                "managed_ssh_receipt_sha256": getattr(
                    ssh_policy,
                    "managed_receipt_sha256",
                    None,
                ),
                "managed_credential": vm_ha_credential_plan.approval_record(),
                "owner_refresh_required": owner_refresh_required,
                "artifact_sha256": artifact.sha256,
                "impact": impact.to_dict(),
            }
        )
        apply_report = _VMHAApplyPlanReport(
            kind=plan_kind,
            digest=public_digest,
            engine_digest=engine_digest,
            effects=plan_effects,
            has_destructive_changes=has_destructive,
            managed_ssh_action=managed_ssh_action,
            managed_credential_action=vm_ha_credential_plan.action,
            authorization_persisted=bool(
                vm_ha_missing_standby_replacement is not None
                and vm_ha_missing_standby_replacement.authorization_persisted
            ),
            owner_refresh_required=owner_refresh_required,
            artifact_sha256=artifact.sha256,
            artifact=artifact,
            impact=impact,
        )
        with _vm_ha_progress_step(
            vm_ha_progress_sink,
            _VMHAProgressPhase.VERIFY_ENGINE_PLAN,
        ):
            _validate_vm_ha_expected_apply_plan(apply_report, expected_vm_ha_plan)
        if vm_ha_plan_sink is not None:
            vm_ha_plan_sink(apply_report)
        if stop_after_vm_ha_plan:
            raise _VMHAApplyPlanCaptured(apply_report)
    else:
        with _vm_ha_progress_step(
            vm_ha_progress_sink,
            _VMHAProgressPhase.VERIFY_ENGINE_PLAN,
        ):
            _validate_vm_ha_expected_apply_plan(None, expected_vm_ha_plan)

    if dry_run:
        if ssh_policy is not None and getattr(ssh_policy, "managed_action", None):
            print(
                "[dim]Dry-run: apply would "
                f"{ssh_policy.managed_action} the per-deployment SSH trust store.[/dim]"
            )
        if prepare_vm_ha_peer_rotation:
            print(
                "[dim]Dry-run: apply would stop after exact-generation activation "
                "with both VM-HA members passively fenced and locked.[/dim]"
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
            replace_missing_vm_ha_standby,
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
    if vm_ha_missing_standby_replacement is not None:
        if (
            not vm_ha_missing_standby_replacement.authorization_persisted
            and replace_missing_vm_ha_standby != vm_ha_missing_standby_replacement.approval_digest
        ):
            refusal, next_action = _missing_standby_apply_approval_lines(local_config_file)
            print(f"[red]{refusal}[/red]")
            print(f"[yellow]{next_action}[/yellow]")
            raise typer.Exit(code=1)
        if (
            replace_missing_vm_ha_standby is not None
            and replace_missing_vm_ha_standby != vm_ha_missing_standby_replacement.approval_digest
        ):
            print("[red]Missing standby replacement approval became stale.[/red]")
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

    managed_vm_ha_auth_token: str | None = None
    if plan.vm_ha is not None:
        assert vm_ha_credential_plan is not None
        if (
            vm_ha_credential_plan.credentials is None
            and expected_vm_ha_plan is None
            and vm_ha_migration_active_name is None
            and not typer.confirm(
                "Proceed with this exact VM-HA plan, including managed runtime credential enrollment?",
                default=False,
            )
        ):
            print("[green]Aborted. No VM-HA credentials or infrastructure were changed.[/green]")
            raise typer.Exit(code=0)
        print(
            f"[bold]{vm_ha_credential_plan.action.title()} managed VM-HA runtime "
            "credentials...[/bold]"
        )
        try:
            managed_result = ensure_managed_vm_ha_credentials(
                vm_ha_credential_plan,
                node_ids=vm_ha_node_ids,
                tenant_id=tenant_id,
                region_id=region_id,
            )
        except (VMHACredentialIdentityError, VMHAManagedCredentialError, RuntimeError) as error:
            reason = getattr(error, "reason", str(error))
            print("[red]Managed VM-HA runtime credential reconciliation failed.[/red]")
            print(f"[yellow]  - {reason}[/yellow]")
            raise typer.Exit(code=1) from error
        vm_ha_credentials = managed_result.credentials
        managed_vm_ha_auth_token = managed_result.token_identity.token
        print("[green]✓ Managed VM-HA runtime credentials are ready[/green]")

    if (
        ssh_policy is not None
        and getattr(ssh_policy, "managed_action", None)
        and ssh_policy.managed_action != "rotate"
    ):
        try:
            publish_vm_ha_ssh_trust(ssh_policy)
        except (OSError, RuntimeError, ValueError) as error:
            print("[red]Managed SSH trust publication failed before cloud mutation:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
        print(f"[green]Per-deployment SSH trust {ssh_policy.managed_action} completed.[/green]")

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
            if _vm_ha_error_chain_has_sdk_code(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                raise
            print("[red]Former VM-HA teardown failed before ordinary provisioning:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error

    activating_resume = False
    if plan.vm_ha is not None:
        _emit_vm_ha_progress(
            vm_ha_progress_sink,
            _VMHAProgressPhase.PREPARE_TRANSACTION,
            _VMHAProgressState.STARTED,
        )
        assert vm_ha_credentials is not None
        assert vm_ha_credential_plan is not None
        assert vm_ha_approval_observation is not None
        assert vm_ha_approval_kind is not None
        assert migration_plan_digest is not None
        observer = getattr(discovery_manager, "observe_vm_ha_migration_state", None)
        fresh_observation = (
            observer(plan.gateway_group, plan.gateway.get("local_prefixes"))
            if callable(observer)
            else vm_ha_approval_observation
        )
        if replace_missing_vm_ha_standby is not None:
            if lifecycle_state is None:
                print("[red]Missing standby replacement has no lifecycle authority.[/red]")
                raise typer.Exit(code=1)
            try:
                fresh_missing = _vm_ha_missing_standby_replacement_plan(
                    plan,
                    lifecycle_state,
                    fresh_observation,
                    ssh_identity_rotation=(
                        None
                        if vm_ha_missing_standby_replacement is None
                        else vm_ha_missing_standby_replacement.ssh_identity_rotation
                    ),
                )
            except ValueError as error:
                print(f"[red]Missing standby replacement became unsafe: {error}[/red]")
                raise typer.Exit(code=1) from error
            if fresh_missing.approval_digest != replace_missing_vm_ha_standby:
                print("[red]Missing standby replacement approval became stale.[/red]")
                raise typer.Exit(code=1)
            if not fresh_missing.authorization_persisted:
                desired_digest = _canonical_digest(_vm_ha_desired_approval_state(plan))
                replacement_state = VMHALifecycleState.start_missing_standby_replacement(
                    lifecycle_state,
                    target_instance_name=fresh_missing.target_instance_name,
                    replacement_cycle=fresh_missing.replacement_cycle,
                    replacement_disk_name=fresh_missing.replacement_disk_name,
                    operation_id=fresh_missing.operation_id,
                    approval_digest=fresh_missing.approval_digest,
                    desired_state_digest=desired_digest,
                    current_state_digest=_canonical_digest(
                        _vm_ha_approval_state_with_credentials(
                            fresh_observation,
                            vm_ha_credentials,
                        )
                    ),
                    current_observation=fresh_observation,
                    ssh_identity_rotation=(
                        None
                        if fresh_missing.ssh_identity_rotation is None
                        else fresh_missing.ssh_identity_rotation.approval_state()
                    ),
                )
                lifecycle_store.write_verified(
                    replacement_state,
                    predecessor_sha256=lifecycle_state.record_sha256,
                )
                lifecycle_state = replacement_state
                vm_ha_missing_standby_replacement = replace(
                    fresh_missing,
                    authorization_persisted=True,
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
        initial_bindings = _vm_ha_initial_resource_bindings(
            fresh_observation,
            credential_bindings=vm_ha_credentials.resource_bindings(),
        )
        if lifecycle_state is not None and lifecycle_state.status is VMHALifecycleStatus.DESTROYED:
            retained_public = vm_ha_destroyed_retained_public_bindings(lifecycle_state)
            if any(
                key in initial_bindings and initial_bindings[key] != value
                for key, value in retained_public.items()
            ):
                raise ValueError("VM-HA retained public allocation observation changed")
            initial_bindings.update(retained_public)
        if vm_ha_activation_recovery_required:
            if lifecycle_state is None:
                print("[red]Interrupted VM-HA activation has no lifecycle checkpoint.[/red]")
                raise typer.Exit(code=1)
            try:
                fresh_recovery_state = _vm_ha_activation_recovery_approval_state(
                    plan,
                    lifecycle_state,
                    fresh_observation,
                    credential_bindings=vm_ha_credentials.resource_bindings(),
                )
            except ValueError as error:
                print(f"[red]VM-HA activation recovery became unsafe: {error}[/red]")
                raise typer.Exit(code=1) from error
            fresh_digest = _vm_ha_migration_plan_digest(
                plan,
                _vm_ha_approval_state_with_managed_credential_plan(
                    fresh_recovery_state,
                    vm_ha_credential_plan,
                ),
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
            if (
                vm_ha_missing_standby_replacement is None
                and not _vm_ha_observation_matches_bindings(
                    fresh_observation,
                    dict(lifecycle_state.transaction.resource_bindings),
                    credential_bindings=vm_ha_credentials.resource_bindings(),
                )
            ):
                print("[red]VM-HA authoritative cloud identity drifted from the checkpoint.[/red]")
                raise typer.Exit(code=1)
            activating_resume = lifecycle_state.status is VMHALifecycleStatus.ACTIVATING
        else:
            fresh_digest = _vm_ha_migration_plan_digest(
                plan,
                _vm_ha_approval_state_with_managed_credential_plan(
                    fresh_observation,
                    vm_ha_credential_plan,
                ),
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
                VMHALifecycleStatus.DESTROYED,
            }:
                operation_identity["predecessor_sha256"] = lifecycle_state.record_sha256
            operation_id = _canonical_digest(operation_identity)
            current_digest = _canonical_digest(
                _vm_ha_approval_state_with_credentials(
                    fresh_observation,
                    vm_ha_credentials,
                )
            )
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
        if (
            vm_ha_missing_standby_replacement is not None
            and vm_ha_missing_standby_replacement.ssh_identity_rotation is not None
        ):
            if vm_ha_ssh_trust_scope is None:
                raise RuntimeError("VM-HA replacement SSH rotation lost its trust scope")
            replacement = vm_ha_missing_standby_replacement
            rotation = t.cast(
                VMHASSHIdentityRotationIntent,
                replacement.ssh_identity_rotation,
            )
            transaction = lifecycle_journal.state.transaction
            assert transaction is not None
            bindings = dict(transaction.resource_bindings)

            def rotation_binding(kind: str) -> str | None:
                return bindings.get(
                    vm_ha_missing_standby_ssh_binding_key(
                        kind,
                        replacement.target_instance_name,
                        replacement.replacement_cycle,
                    )
                )

            expected_new_fingerprint = rotation_binding("new-fingerprint")
            expected_successor_receipt = rotation_binding("successor-receipt")
            expected_successor_projection = rotation_binding("successor-projection")
            stage_effect = vm_ha_missing_standby_replacement_effect(
                replacement.target_instance_name,
                replacement.replacement_cycle,
                "stage-ssh-identity",
            )
            validate_vm_ha_ssh_identity_rotation(
                rotation,
                trust_scope=vm_ha_ssh_trust_scope,
                expected_successor_receipt_sha256=expected_successor_receipt,
                expected_successor_projection_sha256=expected_successor_projection,
            )
            lifecycle_journal.begin(stage_effect)
            stage = prepare_vm_ha_ssh_identity_rotation(
                rotation,
                operation_id=replacement.operation_id,
                trust_scope=vm_ha_ssh_trust_scope,
                hosts=tuple(trust_targets),
                additional_aliases=trust_aliases,
                expected_new_fingerprint=expected_new_fingerprint,
                expected_successor_receipt_sha256=expected_successor_receipt,
                expected_successor_projection_sha256=expected_successor_projection,
            )
            lifecycle_journal.complete(
                stage_effect,
                resource_updates={
                    vm_ha_missing_standby_ssh_binding_key(
                        "stage-token",
                        replacement.target_instance_name,
                        replacement.replacement_cycle,
                    ): stage.stage_token,
                    vm_ha_missing_standby_ssh_binding_key(
                        "new-fingerprint",
                        replacement.target_instance_name,
                        replacement.replacement_cycle,
                    ): stage.new_fingerprint,
                    vm_ha_missing_standby_ssh_binding_key(
                        "successor-receipt",
                        replacement.target_instance_name,
                        replacement.replacement_cycle,
                    ): stage.successor_receipt_sha256,
                    vm_ha_missing_standby_ssh_binding_key(
                        "successor-projection",
                        replacement.target_instance_name,
                        replacement.replacement_cycle,
                    ): stage.successor_projection_sha256,
                },
            )
            publish_effect = vm_ha_missing_standby_replacement_effect(
                replacement.target_instance_name,
                replacement.replacement_cycle,
                "publish-ssh-trust",
            )
            lifecycle_journal.begin(publish_effect)
            publish_vm_ha_ssh_identity_rotation(
                rotation,
                stage,
                operation_id=replacement.operation_id,
                trust_scope=vm_ha_ssh_trust_scope,
            )
            lifecycle_journal.complete(publish_effect)
            lifecycle_state = lifecycle_journal.state
            ssh_policy = resolve_ssh_policy()
            ssh_policy.identity_for(replacement.target_instance_name)
        _emit_vm_ha_progress(
            vm_ha_progress_sink,
            _VMHAProgressPhase.PREPARE_TRANSACTION,
            _VMHAProgressState.COMPLETED,
        )

    # Optional Service Account provisioning/auth. Every ordinary --sa path, including
    # lifecycle-bound removal, selected this token before its first cloud read.
    auth_token = managed_vm_ha_auth_token or discovery_auth_token
    if sa and not service_account_selected:
        with _vm_ha_progress_step(
            vm_ha_progress_sink,
            _VMHAProgressPhase.PREPARE_SERVICE_ACCOUNT,
        ):
            if lifecycle_journal is not None:
                lifecycle_journal.begin("prepare-service-account")
            auth_token = _requested_apply_service_account_token(
                sa_name=sa,
                tenant_id=tenant_id,
                project_id=proj_id,
                region_id=region_id,
                vm_ha_enabled=plan.vm_ha is not None,
                expected_service_account_id=(
                    vm_ha_credentials.service_account_id if vm_ha_credentials is not None else None
                ),
            )
            if lifecycle_journal is not None:
                lifecycle_journal.complete("prepare-service-account")
    else:
        if service_account_selected:
            print(
                "[green]Using the short-lived token for the requested Nebius "
                "Service Account.[/green]"
            )
        elif auth_token is not None:
            print("[green]Using the explicitly supplied Nebius IAM token.[/green]")
        else:
            print("[green]Using renewable credentials from the Nebius CLI.[/green]")

    vm_mgr = _own_vm_manager(
        VMManager(
            project_id=proj_id,
            region=effective_region,
            auth_token=auth_token,
            tenant_id=tenant_id,
            region_id=region_id,
            ssh_policy=ssh_policy,
            management_key_path=management_key_path,
            management_public_key=vm_spec.get("ssh_public_key"),
            vm_ha_credentials=vm_ha_credentials,
        )
    )
    if lifecycle_journal is not None:
        setter = getattr(vm_mgr, "set_vm_ha_lifecycle_journal", None)
        if not callable(setter):
            raise RuntimeError("VM-HA manager has no lifecycle journal interface")
        setter(lifecycle_journal)
    ssh_client_auth = _gateway_ssh_client_auth(local_cfg) if plan.vm_ha is not None else None
    ssh = SSHPush(ssh_policy=ssh_policy)
    standby_replacement_inhibition: dict[str, t.Any] | None = None

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

    with _vm_ha_progress_step(
        vm_ha_progress_sink,
        _VMHAProgressPhase.RECONCILE_COMPUTE,
    ):
        replacement_provisioning: t.Any | None = None
        if vm_ha_missing_standby_replacement is not None:
            if lifecycle_journal is None or lifecycle_journal.state.transaction is None:
                raise RuntimeError("VM-HA missing standby replacement lost its transaction")
            (
                standby_replacement_inhibition,
                replacement_provisioning,
            ) = _create_missing_vm_ha_standby_under_owner_inhibition(
                plan=plan,
                planned_instances=planned_instances,
                existing_members=vm_ha_existing_members,
                local_config=local_cfg,
                apply_report=apply_report,
                lifecycle_journal=lifecycle_journal,
                vm_manager=vm_mgr,
                ssh=ssh,
                replacement=vm_ha_missing_standby_replacement,
            )
        if replace_failed_vm_ha_passive is not None:
            replace_passive = getattr(vm_mgr, "replace_failed_vm_ha_passive", None)
            if not callable(replace_passive):
                raise RuntimeError("VM-HA manager has no failed-passive replacement interface")
            replace_passive(
                plan.gateway_group,
                plan.gateway.get("local_prefixes"),
                approval_digest=replace_failed_vm_ha_passive,
            )

        if replacement_provisioning is not None:
            vm_ips = replacement_provisioning
        elif activating_resume:
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

    if plan.vm_ha is not None:
        if vm_ha_ssh_trust_scope is None:
            raise RuntimeError("VM-HA SSH trust scope was lost after Compute reconciliation")
        ssh_policy = _refresh_vm_ha_ssh_policy_after_compute(
            plan=plan,
            vm_manager=vm_mgr,
            vm_ips=vm_ips,
            trust_scope=vm_ha_ssh_trust_scope,
            management_key_path=management_key_path,
            management_public_key=vm_spec.get("ssh_public_key"),
            ordinary_migration_hosts=ordinary_migration_hosts,
            lifecycle_snapshot_loader=lifecycle_snapshot_loader,
        )
        ssh = SSHPush(ssh_policy=ssh_policy)

    # Wait for VMs to be network-reachable and verify bootstrap
    if vm_ips:
        health_username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        print("[bold]Waiting for VMs to become reachable...[/bold]")
        _emit_vm_ha_progress(
            vm_ha_progress_sink,
            _VMHAProgressPhase.WAIT_COMPUTE,
            _VMHAProgressState.STARTED,
        )
        wait_compute_progress = _VMHAProgressWait(
            vm_ha_progress_sink,
            _VMHAProgressPhase.WAIT_COMPUTE,
        )
        all_reachable = True
        for vm_name, vm_ip in vm_ips.items():
            if vm_ha_progress_sink is None:
                reachable = vm_mgr.wait_for_vm_network(vm_name, vm_ip, timeout=180)
            else:
                reachable = vm_mgr.wait_for_vm_network(
                    vm_name,
                    vm_ip,
                    timeout=180,
                    progress_callback=wait_compute_progress.update,
                )
            if not reachable:
                all_reachable = False

        if all_reachable:
            _emit_vm_ha_progress(
                vm_ha_progress_sink,
                _VMHAProgressPhase.WAIT_COMPUTE,
                _VMHAProgressState.COMPLETED,
            )
            print("[bold]Verifying VM bootstrap and package installation...[/bold]")
            _emit_vm_ha_progress(
                vm_ha_progress_sink,
                _VMHAProgressPhase.WAIT_BOOTSTRAP,
                _VMHAProgressState.STARTED,
            )
            all_healthy = True
            for vm_name, vm_ip in vm_ips.items():
                health = vm_mgr.check_vm_health(
                    vm_name,
                    vm_ip,
                    username=health_username,
                )
                if _vm_ready_for_config_push(health) and _vm_packages_verified(health):
                    print(f"[green]{vm_name} ({vm_ip}): {health['message']}[/green]")
                elif health["reachable"]:
                    print(f"[yellow]{vm_name} ({vm_ip}): {health['message']}[/yellow]")
                    all_healthy = False
                else:
                    print(f"{vm_name} ({vm_ip}): {health['message']}")
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
                wait_progress = _VMHAProgressWait(
                    vm_ha_progress_sink,
                    _VMHAProgressPhase.WAIT_BOOTSTRAP,
                )
                for attempt in range(max_wait // wait_interval):
                    time.sleep(wait_interval)
                    wait_elapsed = (attempt + 1) * wait_interval
                    wait_progress.update()
                    all_ready = True
                    packages_verified = True
                    for vm_name, vm_ip in vm_ips.items():
                        health = vm_mgr.check_vm_health(
                            vm_name,
                            vm_ip,
                            username=health_username,
                        )
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
                    if plan.vm_ha is not None and lifecycle_journal is not None:
                        final_ready: dict[str, bool] = {}
                        for vm_name, vm_ip in vm_ips.items():
                            final_health = vm_mgr.check_vm_health(
                                vm_name,
                                vm_ip,
                                username=health_username,
                            )
                            final_ready[vm_name] = _vm_ready_for_config_push(final_health)
                        passive_name = next(
                            f"{plan.gateway_group.name}-{member.instance_index}"
                            for member in plan.vm_ha.members
                            if member.role.value == "passive"
                        )
                        if final_ready.get(passive_name) is False and all(
                            ready for name, ready in final_ready.items() if name != passive_name
                        ):
                            transaction = lifecycle_journal.state.transaction
                            assert transaction is not None
                            cycles = vm_ha_passive_replacement_cycles(
                                dict(transaction.resource_bindings),
                                passive_name,
                            )
                            failure_effect = _vm_ha_failed_passive_bootstrap_effect(
                                passive_name,
                                (cycles[-1] + 1) if cycles else 1,
                            )
                            lifecycle_journal.begin(failure_effect)
                            lifecycle_journal.complete(failure_effect)
                    print(
                        "[red]VM bootstrap did not become ready for config push within timeout.[/red]"
                    )
                    print(
                        "[yellow]Rerun apply after cloud-init and any ESP4/kernel reboot finish.[/yellow]"
                    )
                    raise typer.Exit(code=1)
            _emit_vm_ha_progress(
                vm_ha_progress_sink,
                _VMHAProgressPhase.WAIT_BOOTSTRAP,
                _VMHAProgressState.COMPLETED,
            )
        else:
            _emit_vm_ha_progress(
                vm_ha_progress_sink,
                _VMHAProgressPhase.WAIT_COMPUTE,
                _VMHAProgressState.FAILED,
            )
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
        assert vm_ha_credential_plan is not None
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
            with _vm_ha_progress_step(
                vm_ha_progress_sink,
                _VMHAProgressPhase.BIND_MEMBERS,
            ):
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
        replacement_release_started = False
        replacement_release_effect: str | None = None
        if (
            vm_ha_missing_standby_replacement is not None
            and lifecycle_journal.state.transaction is not None
        ):
            replacement_release_effect = (
                f"release-standby-replacement-inhibition-{current_owner_node_id}"
            )
            replacement_release_started = bool(
                replacement_release_effect in lifecycle_journal.state.transaction.completed_effects
                or lifecycle_journal.state.transaction.pending_effect == replacement_release_effect
            )
        if replacement_release_started:
            username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
            owner_cfg = next(
                instance
                for instance in ordered_instances
                if instance.vm_ha_node.node_id == current_owner_node_id
            )
            owner_target = lifecycle_targets[owner_cfg.hostname]
            owner_generation = owner_cfg.vm_ha_generation
            replacement_transaction = lifecycle_journal.state.transaction
            if (
                owner_generation is None
                or replacement_release_effect is None
                or replacement_transaction is None
            ):
                raise RuntimeError("VM-HA standby replacement release identity is incomplete")
            release_inhibition = {
                "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
                "cluster_id": vm_ha_runtime_binding.cluster_id,
                "node_id": current_owner_node_id,
                "generation_id": owner_generation.generation_id,
                "operation_id": replacement_transaction.operation_id,
            }
            _release_missing_vm_ha_standby_inhibition(
                lifecycle_journal=lifecycle_journal,
                ssh=ssh,
                owner_target=owner_target,
                owner_config=owner_cfg,
                local_config=local_cfg,
                inhibition=release_inhibition,
                effect=replacement_release_effect,
            )
            statuses: dict[str, dict[str, t.Any]] = {}
            for inst_cfg in ordered_instances:
                target = lifecycle_targets[inst_cfg.hostname]
                node_id = inst_cfg.vm_ha_node.node_id
                owner = node_id == current_owner_node_id

                def terminal_replacement_status(
                    payload: dict[str, t.Any],
                    *,
                    expected_owner: bool = owner,
                ) -> bool:
                    mtls = payload.get("mtls")
                    return bool(
                        payload.get("data_plane_mode")
                        == ("active" if expected_owner else "passive")
                        and payload.get("promotion_ready") is expected_owner
                        and payload.get("observed_owner_node_id") == current_owner_node_id
                        and payload.get("pending_operation_id") is None
                        and payload.get("transfer_inhibition_operation_id") is None
                        and isinstance(mtls, dict)
                        and mtls.get("state") == "healthy"
                        and mtls.get("operation_id") is None
                        and mtls.get("inhibited") is False
                        and (
                            not expected_owner
                            or _vm_ha_active_route_receipt_matches(
                                payload,
                                active_node_id=current_owner_node_id,
                                runtime_binding=vm_ha_runtime_binding,
                            )
                        )
                    )

                statuses[node_id] = _wait_for_vm_ha_agent_status(
                    predicate=terminal_replacement_status,
                    target=target,
                    hostname=inst_cfg.hostname,
                    username=username,
                    key_path=management_key_path,
                    client_auth=ssh_client_auth,
                    ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                    inst_cfg=inst_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    expected_apply_locked=False,
                    expected_operation_id=(
                        lifecycle_journal.state.transaction.operation_id
                        if lifecycle_journal.state.transaction is not None
                        else None
                    ),
                )
            if set(statuses) != {node.node_id for node in vm_ha_runtime_binding.nodes}:
                raise RuntimeError("VM-HA standby replacement terminal status is incomplete")
            _commit_missing_vm_ha_standby_replacement_active(lifecycle_journal)
            print("[green]Apply completed successfully.[/green]")
            return
        print("[bold]Staging VM-HA configs non-owner-first without activation...[/bold]")
        assert vm_ha_credential_plan is not None
        staged: list[tuple[t.Any, str, t.Any]] = []
        for inst_cfg in ordered_instances:
            target = _config_target(inst_cfg)
            if not target:
                print(f"[red]Cannot stage VM-HA node {inst_cfg.hostname}: no SSH target[/red]")
                print(
                    "[yellow]No staged node was activated; rerun apply after SSH is ready.[/yellow]"
                )
                raise typer.Exit(code=1)
            stage_phase = (
                _VMHAProgressPhase.STAGE_OWNER
                if inst_cfg.vm_ha_node.node_id == current_owner_node_id
                else _VMHAProgressPhase.STAGE_STANDBY
            )
            with _vm_ha_progress_step(vm_ha_progress_sink, stage_phase):
                stage_effect = f"stage-{inst_cfg.vm_ha_node.node_id}"
                lifecycle_journal.begin(stage_effect)
                receipt = ssh.stage_vm_ha_config(
                    target,
                    inst_cfg,
                    local_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    nebius_credentials_path=vm_ha_credential_plan.source_path,
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

        print("[bold]Preparing exact VM-HA agent packages non-owner-first...[/bold]")
        if apply_report is None or apply_report.artifact is None:
            raise RuntimeError("VM-HA apply has no approved agent artifact")
        approved_agent_artifact = apply_report.artifact
        try:
            with _vm_ha_progress_step(
                vm_ha_progress_sink,
                _VMHAProgressPhase.PREPARE_AGENT_PACKAGES,
            ):
                for inst_cfg, target, _receipt in staged:
                    if (
                        vm_ha_missing_standby_replacement is not None
                        and inst_cfg.vm_ha_node.node_id == current_owner_node_id
                    ):
                        continue
                    ssh.ensure_vm_ha_agent_package(
                        target,
                        inst_cfg,
                        local_cfg,
                        artifact=approved_agent_artifact,
                    )
                    print(f"[green]✓ Prepared {inst_cfg.vm_ha_node.node_id} agent package[/green]")
        except (OSError, RuntimeError, ValueError) as error:
            print(
                "[red]VM-HA agent package preparation failed; no apply-lock installation "
                "was attempted and any pre-existing locks were preserved.[/red]"
            )
            raise typer.Exit(code=1) from error

        activation_transaction = lifecycle_journal.state.transaction
        assert activation_transaction is not None
        transaction_bindings = dict(activation_transaction.resource_bindings)
        operation_id = (
            activation_transaction.operation_id
            if any(key.startswith("standby-replacement-") for key in transaction_bindings)
            else _vm_ha_apply_operation_id(vm_ha_runtime_binding)
        )

        def reconcile_replacement_policy(replacement_node_id: str) -> None:
            effect = f"reconcile-replacement-policy-{replacement_node_id}"
            current_transaction = lifecycle_journal.state.transaction
            if current_transaction is None:
                raise RuntimeError("replacement policy reconciliation lost its transaction")
            if effect in current_transaction.completed_effects:
                return
            if current_transaction.pending_effect != effect:
                lifecycle_journal.begin(effect)
            _reconcile_vm_ha_replacement_auto_healing_policy(
                config_path=local_config_file,
                owner_node_id=current_owner_node_id,
            )
            lifecycle_journal.complete(effect)

        print("[bold]Installing exact-generation VM-HA apply locks non-owner-first...[/bold]")
        locked: list[tuple[t.Any, str, t.Any, t.Any]] = []
        try:
            for inst_cfg, target, receipt in staged:
                if (
                    vm_ha_missing_standby_replacement is not None
                    and receipt.node_id == current_owner_node_id
                ):
                    if standby_replacement_inhibition is None:
                        raise RuntimeError("VM-HA standby replacement owner inhibition was lost")
                    continue
                lock_phase = (
                    _VMHAProgressPhase.LOCK_OWNER
                    if receipt.node_id == current_owner_node_id
                    else _VMHAProgressPhase.LOCK_STANDBY
                )
                with _vm_ha_progress_step(vm_ha_progress_sink, lock_phase):
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

        if vm_ha_missing_standby_replacement is None:
            try:
                owner_locked_entry = next(
                    item for item in locked if item[0].vm_ha_node.node_id == current_owner_node_id
                )
                owner_cfg, owner_target, _owner_stage, owner_lock_receipt = owner_locked_entry
                adoption_effect = f"install-owner-adoption-{current_owner_node_id}"
                with _vm_ha_progress_step(
                    vm_ha_progress_sink,
                    _VMHAProgressPhase.DECLARE_OWNER,
                ):
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
            with _vm_ha_progress_step(
                vm_ha_progress_sink,
                _VMHAProgressPhase.PREPARE_MTLS,
            ):
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

        replacement_policy_request = (
            _vm_ha_replacement_policy_adoption_request(
                config_path=local_config_file,
                owner_node_id=current_owner_node_id,
                apply_operation_id=operation_id,
                mtls_apply_operation_id=mtls_transaction.operation_id,
                mtls_inhibition_operation_id=None,
            )
            if vm_ha_missing_standby_replacement is not None or replacement_policy_reproof_required
            else None
        )

        if lifecycle_state is None:
            raise RuntimeError("VM-HA activation has no durable lifecycle identity")
        try:
            print("[bold]Activating verified VM-HA configs non-owner-first...[/bold]")
            activation_entries = locked
            for inst_cfg, target, receipt, _lock_receipt in activation_entries:
                activation_phase = (
                    _VMHAProgressPhase.RELOAD_OWNER_SERVICES
                    if receipt.node_id == current_owner_node_id
                    else _VMHAProgressPhase.RELOAD_STANDBY_SERVICES
                )
                with _vm_ha_progress_step(vm_ha_progress_sink, activation_phase):
                    activation_effect = f"activate-{receipt.node_id}"
                    lifecycle_journal.begin(activation_effect)
                    activation_kwargs: dict[str, t.Any] = {
                        "agent_artifact": approved_agent_artifact,
                        "staged_receipt": receipt,
                        "runtime_binding": vm_ha_runtime_binding,
                    }
                    if (
                        replacement_policy_request is not None
                        and receipt.node_id != current_owner_node_id
                    ):
                        activation_kwargs["replacement_policy_request"] = replacement_policy_request
                    ssh.push_config_and_reload(
                        target,
                        inst_cfg,
                        local_cfg,
                        **activation_kwargs,
                    )
                    lifecycle_journal.complete(activation_effect)
                print(f"[green]✓ Activated {receipt.node_id}[/green]")

            username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
            print(
                "[bold]Verifying both activated nodes remain fenced on the exact operation...[/bold]"
            )
            activated_agent_statuses: dict[str, dict[str, t.Any]] = {}
            with _vm_ha_progress_step(
                vm_ha_progress_sink,
                _VMHAProgressPhase.VERIFY_FENCED,
            ):
                wait_fenced_progress = _VMHAProgressWait(
                    vm_ha_progress_sink,
                    _VMHAProgressPhase.VERIFY_FENCED,
                )
                for inst_cfg, target, _receipt, _lock_receipt in activation_entries:
                    node_id = inst_cfg.vm_ha_node.node_id
                    owner_replacement_entry = bool(
                        vm_ha_missing_standby_replacement is not None
                        and node_id == current_owner_node_id
                    )

                    def activated_status(
                        payload: dict[str, t.Any],
                        *,
                        owner: bool = owner_replacement_entry,
                    ) -> bool:
                        return bool(
                            payload.get("data_plane_mode") == ("active" if owner else "passive")
                            and payload.get("promotion_ready") is owner
                            and (
                                not owner
                                or payload.get("observed_owner_node_id") == current_owner_node_id
                                and payload.get("transfer_inhibition_operation_id") == operation_id
                                and payload.get("transfer_inhibition_quiescent") is True
                            )
                            and _vm_ha_mtls_agent_evidence_matches(
                                mtls_transaction,
                                str(payload.get("node_id") or ""),
                                payload,
                            )
                        )

                    activated_agent_statuses[node_id] = _wait_for_vm_ha_agent_status(
                        predicate=activated_status,
                        target=target,
                        hostname=inst_cfg.hostname,
                        username=username,
                        key_path=management_key_path,
                        client_auth=ssh_client_auth,
                        ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                        inst_cfg=inst_cfg,
                        runtime_binding=vm_ha_runtime_binding,
                        expected_apply_locked=not owner_replacement_entry,
                        expected_operation_id=operation_id,
                        progress_callback=wait_fenced_progress.update,
                    )
                if vm_ha_missing_standby_replacement is not None:
                    owner_cfg, owner_target, _owner_receipt = next(
                        item
                        for item in staged
                        if item[0].vm_ha_node.node_id == current_owner_node_id
                    )
                    activated_agent_statuses[current_owner_node_id] = _wait_for_vm_ha_agent_status(
                        predicate=lambda payload: bool(
                            payload.get("data_plane_mode") == "active"
                            and payload.get("promotion_ready") is True
                            and payload.get("observed_owner_node_id") == current_owner_node_id
                            and payload.get("transfer_inhibition_operation_id") == operation_id
                            and payload.get("transfer_inhibition_quiescent") is True
                            and _vm_ha_mtls_agent_evidence_matches(
                                mtls_transaction,
                                current_owner_node_id,
                                payload,
                            )
                        ),
                        target=owner_target,
                        hostname=owner_cfg.hostname,
                        username=username,
                        key_path=management_key_path,
                        client_auth=ssh_client_auth,
                        ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                        inst_cfg=owner_cfg,
                        runtime_binding=vm_ha_runtime_binding,
                        expected_apply_locked=False,
                        expected_operation_id=operation_id,
                        progress_callback=wait_fenced_progress.update,
                    )

            with _vm_ha_progress_step(
                vm_ha_progress_sink,
                _VMHAProgressPhase.COMMIT_MTLS,
            ):
                _finalize_vm_ha_managed_mtls(
                    ssh=ssh,
                    transaction=mtls_transaction,
                    local_cfg=local_cfg,
                    agent_statuses=activated_agent_statuses,
                )
            if mtls_transaction.changed:
                print("[green]✓ Managed mTLS committed after fresh bidirectional proof[/green]")

            if vm_ha_missing_standby_replacement is not None:
                if standby_replacement_inhibition is None:
                    raise RuntimeError(
                        "VM-HA standby replacement inhibition was lost before peer publication"
                    )
                owner_cfg, owner_target, _owner_stage = next(
                    item for item in staged if item[0].vm_ha_node.node_id == current_owner_node_id
                )
                peer_binding_effect = (
                    f"publish-live-replacement-peer-identity-{current_owner_node_id}"
                )
                transaction = lifecycle_journal.state.transaction
                if transaction is None or peer_binding_effect not in transaction.completed_effects:
                    lifecycle_journal.begin(peer_binding_effect)
                    ssh.commit_vm_ha_standby_replacement_peer_binding(
                        owner_target,
                        owner_cfg.hostname,
                        local_cfg,
                        inhibition=standby_replacement_inhibition,
                    )
                    lifecycle_journal.complete(peer_binding_effect)

            if prepare_vm_ha_peer_rotation:
                print("[green]VM-HA peer-rotation preparation completed successfully.[/green]")
                print(
                    "[yellow]Both members remain passively fenced under the exact-generation "
                    "apply locks. Run the explicitly authorized peer rotation with this "
                    "same private config, then rerun ordinary apply.[/yellow]"
                )
                return

            if vm_ha_missing_standby_replacement is not None:
                if standby_replacement_inhibition is None:
                    raise RuntimeError(
                        "VM-HA standby replacement inhibition was lost before release"
                    )
                owner_entry = next(
                    item for item in staged if item[0].vm_ha_node.node_id == current_owner_node_id
                )
                passive_entry = next(
                    item for item in locked if item[0].vm_ha_node.node_id != current_owner_node_id
                )
                owner_cfg, owner_target, _owner_stage = owner_entry
                passive_cfg, passive_target, _passive_stage, passive_lock = passive_entry
                ssh.verify_vm_ha_standby_replacement_quiescent(
                    owner_target,
                    owner_cfg.hostname,
                    local_cfg,
                    inhibition=standby_replacement_inhibition,
                )
                _wait_for_vm_ha_agent_status(
                    predicate=lambda payload: (
                        payload.get("data_plane_mode") == "active"
                        and payload.get("promotion_ready") is True
                        and payload.get("observed_owner_node_id") == current_owner_node_id
                        and payload.get("transfer_inhibition_operation_id") == operation_id
                        and payload.get("transfer_inhibition_quiescent") is True
                        and payload.get("pending_operation_id") is None
                        and _vm_ha_active_route_receipt_matches(
                            payload,
                            active_node_id=current_owner_node_id,
                            runtime_binding=vm_ha_runtime_binding,
                        )
                    ),
                    target=owner_target,
                    hostname=owner_cfg.hostname,
                    username=username,
                    key_path=management_key_path,
                    client_auth=ssh_client_auth,
                    ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                    inst_cfg=owner_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    expected_apply_locked=False,
                    expected_operation_id=operation_id,
                )
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
                        and payload.get("promotion_ready") is False
                        and payload.get("observed_owner_node_id") == current_owner_node_id
                        and payload.get("pending_operation_id") is None
                    ),
                    target=passive_target,
                    hostname=passive_cfg.hostname,
                    username=username,
                    key_path=management_key_path,
                    client_auth=ssh_client_auth,
                    ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                    inst_cfg=passive_cfg,
                    runtime_binding=vm_ha_runtime_binding,
                    expected_apply_locked=False,
                    expected_operation_id=operation_id,
                )
                lifecycle_journal.complete("verify-passive-unlocked-non-forwarding")

                reconcile_replacement_policy(passive_cfg.vm_ha_node.node_id)

                release_effect = f"release-standby-replacement-inhibition-{current_owner_node_id}"
                _release_missing_vm_ha_standby_inhibition(
                    lifecycle_journal=lifecycle_journal,
                    ssh=ssh,
                    owner_target=owner_target,
                    owner_config=owner_cfg,
                    local_config=local_cfg,
                    inhibition=standby_replacement_inhibition,
                    effect=release_effect,
                )
                for terminal_cfg, terminal_target in (
                    (owner_cfg, owner_target),
                    (passive_cfg, passive_target),
                ):
                    terminal_owner = terminal_cfg.vm_ha_node.node_id == current_owner_node_id

                    def terminal_status(
                        payload: dict[str, t.Any],
                        *,
                        owner: bool = terminal_owner,
                    ) -> bool:
                        return bool(
                            payload.get("data_plane_mode") == ("active" if owner else "passive")
                            and payload.get("promotion_ready") is owner
                            and payload.get("observed_owner_node_id") == current_owner_node_id
                            and payload.get("pending_operation_id") is None
                            and payload.get("transfer_inhibition_operation_id") is None
                            and (
                                not owner
                                or _vm_ha_active_route_receipt_matches(
                                    payload,
                                    active_node_id=current_owner_node_id,
                                    runtime_binding=vm_ha_runtime_binding,
                                )
                            )
                        )

                    _wait_for_vm_ha_agent_status(
                        predicate=terminal_status,
                        target=terminal_target,
                        hostname=terminal_cfg.hostname,
                        username=username,
                        key_path=management_key_path,
                        client_auth=ssh_client_auth,
                        ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                        inst_cfg=terminal_cfg,
                        runtime_binding=vm_ha_runtime_binding,
                        expected_apply_locked=False,
                        expected_operation_id=operation_id,
                    )
                _commit_missing_vm_ha_standby_replacement_active(lifecycle_journal)
                print("[green]Apply completed successfully.[/green]")
                return

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
                with _vm_ha_progress_step(
                    vm_ha_progress_sink,
                    _VMHAProgressPhase.VERIFY_OWNER,
                ):
                    wait_owner_progress = _VMHAProgressWait(
                        vm_ha_progress_sink,
                        _VMHAProgressPhase.VERIFY_OWNER,
                    )
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
                        client_auth=ssh_client_auth,
                        ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                        inst_cfg=active_cfg,
                        runtime_binding=vm_ha_runtime_binding,
                        expected_apply_locked=False,
                        expected_operation_id=operation_id,
                        progress_callback=wait_owner_progress.update,
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
                with _vm_ha_progress_step(
                    vm_ha_progress_sink,
                    _VMHAProgressPhase.VERIFY_STANDBY,
                ):
                    wait_standby_progress = _VMHAProgressWait(
                        vm_ha_progress_sink,
                        _VMHAProgressPhase.VERIFY_STANDBY,
                    )
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
                        client_auth=ssh_client_auth,
                        ssh_policy=t.cast(SSHTrustPolicy, ssh_policy),
                        inst_cfg=passive_cfg,
                        runtime_binding=vm_ha_runtime_binding,
                        expected_apply_locked=False,
                        expected_operation_id=operation_id,
                        progress_callback=wait_standby_progress.update,
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
            if replacement_policy_reproof_required:
                reconcile_replacement_policy(passive_cfg.vm_ha_node.node_id)
            activating_predecessor = lifecycle_journal.state
            active_successor = activating_predecessor.with_status(
                VMHALifecycleStatus.ACTIVE,
                checkpoint="activation-complete",
            )
            _emit_vm_ha_progress(
                vm_ha_progress_sink,
                _VMHAProgressPhase.COMMIT_LIFECYCLE,
                _VMHAProgressState.STARTED,
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
                            client_auth=ssh_client_auth,
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
                            client_auth=ssh_client_auth,
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
                                client_auth=ssh_client_auth,
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
            _emit_vm_ha_progress(
                vm_ha_progress_sink,
                _VMHAProgressPhase.COMMIT_LIFECYCLE,
                _VMHAProgressState.COMPLETED,
            )
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
            raise typer.Exit(code=1) from _VMHAActivationFailed(str(error))

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


@app.command(epilog=_command_help_epilog("apply"))
@_serialize_explicit_vm_ha_apply
def apply(
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
    ),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: str | None = typer.Option(
        None,
        help=(
            "Ordinary gateways only: ensure the exact dedicated Service Account/group "
            "with the reviewed project editor permit and use an impersonated token"
        ),
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    region: str | None = typer.Option(None, help=_NEBIUS_REGION_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect actions without applying"),
    prepare_vm_ha_peer_rotation: bool = typer.Option(
        False,
        "--prepare-vm-ha-peer-rotation",
        help=(
            "Stage a VM-HA IPsec peer credential change and exit with both members passively fenced"
        ),
    ),
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
) -> None:
    """Reconcile desired state in Nebius and on the gateway VMs.

    Safe to rerun. Existing VMs, the dedicated gateway subnet, its route table,
    and matching IP allocations are reused when they already match the config.
    Use --recreate-gw only when infrastructure changes require VM recreation.
    """

    _apply_impl(
        local_config_file=local_config_file,
        recreate_gw=recreate_gw,
        sa=sa,
        project_id=project_id,
        region=region,
        dry_run=dry_run,
        prepare_vm_ha_peer_rotation=prepare_vm_ha_peer_rotation,
        approve_vm_ha_migration=approve_vm_ha_migration,
        recover_vm_ha_migration=recover_vm_ha_migration,
        replace_failed_vm_ha_passive=replace_failed_vm_ha_passive,
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
                f"The file is schema-valid. PSKs may be environment references or literal values.\n\n"
                f"[dim]Next steps:[/dim]\n"
                f"  1. Complete PSKs: export referenced variables or replace values in YAML\n"
                f"  2. Prepare networking now, or later: [cyan]nebius-vpngw prep-network --local-config-file {config_file}[/cyan]\n"
                f"  3. Validate: [cyan]nebius-vpngw validate-config {config_file}[/cyan]\n"
                f"  4. Deploy: [cyan]nebius-vpngw apply --local-config-file {config_file}[/cyan]",
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
            _run_network_preparation(
                config_file,
                region=None,
                console=console,
                interactive=True,
            )
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
    try:
        snapshot = _read_regular_file_snapshot(path)
    except OSError as error:
        raise ValueError(f"{label} changed while it was being read; rerun the command.") from error
    if snapshot is None:
        raise ValueError(f"{label} changed while it was being read; rerun the command.")
    raw_bytes, fingerprint = snapshot
    if (before.st_dev, before.st_ino) != (fingerprint.device, fingerprint.inode):
        raise ValueError(f"{label} changed while it was being read; rerun the command.")
    try:
        loaded = yaml.safe_load(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"{label} is not valid UTF-8 YAML.") from error
    if not isinstance(loaded, dict):
        raise ValueError(f"{label} must contain a YAML mapping.")
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


def _resolve_vm_ha_region(
    source: t.Mapping[str, t.Any],
    *,
    explicit_region: str | None,
) -> str:
    """Resolve the canonical Nebius region without inventing a zone."""

    group = source.get("gateway_group")
    group_region = group.get("region") if isinstance(group, dict) else None
    selected_value: t.Any
    selected_field: str
    if explicit_region is not None:
        selected_value, selected_field = explicit_region, "--region"
    elif str(group_region or "").strip():
        selected_value, selected_field = group_region, "gateway_group.region"
    else:
        selected_value, selected_field = source.get("region_id"), "region_id"
    resolved = _resolve_cloud_field(
        selected_value,
        field=selected_field,
        required=False,
    )
    if resolved is None:
        raise ValueError(
            f"Nebius region authority {selected_field} must resolve before cloud access."
        )
    return resolved


def _apply_nebius_region_precedence(
    config: dict[str, t.Any],
    *,
    explicit_region: str | None,
) -> str:
    """Resolve and materialize the canonical region for plan construction."""

    effective_region = _resolve_vm_ha_region(
        config,
        explicit_region=explicit_region,
    )
    group = dict(config.get("gateway_group") or {})
    group["region"] = effective_region
    config["gateway_group"] = group
    config["region_id"] = effective_region
    return effective_region


def _load_config_with_region_override(
    path: Path,
    *,
    region: str | None,
    allow_missing_tunnel_psk_placeholders: bool = False,
) -> dict[str, t.Any]:
    """Load config while applying an explicit CLI region before schema validation."""

    if allow_missing_tunnel_psk_placeholders:
        if region is None:
            return load_local_config(
                path,
                allow_missing_tunnel_psk_placeholders=True,
            )
        return load_local_config(
            path,
            allow_missing_tunnel_psk_placeholders=True,
            region_override=region,
        )
    if region is None:
        return load_local_config(path)
    return load_local_config(path, region_override=region)


@_with_vm_manager_lifetimes
def _reserve_vm_ha_passive_public_ip(
    source: dict[str, t.Any],
    *,
    region: str | None,
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
    effective_region = _resolve_vm_ha_region(
        semantic_source,
        explicit_region=region,
    )
    network_id = _resolve_cloud_field(
        group.get("network_id"), field="gateway_group.network_id", required=False
    )
    spec = GatewayGroupSpec(
        name=str(group["name"]),
        instance_count=2,
        region=effective_region,
        external_ips=[],
        subnet=t.cast(dict[str, t.Any], group.get("subnet") or {}),
        vm_spec=t.cast(dict[str, t.Any], group.get("vm_spec") or {}),
        network_id=network_id,
    )
    auth_token = _ensure_authentication(required=True, show_progress=True)
    manager = _own_vm_manager(
        VMManager(
            project_id=project_id,
            region=effective_region,
            auth_token=auth_token,
            tenant_id=tenant_id,
            region_id=effective_region,
        )
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


def _validate_network_preparation_gateway_group(
    gateway_group: t.Any,
) -> tuple[str, int, dict[str, t.Any], list[list[str]]]:
    """Validate the cloud-affecting gateway subset before authentication."""

    if not isinstance(gateway_group, dict):
        raise ValueError("gateway_group must be a mapping")

    raw_name = gateway_group.get("name") or "nebius-vpn-gw"
    name = raw_name.strip() if isinstance(raw_name, str) else raw_name
    if (
        not isinstance(name, str)
        or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?",
            name,
        )
        is None
    ):
        raise ValueError("gateway_group.name must be a valid lowercase resource name")

    raw_instance_count = gateway_group.get("instance_count", 1)
    if isinstance(raw_instance_count, bool):
        raise ValueError("gateway_group.instance_count must be an integer from 1 through 10")
    try:
        instance_count = int(raw_instance_count)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "gateway_group.instance_count must be an integer from 1 through 10"
        ) from error
    if not 1 <= instance_count <= 10:
        raise ValueError("gateway_group.instance_count must be from 1 through 10")

    vm_spec = gateway_group.get("vm_spec") or {}
    if not isinstance(vm_spec, dict):
        raise ValueError("gateway_group.vm_spec must be a mapping")
    raw_num_nics = vm_spec.get("num_nics", 1)
    if isinstance(raw_num_nics, bool):
        raise ValueError("gateway_group.vm_spec.num_nics must be 1")
    try:
        num_nics = int(raw_num_nics)
    except (TypeError, ValueError) as error:
        raise ValueError("gateway_group.vm_spec.num_nics must be 1") from error
    if num_nics != 1:
        raise ValueError("gateway_group.vm_spec.num_nics must be 1")

    subnet = gateway_group.get("subnet") or {}
    if not isinstance(subnet, dict):
        raise ValueError("gateway_group.subnet must be a mapping")
    subnet_name = subnet.get("name") or "vpngw-subnet"
    if (
        not isinstance(subnet_name, str)
        or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?",
            subnet_name.strip(),
        )
        is None
    ):
        raise ValueError("gateway_group.subnet.name must be a valid lowercase resource name")
    raw_prefix_length = subnet.get("prefix_length", 24)
    if isinstance(raw_prefix_length, bool):
        raise ValueError("gateway_group.subnet.prefix_length must be from 8 through 28")
    try:
        prefix_length = int(raw_prefix_length)
    except (TypeError, ValueError) as error:
        raise ValueError("gateway_group.subnet.prefix_length must be from 8 through 28") from error
    if not 8 <= prefix_length <= 28:
        raise ValueError("gateway_group.subnet.prefix_length must be from 8 through 28")
    raw_cidr = subnet.get("cidr")
    if raw_cidr:
        if not isinstance(raw_cidr, str):
            raise ValueError("gateway_group.subnet.cidr must be a private IPv4 CIDR")
        try:
            cidr = ipaddress.ip_network(raw_cidr.strip(), strict=False)
        except ValueError as error:
            raise ValueError("gateway_group.subnet.cidr must be a private IPv4 CIDR") from error
        if (
            not isinstance(cidr, ipaddress.IPv4Network)
            or not cidr.is_private
            or not 8 <= cidr.prefixlen <= 28
        ):
            raise ValueError("gateway_group.subnet.cidr must be a private IPv4 CIDR")

    network_id = gateway_group.get("network_id")
    if network_id is not None and not isinstance(network_id, str):
        raise ValueError("gateway_group.network_id must be a string when set")
    vm_ha = gateway_group.get("vm_ha")
    if vm_ha is not None and not isinstance(vm_ha, dict):
        raise ValueError("gateway_group.vm_ha must be a mapping when set")
    if isinstance(vm_ha, dict) and bool(vm_ha.get("enabled")) and instance_count != 2:
        raise ValueError("gateway_group.vm_ha.enabled requires instance_count=2")

    raw_external_ips = gateway_group.get("external_ips") or []
    if not isinstance(raw_external_ips, list):
        raise ValueError("gateway_group.external_ips must be a list of lists")
    if len(raw_external_ips) > instance_count:
        raise ValueError("gateway_group.external_ips cannot contain more rows than instance_count")
    matrix: list[list[str]] = []
    seen_addresses: set[str] = set()
    for row_index, row in enumerate(raw_external_ips):
        if not isinstance(row, list):
            raise ValueError(f"gateway_group.external_ips[{row_index}] must be a list of IPs")
        if len(row) > 1:
            raise ValueError(f"gateway_group.external_ips[{row_index}] must contain at most one IP")
        if not row:
            matrix.append([])
            continue
        address = row[0]
        if not isinstance(address, str):
            raise ValueError(f"gateway_group.external_ips[{row_index}][0] must be an IPv4 address")
        address = address.strip()
        if not address:
            matrix.append([])
            continue
        try:
            normalized_address = str(ipaddress.IPv4Address(address))
        except ipaddress.AddressValueError as error:
            raise ValueError(
                f"gateway_group.external_ips[{row_index}][0] must be an IPv4 address"
            ) from error
        if normalized_address in seen_addresses:
            raise ValueError("gateway_group.external_ips entries must be globally unique")
        seen_addresses.add(normalized_address)
        matrix.append([normalized_address])
    while len(matrix) < instance_count:
        matrix.append([])
    return name, instance_count, vm_spec, matrix


def _network_preparation_slot_label(
    gateway_group: t.Mapping[str, t.Any],
    instance_index: int,
) -> str:
    vm_ha = gateway_group.get("vm_ha")
    if isinstance(vm_ha, dict) and bool(vm_ha.get("enabled")):
        role = "initial active" if instance_index == 0 else "initial passive"
        return f"Gateway VM {instance_index} ({role})"
    return f"Gateway VM {instance_index}"


def _candidate_available_for_slot(
    candidate: PublicAllocationCandidate,
    *,
    instance_index: int,
    nic_index: int,
) -> bool:
    if candidate.assigned_instance_index is None:
        return True
    return (
        candidate.assigned_instance_index == instance_index
        and candidate.assigned_nic_index == nic_index
    )


def _select_existing_public_allocations(
    console: t.Any,
    *,
    gateway_group: t.Mapping[str, t.Any],
    desired_matrix: list[list[str]],
    candidates: list[PublicAllocationCandidate],
) -> tuple[list[list[str]], dict[tuple[int, int], PublicAllocationCandidate]]:
    """Prompt for distinct eligible allocations while allowing auto per remaining slot."""

    missing_slots = [
        (instance_index, 0) for instance_index, row in enumerate(desired_matrix) if not row
    ]
    configured_addresses = {ip for row in desired_matrix for ip in row if ip}
    available = [
        candidate for candidate in candidates if candidate.address not in configured_addresses
    ]
    if not missing_slots or not any(
        _candidate_available_for_slot(
            candidate,
            instance_index=instance_index,
            nic_index=nic_index,
        )
        for instance_index, nic_index in missing_slots
        for candidate in available
    ):
        return desired_matrix, {}

    try:
        while True:
            assignment = (
                str(
                    typer.prompt(
                        "Public IP assignment [existing/auto]",
                        default="existing",
                    )
                )
                .strip()
                .casefold()
            )
            if assignment in {"existing", "auto"}:
                break
            console.print("[red]Choose existing or auto.[/red]")
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise _NetworkPreparationFailure(
            "selection",
            "Public IP selection was interrupted before any allocation was chosen.",
        ) from error
    if assignment == "auto":
        return desired_matrix, {}

    from rich.table import Table

    selected: dict[tuple[int, int], PublicAllocationCandidate] = {}
    used_ids: set[str] = set()
    result = [list(row) for row in desired_matrix]
    for instance_index, nic_index in missing_slots:
        slot_candidates = [
            candidate
            for candidate in available
            if candidate.allocation_id not in used_ids
            and _candidate_available_for_slot(
                candidate,
                instance_index=instance_index,
                nic_index=nic_index,
            )
        ]
        if not slot_candidates:
            continue
        label = _network_preparation_slot_label(gateway_group, instance_index)
        chosen: PublicAllocationCandidate | None = None
        try:
            if len(slot_candidates) == 1:
                candidate = slot_candidates[0]
                if typer.confirm(
                    f"Use existing public IP {candidate.address} for {label} eth{nic_index}?",
                    default=True,
                ):
                    chosen = candidate
            else:
                table = Table(title=f"Eligible public IPs for {label} eth{nic_index}")
                table.add_column("Choice", justify="right")
                table.add_column("Address", style="cyan")
                table.add_column("Allocation")
                table.add_column("Assignment")
                for choice_index, candidate in enumerate(slot_candidates, start=1):
                    assignment_text = (
                        "unassigned"
                        if candidate.assigned_instance_index is None
                        else "already assigned to this VM/NIC"
                    )
                    table.add_row(
                        str(choice_index),
                        candidate.address,
                        candidate.name,
                        assignment_text,
                    )
                console.print(table)
                while True:
                    choice = (
                        str(
                            typer.prompt(
                                f"Public IP for {label} eth{nic_index} (number or auto)",
                                default="1",
                            )
                        )
                        .strip()
                        .casefold()
                    )
                    if choice == "auto":
                        break
                    if choice.isdigit() and 1 <= int(choice) <= len(slot_candidates):
                        chosen = slot_candidates[int(choice) - 1]
                        break
                    console.print("[red]Enter a listed number or auto.[/red]")
        except (typer.Abort, EOFError, KeyboardInterrupt) as error:
            raise _NetworkPreparationFailure(
                "selection",
                "Public IP selection was interrupted; no new allocation was requested.",
            ) from error
        if chosen is None:
            continue
        while len(result) <= instance_index:
            result.append([])
        result[instance_index] = [chosen.address]
        selected[(instance_index, nic_index)] = chosen
        used_ids.add(chosen.allocation_id)
    return result, selected


@_with_vm_manager_lifetimes
def _prepare_network_config(
    local_config_file: Path,
    *,
    region: str | None,
    console: t.Any,
    interactive: bool,
) -> _NetworkPreparationResult:
    """Own the shared cloud-preparation path used by both CLI entry points."""
    try:
        snapshot = _read_regular_file_snapshot(local_config_file)
        if snapshot is None:
            raise OSError("Configuration file does not exist.")
        source_bytes, source_fingerprint = snapshot
        source_text = source_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _NetworkPreparationFailure("load", str(error)) from error
    try:
        cfg = load_local_config(
            local_config_file,
            allow_missing_placeholders=True,
            validate_schema=False,
            region_override=region,
        )
    except Exception as error:
        raise _NetworkPreparationFailure("load", str(error)) from error
    try:
        if _file_fingerprint(local_config_file) != source_fingerprint:
            raise OSError(
                "Configuration file changed while it was being loaded; rerun the command."
            )
    except OSError as error:
        raise _NetworkPreparationFailure("load", str(error)) from error

    tenant_id = str(cfg.get("tenant_id") or "").strip() or None
    project_id = str(cfg.get("project_id") or "").strip() or None
    if not project_id or "${" in project_id:
        raise _NetworkPreparationFailure(
            "project",
            "Set project_id directly in YAML or via ${PROJECT_ID} env var.",
        )

    try:
        gg = cfg.get("gateway_group", {}) or {}
        name, instance_count, vm_spec, configured_matrix = (
            _validate_network_preparation_gateway_group(gg)
        )
    except ValueError as error:
        raise _NetworkPreparationFailure("config", str(error)) from error
    external_ips = configured_matrix
    network_id = str(gg.get("network_id") or "").strip() or None
    subnet = gg.get("subnet", {}) or {}
    try:
        effective_region = _apply_nebius_region_precedence(
            cfg,
            explicit_region=None,
        )
    except ValueError as error:
        raise _NetworkPreparationFailure("region", str(error)) from error
    spec = GatewayGroupSpec(
        name=name,
        instance_count=instance_count,
        region=effective_region,
        external_ips=external_ips,
        subnet=subnet,
        vm_spec=vm_spec,
        network_id=network_id,
    )

    auth_token = _ensure_authentication(required=True, show_progress=True)
    vm_mgr = _own_vm_manager(
        VMManager(
            project_id=project_id,
            region=effective_region,
            auth_token=auth_token,
            tenant_id=tenant_id,
            region_id=effective_region,
        )
    )
    try:
        subnet_id = vm_mgr.prepare_network_foundation(spec)
        desired_matrix = [list(row) for row in configured_matrix]
        selected: dict[tuple[int, int], PublicAllocationCandidate] = {}
        if interactive and any(not row for row in desired_matrix):
            candidates = vm_mgr.list_eligible_public_allocations(
                spec,
                subnet_id=subnet_id,
            )
            desired_matrix, selected = _select_existing_public_allocations(
                console,
                gateway_group=t.cast(t.Mapping[str, t.Any], gg),
                desired_matrix=desired_matrix,
                candidates=candidates,
            )
        if selected:
            vm_mgr.verify_selected_public_allocations(
                spec,
                subnet_id=subnet_id,
                selections=selected,
            )
        allocated_ips = vm_mgr.prepare_public_allocations_in_subnet(
            spec,
            subnet_id=subnet_id,
            desired_external_ips=desired_matrix,
        )
        if selected:
            vm_mgr.verify_selected_public_allocations(
                spec,
                subnet_id=subnet_id,
                selections=selected,
            )
    except _NetworkPreparationFailure:
        raise
    except Exception as error:
        raise _NetworkPreparationFailure("prepare", str(error)) from error

    if not allocated_ips or any(not row for row in allocated_ips):
        raise _NetworkPreparationFailure("no_ips", "No public IPs were allocated.")

    if allocated_ips == configured_matrix:
        return _NetworkPreparationResult(
            name=name,
            allocated_ips=allocated_ips,
            used_assigned_ips=True,
            yaml_updated=False,
        )

    try:
        yaml_updated = _update_external_ips_in_yaml(
            local_config_file,
            allocated_ips,
            expected_fingerprint=source_fingerprint,
            source_text=source_text,
        )
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
        yaml_updated=yaml_updated,
    )


def _run_network_preparation(
    local_config_file: Path,
    *,
    region: str | None,
    console: t.Any,
    interactive: bool,
) -> _NetworkPreparationResult:
    """Run and render one preparation attempt while preserving legacy CLI messages."""
    from rich.panel import Panel

    try:
        result = _prepare_network_config(
            local_config_file,
            region=region,
            console=console,
            interactive=interactive,
        )
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
        elif error.stage == "config":
            heading = "✗ Invalid gateway network-preparation configuration"
        elif error.stage == "region":
            heading = "✗ Failed to resolve Nebius region"
        elif error.stage == "selection":
            heading = "✗ Public IP selection did not complete"
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
    region: str | None = typer.Option(None, help=_NEBIUS_REGION_HELP),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Prompt to reuse eligible existing public IP allocations",
    ),
    no_interactive: bool = typer.Option(
        False,
        "--no-interactive",
        help="Never prompt; reuse configured or canonical allocations deterministically",
    ),
):
    """Prepare gateway networking before peer setup.

    Safe to rerun. Ensures the configured gateway subnet, its dedicated route
    table, and the requested public IP allocations exist without recreating
    matching resources.
    """
    from rich.console import Console

    console = Console()
    if interactive and no_interactive:
        console.print("[red]--interactive and --no-interactive cannot be used together.[/red]")
        raise typer.Exit(code=2)
    use_interactive = interactive or (
        not no_interactive and bool(sys.stdin.isatty()) and bool(sys.stdout.isatty())
    )
    resolved_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    _run_network_preparation(
        resolved_config_file,
        region=region,
        console=console,
        interactive=use_interactive,
    )


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


def _vm_ha_lifecycle_runtime_binding(state: VMHALifecycleState) -> SimpleNamespace:
    """Project the immutable runtime identity after its authority is proven."""

    if not state.allocation_id or not state.route_runtime_id:
        raise ValueError("VM-HA lifecycle runtime binding is incomplete")
    return SimpleNamespace(
        cluster_id=state.cluster_id,
        route_runtime_id=state.route_runtime_id,
        shared_allocation_id=state.allocation_id,
    )


def _vm_ha_status_runtime_binding(state: VMHALifecycleState) -> SimpleNamespace:
    """Project the immutable lifecycle identity needed for agent status validation."""

    if state.status not in {VMHALifecycleStatus.ACTIVATING, VMHALifecycleStatus.ACTIVE}:
        raise ValueError("VM-HA lifecycle has not reached an authoritative runtime binding")
    return _vm_ha_lifecycle_runtime_binding(state)


def _vm_ha_persisted_replacement_runtime_binding(
    state: VMHALifecycleState,
    replacement: _VMHAMissingStandbyReplacementPlan,
) -> SimpleNamespace:
    """Retain prior ACTIVE runtime authority for one exact replacement checkpoint."""

    transaction = state.transaction
    bindings = {} if transaction is None else dict(transaction.resource_bindings)
    cycle = (
        None
        if transaction is None
        else vm_ha_passive_replacement_cycle_for_approval(
            bindings,
            replacement.target_instance_name,
            transaction.approval_digest,
        )
    )
    owner_sequences = vm_ha_missing_standby_owner_sequences(bindings)
    owner_sequence = owner_sequences[-1] if owner_sequences else None
    owner = next(
        (
            member
            for member in state.members
            if member.instance_name == replacement.owner_instance_name
        ),
        None,
    )
    target = next(
        (
            member
            for member in state.members
            if member.instance_name == replacement.target_instance_name
        ),
        None,
    )
    if (
        state.record_version != 4
        or state.status is not VMHALifecycleStatus.PROVISIONING
        or transaction is None
        or transaction.approval_kind != "recovery"
        or not replacement.authorization_persisted
        or transaction.approval_digest != replacement.approval_digest
        or transaction.operation_id != replacement.operation_id
        or cycle != replacement.replacement_cycle
        or bindings.get(
            vm_ha_missing_standby_disk_name_binding_key(
                replacement.target_instance_name,
                replacement.replacement_cycle,
            )
        )
        != replacement.replacement_disk_name
        or owner_sequence is None
        or owner is None
        or target is None
        or not owner.compute_id
        or not owner.network_interface_name
        or target.compute_id
        or target.disk_id
        or bindings.get(vm_ha_missing_standby_owner_binding_key("instance", owner_sequence))
        != owner.instance_name
        or bindings.get(vm_ha_missing_standby_owner_binding_key("compute", owner_sequence))
        != owner.compute_id
        or bindings.get(vm_ha_missing_standby_owner_binding_key("nic", owner_sequence))
        != owner.network_interface_name
    ):
        raise ValueError("VM-HA persisted missing standby runtime authority is unavailable")
    return _vm_ha_lifecycle_runtime_binding(state)


def _vm_ha_planned_terminal_runtime_binding(
    state: VMHALifecycleState,
    inst_cfg: t.Any,
    *,
    replacement: _VMHAMissingStandbyReplacementPlan | None = None,
) -> SimpleNamespace:
    """Bind terminal route proof to lifecycle authority and exact generation."""

    authority = (
        _vm_ha_persisted_replacement_runtime_binding(state, replacement)
        if state.status is VMHALifecycleStatus.PROVISIONING and replacement is not None
        else _vm_ha_status_runtime_binding(state)
    )
    generation = inst_cfg.vm_ha_generation
    if generation is None:
        raise ValueError("VM-HA planned terminal generation is unavailable")
    return SimpleNamespace(
        cluster_id=authority.cluster_id,
        route_runtime_id=authority.route_runtime_id,
        shared_allocation_id=authority.shared_allocation_id,
        generation_id=generation.generation_id,
        configuration_digest=generation.digests.configuration,
        static_routes_digest=generation.digests.static_routes,
        bgp_policy_digest=generation.digests.bgp_policy,
    )


@dataclass(frozen=True)
class _VMHACloudAuthority:
    lifecycle: str
    condition: str
    owner_name: str | None
    owner_node_id: str | None
    operation_id: str | None
    reasons: tuple[str, ...]
    observation_digest: str = ""
    member_compute_states: tuple[tuple[str, str], ...] = ()
    unavailable_member_node_ids: tuple[str, ...] = ()


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
    action: str = "inspect"
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _VMHAStatusSnapshot:
    """Typed status evidence shared by display and idempotent convergence."""

    view: _VMHAStatusView
    lifecycle_state: VMHALifecycleState | None
    authority: _VMHACloudAuthority
    members: tuple[_VMHAMemberEvidence, _VMHAMemberEvidence]
    authority_digest: str


@dataclass(frozen=True)
class _VMHACommandInspection:
    """Strict command inspection plus the local lock identity."""

    snapshot: _VMHAStatusSnapshot
    project_id: str
    gateway_name: str


def _dedupe_vm_ha_reasons(values: t.Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _safe_vm_ha_reason(value: object) -> str:
    """Return only a known identity-free controller or rearm reason code."""

    text = value if isinstance(value, str) else ""
    if text in _VM_HA_SAFE_REASON_CODES:
        return text
    return "controller-reported-condition"


def _safe_destroy_reason(value: object) -> str:
    """Project one closed destroy reason without exposing provider details."""

    if isinstance(value, DestroyFailure):
        return value.reason_code
    return "destroy-operation-failed"


def _vm_ha_pending_action_kind(
    value: object,
    *,
    member_node_ids: frozenset[str],
) -> tuple[str, str] | None:
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
    return action_kind, target_node_id


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
    if state.status in {
        VMHALifecycleStatus.REMOVED,
        VMHALifecycleStatus.DESTROYED,
    }:
        blocked.append(f"lifecycle-{state.status.value}")
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

    member_compute_states: list[tuple[str, str]] = []
    unavailable_member_node_ids: list[str] = []
    for member in state.members:
        observed = observed_members.get(member.instance_name)
        if observed is None:
            blocked.extend(("cloud-member-unavailable", "cloud-member-identity-conflict"))
            continue
        if observed == {"instance_name": member.instance_name, "present": False}:
            unavailable_member_node_ids.append(member.node_id)
            (transitioning if lifecycle_transition or operation_id else blocked).append(
                "cloud-member-unavailable"
            )
            continue
        if observed.get("present") is not True:
            blocked.append("cloud-member-state-malformed")
            continue
        raw_state = observed.get("state")
        if raw_state not in {item.value for item in InstanceCloudState}:
            blocked.append("cloud-member-state-malformed")
            continue
        member_compute_states.append((member.node_id, t.cast(str, raw_state)))
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
    current_cluster_fingerprint = NebiusSDKRouteBackend._authority_fingerprint(state.cluster_id)
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
        if not route_name.startswith("vpngw-") or labels.get(
            NebiusSDKRouteBackend._AUTHORITY_TARGET_LABEL
        ) != target_fingerprints.get(route_table_id):
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
        observation_digest=_canonical_digest(observation),
        member_compute_states=tuple(sorted(member_compute_states)),
        unavailable_member_node_ids=tuple(sorted(unavailable_member_node_ids)),
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
    mtls_command: str = "nebius-vpngw vm-ha --rotate-mtls",
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
    mtls_transition_operations: set[str] = set()
    mtls_states: list[tuple[str, int | None, str | None, str | None, bool]] = []
    auto_healing_states: list[str] = []
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
        auto_healing = t.cast(dict[str, t.Any], record["auto_healing"])
        auto_healing_state = str(auto_healing["state"])
        if (
            auto_healing_state in {"enabled", "disabled"}
            and auto_healing.get("peer_agrees") is not True
        ):
            auto_healing_states.append("blocked")
            blocked.append("standby-auto-healing-policy-invalid")
        else:
            auto_healing_states.append(auto_healing_state)
        if auto_healing_state == "blocked":
            blocked.append("standby-auto-healing-policy-invalid")
        elif auto_healing_state == "transitioning" or auto_healing.get("accepted_start") is True:
            transitioning.append("standby-auto-healing-policy-transition")
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
        mtls_operation = mtls.get("operation_id")
        mtls_inhibition_operation = mtls.get("inhibition_operation_id")
        mtls_operation_exact = bool(
            isinstance(mtls_inhibition_operation, str)
            and mtls_operation in {None, mtls_inhibition_operation}
            and (
                mtls.get("operation_kind") == "rotation"
                if mtls_operation is not None
                else mtls.get("operation_kind") is None
            )
            and record.get("transfer_inhibition_operation_id") == mtls_inhibition_operation
            and record.get("transfer_inhibition_quiescent") is True
            and record.get("apply_locked") is False
            and apply_operation is None
            and mtls_inhibited
        )
        mtls_states.append((mtls_state, mtls_epoch, mtls_fingerprint, mtls_phase, mtls_inhibited))
        if mtls_state in {"missing", "invalid"}:
            blocked.append(f"managed-mtls-{mtls_state}")
        elif mtls_state == "transitioning" or mtls_inhibited:
            if mtls_operation_exact:
                mtls_transitioning_members.add(member.node_id)
                mtls_transition_operations.add(t.cast(str, mtls_inhibition_operation))
                transitioning.append("managed-mtls-rotation")
            else:
                blocked.append("managed-mtls-transaction-conflict")
        pending_action = _vm_ha_pending_action_kind(
            pending,
            member_node_ids=member_node_ids,
        )
        pending_action_kind = pending_action[0] if pending_action is not None else None
        pending_action_target = pending_action[1] if pending_action is not None else None
        repair_operation_exact = bool(
            pending is not None
            and isinstance(repair, dict)
            and repair.get("operation_id") == pending
        )
        pending_operation_expected = bool(
            pending is not None
            and pending_action_kind in _VM_HA_PENDING_ACTIONS_BY_STATE.get(record_state, ())
            and (pending_action_kind != "disable-active" or pending_action_target == member.node_id)
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
        active_nonowner_fencing = bool(
            authority.condition == "exact"
            and authority.owner_node_id is not None
            and record.get("data_plane_mode") == "active"
            and member.node_id != authority.owner_node_id
        )
        exact_mtls_inhibition_block = bool(
            mtls_operation_exact
            and record_state == "blocked"
            and record.get("data_plane_mode") == "passive"
            and pending is None
            and _vm_ha_record_reasons(record) == ("mtls-rotation-active",)
        )
        if record_state == "blocked" and not exact_mtls_inhibition_block:
            if active_nonowner_fencing and pending_operation_expected:
                transitioning.append("controller-safety-fencing")
            else:
                blocked.extend(_vm_ha_record_reasons(record) or ("controller-blocked",))
        if active_nonowner_fencing:
            transitioning.append("nonowner-forwarding")
        if authority.owner_node_id is not None and observed_owner != authority.owner_node_id:
            if active_nonowner_fencing or (
                member_transitioning
                and (authority.condition == "transitioning" or pending_operation_expected)
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

    if len(mtls_transition_operations) > 1:
        blocked.append("managed-mtls-transaction-conflict")
    committed_auto_healing_states = {
        state for state in auto_healing_states if state in {"enabled", "disabled"}
    }
    if len(committed_auto_healing_states) > 1:
        blocked.append("standby-auto-healing-policy-invalid")
    accepted_auto_healing_start = any(
        member.record is not None
        and t.cast(dict[str, t.Any], member.record["auto_healing"])["accepted_start"] is True
        for member in members
    )
    if "blocked" in auto_healing_states or len(committed_auto_healing_states) > 1:
        auto_healing_value = "blocked"
    elif "transitioning" in auto_healing_states or accepted_auto_healing_start:
        auto_healing_value = "transitioning"
    elif committed_auto_healing_states == {"disabled"}:
        auto_healing_value = "disabled"
    elif committed_auto_healing_states == {"enabled"}:
        auto_healing_value = "enabled"
    else:
        auto_healing_value = "unknown"
    auto_healing_detail = {
        "enabled": "automatic standby restoration is enabled",
        "disabled": "automatic standby restoration is disabled for maintenance",
        "transitioning": "standby auto-healing policy work is in progress",
        "blocked": "standby auto-healing policy evidence is blocked",
        "unknown": "complete standby auto-healing policy evidence is unavailable",
    }[auto_healing_value]
    maintenance_policy_declared = bool(
        committed_auto_healing_states == {"disabled"}
        and "transitioning" not in auto_healing_states
        and "blocked" not in auto_healing_states
    )

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
    if (
        authority.condition == "exact"
        and safe_owner
        and not standby_ready
        and not maintenance_policy_declared
    ):
        if standby_member is None or standby_member.record is None:
            degraded.append("standby-status-unavailable")
        else:
            degraded.extend(_vm_ha_record_reasons(standby_member.record) or ("standby-not-ready",))
    if owner_record is not None and owner_record.get("rearm_phase") in {"blocked", "inhibited"}:
        owner_rearm_reasons = _vm_ha_record_reasons(owner_record)
        if not (
            maintenance_policy_declared
            and owner_rearm_reasons == ("standby-auto-healing-policy-disabled",)
        ):
            degraded.extend(owner_rearm_reasons or ("rearm-not-ready",))

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
    elif maintenance_policy_declared and authority.condition == "exact" and safe_owner:
        overall = "MAINTENANCE"
        overall_reasons = ("standby-auto-healing-policy-disabled",)
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
        "maintenance"
        if overall == "MAINTENANCE"
        else "ready"
        if safe_owner and standby_ready
        else "restoring"
        if overall == "TRANSITIONING"
        else "not ready"
        if safe_owner
        else "unknown"
    )
    redundancy_detail = (
        "standby restoration is intentionally disabled"
        if overall == "MAINTENANCE"
        else "owner and standby evidence agree"
        if safe_owner and standby_ready
        else "; ".join(overall_reasons[:3]) or "required evidence unavailable"
    )

    durations: t.Mapping[str, object] | None = None
    if owner_record is not None:
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
    elif overall == "MAINTENANCE":
        action = rearm_command + " --standby-auto-healing enabled"
        action_detail = "re-enable automatic standby restoration after maintenance"
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
    mtls_value = (
        "healthy" if healthy_mtls else "rotating" if mtls_transitioning_members else "blocked"
    )
    mtls_detail = (
        "; ".join(
            f"epoch {epoch if epoch is not None else 'unknown'} fp "
            f"{fingerprint[:12] if fingerprint is not None else 'unavailable'} "
            f"phase {phase or state}{' inhibited' if inhibited else ''}"
            for state, epoch, fingerprint, phase, inhibited in mtls_states
        )
        or "managed mTLS status unavailable"
    )
    identity_states: list[str] = []
    for member in members:
        identity = member.record.get("runtime_identity") if member.record is not None else None
        state = identity.get("state") if isinstance(identity, dict) else None
        identity_states.append(
            state if state in {"verified", "blocked", "migration-required"} else "unknown"
        )
    if len(identity_states) == 2 and all(state == "verified" for state in identity_states):
        identity_value = "verified"
        identity_detail = "both current-boot runtime identities verified"
    elif "blocked" in identity_states:
        identity_value = "blocked"
        identity_detail = "at least one runtime identity proof is blocked"
    elif "migration-required" in identity_states:
        identity_value = "migration required"
        identity_detail = "run ordinary apply to bind the runtime identity"
    else:
        identity_value = "unknown"
        identity_detail = "complete runtime identity evidence is unavailable"
    summary_rows = (
        ("Overall", overall, "; ".join(overall_reasons[:3]) or "all required evidence agrees"),
        ("Lifecycle", authority.lifecycle, "; ".join(authority.reasons) or "authoritative"),
        ("Owner", owner_label, owner_detail),
        ("Redundancy", redundancy_value, redundancy_detail),
        ("Identity", identity_value, identity_detail),
        ("Auto-healing", auto_healing_value, auto_healing_detail),
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
        action=action,
        reasons=overall_reasons,
    )


def _vm_ha_snapshot_digest(
    authority: _VMHACloudAuthority,
    members: tuple[_VMHAMemberEvidence, _VMHAMemberEvidence],
    *,
    lifecycle_record_sha256: str | None,
) -> str:
    """Bind all safety-relevant status fields without publishing identities."""

    member_records: list[dict[str, object]] = []
    for member in members:
        record = member.record or {}
        identity = record.get("runtime_identity")
        mtls = record.get("mtls")
        repair = record.get("repair")
        member_records.append(
            {
                "name": member.name,
                "configured_role": member.configured_role,
                "node_id": member.node_id,
                "condition": member.condition,
                "reason": member.reason,
                "state": record.get("state"),
                "generation_id": record.get("generation_id"),
                "digests": record.get("digests"),
                "promotion_ready": record.get("promotion_ready"),
                "standby_ready": record.get("standby_ready"),
                "standby_readiness_reasons": record.get("standby_readiness_reasons"),
                "data_plane_mode": record.get("data_plane_mode"),
                "observed_owner_node_id": record.get("observed_owner_node_id"),
                "apply_locked": record.get("apply_locked"),
                "apply_operation_id": record.get("apply_operation_id"),
                "pending_operation_id": record.get("pending_operation_id"),
                "rearm_phase": record.get("rearm_phase"),
                "rearm_reason": record.get("rearm_reason"),
                "runtime_identity_state": (
                    identity.get("state") if isinstance(identity, dict) else None
                ),
                "mtls": (
                    {
                        key: mtls.get(key)
                        for key in (
                            "state",
                            "epoch",
                            "certificate_fingerprint",
                            "phase",
                            "inhibited",
                            "operation_kind",
                            "operation_id",
                            "inhibition_operation_id",
                        )
                    }
                    if isinstance(mtls, dict)
                    else None
                ),
                "repair": (
                    {key: repair.get(key) for key in ("operation_id", "failure_fingerprint")}
                    if isinstance(repair, dict)
                    else None
                ),
            }
        )
    return _canonical_digest(
        {
            "authority": {
                "lifecycle": authority.lifecycle,
                "condition": authority.condition,
                "owner_name": authority.owner_name,
                "owner_node_id": authority.owner_node_id,
                "operation_id": authority.operation_id,
                "reasons": authority.reasons,
                "observation_digest": authority.observation_digest,
                "member_compute_states": authority.member_compute_states,
                "unavailable_member_node_ids": authority.unavailable_member_node_ids,
            },
            "lifecycle_record_sha256": lifecycle_record_sha256,
            "members": member_records,
        }
    )


def _collect_vm_ha_status_snapshot(
    *,
    local_config_file: Path,
    local_cfg: dict[str, t.Any],
    plan: ResolvedDeploymentPlan,
    project_id: str | None,
    vm_manager: VMManager,
    vm_ips: t.Mapping[str, str],
    ssh_context: _StatusSSHContext,
    require_local_generation: bool,
) -> _VMHAStatusSnapshot:
    """Collect the authoritative VM-HA projection without rendering it."""

    lifecycle_state: VMHALifecycleState | None = None
    status_runtime_binding: t.Any | None = None
    try:
        lifecycle_state = VMHALifecycleStore(local_config_file).read(
            expected_project_id=project_id or "",
            expected_gateway_name=plan.gateway_group.name,
        )
    except (OSError, RuntimeError, ValueError):
        authority = _vm_ha_unavailable_authority("unknown", "lifecycle-status-unavailable")
    else:
        if lifecycle_state is None:
            authority = _vm_ha_unavailable_authority("unknown", "lifecycle-status-unavailable")
        elif lifecycle_state.status in {
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
            cloud_observation = vm_manager.observe_vm_ha_migration_state(
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
        if node_id in authority.unavailable_member_node_ids:
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
            status_ssh_policy = ssh_context.policies.get(inst_cfg.hostname)
            if status_ssh_policy is None:
                raise _VMHAStatusSSHUnavailable(
                    "exact SSH trust is unavailable for this VM-HA member"
                )
            if ssh_context.client_auth_required and ssh_context.client_auth is None:
                raise _VMHAStatusSSHUnavailable(
                    "exact SSH client identity is unavailable for this VM-HA member"
                )
            vm_ha = _fetch_vm_ha_agent_status(
                target=target,
                hostname=inst_cfg.hostname,
                username=ssh_context.username,
                key_path=ssh_context.key_path,
                client_auth=ssh_context.client_auth,
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

    members = t.cast(
        tuple[_VMHAMemberEvidence, _VMHAMemberEvidence],
        tuple(member_evidence),
    )
    view = _vm_ha_status_view(
        authority,
        members,
        rearm_command=_vm_ha_local_config_command("nebius-vpngw vm-ha", local_config_file),
        mtls_command=_vm_ha_local_config_command(
            "nebius-vpngw vm-ha --rotate-mtls",
            local_config_file,
        ),
    )
    return _VMHAStatusSnapshot(
        view=view,
        lifecycle_state=lifecycle_state,
        authority=authority,
        members=members,
        authority_digest=_vm_ha_snapshot_digest(
            authority,
            members,
            lifecycle_record_sha256=(
                None if lifecycle_state is None else lifecycle_state.record_sha256
            ),
        ),
    )


@_with_vm_manager_lifetimes
def _inspect_vm_ha_command_status(
    local_config_file: Path,
    *,
    region: str | None = None,
) -> _VMHACommandInspection:
    """Build one strict, non-rendering VM-HA command observation."""

    local_cfg = _load_config_with_region_override(
        local_config_file,
        region=region,
        allow_missing_tunnel_psk_placeholders=True,
    )
    plan = merge_with_peer_configs(local_cfg, [])
    _enforce_command_applicability("vm-ha", plan, local_cfg)
    project_id = str(local_cfg.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project-id-unavailable")
    auth_token = _ensure_authentication(required=False, show_progress=False)
    manager = _own_vm_manager(
        VMManager(
            project_id=project_id,
            region=plan.gateway_group.region,
            auth_token=auth_token,
            tenant_id=str(local_cfg.get("tenant_id") or "").strip() or None,
            region_id=plan.gateway_group.region,
        )
    )
    vm_ips: dict[str, str] = {}
    for instance in plan.iter_instance_configs():
        target = (
            manager.get_vm_public_ip(instance.hostname) or str(instance.external_ip or "").strip()
        )
        if target:
            vm_ips[instance.hostname] = target
    ssh_context = _build_status_ssh_context(
        local_cfg,
        plan,
        vm_ips,
        project_id=project_id,
    )
    snapshot = _collect_vm_ha_status_snapshot(
        local_config_file=local_config_file,
        local_cfg=local_cfg,
        plan=plan,
        project_id=project_id,
        vm_manager=manager,
        vm_ips=vm_ips,
        ssh_context=ssh_context,
        require_local_generation=not has_unresolved_tunnel_psk_placeholders(local_cfg),
    )
    return _VMHACommandInspection(
        snapshot=snapshot,
        project_id=project_id,
        gateway_name=plan.gateway_group.name,
    )


def _vm_ha_local_config_command(command: str, local_config_file: Path) -> str:
    """Return one shell-safe CLI command bound to the current config path."""

    return f"{command} --local-config-file {shlex.quote(str(local_config_file))}"


def _redact_vm_ha_local_config_path(action: str) -> str:
    """Redact one shell-quoted config argument without exposing path fragments."""

    try:
        words = shlex.split(action)
        option_index = words.index("--local-config-file")
        path_index = option_index + 1
        if path_index >= len(words):
            raise ValueError("missing local config path")
    except ValueError:
        return "rerun the reported action with --local-config-file <file>"
    return " ".join(
        "<file>" if index == path_index else shlex.quote(word) for index, word in enumerate(words)
    )


def _render_vm_ha_status(console: t.Any, view: _VMHAStatusView) -> None:
    """Render the sole public VM-HA status section."""

    from rich.table import Table
    from rich.text import Text

    title_style = (
        "bold green"
        if view.overall == "HEALTHY"
        else "bold yellow"
        if view.overall in {"MAINTENANCE", "TRANSITIONING"}
        else "bold red"
    )
    title = Text.assemble("VM-HA Status — ", (view.overall, title_style))
    member_table = Table(title=title, show_header=True, header_style="bold cyan")
    for column in ("Gateway", "Role", "mTLS", "Ready"):
        member_table.add_column(column, style="white")
    for member_row in view.member_rows:
        gateway, role, mtls, ready = member_row
        member_table.add_row(
            gateway,
            role,
            Text(
                mtls,
                style=(
                    "green" if mtls == "healthy" else "yellow" if mtls == "transitioning" else "red"
                ),
            ),
            Text(
                ready,
                style="green" if ready == "yes" else "yellow" if ready == "unknown" else "red",
            ),
        )
    console.print(member_table)

    public_summary = {
        label: (value, detail)
        for label, value, detail in view.summary_rows
        if label in {"Redundancy", "Identity", "Auto-healing", "Action"}
    }
    summary_table = Table(show_header=False, box=None, pad_edge=False)
    summary_table.add_column("Field", style="bold cyan", no_wrap=True)
    summary_table.add_column("Value", no_wrap=True)
    summary_table.add_column("Details", style="white")
    for label in ("Redundancy", "Identity", "Auto-healing"):
        value, detail = public_summary[label]
        value_style = (
            "red"
            if (label, value)
            in {
                ("Redundancy", "maintenance"),
                ("Auto-healing", "disabled"),
            }
            else "white"
        )
        summary_table.add_row(Text(label), Text(value, style=value_style), Text(detail))
    console.print(summary_table)

    action, _action_detail = public_summary["Action"]
    if view.overall != "MAINTENANCE" and "--local-config-file" in action:
        action = _redact_vm_ha_local_config_path(action)
    console.print(
        Text.assemble(("Action", "bold cyan"), "  ", (action, "white")),
        soft_wrap=True,
    )


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
        "Uptime",
    ):
        table.add_column(column, style="white")
    return table


def _safe_status_probe_detail(value: object) -> str:
    """Map raw probe failures to a closed identity-safe diagnostic."""

    text = str(value).lower()
    if "timed out" in text or "timeout" in text:
        return "SSH probe timed out"
    if "host key" in text or "known_hosts" in text:
        return "SSH host verification failed"
    if "connection refused" in text:
        return "SSH connection was refused"
    if "no route to host" in text or "network is unreachable" in text:
        return "gateway network path is unavailable"
    return "gateway status command failed"


def _gateway_tunnel_uptime(
    *,
    bgp_peer_ip: str | None,
    bgp_uptime: t.Mapping[str, str],
    ipsec_uptime: str | None,
) -> str:
    """Use session uptime for BGP and SA uptime for Static/fallback status."""

    if bgp_peer_ip is not None and bgp_peer_ip in bgp_uptime:
        return bgp_uptime[bgp_peer_ip]
    return ipsec_uptime or "n/a"


def _format_uptime(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{days}:{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_bgp_uptime(uptime: str) -> str | None:
    """Normalize usable FRR uptime evidence without inventing zero uptime."""

    value_text = uptime.strip().lower()
    if not value_text or value_text in {"never", "n/a", "unknown", "idle"}:
        return None

    if value_text.isdigit():
        return _format_uptime(int(value_text))

    colon_match = re.match(r"^(\d+):(\d{2}):(\d{2})$", value_text)
    if colon_match:
        hours = int(colon_match.group(1))
        minutes = int(colon_match.group(2))
        seconds = int(colon_match.group(3))
        return _format_uptime(hours * 3600 + minutes * 60 + seconds)

    units_match = re.fullmatch(
        r"(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
        value_text,
    )
    if units_match is None or not any(units_match.groups()):
        return None
    total = sum(
        int(value) * multiplier
        for value, multiplier in zip(
            units_match.groups(),
            (604800, 86400, 3600, 60, 1),
            strict=True,
        )
        if value is not None
    )
    return _format_uptime(total)


def _mark_tunnel_probe_recovered(table: t.Any, hostname: str) -> bool:
    """Replace one exact stale aggregate error after the same probe succeeds."""

    columns = getattr(table, "columns", ())
    if len(columns) != 8:
        return False
    cells = [getattr(column, "_cells", None) for column in columns]
    if any(not isinstance(column_cells, list) for column_cells in cells):
        return False
    for index, gateway in enumerate(t.cast(list[t.Any], cells[2])):
        if str(gateway) != hostname:
            continue
        tunnel = str(t.cast(list[t.Any], cells[0])[index])
        status_value = str(t.cast(list[t.Any], cells[3])[index])
        if tunnel != "All tunnels" or not any(
            marker in status_value for marker in ("ERROR", "TIMEOUT", "PARSE ERROR")
        ):
            continue
        t.cast(list[t.Any], cells[3])[index] = "[yellow]Recovered[/yellow]"
        for column_index in (4, 5, 6, 7):
            t.cast(list[t.Any], cells[column_index])[index] = "-"
        return True
    return False


def _tunnel_probe_retry_has_established_sa(
    probe_command: t.Sequence[str],
    output: str,
) -> bool:
    """Require recognizable established-SA evidence before clearing an error."""

    if not probe_command or not output.strip():
        return False
    remote_command = str(probe_command[-1])
    if "swanctl --list-sas" in remote_command:
        return bool(
            re.search(
                r"(?im)^\s*\S+?:\s+#\d+,.*\bESTABLISHED\b",
                output,
            )
        )
    if "ipsec statusall" in remote_command:
        return bool(
            re.search(
                r"(?im)^\s*\S+\[\d+\]:\s+ESTABLISHED\s+.+?,\s+"
                r"[\d.]+\[[\d.]+\]\.\.\.(?:\d+\.){3}\d+\[",
                output,
            )
        )
    return False


def _mark_service_probe_recovered(
    *,
    service_rows_by_host: t.Mapping[str, dict[str, str]],
    failed_service_details: dict[tuple[str, str], str],
    hostname: str,
    service_name: str,
    returncode: int,
    stdout: str,
) -> bool:
    """Replace only the exact failed service probe after an active retry."""

    services = service_rows_by_host.get(hostname)
    if returncode != 0 or stdout.strip() != "active" or services is None:
        return False
    if service_name not in services:
        return False
    services[service_name] = "[green]active[/green]"
    failed_service_details.pop((hostname, service_name), None)
    return True


@app.command(epilog=_command_help_epilog("status"))
@_with_vm_manager_lifetimes
def status(
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    region: str | None = typer.Option(None, help=_NEBIUS_REGION_HELP),
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
    local_cfg = _load_config_with_region_override(
        local_config_file,
        region=region,
        allow_missing_tunnel_psk_placeholders=True,
    )
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
    require_local_generation = not has_unresolved_tunnel_psk_placeholders(local_cfg)

    # Resolve context from CLI args or config
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    effective_region = plan.gateway_group.region
    region_id = effective_region

    # Get token for API access
    auth_token = _ensure_authentication(required=False, show_progress=True)

    vm_mgr = _own_vm_manager(
        VMManager(
            project_id=proj_id,
            region=effective_region,
            auth_token=auth_token,
            tenant_id=tenant_id,
            region_id=region_id,
        )
    )

    # Quick check: verify at least one gateway VM exists before attempting SSH
    print("[bold]Checking for gateway VMs...[/bold]")
    client = vm_mgr._get_client()
    if client and proj_id:
        try:
            gateway_vms_exist = _configured_gateway_vms_exist(
                client,
                project_id=proj_id,
                instance_names=(instance.hostname for instance in plan.iter_instance_configs()),
            )
        except _GatewayVMDiscoveryError as error:
            console.print("[red]Error: Unable to query configured gateway VMs.[/red]")
            raise typer.Exit(code=1) from error

        if not gateway_vms_exist:
            console.print("[yellow]No configured gateway VMs found.[/yellow]")
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
    status_notes: list[str] = []
    failed_tunnel_probes: dict[str, list[str]] = {}
    vm_ha_snapshot: _VMHAStatusSnapshot | None = None

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

    def parse_strongswan_uptime(uptime_str: str) -> str:
        """Parse strongSwan uptime and return d:h:m:s."""
        seconds = _uptime_seconds(uptime_str)
        if seconds is None:
            return uptime_str.strip()
        return _format_uptime(seconds)

    # Check each gateway VM's tunnels
    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue
        last_tunnel_probe_command: list[str] | None = None

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
                                parsed_uptime = _parse_bgp_uptime(str(uptime_token))
                                if parsed_uptime is not None:
                                    bgp_uptime[ip] = parsed_uptime
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
                                                    if _parse_bgp_uptime(p) is not None
                                                ),
                                                None,
                                            )
                                        if uptime_token and parts[0] not in bgp_uptime:
                                            parsed_uptime = _parse_bgp_uptime(uptime_token)
                                            if parsed_uptime is not None:
                                                bgp_uptime[parts[0]] = parsed_uptime
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
                                parsed_uptime = _parse_bgp_uptime(uptime_token)
                                if parsed_uptime is not None:
                                    bgp_uptime[peer_ip] = parsed_uptime
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # Run swanctl status command (preferred for VICI-based configs)
        try:
            last_tunnel_probe_command = _status_ssh_target_command(
                status_ssh_context,
                hostname=inst_cfg.hostname,
                target=target,
            ) + ["sudo swanctl --list-sas"]
            result = subprocess.run(
                last_tunnel_probe_command,
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
                    render_order = _configured_and_runtime_tunnel_names(
                        inst_cfg.hostname,
                        tunnel_role_map,
                        tunnel_order,
                    )
                    for tunnel_name in render_order:
                        if tunnel_name not in tunnel_statuses:
                            _add_configured_tunnel_without_runtime_row(
                                table,
                                inst_cfg.hostname,
                                tunnel_name,
                                tunnel_role_map,
                                tunnel_peer_map,
                            )
                            continue

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
                        role = _format_configured_tunnel_role(
                            tunnel_role_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                        )
                        enc_algos = tunnel_encryption.get(tunnel_name) or []
                        if not enc_algos:
                            enc_algos = tunnel_ike_encryption.get(tunnel_name) or []
                        encryption_display = ", ".join(enc_algos) if enc_algos else "n/a"
                        uptime_display = _gateway_tunnel_uptime(
                            bgp_peer_ip=peer_cfg_ip,
                            bgp_uptime=bgp_uptime,
                            ipsec_uptime=tunnel_uptime.get(tunnel_name),
                        )

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
            last_tunnel_probe_command = _status_ssh_target_command(
                status_ssh_context,
                hostname=inst_cfg.hostname,
                target=target,
            ) + ["sudo ipsec statusall"]
            result = subprocess.run(
                last_tunnel_probe_command,
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
                    "-",
                )
                status_notes.append(
                    f"{inst_cfg.hostname}: {_safe_status_probe_detail(result.stderr)}"
                )
                failed_tunnel_probes[inst_cfg.hostname] = last_tunnel_probe_command
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
                    "role": _format_configured_tunnel_role(
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
                        "role": _format_configured_tunnel_role(
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
                render_order = _configured_and_runtime_tunnel_names(
                    inst_cfg.hostname,
                    tunnel_role_map,
                    tunnels,
                )
                for tunnel_name in render_order:
                    info = tunnels.get(tunnel_name)
                    if info is None:
                        _add_configured_tunnel_without_runtime_row(
                            table,
                            inst_cfg.hostname,
                            tunnel_name,
                            tunnel_role_map,
                            tunnel_peer_map,
                        )
                        continue

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

                    peer_cfg_ip = tunnel_bgp_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                    info["uptime"] = _gateway_tunnel_uptime(
                        bgp_peer_ip=peer_cfg_ip,
                        bgp_uptime=bgp_uptime,
                        ipsec_uptime=str(info.get("uptime") or "") or None,
                    )

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
                # No runtime tunnels found in output
                if _ipsec_status_reports_no_active_tunnels(output):
                    _add_configured_no_active_tunnel_rows(
                        table,
                        inst_cfg.hostname,
                        tunnel_role_map,
                        tunnel_peer_map,
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
                        "-",
                    )
                    status_notes.append(
                        f"{inst_cfg.hostname}: gateway IPsec status output was not recognized"
                    )

        except subprocess.TimeoutExpired:
            if last_tunnel_probe_command is not None:
                failed_tunnel_probes[inst_cfg.hostname] = last_tunnel_probe_command
            table.add_row(
                "All tunnels",
                "-",
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "-",
                "-",
                "-",
                "-",
            )
            status_notes.append(f"{inst_cfg.hostname}: SSH probe timed out")
        except Exception as e:
            if last_tunnel_probe_command is not None:
                failed_tunnel_probes[inst_cfg.hostname] = last_tunnel_probe_command
            table.add_row(
                "All tunnels",
                "-",
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "-",
                "-",
                "-",
                "-",
            )
            status_notes.append(f"{inst_cfg.hostname}: {_safe_status_probe_detail(e)}")

    if plan.vm_ha is not None:
        vm_ha_snapshot = _collect_vm_ha_status_snapshot(
            local_config_file=local_config_file,
            local_cfg=local_cfg,
            plan=plan,
            project_id=proj_id,
            vm_manager=vm_mgr,
            vm_ips=vm_ips,
            ssh_context=status_ssh_context,
            require_local_generation=require_local_generation,
        )
        recovered_members = {
            member.name
            for member in vm_ha_snapshot.members
            if member.record is not None
            and (
                member.record.get("promotion_ready") is True
                or member.record.get("standby_ready") is True
            )
        }
        for hostname, probe_command in failed_tunnel_probes.items():
            if hostname not in recovered_members:
                continue
            try:
                retry = subprocess.run(
                    probe_command,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if (
                retry.returncode != 0
                or not _tunnel_probe_retry_has_established_sa(
                    probe_command,
                    retry.stdout,
                )
                or not _mark_tunnel_probe_recovered(table, hostname)
            ):
                continue
            status_notes = [note for note in status_notes if not note.startswith(f"{hostname}:")]
            status_notes.append(f"{hostname}: Recovered during this status check")

    console.print(table)
    if status_notes:
        console.print(
            Panel.fit(
                "\n".join(dict.fromkeys(status_notes)),
                title="[yellow]Status notes[/yellow]",
                border_style="yellow",
            )
        )

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
    service_rows: list[tuple[str, dict[str, str]]] = []
    service_rows_by_host: dict[str, dict[str, str]] = {}
    failed_service_probes: dict[tuple[str, str], list[str]] = {}
    failed_service_details: dict[tuple[str, str], str] = {}
    service_notes: list[str] = []

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
            service_probe_command: list[str] | None = None
            try:
                # Special handling for strongSwan - check if charon daemon is running
                if service_name == "strongswan":
                    remote_command = "pgrep -x charon >/dev/null && echo active || echo inactive"
                else:
                    remote_command = f"systemctl is-active {service_name}"
                service_probe_command = _status_ssh_target_command(
                    status_ssh_context,
                    hostname=inst_cfg.hostname,
                    target=target,
                ) + [remote_command]
                result = subprocess.run(
                    service_probe_command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=False,
                )

                status_raw = result.stdout.strip()
                if status_raw == "active":
                    services[service_name] = "[green]active[/green]"
                elif status_raw == "inactive":
                    services[service_name] = "[yellow]inactive[/yellow]"
                else:
                    services[service_name] = f"[red]{status_raw or 'error'}[/red]"
                    failed_service_probes[(inst_cfg.hostname, service_name)] = service_probe_command
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
                            failed_service_details[(inst_cfg.hostname, service_name)] = snippet
                    except Exception:
                        pass

            except Exception:
                services[service_name] = "[red]error[/red]"
                if service_probe_command is not None:
                    failed_service_probes[(inst_cfg.hostname, service_name)] = service_probe_command

        service_rows.append((inst_cfg.hostname, services))
        service_rows_by_host[inst_cfg.hostname] = services

    if failed_service_probes and plan.vm_ha is not None:
        vm_ha_snapshot = _collect_vm_ha_status_snapshot(
            local_config_file=local_config_file,
            local_cfg=local_cfg,
            plan=plan,
            project_id=proj_id,
            vm_manager=vm_mgr,
            vm_ips=vm_ips,
            ssh_context=status_ssh_context,
            require_local_generation=require_local_generation,
        )
        recovered_members = {
            member.name
            for member in vm_ha_snapshot.members
            if member.record is not None
            and (
                member.record.get("promotion_ready") is True
                or member.record.get("standby_ready") is True
            )
        }
        for (hostname, service_name), probe_command in failed_service_probes.items():
            if hostname not in recovered_members:
                continue
            try:
                retry = subprocess.run(
                    probe_command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if not _mark_service_probe_recovered(
                service_rows_by_host=service_rows_by_host,
                failed_service_details=failed_service_details,
                hostname=hostname,
                service_name=service_name,
                returncode=retry.returncode,
                stdout=retry.stdout,
            ):
                continue
            service_notes.append(f"{hostname} {service_name}: Recovered during this status check")

    for (hostname, service_name), snippet in failed_service_details.items():
        print(f"[yellow]{hostname} {service_name} status:[/yellow]\n{snippet}\n")

    for hostname, services in service_rows:
        service_table.add_row(
            hostname,
            services["nebius-vpngw-agent"],
            services["strongswan"],
            services["frr"],
        )

    console.print(service_table)
    if service_notes:
        console.print(
            Panel.fit(
                "\n".join(dict.fromkeys(service_notes)),
                title="[yellow]Service status notes[/yellow]",
                border_style="yellow",
            )
        )

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
                subnet_metadata = getattr(subnet_obj, "metadata", None)
                subnet_spec = getattr(subnet_obj, "spec", None)
                expected_network_id = str(gateway_group_cfg.get("network_id") or "")
                if (
                    not nebius_resource_id(subnet_obj)
                    or str(getattr(subnet_metadata, "parent_id", "") or "") != proj_id
                    or str(getattr(subnet_metadata, "name", "") or "") != gateway_subnet_name
                    or (
                        expected_network_id
                        and str(getattr(subnet_spec, "network_id", "") or "") != expected_network_id
                    )
                ):
                    raise RuntimeError("Configured gateway subnet returned an inexact identity")

                # Get subnet CIDR
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
                    if (
                        nebius_resource_id(rt_obj) != rt_id
                        or str(getattr(rt_meta, "parent_id", "") or "") != proj_id
                    ):
                        raise RuntimeError("Attached route table returned an inexact identity")
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
                    route_items = _list_status_routes(
                        route_client,
                        ListRoutesRequest,
                        route_table_id=rt_id,
                    )

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

            except Exception:
                console.print(
                    f"[yellow]Gateway subnet '{gateway_subnet_name}' route-table inventory is unavailable.[/yellow]"
                )
    except Exception:
        console.print("[yellow]Gateway route-table inventory is unavailable.[/yellow]")

    if plan.vm_ha is not None:
        if vm_ha_snapshot is None:
            vm_ha_snapshot = _collect_vm_ha_status_snapshot(
                local_config_file=local_config_file,
                local_cfg=local_cfg,
                plan=plan,
                project_id=proj_id,
                vm_manager=vm_mgr,
                vm_ips=vm_ips,
                ssh_context=status_ssh_context,
                require_local_generation=require_local_generation,
            )
        _render_vm_ha_status(console, vm_ha_snapshot.view)


@app.command(
    name="add-routes-local",
    epilog=_command_help_epilog("add-routes-local"),
)
def add_routes_local(
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
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
    """Manage ordinary routes or converge installed VM-HA route policy.

    For ordinary gateways, this command selects workload subnets by
    gateway.local_prefixes and adds missing routes through the owning gateway
    private allocation. For explicit VM HA, VPC routes remain controller-owned:
    BGP mode may repair only proven export drift. Static mode waits, without
    writing routes or submitting a repair request, for the autonomous controller
    to reconcile the already-installed exact generation. Both paths first verify
    the installed agent's private capability contract on every affected member.
    Any incomplete route or repair exits nonzero. Use `apply` to deploy local YAML
    changes first.

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
        elif routing_modes == {"static"}:
            print(
                "[dim]VM-HA VPC routes remain controller-owned; skipping legacy "
                "member-primary route mutation.[/dim]"
            )
            print("[bold]Checking installed VM-HA route controller capability...[/bold]")
            convergence = routes.ensure_vm_ha_static_routes_current(
                plan,
                local_cfg,
                lifecycle_state_loader=lambda: _read_vm_ha_route_lifecycle_state(
                    local_config_file,
                    plan,
                    proj_id,
                ),
                on_wait=lambda: print(
                    "[bold]Waiting for controller-owned static-route reconciliation...[/bold]"
                ),
            )
            if convergence is VMHAStaticRouteConvergence.ALREADY_CURRENT:
                print("[green]Static routes already match the installed generation.[/green]")
            else:
                print(
                    "[green]Controller-owned static routes now match the installed "
                    "generation.[/green]"
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
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
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
    # Get token for API access (required for route management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    _ensure_gateway_vms_exist(
        plan,
        project_id=proj_id,
        region=plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
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
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
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
@_with_vm_manager_lifetimes
def destroy(
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    region: str | None = typer.Option(None, help=_NEBIUS_REGION_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Destroy ordinary or VM-HA gateway compute through one resumable workflow.

    The default-No confirmation protects the exact configured gateway scope.
    VPC, subnet, and route-table containers and public IP allocations are
    retained; product-owned routes and private allocations are removed.
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = _load_config_with_region_override(
        local_config_file,
        region=region,
    )

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
    _enforce_command_applicability("destroy", plan, local_cfg)

    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    effective_region = plan.gateway_group.region
    auth_token = _ensure_authentication(required=True, show_progress=True)
    vm_mgr = _own_vm_manager(
        VMManager(
            project_id=proj_id,
            region=effective_region,
            auth_token=auth_token,
            tenant_id=tenant_id,
            region_id=effective_region,
        )
    )

    topology = "VM-HA" if plan.vm_ha is not None else "ordinary"
    if not yes:
        print("\n[yellow]⚠️  WARNING: This will:[/yellow]")
        print(
            f"[yellow]  • Destroy the exact configured {topology} gateway "
            f"({plan.gateway_group.instance_count} VM(s))[/yellow]"
        )
        print("[yellow]  • Delete its boot disks and private IP allocations[/yellow]")
        print("[yellow]  • Delete only product-owned routes to those allocations[/yellow]")
        print("[yellow]  • Terminate all VPN tunnels[/yellow]")
        print("")
        print("[green]  ✓ Preserve the VPC, subnet, and route-table containers[/green]")
        print("[green]  ✓ Preserve public IP allocations for reuse[/green]")
        print("[green]  ✓ Preserve foreign routes and peer/IAM resources[/green]")
        print("")
        sys.stdout.write("\033[1mProceed with destruction? [y/N]:\033[0m ")
        sys.stdout.flush()
        try:
            response = input().strip().lower()
        except EOFError:
            response = ""
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)

    if not proj_id:
        print("[red]Destroy requires a project ID from config or --project-id.[/red]")
        raise typer.Exit(code=1)

    print("[bold]Planning and executing exact gateway destruction...[/bold]")
    try:
        result = execute_destroy(
            config_path=local_config_file,
            config_digest=_canonical_digest(local_cfg),
            spec=plan.gateway_group,
            project_id=proj_id,
            vm_manager=vm_mgr,
            local_prefixes=plan.gateway.get("local_prefixes"),
        )
    except Exception as error:
        print("[red]Destroy failed safely.[/red]")
        print(f"[yellow]Reason: {_safe_destroy_reason(error)}[/yellow]")
        print(
            "[yellow]Next: rerun this exact destroy command to resume its durable "
            "checkpoint. If the same reason repeats, inspect the matching cloud "
            "operation and lifecycle checkpoint.[/yellow]"
        )
        raise typer.Exit(code=1) from error

    print()
    if result.already_absent:
        print("[green]✓ Gateway resources were already absent and are now verified.[/green]")
    else:
        print("[green]✓ Destroy completed and verified successfully.[/green]")
    print(
        "[dim]Verified absent scope: "
        f"{result.deleted_compute} VM(s), {result.deleted_disks} disk(s), "
        f"{result.deleted_routes} route(s), "
        f"{result.deleted_allocations} private allocation(s).[/dim]"
    )
    print("[dim]Preserved resources:[/dim]")
    print("[dim]  • VPC, subnet, and route-table containers[/dim]")
    print("[dim]  • Public IP allocations[/dim]")


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
    Restart an IPsec tunnel on a regular gateway (non-HA).

    This command is supported only on regular gateways (non-HA). It connects
    to the owning VM via SSH,
    restarts the matching IPsec tunnel, and clears the matching BGP neighbor
    when the tunnel uses BGP. Useful for immediate recovery from tunnel and
    control-plane desync or after network maintenance. It is unsupported for
    VM-HA-enabled gateways, whose controller owns data-plane repair; use
    status to inspect health and apply only for configuration convergence.
    In ordinary multi-VM and multi-connection topologies, a named tunnel only
    targets its owning connection/instance.

    """
    try:
        # Resolve config path
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        local_cfg = load_local_config(config_path)
        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        try:
            _enforce_command_applicability("restart-tunnel", plan, local_cfg)
        except typer.BadParameter as error:
            typer.echo(error.message, err=True)
            raise typer.Exit(code=1) from None
        print(f"[bold]Loading config from:[/bold] {config_path}")

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
        client_auth = _gateway_ssh_client_auth(local_cfg)

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

        ssh_policy = _existing_gateway_ssh_policy(
            local_cfg,
            plan,
            tuple(
                (instance.hostname, str(instance.external_ip or "").strip() or instance.hostname)
                for instance in target_instances
            ),
        )

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
            ssh_cmd = _build_ssh_base_cmd(
                key_path,
                client_auth=client_auth,
                ssh_policy=ssh_policy,
                hostname=hostname,
            )
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
    """Fail over a tunnel path; supported only on regular gateways (non-HA) using BGP, not Static routing."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        local_cfg = load_local_config(config_path)
        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        try:
            _enforce_command_applicability("failover tunnel", plan, local_cfg)
        except typer.BadParameter as error:
            typer.echo(error.message, err=True)
            raise typer.Exit(code=1) from None
        print(f"[bold]Loading config from:[/bold] {config_path}")

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
        client_auth = _gateway_ssh_client_auth(local_cfg)

        print(
            f"[bold]Failing over connection '{conn_name}' on {target_instance.hostname}:[/bold] "
            f"{active.get('name')} → {target.get('name')}"
        )

        cmd = (
            f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
            f"-c 'neighbor {active_peer_ip} shutdown'"
        )
        ssh_policy = _existing_gateway_ssh_policy(
            local_cfg,
            plan,
            ((target_instance.hostname, str(target_instance.external_ip)),),
        )
        ssh_cmd = _build_ssh_base_cmd(
            key_path,
            client_auth=client_auth,
            ssh_policy=ssh_policy,
            hostname=target_instance.hostname,
        )
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

        ssh_base = _build_ssh_base_cmd(
            key_path,
            client_auth=client_auth,
            ssh_policy=ssh_policy,
            hostname=target_instance.hostname,
        )
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
    """Restore a tunnel path; supported only on regular gateways (non-HA) using BGP, not Static routing."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        local_cfg = load_local_config(config_path)
        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        try:
            _enforce_command_applicability("failback tunnel", plan, local_cfg)
        except typer.BadParameter as error:
            typer.echo(error.message, err=True)
            raise typer.Exit(code=1) from None
        print(f"[bold]Loading config from:[/bold] {config_path}")

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
        client_auth = _gateway_ssh_client_auth(local_cfg)

        print(
            f"[bold]Failing back connection '{conn_name}' on {target_instance.hostname}:[/bold] "
            f"restore {target.get('name')}"
        )

        cmd = (
            f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
            f"-c 'no neighbor {active_peer_ip} shutdown'"
        )
        ssh_policy = _existing_gateway_ssh_policy(
            local_cfg,
            plan,
            ((target_instance.hostname, str(target_instance.external_ip)),),
        )
        ssh_cmd = _build_ssh_base_cmd(
            key_path,
            client_auth=client_auth,
            ssh_policy=ssh_policy,
            hostname=target_instance.hostname,
        )
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

        ssh_base = _build_ssh_base_cmd(
            key_path,
            client_auth=client_auth,
            ssh_policy=ssh_policy,
            hostname=target_instance.hostname,
        )
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
    client_auth = _gateway_ssh_client_auth(local_cfg)
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
            client_auth=client_auth,
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
            raise _VMHARemoteAgentUnavailable(
                "VM-HA remote agent action failed; run status and inspect VM-HA service journals"
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


def _run_vm_ha_auto_healing_action(
    *,
    local_config_file: Path,
    action: t.Literal[
        "status",
        "initialize",
        "adopt-replacement",
        "prepare",
        "commit",
        "arm-recovery",
        "cancel-recovery",
        "clear-recovery",
    ],
    requests: t.Mapping[str, str] | None = None,
    node_ids: frozenset[str] | None = None,
    require_capability: bool = False,
    timeout_seconds: float = 30.0,
) -> list[dict[str, t.Any]]:
    """Run one strict private policy action on both exact members."""

    local_cfg = load_local_config(local_config_file)
    plan = merge_with_peer_configs(local_cfg, [])
    if plan.vm_ha is None:
        raise typer.BadParameter("VM HA is not enabled in this configuration")
    ssh_policy = require_vm_ha_ssh_policy(
        tuple(
            (instance.hostname, (instance.external_ip or "").strip() or instance.hostname)
            for instance in plan.iter_instance_configs()
        ),
        enrollment_hosts=(),
        trust_scope=_vm_ha_ssh_trust_scope(local_cfg, plan),
    )
    vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    client_auth = _gateway_ssh_client_auth(local_cfg)
    results: list[dict[str, t.Any]] = []
    for instance in _vm_ha_apply_order(plan):
        node = instance.vm_ha_node
        generation = instance.vm_ha_generation
        if node is None or generation is None:
            raise ValueError("VM-HA policy action requires complete member manifests")
        if node_ids is not None and node.node_id not in node_ids:
            continue
        target = (instance.external_ip or "").strip()
        if not target:
            raise RuntimeError("a VM-HA member has no SSH target")
        if require_capability:
            capability_command = _build_ssh_base_cmd(
                key_path,
                client_auth=client_auth,
                ssh_policy=ssh_policy,
                hostname=instance.hostname,
            )
            capability_command.extend(
                [
                    "-o",
                    "BatchMode=yes",
                    f"{username}@{target}",
                    "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities",
                ]
            )
            capability = subprocess.run(
                capability_command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            try:
                capability_payload = json.loads(capability.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "an installed VM-HA member returned invalid capability evidence"
                ) from error
            features = (
                capability_payload.get("features") if isinstance(capability_payload, dict) else None
            )
            if not (
                capability.returncode == 0
                and isinstance(capability_payload, dict)
                and capability_payload.get("schema") == _AGENT_CAPABILITIES_SCHEMA
                and isinstance(features, list)
                and AUTO_HEALING_CAPABILITY in features
                and STANDBY_RESTORATION_CAPABILITY in features
            ):
                raise RuntimeError(
                    "both installed VM-HA members must support standby auto-healing "
                    "policy and standby restoration"
                )
        command = _build_ssh_base_cmd(
            key_path,
            client_auth=client_auth,
            ssh_policy=ssh_policy,
            hostname=instance.hostname,
        )
        command.extend(
            [
                "-o",
                "BatchMode=yes",
                f"{username}@{target}",
                "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
                f"--vm-ha-auto-healing-action {action}"
                + (
                    " --vm-ha-auto-healing-request " + shlex.quote(requests[node.node_id])
                    if requests is not None and node.node_id in requests
                    else ""
                ),
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
            raise RuntimeError("a VM-HA member rejected the standby auto-healing policy action")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("a VM-HA member returned invalid policy evidence") from error
        if action == "initialize":
            required_initialize = {
                "cluster_id",
                "configured_role",
                "decision_digest",
                "desired",
                "generation_id",
                "node_id",
                "operation_id",
                "phase",
                "schema",
            }
            if not (
                isinstance(payload, dict)
                and set(payload) == required_initialize
                and payload.get("schema") == "nebius-vpngw/vm-ha-auto-healing-initialize-result-v1"
                and payload.get("cluster_id") == plan.vm_ha.cluster_id
                and payload.get("node_id") == node.node_id
                and payload.get("configured_role") == node.role.value
                and payload.get("generation_id") == generation.generation_id
                and payload.get("desired") == StandbyAutoHealing.ENABLED.value
                and payload.get("phase") == "committed"
            ):
                raise RuntimeError(
                    "a VM-HA member returned stale or foreign policy initialization evidence"
                )
            results.append(t.cast(dict[str, t.Any], payload))
            continue
        required = {
            "accepted_start",
            "cluster_id",
            "configured_role",
            "decision_digest",
            "desired",
            "generation_id",
            "node_id",
            "operation_id",
            "peer_agrees",
            "phase",
            "record",
            "recovery",
            "recovery_authority",
            "recovery_phase",
            "schema",
        }
        if not (
            isinstance(payload, dict)
            and set(payload) == required
            and payload.get("schema") == AUTO_HEALING_STATUS_SCHEMA
            and payload.get("cluster_id") == plan.vm_ha.cluster_id
            and payload.get("node_id") == node.node_id
            and payload.get("configured_role") == node.role.value
            and payload.get("generation_id") == generation.generation_id
            and isinstance(payload.get("peer_agrees"), bool)
            and isinstance(payload.get("accepted_start"), bool)
        ):
            raise RuntimeError("a VM-HA member returned stale or foreign policy evidence")
        if payload["record"] is not None:
            record = AutoHealingPolicyRecord.from_mapping(payload["record"])
            if not (
                record.node_id == node.node_id
                and record.generation_id == generation.generation_id
                and payload["desired"] == record.desired.value
                and payload["phase"] == record.phase.value
                and payload["operation_id"] == record.operation_id
                and payload["decision_digest"] == record.decision_digest
            ):
                raise RuntimeError("a VM-HA member returned inconsistent policy evidence")
        recovery = payload["recovery"]
        if recovery is not None:
            parsed_recovery = AutoHealingRecoveryRecord.from_mapping(recovery)
            if not (
                parsed_recovery.node_id == node.node_id
                and parsed_recovery.generation_id == generation.generation_id
                and payload["recovery_phase"] == parsed_recovery.phase.value
            ):
                raise RuntimeError("a VM-HA member returned inconsistent recovery evidence")
        elif payload["recovery_phase"] is not None:
            raise RuntimeError("a VM-HA member returned incomplete recovery evidence")
        recovery_authority = payload["recovery_authority"]
        if recovery_authority is not None and not (
            isinstance(recovery_authority, dict)
            and set(recovery_authority)
            == {"allocation_id", "ownership_epoch", "promotion_receipt_id"}
            and all(
                isinstance(recovery_authority[key], str) and recovery_authority[key]
                for key in recovery_authority
            )
        ):
            raise RuntimeError("a VM-HA member returned invalid recovery authority")
        results.append(t.cast(dict[str, t.Any], payload))
    return results


def _vm_ha_default_initialized_enabled_policy(
    record: AutoHealingPolicyRecord,
) -> bool:
    """Return whether one record is the deterministic initial enabled decision."""

    expected_operation_id = _canonical_digest(
        {
            "cluster_id": record.cluster_id,
            "desired": StandbyAutoHealing.ENABLED.value,
            "generation_id": record.generation_id,
            "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-v2",
        }
    )
    return bool(
        record.desired is StandbyAutoHealing.ENABLED
        and record.phase is AutoHealingPolicyPhase.COMMITTED
        and record.operation_id == expected_operation_id
        and record.predecessor_digest == "0" * 64
    )


def _vm_ha_replacement_policy_adoption_request(
    *,
    config_path: Path,
    owner_node_id: str,
    apply_operation_id: str,
    mtls_apply_operation_id: str | None,
    mtls_inhibition_operation_id: str | None,
) -> str:
    """Bind one pre-activation adoption request to the retained owner and apply lock."""

    statuses = _run_vm_ha_auto_healing_action(
        local_config_file=config_path,
        action="status",
        node_ids=frozenset({owner_node_id}),
        require_capability=True,
    )
    if len(statuses) != 1 or statuses[0].get("node_id") != owner_node_id:
        raise RuntimeError("replacement policy owner evidence is unavailable")
    status = statuses[0]
    owner = AutoHealingPolicyRecord.from_mapping(status.get("record"))
    owner_decision_adoptable = bool(
        owner.peer_ack_digest == owner.decision_digest
        or (owner.peer_ack_digest is None and _vm_ha_default_initialized_enabled_policy(owner))
    )
    if not (
        owner.node_id == owner_node_id
        and owner.desired is StandbyAutoHealing.ENABLED
        and owner.phase is AutoHealingPolicyPhase.COMMITTED
        and owner_decision_adoptable
        and status.get("accepted_start") is False
        and status.get("recovery") is None
        and status.get("recovery_phase") is None
    ):
        raise RuntimeError("replacement policy owner is not terminal and quiescent")
    return encode_policy_request(
        {
            "schema": AUTO_HEALING_REQUEST_SCHEMA,
            "apply_operation_id": apply_operation_id,
            "mtls_apply_operation_id": mtls_apply_operation_id,
            "mtls_inhibition_operation_id": mtls_inhibition_operation_id,
            "operation_id": owner.operation_id,
            "peer_record": owner.to_dict(),
        }
    )


def _vm_ha_replacement_policy_reproof_transaction(
    *,
    owner: AutoHealingPolicyRecord,
    replacement: AutoHealingPolicyRecord,
) -> "_VMHAAutoHealingTransaction | None":
    """Admit only the deterministic default-to-reproof replacement lineage."""

    member_node_ids = tuple(sorted((owner.node_id, replacement.node_id)))
    coordinator_node_id = member_node_ids[0]
    default_operation_id = _canonical_digest(
        {
            "cluster_id": owner.cluster_id,
            "desired": StandbyAutoHealing.ENABLED.value,
            "generation_id": owner.generation_id,
            "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-v2",
        }
    )
    default_decision_digest = policy_decision_digest(
        cluster_id=owner.cluster_id,
        member_node_ids=t.cast(tuple[str, str], member_node_ids),
        generation_id=owner.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=default_operation_id,
        coordinator_node_id=coordinator_node_id,
        predecessor_digest="0" * 64,
    )
    transaction = _VMHAAutoHealingTransaction(
        operation_id=_canonical_digest(
            {
                "cluster_id": owner.cluster_id,
                "coordinator_node_id": coordinator_node_id,
                "desired": StandbyAutoHealing.ENABLED.value,
                "generation_id": owner.generation_id,
                "member_node_ids": list(member_node_ids),
                "predecessor_digest": default_decision_digest,
                "schema": "nebius-vpngw/vm-ha-auto-healing-transaction-v2",
            }
        ),
        coordinator_node_id=coordinator_node_id,
        predecessor_digest=default_decision_digest,
        member_node_ids=t.cast(tuple[str, str], member_node_ids),
    )

    def is_exact_default(record: AutoHealingPolicyRecord) -> bool:
        expected_ack = None if record.node_id == owner.node_id else record.decision_digest
        return bool(
            _vm_ha_default_initialized_enabled_policy(record)
            and record.decision_digest == default_decision_digest
            and record.peer_ack_digest == expected_ack
        )

    def is_exact_reproof(record: AutoHealingPolicyRecord) -> bool:
        expected_ack = (
            None if record.phase is AutoHealingPolicyPhase.PREPARED else record.decision_digest
        )
        return bool(
            record.desired is StandbyAutoHealing.ENABLED
            and record.operation_id == transaction.operation_id
            and record.coordinator_node_id == transaction.coordinator_node_id
            and record.predecessor_digest == transaction.predecessor_digest
            and record.phase in {AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED}
            and record.peer_ack_digest == expected_ack
        )

    records = {owner.node_id: owner, replacement.node_id: replacement}
    record_kinds = {
        node_id: "default"
        if is_exact_default(record)
        else "reproof"
        if is_exact_reproof(record)
        else "invalid"
        for node_id, record in records.items()
    }
    if "invalid" in record_kinds.values():
        return None
    if set(record_kinds.values()) == {"default"}:
        return transaction
    if record_kinds[coordinator_node_id] != "reproof":
        return None
    peer_node_id = next(node_id for node_id in member_node_ids if node_id != coordinator_node_id)
    coordinator = records[coordinator_node_id]
    peer = records[peer_node_id]
    if record_kinds[peer_node_id] == "default":
        return transaction if coordinator.phase is AutoHealingPolicyPhase.PREPARED else None
    allowed_phases = {
        (AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.PREPARED),
        (AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED),
        (AutoHealingPolicyPhase.COMMITTED, AutoHealingPolicyPhase.COMMITTED),
    }
    return transaction if (coordinator.phase, peer.phase) in allowed_phases else None


def _reconcile_vm_ha_replacement_auto_healing_policy(
    *,
    config_path: Path,
    owner_node_id: str,
) -> None:
    """Verify that pre-activation replacement adoption durably converged."""

    statuses = _run_vm_ha_auto_healing_action(
        local_config_file=config_path,
        action="status",
        require_capability=True,
    )
    if len(statuses) != 2:
        raise RuntimeError("replacement policy reconciliation requires exactly two members")
    by_node = {str(status.get("node_id")): status for status in statuses}
    if owner_node_id not in by_node or len(by_node) != 2:
        raise RuntimeError("replacement policy reconciliation owner is unavailable")
    replacement_node_id = next(node_id for node_id in by_node if node_id != owner_node_id)
    owner_status = by_node[owner_node_id]
    replacement_status = by_node[replacement_node_id]
    owner = AutoHealingPolicyRecord.from_mapping(owner_status.get("record"))
    replacement = AutoHealingPolicyRecord.from_mapping(replacement_status.get("record"))
    if not (
        owner.node_id == owner_node_id
        and owner.peer_node_id == replacement_node_id
        and replacement.node_id == replacement_node_id
        and replacement.peer_node_id == owner_node_id
        and owner.cluster_id == replacement.cluster_id
        and owner.generation_id == replacement.generation_id
        and owner.desired is StandbyAutoHealing.ENABLED
        and replacement.desired is StandbyAutoHealing.ENABLED
        and owner.phase in {AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED}
        and replacement.phase in {AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED}
        and owner_status.get("accepted_start") is False
        and replacement_status.get("accepted_start") is False
        and owner_status.get("recovery") is None
        and replacement_status.get("recovery") is None
        and owner_status.get("recovery_phase") is None
        and replacement_status.get("recovery_phase") is None
    ):
        raise RuntimeError("replacement policy reconciliation evidence is not quiescent and exact")

    def records_agree() -> bool:
        return bool(
            owner.phase is AutoHealingPolicyPhase.COMMITTED
            and replacement.phase is AutoHealingPolicyPhase.COMMITTED
            and owner.operation_id == replacement.operation_id
            and owner.coordinator_node_id == replacement.coordinator_node_id
            and owner.predecessor_digest == replacement.predecessor_digest
            and owner.decision_digest == replacement.decision_digest
            and owner.peer_ack_digest == owner.decision_digest
            and replacement.peer_ack_digest == replacement.decision_digest
        )

    if records_agree():
        return
    transaction = _vm_ha_replacement_policy_reproof_transaction(
        owner=owner,
        replacement=replacement,
    )
    if transaction is not None:
        converged = _execute_vm_ha_auto_healing_policy(
            config_path=config_path,
            desired=StandbyAutoHealing.ENABLED,
            transaction=transaction,
            initial_statuses=statuses,
        )
        if _vm_ha_auto_healing_is_terminal(converged, StandbyAutoHealing.ENABLED):
            return
    raise RuntimeError("replacement policy adoption did not durably converge")


_VM_HA_PLANNED_CUTOVER_TIMEOUT_SECONDS = 600.0
_VM_HA_PLANNED_RESTORATION_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class _VMHAPlannedTerminalContext:
    target_role: str
    former_role: str
    target_member: t.Any
    former_member: t.Any
    target_owner: AllocationOwner
    allocation_id: str
    runtime_binding: t.Any
    status_reader: t.Callable[[], dict[str, t.Any]]
    standby_status_reader: t.Callable[[], dict[str, t.Any]]
    cloud_reader: t.Callable[[], t.Any]
    request_timeout_seconds: float
    cutover_timeout_seconds: float
    restoration_timeout_seconds: float


@dataclass(frozen=True)
class _VMHAPlannedTransferCompletion:
    cutover_seconds: float
    restoration_seconds: float
    total_seconds: float


class _VMHAPlannedTerminalObservationUnavailable(RuntimeError):
    """One retry-safe terminal observer was unavailable within the phase budget."""

    def __init__(self, source: t.Literal["target-agent", "standby-agent", "cloud"]):
        super().__init__("terminal observation unavailable")
        self.source = source


class _VMHAPlannedCutoverVerificationUnavailable(RuntimeError):
    """The cutover outcome could not be verified before its deadline."""

    def __init__(self, *, elapsed_seconds: float) -> None:
        super().__init__("terminal cutover observation remained unavailable")
        self.elapsed_seconds = elapsed_seconds


class _VMHAPlannedCutoverVerificationIncomplete(RuntimeError):
    """Exact controller reproof did not reach terminal proof before its deadline."""

    def __init__(self, *, elapsed_seconds: float, budget_seconds: float) -> None:
        super().__init__("terminal cutover evidence did not stabilize")
        self.elapsed_seconds = elapsed_seconds
        self.budget_seconds = budget_seconds


class _VMHAPlannedRestorationVerificationUnavailable(RuntimeError):
    """Cutover committed, but restored redundancy could not be verified."""

    def __init__(
        self,
        *,
        cutover_seconds: float,
        restoration_seconds: float,
        total_seconds: float,
    ) -> None:
        super().__init__("terminal observation remained unavailable")
        self.cutover_seconds = cutover_seconds
        self.restoration_seconds = restoration_seconds
        self.total_seconds = total_seconds


class _VMHAPlannedRedundancyRestorationError(RuntimeError):
    """Cutover committed safely, but terminal standby restoration did not."""

    def __init__(
        self,
        message: str,
        *,
        cutover_seconds: float,
        restoration_seconds: float,
        total_seconds: float,
        background_continues: bool = False,
    ) -> None:
        super().__init__(message)
        self.cutover_seconds = cutover_seconds
        self.restoration_seconds = restoration_seconds
        self.total_seconds = total_seconds
        self.background_continues = background_continues


def _read_vm_ha_planned_terminal_agent(
    reader: t.Callable[[], list[dict[str, t.Any]]],
    *,
    source: t.Literal["target-agent", "standby-agent"],
    mismatch_message: str,
) -> dict[str, t.Any]:
    """Translate only retry-safe terminal agent read failures to a closed type."""

    try:
        records = reader()
    except (
        OSError,
        subprocess.TimeoutExpired,
        _VMHAAgentStatusStale,
        _VMHARemoteAgentUnavailable,
    ):
        raise _VMHAPlannedTerminalObservationUnavailable(source) from None
    if len(records) != 1:
        raise RuntimeError(mismatch_message)
    return records[0]


def _read_vm_ha_planned_terminal_cloud(reader: t.Callable[[], t.Any]) -> t.Any:
    """Translate retryable or ambiguous read-only cloud loss to a closed type."""

    try:
        return reader()
    except (
        TimeoutError,
        ConnectionError,
        RetryableHACloudError,
        AmbiguousHACloudError,
    ):
        raise _VMHAPlannedTerminalObservationUnavailable("cloud") from None


@dataclass(frozen=True)
class _VMHAPlannedPreparation:
    outcome: t.Literal["already-owner", "standby-ready", "standby-ssh-ready"]
    target_role: str
    record: dict[str, t.Any]
    terminal_context: _VMHAPlannedTerminalContext | None = None


class _VMHAOutputFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


def _vm_ha_planned_cutover_status_matches(
    record: t.Mapping[str, t.Any],
    *,
    context: _VMHAPlannedTerminalContext,
) -> bool:
    return bool(
        record.get("promotion_committed") is True
        and record.get("state") == "active"
        and record.get("promotion_ready") is True
        and record.get("data_plane_mode") == "active"
        and record.get("observed_owner_node_id") == context.target_member.node_id
        and record.get("former_owner_compute_state")
        in {
            InstanceCloudState.STOPPED.value,
            InstanceCloudState.TRANSITIONAL.value,
            InstanceCloudState.RUNNING.value,
        }
        and record.get("former_attachment_absent") is True
        and record.get("candidate_attachment_exact") is True
        and record.get("ownership_re_read_exact") is True
        and record.get("apply_locked") is False
        and record.get("pending_operation_id") is None
        and record.get("guard_boot_id") == record.get("controller_ready_boot_id")
        and isinstance(record.get("guard_boot_id"), str)
        and bool(record["guard_boot_id"])
        and _vm_ha_active_route_receipt_matches(
            record,
            active_node_id=context.target_member.node_id,
            runtime_binding=context.runtime_binding,
        )
    )


def _vm_ha_planned_cutover_cloud_matches(
    observation: t.Any,
    *,
    context: _VMHAPlannedTerminalContext,
) -> bool:
    by_role = {"active": observation.former, "passive": observation.candidate}
    target = by_role[context.target_role]
    former = by_role[context.former_role]
    return bool(
        observation.allocation.owner == context.target_owner
        and target.state is InstanceCloudState.RUNNING
        and target.has_alias_allocation(
            context.target_member.network_interface_name,
            context.allocation_id,
        )
        and former.state
        in {
            InstanceCloudState.STOPPED,
            InstanceCloudState.TRANSITIONAL,
            InstanceCloudState.RUNNING,
        }
        and not former.has_alias_allocation(
            context.former_member.network_interface_name,
            context.allocation_id,
        )
    )


def _vm_ha_planned_owner_redundancy_matches(
    record: t.Mapping[str, t.Any],
    *,
    context: _VMHAPlannedTerminalContext,
) -> bool:
    return bool(
        _vm_ha_planned_cutover_status_matches(record, context=context)
        and record.get("rearm_phase") == "running"
        and record.get("redundancy_ready") is True
        and record.get("former_owner_compute_state") == InstanceCloudState.RUNNING.value
    )


def _vm_ha_planned_standby_matches(
    record: t.Mapping[str, t.Any],
    *,
    context: _VMHAPlannedTerminalContext,
) -> bool:
    return bool(
        record.get("state") == "normal"
        and record.get("standby_ready") is True
        and record.get("standby_readiness_reasons") == []
        and record.get("data_plane_mode") == "passive"
        and record.get("observed_owner_node_id") == context.target_member.node_id
        and record.get("apply_locked") is False
        and record.get("pending_operation_id") is None
    )


def _vm_ha_planned_restored_cloud_matches(
    observation: t.Any,
    *,
    context: _VMHAPlannedTerminalContext,
) -> bool:
    by_role = {"active": observation.former, "passive": observation.candidate}
    target = by_role[context.target_role]
    former = by_role[context.former_role]
    return bool(
        observation.allocation.owner == context.target_owner
        and target.state is InstanceCloudState.RUNNING
        and target.has_alias_allocation(
            context.target_member.network_interface_name,
            context.allocation_id,
        )
        and former.state is InstanceCloudState.RUNNING
        and not former.has_alias_allocation(
            context.former_member.network_interface_name,
            context.allocation_id,
        )
    )


_VM_HA_TRANSFER_PHASE_LABELS = {
    "stop-former-owner": "stopping current owner",
    "detach-former-attachment": "unassigning shared IP",
    "detach-candidate-for-reproof": "unassigning shared IP for ownership reproof",
    "attach-candidate": "assigning shared IP to the target VM",
    "confirm-candidate-ownership": "confirming shared IP ownership",
    "prepare-candidate-dataplane": "establishing VPN",
    "reconcile-routes": "reconciling routes",
    "enable-active": "enabling forwarding",
}

_VM_HA_CONTROLLER_RECOVERY_GUIDANCE = (
    "Forwarding remains fenced. Run 'nebius-vpngw status --local-config-file <file>' "
    "with the same SSH trust configuration, then inspect "
    "'sudo journalctl -u nebius-vpngw-vm-ha.service' on the target VM."
)


@dataclass(frozen=True)
class _VMHAPlannedProgressObservation:
    """Exact request progress that is safe for presentation and retry waiting."""

    attempts: tuple[tuple[int, str], ...]
    retryable_failure: tuple[int, str] | None
    latest_action: str
    latest_state: str
    reproof_started: bool


def _vm_ha_planned_restoration_phase(record: t.Mapping[str, t.Any]) -> str | None:
    """Map current rearm/Compute evidence to one truthful restoration phase."""

    rearm_phase = record.get("rearm_phase")
    former_state = record.get("former_owner_compute_state")
    if rearm_phase == "starting" or former_state == InstanceCloudState.TRANSITIONAL.value:
        return "starting former owner as standby"
    if rearm_phase == "running" or former_state == InstanceCloudState.RUNNING.value:
        return "waiting for standby readiness"
    return None


def _vm_ha_planned_progress_observation(
    record: t.Mapping[str, t.Any],
    *,
    context: _VMHAPlannedTerminalContext,
    request_fingerprint: str,
    after_sequence: int,
) -> _VMHAPlannedProgressObservation | None:
    """Return exact phase/retry evidence, or ``None`` for invalid evidence."""

    raw = record.get("transfer_progress")
    if not isinstance(raw, t.Mapping):
        return None
    try:
        progress = validate_transfer_progress(raw)
    except (TypeError, ValueError):
        return None
    expected_intent = "planned-failback" if context.target_role == "active" else "planned-failover"
    binding = context.runtime_binding
    expected_digests = {
        "configuration": getattr(binding, "configuration_digest", None),
        "static_routes": getattr(binding, "static_routes_digest", None),
        "bgp_policy": getattr(binding, "bgp_policy_digest", None),
    }
    if not (
        progress["candidate_node_id"] == context.target_member.node_id
        and progress["former_owner_node_id"] == context.former_member.node_id
        and progress["allocation_id"] == context.allocation_id
        and progress["generation_id"] == getattr(binding, "generation_id", None)
        and progress["digests"] == expected_digests
        and progress["route_runtime_id"] == getattr(binding, "route_runtime_id", None)
        and progress["intent"] == expected_intent
        and progress["request_fingerprint"] == request_fingerprint
    ):
        return None
    attempts = tuple(
        (int(entry["sequence"]), _VM_HA_TRANSFER_PHASE_LABELS[str(entry["action"])])
        for entry in progress["history"]
        if entry["state"] == "attempting" and int(entry["sequence"]) > after_sequence
    )
    latest = progress["history"][-1]
    pending_operation_id = record.get("pending_operation_id")
    pending_parts = (
        pending_operation_id.rsplit(":", 3) if isinstance(pending_operation_id, str) else []
    )
    pending_action = _vm_ha_pending_action_kind(
        pending_operation_id,
        member_node_ids=frozenset({context.target_member.node_id, context.former_member.node_id}),
    )
    retryable_failure = None
    if (
        latest["state"] == "failed"
        and latest["error_type"] == "effect-failed"
        and pending_operation_id == latest["operation_id"]
        and pending_action is not None
        and pending_action[0] == latest["action"]
        and pending_parts[0] == latest["boot_id"]
    ):
        retryable_failure = (
            int(latest["sequence"]),
            _VM_HA_TRANSFER_PHASE_LABELS[str(latest["action"])],
        )
    return _VMHAPlannedProgressObservation(
        attempts=attempts,
        retryable_failure=retryable_failure,
        latest_action=str(latest["action"]),
        latest_state=str(latest["state"]),
        reproof_started=any(
            entry["action"] == "detach-candidate-for-reproof" for entry in progress["history"]
        ),
    )


_VM_HA_PLANNED_REPROOF_ACTIONS = frozenset(
    {
        "detach-candidate-for-reproof",
        "attach-candidate",
        "confirm-candidate-ownership",
        "prepare-candidate-dataplane",
        "reconcile-routes",
        "enable-active",
    }
)
_VM_HA_PLANNED_REPROOF_FENCE_REASONS = frozenset(
    {
        "local-ownership-lacks-establishment-proof",
        "active-node-lacks-exact-allocation-ownership",
    }
)


def _vm_ha_planned_reproof_converging(
    record: t.Mapping[str, t.Any],
    *,
    context: _VMHAPlannedTerminalContext,
    request_fingerprint: str | None,
    progress: _VMHAPlannedProgressObservation | None = None,
) -> bool:
    """Recognize only the exact current-request controller ownership reproof path."""

    if (
        request_fingerprint is None
        or record.get("promotion_committed") is not False
        or record.get("apply_locked") is not False
    ):
        return False
    if progress is None:
        progress = _vm_ha_planned_progress_observation(
            record,
            context=context,
            request_fingerprint=request_fingerprint,
            after_sequence=0,
        )
    if progress is None:
        return False

    state = record.get("state")
    data_plane_mode = record.get("data_plane_mode")
    reasons = record.get("reasons")
    pending = _vm_ha_pending_action_kind(
        record.get("pending_operation_id"),
        member_node_ids=frozenset({context.target_member.node_id, context.former_member.node_id}),
    )
    if (
        state == "blocked"
        and data_plane_mode == "active"
        and isinstance(reasons, list)
        and len(reasons) == 1
        and reasons[0] in _VM_HA_PLANNED_REPROOF_FENCE_REASONS
    ):
        return pending == ("disable-active", context.target_member.node_id)
    if (
        state == "normal"
        and data_plane_mode in {"blocked", "passive"}
        and reasons == ["non-owner-must-remain-passive"]
    ):
        return pending == ("enter-passive", context.target_member.node_id)
    if not progress.reproof_started:
        return False
    if pending is not None:
        action, target_node_id = pending
        if not (
            target_node_id == context.target_member.node_id
            and action in _VM_HA_PLANNED_REPROOF_ACTIONS
            and action in _VM_HA_PENDING_ACTIONS_BY_STATE.get(str(state), ())
        ):
            return False
        if action == "enable-active":
            return data_plane_mode in {"blocked", "passive", "active"}
        return data_plane_mode in {"blocked", "passive"}
    if progress.latest_state != "completed":
        return False
    if progress.latest_action == "enable-active":
        return bool(state in {"promoting", "active"} and data_plane_mode == "active")
    return bool(
        progress.latest_action in _VM_HA_PLANNED_REPROOF_ACTIONS
        and state in {"ownership-transfer", "promoting"}
        and data_plane_mode in {"blocked", "passive"}
    )


def _wait_for_vm_ha_planned_transfer(
    *,
    context: _VMHAPlannedTerminalContext,
    operation_name: str,
    started_at: float,
    request_fingerprint: str | None = None,
    clock: t.Callable[[], float] = time.monotonic,
    sleeper: t.Callable[[float], None] = time.sleep,
    poll_seconds: float = 1.0,
    progress_seconds: float = 5.0,
) -> _VMHAPlannedTransferCompletion:
    """Wait for committed cutover and terminal standby redundancy restoration."""

    next_progress = progress_seconds
    cutover_seconds: float | None = None
    restoration_started_at: float | None = None
    phase_deadline = clock() + context.cutover_timeout_seconds
    last_progress_phase: str | None = None
    last_progress_sequence = 0
    last_retry_phase: str | None = None
    last_observation_unavailable = False
    last_reproof_active = False

    def cutover_timeout(now: float) -> t.NoReturn:
        elapsed = max(0.0, now - started_at)
        if last_observation_unavailable:
            raise _VMHAPlannedCutoverVerificationUnavailable(elapsed_seconds=elapsed)
        if last_retry_phase is not None:
            raise RuntimeError(
                f"{operation_name} did not complete within {elapsed:.1f}s; the VM-HA "
                f"controller was still retrying {last_retry_phase}. "
                f"{_VM_HA_CONTROLLER_RECOVERY_GUIDANCE}"
            )
        if last_reproof_active:
            raise _VMHAPlannedCutoverVerificationIncomplete(
                elapsed_seconds=elapsed,
                budget_seconds=context.cutover_timeout_seconds,
            )
        raise RuntimeError(f"{operation_name} did not complete within {elapsed:.1f}s")

    def restoration_error(
        message: str,
        now: float,
        *,
        background_continues: bool = False,
    ) -> t.NoReturn:
        assert cutover_seconds is not None
        assert restoration_started_at is not None
        total_seconds = max(0.0, now - started_at)
        raise _VMHAPlannedRedundancyRestorationError(
            message,
            cutover_seconds=cutover_seconds,
            restoration_seconds=max(0.0, now - restoration_started_at),
            total_seconds=total_seconds,
            background_continues=background_continues,
        )

    def restoration_timeout(now: float) -> t.NoReturn:
        assert cutover_seconds is not None
        assert restoration_started_at is not None
        if last_observation_unavailable:
            total_seconds = max(0.0, now - started_at)
            raise _VMHAPlannedRestorationVerificationUnavailable(
                cutover_seconds=cutover_seconds,
                restoration_seconds=max(0.0, now - restoration_started_at),
                total_seconds=total_seconds,
            )
        restoration_error(
            "standby redundancy did not restore within its "
            f"{context.restoration_timeout_seconds:.1f}s phase deadline",
            now,
            background_continues=True,
        )

    while True:
        now = clock()
        elapsed = max(0.0, now - started_at)
        if now >= phase_deadline:
            if cutover_seconds is not None:
                restoration_timeout(now)
            cutover_timeout(now)
        try:
            record = context.status_reader()
        except _VMHAPlannedTerminalObservationUnavailable:
            record = {}
            last_observation_unavailable = True
        else:
            last_observation_unavailable = False
        retryable_failure: tuple[int, str] | None = None
        progress: _VMHAPlannedProgressObservation | None = None
        if cutover_seconds is None and request_fingerprint is not None:
            progress = _vm_ha_planned_progress_observation(
                record,
                context=context,
                request_fingerprint=request_fingerprint,
                after_sequence=last_progress_sequence,
            )
            if progress is None:
                last_progress_phase = None
                last_retry_phase = None
            else:
                for sequence, phase in progress.attempts:
                    last_progress_sequence = sequence
                    last_progress_phase = phase
                    last_retry_phase = None
                    typer.echo(
                        f"{operation_name} in progress: {elapsed:.1f}s elapsed, {phase}...",
                        err=True,
                    )
                    next_progress = elapsed + progress_seconds
                retryable_failure = progress.retryable_failure
                if retryable_failure is not None and retryable_failure[0] > last_progress_sequence:
                    last_progress_sequence, last_retry_phase = retryable_failure
                    last_progress_phase = last_retry_phase
                    typer.echo(
                        f"{operation_name} in progress: {elapsed:.1f}s elapsed, "
                        f"{last_retry_phase} failed; forwarding remains fenced while "
                        "the controller retries...",
                        err=True,
                    )
                    next_progress = elapsed + progress_seconds
                elif retryable_failure is not None:
                    last_retry_phase = retryable_failure[1]
            last_reproof_active = _vm_ha_planned_reproof_converging(
                record,
                context=context,
                request_fingerprint=request_fingerprint,
                progress=progress,
            )
        if record.get("state") == "blocked":
            reasons = t.cast(list[str], record["reasons"])
            detail = ", ".join(reasons) or "blocked-without-a-reason"
            if cutover_seconds is not None:
                restoration_error(
                    f"standby restoration was blocked by the VM-HA controller: {detail}",
                    now,
                )
            if not (
                last_reproof_active
                or (detail == "controller-step-failed" and retryable_failure is not None)
            ):
                raise RuntimeError(
                    f"{operation_name} was blocked by the VM-HA controller: {detail}. "
                    f"{_VM_HA_CONTROLLER_RECOVERY_GUIDANCE}"
                )
        if elapsed >= next_progress:
            phase = last_progress_phase or (
                "cutting over" if cutover_seconds is None else "restoring standby"
            )
            typer.echo(
                f"{operation_name} in progress: {elapsed:.1f}s elapsed, {phase}...",
                err=True,
            )
            next_progress = elapsed + progress_seconds
        if cutover_seconds is None and _vm_ha_planned_cutover_status_matches(
            record,
            context=context,
        ):
            try:
                cloud = context.cloud_reader()
            except _VMHAPlannedTerminalObservationUnavailable:
                last_observation_unavailable = True
            else:
                last_observation_unavailable = False
                if not _vm_ha_planned_cutover_cloud_matches(cloud, context=context):
                    try:
                        fresh_record = context.status_reader()
                    except _VMHAPlannedTerminalObservationUnavailable:
                        last_observation_unavailable = True
                        last_reproof_active = False
                    else:
                        last_observation_unavailable = False
                        last_reproof_active = _vm_ha_planned_reproof_converging(
                            fresh_record,
                            context=context,
                            request_fingerprint=request_fingerprint,
                        )
                        if not last_reproof_active:
                            raise RuntimeError(
                                f"{operation_name} terminal cloud ownership evidence drifted"
                            )
                        record = fresh_record
                else:
                    try:
                        final_record = context.status_reader()
                    except _VMHAPlannedTerminalObservationUnavailable:
                        last_observation_unavailable = True
                    else:
                        last_observation_unavailable = False
                        if not _vm_ha_planned_cutover_status_matches(
                            final_record,
                            context=context,
                        ):
                            last_reproof_active = _vm_ha_planned_reproof_converging(
                                final_record,
                                context=context,
                                request_fingerprint=request_fingerprint,
                            )
                            if not last_reproof_active:
                                raise RuntimeError(
                                    f"{operation_name} terminal agent evidence drifted"
                                )
                            record = final_record
                        else:
                            verified_at = clock()
                            if verified_at >= phase_deadline:
                                cutover_timeout(verified_at)
                            cutover_seconds = max(0.0, verified_at - started_at)
                            restoration_started_at = verified_at
                            now = restoration_started_at
                            elapsed = max(0.0, now - started_at)
                            phase_deadline = (
                                restoration_started_at + context.restoration_timeout_seconds
                            )
                            typer.echo(
                                f"{operation_name} cutover completed in "
                                f"{cutover_seconds:.1f}s; restoring standby redundancy...",
                                err=True,
                            )
                            last_progress_phase = None
                            next_progress = (
                                max(0.0, restoration_started_at - started_at) + progress_seconds
                            )
                            record = final_record

        if cutover_seconds is not None:
            restoration_phase = _vm_ha_planned_restoration_phase(record)
            if restoration_phase is not None and restoration_phase != last_progress_phase:
                last_progress_phase = restoration_phase
                typer.echo(
                    f"{operation_name} in progress: {elapsed:.1f}s elapsed, {restoration_phase}...",
                    err=True,
                )
                next_progress = elapsed + progress_seconds
            rearm_phase = record.get("rearm_phase")
            if rearm_phase in {"blocked", "inhibited"}:
                raw_rearm_reason = record.get("rearm_reason")
                detail = (
                    _safe_vm_ha_reason(raw_rearm_reason)
                    if isinstance(raw_rearm_reason, str) and raw_rearm_reason
                    else f"rearm-{rearm_phase}"
                )
                restoration_error(detail, now)
            if _vm_ha_planned_owner_redundancy_matches(record, context=context):
                try:
                    cloud = context.cloud_reader()
                except _VMHAPlannedTerminalObservationUnavailable:
                    last_observation_unavailable = True
                else:
                    last_observation_unavailable = False
                    if _vm_ha_planned_restored_cloud_matches(cloud, context=context):
                        try:
                            standby = context.standby_status_reader()
                        except _VMHAPlannedTerminalObservationUnavailable:
                            last_observation_unavailable = True
                        else:
                            last_observation_unavailable = False
                            if _vm_ha_planned_standby_matches(standby, context=context):
                                try:
                                    final_record = context.status_reader()
                                    last_observation_unavailable = False
                                    final_cloud = context.cloud_reader()
                                    last_observation_unavailable = False
                                    final_standby = context.standby_status_reader()
                                except _VMHAPlannedTerminalObservationUnavailable:
                                    last_observation_unavailable = True
                                else:
                                    last_observation_unavailable = False
                                    if not (
                                        _vm_ha_planned_owner_redundancy_matches(
                                            final_record,
                                            context=context,
                                        )
                                        and _vm_ha_planned_restored_cloud_matches(
                                            final_cloud,
                                            context=context,
                                        )
                                        and _vm_ha_planned_standby_matches(
                                            final_standby,
                                            context=context,
                                        )
                                    ):
                                        restoration_error(
                                            "terminal standby evidence drifted during final "
                                            "verification",
                                            clock(),
                                        )
                                    verified_at = clock()
                                    if verified_at >= phase_deadline:
                                        restoration_timeout(verified_at)
                                    total_seconds = max(0.0, verified_at - started_at)
                                    return _VMHAPlannedTransferCompletion(
                                        cutover_seconds=cutover_seconds,
                                        restoration_seconds=max(
                                            0.0,
                                            total_seconds - cutover_seconds,
                                        ),
                                        total_seconds=total_seconds,
                                    )

        now = clock()
        elapsed = max(0.0, now - started_at)
        remaining = phase_deadline - now
        if remaining <= 0:
            if cutover_seconds is not None:
                restoration_timeout(now)
            cutover_timeout(now)
        sleeper(min(poll_seconds, remaining))


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


@_with_vm_manager_lifetimes
def _prepare_vm_ha_planned_target(
    *,
    local_config_file: Path,
    target_role: str | None,
    timeout_seconds: int = 300,
    command: str | None = None,
    region: str | None = None,
    show_auth_progress: bool = True,
    progress_sink: _VMHAProgressSink | None = None,
    before_rearm_request: t.Callable[[str, str, str], None] | None = None,
    on_rearm_authorization_aborted: t.Callable[[], None] | None = None,
    rearm_request_progress_is_exact: t.Callable[[], bool] | None = None,
    return_after_ssh: bool = False,
) -> _VMHAPlannedPreparation:
    """Prepare the exact non-owner through the owner-side rearm bulkhead."""

    local_cfg = _load_config_with_region_override(
        local_config_file,
        region=region,
    )
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
    with _vm_ha_progress_step(
        progress_sink,
        _VMHAProgressPhase.AUTHENTICATE,
    ):
        auth_token = _ensure_authentication(required=True, show_progress=show_auth_progress)
    manager = _own_vm_manager(
        VMManager(
            project_id=project_id,
            region=plan.gateway_group.region,
            auth_token=auth_token,
            tenant_id=str(local_cfg.get("tenant_id") or "").strip() or None,
            region_id=plan.gateway_group.region,
            ssh_policy=ssh_policy,
            management_key_path=key_path,
        )
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
    terminal_call_timeout = min(5.0, float(timeout_seconds))
    terminal_calls = NebiusSDKCloudClient(
        sdk,
        request_timeout_provider=lambda: terminal_call_timeout,
    )
    terminal_adapter = VMHACloudAdapter(
        instance_reader=terminal_calls.get_instance,
        instance_stopper=terminal_calls.stop_instance,
        allocation_reader=terminal_calls.get_allocation,
        alias_allocation_setter=terminal_calls.set_alias_allocation,
    )

    def observe() -> t.Any:
        return adapter.observe_cluster(
            allocation_id=state.allocation_id,
            former_owner=owners["active"],
            candidate=owners["passive"],
        )

    def terminal_observe() -> t.Any:
        return _read_vm_ha_planned_terminal_cloud(
            lambda: terminal_adapter.observe_cluster(
                allocation_id=state.allocation_id,
                former_owner=owners["active"],
                candidate=owners["passive"],
            )
        )

    _emit_vm_ha_progress(
        progress_sink,
        _VMHAProgressPhase.VERIFY_REARM_AUTHORITY,
        _VMHAProgressState.STARTED,
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
    target_already_owns = resolved_target_role == owner_role
    standby_role = "passive" if owner_role == "active" else "active"
    standby_member = members_by_role[standby_role]
    preparation_role = standby_role if target_already_owns else resolved_target_role
    target_member = members_by_role[preparation_role]
    terminal_runtime_binding = _vm_ha_planned_terminal_runtime_binding(
        state,
        planned_by_role[resolved_target_role],
    )
    _emit_vm_ha_progress(
        progress_sink,
        _VMHAProgressPhase.VERIFY_REARM_AUTHORITY,
        _VMHAProgressState.COMPLETED,
    )

    def members(current: t.Any) -> tuple[t.Any, t.Any]:
        by_role = {"active": current.former, "passive": current.candidate}
        owner_observation = by_role[owner_role]
        target_observation = by_role[preparation_role]
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

    def already_owner_result(*, wait_for_members: bool) -> _VMHAPlannedPreparation:
        if wait_for_members:
            for member in (owner_member, standby_member):
                manager.wait_for_vm_ha_member_ssh(
                    member.instance_name,
                    member.public_ip,
                    username=username,
                    timeout=remaining_timeout(),
                )
        records = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag="--vm-ha-status",
            timeout_seconds=remaining_timeout(),
            status_validator=validate_planned_status,
        )
        records_by_node = {str(item.get("node_id") or ""): item for item in records}
        if len(records) != 2 or set(records_by_node) != {
            owner_member.node_id,
            standby_member.node_id,
        }:
            raise RuntimeError("planned VM-HA no-op status did not resolve both exact members")
        record = records_by_node[owner_member.node_id]
        standby_record = records_by_node[standby_member.node_id]
        if not (
            record.get("state") == "active"
            and record.get("promotion_ready") is True
            and record.get("data_plane_mode") == "active"
            and record.get("observed_owner_node_id") == owner_member.node_id
            and record.get("apply_locked") is False
            and record.get("pending_operation_id") is None
            and standby_record.get("state") == "normal"
            and standby_record.get("standby_ready") is True
            and standby_record.get("standby_readiness_reasons") == []
            and standby_record.get("data_plane_mode") == "passive"
            and standby_record.get("observed_owner_node_id") == owner_member.node_id
            and standby_record.get("apply_locked") is False
            and standby_record.get("pending_operation_id") is None
        ):
            raise RuntimeError(
                "planned VM-HA target already owns, but healthy standby redundancy "
                "is not restored; run vm-ha"
            )
        final_owner = observe()
        final_by_role = {"active": final_owner.former, "passive": final_owner.candidate}
        if not (
            final_owner.allocation.owner == owners[owner_role]
            and final_by_role[owner_role].state is InstanceCloudState.RUNNING
            and final_by_role[owner_role].has_alias_allocation(
                owner_member.network_interface_name, state.allocation_id
            )
            and final_by_role[standby_role].state is InstanceCloudState.RUNNING
            and not final_by_role[standby_role].has_alias_allocation(
                standby_member.network_interface_name, state.allocation_id
            )
        ):
            raise RuntimeError("planned VM-HA redundancy drifted before no-op admission")
        return _VMHAPlannedPreparation("already-owner", resolved_target_role, record)

    if target_already_owns:
        by_role = {"active": observation.former, "passive": observation.candidate}
        owner_observation = by_role[owner_role]
        standby_observation = by_role[standby_role]
        if not (
            owner_observation.state is InstanceCloudState.RUNNING
            and owner_observation.has_alias_allocation(
                owner_member.network_interface_name, state.allocation_id
            )
            and not standby_observation.has_alias_allocation(
                standby_member.network_interface_name, state.allocation_id
            )
        ):
            raise RuntimeError(
                "planned VM-HA target already owns, but healthy standby redundancy "
                "is not restored; run vm-ha"
            )
        if standby_observation.state is InstanceCloudState.RUNNING:
            return already_owner_result(wait_for_members=True)
        if standby_observation.state is not InstanceCloudState.STOPPED:
            raise RuntimeError(
                "planned VM-HA target already owns, but healthy standby redundancy "
                "is not restored; run vm-ha"
            )

    _owner_observation, target_observation = members(observation)
    ambiguous_rearm_request = False

    def exact_rearm_progress() -> bool:
        if rearm_request_progress_is_exact is None or not rearm_request_progress_is_exact():
            return False
        owner_records = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag="--vm-ha-status",
            configured_role=owner_role,
            timeout_seconds=remaining_timeout(),
            status_validator=validate_planned_status,
        )
        if len(owner_records) != 1:
            return False
        owner_record = owner_records[0]
        return bool(
            owner_record.get("state") == "active"
            and owner_record.get("promotion_ready") is True
            and owner_record.get("data_plane_mode") == "active"
            and owner_record.get("observed_owner_node_id") == owner_member.node_id
            and owner_record.get("apply_locked") is False
            and owner_record.get("pending_operation_id") is None
            and owner_record.get("rearm_phase") in {"starting", "running"}
            and owner_record.get("rearm_reason") is None
        )

    def wait_for_exact_rearm_progress(error: Exception) -> None:
        """Resolve a retry-writer race only through exact durable progress."""

        while True:
            try:
                if exact_rearm_progress():
                    return
            except (OSError, RuntimeError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise error
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))

    if target_observation.state is InstanceCloudState.STOPPED:
        if before_rearm_request is not None:
            before_rearm_request(
                owner_member.node_id,
                target_member.node_id,
                target_observation.resource_version,
            )
            try:
                observation = observe()
                _owner_observation, target_observation = members(observation)
            except Exception:
                if on_rearm_authorization_aborted is not None:
                    on_rearm_authorization_aborted()
                raise
        if target_observation.state is InstanceCloudState.STOPPED:
            with _vm_ha_progress_step(
                progress_sink,
                _VMHAProgressPhase.REQUEST_REARM,
            ):
                try:
                    retries = _run_vm_ha_operator_command(
                        local_config_file=local_config_file,
                        agent_flag="--vm-ha-rearm-request",
                        configured_role=owner_role,
                        timeout_seconds=remaining_timeout(),
                    )
                    if len(retries) != 1:
                        raise RuntimeError("VM-HA rearm retry did not target the exact owner")
                except (OSError, RuntimeError, ValueError) as error:
                    wait_for_exact_rearm_progress(error)
                    ambiguous_rearm_request = True
        elif target_observation.state not in {
            InstanceCloudState.RUNNING,
            InstanceCloudState.TRANSITIONAL,
        }:
            if on_rearm_authorization_aborted is not None:
                on_rearm_authorization_aborted()
            raise RuntimeError("planned VM-HA target left its safe recovery transition")
    elif target_observation.state not in {
        InstanceCloudState.RUNNING,
        InstanceCloudState.TRANSITIONAL,
    }:
        raise RuntimeError("planned VM-HA target Compute is not safely startable")

    with _vm_ha_progress_step(
        progress_sink,
        _VMHAProgressPhase.WAIT_REARM_COMPUTE,
    ):
        wait_compute_progress = _VMHAProgressWait(
            progress_sink,
            _VMHAProgressPhase.WAIT_REARM_COMPUTE,
        )
        while target_observation.state is not InstanceCloudState.RUNNING:
            if time.monotonic() >= deadline:
                raise RuntimeError("planned VM-HA target did not become Running")
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
            wait_compute_progress.update()
            observation = observe()
            _owner_observation, target_observation = members(observation)
            if target_observation.state is InstanceCloudState.STOPPED:
                if ambiguous_rearm_request and exact_rearm_progress():
                    continue
                raise RuntimeError(
                    "planned VM-HA target remained Stopped without exact rearm progress"
                )
            if target_observation.state not in {
                InstanceCloudState.RUNNING,
                InstanceCloudState.TRANSITIONAL,
            }:
                raise RuntimeError("planned VM-HA target left its safe startup transition")

    with _vm_ha_progress_step(
        progress_sink,
        _VMHAProgressPhase.WAIT_REARM_SSH,
    ):
        wait_ssh_progress = _VMHAProgressWait(
            progress_sink,
            _VMHAProgressPhase.WAIT_REARM_SSH,
        )
        if progress_sink is None:
            manager.wait_for_vm_ha_member_ssh(
                target_member.instance_name,
                target_member.public_ip,
                username=username,
                timeout=remaining_timeout(),
            )
        else:
            manager.wait_for_vm_ha_member_ssh(
                target_member.instance_name,
                target_member.public_ip,
                username=username,
                timeout=remaining_timeout(),
                progress_callback=wait_ssh_progress.update,
            )

    if return_after_ssh:
        return _VMHAPlannedPreparation(
            "standby-ssh-ready",
            resolved_target_role,
            {},
        )

    def standby_status() -> dict[str, t.Any]:
        records = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag="--vm-ha-status",
            configured_role=preparation_role,
            timeout_seconds=remaining_timeout(),
            status_validator=validate_planned_status,
        )
        if len(records) != 1:
            raise RuntimeError("planned VM-HA preparation did not resolve one exact target")
        return records[0]

    def terminal_status() -> dict[str, t.Any]:
        return _read_vm_ha_planned_terminal_agent(
            lambda: _run_vm_ha_operator_command(
                local_config_file=local_config_file,
                agent_flag="--vm-ha-status",
                configured_role=resolved_target_role,
                timeout_seconds=terminal_call_timeout,
                status_validator=validate_planned_status,
            ),
            source="target-agent",
            mismatch_message=("planned VM-HA terminal status did not resolve one exact target"),
        )

    def restored_standby_status() -> dict[str, t.Any]:
        return _read_vm_ha_planned_terminal_agent(
            lambda: _run_vm_ha_operator_command(
                local_config_file=local_config_file,
                agent_flag="--vm-ha-status",
                configured_role=owner_role,
                timeout_seconds=terminal_call_timeout,
                status_validator=validate_planned_status,
            ),
            source="standby-agent",
            mismatch_message=(
                "planned VM-HA terminal standby status did not resolve one exact member"
            ),
        )

    def standby_ready(record: t.Mapping[str, t.Any]) -> bool:
        return bool(
            record.get("standby_ready") is True
            and record.get("standby_readiness_reasons") == []
            and record.get("data_plane_mode") == "passive"
            and record.get("observed_owner_node_id") == owner_member.node_id
            and record.get("apply_locked") is False
            and record.get("pending_operation_id") is None
        )

    with _vm_ha_progress_step(
        progress_sink,
        _VMHAProgressPhase.WAIT_REARM_SERVICES,
    ):
        wait_services_progress = _VMHAProgressWait(
            progress_sink,
            _VMHAProgressPhase.WAIT_REARM_SERVICES,
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
            wait_services_progress.update()
            record = standby_status()

        final = observe()
        _final_owner, final_target = members(final)
        if final_target.state is not InstanceCloudState.RUNNING:
            raise RuntimeError("planned VM-HA target did not remain Running at request admission")
        final_record = standby_status()
        if not standby_ready(final_record):
            raise RuntimeError("planned VM-HA target readiness drifted before request admission")
    if target_already_owns:
        return already_owner_result(wait_for_members=False)
    return _VMHAPlannedPreparation(
        "standby-ready",
        resolved_target_role,
        final_record,
        _VMHAPlannedTerminalContext(
            target_role=resolved_target_role,
            former_role=owner_role,
            target_member=target_member,
            former_member=owner_member,
            target_owner=owners[resolved_target_role],
            allocation_id=state.allocation_id,
            runtime_binding=terminal_runtime_binding,
            status_reader=terminal_status,
            standby_status_reader=restored_standby_status,
            cloud_reader=terminal_observe,
            request_timeout_seconds=float(timeout_seconds),
            cutover_timeout_seconds=_VM_HA_PLANNED_CUTOVER_TIMEOUT_SECONDS,
            restoration_timeout_seconds=_VM_HA_PLANNED_RESTORATION_TIMEOUT_SECONDS,
        ),
    )


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
    client_auth: SSHClientAuth | None = None


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
_VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY = "vm-ha-mtls-rotation-quiescence-v1"
_AGENT_CAPABILITIES_SCHEMA = "nebius-vpngw.agent-capabilities.v1"
_VM_HA_MTLS_ROTATION_APPLY_GUIDANCE = (
    "Run 'nebius-vpngw apply' with this CLI version and the same local config, "
    "verify both members and their running controllers, and retry."
)


def _vm_ha_mtls_remote_result(response: object) -> dict[str, object]:
    result = _vm_ha_mtls_action_result(response)
    if not isinstance(result, dict):
        raise RuntimeError("managed mTLS rotation returned invalid evidence")
    return t.cast(dict[str, object], result)


def _require_vm_ha_mtls_rotation_agent_capability(
    *,
    target: str,
    hostname: str,
    username: str,
    key_path: Path | None,
    ssh_policy: SSHTrustPolicy,
    client_auth: SSHClientAuth | None = None,
) -> None:
    """Fail before rotation when an installed agent cannot prove the barrier contract."""

    command = _build_ssh_base_cmd(
        key_path,
        client_auth=client_auth,
        ssh_policy=ssh_policy,
        hostname=hostname,
    )
    command.extend(
        [
            "-o",
            "BatchMode=yes",
            f"{username}@{target}",
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities",
        ]
    )
    guidance = _VM_HA_MTLS_ROTATION_APPLY_GUIDANCE
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            f"managed mTLS rotation could not verify the installed-agent capability. {guidance}"
        ) from error
    if result.returncode != 0:
        raise RuntimeError(
            "an installed VM-HA agent does not expose the required managed mTLS "
            f"rotation capability. {guidance}"
        )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "an installed VM-HA agent returned malformed managed mTLS rotation "
            f"capability evidence. {guidance}"
        ) from error
    features = payload.get("features") if isinstance(payload, dict) else None
    if not (
        isinstance(payload, dict)
        and payload.get("schema") == _AGENT_CAPABILITIES_SCHEMA
        and isinstance(features, list)
        and all(isinstance(feature, str) for feature in features)
    ):
        raise RuntimeError(
            "an installed VM-HA agent returned unsupported managed mTLS rotation "
            f"capability evidence. {guidance}"
        )
    if _VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY not in features:
        raise RuntimeError(
            "an installed VM-HA agent is missing the required managed mTLS rotation "
            f"quiescence capability. {guidance}"
        )


def _require_vm_ha_mtls_rotation_controller_capability(
    status: dict[str, t.Any],
) -> None:
    """Require split-quiescence evidence written by the running controller process."""

    capabilities = status.get("controller_capabilities")
    if not (
        isinstance(capabilities, list)
        and all(isinstance(capability, str) for capability in capabilities)
        and _VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY in capabilities
    ):
        raise RuntimeError(
            "a running VM-HA controller does not expose the required managed mTLS "
            "rotation quiescence capability. "
            f"{_VM_HA_MTLS_ROTATION_APPLY_GUIDANCE}"
        )


@_with_vm_manager_lifetimes
def _inspect_vm_ha_mtls_rotation(config_path: Path) -> _VMHAMTLSRotationPlan:
    """Build a mutation-free rotation plan from exact cloud, SSH, and node truth."""

    local_config = load_local_config(config_path)
    deployment = merge_with_peer_configs(local_config, [])
    _enforce_command_applicability("vm-ha --rotate-mtls", deployment, local_config)
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
    manager = _own_vm_manager(
        VMManager(
            project_id=project_id,
            region=deployment.gateway_group.region,
            auth_token=auth_token,
            tenant_id=str(local_config.get("tenant_id") or "").strip() or None,
            region_id=deployment.gateway_group.region,
            ssh_policy=ssh_policy,
            management_key_path=key_path,
            management_public_key=vm_spec.get("ssh_public_key"),
        )
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
            isinstance(member, dict) and member.get("state") == InstanceCloudState.RUNNING.value
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
    client_auth = manager.ssh_client_auth
    ssh = SSHPush(ssh_policy=ssh_policy)
    members: list[_VMHAMTLSRotationMember] = []
    operation_candidates: set[str] = set()
    target_epochs: set[int] = set()
    current_epochs: list[int] = []
    current_fingerprints: dict[str, str] = {}
    for instance, member in instance_rows:
        _require_vm_ha_mtls_rotation_agent_capability(
            target=str(member.public_ip),
            hostname=instance.hostname,
            username=username,
            key_path=key_path,
            ssh_policy=ssh_policy,
            client_auth=client_auth,
        )
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
            client_auth=client_auth,
            ssh_policy=ssh_policy,
            inst_cfg=instance,
            runtime_binding=runtime_binding,
        )
        _require_vm_ha_mtls_rotation_controller_capability(agent)
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
                        "certificate_fingerprint": member.mtls["certificate_fingerprint"],
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
        members=t.cast(tuple[_VMHAMTLSRotationMember, _VMHAMTLSRotationMember], tuple(members)),
        ssh_policy=ssh_policy,
        client_auth=client_auth,
    )


def _render_vm_ha_mtls_rotation_plan(plan: _VMHAMTLSRotationPlan) -> tuple[str, str]:
    operation = (
        "resume"
        if any(
            member.mtls.get("operation_id") or member.mtls.get("inhibition_operation_id")
            for member in plan.members
        )
        else "rotate"
    )
    return (
        f"Plan: {operation} {len(plan.members)} members, passive first, target epoch "
        f"{plan.target_epoch}.",
        f"Plan digest: {plan.digest}",
    )


def _vm_ha_mtls_inhibition_quiescent(
    status: dict[str, t.Any],
    *,
    plan: _VMHAMTLSRotationPlan,
    member: _VMHAMTLSRotationMember,
) -> bool:
    """Require one controller-processed, effect-quiescent inhibition barrier."""

    mtls = status.get("mtls")
    expected_mode = "active" if member.node_id == plan.owner_node_id else "passive"
    if not (
        status.get("apply_locked") is False
        and status.get("apply_operation_id") is None
        and status.get("transfer_inhibition_operation_id") == plan.operation_id
        and status.get("transfer_inhibition_quiescent") is True
        and status.get("pending_operation_id") is None
        and status.get("observed_owner_node_id") == plan.owner_node_id
        and status.get("data_plane_mode") == expected_mode
        and isinstance(mtls, dict)
        and mtls.get("inhibited") is True
        and mtls.get("inhibition_operation_id") == plan.operation_id
    ):
        return False
    if member.node_id == plan.passive_node_id:
        return bool(
            status.get("state") == "blocked"
            and status.get("reasons") == ["mtls-rotation-active"]
            and status.get("former_owner_compute_state") == "running"
        )
    return bool(status.get("state") == "active" and status.get("promotion_ready") is True)


def _execute_vm_ha_mtls_rotation(plan: _VMHAMTLSRotationPlan) -> None:
    """Resume one exact rotation and retain inhibition after identity changes begin."""

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
            client_auth=plan.client_auth,
            ssh_policy=plan.ssh_policy,
            inst_cfg=member.instance,
            runtime_binding=runtime_binding,
            expected_apply_locked=False,
            predicate=predicate,
            timeout_seconds=120.0,
            poll_seconds=1.0,
        )

    preparation_started = any(member.mtls.get("operation_id") for member in plan.members)
    inhibition_attempts: list[_VMHAMTLSRotationMember] = []

    def inhibition_predicate(
        target_member: _VMHAMTLSRotationMember,
    ) -> t.Callable[[dict[str, t.Any]], bool]:
        def predicate(status: dict[str, t.Any]) -> bool:
            return _vm_ha_mtls_inhibition_quiescent(
                status,
                plan=plan,
                member=target_member,
            )

        return predicate

    try:
        for member in (passive, owner):
            inhibition_attempts.append(member)
            action(member, "inhibit", inhibition_request(member))
            fetch(member, inhibition_predicate(member))
    except Exception as error:
        if preparation_started:
            raise
        cleanup_failed = False
        for member in reversed(inhibition_attempts):
            try:
                action(member, "release-inhibition", inhibition_request(member))
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError(
                "managed mTLS rotation stopped before identity changes, but exact inhibition "
                "cleanup was incomplete"
            ) from error
        raise RuntimeError(
            "managed mTLS rotation stopped before identity changes; exact inhibition was "
            "released, so retry after VM-HA settles"
        ) from error

    receipts: dict[str, dict[str, object]] = {}
    for member in plan.members:
        preparation_started = True
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
    if (
        owner_remote.get("certificate_fingerprint")
        != receipts[owner.node_id]["certificate_fingerprint"]
    ):
        fetch(
            passive,
            lambda status: matches_pair(
                passive,
                status,
                local_fingerprint=str(receipts[passive.node_id]["certificate_fingerprint"]),
                peer_fingerprint=owner_old_fingerprint,
            ),
        )
        fetch(
            owner,
            lambda status: matches_pair(
                owner,
                status,
                local_fingerprint=owner_old_fingerprint,
                peer_fingerprint=str(receipts[passive.node_id]["certificate_fingerprint"]),
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
                    local_fingerprint=str(receipts[member.node_id]["certificate_fingerprint"]),
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
                    "peer_certificate_fingerprint": receipts[peer_id]["certificate_fingerprint"],
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
                    "peer_certificate_fingerprint": receipts[peer_id]["certificate_fingerprint"],
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
            and final.get("peer_fingerprints") == [receipts[peer_id]["certificate_fingerprint"]]
        ):
            raise RuntimeError("managed mTLS rotation final state is not exact")


def _run_vm_ha_mtls_rotation(
    local_config_file: Path,
    *,
    dry_run: bool,
    approve: str | None,
) -> None:
    """Run the explicit resumable passive-first VM-HA mTLS rotation mode."""

    if dry_run:
        typer.echo("Planning a passive-first VM-HA mTLS rotation; dry-run makes no changes.")
    else:
        typer.echo(
            "Starting a passive-first VM-HA mTLS rotation. "
            "VPN traffic is expected to remain available; failover and rearm "
            "are paused until completion."
        )
    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    try:
        inspected = _inspect_vm_ha_mtls_rotation(config_path)
        for line in _render_vm_ha_mtls_rotation_plan(inspected):
            typer.echo(line)
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
            progress = _vm_ha_progress_sink(sys.stderr)
            try:
                with _vm_ha_progress_step(progress, _VMHAProgressPhase.ROTATE_MTLS):
                    _execute_vm_ha_mtls_rotation(current)
            finally:
                progress.close_unfinished()
    except (typer.Abort, typer.Exit):
        raise
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[red]Managed mTLS rotation failed:[/red] {error}")
        raise typer.Exit(code=1) from error


@dataclass(frozen=True)
class _VMHAEffectiveConfig:
    path: Path
    actions: tuple[str, ...]


def _plan_vm_ha_apply_convergence(
    config_path: Path,
    *,
    region: str | None = None,
) -> _VMHAApplyPlanReport:
    """Run apply preflight through its typed stop-before-mutation boundary."""

    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _apply_impl(
                local_config_file=config_path,
                recreate_gw=False,
                sa=None,
                project_id=None,
                region=region,
                dry_run=True,
                prepare_vm_ha_peer_rotation=False,
                approve_vm_ha_migration=None,
                recover_vm_ha_migration=None,
                replace_failed_vm_ha_passive=None,
                replace_missing_vm_ha_standby=None,
                stop_after_vm_ha_plan=True,
            )
    except _VMHAApplyPlanCaptured as captured:
        return captured.report
    except typer.Exit as error:
        cause = error.__cause__
        if isinstance(cause, _VMHAApplyPlanningFailed):
            raise cause from None
        if isinstance(cause, VMHACredentialIdentityError):
            if cause.reason == "authentication-failed":
                raise _VMHAApplyPlanningFailed(
                    reason="runtime-credential-authentication-failed",
                    next_action=(
                        "repair the exact configured VM-HA runtime credentials and rerun vm-ha"
                    ),
                ) from None
            raise _VMHAApplyPlanningFailed(
                reason="runtime-credential-identity-invalid",
                next_action=(
                    "repair the exact configured VM-HA runtime credential files and rerun vm-ha"
                ),
            ) from None
        if _vm_ha_error_chain_has_sdk_code(
            error, "UNAUTHENTICATED"
        ) or error_chain_has_cli_authentication_failure(error):
            raise _VMHAApplyPlanningFailed(
                reason="authentication-or-provider-unavailable",
                next_action="restore authentication and rerun vm-ha",
            ) from None
        raise _VMHAApplyPlanningFailed(
            reason="apply-planning-prerequisite-unavailable",
            next_action="resolve the VM-HA apply preflight prerequisite and rerun vm-ha",
            classification=VMHACommandClassification.FAILED,
        ) from None
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        if _vm_ha_error_chain_has_sdk_code(
            error, "UNAUTHENTICATED"
        ) or error_chain_has_cli_authentication_failure(error):
            raise _VMHAApplyPlanningFailed(
                reason="authentication-or-provider-unavailable",
                next_action="restore authentication and rerun vm-ha",
            ) from None
        if _vm_ha_error_chain_has_sdk_code(error, "DEADLINE_EXCEEDED"):
            raise _VMHAApplyPlanningFailed(
                reason="provider-timeout",
                next_action="wait for provider availability and rerun vm-ha",
            ) from None
        raise
    raise RuntimeError("typed VM-HA apply planning did not produce a plan")


def _execute_vm_ha_apply_convergence(
    config_path: Path,
    report: _VMHAApplyPlanReport,
    *,
    region: str | None = None,
    progress_sink: _VMHAProgressSink | None = None,
) -> None:
    """Execute one already revalidated typed apply plan without reacquiring a lock."""

    try:
        if report.kind == "artifact-standby-recovery":
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                _execute_vm_ha_artifact_standby_recovery(
                    config_path,
                    report,
                    region=region,
                    progress_sink=progress_sink,
                )
            return

        approve_migration = report.engine_digest if report.kind == "migration" else None
        recover_migration = report.engine_digest if report.kind == "recovery" else None
        replace_passive = (
            report.engine_digest if report.kind == "failed-passive-replacement" else None
        )
        replace_missing_standby = (
            report.engine_digest if report.kind == "active-standby-replacement" else None
        )
        with (
            _vm_ha_progress_step(progress_sink, _VMHAProgressPhase.EXECUTE_APPLY),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _apply_impl(
                local_config_file=config_path,
                recreate_gw=False,
                sa=None,
                project_id=None,
                region=region,
                dry_run=False,
                prepare_vm_ha_peer_rotation=False,
                approve_vm_ha_migration=approve_migration,
                recover_vm_ha_migration=recover_migration,
                replace_failed_vm_ha_passive=replace_passive,
                replace_missing_vm_ha_standby=replace_missing_standby,
                vm_ha_progress_sink=progress_sink,
                expected_vm_ha_plan=report,
            )
    except VMHAAgentArtifactError:
        raise
    except _VMHAApplyConvergenceFailed:
        raise
    except typer.Exit as error:
        cause = error.__cause__
        if isinstance(cause, VMHAAgentArtifactError):
            raise cause from None
        if isinstance(
            cause,
            (
                _VMHAActivationFailed,
                _VMHAActivationSafelyBlocked,
                _VMHAActivationUnsafe,
            ),
        ):
            raise _VMHAApplyConvergenceFailed(str(cause)) from None
        raise _VMHAApplyConvergenceFailed(
            "VM-HA apply convergence stopped after execution began"
        ) from None
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        paramiko.SSHException,
    ):
        raise _VMHAApplyConvergenceFailed(
            "VM-HA apply convergence stopped after execution began"
        ) from None


def _vm_ha_action_required(
    *,
    config_path: Path,
    classification: VMHACommandClassification,
    health: VMHACommandHealth,
    reason: str,
    next_action: str,
    actions: tuple[str, ...] = (),
    impact: VMHACommandImpact | None = None,
) -> VMHACommandResult:
    return VMHACommandResult(
        outcome=VMHACommandOutcome.ACTION_REQUIRED,
        classification=classification,
        health=health,
        effective_config_file=config_path,
        actions=actions,
        reasons=(reason,),
        impact=impact,
        next_action=next_action,
    )


def _vm_ha_region_unavailable(config_path: Path) -> VMHACommandResult:
    return VMHACommandResult(
        outcome=VMHACommandOutcome.FAILED,
        classification=VMHACommandClassification.FAILED,
        health=VMHACommandHealth.UNKNOWN,
        effective_config_file=config_path,
        reasons=("region-unavailable",),
        next_action=("use --region or set gateway_group.region or region_id, then rerun vm-ha"),
    )


def _ordinary_vm_ha_conversion_trust_prerequisite(
    source_path: Path,
    source: t.Mapping[str, t.Any],
) -> VMHACommandResult | None:
    """Require ordinary apply to publish trust before any conversion side effect."""

    try:
        plan = merge_with_peer_configs(dict(source), [])
        instances = tuple(plan.iter_instance_configs())
        if plan.vm_ha is not None or len(instances) != 1:
            raise ValueError("conversion source is not one ordinary gateway member")
        scope = _ordinary_ssh_trust_scope(
            source,
            plan,
            project_id=str(source.get("project_id") or "").strip() or None,
        )
        member = managed_ssh_trust_member(scope, instances[0].hostname)
    except (OSError, RuntimeError, ValueError):
        member = None
    if member is not None:
        return None
    return _vm_ha_action_required(
        config_path=source_path,
        classification=VMHACommandClassification.CONVERSION_REQUIRED,
        health=VMHACommandHealth.NOT_CONFIGURED,
        reason="ordinary-ssh-trust-required",
        next_action=(
            "run nebius-vpngw apply --local-config-file "
            f"{shlex.quote(str(source_path))} before vm-ha conversion"
        ),
    )


def _resolve_vm_ha_effective_config(
    *,
    source_path: Path,
    output: Path | None,
    force: bool,
    dry_run: bool,
    interactive: bool,
    region: str | None,
    before_interactive_wizard: t.Callable[[], None] | None = None,
    progress_sink: _VMHAProgressSink | None = None,
    ordinary_trust_preflight: (
        t.Callable[[Path, t.Mapping[str, t.Any]], VMHACommandResult | None] | None
    ) = None,
) -> _VMHAEffectiveConfig | VMHACommandResult:
    """Resolve or create a separate explicit VM-HA candidate."""

    from rich.console import Console

    source, source_fingerprint = _read_safe_yaml_mapping(
        source_path,
        label="The source configuration",
    )
    group = source.get("gateway_group")
    vm_ha = group.get("vm_ha") if isinstance(group, dict) else None
    if isinstance(vm_ha, dict) and vm_ha.get("enabled") is True:
        try:
            _resolve_vm_ha_region(source, explicit_region=region)
        except ValueError:
            return _vm_ha_region_unavailable(source_path)
        return _VMHAEffectiveConfig(source_path, ())

    conversion_source = dict(source)
    try:
        if region is None:
            validate_vm_ha_conversion_source(conversion_source)
            _apply_nebius_region_precedence(
                conversion_source,
                explicit_region=None,
            )
        else:
            _apply_nebius_region_precedence(
                conversion_source,
                explicit_region=region,
            )
            validate_vm_ha_conversion_source(conversion_source)
    except WizardValidationError:
        return VMHACommandResult(
            outcome=VMHACommandOutcome.BLOCKED,
            classification=VMHACommandClassification.AMBIGUOUS_STATE,
            health=VMHACommandHealth.NOT_CONFIGURED,
            effective_config_file=source_path,
            reasons=("configuration-not-convertible",),
            next_action="validate the ordinary or explicit VM-HA configuration",
        )
    except ValueError:
        return _vm_ha_region_unavailable(source_path)

    if ordinary_trust_preflight is not None:
        trust_result = ordinary_trust_preflight(source_path, conversion_source)
        if trust_result is not None:
            return trust_result

    destination = output or _default_vm_ha_candidate_path(source_path)
    try:
        destination_fingerprint = _safe_destination_fingerprint(
            source_path,
            destination,
        )
    except ValueError:
        return VMHACommandResult(
            outcome=VMHACommandOutcome.BLOCKED,
            classification=VMHACommandClassification.AMBIGUOUS_STATE,
            health=VMHACommandHealth.NOT_CONFIGURED,
            effective_config_file=destination,
            reasons=("candidate-path-unsafe",),
            next_action=("choose a distinct regular non-linked --output candidate and rerun vm-ha"),
        )
    if destination_fingerprint is not None:
        existing, existing_fingerprint = _read_safe_yaml_mapping(
            destination,
            label="The VM-HA candidate destination",
        )
        if existing_fingerprint != destination_fingerprint:
            return VMHACommandResult(
                outcome=VMHACommandOutcome.BLOCKED,
                classification=VMHACommandClassification.AMBIGUOUS_STATE,
                health=VMHACommandHealth.NOT_CONFIGURED,
                effective_config_file=destination,
                reasons=("candidate-changed-during-inspection",),
                next_action="review the candidate and rerun vm-ha",
            )
        if is_vm_ha_conversion_candidate(conversion_source, existing):
            try:
                _resolve_vm_ha_region(existing, explicit_region=region)
            except ValueError:
                return _vm_ha_region_unavailable(destination)
            mode = stat.S_IMODE(destination.lstat().st_mode)
            if mode == 0o600:
                if _file_fingerprint(destination) != existing_fingerprint:
                    return VMHACommandResult(
                        outcome=VMHACommandOutcome.BLOCKED,
                        classification=VMHACommandClassification.AMBIGUOUS_STATE,
                        health=VMHACommandHealth.NOT_CONFIGURED,
                        effective_config_file=destination,
                        reasons=("candidate-changed-during-inspection",),
                        next_action="review the candidate and rerun vm-ha",
                    )
                return _VMHAEffectiveConfig(destination, ("candidate-reused",))
            if not force:
                return _vm_ha_action_required(
                    config_path=destination,
                    classification=VMHACommandClassification.CANDIDATE_READY,
                    health=VMHACommandHealth.NOT_CONFIGURED,
                    reason="candidate-permissions-not-private",
                    next_action="rerun with --force to republish the exact candidate as mode 0600",
                )
            if dry_run:
                return VMHACommandResult(
                    outcome=VMHACommandOutcome.PLANNED,
                    classification=VMHACommandClassification.CANDIDATE_READY,
                    health=VMHACommandHealth.NOT_CONFIGURED,
                    effective_config_file=destination,
                    actions=("republish-candidate-mode-0600",),
                    reasons=("candidate-permissions-not-private",),
                    next_action="rerun without --dry-run to repair candidate permissions",
                )
            candidate_snapshot = _read_regular_file_snapshot(destination)
            if candidate_snapshot is None or candidate_snapshot[1] != existing_fingerprint:
                return VMHACommandResult(
                    outcome=VMHACommandOutcome.BLOCKED,
                    classification=VMHACommandClassification.AMBIGUOUS_STATE,
                    health=VMHACommandHealth.NOT_CONFIGURED,
                    effective_config_file=destination,
                    reasons=("candidate-changed-before-publication",),
                    next_action="review the candidate and rerun vm-ha",
                )
            candidate_text = candidate_snapshot[0].decode("utf-8")
            _conditional_publish_text(
                destination,
                candidate_text,
                expected_fingerprint=existing_fingerprint,
            )
            return _VMHAEffectiveConfig(destination, ("candidate-permissions-repaired",))
        return _vm_ha_action_required(
            config_path=destination,
            classification=VMHACommandClassification.CONVERSION_REQUIRED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            reason="candidate-conflicts-with-source",
            next_action=("choose another --output; --force repairs only an exact candidate"),
        )

    try:
        _resolve_vm_ha_region(conversion_source, explicit_region=region)
    except ValueError:
        return _vm_ha_region_unavailable(destination)

    if dry_run or not interactive:
        return _vm_ha_action_required(
            config_path=destination,
            classification=VMHACommandClassification.CONVERSION_REQUIRED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            reason="conversion-input-required",
            next_action="rerun vm-ha interactively after peer and passive-IP inputs are ready",
        )

    reservation_attempted = False
    reservation_completed = False

    def reserve_passive_ip() -> str:
        nonlocal reservation_attempted, reservation_completed
        if _file_fingerprint(source_path) != source_fingerprint:
            raise OSError("The source configuration changed before cloud preparation.")
        reservation_attempted = True
        with _vm_ha_progress_step(
            progress_sink,
            _VMHAProgressPhase.PREPARE_PASSIVE_IP,
        ):
            passive_ip = _reserve_vm_ha_passive_public_ip(
                conversion_source,
                region=region,
            )
        reservation_completed = True
        return passive_ip

    if before_interactive_wizard is not None:
        before_interactive_wizard()
    try:
        conversion = run_vm_ha_conversion_wizard(
            Console(),
            conversion_source,
            destination,
            reserve_passive_ip=reserve_passive_ip,
        )
    except WizardCancelled:
        actions = (
            ("passive-public-ip-reserved",)
            if reservation_completed
            else (("passive-allocation-may-exist",) if reservation_attempted else ())
        )
        return _vm_ha_action_required(
            config_path=destination,
            classification=VMHACommandClassification.CONVERSION_REQUIRED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            reason="conversion-cancelled",
            next_action=(
                "rerun vm-ha; the deterministic passive allocation will be resolved and reused"
                if actions
                else "rerun vm-ha when ready to publish the candidate"
            ),
            actions=actions,
        )
    except WizardInterrupted:
        raise
    except (OSError, RuntimeError, ValueError, WizardValidationError):
        if reservation_attempted:
            action = (
                "passive-public-ip-reserved"
                if reservation_completed
                else "passive-allocation-may-exist"
            )
            return VMHACommandResult(
                outcome=VMHACommandOutcome.FAILED,
                classification=VMHACommandClassification.FAILED,
                health=VMHACommandHealth.NOT_CONFIGURED,
                effective_config_file=destination,
                actions=(action,),
                reasons=("candidate-not-published-after-reservation",),
                next_action=(
                    "rerun vm-ha; the deterministic passive allocation will be resolved and reused"
                ),
            )
        return VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            effective_config_file=destination,
            reasons=("conversion-failed-before-cloud-effects",),
            next_action="review the unchanged source and rerun vm-ha",
        )
    if conversion.yaml_text is None:
        actions = ("passive-public-ip-reserved",) if conversion.passive_ip_reserved else ()
        return _vm_ha_action_required(
            config_path=destination,
            classification=VMHACommandClassification.CONVERSION_REQUIRED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            reason=(
                "peer-input-required"
                if conversion.passive_ip is not None
                else "passive-public-ip-required"
            ),
            next_action=(
                "complete peer preparation and rerun vm-ha; the deterministic passive "
                "allocation will be resolved and reused"
                if actions
                else "provide an existing unattached passive public IP or confirm its "
                "reservation when rerunning vm-ha"
            ),
            actions=actions,
        )
    if _file_fingerprint(source_path) != source_fingerprint:
        return VMHACommandResult(
            outcome=VMHACommandOutcome.BLOCKED,
            classification=VMHACommandClassification.AMBIGUOUS_STATE,
            health=VMHACommandHealth.NOT_CONFIGURED,
            effective_config_file=destination,
            actions=(("passive-public-ip-reserved",) if conversion.passive_ip_reserved else ()),
            reasons=("source-changed-before-candidate-publication",),
            next_action=(
                "review the source and rerun vm-ha; any deterministic passive allocation "
                "will be resolved and reused"
            ),
        )
    try:
        _conditional_publish_text(
            destination,
            conversion.yaml_text,
            expected_fingerprint=destination_fingerprint,
        )
    except (OSError, ValueError):
        return VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            effective_config_file=destination,
            actions=(("passive-public-ip-reserved",) if conversion.passive_ip_reserved else ()),
            reasons=("candidate-publication-failed",),
            next_action=(
                "review the destination and rerun vm-ha; any deterministic passive allocation "
                "will be resolved and reused"
            ),
        )
    final_actions: tuple[str, ...] = (
        ("passive-public-ip-reserved", "candidate-created")
        if conversion.passive_ip_reserved
        else ("candidate-created",)
    )
    return _VMHAEffectiveConfig(destination, final_actions)


def _vm_ha_snapshot_is_rearmable(snapshot: _VMHAStatusSnapshot) -> bool:
    if not (
        snapshot.lifecycle_state is not None
        and snapshot.lifecycle_state.status is VMHALifecycleStatus.ACTIVE
        and snapshot.authority.condition == "exact"
        and snapshot.authority.owner_node_id is not None
        and snapshot.view.overall == "DEGRADED"
    ):
        return False
    return bool(
        snapshot.view.action.startswith("nebius-vpngw vm-ha")
        or snapshot.view.reasons == ("standby-status-unavailable",)
    )


def _vm_ha_snapshot_is_apply_owned_transition(snapshot: _VMHAStatusSnapshot) -> bool:
    """Return whether the durable lifecycle must be converged by apply."""

    return bool(
        snapshot.lifecycle_state is not None
        and snapshot.lifecycle_state.status
        in {
            VMHALifecycleStatus.PROVISIONING,
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.REMOVED,
            VMHALifecycleStatus.DESTROYED,
        }
    )


def _vm_ha_snapshot_is_missing_non_owner_candidate(
    snapshot: _VMHAStatusSnapshot,
) -> bool:
    """Admit only one stable owner plus one cloud-unavailable non-owner to apply planning."""

    state = snapshot.lifecycle_state
    transaction = None if state is None else getattr(state, "transaction", None)
    owner_node_id = snapshot.authority.owner_node_id
    compute_states = dict(snapshot.authority.member_compute_states)
    owner = next(
        (member for member in snapshot.members if member.node_id == owner_node_id),
        None,
    )
    nonowners = tuple(member for member in snapshot.members if member.node_id != owner_node_id)
    return bool(
        state is not None
        and state.status is VMHALifecycleStatus.ACTIVE
        and transaction is not None
        and transaction.pending_effect is None
        and transaction.accepted_cloud_operation_id is None
        and owner_node_id is not None
        and owner is not None
        and owner.condition == "exact"
        and len(nonowners) == 1
        and nonowners[0].condition == "unknown"
        and nonowners[0].node_id in snapshot.authority.unavailable_member_node_ids
        and compute_states == {owner_node_id: InstanceCloudState.RUNNING.value}
        and snapshot.authority.reasons == ("cloud-member-unavailable",)
    )


def _vm_ha_snapshot_is_replacement_policy_split(
    snapshot: _VMHAStatusSnapshot,
) -> bool:
    """Identify only the terminal replacement checkpoint's policy bootstrap split."""

    state = snapshot.lifecycle_state
    transaction = None if state is None else getattr(state, "transaction", None)
    return bool(
        state is not None
        and state.status is VMHALifecycleStatus.ACTIVE
        and transaction is not None
        and getattr(transaction, "checkpoint", None) == "missing-standby-replacement-complete"
        and getattr(transaction, "pending_effect", None) is None
        and getattr(transaction, "accepted_cloud_operation_id", None) is None
        and snapshot.authority.condition == "exact"
        and snapshot.authority.owner_node_id is not None
        and dict(snapshot.authority.member_compute_states)
        == {member.node_id: InstanceCloudState.RUNNING.value for member in snapshot.members}
        and all(member.condition == "exact" for member in snapshot.members)
        and snapshot.view.reasons == ("standby-auto-healing-policy-invalid",)
    )


def _vm_ha_result_from_snapshot(
    *,
    config_path: Path,
    snapshot: _VMHAStatusSnapshot,
    actions: tuple[str, ...],
    dry_run: bool,
) -> VMHACommandResult:
    view = snapshot.view
    health = VMHACommandHealth(view.overall.lower())
    reasons = dedupe_reason_codes(list(view.reasons))
    quoted_config = shlex.quote(str(config_path))
    if view.overall == "HEALTHY":
        return VMHACommandResult(
            outcome=VMHACommandOutcome.HEALTHY,
            classification=VMHACommandClassification.HEALTHY,
            health=VMHACommandHealth.HEALTHY,
            effective_config_file=config_path,
            actions=actions,
        )
    if view.overall == "MAINTENANCE":
        return VMHACommandResult(
            outcome=VMHACommandOutcome.MAINTENANCE,
            classification=VMHACommandClassification.MAINTENANCE_POLICY,
            health=VMHACommandHealth.MAINTENANCE,
            effective_config_file=config_path,
            actions=actions,
            reasons=reasons or ("standby-auto-healing-policy-disabled",),
            next_action=(
                "rerun vm-ha with --standby-auto-healing enabled when maintenance is complete"
            ),
        )
    if _vm_ha_snapshot_is_rearmable(snapshot):
        return VMHACommandResult(
            outcome=(VMHACommandOutcome.PLANNED if dry_run else VMHACommandOutcome.ACTION_REQUIRED),
            classification=VMHACommandClassification.STANDBY_REARM,
            health=VMHACommandHealth.DEGRADED,
            effective_config_file=config_path,
            actions=(*actions, "rearm-exact-standby"),
            reasons=reasons,
            next_action=(
                "rerun without --dry-run to rearm the exact stopped standby" if dry_run else None
            ),
        )
    if _vm_ha_snapshot_is_apply_owned_transition(snapshot):
        provisioning = snapshot.lifecycle_state is not None and (
            snapshot.lifecycle_state.status
            in {VMHALifecycleStatus.REMOVED, VMHALifecycleStatus.DESTROYED}
        )
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.APPLY_REQUIRED,
            health=health,
            reason=(
                "apply-provisioning-required"
                if provisioning
                else "apply-transaction-resume-required"
            ),
            next_action=f"run nebius-vpngw apply --local-config-file {quoted_config} --dry-run",
            actions=actions,
        )
    if view.overall == "TRANSITIONING" or view.action == "wait":
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.CONTROLLER_TRANSITION,
            health=VMHACommandHealth.TRANSITIONING,
            reason=reasons[0] if reasons else "controller-transition-in-progress",
            next_action="allow the current owner transaction to finish, then rerun vm-ha",
            actions=actions,
        )
    if snapshot.lifecycle_state is None or "lifecycle-status-unavailable" in reasons:
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.APPLY_REQUIRED,
            health=health,
            reason="lifecycle-not-materialized",
            next_action=f"run nebius-vpngw apply --local-config-file {quoted_config} --dry-run",
            actions=actions,
        )
    if _vm_ha_snapshot_is_missing_non_owner_candidate(snapshot):
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.APPLY_REQUIRED,
            health=health,
            reason="active-standby-replacement-required",
            next_action="run vm-ha to plan creation of the missing non-owner VM",
            actions=actions,
        )
    if any(reason in {"ssh-trust-unavailable", "member-address-unavailable"} for reason in reasons):
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=health,
            reason="ssh-trust-or-address-unavailable",
            next_action="pin both VM-HA SSH hosts exactly, then rerun vm-ha",
            actions=actions,
        )
    if any(reason.startswith("managed-mtls-") for reason in reasons):
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=health,
            reason="managed-mtls-requires-explicit-repair",
            next_action=f"run nebius-vpngw vm-ha --rotate-mtls --local-config-file {quoted_config}",
            actions=actions,
        )
    if "cloud-member-unavailable" in reasons:
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=health,
            reason="standby-cloud-resource-unavailable",
            next_action=(
                "inspect the exact non-owner Compute and boot disk; rerun vm-ha if the "
                "Compute is merely stopped; if either resource is absent, preserve the "
                "lifecycle files and service journals and escalate for an identity-bound "
                "standby replacement instead of recreating by name"
            ),
            actions=actions,
        )
    if view.action in {"repair-route-authority", "reconcile-generation"} or any(
        reason.startswith("route-") or reason == "agent-status-stale" for reason in reasons
    ):
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.APPLY_REQUIRED,
            health=health,
            reason="apply-owned-drift",
            next_action=f"run nebius-vpngw apply --local-config-file {quoted_config} --dry-run",
            actions=actions,
        )
    if _vm_ha_snapshot_is_replacement_policy_split(snapshot):
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.APPLY_REQUIRED,
            health=health,
            reason="replacement-policy-convergence-required",
            next_action="rerun vm-ha to reconcile the exact fresh replacement policy",
            actions=actions,
        )
    if "standby-auto-healing-policy-invalid" in reasons:
        return VMHACommandResult(
            outcome=VMHACommandOutcome.BLOCKED,
            classification=VMHACommandClassification.MAINTENANCE_POLICY,
            health=health,
            effective_config_file=config_path,
            actions=actions,
            reasons=reasons,
            next_action=(
                "run nebius-vpngw vm-ha --local-config-file "
                f"{quoted_config} --standby-auto-healing enabled"
            ),
        )
    return VMHACommandResult(
        outcome=VMHACommandOutcome.BLOCKED,
        classification=VMHACommandClassification.AMBIGUOUS_STATE,
        health=health,
        effective_config_file=config_path,
        actions=actions,
        reasons=reasons or ("authoritative-evidence-incomplete",),
        next_action="inspect vm-ha status and service journals before retrying",
    )


def _vm_ha_apply_plan_result(
    *,
    config_path: Path,
    prior: VMHACommandResult,
    report: _VMHAApplyPlanReport,
    dry_run: bool,
) -> VMHACommandResult:
    if report.has_destructive_changes:
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=prior.health,
            reason="destructive-gateway-recreation-required",
            next_action=(
                "review the exact VM diff and use the explicit apply recreation workflow; "
                "vm-ha did not approve owner downtime"
            ),
            actions=prior.actions,
            impact=report.impact,
        )
    if report.managed_ssh_action is not None and report.kind != "active-standby-replacement":
        return _vm_ha_action_required(
            config_path=config_path,
            classification=VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=prior.health,
            reason="ssh-trust-publication-required",
            next_action=(
                "review and publish exact VM-HA host trust through the supported apply "
                "workflow, then rerun vm-ha"
            ),
            actions=prior.actions,
            impact=report.impact,
        )
    approval = VMHACommandApproval(
        kind=report.kind,
        digest=report.digest,
        effects=report.effects,
        artifact_sha256=report.artifact_sha256,
    )
    missing_standby = report.kind == "active-standby-replacement"
    quoted_config = shlex.quote(str(config_path))
    reasons = ("active-standby-replacement-required",) if missing_standby else prior.reasons
    approved_command = (
        "run nebius-vpngw vm-ha --local-config-file "
        f"{quoted_config} --approve {report.digest} to create the missing non-owner VM"
    )
    return VMHACommandResult(
        outcome=VMHACommandOutcome.PLANNED if dry_run else VMHACommandOutcome.ACTION_REQUIRED,
        classification=VMHACommandClassification.VM_HA_REQUIRED,
        health=prior.health,
        effective_config_file=config_path,
        actions=prior.actions,
        reasons=reasons,
        impact=report.impact,
        approval=approval,
        next_action=(
            approved_command
            if missing_standby
            else (
                "rerun without --dry-run using --approve " + report.digest
                if report.impact.approval_required
                else "rerun without --dry-run; this plan does not require approval"
            )
            if dry_run
            else (
                "rerun with --approve " + report.digest if report.impact.approval_required else None
            )
        ),
    )


def _confirm_vm_ha_apply_plan(result: VMHACommandResult) -> bool:
    """Render one sanitized exact plan and ask for default-No approval."""

    approval = result.approval
    if approval is None:
        raise RuntimeError("interactive VM-HA approval requires an exact plan")
    impact = result.impact
    if impact is None or not impact.approval_required:
        raise RuntimeError("interactive VM-HA approval requires a material impact")
    if approval.kind == "active-standby-replacement":
        typer.echo("VM-HA detected that the non-owner VM is missing.", err=True)
        typer.echo(f"Impact: {impact.summary}.", err=True)
        prompt = (
            "Upgrade the serving-owner control services and create the missing non-owner VM now?"
            if impact.vpn_traffic_interruption
            else "Create the missing non-owner VM now?"
        )
        return typer.confirm(prompt, default=False, err=True)
    typer.echo("VM-HA needs approval because this plan has material impact.", err=True)
    typer.echo(f"Config: {result.effective_config_file}", err=True)
    typer.echo(f"Approval kind: {approval.kind}", err=True)
    typer.echo(f"Approval digest: {approval.digest}", err=True)
    if approval.artifact_sha256 is not None:
        typer.echo(f"Artifact SHA-256: {approval.artifact_sha256}", err=True)
    typer.echo(f"Impact: {impact.summary}.", err=True)
    typer.echo("Planned effects:", err=True)
    for effect in approval.effects:
        typer.echo(f"  - {effect.replace('-', ' ')}", err=True)
    return typer.confirm(
        "Proceed with this exact VM-HA plan?",
        default=False,
        err=True,
    )


def _vm_ha_result_requires_approval(result: VMHACommandResult) -> bool:
    """Fail closed unless the exact result positively classifies safe impact."""

    return result.impact is None or result.impact.approval_required


def _vm_ha_operator_declined(result: VMHACommandResult) -> VMHACommandResult:
    """Retain the exact plan while truthfully reporting zero effects."""

    approval = result.approval
    if approval is None:
        raise RuntimeError("declined VM-HA approval requires an exact plan")
    missing_standby = approval.kind == "active-standby-replacement"
    return VMHACommandResult(
        outcome=VMHACommandOutcome.ACTION_REQUIRED,
        classification=result.classification,
        health=result.health,
        effective_config_file=result.effective_config_file,
        actions=result.actions,
        reasons=dedupe_reason_codes([*result.reasons, "operator-declined-approval"]),
        impact=result.impact,
        approval=None if missing_standby else approval,
        next_action=(
            "rerun vm-ha and answer y to create the missing non-owner VM"
            if missing_standby
            else "rerun vm-ha and answer y, or use --approve " + approval.digest
        ),
    )


def _emit_vm_ha_command_result(
    result: VMHACommandResult,
    output_format: _VMHAOutputFormat,
) -> None:
    typer.echo(result.to_json() if output_format is _VMHAOutputFormat.JSON else result.to_text())


def _inspect_vm_ha_status_with_region(
    config_path: Path,
    *,
    region: str | None,
) -> _VMHACommandInspection:
    if region is None:
        return _inspect_vm_ha_command_status(config_path)
    return _inspect_vm_ha_command_status(config_path, region=region)


@dataclass(frozen=True)
class _VMHAArtifactRecoveryContext:
    local_config: dict[str, t.Any]
    plan: ResolvedDeploymentPlan
    lifecycle: VMHALifecycleState
    owner_instance: t.Any
    standby_instance: t.Any
    owner_role: str
    standby_role: str
    owner_target: str
    standby_target: str
    ssh_policy: SSHTrustPolicy
    owner_record: dict[str, t.Any]


def _vm_ha_artifact_recovery_topology(
    snapshot: _VMHAStatusSnapshot,
) -> tuple[_VMHAMemberEvidence, _VMHAMemberEvidence] | None:
    """Admit only one exact stale serving owner and one alias-free stopped standby."""

    lifecycle = snapshot.lifecycle_state
    transaction = None if lifecycle is None else getattr(lifecycle, "transaction", None)
    if not (
        lifecycle is not None
        and lifecycle.status is VMHALifecycleStatus.ACTIVE
        and transaction is not None
        and transaction.pending_effect is None
        and snapshot.authority.condition == "exact"
        and snapshot.authority.operation_id is None
        and snapshot.authority.owner_node_id is not None
    ):
        return None
    members = {member.node_id: member for member in snapshot.members}
    owner = members.get(snapshot.authority.owner_node_id)
    standby = next(
        (
            member
            for member in snapshot.members
            if member.node_id != snapshot.authority.owner_node_id
        ),
        None,
    )
    states = dict(snapshot.authority.member_compute_states)
    if not (
        owner is not None
        and standby is not None
        and len(members) == 2
        and states.get(owner.node_id) == InstanceCloudState.RUNNING.value
        and states.get(standby.node_id) == InstanceCloudState.STOPPED.value
        and owner.record is None
        and owner.condition == "blocked"
        and owner.reason == "agent-status-stale"
        and standby.record is None
    ):
        return None
    return owner, standby


def _vm_ha_artifact_recovery_owner_is_safe(
    record: t.Mapping[str, t.Any],
    *,
    owner_node_id: str,
    allow_rearm_progress: bool = False,
) -> bool:
    auto_healing = record.get("auto_healing")
    mtls = record.get("mtls")
    if not isinstance(auto_healing, dict) or not isinstance(mtls, dict):
        return False
    if not bool(
        record.get("state") == "active"
        and record.get("promotion_ready") is True
        and record.get("promotion_committed") is True
        and record.get("data_plane_mode") == "active"
        and record.get("observed_owner_node_id") == owner_node_id
        and record.get("apply_locked") is False
        and record.get("apply_operation_id") is None
        and record.get("pending_operation_id") is None
        and record.get("repair") is None
        and record.get("transfer_inhibition_operation_id") is None
        and mtls.get("state") == "healthy"
        and mtls.get("operation_id") is None
        and mtls.get("inhibited") is False
    ):
        return False

    rearm_phase = record.get("rearm_phase")
    rearm_reason = record.get("rearm_reason")
    auto_healing_state = auto_healing.get("state")
    accepted_start = auto_healing.get("accepted_start")
    ordinary_rearm = bool(
        auto_healing_state == StandbyAutoHealing.ENABLED.value
        and rearm_phase
        in (
            {"idle", "blocked", "starting", "running"}
            if allow_rearm_progress
            else {"idle", "blocked"}
        )
        and rearm_reason in {None, "compute-start-failed", "explicit-retry-required"}
        and (isinstance(accepted_start, bool) if allow_rearm_progress else accepted_start is False)
    )
    if ordinary_rearm:
        return True

    if not allow_rearm_progress:
        # The legacy owner can prove a bound committed-enabled local policy but
        # cannot obtain fresh peer-policy agreement from the intentionally
        # stopped standby. Its exact public projection is therefore blocked
        # and inhibited even though this is the condition the artifact-first
        # recovery exists to repair.
        pre_policy_agreement = bool(
            rearm_phase == "inhibited"
            and rearm_reason == "standby-auto-healing-peer-policy-unavailable"
            and auto_healing_state == "blocked"
            and auto_healing.get("peer_agrees") is False
            and accepted_start is False
        )
        # A pre-v2 runtime may have committed the exact transfer authorization
        # and then rejected it only because the mutable latest agreement was
        # refreshed during cutover.  The caller separately proves that v2 is
        # the only missing capability before admitting this one old raw reason.
        pre_v2_refreshed_agreement = bool(
            rearm_phase == "inhibited"
            and rearm_reason == "standby restoration policy authority changed"
            and auto_healing_state == "transitioning"
            and auto_healing.get("peer_agrees") is False
            and accepted_start is False
        )
        return pre_policy_agreement or pre_v2_refreshed_agreement

    # Once the capability-bearing owner is running, a valid transfer-bound
    # restoration record makes policy status transitional while the sole rearm
    # writer owns the already accepted Compute start. No other transitional
    # projection is admitted here.
    return bool(
        auto_healing_state == "transitioning"
        and accepted_start is True
        and rearm_phase in {"blocked", "starting", "running"}
        and rearm_reason in {None, "compute-start-failed", "explicit-retry-required"}
    )


def _inspect_vm_ha_artifact_standby_recovery(
    config_path: Path,
    inspection: _VMHACommandInspection,
    *,
    region: str | None,
) -> tuple[_VMHAApplyPlanReport, _VMHAArtifactRecoveryContext] | None:
    topology = _vm_ha_artifact_recovery_topology(inspection.snapshot)
    if topology is None:
        return None
    owner_evidence, standby_evidence = topology
    lifecycle = t.cast(VMHALifecycleState, inspection.snapshot.lifecycle_state)
    local_config = _load_config_with_region_override(config_path, region=region)
    plan = merge_with_peer_configs(local_config, [])
    if plan.vm_ha is None or plan.vm_ha.cluster_id != lifecycle.cluster_id:
        raise RuntimeError("artifact standby recovery lifecycle does not match the config")
    instances_by_node = {
        instance.vm_ha_node.node_id: instance
        for instance in plan.iter_instance_configs()
        if instance.vm_ha_node is not None
    }
    if set(instances_by_node) != {member.node_id for member in lifecycle.members}:
        raise RuntimeError("artifact standby recovery member identity is incomplete")
    lifecycle_by_node = {member.node_id: member for member in lifecycle.members}
    for node_id, instance in instances_by_node.items():
        node = instance.vm_ha_node
        member = lifecycle_by_node[node_id]
        if not (
            node is not None
            and instance.hostname == member.instance_name
            and node.role.value == member.role
            and str(instance.external_ip or "").strip() == member.public_ip
            and member.compute_id
            and member.network_interface_name
        ):
            raise RuntimeError("artifact standby recovery member identity drifted")
    owner_instance = instances_by_node[owner_evidence.node_id]
    standby_instance = instances_by_node[standby_evidence.node_id]
    owner_target = str(owner_instance.external_ip or "").strip()
    standby_target = str(standby_instance.external_ip or "").strip()
    if not owner_target or not standby_target:
        raise RuntimeError("artifact standby recovery requires both exact SSH targets")
    runtime_binding = _vm_ha_status_runtime_binding(lifecycle)

    def validate_stale_owner(
        payload: dict[str, t.Any],
        instance: t.Any,
    ) -> dict[str, t.Any]:
        capabilities = payload.get("controller_capabilities")
        if not (
            isinstance(capabilities, list)
            and all(isinstance(capability, str) for capability in capabilities)
            and STANDBY_RESTORATION_CAPABILITY not in capabilities
        ):
            raise _VMHAAgentStatusPermanent(
                "artifact standby recovery requires only the restoration capability to be stale"
            )
        patched = dict(payload)
        patched["controller_capabilities"] = [
            *capabilities,
            STANDBY_RESTORATION_CAPABILITY,
        ]
        validated = _validate_vm_ha_planned_status(
            patched,
            inst_cfg=instance,
            runtime_binding=runtime_binding,
        )
        _validate_vm_ha_display_status(
            patched,
            inst_cfg=instance,
            runtime_binding=runtime_binding,
        )
        result = dict(validated)
        result["controller_capabilities"] = list(capabilities)
        return result

    owner_node = owner_instance.vm_ha_node
    standby_node = standby_instance.vm_ha_node
    if owner_node is None or standby_node is None:
        raise RuntimeError("artifact standby recovery member manifests are incomplete")
    owner_role = str(owner_node.role.value)
    standby_role = str(standby_node.role.value)
    owner_records = _run_vm_ha_operator_command(
        local_config_file=config_path,
        agent_flag="--vm-ha-status",
        configured_role=owner_role,
        status_validator=validate_stale_owner,
    )
    if len(owner_records) != 1:
        raise RuntimeError("artifact standby recovery did not resolve one exact owner")
    owner_record = owner_records[0]
    if not _vm_ha_artifact_recovery_owner_is_safe(
        owner_record,
        owner_node_id=owner_evidence.node_id,
    ):
        raise RuntimeError("artifact standby recovery owner evidence is not safely admissible")
    vm_spec = (local_config.get("gateway_group") or {}).get("vm_spec") or {}
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    ssh_policy = require_vm_ha_ssh_policy(
        (
            (owner_instance.hostname, owner_target),
            (standby_instance.hostname, standby_target),
        ),
        enrollment_hosts=(),
        management_key_path=key_path,
        require_management_key=True,
        trust_scope=_vm_ha_ssh_trust_scope(local_config, plan),
    )
    artifact = _resolve_vm_ha_agent_artifact(ssh_policy)
    engine_digest = _canonical_digest(
        {
            "domain": "nebius-vpngw/artifact-standby-recovery-engine-v1",
            "authority_digest": inspection.snapshot.authority_digest,
            "artifact_sha256": artifact.sha256,
            "owner": {
                "cluster_id": owner_record.get("cluster_id"),
                "node_id": owner_record.get("node_id"),
                "generation_id": owner_record.get("generation_id"),
                "digests": owner_record.get("digests"),
                "state": owner_record.get("state"),
                "data_plane_mode": owner_record.get("data_plane_mode"),
                "promotion_ready": owner_record.get("promotion_ready"),
                "promotion_committed": owner_record.get("promotion_committed"),
                "observed_owner_node_id": owner_record.get("observed_owner_node_id"),
                "apply_locked": owner_record.get("apply_locked"),
                "apply_operation_id": owner_record.get("apply_operation_id"),
                "pending_operation_id": owner_record.get("pending_operation_id"),
                "repair": owner_record.get("repair"),
                "transfer_inhibition_operation_id": owner_record.get(
                    "transfer_inhibition_operation_id"
                ),
                "rearm_phase": owner_record.get("rearm_phase"),
                "rearm_reason": owner_record.get("rearm_reason"),
                "auto_healing": owner_record.get("auto_healing"),
                "mtls": {
                    key: t.cast(dict[str, t.Any], owner_record.get("mtls"))[key]
                    for key in (
                        "state",
                        "epoch",
                        "certificate_fingerprint",
                        "spki_fingerprint",
                        "peer_fingerprints",
                        "operation_id",
                        "operation_kind",
                        "target_epoch",
                        "peer_target_epoch",
                        "inhibited",
                        "inhibition_operation_id",
                    )
                },
                "route_reconciliation": owner_record.get("route_reconciliation"),
                "controller_capabilities": owner_record.get("controller_capabilities"),
            },
        }
    )
    effects = (
        "install-approved-artifact-on-serving-owner",
        "refresh-serving-owner-vm-ha-services",
        "request-owner-side-standby-rearm",
        "wait-for-exact-standby-compute-and-ssh",
        "install-approved-artifact-on-restored-standby",
        "resume-canonical-non-owner-first-apply",
        "verify-owner-forwarding-and-warm-standby",
    )
    impact = _vm_ha_apply_plan_impact(
        "artifact-standby-recovery",
        has_destructive_changes=False,
    )
    digest = _canonical_digest(
        {
            "domain": "nebius-vpngw/vm-ha-command-approval-v3",
            "engine_digest": engine_digest,
            "kind": "artifact-standby-recovery",
            "effects": effects,
            "has_destructive_changes": False,
            "managed_ssh_action": None,
            "artifact_sha256": artifact.sha256,
            "impact": impact.to_dict(),
        }
    )
    report = _VMHAApplyPlanReport(
        kind="artifact-standby-recovery",
        digest=digest,
        engine_digest=engine_digest,
        effects=effects,
        has_destructive_changes=False,
        managed_ssh_action=None,
        artifact_sha256=artifact.sha256,
        artifact=artifact,
        impact=impact,
    )
    return report, _VMHAArtifactRecoveryContext(
        local_config=local_config,
        plan=plan,
        lifecycle=lifecycle,
        owner_instance=owner_instance,
        standby_instance=standby_instance,
        owner_role=owner_role,
        standby_role=standby_role,
        owner_target=owner_target,
        standby_target=standby_target,
        ssh_policy=ssh_policy,
        owner_record=owner_record,
    )


def _execute_vm_ha_artifact_standby_recovery(
    config_path: Path,
    approved: _VMHAApplyPlanReport,
    *,
    region: str | None,
    progress_sink: _VMHAProgressSink | None,
) -> None:
    """Bootstrap the reachable owner, rearm through its sole writer, then apply."""

    current_inspection = _inspect_vm_ha_status_with_region(config_path, region=region)
    current = _inspect_vm_ha_artifact_standby_recovery(
        config_path,
        current_inspection,
        region=region,
    )
    if current is None or current[0] != approved:
        raise RuntimeError("artifact-standby-recovery-approval-authority-changed")
    _current_report, context = current
    artifact = approved.artifact
    if artifact is None or approved.artifact_sha256 != artifact.sha256:
        raise RuntimeError("artifact standby recovery has no exact approved artifact")
    artifact.verify_current()
    ssh = SSHPush(ssh_policy=context.ssh_policy)
    ssh.ensure_vm_ha_agent_package(
        context.owner_target,
        context.owner_instance,
        context.local_config,
        artifact=artifact,
    )
    ssh.refresh_vm_ha_control_services(
        context.owner_target,
        context.owner_instance,
        context.local_config,
    )

    vm_spec = (context.local_config.get("gateway_group") or {}).get("vm_spec") or {}
    username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
    raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    key_path = Path(raw_key).expanduser() if raw_key else None
    client_auth = _gateway_ssh_client_auth(context.local_config)
    runtime_binding = _vm_ha_status_runtime_binding(context.lifecycle)

    def upgraded_owner_is_safe(payload: dict[str, t.Any]) -> bool:
        try:
            validated = _validate_vm_ha_planned_status(
                payload,
                inst_cfg=context.owner_instance,
                runtime_binding=runtime_binding,
            )
        except _VMHAAgentStatusError:
            return False
        capabilities = validated.get("controller_capabilities")
        return bool(
            isinstance(capabilities, list)
            and STANDBY_RESTORATION_CAPABILITY in capabilities
            and _vm_ha_artifact_recovery_owner_is_safe(
                validated,
                owner_node_id=context.owner_instance.vm_ha_node.node_id,
                allow_rearm_progress=True,
            )
        )

    _wait_for_vm_ha_agent_status(
        predicate=upgraded_owner_is_safe,
        timeout_seconds=120.0,
        target=context.owner_target,
        hostname=context.owner_instance.hostname,
        username=username,
        key_path=key_path,
        client_auth=client_auth,
        ssh_policy=context.ssh_policy,
        inst_cfg=context.owner_instance,
        runtime_binding=runtime_binding,
        expected_apply_locked=False,
    )
    preparation = _prepare_vm_ha_planned_target(
        local_config_file=config_path,
        target_role=None,
        command="vm-ha",
        region=region,
        show_auth_progress=False,
        progress_sink=progress_sink,
        return_after_ssh=True,
    )
    if preparation.outcome != "standby-ssh-ready":
        raise RuntimeError("artifact standby recovery did not reach exact standby SSH")
    ssh.ensure_vm_ha_agent_package(
        context.standby_target,
        context.standby_instance,
        context.local_config,
        artifact=artifact,
    )
    ssh.refresh_vm_ha_control_services(
        context.standby_target,
        context.standby_instance,
        context.local_config,
    )

    canonical = _plan_vm_ha_apply_convergence(config_path, region=region)
    if not (
        canonical.kind == "apply-convergence"
        and canonical.has_destructive_changes is False
        and canonical.managed_ssh_action is None
        and canonical.artifact_sha256 == artifact.sha256
    ):
        raise RuntimeError("canonical apply changed after artifact standby recovery bootstrap")
    _execute_vm_ha_apply_convergence(
        config_path,
        canonical,
        region=region,
        progress_sink=progress_sink,
    )


def _plan_vm_ha_convergence_with_region(
    config_path: Path,
    *,
    region: str | None,
    inspection: _VMHACommandInspection | None = None,
) -> _VMHAApplyPlanReport:
    if inspection is not None:
        artifact_recovery = _inspect_vm_ha_artifact_standby_recovery(
            config_path,
            inspection,
            region=region,
        )
        if artifact_recovery is not None:
            return artifact_recovery[0]
    if region is None:
        return _plan_vm_ha_apply_convergence(config_path)
    return _plan_vm_ha_apply_convergence(config_path, region=region)


def _execute_vm_ha_convergence_with_region(
    config_path: Path,
    report: _VMHAApplyPlanReport,
    *,
    region: str | None,
    progress_sink: _VMHAProgressSink | None,
) -> None:
    if region is None:
        _execute_vm_ha_apply_convergence(
            config_path,
            report,
            progress_sink=progress_sink,
        )
        return
    _execute_vm_ha_apply_convergence(
        config_path,
        report,
        region=region,
        progress_sink=progress_sink,
    )


def _confirm_vm_ha_healthy(
    config_path: Path,
    first: _VMHACommandInspection,
    *,
    attempts: int = 3,
    region: str | None = None,
    progress_sink: _VMHAProgressSink | None = None,
) -> _VMHACommandInspection:
    """Require two consecutive fresh agreeing healthy observations."""

    with _vm_ha_progress_step(
        progress_sink,
        _VMHAProgressPhase.CONFIRM_HEALTH,
    ):
        previous = first
        for _attempt in range(1, attempts):
            time.sleep(1.0)
            current = _inspect_vm_ha_status_with_region(config_path, region=region)
            if (
                previous.snapshot.view.overall == "HEALTHY"
                and current.snapshot.view.overall == "HEALTHY"
                and previous.snapshot.authority_digest == current.snapshot.authority_digest
            ):
                return current
            previous = current
    raise RuntimeError("health-observations-did-not-agree")


def _observe_vm_ha_auto_healing_projection(
    config_path: Path,
    first: _VMHACommandInspection,
    *,
    desired: StandbyAutoHealing,
    expected_owner_node_id: str | None,
    expected_observation_digest: str,
    attempts: int = 31,
    region: str | None = None,
) -> _VMHACommandInspection:
    """Wait only for a terminal policy's public peer-heartbeat projection."""

    expected_overall = "HEALTHY" if desired is StandbyAutoHealing.ENABLED else "MAINTENANCE"
    current = first
    for attempt in range(attempts):
        if not (
            current.snapshot.authority.condition == "exact"
            and current.snapshot.authority.owner_node_id == expected_owner_node_id
            and current.snapshot.authority.observation_digest == expected_observation_digest
        ):
            raise RuntimeError(
                "standby-auto-healing-authority-changed-during-projection-observation"
            )
        auto_healing = next(
            (
                value
                for label, value, _detail in current.snapshot.view.summary_rows
                if label == "Auto-healing"
            ),
            None,
        )
        if current.snapshot.view.overall == expected_overall and auto_healing == desired.value:
            return current
        if current.snapshot.view.reasons != ("standby-auto-healing-policy-invalid",):
            return current
        if attempt + 1 >= attempts:
            return current
        time.sleep(1.0)
        current = _inspect_vm_ha_status_with_region(config_path, region=region)
    return current


def _observe_vm_ha_controller_transition(
    config_path: Path,
    first: _VMHACommandInspection,
    *,
    attempts: int = 3,
    region: str | None = None,
    progress_sink: _VMHAProgressSink | None = None,
) -> tuple[_VMHACommandInspection, str | None]:
    """Observe controller-owned progress without consuming its repair budget."""

    _emit_vm_ha_progress(
        progress_sink,
        _VMHAProgressPhase.OBSERVE_CONTROLLER,
        _VMHAProgressState.STARTED,
    )
    current = first
    seen = {
        (
            current.snapshot.view.overall,
            current.snapshot.view.action,
            current.snapshot.authority_digest,
        )
    }
    for _attempt in range(1, attempts):
        time.sleep(1.0)
        current = _inspect_vm_ha_status_with_region(config_path, region=region)
        if not (
            current.snapshot.view.overall == "TRANSITIONING"
            or current.snapshot.view.action == "wait"
        ):
            _emit_vm_ha_progress(
                progress_sink,
                _VMHAProgressPhase.OBSERVE_CONTROLLER,
                _VMHAProgressState.COMPLETED,
            )
            return current, None
        progress_key = (
            current.snapshot.view.overall,
            current.snapshot.view.action,
            current.snapshot.authority_digest,
        )
        if progress_key in seen:
            return current, "controller-no-progress"
        seen.add(progress_key)
        _emit_vm_ha_progress(
            progress_sink,
            _VMHAProgressPhase.OBSERVE_CONTROLLER,
            _VMHAProgressState.WAITING,
        )
    return current, "controller-observation-budget-exhausted"


@dataclass(frozen=True)
class _VMHAAutoHealingTransaction:
    operation_id: str
    coordinator_node_id: str
    predecessor_digest: str
    member_node_ids: tuple[str, str]


def _vm_ha_auto_healing_transaction(
    *,
    desired: StandbyAutoHealing,
    statuses: list[dict[str, t.Any]],
) -> _VMHAAutoHealingTransaction:
    """Resolve one authority-independent transaction or an exact resumable one."""

    if len(statuses) not in {1, 2}:
        raise RuntimeError("standby auto-healing policy requires one or two exact members")
    records = [AutoHealingPolicyRecord.from_mapping(status.get("record")) for status in statuses]
    first = records[0]
    member_node_ids = tuple(sorted((first.node_id, first.peer_node_id)))
    if len(member_node_ids) != 2 or member_node_ids[0] == member_node_ids[1]:
        raise RuntimeError("standby auto-healing policy has an invalid member set")
    if any(
        record.cluster_id != first.cluster_id
        or record.generation_id != first.generation_id
        or tuple(sorted((record.node_id, record.peer_node_id))) != member_node_ids
        for record in records
    ):
        raise RuntimeError("standby auto-healing policy member evidence conflicts")
    coordinator_node_id = member_node_ids[0]
    resumable = {
        (
            record.operation_id,
            record.coordinator_node_id,
            record.predecessor_digest,
        )
        for record in records
        if record.desired is desired
        and record.phase.value in {"prepared", "committed"}
        and (record.phase.value == "prepared" or record.peer_ack_digest != record.decision_digest)
    }
    if len(resumable) > 1:
        raise RuntimeError("standby auto-healing policy has conflicting transactions")
    if resumable:
        operation_id, resumable_coordinator, predecessor_digest = resumable.pop()
        if resumable_coordinator != coordinator_node_id:
            raise RuntimeError("standby auto-healing policy coordinator is inconsistent")
        return _VMHAAutoHealingTransaction(
            operation_id=operation_id,
            coordinator_node_id=coordinator_node_id,
            predecessor_digest=predecessor_digest,
            member_node_ids=t.cast(tuple[str, str], member_node_ids),
        )
    predecessor_digests = {record.decision_digest for record in records}
    if len(predecessor_digests) != 1:
        raise RuntimeError("standby auto-healing policy predecessors disagree")
    predecessor_digest = predecessor_digests.pop()
    operation_id = _canonical_digest(
        {
            "cluster_id": first.cluster_id,
            "coordinator_node_id": coordinator_node_id,
            "desired": desired.value,
            "generation_id": first.generation_id,
            "member_node_ids": list(member_node_ids),
            "predecessor_digest": predecessor_digest,
            "schema": "nebius-vpngw/vm-ha-auto-healing-transaction-v2",
        }
    )
    return _VMHAAutoHealingTransaction(
        operation_id=operation_id,
        coordinator_node_id=coordinator_node_id,
        predecessor_digest=predecessor_digest,
        member_node_ids=t.cast(tuple[str, str], member_node_ids),
    )


def _vm_ha_auto_healing_transaction_for_statuses(
    *,
    desired: StandbyAutoHealing,
    inspection: _VMHACommandInspection,
    statuses: list[dict[str, t.Any]],
) -> _VMHAAutoHealingTransaction:
    """Resolve an ordinary transaction or the deterministic missing-policy bootstrap."""

    if not any(status.get("record") is None for status in statuses):
        return _vm_ha_auto_healing_transaction(desired=desired, statuses=statuses)
    if len(statuses) != 1 or desired is not StandbyAutoHealing.ENABLED:
        raise RuntimeError("missing standby auto-healing policy cannot be changed safely")
    status = statuses[0]
    owner_node_id = inspection.snapshot.authority.owner_node_id
    member_node_ids = tuple(sorted(member.node_id for member in inspection.snapshot.members))
    if not (
        len(member_node_ids) == 2
        and len(set(member_node_ids)) == 2
        and owner_node_id == status.get("node_id")
        and owner_node_id in member_node_ids
        and status.get("cluster_id")
        and isinstance(status.get("generation_id"), str)
        and len(status["generation_id"]) == 64
    ):
        raise RuntimeError("missing policy bootstrap authority is incomplete")
    operation_id = _canonical_digest(
        {
            "cluster_id": status["cluster_id"],
            "desired": desired.value,
            "generation_id": status["generation_id"],
            "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-v2",
        }
    )
    return _VMHAAutoHealingTransaction(
        operation_id=operation_id,
        coordinator_node_id=member_node_ids[0],
        predecessor_digest="0" * 64,
        member_node_ids=t.cast(tuple[str, str], member_node_ids),
    )


def _vm_ha_auto_healing_recovery_required(
    *,
    desired: StandbyAutoHealing,
    inspection: _VMHACommandInspection,
    statuses: list[dict[str, t.Any]],
) -> bool:
    if len(statuses) == 2:
        return False
    if len(statuses) != 1:
        raise RuntimeError("offline standby recovery requires exactly one owner")
    status = statuses[0]
    authority = status.get("recovery_authority")
    recovery_phase = status.get("recovery_phase")
    recovery = status.get("recovery")
    if status.get("record") is None:
        if not (
            desired is StandbyAutoHealing.ENABLED
            and inspection.snapshot.authority.owner_node_id == status.get("node_id")
            and status.get("desired") is None
            and status.get("phase") == "blocked"
            and status.get("operation_id") is None
            and status.get("decision_digest") is None
            and status.get("peer_agrees") is False
            and status.get("accepted_start") is False
            and isinstance(authority, dict)
            and recovery_phase is None
            and recovery is None
        ):
            raise RuntimeError("missing policy bootstrap requires exact current-owner authority")
        return True

    record = AutoHealingPolicyRecord.from_mapping(status.get("record"))
    expected_initialize_operation = _canonical_digest(
        {
            "cluster_id": record.cluster_id,
            "desired": StandbyAutoHealing.ENABLED.value,
            "generation_id": record.generation_id,
            "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-v2",
        }
    )
    terminal_disabled = bool(
        record.desired is StandbyAutoHealing.DISABLED
        and record.phase.value == "committed"
        and record.peer_ack_digest == record.decision_digest
        and status.get("peer_agrees") is True
    )
    initialized_enabled = bool(
        record.desired is StandbyAutoHealing.ENABLED
        and record.phase.value == "committed"
        and record.operation_id == expected_initialize_operation
        and record.predecessor_digest == "0" * 64
        and record.peer_ack_digest is None
        and status.get("peer_agrees") is False
    )
    recovery_matches = recovery_phase is None
    if recovery_phase in {"armed", "consumed", "completed"}:
        parsed_recovery = AutoHealingRecoveryRecord.from_mapping(recovery)
        current_authority_matches = bool(
            isinstance(authority, dict)
            and parsed_recovery.allocation_id == authority.get("allocation_id")
            and (
                recovery_phase == AutoHealingRecoveryPhase.COMPLETED.value
                or (
                    parsed_recovery.promotion_receipt_id == authority.get("promotion_receipt_id")
                    and parsed_recovery.ownership_epoch == authority.get("ownership_epoch")
                )
            )
        )
        recovery_matches = bool(
            parsed_recovery.phase.value == recovery_phase
            and parsed_recovery.cluster_id == record.cluster_id
            and parsed_recovery.node_id == record.node_id
            and parsed_recovery.target_node_id == record.peer_node_id
            and parsed_recovery.generation_id == record.generation_id
            and parsed_recovery.desired is StandbyAutoHealing.ENABLED
            and parsed_recovery.policy_digest == record.decision_digest
            and parsed_recovery.predecessor_digest
            == (record.decision_digest if terminal_disabled else record.predecessor_digest)
            and current_authority_matches
        )
    if not (
        desired is StandbyAutoHealing.ENABLED
        and inspection.snapshot.authority.owner_node_id == record.node_id
        and (terminal_disabled or initialized_enabled)
        and isinstance(authority, dict)
        and recovery_matches
    ):
        raise RuntimeError(
            "offline standby recovery requires exact current-owner maintenance authority"
        )
    return True


def _vm_ha_auto_healing_recovery_start_required(
    *,
    inspection: _VMHACommandInspection,
    statuses: list[dict[str, t.Any]],
) -> bool:
    """Bind a repeated start to current cloud state after completed recovery."""

    if len(statuses) != 1 or statuses[0].get("recovery_phase") != (
        AutoHealingRecoveryPhase.COMPLETED.value
    ):
        return False
    record = AutoHealingPolicyRecord.from_mapping(statuses[0].get("record"))
    recovery = AutoHealingRecoveryRecord.from_mapping(statuses[0].get("recovery"))
    states = dict(inspection.snapshot.authority.member_compute_states)
    if not (
        set(states) == {record.node_id, record.peer_node_id}
        and recovery.node_id == record.node_id
        and recovery.target_node_id == record.peer_node_id
        and states.get(record.node_id) == InstanceCloudState.RUNNING.value
    ):
        raise RuntimeError("completed recovery Compute authority is incomplete")
    target_state = states[record.peer_node_id]
    if target_state == InstanceCloudState.STOPPED.value:
        return True
    if target_state in {
        InstanceCloudState.RUNNING.value,
        InstanceCloudState.TRANSITIONAL.value,
    }:
        return False
    raise RuntimeError("completed recovery target Compute state is unsafe")


def _run_vm_ha_auto_healing_statuses(
    *,
    local_config_file: Path,
    desired: StandbyAutoHealing,
    inspection: _VMHACommandInspection,
    require_capability: bool,
) -> list[dict[str, t.Any]]:
    """Read both members, or the exact owner for an offline enable recovery."""

    try:
        return _run_vm_ha_auto_healing_action(
            local_config_file=local_config_file,
            action="status",
            require_capability=require_capability,
        )
    except RuntimeError:
        owner_node_id = inspection.snapshot.authority.owner_node_id
        if desired is not StandbyAutoHealing.ENABLED or owner_node_id is None:
            raise
        try:
            statuses = _run_vm_ha_auto_healing_action(
                local_config_file=local_config_file,
                action="status",
                node_ids=frozenset({owner_node_id}),
                require_capability=require_capability,
            )
            _vm_ha_auto_healing_recovery_required(
                desired=desired,
                inspection=inspection,
                statuses=statuses,
            )
            return statuses
        except (RuntimeError, ValueError) as fallback_error:
            raise RuntimeError(
                "standby auto-healing policy is unavailable on an exact safe authority"
            ) from fallback_error


def _vm_ha_auto_healing_approval_digest(
    *,
    desired: StandbyAutoHealing,
    inspection: _VMHACommandInspection,
    statuses: list[dict[str, t.Any]],
    transaction: _VMHAAutoHealingTransaction,
    recovery_required: bool,
    prerequisite_effects: tuple[str, ...] = (),
    prerequisite_recovery_digests: tuple[str, ...] = (),
) -> str:
    if len(statuses) == 1 and statuses[0].get("recovery_phase") in {
        "armed",
        "consumed",
        "completed",
    }:
        recovery = AutoHealingRecoveryRecord.from_mapping(statuses[0].get("recovery"))
        if recovery.operation_id != transaction.operation_id:
            raise RuntimeError("standby recovery operation does not match the transaction")
        if recovery.phase is not AutoHealingRecoveryPhase.COMPLETED:
            return recovery.approval_digest
    payload: dict[str, t.Any] = {
        "authority_digest": inspection.snapshot.authority_digest,
        "desired": desired.value,
        "members": [
            {
                "accepted_start": status["accepted_start"],
                "decision_digest": status["decision_digest"],
                "desired": status["desired"],
                "node_id": status["node_id"],
                "operation_id": status["operation_id"],
                "phase": status["phase"],
                "recovery_authority": status["recovery_authority"],
                "recovery_phase": status["recovery_phase"],
            }
            for status in sorted(statuses, key=lambda item: str(item["node_id"]))
        ],
        "operation_id": transaction.operation_id,
        "recovery_required": recovery_required,
        "schema": "nebius-vpngw/vm-ha-auto-healing-approval-v2",
    }
    if prerequisite_effects or prerequisite_recovery_digests:
        payload["prerequisite_effects"] = list(prerequisite_effects)
        payload["prerequisite_recovery_digests"] = sorted(prerequisite_recovery_digests)
    return _canonical_digest(payload)


def _vm_ha_auto_healing_is_terminal(
    statuses: list[dict[str, t.Any]],
    desired: StandbyAutoHealing,
) -> bool:
    digests = {status.get("decision_digest") for status in statuses}
    operations = {status.get("operation_id") for status in statuses}
    return bool(
        len(statuses) == 2
        and all(
            status.get("desired") == desired.value
            and status.get("phase") == "committed"
            and status.get("peer_agrees") is True
            and status.get("accepted_start") is False
            and status.get("recovery_phase") not in {"armed", "consumed"}
            for status in statuses
        )
        and len(digests) == 1
        and None not in digests
        and len(operations) == 1
        and None not in operations
    )


def _exact_completed_vm_ha_auto_healing_recovery(
    statuses: list[dict[str, t.Any]],
) -> tuple[tuple[AutoHealingPolicyRecord, AutoHealingRecoveryRecord], ...] | None:
    """Bind cleanup to durable enabled agreement, independent of heartbeat freshness."""

    if not any(
        status.get("recovery_phase") == AutoHealingRecoveryPhase.COMPLETED.value
        for status in statuses
    ):
        return None
    if len(statuses) != 2:
        raise RuntimeError("completed recovery cleanup requires exactly two policy records")

    records: list[AutoHealingPolicyRecord] = []
    completed: list[tuple[AutoHealingPolicyRecord, AutoHealingRecoveryRecord]] = []
    for status in statuses:
        record = AutoHealingPolicyRecord.from_mapping(status.get("record"))
        if not (
            status.get("node_id") == record.node_id
            and status.get("generation_id") == record.generation_id
            and status.get("desired") == record.desired.value
            and status.get("operation_id") == record.operation_id
            and status.get("decision_digest") == record.decision_digest
            and status.get("phase") == record.phase.value
            and status.get("accepted_start") is False
            and record.desired is StandbyAutoHealing.ENABLED
            and record.phase is AutoHealingPolicyPhase.COMMITTED
        ):
            raise RuntimeError("completed recovery policy evidence is not committed enabled")
        records.append(record)

        recovery_phase = status.get("recovery_phase")
        recovery_value = status.get("recovery")
        if recovery_phase is None and recovery_value is None:
            continue
        if recovery_phase != AutoHealingRecoveryPhase.COMPLETED.value:
            raise RuntimeError("enabled policy has active recovery state")
        recovery = AutoHealingRecoveryRecord.from_mapping(recovery_value)
        if not (
            recovery.cluster_id == record.cluster_id
            and recovery.node_id == record.node_id
            and recovery.target_node_id == record.peer_node_id
            and recovery.generation_id == record.generation_id
            and recovery.desired is StandbyAutoHealing.ENABLED
            and recovery.operation_id == record.operation_id
            and recovery.policy_digest in {record.predecessor_digest, record.decision_digest}
            and recovery.predecessor_digest == record.predecessor_digest
        ):
            raise RuntimeError("completed standby recovery does not match enabled policy")
        completed.append((record, recovery))

    first = records[0]
    if not (
        {record.node_id for record in records} == {first.node_id, first.peer_node_id}
        and (
            all(status.get("peer_agrees") is True for status in statuses)
            or all(record.peer_ack_digest == record.decision_digest for record in records)
        )
        and all(
            record.cluster_id == first.cluster_id
            and record.generation_id == first.generation_id
            and {record.node_id, record.peer_node_id} == {first.node_id, first.peer_node_id}
            and record.operation_id == first.operation_id
            and record.coordinator_node_id == first.coordinator_node_id
            and record.predecessor_digest == first.predecessor_digest
            and record.decision_digest == first.decision_digest
            for record in records
        )
    ):
        raise RuntimeError("completed recovery policy records do not agree")
    if not completed:
        raise RuntimeError("completed recovery cleanup evidence is missing")
    return tuple(sorted(completed, key=lambda item: item[0].node_id))


def _clear_completed_vm_ha_auto_healing_recovery(
    *,
    config_path: Path,
    statuses: list[dict[str, t.Any]],
) -> bool:
    """Retry exact idempotent cleanup after an enabled transaction commits."""

    completed = _exact_completed_vm_ha_auto_healing_recovery(statuses)
    if completed is None:
        return False
    requests = {
        record.node_id: encode_policy_request(
            {
                "schema": AUTO_HEALING_REQUEST_SCHEMA,
                "operation_id": recovery.operation_id,
                "recovery_digest": auto_healing_recovery_digest(recovery),
            }
        )
        for record, recovery in completed
    }
    responses = _run_vm_ha_auto_healing_action(
        local_config_file=config_path,
        action="clear-recovery",
        requests=requests,
        node_ids=frozenset(requests),
    )
    if len(responses) != len(completed) or any(
        response.get("recovery") is not None or response.get("recovery_phase") is not None
        for response in responses
    ):
        raise RuntimeError("completed standby recovery was not safely cleared")
    return True


def _vm_ha_auto_healing_matches_cleaned_recovery(
    statuses: list[dict[str, t.Any]],
    completed_record: AutoHealingPolicyRecord,
) -> bool:
    """Require the cleanup reread to retain the exact committed enabled policy."""

    if len(statuses) != 2:
        return False
    try:
        records = [
            AutoHealingPolicyRecord.from_mapping(status.get("record")) for status in statuses
        ]
    except (TypeError, ValueError):
        return False
    return bool(
        {record.node_id for record in records}
        == {completed_record.node_id, completed_record.peer_node_id}
        and (
            all(status.get("peer_agrees") is True for status in statuses)
            or all(record.peer_ack_digest == record.decision_digest for record in records)
        )
        and all(
            status.get("node_id") == record.node_id
            and status.get("generation_id") == record.generation_id
            and status.get("desired") == record.desired.value
            and status.get("operation_id") == record.operation_id
            and status.get("decision_digest") == record.decision_digest
            and status.get("phase") == record.phase.value
            and status.get("accepted_start") is False
            and status.get("recovery") is None
            and status.get("recovery_phase") is None
            and record.cluster_id == completed_record.cluster_id
            and record.generation_id == completed_record.generation_id
            and record.desired is StandbyAutoHealing.ENABLED
            and record.operation_id == completed_record.operation_id
            and record.coordinator_node_id == completed_record.coordinator_node_id
            and record.predecessor_digest == completed_record.predecessor_digest
            and record.decision_digest == completed_record.decision_digest
            and record.phase is AutoHealingPolicyPhase.COMMITTED
            for status, record in zip(statuses, records, strict=True)
        )
    )


def _vm_ha_auto_healing_plan_result(
    *,
    config_path: Path,
    desired: StandbyAutoHealing,
    transaction: _VMHAAutoHealingTransaction,
    approval_digest: str,
    recovery_required: bool,
    bootstrap_required: bool,
    owner_initialization_required: bool,
    recovery_phase: str | None,
    recovery_start_required: bool,
    dry_run: bool,
    prerequisite_effects: tuple[str, ...] = (),
) -> VMHACommandResult:
    verb = "enable" if desired is StandbyAutoHealing.ENABLED else "disable"
    recovery_effects = (
        (
            *(("initialize-owner-policy",) if owner_initialization_required else ()),
            *(
                ("arm-owner-local-standby-recovery",)
                if recovery_phase is None or recovery_start_required
                else ()
            ),
            *(
                ("request-owner-rearm-start",)
                if recovery_phase in {None, AutoHealingRecoveryPhase.ARMED.value}
                or recovery_start_required
                else ()
            ),
            "wait-for-restored-standby-readiness",
            *(("initialize-restored-peer-policy",) if bootstrap_required else ()),
        )
        if recovery_required
        else ()
    )
    policy_effects = (
        (
            "wait-for-accepted-start-quiescence",
            "verify-two-member-policy-agreement",
        )
        if bootstrap_required
        else (
            f"prepare-{verb}-on-coordinator-{transaction.coordinator_node_id}",
            f"prepare-{verb}-on-peer",
            "wait-for-accepted-start-quiescence",
            f"commit-{verb}-on-peer",
            f"commit-{verb}-on-coordinator-{transaction.coordinator_node_id}",
            "verify-two-member-policy-agreement",
        )
    )
    approval = VMHACommandApproval(
        kind="standby-auto-healing-policy",
        digest=approval_digest,
        effects=(
            *prerequisite_effects,
            *recovery_effects,
            *policy_effects,
        ),
    )
    impact = VMHACommandImpact(
        summary="No VPN traffic interruption or destructive changes are expected",
        destructive=False,
        vpn_traffic_interruption=False,
        resource_creation=False,
    )
    return VMHACommandResult(
        outcome=VMHACommandOutcome.PLANNED if dry_run else VMHACommandOutcome.ACTION_REQUIRED,
        classification=VMHACommandClassification.MAINTENANCE_POLICY,
        health=VMHACommandHealth.TRANSITIONING,
        effective_config_file=config_path,
        actions=(f"standby-auto-healing-{verb}",),
        reasons=("standby-auto-healing-policy-change-planned",),
        impact=impact,
        approval=approval,
        next_action=(
            "rerun without --dry-run; this plan does not require approval" if dry_run else None
        ),
    )


def _vm_ha_auto_healing_cleanup_noop_plan_result(
    *,
    config_path: Path,
    desired: StandbyAutoHealing,
    inspection: _VMHACommandInspection,
    statuses: list[dict[str, t.Any]],
    recoveries: tuple[tuple[AutoHealingPolicyRecord, AutoHealingRecoveryRecord], ...],
) -> VMHACommandResult:
    """Describe cleanup plus the requested same-state result without mutation."""

    approval_digest = _canonical_digest(
        {
            "authority_digest": inspection.snapshot.authority_digest,
            "desired": desired.value,
            "members": [
                {
                    "decision_digest": status.get("decision_digest"),
                    "node_id": status.get("node_id"),
                    "operation_id": status.get("operation_id"),
                    "recovery_phase": status.get("recovery_phase"),
                }
                for status in sorted(statuses, key=lambda item: str(item.get("node_id")))
            ],
            "prerequisite_effects": ["clear-completed-recovery"],
            "recovery_digests": sorted(
                auto_healing_recovery_digest(recovery) for _, recovery in recoveries
            ),
            "requested_effects": [
                "verify-two-member-policy-agreement",
                "confirm-standby-auto-healing-already-enabled",
            ],
            "schema": "nebius-vpngw/vm-ha-auto-healing-cleanup-plan-v2",
        }
    )
    return VMHACommandResult(
        outcome=VMHACommandOutcome.PLANNED,
        classification=VMHACommandClassification.MAINTENANCE_POLICY,
        health=VMHACommandHealth.TRANSITIONING,
        effective_config_file=config_path,
        actions=("standby-auto-healing-already-enabled",),
        reasons=("standby-auto-healing-recovery-cleanup-planned",),
        impact=VMHACommandImpact(
            summary="No VPN traffic interruption or destructive changes are expected",
            destructive=False,
            vpn_traffic_interruption=False,
            resource_creation=False,
        ),
        approval=VMHACommandApproval(
            kind="standby-auto-healing-policy",
            digest=approval_digest,
            effects=(
                "clear-completed-recovery",
                "verify-two-member-policy-agreement",
                "confirm-standby-auto-healing-already-enabled",
            ),
        ),
        next_action="rerun without --dry-run; this plan does not require approval",
    )


def _arm_vm_ha_auto_healing_recovery(
    *,
    config_path: Path,
    owner_status: dict[str, t.Any],
    transaction: _VMHAAutoHealingTransaction,
    approval_digest: str,
    owner_node_id: str,
    target_node_id: str,
    stopped_revision: str,
) -> None:
    if owner_status.get("node_id") != owner_node_id:
        raise RuntimeError("standby recovery owner authority changed")
    record = AutoHealingPolicyRecord.from_mapping(owner_status.get("record"))
    authority = owner_status.get("recovery_authority")
    if not isinstance(authority, dict):
        raise RuntimeError("standby recovery promotion authority is unavailable")
    request = encode_policy_request(
        {
            "schema": AUTO_HEALING_REQUEST_SCHEMA,
            "desired": StandbyAutoHealing.ENABLED.value,
            "operation_id": transaction.operation_id,
            "approval_digest": approval_digest,
            "policy_digest": record.decision_digest,
            "predecessor_digest": transaction.predecessor_digest,
            "promotion_receipt_id": authority["promotion_receipt_id"],
            "allocation_id": authority["allocation_id"],
            "ownership_epoch": authority["ownership_epoch"],
            "stopped_revision": stopped_revision,
            "target_node_id": target_node_id,
        }
    )
    responses = _run_vm_ha_auto_healing_action(
        local_config_file=config_path,
        action="arm-recovery",
        requests={owner_node_id: request},
        node_ids=frozenset({owner_node_id}),
    )
    if len(responses) != 1 or responses[0].get("recovery_phase") not in {
        "armed",
        "consumed",
        "completed",
    }:
        raise RuntimeError("standby recovery intent was not durably armed")


def _cancel_vm_ha_auto_healing_recovery(
    *,
    config_path: Path,
    owner_node_id: str,
    transaction: _VMHAAutoHealingTransaction,
    approval_digest: str,
) -> None:
    request = encode_policy_request(
        {
            "schema": AUTO_HEALING_REQUEST_SCHEMA,
            "operation_id": transaction.operation_id,
            "approval_digest": approval_digest,
        }
    )
    responses = _run_vm_ha_auto_healing_action(
        local_config_file=config_path,
        action="cancel-recovery",
        requests={owner_node_id: request},
        node_ids=frozenset({owner_node_id}),
    )
    if len(responses) != 1 or responses[0].get("recovery_phase") is not None:
        raise RuntimeError("standby recovery intent was not safely cancelled")


def _execute_vm_ha_auto_healing_policy(
    *,
    config_path: Path,
    desired: StandbyAutoHealing,
    transaction: _VMHAAutoHealingTransaction,
    initial_statuses: list[dict[str, t.Any]],
    authority_guard: t.Callable[[], None] | None = None,
    timeout_seconds: float = 30.0,
) -> list[dict[str, t.Any]]:
    """Resume one deterministic coordinator-first/last CAS transaction."""

    statuses = initial_statuses
    by_node = {str(status["node_id"]): status for status in statuses}
    if set(by_node) != set(transaction.member_node_ids):
        raise RuntimeError("standby auto-healing policy requires exactly two members")
    coordinator = transaction.coordinator_node_id
    peer = next(node for node in transaction.member_node_ids if node != coordinator)

    def mutate(
        action: t.Literal["prepare", "commit"],
        node_id: str,
        peer_record: object,
    ) -> None:
        if authority_guard is not None:
            authority_guard()
        request = encode_policy_request(
            {
                "schema": AUTO_HEALING_REQUEST_SCHEMA,
                "desired": desired.value,
                "operation_id": transaction.operation_id,
                "coordinator_node_id": coordinator,
                "predecessor_digest": transaction.predecessor_digest,
                "peer_record": peer_record,
            }
        )
        changed = _run_vm_ha_auto_healing_action(
            local_config_file=config_path,
            action=action,
            requests={node_id: request},
            node_ids=frozenset({node_id}),
        )
        by_node[node_id] = changed[0]

    coordinator_status = by_node[coordinator]
    if not (
        coordinator_status.get("operation_id") == transaction.operation_id
        and coordinator_status.get("desired") == desired.value
        and coordinator_status.get("phase") in {"prepared", "committed"}
    ):
        mutate("prepare", coordinator, by_node[peer]["record"])
    peer_status = by_node[peer]
    if not (
        peer_status.get("operation_id") == transaction.operation_id
        and peer_status.get("desired") == desired.value
        and peer_status.get("phase") in {"prepared", "committed"}
    ):
        mutate("prepare", peer, by_node[coordinator]["record"])

    deadline = time.monotonic() + timeout_seconds
    while any(status.get("accepted_start") is True for status in by_node.values()):
        if time.monotonic() >= deadline:
            raise RuntimeError("accepted standby start did not quiesce before policy commit")
        time.sleep(1.0)
        statuses = _run_vm_ha_auto_healing_action(
            local_config_file=config_path,
            action="status",
        )
        by_node = {str(status["node_id"]): status for status in statuses}
        current = _vm_ha_auto_healing_transaction(desired=desired, statuses=statuses)
        if current != transaction:
            raise RuntimeError("standby auto-healing transaction changed before commit")

    if not (
        by_node[peer].get("phase") == "committed"
        and by_node[peer].get("operation_id") == transaction.operation_id
    ):
        mutate("commit", peer, by_node[coordinator]["record"])
    if not (
        by_node[coordinator].get("phase") == "committed"
        and by_node[coordinator].get("operation_id") == transaction.operation_id
    ):
        mutate("commit", coordinator, by_node[peer]["record"])

    while True:
        statuses = _run_vm_ha_auto_healing_action(
            local_config_file=config_path,
            action="status",
        )
        if _vm_ha_auto_healing_is_terminal(statuses, desired):
            return statuses
        if time.monotonic() >= deadline:
            raise RuntimeError("two-member standby auto-healing policy agreement timed out")
        time.sleep(1.0)


@app.command(
    name="vm-ha",
    options_metavar="",
    epilog=_command_help_epilog("vm-ha"),
)
def vm_ha(
    local_config_file: Path = typer.Option(
        ...,
        "--local-config-file",
        "-c",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Ordinary or explicit VM-HA local configuration",
    ),
    rotate_mtls: bool = typer.Option(
        False,
        "--rotate-mtls",
        help="Rotate both VM-HA mTLS identities through the explicit safe transaction",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Separate VM-HA candidate path for ordinary input",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Republish only an exact candidate that needs safe repair",
    ),
    standby_auto_healing: StandbyAutoHealing | None = typer.Option(
        None,
        "--standby-auto-healing",
        case_sensitive=False,
        help="Automatic standby restoration policy: enabled or disabled",
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        help=_NEBIUS_REGION_HELP,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Inspect and plan without file, cloud, or gateway mutation",
    ),
    approve: str | None = typer.Option(
        None,
        "--approve",
        help="Exact digest for the currently reported material approval domain",
    ),
    output_format: _VMHAOutputFormat = typer.Option(
        _VMHAOutputFormat.TEXT,
        "--output-format",
        case_sensitive=False,
        help="Result format: text or json",
    ),
) -> None:
    """Create, verify, and safely heal warm-standby VM HA idempotently.

    Plans that create resources, may interrupt VPN traffic, or make destructive
    changes require exact approval; interactive approval is default-No. A
    missing non-owner is confirmed and created in the same interactive command.
    Automation uses --approve DIGEST only for approval-required plans.
    Sanitized progress is written to stderr while the terminal result remains
    on stdout.
    """

    if rotate_mtls:
        if (
            output is not None
            or force
            or standby_auto_healing is not None
            or region is not None
            or output_format is _VMHAOutputFormat.JSON
        ):
            raise typer.BadParameter(
                "--rotate-mtls cannot be combined with --output, --force, "
                "--standby-auto-healing, --region, or --output-format json"
            )
        _run_vm_ha_mtls_rotation(
            local_config_file,
            dry_run=dry_run,
            approve=approve,
        )
        return

    if standby_auto_healing is not None and (output is not None or force):
        raise typer.BadParameter(
            "--standby-auto-healing cannot be combined with candidate --output or --force"
        )

    result: VMHACommandResult
    effective_config_file = local_config_file
    observed_health = VMHACommandHealth.UNKNOWN
    convergence_effects_may_have_started = False
    interactive = output_format is _VMHAOutputFormat.TEXT and _vm_ha_wizard_streams_interactive()
    progress_reporter = _vm_ha_progress_sink(sys.stderr)
    progress_sink: _VMHAProgressSink = progress_reporter
    try:
        resolve_progress_active = True
        _emit_vm_ha_progress(
            progress_sink,
            _VMHAProgressPhase.RESOLVE_CONFIG,
            _VMHAProgressState.STARTED,
        )

        def finish_resolve_progress() -> None:
            nonlocal resolve_progress_active
            if not resolve_progress_active:
                return
            resolve_progress_active = False
            _emit_vm_ha_progress(
                progress_sink,
                _VMHAProgressPhase.RESOLVE_CONFIG,
                _VMHAProgressState.COMPLETED,
            )

        try:
            effective = _resolve_vm_ha_effective_config(
                source_path=local_config_file,
                output=output,
                force=force,
                dry_run=dry_run,
                interactive=interactive,
                region=region,
                before_interactive_wizard=finish_resolve_progress,
                progress_sink=progress_sink,
                ordinary_trust_preflight=_ordinary_vm_ha_conversion_trust_prerequisite,
            )
        except BaseException:
            if resolve_progress_active:
                resolve_progress_active = False
                _emit_vm_ha_progress(
                    progress_sink,
                    _VMHAProgressPhase.RESOLVE_CONFIG,
                    _VMHAProgressState.FAILED,
                )
            raise
        finish_resolve_progress()
        if isinstance(effective, VMHACommandResult):
            effective_config_file = effective.effective_config_file
            if approve is None:
                result = effective
            else:
                result = VMHACommandResult(
                    outcome=VMHACommandOutcome.BLOCKED,
                    classification=VMHACommandClassification.AMBIGUOUS_STATE,
                    health=effective.health,
                    effective_config_file=effective.effective_config_file,
                    actions=effective.actions,
                    reasons=("approval-not-applicable",),
                    next_action=("rerun without --approve and use only a digest emitted by vm-ha"),
                )
        else:
            effective_config_file = effective.path
            with _vm_ha_progress_step(
                progress_sink,
                _VMHAProgressPhase.INSPECT_STATE,
            ):
                inspection = _inspect_vm_ha_status_with_region(
                    effective.path,
                    region=region,
                )
            actions = effective.actions
            policy_result: VMHACommandResult | None = None
            if standby_auto_healing is not None:
                policy_statuses = _run_vm_ha_auto_healing_statuses(
                    local_config_file=effective.path,
                    desired=standby_auto_healing,
                    inspection=inspection,
                    require_capability=True,
                )
                cleanup_prerequisite_effects: tuple[str, ...] = ()
                cleanup_recovery_digests: tuple[str, ...] = ()
                cleanup_plan_statuses: list[dict[str, t.Any]] | None = None
                cleanup_confirmed_enabled = False
                completed_recoveries: (
                    tuple[tuple[AutoHealingPolicyRecord, AutoHealingRecoveryRecord], ...] | None
                ) = None
                if len(policy_statuses) == 2 and any(
                    status.get("recovery_phase") == AutoHealingRecoveryPhase.COMPLETED.value
                    for status in policy_statuses
                ):
                    try:
                        completed_recoveries = _exact_completed_vm_ha_auto_healing_recovery(
                            policy_statuses
                        )
                        if completed_recoveries is None:
                            raise RuntimeError("completed recovery cleanup evidence is missing")
                        cleanup_prerequisite_effects = ("clear-completed-recovery",)
                        cleanup_recovery_digests = tuple(
                            sorted(
                                auto_healing_recovery_digest(recovery)
                                for _, recovery in completed_recoveries
                            )
                        )
                        cleanup_plan_statuses = policy_statuses
                        projected_policy_statuses = [
                            (
                                {
                                    **status,
                                    "recovery": None,
                                    "recovery_phase": None,
                                }
                                if status.get("recovery_phase")
                                == AutoHealingRecoveryPhase.COMPLETED.value
                                else status
                            )
                            for status in policy_statuses
                        ]
                        if dry_run:
                            policy_statuses = projected_policy_statuses
                        else:
                            if approve is not None:
                                if standby_auto_healing is StandbyAutoHealing.ENABLED:
                                    cleanup_plan = _vm_ha_auto_healing_cleanup_noop_plan_result(
                                        config_path=effective.path,
                                        desired=standby_auto_healing,
                                        inspection=inspection,
                                        statuses=cleanup_plan_statuses,
                                        recoveries=completed_recoveries,
                                    )
                                    if cleanup_plan.approval is None:
                                        raise RuntimeError(
                                            "completed recovery cleanup approval is missing"
                                        )
                                    cleanup_approval_digest = cleanup_plan.approval.digest
                                else:
                                    projected_transaction = (
                                        _vm_ha_auto_healing_transaction_for_statuses(
                                            desired=standby_auto_healing,
                                            inspection=inspection,
                                            statuses=projected_policy_statuses,
                                        )
                                    )
                                    projected_recovery_required = (
                                        _vm_ha_auto_healing_recovery_required(
                                            desired=standby_auto_healing,
                                            inspection=inspection,
                                            statuses=projected_policy_statuses,
                                        )
                                    )
                                    cleanup_approval_digest = _vm_ha_auto_healing_approval_digest(
                                        desired=standby_auto_healing,
                                        inspection=inspection,
                                        statuses=projected_policy_statuses,
                                        transaction=projected_transaction,
                                        recovery_required=projected_recovery_required,
                                        prerequisite_effects=(cleanup_prerequisite_effects),
                                        prerequisite_recovery_digests=(cleanup_recovery_digests),
                                    )
                                if approve != cleanup_approval_digest:
                                    policy_result = VMHACommandResult(
                                        outcome=VMHACommandOutcome.BLOCKED,
                                        classification=(
                                            VMHACommandClassification.MAINTENANCE_POLICY
                                        ),
                                        health=VMHACommandHealth.BLOCKED,
                                        effective_config_file=effective.path,
                                        actions=actions,
                                        reasons=("approval-digest-stale-or-incorrect",),
                                        next_action=(
                                            "rerun vm-ha to obtain the current exact "
                                            "approval digest"
                                        ),
                                    )
                            if policy_result is None:
                                if not _clear_completed_vm_ha_auto_healing_recovery(
                                    config_path=effective.path,
                                    statuses=cleanup_plan_statuses,
                                ):
                                    raise RuntimeError(
                                        "completed recovery cleanup evidence is missing"
                                    )
                                actions = (
                                    *actions,
                                    "standby-auto-healing-recovery-cleared",
                                )
                                policy_statuses = _run_vm_ha_auto_healing_statuses(
                                    local_config_file=effective.path,
                                    desired=standby_auto_healing,
                                    inspection=inspection,
                                    require_capability=True,
                                )
                                if any(
                                    status.get("recovery") is not None
                                    or status.get("recovery_phase") is not None
                                    for status in policy_statuses
                                ):
                                    raise RuntimeError(
                                        "completed standby recovery remained after cleanup"
                                    )
                                approve = None
                                cleanup_prerequisite_effects = ()
                                cleanup_recovery_digests = ()
                        if policy_result is None:
                            if not _vm_ha_auto_healing_matches_cleaned_recovery(
                                policy_statuses,
                                completed_recoveries[0][0],
                            ):
                                raise RuntimeError(
                                    "standby auto-healing policy changed during recovery cleanup"
                                )
                            cleanup_confirmed_enabled = True
                    except (OSError, RuntimeError, TypeError, ValueError):
                        policy_result = VMHACommandResult(
                            outcome=VMHACommandOutcome.BLOCKED,
                            classification=VMHACommandClassification.MAINTENANCE_POLICY,
                            health=VMHACommandHealth.BLOCKED,
                            effective_config_file=effective.path,
                            actions=actions,
                            reasons=("standby-auto-healing-recovery-cleanup-required",),
                            next_action=(
                                "inspect VM-HA agent journals for the recovery owner, then "
                                "rerun the same requested vm-ha command"
                            ),
                        )

                if policy_result is None and (
                    any(status.get("record") is None for status in policy_statuses)
                    and standby_auto_healing is StandbyAutoHealing.DISABLED
                ):
                    policy_result = VMHACommandResult(
                        outcome=VMHACommandOutcome.BLOCKED,
                        classification=VMHACommandClassification.MAINTENANCE_POLICY,
                        health=VMHACommandHealth.BLOCKED,
                        effective_config_file=effective.path,
                        actions=actions,
                        reasons=("standby-auto-healing-policy-unavailable",),
                        next_action=(
                            "run apply with this CLI version to initialize the explicit "
                            "two-member policy, then rerun vm-ha"
                        ),
                    )
                elif policy_result is None and (
                    _vm_ha_auto_healing_is_terminal(policy_statuses, standby_auto_healing)
                    or (
                        cleanup_confirmed_enabled
                        and standby_auto_healing is StandbyAutoHealing.ENABLED
                    )
                ):
                    if cleanup_prerequisite_effects:
                        if completed_recoveries is None or cleanup_plan_statuses is None:
                            raise RuntimeError("completed recovery cleanup evidence is missing")
                        policy_result = _vm_ha_auto_healing_cleanup_noop_plan_result(
                            config_path=effective.path,
                            desired=standby_auto_healing,
                            inspection=inspection,
                            statuses=cleanup_plan_statuses,
                            recoveries=completed_recoveries,
                        )
                    elif standby_auto_healing is StandbyAutoHealing.DISABLED:
                        policy_result = VMHACommandResult(
                            outcome=VMHACommandOutcome.MAINTENANCE,
                            classification=VMHACommandClassification.MAINTENANCE_POLICY,
                            health=VMHACommandHealth.MAINTENANCE,
                            effective_config_file=effective.path,
                            actions=(*actions, "standby-auto-healing-already-disabled"),
                            reasons=("maintenance-ready",),
                            next_action=(
                                "rerun vm-ha with --standby-auto-healing enabled "
                                "when maintenance is complete"
                            ),
                        )
                    else:
                        actions = (*actions, "standby-auto-healing-already-enabled")
                elif policy_result is None:
                    transaction = _vm_ha_auto_healing_transaction_for_statuses(
                        desired=standby_auto_healing,
                        inspection=inspection,
                        statuses=policy_statuses,
                    )
                    recovery_required = _vm_ha_auto_healing_recovery_required(
                        desired=standby_auto_healing,
                        inspection=inspection,
                        statuses=policy_statuses,
                    )
                    bootstrap_required = bool(
                        recovery_required
                        and len(policy_statuses) == 1
                        and policy_statuses[0].get("desired") != StandbyAutoHealing.DISABLED.value
                    )
                    owner_initialization_required = bool(
                        bootstrap_required and policy_statuses[0].get("record") is None
                    )
                    recovery_phase = (
                        t.cast(str | None, policy_statuses[0].get("recovery_phase"))
                        if recovery_required
                        else None
                    )
                    recovery_start_required = (
                        _vm_ha_auto_healing_recovery_start_required(
                            inspection=inspection,
                            statuses=policy_statuses,
                        )
                        if recovery_required
                        else False
                    )
                    approval_digest = _vm_ha_auto_healing_approval_digest(
                        desired=standby_auto_healing,
                        inspection=inspection,
                        statuses=policy_statuses,
                        transaction=transaction,
                        recovery_required=recovery_required,
                        prerequisite_effects=cleanup_prerequisite_effects,
                        prerequisite_recovery_digests=cleanup_recovery_digests,
                    )
                    planned_policy = _vm_ha_auto_healing_plan_result(
                        config_path=effective.path,
                        desired=standby_auto_healing,
                        transaction=transaction,
                        approval_digest=approval_digest,
                        recovery_required=recovery_required,
                        bootstrap_required=bootstrap_required,
                        owner_initialization_required=owner_initialization_required,
                        recovery_phase=recovery_phase,
                        recovery_start_required=recovery_start_required,
                        dry_run=dry_run,
                        prerequisite_effects=cleanup_prerequisite_effects,
                    )
                    if approve is not None and approve != approval_digest:
                        policy_result = VMHACommandResult(
                            outcome=VMHACommandOutcome.BLOCKED,
                            classification=VMHACommandClassification.MAINTENANCE_POLICY,
                            health=VMHACommandHealth.BLOCKED,
                            effective_config_file=effective.path,
                            actions=actions,
                            reasons=("approval-digest-stale-or-incorrect",),
                            next_action="rerun vm-ha to obtain the current exact approval digest",
                        )
                    elif dry_run or (
                        approve is None
                        and not interactive
                        and _vm_ha_result_requires_approval(planned_policy)
                    ):
                        policy_result = planned_policy
                    elif (
                        approve is None
                        and _vm_ha_result_requires_approval(planned_policy)
                        and not _confirm_vm_ha_apply_plan(planned_policy)
                    ):
                        policy_result = _vm_ha_operator_declined(planned_policy)
                    else:
                        with VMHAApplyLock(
                            project_id=inspection.project_id,
                            gateway_name=inspection.gateway_name,
                        ):
                            current_inspection = _inspect_vm_ha_status_with_region(
                                effective.path,
                                region=region,
                            )
                            current_statuses = _run_vm_ha_auto_healing_statuses(
                                local_config_file=effective.path,
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                require_capability=True,
                            )
                            current_transaction = _vm_ha_auto_healing_transaction_for_statuses(
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                statuses=current_statuses,
                            )
                            current_recovery_required = _vm_ha_auto_healing_recovery_required(
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                statuses=current_statuses,
                            )
                            current_bootstrap_required = bool(
                                current_recovery_required
                                and len(current_statuses) == 1
                                and current_statuses[0].get("desired")
                                != StandbyAutoHealing.DISABLED.value
                            )
                            current_owner_initialization_required = bool(
                                current_bootstrap_required
                                and current_statuses[0].get("record") is None
                            )
                            current_recovery_start_required = (
                                _vm_ha_auto_healing_recovery_start_required(
                                    inspection=current_inspection,
                                    statuses=current_statuses,
                                )
                                if current_recovery_required
                                else False
                            )
                            current_approval_digest = _vm_ha_auto_healing_approval_digest(
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                statuses=current_statuses,
                                transaction=current_transaction,
                                recovery_required=current_recovery_required,
                            )
                            if not (
                                current_transaction == transaction
                                and current_approval_digest == approval_digest
                                and current_bootstrap_required == bootstrap_required
                                and current_owner_initialization_required
                                == owner_initialization_required
                                and current_recovery_start_required == recovery_start_required
                            ):
                                raise RuntimeError(
                                    "standby-auto-healing-approval-authority-changed"
                                )
                            recovery_owner_node_id: str | None = None
                            if current_recovery_required:
                                owner_status = current_statuses[0]
                                recovery_owner_node_id = str(owner_status["node_id"])
                                if current_owner_initialization_required:
                                    initialized = _run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="initialize",
                                        node_ids=frozenset({recovery_owner_node_id}),
                                    )
                                    if len(initialized) != 1:
                                        raise RuntimeError(
                                            "owner policy initialization did not complete exactly"
                                        )
                                    current_statuses = _run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="status",
                                        node_ids=frozenset({recovery_owner_node_id}),
                                    )
                                    if (
                                        _vm_ha_auto_healing_transaction(
                                            desired=standby_auto_healing,
                                            statuses=current_statuses,
                                        )
                                        != transaction
                                    ):
                                        raise RuntimeError(
                                            "owner policy initialization changed the approved transaction"
                                        )
                                    _vm_ha_auto_healing_recovery_required(
                                        desired=standby_auto_healing,
                                        inspection=current_inspection,
                                        statuses=current_statuses,
                                    )
                                    owner_status = current_statuses[0]

                                current_recovery_phase = owner_status.get("recovery_phase")

                                def arm_recovery(
                                    owner_node_id: str,
                                    target_node_id: str,
                                    stopped_revision: str,
                                ) -> None:
                                    if (
                                        current_recovery_phase
                                        == AutoHealingRecoveryPhase.COMPLETED.value
                                        and not current_recovery_start_required
                                    ):
                                        raise RuntimeError(
                                            "completed standby recovery approval does not "
                                            "authorize another start"
                                        )
                                    _arm_vm_ha_auto_healing_recovery(
                                        config_path=effective.path,
                                        owner_status=owner_status,
                                        transaction=transaction,
                                        approval_digest=approval_digest,
                                        owner_node_id=owner_node_id,
                                        target_node_id=target_node_id,
                                        stopped_revision=stopped_revision,
                                    )

                                def exact_recovery_progress() -> bool:
                                    recovery_statuses = _run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="status",
                                        node_ids=frozenset({recovery_owner_node_id}),
                                    )
                                    if len(recovery_statuses) != 1:
                                        return False
                                    recovery_status = recovery_statuses[0]
                                    if recovery_status.get("recovery_phase") not in {
                                        AutoHealingRecoveryPhase.CONSUMED.value,
                                        AutoHealingRecoveryPhase.COMPLETED.value,
                                    }:
                                        return False
                                    record = AutoHealingPolicyRecord.from_mapping(
                                        recovery_status.get("record")
                                    )
                                    recovery = AutoHealingRecoveryRecord.from_mapping(
                                        recovery_status.get("recovery")
                                    )
                                    return bool(
                                        recovery_status.get("node_id") == recovery_owner_node_id
                                        and record.operation_id == transaction.operation_id
                                        and record.desired is StandbyAutoHealing.ENABLED
                                        and recovery.node_id == recovery_owner_node_id
                                        and recovery.target_node_id in transaction.member_node_ids
                                        and recovery.target_node_id != recovery_owner_node_id
                                        and recovery.desired is StandbyAutoHealing.ENABLED
                                        and recovery.operation_id == transaction.operation_id
                                        and recovery.approval_digest == approval_digest
                                        and recovery.policy_digest == record.decision_digest
                                        and recovery.predecessor_digest == record.predecessor_digest
                                    )

                                _prepare_vm_ha_planned_target(
                                    local_config_file=effective.path,
                                    target_role=None,
                                    region=region,
                                    show_auth_progress=False,
                                    progress_sink=progress_sink,
                                    before_rearm_request=arm_recovery,
                                    on_rearm_authorization_aborted=lambda: (
                                        _cancel_vm_ha_auto_healing_recovery(
                                            config_path=effective.path,
                                            owner_node_id=recovery_owner_node_id,
                                            transaction=transaction,
                                            approval_digest=approval_digest,
                                        )
                                    ),
                                    rearm_request_progress_is_exact=exact_recovery_progress,
                                )
                                current_statuses = _run_vm_ha_auto_healing_action(
                                    local_config_file=effective.path,
                                    action="status",
                                    require_capability=True,
                                )
                                if current_bootstrap_required:
                                    peer_node_id = next(
                                        node_id
                                        for node_id in transaction.member_node_ids
                                        if node_id != recovery_owner_node_id
                                    )
                                    _run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="initialize",
                                        node_ids=frozenset({peer_node_id}),
                                    )
                                    current_statuses = _run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="status",
                                        require_capability=True,
                                    )
                                if (
                                    _vm_ha_auto_healing_transaction(
                                        desired=standby_auto_healing,
                                        statuses=current_statuses,
                                    )
                                    != transaction
                                ):
                                    raise RuntimeError(
                                        "standby-auto-healing-transaction-changed-after-recovery"
                                    )
                                transaction_inspection = _inspect_vm_ha_status_with_region(
                                    effective.path,
                                    region=region,
                                )
                                if (
                                    transaction_inspection.snapshot.authority.owner_node_id
                                    != current_inspection.snapshot.authority.owner_node_id
                                ):
                                    raise RuntimeError(
                                        "standby-auto-healing-owner-changed-after-recovery"
                                    )
                            else:
                                transaction_inspection = current_inspection

                            def require_policy_authority() -> None:
                                observed = _inspect_vm_ha_status_with_region(
                                    effective.path,
                                    region=region,
                                )
                                if (
                                    observed.snapshot.authority_digest
                                    != transaction_inspection.snapshot.authority_digest
                                ):
                                    raise RuntimeError(
                                        "standby-auto-healing-authority-changed-during-transaction"
                                    )

                            terminal_statuses = _execute_vm_ha_auto_healing_policy(
                                config_path=effective.path,
                                desired=standby_auto_healing,
                                transaction=transaction,
                                initial_statuses=current_statuses,
                                authority_guard=require_policy_authority,
                            )
                            if recovery_owner_node_id is not None:
                                exact_recoveries = _exact_completed_vm_ha_auto_healing_recovery(
                                    terminal_statuses
                                )
                                if exact_recoveries is None or not any(
                                    record.node_id == recovery_owner_node_id
                                    for record, _ in exact_recoveries
                                ):
                                    raise RuntimeError(
                                        "completed standby recovery owner changed before cleanup"
                                    )
                                if not _clear_completed_vm_ha_auto_healing_recovery(
                                    config_path=effective.path,
                                    statuses=terminal_statuses,
                                ):
                                    raise RuntimeError(
                                        "completed standby recovery was not safely cleared"
                                    )
                        inspection = _inspect_vm_ha_status_with_region(
                            effective.path,
                            region=region,
                        )
                        inspection = _observe_vm_ha_auto_healing_projection(
                            effective.path,
                            inspection,
                            desired=standby_auto_healing,
                            expected_owner_node_id=(
                                transaction_inspection.snapshot.authority.owner_node_id
                            ),
                            expected_observation_digest=(
                                transaction_inspection.snapshot.authority.observation_digest
                            ),
                            region=region,
                        )
                        actions = (
                            *actions,
                            f"standby-auto-healing-{standby_auto_healing.value}",
                        )
                        if standby_auto_healing is StandbyAutoHealing.DISABLED:
                            if inspection.snapshot.view.overall != "MAINTENANCE":
                                raise RuntimeError(
                                    "standby auto-healing policy committed but stable "
                                    "maintenance authority was not re-proven"
                                )
                            policy_result = VMHACommandResult(
                                outcome=VMHACommandOutcome.MAINTENANCE,
                                classification=VMHACommandClassification.MAINTENANCE_POLICY,
                                health=VMHACommandHealth.MAINTENANCE,
                                effective_config_file=effective.path,
                                actions=actions,
                                reasons=("maintenance-ready",),
                                next_action=(
                                    "rerun vm-ha with --standby-auto-healing enabled "
                                    "when maintenance is complete"
                                ),
                            )
                        # This exact approval has been consumed by the policy
                        # transaction.  Any later convergence plan must obtain
                        # its own independently bound digest.
                        approve = None
            transition_reason: str | None = None
            if policy_result is not None:
                result = policy_result
            elif (
                inspection.snapshot.view.overall == "TRANSITIONING"
                or inspection.snapshot.view.action == "wait"
            ) and not _vm_ha_snapshot_is_apply_owned_transition(inspection.snapshot):
                inspection, transition_reason = _observe_vm_ha_controller_transition(
                    effective.path,
                    inspection,
                    region=region,
                    progress_sink=progress_sink,
                )
                actions = (*actions, "controller-observed")
            if policy_result is not None:
                pass
            elif inspection.snapshot.view.overall == "HEALTHY":
                inspection = _confirm_vm_ha_healthy(
                    effective.path,
                    inspection,
                    region=region,
                    progress_sink=progress_sink,
                )
                result = _vm_ha_result_from_snapshot(
                    config_path=effective.path,
                    snapshot=inspection.snapshot,
                    actions=actions,
                    dry_run=dry_run,
                )
            else:
                result = _vm_ha_result_from_snapshot(
                    config_path=effective.path,
                    snapshot=inspection.snapshot,
                    actions=actions,
                    dry_run=dry_run,
                )
            if policy_result is None and (
                transition_reason is not None
                and result.classification is VMHACommandClassification.CONTROLLER_TRANSITION
            ):
                result = VMHACommandResult(
                    outcome=result.outcome,
                    classification=result.classification,
                    health=result.health,
                    effective_config_file=result.effective_config_file,
                    actions=result.actions,
                    reasons=dedupe_reason_codes([*result.reasons, transition_reason]),
                    impact=result.impact,
                    approval=result.approval,
                    next_action=(
                        "inspect VM-HA controller service journals on both members, "
                        "then rerun vm-ha"
                    ),
                )
            if (
                policy_result is None
                and result.classification is VMHACommandClassification.APPLY_REQUIRED
            ):
                observed_health = result.health
                with _vm_ha_progress_step(
                    progress_sink,
                    _VMHAProgressPhase.PLAN_CONVERGENCE,
                ):
                    apply_plan = _plan_vm_ha_convergence_with_region(
                        effective.path,
                        region=region,
                        inspection=inspection,
                    )
                planned_result = _vm_ha_apply_plan_result(
                    config_path=effective.path,
                    prior=result,
                    report=apply_plan,
                    dry_run=dry_run,
                )
                if approve is not None and approve != apply_plan.digest:
                    result = VMHACommandResult(
                        outcome=VMHACommandOutcome.BLOCKED,
                        classification=VMHACommandClassification.AMBIGUOUS_STATE,
                        health=result.health,
                        effective_config_file=effective.path,
                        actions=effective.actions,
                        reasons=("approval-digest-stale-or-incorrect",),
                        next_action="rerun vm-ha to obtain the current exact approval digest",
                    )
                elif (
                    dry_run
                    or planned_result.approval is None
                    or (
                        approve is None
                        and not interactive
                        and _vm_ha_result_requires_approval(planned_result)
                        and not apply_plan.authorization_persisted
                    )
                ):
                    result = planned_result
                elif (
                    approve is None
                    and _vm_ha_result_requires_approval(planned_result)
                    and not apply_plan.authorization_persisted
                    and not _confirm_vm_ha_apply_plan(planned_result)
                ):
                    result = _vm_ha_operator_declined(planned_result)
                else:
                    with contextlib.ExitStack() as lock_stack:
                        with _vm_ha_progress_step(
                            progress_sink,
                            _VMHAProgressPhase.ACQUIRE_LOCK,
                        ):
                            lock_stack.enter_context(
                                VMHAApplyLock(
                                    project_id=inspection.project_id,
                                    gateway_name=inspection.gateway_name,
                                )
                            )
                        with _vm_ha_progress_step(
                            progress_sink,
                            _VMHAProgressPhase.REVALIDATE_APPROVAL,
                        ):
                            if apply_plan.kind == "artifact-standby-recovery":
                                current_inspection = _inspect_vm_ha_status_with_region(
                                    effective.path,
                                    region=region,
                                )
                                current_plan = _plan_vm_ha_convergence_with_region(
                                    effective.path,
                                    region=region,
                                    inspection=current_inspection,
                                )
                            else:
                                current_plan = _plan_vm_ha_convergence_with_region(
                                    effective.path,
                                    region=region,
                                )
                            if current_plan != apply_plan:
                                raise RuntimeError("apply-approval-authority-changed")
                            if current_plan.artifact is not None:
                                current_plan.artifact.verify_current()
                        convergence_effects_may_have_started = True
                        _execute_vm_ha_convergence_with_region(
                            effective.path,
                            current_plan,
                            region=region,
                            progress_sink=progress_sink,
                        )
                    with _vm_ha_progress_step(
                        progress_sink,
                        _VMHAProgressPhase.INSPECT_STATE,
                    ):
                        healed = _inspect_vm_ha_status_with_region(
                            effective.path,
                            region=region,
                        )
                    healed = _confirm_vm_ha_healthy(
                        effective.path,
                        healed,
                        region=region,
                        progress_sink=progress_sink,
                    )
                    result = _vm_ha_result_from_snapshot(
                        config_path=effective.path,
                        snapshot=healed.snapshot,
                        actions=(
                            *actions,
                            (
                                "created-missing-non-owner-vm"
                                if apply_plan.kind == "active-standby-replacement"
                                else "apply-converged"
                            ),
                        ),
                        dry_run=False,
                    )
            elif policy_result is None and approve is not None:
                result = VMHACommandResult(
                    outcome=VMHACommandOutcome.BLOCKED,
                    classification=VMHACommandClassification.AMBIGUOUS_STATE,
                    health=result.health,
                    effective_config_file=effective.path,
                    actions=effective.actions,
                    reasons=("approval-not-applicable",),
                    next_action="rerun without --approve and use only a digest emitted by vm-ha",
                )
            elif policy_result is None and (
                result.classification is VMHACommandClassification.STANDBY_REARM and not dry_run
            ):
                with contextlib.ExitStack() as lock_stack:
                    with _vm_ha_progress_step(
                        progress_sink,
                        _VMHAProgressPhase.ACQUIRE_LOCK,
                    ):
                        lock_stack.enter_context(
                            VMHAApplyLock(
                                project_id=inspection.project_id,
                                gateway_name=inspection.gateway_name,
                            )
                        )
                    with _vm_ha_progress_step(
                        progress_sink,
                        _VMHAProgressPhase.INSPECT_STATE,
                    ):
                        current = _inspect_vm_ha_status_with_region(
                            effective.path,
                            region=region,
                        )
                    if (
                        not _vm_ha_snapshot_is_rearmable(current.snapshot)
                        or current.snapshot.authority_digest != inspection.snapshot.authority_digest
                    ):
                        raise RuntimeError("rearm-authority-changed")
                    with (
                        contextlib.redirect_stdout(io.StringIO()),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        _prepare_vm_ha_planned_target(
                            local_config_file=effective.path,
                            target_role=None,
                            command="vm-ha",
                            region=region,
                            show_auth_progress=False,
                            progress_sink=progress_sink,
                        )
                with _vm_ha_progress_step(
                    progress_sink,
                    _VMHAProgressPhase.INSPECT_STATE,
                ):
                    healed = _inspect_vm_ha_status_with_region(
                        effective.path,
                        region=region,
                    )
                healed = _confirm_vm_ha_healthy(
                    effective.path,
                    healed,
                    region=region,
                    progress_sink=progress_sink,
                )
                result = _vm_ha_result_from_snapshot(
                    config_path=effective.path,
                    snapshot=healed.snapshot,
                    actions=(*actions, "standby-rearmed"),
                    dry_run=False,
                )
    except WizardInterrupted:
        result = VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=VMHACommandHealth.UNKNOWN,
            effective_config_file=output or _default_vm_ha_candidate_path(local_config_file),
            reasons=("input-interrupted",),
            next_action="rerun vm-ha when the required input is available",
        )
        _emit_vm_ha_command_result(result, output_format)
        raise typer.Exit(code=130) from None
    except (KeyboardInterrupt, EOFError, typer.Abort):
        result = VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=VMHACommandHealth.UNKNOWN,
            effective_config_file=effective_config_file,
            reasons=("interrupted",),
            next_action="rerun vm-ha",
        )
        _emit_vm_ha_command_result(result, output_format)
        raise typer.Exit(code=130) from None
    except WizardCancelled:
        result = _vm_ha_action_required(
            config_path=output or _default_vm_ha_candidate_path(local_config_file),
            classification=VMHACommandClassification.CONVERSION_REQUIRED,
            health=VMHACommandHealth.NOT_CONFIGURED,
            reason="conversion-cancelled",
            next_action="rerun vm-ha when ready to publish the candidate",
        )
    except _VMHAApplyPlanningFailed as error:
        if error.classification is VMHACommandClassification.FAILED:
            result = VMHACommandResult(
                outcome=VMHACommandOutcome.FAILED,
                classification=error.classification,
                health=observed_health,
                effective_config_file=effective_config_file,
                reasons=(error.reason,),
                next_action=error.next_action,
            )
        else:
            result = _vm_ha_action_required(
                config_path=effective_config_file,
                classification=error.classification,
                health=observed_health,
                reason=error.reason,
                next_action=error.next_action,
            )
    except VMHAAgentArtifactError as error:
        reason, next_action = _VM_HA_AGENT_ARTIFACT_PREREQUISITES[error.problem]
        if convergence_effects_may_have_started:
            reason = f"{reason}-during-convergence"
            next_action = (
                f"{next_action}; vm-ha will inspect durable checkpoints and resume "
                "idempotently, but gateway changes may already have started"
            )
        result = _vm_ha_action_required(
            config_path=effective_config_file,
            classification=VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=observed_health,
            reason=reason,
            next_action=next_action,
            actions=(
                ("convergence-effects-may-have-started",)
                if convergence_effects_may_have_started
                else ()
            ),
        )
    except typer.Exit as error:
        if _vm_ha_error_chain_has_sdk_code(
            error, "UNAUTHENTICATED"
        ) or error_chain_has_cli_authentication_failure(error):
            reason = "authentication-or-provider-unavailable"
            next_action = "restore authentication and rerun vm-ha"
        else:
            reason = "command-preflight-failed"
            next_action = "resolve the reported VM-HA preflight failure and rerun vm-ha"
        result = VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=VMHACommandHealth.UNKNOWN,
            effective_config_file=effective_config_file,
            reasons=(reason,),
            next_action=next_action,
        )
    except _VMHAApplyConvergenceFailed as error:
        result = VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=observed_health,
            effective_config_file=effective_config_file,
            actions=("convergence-effects-may-have-started",),
            reasons=(error.reason,),
            next_action=error.next_action,
        )
    except (OSError, RuntimeError, TypeError, ValueError, WizardValidationError):
        result = VMHACommandResult(
            outcome=VMHACommandOutcome.FAILED,
            classification=VMHACommandClassification.FAILED,
            health=VMHACommandHealth.UNKNOWN,
            effective_config_file=effective_config_file,
            reasons=("convergence-failed-safely",),
            next_action="run status and inspect VM-HA service journals before retrying",
        )
    finally:
        progress_reporter.close_unfinished()
    _emit_vm_ha_command_result(result, output_format)
    raise typer.Exit(code=result.exit_code)


_VM_HA_PLANNED_TRANSFER_FAILURE_DETAIL = (
    "VM-HA operation failed safely; run status and inspect VM-HA service journals"
)


@_with_vm_manager_lifetimes
def _run_vm_ha_planned_transfer(
    *,
    local_config_file: Path,
    target_role: t.Literal["active", "passive"],
    command: str,
    agent_flag: str,
    operation_name: str,
    start_message: str,
    success_subject: str,
    output_format: _VMHAOutputFormat,
) -> None:
    started_at = time.monotonic()
    try:
        preparation = _prepare_vm_ha_planned_target(
            local_config_file=local_config_file,
            target_role=target_role,
            command=command,
        )
        if preparation.outcome == "already-owner":
            if output_format is _VMHAOutputFormat.JSON:
                print(
                    json.dumps(
                        {
                            "schema": "nebius-vpngw/vm-ha-planned-transfer-result-v1",
                            "outcome": "already-owner",
                            "target_role": target_role,
                            "request_submitted": False,
                        },
                        sort_keys=True,
                    )
                )
            else:
                typer.echo(
                    f"{operation_name} not needed: the {target_role} VM already owns the gateway.",
                    err=True,
                )
            return
        context = preparation.terminal_context
        if context is None or context.target_role != target_role:
            raise RuntimeError("planned VM-HA transfer preparation lost terminal authority")
        typer.echo(start_message, err=True)
        records = _run_vm_ha_operator_command(
            local_config_file=local_config_file,
            agent_flag=agent_flag,
            configured_role=target_role,
            timeout_seconds=context.request_timeout_seconds,
        )
        if len(records) != 1:
            raise RuntimeError(
                f"manual VM-HA {operation_name.lower()} did not target exactly one "
                f"configured {target_role}"
            )
        request_fingerprint = planned_request_fingerprint(records[0])
        if output_format is _VMHAOutputFormat.JSON:
            print(json.dumps(records[0], sort_keys=True))
        completion = _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name=operation_name,
            started_at=started_at,
            request_fingerprint=request_fingerprint,
        )
    except _VMHAPlannedCutoverVerificationUnavailable:
        typer.echo(
            f"{operation_name} outcome is not yet verified: terminal cutover observation "
            "remained unavailable. The VM-HA controller may still be completing the "
            "transfer; run 'nebius-vpngw status --local-config-file <file>' before "
            "retrying and inspect VM-HA service journals only if status is not healthy.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except _VMHAPlannedCutoverVerificationIncomplete as error:
        typer.echo(
            f"{operation_name} cutover is not yet verified after "
            f"{error.elapsed_seconds:.1f}s total: exact ownership reproof did not "
            f"stabilize within its {error.budget_seconds:.1f}s cutover deadline. "
            "The VM-HA controller may still be completing the transfer; run "
            "'nebius-vpngw status --local-config-file <file>' before retrying and "
            "inspect VM-HA service journals only if status is not healthy.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except _VMHAPlannedRestorationVerificationUnavailable as error:
        typer.echo(
            f"{operation_name} cutover succeeded in {error.cutover_seconds:.1f}s, but "
            "standby restoration is not yet verified after "
            f"{error.restoration_seconds:.1f}s ({error.total_seconds:.1f}s total): "
            "terminal observation remained unavailable. Automatic background "
            "restoration may still be running; run 'nebius-vpngw status "
            "--local-config-file <file>' and use 'nebius-vpngw vm-ha "
            "--local-config-file <file>' only if status reports a blocked recovery.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except _VMHAPlannedRedundancyRestorationError as error:
        recovery_guidance = (
            "Automatic background restoration is still running; use "
            "'nebius-vpngw vm-ha --local-config-file <file>' only if status later "
            "reports a blocked recovery."
            if error.background_continues
            else "Run 'nebius-vpngw vm-ha --local-config-file <file>' to recover "
            "the blocked standby restoration."
        )
        typer.echo(
            f"{operation_name} cutover succeeded in {error.cutover_seconds:.1f}s, "
            "but standby restoration failed after "
            f"{error.restoration_seconds:.1f}s ({error.total_seconds:.1f}s total): {error}. "
            f"{recovery_guidance}",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        typer.echo(
            f"{operation_name} failed: {_VM_HA_PLANNED_TRANSFER_FAILURE_DETAIL}",
            err=True,
        )
        raise typer.Exit(code=1) from None
    typer.echo(
        f"{success_subject} is done successfully in {completion.total_seconds:.1f}s.",
        err=True,
    )


@failback_app.command(
    name="vm",
    epilog=_command_help_epilog("failback", "vm"),
)
def vm_ha_failback(
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
    ),
    output_format: _VMHAOutputFormat = typer.Option(
        _VMHAOutputFormat.TEXT,
        "--output-format",
        help="Output format: human-readable text or structured JSON",
    ),
) -> None:
    """Fail back through fencing, or no-op when the active already owns safely."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    _run_vm_ha_planned_transfer(
        local_config_file=config_path,
        target_role="active",
        command="failback vm",
        agent_flag="--vm-ha-manual-failback",
        operation_name="Failback",
        start_message="Failing back to the active VM...",
        success_subject="Failback to the active VM",
        output_format=output_format,
    )


@failover_app.command(
    name="vm",
    epilog=_command_help_epilog("failover", "vm"),
)
def vm_ha_failover(
    local_config_file: Path | None = typer.Option(
        None,
        "--local-config-file",
        "-c",
        exists=True,
        readable=True,
        help=f"Path to {DEFAULT_CONFIG_FILENAME}",
    ),
    output_format: _VMHAOutputFormat = typer.Option(
        _VMHAOutputFormat.TEXT,
        "--output-format",
        help="Output format: human-readable text or structured JSON",
    ),
) -> None:
    """Fail over through fencing, or no-op when the passive already owns safely."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    _run_vm_ha_planned_transfer(
        local_config_file=config_path,
        target_role="passive",
        command="failover vm",
        agent_flag="--vm-ha-manual-failover",
        operation_name="Failover",
        start_message="Failing over to the passive VM...",
        success_subject="Failover to the passive VM",
        output_format=output_format,
    )


_apply_help_command_order()


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
