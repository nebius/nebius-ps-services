"""Exercise trust rejection at the real service boundary, without network access."""

from __future__ import annotations

import subprocess

import pytest

from nebius_cxcli.ssh_jumphost import (
    SshJumphostAllowedCidrRequest,
    SshJumphostComponent,
    update_ssh_jumphost_allowed_cidrs,
)
from nebius_cxcli.wireguard_clients import (
    WireGuardClientGenerationRequest,
    WireGuardComponent,
    WireGuardLocalSubnetUpdateRequest,
    generate_wireguard_client_config,
    update_wireguard_local_subnets,
)


def _invoke(kind, trust, output, runner):
    common = dict(
        public_ip="203.0.113.10",
        ssh_user="ubuntu",
        ssh_private_key=output.parent / "login-key" if kind != "jumphost" else None,
        ssh_known_hosts_file=trust,
    )
    wg = WireGuardComponent("wireguard-gw", "vpn", {})
    if kind == "generate":
        return generate_wireguard_client_config(
            WireGuardClientGenerationRequest(
                component=wg,
                client_name="laptop",
                local_subnets=(),
                dns=(),
                persistent_keepalive=None,
                output_dir=output,
                **common,
            ),
            run_command=runner,
        )
    if kind == "subnets":
        return update_wireguard_local_subnets(
            WireGuardLocalSubnetUpdateRequest(
                component=wg,
                operation="add",
                local_subnets=("10.0.0.0/8",),
                **common,
            ),
            run_command=runner,
        )
    return update_ssh_jumphost_allowed_cidrs(
        SshJumphostAllowedCidrRequest(
            component=SshJumphostComponent("ssh-jumphost", "bastion", {}),
            operation="list",
            allowed_cidrs=(),
            **common,
        ),
        run_command=runner,
    )


@pytest.mark.parametrize("kind", ["generate", "subnets", "jumphost"])
@pytest.mark.parametrize(
    "contents",
    ["", "# no pins\n", "other-host ssh-ed25519 invalid\n", "203.0.113.10 ssh-ed25519 invalid\n"],
)
def test_missing_or_invalid_target_trust_never_reaches_ssh(tmp_path, kind, contents):
    trust = tmp_path / "known_hosts"
    trust.write_text(contents)
    output = tmp_path / "clients"

    def forbidden(*_args, **_kwargs):
        pytest.fail("SSH must not run without a usable target pin")

    with pytest.raises(RuntimeError, match="SSH.*(host|trust)"):
        _invoke(kind, trust, output, forbidden)
    assert not output.exists()
    assert trust.read_text() == contents


@pytest.mark.parametrize("kind", ["generate", "subnets", "jumphost"])
def test_remote_failures_do_not_disclose_output(tmp_path, trusted_gateway_hosts, kind):
    secret = "private-material-must-never-be-disclosed"
    calls = []

    def fail(cmd, **kwargs):
        calls.append(cmd)
        raise subprocess.CalledProcessError(255, cmd, output=secret, stderr=secret)

    with pytest.raises(RuntimeError) as caught:
        _invoke(kind, trusted_gateway_hosts, tmp_path / "clients", fail)
    assert len(calls) == 1
    assert secret not in str(caught.value)
    assert caught.value.__suppress_context__
    assert caught.value.__cause__ is None


def test_hashed_host_pin_is_recognized(trusted_gateway_hosts):
    from nebius_cxcli.ssh_trust import ssh_host_trust_options

    subprocess.run(
        ["ssh-keygen", "-H", "-f", str(trusted_gateway_hosts)], check=True, capture_output=True
    )
    assert "HostKeyAlias=203.0.113.10" in ssh_host_trust_options(
        trusted_gateway_hosts, "203.0.113.10"
    )


@pytest.mark.integration
def test_real_openssh_trust_options_override_ambient_trust(tmp_path, trusted_gateway_hosts):
    from nebius_cxcli.ssh_trust import ssh_host_trust_options

    trust = tmp_path / "trust %n with spaces"
    trust.write_bytes(trusted_gateway_hosts.read_bytes())
    config = tmp_path / "ssh_config"
    config.write_text(
        "Host *\n  StrictHostKeyChecking no\n  KnownHostsCommand /untrusted\n  VerifyHostKeyDNS yes\n  UpdateHostKeys yes\n  ControlPath /untrusted-socket\n  HostKeyAlias other\n"
    )
    result = subprocess.run(
        [
            "ssh",
            "-G",
            "-F",
            str(config),
            *ssh_host_trust_options(trust, "203.0.113.10"),
            "ubuntu@203.0.113.10",
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    lines = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    assert lines["stricthostkeychecking"] == "true"
    assert lines.get("knownhostscommand", "none") == "none"
    assert lines["verifyhostkeydns"] == "false"
    assert lines["updatehostkeys"] == "false"
    assert lines.get("controlpath", "none") == "none"
    assert lines["hostkeyalias"] == "203.0.113.10"
    assert lines["userknownhostsfile"] == str(trust)


@pytest.mark.parametrize("mode", [0o622, 0o646, 0o666])
def test_other_user_writable_trust_is_rejected(trusted_gateway_hosts, mode):
    from nebius_cxcli.ssh_trust import ssh_host_trust_options

    trusted_gateway_hosts.chmod(mode)
    with pytest.raises(RuntimeError, match="unsafe ownership or write"):
        ssh_host_trust_options(trusted_gateway_hosts, "203.0.113.10")
