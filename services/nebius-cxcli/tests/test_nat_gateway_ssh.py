from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_deploy as deployment
from nebius_cxcli import nat_gateway_ssh as auth
from nebius_cxcli.nat_gateway import nat_module_inputs, validate_nat_config
from nebius_cxcli.runtime_validation import validate_dynamic_payload_structure
from test_nat_gateway import config


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    keys = []
    for name in ("expected", "other"):
        path = tmp_path / name
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
            check=True,
            capture_output=True,
        )
        keys.append((path, path.with_suffix(".pub").read_text().strip()))
    return keys


def test_explicit_identity_is_exact_and_disables_agent(identities):
    (path, public), _ = identities
    with auth.ssh_identity_options(public, path) as options:
        assert options[options.index("-i") + 1] == str(path)
        for option in (
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "PreferredAuthentications=publickey",
            "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no",
        ):
            assert option in options
        assert options[:2] == ["-F", "/dev/null"]


@pytest.mark.parametrize("problem", ["mismatch", "permissions", "symlink", "hardlink"])
def test_explicit_private_key_rejected_before_network(identities, tmp_path, problem):
    (path, public), (other, _) = identities
    if problem == "mismatch":
        path = other
    elif problem == "permissions":
        path.chmod(0o644)
    elif problem == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    else:
        os.link(path, tmp_path / "hardlink")
    with pytest.raises(RuntimeError, match="matching"), auth.ssh_identity_options(public, path):
        pytest.fail("Rejected key was admitted")


def test_agent_selects_only_configured_key_and_cleans_selector(identities, monkeypatch):
    (_, public), (_, other) = identities
    monkeypatch.setattr(
        auth,
        "_output",
        lambda args: (
            "\n".join([other, public])
            if args == ["ssh-add", "-L"]
            else pytest.fail("Unexpected key probe")
        ),
    )
    with auth.ssh_identity_options(public) as options:
        selector = Path(options[options.index("-i") + 1])
        assert selector.read_text().strip() == " ".join(public.split()[:2])
        assert selector.stat().st_mode & 0o777 == 0o600
        assert "IdentitiesOnly=yes" in options and "IdentityAgent=none" not in options
    assert not selector.exists()


def test_missing_or_duplicate_identity_fails_locally(identities, monkeypatch):
    (_, public), _ = identities
    monkeypatch.setattr(auth, "_output", lambda args: None)
    with pytest.raises(RuntimeError, match="no SSH connection"), auth.ssh_identity_options(public):
        pytest.fail("Missing key admitted")
    monkeypatch.setattr(auth, "_output", lambda args: public + "\n" + public)
    with pytest.raises(RuntimeError, match="ambiguous"), auth.ssh_identity_options(public):
        pytest.fail("Ambiguous key admitted")


def test_standard_key_matches_configured_identity(identities, tmp_path, monkeypatch):
    (path, public), _ = identities
    folder = tmp_path / ".ssh"
    folder.mkdir()
    selected = folder / "id_ed25519"
    shutil.copy2(path, selected)
    original = auth._output
    monkeypatch.setattr(
        auth, "_output", lambda args: None if args == ["ssh-add", "-L"] else original(args)
    )
    with auth.ssh_identity_options(public) as options:
        assert options[options.index("-i") + 1] == str(selected)
    shutil.copy2(path, folder / "id_rsa")
    with pytest.raises(RuntimeError, match="one usable"), auth.ssh_identity_options(public):
        pytest.fail("Ambiguous local identities admitted")


@pytest.mark.parametrize("legacy", [[], ["192.0.2.0/24"], ["0.0.0.0/0"]])
def test_obsolete_admin_policy_rejected_with_upgrade_guidance(legacy):
    payload = config()
    payload["infra"]["components"][0]["inputs"]["admin_cidrs"] = legacy
    with pytest.raises(ValueError, match="previous matching CLI/module"):
        validate_dynamic_payload_structure(payload)


def test_new_config_keeps_forwarding_scoped_without_admin_field():
    payload = config()
    validate_nat_config(payload)
    inputs = nat_module_inputs(payload["infra"]["components"][0])
    assert "admin_cidrs" not in inputs
    payload["infra"]["components"][0]["inputs"]["source_cidrs"] = ["0.0.0.0/0"]
    with pytest.raises(ValueError, match="Unrestricted"):
        validate_nat_config(payload)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "schema",
        "config_sha256",
        "rules_valid",
        "ssh_policy_valid",
        "ssh_listener_ready",
        "ssh_ban_service_ready",
    ],
)
def test_guest_readiness_requires_effective_security(identities, tmp_path, monkeypatch, bad):
    from contextlib import contextmanager

    @contextmanager
    def selected(*args):
        yield ["-o", "IdentitiesOnly=yes"]

    monkeypatch.setattr(deployment, "ssh_identity_options", selected)
    report = {
        "schema": 2,
        "config_sha256": "expected",
        "forwarding": True,
        "rules_valid": True,
        "ssh_policy_valid": True,
        "ssh_listener_ready": True,
        "ssh_ban_service_ready": True,
    }
    if bad:
        report[bad] = 1 if bad == "schema" else "wrong" if bad == "config_sha256" else False
    commands = []
    monkeypatch.setattr(
        deployment.subprocess,
        "run",
        lambda args, **kw: commands.append(args) or NS(returncode=0, stdout=json.dumps(report)),
    )
    trust = tmp_path / "known_hosts"
    trust.write_text("fixture")
    inputs = {"ssh_user_name": "natadmin", "ssh_public_key": identities[0][1]}
    outputs = {"public_ip": "192.0.2.1/32", "guest_config_sha256": "expected"}
    if bad:
        with pytest.raises(RuntimeError, match="SSH security"):
            deployment.verify_guest(outputs, inputs, known_hosts=trust)
    else:
        assert deployment.verify_guest(outputs, inputs, known_hosts=trust) == report
    assert "IdentitiesOnly=yes" in commands[0]
    assert "StrictHostKeyChecking=yes" in commands[0]
