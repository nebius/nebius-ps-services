from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_migration import SoperatorMigrationCommandResult


def _fence_state() -> dict[str, Any]:
    frozen_resources = [
        {
            "api_path": (
                "/apis/slurm.nebius.ai/v1/namespaces/soperator/slurmclusters/source-cluster"
            ),
            "api_version": "slurm.nebius.ai/v1",
            "kind": "SlurmCluster",
            "namespace": "soperator",
            "name": "source-cluster",
            "uid": "source-uid",
            "resource_version": "41",
            "labels": {"app.kubernetes.io/instance": "source-cluster"},
        },
        {
            "api_path": "/apis/apps/v1/namespaces/soperator/statefulsets/worker",
            "api_version": "apps/v1",
            "kind": "StatefulSet",
            "namespace": "soperator",
            "name": "worker",
            "uid": "worker-uid",
            "resource_version": "42",
            "labels": {
                "app.kubernetes.io/name": "slurmcluster",
                "app.kubernetes.io/instance": "source-cluster",
            },
        },
    ]
    state: dict[str, Any] = {
        "schema": migration._SOURCE_RECONCILIATION_FENCE_SCHEMA,  # noqa: SLF001
        "status": "verified",
        "operation_id": "fence-operation",
        "source": {
            "namespace": "soperator",
            "name": "source-cluster",
            "uid": "source-uid",
        },
        "manager_service_account": {
            "namespace": "soperator",
            "name": "soperator-manager",
            "username": "system:serviceaccount:soperator:soperator-manager",
        },
        "resource_names": migration._source_reconciliation_fence_names("source-uid"),  # noqa: SLF001
        "frozen_resources": frozen_resources,
        "closure_sha256": migration._fingerprint(  # noqa: SLF001
            migration._source_reconciliation_fence_stable_closure(  # noqa: SLF001
                frozen_resources
            )
        ),
        "managed_resources_sha256": "pending",
        "canary": {"status": "denied-by-policy"},
    }
    desired = migration._source_reconciliation_fence_resources(state=state)  # noqa: SLF001
    state["managed_resources_sha256"] = migration._fingerprint(desired)  # noqa: SLF001
    state["managed_resource_bindings"] = [
        {
            "api_version": item["apiVersion"],
            "kind": item["kind"],
            "namespace": str(item["metadata"].get("namespace") or ""),
            "name": item["metadata"]["name"],
            "uid": f"{item['kind'].lower()}-uid",
            "resource_version": "7",
        }
        for item in desired
    ]
    return state


def test_source_fence_policy_uses_v1_match_resources_schema() -> None:
    state = _fence_state()

    policy = next(
        item
        for item in migration._source_reconciliation_fence_resources(state=state)  # noqa: SLF001
        if item["kind"] == "ValidatingAdmissionPolicy"
    )

    assert "matchPolicy" not in policy["spec"]
    assert policy["spec"]["matchConstraints"]["matchPolicy"] == "Equivalent"


def test_source_fence_canary_requires_admission_policy_attribution() -> None:
    state = _fence_state()

    def rbac_runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        return SoperatorMigrationCommandResult(
            tuple(args),
            1,
            "",
            'Error from server (Forbidden): user cannot patch resource "slurmclusters" (RBAC)',
        )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="was not denied by the exact admission policy",
    ):
        migration._source_reconciliation_fence_canary(  # noqa: SLF001
            state=state,
            command_runner=rbac_runner,
            kube_context="context",
        )

    policy_name = state["resource_names"]["policy"]

    def policy_runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        return SoperatorMigrationCommandResult(
            tuple(args),
            1,
            "",
            (
                f"ValidatingAdmissionPolicy {policy_name} denied request: {policy_name} "
                "denied target-manager mutation of frozen source state"
            ),
        )

    response_sha256 = migration._source_reconciliation_fence_canary(  # noqa: SLF001
        state=state,
        command_runner=policy_runner,
        kube_context="context",
    )
    assert len(response_sha256) == 64


def test_source_fence_cleanup_api_paths_are_narrowly_allowlisted() -> None:
    state = _fence_state()
    for identity in state["managed_resource_bindings"]:
        api_path = migration._source_reconciliation_fence_delete_api_path(identity)  # noqa: SLF001
        assert migration._uid_preconditioned_delete_api_path_is_supported(api_path)  # noqa: SLF001

    assert not migration._uid_preconditioned_delete_api_path_is_supported(  # noqa: SLF001
        "/apis/admissionregistration.k8s.io/v1/validatingadmissionpolicies/user-policy"
    )


def test_source_fence_identity_never_journals_secret_payloads() -> None:
    record = migration._source_reconciliation_fence_identity(  # noqa: SLF001
        {
            "api_path": "/api/v1/namespaces/soperator/secrets/munge-key",
            "identity": {
                "namespace": "soperator",
                "name": "munge-key",
                "uid": "secret-uid",
                "resourceVersion": "9",
            },
            "resource": {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "namespace": "soperator",
                    "name": "munge-key",
                    "uid": "secret-uid",
                    "resourceVersion": "9",
                    "labels": {
                        "app.kubernetes.io/name": "slurmcluster",
                        "app.kubernetes.io/instance": "source-cluster",
                        "private.example/token": "must-not-be-journaled",
                    },
                },
                "data": {"munge.key": "do-not-persist"},
            },
        }
    )

    assert record["labels"] == {
        "app.kubernetes.io/name": "slurmcluster",
        "app.kubernetes.io/instance": "source-cluster",
    }
    assert "data" not in record
    assert "private.example/token" not in record["labels"]


def test_source_fence_inventory_uses_discovered_canonical_mariadb_resource() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        calls.append(command)
        if "api-resources" in command:
            return SoperatorMigrationCommandResult(
                command,
                0,
                "pods\nconfigmaps\nsecrets\nservices\nmariadbs.k8s.mariadb.com\n",
                "",
            )
        resource_type = command[command.index("get") + 1]
        items: list[dict[str, Any]] = []
        if resource_type == "mariadbs.k8s.mariadb.com":
            items = [
                {
                    "apiVersion": "k8s.mariadb.com/v1alpha1",
                    "kind": "MariaDB",
                    "metadata": {
                        "namespace": "soperator",
                        "name": "source-db",
                        "uid": "db-uid",
                        "resourceVersion": "3",
                    },
                }
            ]
        return SoperatorMigrationCommandResult(
            command,
            0,
            json.dumps({"apiVersion": "v1", "kind": "List", "items": items}),
            "",
        )

    inventory = migration._source_reconciliation_fence_inventory(  # noqa: SLF001
        command_runner=runner,
        kube_context="context",
    )

    assert [item["resource"]["kind"] for item in inventory] == ["MariaDB"]
    assert inventory[0]["api_path"].endswith("/mariadbs/source-db")
    assert any("mariadbs.k8s.mariadb.com" in command for command in calls)
    assert not any("mariadb.k8s.mariadb.com" in command for command in calls)


def test_source_fence_install_is_checkpointed_and_resume_revalidates_without_reapply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = _fence_state()
    live_closure = tuple(template["frozen_resources"])
    checkpoint: dict[str, Any] = {
        "source_slurmcluster_ref": copy.deepcopy(template["source"]),
        "controller_bridge": {
            "stage": migration.BridgeStage.SOURCE_HA_ACTIVE.value,
            "authority": {"owner": "bridge-source"},
        },
    }
    phase: dict[str, Any] = {}
    live_by_kind: dict[str, dict[str, Any]] = {}
    writes: list[dict[str, Any]] = []
    apply_calls = 0
    canary_calls = 0

    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_live_closure",
        lambda **_kwargs: live_closure,
    )

    def get_managed(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        kind = str(kwargs["desired"]["kind"])
        live = live_by_kind.get(kind)
        return (live is not None, copy.deepcopy(live or {}))

    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_get_managed_resource",
        get_managed,
    )

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        nonlocal apply_calls, canary_calls
        del timeout_seconds, check
        command = tuple(str(item) for item in args)
        if "apply" in command:
            apply_calls += 1
            manifest = migration.yaml.safe_load(input_text or "")
            for index, desired in enumerate(manifest["items"], start=1):
                live = copy.deepcopy(desired)
                live["metadata"]["uid"] = f"managed-{index}"
                live["metadata"]["resourceVersion"] = "1"
                if desired["kind"] == "ValidatingAdmissionPolicy":
                    live["status"] = {"typeChecking": {"expressionWarnings": []}}
                live_by_kind[desired["kind"]] = live
            return SoperatorMigrationCommandResult(command, 0, "applied", "")
        if "patch" in command:
            canary_calls += 1
            policy_name = phase["source_reconciliation_fence"]["resource_names"]["policy"]
            return SoperatorMigrationCommandResult(
                command,
                1,
                "",
                (
                    f"ValidatingAdmissionPolicy {policy_name}: {policy_name} denied "
                    "target-manager mutation of frozen source state"
                ),
            )
        pytest.fail(f"unexpected command: {command}")

    first_lines = migration._ensure_safe_fresh_target_helm_ordering(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        source_report={},
        target_ref="target-cluster",
        command_runner=runner,
        kube_context="context",
        checkpoint_writer=lambda: writes.append(copy.deepcopy(phase)),
    )
    state = phase["source_reconciliation_fence"]
    operation_id = state["operation_id"]
    managed_uids = [item["uid"] for item in state["managed_resource_bindings"]]

    assert state["status"] == "verified"
    assert state["canary"]["status"] == "denied-by-policy"
    assert apply_calls == 1
    assert canary_calls == 1
    assert writes
    assert "fail-closed ValidatingAdmissionPolicy" in first_lines[0]

    migration._ensure_safe_fresh_target_helm_ordering(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        source_report={},
        target_ref="target-cluster",
        command_runner=runner,
        kube_context="context",
        checkpoint_writer=lambda: writes.append(copy.deepcopy(phase)),
    )

    assert state["operation_id"] == operation_id
    assert [item["uid"] for item in state["managed_resource_bindings"]] == managed_uids
    assert apply_calls == 1
    assert canary_calls == 2


def test_source_fence_reseals_changed_apply_intent_only_while_resources_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _fence_state()
    state["status"] = "apply-intent"
    state["managed_resources_sha256"] = "invalid-pre-apply-contract"
    state["managed_resource_bindings"] = []
    checkpoint = {
        "source_slurmcluster_ref": copy.deepcopy(state["source"]),
        "controller_bridge": {
            "stage": migration.BridgeStage.SOURCE_HA_ACTIVE.value,
            "authority": {"owner": "bridge-source"},
        },
    }
    phase = {"source_reconciliation_fence": state}
    live_by_kind: dict[str, dict[str, Any]] = {}
    writes: list[dict[str, Any]] = []

    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_live_closure",
        lambda **_kwargs: tuple(state["frozen_resources"]),
    )

    def get_managed(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        live = live_by_kind.get(str(kwargs["desired"]["kind"]))
        return (live is not None, copy.deepcopy(live or {}))

    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_get_managed_resource",
        get_managed,
    )
    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_typecheck_ready",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_canary",
        lambda **_kwargs: "a" * 64,
    )

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del timeout_seconds, check
        command = tuple(str(item) for item in args)
        assert "apply" in command
        manifest = migration.yaml.safe_load(input_text or "")
        for index, desired in enumerate(manifest["items"], start=1):
            live = copy.deepcopy(desired)
            live["metadata"]["uid"] = f"managed-{index}"
            live["metadata"]["resourceVersion"] = "1"
            live_by_kind[desired["kind"]] = live
        return SoperatorMigrationCommandResult(command, 0, "applied", "")

    migration._ensure_safe_fresh_target_helm_ordering(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        source_report={},
        target_ref="target-cluster",
        command_runner=runner,
        kube_context="context",
        checkpoint_writer=lambda: writes.append(copy.deepcopy(phase)),
    )

    reseal = state["managed_resources_reseal"]
    assert reseal["status"] == "verified-absent-before-apply"
    assert reseal["previous_sha256"] == "invalid-pre-apply-contract"
    assert reseal["replacement_sha256"] == state["managed_resources_sha256"]
    assert state["status"] == "verified"
    assert writes


def test_source_fence_checks_mutation_guard_before_live_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lost_lease() -> None:
        raise RuntimeError("lost cluster-visible lease")

    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_live_closure",
        lambda **_kwargs: pytest.fail("live inventory must not run after lease loss"),
    )

    with pytest.raises(RuntimeError, match="lost cluster-visible lease"):
        migration._ensure_safe_fresh_target_helm_ordering(  # noqa: SLF001
            checkpoint={},
            phase={},
            source_report={},
            target_ref="target-cluster",
            command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
            kube_context="context",
            checkpoint_writer=lambda: None,
            mutation_guard=lost_lease,
        )


def _pre_target_helm_source_resources(*, replicas: int) -> tuple[dict[str, Any], dict[str, Any]]:
    source = {
        "apiVersion": "slurm.nebius.ai/v1",
        "kind": "SlurmCluster",
        "metadata": {
            "namespace": "soperator",
            "name": "source-cluster",
            "uid": "source-uid",
            "resourceVersion": "40",
        },
        "spec": {"sConfigController": {"node": {"size": 2}}},
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "namespace": "soperator",
            "name": "sconfigcontroller",
            "uid": "sconfig-uid",
            "resourceVersion": "50",
            "generation": 4 if replicas else 5,
            "ownerReferences": [
                {
                    "apiVersion": "slurm.nebius.ai/v1",
                    "kind": "SlurmCluster",
                    "name": "source-cluster",
                    "uid": "source-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": "source-sconfig"}},
            "template": {
                "metadata": {"labels": {"app": "source-sconfig"}},
                "spec": {"containers": [{"name": "writer", "image": "writer:v1"}]},
            },
        },
        "status": {
            "observedGeneration": 4 if replicas else 5,
            "replicas": replicas,
            "readyReplicas": replicas,
        },
    }
    return source, deployment


def test_pre_target_helm_sconfig_writer_is_zero_before_target_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, deployment = _pre_target_helm_source_resources(replicas=2)
    checkpoint = {
        "source_slurmcluster_ref": {
            "namespace": "soperator",
            "name": "source-cluster",
            "uid": "source-uid",
        }
    }
    phase: dict[str, Any] = {}
    events: list[str] = []
    writes: list[dict[str, Any]] = []

    def get_resource(*, resource_type: str, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(source if resource_type == "slurmcluster" else deployment)

    def scale(**kwargs: Any) -> None:
        assert kwargs["resource"] == "deployment/sconfigcontroller"
        assert kwargs["replicas"] == 0
        assert kwargs["current_replicas"] == 2
        assert kwargs["resource_version"] == "50"
        events.append("scale")
        deployment["metadata"]["resourceVersion"] = "51"
        deployment["metadata"]["generation"] = 5
        deployment["spec"]["replicas"] = 0
        deployment["status"] = {"observedGeneration": 5, "replicas": 0, "readyReplicas": 0}

    monkeypatch.setattr(migration, "_immutable_child_get_namespaced_resource", get_resource)
    monkeypatch.setattr(migration, "_immutable_child_list", lambda **_kwargs: ())
    monkeypatch.setattr(migration, "_kubectl_scale_namespace_resource", scale)

    lines = migration._ensure_pre_target_helm_sconfig_writer_fence(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        target_ref="target-cluster",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        kube_context="context",
        checkpoint_writer=lambda: writes.append(copy.deepcopy(phase)),
        mutation_guard=lambda: events.append("guard"),
        timeout_seconds=0,
        poll_interval_seconds=0,
    )

    state = phase["pre_target_helm_sconfig_writer_fence"]
    assert events == ["guard", "scale"]
    assert state["status"] == "verified"
    assert state["scale_mode"] == "scaled-by-cxcli"
    assert state["writer_pod_count"] == 0
    assert writes
    assert "before target CR apply" in lines[0]


def test_pre_target_helm_sconfig_writer_adopts_exact_deployed_recovery_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, deployment = _pre_target_helm_source_resources(replicas=0)
    target = {
        "apiVersion": "slurm.nebius.ai/v1",
        "kind": "SlurmCluster",
        "metadata": {
            "namespace": "soperator",
            "name": "target-cluster",
            "uid": "target-uid",
            "resourceVersion": "60",
        },
        "spec": {"sConfigController": {"node": {"size": 0}}},
    }
    checkpoint = {
        "source_slurmcluster_ref": {
            "namespace": "soperator",
            "name": "source-cluster",
            "uid": "source-uid",
        }
    }
    phase = {"source_reconciliation_fence": _fence_state()}

    def get_resource(*, resource_type: str, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(source if resource_type == "slurmcluster" else deployment)

    monkeypatch.setattr(migration, "_immutable_child_get_namespaced_resource", get_resource)
    monkeypatch.setattr(
        migration,
        "_immutable_child_optional_namespaced_resource",
        lambda **_kwargs: copy.deepcopy(target),
    )
    monkeypatch.setattr(migration, "_immutable_child_list", lambda **_kwargs: ())
    monkeypatch.setattr(
        migration,
        "list_helm_releases",
        lambda **_kwargs: (
            SimpleNamespace(
                name="soperator",
                namespace="soperator",
                status="deployed",
                revision="6",
            ),
        ),
    )

    migration._ensure_pre_target_helm_sconfig_writer_fence(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        target_ref="target-cluster",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        kube_context="context",
        checkpoint_writer=lambda: None,
        timeout_seconds=0,
        poll_interval_seconds=0,
    )

    state = phase["pre_target_helm_sconfig_writer_fence"]
    assert state["status"] == "verified"
    assert state["scale_mode"] == "adopted-deployed-target-recovery"
    assert state["zero_generation"] == 5


def test_source_fence_reseals_only_writer_pods_removed_by_verified_zero_fence() -> None:
    state = _fence_state()
    writer_pod = {
        "api_path": "/api/v1/namespaces/soperator/pods/sconfigcontroller-old",
        "api_version": "v1",
        "kind": "Pod",
        "namespace": "soperator",
        "name": "sconfigcontroller-old",
        "uid": "writer-pod-uid",
        "resource_version": "44",
        "labels": {
            "app.kubernetes.io/name": "slurmcluster",
            "app.kubernetes.io/instance": "source-cluster",
            "app.kubernetes.io/component": "sconfigcontroller",
        },
    }
    state["frozen_resources"].append(writer_pod)
    state["frozen_resources"].sort(key=lambda item: item["api_path"])
    stable = migration._source_reconciliation_fence_stable_closure(  # noqa: SLF001
        state["frozen_resources"]
    )
    state["closure_sha256"] = migration._fingerprint(stable)  # noqa: SLF001
    live = tuple(item for item in state["frozen_resources"] if item["uid"] != "writer-pod-uid")
    phase = {
        "pre_target_helm_sconfig_writer_fence": {
            "schema": migration._PRE_TARGET_HELM_SCONFIG_WRITER_FENCE_SCHEMA,  # noqa: SLF001
            "status": "verified",
            "writer_pod_count": 0,
            "verified_at": "2026-07-14T00:00:00Z",
            "deployment": {
                "uid": "writer-deployment-uid",
                "selector": {
                    "app.kubernetes.io/name": "slurmcluster",
                    "app.kubernetes.io/instance": "source-cluster",
                    "app.kubernetes.io/component": "sconfigcontroller",
                },
            },
        }
    }
    writes: list[dict[str, Any]] = []

    migration._source_reconciliation_fence_reseal_writer_absence(  # noqa: SLF001
        state=state,
        phase=phase,
        live_closure=live,
        checkpoint_writer=lambda: writes.append(copy.deepcopy(state)),
    )
    migration._source_reconciliation_fence_validate_checkpoint(  # noqa: SLF001
        state,
        source_binding=state["source"],
        live_closure=live,
    )

    reseal = state["closure_absence_reseal"]
    assert reseal["status"] == "verified"
    assert [item["uid"] for item in reseal["accepted_absent_resources"]] == ["writer-pod-uid"]
    assert writes


def test_source_fence_reseals_exact_worker_replacement_from_failed_preempting_gpu_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _fence_state()
    state["frozen_resources"][1]["labels"]["app.kubernetes.io/component"] = "worker"
    writer_pod = {
        "api_path": "/api/v1/namespaces/soperator/pods/sconfigcontroller-old",
        "api_version": "v1",
        "kind": "Pod",
        "namespace": "soperator",
        "name": "sconfigcontroller-old",
        "uid": "writer-pod-uid",
        "resource_version": "44",
        "labels": {
            "app.kubernetes.io/name": "slurmcluster",
            "app.kubernetes.io/instance": "source-cluster",
            "app.kubernetes.io/component": "sconfigcontroller",
        },
    }
    frozen_worker = {
        "api_path": "/api/v1/namespaces/soperator/pods/worker-0",
        "api_version": "v1",
        "kind": "Pod",
        "namespace": "soperator",
        "name": "worker-0",
        "uid": "old-worker-pod-uid",
        "resource_version": "45",
        "labels": {
            "app.kubernetes.io/name": "slurmcluster",
            "app.kubernetes.io/instance": "source-cluster",
            "app.kubernetes.io/component": "worker",
        },
    }
    state["frozen_resources"].extend((writer_pod, frozen_worker))
    state["frozen_resources"].sort(key=lambda item: item["api_path"])
    state["closure_sha256"] = migration._fingerprint(  # noqa: SLF001
        migration._source_reconciliation_fence_stable_closure(  # noqa: SLF001
            state["frozen_resources"]
        )
    )
    state["closure_absence_reseal"] = {
        "schema": "nebius-cxcli/source-fence-writer-absence-reseal-v1",
        "status": "verified",
        "writer_deployment_uid": "writer-deployment-uid",
        "writer_fence_verified_at": "2026-07-14T00:00:00Z",
        "accepted_absent_resources": [
            migration._source_reconciliation_fence_stable_identity(writer_pod)  # noqa: SLF001
        ],
        "resealed_at": "2026-07-14T00:01:00Z",
    }
    live_worker = copy.deepcopy(frozen_worker)
    live_worker["uid"] = "new-worker-pod-uid"
    live_worker["resource_version"] = "99"
    live = tuple(
        live_worker if item["uid"] == "old-worker-pod-uid" else item
        for item in state["frozen_resources"]
        if item["uid"] != "writer-pod-uid"
    )
    old_image = "registry.example.invalid/populate-jail@sha256:" + "a" * 64
    checkpoint = {
        "phase_state": {
            migration.POPULATE_JAIL_REFRESH_PHASE_ID: {
                "gpu_post_population": {
                    "status": "intent-recorded",
                    "image_lock": {
                        "source": "registry.example.invalid/populate-jail:4.0.2",
                        "immutable_reference": old_image,
                    },
                    "binding": {
                        "image": "registry.example.invalid/populate-jail:4.0.2",
                        "job": "gpu-probe",
                    },
                }
            }
        }
    }
    unsafe_job = {
        "metadata": {"uid": "unsafe-job-uid"},
        "spec": {
            "template": {
                "spec": {
                    "priorityClassName": "unsafe-high-priority",
                    "containers": [
                        {
                            "image": old_image,
                            "resources": {"limits": {"nvidia.com/gpu": "1"}},
                        }
                    ],
                }
            }
        },
        "status": {"conditions": [{"type": "Failed", "status": "True"}]},
    }
    worker_pod = {
        "metadata": {
            "uid": "new-worker-pod-uid",
            "ownerReferences": [{"uid": "worker-uid", "kind": "StatefulSet", "controller": True}],
        },
        "spec": {"nodeName": "gpu-node-0"},
        "status": {
            "phase": "Running",
            "containerStatuses": [{"name": "slurmd", "ready": True, "restartCount": 0}],
        },
    }

    def get_resource(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        resource = str(kwargs["resource"])
        return True, copy.deepcopy(unsafe_job if resource.startswith("job/") else worker_pod)

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_resource)
    writes: list[dict[str, Any]] = []

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="another stable identity changed",
    ):
        migration._source_reconciliation_fence_reseal_writer_absence(  # noqa: SLF001
            state=state,
            phase={},
            live_closure=live,
            checkpoint_writer=lambda: None,
        )

    migration._source_reconciliation_fence_reseal_gpu_probe_worker_replacement(  # noqa: SLF001
        state=state,
        checkpoint=checkpoint,
        live_closure=live,
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        kube_context="context",
        checkpoint_writer=lambda: writes.append(copy.deepcopy(state)),
    )
    migration._source_reconciliation_fence_validate_checkpoint(  # noqa: SLF001
        state,
        source_binding=state["source"],
        live_closure=live,
    )

    reseal = state["closure_worker_replacement_reseal"]
    assert reseal["status"] == "verified"
    assert reseal["frozen_identity"]["uid"] == "old-worker-pod-uid"
    assert reseal["live_identity"]["uid"] == "new-worker-pod-uid"
    assert writes


def test_source_fence_checkpoint_allows_resource_version_only_change() -> None:
    state = _fence_state()
    live_closure = copy.deepcopy(state["frozen_resources"])
    live_closure[0]["resource_version"] = "9001"
    live_closure[1]["resource_version"] = "9002"

    migration._source_reconciliation_fence_validate_checkpoint(  # noqa: SLF001
        state,
        source_binding=state["source"],
        live_closure=live_closure,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("uid", "replacement-uid"),
        ("name", "replacement-worker"),
        ("labels", {"app.kubernetes.io/instance": "replacement-cluster"}),
    ),
)
def test_source_fence_checkpoint_rejects_stable_identity_drift(
    field: str,
    value: object,
) -> None:
    state = _fence_state()
    live_closure = copy.deepcopy(state["frozen_resources"])
    live_closure[1][field] = value

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="closure changed",
    ):
        migration._source_reconciliation_fence_validate_checkpoint(  # noqa: SLF001
            state,
            source_binding=state["source"],
            live_closure=live_closure,
        )


def test_target_helm_manager_must_use_the_fenced_service_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _fence_state()
    desired_by_kind = {
        item["kind"]: copy.deepcopy(item)
        for item in migration._source_reconciliation_fence_resources(state=state)  # noqa: SLF001
    }
    binding_by_kind = {item["kind"]: item for item in state["managed_resource_bindings"]}
    for kind, live in desired_by_kind.items():
        live["metadata"]["uid"] = binding_by_kind[kind]["uid"]
        live["metadata"]["resourceVersion"] = binding_by_kind[kind]["resource_version"]

    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_get_managed_resource",
        lambda **kwargs: (True, copy.deepcopy(desired_by_kind[kwargs["desired"]["kind"]])),
    )
    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_canary",
        lambda **_kwargs: "a" * 64,
    )
    manager_service_account = "soperator-manager"

    def get_manager(**_kwargs: Any) -> tuple[bool, dict[str, Any]]:
        return (
            True,
            {"spec": {"template": {"spec": {"serviceAccountName": manager_service_account}}}},
        )

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_manager)

    migration._verify_source_reconciliation_fence_after_target_helm(  # noqa: SLF001
        phase={"source_reconciliation_fence": state},
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        kube_context="context",
    )

    manager_service_account = "unfenced-manager"
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="exact service account protected",
    ):
        migration._verify_source_reconciliation_fence_after_target_helm(  # noqa: SLF001
            phase={"source_reconciliation_fence": state},
            command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
            kube_context="context",
        )


def test_source_fence_cleanup_is_uid_bound_and_runs_after_source_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _fence_state()
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "source_cleanup_completed_at": "2026-07-12T00:00:00+00:00",
                "immutable_child_handoff": {"status": "complete"},
                "source_reconciliation_fence": state,
            }
        }
    }
    desired_by_kind = {
        item["kind"]: copy.deepcopy(item)
        for item in migration._source_reconciliation_fence_resources(state=state)  # noqa: SLF001
    }
    binding_by_kind = {item["kind"]: item for item in state["managed_resource_bindings"]}
    live_by_kind: dict[str, dict[str, Any]] = {}
    for kind, desired in desired_by_kind.items():
        live = copy.deepcopy(desired)
        live["metadata"]["uid"] = binding_by_kind[kind]["uid"]
        live["metadata"]["resourceVersion"] = binding_by_kind[kind]["resource_version"]
        live_by_kind[kind] = live
    deleted: list[str] = []
    writes: list[dict[str, Any]] = []

    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (False, {}),
    )

    def get_managed(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        kind = str(kwargs["desired"]["kind"])
        live = live_by_kind.get(kind)
        return (live is not None, copy.deepcopy(live or {}))

    monkeypatch.setattr(
        migration,
        "_source_reconciliation_fence_get_managed_resource",
        get_managed,
    )

    def delete_exact(
        _command_runner: Any,
        *,
        api_path: str,
        **_kwargs: Any,
    ) -> None:
        kind = next(
            candidate
            for candidate, desired in desired_by_kind.items()
            if migration._source_reconciliation_fence_delete_api_path(  # noqa: SLF001
                binding_by_kind[candidate]
            )
            == api_path
        )
        deleted.append(kind)
        live_by_kind.pop(kind)

    monkeypatch.setattr(
        migration,
        "_command_runner_uid_preconditioned_delete",
        delete_exact,
    )

    lines = migration._cleanup_source_reconciliation_fence(  # noqa: SLF001
        checkpoint=checkpoint,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        checkpoint_writer=lambda: writes.append(copy.deepcopy(checkpoint)),
    )

    assert deleted == [
        "RoleBinding",
        "ValidatingAdmissionPolicyBinding",
        "ValidatingAdmissionPolicy",
        "Role",
    ]
    assert state["status"] == "removed"
    assert state["cleanup"]["status"] == "complete"
    assert all(row["state"] == "absent" for row in state["cleanup"]["operations"].values())
    assert writes
    assert "target-singleton proof" in lines[0]


def test_source_fence_cleanup_refuses_a_live_source_slurmcluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _fence_state()
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "source_cleanup_completed_at": "2026-07-12T00:00:00+00:00",
                "immutable_child_handoff": {"status": "complete"},
                "source_reconciliation_fence": state,
            }
        }
    }
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (
            True,
            {"metadata": {"uid": "source-uid", "name": "source-cluster"}},
        ),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="exact source SlurmCluster exists",
    ):
        migration._cleanup_source_reconciliation_fence(  # noqa: SLF001
            checkpoint=checkpoint,
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
            checkpoint_writer=lambda: None,
        )
