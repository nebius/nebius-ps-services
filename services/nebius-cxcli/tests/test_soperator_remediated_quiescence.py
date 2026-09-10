import copy

import pytest

from nebius_cxcli import flux_ops


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "reference",
        "revision",
        "config",
        "source-uid",
        "artifact",
        "source-version",
        "no-proof",
    ],
)
def test_helmchart_quiescence_revalidates_exact_native_and_source_evidence(
    monkeypatch, tmp_path, remediated, mutation
):
    expected = {
        "namespace": "flux-system",
        "name": "checks",
        "uid": "checks-uid",
        "sourceKind": "HelmChart",
        "sourceName": "locked-chart",
        "sourceNamespace": "flux-system",
        "sourceUid": "chart-uid",
        "sourceChart": "collector",
        "sourceVersion": "1.2.3",
        "sourceDigest": "sha256:" + "a" * 64,
        "sourceConfigDigest": "sha256:" + "b" * 64,
    }
    remediated["spec"]["chartRef"] = {
        "kind": "HelmChart",
        "name": "locked-chart",
        "namespace": "flux-system",
    }
    remediated["status"].pop("lastAttemptedRevisionDigest")
    remediated["status"]["lastAttemptedRevision"] = "1.2.3"
    remediated["status"]["lastAttemptedConfigDigest"] = expected["sourceConfigDigest"]
    source = {
        "metadata": {"name": "locked-chart", "namespace": "flux-system", "uid": "chart-uid"},
        "spec": {"chart": "collector", "version": "1.2.3"},
        "status": {"artifact": {"digest": expected["sourceDigest"]}},
    }
    if mutation == "reference":
        remediated["spec"]["chartRef"]["name"] = "foreign"
    elif mutation == "revision":
        remediated["status"]["lastAttemptedRevision"] = "changed"
    elif mutation == "config":
        remediated["status"]["lastAttemptedConfigDigest"] = "changed"
    elif mutation == "source-uid":
        source["metadata"]["uid"] = "replaced"
    elif mutation == "artifact":
        source["status"]["artifact"]["digest"] = "changed"
    elif mutation == "source-version":
        source["spec"]["version"] = "changed"
    elif mutation == "no-proof":
        expected.pop("sourceUid")
    calls = []

    def read(args, **kwargs):
        calls.append(args)
        return source if "helmchart" in args else remediated

    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", read)
    kwargs = dict(
        cache_dir=tmp_path,
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0,
        remediated_install_release=expected,
    )
    if mutation is None:
        flux_ops._wait_for_helmrelease_quiescence({("flux-system", "checks")}, **kwargs)
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError):
            flux_ops._wait_for_helmrelease_quiescence({("flux-system", "checks")}, **kwargs)


@pytest.fixture
def remediated():
    return {
        "metadata": {
            "namespace": "flux-system",
            "name": "checks",
            "uid": "checks-uid",
            "generation": 3,
        },
        "spec": {"suspend": True},
        "status": {
            "observedGeneration": 3,
            "lastAttemptedRevisionDigest": "sha256:pinned",
            "lastAttemptedReleaseAction": "install",
            "history": [
                {"status": "uninstalling", "action": "uninstall-remediation", "version": 1}
            ],
            "conditions": [
                {
                    "type": "Reconciling",
                    "status": "True",
                    "reason": "ProgressingWithRetry",
                    "observedGeneration": 3,
                },
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "InstallFailed",
                    "observedGeneration": 3,
                },
                {
                    "type": "Remediated",
                    "status": "True",
                    "reason": "UninstallSucceeded",
                    "observedGeneration": 3,
                },
            ],
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "no-admission",
        "uid",
        "source",
        "generation",
        "condition-generation",
        "unsuspended",
        "active",
        "no-remediation",
        "successful-history",
        "upgrade",
    ],
)
def test_quiescence_requires_exact_admitted_completed_install_remediation(
    monkeypatch, tmp_path, remediated, mutation
):
    expected = {
        "namespace": "flux-system",
        "name": "checks",
        "uid": "checks-uid",
        "sourceDigest": "sha256:pinned",
    }
    live = copy.deepcopy(remediated)
    if mutation == "uid":
        live["metadata"]["uid"] = "foreign"
    elif mutation == "source":
        live["status"]["lastAttemptedRevisionDigest"] = "foreign"
    elif mutation == "generation":
        live["status"]["observedGeneration"] = 2
    elif mutation == "condition-generation":
        live["status"]["conditions"][2]["observedGeneration"] = 2
    elif mutation == "unsuspended":
        live["spec"]["suspend"] = False
    elif mutation == "active":
        live["status"]["conditions"][0]["reason"] = "Progressing"
    elif mutation == "no-remediation":
        live["status"]["conditions"].pop()
    elif mutation == "successful-history":
        live["status"]["history"].append({"status": "deployed"})
    elif mutation == "upgrade":
        live["status"]["lastAttemptedReleaseAction"] = "upgrade"
    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", lambda *a, **kw: live)
    args = dict(
        cache_dir=tmp_path,
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0,
        remediated_install_release=None if mutation == "no-admission" else expected,
    )
    if mutation is None:
        flux_ops._wait_for_helmrelease_quiescence({("flux-system", "checks")}, **args)
    else:
        with pytest.raises(RuntimeError, match="did not quiesce"):
            flux_ops._wait_for_helmrelease_quiescence({("flux-system", "checks")}, **args)
