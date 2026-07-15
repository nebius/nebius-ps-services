from __future__ import annotations

import hashlib

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.slurm_action_journal import (
    SLURM_ACTION_APPLIED,
    enqueue_slurm_action,
    ensure_slurm_action_journal,
    transition_slurm_action,
)
from nebius_cxcli.slurm_jobs import AffectedSlurmJob


def _observation(
    *,
    job_id: str = "42",
    nodes: str = "worker-1",
    restarts: int = 0,
    state: str = "RUNNING",
    submit_time: str = "2026-07-12T10:00:00",
) -> migration._SlurmJobControlObservation:
    record = (
        f"JobId={job_id} JobName=preserve UserId=alice(1000) JobState={state} "
        f"Partition=gpu NodeList={nodes} Priority=100 Reason=None Requeue=1 "
        f"Restarts={restarts} TimeLimit=01:00:00 SubmitTime={submit_time} "
        "StartTime=2026-07-12T10:01:00"
    )
    identity = {
        "job_id": job_id,
        "user_id": "alice(1000)",
        "job_name": "preserve",
        "submit_time": submit_time,
        "array_job_id": "",
        "array_task_id": "",
        "het_job_id": "",
        "het_job_offset": "",
    }
    return migration._SlurmJobControlObservation(  # noqa: SLF001
        job_id=job_id,
        identity=identity,
        identity_fingerprint=hashlib.sha256(
            migration._stable_json(identity).encode("utf-8")  # noqa: SLF001
        ).hexdigest(),
        job_state=state,
        reason="None",
        priority="100",
        held=False,
        record=record,
        record_fingerprint=hashlib.sha256(record.encode("utf-8")).hexdigest(),
    )


def _job() -> AffectedSlurmJob:
    return AffectedSlurmJob(
        job_id="42",
        user="alice",
        state="RUNNING",
        partition="gpu",
        allocated_nodes="worker-1",
        requested_nodes="1",
        scheduled_nodes="worker-1",
        reason="None",
        elapsed="00:10:00",
        limit="01:00:00",
        remaining="00:50:00",
        name="preserve",
        impact_scope="allocated-node",
    )


def _journal() -> dict[str, object]:
    return {
        "preservation_jobs": {
            "schema": migration._SLURM_PRESERVATION_JOBS_SCHEMA,  # noqa: SLF001
            "captured_at": "",
            "jobs": {},
            "verifications": [],
        }
    }


def _capture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], dict[str, object]]:
    checkpoint: dict[str, object] = {}
    journal = _journal()
    monkeypatch.setattr(
        migration,
        "_external_upgrade_all_slurm_jobs",
        lambda **_kwargs: (_job(),),
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: _observation(),
    )

    lines = migration._capture_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )

    assert lines == [
        "Running-job lineage: captured 1 immutable allocation baseline(s) and 0 natural "
        "completion proof(s) after scheduling pause."
    ]
    return checkpoint, journal


def test_capture_and_verify_running_job_keeps_exact_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, journal = _capture(monkeypatch)

    lines = migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        proof_stage="source-version-bridge-active",
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )

    assert "verified 1 job(s)" in lines[0]
    preservation = journal["preservation_jobs"]
    assert isinstance(preservation, dict)
    assert preservation["verifications"][0]["jobs"]["42"]["status"] == "active-preserved"


def test_running_job_allocation_or_restart_drift_blocks_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, journal = _capture(monkeypatch)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: _observation(nodes="worker-2", restarts=1),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="changed JobID lineage, allocation, start time, submit time, or Restarts",
    ):
        migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
            checkpoint=checkpoint,
            journal=journal,
            proof_stage="target-version-bridge-active",
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
            checkpoint_writer=lambda: None,
        )


def test_natural_successful_completion_uses_exact_accounting_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, journal = _capture(monkeypatch)
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("job left scontrol")),
    )
    monkeypatch.setattr(
        migration,
        "_slurm_preservation_sacct_observation",
        lambda **_kwargs: {
            "job_id": "42",
            "submit_time": "2026-07-12T10:00:00",
            "start_time": "2026-07-12T10:01:00",
            "node_list": "worker-1",
            "restarts": "0",
            "state": "COMPLETED",
            "exit_code": "0:0",
        },
    )

    migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        proof_stage="target-singleton-active",
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )

    preservation = journal["preservation_jobs"]
    assert isinstance(preservation, dict)
    assert preservation["verifications"][0]["jobs"]["42"]["status"] == "completed-preserved"


def test_natural_completion_during_capture_is_journaled_and_resumable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint: dict[str, object] = {}
    journal = _journal()
    preservation = journal["preservation_jobs"]
    assert isinstance(preservation, dict)
    preservation.update(
        {
            "capture_state": "dispatching",
            "capture_job_ids": ["42"],
            "capture_intent_at": "2026-07-12T10:05:00Z",
        }
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: _observation(state="COMPLETED"),
    )
    monkeypatch.setattr(
        migration,
        "_slurm_preservation_sacct_observation",
        lambda **_kwargs: {
            "job_id": "42",
            "submit_time": "2026-07-12T10:00:00",
            "start_time": "2026-07-12T10:01:00",
            "node_list": "worker-1",
            "restarts": "0",
            "state": "COMPLETED",
            "exit_code": "0:0",
        },
    )

    lines = migration._capture_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )

    assert "1 natural completion proof(s)" in lines[0]
    assert preservation["capture_state"] == "complete"
    assert preservation["jobs"] == {}
    assert preservation["completed_during_capture"]["42"]["accounting"]["state"] == "COMPLETED"

    resumed = migration._capture_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )
    assert (
        "reused 0 immutable allocation baseline(s) and 1 natural completion proof(s)" in resumed[0]
    )

    verified = migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        proof_stage="source-version-bridge-active",
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )
    assert "verified 1 job(s)" in verified[0]
    assert preservation["verifications"][0]["jobs"]["42"]["status"] == "completed-during-capture"


def test_failed_job_during_capture_remains_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint: dict[str, object] = {}
    journal = _journal()
    preservation = journal["preservation_jobs"]
    assert isinstance(preservation, dict)
    preservation.update(
        {
            "capture_state": "dispatching",
            "capture_job_ids": ["42"],
            "capture_intent_at": "2026-07-12T10:05:00Z",
        }
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: _observation(state="FAILED"),
    )
    monkeypatch.setattr(
        migration,
        "_slurm_preservation_sacct_observation",
        lambda **_kwargs: {
            "job_id": "42",
            "submit_time": "2026-07-12T10:00:00",
            "start_time": "2026-07-12T10:01:00",
            "node_list": "worker-1",
            "restarts": "0",
            "state": "FAILED",
            "exit_code": "1:0",
        },
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="did not prove an exact natural COMPLETED/0:0 accounting result",
    ):
        migration._capture_controller_bridge_preservation_jobs(  # noqa: SLF001
            checkpoint=checkpoint,
            journal=journal,
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
            checkpoint_writer=lambda: None,
        )


def test_applied_operator_requeue_is_not_misreported_as_upgrade_disruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, journal = _capture(monkeypatch)
    preservation = journal["preservation_jobs"]
    assert isinstance(preservation, dict)
    record = preservation["jobs"]["42"]
    binding = migration.SlurmJobActionBinding.from_payload(record["binding"])
    action_journal = ensure_slurm_action_journal(checkpoint)
    action = enqueue_slurm_action(
        action_journal,
        kind="requeue",
        binding=binding,
        intended_postcondition={"restart_count": 1},
        action_id="operator-requeue",
        batch_id="operator-batch",
    )
    transition_slurm_action(
        action_journal,
        action_id=action["action_id"],
        state="Dispatching",
        authority_epoch="bridge-source-1",
    )
    transition_slurm_action(
        action_journal,
        action_id=action["action_id"],
        state=SLURM_ACTION_APPLIED,
        result="requeue applied",
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: pytest.fail("operator-modified job must not be rechecked"),
    )

    migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        proof_stage="target-version-bridge-active",
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
        checkpoint_writer=lambda: None,
    )

    assert preservation["verifications"][0]["jobs"]["42"] == {
        "status": "operator-modified",
        "actions": ["requeue"],
        "verified_at": preservation["verifications"][0]["jobs"]["42"]["verified_at"],
    }


def test_applied_action_for_reused_job_id_cannot_override_preservation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint, journal = _capture(monkeypatch)
    preservation = journal["preservation_jobs"]
    assert isinstance(preservation, dict)
    record = preservation["jobs"]["42"]
    baseline = migration.SlurmJobActionBinding.from_payload(record["binding"])
    reused_binding = migration.SlurmJobActionBinding(
        job_id=baseline.job_id,
        user_id=baseline.user_id,
        submit_time="2026-07-12T11:00:00",
        restart_baseline=1,
        identity_fingerprint="reused-job-identity",
        lineage_fingerprint=baseline.lineage_fingerprint,
    )
    action_journal = ensure_slurm_action_journal(checkpoint)
    action = enqueue_slurm_action(
        action_journal,
        kind="requeue",
        binding=reused_binding,
        intended_postcondition={"restart_count": 2},
        action_id="reused-job-requeue",
        batch_id="reused-job-batch",
    )
    transition_slurm_action(
        action_journal,
        action_id=action["action_id"],
        state="Dispatching",
        authority_epoch="target-singleton-1",
    )
    transition_slurm_action(
        action_journal,
        action_id=action["action_id"],
        state=SLURM_ACTION_APPLIED,
        result="requeue applied to reused JobID",
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: _observation(
            restarts=1,
            submit_time="2026-07-12T11:00:00",
        ),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="changed JobID lineage, allocation, start time, submit time, or Restarts",
    ):
        migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
            checkpoint=checkpoint,
            journal=journal,
            proof_stage="target-version-bridge-active",
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: pytest.fail("runner was not expected"),
            checkpoint_writer=lambda: None,
        )
