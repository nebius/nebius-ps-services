from __future__ import annotations

import typing as t
from pathlib import Path

from ..config_loader import InstanceResolvedConfig


class SSHPush:
    """Push per-VM config and trigger agent reload via SSH.

    Replace placeholders with Paramiko/AsyncSSH-based implementation.
    """

    def push_config_and_reload(self, ssh_target: str, inst_cfg: InstanceResolvedConfig) -> None:
        # TODO: Implement SSH/SCP. For now, write to local ./out for inspection.
        out_dir = Path("./out")
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"config-resolved-{inst_cfg.instance_index}.yaml"
        out_path.write_text(inst_cfg.config_yaml, encoding="utf-8")
        print(f"[SSHPush] Wrote {out_path} (simulate SCP to {ssh_target})")
        print(f"[SSHPush] Simulate 'systemctl reload nebius-vpngw-agent' on {ssh_target}")
