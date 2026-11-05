"""Execute generated FRR shell with inert commands, never the host bootstrap."""

from __future__ import annotations

import base64
import gzip
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nebius_vpngw import ingress_policy
from nebius_vpngw.deploy.ordinary_apply import UFW_ORDERING_PATH
from nebius_vpngw.deploy.ssh_policy import VM_HA_SSH_HOST_KEY_PATH, SSHHostIdentity
from nebius_vpngw.deploy.vm_ha_identity import recover_product_host_key
from nebius_vpngw.deploy.vm_manager import (
    VMManager,
    _read_esp4_preflight_script,
    _read_firewall_setup_script,
)


@pytest.mark.parametrize("vm_ha", [False, True])
def test_bootstrap_ingress_survives_pinned_host_key_enrollment(vm_ha):
    policy = {"peer_ips": []}
    manager = VMManager(project_id="test", region="eu-west1")
    base = manager._build_cloud_init(ssh_key="ssh-ed25519 AAAAtest", vm_ha=vm_ha, ingress=policy)
    identity = SSHHostIdentity("test-gateway", b"synthetic-test-key\n")
    rendered = manager._render_gateway_ssh_enrollment_cloud_init(base, identity, None)
    document = yaml.safe_load(rendered)
    files = {item["path"]: item for item in document["write_files"]}
    ufw = files[UFW_ORDERING_PATH]
    canonical = Path(ingress_policy.__file__).parent / "systemd/nebius-vpngw-ufw-ordering.conf"
    assert ufw["content"].encode() == canonical.read_bytes()
    assert ufw["owner"] == "root:root" and ufw["permissions"] == "0644"
    assert "network-online" not in ufw["content"] and "cloud-init" not in ufw["content"]
    instance = SimpleNamespace(spec=SimpleNamespace(cloud_init_user_data=rendered))
    assert recover_product_host_key(instance, path=VM_HA_SSH_HOST_KEY_PATH) == identity.private_key
    script = files[str(ingress_policy.EXECUTABLE)]
    assert script["encoding"] == "gz+b64"
    assert (
        gzip.decompress(base64.b64decode(script["content"]))
        == Path(ingress_policy.__file__).read_bytes()
    )
    assert script["owner"] == "root:root" and script["permissions"] == "0644"
    for path, expected in (
        ("/usr/local/bin/setup-vpngw-firewall.sh", _read_firewall_setup_script()),
        ("/usr/local/bin/nebius-vpngw-esp4-preflight.sh", _read_esp4_preflight_script()),
    ):
        script = files[path]
        assert script["encoding"] == "gz+b64"
        assert script["owner"] == "root:root" and script["permissions"] == "0755"
        assert gzip.decompress(base64.b64decode(script["content"])) == (
            expected.rstrip() + "\n"
        ).encode("utf-8")
    assert len(rendered.encode("utf-8")) <= 32768
    assert (
        manager._build_cloud_init(ssh_key="ssh-ed25519 AAAAtest", vm_ha=vm_ha, ingress=policy)
        == base
    )
    assert files[str(ingress_policy.POLICY_FILE)]["content"] == ingress_policy.serialized(policy)
    assert "nftables" in document["packages"]
    assert document["runcmd"][1:3] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "nebius-vpngw-ingress.service"],
    ]


@pytest.mark.parametrize("extra_bytes", [0, 1])
@pytest.mark.parametrize("padding", ["x", "é"])
def test_enrollment_validates_final_cloud_init_utf8_size(extra_bytes, padding):
    base = "#cloud-config\nwrite_files:\n  - content: |\n            Port 22\n"
    identity = SimpleNamespace(cloud_init_entries=lambda: "")
    rendered = VMManager._render_gateway_ssh_enrollment_cloud_init(base, identity)
    remaining = 32768 - len(rendered.encode("utf-8")) + extra_bytes
    width = len(padding.encode("utf-8"))
    padded = base + padding * (remaining // width) + "x" * (remaining % width)
    if extra_bytes:
        with pytest.raises(ValueError, match="cloud-init.*32768"):
            VMManager._render_gateway_ssh_enrollment_cloud_init(padded, identity)
    else:
        assert (
            len(
                VMManager._render_gateway_ssh_enrollment_cloud_init(padded, identity).encode(
                    "utf-8"
                )
            )
            == 32768
        )


@pytest.mark.parametrize("vm_ha", [False, True])
@pytest.mark.parametrize("comment", ['operator: "primary"', "secondary # literal key comment"])
def test_cloud_init_preserves_authorized_key_as_a_yaml_string(vm_ha: bool, comment: str) -> None:
    key = f"ssh-ed25519 AAAAfirst {comment}"
    config = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(
            ingress={"peer_ips": []},
            ssh_key=key,
            vm_ha=vm_ha,
        )
    )
    assert config["users"] == [{"name": "ubuntu", "ssh_authorized_keys": [key]}]


@pytest.mark.parametrize("failure", ["", "curl", "lsb_release", "apt-get", "install"])
def test_generated_frr_shell_stops_on_required_failure(tmp_path: Path, failure: str) -> None:
    config = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(
            ingress={"peer_ips": []}, ssh_key="test"
        )
    )
    files = {item["path"]: item for item in config["write_files"]}
    assert "/etc/frr/daemons" not in files
    assert files["/etc/nebius-vpngw/frr-daemons.bootstrap"]["owner"] == "root:root"
    commands = config["runcmd"]
    assert commands[0] == "set -eu"
    start = next(
        i for i, item in enumerate(commands) if isinstance(item, list) and item[0] == "curl"
    )
    end = next(
        i for i, item in enumerate(commands) if isinstance(item, list) and item[0] == "install"
    )
    assert commands[end][1:7] == ["-o", "frr", "-g", "frr", "-m", "0644"]
    assert commands.index(["systemctl", "mask", "frr.service"]) < start
    assert commands.index(["systemctl", "unmask", "frr.service"]) > end
    # Match cloud-init util.shellify: strings are shell, lists are individually
    # quoted argv. Only execute the FRR block; redirect all paths into tmp_path.
    shell = "#!/bin/sh\nset -eu\n" + "\n".join(
        shlex.join(item) for item in commands[start : end + 1]
    )
    shell = shell.replace("/etc/", str(tmp_path / "etc") + "/")
    shell += '\nprintf complete >> "$TRACE"\n'
    (tmp_path / "etc/apt/sources.list.d").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("curl", "lsb_release", "apt-get", "install"):
        stub = bin_dir / name
        stub.write_text(
            f'#!/bin/sh\nprintf "%s\\n" {name} >> "$TRACE"\n'
            f'[ "$FAIL" != {name} ] || exit 42\n'
            + ("echo noble\n" if name == "lsb_release" else "exit 0\n")
        )
        stub.chmod(0o700)
    trace = tmp_path / "trace"
    result = subprocess.run(
        ["/bin/sh", "-c", shell],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAIL": failure,
            "TRACE": str(trace),
        },
    )
    calls = trace.read_text().splitlines()
    assert (result.returncode == 0) is (failure == "")
    assert ("complete" in calls) is (failure == "")
    if failure:
        assert calls[-1] == failure


def test_required_firewall_is_not_masked_and_esp4_deferral_is_preserved() -> None:
    config = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(
            ingress={"peer_ips": []}, ssh_key="test"
        )
    )
    commands = config["runcmd"]
    firewall = next(item[-1] for item in commands if "setup-vpngw-firewall.sh >" in str(item))
    assert "|| true" not in firewall
    assert any("rc -eq 75" in str(item) for item in commands)
    assert any("augenrules --load || true" in str(item) for item in commands)


def test_firewall_uses_xfrm_name_without_parent_suffix() -> None:
    path = Path(__file__).parents[2] / "src/nebius_vpngw/systemd/setup-vpngw-firewall.sh"
    source = path.read_text()
    start = source.index("for xfrm_if in ")
    loop = source[start : source.index("\ndone", start) + len("\ndone")]
    script = (
        """
ip() { printf 'link fixture\\n'; }
grep() { cat >/dev/null; printf 'xfrm0@eth0\\nxfrm1@eth0\\n'; }
ufw() { printf '%s\\n' "$*"; }
logger() { :; }
"""
        + loop
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [
        "allow in on xfrm0",
        "allow out on xfrm0",
        "allow in on xfrm1",
        "allow out on xfrm1",
    ]


def test_firewall_retires_only_withdrawn_product_prefix_rules(tmp_path, monkeypatch):
    source = (
        Path(__file__).parents[2] / "src/nebius_vpngw/systemd/setup-vpngw-firewall.sh"
    ).read_text()
    start = source.index("import ipaddress\n")
    code = source[start : source.index("\nPY\n", start)]
    prefixes = tmp_path / "prefixes"
    prefixes.write_text("10.1.0.0/16\n")
    code = code.replace("/etc/vpngw_local_prefixes", str(prefixes))
    deleted = []

    def run(argv, **kwargs):
        if argv == ["ufw", "show", "added"]:
            return SimpleNamespace(
                stdout="\n".join(
                    [
                        "Added user rules (see 'ufw status' for running firewall):",
                        "ufw allow from 10.1.0.0/16 comment 'Local VPC subnet'",
                        "ufw allow from 10.2.0.0/16 comment 'Local VPC subnet'",
                        "ufw allow from 10.3.0.0/16 comment 'Customer access'",
                        "ufw deny from 10.4.0.0/16 comment 'Local VPC subnet'",
                        "ufw allow from 10.5.0.0/16 to any port 443 comment 'Local VPC subnet'",
                    ]
                )
            )
        deleted.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    exec(compile(code, "<firewall-prefix-reconciliation>", "exec"), {})
    assert deleted == [
        ["ufw", "--force", "delete", "allow", "from", "10.2.0.0/16", "comment", "Local VPC subnet"]
    ]
