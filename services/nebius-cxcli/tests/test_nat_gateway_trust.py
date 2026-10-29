from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import ssh_host_enrollment as trust
from nebius_cxcli.ssh_trust import ssh_trust_options


def key(value=1):
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([value]) * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


@pytest.fixture
def enrollment(tmp_path, monkeypatch):
    paths = NS(generated_dir=tmp_path)
    outputs = {"public_ip": "192.0.2.1/32"}
    events = []
    binding = (
        "computeinstance-test",
        "project-test",
        "version",
        100.0,
        "disk",
        "allocation",
        "192.0.2.1/32",
        key(),
    )

    def bind(*args):
        events.append("bind")
        return binding

    monkeypatch.setattr(trust, "_binding", bind)
    return NS(paths=paths, outputs=outputs, events=events, bind=bind)


def test_missing_default_file_is_enrolled_before_strict_ssh(enrollment):
    path = enrollment.paths.generated_dir / "ssh_known_hosts"
    # Original failure at the unmodified shared strict consumer.
    with pytest.raises(RuntimeError, match="SSH known-hosts file does not exist"):
        ssh_trust_options(path)
    result = trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert result == path
    assert result.read_text() == f"192.0.2.1 {key()}\n"
    assert result.stat().st_mode & 0o777 == 0o600
    assert "StrictHostKeyChecking=yes" in ssh_trust_options(result)
    assert enrollment.events == ["bind", "bind"]
    before = result.stat().st_mtime_ns
    trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert result.stat().st_mtime_ns == before
    assert enrollment.events == ["bind", "bind"]


def test_preserves_unrelated_entries_and_existing_conflicting_pin(enrollment):
    path = enrollment.paths.generated_dir / "ssh_known_hosts"
    path.write_text(f"192.0.2.2 {key(2)}")
    trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert path.read_text() == f"192.0.2.2 {key(2)}\n192.0.2.1 {key()}\n"
    path.write_text(f"192.0.2.1 {key(2)}\n")
    enrollment.events.clear()
    trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert path.read_text() == f"192.0.2.1 {key(2)}\n"
    assert enrollment.events == []  # Strict SSH, never enrollment, decides mismatches.


def test_explicit_file_is_never_bootstrapped(enrollment):
    path = enrollment.paths.generated_dir / "explicit"
    with pytest.raises(RuntimeError, match="does not exist"):
        trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, path)
    path.write_text(f"192.0.2.1 {key()}\n")
    assert trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, path) == path
    assert enrollment.events == []


@pytest.mark.parametrize("problem", ["symlink", "hardlink", "directory", "permissions", "fifo"])
def test_unsafe_managed_trust_fails_before_provider_reads(enrollment, problem):
    path = enrollment.paths.generated_dir / "ssh_known_hosts"
    other = path.with_name("other")
    other.write_text("preserved")
    if problem == "symlink":
        path.symlink_to(other)
    elif problem == "hardlink":
        os.link(other, path)
    elif problem == "directory":
        path.mkdir()
    elif problem == "fifo":
        os.mkfifo(path)
    else:
        path.write_text("")
        path.chmod(0o666)
    with pytest.raises(RuntimeError):
        trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert other.read_text() == "preserved"
    assert enrollment.events == []


@pytest.mark.parametrize("problem", ["provider", "binding", "concurrent"])
def test_failure_does_not_publish_pin(enrollment, monkeypatch, problem):
    path = enrollment.paths.generated_dir / "ssh_known_hosts"

    def changed(*args):
        binding = enrollment.bind(*args)
        if problem == "provider":
            raise RuntimeError("unavailable")
        if problem == "binding" and enrollment.events.count("bind") > 1:
            return (*binding[:-1], key(2))
        if problem == "concurrent":
            path.write_text("concurrent writer\n")
        return binding

    monkeypatch.setattr(trust, "_binding", changed)
    with pytest.raises(RuntimeError):
        trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert not path.exists() or path.read_text() == "concurrent writer\n"
    assert not list(path.parent.glob(".ssh-host-pin-*"))


def test_retained_guest_generated_key_needs_operator_trust(enrollment, monkeypatch):
    monkeypatch.setattr(trust, "_binding", lambda *args: ("instance", ""))
    with pytest.raises(RuntimeError, match="independently verified"):
        trust.prepare_ssh_host_trust(None, enrollment.outputs, enrollment.paths, None)
    assert not (enrollment.paths.generated_dir / "ssh_known_hosts").exists()


@pytest.mark.parametrize(
    "problem", [None, "parent", "id", "disk", "allocation", "address", "state"]
)
def test_compute_binding_checks_actual_provider_object(monkeypatch, problem):
    from nebius.api.nebius.compute import v1

    outputs = dict(
        instance_id="computeinstance-test",
        boot_disk_id="disk",
        public_allocation_id="allocation",
        public_ip="192.0.2.1/32",
    )
    obj = NS(
        metadata=NS(
            id="computeinstance-test",
            parent_id="project-test",
            resource_version="v1",
            created_at=datetime.fromtimestamp(100, UTC),
        ),
        spec=NS(boot_disk=NS(existing_disk=NS(id="disk")), cloud_init_user_data="#cloud-config\n"),
        status=NS(
            state=NS(name="RUNNING"),
            network_interfaces=[
                NS(public_ip_address=NS(allocation_id="allocation", address="192.0.2.1/32"))
            ],
        ),
    )
    if problem in {"parent", "id"}:
        setattr(obj.metadata, "parent_id" if problem == "parent" else "id", "other")
    elif problem == "disk":
        obj.spec.boot_disk.existing_disk.id = "other"
    elif problem in {"allocation", "address"}:
        setattr(
            obj.status.network_interfaces[0].public_ip_address,
            "allocation_id" if problem == "allocation" else "address",
            "other" if problem == "allocation" else "192.0.2.2/32",
        )
    elif problem == "state":
        obj.status.state.name = "STOPPED"
    monkeypatch.setattr(v1, "InstanceServiceClient", lambda sdk: NS(get=None))
    sdk = NS(sdk=None, project_id="project-test", call=lambda *args: obj)
    if problem:
        with pytest.raises(RuntimeError):
            trust._binding(sdk, outputs)
    else:
        assert trust._binding(sdk, outputs)[3] == 100
