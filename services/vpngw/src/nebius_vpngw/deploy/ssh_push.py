from __future__ import annotations

import os
import subprocess
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

        # Find project root (where pyproject.toml is)
        current = Path(__file__).resolve()
        project_root = None
        for parent in current.parents:
            if (parent / "pyproject.toml").exists():
                project_root = parent
                break

        if not project_root:
            print("[SSHPush] WARNING: Could not find project root with pyproject.toml")
            return None

        dist_dir = project_root / "dist"
        
        # Check if wheel already exists
        if dist_dir.exists():
            wheels = list(dist_dir.glob("nebius_vpngw-*.whl"))
            if wheels:
                self._wheel_path = wheels[0]
                print(f"[SSHPush] Using existing wheel: {self._wheel_path.name}")
                return self._wheel_path

        # Build the wheel
        print("[SSHPush] Building nebius-vpngw wheel package...")
        try:
            result = subprocess.run(
                ["python3", "-m", "build", "--wheel"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                print(f"[SSHPush] Wheel build failed: {result.stderr}")
                return None

            # Find the built wheel
            wheels = list(dist_dir.glob("nebius_vpngw-*.whl"))
            if wheels:
                self._wheel_path = wheels[0]
                print(f"[SSHPush] Built wheel: {self._wheel_path.name}")
                return self._wheel_path
            else:
                print("[SSHPush] Wheel build completed but file not found")
                return None

        except FileNotFoundError:
            print("[SSHPush] WARNING: 'build' module not found. Install with: pip install build")
            return None
        except Exception as e:
            print(f"[SSHPush] Wheel build error: {e}")
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
            print(f"[SSHPush] SSH connect failed to {ssh_target}: {e}")
            return

        # Always deploy the latest agent package from local build
        print("[SSHPush] Deploying latest nebius-vpngw package...")
        wheel_path = self._build_wheel()
        if wheel_path and wheel_path.exists():
            try:
                sftp = client.open_sftp()
                remote_wheel = f"/tmp/{wheel_path.name}"
                sftp.put(str(wheel_path), remote_wheel)
                sftp.close()
                print(f"[SSHPush] Uploaded {wheel_path.name}")

                # Install/upgrade the wheel
                install_cmd = f"sudo pip3 install --force-reinstall --break-system-packages {remote_wheel} || sudo pip3 install --force-reinstall {remote_wheel}"
                stdin, stdout, stderr = client.exec_command(install_cmd, get_pty=True, timeout=60)
                rc = stdout.channel.recv_exit_status()
                if rc == 0:
                    print("[SSHPush] Package installed/upgraded successfully")
                    # Create Python wrapper script for the agent (entry points may not be set up)
                    wrapper_script = (
                        "#!/usr/bin/python3\n"
                        "import sys\n"
                        "from nebius_vpngw.agent.main import main\n"
                        "if __name__ == \"__main__\":\n"
                        "    sys.exit(main())\n"
                    )
                    try:
                        sftp = client.open_sftp()
                        with sftp.file("/tmp/nebius-vpngw-agent-wrapper", "w") as f:
                            f.write(wrapper_script)
                        sftp.close()
                        # Install wrapper script
                        wrapper_cmds = [
                            "sudo mv /tmp/nebius-vpngw-agent-wrapper /usr/bin/nebius-vpngw-agent",
                            "sudo chmod +x /usr/bin/nebius-vpngw-agent",
                        ]
                        for wcmd in wrapper_cmds:
                            client.exec_command(wcmd, timeout=10)
                        print("[SSHPush] Agent wrapper script installed")
                    except Exception as e:
                        print(f"[SSHPush] Failed to create wrapper script: {e}")
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
            sftp = client.open_sftp()
            with sftp.file(tmp_path, "w") as f:
                f.write(inst_cfg.config_yaml)
            sftp.close()
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
            # Start service if inactive, reload if active
            "sudo systemctl is-active --quiet nebius-vpngw-agent && sudo systemctl reload nebius-vpngw-agent || sudo systemctl start nebius-vpngw-agent",
        ]
        for cmd in cmds:
            try:
                stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=20)
                rc = stdout.channel.recv_exit_status()
                if rc != 0:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] Command failed (rc={rc}): {cmd}\n{err}")
                else:
                    print(f"[SSHPush] OK: {cmd}")
            except Exception as e:
                print(f"[SSHPush] Exec failed for: {cmd} -> {e}")

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

            # FRR check
            stdin, stdout, stderr = client.exec_command("sudo systemctl is-active frr", timeout=10)
            rc = stdout.channel.recv_exit_status()
            svc_status = stdout.read().decode().strip()
            if rc == 0 and svc_status == "active":
                print("[SSHPush] ✓ frr is running")
            else:
                print(f"[SSHPush] ✗ frr is NOT running (status: {svc_status})")
        except Exception as e:
            print(f"[SSHPush] Failed to verify service status: {e}")

        try:
            client.close()
        except Exception:
            pass  # Ignore Paramiko cleanup warnings
