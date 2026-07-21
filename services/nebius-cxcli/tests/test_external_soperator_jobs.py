from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.slurm_action_journal import (
    SLURM_ACTION_ADMISSION_CLOSED,
    SlurmJobActionBinding,
    enqueue_slurm_action,
    new_slurm_action_journal,
    record_slurm_login_observation,
    set_slurm_action_broker_mode,
    transition_slurm_action,
)
from nebius_cxcli.slurm_jobs import AffectedSlurmJob


def _payload() -> dict[str, Any]:
    return {
        "deploy": {
            "targets": [
                {
                    "instance_id": "external-cluster",
                    "soperator_onboarding": {
                        "upgrade_path": {
                            "campaign_id": "campaign-1",
                            "fingerprint": "campaign-fingerprint",
                        }
                    },
                }
            ]
        }
    }


def _job() -> AffectedSlurmJob:
    return AffectedSlurmJob(
        job_id="42",
        user="alice",
        state="RUNNING",
        partition="gpu",
        allocated_nodes="worker-0",
        requested_nodes="1",
        scheduled_nodes="worker-0",
        reason="None",
        elapsed="00:01:00",
        limit="01:00:00",
        remaining="00:59:00",
        name="preserve",
        impact_scope="allocated-node",
    )


def _observation() -> migration._SlurmJobControlObservation:
    record = (
        "JobId=42 UserId=alice(1000) JobName=preserve "
        "SubmitTime=2026-07-12T10:00:00 JobState=RUNNING NodeList=worker-0 "
        "Partition=gpu Priority=100 Reason=None Requeue=1 Restarts=0 TimeLimit=01:00:00"
    )
    identity = {
        "job_id": "42",
        "user_id": "alice(1000)",
        "job_name": "preserve",
        "submit_time": "2026-07-12T10:00:00",
        "array_job_id": "",
        "array_task_id": "",
        "het_job_id": "",
        "het_job_offset": "",
    }
    return migration._SlurmJobControlObservation(
        job_id="42",
        identity=identity,
        identity_fingerprint=hashlib.sha256(
            migration._stable_json(identity).encode("utf-8")
        ).hexdigest(),
        job_state="RUNNING",
        reason="None",
        priority="100",
        held=False,
        record=record,
        record_fingerprint=hashlib.sha256(record.encode("utf-8")).hexdigest(),
    )


def _checkpoint_with_snapshot() -> dict[str, Any]:
    checkpoint: dict[str, Any] = {
        "target_ref": "external-cluster",
        "campaign_fingerprint": "campaign-fingerprint",
        "slurm": {"action_journal": new_slurm_action_journal()},
    }
    migration._checkpoint_slurm_action_job_snapshot(
        checkpoint["slurm"]["action_journal"],
        job=_job(),
        observation=_observation(),
    )
    return checkpoint


def _valid_v4_checkpoint(journal: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
        "target_ref": "external-cluster",
        "campaign_fingerprint": "campaign-fingerprint",
        "current_segment_id": "segment-1",
        "completed_segment_ids": [],
        "segment_state": {},
        "pending_phase": "controller-ha-bridge",
        "planned_phases": ["controller-ha-bridge"],
        "completed_phases": ["discovery-and-plan"],
        "operation_intent": {
            "segment_id": "segment-1",
            "idempotency_key": "a" * 64,
            "attempt_state": "intent-recorded",
            "intended_postcondition": {},
            "resources": {},
            "provider_operations": [],
        },
        "compensation_obligations": [],
        "phase_state": {},
        "slurm": {"action_journal": journal},
    }


def test_concurrent_journal_merge_preserves_both_writers() -> None:
    disk = new_slurm_action_journal()
    local = copy.deepcopy(disk)
    binding = migration._slurm_action_binding_from_observation(_observation())
    enqueue_slurm_action(
        disk,
        kind="cancel",
        binding=binding,
        intended_postcondition={"terminal_or_absent": True},
        action_id="disk-action",
    )
    enqueue_slurm_action(
        local,
        kind="hold",
        binding=SlurmJobActionBinding(
            job_id="43",
            user_id="bob(1001)",
            submit_time="2026-07-12T10:01:00",
            restart_baseline=0,
            identity_fingerprint="identity-43",
            lineage_fingerprint="lineage-43",
        ),
        intended_postcondition={"held": True},
        action_id="local-action",
    )

    merged = migration._merge_slurm_action_journals(local, disk)

    assert [item["action_id"] for item in merged["actions"]] == [
        "disk-action",
        "local-action",
    ]


def test_concurrent_journal_merge_rejects_same_binding_mutations() -> None:
    disk = new_slurm_action_journal()
    local = copy.deepcopy(disk)
    binding = migration._slurm_action_binding_from_observation(_observation())
    enqueue_slurm_action(
        disk,
        kind="cancel",
        binding=binding,
        intended_postcondition={"terminal_or_absent": True},
        action_id="disk-action",
    )
    enqueue_slurm_action(
        local,
        kind="hold",
        binding=binding,
        intended_postcondition={"held": True},
        action_id="local-action",
    )

    with pytest.raises(
        ValueError,
        match="multiple active mutating actions for immutable job binding 42",
    ):
        migration._merge_slurm_action_journals(local, disk)


def test_checkpoint_writer_merges_concurrent_tui_action_under_lock(tmp_path: Path) -> None:
    base = new_slurm_action_journal()
    local_checkpoint = _valid_v4_checkpoint(copy.deepcopy(base))
    disk_checkpoint = _valid_v4_checkpoint(copy.deepcopy(base))
    binding = migration._slurm_action_binding_from_observation(_observation())
    enqueue_slurm_action(
        local_checkpoint["slurm"]["action_journal"],
        kind="cancel",
        binding=binding,
        intended_postcondition={"terminal_or_absent": True},
        action_id="upgrade-writer",
    )
    enqueue_slurm_action(
        disk_checkpoint["slurm"]["action_journal"],
        kind="refresh",
        binding=binding,
        intended_postcondition={"controller_observed": True},
        action_id="tui-writer",
    )
    path = tmp_path / ".nebius-cxcli" / "checkpoint.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(disk_checkpoint), encoding="utf-8")

    migration._write_checkpoint(path, local_checkpoint)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert [action["action_id"] for action in written["slurm"]["action_journal"]["actions"]] == [
        "tui-writer",
        "upgrade-writer",
    ]
    assert [
        action["action_id"] for action in local_checkpoint["slurm"]["action_journal"]["actions"]
    ] == ["tui-writer", "upgrade-writer"]
    assert (path.parent / "checkpoint.json.lock").stat().st_mode & 0o777 == 0o600


def test_managed_checkpoint_writer_merges_concurrent_jobs_action(tmp_path: Path) -> None:
    base = new_slurm_action_journal()
    local_checkpoint: dict[str, Any] = {
        "schema": cli.SOPERATOR_UPGRADE_CHECKPOINT_SCHEMA,
        "slurm": {"action_journal": copy.deepcopy(base)},
    }
    disk_checkpoint: dict[str, Any] = {
        "schema": cli.SOPERATOR_UPGRADE_CHECKPOINT_SCHEMA,
        "slurm": {"action_journal": copy.deepcopy(base)},
    }
    binding = migration._slurm_action_binding_from_observation(_observation())
    enqueue_slurm_action(
        local_checkpoint["slurm"]["action_journal"],
        kind="cancel",
        binding=binding,
        intended_postcondition={"terminal_or_absent": True},
        action_id="managed-upgrade-writer",
    )
    enqueue_slurm_action(
        disk_checkpoint["slurm"]["action_journal"],
        kind="refresh",
        binding=binding,
        intended_postcondition={"controller_observed": True},
        action_id="managed-tui-writer",
    )
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(disk_checkpoint), encoding="utf-8")

    cli._write_soperator_upgrade_checkpoint(path, local_checkpoint)  # noqa: SLF001

    written = json.loads(path.read_text(encoding="utf-8"))
    action_ids = [action["action_id"] for action in written["slurm"]["action_journal"]["actions"]]
    assert action_ids == ["managed-tui-writer", "managed-upgrade-writer"]
    assert [
        action["action_id"] for action in local_checkpoint["slurm"]["action_journal"]["actions"]
    ] == action_ids


def test_checkpoint_writer_preserves_concurrent_login_exit_acknowledgement(
    tmp_path: Path,
) -> None:
    base = new_slurm_action_journal()
    fingerprint = "a" * 64
    record_slurm_login_observation(
        base,
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
    local_checkpoint = _valid_v4_checkpoint(copy.deepcopy(base))
    disk_checkpoint = _valid_v4_checkpoint(copy.deepcopy(base))
    migration.acknowledge_slurm_login_exit(
        disk_checkpoint["slurm"]["action_journal"],
        socket_fingerprint=fingerprint,
        acknowledged_by="ext-soperator-jobs-cli",
        disposition=migration.SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
        acknowledged_at="2026-07-12T10:03:00Z",
    )
    path = tmp_path / ".nebius-cxcli" / "checkpoint.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(disk_checkpoint), encoding="utf-8")

    migration._write_checkpoint(path, local_checkpoint)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["slurm"]["action_journal"]["login"]["exit_acknowledgements"] == [
        {
            "socket_fingerprint": fingerprint,
            "absence_observed_at": "2026-07-12T10:02:00Z",
                "acknowledged_at": "2026-07-12T10:03:00Z",
                "acknowledged_by": "ext-soperator-jobs-cli",
                "disposition": migration.SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
            }
        ]


def test_tui_writer_never_overwrites_non_action_checkpoint_progress(tmp_path: Path) -> None:
    disk_journal = new_slurm_action_journal()
    disk_checkpoint = _valid_v4_checkpoint(disk_journal)
    disk_checkpoint["pending_reason"] = "upgrade-writer-authoritative"
    tui_journal = copy.deepcopy(disk_journal)
    enqueue_slurm_action(
        tui_journal,
        kind="refresh",
        binding=migration._slurm_action_binding_from_observation(_observation()),
        intended_postcondition={"controller_observed": True},
        action_id="tui-action",
    )
    path = tmp_path / ".nebius-cxcli" / "checkpoint.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(disk_checkpoint), encoding="utf-8")

    migration._write_external_upgrade_action_journal(path, tui_journal)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["pending_reason"] == "upgrade-writer-authoritative"
    assert [action["action_id"] for action in written["slurm"]["action_journal"]["actions"]] == [
        "tui-action"
    ]


def test_concurrent_dispatch_claim_fails_if_broker_entered_accept_only() -> None:
    local = new_slurm_action_journal()
    set_slurm_action_broker_mode(local, "dispatch-enabled")
    action = enqueue_slurm_action(
        local,
        kind="cancel",
        binding=migration._slurm_action_binding_from_observation(_observation()),
        intended_postcondition={"terminal_or_absent": True},
    )
    disk = copy.deepcopy(local)
    transition_slurm_action(
        local,
        action_id=str(action["action_id"]),
        state="Dispatching",
        authority_epoch="source-1",
    )
    set_slurm_action_broker_mode(disk, "accept-only")

    with pytest.raises(RuntimeError, match="accept-only before dispatch claim"):
        migration._merge_slurm_action_journals(local, disk)


def test_late_tui_intent_cannot_cross_final_cleanup_admission_boundary() -> None:
    durable = new_slurm_action_journal()
    set_slurm_action_broker_mode(durable, "dispatch-enabled")
    stale_tui = copy.deepcopy(durable)
    enqueue_slurm_action(
        stale_tui,
        kind="cancel",
        binding=migration._slurm_action_binding_from_observation(_observation()),
        intended_postcondition={"terminal_or_absent": True},
        action_id="late-cleanup-action",
    )
    set_slurm_action_broker_mode(durable, "accept-only")
    set_slurm_action_broker_mode(durable, SLURM_ACTION_ADMISSION_CLOSED)
    stale_tui["broker_mode_updated_at"] = "2026-07-12T10:00:00+00:00"
    durable["broker_mode_updated_at"] = "2026-07-12T10:01:00+00:00"

    with pytest.raises(
        RuntimeError,
        match="admission closed before the concurrent TUI intent was durably accepted",
    ):
        migration._merge_slurm_action_journals(stale_tui, durable)

    assert durable["broker_mode"] == SLURM_ACTION_ADMISSION_CLOSED
    assert durable["actions"] == []


def test_final_cleanup_closure_preserves_a_concurrently_durable_action() -> None:
    cleanup = new_slurm_action_journal()
    set_slurm_action_broker_mode(cleanup, "dispatch-enabled")
    durable = copy.deepcopy(cleanup)
    enqueue_slurm_action(
        durable,
        kind="hold",
        binding=migration._slurm_action_binding_from_observation(_observation()),
        intended_postcondition={"held": True},
        action_id="accepted-before-cleanup-closure",
    )
    set_slurm_action_broker_mode(cleanup, "accept-only")
    set_slurm_action_broker_mode(cleanup, SLURM_ACTION_ADMISSION_CLOSED)
    durable["broker_mode_updated_at"] = "2026-07-12T10:00:00+00:00"
    cleanup["broker_mode_updated_at"] = "2026-07-12T10:01:00+00:00"

    merged = migration._merge_slurm_action_journals(cleanup, durable)

    assert merged["broker_mode"] == SLURM_ACTION_ADMISSION_CLOSED
    assert [item["action_id"] for item in merged["actions"]] == ["accepted-before-cleanup-closure"]
    assert merged["actions"][0]["state"] == "Queued"
    assert durable["actions"][0]["state"] == "Queued"
    assert durable["broker_mode"] == "dispatch-enabled"


def test_jobs_screen_queues_from_snapshot_during_controller_outage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = _checkpoint_with_snapshot()
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(
        migration,
        "_active_external_upgrade_jobs_checkpoint",
        lambda **_kwargs: checkpoint,
    )
    monkeypatch.setattr(
        migration,
        "soperator_migration_checkpoint_path",
        lambda *_args, **_kwargs: checkpoint_path,
    )
    monkeypatch.setattr(migration, "_target_kube_context", lambda *_args: "ctx")
    monkeypatch.setattr(
        migration,
        "_external_upgrade_all_slurm_jobs",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("controller unavailable")),
    )
    monkeypatch.setattr(
        migration,
        "_write_external_upgrade_action_journal",
        lambda _path, _journal: None,
    )

    result = migration.run_external_soperator_upgrade_jobs(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        prompt_runner=lambda _jobs, **_kwargs: ("cancel", ("42",)),
    )

    assert result == checkpoint_path
    [action] = checkpoint["slurm"]["action_journal"]["actions"]
    assert action["state"] == "Queued"
    assert action["binding"]["job_id"] == "42"
    assert action["binding"]["user_id"] == "alice(1000)"
    assert action["binding"]["submit_time"] == "2026-07-12T10:00:00"
    assert action["binding"]["restart_baseline"] == 0
    serialized_journal = json.dumps(checkpoint["slurm"]["action_journal"])
    assert "kubeconfig" not in serialized_journal.lower()
    assert "bearer" not in serialized_journal.lower()
    assert '"ctx"' not in serialized_journal


def test_jobs_prompt_callback_and_returned_raw_action_enqueue_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = _checkpoint_with_snapshot()
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(
        migration,
        "_active_external_upgrade_jobs_checkpoint",
        lambda **_kwargs: checkpoint,
    )
    monkeypatch.setattr(
        migration,
        "soperator_migration_checkpoint_path",
        lambda *_args, **_kwargs: checkpoint_path,
    )
    monkeypatch.setattr(migration, "_target_kube_context", lambda *_args: "ctx")
    monkeypatch.setattr(
        migration,
        "_external_upgrade_all_slurm_jobs",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("controller unavailable")),
    )
    monkeypatch.setattr(
        migration,
        "_write_external_upgrade_action_journal",
        lambda _path, _journal: None,
    )

    def _callback_prompt(
        jobs: tuple[AffectedSlurmJob, ...],
        **kwargs: Any,
    ) -> tuple[str, tuple[str, ...]]:
        kwargs["action_handler"]("cancel", ("42",), tuple(jobs))
        return "cancel", ("42",)

    migration.run_external_soperator_upgrade_jobs(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        prompt_runner=_callback_prompt,
    )

    actions = checkpoint["slurm"]["action_journal"]["actions"]
    assert len(actions) == 1
    assert actions[0]["kind"] == "cancel"
    assert actions[0]["state"] == "Queued"


def test_jobs_screen_reconnect_drains_queued_action_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = _checkpoint_with_snapshot()
    journal = checkpoint["slurm"]["action_journal"]
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=migration._slurm_action_binding_from_observation(_observation()),
        intended_postcondition=migration._slurm_action_intended_postcondition(
            kind="cancel",
            pre_observation=_observation().as_payload(),
        ),
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(
        migration,
        "_active_external_upgrade_jobs_checkpoint",
        lambda **_kwargs: checkpoint,
    )
    monkeypatch.setattr(
        migration,
        "soperator_migration_checkpoint_path",
        lambda *_args, **_kwargs: checkpoint_path,
    )
    monkeypatch.setattr(migration, "_target_kube_context", lambda *_args: "ctx")
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
    monkeypatch.setattr(
        migration,
        "_write_external_upgrade_action_journal",
        lambda _path, _journal: None,
    )
    rpc_calls: list[tuple[str, ...]] = []

    def _exec(**kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        rpc_calls.append(tuple(kwargs["args"]))
        return migration.SoperatorMigrationCommandResult(tuple(kwargs["args"]), 0, "", "")

    monkeypatch.setattr(migration, "_kubectl_exec_login", _exec)

    migration.run_external_soperator_upgrade_jobs(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        prompt_runner=lambda _jobs, **_kwargs: ("jobs-cleared", ()),
    )

    assert [item["state"] for item in journal["actions"]] == ["Dispatching"]
    assert rpc_calls[0] == ("scancel", "42")
    assert len(rpc_calls) == 2
    sacct_call = rpc_calls[1]
    assert sacct_call[0] == "sacct"
    assert "--duplicates" in sacct_call
    assert sacct_call[sacct_call.index("--jobs") + 1] == "42"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "requires an active v6 upgrade checkpoint"),
        ("{not-json", "checkpoint is invalid JSON"),
        (
            json.dumps({"schema": "nebius-cxcli-ext-soperator-upgrade-journal/v3"}),
            "Unsupported external Soperator upgrade checkpoint schema",
        ),
    ],
)
def test_jobs_checkpoint_rejects_missing_invalid_or_non_v6(
    tmp_path: Path,
    content: str | None,
    message: str,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    if content is not None:
        checkpoint_path.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        migration._active_external_upgrade_jobs_checkpoint(
            checkpoint_path=checkpoint_path,
            target_ref="external-cluster",
            payload=_payload(),
        )


def test_jobs_checkpoint_enables_dispatch_while_source_singleton_is_authoritative(
    tmp_path: Path,
) -> None:
    checkpoint = _valid_v4_checkpoint(new_slurm_action_journal())
    checkpoint.pop("controller_bridge", None)
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    active = migration._active_external_upgrade_jobs_checkpoint(
        checkpoint_path=checkpoint_path,
        target_ref="external-cluster",
        payload=_payload(),
    )

    assert active["slurm"]["action_journal"]["broker_mode"] == "dispatch-enabled"


@pytest.mark.parametrize(
    ("complete", "bridge_stage", "message"),
    [
        (True, "", "completed checkpoint"),
        (False, "cleaned", "cleaned controller bridge"),
    ],
)
def test_jobs_checkpoint_rejects_completed_or_cleaned_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete: bool,
    bridge_stage: str,
    message: str,
) -> None:
    checkpoint = {
        "target_ref": "external-cluster",
        "campaign_fingerprint": "campaign-fingerprint",
        "controller_bridge": {"stage": bridge_stage} if bridge_stage else {},
        "slurm": {"action_journal": new_slurm_action_journal()},
    }
    monkeypatch.setattr(migration, "_load_checkpoint", lambda _path: checkpoint)
    monkeypatch.setattr(migration, "_checkpoint_run_complete", lambda _checkpoint: complete)

    with pytest.raises(RuntimeError, match=message):
        migration._active_external_upgrade_jobs_checkpoint(
            checkpoint_path=tmp_path / "checkpoint.json",
            target_ref="external-cluster",
            payload=_payload(),
        )


def test_jobs_command_records_only_current_fingerprint_bound_login_exit(
    tmp_path: Path,
) -> None:
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
    checkpoint = _valid_v4_checkpoint(journal)
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    acknowledgements = migration._acknowledge_external_upgrade_login_exits(  # noqa: SLF001
        checkpoint_path=checkpoint_path,
        target_ref="external-cluster",
        payload=_payload(),
        socket_fingerprints=(fingerprint,),
    )

    assert len(acknowledgements) == 1
    persisted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert (
        persisted["slurm"]["action_journal"]["login"]["exit_acknowledgements"][0][
            "socket_fingerprint"
        ]
        == fingerprint
    )

    with pytest.raises(ValueError, match="currently pending"):
        migration._acknowledge_external_upgrade_login_exits(  # noqa: SLF001
            checkpoint_path=checkpoint_path,
            target_ref="external-cluster",
            payload=_payload(),
            socket_fingerprints=("b" * 64,),
        )


def test_jobs_command_acknowledgement_exits_without_opening_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    fingerprint = "a" * 64
    monkeypatch.setattr(
        migration,
        "soperator_migration_checkpoint_path",
        lambda *_args, **_kwargs: checkpoint_path,
    )
    monkeypatch.setattr(
        migration,
        "_acknowledge_external_upgrade_login_exits",
        lambda **_kwargs: (
            {
                "socket_fingerprint": fingerprint,
                "absence_observed_at": "2026-07-12T10:02:00Z",
                "acknowledged_at": "2026-07-12T10:03:00Z",
                "acknowledged_by": "ext-soperator-jobs-cli",
            },
        ),
    )

    result = migration.run_external_soperator_upgrade_jobs(
        config_path=tmp_path / "config.yaml",
        target_ref="external-cluster",
        payload=_payload(),
        acknowledge_login_exit_fingerprints=(fingerprint,),
        prompt_runner=lambda *_args, **_kwargs: pytest.fail("prompt must not open"),
    )

    assert result == checkpoint_path


def test_ext_soperator_jobs_help_exposes_canonical_command() -> None:
    result = CliRunner().invoke(cli.app, ["ext-soperator", "jobs", "--help"])

    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "ext-soperator jobs [OPTIONS] CONFIG_YAML" in normalized
    assert "--target" in normalized
    assert "--acknowledge-login-exit" in normalized
    assert "--authorize-login-timeout-continuation" in normalized
    assert "actions remain durably Queued" in normalized
    assert "Indeterminate" in normalized


def test_ext_soperator_jobs_uses_temporary_cluster_handoff_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_payload = _payload()
    source_payload["deploy"]["targets"][0]["cluster_id"] = "mk8scluster-example"
    execution_payload = copy.deepcopy(source_payload)
    execution_payload["deploy"]["targets"][0]["kube_context"] = "temporary-context"
    captured: dict[str, Any] = {}

    @contextmanager
    def _execute_payload(payload, *, target_ref):  # type: ignore[no-untyped-def]
        captured["context_input"] = (payload, target_ref)
        yield execution_payload

    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: source_payload)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_migration_target_ref",
        lambda _payload, *, target_ref: target_ref or "external-cluster",
    )
    monkeypatch.setattr(
        cli, "validate_soperator_onboarding_acceptance", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(cli, "_soperator_migration_execute_payload", _execute_payload)
    monkeypatch.setattr(
        cli,
        "run_external_soperator_upgrade_jobs",
        lambda **kwargs: captured.update({"jobs_kwargs": kwargs}) or tmp_path / "checkpoint.json",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "ext-soperator",
            "jobs",
            str(tmp_path / "config.yaml"),
            "--target",
            "external-cluster",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["context_input"] == (source_payload, "external-cluster")
    assert captured["jobs_kwargs"]["payload"] is execution_payload
    assert captured["jobs_kwargs"]["target_ref"] == "external-cluster"


def test_managed_soperator_jobs_help_exposes_shared_journal_contract() -> None:
    result = CliRunner().invoke(cli.app, ["soperator", "jobs", "--help"])

    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "soperator jobs [OPTIONS] CONFIG_YAML" in normalized
    assert "--target" in normalized
    assert "--acknowledge-login-exit" in normalized
    assert "--authorize-login-timeout-continuation" in normalized
    assert "controller authority epoch" in normalized


def test_managed_soperator_jobs_uses_managed_checkpoint_and_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_payload = {"apps": {"charts": []}}
    checkpoint_path = tmp_path / "checkpoint.json"
    captured: dict[str, Any] = {}
    target = cli._HelmChartUpgradeTarget(  # noqa: SLF001
        selector="apps:soperator@mk8s",
        chart_id="soperator",
        target_ref="mk8s",
    )

    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: source_payload)
    monkeypatch.setattr(
        cli,
        "_prompt_soperator_upgrade_target_if_needed",
        lambda **_kwargs: target,
    )
    monkeypatch.setattr(
        cli, "_soperator_upgrade_checkpoint_path", lambda *_args, **_kwargs: checkpoint_path
    )
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (object(), object(), {"deploy": {"targets": []}}),
    )
    monkeypatch.setattr(
        cli,
        "_manifest_kube_context_for_target",
        lambda _manifest, _target_ref: "managed-context",
    )
    monkeypatch.setattr(
        cli,
        "run_external_soperator_upgrade_jobs",
        lambda **kwargs: captured.update(kwargs) or checkpoint_path,
    )

    result = CliRunner().invoke(
        cli.app,
        ["soperator", "jobs", str(tmp_path / "config.yaml"), "--target", "mk8s"],
    )

    assert result.exit_code == 0, result.output
    assert captured["checkpoint_path_override"] == checkpoint_path
    assert captured["kube_context_override"] == "managed-context"
    assert captured["command_origin"] == "soperator-jobs-tui"
    assert callable(captured["checkpoint_loader"])
