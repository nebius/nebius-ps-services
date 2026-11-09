from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.deploy import package_recovery as recovery
from nebius_vpngw.deploy import package_reservation as reservations
from nebius_vpngw.deploy.activation_recovery import Diagnosis, Recovery
from nebius_vpngw.deploy.ordinary_handoff import REPAIR, PackageUnresolved
from nebius_vpngw.package_worker import digest


def ledger(tmp_path):
    value = Recovery(tmp_path, execution="a" * 32, authority="b" * 64)
    value.initialize()
    return value


def test_prepared_observation_uses_admitted_phase_after_permitted_peer_transition(tmp_path):
    value = ledger(tmp_path)
    value.initial("packages", state="started")
    submitted = {"action": "prepare-package", "plan_digest": "plan", "peer": {"old": True}}
    calls = []

    def control(request):
        assert request["phase"] == digest(submitted)
        return {"state": "PREPARED", "result": {"exact": True}}

    holder = NS(
        job=NS(identity="id", submitted={"prepare-package": submitted}, control=control),
        _peer_request=lambda: {"peer": {"new": True}},
        verify_prepared=lambda: calls.append("fresh-proof"),
    )
    plan = NS(instance=NS(hostname="member"), digest="plan")
    recovery.prepare(
        value,
        effect=lambda: pytest.fail("completed phase replayed"),
        members=lambda: [(None, plan, {}, None)],
        handoffs={"member": holder},
    )
    assert calls == ["fresh-proof"] and value.history() == []


def test_initial_reservation_failure_uses_same_command_allowance(tmp_path):
    value = ledger(tmp_path)
    calls = []

    def start(request):
        calls.append(REPAIR.get())
        if not REPAIR.get():
            raise PackageUnresolved("id")
        return {"ready": True}

    def control(request):
        return {
            "state": "PREPARED" if len(calls) == 2 else "READY_TO_CONTINUE",
            "result": {"ready": True} if len(calls) == 2 else {"generation": 1},
        }

    job = NS(
        identity="id",
        instance=NS(hostname="member"),
        start=start,
        control=control,
        reservation={"identity": "id"},
        bind=lambda _r: None,
    )
    token = reservations.SCOPE.set((tmp_path, value.execution, value.authority))
    try:
        assert recovery.reserve(job, {}) == {"ready": True}
    finally:
        reservations.SCOPE.reset(token)
    assert calls == [False, True]
    assert len(value.history()) == 1 and value.history()[0]["state"] == "verified"


def test_composed_budget_owner_survives_context_loss_and_cannot_reset(tmp_path):
    parent = ledger(tmp_path)
    selected = reservations.recovery_owner(
        tmp_path,
        "c" * 32,
        "d" * 64,
        parent=(tmp_path, parent.execution, parent.authority),
        admit=True,
    )
    assert reservations.recovery_owner(tmp_path, "c" * 32, "d" * 64) == selected
    for number in range(3):
        attempt = parent.reserve("packages", Diagnosis("packages", digest(number)))
        attempt = parent.transition(attempt, "failed")
        parent.cleaned(attempt)
    recovered = Recovery(selected[0], execution=selected[1], authority=selected[2])
    with pytest.raises(RuntimeError, match="three attempts"):
        recovered.reserve("activation", Diagnosis("service", digest("new")))
    with pytest.raises(RuntimeError, match="differs"):
        reservations.recovery_owner(
            tmp_path, "c" * 32, "d" * 64, parent=(tmp_path, "e" * 32, "f" * 64)
        )


@pytest.mark.parametrize("state", ["PREPARED", "NOT_SUBMITTED"])
def test_reserve_reentry_binds_identity_before_observation_without_spending_attempt(
    tmp_path, state
):
    value = ledger(tmp_path)
    value.initial("packages-reserve-member", state="started")
    calls = []
    job = NS(identity=None, request=None, instance=NS(hostname="member"))

    def bind(request):
        job.request = request
        job.identity = "exact-id"
        job.reservation = {"identity": "exact-id", "source": "frozen"}

    def control(request):
        from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_remaining

        assert 0 < vm_ha_observation_remaining() <= 20
        assert request == {"action": "worker-reservation", "reservation": job.reservation}
        assert job.identity == "exact-id"
        return {"state": state, "result": {"ready": True}}

    def start(request):
        from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_remaining

        assert vm_ha_observation_remaining(default=float("inf")) == float("inf")
        assert request == job.request
        calls.append("submit")
        return {"ready": True}

    job.bind, job.control, job.start = bind, control, start
    token = reservations.SCOPE.set((tmp_path, value.execution, value.authority))
    try:
        assert recovery.reserve(job, {"exact": True}) == {"ready": True}
    finally:
        reservations.SCOPE.reset(token)
    assert calls == (["submit"] if state == "NOT_SUBMITTED" else [])
    assert value.history() == []


@pytest.mark.parametrize(
    "initial_states", [("PREPARED", "NOT_SUBMITTED"), ("NOT_SUBMITTED", "NOT_SUBMITTED")]
)
@pytest.mark.parametrize("resuming", [False, True])
def test_pair_continues_verified_unsubmitted_package_with_shared_allowance(
    tmp_path, monkeypatch, initial_states, resuming
):
    from nebius_vpngw.deploy import vm_ha_package

    value = ledger(tmp_path)
    if resuming:
        value.initial("packages", state="started")
    calls = []
    verified = []
    complete = False
    plans = [NS(instance=NS(hostname="member-" + str(i))) for i in range(2)]

    def effect():
        nonlocal complete
        calls.append(REPAIR.get())
        if not REPAIR.get():
            raise RuntimeError("Compute authority read temporarily unavailable")
        complete = True

    def observe(_ssh, plan, _local, _expected):
        index = plans.index(plan)
        return {
            "state": "PREPARED" if complete else initial_states[index],
            "identity": "exact-member-" + str(index),
            "result": {"approved": True}
            if complete or initial_states[index] == "PREPARED"
            else None,
        }

    monkeypatch.setattr(recovery, "observe_guarded", observe)
    monkeypatch.setattr(
        vm_ha_package,
        "verify_prepared",
        lambda _ssh, plan, *_args, **_kwargs: verified.append(plan),
    )
    recovery.prepare(
        value,
        effect=effect,
        members=lambda: [(None, plan, {}, {"operation_id": "c" * 64}) for plan in plans],
    )
    assert calls == ([True] if resuming else [False, True])
    assert all(plan in verified for plan in plans)
    attempts = value.history()
    assert len(attempts) == 1 and attempts[0]["state"] == "verified" and attempts[0]["cleaned"]


def test_unsubmitted_holder_phase_keeps_existing_worker_and_shared_attempt(tmp_path):
    value = ledger(tmp_path)
    value.initial("packages", state="started")
    submitted = {}
    calls = []
    job = NS(identity="same-worker", submitted=submitted)
    plan = NS(instance=NS(hostname="member"), digest="same-package")

    def control(request):
        assert request["identity"] == "same-worker"
        assert request["phase"] == digest(
            {"action": "prepare-package", "plan_digest": "same-package", "peer": "current"}
        )
        return {"state": "PREPARED" if submitted else "NOT_SUBMITTED", "result": {"exact": True}}

    job.control = control
    holder = NS(
        job=job,
        _peer_request=lambda: {"peer": "current"},
        verify_prepared=lambda: calls.append("verify"),
    )

    def effect():
        assert REPAIR.get()
        assert job.identity == "same-worker" and not submitted
        submitted["prepare-package"] = {
            "action": "prepare-package",
            "plan_digest": "same-package",
            "peer": "current",
        }
        calls.append("submit-exact-phase")

    recovery.prepare(
        value, effect=effect, members=lambda: [(None, plan, {}, None)], handoffs={"member": holder}
    )
    assert calls == ["submit-exact-phase", "verify"]
    assert holder.package_receipt == {"exact": True}
    assert len(value.history()) == 1 and value.history()[0]["state"] == "verified"


@pytest.mark.parametrize("other_state", ["BLOCKED", "UNKNOWN"])
def test_unsubmitted_member_does_not_override_unavailable_peer_authority(
    tmp_path, monkeypatch, other_state
):
    value = ledger(tmp_path)
    value.initial("packages", state="started")
    plans = [NS(instance=NS(hostname="member-" + str(i))) for i in range(2)]

    def observe(_ssh, plan, _local, _expected):
        if plan is plans[0]:
            return {"state": "NOT_SUBMITTED", "identity": "exact", "result": None}
        if other_state == "UNKNOWN":
            raise TimeoutError("authority read unavailable")
        return {"state": other_state, "identity": "exact-peer", "result": None}

    monkeypatch.setattr(recovery, "observe_guarded", observe)
    with pytest.raises(
        RuntimeError,
        match="observation pending"
        if other_state == "UNKNOWN"
        else "cannot prove current local state",
    ):
        recovery.prepare(
            value,
            effect=lambda: pytest.fail("unproven pair dispatched"),
            members=lambda: [(None, plan, {}, None) for plan in plans],
        )
    assert value.history() == []


def test_unsubmitted_phase_cannot_replenish_shared_attempts(tmp_path, monkeypatch):
    value = ledger(tmp_path)
    value.initial("packages", state="started")
    plan = NS(instance=NS(hostname="member"))
    calls = []
    monkeypatch.setattr(
        recovery,
        "observe_guarded",
        lambda *_args: {"state": "NOT_SUBMITTED", "identity": "exact", "result": None},
    )

    def effect():
        assert REPAIR.get()
        calls.append("same-approved-request")
        raise RuntimeError("current lock no longer admits submission")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="refuses unchanged repair"):
            recovery.prepare(value, effect=effect, members=lambda: [(None, plan, {}, None)])
    assert calls == ["same-approved-request"]
    assert len(value.history()) == 1
    assert value.history()[0]["state"] == "failed" and value.history()[0]["cleaned"]


def test_authority_outage_resume_continues_original_pair_without_repair(tmp_path, monkeypatch):
    from nebius_vpngw.deploy import vm_ha_package
    from nebius_vpngw.deploy.authority_observation import AuthorityUnavailable

    value = ledger(tmp_path)
    plans = [NS(instance=NS(hostname="member-" + str(i))) for i in range(2)]

    def members():
        return [(None, plan, {}, None) for plan in plans]

    calls = []

    def unavailable():
        calls.append("observation")
        raise AuthorityUnavailable("compute-read-timeout", attempts=3)

    with pytest.raises(AuthorityUnavailable):
        recovery.prepare(value, effect=unavailable, members=members)
    assert value.history() == []
    # Reopen the actual protected ledger, as a later public command does.
    value = Recovery(tmp_path, execution=value.execution, authority=value.authority)
    assert value.initial("packages") == "waiting-authority"
    monkeypatch.setattr(
        recovery,
        "observe_guarded",
        lambda _ssh, plan, *_: {
            "state": "PREPARED" if plan is plans[0] else "NOT_SUBMITTED",
            "identity": plan.instance.hostname,
            "result": {"exact": True},
        },
    )
    verified = []
    monkeypatch.setattr(
        vm_ha_package, "verify_prepared", lambda _ssh, plan, *a, **kw: verified.append(plan)
    )

    def continue_original():
        assert not REPAIR.get()
        assert value.initial("packages") == "started"
        calls.append("continue-original")

    recovery.prepare(value, effect=continue_original, members=members)
    assert calls == ["observation", "continue-original"]
    assert verified == [plans[0]] and value.history() == []
    assert value.initial("packages") == "verified"


def test_partial_pair_resumes_real_worker_receipt_without_reinstall(tmp_path, monkeypatch):
    """Compose read retries, durable recovery and the actual worker replay gate."""
    import subprocess
    import tempfile

    from nebius_vpngw import package_worker as worker
    from nebius_vpngw.deploy import ordinary_handoff as handoff
    from nebius_vpngw.deploy import vm_ha_package
    from nebius_vpngw.deploy.authority_observation import AuthorityUnavailable, read_compute

    root = tmp_path.resolve()
    boot = root / "boot"
    boot.write_text("boot-a")
    monkeypatch.setattr(worker, "ROOT", root / "workers")
    monkeypatch.setattr(worker, "RUN", root / "run")
    monkeypatch.setattr(worker, "BOOT", boot)
    plans = [
        NS(
            instance=NS(hostname=name),
            target=name,
            envelope={"desired": {"member": name}},
            manifest={"wheel_sha256": "c" * 64},
        )
        for name in ("member-0", "member-1")
    ]
    installed, reads = [], []
    unavailable = True

    def authority(name, policy):
        assert policy["retries"] == 1 and 0 < policy["timeout"] <= 10
        reads.append(name)
        if name == "member-1" and unavailable:
            raise TimeoutError("transient read loss")
        return {"identity": name}

    def control(job, request):
        assert read_compute(lambda policy: authority(job.target, policy), sleep=lambda _: None) == {
            "identity": job.target
        }
        return worker.control(request)

    def native_launch(arguments, **_kwargs):
        assert arguments[0] == "systemd-run"
        key = arguments[-3]
        transport = worker.Transport(key)
        try:
            request = transport.receive()
            installed.append(request["package"]["desired"]["member"])
            transport.reply(
                {"schema": "nebius-vpngw/vm-ha-package-v1", "artifact_sha256": "c" * 64}
            )
            transport.close(False)
        finally:
            transport.listener.close()
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(handoff.PackageJob, "control", control)
    monkeypatch.setattr(worker.subprocess, "run", native_launch)
    verified = []
    monkeypatch.setattr(
        vm_ha_package, "verify_prepared", lambda _ssh, plan, *a, **kw: verified.append(plan.target)
    )
    value = ledger(root / "recovery")

    def effect():
        assert not REPAIR.get()
        for plan in plans:
            handoff.prepare_guarded(None, plan, {}, None)

    def members():
        return [(None, plan, {}, None) for plan in plans]

    # Keep Unix sockets within macOS's short path limit; receipts remain real.
    with tempfile.TemporaryDirectory(prefix="vpngw-pair-", dir="/tmp") as sockets:
        monkeypatch.setattr(worker, "address", lambda key: sockets + "/" + key[:12])
        with pytest.raises(AuthorityUnavailable) as caught:
            recovery.prepare(value, effect=effect, members=members)
        assert caught.value.attempts == 3 and reads.count("member-1") == 3
        assert installed == ["member-0"] and value.history() == []
        key = worker.identity(
            {"action": "prepare-guarded", "package": plans[0].envelope, "expected": None}
        )
        receipt = worker.ROOT / key / "transport.json"
        original_receipt = receipt.read_bytes()
        execution, authority_binding = value.execution, value.authority
        value = Recovery(root / "recovery", execution=execution, authority=authority_binding)
        assert value.initial("packages") == "waiting-authority"
        unavailable = False
        recovery.prepare(value, effect=effect, members=members)
        assert installed == ["member-0", "member-1"]
        assert receipt.read_bytes() == original_receipt
        assert verified == ["member-0"] and value.history() == []
        assert value.initial("packages") == "verified" and value.execution == execution
