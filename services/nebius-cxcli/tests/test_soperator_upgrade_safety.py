from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from nebius_cxcli.soperator_upgrade_safety import (
    ProtectedCustomerState,
    _classify_external_intentional_deltas,
    build_stage_fast_verification_payload,
    capture_protected_customer_state,
    compare_protected_customer_state,
    run_post_upgrade_fast_verification,
    stage_fast_verification_check,
    stage_fast_verification_markdown_lines,
    stage_fast_verification_report,
    stage_fast_verification_status,
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


class _NodeRunner(_Runner):
    def __init__(
        self,
        node_name: str,
        *,
        node_labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.node_name = node_name
        self.node_labels = node_labels or {"nebius.com/node-group": "workers"}

    def _node(self) -> dict[str, Any]:
        return {
            "kind": "Node",
            "metadata": {"name": self.node_name, "labels": self.node_labels},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }


class _ManagedChartRunner(_Runner):
    def __init__(self, chart_version: str) -> None:
        super().__init__()
        self.chart_version = chart_version

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
        if "deployments" in command:
            self.calls.append(command)
            return _Result(command, 0, _json({"items": [self._deployment()]}))
        if "daemonsets" in command:
            self.calls.append(command)
            return _Result(command, 0, _json({"items": [self._daemonset()]}))
        return super().__call__(command)

    def _chart_labels(self) -> dict[str, str]:
        return {"helm.sh/chart": f"soperator-{self.chart_version}"}

    def _configmap(self) -> dict[str, Any]:
        return {
            "kind": "ConfigMap",
            "metadata": {
                "name": "soperator-upgrade-live-mount-scripts",
                "namespace": "soperator",
                "labels": self._chart_labels(),
            },
            "data": {"mount.sh": "mount-home"},
        }

    def _slurmcluster(self) -> dict[str, Any]:
        return {
            "kind": "SlurmCluster",
            "metadata": {
                "name": "soperator-upgrade-live",
                "namespace": "soperator",
                "labels": self._chart_labels(),
            },
            "spec": {
                "clusterName": "soperator-upgrade-live",
                "chartDefault": self.chart_version,
            },
            "status": {"phase": "Available"},
        }

    def _nodeset(self) -> dict[str, Any]:
        return {
            "kind": "NodeSet",
            "metadata": {
                "name": "worker-gpu-0",
                "namespace": "soperator",
                "labels": self._chart_labels(),
            },
            "spec": {
                "template": {
                    "spec": {
                        "initScript": "mount-home",
                        "chartDefault": self.chart_version,
                    }
                }
            },
            "status": {"phase": "Ready"},
        }

    def _deployment(self) -> dict[str, Any]:
        return {
            "kind": "Deployment",
            "metadata": {
                "name": "soperator-manager",
                "namespace": "soperator",
                "labels": self._chart_labels(),
            },
            "spec": {
                "replicas": 1,
                "template": {
                    "metadata": {"labels": self._chart_labels()},
                    "spec": {
                        "containers": [
                            {
                                "name": "manager",
                                "image": f"registry/soperator:{self.chart_version}",
                            }
                        ]
                    },
                },
            },
            "status": {"readyReplicas": 1, "availableReplicas": 1},
        }

    def _daemonset(self) -> dict[str, Any]:
        return {
            "kind": "DaemonSet",
            "metadata": {
                "name": "controller-placeholder",
                "namespace": "soperator",
                "labels": self._chart_labels(),
            },
            "spec": {
                "template": {
                    "metadata": {"labels": self._chart_labels()},
                    "spec": {
                        "containers": [
                            {
                                "name": "placeholder",
                                "image": f"registry/placeholder:{self.chart_version}",
                            }
                        ]
                    },
                },
            },
            "status": {"readyReplicas": 1},
        }

    def _exec_output(self, command: Sequence[str]) -> str:
        joined = " ".join(command)
        if "show config" in joined:
            return f"ClusterName=cluster\nChartDefault={self.chart_version}"
        if "show nodes" in joined:
            return f"NodeName=worker-0 State=IDLE ChartDefault={self.chart_version}"
        return super()._exec_output(command)


class _RuntimeOnlyRunner(_Runner):
    def __init__(self, runtime_token: str) -> None:
        super().__init__()
        self.runtime_token = runtime_token

    def _exec_output(self, command: Sequence[str]) -> str:
        joined = " ".join(command)
        if "show config" in joined:
            return f"ClusterName=cluster\nRuntimeToken={self.runtime_token}"
        if "show nodes" in joined:
            return f"NodeName=worker-0 State=IDLE RuntimeToken={self.runtime_token}"
        return super()._exec_output(command)


class _UnresponsiveSlurmRunner(_Runner):
    def _exec_output(self, command: Sequence[str]) -> str:
        joined = " ".join(command)
        if "show nodes" in joined:
            return (
                "NodeName=worker-0 State=IDLE+CLOUD+NOT_RESPONDING\n"
                "NodeName=worker-1 State=DOWN+CLOUD"
            )
        return super()._exec_output(command)


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


_OPEN_METRICS_UNSET = object()


class _OpenMetricsRunner(_Runner):
    def __init__(
        self,
        enabled: object = _OPEN_METRICS_UNSET,
        *,
        extra_spec: dict[str, Any] | None = None,
        resource_name: str = "cluster",
        resource_uid: str | None = None,
    ) -> None:
        super().__init__()
        self.enabled = enabled
        self.extra_spec = dict(extra_spec or {})
        self.resource_name = resource_name
        self.resource_uid = resource_uid or f"slurmcluster-uid-{resource_name}"

    def _slurmcluster(self) -> dict[str, Any]:
        spec = dict(self.extra_spec)
        if self.enabled is not _OPEN_METRICS_UNSET:
            spec["slurmNodes"] = {
                "controller": {
                    "openMetrics": {"enabled": self.enabled},
                }
            }
        return {
            "kind": "SlurmCluster",
            "metadata": {
                "name": self.resource_name,
                "namespace": "soperator",
                "uid": self.resource_uid,
            },
            "spec": spec,
            "status": {"phase": "Available"},
        }


def _capture_open_metrics(
    runner: _OpenMetricsRunner,
    *,
    target_ref: str = "cluster",
) -> ProtectedCustomerState:
    return capture_protected_customer_state(
        command_runner=runner,
        target_ref=target_ref,
        namespace="soperator",
        kube_context="external-context",
    )


def _restored_open_metrics_proof(*, desired_enabled: bool = True) -> dict[str, Any]:
    return {
        "revision": 1,
        "status": "restored",
        "desired_enabled": desired_enabled,
        "observed_enabled": desired_enabled,
        "slurmcluster_uid": "slurmcluster-uid-cluster",
        "restored_at": "2026-07-11T00:00:00Z",
        "post_restore_readiness_verified_at": "2026-07-11T00:00:01Z",
    }


def test_slurm_runtime_capture_uses_login_sshd_container() -> None:
    runner = _Runner()

    _capture(runner)

    exec_calls = [call for call in runner.calls if "exec" in call]
    assert exec_calls
    assert all("-c" in call and call[call.index("-c") + 1] == "sshd" for call in exec_calls)


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


def test_recovered_slurm_runtime_probes_are_noncomparable_audit_evidence() -> None:
    fields = (
        "accounting_associations",
        "accounting_qos",
        "home_mount",
        "slurm_config",
        "slurm_nodes",
        "slurm_partitions",
    )
    before = ProtectedCustomerState(
        target_ref="gpu",
        namespace="soperator",
        captured_at="before",
        sections={
            "slurm_runtime": {
                field: {"available": False, "error": "pre-upgrade probe unavailable"}
                for field in fields
            }
        },
    )
    after = ProtectedCustomerState(
        target_ref="gpu",
        namespace="soperator",
        captured_at="after",
        sections={
            "slurm_runtime": {
                field: {
                    "available": True,
                    "stdout_sha256": f"post-upgrade-{field}",
                    "line_count": 1,
                }
                for field in fields
            }
        },
    )

    comparison = compare_protected_customer_state(before=before, after=after)

    assert comparison["approval_required_count"] == 0
    by_field = {delta["field"]: delta for delta in comparison["deltas"]}
    assert set(by_field) == set(fields)
    for field in fields:
        assert by_field[field]["classification"] == "preserve"
        assert by_field[field]["approval_required"] is False
        assert "no comparable baseline exists" in by_field[field]["remediation"]


def test_comparable_slurm_policy_drift_still_requires_remediation_approval() -> None:
    fields = ("accounting_associations", "accounting_qos", "slurm_partitions")
    before = ProtectedCustomerState(
        target_ref="gpu",
        namespace="soperator",
        captured_at="before",
        sections={
            "slurm_runtime": {
                field: {"available": True, "stdout_sha256": f"before-{field}"} for field in fields
            }
        },
    )
    after = ProtectedCustomerState(
        target_ref="gpu",
        namespace="soperator",
        captured_at="after",
        sections={
            "slurm_runtime": {
                field: {"available": True, "stdout_sha256": f"after-{field}"} for field in fields
            }
        },
    )

    comparison = compare_protected_customer_state(before=before, after=after)

    assert comparison["approval_required_count"] == len(fields)
    by_field = {delta["field"]: delta for delta in comparison["deltas"]}
    assert set(by_field) == set(fields)
    for field in fields:
        assert by_field[field]["classification"] == "remediation_required"
        assert by_field[field]["approval_required"] is True


@pytest.mark.parametrize(
    "before_enabled",
    (_OPEN_METRICS_UNSET, False),
    ids=("absent", "false"),
)
def test_external_open_metrics_restoration_is_intentional_with_exact_handoff_proof(
    before_enabled: object,
) -> None:
    before = _capture_open_metrics(_OpenMetricsRunner(before_enabled))

    result = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(True),
        target_ref="cluster",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        external_open_metrics_handoff=_restored_open_metrics_proof(),
    )

    assert result.status == "passed"
    open_metrics_delta = next(
        delta
        for delta in result.comparison["deltas"]
        if delta["field"] == "controller_open_metrics_enabled"
    )
    assert open_metrics_delta["after"] == "True"
    assert open_metrics_delta["classification"] == "intentional_upgrade"
    assert open_metrics_delta["approval_required"] is False
    by_field = {
        delta["field"]: delta
        for delta in result.comparison["deltas"]
        if delta["kind"] == "slurmclusters"
    }
    assert by_field["spec_hash"]["classification"] == "intentional_upgrade"
    if "spec_keys" in by_field:
        assert by_field["spec_keys"]["classification"] == "intentional_upgrade"
    assert "spec_hash_without_controller_open_metrics_enabled" not in by_field


@pytest.mark.parametrize(
    ("target_ref", "proof"),
    (
        ("other-target", _restored_open_metrics_proof()),
        ("cluster", {**_restored_open_metrics_proof(), "revision": 2}),
        ("cluster", {**_restored_open_metrics_proof(), "status": "compatibility-active"}),
        ("cluster", _restored_open_metrics_proof(desired_enabled=False)),
        (
            "cluster",
            {
                **_restored_open_metrics_proof(),
                "observed_enabled": False,
            },
        ),
        ("cluster", None),
    ),
    ids=(
        "wrong-target",
        "wrong-revision",
        "wrong-status",
        "wrong-desired",
        "wrong-observed",
        "missing-proof",
    ),
)
def test_external_open_metrics_restoration_stays_protected_without_exact_proof(
    target_ref: str,
    proof: dict[str, Any] | None,
) -> None:
    before = _capture_open_metrics(
        _OpenMetricsRunner(False),
        target_ref=target_ref,
    )

    result = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(True),
        target_ref=target_ref,
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        external_open_metrics_handoff=proof,
        remediation_approved=True,
    )

    assert result.status == "failed"
    delta = next(
        item
        for item in result.comparison["deltas"]
        if item["field"] == "controller_open_metrics_enabled"
    )
    assert delta["classification"] == "remediation_required"
    assert delta["approval_required"] is True
    assert any(
        check["name"] == "external-open-metrics-handoff" and check["status"] == "failed"
        for check in result.checks
    )


def test_external_open_metrics_proof_does_not_exempt_unrelated_slurmcluster_spec_drift() -> None:
    before = _capture_open_metrics(
        _OpenMetricsRunner(False, extra_spec={"customerSetting": "before"})
    )

    result = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(True, extra_spec={"customerSetting": "after"}),
        target_ref="cluster",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        external_open_metrics_handoff=_restored_open_metrics_proof(),
    )

    assert result.status == "failed"
    by_field = {
        delta["field"]: delta
        for delta in result.comparison["deltas"]
        if delta["kind"] == "slurmclusters"
    }
    assert by_field["controller_open_metrics_enabled"]["classification"] == ("intentional_upgrade")
    assert by_field["spec_hash"]["classification"] == "remediation_required"
    assert by_field["spec_hash"]["approval_required"] is True


@pytest.mark.parametrize(
    ("after_enabled", "after_uid", "proof_changes"),
    (
        (
            False,
            "slurmcluster-uid-cluster",
            {},
        ),
        (
            True,
            "replacement-slurmcluster-uid",
            {},
        ),
        (
            True,
            "slurmcluster-uid-cluster",
            {"restored_at": ""},
        ),
        (
            True,
            "slurmcluster-uid-cluster",
            {"post_restore_readiness_verified_at": ""},
        ),
    ),
    ids=(
        "no-delta-wrong-live-value",
        "wrong-live-uid",
        "missing-restored-at",
        "missing-readiness-at",
    ),
)
def test_external_open_metrics_after_state_proof_is_nonwaivable(
    after_enabled: bool,
    after_uid: str,
    proof_changes: dict[str, Any],
) -> None:
    before = _capture_open_metrics(_OpenMetricsRunner(False))
    proof = {**_restored_open_metrics_proof(), **proof_changes}

    result = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(after_enabled, resource_uid=after_uid),
        target_ref="cluster",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        external_open_metrics_handoff=proof,
        remediation_approved=True,
    )

    assert result.status == "failed"
    check = next(item for item in result.checks if item["name"] == "external-open-metrics-handoff")
    assert check["status"] == "failed"


def test_external_open_metrics_restore_keeps_legacy_whole_hash_baseline_protected() -> None:
    captured = _capture_open_metrics(_OpenMetricsRunner(False))
    legacy_sections = json.loads(json.dumps(captured.sections))
    legacy_item = legacy_sections["slurmclusters"]["items"][0]
    legacy_item.pop("controller_open_metrics_enabled")
    legacy_item.pop("spec_hash_without_controller_open_metrics_enabled")
    legacy_item.pop("resource_uid")
    legacy_item["spec_hash"] = "legacy-whole-slurmcluster-spec-hash"
    legacy_item["spec_keys"] = ["slurmNodes"]
    legacy_before = ProtectedCustomerState(
        target_ref=captured.target_ref,
        namespace=captured.namespace,
        captured_at=captured.captured_at,
        sections=legacy_sections,
    )

    result = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(True),
        target_ref="cluster",
        namespace="soperator",
        kube_context="external-context",
        before_state=legacy_before,
        external_cluster=True,
        external_open_metrics_handoff=_restored_open_metrics_proof(),
    )

    assert result.status == "failed"
    by_field = {
        delta["field"]: delta
        for delta in result.comparison["deltas"]
        if delta["kind"] == "slurmclusters"
    }
    assert by_field["controller_open_metrics_enabled"]["classification"] == ("intentional_upgrade")
    assert by_field["spec_hash"]["classification"] == "remediation_required"
    assert (
        by_field["spec_hash_without_controller_open_metrics_enabled"]["classification"]
        == "remediation_required"
    )


def test_external_open_metrics_legacy_baseline_without_handoff_uses_normal_remediation() -> None:
    captured = _capture_open_metrics(_OpenMetricsRunner(False))
    legacy_sections = json.loads(json.dumps(captured.sections))
    legacy_item = legacy_sections["slurmclusters"]["items"][0]
    legacy_item.pop("controller_open_metrics_enabled")
    legacy_item.pop("spec_hash_without_controller_open_metrics_enabled")
    legacy_item.pop("resource_uid")
    legacy_before = ProtectedCustomerState(
        target_ref=captured.target_ref,
        namespace=captured.namespace,
        captured_at=captured.captured_at,
        sections=legacy_sections,
    )

    result = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(True),
        target_ref="cluster",
        namespace="soperator",
        kube_context="external-context",
        before_state=legacy_before,
        external_cluster=True,
        remediation_approved=True,
    )

    assert result.status == "passed"
    open_metrics_check = next(
        item for item in result.checks if item["name"] == "external-open-metrics-handoff"
    )
    assert open_metrics_check["status"] == "skipped"
    by_field = {
        delta["field"]: delta
        for delta in result.comparison["deltas"]
        if delta["kind"] == "slurmclusters"
    }
    assert by_field["controller_open_metrics_enabled"]["classification"] == ("remediation_required")
    assert by_field["spec_hash"]["classification"] == "remediation_required"
    assert by_field["resource_uid"]["classification"] == "remediation_required"


def test_managed_chart_upgrade_classifies_chart_owned_deltas_as_intentional() -> None:
    before = _capture(_ManagedChartRunner("4.0.1-ps.2"))
    result = run_post_upgrade_fast_verification(
        command_runner=_ManagedChartRunner("4.0.2-ps.3"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        managed_chart_upgrade=True,
    )

    assert result.status == "passed"
    assert result.comparison["status"] == "intentional-upgrade"
    assert result.comparison["approval_required_count"] == 0
    assert any(
        delta["kind"] == "nodesets"
        and delta["field"] == "spec_hash"
        and delta["classification"] == "intentional_upgrade"
        for delta in result.comparison["deltas"]
    )


def test_managed_chart_upgrade_keeps_non_chart_nodeset_drift_protected() -> None:
    before = _capture(_Runner(nodeset_script="mount-home"))
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(nodeset_script="changed-mount-home"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        managed_chart_upgrade=True,
    )

    assert result.status == "failed"
    assert any(
        delta["kind"] == "nodesets"
        and delta["field"] == "spec_hash"
        and delta["classification"] == "remediation_required"
        for delta in result.comparison["deltas"]
    )


def test_managed_chart_upgrade_classifies_slurm_runtime_hash_deltas_as_intentional() -> None:
    before = _capture(_RuntimeOnlyRunner("before"))
    result = run_post_upgrade_fast_verification(
        command_runner=_RuntimeOnlyRunner("after"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        managed_chart_upgrade=True,
    )

    assert result.status == "passed"
    assert result.comparison["approval_required_count"] == 0
    assert {
        (delta["kind"], delta["field"], delta["classification"])
        for delta in result.comparison["deltas"]
    } >= {
        ("slurm_runtime", "slurm_config", "intentional_upgrade"),
        ("slurm_runtime", "slurm_nodes", "intentional_upgrade"),
    }


def test_zero_downtime_fails_when_slurm_workers_are_not_responsive() -> None:
    result = run_post_upgrade_fast_verification(
        command_runner=_UnresponsiveSlurmRunner(),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
    )

    assert result.status == "failed"
    assert result.zero_downtime_eligibility["eligible"] is False
    assert result.zero_downtime_eligibility["slurm_problem_nodes"] == [
        {"name": "worker-0", "state": "IDLE+CLOUD+NOT_RESPONDING"},
        {"name": "worker-1", "state": "DOWN+CLOUD"},
    ]
    assert any(
        check["name"] == "zero-downtime-eligibility" and check["status"] == "failed"
        for check in result.checks
    )


def test_managed_node_template_upgrade_classifies_node_replacement_as_intentional() -> None:
    before = _capture(_NodeRunner("node-old"))
    result = run_post_upgrade_fast_verification(
        command_runner=_NodeRunner("node-new"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        managed_node_template_upgrade=True,
    )

    assert result.status == "passed"
    assert result.comparison["status"] == "intentional-upgrade"
    assert result.comparison["approval_required_count"] == 0
    assert {
        (delta["kind"], delta["field"], delta["before"], delta["after"], delta["classification"])
        for delta in result.comparison["deltas"]
        if delta["kind"] == "nodes" and delta["field"] == "presence"
    } == {
        ("nodes", "presence", "present", "absent", "intentional_upgrade"),
        ("nodes", "presence", "absent", "present", "intentional_upgrade"),
    }


def test_managed_node_template_upgrade_keeps_node_label_drift_protected() -> None:
    before = _capture(_NodeRunner("node-1", node_labels={"nebius.com/node-group": "workers"}))
    result = run_post_upgrade_fast_verification(
        command_runner=_NodeRunner("node-1", node_labels={"nebius.com/node-group": "other"}),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        managed_node_template_upgrade=True,
    )

    assert result.status == "failed"
    assert any(
        delta["kind"] == "nodes"
        and delta["field"] == "labels"
        and delta["classification"] == "remediation_required"
        and delta["approval_required"] is True
        for delta in result.comparison["deltas"]
    )


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


def test_external_shared_jail_home_mount_change_is_intentional() -> None:
    before = _capture(_Runner(home_mount="nfs.example:/home on /home type nfs4 (rw)"))

    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(
            home_mount=(
                '{ "filesystems": [ { "target": "/mnt/jail/home", '
                '"source": "jail[/shared/home]", "fstype": "virtiofs" } ] }'
            )
        ),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    assert result.status == "passed"
    assert any(
        check["name"] == "home-mounted"
        and check["status"] == "passed"
        and "shared jail home" in check["summary"]
        for check in result.checks
    )


def test_activechecks_restored_allows_baseline_suspended_checks() -> None:
    before = _capture(
        _Runner(
            activecheck={
                "kind": "ActiveCheck",
                "metadata": {"name": "storage", "namespace": "soperator"},
                "spec": {"suspend": True},
                "status": {"phase": "Completed"},
            },
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
        ),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    assert not any(
        check["name"] == "activechecks-restored" and check["status"] == "failed"
        for check in result.checks
    )


def test_external_migration_classifier_allows_owned_cleanup_and_keeps_config_drift() -> None:
    comparison = {
        "schema": "nebius-cxcli-soperator-upgrade-safety/v1",
        "status": "drift-detected",
        "before_hash": "before",
        "after_hash": "after",
        "blocked_count": 0,
        "approval_required_count": 6,
        "deltas": [
            {
                "kind": "configmaps",
                "resource": "soperator/cxcli-ext-upg-1223b-slurm-configs",
                "field": "presence",
                "before": "absent",
                "after": "present",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
            {
                "kind": "secrets",
                "resource": "soperator/sh.helm.release.v1.soperator.v2",
                "field": "presence",
                "before": "absent",
                "after": "present",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
            {
                "kind": "slurmclusters",
                "resource": "soperator/cxcli-ext-upg-1223b",
                "field": "presence",
                "before": "absent",
                "after": "present",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
            {
                "kind": "flux.flux_system_kustomizations",
                "resource": "flux-system/flux-system",
                "field": "suspend",
                "before": False,
                "after": True,
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
            {
                "kind": "flux.flux_system_kustomizations",
                "resource": "flux-system/flux-system",
                "field": "spec_hash",
                "before": "old",
                "after": "new",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
            {
                "kind": "workloads.statefulsets",
                "resource": "soperator/soperator-acct-db",
                "field": "presence",
                "before": "present",
                "after": "absent",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this workload template drift.",
            },
            {
                "kind": "configmaps",
                "resource": "soperator/customer-config",
                "field": "spec_hash",
                "before": "old",
                "after": "new",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
        ],
    }

    classified = _classify_external_intentional_deltas(
        comparison,
        target_ref="cxcli-ext-upg-1223b",
    )

    by_resource = {item["resource"]: item for item in classified["deltas"]}
    assert (
        by_resource["soperator/cxcli-ext-upg-1223b-slurm-configs"]["classification"]
        == "intentional_upgrade"
    )
    assert by_resource["soperator/cxcli-ext-upg-1223b-slurm-configs"]["approval_required"] is False
    assert (
        by_resource["soperator/sh.helm.release.v1.soperator.v2"]["classification"]
        == "intentional_upgrade"
    )
    assert by_resource["soperator/cxcli-ext-upg-1223b"]["classification"] == "intentional_upgrade"
    assert by_resource["flux-system/flux-system"]["classification"] == "intentional_upgrade"
    assert by_resource["soperator/soperator-acct-db"]["classification"] == "intentional_upgrade"
    assert by_resource["soperator/customer-config"]["classification"] == "remediation_required"
    assert classified["approval_required_count"] == 1


def test_external_migration_classifier_keeps_unowned_flux_and_slurm_policy_drift_protected() -> (
    None
):
    comparison: dict[str, object] = {
        "schema": "nebius-cxcli-soperator-upgrade-safety/v1",
        "status": "drift-detected",
        "before_hash": "before",
        "after_hash": "after",
        "blocked_count": 0,
        "approval_required_count": 4,
        "deltas": [
            {
                "kind": "flux.helmreleases",
                "resource": "customer/customer-app",
                "field": "spec_hash",
                "before": "old",
                "after": "new",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Kubernetes resource drift.",
            },
            {
                "kind": "slurm_runtime",
                "resource": "slurm-runtime",
                "field": "accounting_qos",
                "before": "old",
                "after": "new",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Slurm runtime drift.",
            },
            {
                "kind": "slurm_runtime",
                "resource": "slurm-runtime",
                "field": "accounting_associations",
                "before": "old",
                "after": "new",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Slurm runtime drift.",
            },
            {
                "kind": "slurm_runtime",
                "resource": "slurm-runtime",
                "field": "slurm_partitions",
                "before": "old",
                "after": "new",
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review and approve this protected Slurm runtime drift.",
            },
        ],
    }

    classified = _classify_external_intentional_deltas(
        comparison,
        target_ref="cxcli-ext-upg-1223b",
    )

    assert classified["approval_required_count"] == 4
    assert classified["blocked_count"] == 0
    for delta in classified["deltas"]:
        assert delta["classification"] == "remediation_required"
        assert delta["approval_required"] is True


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


def test_stage_fast_verification_payload_and_report_defaults_are_canonical() -> None:
    passed = build_stage_fast_verification_payload(
        phase_id="soperator-chart",
        status="passed",
        summary="chart aligned",
        checks=[stage_fast_verification_check("chart version", "passed", "expected")],
        verified_at="2026-06-27T00:00:00Z",
    )
    failed = build_stage_fast_verification_payload(
        phase_id="postflight-validation",
        status="failed",
        summary="protected config changed",
        checks=[stage_fast_verification_check("config comparison", "failed", "drift")],
        verified_at="2026-06-27T00:00:01Z",
    )
    pending = build_stage_fast_verification_payload(
        phase_id="mk8s-node-template",
        status="skipped",
        summary="not selected",
        checks=[stage_fast_verification_check("node-template", "skipped", "not requested")],
        verified_at="2026-06-27T00:00:02Z",
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert pending["passed"] is None
    assert stage_fast_verification_status(passed["checks"]) == "passed"
    assert stage_fast_verification_status(failed["checks"]) == "failed"
    assert stage_fast_verification_status(pending["checks"]) == "skipped"
    assert stage_fast_verification_report("missing-stage", {}) == {
        "phase_id": "missing-stage",
        "status": "not_run",
        "passed": None,
        "summary": "No fast stage verification recorded.",
        "checks": [],
    }

    markdown = "\n".join(stage_fast_verification_markdown_lines([passed, failed, pending]))
    assert "`soperator-chart`: `PASS` - chart aligned" in markdown
    assert "`postflight-validation`: `FAIL` - protected config changed" in markdown
    assert "`mk8s-node-template`: `SKIP` - not selected" in markdown
