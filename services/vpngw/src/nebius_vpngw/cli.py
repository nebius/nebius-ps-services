import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import typing as t
from pathlib import Path

import typer
from rich import print

from . import __version__
from .config_loader import (
    GatewayGroupSpec,
    ResolvedDeploymentPlan,
    build_config_from_peer_files,
    load_local_config,
    merge_with_peer_configs,
)
from .config_template import DEFAULT_CONFIG_TEMPLATE
from .deploy.route_manager import RouteManager
from .deploy.ssh_policy import (
    SSHTrustPolicy,
    build_openssh_base_command,
    require_explicit_known_hosts_file,
    require_vm_ha_ssh_policy,
)
from .deploy.ssh_push import SSHPush
from .deploy.vm_ha_identity import FormerVMHAProvenance, LegacyVMHAIdentity
from .deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
)
from .deploy.vm_manager import VMManager

DEFAULT_CONFIG_FILENAME = "nebius-vpngw.config.yaml"

app = typer.Typer(
    add_completion=False,
    help="""
Nebius VM-based VPN Gateway orchestrator

Most commands look for 'nebius-vpngw.config.yaml' in your current directory.
Use --local-config-file for status and mutating commands.
Use positional file arguments for create-config and validate-config.
""",
)

_HELP_COMMAND_ORDER = [
    "create-config",
    "prep-network",
    "validate-config",
    "apply",
    "status",
    "vm-ha-recover",
    "vm-ha-failback",
    "add-routes-local",
    "list-routes-local",
    "list-routes-remote",
    "restart-tunnel",
    "failover",
    "failback",
    "create-from-peer-config",
    "destroy",
]


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
    order_index = {name: idx for idx, name in enumerate(_HELP_COMMAND_ORDER)}
    indexed_commands = list(enumerate(app.registered_commands))
    indexed_commands.sort(
        key=lambda item: (
            order_index.get(_registered_command_name(item[1]), len(_HELP_COMMAND_ORDER)),
            item[0],
        )
    )
    app.registered_commands[:] = [command for _, command in indexed_commands]


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


def _format_traffic_state(
    hostname: str,
    tunnel_name: str,
    carrying_tunnel: str | None,
    tunnel_statuses: dict[str, str],
    bgp_states: dict[str, str],
    tunnel_bgp_map: dict[str, dict[str, str]],
) -> str:
    if carrying_tunnel == tunnel_name:
        return "[green]active path[/green]"

    bgp_state = _bgp_state_for_tunnel(hostname, tunnel_name, bgp_states, tunnel_bgp_map).lower()
    ipsec_state = str(tunnel_statuses.get(tunnel_name, "")).strip().upper()

    if "admin" in bgp_state:
        return "[red]admin down[/red]"
    if bgp_state == "established" or ipsec_state == "ESTABLISHED":
        return "[dim]standby[/dim]"
    if ipsec_state == "CONNECTING":
        return "[yellow]recovering[/yellow]"
    return "[red]down[/red]"


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
        "Configured roles remain unchanged by design. Manual failover is an operational override; run failback to restore steady state.",
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
            )
        if token:
            print("[green]Service Account token acquired.[/green]")
            os.environ["NEBIUS_IAM_TOKEN"] = token
            return token
        if vm_ha_enabled:
            raise RuntimeError("VM-HA Service Account flow returned no access token")
        print(
            "[yellow]Service Account flow returned no token; falling back to CLI config.[/yellow]"
        )
        return None
    except Exception as error:
        if vm_ha_enabled:
            print(f"[red]VM-HA Service Account setup failed:[/red] {error}")
            print("[yellow]VM HA grants only its reviewed Compute and VPC role allowlist.[/yellow]")
            raise typer.Exit(code=1) from error
        print(f"[yellow]Service Account setup skipped due to error:[/yellow] {error}")
        return None


def _active_vm_ha_lifecycle_state(
    *,
    plan: ResolvedDeploymentPlan,
    runtime_binding: t.Any,
    staged: t.Iterable[tuple[t.Any, str, t.Any]],
    project_id: str | None,
) -> VMHALifecycleState:
    """Bind staged remote provenance to the exact authoritative runtime identity."""

    if not project_id:
        raise RuntimeError("VM-HA lifecycle provenance requires an exact project ID")
    binding_nodes = {node.node_id: node for node in runtime_binding.nodes}
    members: list[VMHALifecycleMember] = []
    for inst_cfg, target, receipt in staged:
        node = inst_cfg.vm_ha_node
        if node is None or receipt.node_id != node.node_id:
            raise RuntimeError("VM-HA staged provenance does not match the node manifest")
        bound = binding_nodes.get(node.node_id)
        if bound is None or str(bound.role.value) != str(node.role.value):
            raise RuntimeError("VM-HA staged provenance does not match the runtime binding")
        members.append(
            VMHALifecycleMember(
                instance_index=inst_cfg.instance_index,
                instance_name=inst_cfg.hostname,
                node_id=node.node_id,
                role=str(node.role.value),
                compute_id=bound.compute_id,
                network_interface_name=bound.network_interface_name,
                public_ip=target,
            )
        )
    members.sort(key=lambda member: member.instance_index)
    return VMHALifecycleState(
        status=VMHALifecycleStatus.ACTIVE,
        project_id=project_id,
        gateway_name=plan.gateway_group.name,
        cluster_id=runtime_binding.cluster_id,
        allocation_id=runtime_binding.shared_allocation_id,
        allocation_name=(
            f"{plan.gateway_group.name}-{runtime_binding.cluster_id}-shared-private-ip"
        ),
        members=t.cast(tuple[VMHALifecycleMember, VMHALifecycleMember], tuple(members)),
    )


def _fetch_vm_ha_agent_status(
    *, target: str, username: str, key_path: Path | None
) -> dict[str, t.Any]:
    command = _build_ssh_base_cmd(key_path)
    command.extend(
        [
            f"{username}@{target}",
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-status",
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "VM-HA agent status command failed")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get("schema") != "nebius-vpngw/vm-ha-status-v1":
        raise ValueError("VM-HA agent returned an invalid status record")
    return payload


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

        items = []
        if hasattr(ilist, "items"):
            items = ilist.items
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


@app.command()
def apply(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: str | None = typer.Option(
        None,
        hidden=True,
        help="If provided, ensure a Service Account with this name and use it for auth",
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, hidden=True, help="Render actions without applying"),
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

    if dry_run:
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
        if lifecycle_state.status is VMHALifecycleStatus.ACTIVE:
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
    needs_operator_removal = bool(
        plan.vm_ha is None
        and lifecycle_state is not None
        and lifecycle_state.status
        in {VMHALifecycleStatus.ACTIVE, VMHALifecycleStatus.REMOVAL_IN_PROGRESS}
    )
    service_account_selected = bool(plan.vm_ha is None and sa and not needs_operator_removal)
    if service_account_selected:
        discovery_auth_token = _requested_apply_service_account_token(
            sa_name=t.cast(str, sa),
            tenant_id=tenant_id,
            project_id=proj_id,
            region_id=region_id,
            vm_ha_enabled=False,
        )
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
            require_explicit_known_hosts_file()
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
            enrollment_hosts = {
                instance.hostname
                for instance in planned_instances
                if recreate_gw or instance.hostname not in existing_members
            }
            ssh_policy = require_vm_ha_ssh_policy(
                tuple(
                    (
                        instance.hostname,
                        (instance.external_ip or "").strip() or instance.hostname,
                    )
                    for instance in planned_instances
                ),
                enrollment_hosts=enrollment_hosts,
            )
            discovery_manager.verify_vm_ha_existing_identities(
                existing_members,
                policy=ssh_policy,
                username=(
                    vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
        except (RuntimeError, ValueError) as error:
            print("[red]VM-HA SSH trust preflight failed before external mutation:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error
    elif needs_operator_removal or (lifecycle_state is None and sa is None):
        try:
            discovery_manager = VMManager(
                project_id=proj_id,
                zone=zone or plan.gateway_group.region,
                auth_token=discovery_auth_token,
                tenant_id=tenant_id,
                region_id=region_id,
                management_key_path=management_key_path,
            )
            if lifecycle_state is None:
                former_candidates = discovery_manager.discover_former_vm_ha_candidate_members(
                    plan.gateway_group
                )
            else:
                former_candidates = discovery_manager.discover_former_vm_ha_candidate_members(
                    plan.gateway_group,
                    lifecycle_state=lifecycle_state,
                )
            if former_candidates:
                require_explicit_known_hosts_file()
                ssh_policy = require_vm_ha_ssh_policy(
                    tuple(former_candidates.items()),
                    enrollment_hosts=set(),
                )
                discovery_manager.verify_vm_ha_existing_identities(
                    former_candidates,
                    policy=ssh_policy,
                    username=(
                        vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                    ),
                )
                if discovery_manager.former_vm_ha_candidate_provenance in {
                    FormerVMHAProvenance.LEGACY_RUNTIME,
                    FormerVMHAProvenance.LIFECYCLE_STATE,
                } and (
                    lifecycle_state is None or lifecycle_state.status is VMHALifecycleStatus.ACTIVE
                ):
                    inspector = SSHPush(ssh_policy=ssh_policy)
                    legacy_vm_ha_identities = {
                        name: inspector.inspect_legacy_vm_ha_identity(target, name, local_cfg)
                        for name, target in sorted(former_candidates.items())
                    }
                if lifecycle_state is None:
                    former_vm_ha_members = discovery_manager.discover_former_vm_ha_members(
                        plan.gateway_group,
                        legacy_identities=legacy_vm_ha_identities,
                    )
                else:
                    former_vm_ha_members = discovery_manager.discover_former_vm_ha_members(
                        plan.gateway_group,
                        legacy_identities=legacy_vm_ha_identities,
                        lifecycle_state=lifecycle_state,
                    )
                if former_vm_ha_members and lifecycle_state is None:
                    adopter = getattr(discovery_manager, "former_vm_ha_lifecycle_state", None)
                    if callable(adopter):
                        lifecycle_state = adopter(plan.gateway_group)
                        if legacy_vm_ha_identities is None:
                            inspector = SSHPush(ssh_policy=ssh_policy)
                            legacy_vm_ha_identities = {
                                name: inspector.inspect_legacy_vm_ha_identity(
                                    target, name, local_cfg
                                )
                                for name, target in sorted(former_vm_ha_members.items())
                            }
                        adopted_candidates = (
                            discovery_manager.discover_former_vm_ha_candidate_members(
                                plan.gateway_group,
                                lifecycle_state=lifecycle_state,
                            )
                        )
                        if adopted_candidates != former_vm_ha_members:
                            raise RuntimeError(
                                "Former VM-HA lifecycle adoption changed the member set"
                            )
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
    # calls only and deliberately has no mutation-capable service-account token.
    assert discovery_manager is not None
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

    if former_vm_ha_members:
        assert discovery_manager is not None
        try:
            if (
                lifecycle_state is not None
                and (lifecycle_state.status is VMHALifecycleStatus.ACTIVE)
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
                lifecycle_state = lifecycle_state.with_status(
                    VMHALifecycleStatus.REMOVAL_IN_PROGRESS
                )
                lifecycle_store.write_verified(lifecycle_state)
            planned_names = {instance.hostname for instance in plan.iter_instance_configs()}
            transition_ssh = SSHPush(ssh_policy=ssh_policy)
            for name, target in sorted(former_vm_ha_members.items()):
                transition_ssh.deactivate_vm_ha(
                    target,
                    local_cfg,
                    retire_member=name not in planned_names,
                )
            for name, target in sorted(former_vm_ha_members.items()):
                transition_ssh.verify_vm_ha_deactivated(
                    target,
                    local_cfg,
                    retire_member=name not in planned_names,
                )
            if lifecycle_state is not None:
                discovery_manager.verify_former_vm_ha_member_snapshot(
                    plan.gateway_group,
                    former_vm_ha_members,
                    lifecycle_state=lifecycle_state,
                )
                lifecycle_state = lifecycle_state.with_status(VMHALifecycleStatus.REMOVED)
                lifecycle_store.write_verified(lifecycle_state)
        except (RuntimeError, ValueError) as error:
            print("[red]Former VM-HA teardown failed before ordinary provisioning:[/red]")
            print(f"[yellow]  - {error}[/yellow]")
            raise typer.Exit(code=1) from error

    # Optional Service Account provisioning/auth. Ordinary no-transition --sa
    # selected this token before its first cloud read; proven removal waits until
    # both members are terminally non-HA.
    auth_token = discovery_auth_token
    if sa and not service_account_selected:
        auth_token = _requested_apply_service_account_token(
            sa_name=sa,
            tenant_id=tenant_id,
            project_id=proj_id,
            region_id=region_id,
            vm_ha_enabled=plan.vm_ha is not None,
        )
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

    if plan.vm_ha is None:
        print("[bold]Pushing per-VM resolved configs and reloading agent...[/bold]")
        for inst_cfg in plan.iter_instance_configs():
            target = _config_target(inst_cfg)
            if not target:
                print(
                    f"[dim]Skipping config push for {inst_cfg.hostname}: No IP address available[/dim]"
                )
                continue
            if (
                lifecycle_state is not None
                and lifecycle_state.status is VMHALifecycleStatus.REMOVED
            ):
                stale_vm_ha_removed = False
            else:
                stale_vm_ha_removed = bool(former_vm_ha_members) or ssh.deactivate_vm_ha(
                    target, local_cfg
                )
            ssh.push_config_and_reload(
                target,
                inst_cfg,
                local_cfg,
                fail_closed=stale_vm_ha_removed,
            )
    else:
        assert vm_ha_runtime_binding is not None
        ordered_instances = _vm_ha_apply_order(plan)
        print("[bold]Staging VM-HA configs passive-first without activation...[/bold]")
        staged: list[tuple[t.Any, str, t.Any]] = []
        for inst_cfg in ordered_instances:
            target = _config_target(inst_cfg)
            if not target:
                print(f"[red]Cannot stage VM-HA node {inst_cfg.hostname}: no SSH target[/red]")
                print(
                    "[yellow]No staged node was activated; rerun apply after SSH is ready.[/yellow]"
                )
                raise typer.Exit(code=1)
            receipt = ssh.stage_vm_ha_config(
                target,
                inst_cfg,
                local_cfg,
                runtime_binding=vm_ha_runtime_binding,
                credential_sources=inst_cfg.vm_ha_node.credential_sources,
            )
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

        lifecycle_state = _active_vm_ha_lifecycle_state(
            plan=plan,
            runtime_binding=vm_ha_runtime_binding,
            staged=staged,
            project_id=proj_id,
        )
        lifecycle_store.write_verified(lifecycle_state)

        print("[bold]Activating verified VM-HA configs passive-first...[/bold]")
        for inst_cfg, target, receipt in staged:
            ssh.push_config_and_reload(
                target,
                inst_cfg,
                local_cfg,
                staged_receipt=receipt,
                runtime_binding=vm_ha_runtime_binding,
            )
            print(f"[green]✓ Activated {receipt.node_id}[/green]")

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


@app.command(options_metavar="")
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

    Examples:
        nebius-vpngw validate-config my-config.yaml
        nebius-vpngw validate-config nebius-gcp-ha-vpngw.config.yaml
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


@app.command(options_metavar="")
def create_config(
    config_file: Path = typer.Argument(
        ..., help="Path for new configuration file (recommended: *.config.yaml)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing file if it exists"),
):
    """Create a new configuration file from template.

    Generates a new YAML configuration file with comprehensive comments and examples.
    The template includes all available options for gateway setup, crypto settings,
    BGP/static routing, and tunnel configuration.

    Security best practice: Use *.config.yaml extension - these files are git-ignored
    automatically to prevent committing sensitive information (IPs, ASNs, secrets).

    Safe to rerun: if the target file already contains the exact generated
    template, the command exits successfully without rewriting it.

    Examples:
        nebius-vpngw create-config gcp-ha-vpn.config.yaml
        nebius-vpngw create-config aws-vpn.config.yaml
        nebius-vpngw create-config test.yaml  # Warning: not git-ignored
    """
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

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

    # Create the config file
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


@app.command(name="prep-network")
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
    from rich.panel import Panel

    console = Console()

    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    try:
        cfg = load_local_config(
            local_config_file,
            allow_missing_placeholders=True,
            validate_schema=False,
        )
    except Exception as e:
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to load configuration[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e

    tenant_id = str(cfg.get("tenant_id") or "").strip() or None
    project_id = str(cfg.get("project_id") or "").strip() or None
    region_id = str(cfg.get("region_id") or "").strip() or None

    if not project_id or "${" in project_id:
        console.print(
            Panel.fit(
                "[bold red]✗ project_id is required for prep-network[/bold red]\n\n"
                "Set project_id directly in YAML or via ${PROJECT_ID} env var.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    gg = cfg.get("gateway_group", {}) or {}
    name = gg.get("name") or "nebius-vpn-gw"
    instance_count = int(gg.get("instance_count", 1))
    if instance_count < 1:
        console.print(
            Panel.fit(
                "[bold red]✗ instance_count must be >= 1[/bold red]",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

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
        desired_external_ips = external_ips if has_assigned_ips else []
        allocated_ips = vm_mgr.prepare_network(
            spec,
            allocate_ips=True,
            desired_external_ips=desired_external_ips,
        )
    except Exception as e:
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to prepare network[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e

    if has_assigned_ips:
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
        for inst_index, inst_ips in enumerate(allocated_ips):
            for nic_index, ip in enumerate(inst_ips):
                console.print(f"  - {name}-{inst_index} eth{nic_index}: [cyan]{ip}[/cyan]")
        return

    if not allocated_ips:
        console.print(
            Panel.fit(
                "[bold red]✗ No public IPs were allocated.[/bold red]",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    console.print()
    console.print("[bold]Reserved public IPs:[/bold]")
    for inst_index, inst_ips in enumerate(allocated_ips):
        for nic_index, ip in enumerate(inst_ips):
            console.print(f"  - {name}-{inst_index} eth{nic_index}: [cyan]{ip}[/cyan]")

    try:
        _update_external_ips_in_yaml(local_config_file, allocated_ips)
    except Exception as e:
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to update YAML with allocated IPs[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e

    console.print()
    console.print(
        Panel.fit(
            f"[bold green]✓ Updated config with allocated IPs[/bold green]\n\n"
            f"File: [cyan]{local_config_file}[/cyan]",
            title="[green]Success[/green]",
            border_style="green",
        )
    )


@app.command(options_metavar="")
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


@app.command()
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
    local_cfg = load_local_config(local_config_file)
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

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

        items = []
        if hasattr(ilist, "items"):
            items = ilist.items
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

    # Create status table
    table = Table(title="VPN Gateway Status", show_header=True, header_style="bold cyan")
    table.add_column("Tunnel", style="white")
    table.add_column("Configured Role", style="white")
    table.add_column("Traffic State", style="white")
    table.add_column("Gateway VM", style="white")
    table.add_column("IPsec", style="white")
    table.add_column("BGP", style="white")
    table.add_column("Peer IP", style="white")
    table.add_column("Encryption", style="white")
    table.add_column("BGP Uptime", style="white")

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
                    _build_ssh_base_cmd(None)
                    + [
                        f"ubuntu@{target}",
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
                        _build_ssh_base_cmd(None)
                        + [
                            f"ubuntu@{target}",
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
                    _build_ssh_base_cmd(None)
                    + [
                        f"ubuntu@{target}",
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
                        _build_ssh_base_cmd(None)
                        + [
                            f"ubuntu@{target}",
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
                _build_ssh_base_cmd(None)
                + [
                    f"ubuntu@{target}",
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
                    swanctl_carrying_by_connection: dict[str, str | None] = {}
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
                        connection_name = tunnel_connection_map.get(inst_cfg.hostname, {}).get(
                            tunnel_name
                        )
                        cache_key = connection_name or "__all__"
                        if cache_key not in swanctl_carrying_by_connection:
                            swanctl_carrying_by_connection[cache_key] = (
                                _select_carrying_tunnel_for_connection(
                                    inst_cfg.hostname,
                                    connection_name,
                                    tunnel_order,
                                    tunnel_statuses,
                                    bgp_states,
                                    tunnel_bgp_map,
                                    tunnel_role_map,
                                    tunnel_connection_map,
                                )
                            )
                        traffic_state_display = _format_traffic_state(
                            inst_cfg.hostname,
                            tunnel_name,
                            swanctl_carrying_by_connection[cache_key],
                            tunnel_statuses,
                            bgp_states,
                            tunnel_bgp_map,
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
                            traffic_state_display,
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
                _build_ssh_base_cmd(None)
                + [
                    f"ubuntu@{target}",
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
                ipsec_carrying_by_connection: dict[str, str | None] = {}
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
                    connection_name = tunnel_connection_map.get(inst_cfg.hostname, {}).get(
                        tunnel_name
                    )
                    cache_key = connection_name or "__all__"
                    if cache_key not in ipsec_carrying_by_connection:
                        ipsec_carrying_by_connection[cache_key] = (
                            _select_carrying_tunnel_for_connection(
                                inst_cfg.hostname,
                                connection_name,
                                list(tunnels.keys()),
                                tunnel_statuses,
                                bgp_states,
                                tunnel_bgp_map,
                                tunnel_role_map,
                                tunnel_connection_map,
                            )
                        )
                    traffic_state_display = _format_traffic_state(
                        inst_cfg.hostname,
                        tunnel_name,
                        ipsec_carrying_by_connection[cache_key],
                        tunnel_statuses,
                        bgp_states,
                        tunnel_bgp_map,
                    )

                    if peer_cfg_ip and peer_cfg_ip in bgp_uptime:
                        info["uptime"] = bgp_uptime[peer_cfg_ip]

                    table.add_row(
                        tunnel_name,
                        info.get("role", "-"),
                        traffic_state_display,
                        inst_cfg.hostname,
                        status_display,
                        bgp_display,
                        info["peer_ip"],
                        info["encryption"],
                        info["uptime"],
                    )
            else:
                # No tunnels found in output
                if "no matching" in output.lower() or "no active" in output.lower():
                    table.add_row(
                        "No tunnels",
                        "-",
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
                        _build_ssh_base_cmd(None)
                        + [
                            f"ubuntu@{target}",
                            "pgrep -x charon >/dev/null && echo active || echo inactive",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        shell=False,
                    )
                else:
                    result = subprocess.run(
                        _build_ssh_base_cmd(None)
                        + [
                            f"ubuntu@{target}",
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
                            _build_ssh_base_cmd(None)
                            + [
                                f"ubuntu@{target}",
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
    'broad_apipa': False,
    'orphaned_count': 0,
    'status': 'healthy'
}

# Check table 220
r = subprocess.run(['ip', 'rule', 'show'], capture_output=True, text=True)
if '220' in r.stdout:
    health['table_220'] = True
    health['status'] = 'error'

# Check broad APIPA
r = subprocess.run(['ip', 'route', 'show', '169.254.0.0/16'], capture_output=True, text=True)
if r.stdout.strip():
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
                _build_ssh_base_cmd(None)
                + [
                    f"ubuntu@{target}",
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
                    if health.get("table_220"):
                        table_220_display = "[red]EXISTS[/red]"
                    else:
                        table_220_display = "[green]OK[/green]"

                    # Format broad APIPA status
                    if health.get("broad_apipa"):
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

                    route_items = []
                    if hasattr(routes_list, "items"):
                        route_items = routes_list.items
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
        console.print("\n[bold]VM-HA Controller Status:[/bold]")
        vm_ha_table = Table(show_header=True, header_style="bold cyan")
        for column in (
            "Gateway VM",
            "Role",
            "State",
            "Data Plane",
            "Observed Owner",
            "Promotion Ready",
            "Reason / Recovery",
        ):
            vm_ha_table.add_column(column, style="white")
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        raw_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(raw_key).expanduser() if raw_key else None
        for inst_cfg in plan.iter_instance_configs():
            target = vm_ips.get(inst_cfg.hostname)
            if not target:
                continue
            try:
                vm_ha = _fetch_vm_ha_agent_status(
                    target=target,
                    username=username,
                    key_path=key_path,
                )
                reasons = ", ".join(str(reason) for reason in vm_ha.get("reasons") or [])
                recovery = str(vm_ha.get("recovery_action") or "")
                vm_ha_table.add_row(
                    inst_cfg.hostname,
                    str(vm_ha.get("configured_role") or "unknown"),
                    str(vm_ha.get("state") or "blocked"),
                    str(vm_ha.get("data_plane_mode") or "blocked"),
                    str(vm_ha.get("observed_owner_node_id") or "unknown"),
                    "yes" if vm_ha.get("promotion_ready") else "no",
                    reasons or recovery or "no authoritative status",
                )
            except Exception as error:
                vm_ha_table.add_row(
                    inst_cfg.hostname,
                    str(getattr(inst_cfg.vm_ha_node, "role", "unknown")),
                    "blocked",
                    "unknown",
                    "unknown",
                    "no",
                    f"status unavailable: {error}; run vm-ha-recover",
                )
        console.print(vm_ha_table)


@app.command(name="add-routes-local")
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
    """Ensure Nebius VPC routes exist for remote prefixes (Nebius → Remote).

    Safe to rerun. The command selects workload subnets by
    gateway.local_prefixes, adds only missing routes whose next-hop is the
    gateway private IP, and reconciles stale BGP advertisement state when the
    live gateway config no longer matches the current YAML.

    Optional `--swap-route-table` performs a blue/green route-table cutover:
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

    routes = RouteManager(project_id=proj_id, auth_token=auth_token)

    print("[bold]Ensuring VPC routes for remote prefixes on local subnets...[/bold]")
    routes.add_routes(
        plan,
        local_cfg,
        summarize=summarize,
        swap_route_table=swap_route_table,
        rollback_dir=rollback_dir,
    )
    routes.ensure_bgp_advertisements_current(plan, local_cfg)

    print("[green]Local route management completed.[/green]")


@app.command(name="list-routes-local")
def list_routes_local(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
):
    """List Nebius-side route state for workload subnets and advertised BGP routes.

    Shows:
    1. Route table entries on workload subnets selected by gateway.local_prefixes
    2. BGP routes being advertised to peer routers, organized by connection/tunnel
    3. Multi-connection-safe peer attribution by owning gateway VM and peer IP
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

    routes = RouteManager(project_id=proj_id, auth_token=auth_token)

    print("[bold]Listing VPC routes for local prefixes...[/bold]")
    try:
        routes.list_routes(plan, local_cfg)
    except Exception as e:
        print(f"[red]Failed to list routes:[/red] {e}")
        raise typer.Exit(code=1)


@app.command(name="list-routes-remote")
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

    # Get project_id for RouteManager (not really needed for this command but kept for consistency)
    proj_id = local_cfg.get("project_id") or ""

    routes = RouteManager(project_id=proj_id, auth_token=None)

    print("[bold]Querying remote routes from gateway VMs...[/bold]")
    try:
        routes.list_remote_routes(plan, local_cfg, connection_filter=connection)
    except Exception as e:
        print(f"[red]Failed to list remote routes:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def destroy(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Destroy gateway compute resources while preserving public IPs and VPC objects.

    Safe to rerun. Missing VMs, disks, routes, or private allocations are
    treated as already-cleaned-up state.
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
        # Import the client
        Client = None
        try:
            from nebius.sdk import SDK as _C

            Client = _C
        except Exception:
            try:
                from nebius.sdk import Client as _C

                Client = _C
            except Exception:
                try:
                    from nebius.client import Client as _C

                    Client = _C
                except Exception as e:
                    print(
                        "[red]Error: Nebius SDK not available. Install with 'pip install nebius'.[/red]"
                    )
                    raise typer.Exit(code=1) from e

        if (
            vm_mgr.tenant_id
            and vm_mgr.project_id
            and (vm_mgr.region_id or plan.gateway_group.region)
        ):
            try:
                client = Client(
                    tenant_id=vm_mgr.tenant_id,
                    project_id=vm_mgr.project_id,
                    region_id=vm_mgr.region_id or plan.gateway_group.region,
                )
            except TypeError:
                client = Client()
        else:
            client = Client()

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
        items = []
        if hasattr(ilist, "items"):
            items = ilist.items
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

            alloc_items = []
            if hasattr(alloc_list, "items"):
                alloc_items = alloc_list.items
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

            rt_items = []
            if hasattr(rt_list, "items"):
                rt_items = rt_list.items
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

                    route_items = []
                    if hasattr(routes_list, "items"):
                        route_items = routes_list.items
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


@app.command(name="restart-tunnel")
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

    This command connects to the gateway VMs via SSH, restarts the matching
    IPsec tunnel, and clears the matching BGP neighbor when the tunnel uses
    BGP. Useful for immediate recovery from tunnel and control-plane desync
    or after network maintenance. In multi-VM and multi-connection topologies,
    a named tunnel only targets its owning connection/instance.

    Examples:

      # Restart specific tunnel
      nebius-vpngw restart-tunnel gcp-ha-tunnel-1

      # Restart all tunnels
      nebius-vpngw restart-tunnel all

      # Use custom config file
      nebius-vpngw restart-tunnel gcp-ha-tunnel-1 -c my-config.yaml
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

        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

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


@app.command(name="failover")
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
    """Manually fail over traffic within one connection/instance to a passive tunnel."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)

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
                    "nebius-vpngw failover <passive-tunnel-name> --local-config-file <file>[/red]"
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

        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
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


@app.command(name="failback")
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
    """Restore traffic within one connection/instance to the active tunnel."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)

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
                    "nebius-vpngw failback <active-tunnel-name> --local-config-file <file>[/red]"
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

        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
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
    *, local_config_file: Path, agent_flag: str, configured_role: str | None = None
) -> list[dict[str, t.Any]]:
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
            timeout=30,
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
        expected_schema = (
            "nebius-vpngw/vm-ha-manual-failback-v1"
            if agent_flag == "--vm-ha-manual-failback"
            else "nebius-vpngw/vm-ha-status-v1"
        )
        if payload.get("schema") != expected_schema:
            raise ValueError(f"VM-HA action on {node.node_id} returned the wrong record type")
        if not (
            payload.get("cluster_id") == plan.vm_ha.cluster_id
            and payload.get("node_id") == node.node_id
            and payload.get("generation_id") == generation.generation_id
        ):
            raise ValueError(f"VM-HA action on {node.node_id} returned stale node identity")
        if (
            agent_flag != "--vm-ha-manual-failback"
            and payload.get("configured_role") != node.role.value
        ):
            raise ValueError(f"VM-HA status on {node.node_id} returned the wrong configured role")
        results.append(payload)
    return results


@app.command(name="vm-ha-recover")
def vm_ha_recover(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
) -> None:
    """Validate durable VM-HA recovery state on both nodes without bypassing fencing."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    for status_record in _run_vm_ha_operator_command(
        local_config_file=config_path,
        agent_flag="--vm-ha-recover",
    ):
        print(json.dumps(status_record, sort_keys=True))


@app.command(name="vm-ha-failback")
def vm_ha_failback(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
) -> None:
    """Request manual VM ownership failback through the normal fenced controller path."""

    config_path = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )
    records = _run_vm_ha_operator_command(
        local_config_file=config_path,
        agent_flag="--vm-ha-manual-failback",
        configured_role="active",
    )
    if len(records) != 1:
        raise RuntimeError("manual VM-HA failback did not target exactly one configured active")
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
