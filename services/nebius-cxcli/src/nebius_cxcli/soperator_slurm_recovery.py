"""Typed recovery contracts for Soperator Slurm scheduling mutations."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum

from .slurm_jobs import AffectedSlurmJob, SlurmPartitionState

SOPERATOR_SLURM_RECOVERY_SCHEMA = "nebius-cxcli.soperator-slurm-recovery.v3"


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SlurmJobPreimage:
    job: AffectedSlurmJob
    fingerprint: str

    def __post_init__(self) -> None:
        if self.fingerprint != _sha256(asdict(self.job)):
            raise ValueError("Slurm job preimage fingerprint does not match its record")

    def as_payload(self) -> dict[str, object]:
        return {"job": asdict(self.job), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class SlurmReservationPreimage:
    name: str
    record: str
    fingerprint: str

    def __post_init__(self) -> None:
        canonical = canonical_slurm_reservation_record(self.record)
        if canonical != self.record:
            raise ValueError("Slurm reservation preimage must use canonical token ordering")
        fields = _record_fields(canonical)
        if str(fields.get("ReservationName") or "") != self.name:
            raise ValueError("Slurm reservation preimage name does not match its record")
        if self.fingerprint != slurm_reservation_record_fingerprint(canonical):
            raise ValueError("Slurm reservation preimage fingerprint does not match its record")

    def as_payload(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SlurmUpgradePreimage:
    schema: str
    partitions: tuple[SlurmPartitionState, ...]
    jobs: tuple[SlurmJobPreimage, ...]
    reservations: tuple[SlurmReservationPreimage, ...]

    def __post_init__(self) -> None:
        if self.schema != SOPERATOR_SLURM_RECOVERY_SCHEMA:
            raise ValueError("Slurm upgrade preimage schema is unsupported")
        for label, identities in (
            ("partition", [item.name for item in self.partitions]),
            ("job", [item.job.job_id for item in self.jobs]),
            ("reservation", [item.name for item in self.reservations]),
        ):
            if identities != sorted(identities) or len(identities) != len(set(identities)):
                raise ValueError(f"Slurm {label} preimages must be unique and sorted")

    @property
    def receipt_sha256(self) -> str:
        return _sha256(self.as_payload(include_receipt=False))

    def as_payload(self, *, include_receipt: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": self.schema,
            "partitions": [asdict(item) for item in self.partitions],
            "jobs": [item.as_payload() for item in self.jobs],
            "reservations": [item.as_payload() for item in self.reservations],
        }
        if include_receipt:
            payload["receiptSha256"] = self.receipt_sha256
        return payload


def slurm_job_preimage(job: AffectedSlurmJob) -> SlurmJobPreimage:
    return SlurmJobPreimage(job=job, fingerprint=_sha256(asdict(job)))


def canonical_slurm_reservation_record(record: str) -> str:
    fields = _record_fields(record)
    if not fields or not fields.get("ReservationName"):
        raise ValueError("Slurm reservation record has no ReservationName")
    return " ".join(f"{key}={shlex.quote(fields[key])}" for key in sorted(fields))


def slurm_reservation_record_fingerprint(record: str) -> str:
    return _sha256(canonical_slurm_reservation_record(record))


def parse_slurm_reservation_preimages(output: str) -> tuple[SlurmReservationPreimage, ...]:
    reservations: list[SlurmReservationPreimage] = []
    for line in str(output or "").splitlines():
        normalized_line = line.strip()
        if not normalized_line:
            continue
        if normalized_line.rstrip(".") == "No reservations in the system":
            continue
        canonical = canonical_slurm_reservation_record(normalized_line)
        name = str(_record_fields(canonical)["ReservationName"])
        reservations.append(
            SlurmReservationPreimage(
                name=name,
                record=canonical,
                fingerprint=slurm_reservation_record_fingerprint(canonical),
            )
        )
    return tuple(sorted(reservations, key=lambda item: item.name))


def build_slurm_upgrade_preimage(
    *,
    partitions: Sequence[SlurmPartitionState],
    jobs: Sequence[AffectedSlurmJob],
    reservations: Sequence[SlurmReservationPreimage],
) -> SlurmUpgradePreimage:
    return SlurmUpgradePreimage(
        schema=SOPERATOR_SLURM_RECOVERY_SCHEMA,
        partitions=tuple(sorted(partitions, key=lambda item: item.name)),
        jobs=tuple(
            sorted((slurm_job_preimage(item) for item in jobs), key=lambda item: item.job.job_id)
        ),
        reservations=tuple(sorted(reservations, key=lambda item: item.name)),
    )


def slurm_upgrade_preimage_from_payload(
    payload: Mapping[str, object],
) -> SlurmUpgradePreimage:
    """Reconstruct and verify one content-complete Slurm admission preimage."""

    raw_partitions = payload.get("partitions")
    raw_jobs = payload.get("jobs")
    raw_reservations = payload.get("reservations")
    if not all(isinstance(item, list) for item in (raw_partitions, raw_jobs, raw_reservations)):
        raise RuntimeError("Soperator Slurm preimage payload is incomplete")
    try:
        partitions = tuple(
            SlurmPartitionState(**dict(item))
            for item in raw_partitions
            if isinstance(item, Mapping)
        )
        jobs = tuple(
            SlurmJobPreimage(
                job=AffectedSlurmJob(**dict(item["job"])),
                fingerprint=str(item.get("fingerprint") or ""),
            )
            for item in raw_jobs
            if isinstance(item, Mapping) and isinstance(item.get("job"), Mapping)
        )
        reservations = tuple(
            SlurmReservationPreimage(**dict(item))
            for item in raw_reservations
            if isinstance(item, Mapping)
        )
        preimage = SlurmUpgradePreimage(
            schema=str(payload.get("schema") or ""),
            partitions=partitions,
            jobs=jobs,
            reservations=reservations,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Soperator Slurm preimage payload is invalid") from exc
    if (
        len(partitions) != len(raw_partitions)
        or len(jobs) != len(raw_jobs)
        or len(reservations) != len(raw_reservations)
        or preimage.receipt_sha256 != str(payload.get("receiptSha256") or "")
    ):
        raise RuntimeError("Soperator Slurm preimage receipt identity changed")
    return preimage


_SLURM_RECORD_FIELD = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=")


def _record_fields(record: str) -> dict[str, str]:
    text = str(record or "").strip()
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError("Slurm reservation record contains malformed quoting") from exc
    fields: dict[str, str] = {}
    strict = True
    for token in tokens:
        key, separator, value = token.partition("=")
        if not separator:
            strict = False
            break
        if not key:
            raise ValueError("Slurm reservation record contains an empty field name")
        if key in fields:
            raise ValueError(
                f"Slurm reservation record contains duplicates: field {key}"
            )
        fields[key] = value
    if strict:
        return fields

    starts: list[tuple[int, str, int]] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\":
            escaped = True
            index += 1
            continue
        if quote:
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        if index == 0 or text[index - 1].isspace():
            match = _SLURM_RECORD_FIELD.match(text, index)
            if match is not None:
                starts.append((index, match.group(1), match.end()))
                index = match.end()
                continue
        index += 1
    if quote or escaped:
        raise ValueError("Slurm reservation record contains malformed quoting")
    if not starts or text[: starts[0][0]].strip():
        raise ValueError("Slurm reservation record has no valid first field")

    fields = {}
    for item_index, (_start, key, value_start) in enumerate(starts):
        value_end = starts[item_index + 1][0] if item_index + 1 < len(starts) else len(text)
        raw_value = text[value_start:value_end].strip()
        if key in fields:
            raise ValueError(
                f"Slurm reservation record contains duplicates: field {key}"
            )
        try:
            decoded = shlex.split(f"{key}={raw_value}", posix=True)
        except ValueError as exc:
            raise ValueError("Slurm reservation record contains malformed quoting") from exc
        if len(decoded) == 1:
            decoded_key, separator, value = decoded[0].partition("=")
            if separator and decoded_key == key:
                fields[key] = value
                continue
        if any(character in raw_value for character in {"'", '"', "\\"}):
            raise ValueError("Slurm reservation record contains ambiguous field quoting")
        fields[key] = raw_value
    return fields


class SlurmRecoveryDisposition(StrEnum):
    OBSERVED = "observed"
    INTENT = "intent-persisted"
    APPLIED = "applied"
    RECOVERED_APPLIED = "recovered-applied"
    SATISFIED_EXTERNAL = "satisfied-external"
    COMPLETE = "complete"


_INTENT_ACTIONS = {
    "slurm-gate-started",
    "maintenance-reservation-recorded",
    "pending-hold-recorded",
    "scheduling-pause-recorded",
    "nodes-drain-recorded",
    "requeue-hold",
    "requeue-hold-selected",
    "requeue-hold-all",
}

_HELD_JOB_COMPLETIONS = {
    "pending-hold-applied",
    "requeue-hold-applied",
    "requeue-hold-selected-applied",
    "requeue-hold-all-applied",
}


def _canonical_action(action: str) -> str:
    replacements = {
        "slurm-gate-complete": "slurm-gate-started",
        "maintenance-reservation-applied": "maintenance-reservation-recorded",
        "pending-hold-applied": "pending-hold-recorded",
        "pending-hold-not-required": "pending-hold-recorded",
        "scheduling-pause-applied": "scheduling-pause-recorded",
        "scheduling-pause-skipped": "scheduling-pause-recorded",
        "nodes-drained": "nodes-drain-recorded",
        "nodes-drain-not-required": "nodes-drain-recorded",
        "requeue-hold-applied": "requeue-hold",
        "requeue-hold-selected-applied": "requeue-hold-selected",
        "requeue-hold-all-applied": "requeue-hold-all",
    }
    return replacements.get(action, action)


def _subjects(event: Mapping[str, object]) -> list[str]:
    subjects: list[str] = []
    for key in ("job_ids", "node_names"):
        value = event.get(key)
        if isinstance(value, list):
            subjects.extend(str(item) for item in value if str(item or "").strip())
    reservation = str(event.get("reservation_name") or "").strip()
    if reservation:
        subjects.append(reservation)
    partitions = event.get("partitions")
    if isinstance(partitions, list):
        subjects.extend(
            str(item.get("partition") or "")
            for item in partitions
            if isinstance(item, Mapping) and str(item.get("partition") or "").strip()
        )
    return sorted(set(subjects))


def normalize_slurm_recovery_event(
    event: Mapping[str, object],
    *,
    fencing_epoch: int,
    disposition: SlurmRecoveryDisposition | None = None,
) -> dict[str, object]:
    """Bind an event to a deterministic action identity and fencing epoch."""

    action = str(event.get("action") or "").strip()
    namespace = str(event.get("namespace") or "").strip()
    checkpoint_id = str(event.get("checkpoint_id") or "").strip()
    if not action or not namespace or not checkpoint_id or fencing_epoch < 1:
        raise RuntimeError("Soperator Slurm recovery event identity is incomplete")
    canonical = _canonical_action(action)
    selected_disposition = disposition
    if selected_disposition is None:
        if action in _INTENT_ACTIONS:
            selected_disposition = SlurmRecoveryDisposition.INTENT
        elif action == "slurm-gate-complete":
            selected_disposition = SlurmRecoveryDisposition.COMPLETE
        elif action.endswith("-applied") or action in {
            "nodes-drained",
            "nodes-drain-not-required",
            "pending-hold-not-required",
            "scheduling-pause-skipped",
        }:
            selected_disposition = SlurmRecoveryDisposition.APPLIED
        else:
            selected_disposition = SlurmRecoveryDisposition.OBSERVED
    identity = {
        "namespace": namespace,
        "checkpointId": checkpoint_id,
        "action": canonical,
        "subjects": _subjects(event),
    }
    action_id = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    return {
        **dict(event),
        "actionId": action_id,
        "actionKind": canonical,
        "disposition": selected_disposition.value,
        "fencingEpoch": fencing_epoch,
    }


def validate_slurm_recovery_actions(actions: Sequence[object]) -> None:
    """Reject untyped, foreign, or internally inconsistent action histories."""

    for action in actions:
        if not isinstance(action, Mapping):
            raise RuntimeError("Soperator Slurm recovery action is malformed")
        action_id = str(action.get("actionId") or "")
        disposition = str(action.get("disposition") or "")
        try:
            epoch = int(action.get("fencingEpoch") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Soperator Slurm recovery action has an invalid epoch") from exc
        if (
            not action_id.startswith("sha256:")
            or len(action_id) != 71
            or disposition not in {item.value for item in SlurmRecoveryDisposition}
            or epoch < 1
        ):
            raise RuntimeError("Soperator Slurm recovery action is incomplete")
        normalized = normalize_slurm_recovery_event(
            action,
            fencing_epoch=epoch,
            disposition=SlurmRecoveryDisposition(disposition),
        )
        if (
            action_id != normalized["actionId"]
            or action.get("actionKind") != normalized["actionKind"]
        ):
            raise RuntimeError("Soperator Slurm recovery action identity changed")
        action_name = str(action.get("action") or "").strip()
        if action_name in _HELD_JOB_COMPLETIONS:
            job_ids = {
                str(item).strip()
                for item in action.get("job_ids", []) or []
                if str(item or "").strip()
            }
            jobs = action.get("jobs")
            postimage_ids = (
                {
                    str(item.get("job_id") or "").strip()
                    for item in jobs
                    if isinstance(item, Mapping) and str(item.get("job_id") or "").strip()
                }
                if isinstance(jobs, list)
                else set()
            )
            raw_external_ids = action.get("satisfied_external_job_ids", [])
            external_ids = (
                {str(item).strip() for item in raw_external_ids if str(item or "").strip()}
                if isinstance(raw_external_ids, list)
                else set()
            )
            if (
                postimage_ids & external_ids
                or postimage_ids | external_ids != job_ids
                or (
                    disposition == SlurmRecoveryDisposition.SATISFIED_EXTERNAL.value
                    and postimage_ids
                )
                or (
                    disposition != SlurmRecoveryDisposition.SATISFIED_EXTERNAL.value
                    and job_ids
                    and not postimage_ids
                )
            ):
                raise RuntimeError("Soperator Slurm held-job completion lacks an exact postimage")


__all__ = [
    "SOPERATOR_SLURM_RECOVERY_SCHEMA",
    "SlurmJobPreimage",
    "SlurmRecoveryDisposition",
    "SlurmReservationPreimage",
    "SlurmUpgradePreimage",
    "build_slurm_upgrade_preimage",
    "canonical_slurm_reservation_record",
    "normalize_slurm_recovery_event",
    "parse_slurm_reservation_preimages",
    "slurm_job_preimage",
    "slurm_upgrade_preimage_from_payload",
    "slurm_reservation_record_fingerprint",
    "validate_slurm_recovery_actions",
]
