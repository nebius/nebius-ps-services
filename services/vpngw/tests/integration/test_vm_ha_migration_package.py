"""Composition regression: real inspection, planning, journal and holder protocol."""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw import ha_repair
from nebius_vpngw import ordinary_operations as ops
from nebius_vpngw.deploy import handoff_remote as guest
from nebius_vpngw.deploy import ordinary_apply, ordinary_remote, vm_ha_package

pytestmark = pytest.mark.integration


@pytest.fixture
def migration_guest(monkeypatch, tmp_path, ordinary_operation_guest):
    root = tmp_path / "guest"
    root.mkdir()
    config = root / "resolved.yaml"
    config.write_text("gateway: {}\n")
    marker = root / "vm-ha-enabled"
    monkeypatch.setattr(ordinary_remote, "CONFIG", config)
    monkeypatch.setattr(ordinary_remote, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary_remote, "LOCK", root / "deploy.lock")
    monkeypatch.setattr(ordinary_remote, "ROUTING_LOCK", ops.ROUTING_LOCK)
    monkeypatch.setattr(guest, "MARKER", marker)
    monkeypatch.setattr(guest, "STAGING", root / "packages")
    monkeypatch.setattr(guest, "STAGED_CONFIG", root / "staged")
    monkeypatch.setattr(guest, "STARTUP_HELPER", root / "startup.py")
    monkeypatch.setattr(guest, "SYSTEMD_ROOT", root / "systemd")
    monkeypatch.setattr(ha_repair, "persist_cold_guard", lambda source: None)
    monkeypatch.setattr(ordinary_remote, "DEADLINE", time.monotonic() + 600)

    def install_startup(**kwargs):
        source = Path(ops.__file__).read_bytes()
        guest.STARTUP_HELPER.write_bytes(
            b"# nebius-vpngw ordinary startup admission\n"
            + source
            + b"\nif __name__ == '__main__':\n    raise SystemExit(0 if startup_allowed() else 1)\n"
        )
        for name in ops.HA_WRITERS:
            path = guest.SYSTEMD_ROOT / (name + ".d") / "ordinary-admission.conf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                b"[Service]\nExecCondition=/usr/bin/python3 -B /var/lib/nebius-vpngw/ordinary-startup.py\n"
            )

    monkeypatch.setattr(ops, "install_startup_guard", install_startup)
    monkeypatch.setattr(guest.signal, "signal", lambda *args: None)
    monkeypatch.setattr(guest.signal, "alarm", lambda *args: None)
    monkeypatch.setattr(guest.select, "select", lambda *args: ([True], [], []))

    # Only guest OS/package boundaries are simulated. Inspectors, the plan,
    # digest comparison, journal schema, holder and action dispatch remain real.
    class RootPath(type(root)):
        def stat(self, **kwargs):
            value = list(super().stat(**kwargs))
            value[4:6] = [0, 0]
            return os.stat_result(value)

    monkeypatch.setattr(ordinary_remote, "Path", RootPath)

    def publish(path, raw, mode=0o600):
        path = Path(path)
        assert path.is_relative_to(root), "guest write escaped fixture"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)

    monkeypatch.setattr(ha_repair, "publish", publish)
    installed = root / "installed"
    installed.mkdir()
    package_file = installed / "nebius_vpngw/module.py"
    package_file.parent.mkdir()
    package_file.write_bytes(b"old agent without new capabilities\n")
    version = {"value": "0.6.0"}
    distribution = SimpleNamespace(
        metadata={"Name": "nebius-vpngw"},
        requires=[],
        locate_file=lambda name: installed / name,
    )

    def dist(name):
        distribution.version = version["value"]
        return distribution

    monkeypatch.setattr(ordinary_remote.metadata, "distribution", dist)
    monkeypatch.setattr(ordinary_remote.metadata, "distributions", lambda: [dist("nebius-vpngw")])
    wheel = root / "nebius_vpngw-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("nebius_vpngw/module.py", b"approved new package\n")
        archive.write(Path(ops.__file__), "nebius_vpngw/ordinary_operations.py")
        archive.writestr("nebius_vpngw/systemd/ordinary.service", b"[Unit]\n[Service]\n")
        archive.writestr("nebius_vpngw/systemd/ha.service", b"[Unit]\nWants=dataplane.service\n")
    unit = root / "agent.service"
    unit.write_bytes(b"previous release service\n")
    old_asset = {
        "member": "nebius_vpngw/systemd/ordinary.service",
        "sha256": ordinary_remote.sha(b"[Unit]\n[Service]\n"),
        "mode": 0o644,
    }
    asset = {
        "member": "nebius_vpngw/systemd/ha.service",
        "sha256": ordinary_remote.sha(b"[Unit]\nWants=dataplane.service\n"),
        "mode": 0o644,
    }
    manifest = dict(
        wheel_name=wheel.name,
        wheel_sha256=ordinary_remote.sha(wheel.read_bytes()),
        version="0.6.1",
        config_sha256=ordinary_remote.sha(config.read_bytes()),
        files={"nebius_vpngw/module.py": ordinary_remote.sha(b"approved new package\n")},
        assets={str(unit): asset},
        ordinary_assets={str(unit): old_asset},
        requirements=[],
        dependency_wheels={},
        package_identity="1" * 64,
    )
    monkeypatch.setattr(vm_ha_package, "artifact", lambda *args: copy.deepcopy(manifest))
    monkeypatch.setattr(
        ordinary_apply,
        "remote",
        lambda *args: {
            "observation": ordinary_remote.inspect(args[-1]["manifest"], runtime_admission=False)
        },
    )
    state = {"agent": "active", "installs": 0}

    def command(args, **kwargs):
        if args[0] == "systemctl":
            return 0, state["agent"]
        if "pip" in args:
            assert ops.Journal().read()["phase"] == "install"
            assert state["agent"] == "inactive"
            if not marker.exists():
                assert unit.read_bytes() == b"[Unit]\n[Service]\n"
            state["installs"] += 1
            with zipfile.ZipFile(args[-1]) as archive:
                package_file.write_bytes(archive.read("nebius_vpngw/module.py"))
            version["value"] = "0.6.1"
        if "--agent-capabilities" in args:
            return 0, json.dumps({"schema": "nebius-vpngw.agent-capabilities.v1", "features": []})
        return 0, ""

    monkeypatch.setattr(ordinary_remote, "command", command)
    units = {name: "inactive" for name in ops.HA_WRITERS}

    def service(action, name):
        units[name] = "inactive" if action == "stop" else "active"
        if name in ops.MANAGEMENT:
            state["agent"] = units[name]

    ordinary_operation_guest.on_service = staticmethod(service)
    monkeypatch.setattr(
        ordinary_operation_guest,
        "stopped",
        lambda self, name: units.get(name, state["agent"]) == "inactive",
    )
    ha = dict(cluster_id="cluster", node_id="node-a", generation_id="a" * 64)
    lock = dict(
        ha, operation_id="b" * 64, apply_locked=True, schema="nebius-vpngw/vm-ha-apply-lock-v2"
    )
    ha_state = root / "ha-state"
    ha_state.mkdir()
    publish(ha_state / "apply.lock", json.dumps(lock).encode())
    monkeypatch.setattr(ha_repair, "STATE", ha_state)
    monkeypatch.setattr(ha_repair, "BOOT", ops.BOOT)
    forwarding = root / "forwarding"
    forwarding.write_text("0")
    monkeypatch.setattr(ha_repair, "FORWARDING", forwarding)
    original_exact, original_evidence = ha_repair.exact_lock, ha_repair.evidence
    original_initialize, original_guard = (
        ha_repair.initialize_unstarted_checkpoint,
        ha_repair.cold_guard,
    )
    monkeypatch.setattr(
        ha_repair, "exact_lock", lambda expected, *args: original_exact(expected, ha_state)
    )
    monkeypatch.setattr(
        ha_repair,
        "evidence",
        lambda expected, **kwargs: original_evidence(expected, state_dir=ha_state, **kwargs),
    )
    monkeypatch.setattr(
        ha_repair,
        "initialize_unstarted_checkpoint",
        lambda **kwargs: original_initialize(state_dir=ha_state, **kwargs),
    )
    monkeypatch.setattr(ha_repair, "writer_locks", lambda **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(
        ha_repair,
        "cold_guard",
        lambda **kwargs: original_guard(
            **kwargs, runner=lambda *args, **opts: SimpleNamespace(returncode=0)
        ),
    )
    active = dict(
        schema="nebius-vpngw/vm-ha-mtls-active-v1",
        cluster_id="cluster",
        node_id="node-a",
        compute_id="compute-a",
        epoch=1,
        certificate_fingerprint="c" * 64,
        spki_fingerprint="d" * 64,
        peers=[],
        operation_id=None,
    )
    publish(ha_state / "mtls/active.json", json.dumps(active).encode())
    credential = root / "credentials.json"
    publish(credential, b"{}")
    ssh = SimpleNamespace(_build_wheel=lambda **kwargs: wheel)
    instance = SimpleNamespace(hostname="gateway-a", config_yaml="vm_ha: {}\n")
    plan = vm_ha_package.inspect_plan(
        ssh, "203.0.113.10", instance, {}, target_identity="compute-a"
    )
    guest.STAGED_CONFIG.mkdir()
    publish(guest.STAGED_CONFIG / (ha["generation_id"] + ".yaml"), config.read_bytes())
    yield SimpleNamespace(
        root=root,
        config=config,
        marker=marker,
        ha=ha,
        state=state,
        plan=plan,
        unit=unit,
        package_file=package_file,
        ssh=ssh,
        instance=instance,
        ha_state=ha_state,
        credential=credential,
        units=units,
    )
    plan.close()


def run_protocol(monkeypatch, requests):
    output = io.StringIO()
    monkeypatch.setattr(
        guest.sys, "stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    )
    monkeypatch.setattr(guest.sys, "stdout", output)
    guest.main()
    return [json.loads(line) for line in output.getvalue().splitlines()]


def requests(fixture):
    plan = fixture.plan
    staged = {"wheel": base64.b64encode(plan.wheel.read_bytes()).decode(), "dependency_wheels": {}}
    return [
        dict(
            action="reserve",
            observed=guest.inspect(fixture.ha),
            ha=fixture.ha,
            binding="2" * 64,
            artifact=plan.manifest["wheel_sha256"],
            approval="3" * 64,
            package=plan.envelope,
            staged_package=staged,
        ),
        dict(action="prepare-package", plan_digest=plan.digest, **staged),
        dict(
            action="publish-activation",
            operation_id="b" * 64,
            receipt=dict(
                node_id=fixture.ha["node_id"],
                generation_id=fixture.ha["generation_id"],
                staged_file_sha256=ordinary_remote.sha(fixture.config.read_bytes()),
                nebius_credentials_path=str(fixture.credential),
                nebius_credentials_sha256=ordinary_remote.sha(fixture.credential.read_bytes()),
            ),
        ),
        dict(action="complete", operation="b" * 64),
    ]


def test_real_plan_reservation_and_package_accept_expected_handoff(monkeypatch, migration_guest):
    result = run_protocol(monkeypatch, requests(migration_guest))
    assert result[0] == {"ready": True}
    assert result[1]["artifact_sha256"] == migration_guest.plan.manifest["wheel_sha256"]
    assert result[2] == {"published": True}
    assert result[3] == {"complete": True}
    assert migration_guest.state["installs"] == 1
    assert ops.Journal().read()["state"] == "complete"


def test_real_handoff_rejects_unrelated_post_approval_drift(monkeypatch, migration_guest):
    messages = requests(migration_guest)
    migration_guest.config.write_text("gateway: {changed: true}\n")
    with pytest.raises(RuntimeError, match="predecessor changed"):
        run_protocol(monkeypatch, messages)
    assert migration_guest.state["installs"] == 0
    assert ops.Journal().read() is None


def test_retry_uses_handoff_admission_after_partial_assets(monkeypatch, migration_guest):
    messages = requests(migration_guest)
    with pytest.raises(RuntimeError, match="disconnect"):
        run_protocol(monkeypatch, messages[:1])
    old = ops.Journal().read()
    assert old["state"] == "unresolved"
    monkeypatch.setattr(ops, "alive", lambda owner: False)
    observed = guest.inspect(migration_guest.ha)
    assert observed["mode"] == "ordinary"
    assert observed["journal"] == ops.digest(old)
    assert not ops.recoverable(old, ops.Manager(time.monotonic() + 60))
    assert ops.handoff_recoverable(old, ops.Manager(time.monotonic() + 60))


def test_retry_completes_with_fresh_package_plan(monkeypatch, migration_guest):
    fixture = migration_guest
    with pytest.raises(RuntimeError, match="disconnect"):
        run_protocol(monkeypatch, requests(fixture)[:1])
    previous = ops.identity
    monkeypatch.setattr(ops, "identity", lambda pid=None: dict(previous(pid), start="2"))
    monkeypatch.setattr(ops, "alive", lambda owner: owner["start"] == "2")
    fixture.plan.close()
    fixture.plan = vm_ha_package.inspect_plan(
        fixture.ssh, "203.0.113.10", fixture.instance, {}, target_identity="compute-a"
    )
    assert run_protocol(monkeypatch, requests(fixture))[-1] == {"complete": True}


def test_first_marker_interruption_proves_controller_never_started(monkeypatch, migration_guest):
    fixture = migration_guest
    original = ha_repair.publish

    def interrupt(path, raw, mode=0o600):
        original(path, raw, mode)
        if path == fixture.marker:
            raise OSError("crash after marker publication")

    monkeypatch.setattr(ha_repair, "publish", interrupt)
    with pytest.raises(OSError, match="crash after marker"):
        run_protocol(monkeypatch, requests(fixture))
    old = ops.Journal().read()
    assert old["phase"] == "publish" and old["state"] == "unresolved"
    assert guest.never_started(old, fixture.ha)
    monkeypatch.setattr(ops, "alive", lambda owner: False)
    assert guest.inspect(fixture.ha)["unstarted"] is True
    assert guest.inspect(fixture.ha)["mode"] == "ha"
    assert guest.never_started(dict(old, phase="activate"), fixture.ha) is False
    guest.STARTUP_HELPER.write_bytes(b"unknown guard")
    assert guest.never_started(old, fixture.ha) is False


def test_completed_handoff_does_not_reopen_ordinary_admission(monkeypatch, migration_guest):
    fixture = migration_guest
    run_protocol(monkeypatch, requests(fixture))
    observed = guest.inspect(fixture.ha)
    assert observed["mode"] == "ha"
    assert observed["unstarted"] is False
    assert observed["environment"] is None


def fresh_owner(monkeypatch, start="2"):
    previous = ops.identity
    monkeypatch.setattr(ops, "identity", lambda pid=None: dict(previous(pid), start=start))
    monkeypatch.setattr(ops, "alive", lambda owner: owner["start"] == start)


@pytest.mark.parametrize("damage", ["product", "dependency"])
def test_staged_bytes_are_rechecked_before_any_installer(monkeypatch, migration_guest, damage):
    fixture = migration_guest
    messages = requests(fixture)
    if damage == "dependency":
        name = "dep-1-py3-none-any.whl"
        fixture.plan.manifest["dependency_wheels"][name] = ordinary_remote.sha(
            b"approved dependency"
        )
        messages[0]["package"] = fixture.plan.envelope
        messages[0]["staged_package"]["dependency_wheels"][name] = base64.b64encode(
            b"approved dependency"
        ).decode()
        messages[1]["plan_digest"] = fixture.plan.digest
    original = guest.Holder.prepare

    def tamper(holder, payload):
        name = (
            fixture.plan.manifest["wheel_name"]
            if damage == "product"
            else next(iter(fixture.plan.manifest["dependency_wheels"]))
        )
        (guest.STAGING / fixture.plan.digest / name).write_bytes(b"unapproved installer bytes")
        return original(holder, payload)

    monkeypatch.setattr(guest.Holder, "prepare", tamper)
    with pytest.raises(RuntimeError, match="approved file changed"):
        run_protocol(monkeypatch, messages)
    assert fixture.state["installs"] == 0
    assert not fixture.marker.exists()


@pytest.mark.parametrize(
    "damage", ["none", "broken-product", "peer-drift", "post-start-disconnect"]
)
def test_post_publication_retry_runs_real_ha_repair(monkeypatch, migration_guest, damage):
    fixture = migration_guest
    # Disconnect after activate is durable but before the first controller start.
    with pytest.raises(RuntimeError, match="disconnect"):
        run_protocol(monkeypatch, requests(fixture)[:3])
    assert ops.Journal().read()["phase"] == "activate"
    assert (fixture.ha_state / "controller-checkpoint.json").exists()
    fresh_owner(monkeypatch)
    if damage == "broken-product":
        fixture.package_file.write_bytes(b"raise ImportError('damaged installation')")
    fixture.plan.close()
    fixture.plan = vm_ha_package.inspect_plan(
        fixture.ssh, "203.0.113.10", fixture.instance, {}, target_identity="compute-a"
    )
    if damage == "none":
        assert not fixture.plan.effects
    expected = dict(fixture.ha, compute_id="compute-a", operation_id="b" * 64)
    local = ha_repair.evidence(expected)
    peer = copy.deepcopy(local)
    peer["lock"]["node_id"] = "node-b"
    peer["mtls"].update(node_id="node-b", compute_id="compute-b")
    messages = requests(fixture)
    assert messages[0]["observed"]["pending"] is True
    messages[0].update(repair=expected, peer=peer)
    messages[1]["peer"] = copy.deepcopy(peer)
    messages[2] = dict(action="resume-repair", peer=peer)
    previous_command = ordinary_remote.command

    def command(args, **kwargs):
        if "--vm-ha-status" in args:
            return 0, json.dumps(
                dict(
                    fixture.ha,
                    data_plane_mode="passive",
                    apply_locked=True,
                    apply_operation_id="b" * 64,
                    pending_operation_id=None,
                    guard_boot_id=ops.BOOT.read_text().strip(),
                )
            )
        return previous_command(args, **kwargs)

    monkeypatch.setattr(ordinary_remote, "command", command)
    if damage == "peer-drift":
        messages[1]["peer"]["lock"]["generation_id"] = "f" * 64
        before = fixture.state["installs"]
        with pytest.raises(RuntimeError, match="peer repair admission changed"):
            run_protocol(monkeypatch, messages)
        assert fixture.state["installs"] == before
    else:
        if damage == "post-start-disconnect":
            with pytest.raises(RuntimeError, match="disconnect"):
                run_protocol(monkeypatch, messages[:3])
            assert any(e["kind"] == "reload" for e in ops.Journal().read()["effects"])
            assert all(fixture.units[name] == "active" for name in ops.HA_WRITERS)
            fresh_owner(monkeypatch, start="3")
            fixture.plan.close()
            fixture.plan = vm_ha_package.inspect_plan(
                fixture.ssh, "203.0.113.10", fixture.instance, {}, target_identity="compute-a"
            )
            messages = requests(fixture)
            messages[0].update(repair=expected, peer=peer)
            messages[1]["peer"] = peer
            messages[2] = dict(action="resume-repair", peer=peer)
        assert run_protocol(monkeypatch, messages)[-1] == {"complete": True}
        assert fixture.package_file.read_bytes() == b"approved new package\n"
        assert all(fixture.units[name] == "active" for name in ops.HA_WRITERS)
        assert ops.Journal().read()["state"] == "complete"
        assert ha_repair.evidence(expected)["forwarding"] is False


def test_credential_drift_rejects_before_marker(monkeypatch, migration_guest):
    messages = requests(migration_guest)
    migration_guest.credential.write_bytes(b"changed credential")
    with pytest.raises(RuntimeError, match="approved file changed"):
        run_protocol(monkeypatch, messages)
    assert not migration_guest.marker.exists()


def test_completed_handoff_classifies_healthy_ha_after_lock_release(monkeypatch, migration_guest):
    fixture = migration_guest
    run_protocol(monkeypatch, requests(fixture))
    (fixture.ha_state / "apply.lock").unlink()
    observed = guest.inspect(fixture.ha)
    assert observed["mode"] == "ha"
    assert observed["pending"] is False
    assert observed["lock"] is None


def test_retry_does_not_reinterpret_settled_stops_after_canonical_start(
    monkeypatch, migration_guest
):
    fixture = migration_guest
    with pytest.raises(RuntimeError, match="disconnect"):
        run_protocol(monkeypatch, requests(fixture)[:3])
    fixture.state["agent"] = "active"
    fixture.units.update({name: "active" for name in (*ops.MANAGEMENT, *ops.HA_WRITERS)})
    monkeypatch.setattr(ops.Manager, "unit_finished", lambda self, effect: False)
    monkeypatch.setattr(ops, "alive", lambda owner: False)
    observed = guest.inspect(fixture.ha)
    assert observed["pending"] is True and observed["mode"] == "ha"
