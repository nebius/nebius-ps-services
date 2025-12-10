from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

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

    def _build_wheel(self) -> Optional[Path]:
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
                    print(f"[SSHPush] Removing {len(old_wheels)} old wheel(s) to ensure fresh build...")
                    for wheel in old_wheels:
                        wheel.unlink()
            
            built = False
            # Prefer poetry build when available (faster, uses poetry.lock if present)
            poetry = shutil.which("poetry")
            if poetry:
                print("[SSHPush] Building wheel via poetry...")
                try:
                    result = subprocess.run(
                        [poetry, "build", "-f", "wheel"],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    if result.returncode == 0:
                        built = True
                    else:
                        print(f"[SSHPush] poetry build failed: {result.stderr}")
                except Exception as e:
                    print(f"[SSHPush] poetry build error: {e}")
            if not built:
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
                    print("[SSHPush] WARNING: 'build' module not found. Install with: pip install build")
                except Exception as e:
                    print(f"[SSHPush] Wheel build error: {e}")

        # Reuse newest existing wheel (works with poetry build or python -m build)
        if dist_dir.exists():
            wheels = sorted(dist_dir.glob("nebius_vpngw-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
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

    def push_config_and_reload(self, ssh_target: str, inst_cfg: InstanceResolvedConfig, local_cfg: dict) -> None:
        if not ssh_target:
            print(f"[SSHPush] No SSH target for instance {inst_cfg.instance_index}; skipping")
            return

        paramiko = self._ensure_paramiko()
        gg = (local_cfg.get("gateway_group") or {})
        vm_spec = (gg.get("vm_spec") or {})
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        key_path: Optional[str] = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_file = Path(key_path).expanduser() if key_path else None

        print(f"[SSHPush] Connecting to {ssh_target} as {username} ...")
        client = paramiko.SSHClient()
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
                print("\n" + "="*80)
                print("⚠️  NETWORK CONNECTIVITY ISSUE DETECTED")
                print("="*80)
                print("The VM appears to be unreachable. This can happen if:")
                print("  1. The VM is still booting (cloud-init may be installing packages)")
                print("  2. Network configuration issues during VM initialization")
                print("  3. Firewall or security group blocking SSH access")
                print("\nRECOMMENDED ACTIONS:")
                print("  • Wait 2-3 minutes and try running 'apply' again")
                print("  • Check VM status in Nebius Console (serial logs can show boot issues)")
                print("  • If the issue persists, restart the VM from the console and retry")
                print("  • As a last resort, run: nebius-vpngw destroy -y && nebius-vpngw apply")
                print("="*80 + "\n")
            return

        # Always deploy the latest agent package from local build
        print("[SSHPush] Deploying latest nebius-vpngw package...")
        wheel_path = self._build_wheel()
        if wheel_path and wheel_path.exists():
            try:
                with client.open_sftp() as sftp:
                    remote_wheel = f"/tmp/{wheel_path.name}"
                    sftp.put(str(wheel_path), remote_wheel)
                    print(f"[SSHPush] Uploaded {wheel_path.name}")

                # Install/upgrade the wheel
                # Use --break-system-packages on Ubuntu 24.04+ which has PEP 668 restrictions
                # Use --ignore-installed to avoid conflicts with system packages like typing_extensions
                install_cmd = (
                    f"sudo pip3 install --upgrade --no-deps --break-system-packages --ignore-installed {remote_wheel} && "
                    f"sudo pip3 install --break-system-packages --ignore-installed {remote_wheel}"
                )
                stdin, stdout, stderr = client.exec_command(install_cmd, get_pty=True, timeout=60)
                rc = stdout.channel.recv_exit_status()
                if rc == 0:
                    # Verify package actually installed by checking pip list
                    stdin_check, stdout_check, stderr_check = client.exec_command("pip3 list | grep nebius-vpngw", timeout=10)
                    pkg_check = stdout_check.read().decode().strip()
                    if "nebius-vpngw" in pkg_check:
                        print(f"[SSHPush] Package installed/upgraded successfully: {pkg_check}")
                    else:
                        print("[SSHPush] WARNING: pip install succeeded but package not found in pip list")
                    # Install/refresh systemd unit so ExecStart points to python -m entrypoint
                    service_unit = """[Unit]
Description=Nebius VPNGW Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
                    try:
                        with client.open_sftp() as sftp:
                            with sftp.file("/tmp/nebius-vpngw-agent.service", "w") as f:
                                f.write(service_unit)
                            print("[SSHPush] Staged systemd unit update")
                            
                            # Deploy route fix script, service, and timer
                            # Use installed package location (works both in dev and deployed)
                            import nebius_vpngw
                            systemd_dir = Path(nebius_vpngw.__file__).parent / "systemd"
                            
                            # Deploy ipsec-vti.sh updown script
                            ipsec_vti_script = systemd_dir / "ipsec-vti.sh"
                            if ipsec_vti_script.exists():
                                with sftp.file("/tmp/ipsec-vti.sh", "w") as f:
                                    f.write(ipsec_vti_script.read_text())
                                print("[SSHPush] Staged ipsec-vti.sh updown script")
                            fix_routes_script = systemd_dir / "fix-routes.sh"
                            fix_routes_service = systemd_dir / "nebius-vpngw-fix-routes.service"
                            fix_routes_timer = systemd_dir / "nebius-vpngw-fix-routes.timer"
                            
                            if fix_routes_script.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.sh", "w") as f:
                                    f.write(fix_routes_script.read_text())
                                print("[SSHPush] Staged route fix script")
                            
                            if fix_routes_service.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.service", "w") as f:
                                    f.write(fix_routes_service.read_text())
                                print("[SSHPush] Staged route fix service")
                            
                            if fix_routes_timer.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.timer", "w") as f:
                                    f.write(fix_routes_timer.read_text())
                                print("[SSHPush] Staged route fix timer")
                    except Exception as e:
                        print(f"[SSHPush] Failed to stage systemd unit: {e}")
                else:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] Package installation failed: {err}")
                    print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
            except Exception as e:
                print(f"[SSHPush] Failed to deploy package: {e}")
                print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
        else:
            print("[SSHPush] WARNING: Could not build wheel, skipping package deployment")

        # Upload to /tmp then move with sudo
        tmp_path = f"/tmp/nebius-config-{inst_cfg.instance_index}.yaml"
        try:
            with client.open_sftp() as sftp:
                with sftp.file(tmp_path, "w") as f:
                    f.write(inst_cfg.config_yaml)
            print(f"[SSHPush] Uploaded temp config to {tmp_path}")
        except Exception as e:
            print(f"[SSHPush] SFTP upload failed: {e}")
            client.close()
            return

        # Move into place and trigger reload
        cmds = [
            "sudo mkdir -p /etc/nebius-vpngw",
            f"sudo mv {tmp_path} /etc/nebius-vpngw/config-resolved.yaml",
            "sudo chown root:root /etc/nebius-vpngw/config-resolved.yaml",
            "sudo chmod 0644 /etc/nebius-vpngw/config-resolved.yaml",
            # Install ipsec-vti.sh updown script if staged
            "sudo mkdir -p /var/lib/strongswan",
            "if [ -f /tmp/ipsec-vti.sh ]; then sudo mv /tmp/ipsec-vti.sh /var/lib/strongswan/ipsec-vti.sh; fi",
            "if [ -f /var/lib/strongswan/ipsec-vti.sh ]; then sudo chmod 0755 /var/lib/strongswan/ipsec-vti.sh; fi",
            # Install route fix script, service, and timer if staged
            "if [ -f /tmp/nebius-vpngw-fix-routes.sh ]; then sudo mv /tmp/nebius-vpngw-fix-routes.sh /usr/local/bin/nebius-vpngw-fix-routes.sh; fi",
            "if [ -f /usr/local/bin/nebius-vpngw-fix-routes.sh ]; then sudo chmod 0755 /usr/local/bin/nebius-vpngw-fix-routes.sh; fi",
            "if [ -f /tmp/nebius-vpngw-fix-routes.service ]; then sudo mv /tmp/nebius-vpngw-fix-routes.service /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
            "if [ -f /tmp/nebius-vpngw-fix-routes.timer ]; then sudo mv /tmp/nebius-vpngw-fix-routes.timer /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
            "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.service",
            "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.timer",
            # Refresh systemd unit if staged
            "if [ -f /tmp/nebius-vpngw-agent.service ]; then sudo mv /tmp/nebius-vpngw-agent.service /etc/systemd/system/nebius-vpngw-agent.service; fi",
            "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-agent.service",
            "sudo systemctl daemon-reload",
            # Enable and start route fix timer
            "sudo systemctl enable --now nebius-vpngw-fix-routes.timer",
            # Start service if inactive, reload if active
            "sudo systemctl is-active --quiet nebius-vpngw-agent && sudo systemctl reload nebius-vpngw-agent || sudo systemctl start nebius-vpngw-agent",
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
            print("[SSHPush] Applied config, systemd unit, and restarted agent")

        # Verify service is actually running
        try:
            print("[SSHPush] Verifying service status...")
            stdin, stdout, stderr = client.exec_command("sudo systemctl is-active nebius-vpngw-agent", timeout=10)
            rc = stdout.channel.recv_exit_status()
            status = stdout.read().decode().strip()
            
            if rc == 0 and status == "active":
                print("[SSHPush] ✓ nebius-vpngw-agent is running")
            else:
                print(f"[SSHPush] ✗ nebius-vpngw-agent is NOT running (status: {status})")
                # Get detailed status for troubleshooting
                stdin, stdout, stderr = client.exec_command("sudo systemctl status nebius-vpngw-agent --no-pager -l", timeout=10)
                detailed_status = stdout.read().decode()
                print(f"[SSHPush] Service status:\n{detailed_status}")

            # Verify strongSwan (account for different service names) and FRR
            strongswan_checks = [
                ("strongswan-starter", "sudo systemctl is-active strongswan-starter"),
                ("strongswan-swanctl", "sudo systemctl is-active strongswan-swanctl"),
                ("charon", "pgrep -x charon >/dev/null && echo active || echo inactive"),
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
                stdin, stdout, stderr = client.exec_command("sudo systemctl is-active frr", timeout=10)
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

            # Quick BGP port probe to detect blocked TCP/179 on peer side
            try:
                defaults_mode = (
                    (local_cfg.get("defaults", {}) or {}).get("routing", {}) or {}
                ).get("mode", "bgp")
                tunnels_to_probe = []
                for conn in (local_cfg.get("connections") or []):
                    routing_mode = conn.get("routing_mode") or defaults_mode
                    if routing_mode != "bgp":
                        continue
                    for tun in (conn.get("tunnels") or []):
                        if int(tun.get("gateway_instance_index", 0)) != inst_cfg.instance_index:
                            continue
                        if tun.get("ha_role", "active") != "active":
                            continue
                        r_ip = tun.get("inner_remote_ip")
                        l_ip = tun.get("inner_local_ip")
                        if r_ip:
                            tunnels_to_probe.append((l_ip, r_ip))

                for l_ip, r_ip in tunnels_to_probe:
                    cmd = (
                        f"if command -v nc >/dev/null; then "
                        f"timeout 3 nc -z -w2 {r_ip} 179; "
                        f"elif [ -f /bin/bash ]; then "
                        f"timeout 3 bash -lc 'echo > /dev/tcp/{r_ip}/179'; "
                        f"else exit 1; fi"
                    )
                    stdin, stdout, stderr = client.exec_command(cmd, timeout=6)
                    rc = stdout.channel.recv_exit_status()
                    if rc == 0:
                        print(f"[SSHPush] ✓ BGP port 179 reachable on peer {r_ip}")
                    else:
                        print(f"[SSHPush] WARNING: BGP port 179 not reachable on peer {r_ip} (source {l_ip or 'auto'}). Check peer firewall for TCP/179.")
            except Exception as e:
                print(f"[SSHPush] WARNING: BGP port probe failed: {e}")
        except Exception as e:
            print(f"[SSHPush] Failed to verify service status: {e}")

        try:
            client.close()
        except Exception:
            pass  # Ignore Paramiko cleanup warnings
