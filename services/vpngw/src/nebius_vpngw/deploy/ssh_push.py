from __future__ import annotations

import ast
import base64
import csv
import errno
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.parser import BytesParser
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname, urlopen

import yaml

from ..agent.vm_ha.inhibition import (
    LIVE_PEER_REPLACEMENT_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
    STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
)
from ..agent.vm_ha.mtls import MTLSReceipt, PeerLeaf
from ..agent.vm_ha.mtls_actions import ACTION_NAMES, ACTION_SCHEMA, encode_action_request
from ..agent.vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY
from ..config_loader import InstanceResolvedConfig
from ..schema import VMHARuntimeBinding
from ..vm_ha_credentials import (
    VMHACredentialIdentityError,
    credential_ids_from_payload,
    installed_vm_ha_credential_path,
    read_vm_ha_credential_source,
)
from .ssh_client_auth import SSHClientAuth, resolve_ssh_client_auth
from .ssh_policy import SSHTrustPolicy, configure_paramiko_host_verification
from .vm_ha_identity import LegacyVMHAIdentity, parse_legacy_vm_ha_identity

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


class VMHAStandbyReplacementNotReady(RuntimeError):
    """The owner has not yet acknowledged its exact replacement inhibition."""


_VM_HA_SERVICE_ASSET_DESTINATIONS = (
    ("nebius-vpngw-agent.service", "/etc/systemd/system/nebius-vpngw-agent.service", 0o644),
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
        "/etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf",
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
                        limit=256 * 1024,
                    ),
                    f"{dist_info}/WHEEL": cls._read_wheel_member(
                        wheel,
                        f"{dist_info}/WHEEL",
                        limit=64 * 1024,
                    ),
                }
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
                LIVE_PEER_REPLACEMENT_CAPABILITY,
                STANDBY_RESTORATION_CAPABILITY,
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

    def __init__(self, ssh_policy: SSHTrustPolicy | None = None) -> None:
        # Lazy import to avoid hard dependency when running dry-run
        self._paramiko = None
        self._wheel_path: Path | None = None
        self._wheel_is_installed_fallback = False
        self._temp_wheel_dir: Path | None = None
        self._ssh_policy = ssh_policy
        self._ssh_client_auth: SSHClientAuth | None = None
        self._ssh_client_auth_input: tuple[str, str | None] | None = None

    def _connect_client(
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
        if public_key:
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
            "sudo systemctl enable nebius-vpngw-vm-ha-guard.service "
            "nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-rearm.service",
            "sudo systemctl restart nebius-vpngw-vm-ha-guard.service",
            "sudo systemctl restart nebius-vpngw-vm-ha.service",
            "sudo systemctl restart nebius-vpngw-vm-ha-rearm.service",
            "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-guard.service",
            "sudo systemctl is-active --quiet nebius-vpngw-vm-ha.service",
            "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-rearm.service",
        )

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

        script = (
            f"assets = {_VM_HA_SERVICE_ASSET_DESTINATIONS!r}\n"
            + """\
import os
import pathlib
import sys
import tempfile
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    for name, destination_text, mode in assets:
        payload = archive.read(f"nebius_vpngw/systemd/{name}")
        destination = pathlib.Path(destination_text)
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
                    timeout=20,
                )
                safe_stdout = cls._read_bounded_remote_output(stdout)
                safe_stderr = cls._read_bounded_remote_output(stderr)
                return_code = stdout.channel.recv_exit_status()
            except Exception as error:
                if required_remote:
                    raise RuntimeError(
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
                raise RuntimeError(failure)
            had_failures = True
        return had_failures

    @staticmethod
    def _wait_for_vm_ha_materialization(
        client: Any,
        *,
        attempts: int = 12,
        interval_seconds: float = 5.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Wait for the controller-owned passive materialization predicate."""

        if attempts < 1:
            raise ValueError("VM-HA materialization attempts must be positive")
        command = "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-materialized"
        for attempt in range(1, attempts + 1):
            stdin, stdout, stderr = client.exec_command(command, get_pty=True, timeout=20)
            rc = stdout.channel.recv_exit_status()
            stdout.read()
            stderr.read()
            if rc == 0:
                return
            if attempt < attempts:
                sleeper(interval_seconds)
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
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode("utf-8")
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
                "--vm-ha-auto-healing-quiescent && "
                f"sudo install -o root -g root -m 0600 {temporary} {pending} && "
                f"sudo test \"$(sudo stat -c '%U:%G:%a' {pending})\" = root:root:600 && "
                f"echo '{receipt.record_sha256}  {pending}' | "
                "sudo sha256sum --check --status && "
                f"if sudo test -e {destination}; then "
                f"echo '{receipt.record_sha256}  {destination}' | "
                "sudo sha256sum --check --status; fi && "
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
                f"if sudo test -e {destination}; then "
                f"echo '{receipt.record_sha256}  {destination}' | "
                "sudo sha256sum --check --status; fi && "
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
            stdin, stdout, stderr = client.exec_command(command, timeout=60)
            return_code = stdout.channel.recv_exit_status()
            if return_code != 0:
                if agent_flag == "--vm-ha-standby-replacement-ready" and return_code == 75:
                    raise VMHAStandbyReplacementNotReady(
                        "VM-HA standby replacement inhibition is not controller-quiescent"
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
    ) -> dict[str, object]:
        """Install and prove the exact agent plus its crypto dependencies."""

        if not ssh_target or self._ssh_policy is None:
            raise ValueError("VM-HA package preparation requires exact-pinned SSH")
        if artifact is None:
            wheel_path = self._build_wheel(allow_installed_fallback=False)
            if wheel_path is None or not wheel_path.is_file():
                raise RuntimeError("VM-HA package preparation requires a deployable agent wheel")
            artifact = VMHAAgentArtifact.from_wheel(wheel_path, source="direct-apply-build")
        wheel_path = artifact.path
        wheel_sha256 = artifact.sha256
        remote_directory: str | None = None
        remote_wheel: str | None = None
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
            _stdin, stdout, _stderr = client.exec_command(
                "umask 077 && mktemp -d /tmp/nebius-vpngw-agent.XXXXXXXXXX",
                timeout=10,
            )
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("VM-HA agent package staging directory creation failed")
            remote_directory = stdout.read().decode("ascii").strip()
            if re.fullmatch(r"/tmp/nebius-vpngw-agent\.[A-Za-z0-9]{10}", remote_directory) is None:
                raise RuntimeError("VM-HA agent package staging directory was invalid")
            remote_wheel = f"{remote_directory}/{wheel_path.name}"
            with artifact.open_verified() as wheel_stream, client.open_sftp() as sftp:
                sftp.putfo(
                    wheel_stream,
                    remote_wheel,
                    file_size=os.fstat(wheel_stream.fileno()).st_size,
                    confirm=True,
                )
            verify_wheel_command = (
                f"echo {shlex.quote(f'{wheel_sha256}  {remote_wheel}')} "
                "| sha256sum --check --status"
            )
            _stdin, stdout, _stderr = client.exec_command(verify_wheel_command, timeout=30)
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("VM-HA agent package bytes did not match the approved artifact")
            install_command = (
                "sudo /usr/bin/python3 -m pip install --upgrade --force-reinstall "
                "--ignore-installed "
                f"--break-system-packages {shlex.quote(remote_wheel)}"
            )
            _stdin, stdout, stderr = client.exec_command(install_command, get_pty=True, timeout=180)
            safe_stdout = self._read_bounded_remote_output(stdout)
            safe_stderr = self._read_bounded_remote_output(stderr)
            if stdout.channel.recv_exit_status() != 0:
                failure_class = self._remote_failure_class(safe_stdout, safe_stderr)
                raise RuntimeError(f"VM-HA agent package installation failed ({failure_class})")
            verification = (
                "import cffi,cryptography,importlib.metadata as m,json,nebius_vpngw;"
                "from cryptography.hazmat.primitives.asymmetric import ec;"
                "print(json.dumps({'schema':'nebius-vpngw/vm-ha-package-v1',"
                "'package_version':m.version('nebius-vpngw'),"
                "'cryptography_version':cryptography.__version__,"
                "'cffi_version':cffi.__version__},sort_keys=True,separators=(',',':')))"
            )
            verify_command = f"/usr/bin/python3 -c {shlex.quote(verification)}"
            _stdin, stdout, _stderr = client.exec_command(verify_command, timeout=30)
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("VM-HA agent package verification failed")
            try:
                receipt = json.loads(stdout.read().decode("ascii"))
            except (UnicodeError, json.JSONDecodeError):
                raise RuntimeError("VM-HA agent package verification was malformed") from None
            if not (
                isinstance(receipt, dict)
                and set(receipt)
                == {
                    "schema",
                    "package_version",
                    "cryptography_version",
                    "cffi_version",
                }
                and receipt.get("schema") == "nebius-vpngw/vm-ha-package-v1"
                and all(
                    isinstance(receipt.get(name), str) and bool(receipt[name])
                    for name in (
                        "package_version",
                        "cryptography_version",
                        "cffi_version",
                    )
                )
            ):
                raise RuntimeError("VM-HA agent package verification was invalid")
            capability_command = (
                "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities"
            )
            _stdin, stdout, _stderr = client.exec_command(capability_command, timeout=30)
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("VM-HA agent capability verification failed")
            try:
                capability = json.loads(stdout.read().decode("ascii"))
            except (UnicodeError, json.JSONDecodeError):
                raise RuntimeError("VM-HA agent capability verification was malformed") from None
            features = capability.get("features") if isinstance(capability, dict) else None
            if not (
                isinstance(capability, dict)
                and capability.get("schema") == "nebius-vpngw.agent-capabilities.v1"
                and isinstance(features, list)
                and all(isinstance(feature, str) for feature in features)
                and STANDBY_RESTORATION_CAPABILITY in features
            ):
                raise RuntimeError("VM-HA agent capability verification was invalid")
            if not set(artifact.capabilities).issubset(features):
                raise RuntimeError(
                    "VM-HA agent capability verification did not match the approved artifact"
                )
            asset_command = self._install_vm_ha_assets_from_artifact_command(remote_wheel)
            _stdin, stdout, stderr = client.exec_command(
                asset_command,
                get_pty=True,
                timeout=60,
            )
            safe_stdout = self._read_bounded_remote_output(stdout)
            safe_stderr = self._read_bounded_remote_output(stderr)
            if stdout.channel.recv_exit_status() != 0:
                failure_class = self._remote_failure_class(safe_stdout, safe_stderr)
                raise RuntimeError(f"VM-HA service asset installation failed ({failure_class})")
            receipt["artifact_sha256"] = wheel_sha256
            receipt["capabilities"] = features
            return receipt
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            if remote_wheel is not None and remote_directory is not None:
                try:
                    cleanup = (
                        f"rm -f {shlex.quote(remote_wheel)} && "
                        f"rmdir {shlex.quote(remote_directory)}"
                    )
                    client.exec_command(cleanup, timeout=10)
                except Exception:
                    pass
            client.close()

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
            except VMHAStandbyReplacementNotReady:
                if final_probe_due:
                    raise VMHAStandbyReplacementNotReady(
                        "VM-HA standby replacement inhibition did not become "
                        "controller-quiescent within the bounded wait"
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
        """Stop both mutation writers after every member is inhibited."""

        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        client = paramiko.SSHClient()
        command = """sudo /bin/bash -lc '
set -eu
systemctl stop nebius-vpngw-vm-ha-rearm.service
systemctl stop nebius-vpngw-vm-ha.service
if systemctl is-active --quiet nebius-vpngw-vm-ha-rearm.service; then exit 47; fi
if systemctl is-active --quiet nebius-vpngw-vm-ha.service; then exit 48; fi
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
            stdin, stdout, stderr = client.exec_command(command, timeout=90)
            return_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8").splitlines()
            if return_code != 0 or "VM_HA_MUTATION_SERVICES_STOPPED=1" not in output:
                raise RuntimeError("VM-HA mutation services did not stop for removal")
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
                    for old_wheel in old_wheels:
                        old_wheel.unlink()

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
    ) -> None:
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
        except Exception as e:
            error_msg = str(e).lower()
            print(f"[SSHPush] SSH connect failed to {ssh_target}: {e}")

            identity_failure = _host_identity_failure(e, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from e

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
            if required_remote:
                raise RuntimeError(
                    f"{'VM-HA activation' if staged_receipt is not None else 'required'} "
                    f"SSH connection failed for {ssh_target}"
                ) from e
            return

        restart_agent = False

        # VM-HA package preparation already installed and verified the exact
        # approval-bound artifact plus its service assets. Activation must not
        # select or rebuild different bytes after that proof.
        if staged_receipt is not None:
            print("[SSHPush] Reusing the verified VM-HA agent artifact...")
            wheel_path = None
            restart_agent = True
        else:
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
                        if required_remote:
                            raise RuntimeError("VM-HA package reinstall verification failed")
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
                        if required_remote:
                            raise RuntimeError("VM-HA installed package import check failed")
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
                            health_monitor_service = (
                                systemd_dir / "nebius-vpngw-health-monitor.service"
                            )
                            if health_monitor_service.exists():
                                with sftp.file(
                                    "/tmp/nebius-vpngw-health-monitor.service", "w"
                                ) as f:
                                    f.write(health_monitor_service.read_text())
                                print("[SSHPush] Staged health monitoring service")

                            firewall_script = systemd_dir / "setup-vpngw-firewall.sh"
                            if firewall_script.exists():
                                with sftp.file("/tmp/setup-vpngw-firewall.sh", "w") as f:
                                    f.write(firewall_script.read_text())
                                print("[SSHPush] Staged firewall setup script")

                            vm_ha_peer_firewall_script = (
                                systemd_dir / "nebius-vpngw-vm-ha-peer-firewall.sh"
                            )
                            if staged_receipt is not None and vm_ha_peer_firewall_script.exists():
                                with sftp.file(
                                    "/tmp/nebius-vpngw-vm-ha-peer-firewall.sh", "w"
                                ) as f:
                                    f.write(vm_ha_peer_firewall_script.read_text())
                                print("[SSHPush] Staged VM-HA peer firewall helper")

                            esp4_preflight_script = systemd_dir / "nebius-vpngw-esp4-preflight.sh"
                            if esp4_preflight_script.exists():
                                with sftp.file("/tmp/nebius-vpngw-esp4-preflight.sh", "w") as f:
                                    f.write(esp4_preflight_script.read_text())
                                print("[SSHPush] Staged ESP4 preflight helper")

                            if staged_receipt is not None:
                                ufw_lock_tmpfiles = systemd_dir / "nebius-vpngw-ufw-lock.conf"
                                if ufw_lock_tmpfiles.exists():
                                    with sftp.file("/tmp/nebius-vpngw-ufw-lock.conf", "w") as f:
                                        f.write(ufw_lock_tmpfiles.read_text())
                                    print("[SSHPush] Staged VM-HA UFW lock tmpfiles policy")
                    except Exception as e:
                        if required_remote:
                            raise RuntimeError("VM-HA systemd asset staging failed") from e
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
                    if required_remote:
                        raise RuntimeError("VM-HA package installation failed")
                    print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
            except Exception as e:
                if required_remote:
                    raise RuntimeError("VM-HA package deployment failed") from e
                print(f"[SSHPush] Failed to deploy package: {e}")
                print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
        elif staged_receipt is None:
            if required_remote:
                raise RuntimeError("VM-HA activation requires a deployable agent wheel")
            print("[SSHPush] WARNING: Could not build wheel, skipping package deployment")

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
        else:
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
        config_install_cmd = (
            f"sudo install -o root -g root -m 0600 {tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
            if staged_receipt is not None
            else f"sudo mv {tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
        )
        config_mode = "0600" if staged_receipt is not None else "0644"
        legacy_service_asset_commands = (
            (
                # Ordinary non-HA deployment still stages service assets in /tmp.
                "if [ -f /tmp/nebius-vpngw-fix-routes.service ]; then sudo mv /tmp/nebius-vpngw-fix-routes.service /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
                "if [ -f /tmp/nebius-vpngw-fix-routes.timer ]; then sudo mv /tmp/nebius-vpngw-fix-routes.timer /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
                "if [ -f /tmp/nebius-vpngw-health-monitor.service ]; then sudo mv /tmp/nebius-vpngw-health-monitor.service /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
                "if [ -f /tmp/setup-vpngw-firewall.sh ]; then sudo mv /tmp/setup-vpngw-firewall.sh /usr/local/bin/setup-vpngw-firewall.sh; fi",
                "if [ -f /tmp/nebius-vpngw-esp4-preflight.sh ]; then sudo mv /tmp/nebius-vpngw-esp4-preflight.sh /usr/local/bin/nebius-vpngw-esp4-preflight.sh; fi",
                "if [ -f /tmp/nebius-vpngw-agent.service ]; then sudo mv /tmp/nebius-vpngw-agent.service /etc/systemd/system/nebius-vpngw-agent.service; fi",
            )
            if staged_receipt is None
            else ()
        )
        vm_ha_service_asset_commands = (
            ("sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf",)
            if staged_receipt is not None
            else ()
        )

        # Move into place and trigger reload
        cmds = [
            "sudo mkdir -p /etc/nebius-vpngw",
            *(
                [self._vm_ha_staged_verify_command(staged_receipt)]
                if staged_receipt is not None
                else []
            ),
            config_install_cmd,
            "sudo chown root:root /etc/nebius-vpngw/config-resolved.yaml",
            f"sudo chmod {config_mode} /etc/nebius-vpngw/config-resolved.yaml",
            *self._vm_ha_peer_firewall_commands(vm_ha=staged_receipt is not None),
            *legacy_service_asset_commands,
            *vm_ha_service_asset_commands,
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
            "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo chmod 0755 /usr/local/bin/setup-vpngw-firewall.sh; fi",
            "if [ -f /usr/local/bin/nebius-vpngw-esp4-preflight.sh ]; then sudo chmod 0755 /usr/local/bin/nebius-vpngw-esp4-preflight.sh; fi",
            "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-agent.service",
            "sudo systemctl daemon-reload",
            *self._vm_ha_reset_failed_commands(vm_ha=staged_receipt is not None),
            *(
                [
                    "sudo install -o root -g root -m 0600 /dev/null /etc/nebius-vpngw/vm-ha-enabled",
                ]
                if staged_receipt is not None
                else []
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
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo systemctl enable --now nebius-vpngw-fix-routes.timer; fi",
            # Enable and start health monitoring service (only if service file exists)
            "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo systemctl enable --now nebius-vpngw-health-monitor.service; fi",
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
                    'if ! dpkg -l frr 2>/dev/null | grep -q "^ii"; then '
                    "command -v curl >/dev/null 2>&1 || (apt-get update && apt-get install -y curl); "
                    "if [ ! -f /etc/apt/sources.list.d/frr.list ]; then "
                    "curl -s https://deb.frrouting.org/frr/keys.asc | tee /usr/share/keyrings/frrouting.asc > /dev/null; "
                    "UBUNTU_CODENAME=$(lsb_release -cs); "
                    'echo "deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr $UBUNTU_CODENAME frr-stable" > /etc/apt/sources.list.d/frr.list; '
                    "fi; "
                    "apt-get update; "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y frr frr-pythontools; "
                    "fi'"
                )
                stdin, stdout, stderr = client.exec_command(install_cmd, timeout=300, get_pty=True)
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
                stdin, stdout, stderr = client.exec_command(install_cmd, timeout=300, get_pty=True)
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
            svc_status = "unknown"
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
                    last_states: dict[str, str] = {}

                    while (time.time() - start_time) < max_wait_time:
                        cmd = "sudo vtysh -c 'show bgp summary json' 2>/dev/null || echo '{}'"
                        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                        output = stdout.read().decode().strip()

                        try:
                            bgp_summary = json.loads(output) if output != "{}" else {}
                            ipv4_peers = bgp_summary.get("ipv4Unicast", {}).get("peers", {})

                            established_count = 0
                            current_states: dict[str, str] = {}

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
