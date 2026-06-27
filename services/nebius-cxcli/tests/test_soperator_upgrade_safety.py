from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nebius_cxcli.soperator_upgrade_safety import (
    ProtectedCustomerState,
    capture_protected_customer_state,
    compare_protected_customer_state,
    run_post_upgrade_fast_verification,
)


@dataclass(frozen=True)
class _Result:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str = ""


class _Runner:
    def __init__(
        self,
        *,
        config_value: str = "baseline",
        nodeset_script: str = "mount-home",
        pvc_storage: str = "100Gi",
        include_accounting_pvc: bool = True,
        pod_phase: str = "Running",
        waiting_reason: str = "",
        activecheck: dict[str, Any] | None = None,
        home_mount: str = "nfs.example:/home on /home type nfs4 (rw)",
    ) -> None:
        self.config_value = config_value
        self.nodeset_script = nodeset_script
        self.pvc_storage = pvc_storage
        self.include_accounting_pvc = include_accounting_pvc
        self.pod_phase = pod_phase
        self.waiting_reason = waiting_reason
        self.activecheck = activecheck
        self.home_mount = home_mount
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 120,
        check: bool = True,
    ) -> _Result:
        del input_text, timeout_seconds, check
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        if "exec" in command:
            return _Result(command, 0, self._exec_output(command))
        if "pods" in command:
            return _Result(command, 0, _json({"items": [self._pod()]}))
        if "pvc" in command:
            items = [self._accounting_pvc()] if self.include_accounting_pvc else []
            return _Result(command, 0, _json({"items": items}))
        if "pv" in command:
            return _Result(command, 0, _json({"items": []}))
        if "configmaps" in command:
            return _Result(command, 0, _json({"items": [self._configmap()]}))
        if "secrets" in command:
            return _Result(command, 0, _json({"items": [self._secret()]}))
        if "deployments" in command or "statefulsets" in command or "daemonsets" in command:
            return _Result(command, 0, _json({"items": []}))
        if "slurmclusters" in command:
            return _Result(command, 0, _json({"items": [self._slurmcluster()]}))
        if "nodesets" in command:
            return _Result(command, 0, _json({"items": [self._nodeset()]}))
        if "activechecks" in command:
            items = [self.activecheck] if self.activecheck is not None else []
            return _Result(command, 0, _json({"items": items}))
        if "helmreleases" in command or "kustomizations" in command:
            return _Result(command, 0, _json({"items": []}))
        if "nodes" in command:
            return _Result(command, 0, _json({"items": [self._node()]}))
        return _Result(command, 0, _json({"items": []}))

    def _pod(self) -> dict[str, Any]:
        container_state: dict[str, Any] = {}
        if self.waiting_reason:
            container_state = {"waiting": {"reason": self.waiting_reason}}
        return {
            "kind": "Pod",
            "metadata": {
                "name": "login-0",
                "namespace": "soperator",
                "labels": {"app": "login"},
                "ownerReferences": [{"kind": "StatefulSet", "name": "login"}],
            },
            "spec": {"nodeName": "node-1"},
            "status": {
                "phase": self.pod_phase,
                "containerStatuses": [
                    {"name": "login", "restartCount": 0, "state": container_state}
                ],
            },
        }

    def _accounting_pvc(self) -> dict[str, Any]:
        return {
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": "accounting-pvc",
                "namespace": "soperator",
                "labels": {"soperator.nebius.com/storage": "accounting"},
            },
            "spec": {
                "volumeName": "pv-accounting",
                "storageClassName": "nfs",
                "accessModes": ["ReadWriteMany"],
                "resources": {"requests": {"storage": self.pvc_storage}},
            },
            "status": {"phase": "Bound", "capacity": {"storage": self.pvc_storage}},
        }

    def _configmap(self) -> dict[str, Any]:
        return {
            "kind": "ConfigMap",
            "metadata": {"name": "slurm-config", "namespace": "soperator"},
            "data": {"slurm.conf": self.config_value},
        }

    def _secret(self) -> dict[str, Any]:
        return {
            "kind": "Secret",
            "metadata": {"name": "munge", "namespace": "soperator"},
            "type": "Opaque",
            "data": {"munge.key": "hashed-only"},
        }

    def _slurmcluster(self) -> dict[str, Any]:
        return {
            "kind": "SlurmCluster",
            "metadata": {"name": "cluster", "namespace": "soperator"},
            "spec": {"clusterName": "cluster"},
            "status": {"phase": "Available"},
        }

    def _nodeset(self) -> dict[str, Any]:
        return {
            "kind": "NodeSet",
            "metadata": {"name": "worker", "namespace": "soperator"},
            "spec": {
                "template": {
                    "spec": {
                        "initScript": self.nodeset_script,
                        "volumes": [{"name": "home", "nfs": {"server": "nfs.example"}}],
                    }
                }
            },
            "status": {"phase": "Ready"},
        }

    def _node(self) -> dict[str, Any]:
        return {
            "kind": "Node",
            "metadata": {"name": "node-1", "labels": {"nebius.com/node-group": "workers"}},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }

    def _exec_output(self, command: Sequence[str]) -> str:
        joined = " ".join(command)
        if "findmnt" in joined:
            return self.home_mount
        if "show qos" in joined:
            return "normal|100"
        if "show assoc" in joined:
            return "cluster|root|user|gpu|normal"
        if "show nodes" in joined:
            return "NodeName=worker-0 State=IDLE"
        if "show partition" in joined:
            return "PartitionName=gpu Nodes=worker-0 State=UP"
        return "ClusterName=cluster"


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def _capture(runner: _Runner) -> ProtectedCustomerState:
    return capture_protected_customer_state(
        command_runner=runner,
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
    )


def test_configmap_and_nodeset_drift_are_protected_deltas() -> None:
    before = _capture(_Runner(config_value="baseline", nodeset_script="mount-home"))
    after = _capture(_Runner(config_value="changed", nodeset_script="changed-mount-home"))

    comparison = compare_protected_customer_state(before=before, after=after)

    assert comparison["status"] == "drift-detected"
    resources = {
        (delta["kind"], delta["resource"], delta["classification"])
        for delta in comparison["deltas"]
    }
    assert ("configmaps", "soperator/slurm-config", "remediation_required") in resources
    assert ("nodesets", "soperator/worker", "remediation_required") in resources


def test_custom_resource_specs_and_annotations_are_redacted_from_report_payload() -> None:
    class _SensitiveRunner(_Runner):
        def _slurmcluster(self) -> dict[str, Any]:
            return {
                "kind": "SlurmCluster",
                "metadata": {
                    "name": "cluster",
                    "namespace": "soperator",
                    "annotations": {
                        "kubectl.kubernetes.io/last-applied-configuration": json.dumps(
                            {
                                "spec": {
                                    "accounting": {
                                        "externalDB": {
                                            "host": "internal-db.example",
                                            "password": "raw-password",
                                        }
                                    }
                                }
                            }
                        )
                    },
                },
                "spec": {
                    "accounting": {
                        "externalDB": {
                            "host": "internal-db.example",
                            "passwordSecretKeyRef": {
                                "name": "accounting-secret",
                                "key": "password",
                            },
                        }
                    },
                    "slurmConfig": "AccountingStoragePass=raw-password",
                },
                "status": {"phase": "Available"},
            }

        def _nodeset(self) -> dict[str, Any]:
            return {
                "kind": "NodeSet",
                "metadata": {
                    "name": "worker",
                    "namespace": "soperator",
                    "annotations": {"internal-note": "token=raw-token"},
                },
                "spec": {
                    "template": {
                        "spec": {
                            "initScript": (
                                "curl https://internal-nfs.example/bootstrap.sh?token=raw-token"
                            )
                        }
                    }
                },
                "status": {"phase": "Ready"},
            }

    state = _capture(_SensitiveRunner())
    payload_text = json.dumps(state.as_payload(), sort_keys=True)

    assert "raw-password" not in payload_text
    assert "raw-token" not in payload_text
    assert "internal-db.example" not in payload_text
    assert "internal-nfs.example" not in payload_text
    nodeset = state.sections["nodesets"]["items"][0]  # type: ignore[index]
    slurmcluster = state.sections["slurmclusters"]["items"][0]  # type: ignore[index]
    assert "spec" not in nodeset
    assert "spec" not in slurmcluster
    assert nodeset["annotation_keys"] == ["internal-note"]
    assert "spec_hash" in nodeset
    assert "spec_hash" in slurmcluster


def test_fast_verification_fails_for_pending_or_crashloop_pod() -> None:
    before = _capture(_Runner())
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(pod_phase="Pending", waiting_reason="CrashLoopBackOff"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    assert result.status == "failed"
    assert any(
        check["name"] == "pods-running-or-completed" and check["status"] == "failed"
        for check in result.checks
    )


def test_pvc_size_reduction_fails_fast_verification() -> None:
    before = _capture(_Runner(pvc_storage="100Gi"))
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(pvc_storage="10Gi"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    assert result.status == "failed"
    assert any(
        check["name"] == "protected-pvcs-restored" and "size reduced" in check["summary"]
        for check in result.checks
    )


def test_remediation_required_drift_requires_explicit_approval() -> None:
    before = _capture(_Runner(config_value="baseline"))

    unapproved = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="changed"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    assert unapproved.status == "failed"
    assert any(
        check["name"] == "protected-state-comparison"
        and check["status"] == "failed"
        and "require remediation approval" in check["summary"]
        for check in unapproved.checks
    )

    approved = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="changed"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        remediation_approved=True,
    )

    assert approved.status == "passed"
    assert any(
        check["name"] == "protected-state-comparison"
        and check["status"] == "passed"
        and "remediation-approved" in check["summary"]
        for check in approved.checks
    )


def test_home_mount_and_activechecks_baseline_are_verified() -> None:
    before = _capture(
        _Runner(
            activecheck={
                "kind": "ActiveCheck",
                "metadata": {"name": "storage", "namespace": "soperator"},
                "spec": {"suspend": False},
                "status": {"phase": "Completed"},
            },
            home_mount="nfs.example:/home on /home type nfs4 (rw)",
        )
    )
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(
            activecheck={
                "kind": "ActiveCheck",
                "metadata": {"name": "storage", "namespace": "soperator"},
                "spec": {"suspend": True},
                "status": {"phase": "Completed"},
            },
            home_mount="other.example:/home on /home type nfs4 (rw)",
        ),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    failed = {check["name"] for check in result.checks if check["status"] == "failed"}
    assert "home-mounted" in failed
    assert "activechecks-restored" in failed


def test_helmrelease_suspended_state_drift_is_detected() -> None:
    before = ProtectedCustomerState(
        target_ref="gpu",
        namespace="soperator",
        captured_at="before",
        sections={
            "flux": {
                "helmreleases": {
                    "available": True,
                    "items": [
                        {
                            "kind": "HelmRelease",
                            "namespace": "soperator",
                            "name": "soperator",
                            "suspend": False,
                        }
                    ],
                }
            }
        },
    )
    after = ProtectedCustomerState(
        target_ref="gpu",
        namespace="soperator",
        captured_at="after",
        sections={
            "flux": {
                "helmreleases": {
                    "available": True,
                    "items": [
                        {
                            "kind": "HelmRelease",
                            "namespace": "soperator",
                            "name": "soperator",
                            "suspend": True,
                        }
                    ],
                }
            }
        },
    )

    comparison = compare_protected_customer_state(before=before, after=after)

    assert any(
        delta["kind"] == "flux.helmreleases"
        and delta["field"] == "suspend"
        and delta["classification"] == "remediation_required"
        for delta in comparison["deltas"]
    )


def test_safety_capture_uses_readonly_kubectl_commands_only() -> None:
    runner = _Runner()

    _capture(runner)

    forbidden = {"apply", "delete", "patch", "scale", "cordon", "drain", "uncordon"}
    assert runner.calls
    for command in runner.calls:
        assert not (set(command) & forbidden)
