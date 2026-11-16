from types import SimpleNamespace

import pytest

from nebius_vpngw import cli
from nebius_vpngw.deploy.controller_observation import progress_key


def inspection(progress=0, *, overall="TRANSITIONING", heartbeat=0):
    return SimpleNamespace(
        snapshot=SimpleNamespace(
            view=SimpleNamespace(
                overall=overall, action="wait" if overall == "TRANSITIONING" else "none"
            ),
            authority_digest="unchanged-authority",
            members=(
                SimpleNamespace(
                    node_id="node-a",
                    record={
                        "repair": {"operation_id": "retained", "healthy_observations": progress},
                        "heartbeat": {"timestamp": heartbeat},
                    },
                ),
            ),
        )
    )


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cli.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    return now


def test_progress_continues_past_stall_window_with_unchanged_overall(monkeypatch, clock):
    observations = iter(
        [
            inspection(0),
            inspection(1),
            inspection(1),
            inspection(2),
            inspection(2),
            inspection(2, overall="HEALTHY"),
        ]
    )
    monkeypatch.setattr(
        cli, "_inspect_vm_ha_status_with_region", lambda *_a, **_k: next(observations)
    )
    result, reason = cli._observe_vm_ha_controller_transition(
        None,
        inspection(),
        attempts=4,
        timeout_seconds=10,
        stall_timeout_seconds=3,
    )
    assert result.snapshot.view.overall == "HEALTHY"
    assert reason is None
    assert clock[0] == 6


def test_timestamps_and_repeated_states_do_not_count_as_progress(monkeypatch, clock):
    first = inspection()
    assert progress_key(first.snapshot) == progress_key(inspection(heartbeat=99).snapshot)
    monkeypatch.setattr(
        cli, "_inspect_vm_ha_status_with_region", lambda *_a, **_k: inspection(heartbeat=clock[0])
    )
    _, reason = cli._observe_vm_ha_controller_transition(
        None,
        first,
        timeout_seconds=10,
        stall_timeout_seconds=3,
    )
    assert reason == "controller-no-progress"
    assert clock[0] == 3


def test_progress_cannot_extend_operation_deadline(monkeypatch, clock):
    monkeypatch.setattr(
        cli, "_inspect_vm_ha_status_with_region", lambda *_a, **_k: inspection(int(clock[0]))
    )
    _, reason = cli._observe_vm_ha_controller_transition(
        None,
        inspection(),
        timeout_seconds=4,
        stall_timeout_seconds=3,
    )
    assert reason == "controller-observation-budget-exhausted"
    assert clock[0] == 4


def test_unavailable_observation_stays_pending_without_repair(monkeypatch, clock):
    monkeypatch.setattr(
        cli,
        "_inspect_vm_ha_status_with_region",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()),
    )
    _, reason = cli._observe_vm_ha_controller_transition(None, inspection())
    assert reason == "controller-observation-unavailable"


def test_contradictory_progress_is_distinct(monkeypatch, clock):
    first = inspection()
    first.snapshot.members[0].record["transfer_progress"] = {"unexpected": "data"}
    _, reason = cli._observe_vm_ha_controller_transition(None, first)
    assert reason == "controller-observation-contradictory"
    assert clock[0] == 0


def test_progress_uses_existing_deadline_beyond_default_observation_window(monkeypatch, clock):
    from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_budget

    def observe(*_args, **_kwargs):
        return inspection(int(clock[0]), overall="HEALTHY" if clock[0] >= 350 else "TRANSITIONING")

    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", observe)
    with vm_ha_observation_budget(seconds=400, clock=lambda: clock[0]):
        result, reason = cli._observe_vm_ha_controller_transition(None, inspection())
    assert result.snapshot.view.overall == "HEALTHY"
    assert reason is None
    assert clock[0] == 350


def test_stall_after_early_progress_reports_stall_not_exhausted_deadline(monkeypatch, clock):
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", lambda *_a, **_k: inspection(1))
    _, reason = cli._observe_vm_ha_controller_transition(
        None, inspection(), timeout_seconds=30, stall_timeout_seconds=3
    )
    assert reason == "controller-no-progress"
    assert clock[0] == 4


def test_inherited_deadline_expiry_is_pending_budget_not_unavailable(monkeypatch, clock):
    from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_budget

    monkeypatch.setattr(
        cli, "_inspect_vm_ha_status_with_region", lambda *_a, **_k: inspection(int(clock[0]))
    )
    with pytest.raises(TimeoutError), vm_ha_observation_budget(seconds=4, clock=lambda: clock[0]):
        _, reason = cli._observe_vm_ha_controller_transition(None, inspection())
        assert reason == "controller-observation-budget-exhausted"
        assert clock[0] == 4
