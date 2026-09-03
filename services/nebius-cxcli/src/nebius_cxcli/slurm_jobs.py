"""Shared helpers for Soperator Slurm job gates."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

_EMPTY_SLURM_VALUES = {"", "-", "(NULL)", "N/A", "NONE", "NOT_SET", "UNLIMITED"}
_PENDING_STATES = {"PD", "PENDING"}
_TERMINATING_STATES = {"CG", "COMPLETING"}
_ACTIVE_STATES = {
    "BF",
    "BOOT_FAIL",
    "CF",
    "CONFIGURING",
    "CG",
    "COMPLETING",
    "R",
    "RUNNING",
    "S",
    "SUSPENDED",
    "ST",
    "STOPPED",
}
_REQUEUEHOLD_ACTIVE_STATES = {
    "CF",
    "CONFIGURING",
    "R",
    "RUNNING",
    "S",
    "SUSPENDED",
    "ST",
    "STOPPED",
}

# Slurm exposes no resource version for partitions.  Pause ownership therefore
# compares the fields customers can configure, including State, and also keeps
# every unknown field in the guarded view so a newly introduced Slurm field
# fails closed until it is classified.  Only these controller-derived topology
# summaries are excluded because they legitimately change while workers are
# replaced and the controller is upgraded.
SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS = frozenset(
    {
        "NodeIndices",
        "TotalCPUs",
        "TotalNodes",
        "TRES",
    }
)
SLURM_PARTITION_MIGRATION_TOPOLOGY_FIELDS = SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS | {
    "Nodes",
    "NodeSets",
}
SLURM_PARTITION_MIGRATION_VERSION_DEFAULTS = {
    "PowerDownOnIdle": "NO",
    "ResumeTimeout": "GLOBAL",
    "SuspendTimeout": "GLOBAL",
    "SuspendTime": "GLOBAL",
}
# Slurm 25.11 can render an explicitly configured per-node unlimited memory
# policy through the mutually exclusive per-CPU field with INT64_MAX.  Slurm
# 26.05 fixed this output formatting.  Normalize only that exact representation
# during the authenticated cross-version restore; every finite memory value and
# every other memory-field transition remains guarded.
SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_FIELDS = {
    "DefMemPerNode": "DefMemPerCPU",
    "MaxMemPerNode": "MaxMemPerCPU",
}
SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_OUTPUT_ALIASES = {
    per_cpu: per_node
    for per_node, per_cpu in SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_FIELDS.items()
}
SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_SENTINEL = "9223372036854775807"
SLURM_PARTITION_MIGRATION_MEMORY_FIELDS = frozenset(
    (
        *SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_FIELDS.keys(),
        *SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_FIELDS.values(),
    )
)
SLURM_PARTITION_CUSTOMER_OWNED_FIELDS = frozenset(
    {
        "AllocNodes",
        "AllowAccounts",
        "AllowGroups",
        "AllowQOS",
        "AllowQos",
        "Alternate",
        "CpuBind",
        "Default",
        "DefaultTime",
        "DefMemPerCPU",
        "DefCpuPerGPU",
        "DefMemPerGPU",
        "DefMemPerNode",
        "DenyAccounts",
        "DenyQOS",
        "DenyQos",
        "DisableRootJobs",
        "Exclusive",
        "ExclusiveTopo",
        "ExclusiveUser",
        "GraceTime",
        "Hidden",
        "JobDefaults",
        "LLN",
        "MaxCPUsPerNode",
        "MaxCPUsPerSocket",
        "MaxMemPerCPU",
        "MaxMemPerNode",
        "MaxNodes",
        "MaxTime",
        "MinNodes",
        "Nodes",
        "NodeSets",
        "OverSubscribe",
        "OverTimeLimit",
        "PartitionName",
        "PowerDownOnIdle",
        "PreemptMode",
        "PriorityJobFactor",
        "PriorityTier",
        "QoS",
        "ReqResv",
        "ResumeTimeout",
        "RootOnly",
        "SelectTypeParameters",
        "State",
        "SuspendTimeout",
        "SuspendTime",
        "Topology",
        "TRESBillingWeights",
    }
)


@dataclass(frozen=True)
class AffectedSlurmJob:
    job_id: str
    user: str
    state: str
    partition: str
    allocated_nodes: str
    requested_nodes: str
    scheduled_nodes: str
    reason: str
    elapsed: str
    limit: str
    remaining: str
    name: str
    impact_scope: str


@dataclass(frozen=True)
class SlurmJobControlRecord:
    """Authoritative identity and control state from ``scontrol show job -o``."""

    job_id: str
    user_id: str
    state: str
    batch_flag: int
    submit_time: str
    job_name: str
    job_id_raw: str = ""
    array_job_id: str = ""
    array_task_id: str = ""
    het_job_id: str = ""
    het_job_offset: str = ""
    priority: int | None = None
    reason: str = ""

    @property
    def identity_sha256(self) -> str:
        fields = (
            self.job_id,
            self.job_id_raw,
            self.user_id,
            self.submit_time,
            self.job_name,
            self.array_job_id,
            self.array_task_id,
            self.het_job_id,
            self.het_job_offset,
        )
        return "sha256:" + hashlib.sha256("\0".join(fields).encode()).hexdigest()

    def as_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_id_raw": self.job_id_raw,
            "user_id": self.user_id,
            "state": self.state,
            "batch_flag": self.batch_flag,
            "submit_time": self.submit_time,
            "job_name": self.job_name,
            "array_job_id": self.array_job_id,
            "array_task_id": self.array_task_id,
            "het_job_id": self.het_job_id,
            "het_job_offset": self.het_job_offset,
            "priority": self.priority,
            "reason": self.reason,
            "identity_sha256": self.identity_sha256,
        }


def _scontrol_record_fields(record: str) -> dict[str, str]:
    text = str(record or "").strip()
    matches = tuple(re.finditer(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=", text))
    if not matches:
        raise ValueError("Slurm job control record contains no key=value fields")
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1)
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if key in fields:
            raise ValueError(f"Slurm job control record contains duplicate field {key}")
        fields[key] = text[value_start:value_end].strip()
    return fields


def parse_scontrol_show_job_record(output: str) -> SlurmJobControlRecord:
    """Parse one exact job record without treating mutable state as identity."""

    records = tuple(line.strip() for line in str(output or "").splitlines() if line.strip())
    if len(records) != 1:
        raise ValueError("Slurm job control query must return exactly one record")
    fields = _scontrol_record_fields(records[0])
    required = ("JobId", "UserId", "JobState", "BatchFlag", "SubmitTime", "JobName")
    missing = tuple(field for field in required if not clean_slurm_value(fields.get(field, "")))
    if missing:
        raise ValueError("Slurm job control record is missing: " + ", ".join(missing))
    try:
        batch_flag = int(fields["BatchFlag"])
    except ValueError as exc:
        raise ValueError("Slurm job control BatchFlag is invalid") from exc
    priority: int | None = None
    if clean_slurm_value(fields.get("Priority", "")):
        try:
            priority = int(fields["Priority"])
        except ValueError as exc:
            raise ValueError("Slurm job control Priority is invalid") from exc
    record = SlurmJobControlRecord(
        job_id=fields["JobId"],
        job_id_raw=clean_slurm_value(fields.get("JobIdRaw", "")),
        user_id=fields["UserId"],
        state=fields["JobState"],
        batch_flag=batch_flag,
        submit_time=fields["SubmitTime"],
        job_name=fields["JobName"],
        array_job_id=clean_slurm_value(fields.get("ArrayJobId", "")),
        array_task_id=clean_slurm_value(fields.get("ArrayTaskId", "")),
        het_job_id=clean_slurm_value(fields.get("HetJobId", "")),
        het_job_offset=clean_slurm_value(fields.get("HetJobOffset", "")),
        priority=priority,
        reason=clean_slurm_value(fields.get("Reason", "")),
    )
    return record


def slurm_job_control_record_from_payload(payload: Mapping[str, Any]) -> SlurmJobControlRecord:
    """Load a journaled control record and verify its immutable identity digest."""

    try:
        batch_flag_value = payload.get("batch_flag")
        if batch_flag_value is None:
            raise ValueError("missing batch flag")
        priority_value = payload.get("priority")
        record = SlurmJobControlRecord(
            job_id=str(payload.get("job_id") or "").strip(),
            job_id_raw=str(payload.get("job_id_raw") or "").strip(),
            user_id=str(payload.get("user_id") or "").strip(),
            state=str(payload.get("state") or "").strip(),
            batch_flag=int(batch_flag_value),
            submit_time=str(payload.get("submit_time") or "").strip(),
            job_name=str(payload.get("job_name") or "").strip(),
            array_job_id=str(payload.get("array_job_id") or "").strip(),
            array_task_id=str(payload.get("array_task_id") or "").strip(),
            het_job_id=str(payload.get("het_job_id") or "").strip(),
            het_job_offset=str(payload.get("het_job_offset") or "").strip(),
            priority=None if priority_value is None else int(priority_value),
            reason=str(payload.get("reason") or "").strip(),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Slurm held-job journal record is invalid") from exc
    if not all((record.job_id, record.user_id, record.state, record.submit_time, record.job_name)):
        raise ValueError("Slurm held-job journal record is incomplete")
    if str(payload.get("identity_sha256") or "") != record.identity_sha256:
        raise ValueError("Slurm held-job journal identity digest does not match")
    return record


def slurm_job_control_record_from_query(
    *,
    requested_job_id: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> SlurmJobControlRecord | None:
    """Validate one ``scontrol show job -o`` result for an exact requested ID."""

    selected = str(requested_job_id or "").strip()
    if not selected:
        raise ValueError("Slurm job identity is empty")
    detail = str(stderr or stdout or "")
    if returncode != 0:
        if "invalid job id" in detail.lower() or "slurm_load_jobs error" in detail.lower():
            return None
        raise RuntimeError("Could not inspect authoritative Slurm job details")
    if not str(stdout or "").strip():
        return None
    try:
        record = parse_scontrol_show_job_record(stdout)
    except ValueError as exc:
        raise RuntimeError("Slurm returned an invalid authoritative job record") from exc
    if record.job_id != selected:
        raise RuntimeError(
            "recovery-required: Slurm returned a different job identity than requested"
        )
    return record


def slurm_requeuehold_eligibility(record: SlurmJobControlRecord) -> tuple[bool, str]:
    """Classify only Slurm-documented batch jobs and incomplete active states."""

    if record.batch_flag < 1:
        return False, "not a batch job"
    state = state_token(record.state)
    if state in _TERMINATING_STATES:
        return False, "job is completing"
    if state not in _REQUEUEHOLD_ACTIVE_STATES:
        return False, f"state {record.state} is not safely requeueable"
    return True, "eligible running batch job"


def slurm_job_control_is_held(record: SlurmJobControlRecord) -> bool:
    return (
        state_token(record.state) in _PENDING_STATES
        and record.priority == 0
        and record.reason.upper().startswith("JOBHELD")
    )


def applied_slurm_held_job_records(
    events: Sequence[Mapping[str, Any]],
) -> tuple[SlurmJobControlRecord, ...]:
    """Return only exact applied held-job bindings, never write-ahead intents."""

    applied_actions = {
        "requeue-hold-applied",
        "requeue-hold-selected-applied",
        "requeue-hold-all-applied",
    }
    records: dict[str, SlurmJobControlRecord] = {}
    for event in events:
        if str(event.get("action") or "").strip() not in applied_actions:
            continue
        payloads = event.get("job_control_postimages")
        if not isinstance(payloads, Sequence) or isinstance(payloads, (str, bytes)):
            if event.get("job_ids") or event.get("jobs"):
                raise RuntimeError(
                    "recovery-required: an applied Slurm hold lacks an exact job identity "
                    "postimage; automatic release is unsafe"
                )
            continue
        for payload in payloads:
            if not isinstance(payload, Mapping):
                raise RuntimeError(
                    "recovery-required: the applied Slurm held-job postimage is invalid"
                )
            try:
                record = slurm_job_control_record_from_payload(payload)
            except ValueError as exc:
                raise RuntimeError(
                    "recovery-required: the applied Slurm held-job identity is invalid"
                ) from exc
            if not slurm_job_control_is_held(record):
                raise RuntimeError("recovery-required: the applied Slurm job postimage is not held")
            previous = records.get(record.job_id)
            if previous is not None and previous.identity_sha256 != record.identity_sha256:
                raise RuntimeError(
                    "recovery-required: the Slurm held-job journal contains reused job IDs"
                )
            records[record.job_id] = record
    return tuple(records[job_id] for job_id in sorted(records))


@dataclass(frozen=True)
class SlurmPartitionState:
    name: str
    state: str
    record: str
    record_fingerprint: str
    nodes: str = ""

    def __post_init__(self) -> None:
        canonical = canonical_slurm_partition_record(self.record)
        if canonical != self.record:
            raise ValueError("Slurm partition state record must use canonical token ordering.")
        if slurm_partition_record_fingerprint(canonical) != self.record_fingerprint:
            raise ValueError("Slurm partition state record fingerprint does not match its record.")
        fields = _slurm_partition_record_fields(canonical)
        if clean_slurm_value(fields.get("PartitionName", "")) != self.name:
            raise ValueError("Slurm partition state record name does not match its parsed name.")
        if slurm_partition_state_token(fields.get("State", "")) != slurm_partition_state_token(
            self.state
        ):
            raise ValueError("Slurm partition state record state does not match its parsed state.")


@dataclass(frozen=True)
class SlurmPartitionPauseRecord:
    partition: str
    previous_state: str
    previous_record: str
    previous_record_fingerprint: str
    applied_state: str = "DOWN"
    applied_record: str = ""
    applied_record_fingerprint: str = ""

    def __post_init__(self) -> None:
        previous = canonical_slurm_partition_record(self.previous_record)
        if previous != self.previous_record:
            raise ValueError("Slurm pause previous record must use canonical token ordering.")
        if slurm_partition_record_fingerprint(previous) != self.previous_record_fingerprint:
            raise ValueError("Slurm pause previous fingerprint does not match its record.")
        previous_fields = _slurm_partition_record_fields(previous)
        if clean_slurm_value(previous_fields.get("PartitionName", "")) != self.partition:
            raise ValueError("Slurm pause previous record belongs to another partition.")
        if slurm_partition_state_token(
            previous_fields.get("State", "")
        ) != slurm_partition_state_token(self.previous_state):
            raise ValueError("Slurm pause previous record has a different state.")
        if bool(self.applied_record) != bool(self.applied_record_fingerprint):
            raise ValueError("Slurm pause applied record and fingerprint must be present together.")
        if not self.applied_record:
            return
        applied = canonical_slurm_partition_record(self.applied_record)
        if applied != self.applied_record:
            raise ValueError("Slurm pause applied record must use canonical token ordering.")
        if slurm_partition_record_fingerprint(applied) != self.applied_record_fingerprint:
            raise ValueError("Slurm pause applied fingerprint does not match its record.")
        applied_fields = _slurm_partition_record_fields(applied)
        if clean_slurm_value(applied_fields.get("PartitionName", "")) != self.partition:
            raise ValueError("Slurm pause applied record belongs to another partition.")
        if slurm_partition_state_token(
            applied_fields.get("State", "")
        ) != slurm_partition_state_token(self.applied_state):
            raise ValueError("Slurm pause applied record has a different state.")
        if not slurm_partition_owned_fields_match(previous, applied, include_state=False):
            raise ValueError(
                "Slurm pause applied record changed a customer-owned or unknown field."
            )

    def as_payload(self) -> dict[str, str]:
        return {
            "partition": self.partition,
            "previous_state": self.previous_state,
            "previous_record": self.previous_record,
            "previous_record_fingerprint": self.previous_record_fingerprint,
            "applied_state": self.applied_state,
            "applied_record": self.applied_record,
            "applied_record_fingerprint": self.applied_record_fingerprint,
        }

    def with_applied_observation(
        self,
        observation: SlurmPartitionState,
    ) -> SlurmPartitionPauseRecord:
        if observation.name != self.partition:
            raise ValueError("Slurm pause observation belongs to another partition.")
        if slurm_partition_state_token(observation.state) != slurm_partition_state_token(
            self.applied_state
        ):
            raise ValueError("Slurm pause observation does not show the applied state.")
        return replace(
            self,
            applied_record=observation.record,
            applied_record_fingerprint=observation.record_fingerprint,
        )


def canonical_slurm_partition_record(record: str) -> str:
    """Canonicalize one raw `scontrol show partition -o` evidence record.

    Full raw records and their fingerprints remain durable evidence. Pause
    ownership uses :func:`canonical_slurm_partition_owned_record` so known
    controller-derived topology summaries do not make a safe restore impossible.
    """

    tokens = str(record or "").strip().split()
    if not tokens or any("=" not in token for token in tokens):
        raise ValueError("Slurm partition record must contain only key=value tokens.")
    return " ".join(sorted(tokens))


def slurm_partition_record_fingerprint(record: str) -> str:
    canonical = canonical_slurm_partition_record(record)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_slurm_partition_owned_record(
    record: str,
    *,
    include_state: bool = True,
) -> str:
    """Return the fail-closed partition pause ownership view.

    Known customer-configurable fields are explicitly catalogued above. Unknown
    fields remain in this view and therefore invalidate ownership when they
    change. Only the explicit controller-derived topology fields are ignored.
    ``State`` is included for normal compare-and-set checks and can be excluded
    only while validating the immediate ``UP`` to ``DOWN`` mutation itself.
    """

    customer_owned: list[str] = []
    unknown_guarded: list[str] = []
    for token in canonical_slurm_partition_record(record).split():
        key, _value = token.split("=", 1)
        if key in SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS:
            continue
        if key == "State" and not include_state:
            continue
        if key in SLURM_PARTITION_CUSTOMER_OWNED_FIELDS:
            customer_owned.append(token)
        else:
            unknown_guarded.append(token)
    return " ".join(sorted((*customer_owned, *unknown_guarded)))


def slurm_partition_owned_fields_match(
    left: str,
    right: str,
    *,
    include_state: bool = True,
) -> bool:
    """Compare partition pause ownership while ignoring only known derived fields."""

    return canonical_slurm_partition_owned_record(
        left,
        include_state=include_state,
    ) == canonical_slurm_partition_owned_record(
        right,
        include_state=include_state,
    )


def canonical_slurm_partition_migration_record(
    record: str,
    *,
    include_state: bool = True,
) -> str:
    """Return the guarded cross-version partition restore view.

    A static NodeSet migration intentionally changes the target node names and
    NodeSet membership, and newer Slurm versions expose a small set of explicit
    defaults that older versions omitted. Every other known and unknown field
    stays guarded. This view is deliberately separate from the strict immediate
    pause ownership view above.
    """

    fields = _slurm_partition_record_fields(record)
    normalized: dict[str, str] = {}
    aliases = {
        "AllowQOS": "AllowQos",
        "DenyQOS": "DenyQos",
    }
    for key, value in fields.items():
        if key in SLURM_PARTITION_MIGRATION_TOPOLOGY_FIELDS:
            continue
        if key == "State" and not include_state:
            continue
        normalized_key = aliases.get(key, key)
        if normalized_key in normalized and normalized[normalized_key] != value:
            raise ValueError("Slurm partition migration record contains conflicting aliases.")
        normalized[normalized_key] = value
    for per_node_key, per_cpu_key in SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_FIELDS.items():
        if normalized.get(per_node_key, "").upper() != "UNLIMITED":
            continue
        if per_cpu_key in normalized:
            raise ValueError(
                "Slurm partition migration record contains mutually exclusive memory fields."
            )
        normalized.pop(per_node_key)
        normalized[per_cpu_key] = SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_SENTINEL
    for key, value in SLURM_PARTITION_MIGRATION_VERSION_DEFAULTS.items():
        normalized.setdefault(key, value)
    return " ".join(f"{key}={normalized[key]}" for key in sorted(normalized))


def slurm_partition_migration_fields_match(
    left: str,
    right: str,
    *,
    include_state: bool = True,
) -> bool:
    """Compare the exact guarded view at a cross-version topology restore."""

    return canonical_slurm_partition_migration_record(
        left,
        include_state=include_state,
    ) == canonical_slurm_partition_migration_record(
        right,
        include_state=include_state,
    )


def slurm_partition_alloc_nodes_is_all(record: str) -> bool:
    """Return whether the guarded partition view permits every submit host.

    Cross-version Slurm state can render both the canonical unrestricted value
    and a stale literal ``ALL`` hostlist as ``AllocNodes=ALL``.  Reapplying the
    documented sentinel through ``scontrol update`` normalizes the controller's
    internal value without changing the customer-visible partition policy.
    """

    fields = _slurm_partition_record_fields(record)
    return clean_slurm_value(fields.get("AllocNodes", "")).upper() == "ALL"


def slurm_partition_configuration_from_preimage(
    partitions: Sequence[SlurmPartitionState],
    *,
    desired_state: str = "DOWN",
    output_sentinel_policy: str = "target-config",
) -> dict[str, object]:
    """Project a frozen live partition set into target structured values.

    Target topology membership is derived from its static NodeSets, while the
    customer scheduling policy is carried forward. The rendered partitions
    remain DOWN until the durable pause journal restores their frozen states.
    Output-only null sentinels are omitted because they are not valid Slurm
    configuration values. Unlimited memory output is rendered as Slurm's
    explicit numeric-zero sentinel so it cannot inherit a finite cluster-wide
    default. Unknown fields fail closed instead of being dropped.
    """

    state = str(desired_state or "").strip().upper()
    if not state or re.search(r"[\s=]", state):
        raise ValueError("Slurm partition migration state must be one token.")
    if output_sentinel_policy not in {"legacy-v1", "target-config"}:
        raise ValueError("Slurm partition migration output sentinel policy is unsupported.")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    omitted_output_values = (
        frozenset({"(NULL)", "N/A"})
        if output_sentinel_policy == "legacy-v1"
        else frozenset({"-", "(NULL)", "N/A", "NONE", "NOT_SET"})
    )
    for partition in sorted(partitions, key=lambda item: item.name):
        if partition.name in seen:
            raise ValueError("Slurm partition migration preimage contains duplicates.")
        seen.add(partition.name)
        fields = _slurm_partition_record_fields(partition.record)
        unknown = sorted(
            set(fields)
            - SLURM_PARTITION_CUSTOMER_OWNED_FIELDS
            - SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS
        )
        if unknown:
            raise ValueError(
                "Slurm partition migration encountered unsupported guarded fields: "
                + ", ".join(unknown)
            )
        for per_node_key, per_cpu_key in SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_FIELDS.items():
            if per_node_key in fields and per_cpu_key in fields:
                raise ValueError(
                    "Slurm partition migration record contains mutually exclusive memory fields."
                )
        config_fields: dict[str, str] = {}
        for key, value in fields.items():
            if key in {
                "PartitionName",
                "Nodes",
                "NodeSets",
                *SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS,
            }:
                continue
            if value.upper() in omitted_output_values:
                continue
            if (
                output_sentinel_policy == "target-config"
                and key in (SLURM_PARTITION_MIGRATION_MEMORY_FIELDS)
                and value.upper()
                in {
                    "UNLIMITED",
                    SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_SENTINEL,
                }
            ):
                # Slurm configuration uses numeric zero for an explicit
                # unlimited partition policy. Slurm 25.11 can render a
                # per-node zero through the mutually exclusive per-CPU field
                # as INT64_MAX, so map that output alias back to the per-node
                # key instead of replaying the impossible numeric limit.
                target_key = (
                    SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_OUTPUT_ALIASES.get(key, key)
                    if value.upper() == SLURM_PARTITION_MIGRATION_UNLIMITED_MEMORY_SENTINEL
                    else key
                )
                config_fields[target_key] = "0"
                continue
            config_fields[key] = state if key == "State" else value
        config_fields["State"] = state
        config = " ".join(f"{key}={config_fields[key]}" for key in sorted(config_fields))
        result.append(
            {
                "name": partition.name,
                "isAll": True,
                "config": config,
            }
        )
    if not result:
        raise ValueError("Slurm partition migration preimage is empty.")
    return {"configType": "structured", "partitions": result}


def _slurm_partition_record_fields(record: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in canonical_slurm_partition_record(record).split():
        key, value = token.split("=", 1)
        if key in fields:
            raise ValueError("Slurm partition record contains a duplicate field.")
        fields[key] = value
    return fields


def clean_slurm_value(value: str) -> str:
    text = str(value or "").strip()
    if text.upper() in _EMPTY_SLURM_VALUES:
        return ""
    return text


def parse_squeue_jobs(output: str, *, impact_scope: str) -> tuple[AffectedSlurmJob, ...]:
    jobs: list[AffectedSlurmJob] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", 11)
        if len(parts) >= 12:
            (
                job_id,
                user,
                state,
                partition,
                allocated_nodes,
                requested_nodes,
                scheduled_nodes,
                reason,
                elapsed,
                limit_value,
                remaining,
                name,
            ) = parts[:12]
        else:
            legacy_parts = line.split("|", 8)
            while len(legacy_parts) < 9:
                legacy_parts.append("")
            (
                job_id,
                user,
                state,
                partition,
                allocated_nodes,
                elapsed,
                limit_value,
                remaining,
                name,
            ) = legacy_parts[:9]
            requested_nodes = ""
            scheduled_nodes = ""
            reason = ""
        jobs.append(
            AffectedSlurmJob(
                job_id=job_id.strip(),
                user=user.strip(),
                state=state.strip(),
                partition=partition.strip(),
                allocated_nodes=clean_slurm_value(allocated_nodes),
                requested_nodes=clean_slurm_value(requested_nodes),
                scheduled_nodes=clean_slurm_value(scheduled_nodes),
                reason=clean_slurm_value(reason).strip("()"),
                elapsed=elapsed.strip(),
                limit=limit_value.strip(),
                remaining=remaining.strip(),
                name=name.strip(),
                impact_scope=impact_scope,
            )
        )
    return tuple(jobs)


def state_token(state: str) -> str:
    return str(state or "").strip().upper().replace(" ", "_")


def slurm_partition_state_token(state: str) -> str:
    text = state_token(state)
    if not text:
        return ""
    return re.split(r"[^A-Z_]+", text, maxsplit=1)[0]


def slurm_job_is_pending(job: AffectedSlurmJob) -> bool:
    return state_token(job.state) in _PENDING_STATES


def slurm_job_is_held(job: AffectedSlurmJob) -> bool:
    """Return whether Slurm reports a pending job as already held."""

    return slurm_job_is_pending(job) and clean_slurm_value(job.reason).upper().startswith("JOBHELD")


def slurm_job_is_terminating(job: AffectedSlurmJob) -> bool:
    return state_token(job.state) in _TERMINATING_STATES


def slurm_job_is_active(job: AffectedSlurmJob) -> bool:
    return state_token(job.state) in _ACTIVE_STATES


def dedupe_slurm_jobs(jobs: Sequence[AffectedSlurmJob]) -> tuple[AffectedSlurmJob, ...]:
    selected: list[AffectedSlurmJob] = []
    seen: set[str] = set()
    for job in jobs:
        key = job.job_id
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(job)
    return tuple(selected)


def affected_slurm_partitions_from_scontrol_show_node(output: str) -> tuple[str, ...]:
    partitions: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = re.search(r"(?:^|\s)Partitions=(\S+)", line)
        if not match:
            continue
        for raw_partition in match.group(1).split(","):
            partition = clean_slurm_value(raw_partition)
            if partition and partition not in seen:
                seen.add(partition)
                partitions.append(partition)
    return tuple(partitions)


def parse_scontrol_show_partition_states(output: str) -> tuple[SlurmPartitionState, ...]:
    states: list[SlurmPartitionState] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = canonical_slurm_partition_record(raw_line)
            tokens = _slurm_partition_record_fields(record)
        except ValueError as exc:
            raise ValueError(
                f"Slurm partition observation line {line_number} is malformed."
            ) from exc
        name = clean_slurm_value(tokens.get("PartitionName", ""))
        state = clean_slurm_value(tokens.get("State", ""))
        if not name or not state:
            raise ValueError(f"Slurm partition observation line {line_number} is incomplete.")
        if name in seen:
            raise ValueError(
                f"Slurm partition observation line {line_number} duplicates a partition."
            )
        seen.add(name)
        states.append(
            SlurmPartitionState(
                name=name,
                state=state,
                record=record,
                record_fingerprint=slurm_partition_record_fingerprint(record),
                nodes=clean_slurm_value(tokens.get("Nodes", "")),
            )
        )
    return tuple(states)


def slurm_partition_pause_records(
    *,
    partitions: Sequence[str],
    states: Sequence[SlurmPartitionState],
    applied_state: str = "DOWN",
) -> tuple[SlurmPartitionPauseRecord, ...]:
    by_name = {state.name: state for state in states}
    records: list[SlurmPartitionPauseRecord] = []
    for raw_partition in partitions:
        partition = clean_slurm_value(raw_partition)
        if not partition:
            continue
        current = by_name.get(partition)
        if current is None:
            raise RuntimeError(
                f"Could not inspect Slurm partition `{partition}` before scheduling pause."
            )
        if slurm_partition_state_token(current.state) != "UP":
            continue
        records.append(
            SlurmPartitionPauseRecord(
                partition=partition,
                previous_state=current.state,
                previous_record=current.record,
                previous_record_fingerprint=current.record_fingerprint,
                applied_state=applied_state,
            )
        )
    return tuple(records)


def slurm_partitions_overlapping_nodes(
    *,
    states: Sequence[SlurmPartitionState],
    node_names: Sequence[str],
    fallback_partitions: Sequence[str] = (),
) -> tuple[str, ...]:
    selected_nodes = {
        clean_slurm_value(str(node or "")) for node in node_names if clean_slurm_value(str(node))
    }
    selected_fallback = [
        clean_slurm_value(str(partition or ""))
        for partition in fallback_partitions
        if clean_slurm_value(str(partition))
    ]
    partitions: list[str] = []
    seen: set[str] = set()

    def _append(partition: str) -> None:
        if partition and partition not in seen:
            seen.add(partition)
            partitions.append(partition)

    for state in states:
        nodes = clean_slurm_value(state.nodes)
        if not state.name or not nodes:
            continue
        if nodes.upper() == "ALL":
            if selected_nodes:
                _append(state.name)
            continue
        if selected_nodes.intersection(expand_slurm_hostlist(nodes)):
            _append(state.name)
    for partition in selected_fallback:
        _append(partition)
    return tuple(partitions)


def _split_hostlist_items(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "[":
            depth += 1
        elif char == "]" and depth:
            depth -= 1
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def expand_slurm_hostlist(value: str) -> tuple[str, ...]:
    text = clean_slurm_value(value)
    if not text:
        return ()
    expanded: list[str] = []
    for item in _split_hostlist_items(text):
        match = re.fullmatch(r"([^\[\]]*)\[([^\[\]]+)\]([^\[\]]*)", item)
        if not match:
            expanded.append(item)
            continue
        prefix, body, suffix = match.groups()
        for part in body.split(","):
            range_match = re.fullmatch(r"(\d+)-(\d+)", part)
            if range_match:
                start_text, end_text = range_match.groups()
                width = max(len(start_text), len(end_text))
                start = int(start_text)
                end = int(end_text)
                step = 1 if end >= start else -1
                for value_int in range(start, end + step, step):
                    expanded.append(f"{prefix}{value_int:0{width}d}{suffix}")
                continue
            expanded.append(f"{prefix}{part}{suffix}")
    return tuple(expanded)


def _node_fields_for_job(job: AffectedSlurmJob) -> tuple[str, ...]:
    return tuple(
        item
        for field in (job.allocated_nodes, job.requested_nodes, job.scheduled_nodes)
        for item in expand_slurm_hostlist(field)
    )


def pending_job_impact_scope(
    job: AffectedSlurmJob,
    *,
    affected_nodes: Sequence[str],
    affected_partitions: Sequence[str],
) -> str:
    scopes: list[str] = []
    affected_partition_set = {
        str(partition or "").strip()
        for partition in affected_partitions
        if str(partition or "").strip()
    }
    if job.partition and job.partition in affected_partition_set:
        scopes.append("pending-partition")
    affected_node_set = {
        str(node or "").strip() for node in affected_nodes if str(node or "").strip()
    }
    if affected_node_set and affected_node_set.intersection(_node_fields_for_job(job)):
        scopes.append("pending-node")
    return ",".join(scopes)


def filter_affected_pending_slurm_jobs(
    jobs: Sequence[AffectedSlurmJob],
    *,
    affected_nodes: Sequence[str],
    affected_partitions: Sequence[str],
) -> tuple[AffectedSlurmJob, ...]:
    selected: list[AffectedSlurmJob] = []
    for job in jobs:
        if not slurm_job_is_pending(job):
            continue
        impact_scope = pending_job_impact_scope(
            job,
            affected_nodes=affected_nodes,
            affected_partitions=affected_partitions,
        )
        if impact_scope:
            selected.append(replace(job, impact_scope=impact_scope))
    return tuple(selected)


def selected_display_job_ids(
    jobs: Sequence[AffectedSlurmJob],
    job_ids: Sequence[str],
    *,
    action: str,
) -> tuple[str, ...]:
    displayed = {job.job_id for job in jobs}
    selected = tuple(str(job_id or "").strip() for job_id in job_ids if str(job_id or "").strip())
    missing = tuple(job_id for job_id in selected if job_id not in displayed)
    if missing:
        raise RuntimeError(
            f"--job-policy {action} can only act on displayed affected Slurm jobs. "
            "Unknown or unrelated job id(s): " + ", ".join(missing)
        )
    return selected


def ensure_requeueable_slurm_jobs(
    jobs: Sequence[AffectedSlurmJob],
    job_ids: Sequence[str],
    *,
    action: str,
) -> tuple[str, ...]:
    selected = selected_display_job_ids(jobs, job_ids, action=action)
    pending = tuple(
        job.job_id for job in jobs if job.job_id in selected and slurm_job_is_pending(job)
    )
    if pending:
        raise RuntimeError(
            f"--job-policy {action} cannot requeue pending Slurm job(s): "
            + ", ".join(pending)
            + ". Cancel them, wait for them to start or clear, choose another job, or abort."
        )
    return selected


def slurm_remaining_seconds(value: str) -> int | None:
    text = clean_slurm_value(value)
    if not text:
        return None
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return None
    fields = text.split(":")
    try:
        if len(fields) == 3:
            hours, minutes, seconds = (int(item) for item in fields)
        elif len(fields) == 2:
            hours = 0
            minutes, seconds = (int(item) for item in fields)
        elif len(fields) == 1:
            hours = 0
            minutes = 0
            seconds = int(fields[0])
        else:
            return None
    except ValueError:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_slurm_duration_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    total = max(0, int(seconds))
    minutes, seconds_part = divmod(total, 60)
    hours, minutes_part = divmod(minutes, 60)
    days, hours_part = divmod(hours, 24)
    if days:
        return f"{days}-{hours_part:02d}:{minutes_part:02d}:{seconds_part:02d}"
    if hours_part:
        return f"{hours_part}:{minutes_part:02d}:{seconds_part:02d}"
    return f"{minutes_part}:{seconds_part:02d}"
