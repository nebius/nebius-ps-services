from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest

from nebius_vpngw.deploy import ssh_policy as ssh_policy_module
from nebius_vpngw.deploy.ssh_policy import (
    HOST_KEYS_DIR_ENV,
    KNOWN_HOSTS_ENV,
    VM_HA_SSH_HOST_KEY_PATH,
    LegacyOrdinarySSHEnrollmentRequired,
    SSHHostIdentity,
    SSHHostKeyEnrollment,
    SSHHostKeyRecovery,
    SSHTrustAuthority,
    TrustedSSHMemberImport,
    VMHAReplacementSSHIdentityProblem,
    VMHAReplacementSSHIdentityUnavailable,
    VMHASSHTrustScope,
    build_openssh_base_command,
    configure_paramiko_host_verification,
    managed_ssh_trust_available,
    managed_ssh_trust_member,
    prepare_vm_ha_ssh_identity_rotation,
    publish_vm_ha_ssh_identity_rotation,
    publish_vm_ha_ssh_trust,
    require_explicit_known_hosts_file,
    require_vm_ha_management_key,
    require_vm_ha_ssh_policy,
    validate_vm_ha_ssh_identity_rotation,
)


def _trust_scope(*, cluster_id: str = "cluster-a") -> VMHASSHTrustScope:
    return VMHASSHTrustScope(
        tenant_id="tenant-a",
        project_id="project-a",
        region_id="eu-west1",
        gateway_name="gateway-a",
        cluster_id=cluster_id,
    )


def _default_host_key_directory(home: Path, scope: VMHASSHTrustScope) -> Path:
    return home / ".ssh" / "nebius-vpngw" / "host-keys" / scope.gateway_name / scope.digest


def _private_host_keys(directory: Path, hostnames: tuple[str, ...]) -> dict[str, paramiko.PKey]:
    directory.mkdir()
    keys: dict[str, paramiko.PKey] = {}
    for hostname in hostnames:
        key = paramiko.RSAKey.generate(1024)
        key.write_private_key_file(str(directory / f"{hostname}.key"))
        keys[hostname] = key
    return keys


def _protect_default_host_key_directories(home: Path, directory: Path) -> None:
    for path in (
        home / ".ssh",
        home / ".ssh" / "nebius-vpngw",
        home / ".ssh" / "nebius-vpngw" / "host-keys",
        directory.parent,
        directory,
    ):
        path.chmod(0o700)


def _ed25519_public_key(tmp_path: Path) -> tuple[str, str]:
    private_key = tmp_path / "observed-host-key"
    result = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    fields = private_key.with_suffix(".pub").read_text(encoding="utf-8").split()
    return fields[0], fields[1]


def test_retained_ordinary_network_enrollment_is_typed_and_persisted_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KNOWN_HOSTS_ENV, raising=False)
    monkeypatch.delenv(HOST_KEYS_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = _trust_scope(cluster_id="ordinary-v1")
    host = ("gateway-0", "203.0.113.10")

    with pytest.raises(LegacyOrdinarySSHEnrollmentRequired) as required:
        require_vm_ha_ssh_policy(
            (host,),
            enrollment_hosts=(),
            retained_hosts={host[0]},
            trust_scope=scope,
            allow_managed_repair=True,
            persist_default_host_keys=True,
            allow_legacy_ordinary_enrollment=True,
        )
    assert required.value.hostnames == {host[0]}

    key_type, key_data = _ed25519_public_key(tmp_path)
    current_checks: list[str] = []
    enrollment = SSHHostKeyEnrollment(
        hostname=host[0],
        key_type=key_type,
        key_data=key_data,
        authority=SSHTrustAuthority(
            kind="legacy-ordinary-network-enrollment-v1",
            compute_binding_sha256="a" * 64,
        ),
        assert_current=lambda: current_checks.append("current"),
    )
    policy = require_vm_ha_ssh_policy(
        (host,),
        enrollment_hosts=(),
        retained_hosts={host[0]},
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        allow_legacy_ordinary_enrollment=True,
        legacy_host_key_enrollments={host[0]: enrollment},
    )
    assert policy.managed_action == "enroll"
    assert publish_vm_ha_ssh_trust(policy) is True
    member = managed_ssh_trust_member(scope, host[0])
    assert member is not None
    assert member.authority == enrollment.authority
    assert member.pins == {key_type: key_data}
    assert len(current_checks) >= 2

    repeated = require_vm_ha_ssh_policy(
        (host,),
        enrollment_hosts=(),
        retained_hosts={host[0]},
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        allow_legacy_ordinary_enrollment=True,
    )
    assert repeated.managed_action is None


def test_existing_receipt_member_drift_never_reopens_network_enrollment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KNOWN_HOSTS_ENV, raising=False)
    monkeypatch.delenv(HOST_KEYS_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = _trust_scope(cluster_id="ordinary-v1")
    foreign_policy = require_vm_ha_ssh_policy(
        (("foreign-0", "203.0.113.20"),),
        enrollment_hosts={"foreign-0"},
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
    )
    assert publish_vm_ha_ssh_trust(foreign_policy) is True

    recovery_calls: list[frozenset[str]] = []
    with pytest.raises(ValueError, match="receipt member set does not match"):
        require_vm_ha_ssh_policy(
            (("gateway-0", "203.0.113.10"),),
            enrollment_hosts=(),
            retained_hosts={"gateway-0"},
            trust_scope=scope,
            allow_managed_repair=True,
            persist_default_host_keys=True,
            allow_legacy_ordinary_enrollment=True,
            host_identity_recovery=lambda hostnames: recovery_calls.append(hostnames) or {},
        )
    assert recovery_calls == []


def test_ordinary_receipt_pin_is_rebound_into_vm_ha_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KNOWN_HOSTS_ENV, raising=False)
    monkeypatch.delenv(HOST_KEYS_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    key_type, key_data = _ed25519_public_key(tmp_path)
    ordinary_scope = _trust_scope(cluster_id="ordinary-v1")
    active = ("gateway-0", "203.0.113.10")
    ordinary_policy = require_vm_ha_ssh_policy(
        (active,),
        enrollment_hosts=(),
        retained_hosts={active[0]},
        trust_scope=ordinary_scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        allow_legacy_ordinary_enrollment=True,
        legacy_host_key_enrollments={
            active[0]: SSHHostKeyEnrollment(
                hostname=active[0],
                key_type=key_type,
                key_data=key_data,
                authority=SSHTrustAuthority(
                    kind="legacy-ordinary-network-enrollment-v1",
                    compute_binding_sha256="b" * 64,
                ),
                assert_current=lambda: None,
            )
        },
    )
    publish_vm_ha_ssh_trust(ordinary_policy)
    predecessor = managed_ssh_trust_member(ordinary_scope, active[0])
    assert predecessor is not None

    ha_scope = _trust_scope(cluster_id="cluster-a")
    current_checks: list[str] = []
    ha_policy = require_vm_ha_ssh_policy(
        (active, ("gateway-1", "203.0.113.11")),
        enrollment_hosts={"gateway-1"},
        retained_hosts={active[0]},
        trust_scope=ha_scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        trusted_member_imports={
            active[0]: TrustedSSHMemberImport(
                hostname=active[0],
                pins=predecessor.pins,
                predecessor_receipt_sha256=predecessor.receipt_sha256,
                compute_binding_sha256="c" * 64,
                assert_current=lambda: current_checks.append("current"),
            )
        },
    )
    assert ha_policy.managed_receipt_sha256 is not None
    publish_vm_ha_ssh_trust(ha_policy)
    imported = managed_ssh_trust_member(ha_scope, active[0])
    assert imported is not None
    assert imported.pins == predecessor.pins
    assert imported.authority.kind == "ordinary-migration-v1"
    assert imported.authority.predecessor_receipt_sha256 == predecessor.receipt_sha256
    assert current_checks


def test_managed_trust_availability_changes_only_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KNOWN_HOSTS_ENV, raising=False)
    monkeypatch.delenv(HOST_KEYS_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = _trust_scope(cluster_id="ordinary-v1")

    assert managed_ssh_trust_available(scope) is False
    policy = require_vm_ha_ssh_policy(
        (("gateway-0", "203.0.113.10"),),
        enrollment_hosts={"gateway-0"},
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
    )
    assert managed_ssh_trust_available(scope) is False

    assert publish_vm_ha_ssh_trust(policy) is True
    assert managed_ssh_trust_available(scope) is True


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
        compatibility_keys.lookup("203.0.113.10")[keys["gateway-0"].get_name()] == keys["gateway-0"]
    )


def test_unset_host_keys_directory_uses_per_gateway_operator_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    default_directory = _default_host_key_directory(tmp_path, _trust_scope())

    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {},
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        persist_default_host_keys=True,
        managed_root=tmp_path / "managed",
    )

    private_key = default_directory / f"{hostname}.key"
    assert stat_mode(tmp_path / ".ssh") == 0o700
    assert stat_mode(tmp_path / ".ssh" / "nebius-vpngw") == 0o700
    assert stat_mode(default_directory.parent) == 0o700
    assert stat_mode(default_directory) == 0o700
    assert stat_mode(private_key) == 0o600
    assert private_key.stat().st_nlink == 1
    assert policy.identity_for(hostname).private_key == private_key.read_bytes()
    assert not private_key.with_suffix(".key.pub").exists()
    assert "ssh-ed25519" in policy.known_hosts_file.read_text(encoding="utf-8")


def test_default_host_keys_are_isolated_by_complete_deployment_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    first_scope = _trust_scope(cluster_id="cluster-a")
    second_scope = _trust_scope(cluster_id="cluster-b")

    first = require_vm_ha_ssh_policy(
        ("gateway-0",),
        {},
        trust_scope=first_scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        managed_root=tmp_path / "managed-a",
    )
    second = require_vm_ha_ssh_policy(
        ("gateway-0",),
        {},
        trust_scope=second_scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        managed_root=tmp_path / "managed-b",
    )

    first_path = _default_host_key_directory(tmp_path, first_scope) / "gateway-0.key"
    second_path = _default_host_key_directory(tmp_path, second_scope) / "gateway-0.key"
    assert first_path != second_path
    assert first_path.is_file()
    assert second_path.is_file()
    assert first.identity_for("gateway-0").private_key == first_path.read_bytes()
    assert second.identity_for("gateway-0").private_key == second_path.read_bytes()
    assert first_path.read_bytes() != second_path.read_bytes()


def test_default_host_key_persistence_normalizes_filesystem_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    def fail_write(path: Path, content: bytes) -> None:
        raise OSError("simulated private filesystem detail")

    monkeypatch.setattr(ssh_policy_module, "_write_exclusive_private_key", fail_write)

    with pytest.raises(
        ValueError,
        match="VM-HA managed SSH host-key persistence failed",
    ) as captured:
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    assert "simulated private filesystem detail" not in str(captured.value)


def test_concurrent_default_host_key_preparation_converges_without_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = _trust_scope()

    def resolve() -> bytes:
        policy = require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            trust_scope=scope,
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )
        return policy.identity_for("gateway-0").private_key

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(lambda _: resolve(), range(2))

    directory = _default_host_key_directory(tmp_path, scope)
    private_key = directory / "gateway-0.key"
    assert first == second == private_key.read_bytes()
    assert stat_mode(private_key) == 0o600
    assert private_key.stat().st_nlink == 1
    assert sorted(path.name for path in directory.iterdir()) == [".lock", "gateway-0.key"]


def test_unset_host_keys_directory_dry_run_uses_ephemeral_key_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    policy = require_vm_ha_ssh_policy(
        ("gateway-0",),
        {},
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        managed_root=tmp_path / "managed",
    )

    assert policy.identity_for("gateway-0").private_key.startswith(b"-----BEGIN OPENSSH")
    assert "ssh-ed25519" in policy.known_hosts_file.read_text(encoding="utf-8")
    assert not (tmp_path / ".ssh").exists()
    assert not (tmp_path / "managed").exists()


def test_unset_host_keys_directory_repairs_retained_member_from_operator_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    default_directory = _default_host_key_directory(tmp_path, _trust_scope())
    default_directory.parent.mkdir(parents=True)
    keys = _private_host_keys(default_directory, (hostname,))
    _protect_default_host_key_directories(tmp_path, default_directory)

    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {},
        enrollment_hosts=(),
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        managed_root=tmp_path / "managed",
    )

    assert policy.identities == ()
    assert policy.managed_action == "create"
    assert keys[hostname].get_base64() in policy.known_hosts_file.read_text(encoding="utf-8")


def test_explicit_host_keys_directory_overrides_per_gateway_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    default_directory = _default_host_key_directory(tmp_path, _trust_scope())
    default_directory.parent.mkdir(parents=True)
    default_keys = _private_host_keys(default_directory, (hostname,))
    _protect_default_host_key_directories(tmp_path, default_directory)
    explicit_directory = tmp_path / "explicit-host-keys"
    explicit_keys = _private_host_keys(explicit_directory, (hostname,))

    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(explicit_directory)},
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        managed_root=tmp_path / "managed",
    )

    projection = policy.known_hosts_file.read_text(encoding="utf-8")
    assert explicit_keys[hostname].get_base64() in projection
    assert default_keys[hostname].get_base64() not in projection


@pytest.mark.parametrize(
    ("override", "error_match"),
    (("", "set but empty"), ("relative-host-keys", "must name an absolute")),
)
def test_invalid_explicit_host_keys_directory_never_falls_back_to_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: str,
    error_match: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    default_directory = _default_host_key_directory(tmp_path, _trust_scope())
    default_directory.parent.mkdir(parents=True)
    _private_host_keys(default_directory, ("gateway-0",))
    _protect_default_host_key_directories(tmp_path, default_directory)

    with pytest.raises(ValueError, match=error_match):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {HOST_KEYS_DIR_ENV: override},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            managed_root=tmp_path / "managed",
        )


@pytest.mark.parametrize("use_default", (False, True))
def test_private_host_key_rejects_hard_links_for_explicit_and_default_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_default: bool,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    original_directory = tmp_path / "original-host-keys"
    _private_host_keys(original_directory, ("gateway-0",))
    if use_default:
        selected_directory = _default_host_key_directory(tmp_path, _trust_scope())
        environment: dict[str, str] = {}
    else:
        selected_directory = tmp_path / "explicit-host-keys"
        environment = {HOST_KEYS_DIR_ENV: str(selected_directory)}
    selected_directory.mkdir(parents=True)
    if use_default:
        _protect_default_host_key_directories(tmp_path, selected_directory)
    os.link(
        original_directory / "gateway-0.key",
        selected_directory / "gateway-0.key",
    )

    with pytest.raises(ValueError, match="owner-only single-link"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            environment,
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            managed_root=tmp_path / "managed",
        )


def test_private_host_key_rejects_non_current_user_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_directory = tmp_path / "host-keys"
    _private_host_keys(private_directory, ("gateway-0",))
    current_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: current_uid + 1)

    with pytest.raises(ValueError, match="owner-only single-link"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {HOST_KEYS_DIR_ENV: str(private_directory)},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            managed_root=tmp_path / "managed",
        )


@pytest.mark.parametrize(
    ("gateway_name", "error_match"),
    (("", "complete deployment identity"), ("..", "gateway name is not safe")),
)
def test_default_host_keys_directory_rejects_unsafe_gateway_path_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gateway_name: str,
    error_match: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ValueError, match=error_match):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            trust_scope=VMHASSHTrustScope(
                tenant_id="tenant-a",
                project_id="project-a",
                region_id="eu-west1",
                gateway_name=gateway_name,
                cluster_id="cluster-a",
            ),
            allow_managed_repair=True,
            managed_root=tmp_path / "managed",
        )


def test_default_host_keys_never_generate_a_retained_member_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    default_directory = _default_host_key_directory(tmp_path, _trust_scope())

    with pytest.raises(ValueError, match="retained member gateway-0"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            enrollment_hosts=(),
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    assert default_directory.is_dir()
    assert not tuple(default_directory.glob("*.key"))


def test_recreation_with_managed_receipt_never_persists_a_replacement_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    scope = _trust_scope()
    managed_root = tmp_path / "managed"
    original_directory = tmp_path / "original-host-keys"
    _private_host_keys(original_directory, (hostname,))
    creation = require_vm_ha_ssh_policy(
        (hostname,),
        {HOST_KEYS_DIR_ENV: str(original_directory)},
        enrollment_hosts=(hostname,),
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    publish_vm_ha_ssh_trust(creation)
    (original_directory / f"{hostname}.key").unlink()
    default_private_key = _default_host_key_directory(tmp_path, scope) / f"{hostname}.key"

    with pytest.raises(VMHAReplacementSSHIdentityUnavailable) as captured:
        require_vm_ha_ssh_policy(
            (hostname,),
            {},
            enrollment_hosts=(hostname,),
            trust_scope=scope,
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=managed_root,
        )

    assert not default_private_key.exists()
    assert f"replacement or recreation member {hostname}" in str(captured.value)


def test_recreation_with_explicit_pin_never_persists_a_replacement_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    scope = _trust_scope()
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    pins = paramiko.HostKeys()
    pins.add(hostname, key.get_name(), key)
    pins.save(str(known_hosts))
    default_private_key = _default_host_key_directory(tmp_path, scope) / f"{hostname}.key"

    with pytest.raises(VMHAReplacementSSHIdentityUnavailable) as captured:
        require_vm_ha_ssh_policy(
            (hostname,),
            {KNOWN_HOSTS_ENV: str(known_hosts)},
            enrollment_hosts=(hostname,),
            trust_scope=scope,
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    assert not default_private_key.exists()
    assert f"replacement or recreation member {hostname}" in str(captured.value)
    assert captured.value.rotation_intent is None


@pytest.mark.parametrize("predecessor", ("missing", "non-ed25519"))
def test_checkpointed_rotation_classifies_managed_predecessor_unavailable(
    tmp_path: Path,
    predecessor: str,
) -> None:
    hostname = "gateway-0"
    scope = _trust_scope()
    managed_root = tmp_path / "managed"
    if predecessor == "non-ed25519":
        paths = ssh_policy_module._managed_trust_paths(scope, managed_root)
        paths.product_directory.mkdir(mode=0o700)
        paths.scope_directory.mkdir(mode=0o700)
        key = paramiko.RSAKey.generate(1024)
        paths.projection.write_text(
            f"{hostname} {key.get_name()} {key.get_base64()}\n",
            encoding="utf-8",
        )
        paths.projection.chmod(0o600)

    with pytest.raises(VMHAReplacementSSHIdentityUnavailable) as captured:
        require_vm_ha_ssh_policy(
            (hostname,),
            {},
            enrollment_hosts=(hostname,),
            trust_scope=scope,
            allow_managed_repair=True,
            rotate_identity_hosts=(hostname,),
            managed_root=managed_root,
        )

    assert (
        captured.value.problem is VMHAReplacementSSHIdentityProblem.MANAGED_PREDECESSOR_UNAVAILABLE
    )


def test_approved_missing_non_owner_rotation_is_write_free_then_retry_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = _trust_scope()
    hostnames = ("gateway-0", "gateway-1")
    managed_root = tmp_path / "managed"
    creation = require_vm_ha_ssh_policy(
        hostnames,
        {},
        enrollment_hosts=hostnames,
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        managed_root=managed_root,
    )
    publish_vm_ha_ssh_trust(creation)
    directory = _default_host_key_directory(tmp_path, scope)
    target_key = directory / "gateway-1.key"
    original_private = target_key.read_bytes()
    target_key.unlink()
    receipt = managed_root / scope.digest / "trust.json"
    projection = managed_root / scope.digest / "known_hosts"
    predecessor = (receipt.read_bytes(), projection.read_bytes())

    with pytest.raises(VMHAReplacementSSHIdentityUnavailable) as captured:
        require_vm_ha_ssh_policy(
            hostnames,
            {},
            enrollment_hosts=("gateway-1",),
            retained_hosts=("gateway-0",),
            trust_scope=scope,
            allow_managed_repair=True,
            managed_root=managed_root,
        )

    intent = captured.value.rotation_intent
    assert intent is not None
    with pytest.raises(ValueError, match="rotation intent is invalid"):
        ssh_policy_module._rotation_key_paths(
            scope,
            replace(intent, hostname="../escaped"),
            "d" * 64,
        )
    preview = require_vm_ha_ssh_policy(
        hostnames,
        {},
        enrollment_hosts=("gateway-1",),
        retained_hosts=("gateway-0",),
        trust_scope=scope,
        allow_managed_repair=True,
        rotate_identity_hosts=("gateway-1",),
        managed_root=managed_root,
    )
    assert preview.managed_action == "rotate"
    assert not target_key.exists()
    assert not tuple(directory.glob(".gateway-1.replacement-*.key"))
    assert (receipt.read_bytes(), projection.read_bytes()) == predecessor

    operator_directory = tmp_path / "operator-host-keys"
    operator_directory.mkdir()
    with pytest.raises(
        VMHAReplacementSSHIdentityUnavailable,
        match="requires product-managed trust",
    ) as directory_conflict:
        require_vm_ha_ssh_policy(
            hostnames,
            {HOST_KEYS_DIR_ENV: str(operator_directory)},
            enrollment_hosts=("gateway-1",),
            retained_hosts=("gateway-0",),
            trust_scope=scope,
            allow_managed_repair=True,
            rotate_identity_hosts=("gateway-1",),
            managed_root=managed_root,
        )
    assert (
        directory_conflict.value.problem
        is VMHAReplacementSSHIdentityProblem.OPERATOR_SOURCE_CONFLICT
    )
    assert not tuple(operator_directory.iterdir())

    operator_known_hosts = tmp_path / "operator-known-hosts"
    operator_known_hosts.write_bytes(predecessor[1])
    with pytest.raises(
        VMHAReplacementSSHIdentityUnavailable,
        match="requires product-managed trust",
    ) as known_hosts_conflict:
        require_vm_ha_ssh_policy(
            hostnames,
            {KNOWN_HOSTS_ENV: str(operator_known_hosts)},
            enrollment_hosts=("gateway-1",),
            retained_hosts=("gateway-0",),
            trust_scope=scope,
            allow_managed_repair=True,
            rotate_identity_hosts=("gateway-1",),
            managed_root=managed_root,
        )
    assert (
        known_hosts_conflict.value.problem
        is VMHAReplacementSSHIdentityProblem.OPERATOR_SOURCE_CONFLICT
    )

    operation_id = "d" * 64
    projection.write_bytes(predecessor[1] + b"# changed after approval\n")
    with pytest.raises(RuntimeError, match="changed after rotation approval"):
        validate_vm_ha_ssh_identity_rotation(
            intent,
            trust_scope=scope,
            managed_root=managed_root,
        )
    projection.write_bytes(predecessor[1])
    validate_vm_ha_ssh_identity_rotation(
        intent,
        trust_scope=scope,
        managed_root=managed_root,
    )
    stage = prepare_vm_ha_ssh_identity_rotation(
        intent,
        operation_id=operation_id,
        trust_scope=scope,
        hosts=hostnames,
        managed_root=managed_root,
    )
    resumed = prepare_vm_ha_ssh_identity_rotation(
        intent,
        operation_id=operation_id,
        trust_scope=scope,
        hosts=hostnames,
        expected_new_fingerprint=stage.new_fingerprint,
        expected_successor_receipt_sha256=stage.successor_receipt_sha256,
        expected_successor_projection_sha256=stage.successor_projection_sha256,
        managed_root=managed_root,
    )
    assert resumed.new_fingerprint == stage.new_fingerprint
    assert resumed._private_key == stage._private_key

    assert publish_vm_ha_ssh_identity_rotation(
        intent,
        resumed,
        operation_id=operation_id,
        trust_scope=scope,
        managed_root=managed_root,
    ) == (
        stage.successor_receipt_sha256,
        stage.successor_projection_sha256,
    )
    final = require_vm_ha_ssh_policy(
        hostnames,
        {},
        enrollment_hosts=("gateway-1",),
        retained_hosts=("gateway-0",),
        trust_scope=scope,
        allow_managed_repair=True,
        managed_root=managed_root,
    )
    assert final.identity_for("gateway-1").private_key == target_key.read_bytes()
    assert target_key.read_bytes() != original_private
    assert stat_mode(target_key) == 0o600
    assert "PRIVATE" not in receipt.read_text(encoding="utf-8")


def test_default_host_key_generation_rejects_a_symlinked_product_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (ssh_directory / "nebius-vpngw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink directory"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    assert not tuple(outside.iterdir())


def test_default_host_key_generation_never_overwrites_an_invalid_existing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    default_directory = _default_host_key_directory(tmp_path, _trust_scope())
    default_directory.mkdir(parents=True)
    _protect_default_host_key_directories(tmp_path, default_directory)
    private_key = default_directory / "gateway-0.key"
    original = b"not-a-private-key\n"
    private_key.write_bytes(original)
    private_key.chmod(0o600)

    with pytest.raises(ValueError, match="malformed, encrypted, or unusable"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    assert private_key.read_bytes() == original
    assert private_key.stat().st_nlink == 1


def test_explicit_host_key_directory_is_never_populated(
    tmp_path: Path,
) -> None:
    explicit_directory = tmp_path / "explicit-host-keys"
    explicit_directory.mkdir()

    with pytest.raises(ValueError, match="fresh member gateway-0 is unavailable"):
        require_vm_ha_ssh_policy(
            ("gateway-0",),
            {HOST_KEYS_DIR_ENV: str(explicit_directory)},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    assert not tuple(explicit_directory.iterdir())


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


@pytest.mark.parametrize("hashed", (False, True))
def test_apply_imports_exact_retained_ed25519_pin_from_default_known_hosts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hashed: bool,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir(mode=0o700)
    hostname = "gateway-0"
    address = "203.0.113.10"
    _, key_type, key_data = ssh_policy_module._generate_ed25519_host_key(hostname)
    host_field = paramiko.HostKeys.hash_host(address) if hashed else address
    known_hosts = ssh_directory / "known_hosts"
    original = f"unrelated malformed\n{host_field} {key_type} {key_data}\n"
    known_hosts.write_text(original, encoding="utf-8")
    known_hosts.chmod(0o600)
    checked: list[str] = []

    policy = require_vm_ha_ssh_policy(
        ((hostname, address),),
        {},
        enrollment_hosts=(),
        retained_hosts=(hostname,),
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        allow_default_known_hosts_import=True,
        default_known_hosts_bindings={hostname: lambda: checked.append(hostname)},
        host_identity_recovery=lambda _hosts: pytest.fail("cloud recovery was not expected"),
        managed_root=tmp_path / "managed",
    )

    assert policy.managed_action == "migrate"
    assert key_data in policy.known_hosts_file.read_text(encoding="utf-8")
    assert publish_vm_ha_ssh_trust(policy) is True
    assert checked == [hostname, hostname]
    assert known_hosts.read_text(encoding="utf-8") == original


def test_default_known_hosts_import_rechecks_immutable_source_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir(mode=0o700)
    hostname = "gateway-0"
    _, key_type, key_data = ssh_policy_module._generate_ed25519_host_key(hostname)
    known_hosts = ssh_directory / "known_hosts"
    known_hosts.write_text(f"{hostname} {key_type} {key_data}\n", encoding="utf-8")
    known_hosts.chmod(0o600)
    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {},
        enrollment_hosts=(),
        retained_hosts=(hostname,),
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        allow_default_known_hosts_import=True,
        default_known_hosts_bindings={hostname: lambda: None},
        managed_root=tmp_path / "managed",
    )

    known_hosts.write_text("changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="default known-hosts evidence changed"):
        publish_vm_ha_ssh_trust(policy)
    assert not (tmp_path / "managed" / _trust_scope().digest / "trust.json").exists()


def test_authenticated_cloud_recovery_can_exclude_a_stale_default_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir(mode=0o700)
    hostname = "gateway-0"
    address = "203.0.113.10"
    _, stale_type, stale_data = ssh_policy_module._generate_ed25519_host_key(hostname)
    recovered_private, recovered_type, recovered_data = (
        ssh_policy_module._generate_ed25519_host_key(hostname)
    )
    known_hosts = ssh_directory / "known_hosts"
    known_hosts.write_text(
        f"{address} {stale_type} {stale_data}\n",
        encoding="utf-8",
    )
    known_hosts.chmod(0o600)
    checked: list[str] = []

    policy = require_vm_ha_ssh_policy(
        ((hostname, address),),
        {},
        enrollment_hosts=(),
        retained_hosts=(hostname,),
        trust_scope=_trust_scope(),
        allow_managed_repair=True,
        persist_default_host_keys=True,
        allow_default_known_hosts_import=True,
        default_known_hosts_bindings={hostname: lambda: checked.append("compute")},
        default_known_hosts_import_hosts=(),
        host_identity_recovery=lambda hosts: {
            name: SSHHostKeyRecovery(
                hostname=name,
                private_key=recovered_private,
                assert_current=lambda: checked.append("cloud"),
            )
            for name in hosts
        },
        managed_root=tmp_path / "managed",
    )

    projection = policy.known_hosts_file.read_text(encoding="utf-8")
    assert recovered_type == stale_type == "ssh-ed25519"
    assert recovered_data in projection
    assert stale_data not in projection
    policy.assert_current()
    assert checked == ["compute", "cloud"]


def test_retained_compute_binding_is_rechecked_for_explicit_trust(
    tmp_path: Path,
) -> None:
    hostname = "gateway-0"
    _, key_type, key_data = ssh_policy_module._generate_ed25519_host_key(hostname)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"{hostname} {key_type} {key_data}\n", encoding="utf-8")
    known_hosts.chmod(0o600)
    checked: list[str] = []

    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {KNOWN_HOSTS_ENV: str(known_hosts)},
        enrollment_hosts=(),
        retained_hosts=(hostname,),
        default_known_hosts_bindings={hostname: lambda: checked.append(hostname)},
    )

    policy.assert_current()

    assert checked == [hostname]


def test_apply_recovers_retained_product_host_key_only_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hostname = "gateway-0"
    scope = _trust_scope()
    private_key, _, key_data = ssh_policy_module._generate_ed25519_host_key(hostname)
    checked: list[str] = []

    policy = require_vm_ha_ssh_policy(
        (hostname,),
        {},
        enrollment_hosts=(),
        retained_hosts=(hostname,),
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
        managed_root=tmp_path / "managed",
        host_identity_recovery=lambda hosts: {
            name: SSHHostKeyRecovery(
                hostname=name,
                private_key=private_key,
                assert_current=lambda member=name: checked.append(member),
            )
            for name in hosts
        },
    )
    recovered_path = _default_host_key_directory(tmp_path, scope) / f"{hostname}.key"

    assert not recovered_path.exists()
    assert key_data in policy.known_hosts_file.read_text(encoding="utf-8")
    assert publish_vm_ha_ssh_trust(policy) is True
    assert recovered_path.read_bytes() == private_key
    assert checked == [hostname]


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


def test_fresh_managed_trust_dry_run_uses_ephemeral_identity_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KNOWN_HOSTS_ENV, raising=False)
    monkeypatch.delenv(HOST_KEYS_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    policy = require_vm_ha_ssh_policy(
        (("gateway-0", "203.0.113.10"),),
        enrollment_hosts={"gateway-0"},
        trust_scope=_trust_scope(cluster_id="ordinary-v1"),
        allow_managed_repair=True,
        persist_default_host_keys=False,
    )

    assert policy.managed_action == "create"
    assert policy.managed_receipt_sha256 is not None
    assert not (tmp_path / ".ssh").exists()


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
