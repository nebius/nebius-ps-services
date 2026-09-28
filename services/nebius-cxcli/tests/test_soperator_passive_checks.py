from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_passive_checks as module
from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_passive_checks import PassiveDiagnostics, PassivePending
from passive_scheduler_fakes import DESIRED_SCHEDULER, LIVE_SCHEDULER


@pytest.mark.parametrize("changed", [None, "worker", "uid", "container"])
def test_observer_transports_worker_binding_and_preserves_identity_guards(changed):
    pod = {
        "metadata": {"uid": "pod-uid"},
        "spec": {"nodeName": "kube-node"},
        "status": {
            "containerStatuses": [
                {"name": "slurmd", "ready": True, "containerID": "container", "restartCount": 0}
            ]
        },
    }
    expected = {"policy": "fixed"}

    def kube(args, _input):
        assert args[3] == "worker-0"
        assert json.loads(args[-2]) == {**expected, "worker": "worker-0"}
        if changed == "uid":
            pod["metadata"]["uid"] = "replacement"
        if changed == "container":
            pod["status"]["containerStatuses"][0]["restartCount"] = 1
        return {"worker": "worker-1" if changed == "worker" else "worker-0"}

    checks = SimpleNamespace(
        _get=lambda *args: copy.deepcopy(pod), authority=lambda: None, kube=kube
    )
    instance = PassiveDiagnostics(checks)
    if changed:
        with pytest.raises(RuntimeError, match="different worker|changed during observation"):
            instance._observe("worker-0", expected, "observe")
    else:
        assert instance._observe("worker-0", expected, "observe")["worker"] == "worker-0"
    assert expected == {"policy": "fixed"}


@pytest.fixture
def passive(tmp_path):
    entry = {"name": "health", "command": "./boot_disk_full.sh", "contexts": ["any"]}
    policy = {
        "supported": True,
        "scheduler": copy.deepcopy(DESIRED_SCHEDULER),
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
        policy=SimpleNamespace(
            passive=policy,
            readiness=(SimpleNamespace(name="cuda-samples", check_type="slurmJob"),),
            diagnostics={},
        ),
        state={
            "operation": "op",
            "reservation": "reserve",
            "jobs": {
                "smoke": {
                    "check": "cuda-samples",
                    "slurmIds": ["42"],
                    "slurmResult": {"nodes": ["gpu-0"]},
                    "passiveEvidence": {
                        "job": "42",
                        "attempt": "0",
                        "workers": {"gpu-0": {"prolog": {}, "epilog": {}}},
                    },
                }
            },
        },
        operation_id="op",
        path=tmp_path / "checks.json",
        authority=lambda: None,
        emit=calls["messages"].append,
        _save=save,
        _node_inventory=lambda: {"gpu-0": {"gpus": 8}},
        _verify_isolation=lambda: None,
        _reservation=lambda name: {"users": ["root"]},
        slurm=lambda command: LIVE_SCHEDULER,
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


def fast_passive_fixture(passive):
    from nebius_cxcli.soperator_deployment_profile import _coverage

    instance, checks, cm, calls = passive
    checks.policy.diagnostics = _coverage()
    checks.policy.passive.update(
        entries={}, diagnostics=[], proofRoles={}, scripts={"check_runner.py": "native"}
    )
    cm["data"] = {"checks.json": "[]", "check_runner.py": "native"}
    checks.policy.readiness = (SimpleNamespace(name="create-user-nebius", check_type="k8sJob"),)
    checks.state["jobs"] = {"bootstrap": {"check": "create-user-nebius", "status": "complete"}}
    checks.state["phase"] = "accepted"
    checks.state["validation"] = {
        "profile": "readiness",
        "readiness": "passed",
        "extended": "skipped",
    }
    original = instance._observe

    def observe(*args):
        result = original(*args)
        result["verdicts"] = []
        return result

    instance._observe = observe
    instance.begin_acceptance()
    instance.verify(paused=False, fresh=True)
    checks.state["acceptance"] = {"workers": ["gpu-0"]}
    return instance, checks, cm, calls


@pytest.mark.parametrize("sealed", [False, True])
def test_fast_day2_accepts_bootstrap_without_inventing_diagnostic_hook_proof(passive, sealed):
    instance, checks, _, calls = fast_passive_fixture(passive)
    before = copy.deepcopy(checks.state)
    calls["observations"].clear()
    instance.verify_acceptance(sealed=sealed)
    # Existing periodic evidence is observed, never rerun to invent hook proof.
    assert calls["observations"] == [("gpu-0", "observe")]
    assert checks.state["jobs"] == before["jobs"]
    assert checks.state["validation"] == before["validation"]
    assert not any("passiveEvidence" in entry for entry in checks.state["jobs"].values())
    assert checks.state["passive"]["acceptance"]["workers"]["gpu-0"]["verdicts"] == []


def test_standard_still_requires_hooks_on_every_worker_with_empty_required_slurm_jobs(passive):
    instance, checks, _, _ = fast_passive_fixture(passive)
    checks.policy.diagnostics = {}
    with pytest.raises(RuntimeError, match="native hooks on every worker"):
        instance.verify_acceptance(sealed=True)


@pytest.mark.parametrize(
    "damage", ["coverage", "profile", "enabled-diagnostics", "missing-diagnostics", "unsupported"]
)
def test_fast_hook_proof_selection_requires_exact_frozen_coverage(passive, damage):
    instance, checks, _, _ = fast_passive_fixture(passive)
    if damage == "coverage":
        checks.policy.diagnostics["waived"] = []
    elif damage == "profile":
        checks.policy.diagnostics["profile"] = "unknown"
    elif damage == "enabled-diagnostics":
        checks.policy.passive["diagnostics"] = ["health.sh"]
    elif damage == "missing-diagnostics":
        del checks.policy.passive["diagnostics"]
    else:
        checks.policy.passive["supported"] = False
    with pytest.raises(RuntimeError, match="Fast.*coverage"):
        instance.verify_acceptance(sealed=True)


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("drift", ["config", "script", "scheduler", "inventory", "coverage"])
def test_fast_passive_acceptance_still_checks_current_contract(passive, sealed, drift):
    instance, checks, _, _ = fast_passive_fixture(passive)
    original = instance._observe

    def observe(*args):
        result = original(*args)
        if drift == "config":
            result["config"] = [{"name": "foreign"}]
        elif drift == "script":
            result["hashes"]["check_runner.py"] = "changed"
        return result

    instance._observe = observe
    if drift == "scheduler":
        checks.slurm = lambda _: LIVE_SCHEDULER.replace("= 120", "= 0")
    elif drift == "inventory":
        checks._node_inventory = lambda: {"replacement": {"gpus": 8}}
    elif drift == "coverage":
        checks.state["passive"]["acceptance"]["workers"] = {}
    with pytest.raises(RuntimeError):
        instance.verify_acceptance(sealed=sealed)


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
    checks.policy.passive = {
        "supported": False,
        "scheduler": copy.deepcopy(DESIRED_SCHEDULER),
        "opaque": copy.deepcopy(cm["data"]),
    }
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
    checks.slurm = lambda _: LIVE_SCHEDULER.replace("CYCLE,ANY", "ALLOC")
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


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize(
    "before,after",
    [
        ("HealthCheckInterval = 120", "HealthCheckInterval = 0"),
        ("HealthCheckProgram = /opt/slurm_scripts/hc_program.sh", "HealthCheckProgram = (null)"),
        ("Prolog[0] = /opt/slurm_scripts/prolog.sh", "Prolog = (null)"),
        ("Epilog[0] = /opt/slurm_scripts/epilog.sh", "Epilog = (null)"),
    ],
)
def test_enabled_fallback_and_supported_policy_require_live_scheduler(
    passive, supported, before, after
):
    instance, checks, cm, calls = passive
    checks.policy.passive.update(supported=supported, opaque=copy.deepcopy(cm["data"]))
    checks.slurm = lambda _: LIVE_SCHEDULER.replace(before, after)
    with pytest.raises(RuntimeError, match="scheduler|hook"):
        instance.verify(paused=False)
    assert calls["observations"] == []
    assert instance.state.get("status") not in {"enabled", "enabled-fallback"}


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("drift", [None, "config", "script", "scheduler", "inventory"])
def test_sealed_acceptance_reobserves_restoration_without_rerunning_evidence(
    passive, supported, drift
):
    import hashlib

    instance, checks, cm, calls = passive
    checks.policy.passive.update(supported=supported, opaque=copy.deepcopy(cm["data"]))
    instance.begin_acceptance()
    instance.verify(paused=False, fresh=True)
    checks.state["acceptance"] = {"workers": ["gpu-0"]}
    mounted = copy.deepcopy(cm["data"])
    if drift == "config":
        mounted["checks.json"] = "[]"
    elif drift == "script":
        mounted["boot_disk_full.sh"] = "changed"
    elif drift == "scheduler":
        checks.slurm = lambda _: LIVE_SCHEDULER.replace("= 120", "= 0")
    elif drift == "inventory":
        checks._node_inventory = lambda: {"replacement": {"gpus": 8}}

    def observe(worker, _expected, mode):
        assert mode == "observe", "sealed evidence must not rerun native diagnostics"
        calls["observations"].append((worker, mode))
        return {
            "hashes": {
                name: hashlib.sha256(text.rstrip("\n").encode()).hexdigest()
                for name, text in mounted.items()
                if name != "checks.json"
            },
            "config": json.loads(mounted["checks.json"]),
        }

    instance._observe = observe
    before = copy.deepcopy(checks.state)
    writes = calls["saves"]
    calls["observations"].clear()
    if drift:
        with pytest.raises(RuntimeError, match="restor|scheduler|inventory"):
            instance.verify_acceptance(sealed=True)
    else:
        instance.verify_acceptance(sealed=True)
        assert calls["observations"] == [("gpu-0", "observe")]
    assert checks.state == before and calls["saves"] == writes
