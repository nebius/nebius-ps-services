from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_passive_checks as module
from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_passive_checks import PassiveDiagnostics, PassivePending


@pytest.fixture
def passive(tmp_path):
    entry = {"name": "health", "command": "./boot_disk_full.sh", "contexts": ["any"]}
    policy = {
        "supported": True,
        "scheduler": {"HealthCheckInterval": "1"},
        "entries": {"boot_disk_full.sh": entry},
        "diagnostics": ["boot_disk_full.sh"],
        "proofRoles": {"boot_disk_full.sh": "required-measurement"},
        "scripts": {"check_runner.py": "native", "boot_disk_full.sh": "native"},
    }
    cm = {
        "metadata": {"uid": "cm"},
        "data": {"checks.json": json.dumps([entry]), **policy["scripts"]},
    }
    calls = {"saves": 0, "observations": [], "patches": [], "messages": []}

    def save():
        calls["saves"] += 1

    checks = SimpleNamespace(
        policy=SimpleNamespace(passive=policy),
        state={"operation": "op", "reservation": "reserve"},
        operation_id="op",
        path=tmp_path / "checks.json",
        authority=lambda: None,
        emit=calls["messages"].append,
        _save=save,
        _node_inventory=lambda: {"gpu-0": {"gpus": 8}},
        _verify_isolation=lambda: None,
        _reservation=lambda name: {"users": ["root"]},
        slurm=lambda command: "HealthCheckInterval = 1",
        _get=lambda *args: copy.deepcopy(cm),
    )

    def patch(kind, name, namespace, data, *, uid):
        assert uid == cm["metadata"]["uid"]
        calls["patches"].append(copy.deepcopy(data))
        cm["data"].update(data["data"])

    checks._patch = patch
    instance = PassiveDiagnostics(checks)

    def observe(worker, expected, mode):
        calls["observations"].append((worker, mode))
        return {
            "hashes": copy.deepcopy(expected["hashes"]),
            "config": copy.deepcopy(expected["config"]),
            "worker": worker,
            "identity": {"uid": worker},
            "boot": "boot",
            "running": [],
            "observedAt": 1,
            "suppressed": mode == "paused",
            "verdicts": [{"status": "PASS"}],
            "log": {"sha256": "fresh"},
        }

    instance._observe = observe
    return instance, checks, cm, calls


def test_supporting_limitations_are_visible_once_across_workers_and_resume(passive):
    instance, checks, _, calls = passive
    original = instance._observe

    def observe(worker, expected, mode):
        result = original(worker, expected, mode)
        result["verdicts"].append(
            {
                "script": "nvme_raid_health.sh",
                "proofRole": "supporting-only",
                "status": "NATIVE_SKIP",
                "limitation": "native discovery success is not reported",
            }
        )
        return result

    instance._observe = observe
    checks._node_inventory = lambda: {"gpu-0": {"gpus": 8}, "gpu-1": {"gpus": 8}}
    instance.begin_acceptance()
    instance.verify(paused=False, fresh=True)
    resumed = PassiveDiagnostics(checks)
    resumed._observe = observe
    resumed.verify(paused=False, fresh=True)
    assert len(calls["messages"]) == 1
    assert "not a measured PASS" in calls["messages"][0]
    assert "fresh active acceptance remain mandatory" in calls["messages"][0]
    assert instance.state["supportingLimitations"] == {
        "nvme_raid_health.sh": "native discovery success is not reported"
    }


def test_partial_source_suppression_is_restored_before_enabled_fallback(passive):
    instance, checks, cm, calls = passive
    original = instance._observe

    def observe(worker, expected, mode):
        if mode == "paused":
            raise RuntimeError("one worker still uses old config")
        return original(worker, expected, mode)

    instance._observe = observe
    before = copy.deepcopy(cm["data"])
    result = instance.pause_source()
    assert len(calls["patches"]) == 2
    assert cm["data"] == before
    assert result["status"] == "enabled-fallback"
    assert result["sourceIntent"]["restored"] is True


def test_failed_fallback_restoration_cannot_claim_enabled(passive):
    instance, _, _, _ = passive
    instance._observe = lambda *args: (_ for _ in ()).throw(RuntimeError("transport unavailable"))
    with pytest.raises(RuntimeError):
        instance.pause_source()
    assert instance.state.get("status") != "enabled-fallback"
    assert instance.state["fallbackIntent"]
    assert not instance.state["sourceIntent"].get("restored")


@pytest.mark.parametrize("change", ["uid", "config"])
def test_source_resume_does_not_overwrite_foreign_configuration(passive, change):
    instance, _, cm, calls = passive
    instance.pause_source()
    if change == "uid":
        cm["metadata"]["uid"] = "replaced"
    else:
        cm["data"]["checks.json"] = "[]"
    writes = copy.deepcopy(calls["patches"])
    with pytest.raises(RuntimeError, match="changed|unknown"):
        instance.pause_source()
    assert calls["patches"] == writes


def test_unsupported_target_requires_its_frozen_opaque_contract(passive):
    instance, checks, cm, _ = passive
    checks.policy.passive = {"supported": False, "opaque": copy.deepcopy(cm["data"])}
    cm["data"]["check_runner.py"] = "old source or unrelated edit"
    with pytest.raises(RuntimeError, match="frozen desired"):
        instance.verify(paused=False)
    assert instance.state.get("status") != "enabled-fallback"


def test_scheduler_drift_is_not_passive_restoration(passive):
    instance, checks, _, _ = passive
    checks.slurm = lambda _: "HealthCheckInterval = 0"
    with pytest.raises(RuntimeError, match="scheduler"):
        instance.verify(paused=False, fresh=True)


def test_partial_acceptance_reuses_only_same_worker_execution(passive):
    instance, checks, _, calls = passive
    checks._node_inventory = lambda: {"gpu-0": {"gpus": 8}, "gpu-1": {"gpus": 8}}
    instance.begin_acceptance()
    original = instance._observe

    def observe(worker, expected, mode):
        result = original(worker, expected, mode)
        if worker == "gpu-1" and mode == "accept":
            result["pending"] = True
        return result

    instance._observe = observe
    with pytest.raises(PassivePending):
        instance.verify(paused=False, fresh=True)
    instance._observe = original
    instance.verify(paused=False, fresh=True)
    assert calls["observations"].count(("gpu-0", "accept")) == 1
    assert instance.state["acceptance"]["status"] == "accepted"


def test_partial_baselines_survive_alternating_busy_workers_and_resume(passive):
    instance, checks, _, calls = passive
    checks._node_inventory = lambda: {"gpu-0": {"gpus": 8}, "gpu-1": {"gpus": 8}}
    original = instance._observe
    busy = "gpu-1"
    persisted = []
    save = checks._save

    def save_baseline():
        save()
        persisted.append(copy.deepcopy(instance.state.get("baseline", {})))

    checks._save = save_baseline

    def observe(worker, expected, mode):
        result = original(worker, expected, mode)
        if worker == busy:
            result["running"] = ["17"]
        return result

    instance._observe = observe
    with pytest.raises(PassivePending):
        instance.begin_acceptance()
    assert persisted and set(persisted[-1]) == {"gpu-0"}
    assert "acceptance" not in instance.state
    busy = "gpu-0"
    resumed = PassiveDiagnostics(checks)
    resumed._observe = observe
    resumed.begin_acceptance()
    assert set(resumed.state["baseline"]) == {"gpu-0", "gpu-1"}
    assert calls["observations"].count(("gpu-0", "baseline")) == 1


def test_periodic_inapplicability_does_not_skip_native_job_hook_acceptance(passive):
    instance, checks, _, _ = passive
    checks.policy.passive["scheduler"]["HealthCheckNodeState"] = "ALLOC"
    checks.slurm = lambda _: "HealthCheckInterval = 1\nHealthCheckNodeState = ALLOC"
    original = instance._observe

    def observe(worker, expected, mode):
        result = original(worker, expected, mode)
        assert expected["scheduler"]["HealthCheckNodeState"] == "ALLOC"
        if mode == "accept":
            result.update(periodicApplicable=False, verdicts=[])
        elif mode == "job":
            result["pending"] = True
        return result

    instance._observe = observe
    assert instance.accept_ready()
    assert instance.state["acceptance"]["workers"]["gpu-0"]["periodicApplicable"] is False
    checks.state["acceptance"] = {"resources": {"gpu-0": {"gpus": 8}}}
    checks.slurm = lambda _: "JobId=17 JobState=COMPLETED Restarts=0"
    entry = {"slurmIds": ["17"], "slurmResult": {"nodes": ["gpu-0"]}}
    with pytest.raises(PassivePending, match="job hook"):
        instance.collect_job(entry)
    assert not entry.get("passiveEvidence")


def test_replaced_worker_invalidates_first_acceptance_baseline(passive):
    instance, _, _, _ = passive
    instance.begin_acceptance()
    original = instance._observe

    def observe(worker, expected, mode):
        result = original(worker, expected, mode)
        result["identity"] = {"uid": "replacement"}
        result["boot"] = "new-boot"
        return result

    instance._observe = observe
    with pytest.raises(PassivePending):
        instance.verify(paused=False, fresh=True)
    assert instance.state.get("acceptance") is None
    assert instance.state["baseline"]["gpu-0"]["identity"]["uid"] == "replacement"


def test_thousand_workers_use_bounded_transport_and_linear_receipt_writes(passive, monkeypatch):
    instance, checks, _, calls = passive
    inventory = {f"worker-{n}": {"gpus": 8} for n in range(1000)}
    checks._node_inventory = lambda: inventory
    writes = []
    monkeypatch.setattr(
        module,
        "write_owner_only_json",
        lambda path, data: writes.append((path, len(json.dumps(data)))),
    )
    original_pool = module.ThreadPoolExecutor
    widths = []

    def pool(*, max_workers):
        widths.append(max_workers)
        return original_pool(max_workers=max_workers)

    monkeypatch.setattr(module, "ThreadPoolExecutor", pool)
    instance.begin_acceptance()
    instance.verify(paused=False, fresh=True)
    assert widths == [16, 16]
    assert len(writes) == 1000 and len({row[0] for row in writes}) == 1000
    assert calls["saves"] <= 3
    assert sum(size for _, size in writes) < 1_000_000
    assert len(instance.state["acceptance"]["workers"]) == 1000
    assert instance.state["acceptance"]["policy"] == checks_digest(instance._expected(paused=False))


@pytest.mark.parametrize("interrupt_sidecar", [False, True])
def test_replaced_accepted_worker_keeps_one_fresh_boundary(passive, interrupt_sidecar):
    instance, _, _, calls = passive
    instance.begin_acceptance()
    instance.verify(paused=False, fresh=True)
    original = instance._observe
    pending = True

    def observe(worker, expected, mode):
        result = original(worker, expected, mode)
        result.update(identity={"uid": "replacement"}, boot="replacement-boot", observedAt=2)
        if mode == "accept":
            result["pending"] = pending
            assert expected["baseline"]["observedAt"] == 2
        return result

    instance._observe = observe
    receipt = instance._worker_receipt
    if interrupt_sidecar:

        def interrupted(*args, **kwargs):
            if kwargs.get("invalidate"):
                raise KeyboardInterrupt
            return receipt(*args, **kwargs)

        instance._worker_receipt = interrupted
    with pytest.raises(KeyboardInterrupt if interrupt_sidecar else PassivePending):
        instance.verify(paused=False, fresh=True)
    instance._worker_receipt = receipt
    boundary = copy.deepcopy(instance.state["baseline"])
    for _ in range(2):
        with pytest.raises(PassivePending):
            instance.verify(paused=False, fresh=True)
        assert instance.state["baseline"] == boundary
        assert "gpu-0" not in instance.state["acceptance"]["workers"]
    pending = False
    instance.verify(paused=False, fresh=True)
    assert instance.state["acceptance"]["workers"]["gpu-0"]["identity"] == {"uid": "replacement"}
    assert calls["observations"].count(("gpu-0", "baseline")) == 2


def test_failed_source_fallback_resumes_restoration_without_reapplying_pause(passive):
    instance, _, cm, calls = passive
    original = instance._observe
    before = copy.deepcopy(cm["data"])
    instance._observe = lambda *args: (_ for _ in ()).throw(RuntimeError("transport unavailable"))
    with pytest.raises(RuntimeError):
        instance.pause_source()
    writes = len(calls["patches"])
    instance._observe = original
    instance.pause_source()
    assert instance.state["status"] == "enabled-fallback"
    assert instance.state["sourceIntent"]["restored"]
    assert cm["data"] == before
    assert all(
        json.loads(row["data"]["checks.json"]) == json.loads(before["checks.json"])
        for row in calls["patches"][writes:]
    )
