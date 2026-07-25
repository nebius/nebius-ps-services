from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from nebius_cxcli.soperator_upgrade_safety import (
    ProtectedCustomerState,
    _classify_external_intentional_deltas,
    _classify_managed_chart_upgrade_deltas,
    _classify_managed_node_template_upgrade_deltas,
    _external_open_metrics_comparison_hashes_match,
    _pvc_check,
    build_intentional_delta_proof,
    build_remediation_approval_plan,
    build_stage_fast_verification_payload,
    capture_protected_customer_state,
    checkpointed_remediation_approval_fingerprint,
    classify_intentional_deltas_from_proofs,
    compare_protected_customer_state,
    run_post_upgrade_fast_verification,
    stage_fast_verification_check,
    stage_fast_verification_markdown_lines,
    stage_fast_verification_report,
    stage_fast_verification_status,
)

_TEST_PROOF_CONTEXT = {
    "command_kind": "test-upgrade",
    "cluster_identity": "test-cluster",
    "checkpoint_identity": "test-checkpoint",
}


def _proof_builder_from_classifier(
    classifier: Any,
    *,
    phase_id: str = "test-operation",
) -> Any:
    def _build(
        before: ProtectedCustomerState,
        after: ProtectedCustomerState,
        comparison: dict[str, Any],
    ) -> list[dict[str, Any]]:
        classified = classifier(comparison, before, after)
        return [
            build_intentional_delta_proof(
                delta=delta,
                command_kind=_TEST_PROOF_CONTEXT["command_kind"],
                cluster_identity=_TEST_PROOF_CONTEXT["cluster_identity"],
                target_ref=before.target_ref,
                namespace=before.namespace,
                checkpoint_identity=_TEST_PROOF_CONTEXT["checkpoint_identity"],
                phase_id=phase_id,
                operation_fingerprint="a" * 64,
                baseline_hash=before.content_hash,
                resource_uid="test-resource-uid",
            )
            for delta in classified["deltas"]
            if delta["classification"] == "intentional_upgrade"
        ]

    return _build


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
        pod_restart_count: int = 0,
        pod_ready: bool | None = True,
        activecheck: dict[str, Any] | None = None,
        home_mount: str = "nfs.example:/home on /home type nfs4 (rw)",
    ) -> None:
        self.config_value = config_value
        self.nodeset_script = nodeset_script
        self.pvc_storage = pvc_storage
        self.include_accounting_pvc = include_accounting_pvc
        self.pod_phase = pod_phase
        self.waiting_reason = waiting_reason
        self.pod_restart_count = pod_restart_count
        self.pod_ready = pod_ready
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
                    {
                        "name": "login",
                        "restartCount": self.pod_restart_count,
                        "ready": self.pod_ready,
                        "state": container_state,
                    }
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


class _ConcurrentRunner(_Runner):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 120,
        check: bool = True,
    ) -> _Result:
        command = tuple(str(arg) for arg in args)
        if "exec" in command:
            return super().__call__(
                args,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            return super().__call__(
                args,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
            )
        finally:
            with self._lock:
                self.active -= 1


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


class _SlurmConfigBannerRunner(_Runner):
    def __init__(
        self,
        banner: str,
        *,
        config_token: str = "stable",
        protected_config_value: str = "baseline",
    ) -> None:
        super().__init__(config_value=protected_config_value)
        self.banner = banner
        self.config_token = config_token

    def _exec_output(self, command: Sequence[str]) -> str:
        joined = " ".join(command)
        if "show config" in joined:
            return (
                f"Configuration data as of {self.banner}\n"
                f"ClusterName=cluster\nConfigToken={self.config_token}\n"
            )
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


def test_slurm_config_capture_ignores_only_the_volatile_observation_banner() -> None:
    before = _capture(_SlurmConfigBannerRunner("2026-07-24T12:00:00"))
    after = _capture(_SlurmConfigBannerRunner("2026-07-24T12:00:01"))

    assert (
        before.sections["slurm_runtime"]["slurm_config"]
        == (after.sections["slurm_runtime"]["slurm_config"])
    )
    assert before.content_hash == after.content_hash
    assert compare_protected_customer_state(before=before, after=after)["status"] == "matched"


def test_slurm_config_capture_keeps_real_configuration_changes_protected() -> None:
    before = _capture(_SlurmConfigBannerRunner("2026-07-24T12:00:00", config_token="before"))
    after = _capture(_SlurmConfigBannerRunner("2026-07-24T12:00:01", config_token="after"))

    comparison = compare_protected_customer_state(before=before, after=after)
    delta = next(
        item
        for item in comparison["deltas"]
        if item["kind"] == "slurm_runtime" and item["field"] == "slurm_config"
    )
    assert delta["classification"] == "remediation_required"
    assert delta["approval_required"] is True


def test_remediation_fingerprint_is_stable_across_slurm_config_banner_changes() -> None:
    before = _capture(
        _SlurmConfigBannerRunner(
            "2026-07-24T12:00:00",
            protected_config_value="baseline",
        )
    )
    first = _capture(
        _SlurmConfigBannerRunner(
            "2026-07-24T12:00:01",
            protected_config_value="reviewed-drift",
        )
    )
    second = _capture(
        _SlurmConfigBannerRunner(
            "2026-07-24T12:00:02",
            protected_config_value="reviewed-drift",
        )
    )

    first_plan = build_remediation_approval_plan(
        compare_protected_customer_state(before=before, after=first)
    )
    second_plan = build_remediation_approval_plan(
        compare_protected_customer_state(before=before, after=second)
    )

    assert first_plan["status"] == "approval-required"
    assert first_plan["comparison_fingerprint"] == second_plan["comparison_fingerprint"]


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


def _external_handoff_proof_builder(
    *,
    target_ref: str,
    handoff: dict[str, Any] | None = None,
    handoff_verified: bool = False,
) -> Any:
    return _proof_builder_from_classifier(
        lambda comparison, before, after: _classify_external_intentional_deltas(
            comparison,
            target_ref=target_ref,
            before_state=before,
            after_state=after,
            open_metrics_handoff=handoff,
            open_metrics_handoff_verified=handoff_verified,
            open_metrics_comparison_hashes_match=(
                _external_open_metrics_comparison_hashes_match(
                    before_state=before,
                    after_state=after,
                    target_ref=target_ref,
                    namespace=before.namespace,
                )
            ),
        )
    )


def test_slurm_runtime_capture_uses_login_sshd_container() -> None:
    runner = _Runner()

    _capture(runner)

    exec_calls = [call for call in runner.calls if "exec" in call]
    assert exec_calls
    assert all("-c" in call and call[call.index("-c") + 1] == "sshd" for call in exec_calls)


def test_kubernetes_protected_state_probes_run_in_parallel_with_stable_audit_order() -> None:
    runner = _ConcurrentRunner()

    state = _capture(runner)

    assert runner.max_active > 1
    assert [item["section"] for item in state.command_audit[:16]] == [
        "pods",
        "pvcs",
        "pvs",
        "configmaps",
        "secrets",
        "deployments",
        "statefulsets",
        "daemonsets",
        "slurmclusters",
        "nodesets",
        "activechecks",
        "helmreleases",
        "flux-system.helmreleases",
        "kustomizations",
        "flux-system.kustomizations",
        "nodes",
    ]


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
        intentional_delta_proof_builder=_external_handoff_proof_builder(
            target_ref="cluster",
            handoff=_restored_open_metrics_proof(),
            handoff_verified=True,
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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
        intentional_delta_proof_builder=_external_handoff_proof_builder(
            target_ref="cluster",
            handoff=_restored_open_metrics_proof(),
            handoff_verified=True,
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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
        intentional_delta_proof_builder=_external_handoff_proof_builder(
            target_ref="cluster",
            handoff=_restored_open_metrics_proof(),
            handoff_verified=True,
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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
    )

    assert result.status == "failed"
    approval_plan = build_remediation_approval_plan(result.comparison)
    approved = run_post_upgrade_fast_verification(
        command_runner=_OpenMetricsRunner(True),
        target_ref="cluster",
        namespace="soperator",
        kube_context="external-context",
        before_state=legacy_before,
        external_cluster=True,
        approved_remediation_fingerprint=approval_plan["comparison_fingerprint"],
    )
    assert approved.status == "passed"
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
        command_runner=_ManagedChartRunner("4.0.2-ps.4"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_proof_builder_from_classifier(
            lambda comparison, _before, _after: _classify_managed_chart_upgrade_deltas(comparison)
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_proof_builder_from_classifier(
            lambda comparison, _before, _after: _classify_managed_chart_upgrade_deltas(comparison)
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_proof_builder_from_classifier(
            lambda comparison, _before, _after: _classify_managed_chart_upgrade_deltas(comparison)
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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


def test_zero_downtime_reports_planned_controller_gap_without_failing_health() -> None:
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        external_cluster=True,
        external_transition_handoff={
            "controller_continuity_mode": "cold-boundary-observed",
            "controller_outage_boundaries": [
                {
                    "kind": "bridge-to-target-singleton-fence",
                    "service": "slurm-controller",
                    "planned": True,
                    "started_at": "2026-07-24T00:27:47Z",
                    "ended_at": "2026-07-24T00:41:10Z",
                    "complete": True,
                }
            ],
        },
    )

    assert result.status == "passed"
    assert result.zero_downtime_eligibility["eligible"] is False
    assert result.zero_downtime_eligibility["blocking_reasons"] == []
    assert result.zero_downtime_eligibility["controller_continuity_mode"] == (
        "cold-boundary-observed"
    )
    assert any(
        check["name"] == "zero-downtime-eligibility" and check["status"] == "skipped"
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
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_proof_builder_from_classifier(
            lambda comparison, _before, _after: _classify_managed_node_template_upgrade_deltas(
                comparison
            )
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_proof_builder_from_classifier(
            lambda comparison, _before, _after: _classify_managed_node_template_upgrade_deltas(
                comparison
            )
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
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


def test_fast_verification_accepts_historical_restarts_when_pod_is_currently_ready() -> None:
    before = _capture(_Runner())
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(pod_restart_count=17, pod_ready=True),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    pod_check = next(
        check for check in result.checks if check["name"] == "pods-running-or-completed"
    )
    assert pod_check["status"] == "passed"
    assert "Historical high restart count" in pod_check["summary"]


def test_fast_verification_rejects_running_pod_when_container_is_not_ready() -> None:
    before = _capture(_Runner())
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(pod_restart_count=17, pod_ready=False),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    pod_check = next(
        check for check in result.checks if check["name"] == "pods-running-or-completed"
    )
    assert pod_check["status"] == "failed"


def test_fast_verification_accepts_succeeded_pod_when_container_is_not_ready() -> None:
    before = _capture(_Runner())
    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(pod_phase="Succeeded", pod_ready=False),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )

    pod_check = next(
        check for check in result.checks if check["name"] == "pods-running-or-completed"
    )
    assert pod_check["status"] == "passed"


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

    approval_plan = build_remediation_approval_plan(unapproved.comparison)
    approved = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="changed"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        approved_remediation_fingerprint=approval_plan["comparison_fingerprint"],
    )

    assert approved.status == "passed"
    assert any(
        check["name"] == "protected-state-comparison"
        and check["status"] == "passed"
        and "remediation-approved" in check["summary"]
        for check in approved.checks
    )


def test_stale_remediation_fingerprint_does_not_approve_fresh_drift() -> None:
    before = _capture(_Runner(config_value="baseline"))
    first = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="first-drift"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )
    stale_fingerprint = build_remediation_approval_plan(first.comparison)["comparison_fingerprint"]

    changed = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="different-drift"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        approved_remediation_fingerprint=stale_fingerprint,
    )

    assert changed.status == "failed"
    assert build_remediation_approval_plan(changed.comparison)["comparison_fingerprint"] != (
        stale_fingerprint
    )


def test_checkpointed_remediation_approval_consumes_only_the_persisted_comparison() -> None:
    before = _capture(_Runner(config_value="baseline"))
    observed = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="first-drift"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )
    plan = build_remediation_approval_plan(observed.comparison)
    safety_payload = {
        "post_upgrade_verification": {"comparison": observed.comparison},
        "remediation_approval_plan": plan,
    }

    assert (
        checkpointed_remediation_approval_fingerprint(
            safety_payload,
            approval_requested=False,
        )
        is None
    )
    assert (
        checkpointed_remediation_approval_fingerprint(
            safety_payload,
            approval_requested=True,
        )
        == plan["comparison_fingerprint"]
    )

    changed = run_post_upgrade_fast_verification(
        command_runner=_Runner(config_value="different-drift"),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
    )
    stale_payload = {
        **safety_payload,
        "post_upgrade_verification": {"comparison": changed.comparison},
    }
    with pytest.raises(ValueError, match="fingerprint is inconsistent"):
        checkpointed_remediation_approval_fingerprint(
            stale_payload,
            approval_requested=True,
        )


def test_intentional_proof_is_bound_to_one_exact_after_digest() -> None:
    before = _capture(_Runner(config_value="baseline"))
    first_after = _capture(_Runner(config_value="first-drift"))
    first_comparison = compare_protected_customer_state(before=before, after=first_after)
    first_delta = next(
        delta
        for delta in first_comparison["deltas"]
        if delta["kind"] == "configmaps" and delta["field"] == "data_sha256_by_key"
    )
    proof = build_intentional_delta_proof(
        delta=first_delta,
        command_kind="test-upgrade",
        cluster_identity="test-cluster",
        target_ref="gpu",
        namespace="soperator",
        checkpoint_identity="test-checkpoint",
        phase_id="test-operation",
        operation_fingerprint="a" * 64,
        baseline_hash=before.content_hash,
        resource_uid="test-resource-uid",
    )
    second_after = _capture(_Runner(config_value="different-drift"))
    second_comparison = compare_protected_customer_state(before=before, after=second_after)

    with pytest.raises(ValueError, match="does not match one current exact delta"):
        classify_intentional_deltas_from_proofs(
            second_comparison,
            proofs=[proof],
            target_ref="gpu",
            namespace="soperator",
            expected_context=_TEST_PROOF_CONTEXT,
        )


def test_blocked_delta_cannot_be_consumed_as_intentional_proof() -> None:
    before = _capture(_Runner(include_accounting_pvc=True))
    after = _capture(_Runner(include_accounting_pvc=False))
    comparison = compare_protected_customer_state(before=before, after=after)
    blocked = next(delta for delta in comparison["deltas"] if delta["classification"] == "blocked")
    proof = build_intentional_delta_proof(
        delta=blocked,
        command_kind="test-upgrade",
        cluster_identity="test-cluster",
        target_ref="gpu",
        namespace="soperator",
        checkpoint_identity="test-checkpoint",
        phase_id="test-operation",
        operation_fingerprint="a" * 64,
        baseline_hash=before.content_hash,
        resource_uid="test-resource-uid",
    )

    with pytest.raises(ValueError, match="does not match one current exact delta"):
        classify_intentional_deltas_from_proofs(
            comparison,
            proofs=[proof],
            target_ref="gpu",
            namespace="soperator",
            expected_context=_TEST_PROOF_CONTEXT,
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
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_external_handoff_proof_builder(
            target_ref="gpu",
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
    )

    assert result.status == "passed"
    assert any(
        check["name"] == "home-mounted"
        and check["status"] == "passed"
        and "shared jail home" in check["summary"]
        for check in result.checks
    )


def test_external_jail_home_subpath_mount_change_is_intentional() -> None:
    before = _capture(_Runner(home_mount='{"filesystems":[{"target":"/mnt/jail"}]}'))

    result = run_post_upgrade_fast_verification(
        command_runner=_Runner(
            home_mount=(
                '{ "filesystems": [ { "target": "/mnt/jail/home", '
                '"source": "jail[/home]", "fstype": "virtiofs" } ] }'
            )
        ),
        target_ref="gpu",
        namespace="soperator",
        kube_context="external-context",
        before_state=before,
        external_cluster=True,
        source_payload={"deploy": {"targets": [{"instance_id": "gpu"}]}},
        intentional_delta_proof_builder=_external_handoff_proof_builder(
            target_ref="gpu",
        ),
        intentional_delta_proof_context=_TEST_PROOF_CONTEXT,
    )

    assert result.status == "passed"
    assert any(
        check["name"] == "home-mounted" and check["status"] == "passed" for check in result.checks
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


def test_external_verified_jail_successor_replaces_retired_legacy_pvc() -> None:
    def state(names: list[str]) -> ProtectedCustomerState:
        return ProtectedCustomerState(
            target_ref="soperator-cluster",
            namespace="soperator",
            captured_at="2026-07-19T00:00:00Z",
            sections={
                "pvcs": {
                    "available": True,
                    "items": [
                        {
                            "kind": "PersistentVolumeClaim",
                            "namespace": "soperator",
                            "name": name,
                            "phase": "Bound",
                            "request_storage": "512Gi",
                        }
                        for name in names
                    ],
                }
            },
        )

    before = state(["jail-pvc", "jail-rootfs-slot-b-pvc"])
    after = state(["jail-rootfs-slot-b-pvc"])
    handoff = {
        "completed_at": "2026-07-19T00:01:00Z",
        "legacy_jail_pvc": "jail-pvc",
        "rootfs_handoff_verification": {
            "status": "verified",
            "active_pvc": "jail-rootfs-slot-b-pvc",
        },
    }

    check = _pvc_check(before, after, external_jail_storage_handoff=handoff)
    classified = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=after),
        target_ref="soperator-cluster",
        before_state=before,
        after_state=after,
        jail_storage_handoff=handoff,
    )

    assert check["status"] == "passed"
    delta = classified["deltas"][0]
    assert delta["classification"] == "intentional_upgrade"
    assert delta["approval_required"] is False
    assert classified["blocked_count"] == 0


def test_external_verified_bridge_transition_classifies_only_owned_temporary_drift() -> None:
    transition = {
        "status": "verified",
        "stage": "source-ha-active",
        "authority_owner": "bridge-source",
        "manager_pause_status": "verified",
        "client_propagation_status": "verified",
        "partition_pause_verified": True,
    }
    comparison = {
        "schema": "nebius-cxcli-soperator-upgrade-safety/v1",
        "status": "drift-detected",
        "before_hash": "before",
        "after_hash": "after",
        "blocked_count": 0,
        "approval_required_count": 2,
        "deltas": [
            {
                "kind": "slurm_runtime",
                "resource": "slurm-runtime",
                "field": "slurm_partitions",
                "before": "before",
                "after": "after",
                "classification": "remediation_required",
                "approval_required": True,
            },
            {
                "kind": "configmaps",
                "resource": "soperator/customer-config",
                "field": "data_sha256_by_key",
                "before": "before",
                "after": "after",
                "classification": "remediation_required",
                "approval_required": True,
            },
        ],
    }

    classified = _classify_external_intentional_deltas(
        comparison,
        target_ref="soperator-cluster",
        transition_handoff=transition,
    )

    by_resource = {delta["resource"]: delta for delta in classified["deltas"]}
    assert by_resource["slurm-runtime"]["classification"] == "intentional_upgrade"
    assert by_resource["soperator/customer-config"]["classification"] == ("remediation_required")
    assert classified["approval_required_count"] == 1


def _terminal_transition_handoff() -> dict[str, Any]:
    material = {
        "schema": "nebius-cxcli/external-terminal-transition-proof/v1",
        "status": "verified",
        "target_ref": "soperator-cluster",
        "target_uid": "target-slurmcluster-uid",
        "checkpoint_identity": "a" * 64,
        "terminal_stage": "cleaned",
        "terminal_authority_owner": "target-singleton",
        "source_transition": {
            "authority_history_fingerprint": "b" * 64,
            "manager_pause_fingerprint": "c" * 64,
            "source_configuration_fingerprint": "d" * 64,
            "client_propagation_fingerprint": "e" * 64,
        },
        "target_transition": {
            "takeover_fingerprint": "f" * 64,
            "cleanup_fingerprint": "1" * 64,
        },
        "chart_operation_fingerprint": "2" * 64,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return {
        "status": "verified",
        "stage": "cleaned",
        "authority_owner": "target-singleton",
        "historical_transition": {
            **material,
            "proof_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        },
    }


def test_external_terminal_transition_reuses_only_exact_historical_owned_deltas() -> None:
    comparison = {
        "schema": "nebius-cxcli-soperator-upgrade-safety/v1",
        "status": "drift-detected",
        "before_hash": "before",
        "after_hash": "after",
        "blocked_count": 0,
        "approval_required_count": 4,
        "deltas": [
            {
                "kind": "configmaps",
                "resource": "soperator/soperator-cluster-slurm-configs",
                "field": "data_sha256_by_key",
                "before": "before",
                "after": "after",
            },
            {
                "kind": "secrets",
                "resource": "soperator/sh.helm.release.v1.soperator.v7",
                "field": "data_sha256_by_key",
                "before": "before",
                "after": "after",
            },
            {
                "kind": "secrets",
                "resource": "soperator/sh.helm.release.v1.soperator.v7",
                "field": "labels",
                "before": "before",
                "after": "after",
            },
            {
                "kind": "configmaps",
                "resource": "soperator/customer-config",
                "field": "data_sha256_by_key",
                "before": "before",
                "after": "after",
            },
        ],
    }
    for delta in comparison["deltas"]:
        delta.update(
            {
                "classification": "remediation_required",
                "approval_required": True,
                "remediation": "Review protected drift.",
            }
        )

    classified = _classify_external_intentional_deltas(
        comparison,
        target_ref="soperator-cluster",
        transition_handoff=_terminal_transition_handoff(),
    )

    intentional = {
        (delta["kind"], delta["resource"], delta["field"])
        for delta in classified["deltas"]
        if delta["classification"] == "intentional_upgrade"
    }
    assert intentional == {
        (
            "configmaps",
            "soperator/soperator-cluster-slurm-configs",
            "data_sha256_by_key",
        ),
        (
            "secrets",
            "soperator/sh.helm.release.v1.soperator.v7",
            "data_sha256_by_key",
        ),
        ("secrets", "soperator/sh.helm.release.v1.soperator.v7", "labels"),
    }
    assert classified["approval_required_count"] == 1


@pytest.mark.parametrize(
    ("mutation", "resource"),
    [
        ("fingerprint", "soperator/soperator-cluster-slurm-configs"),
        ("target", "soperator/soperator-cluster-slurm-configs"),
        ("secret-name", "soperator/sh.helm.release.v1.soperator.v7.invalid"),
    ],
)
def test_external_terminal_transition_rejects_tampered_or_unowned_proof(
    mutation: str,
    resource: str,
) -> None:
    handoff = _terminal_transition_handoff()
    historical = handoff["historical_transition"]
    if mutation == "fingerprint":
        historical["proof_sha256"] = "0" * 64
    elif mutation == "target":
        historical["target_ref"] = "different-cluster"
    comparison = {
        "schema": "nebius-cxcli-soperator-upgrade-safety/v1",
        "status": "drift-detected",
        "before_hash": "before",
        "after_hash": "after",
        "blocked_count": 0,
        "approval_required_count": 1,
        "deltas": [
            {
                "kind": "secrets" if mutation == "secret-name" else "configmaps",
                "resource": resource,
                "field": ("labels" if mutation == "secret-name" else "data_sha256_by_key"),
                "before": "before",
                "after": "after",
            }
        ],
    }
    comparison["deltas"][0].update(
        {
            "classification": "remediation_required",
            "approval_required": True,
            "remediation": "Review protected drift.",
        }
    )

    classified = _classify_external_intentional_deltas(
        comparison,
        target_ref="soperator-cluster",
        transition_handoff=handoff,
    )

    assert classified["deltas"][0]["classification"] == "remediation_required"
    assert classified["approval_required_count"] == 1


def test_external_accounting_config_delta_requires_exact_target_successor_contract() -> None:
    target_ref = "cxcli-ext-upg-1223b"

    def state(*, legacy_hash: str, successor_hash: str | None) -> ProtectedCustomerState:
        items = [
            {
                "kind": "ConfigMap",
                "namespace": "soperator",
                "name": "soperator-acct-db-config-default",
                "data_keys": ["0-default.cnf"],
                "binary_data_keys": [],
                "data_sha256_by_key": {"0-default.cnf": legacy_hash},
                "binary_data_sha256_by_key": {},
            }
        ]
        if successor_hash is not None:
            items.append(
                {
                    "kind": "ConfigMap",
                    "namespace": "soperator",
                    "name": f"{target_ref}-acct-db-config-default",
                    "data_keys": ["0-default.cnf"],
                    "binary_data_keys": [],
                    "data_sha256_by_key": {"0-default.cnf": successor_hash},
                    "binary_data_sha256_by_key": {},
                }
            )
        return ProtectedCustomerState(
            target_ref=target_ref,
            namespace="soperator",
            captured_at="2026-07-18T00:00:00Z",
            sections={
                "configmaps": {
                    "available": True,
                    "count": len(items),
                    "items": items,
                }
            },
        )

    before = state(legacy_hash="old", successor_hash=None)
    matching_after = state(legacy_hash="new", successor_hash="new")
    classified = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=matching_after),
        target_ref=target_ref,
        before_state=before,
        after_state=matching_after,
    )
    matching_delta = next(
        delta
        for delta in classified["deltas"]
        if delta["resource"] == "soperator/soperator-acct-db-config-default"
        and delta["field"] == "data_sha256_by_key"
    )
    assert matching_delta["classification"] == "intentional_upgrade"
    assert matching_delta["approval_required"] is False

    mismatched_after = state(legacy_hash="new", successor_hash="different")
    mismatched = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=mismatched_after),
        target_ref=target_ref,
        before_state=before,
        after_state=mismatched_after,
    )
    mismatched_delta = next(
        delta
        for delta in mismatched["deltas"]
        if delta["resource"] == "soperator/soperator-acct-db-config-default"
        and delta["field"] == "data_sha256_by_key"
    )
    assert mismatched_delta["classification"] == "remediation_required"
    assert mismatched_delta["approval_required"] is True


def test_external_accounting_config_delta_accepts_retired_legacy_reconciliation() -> None:
    target_ref = "cxcli-ext-upg-1223b"

    def state(
        *,
        legacy_hash: str,
        successor_hash: str | None,
        statefulsets: list[str],
    ) -> ProtectedCustomerState:
        configmaps = [
            {
                "kind": "ConfigMap",
                "namespace": "soperator",
                "name": "soperator-acct-db-config-default",
                "data_sha256_by_key": {"0-default.cnf": legacy_hash},
            }
        ]
        if successor_hash is not None:
            configmaps.append(
                {
                    "kind": "ConfigMap",
                    "namespace": "soperator",
                    "name": f"{target_ref}-acct-db-config-default",
                    "data_sha256_by_key": {"0-default.cnf": successor_hash},
                }
            )
        return ProtectedCustomerState(
            target_ref=target_ref,
            namespace="soperator",
            captured_at="2026-07-18T00:00:00Z",
            sections={
                "configmaps": {"available": True, "items": configmaps},
                "workloads": {
                    "statefulsets": {
                        "available": True,
                        "items": [
                            {
                                "kind": "StatefulSet",
                                "namespace": "soperator",
                                "name": name,
                            }
                            for name in statefulsets
                        ],
                    }
                },
            },
        )

    before = state(
        legacy_hash="protected",
        successor_hash=None,
        statefulsets=["soperator-acct-db"],
    )
    retired_after = state(
        legacy_hash="reconciled",
        successor_hash="protected",
        statefulsets=[f"{target_ref}-acct-db"],
    )
    classified = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=retired_after),
        target_ref=target_ref,
        before_state=before,
        after_state=retired_after,
    )
    matching_delta = next(
        delta
        for delta in classified["deltas"]
        if delta["resource"] == "soperator/soperator-acct-db-config-default"
        and delta["field"] == "data_sha256_by_key"
    )
    assert matching_delta["classification"] == "intentional_upgrade"
    assert matching_delta["approval_required"] is False

    source_still_live = state(
        legacy_hash="reconciled",
        successor_hash="protected",
        statefulsets=["soperator-acct-db", f"{target_ref}-acct-db"],
    )
    rejected = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=source_still_live),
        target_ref=target_ref,
        before_state=before,
        after_state=source_still_live,
    )
    rejected_delta = next(
        delta
        for delta in rejected["deltas"]
        if delta["resource"] == "soperator/soperator-acct-db-config-default"
        and delta["field"] == "data_sha256_by_key"
    )
    assert rejected_delta["classification"] == "remediation_required"
    assert rejected_delta["approval_required"] is True


def test_external_accounting_target_config_delta_accepts_verified_takeover() -> None:
    target_ref = "cxcli-ext-upg-1223b"

    def state(*, successor_hash: str, source_live: bool) -> ProtectedCustomerState:
        statefulsets = [f"{target_ref}-acct-db"]
        if source_live:
            statefulsets.append("soperator-acct-db")
        return ProtectedCustomerState(
            target_ref=target_ref,
            namespace="soperator",
            captured_at="2026-07-18T00:00:00Z",
            sections={
                "configmaps": {
                    "available": True,
                    "items": [
                        {
                            "kind": "ConfigMap",
                            "namespace": "soperator",
                            "name": "soperator-acct-db-config-default",
                            "data_sha256_by_key": {"0-default.cnf": "protected"},
                        },
                        {
                            "kind": "ConfigMap",
                            "namespace": "soperator",
                            "name": f"{target_ref}-acct-db-config-default",
                            "data_sha256_by_key": {"0-default.cnf": successor_hash},
                        },
                    ],
                },
                "workloads": {
                    "statefulsets": {
                        "available": True,
                        "items": [
                            {
                                "kind": "StatefulSet",
                                "namespace": "soperator",
                                "name": name,
                            }
                            for name in statefulsets
                        ],
                    }
                },
            },
        )

    before = state(successor_hash="protected", source_live=True)
    after = state(successor_hash="operator-default", source_live=False)
    classified = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=after),
        target_ref=target_ref,
        before_state=before,
        after_state=after,
    )
    target_delta = next(
        delta
        for delta in classified["deltas"]
        if delta["resource"] == f"soperator/{target_ref}-acct-db-config-default"
    )
    assert target_delta["classification"] == "intentional_upgrade"
    assert target_delta["approval_required"] is False

    source_still_live = state(successor_hash="operator-default", source_live=True)
    rejected = _classify_external_intentional_deltas(
        compare_protected_customer_state(before=before, after=source_still_live),
        target_ref=target_ref,
        before_state=before,
        after_state=source_still_live,
    )
    rejected_delta = next(
        delta
        for delta in rejected["deltas"]
        if delta["resource"] == f"soperator/{target_ref}-acct-db-config-default"
    )
    assert rejected_delta["classification"] == "remediation_required"


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
