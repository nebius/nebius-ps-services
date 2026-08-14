"""Complete runtime ports for the explicitly enabled VM-HA controller."""

from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import ssl
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha.transport import (
    MutualTLSConfig,
    MutualTLSPeerTransport,
    PeerStateExchange,
    ReplayStateStore,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    DataPlaneMode,
    LocalReadiness,
)
from nebius_vpngw.agent.vm_ha_controller import (
    RouteReconciliationContext as ControllerRouteContext,
)
from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
from nebius_vpngw.deploy.vm_ha_cloud import (
    AllocationOwner,
    ClusterCloudObservation,
    InstanceCloudState,
    NebiusSDKCloudClient,
    VMHACloudAdapter,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    BGPRouteReadiness,
    LogicalStaticRouteManifest,
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    RouteMutation,
    RouteMutationKind,
    RouteOccupancySnapshot,
    RouteReconciliationContext,
    RouteReconciliationReceipt,
    RouteTransitionState,
    VerifiedAllocationOwnership,
    VMHARouteReconciler,
)
from nebius_vpngw.schema import (
    VMHARouteTarget,
    VMHARuntimeBinding,
    VMHARuntimeNodeBinding,
)


def _default_sdk_factory(*, credentials_file_name: str) -> Any:
    from nebius.sdk import SDK

    return SDK(credentials_file_name=credentials_file_name)


def _credential_file(path: str, name: str) -> Path:
    candidate = Path(path)
    try:
        stat = candidate.lstat()
        if (
            not candidate.is_absolute()
            or candidate.is_symlink()
            or not candidate.is_file()
            or stat.st_mode & 0o077
        ):
            raise ValueError
        with candidate.open("rb"):
            pass
    except (OSError, ValueError):
        raise ValueError(f"installed VM-HA {name} is not a private readable regular file") from None
    return candidate


_CREDENTIAL_LABELS = (
    "certificate_authority",
    "certificate",
    "private_key",
    "nebius_credentials",
)


@dataclass(frozen=True)
class InstalledCredentialBundle:
    """One immutable root-owned installed credential bundle."""

    node_id: str
    generation_id: str
    bundle_digest: str
    files: tuple[tuple[str, Path], ...]

    def revalidate(self) -> None:
        digests: list[tuple[str, str]] = []
        for label, path in self.files:
            ancestors = tuple(dict.fromkeys((path.parent, *path.parents)))
            try:
                install_root_index = ancestors.index(Path("/etc/nebius-vpngw"))
            except ValueError:
                raise ValueError(
                    "installed VM-HA credentials are outside the install root"
                ) from None
            for ancestor in ancestors[: install_root_index + 1]:
                metadata = ancestor.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != 0
                    or metadata.st_mode & 0o022
                ):
                    raise ValueError("installed VM-HA credential ancestors are not immutable")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != 0
                    or metadata.st_mode & 0o077
                    or metadata.st_nlink != 1
                ):
                    raise ValueError("installed VM-HA credential file is not immutable")
                digest = hashlib.sha256()
                while chunk := os.read(descriptor, 1024 * 1024):
                    digest.update(chunk)
            finally:
                os.close(descriptor)
            if path.lstat().st_ino != metadata.st_ino or path.lstat().st_dev != metadata.st_dev:
                raise ValueError("installed VM-HA credential identity changed during validation")
            digests.append((label, digest.hexdigest()))
        identity = "\n".join(f"{label}:{digest}" for label, digest in digests)
        if hashlib.sha256(identity.encode("ascii")).hexdigest() != self.bundle_digest:
            raise ValueError("installed VM-HA credential bundle digest mismatch")

        paths = dict(self.files)
        try:
            credentials = json.loads(paths["nebius_credentials"].read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("installed VM-HA Nebius credentials are malformed") from None
        if not isinstance(credentials, Mapping) or not credentials:
            raise ValueError("installed VM-HA Nebius credentials are malformed")
        try:
            context = ssl.create_default_context(
                ssl.Purpose.SERVER_AUTH,
                cafile=str(paths["certificate_authority"]),
            )
            context.load_cert_chain(str(paths["certificate"]), str(paths["private_key"]))
            decoded = ssl._ssl._test_decode_cert(str(paths["certificate"]))  # type: ignore[attr-defined]
        except (OSError, ssl.SSLError, ValueError):
            raise ValueError("installed VM-HA TLS credential bundle is invalid") from None
        now = datetime.now(timezone.utc).timestamp()
        if not (
            ssl.cert_time_to_seconds(str(decoded.get("notBefore")))
            <= now
            < ssl.cert_time_to_seconds(str(decoded.get("notAfter")))
        ):
            raise ValueError("installed VM-HA node certificate is outside its validity window")
        identities = {value for kind, value in decoded.get("subjectAltName", ()) if kind == "URI"}
        if identities != {f"urn:nebius-vpngw:node:{self.node_id}"}:
            raise ValueError("installed VM-HA node certificate identity is invalid")


class CredentialBundle(Protocol):
    def revalidate(self) -> None: ...


def validate_installed_credential_bundle(
    binding: VMHARuntimeBinding,
    local: VMHARuntimeNodeBinding,
) -> InstalledCredentialBundle:
    references = local.credentials
    files = tuple((label, Path(str(getattr(references, label)))) for label in _CREDENTIAL_LABELS)
    parents = {path.parent for _label, path in files}
    if len(parents) != 1:
        raise ValueError("installed VM-HA credentials do not share one bundle identity")
    parent = parents.pop()
    expected_names = {
        "certificate_authority": "ca.crt",
        "certificate": f"{local.node_id}.crt",
        "private_key": f"{local.node_id}.key",
        "nebius_credentials": "nebius-credentials.json",
    }
    if (
        parent.parent.name != local.node_id
        or parent.parent.parent.name != binding.generation_id
        or any(path.name != expected_names[label] for label, path in files)
    ):
        raise ValueError("installed VM-HA credentials have a non-canonical bundle path")
    bundle = InstalledCredentialBundle(
        node_id=local.node_id,
        generation_id=binding.generation_id,
        bundle_digest=parent.name,
        files=files,
    )
    if len(bundle.bundle_digest) != 64 or any(
        c not in "0123456789abcdef" for c in bundle.bundle_digest
    ):
        raise ValueError("installed VM-HA credential bundle identity is invalid")
    bundle.revalidate()
    return bundle


def _compute_state(value: InstanceCloudState) -> ComputeState:
    return ComputeState(value.value)


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class RenewableNebiusSDK:
    """Own exactly one renewable SDK and close its background resources."""

    def __init__(
        self,
        credentials_file_name: str,
        *,
        factory: Callable[..., Any] = _default_sdk_factory,
        credential_check: Callable[[], None] | None = None,
    ) -> None:
        credentials = _credential_file(credentials_file_name, "Nebius credentials")
        if credential_check is not None:
            credential_check()
        self.client = factory(credentials_file_name=str(credentials))
        if self.client is None or not callable(getattr(self.client, "sync_close", None)):
            raise RuntimeError("Nebius SDK does not provide the required synchronous lifecycle")
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.client.sync_close()

    def __enter__(self) -> RenewableNebiusSDK:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class BoundCloudRuntime:
    """Map the strict SDK adapter to controller observations and effects."""

    def __init__(
        self,
        binding: VMHARuntimeBinding,
        local: VMHARuntimeNodeBinding,
        peer: VMHARuntimeNodeBinding,
        adapter: VMHACloudAdapter,
    ) -> None:
        self.binding = binding
        self.local = local
        self.peer = peer
        self.adapter = adapter
        self._attach_started_revision: str | None = None
        self._confirmed_candidate_revision: str | None = None

    @property
    def allocation_id(self) -> str:
        return self.binding.shared_allocation_id

    @staticmethod
    def _owner(node: VMHARuntimeNodeBinding) -> AllocationOwner:
        return AllocationOwner(node.compute_id, node.network_interface_name)

    def _cluster(self) -> ClusterCloudObservation:
        return self.adapter.observe_cluster(
            allocation_id=self.allocation_id,
            former_owner=self._owner(self.peer),
            candidate=self._owner(self.local),
        )

    def observe(self) -> CloudObservation:
        observed = self._cluster()
        owner = observed.allocation.owner
        owner_id = None
        for node in self.binding.nodes:
            if owner == self._owner(node):
                owner_id = node.node_id
        if owner is not None and owner_id is None:
            raise RuntimeError("shared allocation has an unexpected Compute owner")
        # The candidate Compute revision is the transfer epoch both before and
        # after detachment.  That keeps every checkpoint on one resource and
        # lets attachment prove a strictly newer post-mutation revision.
        epoch = observed.candidate.resource_version
        exact = observed.candidate_attachment_exact
        if not exact:
            self._confirmed_candidate_revision = None
        elif self._attach_started_revision is None and self.local.role.value == "active":
            # The configured initial owner did not traverse a local transfer.
            self._confirmed_candidate_revision = epoch
        return CloudObservation(
            authoritative=True,
            allocation_id=self.allocation_id,
            observed_owner_node_id=owner_id,
            former_owner_node_id=self.peer.node_id,
            former_owner_compute_state=_compute_state(observed.former.state),
            former_attachment_absent=observed.former_attachment_absent,
            candidate_attachment_exact=exact,
            ownership_re_read_exact=bool(exact and self._confirmed_candidate_revision == epoch),
            ownership_epoch=epoch,
        )

    def stop_former(self, action: ControllerAction) -> None:
        observation = self.observe()
        if observation.former_owner_compute_state is ComputeState.STOPPED:
            return
        if not (
            observation.observed_owner_node_id == self.peer.node_id
            and observation.ownership_epoch == action.ownership_epoch
        ):
            raise RuntimeError("former-owner stop authority changed before the effect")
        self.adapter.require_stopped(self.peer.compute_id)

    def detach_former(self, action: ControllerAction) -> None:
        observation = self.observe()
        if observation.former_owner_compute_state is not ComputeState.STOPPED:
            raise RuntimeError("former owner is not STOPPED")
        if observation.former_attachment_absent:
            self.adapter.require_compute_attachment(
                self.allocation_id, self._owner(self.peer), present=False
            )
            return
        if not (
            observation.observed_owner_node_id == self.peer.node_id
            and observation.ownership_epoch == action.ownership_epoch
        ):
            raise RuntimeError("former-owner detach authority changed before the effect")
        self.adapter.require_former_attachment_absent(
            self.allocation_id, self._owner(self.peer), self._owner(self.local)
        )
        self.adapter.require_compute_attachment(
            self.allocation_id, self._owner(self.peer), present=False
        )

    def attach_candidate(self, action: ControllerAction) -> None:
        observation = self.observe()
        if observation.candidate_attachment_exact:
            self.adapter.require_compute_attachment(
                self.allocation_id, self._owner(self.local), present=True
            )
            return
        if not (
            observation.observed_owner_node_id is None
            and observation.former_owner_compute_state is ComputeState.STOPPED
            and observation.former_attachment_absent
        ):
            raise RuntimeError("candidate attach requires completed former-owner fencing")
        if observation.ownership_epoch != action.ownership_epoch:
            raise RuntimeError("candidate pre-attach revision changed before the effect")
        self._attach_started_revision = action.ownership_epoch
        self._confirmed_candidate_revision = None
        self.adapter.require_candidate_attachment(self.allocation_id, self._owner(self.local))
        self.adapter.require_compute_attachment(
            self.allocation_id, self._owner(self.local), present=True
        )

    def confirm_candidate(self) -> None:
        observed = self._cluster()
        revision = observed.candidate.resource_version
        if not (
            observed.former.state is InstanceCloudState.STOPPED
            and observed.former_attachment_absent
            and observed.candidate_attachment_exact
            and observed.allocation.owner == self._owner(self.local)
            and revision.isascii()
            and revision.isdecimal()
        ):
            raise RuntimeError("candidate ownership is not exact after former-owner fencing")
        self._confirmed_candidate_revision = revision


def build_cloud_runtime(
    binding: VMHARuntimeBinding,
    local_node_id: str,
    sdk: Any,
    *,
    attempts: int = 6,
    poll_interval: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> BoundCloudRuntime:
    nodes = {node.node_id: node for node in binding.nodes}
    if local_node_id not in nodes or len(nodes) != 2:
        raise ValueError("VM-HA runtime binding does not contain the local node")
    local = nodes[local_node_id]
    peer = next(node for node in binding.nodes if node.node_id != local_node_id)
    calls = NebiusSDKCloudClient(sdk)
    adapter = VMHACloudAdapter(
        instance_reader=calls.get_instance,
        instance_stopper=calls.stop_instance,
        allocation_reader=calls.get_allocation,
        allocation_setter=calls.set_allocation,
        attempts=attempts,
        poll_interval=poll_interval,
        sleeper=sleeper,
    )
    return BoundCloudRuntime(binding, local, peer, adapter)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""


CommandRunner = Callable[[Sequence[str], float], CommandResult]


def _run_command(argv: Sequence[str], timeout: float) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return CommandResult(completed.returncode, completed.stdout)


@dataclass(frozen=True)
class LocalDataPlaneObservation:
    service_healthy: bool
    forwarding_enabled: bool
    static_prefixes: frozenset[str]
    configured_bgp_sessions: frozenset[str]
    established_bgp_sessions: frozenset[str]
    learned_bgp_prefixes: frozenset[str]
    usable_xfrm_prefixes: frozenset[str]
    observed_bgp_policy_digest: str


@dataclass(frozen=True)
class DataPlaneCommandSet:
    """Absolute, argument-vector-only local commands used by the runtime."""

    systemctl: str = "/usr/bin/systemctl"
    sysctl: str = "/usr/sbin/sysctl"
    vtysh: str = "/usr/bin/vtysh"
    ip: str = "/usr/sbin/ip"
    swanctl: str = "/usr/sbin/swanctl"

    def __post_init__(self) -> None:
        if any(not Path(value).is_absolute() for value in self.__dict__.values()):
            raise ValueError("VM-HA local commands must use absolute executable paths")


class LocalDataPlanePort(Protocol):
    def observe(self) -> LocalDataPlaneObservation: ...

    def install_guard(self, action: ControllerAction) -> None: ...

    def enter_passive(self, action: ControllerAction) -> None: ...

    def disable_active(self, action: ControllerAction) -> None: ...

    def enable_active(self, action: ControllerAction) -> None: ...

    def mode(self) -> DataPlaneMode: ...


def _json_object(result: CommandResult, description: str) -> object:
    if result.returncode != 0:
        raise RuntimeError(f"unable to observe local {description}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"local {description} observation is malformed") from None


def _recursive_values(value: object, key_names: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in key_names and isinstance(item, (str, int)):
                found.add(str(item))
            found.update(_recursive_values(item, key_names))
    elif isinstance(value, list):
        for item in value:
            found.update(_recursive_values(item, key_names))
    return found


def _established_bgp_sessions(value: object) -> set[str]:
    established: set[str] = set()
    if isinstance(value, Mapping):
        state = next(
            (str(value[key]).lower() for key in ("state", "bgpState", "peerState") if key in value),
            "",
        )
        if state == "established":
            peer = next(
                (str(value[key]) for key in ("peer", "peerId", "neighbor") if key in value),
                "",
            )
            if peer:
                established.add(peer)
        for key, item in value.items():
            if isinstance(item, Mapping):
                nested_state = next(
                    (
                        str(item[state_key]).lower()
                        for state_key in ("state", "bgpState", "peerState")
                        if state_key in item
                    ),
                    "",
                )
                if nested_state == "established":
                    try:
                        ipaddress.ip_address(str(key))
                    except ValueError:
                        pass
                    else:
                        established.add(str(key))
            established.update(_established_bgp_sessions(item))
    elif isinstance(value, list):
        for item in value:
            established.update(_established_bgp_sessions(item))
    return established


def _learned_bgp_prefixes(value: object) -> set[str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("routes"), Mapping):
        return set()
    learned: set[str] = set()
    for prefix, paths in value["routes"].items():
        try:
            normalized = str(ipaddress.ip_network(str(prefix), strict=False))
        except ValueError:
            continue
        if isinstance(paths, list) and paths:
            learned.add(normalized)
    return learned


def _xfrm_if_ids(value: object) -> set[str]:
    return _recursive_values(value, frozenset({"if_id", "ifId"})) - {"0"}


def _usable_xfrm_devices(links: object, state: object, policies: object) -> set[str]:
    if not isinstance(links, list) or not isinstance(state, list) or not isinstance(policies, list):
        return set()
    active_if_ids = _xfrm_if_ids(state) & _xfrm_if_ids(policies)
    devices: set[str] = set()
    for link in links:
        if not isinstance(link, Mapping):
            continue
        if_name = link.get("ifname")
        link_info = link.get("linkinfo")
        if not isinstance(if_name, str) or not isinstance(link_info, Mapping):
            continue
        info_data = link_info.get("info_data")
        if not isinstance(info_data, Mapping):
            continue
        if_id = info_data.get("if_id", info_data.get("ifId"))
        if if_name.startswith("xfrm") and str(if_id) in active_if_ids:
            devices.add(if_name)
    return devices


def _policy_digest(value: Mapping[str, Iterable[str]]) -> str:
    normalized = {
        peer: sorted(str(ipaddress.ip_network(prefix, strict=True)) for prefix in prefixes)
        for peer, prefixes in sorted(value.items())
    }
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _observed_import_policy_digest(running_config: str, sessions: frozenset[str]) -> str:
    prefix_lists: dict[str, list[str]] = {}
    bindings: dict[str, str] = {}
    for raw_line in running_config.splitlines():
        line = raw_line.strip()
        match = re.fullmatch(r"ip prefix-list (\S+) seq \d+ permit (\S+)", line)
        if match:
            prefix_lists.setdefault(match.group(1), []).append(match.group(2))
            continue
        match = re.fullmatch(r"neighbor (\S+) prefix-list (\S+) in", line)
        if match:
            peer, list_name = match.groups()
            if peer in bindings and bindings[peer] != list_name:
                raise RuntimeError("FRR import policy has ambiguous neighbor bindings")
            bindings[peer] = list_name
    if set(bindings) - sessions:
        raise RuntimeError("FRR import policy contains an unexpected VM-HA neighbor")
    policy: dict[str, Iterable[str]] = {}
    for peer in sessions:
        list_name = bindings.get(peer)
        policy[peer] = () if list_name is None else prefix_lists.get(list_name, ())
        if list_name is not None and list_name not in prefix_lists:
            raise RuntimeError("FRR import policy references a missing prefix list")
    try:
        return _policy_digest(policy)
    except ValueError:
        raise RuntimeError("FRR import policy contains an invalid prefix") from None


class SystemDataPlaneRuntime:
    """Bounded command adapter for local service, FRR, XFRM, and forwarding truth."""

    def __init__(
        self,
        *,
        state_path: Path,
        guard_path: Path,
        configured_bgp_sessions: Iterable[str],
        expected_bgp_policy_digest: str,
        runner: CommandRunner = _run_command,
        command_timeout: float = 5.0,
        commands: DataPlaneCommandSet = DataPlaneCommandSet(),
        routing_lock_path: Path = Path("/run/nebius-vpngw/fix-routes.lock"),
    ) -> None:
        if not math.isfinite(command_timeout) or command_timeout <= 0:
            raise ValueError("local command timeout must be finite and positive")
        self.state_path = state_path
        self.guard_path = guard_path
        self.configured_bgp_sessions = frozenset(str(item) for item in configured_bgp_sessions)
        self.expected_bgp_policy_digest = expected_bgp_policy_digest
        self.runner = runner
        self.command_timeout = command_timeout
        self.commands = commands
        self.routing_lock_path = routing_lock_path

    def _write_guard(self, action: ControllerAction, mode: DataPlaneMode) -> None:
        _atomic_write_json(
            self.guard_path,
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "guard_boot_id": action.boot_id,
                "data_plane_mode": mode.value,
                "installed_at": time.time(),
            },
        )

    def _run(self, *argv: str) -> CommandResult:
        try:
            return self.runner(argv, self.command_timeout)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            raise RuntimeError("bounded local data-plane command failed") from None

    def _forwarding(self) -> bool:
        result = self._run(self.commands.sysctl, "-n", "net.ipv4.ip_forward")
        if result.returncode != 0 or result.stdout.strip() not in {"0", "1"}:
            raise RuntimeError("local forwarding state is unavailable")
        return result.stdout.strip() == "1"

    def observe(self) -> LocalDataPlaneObservation:
        strongswan_healthy = any(
            self._run(self.commands.systemctl, "is-active", "--quiet", service).returncode == 0
            for service in ("strongswan-starter", "strongswan")
        )
        services = (
            strongswan_healthy
            and self._run(self.commands.systemctl, "is-active", "--quiet", "frr").returncode == 0
        )
        summary = _json_object(
            self._run(self.commands.vtysh, "-c", "show bgp summary json"), "FRR session state"
        )
        rib = _json_object(
            self._run(self.commands.vtysh, "-c", "show bgp ipv4 unicast json"), "FRR RIB"
        )
        routes = _json_object(
            self._run(self.commands.ip, "-j", "route", "show", "table", "all"), "routes"
        )
        xfrm = _json_object(self._run(self.commands.ip, "-j", "xfrm", "state"), "XFRM state")
        xfrm_policies = _json_object(
            self._run(self.commands.ip, "-j", "xfrm", "policy"), "XFRM policy"
        )
        xfrm_links = _json_object(
            self._run(self.commands.ip, "-j", "link", "show", "type", "xfrm"),
            "XFRM interfaces",
        )
        running_config = self._run(self.commands.vtysh, "-c", "show running-config")
        if running_config.returncode != 0:
            raise RuntimeError("unable to observe current FRR import policy")
        established = _established_bgp_sessions(summary)
        learned = _learned_bgp_prefixes(rib)
        static_prefixes: set[str] = set()
        usable_xfrm: set[str] = set()
        usable_xfrm_devices = _usable_xfrm_devices(xfrm_links, xfrm, xfrm_policies)
        if isinstance(routes, list):
            for route in routes:
                if not isinstance(route, Mapping):
                    continue
                prefix = route.get("dst")
                device = route.get("dev")
                if isinstance(prefix, str):
                    static_prefixes.add(prefix)
                    if isinstance(device, str) and device in usable_xfrm_devices:
                        usable_xfrm.add(prefix)
        return LocalDataPlaneObservation(
            service_healthy=services,
            forwarding_enabled=self._forwarding(),
            static_prefixes=frozenset(static_prefixes),
            configured_bgp_sessions=self.configured_bgp_sessions,
            established_bgp_sessions=frozenset(established),
            learned_bgp_prefixes=frozenset(learned),
            usable_xfrm_prefixes=frozenset(usable_xfrm),
            observed_bgp_policy_digest=_observed_import_policy_digest(
                running_config.stdout, self.configured_bgp_sessions
            ),
        )

    def _set_mode(self, action: ControllerAction, mode: DataPlaneMode) -> None:
        self.routing_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.routing_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            self._set_mode_locked(action, mode)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _set_mode_locked(self, action: ControllerAction, mode: DataPlaneMode) -> None:
        forwarding = "1" if mode is DataPlaneMode.ACTIVE else "0"
        if mode is not DataPlaneMode.ACTIVE:
            # Revoke the shared readiness authority before touching kernel or
            # tunnel state so no concurrent writer can re-enable forwarding.
            self._write_guard(action, DataPlaneMode.BLOCKED)
        else:
            _atomic_write_json(
                self.state_path,
                {
                    "boot_id": action.boot_id,
                    "mode": DataPlaneMode.BLOCKED.value,
                    "operation_id": action.operation_id,
                    "schema": "nebius-vpngw/vm-ha-data-plane-v1",
                },
            )
        result = self._run(self.commands.sysctl, "-w", f"net.ipv4.ip_forward={forwarding}")
        if result.returncode != 0 or self._forwarding() is (mode is not DataPlaneMode.ACTIVE):
            raise RuntimeError("local forwarding postcondition was not observed")
        if mode is DataPlaneMode.BLOCKED:
            strongswan_running = any(
                self._run(self.commands.systemctl, "is-active", "--quiet", service).returncode == 0
                for service in ("strongswan-starter", "strongswan")
            )
            if strongswan_running:
                terminate = self._run(
                    self.commands.swanctl, "--terminate", "--ike", "all", "--timeout", "5"
                )
                if terminate.returncode != 0:
                    raise RuntimeError("cluster tunnel initiation could not be disabled")
                unload = self._run(self.commands.swanctl, "--unload-conns")
                if unload.returncode != 0:
                    raise RuntimeError("cluster tunnel start actions could not be disabled")
        elif mode is DataPlaneMode.PASSIVE:
            strongswan_running = any(
                self._run(self.commands.systemctl, "is-active", "--quiet", service).returncode == 0
                for service in ("strongswan-starter", "strongswan")
            )
            if strongswan_running:
                load = self._run(self.commands.swanctl, "--load-all", "--noprompt")
                if load.returncode != 0:
                    raise RuntimeError("cluster tunnel configuration could not be loaded")
        try:
            _atomic_write_json(
                self.state_path,
                {
                    "boot_id": action.boot_id,
                    "mode": mode.value,
                    "operation_id": action.operation_id,
                    "schema": "nebius-vpngw/vm-ha-data-plane-v1",
                },
            )
            self._write_guard(action, mode)
        except Exception:
            if mode is DataPlaneMode.ACTIVE:
                self._write_guard(action, DataPlaneMode.BLOCKED)
                rollback = self._run(self.commands.sysctl, "-w", "net.ipv4.ip_forward=0")
                if rollback.returncode != 0 or self._forwarding():
                    raise RuntimeError(
                        "active authority persistence failed and forwarding rollback was not verified"
                    ) from None
            raise

    def install_guard(self, action: ControllerAction) -> None:
        self._set_mode(action, DataPlaneMode.BLOCKED)

    def enter_passive(self, action: ControllerAction) -> None:
        self._set_mode(action, DataPlaneMode.PASSIVE)

    def disable_active(self, action: ControllerAction) -> None:
        self._set_mode(action, DataPlaneMode.BLOCKED)

    def enable_active(self, action: ControllerAction) -> None:
        self._set_mode(action, DataPlaneMode.ACTIVE)

    def mode(self) -> DataPlaneMode:
        forwarding = self._forwarding()
        if not self.state_path.exists():
            if forwarding:
                raise RuntimeError("forwarding is enabled without durable VM-HA authority")
            return DataPlaneMode.BLOCKED
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping) or value.get("schema") != (
                "nebius-vpngw/vm-ha-data-plane-v1"
            ):
                raise ValueError
            mode = DataPlaneMode(str(value.get("mode")))
            if forwarding != (mode is DataPlaneMode.ACTIVE):
                raise ValueError
            return mode
        except (OSError, ValueError, json.JSONDecodeError):
            if forwarding:
                raise RuntimeError("forwarding conflicts with durable VM-HA authority") from None
            return DataPlaneMode.BLOCKED


class RouteBackend(Protocol):
    def verify_target(self, target: VMHARouteTarget) -> None: ...

    def list_routes(
        self, target: VMHARouteTarget, ownership: Mapping[str, ManagedRouteOwnership]
    ) -> tuple[ManagedRouteSnapshot | RouteOccupancySnapshot, ...]: ...

    def apply_mutation(self, mutation: RouteMutation) -> str | None: ...

    def recover_created_route(self, mutation: RouteMutation) -> str | None: ...


class RuntimeStateStore:
    """Small atomic store for route ledger, transition, and exact receipt state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ledger_path = root / "route-ledger.json"
        self.transition_path = root / "route-transition.json"
        self.receipt_path = root / "route-receipt.json"
        self.pending_path = root / "route-pending.json"

    @staticmethod
    def _read(path: Path) -> Mapping[str, object] | None:
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("VM-HA runtime state is malformed")
        return value

    def load_ledger(self) -> dict[str, ManagedRouteOwnership]:
        value = self._read(self.ledger_path)
        if value is None:
            return {}
        if value.get("schema") != "nebius-vpngw/vm-ha-route-ledger-v1":
            raise ValueError("VM-HA route ledger schema is invalid")
        routes = value.get("routes")
        if not isinstance(routes, Mapping):
            raise ValueError("VM-HA route ledger is malformed")
        result: dict[str, ManagedRouteOwnership] = {}
        for route_id, record in routes.items():
            if not isinstance(route_id, str) or not isinstance(record, Mapping):
                raise ValueError("VM-HA route ledger entry is malformed")
            result[route_id] = ManagedRouteOwnership(
                cluster_id=str(record.get("cluster_id") or ""),
                kind=ManagedRouteKind(str(record.get("kind"))),
                route_target=VMHARouteTarget.model_validate(record.get("route_target")),
            )
        return result

    def save_ledger(self, ledger: Mapping[str, ManagedRouteOwnership]) -> None:
        _atomic_write_json(
            self.ledger_path,
            {
                "routes": {
                    route_id: {
                        "cluster_id": owner.cluster_id,
                        "kind": owner.kind.value,
                        "route_target": owner.route_target.model_dump(mode="json"),
                    }
                    for route_id, owner in sorted(ledger.items())
                },
                "schema": "nebius-vpngw/vm-ha-route-ledger-v1",
            },
        )

    def load_transition(self, *, now: float) -> RouteTransitionState:
        value = self._read(self.transition_path)
        if value is None:
            return RouteTransitionState(now)
        if value.get("schema") != "nebius-vpngw/vm-ha-route-transition-v1":
            raise ValueError("VM-HA route transition schema is invalid")
        counts = value.get("absent_bgp_observations")
        takeover_started_at = value.get("takeover_started_at")
        if (
            not isinstance(counts, list)
            or isinstance(takeover_started_at, bool)
            or not isinstance(takeover_started_at, (int, float))
            or not math.isfinite(takeover_started_at)
        ):
            raise ValueError("VM-HA route transition counts are invalid")
        return RouteTransitionState(
            takeover_started_at=float(takeover_started_at),
            absent_bgp_observations=tuple((str(item[0]), int(item[1])) for item in counts),
        )

    def save_transition(self, state: RouteTransitionState) -> None:
        _atomic_write_json(
            self.transition_path,
            {
                "absent_bgp_observations": [list(item) for item in state.absent_bgp_observations],
                "schema": "nebius-vpngw/vm-ha-route-transition-v1",
                "takeover_started_at": state.takeover_started_at,
            },
        )

    def save_route_reconciliation_receipt(self, receipt: Mapping[str, object]) -> None:
        _atomic_write_json(self.receipt_path, receipt)

    def load_route_reconciliation_receipt(self) -> Mapping[str, object] | None:
        return self._read(self.receipt_path)

    def save_pending_mutation(
        self, mutation: RouteMutation, context: RouteReconciliationContext
    ) -> None:
        _atomic_write_json(
            self.pending_path,
            {
                "context": context.to_dict(),
                "mutation": {
                    "allocation_id": mutation.allocation_id,
                    "cluster_id": mutation.cluster_id,
                    "kind": mutation.kind.value,
                    "prefix": mutation.prefix,
                    "route_id": mutation.route_id,
                    "route_kind": mutation.route_kind.value,
                    "route_target": mutation.route_target.model_dump(mode="json"),
                },
                "schema": "nebius-vpngw/vm-ha-route-mutation-intent-v1",
            },
        )

    def load_pending_mutation(
        self,
    ) -> tuple[RouteMutation, RouteReconciliationContext] | None:
        value = self._read(self.pending_path)
        if value is None:
            return None
        if set(value) != {"context", "mutation", "schema"} or value.get("schema") != (
            "nebius-vpngw/vm-ha-route-mutation-intent-v1"
        ):
            raise ValueError("VM-HA route mutation intent is malformed")
        mutation = value.get("mutation")
        if not isinstance(mutation, Mapping) or set(mutation) != {
            "allocation_id",
            "cluster_id",
            "kind",
            "prefix",
            "route_id",
            "route_kind",
            "route_target",
        }:
            raise ValueError("VM-HA route mutation intent is malformed")
        route_id = mutation["route_id"]
        if route_id is not None and not isinstance(route_id, str):
            raise ValueError("VM-HA route mutation intent route identity is invalid")
        return (
            RouteMutation(
                kind=RouteMutationKind(str(mutation["kind"])),
                prefix=str(mutation["prefix"]),
                route_kind=ManagedRouteKind(str(mutation["route_kind"])),
                allocation_id=str(mutation["allocation_id"]),
                cluster_id=str(mutation["cluster_id"]),
                route_target=VMHARouteTarget.model_validate(mutation["route_target"]),
                route_id=route_id,
            ),
            RouteReconciliationContext.from_mapping(value["context"]),
        )


class BoundRouteRuntime:
    def __init__(
        self,
        *,
        binding: VMHARuntimeBinding,
        local_node_id: str,
        cloud: BoundCloudRuntime,
        data_plane: LocalDataPlanePort,
        backend: RouteBackend,
        store: RuntimeStateStore,
        static_manifest_json: str,
        bgp_policy_json: str,
        expected_import_policy_digest: str,
        clock: Callable[[], float] = time.time,
        takeover_hold_down_seconds: float = 30.0,
        withdrawal_stability_observations: int = 3,
    ) -> None:
        if hashlib.sha256(bgp_policy_json.encode()).hexdigest() != binding.bgp_policy_digest:
            raise ValueError("Committed BGP policy manifest digest mismatch")
        self.binding = binding
        self.local_node_id = local_node_id
        self.cloud = cloud
        self.data_plane = data_plane
        self.backend = backend
        self.store = store
        self.static_manifest = LogicalStaticRouteManifest.from_committed_json(
            static_manifest_json, expected_digest=binding.static_routes_digest
        )
        self.bgp_policies = json.loads(bgp_policy_json)
        if not isinstance(self.bgp_policies, list):
            raise ValueError("Committed BGP policy manifest must be a list")
        self.expected_import_policy_digest = expected_import_policy_digest
        self.clock = clock
        self.reconciler = VMHARouteReconciler(
            cluster_id=binding.cluster_id,
            node_id=local_node_id,
            takeover_hold_down_seconds=takeover_hold_down_seconds,
            withdrawal_stability_observations=withdrawal_stability_observations,
            route_targets=binding.route_targets,
        )

    def _bgp(self, observed: LocalDataPlaneObservation) -> BGPRouteReadiness:
        if not self.bgp_policies:
            return BGPRouteReadiness.normalize(
                configured_sessions=("not-required",),
                established_sessions=("not-required",),
                required_prefixes=(),
                learned_prefixes=(),
                usable_xfrm_prefixes=(),
                observed_import_policy_digest=observed.observed_bgp_policy_digest,
                committed_import_policy_digest=self.expected_import_policy_digest,
            )
        required: set[str] = set()
        for policy in self.bgp_policies:
            if not isinstance(policy, Mapping) or not isinstance(
                policy.get("remote_prefixes"), list
            ):
                raise ValueError("Committed BGP policy record is invalid")
            required.update(str(prefix) for prefix in policy["remote_prefixes"])
        return BGPRouteReadiness.normalize(
            configured_sessions=observed.configured_bgp_sessions,
            established_sessions=observed.established_bgp_sessions,
            required_prefixes=required,
            learned_prefixes=observed.learned_bgp_prefixes,
            usable_xfrm_prefixes=observed.usable_xfrm_prefixes,
            observed_import_policy_digest=observed.observed_bgp_policy_digest,
            committed_import_policy_digest=self.expected_import_policy_digest,
        )

    def readiness(self) -> LocalReadiness:
        observed = self.data_plane.observe()
        bgp = self._bgp(observed)
        static_ready = self.static_manifest.prefixes.issubset(observed.usable_xfrm_prefixes)
        required_xfrm_prefixes = self.static_manifest.prefixes | bgp.required_prefixes
        return LocalReadiness(
            service_healthy=observed.service_healthy,
            static_ready=static_ready,
            bgp_ready=bgp.promotion_ready,
            xfrm_ready=required_xfrm_prefixes.issubset(observed.usable_xfrm_prefixes),
        )

    def _ownership(self, *, require_takeover_fence: bool) -> VerifiedAllocationOwnership:
        observed = self.cloud.observe()
        exact_local = observed.local_attachment_exact(self.local_node_id)
        fenced = observed.transfer_complete(self.local_node_id)
        if not exact_local or (require_takeover_fence and not fenced):
            raise RuntimeError("route reconciliation requires exact current ownership authority")
        return VerifiedAllocationOwnership(
            cluster_id=self.binding.cluster_id,
            candidate_node_id=self.local_node_id,
            observed_owner_node_id=self.local_node_id,
            allocation_id=observed.allocation_id,
            ownership_epoch=observed.ownership_epoch,
        )

    def _routes(self, ledger: Mapping[str, ManagedRouteOwnership]):
        routes: list[ManagedRouteSnapshot | RouteOccupancySnapshot] = []
        for target in self.binding.route_targets:
            self.backend.verify_target(target)
            routes.extend(self.backend.list_routes(target, ledger))
        return tuple(routes)

    def _plan(self, ownership: VerifiedAllocationOwnership, state: RouteTransitionState):
        observed = self.data_plane.observe()
        return self.reconciler.plan(
            ownership=ownership,
            static_manifest=self.static_manifest,
            bgp=self._bgp(observed),
            existing_routes=self._routes(self.store.load_ledger()),
            state=state,
            now=self.clock(),
        )

    @staticmethod
    def _context(action: ControllerAction, cluster_id: str) -> RouteReconciliationContext:
        return RouteReconciliationContext(
            operation_id=action.operation_id,
            cluster_id=cluster_id,
            owner_node_id=action.target_node_id,
            allocation_id=action.allocation_id,
            ownership_epoch=action.ownership_epoch,
            generation_id=action.generation_id,
            configuration_digest=action.digests.configuration,
            static_routes_digest=action.digests.static_routes,
            bgp_policy_digest=action.digests.bgp_policy,
            ownership_incarnation=action.ownership_incarnation,
        )

    def reconcile(self, action: ControllerAction) -> None:
        ownership = self._ownership(require_takeover_fence=action.ownership_incarnation > 0)
        context = self._context(action, self.binding.cluster_id)
        if not context.matches(ownership):
            raise RuntimeError("route action does not match fresh current ownership")
        ledger = self.store.load_ledger()
        pending = self.store.load_pending_mutation()
        if pending is not None:
            pending_mutation, pending_context = pending
            if not (
                pending_context.has_same_authority(context)
                and pending_mutation.cluster_id == self.binding.cluster_id
                and pending_mutation.allocation_id == self.binding.shared_allocation_id
                and pending_mutation.route_target in self.binding.route_targets
            ):
                raise RuntimeError(
                    "pending route mutation does not match the current controller authority"
                )
            self._apply_mutation(pending_mutation, ledger)

        state = self.store.load_transition(now=self.clock())
        plan = self._plan(ownership, state)

        def apply(mutation: RouteMutation) -> None:
            self.store.save_pending_mutation(mutation, context)
            self._apply_mutation(mutation, ledger)

        result = NebiusSDKRouteBackend.execute_verified_plan(
            plan,
            context=context,
            apply_mutation=apply,
            reobserve_ownership=lambda: self._ownership(
                require_takeover_fence=action.ownership_incarnation > 0
            ),
            reobserve_plan=lambda: self._plan(
                self._ownership(require_takeover_fence=action.ownership_incarnation > 0),
                state,
            ),
            receipt_store=self.store,
        )
        if result.receipt is None or result.committed_state is None:
            raise RuntimeError("route reconciliation did not produce an exact durable receipt")
        self.store.save_transition(result.committed_state)

    def _apply_mutation(
        self, mutation: RouteMutation, ledger: dict[str, ManagedRouteOwnership]
    ) -> None:
        pending = self.store.load_pending_mutation()
        if pending is None or pending[0] != mutation:
            raise RuntimeError("route mutation lacks an exact durable pending intent")
        if mutation.route_id:
            recorded = ledger.get(mutation.route_id)
            if recorded is not None and (
                recorded.cluster_id != mutation.cluster_id
                or recorded.route_target != mutation.route_target
                or (
                    mutation.kind is RouteMutationKind.DELETE
                    and recorded.kind is not mutation.route_kind
                )
            ):
                raise RuntimeError("route mutation lacks exact durable management authority")
        self.backend.verify_target(mutation.route_target)
        route_id = self.backend.apply_mutation(mutation)
        self.backend.verify_target(mutation.route_target)
        if mutation.kind is RouteMutationKind.DELETE:
            if mutation.route_id:
                ledger.pop(mutation.route_id, None)
        else:
            route_id = route_id or self.backend.recover_created_route(mutation)
            if not route_id:
                raise RuntimeError("route mutation did not expose an exact route identity")
            if mutation.route_id and mutation.route_id != route_id:
                ledger.pop(mutation.route_id, None)
            ledger[route_id] = ManagedRouteOwnership(
                cluster_id=mutation.cluster_id,
                kind=mutation.route_kind,
                route_target=mutation.route_target,
            )
        self.store.save_ledger(ledger)
        self.store.pending_path.unlink(missing_ok=True)

    def receipt_context(self) -> ControllerRouteContext | None:
        value = self.store.load_route_reconciliation_receipt()
        if value is None:
            return None
        receipt = RouteReconciliationReceipt.from_mapping(value)
        ownership = self._ownership(
            require_takeover_fence=receipt.context.ownership_incarnation > 0
        )
        state = self.store.load_transition(now=self.clock())
        current_plan = self._plan(ownership, state)
        if (
            current_plan.blocked_reasons
            or current_plan.mutations
            or not receipt.context.matches(ownership)
            or receipt.plan_digest != self._plan_digest(current_plan)
        ):
            return None
        context = receipt.context
        return ControllerRouteContext(
            owner_node_id=context.owner_node_id,
            allocation_id=context.allocation_id,
            ownership_epoch=context.ownership_epoch,
            generation_id=context.generation_id,
            digests=DigestSet(
                context.configuration_digest,
                context.static_routes_digest,
                context.bgp_policy_digest,
            ),
            route_runtime_id=self.binding.route_runtime_id,
            ownership_incarnation=context.ownership_incarnation,
            operation_id=context.operation_id,
        )

    @staticmethod
    def _plan_digest(plan: object) -> str:
        from nebius_vpngw.deploy.vm_ha_routes import _route_plan_digest

        return _route_plan_digest(plan)  # type: ignore[arg-type]


class BoundPeerRuntime:
    """Explicit service-loop peer port; no hidden thread or ownership claim."""

    def __init__(self, exchange: PeerStateExchange, *, clock: Callable[[], float]) -> None:
        self.exchange = exchange
        self.clock = clock
        self.latest_heartbeat: PeerHeartbeat | None = None
        self.received_at: float | None = None

    def poll(self, *, timeout_seconds: float) -> PeerHeartbeat:
        heartbeat, _replay = self.exchange.receive(timeout_seconds=timeout_seconds)
        self.latest_heartbeat = heartbeat
        self.received_at = self.clock()
        return heartbeat

    def send(self, heartbeat: PeerHeartbeat) -> None:
        self.exchange.send(heartbeat)

    def observe(self) -> tuple[PeerHeartbeat | None, float | None]:
        return self.latest_heartbeat, self.received_at


@dataclass(frozen=True)
class VMHARuntimePorts:
    """Complete providers and handlers consumed by the activation task."""

    binding: VMHARuntimeBinding
    local: VMHARuntimeNodeBinding
    peer: VMHARuntimeNodeBinding
    sdk: RenewableNebiusSDK
    cloud: BoundCloudRuntime
    data_plane: LocalDataPlanePort
    routes: BoundRouteRuntime
    peer_runtime: BoundPeerRuntime
    credential_bundle: CredentialBundle

    def providers(self) -> dict[str, Callable[..., object]]:
        return {
            "peer": self.peer_runtime.observe,
            "readiness": self.routes.readiness,
            "cloud": self.cloud.observe,
            "data_plane": self.data_plane.mode,
            "routes": self.routes.receipt_context,
        }

    def _require_action_identity(self, action: ControllerAction) -> None:
        if not (
            action.allocation_id == self.binding.shared_allocation_id
            and action.generation_id == self.binding.generation_id
            and action.digests
            == DigestSet(
                self.binding.configuration_digest,
                self.binding.static_routes_digest,
                self.binding.bgp_policy_digest,
            )
        ):
            raise RuntimeError("controller action does not match the installed runtime binding")

    def handlers(self) -> dict[ActionKind, Callable[[ControllerAction], None]]:
        former_actions = {
            ActionKind.STOP_FORMER_OWNER,
            ActionKind.DETACH_FORMER_ATTACHMENT,
        }

        def checked(expected_kind: ActionKind, handler: Callable[[ControllerAction], None]):
            def apply(action: ControllerAction) -> None:
                expected_target = (
                    self.peer.node_id if expected_kind in former_actions else self.local.node_id
                )
                if action.kind is not expected_kind or action.target_node_id != expected_target:
                    raise RuntimeError("controller action has the wrong kind or target identity")
                self._require_action_identity(action)
                handler(action)

            return apply

        def enable(action: ControllerAction) -> None:
            self.credential_bundle.revalidate()
            if not self.cloud.observe().local_attachment_exact(self.local.node_id):
                raise RuntimeError("active forwarding requires exact current allocation ownership")
            if not self.routes.readiness().promotion_ready:
                raise RuntimeError("active forwarding requires fresh local readiness")
            expected = BoundRouteRuntime._context(action, self.binding.cluster_id)
            current_receipt = self.routes.receipt_context()
            if current_receipt is None:
                raise RuntimeError("active forwarding requires the exact current route receipt")
            if not current_receipt.operation_id or current_receipt != ControllerRouteContext(
                owner_node_id=expected.owner_node_id,
                allocation_id=expected.allocation_id,
                ownership_epoch=expected.ownership_epoch,
                generation_id=expected.generation_id,
                digests=DigestSet(
                    expected.configuration_digest,
                    expected.static_routes_digest,
                    expected.bgp_policy_digest,
                ),
                route_runtime_id=self.binding.route_runtime_id,
                ownership_incarnation=expected.ownership_incarnation,
                operation_id=current_receipt.operation_id,
            ):
                raise RuntimeError("active forwarding requires the exact current route receipt")
            self.data_plane.enable_active(action)

        def confirm(action: ControllerAction) -> None:
            before = self.cloud.observe()
            if before.ownership_epoch != action.ownership_epoch:
                raise RuntimeError("candidate ownership revision changed before confirmation")
            self.cloud.confirm_candidate()
            after = self.cloud.observe()
            if not (
                after.ownership_epoch == action.ownership_epoch
                and after.transfer_complete(self.local.node_id)
            ):
                raise RuntimeError("candidate ownership is not exact after confirmation")

        return {
            ActionKind.INSTALL_COLD_START_GUARD: checked(
                ActionKind.INSTALL_COLD_START_GUARD, self.data_plane.install_guard
            ),
            ActionKind.ENTER_PASSIVE: checked(
                ActionKind.ENTER_PASSIVE, self.data_plane.enter_passive
            ),
            ActionKind.DISABLE_ACTIVE: checked(
                ActionKind.DISABLE_ACTIVE, self.data_plane.disable_active
            ),
            ActionKind.STOP_FORMER_OWNER: checked(
                ActionKind.STOP_FORMER_OWNER, self.cloud.stop_former
            ),
            ActionKind.DETACH_FORMER_ATTACHMENT: checked(
                ActionKind.DETACH_FORMER_ATTACHMENT, self.cloud.detach_former
            ),
            ActionKind.ATTACH_CANDIDATE: checked(
                ActionKind.ATTACH_CANDIDATE,
                self.cloud.attach_candidate,
            ),
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP: checked(
                ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
                confirm,
            ),
            ActionKind.RECONCILE_ROUTES: checked(
                ActionKind.RECONCILE_ROUTES, self.routes.reconcile
            ),
            ActionKind.ENABLE_ACTIVE: checked(ActionKind.ENABLE_ACTIVE, enable),
        }

    def heartbeat(self, *, boot_id: str, sequence: int, clock: float) -> PeerHeartbeat:
        """Build one secret-free advisory heartbeat from fresh local truth."""

        cloud = self.cloud.observe()
        readiness = self.routes.readiness()
        return PeerHeartbeat(
            cluster_id=self.binding.cluster_id,
            node_id=self.local.node_id,
            boot_id=boot_id,
            sequence=sequence,
            sent_at=datetime.fromtimestamp(clock, timezone.utc).isoformat().replace("+00:00", "Z"),
            configured_role=self.local.role.value,
            observed_owner_id=cloud.observed_owner_node_id,
            generation_id=self.binding.generation_id,
            digests=DigestSet(
                self.binding.configuration_digest,
                self.binding.static_routes_digest,
                self.binding.bgp_policy_digest,
            ),
            service_healthy=readiness.service_healthy,
            route_ready=readiness.promotion_ready,
            promotion_ready=bool(
                readiness.promotion_ready and cloud.local_attachment_exact(self.local.node_id)
            ),
        )

    def install_shutdown_guard(self, *, boot_id: str) -> None:
        """Restore the local fail-closed data plane without cloud authority."""

        action = ControllerAction(
            kind=ActionKind.INSTALL_COLD_START_GUARD,
            operation_id=f"{boot_id}:shutdown-guard:{self.local.node_id}",
            boot_id=boot_id,
            target_node_id=self.local.node_id,
            allocation_id=self.binding.shared_allocation_id,
            ownership_epoch="shutdown",
            generation_id=self.binding.generation_id,
            digests=DigestSet(
                self.binding.configuration_digest,
                self.binding.static_routes_digest,
                self.binding.bgp_policy_digest,
            ),
        )
        self.handlers()[ActionKind.INSTALL_COLD_START_GUARD](action)

    def close(self) -> None:
        self.sdk.close()


def _split_endpoint(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise ValueError("VM-HA peer endpoint must contain host and port")
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError("VM-HA peer endpoint port is invalid") from None
    if not 1 <= port <= 65535:
        raise ValueError("VM-HA peer endpoint port is invalid")
    return host, port


def _configured_bgp_sessions(
    config: Mapping[str, object], bgp_records: list[object]
) -> frozenset[str]:
    expected_connections = {
        str(record.get("connection"))
        for record in bgp_records
        if isinstance(record, Mapping) and record.get("connection")
    }
    if not expected_connections:
        return frozenset()
    connections = config.get("connections")
    if not isinstance(connections, list):
        raise ValueError("VM-HA BGP runtime requires resolved node connections")
    found_connections: set[str] = set()
    sessions: set[str] = set()
    for connection in connections:
        if not isinstance(connection, Mapping):
            raise ValueError("VM-HA resolved connection is malformed")
        connection_name = str(connection.get("name") or "")
        if connection_name not in expected_connections:
            continue
        found_connections.add(connection_name)
        tunnels = connection.get("tunnels")
        if not isinstance(tunnels, list):
            raise ValueError("VM-HA BGP connection has no resolved tunnels")
        for tunnel in tunnels:
            if not isinstance(tunnel, Mapping):
                raise ValueError("VM-HA BGP tunnel is malformed")
            peer = str(tunnel.get("inner_remote_ip") or "")
            if peer:
                try:
                    ipaddress.ip_address(peer)
                except ValueError:
                    raise ValueError("VM-HA BGP peer address is invalid") from None
                sessions.add(peer)
    if found_connections != expected_connections or not sessions:
        raise ValueError("VM-HA BGP manifest does not match resolved node sessions")
    return frozenset(sessions)


def _expected_import_policy_digest(config: Mapping[str, object], bgp_records: list[object]) -> str:
    expected_by_connection = {
        str(record.get("connection")): tuple(record.get("remote_prefixes") or ())
        for record in bgp_records
        if isinstance(record, Mapping) and record.get("connection")
    }
    policy: dict[str, Iterable[str]] = {}
    connections = config.get("connections")
    if not isinstance(connections, list):
        connections = []
    for connection in connections:
        if not isinstance(connection, Mapping):
            raise ValueError("VM-HA resolved connection is malformed")
        connection_name = str(connection.get("name") or "")
        if connection_name not in expected_by_connection:
            continue
        tunnels = connection.get("tunnels")
        if not isinstance(tunnels, list):
            raise ValueError("VM-HA BGP connection has no resolved tunnels")
        for tunnel in tunnels:
            if not isinstance(tunnel, Mapping):
                raise ValueError("VM-HA BGP tunnel is malformed")
            peer = str(tunnel.get("inner_remote_ip") or "")
            if peer:
                policy[peer] = expected_by_connection[connection_name]
    return _policy_digest(policy)


def build_runtime_ports(
    config: Mapping[str, object],
    *,
    state_dir: Path,
    replay_store: ReplayStateStore,
    sdk_factory: Callable[..., Any] = _default_sdk_factory,
    route_backend_factory: Callable[[Any], RouteBackend] = NebiusSDKRouteBackend,
    data_plane_factory: Callable[..., LocalDataPlanePort] = SystemDataPlaneRuntime,
    credential_bundle_factory: Callable[
        [VMHARuntimeBinding, VMHARuntimeNodeBinding], CredentialBundle
    ] = validate_installed_credential_bundle,
    runner: CommandRunner = _run_command,
    clock: Callable[[], float] = time.time,
) -> VMHARuntimePorts:
    """Construct every runtime port without connecting it to the default service."""

    binding = VMHARuntimeBinding.model_validate(config.get("runtime_binding"))
    node = config.get("node")
    generation = config.get("generation")
    if not isinstance(node, Mapping) or not isinstance(generation, Mapping):
        raise ValueError("VM-HA node and generation are required")
    local_node_id = str(node.get("node_id") or "")
    nodes = {item.node_id: item for item in binding.nodes}
    if local_node_id not in nodes or len(nodes) != 2:
        raise ValueError("VM-HA runtime binding does not identify the local node")
    local = nodes[local_node_id]
    peer = next(item for item in binding.nodes if item.node_id != local_node_id)
    digests = generation.get("digests")
    readiness = config.get("readiness")
    expected_digests = {
        "configuration": binding.configuration_digest,
        "static_routes": binding.static_routes_digest,
        "bgp_policy": binding.bgp_policy_digest,
    }
    if not (
        config.get("cluster_id") == binding.cluster_id
        and node.get("role") == local.role.value
        and generation.get("generation_id") == binding.generation_id
        and isinstance(digests, Mapping)
        and dict(digests) == expected_digests
        and isinstance(readiness, Mapping)
        and readiness.get("generation_id") == binding.generation_id
        and isinstance(readiness.get("digests"), Mapping)
        and dict(readiness["digests"]) == expected_digests
        and set(readiness.get("required_node_ids") or ()) == set(nodes)
    ):
        raise ValueError("VM-HA runtime binding does not match the installed node generation")
    manifests = generation.get("logical_manifests")
    if not isinstance(manifests, Mapping):
        raise ValueError("VM-HA committed logical manifests are required")
    static_json = str(manifests.get("static_routes_json") or "")
    bgp_json = str(manifests.get("bgp_policy_json") or "")
    bgp_records = json.loads(bgp_json)
    if not isinstance(bgp_records, list):
        raise ValueError("VM-HA BGP policy manifest must be a list")
    configured_sessions = _configured_bgp_sessions(config, bgp_records)
    expected_import_policy_digest = _expected_import_policy_digest(config, bgp_records)
    credential_bundle = credential_bundle_factory(binding, local)
    sdk = RenewableNebiusSDK(
        local.credentials.nebius_credentials,
        factory=sdk_factory,
        credential_check=credential_bundle.revalidate,
    )
    try:
        cloud = build_cloud_runtime(binding, local_node_id, sdk.client)
        data_plane = data_plane_factory(
            state_path=state_dir / "data-plane.json",
            guard_path=state_dir / "guard.json",
            configured_bgp_sessions=configured_sessions,
            expected_bgp_policy_digest=binding.bgp_policy_digest,
            runner=runner,
        )
        backend = route_backend_factory(sdk.client)
        routes = BoundRouteRuntime(
            binding=binding,
            local_node_id=local_node_id,
            cloud=cloud,
            data_plane=data_plane,
            backend=backend,
            store=RuntimeStateStore(state_dir),
            static_manifest_json=static_json,
            bgp_policy_json=bgp_json,
            expected_import_policy_digest=expected_import_policy_digest,
            clock=clock,
        )
        peer_host, peer_port = _split_endpoint(peer.peer_endpoint)
        local_host, local_port = _split_endpoint(local.peer_endpoint)
        tls = MutualTLSConfig(
            certificate_authority=_credential_file(
                local.credentials.certificate_authority, "peer certificate authority"
            ),
            certificate=_credential_file(local.credentials.certificate, "peer certificate"),
            private_key=_credential_file(local.credentials.private_key, "peer private key"),
            server_hostname=peer_host,
            credential_check=credential_bundle.revalidate,
        )
        exchange = PeerStateExchange(
            MutualTLSPeerTransport(
                tls,
                peer_host=peer_host,
                peer_port=peer_port,
                listen_host=local_host,
                listen_port=local_port,
                expected_peer_node_id=peer.node_id,
            ),
            cluster_id=binding.cluster_id,
            peer_node_id=peer.node_id,
            replay_store=replay_store,
            max_heartbeat_age_seconds=30.0,
            wall_clock=clock,
        )
        ports = VMHARuntimePorts(
            binding=binding,
            local=local,
            peer=peer,
            sdk=sdk,
            cloud=cloud,
            data_plane=data_plane,
            routes=routes,
            peer_runtime=BoundPeerRuntime(exchange, clock=clock),
            credential_bundle=credential_bundle,
        )
        return ports
    except Exception:
        sdk.close()
        raise
