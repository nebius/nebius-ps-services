"""Complete runtime ports for the explicitly enabled VM-HA controller."""

from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from nebius_vpngw.agent.routing_guard import (
    has_table_220_rule,
    table_220_routes_from_all_json,
)
from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore, MTLSSnapshot
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
    VMHACloudOperationJournal,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    AcceptedRouteOperation,
    BGPRouteReadiness,
    LogicalStaticRouteManifest,
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    PendingRouteMutation,
    RouteMutation,
    RouteMutationKind,
    RouteMutationPhase,
    RouteOccupancySnapshot,
    RouteReconciliationContext,
    RouteReconciliationReceipt,
    RouteReplacementCompensated,
    RouteRollbackSnapshot,
    RouteTransitionState,
    VerifiedAllocationOwnership,
    VMHARouteReconciler,
)
from nebius_vpngw.schema import (
    VMHAMigrationRouteBinding,
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


@dataclass(frozen=True)
class InstalledCredentialBundle:
    """One immutable root-owned installed Nebius credential file."""

    node_id: str
    generation_id: str
    bundle_digest: str
    path: Path

    def revalidate(self) -> None:
        ancestors = tuple(dict.fromkeys((self.path.parent, *self.path.parents)))
        try:
            install_root_index = ancestors.index(Path("/etc/nebius-vpngw"))
        except ValueError:
            raise ValueError("installed VM-HA credentials are outside the install root") from None
        for ancestor in ancestors[: install_root_index + 1]:
            metadata = ancestor.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_mode & 0o022
            ):
                raise ValueError("installed VM-HA credential ancestors are not immutable")
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_mode & 0o077
                or metadata.st_nlink != 1
            ):
                raise ValueError("installed VM-HA Nebius credential file is not immutable")
            digest = hashlib.sha256()
            payload = bytearray()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                payload.extend(chunk)
        finally:
            os.close(descriptor)
        identity = self.path.lstat()
        if identity.st_ino != metadata.st_ino or identity.st_dev != metadata.st_dev:
            raise ValueError("installed VM-HA credential identity changed during validation")
        if digest.hexdigest() != self.bundle_digest:
            raise ValueError("installed VM-HA credential digest mismatch")
        try:
            credentials = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("installed VM-HA Nebius credentials are malformed") from None
        if not isinstance(credentials, Mapping) or not credentials:
            raise ValueError("installed VM-HA Nebius credentials are malformed")


class CredentialBundle(Protocol):
    def revalidate(self) -> None: ...


def validate_installed_credential_bundle(
    binding: VMHARuntimeBinding,
    local: VMHARuntimeNodeBinding,
) -> InstalledCredentialBundle:
    path = Path(local.nebius_credentials_path)
    parent = path.parent
    if (
        path.name != "nebius-credentials.json"
        or parent.parent.name != local.node_id
        or parent.parent.parent.name != binding.generation_id
    ):
        raise ValueError("installed VM-HA Nebius credentials have a non-canonical path")
    bundle = InstalledCredentialBundle(
        node_id=local.node_id,
        generation_id=binding.generation_id,
        bundle_digest=parent.name,
        path=path,
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


def _durably_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


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
            ownership_re_read_exact=exact,
            ownership_epoch=epoch,
            former_attachment_exact=observed.former_attachment_exact,
            candidate_attachment_absent=observed.candidate_attachment_absent,
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
        self.adapter.require_stopped(self.peer.compute_id, action.operation_id)

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
            self.allocation_id,
            self._owner(self.peer),
            self._owner(self.local),
            action.operation_id,
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
        self.adapter.require_candidate_attachment(
            self.allocation_id,
            self._owner(self.local),
            action.operation_id,
        )
        self.adapter.require_compute_attachment(
            self.allocation_id, self._owner(self.local), present=True
        )

    def detach_candidate_for_reproof(self, action: ControllerAction) -> None:
        observation = self.observe()
        if not (
            observation.former_owner_compute_state is ComputeState.STOPPED
            and observation.former_attachment_absent
            and observation.candidate_attachment_exact
            and observation.observed_owner_node_id == self.local.node_id
        ):
            raise RuntimeError("candidate detach reproof requires exact fenced ownership")
        self.adapter.require_former_attachment_absent(
            self.allocation_id,
            self._owner(self.local),
            self._owner(self.peer),
            action.operation_id,
        )
        self.adapter.require_compute_attachment(
            self.allocation_id,
            self._owner(self.local),
            present=False,
        )

    def confirm_candidate(self, action: ControllerAction) -> None:
        observed = self._cluster()
        revision = observed.candidate.resource_version
        if not (
            observed.former.state is InstanceCloudState.STOPPED
            and observed.former_attachment_absent
            and observed.candidate_attachment_exact
            and observed.allocation.owner == self._owner(self.local)
            and revision.isascii()
            and revision.isdecimal()
            and revision == action.ownership_epoch
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
    operation_journal: VMHACloudOperationJournal | None = None,
) -> BoundCloudRuntime:
    nodes = {node.node_id: node for node in binding.nodes}
    if local_node_id not in nodes or len(nodes) != 2:
        raise ValueError("VM-HA runtime binding does not contain the local node")
    local = nodes[local_node_id]
    peer = next(node for node in binding.nodes if node.node_id != local_node_id)
    calls = NebiusSDKCloudClient(sdk, operation_journal=operation_journal)
    adapter = VMHACloudAdapter(
        instance_reader=calls.get_instance,
        instance_stopper=calls.stop_instance,
        allocation_reader=calls.get_allocation,
        alias_allocation_setter=calls.set_alias_allocation,
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


class BGPExportState(str, Enum):
    """Result of comparing complete per-peer Adj-RIB-Out evidence."""

    MATCH = "match"
    DRIFT = "drift"
    UNKNOWN = "unknown"


def _routing_hygiene_ready(
    rules: CommandResult,
    all_routes: CommandResult,
    broad_apipa: CommandResult,
) -> bool:
    return (
        rules.returncode == 0
        and not has_table_220_rule(rules.stdout)
        and all_routes.returncode == 0
        and table_220_routes_from_all_json(all_routes.stdout) == []
        and broad_apipa.returncode == 0
        and not broad_apipa.stdout.strip()
    )


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
    established_ike_sa_count: int = 0
    bgp_export_state: BGPExportState = BGPExportState.UNKNOWN
    routing_hygiene_ready: bool = True


@dataclass(frozen=True)
class DataPlaneCommandSet:
    """Absolute, argument-vector-only local commands used by the runtime."""

    systemctl: str = "/usr/bin/systemctl"
    sysctl: str = "/usr/sbin/sysctl"
    vtysh: str = "/usr/bin/vtysh"
    ip: str = "/usr/sbin/ip"
    swanctl: str = "/usr/sbin/swanctl"
    python: str = "/usr/bin/python3"
    empty_swanctl_config: str = "/dev/null"

    def __post_init__(self) -> None:
        if any(not Path(value).is_absolute() for value in self.__dict__.values()):
            raise ValueError("VM-HA local commands must use absolute executable paths")


class LocalDataPlanePort(Protocol):
    def observe(self) -> LocalDataPlaneObservation: ...

    def install_guard(self, action: ControllerAction) -> None: ...

    def enter_passive(self, action: ControllerAction) -> None: ...

    def disable_active(self, action: ControllerAction) -> None: ...

    def repair_local(self, action: ControllerAction) -> None: ...

    def prepare_candidate(self, action: ControllerAction) -> None: ...

    def enable_active(self, action: ControllerAction) -> None: ...

    def mode(self) -> DataPlaneMode: ...


def _json_object(result: CommandResult, description: str) -> object:
    if result.returncode != 0:
        raise RuntimeError(f"unable to observe local {description}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"local {description} observation is malformed") from None


def _json_or_text(result: CommandResult, description: str) -> object:
    """Accept iproute2 XFRM output across JSON-capable and text-only builds."""

    if result.returncode != 0:
        raise RuntimeError(f"unable to observe local {description}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout


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


def _configured_bgp_summary_sessions(value: object) -> frozenset[str] | None:
    """Return the exact configured IPv4 peer set from an FRR summary."""

    if not isinstance(value, Mapping):
        return None
    ipv4_unicast = value.get("ipv4Unicast")
    if not isinstance(ipv4_unicast, Mapping):
        return None
    peers = ipv4_unicast.get("peers")
    if not isinstance(peers, Mapping):
        return None
    configured: set[str] = set()
    for peer, details in peers.items():
        if not isinstance(details, Mapping):
            return None
        try:
            address = ipaddress.ip_address(str(peer))
        except ValueError:
            return None
        if address.version != 4:
            return None
        configured.add(str(address))
    return frozenset(configured)


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
    raw = _recursive_values(value, frozenset({"if_id", "ifId"}))
    if isinstance(value, str):
        raw.update(re.findall(r"\bif_id\s+(0[xX][0-9a-fA-F]+|[0-9]+)\b", value))
    normalized: set[str] = set()
    for item in raw:
        try:
            interface_id = int(item, 0)
        except ValueError:
            continue
        if interface_id > 0:
            normalized.add(str(interface_id))
    return normalized


def _ike_sa_ids(raw: str) -> tuple[str, ...]:
    """Extract exact IKE_SA unique IDs from a complete raw VICI response."""

    if not re.search(r"(?:^|\n)list-sas reply \{.*\}\s*$", raw, re.DOTALL):
        raise RuntimeError("local IKE SA observation is malformed")
    event_count = raw.count("list-sa event {")
    observed = tuple(
        re.findall(r"(?:^|\n)list-sa event \{[^{}\s]+ \{uniqueid=([1-9][0-9]*)\b", raw)
    )
    if len(observed) != event_count or len(set(observed)) != len(observed):
        raise RuntimeError("local IKE SA observation is malformed")
    return observed


def _usable_xfrm_devices(links: object, state: object, policies: object) -> set[str]:
    if (
        not isinstance(links, list)
        or not isinstance(state, (list, str))
        or not isinstance(policies, (list, str))
    ):
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
        link_if_ids = _xfrm_if_ids({"if_id": if_id})
        if if_name.startswith("xfrm") and bool(link_if_ids & active_if_ids):
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


def _advertised_bgp_prefixes(value: object) -> frozenset[str] | None:
    """Normalize one FRR advertised-routes response without guessing on malformed data."""

    if not isinstance(value, Mapping):
        return None
    total = value.get("totalPrefixCounter")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    routes = value.get("advertisedRoutes")
    if routes is None and total == 0:
        routes = {}
    if not isinstance(routes, Mapping):
        return None
    normalized: set[str] = set()
    try:
        for prefix in routes:
            network = ipaddress.ip_network(str(prefix), strict=False)
            if network.version != 4:
                return None
            normalized.add(str(network))
    except ValueError:
        return None
    return frozenset(normalized) if len(normalized) == total else None


def _bgp_export_state(
    expected: Mapping[str, frozenset[str]],
    observed: Mapping[str, frozenset[str]],
    *,
    configured_peers: Iterable[str] | None = None,
) -> BGPExportState:
    expected_peers = set(expected)
    live_peers = set(observed) if configured_peers is None else set(configured_peers)
    if live_peers - expected_peers:
        return BGPExportState.DRIFT
    if expected_peers - live_peers or set(observed) != expected_peers:
        return BGPExportState.UNKNOWN
    if any(observed[peer] != prefixes for peer, prefixes in expected.items()):
        return BGPExportState.DRIFT
    return BGPExportState.MATCH


def _bgp_export_diagnostic(
    expected: Mapping[str, frozenset[str]],
    evidence: tuple[frozenset[str], Mapping[str, frozenset[str]]] | None,
) -> str:
    """Summarize export divergence without exposing peer or prefix values."""

    if evidence is None:
        return "observation=unavailable"
    configured, observed = evidence
    expected_peers = set(expected)
    configured_peers = set(configured)
    observed_peers = set(observed)
    shared_peers = expected_peers & observed_peers
    mismatched = sum(1 for peer in shared_peers if observed[peer] != expected[peer])
    return (
        f"expected_peers={len(expected_peers)},"
        f"configured_peers={len(configured_peers)},"
        f"observed_peers={len(observed_peers)},"
        f"missing_configured={len(expected_peers - configured_peers)},"
        f"unexpected_configured={len(configured_peers - expected_peers)},"
        f"missing_observations={len(expected_peers - observed_peers)},"
        f"unexpected_observations={len(observed_peers - expected_peers)},"
        f"mismatched_prefix_sets={mismatched},"
        f"expected_prefixes={sum(len(prefixes) for prefixes in expected.values())},"
        f"observed_prefixes={sum(len(prefixes) for prefixes in observed.values())}"
    )


def _passive_bgp_policy_matches(
    running_config: str,
    expected_peers: Iterable[str],
) -> bool:
    """Prove that every expected peer is bound only to the deny-all export map."""

    peers = frozenset(str(peer) for peer in expected_peers)
    if not peers:
        return True
    lines = {line.strip() for line in running_config.splitlines() if line.strip()}
    deny_entries = {
        line for line in lines if line.startswith("route-map ADVERTISE-NONE ")
    }
    if deny_entries != {"route-map ADVERTISE-NONE deny 10"}:
        return False
    for peer in peers:
        outbound = {
            line
            for line in lines
            if line.startswith(f"neighbor {peer} route-map ") and line.endswith(" out")
        }
        if outbound != {f"neighbor {peer} route-map ADVERTISE-NONE out"}:
            return False
    return True


class SystemDataPlaneRuntime:
    """Bounded command adapter for local service, FRR, XFRM, and forwarding truth."""

    def __init__(
        self,
        *,
        state_path: Path,
        guard_path: Path,
        materialization_path: Path | None = None,
        configured_bgp_sessions: Iterable[str],
        expected_bgp_policy_digest: str,
        expected_bgp_exports: Mapping[str, Iterable[str]] | None = None,
        bgp_export_observation_required: bool = False,
        runner: CommandRunner = _run_command,
        command_timeout: float = 5.0,
        commands: DataPlaneCommandSet = DataPlaneCommandSet(),
        routing_lock_path: Path = Path("/run/nebius-vpngw/fix-routes.lock"),
        active_preparer: Callable[[], None] | None = None,
        blocked_preparer: Callable[[], None] | None = None,
        tunnel_cold_passive: bool = False,
        passive_materialization_attempts: int = 12,
        passive_materialization_interval: float = 0.5,
        bgp_export_attempts: int = 60,
        bgp_export_interval: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(command_timeout) or command_timeout <= 0:
            raise ValueError("local command timeout must be finite and positive")
        if passive_materialization_attempts < 1:
            raise ValueError("passive materialization attempts must be positive")
        if bgp_export_attempts < 1:
            raise ValueError("BGP export attempts must be positive")
        if (
            not math.isfinite(passive_materialization_interval)
            or passive_materialization_interval < 0
        ):
            raise ValueError("passive materialization interval must be finite and non-negative")
        if not math.isfinite(bgp_export_interval) or bgp_export_interval < 0:
            raise ValueError("BGP export interval must be finite and non-negative")
        self.state_path = state_path
        self.guard_path = guard_path
        self.materialization_path = materialization_path or state_path.with_name(
            "materialization.json"
        )
        self.configured_bgp_sessions = frozenset(str(item) for item in configured_bgp_sessions)
        self.expected_bgp_policy_digest = expected_bgp_policy_digest
        expected_exports = (
            expected_bgp_exports
            if expected_bgp_exports is not None
            else {peer: () for peer in self.configured_bgp_sessions}
        )
        if set(expected_exports) != set(self.configured_bgp_sessions):
            raise ValueError("expected BGP exports must cover every configured session exactly")
        try:
            self.expected_bgp_exports = {
                str(peer): frozenset(
                    str(ipaddress.ip_network(str(prefix), strict=True))
                    for prefix in prefixes
                )
                for peer, prefixes in expected_exports.items()
            }
        except ValueError:
            raise ValueError("expected BGP exports contain an invalid prefix") from None
        self.bgp_export_observation_required = bgp_export_observation_required
        self.runner = runner
        self.command_timeout = command_timeout
        self.commands = commands
        self.routing_lock_path = routing_lock_path
        self.active_preparer = active_preparer
        self.blocked_preparer = blocked_preparer
        self.tunnel_cold_passive = tunnel_cold_passive
        self.passive_materialization_attempts = passive_materialization_attempts
        self.passive_materialization_interval = passive_materialization_interval
        self.bgp_export_attempts = bgp_export_attempts
        self.bgp_export_interval = bgp_export_interval
        self.sleeper = sleeper
        self.monotonic_clock = monotonic_clock

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

    def _write_state(self, action: ControllerAction, mode: DataPlaneMode) -> None:
        _atomic_write_json(
            self.state_path,
            {
                "boot_id": action.boot_id,
                "mode": mode.value,
                "operation_id": action.operation_id,
                "schema": "nebius-vpngw/vm-ha-data-plane-v1",
            },
        )

    def _run(self, *argv: str) -> CommandResult:
        try:
            return self.runner(argv, self.command_timeout)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            raise RuntimeError("bounded local data-plane command failed") from None

    def _run_before(self, deadline: float, *argv: str) -> CommandResult:
        remaining = deadline - self.monotonic_clock()
        if remaining <= 0:
            raise TimeoutError
        try:
            return self.runner(argv, min(self.command_timeout, remaining))
        except (OSError, subprocess.SubprocessError, TimeoutError):
            raise RuntimeError("bounded local repair command failed") from None

    def _forwarding(self) -> bool:
        result = self._run(self.commands.sysctl, "-n", "net.ipv4.ip_forward")
        if result.returncode != 0 or result.stdout.strip() not in {"0", "1"}:
            raise RuntimeError("local forwarding state is unavailable")
        return result.stdout.strip() == "1"

    def _observe_bgp_exports(
        self,
        *,
        configured_sessions: Iterable[str] | None = None,
        established_sessions: Iterable[str] | None = None,
    ) -> tuple[frozenset[str], dict[str, frozenset[str]]] | None:
        if (
            not self.expected_bgp_exports
            and not self.bgp_export_observation_required
        ):
            return frozenset(), {}
        configured = (
            frozenset(configured_sessions)
            if configured_sessions is not None
            else frozenset()
        )
        established = (
            frozenset(established_sessions)
            if established_sessions is not None
            else frozenset()
        )
        if configured_sessions is None or established_sessions is None:
            summary_result = self._run(
                self.commands.vtysh,
                "-c",
                "show bgp summary json",
            )
            if summary_result.returncode != 0:
                return None
            try:
                summary = json.loads(summary_result.stdout)
            except json.JSONDecodeError:
                return None
            configured_summary = _configured_bgp_summary_sessions(summary)
            if configured_summary is None:
                return None
            configured = configured_summary
            established = frozenset(_established_bgp_sessions(summary)) & configured
        observed: dict[str, frozenset[str]] = {}
        for peer in sorted(established):
            result = self._run(
                self.commands.vtysh,
                "-c",
                f"show bgp ipv4 unicast neighbors {peer} advertised-routes json",
            )
            if result.returncode != 0:
                continue
            try:
                prefixes = _advertised_bgp_prefixes(json.loads(result.stdout))
            except json.JSONDecodeError:
                prefixes = None
            if prefixes is not None:
                observed[peer] = prefixes
        return configured, observed

    def _expected_exports_for_mode(
        self, mode: DataPlaneMode
    ) -> dict[str, frozenset[str]]:
        if mode is DataPlaneMode.ACTIVE:
            return dict(self.expected_bgp_exports)
        return {peer: frozenset() for peer in self.expected_bgp_exports}

    def _passive_bgp_exports_proven(self) -> bool:
        """Prove zero current exports without requiring every peer to be established."""

        summary_result = self._run(
            self.commands.vtysh,
            "-c",
            "show bgp summary json",
        )
        if summary_result.returncode != 0:
            return False
        try:
            summary = json.loads(summary_result.stdout)
        except json.JSONDecodeError:
            return False
        configured = _configured_bgp_summary_sessions(summary)
        if configured is None or configured != frozenset(self.expected_bgp_exports):
            return False
        running = self._run(self.commands.vtysh, "-c", "show running-config")
        if running.returncode != 0 or not _passive_bgp_policy_matches(
            running.stdout,
            configured,
        ):
            return False
        established = frozenset(_established_bgp_sessions(summary)) & configured
        for peer in sorted(established):
            result = self._run(
                self.commands.vtysh,
                "-c",
                f"show bgp ipv4 unicast neighbors {peer} advertised-routes json",
            )
            if result.returncode != 0:
                return False
            try:
                prefixes = _advertised_bgp_prefixes(json.loads(result.stdout))
            except json.JSONDecodeError:
                return False
            if prefixes != frozenset():
                return False
        return True

    def _require_bgp_exports(self, mode: DataPlaneMode) -> None:
        expected = self._expected_exports_for_mode(mode)
        state = BGPExportState.UNKNOWN
        evidence: tuple[frozenset[str], dict[str, frozenset[str]]] | None = None
        for attempt in range(1, self.bgp_export_attempts + 1):
            evidence = self._observe_bgp_exports()
            state = (
                BGPExportState.UNKNOWN
                if evidence is None
                else _bgp_export_state(
                    expected,
                    evidence[1],
                    configured_peers=evidence[0],
                )
            )
            if state is BGPExportState.MATCH:
                return
            if (
                mode is not DataPlaneMode.ACTIVE
                and state is BGPExportState.UNKNOWN
                and self._passive_bgp_exports_proven()
            ):
                return
            if attempt < self.bgp_export_attempts:
                self.sleeper(self.bgp_export_interval)
        raise RuntimeError(
            f"{mode.value} BGP advertisements are {state.value}; exact evidence is required "
            f"({_bgp_export_diagnostic(expected, evidence)})"
        )

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
        all_routes = self._run(
            self.commands.ip,
            "-j",
            "-4",
            "route",
            "show",
            "table",
            "all",
        )
        routes = _json_object(all_routes, "routes")
        rules = self._run(self.commands.ip, "rule", "show")
        broad_apipa = self._run(
            self.commands.ip, "route", "show", "169.254.0.0/16"
        )
        xfrm = _json_or_text(self._run(self.commands.ip, "-j", "xfrm", "state"), "XFRM state")
        xfrm_policies = _json_or_text(
            self._run(self.commands.ip, "-j", "xfrm", "policy"), "XFRM policy"
        )
        xfrm_links = _json_object(
            self._run(self.commands.ip, "-d", "-j", "link", "show", "type", "xfrm"),
            "XFRM interfaces",
        )
        running_config = self._run(self.commands.vtysh, "-c", "show running-config")
        if running_config.returncode != 0:
            raise RuntimeError("unable to observe current FRR import policy")
        configured = _configured_bgp_summary_sessions(summary)
        if configured is None:
            raise RuntimeError("FRR configured peer state is malformed")
        established = frozenset(_established_bgp_sessions(summary)) & configured
        export_evidence = self._observe_bgp_exports(
            configured_sessions=configured,
            established_sessions=established,
        )
        advertised = {} if export_evidence is None else export_evidence[1]
        established_ike_sa_count = 0
        if self.tunnel_cold_passive:
            listed_sas = self._run(self.commands.swanctl, "--list-sas", "--raw")
            if listed_sas.returncode != 0:
                raise RuntimeError("unable to observe local IKE SA state")
            established_ike_sa_count = len(_ike_sa_ids(listed_sas.stdout))
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
                    try:
                        network = ipaddress.ip_network(prefix, strict=False)
                    except ValueError:
                        continue
                    if (
                        network.version == 4
                        and isinstance(device, str)
                        and device in usable_xfrm_devices
                    ):
                        usable_xfrm.add(str(network))
        return LocalDataPlaneObservation(
            service_healthy=services,
            forwarding_enabled=self._forwarding(),
            static_prefixes=frozenset(static_prefixes),
            configured_bgp_sessions=configured,
            established_bgp_sessions=frozenset(established),
            learned_bgp_prefixes=frozenset(learned),
            usable_xfrm_prefixes=frozenset(usable_xfrm),
            observed_bgp_policy_digest=_observed_import_policy_digest(
                running_config.stdout, configured
            ),
            established_ike_sa_count=established_ike_sa_count,
            bgp_export_state=_bgp_export_state(
                self._expected_exports_for_mode(self.mode()),
                advertised,
                configured_peers=configured,
            ),
            routing_hygiene_ready=_routing_hygiene_ready(
                rules, all_routes, broad_apipa
            ),
        )

    def _suspend_cluster_tunnels_locked(self, *, stop_if_unavailable: bool) -> None:
        active_services = tuple(
            service
            for service in ("strongswan-starter", "strongswan")
            if self._run(self.commands.systemctl, "is-active", "--quiet", service).returncode
            == 0
        )
        if not active_services:
            return
        unload = self._run(
            self.commands.swanctl,
            "--load-conns",
            "--file",
            self.commands.empty_swanctl_config,
        )
        if unload.returncode != 0:
            if not stop_if_unavailable:
                raise RuntimeError("cluster tunnel configuration could not be suspended")
            # A booting charon can report its unit active before VICI accepts
            # requests. Blocked mode may stop that exact unit; passive mode may
            # not because it must remain a service-ready warm Compute standby.
            for service in active_services:
                stopped = self._run(self.commands.systemctl, "stop", service)
                if stopped.returncode != 0:
                    raise RuntimeError(
                        "cluster tunnel service could not be stopped behind the guard"
                    )
            if any(
                self._run(
                    self.commands.systemctl, "is-active", "--quiet", service
                ).returncode
                == 0
                for service in active_services
            ):
                raise RuntimeError("cluster tunnel service remained active behind the guard")
            return
        listed = self._run(self.commands.swanctl, "--list-sas", "--raw")
        if listed.returncode != 0:
            raise RuntimeError("cluster tunnel state could not be observed")
        for ike_sa_id in _ike_sa_ids(listed.stdout):
            self._run(
                self.commands.swanctl,
                "--terminate",
                "--ike-id",
                ike_sa_id,
                "--timeout",
                "5",
            )
        remaining = self._run(self.commands.swanctl, "--list-sas", "--raw")
        if remaining.returncode != 0:
            raise RuntimeError("cluster tunnel state could not be observed")
        if _ike_sa_ids(remaining.stdout):
            raise RuntimeError("cluster tunnel initiation could not be disabled")

    def _set_mode(self, action: ControllerAction, mode: DataPlaneMode) -> None:
        self.routing_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.routing_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            self._set_mode_locked(action, mode)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _withdraw_bgp_exports_locked(self) -> None:
        """Install and prove deny-all exports, with service stop as the safety fallback."""

        if (
            not self.expected_bgp_exports
            and not self.bgp_export_observation_required
        ):
            return
        render_error: Exception | None = None
        if self.blocked_preparer is not None:
            try:
                self.blocked_preparer()
                self._require_bgp_exports(DataPlaneMode.BLOCKED)
                return
            except Exception as error:
                render_error = error

        stopped = self._run(self.commands.systemctl, "stop", "frr")
        active = self._run(self.commands.systemctl, "is-active", "--quiet", "frr")
        if stopped.returncode != 0 or active.returncode == 0:
            raise RuntimeError("blocked BGP export withdrawal could not be verified") from (
                render_error
            )

    def _restore_blocked_authority_locked(self, action: ControllerAction) -> None:
        if self._forwarding():
            result = self._run(self.commands.sysctl, "-w", "net.ipv4.ip_forward=0")
            if result.returncode != 0 or self._forwarding():
                raise RuntimeError("failed transition is not forwarding-fenced")
        self._withdraw_bgp_exports_locked()
        self._write_state(action, DataPlaneMode.BLOCKED)
        self._write_guard(action, DataPlaneMode.BLOCKED)

    def _set_mode_locked(self, action: ControllerAction, mode: DataPlaneMode) -> None:
        forwarding = "1" if mode is DataPlaneMode.ACTIVE else "0"
        if mode is not DataPlaneMode.ACTIVE:
            # Revoke the shared readiness authority before touching kernel or
            # tunnel state so no concurrent writer can re-enable forwarding.
            self._write_guard(action, DataPlaneMode.BLOCKED)
        else:
            self._write_state(action, DataPlaneMode.BLOCKED)
        result = self._run(self.commands.sysctl, "-w", f"net.ipv4.ip_forward={forwarding}")
        if result.returncode != 0 or self._forwarding() is (mode is not DataPlaneMode.ACTIVE):
            raise RuntimeError("local forwarding postcondition was not observed")
        if mode is not DataPlaneMode.ACTIVE:
            self._withdraw_bgp_exports_locked()
        if mode is DataPlaneMode.BLOCKED:
            self._suspend_cluster_tunnels_locked(stop_if_unavailable=True)
        elif mode is DataPlaneMode.PASSIVE:
            strongswan_running = any(
                self._run(self.commands.systemctl, "is-active", "--quiet", service).returncode == 0
                for service in ("strongswan-starter", "strongswan")
            )
            if strongswan_running and self.tunnel_cold_passive:
                self._suspend_cluster_tunnels_locked(stop_if_unavailable=False)
            elif strongswan_running:
                load = self._run(self.commands.swanctl, "--load-all", "--noprompt")
                if load.returncode != 0:
                    raise RuntimeError("cluster tunnel configuration could not be loaded")
        try:
            self._write_state(action, mode)
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
        try:
            try:
                _durably_unlink(self.materialization_path)
            except OSError:
                raise RuntimeError(
                    "prior passive materialization receipt could not be invalidated"
                ) from None
            reload_agent = self._run(
                self.commands.systemctl,
                "reload-or-restart",
                "nebius-vpngw-agent",
            )
            if reload_agent.returncode != 0:
                raise RuntimeError("passive data-plane materialization could not be requested")
            for attempt in range(1, self.passive_materialization_attempts + 1):
                materialized = self._run(
                    self.commands.python,
                    "-m",
                    "nebius_vpngw.agent.main",
                    "--vm-ha-materialized",
                )
                if materialized.returncode == 0:
                    break
                if attempt < self.passive_materialization_attempts:
                    self.sleeper(self.passive_materialization_interval)
            else:
                raise RuntimeError("passive data-plane materialization did not converge")
            self._require_bgp_exports(DataPlaneMode.PASSIVE)
            if self.tunnel_cold_passive:
                self._suspend_passive_tunnels(action)
        except Exception as error:
            # PASSIVE is observable controller authority, not merely a forwarding
            # setting.  If materialization or hygiene fails after the initial
            # mode write, durably return to BLOCKED so the checkpointed action is
            # replayed instead of being mistaken for complete on the next step.
            try:
                self._set_failed_passive_blocked(action)
            except Exception:
                raise RuntimeError(
                    "failed passive transition could not restore blocked authority"
                ) from error
            raise

    def _set_failed_passive_blocked(self, action: ControllerAction) -> None:
        self.routing_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.routing_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            self._restore_blocked_authority_locked(action)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def disable_active(self, action: ControllerAction) -> None:
        self._set_mode(action, DataPlaneMode.BLOCKED)

    def _suspend_passive_tunnels(self, action: ControllerAction) -> None:
        self.routing_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.routing_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            if self._forwarding() or self.mode() is not DataPlaneMode.PASSIVE:
                raise RuntimeError("cold static standby lost its passive forwarding fence")
            self._suspend_cluster_tunnels_locked(stop_if_unavailable=False)
            self._write_state(action, DataPlaneMode.PASSIVE)
            self._write_guard(action, DataPlaneMode.PASSIVE)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def prepare_candidate(self, action: ControllerAction) -> None:
        if not self.tunnel_cold_passive:
            raise RuntimeError("candidate tunnel preparation is not enabled for this runtime")
        self.routing_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.routing_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            if self._forwarding() or self.mode() is not DataPlaneMode.PASSIVE:
                raise RuntimeError("candidate tunnel preparation requires passive fenced mode")
            load = self._run(self.commands.swanctl, "--load-all", "--noprompt")
            if load.returncode != 0:
                raise RuntimeError("candidate tunnel configuration could not be loaded")
            for attempt in range(1, self.passive_materialization_attempts + 1):
                listed = self._run(self.commands.swanctl, "--list-sas", "--raw")
                if listed.returncode == 0 and _ike_sa_ids(listed.stdout):
                    break
                if attempt < self.passive_materialization_attempts:
                    self.sleeper(self.passive_materialization_interval)
            else:
                raise RuntimeError("candidate IKE SA establishment did not converge")
            if self._forwarding():
                raise RuntimeError("candidate tunnel preparation changed forwarding state")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def emergency_fence(self, action: ControllerAction) -> None:
        """Physically disable forwarding without waiting for the ordinary routing lock."""

        result = self.runner(
            (self.commands.sysctl, "-w", "net.ipv4.ip_forward=0"),
            min(self.command_timeout, 0.75),
        )
        verified = self.runner(
            (self.commands.sysctl, "-n", "net.ipv4.ip_forward"),
            min(self.command_timeout, 0.2),
        )
        if result.returncode != 0 or verified.returncode != 0 or verified.stdout.strip() != "0":
            raise RuntimeError("emergency local forwarding fence was not verified")
        # Forwarding is already physically closed. Persistence is deliberately
        # last so a filesystem or lock failure cannot delay the safety action.
        self._write_state(action, DataPlaneMode.BLOCKED)
        self._write_guard(action, DataPlaneMode.BLOCKED)

    def repair_local(self, action: ControllerAction) -> None:
        if action.repair_deadline_at is None or not action.repair_reasons:
            raise RuntimeError("local repair action has no bounded authority")
        repair_deadline = action.repair_deadline_at - 1.0
        try:
            reasons = set(action.repair_reasons)
            encrypted_path_unhealthy = bool(
                {"local-service-unhealthy", "xfrm-not-ready"} & reasons
            )
            if encrypted_path_unhealthy:
                result = self._run_before(
                    repair_deadline,
                    self.commands.systemctl,
                    "restart",
                    "strongswan-starter",
                    "frr",
                )
            elif "bgp-not-ready" in reasons:
                result = self._run_before(
                    repair_deadline,
                    self.commands.systemctl,
                    "reload-or-restart",
                    "frr",
                )
            else:
                result = self._run_before(
                    repair_deadline,
                    self.commands.systemctl,
                    "reload-or-restart",
                    "nebius-vpngw-agent",
                )
            if result.returncode != 0:
                raise RuntimeError("local repair command did not complete successfully")
            if encrypted_path_unhealthy:
                loaded = self._run_before(
                    repair_deadline,
                    self.commands.swanctl,
                    "--load-all",
                    "--noprompt",
                )
                if loaded.returncode != 0:
                    raise RuntimeError("local tunnel state did not reload")
            if self.monotonic_clock() >= repair_deadline:
                raise TimeoutError
        except (RuntimeError, TimeoutError):
            self.emergency_fence(action)

    def enable_active(self, action: ControllerAction) -> None:
        # Promotion prerequisites must be installed and verified while the
        # forwarding fence is still closed.  A failed preparation therefore
        # cannot expose an incompletely protected gateway.
        self.routing_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.routing_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            if self._forwarding():
                if self.mode() is not DataPlaneMode.ACTIVE:
                    raise RuntimeError("forwarding is enabled without active authority")
                try:
                    self._require_bgp_exports(DataPlaneMode.ACTIVE)
                except Exception as error:
                    self._restore_blocked_authority_locked(action)
                    raise RuntimeError(
                        "replayed active authority did not retain exact BGP exports"
                    ) from error
                return
            try:
                if self.active_preparer is not None:
                    self.active_preparer()
                if self._forwarding():
                    raise RuntimeError("active preparation changed forwarding state")
                self._require_bgp_exports(DataPlaneMode.ACTIVE)
                if self._forwarding():
                    raise RuntimeError("active export verification changed forwarding state")
                self._set_mode_locked(action, DataPlaneMode.ACTIVE)
            except Exception as error:
                try:
                    self._restore_blocked_authority_locked(action)
                except Exception:
                    raise RuntimeError(
                        "failed active transition could not restore blocked authority"
                    ) from error
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def request_agent_reconcile(self) -> None:
        reload_agent = self._run(
            self.commands.systemctl,
            "reload-or-restart",
            "nebius-vpngw-agent",
        )
        if reload_agent.returncode != 0:
            raise RuntimeError("active data-plane reconciliation could not be requested")

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

    def verify_migration_route(self, binding: VMHAMigrationRouteBinding) -> bool: ...

    def verify_migration_successor(
        self,
        binding: VMHAMigrationRouteBinding,
        ownership: ManagedRouteOwnership,
    ) -> bool: ...

    def list_routes(
        self, target: VMHARouteTarget, ownership: Mapping[str, ManagedRouteOwnership]
    ) -> tuple[ManagedRouteSnapshot | RouteOccupancySnapshot, ...]: ...

    def apply_mutation(self, mutation: RouteMutation) -> str | None: ...

    def recover_deleted_route(self, mutation: RouteMutation) -> bool: ...

    def recover_created_route(self, mutation: RouteMutation) -> str | None: ...

    def recover_restored_route(self, mutation: RouteMutation) -> str | None: ...


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
        if mutation.kind is RouteMutationKind.REPLACE and mutation.rollback is None:
            raise ValueError("VM-HA replacement intent requires an exact rollback snapshot")
        pending = PendingRouteMutation(mutation=mutation, context=context)
        existing = self.load_pending_mutation()
        if existing is not None:
            if existing == pending:
                return
            raise ValueError("another VM-HA route mutation intent is pending")
        self._write_pending_mutation(pending)

    @staticmethod
    def _mutation_payload(mutation: RouteMutation) -> dict[str, object]:
        return {
            "allocation_id": mutation.allocation_id,
            "cluster_id": mutation.cluster_id,
            "kind": mutation.kind.value,
            "prefix": mutation.prefix,
            "route_id": mutation.route_id,
            "route_kind": mutation.route_kind.value,
            "route_target": mutation.route_target.model_dump(mode="json"),
        }

    def _write_pending_mutation(self, pending: PendingRouteMutation) -> None:
        if pending.record_version != 2:
            raise ValueError("VM-HA legacy route intent cannot be rewritten implicitly")
        accepted = pending.accepted_operation
        _atomic_write_json(
            self.pending_path,
            {
                "accepted_operation": (
                    None
                    if accepted is None
                    else {
                        "action": accepted.action,
                        "action_operation_id": accepted.action_operation_id,
                        "cloud_operation_id": accepted.cloud_operation_id,
                    }
                ),
                "context": pending.context.to_dict(),
                "mutation": self._mutation_payload(pending.mutation),
                "phase": pending.phase.value,
                "rollback": (
                    None
                    if pending.mutation.rollback is None
                    else pending.mutation.rollback.to_dict()
                ),
                "schema": "nebius-vpngw/vm-ha-route-mutation-intent-v2",
            },
        )

    def load_pending_mutation(
        self,
    ) -> PendingRouteMutation | None:
        value = self._read(self.pending_path)
        if value is None:
            return None
        schema = value.get("schema")
        if schema == "nebius-vpngw/vm-ha-route-mutation-intent-v1":
            if set(value) != {"context", "mutation", "schema"}:
                raise ValueError("VM-HA route mutation intent is malformed")
            phase = RouteMutationPhase.INTENT
            rollback = None
            accepted = None
            record_version = 1
        elif schema == "nebius-vpngw/vm-ha-route-mutation-intent-v2":
            if set(value) != {
                "accepted_operation",
                "context",
                "mutation",
                "phase",
                "rollback",
                "schema",
            }:
                raise ValueError("VM-HA route mutation intent is malformed")
            try:
                phase = RouteMutationPhase(str(value.get("phase")))
            except ValueError as error:
                raise ValueError("VM-HA route mutation phase is invalid") from error
            raw_rollback = value.get("rollback")
            rollback = (
                None if raw_rollback is None else RouteRollbackSnapshot.from_mapping(raw_rollback)
            )
            raw_accepted = value.get("accepted_operation")
            if raw_accepted is None:
                accepted = None
            elif isinstance(raw_accepted, Mapping) and set(raw_accepted) == {
                "action",
                "action_operation_id",
                "cloud_operation_id",
            }:
                accepted = AcceptedRouteOperation(
                    action_operation_id=str(raw_accepted["action_operation_id"]),
                    action=str(raw_accepted["action"]),
                    cloud_operation_id=str(raw_accepted["cloud_operation_id"]),
                )
            else:
                raise ValueError("VM-HA accepted route operation is malformed")
            record_version = 2
        else:
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
        route_mutation = RouteMutation(
            kind=RouteMutationKind(str(mutation["kind"])),
            prefix=str(mutation["prefix"]),
            route_kind=ManagedRouteKind(str(mutation["route_kind"])),
            allocation_id=str(mutation["allocation_id"]),
            cluster_id=str(mutation["cluster_id"]),
            route_target=VMHARouteTarget.model_validate(mutation["route_target"]),
            route_id=route_id,
            rollback=rollback,
        )
        return PendingRouteMutation(
            mutation=route_mutation,
            context=RouteReconciliationContext.from_mapping(value["context"]),
            phase=phase,
            accepted_operation=accepted,
            record_version=record_version,
        )

    def checkpoint_pending_mutation(
        self,
        expected: PendingRouteMutation,
        *,
        phase: RouteMutationPhase,
        rollback: RouteRollbackSnapshot | None,
        accepted_operation: AcceptedRouteOperation | None,
    ) -> PendingRouteMutation:
        current = self.load_pending_mutation()
        if current != expected:
            raise ValueError("VM-HA route mutation checkpoint changed")
        mutation = replace(current.mutation, rollback=rollback)
        successor = PendingRouteMutation(
            mutation=mutation,
            context=current.context,
            phase=phase,
            accepted_operation=accepted_operation,
            record_version=2,
        )
        self._write_pending_mutation(successor)
        observed = self.load_pending_mutation()
        if observed != successor:
            raise RuntimeError("VM-HA route mutation checkpoint did not reread exactly")
        return successor

    def clear_pending_mutation(self, expected: PendingRouteMutation) -> None:
        if self.load_pending_mutation() != expected:
            raise ValueError("VM-HA route mutation intent changed before clearing")
        self.pending_path.unlink()
        directory = os.open(self.pending_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


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
        bind_route_authority = getattr(self.backend, "bind_route_authority", None)
        if callable(bind_route_authority):
            bind_route_authority(
                cluster_id=binding.cluster_id,
                allocation_id=binding.shared_allocation_id,
                route_targets=binding.route_targets,
            )
        set_checkpoint = getattr(self.backend, "set_mutation_checkpoint", None)
        if callable(set_checkpoint):
            set_checkpoint(self.store)
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
        try:
            observed = self.data_plane.observe()
        except RuntimeError:
            # A freshly guarded member intentionally starts before FRR and
            # strongSwan are materialized.  Treat unavailable local service
            # observations as not-ready so the controller can enter passive
            # and request that materialization.  Promotion remains blocked
            # until a later complete observation proves every readiness bit.
            return LocalReadiness(False, False, False, False)
        bgp = self._bgp(observed)
        static_ready = self.static_manifest.prefixes.issubset(observed.usable_xfrm_prefixes)
        required_xfrm_prefixes = self.static_manifest.prefixes | bgp.required_prefixes
        missing_redundant_sessions = (
            bgp.configured_sessions - bgp.established_sessions
            if bgp.promotion_ready
            else frozenset()
        )
        export_ready = observed.bgp_export_state is BGPExportState.MATCH
        export_reason = {
            BGPExportState.DRIFT: "bgp-export-drift",
            BGPExportState.UNKNOWN: "bgp-export-unavailable",
        }.get(observed.bgp_export_state)
        passive_mode = self.data_plane.mode() is DataPlaneMode.PASSIVE
        routing_hygiene_ready = (
            observed.routing_hygiene_ready if passive_mode else True
        )
        hygiene_reason = (
            "routing-hygiene-not-ready" if not routing_hygiene_ready else None
        )
        readiness = LocalReadiness(
            service_healthy=observed.service_healthy,
            static_ready=static_ready,
            bgp_ready=bgp.promotion_ready and export_ready,
            xfrm_ready=required_xfrm_prefixes.issubset(observed.usable_xfrm_prefixes),
            path_degraded=bool(
                missing_redundant_sessions or export_reason or hygiene_reason
            ),
            degraded_reasons=tuple(
                reason
                for condition, reason in (
                    (
                        bool(missing_redundant_sessions),
                        "redundant-bgp-session-unavailable",
                    ),
                    (export_reason is not None, export_reason or ""),
                    (hygiene_reason is not None, hygiene_reason or ""),
                )
                if condition
            ),
            routing_hygiene_ready=routing_hygiene_ready,
        )
        static_only = bool(self.static_manifest.prefixes) and not self.bgp_policies
        candidate_preparation_required = bool(
            static_only and passive_mode and not readiness.promotion_ready
        )
        cold_standby_ready = bool(
            candidate_preparation_required
            and observed.service_healthy
            and bgp.promotion_ready
            and observed.established_ike_sa_count == 0
            and not observed.usable_xfrm_prefixes
            and routing_hygiene_ready
        )
        return replace(
            readiness,
            candidate_preparation_required=candidate_preparation_required,
            cold_standby_ready=cold_standby_ready,
        )

    def _current_ownership(
        self, *, require_takeover_fence: bool
    ) -> VerifiedAllocationOwnership | None:
        observed = self.cloud.observe()
        exact_local = observed.local_attachment_exact(self.local_node_id)
        fenced = observed.transfer_complete(self.local_node_id)
        if not exact_local or (require_takeover_fence and not fenced):
            return None
        return VerifiedAllocationOwnership(
            cluster_id=self.binding.cluster_id,
            candidate_node_id=self.local_node_id,
            observed_owner_node_id=self.local_node_id,
            allocation_id=observed.allocation_id,
            ownership_epoch=observed.ownership_epoch,
        )

    def _ownership(self, *, require_takeover_fence: bool) -> VerifiedAllocationOwnership:
        ownership = self._current_ownership(require_takeover_fence=require_takeover_fence)
        if ownership is None:
            raise RuntimeError("route reconciliation requires exact current ownership authority")
        return ownership

    def _routes(self, ledger: Mapping[str, ManagedRouteOwnership]):
        routes: list[ManagedRouteSnapshot | RouteOccupancySnapshot] = []
        for target in self.binding.route_targets:
            self.backend.verify_target(target)
            routes.extend(self.backend.list_routes(target, ledger))
        return tuple(routes)

    def _adopt_migration_routes(
        self,
        ledger: dict[str, ManagedRouteOwnership],
        bgp: BGPRouteReadiness,
    ) -> None:
        """Seed exact approval-bound authority after current owner/cloud reproof."""

        desired_kinds = {prefix: ManagedRouteKind.BGP for prefix in bgp.eligible_prefixes}
        desired_kinds.update(
            {prefix: ManagedRouteKind.STATIC for prefix in self.static_manifest.prefixes}
        )
        changed = False
        for migration in self.binding.migration_routes:
            route_kind = desired_kinds.get(migration.prefix)
            if route_kind is None:
                continue
            expected = ManagedRouteOwnership(
                cluster_id=self.binding.cluster_id,
                kind=route_kind,
                route_target=migration.route_target,
            )
            recorded = ledger.get(migration.route_id)
            if recorded is not None:
                if (
                    recorded.cluster_id != expected.cluster_id
                    or recorded.route_target != expected.route_target
                ):
                    raise RuntimeError(
                        "approval-bound migration route conflicts with the durable ledger"
                    )
                if self.backend.verify_migration_successor(migration, expected):
                    if recorded.kind is not expected.kind:
                        ledger[migration.route_id] = expected
                        changed = True
                    continue
                if not self.backend.verify_migration_route(migration):
                    raise RuntimeError("approval-bound migration route changed before replacement")
                if recorded.kind is not expected.kind:
                    # Route kind is controller-owned intent, not cloud identity. A
                    # generation may deliberately make one exact owned prefix
                    # static instead of BGP (or the reverse).  Retype the durable
                    # record only after current owner/cloud reproof and an exact
                    # approval-bound route verification; foreign cluster/target
                    # records remain non-adoptable above.
                    ledger[migration.route_id] = expected
                    changed = True
                continue
            self.backend.verify_target(migration.route_target)
            successor_present = any(
                isinstance(route, ManagedRouteSnapshot)
                and route.prefix == migration.prefix
                and route.ownership == expected
                for route in self.backend.list_routes(migration.route_target, ledger)
            )
            if successor_present:
                continue
            if not self.backend.verify_migration_route(migration):
                raise RuntimeError("approval-bound migration route changed before adoption")
            ledger[migration.route_id] = expected
            changed = True
        if changed:
            self.store.save_ledger(ledger)

    def _retire_stably_absent_ledger_routes(
        self,
        ledger: dict[str, ManagedRouteOwnership],
    ) -> None:
        """Retire only cloud-absent identities after a backend-stable reread."""

        observe_absent = getattr(self.backend, "stably_absent_ledger_route_ids", None)
        if not callable(observe_absent) or not ledger:
            return
        absent = observe_absent(ledger)
        if not isinstance(absent, frozenset) or any(
            not isinstance(route_id, str) or route_id not in ledger for route_id in absent
        ):
            raise RuntimeError("VM-HA absent route-ledger observation is invalid")
        if not absent:
            return
        for route_id in absent:
            del ledger[route_id]
        self.store.save_ledger(ledger)
        if self.store.load_ledger() != ledger:
            raise RuntimeError("VM-HA retired route ledger did not reread exactly")

    def _plan(
        self,
        ownership: VerifiedAllocationOwnership,
        state: RouteTransitionState,
        *,
        prepare_authority: bool = False,
        observed: LocalDataPlaneObservation | None = None,
    ):
        observed = self.data_plane.observe() if observed is None else observed
        bgp = self._bgp(observed)
        ledger = self.store.load_ledger()
        if prepare_authority:
            self._retire_stably_absent_ledger_routes(ledger)
            self._adopt_migration_routes(ledger, bgp)
            synchronize_authority_labels = getattr(
                self.backend,
                "synchronize_authority_labels",
                None,
            )
            if callable(synchronize_authority_labels):
                synchronize_authority_labels(ledger)
        return self.reconciler.plan(
            ownership=ownership,
            static_manifest=self.static_manifest,
            bgp=bgp,
            existing_routes=self._routes(ledger),
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

    def _record_compensated_route(
        self,
        mutation: RouteMutation,
        restored_route_id: str,
        ledger: dict[str, ManagedRouteOwnership],
    ) -> None:
        if not mutation.route_id:
            raise RuntimeError("compensated route replacement has no original identity")
        expected = ManagedRouteOwnership(
            cluster_id=mutation.cluster_id,
            kind=mutation.route_kind,
            route_target=mutation.route_target,
        )
        original = ledger.get(mutation.route_id)
        restored = ledger.get(restored_route_id)
        if restored_route_id == mutation.route_id:
            if original != expected:
                raise RuntimeError("compensated route lacks exact durable management authority")
            return
        if original is None:
            if restored != expected:
                raise RuntimeError("compensated route ledger recovery is ambiguous")
            return
        if original != expected or restored is not None:
            raise RuntimeError("compensated route ledger recovery is ambiguous")
        del ledger[mutation.route_id]
        ledger[restored_route_id] = original
        self.store.save_ledger(ledger)

    def reconcile(self, action: ControllerAction) -> None:
        ownership = self._ownership(
            require_takeover_fence=action.takeover_fence_required
        )
        context = self._context(action, self.binding.cluster_id)
        if not context.matches(ownership):
            raise RuntimeError("route action does not match fresh current ownership")
        set_operation_id = getattr(
            self.backend,
            "set_reconciliation_operation_id",
            None,
        )
        if callable(set_operation_id):
            set_operation_id(context.operation_id)
        ledger = self.store.load_ledger()
        pending = self.store.load_pending_mutation()
        if pending is not None:
            pending_mutation = pending.mutation
            pending_context = pending.context
            if not (
                pending_context.has_same_authority(context)
                and pending_mutation.cluster_id == self.binding.cluster_id
                and pending_mutation.allocation_id == self.binding.shared_allocation_id
                and pending_mutation.route_target in self.binding.route_targets
            ):
                raise RuntimeError(
                    "pending route mutation does not match the current controller authority"
                )
            if callable(set_operation_id):
                set_operation_id(pending_context.operation_id)
            if pending.phase is RouteMutationPhase.RESTORED:
                if pending_context.operation_id == context.operation_id:
                    raise RuntimeError("the current route replacement was terminally compensated")
                restored_route_id = self.backend.recover_restored_route(pending_mutation)
                if not restored_route_id:
                    raise RuntimeError(
                        "the compensated route replacement no longer matches cloud truth"
                    )
                self._record_compensated_route(
                    pending_mutation,
                    restored_route_id,
                    ledger,
                )
                self.store.clear_pending_mutation(pending)
                pending = None
            else:
                self._apply_mutation(pending_mutation, ledger)
            if callable(set_operation_id):
                set_operation_id(context.operation_id)

        state = self.store.load_transition(now=self.clock())
        plan = self._plan(ownership, state, prepare_authority=True)

        def apply(mutation: RouteMutation) -> None:
            self.store.save_pending_mutation(mutation, context)
            self._apply_mutation(mutation, ledger)

        result = NebiusSDKRouteBackend.execute_verified_plan(
            plan,
            context=context,
            apply_mutation=apply,
            reobserve_ownership=lambda: self._ownership(
                require_takeover_fence=action.takeover_fence_required
            ),
            reobserve_plan=lambda: self._plan(
                self._ownership(
                    require_takeover_fence=action.takeover_fence_required
                ),
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
        if pending is None or pending.mutation != mutation:
            raise RuntimeError("route mutation lacks an exact durable pending intent")
        if mutation.route_id:
            recorded = ledger.get(mutation.route_id)
            if recorded is None and self._recover_terminal_successor_ledger(
                pending,
                ledger,
            ):
                return
            if recorded is None or (
                recorded.cluster_id != mutation.cluster_id
                or recorded.route_target != mutation.route_target
                or recorded.kind is not mutation.route_kind
            ):
                raise RuntimeError("route mutation lacks exact durable management authority")
        self.backend.verify_target(mutation.route_target)
        try:
            route_id = self.backend.apply_mutation(mutation)
        except RouteReplacementCompensated as error:
            self._record_compensated_route(
                mutation,
                error.restored_route_id,
                ledger,
            )
            raise RuntimeError(str(error)) from error
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
        completed = self.store.load_pending_mutation()
        if completed is None:
            raise RuntimeError("route mutation checkpoint disappeared before completion")
        self.store.clear_pending_mutation(completed)

    def _recover_terminal_successor_ledger(
        self,
        pending: PendingRouteMutation,
        ledger: Mapping[str, ManagedRouteOwnership],
    ) -> bool:
        """Clear an intent only after exact cloud and successor-ledger reproof."""

        mutation = pending.mutation
        if not mutation.route_id or mutation.route_id in ledger:
            return False
        delete_complete = (
            mutation.kind is RouteMutationKind.DELETE
            and pending.phase is RouteMutationPhase.ORIGINAL_ABSENT
        )
        replacement_complete = (
            mutation.kind is RouteMutationKind.REPLACE
            and pending.phase is RouteMutationPhase.DESIRED_PRESENT
        )
        if not delete_complete and not replacement_complete:
            return False

        self.backend.verify_target(mutation.route_target)
        if delete_complete:
            if not self.backend.recover_deleted_route(mutation):
                raise RuntimeError("completed route deletion changed after ledger commit")
        else:
            route_id = self.backend.apply_mutation(mutation)
            route_id = route_id or self.backend.recover_created_route(mutation)
            expected = ManagedRouteOwnership(
                cluster_id=mutation.cluster_id,
                kind=mutation.route_kind,
                route_target=mutation.route_target,
            )
            if not route_id or ledger.get(route_id) != expected:
                raise RuntimeError("route mutation lacks exact successor management authority")
        self.backend.verify_target(mutation.route_target)

        completed = self.store.load_pending_mutation()
        if completed != pending:
            raise RuntimeError("route mutation checkpoint changed during ledger recovery")
        self.store.clear_pending_mutation(completed)
        return True

    def receipt_context(self) -> ControllerRouteContext | None:
        value = self.store.load_route_reconciliation_receipt()
        if value is None:
            return None
        receipt = RouteReconciliationReceipt.from_mapping(value)
        # The durable receipt proves its promotion-time Stop/detach fence.  A
        # repaired former member may subsequently rejoin as a guarded passive;
        # validating the existing receipt therefore requires current exact
        # local alias ownership, not that the historical former owner remain
        # stopped forever.
        ownership = self._current_ownership(require_takeover_fence=False)
        if ownership is None:
            return None
        state = self.store.load_transition(now=self.clock())
        try:
            observed = self.data_plane.observe()
        except RuntimeError:
            # A current-owner restart can retain a durable route receipt while
            # FRR is intentionally unavailable behind the boot guard. Treat
            # the receipt as unproven until passive materialization completes;
            # this blocks promotion without blocking the materialization step.
            return None
        current_plan = self._plan(ownership, state, observed=observed)
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
    mtls_snapshot_provider: Callable[[], MTLSSnapshot]

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
            self.cloud.confirm_candidate(action)
            after = self.cloud.observe()
            if not (
                after.ownership_epoch == action.ownership_epoch
                and after.transfer_complete(self.local.node_id)
            ):
                raise RuntimeError("candidate ownership is not exact after confirmation")

        def repair(action: ControllerAction) -> None:
            repair_local = getattr(self.data_plane, "repair_local", None)
            if not callable(repair_local):
                raise RuntimeError("local data-plane repair is unavailable")
            repair_local(action)

        def prepare_candidate(action: ControllerAction) -> None:
            observation = self.cloud.observe()
            if not observation.local_attachment_exact(self.local.node_id):
                raise RuntimeError(
                    "candidate tunnel preparation requires exact current allocation ownership"
                )
            if action.takeover_fence_required and not observation.transfer_complete(
                self.local.node_id
            ):
                raise RuntimeError(
                    "candidate tunnel preparation requires the completed transfer fence"
                )
            prepare = getattr(self.data_plane, "prepare_candidate", None)
            if not callable(prepare):
                raise RuntimeError("candidate tunnel preparation is unavailable")
            prepare(action)

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
            ActionKind.REPAIR_LOCAL_DATAPLANE: checked(
                ActionKind.REPAIR_LOCAL_DATAPLANE, repair
            ),
            ActionKind.STOP_FORMER_OWNER: checked(
                ActionKind.STOP_FORMER_OWNER, self.cloud.stop_former
            ),
            ActionKind.DETACH_FORMER_ATTACHMENT: checked(
                ActionKind.DETACH_FORMER_ATTACHMENT, self.cloud.detach_former
            ),
            ActionKind.DETACH_CANDIDATE_FOR_REPROOF: checked(
                ActionKind.DETACH_CANDIDATE_FOR_REPROOF,
                self.cloud.detach_candidate_for_reproof,
            ),
            ActionKind.ATTACH_CANDIDATE: checked(
                ActionKind.ATTACH_CANDIDATE,
                self.cloud.attach_candidate,
            ),
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP: checked(
                ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
                confirm,
            ),
            ActionKind.PREPARE_CANDIDATE_DATAPLANE: checked(
                ActionKind.PREPARE_CANDIDATE_DATAPLANE,
                prepare_candidate,
            ),
            ActionKind.RECONCILE_ROUTES: checked(
                ActionKind.RECONCILE_ROUTES, self.routes.reconcile
            ),
            ActionKind.ENABLE_ACTIVE: checked(ActionKind.ENABLE_ACTIVE, enable),
        }

    def heartbeat(self, *, boot_id: str, sequence: int, clock: float) -> PeerHeartbeat:
        """Build one secret-free advisory heartbeat from fresh local truth."""

        mtls = self.mtls_snapshot_provider()
        cloud = self.cloud.observe()
        readiness = self.routes.readiness()
        data_plane_mode = self.data_plane.mode()
        active_ready = bool(
            data_plane_mode is DataPlaneMode.ACTIVE
            and cloud.local_attachment_exact(self.local.node_id)
        )
        passive_ready = bool(
            data_plane_mode is DataPlaneMode.PASSIVE
            and cloud.authoritative
            and cloud.allocation_id
            and cloud.ownership_epoch
            and cloud.observed_owner_node_id == self.peer.node_id
            and cloud.former_owner_node_id == self.peer.node_id
            and cloud.former_attachment_exact
            and cloud.candidate_attachment_absent
        )
        return PeerHeartbeat(
            cluster_id=self.binding.cluster_id,
            node_id=self.local.node_id,
            boot_id=boot_id,
            sequence=sequence,
            sent_at=datetime.fromtimestamp(clock, timezone.utc).isoformat().replace("+00:00", "Z"),
            configured_role=self.local.role.value,
            observed_owner_id=cloud.observed_owner_node_id,
            generation_id=self.binding.generation_id,
            mtls_epoch=mtls.epoch,
            certificate_fingerprint=mtls.certificate_fingerprint,
            digests=DigestSet(
                self.binding.configuration_digest,
                self.binding.static_routes_digest,
                self.binding.bgp_policy_digest,
            ),
            service_healthy=readiness.service_healthy,
            route_ready=readiness.transfer_ready,
            promotion_ready=bool(
                (
                    readiness.promotion_ready
                    if active_ready
                    else readiness.transfer_ready
                )
                and (active_ready or passive_ready)
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


def _tunnel_is_bgp_enabled(tunnel: Mapping[str, object]) -> bool:
    return str(tunnel.get("ha_role") or "active") != "disable"


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
            if not _tunnel_is_bgp_enabled(tunnel):
                continue
            peer = str(tunnel.get("inner_remote_ip") or "")
            if peer:
                try:
                    ipaddress.ip_address(peer)
                except ValueError:
                    raise ValueError("VM-HA BGP peer address is invalid") from None
                sessions.add(peer)
    if found_connections != expected_connections:
        raise ValueError("VM-HA BGP manifest does not match resolved node sessions")
    return frozenset(sessions)


def _expected_bgp_exports(
    config: Mapping[str, object], bgp_records: list[object]
) -> dict[str, frozenset[str]]:
    expected_connections = {
        str(record.get("connection"))
        for record in bgp_records
        if isinstance(record, Mapping) and record.get("connection")
    }
    gateway = config.get("gateway")
    local_prefixes = (
        tuple(gateway.get("local_prefixes") or ()) if isinstance(gateway, Mapping) else ()
    )
    try:
        normalized_local = frozenset(
            str(ipaddress.ip_network(str(prefix), strict=True)) for prefix in local_prefixes
        )
    except ValueError:
        raise ValueError("VM-HA local BGP export prefix is invalid") from None
    exports: dict[str, frozenset[str]] = {}
    connections = config.get("connections")
    if not isinstance(connections, list):
        connections = []
    for connection in connections:
        if not isinstance(connection, Mapping):
            raise ValueError("VM-HA resolved connection is malformed")
        connection_name = str(connection.get("name") or "")
        if connection_name not in expected_connections:
            continue
        bgp = connection.get("bgp")
        advertise = not isinstance(bgp, Mapping) or bgp.get(
            "advertise_local_prefixes", True
        ) is True
        tunnels = connection.get("tunnels")
        if not isinstance(tunnels, list):
            raise ValueError("VM-HA BGP connection has no resolved tunnels")
        for tunnel in tunnels:
            if not isinstance(tunnel, Mapping):
                raise ValueError("VM-HA BGP tunnel is malformed")
            if not _tunnel_is_bgp_enabled(tunnel):
                continue
            peer = str(tunnel.get("inner_remote_ip") or "")
            if peer:
                exports[peer] = normalized_local if advertise else frozenset()
    if set(exports) != set(_configured_bgp_sessions(config, bgp_records)):
        raise ValueError("VM-HA BGP export policy does not match resolved node sessions")
    return exports


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
            if not _tunnel_is_bgp_enabled(tunnel):
                continue
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
    mtls_snapshot_provider: Callable[[], MTLSSnapshot] | None = None,
    runner: CommandRunner = _run_command,
    clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] | None = None,
    active_preparer: Callable[[], None] | None = None,
    blocked_preparer: Callable[[], None] | None = None,
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
    static_records = json.loads(static_json)
    if not isinstance(static_records, list):
        raise ValueError("VM-HA static route manifest must be a list")
    bgp_records = json.loads(bgp_json)
    if not isinstance(bgp_records, list):
        raise ValueError("VM-HA BGP policy manifest must be a list")
    configured_sessions = _configured_bgp_sessions(config, bgp_records)
    expected_bgp_exports = _expected_bgp_exports(config, bgp_records)
    expected_import_policy_digest = _expected_import_policy_digest(config, bgp_records)
    credential_bundle = credential_bundle_factory(binding, local)
    snapshot_provider = mtls_snapshot_provider or ManagedMTLSStore(
        state_dir / "mtls"
    ).snapshot
    sdk = RenewableNebiusSDK(
        local.nebius_credentials_path,
        factory=sdk_factory,
        credential_check=credential_bundle.revalidate,
    )
    try:
        cloud = build_cloud_runtime(
            binding,
            local_node_id,
            sdk.client,
            operation_journal=VMHACloudOperationJournal(
                state_dir / "accepted-cloud-operation.json"
            ),
        )
        effective_monotonic_clock = monotonic_clock or clock
        data_plane_kwargs: dict[str, object] = {
            "state_path": state_dir / "data-plane.json",
            "guard_path": state_dir / "guard.json",
            "materialization_path": state_dir / "materialization.json",
            "configured_bgp_sessions": configured_sessions,
            "expected_bgp_policy_digest": binding.bgp_policy_digest,
            "expected_bgp_exports": expected_bgp_exports,
            "bgp_export_observation_required": bool(bgp_records),
            "runner": runner,
            "monotonic_clock": effective_monotonic_clock,
            "tunnel_cold_passive": bool(static_records) and not bgp_records,
        }
        if active_preparer is not None:
            data_plane_kwargs["active_preparer"] = active_preparer
        if blocked_preparer is not None:
            data_plane_kwargs["blocked_preparer"] = blocked_preparer
        data_plane = data_plane_factory(
            **data_plane_kwargs,
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
            snapshot_provider=snapshot_provider,
            server_hostname=peer.node_id,
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
            peer_runtime=BoundPeerRuntime(exchange, clock=effective_monotonic_clock),
            credential_bundle=credential_bundle,
            mtls_snapshot_provider=snapshot_provider,
        )
        return ports
    except Exception:
        sdk.close()
        raise
