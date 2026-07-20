from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_migration import SoperatorMigrationCommandResult

_BRIDGE_NAMESPACE = "cxcli-soperator-upgrade"
_BRIDGE_WORKLOAD_UID = "bridge-workload-uid"
_BRIDGE_PODS = (
    (_BRIDGE_NAMESPACE, "cxcli-slurm-controller-bridge-0", "bridge-pod-0-uid"),
    (_BRIDGE_NAMESPACE, "cxcli-slurm-controller-bridge-1", "bridge-pod-1-uid"),
)
_ORIGINAL_RUNTIME_PROCESS_CENSUS = migration._prove_controller_runtime_process_census  # noqa: SLF001


@pytest.fixture(autouse=True)
def _stub_runtime_process_census(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_process_census",
        lambda **_kwargs: [],
    )


def _result(args: Sequence[str]) -> SoperatorMigrationCommandResult:
    return SoperatorMigrationCommandResult(tuple(args), 0, "", "")


def _pod(
    namespace: str,
    name: str,
    uid: str,
    *,
    phase: str = "Running",
    container: Mapping[str, Any] | None = None,
    owner_uid: str = _BRIDGE_WORKLOAD_UID,
    crash_loop: bool = False,
) -> dict[str, Any]:
    status: dict[str, Any] = {"phase": phase}
    if crash_loop:
        status["containerStatuses"] = [
            {
                "name": "slurmctld",
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
            }
        ]
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "uid": uid,
            "ownerReferences": [{"uid": owner_uid, "controller": True}],
        },
        "spec": {"containers": [dict(container or {"name": "slurmctld"})]},
        "status": status,
    }


def _workload(
    resource: str,
    namespace: str,
    name: str,
    uid: str,
    *,
    replicas: int = 1,
    container: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    template = {"spec": {"containers": [dict(container or {"name": "slurmctld"})]}}
    spec: dict[str, Any]
    if resource == "cronjobs.batch":
        spec = {"jobTemplate": {"spec": {"template": template}}}
    else:
        spec = {"replicas": replicas, "template": template}
    return {
        "metadata": {"namespace": namespace, "name": name, "uid": uid},
        "spec": spec,
    }


def _gated_container() -> dict[str, Any]:
    values = migration.target_controller_gate_values({})
    return {
        "name": "slurmctld",
        **values["slurmNodes"]["controller"]["slurmctld"],
    }


def _runner(
    *,
    extra_pods: Sequence[Mapping[str, Any]] = (),
    extra_workloads: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> migration.SoperatorMigrationCommandRunner:
    pods = [_pod(namespace, name, uid) for namespace, name, uid in _BRIDGE_PODS]
    pods.extend(dict(item) for item in extra_pods)
    pods.append(
        _pod(
            "soperator",
            "controller-0",
            "gated-controller-uid",
            container=_gated_container(),
            owner_uid="gated-workload-uid",
        )
    )
    workloads: dict[str, list[Mapping[str, Any]]] = {
        resource: [] for resource in migration._SLURMCTLD_CAPABLE_WORKLOAD_RESOURCES
    }
    workloads["statefulsets.apps"].append(
        _workload(
            "statefulsets.apps",
            _BRIDGE_NAMESPACE,
            "cxcli-slurm-controller-bridge",
            _BRIDGE_WORKLOAD_UID,
            replicas=2,
        )
    )
    workloads["statefulsets.apps.kruise.io"].append(
        _workload(
            "statefulsets.apps.kruise.io",
            "soperator",
            "controller",
            "gated-workload-uid",
            container=_gated_container(),
        )
    )
    for resource, items in (extra_workloads or {}).items():
        workloads[resource].extend(items)

    def run(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        resource = command[command.index("get") + 1]
        payload = {"items": pods if resource == "pods" else workloads[resource]}
        return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")

    return run


def _prove(
    command_runner: migration.SoperatorMigrationCommandRunner,
    *,
    journal: Mapping[str, Any] | None = None,
) -> None:
    migration._prove_cluster_wide_slurmctld_exclusivity(  # noqa: SLF001
        expected_pods=_BRIDGE_PODS,
        expected_workload=(
            "statefulsets.apps",
            _BRIDGE_NAMESPACE,
            "cxcli-slurm-controller-bridge",
            _BRIDGE_WORKLOAD_UID,
        ),
        expected_replicas=2,
        proof_label="test bridge activation",
        campaign_fingerprint="a" * 64,
        journal=journal or {"campaign_fingerprint": "a" * 64},
        kube_context="test-context",
        command_runner=command_runner,
    )


def test_bridge_exclusivity_allows_only_exact_command_gated_target() -> None:
    _prove(_runner())


def _bridge_stager(*, command: Sequence[str] | None = None) -> dict[str, Any]:
    return {
        "metadata": {
            "namespace": migration.CONTROLLER_BRIDGE_NAMESPACE,
            "name": "cxcli-controller-bridge-stager",
            "uid": "bridge-stager-uid",
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                migration.CONTROLLER_BRIDGE_LABEL: "stager",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "stager",
                    "image": "registry.example/controller_slurmctld@sha256:" + "8" * 64,
                    "command": list(
                        command
                        or (
                            "/bin/sh",
                            "-ec",
                            "trap 'exit 0' TERM INT; while :; do sleep 30; done",
                        )
                    ),
                }
            ],
        },
        "status": {"phase": "Running"},
    }


def test_bridge_exclusivity_allows_exact_inert_campaign_stager() -> None:
    _prove(_runner(extra_pods=(_bridge_stager(),)))


def test_bridge_exclusivity_rejects_stager_with_changed_command() -> None:
    stager = _bridge_stager(command=("/usr/sbin/slurmctld", "-D"))
    with pytest.raises(migration.SoperatorMigrationPhasePending, match="second nonterminal"):
        _prove(_runner(extra_pods=(stager,)))


def _verified_runtime_fence_pod() -> tuple[dict[str, Any], dict[str, Any]]:
    campaign = "a" * 64
    attempt_id = "b" * 32
    purpose = "source-version-bridge-pre-authority"
    target = migration.ControllerFenceTarget(
        node_name="controller-node",
        node_uid="controller-node-uid",
        state_markers=("/mnt/controller-spool/current",),
    )
    image = "registry.example/controller_slurmctld@sha256:" + "7" * 64
    epoch_slug = purpose[:32]
    desired = migration.controller_runtime_fence_pod(
        campaign_fingerprint=campaign,
        authority_epoch=f"fence-{epoch_slug}-{attempt_id[:12]}",
        image=image,
        target=target,
    )
    desired["metadata"]["uid"] = "fence-pod-uid"
    desired["status"] = {"phase": "Running"}
    journal = {
        "campaign_fingerprint": campaign,
        "runtime_fence_proofs": [
            {
                "purpose": purpose,
                "boundary": f"{purpose}-attempt-{attempt_id}",
                "attempt_id": attempt_id,
                "status": "verified",
                "image": image,
                "targets": [
                    {
                        "node_name": target.node_name,
                        "node_uid": target.node_uid,
                        "state_markers": list(target.state_markers),
                    }
                ],
                "results": [
                    {
                        "node_uid": target.node_uid,
                        "pod_name": desired["metadata"]["name"],
                        "pod_uid": desired["metadata"]["uid"],
                    }
                ],
            }
        ],
    }
    return desired, journal


def test_bridge_exclusivity_allows_exact_verified_runtime_fence_pod() -> None:
    fence, journal = _verified_runtime_fence_pod()

    _prove(_runner(extra_pods=(fence,)), journal=journal)


def test_bridge_exclusivity_allows_verified_fence_resource_tuning() -> None:
    fence, journal = _verified_runtime_fence_pod()
    fence["spec"]["containers"][0]["resources"] = {
        "requests": {"cpu": "5m", "memory": "16Mi"},
        "limits": {"cpu": "100m", "memory": "64Mi"},
    }

    _prove(_runner(extra_pods=(fence,)), journal=journal)


def test_bridge_exclusivity_rejects_runtime_fence_with_changed_command() -> None:
    fence, journal = _verified_runtime_fence_pod()
    fence["spec"]["containers"][0]["command"] = ["/usr/sbin/slurmctld", "-D"]

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="second nonterminal"):
        _prove(_runner(extra_pods=(fence,)), journal=journal)


def _inert_bridge_mount_pod() -> dict[str, Any]:
    return {
        "metadata": {
            "namespace": migration.CONTROLLER_BRIDGE_NAMESPACE,
            "name": "cxcli-controller-bridge-mounts-test",
            "uid": "bridge-mount-pod-uid",
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "nebius.ai/cxcli-controller-bridge-mounts": "true",
            },
            "ownerReferences": [
                {
                    "kind": "DaemonSet",
                    "name": "cxcli-controller-bridge-mounts",
                    "uid": "bridge-mount-workload-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "mount-shared-filesystems",
                    "image": "registry.example/controller_slurmctld@sha256:" + "7" * 64,
                    "command": list(migration.CONTROLLER_BRIDGE_MOUNT_HELPER_COMMAND),
                }
            ]
        },
        "status": {"phase": "Running"},
    }


def _inert_legacy_placeholder_pod() -> dict[str, Any]:
    return {
        "metadata": {
            "namespace": "soperator",
            "name": "controller-placeholder-test",
            "uid": "placeholder-pod-uid",
            "labels": {
                "app.kubernetes.io/managed-by": "slurm-operator",
                "slurm.nebius.ai/controller-type": "placeholder",
            },
            "ownerReferences": [
                {
                    "kind": "DaemonSet",
                    "name": "controller-placeholder",
                    "uid": "placeholder-workload-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "initContainers": [
                {
                    "name": "munge",
                    "image": "registry.example/munge:legacy",
                    "restartPolicy": "Always",
                    "command": ["sleep"],
                    "args": ["infinity"],
                }
            ],
            "containers": [
                {
                    "name": "slurmctld",
                    "image": "registry.example/controller_slurmctld:legacy",
                    "command": ["sleep"],
                    "args": ["infinity"],
                },
                {
                    "name": "ensure-jail-virtiofs",
                    "image": "registry.example/controller_slurmctld:legacy",
                    "command": ["sleep"],
                    "args": ["infinity"],
                },
            ],
        },
        "status": {"phase": "Running"},
    }


def _inert_helper_workload(*, placeholder: bool) -> dict[str, Any]:
    pod = _inert_legacy_placeholder_pod() if placeholder else _inert_bridge_mount_pod()
    metadata = pod["metadata"]
    owner = metadata["ownerReferences"][0]
    labels = dict(metadata["labels"])
    if not placeholder:
        labels[migration.CONTROLLER_BRIDGE_LABEL] = "true"
    return {
        "metadata": {
            "namespace": metadata["namespace"],
            "name": owner["name"],
            "uid": owner["uid"],
            "labels": labels,
        },
        "spec": {"template": {"spec": pod["spec"]}},
    }


def test_bridge_exclusivity_allows_exact_inert_mount_and_placeholder_helpers() -> None:
    _prove(
        _runner(
            extra_pods=(
                _inert_bridge_mount_pod(),
                _inert_legacy_placeholder_pod(),
            ),
            extra_workloads={
                "daemonsets.apps": (
                    _inert_helper_workload(placeholder=False),
                    _inert_helper_workload(placeholder=True),
                )
            },
        )
    )


def test_bridge_exclusivity_rejects_mount_helper_with_changed_command() -> None:
    mount = _inert_bridge_mount_pod()
    mount["spec"]["containers"][0]["command"] = ["/usr/sbin/slurmctld", "-D"]
    with pytest.raises(migration.SoperatorMigrationPhasePending, match="second nonterminal"):
        _prove(_runner(extra_pods=(mount,)))


@pytest.mark.parametrize(
    ("phase", "crash_loop"),
    (("Pending", False), ("Running", True)),
)
def test_bridge_exclusivity_rejects_extra_nonterminal_controller_pod(
    phase: str,
    crash_loop: bool,
) -> None:
    extra = _pod(
        "foreign",
        "unexpected-controller",
        "unexpected-controller-uid",
        phase=phase,
        owner_uid="unexpected-workload-uid",
        crash_loop=crash_loop,
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="second nonterminal",
    ):
        _prove(_runner(extra_pods=(extra,)))


def test_bridge_exclusivity_rejects_extra_desired_controller_workload() -> None:
    extra = _workload(
        "deployments.apps",
        "foreign",
        "unexpected-controller",
        "unexpected-workload-uid",
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="another desired workload",
    ):
        _prove(_runner(extra_workloads={"deployments.apps": (extra,)}))


def test_bridge_exclusivity_runtime_census_rejects_generic_config_script_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic = _pod(
        "foreign",
        "generic-runner",
        "generic-runner-uid",
        owner_uid="generic-workload-uid",
        container={
            "name": "runner",
            "image": "registry.example/generic@sha256:" + "9" * 64,
            "command": ["/bin/sh", "/config/start-controller.sh"],
        },
    )
    calls: list[Sequence[tuple[str, str, str]]] = []

    def reject_runtime_process(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs["expected_pods"])
        raise migration.SoperatorMigrationPhasePending(
            "runtime census found an unexpected host slurmctld process"
        )

    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_process_census",
        reject_runtime_process,
    )

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="unexpected host"):
        _prove(_runner(extra_pods=(generic,)))
    assert calls == [_BRIDGE_PODS]


def _authority_runner(
    *,
    pods: Sequence[Mapping[str, Any]],
    workloads: Mapping[str, Sequence[Mapping[str, Any]]],
) -> migration.SoperatorMigrationCommandRunner:
    by_resource = {
        resource: list(workloads.get(resource, ()))
        for resource in migration._SLURMCTLD_CAPABLE_WORKLOAD_RESOURCES  # noqa: SLF001
    }
    lease = migration.controller_authority_lease(
        namespace=_BRIDGE_NAMESPACE,
        campaign_fingerprint="a" * 64,
        cluster_id="cluster-1",
        authority_epoch="target-aaaaaaaaaaaa",
        owner="target-singleton",
    )
    lease["metadata"].update({"uid": "authority-lease-uid", "resourceVersion": "17"})

    def run(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        resource = command[command.index("get") + 1]
        if resource == "lease":
            payload = lease
        else:
            payload = {"items": list(pods) if resource == "pods" else by_resource[resource]}
        return SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")

    return run


def test_zero_authority_proof_allows_only_the_command_gated_target() -> None:
    gated_pod = _pod(
        "soperator",
        "controller-0",
        "gated-pod-uid",
        container=_gated_container(),
        owner_uid="gated-workload-uid",
    )
    gated_workload = _workload(
        "statefulsets.apps.kruise.io",
        "soperator",
        "controller",
        "gated-workload-uid",
        container=_gated_container(),
    )

    migration._prove_cluster_wide_slurmctld_absence(  # noqa: SLF001
        proof_label="test fenced bridge",
        campaign_fingerprint="a" * 64,
        journal={"campaign_fingerprint": "a" * 64},
        kube_context="test-context",
        command_runner=_authority_runner(
            pods=(gated_pod,),
            workloads={"statefulsets.apps.kruise.io": (gated_workload,)},
        ),
    )


def test_zero_authority_api_proof_can_reuse_pre_stop_runtime_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gated_pod = _pod(
        "soperator",
        "controller-0",
        "gated-pod-uid",
        container=_gated_container(),
        owner_uid="gated-workload-uid",
    )
    gated_workload = _workload(
        "statefulsets.apps.kruise.io",
        "soperator",
        "controller",
        "gated-workload-uid",
        container=_gated_container(),
    )

    def unexpected_runtime_census(**_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("post-stop API proof must not start another all-node census")

    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_process_census",
        unexpected_runtime_census,
    )

    result = migration._prove_cluster_wide_slurmctld_absence(  # noqa: SLF001
        proof_label="test fenced bridge API-only",
        campaign_fingerprint="a" * 64,
        journal={"campaign_fingerprint": "a" * 64},
        kube_context="test-context",
        command_runner=_authority_runner(
            pods=(gated_pod,),
            workloads={"statefulsets.apps.kruise.io": (gated_workload,)},
        ),
        process_census=False,
    )

    assert result == []


def test_target_singleton_proof_rejects_a_foreign_controller() -> None:
    target_pod = _pod(
        "soperator",
        "controller-0",
        "target-pod-uid",
        owner_uid="target-workload-uid",
    )
    foreign_pod = _pod(
        "foreign",
        "controller-0",
        "foreign-pod-uid",
        owner_uid="foreign-workload-uid",
    )
    target_workload = _workload(
        "statefulsets.apps.kruise.io",
        "soperator",
        "controller",
        "target-workload-uid",
    )
    foreign_workload = _workload(
        "deployments.apps",
        "foreign",
        "controller",
        "foreign-workload-uid",
    )
    journal = {
        "namespace": _BRIDGE_NAMESPACE,
        "campaign_fingerprint": "a" * 64,
        "cluster_id": "cluster-1",
        "authority": {"epoch": "target-aaaaaaaaaaaa", "owner": "target-singleton"},
        "target_singleton_takeover": {
            "controller_pod_uid": "target-pod-uid",
            "controller_workload_uid": "target-workload-uid",
        },
    }

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="unexpected"):
        migration._prove_journaled_target_singleton_exclusivity(  # noqa: SLF001
            journal=journal,
            proof_label="test target handoff",
            kube_context="test-context",
            command_runner=_authority_runner(
                pods=(target_pod, foreign_pod),
                workloads={
                    "statefulsets.apps.kruise.io": (target_workload,),
                    "deployments.apps": (foreign_workload,),
                },
            ),
        )


def test_target_singleton_proof_accepts_tagged_spec_with_locked_runtime_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "a" * 64
    final_config = (
        "ClusterName=target-cluster\n"
        "SlurmctldHost=controller-0(target-cluster-controller-svc)\n"
        "StateSaveLocation=/target/state\n"
    )
    journal = {
        "campaign_fingerprint": "b" * 64,
        "authority": {"epoch": "target-bbbbbbbbbbbb", "owner": "target-singleton"},
        "source_configuration": {"config_key": "slurm.conf"},
        "version_transition": {
            "target_image": f"registry.example/controller@sha256:{digest}",
        },
        "target_singleton_takeover": {
            "target_ref": "target-cluster",
            "controller_pod_uid": "target-pod-uid",
            "controller_workload_uid": "target-workload-uid",
            "final_config_map_name": "target-config",
            "final_config_sha256": hashlib.sha256(final_config.encode()).hexdigest(),
            "target_state_save_location": "/target/state",
        },
    }
    monkeypatch.setattr(migration, "_revalidate_controller_authority_lease", lambda **_: None)
    monkeypatch.setattr(migration, "_prove_cluster_wide_slurmctld_exclusivity", lambda **_: [])
    monkeypatch.setattr(migration, "_controller_bridge_client_consumers", lambda **_: [])

    def fake_json(_runner: Any, args: list[str], **_kwargs: Any) -> dict[str, Any]:
        if "configmap" in args:
            return {"data": {"slurm.conf": final_config}}
        return {
            "metadata": {"uid": "target-pod-uid"},
            "spec": {
                "containers": [
                    {
                        "name": "slurmctld",
                        "image": "registry.example/controller:4.0.2",
                    }
                ]
            },
            "status": {
                "containerStatuses": [
                    {
                        "name": "slurmctld",
                        "imageID": f"registry.example/controller@sha256:{digest}",
                    }
                ]
            },
        }

    monkeypatch.setattr(migration, "_json_from_command", fake_json)

    migration._prove_journaled_target_singleton_exclusivity(  # noqa: SLF001
        journal=journal,
        proof_label="test target singleton",
        kube_context="test-context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
    )


def test_every_action_dispatch_boundary_has_a_cluster_wide_authority_proof() -> None:
    recovery = inspect.getsource(
        migration._restart_target_version_bridge_after_failed_takeover  # noqa: SLF001
    )
    handoff = inspect.getsource(
        migration._complete_target_singleton_final_config  # noqa: SLF001
    )
    satisfied = inspect.getsource(migration._controller_bridge_satisfied)  # noqa: SLF001

    assert recovery.index("_prove_journaled_controller_bridge_exclusivity") < recovery.index(
        'set_slurm_action_broker_mode(action_journal, "dispatch-enabled")'
    )
    assert handoff.index("_prove_journaled_target_singleton_exclusivity") < handoff.index(
        'set_slurm_action_broker_mode(action_journal, "dispatch-enabled")'
    )
    assert "_prove_journaled_controller_bridge_exclusivity" in satisfied
    assert "_prove_journaled_target_singleton_exclusivity" in satisfied
    assert "_prove_cluster_wide_slurmctld_absence" in satisfied


def test_runtime_census_starts_all_node_inspectors_together_and_removes_them() -> None:
    source = inspect.getsource(  # noqa: SLF001
        _ORIGINAL_RUNTIME_PROCESS_CENSUS
    )

    assert source.index("objects=tuple(desired for _target, desired") < source.index(
        "terminal_deadline = time.monotonic() + 300"
    )
    assert 'phase in {"Succeeded", "Failed"}' in source
    assert '"--for=jsonpath={.status.phase}=Succeeded"' not in source
    assert "CONTROLLER_INSPECTOR_NAMESPACE" in source
    assert source.index("try:") < source.index("_kubectl_apply_objects(")
    finally_index = source.index("finally:")
    assert finally_index < source.index("_delete_census_pods_and_prove_absence()", finally_index)
    assert '"--wait=true"' in source
    assert "_inspector_pods_by_name" in source
    assert "set(census_pod_names) & set(_inspector_pods_by_name())" in source
    assert "_kubectl_get_namespace_resource(" not in source


def test_source_reconciliation_is_fenced_before_source_config_mutation() -> None:
    bridge_phase = inspect.getsource(migration._execute_controller_ha_bridge_phase)  # noqa: SLF001
    source_fence = inspect.getsource(migration._fence_source_controller_for_bridge)  # noqa: SLF001

    assert bridge_phase.index("_suspend_source_reconciliation_for_bridge") < (
        bridge_phase.index("_configure_source_controller_for_bridge")
    )
    assert source_fence.index("_suspend_source_reconciliation_for_bridge") < (
        source_fence.index("_prove_controller_bridge_client_configuration")
    )
    assert source_fence.index("_prove_controller_bridge_client_configuration") < (
        source_fence.index("_kubectl_scale_namespace_resource")
    )


def test_inspector_admission_precedes_every_source_bridge_mutation() -> None:
    bridge_phase = inspect.getsource(migration._execute_controller_ha_bridge_phase)  # noqa: SLF001
    preflight = inspect.getsource(  # noqa: SLF001
        migration._preflight_controller_inspector_namespace
    )

    preflight_index = bridge_phase.index("_preflight_controller_inspector_namespace")
    assert preflight_index < bridge_phase.index("_protect_upgrade_login_sessions")
    assert preflight_index < bridge_phase.index("_bridge_pause_all_partitions")
    assert preflight_index < bridge_phase.index("_create_controller_bridge_node_groups")
    assert '"--server-side"' in preflight
    assert '"--dry-run=server"' in preflight
    assert "CONTROLLER_INSPECTOR_NAMESPACE" in preflight


def test_every_writer_start_revalidates_security_and_full_node_membership() -> None:
    for function, writer_call in (
        (migration._activate_source_version_controller_bridge, "_kubectl_scale_namespace_resource"),
        (
            migration._upgrade_controller_bridge_to_target_version,
            "_kubectl_scale_namespace_resource",
        ),
        (
            migration._restart_target_version_bridge_after_failed_takeover,
            "_kubectl_scale_namespace_resource",
        ),
        (
            migration._handoff_controller_bridge_to_target_singleton,
            "_ungate_in_place_target_controller_for_takeover",
        ),
    ):
        source = inspect.getsource(function)
        writer_index = source.rindex(writer_call)
        security_index = source.rindex(
            "_revalidate_controller_bridge_security_contract", 0, writer_index
        )
        nodes_index = source.rindex("_revalidate_controller_runtime_census_nodes", 0, writer_index)
        assert security_index < nodes_index < writer_index


def test_source_to_target_bridge_census_runs_before_cold_stop_and_is_reused() -> None:
    source = inspect.getsource(
        migration._upgrade_controller_bridge_to_target_version  # noqa: SLF001
    )

    pre_stop_census = source.index('proof_label="source-version bridge pre-stop runtime census"')
    stop_dispatch = source.index('"cold_stop_dispatching_at": _utc_now()', pre_stop_census)
    cold_stop_scale = source.index(
        "_kubectl_scale_namespace_resource",
        stop_dispatch,
    )
    exact_node_fence = source.index(
        'boundary="source-version-bridge-stopped"',
        cold_stop_scale,
    )
    api_absence = source.index(
        'proof_label="source-version bridge cold stop"',
        exact_node_fence,
    )
    reused_node_identity = source.index(
        "_revalidate_controller_runtime_census_nodes",
        api_absence,
    )

    assert pre_stop_census < stop_dispatch < cold_stop_scale < exact_node_fence < api_absence
    assert "process_census=False" in source[api_absence:reused_node_identity]


def test_source_bridge_stages_jailed_config_before_initial_or_recovery_writer_scale() -> None:
    activation = inspect.getsource(  # noqa: SLF001
        migration._activate_source_version_controller_bridge
    )
    repair = inspect.getsource(  # noqa: SLF001
        migration._ensure_controller_bridge_source_jailed_config
    )

    staging_gate = activation.index("_ensure_controller_bridge_source_jailed_config")
    authority = activation.index("_transition_controller_authority_lease", staging_gate)
    initial_scale = activation.index("_kubectl_scale_namespace_resource", authority)
    assert staging_gate < authority < initial_scale

    stop_scale = repair.index("_kubectl_scale_namespace_resource")
    stopped_fence = repair.index('boundary="source-bridge-jailed-config-repair"', stop_scale)
    stage = repair.index("_stage_controller_bridge_jailed_config", stopped_fence)
    restart_scale = repair.index("_kubectl_scale_namespace_resource", stage)
    assert stop_scale < stopped_fence < stage < restart_scale


def test_every_writer_handoff_runtime_fences_then_cas_authority_before_start() -> None:
    bridge_takeover_fence = inspect.getsource(
        migration._fence_target_version_bridge_for_takeover  # noqa: SLF001
    )
    failed_takeover_recovery = inspect.getsource(
        migration._restart_target_version_bridge_after_failed_takeover  # noqa: SLF001
    )
    target_handoff = inspect.getsource(
        migration._handoff_controller_bridge_to_target_singleton  # noqa: SLF001
    )
    source_activation = inspect.getsource(
        migration._activate_source_version_controller_bridge  # noqa: SLF001
    )
    version_transition = inspect.getsource(
        migration._upgrade_controller_bridge_to_target_version  # noqa: SLF001
    )

    assert bridge_takeover_fence.count("fresh_attempt=True") == 1
    takeover_runtime_index = bridge_takeover_fence.index("_prove_controller_runtime_fence")
    takeover_fresh_index = bridge_takeover_fence.index("fresh_attempt=True", takeover_runtime_index)
    assert (
        takeover_runtime_index
        < takeover_fresh_index
        < bridge_takeover_fence.index("_prove_cluster_wide_slurmctld_absence", takeover_fresh_index)
        < bridge_takeover_fence.index("_transition_controller_authority_lease")
    )

    assert target_handoff.count("fresh_attempt=True") == 1
    target_start_runtime_index = target_handoff.index(
        'boundary="target-singleton-pre-writer-start"'
    )
    target_start_fresh_index = target_handoff.index(
        "fresh_attempt=True", target_start_runtime_index
    )
    assert (
        target_handoff.index("_fence_target_version_bridge_for_takeover")
        < (target_start_runtime_index)
        < target_start_fresh_index
        < target_handoff.index("_prove_cluster_wide_slurmctld_absence", target_start_fresh_index)
        < target_handoff.index(
            "_ungate_in_place_target_controller_for_takeover", target_start_fresh_index
        )
    )

    assert failed_takeover_recovery.count("fresh_attempt=True") == 3
    recovery_pre_authority_index = failed_takeover_recovery.index(
        'boundary="failed-target-takeover-recovery-pre-authority"'
    )
    recovery_pre_authority_fresh = failed_takeover_recovery.index(
        "fresh_attempt=True", recovery_pre_authority_index
    )
    recovery_absence_before_lease = failed_takeover_recovery.index(
        "_prove_cluster_wide_slurmctld_absence", recovery_pre_authority_fresh
    )
    recovery_lease_index = failed_takeover_recovery.index(
        "_transition_controller_authority_lease", recovery_absence_before_lease
    )
    recovery_pre_scale_index = failed_takeover_recovery.index(
        'boundary="failed-target-takeover-recovery-pre-writer-scale"',
        recovery_lease_index,
    )
    recovery_pre_scale_fresh = failed_takeover_recovery.index(
        "fresh_attempt=True", recovery_pre_scale_index
    )
    recovery_absence_before_scale = failed_takeover_recovery.index(
        "_prove_cluster_wide_slurmctld_absence", recovery_pre_scale_fresh
    )
    recovery_bridge_start_index = failed_takeover_recovery.index(
        "_kubectl_scale_namespace_resource", recovery_absence_before_scale
    )
    assert (
        recovery_pre_authority_index
        < recovery_pre_authority_fresh
        < recovery_absence_before_lease
        < recovery_lease_index
        < recovery_pre_scale_index
        < recovery_pre_scale_fresh
        < recovery_absence_before_scale
        < recovery_bridge_start_index
    )

    recovery_jailed_config_index = failed_takeover_recovery.index(
        "_stage_controller_bridge_jailed_config"
    )
    assert recovery_lease_index < recovery_jailed_config_index < recovery_pre_scale_index
    assert (
        'resource="statefulset/cxcli-slurm-controller-bridge"'
        in failed_takeover_recovery[:recovery_pre_authority_index]
    )

    assert source_activation.count("fresh_attempt=True") == 2
    source_pre_authority_index = source_activation.index(
        'boundary="source-version-bridge-pre-authority"'
    )
    source_pre_authority_fresh = source_activation.index(
        "fresh_attempt=True", source_pre_authority_index
    )
    source_absence_before_lease = source_activation.index(
        "_prove_cluster_wide_slurmctld_absence", source_pre_authority_fresh
    )
    source_lease_index = source_activation.index("_transition_controller_authority_lease")
    source_absence_before_scale = source_activation.index(
        "_prove_cluster_wide_slurmctld_absence", source_lease_index
    )
    source_revalidated_runtime = source_activation.index(
        "_revalidate_controller_runtime_census_nodes", source_absence_before_scale
    )
    source_scale_index = source_activation.index(
        "_kubectl_scale_namespace_resource", source_absence_before_scale
    )
    assert (
        source_pre_authority_index
        < source_pre_authority_fresh
        < source_absence_before_lease
        < source_lease_index
        < source_absence_before_scale
        < source_revalidated_runtime
        < source_scale_index
    )
    assert (
        "process_census=False" in source_activation[source_absence_before_scale:source_scale_index]
    )
    assert (
        "node_census=pre_authority_census"
        in source_activation[source_absence_before_scale:source_scale_index]
    )
    source_rollback_index = source_activation.index(
        'boundary="source-version-bridge-scale-rejected-pre-authority-rollback"',
        source_scale_index,
    )
    source_rollback_fresh = source_activation.index("fresh_attempt=True", source_rollback_index)
    source_rollback_absence = source_activation.index(
        "_prove_cluster_wide_slurmctld_absence", source_rollback_fresh
    )
    source_rollback_lease = source_activation.index(
        "_transition_controller_authority_lease", source_rollback_absence
    )
    assert (
        source_scale_index
        < source_rollback_index
        < source_rollback_fresh
        < source_rollback_absence
        < source_rollback_lease
    )

    assert version_transition.count("fresh_attempt=True") == 3
    target_bridge_pre_authority_index = version_transition.index(
        'boundary="target-version-bridge-pre-authority"'
    )
    target_bridge_pre_authority_fresh = version_transition.index(
        "fresh_attempt=True", target_bridge_pre_authority_index
    )
    target_bridge_absence_before_lease = version_transition.index(
        "_prove_cluster_wide_slurmctld_absence", target_bridge_pre_authority_fresh
    )
    target_bridge_lease_index = version_transition.index(
        "_transition_controller_authority_lease", target_bridge_absence_before_lease
    )
    target_bridge_pre_scale_index = version_transition.index(
        'boundary="target-version-bridge-pre-writer-scale"', target_bridge_lease_index
    )
    target_bridge_pre_scale_fresh = version_transition.index(
        "fresh_attempt=True", target_bridge_pre_scale_index
    )
    target_bridge_absence_before_scale = version_transition.index(
        "_prove_cluster_wide_slurmctld_absence", target_bridge_pre_scale_fresh
    )
    target_bridge_scale_index = version_transition.index(
        "_kubectl_scale_namespace_resource", target_bridge_absence_before_scale
    )
    assert (
        target_bridge_pre_authority_index
        < target_bridge_pre_authority_fresh
        < target_bridge_absence_before_lease
        < target_bridge_lease_index
        < target_bridge_pre_scale_index
        < target_bridge_pre_scale_fresh
        < target_bridge_absence_before_scale
        < target_bridge_scale_index
    )


def test_checkpointed_target_start_revalidates_bridge_fence_without_demanding_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal: dict[str, Any] = {
        "stage": "bridge-fenced",
        "namespace": "cxcli-soperator-upgrade-bridge",
        "campaign_fingerprint": "a" * 64,
        "cluster_id": "cluster-1",
        "authority": {"epoch": "target-aaaaaaaaaaaa", "owner": "target-singleton"},
        "target_singleton_takeover": {
            "client_handoff_propagation": {
                "status": "verified",
                "live_rpc_verified": True,
                "proof_stage": "before-target-takeover",
            },
            "target_start": {"state": "dispatching"},
            "bridge_stop_attempt": 1,
            "bridge_stop_pods": [
                {
                    "pod_name": f"bridge-{slot}",
                    "pod_uid": f"bridge-pod-uid-{slot}",
                    "node_name": f"bridge-node-{slot}",
                    "node_uid": f"bridge-node-uid-{slot}",
                }
                for slot in range(2)
            ],
        },
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
        "version_transition": {"target_image": f"registry.example/slurm@sha256:{'b' * 64}"},
        "fencing": {"bridge": {}},
    }
    stopped_workload = {
        "metadata": {"uid": "bridge-workload-uid", "resourceVersion": "10"},
        "spec": {"replicas": 0},
    }
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, stopped_workload),
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"items": []},
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_fence",
        lambda **_kwargs: [],
    )
    revalidated: list[bool] = []
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_authority_lease",
        lambda **_kwargs: revalidated.append(True),
    )
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_absence",
        lambda **_kwargs: pytest.fail(
            "a checkpointed target writer must be adopted before any global absence proof"
        ),
    )

    stopped = migration._fence_target_version_bridge_for_takeover(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=None,
    )

    assert stopped == ["bridge-pod-uid-0", "bridge-pod-uid-1"]
    assert revalidated == [True]
    assert journal["authority"]["owner"] == "target-singleton"


def test_cleaned_bridge_satisfaction_uses_terminal_absence_without_deleted_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = {"stage": "cleaned", "cleanup": {"completed_at": "2026-07-12T12:00:00Z"}}
    target_state = {
        "pvc_name": "controller-spool",
        "pvc_uid": "target-pvc-uid",
        "pv_name": "controller-spool-pv",
        "pv_uid": "target-pv-uid",
        "state_path": "/mnt/controller-spool/current",
    }
    absence_proofs: list[Mapping[str, Any]] = []
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_cleanup_target_singleton",
        lambda **_kwargs: target_state,
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_cleanup_prove_absent",
        lambda **kwargs: absence_proofs.append(kwargs),
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_authority_lease",
        lambda **_kwargs: pytest.fail("the bridge authority Lease is deleted at CLEANED"),
    )

    assert migration._controller_bridge_satisfied(  # noqa: SLF001
        checkpoint={"controller_bridge": journal},
        kube_context="context",
        nebius_api=object(),  # type: ignore[arg-type]
        command_runner=lambda args, **_kwargs: _result(args),
    )
    assert absence_proofs[0]["target_state"] == target_state


def test_cleaned_bridge_proof_failure_never_demotes_to_recreation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_cleanup_target_singleton",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("target unavailable")),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="will not recreate deleted bridge resources",
    ):
        migration._controller_bridge_satisfied(  # noqa: SLF001
            checkpoint={"controller_bridge": {"stage": "cleaned", "cleanup": {}}},
            kube_context="context",
            nebius_api=object(),  # type: ignore[arg-type]
            command_runner=lambda args, **_kwargs: _result(args),
        )


def test_in_place_login_bridge_config_repair_serially_recreates_session_free_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_ref = "external-cluster"
    expected_text = (
        f"ClusterName={target_ref}\n"
        "SlurmctldHost=bridge-0(bridge-0.bridge.svc)\n"
        "SlurmctldHost=bridge-1(bridge-1.bridge.svc)\n"
        "SlurmdTimeout=3600\nSlurmctldTimeout=3600\n"
    )
    stale_text = expected_text.replace(
        "SlurmctldHost=bridge-0",
        "SlurmctldHost=controller-0(controller.svc)\nSlurmctldHost=bridge-0",
    )
    expected_contract = migration._controller_bridge_client_config_contract(  # noqa: SLF001
        expected_text
    )
    state = {"recreated": False, "deletes": 0}

    def consumers(**_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "role": "target-login",
                "name": "login-0",
                "uid": "new-login-uid" if state["recreated"] else "old-login-uid",
                "container_name": "sshd",
            },
            {
                "role": "target-login",
                "name": "login-1",
                "uid": "login-1-uid",
                "container_name": "sshd",
            },
            {
                "role": "target-worker",
                "name": "worker-0",
                "uid": "worker-uid",
                "container_name": "slurmd",
            },
        ]

    def runner(args: Sequence[str], **_kwargs: Any) -> SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        pod = command[command.index("exec") + 1] if "exec" in command else ""
        output = stale_text if pod == "login-0" and not state["recreated"] else expected_text
        return SoperatorMigrationCommandResult(command, 0, output, "")

    monkeypatch.setattr(migration, "_controller_bridge_client_consumers", consumers)
    monkeypatch.setattr(
        migration,
        "_active_login_ssh_session_probe",
        lambda **_kwargs: {"status": "checked", "active_sessions": 0, "pods": []},
    )
    monkeypatch.setattr(
        migration,
        "wait_for_login_service_ready_endpoints",
        lambda *_args, **_kwargs: {"ready_endpoints": 2},
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {
            "metadata": {
                "name": "login-0",
                "uid": "new-login-uid" if state["recreated"] else "old-login-uid",
                "resourceVersion": "42",
            },
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        },
    )

    def delete(*_args: Any, **_kwargs: Any) -> None:
        state["recreated"] = True
        state["deletes"] += 1

    monkeypatch.setattr(migration, "_command_runner_uid_preconditioned_delete", delete)

    phase: dict[str, Any] = {}
    lines = migration._repair_in_place_login_bridge_config_propagation(  # noqa: SLF001
        checkpoint={"controller_bridge": {}},
        phase=phase,
        expected_contract=expected_contract,
        target_ref=target_ref,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    assert state["recreated"] is True
    assert state["deletes"] == 1
    assert lines
    rollout = phase["in_place_bridge_client_handoff"]["login_config_rollout"]
    assert rollout["status"] == "verified"
    assert rollout["pods"]["login-0"]["uid"] == "new-login-uid"


def test_controller_runtime_census_retries_one_fresh_attempt_after_malformed_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def census(**_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise migration.SoperatorMigrationPhasePending(
                "boundary runtime census failed on node-a after three immutable log reads: "
                "controller runtime census output is malformed or duplicated."
            )
        return [{"node_name": "node-a"}]

    monkeypatch.setattr(migration, "_prove_controller_runtime_process_census", census)

    result = migration._prove_controller_runtime_process_census_with_fresh_retry(  # noqa: SLF001
        expected_pods=(),
        proof_label="boundary",
        campaign_fingerprint="a" * 64,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
    )

    assert attempts == 2
    assert result == [{"node_name": "node-a"}]
