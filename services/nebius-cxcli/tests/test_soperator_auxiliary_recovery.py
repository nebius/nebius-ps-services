import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_checks_auxiliary_recovery import AuxiliaryBindingRecovery
from nebius_cxcli.soperator_checks_binding import AUXILIARY_CRONJOB


def fixture():
    owner = {
        "name": "checks",
        "namespace": "flux-system",
        "uid": "hr",
        "sourceDigest": "sha256:source",
    }
    template = {
        "spec": {
            "parallelism": 1,
            "completions": 1,
            "backoffLimit": 0,
            "template": {
                "spec": {
                    "containers": [
                        {"name": AUXILIARY_CRONJOB, "image": "native", "command": ["native-script"]}
                    ],
                    "initContainers": [{"name": "munge", "image": "native-munge"}],
                    "volumes": [
                        {"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}},
                        {"name": "slurm-configs", "configMap": {"name": "soperator-slurm-configs"}},
                        {"name": "munge-key", "secret": {"secretName": "soperator-munge"}},
                    ],
                }
            },
        }
    }
    rendered = {"spec": {"jobTemplate": copy.deepcopy(template)}}
    template["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = (
        "active-jail"
    )
    cron = {
        "metadata": {
            "name": AUXILIARY_CRONJOB,
            "uid": "cron",
            "annotations": {
                "meta.helm.sh/release-name": "native-checks",
                "meta.helm.sh/release-namespace": "soperator",
            },
        },
        "spec": {"suspend": True, "jobTemplate": copy.deepcopy(template)},
    }
    job = {
        "metadata": {
            "name": "old-auxiliary-job",
            "uid": "job",
            "ownerReferences": [
                {
                    "kind": "CronJob",
                    "name": AUXILIARY_CRONJOB,
                    "uid": "cron",
                    "apiVersion": "batch/v1",
                    "controller": True,
                }
            ],
        },
        **copy.deepcopy(template),
        "status": {"active": 1},
    }
    pod = {
        "metadata": {
            "name": "old-pod",
            "uid": "pod",
            "ownerReferences": [
                {
                    "kind": "Job",
                    "name": "old-auxiliary-job",
                    "uid": "job",
                    "apiVersion": "batch/v1",
                    "controller": True,
                }
            ],
        },
        "spec": copy.deepcopy(template["spec"]["template"]["spec"]),
        "status": {
            "phase": "Pending",
            "containerStatuses": [
                {"name": AUXILIARY_CRONJOB, "state": {"waiting": {"reason": "PodInitializing"}}}
            ],
            "initContainerStatuses": [
                {"name": "munge", "state": {"waiting": {"reason": "PodInitializing"}}}
            ],
        },
    }
    hr = {
        "metadata": {"uid": "hr", "generation": 1},
        "spec": {
            "releaseName": "native-checks",
            "targetNamespace": "soperator",
            "values": {},
            "suspend": False,
        },
        "status": {
            "lastAttemptedRevisionDigest": "sha256:source",
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }
    state = {
        "phase": "accepted",
        "operation": "operation",
        "reservation": "maintenance",
        "reservationFingerprint": "fingerprint",
        "jobs": {"accepted": {"status": "complete"}},
        "catchupRecovery": {
            "status": "accepted",
            "checksRelease": owner,
            "resultSha256": "sha256:fresh",
        },
    }
    calls = []
    events = [
        {"reason": "FailedMount", "message": f'volume "{name}" not found'}
        for name in ("soperator-slurm-configs", "soperator-munge")
    ]
    parent = SimpleNamespace(
        state=state,
        policy=SimpleNamespace(
            sha256="policy",
            auxiliary_pvc="active-jail",
            execution_specs={"check": {"slurmClusterRefName": "lab"}},
        ),
        emit=lambda _: None,
    )
    parent._get = lambda kind, *_: {"cronjob": cron, "job": job, "helmrelease": hr}[kind]
    parent._jobs = lambda: {"old-auxiliary-job": job}
    parent._reservation = lambda _: {"users": ["root"]}
    parent.verify_acceptance = lambda: calls.append("verified")
    parent.kube = lambda args, _: {"items": [pod] if args[1] == "pods" else events}
    parent._save = lambda: calls.append(copy.deepcopy(state.get("auxiliaryRecovery")))
    parent._until = lambda action, _: (
        action() or (_ for _ in ()).throw(RuntimeError("not terminal"))
    )

    def patch(kind, name, namespace, payload, *, uid):
        assert (kind, name, namespace, uid) == ("job", "old-auxiliary-job", "soperator", "job")
        assert state["auxiliaryRecovery"]["terminationIntent"] is True
        assert hr["spec"]["suspend"] is True and cron["spec"]["suspend"] is True
        assert payload == {"spec": {"activeDeadlineSeconds": 1}}
        calls.append("deadline")
        job["spec"]["activeDeadlineSeconds"] = 1
        job["status"] = {
            "conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]
        }
        pod["status"]["phase"] = "Failed"

    parent._patch = patch

    def apply(proof, stop):
        assert proof == state["auxiliaryRecovery"]
        hr["spec"]["suspend"] = True
        calls.append("suspended")
        stop()
        volumes = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
        volumes[1]["configMap"]["name"] = "lab-slurm-configs"
        volumes[2]["secret"]["secretName"] = "lab-munge"
        calls.append("bound")

    recovery = AuxiliaryBindingRecovery(
        parent, render=lambda _: copy.deepcopy(rendered), apply_quiet=apply
    )
    return SimpleNamespace(
        recovery=recovery,
        parent=parent,
        job=job,
        pod=pod,
        cron=cron,
        hr=hr,
        rendered=rendered,
        events=events,
        calls=calls,
    )


def test_recovery_preserves_acceptance_and_ends_only_the_never_started_job():
    f = fixture()
    accepted = copy.deepcopy(f.parent.state["jobs"])
    executable = copy.deepcopy(f.job["spec"]["template"])
    result = f.recovery.recover()
    assert result["status"] == "complete"
    assert f.parent.state["jobs"] == accepted
    assert f.job["spec"]["template"] == executable
    assert f.calls.index("suspended") < f.calls.index("deadline") < f.calls.index("bound")
    before = copy.deepcopy(f.calls)
    assert f.recovery.recover() == result
    assert f.calls == before


@pytest.mark.parametrize(
    "change",
    [
        "running",
        "image",
        "restarted",
        "last-state",
        "slurm",
        "owner",
        "source",
        "custom-ref",
        "script",
        "missing-event",
        "authorization",
        "not-accepted",
        "cron-running",
        "parallel",
    ],
)
def test_unproven_auxiliary_recovery_fails_without_mutation(change):
    f = fixture()
    if change == "running":
        f.pod["status"]["phase"] = "Running"
    elif change == "image":
        f.pod["status"]["initContainerStatuses"][0]["imageID"] = "ran"
    elif change == "restarted":
        f.pod["status"]["containerStatuses"][0]["restartCount"] = 1
    elif change == "last-state":
        f.pod["status"]["containerStatuses"][0]["lastState"] = {"terminated": {"exitCode": 0}}
    elif change == "slurm":
        f.job["metadata"]["annotations"] = {"slurm-job-id": "123"}
    elif change == "owner":
        f.pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    elif change == "source":
        f.hr["status"]["lastAttemptedRevisionDigest"] = "foreign"
    elif change == "custom-ref":
        f.cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"][1]["configMap"][
            "name"
        ] = "foreign"
    elif change == "script":
        f.rendered["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][
            "command"
        ] = ["different"]
    elif change == "missing-event":
        f.events.clear()
    elif change == "authorization":
        f.parent._reservation = lambda _: {"users": ["root", "checks"]}
    elif change == "not-accepted":
        f.parent.state["catchupRecovery"]["status"] = "quiet"
    elif change == "cron-running":
        f.cron["spec"]["suspend"] = False
    else:
        f.job["spec"]["parallelism"] = 2
    with pytest.raises(RuntimeError):
        f.recovery.recover()
    assert "auxiliaryRecovery" not in f.parent.state
    assert "deadline" not in f.calls and "suspended" not in f.calls


def test_termination_intent_does_not_authorize_a_pod_that_started_later():
    f = fixture()
    original_save = f.parent._save

    def interrupted():
        original_save()
        if f.parent.state["auxiliaryRecovery"].get("terminationIntent"):
            raise KeyboardInterrupt

    f.parent._save = interrupted
    with pytest.raises(KeyboardInterrupt):
        f.recovery.recover()
    f.parent._save = original_save
    f.pod["status"]["containerStatuses"][0]["imageID"] = "started-after-intent"
    with pytest.raises(RuntimeError, match="never executed"):
        f.recovery.recover()
    assert "deadline" not in f.calls


def test_changed_job_identity_cannot_resume_an_admitted_recovery():
    f = fixture()
    f.recovery.apply_quiet = lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        f.recovery.recover()
    f.job["metadata"]["uid"] = "recreated"
    f.hr["spec"]["suspend"] = True
    with pytest.raises(RuntimeError, match="identity"):
        f.recovery.stop_admitted_job(f.parent.state["auxiliaryRecovery"])
    assert "deadline" not in f.calls


def test_completed_native_deadline_is_not_repeated_after_interruption():
    f = fixture()
    apply = f.recovery.apply_quiet

    def interrupted(proof, stop):
        apply(proof, stop)
        raise KeyboardInterrupt

    f.recovery.apply_quiet = interrupted
    with pytest.raises(KeyboardInterrupt):
        f.recovery.recover()
    assert f.parent.state["auxiliaryRecovery"]["status"] == "admitted"
    assert f.calls.count("deadline") == 1
    f.recovery.apply_quiet = apply
    assert f.recovery.recover()["status"] == "complete"
    assert f.calls.count("deadline") == 1
