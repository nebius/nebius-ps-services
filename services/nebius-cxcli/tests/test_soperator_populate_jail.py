from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from nebius_cxcli.soperator_populate_jail import (
    PopulateJailSnapshot,
    plan_populate_jail_refresh,
    populate_jail_refresh_values,
    populate_jail_steady_state_values,
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
    ) -> None:
        self.job_failed = job_failed
        self.consumer_pod_batches = [tuple(batch) for batch in consumer_pod_batches or [()]]
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
        if command[-4:] == ("job", "cluster-populate-jail", "-o", "json"):
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
                        "metadata": {"name": "cluster-populate-jail", "uid": "new-job"},
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
    assert "downscaleAndOverwritePopulateJail" in manual.manual_instruction


def test_populate_jail_refresh_values_set_temporary_and_steady_state_modes() -> None:
    values = {"maintenance": "none", "populateJail": {"overwrite": False}, "other": "kept"}

    refresh = populate_jail_refresh_values(values)
    steady = populate_jail_steady_state_values(refresh)

    assert refresh["maintenance"] == "downscaleAndOverwritePopulateJail"
    assert refresh["populateJail"]["overwrite"] is True
    assert steady["maintenance"] == "none"
    assert steady["populateJail"]["overwrite"] is False
    assert values["populateJail"]["overwrite"] is False


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
