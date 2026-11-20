import json

import pytest

from nebius_vpngw import repair_guest as guest
from nebius_vpngw import repair_state as state


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setattr(guest, "GENERATED_FILES", ())
    for name, relative in {
        "ROOT": "repair",
        "SYSTEMD": "systemd",
        "PRODUCT_STATE": "state",
        "PRODUCT_CONFIG": "config",
        "BOOT": "boot",
        "FORWARDING": "forwarding",
        "DEPLOY_LOCK": "locks/deploy",
        "ROUTING_LOCK": "locks/routing",
        "POLICY": "sbin/policy-rc.d",
    }.items():
        monkeypatch.setattr(guest, name, tmp_path / relative)
    guest.BOOT.write_text("boot-a")
    guest.FORWARDING.write_text("0")
    journal = state.Journal(guest.ROOT)
    value = journal.begin(
        {
            "project": "fixture-project",
            "gateway": "fixture-gateway",
            "configuration": "a" * 64,
            "artifact": "b" * 64,
            "ha": False,
            "targets": [
                {
                    "name": "fixture-0",
                    "compute": "vm-a",
                    "disk": "disk-a",
                    "transport": "192.0.2.1",
                    "binding": "c" * 64,
                }
            ],
        }
    )
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if args[:2] == ["systemctl", "is-enabled"]:
            return 0, "enabled\n"
        if "--property=ExecCondition" in args:
            name = args[3]
            return (
                0,
                "Id="
                + name
                + "\nLoadState=loaded\nExecCondition={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B /var/lib/nebius-vpngw-repair/startup.py "
                + name
                + " ; ignore_errors=no ; start_time= ; stop_time= ; pid=0 ; code=(null) ; status=0/0 }\n",
            )
        if args[:2] == ["systemctl", "show"]:
            return (
                0,
                "LoadState=loaded\nActiveState=inactive\nMainPID=0\nControlPID=0\nJob=\nControlGroup=\n",
            )
        return 0, ""

    return journal, value["id"], calls, run


def test_same_repair_admission_reports_live_writer_and_preserves_journal(host, monkeypatch, capsys):
    import io
    import sys

    from nebius_vpngw.deploy import repair_remote

    journal, operation, _calls, _run = host
    original = journal.path.read_bytes()
    request = {"action": "admit", "operation": operation, "intent": journal.load()["intent"]}

    def invoke():
        monkeypatch.setattr(
            sys, "stdin", type("Input", (), {"buffer": io.BytesIO(json.dumps(request).encode())})()
        )
        repair_remote.control_main()

    with guest.lock(guest.DEPLOY_LOCK):
        with pytest.raises(SystemExit) as result:
            invoke()
        assert result.value.code == 1
        assert json.loads(capsys.readouterr().out) == {
            "status": "failed",
            "stage": "predecessor",
            "error_code": "repair_writer_busy",
        }
        assert journal.path.read_bytes() == original
    invoke()
    assert json.loads(capsys.readouterr().out) == {"operation": operation, "phase": "confirmed"}
    assert journal.path.read_bytes() == original


def test_guard_archives_broken_owned_overrides_and_masks(host):
    journal, operation, calls, run = host
    guest.SYSTEMD.mkdir()
    mask = guest.SYSTEMD / "frr.service"
    mask.symlink_to("/dev/null")
    overrides = guest.SYSTEMD / "strongswan.service.d"
    overrides.mkdir()
    (overrides / "99-broken.conf").write_text("[Service]\nExecCondition=\n")
    unrelated = guest.SYSTEMD / "ssh.service"
    unrelated.write_text("keep SSH")
    guest.install_guard(operation, run=run)
    guest.verify_guard(operation, run=run)
    assert not mask.is_symlink()
    assert not (overrides / "99-broken.conf").exists()
    assert (guest.ROOT / "archives" / operation / "systemd/frr.service/unit").is_symlink()
    assert unrelated.read_text() == "keep SSH"
    assert ["systemctl", "enable", guest.BOOT_UNIT] in calls
    assert json.loads((guest.ROOT / "startup.json").read_bytes())["units"] == []
    # Repeating installation after a lost response does not overwrite originals.
    guest.install_guard(operation, run=run)
    guest.verify_guard(operation, run=run)


def test_guard_checks_effective_conditions_not_only_files(host):
    _journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)

    def cleared(args, **kwargs):
        if "--property=ExecCondition" in args:
            return 0, "LoadState=loaded\nExecCondition=\n"
        return run(args, **kwargs)

    with pytest.raises(RuntimeError, match="Effective repair service inhibition"):
        guest.verify_guard(operation, run=cleared)


def test_guard_retires_only_exact_product_ssh_ordering_before_archiving_ingress(host):
    _journal, operation, _calls, run = host
    retained = []
    for unit in ("ssh.service", "ssh.socket"):
        directory = guest.SYSTEMD / (unit + ".d")
        directory.mkdir(parents=True)
        (directory / "40-vpngw-ingress.conf").write_bytes(guest.SSH_ORDERING)
        unrelated = directory / "50-customer.conf"
        unrelated.write_bytes(b"[Unit]\nAfter=network.target\n")
        retained.append(unrelated)
    guest.install_guard(operation, run=run)
    for unit in ("ssh.service", "ssh.socket"):
        assert not (guest.SYSTEMD / (unit + ".d") / "40-vpngw-ingress.conf").exists()
        assert (
            guest.ROOT / "archives" / operation / "ssh-ordering" / unit
        ).read_bytes() == guest.SSH_ORDERING
    guest.retire_ssh_ordering(operation)
    assert all(path.read_bytes() == b"[Unit]\nAfter=network.target\n" for path in retained)


@pytest.mark.parametrize(
    "changed", ["content", "symlink", "parent-symlink", "writable", "hardlink"]
)
def test_guard_refuses_modified_ssh_ordering_before_any_service_change(host, changed):
    import os

    _journal, operation, calls, run = host
    directory = guest.SYSTEMD / "ssh.socket.d"
    directory.mkdir(parents=True)
    path = directory / "40-vpngw-ingress.conf"
    path.write_bytes(guest.SSH_ORDERING)
    if changed == "content":
        path.write_bytes(guest.SSH_ORDERING + b"Requires=customer.service\n")
    elif changed == "symlink":
        target = directory / "customer.conf"
        path.rename(target)
        path.symlink_to(target)
    elif changed == "parent-symlink":
        target = directory.with_name("customer-directory")
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
    elif changed == "writable":
        path.chmod(0o666)
    else:
        os.link(path, directory / "customer.conf")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="modified SSH ingress ordering"):
        guest.install_guard(operation, run=run)
    assert path.read_bytes() == before
    assert not (guest.SYSTEMD / guest.BOOT_UNIT).exists()
    assert not calls


def test_guard_verifies_native_alias_using_systemd_canonical_identity(host):
    _journal, operation, _calls, run = host
    guest.install_guard(operation, preserve_services=True, run=run)

    def native_alias(args, **kwargs):
        code, output = run(args, **kwargs)
        if "--property=ExecCondition" in args and "strongswan-swanctl.service" in args:
            output = output.replace("strongswan-swanctl.service", "strongswan.service")
        return code, output

    guest.verify_guard(operation, run=native_alias)


def test_guard_does_not_accept_alias_to_unrelated_service(host):
    _journal, operation, _calls, run = host
    guest.install_guard(operation, preserve_services=True, run=run)

    def foreign_alias(args, **kwargs):
        code, output = run(args, **kwargs)
        if "--property=ExecCondition" in args and "strongswan-swanctl.service" in args:
            output = output.replace("Id=strongswan-swanctl.service", "Id=ssh.service")
        return code, output

    with pytest.raises(RuntimeError, match="Effective repair service inhibition"):
        guest.verify_guard(operation, run=foreign_alias)


@pytest.mark.parametrize(
    "fault",
    [None, "foreign-dependency", "foreign-path", "symlink", "changed-boot", "missing-journal"],
)
def test_ordinary_activation_admits_only_verified_repair_guard(host, monkeypatch, fault):
    import hashlib
    import time

    from nebius_vpngw import ordinary_operations as ops

    journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)
    name = "nebius-vpngw-agent.service"
    path = guest.SYSTEMD / (name + ".d") / "00-vpngw-repair.conf"
    if fault == "foreign-dependency":
        path.write_bytes(guest.service_guard() + b"[Unit]\nRequires=unrelated.service\n")
    elif fault == "foreign-path":
        path = guest.SYSTEMD / "00-vpngw-repair.conf"
        path.write_bytes(guest.service_guard())
    elif fault == "symlink":
        target = guest.ROOT / "copy.conf"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    elif fault == "changed-boot":
        (guest.SYSTEMD / guest.BOOT_UNIT).write_bytes(b"[Service]\nExecStart=/bin/true\n")
    elif fault == "missing-journal":
        journal.path.unlink()
    verify = guest.verify_guard
    monkeypatch.setattr(guest, "verify_guard", lambda value: verify(value, run=run))
    manager = ops.Manager(time.monotonic() + 30)
    monkeypatch.setattr(manager, "preflight", lambda: None)
    monkeypatch.setattr(manager, "network", lambda: {"inputs": {}, "links": []})
    monkeypatch.setattr(
        manager, "unit", lambda unit: {"DropInPaths": [str(path)]} if unit == name else {}
    )
    if fault:
        with pytest.raises(RuntimeError):
            manager.environment()
    else:
        before = journal.path.read_bytes()
        environment = manager.environment()
        assert environment["unit_files"][str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert journal.path.read_bytes() == before
        assert path.read_bytes() == guest.service_guard()


def test_settled_guarded_guest_can_admit_successor_with_readable_owned_archive(host, monkeypatch):
    from nebius_vpngw.deploy import repair_remote

    journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)
    guest.inhibit_package_starts(operation)
    archive = guest.ROOT / "archives" / operation
    archive.chmod(0o755)
    (guest.ROOT / "archives").chmod(0o755)
    settled = journal.settle_pending({"quiet": True})
    state.publish(guest.ROOT / "settled.json", {"predecessor": state.digest(settled)})
    verify_guard, verify_quiescent = guest.verify_guard, guest.verify_quiescent
    monkeypatch.setattr(guest, "verify_guard", lambda op: verify_guard(op, run=run))
    monkeypatch.setattr(guest, "verify_quiescent", lambda: verify_quiescent(run=run))
    next_operation = "f" * 32
    repair_remote.admit({"operation": next_operation, "intent": settled["intent"]})
    assert journal.load()["id"] == next_operation
    assert archive.stat().st_mode & 0o077 == 0
    assert (guest.ROOT / "archives").stat().st_mode & 0o077 == 0
    assert (
        json.loads((archive / "repair-package-policy.json").read_bytes())["operation"] == operation
    )
    assert (
        json.loads((guest.ROOT / "package-policy.json").read_bytes())["operation"] == next_operation
    )


@pytest.mark.parametrize("unsafe", ["writable", "symlink", "outside"])
def test_archive_permission_recovery_stays_inside_owned_safe_storage(host, tmp_path, unsafe):
    _journal, operation, _calls, _run = host
    archive = guest.ROOT / "archives" / operation
    archive.parent.mkdir(mode=0o700)
    if unsafe == "writable":
        archive.mkdir()
        archive.chmod(0o777)
    elif unsafe == "symlink":
        archive.symlink_to(tmp_path, target_is_directory=True)
    else:
        archive = tmp_path / "unrelated"
    with pytest.raises((OSError, RuntimeError, ValueError)):
        guest.archive_directory(archive)
    if unsafe == "writable":
        assert archive.stat().st_mode & 0o777 == 0o777
    if unsafe == "outside":
        assert not archive.exists()


def test_native_timer_readback_does_not_require_service_process_properties():
    def run(_args):
        return 0, "LoadState=not-found\nActiveState=inactive\nJob=\n"

    state = guest.service_state("nebius-vpngw-fix-routes.timer", run=run)
    assert state["ActiveState"] == "inactive" and state["MainPID"] == "0"
    with pytest.raises(RuntimeError, match="observation is incomplete"):
        guest.service_state("nebius-vpngw-fix-routes.service", run=run)


def test_active_timer_still_prevents_quiescence(host):
    _journal, _operation, _calls, original = host
    guest.FORWARDING.write_text("0\n")

    def run(args, **kwargs):
        if "nebius-vpngw-fix-routes.timer" in args:
            return 0, "LoadState=loaded\nActiveState=active\nJob=\n"
        return original(args, **kwargs)

    with pytest.raises(RuntimeError, match="service is still settling"):
        guest.verify_quiescent(run=run)


@pytest.mark.parametrize("settles_at", [0.0, 90.1])
def test_stop_writers_waits_for_normal_systemd_shutdown(host, monkeypatch, settles_at):
    from types import SimpleNamespace

    _journal, _operation, _calls, original = host
    guest.FORWARDING.write_text("0\n")
    elapsed = [0.0]
    stops = []
    monkeypatch.setattr(
        guest,
        "time",
        SimpleNamespace(
            monotonic=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        ),
    )

    def run(args, **kwargs):
        if args[:3] == ["systemctl", "--no-block", "stop"]:
            stops.append(args)
            return 0, ""
        if args[:2] == ["systemctl", "show"] and args[2] == "nebius-vpngw-vm-ha.service":
            busy = not stops or elapsed[0] < settles_at
            return 0, (
                "LoadState=loaded\n"
                f"ActiveState={'deactivating' if busy else 'inactive'}\n"
                f"MainPID={'123' if busy else '0'}\nControlPID=0\n"
                f"Job={'42' if busy else ''}\nControlGroup=\n"
            )
        return original(args, **kwargs)

    guest.stop_writers(run=run)
    assert len(stops) == 1
    assert settles_at <= elapsed[0] < settles_at + 0.21
    guest.verify_quiescent(run=run)


def test_stop_writers_does_not_reset_deadline_or_accept_live_writer(host, monkeypatch):
    from types import SimpleNamespace

    _journal, _operation, _calls, original = host
    guest.FORWARDING.write_text("0\n")
    elapsed = [0.0]
    stops = []
    monkeypatch.setattr(
        guest,
        "time",
        SimpleNamespace(
            monotonic=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        ),
    )

    def run(args, **kwargs):
        if args[:3] == ["systemctl", "--no-block", "stop"]:
            stops.append(args)
            return 0, ""
        if args[:2] == ["systemctl", "show"] and args[2] == "nebius-vpngw-vm-ha.service":
            return 0, (
                "LoadState=loaded\nActiveState=deactivating\nMainPID=123\n"
                "ControlPID=0\nJob=42\nControlGroup=\n"
            )
        return original(args, **kwargs)

    with pytest.raises(RuntimeError, match="service is still settling"):
        guest.stop_writers(run=run, timeout=7)
    assert len(stops) == 1 and 7 <= elapsed[0] < 7.21


@pytest.mark.parametrize("ledger_source", ["live", "exact-archive"])
def test_reset_preserves_lock_inodes_and_opaque_predecessor(host, monkeypatch, ledger_source):
    journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)
    journal.advance("isolated")
    journal.advance("staged")
    guest.PRODUCT_CONFIG.mkdir()
    (guest.PRODUCT_CONFIG / "broken.yaml").write_bytes(b"not: [valid YAML")
    inodes = {}
    for relative in guest.PRESERVED_LOCKS:
        path = guest.PRODUCT_STATE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        inodes[relative] = path.stat().st_ino
    (guest.PRODUCT_STATE / "vm-ha/guard.json").write_bytes(b"malformed old state")
    network_root = guest.ROOT.parent / "network"
    monkeypatch.setattr(guest.underlay_state, "NETWORK_ROOT", network_root)
    dropin = network_root / "10-netplan-eth0.network.d" / guest.underlay_state.DROPIN
    dropin.parent.mkdir(parents=True)
    destinations = ["10.64.16.0/24"]
    content = guest.underlay_state.render(tuple(destinations))
    dropin.write_text(content)
    ownership = {
        "schema": guest.underlay_state.SCHEMA,
        "phase": "committed",
        "network": {
            "interface": "eth0",
            "index": 2,
            "address": "10.64.0.5",
            "gateway": "169.254.169.1",
            "network_file": "/run/systemd/network/10-netplan-eth0.network",
            "config_digest": "a" * 64,
            "routes": [],
        },
        "destinations": destinations,
        "dropin": str(dropin),
        "content": content,
        "intent": "b" * 64,
        "boot": "old-boot",
    }
    ledger = guest.PRODUCT_STATE / "underlay.json"
    ledger.write_text(json.dumps(ownership))
    ledger_before = ledger.read_bytes()
    if ledger_source == "exact-archive":
        archive = guest.ROOT / "archives" / operation / "state/underlay.json"
        state.directory(archive.parent)
        ledger.rename(archive)
    admission = guest.PRODUCT_STATE / "routing-semantics.json"
    admission.write_text("old readiness")
    receipt = guest.reset(operation, run=run)
    assert receipt["operation"] == operation
    assert ledger.read_bytes() == ledger_before
    assert dropin.read_text() == content
    assert not admission.exists()
    assert (
        guest.ROOT / "archives" / operation / "state/routing-semantics.json"
    ).read_text() == "old readiness"
    assert not (guest.PRODUCT_STATE / "vm-ha/guard.json").exists()
    for relative, inode in inodes.items():
        assert (guest.PRODUCT_STATE / relative).stat().st_ino == inode
    assert (
        guest.ROOT / "archives" / operation / "state/vm-ha/guard.json"
    ).read_bytes() == b"malformed old state"
    assert guest.reset(operation, run=run) == receipt


@pytest.mark.parametrize("prior_mode", [None, 0o755, 0o700])
def test_reset_leaves_ordinary_operation_store_ready_for_route_handoff(
    host, monkeypatch, prior_mode
):
    import os
    import time

    from nebius_vpngw import ordinary_operations

    journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)
    journal.advance("isolated")
    journal.advance("staged")
    root = guest.PRODUCT_STATE / "ordinary"
    previous_inode = None
    if prior_mode is not None:
        root.mkdir(parents=True, mode=prior_mode)
        root.chmod(prior_mode)
        (root / "operation.lock").touch(mode=0o600)
        previous_inode = (root / "operation.lock").stat().st_ino
    previous_umask = os.umask(0o022)
    try:
        guest.reset(operation, run=run)
    finally:
        os.umask(previous_umask)
    monkeypatch.setattr(ordinary_operations, "JOURNAL", root / "operation.json")
    monkeypatch.setattr(ordinary_operations, "ROUTING_LOCK", guest.ROUTING_LOCK)
    with ordinary_operations.mutation_lock(deadline=time.monotonic() + 1):
        assert ordinary_operations.Journal().read() is None
    assert root.stat().st_mode & 0o777 == 0o700
    if previous_inode is not None:
        assert (root / "operation.lock").stat().st_ino == previous_inode


@pytest.mark.parametrize("prior_mode", [None, 0o755, 0o700])
def test_ordinary_reset_leaves_private_mtls_storage_ready_for_conversion(host, prior_mode):
    import os
    import stat

    from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore, MTLSOperationKind

    journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)
    journal.advance("isolated")
    journal.advance("staged")
    root = guest.PRODUCT_STATE / "vm-ha/mtls"
    previous_inode = None
    if prior_mode is not None:
        root.mkdir(parents=True, mode=prior_mode)
        root.chmod(prior_mode)
        (root / "writer.lock").touch(mode=0o600)
        previous_inode = (root / "writer.lock").stat().st_ino
    previous_umask = os.umask(0o022)
    try:
        guest.reset(operation, run=run)
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    if previous_inode is not None:
        assert (root / "writer.lock").stat().st_ino == previous_inode
    receipt = ManagedMTLSStore(root).prepare_identity(
        operation_id="c" * 64,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="fixture-cluster",
        node_id="fixture-node",
        compute_id="fixture-compute",
        target_epoch=1,
    )
    assert receipt.node_id == "fixture-node"
    assert receipt.epoch == 1


def test_private_lock_directory_is_unchanged_while_another_writer_owns_it(tmp_path):
    path = tmp_path / "mtls/writer.lock"
    path.parent.mkdir(mode=0o755)
    path.parent.chmod(0o755)
    with guest.lock(path):
        inode = path.stat().st_ino
        with (
            pytest.raises(RuntimeError, match="still settling"),
            guest.lock(path, private_parent=True),
        ):
            pytest.fail("A concurrent writer was admitted")
        assert path.parent.stat().st_mode & 0o777 == 0o755
        assert path.stat().st_ino == inode


@pytest.mark.parametrize("unsafe", ["writable", "symlink", "foreign-owner"])
def test_private_lock_directory_rejects_unsafe_parent_without_changing_it(
    tmp_path, monkeypatch, unsafe
):
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o755)
    path = tmp_path / "mtls/writer.lock"
    if unsafe == "symlink":
        path.parent.symlink_to(actual, target_is_directory=True)
    elif unsafe == "writable":
        path.parent.mkdir(mode=0o777)
        path.parent.chmod(0o777)
    else:
        path.parent.mkdir(mode=0o755)
        foreign_uid = guest.os.geteuid() + 1
        monkeypatch.setattr(guest.os, "geteuid", lambda: foreign_uid)
    before = path.parent.stat().st_mode
    with pytest.raises(RuntimeError, match="unsafe|symlink"), guest.lock(path, private_parent=True):
        pytest.fail("Unsafe private storage was accepted")
    assert path.parent.stat().st_mode == before
    assert not path.exists()


def test_reset_does_not_skip_running_unit_with_removed_definition(host):
    journal, operation, _calls, run = host
    guest.install_guard(operation, run=run)
    journal.advance("isolated")
    journal.advance("staged")

    def active(args, **kwargs):
        if args[:2] == ["systemctl", "show"] and args[2] == "frr.service":
            return (
                0,
                "LoadState=not-found\nActiveState=active\nMainPID=42\nControlPID=0\nJob=\nControlGroup=\n",
            )
        return run(args, **kwargs)

    with pytest.raises(RuntimeError, match="still settling"):
        guest.reset(operation, run=active)
    assert not (guest.ROOT / "reset.json").exists()


def test_package_policy_is_restored_exactly_after_interrupted_install(host):
    _journal, operation, _calls, _run = host
    guest.POLICY.parent.mkdir()
    guest.POLICY.write_bytes(b"#!/bin/sh\nexit 0\n")
    guest.POLICY.chmod(0o750)
    guest.inhibit_package_starts(operation)
    guest.inhibit_package_starts(operation)
    assert guest.POLICY.read_bytes() == guest.POLICY_BYTES
    guest.restore_package_policy(operation)
    assert guest.POLICY.read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert guest.POLICY.stat().st_mode & 0o777 == 0o750
    guest.restore_package_policy(operation)


@pytest.mark.parametrize("original", [None, b"#!/bin/sh\n# operator policy\nexit 0\n"])
def test_activation_policy_handoff_precedes_planning_and_grant_preserves_new_owner(
    host, monkeypatch, original
):
    from types import SimpleNamespace

    from nebius_vpngw import runtime_migration
    from nebius_vpngw.deploy import repair_remote

    journal, operation, _calls, _run = host
    guest.POLICY.parent.mkdir()
    if original is not None:
        guest.POLICY.write_bytes(original)
        guest.POLICY.chmod(0o750)
    guest.inhibit_package_starts(operation)
    for phase in ("isolated", "staged", "reset", "installed"):
        journal.advance(phase)
    checked = []
    monkeypatch.setattr(guest, "verify_guard", checked.append)
    monkeypatch.setattr(guest, "install_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(guest, "fence", lambda: None)
    monkeypatch.setattr(guest, "stop_writers", lambda: None)
    # The fixture is unprivileged; only emulate the runtime reader's root UID.
    monkeypatch.setattr(
        runtime_migration,
        "POLICY",
        SimpleNamespace(
            lstat=lambda: SimpleNamespace(st_uid=0, st_mode=guest.POLICY.lstat().st_mode),
            read_bytes=guest.POLICY.read_bytes,
        ),
    )
    request = {"operation": operation, "action": "prepare-activation"}
    repair_remote.control(request)
    before_plan = runtime_migration.policy_snapshot()
    assert not (guest.ROOT / "startup.json").exists()
    assert guest.POLICY.read_bytes() == original if original else not guest.POLICY.exists()
    repair_remote.control(request)
    repair_remote.control({"operation": operation, "action": "grant", "units": ["frr.service"]})
    assert runtime_migration.policy_snapshot() == before_plan
    # A resumed canonical ordinary transaction may already own its inhibition.
    # Reissuing the service grant must neither reject nor replace that policy.
    guest.POLICY.write_bytes(runtime_migration.POLICY_BYTES)
    guest.POLICY.chmod(0o755)
    repair_remote.control({"operation": operation, "action": "grant", "units": ["frr.service"]})
    assert guest.POLICY.read_bytes() == runtime_migration.POLICY_BYTES
    assert checked == [operation] * 4


def test_repair_recovers_policy_from_interrupted_normal_install(host):
    import base64

    _journal, operation, _calls, _run = host
    guest.POLICY.parent.mkdir()
    guest.POLICY.write_bytes(b"#!/bin/sh\n# nebius-vpngw approved runtime transaction\nexit 101\n")
    guest.POLICY.chmod(0o755)
    original = b"#!/bin/sh\n# operator policy\nexit 0\n"
    state.publish(
        guest.PRODUCT_STATE / "runtime-migration/operation.json",
        {"policy": {"content": base64.b64encode(original).decode(), "mode": 0o750}},
    )
    guest.inhibit_package_starts(operation)
    guest.restore_package_policy(operation)
    assert guest.POLICY.read_bytes() == original
    assert guest.POLICY.stat().st_mode & 0o777 == 0o750


def test_startup_grant_does_not_survive_reboot(host, monkeypatch):
    journal, operation, _calls, _run = host
    for phase in ("isolated", "staged", "reset", "installed"):
        journal.advance(phase)
    state.publish(
        guest.ROOT / "startup.json",
        {"operation": operation, "boot": "boot-a", "units": ["frr.service"]},
    )
    code = (
        guest.GUARD.decode()
        .replace("/var/lib/nebius-vpngw-repair", str(guest.ROOT))
        .replace("/proc/sys/kernel/random/boot_id", str(guest.BOOT))
    )
    monkeypatch.setattr("sys.argv", ["startup.py", "frr.service"])
    with pytest.raises(SystemExit) as allowed:
        exec(compile(code, "<repair-guard>", "exec"), {})
    assert allowed.value.code == 0
    guest.BOOT.write_text("boot-b")
    with pytest.raises(SystemExit) as denied:
        exec(compile(code, "<repair-guard>", "exec"), {})
    assert denied.value.code == 1


def test_native_ownership_probe_retains_stderr():
    import sys

    code, output = guest.command(
        [sys.executable, "-c", "import sys;sys.stderr.write('ownership evidence');sys.exit(1)"],
        required=False,
        capture_stderr=True,
    )
    assert code == 1 and output == "ownership evidence"


@pytest.mark.parametrize("fault", [None, "boot", "operation", "units", "guard", "inhibition"])
def test_route_timer_defers_only_to_current_repair_completion(host, fault):
    journal, operation, calls, run = host
    assert not guest.defer_route_timer(run=run)
    guest.install_guard(operation, run=run)
    guest.inhibit_package_starts(operation)
    guest.restore_package_policy(operation)
    for phase in ("isolated", "staged", "reset", "installed", "enrolled"):
        journal.advance(phase)
    grant = {"operation": operation, "boot": "boot-a", "units": list(guest.UNITS)}
    if fault == "boot":
        grant["boot"] = "previous-boot"
    elif fault == "operation":
        grant["operation"] = "0" * 32
    elif fault == "units":
        grant["units"] = []
    elif fault == "guard":
        (guest.SYSTEMD / "nebius-vpngw-fix-routes.timer.d/00-vpngw-repair.conf").write_bytes(b"")
    elif fault == "inhibition":
        (guest.ROOT / "inhibited").unlink()
    state.publish(guest.ROOT / "startup.json", grant)
    calls.clear()
    if fault:
        with pytest.raises(RuntimeError, match="timer completion authority"):
            guest.defer_route_timer(run=run)
        return
    assert guest.defer_route_timer(run=run)
    assert not any(row[:2] == ["systemctl", "start"] for row in calls)
    assert (guest.ROOT / "inhibited").exists()
    for phase in ("prepared", "originating", "verified", "complete"):
        journal.advance(phase)
    guest.finish(operation, run=run)
    assert calls[-2:] == [
        ["systemctl", "start", "nebius-vpngw-fix-routes.timer"],
        ["systemctl", "is-active", "--quiet", "nebius-vpngw-fix-routes.timer"],
    ]
    assert not guest.defer_route_timer(run=run)
