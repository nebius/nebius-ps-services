from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from nebius_cxcli.soperator_populate_jail import (
    PopulateJailSnapshot,
    active_passive_jail_rootfs_slots,
    active_passive_populate_jail_job_manifest,
    inspect_active_passive_populate_jail_progress,
    login_service_ready_endpoint_count,
    observe_login_service_continuity,
    switch_active_passive_jail_rootfs_values,
    wait_for_active_passive_populate_jail_job,
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
        ready_endpoints: int = 1,
    ) -> None:
        self.ready_endpoints = ready_endpoints
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
                        "status": {
                            "succeeded": 1,
                            "conditions": [{"type": "Complete", "status": "True"}],
                        },
                    }
                ),
            )
        if command[-3:] == ("services", "-o", "json"):
            return _CommandResult(
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "login", "uid": "service-uid"},
                                "spec": {"type": "LoadBalancer", "clusterIP": "10.0.0.20"},
                                "status": {
                                    "loadBalancer": {"ingress": [{"ip": "192.0.2.10"}]}
                                },
                            }
                        ]
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
        failed_init: bool = False,
    ) -> None:
        self.complete_after = complete_after
        self.job_uid = job_uid
        self.log_error = log_error
        self.incomplete_summary = incomplete_summary
        self.pod_uid = pod_uid
        self.failed_init = failed_init
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
                                "failed": 1,
                                "startTime": "2099-01-01T00:00:00Z",
                                "conditions": [{"type": "Failed", "status": "True"}],
                            }
                            if self.failed_init
                            else
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
                                    "phase": "Failed" if self.failed_init else "Running",
                                    "initContainerStatuses": (
                                        [
                                            {
                                                "name": "mount-gate-populate-jail",
                                                "state": {
                                                    "terminated": {
                                                        "exitCode": 1,
                                                        "reason": "Error",
                                                    }
                                                },
                                            }
                                        ]
                                        if self.failed_init
                                        else []
                                    ),
                                    "containerStatuses": [
                                        {
                                            "name": "populate-jail",
                                            "restartCount": 0,
                                            "state": (
                                                {"waiting": {"reason": "PodInitializing"}}
                                                if self.failed_init
                                                else {"running": {}}
                                            ),
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
    gate = pod_spec["initContainers"][0]
    assert gate["name"] == "mount-gate-populate-jail"
    assert "@sha256:" in gate["image"]
    assert gate["volumeMounts"] == [
        {"name": "jail-rootfs", "mountPath": "/proof", "readOnly": True}
    ]
    container = pod_spec["containers"][0]
    assert container["image"] == "repo/populate-jail:target"
    assert container["env"] == [{"name": "OVERWRITE", "value": "1"}]
    assert container["securityContext"] == {
        "capabilities": {"add": ["SYS_ADMIN", "SETFCAP"]}
    }
    assert container["volumeMounts"] == [{"name": "jail-rootfs", "mountPath": "/mnt/jail"}]
    assert pod_spec["volumes"] == [
        {
            "name": "jail-rootfs",
            "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"},
        }
    ]


def test_active_passive_populate_job_can_bind_protected_operation_identity() -> None:
    manifest = active_passive_populate_jail_job_manifest(
        namespace="soperator",
        target_ref="cxcli-source",
        image="repo/populate-jail@sha256:" + "a" * 64,
        passive_slot="slot-b",
        passive_pvc="jail-rootfs-slot-b-pvc",
        operation_purpose="populate-source",
    )

    assert (
        manifest["metadata"]["labels"]["soperator.nebius.ai/protected-data-plane"]
        == "populate-source"
    )
    assert (
        manifest["spec"]["template"]["metadata"]["labels"][
            "soperator.nebius.ai/protected-data-plane"
        ]
        == "populate-source"
    )


def test_operation_scratch_populate_job_omits_persistent_mount_receipt_gate() -> None:
    manifest = active_passive_populate_jail_job_manifest(
        namespace="soperator",
        target_ref="cxcli-source",
        image="repo/populate-jail@sha256:" + "a" * 64,
        passive_slot="slot-b",
        passive_pvc="cxcli-rootfs-scratch",
        operation_purpose="admission-source",
        require_mount_receipt=False,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    assert "initContainers" not in pod_spec
    container = pod_spec["containers"][0]
    assert container["name"] == "populate-jail"
    assert container["env"] == [{"name": "OVERWRITE", "value": "1"}]
    assert container["securityContext"] == {
        "capabilities": {"add": ["SYS_ADMIN", "SETFCAP"]}
    }


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


def test_active_passive_monitor_reports_failed_init_gate_before_waiting_main() -> None:
    progress = inspect_active_passive_populate_jail_progress(
        _ProgressRunner(failed_init=True),
        namespace="soperator",
        job_name="populate-slot-b",
        expected_job_uid="new-job",
        expected_image="repo/populate-jail:target",
    )

    assert progress.status == "failed"
    assert progress.reason == (
        "init container mount-gate-populate-jail failed: Error (exit 1)"
    )
    assert progress.container_state == "waiting"


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


def test_login_service_ready_endpoint_count_reads_endpoint_slices() -> None:
    runner = _PopulateJailRunner(ready_endpoints=2)

    assert (
        login_service_ready_endpoint_count(
            runner,
            namespace="soperator",
            service_names=("login",),
        )
        == 2
    )


def test_login_service_continuity_observation_is_advisory() -> None:
    result = observe_login_service_continuity(
        _PopulateJailRunner(ready_endpoints=1),
        namespace="soperator",
        tcp_probe=lambda host, port: host == "192.0.2.10" and port == 22,
    )

    assert result["status"] == "available"
    assert result["readyEndpoints"] == 1
    assert result["tcp22Reachable"] is True
    assert result["services"][0]["uid"] == "service-uid"
