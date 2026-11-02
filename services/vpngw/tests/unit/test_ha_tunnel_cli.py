"""Exercise the HA CLI adapter with pinned transport replaced at its boundary."""

from __future__ import annotations

import base64
import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from nebius_vpngw import cli
from nebius_vpngw.agent.vm_ha.tunnels import CAPABILITY, SCHEMA
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus


@pytest.fixture
def command_context(monkeypatch, tmp_path):
    authority = {
        "cluster": "cluster",
        "owner": "owner",
        "allocation": "allocation",
        "epoch": "epoch",
        "incarnation": 1,
        "generation": "a" * 64,
    }
    members = [
        SimpleNamespace(
            node_id=node,
            name=node,
            condition="exact",
            record={
                "controller_capabilities": [CAPABILITY],
                "apply_locked": False,
                "observed_owner_node_id": "owner",
                "data_plane_mode": "active",
                "cluster_id": authority["cluster"],
                "allocation_id": authority["allocation"],
                "ownership_epoch": authority["epoch"],
                "generation_id": authority["generation"],
                "route_reconciliation": {"ownership_incarnation": 1},
            },
        )
        for node in ("owner", "standby")
    ]
    inspection = SimpleNamespace(
        project_id="project",
        gateway_name="gateway",
        ssh_context=object(),
        targets={m.name: m.name for m in members},
        snapshot=SimpleNamespace(
            lifecycle_state=SimpleNamespace(status=VMHALifecycleStatus.ACTIVE, transaction=None),
            authority=SimpleNamespace(condition="exact", owner_node_id="owner"),
            members=members,
        ),
        plan=SimpleNamespace(
            iter_instance_configs=lambda: [
                SimpleNamespace(
                    vm_ha_node=SimpleNamespace(node_id="owner"),
                    config_yaml=yaml.safe_dump(
                        {"connections": [{"tunnels": [{"name": "backup"}]}]}
                    ),
                )
            ]
        ),
    )
    context = SimpleNamespace(
        inspection=inspection,
        authority=authority,
        calls=[],
        requests=[],
        installed_capabilities={"owner": [CAPABILITY], "standby": [CAPABILITY]},
        submit_timeout=False,
        poll_timeout=False,
        statuses=[
            {
                "schema": SCHEMA,
                "authority": dict(authority),
                "operation": {"id": "operation", "phase": "succeeded"},
            }
        ],
        path=tmp_path / "config.yaml",
    )
    clock = [0.0]
    monkeypatch.setattr(cli, "_inspect_vm_ha_command_status", lambda *a, **k: inspection)
    monkeypatch.setattr(cli, "_status_ssh_target_command", lambda *a, target, **k: ["ssh", target])
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def run(argv, **kwargs):
        context.calls.append(argv)
        command = argv[-1]
        if command.endswith("--agent-capabilities"):
            result = {"features": context.installed_capabilities[argv[1]]}
        elif "--vm-ha-tunnel-request " in command:
            request = json.loads(base64.b64decode(command.split()[-1]))
            assert argv[1] == "owner"
            assert request == {"authority": authority, "verb": "failover", "target": "backup"}
            context.requests.append(request)
            if context.submit_timeout:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            result = {"operation_id": "operation"}
        else:
            assert command.endswith("--vm-ha-tunnel-status") and argv[1] == "owner"
            if context.poll_timeout:
                clock[0] += kwargs["timeout"]
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            result = context.statuses.pop(0) if len(context.statuses) > 1 else context.statuses[0]
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))

    monkeypatch.setattr(cli.subprocess, "run", run)
    return context


@pytest.mark.parametrize("source", ["reported", "installed"])
def test_tunnel_command_rejects_missing_member_capability_before_submission(
    command_context, source, capsys
):
    context = command_context
    if source == "reported":
        context.inspection.snapshot.members[1].record["controller_capabilities"] = []
    else:
        context.installed_capabilities["standby"] = []
    with pytest.raises(RuntimeError, match="capability"):
        cli._ha_tunnel_command(context.path, "failover", "backup")
    assert context.requests == []
    if source == "installed":
        assert [call[1] for call in context.calls] == ["owner", "standby"]
    assert "Tunnel failover verified" not in capsys.readouterr().out


def test_tunnel_command_rejects_standby_target_before_submission(command_context):
    with pytest.raises(ValueError, match="not on the current owner"):
        cli._ha_tunnel_command(command_context.path, "failover", "standby-tunnel")
    assert command_context.requests == []


@pytest.mark.parametrize(
    ("outcome", "message"),
    [("authority", "authority changed"), ("superseded", "superseded"), ("failed", "target-failed")],
)
def test_tunnel_command_never_reports_unverified_completion(
    command_context, outcome, message, capsys
):
    context = command_context
    status = context.statuses[0]
    if outcome == "authority":
        status["authority"]["incarnation"] += 1
    elif outcome == "superseded":
        status["operation"]["id"] = "different-operation"
    else:
        status["operation"].update(phase="failed", result="target-failed")
    with pytest.raises(RuntimeError, match=message):
        cli._ha_tunnel_command(context.path, "failover", "backup")
    assert len(context.requests) == 1
    assert "Tunnel failover verified" not in capsys.readouterr().out


@pytest.mark.parametrize("boundary", ["submit", "poll"])
def test_tunnel_command_timeout_does_not_cancel_or_resubmit(command_context, boundary, capsys):
    context = command_context
    setattr(context, f"{boundary}_timeout", True)
    with pytest.raises(RuntimeError, match="outstanding"):
        cli._ha_tunnel_command(context.path, "failover", "backup")
    assert len(context.requests) == 1
    assert all("cancel" not in call[-1] for call in context.calls)
    assert "Tunnel failover verified" not in capsys.readouterr().out


def test_tunnel_command_waits_for_mailbox_admission_then_exact_success(command_context, capsys):
    context = command_context
    context.statuses.insert(0, {"pending_request": {"id": "operation", "result": "outstanding"}})
    cli._ha_tunnel_command(context.path, "failover", "backup")
    assert len(context.requests) == 1
    assert "Tunnel failover verified" in capsys.readouterr().out


@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "old-incarnation",
        "foreign-cluster",
        "missing-receipt",
        "malformed-receipt",
        "malformed-authority",
        "boolean-incarnation",
    ],
)
def test_tunnel_status_requires_complete_current_authority(command_context, monkeypatch, case):
    import io

    from rich.console import Console
    from tests.unit.test_ha_tunnel_selection import config

    context = command_context
    owner = context.inspection.snapshot.members[0]
    owner.record.update(node_id="owner", route_reconciliation={"ownership_incarnation": 2})
    authority = {**context.authority, "incarnation": 2}
    if case == "old-incarnation":
        authority["incarnation"] = 1
    elif case == "foreign-cluster":
        authority["cluster"] = "other-cluster"
    elif case == "missing-receipt":
        owner.record.pop("route_reconciliation")
    elif case == "malformed-receipt":
        owner.record["route_reconciliation"] = {"ownership_incarnation": "2"}
    elif case == "malformed-authority":
        authority = []
    elif case == "boolean-incarnation":
        authority["incarnation"] = True
    state = {
        "schema": SCHEMA,
        "authority": authority,
        "selected": {"site": "backup"},
        "health": {"site": "healthy"},
        "operation": {"connection": "site", "result": "verified"},
    }
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(state)),
    )
    plan = SimpleNamespace(
        iter_instance_configs=lambda: [
            SimpleNamespace(
                hostname="owner",
                config_yaml=yaml.safe_dump(config("bgp")),
            )
        ]
    )
    output = io.StringIO()
    cli._render_ha_tunnel_selection(
        Console(file=output, color_system=None, width=200),
        context.inspection.snapshot,
        plan,
        context.inspection.ssh_context,
        context.inspection.targets,
    )
    rendered = output.getvalue()
    if case == "exact":
        assert all(word in rendered for word in ("backup", "healthy", "verified"))
        assert "unavailable" not in rendered
    else:
        assert "HA tunnel selection is unavailable" in rendered
        assert "verified" not in rendered and "healthy" not in rendered
