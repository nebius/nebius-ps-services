from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from nebius_cxcli.helm_readiness import (
    HelmCommandResult,
    verify_helm_chart_ready,
)


class _FakeHelmReadinessRunner:
    def __init__(self, *, workload_ready: bool = True) -> None:
        self.workload_ready = workload_ready

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> HelmCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if command[:2] == ("helm", "list"):
            return HelmCommandResult(
                command,
                0,
                json.dumps(
                    [
                        {
                            "name": "demo",
                            "namespace": "apps",
                            "chart": "demo-1.2.3",
                            "app_version": "1.2.3",
                            "status": "deployed",
                            "revision": "2",
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ("helm", "get", "manifest"):
            return HelmCommandResult(
                command,
                0,
                """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo
  namespace: apps
spec:
  replicas: 2
""",
                "",
            )
        if command[:5] == ("kubectl", "-n", "apps", "get", "deployment"):
            ready = 2 if self.workload_ready else 1
            return HelmCommandResult(
                command,
                0,
                json.dumps(
                    {
                        "metadata": {"generation": 1},
                        "spec": {"replicas": 2},
                        "status": {
                            "observedGeneration": 1,
                            "readyReplicas": ready,
                            "availableReplicas": ready,
                            "updatedReplicas": ready,
                        },
                    }
                ),
                "",
            )
        return HelmCommandResult(command, 1, "", "not found")


def test_verify_helm_chart_ready_passes_for_deployed_release_and_ready_workload() -> None:
    result = verify_helm_chart_ready(
        command_runner=_FakeHelmReadinessRunner(),
        release_name="demo",
        namespace="apps",
        expected_version="1.2.3",
    )

    assert result.release.chart == "demo-1.2.3"
    assert result.ready_workload_count == 1
    assert "workloads 1/1 ready" in result.summary()


def test_verify_helm_chart_ready_fails_when_workload_not_ready() -> None:
    with pytest.raises(RuntimeError, match="Deployment/apps/demo is not ready"):
        verify_helm_chart_ready(
            command_runner=_FakeHelmReadinessRunner(workload_ready=False),
            release_name="demo",
            namespace="apps",
            expected_version="1.2.3",
        )
