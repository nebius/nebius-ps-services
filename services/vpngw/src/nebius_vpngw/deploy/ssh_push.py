from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..config_loader import InstanceResolvedConfig


class SSHPush:
    """Push per-VM config and trigger agent reload via SSH using Paramiko.

    Looks for the following optional fields in the loaded YAML config under
    `gateway_group.vm_spec`:
      - ssh_username (default: "ubuntu")
      - ssh_private_key_path (if omitted, relies on SSH agent/known defaults)
    """

    def __init__(self) -> None:
        # Lazy import to avoid hard dependency when running dry-run
        self._paramiko = None
        self._wheel_path = None

    def _ensure_paramiko(self):
        if self._paramiko is None:
            import paramiko  # type: ignore

            self._paramiko = paramiko
        return self._paramiko

    def _build_wheel(self) -> Path | None:
        """Build the nebius-vpngw wheel package if not already built."""
        if self._wheel_path and self._wheel_path.exists():
            return self._wheel_path

        # Find project root (where pyproject.toml is). Prefer cwd if running from source tree,
        # else fall back to the installed module location.
        project_root = None
        cwd = Path.cwd()
        if (cwd / "pyproject.toml").exists():
            project_root = cwd
        else:
            current = Path(__file__).resolve()
            for parent in current.parents:
                if (parent / "pyproject.toml").exists():
                    project_root = parent
                    break

        if not project_root:
            print("[SSHPush] WARNING: Could not find project root with pyproject.toml")
            return None

        dist_dir = project_root / "dist"

        # Always attempt to build latest wheel if pyproject is present
        if (project_root / "pyproject.toml").exists():
            # Clean old wheels to prevent stale dependencies
            if dist_dir.exists():
                old_wheels = list(dist_dir.glob("nebius_vpngw-*.whl"))
                if old_wheels:
                    print(
                        f"[SSHPush] Removing {len(old_wheels)} old wheel(s) to ensure fresh build..."
                    )
                    for wheel in old_wheels:
                        wheel.unlink()

            print("[SSHPush] Building nebius-vpngw wheel package with python -m build...")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "build", "--wheel"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if result.returncode != 0:
                    print(f"[SSHPush] Wheel build failed: {result.stderr}")
            except FileNotFoundError:
                print(
                    "[SSHPush] WARNING: 'build' module not found. Install with: pip install build"
                )
            except Exception as e:
                print(f"[SSHPush] Wheel build error: {e}")

        # Reuse newest existing wheel (works with python -m build)
        if dist_dir.exists():
            wheels = sorted(
                dist_dir.glob("nebius_vpngw-*.whl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if wheels:
                self._wheel_path = wheels[0]
                print(f"[SSHPush] Using wheel: {self._wheel_path.name}")
                return self._wheel_path
            else:
                print("[SSHPush] No wheel found in dist/ after build attempt")
                return None
        else:
            print("[SSHPush] dist/ directory not found; wheel not built")
            return None

    def push_config_and_reload(
        self, ssh_target: str, inst_cfg: InstanceResolvedConfig, local_cfg: dict
    ) -> None:
        if not ssh_target:
            print(f"[SSHPush] No SSH target for instance {inst_cfg.instance_index}; skipping")
            return

        paramiko = self._ensure_paramiko()
        gg = local_cfg.get("gateway_group") or {}
        vm_spec = gg.get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        key_path: str | None = vm_spec.get("ssh_private_key_path") or os.environ.get(
            "VPNGW_SSH_KEY"
        )
        key_file = Path(key_path).expanduser() if key_path else None

        print(f"[SSHPush] Connecting to {ssh_target} as {username} ...")
        client = paramiko.SSHClient()
        # codeql[py/unsafe-ssh-host-key-policy] - VMs are created on demand; auto-add avoids breaking apply while still using SSH key auth.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=ssh_target,
                username=username,
                key_filename=str(key_file) if key_file else None,
                look_for_keys=True,
                allow_agent=True,
                timeout=15,
            )
        except Exception as e:
            error_msg = str(e).lower()
            print(f"[SSHPush] SSH connect failed to {ssh_target}: {e}")

            # Provide helpful guidance for common network issues
            if "timed out" in error_msg or "timeout" in error_msg:
                print("\n" + "=" * 80)
                print("⚠️  NETWORK CONNECTIVITY ISSUE DETECTED")
                print("=" * 80)
                print("The VM appears to be unreachable. This can happen if:")
                print("  1. The VM is still booting (cloud-init may be installing packages)")
                print("  2. Network configuration issues during VM initialization")
                print("  3. Firewall or security group blocking SSH access")
                print("\nRECOMMENDED ACTIONS:")
                print("  • Wait 2-3 minutes and try running 'apply' again")
                print("  • Check VM status in Nebius Console (serial logs can show boot issues)")
                print("  • If the issue persists, restart the VM from the console and retry")
                print("  • As a last resort, run: nebius-vpngw destroy -y && nebius-vpngw apply")
                print("=" * 80 + "\n")
            return

        restart_agent = False

        # Always deploy the latest agent package from local build
        print("[SSHPush] Deploying latest nebius-vpngw package...")
        wheel_path = self._build_wheel()
        if wheel_path and wheel_path.exists():
            wheel_version = None
            wheel_parts = wheel_path.name.split("-")
            if len(wheel_parts) >= 2:
                wheel_version = wheel_parts[1]
            try:
                with client.open_sftp() as sftp:
                    remote_wheel = f"/tmp/{wheel_path.name}"
                    sftp.put(str(wheel_path), remote_wheel)
                    print(f"[SSHPush] Uploaded {wheel_path.name}")

                # Install/upgrade the wheel with dependencies
                # Use --break-system-packages on Ubuntu 24.04+ which has PEP 668 restrictions
                # Use --ignore-installed to avoid conflicts with system-managed packages like typing_extensions
                # Use 'python3 -m pip' instead of 'pip3' for Ubuntu 24.04 compatibility
                install_cmd = f"sudo python3 -m pip install --upgrade --ignore-installed --break-system-packages {remote_wheel}"
                stdin, stdout, stderr = client.exec_command(install_cmd, get_pty=True, timeout=120)
                rc = stdout.channel.recv_exit_status()
                out = stdout.read().decode().strip()
                err = stderr.read().decode().strip()
                if rc == 0:
                    restart_agent = True
                    # Reinstall just our package to avoid stale version metadata, without touching deps
                    reinstall_cmd = (
                        f"sudo python3 -m pip install --upgrade --force-reinstall "
                        f"--no-deps --break-system-packages {remote_wheel}"
                    )
                    stdin_re, stdout_re, stderr_re = client.exec_command(
                        reinstall_cmd, get_pty=True, timeout=120
                    )
                    rc_re = stdout_re.channel.recv_exit_status()
                    re_out = stdout_re.read().decode().strip()
                    re_err = stderr_re.read().decode().strip()
                    if rc_re != 0:
                        print("[SSHPush] WARNING: Forced reinstall failed; continuing anyway")
                        if re_out:
                            print(
                                f"[SSHPush] stdout: {re_out[-500:]}"
                                if len(re_out) > 500
                                else f"[SSHPush] stdout: {re_out}"
                            )
                        if re_err:
                            print(
                                f"[SSHPush] stderr: {re_err[-500:]}"
                                if len(re_err) > 500
                                else f"[SSHPush] stderr: {re_err}"
                            )

                    # Verify package actually installed by importing it
                    verify_cmd = (
                        'python3 -c "import importlib.metadata as m, nebius_vpngw; '
                        "print('version=' + m.version('nebius-vpngw')); "
                        "print('path=' + nebius_vpngw.__file__)\""
                    )
                    stdin_check, stdout_check, stderr_check = client.exec_command(
                        verify_cmd, timeout=10
                    )
                    rc_check = stdout_check.channel.recv_exit_status()
                    verify_out = stdout_check.read().decode().strip()
                    verify_err = stderr_check.read().decode().strip()
                    if rc_check == 0 and verify_out:
                        lines = [line.strip() for line in verify_out.splitlines() if line.strip()]
                        version_line = next(
                            (line for line in lines if line.startswith("version=")), ""
                        )
                        path_line = next((line for line in lines if line.startswith("path=")), "")
                        if version_line:
                            installed_version = version_line.split("=", 1)[1]
                            installed_path = path_line.split("=", 1)[1] if path_line else None
                            print(
                                "[SSHPush] Package installed/upgraded successfully: "
                                f"nebius-vpngw {installed_version}"
                            )
                            if wheel_version and installed_version != wheel_version:
                                print(
                                    "[SSHPush] WARNING: Installed version does not match "
                                    f"wheel ({installed_version} != {wheel_version}). "
                                    "A system package may be shadowing the installed wheel."
                                )
                                if installed_path:
                                    print(f"[SSHPush] Installed package path: {installed_path}")
                        else:
                            print("[SSHPush] WARNING: Could not read installed package version")
                    else:
                        print(
                            "[SSHPush] WARNING: pip install succeeded but package import check failed"
                        )
                        if verify_err:
                            print(
                                f"[SSHPush] stderr: {verify_err[-500:]}"
                                if len(verify_err) > 500
                                else f"[SSHPush] stderr: {verify_err}"
                            )
                    # Install/refresh systemd unit - read from package systemd/ directory
                    import nebius_vpngw

                    systemd_dir = Path(nebius_vpngw.__file__).parent / "systemd"
                    service_unit_file = systemd_dir / "nebius-vpngw-agent.service"

                    if service_unit_file.exists():
                        service_unit = service_unit_file.read_text()
                    else:
                        # Fallback to embedded content if file not found
                        service_unit = """[Unit]
Description=Nebius VPNGW Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

                    try:
                        with client.open_sftp() as sftp:
                            with sftp.file("/tmp/nebius-vpngw-agent.service", "w") as f:
                                f.write(service_unit)
                            print("[SSHPush] Staged systemd unit update")

                            # Deploy route fix service and timer
                            # Use installed package location (works both in dev and deployed)
                            import nebius_vpngw

                            systemd_dir = Path(nebius_vpngw.__file__).parent / "systemd"

                            fix_routes_service = systemd_dir / "nebius-vpngw-fix-routes.service"
                            fix_routes_timer = systemd_dir / "nebius-vpngw-fix-routes.timer"

                            if fix_routes_service.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.service", "w") as f:
                                    f.write(fix_routes_service.read_text())
                                print("[SSHPush] Staged route fix service")

                            if fix_routes_timer.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.timer", "w") as f:
                                    f.write(fix_routes_timer.read_text())
                                print("[SSHPush] Staged route fix timer")

                            # Deploy health monitoring service
                            health_monitor_service = systemd_dir / "nebius-vpngw-health-monitor.service"
                            if health_monitor_service.exists():
                                with sftp.file("/tmp/nebius-vpngw-health-monitor.service", "w") as f:
                                    f.write(health_monitor_service.read_text())
                                print("[SSHPush] Staged health monitoring service")

                            firewall_script = systemd_dir / "setup-vpngw-firewall.sh"
                            if firewall_script.exists():
                                with sftp.file("/tmp/setup-vpngw-firewall.sh", "w") as f:
                                    f.write(firewall_script.read_text())
                                print("[SSHPush] Staged firewall setup script")
                    except Exception as e:
                        print(f"[SSHPush] Failed to stage systemd unit: {e}")
                else:
                    print(f"[SSHPush] Package installation failed (rc={rc})")
                    if out:
                        print(
                            f"[SSHPush] stdout: {out[-500:]}"
                            if len(out) > 500
                            else f"[SSHPush] stdout: {out}"
                        )
                    if err:
                        print(
                            f"[SSHPush] stderr: {err[-500:]}"
                            if len(err) > 500
                            else f"[SSHPush] stderr: {err}"
                        )
                    print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
            except Exception as e:
                print(f"[SSHPush] Failed to deploy package: {e}")
                print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
        else:
            print("[SSHPush] WARNING: Could not build wheel, skipping package deployment")

        # Upload to /tmp then move with sudo
        tmp_path = f"/tmp/nebius-config-{inst_cfg.instance_index}.yaml"
        try:
            with client.open_sftp() as sftp, sftp.file(tmp_path, "w") as f:
                f.write(inst_cfg.config_yaml)
            print(f"[SSHPush] Uploaded temp config to {tmp_path}")
        except Exception as e:
            print(f"[SSHPush] SFTP upload failed: {e}")
            client.close()
            return

        agent_cmd = (
            "sudo systemctl restart nebius-vpngw-agent"
            if restart_agent
            else "sudo systemctl is-active --quiet nebius-vpngw-agent && sudo systemctl reload nebius-vpngw-agent || sudo systemctl start nebius-vpngw-agent"
        )

        # Move into place and trigger reload
        cmds = [
            "sudo mkdir -p /etc/nebius-vpngw",
            f"sudo mv {tmp_path} /etc/nebius-vpngw/config-resolved.yaml",
            "sudo chown root:root /etc/nebius-vpngw/config-resolved.yaml",
            "sudo chmod 0644 /etc/nebius-vpngw/config-resolved.yaml",
            # Install route fix service and timer if staged
            "if [ -f /tmp/nebius-vpngw-fix-routes.service ]; then sudo mv /tmp/nebius-vpngw-fix-routes.service /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
            "if [ -f /tmp/nebius-vpngw-fix-routes.timer ]; then sudo mv /tmp/nebius-vpngw-fix-routes.timer /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
            # Install health monitoring service if staged
            "if [ -f /tmp/nebius-vpngw-health-monitor.service ]; then sudo mv /tmp/nebius-vpngw-health-monitor.service /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
            "if [ -f /tmp/setup-vpngw-firewall.sh ]; then sudo mv /tmp/setup-vpngw-firewall.sh /usr/local/bin/setup-vpngw-firewall.sh; fi",
            "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo chmod 0755 /usr/local/bin/setup-vpngw-firewall.sh; fi",
            # Refresh systemd unit if staged
            "if [ -f /tmp/nebius-vpngw-agent.service ]; then sudo mv /tmp/nebius-vpngw-agent.service /etc/systemd/system/nebius-vpngw-agent.service; fi",
            "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-agent.service",
            "sudo systemctl daemon-reload",
            # Enable and start route fix timer (only if service file exists)
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo systemctl enable --now nebius-vpngw-fix-routes.timer; fi",
            # Enable and start health monitoring service (only if service file exists)
            "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo systemctl enable --now nebius-vpngw-health-monitor.service; fi",
            # Run route fix immediately before starting agent (non-fatal if unavailable)
            'if python3 -c "import nebius_vpngw" >/dev/null 2>&1; then sudo /usr/bin/python3 -m nebius_vpngw.agent.fix_routes > /var/log/vpngw-fix-routes.log 2>&1 || true; fi',
            # Apply firewall rules (including MSS clamp and ICMP allowances)
            "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo /usr/local/bin/setup-vpngw-firewall.sh > /var/log/vpngw-firewall-setup.log 2>&1 || true; fi",
            # Start or reload agent
            agent_cmd,
        ]
        had_failures = False
        for cmd in cmds:
            try:
                stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=20)
                rc = stdout.channel.recv_exit_status()
                if rc != 0:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] Command failed (rc={rc}): {cmd}\n{err}")
                    had_failures = True
                else:
                    # Suppress noisy per-command logs on success
                    pass
            except Exception as e:
                print(f"[SSHPush] Exec failed for: {cmd} -> {e}")
                had_failures = True

        if not had_failures:
            if restart_agent:
                print("[SSHPush] Applied config, systemd unit, and restarted agent")
            else:
                print("[SSHPush] Applied config, systemd unit, and reloaded agent")

        # Verify routing table health after route fix ran
        try:

            def _check_routing_health() -> tuple[str, str]:
                stdin, stdout, stderr = client.exec_command(
                    "ip rule list | grep -q 'lookup 220' && echo 'EXISTS' || echo 'OK'",
                    timeout=10,
                )
                table220 = stdout.read().decode().strip()

                stdin, stdout, stderr = client.exec_command(
                    "ip route show 169.254.0.0/16 2>/dev/null | grep -q eth0 && echo 'EXISTS' || echo 'OK'",
                    timeout=10,
                )
                apipa = stdout.read().decode().strip()
                return table220, apipa

            table220_status, apipa_status = _check_routing_health()
            if table220_status != "OK" or apipa_status != "OK":
                import time

                time.sleep(5)
                table220_status, apipa_status = _check_routing_health()

            if table220_status == "OK" and apipa_status == "OK":
                print("[SSHPush] ✓ Routing table clean (Table 220 and broad APIPA removed)")
            else:
                if table220_status == "EXISTS":
                    print(
                        "[SSHPush] ⚠ Table 220 policy route still exists (may impact VPN routing)"
                    )
                if apipa_status == "EXISTS":
                    print(
                        "[SSHPush] ⚠ Broad APIPA route (169.254.0.0/16) still exists (should be removed for XFRM tunnels)"
                    )
        except Exception:
            # Non-critical check, don't fail deployment
            pass

        # Ensure FRR is installed (cloud-init can fail if repo/version is unavailable)
        try:
            stdin, stdout, stderr = client.exec_command(
                "dpkg -l frr 2>/dev/null | grep -q '^ii'", timeout=10
            )
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                print("[SSHPush] ⚠ FRR package missing; attempting install...")
                install_cmd = (
                    "sudo bash -lc '"
                    "set -e;"
                    "if ! dpkg -l frr 2>/dev/null | grep -q \"^ii\"; then "
                    "command -v curl >/dev/null 2>&1 || (apt-get update && apt-get install -y curl); "
                    "if [ ! -f /etc/apt/sources.list.d/frr.list ]; then "
                    "curl -s https://deb.frrouting.org/frr/keys.asc | tee /usr/share/keyrings/frrouting.asc > /dev/null; "
                    "UBUNTU_CODENAME=$(lsb_release -cs); "
                    "echo \"deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr $UBUNTU_CODENAME frr-stable\" > /etc/apt/sources.list.d/frr.list; "
                    "fi; "
                    "apt-get update; "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y frr frr-pythontools; "
                    "fi'"
                )
                stdin, stdout, stderr = client.exec_command(
                    install_cmd, timeout=300, get_pty=True
                )
                install_rc = stdout.channel.recv_exit_status()
                if install_rc == 0:
                    print("[SSHPush] ✓ FRR installed")
                else:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] ✗ FRR install failed: {err}")
        except Exception as e:
            print(f"[SSHPush] ⚠ FRR install check failed: {e}")

        # Ensure swanctl is installed (required for if_id_in/out and VICI config loading)
        try:
            stdin, stdout, stderr = client.exec_command(
                "dpkg -l strongswan-swanctl 2>/dev/null | grep -q '^ii'", timeout=10
            )
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                print("[SSHPush] ⚠ strongswan-swanctl missing; attempting install...")
                install_cmd = (
                    "sudo bash -lc '"
                    "set -e;"
                    "apt-get update; "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y strongswan-swanctl'"
                )
                stdin, stdout, stderr = client.exec_command(
                    install_cmd, timeout=300, get_pty=True
                )
                install_rc = stdout.channel.recv_exit_status()
                if install_rc == 0:
                    print("[SSHPush] ✓ strongswan-swanctl installed")
                else:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] ✗ strongswan-swanctl install failed: {err}")
        except Exception as e:
            print(f"[SSHPush] ⚠ strongswan-swanctl install check failed: {e}")

        # Verify service is actually running
        try:
            print("[SSHPush] Verifying service status...")
            stdin, stdout, stderr = client.exec_command(
                "sudo systemctl is-active nebius-vpngw-agent", timeout=10
            )
            rc = stdout.channel.recv_exit_status()
            status = stdout.read().decode().strip()

            if rc == 0 and status == "active":
                print("[SSHPush] ✓ nebius-vpngw-agent is running")
            else:
                print(f"[SSHPush] ✗ nebius-vpngw-agent is NOT running (status: {status})")
                # Get detailed status for troubleshooting
                stdin, stdout, stderr = client.exec_command(
                    "sudo systemctl status nebius-vpngw-agent --no-pager -l", timeout=10
                )
                detailed_status = stdout.read().decode()
                print(f"[SSHPush] Service status:\n{detailed_status}")

            # Verify strongSwan (account for different service names) and FRR
            strongswan_checks = [
                ("strongswan-starter", "sudo systemctl is-active strongswan-starter"),
                ("strongswan-swanctl", "sudo systemctl is-active strongswan-swanctl"),
                (
                    "charon",
                    "pgrep -x charon >/dev/null && echo active || echo inactive",
                ),
            ]
            strongswan_statuses = []
            strongswan_ok = False
            for name, cmd in strongswan_checks:
                stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                rc = stdout.channel.recv_exit_status()
                svc_status = stdout.read().decode().strip()
                strongswan_statuses.append(f"{name}={svc_status or rc}")
                if rc == 0 and svc_status == "active":
                    print(f"[SSHPush] ✓ strongSwan is running ({name})")
                    strongswan_ok = True
                    break
            if not strongswan_ok:
                joined = ", ".join(strongswan_statuses)
                print(f"[SSHPush] ✗ strongSwan appears inactive (checked: {joined})")

            # FRR check - wait up to 15 seconds for FRR to start
            frr_active = False
            for attempt in range(3):  # 3 attempts, 5 seconds apart
                stdin, stdout, stderr = client.exec_command(
                    "sudo systemctl is-active frr", timeout=10
                )
                rc = stdout.channel.recv_exit_status()
                svc_status = stdout.read().decode().strip()
                if rc == 0 and svc_status == "active":
                    print("[SSHPush] ✓ frr is running")
                    frr_active = True
                    break
                elif attempt < 2:  # Don't sleep on last attempt
                    import time

                    time.sleep(5)

            if not frr_active:
                print(f"[SSHPush] ✗ frr is NOT running (status: {svc_status})")

            # Check BGP session status via FRR instead of TCP port probing
            # (TCP probes fail when BGP sessions are already established)
            try:
                defaults_mode = (
                    (local_cfg.get("defaults", {}) or {}).get("routing", {}) or {}
                ).get("mode", "bgp")

                # Collect BGP peers for this instance (active and passive tunnels)
                bgp_peers = []
                for conn in local_cfg.get("connections") or []:
                    routing_mode = conn.get("routing_mode") or defaults_mode
                    if routing_mode != "bgp":
                        continue
                    for tun in conn.get("tunnels") or []:
                        if int(tun.get("gateway_instance_index", 0)) != inst_cfg.instance_index:
                            continue
                        ha_role = tun.get("ha_role", "active")
                        if ha_role == "disable":
                            continue  # Skip only explicitly disabled tunnels
                        r_ip = tun.get("inner_remote_ip")
                        if r_ip:
                            bgp_peers.append(r_ip)

                if bgp_peers:
                    # Wait for IPsec tunnels to establish before testing connectivity
                    import json
                    import time

                    print("[SSHPush] Waiting for IPsec tunnels to establish...")
                    time.sleep(10)

                    print(f"[SSHPush] Verifying tunnel connectivity to {len(bgp_peers)} peer(s)...")

                    # Step 1: Test ping connectivity to BGP peers
                    all_peers_reachable = True
                    for peer_ip in bgp_peers:
                        cmd = f"ping -c 2 -W 2 {peer_ip} >/dev/null 2>&1 && echo OK || echo FAIL"
                        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                        result = stdout.read().decode().strip()
                        if result == "OK":
                            print(f"[SSHPush] ✓ Tunnel connectivity OK: {peer_ip} is reachable")
                        else:
                            print(
                                f"[SSHPush] ✗ Tunnel connectivity FAILED: {peer_ip} is NOT reachable"
                            )
                            all_peers_reachable = False

                    if not all_peers_reachable:
                        print(
                            "[SSHPush] WARNING: Some peers are not reachable. BGP may not establish."
                        )

                    # Step 2: Wait for BGP sessions to establish (up to 60 seconds)
                    print("[SSHPush] Waiting for BGP sessions to establish...")
                    max_wait_time = 60
                    start_time = time.time()
                    all_established = False
                    last_states = {}

                    while (time.time() - start_time) < max_wait_time:
                        cmd = "sudo vtysh -c 'show bgp summary json' 2>/dev/null || echo '{}'"
                        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                        output = stdout.read().decode().strip()

                        try:
                            bgp_summary = json.loads(output) if output != "{}" else {}
                            ipv4_peers = bgp_summary.get("ipv4Unicast", {}).get("peers", {})

                            established_count = 0
                            current_states = {}

                            for peer_ip in bgp_peers:
                                peer_info = ipv4_peers.get(peer_ip, {})
                                state = peer_info.get("state", "Unknown")
                                current_states[peer_ip] = state

                                if state == "Established":
                                    established_count += 1

                            # Print state changes
                            for peer_ip, state in current_states.items():
                                if peer_ip not in last_states or last_states[peer_ip] != state:
                                    elapsed = int(time.time() - start_time)
                                    if state == "Established":
                                        print(
                                            f"[SSHPush] ✓ BGP session with {peer_ip} is Established (after {elapsed}s)"
                                        )
                                    elif state != "Unknown":
                                        print(
                                            f"[SSHPush]   BGP session with {peer_ip}: {state} (waiting...)"
                                        )

                            last_states = current_states

                            if established_count == len(bgp_peers):
                                all_established = True
                                break

                            # Wait 3 seconds before checking again
                            time.sleep(3)

                        except (json.JSONDecodeError, Exception):
                            # FRR might not be fully started yet
                            time.sleep(3)
                            continue

                    # Final status report
                    if all_established:
                        elapsed = int(time.time() - start_time)
                        print(
                            f"[SSHPush] ✓ All BGP sessions established successfully (took {elapsed}s)"
                        )
                    else:
                        elapsed = int(time.time() - start_time)
                        print(f"[SSHPush] ⚠ BGP sessions not yet established after {elapsed}s")
                        print(
                            f"[SSHPush]   Current states: {', '.join([f'{ip}={state}' for ip, state in last_states.items()])}"
                        )
                        print(
                            "[SSHPush]   BGP sessions may take additional time to establish. Check with: nebius-vpngw status"
                        )
            except Exception:
                # BGP check is informational only, don't fail deployment
                pass
        except Exception as e:
            print(f"[SSHPush] Failed to verify service status: {e}")

        try:
            client.close()
        except Exception:
            pass  # Ignore Paramiko cleanup warnings
