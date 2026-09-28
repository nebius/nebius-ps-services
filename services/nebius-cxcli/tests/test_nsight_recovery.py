import copy
import json
import re
import shlex
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli import nsight_recovery as recovery
from nebius_cxcli.deployment_cli import DeployOptions
from nebius_cxcli.deployment_state import digest
from nebius_cxcli.nsight_profiling import customization_job
from nebius_cxcli.nsight_upgrade_recovery import deploy_resume_command, require_pre_promotion
from nebius_cxcli.soperator_protected_data_plane import (
    bind_protected_job_authority,
    bind_protected_workload_identity,
    protected_workload_identity,
)
from test_soperator_recovery_journal import _journal, _JournalRunner


def manifest():
    return bind_protected_job_authority(
        customization_job(
            name="cxcli-nsight-admit",
            image="example/jail@sha256:" + "a" * 64,
            pvc="passive",
            filesystem_id="fs",
            request={"action": "admit"},
        ),
        operation_id=digest("operation"),
        fence_epoch=1,
        pvc_uid="pvc-uid",
    )


def failed_attempt():
    request = manifest()
    workload = protected_workload_identity(request).workload_sha256
    job = bind_protected_workload_identity(
        request, requested_workload_sha256=workload, admitted_workload_sha256=workload
    )
    job["metadata"]["uid"] = "failed-uid"
    job["status"] = {"active": 0, "conditions": [{"type": "Failed", "status": "True"}]}
    pod = copy.deepcopy(job["spec"]["template"])
    pod["metadata"].update(
        uid="pod-uid", ownerReferences=[{"kind": "Job", "uid": "failed-uid", "controller": True}]
    )
    pod["status"] = {"phase": "Failed"}
    for spec_key, status_key in (
        ("containers", "containerStatuses"),
        ("initContainers", "initContainerStatuses"),
    ):
        pod["status"][status_key] = [
            {
                "name": c["name"],
                "containerID": "container://" + c["name"],
                "state": {"terminated": {"finishedAt": "2026-09-18T00:00:00Z", "exitCode": 1}},
            }
            for c in pod["spec"].get(spec_key, [])
        ]
    chain = recovery.initial_chain(request)
    chain["attempts"][0].update(jobUid="failed-uid", workloadSha256=workload)
    kube = SimpleNamespace(get=lambda *a: job, run=lambda *a: json.dumps({"items": [pod]}))
    return chain, job, pod, kube


class Journal:
    def __init__(self, chain=None):
        self.rows = {} if chain is None else {"nsight-admit": copy.deepcopy(chain)}
        self.writes = 0

    def stage(self, name):
        return self.rows.get(name)

    def checkpoint(self, name, value):
        recovery.validate_transition(self.stage(name), value)
        self.rows[name] = copy.deepcopy(value)
        self.writes += 1


def test_uid_is_checkpointed_before_wait_and_missing_recorded_job_cannot_create():
    journal = Journal()
    request = manifest()
    calls = []

    def ensure(job, **kwargs):
        calls.append(kwargs)
        if kwargs["expected_job_uid"]:
            assert not kwargs["allow_create"]
            raise RuntimeError("recorded Job missing")
        return "uid", digest(job)

    def wait(**kwargs):
        assert journal.stage("nsight-admit")["attempts"][0]["jobUid"] == "uid"
        raise RuntimeError("client disconnected")

    fake = SimpleNamespace(
        _ensure_protected_data_plane_job=ensure, _wait_protected_data_plane_job=wait
    )
    for message in ("client disconnected", "recorded Job missing"):
        with pytest.raises(RuntimeError, match=message):
            recovery.run_attempt(
                fake,
                journal,
                stage="nsight-admit",
                manifest=request,
                fence=lambda: None,
                context="ctx",
                env={},
            )
    assert len(journal.stage("nsight-admit")["attempts"]) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "active",
        "uid",
        "deleting",
        "running",
        "missing-status",
        "container",
        "owner",
        "execution",
        "annotation",
    ],
)
def test_terminal_proof_requires_exact_quiescent_execution(mutation):
    chain, job, pod, kube = failed_attempt()
    assert recovery.terminal_proof(kube, chain["attempts"][0])["podUid"] == "pod-uid"
    if mutation == "active":
        job["status"]["active"] = 1
    elif mutation == "uid":
        job["metadata"]["uid"] = "replacement"
    elif mutation == "deleting":
        job["metadata"]["deletionTimestamp"] = "now"
    elif mutation == "running":
        pod["status"]["phase"] = "Running"
    elif mutation == "missing-status":
        pod["status"]["initContainerStatuses"] = []
    elif mutation == "container":
        pod["status"]["containerStatuses"][0]["state"] = {"running": {}}
    elif mutation == "owner":
        pod["metadata"]["ownerReferences"][0]["uid"] = "other"
    elif mutation == "execution":
        pod["spec"]["containers"][0]["image"] = "other"
    else:
        job["metadata"]["annotations"] = {}
    with pytest.raises(RuntimeError):
        recovery.terminal_proof(kube, chain["attempts"][0])


def test_recovery_reservation_is_idempotent_immutable_and_bounded():
    chain, _, _, kube = failed_attempt()
    proof = recovery.terminal_proof(kube, chain["attempts"][0])
    second = recovery.reserve_successor(chain, predecessor_uid="failed-uid", epoch=2, proof=proof)
    recovery.validate_transition(chain, second)
    assert (
        recovery.reserve_successor(second, predecessor_uid="failed-uid", epoch=3, proof=proof)
        == second
    )
    assert second["attempts"][1]["manifest"]["metadata"]["name"].endswith("-r1")
    second["attempts"][-1].update(jobUid="second", workloadSha256=digest("second"))
    proof2 = {**proof, "jobUid": "second", "workloadSha256": digest("second")}
    proof2["proofSha256"] = digest({k: v for k, v in proof2.items() if k != "proofSha256"})
    third = recovery.reserve_successor(second, predecessor_uid="second", epoch=4, proof=proof2)
    third["attempts"][-1].update(jobUid="third", workloadSha256=digest("third"))
    with pytest.raises(RuntimeError, match="budget"):
        recovery.reserve_successor(third, predecessor_uid="third", epoch=5, proof={})
    changed = copy.deepcopy(second)
    changed["attempts"][0]["jobUid"] = "foreign"
    with pytest.raises(RuntimeError):
        recovery.validate_transition(second, changed)


def test_standalone_install_reserves_one_terminal_successor_before_creation(monkeypatch):
    chain, _, _, kube = failed_attempt()
    monkeypatch.setattr("nebius_cxcli.nsight_runtime.NsightKubernetes", lambda _: kube)
    journal = Journal(chain)
    creates = []

    def ensure(job, **kwargs):
        reserved = journal.stage("nsight-admit")["attempts"]
        assert len(reserved) == 2 and reserved[0] == chain["attempts"][0]
        assert reserved[1]["epoch"] == 2
        creates.append(kwargs["allow_create"])
        return "successor", digest(job)

    fake = SimpleNamespace(
        console=SimpleNamespace(print=lambda *a: None),
        _ensure_protected_data_plane_job=ensure,
        _wait_protected_data_plane_job=lambda **kw: 'CXCLI_NSIGHT={"schema":"done"}',
    )
    arguments = dict(
        stage="nsight-admit",
        manifest=manifest(),
        fence=lambda: None,
        context="ctx",
        env={},
        retry_epoch=2,
    )
    assert recovery.run_attempt(fake, journal, **arguments) == {"schema": "done"}
    writes = journal.writes
    assert recovery.run_attempt(fake, journal, **arguments) == {"schema": "done"}
    assert creates == [True] and journal.writes == writes


def test_standalone_retry_does_not_recreate_running_or_missing_recorded_job(monkeypatch):
    chain, job, _, kube = failed_attempt()
    job["status"] = {"active": 1}
    monkeypatch.setattr("nebius_cxcli.nsight_runtime.NsightKubernetes", lambda _: kube)
    journal = Journal(chain)

    def ensure(job, **kwargs):
        assert kwargs["allow_create"] is False and kwargs["expected_job_uid"] == "failed-uid"
        raise RuntimeError("recorded operation pending")

    fake = SimpleNamespace(_ensure_protected_data_plane_job=ensure)
    with pytest.raises(RuntimeError, match="pending"):
        recovery.run_attempt(
            fake,
            journal,
            stage="nsight-admit",
            manifest=manifest(),
            fence=lambda: None,
            context="ctx",
            env={},
            retry_epoch=2,
        )
    assert journal.writes == 0


def test_dry_run_reserves_nothing_and_repeated_command_resumes_one_successor(monkeypatch):
    chain, _, _, kube = failed_attempt()
    kube.context = "ctx"
    monkeypatch.setattr("nebius_cxcli.nsight_runtime.NsightKubernetes", lambda _: kube)
    journal = Journal(chain)
    fake = SimpleNamespace(
        console=SimpleNamespace(print=lambda *a, **kw: None),
        _ensure_protected_data_plane_job=lambda *a, **kw: ("successor", digest("admitted")),
        _wait_protected_data_plane_job=lambda **kw: (
            'CXCLI_NSIGHT={"schema":"nebius-cxcli.nsight-packages.v2"}'
        ),
    )
    args = dict(
        stage="nsight-admit",
        predecessor_uid="failed-uid",
        epoch=2,
        manifest=manifest(),
        fence=lambda: None,
        env={},
    )
    recovery.recover_stage(fake, journal, dry_run=True, **args)
    assert journal.writes == 0 and len(journal.stage("nsight-admit")["attempts"]) == 1
    recovery.recover_stage(fake, journal, dry_run=False, **args)
    writes = journal.writes
    recovery.recover_stage(fake, journal, dry_run=False, **args)
    assert journal.writes == writes and len(journal.stage("nsight-admit")["attempts"]) == 2


def test_configmap_owner_records_attempts_with_cas_and_refuses_rewind(tmp_path):
    runner = _JournalRunner()
    owner = _journal(runner, tmp_path / "journal.json")
    owner.establish()
    stages = recovery.RootfsStages(owner)
    chain, _, _, kube = failed_attempt()
    stages.checkpoint("nsight-admit", recovery.initial_chain(manifest()))
    stages.checkpoint("nsight-admit", chain)
    successor = recovery.reserve_successor(
        chain,
        predecessor_uid="failed-uid",
        epoch=2,
        proof=recovery.terminal_proof(kube, chain["attempts"][0]),
    )
    runner.reject_next_replace = True
    with pytest.raises(RuntimeError, match="CAS"):
        stages.checkpoint("nsight-admit", successor)
    assert stages.stage("nsight-admit") == chain
    stages.checkpoint("nsight-admit", successor)
    with pytest.raises(RuntimeError):
        stages.checkpoint("nsight-admit", chain)


@pytest.mark.parametrize(
    "controls",
    [
        DeployOptions().controls(),
        DeployOptions(
            target_ref="target", job_policy="wait-to-finish", job_wait_timeout="2h"
        ).controls(),
        DeployOptions(all_targets=True, skip_validations=True).controls(),
    ],
)
def test_resume_command_preserves_exact_original_controls(controls):
    args = shlex.split(deploy_resume_command("/tmp/a b/config.yaml", controls))
    assert args[:3] == ["nebius-cxcli", "deploy", "/tmp/a b/config.yaml"]
    assert ("--target" in args) == bool(controls["targetRef"])
    assert ("--all-targets" in args) == controls["allTargets"]
    assert args[args.index("--job-policy") + 1] == controls["jobPolicy"]
    with pytest.raises(RuntimeError, match="missing"):
        deploy_resume_command("config", {})


@pytest.mark.parametrize(
    "boundary", ["irreversibleIntent", "irreversibleFrontier", "stage", "sealed", "customization"]
)
def test_upgrade_recovery_refuses_promotion_or_ambiguous_frontier(boundary):
    receipt = {"transitions": [{"phase": "populate-passive-jail-rootfs", "status": "failed"}]}
    snapshot = {"status": "active", "stages": {}}
    require_pre_promotion(receipt, snapshot)
    if boundary.startswith("irreversible"):
        receipt[boundary] = {}
    elif boundary == "stage":
        receipt["transitions"][-1]["phase"] = "apply-declarative-release"
    elif boundary == "sealed":
        snapshot["status"] = "sealed"
    else:
        snapshot["stages"]["rootfs-nsight-customization"] = {}
    with pytest.raises(RuntimeError):
        require_pre_promotion(receipt, snapshot)


def test_recovery_cli_is_registered_and_requires_exact_selection():
    result = CliRunner().invoke(cli.app, ["soperator", "profiling", "recover", "--help"])
    assert result.exit_code == 0
    rendered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
    assert all(flag in rendered for flag in ("--target", "--stage", "--job-uid", "--dry-run"))


def test_large_package_inventory_and_max_attempts_fit_linux_and_configmap(tmp_path):
    from nebius_cxcli.nsight_profiling import customize_generation
    from test_nsight_profiling import package_receipts, profiling_config

    admission, verified = package_receipts()
    admission["baseline"] = {
        f"library-{index}:amd64": {
            "package": f"library-{index}",
            "architecture": "amd64",
            "version": f"1.{index}.0-1ubuntu1",
            "status": "install ok installed",
            "pending": "",
            "awaited": "",
        }
        for index in range(2000)
    }
    verified["admissionSha256"] = digest(admission)
    runner = _JournalRunner()
    owner = _journal(runner, tmp_path / "journal.json")
    owner.establish()
    owner.begin_stage(
        name="pristine-inventory", intent={"digest": digest("pristine"), "padding": "x" * 100_000}
    )
    stages = recovery.RootfsStages(owner)

    def execute(name, job):
        assert (
            len(job["spec"]["template"]["spec"]["containers"][0]["command"][2].encode())
            < 120 * 1024
        )
        chain = recovery.initial_chain(
            bind_protected_job_authority(
                job, operation_id=digest("operation"), fence_epoch=1, pvc_uid="pvc"
            )
        )
        stages.checkpoint(name, chain)
        for attempt in range(3):
            row = chain["attempts"][-1]
            row.update(jobUid=f"{name}-{attempt}", workloadSha256=digest(row["manifest"]))
            if attempt == 2:
                row.update(complete=True, result=admission if name == "nsight-admit" else verified)
            stages.checkpoint(name, chain)
            if attempt < 2:
                proof = {
                    "jobUid": row["jobUid"],
                    "workloadSha256": row["workloadSha256"],
                    "podUid": "pod",
                    "podIdentitySha256": digest("pod"),
                    "terminated": {"container": "done"},
                }
                proof["proofSha256"] = digest(proof)
                chain = recovery.reserve_successor(
                    chain, predecessor_uid=row["jobUid"], epoch=attempt + 2, proof=proof
                )
                stages.checkpoint(name, chain)
        return chain["attempts"][-1]["result"]

    receipt = customize_generation(
        config=profiling_config(),
        target_ref="cluster",
        image="example/jail@sha256:" + "a" * 64,
        pvc="passive",
        pvc_uid="pvc",
        pv_uid="pv",
        filesystem_id="fs",
        generation=digest("operation"),
        run_job=execute,
    )
    receipt = recovery.bind_attempt_receipt(receipt, stages)
    owner.begin_stage(
        name="rootfs-nsight-customization", intent={"receiptSha256": receipt["receiptSha256"]}
    )
    owner.complete_stage(name="rootfs-nsight-customization", evidence=receipt)
    assert len(runner.resource["data"]["journal.json"].encode()) < 900 * 1024
    assert owner.stage("rootfs-nsight-customization")["evidence"] == receipt


def test_nsight_capacity_is_reserved_before_first_job(tmp_path):
    owner = _journal(_JournalRunner(), tmp_path / "journal.json")
    owner.establish()
    owner.begin_stage(name="pristine-inventory", intent={"padding": "x" * 700_000})
    with pytest.raises(RuntimeError, match="reserved Nsight"):
        recovery.RootfsStages(owner).checkpoint("nsight-admit", recovery.initial_chain(manifest()))
    assert owner.stage("rootfs-nsight-admit") is None
