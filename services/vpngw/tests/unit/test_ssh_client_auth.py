from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest

from nebius_vpngw.deploy.ssh_client_auth import resolve_ssh_client_auth


def _private_key(path: Path) -> paramiko.RSAKey:
    key = paramiko.RSAKey.generate(1024)
    key.write_private_key_file(str(path))
    path.chmod(0o600)
    return key


def _public(key: paramiko.PKey) -> str:
    return f"{key.get_name()} {key.get_base64()} operator@example"


def _agent(*keys: paramiko.PKey) -> SimpleNamespace:
    return SimpleNamespace(Agent=lambda: SimpleNamespace(get_keys=lambda: keys))


def test_explicit_private_key_must_match_configured_public_key(tmp_path: Path) -> None:
    configured = _private_key(tmp_path / "configured")
    wrong = _private_key(tmp_path / "wrong")

    with pytest.raises(ValueError, match="does not match"):
        resolve_ssh_client_auth(
            _public(configured),
            explicit_private_key=tmp_path / "wrong",
            paramiko_module=_agent(),
        )

    auth = resolve_ssh_client_auth(
        _public(configured),
        explicit_private_key=tmp_path / "configured",
        paramiko_module=_agent(),
    )
    assert auth.source == "explicit-private-key"
    assert auth.paramiko_connect_kwargs() == {
        "allow_agent": False,
        "look_for_keys": False,
        "password": None,
        "key_filename": str(tmp_path / "configured"),
    }
    assert wrong is not configured


def test_agent_resolution_selects_only_the_exact_configured_identity(tmp_path: Path) -> None:
    configured = _private_key(tmp_path / "configured")
    unrelated = _private_key(tmp_path / "unrelated")

    auth = resolve_ssh_client_auth(
        _public(configured),
        home=tmp_path,
        paramiko_module=_agent(unrelated, configured),
    )

    kwargs = auth.paramiko_connect_kwargs()
    assert auth.source == "ssh-agent"
    assert kwargs["pkey"] is configured
    assert kwargs["allow_agent"] is False
    assert kwargs["look_for_keys"] is False
    options = auth.openssh_options()
    assert "IdentitiesOnly=yes" in options
    assert "PasswordAuthentication=no" in options
    assert "BatchMode=yes" in options


def test_public_key_comment_may_contain_spaces(tmp_path: Path) -> None:
    configured = _private_key(tmp_path / "configured")

    auth = resolve_ssh_client_auth(
        f"{configured.get_name()} {configured.get_base64()} operations key with spaces",
        explicit_private_key=tmp_path / "configured",
        paramiko_module=_agent(),
    )

    assert auth.source == "explicit-private-key"


def test_default_key_resolution_is_exact_and_disables_fallback(tmp_path: Path) -> None:
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir()
    configured = _private_key(ssh_directory / "id_ed25519")
    _private_key(ssh_directory / "id_rsa")

    auth = resolve_ssh_client_auth(
        _public(configured),
        home=tmp_path,
        paramiko_module=_agent(),
    )

    assert auth.source == "default-private-key"
    assert auth.paramiko_connect_kwargs()["key_filename"] == str(ssh_directory / "id_ed25519")


def test_default_key_resolution_survives_unavailable_agent(tmp_path: Path) -> None:
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir()
    configured = _private_key(ssh_directory / "id_ed25519")

    class BrokenAgent:
        def get_keys(self) -> tuple[()]:
            raise OSError("agent unavailable")

    auth = resolve_ssh_client_auth(
        _public(configured),
        home=tmp_path,
        paramiko_module=SimpleNamespace(Agent=BrokenAgent),
    )

    assert auth.source == "default-private-key"


def test_resolution_rejects_absent_or_insecure_matching_private_key(tmp_path: Path) -> None:
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir()
    configured = _private_key(ssh_directory / "id_ed25519")
    ssh_directory.joinpath("id_ed25519").chmod(0o644)

    with pytest.raises(ValueError, match="No usable SSH client identity"):
        resolve_ssh_client_auth(
            _public(configured),
            home=tmp_path,
            paramiko_module=_agent(),
        )


def test_resolution_never_falls_back_from_mismatched_explicit_key(tmp_path: Path) -> None:
    configured = _private_key(tmp_path / "configured")
    wrong = _private_key(tmp_path / "wrong")

    with pytest.raises(ValueError, match="does not match"):
        resolve_ssh_client_auth(
            _public(configured),
            explicit_private_key=tmp_path / "wrong",
            home=tmp_path,
            paramiko_module=_agent(configured),
        )

    assert wrong is not configured
