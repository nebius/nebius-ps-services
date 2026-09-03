from __future__ import annotations

import hashlib
import hmac
import importlib.resources as resources
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import textwrap
import time
import typing as t
from dataclasses import dataclass, replace
from pathlib import Path

from ..config_loader import GatewayGroupSpec
from ..nebius_auth import (
    build_operator_sdk_client,
    error_chain_has_cli_authentication_failure,
)
from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from ..schema import (
    VMHAMigrationRouteBinding,
    VMHARole,
    VMHARouteTarget,
    VMHARuntimeBinding,
    VMHARuntimeNodeBinding,
)
from ..vm_ha_credentials import (
    VMHACredentialIdentity,
    VMHACredentialSet,
    credential_bindings_from_runtime,
    installed_vm_ha_credential_path,
)
from .ordinary_ssh_enrollment import (
    OrdinarySSHEnrollmentTarget,
    enroll_ordinary_ssh_host_key,
)
from .ssh_client_auth import SSHClientAuth, resolve_ssh_client_auth
from .ssh_policy import (
    VM_HA_SSH_HOST_KEY_PATH,
    SSHHostKeyEnrollment,
    SSHHostKeyRecovery,
    SSHTrustPolicy,
    TrustedSSHMemberImport,
    VMHASSHTrustScope,
    build_openssh_base_command,
    managed_ssh_trust_member,
    require_vm_ha_ssh_policy,
)
from .vm_diff import VMDiffAnalyzer, VMSpec
from .vm_ha_cloud import (
    AllocationOwner,
    instance_cloud_state,
    nebius_request_error_code_is,
    operation_status_lookup_unsupported,
    vm_ha_request_kwargs,
    wait_vm_ha_operation,
)
from .vm_ha_identity import (
    LEGACY_VM_HA_SSH_HOST_KEY_PATH,
    PROVISIONING_MARKER_PREFIX,
    FormerVMHAEvidence,
    FormerVMHAProvenance,
    LegacyVMHAIdentity,
    classify_former_vm_ha_evidence,
    compute_provisioning_provenance,
    parse_provisioning_marker,
    recover_product_host_key,
    render_provisioning_marker,
    validate_provisioning_marker,
)
from .vm_ha_lifecycle import (
    VMHALifecycleJournal,
    VMHALifecycleMember,
    VMHALifecycleSnapshot,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHAMigrationTransaction,
    lifecycle_member_map,
    normalize_vm_ha_observation,
    vm_ha_activation_effect_is_host_only,
    vm_ha_effective_resource_bindings,
    vm_ha_missing_standby_disk_name_binding_key,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_observation_changed_paths,
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_cycle_for_approval,
    vm_ha_passive_replacement_cycles,
    vm_ha_passive_replacement_effect,
    vm_ha_resource_binding_matches_observation,
)

if t.TYPE_CHECKING:
    from .vm_ha_cloud import VMHACloudAdapter


_SDK_CLIENT_UNSET = object()


def _read_firewall_setup_script() -> str:
    script_path = Path(__file__).resolve().parents[1] / "systemd" / "setup-vpngw-firewall.sh"
    return script_path.read_text(encoding="utf-8")


def _read_esp4_preflight_script() -> str:
    script_path = Path(__file__).resolve().parents[1] / "systemd" / "nebius-vpngw-esp4-preflight.sh"
    return script_path.read_text(encoding="utf-8")


def _parse_ipv4_network(cidr: str) -> ipaddress.IPv4Network | None:
    """Parse a CIDR as IPv4 only.

    The gateway subnet flow is IPv4-only. `ipaddress.ip_network()` returns an
    IPv4/IPv6 union, so we narrow it explicitly for both runtime safety and
    static type-checkers.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    if isinstance(network, ipaddress.IPv4Network):
        return network
    return None


@dataclass
class VMProvisioningConfig:
    subnet_id: str | None
    num_nics: int
    platform: str
    preset: str | None
    boot_image: str
    disk_gb: int
    disk_type: str
    disk_block_bytes: int
    cloud_init: str


@dataclass(frozen=True)
class PublicAllocationCandidate:
    """One stable subnet-bound allocation safe to offer in an interactive selector."""

    allocation_id: str
    name: str
    address: str
    resource_version: int
    assigned_instance_index: int | None = None
    assigned_nic_index: int | None = None


class VMProvisioningResult(dict[str, str]):
    """VM addresses plus the authoritative binding emitted only for explicit HA."""

    def __init__(
        self,
        vm_ips: dict[str, str],
        *,
        vm_ha_runtime_binding: VMHARuntimeBinding,
    ) -> None:
        super().__init__(vm_ips)
        self.vm_ha_runtime_binding = vm_ha_runtime_binding


def _protobuf_field_present(message: t.Any, field: str) -> bool:
    if message is None:
        return False
    has_field = getattr(message, "HasField", None)
    if callable(has_field):
        try:
            return bool(has_field(field))
        except (TypeError, ValueError):
            pass
    return getattr(message, field, None) is not None


def validate_vm_ha_shared_allocation(
    allocation: t.Any,
    *,
    expected_allocation_id: str,
    expected_name: str,
    expected_project_id: str,
    expected_subnet_id: str,
    expected_owner: tuple[str, str] | None,
) -> str:
    """Validate one shared allocation without using its name as authority."""

    metadata = getattr(allocation, "metadata", None)
    allocation_id = str(getattr(allocation, "id", None) or getattr(metadata, "id", None) or "")
    if not expected_allocation_id or allocation_id != expected_allocation_id:
        raise RuntimeError("VM-HA shared allocation identity does not match approval")
    if str(getattr(metadata, "name", "") or "") != expected_name:
        raise RuntimeError("VM-HA shared allocation name is not canonical")
    if str(getattr(metadata, "parent_id", "") or "") != expected_project_id:
        raise RuntimeError("VM-HA shared allocation parent project changed")
    spec = getattr(allocation, "spec", None)
    if not _protobuf_field_present(spec, "ipv4_private") or _protobuf_field_present(
        spec, "ipv4_public"
    ):
        raise RuntimeError("VM-HA shared allocation must be private and not public")
    private = getattr(spec, "ipv4_private", None)
    if str(getattr(private, "subnet_id", "") or "") != expected_subnet_id:
        raise RuntimeError("VM-HA shared allocation subnet does not match the gateway")

    from .vm_ha_cloud import AllocationOwner, allocation_observation

    observed_owner = allocation_observation(allocation_id, allocation).owner
    required_owner = None if expected_owner is None else AllocationOwner(*expected_owner)
    if observed_owner != required_owner:
        raise RuntimeError("VM-HA shared allocation assignment does not match approval")
    return allocation_id


class VMManager:
    """Manage Nebius gateway VM lifecycle.

    This is a scaffold placeholder. Integrate with Nebius Python SDK when available.
    """

    def __init__(
        self,
        project_id: str | None,
        region: str | None,
        auth_token: str | None = None,
        tenant_id: str | None = None,
        region_id: str | None = None,
        ssh_policy: SSHTrustPolicy | None = None,
        management_key_path: Path | None = None,
        management_public_key: str | None = None,
        vm_ha_credentials: VMHACredentialSet | None = None,
    ) -> None:
        self.project_id = project_id
        self.region = region
        self.auth_token = auth_token
        self.tenant_id = tenant_id
        self.region_id = region_id
        self._ssh_policy = ssh_policy
        self._management_key_path = management_key_path
        self._management_public_key = management_public_key
        self._ssh_client_auth: SSHClientAuth | None = None
        self._vm_ha_credentials = vm_ha_credentials
        self.diff_analyzer = VMDiffAnalyzer()
        self._private_alloc_ids: dict[str, list[str]] = {}
        self._vm_ha_shared_allocation_id: str | None = None
        self._former_vm_ha_snapshot: dict[str, tuple[t.Any, str]] | None = None
        self._ordinary_ssh_preflight_snapshot: dict[str, tuple[t.Any, str]] | None = None
        self._ordinary_ssh_binding_assertions: dict[str, t.Callable[[], None]] = {}
        self._vm_ha_ssh_preflight_snapshot: dict[str, tuple[t.Any, str]] | None = None
        self._vm_ha_ssh_binding_assertions: dict[str, t.Callable[[], None]] = {}
        self._vm_ha_ssh_lifecycle_snapshot: VMHALifecycleSnapshot | None = None
        self._former_vm_ha_evidence: FormerVMHAEvidence | None = None
        self._former_vm_ha_candidate_provenance: FormerVMHAProvenance | None = None
        self._former_vm_ha_lifecycle: VMHALifecycleState | None = None
        self._vm_ha_route_targets: tuple[VMHARouteTarget, ...] | None = None
        self._vm_ha_journal: VMHALifecycleJournal | None = None
        self._vm_ha_effect_spec: GatewayGroupSpec | None = None
        self._vm_ha_effect_prefixes: list[str] | None = None
        self._vm_ha_accepted_resource_ids: dict[str, str] = {}
        self._reported_gateway_network_messages: set[str] = set()
        self._sdk_client: t.Any = _SDK_CLIENT_UNSET
        self._closed = False

    def _require_ssh_client_auth(self) -> SSHClientAuth | None:
        if not str(self._management_public_key or "").strip():
            return None
        if self._ssh_client_auth is None:
            self._ssh_client_auth = resolve_ssh_client_auth(
                self._management_public_key,
                explicit_private_key=self._management_key_path,
            )
        return self._ssh_client_auth

    @property
    def ssh_client_auth(self) -> SSHClientAuth | None:
        return self._require_ssh_client_auth()

    def __enter__(self) -> VMManager:
        if self._closed:
            raise RuntimeError("VMManager is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: t.Any,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        """Close the owned Nebius SDK at most once without changing command outcome."""

        if self._closed:
            return
        self._closed = True
        if self._sdk_client is _SDK_CLIENT_UNSET:
            return
        try:
            self._sdk_client.sync_close()
        except Exception:
            try:
                sys.stderr.write("[VMManager] Warning: failed to close Nebius SDK resources.\n")
            except Exception:
                pass

    def _report_gateway_network_once(self, message: str) -> None:
        """Emit one network-resolution notice per manager."""

        if message in self._reported_gateway_network_messages:
            return
        self._reported_gateway_network_messages.add(message)
        print(message)

    @staticmethod
    def _existing_instances_message(count: int, *, recreate: bool) -> str:
        if count == 0:
            return "[VMManager] No existing VMs found"
        suffix = " for recreation" if recreate else ""
        return f"[VMManager] Found {count} existing VM(s){suffix}"

    def set_vm_ha_lifecycle_journal(self, journal: VMHALifecycleJournal) -> None:
        """Install the only durable authority for explicit VM-HA effects."""

        if self._vm_ha_journal is not None and self._vm_ha_journal is not journal:
            raise RuntimeError("VM-HA lifecycle journal cannot be rebound")
        self._vm_ha_journal = journal

    def set_ssh_policy(self, policy: SSHTrustPolicy) -> None:
        """Rebind SSH after an authorized Compute transition and recheck its new evidence."""

        policy.assert_current()
        self._ssh_policy = policy

    def _vm_ha_resource_binding(self, key: str) -> str | None:
        journal = self._vm_ha_journal
        if journal is None or journal.state.transaction is None:
            return None
        return vm_ha_effective_resource_bindings(
            dict(journal.state.transaction.resource_bindings)
        ).get(key)

    def _stable_vm_ha_effect_observation(self) -> dict[str, object]:
        spec = self._vm_ha_effect_spec
        if spec is None or spec.vm_ha is None:
            raise RuntimeError("VM-HA cloud effect has no bound observation scope")
        first = self.observe_vm_ha_migration_state(
            spec,
            self._vm_ha_effect_prefixes,
        )
        second = self.observe_vm_ha_migration_state(
            spec,
            self._vm_ha_effect_prefixes,
        )
        if first != second:
            raise RuntimeError("VM-HA cloud observation changed during stable reread")
        return first

    @staticmethod
    def _vm_ha_effect_member_index(effect: str, observation: t.Mapping[str, object]) -> int:
        matched = re.fullmatch(r"provision-(.+)-(?:compute|boot-disk)", effect)
        if matched is None:
            matched = re.fullmatch(
                r"provision-(.+)-eth[0-9]+-(?:public|primary)-allocation",
                effect,
            )
        members = observation.get("members")
        if not isinstance(members, list):
            raise RuntimeError("VM-HA cloud observation has no member set")
        name = matched.group(1) if matched is not None else ""
        if not name and effect.startswith(("replace-failed-", "replace-missing-")):
            candidate_names = [
                str(item.get("instance_name") or "") for item in members if isinstance(item, dict)
            ]
            replacement_actions = (
                "delete-compute",
                "delete-boot-disk",
                "create-compute",
                "create-boot-disk",
            )
            name_matches = [
                candidate
                for candidate in candidate_names
                if candidate
                and any(
                    re.fullmatch(
                        rf"replace-(?:failed|missing)-(?:[2-9][0-9]*-)?{re.escape(candidate)}-{action}",
                        effect,
                    )
                    for action in replacement_actions
                )
            ]
            if len(name_matches) == 1:
                name = name_matches[0]
        if not name:
            raise RuntimeError(f"VM-HA cloud effect {effect} has no member identity")
        matches = [
            index
            for index, item in enumerate(members)
            if isinstance(item, dict) and item.get("instance_name") == name
        ]
        if len(matches) != 1:
            raise RuntimeError(f"VM-HA cloud effect member {name} is not canonical")
        return matches[0]

    def _vm_ha_effect_permitted_paths(
        self,
        effect: str,
        observation: t.Mapping[str, object],
    ) -> tuple[str, ...]:
        """Return the exhaustive path-level mutation contract for one cloud effect."""

        if effect == "provision-shared-allocation":
            return tuple(
                sorted(
                    {
                        "/shared_allocation/allocation_id",
                        "/shared_allocation/owner",
                        "/shared_allocation/parent_id",
                        "/shared_allocation/present",
                        "/shared_allocation/private_subnet_id",
                        "/shared_allocation/public_shape_present",
                        "/shared_allocation/resource_revision",
                    }
                )
            )
        if effect == "attach-shared-allocation-active":
            members = observation.get("members")
            if not isinstance(members, list):
                raise RuntimeError("VM-HA cloud observation has no member set")
            spec = self._vm_ha_effect_spec
            if spec is None or spec.vm_ha is None:
                raise RuntimeError("VM-HA active effect has no bound specification")
            active_name = f"{spec.name}-{spec.vm_ha.active_instance_index}"
            active = [
                index
                for index, item in enumerate(members)
                if isinstance(item, dict) and item.get("instance_name") == active_name
            ]
            if len(active) != 1:
                raise RuntimeError("VM-HA active member observation is not canonical")
            prefix = f"/members/{active[0]}"
            return tuple(
                sorted(
                    {
                        f"{prefix}/aliases",
                        f"{prefix}/aliases/*",
                        f"{prefix}/compute_revision",
                        "/shared_allocation/owner",
                        "/shared_allocation/owner/*",
                        "/shared_allocation/resource_revision",
                    }
                )
            )
        if re.fullmatch(
            r"(?:provision-.+|replace-(?:failed|missing)-(?:[2-9][0-9]*-)?.+-create)-compute",
            effect,
        ):
            index = self._vm_ha_effect_member_index(effect, observation)
            prefix = f"/members/{index}"
            return tuple(
                sorted(
                    {
                        f"{prefix}/aliases",
                        f"{prefix}/aliases/*",
                        f"{prefix}/boot_disk_id",
                        f"{prefix}/compute_id",
                        f"{prefix}/compute_revision",
                        f"{prefix}/network_interface_name",
                        f"{prefix}/parent_id",
                        f"{prefix}/present",
                        f"{prefix}/primary_allocation_id",
                        f"{prefix}/public_allocation_id",
                        f"{prefix}/public_ip",
                        f"{prefix}/state",
                        f"{prefix}/subnet_id",
                    }
                )
            )
        if (
            re.fullmatch(r"provision-.+-boot-disk", effect)
            or re.fullmatch(
                r"replace-(?:failed|missing)-(?:[2-9][0-9]*-)?.+-(?:delete|create)-boot-disk",
                effect,
            )
            or re.fullmatch(
                r"provision-.+-eth[0-9]+-(?:public|primary)-allocation",
                effect,
            )
            or effect
            in {
                "construct-authoritative-runtime-binding",
                "resolve-authoritative-route-targets",
            }
        ):
            return ()
        if re.fullmatch(r"replace-failed-(?:[2-9][0-9]*-)?.+-delete-compute", effect):
            index = self._vm_ha_effect_member_index(effect, observation)
            prefix = f"/members/{index}"
            return tuple(
                sorted(
                    {
                        prefix,
                        f"{prefix}/*",
                        f"{prefix}/aliases/*",
                    }
                )
            )
        raise RuntimeError(f"VM-HA cloud effect {effect} is not registered")

    @staticmethod
    def _normalized_observation_string_set(
        observation: tuple[tuple[str, str], ...],
        path: str,
    ) -> frozenset[str]:
        values: list[str] = []
        for leaf_path, encoded in observation:
            if not re.fullmatch(rf"{re.escape(path)}/[0-9]+", leaf_path):
                continue
            value = json.loads(encoded)
            if not isinstance(value, str) or not value:
                raise RuntimeError("VM-HA alias observation is malformed")
            values.append(value)
        if len(values) != len(set(values)):
            raise RuntimeError("VM-HA alias observation contains duplicates")
        return frozenset(values)

    @staticmethod
    def _normalized_observation_value(
        observation: tuple[tuple[str, str], ...],
        path: str,
    ) -> object:
        values = [json.loads(encoded) for leaf_path, encoded in observation if leaf_path == path]
        if len(values) != 1:
            raise RuntimeError(f"VM-HA observation has no canonical value for {path}")
        return values[0]

    @staticmethod
    def _vm_ha_operation_service(client: t.Any, effect: str) -> t.Any:
        """Bind accepted-operation lookup to the service that created it."""

        if effect == "attach-shared-allocation-active" or (
            effect.endswith("-compute")
            and effect.startswith(("provision-", "replace-failed-", "replace-missing-"))
        ):
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

            return InstanceServiceClient(client).operation_service()
        if effect.endswith("-boot-disk") and effect.startswith(
            ("provision-", "replace-failed-", "replace-missing-")
        ):
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

            return DiskServiceClient(client).operation_service()
        if effect == "provision-shared-allocation" or re.fullmatch(
            r"provision-.+-eth[0-9]+-(?:public|primary)-allocation",
            effect,
        ):
            from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore

            return AllocationServiceClient(client).operation_service()
        raise RuntimeError(f"VM-HA cloud effect {effect} has no operation service binding")

    def _validate_vm_ha_effect_postcondition(
        self,
        effect: str,
        observation: t.Mapping[str, object],
    ) -> None:
        journal = self._vm_ha_journal
        transaction = None if journal is None else journal.state.transaction
        guard = None if transaction is None else transaction.observation_guard
        if guard is None or guard.effect != effect:
            raise RuntimeError("VM-HA cloud effect lost its durable observation guard")
        if re.fullmatch(r"replace-failed-(?:[2-9][0-9]*-)?.+-delete-compute", effect):
            index = self._vm_ha_effect_member_index(effect, observation)
            members = observation.get("members")
            if not isinstance(members, list) or not isinstance(members[index], dict):
                raise RuntimeError("VM-HA Compute delete observation is malformed")
            prefix = f"/members/{index}"
            if self._normalized_observation_value(
                guard.pre_observation,
                f"{prefix}/present",
            ) is not True or members[index] != {
                "instance_name": members[index].get("instance_name"),
                "present": False,
            }:
                raise RuntimeError("VM-HA Compute delete footprint changed unexpectedly")
            return
        if re.fullmatch(
            r"(?:provision-.+|replace-(?:failed|missing)-(?:[2-9][0-9]*-)?.+-create)-compute",
            effect,
        ):
            index = self._vm_ha_effect_member_index(effect, observation)
            members = observation.get("members")
            if not isinstance(members, list) or not isinstance(members[index], dict):
                raise RuntimeError("VM-HA Compute create observation is malformed")
            member = t.cast(dict[str, object], members[index])
            instance_name = str(member.get("instance_name") or "")
            prefix = f"/members/{index}"
            if (
                self._normalized_observation_value(
                    guard.pre_observation,
                    f"{prefix}/present",
                )
                is not False
            ):
                raise RuntimeError("VM-HA Compute create footprint did not begin absent")
            before_aliases = self._normalized_observation_string_set(
                guard.pre_observation,
                f"{prefix}/aliases",
            )
            raw_aliases = member.get("aliases")
            if not isinstance(raw_aliases, list) or any(
                not isinstance(alias, str) or not alias for alias in raw_aliases
            ):
                raise RuntimeError("VM-HA Compute create footprint has malformed aliases")
            aliases = frozenset(raw_aliases)
            if len(aliases) != len(raw_aliases):
                raise RuntimeError("VM-HA Compute create footprint has duplicate aliases")
            shared = observation.get("shared_allocation")
            if not isinstance(shared, dict):
                raise RuntimeError("VM-HA Compute create footprint has no shared allocation")
            expected_shared_id = self._vm_ha_resource_binding("shared-allocation-id")
            expected_disk = self._vm_ha_resource_binding(f"disk:{instance_name}")
            expected_primary = self._vm_ha_resource_binding(
                f"primary-allocation:{instance_name}:eth0"
            )
            expected_public = self._vm_ha_resource_binding(
                f"public-allocation:{instance_name}:eth0"
            )
            expected_subnet = str(shared.get("private_subnet_id") or "")
            if (
                not all(
                    (
                        expected_shared_id,
                        expected_disk,
                        expected_primary,
                        expected_public,
                        expected_subnet,
                    )
                )
                or shared.get("allocation_id") != expected_shared_id
            ):
                raise RuntimeError("VM-HA Compute create footprint lacks durable dependencies")
            expected = {
                "aliases": before_aliases,
                "boot_disk_id": expected_disk,
                "network_interface_name": "eth0",
                "parent_id": self.project_id or "",
                "present": True,
                "primary_allocation_id": expected_primary,
                "public_allocation_id": expected_public,
                "subnet_id": expected_subnet,
            }
            actual = {
                "aliases": aliases,
                "boot_disk_id": member.get("boot_disk_id"),
                "network_interface_name": member.get("network_interface_name"),
                "parent_id": member.get("parent_id"),
                "present": member.get("present"),
                "primary_allocation_id": member.get("primary_allocation_id"),
                "public_allocation_id": member.get("public_allocation_id"),
                "subnet_id": member.get("subnet_id"),
            }
            if actual != expected or any(
                not isinstance(member.get(key), str) or not member.get(key)
                for key in ("compute_id", "compute_revision")
            ):
                raise RuntimeError("VM-HA Compute create footprint changed unexpectedly")
            return
        if effect != "attach-shared-allocation-active":
            return
        members = observation.get("members")
        spec = self._vm_ha_effect_spec
        if not isinstance(members, list) or spec is None or spec.vm_ha is None:
            raise RuntimeError("VM-HA active member observation is malformed")
        shared = observation.get("shared_allocation")
        if not isinstance(shared, dict):
            raise RuntimeError("VM-HA shared allocation observation is malformed")
        raw_owner = shared.get("owner")
        allocation_id = self._vm_ha_resource_binding("shared-allocation-id")
        if isinstance(raw_owner, dict) and allocation_id:
            retained_owner = AllocationOwner(
                str(raw_owner.get("compute_id") or ""),
                str(raw_owner.get("network_interface_name") or ""),
            )
            if self._is_retained_vm_ha_owner(allocation_id, retained_owner):
                matching_indices = [
                    index
                    for index, member in enumerate(members)
                    if isinstance(member, dict)
                    and member.get("compute_id") == retained_owner.instance_id
                    and member.get("network_interface_name")
                    == retained_owner.network_interface_name
                ]
                if len(matching_indices) != 1:
                    raise RuntimeError("VM-HA retained owner observation is not canonical")
                retained_index = matching_indices[0]
                retained_member = members[retained_index]
                assert isinstance(retained_member, dict)
                raw_aliases = retained_member.get("aliases")
                if not isinstance(raw_aliases, list) or any(
                    not isinstance(alias, str) or not alias for alias in raw_aliases
                ):
                    raise RuntimeError("VM-HA retained owner aliases are malformed")
                after_aliases = frozenset(raw_aliases)
                before_aliases = self._normalized_observation_string_set(
                    guard.pre_observation,
                    f"/members/{retained_index}/aliases",
                )
                before_owner = AllocationOwner(
                    str(
                        self._normalized_observation_value(
                            guard.pre_observation,
                            "/shared_allocation/owner/compute_id",
                        )
                    ),
                    str(
                        self._normalized_observation_value(
                            guard.pre_observation,
                            "/shared_allocation/owner/network_interface_name",
                        )
                    ),
                )
                if (
                    len(after_aliases) != len(raw_aliases)
                    or allocation_id not in after_aliases
                    or after_aliases != before_aliases
                    or before_owner != retained_owner
                ):
                    raise RuntimeError("VM-HA retained owner changed during apply")
                return
        active_name = f"{spec.name}-{spec.vm_ha.active_instance_index}"
        matching_indices = [
            index
            for index, member in enumerate(members)
            if isinstance(member, dict) and member.get("instance_name") == active_name
        ]
        if len(matching_indices) != 1:
            raise RuntimeError("VM-HA active member observation is not canonical")
        index = matching_indices[0]
        if not isinstance(members[index], dict):
            raise RuntimeError("VM-HA active member observation is malformed")
        raw_aliases = members[index].get("aliases")
        if not isinstance(raw_aliases, list) or any(
            not isinstance(alias, str) or not alias for alias in raw_aliases
        ):
            raise RuntimeError("VM-HA active alias observation is malformed")
        after_aliases = frozenset(raw_aliases)
        if len(after_aliases) != len(raw_aliases):
            raise RuntimeError("VM-HA active alias observation contains duplicates")
        before_aliases = self._normalized_observation_string_set(
            guard.pre_observation,
            f"/members/{index}/aliases",
        )
        if not allocation_id:
            raise RuntimeError("VM-HA shared allocation identity is not durable")
        if after_aliases != before_aliases | {allocation_id}:
            raise RuntimeError("VM-HA shared-allocation attachment changed an unrelated alias")

    def _begin_vm_ha_effect(self, effect: str) -> str | None:
        journal = self._vm_ha_journal
        if journal is None:
            return None
        observation = self._stable_vm_ha_effect_observation()
        operation_id = journal.begin(
            effect,
            observation=observation,
            permitted_paths=self._vm_ha_effect_permitted_paths(effect, observation),
        )
        transaction = journal.state.transaction
        if (
            transaction is not None
            and transaction.accepted_cloud_operation_effect == effect
            and transaction.accepted_cloud_operation_id is not None
        ):
            client = self._get_client()
            if client is None:
                raise RuntimeError("VM-HA accepted cloud operation cannot be resumed")
            from nebius.api.nebius.common.v1 import GetOperationRequest  # type: ignore

            try:
                accepted = (
                    self._vm_ha_operation_service(client, effect)
                    .get(
                        GetOperationRequest(id=transaction.accepted_cloud_operation_id),
                        **vm_ha_request_kwargs(),
                    )
                    .wait()
                )
            except Exception as error:
                if not operation_status_lookup_unsupported(error):
                    raise
            else:
                wait_vm_ha_operation(accepted)
                successful = getattr(accepted, "successful", None)
                if not callable(successful) or not successful():
                    raise RuntimeError("VM-HA accepted cloud operation did not succeed")
                resource = getattr(getattr(accepted, "result", None), "resource", None)
                resource_id = self._resource_id(resource) or str(
                    getattr(accepted, "resource_id", "") or ""
                )
                if resource_id:
                    self._vm_ha_accepted_resource_ids[effect] = resource_id
        return operation_id

    def _complete_vm_ha_effect(
        self,
        effect: str,
        *,
        resource_updates: t.Mapping[str, str] | None = None,
    ) -> None:
        journal = self._vm_ha_journal
        if journal is None:
            return
        transaction = journal.state.transaction
        if transaction is not None and effect in transaction.completed_effects:
            journal.complete(effect, resource_updates=resource_updates)
            return
        observation = self._stable_vm_ha_effect_observation()
        self._validate_vm_ha_effect_postcondition(effect, observation)
        journal.complete(
            effect,
            resource_updates=resource_updates,
            observation=observation,
        )

    @staticmethod
    def _idempotency_kwargs(operation_id: str | None) -> dict[str, object]:
        if not operation_id:
            return {}
        return {"metadata": (("x-idempotency-key", operation_id),)}

    @staticmethod
    def _canonical_observation_digest(value: object) -> str:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _observation_resource_bindings(
        observation: t.Mapping[str, object],
    ) -> dict[str, str]:
        bindings: dict[str, str] = {}
        raw_members = observation.get("members", [])
        if not isinstance(raw_members, list):
            raise RuntimeError("VM-HA authoritative member observation is malformed")
        for raw_member in raw_members:
            if not isinstance(raw_member, dict) or not raw_member.get("present"):
                continue
            name = str(raw_member.get("instance_name") or "")
            nic = str(raw_member.get("network_interface_name") or "")
            for source, key in (
                ("compute_id", f"compute:{name}"),
                ("boot_disk_id", f"disk:{name}"),
                ("primary_allocation_id", f"primary-allocation:{name}:{nic}"),
                ("public_allocation_id", f"public-allocation:{name}:{nic}"),
            ):
                value = str(raw_member.get(source) or "")
                if value:
                    bindings[key] = value
        shared = observation.get("shared_allocation")
        if isinstance(shared, dict) and shared.get("present"):
            allocation_id = str(shared.get("allocation_id") or "")
            if allocation_id:
                bindings["shared-allocation-id"] = allocation_id
            owner = shared.get("owner")
            if isinstance(owner, dict):
                compute_id = str(owner.get("compute_id") or "")
                nic = str(owner.get("network_interface_name") or "")
                if compute_id and nic:
                    bindings["shared-allocation-owner-compute"] = compute_id
                    bindings["shared-allocation-owner-nic"] = nic
        bindings["route-targets-digest"] = VMManager._canonical_observation_digest(
            observation.get("route_targets", [])
        )
        return bindings

    def _verify_vm_ha_transaction_preconditions(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> None:
        journal = self._vm_ha_journal
        if journal is None or journal.state.transaction is None:
            raise RuntimeError("VM-HA mutation has no durable transaction")
        observation = self.observe_vm_ha_migration_state(spec, local_prefixes)
        transaction = journal.state.transaction
        normalized = normalize_vm_ha_observation(observation)
        if transaction.pending_effect is None:
            if transaction.observation and normalized != transaction.observation:
                raise RuntimeError("VM-HA trusted cloud state changed before mutation")
        else:
            if transaction.observation_guard is None:
                raise RuntimeError("VM-HA non-cloud effect cannot resume through provisioning")
            try:
                journal.state.begin_effect(
                    transaction.pending_effect,
                    observation=observation,
                )
            except ValueError as error:
                raise RuntimeError(
                    f"VM-HA pending effect observed unrelated cloud drift: {error}"
                ) from error
        cloud_effects = set(transaction.completed_effects) - {"prepare-service-account"}
        if (
            not cloud_effects
            and transaction.pending_effect is None
            and not transaction.observation
            and (
                self._canonical_observation_digest(observation) != transaction.current_state_digest
            )
        ):
            raise RuntimeError("VM-HA approved current state changed before mutation")
        observed_bindings = self._observation_resource_bindings(observation)
        if self._vm_ha_credentials is not None:
            observed_bindings.update(self._vm_ha_credentials.resource_bindings())
        expected_bindings = vm_ha_effective_resource_bindings(dict(transaction.resource_bindings))
        for key, value in expected_bindings.items():
            if not vm_ha_resource_binding_matches_observation(
                key,
                value,
                observed=observed_bindings,
                expected=expected_bindings,
            ):
                raise RuntimeError(f"VM-HA transaction resource identity drifted: {key}")

        state = journal.state
        if state.status not in {
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.ACTIVE,
        }:
            return
        raw_members = observation.get("members", [])
        if not isinstance(raw_members, list):
            raise RuntimeError("VM-HA authoritative member observation is malformed")
        observed_members = {
            str(item.get("instance_name")): item
            for item in raw_members
            if isinstance(item, dict) and item.get("present")
        }
        for member in state.members:
            current = observed_members.get(member.instance_name)
            if current is None or {
                "aliases": tuple(sorted(str(value) for value in current.get("aliases", []))),
                "compute_id": str(current.get("compute_id") or ""),
                "compute_revision": str(current.get("compute_revision") or ""),
                "disk_id": str(current.get("boot_disk_id") or ""),
                "network_interface_name": str(current.get("network_interface_name") or ""),
                "primary_allocation_id": str(current.get("primary_allocation_id") or ""),
                "public_allocation_id": str(current.get("public_allocation_id") or ""),
                "public_ip": str(current.get("public_ip") or ""),
                "subnet_id": str(current.get("subnet_id") or ""),
            } != {
                "aliases": member.alias_allocation_ids,
                "compute_id": member.compute_id,
                "compute_revision": member.compute_revision,
                "disk_id": member.disk_id,
                "network_interface_name": member.network_interface_name,
                "primary_allocation_id": member.primary_allocation_id,
                "public_allocation_id": member.public_allocation_id,
                "public_ip": member.public_ip,
                "subnet_id": member.network_interface_subnet_id,
            }:
                raise RuntimeError(
                    f"VM-HA lifecycle member identity drifted: {member.instance_name}"
                )

    @staticmethod
    def _is_vm_ha_activation_effect(effect: str | None) -> bool:
        return effect is not None and vm_ha_activation_effect_is_host_only(effect)

    @staticmethod
    def _without_route_observation(
        observation: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        route_effect_paths = {
            "/shared_allocation/resource_revision",
        }
        return tuple(
            item
            for item in observation
            if not item[0].startswith("/routes") and item[0] not in route_effect_paths
        )

    @staticmethod
    def _without_route_revisions(
        observation: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        """Remove only mutable revisions while retaining exact route identity."""

        return tuple(
            item for item in observation if re.fullmatch(r"/routes/\d+/revision", item[0]) is None
        )

    @staticmethod
    def _serialized_route_targets(
        targets: tuple[VMHARouteTarget, ...],
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                json.dumps(
                    target.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for target in targets
            )
        )

    def resume_vm_ha_activation(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> VMProvisioningResult:
        """Rebuild a bound activation view without replaying provisioning effects."""

        journal = self._vm_ha_journal
        if (
            journal is None
            or journal.state.record_version != 4
            or journal.state.status is not VMHALifecycleStatus.ACTIVATING
            or journal.state.transaction is None
        ):
            raise RuntimeError("VM-HA activation resume requires an exact v4 checkpoint")
        state = journal.state
        transaction = t.cast(VMHAMigrationTransaction, state.transaction)
        assert transaction is not None
        if transaction.pending_effect is not None and not self._is_vm_ha_activation_effect(
            transaction.pending_effect
        ):
            raise RuntimeError("VM-HA activation has an unknown pending effect")
        if transaction.pending_effect is not None and transaction.observation_guard is not None:
            raise RuntimeError("VM-HA activation effect unexpectedly carries a cloud guard")

        try:
            route_targets = tuple(
                sorted(
                    (
                        VMHARouteTarget.model_validate(json.loads(value))
                        for value in state.route_targets
                    ),
                    key=lambda target: (
                        target.project_id,
                        target.network_id,
                        target.workload_subnet_id,
                        target.route_table_id,
                    ),
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("VM-HA lifecycle route targets are malformed") from error
        self._vm_ha_shared_allocation_id = state.allocation_id
        self._vm_ha_route_targets = route_targets
        self._vm_ha_effect_spec = spec
        self._vm_ha_effect_prefixes = local_prefixes

        before = self.observe_vm_ha_migration_state(spec, local_prefixes)
        normalized = normalize_vm_ha_observation(before)
        route_effect = "verify-active-forwarding-and-routes"
        route_changes_authorized = bool(
            transaction.pending_effect == route_effect
            or route_effect in transaction.completed_effects
        )
        if route_changes_authorized:
            current_non_route = self._without_route_observation(normalized)
            trusted_non_route = self._without_route_observation(transaction.observation)
            if current_non_route != trusted_non_route:
                changed_paths = ", ".join(
                    sorted(
                        vm_ha_observation_changed_paths(
                            trusted_non_route,
                            current_non_route,
                        )
                    )
                )
                raise RuntimeError(
                    f"VM-HA non-route cloud state changed during activation: {changed_paths}"
                )
        elif normalized != transaction.observation:
            current_without_revisions = self._without_route_revisions(normalized)
            trusted_without_revisions = self._without_route_revisions(transaction.observation)
            if current_without_revisions != trusted_without_revisions:
                raise RuntimeError("VM-HA cloud state changed before route activation")

        observed_bindings = self._observation_resource_bindings(before)
        if self._vm_ha_credentials is not None:
            observed_bindings.update(self._vm_ha_credentials.resource_bindings())
        for key, value in vm_ha_effective_resource_bindings(
            dict(transaction.resource_bindings)
        ).items():
            if key == "route-runtime-id":
                continue
            if observed_bindings.get(key) != value:
                raise RuntimeError(f"VM-HA activation resource identity drifted: {key}")

        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA activation resume requires the Nebius SDK")
        binding = self._build_vm_ha_runtime_binding(client, spec)
        if (
            binding.cluster_id != state.cluster_id
            or binding.shared_allocation_id != state.allocation_id
            or binding.route_runtime_id != state.route_runtime_id
            or self._serialized_route_targets(binding.route_targets) != state.route_targets
        ):
            raise RuntimeError("VM-HA activation runtime binding changed")
        bound_nodes = {node.node_id: node for node in binding.nodes}
        for member in state.members:
            node = bound_nodes.get(member.node_id)
            if (
                node is None
                or node.compute_id != member.compute_id
                or node.network_interface_name != member.network_interface_name
                or node.role.value != member.role
            ):
                raise RuntimeError(
                    f"VM-HA activation member binding changed: {member.instance_name}"
                )
        after = self.observe_vm_ha_migration_state(spec, local_prefixes)
        if normalize_vm_ha_observation(after) != normalized:
            raise RuntimeError("VM-HA cloud state changed during activation resume")
        return VMProvisioningResult(
            {member.instance_name: member.public_ip for member in state.members},
            vm_ha_runtime_binding=binding,
        )

    def get_ha_instance(self, instance_id: str) -> t.Any:
        """Read one Compute instance without permissive provisioning fallback."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            GetInstanceRequest,
            InstanceServiceClient,
        )

        return (
            InstanceServiceClient(client)
            .get(
                GetInstanceRequest(id=instance_id),
                **vm_ha_request_kwargs(),
            )
            .wait()
        )

    def _require_ha_compute_absent(self, instance_id: str) -> None:
        """Accept absence only from the typed lifecycle-bound Compute lookup."""

        try:
            self.get_ha_instance(instance_id)
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return
            raise RuntimeError(
                "VM-HA missing standby Compute identity could not be classified"
            ) from error
        raise RuntimeError("VM-HA lifecycle-bound standby Compute still exists")

    def _get_ha_instance_by_name(self, client: t.Any, name: str) -> t.Any:
        """Resolve one HA Compute identity without treating SDK errors as absence."""
        if not self.project_id:
            raise RuntimeError("VM-HA Compute lookup requires a project ID")
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
        from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

        instance = (
            InstanceServiceClient(client)
            .get_by_name(
                GetByNameRequest(parent_id=self.project_id, name=name),
                **vm_ha_request_kwargs(),
            )
            .wait()
        )
        metadata = getattr(instance, "metadata", None)
        instance_id = self._resource_id(instance)
        if (
            not instance_id
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"VM-HA Compute {name} returned an inexact identity")
        authoritative = self.get_ha_instance(instance_id)
        authoritative_metadata = getattr(authoritative, "metadata", None)
        if (
            self._resource_id(authoritative) != instance_id
            or str(getattr(authoritative_metadata, "name", "") or "") != name
            or str(getattr(authoritative_metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"VM-HA Compute {name} changed identity during re-read")
        return authoritative

    def _get_ha_disk_by_name(self, client: t.Any, name: str) -> t.Any | None:
        """Resolve one HA disk, treating only typed NOT_FOUND as absence."""

        if not self.project_id:
            raise RuntimeError("VM-HA disk lookup requires a project ID")
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
        from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

        try:
            disk = (
                DiskServiceClient(client)
                .get_by_name(
                    GetByNameRequest(parent_id=self.project_id, name=name),
                    **vm_ha_request_kwargs(),
                )
                .wait()
            )
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(f"VM-HA disk {name} could not be classified") from error
        metadata = getattr(disk, "metadata", None)
        if (
            not self._resource_id(disk)
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"VM-HA disk {name} returned an inexact identity")
        return disk

    def _get_ha_disk_by_id(self, client: t.Any, disk_id: str) -> t.Any | None:
        """Resolve one exact HA disk, treating only typed NOT_FOUND as absence."""

        if not disk_id:
            raise ValueError("VM-HA disk lookup requires an exact ID")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            DiskServiceClient,
            GetDiskRequest,
        )

        try:
            return (
                DiskServiceClient(client)
                .get(
                    GetDiskRequest(id=disk_id),
                    **vm_ha_request_kwargs(),
                )
                .wait()
            )
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError("VM-HA disk identity could not be classified") from error

    def _require_retained_allocation(
        self,
        allocation_client: t.Any,
        allocation_id: str,
        *,
        require_detached: bool,
        expected_owner: AllocationOwner | None = None,
    ) -> t.Any:
        """Reread one exact retained allocation without name-based adoption."""

        if require_detached and expected_owner is not None:
            raise ValueError("VM-HA retained allocation owner requirement is ambiguous")

        from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

        try:
            allocation = allocation_client.get(
                GetAllocationRequest(id=allocation_id),
                **vm_ha_request_kwargs(),
            ).wait()
        except Exception as error:
            raise RuntimeError(
                "VM-HA retained allocation disappeared during passive replacement"
            ) from error
        if self._resource_id(allocation) != allocation_id:
            raise RuntimeError("VM-HA retained allocation identity changed")
        parent_id = str(getattr(getattr(allocation, "metadata", None), "parent_id", "") or "")
        if parent_id and parent_id != (self.project_id or ""):
            raise RuntimeError("VM-HA retained allocation parent project changed")
        if expected_owner is not None:
            from .vm_ha_cloud import allocation_observation

            if allocation_observation(allocation_id, allocation).owner != expected_owner:
                raise RuntimeError(
                    "VM-HA retained allocation is not attached to the accepted Compute"
                )
        elif require_detached and self._allocation_is_attached(allocation):
            raise RuntimeError("VM-HA retained allocation did not detach from the retired passive")
        return allocation

    def stop_ha_instance(
        self,
        instance_id: str,
        operation_id: str | None = None,
    ) -> None:
        """Request a Compute stop without accepting scaffold-mode success."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            InstanceServiceClient,
            StopInstanceRequest,
        )

        operation = (
            InstanceServiceClient(client)
            .stop(
                StopInstanceRequest(id=instance_id),
                **vm_ha_request_kwargs(operation_id),
            )
            .wait()
        )
        self._sync_vm_ha_operation(operation)

    def get_ha_allocation(self, allocation_id: str) -> t.Any:
        """Read one allocation without converting access failure into absence."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationServiceClient,
            GetAllocationRequest,
        )

        return (
            AllocationServiceClient(client)
            .get(
                GetAllocationRequest(id=allocation_id),
                **vm_ha_request_kwargs(),
            )
            .wait()
        )

    def set_ha_private_alias(
        self,
        instance_id: str,
        network_interface_name: str,
        allocation_id: str,
        present: bool,
        operation_id: str | None = None,
    ) -> None:
        """Idempotently add or remove one exact secondary alias on one NIC."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            InstanceServiceClient,
            IPAlias,
            UpdateInstanceRequest,
        )

        from .vm_ha_cloud import (
            clone_nebius_sdk_message,
            network_interface_alias_allocation_ids,
        )

        instance = self.get_ha_instance(instance_id)
        original_spec = getattr(instance, "spec", None)
        original_metadata = getattr(instance, "metadata", None)
        if original_spec is None or original_metadata is None:
            raise RuntimeError(f"Compute instance {instance_id} has no mutable spec metadata")
        # Nebius SDK response messages are not deepcopy-safe: deserialized
        # metadata and repeated NIC fields are cleared by deepcopy.
        spec = clone_nebius_sdk_message(original_spec)
        metadata = clone_nebius_sdk_message(original_metadata)
        resource_version = str(getattr(metadata, "resource_version", "") or "")
        if not resource_version.isdecimal() or int(resource_version) <= 0:
            raise RuntimeError(
                f"Compute instance {instance_id} has no positive resource-version precondition"
            )

        interfaces = list(getattr(spec, "network_interfaces", []) or [])
        matching = [
            (index, interface)
            for index, interface in enumerate(interfaces)
            if str(getattr(interface, "name", "")) == network_interface_name
        ]
        if len(matching) != 1:
            raise RuntimeError(
                f"Compute instance {instance_id} must have exactly one NIC named "
                f"{network_interface_name}; found {len(matching)}"
            )

        index, interface = matching[0]
        current_ids = network_interface_alias_allocation_ids(
            interface,
            description=f"Compute instance {instance_id} NIC {network_interface_name}",
        )
        if (allocation_id in current_ids) is present:
            return

        updated_interface = clone_nebius_sdk_message(interface)
        desired_ids = (
            (*current_ids, allocation_id)
            if present
            else tuple(current_id for current_id in current_ids if current_id != allocation_id)
        )
        updated_interface.aliases = [
            IPAlias(allocation_id=current_id) for current_id in desired_ids
        ]
        interfaces[index] = updated_interface
        spec.network_interfaces = interfaces
        operation = (
            InstanceServiceClient(client)
            .update(
                UpdateInstanceRequest(metadata=metadata, spec=spec),
                **vm_ha_request_kwargs(operation_id),
            )
            .wait()
        )
        self._sync_vm_ha_operation(operation)

    def vm_ha_cloud_adapter(
        self,
        *,
        attempts: int = 10,
        poll_interval: float = 1.0,
        sleeper: t.Callable[[float], None] = time.sleep,
    ) -> VMHACloudAdapter:
        """Build the strict policy-facing adapter over the SDK translations."""
        from .vm_ha_cloud import VMHACloudAdapter

        return VMHACloudAdapter(
            instance_reader=self.get_ha_instance,
            instance_stopper=self.stop_ha_instance,
            allocation_reader=self.get_ha_allocation,
            alias_allocation_setter=self.set_ha_private_alias,
            attempts=attempts,
            poll_interval=poll_interval,
            sleeper=sleeper,
        )

    def _vm_ha_migration_route_bindings(
        self,
        spec: GatewayGroupSpec,
        route_targets: tuple[VMHARouteTarget, ...],
        shared_allocation_id: str,
    ) -> tuple[VMHAMigrationRouteBinding, ...]:
        """Recover exact approval-bound routes from the v4 journal.

        A later apply observes routes after ordinary-to-HA migration, when their
        next hop is already the shared allocation.  Carry those exact records to
        both members so either node can independently materialize the same
        durable route authority after exact cloud reproof.
        """

        journal = self._vm_ha_journal
        transaction = None if journal is None else journal.state.transaction
        if spec.vm_ha is None or transaction is None or not transaction.observation:
            return ()

        active_index = spec.vm_ha.active_instance_index
        member_path = f"/members/{active_index}"
        expected_instance_name = f"{spec.name}-{active_index}"
        observed_instance_name = self._normalized_observation_value(
            transaction.observation,
            f"{member_path}/instance_name",
        )
        primary_allocation_id = self._normalized_observation_value(
            transaction.observation,
            f"{member_path}/primary_allocation_id",
        )
        if (
            observed_instance_name != expected_instance_name
            or not isinstance(primary_allocation_id, str)
            or not primary_allocation_id
        ):
            raise RuntimeError("VM-HA approved active primary allocation is malformed")

        targets_by_table = {target.route_table_id: target for target in route_targets}
        route_indexes = sorted(
            {
                int(matched.group(1))
                for path, _value in transaction.observation
                if (matched := re.fullmatch(r"/routes/([0-9]+)/route_id", path)) is not None
            }
        )
        bindings: list[VMHAMigrationRouteBinding] = []
        for index in route_indexes:
            base = f"/routes/{index}"
            values = {
                field: self._normalized_observation_value(
                    transaction.observation,
                    f"{base}/{field}",
                )
                for field in (
                    "allocation_id",
                    "name",
                    "prefix",
                    "revision",
                    "route_id",
                    "route_table_id",
                )
            }
            if any(not isinstance(value, str) for value in values.values()):
                raise RuntimeError("VM-HA approved route observation is malformed")
            string_values = t.cast(dict[str, str], values)
            if string_values["allocation_id"] not in {
                primary_allocation_id,
                shared_allocation_id,
            }:
                continue
            prefix = string_values["prefix"]
            route_target = targets_by_table.get(string_values["route_table_id"])
            if route_target is None:
                raise RuntimeError("VM-HA approved migration route target is undeclared")
            legacy_name = f"vpngw-{prefix.replace('/', '-')}"[:63]
            if string_values["allocation_id"] == primary_allocation_id:
                if string_values["name"] != legacy_name:
                    continue
            elif string_values["name"] not in self._vm_ha_managed_route_names(
                cluster_id=spec.vm_ha.cluster_id,
                route_target=route_target,
                prefix=prefix,
                allocation_id=shared_allocation_id,
            ):
                continue
            try:
                bindings.append(
                    VMHAMigrationRouteBinding(
                        route_id=string_values["route_id"],
                        name=string_values["name"],
                        prefix=prefix,
                        allocation_id=string_values["allocation_id"],
                        resource_revision=string_values["revision"],
                        route_target=route_target,
                    )
                )
            except ValueError as error:
                raise RuntimeError("VM-HA approved migration route is malformed") from error
        return tuple(
            sorted(
                bindings,
                key=lambda route: (
                    route.route_target.route_table_id,
                    route.prefix,
                    route.route_id,
                ),
            )
        )

    @staticmethod
    def _vm_ha_managed_route_names(
        *,
        cluster_id: str,
        route_target: VMHARouteTarget,
        prefix: str,
        allocation_id: str,
    ) -> frozenset[str]:
        """Return exact canonical static/BGP names for one managed route identity."""

        names: set[str] = set()
        for route_kind in ("bgp", "static"):
            identity = (
                f"{cluster_id}:{route_target.route_table_id}:{prefix}:{route_kind}:{allocation_id}"
            )
            names.add(f"vpngw-ha-{hashlib.sha256(identity.encode()).hexdigest()[:24]}")
        return frozenset(names)

    def _build_vm_ha_runtime_binding(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
    ) -> VMHARuntimeBinding:
        vm_ha = spec.vm_ha
        allocation_id = self._vm_ha_shared_allocation_id
        route_targets = self._vm_ha_route_targets
        if vm_ha is None or not allocation_id or not route_targets:
            raise RuntimeError("VM-HA runtime binding requires complete provisioning intent")
        credential_set = self._vm_ha_credentials
        if credential_set is None or credential_set.project_id != self.project_id:
            raise RuntimeError("VM-HA runtime credential identity is unavailable")
        credentials_by_node: dict[str, VMHACredentialIdentity] = credential_set.by_node()
        if set(credentials_by_node) != {member.node_id for member in vm_ha.members}:
            raise RuntimeError("VM-HA runtime credential member identity is incomplete")
        allocation = self.get_ha_allocation(allocation_id)
        if self._resource_id(allocation) != allocation_id:
            raise RuntimeError("VM-HA shared allocation changed identity during final re-read")
        from .vm_ha_cloud import (
            AllocationOwner,
            allocation_observation,
            network_interface_alias_allocation_ids,
        )

        allocation_owner = allocation_observation(allocation_id, allocation).owner

        nodes: list[VMHARuntimeNodeBinding] = []
        alias_owners: list[AllocationOwner] = []
        for member in vm_ha.members:
            credential = credentials_by_node[member.node_id]
            instance_name = f"{spec.name}-{member.instance_index}"
            instance = self._get_ha_instance_by_name(client, instance_name)
            compute_id = self._resource_id(instance)
            if not compute_id:
                raise RuntimeError(f"VM-HA Compute identity unavailable for {instance_name}")
            interfaces = list(
                getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
            )
            if len(interfaces) != 1 or not getattr(interfaces[0], "name", None):
                raise RuntimeError(f"VM-HA {instance_name} must have one authoritative NIC")
            interface_name = str(interfaces[0].name)
            alias_allocation_ids = network_interface_alias_allocation_ids(
                interfaces[0],
                description=f"VM-HA {instance_name} NIC {interface_name}",
            )
            if allocation_id in alias_allocation_ids:
                alias_owners.append(AllocationOwner(compute_id, interface_name))
            status_interfaces = list(
                getattr(getattr(instance, "status", None), "network_interfaces", []) or []
            )
            if len(status_interfaces) != 1:
                raise RuntimeError(f"VM-HA {instance_name} has ambiguous runtime NIC state")
            address = getattr(getattr(status_interfaces[0], "ip_address", None), "address", None)
            endpoint_address = self._normalize_ip_value(str(address)) if address else None
            if not endpoint_address:
                raise RuntimeError(f"VM-HA {instance_name} has no authoritative peer endpoint")
            nodes.append(
                VMHARuntimeNodeBinding(
                    node_id=member.node_id,
                    role=VMHARole(member.role.value),
                    compute_id=compute_id,
                    network_interface_name=interface_name,
                    peer_endpoint=f"{endpoint_address}:9443",
                    nebius_credentials_path=installed_vm_ha_credential_path(
                        node_id=member.node_id,
                        generation_id=vm_ha.generation.generation_id,
                        credential_sha256=credential.credential_sha256,
                    ),
                    nebius_credentials_sha256=credential.credential_sha256,
                )
            )

        if len(alias_owners) != 1:
            raise RuntimeError("VM-HA passive Compute NIC conflicts with shared alias ownership")
        if allocation_owner != alias_owners[0]:
            raise RuntimeError("VM-HA shared allocation owner does not match the exact member NIC")
        active = next(node for node in nodes if node.role is VMHARole.ACTIVE)
        configured_active_owner = AllocationOwner(
            active.compute_id,
            active.network_interface_name,
        )
        if allocation_owner != configured_active_owner and (
            allocation_owner is None
            or not self._is_retained_vm_ha_owner(allocation_id, allocation_owner)
        ):
            raise RuntimeError("VM-HA shared allocation is not exact on configured active")
        final_allocation = self.get_ha_allocation(allocation_id)
        if self._resource_id(final_allocation) != allocation_id or (
            allocation_observation(allocation_id, final_allocation).owner != allocation_owner
        ):
            raise RuntimeError("VM-HA shared allocation ownership changed during final binding")

        digests = vm_ha.generation.digests
        return VMHARuntimeBinding(
            cluster_id=vm_ha.cluster_id,
            shared_allocation_id=allocation_id,
            nodes=t.cast(tuple[VMHARuntimeNodeBinding, VMHARuntimeNodeBinding], tuple(nodes)),
            route_targets=route_targets,
            migration_routes=self._vm_ha_migration_route_bindings(
                spec, route_targets, allocation_id
            ),
            route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
                vm_ha.cluster_id, allocation_id, route_targets
            ),
            generation_id=vm_ha.generation.generation_id,
            configuration_digest=digests.configuration,
            static_routes_digest=digests.static_routes,
            bgp_policy_digest=digests.bgp_policy,
            nebius_project_id=credential_set.project_id,
            nebius_service_account_id=credential_set.service_account_id,
            nebius_authorized_key_id=credential_set.authorized_key_id,
        )

    @staticmethod
    def _instance_boot_disk_id(instance: t.Any) -> str:
        boot_disk = getattr(getattr(instance, "spec", None), "boot_disk", None)
        existing = getattr(boot_disk, "existing_disk", None)
        return str(getattr(existing, "id", None) or getattr(boot_disk, "disk_id", None) or "")

    @staticmethod
    def _metadata_revision(resource: t.Any) -> str:
        return str(getattr(getattr(resource, "metadata", None), "resource_version", "") or "")

    def observe_vm_ha_migration_state(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> dict[str, object]:
        """Return a canonical, secret-free approval observation of cloud truth."""

        if spec.vm_ha is None or not self.project_id:
            raise RuntimeError("VM-HA approval observation requires exact HA intent")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA approval observation requires the Nebius SDK")
        existing = self._discover_vm_ha_members(client, spec)
        from .vm_ha_cloud import allocation_observation, network_interface_alias_allocation_ids

        members: list[dict[str, object]] = []
        for member in sorted(spec.vm_ha.members, key=lambda item: item.instance_index):
            name = f"{spec.name}-{member.instance_index}"
            observed = existing.get(name)
            if observed is None:
                members.append({"instance_name": name, "present": False})
                continue
            instance, public_ip = observed
            metadata = getattr(instance, "metadata", None)
            interfaces = list(
                getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
            )
            if len(interfaces) != 1:
                raise RuntimeError(f"VM-HA approval found ambiguous NICs on {name}")
            interface = interfaces[0]
            nic_name = str(getattr(interface, "name", "") or "")
            if not nic_name:
                raise RuntimeError(f"VM-HA approval found an unnamed NIC on {name}")
            members.append(
                {
                    "aliases": list(
                        network_interface_alias_allocation_ids(
                            interface,
                            description=f"VM-HA approval {name} NIC {nic_name}",
                        )
                    ),
                    "boot_disk_id": self._instance_boot_disk_id(instance),
                    "compute_id": self._resource_id(instance),
                    "compute_revision": self._metadata_revision(instance),
                    "instance_name": name,
                    "network_interface_name": nic_name,
                    "parent_id": str(getattr(metadata, "parent_id", "") or ""),
                    "primary_allocation_id": str(
                        getattr(getattr(interface, "ip_address", None), "allocation_id", "") or ""
                    ),
                    "public_allocation_id": str(
                        getattr(
                            getattr(interface, "public_ip_address", None),
                            "allocation_id",
                            "",
                        )
                        or ""
                    ),
                    "public_ip": public_ip,
                    "subnet_id": str(getattr(interface, "subnet_id", "") or ""),
                    "state": instance_cloud_state(instance).value,
                    "present": True,
                }
            )

        allocation_name = f"{spec.name}-{spec.vm_ha.cluster_id}-shared-private-ip"
        allocation = self._find_ha_allocation_by_name(
            self._resolve_client_apis(client)[3], allocation_name
        )
        shared: dict[str, object] = {
            "allocation_name": allocation_name,
            "present": allocation is not None,
        }
        if allocation is not None:
            allocation_id = self._resource_id(allocation)
            if not allocation_id:
                raise RuntimeError("VM-HA approval allocation has no identity")
            metadata = getattr(allocation, "metadata", None)
            allocation_spec = getattr(allocation, "spec", None)
            private = getattr(allocation_spec, "ipv4_private", None)
            public_present = _protobuf_field_present(allocation_spec, "ipv4_public")
            owner = allocation_observation(allocation_id, allocation).owner
            shared.update(
                {
                    "allocation_id": allocation_id,
                    "parent_id": str(getattr(metadata, "parent_id", "") or ""),
                    "private_subnet_id": str(getattr(private, "subnet_id", "") or ""),
                    "public_shape_present": public_present,
                    "resource_revision": self._metadata_revision(allocation),
                    "owner": (
                        None
                        if owner is None
                        else {
                            "compute_id": owner.instance_id,
                            "network_interface_name": owner.network_interface_name,
                        }
                    ),
                }
            )

        route_targets = self._resolve_vm_ha_route_targets(client, spec, local_prefixes)
        from .route_manager import NebiusSDKRouteBackend, RouteManager

        backend = NebiusSDKRouteBackend(client)
        route_rows: list[dict[str, object]] = []
        for target in route_targets:
            backend.verify_target(target)
            for route in backend._raw_routes(target.route_table_id):
                prefix = RouteManager._route_destination_network(route)
                labels = backend._route_labels(route)
                route_rows.append(
                    {
                        "allocation_id": str(
                            RouteManager._route_next_hop_allocation_id(route) or ""
                        ),
                        "authority_labels": {
                            key: labels[key]
                            for key in sorted(backend._AUTHORITY_LABEL_KEYS)
                            if key in labels
                        },
                        "name": RouteManager._metadata_name(route),
                        "prefix": "" if prefix is None else str(prefix),
                        "route_id": RouteManager._metadata_id(route),
                        "route_table_id": target.route_table_id,
                        "revision": self._metadata_revision(route),
                    }
                )
        route_rows.sort(
            key=lambda item: (
                str(item["route_table_id"]),
                str(item["prefix"]),
                str(item["route_id"]),
            )
        )
        return {
            "members": members,
            "project_id": self.project_id,
            "routes": route_rows,
            "route_targets": [target.model_dump(mode="json") for target in route_targets],
            "shared_allocation": shared,
        }

    def build_vm_ha_lifecycle_members(
        self,
        spec: GatewayGroupSpec,
        public_targets: t.Mapping[str, str],
    ) -> tuple[VMHALifecycleMember, VMHALifecycleMember]:
        """Authoritatively reread every persisted member identity after provisioning."""

        if spec.vm_ha is None:
            raise RuntimeError("VM-HA lifecycle binding requires explicit HA")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA lifecycle binding requires the Nebius SDK")
        from .vm_ha_cloud import network_interface_alias_allocation_ids

        members: list[VMHALifecycleMember] = []
        for member in sorted(spec.vm_ha.members, key=lambda item: item.instance_index):
            name = f"{spec.name}-{member.instance_index}"
            instance = self._get_ha_instance_by_name(client, name)
            interfaces = list(
                getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
            )
            if len(interfaces) != 1:
                raise RuntimeError(f"VM-HA lifecycle found ambiguous NICs on {name}")
            interface = interfaces[0]
            members.append(
                VMHALifecycleMember(
                    instance_index=member.instance_index,
                    instance_name=name,
                    node_id=member.node_id,
                    role=member.role.value,
                    compute_id=self._resource_id(instance) or "",
                    network_interface_name=str(getattr(interface, "name", "") or ""),
                    public_ip=str(public_targets.get(name, "") or ""),
                    compute_revision=self._metadata_revision(instance),
                    disk_id=self._instance_boot_disk_id(instance),
                    network_interface_subnet_id=str(getattr(interface, "subnet_id", "") or ""),
                    primary_allocation_id=str(
                        getattr(getattr(interface, "ip_address", None), "allocation_id", "") or ""
                    ),
                    public_allocation_id=str(
                        getattr(
                            getattr(interface, "public_ip_address", None),
                            "allocation_id",
                            "",
                        )
                        or ""
                    ),
                    alias_allocation_ids=network_interface_alias_allocation_ids(
                        interface,
                        description=f"VM-HA lifecycle {name}",
                    ),
                )
            )
        return t.cast(tuple[VMHALifecycleMember, VMHALifecycleMember], tuple(members))

    def finalize_vm_ha_provisioning(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
        public_targets: t.Mapping[str, str],
    ) -> tuple[VMHALifecycleMember, VMHALifecycleMember]:
        """Freeze one stable, transaction-bound aggregate before activation staging."""

        self._vm_ha_effect_spec = spec
        self._vm_ha_effect_prefixes = local_prefixes
        before = self._stable_vm_ha_effect_observation()
        self._verify_vm_ha_transaction_preconditions(spec, local_prefixes)
        members = self.build_vm_ha_lifecycle_members(spec, public_targets)
        after = self._stable_vm_ha_effect_observation()
        if before != after:
            raise RuntimeError("VM-HA cloud state changed during final provisioning proof")
        journal = self._vm_ha_journal
        if (
            journal is None
            or journal.state.transaction is None
            or normalize_vm_ha_observation(after) != journal.state.transaction.observation
        ):
            raise RuntimeError("VM-HA final provisioning proof does not match the transaction")
        return members

    def _resolve_vm_ha_route_targets(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> tuple[VMHARouteTarget, ...]:
        if not self.project_id:
            raise RuntimeError("VM-HA route targets require an exact project ID")
        if not local_prefixes:
            raise RuntimeError("VM-HA route targets require gateway.local_prefixes")
        _, network_id, _, subnet_client = self._resolve_gateway_network(client, spec)
        from nebius.api.nebius.vpc.v1 import ListSubnetsByNetworkRequest  # type: ignore

        from .route_manager import RouteManager

        route_manager = RouteManager(project_id=self.project_id)

        def observe() -> tuple[VMHARouteTarget, ...]:
            subnets = collect_nebius_pages(
                lambda page_token: subnet_client.list_by_network(
                    ListSubnetsByNetworkRequest(
                        network_id=network_id,
                        page_size=1000,
                        page_token=page_token,
                    ),
                    **vm_ha_request_kwargs(),
                ),
                context="VM HA route subnet",
                item_identity=nebius_resource_id,
            )
            return route_manager.resolve_vm_ha_route_targets(
                subnets,
                local_prefixes,
                project_id=self.project_id or "",
                target_network_id=network_id,
                gateway_subnet_name=str(self._gateway_subnet_settings(spec)["name"]),
            )

        first = observe()
        if observe() != first:
            raise RuntimeError("VM-HA route target membership changed during binding")
        return first

    def _attach_vm_ha_shared_allocation_initially(
        self,
        *,
        allocation_id: str,
        active_compute_id: str,
        active_network_interface_name: str,
    ) -> None:
        """Attach only from detached state; provisioning never performs a takeover."""
        from .vm_ha_cloud import allocation_observation

        expected = AllocationOwner(active_compute_id, active_network_interface_name)
        effect = "attach-shared-allocation-active"
        operation_id = self._begin_vm_ha_effect(effect)
        observed = allocation_observation(
            allocation_id,
            self.get_ha_allocation(allocation_id),
        ).owner
        if observed == expected:
            self._complete_vm_ha_effect(
                effect,
                resource_updates={
                    "shared-allocation-owner-compute": active_compute_id,
                    "shared-allocation-owner-nic": active_network_interface_name,
                },
            )
            return
        if observed is not None:
            if self._is_retained_vm_ha_owner(allocation_id, observed):
                self._complete_vm_ha_effect(
                    effect,
                    resource_updates={
                        "shared-allocation-owner-compute": observed.instance_id,
                        "shared-allocation-owner-nic": observed.network_interface_name,
                    },
                )
                return
            raise RuntimeError(
                "VM-HA shared allocation is already attached outside configured active"
            )
        self.set_ha_private_alias(
            active_compute_id,
            active_network_interface_name,
            allocation_id,
            True,
            operation_id,
        )
        confirmed = allocation_observation(
            allocation_id,
            self.get_ha_allocation(allocation_id),
        ).owner
        if confirmed != expected:
            raise RuntimeError("VM-HA shared allocation attachment postcondition was not observed")
        self._complete_vm_ha_effect(
            effect,
            resource_updates={
                "shared-allocation-owner-compute": active_compute_id,
                "shared-allocation-owner-nic": active_network_interface_name,
            },
        )

    def _is_retained_vm_ha_owner(
        self,
        allocation_id: str,
        owner: t.Any,
    ) -> bool:
        """Accept one exact transaction-bound owner during managed apply/resume."""

        journal = self._vm_ha_journal
        if (
            journal is None
            or journal.state.status
            not in {
                VMHALifecycleStatus.PROVISIONING,
                VMHALifecycleStatus.ACTIVATING,
            }
            or journal.state.allocation_id != allocation_id
            or journal.state.transaction is None
            or journal.state.transaction.predecessor_sha256 is None
        ):
            return False
        compute_id = str(getattr(owner, "instance_id", "") or "")
        nic_name = str(getattr(owner, "network_interface_name", "") or "")
        if not compute_id or not nic_name:
            return False
        bindings = vm_ha_effective_resource_bindings(
            dict(journal.state.transaction.resource_bindings)
        )
        if (
            bindings.get("shared-allocation-id") != allocation_id
            or bindings.get("shared-allocation-owner-compute") != compute_id
            or bindings.get("shared-allocation-owner-nic") != nic_name
        ):
            return False
        return any(
            member.compute_id == compute_id and member.network_interface_name == nic_name
            for member in journal.state.members
        )

    def check_changes(self, spec: GatewayGroupSpec) -> list[tuple[str, t.Any]]:
        """Check what changes would be applied without making them.

        Returns:
            List of (instance_name, VMDiff) tuples for all instances
        """
        print(f"[VMManager] Checking changes for {spec.instance_count} instance(s)...")

        # Setup SDK client (reuse logic from ensure_group)
        client = self._get_client()
        if client is None:
            print("[VMManager] Cannot check changes: SDK not available")
            return []

        results: list[tuple[str, t.Any]] = []
        desired_spec = VMSpec.from_config(spec.vm_spec)

        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"

            # Try to get existing VM and disk
            vm_obj = self._get_vm_by_name(client, inst_name)

            if vm_obj is None:
                # VM doesn't exist
                diff = self.diff_analyzer.compare(desired_spec, None)
                results.append((inst_name, diff))
                continue

            # Resolve an HA boot disk from the Compute attachment. Replacement
            # disks are cycle-qualified, while retired canonical disks remain
            # intentionally untouched.
            if spec.vm_ha is not None:
                boot_disk_id = self._instance_boot_disk_id(vm_obj)
                disk_obj = self._get_ha_disk_by_id(client, boot_disk_id) if boot_disk_id else None
            else:
                boot_disk_name = f"{inst_name}-boot"
                disk_obj = self._get_disk_by_name(client, boot_disk_name)

            if disk_obj is None:
                print(f"[VMManager] Warning: VM {inst_name} exists but boot disk not found")
                diff = self.diff_analyzer.compare(desired_spec, None)
                results.append((inst_name, diff))
                continue

            # Extract actual spec from live resources
            actual_spec = VMSpec.from_live_vm(vm_obj, disk_obj)

            # Compare
            diff = self.diff_analyzer.compare(desired_spec, actual_spec)
            results.append((inst_name, diff))

        return results

    def _get_client(self) -> t.Any | None:
        """Get Nebius SDK client (extracted from ensure_group for reuse)."""
        return self._build_sdk_client(self.region or self.region_id or "")

    def _get_vm_by_name(self, client: t.Any, name: str) -> t.Any | None:
        """Get one exact VM by name, treating only typed NOT_FOUND as absence."""
        if not self.project_id:
            raise RuntimeError("Gateway VM lookup requires an exact project ID")
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

            isc = InstanceServiceClient(client)
            vm = isc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=name)).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(f"Gateway VM {name!r} could not be classified") from error
        metadata = getattr(vm, "metadata", None)
        if (
            not self._resource_id(vm)
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"Gateway VM {name!r} returned an inexact identity")
        return vm

    def _get_disk_by_name(self, client: t.Any, name: str) -> t.Any | None:
        """Get one exact disk by name, treating only typed NOT_FOUND as absence."""
        if not self.project_id:
            raise RuntimeError("Boot disk lookup requires an exact project ID")
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

            dsc = DiskServiceClient(client)
            disk = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=name)).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(f"Boot disk {name!r} could not be classified") from error
        metadata = getattr(disk, "metadata", None)
        if (
            not self._resource_id(disk)
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"Boot disk {name!r} returned an inexact identity")
        return disk

    @staticmethod
    def _gateway_subnet_settings(spec: GatewayGroupSpec) -> dict[str, t.Any]:
        subnet_cfg = spec.subnet or {}
        prefix_length_value = subnet_cfg.get("prefix_length")
        return {
            "name": str(subnet_cfg.get("name") or "vpngw-subnet").strip() or "vpngw-subnet",
            "cidr": str(subnet_cfg.get("cidr")).strip() if subnet_cfg.get("cidr") else None,
            "prefix_length": int(prefix_length_value) if prefix_length_value is not None else 24,
        }

    @staticmethod
    def _gateway_subnet_name(spec: GatewayGroupSpec) -> str:
        return str(VMManager._gateway_subnet_settings(spec)["name"])

    @staticmethod
    def _gateway_route_table_name(subnet_name: str) -> str:
        return f"{subnet_name}-routing-table"[:63]

    @staticmethod
    def _extract_explicit_subnet_networks(subnet_obj: t.Any) -> list[ipaddress.IPv4Network]:
        subnet_spec = getattr(subnet_obj, "spec", None)
        ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
        if not ipv4_private_pools or getattr(ipv4_private_pools, "use_network_pools", False):
            return []

        networks: list[ipaddress.IPv4Network] = []
        for pool in getattr(ipv4_private_pools, "pools", []) or []:
            for cidr_obj in getattr(pool, "cidrs", []) or []:
                cidr = getattr(cidr_obj, "cidr", None)
                if not cidr:
                    continue
                network = _parse_ipv4_network(str(cidr))
                if network is not None:
                    networks.append(network)
        return networks

    def _get_network_private_pools(
        self, client: t.Any, network_obj: t.Any
    ) -> list[tuple[str, t.Any]]:
        from nebius.api.nebius.vpc.v1 import GetPoolRequest, PoolServiceClient  # type: ignore

        net_spec = getattr(network_obj, "spec", None)
        pool_refs = getattr(getattr(net_spec, "ipv4_private_pools", None), "pools", []) or []
        pool_client = PoolServiceClient(client)  # type: ignore

        pools: list[tuple[str, t.Any]] = []
        for pool_ref in pool_refs:
            pool_id = getattr(pool_ref, "pool_id", None) or getattr(pool_ref, "id", None)
            if not pool_id:
                continue
            pool_id = str(pool_id)
            pool_obj = pool_client.get(GetPoolRequest(id=pool_id)).wait()
            pools.append((pool_id, pool_obj))
        return pools

    def _ensure_network_pool_contains_cidr(
        self,
        client: t.Any,
        network_obj: t.Any,
        desired_network: ipaddress.IPv4Network,
        *,
        strict: bool = False,
    ) -> None:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (
            GetPoolRequest,
            PoolCidr,
            PoolServiceClient,
            PoolSpec,
            UpdatePoolRequest,
        )  # type: ignore

        network_pools = self._get_network_private_pools(client, network_obj)
        for _, pool_obj in network_pools:
            for pool_cidr_obj in getattr(getattr(pool_obj, "spec", None), "cidrs", []) or []:
                pool_cidr = getattr(pool_cidr_obj, "cidr", None)
                if not pool_cidr:
                    continue
                pool_network = _parse_ipv4_network(str(pool_cidr))
                if pool_network is None:
                    continue
                if desired_network.subnet_of(pool_network):
                    return

        if len(network_pools) != 1:
            raise RuntimeError(
                f"Gateway subnet CIDR {desired_network} is outside the current network pool(s). "
                "Automatic pool extension is supported only when the target network has exactly "
                "one private pool. Extend the network pool manually or specify gateway_group.network_id "
                "for a network with a single private pool."
            )

        pool_id, pool_obj = network_pools[0]
        pool_client = PoolServiceClient(client)  # type: ignore
        if strict:
            try:
                pool_obj = pool_client.get(GetPoolRequest(id=pool_id)).wait()
            except Exception as error:
                raise RuntimeError(
                    "Network private pool could not be reread before extension"
                ) from error
        pool_meta = getattr(pool_obj, "metadata", None)
        pool_spec = getattr(pool_obj, "spec", None)
        if not pool_meta or not pool_spec:
            raise RuntimeError(f"Network private pool {pool_id} is missing metadata/spec.")
        if strict:
            if self._resource_id(pool_obj) != pool_id:
                raise RuntimeError("Network private pool changed identity before extension")
            if str(getattr(pool_meta, "parent_id", "") or "") != (self.project_id or ""):
                raise RuntimeError("Network private pool belongs to a different project")
            if int(getattr(pool_meta, "resource_version", 0) or 0) < 1:
                raise RuntimeError("Network private pool has no authoritative resource version")

        preserved_cidrs = {
            str(network)
            for pool_cidr in getattr(pool_spec, "cidrs", []) or []
            if (network := _parse_ipv4_network(str(getattr(pool_cidr, "cidr", "") or "")))
            is not None
        }
        updated_cidrs = [
            PoolCidr(
                cidr=getattr(pool_cidr, "cidr", ""),
                max_mask_length=getattr(pool_cidr, "max_mask_length", 0),
                state=getattr(pool_cidr, "state", None),
            )
            for pool_cidr in getattr(pool_spec, "cidrs", []) or []
        ]
        updated_cidrs.append(PoolCidr(cidr=str(desired_network)))

        updated_spec = PoolSpec(
            cidrs=updated_cidrs,
            version=getattr(pool_spec, "version", None),
            visibility=getattr(pool_spec, "visibility", None),
            source_pool_id=getattr(pool_spec, "source_pool_id", ""),
        )

        pool_name = getattr(pool_meta, "name", None) or pool_id
        print(
            f"[VMManager] Extending network private pool '{pool_name}' with {desired_network} ..."
        )

        update_operation = pool_client.update(
            UpdatePoolRequest(
                metadata=ResourceMetadata(
                    id=getattr(pool_meta, "id", pool_id),
                    parent_id=getattr(pool_meta, "parent_id", ""),
                    name=getattr(pool_meta, "name", ""),
                    resource_version=int(getattr(pool_meta, "resource_version", 0) or 0),
                ),
                spec=updated_spec,
            )
        ).wait()
        if not strict:
            return
        self._sync_preparation_operation(
            update_operation,
            action="Network private pool extension",
        )
        try:
            verified_pool = pool_client.get(GetPoolRequest(id=pool_id)).wait()
        except Exception as error:
            raise RuntimeError("Extended network private pool could not be reread") from error
        if self._resource_id(verified_pool) != pool_id:
            raise RuntimeError("Extended network private pool changed identity")
        verified_meta = getattr(verified_pool, "metadata", None)
        if str(getattr(verified_meta, "parent_id", "") or "") != (self.project_id or ""):
            raise RuntimeError("Extended network private pool belongs to a different project")
        verified_spec = getattr(verified_pool, "spec", None)
        verified_cidrs = {
            str(network)
            for pool_cidr in getattr(verified_spec, "cidrs", []) or []
            if (network := _parse_ipv4_network(str(getattr(pool_cidr, "cidr", "") or "")))
            is not None
        }
        required_cidrs = preserved_cidrs | {str(desired_network)}
        if not required_cidrs.issubset(verified_cidrs):
            raise RuntimeError(
                "Network private pool extension did not preserve every observed CIDR"
            )

    @staticmethod
    def _find_first_free_subnet_cidr(
        network_pool_cidrs: list[ipaddress.IPv4Network],
        existing_subnet_cidrs: list[ipaddress.IPv4Network],
        *,
        prefix_length: int,
    ) -> str | None:
        for pool_network in network_pool_cidrs:
            if prefix_length < pool_network.prefixlen:
                continue

            candidates: t.Iterable[ipaddress.IPv4Network]
            if prefix_length == pool_network.prefixlen:
                candidates = [pool_network]
            else:
                candidates = pool_network.subnets(new_prefix=prefix_length)

            for candidate in candidates:
                if any(candidate.overlaps(existing) for existing in existing_subnet_cidrs):
                    continue
                return str(candidate)

        return None

    def get_vm_public_ip(self, vm_name: str) -> str | None:
        """Get the public IP address of a VM by querying its network interfaces.

        Args:
            vm_name: Name of the VM instance

        Returns:
            Public IP address string, or None if not found
        """
        try:
            client = self._get_client()
            if client is None:
                return None

            vm_obj = self._get_vm_by_name(client, vm_name)
            if vm_obj is None:
                return None

            # Try to get public IP from status.network_interfaces first (actual assigned IP)
            status = getattr(vm_obj, "status", None)
            if status is not None:
                network_interfaces = getattr(status, "network_interfaces", [])
                if network_interfaces:
                    first_nic = network_interfaces[0]
                    pub_ip_addr = getattr(first_nic, "public_ip_address", None)
                    if pub_ip_addr is not None:
                        address = getattr(pub_ip_addr, "address", None)
                        if address:
                            # Strip CIDR suffix if present (e.g., "66.201.7.110/32" -> "66.201.7.110")
                            ip_str = str(address).split("/")[0]
                            return ip_str

            # Fallback: check spec.network_interfaces (configured IP)
            spec = getattr(vm_obj, "spec", None)
            if spec is not None:
                network_interfaces = getattr(spec, "network_interfaces", [])
                if network_interfaces:
                    first_nic = network_interfaces[0]
                    pub_ip_addr = getattr(first_nic, "public_ip_address", None)
                    if pub_ip_addr is not None:
                        address = getattr(pub_ip_addr, "address", None)
                        if address:
                            # Strip CIDR suffix if present
                            ip_str = str(address).split("/")[0]
                            return ip_str
        except Exception:
            pass
        return None

    def get_allocation_ip(self, allocation_id: str) -> str | None:
        """Get the IP address from an allocation.

        Args:
            allocation_id: The allocation ID

        Returns:
            IP address string, or None if not found
        """
        try:
            client = self._get_client()
            if client is None:
                return None

            from nebius.api.nebius.vpc.v1 import (
                AllocationServiceClient,  # type: ignore
                GetAllocationRequest,  # type: ignore
            )

            asc = AllocationServiceClient(client)
            alloc = asc.get(GetAllocationRequest(id=allocation_id)).wait()

            # Try to extract IP from allocation
            spec = getattr(alloc, "spec", None)
            if spec:
                ipv4_public = getattr(spec, "ipv4_public", None)
                if ipv4_public:
                    address = getattr(ipv4_public, "address", None)
                    if address:
                        return str(address)
        except Exception:
            pass
        return None

    def wait_for_vm_network(
        self,
        vm_name: str,
        ip_address: str,
        timeout: int = 180,
        *,
        progress_callback: t.Callable[[], None] | None = None,
    ) -> bool:
        """Wait for VM to be reachable via ping.

        Args:
            vm_name: Name of the VM instance
            ip_address: IP address to ping
            timeout: Maximum seconds to wait (default 180)

        Returns:
            True if VM became reachable, False if timeout
        """
        import subprocess
        import time

        print(f"[VMManager] Waiting for {vm_name} ({ip_address}) to be reachable...")
        start_time = time.time()
        attempt = 0
        printed_progress = False

        while time.time() - start_time < timeout:
            attempt += 1
            try:
                # Ping with 1 second timeout, 1 packet
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", ip_address],
                    capture_output=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    elapsed = int(time.time() - start_time)
                    if printed_progress:
                        print()  # finish the progress line
                    print(f"[VMManager] ✓ {vm_name} is reachable (took {elapsed}s)")
                    return True
                else:
                    # Show progress
                    if attempt % 3 == 0:  # Every 3 attempts
                        print(".", end="", flush=True)
                        printed_progress = True
            except Exception:
                pass

            time.sleep(1)
            if progress_callback is not None:
                progress_callback()

        print(f"\n✗ Timeout waiting for {vm_name} to become reachable")
        return False

    def get_vm_allocations(self, vm_name: str) -> list[tuple[int, str]]:
        """Get allocation IDs attached to a VM's network interfaces.

        Args:
            vm_name: Name of the VM instance

        Returns:
            List of (nic_index, allocation_id) tuples
        """
        allocations: list[tuple[int, str]] = []
        try:
            client = self._get_client()
            if client is None:
                return allocations

            vm_obj = self._get_vm_by_name(client, vm_name)
            if vm_obj is None:
                return allocations

            # Extract allocation IDs from network interfaces
            spec = getattr(vm_obj, "spec", None)
            if spec is None:
                return allocations

            network_interfaces = getattr(spec, "network_interfaces", [])
            for idx, nic in enumerate(network_interfaces):
                pub_ip_addr = getattr(nic, "public_ip_address", None)
                if pub_ip_addr:
                    alloc_id = getattr(pub_ip_addr, "allocation_id", None)
                    if alloc_id:
                        allocations.append((idx, str(alloc_id)))
        except Exception:
            pass
        return allocations

    def check_vm_health(
        self,
        vm_name: str,
        public_ip: str,
        *,
        username: str = "ubuntu",
    ) -> dict:
        """Check if VM bootstrap completed and services are running.

        Args:
            vm_name: Name of the VM instance
            public_ip: Public IP address to connect to
            username: Configured SSH management username

        Returns:
            Dict with health status: {
                'reachable': bool,
                'cloud_init_complete': bool,
                'strongswan_installed': bool,
                'frr_installed': bool,
                'agent_installed': bool,
                'esp4_ready': bool,
                'esp4_reboot_pending': bool,
                'message': str
            }
        """
        import subprocess
        import time

        result: dict[str, t.Any] = {
            "reachable": False,
            "cloud_init_complete": False,
            "strongswan_installed": False,
            "frr_installed": False,
            "agent_installed": False,
            "esp4_ready": False,
            "esp4_reboot_pending": False,
            "message": "VM not reachable",
        }

        # Wait a moment for VM to boot and network to initialize
        time.sleep(2)
        ssh_base = build_openssh_base_command(
            key_path=(self._management_key_path if self._management_public_key is None else None),
            client_auth=self._require_ssh_client_auth(),
            connect_timeout=5,
            policy=self._ssh_policy,
            hostname=vm_name if self._ssh_policy is not None else None,
        )
        ssh_target = f"{username}@{public_ip}"

        # Test SSH connectivity
        try:
            ssh_test = subprocess.run(
                ssh_base
                + [
                    ssh_target,
                    "echo connected",
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            result["message"] = f"SSH connection failed: {e}"
            return result
        if ssh_test.returncode != 0:
            detail = ssh_test.stderr.decode(errors="replace").lower()
            if (
                "host key verification failed" in detail
                or "remote host identification has changed" in detail
            ):
                raise RuntimeError(f"SSH host identity verification failed for {public_ip}")
            result["message"] = "SSH not ready yet"
            return result
        result["reachable"] = True

        # Check cloud-init status
        try:
            cloud_init_check = subprocess.run(
                ssh_base
                + [
                    ssh_target,
                    "cloud-init status --wait --long 2>/dev/null || cloud-init status",
                ],
                capture_output=True,
                timeout=30,
                text=True,
            )
            if (
                "done" in cloud_init_check.stdout.lower()
                or "status: done" in cloud_init_check.stdout.lower()
            ):
                result["cloud_init_complete"] = True
                # Also verify pip module is available (critical for package installation)
                pip_check = subprocess.run(
                    ssh_base
                    + [
                        ssh_target,
                        "python3 -m pip --version 2>/dev/null",
                    ],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                if pip_check.returncode != 0:
                    # pip module not available yet, cloud-init might not be fully done
                    result["cloud_init_complete"] = False
        except Exception:
            pass

        if result["cloud_init_complete"]:
            try:
                esp4_check = subprocess.run(
                    ssh_base
                    + [
                        ssh_target,
                        (
                            "if [ -f /var/lib/nebius-vpngw/esp4-reboot-pending ]; then "
                            "echo reboot-pending; exit 75; "
                            "fi; "
                            "if [ -x /usr/local/bin/nebius-vpngw-esp4-preflight.sh ]; then "
                            "sudo /usr/local/bin/nebius-vpngw-esp4-preflight.sh --verify "
                            ">/dev/null 2>&1; "
                            "else "
                            "sudo modprobe esp4 >/dev/null 2>&1; "
                            "fi"
                        ),
                    ],
                    capture_output=True,
                    timeout=20,
                    text=True,
                )
                if esp4_check.returncode == 0:
                    result["esp4_ready"] = True
                elif esp4_check.returncode == 75 or "reboot-pending" in esp4_check.stdout:
                    result["esp4_reboot_pending"] = True
                    result["esp4_ready"] = False
            except Exception:
                result["esp4_ready"] = False

        # Check installed packages
        try:
            pkg_check = subprocess.run(
                ssh_base
                + [
                    ssh_target,
                    'dpkg -l strongswan frr 2>/dev/null | grep "^ii" && systemctl is-active nebius-vpngw-agent 2>/dev/null',
                ],
                capture_output=True,
                timeout=10,
                text=True,
            )
            if "strongswan" in pkg_check.stdout:
                result["strongswan_installed"] = True
            if "frr" in pkg_check.stdout:
                result["frr_installed"] = True
            if "active" in pkg_check.stdout:
                result["agent_installed"] = True
        except Exception:
            pass

        # Generate status message
        if (
            result["cloud_init_complete"]
            and result["strongswan_installed"]
            and result["frr_installed"]
            and result["esp4_ready"]
        ):
            result["message"] = (
                "✓ VM ready: cloud-init complete, strongSwan and FRR installed, ESP4 ready"
            )
            if result["agent_installed"]:
                result["message"] += ", agent running"
        elif result["esp4_reboot_pending"]:
            result["message"] = "⏳ ESP4/kernel update prepared; waiting for gateway reboot"
        elif result["cloud_init_complete"] and not result["esp4_ready"]:
            result["message"] = "⚠ Cloud-init complete but ESP4 is not ready"
        elif result["cloud_init_complete"]:
            result["message"] = "⚠ Cloud-init complete but packages not verified"
        else:
            result["message"] = "⏳ Cloud-init still running (packages being installed)"

        return result

    @staticmethod
    def _resolve_service_handle(obj: t.Any, name: str) -> t.Any:
        if obj is None:
            return None
        attr = getattr(obj, name, None)
        if attr is None:
            return None
        try:
            return attr() if callable(attr) else attr
        except Exception:
            return attr

    def _build_sdk_client(self, region: str) -> t.Any | None:
        del region
        if self._closed:
            raise RuntimeError("VMManager is closed")
        if self._sdk_client is not _SDK_CLIENT_UNSET:
            return self._sdk_client
        try:
            client = build_operator_sdk_client(explicit_token=self.auth_token)
            self._sdk_client = client
            return client
        except Exception as e:
            print(
                "[VMManager] Nebius SDK not available; install with 'pip install nebius'. "
                f"Running in dry scaffold mode: {e}"
            )
            return None

    def _resolve_client_apis(self, client: t.Any) -> tuple[t.Any, t.Any, t.Any, t.Any]:
        compute = self._resolve_service_handle(client, "compute") or self._resolve_service_handle(
            getattr(client, "cloud", None),
            "compute",
        )
        vpc = (
            self._resolve_service_handle(client, "vpc")
            or self._resolve_service_handle(getattr(client, "network", None), "vpc")
            or self._resolve_service_handle(getattr(client, "cloud", None), "vpc")
        )

        instance_api = None
        if compute is not None:
            for name in ("instance", "instances", "vm", "virtual_machine"):
                instance_api = getattr(compute, name, None)
                if instance_api is not None:
                    break

        disk_api = None
        if compute is not None:
            for name in ("disk", "disks", "storage_disk"):
                disk_api = getattr(compute, name, None)
                if disk_api is not None:
                    break

        alloc_api = None
        alloc_client = None
        try:
            from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore

            alloc_client = AllocationServiceClient(client)  # type: ignore
        except Exception:
            alloc_client = None

        if alloc_client is None and vpc is not None:
            for name in ("allocation", "allocations", "public_ip", "public_ips"):
                alloc_api = getattr(vpc, name, None)
                if alloc_api is not None:
                    break

        return instance_api, disk_api, alloc_api, alloc_client

    def _discover_existing_instances(self, client: t.Any, spec: GatewayGroupSpec) -> list[t.Any]:
        existing: list[t.Any] = []
        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            vm_obj = self._get_vm_by_name(client, inst_name)
            if vm_obj:
                existing.append(vm_obj)
        return existing

    def _get_vm_by_name_for_ordinary_ssh_preflight(
        self,
        client: t.Any,
        name: str,
    ) -> t.Any | None:
        if not self.project_id:
            raise RuntimeError("Gateway SSH discovery requires an exact project ID")
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

            response = (
                InstanceServiceClient(client)
                .get_by_name(GetByNameRequest(parent_id=self.project_id, name=name))
                .wait()
            )
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            if nebius_request_error_code_is(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                raise RuntimeError(
                    "Gateway cloud authentication failed during SSH trust discovery"
                ) from error
            raise RuntimeError(
                f"Gateway VM {name} could not be classified as existing or fresh"
            ) from error
        if response is None:
            raise RuntimeError(f"Gateway SSH discovery returned no result for {name}")
        metadata = getattr(response, "metadata", None)
        if (
            not self._resource_id(response)
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"Gateway VM {name} returned an inexact identity")
        return response

    @staticmethod
    def _vm_public_ip_from_object(vm_obj: t.Any) -> str | None:
        for owner in (getattr(vm_obj, "status", None), getattr(vm_obj, "spec", None)):
            interfaces = list(getattr(owner, "network_interfaces", []) or []) if owner else []
            if not interfaces:
                continue
            public = getattr(interfaces[0], "public_ip_address", None)
            address = getattr(public, "address", None) if public else None
            if address:
                return str(address).split("/", 1)[0]
        return None

    def _get_vm_by_name_for_vm_ha_preflight(self, client: t.Any, name: str) -> t.Any | None:
        if not self.project_id:
            raise RuntimeError("VM-HA member discovery requires an exact project ID")
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

            response = (
                InstanceServiceClient(client)
                .get_by_name(GetByNameRequest(parent_id=self.project_id, name=name))
                .wait()
            )
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            if nebius_request_error_code_is(
                error, "UNAUTHENTICATED"
            ) or error_chain_has_cli_authentication_failure(error):
                raise RuntimeError(
                    "VM-HA cloud authentication failed while discovering gateway members"
                ) from error
            raise RuntimeError(
                f"VM-HA member {name} could not be classified as existing or fresh"
            ) from error
        if response is None:
            raise RuntimeError(f"VM-HA member discovery returned no result for {name}")
        metadata = getattr(response, "metadata", None)
        if (
            not self._resource_id(response)
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"VM-HA member {name} returned an inexact identity")
        return response

    def _discover_vm_ha_members(
        self, client: t.Any, spec: GatewayGroupSpec
    ) -> dict[str, tuple[t.Any, str]]:
        existing: dict[str, tuple[t.Any, str]] = {}
        for index in range(spec.instance_count):
            name = f"{spec.name}-{index}"
            vm_obj = self._get_vm_by_name_for_vm_ha_preflight(client, name)
            if vm_obj is None:
                continue
            public_ip = self._vm_public_ip_from_object(vm_obj)
            if not public_ip:
                raise RuntimeError(
                    f"Existing VM-HA member {name} has no readable public SSH address"
                )
            existing[name] = (vm_obj, public_ip)
        return existing

    def _require_vm_ha_member_snapshot(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        expected: dict[str, tuple[t.Any, str]],
    ) -> None:
        current = self._discover_vm_ha_members(client, spec)
        if set(current) != set(expected):
            raise RuntimeError("VM-HA member set changed after SSH identity verification")
        for name, (expected_vm, expected_ip) in expected.items():
            current_vm, current_ip = current[name]
            expected_id = self._resource_id(expected_vm)
            current_id = self._resource_id(current_vm)
            if not expected_id or current_id != expected_id or current_ip != expected_ip:
                raise RuntimeError(f"VM-HA member {name} changed identity after SSH verification")

    def discover_vm_ha_members(self, spec: GatewayGroupSpec) -> dict[str, str]:
        """Classify existing members with read-only Compute calls only."""

        if spec.vm_ha is None:
            return {}
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA member discovery requires the Nebius SDK")
        snapshot = self._discover_vm_ha_members(client, spec)
        self._vm_ha_ssh_preflight_snapshot = dict(snapshot)
        return {name: public_ip for name, (_, public_ip) in snapshot.items()}

    def discover_ordinary_gateway_members(self, spec: GatewayGroupSpec) -> dict[str, str]:
        """Classify existing ordinary gateway VMs with strict read-only Compute calls."""

        if spec.vm_ha is not None:
            raise RuntimeError("Ordinary SSH discovery cannot inspect a VM-HA plan")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Gateway SSH discovery requires the Nebius SDK")
        snapshot: dict[str, tuple[t.Any, str]] = {}
        for index in range(spec.instance_count):
            name = f"{spec.name}-{index}"
            vm_obj = self._get_vm_by_name_for_ordinary_ssh_preflight(client, name)
            if vm_obj is None:
                continue
            public_ip = self._vm_public_ip_from_object(vm_obj)
            if not public_ip:
                raise RuntimeError(f"Existing gateway VM {name} has no readable public SSH address")
            snapshot[name] = (vm_obj, public_ip)
        self._ordinary_ssh_preflight_snapshot = dict(snapshot)
        return {name: public_ip for name, (_, public_ip) in snapshot.items()}

    def _get_vm_by_id_for_ordinary_ssh_preflight(
        self,
        client: t.Any,
        compute_id: str,
    ) -> t.Any:
        try:
            from nebius.api.nebius.compute.v1 import (  # type: ignore
                GetInstanceRequest,
                InstanceServiceClient,
            )

            response = InstanceServiceClient(client).get(GetInstanceRequest(id=compute_id)).wait()
        except Exception as error:
            raise RuntimeError("Gateway Compute identity changed after SSH preflight") from error
        if response is None:
            raise RuntimeError("Gateway Compute identity changed after SSH preflight")
        return response

    def ordinary_ssh_trust_bindings(
        self,
        spec: GatewayGroupSpec,
        *,
        retained_hosts: t.Iterable[str],
    ) -> dict[str, t.Callable[[], None]]:
        """Bind ordinary retained hosts to immutable Compute identity evidence."""

        if spec.vm_ha is not None or not self.project_id:
            raise RuntimeError("Ordinary SSH trust requires exact deployment identity")
        snapshot = self._ordinary_ssh_preflight_snapshot
        if snapshot is None:
            raise RuntimeError("Ordinary SSH trust requires prior member discovery")
        requested = set(retained_hosts)
        if requested - set(snapshot):
            raise RuntimeError("Ordinary gateway member set changed after discovery")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Ordinary SSH trust requires the Nebius SDK")
        assertions: dict[str, t.Callable[[], None]] = {}
        for name in sorted(requested):
            vm_obj, public_ip = snapshot[name]
            expected = self._vm_ha_ssh_member_signature(vm_obj)
            if (
                not expected[0]
                or expected[1] != name
                or expected[2] != self.project_id
                or expected[4] != public_ip
            ):
                raise RuntimeError(f"Gateway Compute binding is incomplete for {name}")

            def assert_current(
                *,
                expected_signature: tuple[str, str, str, str, str, str] = expected,
                expected_name: str = name,
            ) -> None:
                current = self._get_vm_by_id_for_ordinary_ssh_preflight(
                    client,
                    expected_signature[0],
                )
                if self._vm_ha_ssh_member_signature(current) != expected_signature:
                    raise RuntimeError(f"Gateway SSH trust evidence changed for {expected_name}")

            assertions[name] = assert_current
        self._ordinary_ssh_binding_assertions = dict(assertions)
        return assertions

    def recover_ordinary_ssh_host_keys(
        self,
        hostnames: frozenset[str],
        *,
        spec: GatewayGroupSpec,
    ) -> dict[str, SSHHostKeyRecovery]:
        """Recover exact ordinary product identities from authenticated cloud-init."""

        if spec.vm_ha is not None or not self.project_id:
            raise RuntimeError("Ordinary SSH recovery requires exact deployment identity")
        snapshot = self._ordinary_ssh_preflight_snapshot
        if snapshot is None:
            raise RuntimeError("Ordinary SSH recovery requires prior member discovery")
        expected_names = {f"{spec.name}-{index}" for index in range(spec.instance_count)}
        if set(hostnames) - expected_names:
            raise RuntimeError("Ordinary SSH recovery member is outside the deployment plan")
        recovered: dict[str, SSHHostKeyRecovery] = {}
        for name in sorted(hostnames):
            observed = snapshot.get(name)
            assertion = self._ordinary_ssh_binding_assertions.get(name)
            if observed is None or assertion is None:
                continue
            assertion()
            vm_obj, _public_ip = observed
            cloud_init = str(
                getattr(getattr(vm_obj, "spec", None), "cloud_init_user_data", "") or ""
            )
            if (
                f"path: {VM_HA_SSH_HOST_KEY_PATH}" not in cloud_init
                and f"HostKey {VM_HA_SSH_HOST_KEY_PATH}" not in cloud_init
            ):
                continue
            recovered[name] = SSHHostKeyRecovery(
                hostname=name,
                private_key=recover_product_host_key(
                    vm_obj,
                    path=VM_HA_SSH_HOST_KEY_PATH,
                ),
                assert_current=assertion,
            )
        return recovered

    def enroll_ordinary_ssh_host_keys(
        self,
        spec: GatewayGroupSpec,
        hostnames: frozenset[str],
        *,
        management_public_key: str | None,
        username: str,
    ) -> dict[str, SSHHostKeyEnrollment]:
        """Enroll exactly one unchanged pre-branch ordinary gateway member."""

        if spec.vm_ha is not None or not self.project_id:
            raise RuntimeError("Ordinary SSH enrollment requires exact deployment identity")
        if len(hostnames) != 1:
            raise RuntimeError("Ordinary SSH enrollment requires exactly one retained member")
        snapshot = self._ordinary_ssh_preflight_snapshot
        if snapshot is None:
            raise RuntimeError("Ordinary SSH enrollment requires prior member discovery")
        name = next(iter(hostnames))
        observed = snapshot.get(name)
        assertion = self._ordinary_ssh_binding_assertions.get(name)
        if observed is None or assertion is None:
            raise RuntimeError("Ordinary SSH enrollment member changed after discovery")
        vm_obj, public_ip = observed
        signature = self._vm_ha_ssh_member_signature(vm_obj)
        compute_id, instance_name, parent_id = signature[:3]
        if (
            not compute_id
            or instance_name != name
            or parent_id != self.project_id
            or signature[4] != public_ip
        ):
            raise RuntimeError("Ordinary SSH enrollment Compute binding is incomplete")
        binding_digest = self._ordinary_compute_binding_digest(signature, spec.region)
        self._management_public_key = management_public_key
        client_auth = self._require_ssh_client_auth()
        if client_auth is None:
            raise ValueError("Ordinary SSH enrollment requires the configured public client key")
        enrollment = enroll_ordinary_ssh_host_key(
            OrdinarySSHEnrollmentTarget(
                hostname=name,
                transport_address=public_ip,
                compute_id=compute_id,
                project_id=parent_id,
                instance_name=instance_name,
                region_id=str(spec.region or ""),
                compute_binding_sha256=binding_digest,
                assert_current=assertion,
            ),
            client_auth=client_auth,
            username=username,
        )
        return {name: enrollment}

    @staticmethod
    def _ordinary_compute_binding_digest(
        signature: tuple[str, str, str, str, str, str],
        region: str | None,
    ) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "compute_signature": signature,
                    "region_id": str(region or ""),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def ordinary_migration_ssh_imports(
        self,
        spec: GatewayGroupSpec,
        *,
        ordinary_scope: VMHASSHTrustScope,
        hostnames: t.Iterable[str],
    ) -> dict[str, TrustedSSHMemberImport]:
        """Rebind ordinary receipt members to unchanged migration Compute resources."""

        if spec.vm_ha is None or not self.project_id:
            raise RuntimeError("Ordinary SSH trust import requires a VM-HA migration plan")
        snapshot = self._vm_ha_ssh_preflight_snapshot
        if snapshot is None:
            raise RuntimeError("Ordinary SSH trust import requires prior member discovery")
        imports: dict[str, TrustedSSHMemberImport] = {}
        for name in sorted(set(hostnames)):
            observed = snapshot.get(name)
            assertion = self._vm_ha_ssh_binding_assertions.get(name)
            member = managed_ssh_trust_member(ordinary_scope, name)
            if observed is None or assertion is None:
                raise RuntimeError("Ordinary SSH trust import member changed after discovery")
            if member is None:
                raise RuntimeError(
                    "Ordinary gateway managed SSH receipt is unavailable; run ordinary apply "
                    "with the original configuration before vm-ha"
                )
            vm_obj, public_ip = observed
            signature = self._vm_ha_ssh_member_signature(vm_obj)
            if (
                not signature[0]
                or signature[1] != name
                or signature[2] != self.project_id
                or signature[4] != public_ip
            ):
                raise RuntimeError("Ordinary SSH trust import Compute binding is incomplete")
            binding_digest = self._ordinary_compute_binding_digest(signature, spec.region)
            prior_binding = member.authority.compute_binding_sha256
            if prior_binding is not None and not hmac.compare_digest(
                prior_binding,
                binding_digest,
            ):
                raise RuntimeError("Ordinary SSH trust receipt Compute binding changed")
            imports[name] = TrustedSSHMemberImport(
                hostname=name,
                pins=member.pins,
                predecessor_receipt_sha256=member.receipt_sha256,
                compute_binding_sha256=binding_digest,
                assert_current=assertion,
            )
        return imports

    def prepare_ordinary_ssh_policy(
        self,
        spec: GatewayGroupSpec,
        planned_instances: t.Iterable[t.Any],
        *,
        trust_scope: VMHASSHTrustScope,
        recreate: bool,
        management_public_key: str | None,
        dry_run: bool,
        username: str,
        legacy_host_key_enrollments: t.Mapping[str, SSHHostKeyEnrollment] | None = None,
    ) -> SSHTrustPolicy:
        """Pre-pin ordinary gateway SSH identities before any cloud mutation."""

        instances = tuple(planned_instances)
        self._management_public_key = management_public_key
        existing = self.discover_ordinary_gateway_members(spec)
        enrollment = {
            instance.hostname
            for instance in instances
            if recreate or instance.hostname not in existing
        }
        # Recreate changes the Compute object, never the stable SSH identity.
        # Present members are both enrollment targets and retained identities;
        # they must supply the original private key locally or through exact
        # authenticated product cloud-init recovery.
        retained = set(existing)
        targets: list[tuple[str, str]] = []
        aliases: dict[str, tuple[str, ...]] = {}
        for instance in instances:
            configured = str(getattr(instance, "external_ip", "") or "").strip()
            discovered = str(existing.get(instance.hostname) or "").strip()
            target = discovered or configured or instance.hostname
            targets.append((instance.hostname, target))
            aliases[instance.hostname] = tuple(
                alias
                for alias in (configured, discovered)
                if alias and alias not in {instance.hostname, target}
            )
        bindings = self.ordinary_ssh_trust_bindings(spec, retained_hosts=retained)
        policy = require_vm_ha_ssh_policy(
            tuple(targets),
            enrollment_hosts=enrollment,
            management_key_path=self._management_key_path,
            management_public_key=management_public_key,
            require_management_key=False,
            trust_scope=trust_scope,
            allow_managed_repair=True,
            persist_default_host_keys=not dry_run,
            additional_aliases=aliases,
            retained_hosts=retained,
            allow_default_known_hosts_import=not dry_run,
            default_known_hosts_bindings=bindings,
            host_identity_recovery=lambda hostnames: self.recover_ordinary_ssh_host_keys(
                hostnames,
                spec=spec,
            ),
            allow_legacy_ordinary_enrollment=(not recreate and len(retained) == 1),
            legacy_host_key_enrollments=legacy_host_key_enrollments,
        )
        self.verify_ordinary_existing_identities(
            {name: existing[name] for name in sorted(retained)},
            policy=policy,
            username=username,
        )
        return policy

    def _get_vm_by_id_for_vm_ha_preflight(self, client: t.Any, compute_id: str) -> t.Any:
        try:
            from nebius.api.nebius.compute.v1 import (  # type: ignore
                GetInstanceRequest,
                InstanceServiceClient,
            )

            response = (
                InstanceServiceClient(client)
                .get(
                    GetInstanceRequest(id=compute_id),
                    **vm_ha_request_kwargs(),
                )
                .wait()
            )
        except Exception as error:
            raise RuntimeError("VM-HA Compute identity changed after SSH preflight") from error
        if response is None:
            raise RuntimeError("VM-HA Compute identity changed after SSH preflight")
        return response

    def _vm_ha_ssh_member_signature(self, vm_obj: t.Any) -> tuple[str, str, str, str, str, str]:
        metadata = getattr(vm_obj, "metadata", None)
        compute_id = self._resource_id(vm_obj) or ""
        name = str(getattr(metadata, "name", "") or getattr(vm_obj, "name", "") or "")
        parent_id = str(getattr(metadata, "parent_id", "") or "")
        revision = str(
            getattr(metadata, "resource_version", "") or getattr(metadata, "version", "") or ""
        )
        public_ip = self._vm_public_ip_from_object(vm_obj) or ""
        cloud_init = str(getattr(getattr(vm_obj, "spec", None), "cloud_init_user_data", "") or "")
        return (
            compute_id,
            name,
            parent_id,
            revision,
            public_ip,
            hashlib.sha256(cloud_init.encode("utf-8")).hexdigest(),
        )

    def _validate_vm_ha_ssh_member_binding(
        self,
        *,
        name: str,
        vm_obj: t.Any,
        public_ip: str,
        spec: GatewayGroupSpec,
        lifecycle_snapshot: VMHALifecycleSnapshot | None,
    ) -> FormerVMHAProvenance:
        if spec.vm_ha is None or not self.project_id:
            raise RuntimeError("VM-HA SSH recovery requires exact deployment identity")
        signature = self._vm_ha_ssh_member_signature(vm_obj)
        try:
            index = int(name.rsplit("-", 1)[1])
        except (IndexError, ValueError) as error:
            raise RuntimeError("VM-HA SSH recovery member identity is malformed") from error
        if (
            not signature[0]
            or signature[1] != name
            or signature[2] != self.project_id
            or signature[4] != public_ip
            or index not in {0, 1}
        ):
            raise RuntimeError("VM-HA SSH recovery Compute binding is incomplete")
        provenance = compute_provisioning_provenance(vm_obj)
        if provenance is FormerVMHAProvenance.CURRENT_MARKER:
            marker = parse_provisioning_marker(vm_obj)
            topology = (
                validate_provisioning_marker(
                    marker,
                    expected_instance_index=index,
                    gateway_name=spec.name,
                    allocation_name=(f"{spec.name}-{spec.vm_ha.cluster_id}-shared-private-ip"),
                    cluster_id=spec.vm_ha.cluster_id,
                )
                if marker is not None
                else None
            )
            if topology is None:
                raise RuntimeError("VM-HA SSH recovery provisioning identity does not match")
            return provenance
        if provenance not in {None, FormerVMHAProvenance.LEGACY_RUNTIME}:
            raise RuntimeError("VM-HA SSH recovery has no product provisioning identity")
        if lifecycle_snapshot is None:
            raise RuntimeError("VM-HA legacy SSH recovery requires hardened lifecycle authority")
        lifecycle = lifecycle_snapshot.state
        planned = {f"{spec.name}-{member.instance_index}": member for member in spec.vm_ha.members}
        lifecycle_members = lifecycle_member_map(lifecycle)
        member = lifecycle_members.get(name)
        if (
            lifecycle.status
            not in {
                VMHALifecycleStatus.PROVISIONING,
                VMHALifecycleStatus.ACTIVATING,
                VMHALifecycleStatus.ACTIVE,
            }
            or lifecycle.project_id != self.project_id
            or lifecycle.gateway_name != spec.name
            or lifecycle.cluster_id != spec.vm_ha.cluster_id
            or set(lifecycle_members) != set(planned)
            or member is None
            or member.compute_id != signature[0]
            or member.public_ip != public_ip
            or member.node_id != planned[name].node_id
            or member.role != planned[name].role.value
            or (
                provenance is None
                and (
                    planned[name].role is not VMHARole.ACTIVE
                    or member.role != VMHARole.ACTIVE.value
                )
            )
        ):
            raise RuntimeError("VM-HA legacy SSH recovery lifecycle binding does not match")
        return FormerVMHAProvenance.LIFECYCLE_STATE if provenance is None else provenance

    def _validate_ordinary_to_vm_ha_ssh_member_binding(
        self,
        *,
        name: str,
        vm_obj: t.Any,
        public_ip: str,
        spec: GatewayGroupSpec,
        lifecycle_snapshot: VMHALifecycleSnapshot | None = None,
    ) -> None:
        """Bind the one retained ordinary active without requiring prior HA provenance."""

        if spec.vm_ha is None or not self.project_id:
            raise RuntimeError("VM-HA SSH migration requires exact deployment identity")
        active_names = {
            f"{spec.name}-{member.instance_index}"
            for member in spec.vm_ha.members
            if member.role is VMHARole.ACTIVE
        }
        signature = self._vm_ha_ssh_member_signature(vm_obj)
        if (
            active_names != {name}
            or not signature[0]
            or signature[1] != name
            or signature[2] != self.project_id
            or signature[4] != public_ip
        ):
            raise RuntimeError("Ordinary-to-HA SSH migration Compute binding is incomplete")
        if lifecycle_snapshot is None:
            return
        lifecycle = lifecycle_snapshot.state
        planned = {f"{spec.name}-{member.instance_index}": member for member in spec.vm_ha.members}
        lifecycle_members = lifecycle_member_map(lifecycle)
        lifecycle_member = lifecycle_members.get(name)
        planned_member = planned.get(name)
        if (
            lifecycle.status
            not in {
                VMHALifecycleStatus.PROVISIONING,
                VMHALifecycleStatus.ACTIVATING,
                VMHALifecycleStatus.ACTIVE,
            }
            or lifecycle.project_id != self.project_id
            or lifecycle.gateway_name != spec.name
            or lifecycle.cluster_id != spec.vm_ha.cluster_id
            or set(lifecycle_members) != set(planned)
            or lifecycle_member is None
            or planned_member is None
            or lifecycle_member.compute_id != signature[0]
            or lifecycle_member.public_ip != public_ip
            or lifecycle_member.node_id != planned_member.node_id
            or lifecycle_member.role != planned_member.role.value
        ):
            raise RuntimeError("Ordinary-to-HA SSH migration lifecycle binding does not match")

    def vm_ha_ssh_trust_bindings(
        self,
        spec: GatewayGroupSpec,
        *,
        retained_hosts: t.Iterable[str],
        lifecycle_snapshot_loader: t.Callable[[], VMHALifecycleSnapshot | None] | None = None,
        ordinary_migration_hosts: t.Iterable[str] = (),
    ) -> dict[str, t.Callable[[], None]]:
        """Bind retained members to immutable Compute and provisioning evidence."""

        snapshot = self._vm_ha_ssh_preflight_snapshot
        if snapshot is None:
            raise RuntimeError("VM-HA SSH recovery requires prior member discovery")
        requested = set(retained_hosts)
        if requested - set(snapshot):
            raise RuntimeError("VM-HA SSH recovery member set changed after discovery")
        ordinary_migration = set(ordinary_migration_hosts)
        if ordinary_migration - requested or len(ordinary_migration) > 1:
            raise RuntimeError("VM-HA SSH migration member set is invalid")
        lifecycle_snapshot = self._vm_ha_ssh_lifecycle_snapshot
        if lifecycle_snapshot_loader is not None:
            lifecycle_snapshot = lifecycle_snapshot_loader()
            self._vm_ha_ssh_lifecycle_snapshot = lifecycle_snapshot
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA SSH recovery requires the Nebius SDK")
        assertions: dict[str, t.Callable[[], None]] = {}
        for name in sorted(requested):
            vm_obj, public_ip = snapshot[name]
            expected_signature = self._vm_ha_ssh_member_signature(vm_obj)
            ordinary_migration_member = name in ordinary_migration
            if ordinary_migration_member:
                self._validate_ordinary_to_vm_ha_ssh_member_binding(
                    name=name,
                    vm_obj=vm_obj,
                    public_ip=public_ip,
                    spec=spec,
                    lifecycle_snapshot=lifecycle_snapshot,
                )

            def assert_current(
                *,
                expected: tuple[str, str, str, str, str, str] = expected_signature,
                expected_name: str = name,
                expected_vm: t.Any = vm_obj,
                expected_public_ip: str = public_ip,
                expected_ordinary_migration: bool = ordinary_migration_member,
            ) -> None:
                raw_expected_provenance = (
                    None
                    if expected_ordinary_migration
                    else compute_provisioning_provenance(expected_vm)
                )
                assertion_lifecycle_snapshot = self._vm_ha_ssh_lifecycle_snapshot
                if lifecycle_snapshot_loader is not None and (
                    expected_ordinary_migration
                    or raw_expected_provenance in {None, FormerVMHAProvenance.LEGACY_RUNTIME}
                ):
                    assertion_lifecycle_snapshot = lifecycle_snapshot_loader()
                    self._vm_ha_ssh_lifecycle_snapshot = assertion_lifecycle_snapshot
                if expected_ordinary_migration:
                    self._validate_ordinary_to_vm_ha_ssh_member_binding(
                        name=expected_name,
                        vm_obj=expected_vm,
                        public_ip=expected_public_ip,
                        spec=spec,
                        lifecycle_snapshot=assertion_lifecycle_snapshot,
                    )
                    expected_provenance = None
                else:
                    expected_provenance = self._validate_vm_ha_ssh_member_binding(
                        name=expected_name,
                        vm_obj=expected_vm,
                        public_ip=expected_public_ip,
                        spec=spec,
                        lifecycle_snapshot=assertion_lifecycle_snapshot,
                    )
                current = self._get_vm_by_id_for_vm_ha_preflight(client, expected[0])
                if self._vm_ha_ssh_member_signature(current) != expected:
                    raise RuntimeError(f"VM-HA SSH recovery evidence changed for {expected_name}")
                if expected_ordinary_migration:
                    self._validate_ordinary_to_vm_ha_ssh_member_binding(
                        name=expected_name,
                        vm_obj=current,
                        public_ip=expected[4],
                        spec=spec,
                        lifecycle_snapshot=assertion_lifecycle_snapshot,
                    )
                    current_provenance = None
                else:
                    current_provenance = self._validate_vm_ha_ssh_member_binding(
                        name=expected_name,
                        vm_obj=current,
                        public_ip=expected[4],
                        spec=spec,
                        lifecycle_snapshot=assertion_lifecycle_snapshot,
                    )
                if current_provenance is not expected_provenance:
                    raise RuntimeError(f"VM-HA SSH recovery evidence changed for {expected_name}")
                if assertion_lifecycle_snapshot is not None and (
                    expected_ordinary_migration
                    or expected_provenance
                    in {
                        FormerVMHAProvenance.LEGACY_RUNTIME,
                        FormerVMHAProvenance.LIFECYCLE_STATE,
                    }
                ):
                    assertion_lifecycle_snapshot.assert_current()

            assertions[name] = assert_current
        self._vm_ha_ssh_binding_assertions = dict(assertions)
        return assertions

    def recover_vm_ha_ssh_host_keys(
        self,
        hostnames: frozenset[str],
        *,
        spec: GatewayGroupSpec,
        ordinary_migration_hosts: t.Iterable[str] = (),
    ) -> dict[str, SSHHostKeyRecovery]:
        """Recover exact product-generated private identities from persisted cloud-init."""

        snapshot = self._vm_ha_ssh_preflight_snapshot or {}
        ordinary_migration = set(ordinary_migration_hosts)
        if ordinary_migration - set(snapshot) or len(ordinary_migration) > 1:
            raise RuntimeError("VM-HA SSH migration member set is invalid")
        recovered: dict[str, SSHHostKeyRecovery] = {}
        for name in sorted(hostnames):
            observed = snapshot.get(name)
            assertion = self._vm_ha_ssh_binding_assertions.get(name)
            if observed is None or assertion is None:
                continue
            assertion()
            vm_obj, public_ip = observed
            if name in ordinary_migration:
                self._validate_ordinary_to_vm_ha_ssh_member_binding(
                    name=name,
                    vm_obj=vm_obj,
                    public_ip=public_ip,
                    spec=spec,
                )
                path = VM_HA_SSH_HOST_KEY_PATH
            else:
                provenance = self._validate_vm_ha_ssh_member_binding(
                    name=name,
                    vm_obj=vm_obj,
                    public_ip=public_ip,
                    spec=spec,
                    lifecycle_snapshot=self._vm_ha_ssh_lifecycle_snapshot,
                )
                path = (
                    VM_HA_SSH_HOST_KEY_PATH
                    if provenance is FormerVMHAProvenance.CURRENT_MARKER
                    else LEGACY_VM_HA_SSH_HOST_KEY_PATH
                )
            recovered[name] = SSHHostKeyRecovery(
                hostname=name,
                private_key=recover_product_host_key(vm_obj, path=path),
                assert_current=assertion,
            )
        return recovered

    def _classify_former_vm_ha_evidence(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        legacy_identities: t.Mapping[str, LegacyVMHAIdentity | None] | None = None,
        lifecycle_state: VMHALifecycleState | None = None,
    ) -> tuple[dict[str, tuple[t.Any, str]], FormerVMHAEvidence] | None:
        return classify_former_vm_ha_evidence(
            project_id=self.project_id,
            client=client,
            gateway_name=spec.name,
            resource_id=self._resource_id,
            instance_reader=self._get_vm_by_name_for_vm_ha_preflight,
            public_ip_reader=self._vm_public_ip_from_object,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )

    def discover_former_vm_ha_candidate_members(
        self,
        spec: GatewayGroupSpec,
        *,
        lifecycle_state: VMHALifecycleState | None = None,
        allow_unmarked_runtime_probe: bool = False,
    ) -> dict[str, str]:
        """Find exact two-member runtime-probe candidates without VPC reads."""

        self._former_vm_ha_candidate_provenance = None
        if spec.vm_ha is not None or not self.project_id:
            return {}
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Former VM-HA discovery requires the Nebius SDK")
        members = self._discover_vm_ha_members(client, replace(spec, instance_count=2))
        if lifecycle_state is not None:
            expected = lifecycle_member_map(lifecycle_state)
            if set(members) != set(expected):
                raise RuntimeError("Former VM-HA lifecycle member set changed")
            for name, (instance, public_ip) in members.items():
                member = expected[name]
                interfaces = list(
                    getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
                )
                if (
                    self._resource_id(instance) != member.compute_id
                    or len(interfaces) != 1
                    or str(getattr(interfaces[0], "name", "") or "")
                    != member.network_interface_name
                    or public_ip != member.public_ip
                ):
                    raise RuntimeError("Former VM-HA lifecycle member identity changed")
            self._former_vm_ha_candidate_provenance = FormerVMHAProvenance.LIFECYCLE_STATE
            self._former_vm_ha_lifecycle = lifecycle_state
            return {name: public_ip for name, (_, public_ip) in members.items()}
        provenances = {
            compute_provisioning_provenance(instance) for instance, _ in members.values()
        }
        provenances.discard(None)
        if not provenances:
            if allow_unmarked_runtime_probe and len(members) == 2:
                return {name: public_ip for name, (_, public_ip) in members.items()}
            return {}
        if (
            len(members) != 2
            or len(provenances) != 1
            or any(
                compute_provisioning_provenance(instance) is None
                for instance, _ in members.values()
            )
        ):
            raise RuntimeError("Former VM-HA Compute provenance is incomplete or mixed")
        self._former_vm_ha_candidate_provenance = provenances.pop()
        return {name: public_ip for name, (_, public_ip) in members.items()}

    @property
    def former_vm_ha_candidate_provenance(self) -> FormerVMHAProvenance | None:
        return self._former_vm_ha_candidate_provenance

    def discover_former_vm_ha_members(
        self,
        spec: GatewayGroupSpec,
        *,
        legacy_identities: t.Mapping[str, LegacyVMHAIdentity | None] | None = None,
        lifecycle_state: VMHALifecycleState | None = None,
    ) -> dict[str, str]:
        """Discover both former HA members independently of the new member count."""

        self._former_vm_ha_snapshot = None
        self._former_vm_ha_evidence = None
        if spec.vm_ha is not None or not self.project_id:
            return {}
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Former VM-HA discovery requires the Nebius SDK")
        classified = self._classify_former_vm_ha_evidence(
            client,
            spec,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )
        if classified is None:
            if self._former_vm_ha_candidate_provenance is not None:
                raise RuntimeError("Former VM-HA candidate evidence is not authoritative")
            return {}
        members, evidence = classified
        if (
            self._former_vm_ha_candidate_provenance is not None
            and evidence.provenance is not self._former_vm_ha_candidate_provenance
        ):
            raise RuntimeError("Former VM-HA provenance changed during classification")
        if self._former_vm_ha_lifecycle != lifecycle_state:
            raise RuntimeError("Former VM-HA lifecycle state changed during classification")
        self._former_vm_ha_snapshot = members
        self._former_vm_ha_evidence = evidence
        return {name: public_ip for name, (_, public_ip) in members.items()}

    def verify_former_vm_ha_member_snapshot(
        self,
        spec: GatewayGroupSpec,
        expected: t.Mapping[str, str],
        *,
        legacy_identities: t.Mapping[str, LegacyVMHAIdentity | None] | None = None,
        lifecycle_state: VMHALifecycleState | None = None,
    ) -> None:
        """Re-read every former Compute identity immediately before teardown."""

        snapshot = self._former_vm_ha_snapshot
        evidence = self._former_vm_ha_evidence
        if (
            snapshot is None
            or {name: public_ip for name, (_, public_ip) in snapshot.items()} != dict(expected)
            or evidence is None
        ):
            raise RuntimeError("Former VM-HA discovery snapshot is unavailable or stale")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Former VM-HA identity recheck requires the Nebius SDK")
        if evidence.provenance is FormerVMHAProvenance.LEGACY_RUNTIME and legacy_identities is None:
            raise RuntimeError(
                "Former VM-HA legacy runtime evidence must be re-read immediately before teardown"
            )
        if evidence.provenance is FormerVMHAProvenance.LIFECYCLE_STATE and (
            lifecycle_state is None
            or self._former_vm_ha_lifecycle is None
            or not lifecycle_state.has_same_identity(self._former_vm_ha_lifecycle)
            or (
                lifecycle_state.status is not self._former_vm_ha_lifecycle.status
                and not (
                    self._former_vm_ha_lifecycle.status
                    in {VMHALifecycleStatus.ACTIVATING, VMHALifecycleStatus.ACTIVE}
                    and lifecycle_state.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS
                )
            )
        ):
            raise RuntimeError("Former VM-HA lifecycle state is unavailable or stale")
        if evidence.provenance is FormerVMHAProvenance.LIFECYCLE_STATE:
            assert lifecycle_state is not None
            self._former_vm_ha_lifecycle = lifecycle_state
        current = self._classify_former_vm_ha_evidence(
            client,
            spec,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )
        if current is None or current[1] != evidence:
            raise RuntimeError("Former VM-HA allocation or member evidence changed")
        if {name: public_ip for name, (_, public_ip) in current[0].items()} != dict(expected):
            raise RuntimeError("Former VM-HA member addresses changed")
        self._require_vm_ha_member_snapshot(client, replace(spec, instance_count=2), snapshot)
        final = self._classify_former_vm_ha_evidence(
            client,
            spec,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )
        if final is None or final[1] != evidence:
            raise RuntimeError("Former VM-HA allocation or member evidence changed")

    def former_vm_ha_lifecycle_state(self, spec: GatewayGroupSpec) -> VMHALifecycleState:
        """Adopt the exact classified cloud snapshot into a durable selector."""

        evidence = self._former_vm_ha_evidence
        snapshot = self._former_vm_ha_snapshot
        if not self.project_id or evidence is None or snapshot is None:
            raise RuntimeError("Former VM-HA lifecycle adoption requires classified evidence")
        members: list[VMHALifecycleMember] = []
        for index, identity in enumerate(evidence.members):
            name, compute_id, network_interface_name, node_id, role = identity
            observed = snapshot.get(name)
            if observed is None:
                raise RuntimeError("Former VM-HA lifecycle adoption snapshot is incomplete")
            members.append(
                VMHALifecycleMember(
                    instance_index=index,
                    instance_name=name,
                    node_id=node_id,
                    role=role,
                    compute_id=compute_id,
                    network_interface_name=network_interface_name,
                    public_ip=observed[1],
                )
            )
        return VMHALifecycleState(
            status=VMHALifecycleStatus.ACTIVE,
            project_id=self.project_id,
            gateway_name=spec.name,
            cluster_id=evidence.cluster_id,
            allocation_id=evidence.allocation_id,
            allocation_name=evidence.allocation_name,
            members=t.cast(tuple[VMHALifecycleMember, VMHALifecycleMember], tuple(members)),
        )

    def verify_vm_ha_existing_identities(
        self,
        existing: t.Mapping[str, str],
        *,
        policy: SSHTrustPolicy | None = None,
        username: str = "ubuntu",
        probe_timeout: float = 10.0,
    ) -> None:
        """Verify every existing member's exact pin without changing remote state."""

        if not math.isfinite(probe_timeout) or probe_timeout <= 0:
            raise ValueError("VM-HA SSH probe timeout must be finite and positive")
        selected_policy = policy or self._ssh_policy
        if selected_policy is None:
            raise RuntimeError("VM-HA existing-member verification requires an SSH policy")
        for name, public_ip in existing.items():
            ssh_base = build_openssh_base_command(
                key_path=(
                    self._management_key_path if self._management_public_key is None else None
                ),
                client_auth=self._require_ssh_client_auth(),
                connect_timeout=max(1, min(5, math.ceil(probe_timeout))),
                policy=selected_policy,
                hostname=name,
            )
            ssh_base.extend(["-o", "BatchMode=yes"])
            try:
                result = subprocess.run(
                    ssh_base + [f"{username}@{public_ip}", "true"],
                    capture_output=True,
                    timeout=probe_timeout,
                )
            except Exception as error:
                raise RuntimeError(
                    f"Existing VM-HA member {name} is unreachable before cloud mutation"
                ) from error
            if result.returncode == 0:
                continue
            detail = result.stderr.decode(errors="replace").lower()
            if (
                "host key verification failed" in detail
                or "remote host identification has changed" in detail
            ):
                raise RuntimeError(
                    f"SSH host identity verification failed for existing VM-HA member {name}"
                )
            raise RuntimeError(f"Existing VM-HA member {name} is unreachable before cloud mutation")

    def verify_ordinary_existing_identities(
        self,
        existing: t.Mapping[str, str],
        *,
        policy: SSHTrustPolicy | None = None,
        username: str = "ubuntu",
        probe_timeout: float = 10.0,
    ) -> None:
        """Verify retained ordinary gateway pins without changing remote state."""

        try:
            self.verify_vm_ha_existing_identities(
                existing,
                policy=policy,
                username=username,
                probe_timeout=probe_timeout,
            )
        except RuntimeError as error:
            message = str(error)
            if "existing VM-HA member" in message:
                message = message.replace("existing VM-HA member", "existing gateway VM")
            raise RuntimeError(message) from error

    def _wait_for_vm_ha_member_ssh(
        self,
        name: str,
        public_ip: str,
        *,
        username: str,
        timeout: float = 300,
        progress_callback: t.Callable[[], None] | None = None,
    ) -> None:
        """Wait until one newly created, pinned member accepts management SSH."""

        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("VM-HA SSH readiness timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        last_error: RuntimeError | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"Replacement VM-HA member {name} did not reach pinned SSH readiness"
                ) from last_error
            try:
                self.verify_vm_ha_existing_identities(
                    {name: public_ip},
                    username=username,
                    probe_timeout=min(10.0, remaining),
                )
                return
            except RuntimeError as error:
                if "identity verification failed" in str(error).lower():
                    raise
                last_error = error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"Replacement VM-HA member {name} did not reach pinned SSH readiness"
                ) from last_error
            time.sleep(min(5.0, remaining))
            if progress_callback is not None:
                progress_callback()

    def wait_for_vm_ha_member_ssh(
        self,
        name: str,
        public_ip: str,
        *,
        username: str,
        timeout: float = 300,
        progress_callback: t.Callable[[], None] | None = None,
    ) -> None:
        """Wait for one exact pinned VM-HA management endpoint."""

        self._wait_for_vm_ha_member_ssh(
            name,
            public_ip,
            username=username,
            timeout=timeout,
            progress_callback=progress_callback,
        )

    @staticmethod
    def _render_gateway_ssh_enrollment_cloud_init(
        cloud_init: str,
        identity: t.Any,
        provisioning_marker: str | None = None,
    ) -> str:
        write_files_anchor = "write_files:\n"
        sshd_anchor = "            Port 22\n"
        cloud_config_anchor = "#cloud-config\n"
        if cloud_init.count(write_files_anchor) != 1:
            raise RuntimeError("Gateway cloud-init must contain exactly one write_files anchor")
        if cloud_init.count(sshd_anchor) != 1:
            raise RuntimeError("Gateway cloud-init must contain exactly one sshd HostKey anchor")
        rendered = cloud_init
        if provisioning_marker is not None:
            if cloud_init.count(cloud_config_anchor) != 1 or "\n" in provisioning_marker:
                raise RuntimeError(
                    "VM-HA cloud-init must contain one safe provisioning marker anchor"
                )
            rendered = rendered.replace(
                cloud_config_anchor,
                cloud_config_anchor + PROVISIONING_MARKER_PREFIX + provisioning_marker + "\n",
                1,
            )
        return rendered.replace(
            write_files_anchor, write_files_anchor + identity.cloud_init_entries(), 1
        ).replace(sshd_anchor, sshd_anchor + f"            HostKey {VM_HA_SSH_HOST_KEY_PATH}\n", 1)

    def _prepare_gateway_ssh_enrollment_cloud_inits(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
        existing_names: set[str],
        recreate: bool,
    ) -> dict[str, str]:
        if self._ssh_policy is None:
            raise RuntimeError("Gateway provisioning requires a validated immutable SSH policy")
        base = self._build_cloud_init(
            ssh_key=spec.vm_spec.get("ssh_public_key"),
            local_prefixes=local_prefixes,
        )
        rendered: dict[str, str] = {}
        for index in range(spec.instance_count):
            name = f"{spec.name}-{index}"
            if name in existing_names and not recreate:
                continue
            rendered[name] = self._render_gateway_ssh_enrollment_cloud_init(
                base,
                self._ssh_policy.identity_for(name),
                render_provisioning_marker(spec, index) if spec.vm_ha is not None else None,
            )
        return rendered

    def _collect_preserved_allocations(self, existing: list[t.Any]) -> dict[str, list[str]]:
        preserved_allocations: dict[str, list[str]] = {}
        if not existing:
            return preserved_allocations

        print(
            f"[VMManager] Querying allocations from {len(existing)} existing VMs for preservation..."
        )
        for inst in existing:
            vm_name = getattr(getattr(inst, "metadata", None), "name", None) or getattr(
                inst,
                "name",
                None,
            )
            if not vm_name:
                continue
            allocs = self.get_vm_allocations(vm_name)
            if not allocs:
                continue
            alloc_ids = [alloc_id for _, alloc_id in sorted(allocs, key=lambda x: x[0])]
            preserved_allocations[vm_name] = alloc_ids
            print(f"[VMManager] Preserved allocations for {vm_name}: {alloc_ids}")
        return preserved_allocations

    def _delete_existing_instances_and_boot_disks(
        self,
        client: t.Any,
        existing: list[t.Any],
        spec: GatewayGroupSpec,
    ) -> None:
        print(
            f"[VMManager] Recreate requested; deleting {len(existing)} instances and boot disks (preserving subnet and allocations)"
        )
        isc = None
        dsc = None
        try:
            from nebius.api.nebius.compute.v1 import (
                DiskServiceClient,
                InstanceServiceClient,
            )  # type: ignore

            isc = InstanceServiceClient(client)
            dsc = DiskServiceClient(client)
        except Exception as e:
            print(f"[VMManager] Cannot get service clients for deletion: {e}")

        if isc is None:
            print("[VMManager] ERROR: Cannot delete VMs - InstanceServiceClient not available")
            raise RuntimeError("Cannot proceed with --recreate-gw: VM deletion failed")

        for inst in existing:
            inst_id = getattr(inst, "id", None)
            if not inst_id:
                metadata = getattr(inst, "metadata", None)
                if metadata:
                    inst_id = getattr(metadata, "id", None)

            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or getattr(
                inst,
                "name",
                "unknown",
            )
            if not inst_id:
                continue

            try:
                print(f"[VMManager] Deleting VM {inst_name} (id={inst_id})...")
                from nebius.api.nebius.compute.v1 import DeleteInstanceRequest  # type: ignore

                delete_req = DeleteInstanceRequest(id=inst_id)
                op = isc.delete(delete_req)
                if hasattr(op, "wait"):
                    op.wait()
                    print(f"[VMManager] VM {inst_name} deletion initiated")
                else:
                    time.sleep(5)
            except Exception as e:
                print(f"[VMManager] Failed to delete VM {inst_name}: {e}")

        if existing:
            print("[VMManager] Waiting for VM deletions to complete...")
            time.sleep(15)

        if dsc is None:
            return

        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore

        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            boot_disk_name = f"{inst_name}-boot"
            try:
                if self.project_id and hasattr(dsc, "get_by_name"):
                    disk_obj = dsc.get_by_name(
                        GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)
                    ).wait()
                    disk_id = getattr(disk_obj, "id", None) or getattr(
                        getattr(disk_obj, "metadata", None),
                        "id",
                        None,
                    )
                    if not disk_id:
                        continue

                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(
                                f"[VMManager] Deleting boot disk {boot_disk_name} (id={disk_id})..."
                            )
                            from nebius.api.nebius.compute.v1 import (
                                DeleteDiskRequest,  # type: ignore
                            )

                            delete_disk_req = DeleteDiskRequest(id=disk_id)
                            disk_op = dsc.delete(delete_disk_req)
                            if hasattr(disk_op, "wait"):
                                disk_op.wait()
                                print(
                                    f"[VMManager] Boot disk {boot_disk_name} deleted successfully"
                                )
                            break
                        except Exception as disk_err:
                            if "FAILED_PRECONDITION" in str(
                                disk_err
                            ) and "read-write attachments" in str(disk_err):
                                if attempt < max_retries - 1:
                                    wait_time = 10 * (attempt + 1)
                                    print(
                                        f"[VMManager] Disk still attached, waiting {wait_time}s before retry {attempt + 2}/{max_retries}..."
                                    )
                                    time.sleep(wait_time)
                                else:
                                    print(
                                        f"[VMManager] Could not delete boot disk {boot_disk_name} after {max_retries} attempts: {disk_err}"
                                    )
                            else:
                                print(
                                    f"[VMManager] Could not delete boot disk {boot_disk_name}: {disk_err}"
                                )
                                break
            except Exception as e:
                print(
                    f"[VMManager] Could not find or delete boot disk {boot_disk_name} (non-fatal): {e}"
                )

        if existing:
            print(
                "[VMManager] Waiting for allocations to fully detach and disk deletions to complete..."
            )
            time.sleep(15)

    def _instance_exists(self, client: t.Any, inst_name: str) -> bool:
        return self._get_vm_by_name(client, inst_name) is not None

    @staticmethod
    def _normalize_disk_type(disk_type: t.Any) -> str:
        try:
            dt = str(disk_type).upper()
            if dt in {
                "NETWORK_SSD",
                "NETWORK_HDD",
                "NETWORK_SSD_NON_REPLICATED",
                "NETWORK_SSD_IO_M3",
            }:
                return dt
            if dt in {"SSD", "NVME"}:
                return "NETWORK_SSD"
            if dt == "HDD":
                return "NETWORK_HDD"
        except Exception:
            pass
        return "NETWORK_SSD"

    def _build_vm_provisioning_config(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> VMProvisioningConfig:
        num_nics = int(spec.vm_spec.get("num_nics", 1))
        if num_nics > 1:
            print(
                f"[VMManager] WARNING: num_nics={num_nics} but current platform only supports 1 NIC. Using num_nics=1."
            )
            num_nics = 1

        subnet_id = self._ensure_vpngw_subnet(client, spec)
        self._ensure_vpngw_route_table(client, subnet_id)

        ssh_key = spec.vm_spec.get("ssh_public_key")
        cloud_init = self._build_cloud_init(ssh_key=ssh_key, local_prefixes=local_prefixes)

        return VMProvisioningConfig(
            subnet_id=subnet_id,
            num_nics=num_nics,
            platform=spec.vm_spec.get("platform") or "cpu-d3",
            preset=spec.vm_spec.get("preset"),
            boot_image=spec.vm_spec.get("disk_boot_image")
            or spec.vm_spec.get("image_family")
            or "ubuntu24.04-driverless",
            disk_gb=spec.vm_spec.get("disk_gb", 200),
            disk_type=self._normalize_disk_type(spec.vm_spec.get("disk_type", "network_ssd")),
            disk_block_bytes=spec.vm_spec.get("disk_block_bytes", 4096),
            cloud_init=cloud_init,
        )

    @staticmethod
    def _resource_id(resource: t.Any) -> str | None:
        resource_id = getattr(resource, "id", None) or getattr(
            getattr(resource, "metadata", None),
            "id",
            None,
        )
        return str(resource_id) if resource_id else None

    @staticmethod
    def _sync_operation(op: t.Any) -> None:
        try:
            if hasattr(op, "sync_wait"):
                op.sync_wait()
                return
            if hasattr(op, "wait"):
                op.wait()
        except Exception:
            pass

    def _sync_vm_ha_operation(self, operation: t.Any) -> None:
        """Synchronize an HA mutation without swallowing SDK ambiguity."""
        journal = self._vm_ha_journal
        effect: str | None = None
        if journal is not None and journal.state.transaction is not None:
            effect = journal.state.transaction.pending_effect
            cloud_operation_id = str(getattr(operation, "id", "") or "")
            if not effect or not cloud_operation_id:
                raise RuntimeError("VM-HA mutation returned no durable operation identity")
            journal.record_cloud_operation(effect, cloud_operation_id)
        wait_vm_ha_operation(operation)
        resource = getattr(getattr(operation, "result", None), "resource", None)
        resource_id = self._resource_id(resource) or str(
            getattr(operation, "resource_id", "") or ""
        )
        if effect and resource_id:
            self._vm_ha_accepted_resource_ids[effect] = resource_id

    @staticmethod
    def _resource_state(resource: t.Any) -> str | None:
        status = getattr(resource, "status", None)
        state = getattr(status, "status", None) if status else None
        return str(state) if state else None

    def _get_disk_by_name_from_client(self, disk_client: t.Any, disk_name: str) -> t.Any | None:
        if not self.project_id or not hasattr(disk_client, "get_by_name"):
            raise RuntimeError("Boot disk lookup requires the exact-name SDK API")
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore

            disk = disk_client.get_by_name(
                GetByNameRequest(parent_id=self.project_id, name=disk_name)
            ).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(f"Boot disk {disk_name!r} could not be classified") from error
        metadata = getattr(disk, "metadata", None)
        if (
            not self._resource_id(disk)
            or str(getattr(metadata, "name", "") or "") != disk_name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"Boot disk {disk_name!r} returned an inexact identity")
        return disk

    def _lookup_boot_disk_id(self, disk_client: t.Any, disk_name: str) -> str | None:
        disk_obj = self._get_disk_by_name_from_client(disk_client, disk_name)
        return self._resource_id(disk_obj) or None

    def _resolve_existing_boot_disk_id(self, disk_client: t.Any, disk_name: str) -> str | None:
        disk_obj = self._get_disk_by_name_from_client(disk_client, disk_name)
        disk_id = self._resource_id(disk_obj)
        if not disk_id:
            return None

        disk_state = self._resource_state(disk_obj)
        if disk_state and "DELET" in disk_state.upper():
            print(
                f"[VMManager] Disk {disk_name} is in state {disk_state}, waiting for deletion to complete..."
            )
            max_wait = 60
            wait_interval = 5
            for wait_attempt in range(max_wait // wait_interval):
                time.sleep(wait_interval)
                disk_obj = self._get_disk_by_name_from_client(disk_client, disk_name)
                if disk_obj is None:
                    print(
                        f"[VMManager] Disk deletion complete after {(wait_attempt + 1) * wait_interval}s"
                    )
                    return None
                disk_state = self._resource_state(disk_obj)
                if disk_state and "DELET" in disk_state.upper():
                    print(f"[VMManager] Still deleting... ({(wait_attempt + 1) * wait_interval}s)")
                    continue
                print(f"[VMManager] Disk state changed to {disk_state}")
                return self._resource_id(disk_obj)

            print("[VMManager] Timeout waiting for disk deletion, will retry creation")
            return None

        print(f"[VMManager] Found existing disk {disk_name} id={disk_id}")
        return disk_id

    def _resolve_boot_image_id(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        provisioning: VMProvisioningConfig,
    ) -> str | None:
        image_id = spec.vm_spec.get("image_id") or None
        if image_id or not provisioning.boot_image:
            return image_id

        try:
            from nebius.api.nebius.compute.v1 import (  # type: ignore
                GetImageLatestByFamilyRequest,
                ImageServiceClient,
            )

            image_client = ImageServiceClient(client)  # type: ignore
            routing_code = None
            try:
                if self.project_id and self.project_id.startswith("project-"):
                    routing_code = (self.project_id.split("-")[1] or "")[:3]
            except Exception:
                routing_code = None

            parents_to_try: list[str | None] = []
            if routing_code:
                parents_to_try.append(f"project-{routing_code}public-images")
            parents_to_try.extend([None, "project-u00public-images"])

            for parent in parents_to_try:
                try:
                    request = (
                        GetImageLatestByFamilyRequest(
                            image_family=provisioning.boot_image,
                            parent_id=parent,
                        )
                        if parent
                        else GetImageLatestByFamilyRequest(
                            image_family=provisioning.boot_image,
                        )
                    )
                    image = image_client.get_latest_by_family(request).wait()
                    candidate_id = self._resource_id(image)
                    if not candidate_id:
                        continue
                    if routing_code and not candidate_id.startswith(f"computeimage-{routing_code}"):
                        continue
                    return candidate_id
                except Exception:
                    continue
        except Exception:
            return None

        return None

    def _build_boot_disk_create_request(
        self,
        disk_name: str,
        provisioning: VMProvisioningConfig,
        image_id: str,
    ) -> t.Any:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.compute.v1 import CreateDiskRequest, DiskSpec  # type: ignore

        return CreateDiskRequest(
            metadata=ResourceMetadata(
                name=disk_name,
                parent_id=self.project_id or "",
            ),
            spec=DiskSpec(
                block_size_bytes=provisioning.disk_block_bytes,
                size_gibibytes=provisioning.disk_gb,
                type=t.cast(t.Any, provisioning.disk_type),
                source_image_id=image_id,
            ),
        )

    def _submit_boot_disk_create(
        self,
        disk_client: t.Any,
        create_request: t.Any,
        disk_name: str,
        operation_id: str | None = None,
    ) -> str | None:
        operation = disk_client.create(
            create_request,
            **(
                vm_ha_request_kwargs(operation_id)
                if operation_id
                else self._idempotency_kwargs(operation_id)
            ),
        ).wait()
        if operation_id:
            self._sync_vm_ha_operation(operation)
        else:
            self._sync_operation(operation)
        resource = getattr(getattr(operation, "result", None), "resource", None)
        return self._resource_id(resource) or self._lookup_boot_disk_id(disk_client, disk_name)

    def _wait_for_boot_disk_deletion(self, disk_client: t.Any, disk_name: str) -> bool:
        if not self.project_id or not hasattr(disk_client, "get_by_name"):
            return False

        max_wait = 60
        wait_interval = 5
        for wait_attempt in range(max_wait // wait_interval):
            time.sleep(wait_interval)
            if self._get_disk_by_name_from_client(disk_client, disk_name) is not None:
                print(
                    f"[VMManager] Disk still exists, waiting... ({(wait_attempt + 1) * wait_interval}s)"
                )
                continue
            print("[VMManager] Disk deletion complete, retrying creation...")
            return True

        print("[VMManager] Timeout waiting for disk deletion to complete")
        return False

    def _ensure_boot_disk(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        inst_name: str,
        provisioning: VMProvisioningConfig,
        recreate: bool,
    ) -> str:
        boot_disk_name = f"{inst_name}-boot"
        boot_disk_id = None
        effect = f"provision-{inst_name}-boot-disk"
        binding_key = f"disk:{inst_name}"
        expected_disk_id = self._vm_ha_resource_binding(binding_key)
        operation_id = self._begin_vm_ha_effect(effect) if spec.vm_ha is not None else None

        from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

        disk_client = DiskServiceClient(client)  # type: ignore
        if expected_disk_id:
            boot_disk_id = self._resolve_existing_boot_disk_id(disk_client, boot_disk_name)
            if boot_disk_id != expected_disk_id:
                raise RuntimeError(f"VM-HA boot disk identity changed for {inst_name}")
        elif not recreate and spec.vm_ha is None:
            boot_disk_id = self._resolve_existing_boot_disk_id(disk_client, boot_disk_name)

        if not boot_disk_id:
            print(
                f"[VMManager] Creating boot disk {boot_disk_name} (project_id={self.project_id}) ..."
            )
            image_id = self._resolve_boot_image_id(client, spec, provisioning)
            if not image_id:
                raise RuntimeError(
                    f"[VMManager] Unable to resolve image id for family '{provisioning.boot_image}'. "
                    "Ensure the image family exists or provide vm_spec.image_id."
                )

            create_request = self._build_boot_disk_create_request(
                boot_disk_name,
                provisioning,
                image_id,
            )
            try:
                boot_disk_id = self._submit_boot_disk_create(
                    disk_client,
                    create_request,
                    boot_disk_name,
                    operation_id,
                )
            except Exception as error:
                message = str(error)
                print(f"[VMManager] Disk create exception: {message}")
                already_exists = nebius_request_error_code_is(error, "ALREADY_EXISTS")
                if already_exists and recreate:
                    print(
                        f"[VMManager] Disk {boot_disk_name} still exists (likely deleting), waiting for deletion to complete..."
                    )
                    if self._wait_for_boot_disk_deletion(disk_client, boot_disk_name):
                        try:
                            boot_disk_id = self._submit_boot_disk_create(
                                disk_client,
                                create_request,
                                boot_disk_name,
                                operation_id,
                            )
                        except Exception as retry_error:
                            raise RuntimeError(
                                f"Boot disk {boot_disk_name!r} creation retry failed"
                            ) from retry_error
                elif already_exists and not recreate:
                    print("[VMManager] Disk already exists, refetching ID...")
                    boot_disk_id = self._lookup_boot_disk_id(disk_client, boot_disk_name)
                else:
                    raise RuntimeError(f"Boot disk {boot_disk_name!r} creation failed") from error

        if not boot_disk_id:
            raise RuntimeError(f"Boot disk {boot_disk_name!r} has no authoritative identity")

        if spec.vm_ha is not None:
            self._complete_vm_ha_effect(
                effect,
                resource_updates={binding_key: boot_disk_id},
            )
        return boot_disk_id

    @staticmethod
    def _desired_public_ips(
        spec: GatewayGroupSpec,
        instance_index: int,
        num_nics: int,
    ) -> list[str]:
        if not spec.external_ips or instance_index >= len(spec.external_ips):
            return []
        instance_ips = spec.external_ips[instance_index] or []
        if not isinstance(instance_ips, list):
            return []
        return [ip for ip in instance_ips[:num_nics] if ip]

    def _get_allocation_by_name(
        self,
        alloc_client: t.Any,
        alloc_name: str,
        *,
        strict: bool = False,
    ) -> t.Any | None:
        if alloc_client is None:
            raise RuntimeError("Public allocation lookup requires the exact-name SDK API")
        try:
            from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore

            allocation = alloc_client.get_by_name(
                GetAllocationByNameRequest(
                    parent_id=self.project_id or "",
                    name=alloc_name,
                )
            ).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(
                f"Public allocation {alloc_name!r} could not be classified"
            ) from error
        del strict
        metadata = getattr(allocation, "metadata", None)
        if (
            not self._resource_id(allocation)
            or str(getattr(metadata, "name", "") or "") != alloc_name
            or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
        ):
            raise RuntimeError(f"Public allocation {alloc_name!r} returned an inexact identity")
        return allocation

    def _get_allocation_by_id(self, alloc_client: t.Any, allocation_id: str) -> t.Any | None:
        if alloc_client is None:
            raise RuntimeError("Public allocation lookup requires the exact-ID SDK API")
        try:
            from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

            allocation = alloc_client.get(GetAllocationRequest(id=allocation_id)).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError("Public allocation identity could not be classified") from error
        if self._resource_id(allocation) != allocation_id or str(
            getattr(getattr(allocation, "metadata", None), "parent_id", "") or ""
        ) != (self.project_id or ""):
            raise RuntimeError("Public allocation lookup returned an inexact identity")
        return allocation

    @staticmethod
    def _allocation_name(alloc_obj: t.Any, fallback: str | None = None) -> str:
        name = getattr(getattr(alloc_obj, "metadata", None), "name", None)
        if name:
            return str(name)
        alloc_id = getattr(alloc_obj, "id", None) or getattr(
            getattr(alloc_obj, "metadata", None),
            "id",
            None,
        )
        if alloc_id:
            return str(alloc_id)
        return fallback or "allocation"

    @staticmethod
    def _allocation_is_transitional(state: str | None) -> bool:
        return bool(
            state and any(token in state.lower() for token in ("delet", "releas", "pending"))
        )

    def _hydrate_allocation(self, alloc_client: t.Any, alloc_obj: t.Any | None) -> t.Any | None:
        if alloc_client is None or alloc_obj is None:
            return alloc_obj
        alloc_id = self._resource_id(alloc_obj)
        if not alloc_id:
            return alloc_obj
        return self._get_allocation_by_id(alloc_client, alloc_id) or alloc_obj

    def _resolve_known_allocation_ip(
        self, alloc_client: t.Any, alloc_obj: t.Any | None
    ) -> str | None:
        if alloc_obj is None:
            return None
        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        alloc_ip = self._allocation_ip_from_obj(alloc_obj)
        if alloc_ip:
            return alloc_ip
        alloc_id = self._resource_id(alloc_obj)
        if alloc_id:
            return self._normalize_ip_value(self.get_allocation_ip(alloc_id))
        return None

    def _validate_requested_public_allocation(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        *,
        desired_ip: str,
        alloc_name: str,
        require_resolved_ip: bool,
        expected_attachment: tuple[str, str] | None = None,
    ) -> t.Any:
        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        resolved_ip = self._resolve_known_allocation_ip(alloc_client, alloc_obj)
        actual_name = self._allocation_name(alloc_obj, alloc_name)

        if require_resolved_ip and not resolved_ip:
            raise RuntimeError(
                f"Public IP allocation {actual_name} exists, but its IP could not be resolved to "
                f"confirm the requested external_ips value {desired_ip}."
            )
        if resolved_ip and resolved_ip != desired_ip:
            raise RuntimeError(
                f"Public IP allocation {actual_name} has IP {resolved_ip}, but "
                f"external_ips requested {desired_ip}."
            )
        if self._allocation_is_attached(alloc_obj):
            assignment = getattr(
                getattr(getattr(alloc_obj, "status", None), "assignment", None),
                "network_interface",
                None,
            )
            observed_attachment = (
                str(getattr(assignment, "instance_id", "") or ""),
                str(getattr(assignment, "name", "") or ""),
            )
            if expected_attachment is None or observed_attachment != expected_attachment:
                raise RuntimeError(
                    f"Requested public IP allocation {desired_ip} ({actual_name}) is already "
                    "attached to another resource. Detach it before using it in "
                    "gateway_group.external_ips."
                )
        return alloc_obj

    def _require_public_allocation_in_gateway_subnet(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        target_subnet_id: str | None,
        desired_ip: str | None,
    ) -> t.Any:
        if not alloc_obj or not target_subnet_id:
            return alloc_obj

        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        alloc_spec = getattr(alloc_obj, "spec", None)
        ipv4_public = getattr(alloc_spec, "ipv4_public", None) if alloc_spec else None
        if not ipv4_public:
            return alloc_obj

        current_subnet_id = getattr(ipv4_public, "subnet_id", None)
        if not current_subnet_id or current_subnet_id == target_subnet_id:
            if current_subnet_id == target_subnet_id:
                print(f"[VMManager] Allocation {desired_ip} already in the gateway subnet")
            return alloc_obj

        alloc_meta = getattr(alloc_obj, "metadata", None)
        alloc_id = getattr(alloc_obj, "id", None) or getattr(alloc_meta, "id", None)
        alloc_name = getattr(alloc_meta, "name", None) if alloc_meta else None
        raise RuntimeError(
            f"Public IP allocation {alloc_name or alloc_id} is bound to subnet {current_subnet_id} "
            f"and cannot be moved to gateway subnet {target_subnet_id}. "
            "Nebius marks public allocation subnet binding as immutable. "
            "Options: (1) deploy the gateway in the original subnet/network so the IP matches, "
            "or (2) remove the IP from external_ips to allow a new allocation, "
            "or (3) release the old allocation and re-request the same IP in the gateway subnet "
            "(best effort only)."
        )

    def _validate_vm_ha_replayed_public_allocation(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        *,
        alloc_name: str,
        subnet_id: str,
        desired_ip: str,
    ) -> t.Any:
        """Validate the exact unattached public allocation after create replay."""

        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        metadata = getattr(alloc_obj, "metadata", None)
        spec = getattr(alloc_obj, "spec", None)
        ipv4_public = getattr(spec, "ipv4_public", None) if spec is not None else None
        if (
            self._allocation_name(alloc_obj) != alloc_name
            or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
            or ipv4_public is None
            or getattr(spec, "ipv4_private", None) is not None
            or str(getattr(ipv4_public, "subnet_id", "") or "") != subnet_id
        ):
            raise RuntimeError(f"VM-HA public allocation {alloc_name} has a foreign resource shape")
        state = self._allocation_state(alloc_obj)
        if not state or state.rsplit(".", 1)[-1].upper() != "ALLOCATED":
            raise RuntimeError(f"VM-HA public allocation {alloc_name} is not stably allocated")
        return self._validate_requested_public_allocation(
            alloc_client,
            alloc_obj,
            desired_ip=desired_ip,
            alloc_name=alloc_name,
            require_resolved_ip=True,
        )

    def _find_requested_public_allocation(
        self,
        alloc_client: t.Any,
        alloc_api: t.Any,
        alloc_name: str,
        desired_ip: str,
        allocations_by_ip: dict[str, t.Any] | None = None,
        *,
        strict: bool = False,
        expected_attachment: tuple[str, str] | None = None,
    ) -> tuple[t.Any | None, dict[str, t.Any]]:
        normalized_ip = self._normalize_ip_value(desired_ip)
        if not normalized_ip:
            return None, allocations_by_ip or {}

        mapping = allocations_by_ip or {}
        alloc_obj = mapping.get(normalized_ip)
        if alloc_obj is None and alloc_client is not None:
            mapping = self._list_allocations_by_ip(alloc_client, fail_closed=strict)
            alloc_obj = mapping.get(normalized_ip)

        if alloc_obj is None and alloc_api is not None:
            get_by_addr = getattr(alloc_api, "get_by_address", None)
            if get_by_addr:
                try:
                    alloc_obj = get_by_addr(address=normalized_ip, project_id=self.project_id)
                except Exception:
                    alloc_obj = None

        if alloc_obj is not None:
            alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
            state = self._allocation_state(alloc_obj)
            if alloc_client is not None and self._allocation_is_transitional(state):
                mapping = self._wait_for_allocation_release(
                    alloc_client,
                    normalized_ip,
                    fail_closed=strict,
                )
                alloc_obj = mapping.get(normalized_ip)
                alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
            if alloc_obj is not None:
                alloc_obj = self._validate_requested_public_allocation(
                    alloc_client,
                    alloc_obj,
                    desired_ip=normalized_ip,
                    alloc_name=alloc_name,
                    require_resolved_ip=False,
                    expected_attachment=expected_attachment,
                )
                return alloc_obj, mapping

        by_name = self._get_allocation_by_name(alloc_client, alloc_name, strict=strict)
        if by_name is None:
            return None, mapping

        by_name = self._validate_requested_public_allocation(
            alloc_client,
            by_name,
            desired_ip=normalized_ip,
            alloc_name=alloc_name,
            require_resolved_ip=True,
            expected_attachment=expected_attachment,
        )
        return by_name, mapping

    def _create_public_allocation_via_client(
        self,
        alloc_client: t.Any,
        alloc_name: str,
        subnet_id: str,
        desired_ip: str | None,
        operation_id: str | None = None,
    ) -> t.Any | None:
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.vpc.v1 import (  # type: ignore
                AllocationSpec,
                CreateAllocationRequest,
                IPv4PublicAllocationSpec,
            )

            request = CreateAllocationRequest(
                metadata=ResourceMetadata(
                    name=alloc_name,
                    parent_id=self.project_id or "",
                ),
                spec=AllocationSpec(
                    ipv4_public=IPv4PublicAllocationSpec(
                        subnet_id=subnet_id,
                        cidr="/32" if not desired_ip else desired_ip,
                    )
                ),
            )
            operation = alloc_client.create(
                request,
                **(
                    vm_ha_request_kwargs(operation_id)
                    if operation_id
                    else self._idempotency_kwargs(operation_id)
                ),
            ).wait()
            if operation_id:
                self._sync_vm_ha_operation(operation)
            else:
                self._sync_operation(operation)
            resource = getattr(getattr(operation, "result", None), "resource", None)
            return (
                resource
                if resource is not None
                else self._get_allocation_by_name(
                    alloc_client,
                    alloc_name,
                )
            )
        except Exception as e:
            print(f"[VMManager] allocation create via client failed: {e}")
            if operation_id:
                raise
            return self._get_allocation_by_name(alloc_client, alloc_name)

    def _create_public_allocation_via_api(
        self,
        alloc_api: t.Any,
        alloc_name: str,
        subnet_id: str,
    ) -> t.Any | None:
        create_args = {
            "name": alloc_name,
            "ipv_4_public_subnet_id": subnet_id,
            **({"project_id": self.project_id} if self.project_id else {}),
        }
        try:
            return alloc_api.create(**create_args)  # type: ignore
        except TypeError:
            return alloc_api.create(create_args)

    def _ensure_public_allocation(
        self,
        alloc_api: t.Any,
        alloc_client: t.Any,
        inst_name: str,
        nic_name: str,
        subnet_id: str | None,
        desired_ip: str | None,
        preserved_alloc_id: str | None,
        approved_allocation_id: str | None = None,
        operation_id: str | None = None,
    ) -> tuple[str, t.Any | None]:
        alloc_name = f"{inst_name}-{nic_name}-ip"
        alloc_obj = None

        if approved_allocation_id:
            alloc_obj = self._get_allocation_by_id(alloc_client, approved_allocation_id)
            if self._resource_id(alloc_obj) != approved_allocation_id:
                raise RuntimeError(
                    f"VM-HA approved public allocation changed identity for {inst_name}"
                )
            approved_ip = desired_ip or self._resolve_known_allocation_ip(alloc_client, alloc_obj)
            if not subnet_id or not approved_ip:
                raise RuntimeError(
                    f"VM-HA approved public allocation is incomplete for {inst_name}"
                )
            alloc_obj = self._validate_vm_ha_replayed_public_allocation(
                alloc_client,
                alloc_obj,
                alloc_name=alloc_name,
                subnet_id=subnet_id,
                desired_ip=approved_ip,
            )
        elif desired_ip and self._vm_ha_journal is None:
            alloc_obj, _ = self._find_requested_public_allocation(
                alloc_client,
                alloc_api,
                alloc_name,
                desired_ip,
            )
            if alloc_obj is not None:
                print(f"[VMManager] Found existing allocation with IP {desired_ip}")
                alloc_obj = self._require_public_allocation_in_gateway_subnet(
                    alloc_client,
                    alloc_obj,
                    subnet_id,
                    desired_ip,
                )

        if (
            alloc_obj is None
            and not desired_ip
            and preserved_alloc_id
            and self._vm_ha_journal is None
        ):
            try:
                alloc_obj = self._get_allocation_by_id(alloc_client, preserved_alloc_id)
                if alloc_obj:
                    preserved_ip = self.get_allocation_ip(preserved_alloc_id)
                    print(
                        f"[VMManager] Reusing preserved allocation {preserved_alloc_id} ({preserved_ip}) for {inst_name} {nic_name}"
                    )
                    alloc_obj = self._require_public_allocation_in_gateway_subnet(
                        alloc_client,
                        alloc_obj,
                        subnet_id,
                        preserved_ip,
                    )
            except Exception as e:
                print(
                    f"[VMManager] Could not retrieve preserved allocation {preserved_alloc_id}: {e}"
                )
                alloc_obj = None

        if alloc_obj is None and self._vm_ha_journal is None:
            alloc_obj = self._get_allocation_by_name(alloc_client, alloc_name)
            if alloc_obj:
                print(f"[VMManager] Found existing allocation by name: {alloc_name}")
                by_name_ip = self._resolve_known_allocation_ip(alloc_client, alloc_obj)
                alloc_obj = self._require_public_allocation_in_gateway_subnet(
                    alloc_client,
                    alloc_obj,
                    subnet_id,
                    by_name_ip,
                )

        if alloc_obj is not None:
            return alloc_name, alloc_obj

        if not subnet_id:
            raise RuntimeError(
                "[VMManager] Cannot create public IP allocation: subnet_id is not set. "
                "Resolve subnet creation first or provide a valid network_id."
            )

        if desired_ip:
            print(
                f"[VMManager] Creating public IP allocation {alloc_name} for {nic_name} in the gateway subnet requesting IP {desired_ip} ..."
            )
        else:
            print(
                f"[VMManager] Creating public IP allocation {alloc_name} for {nic_name} in the gateway subnet ..."
            )

        try:
            if alloc_client is not None:
                created_alloc = self._create_public_allocation_via_client(
                    alloc_client,
                    alloc_name,
                    subnet_id,
                    desired_ip,
                    operation_id,
                )
                if desired_ip and created_alloc is not None:
                    created_alloc = self._validate_requested_public_allocation(
                        alloc_client,
                        created_alloc,
                        desired_ip=desired_ip,
                        alloc_name=alloc_name,
                        require_resolved_ip=False,
                    )
                return alloc_name, created_alloc
            if alloc_api is not None:
                created_alloc = self._create_public_allocation_via_api(
                    alloc_api,
                    alloc_name,
                    subnet_id,
                )
                if desired_ip and created_alloc is not None:
                    created_alloc = self._validate_requested_public_allocation(
                        alloc_client,
                        created_alloc,
                        desired_ip=desired_ip,
                        alloc_name=alloc_name,
                        require_resolved_ip=False,
                    )
                return alloc_name, created_alloc
        except Exception as e:
            if self._vm_ha_journal is not None:
                if (
                    not operation_id
                    or not desired_ip
                    or alloc_client is None
                    or not nebius_request_error_code_is(e, "ALREADY_EXISTS")
                ):
                    raise
                existing = self._find_ha_allocation_by_name(alloc_client, alloc_name)
                if existing is None:
                    raise RuntimeError(
                        f"VM-HA public allocation {alloc_name} was absent after create conflict"
                    ) from e
                return alloc_name, self._validate_vm_ha_replayed_public_allocation(
                    alloc_client,
                    existing,
                    alloc_name=alloc_name,
                    subnet_id=subnet_id,
                    desired_ip=desired_ip,
                )
            print(f"[VMManager] allocation create failed: {e}")

        return alloc_name, None

    def _create_private_allocation_via_client(
        self,
        alloc_client: t.Any,
        alloc_name: str,
        subnet_id: str,
        operation_id: str | None = None,
    ) -> t.Any | None:
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.vpc.v1 import (  # type: ignore
                AllocationSpec,
                CreateAllocationRequest,
                IPv4PrivateAllocationSpec,
            )

            request = CreateAllocationRequest(
                metadata=ResourceMetadata(
                    name=alloc_name,
                    parent_id=self.project_id or "",
                ),
                spec=AllocationSpec(ipv4_private=IPv4PrivateAllocationSpec(subnet_id=subnet_id)),
            )
            operation = alloc_client.create(
                request,
                **(
                    vm_ha_request_kwargs(operation_id)
                    if operation_id
                    else self._idempotency_kwargs(operation_id)
                ),
            ).wait()
            if operation_id:
                self._sync_vm_ha_operation(operation)
            else:
                self._sync_operation(operation)
            resource = getattr(getattr(operation, "result", None), "resource", None)
            return (
                resource
                if resource is not None
                else self._get_allocation_by_name(
                    alloc_client,
                    alloc_name,
                )
            )
        except Exception as e:
            print(f"[VMManager] private allocation create via client failed: {e}")
            if operation_id:
                raise
            return self._get_allocation_by_name(alloc_client, alloc_name)

    def _find_ha_allocation_by_name(self, alloc_client: t.Any, alloc_name: str) -> t.Any | None:
        """Read one exact named HA allocation without translating failure to absence."""
        if alloc_client is None or not self.project_id:
            raise RuntimeError("VM-HA allocation lookup requires an SDK client and project ID")
        from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore

        try:
            listed = alloc_client.get_by_name(
                GetAllocationByNameRequest(
                    parent_id=self.project_id,
                    name=alloc_name,
                ),
                **vm_ha_request_kwargs(),
            ).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(
                f"VM-HA allocation {alloc_name!r} could not be classified"
            ) from error
        metadata = getattr(listed, "metadata", None)
        allocation_id = self._resource_id(listed)
        if (
            not allocation_id
            or str(getattr(metadata, "name", "") or "") != alloc_name
            or str(getattr(metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"VM-HA allocation {alloc_name!r} returned an inexact identity")
        allocation = self.get_ha_allocation(allocation_id)
        reread_metadata = getattr(allocation, "metadata", None)
        if (
            self._resource_id(allocation) != allocation_id
            or str(getattr(reread_metadata, "name", "") or "") != alloc_name
            or str(getattr(reread_metadata, "parent_id", "") or "") != self.project_id
        ):
            raise RuntimeError(f"VM-HA allocation {alloc_name} changed identity during re-read")
        return allocation

    def _ensure_vm_ha_shared_allocation(
        self,
        alloc_client: t.Any,
        spec: GatewayGroupSpec,
        subnet_id: str | None,
    ) -> str:
        """Create or reuse exactly one deterministic shared private allocation."""
        vm_ha = spec.vm_ha
        if vm_ha is None:
            raise RuntimeError("VM-HA shared allocation requested without explicit VM HA")
        if alloc_client is None or not subnet_id:
            raise RuntimeError("VM-HA provisioning requires allocation SDK and resolved subnet")

        allocation_name = f"{spec.name}-{vm_ha.cluster_id}-shared-private-ip"
        effect = "provision-shared-allocation"
        binding_key = "shared-allocation-id"
        approved_allocation_id = self._vm_ha_resource_binding(binding_key)
        approved_owner_compute = self._vm_ha_resource_binding("shared-allocation-owner-compute")
        approved_owner_nic = self._vm_ha_resource_binding("shared-allocation-owner-nic")
        approved_owner = (
            (approved_owner_compute, approved_owner_nic)
            if approved_owner_compute and approved_owner_nic
            else None
        )
        operation_id = self._begin_vm_ha_effect(effect)
        allocation = None
        if approved_allocation_id:
            allocation = self.get_ha_allocation(approved_allocation_id)
            validate_vm_ha_shared_allocation(
                allocation,
                expected_allocation_id=approved_allocation_id,
                expected_name=allocation_name,
                expected_project_id=self.project_id or "",
                expected_subnet_id=subnet_id,
                expected_owner=approved_owner,
            )
        else:
            try:
                from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
                from nebius.api.nebius.vpc.v1 import (  # type: ignore
                    AllocationSpec,
                    CreateAllocationRequest,
                    IPv4PrivateAllocationSpec,
                )

                operation = alloc_client.create(
                    CreateAllocationRequest(
                        metadata=ResourceMetadata(
                            name=allocation_name,
                            parent_id=self.project_id or "",
                        ),
                        spec=AllocationSpec(
                            ipv4_private=IPv4PrivateAllocationSpec(subnet_id=subnet_id)
                        ),
                    ),
                    **vm_ha_request_kwargs(operation_id),
                ).wait()
                self._sync_vm_ha_operation(operation)
                created = getattr(getattr(operation, "result", None), "resource", None)
                approved_allocation_id = self._resource_id(created) or str(
                    getattr(operation, "resource_id", "") or ""
                )
                if not approved_allocation_id:
                    raise RuntimeError(
                        "VM-HA allocation create returned no authoritative resource identity"
                    )
            except Exception as create_error:
                if not nebius_request_error_code_is(create_error, "ALREADY_EXISTS"):
                    raise RuntimeError(
                        f"VM-HA shared allocation {allocation_name} could not be created"
                    ) from create_error
            allocation = self._find_ha_allocation_by_name(alloc_client, allocation_name)
            if allocation is None:
                raise RuntimeError(
                    f"VM-HA shared allocation {allocation_name} was absent after create"
                )

        allocation_id = self._resource_id(allocation)
        if not allocation_id:
            raise RuntimeError(f"VM-HA shared allocation {allocation_name} has no ID")
        if approved_allocation_id and allocation_id != approved_allocation_id:
            raise RuntimeError("VM-HA shared allocation changed identity after create")
        validate_vm_ha_shared_allocation(
            allocation,
            expected_allocation_id=allocation_id,
            expected_name=allocation_name,
            expected_project_id=self.project_id or "",
            expected_subnet_id=subnet_id,
            expected_owner=approved_owner,
        )
        self._complete_vm_ha_effect(
            effect,
            resource_updates={binding_key: allocation_id},
        )
        self._vm_ha_shared_allocation_id = allocation_id
        return allocation_id

    def _ensure_private_allocation(
        self,
        alloc_client: t.Any,
        inst_name: str,
        nic_name: str,
        subnet_id: str | None,
        approved_allocation_id: str | None = None,
        operation_id: str | None = None,
    ) -> tuple[str, t.Any | None]:
        alloc_name = f"{inst_name}-{nic_name}-private-ip"
        alloc_obj = (
            self._get_allocation_by_id(alloc_client, approved_allocation_id)
            if approved_allocation_id
            else None
        )
        if approved_allocation_id and self._resource_id(alloc_obj) != approved_allocation_id:
            raise RuntimeError(
                f"VM-HA approved primary allocation changed identity for {inst_name}"
            )
        if alloc_obj is None and self._vm_ha_journal is None:
            alloc_obj = self._get_allocation_by_name(alloc_client, alloc_name)
        if alloc_obj is not None:
            print(f"[VMManager] Found existing private allocation by name: {alloc_name}")
            return alloc_name, alloc_obj

        if alloc_client is None:
            return alloc_name, None

        if not subnet_id:
            raise RuntimeError(
                "[VMManager] Cannot create private IP allocation: subnet_id is not set. "
                "Resolve subnet creation first or provide a valid network_id."
            )

        print(
            f"[VMManager] Creating static private IP allocation {alloc_name} for {nic_name} in the gateway subnet ..."
        )
        try:
            return alloc_name, self._create_private_allocation_via_client(
                alloc_client,
                alloc_name,
                subnet_id,
                operation_id,
            )
        except Exception as e:
            print(f"[VMManager] private allocation create failed: {e}")
            if self._vm_ha_journal is not None:
                raise
            return alloc_name, None

    def _ensure_instance_allocations(
        self,
        alloc_api: t.Any,
        alloc_client: t.Any,
        spec: GatewayGroupSpec,
        inst_name: str,
        instance_index: int,
        provisioning: VMProvisioningConfig,
        preserved_alloc_ids: list[str],
        vm_ips: dict[str, str],
    ) -> list[str]:
        desired_ips = self._desired_public_ips(spec, instance_index, provisioning.num_nics)
        alloc_ids: list[str] = []
        self._private_alloc_ids[inst_name] = []

        if alloc_api is None and alloc_client is None:
            return alloc_ids

        for nic_index in range(provisioning.num_nics):
            nic_name = f"eth{nic_index}"
            desired_ip = desired_ips[nic_index] if nic_index < len(desired_ips) else None
            preserved_alloc_id = (
                preserved_alloc_ids[nic_index] if nic_index < len(preserved_alloc_ids) else None
            )
            public_effect = f"provision-{inst_name}-{nic_name}-public-allocation"
            public_key = f"public-allocation:{inst_name}:{nic_name}"
            public_operation_id = (
                self._begin_vm_ha_effect(public_effect) if spec.vm_ha is not None else None
            )
            alloc_name, alloc_obj = self._ensure_public_allocation(
                alloc_api,
                alloc_client,
                inst_name,
                nic_name,
                provisioning.subnet_id,
                desired_ip,
                preserved_alloc_id,
                approved_allocation_id=self._vm_ha_resource_binding(public_key),
                operation_id=public_operation_id,
            )
            alloc_id = self._resource_id(alloc_obj)
            if alloc_id:
                alloc_ids.append(alloc_id)
                print(f"[VMManager] Public IP allocation {alloc_name} ready: {alloc_id}")
                if nic_index == 0:
                    alloc_ip = self.get_allocation_ip(alloc_id)
                    if alloc_ip:
                        vm_ips[inst_name] = alloc_ip
                if spec.vm_ha is not None:
                    self._complete_vm_ha_effect(
                        public_effect,
                        resource_updates={public_key: alloc_id},
                    )
            elif spec.vm_ha is not None:
                raise RuntimeError(f"VM-HA member {inst_name} requires an exact public allocation")

            private_effect = f"provision-{inst_name}-{nic_name}-primary-allocation"
            private_key = f"primary-allocation:{inst_name}:{nic_name}"
            private_operation_id = (
                self._begin_vm_ha_effect(private_effect) if spec.vm_ha is not None else None
            )
            private_alloc_name, private_alloc_obj = self._ensure_private_allocation(
                alloc_client,
                inst_name,
                nic_name,
                provisioning.subnet_id,
                approved_allocation_id=self._vm_ha_resource_binding(private_key),
                operation_id=private_operation_id,
            )
            private_alloc_id = self._resource_id(private_alloc_obj)
            if private_alloc_id:
                self._private_alloc_ids[inst_name].append(private_alloc_id)
                print(
                    f"[VMManager] Private IP allocation {private_alloc_name} ready: {private_alloc_id}"
                )
                if spec.vm_ha is not None:
                    self._complete_vm_ha_effect(
                        private_effect,
                        resource_updates={private_key: private_alloc_id},
                    )
            elif spec.vm_ha is not None:
                raise RuntimeError(
                    f"VM-HA member {inst_name} requires an independent primary private allocation"
                )

        return alloc_ids

    def _create_instance_with_fallback(
        self,
        client: t.Any,
        instance_api: t.Any,
        inst_name: str,
        provisioning: VMProvisioningConfig,
        boot_disk_id: str | None,
        alloc_ids: list[str],
        vm_ips: dict[str, str],
        *,
        strict_vm_ha: bool = False,
        operation_id: str | None = None,
    ) -> bool:
        inst_req = {
            "metadata": {
                "name": inst_name,
                **({"parent_id": self.project_id} if self.project_id else {}),
            },
            "spec": {
                "resources": {
                    "platform": provisioning.platform,
                    **({"preset": provisioning.preset} if provisioning.preset else {}),
                },
                **(
                    {
                        "boot_disk": {
                            "attach_mode": "READ_WRITE",
                            "device_id": "boot",
                            "existing_disk": {"id": boot_disk_id},
                        }
                    }
                    if boot_disk_id
                    else {}
                ),
                "network_interfaces": [
                    {
                        "name": f"eth{nic_idx}",
                        "ip_address": (
                            {"allocation_id": self._private_alloc_ids[inst_name][nic_idx]}
                            if nic_idx < len(self._private_alloc_ids.get(inst_name, []))
                            else {}
                        ),
                        "public_ip_address": (
                            {"allocation_id": alloc_ids[nic_idx], "static": True}
                            if nic_idx < len(alloc_ids)
                            else {}
                        ),
                        "subnet_id": provisioning.subnet_id,
                    }
                    for nic_idx in range(min(provisioning.num_nics, 1))
                ],
                "cloud_init_user_data": provisioning.cloud_init,
            },
        }

        created = False
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.compute.v1 import (
                AttachedDiskSpec,
                CreateInstanceRequest,
                ExistingDisk,
                InstanceServiceClient,
                InstanceSpec,
                IPAddress,
                NetworkInterfaceSpec,
                PublicIPAddress,
                ResourcesSpec,
            )  # type: ignore

            isc = InstanceServiceClient(client)  # type: ignore
            print(
                f"[VMManager] Creating instance {inst_name} via InstanceServiceClient (project_id={self.project_id}) ..."
            )
            metadata = ResourceMetadata(name=inst_name, parent_id=self.project_id or "")
            if provisioning.preset:
                resources = ResourcesSpec(
                    platform=provisioning.platform,
                    preset=t.cast(t.Any, provisioning.preset),
                )
            else:
                resources = ResourcesSpec(platform=provisioning.platform)
            boot_disk_msg = None
            if boot_disk_id:
                boot_disk_msg = AttachedDiskSpec(
                    attach_mode=t.cast(t.Any, "READ_WRITE"),
                    device_id="boot",
                    existing_disk=ExistingDisk(id=boot_disk_id),
                )
            if not boot_disk_id:
                print(
                    "[VMManager] Warning: boot_disk_id missing; proceeding without boot_disk in spec."
                )

            ni_msgs = []
            for nic_idx in range(provisioning.num_nics):
                nic_name = f"eth{nic_idx}"
                pub = (
                    PublicIPAddress(allocation_id=alloc_ids[nic_idx], static=True)
                    if nic_idx < len(alloc_ids)
                    else None
                )
                priv_alloc_id = None
                priv = None
                if nic_idx < len(self._private_alloc_ids.get(inst_name, [])):
                    priv_alloc_id = self._private_alloc_ids[inst_name][nic_idx]
                    priv = IPAddress(allocation_id=priv_alloc_id)
                if priv is None:
                    priv = IPAddress()
                ni_msgs.append(
                    NetworkInterfaceSpec(
                        name=nic_name,
                        ip_address=priv,
                        public_ip_address=pub if pub is not None else PublicIPAddress(),
                        subnet_id=provisioning.subnet_id,
                    )
                )
                print(
                    f"[VMManager] NIC {nic_name} configured with public={alloc_ids[nic_idx] if nic_idx < len(alloc_ids) else 'auto'}, private={priv_alloc_id or 'auto'}"
                )

            if len(ni_msgs) > 1:
                print(
                    f"[VMManager] WARNING: {len(ni_msgs)} NICs configured but platform only supports 1. Using first NIC only."
                )
                ni_msgs = ni_msgs[:1]

            try:
                print(f"[VMManager] Using boot_disk_id={boot_disk_id}")
            except Exception:
                pass

            if boot_disk_msg is not None and boot_disk_id:
                spec = InstanceSpec(
                    resources=resources,
                    network_interfaces=ni_msgs,
                    cloud_init_user_data=provisioning.cloud_init,
                    boot_disk=boot_disk_msg,
                )
            else:
                spec = InstanceSpec(
                    resources=resources,
                    network_interfaces=ni_msgs,
                    cloud_init_user_data=provisioning.cloud_init,
                )
            req = CreateInstanceRequest(metadata=metadata, spec=spec)
            try:
                op = isc.create(
                    req,
                    **(
                        vm_ha_request_kwargs(operation_id)
                        if strict_vm_ha
                        else self._idempotency_kwargs(operation_id)
                    ),
                ).wait()
                if strict_vm_ha:
                    self._sync_vm_ha_operation(op)
                else:
                    try:
                        op.sync_wait()
                    except Exception:
                        pass
                created = True
                print(f"[VMManager] Instance {inst_name} created successfully via SDK")

                print(f"[VMManager] Waiting for {inst_name} to receive public IP...")
                max_ip_wait = 60
                ip_wait_interval = 5
                for attempt in range(max_ip_wait // ip_wait_interval):
                    time.sleep(ip_wait_interval)
                    vm_ip = self.get_vm_public_ip(inst_name)
                    if vm_ip:
                        print(f"[VMManager] {inst_name} ready with IP: {vm_ip}")
                        vm_ips[inst_name] = vm_ip
                        break
                    if attempt < (max_ip_wait // ip_wait_interval) - 1:
                        print(
                            f"[VMManager] Waiting for IP assignment ({(attempt + 1) * ip_wait_interval}s elapsed)..."
                        )
                else:
                    print(
                        f"[VMManager] Warning: {inst_name} did not receive public IP within {max_ip_wait}s"
                    )
            except Exception as e:
                print(f"[VMManager] InstanceServiceClient create failed: {e}")
                if strict_vm_ha:
                    raise
                import traceback

                traceback.print_exc()
        except Exception as e:
            print(f"[VMManager] InstanceServiceClient initialization failed: {e}")
            if strict_vm_ha:
                raise

        if created:
            return True

        if not strict_vm_ha and instance_api is not None and hasattr(instance_api, "create"):
            print(f"[VMManager] Creating instance {inst_name} ...")
            try:
                try:
                    instance_api.create(**inst_req)  # type: ignore[arg-type]
                except TypeError:
                    instance_api.create(inst_req)
                return True
            except Exception as e:
                print(f"[VMManager] create failed for {inst_name}: {e}")

        print(f"[VMManager] Would create with payload: {inst_req}")
        return False

    def _provision_instance(
        self,
        client: t.Any,
        instance_api: t.Any,
        alloc_api: t.Any,
        alloc_client: t.Any,
        spec: GatewayGroupSpec,
        instance_index: int,
        recreate: bool,
        provisioning: VMProvisioningConfig | None,
        preserved_allocations: dict[str, list[str]],
        vm_ips: dict[str, str],
        expected_vm_exists: bool | None = None,
    ) -> None:
        inst_name = f"{spec.name}-{instance_index}"
        vm_exists = self._instance_exists(client, inst_name)
        compute_key = f"compute:{inst_name}"
        compute_effect = f"provision-{inst_name}-compute"
        approved_compute_id = self._vm_ha_resource_binding(compute_key)
        pending_compute = bool(
            self._vm_ha_journal is not None
            and self._vm_ha_journal.state.transaction is not None
            and self._vm_ha_journal.state.transaction.pending_effect == compute_effect
        )
        if expected_vm_exists is not None and vm_exists != expected_vm_exists:
            raise RuntimeError(
                f"VM-HA member {inst_name} changed existence after identity preflight"
            )
        if vm_exists and not recreate:
            if spec.vm_ha is not None:
                authoritative = self._get_ha_instance_by_name(client, inst_name)
                observed_compute_id = self._resource_id(authoritative)
                if approved_compute_id:
                    if observed_compute_id != approved_compute_id:
                        raise RuntimeError(f"VM-HA Compute identity changed for {inst_name}")
                elif not pending_compute:
                    raise RuntimeError(
                        f"VM-HA Compute {inst_name} exists without approved transaction identity"
                    )
                else:
                    # Replay the exact idempotent create below so the accepted
                    # operation, rather than the resource name, proves ownership.
                    vm_exists = False
            if vm_exists:
                print(
                    f"[VMManager] VM {inst_name} already exists (recreate=False), skipping creation"
                )
                vm_ip = self.get_vm_public_ip(inst_name)
                if vm_ip:
                    vm_ips[inst_name] = vm_ip
                    print(f"[VMManager] {inst_name} IP: {vm_ip}")
                return
        if vm_exists and recreate:
            print(
                f"[VMManager] WARNING: VM {inst_name} still exists after deletion (race condition?)"
            )
            return

        if provisioning is None:
            raise RuntimeError(
                f"[VMManager] Internal error: provisioning config missing for {inst_name}."
            )

        boot_disk_id = self._ensure_boot_disk(
            client,
            spec,
            inst_name,
            provisioning,
            recreate,
        )
        alloc_ids = self._ensure_instance_allocations(
            alloc_api,
            alloc_client,
            spec,
            inst_name,
            instance_index,
            provisioning,
            preserved_allocations.get(inst_name, []),
            vm_ips,
        )
        operation_id = self._begin_vm_ha_effect(compute_effect) if spec.vm_ha is not None else None
        created = self._create_instance_with_fallback(
            client,
            instance_api,
            inst_name,
            provisioning,
            boot_disk_id,
            alloc_ids,
            vm_ips,
            strict_vm_ha=spec.vm_ha is not None,
            operation_id=operation_id,
        )
        if spec.vm_ha is not None:
            if not created:
                raise RuntimeError(f"VM-HA Compute {inst_name} was not created")
            authoritative = self._get_ha_instance_by_name(client, inst_name)
            compute_id = self._resource_id(authoritative)
            if not compute_id:
                raise RuntimeError(f"VM-HA Compute {inst_name} has no identity")
            expected_compute_id = self._vm_ha_resource_binding(compute_key)
            if expected_compute_id and expected_compute_id != compute_id:
                raise RuntimeError(f"VM-HA Compute identity changed for {inst_name}")
            self._complete_vm_ha_effect(
                compute_effect,
                resource_updates={compute_key: compute_id},
            )

    def _log_scaffold_mode_instances(self, spec: GatewayGroupSpec) -> None:
        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            inst_ips = spec.external_ips[i] if i < len(spec.external_ips) else []
            pub_ip = inst_ips[0] if inst_ips else None
            print(
                f"[VMManager] ensure instance {inst_name} pub_ip={pub_ip} platform={spec.vm_spec.get('platform')} subnet={self._gateway_subnet_name(spec)}"
            )

    def validate_missing_vm_ha_standby_replacement(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
        *,
        target_instance_name: str,
        retired_compute_id: str,
        replacement_disk_name: str,
        primary_allocation_id: str,
        public_allocation_id: str,
    ) -> None:
        """Prove the creation-only replacement footprint without touching old disks."""

        if spec.vm_ha is None:
            raise RuntimeError("VM-HA missing standby replacement requires explicit HA intent")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA missing standby replacement requires the Nebius SDK")
        self._require_ha_compute_absent(retired_compute_id)
        if self._get_vm_by_name_for_vm_ha_preflight(client, target_instance_name) is not None:
            raise RuntimeError("VM-HA missing standby configured Compute name is occupied")
        if self._get_ha_disk_by_name(client, replacement_disk_name) is not None:
            raise RuntimeError("VM-HA fresh standby disk name is already occupied")
        _instance_api, _disk_api, _alloc_api, allocation_client = self._resolve_client_apis(client)
        if allocation_client is None:
            raise RuntimeError("VM-HA missing standby replacement requires the Allocation API")
        self._require_retained_allocation(
            allocation_client,
            primary_allocation_id,
            require_detached=True,
        )
        self._require_retained_allocation(
            allocation_client,
            public_allocation_id,
            require_detached=True,
        )
        self._vm_ha_effect_spec = spec
        self._vm_ha_effect_prefixes = local_prefixes

    def replace_missing_vm_ha_standby(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
        *,
        approval_digest: str,
    ) -> VMProvisioningResult:
        """Create a fresh disk and Compute for one lifecycle-proven missing non-owner."""

        journal = self._vm_ha_journal
        if spec.vm_ha is None or journal is None or journal.state.transaction is None:
            raise RuntimeError("VM-HA missing standby replacement requires durable HA intent")
        state = journal.state
        transaction = t.cast(VMHAMigrationTransaction, state.transaction)
        if (
            state.status not in {VMHALifecycleStatus.PROVISIONING, VMHALifecycleStatus.ACTIVATING}
            or transaction.approval_kind != "recovery"
        ):
            raise RuntimeError(
                "VM-HA missing standby replacement requires its active transaction checkpoint"
            )
        bindings = dict(transaction.resource_bindings)
        matches: list[tuple[VMHALifecycleMember, int]] = []
        for member in state.members:
            cycle = vm_ha_passive_replacement_cycle_for_approval(
                bindings,
                member.instance_name,
                approval_digest,
            )
            if (
                cycle is not None
                and vm_ha_missing_standby_disk_name_binding_key(
                    member.instance_name,
                    cycle,
                )
                in bindings
            ):
                matches.append((member, cycle))
        if len(matches) != 1:
            raise RuntimeError("VM-HA missing standby replacement approval is not exact")
        target, replacement_cycle = matches[0]
        target_name = target.instance_name
        retired_compute_id = bindings.get(
            vm_ha_passive_replacement_binding_key(
                "retired-compute",
                target_name,
                replacement_cycle,
            )
        )
        retired_disk_id = bindings.get(
            vm_ha_passive_replacement_binding_key(
                "retired-disk",
                target_name,
                replacement_cycle,
            )
        )
        replacement_disk_name = bindings.get(
            vm_ha_missing_standby_disk_name_binding_key(
                target_name,
                replacement_cycle,
            )
        )
        primary_id = vm_ha_effective_resource_bindings(bindings).get(
            f"primary-allocation:{target_name}:eth0"
        )
        public_id = vm_ha_effective_resource_bindings(bindings).get(
            f"public-allocation:{target_name}:eth0"
        )
        if not all(
            (
                retired_compute_id,
                retired_disk_id,
                replacement_disk_name,
                primary_id,
                public_id,
            )
        ):
            raise RuntimeError("VM-HA missing standby replacement bindings are incomplete")

        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA missing standby replacement requires the Nebius SDK")
        self._vm_ha_effect_spec = spec
        self._vm_ha_effect_prefixes = local_prefixes

        def replacement_result() -> VMProvisioningResult:
            try:
                route_targets = tuple(
                    sorted(
                        (
                            VMHARouteTarget.model_validate(json.loads(value))
                            for value in state.route_targets
                        ),
                        key=lambda target: (
                            target.project_id,
                            target.network_id,
                            target.workload_subnet_id,
                            target.route_table_id,
                        ),
                    )
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("VM-HA lifecycle route targets are malformed") from error
            self._vm_ha_shared_allocation_id = state.allocation_id
            self._vm_ha_route_targets = route_targets
            vm_ips: dict[str, str] = {}
            for member in state.members:
                instance = self._get_ha_instance_by_name(client, member.instance_name)
                public_ip = self._vm_public_ip_from_object(instance)
                if not public_ip:
                    raise RuntimeError(
                        f"VM-HA member {member.instance_name} has no authoritative public IP"
                    )
                vm_ips[member.instance_name] = public_ip
            return VMProvisioningResult(
                vm_ips,
                vm_ha_runtime_binding=self._build_vm_ha_runtime_binding(client, spec),
            )

        completed = set(transaction.completed_effects)
        create_disk_effect = vm_ha_missing_standby_replacement_effect(
            target_name,
            replacement_cycle,
            "create-boot-disk",
        )
        create_compute_effect = vm_ha_missing_standby_replacement_effect(
            target_name,
            replacement_cycle,
            "create-compute",
        )

        if create_compute_effect in completed:
            replacement_compute_id = self._vm_ha_resource_binding(f"compute:{target_name}")
            replacement_compute = self._get_ha_instance_by_name(client, target_name)
            if (
                not replacement_compute_id
                or replacement_compute_id == retired_compute_id
                or self._resource_id(replacement_compute) != replacement_compute_id
            ):
                raise RuntimeError("VM-HA completed missing standby identity drifted")
            replacement_public_ip = self._vm_public_ip_from_object(replacement_compute)
            if not replacement_public_ip:
                raise RuntimeError("VM-HA completed missing standby has no public IP")
            self._wait_for_vm_ha_member_ssh(
                target_name,
                replacement_public_ip,
                username=(
                    spec.vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
            return replacement_result()

        self._verify_vm_ha_transaction_preconditions(spec, local_prefixes)
        self._require_ha_compute_absent(t.cast(str, retired_compute_id))
        current_target = self._get_vm_by_name_for_vm_ha_preflight(client, target_name)
        pending_compute = transaction.pending_effect == create_compute_effect
        accepted_compute = bool(
            pending_compute
            and transaction.accepted_cloud_operation_effect == create_compute_effect
            and transaction.accepted_cloud_operation_id
        )
        if current_target is not None and not accepted_compute:
            raise RuntimeError("VM-HA missing standby Compute name is occupied")

        resumed_compute_operation_id: str | None = None
        expected_allocation_owner: AllocationOwner | None = None
        if accepted_compute:
            resumed_compute_operation_id = self._begin_vm_ha_effect(create_compute_effect)
            accepted_compute_id = self._vm_ha_accepted_resource_ids.get(create_compute_effect)
            if (
                current_target is None
                or not accepted_compute_id
                or self._resource_id(current_target) != accepted_compute_id
                or not target.network_interface_name
            ):
                raise RuntimeError("VM-HA accepted missing standby Compute identity is invalid")
            expected_allocation_owner = AllocationOwner(
                accepted_compute_id,
                target.network_interface_name,
            )

        expected_names = {member.instance_name for member in state.members}
        rendered = self._prepare_gateway_ssh_enrollment_cloud_inits(
            spec,
            local_prefixes,
            expected_names - {target_name},
            False,
        )
        target_cloud_init = rendered.get(target_name)
        if not target_cloud_init:
            raise RuntimeError("VM-HA missing standby cloud-init was not prevalidated")
        provisioning = replace(
            self._build_vm_provisioning_config(client, spec, local_prefixes),
            cloud_init=target_cloud_init,
        )
        instance_api, _disk_api, _alloc_api, allocation_client = self._resolve_client_apis(client)
        if allocation_client is None:
            raise RuntimeError("VM-HA missing standby replacement requires the Allocation API")
        self._require_retained_allocation(
            allocation_client,
            t.cast(str, primary_id),
            require_detached=expected_allocation_owner is None,
            expected_owner=expected_allocation_owner,
        )
        self._require_retained_allocation(
            allocation_client,
            t.cast(str, public_id),
            require_detached=expected_allocation_owner is None,
            expected_owner=expected_allocation_owner,
        )

        replacement_disk_key = vm_ha_passive_replacement_binding_key(
            "disk",
            target_name,
            replacement_cycle,
        )
        replacement_disk_id = bindings.get(replacement_disk_key)
        if create_disk_effect not in completed:
            operation_id = self._begin_vm_ha_effect(create_disk_effect)
            transaction = t.cast(VMHAMigrationTransaction, journal.state.transaction)
            accepted_disk = bool(
                transaction.accepted_cloud_operation_effect == create_disk_effect
                and transaction.accepted_cloud_operation_id
            )
            replacement_disk = self._get_ha_disk_by_name(
                client,
                t.cast(str, replacement_disk_name),
            )
            if replacement_disk is not None and not accepted_disk:
                raise RuntimeError("VM-HA fresh standby disk name is occupied")
            accepted_disk_id = self._vm_ha_accepted_resource_ids.get(create_disk_effect)
            if replacement_disk is None or (accepted_disk and not accepted_disk_id):
                from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

                image_id = self._resolve_boot_image_id(client, spec, provisioning)
                if not image_id:
                    raise RuntimeError("VM-HA missing standby boot image could not be resolved")
                replacement_disk_id = self._submit_boot_disk_create(
                    DiskServiceClient(client),
                    self._build_boot_disk_create_request(
                        t.cast(str, replacement_disk_name),
                        provisioning,
                        image_id,
                    ),
                    t.cast(str, replacement_disk_name),
                    operation_id,
                )
                replacement_disk = self._get_ha_disk_by_name(
                    client,
                    t.cast(str, replacement_disk_name),
                )
            else:
                replacement_disk_id = self._resource_id(replacement_disk)
            accepted_disk_id = self._vm_ha_accepted_resource_ids.get(create_disk_effect)
            if (
                not replacement_disk_id
                or replacement_disk is None
                or self._resource_id(replacement_disk) != replacement_disk_id
                or replacement_disk_id == retired_disk_id
                or accepted_disk_id != replacement_disk_id
            ):
                raise RuntimeError("VM-HA fresh standby disk identity is invalid")
            self._complete_vm_ha_effect(
                create_disk_effect,
                resource_updates={replacement_disk_key: replacement_disk_id},
            )
            completed.add(create_disk_effect)
        else:
            replacement_disk = self._get_ha_disk_by_name(
                client,
                t.cast(str, replacement_disk_name),
            )
            if (
                not replacement_disk_id
                or replacement_disk is None
                or self._resource_id(replacement_disk) != replacement_disk_id
                or replacement_disk_id == retired_disk_id
            ):
                raise RuntimeError("VM-HA completed fresh standby disk identity drifted")

        operation_id = resumed_compute_operation_id or self._begin_vm_ha_effect(
            create_compute_effect
        )
        transaction = t.cast(VMHAMigrationTransaction, journal.state.transaction)
        accepted_compute = bool(
            transaction.accepted_cloud_operation_effect == create_compute_effect
            and transaction.accepted_cloud_operation_id
        )
        replacement_compute = self._get_vm_by_name_for_vm_ha_preflight(client, target_name)
        if replacement_compute is not None and not accepted_compute:
            raise RuntimeError("VM-HA missing standby Compute name is occupied")
        vm_ips: dict[str, str] = {}
        self._private_alloc_ids[target_name] = [t.cast(str, primary_id)]
        accepted_compute_id = self._vm_ha_accepted_resource_ids.get(create_compute_effect)
        if replacement_compute is None or (accepted_compute and not accepted_compute_id):
            created = self._create_instance_with_fallback(
                client,
                instance_api,
                target_name,
                provisioning,
                replacement_disk_id,
                [t.cast(str, public_id)],
                vm_ips,
                strict_vm_ha=True,
                operation_id=operation_id,
            )
            if not created:
                raise RuntimeError("VM-HA missing standby Compute was not created")
            replacement_compute = self._get_ha_instance_by_name(client, target_name)
        replacement_compute_id = self._resource_id(replacement_compute)
        accepted_compute_id = self._vm_ha_accepted_resource_ids.get(create_compute_effect)
        if (
            not replacement_compute_id
            or replacement_compute_id == retired_compute_id
            or accepted_compute_id != replacement_compute_id
        ):
            raise RuntimeError("VM-HA fresh standby Compute identity is invalid")
        self._complete_vm_ha_effect(
            create_compute_effect,
            resource_updates={
                vm_ha_passive_replacement_binding_key(
                    "compute",
                    target_name,
                    replacement_cycle,
                ): replacement_compute_id,
            },
        )
        replacement_public_ip = self._vm_public_ip_from_object(replacement_compute)
        if not replacement_public_ip:
            replacement_public_ip = vm_ips.get(target_name)
        if not replacement_public_ip:
            raise RuntimeError("VM-HA fresh standby has no public IP")
        self._wait_for_vm_ha_member_ssh(
            target_name,
            replacement_public_ip,
            username=(
                spec.vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
            ),
        )
        return replacement_result()

    def replace_failed_vm_ha_passive(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
        *,
        approval_digest: str,
    ) -> None:
        """Replace only a transaction-created passive that failed initial bootstrap."""

        journal = self._vm_ha_journal
        if spec.vm_ha is None or journal is None or journal.state.transaction is None:
            raise RuntimeError("VM-HA passive replacement requires durable explicit HA intent")
        state = journal.state
        if state.status is not VMHALifecycleStatus.PROVISIONING:
            raise RuntimeError("VM-HA passive replacement requires a PROVISIONING checkpoint")
        transaction = state.transaction
        assert transaction is not None
        passive_member = next(
            (member for member in state.members if member.role == "passive"),
            None,
        )
        if passive_member is None:
            raise RuntimeError("VM-HA passive replacement has no configured passive")
        passive_name = passive_member.instance_name
        bindings = dict(transaction.resource_bindings)
        replacement_cycle = vm_ha_passive_replacement_cycle_for_approval(
            bindings,
            passive_name,
            approval_digest,
        )
        if replacement_cycle is None:
            cycles = vm_ha_passive_replacement_cycles(bindings, passive_name)
            replacement_cycle = (cycles[-1] + 1) if cycles else 1
            current_bindings = vm_ha_effective_resource_bindings(bindings)
            retired_compute_id = current_bindings.get(f"compute:{passive_name}")
            retired_disk_id = current_bindings.get(f"disk:{passive_name}")
        else:
            retired_compute_id = bindings.get(
                vm_ha_passive_replacement_binding_key(
                    "retired-compute",
                    passive_name,
                    replacement_cycle,
                )
            )
            retired_disk_id = bindings.get(
                vm_ha_passive_replacement_binding_key(
                    "retired-disk",
                    passive_name,
                    replacement_cycle,
                )
            )
        if not retired_compute_id or not retired_disk_id:
            raise RuntimeError("VM-HA passive replacement lacks exact retired identities")

        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA passive replacement requires the Nebius SDK")
        self._vm_ha_effect_spec = spec
        self._vm_ha_effect_prefixes = local_prefixes
        before = self._stable_vm_ha_effect_observation()
        journal.authorize_failed_passive_replacement(
            passive_instance_name=passive_name,
            approval_digest=approval_digest,
            retired_compute_id=retired_compute_id,
            retired_disk_id=retired_disk_id,
            current_observation=before,
            replacement_cycle=replacement_cycle,
        )
        self._verify_vm_ha_transaction_preconditions(spec, local_prefixes)
        transaction = journal.state.transaction
        assert transaction is not None
        completed = set(transaction.completed_effects)

        create_compute_effect = vm_ha_passive_replacement_effect(
            passive_name,
            replacement_cycle,
            "create-compute",
        )
        if create_compute_effect in completed:
            replacement_compute_id = self._vm_ha_resource_binding(f"compute:{passive_name}")
            replacement_compute = self._get_ha_instance_by_name(client, passive_name)
            if (
                not replacement_compute_id
                or replacement_compute_id == retired_compute_id
                or self._resource_id(replacement_compute) != replacement_compute_id
            ):
                raise RuntimeError("VM-HA completed passive replacement identity drifted")
            replacement_public_ip = self._vm_public_ip_from_object(replacement_compute)
            if not replacement_public_ip:
                raise RuntimeError("VM-HA completed passive replacement has no public IP")
            self._wait_for_vm_ha_member_ssh(
                passive_name,
                replacement_public_ip,
                username=(
                    spec.vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                ),
            )
            return

        current_members = self._discover_vm_ha_members(client, spec)
        expected_names = {member.instance_name for member in state.members}
        active_names = {member.instance_name for member in state.members if member.role == "active"}
        if frozenset(current_members) not in {
            frozenset(expected_names),
            frozenset(active_names),
        }:
            raise RuntimeError("VM-HA passive replacement member set changed before deletion")
        rendered = self._prepare_gateway_ssh_enrollment_cloud_inits(
            spec,
            local_prefixes,
            expected_names - {passive_name},
            False,
        )
        passive_cloud_init = rendered.get(passive_name)
        if not passive_cloud_init:
            raise RuntimeError("VM-HA passive replacement cloud-init was not prevalidated")
        provisioning = replace(
            self._build_vm_provisioning_config(client, spec, local_prefixes),
            cloud_init=passive_cloud_init,
        )
        instance_api, _disk_api, _alloc_api, allocation_client = self._resolve_client_apis(client)
        if allocation_client is None:
            raise RuntimeError("VM-HA passive replacement requires the Allocation API")
        primary_id = self._vm_ha_resource_binding(f"primary-allocation:{passive_name}:eth0")
        public_id = self._vm_ha_resource_binding(f"public-allocation:{passive_name}:eth0")
        if not primary_id or not public_id:
            raise RuntimeError("VM-HA passive replacement lacks retained member allocations")
        self._require_retained_allocation(
            allocation_client,
            primary_id,
            require_detached=False,
        )
        self._require_retained_allocation(
            allocation_client,
            public_id,
            require_detached=False,
        )

        delete_compute_effect = vm_ha_passive_replacement_effect(
            passive_name,
            replacement_cycle,
            "delete-compute",
        )
        if delete_compute_effect not in completed:
            operation_id = self._begin_vm_ha_effect(delete_compute_effect)
            current_passive = self._get_vm_by_name_for_vm_ha_preflight(client, passive_name)
            if current_passive is not None:
                if self._resource_id(current_passive) != retired_compute_id:
                    raise RuntimeError("VM-HA passive replacement Compute identity changed")
                from nebius.api.nebius.compute.v1 import (  # type: ignore
                    DeleteInstanceRequest,
                    InstanceServiceClient,
                )

                operation = (
                    InstanceServiceClient(client)
                    .delete(
                        DeleteInstanceRequest(id=retired_compute_id),
                        **vm_ha_request_kwargs(operation_id),
                    )
                    .wait()
                )
                self._sync_vm_ha_operation(operation)
            if self._get_vm_by_name_for_vm_ha_preflight(client, passive_name) is not None:
                raise RuntimeError("VM-HA retired passive Compute still exists after deletion")
            self._complete_vm_ha_effect(delete_compute_effect)
            completed.add(delete_compute_effect)

        self._require_retained_allocation(
            allocation_client,
            primary_id,
            require_detached=True,
        )
        self._require_retained_allocation(
            allocation_client,
            public_id,
            require_detached=True,
        )

        boot_disk_name = f"{passive_name}-boot"
        delete_disk_effect = vm_ha_passive_replacement_effect(
            passive_name,
            replacement_cycle,
            "delete-boot-disk",
        )
        if delete_disk_effect not in completed:
            operation_id = self._begin_vm_ha_effect(delete_disk_effect)
            disk = self._get_ha_disk_by_name(client, boot_disk_name)
            if disk is not None:
                metadata = getattr(disk, "metadata", None)
                if (
                    self._resource_id(disk) != retired_disk_id
                    or str(getattr(metadata, "name", "") or "") != boot_disk_name
                    or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
                ):
                    raise RuntimeError("VM-HA passive replacement boot disk identity changed")
                from nebius.api.nebius.compute.v1 import (  # type: ignore
                    DeleteDiskRequest,
                    DiskServiceClient,
                )

                operation = (
                    DiskServiceClient(client)
                    .delete(
                        DeleteDiskRequest(id=retired_disk_id),
                        **vm_ha_request_kwargs(operation_id),
                    )
                    .wait()
                )
                self._sync_vm_ha_operation(operation)
            if self._get_ha_disk_by_name(client, boot_disk_name) is not None:
                raise RuntimeError("VM-HA retired passive boot disk still exists after deletion")
            self._complete_vm_ha_effect(delete_disk_effect)
            completed.add(delete_disk_effect)

        create_disk_effect = vm_ha_passive_replacement_effect(
            passive_name,
            replacement_cycle,
            "create-boot-disk",
        )
        operation_id = (
            None
            if create_disk_effect in completed
            else self._begin_vm_ha_effect(create_disk_effect)
        )
        replacement_disk = self._get_ha_disk_by_name(client, boot_disk_name)
        replacement_disk_id = self._resource_id(replacement_disk)
        if replacement_disk is None:
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

            image_id = self._resolve_boot_image_id(client, spec, provisioning)
            if not image_id:
                raise RuntimeError("VM-HA passive replacement could not resolve the boot image")
            replacement_disk_id = self._submit_boot_disk_create(
                DiskServiceClient(client),
                self._build_boot_disk_create_request(
                    boot_disk_name,
                    provisioning,
                    image_id,
                ),
                boot_disk_name,
                operation_id,
            )
            replacement_disk = self._get_ha_disk_by_name(client, boot_disk_name)
        if (
            not replacement_disk_id
            or replacement_disk is None
            or self._resource_id(replacement_disk) != replacement_disk_id
            or replacement_disk_id == retired_disk_id
        ):
            raise RuntimeError("VM-HA replacement passive boot disk identity is invalid")
        if create_disk_effect not in completed:
            self._complete_vm_ha_effect(
                create_disk_effect,
                resource_updates={
                    vm_ha_passive_replacement_binding_key(
                        "disk",
                        passive_name,
                        replacement_cycle,
                    ): replacement_disk_id
                },
            )
            completed.add(create_disk_effect)
        elif self._vm_ha_resource_binding(f"disk:{passive_name}") != replacement_disk_id:
            raise RuntimeError("VM-HA replacement passive boot disk identity drifted")

        operation_id = self._begin_vm_ha_effect(create_compute_effect)
        replacement_compute = self._get_vm_by_name_for_vm_ha_preflight(client, passive_name)
        vm_ips: dict[str, str] = {}
        self._private_alloc_ids[passive_name] = [primary_id]
        if replacement_compute is None:
            created = self._create_instance_with_fallback(
                client,
                instance_api,
                passive_name,
                provisioning,
                replacement_disk_id,
                [public_id],
                vm_ips,
                strict_vm_ha=True,
                operation_id=operation_id,
            )
            if not created:
                raise RuntimeError("VM-HA replacement passive Compute was not created")
            replacement_compute = self._get_ha_instance_by_name(client, passive_name)
        replacement_compute_id = self._resource_id(replacement_compute)
        if not replacement_compute_id or replacement_compute_id == retired_compute_id:
            raise RuntimeError("VM-HA replacement passive Compute identity is invalid")
        self._complete_vm_ha_effect(
            create_compute_effect,
            resource_updates={
                vm_ha_passive_replacement_binding_key(
                    "compute",
                    passive_name,
                    replacement_cycle,
                ): replacement_compute_id,
            },
        )
        replacement_public_ip = self._vm_public_ip_from_object(replacement_compute)
        if not replacement_public_ip:
            replacement_public_ip = vm_ips.get(passive_name)
        if not replacement_public_ip:
            raise RuntimeError("VM-HA replacement passive has no public IP")
        self._wait_for_vm_ha_member_ssh(
            passive_name,
            replacement_public_ip,
            username=(
                spec.vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
            ),
        )

    def ensure_group(
        self,
        spec: GatewayGroupSpec,
        recreate: bool = False,
        local_prefixes: list[str] | None = None,
    ) -> dict[str, str]:
        """Ensure gateway VMs exist per spec.

        Pseudocode for Nebius SDK integration:
        - client = InstanceServiceClient(auth=...)
        - existing = client.list(filter=name prefix)
        - if recreate: delete existing, wait; then create all
        - else: create missing, skip existing
        - attach public IPs according to spec.external_ips
        - set network interface subnet to spec.vm_spec.vpn_subnet_id

        Args:
            spec: Gateway group specification
            recreate: Whether to recreate existing VMs
            local_prefixes: Optional list of local VPC prefixes for firewall configuration

        Returns:
            Dict mapping VM names to their public IP addresses
        """
        print(
            f"[VMManager] ensure_group name={spec.name} count={spec.instance_count} region={spec.region} recreate={recreate}"
        )
        vm_ips: dict[str, str] = {}
        try:
            print(
                f"[VMManager] Using project_id={self.project_id} "
                f"region={self.region or spec.region}"
            )
        except Exception:
            pass
        client = self._build_sdk_client(spec.region)
        if spec.vm_ha is not None:
            self._vm_ha_effect_spec = spec
            self._vm_ha_effect_prefixes = local_prefixes

        try:
            if client is not None:
                instance_api, _disk_api, alloc_api, alloc_client = self._resolve_client_apis(client)
                vm_ha_existing: dict[str, tuple[t.Any, str]] = {}
                gateway_ssh_cloud_inits: dict[str, str] = {}
                if spec.vm_ha is not None:
                    vm_ha_existing = self._discover_vm_ha_members(client, spec)
                    self.verify_vm_ha_existing_identities(
                        {name: public_ip for name, (_, public_ip) in vm_ha_existing.items()},
                        username=(
                            spec.vm_spec.get("ssh_username")
                            or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                        ),
                    )
                    gateway_ssh_cloud_inits = self._prepare_gateway_ssh_enrollment_cloud_inits(
                        spec,
                        local_prefixes,
                        set(vm_ha_existing),
                        recreate,
                    )
                    self._require_vm_ha_member_snapshot(
                        client,
                        spec,
                        vm_ha_existing,
                    )
                    if self._vm_ha_journal is None:
                        raise RuntimeError(
                            "VM-HA provisioning requires a durable lifecycle transaction"
                        )
                    self._verify_vm_ha_transaction_preconditions(spec, local_prefixes)
                    existing = [vm_obj for vm_obj, _ in vm_ha_existing.values()]
                else:
                    existing = self._discover_existing_instances(client, spec)
                    if self._ssh_policy is not None:
                        existing_names = {
                            str(
                                getattr(getattr(instance, "metadata", None), "name", "")
                                or getattr(instance, "name", "")
                                or ""
                            )
                            for instance in existing
                        }
                        gateway_ssh_cloud_inits = self._prepare_gateway_ssh_enrollment_cloud_inits(
                            spec,
                            local_prefixes,
                            existing_names,
                            recreate,
                        )

                print(self._existing_instances_message(len(existing), recreate=recreate))

                preserved_allocations: dict[str, list[str]] = {}
                if recreate and existing:
                    preserved_allocations = self._collect_preserved_allocations(existing)
                    self._delete_existing_instances_and_boot_disks(client, existing, spec)

                if spec.vm_ha is not None:
                    self._require_vm_ha_member_snapshot(
                        client,
                        spec,
                        {} if recreate else vm_ha_existing,
                    )

                provisioning: VMProvisioningConfig | None = None
                if spec.vm_ha is not None:
                    provisioning = self._build_vm_provisioning_config(
                        client,
                        spec,
                        local_prefixes,
                    )
                    self._ensure_vm_ha_shared_allocation(
                        alloc_client,
                        spec,
                        provisioning.subnet_id,
                    )
                    route_effect = "resolve-authoritative-route-targets"
                    self._begin_vm_ha_effect(route_effect)
                    self._vm_ha_route_targets = self._resolve_vm_ha_route_targets(
                        client, spec, local_prefixes
                    )
                    self._complete_vm_ha_effect(route_effect)
                for i in range(spec.instance_count):
                    inst_name = f"{spec.name}-{i}"
                    if spec.vm_ha is not None:
                        expected_vm_exists: bool | None = (
                            False if recreate else inst_name in vm_ha_existing
                        )
                        needs_provisioning = recreate or not expected_vm_exists
                    else:
                        expected_vm_exists = None
                        needs_provisioning = not self._instance_exists(client, inst_name)
                    if needs_provisioning and provisioning is None:
                        provisioning = self._build_vm_provisioning_config(
                            client,
                            spec,
                            local_prefixes,
                        )
                    instance_provisioning = provisioning
                    if needs_provisioning and self._ssh_policy is not None:
                        if (
                            provisioning is None
                            or f"{spec.name}-{i}" not in gateway_ssh_cloud_inits
                        ):
                            raise RuntimeError(
                                "Gateway SSH enrollment cloud-init was not prevalidated"
                            )
                        instance_provisioning = replace(
                            provisioning,
                            cloud_init=gateway_ssh_cloud_inits[f"{spec.name}-{i}"],
                        )
                    self._provision_instance(
                        client,
                        instance_api,
                        alloc_api,
                        alloc_client,
                        spec,
                        i,
                        recreate,
                        instance_provisioning,
                        preserved_allocations,
                        vm_ips,
                        expected_vm_exists=expected_vm_exists,
                    )
                if spec.vm_ha is not None:
                    active_index = spec.vm_ha.active_instance_index
                    active_name = f"{spec.name}-{active_index}"
                    active = self._get_ha_instance_by_name(client, active_name)
                    active_id = self._resource_id(active)
                    active_interfaces = list(
                        getattr(getattr(active, "spec", None), "network_interfaces", []) or []
                    )
                    if not active_id or len(active_interfaces) != 1:
                        raise RuntimeError("VM-HA active Compute/NIC identity is unavailable")
                    active_nic_name = str(getattr(active_interfaces[0], "name", ""))
                    if not active_nic_name or not self._vm_ha_shared_allocation_id:
                        raise RuntimeError("VM-HA active attachment identity is incomplete")
                    self._attach_vm_ha_shared_allocation_initially(
                        allocation_id=self._vm_ha_shared_allocation_id,
                        active_compute_id=active_id,
                        active_network_interface_name=active_nic_name,
                    )
            else:
                if spec.vm_ha is not None:
                    raise RuntimeError("VM-HA provisioning requires the Nebius SDK")
                self._log_scaffold_mode_instances(spec)
        except Exception as e:
            if spec.vm_ha is not None:
                self._private_alloc_ids.clear()
                self._vm_ha_shared_allocation_id = None
                self._vm_ha_route_targets = None
                raise RuntimeError(f"VM-HA provisioning failed closed: {e}") from e
            if self._ssh_policy is not None:
                raise RuntimeError(f"Gateway provisioning failed closed: {e}") from e
            print(f"[VMManager] ensure_group failed: {e}. Proceeding in scaffold mode.")

        if spec.vm_ha is not None:
            try:
                binding_effect = "construct-authoritative-runtime-binding"
                self._begin_vm_ha_effect(binding_effect)
                binding = self._build_vm_ha_runtime_binding(client, spec)
                self._complete_vm_ha_effect(
                    binding_effect,
                    resource_updates={
                        "route-runtime-id": binding.route_runtime_id,
                        **credential_bindings_from_runtime(binding),
                    },
                )
            except Exception as e:
                self._private_alloc_ids.clear()
                self._vm_ha_shared_allocation_id = None
                self._vm_ha_route_targets = None
                raise RuntimeError(f"VM-HA runtime binding failed closed: {e}") from e
            return VMProvisioningResult(vm_ips, vm_ha_runtime_binding=binding)
        return vm_ips

    @staticmethod
    def _normalize_ip_value(ip_value: str | None) -> str | None:
        if not ip_value:
            return None
        return str(ip_value).strip().split("/")[0]

    def _allocation_ip_from_obj(self, alloc_obj: t.Any) -> str | None:
        try:
            spec_obj = getattr(alloc_obj, "spec", None)
            if spec_obj:
                ipv4_public = getattr(spec_obj, "ipv4_public", None)
                if ipv4_public:
                    address = getattr(ipv4_public, "address", None)
                    if address:
                        normalized_address = self._normalize_ip_value(str(address))
                        if normalized_address:
                            return normalized_address
                    cidr = getattr(ipv4_public, "cidr", None)
                    if cidr:
                        normalized_cidr = self._normalize_ip_value(str(cidr))
                        if normalized_cidr:
                            return normalized_cidr
            status = getattr(alloc_obj, "status", None)
            details = getattr(status, "details", None) if status else None
            cidr = getattr(details, "allocated_cidr", None) if details else None
            if cidr:
                normalized_cidr = self._normalize_ip_value(str(cidr))
                if normalized_cidr:
                    return normalized_cidr
        except Exception:
            return None
        return None

    def _list_allocations_by_ip(
        self,
        alloc_client: t.Any,
        *,
        fail_closed: bool = False,
    ) -> dict[str, t.Any]:
        try:
            from nebius.api.nebius.vpc.v1 import ListAllocationsRequest  # type: ignore

            mapping: dict[str, t.Any] = {}
            allocations = collect_nebius_pages(
                lambda page_token: alloc_client.list(
                    ListAllocationsRequest(
                        parent_id=self.project_id or "",
                        page_size=1000,
                        page_token=page_token,
                    )
                ),
                context="Public allocation",
                item_identity=nebius_resource_id,
            )
            for allocation in allocations:
                full_allocation = allocation
                ip_value = self._allocation_ip_from_obj(full_allocation)
                if not ip_value:
                    full_allocation = self._hydrate_allocation(alloc_client, allocation)
                    ip_value = self._allocation_ip_from_obj(full_allocation)
                if not ip_value:
                    continue
                existing = mapping.get(ip_value)
                if (
                    fail_closed
                    and existing is not None
                    and self._resource_id(existing) != self._resource_id(full_allocation)
                ):
                    raise RuntimeError("Multiple public allocations report the same address")
                mapping[ip_value] = full_allocation
            return mapping
        except Exception as error:
            raise RuntimeError("Public allocations could not be listed") from error

    @staticmethod
    def _allocation_state(alloc_obj: t.Any) -> str | None:
        for path in (
            ("status", "state"),
            ("status", "state_name"),
            ("status", "lifecycle_state"),
            ("status", "phase"),
            ("status", "details", "state"),
            ("status", "details", "lifecycle_state"),
        ):
            current = alloc_obj
            for key in path:
                current = getattr(current, key, None)
                if current is None:
                    break
            if not current:
                continue
            try:
                if hasattr(current, "name"):
                    current = current.name
                elif hasattr(current, "value"):
                    current = current.value
            except Exception:
                pass
            return str(current)
        return None

    @classmethod
    def _allocation_has_stable_assignment_state(cls, alloc_obj: t.Any) -> bool:
        """Accept only provider-stable states with a matching assignment shape."""

        state = cls._allocation_state(alloc_obj)
        normalized_state = state.rsplit(".", 1)[-1].upper() if state else ""
        attached = cls._allocation_is_attached(alloc_obj)
        return (normalized_state == "ALLOCATED" and not attached) or (
            normalized_state == "ASSIGNED" and attached
        )

    @staticmethod
    def _allocation_is_attached(alloc_obj: t.Any) -> bool:
        for path in (
            ("status", "details", "attachments"),
            ("status", "attachments"),
            ("status", "assignment", "network_interface", "instance_id"),
            ("status", "assignment", "load_balancer", "id"),
            ("status", "details", "attached_to"),
            ("status", "details", "instance_id"),
            ("status", "details", "resource_id"),
            ("spec", "ipv4_public", "attached_to"),
            ("spec", "ipv4_public", "instance_id"),
        ):
            current = alloc_obj
            for key in path:
                current = getattr(current, key, None)
                if current is None:
                    break
            if not current:
                continue
            if isinstance(current, list):
                return len(current) > 0
            return True
        return False

    def _wait_for_allocation_release(
        self,
        alloc_client: t.Any,
        desired_ip: str,
        *,
        fail_closed: bool = False,
    ) -> dict[str, t.Any]:
        print(
            f"[VMManager] Allocation {desired_ip} appears to be releasing. Waiting up to 10s before retry..."
        )
        allocations_by_ip: dict[str, t.Any] = {}
        for _ in range(5):
            time.sleep(2)
            allocations_by_ip = self._list_allocations_by_ip(
                alloc_client,
                fail_closed=fail_closed,
            )
            alloc_obj = allocations_by_ip.get(desired_ip)
            if alloc_obj is None:
                break
            state = self._allocation_state(alloc_obj)
            if not state or not any(
                token in state.lower() for token in ("delet", "releas", "pending")
            ):
                break
        return allocations_by_ip

    @staticmethod
    def _desired_requested_ip(
        desired_matrix: list[list[str]],
        instance_index: int,
        nic_index: int,
    ) -> str | None:
        if instance_index >= len(desired_matrix):
            return None
        row = desired_matrix[instance_index]
        if not isinstance(row, list) or nic_index >= len(row):
            return None
        return row[nic_index] if row[nic_index] else None

    def _resolve_prepared_public_allocation(
        self,
        alloc_client: t.Any,
        subnet_id: str,
        alloc_name: str,
        desired_ip: str | None,
        allocations_by_ip: dict[str, t.Any],
        *,
        strict: bool = False,
        expected_attachment: tuple[str, str] | None = None,
    ) -> tuple[t.Any | None, dict[str, t.Any]]:
        alloc_obj = None

        if desired_ip:
            alloc_obj, allocations_by_ip = self._find_requested_public_allocation(
                alloc_client,
                None,
                alloc_name,
                desired_ip,
                allocations_by_ip,
                strict=strict,
                expected_attachment=expected_attachment,
            )
            if alloc_obj is not None:
                alloc_obj = self._require_public_allocation_in_gateway_subnet(
                    alloc_client,
                    alloc_obj,
                    subnet_id,
                    desired_ip,
                )
        else:
            by_name = self._get_allocation_by_name(
                alloc_client,
                alloc_name,
                strict=strict,
            )
            if by_name is not None:
                alloc_obj = by_name

        if alloc_obj is None:
            return None, allocations_by_ip

        alloc_ip = self._resolve_known_allocation_ip(alloc_client, alloc_obj)
        alloc_obj = self._require_public_allocation_in_gateway_subnet(
            alloc_client,
            alloc_obj,
            subnet_id,
            alloc_ip,
        )
        if desired_ip and alloc_ip and alloc_ip != desired_ip:
            print(
                f"[VMManager] Note: Allocation {alloc_name} has IP {alloc_ip} which differs from requested {desired_ip}."
            )

        return alloc_obj, allocations_by_ip

    def _create_prepared_public_allocation(
        self,
        alloc_client: t.Any,
        subnet_id: str,
        alloc_name: str,
        desired_ip: str | None,
        allocations_by_ip: dict[str, t.Any],
        *,
        strict: bool = False,
    ) -> t.Any:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationSpec,
            CreateAllocationRequest,
            IPv4PublicAllocationSpec,
        )

        request = CreateAllocationRequest(
            metadata=ResourceMetadata(
                name=alloc_name,
                parent_id=self.project_id or "",
            ),
            spec=AllocationSpec(
                ipv4_public=IPv4PublicAllocationSpec(
                    subnet_id=subnet_id,
                    cidr=desired_ip if desired_ip else "/32",
                )
            ),
        )
        try:
            operation = alloc_client.create(request).wait()
            if strict:
                self._sync_preparation_operation(operation, action="Public allocation creation")
            else:
                self._sync_operation(operation)
        except Exception as e:
            error_text = str(e).lower()
            reconciled = self._get_allocation_by_name(
                alloc_client,
                alloc_name,
                strict=strict,
            )
            if reconciled is not None:
                return reconciled
            if "already exists" not in error_text and "duplicate" not in error_text:
                if desired_ip and ("immutable" in error_text or "subnet" in error_text):
                    print(
                        f"[VMManager] Requested IP {desired_ip} could not be allocated in the gateway subnet: {e}"
                    )
                else:
                    raise RuntimeError(
                        f"Failed to create public IP allocation {alloc_name}: {e}"
                    ) from e

        alloc_obj = self._get_allocation_by_name(
            alloc_client,
            alloc_name,
            strict=strict,
        )
        if alloc_obj is not None:
            if desired_ip:
                alloc_obj = self._validate_requested_public_allocation(
                    alloc_client,
                    alloc_obj,
                    desired_ip=desired_ip,
                    alloc_name=alloc_name,
                    require_resolved_ip=False,
                )
            return alloc_obj
        if desired_ip and desired_ip in allocations_by_ip:
            return self._validate_requested_public_allocation(
                alloc_client,
                allocations_by_ip[desired_ip],
                desired_ip=desired_ip,
                alloc_name=alloc_name,
                require_resolved_ip=False,
            )
        raise RuntimeError(f"Failed to fetch allocation {alloc_name} after creation.")

    def _resolve_prepared_public_ip(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        alloc_name: str,
        desired_ip: str | None,
    ) -> str:
        alloc_id = self._resource_id(alloc_obj)
        if not alloc_id:
            if desired_ip:
                return desired_ip
            raise RuntimeError(f"Allocation {alloc_name} returned no id.")

        alloc_ip = self._allocation_ip_from_obj(alloc_obj)
        if alloc_ip:
            return alloc_ip

        from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

        for _ in range(3):
            alloc_ip = self.get_allocation_ip(alloc_id)
            if alloc_ip:
                return alloc_ip
            try:
                alloc_obj = alloc_client.get(GetAllocationRequest(id=alloc_id)).wait()
            except Exception:
                alloc_obj = None
            alloc_ip = self._allocation_ip_from_obj(alloc_obj) if alloc_obj else None
            if alloc_ip:
                return alloc_ip
            time.sleep(1)

        if desired_ip:
            return desired_ip
        raise RuntimeError(f"Allocation {alloc_name} returned no IP address (API may be delayed).")

    def _strict_allocation_by_id(self, allocation_client: t.Any, allocation_id: str) -> t.Any:
        from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

        try:
            allocation = allocation_client.get(GetAllocationRequest(id=allocation_id)).wait()
        except Exception as error:
            raise RuntimeError("Public allocation could not be reread by identity") from error
        if self._resource_id(allocation) != allocation_id or str(
            getattr(getattr(allocation, "metadata", None), "parent_id", "") or ""
        ) != (self.project_id or ""):
            raise RuntimeError("Public allocation changed identity during reread")
        return allocation

    def _preparation_instance_ids(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
    ) -> dict[int, str | None]:
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
        from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

        instance_client = InstanceServiceClient(client)
        identities: dict[int, str | None] = {}
        for index in range(spec.instance_count):
            name = f"{spec.name}-{index}"
            try:
                instance = instance_client.get_by_name(
                    GetByNameRequest(parent_id=self.project_id or "", name=name)
                ).wait()
            except Exception as error:
                if nebius_request_error_code_is(error, "NOT_FOUND"):
                    identities[index] = None
                    continue
                raise RuntimeError(
                    f"Gateway VM {name!r} could not be classified for allocation reuse"
                ) from error
            instance_id = self._resource_id(instance)
            metadata = getattr(instance, "metadata", None)
            if (
                not instance_id
                or str(getattr(metadata, "name", "") or "") != name
                or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
            ):
                raise RuntimeError(f"Gateway VM {name!r} returned an inexact identity")
            identities[index] = instance_id
        return identities

    def _validate_prepared_public_allocation(
        self,
        allocation_client: t.Any,
        allocation: t.Any,
        *,
        subnet_id: str,
        desired_ip: str | None,
        expected_attachment: tuple[str, str] | None,
        expected_resource_version: int | None = None,
    ) -> tuple[t.Any, str]:
        allocation_id = self._resource_id(allocation)
        if not allocation_id:
            raise RuntimeError("Public allocation has no authoritative identity")
        allocation = self._strict_allocation_by_id(allocation_client, allocation_id)
        metadata = getattr(allocation, "metadata", None)
        allocation_spec = getattr(allocation, "spec", None)
        ipv4_public = getattr(allocation_spec, "ipv4_public", None)
        resource_version = int(getattr(metadata, "resource_version", 0) or 0)
        if expected_resource_version is not None and resource_version != expected_resource_version:
            raise RuntimeError("Selected public allocation changed after it was displayed")
        if str(getattr(metadata, "parent_id", "") or "") != (self.project_id or ""):
            raise RuntimeError("Public allocation belongs to a different project")
        if ipv4_public is None or getattr(allocation_spec, "ipv4_private", None) is not None:
            raise RuntimeError("Selected allocation is not exclusively public IPv4")
        if str(getattr(ipv4_public, "subnet_id", "") or "") != subnet_id:
            raise RuntimeError("Public allocation belongs to a different subnet")
        if not self._allocation_has_stable_assignment_state(allocation):
            raise RuntimeError("Public allocation is not stably allocated")
        address = self._resolve_known_allocation_ip(allocation_client, allocation)
        if not address:
            raise RuntimeError("Public allocation address could not be resolved")
        if desired_ip and address != self._normalize_ip_value(desired_ip):
            raise RuntimeError("Public allocation address changed after selection")
        self._validate_requested_public_allocation(
            allocation_client,
            allocation,
            desired_ip=address,
            alloc_name=self._allocation_name(allocation),
            require_resolved_ip=True,
            expected_attachment=expected_attachment,
        )
        return allocation, address

    def list_eligible_public_allocations(
        self,
        spec: GatewayGroupSpec,
        *,
        subnet_id: str,
    ) -> list[PublicAllocationCandidate]:
        """Return stable exact-subnet allocations suitable for interactive reuse."""

        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client not available; cannot list public allocations.")
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationServiceClient,
            ListAllocationsRequest,
        )

        allocation_client = AllocationServiceClient(client)
        instance_ids = self._preparation_instance_ids(client, spec)
        index_by_instance_id = {
            instance_id: index
            for index, instance_id in instance_ids.items()
            if instance_id is not None
        }
        candidates: list[PublicAllocationCandidate] = []
        listed_allocations = collect_nebius_pages(
            lambda page_token: allocation_client.list(
                ListAllocationsRequest(
                    parent_id=self.project_id or "",
                    page_size=1000,
                    page_token=page_token,
                )
            ),
            context="Public allocation",
            item_identity=nebius_resource_id,
        )
        for listed in listed_allocations:
            allocation_id = self._resource_id(listed)
            if not allocation_id:
                raise RuntimeError("Public allocation inventory contains an unidentified resource")
            allocation = self._strict_allocation_by_id(allocation_client, allocation_id)
            metadata = getattr(allocation, "metadata", None)
            allocation_spec = getattr(allocation, "spec", None)
            ipv4_public = getattr(allocation_spec, "ipv4_public", None)
            if (
                str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
                or ipv4_public is None
                or getattr(allocation_spec, "ipv4_private", None) is not None
                or str(getattr(ipv4_public, "subnet_id", "") or "") != subnet_id
            ):
                continue
            if not self._allocation_has_stable_assignment_state(allocation):
                continue
            resource_version = int(getattr(metadata, "resource_version", 0) or 0)
            if resource_version < 1:
                continue
            address = self._resolve_known_allocation_ip(allocation_client, allocation)
            if not address:
                continue
            assigned_instance_index: int | None = None
            assigned_nic_index: int | None = None
            if self._allocation_is_attached(allocation):
                assignment = getattr(
                    getattr(getattr(allocation, "status", None), "assignment", None),
                    "network_interface",
                    None,
                )
                assigned_instance_id = str(getattr(assignment, "instance_id", "") or "")
                assigned_nic_name = str(getattr(assignment, "name", "") or "")
                assigned_instance_index = index_by_instance_id.get(assigned_instance_id)
                match = re.fullmatch(r"eth([0-9]+)", assigned_nic_name)
                if assigned_instance_index is None or match is None:
                    continue
                assigned_nic_index = int(match.group(1))
                if assigned_nic_index != 0:
                    continue
            candidates.append(
                PublicAllocationCandidate(
                    allocation_id=allocation_id,
                    name=self._allocation_name(allocation),
                    address=address,
                    resource_version=resource_version,
                    assigned_instance_index=assigned_instance_index,
                    assigned_nic_index=assigned_nic_index,
                )
            )
        return sorted(candidates, key=lambda item: (item.address, item.allocation_id))

    def verify_selected_public_allocations(
        self,
        spec: GatewayGroupSpec,
        *,
        subnet_id: str,
        selections: t.Mapping[tuple[int, int], PublicAllocationCandidate],
    ) -> None:
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client not available; cannot verify allocations.")
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore

        allocation_client = AllocationServiceClient(client)
        instance_ids = self._preparation_instance_ids(client, spec)
        selected_ids: set[str] = set()
        for (instance_index, nic_index), candidate in selections.items():
            if candidate.resource_version < 1:
                raise RuntimeError("Selected public allocation has no authoritative version")
            if candidate.allocation_id in selected_ids:
                raise RuntimeError("The same public allocation was selected more than once")
            selected_ids.add(candidate.allocation_id)
            expected_instance_id = instance_ids.get(instance_index)
            expected_attachment = (
                (expected_instance_id, f"eth{nic_index}")
                if expected_instance_id is not None
                else None
            )
            allocation = self._strict_allocation_by_id(
                allocation_client,
                candidate.allocation_id,
            )
            self._validate_prepared_public_allocation(
                allocation_client,
                allocation,
                subnet_id=subnet_id,
                desired_ip=candidate.address,
                expected_attachment=expected_attachment,
                expected_resource_version=candidate.resource_version,
            )

    def _prepare_public_allocations_for_subnet(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        subnet_id: str,
        *,
        instance_indices: t.Collection[int],
        desired_external_ips: list[list[str]] | None,
        require_unattached: bool,
        strict: bool = False,
    ) -> dict[int, list[str]]:
        selected_indices = tuple(sorted(set(instance_indices)))
        if not selected_indices:
            return {}
        invalid_indices = [
            index for index in selected_indices if index < 0 or index >= spec.instance_count
        ]
        if invalid_indices:
            raise ValueError(
                "Public allocation instance indices are outside the gateway group: "
                + ", ".join(str(index) for index in invalid_indices)
            )

        # CURRENT PLATFORM LIMITATION: num_nics=1 (enforced elsewhere on apply).
        num_nics = int(spec.vm_spec.get("num_nics", 1))
        if num_nics > 1:
            print(
                f"[VMManager] WARNING: num_nics={num_nics} but current platform only supports 1 NIC. Using num_nics=1."
            )
            num_nics = 1

        from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore

        alloc_client = AllocationServiceClient(client)
        desired_matrix = desired_external_ips or []
        desired_any = any(
            self._desired_requested_ip(desired_matrix, index, nic_index)
            for index in selected_indices
            for nic_index in range(num_nics)
        )
        allocations_by_ip = (
            self._list_allocations_by_ip(alloc_client, fail_closed=strict) if desired_any else {}
        )
        preparation_instance_ids = self._preparation_instance_ids(client, spec) if strict else {}
        allocated_by_instance: dict[int, list[str]] = {}

        for inst_index in selected_indices:
            inst_name = f"{spec.name}-{inst_index}"
            inst_ips: list[str] = []
            for nic_index in range(num_nics):
                nic_name = f"eth{nic_index}"
                alloc_name = f"{inst_name}-{nic_name}-ip"
                expected_instance_id = preparation_instance_ids.get(inst_index)
                expected_attachment = (
                    (expected_instance_id, nic_name) if expected_instance_id is not None else None
                )
                desired_ip = self._normalize_ip_value(
                    self._desired_requested_ip(desired_matrix, inst_index, nic_index)
                )
                if strict:
                    alloc_obj, allocations_by_ip = self._resolve_prepared_public_allocation(
                        alloc_client,
                        subnet_id,
                        alloc_name,
                        desired_ip,
                        allocations_by_ip,
                        strict=True,
                        expected_attachment=expected_attachment,
                    )
                else:
                    alloc_obj, allocations_by_ip = self._resolve_prepared_public_allocation(
                        alloc_client,
                        subnet_id,
                        alloc_name,
                        desired_ip,
                        allocations_by_ip,
                    )
                if alloc_obj is None:
                    if strict:
                        alloc_obj = self._create_prepared_public_allocation(
                            alloc_client,
                            subnet_id,
                            alloc_name,
                            desired_ip,
                            allocations_by_ip,
                            strict=True,
                        )
                    else:
                        alloc_obj = self._create_prepared_public_allocation(
                            alloc_client,
                            subnet_id,
                            alloc_name,
                            desired_ip,
                            allocations_by_ip,
                        )

                resolved_ip = self._resolve_prepared_public_ip(
                    alloc_client,
                    alloc_obj,
                    alloc_name,
                    desired_ip,
                )
                if strict:
                    alloc_obj, resolved_ip = self._validate_prepared_public_allocation(
                        alloc_client,
                        alloc_obj,
                        subnet_id=subnet_id,
                        desired_ip=desired_ip or resolved_ip,
                        expected_attachment=expected_attachment,
                    )
                if require_unattached:
                    alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
                    metadata = getattr(alloc_obj, "metadata", None)
                    allocation_spec = getattr(alloc_obj, "spec", None)
                    ipv4_public = (
                        getattr(allocation_spec, "ipv4_public", None)
                        if allocation_spec is not None
                        else None
                    )
                    state = self._allocation_state(alloc_obj)
                    if (
                        self._allocation_name(alloc_obj) != alloc_name
                        or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
                        or ipv4_public is None
                        or getattr(allocation_spec, "ipv4_private", None) is not None
                        or str(getattr(ipv4_public, "subnet_id", "") or "") != subnet_id
                    ):
                        raise RuntimeError(
                            f"Passive public allocation {alloc_name} has a foreign resource shape"
                        )
                    if not state or state.rsplit(".", 1)[-1].upper() != "ALLOCATED":
                        raise RuntimeError(
                            f"Passive public allocation {alloc_name} is not stably allocated"
                        )
                    self._validate_requested_public_allocation(
                        alloc_client,
                        alloc_obj,
                        desired_ip=resolved_ip,
                        alloc_name=alloc_name,
                        require_resolved_ip=True,
                    )
                inst_ips.append(resolved_ip)
            allocated_by_instance[inst_index] = inst_ips

        return allocated_by_instance

    def prepare_public_allocations(
        self,
        spec: GatewayGroupSpec,
        *,
        instance_indices: t.Collection[int],
        desired_external_ips: list[list[str]] | None = None,
        require_unattached: bool = False,
    ) -> dict[int, list[str]]:
        """Ensure networking and reserve public allocations only for selected instances."""

        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client not available; cannot prepare network.")
        subnet_id = self._ensure_vpngw_subnet(client, spec)
        if not subnet_id:
            raise RuntimeError("Failed to resolve or create the dedicated gateway subnet.")
        self._ensure_vpngw_route_table(client, subnet_id)
        return self._prepare_public_allocations_for_subnet(
            client,
            spec,
            subnet_id,
            instance_indices=instance_indices,
            desired_external_ips=desired_external_ips,
            require_unattached=require_unattached,
        )

    def prepare_network_foundation(self, spec: GatewayGroupSpec) -> str:
        """Converge and verify the subnet and its required default-egress route."""

        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client not available; cannot prepare network.")
        subnet_id = self._ensure_vpngw_subnet(client, spec, strict=True)
        if not subnet_id:
            raise RuntimeError("Failed to resolve or create the dedicated gateway subnet.")
        self._ensure_vpngw_route_table(client, subnet_id)
        return subnet_id

    def prepare_public_allocations_in_subnet(
        self,
        spec: GatewayGroupSpec,
        *,
        subnet_id: str,
        desired_external_ips: list[list[str]] | None,
    ) -> list[list[str]]:
        """Converge and verify the complete public-allocation matrix in a prepared subnet."""

        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client not available; cannot prepare allocations.")
        allocated_by_instance = self._prepare_public_allocations_for_subnet(
            client,
            spec,
            subnet_id,
            instance_indices=range(spec.instance_count),
            desired_external_ips=desired_external_ips,
            require_unattached=False,
            strict=True,
        )
        return [allocated_by_instance[index] for index in range(spec.instance_count)]

    def prepare_network(
        self,
        spec: GatewayGroupSpec,
        *,
        allocate_ips: bool = True,
        desired_external_ips: list[list[str]] | None = None,
    ) -> list[list[str]]:
        """Ensure the dedicated gateway subnet exists and optionally reserve public IP allocations.

        Returns a list of public IPs per instance (list-of-lists, NIC order).
        """
        subnet_id = self.prepare_network_foundation(spec)

        if not allocate_ips:
            return []

        return self.prepare_public_allocations_in_subnet(
            spec,
            subnet_id=subnet_id,
            desired_external_ips=desired_external_ips,
        )

    def _resolve_gateway_network(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        *,
        report_selection: bool = False,
        strict: bool = False,
    ) -> tuple[t.Any, str, str, t.Any]:
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            GetNetworkByNameRequest,
            GetNetworkRequest,
            ListNetworksRequest,
            NetworkServiceClient,
            SubnetServiceClient,
        )

        net_client = NetworkServiceClient(client)  # type: ignore
        network_obj = None
        if spec.network_id:
            try:
                network_obj = net_client.get(GetNetworkRequest(id=spec.network_id)).wait()
                if report_selection:
                    self._report_gateway_network_once(
                        f"[VMManager] Using network from YAML: {spec.network_id}"
                    )
            except Exception as e:
                raise RuntimeError(
                    f"[VMManager] Specified network_id '{spec.network_id}' could not be read."
                ) from e
        else:
            if report_selection:
                self._report_gateway_network_once(
                    "[VMManager] No gateway_group.network_id in YAML, auto-discovering network..."
                )
            try:
                network_obj = net_client.get_by_name(
                    GetNetworkByNameRequest(
                        parent_id=self.project_id or "",
                        name="default-network",
                    )
                ).wait()
                if (
                    str(getattr(getattr(network_obj, "metadata", None), "name", "") or "")
                    != "default-network"
                ):
                    raise RuntimeError("default-network lookup returned an inexact identity")
                if report_selection:
                    self._report_gateway_network_once("[VMManager] Found default-network, using it")
            except Exception as error:
                if not nebius_request_error_code_is(error, "NOT_FOUND"):
                    raise RuntimeError(
                        "default-network could not be classified during VPC auto-discovery"
                    ) from error
                network_obj = None

            if network_obj is None:
                try:
                    items = collect_nebius_pages(
                        lambda page_token: net_client.list(
                            ListNetworksRequest(
                                parent_id=self.project_id or "",
                                page_size=1000,
                                page_token=page_token,
                            )
                        ),
                        context="Network",
                        item_identity=nebius_resource_id,
                    )
                    if not items:
                        raise RuntimeError(
                            "[VMManager] No networks found in project. "
                            "Please create a network or specify gateway_group.network_id in YAML."
                        )
                    if len(items) == 1:
                        network_obj = t.cast(t.Any, items[0])
                        net_name = getattr(
                            getattr(network_obj, "metadata", None),
                            "name",
                            "unknown",
                        )
                        if report_selection:
                            self._report_gateway_network_once(
                                f"[VMManager] Found single custom network: {net_name}, using it"
                            )
                    else:
                        net_names = [
                            getattr(getattr(network, "metadata", None), "name", "unknown")
                            for network in items
                        ]
                        raise RuntimeError(
                            f"[VMManager] Multiple networks found in project: {', '.join(net_names)}. "
                            "Please specify which network to use by setting gateway_group.network_id in your YAML config."
                        )
                except RuntimeError:
                    raise
                except Exception as error:
                    raise RuntimeError("[VMManager] Failed to list networks") from error

        if network_obj is None:
            raise RuntimeError(
                "[VMManager] Could not resolve network. "
                "Please specify gateway_group.network_id in your YAML config."
            )

        network_id = self._resource_id(network_obj)
        if not network_id:
            raise RuntimeError("[VMManager] Resolved network is missing an id.")
        if spec.network_id and network_id != spec.network_id:
            raise RuntimeError("[VMManager] Specified network lookup returned an inexact identity.")
        if str(getattr(getattr(network_obj, "metadata", None), "parent_id", "") or "") != (
            self.project_id or ""
        ):
            raise RuntimeError("[VMManager] Resolved network belongs to a different project.")

        network_name = (
            getattr(getattr(network_obj, "metadata", None), "name", None) or "default-network"
        )
        subnet_client = SubnetServiceClient(client)  # type: ignore
        return network_obj, network_id, network_name, subnet_client

    def _find_gateway_subnet(
        self,
        subnet_client: t.Any,
        network_id: str,
        subnet_name: str,
        *,
        strict: bool = False,
    ) -> t.Any | None:
        from nebius.api.nebius.vpc.v1 import GetSubnetByNameRequest  # type: ignore

        try:
            candidate = subnet_client.get_by_name(
                GetSubnetByNameRequest(parent_id=self.project_id or "", name=subnet_name)
            ).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(
                f"Failed to classify gateway subnet {subnet_name!r} by name."
            ) from error
        del strict
        metadata = getattr(candidate, "metadata", None)
        candidate_network_id = str(
            getattr(getattr(candidate, "spec", None), "network_id", "") or ""
        )
        if (
            not self._resource_id(candidate)
            or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
            or str(getattr(metadata, "name", "") or "") != subnet_name
            or candidate_network_id != network_id
        ):
            raise RuntimeError(f"Gateway subnet {subnet_name!r} returned an inexact identity.")
        return candidate

    def _validate_existing_gateway_subnet(
        self,
        subnet_obj: t.Any,
        subnet_name: str,
        desired_network: ipaddress.IPv4Network | None,
        desired_prefix_length: int,
    ) -> ipaddress.IPv4Network:
        subnet_networks = self._extract_explicit_subnet_networks(subnet_obj)
        subnet_spec = getattr(subnet_obj, "spec", None)
        subnet_uses_network_pools = bool(
            getattr(getattr(subnet_spec, "ipv4_private_pools", None), "use_network_pools", False)
        )
        if subnet_uses_network_pools:
            raise ValueError(
                f"{subnet_name} exists but uses parent network pools (use_network_pools=true). "
                "Dedicated gateway subnets must use explicit private pools. "
                "Please delete the subnet manually and rerun the command."
            )
        if len(subnet_networks) != 1:
            raise ValueError(
                f"{subnet_name} must have exactly one explicit private CIDR. "
                f"Found {len(subnet_networks)} explicit CIDR(s): "
                + ", ".join(str(network) for network in subnet_networks)
            )

        existing_network = subnet_networks[0]
        if desired_network and existing_network != desired_network:
            raise ValueError(
                f"{subnet_name} exists with CIDR {existing_network}, but the config requires "
                f"{desired_network}. Delete the subnet manually and rerun the command."
            )
        # prefix_length controls creation only. When CIDR is omitted, an existing
        # exact-name subnet owns its already established explicit CIDR.
        del desired_prefix_length
        return existing_network

    def _list_existing_subnet_networks(
        self,
        subnet_client: t.Any,
        network_id: str,
    ) -> list[ipaddress.IPv4Network]:
        from nebius.api.nebius.vpc.v1 import ListSubnetsByNetworkRequest  # type: ignore

        existing_networks: list[ipaddress.IPv4Network] = []
        subnets = collect_nebius_pages(
            lambda page_token: subnet_client.list_by_network(
                ListSubnetsByNetworkRequest(
                    network_id=network_id,
                    page_size=1000,
                    page_token=page_token,
                )
            ),
            context="Subnet",
            item_identity=nebius_resource_id,
        )
        for subnet in subnets:
            existing_networks.extend(self._extract_explicit_subnet_networks(subnet))
        return existing_networks

    def _select_gateway_subnet_cidr(
        self,
        client: t.Any,
        network_obj: t.Any,
        subnet_client: t.Any,
        network_id: str,
        desired_network: ipaddress.IPv4Network | None,
        desired_prefix_length: int,
        network_name: str,
        *,
        strict: bool = False,
    ) -> str:
        existing_subnet_networks = self._list_existing_subnet_networks(subnet_client, network_id)
        if desired_network is not None:
            if any(desired_network.overlaps(existing) for existing in existing_subnet_networks):
                raise RuntimeError(
                    f"Requested gateway subnet CIDR {desired_network} overlaps with an existing explicit subnet in the target network."
                )
            self._ensure_network_pool_contains_cidr(
                client,
                network_obj,
                desired_network,
                strict=strict,
            )
            return str(desired_network)

        network_pool_cidrs: list[ipaddress.IPv4Network] = []
        for _, pool_obj in self._get_network_private_pools(client, network_obj):
            for pool_cidr_obj in getattr(getattr(pool_obj, "spec", None), "cidrs", []) or []:
                pool_cidr = getattr(pool_cidr_obj, "cidr", None)
                if not pool_cidr:
                    continue
                pool_network = _parse_ipv4_network(str(pool_cidr))
                if pool_network is not None:
                    network_pool_cidrs.append(pool_network)

        cidr_to_use = self._find_first_free_subnet_cidr(
            network_pool_cidrs,
            existing_subnet_networks,
            prefix_length=desired_prefix_length,
        )
        if cidr_to_use:
            return cidr_to_use

        raise RuntimeError(
            f"Failed to calculate a free /{desired_prefix_length} gateway subnet in network '{network_name}'. "
            "Extend the network pool or specify gateway_group.subnet.cidr explicitly."
        )

    def _create_gateway_subnet(
        self,
        client: t.Any,
        subnet_client: t.Any,
        subnet_name: str,
        network_id: str,
        cidr_to_use: str,
    ) -> t.Any | None:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            CreateSubnetRequest,
            IPv4PrivateSubnetPools,
            SubnetCidr,
            SubnetPool,
            SubnetSpec,
        )

        print(f"[VMManager] Creating gateway subnet '{subnet_name}' with CIDR {cidr_to_use}")

        ipv4_private_pools = IPv4PrivateSubnetPools()
        ipv4_private_pools.pools.extend([SubnetPool(cidrs=[SubnetCidr(cidr=cidr_to_use)])])
        ipv4_private_pools.use_network_pools = False

        request = CreateSubnetRequest(
            metadata=ResourceMetadata(
                name=subnet_name,
                parent_id=self.project_id or "",
            ),
            spec=SubnetSpec(
                network_id=network_id,
                ipv4_private_pools=ipv4_private_pools,
            ),
        )
        operation = subnet_client.create(request)
        self._sync_operation(operation)

        time.sleep(5)
        subnet = self._find_gateway_subnet(
            subnet_client,
            network_id,
            subnet_name,
            strict=True,
        )
        if subnet is not None:
            print(f"[VMManager] ✓ Subnet '{subnet_name}' created successfully")
            subnet_spec = getattr(subnet, "spec", None)
            private_pools = (
                getattr(subnet_spec, "ipv4_private_pools", None) if subnet_spec else None
            )
            if private_pools and getattr(private_pools, "use_network_pools", True):
                raise RuntimeError(
                    f"[VMManager] CRITICAL: Subnet '{subnet_name}' was created but use_network_pools=true! "
                    "This means the subnet will inherit the network's private pool instead of using the specified CIDR. "
                    "This is a bug in the Nebius API - it ignored our explicit use_network_pools=False setting. "
                    "The subnet has been created incorrectly and must be deleted manually."
                )

            return subnet

        return None

    def _ensure_vpngw_subnet(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        *,
        strict: bool = False,
    ) -> str | None:
        """Ensure the configured dedicated gateway subnet exists in the chosen network."""
        if client is None:
            return None

        subnet_settings = self._gateway_subnet_settings(spec)
        subnet_name = str(subnet_settings["name"])
        desired_cidr = subnet_settings["cidr"]
        desired_prefix_length = int(subnet_settings["prefix_length"])

        try:
            desired_network = _parse_ipv4_network(desired_cidr) if desired_cidr else None
            if desired_cidr and desired_network is None:
                raise ValueError(
                    f"gateway_group.subnet.cidr must be a valid IPv4 CIDR, got {desired_cidr!r}."
                )
            if strict:
                network_obj, network_id, network_name, subnet_client = (
                    self._resolve_gateway_network(
                        client,
                        spec,
                        report_selection=True,
                        strict=True,
                    )
                )
            else:
                network_obj, network_id, network_name, subnet_client = (
                    self._resolve_gateway_network(
                        client,
                        spec,
                        report_selection=True,
                    )
                )
            if strict:
                subnet_obj = self._find_gateway_subnet(
                    subnet_client,
                    network_id,
                    subnet_name,
                    strict=True,
                )
            else:
                subnet_obj = self._find_gateway_subnet(
                    subnet_client,
                    network_id,
                    subnet_name,
                )
            if subnet_obj is not None:
                existing_network = self._validate_existing_gateway_subnet(
                    subnet_obj,
                    subnet_name,
                    desired_network,
                    desired_prefix_length,
                )
                print(
                    f"[VMManager] Found existing gateway subnet '{subnet_name}' ({existing_network})"
                )

            if subnet_obj is None:
                try:
                    print(f"[VMManager] Creating gateway subnet '{subnet_name}' ...")
                    cidr_to_use = self._select_gateway_subnet_cidr(
                        client,
                        network_obj,
                        subnet_client,
                        network_id,
                        desired_network,
                        desired_prefix_length,
                        network_name,
                        strict=strict,
                    )
                    subnet_obj = self._create_gateway_subnet(
                        client,
                        subnet_client,
                        subnet_name,
                        network_id,
                        cidr_to_use,
                    )
                    if strict:
                        subnet_obj = self._find_gateway_subnet(
                            subnet_client,
                            network_id,
                            subnet_name,
                            strict=True,
                        )
                        created_network = _parse_ipv4_network(cidr_to_use)
                        if created_network is None:
                            raise RuntimeError(
                                "Created gateway subnet CIDR could not be normalized"
                            )
                        if subnet_obj is None:
                            raise RuntimeError(
                                "Created gateway subnet could not be reread by exact name"
                            )
                        self._validate_existing_gateway_subnet(
                            subnet_obj,
                            subnet_name,
                            created_network,
                            desired_prefix_length,
                        )
                except Exception as e:
                    raise RuntimeError(
                        f"[VMManager] Failed to create '{subnet_name}' in {network_name}: {e}. "
                        "Please provide gateway_group.network_id with sufficient IP space or pre-create the subnet."
                    ) from e

            if strict and subnet_obj is not None:
                subnet_metadata = getattr(subnet_obj, "metadata", None)
                subnet_network_id = str(
                    getattr(getattr(subnet_obj, "spec", None), "network_id", "") or ""
                )
                if str(getattr(subnet_metadata, "parent_id", "") or "") != (self.project_id or ""):
                    raise RuntimeError("Gateway subnet belongs to a different project.")
                if subnet_network_id != network_id:
                    raise RuntimeError("Gateway subnet belongs to a different VPC network.")
            subnet_id = self._resource_id(subnet_obj)
            if strict and not subnet_id:
                raise RuntimeError(f"Gateway subnet {subnet_name!r} has no authoritative identity.")
            return subnet_id
        except Exception as e:
            if strict:
                raise
            print(f"[VMManager] Error in _ensure_vpngw_subnet: {e}")
            return None

    @staticmethod
    def _sync_preparation_operation(operation: t.Any, *, action: str) -> None:
        """Wait for one prep mutation without turning ambiguity into success."""

        try:
            if hasattr(operation, "sync_wait"):
                operation.sync_wait()
            elif hasattr(operation, "wait"):
                operation.wait()
            successful = getattr(operation, "successful", None)
            if callable(successful) and not successful():
                raise RuntimeError(f"{action} operation reported failure")
        except Exception as error:
            raise RuntimeError(f"{action} did not complete successfully") from error

    def _strict_route_table_by_name(self, route_table_client: t.Any, name: str) -> t.Any | None:
        from nebius.api.nebius.vpc.v1 import GetRouteTableByNameRequest  # type: ignore

        try:
            route_table = route_table_client.get_by_name(
                GetRouteTableByNameRequest(parent_id=self.project_id or "", name=name)
            ).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise RuntimeError(f"Route table {name!r} could not be classified") from error
        metadata = getattr(route_table, "metadata", None)
        if (
            not self._resource_id(route_table)
            or str(getattr(metadata, "name", "") or "") != name
            or str(getattr(metadata, "parent_id", "") or "") != (self.project_id or "")
        ):
            raise RuntimeError(f"Route table {name!r} returned an inexact identity")
        return route_table

    def _strict_route_table_by_id(self, route_table_client: t.Any, route_table_id: str) -> t.Any:
        from nebius.api.nebius.vpc.v1 import GetRouteTableRequest  # type: ignore

        try:
            route_table = route_table_client.get(GetRouteTableRequest(id=route_table_id)).wait()
        except Exception as error:
            raise RuntimeError("Attached route table could not be read") from error
        if self._resource_id(route_table) != route_table_id or str(
            getattr(getattr(route_table, "metadata", None), "parent_id", "") or ""
        ) != (self.project_id or ""):
            raise RuntimeError("Attached route table changed identity during verification")
        return route_table

    @staticmethod
    def _route_destination_cidr(route: t.Any) -> str:
        return str(
            getattr(getattr(getattr(route, "spec", None), "destination", None), "cidr", "") or ""
        )

    @staticmethod
    def _route_uses_default_egress(route: t.Any) -> bool:
        next_hop = getattr(getattr(route, "spec", None), "next_hop", None)
        return bool(getattr(next_hop, "default_egress_gateway", False))

    def _list_route_table_routes(self, route_client: t.Any, route_table_id: str) -> list[t.Any]:
        from nebius.api.nebius.vpc.v1 import ListRoutesRequest  # type: ignore

        return list(
            collect_nebius_pages(
                lambda page_token: route_client.list(
                    ListRoutesRequest(
                        parent_id=route_table_id,
                        page_size=1000,
                        page_token=page_token,
                    )
                ),
                context="Route table route",
                item_identity=nebius_resource_id,
            )
        )

    def _route_table_attached_subnets(
        self,
        subnet_client: t.Any,
        *,
        network_id: str,
        route_table_id: str,
    ) -> set[str]:
        from nebius.api.nebius.vpc.v1 import ListSubnetsByNetworkRequest  # type: ignore

        subnets = collect_nebius_pages(
            lambda page_token: subnet_client.list_by_network(
                ListSubnetsByNetworkRequest(
                    network_id=network_id,
                    page_size=1000,
                    page_token=page_token,
                )
            ),
            context="Route table subnet assignment",
            item_identity=nebius_resource_id,
        )
        attached: set[str] = set()
        for subnet in subnets:
            if (
                str(getattr(getattr(subnet, "spec", None), "route_table_id", "") or "")
                == route_table_id
            ):
                subnet_identity = self._resource_id(subnet)
                if not subnet_identity:
                    raise RuntimeError("An attached subnet has no authoritative identity")
                attached.add(subnet_identity)
        return attached

    def _validate_preparation_route_table(
        self,
        route_table: t.Any,
        *,
        network_id: str,
    ) -> str:
        route_table_id = self._resource_id(route_table)
        metadata = getattr(route_table, "metadata", None)
        table_network_id = str(getattr(getattr(route_table, "spec", None), "network_id", "") or "")
        if not route_table_id:
            raise RuntimeError("Route table has no authoritative identity")
        if str(getattr(metadata, "parent_id", "") or "") != (self.project_id or ""):
            raise RuntimeError("Route table belongs to a different project")
        if table_network_id != network_id:
            raise RuntimeError("Route table belongs to a different VPC network")
        return route_table_id

    def _ensure_default_egress_route(
        self,
        route_client: t.Any,
        subnet_client: t.Any,
        route_table: t.Any,
        *,
        network_id: str,
        subnet_id: str,
        require_exclusive_before_create: bool,
    ) -> None:
        if require_exclusive_before_create:
            attached = self._route_table_attached_subnets(
                subnet_client,
                network_id=network_id,
                route_table_id=self._resource_id(route_table) or "",
            )
            if attached != {subnet_id}:
                raise RuntimeError(
                    "The attached route table is not exclusively assigned to the gateway subnet"
                )

        routes = self._list_route_table_routes(route_client, self._resource_id(route_table) or "")
        default_routes = [
            route for route in routes if self._route_destination_cidr(route) == "0.0.0.0/0"
        ]
        if len(default_routes) > 1:
            raise RuntimeError("Route table has multiple default routes; refusing to choose one")
        if default_routes:
            if not self._route_uses_default_egress(default_routes[0]):
                raise RuntimeError("Route table has a conflicting 0.0.0.0/0 next hop")
            return

        for route in routes:
            if str(getattr(getattr(route, "metadata", None), "name", "") or "") == "default-egress":
                raise RuntimeError(
                    "Route name default-egress already exists with different semantics"
                )

        route_table_id = self._resource_id(route_table) or ""

        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import CreateRouteRequest, route_pb2  # type: ignore

        request = CreateRouteRequest(
            metadata=ResourceMetadata(name="default-egress", parent_id=route_table_id),
            spec=route_pb2.RouteSpec(
                destination=route_pb2.DestinationMatch(cidr="0.0.0.0/0"),
                next_hop=route_pb2.NextHop(default_egress_gateway=True),
            ),
        )
        try:
            operation = route_client.create(request).wait()
            self._sync_preparation_operation(operation, action="Default egress route creation")
        except Exception as error:
            # The result of an accepted create may be lost. Re-list once and accept only
            # the exact postcondition, which also prevents duplicate retry creation.
            routes = self._list_route_table_routes(route_client, route_table_id)
            exact = [
                route
                for route in routes
                if self._route_destination_cidr(route) == "0.0.0.0/0"
                and self._route_uses_default_egress(route)
            ]
            if len(exact) != 1:
                raise RuntimeError("Default egress route creation could not be verified") from error

        verified = self._list_route_table_routes(route_client, route_table_id)
        exact = [
            route
            for route in verified
            if self._route_destination_cidr(route) == "0.0.0.0/0"
            and self._route_uses_default_egress(route)
        ]
        conflicting = [
            route
            for route in verified
            if self._route_destination_cidr(route) == "0.0.0.0/0"
            and not self._route_uses_default_egress(route)
        ]
        if len(exact) != 1 or conflicting:
            raise RuntimeError("Default egress route postcondition is not uniquely satisfied")

    def _reconcile_vpngw_route_table(self, client: t.Any, subnet_id: str) -> None:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            CreateRouteTableRequest,
            GetSubnetRequest,
            RouteServiceClient,
            RouteTableServiceClient,
            RouteTableSpec,
            SubnetServiceClient,
            SubnetSpec,
            UpdateSubnetRequest,
        )

        subnet_client = SubnetServiceClient(client)
        route_table_client = RouteTableServiceClient(client)
        route_client = RouteServiceClient(client)
        try:
            subnet = subnet_client.get(GetSubnetRequest(id=subnet_id)).wait()
        except Exception as error:
            raise RuntimeError(
                "Gateway subnet could not be read before route preparation"
            ) from error
        if self._resource_id(subnet) != subnet_id:
            raise RuntimeError("Gateway subnet changed identity during route preparation")
        subnet_spec = getattr(subnet, "spec", None)
        subnet_metadata = getattr(subnet, "metadata", None)
        network_id = str(getattr(subnet_spec, "network_id", "") or "")
        if not network_id:
            raise RuntimeError("Gateway subnet has no VPC network identity")
        attached_route_table_id = str(getattr(subnet_spec, "route_table_id", "") or "")

        if attached_route_table_id:
            route_table = self._strict_route_table_by_id(
                route_table_client,
                attached_route_table_id,
            )
            self._validate_preparation_route_table(route_table, network_id=network_id)
            self._ensure_default_egress_route(
                route_client,
                subnet_client,
                route_table,
                network_id=network_id,
                subnet_id=subnet_id,
                require_exclusive_before_create=True,
            )
            return

        subnet_name = str(getattr(subnet_metadata, "name", "") or "vpngw-subnet")
        route_table_name = self._gateway_route_table_name(subnet_name)
        route_table = self._strict_route_table_by_name(route_table_client, route_table_name)
        if route_table is None:
            request = CreateRouteTableRequest(
                metadata=ResourceMetadata(
                    name=route_table_name,
                    parent_id=self.project_id or "",
                ),
                spec=RouteTableSpec(network_id=network_id),
            )
            try:
                operation = route_table_client.create(request).wait()
                self._sync_preparation_operation(operation, action="Route table creation")
            except Exception as error:
                route_table = self._strict_route_table_by_name(
                    route_table_client,
                    route_table_name,
                )
                if route_table is None:
                    raise RuntimeError("Route table creation could not be verified") from error
            if route_table is None:
                route_table = self._strict_route_table_by_name(
                    route_table_client,
                    route_table_name,
                )
        if route_table is None:
            raise RuntimeError("Canonical route table could not be resolved")
        route_table_id = self._validate_preparation_route_table(
            route_table,
            network_id=network_id,
        )
        attached = self._route_table_attached_subnets(
            subnet_client,
            network_id=network_id,
            route_table_id=route_table_id,
        )
        if attached - {subnet_id}:
            raise RuntimeError("Canonical route table is already assigned to another subnet")
        self._ensure_default_egress_route(
            route_client,
            subnet_client,
            route_table,
            network_id=network_id,
            subnet_id=subnet_id,
            require_exclusive_before_create=False,
        )

        update = UpdateSubnetRequest(
            metadata=ResourceMetadata(
                id=subnet_id,
                parent_id=str(getattr(subnet_metadata, "parent_id", "") or ""),
                name=subnet_name,
                resource_version=int(getattr(subnet_metadata, "resource_version", 0) or 0),
            ),
            spec=SubnetSpec(
                network_id=network_id,
                route_table_id=route_table_id,
                ipv4_private_pools=getattr(subnet_spec, "ipv4_private_pools", None),
                ipv4_public_pools=getattr(subnet_spec, "ipv4_public_pools", None),
            ),
        )
        operation = subnet_client.update(update).wait()
        self._sync_preparation_operation(operation, action="Route table attachment")
        try:
            verified_subnet = subnet_client.get(GetSubnetRequest(id=subnet_id)).wait()
        except Exception as error:
            raise RuntimeError("Route table attachment could not be reread") from error
        verified_route_table_id = str(
            getattr(getattr(verified_subnet, "spec", None), "route_table_id", "") or ""
        )
        if (
            self._resource_id(verified_subnet) != subnet_id
            or verified_route_table_id != route_table_id
        ):
            raise RuntimeError("Route table attachment postcondition was not satisfied")
        verified_table = self._strict_route_table_by_id(route_table_client, route_table_id)
        self._validate_preparation_route_table(verified_table, network_id=network_id)
        self._ensure_default_egress_route(
            route_client,
            subnet_client,
            verified_table,
            network_id=network_id,
            subnet_id=subnet_id,
            require_exclusive_before_create=True,
        )

    def _ensure_vpngw_route_table(
        self,
        client: t.Any,
        subnet_id: str | None,
    ) -> None:
        """Converge and verify the gateway subnet's dedicated route table."""
        if not client or not subnet_id:
            raise RuntimeError("Gateway subnet identity is required for route preparation")
        self._reconcile_vpngw_route_table(client, subnet_id)

    def get_instance_ssh_target(self, instance_index: int) -> str:
        # Placeholder fallback if external IP wasn't available in plan
        return f"{instance_index}"

    def _build_cloud_init(
        self,
        ssh_key: str | None = None,
        local_prefixes: list[str] | None = None,
    ) -> str:
        """Return a hardened cloud-init to install deps, configure security, and setup the gateway.

        This prepares a production-hardened VPN gateway with:
        - SSH hardening (key-only, no root, limited retries)
        - Fail2ban for SSH brute-force protection
        - Unattended security upgrades
        - Auditd for exec logging
        - UFW firewall (allows IPsec, SSH from management, blocks rest)
        - System hardening (sysctl, minimal packages)

        Args:
            ssh_key: Optional SSH public key to add to ubuntu user's authorized_keys
            local_prefixes: Optional list of local VPC prefixes for firewall configuration
        """
        try:
            with resources.as_file(
                resources.files("nebius_vpngw").joinpath("systemd/nebius-vpngw-agent.service")
            ) as p:
                unit_text = p.read_text(encoding="utf-8")
        except Exception:
            unit_text = textwrap.dedent(
                """
                [Unit]
                Description=Nebius VPNGW Agent
                After=network.target

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
            ).strip()

        indented_unit = textwrap.indent(unit_text, " " * 12)

        # Build users section with SSH key if provided
        users_section = ""
        if ssh_key:
            users_section = (
                f"users:\n  - name: ubuntu\n    ssh_authorized_keys:\n      - {ssh_key}\n"
            )

        cloud = (
            "#cloud-config\n"
            f"{users_section}"
            "package_update: true\n"
            "package_upgrade: true\n"
            "packages:\n"
            "  - strongswan\n"
            "  - strongswan-swanctl\n"
            "  - strongswan-pki\n"
            "  - libcharon-extra-plugins\n"
            "  - python3\n"
            "  - python3-pip\n"
            "  - python3-yaml\n"
            "  - ufw\n"
            "  - fail2ban\n"
            "  - unattended-upgrades\n"
            "  - auditd\n"
            "  - iproute2\n"
            "  - curl\n"
            "  - vim\n"
            "write_files:\n"
            "  - path: /etc/systemd/system/nebius-vpngw-agent.service\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            f"{indented_unit}\n"
            "  - path: /etc/ssh/sshd_config.d/50-vpngw.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            Port 22\n"
            "            AddressFamily any\n"
            "            ListenAddress 0.0.0.0\n"
            "            PermitRootLogin no\n"
            "            PasswordAuthentication no\n"
            "            ChallengeResponseAuthentication no\n"
            "            UsePAM yes\n"
            "            X11Forwarding no\n"
            "            AllowTcpForwarding yes\n"
            "            GatewayPorts no\n"
            "            PermitTunnel no\n"
            "            MaxAuthTries 3\n"
            "            LogLevel VERBOSE\n"
            "            AcceptEnv LANG LC_*\n"
            "            Subsystem sftp /usr/lib/openssh/sftp-server\n"
            "  - path: /etc/fail2ban/jail.d/sshd.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [sshd]\n"
            "            enabled = true\n"
            "            port = ssh\n"
            "            logpath = /var/log/auth.log\n"
            "            maxretry = 3\n"
            "            bantime = 3600\n"
            "            findtime = 600\n"
            "  - path: /etc/apt/apt.conf.d/50unattended-upgrades\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            Unattended-Upgrade::Allowed-Origins {\n"
            '              "${distro_id}:${distro_codename}-security";\n'
            "            };\n"
            '            Unattended-Upgrade::AutoFixInterruptedDpkg "true";\n'
            '            Unattended-Upgrade::MinimalSteps "true";\n'
            '            Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";\n'
            '            Unattended-Upgrade::Remove-Unused-Dependencies "true";\n'
            '            Unattended-Upgrade::Automatic-Reboot "false";\n'
            '            Unattended-Upgrade::Automatic-Reboot-Time "03:00";\n'
            "  - path: /etc/audit/rules.d/50-vpngw.rules\n"
            '    permissions: "0640"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # Audit execve for security monitoring\n"
            "            -a always,exit -F arch=b64 -S execve -k exec\n"
            "            -a always,exit -F arch=b32 -S execve -k exec\n"
            "            # Audit file modifications in sensitive directories\n"
            "            -w /etc/ipsec.conf -p wa -k ipsec_config\n"
            "            -w /etc/ipsec.secrets -p wa -k ipsec_secrets\n"
            "            -w /etc/frr/frr.conf -p wa -k frr_config\n"
            "            -w /etc/nebius-vpngw/ -p wa -k vpngw_config\n"
            "  - path: /usr/local/bin/log-restart-required.sh\n"
            '    permissions: "0755"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            #!/bin/bash\n"
            "            if [ -f /var/run/reboot-required ]; then\n"
            '              logger -t vpngw "Restart required after updates on $(hostname)"\n'
            "            fi\n"
            "  - path: /etc/systemd/system/log-restart-required.service\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            Description=Log when a restart is required after upgrades\n"
            "            [Service]\n"
            "            Type=oneshot\n"
            "            ExecStart=/usr/local/bin/log-restart-required.sh\n"
            "  - path: /etc/systemd/system/log-restart-required.timer\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            Description=Run restart-required logger hourly\n"
            "            [Timer]\n"
            "            OnBootSec=5min\n"
            "            OnUnitActiveSec=1h\n"
            "            Persistent=true\n"
            "            [Install]\n"
            "            WantedBy=timers.target\n"
            "  - path: /etc/vpngw_mgmt_cidrs\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # Management CIDRs allowed for SSH access (one per line)\n"
            "            # Examples:\n"
            "            # 203.0.113.4/32\n"
            "            # 198.51.100.0/24\n"
            "            # This file will be populated by the agent with allowed management IPs\n"
            "  - path: /etc/vpngw_peer_ips\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # VPN peer public IPs (one per line)\n"
            "            # This file will be populated by the agent from connection configs\n"
            "  - path: /etc/vpngw_local_prefixes\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # Local VPC prefixes to allow through firewall (one per line)\n"
            "            # This file will be populated by the agent from gateway.local_prefixes\n"
        )

        # Add local prefixes if provided
        if local_prefixes:
            for prefix in local_prefixes:
                cloud += f"            {prefix}\n"

        firewall_script = _read_firewall_setup_script()
        esp4_preflight_script = _read_esp4_preflight_script()
        cloud += (
            "  - path: /usr/local/bin/setup-vpngw-firewall.sh\n"
            '    permissions: "0755"\n'
            "    owner: root:root\n"
            "    content: |\n"
            + textwrap.indent(firewall_script.rstrip() + "\n", "            ")
            + "  - path: /usr/local/bin/nebius-vpngw-esp4-preflight.sh\n"
            '    permissions: "0755"\n'
            "    owner: root:root\n"
            "    content: |\n"
            + textwrap.indent(esp4_preflight_script.rstrip() + "\n", "            ")
        )

        cloud += (
            "  - path: /etc/systemd/system/nebius-vpngw-esp4-preflight.service\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            Description=Nebius VPNGW ESP4 kernel module readiness preflight\n"
            "            After=local-fs.target systemd-modules-load.service\n"
            "            Before=strongswan-starter.service strongswan.service frr.service nebius-vpngw-agent.service\n"
            "            \n"
            "            [Service]\n"
            "            Type=oneshot\n"
            "            ExecStart=/usr/local/bin/nebius-vpngw-esp4-preflight.sh --verify\n"
            "            RemainAfterExit=yes\n"
            "            \n"
            "            [Install]\n"
            "            WantedBy=multi-user.target\n"
            "  - path: /etc/frr/daemons\n"
            '    permissions: "0644"\n'
            "    owner: frr:frr\n"
            "    content: |\n"
            "            # FRR daemons configuration - enable bgpd\n"
            "            bgpd=yes\n"
            '            bgpd_options="   -A 0.0.0.0"\n'
            "            ospfd=no\n"
            "            ospf6d=no\n"
            "            ripd=no\n"
            "            ripngd=no\n"
            "            isisd=no\n"
            "            pimd=no\n"
            "            ldpd=no\n"
            "            nhrpd=no\n"
            "            eigrpd=no\n"
            "            babeld=no\n"
            "            sharpd=no\n"
            "            pbrd=no\n"
            "            bfdd=no\n"
            "            fabricd=no\n"
            "            vrrpd=no\n"
            "  - path: /etc/sysctl.d/99-zzz-vpngw.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            ########################################\n"
            "            # Nebius VPN Gateway – XFRM Routing Stack\n"
            "            ########################################\n"
            "            \n"
            "            # 1. Global forwarding – the box is a router\n"
            "            net.ipv4.ip_forward = 1\n"
            "            \n"
            "            # 1.1 TCP MTU probing – recover when PMTUD is blocked\n"
            "            net.ipv4.tcp_mtu_probing = 1\n"
            "            \n"
            "            # 2. Reverse path filtering\n"
            "            # Route-based IPsec with xfrm-interfaces often looks asymmetric to the kernel.\n"
            "            # Strict or even loose rp_filter can drop valid tunnel traffic. Disable it.\n"
            "            net.ipv4.conf.all.rp_filter = 0\n"
            "            net.ipv4.conf.default.rp_filter = 0\n"
            "            \n"
            "            # Interface-specific – belt and suspenders\n"
            "            net.ipv4.conf.eth0.rp_filter = 0\n"
            "            net.ipv4.conf.lo.rp_filter = 0\n"
            "            \n"
            "            # 3. Disable ICMP redirects – the gateway must not rely on redirects, and\n"
            "            # cloud fabric should never try to optimize routes via redirects.\n"
            "            net.ipv4.conf.all.accept_redirects = 0\n"
            "            net.ipv4.conf.default.accept_redirects = 0\n"
            "            net.ipv4.conf.all.send_redirects = 0\n"
            "            net.ipv4.conf.default.send_redirects = 0\n"
            "            \n"
            "            # 4. Source routing – off for security and predictability\n"
            "            net.ipv4.conf.all.accept_source_route = 0\n"
            "            net.ipv4.conf.default.accept_source_route = 0\n"
            "            \n"
            "            # 5. Martian logging (optional, useful for debugging weird traffic)\n"
            "            net.ipv4.conf.all.log_martians = 1\n"
            "            net.ipv4.conf.default.log_martians = 1\n"
            "            \n"
            "            # 6. Basic IPv6 hygiene (if you are not using IPv6 in the tunnels yet)\n"
            "            net.ipv6.conf.all.accept_redirects = 0\n"
            "            net.ipv6.conf.default.accept_redirects = 0\n"
            "            net.ipv6.conf.all.accept_ra = 0\n"
            "            net.ipv6.conf.default.accept_ra = 0\n"
            "  - path: /etc/systemd/system/ufw.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=network-online.target cloud-init.service\n"
            "            Wants=network-online.target\n"
            "  - path: /etc/systemd/system/strongswan-starter.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=nebius-vpngw-esp4-preflight.service ufw.service network-online.target\n"
            "            Wants=ufw.service\n"
            "            Requires=nebius-vpngw-esp4-preflight.service\n"
            "  - path: /etc/systemd/system/frr.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=strongswan-starter.service\n"
            "            Wants=strongswan-starter.service\n"
            "  - path: /etc/systemd/system/nebius-vpngw-agent.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=strongswan-starter.service frr.service\n"
            "            Wants=strongswan-starter.service frr.service\n"
        )

        # Add runcmd section
        cloud += (
            "runcmd:\n"
            '  - [ bash, -lc, "mkdir -p /etc/nebius-vpngw" ]\n'
            '  - [ bash, -lc, "mkdir -p /var/lib/nebius-vpngw" ]\n'
            '  - [ bash, -lc, "mkdir -p /etc/ipsec.d" ]\n'
            "  # Install FRR 10.x from official repository (fixes route installation bug in 8.4.4)\n"
            '  - [ bash, -c, "curl -s https://deb.frrouting.org/frr/keys.asc | tee /usr/share/keyrings/frrouting.asc > /dev/null" ]\n'
            '  - [ bash, -c, "UBUNTU_CODENAME=$(lsb_release -cs); echo \\"deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr $UBUNTU_CODENAME frr-stable\\" > /etc/apt/sources.list.d/frr.list" ]\n'
            "  - [ apt-get, update ]\n"
            '  - [ bash, -c, "DEBIAN_FRONTEND=noninteractive apt-get install -y frr frr-pythontools" ]\n'
            "  # Prepare ESP4 after Ubuntu package upgrades; defer VPN services if a reboot is required\n"
            '  - [ bash, -lc, "/usr/local/bin/nebius-vpngw-esp4-preflight.sh --prepare; rc=$?; if [ $rc -eq 75 ]; then exit 0; fi; exit $rc" ]\n'
            "  # Comment out conflicting sysctl settings in /etc/sysctl.conf (prevents our 99-zzz-vpngw.conf from being overridden)\n"
            "  - [ bash, -c, \"sed -i 's/^net.ipv4.ip_forward=.*/#&  # Overridden by 99-zzz-vpngw.conf/' /etc/sysctl.conf\" ]\n"
            "  - [ bash, -c, \"sed -i 's/^net.ipv4.conf.all.rp_filter=.*/#&  # Overridden by 99-zzz-vpngw.conf/' /etc/sysctl.conf\" ]\n"
            "  - [ bash, -c, \"sed -i 's/^net.ipv4.conf.default.rp_filter=.*/#&  # Overridden by 99-zzz-vpngw.conf/' /etc/sysctl.conf\" ]\n"
            "  # Apply all sysctl settings (99-zzz-vpngw.conf loads after 99-sysctl.conf symlink)\n"
            "  - [ sysctl, --system ]\n"
            "  # CRITICAL: Enable UFW firewall for security hardening\n"
            "  # UFW MUST be active for proper packet forwarding through XFRM tunnels\n"
            "  # Without UFW active, netfilter is not properly initialized and VPC traffic\n"
            "  # may not be correctly routed through the VPN gateway\n"
            '  - [ bash, -lc, "/usr/local/bin/setup-vpngw-firewall.sh > /var/log/vpngw-firewall-setup.log 2>&1 || true" ]\n'
            "  # Load auditd rules\n"
            '  - [ bash, -lc, "augenrules --load || true" ]\n'
            "  # Enable and start services\n"
            "  - [ systemctl, daemon-reload ]\n"
            "  - [ systemctl, enable, auditd ]\n"
            "  - [ systemctl, enable, fail2ban ]\n"
            "  - [ systemctl, enable, log-restart-required.timer ]\n"
            "  - [ systemctl, enable, nebius-vpngw-esp4-preflight ]\n"
            "  - [ systemctl, enable, strongswan-starter ]\n"
            "  - [ systemctl, enable, frr ]\n"
            "  - [ systemctl, enable, nebius-vpngw-agent ]\n"
            "  - [ systemctl, start, auditd ]\n"
            "  - [ systemctl, start, fail2ban ]\n"
            "  - [ systemctl, start, log-restart-required.timer ]\n"
            '  - [ bash, -lc, "if [ ! -f /var/lib/nebius-vpngw/esp4-reboot-pending ]; then systemctl start nebius-vpngw-esp4-preflight strongswan-starter frr; fi" ]\n'
            "  # Validate SSH configuration, then activate the service model provided by the image\n"
            '  - [ bash, -lc, "set -e; /usr/sbin/sshd -t; systemctl reset-failed ssh.service ssh.socket 2>/dev/null || true; if systemctl is-active --quiet ssh.socket || systemctl is-enabled --quiet ssh.socket; then systemctl restart ssh.socket; else systemctl restart ssh.service; fi; ss -H -lnt sport = :22 | grep -q ." ]\n'
            "  # Reboot only after cloud-init has written files and enabled services\n"
            '  - [ bash, -lc, "if [ -f /var/lib/nebius-vpngw/esp4-reboot-pending ]; then logger -t vpngw \\"Rebooting to activate ESP4/kernel update before VPN services start\\"; shutdown -r +1 \\"Nebius VPN Gateway rebooting to activate ESP4/kernel update\\"; fi" ]\n'
            "  # Log completion\n"
            "  - [ bash, -c, \"logger -t vpngw 'Cloud-init hardening complete for VPN gateway'\" ]\n"
        )
        return cloud
