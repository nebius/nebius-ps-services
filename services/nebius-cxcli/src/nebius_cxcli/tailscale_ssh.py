"""Strict SSH transport and render-bound guest assets for Tailscale gateways."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .nat_gateway_ssh import ssh_identity_options
from .ssh_host_enrollment import _binding, prepare_ssh_host_trust
from .ssh_trust import ssh_host_trust_options

MAX_ASSET = 128 * 1024


def freeze_assets(sources: Any) -> dict[str, Any]:
    from .runtime_introspection import _module_inspection_path

    assets = {}
    for source in sources:
        if source.get("component_id") != "tailscale-gw":
            continue
        directory, error = _module_inspection_path(str(source["source"]))
        if not directory or error:
            raise RuntimeError("Cannot resolve Tailscale module guest helper")
        path = Path(directory) / "tailscale_guest.py"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ASSET:
            raise RuntimeError("Tailscale guest asset is missing or unsafe")
        body = path.read_text()
        compile(body, "tailscale_guest.py", "exec")
        assets[source["instance_id"]] = {
            "source": source["source"],
            "module_name": source["module_name"],
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "content": body,
        }
    return assets


def asset(manifest: Any, instance: str) -> dict[str, str]:
    value = manifest.get("render", {}).get("tailscale_guest", {}).get(instance)
    sources = [
        s
        for s in manifest.get("render", {}).get("module_sources", [])
        if s.get("component_id") == "tailscale-gw" and s.get("instance_id") == instance
    ]
    if not isinstance(value, dict) or len(sources) != 1:
        raise RuntimeError("Tailscale guest asset missing; render the configuration")
    body = value.get("content", "")
    if (
        not isinstance(body, str)
        or len(body.encode()) > MAX_ASSET
        or hashlib.sha256(body.encode()).hexdigest() != value.get("sha256")
        or any(value.get(k) != sources[0].get(k) for k in ("source", "module_name"))
    ):
        raise RuntimeError("Tailscale guest asset binding is invalid")
    return value


class Guest:
    def __init__(
        self,
        sdk: Any,
        paths: Any,
        output: dict[str, Any],
        inputs: dict[str, Any],
        private_key: Path | None,
    ) -> None:
        self.output, self.inputs, self.private_key = output, inputs, private_key
        self.sdk = sdk
        self.binding = _binding(sdk, output)
        self.known_hosts = prepare_ssh_host_trust(sdk, output, paths, None)

    def execute(self, command: str, value: Any = None, *, timeout: int = 360) -> dict[str, Any]:
        if _binding(self.sdk, self.output) != self.binding:
            raise RuntimeError("Gateway Compute identity changed before SSH")
        user = self.inputs["ssh_user_name"]
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            raise ValueError("Invalid management SSH user")
        address = str(ipaddress.IPv4Interface(self.output["public_ip"]).ip)
        with ssh_identity_options(self.inputs["ssh_public_key"], self.private_key) as identity:
            args = [
                "ssh",
                *identity,
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                *ssh_host_trust_options(self.known_hosts, address),
                f"{user}@{address}",
                command,
            ]
            try:
                result = subprocess.run(
                    args,
                    input=json.dumps(value or {}),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError(
                    "Tailscale SSH outcome unknown; inspect status before retrying"
                ) from None
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise RuntimeError(
                "Tailscale trusted SSH operation failed; inspect management reachability and guest state"
            )
        try:
            report = json.loads(result.stdout or "{}")
            if not isinstance(report, dict):
                raise ValueError
            return report
        except ValueError:
            raise RuntimeError("Tailscale guest returned an invalid report") from None

    def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        return self.execute(
            "sudo -n /usr/local/sbin/cxcli-tailscale", {"operation": operation, **kwargs}
        )

    def install(self, item: dict[str, str]) -> None:
        # Fixed code, not configuration-selected shell. Auth material never shares this request.
        installer = """import hashlib,json,os,sys,tempfile
from pathlib import Path
r=json.load(sys.stdin); b=r['content'].encode(); p=Path('/usr/local/sbin/cxcli-tailscale')
if len(b)>131072 or hashlib.sha256(b).hexdigest()!=r['sha256'] or p.is_symlink(): raise RuntimeError('Invalid helper')
compile(b,'tailscale_guest.py','exec')
if not p.exists() or p.read_bytes()!=b:
 f,n=tempfile.mkstemp(dir=p.parent,prefix='.tailscale-'); os.fchmod(f,0o700)
 with os.fdopen(f,'wb') as s: s.write(b); s.flush(); os.fsync(s.fileno())
 os.replace(n,p)
print('{}')
"""
        self.execute(
            "timeout 300 cloud-init status --wait >/dev/null && sudo -n python3 -c "
            + shlex.quote(installer),
            item,
        )
