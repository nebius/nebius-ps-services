from copy import deepcopy

import pytest

from nebius_vpngw.deploy.activation_driver import Completion
from nebius_vpngw.deploy.activation_recovery import Diagnosis, Recovery


@pytest.fixture
def recovery(tmp_path):
    state = {"time": 100.0, "boot": "boot-a"}
    owner = Recovery(
        tmp_path,
        execution="a" * 32,
        authority="b" * 64,
        clock=lambda: state["time"],
        boot=lambda: state["boot"],
    )
    owner.initialize()
    return owner, state


def test_three_attempts_and_no_restart_allowance(recovery):
    owner, state = recovery
    for ordinal in range(1, 4):
        attempt = owner.reserve("activate-member", Diagnosis("service", str(ordinal) * 64))
        assert attempt["ordinal"] == ordinal
        attempt = owner.transition(attempt, "dispatched")
        owner.transition(attempt, "failed")
    resumed = Recovery(
        owner.path.parents[1],
        execution=owner.execution,
        authority=owner.authority,
        clock=lambda: state["time"],
        boot=lambda: state["boot"],
    )
    with pytest.raises(RuntimeError, match="exhausted"):
        resumed.reserve("activate-member", Diagnosis("service", "4" * 64))


def test_uncertain_effect_blocks_resubmission_and_is_settled_by_readback(recovery):
    owner, _ = recovery
    attempt = owner.reserve("activate-member", Diagnosis("service", "c" * 64))
    attempt = owner.transition(attempt, "dispatched")
    attempt = owner.transition(attempt, "uncertain")
    with pytest.raises(RuntimeError, match="observation"):
        owner.reserve("activate-member", Diagnosis("configuration", "d" * 64))
    owner.transition(attempt, "verified")
    assert len(owner.history()) == 1


def test_deadline_and_boot_change_never_replenish_attempt(recovery):
    owner, state = recovery
    attempt = owner.reserve("activate-member", Diagnosis("service", "c" * 64))
    state["time"] += 119
    assert owner.remaining(attempt) == 1
    state["boot"] = "boot-b"
    assert owner.remaining(attempt) == 0
    attempt = owner.transition(attempt, "dispatched")
    assert owner.transition(attempt, "failed")["ordinal"] == 1


def test_same_repair_not_repeated_but_distinct_repair_allowed(recovery):
    owner, _ = recovery
    diagnosis = Diagnosis("service", "c" * 64)
    attempt = owner.transition(owner.reserve("stage", diagnosis), "dispatched")
    owner.transition(attempt, "failed")
    with pytest.raises(RuntimeError, match="unchanged"):
        owner.reserve("stage", diagnosis)
    assert owner.reserve("stage", Diagnosis("configuration", "c" * 64))["ordinal"] == 2


def test_foreign_history_and_changed_receipt_rejected(recovery):
    owner, _ = recovery
    attempt = owner.reserve("stage", Diagnosis("service", "c" * 64))
    altered = deepcopy(attempt)
    altered["target_at"] += 120
    with pytest.raises(RuntimeError, match="changed"):
        owner.transition(altered, "dispatched")
    other = Recovery(owner.path.parents[1], execution=owner.execution, authority="d" * 64)
    with pytest.raises(RuntimeError, match="authority"):
        other.history()


def test_missing_history_never_mints_resume_budget(recovery):
    owner, _ = recovery
    owner.path.unlink()
    with pytest.raises(RuntimeError, match="missing"):
        owner.reserve("stage", Diagnosis("service", "c" * 64))


def test_exhausted_resume_never_reexecutes_initial_activation(recovery):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, _ = recovery
    for ordinal in range(3):
        item = owner.transition(
            owner.reserve("stage", Diagnosis("service", str(ordinal) * 64)), "dispatched"
        )
        owner.transition(item, "failed")
    effects = []
    with pytest.raises(RuntimeError, match="exhausted"):
        execute(
            owner,
            "stage",
            effect=effects.append,
            observe=lambda _: {"settled": True, "diagnosis": Diagnosis("configuration", "f" * 64)},
            repair=lambda *_: pytest.fail("repair dispatched"),
            finish=lambda _: None,
        )
    assert effects == []


def test_lost_initial_reply_is_resolved_by_complete_readback(recovery):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, _ = recovery

    def disconnected(_):
        assert owner.initial("stage") == "started"
        raise TimeoutError("reply lost")

    execute(
        owner,
        "stage",
        effect=disconnected,
        observe=lambda _: {"settled": True, "complete": True},
        repair=lambda *_: pytest.fail("duplicate repair"),
        finish=lambda _: None,
    )
    assert not owner.history()
    assert owner.initial("stage") == "verified"
    execute(
        owner,
        "stage",
        effect=lambda _: pytest.fail("initial replayed"),
        observe=lambda _: {"settled": True, "complete": True},
        repair=lambda *_: pytest.fail("repair replayed"),
        finish=lambda _: None,
    )
    assert owner.initial("stage") == "verified"


@pytest.mark.parametrize("state", ["reserved", "dispatched"])
@pytest.mark.parametrize("initial", [None, "started"])
def test_complete_readback_preserves_existing_attempt_accounting(recovery, state, initial):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, _ = recovery
    if initial is not None:
        owner.initial("stage", state=initial)
    attempt = owner.reserve("stage", Diagnosis("service", "c" * 64))
    if state == "dispatched":
        attempt = owner.transition(attempt, state)
    finished = []
    execute(
        owner,
        "stage",
        effect=lambda _: pytest.fail("completed effect replayed"),
        observe=lambda _: {"settled": True, "complete": True},
        repair=lambda *_: pytest.fail("new repair admitted"),
        finish=finished.append,
    )
    terminal = "failed" if state == "reserved" else "verified"
    assert finished == [dict(attempt, state=terminal)]
    assert owner.history() == [dict(attempt, state=terminal, cleaned=True)]
    expected = "verified" if state == "reserved" and initial == "started" else initial
    assert owner.initial("stage") == expected


@pytest.mark.parametrize("retryable", [True, False])
@pytest.mark.parametrize("typed_first", [True, False])
def test_recovery_keeps_first_effect_and_latest_observation_separate(
    recovery, tmp_path, retryable, typed_first
):
    from tests.unit.test_apply_result import plan

    from nebius_vpngw.deploy.activation_recovery import execute
    from nebius_vpngw.deploy.apply_command import CommandJournal
    from nebius_vpngw.deploy.apply_diagnostics import (
        Failure,
        ObservationUnavailable,
        VerificationFailure,
        observation_pending,
        unavailable_observation,
    )
    from nebius_vpngw.deploy.apply_result import ApplyReport

    owner, _ = recovery
    original = Failure("prepare-packages", "checkpoint-admission-failed", ("cause-runtime",))
    first = VerificationFailure(original, original) if typed_first else RuntimeError("private")
    latest = ObservationUnavailable(
        "package-response", "observation-deadline-expired", retryable=retryable
    )
    effects = []

    def effect(_):
        effects.append(1)
        raise first

    def observe(_):
        raise latest

    with pytest.raises(VerificationFailure) as caught:
        execute(
            owner,
            "packages",
            effect=effect,
            observe=observe,
            repair=lambda *_: pytest.fail("unknown observation selected repair"),
            finish=lambda _: pytest.fail("unknown observation completed work"),
        )
    failure = caught.value
    expected = original if typed_first else Failure("activation-recovery", "initial-effect-failed")
    assert failure.first == expected and failure.latest == latest.first
    assert unavailable_observation(failure) is latest
    assert observation_pending(failure) is retryable
    assert effects == [1] and owner.history() == [] and owner.initial("packages") == "started"
    journal = CommandJournal("project", "gateway", root=tmp_path / "journal")
    with journal:
        journal.begin(plan())
    report = ApplyReport(started=True, journal=journal)
    report.failed(failure)
    execution = journal.snapshot()["transaction"]["execution_id"]
    reopened = CommandJournal("project", "gateway", root=journal.root)
    assert reopened.failure_for(execution) == expected
    assert reopened.failure_for(execution, latest=True) == latest.first
    assert "private" not in str(report.mapping())


def test_unresolved_remote_writer_keeps_attempt_uncertain(recovery):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, _ = recovery
    attempt = owner.transition(owner.reserve("stage", Diagnosis("service", "c" * 64)), "dispatched")
    observations = iter([{"settled": False}, {"settled": False}, {"complete": True}])
    execute(
        owner,
        "stage",
        effect=lambda _: pytest.fail("replayed"),
        observe=lambda _: next(observations),
        repair=lambda *_: pytest.fail("replayed"),
        finish=lambda _: None,
    )
    assert owner.history()[-1]["state"] == "verified"
    assert owner.history()[-1]["target_at"] == attempt["target_at"]
    assert len(owner.history()) == 1


def test_repair_dispatch_is_persisted_and_uses_original_deadline(recovery):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, clock = recovery
    calls = []

    def effect(deadline):
        calls.append(deadline)
        if len(calls) == 1:
            raise RuntimeError("initial failure")
        assert deadline is None
        clock["time"] += 5

    def repair(attempt, _):
        assert owner.history()[-1]["state"] == "dispatched"
        clock["time"] += 7

    observations = iter(
        [
            {"settled": True, "complete": False, "diagnosis": Diagnosis("service", "c" * 64)},
            {"settled": True, "complete": True},
        ]
    )
    execute(
        owner,
        "stage",
        effect=effect,
        observe=lambda _: next(observations),
        repair=repair,
        finish=lambda _: None,
    )
    assert calls == [None, None]
    assert owner.history()[-1]["state"] == "verified"


def test_verified_but_uncleaned_attempt_retries_cleanup_before_return(recovery):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, clock = recovery
    attempt = owner.transition(owner.reserve("stage", Diagnosis("service", "c" * 64)), "dispatched")
    attempt = owner.transition(attempt, "verified")
    clock["time"] = 240  # Native verification may be healthy after the old deadline.
    cleanups = []
    execute(
        owner,
        "stage",
        effect=lambda _: pytest.fail("replayed effect"),
        observe=lambda _: {"settled": True, "complete": True},
        repair=lambda *_: pytest.fail("replayed repair"),
        finish=cleanups.append,
    )
    assert cleanups == [attempt]
    assert owner.history()[-1]["cleaned"] is True


def test_late_complete_readback_cleans_guest_enforcement_without_new_effect(recovery):
    from nebius_vpngw.deploy.activation_recovery import execute

    owner, clock = recovery
    owner.transition(owner.reserve("stage", Diagnosis("service", "c" * 64)), "dispatched")
    clock["time"] = 240
    cleaned = []
    execute(
        owner,
        "stage",
        effect=lambda _: pytest.fail("new effect"),
        observe=lambda _: {"complete": True, "settled": True},
        repair=lambda *_: pytest.fail("new repair"),
        finish=cleaned.append,
    )
    assert cleaned[0]["state"] == "verified"
    assert owner.history()[0]["cleaned"]


def test_active_locked_owner_continuation_never_dispatches_service_repair(tmp_path):
    import time
    from types import SimpleNamespace as NS

    from nebius_vpngw.deploy.activation_driver import activate

    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    owner.initialize()
    owner.initial("verify-active-forwarding-and-routes-node", state="started")
    complete = []
    controls = []

    def control(_target, _instance, _local, request, **_):
        controls.append(request)
        return {"clock": time.monotonic(), "boot": "boot"}

    activate(
        NS(activation_control=control),
        "target",
        NS(),
        {},
        recovery=owner,
        phase="verify-active-forwarding-and-routes",
        phase_effect=complete.append,
        phase_complete=lambda _: Completion.COMPLETE if complete else Completion.INCOMPLETE,
        phase_resume=lambda _: Completion.COMPLETE,
        staged_receipt=NS(node_id="node", generation_id="c" * 64, staged_file_sha256="d" * 64),
        runtime_binding=NS(cluster_id="cluster"),
        apply_operation_id="e" * 64,
    )
    assert [request["action"] for request in controls] == ["inspect-unlock", "arm", "finish"]
    assert controls[1]["unlock_only"] is True and controls[2]["verified"] is True
    assert owner.history()[0]["action"] == "unlock"


@pytest.mark.parametrize("materialized", [False, None, True])
def test_activation_resume_requires_positive_configuration_evidence(tmp_path, materialized):
    """Compose the real driver and reopened ledger; guest controls are synthetic."""
    import time
    from types import SimpleNamespace as NS

    from nebius_vpngw.deploy.activation_driver import activate
    from nebius_vpngw.deploy.authority_observation import AuthorityUnavailable

    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    owner.initialize()
    controls, effects = [], []
    unavailable = True
    observed_materialized = materialized
    receipt = NS(node_id="node", generation_id="c" * 64, staged_file_sha256="d" * 64)
    operation = "e" * 64

    def push(*_args, **kwargs):
        nonlocal observed_materialized
        assert kwargs["apply_operation_id"] == operation
        if unavailable:
            raise AuthorityUnavailable("compute-read-timeout")
        effects.append(kwargs["staged_receipt"])
        observed_materialized = True

    def control(_target, _instance, _local, request, **_kwargs):
        controls.append(request)
        assert request["lock"] == {
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
            "cluster_id": "cluster",
            "node_id": "node",
            "generation_id": receipt.generation_id,
            "operation_id": operation,
        }
        assert request["configuration"] == receipt.staged_file_sha256
        return {
            "clock": time.monotonic(),
            "boot": "boot",
            "units": {"unit": {"Job": "", "ControlPID": "0", "ActiveState": "active"}},
            "materialized": observed_materialized,
            "complete": observed_materialized is True,
        }

    def run(recovery):
        activate(
            NS(push_config_and_reload=push, activation_control=control),
            "target",
            NS(),
            {},
            recovery=recovery,
            staged_receipt=receipt,
            runtime_binding=NS(cluster_id="cluster"),
            apply_operation_id=operation,
        )

    with pytest.raises(AuthorityUnavailable):
        run(owner)
    assert owner.initial("activate-node") == "waiting-authority"
    assert owner.history() == [] and controls == [] and effects == []

    resumed = Recovery(tmp_path, execution=owner.execution, authority=owner.authority)
    unavailable = False
    if materialized is None:
        with pytest.raises(RuntimeError, match="no proven safe local repair"):
            run(resumed)
    else:
        run(resumed)
    if materialized is False:
        assert effects == [receipt]
        assert [item["action"] for item in controls] == [
            "inspect",
            "arm",
            "service",
            "mark",
            "inspect",
            "finish",
        ]
        assert controls[2]["action_kind"] == "configuration"
        assert len(resumed.history()) == 1
        assert resumed.history()[0]["action"] == "configuration"
        assert resumed.history()[0]["state"] == "verified"
        assert resumed.history()[0]["cleaned"]
    else:
        assert effects == [] and resumed.history() == []
        assert [item["action"] for item in controls] == ["inspect"]
