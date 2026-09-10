import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli import flux_ops
from nebius_cxcli.soperator_install_retry import (
    collector_install_retry_patch,
    install_retry_pending,
)
from nebius_cxcli.soperator_jail_logs_binding import JAIL_LOGS_RELEASE


@pytest.fixture
def retry_state():
    expected = {
        "namespace": "flux-system",
        "name": "cxcli-" + JAIL_LOGS_RELEASE,
        "uid": "release-uid",
        "sourceKind": "HelmChart",
        "sourceName": "collector-source",
        "sourceNamespace": "flux-system",
        "sourceVersion": "0.149.0",
        "sourceDigest": "sha256:" + "a" * 64,
        "sourceUid": "source-uid",
        "sourceChart": "opentelemetry-collector",
    }
    payload = {
        "metadata": {
            "name": expected["name"],
            "namespace": "flux-system",
            "uid": "release-uid",
            "resourceVersion": "10",
            "generation": 6,
            "annotations": {"kept": "value"},
        },
        "spec": {
            "suspend": True,
            "releaseName": "collector",
            "targetNamespace": "logs",
            "chartRef": {
                "kind": "HelmChart",
                "name": "collector-source",
                "namespace": "flux-system",
            },
            "values": {"unchanged": True},
        },
        "status": {
            "observedGeneration": 6,
            "lastAttemptedRevision": "0.149.0",
            "lastAttemptedReleaseAction": "upgrade",
            "lastAttemptedConfigDigest": "sha256:" + "b" * 64,
            "history": [
                {
                    "status": "failed",
                    "action": "upgrade",
                    "configDigest": "sha256:" + "b" * 64,
                    "chartVersion": "0.149.0",
                }
            ],
            "conditions": [
                {
                    "type": "Stalled",
                    "status": "True",
                    "reason": "MissingRollbackTarget",
                    "observedGeneration": 5,
                },
                *[
                    {
                        "type": k,
                        "status": "False",
                        "reason": "UpgradeFailed",
                        "observedGeneration": 5,
                    }
                    for k in ("Ready", "Released")
                ],
            ],
        },
    }
    deployment = {
        "metadata": {
            "name": "collector",
            "namespace": "logs",
            "uid": "deployment-uid",
            "generation": 2,
            "annotations": {
                "meta.helm.sh/release-name": "collector",
                "meta.helm.sh/release-namespace": "logs",
            },
        },
        "spec": {"replicas": 1},
        "status": {
            "observedGeneration": 2,
            "replicas": 1,
            "readyReplicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
            "conditions": [{"type": k, "status": "True"} for k in ("Available", "Progressing")],
        },
    }
    source = {
        "metadata": {"name": "collector-source", "namespace": "flux-system", "uid": "source-uid"},
        "spec": {"chart": "opentelemetry-collector", "version": "0.149.0"},
        "status": {"artifact": {"digest": expected["sourceDigest"]}},
    }
    return payload, expected, deployment, source


def test_retry_is_cas_bound_and_once_per_configuration(retry_state):
    payload, expected, deployment, _ = retry_state
    before = copy.deepcopy(payload)
    token, patch = collector_install_retry_patch(payload, expected, deployment)
    assert payload == before
    assert [(x["op"], x["path"]) for x in patch] == [
        ("test", "/metadata/uid"),
        ("test", "/metadata/resourceVersion"),
        ("test", "/spec"),
        ("add", "/metadata/annotations"),
    ]
    assert patch[2]["value"] == before["spec"]
    assert patch[-1]["value"]["kept"] == "value"
    payload["metadata"]["annotations"] = patch[-1]["value"]
    assert collector_install_retry_patch(payload, expected, deployment) == (token, [])
    assert install_retry_pending(payload, token)
    payload["status"]["lastHandledForceAt"] = token
    assert not install_retry_pending(payload, token)
    assert collector_install_retry_patch(payload, expected, deployment) is None
    payload["metadata"]["generation"] += 1
    payload["status"]["observedGeneration"] += 1
    assert collector_install_retry_patch(payload, expected, deployment) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "uid",
        "chart",
        "source-version",
        "config",
        "successful-history",
        "suspension",
        "generation",
        "running",
        "deployment-owner",
        "deployment-generation",
        "deployment-ready",
        "other-request",
    ],
)
def test_retry_rejects_unproven_or_changed_authority(retry_state, mutation):
    p, e, d, _ = retry_state
    if mutation == "uid":
        p["metadata"]["uid"] = "other"
    elif mutation == "chart":
        p["spec"]["chartRef"]["name"] = "other"
    elif mutation == "source-version":
        p["status"]["lastAttemptedRevision"] = "other"
    elif mutation == "config":
        p["status"]["history"][0]["configDigest"] = "other"
    elif mutation == "successful-history":
        p["status"]["history"].append({"status": "deployed"})
    elif mutation == "suspension":
        p["spec"]["suspend"] = False
    elif mutation == "generation":
        p["status"]["observedGeneration"] -= 1
    elif mutation == "running":
        p["status"]["conditions"].append({"type": "Reconciling", "status": "True"})
    elif mutation == "deployment-owner":
        d["metadata"]["annotations"]["meta.helm.sh/release-name"] = "other"
    elif mutation == "deployment-generation":
        d["status"]["observedGeneration"] -= 1
    elif mutation == "deployment-ready":
        d["status"]["readyReplicas"] = 0
    elif mutation == "other-request":
        p["metadata"]["annotations"]["reconcile.fluxcd.io/forceAt"] = "other"
    with pytest.raises(RuntimeError):
        collector_install_retry_patch(p, e, d)


@pytest.mark.parametrize("source_changed", [False, True])
def test_native_retry_orchestrator_verifies_source_before_mutation(
    tmp_path, monkeypatch, retry_state, source_changed
):
    p, e, d, source = retry_state
    if source_changed:
        source["metadata"]["uid"] = "other"
    values = iter([p, source, d])
    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", lambda *a, **kw: next(values))
    writes = []
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda args, **kw: writes.append(args) or SimpleNamespace(returncode=0),
    )
    if source_changed:
        with pytest.raises(RuntimeError, match="artifact"):
            flux_ops._request_collector_install_retry(e, cache_dir=tmp_path, env={})
        assert writes == []
    else:
        assert flux_ops._request_collector_install_retry(e, cache_dir=tmp_path, env={}).startswith(
            "cxcli-install-retry-"
        )
        assert len(writes) == 1
        assert "--type=json" in writes[0]


@pytest.mark.parametrize("retry_succeeds", [False, True])
def test_stage_waits_for_exact_retry_then_preserves_terminal_gate(
    tmp_path, monkeypatch, retry_state, retry_succeeds
):
    p, e, d, _ = retry_state
    token, patch = collector_install_retry_patch(p, e, d)
    p["metadata"]["annotations"] = patch[-1]["value"]
    p["spec"]["suspend"] = False
    result = copy.deepcopy(p)
    result["status"]["lastHandledForceAt"] = token
    if retry_succeeds:
        result["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
    observations = iter([p, result])
    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", lambda *a, **kw: next(observations))
    monkeypatch.setattr(flux_ops.time, "sleep", lambda _: None)
    args = (
        {
            "releases": [
                {
                    "releaseName": e["name"],
                    "namespace": e["namespace"],
                    "sourceKind": "HelmChart",
                    "sourceName": e["sourceName"],
                    "stage": 8,
                }
            ]
        },
        8,
    )
    kwargs = {
        "cache_dir": tmp_path,
        "env": {},
        "timeout_seconds": 5,
        "poll_interval_seconds": 0.01,
        "pending_install_retries": {(e["namespace"], e["name"]): token},
    }
    if retry_succeeds:
        flux_ops._wait_for_soperator_release_stage(*args, **kwargs)
    else:
        with pytest.raises(RuntimeError, match="MissingRollbackTarget"):
            flux_ops._wait_for_soperator_release_stage(*args, **kwargs)
