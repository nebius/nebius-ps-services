from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import nebius_vpngw.cli as cli
from nebius_vpngw.agent import main as agent_main
from nebius_vpngw.agent.vm_ha.mtls_actions import execute_mtls_action


def _member(node_id: str, role: str, target: str, root: Path) -> tuple[object, Path]:
    generation_id = "a" * 64
    instance = SimpleNamespace(
        hostname=target,
        vm_ha_node=SimpleNamespace(node_id=node_id, role=SimpleNamespace(value=role)),
        vm_ha_generation=SimpleNamespace(generation_id=generation_id),
    )
    config_path = root / "installed.yaml"
    root.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "vm_ha": {
                    "cluster_id": "cluster-a",
                    "node": {"node_id": node_id},
                    "generation": {"generation_id": generation_id},
                }
            }
        ),
        encoding="utf-8",
    )
    return instance, config_path


def _bootstrap(roots: dict[str, Path]) -> dict[str, dict[str, object]]:
    operation_id = "b" * 64
    receipts: dict[str, dict[str, object]] = {}
    for node_id, compute_id in (("node-a", "compute-a"), ("node-b", "compute-b")):
        response = execute_mtls_action(
            "prepare",
            {
                "operation_id": operation_id,
                "operation_kind": "bootstrap",
                "cluster_id": "cluster-a",
                "node_id": node_id,
                "compute_id": compute_id,
                "target_epoch": 1,
                "peer_epoch": 1,
            },
            state_dir=roots[node_id],
            require_root=False,
        )
        receipts[node_id] = response["result"]  # type: ignore[assignment]
    for node_id in roots:
        peer_id = "node-b" if node_id == "node-a" else "node-a"
        for action, request in (
            (
                "stage-peer",
                {"operation_id": operation_id, "peer_receipt": receipts[peer_id]},
            ),
            ("expand-trust", {"operation_id": operation_id}),
            ("activate", {"operation_id": operation_id}),
        ):
            execute_mtls_action(
                action,
                request,
                state_dir=roots[node_id],
                require_root=False,
            )
        execute_mtls_action(
            "record-observation",
            {
                "operation_id": operation_id,
                "local_certificate_fingerprint": receipts[node_id]["certificate_fingerprint"],
                "peer_certificate_fingerprint": receipts[peer_id]["certificate_fingerprint"],
                "local_epoch": 1,
                "peer_epoch": 1,
                "observation_id": ("c" if node_id == "node-a" else "d") * 64,
            },
            state_dir=roots[node_id],
            require_root=False,
        )
        for action in ("commit", "prune"):
            execute_mtls_action(
                action,
                {"operation_id": operation_id},
                state_dir=roots[node_id],
                require_root=False,
            )
    return receipts


def _preview_plan(tmp_path: Path, *, digest: str = "e" * 64) -> cli._VMHAMTLSRotationPlan:
    active, _active_config = _member("node-a", "active", "active-host", tmp_path / "a")
    passive, _passive_config = _member("node-b", "passive", "passive-host", tmp_path / "b")
    members = (
        cli._VMHAMTLSRotationMember(
            active,
            "active-host",
            "compute-a",
            "node-a",
            "active",
            "a" * 64,
            {
                "operation_id": None,
                "epoch": 1,
                "certificate_fingerprint": "1" * 64,
                "phase": None,
            },
            {},
        ),
        cli._VMHAMTLSRotationMember(
            passive,
            "passive-host",
            "compute-b",
            "node-b",
            "passive",
            "a" * 64,
            {
                "operation_id": None,
                "epoch": 1,
                "certificate_fingerprint": "2" * 64,
                "phase": None,
            },
            {},
        ),
    )
    return cli._VMHAMTLSRotationPlan(
        config_path=tmp_path / "gateway.yaml",
        local_config={},
        project_id="project-a",
        gateway_name="gateway-a",
        cluster_id="cluster-a",
        allocation_id="allocation-a",
        owner_node_id="node-a",
        passive_node_id="node-b",
        operation_id="f" * 64,
        target_epoch=2,
        digest=digest,
        plan_payload={},
        members=members,
        ssh_policy=object(),  # type: ignore[arg-type]
    )


def test_agent_advertises_rotation_quiescence_capability(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["nebius-vpngw-agent", "--agent-capabilities"],
    )

    agent_main.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == cli._AGENT_CAPABILITIES_SCHEMA
    assert cli._VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY in payload["features"]


def test_rotation_capability_preflight_accepts_exact_contract(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(cli, "_build_ssh_base_cmd", lambda *_args, **_kwargs: ["ssh"])
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "schema": cli._AGENT_CAPABILITIES_SCHEMA,
                        "features": [cli._VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY],
                    }
                ),
                stderr="",
            )
        ),
    )

    cli._require_vm_ha_mtls_rotation_agent_capability(
        target="management-target",
        hostname="gateway-a",
        username="ubuntu",
        key_path=None,
        ssh_policy=object(),  # type: ignore[arg-type]
    )

    assert commands == [
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "ubuntu@management-target",
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities",
        ]
    ]


def test_rotation_controller_capability_requires_running_process_evidence() -> None:
    cli._require_vm_ha_mtls_rotation_controller_capability(
        {
            "controller_capabilities": [
                cli._VM_HA_MTLS_ROTATION_QUIESCENCE_CAPABILITY,
            ]
        }
    )

    with pytest.raises(RuntimeError, match="running VM-HA controller") as failure:
        cli._require_vm_ha_mtls_rotation_controller_capability({})

    assert "nebius-vpngw apply" in str(failure.value)


@pytest.mark.parametrize(
    ("result", "message"),
    (
        (SimpleNamespace(returncode=2, stdout="", stderr="usage"), "does not expose"),
        (SimpleNamespace(returncode=0, stdout="not-json", stderr=""), "malformed"),
        (
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "schema": cli._AGENT_CAPABILITIES_SCHEMA,
                        "features": [],
                    }
                ),
                stderr="",
            ),
            "missing the required",
        ),
    ),
)
def test_rotation_capability_preflight_rejects_installed_skew(
    monkeypatch: pytest.MonkeyPatch,
    result: SimpleNamespace,
    message: str,
) -> None:
    monkeypatch.setattr(cli, "_build_ssh_base_cmd", lambda *_args, **_kwargs: ["ssh"])
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: result)

    with pytest.raises(RuntimeError, match=message) as failure:
        cli._require_vm_ha_mtls_rotation_agent_capability(
            target="management-target",
            hostname="gateway-a",
            username="ubuntu",
            key_path=None,
            ssh_policy=object(),  # type: ignore[arg-type]
        )

    assert "nebius-vpngw apply" in str(failure.value)


def test_vm_ha_rotate_mtls_dry_run_has_no_writer_or_remote_effect(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    plan = _preview_plan(tmp_path)
    effects: list[str] = []
    monkeypatch.setattr(cli, "_inspect_vm_ha_mtls_rotation", lambda _path: plan)
    monkeypatch.setattr(cli, "_execute_vm_ha_mtls_rotation", lambda _plan: effects.append("run"))

    result = CliRunner().invoke(
        cli.app,
        ["vm-ha", "--rotate-mtls", "--local-config-file", str(config_path), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert plan.digest in result.output
    assert "dry-run makes no changes" in result.output
    assert "Plan: rotate 2 members, passive first, target epoch 2." in result.output
    assert "vm-ha-mtls-rotation-preview-v1" not in result.output
    assert "vm-ha-mtls-rotation-result-v1" not in result.output
    assert effects == []


@pytest.mark.parametrize(
    "incompatible_arguments",
    (
        ("--output", "candidate.yaml"),
        ("--force",),
        ("--standby-auto-healing", "enabled"),
        ("--region", "eu-north1"),
        ("--output-format", "json"),
    ),
)
def test_vm_ha_rotate_mtls_rejects_other_facade_modes_before_dispatch(
    incompatible_arguments: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    dispatched: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_run_vm_ha_mtls_rotation",
        lambda path, **_kwargs: dispatched.append(path),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "vm-ha",
            "--rotate-mtls",
            "--local-config-file",
            str(config_path),
            *incompatible_arguments,
        ],
    )

    assert result.exit_code == 2
    assert "--rotate-mtls cannot be combined" in result.output
    assert dispatched == []


def test_vm_ha_rotate_mtls_dispatches_before_general_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    dispatched: list[tuple[Path, bool, str | None]] = []
    monkeypatch.setattr(
        cli,
        "_run_vm_ha_mtls_rotation",
        lambda path, *, dry_run, approve: dispatched.append((path, dry_run, approve)),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_vm_ha_effective_config",
        lambda **_kwargs: pytest.fail("rotation entered ordinary VM-HA convergence"),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "vm-ha",
            "--rotate-mtls",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "text",
        ],
    )

    assert result.exit_code == 0, result.output
    assert dispatched == [(config_path, True, None)]


def test_bare_vm_ha_never_dispatches_mtls_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    healthy = cli.VMHACommandResult(
        outcome=cli.VMHACommandOutcome.HEALTHY,
        classification=cli.VMHACommandClassification.HEALTHY,
        health=cli.VMHACommandHealth.HEALTHY,
        effective_config_file=config_path,
    )
    monkeypatch.setattr(
        cli,
        "_run_vm_ha_mtls_rotation",
        lambda *_args, **_kwargs: pytest.fail("bare vm-ha dispatched rotation"),
    )
    monkeypatch.setattr(cli, "_resolve_vm_ha_effective_config", lambda **_kwargs: healthy)

    result = CliRunner().invoke(
        cli.app,
        ["vm-ha", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "VM-HA is healthy now." in result.output


def test_rotation_plan_labels_inhibition_only_recovery_as_resume(tmp_path: Path) -> None:
    plan = _preview_plan(tmp_path)
    plan.members[1].mtls["inhibition_operation_id"] = plan.operation_id

    summary, _digest = cli._render_vm_ha_mtls_rotation_plan(plan)

    assert summary.startswith("Plan: resume ")


def test_vm_ha_rotate_mtls_rejects_plan_drift_after_lock(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    first = _preview_plan(tmp_path, digest="1" * 64)
    second = _preview_plan(tmp_path, digest="2" * 64)
    plans = iter((first, second))
    effects: list[str] = []

    class _Lock:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(cli, "_inspect_vm_ha_mtls_rotation", lambda _path: next(plans))
    monkeypatch.setattr(cli, "VMHAApplyLock", _Lock)
    monkeypatch.setattr(cli, "_execute_vm_ha_mtls_rotation", lambda _plan: effects.append("run"))

    result = CliRunner().invoke(
        cli.app,
        [
            "vm-ha",
            "--rotate-mtls",
            "--local-config-file",
            str(config_path),
            "--approve",
            first.digest,
        ],
    )

    assert result.exit_code == 1
    assert "plan drifted" in result.output
    assert effects == []


def test_vm_ha_rotate_mtls_rejects_mismatched_approval_before_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    plan = _preview_plan(tmp_path)
    monkeypatch.setattr(cli, "_inspect_vm_ha_mtls_rotation", lambda _path: plan)
    monkeypatch.setattr(
        cli,
        "VMHAApplyLock",
        lambda **_kwargs: pytest.fail("approval mismatch acquired the writer lock"),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "vm-ha",
            "--rotate-mtls",
            "--local-config-file",
            str(config_path),
            "--approve",
            "0" * 64,
        ],
    )

    assert result.exit_code == 1
    assert "approval digest does not match" in " ".join(result.output.split())


@pytest.mark.parametrize(
    ("input_text", "expected_exit_code", "should_execute"),
    (("y\n", 0, True), ("n\n", 1, False), ("", 1, False)),
)
def test_vm_ha_rotate_mtls_interactive_confirmation_boundary(
    input_text: str,
    expected_exit_code: int,
    should_execute: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    plan = _preview_plan(tmp_path)
    lock_entries: list[str] = []
    effects: list[str] = []

    class _Lock:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            lock_entries.append("entered")
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(cli, "_inspect_vm_ha_mtls_rotation", lambda _path: plan)
    monkeypatch.setattr(cli, "VMHAApplyLock", _Lock)
    monkeypatch.setattr(cli, "_execute_vm_ha_mtls_rotation", lambda _plan: effects.append("run"))

    result = CliRunner().invoke(
        cli.app,
        ["vm-ha", "--rotate-mtls", "--local-config-file", str(config_path)],
        input=input_text,
    )

    assert result.exit_code == expected_exit_code, result.output
    assert lock_entries == (["entered"] if should_execute else [])
    assert effects == (["run"] if should_execute else [])


def test_rotation_switches_passive_first_records_three_rounds_and_releases_inhibition(
    tmp_path: Path, monkeypatch
) -> None:
    roots = {"node-a": tmp_path / "a", "node-b": tmp_path / "b"}
    _bootstrap(roots)
    active_instance, active_config = _member("node-a", "active", "active-host", roots["node-a"])
    passive_instance, passive_config = _member("node-b", "passive", "passive-host", roots["node-b"])
    config_paths = {"node-a": active_config, "node-b": passive_config}
    targets = {"active-host": "node-a", "passive-host": "node-b"}
    trace: list[tuple[str, str]] = []
    sequences = {"node-a": 0, "node-b": 0}

    class _SSH:
        def __init__(self, ssh_policy=None) -> None:
            pass

        def run_vm_ha_mtls_action(self, target, instance_name, local_config, *, action, request):
            node_id = targets[target]
            trace.append((node_id, action))
            return execute_mtls_action(
                action,
                request,
                state_dir=roots[node_id],
                config_path=config_paths[node_id],
                require_root=False,
            )

    def wait_status(*, target, predicate, expected_apply_locked, **_kwargs):
        assert expected_apply_locked is False
        node_id = targets[target]
        peer_id = "node-b" if node_id == "node-a" else "node-a"
        local = execute_mtls_action("status", {}, state_dir=roots[node_id], require_root=False)[
            "result"
        ]
        peer = execute_mtls_action("status", {}, state_dir=roots[peer_id], require_root=False)[
            "result"
        ]
        sequences[node_id] += 1
        status = {
            "apply_locked": False,
            "apply_operation_id": None,
            "transfer_inhibition_operation_id": local["inhibition_operation_id"],
            "transfer_inhibition_quiescent": local["inhibited"],
            "pending_operation_id": None,
            "data_plane_mode": "active" if node_id == "node-a" else "passive",
            "observed_owner_node_id": "node-a",
            "former_owner_compute_state": "running",
            "state": (
                "blocked"
                if node_id == "node-b" and local["inhibited"]
                else "active"
                if node_id == "node-a"
                else "normal"
            ),
            "reasons": (
                ["mtls-rotation-active"]
                if node_id == "node-b" and local["inhibited"]
                else ["authoritative-owner-active"]
                if node_id == "node-a"
                else ["authoritative-owner-peer-is-healthy"]
            ),
            "promotion_ready": node_id == "node-a",
            "mtls": {
                **local,
                "peer": {
                    "node_id": peer_id,
                    "boot_id": f"boot-{peer_id}",
                    "sequence": sequences[node_id],
                    "epoch": peer["epoch"],
                    "certificate_fingerprint": peer["certificate_fingerprint"],
                    "fresh": True,
                },
            },
        }
        assert predicate(status)
        return status

    monkeypatch.setattr(cli, "SSHPush", _SSH)
    monkeypatch.setattr(cli, "_wait_for_vm_ha_agent_status", wait_status)
    monkeypatch.setattr(
        cli,
        "VMHALifecycleStore",
        lambda _path: SimpleNamespace(read=lambda **_kwargs: SimpleNamespace(status="active")),
    )
    monkeypatch.setattr(cli, "_vm_ha_status_runtime_binding", lambda _state: object())
    current = {
        node_id: execute_mtls_action("status", {}, state_dir=root, require_root=False)["result"]
        for node_id, root in roots.items()
    }
    members = (
        cli._VMHAMTLSRotationMember(
            active_instance,
            "active-host",
            "compute-a",
            "node-a",
            "active",
            "a" * 64,
            current["node-a"],
            {},
        ),
        cli._VMHAMTLSRotationMember(
            passive_instance,
            "passive-host",
            "compute-b",
            "node-b",
            "passive",
            "a" * 64,
            current["node-b"],
            {},
        ),
    )
    plan = cli._VMHAMTLSRotationPlan(
        config_path=tmp_path / "gateway.yaml",
        local_config={},
        project_id="project-a",
        gateway_name="gateway-a",
        cluster_id="cluster-a",
        allocation_id="allocation-a",
        owner_node_id="node-a",
        passive_node_id="node-b",
        operation_id="e" * 64,
        target_epoch=2,
        digest="f" * 64,
        plan_payload={},
        members=members,
        ssh_policy=object(),  # type: ignore[arg-type]
    )

    cli._execute_vm_ha_mtls_rotation(plan)

    inhibitions = [entry for entry in trace if entry[1] == "inhibit"]
    assert inhibitions[:2] == [("node-b", "inhibit"), ("node-a", "inhibit")]
    activations = [entry for entry in trace if entry[1] == "activate"]
    assert activations[:2] == [("node-b", "activate"), ("node-a", "activate")]
    assert len([entry for entry in trace if entry[1] == "record-observation"]) == 6
    for node_id, root in roots.items():
        status = execute_mtls_action("status", {}, state_dir=root, require_root=False)["result"]
        assert status["state"] == "healthy"
        assert status["epoch"] == 2
        assert status["inhibited"] is False
        assert status["peer_fingerprints"] == [
            execute_mtls_action(
                "status",
                {},
                state_dir=roots["node-b" if node_id == "node-a" else "node-a"],
                require_root=False,
            )["result"]["certificate_fingerprint"]
        ]


def test_rotation_inhibition_requires_controller_processed_quiescence(tmp_path: Path) -> None:
    plan = _preview_plan(tmp_path)
    passive = next(member for member in plan.members if member.node_id == plan.passive_node_id)
    status = {
        "apply_locked": False,
        "apply_operation_id": None,
        "transfer_inhibition_operation_id": None,
        "transfer_inhibition_quiescent": False,
        "pending_operation_id": None,
        "observed_owner_node_id": plan.owner_node_id,
        "data_plane_mode": "passive",
        "state": "blocked",
        "reasons": ["mtls-rotation-active"],
        "former_owner_compute_state": "running",
        "mtls": {
            "inhibited": True,
            "inhibition_operation_id": plan.operation_id,
        },
    }

    assert not cli._vm_ha_mtls_inhibition_quiescent(status, plan=plan, member=passive)

    status.update(
        transfer_inhibition_operation_id=plan.operation_id,
        transfer_inhibition_quiescent=True,
    )
    assert cli._vm_ha_mtls_inhibition_quiescent(status, plan=plan, member=passive)


def test_rotation_releases_preparation_free_inhibition_after_barrier_drift(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _preview_plan(tmp_path)
    trace: list[tuple[str, str]] = []

    class _SSH:
        def __init__(self, ssh_policy=None) -> None:
            pass

        def run_vm_ha_mtls_action(self, target, instance_name, local_config, *, action, request):
            trace.append((target, action))
            return {
                "schema": "nebius-vpngw/vm-ha-mtls-action-response-v1",
                "action": action,
                "result": {},
            }

    monkeypatch.setattr(cli, "SSHPush", _SSH)
    monkeypatch.setattr(
        cli,
        "VMHALifecycleStore",
        lambda _path: SimpleNamespace(read=lambda **_kwargs: SimpleNamespace(status="active")),
    )
    monkeypatch.setattr(cli, "_vm_ha_status_runtime_binding", lambda _state: object())
    monkeypatch.setattr(
        cli,
        "_wait_for_vm_ha_agent_status",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("topology drifted")),
    )

    with pytest.raises(RuntimeError, match="exact inhibition was released"):
        cli._execute_vm_ha_mtls_rotation(plan)

    assert trace == [
        ("passive-host", "inhibit"),
        ("passive-host", "release-inhibition"),
    ]


def test_vm_ha_rotate_mtls_uses_human_output_and_rotation_progress(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("project_id: project-a\n", encoding="utf-8")
    plan = _preview_plan(tmp_path)
    events: list[tuple[cli._VMHAProgressPhase, cli._VMHAProgressState]] = []

    class _Lock:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    class _Progress:
        def __call__(self, event: cli._VMHAProgressEvent) -> None:
            events.append((event.phase, event.state))

        def close_unfinished(self) -> None:
            pass

    monkeypatch.setattr(cli, "_inspect_vm_ha_mtls_rotation", lambda _path: plan)
    monkeypatch.setattr(cli, "VMHAApplyLock", _Lock)
    monkeypatch.setattr(cli, "_execute_vm_ha_mtls_rotation", lambda _plan: None)
    monkeypatch.setattr(cli, "_vm_ha_progress_sink", lambda _stream: _Progress())

    result = CliRunner().invoke(
        cli.app,
        [
            "vm-ha",
            "--rotate-mtls",
            "--local-config-file",
            str(config_path),
            "--approve",
            plan.digest,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "VPN traffic is expected to remain available" in result.output
    assert "failover and rearm are paused until completion" in result.output
    assert "Plan: rotate 2 members, passive first, target epoch 2." in result.output
    assert "Plan digest:" in result.output
    assert "vm-ha-mtls-rotation-preview-v1" not in result.output
    assert "vm-ha-mtls-rotation-result-v1" not in result.output
    assert events == [
        (cli._VMHAProgressPhase.ROTATE_MTLS, cli._VMHAProgressState.STARTED),
        (cli._VMHAProgressPhase.ROTATE_MTLS, cli._VMHAProgressState.COMPLETED),
    ]
