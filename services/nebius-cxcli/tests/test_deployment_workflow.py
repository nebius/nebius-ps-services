from __future__ import annotations

import pytest

from nebius_cxcli.deployment_plan import (
    DeploymentAction,
    DeploymentPlan,
    DeploymentStage,
    DeploymentStageKind,
)
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState
from nebius_cxcli.deployment_workflow import StageAdmission, run_deployment
from test_deployment_state import Store, settings


def plan():
    return DeploymentPlan(
        DeploymentAction.UPGRADE,
        "cluster",
        "1.0.0",
        "1.1.0",
        ("capacity",),
        tuple(DeploymentStage(kind, {"stage": kind.value}) for kind in DeploymentStageKind),
    )


class Executor:
    def __init__(self, live, *, interrupt=False):
        self.live, self.interrupt = live, interrupt
        self.restored = False

    def acceptance_generations(self):
        return ()

    def recovery_cache(self):
        return {"source": "accepted-generation"}

    def admit(self, plan):
        self.live.append("admit-all")
        return tuple(
            StageAdmission(stage, {"resource_changes": []}, {"intent": stage.name.value})
            for stage in plan.stages
        )

    def restore_admissions(self, admissions):
        self.restored = True

    def verify(self, admission):
        return {"proof": "final"} if "completed" in self.live else None

    def execute(self, admission, *, recovering):
        self.live.append("maintenance-entry")
        if self.interrupt:
            raise RuntimeError("interrupted")
        self.live.extend(
            ["retire", "upgrade-survivors", "grow", "final-readiness", "restore", "completed"]
        )

    def verify_final(self, plan):
        return {"ready": True}


def test_one_campaign_owns_all_stages_and_preview_has_no_authority():
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    live = []
    preview = run_deployment(
        generation=generation,
        plan=plan(),
        state=state,
        executor=Executor(live),
        dry_run=True,
        controls={},
    )
    assert preview.preview and state.read() is None and live == ["admit-all"]
    run_deployment(
        generation=generation,
        plan=plan(),
        state=state,
        executor=Executor(live),
        dry_run=False,
        controls={},
    )
    assert live.count("maintenance-entry") == 1 and live.count("restore") == 1
    assert state.read().value["active"] is None


def test_initial_recovery_is_atomic_and_resumes_frozen_admission():
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    live = []
    original_begin = state.begin

    def crash_after_begin(*args, **kwargs):
        original_begin(*args, **kwargs)
        raise RuntimeError("crash after begin")

    state.begin = crash_after_begin
    with pytest.raises(RuntimeError, match="crash after begin"):
        run_deployment(
            generation=generation,
            plan=plan(),
            state=state,
            executor=Executor(live),
            dry_run=False,
            controls={},
        )
    assert state.read().value["active"]["recovery"] == {"source": "accepted-generation"}
    state.begin = original_begin
    resumed = Executor(live)
    run_deployment(
        generation=generation,
        plan=plan(),
        state=state,
        executor=resumed,
        dry_run=False,
        controls={},
    )
    assert resumed.restored and live.count("admit-all") == 1


@pytest.mark.parametrize("before,after", [("1m", "60s"), ("0s", "none"), ("0", "0s")])
def test_recovery_accepts_equivalent_duration_controls(before, after):
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    live = []
    with pytest.raises(RuntimeError, match="interrupted"):
        run_deployment(
            generation=generation,
            plan=plan(),
            state=state,
            executor=Executor(live, interrupt=True),
            dry_run=False,
            controls={"jobWaitTimeout": before, "jobRefreshInterval": "30s"},
        )
    active = state.read().value["active"]
    failed_stage = next(iter(active["stages"].values()))
    assert failed_stage["status"] == "recovery-required"
    assert failed_stage["failureType"] == "RuntimeError"
    assert active["recovery"] == {"source": "accepted-generation"}
    frozen = active["plan"]["controls"].copy()
    observed_controls = []

    class RecordingExecutor(Executor):
        def restore_admissions(self, admissions):
            observed_controls.append(state.read().value["active"]["plan"]["controls"].copy())
            return super().restore_admissions(admissions)

    run_deployment(
        generation=generation,
        plan=plan(),
        state=state,
        executor=RecordingExecutor(live),
        dry_run=False,
        controls={"jobWaitTimeout": after, "jobRefreshInterval": "30000ms"},
    )
    assert state.read().value["active"] is None
    assert observed_controls == [frozen]


@pytest.mark.parametrize(
    "changed",
    [
        {"jobWaitTimeout": "2m", "jobRefreshInterval": "30s"},
        {"jobWaitTimeout": "1m", "jobRefreshInterval": "31s"},
        {"jobWaitTimeout": "1m", "jobRefreshInterval": "30s", "skipValidations": True},
        {"jobWaitTimeout": "1m"},
        {"jobWaitTimeout": 60, "jobRefreshInterval": "30s"},
        {"jobWaitTimeout": "invalid", "jobRefreshInterval": "30s"},
    ],
)
def test_recovery_still_rejects_changed_or_invalid_controls(changed):
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    live = []
    with pytest.raises(RuntimeError, match="interrupted"):
        run_deployment(
            generation=generation,
            plan=plan(),
            state=state,
            executor=Executor(live, interrupt=True),
            dry_run=False,
            controls={"jobWaitTimeout": "1m", "jobRefreshInterval": "30s"},
        )
    original = state.read()
    with pytest.raises(RuntimeError, match="execution controls|duration"):
        run_deployment(
            generation=generation,
            plan=plan(),
            state=state,
            executor=Executor(live),
            dry_run=False,
            controls=changed,
        )
    assert state.read() == original
    assert live.count("maintenance-entry") == 1


def test_finish_is_remote_durable_before_later_stage_resume():
    from nebius_cxcli.deployment_recovery import checkpoint_execution
    from nebius_cxcli.soperator_acceptance import _CONTROL, AcceptanceControl, current_control

    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    live = []

    class Interrupting(Executor):
        def execute(self, admission, *, recovering):
            current_control().finish_requested = True
            checkpoint_execution()
            live.append("completed")
            raise RuntimeError("after finish checkpoint")

    first = _CONTROL.set(AcceptanceControl())
    try:
        with pytest.raises(RuntimeError, match="after finish"):
            run_deployment(
                generation=generation,
                plan=plan(),
                state=state,
                executor=Interrupting(live),
                dry_run=False,
                controls={},
            )
    finally:
        _CONTROL.reset(first)
    assert state.read().value["active"]["acceptance"]["finish_requested"] is True
    resumed = AcceptanceControl()
    token = _CONTROL.set(resumed)
    try:
        run_deployment(
            generation=generation,
            plan=plan(),
            state=state,
            executor=Executor(live),
            dry_run=False,
            controls={},
        )
        assert resumed.finish_requested
        assert state.read().value["accepted"]["evidence"]["acceptanceControl"]["finish_requested"]
    finally:
        _CONTROL.reset(token)
