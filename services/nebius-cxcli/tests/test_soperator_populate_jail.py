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
    inspect_active_passive_populate_jail_progress,
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
                                        {
                                            "name": "populate-jail",
                                            "image": "repo/populate-jail:target",
                                        }
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
        endpoint_slice_command = (
            "get",
            "endpointslices.discovery.k8s.io",
            "-l",
            "kubernetes.io/service-name=login",
            "-o",
            "json",
        )
        if command[-len(endpoint_slice_command) :] == endpoint_slice_command:
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


class _ProgressRunner:
    def __init__(
        self,
        *,
        complete_after: int = 1,
        job_uid: str = "new-job",
        log_error: bool = False,
        incomplete_summary: bool = False,
        pod_uid: str = "pod-uid",
    ) -> None:
        self.complete_after = complete_after
        self.job_uid = job_uid
        self.log_error = log_error
        self.incomplete_summary = incomplete_summary
        self.pod_uid = pod_uid
        self.job_reads = 0
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
        if command[-4:] == ("job", "populate-slot-b", "-o", "json"):
            self.job_reads += 1
            complete = self.job_reads >= self.complete_after
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "metadata": {
                            "name": "populate-slot-b",
                            "uid": self.job_uid,
                            "creationTimestamp": "2099-01-01T00:00:00Z",
                        },
                        "spec": {
                            "backoffLimit": 0,
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "name": "populate-jail",
                                            "image": "repo/populate-jail:target",
                                        }
                                    ]
                                }
                            },
                        },
                        "status": (
                            {
                                "succeeded": 1,
                                "startTime": "2099-01-01T00:00:00Z",
                                "conditions": [{"type": "Complete", "status": "True"}],
                            }
                            if complete
                            else {"active": 1, "startTime": "2099-01-01T00:00:00Z"}
                        ),
                    }
                ),
            )
        if "job-name=populate-slot-b" in command:
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": "populate-slot-b-pod",
                                    "uid": self.pod_uid,
                                    "ownerReferences": [
                                        {
                                            "kind": "Job",
                                            "uid": self.job_uid,
                                            "controller": True,
                                        }
                                    ],
                                },
                                "spec": {"nodeName": "system-0"},
                                "status": {
                                    "phase": "Running",
                                    "containerStatuses": [
                                        {
                                            "name": "populate-jail",
                                            "restartCount": 0,
                                            "state": {"running": {}},
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                ),
            )
        if command[-1:] == ("json",) and "events" in command:
            return _CommandResult(0, json.dumps({"items": []}))
        if "logs" in command:
            if self.log_error:
                return _CommandResult(1, "", "temporary log stream failure")
            return _CommandResult(
                0,
                "\n".join(
                    (
                        '{"message_type":"status","percent_done":1,'
                        '"total_files":20,"files_restored":10,'
                        '"total_bytes":100,"bytes_restored":50}',
                        '{"message_type":"summary","percent_done":1,'
                        f'"total_files":20,"files_restored":{19 if self.incomplete_summary else 20},'
                        '"total_bytes":100,"bytes_restored":100}',
                        "sensitive raw line that must not enter the checkpoint payload",
                    )
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
    assert "canonical jail volume-source alias" in manual.manual_instruction
    assert "SConfigController" in manual.manual_instruction
    assert "every enabled alias consumer" in manual.manual_instruction


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
            "store": {"volumeKey": "jail"},
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
            "controller": {
                "volumes": {
                    "jail": {
                        "volumeSourceName": "jail-rootfs-slot-a",
                        "persistentVolumeClaim": {"claimName": "stale-controller-pvc"},
                    },
                    "spool": {"volumeSourceName": "controller-spool"},
                }
            },
            "login": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
            "rest": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
        },
        "nodesets": [
            {
                "name": "worker",
                "slurmd": {
                    "volumes": {
                        "jail": {
                            "volumeSourceName": "jail-rootfs-slot-a",
                            "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-a-pvc"},
                        }
                    }
                },
            }
        ],
        "volumeSources": [
            {
                "name": "jail",
                "persistentVolumeClaim": {
                    "claimName": "jail-rootfs-slot-a-pvc",
                    "readOnly": False,
                },
            },
        ],
    }

    slots = active_passive_jail_rootfs_slots(values)
    switched = switch_active_passive_jail_rootfs_values(values)

    assert slots.active_slot == "slot-a"
    assert slots.passive_slot == "slot-b"
    assert switched["jailRootfs"]["activeSlot"] == "slot-b"
    assert switched["jailRootfs"]["passiveSlot"] == "slot-a"
    for role in ("controller", "login"):
        assert switched["slurmNodes"][role]["volumes"]["jail"] == {
            "volumeSourceName": "jail-rootfs-slot-b"
        }
    assert "volumes" not in switched["slurmNodes"]["rest"]
    assert switched["nodesets"][0]["slurmd"]["volumes"]["jail"] == {
        "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}
    }
    volume_sources = {item["name"]: item for item in switched["volumeSources"]}
    assert set(volume_sources) == {"controller-spool", "jail"}
    assert volume_sources["controller-spool"]["persistentVolumeClaim"]["claimName"] == (
        "controller-spool-pvc"
    )
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-b-pvc"
    )


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
    assert "priorityClassName" not in scheduling
    assert (
        active_passive_populate_jail_job_scheduling(
            {"populateJail": {"k8sNodeFilterName": "missing"}},
        )
        == {}
    )


def test_active_passive_populate_job_scheduling_does_not_invent_priority_class() -> None:
    assert (
        active_passive_populate_jail_job_scheduling(
            {
                "clusterName": "target-cluster",
                "populateJail": {"k8sNodeFilterName": "missing"},
            }
        )
        == {}
    )

    assert active_passive_populate_jail_job_scheduling(
        {
            "clusterName": "target-cluster",
            "populateJail": {
                "k8sNodeFilterName": "missing",
                "priorityClass": "accepted-populate-jail",
            },
        }
    ) == {"priorityClassName": "accepted-populate-jail"}


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
    assert manifest["spec"]["activeDeadlineSeconds"] == 2700
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


def test_active_passive_monitor_reads_owner_bound_pod_and_structured_logs() -> None:
    progress = inspect_active_passive_populate_jail_progress(
        _ProgressRunner(),
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_image="repo/populate-jail:target",
    )

    payload = progress.as_payload()
    assert progress.status == "completed"
    assert progress.pod_uid == "pod-uid"
    assert progress.summary_complete is True
    assert progress.summary_seen is True
    assert progress.files_restored == progress.total_files == 20
    assert progress.bytes_restored == progress.total_bytes == 100
    assert progress.log_line_count == 3
    assert progress.log_sha256
    assert "percent_done" not in payload["progress"]
    assert "sensitive raw line" not in json.dumps(payload)


def test_active_passive_monitor_rejects_job_uid_replacement() -> None:
    runner = _ProgressRunner(job_uid="replacement-job")
    progress = inspect_active_passive_populate_jail_progress(
        runner,
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_image="repo/populate-jail:target",
    )

    assert progress.status == "identity-drift"
    assert "expected Job UID new-job" in progress.reason
    assert not any("logs" in call for call in runner.calls)


def test_active_passive_monitor_rejects_checkpointed_pod_uid_replacement() -> None:
    progress = inspect_active_passive_populate_jail_progress(
        _ProgressRunner(pod_uid="replacement-pod"),
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_pod_uid="checkpointed-pod",
        expected_image="repo/populate-jail:target",
    )

    assert progress.status == "identity-drift"
    assert "expected Pod UID checkpointed-pod" in progress.reason


def test_active_passive_monitor_treats_log_read_failure_as_nonfatal() -> None:
    progress = inspect_active_passive_populate_jail_progress(
        _ProgressRunner(log_error=True),
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_image="repo/populate-jail:target",
    )

    assert progress.status == "completed"
    assert progress.log_status == "unavailable"


def test_active_passive_monitor_fails_incomplete_structured_summary() -> None:
    progress = inspect_active_passive_populate_jail_progress(
        _ProgressRunner(incomplete_summary=True),
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_image="repo/populate-jail:target",
    )

    assert progress.status == "failed"
    assert progress.summary_seen is True
    assert progress.summary_complete is False
    assert "incomplete file or byte restoration" in progress.reason


def test_active_passive_wait_reports_running_then_complete_without_replacing_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.soperator_populate_jail.time.sleep",
        lambda _seconds: None,
    )
    runner = _ProgressRunner(complete_after=2)
    observations = []

    snapshot = wait_for_active_passive_populate_jail_job(
        runner,
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_image="repo/populate-jail:target",
        timeout_seconds=30,
        poll_interval_seconds=1,
        progress_emit_interval_seconds=30,
        on_progress=observations.append,
    )

    assert snapshot.job_complete is True
    assert [item.status for item in observations] == ["running", "completed"]
    assert not any("delete" in call or "apply" in call for call in runner.calls)


def test_active_passive_wait_reuses_checkpointed_absolute_deadline() -> None:
    observations = []

    with pytest.raises(RuntimeError, match="exact Job was left untouched"):
        wait_for_active_passive_populate_jail_job(
            lambda _args, **_kwargs: _CommandResult(1, "", "temporary API failure"),
            namespace="soperator",
            job_name="populate-slot-b",
            expected_job_uid="new-job",
            expected_image="repo/populate-jail:target",
            timeout_seconds=2700,
            absolute_deadline_at="2000-01-01T00:00:00+00:00",
            on_progress=observations.append,
        )

    assert [item.status for item in observations] == ["unavailable", "timed-out"]


def test_active_passive_wait_marks_non_progressing_exact_job_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((0.0, 0.0, 0.0, 301.0, 301.0))
    monkeypatch.setattr(
        "nebius_cxcli.soperator_populate_jail.time.monotonic",
        lambda: next(ticks),
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_populate_jail.time.sleep",
        lambda _seconds: None,
    )
    observations = []

    with pytest.raises(RuntimeError, match="stalled.*left untouched"):
        wait_for_active_passive_populate_jail_job(
            _ProgressRunner(complete_after=999),
            namespace="soperator",
            job_name="populate-slot-b",
            expected_job_uid="new-job",
            expected_image="repo/populate-jail:target",
            timeout_seconds=2700,
            poll_interval_seconds=1,
            stall_timeout_seconds=300,
            on_progress=observations.append,
        )

    assert observations[-1].status == "stalled"


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
        call[-4:] == ("statefulsets.apps.kruise.io", "login", "-o", "json") for call in runner.calls
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
