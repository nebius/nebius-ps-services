from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import nebius_vpngw.cli as cli
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
                "local_certificate_fingerprint": receipts[node_id][
                    "certificate_fingerprint"
                ],
                "peer_certificate_fingerprint": receipts[peer_id][
                    "certificate_fingerprint"
                ],
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


def test_set_vm_ha_mtls_dry_run_has_no_writer_or_remote_effect(
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
        ["set-vm-ha-mtls", "--local-config-file", str(config_path), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert plan.digest in result.output
    assert effects == []


def test_set_vm_ha_mtls_rejects_plan_drift_after_lock(tmp_path: Path, monkeypatch) -> None:
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
            "set-vm-ha-mtls",
            "--local-config-file",
            str(config_path),
            "--approve",
            first.digest,
        ],
    )

    assert result.exit_code == 1
    assert "plan drifted" in result.output
    assert effects == []


def test_rotation_switches_passive_first_records_three_rounds_and_releases_inhibition(
    tmp_path: Path, monkeypatch
) -> None:
    roots = {"node-a": tmp_path / "a", "node-b": tmp_path / "b"}
    _bootstrap(roots)
    active_instance, active_config = _member(
        "node-a", "active", "active-host", roots["node-a"]
    )
    passive_instance, passive_config = _member(
        "node-b", "passive", "passive-host", roots["node-b"]
    )
    config_paths = {"node-a": active_config, "node-b": passive_config}
    targets = {"active-host": "node-a", "passive-host": "node-b"}
    trace: list[tuple[str, str]] = []
    sequences = {"node-a": 0, "node-b": 0}

    class _SSH:
        def __init__(self, ssh_policy=None) -> None:
            pass

        def run_vm_ha_mtls_action(
            self, target, instance_name, local_config, *, action, request
        ):
            node_id = targets[target]
            trace.append((node_id, action))
            return execute_mtls_action(
                action,
                request,
                state_dir=roots[node_id],
                config_path=config_paths[node_id],
                require_root=False,
            )

    def wait_status(*, target, predicate, **_kwargs):
        node_id = targets[target]
        peer_id = "node-b" if node_id == "node-a" else "node-a"
        local = execute_mtls_action(
            "status", {}, state_dir=roots[node_id], require_root=False
        )["result"]
        peer = execute_mtls_action(
            "status", {}, state_dir=roots[peer_id], require_root=False
        )["result"]
        sequences[node_id] += 1
        status = {
            "apply_locked": True,
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
        node_id: execute_mtls_action(
            "status", {}, state_dir=root, require_root=False
        )["result"]
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
