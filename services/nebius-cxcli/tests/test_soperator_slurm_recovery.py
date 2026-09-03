from __future__ import annotations

import pytest

from nebius_cxcli.slurm_jobs import (
    AffectedSlurmJob,
    parse_scontrol_show_partition_states,
)
from nebius_cxcli.soperator_slurm_recovery import (
    SOPERATOR_SLURM_RECOVERY_SCHEMA,
    SlurmRecoveryDisposition,
    build_slurm_upgrade_preimage,
    canonical_slurm_reservation_record,
    normalize_slurm_recovery_event,
    parse_slurm_reservation_preimages,
    slurm_upgrade_preimage_from_payload,
    validate_slurm_recovery_actions,
)


def _job(*, job_id: str = "42", state: str = "PENDING", reason: str = "JobHeldUser"):
    return AffectedSlurmJob(
        job_id=job_id,
        user="alice",
        state=state,
        partition="gpu",
        allocated_nodes="",
        requested_nodes="worker-[0-1]",
        scheduled_nodes="",
        reason=reason,
        elapsed="00:00:00",
        limit="01:00:00",
        remaining="01:00:00",
        name="training",
        impact_scope="cluster",
    )


def test_upgrade_preimage_binds_complete_sorted_slurm_records() -> None:
    partitions = parse_scontrol_show_partition_states(
        "PartitionName=gpu State=UP Nodes=worker-[0-1]\n"
        "PartitionName=system State=DOWN Nodes=system-0\n"
    )
    reservations = parse_slurm_reservation_preimages(
        "ReservationName=customer Users=alice Nodes=worker-0 Flags=MAINT\n"
    )

    receipt = build_slurm_upgrade_preimage(
        partitions=partitions,
        jobs=[_job()],
        reservations=reservations,
    )

    assert receipt.schema == SOPERATOR_SLURM_RECOVERY_SCHEMA
    assert receipt.jobs[0].job.reason == "JobHeldUser"
    assert receipt.reservations[0].record == (
        "Flags=MAINT Nodes=worker-0 ReservationName=customer Users=alice"
    )
    assert receipt.as_payload()["receiptSha256"] == receipt.receipt_sha256
    assert slurm_upgrade_preimage_from_payload(receipt.as_payload()) == receipt


def test_upgrade_preimage_payload_tamper_is_rejected() -> None:
    receipt = build_slurm_upgrade_preimage(
        partitions=(),
        jobs=[_job()],
        reservations=(),
    )
    payload = receipt.as_payload()
    payload["receiptSha256"] = "sha256:" + "0" * 64

    with pytest.raises(RuntimeError, match="receipt identity changed"):
        slurm_upgrade_preimage_from_payload(payload)


def test_reservation_parser_rejects_duplicate_or_missing_identity() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        canonical_slurm_reservation_record(
            "ReservationName=customer ReservationName=other Users=alice"
        )
    with pytest.raises(ValueError, match="ReservationName"):
        canonical_slurm_reservation_record("Users=alice Nodes=worker-0")


def test_reservation_parser_accepts_unquoted_time_values_with_spaces() -> None:
    reservations = parse_slurm_reservation_preimages(
        "ReservationName=cxcli_0123456789abcdef "
        "StartTime=2026-08-29 17:36:35.000 "
        "EndTime=2026-08-30 17:36:35.000 "
        "Users=root Flags=MAINT\n"
    )

    assert len(reservations) == 1
    assert reservations[0].record == (
        "EndTime='2026-08-30 17:36:35.000' Flags=MAINT "
        "ReservationName=cxcli_0123456789abcdef "
        "StartTime='2026-08-29 17:36:35.000' Users=root"
    )
    assert slurm_upgrade_preimage_from_payload(
        build_slurm_upgrade_preimage(
            partitions=(),
            jobs=(),
            reservations=reservations,
        ).as_payload()
    ).reservations == reservations


def test_reservation_parser_does_not_split_key_like_text_inside_quotes() -> None:
    reservations = parse_slurm_reservation_preimages(
        'ReservationName=customer Comment="keep Other=value together" Users=alice\n'
    )

    assert reservations[0].record == (
        "Comment='keep Other=value together' ReservationName=customer Users=alice"
    )


@pytest.mark.parametrize(
    "output",
    ["No reservations in the system\n", "No reservations in the system.\n"],
)
def test_reservation_parser_accepts_slurm_empty_inventory(output: str) -> None:
    assert parse_slurm_reservation_preimages(output) == ()


def test_job_preimage_tamper_is_rejected() -> None:
    receipt = build_slurm_upgrade_preimage(
        partitions=(),
        jobs=[_job()],
        reservations=(),
    )
    preimage = receipt.jobs[0]

    with pytest.raises(ValueError, match="fingerprint"):
        type(preimage)(job=_job(reason="Resources"), fingerprint=preimage.fingerprint)


def test_intent_and_completion_share_one_fenced_action_identity() -> None:
    intent = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "pending-hold-recorded",
            "job_ids": ["42"],
        },
        fencing_epoch=7,
    )
    completion = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "pending-hold-applied",
            "job_ids": ["42"],
            "jobs": [{"job_id": "42"}],
        },
        fencing_epoch=7,
        disposition=SlurmRecoveryDisposition.RECOVERED_APPLIED,
    )

    assert intent["actionId"] == completion["actionId"]
    assert intent["disposition"] == "intent-persisted"
    assert completion["disposition"] == "recovered-applied"
    validate_slurm_recovery_actions([intent, completion])


def test_noop_node_drain_closes_the_write_ahead_action() -> None:
    intent = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "nodes-drain-recorded",
            "node_names": ["worker-0"],
        },
        fencing_epoch=7,
    )
    completion = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "nodes-drain-not-required",
            "node_names": ["worker-0"],
            "owned_node_names": [],
        },
        fencing_epoch=7,
    )

    assert completion["actionId"] == intent["actionId"]
    assert completion["disposition"] == "applied"


def test_untyped_or_unfenced_action_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="incomplete"):
        validate_slurm_recovery_actions(
            [
                {
                    "actionId": "",
                    "disposition": "intent-persisted",
                    "fencingEpoch": 0,
                }
            ]
        )


def test_action_identity_tampering_is_rejected() -> None:
    action = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "nodes-drain-recorded",
            "node_names": ["worker-0"],
        },
        fencing_epoch=7,
    )
    action["node_names"] = ["worker-1"]

    with pytest.raises(RuntimeError, match="identity changed"):
        validate_slurm_recovery_actions([action])


def test_non_mapping_action_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="malformed"):
        validate_slurm_recovery_actions(["discarded-action"])  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("jobs", "satisfied_external_job_ids", "disposition"),
    [
        ([], ["42", "43"], SlurmRecoveryDisposition.SATISFIED_EXTERNAL),
        ([{"job_id": "42"}], ["43"], SlurmRecoveryDisposition.RECOVERED_APPLIED),
    ],
)
def test_held_job_completion_accepts_exact_external_tombstones(
    jobs: list[dict[str, str]],
    satisfied_external_job_ids: list[str],
    disposition: SlurmRecoveryDisposition,
) -> None:
    completion = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "pending-hold-applied",
            "job_ids": ["42", "43"],
            "jobs": jobs,
            "satisfied_external_job_ids": satisfied_external_job_ids,
        },
        fencing_epoch=7,
        disposition=disposition,
    )

    validate_slurm_recovery_actions([completion])


def test_missing_held_job_postimage_without_tombstone_is_rejected() -> None:
    completion = normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "pending-hold-applied",
            "job_ids": ["42", "43"],
            "jobs": [{"job_id": "42"}],
        },
        fencing_epoch=7,
        disposition=SlurmRecoveryDisposition.RECOVERED_APPLIED,
    )

    with pytest.raises(RuntimeError, match="exact postimage"):
        validate_slurm_recovery_actions([completion])
