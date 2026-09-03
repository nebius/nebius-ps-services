"""Durable create-cutover-retire orchestration for managed MK8s node groups."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .operation_config_authority import (
    ConfigGenerationTransition,
    config_transition_from_payload,
    upsert_config_transition,
    validate_config_transition_chain,
)
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

NODE_GROUP_MIGRATION_INTENT_SCHEMA = "nebius-cxcli.mk8s-node-group-migration.v3"
NODE_GROUP_MIGRATION_RECEIPT_SCHEMA = "nebius-cxcli.mk8s-node-group-migration-receipt.v3"

MIGRATION_PHASES = (
    "replacement-configured",
    "replacement-applied",
    "replacement-ready",
    "dual-placement-ready",
    "cutover-complete",
    "source-retired",
    "final-readiness",
)
CUTOVER_PHASE = "cutover-complete"

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_RESERVATION_POLICIES = frozenset({"AUTO", "FORBID", "STRICT"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _string_tuple(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"node-group migration {label} must be a list")
    return tuple(str(item) for item in value)


def _mapping_sequence(value: object, *, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"node-group migration {label} must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"node-group migration {label} contains a non-object item")
    return tuple(value)


def _placement_preimage(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError("node-group migration placement preimage must be a mapping")
    return {
        str(key): _string_tuple(refs, label=f"placement {key!r}") for key, refs in value.items()
    }


@dataclass(frozen=True)
class NodeGroupMigrationIntent:
    """Immutable authority for replacing one Terraform-managed node group."""

    schema: str
    target_selector: str
    instance_id: str
    cluster_id: str
    kubernetes_uid: str
    source_key: str
    source_provider_name: str
    replacement_key: str
    replacement_provider_name: str
    source_platform: str
    target_platform: str
    source_preset: str
    target_preset: str
    source_os: str
    target_os: str
    source_gpu_stack_preset: str
    target_gpu_stack_preset: str
    source_gpu_cluster_key: str
    target_gpu_cluster_key: str
    source_fabric: str
    target_fabric: str
    reservation_policy: str
    reservation_ids: tuple[str, ...]
    desired_node_count: int
    autoscaling_preimage: Mapping[str, Any]
    soperator_managed: bool
    placement_preimage: Mapping[str, tuple[str, ...]]
    shared_storage_evidence: tuple[str, ...]
    source_group: Mapping[str, Any]
    job_policy: str
    cancel_job_ids: tuple[str, ...]
    requeue_job_ids: tuple[str, ...]
    job_wait_timeout: str
    job_refresh_interval: str
    source_config_sha256: str
    source_project_snapshot_sha256: str

    @property
    def digest(self) -> str:
        return _sha256(asdict(self))


def build_node_group_migration_intent(
    *,
    target_selector: str,
    instance_id: str,
    cluster_id: str,
    kubernetes_uid: str,
    source_key: str,
    source_provider_name: str,
    replacement_key: str,
    replacement_provider_name: str,
    source_platform: str,
    target_platform: str,
    source_preset: str,
    target_preset: str,
    source_os: str,
    target_os: str,
    source_gpu_stack_preset: str,
    target_gpu_stack_preset: str,
    source_gpu_cluster_key: str,
    target_gpu_cluster_key: str,
    source_fabric: str,
    target_fabric: str,
    reservation_policy: str,
    reservation_ids: Sequence[str],
    desired_node_count: int,
    autoscaling_preimage: Mapping[str, Any],
    soperator_managed: bool,
    placement_preimage: Mapping[str, Sequence[str]],
    shared_storage_evidence: Sequence[str],
    source_group: Mapping[str, Any],
    job_policy: str,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    job_wait_timeout: str,
    job_refresh_interval: str,
    source_config_sha256: str,
    source_project_snapshot_sha256: str,
) -> NodeGroupMigrationIntent:
    intent = NodeGroupMigrationIntent(
        schema=NODE_GROUP_MIGRATION_INTENT_SCHEMA,
        target_selector=str(target_selector).strip(),
        instance_id=str(instance_id).strip(),
        cluster_id=str(cluster_id).strip(),
        kubernetes_uid=str(kubernetes_uid).strip(),
        source_key=str(source_key).strip(),
        source_provider_name=str(source_provider_name).strip(),
        replacement_key=str(replacement_key).strip(),
        replacement_provider_name=str(replacement_provider_name).strip(),
        source_platform=str(source_platform).strip(),
        target_platform=str(target_platform).strip(),
        source_preset=str(source_preset).strip(),
        target_preset=str(target_preset).strip(),
        source_os=str(source_os).strip(),
        target_os=str(target_os).strip(),
        source_gpu_stack_preset=str(source_gpu_stack_preset).strip(),
        target_gpu_stack_preset=str(target_gpu_stack_preset).strip(),
        source_gpu_cluster_key=str(source_gpu_cluster_key).strip(),
        target_gpu_cluster_key=str(target_gpu_cluster_key).strip(),
        source_fabric=str(source_fabric).strip(),
        target_fabric=str(target_fabric).strip(),
        reservation_policy=str(reservation_policy).strip().upper(),
        reservation_ids=tuple(
            sorted(set(str(value).strip() for value in reservation_ids if str(value).strip()))
        ),
        desired_node_count=int(desired_node_count),
        autoscaling_preimage=dict(autoscaling_preimage),
        soperator_managed=bool(soperator_managed),
        placement_preimage={
            str(key): tuple(str(value) for value in values)
            for key, values in placement_preimage.items()
        },
        shared_storage_evidence=tuple(sorted(set(shared_storage_evidence))),
        source_group=dict(source_group),
        job_policy=str(job_policy).strip(),
        cancel_job_ids=tuple(sorted(set(cancel_job_ids))),
        requeue_job_ids=tuple(sorted(set(requeue_job_ids))),
        job_wait_timeout=str(job_wait_timeout).strip(),
        job_refresh_interval=str(job_refresh_interval).strip(),
        source_config_sha256=str(source_config_sha256).strip(),
        source_project_snapshot_sha256=str(source_project_snapshot_sha256).strip(),
    )
    validate_node_group_migration_intent(intent)
    return intent


def validate_node_group_migration_intent(intent: NodeGroupMigrationIntent) -> None:
    if intent.schema != NODE_GROUP_MIGRATION_INTENT_SCHEMA:
        raise ValueError("node-group migration intent has an unsupported schema")
    for label, value in (
        ("target selector", intent.target_selector),
        ("instance id", intent.instance_id),
        ("cluster id", intent.cluster_id),
        ("Kubernetes uid", intent.kubernetes_uid),
        ("source key", intent.source_key),
        ("source provider name", intent.source_provider_name),
        ("replacement key", intent.replacement_key),
        ("replacement provider name", intent.replacement_provider_name),
        ("source platform", intent.source_platform),
        ("target platform", intent.target_platform),
        ("source preset", intent.source_preset),
        ("target preset", intent.target_preset),
    ):
        if not value:
            raise ValueError(f"node-group migration requires {label}")
    if intent.source_key == intent.replacement_key:
        raise ValueError("replacement node-group key must differ from the source key")
    if intent.source_provider_name == intent.replacement_provider_name:
        raise ValueError("replacement provider name must differ from the source name")
    if intent.desired_node_count < 1:
        raise ValueError("node-group migration desired node count must be at least one")
    if not _SHA256.fullmatch(intent.source_config_sha256):
        raise ValueError("node-group migration requires an exact source config digest")
    if not _SHA256.fullmatch(intent.source_project_snapshot_sha256):
        raise ValueError("node-group migration requires an exact source project snapshot digest")
    if intent.soperator_managed:
        if not intent.shared_storage_evidence:
            raise ValueError(
                "Soperator node-group migration requires shared-storage continuity evidence"
            )
        if not any(intent.source_key in refs for refs in intent.placement_preimage.values()):
            raise ValueError(
                "Soperator node-group migration requires a frozen placement that references "
                "the source group"
            )
    if not intent.source_group:
        raise ValueError("node-group migration requires the frozen source group preimage")
    if not intent.job_policy or not intent.job_wait_timeout or not intent.job_refresh_interval:
        raise ValueError("node-group migration requires frozen Slurm job controls")
    if intent.reservation_policy not in _RESERVATION_POLICIES:
        raise ValueError("node-group migration reservation policy must be AUTO, FORBID, or STRICT")
    if intent.reservation_policy == "STRICT" and not intent.reservation_ids:
        raise ValueError("STRICT replacement reservation policy requires reservation ids")


def node_group_migration_intent_from_payload(
    payload: Mapping[str, Any],
) -> NodeGroupMigrationIntent:
    """Rehydrate the frozen intent embedded in a migration receipt."""

    try:
        intent = NodeGroupMigrationIntent(
            schema=str(payload.get("schema", "")),
            target_selector=str(payload.get("target_selector", "")),
            instance_id=str(payload.get("instance_id", "")),
            cluster_id=str(payload.get("cluster_id", "")),
            kubernetes_uid=str(payload.get("kubernetes_uid", "")),
            source_key=str(payload.get("source_key", "")),
            source_provider_name=str(payload.get("source_provider_name", "")),
            replacement_key=str(payload.get("replacement_key", "")),
            replacement_provider_name=str(payload.get("replacement_provider_name", "")),
            source_platform=str(payload.get("source_platform", "")),
            target_platform=str(payload.get("target_platform", "")),
            source_preset=str(payload.get("source_preset", "")),
            target_preset=str(payload.get("target_preset", "")),
            source_os=str(payload.get("source_os", "")),
            target_os=str(payload.get("target_os", "")),
            source_gpu_stack_preset=str(payload.get("source_gpu_stack_preset", "")),
            target_gpu_stack_preset=str(payload.get("target_gpu_stack_preset", "")),
            source_gpu_cluster_key=str(payload.get("source_gpu_cluster_key", "")),
            target_gpu_cluster_key=str(payload.get("target_gpu_cluster_key", "")),
            source_fabric=str(payload.get("source_fabric", "")),
            target_fabric=str(payload.get("target_fabric", "")),
            reservation_policy=str(payload.get("reservation_policy", "")),
            reservation_ids=_string_tuple(
                payload.get("reservation_ids", ()), label="reservation ids"
            ),
            desired_node_count=int(payload.get("desired_node_count", 0)),
            autoscaling_preimage=(
                dict(payload.get("autoscaling_preimage", {}))
                if isinstance(payload.get("autoscaling_preimage"), Mapping)
                else {}
            ),
            soperator_managed=bool(payload.get("soperator_managed", False)),
            placement_preimage=_placement_preimage(payload.get("placement_preimage", {})),
            shared_storage_evidence=_string_tuple(
                payload.get("shared_storage_evidence", ()),
                label="shared-storage evidence",
            ),
            source_group=(
                dict(payload.get("source_group", {}))
                if isinstance(payload.get("source_group"), Mapping)
                else {}
            ),
            job_policy=str(payload.get("job_policy", "")),
            cancel_job_ids=_string_tuple(payload.get("cancel_job_ids", ()), label="cancel job ids"),
            requeue_job_ids=_string_tuple(
                payload.get("requeue_job_ids", ()), label="requeue job ids"
            ),
            job_wait_timeout=str(payload.get("job_wait_timeout", "")),
            job_refresh_interval=str(payload.get("job_refresh_interval", "")),
            source_config_sha256=str(payload.get("source_config_sha256", "")),
            source_project_snapshot_sha256=str(payload.get("source_project_snapshot_sha256", "")),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("node-group migration embedded intent is invalid") from exc
    validate_node_group_migration_intent(intent)
    return intent


@dataclass(frozen=True)
class MigrationPhaseResult:
    evidence: Mapping[str, Any]
    irreversible_frontier: str = ""


@dataclass(frozen=True)
class MigrationPhaseReceipt:
    name: str
    status: str
    attempts: int = 0
    irreversible_frontier: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    evidence_sha256: str = ""
    failure_type: str = ""


@dataclass(frozen=True)
class NodeGroupMigrationReceipt:
    schema: str
    status: str
    recovery_mode: str
    intent_sha256: str
    intent: Mapping[str, Any]
    target_selector: str
    cluster_id: str
    kubernetes_uid: str
    maintenance: str
    maintenance_evidence: Mapping[str, Any]
    config_generations: tuple[ConfigGenerationTransition, ...]
    phases: tuple[MigrationPhaseReceipt, ...]
    created_at: str
    updated_at: str


def node_group_migration_receipt_path(
    project_dir: Path,
    *,
    instance_id: str,
    source_key: str,
) -> Path:
    def _token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9.-]+", "-", value).strip("-.")
        if not token or token in {".", ".."}:
            raise ValueError("node-group migration identity does not produce a safe path")
        return token

    return (
        Path(project_dir)
        / ".nebius-cxcli"
        / "node-group-migrations-v1"
        / _token(instance_id)
        / _token(source_key)
        / "receipt.json"
    )


def legacy_node_group_checkpoint_path(
    project_dir: Path,
    *,
    instance_id: str,
    source_key: str,
) -> Path:
    token_instance = re.sub(r"[^A-Za-z0-9.-]+", "-", instance_id).strip("-.")
    token_group = re.sub(r"[^A-Za-z0-9.-]+", "-", source_key).strip("-.")
    return (
        Path(project_dir)
        / ".nebius-cxcli"
        / "node-group-migrations"
        / token_instance
        / token_group
        / "checkpoint.json"
    )


def refuse_legacy_node_group_checkpoint(
    project_dir: Path,
    *,
    instance_id: str,
    source_key: str,
) -> None:
    legacy = legacy_node_group_checkpoint_path(
        project_dir,
        instance_id=instance_id,
        source_key=source_key,
    )
    try:
        legacy.lstat()
    except FileNotFoundError:
        return
    raise RuntimeError(
        "legacy node-group pre-mutation checkpoint detected; it is read-only historical "
        f"evidence and cannot be translated into a migrate node-group receipt: {legacy}"
    )


def _new_receipt(intent: NodeGroupMigrationIntent) -> NodeGroupMigrationReceipt:
    now = _utc_now()
    return NodeGroupMigrationReceipt(
        schema=NODE_GROUP_MIGRATION_RECEIPT_SCHEMA,
        status="active",
        recovery_mode="pre-cutover",
        intent_sha256=intent.digest,
        intent=asdict(intent),
        target_selector=intent.target_selector,
        cluster_id=intent.cluster_id,
        kubernetes_uid=intent.kubernetes_uid,
        maintenance="pending",
        maintenance_evidence={},
        config_generations=(),
        phases=tuple(
            MigrationPhaseReceipt(name=name, status="pending", evidence={})
            for name in MIGRATION_PHASES
        ),
        created_at=now,
        updated_at=now,
    )


def _from_payload(payload: object) -> NodeGroupMigrationReceipt:
    if not isinstance(payload, Mapping):
        raise RuntimeError("node-group migration receipt must be an object")
    raw_phases = payload.get("phases")
    if not isinstance(raw_phases, list):
        raise RuntimeError("node-group migration receipt has no phase ledger")
    try:
        phases = tuple(
            MigrationPhaseReceipt(
                name=str(item["name"]),
                status=str(item["status"]),
                attempts=int(item.get("attempts", 0)),
                irreversible_frontier=str(item.get("irreversible_frontier", "")),
                evidence=(
                    dict(item.get("evidence", {}))
                    if isinstance(item.get("evidence"), Mapping)
                    else {}
                ),
                evidence_sha256=str(item.get("evidence_sha256", "")),
                failure_type=str(item.get("failure_type", "")),
            )
            for item in raw_phases
            if isinstance(item, Mapping)
        )
        receipt = NodeGroupMigrationReceipt(
            schema=str(payload.get("schema", "")),
            status=str(payload.get("status", "")),
            recovery_mode=str(payload.get("recovery_mode", "")),
            intent_sha256=str(payload.get("intent_sha256", "")),
            intent=(
                dict(payload.get("intent", {}))
                if isinstance(payload.get("intent"), Mapping)
                else {}
            ),
            target_selector=str(payload.get("target_selector", "")),
            cluster_id=str(payload.get("cluster_id", "")),
            kubernetes_uid=str(payload.get("kubernetes_uid", "")),
            maintenance=str(payload.get("maintenance", "")),
            maintenance_evidence=(
                dict(payload.get("maintenance_evidence", {}))
                if isinstance(payload.get("maintenance_evidence"), Mapping)
                else {}
            ),
            config_generations=tuple(
                config_transition_from_payload(item)
                for item in _mapping_sequence(
                    payload.get("config_generations", ()),
                    label="config generations",
                )
            ),
            phases=phases,
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("node-group migration receipt is invalid") from exc
    if (
        receipt.schema != NODE_GROUP_MIGRATION_RECEIPT_SCHEMA
        or receipt.status not in {"active", "complete"}
        or receipt.recovery_mode not in {"pre-cutover", "forward-only", "complete"}
        or receipt.maintenance not in {"pending", "entering", "active", "restoring", "restored"}
        or not _SHA256.fullmatch(receipt.intent_sha256)
        or _sha256(receipt.intent) != receipt.intent_sha256
        or len(phases) != len(raw_phases)
        or tuple(phase.name for phase in receipt.phases) != MIGRATION_PHASES
        or any(
            phase.status not in {"pending", "running", "failed", "complete"}
            for phase in receipt.phases
        )
    ):
        raise RuntimeError("node-group migration receipt has an invalid contract")
    intent = node_group_migration_intent_from_payload(receipt.intent)
    validate_config_transition_chain(
        receipt.config_generations,
        initial_config_sha256=intent.source_config_sha256,
    )
    for phase in phases:
        evidence = dict(phase.evidence or {})
        if phase.evidence_sha256 and _sha256(evidence) != phase.evidence_sha256:
            raise RuntimeError("node-group migration phase evidence digest does not match")
        if phase.status == "complete" and not phase.evidence_sha256:
            raise RuntimeError("completed node-group migration phase has no evidence digest")
    maintenance_phase_started = any(
        phase.name
        in {"dual-placement-ready", "cutover-complete", "source-retired", "final-readiness"}
        and phase.status in {"running", "failed", "complete"}
        for phase in phases
    )
    if maintenance_phase_started and receipt.maintenance == "pending":
        raise RuntimeError("node-group migration phase advanced without maintenance authority")
    return receipt


def load_node_group_migration_receipt(path: Path) -> NodeGroupMigrationReceipt | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return _from_payload(read_owner_only_json(path, label="node-group migration receipt"))


def _write(path: Path, receipt: NodeGroupMigrationReceipt) -> None:
    write_owner_only_json(path, asdict(receipt))


@dataclass(frozen=True)
class MigrationConfigTransitionStore:
    """Bind generic config-generation recovery to one migration receipt."""

    path: Path
    intent: NodeGroupMigrationIntent

    def get(self, stage: str) -> ConfigGenerationTransition | None:
        receipt = load_node_group_migration_receipt(self.path)
        if receipt is None or receipt.intent_sha256 != self.intent.digest:
            raise RuntimeError("node-group migration config authority receipt is unavailable")
        return next(
            (item for item in receipt.config_generations if item.stage == stage),
            None,
        )

    def record(self, transition: ConfigGenerationTransition) -> None:
        receipt = load_node_group_migration_receipt(self.path)
        if receipt is None or receipt.intent_sha256 != self.intent.digest:
            raise RuntimeError("node-group migration config authority receipt is unavailable")
        generations = upsert_config_transition(
            receipt.config_generations,
            transition,
            initial_config_sha256=self.intent.source_config_sha256,
        )
        _write(
            self.path,
            replace(receipt, config_generations=generations, updated_at=_utc_now()),
        )


def create_or_resume_node_group_migration(
    *,
    path: Path,
    intent: NodeGroupMigrationIntent,
) -> NodeGroupMigrationReceipt:
    validate_node_group_migration_intent(intent)
    receipt = load_node_group_migration_receipt(path)
    if receipt is None:
        receipt = _new_receipt(intent)
        _write(path, receipt)
        return receipt
    if (
        receipt.intent_sha256 != intent.digest
        or receipt.target_selector != intent.target_selector
        or receipt.cluster_id != intent.cluster_id
        or receipt.kubernetes_uid != intent.kubernetes_uid
    ):
        raise RuntimeError(
            "recovery-required: the unfinished node-group migration belongs to "
            "different frozen intent"
        )
    return receipt


def run_node_group_migration(
    *,
    path: Path,
    intent: NodeGroupMigrationIntent,
    phase_executors: Mapping[str, Callable[[], MigrationPhaseResult]],
    enter_maintenance: Callable[
        [Callable[[Mapping[str, Any]], None], Mapping[str, Any]],
        Mapping[str, Any],
    ],
    restore_maintenance: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    assert_fence: Callable[[], None],
) -> NodeGroupMigrationReceipt:
    """Run or resume the exact replacement; never roll back after cutover."""

    receipt = create_or_resume_node_group_migration(path=path, intent=intent)
    if receipt.status == "complete":
        return receipt
    missing = [phase.name for phase in receipt.phases if phase.name not in phase_executors]
    if missing:
        raise RuntimeError(
            "node-group migration has no executor for frozen phase(s): " + ", ".join(missing)
        )
    for index, phase in enumerate(receipt.phases):
        if phase.status == "complete":
            continue
        if phase.name == "dual-placement-ready" and receipt.maintenance in {
            "pending",
            "entering",
        }:
            if receipt.maintenance == "pending":
                receipt = replace(
                    receipt,
                    maintenance="entering",
                    maintenance_evidence={"events": []},
                    updated_at=_utc_now(),
                )
                _write(path, receipt)
            raw_events = receipt.maintenance_evidence.get("events", ())
            if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
                raise RuntimeError("node-group migration maintenance journal is invalid")
            events = [dict(item) for item in raw_events if isinstance(item, Mapping)]
            if len(events) != len(raw_events):
                raise RuntimeError("node-group migration maintenance journal is invalid")

            def _record_maintenance_event(
                event: Mapping[str, Any],
                journal_events: list[dict[str, Any]] = events,
            ) -> None:
                nonlocal receipt
                journal_events.append(dict(event))
                receipt = replace(
                    receipt,
                    maintenance_evidence={"events": journal_events},
                    updated_at=_utc_now(),
                )
                _write(path, receipt)

            assert_fence()
            summary = enter_maintenance(
                _record_maintenance_event,
                receipt.maintenance_evidence,
            )
            if not isinstance(summary, Mapping):
                raise RuntimeError("node-group migration maintenance returned invalid evidence")
            receipt = replace(
                receipt,
                maintenance="active",
                maintenance_evidence={"summary": dict(summary), "events": events},
                updated_at=_utc_now(),
            )
            _write(path, receipt)
        assert_fence()
        running = replace(
            phase,
            status="running",
            attempts=phase.attempts + 1,
            failure_type="",
        )
        phases = list(receipt.phases)
        phases[index] = running
        recovery_mode = "forward-only" if phase.name == CUTOVER_PHASE else receipt.recovery_mode
        receipt = replace(
            receipt,
            phases=tuple(phases),
            recovery_mode=recovery_mode,
            updated_at=_utc_now(),
        )
        _write(path, receipt)
        try:
            result = phase_executors[phase.name]()
            if not isinstance(result, MigrationPhaseResult):
                raise RuntimeError(
                    f"node-group migration phase {phase.name} returned invalid evidence"
                )
            assert_fence()
        except Exception as exc:
            refreshed = load_node_group_migration_receipt(path)
            if refreshed is None or refreshed.intent_sha256 != intent.digest:
                raise RuntimeError(
                    "node-group migration receipt disappeared during a phase"
                ) from exc
            receipt = refreshed
            phases = list(receipt.phases)
            phases[index] = replace(running, status="failed", failure_type=type(exc).__name__)
            receipt = replace(receipt, phases=tuple(phases), updated_at=_utc_now())
            _write(path, receipt)
            raise
        refreshed = load_node_group_migration_receipt(path)
        if refreshed is None or refreshed.intent_sha256 != intent.digest:
            raise RuntimeError("node-group migration receipt disappeared during a phase")
        receipt = refreshed
        evidence = dict(result.evidence)
        complete = replace(
            running,
            status="complete",
            irreversible_frontier=str(result.irreversible_frontier).strip(),
            evidence=evidence,
            evidence_sha256=_sha256(evidence),
            failure_type="",
        )
        phases = list(receipt.phases)
        phases[index] = complete
        receipt = replace(
            receipt,
            phases=tuple(phases),
            updated_at=_utc_now(),
        )
        _write(path, receipt)
    if receipt.maintenance not in {"active", "restoring"}:
        raise RuntimeError("node-group migration completed phases without active maintenance")
    receipt = replace(receipt, maintenance="restoring", updated_at=_utc_now())
    _write(path, receipt)
    assert_fence()
    restoration = restore_maintenance(receipt.maintenance_evidence)
    if not isinstance(restoration, Mapping):
        raise RuntimeError("node-group migration maintenance restoration is invalid")
    receipt = replace(
        receipt,
        status="complete",
        recovery_mode="complete",
        maintenance="restored",
        maintenance_evidence={
            "entry": dict(receipt.maintenance_evidence),
            "restoration": dict(restoration),
        },
        updated_at=_utc_now(),
    )
    _write(path, receipt)
    return receipt


__all__ = [
    "CUTOVER_PHASE",
    "MIGRATION_PHASES",
    "MigrationPhaseReceipt",
    "MigrationPhaseResult",
    "MigrationConfigTransitionStore",
    "NODE_GROUP_MIGRATION_INTENT_SCHEMA",
    "NODE_GROUP_MIGRATION_RECEIPT_SCHEMA",
    "NodeGroupMigrationIntent",
    "NodeGroupMigrationReceipt",
    "build_node_group_migration_intent",
    "create_or_resume_node_group_migration",
    "legacy_node_group_checkpoint_path",
    "load_node_group_migration_receipt",
    "node_group_migration_intent_from_payload",
    "node_group_migration_receipt_path",
    "refuse_legacy_node_group_checkpoint",
    "run_node_group_migration",
    "validate_node_group_migration_intent",
]
