from __future__ import annotations

from collections.abc import Sequence

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.slurm_action_journal import (
    SLURM_ACTION_APPLIED,
    SLURM_ACTION_DISPATCHING,
    SLURM_ACTION_INDETERMINATE,
    SLURM_ACTION_QUEUED,
    SLURM_ACTION_REJECTED,
    begin_target_slurm_action_generation,
    dispatchable_slurm_actions,
    enqueue_slurm_action,
    new_slurm_action_journal,
    set_slurm_action_broker_mode,
)


def _observation(
    *,
    state: str,
    user_id: str = "1000",
) -> migration._SlurmJobControlObservation:  # noqa: SLF001
    return migration._SlurmJobControlObservation(  # noqa: SLF001
        job_id="42",
        identity={
            "job_id": "42",
            "user_id": user_id,
            "job_name": "preservation-job",
            "submit_time": "2026-07-12T10:00:00",
            "array_job_id": "0",
            "array_task_id": "4294967294",
            "het_job_id": "0",
            "het_job_offset": "4294967294",
        },
        identity_fingerprint="identity-42",
        job_state=state,
        reason="None",
        priority="100",
        held=False,
        record=f"JobId=42 Restarts=0 JobState={state}",
        record_fingerprint=f"record-{state}",
    )


def _queued_cancel(
    pre: migration._SlurmJobControlObservation,  # noqa: SLF001
) -> tuple[dict[str, object], dict[str, object]]:
    journal = new_slurm_action_journal()
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=migration._slurm_action_binding_from_observation(pre),  # noqa: SLF001
        intended_postcondition=migration._slurm_action_intended_postcondition(  # noqa: SLF001
            kind="cancel",
            pre_observation=pre.as_payload(),
        ),
        action_id="cancel-42",
        batch_id="batch-42",
    )
    return journal, action


def _result(
    args: Sequence[str],
    *,
    stdout: str = "",
    returncode: int = 0,
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(tuple(args), returncode, stdout, "")


def _sacct_row(
    *,
    state: str,
    job_id: str = "42",
    user: str = "slurm-user",
    uid: str = "1000",
    submit_time: str = "2026-07-12T10:00:00",
    restarts: str = "0",
) -> str:
    return "|".join((job_id, user, uid, submit_time, restarts, state)) + "\n"


def test_scancel_ack_stays_dispatching_until_terminal_postcondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _observation(state="RUNNING", user_id="slurm-user(1000)")
    completed = _observation(state="CANCELLED", user_id="slurm-user(1000)")
    observations = [running, running]
    accounting_states = ["RUNNING", "CANCELLED by 1000"]
    journal, action = _queued_cancel(running)
    assert action["intended_postcondition"]["expected"] == {
        "accounting_state": "CANCELLED",
        "exact_lineage": True,
    }
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: observations.pop(0),
    )

    def exec_login(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        if args[0] == "sacct":
            return _result(args, stdout=_sacct_row(state=accounting_states.pop(0)))
        return _result(args)

    monkeypatch.setattr(migration, "_kubectl_exec_login", exec_login)

    migration._dispatch_queued_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )

    assert action["state"] == SLURM_ACTION_DISPATCHING
    assert action["result"] == ""
    assert dispatchable_slurm_actions(journal) == ()

    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: completed,
    )
    migration._reconcile_dispatching_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
        finalize_unresolved=False,
    )

    assert action["state"] == SLURM_ACTION_APPLIED
    assert action["result"] == "sacct verified exact CANCELLED outcome for the bound job lineage"
    assert action["observed_postcondition"]["accounting_state"] == "CANCELLED by 1000"


def test_natural_completion_after_scancel_dispatch_is_rejected_as_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _observation(state="RUNNING")
    completed = _observation(state="COMPLETED")
    observations = [running, completed]
    journal, action = _queued_cancel(running)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: observations.pop(0),
    )

    def exec_login(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        if args[0] == "sacct":
            return _result(args, stdout=_sacct_row(state="COMPLETED"))
        return _result(args)

    monkeypatch.setattr(migration, "_kubectl_exec_login", exec_login)

    migration._dispatch_queued_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )

    assert action["state"] == SLURM_ACTION_REJECTED
    assert action["result"] == (
        "job reached COMPLETED without a verifiable cancel outcome; cancel was a no-op"
    )
    assert action["observed_postcondition"]["accounting_state"] == "COMPLETED"


@pytest.mark.parametrize(
    ("user", "uid", "submit_time", "restarts"),
    (
        ("other-user", "2000", "2026-07-12T10:00:00", "0"),
        ("slurm-user", "1000", "2026-07-12T10:00:01", "0"),
        ("slurm-user", "1000", "2026-07-12T10:00:00", "1"),
    ),
)
def test_cancel_accounting_requires_exact_user_submit_and_restart_lineage(
    monkeypatch: pytest.MonkeyPatch,
    user: str,
    uid: str,
    submit_time: str,
    restarts: str,
) -> None:
    running = _observation(state="RUNNING")
    journal, action = _queued_cancel(running)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: running,
    )

    def exec_login(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        if args[0] == "sacct":
            return _result(
                args,
                stdout=_sacct_row(
                    state="CANCELLED by 1000",
                    user=user,
                    uid=uid,
                    submit_time=submit_time,
                    restarts=restarts,
                ),
            )
        return _result(args)

    monkeypatch.setattr(migration, "_kubectl_exec_login", exec_login)
    migration._dispatch_queued_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )
    assert action["state"] == SLURM_ACTION_DISPATCHING

    migration._reconcile_dispatching_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
        finalize_unresolved=True,
    )

    assert action["state"] == SLURM_ACTION_INDETERMINATE
    assert action["result"] == "sacct has no exact cancel lineage yet"
    assert action["observed_postcondition"]["exact_lineage_row_count"] == 0


def test_missing_job_and_accounting_lag_remain_indeterminate_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _observation(state="RUNNING")
    journal, action = _queued_cancel(running)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: running,
    )
    scancel_count = 0
    accounting_outputs = [_sacct_row(state="RUNNING"), "", ""]

    def exec_login(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        nonlocal scancel_count
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        if args[0] == "sacct":
            return _result(args, stdout=accounting_outputs.pop(0))
        if args[0] == "scancel":
            scancel_count += 1
        return _result(args)

    monkeypatch.setattr(migration, "_kubectl_exec_login", exec_login)
    migration._dispatch_queued_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )
    assert action["state"] == SLURM_ACTION_DISPATCHING

    def job_missing(**_kwargs: object) -> migration._SlurmJobControlObservation:  # noqa: SLF001
        raise RuntimeError("Invalid job id specified")

    monkeypatch.setattr(migration, "_external_upgrade_slurm_job_control_observation", job_missing)
    for finalize in (False, True):
        migration._reconcile_dispatching_slurm_action(  # noqa: SLF001
            journal=journal,
            action=action,
            command_runner=lambda args, **_kwargs: _result(args),
            kube_context="context",
            authority_provider=lambda: {
                "authority": "target-singleton",
                "authority_epoch": "target-epoch-1",
            },
            checkpoint_writer=lambda: None,
            finalize_unresolved=finalize,
        )

    assert action["state"] == SLURM_ACTION_INDETERMINATE
    assert action["result"] == "sacct has no exact cancel lineage yet"
    assert action["observed_postcondition"]["job_id_row_count"] == 0
    assert scancel_count == 1


def test_lost_scancel_response_reconciles_from_accounting_without_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _observation(state="RUNNING")
    journal, action = _queued_cancel(running)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: running,
    )
    dispatch_count = 0

    def lose_response(**_kwargs: object) -> migration.SoperatorMigrationCommandResult:
        nonlocal dispatch_count
        dispatch_count += 1
        raise RuntimeError("controller connection lost after request write")

    monkeypatch.setattr(migration, "_kubectl_exec_login", lose_response)

    with pytest.raises(RuntimeError, match="connection lost"):
        migration._dispatch_queued_slurm_action(  # noqa: SLF001
            journal=journal,
            action=action,
            command_runner=lambda args, **_kwargs: _result(args),
            kube_context="context",
            authority_provider=lambda: {
                "authority": "target-singleton",
                "authority_epoch": "target-epoch-1",
            },
            checkpoint_writer=lambda: None,
        )

    assert action["state"] == SLURM_ACTION_INDETERMINATE
    assert "will not retry" in str(action["result"])

    def reconcile_cancel(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        assert args[0] == "sacct"
        return _result(args, stdout=_sacct_row(state="CANCELLED by 1000"))

    monkeypatch.setattr(migration, "_kubectl_exec_login", reconcile_cancel)
    migration._drain_external_upgrade_slurm_action_journal(  # noqa: SLF001
        journal=journal,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )

    assert action["state"] == SLURM_ACTION_APPLIED
    assert [item["state"] for item in action["transitions"]] == [
        SLURM_ACTION_QUEUED,
        SLURM_ACTION_DISPATCHING,
        SLURM_ACTION_INDETERMINATE,
        SLURM_ACTION_APPLIED,
    ]
    assert dispatch_count == 1


def test_uncertain_nonzero_hold_reconciles_exact_postcondition_without_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _observation(state="PENDING")
    held = migration._SlurmJobControlObservation(  # noqa: SLF001
        **{**pending.__dict__, "held": True, "record_fingerprint": "record-held"}
    )
    observations = [pending, held]
    journal = new_slurm_action_journal()
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    action = enqueue_slurm_action(
        journal,
        kind="hold",
        binding=migration._slurm_action_binding_from_observation(pending),  # noqa: SLF001
        intended_postcondition=migration._slurm_action_intended_postcondition(  # noqa: SLF001
            kind="hold",
            pre_observation=pending.as_payload(),
        ),
        action_id="hold-42",
        batch_id="batch-42",
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: observations.pop(0),
    )
    dispatch_count = 0

    def uncertain_hold(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        nonlocal dispatch_count
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        dispatch_count += 1
        return _result(args, stdout="controller transport closed", returncode=1)

    monkeypatch.setattr(migration, "_kubectl_exec_login", uncertain_hold)
    migration._dispatch_queued_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )

    assert action["state"] == SLURM_ACTION_INDETERMINATE
    migration._drain_external_upgrade_slurm_action_journal(  # noqa: SLF001
        journal=journal,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )

    assert action["state"] == SLURM_ACTION_APPLIED
    assert action["result"] == "hold postcondition verified"
    assert dispatch_count == 1


def test_unresolved_scancel_ack_remains_indeterminate_cleanup_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _observation(state="RUNNING")
    journal, action = _queued_cancel(running)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: running,
    )
    monkeypatch.setattr(migration, "_kubectl_exec_login", lambda **kwargs: _result(kwargs["args"]))
    migration._dispatch_queued_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )
    assert action["state"] == SLURM_ACTION_DISPATCHING

    migration._reconcile_dispatching_slurm_action(  # noqa: SLF001
        journal=journal,
        action=action,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
        finalize_unresolved=True,
    )

    assert action["state"] == SLURM_ACTION_INDETERMINATE
    assert action["result"] == "sacct has no exact cancel lineage yet"

    blockers = migration._drain_external_upgrade_slurm_action_journal(  # noqa: SLF001
        journal=journal,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
        finalize_unresolved=True,
    )

    assert blockers == (action,)
    assert action["state"] == SLURM_ACTION_INDETERMINATE


def test_target_generation_hold_dispatches_while_bridge_cleanup_is_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _observation(state="PENDING")
    held = migration._SlurmJobControlObservation(  # noqa: SLF001
        **{**pending.__dict__, "held": True, "record_fingerprint": "record-held"}
    )
    observations = [pending, held]
    journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(
        journal,
        authority_epoch="target-epoch-1",
    )
    action = enqueue_slurm_action(
        journal,
        kind="hold",
        binding=migration._slurm_action_binding_from_observation(pending),  # noqa: SLF001
        intended_postcondition=migration._slurm_action_intended_postcondition(  # noqa: SLF001
            kind="hold",
            pre_observation=pending.as_payload(),
        ),
        action_id="cleanup-hold-42",
        batch_id="cleanup-batch-42",
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: observations.pop(0),
    )
    dispatches: list[tuple[str, ...]] = []

    def exec_login(**kwargs: object) -> migration.SoperatorMigrationCommandResult:
        args = kwargs["args"]
        assert isinstance(args, Sequence)
        dispatches.append(tuple(str(item) for item in args))
        return _result(args)

    monkeypatch.setattr(migration, "_kubectl_exec_login", exec_login)

    blockers = migration._drain_external_upgrade_slurm_action_journal(  # noqa: SLF001
        journal=journal,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="context",
        authority_provider=lambda: {
            "authority": "target-singleton",
            "authority_epoch": "target-epoch-1",
        },
        checkpoint_writer=lambda: None,
    )

    assert blockers == ()
    assert action["authority_generation"] == 1
    assert action["state"] == SLURM_ACTION_APPLIED
    assert any(command[:2] == ("scontrol", "hold") for command in dispatches)
