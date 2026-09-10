"""A completed native rollback can retain a queued retry on a suspended writer."""

from types import SimpleNamespace

import pytest

from nebius_cxcli import flux_ops, soperator_checks_catchup_flux


@pytest.fixture
def rollback():
    expected = {
        "name": "checks",
        "namespace": "flux-system",
        "uid": "checks-uid",
        "sourceDigest": "sha256:pinned",
    }
    live = {
        "metadata": {**{k: expected[k] for k in ("name", "namespace", "uid")}, "generation": 4},
        "spec": {
            "suspend": True,
            "releaseName": "native-checks",
            "targetNamespace": "soperator",
            "chartRef": {
                "kind": "OCIRepository",
                "name": "checks-source",
                "namespace": "flux-system",
            },
        },
        "status": {
            "observedGeneration": 4,
            "lastAttemptedReleaseAction": "upgrade",
            "lastAttemptedRevision": "4.1.5+abc",
            "lastAttemptedRevisionDigest": expected["sourceDigest"],
            "history": [
                {
                    "action": "rollback",
                    "status": "deployed",
                    "version": 9,
                    "name": "native-checks",
                    "namespace": "soperator",
                    "chartVersion": "4.1.5+abc",
                    "ociDigest": expected["sourceDigest"],
                    "configDigest": "sha256:quiet",
                }
            ],
            "conditions": [
                {"type": kind, "status": state, "reason": reason, "observedGeneration": 4}
                for kind, state, reason in [
                    ("Reconciling", "True", "ProgressingWithRetry"),
                    ("Ready", "False", "RollbackSucceeded"),
                    ("Released", "False", "UpgradeFailed"),
                    ("Remediated", "True", "RollbackSucceeded"),
                ]
            ],
        },
    }
    current = live["status"]["history"][0]
    current.pop("ociDigest")
    live["status"]["lastAttemptedConfigDigest"] = "sha256:steady"
    live["status"]["history"].extend(
        [
            {
                **current,
                "action": "upgrade",
                "status": "failed",
                "version": 8,
                "ociDigest": expected["sourceDigest"],
                "configDigest": "sha256:steady",
            },
            {
                **current,
                "status": "superseded",
                "version": 7,
                "ociDigest": expected["sourceDigest"],
            },
        ]
    )
    source = {
        "metadata": {
            "name": "checks-source",
            "namespace": "flux-system",
            "uid": "source-uid",
            "generation": 1,
        },
        "spec": {"ref": {"digest": expected["sourceDigest"]}},
        "status": {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}],
            "artifact": {"digest": "sha256:archive-checksum", "revision": expected["sourceDigest"]},
        },
    }
    return expected, live, source


def test_accepted_catchup_stage_passes_completed_rollback_authority(
    monkeypatch, tmp_path, rollback
):
    expected, live, source = rollback
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda args, **_: source if "ocirepository" in args else live,
    )

    class AdmittedRecovery:
        def __init__(self, _checks, **kwargs):
            self.apply = kwargs["apply_quiet"]

        def recover(self):
            self.apply({"checksRelease": expected})

    def staged(_paths, **kwargs):
        evidence = {
            k: v
            for k, v in kwargs.items()
            if k in {"remediated_install_release", "remediated_checks_release"}
        }
        flux_ops._wait_for_helmrelease_quiescence(
            {("flux-system", "checks")},
            cache_dir=tmp_path,
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0,
            **evidence,
        )

    monkeypatch.setattr(soperator_checks_catchup_flux, "ChecksCatchupRecovery", AdmittedRecovery)
    monkeypatch.setattr(soperator_checks_catchup_flux, "apply_staged_soperator_release", staged)
    soperator_checks_catchup_flux.recover_staged_checks(
        SimpleNamespace(
            lifecycle=None, authority=lambda: None, policy=SimpleNamespace(auxiliary_pvc="")
        ),
        paths=tmp_path,
        source_dir=tmp_path,
        kube_context="lab",
        extra_env={},
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "no-admission",
        "uid",
        "unsuspended",
        "generation",
        "condition-generation",
        "active",
        "incomplete",
        "failed-rollback",
        "source",
        "old-chart",
        "release",
        "artifact",
        "source-name",
        "stalled",
        "pending-history",
        "install",
        "missing-config",
        "failed-config",
        "prior-source",
        "prior-config",
        "missing-failed-history",
        "source-pin",
        "source-generation",
        "source-not-ready",
        "source-reconciling",
    ],
)
def test_rollback_quiescence_requires_exact_terminal_writer_and_artifact(
    monkeypatch, tmp_path, rollback, change
):
    expected, live, source = rollback
    status = live["status"]
    if change == "no-admission":
        expected = None
    elif change == "uid":
        live["metadata"]["uid"] = "foreign"
    elif change == "unsuspended":
        live["spec"]["suspend"] = False
    elif change == "generation":
        status["observedGeneration"] = 3
    elif change == "condition-generation":
        status["conditions"][3]["observedGeneration"] = 3
    elif change == "active":
        status["conditions"][0]["reason"] = "Progressing"
    elif change == "incomplete":
        status["history"][0]["status"] = "pending-rollback"
    elif change == "failed-rollback":
        status["conditions"][3]["reason"] = "RollbackFailed"
    elif change == "source":
        status["lastAttemptedRevisionDigest"] = "foreign"
    elif change == "old-chart":
        status["history"][0]["chartVersion"] = "4.1.4"
    elif change == "release":
        status["history"][0]["name"] = "foreign"
    elif change == "artifact":
        source["status"]["artifact"]["revision"] = "foreign"
    elif change == "source-name":
        source["metadata"]["name"] = "foreign"
    elif change == "stalled":
        status["conditions"].append({"type": "Stalled", "status": "True"})
    elif change == "pending-history":
        status["history"].append({"status": "pending-upgrade"})
    elif change == "install":
        status["lastAttemptedReleaseAction"] = "install"
    elif change == "missing-config":
        status["history"][0].pop("configDigest")
    elif change == "failed-config":
        status["lastAttemptedConfigDigest"] = "foreign"
    elif change == "prior-source":
        status["history"][2]["ociDigest"] = "foreign"
    elif change == "prior-config":
        status["history"][2]["configDigest"] = "foreign"
    elif change == "missing-failed-history":
        status["history"].pop(1)
    elif change == "source-pin":
        source["spec"]["ref"]["digest"] = "foreign"
    elif change == "source-generation":
        source["status"]["observedGeneration"] = 0
    elif change == "source-not-ready":
        source["status"]["conditions"][0]["status"] = "False"
    elif change == "source-reconciling":
        source["status"]["conditions"].append({"type": "Reconciling", "status": "True"})
    reads = []

    def read(args, **_):
        reads.append(args)
        return source if "ocirepository" in args else live

    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", read)
    args = dict(
        cache_dir=tmp_path,
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0,
        remediated_checks_release=expected,
    )
    if change is None:
        flux_ops._wait_for_helmrelease_quiescence({("flux-system", "checks")}, **args)
        assert len(reads) == 2
    else:
        with pytest.raises(RuntimeError):
            flux_ops._wait_for_helmrelease_quiescence({("flux-system", "checks")}, **args)
