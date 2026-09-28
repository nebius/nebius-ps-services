import json
import shlex
import subprocess
import time

import pytest

from nebius_cxcli.soperator_fast_readiness import (
    FastSlurmReadiness,
    active_workers,
    preflight_fast_workers,
    smoke_groups,
    verify_registered_workers,
)


class Slurm:
    def __init__(self):
        self.row = None
        self.submits = 0
        self.cancels = []
        self.interrupt = False
        self.foreign = False

    def run(self, command, timeout):
        words = shlex.split(command)
        if command == "id -u nebius":
            return "1000\n"
        if command == "getent passwd nebius":
            return "nebius:x:1000:1000::/opt/soperator-home/nebius:/bin/bash\n"
        if "mkdir" in words:
            return ""
        if "squeue" in words:
            return ""
        if "sbatch" in words:
            flags = dict(w[2:].split("=", 1) for w in words if w.startswith("--") and "=" in w)
            self.row = [
                "42",
                flags["job-name"],
                "nebius",
                flags["partition"],
                "1000",
                flags["nodelist"],
                "COMPLETED",
                "0:0",
                "cpu=2,gres/gpu=2",
            ]
            self.submits += 1
            return "42\n"
        if "sacct" in words:
            if self.interrupt and self.row:
                self.interrupt = False
                self.row[6] = "RUNNING"
                raise KeyboardInterrupt
            if self.row:
                row = self.row.copy()
                if any(word.startswith("--name=") and word[7:] != row[1] for word in words):
                    return ""
                if self.foreign:
                    row[2] = "other-user"
                return "|".join(row) + "\n"
            return ""
        if "head" in words:
            return "worker-0\nworker-1\n"
        if "scancel" in words:
            self.cancels.append(words[-1])
            self.row[6] = "CANCELLED"
            return ""
        raise AssertionError(command)


def probe(tmp_path, slurm):
    return FastSlurmReadiness(
        generation="sha256:" + "a" * 64,
        receipt_path=tmp_path / "fast.json",
        run=slurm.run,
        assert_authority=lambda: None,
        sleep=lambda _: None,
    )


VALUES = {
    "slurmCluster": {
        "overrideValues": {
            "partitionConfiguration": {
                "configType": "structured",
                "partitions": [{"name": "main", "isAll": True}],
            }
        }
    }
}

GROUPS = [{"partition": "main", "gpu": True, "workers": ["worker-0", "worker-1"]}]


@pytest.mark.parametrize("home", ["/opt/soperator-home/nebius", "/shared/user homes/nebius"])
def test_smoke_uses_the_account_home_and_explicit_working_directory(tmp_path, home):
    slurm = Slurm()
    original = slurm.run
    workspace = home + "/.cache/nebius-cxcli"

    def run(command, timeout):
        words = shlex.split(command)
        if command == "getent passwd nebius":
            return f"nebius:x:1000:1000::{home}:/bin/bash\n"
        if "mkdir" in words:
            assert words[:4] == ["runuser", "-u", "nebius", "--"]
            if words[-1] != workspace:
                raise PermissionError("mkdir: cannot create directory /home/nebius")
        if "sbatch" in words:
            assert "--chdir=" + workspace in words
            assert any(word.startswith("--output=" + workspace + "/") for word in words)
        return original(command, timeout)

    slurm.run = run
    assert probe(tmp_path, slurm).verify(GROUPS)["status"] == "passed"


def _failed_unsubmitted_workspace(tmp_path):
    slurm = Slurm()
    original = slurm.run

    def run(command, timeout):
        if "mkdir" in shlex.split(command):
            raise PermissionError("workspace unavailable")
        return original(command, timeout)

    slurm.run = run
    with pytest.raises(PermissionError):
        probe(tmp_path, slurm).verify(GROUPS)
    path = tmp_path / "fast.json"
    saved = json.loads(path.read_text())
    saved.pop("home")
    entry = saved["jobs"][0]
    entry["output"] = f"/home/nebius/.cache/nebius-cxcli/{entry['name']}.out"
    path.write_text(json.dumps(saved))
    slurm.run = original
    return slurm, saved


def test_canceled_unsubmitted_intent_preserved_without_using_its_workspace(tmp_path):
    slurm, saved = _failed_unsubmitted_workspace(tmp_path)
    original = slurm.run
    calls = []

    def run(command, timeout):
        calls.append(command)
        return original(command, timeout)

    slurm.run = run
    assert probe(tmp_path, slurm).verify(GROUPS)["status"] == "passed"
    current = json.loads((tmp_path / "fast.json").read_text())
    assert current["jobs"][0] == saved["jobs"][0]
    assert len(current["jobs"]) == 2
    assert current["home"] == "/opt/soperator-home/nebius"
    assert all("/home/nebius" not in command for command in calls)
    assert slurm.submits == 1
    assert not slurm.cancels


def test_preparation_checkpoint_does_not_consume_the_submission_budget(tmp_path):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    slurm = Slurm()
    clock = [1800000000.0]
    delayed = False
    path = tmp_path / "fast.json"

    def checkpoint():
        nonlocal delayed
        jobs = json.loads(path.read_text())["jobs"]
        if jobs and not jobs[-1].get("submissionStarted") and not delayed:
            delayed = True
            clock[0] += 180

    instance = probe(tmp_path, slurm)
    instance.clock = lambda: clock[0]
    with execution_checkpoint(checkpoint):
        assert instance.verify(GROUPS)["status"] == "passed"
    saved = json.loads(path.read_text())
    assert saved["jobs"][0]["startedAtSeconds"] == clock[0]
    assert saved["jobs"][0]["deadline"] == clock[0] + 120


def test_slow_authority_and_checkpoints_leave_the_job_budget_available(tmp_path):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    slurm = Slurm()
    clock = [1800000000.0]

    def authority():
        clock[0] += 35

    def checkpoint():
        clock[0] += 20

    instance = probe(tmp_path, slurm)
    instance.authority = authority
    instance.clock = lambda: clock[0]
    with execution_checkpoint(checkpoint):
        assert instance.verify(GROUPS)["status"] == "passed"
    assert slurm.submits == 1


def test_authority_loss_during_submission_checkpoint_prevents_dispatch(tmp_path):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    slurm = Slurm()
    path = tmp_path / "fast.json"
    lost_authority = False
    published_intent = None

    def authority():
        if lost_authority:
            raise RuntimeError("operation authority lost")

    def checkpoint():
        nonlocal lost_authority, published_intent
        saved = json.loads(path.read_text())
        if saved["jobs"] and saved["jobs"][-1].get("submissionStarted"):
            published_intent = saved
            lost_authority = True

    instance = probe(tmp_path, slurm)
    instance.authority = authority
    with execution_checkpoint(checkpoint), pytest.raises(RuntimeError, match="authority lost"):
        instance.verify(GROUPS)

    assert slurm.submits == 0
    assert not slurm.cancels
    assert json.loads(path.read_text()) == published_intent
    entry = published_intent["jobs"][-1]
    assert entry["submissionStarted"] is True
    assert entry["jobId"] == ""
    assert "submissionNotDispatched" not in entry
    assert "proof" not in entry


@pytest.mark.parametrize("recover_by_name", [False, True])
def test_completed_job_is_proven_before_checkpoint_publication(tmp_path, recover_by_name):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    slurm = Slurm()
    clock = [1800000000.0]
    path = tmp_path / "fast.json"
    instance = probe(tmp_path, slurm)
    instance.clock = lambda: clock[0]
    if recover_by_name:
        instance.verify(GROUPS)
        saved = json.loads(path.read_text())
        saved.pop("status")
        saved["jobs"][0].pop("proof")
        saved["jobs"][0]["jobId"] = ""
        path.write_text(json.dumps(saved))

    def checkpoint():
        jobs = json.loads(path.read_text())["jobs"]
        if jobs and jobs[-1].get("jobId"):
            clock[0] += 121

    with execution_checkpoint(checkpoint):
        assert instance.verify(GROUPS)["status"] == "passed"
    assert slurm.submits == 1


def _expired_before_submission_transport(tmp_path):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    slurm = Slurm()
    clock = [1800000000.0]
    path = tmp_path / "fast.json"

    def checkpoint():
        jobs = json.loads(path.read_text())["jobs"]
        if jobs and jobs[-1].get("submissionStarted") and not jobs[-1].get("cancelIntent"):
            clock[0] += 121

    instance = probe(tmp_path, slurm)
    instance.clock = lambda: clock[0]
    with execution_checkpoint(checkpoint), pytest.raises(TimeoutError, match="two minutes"):
        instance.verify(GROUPS)
    saved = json.loads(path.read_text())
    entry = saved["jobs"][0]
    return slurm, entry


def test_pretransport_expiry_records_no_dispatch_and_can_resume(tmp_path):
    slurm, entry = _expired_before_submission_transport(tmp_path)
    path = tmp_path / "fast.json"
    assert slurm.submits == 0
    assert entry["submissionStarted"] is True
    assert entry["submissionNotDispatched"] is True
    assert entry["cancelIntent"] is True
    assert entry["jobId"] == ""
    assert probe(tmp_path, slurm).verify(GROUPS)["status"] == "passed"
    assert json.loads(path.read_text())["jobs"][0] == entry
    assert slurm.submits == 1


def _uncertain_submission(tmp_path):
    slurm, entry = _expired_before_submission_transport(tmp_path)
    path = tmp_path / "fast.json"
    saved = json.loads(path.read_text())
    saved["jobs"][-1].pop("submissionNotDispatched")
    path.write_text(json.dumps(saved))
    instance = probe(tmp_path, slurm)
    instance.clock = lambda: entry["deadline"] + 1
    return slurm, instance, saved


def test_explicit_retirement_preserves_uncertainty_and_requires_fresh_smoke(tmp_path):
    from nebius_cxcli.soperator_checks_policy import checks_digest

    slurm, instance, original = _uncertain_submission(tmp_path)
    confirmations = []
    instance.confirm_retirement = lambda prompt: confirmations.append(prompt) or True
    assert instance.verify(GROUPS)["status"] == "passed"
    saved = json.loads(instance.path.read_text())
    assert saved["jobs"][0] == original["jobs"][0]
    assert saved["jobs"][1]["name"] != original["jobs"][0]["name"]
    assert saved["jobs"][1]["proof"]["state"] == "COMPLETED"
    assert saved["retirements"][0]["attemptSha256"] == checks_digest(original["jobs"][0])
    assert saved["retirements"][0]["disposition"] == "outcome-unknown"
    assert original["jobs"][0]["name"] in confirmations[0]
    assert slurm.submits == 1
    assert not slurm.cancels


@pytest.mark.parametrize("decision", [None, False, "yes", 1])
def test_uncertain_submission_is_not_retired_without_explicit_confirmation(tmp_path, decision):
    slurm, instance, original = _uncertain_submission(tmp_path)
    if decision is not None:
        instance.confirm_retirement = lambda _: decision
    with pytest.raises(RuntimeError, match="explicit interactive"):
        instance.verify(GROUPS)
    assert json.loads(instance.path.read_text()) == original
    assert not slurm.submits and not slurm.cancels


@pytest.mark.parametrize("query", ["sacct", "squeue"])
@pytest.mark.parametrize("after_confirmation", [False, True])
def test_retirement_query_failure_is_never_absence(tmp_path, query, after_confirmation):
    slurm, instance, original = _uncertain_submission(tmp_path)
    confirmed = False
    run = slurm.run

    def confirm(_):
        nonlocal confirmed
        confirmed = True
        return True

    def failing_run(command, timeout):
        if query in shlex.split(command) and confirmed == after_confirmation:
            raise TimeoutError("lookup unavailable")
        return run(command, timeout)

    instance.confirm_retirement = confirm
    instance.run = failing_run
    with pytest.raises(TimeoutError, match="lookup unavailable"):
        instance.verify(GROUPS)
    assert json.loads(instance.path.read_text()) == original
    assert not slurm.submits and not slurm.cancels


def test_visible_queue_job_blocks_retirement_without_accounting(tmp_path):
    slurm, instance, original = _uncertain_submission(tmp_path)
    run = slurm.run
    instance.run = lambda command, timeout: (
        "99\n" if "squeue" in shlex.split(command) else run(command, timeout)
    )
    instance.confirm_retirement = lambda _: pytest.fail("visible job cannot be retired")
    with pytest.raises(RuntimeError, match="visible Slurm job"):
        instance.verify(GROUPS)
    assert json.loads(instance.path.read_text()) == original
    assert not slurm.submits and not slurm.cancels


@pytest.mark.parametrize("change", ["receipt", "authority"])
def test_retirement_revalidates_receipt_and_authority_after_confirmation(tmp_path, change):
    slurm, instance, original = _uncertain_submission(tmp_path)
    confirmed = False

    def confirm(_):
        nonlocal confirmed
        confirmed = True
        if change == "receipt":
            original["concurrentWriter"] = True
            instance.path.write_text(json.dumps(original))
        return True

    def authority():
        if confirmed and change == "authority":
            raise RuntimeError("authority lost")

    instance.confirm_retirement = confirm
    instance.authority = authority
    with pytest.raises(RuntimeError, match="receipt changed|authority lost"):
        instance.verify(GROUPS)
    assert json.loads(instance.path.read_text()) == original
    assert not slurm.submits and not slurm.cancels


def test_retirement_checkpoint_failure_cannot_dispatch_and_resume_republishes(tmp_path):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    slurm, instance, original = _uncertain_submission(tmp_path)
    instance.confirm_retirement = lambda _: True

    def failed_checkpoint():
        if json.loads(instance.path.read_text()).get("retirements"):
            raise RuntimeError("checkpoint not acknowledged")

    with execution_checkpoint(failed_checkpoint), pytest.raises(RuntimeError, match="checkpoint"):
        instance.verify(GROUPS)
    audit = json.loads(instance.path.read_text())["retirements"]
    assert not slurm.submits
    published = False
    run = slurm.run

    def successful_checkpoint():
        nonlocal published
        saved = json.loads(instance.path.read_text())
        assert saved["retirements"] == audit
        published = True

    def checked_run(command, timeout):
        if "sbatch" in shlex.split(command):
            assert published
        return run(command, timeout)

    instance.run = checked_run
    instance.confirm_retirement = lambda _: pytest.fail("retirement already approved")
    with execution_checkpoint(successful_checkpoint):
        assert instance.verify(GROUPS)["status"] == "passed"
    assert json.loads(instance.path.read_text())["jobs"][0] == original["jobs"][0]
    assert slurm.submits == 1


@pytest.mark.parametrize("verify_only", [False, True])
@pytest.mark.parametrize("accounting_state", ["RUNNING", "COMPLETED"])
def test_late_retired_job_blocks_acceptance_and_completed_verification(
    tmp_path, verify_only, accounting_state
):
    slurm, instance, original = _uncertain_submission(tmp_path)
    instance.confirm_retirement = lambda _: True
    if verify_only:
        instance.verify(GROUPS)
    run = slurm.run
    before = instance.path.read_bytes()
    old = original["jobs"][0]

    def late_job(command, timeout):
        if slurm.submits and "sacct" in shlex.split(command) and "--name=" + old["name"] in command:
            return f"99|{old['name']}|nebius|main|1000|worker-0,worker-1|{accounting_state}|0:0|cpu=2,gres/gpu=2\n"
        if slurm.submits and "squeue" in shlex.split(command):
            return "99\n"
        return run(command, timeout)

    instance.run = late_job
    with pytest.raises(
        RuntimeError, match="retired fast smoke attempt has active|visible Slurm job"
    ):
        instance.verify(GROUPS, verify_only=verify_only)
    if verify_only:
        assert instance.path.read_bytes() == before
    else:
        assert json.loads(instance.path.read_text()).get("status") != "passed"
    assert slurm.submits == 1 and not slurm.cancels


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", "sha256:" + "b" * 64),
        ("attemptName", "cxcli-fast-foreign"),
        ("attemptSha256", "sha256:" + "b" * 64),
        ("disposition", "never-submitted"),
        ("approvedAtSeconds", True),
        ("observedAtSeconds", 0),
        ("extra", True),
    ],
)
def test_retirement_audit_tampering_rejected_before_transport(tmp_path, field, value):
    _slurm, instance, _ = _uncertain_submission(tmp_path)
    instance.confirm_retirement = lambda _: True
    instance.verify(GROUPS)
    saved = json.loads(instance.path.read_text())
    saved["retirements"][0][field] = value
    instance.path.write_text(json.dumps(saved))
    instance.run = lambda *_: pytest.fail("invalid retirement reached transport")
    with pytest.raises(RuntimeError, match="retirement"):
        instance.verify(GROUPS, verify_only=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("submissionNotDispatched", "true"),
        ("submissionNotDispatched", 1),
        ("submissionStarted", False),
        ("cancelIntent", False),
        ("jobId", "42"),
        ("proof", {}),
    ],
)
def test_no_dispatch_evidence_cannot_authorize_a_possible_job(tmp_path, field, value):
    slurm, _entry = _expired_before_submission_transport(tmp_path)
    path = tmp_path / "fast.json"
    saved = json.loads(path.read_text())
    saved["jobs"][0][field] = value
    path.write_text(json.dumps(saved))
    calls = []
    instance = probe(tmp_path, slurm)
    instance.run = lambda *args: calls.append(args) or ""
    with pytest.raises(RuntimeError, match="receipt"):
        instance.verify(GROUPS)
    assert not calls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("submissionStarted", True),
        ("submissionStarted", 0),
        ("jobId", "42"),
        ("proof", {}),
        ("cancelIntent", False),
        ("cancelIntent", 1),
        ("output", "/unrelated/file"),
    ],
)
def test_unbound_workspace_cannot_reinterpret_a_possible_submission(tmp_path, field, value):
    slurm, saved = _failed_unsubmitted_workspace(tmp_path)
    saved["jobs"][0][field] = value
    (tmp_path / "fast.json").write_text(json.dumps(saved))
    calls = []
    instance = probe(tmp_path, slurm)
    instance.run = lambda *args: calls.append(args) or ""
    with pytest.raises(RuntimeError, match="receipt"):
        instance.verify(GROUPS)
    assert not calls


@pytest.mark.parametrize(
    "home",
    ["relative", "/", "/opt/../home", "/opt//home", "/opt/%j/nebius", "/opt/\\home", "/opt/\nhome"],
)
def test_account_home_must_be_literal_and_canonical_before_submission(tmp_path, home):
    slurm = Slurm()
    original = slurm.run

    def run(command, timeout):
        if command == "getent passwd nebius":
            return f"nebius:x:1000:1000::{home}:/bin/bash\n"
        return original(command, timeout)

    slurm.run = run
    with pytest.raises(RuntimeError, match="account home"):
        probe(tmp_path, slurm).verify(GROUPS)
    assert not slurm.submits
    assert not (tmp_path / "fast.json").exists()


@pytest.mark.parametrize("field,value", [(0, "other"), (2, "0"), (2, "1001")])
def test_account_lookup_must_match_the_nonroot_user(tmp_path, field, value):
    slurm = Slurm()
    original = slurm.run
    account = ["nebius", "x", "1000", "1000", "", "/opt/soperator-home/nebius", "/bin/bash"]
    account[field] = value
    slurm.run = lambda command, timeout: (
        ":".join(account) if command == "getent passwd nebius" else original(command, timeout)
    )
    with pytest.raises(RuntimeError, match="account home"):
        probe(tmp_path, slurm).verify(GROUPS)
    assert not slurm.submits


def test_completed_smoke_rejects_changed_home_without_job_transport_or_rewrite(tmp_path):
    slurm = Slurm()
    instance = probe(tmp_path, slurm)
    instance.verify(GROUPS)
    before = instance.path.read_bytes()
    original = slurm.run
    calls = []

    def run(command, timeout):
        calls.append(command)
        if command == "getent passwd nebius":
            return "nebius:x:1000:1000::/different/home:/bin/bash\n"
        return original(command, timeout)

    instance.run = run
    with pytest.raises(RuntimeError, match="home changed"):
        instance.verify(GROUPS, verify_only=True)
    assert calls == ["id -u nebius", "getent passwd nebius"]
    assert instance.path.read_bytes() == before
    assert slurm.submits == 1
    assert not slurm.cancels


def test_smoke_ordinary_user_exact_workers_and_resume_no_duplicate(tmp_path):
    slurm = Slurm()
    proof = probe(tmp_path, slurm).verify(GROUPS)
    assert proof["jobs"][0]["user"] == "nebius"
    assert proof["jobs"][0]["allocation"] == "cpu=2,gres/gpu=2"
    assert probe(tmp_path, slurm).verify(GROUPS) == proof
    assert slurm.submits == 1
    state = json.loads((tmp_path / "fast.json").read_text())
    state["jobs"][0]["jobId"] = ""  # submission returned, process died before ID persisted
    (tmp_path / "fast.json").write_text(json.dumps(state))
    assert probe(tmp_path, slurm).verify(GROUPS) == proof
    assert slurm.submits == 1


def test_interrupt_cancels_only_its_recorded_job_and_can_resume(tmp_path):
    slurm = Slurm()
    slurm.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        probe(tmp_path, slurm).verify(GROUPS)
    assert slurm.cancels == ["42"]
    # Accounting name filtering excludes the old terminal attempt on retry.
    slurm.row = None
    # Keep cancellation terminal evidence for initial resume classification.
    saved = json.loads((tmp_path / "fast.json").read_text())["jobs"][0]
    original = slurm.run

    def run(command, timeout):
        if command.startswith("TZ=UTC sacct") and "--jobs=42" in command and slurm.row is None:
            return (
                "|".join(
                    [
                        "42",
                        saved["name"],
                        "nebius",
                        "main",
                        "1000",
                        "worker-0,worker-1",
                        "CANCELLED",
                        "0:0",
                        "",
                    ]
                )
                + "\n"
            )
        return original(command, timeout)

    slurm.run = run
    assert probe(tmp_path, slurm).verify(GROUPS)["status"] == "passed"
    assert slurm.submits == 2


def test_foreign_accounting_never_authorizes_cancellation(tmp_path):
    slurm = Slurm()
    probe(tmp_path, slurm).verify(GROUPS)
    slurm.foreign = True
    with pytest.raises(RuntimeError, match="ownership"):
        probe(tmp_path, slurm).verify(GROUPS)
    assert not slurm.cancels


@pytest.mark.parametrize(
    "field,value,error",
    [
        (5, "worker-0", "different workers"),
        (7, "1:0", "successfully"),
        (8, "gres/gpu=1", "one GPU"),
    ],
)
def test_incomplete_accounting_is_not_success(tmp_path, field, value, error):
    slurm = Slurm()
    probe(tmp_path, slurm).verify(GROUPS)
    slurm.row[field] = value
    with pytest.raises(RuntimeError, match=error):
        probe(tmp_path, slurm).verify(GROUPS)


def test_changed_generation_cannot_reuse_probe(tmp_path):
    slurm = Slurm()
    probe(tmp_path, slurm).verify(GROUPS)
    other = probe(tmp_path, slurm)
    other.generation = "different"
    with pytest.raises(RuntimeError, match="different inputs"):
        other.verify(GROUPS)
    assert slurm.submits == 1


def test_ephemeral_inventory_does_not_wake_maximum():
    values = {
        "nodesets": {
            "overrideValues": {
                "nodesets": [
                    {
                        "name": "worker",
                        "replicas": 8,
                        "ephemeralNodes": True,
                        "gpu": {"enabled": True},
                    }
                ]
            }
        }
    }
    nodeset = {
        "kind": "NodeSet",
        "metadata": {"name": "worker", "uid": "set-uid"},
        "spec": {"replicas": 8, "ephemeralNodes": True},
    }
    power = {
        "kind": "NodeSetPowerState",
        "metadata": {
            "ownerReferences": [
                {"kind": "NodeSet", "name": "worker", "uid": "set-uid", "controller": True}
            ]
        },
        "spec": {"nodeSetRef": "worker", "activeNodes": [0]},
    }
    assert active_workers(values, [nodeset], [power]) == {"worker-0": True}
    power["spec"]["activeNodes"] = []
    with pytest.raises(RuntimeError, match="one active"):
        active_workers(values, [nodeset], [power])


def test_zero_initial_capacity_fails_before_cloud():
    with pytest.raises(ValueError, match="initially active"):
        preflight_fast_workers({"nodesets": {"overrideValues": {"nodesets": []}}})


def test_smoke_groups_separate_cpu_gpu_and_reject_hidden_only():
    nodes = "NodeName=cpu-0 State=IDLE+CLOUD SlurmdStartTime=2026-01-01\nNodeName=gpu-0 State=IDLE SlurmdStartTime=2026-01-01"
    partitions = "PartitionName=hidden State=UP Hidden=YES Nodes=cpu-0,gpu-0\nPartitionName=main State=UP Hidden=NO Nodes=cpu-0,gpu-0"
    groups = smoke_groups({"cpu-0": False, "gpu-0": True}, nodes, partitions, values=VALUES)
    assert groups == [
        {"partition": "main", "gpu": False, "workers": ["cpu-0"]},
        {"partition": "main", "gpu": True, "workers": ["gpu-0"]},
    ]
    with pytest.raises(RuntimeError, match="ordinary partition"):
        smoke_groups({"cpu-0": False}, nodes, partitions.splitlines()[0], values=VALUES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("jobId", "42; unsafe-command"),
        ("name", "foreign-job"),
        ("output", "/unrelated/file"),
        ("workers", ["foreign-worker"]),
    ],
)
def test_receipt_job_binding_validated_before_transport(tmp_path, field, value):
    slurm = Slurm()
    probe(tmp_path, slurm).verify(GROUPS)
    path = tmp_path / "fast.json"
    saved = json.loads(path.read_text())
    saved["jobs"][0][field] = value
    path.write_text(json.dumps(saved))
    calls = []
    reader = probe(tmp_path, slurm)
    reader.run = lambda *args: calls.append(args) or ""
    with pytest.raises(RuntimeError, match="receipt"):
        reader.verify(GROUPS)
    assert not calls


def test_smoke_rejects_unconfigured_live_partition_and_wrong_nodeset_membership():
    nodes = "NodeName=worker-0 State=IDLE SlurmdStartTime=2026-01-01"
    for partitions, values in (
        ("PartitionName=foreign State=UP Nodes=worker-0", VALUES),
        (
            "PartitionName=main State=UP Nodes=worker-0",
            {
                "slurmCluster": {
                    "overrideValues": {
                        "partitionConfiguration": {
                            "configType": "structured",
                            "partitions": [{"name": "main", "nodeSetRefs": ["different"]}],
                        }
                    }
                }
            },
        ),
    ):
        with pytest.raises(RuntimeError, match="ordinary partition"):
            smoke_groups({"worker-0": True}, nodes, partitions, values=values)


@pytest.mark.parametrize(
    "state",
    [
        "IDLE+MAINT",
        "IDLE*",
        "IDLE+FAIL",
        "IDLE+POWERING_UP",
        "IDLE+DRAINED",
        "IDLE+CLOUD+DRAIN+MAINTENANCE+RESERVED",
        "IDLE+DRAINING",
        "IDLE+DRAIN",
    ],
)
def test_smoke_rejects_currently_unschedulable_workers(state):
    with pytest.raises(RuntimeError, match="not registered|not schedulable"):
        smoke_groups(
            {"worker-0": True},
            f"NodeName=worker-0 State={state} SlurmdStartTime=2026-01-01",
            "PartitionName=main State=UP Nodes=worker-0",
            values=VALUES,
        )


@pytest.mark.parametrize(
    "state",
    ["IDLE", "MIXED", "ALLOCATED", "IDLE+CLOUD+DRAIN+MAINTENANCE+RESERVED", "IDLE+MAINT"],
)
def test_pre_restore_registration_does_not_require_open_scheduling(state):
    verify_registered_workers(
        {"worker-0": True}, f"NodeName=worker-0 State={state} SlurmdStartTime=2026-01-01"
    )


@pytest.mark.parametrize(
    "evidence",
    [
        "",
        "NodeName=other State=IDLE SlurmdStartTime=2026-01-01",
        "NodeName=worker-0 State=IDLE SlurmdStartTime=Unknown",
        "NodeName=worker-0 State=IDLE",
        *[
            f"NodeName=worker-0 State={state} SlurmdStartTime=2026-01-01"
            for state in (
                "IDLE*",
                "IDLE+NO_RESPOND",
                "UNKNOWN",
                "FUTURE",
                "IDLE+INVALID_REG",
                "DOWN",
                "IDLE+FAIL",
                "IDLE+POWERING_UP",
                "IDLE+POWERING_DOWN",
                "POWERED_DOWN",
            )
        ],
    ],
)
def test_pre_restore_registration_still_rejects_missing_or_unhealthy_workers(evidence):
    with pytest.raises(RuntimeError, match="not registered"):
        verify_registered_workers({"worker-0": True}, evidence)


@pytest.mark.parametrize("before_id", [False, True])
def test_transport_timeout_reconciles_and_cancels_accepted_job(tmp_path, before_id):
    slurm = Slurm()
    original = slurm.run
    interrupted = False

    def run(command, timeout):
        nonlocal interrupted
        result = original(command, timeout)
        if slurm.row and not interrupted and (("sbatch" in shlex.split(command)) == before_id):
            interrupted = True
            slurm.row[6] = "RUNNING"
            raise subprocess.TimeoutExpired("transport", timeout)
        return result

    slurm.run = run
    with pytest.raises(subprocess.TimeoutExpired):
        probe(tmp_path, slurm).verify(GROUPS)
    assert slurm.cancels == ["42"]
    saved = json.loads((tmp_path / "fast.json").read_text())
    assert saved["jobs"][0]["cancelIntent"] is True
    assert saved["jobs"][0]["jobId"] == "42"
    assert not saved["jobs"][0].get("submissionNotDispatched")


def test_expired_terminal_attempt_can_resume_with_new_owned_job(tmp_path):
    slurm = Slurm()
    first = probe(tmp_path, slurm)
    first.verify(GROUPS)
    path = tmp_path / "fast.json"
    saved = json.loads(path.read_text())
    saved["jobs"][0].pop("proof")
    saved.pop("status")
    path.write_text(json.dumps(saved))
    retry = probe(tmp_path, slurm)
    retry.clock = lambda: saved["jobs"][0]["deadline"] + 1
    with pytest.raises(TimeoutError):
        retry.verify(GROUPS)
    assert json.loads(path.read_text())["jobs"][0]["cancelIntent"] is True
    original = slurm.run

    def run(command, timeout):
        # A new deterministic name must not match the retired attempt.
        if "--name=" in command and saved["jobs"][0]["name"] not in command:
            return ""
        return original(command, timeout)

    slurm.run = run
    assert probe(tmp_path, slurm).verify(GROUPS)["status"] == "passed"
    assert slurm.submits == 2


def test_accounting_uses_utc_independent_of_client_timezone(tmp_path, monkeypatch):
    monkeypatch.setattr(
        time, "localtime", lambda *_: (_ for _ in ()).throw(AssertionError("local timezone"))
    )
    slurm = Slurm()
    original = slurm.run

    def run(command, timeout):
        if "sacct" in shlex.split(command):
            assert command.startswith("TZ=UTC sacct ")
        return original(command, timeout)

    slurm.run = run
    assert probe(tmp_path, slurm).verify(GROUPS)["status"] == "passed"


def test_smoke_binds_numeric_user_identity_without_optional_accounting_comments(tmp_path):
    slurm = Slurm()
    original = slurm.run

    def run(command, timeout):
        if "sacct" in shlex.split(command):
            assert ",UID," in command
            assert "Comment" not in command
        return original(command, timeout)

    slurm.run = run
    assert probe(tmp_path, slurm).verify(GROUPS)["jobs"][0]["uid"] == "1000"
    slurm.row[4] = "1001"
    with pytest.raises(RuntimeError, match="ownership"):
        probe(tmp_path, slurm).verify(GROUPS)
    assert not slurm.cancels


@pytest.mark.parametrize("damage", ["jobs", "cancel", "proof", "accounting", None])
def test_completed_revalidation_never_submits_or_cancels(tmp_path, damage):
    slurm = Slurm()
    instance = probe(tmp_path, slurm)
    proof = instance.verify(GROUPS)
    saved = json.loads(instance.path.read_text())
    if damage == "jobs":
        saved["jobs"] = []
    elif damage == "cancel":
        saved["jobs"][0]["cancelIntent"] = True
    elif damage == "proof":
        saved["jobs"][0].pop("proof")
    elif damage == "accounting":
        slurm.row = None
    instance.path.write_text(json.dumps(saved))
    before = instance.path.read_bytes()
    if damage:
        with pytest.raises(RuntimeError, match="[Pp]roof"):
            instance.verify(GROUPS, verify_only=True)
    else:
        assert instance.verify(GROUPS, verify_only=True) == proof
    assert slurm.submits == 1
    assert not slurm.cancels
    assert instance.path.read_bytes() == before
