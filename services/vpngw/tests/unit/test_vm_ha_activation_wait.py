"""Activation diagnostics must observe deadlines without changing authority."""

import io

import pytest

from nebius_vpngw import cli
from nebius_vpngw.deploy.apply_diagnostics import VerificationFailure

HEALTHY = {"heartbeat": {"peer_v6_fresh": True}}
STALE = {"heartbeat": {"peer_v6_fresh": False}}


def failed(kind="tls", attempts=10):
    return {"heartbeat": {"peer_send_error_kind": kind, "peer_send_consecutive_failures": attempts}}


@pytest.fixture
def clock(monkeypatch):
    value = [0.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: value[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: value.__setitem__(0, value[0] + seconds))
    return value


def script(monkeypatch, values):
    pending = list(values)

    def fetch(**kwargs):
        item = pending.pop(0) if len(pending) > 1 else pending[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(cli, "_fetch_vm_ha_agent_status", fetch)


@pytest.mark.parametrize("kind", ["timeout", "unreachable", "refused", "tls"])
def test_peer_failure_threshold_counts_attempts(kind):
    assert cli._vm_ha_peer_channel_classified_failure(failed(kind, 9)["heartbeat"]) is None
    assert cli._vm_ha_peer_channel_classified_failure(failed(kind, 10)["heartbeat"]) == (
        "peer-heartbeat-tls-failure" if kind == "tls" else "peer-heartbeat-endpoint-unreachable"
    )


@pytest.mark.parametrize(
    "values,outcome,latest,previous",
    [
        ([failed(), HEALTHY], "healthy", None, "peer-heartbeat-tls-failure"),
        ([STALE, failed()], "failed", "peer-heartbeat-tls-failure", "peer-heartbeat-tls-failure"),
        (
            [STALE, failed(), OSError("private")],
            "unknown",
            "status-observation-unavailable",
            "peer-heartbeat-tls-failure",
        ),
        ([failed(), STALE], "unknown", "peer-heartbeat-stale", "peer-heartbeat-tls-failure"),
        ([OSError("private")], "unknown", "status-observation-unavailable", None),
    ],
)
def test_gate_outcome_uses_latest_evidence_and_retains_history(
    monkeypatch, clock, values, outcome, latest, previous
):
    script(monkeypatch, values)
    result = cli._wait_for_vm_ha_peer_channel(phase="verify-peer-channel-node-0")
    assert result.outcome.value == outcome
    assert (result.failure.latest.predicate if result.failure else None) == latest
    assert (result.last_classified.predicate if result.last_classified else None) == previous
    if outcome != "healthy":
        assert clock[0] == 30.0
    if latest == "status-observation-unavailable" and previous:
        assert result.failure.first.predicate == "peer-heartbeat-stale"
        assert result.last_classified.observed_at == 2.0
    assert "private" not in repr(result)


def test_gate_rejects_late_recovery_without_losing_previous_failure(monkeypatch, clock):
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs["timeout_seconds"])
        if len(calls) == 1:
            return failed("refused")
        clock[0] = 31.0
        return HEALTHY

    monkeypatch.setattr(cli, "_fetch_vm_ha_agent_status", fetch)
    result = cli._wait_for_vm_ha_peer_channel()
    assert result.outcome is cli._VMHAPeerChannelOutcome.UNKNOWN
    assert result.failure.latest.predicate == "status-arrived-after-deadline"
    assert result.last_classified.observed_at == 0.0
    assert calls == [15.0, 15.0]


def test_gate_preserves_permanent_validation_failure(monkeypatch, clock):
    error = cli._VMHAAgentStatusPermanent("foreign identity")
    script(monkeypatch, [error])
    with pytest.raises(cli._VMHAAgentStatusPermanent) as caught:
        cli._wait_for_vm_ha_peer_channel()
    assert caught.value is error
    assert clock[0] == 0.0


def test_observations_use_effective_deadline_including_fetch_and_sleep(monkeypatch, clock):
    budgets, observations = [], []

    def fetch(**kwargs):
        budgets.append(kwargs["timeout_seconds"])
        clock[0] += 3.0
        return STALE

    monkeypatch.setattr(cli, "_fetch_vm_ha_agent_status", fetch)
    with pytest.raises(VerificationFailure):
        cli._wait_for_vm_ha_agent_status(
            predicate=lambda _: False,
            timeout_seconds=300.0,
            deadline=10.0,
            observation_callback=observations.append,
        )
    assert budgets == [10.0, 5.0]
    assert clock[0] == 10.0
    assert [(o.elapsed_seconds, o.remaining_seconds) for o in observations] == [
        (0.0, 10.0),
        (3.0, 7.0),
        (5.0, 5.0),
        (8.0, 2.0),
        (10.0, 0.0),
        (10.0, 0.0),
    ]
    assert observations[-1].complete
    assert observations[-1].observed_at == 8.0


@pytest.mark.parametrize("healthy", [False, True])
def test_broken_observer_does_not_change_gate_decision(monkeypatch, clock, healthy):
    script(monkeypatch, [failed(), HEALTHY if healthy else OSError("private")])

    def broken(_observation):
        raise RuntimeError("presentation failure")

    result = cli._wait_for_vm_ha_peer_channel(observation_callback=broken)
    assert result.outcome.value == ("healthy" if healthy else "unknown")
    assert result.last_classified.predicate == "peer-heartbeat-tls-failure"
    assert clock[0] == (2.0 if healthy else 30.0)


@pytest.mark.parametrize("succeeds", [False, True])
def test_noninteractive_summary_retains_unknown_history(monkeypatch, clock, succeeds):
    stream = io.StringIO()
    reporter = cli._VMHAProgressReporter(stream)
    progress = cli._VMHAActivationProgress(reporter)
    script(monkeypatch, [STALE, failed(), OSError("private")])
    try:
        with cli._vm_ha_progress_step(progress, cli._VMHAProgressPhase.VERIFY_FENCED):
            result = cli._wait_for_vm_ha_peer_channel(
                observation_callback=lambda o: progress.observe(0, "peer check", o)
            )
            progress.peer_result(0, result)
            assert stream.getvalue() == ""
            script(monkeypatch, [HEALTHY if succeeds else STALE])
            cli._wait_for_vm_ha_agent_status(
                predicate=cli._vm_ha_peer_channel_up,
                timeout_seconds=300.0,
                observation_callback=lambda o: progress.observe(0, "fenced verification", o),
            )
    except VerificationFailure:
        assert not succeeds
    output = stream.getvalue()
    assert output.count("\n") == 1
    assert "member 0 peer check was unknown" in output
    assert "previous observation: peer-heartbeat-tls-failure, 28s before gate ended" in output
    assert "private" not in output
    assert (
        "verification total 30s; full verification passed" in output
        if succeeds
        else "verification total 330s" in output
    )
    assert output.startswith("✓" if succeeds else "✗")
    assert "still required" not in output


def test_progress_tracks_member_windows_and_total_without_resetting_deadlines(monkeypatch, clock):
    events = []
    progress = cli._VMHAActivationProgress(events.append)
    script(monkeypatch, [STALE])
    for member in (0, 1):
        result = cli._wait_for_vm_ha_peer_channel(
            observation_callback=lambda o, index=member: progress.observe(index, "peer check", o)
        )
        progress.peer_result(member, result)
    starts = [e for e in events if e.wait.observation.elapsed_seconds == 0]
    assert [
        (e.wait.member_index, e.elapsed_seconds, e.wait.observation.remaining_seconds)
        for e in starts
    ] == [
        (0, 0.0, 30.0),
        (1, 30.0, 30.0),
    ]
    assert clock[0] == 60.0
    assert events[-1].elapsed_seconds == 60.0
    assert events[-1].wait.observation.remaining_seconds == 0.0
    # At most one regular update per five seconds, plus start/end/warning events.
    assert len(events) <= 18


def test_interactive_wait_details_use_only_safe_member_and_condition(clock):
    from tests.unit.test_vm_ha_command import _FakeStatus, _TTYBuffer

    stream, updates = _TTYBuffer(), []
    reporter = cli._VMHAProgressReporter(
        stream, status_factory=lambda _s, label: _FakeStatus(updates, label)
    )
    progress = cli._VMHAActivationProgress(reporter)
    with cli._vm_ha_progress_step(progress, cli._VMHAProgressPhase.VERIFY_FENCED):
        clock[0] = 22.0
        progress.observe(
            1,
            "peer check",
            cli._VMHAWaitObservation("status-observation-unavailable", 20.0, 22.0, 8.0),
        )
    assert any(
        "member 1: peer check; status-observation-unavailable; 22s elapsed, 8s remaining in this window; verification total 22s"
        in text
        for _, text in updates
    )
    assert stream.getvalue().count("\n") == 1


def test_unknown_then_failed_peer_never_claims_full_verification_ran(monkeypatch, clock, capsys):
    progress = cli._VMHAActivationProgress(None)
    with (
        pytest.raises(VerificationFailure),
        cli._vm_ha_progress_step(progress, cli._VMHAProgressPhase.VERIFY_FENCED),
    ):
        for member in (0, 1):
            script(monkeypatch, [STALE if member == 0 else failed()])
            result = cli._wait_for_vm_ha_peer_channel(
                observation_callback=lambda o, index=member: progress.observe(
                    index, "peer check", o
                )
            )
            progress.peer_result(member, result)
            if result.outcome is cli._VMHAPeerChannelOutcome.FAILED:
                raise result.failure
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "member 0 peer check was unknown at gate expiry" in captured.err
    assert "member 1: peer check; peer-heartbeat-tls-failure" in captured.err
    assert "full verification passed" not in captured.err
    assert "continued to full verification" not in captured.err
    assert clock[0] == 60.0
