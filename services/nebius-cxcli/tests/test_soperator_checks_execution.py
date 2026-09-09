"""Exercise the actual checks executor with native-shaped Kubernetes and Slurm evidence."""

from __future__ import annotations

import copy
import json
import re
import shlex
from dataclasses import replace

import pytest

from nebius_cxcli.soperator_checks import SoperatorChecksExecution
from nebius_cxcli.soperator_checks_policy import CHECKS_POLICY_ENV, CheckRule, SoperatorChecksPolicy


class Cluster:
    def __init__(self, policy):
        self.policy = policy
        self.jobs = {}
        self.pods = {}
        self.checks = {}
        self.crons = {}
        self.configmaps = {}
        self.writes = []
        self.accounting = {}
        self.users = "root"
        self.reservation_present = True
        self.reservation_fields = {}
        self.nodes = {"cpu-0": (8, 0), "gpu-0": (16, 4), "gpu-1": (32, 8)}
        self.running = ""
        self.next_id = 40
        self.now = 0
        self.fail_create = False
        self.job_terminal = True
        self.sacct_state = "COMPLETED"
        self.native_health_status = "PASS"
        self.drop_gpus = False
        self.crd = {}
        self.releases = []
        for rule in policy.rules:
            self.checks[rule.name] = {
                "metadata": {
                    "name": rule.name,
                    "uid": "check-" + rule.name,
                    "generation": 2,
                    "resourceVersion": "2",
                },
                "spec": {
                    **copy.deepcopy(policy.execution_specs[rule.name]),
                    "suspend": True,
                    "runAfterCreation": rule.bootstrap and rule.required,
                },
                "status": {},
            }
            base = copy.deepcopy(
                policy.execution_specs[rule.name][rule.check_type + "Spec"]["jobContainer"]
            )
            container = {"name": rule.name, **base}
            volumes = []
            if rule.check_type == "slurmJob":
                container["env"].extend(
                    [
                        {"name": "ACTIVE_CHECK_NAME", "value": rule.name},
                        {"name": "EACH_WORKER_JOBS", "value": "true"},
                        {"name": "ACTIVE_CHECK_MAX_NUMBER_OF_JOBS", "value": "2"},
                    ]
                )
                container["volumeMounts"] = [
                    {"name": "slurm-configs", "mountPath": "/mnt/slurm-configs", "readOnly": True},
                    {"name": "munge-key", "mountPath": "/mnt/munge-key", "readOnly": True},
                    {"name": "munge-socket", "mountPath": "/run/munge"},
                    {
                        "name": "sbatch-volume",
                        "mountPath": "/opt/bin/sbatch.sh",
                        "subPath": "sbatch.sh",
                        "readOnly": True,
                    },
                ]
                volumes = [
                    {"name": "slurm-configs", "configMap": {"name": "cluster-slurm-configs"}},
                    {
                        "name": "munge-key",
                        "secret": {
                            "secretName": "cluster-munge",
                            "defaultMode": 272,
                            "items": [{"key": "munge.key", "path": "munge.key", "mode": 256}],
                        },
                    },
                    {"name": "munge-socket", "emptyDir": {}},
                    {
                        "name": "sbatch-volume",
                        "configMap": {
                            "name": "sbatch-script-" + rule.name,
                            "items": [{"key": "sbatch.sh", "path": "sbatch.sh", "mode": 493}],
                        },
                    },
                ]
                self.configmaps["sbatch-script-" + rule.name] = {
                    "metadata": {"uid": "script-" + rule.name},
                    "data": {
                        "sbatch.sh": policy.execution_specs[rule.name]["slurmJobSpec"][
                            "sbatchScript"
                        ]
                    },
                }
            self.crons[rule.name] = {
                "metadata": {
                    "name": rule.name,
                    "uid": "cron-" + rule.name,
                    "ownerReferences": [
                        {
                            "kind": "SlurmCluster",
                            "name": "cluster",
                            "uid": "cluster-uid",
                            "controller": True,
                        }
                    ],
                    "labels": {"component": "soperatorchecks"},
                },
                "spec": {
                    "suspend": True,
                    "schedule": policy.execution_specs[rule.name]["schedule"],
                    "jobTemplate": {
                        "metadata": {},
                        "spec": {
                            "template": {
                                "metadata": {
                                    "annotations": {"slurm.nebius.ai/active-check-name": rule.name}
                                },
                                "spec": {
                                    "containers": [container],
                                    "volumes": volumes,
                                    "serviceAccountName": "cluster-activecheck-sa",
                                },
                            }
                        },
                    },
                },
            }

    def kube(self, args, document):
        verb = args[0]
        if verb == "create" and document.get("kind") == "List":
            return {"items": [self.kube(args, item) for item in document["items"]]}
        if verb == "create":
            self.writes.append(("create", copy.deepcopy(document)))
            if self.fail_create:
                raise RuntimeError("transport lost after submission intent")
            job = copy.deepcopy(document)
            name = job["metadata"]["name"]
            assert name not in self.jobs
            job["metadata"]["uid"] = "job-" + name
            job["status"] = (
                {"conditions": [{"type": "Complete", "status": "True"}]}
                if self.job_terminal
                else {}
            )
            check_name = job["metadata"]["annotations"]["cxcli.nebius.ai/check"]
            rule = next(rule for rule in self.policy.rules if rule.name == check_name)
            if rule.check_type == "slurmJob":
                self.next_id += 1
                job_id = str(self.next_id)
                job["metadata"]["annotations"]["slurm-job-id"] = job_id
                env = {
                    item["name"]: item["value"]
                    for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
                }
                gpu = 1 if self.drop_gpus else int(env.get("SBATCH_GPUS_PER_NODE", "0"))
                # Slurm 25.11 does not consume SBATCH_NODELIST or SBATCH_NODES.
                # Model its script directives, with an unconstrained scheduler
                # deliberately choosing a different node when no pin exists.
                script = (
                    job["spec"]["template"]["metadata"]
                    .get("annotations", {})
                    .get("cxcli.nebius.ai/acceptance-sbatch", "")
                )
                pins = re.findall(r"^#SBATCH --nodelist=(\S+)$", script, re.MULTILINE)
                node = pins[-1] if pins else "gpu-1"
                self.accounting[job_id] = (
                    f"{job_id}|{self.sacct_state}|0:0|{node}|soperatorchecks|{name}|reserve|cpu=8,gres/gpu={gpu}"
                )
                # Real upstream slurmJob never sets k8sJobsStatus.
                self.checks[check_name]["status"] = {
                    "slurmJobsStatus": {
                        "lastRunId": self.next_id,
                        "lastRunName": name,
                        "lastRunStatus": "Complete",
                        "lastTransitionTime": "now",
                    }
                }
            else:
                self.checks[check_name]["status"] = {
                    "k8sJobsStatus": {
                        "lastJobName": name,
                        "lastJobStatus": "Complete",
                        "lastTransitionTime": "now",
                    }
                }
            self.jobs[name] = job
            return copy.deepcopy(job)
        if verb == "patch":
            kind, name = args[1:3]
            item = (
                self.checks[name]
                if kind == "activecheck"
                else self.crons[name]
                if kind == "cronjob"
                else next(row for row in self.releases if row["metadata"]["name"] == name)
            )
            patch = json.loads(args[args.index("-p") + 1])
            assert patch["metadata"]["uid"] == item["metadata"]["uid"]
            assert patch["metadata"]["resourceVersion"] == item["metadata"]["resourceVersion"]
            item["spec"].update(patch["spec"])
            self.writes.append(("patch", name))
            if kind == "activecheck":
                self.crons[name]["spec"]["suspend"] = patch["spec"]["suspend"]
            return copy.deepcopy(item)
        assert verb == "get", args
        kind = args[1]
        name = args[2] if len(args) > 2 and not args[2].startswith("-") else ""
        if kind == "slurmcluster":
            return {"metadata": {"name": "cluster", "uid": "cluster-uid"}}
        if kind == "crd":
            return self.crd
        if kind == "helmreleases.helm.toolkit.fluxcd.io":
            return {"items": self.releases}
        if kind == "helmrelease":
            return copy.deepcopy(
                next((row for row in self.releases if row["metadata"]["name"] == name), {})
            )
        resources = {
            "configmap": self.configmaps,
            "activecheck": self.checks,
            "activechecks.slurm.nebius.ai": self.checks,
            "cronjob": self.crons,
            "job": self.jobs,
            "jobs": self.jobs,
            "pods": self.pods,
        }[kind]
        return (
            copy.deepcopy(resources.get(name, {}))
            if name
            else {"items": copy.deepcopy(list(resources.values()))}
        )

    def slurm(self, command):
        command = command.removeprefix("env SLURM_TIME_FORMAT=standard TZ=UTC ")
        if command.startswith("head -c 4194305 -- /opt/soperator-outputs/slurm_jobs/"):
            status = self.native_health_status
            report = {
                "meta": {"run_id": "native-run", "timestamp": 1},
                "status": status,
                "tests": [
                    {
                        "name": "gpu_fryer",
                        "enable": True,
                        "state": {"code": 0 if status == "PASS" else 1, "error": ""},
                        "checks": [{"enable": True, "state": {"status": status, "error": ""}}],
                    }
                ],
            }
            return (
                "Health checker output:\n"
                + json.dumps(report)
                + f"\nHealth checker status: {status}\n"
            )
        if command == "scontrol show reservations -o":
            return "ReservationName=reserve" if self.reservation_present else ""
        if command == "scontrol delete ReservationName=reserve":
            self.writes.append(("delete-reservation", command))
            self.reservation_present = False
            return ""
        if command.startswith("scontrol show reservation "):
            if not self.reservation_present:
                return ""
            fields = dict(
                ReservationName="reserve",
                StartTime="2026-01-01T00:00:00",
                EndTime="2027-01-01T00:00:00",
                Duration="365-00:00:00",
                State="ACTIVE",
                Nodes=",".join(self.nodes),
                NodeCnt=str(len(self.nodes)),
                CoreCnt=str(sum(row[0] for row in self.nodes.values())),
                Flags="MAINT,IGNORE_JOBS,SPEC_NODES",
                Users=self.users,
                Accounts="(null)",
            )
            fields.update(self.reservation_fields)
            return " ".join(f"{key}={value}" for key, value in fields.items())
        if command == "scontrol show nodes -o":
            return "\n".join(
                f"NodeName={name} CoresPerSocket={cores} Sockets=1 CfgTRES=cpu={cores},gres/gpu={gpus}"
                for name, (cores, gpus) in self.nodes.items()
            )
        if command.startswith("scontrol show hostnames "):
            return "\n".join(shlex.split(command)[-1].split(","))
        if command.startswith("scontrol update ReservationName=reserve Users="):
            self.writes.append(("reservation", command))
            self.users = command.split("Users=")[1]
            return ""
        if command == "id -u soperatorchecks":
            return "1001"
        if command.startswith("squeue "):
            return self.running
        if command.startswith("sacct "):
            return "\n".join(
                self.accounting[job_id] for job_id in shlex.split(command)[4].split(",")
            )
        raise AssertionError(command)

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def policy():
    rules = (
        CheckRule(
            "create-user-soperatorchecks", "k8sJob", True, True, True, (), False, 1, "source"
        ),
        CheckRule(
            "wait-for-soperatorchecks-srun-ready",
            "k8sJob",
            False,
            True,
            True,
            ("create-user-soperatorchecks",),
            False,
            1,
            "source",
        ),
        CheckRule(
            "gpu-fryer",
            "slurmJob",
            False,
            True,
            False,
            ("wait-for-soperatorchecks-srun-ready",),
            True,
            2,
            "source",
            True,
        ),
    )
    policy = SoperatorChecksPolicy(
        "source",
        "values",
        rules,
        {},
        passive={
            "supported": False,
            "opaque": {"checks.json": "[]", "check_runner.py": "# native fixture"},
        },
        partitions={
            "configType": "structured",
            "partitions": [
                {"name": "gpu", "config": "State=UP"},
                {"name": "hidden", "config": "Hidden=YES State=UP"},
            ],
        },
    )
    specs = {}
    for rule in rules:
        spec = {
            "jobContainer": {
                "image": "upstream/check:4.1.7",
                "command": ["bash", "-c", "upstream-diagnostic"],
                "env": [{"name": CHECKS_POLICY_ENV, "value": policy.sha256}],
            }
        }
        if rule.check_type == "slurmJob":
            spec.update(
                sbatchScript="#!/bin/bash\nsrun gpu-fryer\n", eachWorkerJobs=True, maxNumberOfJobs=2
            )
        specs[rule.name] = {
            "name": rule.name,
            "slurmClusterRefName": "cluster",
            "checkType": rule.check_type,
            "schedule": "0 */6 * * *",
            rule.check_type + "Spec": spec,
        }
    return replace(policy, execution_specs=specs)


def execution(tmp_path, policy, cluster):
    return SoperatorChecksExecution(
        policy=policy,
        operation_id="test-operation",
        receipt_path=tmp_path / "checks.json",
        kubernetes=cluster.kube,
        slurm=cluster.slurm,
        assert_authority=lambda: None,
        timeout_seconds=2,
        poll_seconds=1,
        clock=lambda: cluster.now,
        sleep=cluster.sleep,
    )


def accept(executor):
    executor.prepare_reservation("reserve", installing=False)
    return executor.accept(
        reservation="reserve", workers=("cpu-0", "gpu-0", "gpu-1"), gpu_workers=("gpu-0", "gpu-1")
    )


def test_native_health_error_cannot_pass_with_successful_slurm_accounting(tmp_path, policy):
    cluster = Cluster(policy)
    cluster.native_health_status = "ERROR"
    executor = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError, match="health.*ERROR"):
        accept(executor)
    assert executor.state["phase"] != "accepted"


def test_accepted_native_health_output_is_reverified(tmp_path, policy):
    cluster = Cluster(policy)
    executor = execution(tmp_path, policy, cluster)
    accept(executor)
    cluster.native_health_status = "ERROR"
    with pytest.raises(RuntimeError, match="health.*ERROR"):
        executor.verify_acceptance()


def failed_unpinned_acceptance(tmp_path, policy, monkeypatch):
    import nebius_cxcli.soperator_checks as checks
    from nebius_cxcli.soperator_checks_allocation import SCRIPT_ANNOTATION
    from nebius_cxcli.soperator_checks_policy import checks_digest

    original = checks.native_acceptance_job

    def obsolete(cron, **kwargs):
        job = original(cron, **kwargs)
        if kwargs["rule"].check_type != "slurmJob":
            return job
        pod = job["spec"]["template"]
        del pod["metadata"]["annotations"][SCRIPT_ANNOTATION]
        volumes = pod["spec"]["volumes"]
        volumes[volumes.index(next(v for v in volumes if v["name"] == "sbatch-volume"))] = (
            copy.deepcopy(
                next(
                    v
                    for v in cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
                    if v["name"] == "sbatch-volume"
                )
            )
        )
        if not kwargs.get("job_name"):
            job["metadata"]["name"] = (
                "cxcli-check-"
                + checks_digest(
                    [kwargs["operation_id"], kwargs["rule"].name, kwargs["worker"]]
                ).split(":")[1][:24]
            )
        env = pod["spec"]["containers"][0]["env"]
        for item in env:
            if item["name"] == "ACTIVE_CHECK_NAME":
                item["value"] = job["metadata"]["name"]
        index = next(
            (i for i, e in enumerate(env) if e["name"] == "SBATCH_GPUS_PER_NODE"), len(env)
        )
        env[index:index] = [
            {"name": "SBATCH_NODELIST", "value": kwargs["worker"]},
            {"name": "SBATCH_NODES", "value": "1"},
        ]
        return job

    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    with monkeypatch.context() as patch:
        patch.setattr(checks, "native_acceptance_job", obsolete)
        with pytest.raises(RuntimeError, match="worker coverage"):
            accept(runner)
    return cluster, runner


def test_wrong_worker_jobs_are_retained_and_replaced_with_fresh_pinned_jobs(
    tmp_path, policy, monkeypatch
):
    cluster, runner = failed_unpinned_acceptance(tmp_path, policy, monkeypatch)
    prior = copy.deepcopy(cluster.jobs)
    entries = copy.deepcopy(runner.state["jobs"])
    resumed = execution(tmp_path, policy, cluster)
    assert accept(resumed)["jobs"] == 4
    retired = resumed.state["allocationRetirements"]
    assert len(retired) == 2
    for name, proof in retired.items():
        assert proof["entry"] == entries[name]
        assert cluster.jobs[name] == prior[name]
        assert name not in resumed.state["jobs"]
    assert len(cluster.jobs) == len(prior) + 2
    assert {
        e["worker"]: e["slurmResult"]["nodes"]
        for e in resumed.state["jobs"].values()
        if e["worker"]
    } == {"gpu-0": ["gpu-0"], "gpu-1": ["gpu-1"]}
    writes = copy.deepcopy(cluster.writes)
    assert execution(tmp_path, policy, cluster).verify_acceptance()["jobs"] == 4
    assert writes == cluster.writes


@pytest.mark.parametrize(
    "drift",
    ["uid", "executable", "epoch", "running", "missing-id", "wrong-reservation", "partial-gpu"],
)
def test_allocation_retirement_rejects_unproven_or_changed_work(
    tmp_path, policy, monkeypatch, drift
):
    cluster, runner = failed_unpinned_acceptance(tmp_path, policy, monkeypatch)
    name = "gpu-fryer-initial-run"
    job = cluster.jobs[name]
    entry = runner.state["jobs"][name]
    job_id = job["metadata"]["annotations"]["slurm-job-id"]
    if drift == "uid":
        job["metadata"]["uid"] = "replacement"
    elif drift == "executable":
        job["spec"]["template"]["spec"]["containers"][0]["image"] = "unreviewed"
    elif drift == "epoch":
        entry["epoch"]["generation"] += 1
        runner._save()
    elif drift == "running":
        cluster.accounting[job_id] = cluster.accounting[job_id].replace("|COMPLETED|", "|RUNNING|")
    elif drift == "missing-id":
        del job["metadata"]["annotations"]["slurm-job-id"]
    elif drift == "wrong-reservation":
        cluster.accounting[job_id] = cluster.accounting[job_id].replace("|reserve|", "|foreign|")
    else:
        cluster.accounting[job_id] = cluster.accounting[job_id].replace("gres/gpu=4", "gres/gpu=1")
    prior_names = set(cluster.jobs)
    with pytest.raises(RuntimeError):
        accept(execution(tmp_path, policy, cluster))
    state = execution(tmp_path, policy, cluster).state
    assert not state.get("allocationRetirements")
    assert set(cluster.jobs) == prior_names


def test_interrupt_after_atomic_retirement_resumes_without_reusing_old_jobs(
    tmp_path, policy, monkeypatch
):
    cluster, runner = failed_unpinned_acceptance(tmp_path, policy, monkeypatch)
    resumed = execution(tmp_path, policy, cluster)
    save = resumed._save

    def interrupt():
        save()
        if resumed.state.get("allocationRetirements"):
            raise RuntimeError("interrupted after retirement")

    resumed._save = interrupt
    with pytest.raises(RuntimeError, match="interrupted after retirement"):
        accept(resumed)
    assert len(cluster.jobs) == 4
    final = execution(tmp_path, policy, cluster)
    assert accept(final)["jobs"] == 4
    assert len(final.state["allocationRetirements"]) == 2
    assert len(cluster.jobs) == 6


def test_native_acceptance_covers_all_gpus_consumes_initial_trigger_and_resumes(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    assert accept(runner)["jobs"] == 4
    assert "gpu-fryer-initial-run" in cluster.jobs
    allocations = {
        entry["worker"]: entry["slurmResult"]["allocatedGpus"]
        for entry in runner.state["jobs"].values()
        if entry["worker"]
    }
    assert allocations == {"gpu-0": 4, "gpu-1": 8}
    assert "k8sJobsStatus" not in cluster.checks["gpu-fryer"]["status"]
    runner.close_authorization()
    assert cluster.users == "root"
    writes = copy.deepcopy(cluster.writes)
    resumed = execution(tmp_path, policy, cluster)
    assert resumed.verify_acceptance()["jobs"] == 4
    assert (
        resumed.accept(
            reservation="reserve", workers=tuple(cluster.nodes), gpu_workers=("gpu-0", "gpu-1")
        )["jobs"]
        == 4
    )
    assert cluster.writes == writes


def test_acceptance_resumes_owned_probe_after_authorization_before_jobs(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    runner.prepare_reservation("reserve", installing=False)
    calls = []

    def resume():
        assert cluster.users == "root,soperatorchecks"
        assert cluster.jobs == {}
        calls.append("resume")

    args = {
        "reservation": "reserve",
        "workers": ("cpu-0", "gpu-0", "gpu-1"),
        "gpu_workers": ("gpu-0", "gpu-1"),
        "before_jobs": resume,
    }
    assert runner.accept(**args)["jobs"] == 4
    assert runner.accept(**args)["jobs"] == 4
    assert calls == ["resume"]


@pytest.mark.parametrize(
    "fields",
    [
        {"State": "INACTIVE"},
        {"Duration": "00:01:00"},
        {"Duration": "UNLIMITED"},
        {"Duration": "364-00:00:00"},
        {"CoreCnt": "1"},
        {"Accounts": "customer"},
        {"Flags": "MAINT,IGNORE_JOBS,FLEX"},
        {"NodeCnt": "2"},
        {"PartitionName": "hidden"},
    ],
)
def test_ineffective_reservation_fails_before_any_write(tmp_path, policy, fields):
    cluster = Cluster(policy)
    cluster.reservation_fields = fields
    with pytest.raises(RuntimeError):
        accept(execution(tmp_path, policy, cluster))
    assert cluster.writes == []


def test_reservation_contract_drift_is_not_rebound_on_resume(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    runner.prepare_reservation("reserve", installing=False)
    cluster.reservation_fields["StartTime"] = "2026-02-01T00:00:00"
    with pytest.raises(RuntimeError, match="contract changed"):
        execution(tmp_path, policy, cluster).prepare_reservation("reserve", installing=False)
    assert cluster.writes == []


@pytest.mark.parametrize(
    "failure", ["cancelled", "partial-gpus", "missing-accounting", "pending-k8s"]
)
def test_unsuccessful_or_partial_checks_cannot_reach_handoff(tmp_path, policy, failure):
    cluster = Cluster(policy)
    cluster.sacct_state = "CANCELLED" if failure == "cancelled" else "COMPLETED"
    cluster.drop_gpus = failure == "partial-gpus"
    cluster.job_terminal = failure != "pending-k8s"
    if failure == "missing-accounting":
        original = cluster.slurm
        cluster.slurm = lambda command: "" if command.startswith("sacct ") else original(command)
    runner = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError):
        accept(runner)
    assert runner.state["phase"] != "accepted"
    with pytest.raises(RuntimeError):
        runner.close_authorization()


def test_uncertain_job_create_is_never_blindly_repeated(tmp_path, policy):
    cluster = Cluster(policy)
    cluster.fail_create = True
    with pytest.raises(RuntimeError, match="transport lost"):
        accept(execution(tmp_path, policy, cluster))
    cluster.fail_create = False
    with pytest.raises(RuntimeError, match="submitted acceptance Job is missing"):
        accept(execution(tmp_path, policy, cluster))
    assert sum(verb == "create" for verb, _ in cluster.writes) == 1


def test_replaced_acceptance_job_and_lost_initial_guard_fail_reproof(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    accept(runner)
    del cluster.jobs["gpu-fryer-initial-run"]
    with pytest.raises(RuntimeError, match="identity"):
        runner.verify_acceptance()


def test_stale_cron_template_cannot_launch_target_acceptance(tmp_path, policy):
    cluster = Cluster(policy)
    cron = cluster.crons["create-user-soperatorchecks"]
    cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"] = []
    with pytest.raises(RuntimeError, match="template convergence"):
        accept(execution(tmp_path, policy, cluster))
    assert not any(verb == "create" for verb, _ in cluster.writes)


@pytest.mark.parametrize("auxiliary", [False, True])
def test_unscheduled_source_job_prevents_quiescence(tmp_path, policy, auxiliary):
    if auxiliary:
        policy = replace(policy, auxiliary_pvc="active-jail")
    cluster = Cluster(policy)
    cluster.crd = {"metadata": {"uid": "api"}}
    cluster.releases = [
        {
            "metadata": {
                "name": "checks",
                "namespace": "flux-system",
                "uid": "hr",
                "resourceVersion": "1",
                "generation": 1,
            },
            "status": {"observedGeneration": 1},
            "spec": {"releaseName": "checks", "targetNamespace": "soperator"},
        }
    ]
    for check in cluster.checks.values():
        check["metadata"]["annotations"] = {
            "meta.helm.sh/release-name": "checks",
            "meta.helm.sh/release-namespace": "soperator",
        }
    if auxiliary:
        cluster.crons["run-extensive-check-on-reservations"] = {
            "metadata": {
                "name": "run-extensive-check-on-reservations",
                "uid": "aux-uid",
                "resourceVersion": "1",
                "annotations": copy.deepcopy(check["metadata"]["annotations"]),
            },
            "spec": {"suspend": False},
        }
    cluster.jobs["pending"] = {
        "metadata": {
            "name": "pending",
            "uid": "pending",
            "ownerReferences": [
                {
                    "kind": "CronJob",
                    "name": "run-extensive-check-on-reservations" if auxiliary else "gpu-fryer",
                }
            ],
        },
        "status": {},
    }
    runner = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError, match="in-flight upstream checks"):
        runner.quiesce_source()
    assert not runner.state.get("sourceQuiesced")
    assert cluster.releases[0]["spec"]["suspend"] is True
    if auxiliary:
        assert cluster.crons["run-extensive-check-on-reservations"]["spec"]["suspend"] is True
        assert runner.state["sourceAuxiliary"]["uid"] == "aux-uid"


def test_source_api_absence_is_not_an_execution_error(tmp_path, policy):
    cluster = Cluster(policy)
    assert execution(tmp_path, policy, cluster).quiesce_source() == {"status": "not-required"}
    assert cluster.writes == []


@pytest.mark.parametrize("surface", ["cron-command", "created-image", "script", "epoch"])
def test_execution_substitution_retaining_policy_marker_is_rejected(tmp_path, policy, surface):
    cluster = Cluster(policy)
    if surface == "cron-command":
        cluster.crons["create-user-soperatorchecks"]["spec"]["jobTemplate"]["spec"]["template"][
            "spec"
        ]["containers"][0]["command"] = ["true"]
    elif surface == "script":
        cluster.configmaps["sbatch-script-gpu-fryer"]["data"]["sbatch.sh"] = "exit 0"
    else:
        native = cluster.kube

        def changed(args, document):
            result = native(args, document)
            if args[0] == "create" and document.get("kind") == "Job":
                if surface == "created-image":
                    cluster.jobs[document["metadata"]["name"]]["spec"]["template"]["spec"][
                        "containers"
                    ][0]["image"] = "unreviewed/noop"
                else:
                    cluster.checks[document["metadata"]["annotations"]["cxcli.nebius.ai/check"]][
                        "metadata"
                    ]["generation"] += 1
            return result

        cluster.kube = changed
    runner = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError, match="executable|script|epoch"):
        accept(runner)
    assert runner.state.get("phase") != "accepted"


@pytest.mark.parametrize("surface", ["job", "script", "check"])
def test_completed_acceptance_reproves_executable_authority(tmp_path, policy, surface):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    accept(runner)
    if surface == "job":
        next(iter(cluster.jobs.values()))["spec"]["template"]["spec"]["containers"][0][
            "command"
        ] = ["true"]
    elif surface == "script":
        cluster.configmaps["sbatch-script-gpu-fryer"]["data"]["sbatch.sh"] = "true"
    else:
        cluster.checks["gpu-fryer"]["spec"]["slurmJobSpec"]["jobContainer"]["image"] = "unreviewed"
    with pytest.raises(RuntimeError, match="executable|script|execution"):
        runner.verify_acceptance()


def test_check_added_while_source_writer_finishes_blocks_quiescence(tmp_path, policy):
    cluster = Cluster(policy)
    cluster.crd = {"metadata": {"uid": "api"}}
    cluster.releases = [
        {
            "metadata": {
                "name": "checks",
                "namespace": "flux-system",
                "uid": "hr",
                "resourceVersion": "1",
                "generation": 1,
            },
            "status": {"observedGeneration": 1},
            "spec": {"releaseName": "checks", "targetNamespace": "soperator"},
        }
    ]
    for check in cluster.checks.values():
        check["metadata"]["annotations"] = {
            "meta.helm.sh/release-name": "checks",
            "meta.helm.sh/release-namespace": "soperator",
        }
    native = cluster.kube

    def changed(args, document):
        result = native(args, document)
        if args[:2] == ["patch", "helmrelease"]:
            extra = copy.deepcopy(cluster.checks["gpu-fryer"])
            extra["metadata"].update(name="late-check", uid="late-check")
            extra["spec"]["suspend"] = False
            cluster.checks["late-check"] = extra
        return result

    cluster.kube = changed
    runner = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError, match="inventory changed"):
        runner.quiesce_source()
    assert not runner.state.get("sourceQuiesced")


@pytest.mark.parametrize("observed", [None, 1])
def test_source_writer_requires_suspension_generation_ack(tmp_path, policy, observed):
    cluster = Cluster(policy)
    cluster.crd = {"metadata": {"uid": "api"}}
    cluster.releases = [
        {
            "metadata": {
                "name": "checks",
                "namespace": "flux-system",
                "uid": "hr",
                "generation": 2,
                "resourceVersion": "2",
            },
            "spec": {"releaseName": "checks", "targetNamespace": "soperator"},
            "status": {"observedGeneration": observed},
        }
    ]
    for check in cluster.checks.values():
        check["metadata"]["annotations"] = {
            "meta.helm.sh/release-name": "checks",
            "meta.helm.sh/release-namespace": "soperator",
        }
    runner = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError, match="declarative writers to quiesce"):
        runner.quiesce_source()
    assert not runner.state.get("sourceQuiesced")
    assert all(args[:2] != ["patch", "activecheck"] for args, _ in cluster.writes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("env", [{"name": "BASH_ENV", "value": "/tmp/injected"}]),
        ("args", ["injected"]),
        ("volumeMounts", []),
    ],
)
def test_native_munge_execution_cannot_be_substituted(policy, field, value):
    from nebius_cxcli.soperator_checks_contract import verify_native_template

    cluster = Cluster(policy)
    check = cluster.checks["gpu-fryer"]
    expected = copy.deepcopy(policy.execution_specs["gpu-fryer"])
    expected["slurmJobSpec"]["mungeContainer"] = {"image": "upstream/munge:4.1.7"}
    check["spec"]["slurmJobSpec"]["mungeContainer"] = copy.deepcopy(
        expected["slurmJobSpec"]["mungeContainer"]
    )
    template = cluster.crons["gpu-fryer"]["spec"]["jobTemplate"]["spec"]["template"]
    init = {
        "name": "munge",
        "image": "upstream/munge:4.1.7",
        "restartPolicy": "Always",
        "volumeMounts": [
            {"name": "munge-key", "mountPath": "/mnt/munge-key", "readOnly": True},
            {"name": "munge-socket", "mountPath": "/run/munge"},
        ],
    }
    template["spec"]["initContainers"] = [init]
    verify_native_template(expected, check, template)
    init[field] = value
    with pytest.raises(RuntimeError, match="munge"):
        verify_native_template(expected, check, template)


@pytest.mark.parametrize(
    "mutation",
    [None, "secret-old-name", "secret-foreign", "key-mode", "key-path", "mount-writable"],
)
def test_native_k8s_munge_template_uses_upstream_secret_identity(mutation):
    from nebius_cxcli.soperator_checks_contract import verify_native_template

    jail_mount = {"name": "jail", "mountPath": "/mnt/jail"}
    jail = {"name": "jail", "persistentVolumeClaim": {"claimName": "active-jail"}}
    expected = {
        "name": "probe",
        "slurmClusterRefName": "lab",
        "checkType": "k8sJob",
        "k8sJobSpec": {
            "jobContainer": {
                "image": "upstream/check",
                "command": ["bash", "-c", "native-script"],
                "volumeMounts": [jail_mount],
                "volumes": [jail],
            },
            "mungeContainer": {"image": "upstream/munge", "command": ["munged", "--foreground"]},
        },
    }
    secret = {
        "secretName": "lab-munge",
        "defaultMode": 272,
        "items": [{"key": "munge.key", "path": "munge.key", "mode": 256}],
    }
    key_mount = {"name": "munge-key", "mountPath": "/mnt/munge-key", "readOnly": True}
    socket = {"name": "munge-socket", "mountPath": "/run/munge"}
    pod = {
        "serviceAccountName": "lab-activecheck-sa",
        "containers": [
            {
                "name": "probe",
                "image": "upstream/check",
                "command": ["bash", "-c", "native-script"],
                "volumeMounts": [
                    jail_mount,
                    {"name": "slurm-configs", "mountPath": "/mnt/slurm-configs", "readOnly": True},
                    socket,
                ],
            }
        ],
        "initContainers": [
            {
                "name": "munge",
                "image": "upstream/munge",
                "command": ["munged", "--foreground"],
                "restartPolicy": "Always",
                "volumeMounts": [key_mount, socket],
            }
        ],
        "volumes": [
            jail,
            {
                "name": "slurm-configs",
                "configMap": {"name": "lab-slurm-configs", "defaultMode": 420},
            },
            {"name": "munge-key", "secret": secret},
            {"name": "munge-socket", "emptyDir": {}},
        ],
    }
    if mutation == "secret-old-name":
        secret["secretName"] = "lab-munge-key"
    elif mutation == "secret-foreign":
        secret["secretName"] = "foreign-munge"
    elif mutation == "key-mode":
        secret["items"][0]["mode"] = 420
    elif mutation == "key-path":
        secret["items"][0]["path"] = "other"
    elif mutation == "mount-writable":
        key_mount["readOnly"] = False
    if mutation:
        with pytest.raises(RuntimeError, match="native check"):
            verify_native_template(expected, {"spec": expected}, {"spec": pod})
    else:
        assert verify_native_template(expected, {"spec": expected}, {"spec": pod}) is None


@pytest.mark.parametrize("drift", [None, "owner", "executable", "diagnostic"])
def test_only_native_bootstrap_work_may_overlap_maintenance(tmp_path, policy, drift):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    name = "gpu-fryer" if drift == "diagnostic" else "create-user-soperatorchecks"
    cron = cluster.crons[name]
    job = copy.deepcopy(cron["spec"]["jobTemplate"])
    job["metadata"].update(
        name=name + "-initial-run",
        uid="initial-job",
        ownerReferences=[
            {
                "kind": "CronJob",
                "name": name,
                "uid": "foreign" if drift == "owner" else cron["metadata"]["uid"],
                "controller": True,
            }
        ],
    )
    if drift == "executable":
        job["spec"]["template"]["spec"]["containers"][0]["command"] = ["foreign"]
    cluster.jobs[job["metadata"]["name"]] = job
    if drift:
        with pytest.raises(RuntimeError, match="diagnostic|executable"):
            runner.verify_deferred_diagnostics()
    else:
        runner.verify_deferred_diagnostics()
    assert not cluster.writes


@pytest.mark.parametrize("drift", [None, "uid", "executable", "operation"])
def test_deferred_guard_allows_only_receipt_bound_acceptance_resume(tmp_path, policy, drift):
    from nebius_cxcli.soperator_checks import native_acceptance_job
    from nebius_cxcli.soperator_checks_contract import job_execution_digest

    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    rule = policy.rules[0]
    job = native_acceptance_job(
        cluster.crons[rule.name], rule=rule, operation_id=runner.operation_id, reservation="reserve"
    )
    job["metadata"]["uid"] = "owned-job"
    name = job["metadata"]["name"]
    runner.state.update(
        phase="acceptance",
        reservation="reserve",
        jobs={
            name: {
                "uid": "owned-job",
                "execution": job_execution_digest(job),
                "check": rule.name,
                "status": "submitted",
            }
        },
    )
    if drift == "uid":
        job["metadata"]["uid"] = "foreign"
    elif drift == "executable":
        job["spec"]["template"]["spec"]["containers"][0]["command"] = ["foreign"]
    elif drift == "operation":
        job["metadata"]["labels"]["cxcli.nebius.ai/check-operation"] = "foreign"
    cluster.jobs[name] = job
    if drift:
        with pytest.raises(RuntimeError, match="diagnostic"):
            runner.verify_deferred_diagnostics(allow_acceptance=True)
    else:
        runner.verify_deferred_diagnostics(allow_acceptance=True)
    with pytest.raises(RuntimeError, match="diagnostic"):
        runner.verify_deferred_diagnostics()
    assert not cluster.writes


@pytest.mark.parametrize("drift", [None, "active", "terminating", "pod", "unacknowledged", "slurm"])
def test_retained_suspended_diagnostics_require_effective_quiescence(tmp_path, policy, drift):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    job = {
        "metadata": {
            "name": "retained",
            "uid": "retained-job",
            "labels": {"app.kubernetes.io/component": "soperatorchecks"},
        },
        "spec": {"suspend": True},
        "status": {"conditions": [{"type": "Suspended", "status": "True"}]},
    }
    if drift in {"active", "terminating"}:
        job["status"][drift] = 1
    elif drift == "unacknowledged":
        job["status"]["conditions"] = []
    elif drift == "pod":
        cluster.pods["pending"] = {
            "metadata": {
                "ownerReferences": [
                    {"kind": "Job", "name": "retained", "uid": "retained-job", "controller": True}
                ]
            },
            "status": {"phase": "Pending"},
        }
    elif drift == "slurm":
        cluster.running = "123"
    cluster.jobs["retained"] = job
    if drift:
        with pytest.raises(RuntimeError, match="diagnostic"):
            runner.verify_deferred_diagnostics()
    else:
        runner.verify_deferred_diagnostics()
    assert not cluster.writes
