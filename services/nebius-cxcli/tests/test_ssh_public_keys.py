from __future__ import annotations

from pathlib import Path

import pytest

from nebius_cxcli.ssh_public_keys import (
    normalize_runtime_ssh_public_key_inputs,
    normalize_ssh_public_key_value,
)

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)
_VALID_RSA_PUBLIC_KEY = (
    "ssh-rsa "
    "AAAAB3NzaC1yc2EAAAADAQABAAAAgAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBA"
    "QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB "
    "demo@example"
)


def test_normalize_ssh_public_key_value_accepts_inline_ed25519() -> None:
    assert (
        normalize_ssh_public_key_value(
            _VALID_ED25519_PUBLIC_KEY,
            field_label="infra.components[0].inputs.ssh_public_key",
        )
        == _VALID_ED25519_PUBLIC_KEY
    )


def test_normalize_ssh_public_key_value_accepts_inline_rsa() -> None:
    assert (
        normalize_ssh_public_key_value(
            _VALID_RSA_PUBLIC_KEY,
            field_label="infra.components[0].inputs.ssh_public_key",
        )
        == _VALID_RSA_PUBLIC_KEY
    )


def test_normalize_ssh_public_key_value_reads_tilde_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    key_path = ssh_dir / "id_ed25519.pub"
    key_path.write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))

    assert (
        normalize_ssh_public_key_value(
            "~/.ssh/id_ed25519.pub",
            field_label="infra.components[0].inputs.ssh_public_key",
        )
        == _VALID_ED25519_PUBLIC_KEY
    )


def test_normalize_runtime_ssh_public_key_inputs_rewrites_relative_path(tmp_path: Path) -> None:
    key_path = tmp_path / "id_rsa.pub"
    key_path.write_text(_VALID_RSA_PUBLIC_KEY + "\n", encoding="utf-8")
    payload = {
        "infra": {
            "components": [
                {
                    "id": "ssh-jumphost",
                    "enabled": True,
                    "inputs": {"ssh_public_key": "./id_rsa.pub"},
                }
            ]
        }
    }

    changed = normalize_runtime_ssh_public_key_inputs(payload, base_dir=tmp_path)

    assert changed is True
    assert payload["infra"]["components"][0]["inputs"]["ssh_public_key"] == _VALID_RSA_PUBLIC_KEY


def test_normalize_ssh_public_key_value_rejects_unsupported_key_type() -> None:
    with pytest.raises(ValueError, match="supported key"):
        normalize_ssh_public_key_value(
            "ssh-ecdsa AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY= demo@example",
            field_label="infra.components[0].inputs.ssh_public_key",
        )


def test_normalize_ssh_public_key_value_rejects_malformed_inline_key() -> None:
    with pytest.raises(ValueError, match="supported key"):
        normalize_ssh_public_key_value(
            "ssh-ed25519 AAAA/not-a-real-key demo@example",
            field_label="infra.components[0].inputs.ssh_public_key",
        )
