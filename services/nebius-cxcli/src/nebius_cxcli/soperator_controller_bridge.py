"""Upgrade-only Slurm controller HA bridge contracts and Kubernetes manifests."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .slurm_action_journal import (
    SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
    SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION,
)

CONTROLLER_BRIDGE_SCHEMA = "nebius-cxcli-soperator-controller-bridge/v2"
CONTROLLER_BRIDGE_SOURCE_CONFIGURATION_SCHEMA = (
    "nebius-cxcli-controller-bridge-source-configuration/v1"
)
CONTROLLER_BRIDGE_MOUNT_CANARY_SCHEMA = "nebius-cxcli-controller-bridge-mount-canary/v1"
CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY = "pre-source-mutation"
CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY = "pre-source-fence"
CONTROLLER_BRIDGE_NAMESPACE = "cxcli-soperator-upgrade-bridge"
CONTROLLER_BRIDGE_LABEL = "nebius.ai/cxcli-controller-bridge"
CONTROLLER_BRIDGE_SLOT_LABEL = "nebius.ai/cxcli-controller-bridge-slot"
CONTROLLER_BRIDGE_TAINT_KEY = CONTROLLER_BRIDGE_LABEL
CONTROLLER_BRIDGE_STATE_PVC = "cxcli-controller-bridge-state"
CONTROLLER_BRIDGE_JAIL_PVC = "cxcli-controller-bridge-jail"
CONTROLLER_BRIDGE_CONTROLLER_HOSTS = (
    "cxcli-slurm-controller-bridge-0"
    "(cxcli-slurm-controller-bridge-0.cxcli-slurm-controller-bridge."
    "cxcli-soperator-upgrade-bridge.svc)",
    "cxcli-slurm-controller-bridge-1"
    "(cxcli-slurm-controller-bridge-1.cxcli-slurm-controller-bridge."
    "cxcli-soperator-upgrade-bridge.svc)",
)
CONTROLLER_BRIDGE_MOUNT_HELPER_COMMAND = (
    "/bin/sh",
    "-ec",
    (
        "mount_one() { "
        'tag="$1"; path="$2"; host_path="/host${path}"; '
        'mkdir -p "${host_path}"; '
        'if ! awk -v target="${host_path}" -v source="${tag}" '
        "'$1 == source && $2 == target && $4 ~ /(^|,)rw(,|$)/ "
        "{ found=1 } END { exit(found ? 0 : 1) }' /proc/mounts; then "
        'umount "${host_path}" 2>/dev/null || true; '
        'mount -t virtiofs -o rw,relatime "${tag}" "${host_path}"; '
        "fi; }; "
        'mount_one "$CONTROLLER_SPOOL_TAG" "$CONTROLLER_SPOOL_PATH"; '
        'mount_one "$JAIL_TAG" "$JAIL_PATH"; '
        "while :; do sleep 30; done"
    ),
)


class BridgeStage(StrEnum):
    PLANNED = "planned"
    SUBSTRATE_READY = "substrate-ready"
    SOURCE_CONFIGURED = "source-configured"
    SOURCE_PRECOPIED = "source-precopied"
    SOURCE_FENCED = "source-fenced"
    STATE_PROMOTED = "state-promoted"
    SOURCE_HA_ACTIVE = "source-ha-active"
    TARGET_HA_ACTIVE = "target-ha-active"
    BRIDGE_FENCED = "bridge-fenced"
    TARGET_SINGLETON_ACTIVE = "target-singleton-active"
    HANDOFF_VALIDATED = "handoff-validated"
    PARTITIONS_RESTORED = "partitions-restored"
    CLEANED = "cleaned"


_BRIDGE_STAGES = tuple(BridgeStage)
_BRIDGE_STAGE_INDEX = {stage.value: index for index, stage in enumerate(_BRIDGE_STAGES)}
_BRIDGE_AUTHORITY_OWNERS = frozenset(
    {"source-singleton", "bridge-source", "bridge-target", "target-singleton", "none"}
)
_CONTROLLER_RUNTIME_FENCE_SCHEMA = "nebius-cxcli-controller-runtime-fence/v1"
CONTROLLER_BRIDGE_JWT_MATERIAL_CONTRACT_SCHEMA = "nebius-cxcli-controller-jwt-material-contract/v1"
CONTROLLER_BRIDGE_JWT_MATERIAL_PREFLIGHT_SCHEMA = (
    "nebius-cxcli-controller-jwt-material-preflight/v1"
)
CONTROLLER_BRIDGE_JWT_MATERIAL_PROOF_SCHEMA = "nebius-cxcli-controller-jwt-material-proof/v1"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be non-empty.")
    return text


def _required_mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping.")
    return value


def _sha256(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return text


def _journal_payload_fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _image_digest_reference(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", text):
        raise ValueError(f"{field} must be an immutable OCI image digest reference.")
    return text


def _safe_token(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?", text):
        raise ValueError(f"{field} must be a DNS-safe token.")
    return text


def _validate_runtime_fence_evidence(
    value: object,
    *,
    field: str,
    expected_count: int,
) -> None:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != expected_count
    ):
        raise ValueError(f"{field} must contain exactly {expected_count} node proof(s).")
    node_uids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or item.get("schema") != _CONTROLLER_RUNTIME_FENCE_SCHEMA:
            raise ValueError(f"{field} has an invalid runtime-fence schema.")
        node_uids.add(_required_text(item.get("node_uid"), field=f"{field} node_uid"))
        _required_text(item.get("node_name"), field=f"{field} node_name")
        _sha256(item.get("marker_sha256"), field=f"{field} marker_sha256")
        counts = {
            name: item.get(name)
            for name in (
                "slurmctld_count",
                "writable_state_mount_count",
                "inspected_process_count",
                "unreadable_process_count",
            )
        }
        if (
            item.get("fenced") is not True
            or any(
                not isinstance(count, int) or isinstance(count, bool) for count in counts.values()
            )
            or counts["slurmctld_count"] != 0
            or counts["writable_state_mount_count"] != 0
            or counts["inspected_process_count"] <= 0
            or counts["unreadable_process_count"] != 0
        ):
            raise ValueError(f"{field} did not prove process and writable-mount absence.")
    if len(node_uids) != expected_count:
        raise ValueError(f"{field} must bind {expected_count} distinct immutable node UID(s).")


@dataclass(frozen=True)
class BridgeSourceBinding:
    namespace: str
    slurmcluster_name: str
    slurmcluster_uid: str
    controller_workload_name: str
    controller_workload_uid: str
    controller_pod_name: str
    controller_pod_uid: str
    controller_pvc_name: str
    controller_pvc_uid: str
    controller_pv_name: str
    controller_pv_uid: str
    jail_pvc_name: str
    jail_pvc_uid: str
    jail_pv_name: str
    jail_pv_uid: str
    jail_filesystem_id: str
    slurm_image_digest: str
    slurm_version: str
    configuration_fingerprint: str
    munge_fingerprint: str
    jwt_fingerprint: str

    def as_payload(self) -> dict[str, str]:
        payload = {
            field: _required_text(getattr(self, field), field=f"bridge source {field}")
            for field in self.__dataclass_fields__
        }
        payload["slurm_image_digest"] = _image_digest_reference(
            self.slurm_image_digest,
            field="bridge source slurm_image_digest",
        )
        for field in ("configuration_fingerprint", "munge_fingerprint", "jwt_fingerprint"):
            payload[field] = _sha256(payload[field], field=f"bridge source {field}")
        return payload


@dataclass(frozen=True)
class BridgePlacementDomain:
    """One immutable scheduling domain used by a bridge controller Pod.

    External campaigns own temporary one-node domains. Managed campaigns bind
    the pre-existing controller and system groups and may mutate only
    Kubernetes bridge resources.
    """

    name: str
    role: str
    template: Mapping[str, Any]
    ownership: str = "external-temporary"
    node_group_id: str = ""
    selector: Mapping[str, str] | None = None
    live_node_uid: str = ""
    mutation_policy: str = "provider-create-delete"
    cleanup_policy: str = "delete-domain"
    mount_policy: str = "cxcli-mount-daemonset"
    ready_capacity: int = 1

    def __post_init__(self) -> None:
        _safe_token(self.name, field="bridge placement-domain name")
        if self.role not in {"controller", "system", "external-a", "external-b"}:
            raise ValueError("bridge placement-domain role is invalid.")
        if self.ownership not in {"managed-existing", "external-temporary"}:
            raise ValueError("bridge placement-domain ownership is invalid.")
        expected = {
            "managed-existing": (
                "kubernetes-only",
                "preserve-domain",
                "reuse-chart-mount-substrate",
            ),
            "external-temporary": (
                "provider-create-delete",
                "delete-domain",
                "cxcli-mount-daemonset",
            ),
        }[self.ownership]
        if (
            self.mutation_policy,
            self.cleanup_policy,
            self.mount_policy,
        ) != expected:
            raise ValueError(
                "bridge placement-domain ownership, mutation, cleanup, and mount policies "
                "must form one canonical adapter contract."
            )
        if self.ownership == "managed-existing" and not self.node_group_id:
            raise ValueError("managed bridge placement domains require a stable node-group ID.")
        if isinstance(self.ready_capacity, bool) or self.ready_capacity < 1:
            raise ValueError("bridge placement-domain ready_capacity must be positive.")

    @classmethod
    def external(
        cls, *, name: str, role: str, template: Mapping[str, Any]
    ) -> BridgePlacementDomain:
        return cls(name=name, role=role, template=template)

    @classmethod
    def managed(
        cls,
        *,
        name: str,
        role: str,
        node_group_id: str,
        selector: Mapping[str, str],
        live_node_uid: str,
        ready_capacity: int,
        template: Mapping[str, Any] | None = None,
    ) -> BridgePlacementDomain:
        return cls(
            name=name,
            role=role,
            template=template or {},
            ownership="managed-existing",
            node_group_id=_required_text(
                node_group_id,
                field="managed bridge placement-domain node_group_id",
            ),
            selector=dict(selector),
            live_node_uid=_required_text(
                live_node_uid,
                field="managed bridge placement-domain live_node_uid",
            ),
            mutation_policy="kubernetes-only",
            cleanup_policy="preserve-domain",
            mount_policy="reuse-chart-mount-substrate",
            ready_capacity=ready_capacity,
        )


def managed_bridge_placement_domains_from_live_nodes(
    *,
    desired_node_groups: Mapping[str, Any],
    kubernetes_nodes: Sequence[Mapping[str, Any]],
) -> tuple[BridgePlacementDomain, BridgePlacementDomain]:
    """Bind the canonical managed bridge domains without provider mutation.

    This is deliberately strict: an existing managed cluster must first be
    reconciled to the current production profile. The upgrade path never
    creates replacement capacity or accepts an autoscaled compatibility shape.
    """

    domains: list[BridgePlacementDomain] = []
    group_ids: set[str] = set()
    node_uids: set[str] = set()
    for role, expected_capacity in (("controller", 2), ("system", 3)):
        raw_group = desired_node_groups.get(role)
        if not isinstance(raw_group, Mapping):
            raise ValueError(f"managed bridge substrate lacks the {role} node group.")
        if raw_group.get("node_count") != expected_capacity or "autoscaling" in raw_group:
            raise ValueError(
                f"managed bridge {role} must be fixed at {expected_capacity} nodes with "
                "autoscaling absent; reconcile desired state before upgrading."
            )
        filesystem_keys = raw_group.get("sfs_filesystem_keys")
        if not isinstance(filesystem_keys, Sequence) or isinstance(
            filesystem_keys, (str, bytes, bytearray)
        ):
            filesystem_keys = ()
        normalized_filesystems = {
            str(item or "").strip() for item in filesystem_keys if str(item or "").strip()
        }
        if raw_group.get("jail") is not True or "controller-spool" not in normalized_filesystems:
            raise ValueError(
                f"managed bridge {role} must attach both Jail and controller-spool storage."
            )
        desired_labels = raw_group.get("node_labels")
        if not isinstance(desired_labels, Mapping) or (
            str(desired_labels.get("nebius.ai/soperator-bridge-domain", "") or "").strip() != role
        ):
            raise ValueError(
                f"managed bridge {role} lacks its canonical Kubernetes placement label."
            )

        matching_nodes: list[Mapping[str, Any]] = []
        for node in kubernetes_nodes:
            metadata = node.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            labels = metadata.get("labels")
            labels = labels if isinstance(labels, Mapping) else {}
            if str(labels.get("nebius.ai/soperator-bridge-domain", "") or "").strip() == role:
                matching_nodes.append(node)
        if len(matching_nodes) != expected_capacity:
            raise ValueError(
                f"managed bridge {role} requires exactly {expected_capacity} live nodes; "
                f"discovered {len(matching_nodes)}."
            )

        live_group_ids: set[str] = set()
        live_uids: list[str] = []
        for node in matching_nodes:
            metadata = node.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            labels = metadata.get("labels")
            labels = labels if isinstance(labels, Mapping) else {}
            group_id = str(labels.get("nebius.com/node-group-id", "") or "").strip()
            uid = str(metadata.get("uid", "") or "").strip()
            spec = node.get("spec")
            spec = spec if isinstance(spec, Mapping) else {}
            status = node.get("status")
            status = status if isinstance(status, Mapping) else {}
            conditions = status.get("conditions")
            ready = any(
                isinstance(condition, Mapping)
                and condition.get("type") == "Ready"
                and str(condition.get("status", "") or "").lower() == "true"
                for condition in (
                    conditions
                    if isinstance(conditions, Sequence)
                    and not isinstance(conditions, (str, bytes, bytearray))
                    else ()
                )
            )
            if not group_id or not uid or spec.get("unschedulable") is True or not ready:
                raise ValueError(
                    f"managed bridge {role} contains a node without stable group ID, UID, "
                    "Ready capacity, or schedulability."
                )
            live_group_ids.add(group_id)
            live_uids.append(uid)
        if len(live_group_ids) != 1:
            raise ValueError(f"managed bridge {role} spans multiple node-group scheduling domains.")
        node_group_id = next(iter(live_group_ids))
        if node_group_id in group_ids or any(uid in node_uids for uid in live_uids):
            raise ValueError("managed bridge controller and system domains are not distinct.")
        group_ids.add(node_group_id)
        node_uids.update(live_uids)
        domains.append(
            BridgePlacementDomain.managed(
                name=role,
                role=role,
                node_group_id=node_group_id,
                selector={"nebius.ai/soperator-bridge-domain": role},
                live_node_uid=sorted(live_uids)[0],
                ready_capacity=expected_capacity,
                template={
                    "eligible_node_uids": sorted(live_uids),
                    "attachment_fingerprint": _journal_payload_fingerprint(
                        {
                            "jail": True,
                            "sfs_filesystem_keys": sorted(normalized_filesystems),
                        }
                    ),
                },
            )
        )
    return domains[0], domains[1]


@dataclass(frozen=True)
class BridgePlan:
    campaign_fingerprint: str
    cluster_id: str
    cluster_name: str
    source_kubernetes_version: str
    source_slurm_image: str
    target_slurm_image: str
    source_slurm_version: str
    target_slurm_version: str
    state_save_location: str
    controller_spool_attachment: Mapping[str, Any]
    jail_attachment: Mapping[str, Any]
    placement_domains: tuple[BridgePlacementDomain, BridgePlacementDomain]
    namespace: str = CONTROLLER_BRIDGE_NAMESPACE

    def __post_init__(self) -> None:
        _sha256(self.campaign_fingerprint, field="bridge campaign_fingerprint")
        _required_text(self.cluster_id, field="bridge cluster_id")
        _safe_token(self.cluster_name, field="bridge cluster_name")
        _required_text(self.source_kubernetes_version, field="bridge source Kubernetes version")
        _image_digest_reference(self.source_slurm_image, field="bridge source Slurm image")
        _image_digest_reference(self.target_slurm_image, field="bridge target Slurm image")
        _required_text(self.source_slurm_version, field="bridge source Slurm version")
        _required_text(self.target_slurm_version, field="bridge target Slurm version")
        if not str(self.state_save_location or "").startswith("/"):
            raise ValueError("bridge state_save_location must be absolute.")
        _safe_token(self.namespace, field="bridge namespace")
        names = [
            _safe_token(item.name, field="bridge placement-domain name")
            for item in self.placement_domains
        ]
        if len(set(names)) != 2:
            raise ValueError("bridge placement domains require two distinct names.")
        ownerships = {item.ownership for item in self.placement_domains}
        if len(ownerships) != 1:
            raise ValueError("bridge placement domains must use one capacity adapter.")
        if "managed-existing" in ownerships and {item.role for item in self.placement_domains} != {
            "controller",
            "system",
        }:
            raise ValueError(
                "managed bridge placement domains must be the controller and system roles."
            )
        if not self.controller_spool_attachment:
            raise ValueError("bridge controller-spool attachment must be provided.")
        if not self.jail_attachment:
            raise ValueError("bridge Jail attachment must be provided.")
        attachment_tags = {
            _safe_token(
                attachment.get("mount_tag"),
                field=f"bridge {label} attachment mount tag",
            )
            for label, attachment in (
                ("controller-spool", self.controller_spool_attachment),
                ("Jail", self.jail_attachment),
            )
        }
        if len(attachment_tags) != 2:
            raise ValueError("bridge controller-spool and Jail mount tags must be distinct.")


def new_bridge_journal(
    *,
    source: BridgeSourceBinding,
    plan: BridgePlan,
    authority_epoch: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or _utc_now()
    source_payload = source.as_payload()
    epoch = _safe_token(authority_epoch, field="bridge authority_epoch")
    journal: dict[str, Any] = {
        "schema": CONTROLLER_BRIDGE_SCHEMA,
        "stage": BridgeStage.PLANNED.value,
        "created_at": timestamp,
        "updated_at": timestamp,
        "namespace": plan.namespace,
        "campaign_fingerprint": plan.campaign_fingerprint,
        "cluster_id": plan.cluster_id,
        "cluster_name": plan.cluster_name,
        "source_binding": source_payload,
        "node_groups": [
            {
                "slot": index,
                "name": item.name,
                "role": item.role,
                "ownership": item.ownership,
                "id": item.node_group_id,
                "selector": dict(item.selector or {}),
                "live_node_uid": item.live_node_uid,
                "mutation_policy": item.mutation_policy,
                "cleanup_policy": item.cleanup_policy,
                "mount_policy": item.mount_policy,
                "scheduling_failure_domain": {
                    "topology_key": "nebius.com/node-group-id",
                    "node_group_id": item.node_group_id,
                },
                "ready_capacity": item.ready_capacity,
                "kubernetes_version": plan.source_kubernetes_version,
                "controller_spool_attachment_sha256": _journal_payload_fingerprint(
                    plan.controller_spool_attachment
                ),
                "jail_attachment_sha256": _journal_payload_fingerprint(plan.jail_attachment),
                "created": item.ownership == "external-temporary" and False,
                "bound": item.ownership == "managed-existing",
                "excluded_from_provider_upgrade": True,
            }
            for index, item in enumerate(plan.placement_domains)
        ],
        "kubernetes_resources": [],
        "security_contract": {},
        "authority": {
            "epoch": epoch,
            "owner": "source-singleton",
            "first_bridge_write_at": "",
            "source_restart_prohibited": False,
            "history": [
                {
                    "epoch": epoch,
                    "owner": "source-singleton",
                    "at": timestamp,
                    "reason": "bridge journal created",
                }
            ],
        },
        "authority_lease": {
            "uid": "",
            "resource_version": "",
            "holder_identity": f"{epoch}:source-singleton",
        },
        "authority_lease_transitions": [],
        "runtime_fence_proofs": [],
        "state_manifest": {
            "epoch_directory": "",
            "stable_path": plan.state_save_location,
            "sha256": "",
            "file_count": 0,
            "fsynced": False,
            "promoted": False,
        },
        "state_precopy": {},
        "pre_source_fence_readiness": {},
        "source_fence_intent": {},
        "source_configuration": {},
        "configuration_recoveries": [],
        "jwt_material_contract": {},
        "cold_reader": {},
        "fencing": {
            "source": {
                "proven": False,
                "workload_uid": source_payload["controller_workload_uid"],
                "pod_uid": source_payload["controller_pod_uid"],
                "process_absent": False,
                "writable_mount_absent": False,
                "observed_at": "",
            },
            "bridge": {
                "proven": False,
                "pod_uids": [],
                "processes_absent": False,
                "writable_state_mounts_absent": False,
                "runtime_fence": [],
                "observed_at": "",
            },
        },
        "controller_roles": [],
        "shared_mount_canaries": [],
        "version_transition": {
            "source_image": plan.source_slurm_image,
            "source_version": plan.source_slurm_version,
            "target_image": plan.target_slurm_image,
            "target_version": plan.target_slurm_version,
            "backup_sha256": "",
            "backup_operation": {},
            "backup_recovery": {},
            "both_stopped_at": "",
            "target_write_at": "",
            "downgrade_prohibited": False,
        },
        "target_image_lock": {
            "immutable_reference": plan.target_slurm_image,
            "index_digest": plan.target_slurm_image.rsplit("@", 1)[-1],
            "platform_digest": plan.target_slurm_image.rsplit("@", 1)[-1],
            "os": "linux",
            "architecture": "amd64",
            "variant": "",
        },
        "target_singleton_takeover": {
            "command_gate_applied": False,
            "command_gate_removed_at": "",
            "controller_pod_uid": "",
            "jwt_material_preflight": {},
            "jwt_material_proof": {},
            "state_loaded": False,
            "only_primary_proven": False,
            "final_slurmctld_host_count": 0,
        },
        "login_session_handoff": {
            "revision": 1,
            "state": "uninitialized",
            "locked_at": "",
            "source_services": [],
            "protected_pods": [],
            "sessions": [],
            "no_sessions_at_lock": False,
            "target_ready": False,
            "target_binding": {},
            "target_pod": {},
            "service_switched_at": "",
            "voluntary_handoff_at": "",
            "session_handoff_completed_at": "",
            "indeterminate_reason": "",
        },
        "tui_actions": {
            "journal_schema": "nebius-cxcli-slurm-action-journal/v1",
            "checkpoint_path": "slurm.action_journal",
        },
        "preservation_jobs": {
            "schema": "nebius-cxcli-slurm-preservation-jobs/v1",
            "captured_at": "",
            "jobs": {},
            "verifications": [],
        },
        "partition_restore": {
            "status": "pending",
            "records_sha256": "",
            "partition_count": 0,
            "action_journal_generation": 0,
            "authority_epoch": "",
            "intent_at": "",
            "restored_at": "",
        },
        "cleanup": {
            "intent_at": "",
            "pre_cleanup_preservation_proof": {},
            "kubernetes_operations": {},
            "node_group_operations": {},
            "target_state_binding": {},
            "kubernetes_resources_deleted": [],
            "namespace_deleted": False,
            "node_groups_deleted": [],
            "node_groups_preserved": [],
            "bridge_resources_absent": False,
            "bridge_storage_bindings": {},
            "bridge_storage_verified_at": "",
            "shared_state_retained": False,
            "final_singleton_proven": False,
            "proof_checked_at": "",
            "completed_at": "",
        },
    }
    validate_bridge_journal(journal)
    return journal


def _validate_client_propagation_proof(
    value: object,
    *,
    field: str,
    cluster_name: str,
    controller_hosts: Sequence[str],
    roles: tuple[str, str],
    raised_timeouts: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping.")
    observed_hosts = value.get("controller_hosts")
    if not isinstance(observed_hosts, Sequence) or isinstance(
        observed_hosts, (str, bytes, bytearray)
    ):
        raise ValueError(f"{field} controller_hosts must be a list.")
    if (
        value.get("schema") != "nebius-cxcli-controller-client-propagation/v1"
        or value.get("status") != "verified"
        or value.get("live_rpc_verified") is not True
        or value.get("cluster_name") != cluster_name
        or list(observed_hosts) != list(controller_hosts)
    ):
        raise ValueError(f"{field} authority/configuration proof is incomplete.")
    config_sha256 = _sha256(value.get("config_sha256"), field=f"{field} config_sha256")
    _required_text(value.get("verified_at"), field=f"{field} verified_at")
    _required_text(value.get("revalidated_at"), field=f"{field} revalidated_at")
    timeouts = value.get("timeouts")
    if not isinstance(timeouts, Mapping):
        raise ValueError(f"{field} timeouts must be a mapping.")
    if raised_timeouts and any(
        str(timeouts.get(key, "") or "") != "3600" for key in ("SlurmdTimeout", "SlurmctldTimeout")
    ):
        raise ValueError(f"{field} lacks the exact raised controller-gap timeouts.")
    consumers = value.get("consumers")
    if not isinstance(consumers, Sequence) or isinstance(consumers, (str, bytes, bytearray)):
        raise ValueError(f"{field} consumers must be a list.")
    seen_uids: set[str] = set()
    observed_roles: set[str] = set()
    for item in consumers:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} consumer must be a mapping.")
        role = str(item.get("role", "") or "")
        if role not in roles:
            raise ValueError(f"{field} consumer role is invalid.")
        observed_roles.add(role)
        for identity_field in (
            "name",
            "uid",
            "node_name",
            "container_name",
            "container_id",
        ):
            _required_text(
                item.get(identity_field),
                field=f"{field} consumer {identity_field}",
            )
        uid = str(item.get("uid", "") or "")
        if uid in seen_uids:
            raise ValueError(f"{field} consumer Pod UIDs must be unique.")
        seen_uids.add(uid)
        restart_count = item.get("restart_count")
        if (
            isinstance(restart_count, bool)
            or not isinstance(restart_count, int)
            or restart_count < 0
        ):
            raise ValueError(f"{field} consumer restart_count must be non-negative.")
        if _sha256(item.get("config_sha256"), field=f"{field} consumer config_sha256") != (
            config_sha256
        ):
            raise ValueError(f"{field} consumer configuration digest differs.")
        for rpc_field in (
            "ping_sha256",
            "show_config_sha256",
        ):
            _sha256(item.get(rpc_field), field=f"{field} consumer {rpc_field}")
    if observed_roles != set(roles):
        raise ValueError(f"{field} must cover at least one login and every worker client.")


def _validate_source_configuration_transition(
    value: Mapping[str, Any],
    *,
    stage: str,
) -> None:
    if not value:
        if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_CONFIGURED.value]:
            raise ValueError("Controller bridge source configuration transition is missing.")
        return
    if value.get("schema") != CONTROLLER_BRIDGE_SOURCE_CONFIGURATION_SCHEMA:
        raise ValueError("Controller bridge source configuration schema mismatch.")
    names = value.get("config_map_names")
    if (
        not isinstance(names, Sequence)
        or isinstance(names, (str, bytes, bytearray))
        or len(names) != 1
    ):
        raise ValueError("Controller bridge source configuration requires one ConfigMap.")
    config_map_name = _safe_token(
        names[0],
        field="bridge source configuration ConfigMap name",
    )
    config_key = _required_text(
        value.get("config_key"),
        field="bridge source configuration data key",
    )
    source_reference = _required_mapping(
        value.get("source_reference"),
        field="bridge source configuration source_reference",
    )
    _safe_token(
        source_reference.get("jailed_config_name"),
        field="bridge source configuration JailedConfig name",
    )
    for field in (
        "jailed_config_uid",
        "jailed_config_resource_version",
        "config_map_uid",
    ):
        _required_text(
            source_reference.get(field),
            field=f"bridge source configuration source_reference {field}",
        )
    if (
        source_reference.get("config_map_name") != config_map_name
        or source_reference.get("config_key") != config_key
        or source_reference.get("path") != "/etc/slurm/slurm.conf"
    ):
        raise ValueError(
            "Controller bridge source configuration must bind exactly one active "
            "JailedConfig /etc/slurm/slurm.conf item."
        )
    original_value = value.get("original_slurm_conf")
    intended_value = value.get("intended_slurm_conf")
    if not isinstance(original_value, str) or not original_value.strip():
        raise ValueError("bridge source original slurm.conf must be non-empty text.")
    if not isinstance(intended_value, str) or not intended_value.strip():
        raise ValueError("bridge source intended slurm.conf must be non-empty text.")
    original = original_value
    intended = intended_value
    original_digest = _sha256(
        value.get("original_slurm_conf_sha256"),
        field="bridge source original slurm.conf sha256",
    )
    intended_digest = _sha256(
        value.get("intended_slurm_conf_sha256"),
        field="bridge source intended slurm.conf sha256",
    )
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != original_digest:
        raise ValueError("Controller bridge original slurm.conf digest differs from its content.")
    if hashlib.sha256(intended.encode("utf-8")).hexdigest() != intended_digest:
        raise ValueError("Controller bridge intended slurm.conf digest differs from its content.")
    if original == intended:
        raise ValueError(
            "Controller bridge source configuration transition must change slurm.conf."
        )
    configuration_digests = {
        field: _sha256(value.get(field), field=f"bridge source configuration {field}")
        for field in (
            "original_config_data_sha256",
            "intended_config_data_sha256",
            "bridge_config_sha256",
        )
    }
    if configuration_digests["bridge_config_sha256"] != intended_digest:
        raise ValueError("Controller bridge intended configuration digest binding differs.")
    original_timeouts = value.get("original_timeouts")
    if not isinstance(original_timeouts, Mapping) or set(original_timeouts) != {
        "SlurmdTimeout",
        "SlurmctldTimeout",
    }:
        raise ValueError("Controller bridge original timeout contract is incomplete.")
    for timeout_name in (
        "SlurmdTimeout",
        "SlurmctldTimeout",
    ):
        original_matches = re.findall(
            rf"(?im)^\s*{timeout_name}\s*=\s*(\S+)\s*$",
            original,
        )
        intended_matches = re.findall(
            rf"(?im)^\s*{timeout_name}\s*=\s*(\S+)\s*$",
            intended,
        )
        if len(original_matches) > 1 or str(original_timeouts.get(timeout_name, "") or "") != (
            original_matches[0] if original_matches else ""
        ):
            raise ValueError(f"Controller bridge original {timeout_name} contract differs.")
        if intended_matches != ["3600"]:
            raise ValueError(f"Controller bridge intended {timeout_name} must be exactly 3600.")
    client_contract = value.get("client_config_contract")
    if (
        not isinstance(client_contract, Mapping)
        or client_contract.get("config_sha256") != intended_digest
        or client_contract.get("timeouts") != {"SlurmdTimeout": "3600", "SlurmctldTimeout": "3600"}
    ):
        raise ValueError("Controller bridge intended client configuration contract differs.")
    _required_text(value.get("intent_at"), field="bridge source configuration intent timestamp")
    for field in (
        "original_config_data_sha256",
        "intended_config_data_sha256",
    ):
        if value.get(field) != configuration_digests[field]:
            raise ValueError(f"Controller bridge source configuration {field} differs.")
    copies = value.get("copies")
    if not isinstance(copies, Mapping) or set(copies) != {"source", "bridge"}:
        raise ValueError("Controller bridge source configuration copy journal is incomplete.")
    for copy_name, raw_copy in copies.items():
        if not isinstance(raw_copy, Mapping):
            raise ValueError(f"Controller bridge {copy_name} configuration copy must be a mapping.")
        for field in ("namespace", "name", "uid", "intent_resource_version", "intent_at"):
            _required_text(
                raw_copy.get(field),
                field=f"bridge {copy_name} configuration copy {field}",
            )
        if str(raw_copy.get("name", "") or "") != config_map_name:
            raise ValueError(f"Controller bridge {copy_name} configuration copy name differs.")
        for field in (
            "preimage_data_sha256",
            "intended_data_sha256",
            "preimage_material_sha256",
            "intended_material_sha256",
        ):
            _sha256(
                raw_copy.get(field),
                field=f"bridge {copy_name} configuration copy {field}",
            )
        if raw_copy.get("preimage_data_sha256") != value.get("original_config_data_sha256"):
            raise ValueError(f"Controller bridge {copy_name} configuration preimage differs.")
        if raw_copy.get("intended_data_sha256") != value.get("intended_config_data_sha256"):
            raise ValueError(f"Controller bridge {copy_name} intended configuration differs.")
        copy_state = str(raw_copy.get("state", "") or "")
        if copy_state not in {"intent-recorded", "dispatching", "accepted"}:
            raise ValueError(f"Controller bridge {copy_name} configuration copy state is invalid.")
        if copy_state == "accepted":
            accepted_resource_version = _required_text(
                raw_copy.get("accepted_resource_version"),
                field=f"bridge {copy_name} configuration accepted resourceVersion",
            )
            if accepted_resource_version == str(raw_copy.get("intent_resource_version", "") or ""):
                raise ValueError(
                    f"Controller bridge {copy_name} configuration acceptance did not advance "
                    "resourceVersion."
                )
            accepted_data_sha256 = _sha256(
                raw_copy.get("accepted_data_sha256"),
                field=f"bridge {copy_name} configuration accepted data sha256",
            )
            if accepted_data_sha256 != value.get("intended_config_data_sha256"):
                raise ValueError(
                    f"Controller bridge {copy_name} accepted configuration digest differs."
                )
            _required_text(
                raw_copy.get("accepted_at"),
                field=f"bridge {copy_name} configuration accepted timestamp",
            )
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[
        BridgeStage.SOURCE_CONFIGURED.value
    ] and any(
        not isinstance(copy_value, Mapping) or copy_value.get("state") != "accepted"
        for copy_value in copies.values()
    ):
        raise ValueError("Configured controller bridge requires both exact ConfigMap copies.")
    source_ping_sha256 = str(value.get("source_ping_sha256", "") or "").strip()
    if source_ping_sha256:
        _sha256(
            source_ping_sha256,
            field="bridge source configuration source_ping_sha256",
        )


def _validate_configuration_recoveries(journal: Mapping[str, Any]) -> None:
    recoveries = journal.get("configuration_recoveries", [])
    if not isinstance(recoveries, Sequence) or isinstance(recoveries, (str, bytes, bytearray)):
        raise ValueError("Controller bridge configuration_recoveries must be a list.")
    for index, recovery in enumerate(recoveries):
        field = f"bridge configuration recovery {index}"
        if not isinstance(recovery, Mapping):
            raise ValueError(f"{field} must be a mapping.")
        if (
            recovery.get("schema") != "nebius-cxcli-controller-bridge-configuration-recovery/v1"
            or recovery.get("reason")
            != "operator-restored-exact-preimage-before-authority-transfer"
        ):
            raise ValueError(f"{field} contract is unsupported.")
        _sha256(
            recovery.get("prior_intended_slurm_conf_sha256"),
            field=f"{field} prior intended slurm.conf sha256",
        )
        _sha256(
            recovery.get("source_ping_sha256"),
            field=f"{field} source ping sha256",
        )
        _required_text(recovery.get("recovered_at"), field=f"{field} recovered_at")
        copies = recovery.get("copies")
        if not isinstance(copies, Mapping) or set(copies) != {"source", "bridge"}:
            raise ValueError(f"{field} must bind exact source and bridge copies.")
        for copy_name, copy_record in copies.items():
            if not isinstance(copy_record, Mapping):
                raise ValueError(f"{field} {copy_name} copy must be a mapping.")
            for identity_field in ("namespace", "name", "uid", "resource_version"):
                _required_text(
                    copy_record.get(identity_field),
                    field=f"{field} {copy_name} {identity_field}",
                )
            for digest_field in ("data_sha256", "material_sha256"):
                _sha256(
                    copy_record.get(digest_field),
                    field=f"{field} {copy_name} {digest_field}",
                )


def _scheduling_domain_repair_window(journal: Mapping[str, Any]) -> bool:
    authority = journal.get("authority")
    return (
        journal.get("stage") == BridgeStage.SUBSTRATE_READY.value
        and not journal.get("source_configuration")
        and isinstance(authority, Mapping)
        and authority.get("owner") == "source-singleton"
        and not str(authority.get("first_bridge_write_at", "") or "")
        and authority.get("source_restart_prohibited") is False
    )


def _validate_shared_mount_canary(
    journal: Mapping[str, Any],
    canary: Mapping[str, Any],
) -> None:
    if canary.get("schema") != CONTROLLER_BRIDGE_MOUNT_CANARY_SCHEMA:
        raise ValueError("Controller bridge shared-mount canary schema mismatch.")
    purpose = _safe_token(
        canary.get("purpose"),
        field="bridge shared-mount canary purpose",
    )
    image = _image_digest_reference(
        canary.get("image"),
        field=f"bridge {purpose} shared-mount canary image",
    )
    source_image = str(
        _required_text(
            _required_mapping(journal.get("source_binding"), field="bridge source_binding").get(
                "slurm_image_digest"
            ),
            field="bridge source Slurm image",
        )
    )
    target_image = str(
        _required_mapping(
            journal.get("version_transition"),
            field="bridge version_transition",
        ).get("target_image", "")
        or ""
    )
    if image not in {source_image, target_image}:
        raise ValueError("Controller bridge shared-mount canary image is outside its image lock.")
    if (
        purpose
        in {
            CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
            CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY,
        }
        and image != source_image
    ):
        raise ValueError("Controller bridge pre-source canary must use the exact source image.")
    _sha256(
        canary.get("token_sha256"),
        field=f"bridge {purpose} shared-mount canary token sha256",
    )
    attachment_sha256 = _sha256(
        canary.get("controller_spool_attachment_sha256"),
        field=f"bridge {purpose} controller-spool attachment sha256",
    )
    jail_attachment_sha256 = _sha256(
        canary.get("jail_attachment_sha256"),
        field=f"bridge {purpose} Jail attachment sha256",
    )
    _required_text(
        canary.get("observed_at"),
        field=f"bridge {purpose} shared-mount canary timestamp",
    )
    if canary.get("bidirectional") is not True or canary.get("mount_paths") != {
        "controller_spool": "/shared",
        "jail": "/jail",
    }:
        raise ValueError(
            "Controller bridge shared-mount canary must prove bidirectional state and Jail access."
        )

    node_groups = journal.get("node_groups")
    if (
        not isinstance(node_groups, Sequence)
        or isinstance(node_groups, (str, bytes, bytearray))
        or len(node_groups) != 2
    ):
        raise ValueError("Controller bridge shared-mount canary requires two node groups.")
    expected_groups: dict[int, Mapping[str, Any]] = {}
    for group in node_groups:
        if not isinstance(group, Mapping):
            raise ValueError("Controller bridge shared-mount node-group binding is invalid.")
        slot = group.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in {0, 1}:
            raise ValueError("Controller bridge shared-mount node-group slot is invalid.")
        expected_groups[slot] = group
        if (
            _sha256(
                group.get("controller_spool_attachment_sha256"),
                field="bridge node-group controller-spool attachment sha256",
            )
            != attachment_sha256
        ):
            raise ValueError(
                "Controller bridge canary and node-group controller-spool attachments differ."
            )
        if (
            _sha256(
                group.get("jail_attachment_sha256"),
                field="bridge node-group Jail attachment sha256",
            )
            != jail_attachment_sha256
        ):
            raise ValueError("Controller bridge canary and node-group Jail attachments differ.")

    pods = canary.get("pods")
    if (
        not isinstance(pods, Sequence)
        or isinstance(pods, (str, bytes, bytearray))
        or len(pods) != 2
    ):
        raise ValueError("Controller bridge shared-mount canary requires exactly two Pods.")
    pod_uids: set[str] = set()
    node_uids: set[str] = set()
    failure_domains: set[str] = set()
    observed_slots: set[int] = set()
    for pod in pods:
        if not isinstance(pod, Mapping):
            raise ValueError("Controller bridge shared-mount canary Pod must be a mapping.")
        slot = pod.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in expected_groups:
            raise ValueError("Controller bridge shared-mount canary Pod slot is invalid.")
        if slot in observed_slots:
            raise ValueError("Controller bridge shared-mount canary Pod slots must be unique.")
        observed_slots.add(slot)
        group = expected_groups[slot]
        scheduling_domain = _required_mapping(
            group.get("scheduling_failure_domain"),
            field="bridge scheduling failure domain",
        )
        values = {
            field: _required_text(
                pod.get(field),
                field=f"bridge shared-mount canary Pod {field}",
            )
            for field in (
                "pod_name",
                "pod_uid",
                "node_name",
                "node_uid",
                "node_group_id",
                "failure_domain",
            )
        }
        runtime_binding = {
            field: str(scheduling_domain.get(field, "") or "")
            for field in ("topology_value", "node_name", "node_uid")
        }
        missing_runtime_binding = not any(runtime_binding.values())
        if any(runtime_binding.values()) != all(runtime_binding.values()):
            raise ValueError(
                "Controller bridge scheduling-domain runtime binding is partially populated."
            )
        if (
            values["node_group_id"] != str(group.get("id", "") or "")
            or values["failure_domain"] != str(group.get("id", "") or "")
            or (missing_runtime_binding and not _scheduling_domain_repair_window(journal))
            or (
                not missing_runtime_binding
                and (
                    values["node_name"] != runtime_binding["node_name"]
                    or values["node_uid"] != runtime_binding["node_uid"]
                    or values["failure_domain"] != runtime_binding["topology_value"]
                )
            )
        ):
            raise ValueError(
                "Controller bridge shared-mount canary Pod binding differs from its "
                "journaled node group and scheduling domain."
            )
        pod_uids.add(values["pod_uid"])
        node_uids.add(values["node_uid"])
        failure_domains.add(values["failure_domain"])
    if (
        observed_slots != {0, 1}
        or len(pod_uids) != 2
        or len(node_uids) != 2
        or len(failure_domains) != 2
    ):
        raise ValueError(
            "Controller bridge shared-mount canary requires distinct Pods, Nodes, and "
            "immutable node-group scheduling domains."
        )

    storage = _required_mapping(
        canary.get("storage"),
        field="bridge shared-mount canary storage",
    )
    kubernetes_resources = journal.get("kubernetes_resources")
    if not isinstance(kubernetes_resources, Sequence) or isinstance(
        kubernetes_resources, (str, bytes, bytearray)
    ):
        raise ValueError("Controller bridge shared-mount canary lacks Kubernetes resources.")
    pv_records = [
        item
        for item in kubernetes_resources
        if isinstance(item, Mapping) and item.get("kind") == "PersistentVolume"
    ]
    pvc_records = [
        item
        for item in kubernetes_resources
        if isinstance(item, Mapping)
        and item.get("kind") == "PersistentVolumeClaim"
        and item.get("namespace") == journal.get("namespace")
    ]
    if len(pv_records) != 2 or len(pvc_records) != 2:
        raise ValueError(
            "Controller bridge shared-mount canary requires exact state and Jail PV/PVC bindings."
        )
    pv_by_name = {str(item.get("name", "") or ""): item for item in pv_records}
    pvc_by_name = {str(item.get("name", "") or ""): item for item in pvc_records}

    def _expected_storage_binding(claim_name: str) -> dict[str, str]:
        pv = pv_by_name.get(f"{claim_name}-pv", {})
        pvc = pvc_by_name.get(claim_name, {})
        return {
            "pv_name": str(pv.get("name", "") or ""),
            "pv_uid": str(pv.get("uid", "") or ""),
            "pvc_name": str(pvc.get("name", "") or ""),
            "pvc_uid": str(pvc.get("uid", "") or ""),
        }

    expected_storage = {
        "controller_spool": _expected_storage_binding(CONTROLLER_BRIDGE_STATE_PVC),
        "jail": _expected_storage_binding(CONTROLLER_BRIDGE_JAIL_PVC),
    }
    if dict(storage) != expected_storage or any(
        not all(binding.values()) for binding in expected_storage.values()
    ):
        raise ValueError("Controller bridge shared-mount canary PV/PVC identity binding differs.")


def pre_source_mutation_mount_canary(journal: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mount_canary_for_purpose(
        journal,
        purpose=CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
        boundary="substrate",
    )


def pre_source_fence_mount_canary(journal: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mount_canary_for_purpose(
        journal,
        purpose=CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY,
        boundary="source fence",
    )


def _mount_canary_for_purpose(
    journal: Mapping[str, Any],
    *,
    purpose: str,
    boundary: str,
) -> Mapping[str, Any]:
    records = journal.get("shared_mount_canaries")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise ValueError("Controller bridge shared_mount_canaries must be a list.")
    matches = [
        item for item in records if isinstance(item, Mapping) and item.get("purpose") == purpose
    ]
    if len(matches) != 1:
        raise ValueError(f"Controller bridge {boundary} requires one {purpose} shared-SFS canary.")
    _validate_shared_mount_canary(journal, matches[0])
    return matches[0]


def _validate_pre_source_fence_boundary(
    journal: Mapping[str, Any],
    *,
    stage: str,
) -> None:
    readiness = journal.get("pre_source_fence_readiness")
    intent = journal.get("source_fence_intent")
    if not isinstance(readiness, Mapping) or not isinstance(intent, Mapping):
        raise ValueError("Controller bridge pre-source-fence records must be mappings.")
    required = _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_FENCED.value]
    if not readiness:
        if required:
            raise ValueError("Controller bridge source fence lacks fresh readiness proof.")
        if intent:
            raise ValueError("Controller bridge source fence intent lacks readiness proof.")
        return
    if (
        readiness.get("schema") != "nebius-cxcli-controller-bridge-pre-source-fence-readiness/v1"
        or readiness.get("status") != "verified"
        or readiness.get("source_replicas") != 1
    ):
        raise ValueError("Controller bridge pre-source-fence readiness proof is invalid.")
    semantic_sha256 = _sha256(
        readiness.get("semantic_sha256"),
        field="bridge pre-source-fence readiness semantic sha256",
    )
    if (
        _journal_payload_fingerprint(
            {key: value for key, value in readiness.items() if key != "semantic_sha256"}
        )
        != semantic_sha256
    ):
        raise ValueError("Controller bridge pre-source-fence readiness fingerprint differs.")
    for field in (
        "authority_epoch",
        "source_workload_uid",
        "source_workload_resource_version",
        "source_pod_uid",
        "verified_at",
    ):
        _required_text(
            readiness.get(field),
            field=f"bridge pre-source-fence readiness {field}",
        )
    source = _required_mapping(journal.get("source_binding"), field="bridge source_binding")
    if readiness.get("source_workload_uid") != source.get(
        "controller_workload_uid"
    ) or readiness.get("source_pod_uid") != source.get("controller_pod_uid"):
        raise ValueError("Controller bridge pre-source-fence source identity differs.")
    canary = pre_source_fence_mount_canary(journal)
    canary_sha256 = _sha256(
        readiness.get("canary_sha256"),
        field="bridge pre-source-fence canary sha256",
    )
    if _journal_payload_fingerprint(canary) != canary_sha256:
        raise ValueError("Controller bridge pre-source-fence canary binding differs.")
    node_group_proofs = readiness.get("node_groups")
    if (
        not isinstance(node_group_proofs, Sequence)
        or isinstance(node_group_proofs, (str, bytes, bytearray))
        or len(node_group_proofs) != 2
    ):
        raise ValueError("Controller bridge pre-source-fence requires two node-group proofs.")
    if _journal_payload_fingerprint(node_group_proofs) != _sha256(
        readiness.get("node_groups_sha256"),
        field="bridge pre-source-fence node-groups sha256",
    ):
        raise ValueError("Controller bridge pre-source-fence node-group proof differs.")
    journal_groups = journal.get("node_groups")
    if (
        not isinstance(journal_groups, Sequence)
        or isinstance(journal_groups, (str, bytes, bytearray))
        or len(journal_groups) != 2
    ):
        raise ValueError("Controller bridge pre-source-fence journal groups are invalid.")
    for slot, (proof, group) in enumerate(zip(node_group_proofs, journal_groups, strict=True)):
        if not isinstance(proof, Mapping) or not isinstance(group, Mapping):
            raise ValueError("Controller bridge pre-source-fence node-group proof is invalid.")
        domain = _required_mapping(
            group.get("scheduling_failure_domain"),
            field="bridge scheduling failure domain",
        )
        resource_version = proof.get("resource_version")
        if (
            proof.get("slot") != slot
            or proof.get("id") != group.get("id")
            or proof.get("name") != group.get("name")
            or proof.get("controller_spool_attachment_sha256")
            != group.get("controller_spool_attachment_sha256")
            or proof.get("jail_attachment_sha256") != group.get("jail_attachment_sha256")
            or proof.get("node_name") != domain.get("node_name")
            or proof.get("node_uid") != domain.get("node_uid")
            or proof.get("failure_domain") != domain.get("topology_value")
            or isinstance(resource_version, bool)
            or not isinstance(resource_version, int)
            or resource_version <= 0
        ):
            raise ValueError(
                "Controller bridge pre-source-fence node-group identity or attachment differs."
            )

    if not intent:
        if required:
            raise ValueError("Controller bridge source fence lacks a readiness-bound intent.")
        return
    if intent.get("schema") != "nebius-cxcli-controller-bridge-source-fence-intent/v1":
        raise ValueError("Controller bridge source fence intent schema mismatch.")
    intent_state = str(intent.get("state", "") or "")
    if intent_state not in {"intent-recorded", "dispatching", "accepted"}:
        raise ValueError("Controller bridge source fence intent state is invalid.")
    for field in (
        "authority_epoch",
        "readiness_sha256",
        "canary_sha256",
        "source_workload_uid",
        "source_pod_uid",
        "intent_at",
    ):
        _required_text(intent.get(field), field=f"bridge source fence intent {field}")
    if (
        intent.get("authority_epoch") != readiness.get("authority_epoch")
        or intent.get("readiness_sha256") != semantic_sha256
        or intent.get("canary_sha256") != canary_sha256
        or intent.get("source_workload_uid") != readiness.get("source_workload_uid")
        or intent.get("source_pod_uid") != readiness.get("source_pod_uid")
    ):
        raise ValueError("Controller bridge source fence intent binding differs.")
    if required and intent_state != "accepted":
        raise ValueError("Controller bridge completed source fence lacks accepted intent.")
    if intent_state == "dispatching":
        _required_text(
            intent.get("dispatching_at"),
            field="bridge source fence intent dispatching_at",
        )
    if intent_state == "accepted":
        _required_text(
            intent.get("accepted_at"),
            field="bridge source fence intent accepted_at",
        )


def _ordered_unique_texts(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be a list.")
    values = tuple(str(item or "").strip() for item in value)
    if not values or any(not item for item in values) or values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must be a non-empty sorted unique list.")
    return values


def _validate_jwt_material_contract(
    journal: Mapping[str, Any],
    *,
    stage: str,
) -> None:
    proof_required = (
        _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.TARGET_SINGLETON_ACTIVE.value]
    )
    preflight_required = (
        _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.BRIDGE_FENCED.value]
    )
    contract = journal.get("jwt_material_contract")
    if not isinstance(contract, Mapping) or not contract:
        if preflight_required:
            raise ValueError(
                "Target singleton stage requires the accepted source JWT material contract."
            )
        return
    if contract.get("schema") != CONTROLLER_BRIDGE_JWT_MATERIAL_CONTRACT_SCHEMA:
        raise ValueError("Controller bridge JWT material contract schema mismatch.")
    secret_names = _ordered_unique_texts(
        contract.get("secret_names"),
        field="bridge JWT material secret_names",
    )
    data_keys = _ordered_unique_texts(
        contract.get("data_keys"),
        field="bridge JWT material data_keys",
    )
    source = _required_mapping(journal.get("source_binding"), field="bridge source_binding")
    source_fingerprint = _sha256(
        contract.get("source_fingerprint"),
        field="bridge JWT material source_fingerprint",
    )
    if source_fingerprint != source.get("jwt_fingerprint"):
        raise ValueError("Controller bridge JWT material source fingerprint differs.")
    content_sha256 = _sha256(
        contract.get("content_sha256"),
        field="bridge JWT material content_sha256",
    )
    _required_text(contract.get("captured_at"), field="bridge JWT material captured_at")
    raw_bindings = contract.get("source_secret_bindings")
    if not isinstance(raw_bindings, Sequence) or isinstance(raw_bindings, (str, bytes, bytearray)):
        raise ValueError("Controller bridge JWT source Secret bindings must be a list.")
    bindings: list[dict[str, Any]] = []
    for raw_binding in raw_bindings:
        if not isinstance(raw_binding, Mapping):
            raise ValueError("Controller bridge JWT source Secret binding must be a mapping.")
        binding: dict[str, Any] = {
            key: _required_text(
                raw_binding.get(key),
                field=f"bridge JWT source Secret {key}",
            )
            for key in ("namespace", "name", "uid")
        }
        binding["data_keys"] = list(
            _ordered_unique_texts(
                raw_binding.get("data_keys"),
                field="bridge JWT source Secret data_keys",
            )
        )
        bindings.append(binding)
    if (
        tuple(binding["name"] for binding in bindings) != secret_names
        or len({binding["uid"] for binding in bindings}) != len(bindings)
        or any(binding["namespace"] != source.get("namespace") for binding in bindings)
        or tuple(sorted({key for binding in bindings for key in binding["data_keys"]})) != data_keys
    ):
        raise ValueError("Controller bridge JWT source Secret bindings differ.")

    takeover = _required_mapping(
        journal.get("target_singleton_takeover"),
        field="bridge target_singleton_takeover",
    )

    def _validate_live_binding(record: Mapping[str, Any], *, label: str) -> None:
        binding = _required_mapping(
            record.get("jwt_secret_binding"),
            field=f"{label} JWT Secret binding",
        )
        normalized_binding = {
            field: _required_text(
                binding.get(field),
                field=f"{label} JWT Secret {field}",
            )
            for field in ("namespace", "name", "uid")
        }
        data_key = _required_text(record.get("jwt_data_key"), field=f"{label} JWT data key")
        matches = [
            item
            for item in bindings
            if all(item[field] == normalized_binding[field] for field in normalized_binding)
            and data_key in item["data_keys"]
        ]
        if len(matches) != 1:
            raise ValueError(f"{label} JWT configured key is not source-bound.")
        key_path = _required_text(record.get("jwt_key_path"), field=f"{label} jwt_key path")
        if not key_path.startswith("/") or "/../" in f"{key_path}/":
            raise ValueError(f"{label} jwt_key path must be normalized and absolute.")
        secret_file_path = _required_text(
            record.get("jwt_secret_file_path"),
            field=f"{label} JWT Secret file path",
        )
        if not secret_file_path.startswith("/") or "/../" in f"{secret_file_path}/":
            raise ValueError(f"{label} JWT Secret file path must be normalized and absolute.")
        for field in (
            "controller_pod_uid",
            "controller_container_name",
            "controller_container_image",
            "controller_container_image_id",
        ):
            _required_text(record.get(field), field=f"{label} {field}")
        for field in (
            "source_wiring_sha256",
            "destination_wiring_sha256",
            "wiring_sha256",
        ):
            _sha256(record.get(field), field=f"{label} JWT {field}")
        _sha256(record.get("live_key_sha256"), field=f"{label} JWT live_key_sha256")

    preflight = takeover.get("jwt_material_preflight")
    if not isinstance(preflight, Mapping) or not preflight:
        if preflight_required:
            raise ValueError("Bridge fencing requires the live target JWT material preflight.")
    else:
        if preflight.get("schema") != CONTROLLER_BRIDGE_JWT_MATERIAL_PREFLIGHT_SCHEMA:
            raise ValueError("Target singleton JWT material preflight schema mismatch.")
        if (
            preflight.get("status") != "verified"
            or _sha256(
                preflight.get("source_fingerprint"),
                field="target JWT preflight source_fingerprint",
            )
            != source_fingerprint
            or _sha256(
                preflight.get("content_sha256"),
                field="target JWT preflight content_sha256",
            )
            != content_sha256
            or preflight.get("secret_bindings") != bindings
            or preflight.get("target_ref") != takeover.get("target_ref")
        ):
            raise ValueError("Target singleton JWT material preflight differs from its source.")
        for field in (
            "target_ref",
            "controller_workload_uid",
            "controller_workload_resource_version",
            "verified_at",
        ):
            _required_text(preflight.get(field), field=f"target JWT preflight {field}")
        _validate_live_binding(preflight, label="target JWT preflight")

    proof = takeover.get("jwt_material_proof")
    if not isinstance(proof, Mapping) or not proof:
        if proof_required:
            raise ValueError("Target singleton stage requires the live JWT material proof.")
        return
    if proof.get("schema") != CONTROLLER_BRIDGE_JWT_MATERIAL_PROOF_SCHEMA:
        raise ValueError("Target singleton JWT material proof schema mismatch.")
    if (
        proof.get("status") != "verified"
        or _sha256(
            proof.get("source_fingerprint"),
            field="target singleton JWT source_fingerprint",
        )
        != source_fingerprint
        or _sha256(
            proof.get("content_sha256"),
            field="target singleton JWT content_sha256",
        )
        != content_sha256
        or proof.get("secret_bindings") != bindings
        or proof.get("target_ref") != takeover.get("target_ref")
        or any(
            proof.get(field) != preflight.get(field)
            for field in (
                "jwt_key_path",
                "jwt_secret_binding",
                "jwt_data_key",
                "jwt_secret_file_path",
                "source_wiring_sha256",
                "destination_wiring_sha256",
                "wiring_sha256",
                "live_key_sha256",
                "controller_container_name",
                "controller_container_image",
                "controller_container_image_id",
            )
        )
        or proof.get("controller_pod_uid") != takeover.get("controller_pod_uid")
        or proof.get("controller_workload_uid") != preflight.get("controller_workload_uid")
        or proof.get("controller_workload_uid") != takeover.get("controller_workload_uid")
        or proof.get("authority_epoch")
        != _required_mapping(journal.get("authority"), field="bridge authority").get("epoch")
    ):
        raise ValueError("Target singleton JWT material proof differs from its source contract.")
    for field in (
        "target_ref",
        "controller_pod_uid",
        "controller_workload_uid",
        "authority_epoch",
        "verified_at",
    ):
        _required_text(proof.get(field), field=f"target singleton JWT material {field}")
    _required_text(
        proof.get("revalidated_at"),
        field="target singleton JWT material revalidated_at",
    )
    _validate_live_binding(proof, label="target singleton JWT proof")
    if proof.get("token_smoke_passed") is not True:
        raise ValueError("Target singleton JWT proof requires a successful scontrol token smoke.")
    _required_text(
        proof.get("token_smoke_observed_at"),
        field="target singleton JWT token smoke observed_at",
    )


def validate_bridge_journal(journal: Mapping[str, Any]) -> None:
    if journal.get("schema") != CONTROLLER_BRIDGE_SCHEMA:
        raise ValueError("Controller bridge journal schema mismatch.")
    stage = str(journal.get("stage", "") or "")
    if stage not in _BRIDGE_STAGE_INDEX:
        raise ValueError("Controller bridge stage is invalid.")
    _sha256(journal.get("campaign_fingerprint"), field="bridge campaign_fingerprint")
    _required_text(journal.get("cluster_id"), field="bridge cluster_id")
    _safe_token(journal.get("cluster_name"), field="bridge cluster_name")
    _safe_token(journal.get("namespace"), field="bridge namespace")
    security_contract = journal.get("security_contract")
    if not isinstance(security_contract, Mapping):
        raise ValueError("Controller bridge security_contract must be a mapping.")
    if security_contract:
        if security_contract.get("schema") != "nebius-cxcli-controller-security/v1":
            raise ValueError("Controller bridge security contract schema mismatch.")
        expected_security_sha256 = _sha256(
            security_contract.get("semantic_sha256"),
            field="bridge security semantic_sha256",
        )
        semantic_payload = {
            str(key): value for key, value in security_contract.items() if key != "semantic_sha256"
        }
        if _journal_payload_fingerprint(semantic_payload) != expected_security_sha256:
            raise ValueError("Controller bridge security contract semantic fingerprint differs.")
        api_cidrs = security_contract.get("kubernetes_api_cidrs")
        if (
            not isinstance(api_cidrs, Sequence)
            or isinstance(api_cidrs, (str, bytes, bytearray))
            or not api_cidrs
        ):
            raise ValueError("Controller bridge security contract requires API CIDRs.")
        for value in api_cidrs:
            try:
                network = ipaddress.ip_network(str(value), strict=True)
            except ValueError as exc:
                raise ValueError("Controller bridge security API CIDR is invalid.") from exc
            if network.prefixlen == 0 or network.is_link_local or network.is_multicast:
                raise ValueError("Controller bridge security API CIDR is overly broad or unsafe.")
        soperator_namespace = security_contract.get("soperator_namespace")
        if not isinstance(soperator_namespace, Mapping):
            raise ValueError("Controller bridge security Soperator namespace is missing.")
        _safe_token(
            soperator_namespace.get("name"),
            field="bridge security Soperator namespace name",
        )
        _required_text(
            soperator_namespace.get("uid"),
            field="bridge security Soperator namespace uid",
        )
        api_service = security_contract.get("kubernetes_api_service")
        if not isinstance(api_service, Mapping):
            raise ValueError("Controller bridge Kubernetes API service binding is missing.")
        for field in ("namespace", "name", "uid"):
            _required_text(
                api_service.get(field),
                field=f"bridge Kubernetes API service {field}",
            )
        inspector = security_contract.get("inspector")
        if not isinstance(inspector, Mapping):
            raise ValueError("Controller bridge inspector security binding is missing.")
        if inspector.get("namespace") != "cxcli-soperator-upgrade-inspectors":
            raise ValueError("Controller bridge inspector namespace is not dedicated.")
        _required_text(inspector.get("namespace_uid"), field="bridge inspector namespace uid")
        _sha256(inspector.get("resources_sha256"), field="bridge inspector resources sha256")
        admission_nodes = inspector.get("admission_nodes")
        if (
            not isinstance(admission_nodes, Sequence)
            or isinstance(admission_nodes, (str, bytes, bytearray))
            or not admission_nodes
        ):
            raise ValueError("Controller bridge inspector admission preflight has no Nodes.")
        for node in admission_nodes:
            if not isinstance(node, Mapping):
                raise ValueError("Controller bridge inspector Node binding must be a mapping.")
            for field in ("node_name", "node_uid", "provider_id", "system_uuid"):
                _required_text(node.get(field), field=f"bridge inspector Node {field}")
        _sha256(
            inspector.get("admission_nodes_sha256"),
            field="bridge inspector admission Nodes sha256",
        )
        _required_text(
            inspector.get("server_dry_run_at"),
            field="bridge inspector server dry-run timestamp",
        )
        bridge_security = security_contract.get("bridge")
        if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SUBSTRATE_READY.value]:
            if not isinstance(bridge_security, Mapping):
                raise ValueError("Controller bridge live security binding is missing.")
            if bridge_security.get("namespace") != journal.get("namespace"):
                raise ValueError("Controller bridge security namespace binding differs.")
            _required_text(
                bridge_security.get("namespace_uid"),
                field="bridge security namespace uid",
            )
            if bridge_security.get("pod_security_enforce") not in {"baseline", "privileged"}:
                raise ValueError("Controller bridge Pod Security enforcement is invalid.")
            _sha256(
                bridge_security.get("network_policies_sha256"),
                field="bridge NetworkPolicy semantic sha256",
            )
            _sha256(
                bridge_security.get("workload_security_sha256"),
                field="bridge workload security sha256",
            )
    elif _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SUBSTRATE_READY.value]:
        raise ValueError("Controller bridge substrate requires its immutable security contract.")

    source = journal.get("source_binding")
    if not isinstance(source, Mapping):
        raise ValueError("Controller bridge source_binding must be a mapping.")
    source_fields = tuple(BridgeSourceBinding.__dataclass_fields__)
    for field in source_fields:
        _required_text(source.get(field), field=f"bridge source {field}")
    for field in ("configuration_fingerprint", "munge_fingerprint", "jwt_fingerprint"):
        _sha256(source.get(field), field=f"bridge source {field}")

    node_groups = journal.get("node_groups")
    if (
        not isinstance(node_groups, Sequence)
        or isinstance(node_groups, (str, bytes, bytearray))
        or len(node_groups) != 2
    ):
        raise ValueError("Controller bridge requires exactly two node-group records.")
    names: set[str] = set()
    ids: set[str] = set()
    scheduling_domains: set[str] = set()
    attachment_digests: set[str] = set()
    jail_attachment_digests: set[str] = set()
    adapter_ownerships: set[str] = set()
    for index, group in enumerate(node_groups):
        if not isinstance(group, Mapping) or group.get("slot") != index:
            raise ValueError("Controller bridge node-group slots must be ordered 0 and 1.")
        names.add(_safe_token(group.get("name"), field="bridge node-group name"))
        scheduling_domain = group.get("scheduling_failure_domain")
        if (
            not isinstance(scheduling_domain, Mapping)
            or scheduling_domain.get("topology_key") != "nebius.com/node-group-id"
        ):
            raise ValueError(
                "Controller bridge scheduling failure domain must use the immutable "
                "Nebius node-group-id topology key."
            )
        ownership = _required_text(
            group.get("ownership"),
            field="bridge placement-domain ownership",
        )
        adapter_ownerships.add(ownership)
        expected_policy = {
            "managed-existing": (
                "kubernetes-only",
                "preserve-domain",
                "reuse-chart-mount-substrate",
            ),
            "external-temporary": (
                "provider-create-delete",
                "delete-domain",
                "cxcli-mount-daemonset",
            ),
        }.get(ownership)
        if (
            expected_policy is None
            or (
                group.get("mutation_policy"),
                group.get("cleanup_policy"),
                group.get("mount_policy"),
            )
            != expected_policy
        ):
            raise ValueError("Controller bridge placement-domain adapter policy is invalid.")
        ready_capacity = group.get("ready_capacity")
        if (
            isinstance(ready_capacity, bool)
            or not isinstance(ready_capacity, int)
            or ready_capacity < 1
            or group.get("excluded_from_provider_upgrade") is not True
        ):
            raise ValueError(
                "Controller bridge placement domains require positive Ready capacity and "
                "provider-upgrade exclusion."
            )
        attachment_digests.add(
            _sha256(
                group.get("controller_spool_attachment_sha256"),
                field="bridge node-group controller-spool attachment sha256",
            )
        )
        jail_attachment_digests.add(
            _sha256(
                group.get("jail_attachment_sha256"),
                field="bridge node-group Jail attachment sha256",
            )
        )
        group_id = str(group.get("id", "") or "").strip()
        domain_group_id = str(scheduling_domain.get("node_group_id", "") or "").strip()
        if domain_group_id != group_id:
            raise ValueError(
                "Controller bridge scheduling failure-domain value must match its "
                "immutable node-group ID."
            )
        topology_value = str(scheduling_domain.get("topology_value", "") or "").strip()
        if topology_value and (
            topology_value != group_id
            or not str(scheduling_domain.get("node_name", "") or "").strip()
            or not str(scheduling_domain.get("node_uid", "") or "").strip()
        ):
            raise ValueError(
                "Controller bridge scheduling domain must bind its exact immutable node-group "
                "ID, node, and Node UID."
            )
        if topology_value:
            scheduling_domains.add(topology_value)
        if group_id:
            ids.add(group_id)
    if len(names) != 2:
        raise ValueError("Controller bridge node-group names must be distinct.")
    if len(adapter_ownerships) != 1:
        raise ValueError("Controller bridge placement domains must use one capacity adapter.")
    if len(attachment_digests) != 1:
        raise ValueError(
            "Controller bridge node groups must bind one identical controller-spool "
            "attachment fingerprint."
        )
    if len(jail_attachment_digests) != 1:
        raise ValueError(
            "Controller bridge node groups must bind one identical Jail attachment fingerprint."
        )
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SUBSTRATE_READY.value]:
        if len(ids) != 2:
            raise ValueError("Controller bridge substrate requires two distinct node groups.")
        ownership = next(iter(adapter_ownerships))
        if ownership == "external-temporary" and any(
            group.get("created") is not True for group in node_groups
        ):
            raise ValueError(
                "External controller bridge substrate requires two cxcli-created node groups."
            )
        if ownership == "managed-existing" and any(
            group.get("bound") is not True or not str(group.get("live_node_uid", "") or "").strip()
            for group in node_groups
        ):
            raise ValueError(
                "Managed controller bridge substrate requires two exact existing domain "
                "bindings and live Node UIDs."
            )
    if (
        scheduling_domains
        and len(scheduling_domains) != 2
        and not _scheduling_domain_repair_window(journal)
    ):
        raise ValueError("Controller bridge immutable node-group domains must be distinct.")
    if (
        _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_HA_ACTIVE.value]
        and len(scheduling_domains) != 2
    ):
        raise ValueError(
            "Controller bridge writer stages require two proven immutable node-group domains."
        )

    authority = journal.get("authority")
    if not isinstance(authority, Mapping):
        raise ValueError("Controller bridge authority must be a mapping.")
    _safe_token(authority.get("epoch"), field="bridge authority epoch")
    owner = str(authority.get("owner", "") or "")
    if owner not in _BRIDGE_AUTHORITY_OWNERS:
        raise ValueError("Controller bridge authority owner is invalid.")
    history = authority.get("history")
    if (
        not isinstance(history, Sequence)
        or isinstance(history, (str, bytes, bytearray))
        or not history
    ):
        raise ValueError("Controller bridge authority history must be non-empty.")
    for item in history:
        if not isinstance(item, Mapping):
            raise ValueError("Controller bridge authority history entry must be a mapping.")
        _safe_token(item.get("epoch"), field="bridge authority history epoch")
        if item.get("owner") not in _BRIDGE_AUTHORITY_OWNERS:
            raise ValueError("Controller bridge authority history owner is invalid.")
        _required_text(item.get("at"), field="bridge authority history timestamp")
    first_bridge_write_at = str(authority.get("first_bridge_write_at", "") or "").strip()
    if first_bridge_write_at and (
        authority.get("source_restart_prohibited") is not True or owner == "source-singleton"
    ):
        raise ValueError("Controller bridge first write permanently prohibits source restart.")

    authority_lease = journal.get("authority_lease")
    if not isinstance(authority_lease, Mapping):
        raise ValueError("Controller bridge authority_lease must be a mapping.")
    _required_text(
        authority_lease.get("holder_identity"),
        field="bridge authority Lease holder_identity",
    )
    authority_lease_transitions = journal.get("authority_lease_transitions")
    if not isinstance(authority_lease_transitions, Sequence) or isinstance(
        authority_lease_transitions, (str, bytes, bytearray)
    ):
        raise ValueError("Controller bridge authority_lease_transitions must be a list.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SUBSTRATE_READY.value]:
        _required_text(authority_lease.get("uid"), field="bridge authority Lease uid")
        resource_version = _required_text(
            authority_lease.get("resource_version"),
            field="bridge authority Lease resource_version",
        )
        if not resource_version.isdigit() or int(resource_version) <= 0:
            raise ValueError("Controller bridge authority Lease resourceVersion is invalid.")
    for transition_record in authority_lease_transitions:
        if not isinstance(transition_record, Mapping):
            raise ValueError("Controller bridge authority Lease transition must be a mapping.")
        for field in ("from_holder", "to_holder", "intent_at"):
            _required_text(
                transition_record.get(field),
                field=f"bridge authority Lease transition {field}",
            )
        transition_state = str(transition_record.get("state", "") or "")
        if transition_state not in {"intent-recorded", "dispatching", "accepted"}:
            raise ValueError("Controller bridge authority Lease transition state is invalid.")
        if transition_state == "accepted":
            _required_text(
                transition_record.get("accepted_at"),
                field="bridge authority Lease transition accepted_at",
            )

    runtime_fence_proofs = journal.get("runtime_fence_proofs")
    if not isinstance(runtime_fence_proofs, Sequence) or isinstance(
        runtime_fence_proofs, (str, bytes, bytearray)
    ):
        raise ValueError("Controller bridge runtime_fence_proofs must be a list.")
    for runtime_proof in runtime_fence_proofs:
        if not isinstance(runtime_proof, Mapping):
            raise ValueError("Controller bridge runtime-fence proof must be a mapping.")
        _required_text(runtime_proof.get("boundary"), field="bridge runtime-fence boundary")
        _image_digest_reference(
            runtime_proof.get("image"),
            field="bridge runtime-fence image",
        )
        targets = runtime_proof.get("targets")
        if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)):
            raise ValueError("Controller bridge runtime-fence targets must be a list.")
        if runtime_proof.get("status") == "verified":
            _validate_runtime_fence_evidence(
                runtime_proof.get("results"),
                field="bridge runtime-fence results",
                expected_count=len(targets),
            )

    fencing = journal.get("fencing")
    if not isinstance(fencing, Mapping):
        raise ValueError("Controller bridge fencing must be a mapping.")
    source_fencing = fencing.get("source")
    bridge_fencing = fencing.get("bridge")
    if not isinstance(source_fencing, Mapping) or not isinstance(bridge_fencing, Mapping):
        raise ValueError("Controller bridge source and bridge fencing records are required.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_FENCED.value] and (
        not all(
            source_fencing.get(field) is True
            for field in ("proven", "process_absent", "writable_mount_absent")
        )
        or not str(source_fencing.get("observed_at", "") or "").strip()
    ):
        raise ValueError("Controller bridge source fencing proof is incomplete.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_FENCED.value]:
        _validate_runtime_fence_evidence(
            source_fencing.get("runtime_evidence"),
            field="bridge source runtime fence",
            expected_count=1,
        )
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.BRIDGE_FENCED.value] and (
        bridge_fencing.get("proven") is not True
        or bridge_fencing.get("processes_absent") is not True
        or bridge_fencing.get("writable_state_mounts_absent") is not True
        or not str(bridge_fencing.get("observed_at", "") or "").strip()
    ):
        raise ValueError("Controller bridge fencing proof is incomplete.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.BRIDGE_FENCED.value]:
        _validate_runtime_fence_evidence(
            bridge_fencing.get("runtime_fence"),
            field="bridge target-version runtime fence",
            expected_count=2,
        )

    manifest = journal.get("state_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("Controller bridge state_manifest must be a mapping.")
    state_precopy = journal.get("state_precopy")
    if not isinstance(state_precopy, Mapping):
        raise ValueError("Controller bridge state_precopy must be a mapping.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_PRECOPIED.value]:
        for field in (
            "source_state_save_location",
            "archive_path",
            "incremental_snapshot_path",
            "remote_path",
            "completed_at",
        ):
            _required_text(state_precopy.get(field), field=f"bridge state_precopy {field}")
        for field in (
            "archive_sha256",
            "incremental_snapshot_sha256",
            "remote_manifest_sha256",
        ):
            _sha256(state_precopy.get(field), field=f"bridge state_precopy {field}")
        if state_precopy.get("gnu_incremental") is not True:
            raise ValueError("Controller bridge pre-copy must use GNU incremental tar state.")
    cold_reader = journal.get("cold_reader")
    if not isinstance(cold_reader, Mapping):
        raise ValueError("Controller bridge cold_reader must be a mapping.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.STATE_PROMOTED.value]:
        _required_text(manifest.get("epoch_directory"), field="bridge state epoch_directory")
        _required_text(manifest.get("stable_path"), field="bridge state stable_path")
        _sha256(manifest.get("sha256"), field="bridge state manifest sha256")
        if manifest.get("fsynced") is not True or manifest.get("promoted") is not True:
            raise ValueError("Controller bridge state promotion must be fsynced and atomic.")
        for field in (
            "pod_name",
            "pod_uid",
            "source_pvc_name",
            "source_pvc_uid",
            "source_node_name",
            "source_node_uid",
            "source_image",
            "intent_at",
            "absent_at",
        ):
            _required_text(cold_reader.get(field), field=f"bridge cold_reader {field}")
        for field in (
            "delta_archive_sha256",
            "incremental_snapshot_sha256",
            "source_manifest_sha256",
        ):
            _sha256(cold_reader.get(field), field=f"bridge cold_reader {field}")
        _image_digest_reference(
            cold_reader.get("source_image"),
            field="bridge cold_reader source_image",
        )
        if cold_reader.get("status") != "absent" or cold_reader.get("mount_absent") is not True:
            raise ValueError(
                "Controller bridge promoted state requires the exact cold reader and source "
                "PVC mount to be absent."
            )

    roles = journal.get("controller_roles")
    if not isinstance(roles, Sequence) or isinstance(roles, (str, bytes, bytearray)):
        raise ValueError("Controller bridge controller_roles must be a list.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_HA_ACTIVE.value]:
        if authority.get("source_restart_prohibited") is not True or not first_bridge_write_at:
            raise ValueError(
                "Active controller bridge requires an accepted writer scale and permanent "
                "source restart fence."
            )
        writer_scale = authority.get("writer_scale")
        if not isinstance(writer_scale, Mapping) or writer_scale.get("state") != "accepted":
            raise ValueError(
                "Active controller bridge requires a journaled accepted 0 to 2 writer scale."
            )
        _required_text(
            writer_scale.get("statefulset_uid"),
            field="bridge writer scale statefulset_uid",
        )
        _required_text(
            writer_scale.get("accepted_at"),
            field="bridge writer scale accepted_at",
        )
    if stage in {BridgeStage.SOURCE_HA_ACTIVE.value, BridgeStage.TARGET_HA_ACTIVE.value}:
        role_records = [item for item in roles if isinstance(item, Mapping)]
        role_values = [str(item.get("role", "") or "") for item in role_records]
        if sorted(role_values) != ["active", "standby"]:
            raise ValueError("Controller bridge must prove exactly one active and one standby.")
        for field in ("pod_uid", "node_uid", "node_group_id"):
            values = {
                _required_text(item.get(field), field=f"bridge controller role {field}")
                for item in role_records
            }
            if len(values) != 2:
                raise ValueError(
                    f"Controller bridge controllers require two distinct {field} values."
                )
        journaled_group_ids = {
            str(item.get("id", "") or "") for item in node_groups if isinstance(item, Mapping)
        }
        role_group_ids = {str(item.get("node_group_id", "") or "") for item in role_records}
        if role_group_ids != journaled_group_ids:
            raise ValueError(
                "Controller bridge controller roles must bind the two journaled node groups."
            )
        if len({str(item.get("node_uid", "") or "") for item in role_records}) != 2:
            raise ValueError("Controller bridge controllers must run on distinct nodes.")

    transition = journal.get("version_transition")
    if not isinstance(transition, Mapping):
        raise ValueError("Controller bridge version_transition must be a mapping.")
    for field in ("source_image", "source_version", "target_image", "target_version"):
        _required_text(transition.get(field), field=f"bridge version_transition {field}")
    backup_operation = transition.get("backup_operation")
    backup_recovery = transition.get("backup_recovery")
    if not isinstance(backup_operation, Mapping) or not isinstance(backup_recovery, Mapping):
        raise ValueError("Controller bridge backup operation and recovery must be mappings.")
    if backup_operation:
        backup_state = str(backup_operation.get("state", "") or "")
        if backup_state not in {"intent-recorded", "dispatching", "accepted"}:
            raise ValueError("Controller bridge cold-backup operation state is invalid.")
        operation_id = _required_text(
            backup_operation.get("token"), field="bridge cold-backup operation id"
        )
        if len(operation_id) != 32 or any(
            character not in "0123456789abcdef" for character in operation_id
        ):
            raise ValueError("Controller bridge cold-backup operation id is invalid.")
        for field in ("backup_epoch", "intent_at"):
            _required_text(
                backup_operation.get(field),
                field=f"bridge cold-backup operation {field}",
            )
        if backup_operation.get("expected_preimage_sha256"):
            _sha256(
                backup_operation.get("expected_preimage_sha256"),
                field="bridge cold-backup preimage sha256",
            )
            preimage_entries = backup_operation.get("expected_preimage_entries")
            if (
                not isinstance(preimage_entries, int)
                or isinstance(preimage_entries, bool)
                or preimage_entries <= 0
            ):
                raise ValueError("Controller bridge cold-backup preimage must be non-empty.")
        if backup_state == "accepted":
            _required_text(
                backup_operation.get("accepted_at"),
                field="bridge cold-backup operation accepted_at",
            )
    if backup_recovery:
        recovery_state = str(backup_recovery.get("state", "") or "")
        if recovery_state not in {"intent-recorded", "dispatching", "accepted"}:
            raise ValueError("Controller bridge cold-backup recovery state is invalid.")
        _sha256(
            backup_recovery.get("expected_manifest_sha256"),
            field="bridge cold-backup recovery manifest sha256",
        )
        recovery_entries = backup_recovery.get("expected_entry_count")
        if (
            not isinstance(recovery_entries, int)
            or isinstance(recovery_entries, bool)
            or recovery_entries <= 0
        ):
            raise ValueError("Controller bridge cold-backup recovery entry count is invalid.")
        for field in ("token", "restore_epoch", "restore_path", "intent_at"):
            _required_text(
                backup_recovery.get(field),
                field=f"bridge cold-backup recovery {field}",
            )
        if recovery_state == "accepted":
            _required_text(
                backup_recovery.get("accepted_at"),
                field="bridge cold-backup recovery accepted_at",
            )
    if str(transition.get("target_write_at", "") or "").strip():
        if transition.get("downgrade_prohibited") is not True:
            raise ValueError("Controller bridge target-version write must prohibit downgrade.")
        _sha256(transition.get("backup_sha256"), field="bridge version backup_sha256")
        if (
            not isinstance(transition.get("backup_entry_count"), int)
            or int(transition.get("backup_entry_count", 0)) <= 0
            or transition.get("backup_cold_after_runtime_fence") is not True
        ):
            raise ValueError(
                "Controller bridge target write requires a non-empty cold runtime-fenced backup."
            )
        if backup_operation.get("state") != "accepted":
            raise ValueError(
                "Controller bridge target write requires an accepted crash-resumable backup."
            )
        _validate_runtime_fence_evidence(
            transition.get("source_runtime_fence"),
            field="bridge source-version HA cold-stop runtime fence",
            expected_count=2,
        )
        _required_text(
            transition.get("both_stopped_at"),
            field="bridge version both_stopped_at",
        )
        target_writer_scale = transition.get("target_writer_scale")
        if (
            not isinstance(target_writer_scale, Mapping)
            or target_writer_scale.get("state") != "accepted"
        ):
            raise ValueError(
                "Controller bridge target write requires an accepted 0 to 2 writer scale."
            )
        _required_text(
            target_writer_scale.get("statefulset_uid"),
            field="bridge target writer scale statefulset_uid",
        )
        _required_text(
            target_writer_scale.get("accepted_at"),
            field="bridge target writer scale accepted_at",
        )
        _sha256(
            transition.get("target_config_sha256"),
            field="bridge target configuration sha256",
        )
        if (
            stage
            in {
                BridgeStage.SOURCE_HA_ACTIVE.value,
                BridgeStage.TARGET_HA_ACTIVE.value,
            }
            and authority.get("owner") != "bridge-target"
        ):
            raise ValueError("Target-version bridge write requires bridge-target authority.")
    if (
        _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.TARGET_HA_ACTIVE.value]
        and not str(transition.get("target_write_at", "") or "").strip()
    ):
        raise ValueError("Target-version bridge stage requires an accepted target write.")

    target_image_lock = journal.get("target_image_lock")
    if not isinstance(target_image_lock, Mapping):
        raise ValueError("Controller bridge target_image_lock must be a mapping.")
    _image_digest_reference(
        target_image_lock.get("immutable_reference"),
        field="bridge target image immutable_reference",
    )
    for field in ("index_digest", "platform_digest"):
        digest = _required_text(
            target_image_lock.get(field),
            field=f"bridge target image {field}",
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"bridge target image {field} must be a SHA-256 digest.")
    if target_image_lock.get("os") != "linux" or target_image_lock.get("architecture") != "amd64":
        raise ValueError("Controller bridge target image platform must be linux/amd64.")

    takeover = journal.get("target_singleton_takeover")
    if not isinstance(takeover, Mapping):
        raise ValueError("Controller bridge target_singleton_takeover must be a mapping.")
    source_configuration = journal.get("source_configuration")
    if not isinstance(source_configuration, Mapping):
        raise ValueError("Controller bridge source_configuration must be a mapping.")
    _validate_source_configuration_transition(source_configuration, stage=stage)
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_CONFIGURED.value]:
        reconfigured_at = str(source_configuration.get("reconfigured_at", "") or "").strip()
        source_ping_sha256 = str(source_configuration.get("source_ping_sha256", "") or "").strip()
        authority = _required_mapping(journal.get("authority"), field="bridge authority")
        recoverable_pre_authority_gap = (
            stage == BridgeStage.SOURCE_CONFIGURED.value
            and bool(reconfigured_at)
            and not source_ping_sha256
            and authority.get("owner") == "source-singleton"
            and not str(authority.get("first_bridge_write_at", "") or "")
            and authority.get("source_restart_prohibited") is False
        )
        if not (reconfigured_at and source_ping_sha256) and not recoverable_pre_authority_gap:
            raise ValueError(
                "Configured controller bridge requires paired source reconfigure and primary "
                "ping proof outside the exact pre-authority recovery window."
            )
    if stage == BridgeStage.PLANNED.value and source_configuration:
        raise ValueError(
            "Controller bridge source configuration cannot precede substrate readiness."
        )
    _validate_configuration_recoveries(journal)
    _validate_pre_source_fence_boundary(journal, stage=stage)
    _validate_jwt_material_contract(journal, stage=stage)
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_FENCED.value]:
        source_proof = source_configuration.get("client_propagation")
        source_contract = source_configuration.get("client_config_contract")
        source_controller_hosts = (
            source_contract.get("controller_hosts")
            if isinstance(source_contract, Mapping)
            else None
        )
        if not isinstance(source_controller_hosts, Sequence) or isinstance(
            source_controller_hosts, (str, bytes, bytearray)
        ):
            raise ValueError("bridge source client configuration controller_hosts must be a list.")
        _validate_client_propagation_proof(
            source_proof,
            field="bridge source client propagation",
            cluster_name=str(journal.get("cluster_name", "") or ""),
            controller_hosts=source_controller_hosts,
            roles=("source-login", "source-worker"),
            raised_timeouts=True,
        )
        if (
            not isinstance(source_proof, Mapping)
            or source_proof.get("proof_stage") != "before-source-fence"
        ):
            raise ValueError("Bridge source client propagation stage is invalid.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.BRIDGE_FENCED.value]:
        handoff_proof = takeover.get("client_handoff_propagation")
        _validate_client_propagation_proof(
            handoff_proof,
            field="bridge target handoff client propagation",
            cluster_name=str(journal.get("cluster_name", "") or ""),
            controller_hosts=("controller-0", *CONTROLLER_BRIDGE_CONTROLLER_HOSTS),
            roles=("target-login", "target-worker"),
        )
        if (
            not isinstance(handoff_proof, Mapping)
            or handoff_proof.get("proof_stage") != "before-target-takeover"
        ):
            raise ValueError("Bridge target handoff client propagation stage is invalid.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.HANDOFF_VALIDATED.value]:
        final_client_proof = takeover.get("final_client_propagation")
        _validate_client_propagation_proof(
            final_client_proof,
            field="bridge final singleton client propagation",
            cluster_name=str(journal.get("cluster_name", "") or ""),
            controller_hosts=("controller-0",),
            roles=("target-login", "target-worker"),
        )
        if (
            not isinstance(final_client_proof, Mapping)
            or final_client_proof.get("proof_stage") != "final-target-singleton"
        ):
            raise ValueError("Bridge final singleton client propagation stage is invalid.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.HANDOFF_VALIDATED.value] and (
        takeover.get("state_loaded") is not True
        or takeover.get("only_primary_proven") is not True
        or takeover.get("final_slurmctld_host_count") != 1
    ):
        raise ValueError("Controller bridge final singleton proof is incomplete.")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.HANDOFF_VALIDATED.value]:
        for field in (
            "controller_pod_uid",
            "controller_workload_uid",
            "final_config_map_name",
        ):
            _required_text(takeover.get(field), field=f"bridge final singleton {field}")

    for section in (
        "login_session_handoff",
        "tui_actions",
        "preservation_jobs",
        "partition_restore",
        "cleanup",
    ):
        if not isinstance(journal.get(section), Mapping):
            raise ValueError(f"Controller bridge {section} must be a mapping.")
    preservation_jobs = journal.get("preservation_jobs")
    if not isinstance(preservation_jobs, Mapping):
        raise ValueError("Controller bridge preservation_jobs must be a mapping.")
    if preservation_jobs.get("schema") != "nebius-cxcli-slurm-preservation-jobs/v1":
        raise ValueError("Controller bridge preservation_jobs schema mismatch.")
    preservation_records = preservation_jobs.get("jobs")
    completed_during_capture = preservation_jobs.get("completed_during_capture", {})
    preservation_verifications = preservation_jobs.get("verifications")
    if (
        not isinstance(preservation_records, Mapping)
        or not isinstance(completed_during_capture, Mapping)
        or not isinstance(preservation_verifications, Sequence)
        or isinstance(preservation_verifications, (str, bytes, bytearray))
    ):
        raise ValueError("Controller bridge preservation_jobs records are invalid.")
    capture_state = str(preservation_jobs.get("capture_state", "") or "")
    if capture_state not in {"", "dispatching", "complete"}:
        raise ValueError("Controller bridge preservation_jobs capture state is invalid.")
    if (preservation_records or completed_during_capture) and capture_state == "":
        raise ValueError("Controller bridge preservation_jobs records lack capture intent.")
    if capture_state == "complete" and not str(
        preservation_jobs.get("captured_at", "") or ""
    ).strip():
        raise ValueError("Controller bridge preservation_jobs lacks captured_at.")
    verification_stages: set[str] = set()
    for job_id, record in preservation_records.items():
        _required_text(job_id, field="bridge preservation job id")
        if not isinstance(record, Mapping):
            raise ValueError("Controller bridge preservation job record must be a mapping.")
        if record.get("schema") != "nebius-cxcli-slurm-preservation-jobs/v1":
            raise ValueError("Controller bridge preservation job record schema mismatch.")
        for field in ("binding", "observation", "allocation"):
            if not isinstance(record.get(field), Mapping):
                raise ValueError(
                    f"Controller bridge preservation job record {field} must be a mapping."
                )
        _required_text(record.get("captured_at"), field="bridge preservation job captured_at")
    if set(preservation_records) & set(completed_during_capture):
        raise ValueError("Controller bridge preservation and capture-completion job ids overlap.")
    for job_id, record in completed_during_capture.items():
        _required_text(job_id, field="bridge capture-completion job id")
        if not isinstance(record, Mapping):
            raise ValueError("Controller bridge capture-completion record must be a mapping.")
        if record.get("schema") != "nebius-cxcli-slurm-preservation-jobs/v1":
            raise ValueError("Controller bridge capture-completion record schema mismatch.")
        if not isinstance(record.get("accounting"), Mapping):
            raise ValueError("Controller bridge capture-completion accounting is invalid.")
        _required_text(
            record.get("accounting_sha256"),
            field="bridge capture-completion accounting sha256",
        )
        _required_text(record.get("observed_at"), field="bridge capture-completion observed_at")
    for verification in preservation_verifications:
        if not isinstance(verification, Mapping):
            raise ValueError("Controller bridge preservation verification must be a mapping.")
        proof_stage = _required_text(
            verification.get("stage"), field="bridge preservation verification stage"
        )
        if proof_stage in verification_stages:
            raise ValueError("Controller bridge preservation verification stage is duplicated.")
        verification_stages.add(proof_stage)
        if verification.get("status") != "verified" or not isinstance(
            verification.get("jobs"), Mapping
        ):
            raise ValueError("Controller bridge preservation verification is incomplete.")
        _required_text(
            verification.get("verified_at"),
            field="bridge preservation verification timestamp",
        )
    required_preservation_stages: set[str] = set()
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SOURCE_HA_ACTIVE.value]:
        required_preservation_stages.add("source-version-bridge-active")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.TARGET_HA_ACTIVE.value]:
        required_preservation_stages.add("target-version-bridge-active")
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.HANDOFF_VALIDATED.value]:
        required_preservation_stages.add("target-singleton-active")
    if required_preservation_stages and (
        preservation_jobs.get("capture_state") != "complete"
        or not str(preservation_jobs.get("captured_at", "") or "").strip()
        or not required_preservation_stages.issubset(verification_stages)
    ):
        raise ValueError("Controller bridge stage lacks its running-job preservation proof.")
    kubernetes_resources = journal.get("kubernetes_resources")
    if not isinstance(kubernetes_resources, Sequence) or isinstance(
        kubernetes_resources, (str, bytes, bytearray)
    ):
        raise ValueError("Controller bridge kubernetes_resources must be a list.")
    resource_keys: set[tuple[str, str, str, str]] = set()
    for resource in kubernetes_resources:
        if not isinstance(resource, Mapping):
            raise ValueError("Controller bridge Kubernetes resource identity must be a mapping.")
        identity = tuple(
            _required_text(
                resource.get(field),
                field=f"bridge Kubernetes resource {field}",
            )
            for field in ("api_version", "kind", "name", "uid")
        )
        namespace = str(resource.get("namespace", "") or "").strip()
        resource_version = _required_text(
            resource.get("resource_version"),
            field="bridge Kubernetes resource resource_version",
        )
        del resource_version
        key = (identity[0], identity[1], namespace, identity[2])
        if key in resource_keys:
            raise ValueError("Controller bridge Kubernetes resource identities must be unique.")
        resource_keys.add(key)
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SUBSTRATE_READY.value]:
        namespace_resources = [
            item
            for item in kubernetes_resources
            if isinstance(item, Mapping)
            and item.get("kind") == "Namespace"
            and item.get("name") == journal.get("namespace")
        ]
        pv_resources = [
            item
            for item in kubernetes_resources
            if isinstance(item, Mapping) and item.get("kind") == "PersistentVolume"
        ]
        pvc_resources = [
            item
            for item in kubernetes_resources
            if isinstance(item, Mapping)
            and item.get("kind") == "PersistentVolumeClaim"
            and item.get("namespace") == journal.get("namespace")
        ]
        if len(namespace_resources) != 1 or len(pv_resources) != 2 or len(pvc_resources) != 2:
            raise ValueError(
                "Controller bridge substrate requires exact namespace plus state and Jail "
                "PV/PVC identities."
            )
    login_handoff = journal["login_session_handoff"]
    login_state = str(login_handoff.get("state", "") or "")
    if login_handoff.get("revision") != 1 or login_state not in {
        "uninitialized",
        "protected",
        "target-ready",
        "pending-voluntary-exit",
        "complete",
        "indeterminate",
    }:
        raise ValueError("Controller bridge login-session handoff state is invalid.")
    for field in ("source_services", "protected_pods", "sessions"):
        value = login_handoff.get(field)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"Controller bridge login-session {field} must be a list.")
    protected_pod_uids: set[str] = set()
    source_service_names: set[str] = set()
    for service in login_handoff.get("source_services", []):
        if not isinstance(service, Mapping):
            raise ValueError("Controller bridge protected login Service must be a mapping.")
        name = _required_text(service.get("name"), field="protected login Service name")
        _required_text(service.get("uid"), field="protected login Service uid")
        _required_text(service.get("type"), field="protected login Service type")
        if name in source_service_names:
            raise ValueError("Protected login Service names must be unique.")
        source_service_names.add(name)
    for pod in login_handoff.get("protected_pods", []):
        if not isinstance(pod, Mapping):
            raise ValueError("Controller bridge protected login Pod must be a mapping.")
        for field in (
            "name",
            "uid",
            "node_name",
            "node_uid",
            "node_group_id",
            "container_id",
        ):
            _required_text(pod.get(field), field=f"protected login Pod {field}")
        host_keys = pod.get("host_key_fingerprints")
        if (
            not isinstance(host_keys, Sequence)
            or isinstance(host_keys, (str, bytes, bytearray))
            or not host_keys
            or any(not str(item or "").strip() for item in host_keys)
        ):
            raise ValueError("Protected login Pod host-key fingerprints are required.")
        restart_count = pod.get("restart_count")
        if (
            isinstance(restart_count, bool)
            or not isinstance(restart_count, int)
            or restart_count < 0
        ):
            raise ValueError("Protected login Pod restart_count must be non-negative.")
        protected_pod_uids.add(str(pod.get("uid") or ""))
    session_keys: set[tuple[str, str]] = set()
    unconfirmed_session_count = 0
    timeout_authorized_session_count = 0
    for session in login_handoff.get("sessions", []):
        if not isinstance(session, Mapping):
            raise ValueError("Controller bridge protected login session must be a mapping.")
        for field in (
            "pod_uid",
            "socket_fingerprint",
            "socket_inode",
            "sshd_pid",
            "sshd_start_time",
            "shell_pid",
            "shell_start_time",
            "host_key_fingerprint",
        ):
            _required_text(session.get(field), field=f"protected login session {field}")
        _sha256(
            session.get("socket_fingerprint"),
            field="protected login session socket_fingerprint",
        )
        pod_uid = str(session.get("pod_uid") or "")
        if pod_uid not in protected_pod_uids:
            raise ValueError("Protected login session must bind a protected Pod UID.")
        key = (pod_uid, str(session.get("socket_fingerprint") or ""))
        if key in session_keys:
            raise ValueError("Protected login session identities must be unique.")
        session_keys.add(key)
        ended_at = str(session.get("ended_at") or "").strip()
        acknowledgement = session.get("exit_acknowledgement")
        if not ended_at:
            unconfirmed_session_count += 1
            if acknowledgement is not None:
                raise ValueError(
                    "Active protected login session cannot contain an exit acknowledgement."
                )
            continue
        if not isinstance(acknowledgement, Mapping):
            raise ValueError(
                "Ended protected login session requires exact explicit-exit acknowledgement."
            )
        disposition = str(
            acknowledgement.get("disposition") or SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY
        )
        outcome = str(session.get("outcome") or "")
        if not (
            (
                disposition == SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY
                and outcome == "explicitly-acknowledged-voluntary-exit"
            )
            or (
                disposition == SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION
                and outcome == SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION
            )
        ):
            raise ValueError(
                "Ended protected login session outcome conflicts with its exact exit "
                "disposition."
            )
        if disposition == SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION:
            timeout_authorized_session_count += 1
        if (
            acknowledgement.get("socket_fingerprint") != session.get("socket_fingerprint")
            or not str(acknowledgement.get("absence_observed_at") or "").strip()
            or not str(acknowledgement.get("acknowledged_at") or "").strip()
            or not str(acknowledgement.get("acknowledged_by") or "").strip()
        ):
            raise ValueError(
                "Protected login exit acknowledgement does not bind its exact socket identity."
            )
    if login_state != "uninitialized" and (
        not str(login_handoff.get("locked_at", "") or "").strip()
        or not login_handoff.get("protected_pods")
        or not login_handoff.get("source_services")
    ):
        raise ValueError("Controller bridge login-session protection lock is incomplete.")
    if login_handoff.get("no_sessions_at_lock") is True and login_handoff.get("sessions"):
        raise ValueError("Controller bridge no-sessions proof conflicts with session records.")
    if (
        login_state == "indeterminate"
        and not str(login_handoff.get("indeterminate_reason", "") or "").strip()
    ):
        raise ValueError("Indeterminate login handoff requires a reason.")
    if login_state == "complete" and (
        login_handoff.get("target_ready") is not True
        or not str(login_handoff.get("service_switched_at", "") or "").strip()
        or unconfirmed_session_count
        or (
            not login_handoff.get("no_sessions_at_lock")
            and not str(
                login_handoff.get(
                    "session_handoff_completed_at"
                    if timeout_authorized_session_count
                    else "voluntary_handoff_at",
                    "",
                )
                or ""
            ).strip()
        )
    ):
        raise ValueError("Completed login handoff lacks target and explicit exit proof.")
    if login_handoff.get("target_ready") is True:
        target_pod = login_handoff.get("target_pod")
        if not isinstance(target_pod, Mapping):
            raise ValueError("Ready target login Pod proof must be a mapping.")
        for field in (
            "name",
            "uid",
            "node_name",
            "node_uid",
            "node_group_id",
            "node_group_name",
            "kubernetes_version",
            "container_id",
        ):
            _required_text(target_pod.get(field), field=f"target login Pod {field}")
        if str(target_pod.get("uid") or "") in protected_pod_uids:
            raise ValueError("Target login Pod must be distinct from every protected source Pod.")
        target_host_keys = target_pod.get("host_key_fingerprints")
        if (
            not isinstance(target_host_keys, Sequence)
            or isinstance(target_host_keys, (str, bytes, bytearray))
            or not target_host_keys
        ):
            raise ValueError("Ready target login Pod requires SSH host-key fingerprints.")
        target_binding = login_handoff.get("target_binding")
        if not isinstance(target_binding, Mapping):
            raise ValueError("Ready target login Pod requires an exact target binding.")
        target_slurmcluster = target_binding.get("target_slurmcluster")
        target_workload = target_binding.get("workload")
        target_node_group = target_binding.get("replacement_node_group")
        if not all(
            isinstance(item, Mapping)
            for item in (target_slurmcluster, target_workload, target_node_group)
        ):
            raise ValueError(
                "Ready target login Pod requires an exact target binding with complete sections."
            )
        for field in ("namespace", "name", "uid"):
            _required_text(
                target_slurmcluster.get(field),
                field=f"target login SlurmCluster {field}",
            )
        for field in ("api_version", "kind", "name", "uid"):
            _required_text(
                target_workload.get(field),
                field=f"target login workload {field}",
            )
        for field in ("id", "name"):
            _required_text(
                target_node_group.get(field),
                field=f"target login replacement node group {field}",
            )
        _required_text(
            target_binding.get("kubernetes_version"),
            field="target login Kubernetes version",
        )
        target_pod_binding = target_pod.get("target_binding")
        target_pod_workload = target_pod.get("workload")
        if not isinstance(target_pod_binding, Mapping) or not isinstance(
            target_pod_workload, Mapping
        ):
            raise ValueError(
                "Ready target login Pod requires exact nested target and workload bindings."
            )
        if dict(target_pod_binding) != dict(target_binding):
            raise ValueError("Ready target login Pod target binding drifted.")
        if dict(target_pod_workload) != dict(target_workload):
            raise ValueError("Ready target login Pod workload binding drifted.")
        if (
            str(target_pod.get("node_group_id") or "") != str(target_node_group.get("id") or "")
            or str(target_pod.get("node_group_name") or "")
            != str(target_node_group.get("name") or "")
            or str(target_pod.get("kubernetes_version") or "")
            != str(target_binding.get("kubernetes_version") or "")
        ):
            raise ValueError(
                "Ready target login Pod replacement node-group or Kubernetes binding drifted."
            )
        expected_image = str(target_binding.get("sshd_image") or "")
        expected_digest = str(target_binding.get("sshd_image_digest") or "")
        if expected_image and str(target_pod.get("configured_image") or "") != expected_image:
            raise ValueError("Ready target login Pod SSH image binding drifted.")
        if (
            expected_digest
            and str(target_pod.get("resolved_image_digest") or "") != expected_digest
        ):
            raise ValueError("Ready target login Pod SSH image digest binding drifted.")
    shared_mount_canaries = journal.get("shared_mount_canaries")
    if not isinstance(shared_mount_canaries, Sequence) or isinstance(
        shared_mount_canaries, (str, bytes, bytearray)
    ):
        raise ValueError("Controller bridge shared_mount_canaries must be a list.")
    purposes: set[str] = set()
    for canary in shared_mount_canaries:
        if not isinstance(canary, Mapping):
            raise ValueError("Controller bridge shared-mount canary must be a mapping.")
        purpose = _safe_token(
            canary.get("purpose"),
            field="bridge shared-mount canary purpose",
        )
        if purpose in purposes:
            raise ValueError("Controller bridge shared-mount canary purposes must be unique.")
        purposes.add(purpose)
        _validate_shared_mount_canary(journal, canary)
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.SUBSTRATE_READY.value]:
        pre_source_mutation_mount_canary(journal)
    if _BRIDGE_STAGE_INDEX[stage] >= _BRIDGE_STAGE_INDEX[BridgeStage.PARTITIONS_RESTORED.value]:
        partition_restore = journal["partition_restore"]
        if partition_restore.get("status") != "restored":
            raise ValueError("Controller bridge partition restore proof is incomplete.")
        _sha256(
            partition_restore.get("records_sha256"),
            field="bridge partition restore records_sha256",
        )
        partition_count = partition_restore.get("partition_count")
        generation = partition_restore.get("action_journal_generation")
        if (
            isinstance(partition_count, bool)
            or not isinstance(partition_count, int)
            or partition_count < 0
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise ValueError("Controller bridge partition restore counters are invalid.")
        for field in ("authority_epoch", "intent_at", "restored_at"):
            _required_text(
                partition_restore.get(field),
                field=f"bridge partition restore {field}",
            )
    if stage == BridgeStage.CLEANED.value:
        cleanup = journal["cleanup"]
        if login_state != "complete":
            raise ValueError("Controller bridge cleanup requires completed login handoff.")
        _required_text(cleanup.get("intent_at"), field="bridge cleanup intent_at")
        _required_text(
            cleanup.get("bridge_storage_verified_at"),
            field="bridge cleanup bridge_storage_verified_at",
        )
        storage_bindings = _required_mapping(
            cleanup.get("bridge_storage_bindings"),
            field="bridge cleanup bridge_storage_bindings",
        )
        storage_records = {
            "controller_spool": CONTROLLER_BRIDGE_STATE_PVC,
            "jail": CONTROLLER_BRIDGE_JAIL_PVC,
        }
        if set(storage_bindings) != set(storage_records):
            raise ValueError(
                "Controller bridge cleanup requires exact state and Jail storage proofs."
            )
        for label, claim_name in storage_records.items():
            binding = _required_mapping(
                storage_bindings.get(label),
                field=f"bridge cleanup {label} storage binding",
            )
            pv_matches = [
                item
                for item in kubernetes_resources
                if isinstance(item, Mapping)
                and item.get("kind") == "PersistentVolume"
                and item.get("name") == f"{claim_name}-pv"
                and not str(item.get("namespace", "") or "")
            ]
            pvc_matches = [
                item
                for item in kubernetes_resources
                if isinstance(item, Mapping)
                and item.get("kind") == "PersistentVolumeClaim"
                and item.get("name") == claim_name
                and item.get("namespace") == journal.get("namespace")
            ]
            if len(pv_matches) != 1 or len(pvc_matches) != 1:
                raise ValueError(
                    "Controller bridge cleanup storage proof lacks an exact PV/PVC pair."
                )
            expected_identity = {
                "pv_name": str(pv_matches[0].get("name", "") or ""),
                "pv_uid": str(pv_matches[0].get("uid", "") or ""),
                "pvc_name": str(pvc_matches[0].get("name", "") or ""),
                "pvc_uid": str(pvc_matches[0].get("uid", "") or ""),
            }
            local_path = str(binding.get("local_path", "") or "")
            if (
                set(binding)
                != {
                    "pv_name",
                    "pv_uid",
                    "pvc_name",
                    "pvc_uid",
                    "reclaim_policy",
                    "local_path",
                }
                or any(binding.get(field) != value for field, value in expected_identity.items())
                or binding.get("reclaim_policy") != "Retain"
                or not local_path.startswith("/")
                or (label == "controller_spool" and local_path != "/mnt/controller-spool")
            ):
                raise ValueError(
                    "Controller bridge cleanup state or Jail Retain storage proof drifted."
                )
        preservation = cleanup.get("pre_cleanup_preservation_proof")
        if not isinstance(preservation, Mapping) or preservation.get("status") != "verified":
            raise ValueError("Controller bridge cleanup preservation proof is incomplete.")
        generation = preservation.get("action_journal_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("Controller bridge cleanup action-journal generation is invalid.")
        for field in ("authority_epoch", "verified_at", "verification_sha256"):
            _required_text(
                preservation.get(field),
                field=f"bridge cleanup preservation {field}",
            )
        verification_sha256 = _sha256(
            preservation.get("verification_sha256"),
            field="bridge cleanup preservation verification_sha256",
        )
        pre_cleanup_verifications = [
            item
            for item in preservation_verifications
            if isinstance(item, Mapping) and item.get("stage") == "pre-cleanup"
        ]
        if len(pre_cleanup_verifications) != 1:
            raise ValueError("Controller bridge cleanup requires one pre-cleanup verification.")
        pre_cleanup_verification = pre_cleanup_verifications[0]
        if (
            pre_cleanup_verification.get("action_journal_generation") != generation
            or pre_cleanup_verification.get("authority_epoch")
            != preservation.get("authority_epoch")
            or pre_cleanup_verification.get("verified_at") != preservation.get("verified_at")
            or _journal_payload_fingerprint(pre_cleanup_verification) != verification_sha256
        ):
            raise ValueError("Controller bridge cleanup preservation proof binding drifted.")
        if not all(
            cleanup.get(field) is True
            for field in (
                "namespace_deleted",
                "bridge_resources_absent",
                "shared_state_retained",
                "final_singleton_proven",
            )
        ):
            raise ValueError("Controller bridge cleanup proof is incomplete.")
        deleted = cleanup.get("node_groups_deleted")
        preserved = cleanup.get("node_groups_preserved")
        if (
            not isinstance(deleted, Sequence)
            or isinstance(deleted, (str, bytes, bytearray))
            or not isinstance(preserved, Sequence)
            or isinstance(preserved, (str, bytes, bytearray))
        ):
            raise ValueError("Controller bridge cleanup node-group proof must be a list.")
        expected_deleted = {
            str(item.get("id", "") or "")
            for item in node_groups
            if isinstance(item, Mapping) and item.get("cleanup_policy") == "delete-domain"
        }
        expected_preserved = {
            str(item.get("id", "") or "")
            for item in node_groups
            if isinstance(item, Mapping) and item.get("cleanup_policy") == "preserve-domain"
        }
        if set(str(item or "") for item in deleted) != expected_deleted:
            raise ValueError(
                "Controller bridge cleanup must delete both journaled node groups for the "
                "external adapter; managed domains are preserved."
            )
        if set(str(item or "") for item in preserved) != expected_preserved:
            raise ValueError("Controller bridge cleanup preserved-domain proof is incomplete.")
        deleted_resources = cleanup.get("kubernetes_resources_deleted")
        if not isinstance(deleted_resources, Sequence) or isinstance(
            deleted_resources, (str, bytes, bytearray)
        ):
            raise ValueError("Controller bridge cleanup Kubernetes proof must be a list.")
        expected_resource_keys = {
            "|".join(
                (
                    str(item.get("api_version", "") or ""),
                    str(item.get("kind", "") or ""),
                    str(item.get("namespace", "") or ""),
                    str(item.get("name", "") or ""),
                    str(item.get("uid", "") or ""),
                )
            )
            for item in kubernetes_resources
            if isinstance(item, Mapping)
        }
        if set(str(item or "") for item in deleted_resources) != expected_resource_keys:
            raise ValueError("Controller bridge cleanup must prove every bound resource absent.")
        kubernetes_operations = cleanup.get("kubernetes_operations")
        if not isinstance(kubernetes_operations, Mapping):
            raise ValueError("Controller bridge Kubernetes cleanup operations must be a mapping.")
        direct_resources = [
            item
            for item in kubernetes_resources
            if isinstance(item, Mapping)
            and (
                item.get("kind") in {"Namespace", "PersistentVolume", "PersistentVolumeClaim"}
                or (item.get("kind") == "RoleBinding" and item.get("namespace") == "soperator")
            )
        ]
        for item in direct_resources:
            resource_key = "|".join(
                (
                    str(item.get("api_version", "") or ""),
                    str(item.get("kind", "") or ""),
                    str(item.get("namespace", "") or ""),
                    str(item.get("name", "") or ""),
                    str(item.get("uid", "") or ""),
                )
            )
            operation = kubernetes_operations.get(resource_key)
            if not isinstance(operation, Mapping) or operation.get("state") != "absent":
                raise ValueError(
                    "Controller bridge direct Kubernetes cleanup lacks absent operation proof."
                )
            expected_identity = {
                field: str(item.get(field, "") or "")
                for field in (
                    "api_version",
                    "kind",
                    "namespace",
                    "name",
                    "uid",
                    "resource_version",
                )
            }
            if dict(operation.get("identity", {})) != expected_identity:
                raise ValueError("Controller bridge Kubernetes cleanup operation identity drifted.")
            _required_text(
                operation.get("intent_at"),
                field="bridge Kubernetes cleanup intent_at",
            )
            _required_text(
                operation.get("absent_at"),
                field="bridge Kubernetes cleanup absent_at",
            )
        operations_by_resource = {
            (str(item.get("kind", "") or ""), str(item.get("name", "") or "")): _required_mapping(
                kubernetes_operations.get(
                    "|".join(
                        (
                            str(item.get("api_version", "") or ""),
                            str(item.get("kind", "") or ""),
                            str(item.get("namespace", "") or ""),
                            str(item.get("name", "") or ""),
                            str(item.get("uid", "") or ""),
                        )
                    )
                ),
                field="bridge ordered Kubernetes cleanup operation",
            )
            for item in direct_resources
        }
        namespace_operation = operations_by_resource.get(
            ("Namespace", CONTROLLER_BRIDGE_NAMESPACE),
            {},
        )
        pvc_operations = [
            operations_by_resource.get(("PersistentVolumeClaim", claim_name), {})
            for claim_name in (CONTROLLER_BRIDGE_STATE_PVC, CONTROLLER_BRIDGE_JAIL_PVC)
        ]
        pv_operations = [
            operations_by_resource.get(("PersistentVolume", f"{claim_name}-pv"), {})
            for claim_name in (CONTROLLER_BRIDGE_STATE_PVC, CONTROLLER_BRIDGE_JAIL_PVC)
        ]
        namespace_intent = _required_text(
            namespace_operation.get("intent_at"),
            field="bridge Namespace cleanup intent_at",
        )
        namespace_absent = _required_text(
            namespace_operation.get("absent_at"),
            field="bridge Namespace cleanup absent_at",
        )
        pvc_absence_times = [
            _required_text(
                operation.get("absent_at"),
                field="bridge PVC cleanup absent_at",
            )
            for operation in pvc_operations
        ]
        pv_intent_times = [
            _required_text(
                operation.get("intent_at"),
                field="bridge PV cleanup intent_at",
            )
            for operation in pv_operations
        ]
        if any(timestamp > namespace_intent for timestamp in pvc_absence_times) or any(
            timestamp < namespace_absent for timestamp in pv_intent_times
        ):
            raise ValueError(
                "Controller bridge cleanup must delete both PVCs before the Namespace and "
                "both PVs after it."
            )
        node_group_operations = cleanup.get("node_group_operations")
        if not isinstance(node_group_operations, Mapping):
            raise ValueError("Controller bridge node-group cleanup operations must be a mapping.")
        expected_group_ids = {
            str(item.get("id", "") or "") for item in node_groups if isinstance(item, Mapping)
        }
        if set(str(item or "") for item in node_group_operations) != expected_group_ids:
            raise ValueError(
                "Controller bridge cleanup requires exact operations for both node groups."
            )
        for state in node_group_operations.values():
            operation = state.get("operation") if isinstance(state, Mapping) else None
            if (
                not isinstance(operation, Mapping)
                or operation.get("attempt_state") not in {"provider-terminal", "verified"}
                or not str(operation.get("provider_operation_id", "") or "").strip()
            ):
                raise ValueError(
                    "Controller bridge node-group cleanup lacks terminal provider evidence."
                )
        target_state = cleanup.get("target_state_binding")
        if not isinstance(target_state, Mapping):
            raise ValueError("Controller bridge cleanup target state binding must be a mapping.")
        for field in ("pvc_name", "pvc_uid", "pv_name", "pv_uid", "state_path"):
            _required_text(target_state.get(field), field=f"bridge cleanup target state {field}")
        if target_state.get("state_path") != journal.get("state_manifest", {}).get("stable_path"):
            raise ValueError("Controller bridge cleanup target state path changed.")
        _required_text(cleanup.get("proof_checked_at"), field="bridge cleanup proof_checked_at")
        _required_text(cleanup.get("completed_at"), field="bridge cleanup completed_at")


def advance_bridge_stage(
    journal: MutableMapping[str, Any],
    stage: BridgeStage,
    *,
    at: str | None = None,
) -> None:
    validate_bridge_journal(journal)
    current = BridgeStage(str(journal["stage"]))
    current_index = _BRIDGE_STAGE_INDEX[current.value]
    next_index = _BRIDGE_STAGE_INDEX[stage.value]
    if next_index == current_index:
        return
    if next_index != current_index + 1:
        raise ValueError(
            f"Controller bridge stage must advance one durable boundary at a time: "
            f"{current.value} -> {stage.value}."
        )
    journal["stage"] = stage.value
    journal["updated_at"] = at or _utc_now()
    validate_bridge_journal(journal)


def record_bridge_authority(
    journal: MutableMapping[str, Any],
    *,
    epoch: str,
    owner: str,
    reason: str,
    first_bridge_write: bool = False,
    at: str | None = None,
) -> None:
    validate_bridge_journal(journal)
    normalized_epoch = _safe_token(epoch, field="bridge authority epoch")
    if owner not in _BRIDGE_AUTHORITY_OWNERS:
        raise ValueError(f"Unsupported controller bridge authority owner: {owner}.")
    authority = journal.get("authority")
    if not isinstance(authority, dict):
        raise ValueError("Controller bridge authority must be mutable.")
    if (
        str(authority.get("first_bridge_write_at", "") or "").strip()
        and owner == "source-singleton"
    ):
        raise ValueError("Source singleton restart is prohibited after the first bridge write.")
    timestamp = at or _utc_now()
    authority["epoch"] = normalized_epoch
    authority["owner"] = owner
    if first_bridge_write:
        if owner not in {"bridge-source", "bridge-target"}:
            raise ValueError("First bridge write must be owned by a bridge controller pair.")
        authority["first_bridge_write_at"] = timestamp
        authority["source_restart_prohibited"] = True
    history = authority.get("history")
    if not isinstance(history, list):
        raise ValueError("Controller bridge authority history must be mutable.")
    history.append(
        {
            "epoch": normalized_epoch,
            "owner": owner,
            "at": timestamp,
            "reason": _required_text(reason, field="bridge authority reason"),
        }
    )
    journal["updated_at"] = timestamp
    validate_bridge_journal(journal)


def bridge_node_group_payloads(plan: BridgePlan) -> tuple[dict[str, Any], dict[str, Any]]:
    if any(domain.ownership != "external-temporary" for domain in plan.placement_domains):
        raise ValueError(
            "Managed controller bridges bind existing placement domains and must not render "
            "provider node-group create payloads."
        )
    payloads: list[dict[str, Any]] = []
    for slot, item in enumerate(plan.placement_domains):
        template = copy.deepcopy(dict(item.template))
        metadata = copy.deepcopy(dict(template.get("metadata", {})))
        labels = copy.deepcopy(dict(metadata.get("labels", {})))
        for key in tuple(labels):
            if key.startswith("slurm.nebius.ai/") or key == "nebius.com/node-group":
                labels.pop(key, None)
        labels.update(
            {
                CONTROLLER_BRIDGE_LABEL: "true",
                CONTROLLER_BRIDGE_SLOT_LABEL: str(slot),
            }
        )
        metadata["labels"] = labels
        template["metadata"] = metadata
        managed_mount_tags = {
            str(plan.controller_spool_attachment.get("mount_tag", "") or ""),
            str(plan.jail_attachment.get("mount_tag", "") or ""),
        }
        filesystems = [
            copy.deepcopy(dict(existing))
            for existing in template.get("filesystems", [])
            if isinstance(existing, Mapping)
            and str(existing.get("mount_tag", "") or "") not in managed_mount_tags
        ]
        filesystems.extend(
            (
                copy.deepcopy(dict(plan.controller_spool_attachment)),
                copy.deepcopy(dict(plan.jail_attachment)),
            )
        )
        template["filesystems"] = filesystems
        # A source controller group normally carries a role-specific NoSchedule
        # taint.  The temporary bridge does not run the source operator's role
        # workloads, and its stager/canary Pods must share one deterministic
        # scheduling contract with the controller pair.  Do not inherit any
        # source taint that those cxcli-owned Pods cannot tolerate.
        template["taints"] = [
            {"key": CONTROLLER_BRIDGE_TAINT_KEY, "value": "true", "effect": "NO_SCHEDULE"}
        ]
        payloads.append(
            {
                "metadata": {"parent_id": plan.cluster_id, "name": item.name},
                "spec": {
                    "version": plan.source_kubernetes_version,
                    "fixed_node_count": 1,
                    "template": template,
                },
            }
        )
    return payloads[0], payloads[1]


def bridge_kubernetes_objects(
    plan: BridgePlan,
    *,
    controller_pod_spec: Mapping[str, Any],
    state_volume_name: str,
    state_volume_claim_name: str,
    state_storage_class: str,
    state_storage_size: str,
    state_local_path: str,
    state_node_match_expressions: Sequence[Mapping[str, Any]],
    jail_volume_claim_name: str,
    jail_storage_class: str,
    jail_storage_size: str,
    jail_local_path: str,
    jail_access_modes: Sequence[str],
    jail_volume_mode: str,
    soperator_namespace: str,
    soperator_cluster_name: str,
    kubernetes_api_cidrs: Sequence[str],
    replicas: int = 0,
) -> tuple[dict[str, Any], ...]:
    volume_name = _safe_token(state_volume_name, field="bridge state volume name")
    claim_name = _safe_token(state_volume_claim_name, field="bridge state PVC name")
    storage_class = _safe_token(state_storage_class, field="bridge state storage class")
    jail_claim_name = _safe_token(jail_volume_claim_name, field="bridge Jail PVC name")
    jail_class = _safe_token(jail_storage_class, field="bridge Jail storage class")
    if not str(state_local_path or "").startswith("/"):
        raise ValueError("bridge state local path must be absolute.")
    if not str(jail_local_path or "").startswith("/"):
        raise ValueError("bridge Jail local path must be absolute.")
    normalized_jail_access_modes = tuple(str(item or "").strip() for item in jail_access_modes)
    if (
        not normalized_jail_access_modes
        or any(not item for item in normalized_jail_access_modes)
        or "ReadWriteMany" not in normalized_jail_access_modes
    ):
        raise ValueError("bridge Jail storage must preserve ReadWriteMany access.")
    if jail_volume_mode != "Filesystem":
        raise ValueError("bridge Jail storage must use Filesystem volume mode.")
    if not state_node_match_expressions:
        raise ValueError("bridge state PV requires node match expressions.")
    if replicas not in {0, 2}:
        raise ValueError("bridge StatefulSet replicas must be 0 while gated or 2 while active.")
    controller_spool_mount_tag = _required_text(
        plan.controller_spool_attachment.get("mount_tag"),
        field="bridge controller-spool mount tag",
    )
    jail_mount_tag = _required_text(
        plan.jail_attachment.get("mount_tag"),
        field="bridge Jail mount tag",
    )

    pod_spec = copy.deepcopy(dict(controller_pod_spec))
    pod_spec.pop("nodeName", None)
    # Pod priority and preemption policy are admission-owned whenever a
    # PriorityClass is selected. Mirrored live Pod specs contain values
    # computed from their original class; carrying those values across to the
    # bridge's class makes Kubernetes reject the Pod when the classes differ.
    pod_spec.pop("priority", None)
    pod_spec.pop("preemptionPolicy", None)
    pod_spec.setdefault("automountServiceAccountToken", False)
    managed_adapter = all(
        domain.ownership == "managed-existing" for domain in plan.placement_domains
    )
    pod_spec["priorityClassName"] = "cxcli-soperator-upgrade-bridge"
    if managed_adapter:
        pod_spec.pop("nodeSelector", None)
        tolerations = [
            copy.deepcopy(dict(item))
            for item in pod_spec.get("tolerations", [])
            if isinstance(item, Mapping)
        ]
        for role in ("controller", "system"):
            toleration = {
                "key": "slurm.nebius.ai/nodeset-name",
                "operator": "Equal",
                "value": role,
                "effect": "NoSchedule",
            }
            if toleration not in tolerations:
                tolerations.append(toleration)
        pod_spec["tolerations"] = tolerations
        domain_values = sorted({domain.role for domain in plan.placement_domains})
        node_affinity = {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "nebius.ai/soperator-bridge-domain",
                                "operator": "In",
                                "values": domain_values,
                            }
                        ]
                    }
                ]
            }
        }
        pod_spec["topologySpreadConstraints"] = [
            {
                "maxSkew": 1,
                "minDomains": 2,
                "topologyKey": "nebius.ai/soperator-bridge-domain",
                "whenUnsatisfiable": "DoNotSchedule",
                "labelSelector": {
                    "matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"},
                },
            }
        ]
    else:
        pod_spec["nodeSelector"] = {CONTROLLER_BRIDGE_LABEL: "true"}
        pod_spec["tolerations"] = [
            {
                "key": CONTROLLER_BRIDGE_TAINT_KEY,
                "operator": "Equal",
                "value": "true",
                "effect": "NoSchedule",
            }
        ]
        node_affinity = None
    affinity = {
        "podAntiAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
                {
                    "labelSelector": {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}},
                    "topologyKey": "nebius.com/node-group-id",
                }
            ]
        }
    }
    if node_affinity is not None:
        affinity["nodeAffinity"] = node_affinity
    pod_spec["affinity"] = affinity
    volumes = [
        copy.deepcopy(dict(item))
        for item in pod_spec.get("volumes", [])
        if isinstance(item, Mapping) and item.get("name") != volume_name
    ]
    volumes.append({"name": volume_name, "persistentVolumeClaim": {"claimName": claim_name}})
    pod_spec["volumes"] = volumes
    for container_field in ("initContainers", "containers"):
        for container in pod_spec.get(container_field, []):
            if not isinstance(container, dict):
                continue
            for mount in container.get("volumeMounts", []):
                if isinstance(mount, dict) and mount.get("name") == volume_name:
                    mount["subPath"] = "current"
    pod_spec.pop("hostname", None)
    pod_spec["subdomain"] = "cxcli-slurm-controller-bridge"

    labels = {
        "app.kubernetes.io/name": "cxcli-slurm-controller-bridge",
        "app.kubernetes.io/managed-by": "nebius-cxcli",
        CONTROLLER_BRIDGE_LABEL: "true",
    }
    ownership_labels = {
        "app.kubernetes.io/managed-by": "nebius-cxcli",
        CONTROLLER_BRIDGE_LABEL: "true",
    }
    # The bridge mounts provider-attached virtiofs devices into the host mount
    # namespace before its local PVs can be consumed.  That bootstrap requires
    # one tightly selected privileged DaemonSet even when the mirrored source
    # controller itself only requires baseline Pod Security.
    pod_security_level = "baseline" if managed_adapter else "privileged"
    namespace = plan.namespace
    network_policies = bridge_network_policy_objects(
        namespace=namespace,
        soperator_namespace=soperator_namespace,
        soperator_cluster_name=soperator_cluster_name,
        kubernetes_api_cidrs=kubernetes_api_cidrs,
        ownership_labels=ownership_labels,
    )
    objects: tuple[dict[str, Any], ...] = (
        {
            "apiVersion": "scheduling.k8s.io/v1",
            "kind": "PriorityClass",
            "metadata": {
                "name": "cxcli-soperator-upgrade-bridge",
                "labels": ownership_labels,
            },
            "value": 100000000,
            "globalDefault": False,
            "preemptionPolicy": "Never",
            "description": "Priority for the temporary cxcli Slurm authority bridge.",
        },
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    **ownership_labels,
                    "pod-security.kubernetes.io/enforce": pod_security_level,
                    "pod-security.kubernetes.io/enforce-version": "latest",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/audit-version": "latest",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/warn-version": "latest",
                },
                "annotations": {
                    "nebius.ai/cxcli-pod-security-contract": (
                        f"source-controller-compatible:{pod_security_level}"
                    )
                },
            },
        },
        *network_policies,
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "namespace": namespace,
                "name": "cxcli-slurm-controller-bridge",
                "labels": ownership_labels,
            },
            "spec": {
                "clusterIP": "None",
                "publishNotReadyAddresses": True,
                "selector": {CONTROLLER_BRIDGE_LABEL: "true"},
                "ports": [{"name": "slurmctld", "port": 6817, "targetPort": 6817}],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": f"{claim_name}-pv", "labels": ownership_labels},
            "spec": {
                "storageClassName": storage_class,
                "volumeMode": "Filesystem",
                "capacity": {"storage": state_storage_size},
                "accessModes": ["ReadWriteMany"],
                "persistentVolumeReclaimPolicy": "Retain",
                "local": {"path": state_local_path},
                "claimRef": {"namespace": namespace, "name": claim_name},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {"matchExpressions": copy.deepcopy(list(state_node_match_expressions))}
                        ]
                    }
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "namespace": namespace,
                "name": claim_name,
                "labels": ownership_labels,
            },
            "spec": {
                "volumeName": f"{claim_name}-pv",
                "storageClassName": storage_class,
                "volumeMode": "Filesystem",
                "resources": {"requests": {"storage": state_storage_size}},
                "accessModes": ["ReadWriteMany"],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": f"{jail_claim_name}-pv", "labels": ownership_labels},
            "spec": {
                "storageClassName": jail_class,
                "volumeMode": jail_volume_mode,
                "capacity": {"storage": jail_storage_size},
                "accessModes": list(normalized_jail_access_modes),
                "persistentVolumeReclaimPolicy": "Retain",
                "local": {"path": jail_local_path},
                "claimRef": {"namespace": namespace, "name": jail_claim_name},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {"matchExpressions": copy.deepcopy(list(state_node_match_expressions))}
                        ]
                    }
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "namespace": namespace,
                "name": jail_claim_name,
                "labels": ownership_labels,
            },
            "spec": {
                "volumeName": f"{jail_claim_name}-pv",
                "storageClassName": jail_class,
                "volumeMode": jail_volume_mode,
                "resources": {"requests": {"storage": jail_storage_size}},
                "accessModes": list(normalized_jail_access_modes),
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "DaemonSet",
            "metadata": {
                "namespace": namespace,
                "name": "cxcli-controller-bridge-mounts",
                "labels": ownership_labels,
            },
            "spec": {
                "selector": {
                    "matchLabels": {
                        "nebius.ai/cxcli-controller-bridge-mounts": "true",
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/managed-by": "nebius-cxcli",
                            "nebius.ai/cxcli-controller-bridge-mounts": "true",
                        }
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "nodeSelector": {CONTROLLER_BRIDGE_LABEL: "true"},
                        "tolerations": [
                            {
                                "key": CONTROLLER_BRIDGE_TAINT_KEY,
                                "operator": "Equal",
                                "value": "true",
                                "effect": "NoSchedule",
                            }
                        ],
                        "containers": [
                            {
                                "name": "mount-shared-filesystems",
                                "image": plan.source_slurm_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": list(CONTROLLER_BRIDGE_MOUNT_HELPER_COMMAND),
                                "env": [
                                    {
                                        "name": "CONTROLLER_SPOOL_TAG",
                                        "value": controller_spool_mount_tag,
                                    },
                                    {
                                        "name": "CONTROLLER_SPOOL_PATH",
                                        "value": state_local_path,
                                    },
                                    {"name": "JAIL_TAG", "value": jail_mount_tag},
                                    {"name": "JAIL_PATH", "value": jail_local_path},
                                ],
                                "securityContext": {
                                    "privileged": True,
                                    "runAsUser": 0,
                                },
                                "volumeMounts": [
                                    {
                                        "name": "host-mnt",
                                        "mountPath": "/host/mnt",
                                        "mountPropagation": "Bidirectional",
                                    }
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "host-mnt",
                                "hostPath": {"path": "/mnt", "type": "Directory"},
                            }
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "namespace": namespace,
                "name": "cxcli-slurm-controller-bridge",
                "labels": labels,
            },
            "spec": {
                "serviceName": "cxcli-slurm-controller-bridge",
                "replicas": replicas,
                "podManagementPolicy": "Parallel",
                "selector": {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}},
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        },
        {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": {
                "namespace": namespace,
                "name": "cxcli-slurm-controller-bridge",
                "labels": ownership_labels,
            },
            "spec": {
                "minAvailable": 1,
                "selector": {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}},
            },
        },
    )
    if managed_adapter:
        return tuple(item for item in objects if item.get("kind") != "DaemonSet")
    return objects


def bridge_network_policy_objects(
    *,
    namespace: str,
    soperator_namespace: str,
    soperator_cluster_name: str,
    additional_soperator_cluster_names: Sequence[str] = (),
    kubernetes_api_cidrs: Sequence[str],
    ownership_labels: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Allow only bridge peers, the exact Soperator workload, DNS, and API CIDRs."""

    bridge_namespace = _safe_token(namespace, field="bridge network namespace")
    source_namespace = _safe_token(
        soperator_namespace,
        field="bridge Soperator network namespace",
    )
    cluster_name = _safe_token(
        soperator_cluster_name,
        field="bridge Soperator workload instance",
    )
    cluster_names = [cluster_name]
    for value in additional_soperator_cluster_names:
        candidate = _safe_token(value, field="bridge additional Soperator workload instance")
        if candidate not in cluster_names:
            cluster_names.append(candidate)
    normalized_api_cidrs: list[str] = []
    for value in kubernetes_api_cidrs:
        text = _required_text(value, field="bridge Kubernetes API CIDR")
        try:
            network = ipaddress.ip_network(text, strict=True)
        except ValueError as exc:
            raise ValueError(f"bridge Kubernetes API CIDR is invalid: {text}.") from exc
        if (
            network.prefixlen != network.max_prefixlen
            or network.is_link_local
            or network.is_multicast
            or network.is_unspecified
        ):
            raise ValueError(
                "bridge Kubernetes API CIDRs must be exact single-address, non-link-local networks."
            )
        normalized_api_cidrs.append(str(network))
    normalized_api_cidrs = sorted(set(normalized_api_cidrs))
    if not normalized_api_cidrs:
        raise ValueError("bridge Kubernetes API CIDRs must be non-empty.")

    labels = dict(
        ownership_labels
        or {
            "app.kubernetes.io/managed-by": "nebius-cxcli",
            CONTROLLER_BRIDGE_LABEL: "true",
        }
    )
    bridge_peer = {"podSelector": {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}}}
    soperator_peers = [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": source_namespace}
            },
            "podSelector": {"matchLabels": {"app.kubernetes.io/instance": name}},
        }
        for name in cluster_names
    ]
    required_traffic = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "namespace": bridge_namespace,
            "name": "cxcli-controller-bridge-required-traffic",
            "labels": labels,
        },
        "spec": {
            "podSelector": {"matchLabels": {CONTROLLER_BRIDGE_LABEL: "true"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {
                    "from": [bridge_peer, *soperator_peers],
                    "ports": [{"protocol": "TCP", "port": 6817}],
                }
            ],
            "egress": [
                {
                    "to": [bridge_peer, *soperator_peers],
                    "ports": [{"protocol": "TCP", "port": port} for port in (6817, 6818, 6819)],
                },
                {
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                            },
                            "podSelector": {"matchLabels": {"k8s-app": "coredns"}},
                        }
                    ],
                    "ports": [{"protocol": protocol, "port": 53} for protocol in ("UDP", "TCP")],
                },
                {
                    "to": [{"ipBlock": {"cidr": cidr}} for cidr in normalized_api_cidrs],
                    "ports": [{"protocol": "TCP", "port": 443}],
                },
            ],
        },
    }
    default_deny = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "namespace": bridge_namespace,
            "name": "cxcli-controller-bridge-default-deny",
            "labels": labels,
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
        },
    }
    return default_deny, required_traffic


def _bridge_pod_security_enforce_level(pod_spec: Mapping[str, Any]) -> str:
    """Choose the least Pod Security level compatible with the mirrored source Pod."""

    if any(pod_spec.get(field) is True for field in ("hostNetwork", "hostPID", "hostIPC")):
        return "privileged"
    if any(
        isinstance(volume, Mapping) and "hostPath" in volume
        for volume in pod_spec.get("volumes", [])
    ):
        return "privileged"
    baseline_capabilities = {
        "AUDIT_WRITE",
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "FSETID",
        "KILL",
        "MKNOD",
        "NET_BIND_SERVICE",
        "SETFCAP",
        "SETGID",
        "SETPCAP",
        "SETUID",
        "SYS_CHROOT",
    }
    containers = [
        item
        for field in ("initContainers", "containers", "ephemeralContainers")
        for item in pod_spec.get(field, [])
        if isinstance(item, Mapping)
    ]
    for container in containers:
        security_context = container.get("securityContext")
        if not isinstance(security_context, Mapping):
            security_context = {}
        windows_options = security_context.get("windowsOptions")
        seccomp = security_context.get("seccompProfile")
        capabilities = security_context.get("capabilities")
        added_capabilities = (
            {str(item).upper() for item in capabilities.get("add", []) if str(item).strip()}
            if isinstance(capabilities, Mapping)
            else set()
        )
        if (
            security_context.get("privileged") is True
            or security_context.get("procMount") == "Unmasked"
            or (isinstance(windows_options, Mapping) and windows_options.get("hostProcess") is True)
            or (isinstance(seccomp, Mapping) and str(seccomp.get("type") or "") == "Unconfined")
            or not added_capabilities.issubset(baseline_capabilities)
            or any(int(port.get("hostPort") or 0) != 0 for port in container.get("ports", []))
        ):
            return "privileged"
    return "baseline"


_CONTROLLER_AUTHORITY_OVERLAY_FIELDS = frozenset(
    {
        "clustername",
        "slurmctldhost",
        "statesavelocation",
        "slurmctldtimeout",
        "slurmdtimeout",
    }
)


def compose_controller_authority_config(
    slurm_conf: str,
    *,
    authority_owner: str,
    controller_hosts: Sequence[str],
    cluster_name: str | None = None,
    state_save_location: str | None = None,
    compatibility_fields: Mapping[str, str] | None = None,
) -> str:
    """Apply the cxcli-owned authority overlay without rewriting customer config."""

    hosts = [_required_text(item, field="authority SlurmctldHost") for item in controller_hosts]
    expected_host_counts = {
        "source-singleton": {1, 3},
        "bridge-source": {2},
        "bridge-target": {2},
        "target-singleton": {1},
    }
    if (
        authority_owner not in expected_host_counts
        or len(hosts) not in expected_host_counts[authority_owner]
    ):
        raise ValueError(
            f"controller authority owner {authority_owner!r} cannot use {len(hosts)} host(s)."
        )
    if len(set(hosts)) != len(hosts):
        raise ValueError("controller authority host set must be distinct.")
    if authority_owner.startswith("bridge-") and len(hosts) != 2:
        raise ValueError("bridge authority rejects singleton-only controller configuration.")

    source_lines = slurm_conf.splitlines()
    discovered_cluster_names = [
        match.group(1).strip()
        for line in source_lines
        if (match := re.match(r"^\s*ClusterName\s*=\s*(\S+)\s*$", line, flags=re.IGNORECASE))
    ]
    resolved_cluster_name = (
        _required_text(cluster_name, field="authority ClusterName")
        if cluster_name is not None
        else discovered_cluster_names[0]
        if len(discovered_cluster_names) == 1
        else ""
    )
    if not resolved_cluster_name:
        raise ValueError("controller authority composition requires exactly one ClusterName.")
    if state_save_location is not None and not state_save_location.startswith("/"):
        raise ValueError("controller authority StateSaveLocation must be absolute.")

    compatibility = dict(compatibility_fields or {})
    normalized_compatibility: dict[str, str] = {}
    for raw_key, raw_value in compatibility.items():
        key = str(raw_key or "").strip()
        normalized_key = key.lower()
        if normalized_key not in _CONTROLLER_AUTHORITY_OVERLAY_FIELDS - {
            "clustername",
            "slurmctldhost",
            "statesavelocation",
        }:
            raise ValueError(f"unsupported controller compatibility overlay field: {key}.")
        normalized_compatibility[key] = _required_text(
            raw_value,
            field=f"authority compatibility field {key}",
        )

    overlay_keys = {
        "clustername",
        "slurmctldhost",
        *({"statesavelocation"} if state_save_location is not None else set()),
        *(key.lower() for key in normalized_compatibility),
    }
    kept = [
        line
        for line in source_lines
        if not (
            (match := re.match(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*=", line))
            and match.group(1).lower() in overlay_keys
        )
    ]
    overlay = [
        f"ClusterName={resolved_cluster_name}",
        *(f"SlurmctldHost={host}" for host in hosts),
    ]
    if state_save_location is not None:
        overlay.append(f"StateSaveLocation={state_save_location}")
    overlay.extend(f"{key}={value}" for key, value in normalized_compatibility.items())
    kept[0:0] = overlay
    return "\n".join(kept).rstrip() + "\n"


def source_controller_config_with_bridge_hosts(
    slurm_conf: str,
    *,
    source_host: str | None = None,
    bridge_hosts: Sequence[str] = CONTROLLER_BRIDGE_CONTROLLER_HOSTS,
) -> str:
    normalized_bridge_hosts = [
        _required_text(item, field="bridge SlurmctldHost") for item in bridge_hosts
    ]
    bridge_names = {item.split("(", 1)[0] for item in normalized_bridge_hosts}
    existing_hosts = [
        match.group(1).strip()
        for line in slurm_conf.splitlines()
        if (
            match := re.match(
                r"^\s*SlurmctldHost\s*=\s*(\S+)\s*$",
                line,
                flags=re.IGNORECASE,
            )
        )
    ]
    if source_host is None:
        source_candidates = [
            item for item in existing_hosts if item.split("(", 1)[0] not in bridge_names
        ]
        if len(source_candidates) > 1:
            raise ValueError(
                "source controller bridge config requires exactly one non-bridge source host."
            )
        normalized_source_host = source_candidates[0] if source_candidates else "controller-0"
    else:
        normalized_source_host = _required_text(source_host, field="source SlurmctldHost")
    hosts = [normalized_source_host, *normalized_bridge_hosts]
    if len(hosts) != 3 or len(set(hosts)) != 3:
        raise ValueError("source controller bridge config requires three distinct hosts.")
    return compose_controller_authority_config(
        slurm_conf,
        authority_owner="source-singleton",
        controller_hosts=hosts,
    )


def bridge_only_controller_config(
    slurm_conf: str,
    *,
    bridge_hosts: Sequence[str] = CONTROLLER_BRIDGE_CONTROLLER_HOSTS,
    state_save_location: str = "/mnt/controller-spool/current",
) -> str:
    hosts = [_required_text(item, field="bridge SlurmctldHost") for item in bridge_hosts]
    if len(hosts) != 2 or len(set(hosts)) != 2:
        raise ValueError("bridge controller config requires two distinct hosts.")
    if not state_save_location.startswith("/"):
        raise ValueError("bridge StateSaveLocation must be absolute.")
    return compose_controller_authority_config(
        slurm_conf,
        authority_owner="bridge-source",
        controller_hosts=hosts,
        state_save_location=state_save_location,
    )


def final_singleton_controller_config(slurm_conf: str, *, target_host: str = "controller-0") -> str:
    target = _required_text(target_host, field="target SlurmctldHost")
    return compose_controller_authority_config(
        slurm_conf,
        authority_owner="target-singleton",
        controller_hosts=(target,),
    )


def target_controller_gate_values(values: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(values))
    slurm_nodes = result.setdefault("slurmNodes", {})
    if not isinstance(slurm_nodes, dict):
        raise ValueError("target values slurmNodes must be a mapping.")
    controller = slurm_nodes.setdefault("controller", {})
    if not isinstance(controller, dict):
        raise ValueError("target values slurmNodes.controller must be a mapping.")
    slurmctld = controller.setdefault("slurmctld", {})
    if not isinstance(slurmctld, dict):
        raise ValueError("target values controller.slurmctld must be a mapping.")
    slurmctld.update(
        {
            "command": ["/bin/sh", "-ec"],
            "args": [
                "trap 'exit 0' TERM INT; touch /tmp/cxcli-controller-gated; "
                "while :; do sleep 30; done"
            ],
            "livenessProbe": None,
            "readinessProbe": None,
        }
    )
    return result


def bridge_manifest_fingerprint(objects: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(objects, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
