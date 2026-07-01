from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

import pytest

from nebius_cxcli.soperator_scaling import (
    SOPERATOR_SCALE_DOWN,
    SOPERATOR_SCALE_OWNERSHIP_EXTERNAL,
    SOPERATOR_SCALE_UP,
    SoperatorNodeGroupScale,
    SoperatorWorkerScalePlan,
    SoperatorWorkerScaleRequest,
    build_worker_scale_plan,
    execute_worker_scale_plan,
)


@dataclass(frozen=True)
class _Result:
    args: tuple[str, ...]
    returncode: int = 0
    stdout: str = "{}"
    stderr: str = ""


class _Runner:
    def __init__(
        self,
        *,
        ephemeral: bool,
        fixed: bool = False,
        map_node_group: bool = True,
        fail_node_group_update: bool = False,
    ) -> None:
        self.ephemeral = ephemeral
        self.fixed = fixed
        self.map_node_group = map_node_group
        self.fail_node_group_update = fail_node_group_update
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 120,
        check: bool = True,
    ) -> _Result:
        del input_text, timeout_seconds, check
        command = tuple(str(part) for part in args)
        self.commands.append(command)
        if command[:2] == ("kubectl", "--context") and "nodeset" in command and "get" in command:
            return _Result(
                command,
                stdout=json.dumps(
                    {
                        "metadata": {"name": "worker"},
                        "spec": {
                            "replicas": 4,
                            "ephemeralNodes": self.ephemeral,
                            "initialNumberEphemeralNodes": 2,
                        },
                    }
                ),
            )
        if "nodesetpowerstate" in command and "get" in command:
            return _Result(
                command,
                stdout=json.dumps(
                    {
                        "metadata": {"name": "worker"},
                        "spec": {"nodeSetRef": "worker", "activeNodes": [0, 1, 2]},
                    }
                ),
            )
        if "pods" in command and "get" in command:
            return _Result(
                command,
                stdout=json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": f"worker-{ordinal}",
                                    "labels": {"slurm.nebius.ai/nodeset-name": "worker"},
                                },
                                "spec": {"nodeName": f"node-{ordinal}"},
                            }
                            for ordinal in range(4)
                        ]
                    }
                ),
            )
        if command[:3] == ("kubectl", "--context", "ctx") and command[-4:] == (
            "nodes",
            "-o",
            "json",
            "--request-timeout=20s",
        ):
            return _Result(
                command,
                stdout=json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": f"node-{ordinal}",
                                    "labels": {
                                        "nebius.com/node-group-id": "nodegroup-worker",
                                        "nebius.com/node-group": "worker",
                                    },
                                }
                            }
                            for ordinal in range(4)
                        ]
                    }
                ),
            )
        if command[:4] == ("nebius", "mk8s", "node-group", "list"):
            if not self.map_node_group:
                return _Result(command, stdout=json.dumps({"items": []}))
            spec = {"fixed_node_count": 4} if self.fixed else {
                "autoscaling": {"min_node_count": 3, "max_node_count": 4}
            }
            return _Result(
                command,
                stdout=json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"id": "nodegroup-worker", "name": "worker"},
                                "spec": spec,
                                "status": {"target_node_count": 4},
                            }
                        ]
                    }
                ),
            )
        if command[:4] == ("nebius", "mk8s", "node-group", "update"):
            if self.fail_node_group_update:
                return _Result(command, returncode=1, stdout="", stderr="provider update failed")
            return _Result(command)
        if "patch" in command:
            return _Result(command)
        return _Result(command, returncode=1, stdout="", stderr="unexpected command")


def _request(to_workers: int) -> SoperatorWorkerScaleRequest:
    return SoperatorWorkerScaleRequest(
        ownership=SOPERATOR_SCALE_OWNERSHIP_EXTERNAL,
        direction=SOPERATOR_SCALE_DOWN,
        target_ref="external",
        namespace="soperator",
        nodeset="worker",
        to_workers=to_workers,
        kube_context="ctx",
        cluster_id="mk8scluster",
        project_id="project",
    )


def test_ephemeral_scale_down_patches_power_state_and_lowers_autoscaling_min() -> None:
    runner = _Runner(ephemeral=True)
    plan = build_worker_scale_plan(runner, _request(1))

    assert plan.ephemeral is True
    assert plan.current_active_ordinals == (0, 1, 2)
    assert plan.desired_active_ordinals == (0,)
    assert plan.affected_pods == ("worker-1", "worker-2")

    execute_worker_scale_plan(runner, plan, adjust_node_group=True)

    patch_commands = [command for command in runner.commands if "nodesetpowerstate" in command and "patch" in command]
    assert patch_commands
    assert '{"spec": {"activeNodes": [0]}}' in patch_commands[-1]
    assert any("--autoscaling-min-node-count" in command and "1" in command for command in runner.commands)


def test_ephemeral_scale_down_supports_explicit_non_tail_ordinals() -> None:
    runner = _Runner(ephemeral=True)
    request = replace(_request(2), worker_ordinals=(1,))
    plan = build_worker_scale_plan(runner, request)

    assert plan.desired_active_ordinals == (0, 2)
    assert plan.affected_pods == ("worker-1",)

    execute_worker_scale_plan(runner, plan, adjust_node_group=True)

    patch_commands = [
        command for command in runner.commands if "nodesetpowerstate" in command and "patch" in command
    ]
    assert patch_commands
    assert '{"spec": {"activeNodes": [0, 2]}}' in patch_commands[-1]


def test_non_ephemeral_scale_down_patches_nodeset_and_fixed_node_group() -> None:
    runner = _Runner(ephemeral=False, fixed=True)
    plan = build_worker_scale_plan(runner, _request(0))

    assert plan.ephemeral is False
    assert plan.desired_replicas == 0
    assert plan.affected_pods == ("worker-0", "worker-1", "worker-2", "worker-3")

    execute_worker_scale_plan(runner, plan, adjust_node_group=True)

    patch_commands = [command for command in runner.commands if "nodeset" in command and "patch" in command]
    assert patch_commands
    assert '{"spec": {"replicas": 0}}' in patch_commands[-1]
    assert any("--fixed-node-count" in command and "0" in command for command in runner.commands)


def test_non_ephemeral_scale_down_rejects_interior_explicit_ordinals() -> None:
    runner = _Runner(ephemeral=False)
    request = replace(_request(2), worker_ordinals=(1, 3))

    with pytest.raises(RuntimeError, match="tail ordinal removal only"):
        build_worker_scale_plan(runner, request)


def test_ephemeral_scale_up_rejects_count_above_node_group_autoscaling_max() -> None:
    runner = _Runner(ephemeral=True)
    request = replace(_request(5), direction=SOPERATOR_SCALE_UP)

    with pytest.raises(RuntimeError, match="maximum replicas is 4"):
        build_worker_scale_plan(runner, request)


def test_external_execute_fails_before_patch_when_node_group_mapping_is_missing() -> None:
    runner = _Runner(ephemeral=True, map_node_group=False)
    plan = build_worker_scale_plan(runner, _request(1))

    assert plan.node_group is None
    runner.commands.clear()

    with pytest.raises(RuntimeError, match="refusing to mutate worker pods"):
        execute_worker_scale_plan(runner, plan, adjust_node_group=True)

    assert not any("patch" in command for command in runner.commands)


def test_external_scale_up_updates_fixed_node_group_before_nodeset_patch() -> None:
    runner = _Runner(ephemeral=False, fixed=True, fail_node_group_update=True)
    plan = SoperatorWorkerScalePlan(
        request=replace(_request(4), direction=SOPERATOR_SCALE_UP),
        current_replicas=2,
        desired_replicas=4,
        ephemeral=False,
        current_active_ordinals=(0, 1),
        desired_active_ordinals=(0, 1, 2, 3),
        affected_ordinals=(2, 3),
        affected_pods=("worker-2", "worker-3"),
        node_group=SoperatorNodeGroupScale(
            node_group_id="nodegroup-worker",
            node_group_name="worker",
            mode="fixed",
            current_count=2,
            min_count=None,
            max_count=None,
            desired_min_count=None,
            desired_max_count=None,
            desired_fixed_count=4,
        ),
        warnings=(),
    )

    with pytest.raises(RuntimeError, match="provider update failed"):
        execute_worker_scale_plan(runner, plan, adjust_node_group=True)

    assert any(command[:4] == ("nebius", "mk8s", "node-group", "update") for command in runner.commands)
    assert not any("patch" in command for command in runner.commands)
