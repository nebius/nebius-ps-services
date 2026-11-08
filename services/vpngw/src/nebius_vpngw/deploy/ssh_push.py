from __future__ import annotations

import ast
import atexit
import base64
import csv
import errno
import hashlib
import io
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from _thread import LockType
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from email.parser import BytesParser
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname, urlopen

import yaml

from .. import apply_lock, ingress_policy
from ..agent.vm_ha.inhibition import (
    LIVE_PEER_REPLACEMENT_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
)
from ..agent.vm_ha.mtls import MTLSReceipt, PeerLeaf
from ..agent.vm_ha.mtls_actions import ACTION_NAMES, ACTION_SCHEMA, encode_action_request
from ..agent.vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY
from ..config_loader import InstanceResolvedConfig
from ..replay_window import (
    REPLAY_WINDOW_CAPABILITY,
    REPLAY_WINDOW_SOURCES,
    require_replay_window_capability,
    wheel_supports_replay_window,
)
from ..schema import VMHARuntimeBinding
from ..vm_ha_credentials import (
    VMHACredentialIdentityError,
    credential_ids_from_payload,
    installed_vm_ha_credential_path,
    read_vm_ha_credential_source,
)
from ..vm_ha_diagnostics import decode_not_ready, readiness_reasons
from . import ingress_ssh
from .ssh_client_auth import SSHClientAuth, operator_identity, resolve_ssh_client_auth
from .ssh_policy import SSHTrustPolicy, configure_paramiko_host_verification
from .vm_ha_identity import LegacyVMHAIdentity, parse_legacy_vm_ha_identity
from .vm_ha_package import VMHAAgentPackageError, VMHAPackagePlan

_VM_HA_APPLY_QUIESCENCE_SCRIPT = r"""
import json
import sys
import time
from nebius_vpngw.agent.main import verify_vm_ha_removal_quiescent

deadline = time.monotonic() + 60.0
while True:
    if time.monotonic() >= deadline:
        raise RuntimeError("VM-HA apply inhibition acknowledgement timed out")
    try:
        evidence = verify_vm_ha_removal_quiescent(sys.argv[1])
    except RuntimeError as error:
        if str(error) != "VM-HA removal inhibition has not reached a quiescent controller":
            raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("VM-HA apply inhibition acknowledgement timed out") from None
        time.sleep(min(1.0, remaining))
    else:
        if time.monotonic() >= deadline:
            raise RuntimeError("VM-HA apply inhibition acknowledgement timed out")
        print(json.dumps(evidence))
        break
"""

_LEGACY_VM_HA_IDENTITY_SCRIPT = r"""
import json
import sys

import yaml

path = "/etc/nebius-vpngw/config-resolved.yaml"
try:
    with open(path, encoding="utf-8") as stream:
        payload = yaml.safe_load(stream)
except FileNotFoundError:
    print(json.dumps({"status": "ordinary"}, sort_keys=True))
    raise SystemExit(0)
except Exception:
    raise SystemExit(2)
vm_ha = payload.get("vm_ha") if isinstance(payload, dict) else None
if vm_ha is None:
    print(json.dumps({"status": "ordinary"}, sort_keys=True))
    raise SystemExit(0)
if not isinstance(vm_ha, dict):
    raise SystemExit(2)
node = vm_ha.get("node")
binding = vm_ha.get("runtime_binding")
nodes = binding.get("nodes") if isinstance(binding, dict) else None
if not isinstance(node, dict) or not isinstance(nodes, list):
    raise SystemExit(2)
result = {
    "status": "vm-ha",
    "cluster_id": vm_ha.get("cluster_id"),
    "allocation_id": binding.get("shared_allocation_id"),
    "instance_index": node.get("instance_index"),
    "node_id": node.get("node_id"),
    "role": node.get("role"),
    "nodes": [
        {
            "node_id": item.get("node_id"),
            "role": item.get("role"),
            "compute_id": item.get("compute_id"),
            "network_interface_name": item.get("network_interface_name"),
        }
        for item in nodes
        if isinstance(item, dict)
    ],
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
"""

_VM_HA_REARM_LOCK_RUNNER = r"""
import base64
import fcntl
import os
import subprocess
import sys
import time

action = base64.b64decode(sys.argv[1], validate=True).decode("utf-8")
create_parent = sys.argv[2] == "1"
timeout_seconds = float(sys.argv[3])
state_dir = "/var/lib/nebius-vpngw/vm-ha"
lock_path = "/var/lib/nebius-vpngw/vm-ha/rearm.lock"
if create_parent:
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    os.chmod(state_dir, 0o700)
elif not os.path.isdir(state_dir):
    raise SystemExit(44)
descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
os.fchmod(descriptor, 0o600)
try:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise SystemExit(45)
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
    completed = subprocess.run(["/bin/bash", "-lc", action], check=False)
    raise SystemExit(completed.returncode)
finally:
    os.close(descriptor)
"""

_VM_HA_PREHOST_INSPECTION = r"""
import fcntl
import hashlib
import json
import os
import stat
import time
from importlib.metadata import version
from pathlib import Path
import yaml
from nebius_vpngw.agent.main import _require_auto_healing_mutation_admission
from nebius_vpngw.agent.vm_ha.runtime import validate_installed_credential_bundle
from nebius_vpngw.schema import VMHARuntimeBinding

def read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
            raise ValueError('unsafe inspection file')
        return stream.read(1048577)

root = Path('/var/lib/nebius-vpngw/vm-ha')
config_path = Path('/etc/nebius-vpngw/config-resolved.yaml')
# Take the existing rearm writer lock without creating or rewriting it.
with (root / 'rearm.lock').open('rb') as lock:
    deadline = time.monotonic() + 10
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    raw = read(config_path)
    ha = yaml.safe_load(raw)['vm_ha']
    binding = VMHARuntimeBinding.model_validate(ha['runtime_binding'])
    local = next(node for node in binding.nodes if node.node_id == ha['node']['node_id'])
    credential = validate_installed_credential_bundle(binding, local)
    _require_auto_healing_mutation_admission(state_dir=root)
    if (root / 'tunnel-request.json').exists() or (root / 'tunnel-selection.json').exists():
        from nebius_vpngw.agent.vm_ha.tunnels import require_tunnel_quiescent
        require_tunnel_quiescent(root)
    def record(name):
        path = root / name
        return json.loads(read(path)) if path.exists() else None
    pending = [name for name in (
        'apply.lock', 'manual-failover.json', 'manual-failback.json',
        'rearm-request.json', 'rearm-cloud-operation.json',
    ) if (root / name).exists()]
    staged = Path('/etc/nebius-vpngw/vm-ha-staged')
    result = {
        'boot_id': read('/proc/sys/kernel/random/boot_id').decode().strip(),
        'writer_admission': True,
        'credential_sha256': credential.bundle_digest,
        'config_sha256': hashlib.sha256(raw).hexdigest(),
        'package_version': version('nebius-vpngw'),
        'node_id': ha['node']['node_id'],
        'node': ha['node'],
        'cluster_id': ha['cluster_id'],
        'readiness': ha['readiness'],
        'runtime_binding': ha['runtime_binding'],
        'generation': {k: ha['generation'][k] for k in ('generation_id', 'digests')},
        'checkpoint': record('controller-checkpoint.json'),
        'transfer': record('transfer-progress.json'),
        'rearm': record('rearm-checkpoint.json'),
        'pending_files': pending,
        'staged_configs': {p.name: hashlib.sha256(read(p)).hexdigest()
                           for p in sorted(staged.glob('*.yaml'))},
    }
    if read(config_path) != raw:
        raise ValueError('installed configuration changed during inspection')
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
"""


def _host_identity_failure(error: Exception, paramiko: Any, ssh_target: str) -> RuntimeError | None:
    bad_host_key = getattr(paramiko, "BadHostKeyException", ())
    if isinstance(error, bad_host_key):
        return RuntimeError(f"SSH host identity verification failed for {ssh_target}")
    ssh_exception = getattr(paramiko, "SSHException", ())
    message = str(error).lower()
    if isinstance(error, ssh_exception) and (
        "known_hosts" in message or "host key" in message or "known host" in message
    ):
        return RuntimeError(f"SSH host identity verification failed for {ssh_target}")
    return None


@dataclass(frozen=True)
class VMHAStageReceipt:
    """Secret-free acknowledgement for one exact staged node generation."""

    node_id: str
    generation_id: str
    configuration_digest: str
    static_routes_digest: str
    bgp_policy_digest: str
    staged_file_sha256: str
    nebius_credentials_path: str
    nebius_credentials_sha256: str
    nebius_service_account_id: str
    nebius_authorized_key_id: str


class VMHAAgentArtifactProblem(str, Enum):
    """Closed artifact prerequisites surfaced by VM-HA planning and execution."""

    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INCOMPATIBLE = "incompatible"
    CHANGED = "changed"


class VMHAAgentArtifactError(RuntimeError):
    """Report one sanitized artifact prerequisite without exposing its path."""

    def __init__(self, problem: VMHAAgentArtifactProblem, message: str) -> None:
        super().__init__(message)
        self.problem = problem


class VMHARemoteActivationError(RuntimeError):
    """Required remote activation failed with a closed diagnostic classification."""

    def __init__(self, message: str, *, failure_class: str = "remote command failed") -> None:
        super().__init__(message)
        self.diagnostic = {
            "systemd job failed": "remote-systemd-job-failed",
            "standby auto-healing policy rejected activation": "remote-policy-rejected",
            "HA persistent boot policy rejected activation": "boot-policy-rejected",
            "remote permission denied": "remote-permission-denied",
            "required remote command or unit not found": "remote-command-unavailable",
            "remote command timed out": "remote-command-timeout",
            "no diagnostic output": "remote-diagnostic-unavailable",
        }.get(failure_class, "remote-command-failed")


class VMHAStandbyReplacementNotReady(RuntimeError):
    """The owner has not yet acknowledged its exact replacement inhibition."""

    def __init__(self, message: str, *, reasons: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.reasons = readiness_reasons(reasons)


_VM_HA_SERVICE_ASSET_DESTINATIONS = (
    ("nebius-vpngw-agent.service", "/etc/systemd/system/nebius-vpngw-agent.service", 0o644),
    (
        "nebius-vpngw-agent-ordering.conf",
        "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
        0o644,
    ),
    (
        "nebius-vpngw-fix-routes.service",
        "/etc/systemd/system/nebius-vpngw-fix-routes.service",
        0o644,
    ),
    (
        "nebius-vpngw-fix-routes.timer",
        "/etc/systemd/system/nebius-vpngw-fix-routes.timer",
        0o644,
    ),
    (
        "nebius-vpngw-health-monitor.service",
        "/etc/systemd/system/nebius-vpngw-health-monitor.service",
        0o644,
    ),
    ("nebius-vpngw-ingress.service", "/etc/systemd/system/nebius-vpngw-ingress.service", 0o644),
    (
        "nebius-vpngw-strongswan.conf",
        "/etc/systemd/system/strongswan.service.d/override.conf",
        0o644,
    ),
    (
        "nebius-vpngw-ufw-ordering.conf",
        "/etc/systemd/system/ufw.service.d/override.conf",
        0o644,
    ),
    ("nebius-vpngw-ingress.py", "/usr/local/lib/nebius-vpngw-ingress.py", 0o644),
    ("setup-vpngw-firewall.sh", "/usr/local/bin/setup-vpngw-firewall.sh", 0o755),
    (
        "nebius-vpngw-vm-ha-peer-firewall.sh",
        "/usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh",
        0o755,
    ),
    (
        "nebius-vpngw-esp4-preflight.sh",
        "/usr/local/bin/nebius-vpngw-esp4-preflight.sh",
        0o755,
    ),
    (
        "nebius-vpngw-esp4-preflight.service",
        "/etc/systemd/system/nebius-vpngw-esp4-preflight.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-guard.service",
        "/etc/systemd/system/nebius-vpngw-vm-ha-guard.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha.service",
        "/etc/systemd/system/nebius-vpngw-vm-ha.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-rearm.service",
        "/etc/systemd/system/nebius-vpngw-vm-ha-rearm.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-ordering.conf",
        "/etc/systemd/system/strongswan.service.d/30-vm-ha.conf",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-ordering.conf",
        "/etc/systemd/system/frr.service.d/30-vm-ha.conf",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-ordering.conf",
        "/etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf",
        0o644,
    ),
    (
        "nebius-vpngw-ufw-lock.conf",
        "/usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf",
        0o644,
    ),
)


@dataclass(frozen=True)
class VMHAAgentArtifact:
    """One immutable, locally proven VM-HA deployment artifact."""

    path: Path
    sha256: str
    source: str
    capabilities: tuple[str, ...]
    device: int
    inode: int
    dependency_plans: tuple[tuple[str, VMHAPackagePlan], ...] = field(
        default=(), repr=False, compare=False
    )

    @staticmethod
    def _sha256(stream: Any) -> str:
        digest = hashlib.sha256()
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        stream.seek(0)
        return digest.hexdigest()

    @staticmethod
    def _read_wheel_member(
        wheel: zipfile.ZipFile,
        name: str,
        *,
        limit: int,
    ) -> bytes:
        try:
            info = wheel.getinfo(name)
        except KeyError as error:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "VM-HA agent artifact is missing required package content",
            ) from error
        if info.is_dir() or info.file_size > limit:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "VM-HA agent artifact has invalid required package content",
            )
        return wheel.read(info)

    @staticmethod
    def _record_digest(payload: bytes) -> str:
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        return f"sha256={encoded.decode('ascii')}"

    @classmethod
    def _validate_recorded_member(
        cls,
        records: dict[str, tuple[str, str]],
        name: str,
        payload: bytes,
    ) -> None:
        record = records.get(name)
        if record is None or record != (cls._record_digest(payload), str(len(payload))):
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "VM-HA agent artifact package record does not match required content",
            )

    @staticmethod
    def _validate_capability_sources(
        restoration: bytes,
        inhibition: bytes,
        agent_main: bytes,
    ) -> None:
        try:
            restoration_tree = ast.parse(restoration.decode("utf-8"))
            inhibition_tree = ast.parse(inhibition.decode("utf-8"))
            main_tree = ast.parse(agent_main.decode("utf-8"))
        except (SyntaxError, UnicodeError) as error:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "VM-HA agent artifact capability entry point is invalid",
            ) from error
        declared = any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "STANDBY_RESTORATION_CAPABILITY"
                for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
            )
            and isinstance(node.value, ast.Constant)
            and node.value.value == STANDBY_RESTORATION_CAPABILITY
            for node in ast.walk(restoration_tree)
        )
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "vm_ha.restoration"
            and any(
                alias.name == "STANDBY_RESTORATION_CAPABILITY" and alias.asname is None
                for alias in node.names
            )
            for node in ast.walk(main_tree)
        )
        live_peer_declared = any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "LIVE_PEER_REPLACEMENT_CAPABILITY"
                for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
            )
            and isinstance(node.value, ast.Constant)
            and node.value.value == LIVE_PEER_REPLACEMENT_CAPABILITY
            for node in ast.walk(inhibition_tree)
        )
        live_peer_imported = any(
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "vm_ha.inhibition"
            and any(
                alias.name == "LIVE_PEER_REPLACEMENT_CAPABILITY" and alias.asname is None
                for alias in node.names
            )
            for node in ast.walk(main_tree)
        )
        main_function = next(
            (
                node
                for node in main_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
            ),
            None,
        )
        argument_registered = bool(
            main_function is not None
            and any(
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr == "add_argument"
                and bool(statement.value.args)
                and isinstance(statement.value.args[0], ast.Constant)
                and statement.value.args[0].value == "--agent-capabilities"
                and any(
                    keyword.arg == "action"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value == "store_true"
                    for keyword in statement.value.keywords
                )
                for statement in main_function.body
            )
        )

        def capability_payload(value: ast.AST) -> bool:
            if not isinstance(value, ast.Dict):
                return False
            fields = {
                key.value: item
                for key, item in zip(value.keys, value.values, strict=True)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            schema = fields.get("schema")
            features = fields.get("features")
            return bool(
                isinstance(schema, ast.Constant)
                and schema.value == "nebius-vpngw.agent-capabilities.v1"
                and isinstance(features, (ast.List, ast.Tuple))
                and any(
                    isinstance(item, ast.Constant) and item.value == "independent-heartbeat-v6"
                    for item in features.elts
                )
                and any(
                    isinstance(item, ast.Name)
                    and item.id == "STANDBY_RESTORATION_CAPABILITY"
                    and isinstance(item.ctx, ast.Load)
                    for item in features.elts
                )
                and any(
                    isinstance(item, ast.Name)
                    and item.id == "LIVE_PEER_REPLACEMENT_CAPABILITY"
                    and isinstance(item.ctx, ast.Load)
                    for item in features.elts
                )
            )

        guarded_handler = False
        for node in main_function.body if main_function is not None else ():
            if not (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Attribute)
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id == "args"
                and node.test.attr == "agent_capabilities"
            ):
                continue
            emits_payload = any(
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == "print"
                and any(
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Attribute)
                    and isinstance(argument.func.value, ast.Name)
                    and argument.func.value.id == "json"
                    and argument.func.attr == "dumps"
                    and bool(argument.args)
                    and capability_payload(argument.args[0])
                    for argument in statement.value.args
                )
                for statement in node.body
            )
            returns = any(
                isinstance(item, ast.Return)
                and (
                    item.value is None
                    or isinstance(item.value, ast.Constant)
                    and item.value.value == 0
                )
                for item in node.body
            )
            guarded_handler = emits_payload and returns
            if guarded_handler:
                break
        entry_invokes_main = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__"
            and any(
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == "main"
                and not statement.value.args
                and not statement.value.keywords
                for statement in node.body
            )
            for node in main_tree.body
        )
        if not (
            declared
            and imported
            and live_peer_declared
            and live_peer_imported
            and argument_registered
            and guarded_handler
            and entry_invokes_main
        ):
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "VM-HA agent artifact lacks a valid standby restoration capability entry point",
            )

    @classmethod
    def from_wheel(cls, path: Path, *, source: str) -> VMHAAgentArtifact:
        expanded = path.expanduser()
        artifact_path = Path(os.path.abspath(expanded))
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(artifact_path, flags)
        except OSError as error:
            problem = (
                VMHAAgentArtifactProblem.INCOMPATIBLE
                if error.errno == errno.ELOOP
                else VMHAAgentArtifactProblem.MISSING
            )
            raise VMHAAgentArtifactError(
                problem,
                "VM-HA agent artifact must be one available regular non-symlink wheel",
            ) from error
        try:
            stream = os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
        try:
            identity = os.fstat(stream.fileno())
            if not stat.S_ISREG(identity.st_mode) or artifact_path.suffix != ".whl":
                raise VMHAAgentArtifactError(
                    VMHAAgentArtifactProblem.INCOMPATIBLE,
                    "VM-HA agent artifact must be one available regular non-symlink wheel",
                )
            filename_parts = artifact_path.name.removesuffix(".whl").split("-")
            if not (
                len(filename_parts) in {5, 6}
                and filename_parts[0] == "nebius_vpngw"
                and filename_parts[-3:] == ["py3", "none", "any"]
            ):
                raise VMHAAgentArtifactError(
                    VMHAAgentArtifactProblem.INCOMPATIBLE,
                    "VM-HA agent artifact filename is not a supported project wheel",
                )
            version = filename_parts[1]
            dist_info = f"nebius_vpngw-{version}.dist-info"
            before_sha256 = cls._sha256(stream)
            with zipfile.ZipFile(stream) as wheel:
                names = wheel.namelist()
                if len(names) != len(set(names)):
                    raise VMHAAgentArtifactError(
                        VMHAAgentArtifactProblem.INCOMPATIBLE,
                        "VM-HA agent artifact contains duplicate members",
                    )
                required = {
                    "nebius_vpngw/agent/main.py": cls._read_wheel_member(
                        wheel,
                        "nebius_vpngw/agent/main.py",
                        limit=2 * 1024 * 1024,
                    ),
                    "nebius_vpngw/agent/vm_ha/restoration.py": cls._read_wheel_member(
                        wheel,
                        "nebius_vpngw/agent/vm_ha/restoration.py",
                        limit=1024 * 1024,
                    ),
                    "nebius_vpngw/agent/vm_ha/inhibition.py": cls._read_wheel_member(
                        wheel,
                        "nebius_vpngw/agent/vm_ha/inhibition.py",
                        limit=1024 * 1024,
                    ),
                    f"{dist_info}/METADATA": cls._read_wheel_member(
                        wheel,
                        f"{dist_info}/METADATA",
                        # Core metadata includes the full README description.
                        limit=1024 * 1024,
                    ),
                    f"{dist_info}/WHEEL": cls._read_wheel_member(
                        wheel,
                        f"{dist_info}/WHEEL",
                        limit=64 * 1024,
                    ),
                }
                for source_name in ("boot_policy.py", "vm_ha_diagnostics.py"):
                    member_name = "nebius_vpngw/" + source_name
                    required[member_name] = cls._read_wheel_member(
                        wheel, member_name, limit=256 * 1024
                    )
                for asset_name in {item[0] for item in _VM_HA_SERVICE_ASSET_DESTINATIONS}:
                    member_name = f"nebius_vpngw/systemd/{asset_name}"
                    required[member_name] = cls._read_wheel_member(
                        wheel,
                        member_name,
                        limit=1024 * 1024,
                    )
                record_path = f"{dist_info}/RECORD"
                record_payload = cls._read_wheel_member(
                    wheel,
                    record_path,
                    limit=8 * 1024 * 1024,
                )
                metadata_payload = required[f"{dist_info}/METADATA"]
                wheel_payload = required[f"{dist_info}/WHEEL"]
                metadata = BytesParser().parsebytes(metadata_payload)
                wheel_metadata = BytesParser().parsebytes(wheel_payload)
                if (
                    metadata.get("Name") != "nebius-vpngw"
                    or metadata.get("Version") != version
                    or wheel_metadata.get("Wheel-Version") != "1.0"
                    or wheel_metadata.get("Root-Is-Purelib") != "true"
                    or "py3-none-any" not in wheel_metadata.get_all("Tag", [])
                ):
                    raise VMHAAgentArtifactError(
                        VMHAAgentArtifactProblem.INCOMPATIBLE,
                        "VM-HA agent artifact metadata does not match the selected wheel",
                    )
                rows = list(csv.reader(io.StringIO(record_payload.decode("utf-8"))))
                if any(len(row) != 3 for row in rows):
                    raise VMHAAgentArtifactError(
                        VMHAAgentArtifactProblem.INCOMPATIBLE,
                        "VM-HA agent artifact package record is invalid",
                    )
                records = {row[0]: (row[1], row[2]) for row in rows}
                if len(records) != len(rows) or record_path not in records:
                    raise VMHAAgentArtifactError(
                        VMHAAgentArtifactProblem.INCOMPATIBLE,
                        "VM-HA agent artifact package record is incomplete",
                    )
                for name, payload in required.items():
                    cls._validate_recorded_member(records, name, payload)
                cls._validate_capability_sources(
                    required["nebius_vpngw/agent/vm_ha/restoration.py"],
                    required["nebius_vpngw/agent/vm_ha/inhibition.py"],
                    required["nebius_vpngw/agent/main.py"],
                )
                try:
                    ingress_policy.require_wheel(wheel)
                except ingress_policy.IngressCapabilityError as error:
                    raise VMHAAgentArtifactError(
                        VMHAAgentArtifactProblem.INCOMPATIBLE, str(error)
                    ) from error
                replay_supported = wheel_supports_replay_window(wheel)
                if replay_supported:
                    for source_name in REPLAY_WINDOW_SOURCES:
                        member_name = "nebius_vpngw/" + source_name
                        cls._validate_recorded_member(records, member_name, wheel.read(member_name))
            after_sha256 = cls._sha256(stream)
        except VMHAAgentArtifactError:
            raise
        except (OSError, UnicodeError, zipfile.BadZipFile, csv.Error) as error:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "VM-HA agent artifact is not a readable wheel",
            ) from error
        finally:
            stream.close()
        try:
            current = os.stat(artifact_path, follow_symlinks=False)
        except OSError as error:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.CHANGED,
                "VM-HA agent artifact changed while it was being inspected",
            ) from error
        if before_sha256 != after_sha256 or (
            current.st_dev,
            current.st_ino,
        ) != (
            identity.st_dev,
            identity.st_ino,
        ):
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.CHANGED,
                "VM-HA agent artifact changed while it was being inspected",
            )
        return cls(
            path=artifact_path,
            sha256=after_sha256,
            source=source,
            capabilities=(
                "independent-heartbeat-v6",
                ingress_policy.CAPABILITY,
                LIVE_PEER_REPLACEMENT_CAPABILITY,
                STANDBY_RESTORATION_CAPABILITY,
                *((REPLAY_WINDOW_CAPABILITY,) if replay_supported else ()),
            ),
            device=identity.st_dev,
            inode=identity.st_ino,
        )

    @contextmanager
    def open_verified(self) -> Iterator[BinaryIO]:
        """Hold and yield the exact approved inode after rechecking its digest."""

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as error:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.CHANGED,
                "approved VM-HA agent artifact is no longer a regular file",
            ) from error
        with os.fdopen(descriptor, "rb") as stream:
            current = os.fstat(stream.fileno())
            changed = (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (self.device, self.inode)
                or self._sha256(stream) != self.sha256
            )
            if changed:
                raise VMHAAgentArtifactError(
                    VMHAAgentArtifactProblem.CHANGED,
                    "approved VM-HA agent artifact changed after planning",
                )
            if not {
                "independent-heartbeat-v6",
                ingress_policy.CAPABILITY,
                LIVE_PEER_REPLACEMENT_CAPABILITY,
                STANDBY_RESTORATION_CAPABILITY,
            }.issubset(self.capabilities):
                raise VMHAAgentArtifactError(
                    VMHAAgentArtifactProblem.INCOMPATIBLE,
                    "approved VM-HA agent artifact capability proof is incomplete",
                )
            stream.seek(0)
            yield stream

    def verify_current(self) -> None:
        with self.open_verified():
            pass


@dataclass(frozen=True)
class VMHAApplyLockReceipt:
    """Secret-free receipt for one exact node-scoped HA apply lock."""

    cluster_id: str
    node_id: str
    generation_id: str
    operation_id: str
    record_sha256: str

    def __post_init__(self) -> None:
        if not all((self.cluster_id, self.node_id, self.generation_id)):
            raise ValueError("VM-HA apply-lock receipt identity is incomplete")
        for label, value in (
            ("operation", self.operation_id),
            ("record digest", self.record_sha256),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"VM-HA apply-lock {label} is invalid")


@dataclass(frozen=True)
class VMHAOwnerAdoptionReceipt:
    """Secret-free receipt for one exact current-owner apply declaration."""

    cluster_id: str
    node_id: str
    generation_id: str
    operation_id: str
    record_sha256: str


class SSHPush:
    """Push per-VM config and trigger agent reload via SSH using Paramiko.

    Looks for the following optional fields in the loaded YAML config under
    `gateway_group.vm_spec`:
      - ssh_username (default: "ubuntu")
      - ssh_private_key_path (if omitted, relies on SSH agent/known defaults)
    """

    def __init__(
        self,
        ssh_policy: SSHTrustPolicy | None = None,
        *,
        authentication_lock: LockType | None = None,
    ) -> None:
        # Lazy import to avoid hard dependency when running dry-run
        self._paramiko = None
        self._wheel_path: Path | None = None
        self._wheel_is_installed_fallback = False
        self._temp_wheel_dir: Path | None = None
        self._ssh_policy = ssh_policy
        self._ssh_client_auth: SSHClientAuth | None = None
        self._ssh_client_auth_input: tuple[str, str | None] | None = None
        self._authentication_lock = authentication_lock

    def _connect_client(
        self,
        client: Any,
        *,
        hostname: str,
        username: str,
        vm_spec: dict[str, Any],
        timeout: int = 15,
    ) -> None:
        # Parallel member transports can reference the same selected ssh-agent
        # key. Its signing socket is shared, so only authentication is serialized;
        # each authenticated client/channel remains independently owned.
        with self._authentication_lock or nullcontext():
            self._connect_client_unlocked(
                client, hostname=hostname, username=username, vm_spec=vm_spec, timeout=timeout
            )

    def _connect_client_unlocked(
        self,
        client: Any,
        *,
        hostname: str,
        username: str,
        vm_spec: dict[str, Any],
        timeout: int = 15,
    ) -> None:
        public_key = str(vm_spec.get("ssh_public_key") or "").strip()
        raw_private = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        if public_key or operator_identity() is not None:
            auth_input = (public_key, str(raw_private) if raw_private else None)
            if self._ssh_client_auth is None or self._ssh_client_auth_input != auth_input:
                self._ssh_client_auth = resolve_ssh_client_auth(
                    public_key,
                    explicit_private_key=(
                        Path(str(raw_private)).expanduser() if raw_private else None
                    ),
                )
                self._ssh_client_auth_input = auth_input
            client.connect(
                hostname=hostname,
                username=username,
                timeout=timeout,
                auth_timeout=timeout,
                banner_timeout=timeout,
                **self._ssh_client_auth.paramiko_connect_kwargs(),
            )
            return
        client.connect(
            hostname=hostname,
            username=username,
            key_filename=(str(Path(str(raw_private)).expanduser()) if raw_private else None),
            look_for_keys=True,
            allow_agent=True,
            timeout=timeout,
        )

    def resolve_vm_ha_agent_artifact(self) -> VMHAAgentArtifact:
        """Resolve one existing wheel without building, deleting, or falling back."""

        wheel_override = os.environ.get("VPNGW_AGENT_WHEEL")
        if wheel_override:
            try:
                return VMHAAgentArtifact.from_wheel(
                    Path(wheel_override),
                    source="VPNGW_AGENT_WHEEL",
                )
            except OSError as error:
                raise VMHAAgentArtifactError(
                    VMHAAgentArtifactProblem.MISSING,
                    "configured VM-HA agent artifact is unavailable",
                ) from error

        project_root = self._find_project_root()
        search_dirs = (
            (project_root / "dist",)
            if project_root is not None
            else (Path.cwd(), Path.cwd() / "dist")
        )
        candidates = sorted(
            candidate
            for directory in search_dirs
            if directory.is_dir()
            for candidate in directory.glob("nebius_vpngw-*.whl")
        )
        if not candidates:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.MISSING,
                "VM-HA planning requires one existing agent wheel; set VPNGW_AGENT_WHEEL",
            )
        if len(candidates) != 1:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.AMBIGUOUS,
                "VM-HA planning found multiple agent wheels; set VPNGW_AGENT_WHEEL exactly",
            )
        try:
            return VMHAAgentArtifact.from_wheel(candidates[0], source="local-prebuilt-wheel")
        except VMHAAgentArtifactError as error:
            if error.problem is not VMHAAgentArtifactProblem.MISSING:
                raise
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.CHANGED,
                "selected VM-HA agent artifact changed before inspection",
            ) from error

    @staticmethod
    def _agent_activation_commands(*, agent_cmd: str, vm_ha: bool) -> tuple[str, ...]:
        """Leave VM-HA agent activation exclusively to the fenced controller."""

        return () if vm_ha else (agent_cmd,)

    @staticmethod
    def _vm_ha_peer_firewall_commands(*, vm_ha: bool) -> tuple[str, ...]:
        """Apply the private heartbeat rule from the verified installed asset."""

        if not vm_ha:
            return ()
        script = "/usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh"
        return (f"sudo {script} /etc/nebius-vpngw/config-resolved.yaml",)

    @staticmethod
    def _vm_ha_reset_failed_commands(*, vm_ha: bool) -> tuple[str, ...]:
        """Clear bounded start-limit state before a verified HA activation retry."""

        if not vm_ha:
            return ()
        return (
            "for unit in nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-rearm.service frr.service "
            "strongswan-starter.service strongswan.service "
            "nebius-vpngw-agent.service; do "
            'if systemctl cat "$unit" >/dev/null 2>&1; then '
            'sudo systemctl reset-failed "$unit"; fi; done',
        )

    @staticmethod
    def _vm_ha_control_service_commands(
        *,
        initialize_policy: bool,
        replacement_policy_request: str | None = None,
    ) -> tuple[str, ...]:
        if replacement_policy_request is not None and not initialize_policy:
            raise ValueError("replacement policy adoption requires initialization")
        return (
            *(
                (
                    "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
                    "--vm-ha-auto-healing-action initialize",
                )
                if initialize_policy
                else ()
            ),
            *(
                (
                    "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
                    "--vm-ha-auto-healing-action adopt-replacement "
                    "--vm-ha-auto-healing-request " + shlex.quote(replacement_policy_request),
                )
                if replacement_policy_request is not None
                else ()
            ),
            "sudo /usr/bin/python3 -c "
            + shlex.quote(
                "from nebius_vpngw.runtime_migration import enable_units; "
                "enable_units(['nebius-vpngw-vm-ha-guard.service', 'nebius-vpngw-vm-ha.service', 'nebius-vpngw-vm-ha-rearm.service'])"
            ),
            "sudo systemctl restart nebius-vpngw-vm-ha-guard.service",
            "sudo /usr/bin/python3 -m nebius_vpngw.boot_policy",
            "sudo systemctl restart nebius-vpngw-vm-ha.service",
            "sudo systemctl restart nebius-vpngw-vm-ha-rearm.service",
            "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-guard.service",
            "sudo systemctl is-active --quiet nebius-vpngw-vm-ha.service",
            "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-rearm.service",
        )

    def require_vm_ha_heartbeat_v6(
        self, ssh_target: str, inst_cfg: InstanceResolvedConfig, local_cfg: dict
    ) -> None:
        """Reject a one-member wire change before replacing installed code."""
        if not ssh_target or self._ssh_policy is None:
            raise ValueError("VM-HA capability inspection requires exact-pinned SSH")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=inst_cfg.hostname,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
                vm_spec=vm_spec,
            )
            _, stdout, _ = client.exec_command(
                "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities",
                timeout=30,
            )
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("installed heartbeat capability is unavailable")
            value = json.loads(stdout.read().decode("ascii"))
            if not (
                isinstance(value, dict)
                and value.get("schema") == "nebius-vpngw.agent-capabilities.v1"
                and isinstance(value.get("features"), list)
                and "independent-heartbeat-v6" in value["features"]
            ):
                raise RuntimeError("heartbeat wire upgrade requires coordinated two-member apply")
        finally:
            client.close()

    def refresh_vm_ha_control_services(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
    ) -> None:
        """Refresh only the installed VM-HA control services over exact-pinned SSH."""

        if not ssh_target or self._ssh_policy is None:
            raise ValueError("VM-HA service refresh requires exact-pinned SSH")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=inst_cfg.hostname,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            self._run_remote_commands(
                client,
                (
                    "sudo systemctl daemon-reload",
                    *self._vm_ha_reset_failed_commands(vm_ha=True),
                    *self._vm_ha_control_service_commands(initialize_policy=False),
                ),
                required_remote=True,
            )
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    @staticmethod
    def _install_vm_ha_assets_from_artifact_command(remote_wheel: str) -> str:
        """Install service assets directly from the already verified wheel bytes."""

        from .ordinary_apply import PREVIOUS_AGENT_ORDERINGS, PREVIOUS_UFW_ORDERING

        script = (
            f"assets = {_VM_HA_SERVICE_ASSET_DESTINATIONS!r}\n"
            f"previous_ufw_ordering = {PREVIOUS_UFW_ORDERING!r}\n"
            f"previous_agent_orderings = {PREVIOUS_AGENT_ORDERINGS!r}\n"
            + """\
import os
import pathlib
import sys
import tempfile
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    ufw_override = pathlib.Path("/etc/systemd/system/ufw.service.d/override.conf")
    ufw_payload = archive.read("nebius_vpngw/systemd/nebius-vpngw-ufw-ordering.conf")
    if ufw_override.is_symlink() or (
        ufw_override.exists()
        and (not ufw_override.is_file() or ufw_override.read_bytes() not in
             (ufw_payload, previous_ufw_ordering))
    ):
        raise RuntimeError("product UFW override has unknown contents")
    for name, destination_text, mode in assets:
        payload = archive.read(f"nebius_vpngw/systemd/{name}")
        destination = pathlib.Path(destination_text)
        if name == "nebius-vpngw-agent-ordering.conf" and destination.exists():
            ordinary = archive.read("nebius_vpngw/systemd/nebius-vpngw-ordinary-agent-ordering.conf")
            if destination.read_bytes() not in (payload, ordinary, *previous_agent_orderings):
                raise RuntimeError("product agent override has unknown contents")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
"""
        )
        return f"sudo /usr/bin/python3 -c {shlex.quote(script)} {shlex.quote(remote_wheel)}"

    @staticmethod
    def _read_bounded_remote_output(stream: Any, *, limit: int = 4096) -> bytes:
        """Drain one remote stream while retaining only a bounded diagnostic prefix."""

        retained = bytearray()
        while True:
            chunk = stream.read(1024)
            if not chunk:
                break
            payload = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
            if len(retained) < limit:
                retained.extend(payload[: limit - len(retained)])
        return bytes(retained)

    @staticmethod
    def _remote_command_class(command: str) -> str:
        """Return a closed, secret-free label for a required activation command."""

        if "u.admit_activation(" in command:
            return "admit HA checkpoint activation"
        units = (
            "nebius-vpngw-vm-ha-guard.service",
            "nebius-vpngw-vm-ha.service",
            "nebius-vpngw-vm-ha-rearm.service",
        )
        for unit in units:
            if command == f"sudo systemctl restart {unit}":
                return f"restart {unit}"
            if command == f"sudo systemctl is-active --quiet {unit}":
                return f"verify active {unit}"
        if command == (
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
            "--vm-ha-auto-healing-action initialize"
        ):
            return "initialize standby auto-healing policy"
        if command == "sudo /usr/bin/python3 -m nebius_vpngw.boot_policy":
            return "install fenced HA boot policy"
        if "--vm-ha-auto-healing-action adopt-replacement" in command:
            return "adopt retained standby auto-healing policy"
        if command.startswith("sudo systemctl enable "):
            return "enable VM-HA control services"
        if command == "sudo systemctl daemon-reload":
            return "reload systemd units"
        return "required remote activation step"

    @staticmethod
    def _remote_failure_class(stdout: bytes, stderr: bytes) -> str:
        """Map bounded remote output to a closed diagnostic class."""

        output = b"\n".join((stdout, stderr)).decode(errors="replace").lower()
        if "job for " in output and " failed" in output:
            return "systemd job failed"
        if "standby auto-healing policy action failed:" in output:
            return "standby auto-healing policy rejected activation"
        if "ha-boot-policy-" in output:
            return "HA persistent boot policy rejected activation"
        if "permission denied" in output:
            return "remote permission denied"
        if "not found" in output or "not be found" in output:
            return "required remote command or unit not found"
        if "timed out" in output or "timeout" in output:
            return "remote command timed out"
        if not output.strip():
            return "no diagnostic output"
        return "remote command failed"

    @classmethod
    def _run_remote_commands(
        cls,
        client: Any,
        commands: Iterable[str],
        *,
        required_remote: bool,
    ) -> bool:
        """Run activation commands and preserve one bounded, sanitized failure."""

        had_failures = False
        for command in commands:
            command_class = cls._remote_command_class(command)
            try:
                _stdin, stdout, stderr = client.exec_command(
                    command,
                    get_pty=True,
                    # Admission: quiesce35 + locks5 + observe15 + guard30 + transport10.
                    timeout=95 if command_class == "admit HA checkpoint activation" else 20,
                )
                safe_stdout = cls._read_bounded_remote_output(stdout)
                safe_stderr = cls._read_bounded_remote_output(stderr)
                return_code = stdout.channel.recv_exit_status()
            except Exception as error:
                if required_remote:
                    raise VMHARemoteActivationError(
                        f"VM-HA activation command failed: {command_class}"
                    ) from error
                print(f"[SSHPush] Exec failed for: {command} -> {error}")
                had_failures = True
                continue

            if return_code == 0:
                continue
            if not required_remote:
                detail = safe_stderr.decode(errors="replace").strip()
                print(f"[SSHPush] Command failed (rc={return_code}): {command}\n{detail}")
                had_failures = True
                continue
            failure_class = cls._remote_failure_class(safe_stdout, safe_stderr)
            failure = (
                f"VM-HA activation command failed: {command_class} "
                f"(rc={return_code}; {failure_class})"
            )
            print(f"[SSHPush] {failure}")
            if required_remote:
                raise VMHARemoteActivationError(failure, failure_class=failure_class)
            had_failures = True
        return had_failures

    @staticmethod
    def _wait_for_vm_ha_materialization(
        client: Any,
        *,
        timeout_seconds: float = 60.0,
        interval_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Wait for the controller-owned passive materialization predicate."""

        if not all(math.isfinite(value) and value > 0 for value in (timeout_seconds, interval_seconds)):
            raise ValueError("VM-HA materialization timeout and interval must be positive")
        deadline = clock() + timeout_seconds
        command = "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-materialized"
        while (remaining := deadline - clock()) > 0:
            observation_deadline = min(deadline, clock() + 20.0)
            _stdin, stdout, _stderr = client.exec_command(
                command, get_pty=True, timeout=min(20.0, remaining)
            )
            channel = stdout.channel
            try:
                while True:
                    remaining = observation_deadline - clock()
                    if remaining <= 0:
                        raise RuntimeError(
                            "VM-HA passive materialization observation exceeded the bounded wait"
                        )
                    # Drain without retaining potentially sensitive diagnostics.
                    # Bounded reads keep a noisy peer from defeating the deadline.
                    if channel.recv_ready():
                        channel.recv(65536)
                    if channel.recv_stderr_ready():
                        channel.recv_stderr(65536)
                    if (
                        channel.exit_status_ready()
                        and not channel.recv_ready()
                        and not channel.recv_stderr_ready()
                    ):
                        rc = channel.recv_exit_status()
                        break
                    sleeper(min(0.02, remaining))
            finally:
                channel.close()
            remaining = deadline - clock()
            if remaining <= 0:
                break
            if rc == 0:
                return
            sleeper(min(interval_seconds, remaining))
        raise RuntimeError("VM-HA passive materialization did not converge within the bounded wait")

    def _ensure_paramiko(self):
        if self._paramiko is None:
            import paramiko  # type: ignore

            self._paramiko = paramiko
        return self._paramiko

    @staticmethod
    def _render_vm_ha_config(
        inst_cfg: InstanceResolvedConfig,
        runtime_binding: VMHARuntimeBinding,
    ) -> str:
        """Bind authoritative post-provision identities into one node manifest."""

        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        readiness = inst_cfg.vm_ha_readiness
        if node is None or generation is None or readiness is None:
            raise ValueError("VM-HA staging requires a complete resolved node manifest")
        bound_nodes = [item for item in runtime_binding.nodes if item.node_id == node.node_id]
        if len(bound_nodes) != 1 or bound_nodes[0].role.value != node.role.value:
            raise ValueError("VM-HA runtime binding does not match the staged node")
        payload = yaml.safe_load(inst_cfg.config_yaml)
        if not isinstance(payload, dict) or not isinstance(payload.get("vm_ha"), dict):
            raise ValueError("VM-HA resolved YAML has no node manifest")
        if not (
            runtime_binding.cluster_id == payload["vm_ha"].get("cluster_id")
            and runtime_binding.generation_id == generation.generation_id
            and runtime_binding.configuration_digest == generation.digests.configuration
            and runtime_binding.static_routes_digest == generation.digests.static_routes
            and runtime_binding.bgp_policy_digest == generation.digests.bgp_policy
        ):
            raise ValueError("VM-HA runtime binding does not match the staged generation")
        manifests = (payload["vm_ha"].get("generation") or {}).get("logical_manifests")
        expected_manifests = generation.logical_manifests
        if manifests != {
            "static_routes_json": expected_manifests.static_routes_json,
            "bgp_policy_json": expected_manifests.bgp_policy_json,
        }:
            raise ValueError("VM-HA staged logical manifests differ from the committed generation")
        if (
            hashlib.sha256(manifests["static_routes_json"].encode()).hexdigest()
            != runtime_binding.static_routes_digest
            or hashlib.sha256(manifests["bgp_policy_json"].encode()).hexdigest()
            != runtime_binding.bgp_policy_digest
        ):
            raise ValueError("VM-HA staged logical manifest digest mismatch")

        payload["vm_ha"]["runtime_binding"] = runtime_binding.model_dump(mode="json")
        return yaml.safe_dump(payload, sort_keys=False)

    @staticmethod
    def _nebius_credentials_target(
        node_id: str,
        generation_id: str,
        digest: str,
    ) -> str:
        return installed_vm_ha_credential_path(
            node_id=node_id,
            generation_id=generation_id,
            credential_sha256=digest,
        )

    @staticmethod
    def _read_credential_file(path_text: str, *, label: str, node_id: str) -> bytes:
        try:
            return read_vm_ha_credential_source(path_text)
        except VMHACredentialIdentityError as exc:
            raise ValueError(
                f"VM-HA credential source {label} for {node_id} is not an exact private file"
            ) from exc

    @classmethod
    def _runtime_binding_for_nebius_credentials(
        cls,
        *,
        inst_cfg: InstanceResolvedConfig,
        runtime_binding: VMHARuntimeBinding,
        target: str,
        digest: str,
    ) -> VMHARuntimeBinding:
        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        if node is None or generation is None:
            raise ValueError("VM-HA Nebius credential identity is incomplete")
        expected_target = cls._nebius_credentials_target(
            node.node_id, generation.generation_id, digest
        )
        if target != expected_target:
            raise ValueError("VM-HA Nebius credential receipt has a non-canonical target path")
        bound_nodes = [item for item in runtime_binding.nodes if item.node_id == node.node_id]
        if (
            len(bound_nodes) != 1
            or bound_nodes[0].nebius_credentials_path != target
            or bound_nodes[0].nebius_credentials_sha256 != digest
        ):
            raise ValueError("VM-HA runtime credential binding changed after preflight")
        return runtime_binding

    @staticmethod
    def _vm_ha_receipt(
        inst_cfg: InstanceResolvedConfig,
        rendered_config: str,
        *,
        runtime_binding: VMHARuntimeBinding,
        nebius_credentials_path: str | Path,
        nebius_credentials_sha256: str,
    ) -> VMHAStageReceipt:
        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        readiness = inst_cfg.vm_ha_readiness
        if node is None or generation is None or readiness is None:
            raise ValueError("VM-HA staging requires a complete resolved node manifest")
        if generation.generation_id != readiness.generation_id:
            raise ValueError("VM-HA generation and readiness identities do not match")
        if generation.digests != readiness.digests:
            raise ValueError("VM-HA generation and readiness digests do not match")
        service_account_id = runtime_binding.nebius_service_account_id
        authorized_key_id = runtime_binding.nebius_authorized_key_id
        if not service_account_id or not authorized_key_id:
            raise ValueError("VM-HA staged credential identity is incomplete")
        return VMHAStageReceipt(
            node_id=node.node_id,
            generation_id=generation.generation_id,
            configuration_digest=generation.digests.configuration,
            static_routes_digest=generation.digests.static_routes,
            bgp_policy_digest=generation.digests.bgp_policy,
            staged_file_sha256=hashlib.sha256(rendered_config.encode("utf-8")).hexdigest(),
            nebius_credentials_path=str(nebius_credentials_path),
            nebius_credentials_sha256=nebius_credentials_sha256,
            nebius_service_account_id=service_account_id,
            nebius_authorized_key_id=authorized_key_id,
        )

    @staticmethod
    def _vm_ha_staged_verify_command(receipt: VMHAStageReceipt) -> str:
        path = f"/etc/nebius-vpngw/vm-ha-staged/{receipt.generation_id}.yaml"
        checks = [f"echo '{receipt.staged_file_sha256}  {path}' | sudo sha256sum --check --status"]
        checks.append(
            "sudo test \"$(sudo stat -c '%U:%G:%a' "
            f'{receipt.nebius_credentials_path})" = root:root:600 && '
            f"echo '{receipt.nebius_credentials_sha256}  "
            f"{receipt.nebius_credentials_path}' | sudo sha256sum --check --status"
        )
        return " && ".join(checks)

    @staticmethod
    def _credential_install_commands(
        *,
        base: str,
        temporary: str,
        target: str,
        digest: str,
    ) -> tuple[str, ...]:
        pending = f"{target}.new"
        return (
            f"sudo install -d -o root -g root -m 0700 {base}",
            f"sudo install -o root -g root -m 0600 {temporary} {pending}",
            f"sudo test \"$(sudo stat -c '%U:%G:%a' {pending})\" = root:root:600",
            f"echo '{digest}  {pending}' | sudo sha256sum --check --status",
            f"sudo mv {pending} {target}",
            f"sudo test \"$(sudo stat -c '%U:%G:%a' {target})\" = root:root:600",
            f"echo '{digest}  {target}' | sudo sha256sum --check --status",
        )

    @staticmethod
    def _vm_ha_rearm_locked_command(
        action: str,
        *,
        create_parent: bool,
        wait_seconds: float = 25.0,
    ) -> str:
        """Run one remote action while holding the rearm writer lock."""

        encoded_action = base64.b64encode(action.encode("utf-8")).decode("ascii")
        return " ".join(
            (
                "sudo",
                "/usr/bin/python3",
                "-c",
                shlex.quote(_VM_HA_REARM_LOCK_RUNNER),
                shlex.quote(encoded_action),
                "1" if create_parent else "0",
                str(wait_seconds),
            )
        )

    @staticmethod
    def _vm_ha_apply_lock_payload(
        *,
        cluster_id: str,
        node_id: str,
        generation_id: str,
        operation_id: str,
    ) -> tuple[bytes, VMHAApplyLockReceipt]:
        if not all((cluster_id, node_id, generation_id)):
            raise ValueError("VM-HA apply-lock identity is incomplete")
        if len(operation_id) != 64 or any(char not in "0123456789abcdef" for char in operation_id):
            raise ValueError("VM-HA apply-lock operation identity is invalid")
        payload = {
            "apply_locked": True,
            "cluster_id": cluster_id,
            "generation_id": generation_id,
            "node_id": node_id,
            "operation_id": operation_id,
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
        }
        encoded = apply_lock.encode(payload)
        return encoded, VMHAApplyLockReceipt(
            cluster_id=cluster_id,
            node_id=node_id,
            generation_id=generation_id,
            operation_id=operation_id,
            record_sha256=hashlib.sha256(encoded).hexdigest(),
        )

    @staticmethod
    def _vm_ha_owner_adoption_payload(
        *,
        runtime_binding: VMHARuntimeBinding,
        node_id: str,
        generation_id: str,
        digests: Any,
        operation_id: str,
    ) -> tuple[bytes, VMHAOwnerAdoptionReceipt]:
        peers = tuple(node.node_id for node in runtime_binding.nodes if node.node_id != node_id)
        if len(peers) != 1:
            raise ValueError("VM-HA owner adoption requires one exact peer")
        payload = {
            "allocation_id": runtime_binding.shared_allocation_id,
            "cluster_id": runtime_binding.cluster_id,
            "digests": {
                "bgp_policy": digests.bgp_policy,
                "configuration": digests.configuration,
                "static_routes": digests.static_routes,
            },
            "generation_id": generation_id,
            "node_id": node_id,
            "operation_id": operation_id,
            "peer_node_id": peers[0],
            "schema": "nebius-vpngw/vm-ha-apply-owner-adoption-v1",
        }
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode("utf-8")
        return encoded, VMHAOwnerAdoptionReceipt(
            cluster_id=runtime_binding.cluster_id,
            node_id=node_id,
            generation_id=generation_id,
            operation_id=operation_id,
            record_sha256=hashlib.sha256(encoded).hexdigest(),
        )

    def install_vm_ha_apply_lock(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        runtime_binding: VMHARuntimeBinding,
        operation_id: str,
    ) -> VMHAApplyLockReceipt:
        """Atomically install and read back one exact node-scoped apply lock."""

        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        if node is None or generation is None:
            raise ValueError("VM-HA apply lock requires a complete node manifest")
        if self._ssh_policy is None:
            raise ValueError("VM-HA apply lock requires an exact SSH trust policy")
        payload = yaml.safe_load(inst_cfg.config_yaml)
        vm_ha_payload = payload.get("vm_ha") if isinstance(payload, dict) else None
        matching_nodes = tuple(
            item for item in runtime_binding.nodes if item.node_id == node.node_id
        )
        if (
            not isinstance(vm_ha_payload, dict)
            or runtime_binding.cluster_id != vm_ha_payload.get("cluster_id")
            or runtime_binding.generation_id != generation.generation_id
            or len(matching_nodes) != 1
            or matching_nodes[0].role.value != node.role.value
        ):
            raise ValueError("VM-HA apply-lock runtime binding does not match the node")
        encoded, receipt = self._vm_ha_apply_lock_payload(
            cluster_id=runtime_binding.cluster_id,
            node_id=node.node_id,
            generation_id=generation.generation_id,
            operation_id=operation_id,
        )
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        temporary = (
            f"/tmp/nebius-vpngw-vm-ha-apply-lock-{inst_cfg.instance_index}-{operation_id[:12]}.json"
        )
        destination = "/var/lib/nebius-vpngw/vm-ha/apply.lock"
        pending = f"{destination}.new"
        mtls_inhibition = "/var/lib/nebius-vpngw/vm-ha/mtls/inhibition.json"
        replacement_inhibition = "/var/lib/nebius-vpngw/vm-ha/standby-replacement.inhibition.json"
        paramiko = self._ensure_paramiko()
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=inst_cfg.hostname,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            with client.open_sftp() as sftp, sftp.file(temporary, "wb") as stream:
                stream.write(encoded)
                sftp.chmod(temporary, 0o600)
            action = (
                "sudo install -d -o root -g root -m 0700 /var/lib/nebius-vpngw/vm-ha && "
                f"if sudo test -e {mtls_inhibition} || sudo test -L {mtls_inhibition}; "
                "then exit 46; fi && "
                f"if sudo test -e {replacement_inhibition} || "
                f"sudo test -L {replacement_inhibition}; then exit 47; fi && "
                "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
                f"--vm-ha-apply-lock-quiescent {temporary} && "
                f"sudo install -o root -g root -m 0600 {temporary} {pending} && "
                f"sudo test \"$(sudo stat -c '%U:%G:%a' {pending})\" = root:root:600 && "
                f"echo '{receipt.record_sha256}  {pending}' | "
                "sudo sha256sum --check --status && "
                f"if sudo test -e {destination} || sudo test -L {destination}; then "
                "sudo /usr/bin/python3 -B -c "
                f"{shlex.quote(apply_lock.verification_script())} "
                f"{destination} {receipt.record_sha256}; fi && "
                f"sudo mv {pending} {destination} && sudo cat {destination}"
            )
            command = self._vm_ha_rearm_locked_command(action, create_parent=True)
            stdin, stdout, stderr = client.exec_command(command, timeout=60)
            return_code = stdout.channel.recv_exit_status()
            observed = stdout.read()
            if return_code != 0 or observed != encoded:
                raise RuntimeError(f"VM-HA apply-lock verification failed for {node.node_id}")
            return receipt
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            try:
                client.exec_command(f"rm -f {temporary}", timeout=30)
            except Exception:
                pass
            client.close()

    def install_vm_ha_apply_owner_adoption(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        runtime_binding: VMHARuntimeBinding,
        lock_receipt: VMHAApplyLockReceipt,
        predecessor_operation_id: str | None = None,
    ) -> VMHAOwnerAdoptionReceipt:
        """Declare the exact cloud-selected owner inside its fenced apply."""

        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        if node is None or generation is None:
            raise ValueError("VM-HA owner adoption requires a complete node manifest")
        if self._ssh_policy is None:
            raise ValueError("VM-HA owner adoption requires an exact SSH trust policy")
        if (
            lock_receipt.cluster_id != runtime_binding.cluster_id
            or lock_receipt.node_id != node.node_id
            or lock_receipt.generation_id != generation.generation_id
        ):
            raise ValueError("VM-HA owner adoption does not match the apply lock")
        encoded, receipt = self._vm_ha_owner_adoption_payload(
            runtime_binding=runtime_binding,
            node_id=node.node_id,
            generation_id=generation.generation_id,
            digests=generation.digests,
            operation_id=lock_receipt.operation_id,
        )
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        temporary = (
            "/tmp/nebius-vpngw-vm-ha-owner-adoption-"
            f"{inst_cfg.instance_index}-{lock_receipt.operation_id[:12]}.json"
        )
        lock_path = "/var/lib/nebius-vpngw/vm-ha/apply.lock"
        destination = "/var/lib/nebius-vpngw/vm-ha/apply-owner-adoption.json"
        pending = f"{destination}.new"
        predecessor_check = (
            f"echo '{receipt.record_sha256}  {destination}' | sudo sha256sum --check --status"
        )
        if predecessor_operation_id is not None:
            from .. import ha_repair

            if (
                re.fullmatch(r"[a-f0-9]{64}", predecessor_operation_id) is None
                or predecessor_operation_id == lock_receipt.operation_id
            ):
                raise ValueError("VM-HA owner adoption predecessor is invalid")
            previous = json.loads(encoded)
            previous["operation_id"] = predecessor_operation_id
            previous_digest = hashlib.sha256(apply_lock.encode(previous)).hexdigest()
            script = Path(ha_repair.__file__).read_text() + (
                "\nimport sys\narchive_apply_owner_adoption(*sys.argv[1:])\n"
            )
            predecessor_check = (
                f"sudo /usr/bin/python3 -B -c {shlex.quote(script)} "
                f"{receipt.record_sha256} {previous_digest} {lock_receipt.record_sha256}"
            )
        paramiko = self._ensure_paramiko()
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=inst_cfg.hostname,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            with client.open_sftp() as sftp, sftp.file(temporary, "wb") as stream:
                stream.write(encoded)
                sftp.chmod(temporary, 0o600)
            action = (
                f"echo '{lock_receipt.record_sha256}  {lock_path}' | "
                "sudo sha256sum --check --status && "
                f"sudo install -o root -g root -m 0600 {temporary} {pending} && "
                f"echo '{receipt.record_sha256}  {pending}' | "
                "sudo sha256sum --check --status && "
                f"if sudo test -e {destination} || sudo test -L {destination}; then "
                f"{predecessor_check}; fi && "
                f"sudo mv {pending} {destination} && sudo cat {destination}"
            )
            command = self._vm_ha_rearm_locked_command(action, create_parent=False)
            stdin, stdout, stderr = client.exec_command(command, timeout=60)
            return_code = stdout.channel.recv_exit_status()
            observed = stdout.read()
            if return_code != 0:
                failure = (
                    "writer lock timeout"
                    if return_code == 45
                    else f"remote action exit {return_code}"
                )
                raise RuntimeError(
                    f"VM-HA apply-owner adoption verification failed for {node.node_id}: {failure}"
                )
            if observed != encoded:
                raise RuntimeError(
                    "VM-HA apply-owner adoption verification failed for "
                    f"{node.node_id}: exact readback mismatch"
                )
            return receipt
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            try:
                client.exec_command(f"rm -f {temporary}", timeout=30)
            except Exception:
                pass
            client.close()

    def clear_vm_ha_apply_lock(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        receipt: VMHAApplyLockReceipt,
    ) -> None:
        """Clear only the exact lock represented by ``receipt``."""

        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        if node is None or generation is None:
            raise ValueError("VM-HA apply-lock clear requires a complete node manifest")
        if self._ssh_policy is None:
            raise ValueError("VM-HA apply-lock clear requires an exact SSH trust policy")
        payload = yaml.safe_load(inst_cfg.config_yaml)
        vm_ha_payload = payload.get("vm_ha") if isinstance(payload, dict) else None
        if (
            not isinstance(vm_ha_payload, dict)
            or vm_ha_payload.get("cluster_id") != receipt.cluster_id
            or node.node_id != receipt.node_id
            or generation.generation_id != receipt.generation_id
        ):
            raise ValueError("VM-HA apply-lock receipt does not match the node manifest")
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        destination = "/var/lib/nebius-vpngw/vm-ha/apply.lock"
        paramiko = self._ensure_paramiko()
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=inst_cfg.hostname,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            action = (
                f"if sudo test ! -e {destination}; then exit 43; "
                f"elif echo '{receipt.record_sha256}  {destination}' | "
                "sudo sha256sum --check --status; then "
                f"sudo rm -f {destination} && sudo test ! -e {destination} && "
                "printf 'CLEARED\\n'; "
                "else exit 42; fi"
            )
            command = self._vm_ha_rearm_locked_command(action, create_parent=False)
            stdin, stdout, stderr = client.exec_command(command, timeout=60)
            return_code = stdout.channel.recv_exit_status()
            result = stdout.read().decode("utf-8").strip()
            if return_code != 0 or result != "CLEARED":
                raise RuntimeError(f"VM-HA apply-lock clear failed for {node.node_id}")
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def stage_vm_ha_config(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        runtime_binding: VMHARuntimeBinding,
        nebius_credentials_path: str | Path,
    ) -> VMHAStageReceipt:
        """Stage one node and its Nebius credential without exporting mTLS keys."""

        node = inst_cfg.vm_ha_node
        assert node is not None
        credential_content = self._read_credential_file(
            str(nebius_credentials_path),
            label="nebius_credentials",
            node_id=node.node_id,
        )
        generation = inst_cfg.vm_ha_generation
        assert generation is not None
        credential_digest = hashlib.sha256(credential_content).hexdigest()
        declared_service_account_id, declared_key_id = credential_ids_from_payload(
            credential_content
        )
        credential_target = self._nebius_credentials_target(
            node.node_id,
            generation.generation_id,
            credential_digest,
        )
        credential_base = str(Path(credential_target).parent)
        staged_binding = self._runtime_binding_for_nebius_credentials(
            inst_cfg=inst_cfg,
            runtime_binding=runtime_binding,
            target=credential_target,
            digest=credential_digest,
        )
        if (
            staged_binding.nebius_service_account_id != declared_service_account_id
            or staged_binding.nebius_authorized_key_id != declared_key_id
        ):
            raise ValueError("VM-HA credential source identity changed after preflight")
        rendered_config = self._render_vm_ha_config(inst_cfg, staged_binding)
        receipt = self._vm_ha_receipt(
            inst_cfg,
            rendered_config,
            runtime_binding=staged_binding,
            nebius_credentials_path=credential_target,
            nebius_credentials_sha256=credential_digest,
        )
        paramiko = self._ensure_paramiko()
        gg = local_cfg.get("gateway_group") or {}
        vm_spec = gg.get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        upload_directory = f"/tmp/nebius-vpngw-vm-ha-upload-{inst_cfg.instance_index}"
        connected = False
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=inst_cfg.hostname if self._ssh_policy is not None else None,
                transport_host=ssh_target if self._ssh_policy is not None else None,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            ingress_ssh.preflight(client, inst_cfg.config_yaml)
            connected = True
            temporary = f"{upload_directory}/config.yaml"
            destination = f"/etc/nebius-vpngw/vm-ha-staged/{receipt.generation_id}.yaml"
            stdin, stdout, stderr = client.exec_command(
                f"install -d -m 0700 {upload_directory}", timeout=30
            )
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError(
                    f"VM-HA credential upload preparation failed for {receipt.node_id}"
                )
            with client.open_sftp() as sftp, sftp.file(temporary, "w") as stream:
                stream.write(rendered_config)
                sftp.chmod(temporary, 0o600)
            credential_temporary = f"{upload_directory}/nebius-credentials.json"
            with client.open_sftp() as sftp, sftp.file(credential_temporary, "wb") as stream:
                stream.write(credential_content)
                sftp.chmod(credential_temporary, 0o600)
            for command in self._credential_install_commands(
                base=credential_base,
                temporary=credential_temporary,
                target=credential_target,
                digest=credential_digest,
            ):
                stdin, stdout, stderr = client.exec_command(command, timeout=30)
                if stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError(
                        f"VM-HA Nebius credential installation failed for {receipt.node_id}"
                    )
            command = (
                "sudo install -d -m 0700 /etc/nebius-vpngw/vm-ha-staged && "
                f"sudo install -o root -g root -m 0600 {temporary} {destination} && "
                f"sudo sha256sum {destination}"
            )
            stdin, stdout, stderr = client.exec_command(command, timeout=30)
            return_code = stdout.channel.recv_exit_status()
            observed = stdout.read().decode().strip().split(maxsplit=1)[0]
            if return_code != 0 or observed != receipt.staged_file_sha256:
                raise RuntimeError(f"VM-HA stage verification failed for {receipt.node_id}")
            return receipt
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            try:
                if connected:
                    try:
                        stdin, stdout, stderr = client.exec_command(
                            f"find {upload_directory} -depth -delete", timeout=30
                        )
                        if stdout.channel.recv_exit_status() != 0:
                            raise RuntimeError
                    except Exception as error:
                        raise RuntimeError(
                            f"VM-HA private staging cleanup failed for {receipt.node_id}"
                        ) from error
            finally:
                client.close()

    def deactivate_vm_ha(
        self,
        ssh_target: str,
        local_cfg: dict,
        *,
        instance_name: str | None = None,
        retire_member: bool = False,
    ) -> bool:
        """Remove stale HA activation state before an ordinary agent restart."""

        if not ssh_target:
            raise ValueError("VM-HA deactivation requires an SSH target")
        if self._ssh_policy is not None and not instance_name:
            raise ValueError("VM-HA deactivation requires an exact member identity")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        retire_commands = (
            """
for unit in nebius-vpngw-agent.service nebius-vpngw-health-monitor.service nebius-vpngw-fix-routes.timer nebius-vpngw-fix-routes.service strongswan-starter.service strongswan.service strongswan-swanctl.service frr.service; do
  if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q "^$unit"; then
    systemctl disable --now "$unit"
  fi
done
rm -f /etc/nebius-vpngw/config-resolved.yaml
"""
            if retire_member
            else ""
        )
        initial_stale = 1 if retire_member else 0
        stale_check = f"""sudo /bin/bash -lc '
set -eu
stale={initial_stale}
for path in /etc/nebius-vpngw/vm-ha-enabled /etc/systemd/system/nebius-vpngw-vm-ha.service /etc/systemd/system/nebius-vpngw-vm-ha-guard.service /etc/systemd/system/nebius-vpngw-vm-ha-rearm.service /etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf /etc/systemd/system/strongswan.service.d/30-vm-ha.conf /etc/systemd/system/frr.service.d/30-vm-ha.conf /etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf /usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf /etc/nebius-vpngw/vm-ha-staged /etc/nebius-vpngw/vm-ha-credentials /etc/nebius-vpngw/vm-ha; do
  if [ -e "$path" ]; then stale=1; fi
done
state_dir=/var/lib/nebius-vpngw/vm-ha
rearm_lock="$state_dir/rearm.lock"
if [ -L "$state_dir" ]; then
  stale=1
elif [ -e "$state_dir" ]; then
  if [ ! -d "$state_dir" ] || [ ! -f "$rearm_lock" ] || [ -L "$rearm_lock" ]; then
    stale=1
  elif find "$state_dir" -mindepth 1 ! -path "$rearm_lock" -print -quit | grep -q .; then
    stale=1
  fi
fi
for unit in nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha-rearm.service; do
  if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q "^$unit"; then
    stale=1
  fi
done
printf "VM_HA_STALE=%s\\n" "$stale"
'"""
        removal_action = f"""
set -eu
if systemctl list-unit-files --no-legend nebius-vpngw-vm-ha-rearm.service 2>/dev/null | grep -q '^nebius-vpngw-vm-ha-rearm.service'; then
  systemctl disable --now nebius-vpngw-vm-ha-rearm.service
fi
if systemctl is-active --quiet nebius-vpngw-vm-ha-rearm.service 2>/dev/null; then
  exit 46
fi
rm -f /etc/nebius-vpngw/vm-ha-enabled
for unit in nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-guard.service; do
  if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q "^$unit"; then
    systemctl disable --now "$unit"
  fi
done
rm -f /etc/systemd/system/nebius-vpngw-vm-ha.service
rm -f /etc/systemd/system/nebius-vpngw-vm-ha-guard.service
rm -f /etc/systemd/system/nebius-vpngw-vm-ha-rearm.service
rm -f /etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf
rm -f /etc/systemd/system/strongswan.service.d/30-vm-ha.conf
rm -f /etc/systemd/system/frr.service.d/30-vm-ha.conf
rm -f /etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf
rm -f /usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf
for path in /etc/nebius-vpngw/vm-ha-staged /etc/nebius-vpngw/vm-ha-credentials /etc/nebius-vpngw/vm-ha; do
  if [ -d "$path" ]; then find "$path" -depth -delete; fi
done
{retire_commands}
systemctl daemon-reload
if [ -d /var/lib/nebius-vpngw/vm-ha ]; then
  find /var/lib/nebius-vpngw/vm-ha -mindepth 1 -depth ! -path /var/lib/nebius-vpngw/vm-ha/rearm.lock -delete
fi
printf "VM_HA_DEACTIVATED=1\\n"
"""
        command = self._vm_ha_rearm_locked_command(removal_action, create_parent=True)
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            stdin, stdout, stderr = client.exec_command(stale_check, get_pty=True, timeout=30)
            return_code = stdout.channel.recv_exit_status()
            stale_result = stdout.read().decode().splitlines()
            if return_code != 0 or not {"VM_HA_STALE=0", "VM_HA_STALE=1"}.intersection(
                stale_result
            ):
                detail = stderr.read().decode().strip()
                raise RuntimeError(f"VM-HA deactivation inspection failed: {detail or return_code}")
            if "VM_HA_STALE=0" in stale_result:
                return False
            stdin, stdout, stderr = client.exec_command(command, get_pty=True, timeout=90)
            return_code = stdout.channel.recv_exit_status()
            if return_code != 0:
                detail = stderr.read().decode().strip()
                raise RuntimeError(f"VM-HA deactivation failed: {detail or return_code}")
            return "VM_HA_DEACTIVATED=1" in stdout.read().decode().splitlines()
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def _run_vm_ha_removal_agent_command(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        agent_flag: str,
        operation_id: str,
        wait_for_apply_quiescence: bool = False,
    ) -> dict[str, Any]:
        if not (
            ssh_target
            and instance_name
            and len(operation_id) == 64
            and all(character in "0123456789abcdef" for character in operation_id)
            and agent_flag
            in {
                "--vm-ha-removal-inhibit",
                "--vm-ha-removal-ready",
                "--vm-ha-standby-replacement-inhibit",
                "--vm-ha-standby-replacement-ready",
                "--vm-ha-standby-replacement-peer-binding",
                "--vm-ha-standby-replacement-release",
            }
        ):
            raise ValueError("VM-HA inhibition command identity is invalid")
        if wait_for_apply_quiescence and agent_flag != "--vm-ha-removal-ready":
            raise ValueError("VM-HA apply wait requires the quiescence verifier")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            if agent_flag.startswith("--vm-ha-standby-replacement-"):
                _stdin, capability_stdout, _stderr = client.exec_command(
                    "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities",
                    timeout=30,
                )
                if capability_stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError("VM-HA standby replacement capability verification failed")
                try:
                    capability = json.loads(capability_stdout.read().decode("ascii"))
                except (UnicodeError, json.JSONDecodeError):
                    raise RuntimeError(
                        "VM-HA standby replacement capability evidence was malformed"
                    ) from None
                features = capability.get("features") if isinstance(capability, dict) else None
                required_capability = (
                    LIVE_PEER_REPLACEMENT_CAPABILITY
                    if agent_flag == "--vm-ha-standby-replacement-peer-binding"
                    else STANDBY_REPLACEMENT_INHIBITION_CAPABILITY
                )
                if not (
                    isinstance(capability, dict)
                    and capability.get("schema") == "nebius-vpngw.agent-capabilities.v1"
                    and isinstance(features, list)
                    and required_capability in features
                ):
                    raise RuntimeError("VM-HA installed agent lacks standby replacement inhibition")
            command = (
                f"sudo /usr/bin/python3 -m nebius_vpngw.agent.main {agent_flag} {operation_id}"
            )
            if wait_for_apply_quiescence:
                # The installed predecessor already owns this read-only verifier.
                # Lock installation precedes controller acknowledgement; wait for
                # that acknowledgement before stopping either member or installing.
                command = (
                    "sudo timeout 65s /usr/bin/python3 -c "
                    f"{shlex.quote(_VM_HA_APPLY_QUIESCENCE_SCRIPT)} {operation_id}"
                )
            stdin, stdout, stderr = client.exec_command(
                command, timeout=75 if wait_for_apply_quiescence else 60
            )
            return_code = stdout.channel.recv_exit_status()
            if return_code != 0:
                if agent_flag == "--vm-ha-standby-replacement-ready" and return_code == 75:
                    raise VMHAStandbyReplacementNotReady(
                        "VM-HA standby replacement inhibition is not controller-quiescent",
                        reasons=decode_not_ready(stderr.read(2049)),
                    )
                raise RuntimeError("VM-HA inhibition command failed")
            try:
                payload = json.loads(stdout.read().decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError("VM-HA inhibition returned malformed evidence") from error
            expected_schema = {
                "--vm-ha-removal-inhibit": "nebius-vpngw/vm-ha-removal-inhibition-v1",
                "--vm-ha-removal-ready": "nebius-vpngw/vm-ha-removal-quiescent-v1",
                "--vm-ha-standby-replacement-inhibit": (STANDBY_REPLACEMENT_INHIBITION_SCHEMA),
                "--vm-ha-standby-replacement-ready": (
                    "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1"
                ),
                "--vm-ha-standby-replacement-peer-binding": (LIVE_PEER_REPLACEMENT_CAPABILITY),
                "--vm-ha-standby-replacement-release": (
                    "nebius-vpngw/vm-ha-standby-replacement-release-v1"
                ),
            }[agent_flag]
            if not (
                isinstance(payload, dict)
                and set(payload)
                == {"schema", "cluster_id", "node_id", "generation_id", "operation_id"}
                and payload.get("schema") == expected_schema
                and payload.get("operation_id") == operation_id
                and isinstance(payload.get("cluster_id"), str)
                and bool(payload.get("cluster_id"))
                and isinstance(payload.get("node_id"), str)
                and bool(payload.get("node_id"))
                and isinstance(payload.get("generation_id"), str)
                and len(payload["generation_id"]) == 64
            ):
                raise RuntimeError("VM-HA inhibition returned invalid evidence")
            return payload
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def ensure_vm_ha_agent_package(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        artifact: VMHAAgentArtifact | None = None,
        handoff: Any = None,
        checkpoint_conversion: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Install and prove the exact agent plus its crypto dependencies."""

        if not ssh_target or self._ssh_policy is None:
            raise ValueError("VM-HA package preparation requires exact-pinned SSH")
        if artifact is None:
            wheel_path = self._build_wheel(allow_installed_fallback=False)
            if wheel_path is None or not wheel_path.is_file():
                raise VMHAAgentPackageError(
                    "VM-HA package preparation requires a deployable agent wheel"
                )
            artifact = VMHAAgentArtifact.from_wheel(wheel_path, source="direct-apply-build")
        require_replay_window_capability(local_cfg, artifact.capabilities)
        require_replay_window_capability(
            yaml.safe_load(inst_cfg.config_yaml), artifact.capabilities
        )
        dependency_plan = dict(artifact.dependency_plans).get(inst_cfg.hostname)
        with ExitStack() as resources:
            if dependency_plan is None and handoff is None:
                from .vm_ha_package import inspect_plan

                # Fresh/recovery hosts use the same selected dependency installer.
                # Ignoring pip-owned predecessors leaves conflicting dist-info.
                dependency_plan = inspect_plan(
                    self,
                    ssh_target,
                    inst_cfg,
                    local_cfg,
                    target_identity=inst_cfg.hostname,
                    wheel=artifact.path,
                )
                resources.callback(dependency_plan.close)
            return self._ensure_vm_ha_planned_package(
                ssh_target,
                inst_cfg,
                local_cfg,
                artifact=artifact,
                dependency_plan=dependency_plan,
                handoff=handoff,
                checkpoint_conversion=checkpoint_conversion,
            )

    def _ensure_vm_ha_planned_package(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        artifact: VMHAAgentArtifact,
        dependency_plan: Any,
        handoff: Any,
        checkpoint_conversion: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Install an exact product wheel with a verified dependency plan."""

        wheel_sha256 = artifact.sha256
        if handoff is not None:
            if dependency_plan is None:
                raise VMHAAgentPackageError("HA handoff requires its approved package plan")
            receipt = (
                handoff.verify_prepared()
                if handoff.package_receipt is not None
                else handoff.prepare_package(dependency_plan)
            )
            if not set(artifact.capabilities).issubset(receipt.get("capabilities", [])):
                raise VMHAAgentPackageError("HA handoff package capabilities changed")
            return receipt
        if dependency_plan is None:
            raise VMHAAgentPackageError("VM-HA package requires a selected dependency plan")
        from .ordinary_apply import digest, remote
        from .vm_ha_package import package_predecessor

        current = remote(
            self,
            ssh_target,
            inst_cfg,
            local_cfg,
            {"action": "inspect-package", "manifest": dependency_plan.manifest},
        )["observation"]
        if digest(package_predecessor(current)) != digest(dependency_plan.observation):
            raise VMHAAgentPackageError("VM-HA package predecessor changed after approval")
        from .checkpoint_conversion import guard_package

        guard_package(
            self,
            ssh_target,
            inst_cfg,
            local_cfg,
            artifact=wheel_sha256,
            boot_id=dependency_plan.observation["boot_id"],
            expected=checkpoint_conversion,
            publish=False,
        )
        from .ordinary_handoff import prepare_guarded
        from .vm_ha_package import stage

        stage(self, dependency_plan, local_cfg, artifact=artifact)
        receipt = prepare_guarded(self, dependency_plan, local_cfg, checkpoint_conversion)
        guard_package(
            self,
            ssh_target,
            inst_cfg,
            local_cfg,
            artifact=wheel_sha256,
            boot_id=dependency_plan.observation["boot_id"],
            expected=checkpoint_conversion,
            publish=True,
        )
        if not set(artifact.capabilities).issubset(receipt.get("capabilities", [])):
            raise VMHAAgentPackageError("HA guarded package capabilities differ")
        return receipt

    @staticmethod
    def _validate_vm_ha_mtls_action_result(action: str, result: object) -> None:
        if action in {"prepare", "prepare-peer-replacement"}:
            MTLSReceipt.from_mapping(result)
            return
        if action == "stage-peer":
            PeerLeaf.from_mapping(result)
            return
        if action == "record-observation":
            if not (
                isinstance(result, dict)
                and set(result) == {"verified_observations"}
                and isinstance(result["verified_observations"], int)
                and not isinstance(result["verified_observations"], bool)
                and result["verified_observations"] >= 1
            ):
                raise ValueError("managed mTLS observation result is invalid")
            return
        if action == "inhibit":
            if not (
                isinstance(result, dict)
                and set(result)
                == {"schema", "operation_id", "cluster_id", "node_id", "generation_id"}
                and result.get("schema") == "nebius-vpngw/vm-ha-mtls-inhibition-v1"
            ):
                raise ValueError("managed mTLS inhibition result is invalid")
            return
        if action == "release-inhibition":
            if not (
                isinstance(result, dict)
                and set(result) == {"released", "operation_id"}
                and result.get("released") is True
            ):
                raise ValueError("managed mTLS inhibition release is invalid")
            return
        if action in {"status", "rollback"}:
            if not (
                isinstance(result, dict)
                and set(result)
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
                }
            ):
                raise ValueError("managed mTLS status result is invalid")
            return
        if action == "expand-trust" and result is None:
            return
        if not (
            isinstance(result, dict)
            and set(result)
            == {
                "cluster_id",
                "node_id",
                "compute_id",
                "epoch",
                "certificate_fingerprint",
                "spki_fingerprint",
                "peers",
            }
            and isinstance(result["peers"], list)
            and bool(result["peers"])
            and all(PeerLeaf.from_mapping(peer) for peer in result["peers"])
        ):
            raise ValueError("managed mTLS snapshot result is invalid")

    def run_vm_ha_mtls_action(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        action: str,
        request: dict[str, object],
    ) -> dict[str, object]:
        """Run one public-only action over the exact-pinned management channel."""

        if not ssh_target or not instance_name or action not in ACTION_NAMES:
            raise ValueError("managed mTLS remote action identity is invalid")
        if self._ssh_policy is None:
            raise ValueError("managed mTLS remote action requires exact-pinned SSH")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            encoded = encode_action_request(request)
            command = (
                "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
                f"--vm-ha-mtls-action {shlex.quote(action)} "
                f"--vm-ha-mtls-request {shlex.quote(encoded)}"
            )
            _stdin, stdout, _stderr = client.exec_command(command, timeout=90)
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("managed mTLS remote action failed")
            try:
                response = json.loads(stdout.read().decode("ascii"))
            except (UnicodeError, json.JSONDecodeError):
                raise RuntimeError(
                    "managed mTLS remote action returned malformed evidence"
                ) from None
            if not (
                isinstance(response, dict)
                and set(response) == {"schema", "action", "result"}
                and response.get("schema") == ACTION_SCHEMA
                and response.get("action") == action
            ):
                raise RuntimeError("managed mTLS remote action returned invalid evidence")
            try:
                self._validate_vm_ha_mtls_action_result(action, response["result"])
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "managed mTLS remote action returned invalid evidence"
                ) from error
            return response
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def inhibit_vm_ha_removal(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        node_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Install a controller-visible removal gate on one exact member."""

        payload = self._run_vm_ha_removal_agent_command(
            ssh_target,
            instance_name,
            local_cfg,
            agent_flag="--vm-ha-removal-inhibit",
            operation_id=operation_id,
        )
        if payload["node_id"] != node_id:
            raise RuntimeError("VM-HA removal inhibition returned a foreign node identity")
        return payload

    def verify_vm_ha_removal_quiescent(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        inhibition: dict[str, Any],
    ) -> None:
        """Prove one member acknowledged its gate with no accepted effect."""

        payload = self._run_vm_ha_removal_agent_command(
            ssh_target,
            instance_name,
            local_cfg,
            agent_flag="--vm-ha-removal-ready",
            operation_id=str(inhibition.get("operation_id") or ""),
        )
        if payload != {
            **inhibition,
            "schema": "nebius-vpngw/vm-ha-removal-quiescent-v1",
        }:
            raise RuntimeError("VM-HA removal quiescence evidence changed identity")

    def verify_vm_ha_apply_inhibition_quiescent(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        node_id: str,
        operation_id: str,
    ) -> None:
        """Prove an installed apply lock is controller-visible with no accepted effect."""

        payload = self._run_vm_ha_removal_agent_command(
            ssh_target,
            instance_name,
            local_cfg,
            agent_flag="--vm-ha-removal-ready",
            operation_id=operation_id,
            wait_for_apply_quiescence=True,
        )
        if payload.get("node_id") != node_id:
            raise RuntimeError("VM-HA apply inhibition returned a foreign node identity")

    def inhibit_vm_ha_standby_replacement(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        node_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Install the exact owner-preserving standby replacement gate."""

        payload = self._run_vm_ha_removal_agent_command(
            ssh_target,
            instance_name,
            local_cfg,
            agent_flag="--vm-ha-standby-replacement-inhibit",
            operation_id=operation_id,
        )
        if payload["node_id"] != node_id:
            raise RuntimeError(
                "VM-HA standby replacement inhibition returned a foreign node identity"
            )
        return payload

    def verify_vm_ha_standby_replacement_quiescent(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        inhibition: dict[str, Any],
        timeout_seconds: float = 60.0,
        poll_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Prove the exact owner remains active behind the transfer gate."""

        deadline = monotonic() + timeout_seconds
        final_probe_due = False
        while True:
            try:
                payload = self._run_vm_ha_removal_agent_command(
                    ssh_target,
                    instance_name,
                    local_cfg,
                    agent_flag="--vm-ha-standby-replacement-ready",
                    operation_id=str(inhibition.get("operation_id") or ""),
                )
                break
            except VMHAStandbyReplacementNotReady as error:
                if final_probe_due:
                    raise VMHAStandbyReplacementNotReady(
                        "VM-HA standby replacement inhibition did not become "
                        "controller-quiescent within the bounded wait",
                        reasons=error.reasons,
                    ) from None
                now = monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    # The failed probe sampled the remote status before it
                    # returned.  Permit one final observation at the deadline
                    # so a transition completed during that SSH round trip is
                    # not rejected without being observed.
                    final_probe_due = True
                    continue
                sleeper(min(poll_seconds, remaining))
        if payload != {
            **inhibition,
            "schema": "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1",
        }:
            raise RuntimeError("VM-HA standby replacement quiescence evidence changed identity")

    def commit_vm_ha_standby_replacement_peer_binding(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        inhibition: dict[str, Any],
    ) -> None:
        """Publish the staged replacement peer identity without reloading the owner."""

        payload = self._run_vm_ha_removal_agent_command(
            ssh_target,
            instance_name,
            local_cfg,
            agent_flag="--vm-ha-standby-replacement-peer-binding",
            operation_id=str(inhibition.get("operation_id") or ""),
        )
        if payload != {
            **inhibition,
            "schema": LIVE_PEER_REPLACEMENT_CAPABILITY,
        }:
            raise RuntimeError("VM-HA replacement peer binding changed identity")

    def release_vm_ha_standby_replacement_inhibition(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
        *,
        inhibition: dict[str, Any],
    ) -> None:
        """Release only the exact completed standby replacement gate."""

        payload = self._run_vm_ha_removal_agent_command(
            ssh_target,
            instance_name,
            local_cfg,
            agent_flag="--vm-ha-standby-replacement-release",
            operation_id=str(inhibition.get("operation_id") or ""),
        )
        if payload != {
            **inhibition,
            "schema": "nebius-vpngw/vm-ha-standby-replacement-release-v1",
        }:
            raise RuntimeError("VM-HA standby replacement release changed identity")

    def stop_vm_ha_mutation_services(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
    ) -> None:
        """Drain writers and their dependent start jobs behind the installed inhibition."""

        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        watchdog: threading.Timer | None = None
        expired = threading.Event()
        deadline: float | None = None
        failure: Exception | None = None

        def expire() -> None:
            expired.set()
            # Transport closure wakes Paramiko's otherwise unbounded exec
            # acknowledgement wait as well as its output/status waiters.
            client.close()

        command = """sudo /bin/bash -lc '
set -eu
units=(nebius-vpngw-vm-ha-rearm.service nebius-vpngw-vm-ha.service)
for unit in nebius-vpngw-fix-routes.timer nebius-vpngw-fix-routes.service nebius-vpngw-health-monitor.service nebius-vpngw-agent.service frr.service strongswan-starter.service strongswan.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then units+=("$unit"); fi
done
# A timed-out systemctl client can leave an accepted dependency start pending.
# Replace those jobs together; reject late restarts while the stop/guard runs.
# Stop maintenance triggers too: systemd starts their dependencies before
# ExecCondition can reject a routing action behind the apply lock.
# The guard may try to reload FRR; its existing inactive-FRR fallback is safe.
systemctl --job-mode=replace-irreversibly stop "${units[@]}"
# An inactive, never-started controller has no ExecStopPost transition.
# Establish the same current-boot fence explicitly before accepting quiescence.
/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard
for unit in "${units[@]}"; do
  # A stopped unit can retain a historical failure. Its failed label alone
  # cannot authorize this barrier: the current stop/guard and all checks below do.
  case "$(systemctl show --value --property=ActiveState "$unit")" in
    inactive|failed) ;;
    *) exit 1 ;;
  esac
  if [[ "$unit" != *.timer ]]; then
    test "$(systemctl show --value --property=MainPID "$unit")" = 0
    test "$(systemctl show --value --property=ControlPID "$unit")" = 0
  fi
  test -z "$(systemctl show --value --property=Job "$unit")"
done
# Result may retain an earlier failed ExecStopPost even after a successful stop.
# The fresh guard command above and current-boot postconditions below are the
# authority for this barrier; do not reset or rely on historical unit results.
test "$(sysctl -n net.ipv4.ip_forward)" = 0
/usr/bin/python3 - <<"PY"
import json
from pathlib import Path
value = json.loads(Path("/var/lib/nebius-vpngw/vm-ha/guard.json").read_text())
assert value.get("data_plane_mode") == "blocked"
assert value.get("guard_boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
PY
printf "VM_HA_MUTATION_SERVICES_STOPPED=1\\n"
'"""
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            deadline = time.monotonic() + 90.0
            watchdog = threading.Timer(90.0, expire)
            watchdog.daemon = True
            watchdog.start()
            _stdin, stdout, _stderr = client.exec_command(command, timeout=90)
            channel = stdout.channel
            output_bytes = bytearray()
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "VM-HA stop verification exceeded its deadline; remote result is unverified"
                        )
                    # Channel I/O timeouts do not bound recv_exit_status(). Drain
                    # both streams while polling; noisy output cannot defer the
                    # deadline or fill the SSH receive window indefinitely.
                    if channel.recv_ready():
                        output_bytes.extend(channel.recv(65536))
                        if len(output_bytes) > 1048576:
                            raise RuntimeError("VM-HA stop verification output exceeded its limit")
                    if channel.recv_stderr_ready():
                        channel.recv_stderr(65536)
                    if (
                        channel.exit_status_ready()
                        and not channel.recv_ready()
                        and not channel.recv_stderr_ready()
                    ):
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                "VM-HA stop verification exceeded its deadline; remote result is unverified"
                            )
                        return_code = channel.recv_exit_status()
                        break
                    time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
            finally:
                channel.close()
            output = output_bytes.decode("utf-8").splitlines()
            if return_code != 0 or "VM_HA_MUTATION_SERVICES_STOPPED=1" not in output:
                raise RuntimeError("VM-HA mutation services or dependent jobs did not stop")
        except Exception as error:
            failure = error
        finally:
            if watchdog is not None:
                watchdog.cancel()
                # start() can raise before starting a thread, or be interrupted
                # after starting it. Cancel either way; join only a started one.
                if watchdog.ident is not None:
                    watchdog.join()
            client.close()
        # Settle the watchdog before accepting success, including a completion
        # racing its expiry. Unknown remote outcomes never authorize activation.
        if expired.is_set() or (deadline is not None and time.monotonic() >= deadline):
            raise RuntimeError(
                "VM-HA stop verification exceeded its deadline; remote result is unverified"
            ) from failure
        if failure is not None:
            identity_failure = _host_identity_failure(failure, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from failure
            raise failure

    def inspect_vm_ha_prehost(self, ssh_target: str, instance_name: str, local_cfg: dict) -> dict:
        """Read bounded installed identity and operation evidence through exact SSH pins."""

        return self._inspect_vm_ha_host(
            ssh_target, instance_name, local_cfg, script=_VM_HA_PREHOST_INSPECTION
        )

    def inspect_vm_ha_replacement_recovery(
        self, ssh_target: str, instance_name: str, local_cfg: dict, *, generation_id: str
    ) -> dict:
        """Inspect only public recovery authority while retaining the existing gates."""

        from .checkpoint_conversion import source as checkpoint_source
        from .vm_ha_host_recovery import REPLACEMENT_RECOVERY_INSPECTION

        if re.fullmatch(r"[0-9a-f]{64}", generation_id) is None:
            raise ValueError("replacement recovery generation is invalid")
        return self._inspect_vm_ha_host(
            ssh_target,
            instance_name,
            local_cfg,
            script=checkpoint_source()
            + "generation_id = "
            + repr(generation_id)
            + "\n"
            + REPLACEMENT_RECOVERY_INSPECTION,
        )

    def inspect_vm_ha_upgrade_recovery(
        self, ssh_target: str, instance_name: str, local_cfg: dict, *, generation_id: str
    ) -> dict:
        """Observe exact interrupted-upgrade authority without changing host state."""

        from .checkpoint_conversion import source as checkpoint_source
        from .vm_ha_upgrade_recovery import UPGRADE_RECOVERY_INSPECTION

        if re.fullmatch(r"[0-9a-f]{64}", generation_id) is None:
            raise ValueError("upgrade recovery generation is invalid")
        return self._inspect_vm_ha_host(
            ssh_target,
            instance_name,
            local_cfg,
            script=checkpoint_source()
            + "generation_id = "
            + repr(generation_id)
            + "\n"
            + UPGRADE_RECOVERY_INSPECTION,
        )

    def _inspect_vm_ha_host(
        self, ssh_target: str, instance_name: str, local_cfg: dict, *, script: str
    ) -> dict:

        if not ssh_target or self._ssh_policy is None:
            raise ValueError("VM-HA pre-host inspection requires an exact-pinned SSH target")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        source = script.encode()
        if len(source) > 4 * 1024 * 1024:
            raise ValueError("VM-HA source transport exceeds limit")
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(client, hostname=ssh_target, username=username, vm_spec=vm_spec)
            stdin, stdout, _stderr = client.exec_command(
                "sudo -n PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B", timeout=30
            )
            deadline = time.monotonic() + 30
            sent = 0
            while sent < len(source):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("VM-HA source upload timed out; result unverified")
                stdin.channel.settimeout(remaining)
                count = stdin.channel.send(source[sent : sent + 65536])
                if not count:
                    raise RuntimeError("VM-HA source transport closed; result unverified")
                sent += count
            stdin.channel.shutdown_write()
            stdout.channel.settimeout(max(0.001, deadline - time.monotonic()))
            output = stdout.read(1048577)
            if len(output) > 1048576 or stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("VM-HA pre-host observation failed closed")
            payload = json.loads(output)
            if not isinstance(payload, dict):
                raise ValueError("VM-HA pre-host observation is malformed")
            return payload
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def inspect_legacy_vm_ha_identity(
        self,
        ssh_target: str,
        instance_name: str,
        local_cfg: dict,
    ) -> LegacyVMHAIdentity | None:
        """Read only the secret-free legacy runtime identity through an exact SSH pin."""

        if not ssh_target or self._ssh_policy is None:
            raise ValueError("Legacy VM-HA inspection requires an exact-pinned SSH target")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        encoded = base64.b64encode(_LEGACY_VM_HA_IDENTITY_SCRIPT.encode("utf-8")).decode("ascii")
        command = f"printf %s {encoded} | base64 -d | sudo python3"
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            stdin, stdout, stderr = client.exec_command(command, timeout=30)
            return_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode().strip()
            if return_code != 0:
                raise RuntimeError("Former VM-HA runtime identity could not be read")
            try:
                payload = json.loads(output)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("Former VM-HA runtime identity is malformed") from error
            return parse_legacy_vm_ha_identity(payload, instance_name=instance_name)
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def verify_vm_ha_deactivated(
        self,
        ssh_target: str,
        local_cfg: dict,
        *,
        instance_name: str | None = None,
        retire_member: bool = False,
    ) -> None:
        """Independently prove one former member has no remaining HA authority."""

        if not ssh_target:
            raise ValueError("VM-HA deactivation verification requires an SSH target")
        if self._ssh_policy is not None and not instance_name:
            raise ValueError("VM-HA deactivation verification requires an exact member identity")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        retire_checks = (
            """
test ! -e /etc/nebius-vpngw/config-resolved.yaml
for unit in nebius-vpngw-agent.service nebius-vpngw-health-monitor.service nebius-vpngw-fix-routes.timer nebius-vpngw-fix-routes.service strongswan-starter.service strongswan.service strongswan-swanctl.service frr.service; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then exit 1; fi
  if systemctl is-enabled --quiet "$unit" 2>/dev/null; then exit 1; fi
done
"""
            if retire_member
            else ""
        )
        command = f"""sudo /bin/bash -lc '
set -eu
for path in /etc/nebius-vpngw/vm-ha-enabled /etc/systemd/system/nebius-vpngw-vm-ha.service /etc/systemd/system/nebius-vpngw-vm-ha-guard.service /etc/systemd/system/nebius-vpngw-vm-ha-rearm.service /etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf /etc/systemd/system/strongswan.service.d/30-vm-ha.conf /etc/systemd/system/frr.service.d/30-vm-ha.conf /etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf /usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf /etc/nebius-vpngw/vm-ha-staged /etc/nebius-vpngw/vm-ha-credentials /etc/nebius-vpngw/vm-ha; do
  test ! -e "$path"
done
state_dir=/var/lib/nebius-vpngw/vm-ha
rearm_lock="$state_dir/rearm.lock"
test ! -L "$state_dir"
if [ -e "$state_dir" ]; then
  test -d "$state_dir"
  test -f "$rearm_lock"
  test ! -L "$rearm_lock"
  test "$(stat -c "%U:%G:%a" "$state_dir")" = root:root:700
  test "$(stat -c "%U:%G:%a" "$rearm_lock")" = root:root:600
  if find "$state_dir" -mindepth 1 ! -path "$rearm_lock" -print -quit | grep -q .; then exit 1; fi
fi
for unit in nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha-rearm.service; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then exit 1; fi
  if systemctl is-enabled --quiet "$unit" 2>/dev/null; then exit 1; fi
done
{retire_checks}
printf "VM_HA_TERMINAL_NON_HA=1\\n"
'"""
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                paramiko,
                policy=self._ssh_policy,
                hostname=instance_name,
                transport_host=ssh_target,
            )
            self._connect_client(
                client,
                hostname=ssh_target,
                username=username,
                vm_spec=vm_spec,
            )
            stdin, stdout, stderr = client.exec_command(command, get_pty=True, timeout=60)
            return_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode().splitlines()
            if return_code != 0 or "VM_HA_TERMINAL_NON_HA=1" not in output:
                detail = stderr.read().decode().strip()
                raise RuntimeError(
                    f"VM-HA terminal deactivation verification failed: {detail or return_code}"
                )
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def _find_project_root(self) -> Path | None:
        """Locate repo root based on the installed module path."""
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "pyproject.toml").exists():
                return parent
        return None

    def _select_wheel_from_dirs(self, search_dirs: list[Path]) -> Path | None:
        """Pick the best available wheel from search dirs, preferring current version."""
        version = None
        try:
            version = metadata.version("nebius-vpngw")
        except metadata.PackageNotFoundError:
            version = None

        candidates: list[Path] = []
        for directory in search_dirs:
            if not directory.exists():
                continue
            wheels = sorted(
                directory.glob("nebius_vpngw-*.whl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not wheels:
                continue
            if version:
                for wheel in wheels:
                    if f"nebius_vpngw-{version}-" in wheel.name:
                        return wheel
            candidates.extend(wheels)

        if not candidates:
            return None
        return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    def _download_install_wheel(self, wheel_url: str, wheel_name: str) -> Path | None:
        if self._temp_wheel_dir is None:
            self._temp_wheel_dir = Path(tempfile.mkdtemp(prefix="nebius-vpngw-wheel-"))

        target_path = self._temp_wheel_dir / wheel_name
        if target_path.exists():
            return target_path

        print("[SSHPush] Downloading the originally installed release wheel...")
        try:
            with urlopen(wheel_url, timeout=60) as response, target_path.open("wb") as target:
                shutil.copyfileobj(response, target)
        except Exception as e:
            if target_path.exists():
                target_path.unlink()
            print(f"[SSHPush] WARNING: Failed to download the original install wheel: {e}")
            return None
        return target_path

    def _wheel_from_install_metadata(self) -> Path | None:
        try:
            distribution = metadata.distribution("nebius-vpngw")
        except metadata.PackageNotFoundError:
            return None

        direct_url_raw = distribution.read_text("direct_url.json")
        if not direct_url_raw:
            return None

        try:
            direct_url = json.loads(direct_url_raw)
        except json.JSONDecodeError:
            print("[SSHPush] WARNING: Could not parse direct_url.json for installed package")
            return None

        wheel_url = direct_url.get("url")
        if not isinstance(wheel_url, str) or not wheel_url:
            return None

        parsed_url = urlparse(wheel_url)
        wheel_name = Path(unquote(parsed_url.path)).name
        if not wheel_name.endswith(".whl"):
            return None

        if parsed_url.scheme == "file":
            local_path_str = url2pathname(unquote(parsed_url.path))
            if parsed_url.netloc:
                local_path_str = f"//{parsed_url.netloc}{local_path_str}"
            local_path = Path(local_path_str)
            if local_path.is_file():
                print(f"[SSHPush] Reusing install wheel from local file: {local_path.name}")
                return local_path
            print(
                "[SSHPush] WARNING: The original install wheel is no longer present at the "
                "recorded file path"
            )
            return None

        if parsed_url.scheme in {"http", "https"}:
            downloaded_wheel = self._download_install_wheel(wheel_url, wheel_name)
            if downloaded_wheel:
                print(f"[SSHPush] Reusing install wheel from original URL: {downloaded_wheel.name}")
            return downloaded_wheel

        return None

    def _build_wheel(self, *, allow_installed_fallback: bool = True) -> Path | None:
        """Build the nebius-vpngw wheel package if not already built."""
        if self._wheel_path and self._wheel_path.exists():
            if self._wheel_is_installed_fallback and not allow_installed_fallback:
                return None
            return self._wheel_path

        wheel_override = os.environ.get("VPNGW_AGENT_WHEEL")
        if wheel_override:
            override_path = Path(wheel_override).expanduser()
            if override_path.is_file():
                self._wheel_path = override_path
                self._wheel_is_installed_fallback = False
                print(f"[SSHPush] Using wheel from VPNGW_AGENT_WHEEL: {override_path.name}")
                return self._wheel_path
            print(f"[SSHPush] WARNING: VPNGW_AGENT_WHEEL not found: {override_path}")

        # Find project root (where pyproject.toml is) from the installed module path.
        project_root = self._find_project_root()
        if not project_root:
            # Release/pipx install: look for a local wheel in cwd or ./dist
            local_wheel = self._select_wheel_from_dirs([Path.cwd(), Path.cwd() / "dist"])
            if local_wheel:
                self._wheel_path = local_wheel
                self._wheel_is_installed_fallback = False
                print(f"[SSHPush] Using local wheel: {self._wheel_path.name}")
                return self._wheel_path
            install_wheel = self._wheel_from_install_metadata()
            if install_wheel and allow_installed_fallback:
                self._wheel_path = install_wheel
                self._wheel_is_installed_fallback = True
                print(f"[SSHPush] Using install wheel: {self._wheel_path.name}")
                return self._wheel_path
            print(
                "[SSHPush] WARNING: No local wheel found. "
                "Set VPNGW_AGENT_WHEEL, keep the original release URL/file accessible, "
                "or run apply from a directory containing the wheel (or ./dist)."
            )
            return None

        build_directory = tempfile.TemporaryDirectory(prefix="vpngw-agent-wheel-")
        atexit.register(build_directory.cleanup)
        dist_dir = Path(build_directory.name)

        # Always attempt to build latest wheel if pyproject is present
        if (project_root / "pyproject.toml").exists():
            print("[SSHPush] Building nebius-vpngw wheel package with python -m build...")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    env={**os.environ, "SOURCE_DATE_EPOCH": "315532800"},
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
        selected_wheel = self._select_wheel_from_dirs([dist_dir]) if dist_dir.exists() else None
        if selected_wheel:
            self._wheel_path = selected_wheel
            self._wheel_is_installed_fallback = False
            print(f"[SSHPush] Using wheel: {selected_wheel.name}")
            return selected_wheel

        install_wheel = self._wheel_from_install_metadata()
        if install_wheel and allow_installed_fallback:
            print(
                "[SSHPush] WARNING: Fresh build unavailable; falling back to the originally "
                "installed wheel. Local source changes will not be deployed."
            )
            self._wheel_path = install_wheel
            self._wheel_is_installed_fallback = True
            print(f"[SSHPush] Using fallback wheel: {self._wheel_path.name}")
            return self._wheel_path

        if dist_dir.exists():
            print("[SSHPush] No wheel found in dist/ after build attempt")
        else:
            print("[SSHPush] dist/ directory not found; wheel not built")
        return None

    def push_config_and_reload(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        staged_receipt: VMHAStageReceipt | None = None,
        runtime_binding: VMHARuntimeBinding | None = None,
        agent_artifact: VMHAAgentArtifact | None = None,
        replacement_policy_request: str | None = None,
        fail_closed: bool = False,
        ordinary_plan: Any = None,
        approved_disruption: bool = False,
        handoff: Any = None,
        apply_operation_id: str | None = None,
        initialize_checkpoint: bool = False,
    ) -> None:
        if staged_receipt is None:
            from .ordinary_apply import execute_plan

            if ordinary_plan is None:
                raise RuntimeError("Ordinary deployment requires a reviewed exact apply plan")
            execute_plan(self, ordinary_plan, local_cfg, approved=approved_disruption)
            return
        required_remote = staged_receipt is not None or fail_closed
        if staged_receipt is not None:
            if runtime_binding is None:
                raise ValueError("VM-HA activation requires its authoritative runtime binding")
            if agent_artifact is None:
                raise ValueError("VM-HA activation requires its approved agent artifact")
            agent_artifact.verify_current()
            from ..agent.main import vm_ha_runtime_blockers

            blockers = vm_ha_runtime_blockers()
            if blockers:
                raise RuntimeError(f"VM-HA activation BLOCKED: {', '.join(blockers)}")
        if not ssh_target:
            print(f"[SSHPush] No SSH target for instance {inst_cfg.instance_index}; skipping")
            return

        paramiko = self._ensure_paramiko()
        gg = local_cfg.get("gateway_group") or {}
        vm_spec = gg.get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")

        print(f"[SSHPush] Connecting to {ssh_target} as {username} ...")
        client = paramiko.SSHClient()
        try:
            try:
                configure_paramiko_host_verification(
                    client,
                    paramiko,
                    policy=self._ssh_policy,
                    hostname=inst_cfg.hostname if self._ssh_policy is not None else None,
                    transport_host=ssh_target if self._ssh_policy is not None else None,
                )
                self._connect_client(
                    client,
                    hostname=ssh_target,
                    username=username,
                    vm_spec=vm_spec,
                )
            except Exception as error:
                identity_failure = _host_identity_failure(error, paramiko, ssh_target)
                if identity_failure is not None:
                    raise identity_failure from error
                raise RuntimeError("VM-HA activation SSH connection failed") from error

            ingress_boot_id = ingress_ssh.preflight(client, inst_cfg.config_yaml)

            # Package bytes and service assets were proved during approved HA
            # preparation. Activation must reuse that exact artifact.
            print("[SSHPush] Reusing the verified VM-HA agent artifact...")

            # Upload to /tmp then move with sudo
            if staged_receipt is not None:
                assert runtime_binding is not None
                staged_binding = self._runtime_binding_for_nebius_credentials(
                    inst_cfg=inst_cfg,
                    runtime_binding=runtime_binding,
                    target=staged_receipt.nebius_credentials_path,
                    digest=staged_receipt.nebius_credentials_sha256,
                )
                rendered_config = self._render_vm_ha_config(inst_cfg, staged_binding)
                expected = self._vm_ha_receipt(
                    inst_cfg,
                    rendered_config,
                    runtime_binding=staged_binding,
                    nebius_credentials_path=staged_receipt.nebius_credentials_path,
                    nebius_credentials_sha256=staged_receipt.nebius_credentials_sha256,
                )
                if staged_receipt != expected:
                    client.close()
                    raise ValueError("VM-HA activation receipt does not match the node manifest")
                tmp_path = f"/etc/nebius-vpngw/vm-ha-staged/{staged_receipt.generation_id}.yaml"
            agent_cmd = ""  # The fenced HA controller exclusively owns agent activation.
            config_install_cmd = (
                f"sudo install -o root -g root -m 0600 {tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
                if staged_receipt is not None
                else f"sudo mv {tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
            )
            config_mode = "0600" if staged_receipt is not None else "0644"
            vm_ha_service_asset_commands = (
                ("sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf",)
                if staged_receipt is not None
                else ()
            )

            # Move into place and trigger reload
            if handoff is not None:
                if staged_receipt is None or apply_operation_id is None:
                    raise RuntimeError("HA handoff activation requires its exact apply lock")
                handoff.publish_activation(receipt=staged_receipt, operation_id=apply_operation_id)
            checkpoint_commands = []
            if handoff is None:
                if apply_operation_id is None or runtime_binding is None or agent_artifact is None:
                    raise RuntimeError("HA checkpoint activation requires its exact apply identity")
                from .checkpoint_conversion import context as checkpoint_context

                admission = {
                    "expected": checkpoint_context(
                        inst_cfg, apply_operation_id, cluster_id=runtime_binding.cluster_id
                    ),
                    "artifact": agent_artifact.sha256,
                    "boot_id": ingress_boot_id,
                    "unstarted": initialize_checkpoint,
                }
                checkpoint_commands.append(
                    "sudo -n /usr/bin/python3 -B -c "
                    + shlex.quote(
                        "from nebius_vpngw import checkpoint_upgrade as u,ha_repair as r;"
                        f"u.admit_activation(state_dir=r.STATE,repair=r,**{admission!r})"
                    )
                )
            ingress_activation = (
                "import os,yaml;from pathlib import Path;"
                "from nebius_vpngw import ingress_policy as p;"
                "policy=p.from_config(yaml.safe_load(Path('/etc/nebius-vpngw/config-resolved.yaml').read_text()));"
                "p.install(policy)"
            )
            cmds = [
                "sudo mkdir -p /etc/nebius-vpngw",
                *(
                    [self._vm_ha_staged_verify_command(staged_receipt)]
                    if staged_receipt is not None
                    else []
                ),
                *([] if handoff is not None else [config_install_cmd]),
                "sudo chown root:root /etc/nebius-vpngw/config-resolved.yaml",
                f"sudo chmod {config_mode} /etc/nebius-vpngw/config-resolved.yaml",
                "sudo -n /usr/bin/python3 -c " + shlex.quote(ingress_activation),
                *self._vm_ha_peer_firewall_commands(vm_ha=staged_receipt is not None),
                *vm_ha_service_asset_commands,
                "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
                "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
                "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
                "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo chmod 0755 /usr/local/bin/setup-vpngw-firewall.sh; fi",
                "if [ -f /usr/local/bin/nebius-vpngw-esp4-preflight.sh ]; then sudo chmod 0755 /usr/local/bin/nebius-vpngw-esp4-preflight.sh; fi",
                "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-agent.service",
                "sudo systemctl daemon-reload",
                "sudo /usr/bin/python3 -c "
                + shlex.quote(
                    "from nebius_vpngw.runtime_migration import enable_units; "
                    "enable_units(['nebius-vpngw-ingress.service'])"
                ),
                *self._vm_ha_reset_failed_commands(vm_ha=staged_receipt is not None),
                *checkpoint_commands,
                *(
                    [
                        "sudo install -o root -g root -m 0600 /dev/null /etc/nebius-vpngw/vm-ha-enabled",
                    ]
                    if staged_receipt is not None and handoff is None
                    else []
                ),
                "sudo /usr/bin/python3 -c "
                + shlex.quote(
                    "from nebius_vpngw import runtime_migration as m; import json,pathlib; "
                    "m.activate_ha(json.loads(pathlib.Path('/var/lib/nebius-vpngw/vm-ha/apply.lock').read_text()))"
                ),
                *(
                    self._vm_ha_control_service_commands(
                        initialize_policy=True,
                        replacement_policy_request=replacement_policy_request,
                    )
                    if staged_receipt is not None
                    else ()
                ),
                # Enable and start route fix timer (only if service file exists)
                "sudo /usr/bin/python3 -c "
                + shlex.quote(
                    "from nebius_vpngw.runtime_migration import enable_units; "
                    "enable_units(['nebius-vpngw-fix-routes.timer'])"
                ),
                "sudo systemctl start nebius-vpngw-fix-routes.timer",
                # Enable and start health monitoring service (only if service file exists)
                "sudo /usr/bin/python3 -c "
                + shlex.quote(
                    "from nebius_vpngw.runtime_migration import enable_units; "
                    "enable_units(['nebius-vpngw-health-monitor.service'])"
                ),
                "sudo systemctl start nebius-vpngw-health-monitor.service",
                *(
                    [
                        # Ordinary non-HA setup retains the established eager
                        # route/firewall path. VM-HA defers both until the
                        # controller has granted active authority.
                        'if python3 -c "import nebius_vpngw" >/dev/null 2>&1; then sudo /usr/bin/python3 -m nebius_vpngw.agent.fix_routes > /var/log/vpngw-fix-routes.log 2>&1 || true; fi',
                        "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo /usr/local/bin/setup-vpngw-firewall.sh > /var/log/vpngw-firewall-setup.log 2>&1 || true; fi",
                    ]
                    if staged_receipt is None
                    else []
                ),
                # In VM-HA the fenced controller exclusively owns ordinary-agent
                # activation after it has durably entered passive mode.
                *self._agent_activation_commands(
                    agent_cmd=agent_cmd,
                    vm_ha=staged_receipt is not None,
                ),
            ]
            had_failures = self._run_remote_commands(
                client,
                cmds,
                required_remote=required_remote,
            )

            if staged_receipt is not None:
                try:
                    self._wait_for_vm_ha_materialization(client)
                except Exception:
                    client.close()
                    raise

            if had_failures:
                raise RuntimeError("HA activation is incomplete; no rollback was performed")
            ingress_ssh.verify_fresh(
                self,
                ssh_target,
                inst_cfg,
                local_cfg,
                boot_id=ingress_boot_id,
                config_sha256=staged_receipt.staged_file_sha256
                if staged_receipt is not None
                else None,
            )
            if not had_failures:
                print("[SSHPush] HA configuration materialized and verified")

            # The caller owns exact fenced/native/heartbeat verification and
            # owner release. Advisory service, ping and BGP polling here would
            # delay both members while forwarding is deliberately fenced.
        finally:
            client.close()
