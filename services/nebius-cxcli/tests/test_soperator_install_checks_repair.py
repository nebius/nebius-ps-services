import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_install_checks_repair as repair
from nebius_cxcli.soperator_checks_binding import CHECKS_RELEASE


@pytest.fixture
def failed_apply():
    return {
        "status": "recovery-required",
        "transitions": [
            {"phase": "resolve-immutable-sources", "status": "complete"},
            {"phase": "establish-boot-storage-barrier", "status": "complete"},
            {
                "phase": "apply-declarative-release",
                "status": "failed",
                "id": "apply-id",
                "failureType": "operation-error",
                "failureAttempts": 1,
            },
        ],
        "irreversibleIntent": {
            "transitionId": "apply-id",
            "phase": "apply-declarative-release",
            "disposition": "pending-forward-only",
        },
    }


@pytest.mark.parametrize("failed", [True, False])
def test_binding_repair_admits_interrupted_and_recorded_failed_apply(failed_apply, failed):
    if not failed:
        failed_apply["status"] = "running"
        failed_apply["transitions"][-1]["status"] = "running"
    repair._validate_install_apply_frontier(failed_apply)


@pytest.mark.parametrize(
    "mutation",
    [
        "status-pair",
        "incomplete-prefix",
        "later-phase",
        "completed-apply",
        "receipt",
        "frontier",
        "missing-intent",
        "foreign-intent",
        "foreign-failure",
        "missing-attempt",
        "zero-attempts",
        "malformed-attempts",
    ],
)
def test_binding_repair_rejects_unproven_failed_frontier(failed_apply, mutation):
    final = failed_apply["transitions"][-1]
    if mutation == "status-pair":
        failed_apply["status"] = "running"
    elif mutation == "incomplete-prefix":
        failed_apply["transitions"][0]["status"] = "failed"
    elif mutation == "later-phase":
        failed_apply["transitions"].append({"phase": "wait-flux-graph", "status": "failed"})
    elif mutation == "completed-apply":
        final["status"] = "complete"
    elif mutation == "receipt":
        final["receiptSha256"] = "completed"
    elif mutation == "frontier":
        failed_apply["irreversibleFrontier"] = {"phase": "apply-declarative-release"}
    elif mutation == "missing-intent":
        failed_apply.pop("irreversibleIntent")
    elif mutation == "foreign-intent":
        failed_apply["irreversibleIntent"]["transitionId"] = "foreign"
    elif mutation == "foreign-failure":
        final["failureType"] = "verification-error"
    elif mutation == "missing-attempt":
        final.pop("failureAttempts")
    elif mutation == "zero-attempts":
        final["failureAttempts"] = 0
    else:
        final["failureAttempts"] = True
    with pytest.raises(RuntimeError, match="Checks repair"):
        repair._validate_install_apply_frontier(failed_apply)


@pytest.fixture
def files():
    values = {
        "slurmCluster": {
            "overrideValues": {
                "volumeSources": [
                    {
                        "name": "jail",
                        "persistentVolumeClaim": {"claimName": "active-jail"},
                    }
                ]
            }
        },
        "soperatorActiveChecks": {
            "enabled": True,
            "overrideValues": {"checks": {"keep": {"enabled": False}}},
        },
    }
    cm = {"kind": "ConfigMap", "data": {"values.yaml": yaml.safe_dump(values)}}
    outer = {
        "spec": {
            "values": values,
            "postRenderers": [
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"name": CHECKS_RELEASE},
                                "patch": yaml.safe_dump(
                                    [
                                        {
                                            "op": "add",
                                            "path": "/spec/chartRef",
                                            "value": {"name": "pinned"},
                                        }
                                    ]
                                ),
                            }
                        ]
                    }
                }
            ],
        }
    }
    return {
        repair.VALUES_FILE: yaml.safe_dump(cm).encode(),
        repair.OUTER_FILE: yaml.safe_dump(outer).encode(),
        "storage": b"retain bytes",
        "dashboards": b"retain dashboards",
    }


def test_candidate_preserves_every_unrelated_value_and_file(files):
    before = copy.deepcopy(files)
    after = repair.checks_repair_candidate(files)
    assert files == before
    assert after.keys() == before.keys()
    assert {name for name in after if after[name] != before[name]} == {
        repair.VALUES_FILE,
        repair.OUTER_FILE,
    }
    values = yaml.safe_load(yaml.safe_load(after[repair.VALUES_FILE])["data"]["values.yaml"])
    outer = yaml.safe_load(after[repair.OUTER_FILE])
    assert outer["spec"]["values"] == values
    assert (
        values["soperatorActiveChecks"]["overrideValues"]["jobContainer"]["volumes"][0][
            "persistentVolumeClaim"
        ]["claimName"]
        == "active-jail"
    )
    del values["soperatorActiveChecks"]["overrideValues"]["jobContainer"]
    assert values == yaml.safe_load(
        yaml.safe_load(before[repair.VALUES_FILE])["data"]["values.yaml"]
    )


@pytest.mark.parametrize(
    "mutation", ["drift", "foreign-renderer", "missing-child", "ambiguous-jail"]
)
def test_candidate_rejects_unproven_inputs(files, mutation):
    outer = yaml.safe_load(files[repair.OUTER_FILE])
    if mutation == "drift":
        outer["spec"]["values"]["foreign"] = True
    elif mutation == "foreign-renderer":
        outer["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"] = yaml.safe_dump(
            [{"path": "/spec/postRenderers", "value": []}]
        )
    elif mutation == "missing-child":
        outer["spec"]["postRenderers"][0]["kustomize"]["patches"] = []
    else:
        outer["spec"]["values"]["slurmCluster"]["overrideValues"]["volumeSources"] *= 2
        cm = yaml.safe_load(files[repair.VALUES_FILE])
        cm["data"]["values.yaml"] = yaml.safe_dump(outer["spec"]["values"])
        files[repair.VALUES_FILE] = yaml.safe_dump(cm).encode()
    files[repair.OUTER_FILE] = yaml.safe_dump(outer).encode()
    with pytest.raises(RuntimeError):
        repair.checks_repair_candidate(files)


@pytest.fixture
def hook():
    return {
        "metadata": {
            "name": "wait-for-active-checks",
            "namespace": "soperator",
            "uid": "hook-uid",
            "resourceVersion": "7",
            "annotations": {"helm.sh/hook": "post-install,post-upgrade"},
        },
        "spec": {
            "template": {
                "spec": {
                    "serviceAccountName": "activecheck-waiter",
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "wait",
                            "image": "upstream:fixed",
                            "command": ["kubectl", "get", "activechecks"],
                        }
                    ],
                }
            }
        },
        "status": {"active": 1},
    }


@pytest.mark.parametrize(
    "mutation", ["image", "command", "env", "sidecar", "completed", "namespace"]
)
def test_hook_requires_exact_source_execution_and_identity(hook, mutation):
    live = copy.deepcopy(hook)
    container = live["spec"]["template"]["spec"]["containers"][0]
    if mutation in {"image", "command", "env"}:
        container[mutation] = "foreign"
    elif mutation == "sidecar":
        live["spec"]["template"]["spec"]["containers"].append(copy.deepcopy(container))
    elif mutation == "completed":
        live["status"]["succeeded"] = 1
    else:
        live["metadata"]["namespace"] = "foreign"
    with pytest.raises(RuntimeError):
        repair.validate_wait_hook(live, hook)


@pytest.mark.parametrize(
    "mode", ["patch", "replay", "new-hook", "foreign-release", "unsuspended", "lost-authority"]
)
def test_hook_recovery_is_fenced_and_uid_scoped(monkeypatch, hook, mode):
    evidence = repair.validate_wait_hook(hook, hook)
    child = {
        "name": "checks",
        "namespace": "flux-system",
        "uid": "child-uid",
        "sourceDigest": "pinned",
    }
    release = {
        "metadata": {"uid": "child-uid"},
        "spec": {"suspend": True},
        "status": {"lastAttemptedRevisionDigest": "pinned"},
    }
    if mode == "replay":
        hook["spec"]["activeDeadlineSeconds"] = 1
    if mode == "new-hook":
        hook["metadata"]["uid"] = "new"
    if mode == "foreign-release":
        release["metadata"]["uid"] = "foreign"
    if mode == "unsuspended":
        release["spec"]["suspend"] = False
    monkeypatch.setattr(
        repair, "_kube_get", lambda args, **kw: release if args[0] == "helmrelease" else hook
    )
    writes = []

    def run(args, **kwargs):
        patch = json.loads(args[-1])
        writes.append(patch)
        hook["spec"]["activeDeadlineSeconds"] = patch[-1]["value"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(repair.subprocess, "run", run)

    def authority():
        if mode == "lost-authority":
            raise RuntimeError("lost lease")

    args = dict(
        repair={"checksRelease": child, "waitHook": evidence},
        env={},
        kube_context="exact",
        assert_authority=authority,
    )
    if mode in {"foreign-release", "unsuspended", "lost-authority"}:
        with pytest.raises(RuntimeError):
            repair.terminate_admitted_wait_hook(**args)
        assert writes == []
    else:
        repair.terminate_admitted_wait_hook(**args)
        assert len(writes) == (1 if mode == "patch" else 0)
        if writes:
            assert writes[0][:2] == [
                {"op": "test", "path": "/metadata/uid", "value": "hook-uid"},
                {"op": "test", "path": "/metadata/resourceVersion", "value": "7"},
            ]
