from __future__ import annotations

import copy

import pytest

from nebius_cxcli import cli, soperator_login_keys
from nebius_cxcli.soperator_values import explicit_values, seed_soperator_values
from test_ssh_public_keys import _VALID_ED25519_PUBLIC_KEY, _VALID_RSA_PUBLIC_KEY


def _payload():
    return {"apps": {"charts": [{"id": "soperator", "enabled": True, "values": {}}]}}


def test_headless_selects_preferred_public_file_and_persists_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa.pub").write_text(_VALID_RSA_PUBLIC_KEY)
    (ssh / "id_ed25519.pub").write_text(_VALID_ED25519_PUBLIC_KEY)
    (ssh / "broken.pub").write_text("not a public key")
    payload = _payload()
    soperator_login_keys.select_headless_root_keys(payload)
    row = payload["apps"]["charts"][0]
    assert explicit_values(row) == {
        "slurmNodes": {"login": {"sshRootPublicKeys": [_VALID_ED25519_PUBLIC_KEY]}}
    }
    before = copy.deepcopy(payload)
    (ssh / "id_ed25519.pub").unlink()
    soperator_login_keys.select_headless_root_keys(payload)
    assert payload == before


@pytest.mark.parametrize("keys", [[], [_VALID_RSA_PUBLIC_KEY, _VALID_ED25519_PUBLIC_KEY]])
def test_explicit_key_lists_do_not_discover_or_inherit_other_keys(keys, monkeypatch):
    payload = _payload()
    seed_soperator_values(payload, {"slurmNodes": {"login": {"sshRootPublicKeys": keys}}})
    monkeypatch.setattr(
        soperator_login_keys,
        "discover_ssh_public_key_files",
        lambda: pytest.fail("explicit keys must not trigger discovery"),
    )
    before = copy.deepcopy(payload)
    soperator_login_keys.select_headless_root_keys(payload)
    assert payload == before


def test_headless_without_key_fails_without_setting_a_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    payload = _payload()
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="No supported local SSH public key"):
        soperator_login_keys.select_headless_root_keys(payload)
    assert payload == before


def test_fresh_wizard_enter_selects_preferred_local_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519.pub").write_text(_VALID_ED25519_PUBLIC_KEY)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **kwargs: kwargs["default"])
    value, stopped = cli._prompt_scalar_override(
        "apps.charts[0].values.slurmNodes.login.sshRootPublicKeys",
        None,
        type_hint="list(string)",
    )
    assert not stopped and value == [_VALID_ED25519_PUBLIC_KEY]


@pytest.mark.parametrize("keys", [[], [_VALID_RSA_PUBLIC_KEY, _VALID_ED25519_PUBLIC_KEY]])
def test_wizard_enter_preserves_all_explicit_keys(keys, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **kwargs: kwargs["default"])
    monkeypatch.setattr(cli, "discover_ssh_public_key_files", lambda: pytest.fail("must keep keys"))
    assert cli._prompt_scalar_override(
        "apps.charts[0].values.slurmNodes.login.sshRootPublicKeys", keys
    ) == (keys, False)


@pytest.mark.parametrize("action", ["replace", "disable", cli._WIZARD_BACKTRACK])
def test_wizard_deliberate_replace_disable_and_back(action, monkeypatch):
    original = [_VALID_RSA_PUBLIC_KEY, _VALID_ED25519_PUBLIC_KEY]
    monkeypatch.setattr(cli, "_prompt_choice_override", lambda **_kwargs: (action, False))
    monkeypatch.setattr(
        cli,
        "_prompt_ssh_public_key_override",
        lambda *_args, **_kwargs: (_VALID_ED25519_PUBLIC_KEY, False),
    )
    value, stopped = cli._prompt_scalar_override(
        "apps.charts[0].values.slurmNodes.login.sshRootPublicKeys", original
    )
    expected = {"replace": [_VALID_ED25519_PUBLIC_KEY], "disable": []}
    assert not stopped
    assert value == expected.get(action, cli._WIZARD_BACKTRACK)
