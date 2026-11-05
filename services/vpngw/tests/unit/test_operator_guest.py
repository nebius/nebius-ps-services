import os
from pathlib import Path

import pytest

from nebius_vpngw.deploy import operator_guest as guest
from nebius_vpngw.deploy.operator_keys import generate_or_resume


@pytest.fixture
def target(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(guest, "ROOT", root)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / ".ssh").mkdir(mode=0o700)
    path = home / ".ssh" / "authorized_keys"
    path.write_text(
        '# original customer entry\nfrom="192.0.2.0/24",no-agent-forwarding ssh-rsa customer-key retained\n'
    )
    path.chmod(0o600)
    account = {
        "username": "operator",
        "home": str(home),
        "uid": os.getuid(),
        "gid": os.getgid(),
        "paths": [str(path), str(path) + "2"],
    }
    key = generate_or_resume(tmp_path / "keys" / "id_ed25519", "test passphrase")
    return account, path, key


def test_enrollment_preserves_customer_options_and_is_idempotent(target):
    account, path, key = target
    before = path.read_bytes()
    snapshot = guest.inspect_projection(account, "compute-a")
    result = guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    assert path.read_bytes().startswith(before)
    assert result["projection"]["operators"]["alice"]["fingerprint"] == key.fingerprint
    again = guest.enroll(account, "compute-a", result["digest"], "alice", key.public_key)
    assert again == result
    assert path.read_text().count(key.public_key) == 1
    assert not (guest.ROOT / "enrollment.json").exists()


def test_enrollment_rejects_stale_projection_and_manual_managed_key_removal(target):
    account, path, key = target
    snapshot = guest.inspect_projection(account, "compute-a")
    path.write_text(path.read_text() + "# concurrent customer edit\n")
    with pytest.raises(ValueError, match="changed after preflight"):
        guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    snapshot = guest.inspect_projection(account, "compute-a")
    guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    path.write_text(path.read_text().replace(key.public_key + " vpngw-operator:alice\n", ""))
    with pytest.raises(ValueError, match="roster differ"):
        guest.inspect_projection(account, "compute-a")


@pytest.mark.parametrize("crash_path", ["authorized_keys", "operators.json"])
def test_crash_after_each_publication_resumes_same_intent(target, monkeypatch, crash_path):
    account, path, key = target
    snapshot = guest.inspect_projection(account, "compute-a")
    publish = guest.publish

    def interrupted(destination, expected, desired):
        publish(destination, expected, desired)
        if destination.name == crash_path:
            raise OSError("crash after durable publication")

    monkeypatch.setattr(guest, "publish", interrupted)
    with pytest.raises(OSError, match="crash"):
        guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    pending = guest.inspect_pending(account, "compute-a")
    assert pending["pending"] == {"name": "alice", "fingerprint": key.fingerprint}
    monkeypatch.setattr(guest, "publish", publish)
    result = guest.enroll(account, "compute-a", pending["digest"], "alice", key.public_key)
    assert "pending" not in result
    assert path.read_text().count(key.public_key) == 1
    assert not (guest.ROOT / "enrollment.json").exists()


def test_publication_preserves_concurrent_customer_file(target, monkeypatch):
    account, path, key = target
    snapshot = guest.inspect_projection(account, "compute-a")
    exchange = guest._exchange
    edited = False

    def concurrent(source, destination):
        nonlocal edited
        if Path(destination) == path and not edited:
            edited = True
            path.write_text("concurrent customer content\n")
            path.chmod(0o600)
        return exchange(source, destination)

    monkeypatch.setattr(guest, "_exchange", concurrent)
    with pytest.raises(ValueError, match="atomic publication"):
        guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    assert path.read_text() == "concurrent customer content\n"
    assert (path.parent / ("." + path.name + ".vpngw-recovery") / "candidate").exists()


@pytest.mark.parametrize("after_exchange", [False, True])
def test_process_death_during_atomic_exchange_keeps_login_file_and_resumes(
    target, monkeypatch, after_exchange
):
    account, path, key = target
    before = path.read_bytes()
    snapshot = guest.inspect_projection(account, "compute-a")
    exchange = guest._exchange

    class ProcessDied(BaseException):
        pass

    def interrupted(source, destination):
        if Path(destination) == path:
            if after_exchange:
                exchange(source, destination)
            assert path.exists()
            raise ProcessDied()
        exchange(source, destination)

    monkeypatch.setattr(guest, "_exchange", interrupted)
    with pytest.raises(ProcessDied):
        guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    assert path.read_bytes().startswith(before)
    pending = guest.inspect_pending(account, "compute-a")
    monkeypatch.setattr(guest, "_exchange", exchange)
    result = guest.enroll(account, "compute-a", pending["digest"], "alice", key.public_key)
    assert result["projection"]["operators"]["alice"]["fingerprint"] == key.fingerprint
    assert not (path.parent / ("." + path.name + ".vpngw-recovery")).exists()


@pytest.mark.parametrize("separator", [" ", "  ", "\t", " \t "])
def test_existing_customer_key_restrictions_cannot_be_bypassed(target, separator):
    account, path, key = target
    customer_key = separator.join(key.public_key.split())
    path.write_text('from="192.0.2.0/24",no-port-forwarding ' + customer_key + "\n")
    before = path.read_bytes()
    snapshot = guest.inspect_projection(account, "compute-a")
    with pytest.raises(ValueError, match="bypass its restrictions"):
        guest.enroll(account, "compute-a", snapshot["digest"], "alice", key.public_key)
    assert path.read_bytes() == before
    assert not (guest.ROOT / "enrollment.json").exists()


def test_serving_daemon_configuration_flags_are_preserved(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    child = proc / "123"
    parent = proc / "122"
    child.mkdir(parents=True)
    parent.mkdir()
    (child / "cmdline").write_bytes(b"sudo\0python3\0")
    (child / "status").write_text("PPid:\t122\n")
    (parent / "cmdline").write_bytes(
        b"/usr/sbin/sshd\0-D\0-f\0/etc/ssh/custom.conf\0-o\0AuthorizedKeysFile .ssh/custom_keys\0"
    )
    monkeypatch.setattr(guest.os, "getppid", lambda: 123)
    assert guest._sshd_configuration_arguments(proc) == [
        "-f",
        "/etc/ssh/custom.conf",
        "-o",
        "AuthorizedKeysFile .ssh/custom_keys",
    ]


def test_dns_dependent_sshd_policy_is_rejected_before_file_selection(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        guest.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(
            pw_dir="/home/ubuntu", pw_uid=1000, pw_gid=1000, pw_shell="/bin/bash"
        ),
    )
    monkeypatch.setattr(guest, "_sshd_configuration_arguments", lambda: [])
    monkeypatch.setattr(
        guest.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="pubkeyauthentication yes\nauthorizedkeyscommand none\nusedns yes\nauthorizedkeysfile .ssh/authorized_keys\n",
        ),
    )
    with pytest.raises(ValueError, match="DNS-based"):
        guest.resolve_account("ubuntu", "192.0.2.1 1234 192.0.2.2 22")


def test_replacement_reservation_binds_projection_and_blocks_a_different_writer(target):
    account, path, key = target
    snapshot = guest.inspect_projection(account, "compute-a")
    request = {
        "action": "reserve-replacement",
        "operation": "a" * 32,
        "expected_digest": snapshot["digest"],
    }
    first = guest.replacement_access(account, "compute-a", request)
    assert first["reservation"] == "a" * 32
    assert guest.replacement_access(account, "compute-a", request) == first
    with pytest.raises(ValueError, match="different replacement"):
        guest.replacement_access(account, "compute-a", dict(request, operation="b" * 32))
    path.write_text(path.read_text() + "# customer changed authorization\n")
    with pytest.raises(ValueError, match="changed before replacement"):
        guest.replacement_access(account, "compute-a", request)
    guest.replacement_access(
        account, "compute-a", {"action": "release-replacement", "operation": "a" * 32}
    )
    assert not (guest.ROOT / "replacement.json").exists()


def test_writable_authorization_ancestor_is_rejected_before_enrollment(target):
    account, path, key = target
    path.parent.chmod(0o770)
    with pytest.raises(ValueError, match="directory"):
        guest.inspect_projection(account, "compute-a")


def test_replacement_activation_releases_only_the_exact_guard_and_is_resumable(target, monkeypatch):
    import json

    account, path, key = target
    guard, release, sysctl = (
        guest.ROOT / name for name in ("guard.json", "released.json", "forwarding.conf")
    )
    monkeypatch.setattr(guest, "REPLACEMENT_GUARD", guard)
    monkeypatch.setattr(guest, "REPLACEMENT_RELEASE", release)
    monkeypatch.setattr(guest, "REPLACEMENT_SYSCTL", sysctl)
    original_reader = guest.read_file

    def read_privileged_file(destination):
        record = original_reader(destination)
        if record is not None and destination in {guard, sysctl}:
            record = dict(record, uid=0)
        return record

    monkeypatch.setattr(guest, "read_file", read_privileged_file)
    snapshot = guest.inspect_projection(account, "compute-a")
    request = {
        "action": "reserve-replacement",
        "operation": "a" * 32,
        "expected_digest": snapshot["digest"],
    }
    guest.replacement_access(account, "compute-a", request)
    guard.write_text(json.dumps({"operation": "b" * 32}))
    guard.chmod(0o600)
    sysctl.write_text("net.ipv4.ip_forward=0\nnet.ipv6.conf.all.forwarding=0\n")
    with pytest.raises(ValueError, match="guard identity changed"):
        guest.replacement_access(account, "compute-a", dict(request, action="activate-replacement"))
    assert guard.exists() and sysctl.exists() and not release.exists()
    guard.write_text(json.dumps({"operation": "a" * 32}))
    guest.replacement_access(account, "compute-a", dict(request, action="activate-replacement"))
    assert not guard.exists() and not sysctl.exists() and release.exists()
    guest.replacement_access(account, "compute-a", dict(request, action="activate-replacement"))
    for _ in range(2):
        guest.replacement_access(account, "compute-a", dict(request, action="release-replacement"))
        assert not (guest.ROOT / "replacement.json").exists()
        guest.replacement_access(account, "compute-a", dict(request, action="inspect-replacement"))
