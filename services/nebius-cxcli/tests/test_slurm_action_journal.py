from __future__ import annotations

import pytest

from nebius_cxcli.slurm_action_journal import (
    SLURM_ACTION_ADMISSION_CLOSED,
    SLURM_ACTION_APPLIED,
    SLURM_ACTION_DISPATCHING,
    SLURM_ACTION_INDETERMINATE,
    SLURM_ACTION_QUEUED,
    SLURM_ACTION_REJECTED,
    SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
    SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION,
    SlurmJobActionBinding,
    acknowledge_slurm_login_exit,
    begin_target_slurm_action_generation,
    dispatchable_slurm_actions,
    enqueue_slurm_action,
    finalize_target_slurm_action_generation,
    new_slurm_action_journal,
    record_slurm_login_observation,
    set_slurm_action_broker_mode,
    slurm_action_finalization_blockers,
    slurm_action_journal_generation,
    slurm_action_partition_restore_binding,
    slurm_action_partition_restore_blockers,
    slurm_action_target_finalization_binding,
    transition_slurm_action,
    validate_slurm_action_journal,
)


def _binding(
    job_id: str = "42",
    *,
    submit_time: str = "2026-07-12T10:00:00",
    restarts: int = 0,
) -> SlurmJobActionBinding:
    return SlurmJobActionBinding(
        job_id=job_id,
        user_id="1000",
        submit_time=submit_time,
        restart_baseline=restarts,
        identity_fingerprint=f"identity-{job_id}-{submit_time}",
        lineage_fingerprint=f"lineage-{job_id}",
    )


def test_enqueue_records_immutable_binding_before_dispatch() -> None:
    journal = new_slurm_action_journal()

    action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding(),
        intended_postcondition={"job_absent": True},
        action_id="action-1",
        batch_id="batch-1",
        accepted_at="2026-07-12T10:01:00Z",
    )

    assert action["state"] == SLURM_ACTION_QUEUED
    assert action["binding"] == {
        "job_id": "42",
        "user_id": "1000",
        "submit_time": "2026-07-12T10:00:00",
        "restart_baseline": 0,
        "identity_fingerprint": "identity-42-2026-07-12T10:00:00",
        "lineage_fingerprint": "lineage-42",
    }
    assert action["transitions"] == [
        {
            "state": SLURM_ACTION_QUEUED,
            "at": "2026-07-12T10:01:00Z",
            "detail": "accepted",
        }
    ]
    validate_slurm_action_journal(journal)


def test_action_journal_generation_advances_for_intent_and_transition() -> None:
    journal = new_slurm_action_journal()
    assert slurm_action_journal_generation(journal) == 0

    action = enqueue_slurm_action(
        journal,
        kind="wait",
        binding=_binding(),
        intended_postcondition={"observed": True},
    )
    assert slurm_action_journal_generation(journal) == 1

    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_DISPATCHING,
        authority_epoch="target-epoch-1",
    )
    assert slurm_action_journal_generation(journal) == 2


def test_login_observation_records_only_sanitized_handoff_status() -> None:
    journal = new_slurm_action_journal()

    record_slurm_login_observation(
        journal,
        state="pending-voluntary-exit",
        protected_pod_count=1,
        active_session_count=2,
        target_ready=True,
        observed_at="2026-07-12T10:02:00Z",
    )

    assert journal["login"] == {
        "state": "pending-voluntary-exit",
        "protected_pod_count": 1,
        "active_session_count": 2,
        "target_ready": True,
        "exit_confirmation_requests": [],
        "exit_acknowledgements": [],
        "observed_at": "2026-07-12T10:02:00Z",
    }
    assert "socket" not in repr(journal["login"]).lower()
    assert "remote" not in repr(journal["login"]).lower()


def test_login_exit_acknowledgement_binds_exact_pending_absence_epoch() -> None:
    journal = new_slurm_action_journal()
    fingerprint = "a" * 64
    record_slurm_login_observation(
        journal,
        state="indeterminate",
        protected_pod_count=1,
        active_session_count=1,
        target_ready=True,
        exit_confirmation_requests=(
            {
                "socket_fingerprint": fingerprint,
                "absence_observed_at": "2026-07-12T10:02:00Z",
            },
        ),
        observed_at="2026-07-12T10:02:01Z",
    )

    acknowledgement = acknowledge_slurm_login_exit(
        journal,
        socket_fingerprint=fingerprint,
        acknowledged_by="operator",
        disposition=SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
        acknowledged_at="2026-07-12T10:03:00Z",
    )

    assert acknowledgement == {
        "socket_fingerprint": fingerprint,
        "absence_observed_at": "2026-07-12T10:02:00Z",
        "acknowledged_at": "2026-07-12T10:03:00Z",
        "acknowledged_by": "operator",
        "disposition": SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
    }
    assert (
        acknowledge_slurm_login_exit(
            journal,
            socket_fingerprint=fingerprint,
            acknowledged_by="operator",
            disposition=SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
            acknowledged_at="2026-07-12T10:04:00Z",
        )
        == acknowledgement
    )


def test_login_exit_acknowledgement_rejects_live_or_unmatched_session() -> None:
    journal = new_slurm_action_journal()

    with pytest.raises(ValueError, match="exactly one currently pending"):
        acknowledge_slurm_login_exit(
            journal,
            socket_fingerprint="a" * 64,
            acknowledged_by="operator",
            disposition=SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
        )


def test_login_timeout_continuation_refines_unqualified_exact_acknowledgement() -> None:
    journal = new_slurm_action_journal()
    fingerprint = "a" * 64
    record_slurm_login_observation(
        journal,
        state="indeterminate",
        protected_pod_count=1,
        active_session_count=1,
        target_ready=True,
        exit_confirmation_requests=(
            {
                "socket_fingerprint": fingerprint,
                "absence_observed_at": "2026-07-12T10:02:00Z",
            },
        ),
    )
    journal["login"]["exit_acknowledgements"].append(
        {
            "socket_fingerprint": fingerprint,
            "absence_observed_at": "2026-07-12T10:02:00Z",
            "acknowledged_at": "2026-07-12T10:03:00Z",
            "acknowledged_by": "ext-soperator-jobs-cli",
        }
    )

    acknowledgement = acknowledge_slurm_login_exit(
        journal,
        socket_fingerprint=fingerprint,
        acknowledged_by="ext-soperator-jobs-cli",
        disposition=SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION,
        acknowledged_at="2026-07-12T10:04:00Z",
    )

    assert acknowledgement["disposition"] == SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION
    assert acknowledgement["disposition_recorded_at"] == "2026-07-12T10:04:00Z"


def test_login_observation_rejects_invalid_state_or_counts() -> None:
    journal = new_slurm_action_journal()

    with pytest.raises(ValueError, match="Unsupported Slurm login observation state"):
        record_slurm_login_observation(
            journal,
            state="timed-out",
            protected_pod_count=1,
            active_session_count=1,
            target_ready=False,
        )
    with pytest.raises(ValueError, match="active_session_count"):
        record_slurm_login_observation(
            journal,
            state="protected",
            protected_pod_count=1,
            active_session_count=-1,
            target_ready=False,
        )


def test_only_queued_actions_dispatch_and_accept_only_mode_dispatches_none() -> None:
    journal = new_slurm_action_journal()
    action = enqueue_slurm_action(
        journal,
        kind="wait",
        binding=_binding(),
        intended_postcondition={"observed": True},
        action_id="action-1",
    )
    assert dispatchable_slurm_actions(journal) == ()

    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    assert tuple(item["action_id"] for item in dispatchable_slurm_actions(journal)) == ("action-1",)

    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_DISPATCHING,
        authority_epoch="source-1",
    )
    assert dispatchable_slurm_actions(journal) == ()


def test_action_admission_closes_before_final_drain_and_finalizes_only_when_terminal() -> None:
    journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(journal, authority_epoch="target-epoch-1")
    action = enqueue_slurm_action(
        journal,
        kind="wait",
        binding=_binding(),
        intended_postcondition={"observed": True},
    )

    set_slurm_action_broker_mode(journal, "accept-only")
    set_slurm_action_broker_mode(journal, SLURM_ACTION_ADMISSION_CLOSED)

    assert slurm_action_finalization_blockers(journal) == (action,)
    with pytest.raises(ValueError, match="only after every accepted action"):
        finalize_target_slurm_action_generation(journal)

    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_REJECTED,
        result="job completed before final cleanup",
    )
    boundary = finalize_target_slurm_action_generation(journal)

    assert journal["broker_mode"] == SLURM_ACTION_ADMISSION_CLOSED
    assert boundary["target_authority_generation"] == 1
    assert slurm_action_finalization_blockers(journal) == ()
    assert slurm_action_target_finalization_binding(journal) == {
        "action_journal_generation": 0,
        "authority_epoch": "target-epoch-1",
        "sealed_authority_generation": 0,
        "target_authority_generation": 1,
        "target_action_journal_generation": 2,
    }
    with pytest.raises(ValueError, match="admission is closed"):
        enqueue_slurm_action(
            journal,
            kind="refresh",
            binding=_binding(),
            intended_postcondition={"observed": True},
        )
    with pytest.raises(ValueError, match="closure is irreversible"):
        set_slurm_action_broker_mode(journal, "dispatch-enabled")
    validate_slurm_action_journal(journal)


@pytest.mark.parametrize(
    ("terminal_state", "result"),
    [
        (SLURM_ACTION_APPLIED, "cancel confirmed"),
        (SLURM_ACTION_REJECTED, "job completed before dispatch"),
    ],
)
def test_definitive_action_is_terminal_and_never_replayed(
    terminal_state: str,
    result: str,
) -> None:
    journal = new_slurm_action_journal()
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding(),
        intended_postcondition={"job_absent": True},
    )
    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_DISPATCHING,
        authority_epoch="bridge-source-1",
    )
    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=terminal_state,
        result=result,
    )

    assert dispatchable_slurm_actions(journal) == ()
    with pytest.raises(ValueError, match="Invalid Slurm action transition"):
        transition_slurm_action(
            journal,
            action_id=str(action["action_id"]),
            state=SLURM_ACTION_DISPATCHING,
            authority_epoch="bridge-source-1",
        )


def test_indeterminate_action_is_non_resendable_and_later_reconcilable() -> None:
    journal = new_slurm_action_journal()
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding(),
        intended_postcondition={"job_absent": True},
    )
    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_DISPATCHING,
        authority_epoch="bridge-source-1",
    )
    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_INDETERMINATE,
        result="controller response was lost",
    )

    assert dispatchable_slurm_actions(journal) == ()
    assert slurm_action_partition_restore_blockers(journal) == (action,)
    with pytest.raises(ValueError, match="already has an active mutating action"):
        enqueue_slurm_action(
            journal,
            kind="hold",
            binding=_binding(),
            intended_postcondition={"held": True},
        )

    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_APPLIED,
        result="exact accounting lineage is CANCELLED",
    )

    assert action["state"] == SLURM_ACTION_APPLIED
    assert slurm_action_partition_restore_blockers(journal) == ()
    validate_slurm_action_journal(journal)


def test_reused_job_id_cannot_retarget_active_action() -> None:
    journal = new_slurm_action_journal()
    enqueue_slurm_action(
        journal,
        kind="hold",
        binding=_binding(),
        intended_postcondition={"held": True},
    )

    with pytest.raises(ValueError, match="JobID 42 was reused"):
        enqueue_slurm_action(
            journal,
            kind="refresh",
            binding=_binding(submit_time="2026-07-12T11:00:00"),
            intended_postcondition={"observed": True},
        )


def test_conflicting_active_mutation_is_rejected_but_read_action_can_coexist() -> None:
    journal = new_slurm_action_journal()
    enqueue_slurm_action(
        journal,
        kind="hold",
        binding=_binding(),
        intended_postcondition={"held": True},
    )

    with pytest.raises(ValueError, match="already has an active mutating action"):
        enqueue_slurm_action(
            journal,
            kind="cancel",
            binding=_binding(),
            intended_postcondition={"job_absent": True},
        )

    read_action = enqueue_slurm_action(
        journal,
        kind="refresh",
        binding=_binding(),
        intended_postcondition={"observed": True},
    )
    assert read_action["state"] == SLURM_ACTION_QUEUED


def test_partition_restore_blocks_on_queued_dispatching_and_indeterminate_actions() -> None:
    journal = new_slurm_action_journal()
    first = enqueue_slurm_action(
        journal,
        kind="wait",
        binding=_binding("41"),
        intended_postcondition={"observed": True},
    )
    second = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding("42"),
        intended_postcondition={"job_absent": True},
    )
    transition_slurm_action(
        journal,
        action_id=str(second["action_id"]),
        state=SLURM_ACTION_DISPATCHING,
        authority_epoch="bridge-source-1",
    )
    transition_slurm_action(
        journal,
        action_id=str(second["action_id"]),
        state=SLURM_ACTION_INDETERMINATE,
        result="ambiguous cancel outcome",
    )

    assert {item["action_id"] for item in slurm_action_partition_restore_blockers(journal)} == {
        first["action_id"],
        second["action_id"],
    }


def test_dispatch_requires_recorded_authority_epoch() -> None:
    journal = new_slurm_action_journal()
    action = enqueue_slurm_action(
        journal,
        kind="requeue",
        binding=_binding(),
        intended_postcondition={"restarts": 1},
    )

    with pytest.raises(ValueError, match="authority_epoch"):
        transition_slurm_action(
            journal,
            action_id=str(action["action_id"]),
            state=SLURM_ACTION_DISPATCHING,
        )


def test_target_authority_generation_keeps_actions_dispatchable_after_restore_boundary() -> None:
    journal = new_slurm_action_journal()
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    old_action = enqueue_slurm_action(
        journal,
        kind="refresh",
        binding=_binding("41"),
        intended_postcondition={"controller_observed": True},
    )
    transition_slurm_action(
        journal,
        action_id=str(old_action["action_id"]),
        state=SLURM_ACTION_REJECTED,
        result="refresh superseded by target authority",
    )

    boundary = begin_target_slurm_action_generation(
        journal,
        authority_epoch="target-epoch-1",
    )
    frozen = slurm_action_partition_restore_binding(journal)
    target_action = enqueue_slurm_action(
        journal,
        kind="hold",
        binding=_binding("42"),
        intended_postcondition={"held": True},
    )

    assert boundary["sealed_generation"] == 0
    assert frozen == {
        "action_journal_generation": 2,
        "authority_epoch": "target-epoch-1",
        "sealed_authority_generation": 0,
        "target_authority_generation": 1,
    }
    assert target_action["authority_generation"] == 1
    assert dispatchable_slurm_actions(journal) == (target_action,)
    assert slurm_action_partition_restore_blockers(journal) == ()
    assert slurm_action_finalization_blockers(journal) == (target_action,)


def test_target_generation_indeterminate_action_is_never_retried_or_added_to_restore_proof() -> (
    None
):
    journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(
        journal,
        authority_epoch="target-epoch-1",
    )
    frozen = slurm_action_partition_restore_binding(journal)
    action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding(),
        intended_postcondition={"job_absent": True},
    )
    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_DISPATCHING,
        authority_epoch="target-epoch-1",
    )
    transition_slurm_action(
        journal,
        action_id=str(action["action_id"]),
        state=SLURM_ACTION_INDETERMINATE,
        result="target controller RPC response was lost",
    )

    assert dispatchable_slurm_actions(journal) == ()
    assert slurm_action_partition_restore_blockers(journal) == ()
    assert slurm_action_finalization_blockers(journal) == (action,)
    assert slurm_action_partition_restore_binding(journal) == frozen
    with pytest.raises(ValueError, match="Invalid Slurm action transition"):
        transition_slurm_action(
            journal,
            action_id=str(action["action_id"]),
            state=SLURM_ACTION_DISPATCHING,
            authority_epoch="target-epoch-1",
        )
