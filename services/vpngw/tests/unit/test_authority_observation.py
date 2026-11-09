"""Authority reads recover without granting guest effects or repair allowances."""

from types import SimpleNamespace

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw.deploy.authority_observation import AuthorityUnavailable, read_compute
from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_budget


@pytest.mark.parametrize("boundary", ["connection", "request", "response", "completion"])
def test_package_timeout_reports_its_observed_boundary_without_replay(monkeypatch, boundary):
    from contextlib import contextmanager

    from nebius_vpngw.deploy import ordinary_handoff as handoff
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

    calls = []

    def fail_at(phase):
        calls.append(phase)
        if phase == boundary:
            raise TimeoutError("private connection or request data")

    @contextmanager
    def connection(*_):
        fail_at("connection")
        yield object()
        fail_at("completion")

    def start(*_):
        fail_at("request")
        return None, object()

    def receive(_):
        fail_at("response")
        return {"state": "NOT_SUBMITTED"}

    monkeypatch.setattr(handoff, "_connection", connection)
    monkeypatch.setattr(handoff, "_start", start)
    monkeypatch.setattr(handoff, "_receive", receive)
    with pytest.raises(ObservationUnavailable) as caught:
        handoff.PackageJob(None, "target", None, {}).control({"action": "worker-find"})
    assert caught.value.first.phase == "package-" + boundary
    assert caught.value.retryable and len(calls) == len(set(calls))
    assert "private" not in str(caught.value.first.mapping())


@pytest.mark.parametrize("retryable", [True, False])
def test_typed_package_observation_survives_cli_wrapper(monkeypatch, retryable):
    from contextlib import contextmanager

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import ordinary_handoff as handoff

    failure = AuthorityUnavailable("compute-read-timeout" if retryable else "compute-read-denied")

    @contextmanager
    def connection(*_):
        raise failure
        yield  # pragma: no cover

    monkeypatch.setattr(handoff, "_connection", connection)
    with pytest.raises(AuthorityUnavailable) as caught:
        handoff.PackageJob(None, "target", None, {}).control({"action": "worker-find"})
    assert caught.value is failure
    wrapper = RuntimeError("private endpoint")
    wrapper.__cause__ = failure
    reason = cli._vm_ha_package_failure_reason(wrapper)
    assert ("pending" if retryable else "blocked") in reason
    assert (
        "verify-ssh-authority" in reason and "unexpected" not in reason and "private" not in reason
    )


def test_transient_reads_recover_with_one_diminishing_budget():
    now = [0.0]
    calls, sleeps = [], []
    exact = object()

    def read(options):
        calls.append(options)
        now[0] += 3
        if len(calls) < 3:
            raise TimeoutError("private diagnostic")
        return exact

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    assert read_compute(read, clock=lambda: now[0], sleep=sleep, jitter=lambda: 1) is exact
    assert len(calls) == 3 and sleeps == [0.2, 0.4]
    assert all(item["retries"] == 1 for item in calls)
    assert all(item["timeout"] <= 10 and item["auth_timeout"] <= 10 for item in calls)


@pytest.mark.parametrize(
    "code",
    [
        StatusCode.PERMISSION_DENIED,
        StatusCode.UNAUTHENTICATED,
        StatusCode.NOT_FOUND,
        StatusCode.INTERNAL,
    ],
)
def test_nontransient_failures_never_retry_or_expose_remote_text(code):
    calls = []

    def read(_):
        calls.append(1)
        raise RequestError(SimpleNamespace(code=code))

    with pytest.raises(AuthorityUnavailable) as caught:
        read_compute(read)
    assert not caught.value.retryable and len(calls) == 1


def test_empty_response_preserves_uncertainty_without_retry():
    calls = []
    with pytest.raises(AuthorityUnavailable) as caught:
        read_compute(lambda _: calls.append(1))
    assert not caught.value.retryable and len(calls) == 1
    assert caught.value.first.reasons == ("compute-response-empty",)


def test_nested_budget_covers_delays_and_rejects_late_positive_evidence():
    now = [10.0]
    calls = []

    def read(options):
        calls.append(options)
        now[0] += 4
        return object()

    with (
        pytest.raises(AuthorityUnavailable) as caught,
        vm_ha_observation_budget(seconds=5, clock=lambda: now[0]),
    ):
        now[0] += 2  # dispatch/authentication already spent this time
        read_compute(read, clock=lambda: now[0])
    assert caught.value.retryable
    assert len(calls) == 1 and calls[0]["auth_timeout"] <= 3


def test_transient_exhaustion_is_pending_without_more_than_three_reads():
    now = [0.0]
    calls = []

    def read(options):
        calls.append(options)
        raise TimeoutError("private endpoint or token")

    with pytest.raises(AuthorityUnavailable) as caught:
        read_compute(
            read,
            clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            jitter=lambda: 1,
        )
    assert caught.value.retryable and caught.value.attempts == len(calls) == 3
    assert "private" not in str(caught.value)


def test_unavailable_initial_or_recovery_read_never_reserves_repair(tmp_path):
    from nebius_vpngw.deploy.activation_recovery import Recovery, execute

    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    owner.initialize()

    def unavailable(_):
        raise AuthorityUnavailable("compute-read-timeout", attempts=3)

    for _ in range(2):
        with pytest.raises(AuthorityUnavailable):
            execute(
                owner,
                "packages",
                effect=unavailable,
                observe=unavailable,
                repair=lambda *_: pytest.fail("read failure selected repair"),
                finish=lambda _: pytest.fail("read failure settled an effect"),
            )
    assert owner.history() == [] and owner.initial("packages") == "waiting-authority"


@pytest.mark.parametrize(
    "error, expected",
    [
        (TimeoutError("private"), "unknown-retryable"),
        (RuntimeError("foreign lock"), "blocked"),
        (PermissionError("private"), "blocked"),
    ],
)
def test_completion_loss_is_not_incomplete(error, expected):
    from nebius_vpngw import cli
    from nebius_vpngw.deploy.activation_driver import Completion, completed
    from nebius_vpngw.deploy.apply_diagnostics import DiagnosticFailure

    def fetch():
        raise error

    result = cli._vm_ha_completion_probe(fetch, lambda _: True)
    assert result.value == expected and result is not Completion.INCOMPLETE
    with pytest.raises(DiagnosticFailure):
        completed(result)
    with pytest.raises(TypeError):
        bool(result)


@pytest.mark.parametrize(
    "payload,predicate", [({"pending_operation_id": "owned-operation"}, True), ({}, False)]
)
def test_unproven_readiness_is_unknown_not_a_local_fault(payload, predicate):
    from nebius_vpngw import cli
    from nebius_vpngw.deploy.activation_driver import Completion

    assert cli._vm_ha_completion_probe(lambda: payload, lambda _: predicate) is Completion.UNKNOWN


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, "incomplete-with-evidence"),
        ({"apply_locked": False}, "unknown-retryable"),
        ({"apply_operation_id": "foreign"}, "unknown-retryable"),
        ({"observed_owner_node_id": "foreign"}, "unknown-retryable"),
        ({"data_plane_mode": "originating"}, "unknown-retryable"),
        ({"guard_boot_id": "old-boot"}, "unknown-retryable"),
        ({"apply_convergence": None}, "unknown-retryable"),
        ({"apply_convergence": {"fresh": False}}, "unknown-retryable"),
        ({"pending_operation_id": "owned-operation"}, "unknown-retryable"),
    ],
)
def test_only_exact_fenced_own_lock_is_positive_unfinished_phase(change, expected):
    from nebius_vpngw import cli

    payload = {
        "apply_locked": True,
        "apply_operation_id": "operation",
        "observed_owner_node_id": "node",
        "data_plane_mode": "passive",
        "guard_boot_id": "boot",
        "apply_convergence": {"fresh": True, "data_plane_mode": "passive", "boot_id": "boot"},
        **change,
    }
    result = cli._vm_ha_completion_probe(
        lambda: payload,
        lambda _: False,
        incomplete=lambda value: cli._vm_ha_fenced_owner_incomplete(
            value, owner="node", operation="operation"
        ),
    )
    assert result.value == expected


def test_pending_report_keeps_original_and_current_failure_separate(tmp_path):
    from tests.unit.test_apply_result import plan

    from nebius_vpngw.deploy.apply_command import CommandJournal
    from nebius_vpngw.deploy.apply_diagnostics import DiagnosticFailure, Failure
    from nebius_vpngw.deploy.apply_result import ApplyReport

    journal = CommandJournal("project", "gateway", root=tmp_path / "journal")
    with journal:
        journal.begin(plan())
    first = AuthorityUnavailable("compute-read-timeout", attempts=3)
    report = ApplyReport(journal=journal, started=True)
    report.failed(first)
    assert report.mapping()["outcome"] == "pending"
    assert report.mapping()["pending_reason"] == "waiting-for-authority"
    assert journal.pending() is not None
    later = DiagnosticFailure("activation recovery refuses unchanged repair")
    later.first = Failure("activation-recovery", "unchanged-repair-refused")
    resumed = ApplyReport(started=True)
    resumed.bind(journal, resumed=True)
    resumed.failed(later)
    assert resumed.mapping()["failure"] == first.first.mapping()
    assert resumed.mapping()["latest_failure"] == later.first.mapping()
    assert "pending_reason" not in resumed.mapping()
    assert journal.failure_for(journal.pending()["execution_id"], latest=True) == later.first


def test_unavailable_identity_proof_never_selects_configuration_repair():
    from nebius_vpngw import cli
    from nebius_vpngw.deploy.activation_driver import Completion

    for reason in (
        "runtime-identity-unavailable",
        "runtime-identity-generation-stale",
        "standby-restoration-capability-unavailable",
    ):

        def fetch(reason=reason):
            raise cli._VMHAAgentStatusStale("private", predicate=reason)

        assert cli._vm_ha_completion_probe(fetch, lambda _: True) is Completion.UNKNOWN


def test_completed_effect_after_lost_observation_clears_wait_without_replay(tmp_path):
    from nebius_vpngw.deploy.activation_recovery import Recovery, execute

    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    owner.initialize()
    owner.initial("activate", state="started")
    owner.initial("activate", state="waiting-authority")
    execute(
        owner,
        "activate",
        effect=lambda _: pytest.fail("completed effect replayed"),
        observe=lambda _: {"complete": True, "settled": True},
        repair=lambda *_: pytest.fail("completed effect repaired"),
        finish=lambda _: pytest.fail("nonexistent repair settled"),
    )
    assert owner.initial("activate") == "verified" and owner.history() == []


def test_post_repair_observation_uses_fresh_bounded_budget(tmp_path):
    from nebius_vpngw.deploy.activation_recovery import Diagnosis, Recovery, execute
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable
    from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_remaining

    now = [0.0]
    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64, clock=lambda: now[0])
    owner.initialize()
    effects, observations = [], []

    def effect(_):
        effects.append(1)
        assert vm_ha_observation_remaining(default=float("inf")) == float("inf")
        if len(effects) == 1:
            raise RuntimeError("proven local failure")
        now[0] += 121  # Accepted work is not cancelled at the soft target.

    def observe(_):
        observations.append(vm_ha_observation_remaining())
        if len(observations) == 2:
            now[0] += 21
            return {"complete": True, "settled": True}
        return {"complete": False, "settled": True, "diagnosis": Diagnosis("service", "c" * 64)}

    with pytest.raises(ObservationUnavailable):
        execute(
            owner,
            "activate",
            effect=effect,
            observe=observe,
            repair=lambda *_: None,
            finish=lambda _: None,
        )
    assert effects == [1, 1] and observations == [20, 20]
    assert len(owner.history()) == 1 and owner.history()[0]["state"] == "uncertain"


def test_package_ingress_preflight_is_inside_connection_deadline(monkeypatch):
    import threading
    import time
    from unittest.mock import Mock

    from nebius_vpngw.deploy import ordinary_handoff as handoff
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable
    from nebius_vpngw.deploy.ssh_commands import budget

    closed = threading.Event()
    client = Mock(close=closed.set)
    ssh = Mock()
    ssh._ensure_paramiko.return_value.SSHClient.return_value = client
    monkeypatch.setattr(handoff, "configure_paramiko_host_verification", lambda *a, **kw: None)
    monkeypatch.setattr(handoff.ingress_ssh, "preflight", lambda *_: closed.wait(timeout=2))
    start = Mock(side_effect=AssertionError("request admitted after deadline"))
    monkeypatch.setattr(handoff, "_start", start)
    job = handoff.PackageJob(ssh, "target", SimpleNamespace(hostname="member", config_yaml=""), {})
    with pytest.raises(ObservationUnavailable), budget(time.monotonic() + 0.05):
        job.control({"action": "worker-status"})
    assert closed.is_set()
    start.assert_not_called()


@pytest.mark.parametrize("failure", [TimeoutError(), PermissionError()])
def test_host_observation_failure_never_spends_repair(monkeypatch, tmp_path, failure):
    from unittest.mock import Mock

    import paramiko

    from nebius_vpngw.deploy import ssh_push
    from nebius_vpngw.deploy.activation_recovery import Diagnosis, Recovery, execute
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

    ssh = ssh_push.SSHPush(ssh_policy=Mock())
    monkeypatch.setattr(ssh, "_connect_client", Mock(side_effect=failure))
    monkeypatch.setattr(ssh_push, "configure_paramiko_host_verification", lambda *a, **kw: None)
    monkeypatch.setattr(paramiko, "SSHClient", Mock())
    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    owner.initialize()
    with pytest.raises(ObservationUnavailable):
        execute(
            owner,
            "packages",
            effect=lambda _: ssh._inspect_vm_ha_host("target", "member", {}, script="print('{}')"),
            observe=lambda _: {
                "complete": False,
                "settled": True,
                "diagnosis": Diagnosis("packages", "c" * 64),
            },
            repair=lambda *_: None,
            finish=lambda _: None,
        )
    assert owner.history() == []


def test_malformed_package_observation_blocks_without_repair(monkeypatch, tmp_path):
    import io
    from contextlib import nullcontext

    from nebius_vpngw.deploy import ordinary_handoff as handoff
    from nebius_vpngw.deploy.activation_recovery import Diagnosis, Recovery, execute
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

    monkeypatch.setattr(handoff, "_connection", lambda *a: nullcontext(object()))
    monkeypatch.setattr(handoff, "_start", lambda *a: (None, io.StringIO("[]\n")))
    job = handoff.PackageJob(None, "target", SimpleNamespace(hostname="member"), {})
    owner = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    owner.initialize()
    with pytest.raises(ObservationUnavailable) as caught:
        execute(
            owner,
            "packages",
            effect=lambda _: job.control({"action": "worker-status"}),
            observe=lambda _: {
                "complete": False,
                "settled": True,
                "diagnosis": Diagnosis("packages", "c" * 64),
            },
            repair=lambda *_: None,
            finish=lambda _: None,
        )
    assert not caught.value.retryable and owner.history() == []
