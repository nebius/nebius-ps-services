from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from nebius_cxcli.soperator_populate_jail import (
    PopulateJailSnapshot,
    active_passive_jail_rootfs_slots,
    active_passive_populate_jail_job_manifest,
    active_passive_populate_jail_job_scheduling,
    login_service_ready_endpoint_count,
    plan_populate_jail_refresh,
    populate_jail_refresh_values,
    populate_jail_steady_state_values,
    switch_active_passive_jail_rootfs_values,
    wait_for_active_passive_populate_jail_job,
    wait_for_login_service_ready_endpoints,
    wait_for_login_statefulset_rollout_with_ready_endpoint_guard,
    wait_for_populate_jail_consumers_down,
    wait_for_populate_jail_refresh,
)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _PopulateJailRunner:
    def __init__(
        self,
        *,
        job_failed: bool = False,
        consumer_pod_batches: Sequence[Sequence[str]] | None = None,
        ready_endpoints: int = 1,
        statefulset_ready: bool = True,
    ) -> None:
        self.job_failed = job_failed
        self.consumer_pod_batches = [tuple(batch) for batch in consumer_pod_batches or [()]]
        self.ready_endpoints = ready_endpoints
        self.statefulset_ready = statefulset_ready
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> _CommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        self.calls.append(command)
        if command[-4:] == ("slurmcluster", "cluster", "-o", "json"):
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "metadata": {"name": "cluster"},
                        "spec": {"populateJail": {"image": "repo/populate-jail:target"}},
                    }
                ),
            )
        if command[-4:] in {
            ("job", "cluster-populate-jail", "-o", "json"),
            ("job", "cluster-populate-jail-passive-slotb", "-o", "json"),
        }:
            status: dict[str, Any]
            if self.job_failed:
                status = {"failed": 7, "conditions": [{"type": "Failed", "status": "True"}]}
            else:
                status = {
                    "succeeded": 1,
                    "conditions": [{"type": "Complete", "status": "True"}],
                }
            return _CommandResult(
                0,
                json.dumps(
                    {
                            "metadata": {"name": command[-3], "uid": "new-job"},
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {"name": "populate-jail", "image": "repo/populate-jail:target"}
                                    ]
                                }
                            }
                        },
                        "status": status,
                    }
                ),
            )
        if command[-3:] == ("pods", "-o", "json"):
            names = self.consumer_pod_batches.pop(0) if self.consumer_pod_batches else ()
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "items": [
                            {"metadata": {"name": name}, "status": {"phase": "Running"}}
                            for name in names
                        ]
                    }
                ),
            )
        if command[-3:] == ("services", "-o", "json"):
            return _CommandResult(
                0,
                json.dumps({"items": [{"metadata": {"name": "login"}}]}),
            )
        if command[-4:] == ("statefulsets.apps.kruise.io", "login", "-o", "json"):
            replicas = 2
            ready = replicas if self.statefulset_ready else 1
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "metadata": {"name": "login", "generation": 1},
                        "spec": {"replicas": replicas},
                        "status": {
                            "observedGeneration": 1,
                            "readyReplicas": ready,
                            "updatedReplicas": ready,
                        },
                    }
                ),
            )
        if "endpointslices.discovery.k8s.io" in command:
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login-ready"},
                                "endpoints": [
                                    {"conditions": {"ready": True}}
                                    for _ in range(self.ready_endpoints)
                                ],
                            }
                        ]
                    }
                ),
            )
        return _CommandResult(1, "", "not found")


def _snapshot(image: str = "repo/populate-jail:old") -> PopulateJailSnapshot:
    return PopulateJailSnapshot(
        slurmcluster_name="cluster",
        image=image,
        job_name="cluster-populate-jail",
        job_uid="old-job",
        job_complete=True,
        job_image=image,
        status="collected",
    )


def test_populate_jail_plan_skips_when_chart_and_image_unchanged() -> None:
    before = _snapshot("repo/populate-jail:same")
    plan = plan_populate_jail_refresh(
        mode="auto",
        chart_changed=False,
        before=before,
        after_chart=before,
        namespace="soperator",
    )

    assert plan.required is False
    assert "unchanged" in plan.reason


def test_populate_jail_plan_refreshes_when_target_image_changes() -> None:
    plan = plan_populate_jail_refresh(
        mode="auto",
        chart_changed=False,
        before=_snapshot("repo/populate-jail:old"),
        after_chart=_snapshot("repo/populate-jail:new"),
        namespace="soperator",
    )

    assert plan.required is True
    assert "differs" in plan.reason


def test_populate_jail_force_and_manual_modes() -> None:
    before = _snapshot("repo/populate-jail:same")
    forced = plan_populate_jail_refresh(
        mode="force",
        chart_changed=False,
        before=before,
        after_chart=before,
        namespace="soperator",
    )
    manual = plan_populate_jail_refresh(
        mode="manual",
        chart_changed=True,
        before=before,
        after_chart=before,
        namespace="soperator",
    )

    assert forced.required is True
    assert manual.required is True
    assert "passive active/passive slot" in manual.manual_instruction


def test_populate_jail_refresh_values_set_temporary_and_steady_state_modes() -> None:
    values = {"maintenance": "none", "populateJail": {"overwrite": False}, "other": "kept"}

    refresh = populate_jail_refresh_values(values)
    steady = populate_jail_steady_state_values(refresh)

    assert refresh["maintenance"] == "none"
    assert refresh["populateJail"]["overwrite"] is False
    assert refresh["jailRootfs"]["refresh"] == {
        "mode": "populatePassiveSlot",
        "targetSlot": "slot-b",
        "rollbackSlot": "slot-a",
        "status": "planned",
    }
    assert steady["maintenance"] == "none"
    assert steady["populateJail"]["overwrite"] is False
    assert values["populateJail"]["overwrite"] is False


def test_active_passive_slot_selection_and_switch_values() -> None:
    values = {
        "jailRootfs": {
            "strategy": "activePassive",
            "activeSlot": "slot-a",
            "passiveSlot": "slot-b",
            "slots": {
                "slot-a": {
                    "volumeSourceName": "jail-rootfs-slot-a",
                    "pvcName": "jail-rootfs-slot-a-pvc",
                },
                "slot-b": {
                    "volumeSourceName": "jail-rootfs-slot-b",
                    "pvcName": "jail-rootfs-slot-b-pvc",
                },
            },
        },
        "slurmNodes": {
            "controller": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
            "login": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
            "rest": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
        },
        "nodesets": [
            {
                "name": "worker",
                "slurmd": {
                    "volumes": {
                        "jail": {
                            "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-a-pvc"}
                        }
                    }
                },
            }
        ],
    }

    slots = active_passive_jail_rootfs_slots(values)
    switched = switch_active_passive_jail_rootfs_values(values)

    assert slots.active_slot == "slot-a"
    assert slots.passive_slot == "slot-b"
    assert switched["jailRootfs"]["activeSlot"] == "slot-b"
    assert switched["jailRootfs"]["passiveSlot"] == "slot-a"
    assert switched["slurmNodes"]["login"]["volumes"]["jail"]["volumeSourceName"] == (
        "jail-rootfs-slot-b"
    )
    assert switched["nodesets"][0]["slurmd"]["volumes"]["jail"]["persistentVolumeClaim"][
        "claimName"
    ] == "jail-rootfs-slot-b-pvc"


def test_active_passive_populate_job_scheduling_uses_populate_jail_filter() -> None:
    scheduling = active_passive_populate_jail_job_scheduling(
        {
            "clusterName": "prod",
            "populateJail": {"k8sNodeFilterName": "system"},
            "k8sNodeFilters": [
                {
                    "name": "system",
                    "nodeSelector": {"slurm.nebius.ai/nodeset-name": "system"},
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "slurm.nebius.ai/nodeset-name",
                                                "operator": "In",
                                                "values": ["system"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "tolerations": [
                        {
                            "key": "slurm.nebius.ai/nodeset-name",
                            "operator": "Equal",
                            "value": "system",
                            "effect": "NoSchedule",
                        }
                    ],
                }
            ],
        }
    )

    assert scheduling["nodeSelector"] == {"slurm.nebius.ai/nodeset-name": "system"}
    assert scheduling["affinity"]["nodeAffinity"]
    assert scheduling["tolerations"][0]["value"] == "system"
    assert scheduling["priorityClassName"] == "prod-slurm-populate-jail"
    assert active_passive_populate_jail_job_scheduling(
        {"populateJail": {"k8sNodeFilterName": "missing"}},
        target_ref="external-target",
    ) == {"priorityClassName": "soperator-slurm-populate-jail"}


def test_active_passive_populate_job_mounts_passive_slot_pvc() -> None:
    manifest = active_passive_populate_jail_job_manifest(
        namespace="soperator",
        target_ref="cluster",
        image="repo/populate-jail:target",
        passive_slot="slot-b",
        passive_pvc="jail-rootfs-slot-b-pvc",
        scheduling={
            "nodeSelector": {"slurm.nebius.ai/nodeset-name": "system"},
            "tolerations": [
                {
                    "key": "slurm.nebius.ai/nodeset-name",
                    "operator": "Equal",
                    "value": "system",
                    "effect": "NoSchedule",
                }
            ],
            "priorityClassName": "cluster-slurm-populate-jail",
        },
    )

    assert manifest["metadata"]["name"] == "cluster-populate-jail-passive-slotb"
    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["nodeSelector"] == {"slurm.nebius.ai/nodeset-name": "system"}
    assert pod_spec["tolerations"][0]["value"] == "system"
    assert pod_spec["priorityClassName"] == "cluster-slurm-populate-jail"
    container = pod_spec["containers"][0]
    assert container["image"] == "repo/populate-jail:target"
    assert container["volumeMounts"] == [{"name": "jail-rootfs", "mountPath": "/mnt/jail"}]
    assert pod_spec["volumes"] == [
        {
            "name": "jail-rootfs",
            "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"},
        }
    ]


def test_wait_for_active_passive_populate_job_records_completed_image() -> None:
    snapshot = wait_for_active_passive_populate_jail_job(
        _PopulateJailRunner(),
        namespace="soperator",
        job_name="cluster-populate-jail-passive-slotb",
        expected_image="repo/populate-jail:target",
        poll_interval_seconds=1,
        timeout_seconds=1,
    )

    assert snapshot.job_complete is True
    assert snapshot.job_name == "cluster-populate-jail-passive-slotb"
    assert snapshot.job_image == "repo/populate-jail:target"


def test_login_service_ready_endpoint_guard_counts_endpoint_slices() -> None:
    runner = _PopulateJailRunner(ready_endpoints=2)

    result = wait_for_login_service_ready_endpoints(
        runner,
        namespace="soperator",
        target_ref="cluster",
        poll_interval_seconds=1,
        timeout_seconds=1,
    )

    assert result == {"service_names": ["login"], "ready_endpoints": 2}
    assert (
        login_service_ready_endpoint_count(
            runner,
            namespace="soperator",
            service_names=("login",),
        )
        == 2
    )


def test_login_service_rollout_guard_fails_on_zero_ready_endpoints() -> None:
    with pytest.raises(RuntimeError, match="ready endpoints dropped"):
        wait_for_login_statefulset_rollout_with_ready_endpoint_guard(
            _PopulateJailRunner(ready_endpoints=0),
            namespace="soperator",
            target_ref="cluster",
            poll_interval_seconds=1,
            timeout_seconds=1,
        )


def test_login_service_rollout_guard_returns_ready_statefulset() -> None:
    runner = _PopulateJailRunner(ready_endpoints=1)
    result = wait_for_login_statefulset_rollout_with_ready_endpoint_guard(
        runner,
        namespace="soperator",
        target_ref="cluster",
        poll_interval_seconds=1,
        timeout_seconds=1,
    )

    assert result == {"service_names": ["login"], "ready_endpoints": 1}
    assert any(
        call[-4:] == ("statefulsets.apps.kruise.io", "login", "-o", "json")
        for call in runner.calls
    )
    assert not any(call[-4:] == ("statefulset", "login", "-o", "json") for call in runner.calls)


def test_wait_for_populate_jail_refresh_records_completed_target_image() -> None:
    snapshot = wait_for_populate_jail_refresh(
        _PopulateJailRunner(),
        namespace="soperator",
        target_ref="cluster",
        previous_job_uid="old-job",
        expected_image="repo/populate-jail:target",
        poll_interval_seconds=1,
        timeout_seconds=1,
    )

    assert snapshot.job_complete is True
    assert snapshot.job_uid == "new-job"
    assert snapshot.job_image == "repo/populate-jail:target"


def test_wait_for_populate_jail_consumers_down_waits_for_login_and_worker_pods() -> None:
    runner = _PopulateJailRunner(consumer_pod_batches=[("login-0", "worker-gpu-0"), ()])

    snapshot = wait_for_populate_jail_consumers_down(
        runner,
        namespace="soperator",
        target_ref="cluster",
        poll_interval_seconds=1,
        timeout_seconds=2,
    )

    assert snapshot.active_consumer_pods == ()
    pod_list_calls = [call for call in runner.calls if call[-3:] == ("pods", "-o", "json")]
    assert len(pod_list_calls) == 2


def test_wait_for_populate_jail_refresh_fails_failed_job() -> None:
    with pytest.raises(RuntimeError, match="failed"):
        wait_for_populate_jail_refresh(
            _PopulateJailRunner(job_failed=True),
            namespace="soperator",
            target_ref="cluster",
            previous_job_uid="old-job",
            expected_image="repo/populate-jail:target",
            poll_interval_seconds=1,
            timeout_seconds=1,
        )
