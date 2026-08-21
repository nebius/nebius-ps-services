from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest

from nebius_vpngw.deploy.ssh_policy import (
    HOST_KEYS_DIR_ENV,
    KNOWN_HOSTS_ENV,
    VM_HA_SSH_HOST_KEY_PATH,
    SSHHostIdentity,
    VMHASSHTrustScope,
    build_openssh_base_command,
    configure_paramiko_host_verification,
    publish_vm_ha_ssh_trust,
    require_explicit_known_hosts_file,
    require_vm_ha_management_key,
    require_vm_ha_ssh_policy,
)


def _trust_scope(*, cluster_id: str = "cluster-a") -> VMHASSHTrustScope:
    return VMHASSHTrustScope(
        tenant_id="tenant-a",
        project_id="project-a",
        region_id="eu-west1",
        gateway_name="gateway-a",
        cluster_id=cluster_id,
    )


def _private_host_keys(directory: Path, hostnames: tuple[str, ...]) -> dict[str, paramiko.PKey]:
    directory.mkdir()
    keys: dict[str, paramiko.PKey] = {}
    for hostname in hostnames:
        key = paramiko.RSAKey.generate(1024)
        key.write_private_key_file(str(directory / f"{hostname}.key"))
        keys[hostname] = key
    return keys


def test_vm_ha_host_identity_uses_non_cloud_init_managed_path() -> None:
    cloud_init = SSHHostIdentity(
        hostname="gateway-0",
        private_key=b"private-host-key",
    ).cloud_init_entries()

    assert VM_HA_SSH_HOST_KEY_PATH == "/etc/ssh/vpngw_host_key"
    assert f"path: {VM_HA_SSH_HOST_KEY_PATH}" in cloud_init
    assert "/etc/ssh/ssh_host_" not in cloud_init


def test_vm_ha_host_identity_repr_never_exposes_private_key() -> None:
    identity = SSHHostIdentity(
        hostname="gateway-0",
        private_key=b"private-host-key-material",
    )

    assert "private-host-key-material" not in repr(identity)


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
    with pytest.raises(ValueError, match=f"{KNOWN_HOSTS_ENV} is not set"):
        require_explicit_known_hosts_file({})


@pytest.mark.parametrize(
    "hostname",
    ("gateway-*", "gateway-?", "gateway-[0]", "gateway\\0", "../gateway", "gateway\x00"),
)
def test_vm_ha_policy_rejects_pattern_member_hostnames(hostname: str) -> None:
    with pytest.raises(ValueError, match="safe for exact host-key binding"):
        require_vm_ha_ssh_policy((hostname,), {})


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
    private_path = private_directory / f"{hostname}.key"
    private_path.write_text(f"{key.get_name()} {key.get_base64()}\n", encoding="utf-8")
    private_path.chmod(0o600)
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


def test_vm_ha_policy_rejects_insecure_private_host_key_permissions(tmp_path: Path) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    private_directory.mkdir()
    key = paramiko.RSAKey.generate(1024)
    private_path = private_directory / f"{hostname}.key"
    key.write_private_key_file(str(private_path))
    private_path.chmod(0o640)
    known_hosts = tmp_path / "known_hosts"
    pins = paramiko.HostKeys()
    pins.add(hostname, key.get_name(), key)
    pins.save(str(known_hosts))

    with pytest.raises(ValueError, match="group or others"):
        require_vm_ha_ssh_policy(
            (hostname,),
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


def test_managed_vm_ha_trust_is_created_only_when_apply_publishes(tmp_path: Path) -> None:
    hostnames = ("gateway-0", "gateway-1")
    private_directory = tmp_path / "host-keys"
    keys = _private_host_keys(private_directory, hostnames)
    managed_root = tmp_path / "managed"
    scope = _trust_scope()

    policy = require_vm_ha_ssh_policy(
        tuple((hostname, f"203.0.113.{index + 10}") for index, hostname in enumerate(hostnames)),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        enrollment_hosts=hostnames,
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )

    assert policy.managed_action == "create"
    assert not managed_root.exists()
    assert policy.pin_target_for("gateway-0") == "gateway-0"
    assert publish_vm_ha_ssh_trust(policy) is True

    deployment_directory = managed_root / scope.digest
    receipt = deployment_directory / "trust.json"
    projection = deployment_directory / "known_hosts"
    assert stat_mode(receipt) == 0o600
    assert stat_mode(projection) == 0o600
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["scope_sha256"] == scope.digest
    assert {member["hostname"] for member in payload["members"]} == set(hostnames)
    assert "PRIVATE" not in receipt.read_text(encoding="utf-8")
    assert keys["gateway-0"].get_base64() in projection.read_text(encoding="utf-8")

    compatibility_keys = paramiko.HostKeys(filename=str(projection))
    assert (
        compatibility_keys.lookup("203.0.113.10")[keys["gateway-0"].get_name()]
        == keys["gateway-0"]
    )


def test_default_managed_store_never_uses_general_known_hosts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    scope = _trust_scope()
    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        trust_scope=scope,
        allow_managed_repair=True,
    )

    publish_vm_ha_ssh_trust(policy)

    assert (tmp_path / ".ssh" / "nebius-vpngw" / scope.digest / "trust.json").is_file()
    assert not (tmp_path / ".ssh" / "known_hosts").exists()


def test_managed_vm_ha_trust_read_is_read_only_and_survives_address_change(
    tmp_path: Path,
) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    managed_root = tmp_path / "managed"
    scope = _trust_scope()
    creation = require_vm_ha_ssh_policy(
        ((hostname, "203.0.113.10"),),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        enrollment_hosts=(hostname,),
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    publish_vm_ha_ssh_trust(creation)
    projection = managed_root / scope.digest / "known_hosts"
    before = projection.stat().st_mtime_ns

    policy = require_vm_ha_ssh_policy(
        ((hostname, "203.0.113.99"),),
        {},
        enrollment_hosts=(),
        trust_scope=scope,
        managed_root=managed_root,
    )

    assert policy.managed_action is None
    assert policy.pin_target_for(hostname) == hostname
    assert projection.stat().st_mtime_ns == before
    command = build_openssh_base_command(policy=policy, hostname=hostname)
    assert f"HostKeyAlias={hostname}" in command


def test_managed_repair_rejects_conflicting_retained_private_host_key(tmp_path: Path) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    managed_root = tmp_path / "managed"
    scope = _trust_scope()
    creation = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        enrollment_hosts=(hostname,),
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    publish_vm_ha_ssh_trust(creation)

    replacement = paramiko.RSAKey.generate(1024)
    replacement.write_private_key_file(str(private_directory / f"{hostname}.key"))

    with pytest.raises(ValueError, match="does not match its exact pin"):
        require_vm_ha_ssh_policy(
            (hostname,),
            {HOST_KEYS_DIR_ENV: str(private_directory)},
            enrollment_hosts=(),
            trust_scope=scope,
            allow_managed_repair=True,
            managed_root=managed_root,
        )


def test_private_host_key_derivation_uses_the_validated_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    keys = _private_host_keys(private_directory, (hostname,))
    private_path = private_directory / f"{hostname}.key"
    validated_content = private_path.read_bytes()
    replacement = paramiko.RSAKey.generate(1024)
    real_run = subprocess.run

    def replace_source_then_derive(command, **kwargs):
        replacement.write_private_key_file(str(private_path))
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", replace_source_then_derive)
    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        enrollment_hosts=(hostname,),
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        managed_root=tmp_path / "managed",
    )

    assert policy.identity_for(hostname).private_key == validated_content
    projection = policy.known_hosts_file.read_text(encoding="utf-8")
    assert keys[hostname].get_base64() in projection
    assert replacement.get_base64() not in projection


def test_private_host_key_derivation_rejects_unsupported_public_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="ssh-unsupported AAAA\n"),
    )

    with pytest.raises(ValueError, match="malformed, encrypted, or unusable"):
        require_vm_ha_ssh_policy(
            (hostname,),
            {HOST_KEYS_DIR_ENV: str(private_directory)},
            enrollment_hosts=(hostname,),
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            managed_root=tmp_path / "managed",
        )


def test_explicit_override_migrates_after_publish_and_is_never_rewritten(tmp_path: Path) -> None:
    hostname = "gateway-0"
    key = paramiko.RSAKey.generate(1024)
    explicit = tmp_path / "operator-known-hosts"
    explicit.write_text(
        f"203.0.113.10 {key.get_name()} {key.get_base64()} operator-comment\n",
        encoding="utf-8",
    )
    original = explicit.read_bytes()
    managed_root = tmp_path / "managed"
    scope = _trust_scope()

    migration = require_vm_ha_ssh_policy(
        ((hostname, "203.0.113.10"),),
        {KNOWN_HOSTS_ENV: str(explicit)},
        enrollment_hosts=(),
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )

    assert migration.managed_action == "migrate"
    assert not managed_root.exists()
    publish_vm_ha_ssh_trust(migration)
    assert explicit.read_bytes() == original
    managed = require_vm_ha_ssh_policy(
        ((hostname, "203.0.113.20"),),
        {},
        enrollment_hosts=(),
        trust_scope=scope,
        managed_root=managed_root,
    )
    assert key.get_base64() in managed.known_hosts_file.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty readable regular file"):
        require_vm_ha_ssh_policy(
            ((hostname, "203.0.113.20"),),
            {KNOWN_HOSTS_ENV: str(tmp_path / "missing")},
            enrollment_hosts=(),
            trust_scope=scope,
            managed_root=managed_root,
        )
    with pytest.raises(ValueError, match="set but empty"):
        require_vm_ha_ssh_policy(
            ((hostname, "203.0.113.20"),),
            {KNOWN_HOSTS_ENV: ""},
            enrollment_hosts=(),
            trust_scope=scope,
            managed_root=managed_root,
        )


def test_receipt_remains_read_authority_when_projection_is_missing(tmp_path: Path) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    managed_root = tmp_path / "managed"
    scope = _trust_scope()
    creation = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    publish_vm_ha_ssh_trust(creation)
    projection = managed_root / scope.digest / "known_hosts"
    projection.unlink()

    read_only = require_vm_ha_ssh_policy(
        (hostname,),
        {},
        enrollment_hosts=(),
        trust_scope=scope,
        managed_root=managed_root,
    )
    assert not projection.exists()
    assert read_only.managed_action is None

    repair = require_vm_ha_ssh_policy(
        (hostname,),
        {},
        enrollment_hosts=(),
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    assert repair.managed_action == "repair"
    publish_vm_ha_ssh_trust(repair)
    assert projection.exists()


def test_managed_vm_ha_trust_publish_rejects_concurrent_change(tmp_path: Path) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    managed_root = tmp_path / "managed"
    scope = _trust_scope()
    candidate = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    deployment_directory = managed_root / scope.digest
    managed_root.mkdir(mode=0o700)
    deployment_directory.mkdir(mode=0o700)
    projection = deployment_directory / "known_hosts"
    projection.write_text("concurrent change\n", encoding="utf-8")
    projection.chmod(0o600)

    with pytest.raises(RuntimeError, match="changed after preflight"):
        publish_vm_ha_ssh_trust(candidate)


def test_unrelated_openssh_marker_does_not_poison_exact_member_pin(tmp_path: Path) -> None:
    hostname = "gateway-0"
    requested = paramiko.RSAKey.generate(1024)
    unrelated = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"@revoked other.example {unrelated.get_name()} {unrelated.get_base64()}\n"
        f"{hostname} {requested.get_name()} {requested.get_base64()}\n",
        encoding="utf-8",
    )

    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {KNOWN_HOSTS_ENV: str(known_hosts)},
        enrollment_hosts=(),
    )

    assert requested.get_base64() in policy.known_hosts_file.read_text(encoding="utf-8")


def test_hashed_exact_pin_is_normalized_to_stable_member_hostname(tmp_path: Path) -> None:
    hostname = "gateway-0"
    address = "203.0.113.10"
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"{paramiko.HostKeys.hash_host(address)} {key.get_name()} {key.get_base64()}\n",
        encoding="utf-8",
    )

    policy = require_vm_ha_ssh_policy(
        ((hostname, address),),
        {KNOWN_HOSTS_ENV: str(known_hosts)},
        enrollment_hosts=(),
    )

    assert policy.pin_target_for(hostname) == hostname
    assert policy.known_hosts_file.read_text(encoding="utf-8").startswith(f"{hostname},{address} ")


def test_explicit_pin_accepts_configured_and_discovered_member_aliases(tmp_path: Path) -> None:
    hostname = "gateway-0"
    configured_address = "203.0.113.10"
    discovered_address = "203.0.113.99"
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"{discovered_address} {key.get_name()} {key.get_base64()}\n",
        encoding="utf-8",
    )

    policy = require_vm_ha_ssh_policy(
        ((hostname, discovered_address),),
        {KNOWN_HOSTS_ENV: str(known_hosts)},
        enrollment_hosts=(),
        additional_aliases={hostname: (configured_address,)},
    )

    assert policy.hostname_for_transport(discovered_address) == hostname
    assert policy.known_hosts_file.read_text(encoding="utf-8").startswith(
        f"{hostname},{configured_address},{discovered_address} "
    )


def test_explicit_pin_rejects_conflicting_member_aliases(tmp_path: Path) -> None:
    hostname = "gateway-0"
    configured_address = "203.0.113.10"
    discovered_address = "203.0.113.99"
    configured_key = paramiko.RSAKey.generate(1024)
    discovered_key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"{configured_address} {configured_key.get_name()} {configured_key.get_base64()}\n"
        f"{discovered_address} {discovered_key.get_name()} {discovered_key.get_base64()}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflicting exact pins"):
        require_vm_ha_ssh_policy(
            ((hostname, discovered_address),),
            {KNOWN_HOSTS_ENV: str(known_hosts)},
            enrollment_hosts=(),
            additional_aliases={hostname: (configured_address,)},
        )


def test_requested_revoked_pin_is_rejected(tmp_path: Path) -> None:
    hostname = "gateway-0"
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"@revoked {hostname} {key.get_name()} {key.get_base64()}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="revoked"):
        require_vm_ha_ssh_policy(
            (hostname,),
            {KNOWN_HOSTS_ENV: str(known_hosts)},
            enrollment_hosts=(),
        )


def test_missing_managed_trust_read_never_creates_state(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"

    with pytest.raises(ValueError, match="run apply"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            enrollment_hosts=(),
            trust_scope=_trust_scope(),
            managed_root=managed_root,
        )

    assert not managed_root.exists()


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "mode"])
def test_managed_trust_rejects_unsafe_projection(
    tmp_path: Path,
    unsafe: str,
) -> None:
    hostname = "gateway-0"
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, (hostname,))
    managed_root = tmp_path / "managed"
    scope = _trust_scope()
    creation = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(private_directory)},
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    publish_vm_ha_ssh_trust(creation)
    projection = managed_root / scope.digest / "known_hosts"
    original = projection.read_bytes()
    projection.unlink()
    if unsafe == "symlink":
        target = tmp_path / "outside-known-hosts"
        target.write_bytes(original)
        projection.symlink_to(target)
    elif unsafe == "hardlink":
        target = tmp_path / "outside-known-hosts"
        target.write_bytes(original)
        os.link(target, projection)
        projection.chmod(0o600)
    else:
        projection.write_bytes(original)
        projection.chmod(0o644)

    with pytest.raises(ValueError, match="unavailable|owner-only"):
        require_vm_ha_ssh_policy(
            (hostname,),
            {},
            enrollment_hosts=(),
            trust_scope=scope,
            managed_root=managed_root,
        )


def test_scope_digest_separates_vm_ha_clusters() -> None:
    first = _trust_scope(cluster_id="cluster-a")
    second = _trust_scope(cluster_id="cluster-b")

    assert first.digest == _trust_scope(cluster_id="cluster-a").digest
    assert first.digest != second.digest


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_vm_ha_management_key_requires_private_permissions_and_exact_public_key(
    tmp_path: Path,
) -> None:
    private_key = paramiko.RSAKey.generate(1024)
    private_path = tmp_path / "management-key"
    private_key.write_private_key_file(str(private_path))
    private_path.chmod(0o600)
    public_key = f"{private_key.get_name()} {private_key.get_base64()} operator"

    assert require_vm_ha_management_key(private_path, public_key) == private_path

    other_key = paramiko.RSAKey.generate(1024)
    with pytest.raises(ValueError, match="does not match"):
        require_vm_ha_management_key(
            private_path,
            f"{other_key.get_name()} {other_key.get_base64()}",
        )

    private_path.chmod(0o640)
    with pytest.raises(ValueError, match="group or others"):
        require_vm_ha_management_key(private_path, public_key)


@pytest.mark.parametrize(
    ("private_key", "message"),
    [
        (None, "requires ssh_private_key_path"),
        (Path("missing"), "regular file"),
    ],
)
def test_vm_ha_management_key_rejects_missing_inputs(
    tmp_path: Path,
    private_key: Path | None,
    message: str,
) -> None:
    candidate = None if private_key is None else tmp_path / private_key
    with pytest.raises(ValueError, match=message):
        require_vm_ha_management_key(candidate, "ssh-ed25519 fixture")
