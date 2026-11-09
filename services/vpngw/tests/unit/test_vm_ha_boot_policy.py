from __future__ import annotations

import base64
import gzip
from pathlib import Path

import pytest
import yaml

from nebius_vpngw import boot_policy
from nebius_vpngw.deploy.ssh_push import SSHPush
from nebius_vpngw.deploy.vm_manager import VMManager


def write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_existing_host_reconciles_only_owned_files_while_fenced(tmp_path):
    kernel = write(tmp_path, "proc/sys/net/ipv4/ip_forward", "0\n")
    for name in boot_policy.OWNED:
        write(
            tmp_path, name, "# retain\nnet.ipv4.ip_forward = 1\nnet.ipv4.conf.all.rp_filter = 0\n"
        )
    admin = write(tmp_path, "etc/sysctl.conf", "# untouched\nnet.ipv4.ip_forward = 0\n")
    boot_policy.install_ha_policy(root=tmp_path)
    expected = (
        "# retain\nnet.ipv4.conf.all.rp_filter = 0\n"
        + boot_policy.HA_MARKER
        + "\nnet.ipv4.ip_forward = 0\n"
    )
    for name in boot_policy.OWNED:
        assert (tmp_path / name).read_text() == expected
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o644
    before = [(tmp_path / name).stat().st_mtime_ns for name in boot_policy.OWNED]
    boot_policy.install_ha_policy(root=tmp_path)
    assert before == [(tmp_path / name).stat().st_mtime_ns for name in boot_policy.OWNED]
    assert kernel.read_text() == "0\n"
    assert admin.read_text() == "# untouched\nnet.ipv4.ip_forward = 0\n"


@pytest.mark.parametrize(
    "setting",
    [
        "net.ipv4.ip_forward=1",
        "  net.ipv4.ip_forward = 1 # route",
        "-net/ipv4/ip_forward = 1",
        "net.ipv4.conf.all.forwarding = 1",
        "net.ipv4.conf.*.forwarding = 1",
        "net.ipv4.conf.eth0.forwarding = 1",
        "net.ipv4.conf.default.forwarding = 1",
        "net.ipv4.ip_forward = invalid",
    ],
)
@pytest.mark.parametrize(
    "name", ["etc/sysctl.conf", "etc/sysctl.d/10-admin.conf", "etc/ufw/sysctl.conf"]
)
def test_unmanaged_forwarding_writers_rejected_before_any_file_write(tmp_path, name, setting):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", "0")
    original = write(tmp_path, boot_policy.OWNED[0], "net.ipv4.ip_forward = 1\n")
    admin = write(tmp_path, name, setting + "\n")
    with pytest.raises(boot_policy.BootPolicyError, match="unmanaged-forwarding-conflict"):
        boot_policy.install_ha_policy(root=tmp_path)
    assert original.read_text() == "net.ipv4.ip_forward = 1\n"
    assert admin.read_text() == setting + "\n"


def test_policy_respects_sysctl_basename_masking_and_ignores_comments(tmp_path):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", "0")
    write(tmp_path, "usr/lib/sysctl.d/40-admin.conf", "net.ipv4.ip_forward = 1\n")
    write(tmp_path, "etc/sysctl.d/40-admin.conf", "# net.ipv4.ip_forward = 1\n; comment\n")
    boot_policy.install_ha_policy(root=tmp_path)
    assert not (tmp_path / boot_policy.OWNED[1]).exists()


@pytest.mark.parametrize("forwarding", ["1", "", "unknown"])
def test_policy_cannot_change_a_serving_or_unknown_kernel(tmp_path, forwarding):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", forwarding)
    with pytest.raises(boot_policy.BootPolicyError, match="requires-forwarding-off"):
        boot_policy.install_ha_policy(root=tmp_path)
    assert not (tmp_path / boot_policy.OWNED[0]).exists()


def test_owned_symlink_is_never_followed(tmp_path):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", "0")
    admin = write(tmp_path, "etc/admin", "net.ipv4.ip_forward = 1\n")
    owned = tmp_path / boot_policy.OWNED[0]
    owned.parent.mkdir(parents=True)
    owned.symlink_to(admin)
    with pytest.raises(boot_policy.BootPolicyError, match="owned-file-invalid"):
        boot_policy.install_ha_policy(root=tmp_path)
    assert admin.read_text() == "net.ipv4.ip_forward = 1\n"


@pytest.mark.parametrize("ha", [False, True])
def test_bootstrap_persistent_forwarding_is_topology_aware(ha):
    raw = VMManager(project_id="test", region="eu-west1")._build_cloud_init(
        vm_ha=ha,
        ingress={"peer_ips": []},
    )
    document = yaml.safe_load(raw)
    files = {entry["path"]: entry for entry in document["write_files"]}
    assert "net.ipv4.ip_forward = 0" in files["/" + boot_policy.OWNED[0]]["content"]
    command = ["/usr/bin/python3", str(boot_policy.EXECUTABLE)]
    if ha:
        assert document["runcmd"].index(command) < document["runcmd"].index(["sysctl", "--system"])
        bundled = files[str(boot_policy.EXECUTABLE)]
        assert (
            gzip.decompress(base64.b64decode(bundled["content"]))
            == Path(boot_policy.__file__).read_bytes()
        )
        assert not any(
            "sed -i" in str(c) and "net.ipv4.ip_forward" in str(c) for c in document["runcmd"]
        )
    else:
        assert str(boot_policy.EXECUTABLE) not in files
        assert command not in document["runcmd"]


def test_existing_host_policy_install_is_between_guard_and_controller():
    commands = SSHPush._vm_ha_control_service_commands(initialize_policy=False)
    assert (
        commands.index("sudo systemctl restart nebius-vpngw-vm-ha-guard.service")
        < commands.index("sudo /usr/bin/python3 -m nebius_vpngw.boot_policy")
        < commands.index("sudo systemctl restart nebius-vpngw-vm-ha.service")
    )


def test_ordinary_reentry_requires_ha_deactivation_and_preserves_other_settings(tmp_path):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", "0")
    file = write(tmp_path, boot_policy.OWNED[0], "net.ipv4.conf.all.rp_filter = 0\n")
    boot_policy.install_ha_policy(root=tmp_path)
    marker = write(tmp_path, "etc/nebius-vpngw/vm-ha-enabled", "")
    with pytest.raises(boot_policy.BootPolicyError, match="requires-ha-deactivation"):
        boot_policy.restore_ordinary_bootstrap_policy(root=tmp_path)
    marker.unlink()
    boot_policy.restore_ordinary_bootstrap_policy(root=tmp_path)
    assert file.read_text() == "net.ipv4.conf.all.rp_filter = 0\nnet.ipv4.ip_forward = 0\n"
    assert (tmp_path / "proc/sys/net/ipv4/ip_forward").read_text() == "0"
    boot_policy.restore_ordinary_bootstrap_policy(root=tmp_path)
    assert file.read_text() == "net.ipv4.conf.all.rp_filter = 0\nnet.ipv4.ip_forward = 0\n"


def test_idempotent_policy_install_repairs_unsafe_file_mode(tmp_path):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", "0")
    boot_policy.install_ha_policy(root=tmp_path)
    path = tmp_path / boot_policy.OWNED[0]
    path.chmod(0o666)
    boot_policy.install_ha_policy(root=tmp_path)
    assert path.stat().st_mode & 0o777 == 0o644


def test_ordinary_boot_fence_replaces_owned_forwarding_without_touching_live_kernel(tmp_path):
    write(tmp_path, "proc/sys/net/ipv4/ip_forward", "1")
    for name in boot_policy.OWNED:
        write(tmp_path, name, "net.ipv4.ip_forward = 1\nnet.ipv4.conf.all.rp_filter = 0\n")
    boot_policy.restore_ordinary_bootstrap_policy(root=tmp_path)
    assert (tmp_path / "proc/sys/net/ipv4/ip_forward").read_text() == "1"
    for name in boot_policy.OWNED:
        assert (
            tmp_path / name
        ).read_text() == "net.ipv4.conf.all.rp_filter = 0\nnet.ipv4.ip_forward = 0\n"


def test_ordinary_boot_fence_rejects_unmanaged_forwarding_before_mutation(tmp_path):
    owned = write(tmp_path, boot_policy.OWNED[0], "net.ipv4.ip_forward = 1\n")
    write(tmp_path, "etc/sysctl.d/10-admin.conf", "net.ipv4.conf.all.forwarding = 1\n")
    with pytest.raises(boot_policy.BootPolicyError, match="unmanaged-forwarding-conflict"):
        boot_policy.restore_ordinary_bootstrap_policy(root=tmp_path)
    assert owned.read_text() == "net.ipv4.ip_forward = 1\n"


def test_ordinary_runtime_cannot_enable_forwarding_without_destination_proof(monkeypatch):
    from nebius_vpngw.agent import ordinary, routing_guard

    class Protection:
        def verify(self):
            raise RuntimeError("destination protection is missing")

    monkeypatch.setattr(ordinary, "destination_protection", lambda cfg: Protection())
    monkeypatch.setattr(
        routing_guard, "_enforce_routing_sysctls", lambda: pytest.fail("forwarding enabled")
    )
    with pytest.raises(RuntimeError, match="destination protection is missing"):
        routing_guard._enforce_routing_invariants_locked({"connections": []})
