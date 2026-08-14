from __future__ import annotations

from pathlib import Path

import paramiko
import pytest

from nebius_vpngw.deploy.ssh_policy import (
    HOST_KEYS_DIR_ENV,
    KNOWN_HOSTS_ENV,
    build_openssh_base_command,
    configure_paramiko_host_verification,
    require_explicit_known_hosts_file,
    require_vm_ha_ssh_policy,
)


@pytest.mark.parametrize("content", [b"", b"not a known-hosts record\n"])
def test_required_vm_ha_trust_rejects_empty_or_malformed_file(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "known_hosts"
    path.write_bytes(content)

    with pytest.raises(ValueError, match="non-empty readable|usable SSH host keys"):
        require_explicit_known_hosts_file({KNOWN_HOSTS_ENV: str(path)})


def test_required_vm_ha_trust_accepts_pinned_host_identity(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    keys = paramiko.HostKeys()
    keys.add("gateway-a", "ssh-rsa", paramiko.RSAKey.generate(1024))
    keys.save(str(path))

    assert require_explicit_known_hosts_file({KNOWN_HOSTS_ENV: str(path)}) == path


def test_required_vm_ha_trust_rejects_missing_enrollment() -> None:
    with pytest.raises(ValueError, match=f"{KNOWN_HOSTS_ENV} is required"):
        require_explicit_known_hosts_file({})


def test_vm_ha_policy_accepts_unencrypted_private_key_matching_exact_pin(
    tmp_path: Path,
) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    private_directory.mkdir()
    private_key = paramiko.RSAKey.generate(1024)
    private_key.write_private_key_file(str(private_directory / f"{hostname}.key"))
    known_hosts = tmp_path / "known_hosts"
    pins = paramiko.HostKeys()
    pins.add(hostname, private_key.get_name(), private_key)
    pins.save(str(known_hosts))

    policy = require_vm_ha_ssh_policy(
        [hostname],
        {
            KNOWN_HOSTS_ENV: str(known_hosts),
            HOST_KEYS_DIR_ENV: str(private_directory),
        },
    )

    assert policy.identity_for(hostname).private_key.startswith(b"-----BEGIN")


def test_vm_ha_policy_rejects_public_only_host_key(tmp_path: Path) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    private_directory.mkdir()
    key = paramiko.RSAKey.generate(1024)
    (private_directory / f"{hostname}.key").write_text(
        f"{key.get_name()} {key.get_base64()}\n", encoding="utf-8"
    )
    known_hosts = tmp_path / "known_hosts"
    pins = paramiko.HostKeys()
    pins.add(hostname, key.get_name(), key)
    pins.save(str(known_hosts))

    with pytest.raises(ValueError, match="malformed, encrypted, or unusable"):
        require_vm_ha_ssh_policy(
            [hostname],
            {
                KNOWN_HOSTS_ENV: str(known_hosts),
                HOST_KEYS_DIR_ENV: str(private_directory),
            },
        )


def test_vm_ha_policy_consumes_protected_snapshot_after_source_mutation(tmp_path: Path) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    private_directory.mkdir()
    key = paramiko.RSAKey.generate(1024)
    key.write_private_key_file(str(private_directory / f"{hostname}.key"))
    known_hosts = tmp_path / "known_hosts"
    pins = paramiko.HostKeys()
    pins.add(hostname, key.get_name(), key)
    pins.save(str(known_hosts))
    policy = require_vm_ha_ssh_policy(
        [hostname],
        {
            KNOWN_HOSTS_ENV: str(known_hosts),
            HOST_KEYS_DIR_ENV: str(private_directory),
        },
    )

    known_hosts.write_text("changed\n", encoding="utf-8")

    policy.assert_current()
    assert policy.known_hosts_file != known_hosts
    assert key.get_base64() in policy.known_hosts_file.read_text(encoding="utf-8")
    command = build_openssh_base_command(policy=policy, hostname=hostname)
    assert f"UserKnownHostsFile={policy.known_hosts_file}" in command
    assert f"HostKeyAlias={hostname}" in command


def test_existing_only_policy_does_not_require_private_host_key_source(tmp_path: Path) -> None:
    hostname = "gateway-0"
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    pins = paramiko.HostKeys()
    pins.add(hostname, key.get_name(), key)
    pins.save(str(known_hosts))

    policy = require_vm_ha_ssh_policy(
        [hostname],
        {KNOWN_HOSTS_ENV: str(known_hosts)},
        enrollment_hosts=(),
    )

    assert policy.identities == ()
    assert policy.pin_target_for(hostname) == hostname

    known_hosts.write_text("replaced\n", encoding="utf-8")
    client = paramiko.SSHClient()
    configure_paramiko_host_verification(
        client,
        paramiko,
        policy=policy,
        hostname=hostname,
        transport_host="203.0.113.10",
    )
    assert client.get_host_keys().lookup("203.0.113.10")[key.get_name()] == key
