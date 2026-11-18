import fcntl
import hashlib
import os
from types import SimpleNamespace

import pytest

from nebius_vpngw import package_worker as worker
from nebius_vpngw.deploy import ordinary_apply, repair_apply
from nebius_vpngw.deploy import package_reservation as reservations


def test_confirmed_repair_retires_idle_handoff_before_waiting_again(monkeypatch):
    """An idle owner cannot finish while its failed controller never submits activation."""
    owner = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    owner.admission = SimpleNamespace(intent={})
    monkeypatch.setattr(repair_apply.Coordinator, "require_release", lambda *_: None)
    retired = []

    def retire(_self, name):
        retired.append(name)
        return True

    monkeypatch.setattr(repair_apply.Coordinator, "retire_idle_handoffs", retire, raising=False)

    def remote(name, action, **_payload):
        assert name == "node-a" and action == "admit"
        if not retired:
            raise ordinary_apply.RemoteGuestWriterBusy("idle admitted handoff")
        return {}

    owner.remote = remote
    moments = iter([0, 1])
    monkeypatch.setattr(repair_apply.time, "monotonic", lambda: next(moments))
    owner.wait_access("node-a")
    assert retired == ["node-a"]


def test_repeated_retirement_receipt_does_not_extend_writer_deadline(monkeypatch):
    owner = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    owner.admission = SimpleNamespace(intent={})
    monkeypatch.setattr(repair_apply.Coordinator, "require_release", lambda *_: None)
    owner.remote = lambda *_a, **_k: (_ for _ in ()).throw(
        ordinary_apply.RemoteGuestWriterBusy("a different writer")
    )
    monkeypatch.setattr(repair_apply.Coordinator, "retire_idle_handoffs", lambda *_: True)
    moments = iter([0, 1, 2, 21])
    sleeps = []
    monkeypatch.setattr(repair_apply.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(repair_apply.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="waiting for the existing guest"):
        owner.wait_access("node-a", writer_timeout=20)
    assert sleeps == [2]


@pytest.fixture
def idle_handoff(monkeypatch, tmp_path):
    root = tmp_path.resolve() / "workers"
    boot = tmp_path / "boot"
    boot.write_text("boot-a")
    monkeypatch.setattr(worker, "ROOT", root)
    monkeypatch.setattr(worker, "BOOT", boot)
    monkeypatch.setattr(worker, "BACKEND", "handoff")
    request = {"action": "reserve", "package": {"host": "node-a"}, "approval": "a" * 64}
    key = worker.identity(request)
    worker.directory(root)
    worker.directory(root / key)
    path = root / key
    source = b"original immutable bootstrap"
    (path / "bootstrap.py").write_bytes(source)
    (path / "bootstrap.py").chmod(0o600)
    for filename in ("submission.lock", "instance.lock"):
        (path / filename).touch(mode=0o600)
    worker.publish(path / "request.json", request)
    value = {
        "schema": worker.SCHEMA, "identity": key, "boot": "boot-a",
        "source": hashlib.sha256(source).hexdigest(), "generation": 1,
        "unit": worker.unit(key, 1), "terminal": None, "waiting": False,
        "requests": {worker.digest(request): {
            "request": request, "state": "complete", "result": {"reserved": True},
        }},
    }
    worker.publish(path / "transport.json", value)
    expected = {"identity": key, "source": value["source"], "request": request}
    runtime = {"running": True, "frozen": False, "signals": [], "controls": []}
    owner = {"pid": 12345, "start": "789", "group": "/exact/owned/cgroup"}
    monkeypatch.setattr(worker, "unit_running", lambda _: runtime["running"])
    monkeypatch.setattr(worker, "_retirement_owner", lambda *_a, **_k: owner.copy())
    monkeypatch.setattr(worker, "_retirement_frozen", lambda _: runtime["frozen"])
    monkeypatch.setattr(worker.os, "pidfd_open", lambda _: os.open(boot, os.O_RDONLY), raising=False)

    def control(unit, action):
        assert unit == value["unit"]
        runtime["controls"].append(action)
        runtime["frozen"] = action == "freeze"

    def kill(fd, signal):
        assert runtime["frozen"]
        os.fstat(fd)
        runtime["signals"].append(signal)
        runtime["running"] = False

    monkeypatch.setattr(worker, "_retirement_control", control)
    monkeypatch.setattr(worker.signal, "pidfd_send_signal", kill, raising=False)
    return SimpleNamespace(path=path, value=value, expected=expected, runtime=runtime,
                           owner=owner, operation="b" * 32, control=control)


def test_repair_retires_idle_owner_without_completing_old_deployment(idle_handoff):
    case = idle_handoff
    original = (case.path / "transport.json").read_bytes()
    result = worker.retire_idle_for_repair(case.expected, case.operation)
    assert result["state"] == "RETIRED"
    assert result["record"] == worker.digest(case.value)
    assert (case.path / "transport.json").read_bytes() == original
    assert case.runtime["signals"] == [worker.signal.SIGKILL]
    assert case.runtime["controls"] == ["freeze"]
    archive = case.path / "history" / (
        "repair-" + case.operation + "-" + worker.digest(case.value) + ".json"
    )
    assert worker.read(archive) == case.value
    assert worker.read(case.path / "retirements" / (case.operation + ".json"))["phase"] == "stopped"
    # A lost controller reply reconciles the same receipt without another signal.
    assert worker.retire_idle_for_repair(case.expected, case.operation) == result
    assert len(case.runtime["signals"]) == 1


@pytest.mark.parametrize("change", ["accepted", "failed", "waiting", "no-requests", "terminal"])
def test_active_or_uncertain_package_work_is_never_interrupted(idle_handoff, change):
    case = idle_handoff
    value = case.value
    if change in {"accepted", "failed"}:
        value["requests"][worker.digest(case.expected["request"])]["state"] = change
    elif change == "waiting":
        value["waiting"] = True
    elif change == "terminal":
        value["terminal"] = "failed"
    else:
        value["requests"] = {}
    worker.publish(case.path / "transport.json", value)
    assert worker.retire_idle_for_repair(case.expected, case.operation)["state"] == "PENDING"
    assert case.runtime["controls"] == case.runtime["signals"] == []
    assert not (case.path / "retirements").exists()


def test_phase_admitted_during_freeze_is_thawed_and_not_terminated(idle_handoff, monkeypatch):
    case = idle_handoff

    def racing_control(unit, action):
        case.control(unit, action)
        if action == "freeze":
            request = {"action": "prepare-package"}
            case.value["requests"][worker.digest(request)] = {
                "request": request, "state": "accepted", "result": None,
            }
            worker.publish(case.path / "transport.json", case.value)

    monkeypatch.setattr(worker, "_retirement_control", racing_control)
    assert worker.retire_idle_for_repair(case.expected, case.operation)["state"] == "PENDING"
    assert case.runtime["controls"] == ["freeze", "thaw"]
    assert case.runtime["signals"] == []


def test_child_appearing_during_freeze_is_thawed_without_termination(idle_handoff, monkeypatch):
    case = idle_handoff

    def owner(_value, *, require_solitary=True):
        if case.runtime["frozen"] and require_solitary:
            raise RuntimeError("Prior handoff still has executing children")
        return case.owner

    monkeypatch.setattr(worker, "_retirement_owner", owner)
    with pytest.raises(RuntimeError, match="executing children"):
        worker.retire_idle_for_repair(case.expected, case.operation)
    assert case.runtime["controls"] == ["freeze", "thaw"]
    assert case.runtime["signals"] == []


def test_failed_signal_restores_only_our_freeze(idle_handoff, monkeypatch):
    case = idle_handoff
    monkeypatch.setattr(worker.signal, "pidfd_send_signal",
                        lambda *_: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError):
        worker.retire_idle_for_repair(case.expected, case.operation)
    assert case.runtime["controls"] == ["freeze", "thaw"]
    assert case.runtime["signals"] == []


def test_foreign_freeze_is_never_thawed_or_terminated(idle_handoff):
    case = idle_handoff
    case.runtime["frozen"] = True
    with pytest.raises(RuntimeError, match="frozen by another"):
        worker.retire_idle_for_repair(case.expected, case.operation)
    assert case.runtime["controls"] == case.runtime["signals"] == []
    assert not (case.path / "retirements").exists()


@pytest.mark.parametrize("field", ["source", "request"])
def test_changed_original_authority_cannot_select_retirement(idle_handoff, field):
    case = idle_handoff
    expected = dict(case.expected, **{field: "changed"})
    with pytest.raises(RuntimeError, match="differs from original authority"):
        worker.retire_idle_for_repair(expected, case.operation)
    assert case.runtime["controls"] == case.runtime["signals"] == []


def test_concurrent_submission_is_left_to_existing_deadline(idle_handoff):
    case = idle_handoff
    with (case.path / "submission.lock").open("rb") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.retire_idle_for_repair(case.expected, case.operation)["state"] == "PENDING"
    assert case.runtime["controls"] == case.runtime["signals"] == []


@pytest.mark.parametrize("directory", ["retirements", "history"])
def test_symlink_evidence_directory_is_rejected(idle_handoff, tmp_path, directory):
    case = idle_handoff
    (case.path / directory).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="storage is unsafe"):
        worker.retire_idle_for_repair(case.expected, case.operation)
    assert case.runtime["controls"] == case.runtime["signals"] == []


@pytest.mark.parametrize("phase", ["intent", "terminating"])
def test_interrupted_retirement_resumes_exact_frozen_owner(idle_handoff, phase):
    case = idle_handoff
    directory = case.path / "retirements"
    worker.directory(directory)
    worker.publish(directory / (case.operation + ".json"), {
        "operation": case.operation, "identity": case.expected["identity"],
        "boot": "boot-a", "generation": 1, "source": case.expected["source"],
        "record": worker.digest(case.value), "owner": case.owner, "phase": phase,
    })
    case.runtime["frozen"] = True
    assert worker.retire_idle_for_repair(case.expected, case.operation)["state"] == "RETIRED"
    assert case.runtime["signals"] == [worker.signal.SIGKILL]


def test_repair_worker_backend_cannot_retire_itself(idle_handoff, monkeypatch):
    case = idle_handoff
    monkeypatch.setattr(worker, "BACKEND", "repair")
    with pytest.raises(RuntimeError, match="identity is invalid"):
        worker.retire_idle_for_repair(case.expected, case.operation)
    assert case.runtime["controls"] == case.runtime["signals"] == []


def test_original_reservations_are_selected_by_exact_host(tmp_path):
    root = tmp_path.resolve()
    admitted = []
    for execution, host in [("a" * 32, "node-a"), ("b" * 32, "node-a"), ("c" * 32, "node-b")]:
        token = reservations.SCOPE.set((root, execution, "d" * 64))
        try:
            admitted.append(reservations.admit(
                {"action": "reserve", "package": {"host": host}, "approval": execution}, "e" * 64
            ))
        finally:
            reservations.SCOPE.reset(token)
    assert reservations.for_host(root, "node-a") == admitted[:2]
    assert reservations.for_host(root, "node-b") == admitted[2:]
    assert reservations.for_host(root, "unknown") == []
    (root / "package-reservations" / ("a" * 32)).chmod(0o777)
    with pytest.raises(RuntimeError, match="directory is unsafe"):
        reservations.for_host(root, "node-a")


@pytest.mark.parametrize("phase,ha", [("confirmed", None), ("staged", {}), ("reset", {})])
def test_idle_retirement_is_only_admitted_for_confirmed_ha(monkeypatch, phase, ha):
    owner = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    owner.operation = "a" * 32
    owner.journal = SimpleNamespace(load=lambda: {"id": owner.operation, "phase": phase})
    owner.admission = SimpleNamespace(plan=SimpleNamespace(vm_ha=ha))
    monkeypatch.setattr(reservations, "for_host", lambda *_: pytest.fail("unadmitted lookup"))
    assert owner.retire_idle_handoffs("node-a") is False


@pytest.mark.parametrize("response", ["valid", "different-operation", "different-identity", "malformed"])
def test_controller_requires_exact_retirement_response(monkeypatch, tmp_path, response):
    from nebius_vpngw.deploy import apply_command

    owner = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    owner.operation = "a" * 32
    owner.journal = SimpleNamespace(load=lambda: {"id": owner.operation, "phase": "confirmed"})
    owner.admission = SimpleNamespace(plan=SimpleNamespace(vm_ha={}),
                                      intent={"project": "test-project", "gateway": "test-gateway"})
    original = {"identity": "b" * 64}
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(reservations, "for_host", lambda *_: [original])
    row = {"identity": original["identity"], "state": "RETIRED", "operation": owner.operation}
    if response == "different-operation":
        row["operation"] = "c" * 32
    elif response == "different-identity":
        row["identity"] = "d" * 64

    def remote(name, action, **payload):
        assert name == "node-a" and action == "retire-idle-handoffs"
        assert payload == {"intent": owner.admission.intent, "reservations": [original]}
        return {"operation": owner.operation, "retirements": [None if response == "malformed" else row]}

    owner.remote = remote
    if response == "valid":
        assert owner.retire_idle_handoffs("node-a") is True
    else:
        with pytest.raises(RuntimeError, match="differs from confirmed scope"):
            owner.retire_idle_handoffs("node-a")
