"""Frozen WireGuard guest assets and authenticated, resumable in-place updates."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id
from .runtime_introspection import _module_inspection_path
from .wireguard_access import temporary_ssh_access
from .wireguard_clients import _ssh_command_parts

MAX_ASSET = 128 * 1024


def freeze_network_assets(config: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]) -> dict:
    rows = {component_instance_id(r): r for r in config.get("infra", {}).get("components", [])}
    result = {}
    for source in sources:
        if source.get("component_id") != "wireguard-gw":
            continue
        directory, issue = _module_inspection_path(str(source["source"]))
        if not directory or issue:
            raise RuntimeError("Cannot resolve the selected WireGuard networking module")
        path = Path(directory) / "wireguard_network.py"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ASSET:
            raise RuntimeError(
                "Selected WireGuard module lacks its bounded canonical networking asset"
            )
        body = path.read_text()
        compile(body, "wireguard_network.py", "exec")
        instance = str(source["instance_id"])
        mtu = rows[instance].get("inputs", {}).get("wireguard_mtu")
        if mtu is not None and (type(mtu) is not int or not 1280 <= mtu <= 1420):
            raise ValueError("wireguard_mtu must be an integer from 1280 through 1420")
        result[instance] = {
            "schema": 1,
            "source": source["source"],
            "module_name": source["module_name"],
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "content": body,
            "wireguard_mtu": mtu,
        }
    return result


def network_asset(manifest: Mapping[str, Any], instance: str) -> dict:
    item = manifest.get("render", {}).get("wireguard_network", {}).get(instance)
    if not isinstance(item, dict):
        raise RuntimeError("WireGuard networking artifact is missing; rerender the deployment")
    body = item.get("content")
    sources = [
        r
        for r in manifest.get("render", {}).get("module_sources", [])
        if r.get("instance_id") == instance and r.get("component_id") == "wireguard-gw"
    ]
    if (
        item.get("schema") != 1
        or len(sources) != 1
        or item.get("source") != sources[0].get("source")
        or item.get("module_name") != sources[0].get("module_name")
        or not isinstance(body, str)
        or len(body.encode()) > MAX_ASSET
        or hashlib.sha256(body.encode()).hexdigest() != item.get("sha256")
    ):
        raise RuntimeError("WireGuard networking artifact identity or digest is invalid")
    return item


# This installer changes only the canonical guest executable and its protected
# update markers. Network changes remain owned by that executable.
_INSTALL = r"""
import fcntl, hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path
request = json.loads(sys.stdin.read(262145))
body = request['content'].encode()
if os.geteuid() != 0 or len(body) > 131072 or hashlib.sha256(body).hexdigest() != request['sha256']:
    raise RuntimeError('Invalid networking asset')
compile(body, 'wireguard_network.py', 'exec')
state = Path('/var/lib/nebius-wireguard')
target = Path('/usr/local/sbin/nebius-wireguard-client')
if state.is_symlink() or target.is_symlink() or not target.is_file():
    raise RuntimeError('Missing or unsafe existing gateway helper')
def atomic(path, data, mode=0o600):
    if path.is_symlink(): raise RuntimeError('Unsafe networking state')
    fd, name = tempfile.mkstemp(prefix='.network-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)
with (state/'network-install.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    marker = state/'network-upgrade.json'
    intent = {'sha256': request['sha256'], 'wireguard_mtu': request.get('wireguard_mtu')}
    if marker.exists() and json.loads(marker.read_text()) != intent:
        raise RuntimeError('A different networking update is pending')
    atomic(marker, json.dumps(intent).encode())
    backup = state/'network-helper.previous'
    if not backup.exists(): atomic(backup, target.read_bytes())
    if target.read_bytes() != body: atomic(target, body, 0o755)
    deadline = time.monotonic() + 30
    while True:
        active = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit(): continue
            try: args = (proc/'cmdline').read_bytes().split(bytes([0]))
            except (FileNotFoundError, ProcessLookupError): continue
            if str(target).encode() in args and not any(v in args for v in (b'network-status', b'list', b'network-up', b'network-down')):
                active.append(proc.name)
        if not active: break
        if time.monotonic() >= deadline: raise RuntimeError('Prior client writers have not quiesced; retry the same update')
        time.sleep(.1)
    result = subprocess.run([str(target), 'reconcile-network'], input=json.dumps({
        'wireguard_mtu': request.get('wireguard_mtu'), 'revision': request['sha256']
    }), text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Guest networking reconciliation failed; retained state requires same-intent resume')
    response = json.loads(result.stdout)
    response['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    if response['sha256'] != request['sha256']: raise RuntimeError('Networking asset changed during update')
    marker.unlink(); backup.unlink(missing_ok=True)
    print(json.dumps(response))
"""


def remote_network(ssh: Any, command: str, request: Mapping[str, Any]) -> dict:
    parts = (
        ["sudo", "-n", "python3", "-c", _INSTALL]
        if command == "install"
        else ["sudo", "-n", "/usr/local/sbin/nebius-wireguard-client", command]
    )
    argv = _ssh_command_parts(
        ssh_user=ssh.ssh_user,
        public_ip=ssh.public_ip,
        ssh_private_key=ssh.private_key,
        ssh_known_hosts_file=ssh.known_hosts,
        remote_parts=parts,
    )
    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=150,
            env={**os.environ, "LC_ALL": "C"},
        )
        if completed.returncode or len(completed.stdout.encode()) > 256 * 1024:
            raise ValueError
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, subprocess.SubprocessError, ValueError):
        # Remote stdout can contain a client configuration; never include it or
        # stderr in an exception, report, or diagnostic log.
        raise RuntimeError(
            "WireGuard networking operation failed or is unconfirmed; inspect status and resume the same operation"
        ) from None


def reconcile_guest(connection: Mapping[str, Any], pair: Mapping[str, str], asset: dict) -> dict:
    with temporary_ssh_access(connection, pair) as ssh:
        result = remote_network(ssh, "install", asset)
        status = remote_network(ssh, "network-status", {})
    if (
        result.get("sha256") != asset["sha256"]
        or status.get("helper_sha256") != asset["sha256"]
        or result.get("mtu") != status.get("mtu")
    ):
        raise RuntimeError("WireGuard networking independent readback failed")
    return {"sha256": asset["sha256"], "mtu": status["mtu"]}
