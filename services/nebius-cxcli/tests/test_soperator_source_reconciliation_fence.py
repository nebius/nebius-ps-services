from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_migration import SoperatorMigrationCommandResult


def _fence_state() -> dict[str, Any]:
    frozen_resources = [
        {
            "api_path": (
                "/apis/slurm.nebius.ai/v1/namespaces/soperator/"
                "slurmclusters/source-cluster"
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
    binding_by_kind = {
        item["kind"]: item for item in state["managed_resource_bindings"]
    }
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
            {
                "spec": {
                    "template": {
                        "spec": {"serviceAccountName": manager_service_account}
                    }
                }
            },
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
    binding_by_kind = {
        item["kind"]: item for item in state["managed_resource_bindings"]
    }
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
