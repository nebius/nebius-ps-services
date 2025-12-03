from __future__ import annotations

import os
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

    def _ensure_paramiko(self):
        if self._paramiko is None:
            import paramiko  # type: ignore

            self._paramiko = paramiko
        return self._paramiko

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
            # Prefer systemd reload; fallback to HUP
            "sudo systemctl reload nebius-vpngw-agent || sudo kill -HUP $(pidof nebius-vpngw-agent) || true",
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

        client.close()
