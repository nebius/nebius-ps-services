import copy

import pytest

from nebius_cxcli import flux_ops
from nebius_cxcli.nsight_profiling import customization_job
from nebius_cxcli.soperator_protected_data_plane import (
    bind_protected_job_authority,
    bind_protected_workload_identity,
    protected_workload_identity,
)
from test_soperator_flux_readiness import (
    _contract,
    _install_fake_kubectl_json,
    _install_native_kubectl,
    _responses,
)


def attempt(index, phase, *, action="admit", operation="operation", pvc_uid="pvc-uid", epoch=None):
    job = bind_protected_job_authority(
        customization_job(
            name=f"cxcli-nsight-{action}-{index}",
            image="example/jail@sha256:" + "a" * 64,
            pvc="jail",
            filesystem_id="fs",
            request={"action": action},
        ),
        operation_id=operation,
        fence_epoch=index + 2 if epoch is None else epoch,
        pvc_uid=pvc_uid,
    )
    sha = protected_workload_identity(job).workload_sha256
    job = bind_protected_workload_identity(
        job, requested_workload_sha256=sha, admitted_workload_sha256=sha
    )
    job["metadata"]["uid"] = f"job-{index}"
    job["status"] = {
        "active": 0,
        "conditions": [
            {"type": "Complete" if phase == "Succeeded" else "Failed", "status": "True"}
        ],
    }
    pod = copy.deepcopy(job["spec"]["template"])
    pod["metadata"].update(
        name=f"attempt-{index}",
        namespace="soperator",
        uid=f"pod-{index}",
        ownerReferences=[
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "name": job["metadata"]["name"],
                "uid": job["metadata"]["uid"],
                "controller": True,
            }
        ],
    )
    pod["status"] = {"phase": phase}
    for spec_key, status_key in (
        ("containers", "containerStatuses"),
        ("initContainers", "initContainerStatuses"),
    ):
        pod["status"][status_key] = [
            {
                "name": c["name"],
                "state": {"terminated": {"exitCode": 0 if phase == "Succeeded" else 1}},
            }
            for c in pod["spec"].get(spec_key, [])
        ]
    return job, pod


def fixture(*, action="admit", successor=None):
    responses = _responses()
    failed_job, failed_pod = attempt(0, "Failed", action=action)
    succeeded_job, succeeded_pod = attempt(1, "Succeeded", action=action, **(successor or {}))
    responses["pods"]["items"].extend([failed_pod, succeeded_pod])
    responses["jobs.batch"] = {"items": [failed_job, succeeded_job]}
    responses["cronjobs.batch"] = {"items": []}
    responses.setdefault("activechecks.slurm.nebius.ai", {"items": []})
    return responses


@pytest.mark.parametrize("action", ["admit", "install", "verify"])
def test_recovered_nsight_attempt_is_history_not_service_readiness(monkeypatch, action):
    responses = fixture(action=action)
    _install_fake_kubectl_json(monkeypatch, responses)
    ready, detail = flux_ops._soperator_product_readiness(_contract(), env={})
    assert ready, detail


@pytest.mark.parametrize("failure", [None, "foreign-successor", "unready-service"])
def test_native_observation_uses_the_same_nsight_history_gate(monkeypatch, failure):
    payloads = _install_native_kubectl(monkeypatch)
    response = fixture(
        successor={"operation": "foreign"} if failure == "foreign-successor" else None
    )
    payloads["pods"]["items"].extend(response["pods"]["items"][-2:])
    if failure == "unready-service":
        payloads["pods"]["items"][0]["status"]["conditions"] = []
    original = flux_ops._kubectl_json

    def read(command, **kwargs):
        for resource in ("jobs.batch", "cronjobs.batch"):
            if resource in command:
                return response[resource]
        return original(command, **kwargs)

    monkeypatch.setattr(flux_ops, "_kubectl_json", read)
    ready, _, receipt = flux_ops._native_soperator_observation(expected_release="4.1.7", env={})
    assert ready is (failure is None)
    assert (receipt is not None) is ready


@pytest.mark.parametrize(
    "successor", [{"operation": "foreign"}, {"pvc_uid": "foreign"}, {"epoch": 1}]
)
def test_nsight_success_must_match_the_failed_attempt_authority(monkeypatch, successor):
    responses = fixture(successor=successor)
    _install_fake_kubectl_json(monkeypatch, responses)
    assert flux_ops._soperator_product_readiness(_contract(), env={})[0] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "no-success",
        "active-job",
        "running-container",
        "job-hash",
        "pod-image",
        "owner-uid",
        "namespace",
        "unready-service",
    ],
)
def test_nsight_history_cannot_hide_unproven_or_live_failures(monkeypatch, mutation):
    responses = fixture()
    job = responses["jobs.batch"]["items"][0]
    pod = responses["pods"]["items"][-2]
    if mutation == "no-success":
        responses["jobs.batch"]["items"].pop()
    elif mutation == "active-job":
        job["status"]["active"] = 1
    elif mutation == "running-container":
        pod["status"]["containerStatuses"][0]["state"] = {"running": {}}
    elif mutation == "job-hash":
        job["metadata"]["annotations"] = {}
    elif mutation == "pod-image":
        pod["spec"]["containers"][0]["image"] = "foreign"
    elif mutation == "owner-uid":
        pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    elif mutation == "namespace":
        pod["metadata"]["namespace"] = "foreign"
    else:
        responses["pods"]["items"][0]["status"]["conditions"] = []
    _install_fake_kubectl_json(monkeypatch, responses)
    assert flux_ops._soperator_product_readiness(_contract(), env={})[0] is False
